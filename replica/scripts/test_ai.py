#!/usr/bin/env python3
"""End-to-end AI agent tests against the local mock LLM.

Covers streaming, tool calls, approval (approve + reject), and the SCP / tunnel /
FTP tools. No external model or network access is used.

Usage: python3 scripts/test_ai.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import aiohttp

BASE = "http://127.0.0.1:38110"
MOCK_PORT = 39001
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
MOCK_PROC = None
FTP_PROC = None
ORIGINAL_AI = None


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return json.loads(resp.read())
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


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_mock() -> None:
    global MOCK_PROC
    if port_open(MOCK_PORT):
        print(f"mock already on {MOCK_PORT}")
        return
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_llm.py")
    MOCK_PROC = subprocess.Popen([sys.executable, script], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(50):
        if port_open(MOCK_PORT):
            print(f"started mock llm on {MOCK_PORT}")
            return
        time.sleep(0.1)
    raise RuntimeError("mock llm failed to start")


def start_ftp() -> None:
    global FTP_PROC
    if port_open(2121):
        return
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_servers.py")
    os.makedirs("/tmp/h1khub-ftp/sub", exist_ok=True)
    with open("/tmp/h1khub-ftp/hello.txt", "w", encoding="utf-8") as fh:
        fh.write("hello ftp\n")
    FTP_PROC = subprocess.Popen(
        [sys.executable, script, "ftp", "--port", "2121", "--root", "/tmp/h1khub-ftp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    time.sleep(0.8)


async def run_chat(prompt: str, approve: bool = True, timeout: float = 45.0, **extra) -> dict:
    url = BASE.replace("http://", "ws://") + f"/rpc?token={TOKEN}"
    events: list[dict] = []
    approvals = 0
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, heartbeat=10) as ws:
            await ws.send_json({"method": "open", "channel": "ai"})
            deadline = time.time() + timeout
            while time.time() < deadline:
                msg = await asyncio.wait_for(ws.receive(), timeout=max(0.5, deadline - time.time()))
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                packet = json.loads(msg.data)
                if packet.get("method") == "open":
                    await ws.send_json(
                        {
                            "method": "send",
                            "channel": "ai",
                            "data": {
                                "action": "chat",
                                "prompt": prompt,
                                "providerId": "mock",
                                "model": "mock-1",
                                **extra,
                            },
                        }
                    )
                    continue
                if packet.get("method") != "event":
                    continue
                event = packet.get("data") or {}
                events.append(event)
                if event.get("type") == "approval":
                    approvals += 1
                    await ws.send_json(
                        {
                            "method": "send",
                            "channel": "ai",
                            "data": {"action": "approval", "id": event.get("id"), "approve": approve},
                        }
                    )
                if event.get("type") == "done":
                    break
                if event.get("type") == "error":
                    break
            await ws.send_json({"method": "close", "channel": "ai"})
    return {
        "events": events,
        "approvals": approvals,
        "text": "".join(e.get("text") or "" for e in events if e.get("type") == "delta"),
        "toolStarts": [e for e in events if e.get("type") == "tool_start"],
        "toolResults": [e for e in events if e.get("type") == "tool_result"],
        "done": next((e for e in events if e.get("type") == "done"), None),
        "error": next((e for e in events if e.get("type") == "error"), None),
    }


def batch_exec(asset_id: str, command: str) -> str:
    resp = req("/api/batch/exec", {"assetIds": [asset_id], "command": command})
    results = (data_of(resp) or {}).get("results") or [{}]
    return results[0].get("stdout") or ""


def main() -> int:
    global BASE, TOKEN, ORIGINAL_AI
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    start_mock()
    start_ftp()

    assets = {a["name"]: a for a in data_of(req("/api/assets", method="GET"))["assets"]}
    ssh_id = assets["SSH hexlab"]["id"]
    ftp_id = assets["FTP 测试"]["id"]
    tunnel_id = assets["隧道 local 测试"]["id"]

    settings = data_of(req("/api/settings", method="GET"))
    ORIGINAL_AI = json.loads(json.dumps(settings.get("ai") or {}))
    mock_provider = {
        "id": "mock",
        "name": "Mock LLM",
        "protocol": "openai",
        "baseUrl": f"http://127.0.0.1:{MOCK_PORT}/v1",
        "apiKey": "mock-key",
        "models": ["mock-1"],
        "defaultModel": "mock-1",
    }
    req("/api/settings", {"ai": {"providers": [mock_provider], "activeProviderId": "mock", "activeModel": "mock-1"}}, method="PUT")

    local_up = os.path.join(tempfile.gettempdir(), f"h1khub-ai-up-{suffix}.txt")
    local_down = os.path.join(tempfile.gettempdir(), f"h1khub-ai-down-{suffix}.txt")
    remote = f"/tmp/h1khub-ai-{suffix}.txt"
    payload = f"AI SCP E2E {suffix}\nline2\n"
    with open(local_up, "w", encoding="utf-8") as fh:
        fh.write(payload)

    try:
        print("[models]")
        models = data_of(req("/api/ai/models", {"providerId": "mock"}))
        model_items = (models or {}).get("models", [])
        check(
            "ai: model list",
            any((m.get("id") if isinstance(m, dict) else m) == "mock-1" for m in model_items),
            str(models)[:300],
        )
        test = data_of(req("/api/ai/test", {"providerId": "mock"}))
        check("ai: connectivity test", bool(test), str(test)[:300])

        print("\n[chat + streaming]")
        plain = asyncio.run(run_chat("你好"))
        check("ai: plain reply", bool(plain["done"] and plain["text"]), str(plain["done"])[:300])
        check("ai: streaming deltas", sum(1 for e in plain["events"] if e.get("type") == "delta") > 3)
        check("ai: no approval for plain chat", plain["approvals"] == 0)

        print("\n[tool: list_assets]")
        listed = asyncio.run(run_chat("@tool list_assets {}"))
        result = (listed["toolResults"] or [{}])[0]
        check("ai: list_assets called", bool(listed["toolStarts"]) and listed["toolStarts"][0].get("name") == "list_assets", str(listed["toolStarts"])[:300])
        check("ai: list_assets result ok", result.get("ok") is True, str(result)[:300])
        check("ai: list_assets contains SSH hexlab", "SSH hexlab" in (result.get("output") or ""), (result.get("output") or "")[:200])

        print("\n[tool: shell + approval]")
        shell = asyncio.run(run_chat('@tool shell {"command":"echo ai-shell-ok","timeout":20}'))
        check("ai: shell requested approval", shell["approvals"] == 1, str(shell["approvals"]))
        shell_result = (shell["toolResults"] or [{}])[0]
        check("ai: shell executed", "ai-shell-ok" in (shell_result.get("output") or ""), str(shell_result)[:300])

        print("\n[tool: reject]")
        rejected = asyncio.run(run_chat('@tool shell {"command":"echo should-not-run"}', approve=False))
        rejected_result = (rejected["toolResults"] or [{}])[0]
        check("ai: rejection blocks tool", rejected_result.get("ok") is False and "拒绝" in (rejected_result.get("output") or ""), str(rejected_result)[:300])

        print("\n[tool: ftp_list]")
        ftp = asyncio.run(run_chat(f'@tool ftp_list {{"asset_id":"{ftp_id}","path":"/"}}'))
        ftp_result = (ftp["toolResults"] or [{}])[0]
        check("ai: ftp_list ok", ftp_result.get("ok") is True, str(ftp_result)[:300])
        check("ai: ftp_list sees hello.txt", "hello.txt" in (ftp_result.get("output") or ""), (ftp_result.get("output") or "")[:200])

        print("\n[tool: scp upload/download]")
        up = asyncio.run(run_chat(f'@tool scp_upload {{"asset_id":"{ssh_id}","local_path":"{local_up}","remote_path":"{remote}"}}'))
        up_result = (up["toolResults"] or [{}])[0]
        check("ai: scp_upload ok", up_result.get("ok") is True, str(up_result)[:300])
        check("ai: scp_upload content", payload.splitlines()[0] in batch_exec(ssh_id, f"cat {remote}"))
        down = asyncio.run(run_chat(f'@tool scp_download {{"asset_id":"{ssh_id}","remote_path":"{remote}","local_path":"{local_down}"}}'))
        down_result = (down["toolResults"] or [{}])[0]
        check("ai: scp_download ok", down_result.get("ok") is True, str(down_result)[:300])
        check("ai: scp_download content", os.path.isfile(local_down) and open(local_down, encoding="utf-8").read() == payload)

        print("\n[assistant modes]")
        term = asyncio.run(run_chat('@tool docker_logs {"container":"x"}', assistant="terminal", sessionId=""))
        check("ai: terminal mode restricts tools", not term["toolStarts"], str(term["toolStarts"])[:300])
        sql_mode = asyncio.run(
            run_chat(
                '@tool execute_sql {"asset_id":"%s","database":"demo","sql":"select count(*) as n from users"}' % assets["PostgreSQL demo"]["id"],
                assistant="sql",
            )
        )
        sql_result = (sql_mode["toolResults"] or [{}])[0]
        check("ai: sql mode executes query", sql_result.get("ok") is True and "2" in (sql_result.get("output") or ""), str(sql_result)[:400])
        general = asyncio.run(run_chat("你好", assistant="general"))
        check("ai: general mode still answers", bool(general["done"] and general["text"]))

        print("\n[tool: tunnels]")
        started = asyncio.run(run_chat(f'@tool start_tunnel {{"asset_id":"{tunnel_id}"}}'))
        start_result = (started["toolResults"] or [{}])[0]
        check("ai: start_tunnel ok", start_result.get("ok") is True, str(start_result)[:300])
        tunnel_data = {}
        try:
            tunnel_data = json.loads(start_result.get("output") or "{}")
        except json.JSONDecodeError:
            tunnel_data = {}
        check("ai: tunnel has bind port", bool(tunnel_data.get("bindPort")), str(tunnel_data)[:200])
        if tunnel_data.get("id"):
            stopped = asyncio.run(run_chat(f'@tool stop_tunnel {{"tunnel_id":"{tunnel_data["id"]}"}}'))
            stop_result = (stopped["toolResults"] or [{}])[0]
            check("ai: stop_tunnel ok", stop_result.get("ok") is True and "true" in (stop_result.get("output") or "").lower(), str(stop_result)[:300])
        listing = asyncio.run(run_chat("@tool list_tunnels {}"))
        list_result = (listing["toolResults"] or [{}])[0]
        check("ai: list_tunnels ok", list_result.get("ok") is True, str(list_result)[:300])
    finally:
        batch_exec(ssh_id, f"rm -f {remote}")
        for path in (local_up, local_down):
            try:
                os.unlink(path)
            except OSError:
                pass
        if ORIGINAL_AI is not None:
            providers = []
            for p in ORIGINAL_AI.get("providers") or []:
                item = dict(p)
                item["apiKey"] = ""
                providers.append(item)
            req(
                "/api/settings",
                {"ai": {**ORIGINAL_AI, "providers": providers}},
                method="PUT",
            )
        if MOCK_PROC is not None:
            MOCK_PROC.terminate()
        if FTP_PROC is not None:
            FTP_PROC.terminate()

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
