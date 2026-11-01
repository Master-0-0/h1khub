#!/usr/bin/env python3
"""Pull a container image over many parallel range requests and load it into Docker.

The lab link to international registries is slow per TCP connection (~90 KB/s)
but scales with concurrency, while ``docker pull`` uses one stream per layer.
This tool fetches every layer as independently retried byte ranges, verifies the
content digests, then streams a ``docker save`` style archive into ``docker load``
so the resulting image keeps the original config (ENV/ENTRYPOINT/CMD) verbatim.

Only anonymous registry auth is used; no credentials are read or stored.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import threading
import time
from urllib.parse import urlparse, parse_qs

import requests

ACCEPT_INDEX = ", ".join(
    [
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ]
)
ACCEPT_MANIFEST = ", ".join(
    [
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ]
)

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}GB"


class Registry:
    def __init__(self, host: str) -> None:
        self.host = host
        self.base = f"https://{host}"
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "h1khub-pull/1.0"
        self.tokens: dict[str, str] = {}

    def _token(self, scope: str) -> str:
        if scope in self.tokens:
            return self.tokens[scope]
        url = f"{self.base}/v2/"
        resp = self.session.get(url, timeout=30)
        if resp.status_code != 401:
            self.tokens[scope] = ""
            return ""
        header = resp.headers.get("WWW-Authenticate", "")
        if not header.lower().startswith("bearer"):
            self.tokens[scope] = ""
            return ""
        fields = dict(re.findall(r'(\w+)="([^"]*)"', header))
        realm = fields.get("realm")
        service = fields.get("service", "")
        scopes = fields.get("scope", scope)
        params = {"service": service, "scope": scopes}
        token_resp = self.session.get(realm, params=params, timeout=30)
        token_resp.raise_for_status()
        payload = token_resp.json()
        token = payload.get("token") or payload.get("access_token") or ""
        self.tokens[scope] = token
        return token

    def _headers(self, repo: str, accept: str) -> dict:
        scope = f"repository:{repo}:pull"
        token = self._token(scope)
        headers = {"Accept": accept}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def manifest(self, repo: str, ref: str, arch: str = "amd64") -> tuple[dict, dict]:
        """Return (manifest, headers) resolving a multi-arch index for ``arch``."""
        url = f"{self.base}/v2/{repo}/manifests/{ref}"
        headers = self._headers(repo, ACCEPT_INDEX)
        resp = self.session.get(url, headers=headers, timeout=60)
        if resp.status_code == 401:
            self.tokens.pop(f"repository:{repo}:pull", None)
            headers = self._headers(repo, ACCEPT_INDEX)
            resp = self.session.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        doc = resp.json()
        media = doc.get("mediaType", "")
        if "index" in media or "manifest.list" in media or doc.get("manifests"):
            digest = None
            for entry in doc.get("manifests", []):
                platform = entry.get("platform") or {}
                if platform.get("architecture") == arch and platform.get("os") == "linux":
                    digest = entry["digest"]
                    break
            if not digest:
                raise SystemExit(f"no linux/{arch} manifest in index for {repo}:{ref}")
            log(f"  index -> {digest[:19]}… ({arch})")
            resp = self.session.get(
                f"{self.base}/v2/{repo}/manifests/{digest}",
                headers=self._headers(repo, ACCEPT_MANIFEST),
                timeout=60,
            )
            resp.raise_for_status()
            doc = resp.json()
        return doc, self._headers(repo, ACCEPT_MANIFEST)

    def blob_url(self, repo: str, digest: str) -> str:
        return f"{self.base}/v2/{repo}/blobs/{digest}"


class BlobFetcher:
    """Download one blob with ``workers`` concurrent range requests."""

    def __init__(self, registry: Registry, repo: str, digest: str, size: int, dest: str,
                 workers: int, chunk: int, headers: dict) -> None:
        self.registry = registry
        self.repo = repo
        self.digest = digest
        self.size = size
        self.dest = dest
        self.workers = workers
        self.chunk = chunk
        self.headers = headers
        self.url = registry.blob_url(repo, digest)
        self.done_path = dest + ".chunks"
        self.done: set[int] = set()
        self.lock = threading.Lock()
        self.written = 0
        self._load_progress()

    def _load_progress(self) -> None:
        if os.path.exists(self.dest) and os.path.exists(self.done_path):
            try:
                with open(self.done_path) as fh:
                    self.done = {int(x) for x in fh.read().split() if x.strip().isdigit()}
            except Exception:
                self.done = set()
            if os.path.getsize(self.dest) != self.size:
                self.done = set()
        else:
            self.done = set()

    def _mark(self, idx: int, nbytes: int) -> None:
        with self.lock:
            self.done.add(idx)
            self.written += nbytes
            with open(self.done_path, "a") as fh:
                fh.write(f"{idx}\n")

    def _fetch_chunk(self, fd: int, idx: int) -> None:
        start = idx * self.chunk
        end = min(self.size - 1, start + self.chunk - 1)
        last_err: Exception | None = None
        for attempt in range(6):
            try:
                headers = dict(self.headers)
                headers["Range"] = f"bytes={start}-{end}"
                with self.registry.session.get(
                    self.url, headers=headers, timeout=(20, 180), stream=True
                ) as resp:
                    if resp.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {resp.status_code}")
                    buf = bytearray()
                    for block in resp.iter_content(262144):
                        buf.extend(block)
                    if len(buf) != end - start + 1:
                        raise RuntimeError(f"short read {len(buf)} != {end - start + 1}")
                    os.pwrite(fd, bytes(buf), start)
                    self._mark(idx, len(buf))
                    return
            except Exception as exc:  # noqa: BLE001 - retried below
                last_err = exc
                time.sleep(min(8, 1.5 * (attempt + 1)))
        raise RuntimeError(f"chunk {idx} failed after retries: {last_err}")

    def run(self) -> str:
        total_chunks = max(1, (self.size + self.chunk - 1) // self.chunk)
        pending = [i for i in range(total_chunks) if i not in self.done]
        if not pending:
            log(f"  {self.digest[7:19]}… cached")
            return self.dest
        fd = os.open(self.dest, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            os.ftruncate(fd, self.size)
            if not os.path.exists(self.done_path):
                open(self.done_path, "w").close()
            started = time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._fetch_chunk, fd, idx) for idx in pending]
                last_report = 0.0
                for fut in concurrent.futures.as_completed(futures):
                    fut.result()
                    now = time.time()
                    if now - last_report > 5:
                        last_report = now
                        elapsed = max(0.001, now - started)
                        rate = self.written / elapsed
                        pct = 100.0 * (self.size - (total_chunks - len(self.done)) * self.chunk) / self.size
                        log(f"  {self.digest[7:19]}… {human(self.written)} "
                            f"({max(0.0, min(100.0, pct)):.0f}%) {human(rate)}/s")
        finally:
            os.close(fd)
        self._verify()
        for suffix in (".chunks",):
            try:
                os.unlink(self.dest + suffix)
            except OSError:
                pass
        return self.dest

    def _verify(self) -> None:
        algo, _, expect = self.digest.partition(":")
        hasher = hashlib.new(algo)
        with open(self.dest, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                hasher.update(block)
        actual = hasher.hexdigest()
        if actual != expect:
            raise SystemExit(f"digest mismatch for {self.dest}: {actual} != {expect}")
        log(f"  {self.digest[7:19]}… verified sha256")


def write_docker_archive(config_path: str, layer_paths: list[str], repo_tags: list[str],
                         out) -> None:
    manifest = [
        {
            "Config": "config.json",
            "RepoTags": repo_tags,
            "Layers": [f"l{i}/layer.tar" for i in range(len(layer_paths))],
        }
    ]
    size = os.path.getsize(config_path) + sum(os.path.getsize(p) for p in layer_paths)
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for name, path in [("manifest.json", None), ("config.json", config_path)] + [
            (f"l{i}/layer.tar", p) for i, p in enumerate(layer_paths)
        ]:
            if name == "manifest.json":
                data = json.dumps(manifest).encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(data))
                continue
            info = tar.gettarinfo(path, arcname=name)
            info.name = name
            info.size = os.path.getsize(path)
            with open(path, "rb") as fh:
                tar.addfile(info, fh)
    log(f"  archive streamed ({human(size)})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--repo-tag", required=True, help="local name:tag to apply")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--chunk-mb", type=int, default=8)
    ap.add_argument("--cache", default="/var/tmp/h1khub-image-cache")
    ap.add_argument("--save", default="", help="write the archive here instead of piping to docker load")
    ap.add_argument("--arch", default="amd64")
    args = ap.parse_args()

    os.makedirs(args.cache, exist_ok=True)
    reg = Registry(args.registry)
    log(f"resolving {args.registry}/{args.repo}:{args.tag}")
    manifest, headers = reg.manifest(args.repo, args.tag, args.arch)
    config_digest = manifest["config"]["digest"]
    layers = manifest["layers"]
    log(f"  {len(layers)} layers, config {config_digest[7:19]}…")

    def fetch(digest: str, size: int) -> str:
        path = os.path.join(args.cache, digest.replace(":", "_"))
        return BlobFetcher(
            reg, args.repo, digest, size, path, args.workers, args.chunk_mb << 20, headers
        ).run()

    started = time.time()
    config_path = fetch(config_digest, manifest["config"]["size"])
    layer_paths = [fetch(layer["digest"], layer["size"]) for layer in layers]
    log(f"all blobs ready in {time.time() - started:.0f}s")

    repo_tags = [args.repo_tag]
    if args.save:
        with open(args.save, "wb") as fh:
            write_docker_archive(config_path, layer_paths, repo_tags, fh)
        log(f"saved {args.save}")
        return 0

    import subprocess

    proc = subprocess.Popen(["docker", "load"], stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        write_docker_archive(config_path, layer_paths, repo_tags, proc.stdin)
    finally:
        proc.stdin.close()
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
