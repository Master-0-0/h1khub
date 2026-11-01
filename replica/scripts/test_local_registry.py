#!/usr/bin/env python3
"""End-to-end test for pulling an image from the bundled local OCI registry.

Flow: start the registry through the replica API -> publish an existing local
image into it -> pull it back through Docker via the API -> compare the OCI
manifest digest with Docker's RepoDigest -> confirm existing containers were
not touched -> clean up the temporary tag and stop the registry.

Usage: python3 scripts/test_local_registry.py [--base http://127.0.0.1:38110] [--token TOKEN]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []

SOURCE_IMAGE = os.environ.get("H1KHUB_REGISTRY_SOURCE", "agent-compose-ui:latest")


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            detail = json.loads(text)
        except Exception:
            detail = text
        return {"__http_error": exc.code, "detail": detail}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def data_of(resp):
    if isinstance(resp, dict) and resp.get("ok") and "data" in resp:
        return resp["data"]
    return None


def docker(*args, timeout=180):
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def find_asset(name: str) -> str | None:
    data = data_of(req("/api/assets", method="GET")) or {}
    for asset in data.get("assets") or []:
        if asset.get("name") == name:
            return asset["id"]
    return None


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    rc, out, err = docker("image", "inspect", SOURCE_IMAGE, "--format", "{{.Id}}")
    if rc != 0:
        print(f"跳过：本地镜像 {SOURCE_IMAGE} 不存在（{err[:120]}）")
        return 0

    asset_id = find_asset("Docker local")
    check("找到 Docker local 资产", bool(asset_id), str(asset_id))

    rc, before_containers, _ = docker("ps", "-aq")
    container_ids = [line for line in before_containers.splitlines() if line.strip()]

    repo = "h1khub/e2e-local"
    tag = "t1"
    ref = f"127.0.0.1:5050/{repo}:{tag}"

    # Make sure a stale tag from an earlier run is gone; never touch containers.
    docker("rmi", ref)

    status = data_of(req("/api/docker/local-registry", {"action": "status"}))
    started_here = False
    if not (status or {}).get("running"):
        status = data_of(req("/api/docker/local-registry", {"action": "start", "port": 5050}))
        started_here = True
    check("registry 运行中", bool((status or {}).get("running")), str(status)[:200])
    port = (status or {}).get("port") or 5050

    published = data_of(req("/api/docker/local-registry", {"action": "publish", "image": SOURCE_IMAGE, "repo": repo, "tag": tag}))
    check("发布本地镜像", bool(published and published.get("digest")), str(published)[:240])
    digest = (published or {}).get("digest") or ""

    status2 = data_of(req("/api/docker/local-registry", {"action": "status"}))
    keys = [f"{i.get('name')}:{i.get('tag')}" for i in (status2 or {}).get("images") or []]
    check("仓库列表含发布项", f"{repo}:{tag}" in keys, str(keys))

    pulled = data_of(req("/api/docker/local-registry", {"action": "pull", "repo": repo, "tag": tag, "assetId": asset_id}))
    check("通过 app 拉取成功", bool(pulled and pulled.get("imageId")), str(pulled)[:240])
    check("拉取引用正确", (pulled or {}).get("image") == ref, str((pulled or {}).get("image")))

    rc, repo_digest, _ = docker("image", "inspect", ref, "--format", "{{index .RepoDigests 0}}")
    check("docker 中出现新镜像", rc == 0, f"rc={rc}")
    check("RepoDigest 与发布 digest 一致", repo_digest.endswith(digest), f"{repo_digest} vs {digest}")

    rc, after_containers, _ = docker("ps", "-aq")
    check("现有容器未被改动", [x for x in after_containers.splitlines() if x.strip()] == container_ids, "container list changed")

    # cleanup: temporary image tag + registry (leave the source image and containers)
    rc, _, err = docker("rmi", ref)
    check("清理临时镜像标签", rc == 0, err[:160])
    if started_here:
        stopped = data_of(req("/api/docker/local-registry", {"action": "stop"}))
        check("停止临时 registry", not (stopped or {}).get("running"), str(stopped)[:160])

    print(f"\nmanifest digest: {digest}")
    print(f"PASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES:", FAILURES)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
