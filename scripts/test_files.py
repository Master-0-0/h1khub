#!/usr/bin/env python3
"""End-to-end tests for FTP and SCP.

Starts the local FTP fixture (scripts/test_servers.py) if needed, then exercises
the replica's FTP and SCP APIs against it / against the lab SSH host.

Usage: python3 scripts/test_files.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:38110"
TOKEN = ""
FTP_PORT = 2121
FTP_ROOT = "/tmp/h1khub-ftp"
PASS = 0
FAIL = 0
FAILURES: list[str] = []
FTP_PROC = None


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
        with urllib.request.urlopen(r, timeout=60) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload)
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


def is_ok(resp):
    return isinstance(resp, dict) and resp.get("ok") is True and "__http_error" not in resp


def port_open(port: int, host="127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def ensure_ftp_server(root: str) -> None:
    global FTP_PROC
    if port_open(FTP_PORT):
        print(f"ftp fixture already listening on {FTP_PORT}")
        return
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "hello.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello ftp\n")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_servers.py")
    FTP_PROC = subprocess.Popen(
        [sys.executable, script, "ftp", "--port", str(FTP_PORT), "--root", root],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        if port_open(FTP_PORT):
            print(f"started ftp fixture on {FTP_PORT}")
            return
        time.sleep(0.1)
    raise RuntimeError("FTP fixture failed to start")


def find_assets() -> dict:
    return {a["name"]: a for a in data_of(req("/api/assets", method="GET"))["assets"]}


def ensure_ftp_asset(suffix: str) -> tuple[str, str]:
    name = f"FTP E2E {suffix}"
    assets = find_assets()
    if name in assets:
        return assets[name]["id"], name
    resp = req(
        "/api/assets",
        {
            "typeName": "FTP",
            "name": name,
            "config": {
                "protocol": "ftp",
                "host": "127.0.0.1",
                "port": FTP_PORT,
                "username": "hexftp",
                "password": "hex123",
                "passive": True,
            },
        },
    )
    d = data_of(resp)
    if not d:
        raise RuntimeError(f"cannot create FTP asset: {resp}")
    return d["asset"]["id"], name


def test_ftp(asset_id: str, suffix: str) -> None:
    print("\n[ftp]")
    test = req(
        "/api/test-connection",
        {
            "typeName": "FTP",
            "config": {"protocol": "ftp", "host": "127.0.0.1", "port": FTP_PORT, "username": "hexftp", "password": "hex123"},
        },
    )
    check("ftp: test connection", bool(data_of(test)), str(test)[:300])

    listing = data_of(req("/api/ftp/list", {"assetId": asset_id, "path": "/"}))
    names = [e["name"] for e in (listing or {}).get("entries", [])]
    check("ftp: list root", "hello.txt" in names and "sub" in names, str(names))
    check("ftp: dir flagged", any(e["isDir"] for e in (listing or {}).get("entries", [])))

    listing_sub = data_of(req("/api/ftp/list", {"assetId": asset_id, "path": "/sub"}))
    check("ftp: list subdir", listing_sub is not None and listing_sub.get("path") == "/sub", str(listing_sub)[:200])

    folder = f"/e2e-{suffix}"
    check("ftp: mkdir", is_ok(req("/api/ftp/mkdir", {"assetId": asset_id, "path": folder})))

    content = f"FTP E2E {suffix}\n第二行\n"
    written = data_of(req("/api/ftp/write", {"assetId": asset_id, "path": f"{folder}/note.txt", "content": content}))
    check("ftp: write text", bool(written), str(written)[:200])
    read = data_of(req("/api/ftp/read", {"assetId": asset_id, "path": f"{folder}/note.txt"}))
    check("ftp: read text roundtrip", (read or {}).get("content") == content, str(read)[:200])

    payload = b"binary\x00payload-" + suffix.encode()
    up = req("/api/ftp/upload", payload, binary=True, query={"assetId": asset_id, "path": f"{folder}/blob.bin"})
    check("ftp: upload bytes", data_of(up) is not None, str(up)[:200])
    down = req("/api/ftp/download", {"assetId": asset_id, "path": f"{folder}/blob.bin"}, raw=True)
    check("ftp: download roundtrip", down == payload, f"{str(down)[:80]}")

    renamed = req("/api/ftp/rename", {"assetId": asset_id, "path": f"{folder}/blob.bin", "target": f"{folder}/renamed.bin"})
    check("ftp: rename", is_ok(renamed), str(renamed)[:200])

    after = data_of(req("/api/ftp/list", {"assetId": asset_id, "path": folder}))
    names = [e["name"] for e in (after or {}).get("entries", [])]
    check("ftp: renamed visible", "renamed.bin" in names, str(names))

    deleted = req("/api/ftp/delete", {"assetId": asset_id, "path": f"{folder}/renamed.bin"})
    check("ftp: delete file", is_ok(deleted), str(deleted)[:200])
    deleted_dir = req("/api/ftp/delete", {"assetId": asset_id, "path": folder})
    check("ftp: delete directory", is_ok(deleted_dir), str(deleted_dir)[:200])

    root_guard = req("/api/ftp/delete", {"assetId": asset_id, "path": "/"})
    check("ftp: refuses deleting root", root_guard.get("__http_error") == 400, str(root_guard)[:200])

    hidden = data_of(req("/api/ftp/list", {"assetId": asset_id, "path": "/", "showHidden": False}))
    check("ftp: hidden filter accepted", hidden is not None)


def ssh_exec(asset_id: str, command: str):
    resp = req("/api/batch/exec", {"assetIds": [asset_id], "command": command})
    d = data_of(resp)
    if not d:
        return resp
    result = d["results"][0]
    return result


def test_scp(ssh_asset: str, suffix: str) -> None:
    print("\n[scp]")
    remote = f"/tmp/h1khub-scp-{suffix}.txt"
    payload = f"SCP E2E {suffix}\nline-two\n".encode()
    up = req("/api/scp/upload", payload, binary=True, query={"assetId": ssh_asset, "path": remote})
    up_data = data_of(up)
    check("scp: upload", bool(up_data) and up_data.get("size") == len(payload), str(up)[:300])

    result = ssh_exec(ssh_asset, f"cat {remote}")
    check("scp: remote content", payload.decode() in (result.get("stdout") or ""), str(result)[:300])

    down = req("/api/scp/download", {"assetId": ssh_asset, "path": remote}, raw=True)
    check("scp: download roundtrip", down == payload, str(down)[:100])

    missing = req("/api/scp/download", {"assetId": ssh_asset, "path": f"/tmp/no-such-scp-{suffix}"})
    check("scp: missing file error", missing.get("__http_error") == 400 and "No such file" in json.dumps(missing), str(missing)[:300])

    ssh_exec(ssh_asset, f"rm -f {remote}")


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    ensure_ftp_server(FTP_ROOT)
    asset_id, name = ensure_ftp_asset(suffix)
    assets = find_assets()
    try:
        test_ftp(asset_id, suffix)
        test_scp(assets["SSH hexlab"]["id"], suffix)
    finally:
        try:
            req(f"/api/assets/{asset_id}", method="DELETE")
        except Exception:
            pass
        try:
            req("/api/ftp/delete", {"assetId": asset_id, "path": f"/e2e-{suffix}"})
        except Exception:
            pass
        if FTP_PROC is not None:
            FTP_PROC.terminate()
            try:
                FTP_PROC.wait(timeout=3)
            except Exception:
                FTP_PROC.kill()

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
