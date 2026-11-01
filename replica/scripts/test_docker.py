#!/usr/bin/env python3
"""End-to-end tests for Docker Compose, container lifecycle and container files.

Creates only temporary containers/projects (prefixed h1khub-e2e-) and removes them
at the end. No image pulls and no network access.

Usage: python3 scripts/test_docker.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
IMAGE = os.environ.get("H1KHUB_TEST_IMAGE", "agent-compose-ui:latest")


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST", raw=False, query=None, binary=False):
    url = BASE + path + (("?" + urllib.parse.urlencode(query)) if query else "")
    if binary:
        data = body
        headers = {"X-H1KHub-Token": TOKEN, "Content-Type": "application/octet-stream"}
    else:
        data = None if body is None else json.dumps(body).encode()
        headers = {"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return {"__http_error": exc.code, "detail": json.loads(text)}
        except Exception:
            return {"__http_error": exc.code, "detail": text}


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


def docker(*args: str, check_rc: bool = True) -> str:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if check_rc and proc.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {proc.stderr or proc.stdout}")
    return proc.stdout.strip()


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    assets = {a["name"]: a for a in data_of(req("/api/assets", method="GET"))["assets"]}
    asset_id = assets["Docker local"]["id"]
    c1 = f"h1khub-e2e-{suffix}"
    c2 = f"h1khub-e2e-renamed-{suffix}"
    compose_dir = f"/tmp/h1khub-compose-{suffix}"
    compose_name = f"h1khub-compose-{suffix}"
    compose_container = f"h1khub-e2e-compose-{suffix}"

    def call(action, payload=None):
        return req(f"/api/docker/{action}", {"assetId": asset_id, **(payload or {})})

    created = False
    try:
        print("[docker base]")
        check("docker: containers list", bool(data_of(call("containers"))))
        check("docker: images list", bool(data_of(call("images"))))
        check("docker: stats", data_of(call("stats")) is not None)

        print("\n[container lifecycle]")
        create = call(
            "container-create",
            {"image": IMAGE, "name": c1, "command": "sleep 300", "start": True, "restart": "no"},
        )
        cd = data_of(create)
        check("docker: create container", bool(cd and cd.get("id")), str(create)[:400])
        created = True
        time.sleep(1.2)
        listing = data_of(call("containers"))["containers"]
        state = next((x for x in listing if x["name"] == c1), None)
        check("docker: created container running", bool(state and state["state"] == "running"), str(state)[:200])

        renamed = call("container-rename", {"container": c1, "name": c2})
        check("docker: rename container", bool(data_of(renamed)), str(renamed)[:300])
        listing = data_of(call("containers"))["containers"]
        check("docker: renamed visible", any(x["name"] == c2 for x in listing))

        top = data_of(call("container-top", {"container": c2}))
        check("docker: top returns rows", bool(top and len(top.get("rows") or []) >= 1), str(top)[:300])

        paused = call("action", {"container": c2, "action": "pause"})
        check("docker: pause", bool(data_of(paused)), str(paused)[:300])
        listing = data_of(call("containers"))["containers"]
        state = next((x for x in listing if x["name"] == c2), None)
        check("docker: paused state", bool(state and state["state"] == "paused"), str(state)[:200])
        resumed = call("action", {"container": c2, "action": "unpause"})
        check("docker: unpause", bool(data_of(resumed)), str(resumed)[:300])

        print("\n[container files]")
        flist = data_of(call("fs-list", {"container": c2, "path": "/"}))
        names = [e["name"] for e in (flist or {}).get("entries", [])]
        check("docker fs: list root", "etc" in names and "bin" in names, str(names)[:300])
        check("docker fs: dirs flagged", any(e["isDir"] for e in (flist or {}).get("entries", [])))

        mk = data_of(call("fs-mkdir", {"container": c2, "path": "/tmp/h1khub-e2e"}))
        check("docker fs: mkdir", bool(mk), str(mk)[:200])
        content = f"docker file e2e {suffix}\n第二行\n"
        wr = data_of(call("fs-write", {"container": c2, "path": "/tmp/h1khub-e2e/hello.txt", "content": content}))
        check("docker fs: write", bool(wr), str(wr)[:200])
        rd = data_of(call("fs-read", {"container": c2, "path": "/tmp/h1khub-e2e/hello.txt"}))
        check("docker fs: read roundtrip", (rd or {}).get("content") == content, str(rd)[:300])

        sub = data_of(call("fs-list", {"container": c2, "path": "/tmp/h1khub-e2e"}))
        check("docker fs: list subdir", [e["name"] for e in (sub or {}).get("entries", [])] == ["hello.txt"], str(sub)[:300])

        down = req(
            "/api/docker/fs/download",
            {"assetId": asset_id, "container": c2, "path": "/tmp/h1khub-e2e/hello.txt"},
            raw=True,
        )
        check("docker fs: download", down == content.encode(), str(down)[:80])

        payload = b"binary\x00docker-" + suffix.encode()
        up = req(
            "/api/docker/fs/upload",
            payload,
            binary=True,
            query={"assetId": asset_id, "container": c2, "path": "/tmp/h1khub-e2e/upload.bin"},
        )
        check("docker fs: upload", bool(data_of(up)), str(up)[:300])
        back = req(
            "/api/docker/fs/download",
            {"assetId": asset_id, "container": c2, "path": "/tmp/h1khub-e2e/upload.bin"},
            raw=True,
        )
        check("docker fs: upload roundtrip", back == payload, str(back)[:80])

        ren = call("fs-rename", {"container": c2, "path": "/tmp/h1khub-e2e/upload.bin", "target": "/tmp/h1khub-e2e/renamed.bin"})
        check("docker fs: rename", bool(data_of(ren)), str(ren)[:200])
        dele = call("fs-delete", {"container": c2, "path": "/tmp/h1khub-e2e/hello.txt"})
        check("docker fs: delete", bool(data_of(dele)), str(dele)[:200])
        guard = call("fs-delete", {"container": c2, "path": "/"})
        check("docker fs: refuses root delete", guard.get("__http_error") == 400, str(guard)[:200])

        print("\n[compose]")
        os.makedirs(compose_dir, exist_ok=True)
        yaml = (
            "services:\n"
            "  probe:\n"
            f"    image: {IMAGE}\n"
            "    pull_policy: never\n"
            f"    container_name: {compose_container}\n"
            '    command: ["sleep", "240"]\n'
        )
        written = call("compose-write", {"dir": compose_dir, "file": "docker-compose.yml", "content": yaml})
        check("compose: write file", bool(data_of(written)), str(written)[:300])
        read = call("compose-read", {"dir": compose_dir, "file": "docker-compose.yml"})
        check("compose: read file", (data_of(read) or {}).get("content") == yaml, str(read)[:200])
        up = call("compose-action", {"dir": compose_dir, "file": "docker-compose.yml", "action": "up"})
        check("compose: up", bool(data_of(up)), str(up)[:400])
        time.sleep(1.0)
        ps = data_of(call("compose-ps", {"dir": compose_dir, "file": "docker-compose.yml"}))
        services = ps.get("services") if ps else []
        check("compose: ps lists service", bool(services), str(ps)[:300])
        names = [s.get("Name") or s.get("Names") or "" for s in services]
        check("compose: container present", any(compose_container in str(n) for n in names) or docker("ps", "-a", "--filter", f"name={compose_container}", "--format", "{{.Names}}") != "")
        stop = call("compose-action", {"dir": compose_dir, "file": "docker-compose.yml", "action": "stop"})
        check("compose: stop", bool(data_of(stop)), str(stop)[:300])
        restart = call("compose-action", {"dir": compose_dir, "file": "docker-compose.yml", "action": "restart"})
        check("compose: restart", bool(data_of(restart)), str(restart)[:300])
        down = call("compose-action", {"dir": compose_dir, "file": "docker-compose.yml", "action": "down"})
        check("compose: down", bool(data_of(down)), str(down)[:300])
        time.sleep(0.8)
        remaining = docker("ps", "-a", "--filter", f"name={compose_container}", "--format", "{{.Names}}")
        check("compose: down removed container", remaining == "", remaining)

        print("\n[registry]")
        login = call("registry-login", {"registry": "127.0.0.1:5000", "username": "tester", "password": "secret"})
        check("registry: login error is reported", login.get("__http_error") == 400, str(login)[:300])
        bad_name = call("container-create", {"image": "", "name": "x", "start": False})
        check("docker: create rejects empty image", bad_name.get("__http_error") == 400, str(bad_name)[:200])
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", c1, c2, compose_container], capture_output=True, timeout=60)
        try:
            call("compose-action", {"dir": compose_dir, "file": "docker-compose.yml", "action": "down"})
        except Exception:
            pass
        shutil.rmtree(compose_dir, ignore_errors=True)

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
