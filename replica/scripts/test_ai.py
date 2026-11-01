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
import http.server
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import aiohttp
from aiohttp import web

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


def ftp_names(asset_id: str, path: str = "/") -> list[str]:
    """List an FTP directory through the agent so the check stays server-agnostic."""
    chat = asyncio.run(run_chat(f'@tool ftp_list {{"asset_id":"{asset_id}","path":"{path}"}}'))
    result = (chat["toolResults"] or [{}])[0]
    try:
        data = json.loads(result.get("output") or "{}")
    except json.JSONDecodeError:
        return []
    return [str(entry.get("name")) for entry in data.get("entries") or []]


DOCKER_STUB = """#!/bin/sh
if [ "$1" = "ps" ]; then
  printf '%s\\n' '{"ID":"stub0001","Names":"stub-container","Image":"busybox","State":"running"}'
  printf '%s\\n' '{"ID":"boom0001","Names":"boom-container","Image":"busybox","State":"running"}'
  exit 0
fi
case "$1" in
  start|stop|restart)
    if [ "$2" = "boom-container" ]; then
      echo "Error response from daemon: cannot $1 container" >&2
      exit 1
    fi
    echo "$2"
    exit 0
    ;;
esac
echo "stub docker: unsupported $*" >&2
exit 1
"""


def docker_action_stub_tests() -> None:
    """Hermetic docker_container_action coverage with a stub `docker` CLI.

    The agent's tool layer (approval -> existence check -> CLI action -> error
    shaping) is exercised in-process; no Docker daemon or real container is
    touched. Path-confinement for FTP is checked with a synthetic home directory.
    """
    backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    import ai
    from docker_svc import DockerService
    from ftp_svc import FTPClient, FTPError

    stub_dir = tempfile.mkdtemp(prefix="h1khub-docker-stub-")
    stub_path = os.path.join(stub_dir, "docker")
    with open(stub_path, "w", encoding="utf-8") as fh:
        fh.write(DOCKER_STUB)
    os.chmod(stub_path, 0o755)
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = stub_dir + os.pathsep + old_path

    async def emit(event):
        if event.get("type") == "approval":
            future = runner.pending.get(event["id"])
            if future is not None and not future.done():
                future.set_result(True)

    async def call(args):
        return await runner._run_tool("docker_container_action", args)

    names = [tool["name"] for tool in ai.TOOLS]
    handlers = {name[5:] for name in dir(ai.ChatRunner) if name.startswith("tool_")}
    check(
        "ai: TOOLS = 26 and handlers match",
        len(names) == 26 and set(names) == handlers,
        f"count={len(names)} orphan={sorted(set(names) ^ handlers)}",
    )

    try:
        class StubHub:
            async def docker(self, asset_id):
                return DockerService({"mode": "local"}), None

        runner = ai.ChatRunner(StubHub(), None, emit, {})
        for action in ("start", "stop", "restart"):
            ok, out = asyncio.run(call({"container": "stub-container", "action": action}))
            check(
                f"ai: docker_container_action approved {action}",
                ok is True and out.get("action") == action and out.get("container") == "stub-container",
                str(out)[:300],
            )
        ok, out = asyncio.run(call({"container": "missing-container", "action": "start"}))
        check(
            "ai: docker_container_action rejects unknown container",
            ok is False and (out.get("error") or {}).get("code") == "container_not_found",
            str(out)[:300],
        )
        ok, out = asyncio.run(call({"container": "boom-container", "action": "stop"}))
        check(
            "ai: docker_container_action surfaces CLI stderr",
            ok is False
            and (out.get("error") or {}).get("code") == "docker_cli_error"
            and "cannot stop" in str(out),
            str(out)[:300],
        )
        ok, out = asyncio.run(call({"container": "stub-container", "action": "remove"}))
        check(
            "ai: docker_container_action validates action",
            ok is False and (out.get("error") or {}).get("code") == "invalid_action",
            str(out)[:300],
        )
        ok, out = asyncio.run(call({"action": "start"}))
        check(
            "ai: docker_container_action validates container",
            ok is False and (out.get("error") or {}).get("code") == "missing_container",
            str(out)[:300],
        )
    finally:
        os.environ["PATH"] = old_path
        shutil.rmtree(stub_dir, ignore_errors=True)

    client = FTPClient({"host": "127.0.0.1"})
    client.home_path = "/home/hexftp"
    blocked = []
    for path in ("../escape.txt", "/etc/passwd", "sub/../../x"):
        try:
            client.safe_path(path)
            blocked.append(path)
        except FTPError:
            pass
    check(
        "ai: ftp path guard blocks escapes",
        not blocked and client.safe_path("/home/hexftp/ok.txt") == "/home/hexftp/ok.txt",
        f"allowed={blocked}",
    )


