#!/usr/bin/env python3
"""Cross-server transfer, command broadcast, key generation/copy-id, password pair and local files."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import aiohttp

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path, body=None, method="POST", raw=False, query=None, binary=False, timeout=90):
    url = BASE + path + (("?" + urllib.parse.urlencode(query)) if query else "")
    if binary:
        data = body
        headers = {"X-H1KHub-Token": TOKEN, "Content-Type": "application/octet-stream"}
    else:
        data = None if body is None else json.dumps(body).encode()
        headers = {"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return {"__http_error": exc.code, "detail": json.loads(text)}
        except Exception:
            return {"__http_error": exc.code, "detail": text}


def check(name, condition, detail=""):
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


def assets():
    return {a["name"]: a for a in data_of(req("/api/assets", method="GET"))["assets"]}


def ssh_exec(asset_id, command):
    resp = req("/api/batch/exec", {"assetIds": [asset_id], "command": command})
    results = (data_of(resp) or {}).get("results") or [{}]
    return results[0]


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------------
def test_transfer(src_id, dst_id, suffix):
    print("\n[cross-server transfer]")
    src_dir = f"/tmp/h1khub-xfer-src-{suffix}"
    dst_dir = f"/tmp/h1khub-xfer-dst-{suffix}"
    try:
        ssh_exec(src_id, f"rm -rf {src_dir} {dst_dir}; mkdir -p {src_dir}/sub; printf 'alpha' > {src_dir}/a.txt; printf 'beta' > {src_dir}/sub/b.txt")
        result = data_of(req("/api/transfer", {"fromAssetId": src_id, "fromPath": src_dir, "toAssetId": dst_id, "toPath": dst_dir}))
        check("transfer: files copied", (result or {}).get("files") == 2, str(result)[:300])
        check("transfer: bytes copied", (result or {}).get("bytes", 0) >= 9, str(result)[:200])
        a = ssh_exec(dst_id, f"cat {dst_dir}/a.txt")
        b = ssh_exec(dst_id, f"cat {dst_dir}/sub/b.txt")
        check("transfer: target content a", (a.get("stdout") or "").strip() == "alpha", str(a)[:200])
        check("transfer: target content b", (b.get("stdout") or "").strip() == "beta", str(b)[:200])

        # overwrite=False must skip the existing file
        ssh_exec(src_id, f"printf 'changed' > {src_dir}/a.txt")
        skipped = data_of(req("/api/transfer", {"fromAssetId": src_id, "fromPath": src_dir, "toAssetId": dst_id, "toPath": dst_dir, "overwrite": False}))
        check("transfer: overwrite=false skips", any(x.endswith("a.txt") for x in (skipped or {}).get("skipped", [])), str(skipped)[:300])
        still = ssh_exec(dst_id, f"cat {dst_dir}/a.txt")
        check("transfer: skipped content unchanged", (still.get("stdout") or "").strip() == "alpha", str(still)[:200])
    finally:
        ssh_exec(src_id, f"rm -rf {src_dir}")
        ssh_exec(dst_id, f"rm -rf {dst_dir}")


async def _broadcast_ws(session_ids, timeout=15.0):
    url = BASE.replace("http://", "ws://") + f"/rpc?token={TOKEN}"
    buffers = {sid: bytearray() for sid in session_ids}
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, heartbeat=10) as ws:
            for sid in session_ids:
                await ws.send_json({"method": "open", "channel": f"term:{sid}"})

            async def drain(seconds):
                deadline = time.time() + seconds
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=0.3)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        return
                    packet = json.loads(msg.data)
                    if packet.get("method") != "event":
                        continue
                    channel = packet.get("channel") or ""
                    sid = channel.split(":", 1)[1] if channel.startswith("term:") else ""
                    event = packet.get("data") or {}
                    if sid in buffers and event.get("type") in ("data", "history"):
                        buffers[sid].extend(base64.b64decode(event.get("data") or ""))

            await drain(2.5)
            req("/api/sessions/broadcast", {"sessionIds": session_ids, "command": "echo broadcast-e2e-ok"})
            await drain(6.0)
            for sid in session_ids:
                await ws.send_json({"method": "close", "channel": f"term:{sid}"})
    return buffers


def test_broadcast(ssh_id, jump_id):
    print("\n[command broadcast]")
    sessions = []
    try:
        for aid in (ssh_id, jump_id):
            resp = data_of(req("/api/sessions/start", {"assetId": aid, "cols": 100, "rows": 30}))
            sessions.append((resp or {}).get("session", {}).get("id"))
        check("broadcast: two sessions", all(sessions), str(sessions))
        if not all(sessions):
            return
        buffers = asyncio.run(_broadcast_ws(sessions))
        for index, sid in enumerate(sessions, start=1):
            check(f"broadcast: session {index} received command", b"broadcast-e2e-ok" in buffers[sid], buffers[sid][-200:].decode(errors="replace"))
    finally:
        for sid in sessions:
            if sid:
                req(f"/api/sessions/{sid}/close")


def test_keys(ssh_id, suffix):
    print("\n[key generation / copy-id]")
    key_asset = None
    key_ssh = None
    try:
        generated = data_of(req("/api/keys/generate", {"type": "ed25519", "comment": f"h1khub-key-{suffix}", "saveAsset": True, "name": f"测试密钥 {suffix}"}))
        check("keys: generated", bool(generated and generated.get("publicKey")), str(generated)[:300])
        key_asset = (generated or {}).get("asset", {}).get("id")
        check("keys: asset saved", bool(key_asset), str(generated)[:200])
        check("keys: fingerprint", str((generated or {}).get("fingerprint", "")).startswith("SHA256:"), str(generated)[:200])

        key_ssh = data_of(req("/api/assets", {
            "typeName": "SSH", "name": f"密钥登录测试 {suffix}",
            "config": {"host": "127.0.0.1", "port": 22, "username": "hexlab", "authType": "key", "privateKeyAssetId": key_asset, "connectTimeout": 10},
        }))["asset"]["id"]
        before = req("/api/test-connection", {"assetId": key_ssh})
        check("keys: unauthorized key rejected", before.get("__http_error") == 400, str(before)[:300])

        copied = data_of(req("/api/ssh/copy-id", {"assetId": ssh_id, "keyAssetId": key_asset}))
        check("keys: copy-id added", bool(copied and copied.get("added")), str(copied)[:300])
        after = req("/api/test-connection", {"assetId": key_ssh})
        check("keys: key login succeeds", bool(data_of(after)), str(after)[:300])
        again = data_of(req("/api/ssh/copy-id", {"assetId": ssh_id, "keyAssetId": key_asset}))
        check("keys: copy-id idempotent", bool(again and again.get("existed")), str(again)[:200])
    finally:
        try:
            import paramiko

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect("127.0.0.1", 22, username="hexlab", password="Hexlab@123", timeout=10, allow_agent=False, look_for_keys=False)
            marker = f"h1khub-key-{suffix}"
            client.exec_command(f"sed -i '/{marker}/d' ~/.ssh/authorized_keys").channel.recv_exit_status()
            client.close()
        except Exception:
            pass
        for aid in (key_ssh, key_asset):
            if aid:
                req(f"/api/assets/{aid}", method="DELETE")


def test_password_pair(ssh_id, suffix):
    print("\n[shared password pair]")
    pair = None
    ssh_asset = None
    try:
        pair = data_of(req("/api/assets", {
            "typeName": "SSHPasswordPair", "name": f"密码对 {suffix}",
            "config": {"username": "hexlab", "password": "Hexlab@123"},
        }))
        check("password pair: created", bool(pair), str(pair)[:300])
        pair_id = (pair or {}).get("asset", {}).get("id")
        ssh_asset = data_of(req("/api/assets", {
            "typeName": "SSH", "name": f"密码对登录 {suffix}",
            "config": {"host": "127.0.0.1", "port": 22, "username": "hexlab", "authType": "password", "passwordPairAssetId": pair_id, "connectTimeout": 10},
        }))["asset"]["id"]
        test = req("/api/test-connection", {"assetId": ssh_asset})
        check("password pair: SSH login via pair", bool(data_of(test)), str(test)[:300])
    finally:
        for aid in (ssh_asset, (pair or {}).get("asset", {}).get("id")):
            if aid:
                req(f"/api/assets/{aid}", method="DELETE")


def test_local_fs(suffix):
    print("\n[local files]")
    home = data_of(req("/api/fs/home", method="GET"))
    check("local: home", bool(home and (home.get("home") or home.get("path"))), str(home)[:200])
    listing = data_of(req("/api/fs/list", {"path": "~"}))
    check("local: list home", bool(listing and listing.get("entries")), str(listing)[:200])
    base = tempfile.gettempdir()
    folder = os.path.join(base, f"h1khub-local-{suffix}")
    path = os.path.join(folder, "note.txt")
    content = f"local fs {suffix}\n"
    try:
        check("local: mkdir", (req("/api/fs/mkdir", {"path": folder}) or {}).get("ok") is True)
        written = data_of(req("/api/fs/write", {"path": path, "content": content}))
        check("local: write", bool(written), str(written)[:200])
        read = data_of(req("/api/fs/read", {"path": path}))
        check("local: read", (read or {}).get("content") == content, str(read)[:200])
        renamed = os.path.join(folder, "renamed.txt")
        check("local: rename", (req("/api/fs/rename", {"path": path, "target": renamed}) or {}).get("ok") is True)
        downloaded = req("/api/fs/download", {"path": renamed}, raw=True)
        check("local: download", downloaded == content.encode(), str(downloaded)[:80])
        upload = req("/api/fs/upload", b"uploaded-bytes", binary=True, query={"path": os.path.join(folder, "up.bin")})
        check("local: upload", bool(data_of(upload)), str(upload)[:200])
        with open(os.path.join(folder, "up.bin"), "rb") as fh:
            check("local: upload bytes", fh.read() == b"uploaded-bytes")
        check("local: delete", (req("/api/fs/delete", {"path": folder}) or {}).get("ok") is True)
    finally:
        try:
            import shutil

            shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass


def main():
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    found = assets()
    ssh_id = found["SSH hexlab"]["id"]
    jump_id = found["SSH 跳板演示"]["id"]
    try:
        test_transfer(ssh_id, jump_id, suffix)
        test_broadcast(ssh_id, jump_id)
        test_keys(ssh_id, suffix)
        test_password_pair(ssh_id, suffix)
        test_local_fs(suffix)
    finally:
        pass
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
