#!/usr/bin/env python3
"""End-to-end trzsz test over a real SSH connection.

Starts an SSH session through the replica API, uploads a binary file with the
backend trzsz client (remote ``trz``), verifies the bytes through SFTP, then
downloads the same file through the API (remote ``tsz``) and compares bytes.

Usage: python3 scripts/test_trzsz.py [--base http://127.0.0.1:38110] [--token TOKEN]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import paramiko

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []

SSH_HOST = os.environ.get("H1KHUB_DEMO_SSH_HOST", "127.0.0.1")
SSH_USER = os.environ.get("H1KHUB_DEMO_SSH_USER", "hexlab")
SSH_PASSWORD = os.environ.get("H1KHUB_DEMO_SSH_PASSWORD", "Hexlab@123")
ASSET_NAME = "SSH hexlab"


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST", raw=False, headers=None):
    data = None
    hdrs = {"X-H1KHub-Token": TOKEN}
    if isinstance(body, (bytes, bytearray)):
        data = body
        hdrs["Content-Type"] = "application/octet-stream"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            payload = resp.read()
            return (resp, payload) if raw else json.loads(payload)
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


def find_asset() -> str:
    data = data_of(req("/api/assets", method="GET")) or {}
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            return asset["id"]
    raise SystemExit(f"未找到资产 {ASSET_NAME}")


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    asset_id = find_asset()
    started = data_of(req("/api/sessions/start", {"assetId": asset_id, "cols": 100, "rows": 30}))
    session_id = (started or {}).get("session", {}).get("id")
    check("SSH 会话建立", bool(session_id), str(started)[:160])
    if not session_id:
        print(f"\nPASS={PASS} FAIL={FAIL}")
        return 1

    name = f"trzsz-e2e-{os.getpid()}.bin"
    payload = os.urandom(180000) + "trzsz 中文 \x00 tail".encode("utf-8")
    remote = f"/tmp/{name}"

    sftp = None
    try:
        # ---- upload through trzsz ----------------------------------------
        query = urllib.parse.urlencode({"sessionId": session_id, "dest": "/tmp", "name": name})
        up = data_of(req(f"/api/terminal/trzsz/upload?{query}", payload, raw=False))
        check("trzsz 上传返回 ok", bool(up and up.get("ok")), str(up)[:200])
        check("远端确认保存", str((up or {}).get("message", "")).find(name) >= 0 or "Saved" in str((up or {}).get("message", "")), str((up or {}).get("message"))[:160])

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(SSH_HOST, 22, username=SSH_USER, password=SSH_PASSWORD, timeout=10)
        sftp = client.open_sftp()
        with sftp.open(remote, "rb") as fh:
            remote_bytes = fh.read()
        check("远端文件内容一致", remote_bytes == payload, f"remote={len(remote_bytes)} local={len(payload)}")
        check("远端文件大小一致", len(remote_bytes) == len(payload), str(len(remote_bytes)))

        # ---- download through trzsz --------------------------------------
        resp, body = req("/api/terminal/trzsz/download", {"sessionId": session_id, "path": remote}, raw=True)
        status = getattr(resp, "status", 0)
        if status == 200:
            check("trzsz 下载 HTTP 200", True)
            check("下载内容与上传一致", body == payload, f"down={len(body)} up={len(payload)}")
            header = resp.headers.get("X-Trzsz-Filename", "")
            check("下载文件名", urllib.parse.unquote(header) == name, header)
        else:
            check("trzsz 下载 HTTP 200", False, str(body)[:200])
            check("下载内容与上传一致", False, "download failed")

        # ---- error path: missing remote file -----------------------------
        missing = data_of(req("/api/terminal/trzsz/download", {"sessionId": session_id, "path": f"/tmp/does-not-exist-{os.getpid()}"}))
        check("缺失文件返回错误", missing is None, str(missing)[:160])
    finally:
        try:
            if sftp:
                try:
                    sftp.remove(remote)
                except Exception:
                    pass
                sftp.close()
        except Exception:
            pass
        req(f"/api/sessions/{session_id}/close", {})

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES:", FAILURES)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
