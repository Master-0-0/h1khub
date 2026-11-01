#!/usr/bin/env python3
"""End-to-end verification for Telnet, SSH agent, SOCKS5/HTTP proxies and tunnels.

Starts the local fixtures in scripts/test_servers.py and a temporary ssh-agent.
Only loopback services are used.

Usage: python3 scripts/test_network.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

import aiohttp

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
PROCS: list[subprocess.Popen] = []
AGENT = None
AGENT_SOCK = "/tmp/h1khub-agent-test.sock"
AGENT_KEY = "/tmp/h1khub-agent-test-key"
MARKER = f"h1khub-agent-{random.randint(1000, 9999)}"


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        BASE + path, data=data, headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
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


def start_fixture(kind: str, port: int, extra: list[str] | None = None) -> None:
    if port_open(port):
        print(f"fixture {kind} already on {port}")
        return
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_servers.py")
    proc = subprocess.Popen(
        [sys.executable, script, kind, "--port", str(port)] + (extra or []),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    PROCS.append(proc)
    for _ in range(50):
        if port_open(port):
            print(f"started {kind} on {port}")
            return
        time.sleep(0.1)
    raise RuntimeError(f"{kind} fixture failed to start")


def run(cmd: list[str], env=None, check_rc=True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
    if check_rc and proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr or proc.stdout}")
    return proc.stdout


# ---------------------------------------------------------------------------
async def ws_session_roundtrip(session_id: str, steps: list[tuple[bytes, bytes]], timeout: float = 10.0) -> bytes:
    """Open a terminal channel, send each (input, expected) step, return output."""
    url = BASE.replace("http://", "ws://") + f"/rpc?token={TOKEN}"
    output = b""
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, heartbeat=10) as ws:
            await ws.send_json({"method": "open", "channel": f"term:{session_id}"})
            deadline = time.time() + timeout

            async def pump(until: bytes | None) -> None:
                nonlocal output
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=max(0.2, deadline - time.time()))
                    except asyncio.TimeoutError:
                        return
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        return
                    packet = json.loads(msg.data)
                    if packet.get("method") == "event":
                        event = packet.get("data") or {}
                        if event.get("type") in ("data", "history"):
                            output += base64.b64decode(event.get("data") or "")
                        elif event.get("type") == "exit":
                            return
                    if until and until in output:
                        return

            await pump(b"login:")
            for text, expected in steps:
                await ws.send_json(
                    {
                        "method": "send",
                        "channel": f"term:{session_id}",
                        "data": {"type": "input", "data": base64.b64encode(text).decode()},
                    }
                )
                await pump(expected)
            await ws.send_json({"method": "close", "channel": f"term:{session_id}"})
    return output


def create_asset(name: str, type_name: str, config: dict, parent: str | None = None) -> str:
    resp = req("/api/assets", {"typeName": type_name, "name": name, "config": config, "parentId": parent})
    d = data_of(resp)
    if not d:
        raise RuntimeError(f"create asset failed: {resp}")
    return d["asset"]["id"]


def delete_asset(asset_id: str) -> None:
    try:
        req(f"/api/assets/{asset_id}", method="DELETE")
    except Exception:
        pass


def find_assets() -> dict:
    return {a["name"]: a for a in data_of(req("/api/assets", method="GET"))["assets"]}


# ---------------------------------------------------------------------------
def test_telnet(suffix: str) -> None:
    print("\n[telnet]")
    start_fixture("telnet", 2323)
    name = f"Telnet E2E {suffix}"
    asset_id = create_asset(name, "Telnet", {"host": "127.0.0.1", "port": 2323, "termType": "xterm-256color"})
    try:
        test = req("/api/test-connection", {"assetId": asset_id})
        check("telnet: connection test", bool(data_of(test)), str(test)[:300])
        started = req("/api/sessions/start", {"assetId": asset_id, "cols": 100, "rows": 30})
        session = (data_of(started) or {}).get("session")
        check("telnet: session started", bool(session), str(started)[:300])
        if session:
            async def scenario():
                return await ws_session_roundtrip(
                    session["id"],
                    [
                        (b"hexlab\n", b"welcome hexlab"),
                        (b"echo telnet-e2e-ok\n", b"telnet-e2e-ok"),
                        (b"uname\n", b"h1khub-telnet-test"),
                    ],
                )

            output = asyncio.run(scenario())
            check("telnet: login greeting", b"welcome hexlab" in output, output.decode(errors="replace")[-300:])
            check("telnet: echo command", b"telnet-e2e-ok" in output, output.decode(errors="replace")[-300:])
            check("telnet: uname", b"h1khub-telnet-test" in output, output.decode(errors="replace")[-300:])
            req(f"/api/sessions/{session['id']}/close")
    finally:
        delete_asset(asset_id)


def setup_agent() -> str:
    global AGENT
    for path in (AGENT_SOCK, AGENT_KEY, AGENT_KEY + ".pub"):
        try:
            os.unlink(path)
        except OSError:
            pass
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", AGENT_KEY, "-C", MARKER])
    with open(AGENT_KEY + ".pub", encoding="utf-8") as fh:
        pub = fh.read().strip()
    os.chmod(AGENT_KEY, 0o600)
    AGENT = subprocess.Popen(["ssh-agent", "-a", AGENT_SOCK], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(50):
        if os.path.exists(AGENT_SOCK):
            break
        time.sleep(0.1)
    env = {**os.environ, "SSH_AUTH_SOCK": AGENT_SOCK}
    run(["ssh-add", AGENT_KEY], env=env)
    # authorize the key for hexlab (password auth), keeping a marker for cleanup
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("127.0.0.1", 22, username="hexlab", password="Hexlab@123", timeout=10, allow_agent=False, look_for_keys=False)
    script = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        f"grep -q '{MARKER}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys"
    )
    _, stdout, stderr = client.exec_command(script)
    out = stdout.read().decode() + stderr.read().decode()
    client.close()
    return pub


def cleanup_agent() -> None:
    try:
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect("127.0.0.1", 22, username="hexlab", password="Hexlab@123", timeout=10, allow_agent=False, look_for_keys=False)
        client.exec_command(f"sed -i '/{MARKER}/d' ~/.ssh/authorized_keys").channel.recv_exit_status()
        client.close()
    except Exception:
        pass
    if AGENT is not None:
        AGENT.terminate()
        try:
            AGENT.wait(timeout=3)
        except Exception:
            AGENT.kill()
    for path in (AGENT_SOCK, AGENT_KEY, AGENT_KEY + ".pub"):
        try:
            os.unlink(path)
        except OSError:
            pass


def test_agent(suffix: str) -> None:
    print("\n[ssh agent]")
    try:
        setup_agent()
    except Exception as exc:
        check("agent: setup", False, str(exc))
        return
    name = f"SSH Agent {suffix}"
    asset_id = create_asset(
        name,
        "SSH",
        {
            "host": "127.0.0.1",
            "port": 22,
            "username": "hexlab",
            "authType": "agent",
            "agentSocket": AGENT_SOCK,
            "connectTimeout": 10,
        },
    )
    try:
        test = req("/api/test-connection", {"assetId": asset_id})
        d = data_of(test)
        check("agent: authenticated connection", bool(d), str(test)[:400])
        batch = req("/api/batch/exec", {"assetIds": [asset_id], "command": "echo agent-e2e-ok && whoami"})
        result = (data_of(batch) or {}).get("results", [{}])[0]
        check("agent: exec over agent auth", "agent-e2e-ok" in (result.get("stdout") or ""), str(result)[:300])
        # wrong socket must fail cleanly
        bad = req(
            "/api/test-connection",
            {
                "typeName": "SSH",
                "config": {"host": "127.0.0.1", "port": 22, "username": "hexlab", "authType": "agent", "agentSocket": "/tmp/no-such-agent.sock"},
            },
        )
        check("agent: missing socket reports error", bad.get("__http_error") == 400, str(bad)[:300])
    finally:
        delete_asset(asset_id)
        cleanup_agent()


def test_proxy(suffix: str, kind: str, port: int) -> None:
    print(f"\n[{kind} proxy]")
    start_fixture(f"{kind}-proxy", port)
    name = f"SSH Proxy {kind} {suffix}"
    asset_id = create_asset(
        name,
        "SSH",
        {
            "host": "127.0.0.1",
            "port": 22,
            "username": "hexlab",
            "authType": "password",
            "password": "Hexlab@123",
            "connectTimeout": 10,
            "proxy": {"enabled": True, "type": kind, "host": "127.0.0.1", "port": port},
        },
    )
    try:
        test = req("/api/test-connection", {"assetId": asset_id})
        check(f"{kind}: SSH handshake through proxy", bool(data_of(test)), str(test)[:400])
        batch = req("/api/batch/exec", {"assetIds": [asset_id], "command": f"echo {kind}-proxy-ok"})
        result = (data_of(batch) or {}).get("results", [{}])[0]
        check(f"{kind}: exec through proxy", f"{kind}-proxy-ok" in (result.get("stdout") or ""), str(result)[:300])
        # a dead proxy must fail with a clear error
        bad = req(
            "/api/test-connection",
            {
                "typeName": "SSH",
                "config": {
                    "host": "127.0.0.1",
                    "port": 22,
                    "username": "hexlab",
                    "authType": "password",
                    "password": "Hexlab@123",
                    "proxy": {"enabled": True, "type": kind, "host": "127.0.0.1", "port": 9},
                },
            },
        )
        check(f"{kind}: unreachable proxy errors", bad.get("__http_error") == 400, str(bad)[:300])
    finally:
        delete_asset(asset_id)


def test_tunnels(suffix: str, ssh_asset: str) -> None:
    print("\n[tunnels]")
    specs = [
        ("local", {"type": "local", "sshAssetId": ssh_asset, "bindHost": "127.0.0.1", "bindPort": 0, "targetHost": "127.0.0.1", "targetPort": 22}),
        ("dynamic", {"type": "dynamic", "sshAssetId": ssh_asset, "bindHost": "127.0.0.1", "bindPort": 0}),
        ("remote", {"type": "remote", "sshAssetId": ssh_asset, "bindHost": "127.0.0.1", "bindPort": 0, "targetHost": "127.0.0.1", "targetPort": 22}),
    ]
    created = []
    try:
        for kind, cfg in specs:
            asset_id = create_asset(f"Tunnel E2E {kind} {suffix}", "SSHTunnel", cfg)
            created.append(asset_id)
            started = req("/api/tunnels/start", {"assetId": asset_id})
            status = data_of(started)
            check(f"tunnel {kind}: start", bool(status and status.get("bindPort")), str(started)[:300])
            if not status:
                continue
            if kind == "dynamic":
                sock = socket.create_connection(("127.0.0.1", status["bindPort"]), timeout=8)
                sock.sendall(b"\x05\x01\x00")
                greeting = sock.recv(2)
                sock.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", 22))
                reply = sock.recv(10)
                sock.sendall(b"SSH-2.0-dynamic-probe\r\n")
                banner = sock.recv(200)
                sock.close()
                check("tunnel dynamic: SOCKS5 handshake", greeting == b"\x05\x00", str(greeting))
                check("tunnel dynamic: SOCKS5 connect", len(reply) >= 2 and reply[1] == 0, str(reply))
                check("tunnel dynamic: banner via SOCKS5", banner.startswith(b"SSH-2.0"), str(banner[:60]))
            else:
                probe = req("/api/tunnels/probe", {"tunnelId": status["id"], "payload": "SSH-2.0-tunnel-probe\r\n"})
                banner = (data_of(probe) or {}).get("banner") or ""
                check(f"tunnel {kind}: forwarded banner", banner.startswith("SSH-2.0"), str(probe)[:300])
            stopped = req(f"/api/tunnels/{status['id']}/stop")
            check(f"tunnel {kind}: stop", (data_of(stopped) or {}).get("stopped") is True, str(stopped)[:200])
        listing = data_of(req("/api/tunnels", method="GET"))
        check("tunnel: list empty after stop", listing is not None and listing.get("tunnels") == [], str(listing)[:200])
    finally:
        for asset_id in created:
            delete_asset(asset_id)
        for tunnel in (data_of(req("/api/tunnels", method="GET")) or {}).get("tunnels", []):
            req(f"/api/tunnels/{tunnel['id']}/stop")


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    assets = find_assets()
    try:
        test_telnet(suffix)
        test_agent(suffix)
        test_proxy(suffix, "socks5", 1081)
        test_proxy(suffix, "http", 8081)
        test_tunnels(suffix, assets["SSH hexlab"]["id"])
    finally:
        for proc in PROCS:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