def websearch_stub_tests() -> None:
    """Hermetic websearch coverage against a local JSON search stub.

    Proves the degradation contract end to end at the handler layer: disabled
    and unconfigured settings make zero requests, rejection never touches the
    network, and an approved call parses/filters/truncates local stub results.
    """
    backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    import ai

    hits = {"count": 0, "queries": []}
    long_snippet = "长摘要" * 700

    async def handler(request):
        hits["count"] += 1
        hits["queries"].append(request.query.get("q") or "")
        kind = request.match_info["kind"]
        if kind == "slow":
            await asyncio.sleep(2)
            return web.json_response({"results": []})
        if kind == "error":
            return web.Response(status=500, text="boom")
        if kind == "bad":
            return web.Response(text="<html>not json</html>", content_type="text/html")
        return web.json_response(
            {
                "results": [
                    {"title": "Allowed A", "url": "https://allowed.example/a", "snippet": long_snippet},
                    {"title": "Blocked B", "url": "https://blocked.example/b", "snippet": "blocked"},
                    {"title": "Allowed C", "url": "https://allowed.example/c", "snippet": "ok"},
                    {"title": "Bad scheme", "url": "javascript:alert(1)", "snippet": "x"},
                    {"title": "Duplicate", "url": "https://allowed.example/a", "snippet": "dup"},
                    *[
                        {
                            "title": f"Extra {i}",
                            "url": f"https://allowed.example/{i}",
                            "snippet": "x",
                        }
                        for i in range(12)
                    ],
                ]
            }
        )

    class StubStore:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_settings(self):
            return {"websearch": self.cfg}

    async def run_all() -> None:
        app = web.Application()
        app.router.add_get("/{kind}", handler)
        http_runner = web.AppRunner(app)
        await http_runner.setup()
        site = web.TCPSite(http_runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        async def case(cfg, args, approve=True):
            approvals = []
            runner = None

            async def emit(event):
                if event.get("type") == "approval":
                    approvals.append(event)
                    future = runner.pending.get(event["id"])
                    if future is not None and not future.done():
                        future.set_result(approve)

            runner = ai.ChatRunner(None, StubStore(cfg), emit, {})
            ok, out = await runner._run_tool("websearch", args)
            return ok, out, approvals

        def reset():
            hits["count"] = 0
            hits["queries"].clear()

        try:
            reset()
            ok, out, approvals = await case({}, {"query": "hexhub"})
            check(
                "ai: websearch disabled by default",
                ok is False
                and (out.get("error") or {}).get("code") == "websearch_disabled"
                and (out.get("error") or {}).get("status") == 501
                and approvals == []
                and hits["count"] == 0,
                str(out)[:300],
            )

            reset()
            ok, out, approvals = await case({"enabled": True, "endpoint": ""}, {"query": "hexhub"})
            check(
                "ai: websearch unconfigured makes no request",
                ok is False
                and (out.get("error") or {}).get("code") == "websearch_not_configured"
                and (out.get("error") or {}).get("status") == 501
                and approvals == []
                and hits["count"] == 0,
                str(out)[:300],
            )

            reset()
            ok, out, approvals = await case(
                {"enabled": True, "endpoint": "ftp://example.com/search"}, {"query": "hexhub"}
            )
            check(
                "ai: websearch rejects non-http endpoint",
                ok is False
                and (out.get("error") or {}).get("code") == "websearch_endpoint_invalid"
                and approvals == []
                and hits["count"] == 0,
                str(out)[:300],
            )

            reset()
            ok, out, approvals = await case(
                {"enabled": True, "endpoint": base + "/ok"}, {"query": "   "}
            )
            check(
                "ai: websearch validates query before approval",
                ok is False
                and (out.get("error") or {}).get("code") == "invalid_query"
                and approvals == []
                and hits["count"] == 0,
                str(out)[:300],
            )

            reset()
            ok, out, approvals = await case(
                {"enabled": True, "endpoint": base + "/ok"}, {"query": "a\nb"}
            )
            check(
                "ai: websearch rejects control characters",
                ok is False and (out.get("error") or {}).get("code") == "invalid_query",
                str(out)[:300],
            )

            reset()
            ok, out, approvals = await case(
                {"enabled": True, "endpoint": base + "/ok"},
                {"query": "hexhub", "allowed_domains": "allowed.example"},
            )
            check(
                "ai: websearch validates domain lists",
                ok is False
                and (out.get("error") or {}).get("code") == "invalid_domains"
                and approvals == [],
                str(out)[:300],
            )

            reset()
            ok, out, approvals = await case(
                {"enabled": True, "endpoint": base + "/ok"}, {"query": "hexhub"}, approve=False
            )
            check(
                "ai: websearch rejection skips network",
                ok is False and "拒绝" in str(out) and len(approvals) == 1 and hits["count"] == 0,
                str(out)[:300],
            )

            reset()
            ok, out, approvals = await case(
                {"enabled": True, "endpoint": base + "/ok", "maxResults": 8},
                {
                    "query": "h1k hub",
                    "max_results": 2,
                    "allowed_domains": ["allowed.example"],
                    "blocked_domains": ["blocked.example"],
                },
            )
            results = (out or {}).get("results") or []
            snippet = results[0]["snippet"] if results else ""
            check(
                "ai: websearch approved parses and filters results",
                ok is True
                and out.get("count") == 2
                and out.get("engine") == "127.0.0.1"
                and out.get("truncated") is True
                and all("allowed.example" in item["url"] for item in results)
                and not any("blocked.example" in item["url"] for item in results)
                and len(approvals) == 1
                and approvals[0].get("kind") == "websearch"
                and (approvals[0].get("detail") or {}).get("endpoint") == "127.0.0.1"
                and hits["count"] == 1
                and hits["queries"] == ["h1k hub"],
                str(out)[:400],
            )
            check(
                "ai: websearch truncates long snippet",
                "truncated" in snippet and len(snippet) < 1200,
                f"len={len(snippet)}",
            )

            reset()
            ok, out, _ = await case(
                {"enabled": True, "endpoint": base + "/ok"}, {"query": "x", "max_results": 99}
            )
            check(
                "ai: websearch clamps max_results to 10",
                ok is True and out.get("count") == 10,
                str(out)[:300],
            )

            reset()
            ok, out, _ = await case(
                {"enabled": True, "endpoint": base + "/ok?q={query}&format=json"}, {"query": "a b"}
            )
            check(
                "ai: websearch expands {query} template",
                ok is True and hits["queries"] == ["a b"],
                str(hits["queries"])[:200],
            )

            reset()
            ok, out, _ = await case(
                {"enabled": True, "endpoint": base + "/bad"}, {"query": "hexhub"}
            )
            check(
                "ai: websearch rejects non-JSON response",
                ok is False and (out.get("error") or {}).get("code") == "websearch_bad_response",
                str(out)[:300],
            )

            reset()
            ok, out, _ = await case(
                {"enabled": True, "endpoint": base + "/error"}, {"query": "hexhub"}
            )
            check(
                "ai: websearch surfaces HTTP errors",
                ok is False
                and (out.get("error") or {}).get("code") == "websearch_http_error"
                and (out.get("error") or {}).get("status") == 502,
                str(out)[:300],
            )

            reset()
            ok, out, _ = await case(
                {"enabled": True, "endpoint": base + "/slow", "timeoutSeconds": 1},
                {"query": "hexhub"},
            )
            check(
                "ai: websearch times out",
                ok is False and (out.get("error") or {}).get("code") == "websearch_timeout",
                str(out)[:300],
            )
        finally:
            await http_runner.cleanup()

    asyncio.run(run_all())


def start_search_stub():
    """Start a loopback JSON search endpoint for the mock-LLM E2E cases."""
    hits = {"count": 0, "queries": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            parsed = urllib.parse.urlsplit(self.path)
            hits["count"] += 1
            hits["queries"].append((urllib.parse.parse_qs(parsed.query).get("q") or [""])[0])
            payload = {
                "results": [
                    {
                        "title": f"H1KHub 搜索结果 {i}",
                        "url": f"https://search.example/{i}",
                        "snippet": f"本地桩摘要 {i}",
                    }
                    for i in range(1, 6)
                ]
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence the per-request stderr line
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, hits


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
    ORIGINAL_WEBSEARCH = json.loads(json.dumps(settings.get("websearch") or {}))
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

    os.makedirs("/tmp/h1khub-ftp", exist_ok=True)
    ftp_upload_local = os.path.join(tempfile.gettempdir(), f"h1khub-ai-ftp-up-{suffix}.txt")
    ftp_download_local = os.path.join(tempfile.gettempdir(), f"h1khub-ai-ftp-down-{suffix}.txt")
    ftp_payload = f"AI FTP E2E {suffix}\nline2\n"
    with open(ftp_upload_local, "w", encoding="utf-8") as fh:
        fh.write(ftp_payload)
    ftp_remote = f"/ai-upload-{suffix}.txt"
    ftp_renamed = f"/ai-renamed-{suffix}.txt"
    ftp_name_remote = ftp_remote.lstrip("/")
    ftp_name_renamed = ftp_renamed.lstrip("/")

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

        print("\n[tool: ftp upload/download/rename/delete]")
        rej_up = asyncio.run(
            run_chat(
                f'@tool ftp_upload {{"asset_id":"{ftp_id}","local_path":"{ftp_upload_local}","remote_path":"{ftp_remote}"}}',
                approve=False,
            )
        )
        rej_up_result = (rej_up["toolResults"] or [{}])[0]
        check(
            "ai: ftp_upload rejected",
            rej_up_result.get("ok") is False and "拒绝" in (rej_up_result.get("output") or ""),
            str(rej_up_result)[:300],
        )
        check("ai: ftp_upload rejection leaves no file", ftp_name_remote not in ftp_names(ftp_id))

        up = asyncio.run(
            run_chat(f'@tool ftp_upload {{"asset_id":"{ftp_id}","local_path":"{ftp_upload_local}","remote_path":"{ftp_remote}"}}')
        )
        up_result = (up["toolResults"] or [{}])[0]
        check(
            "ai: ftp_upload approved",
            up_result.get("ok") is True and up["approvals"] == 1,
            str(up_result)[:300],
        )
        check("ai: ftp_upload appears in listing", ftp_name_remote in ftp_names(ftp_id))

        down = asyncio.run(
            run_chat(
                f'@tool ftp_download {{"asset_id":"{ftp_id}","remote_path":"{ftp_remote}","local_path":"{ftp_download_local}"}}'
            )
        )
        down_result = (down["toolResults"] or [{}])[0]
        check(
            "ai: ftp_download approved",
            down_result.get("ok") is True and down["approvals"] == 1,
            str(down_result)[:300],
        )
        check(
            "ai: ftp_download content",
            os.path.isfile(ftp_download_local)
            and open(ftp_download_local, encoding="utf-8").read() == ftp_payload,
        )

        moved = asyncio.run(
            run_chat(
                f'@tool ftp_rename {{"asset_id":"{ftp_id}","from_path":"{ftp_remote}","to_path":"{ftp_renamed}"}}'
            )
        )
        moved_result = (moved["toolResults"] or [{}])[0]
        check(
            "ai: ftp_rename approved",
            moved_result.get("ok") is True and moved["approvals"] == 1,
            str(moved_result)[:300],
        )
        names_after_rename = ftp_names(ftp_id)
        check(
            "ai: ftp_rename moved file",
            ftp_name_renamed in names_after_rename and ftp_name_remote not in names_after_rename,
            str(names_after_rename)[:300],
        )

        rej_del = asyncio.run(
            run_chat(f'@tool ftp_delete {{"asset_id":"{ftp_id}","path":"{ftp_renamed}"}}', approve=False)
        )
        rej_del_result = (rej_del["toolResults"] or [{}])[0]
        check(
            "ai: ftp_delete rejected",
            rej_del_result.get("ok") is False and "拒绝" in (rej_del_result.get("output") or ""),
            str(rej_del_result)[:300],
        )
        check("ai: ftp_delete rejection keeps file", ftp_name_renamed in ftp_names(ftp_id))

        deleted = asyncio.run(run_chat(f'@tool ftp_delete {{"asset_id":"{ftp_id}","path":"{ftp_renamed}"}}'))
        deleted_result = (deleted["toolResults"] or [{}])[0]
        check(
            "ai: ftp_delete approved",
            deleted_result.get("ok") is True and deleted["approvals"] == 1,
            str(deleted_result)[:300],
        )
        check("ai: ftp_delete removed file", ftp_name_renamed not in ftp_names(ftp_id))

        bad_rename = asyncio.run(
            run_chat(f'@tool ftp_rename {{"asset_id":"{ftp_id}","from_path":"{ftp_renamed}"}}')
        )
        bad_rename_result = (bad_rename["toolResults"] or [{}])[0]
        check(
            "ai: ftp_rename validates args",
            bad_rename_result.get("ok") is False and bad_rename["approvals"] == 0,
            str(bad_rename_result)[:300],
        )

        print("\n[tool: docker_container_action]")
        docker_action_stub_tests()
        rej_docker = asyncio.run(
            run_chat('@tool docker_container_action {"action":"restart","container":"h1khub-ai-none"}', approve=False)
        )
        rej_docker_result = (rej_docker["toolResults"] or [{}])[0]
        check(
            "ai: docker_container_action rejected",
            rej_docker_result.get("ok") is False and "拒绝" in (rej_docker_result.get("output") or ""),
            str(rej_docker_result)[:300],
        )
        bad_docker = asyncio.run(
            run_chat('@tool docker_container_action {"action":"remove","container":"h1khub-ai-none"}')
        )
        bad_docker_result = (bad_docker["toolResults"] or [{}])[0]
        check(
            "ai: docker_container_action validates before approval",
            bad_docker_result.get("ok") is False
            and bad_docker["approvals"] == 0
            and "invalid_action" in (bad_docker_result.get("output") or ""),
            str(bad_docker_result)[:300],
        )

        print("\n[tool: websearch]")
        websearch_stub_tests()
        search_server, search_thread, search_hits = start_search_stub()
        search_url = f"http://127.0.0.1:{search_server.server_address[1]}/search"
        try:
            disabled = asyncio.run(run_chat('@tool websearch {"query":"h1khub off"}'))
            disabled_result = (disabled["toolResults"] or [{}])[0]
            check(
                "ai: websearch disabled makes no request",
                disabled_result.get("ok") is False
                and "websearch_disabled" in (disabled_result.get("output") or "")
                and disabled["approvals"] == 0
                and search_hits["count"] == 0,
                str(disabled_result)[:300],
            )

            req(
                "/api/settings",
                {"websearch": {"enabled": True, "endpoint": "", "timeoutSeconds": 5, "maxResults": 3}},
                method="PUT",
            )
            unconfigured = asyncio.run(run_chat('@tool websearch {"query":"h1khub unconfigured"}'))
            unconfigured_result = (unconfigured["toolResults"] or [{}])[0]
            check(
                "ai: websearch unconfigured makes no request",
                unconfigured_result.get("ok") is False
                and "websearch_not_configured" in (unconfigured_result.get("output") or "")
                and unconfigured["approvals"] == 0
                and search_hits["count"] == 0,
                str(unconfigured_result)[:300],
            )

            req(
                "/api/settings",
                {
                    "websearch": {
                        "enabled": True,
                        "endpoint": search_url + "?q={query}&format=json",
                        "timeoutSeconds": 5,
                        "maxResults": 3,
                    }
                },
                method="PUT",
            )
            approved = asyncio.run(run_chat('@tool websearch {"query":"h1khub e2e","max_results":3}'))
            approved_result = (approved["toolResults"] or [{}])[0]
            try:
                approved_data = json.loads(approved_result.get("output") or "{}")
            except json.JSONDecodeError:
                approved_data = {}
            check(
                "ai: websearch approved returns local stub results",
                approved_result.get("ok") is True
                and approved["approvals"] == 1
                and approved_data.get("count") == 3
                and "H1KHub 搜索结果 1" in (approved_result.get("output") or ""),
                str(approved_result)[:400],
            )
            check(
                "ai: websearch sends query to configured endpoint",
                search_hits["queries"] == ["h1khub e2e"],
                str(search_hits)[:300],
            )

            before_reject = search_hits["count"]
            rejected = asyncio.run(
                run_chat('@tool websearch {"query":"h1khub reject"}', approve=False)
            )
            rejected_result = (rejected["toolResults"] or [{}])[0]
            check(
                "ai: websearch rejected",
                rejected_result.get("ok") is False
                and "拒绝" in (rejected_result.get("output") or "")
                and rejected["approvals"] == 1
                and search_hits["count"] == before_reject,
                str(rejected_result)[:300],
            )

            before_bad = search_hits["count"]
            invalid = asyncio.run(run_chat('@tool websearch {"query":""}'))
            invalid_result = (invalid["toolResults"] or [{}])[0]
            check(
                "ai: websearch validates before approval",
                invalid_result.get("ok") is False
                and "invalid_query" in (invalid_result.get("output") or "")
                and invalid["approvals"] == 0
                and search_hits["count"] == before_bad,
                str(invalid_result)[:300],
            )
        finally:
            req("/api/settings", {"websearch": ORIGINAL_WEBSEARCH}, method="PUT")
            search_server.shutdown()
            search_server.server_close()

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
        for path in (local_up, local_down, ftp_upload_local, ftp_download_local):
            try:
                os.unlink(path)
            except OSError:
                pass
        for name in (ftp_remote, ftp_renamed):
            try:
                asyncio.run(run_chat(f'@tool ftp_delete {{"asset_id":"{ftp_id}","path":"{name}"}}'))
            except Exception:
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
