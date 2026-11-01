#!/usr/bin/env python3
"""Minimal OCI distribution registry + docker-save packer.

Docker Hub is not reachable from this lab, so the replica ships its own tiny
registry that serves an OCI image layout produced from an existing local
image.  It implements just enough of the distribution spec for ``docker pull``:

    GET  /v2/                              -> 200 {}
    GET  /v2/<name>/manifests/<tag|digest> -> manifest (OCI media type)
    HEAD /v2/<name>/manifests/<tag|digest> -> manifest headers
    GET  /v2/<name>/blobs/<digest>         -> blob bytes
    HEAD /v2/<name>/blobs/<digest>         -> blob headers
    GET  /v2/_catalog                      -> repository list
    GET  /v2/<name>/tags/list              -> tag list

Images are packed with ``docker save`` (no registry access) and converted to an
OCI layout: blobs/sha256/<digest>, index.json and a ``registry.json`` tag map.

Usage:
    oci_registry.py pack  --image agent-compose-ui:latest --repo h1khub/ui \
                          --tag copy --layout /path/to/layout
    oci_registry.py serve --layout /path/to/layout --port 5050
    oci_registry.py list  --layout /path/to/layout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
DOCKER_CONFIG = "application/vnd.docker.container.image.v1+json"
LAYER_TAR = "application/vnd.oci.image.layer.v1.tar"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_blob(layout: str, data_or_path, digest: str) -> int:
    directory = os.path.join(layout, "blobs", "sha256")
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, digest)
    if os.path.exists(target):
        return os.path.getsize(target)
    tmp = target + ".tmp"
    if isinstance(data_or_path, bytes):
        with open(tmp, "wb") as fh:
            fh.write(data_or_path)
    else:
        shutil.copyfile(data_or_path, tmp)
    os.replace(tmp, target)
    return os.path.getsize(target)


def _load_registry(layout: str) -> dict:
    path = os.path.join(layout, "registry.json")
    if not os.path.exists(path):
        return {"images": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("images", {})
        return data
    except Exception:
        return {"images": {}}


def _save_registry(layout: str, data: dict) -> None:
    os.makedirs(layout, exist_ok=True)
    tmp = os.path.join(layout, "registry.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(layout, "registry.json"))


def pack(image: str, repo: str, tag: str, layout: str, docker: str = "docker") -> dict:
    """Export a local docker image and store it as an OCI layout entry."""
    repo = repo.strip("/")
    if not repo:
        raise SystemExit("repo 不能为空")
    tag = tag or "latest"
    os.makedirs(layout, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="oci-pack-") as tmp:
        archive = os.path.join(tmp, "image.tar")
        proc = subprocess.run(
            [docker, "save", "-o", archive, image],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode != 0:
            raise SystemExit(f"docker save 失败：{proc.stderr.strip()}")
        with tarfile.open(archive, "r") as tar:
            manifest_raw = tar.extractfile("manifest.json")
            if not manifest_raw:
                raise SystemExit("docker save 产物缺少 manifest.json")
            saved = json.loads(manifest_raw.read().decode("utf-8"))[0]
            config_name = saved["Config"]
            config_member = tar.extractfile(config_name)
            config_bytes = config_member.read() if config_member else b""
            config_digest = sha256_bytes(config_bytes)
            _write_blob(layout, config_bytes, config_digest)
            layers = []
            for layer_name in saved.get("Layers") or []:
                member = tar.extractfile(layer_name)
                if member is None:
                    raise SystemExit(f"缺少镜像层：{layer_name}")
                layer_path = os.path.join(tmp, "layer.tar")
                with open(layer_path, "wb") as out:
                    shutil.copyfileobj(member, out)
                digest = sha256_file(layer_path)
                size = _write_blob(layout, layer_path, digest)
                layers.append(
                    {
                        "mediaType": LAYER_TAR,
                        "digest": f"sha256:{digest}",
                        "size": size,
                    }
                )
        config_size = os.path.getsize(os.path.join(layout, "blobs", "sha256", config_digest))
        manifest = {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "config": {
                "mediaType": DOCKER_CONFIG,
                "digest": f"sha256:{config_digest}",
                "size": config_size,
            },
            "layers": layers,
        }
        manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        manifest_digest = sha256_bytes(manifest_bytes)
        manifest_size = _write_blob(layout, manifest_bytes, manifest_digest)
        entry = {
            "name": repo,
            "tag": tag,
            "digest": f"sha256:{manifest_digest}",
            "size": manifest_size,
            "source": image,
            "layers": len(layers),
        }
        registry = _load_registry(layout)
        registry["images"][f"{repo}:{tag}"] = entry
        _save_registry(layout, registry)
        _write_index(layout, registry)
    return entry


def _write_index(layout: str, registry: dict) -> None:
    manifests = []
    for key, entry in registry.get("images", {}).items():
        dig = entry["digest"].split(":", 1)[1]
        manifests.append(
            {
                "mediaType": OCI_MANIFEST,
                "digest": entry["digest"],
                "size": entry.get("size") or 0,
                "annotations": {
                    "org.opencontainers.image.ref.name": key,
                    "io.containerd.image.name": f"{key}",
                },
            }
        )
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": manifests}
    with open(os.path.join(layout, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(layout, "oci-layout"), "w", encoding="utf-8") as fh:
        json.dump({"imageLayoutVersion": "1.0.0"}, fh)


class RegistryHandler(BaseHTTPRequestHandler):
    server_version = "H1KHubOCIRegistry/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console quiet
        if os.environ.get("OCI_REGISTRY_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------ routing
    def _layout(self) -> str:
        return self.server.layout  # type: ignore[attr-defined]

    def _registry(self) -> dict:
        return _load_registry(self._layout())

    def _resolve(self, path: str):
        # /v2/<name>/manifests/<ref> or /v2/<name>/blobs/<digest>
        for marker in ("/manifests/", "/blobs/"):
            idx = path.find(marker)
            if idx > 0:
                name = unquote(path[4:idx])
                ref = unquote(path[idx + len(marker) :])
                return marker.strip("/"), name, ref
        return None, None, None

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Docker-Distribution-API-Version", "registry/2.0")
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, code: int, obj, extra: dict | None = None):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json", extra)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/v2", "/v2/"):
            return self._json(200, {})
        if path == "/v2/_catalog":
            names = sorted({key.rsplit(":", 1)[0] for key in self._registry().get("images", {})})
            return self._json(200, {"repositories": names})
        kind, name, ref = self._resolve(path)
        if not kind:
            return self._json(404, {"errors": [{"code": "NAME_UNKNOWN", "message": "unknown path"}]})
        registry = self._registry()
        if kind == "manifests":
            key = f"{name}:{ref}"
            entry = registry.get("images", {}).get(key)
            if not entry and ref.startswith("sha256:"):
                entry = next((e for e in registry.get("images", {}).values() if e["digest"] == ref), None)
            if not entry:
                return self._json(404, {"errors": [{"code": "MANIFEST_UNKNOWN", "message": key}]})
            digest = entry["digest"]
            blob = os.path.join(self._layout(), "blobs", "sha256", digest.split(":", 1)[1])
            if not os.path.exists(blob):
                return self._json(404, {"errors": [{"code": "BLOB_UNKNOWN", "message": digest}]})
            with open(blob, "rb") as fh:
                data = fh.read()
            return self._send(200, data, OCI_MANIFEST, {"Docker-Content-Digest": digest})
        if kind == "blobs":
            if not ref.startswith("sha256:"):
                return self._json(400, {"errors": [{"code": "DIGEST_INVALID"}]})
            hexdigest = ref.split(":", 1)[1]
            blob = os.path.join(self._layout(), "blobs", "sha256", hexdigest)
            if not os.path.exists(blob):
                return self._json(404, {"errors": [{"code": "BLOB_UNKNOWN", "message": ref}]})
            with open(blob, "rb") as fh:
                data = fh.read()
            return self._send(200, data, "application/octet-stream", {"Docker-Content-Digest": ref})
        return self._json(404, {"errors": [{"code": "NAME_UNKNOWN"}]})

    def do_PUT(self):
        self._json(405, {"errors": [{"code": "UNSUPPORTED", "message": "read-only registry"}]})


def serve(layout: str, port: int, host: str = "127.0.0.1") -> None:
    if not os.path.isdir(layout):
        raise SystemExit(f"layout 目录不存在：{layout}")
    _write_index(layout, _load_registry(layout))
    httpd = ThreadingHTTPServer((host, port), RegistryHandler)
    httpd.layout = layout  # type: ignore[attr-defined]
    print(json.dumps({"ok": True, "url": f"http://{host}:{port}", "layout": layout}), flush=True)
    httpd.serve_forever()


def list_images(layout: str) -> dict:
    return _load_registry(layout)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Minimal OCI registry for local docker images")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pack = sub.add_parser("pack")
    p_pack.add_argument("--image", required=True)
    p_pack.add_argument("--repo", required=True)
    p_pack.add_argument("--tag", default="latest")
    p_pack.add_argument("--layout", required=True)
    p_pack.add_argument("--docker", default="docker")
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--layout", required=True)
    p_serve.add_argument("--port", type=int, default=5050)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_list = sub.add_parser("list")
    p_list.add_argument("--layout", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "pack":
        entry = pack(args.image, args.repo, args.tag, args.layout, args.docker)
        print(json.dumps({"ok": True, "entry": entry}, ensure_ascii=False))
        return 0
    if args.cmd == "serve":
        serve(args.layout, args.port, args.host)
        return 0
    if args.cmd == "list":
        print(json.dumps(list_images(args.layout), ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
