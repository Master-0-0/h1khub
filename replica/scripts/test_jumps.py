#!/usr/bin/env python3
"""End-to-end verification for recursive native SSH jump chains.

Starts five throwaway OpenSSH instances on loopback (highest ports are chosen
randomly on every run so the TOFU state is always fresh), creates ``zz-`` test
assets through the API, and verifies:

  * no jump / 1-4 jump layers, banner per hop and host-key state per hop;
  * known_hosts TOFU, second connection = known, changed host key = mismatch;
  * cycle and depth limits fail fast with a clear 400 error;
  * a dead middle hop leaves no transport/session/tunnel behind;
  * an outermost-hop SOCKS5 proxy is honoured;
  * DB/Redis sshAssetId tunnels, Docker SSH mode, SFTP, SCP and terminal
    sessions all traverse a 3-layer chain;
  * the asset dialog shows the jump chain and rejects cycles/self reference.

Everything is cleaned up: assets, sessions, tunnels, sshd processes, the
temporary lab directory and the temporary known_hosts entries.

Usage: python3 scripts/test_jumps.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
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

import paramiko

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_check import CDP, launch  # noqa: E402

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []

LAB = os.path.expanduser("~/.hexhub-hop-lab")
KNOWN_HOSTS = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
KNOWN_HOSTS = os.path.join(KNOWN_HOSTS, "known_hosts")
SUFFIX = "%04x" % random.randint(0, 0xFFFF)
BANNERS = ["HOP-1", "HOP-2", "HOP-3", "HOP-4", "TARGET-5"]
PORTS: list[int] = []
SSHD_PROCS: list[subprocess.Popen] = []
SSHD_BY_INDEX: dict[int, subprocess.Popen] = {}
EXTRA_PROCS: list[subprocess.Popen] = []
CREATED_ASSETS: list[str] = []
SESSIONS: list[str] = []
TUNNELS: list[str] = []
DEAD_PORT = 0

KEY_ASSET = ""
HOPS: list[str] = []
TARGET = ""
ASSET_IDS: dict[str, str] = {}


# --------------------------------------------------------------- HTTP helpers
def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method: str = "POST", raw_body: bytes | None = None):
    data = raw_body
    headers = {"X-H1KHub-Token": TOKEN}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            payload = resp.read()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"__raw": payload}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return {"__http_error": exc.code, "detail": json.loads(text)}
        except Exception:
            return {"__http_error": exc.code, "detail": text}


def data_of(resp):
    if isinstance(resp, dict) and resp.get("ok") and "data" in resp:
        return resp["data"]
    return None


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail[:500]}")


def error_of(resp) -> str:
    detail = resp.get("detail") if isinstance(resp, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("error") or detail)
    return str(detail or resp)


def create_asset(name: str, type_name: str, config: dict) -> str:
    resp = req("/api/assets", {"typeName": type_name, "name": name, "config": config})
    data = data_of(resp)
    if not data:
        raise RuntimeError(f"create asset failed: {resp}")
    asset_id = data["asset"]["id"]
    CREATED_ASSETS.append(asset_id)
    return asset_id


def update_asset(asset_id: str, config: dict):
    return req(f"/api/assets/{asset_id}", {"config": config}, "PUT")


def delete_asset(asset_id: str) -> None:
    try:
        req(f"/api/assets/{asset_id}", method="DELETE")
    except Exception:
        pass


def find_assets() -> dict:
    data = data_of(req("/api/assets", method="GET")) or {}
    return {a["name"]: a for a in data.get("assets", [])}


def asset_count() -> int:
    data = data_of(req("/api/assets", method="GET")) or {}
    return len(data.get("assets") or [])


# ------------------------------------------------------------- lab helpers
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def allocate_ports(count: int) -> list[int]:
    out: list[int] = []
    while len(out) < count:
        port = free_port()
        if port not in out:
            out.append(port)
    return out


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, opened: bool = True, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port) == opened:
            return True
        time.sleep(0.1)
    return False


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr or proc.stdout}")


def sshd_config(index: int) -> str:
    port = PORTS[index]
    return f"""Port {port}
ListenAddress 127.0.0.1
HostKey {LAB}/host_{index}
PidFile {LAB}/sshd_{index}.pid
AuthorizedKeysFile {LAB}/client_key.pub
AllowUsers {getpass.getuser()}
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
StrictModes no
UseDNS no
Banner {LAB}/banner_{index}
LogLevel VERBOSE
Subsystem sftp internal-sftp
"""


def start_sshd(index: int) -> None:
    cfg = f"{LAB}/sshd_{index}.conf"
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(sshd_config(index))
    run(["/usr/sbin/sshd", "-t", "-f", cfg])
    proc = subprocess.Popen(
        ["/usr/sbin/sshd", "-D", "-f", cfg, "-E", f"{LAB}/sshd_{index}.log"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    SSHD_PROCS.append(proc)
    SSHD_BY_INDEX[index] = proc
    if not wait_port(PORTS[index]):
        raise RuntimeError(f"sshd {index} failed to listen on {PORTS[index]}")


def stop_sshd(index: int) -> None:
    """Stop only the daemon instance for one lab index (we run them with -D)."""
    port = PORTS[index]
    proc = SSHD_BY_INDEX.pop(index, None)
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        if proc in SSHD_PROCS:
            SSHD_PROCS.remove(proc)
    if port_open(port):
        # Fallback for an instance that outlived its tracked child.
        try:
            pid_file = f"{LAB}/sshd_{index}.pid"
            with open(pid_file, encoding="utf-8") as fh:
                pid = int(fh.read().strip() or 0)
            if pid > 0:
                os.kill(pid, 15)
        except Exception:
            pass
    wait_port(port, opened=False, timeout=5)


def setup_lab() -> None:
    global DEAD_PORT
    shutil.rmtree(LAB, ignore_errors=True)
    os.makedirs(LAB, 0o700)
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", f"{LAB}/client_key"])
    for index, banner in enumerate(BANNERS):
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", f"{LAB}/host_{index}"])
        with open(f"{LAB}/banner_{index}", "w", encoding="utf-8") as fh:
            fh.write(banner + "\n")
    ports = allocate_ports(len(BANNERS))
    PORTS.extend(ports)
    for index in range(len(BANNERS)):
        start_sshd(index)
    DEAD_PORT = free_port()
    while DEAD_PORT in PORTS:
        DEAD_PORT = free_port()


def restart_sshd_with_new_key(index: int) -> bytes:
    """Replace a host key, restart that instance, return the original key bytes."""
    key_path = f"{LAB}/host_{index}"
    with open(key_path, "rb") as fh:
        original = fh.read()
    stop_sshd(index)
    os.unlink(key_path)
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key_path])
    start_sshd(index)
    return original


def restore_sshd_key(index: int, original: bytes) -> None:
    stop_sshd(index)
    key_path = f"{LAB}/host_{index}"
    with open(key_path, "wb") as fh:
        fh.write(original)
    os.chmod(key_path, 0o600)
    start_sshd(index)


# --------------------------------------------------------- known_hosts helpers
def host_key_entry(port: int) -> str:
    return f"[127.0.0.1]:{port}"


def known_hosts_has(port: int) -> bool:
    if not os.path.exists(KNOWN_HOSTS):
        return False
    keys = paramiko.HostKeys()
    try:
        keys.load(KNOWN_HOSTS)
    except Exception:
        return False
    return bool(keys.lookup(host_key_entry(port)))


def clean_known_hosts(ports: list[int]) -> None:
    if not os.path.exists(KNOWN_HOSTS):
        return
    keys = paramiko.HostKeys()
    try:
        keys.load(KNOWN_HOSTS)
    except Exception:
        return
    changed = False
    for port in ports:
        if keys.pop(host_key_entry(port), None) is not None:
            changed = True
    # Also drop entries left by an aborted earlier run of this suite. Real lab
    # fixtures never use loopback ports above 20000.
    for entry in list(keys.keys()):
        if not entry.startswith("[127.0.0.1]:"):
            continue
        try:
            port = int(entry.rsplit(":", 1)[1])
        except ValueError:
            continue
        if port >= 20000 and keys.pop(entry, None) is not None:
            changed = True
    if changed:
        keys.save(KNOWN_HOSTS)
        try:
            os.chmod(KNOWN_HOSTS, 0o600)
        except OSError:
            pass


# --------------------------------------------------------------- API checks
def test_conn(asset_id: str):
    return req("/api/test-connection", {"assetId": asset_id})


def banner_chain(resp) -> list[str]:
    data = data_of(resp) or {}
    return [hop.get("banner") for hop in (data.get("chain") or [])]


def hop_states(resp) -> list[str]:
    data = data_of(resp) or {}
    return [hop.get("hostKey") for hop in (data.get("chain") or [])]


def set_chain(layers: int) -> None:
    """target.jump=hop1, hop1.jump=hop2 ... with exactly ``layers`` jump hosts."""
    update_asset(TARGET, {"jumpAssetId": HOPS[0] if layers >= 1 else ""})
    for index, hop in enumerate(HOPS):
        update_asset(hop, {"jumpAssetId": HOPS[index + 1] if index + 1 < layers else ""})


def expect_chain(name: str, layers: int):
    # Expected connection order: outermost hop -> ... -> HOP-1 -> TARGET-5.
    expected = [BANNERS[i] for i in range(layers - 1, -1, -1)] + [BANNERS[4]]
    resp = test_conn(TARGET)
    got = banner_chain(resp)
    check(f"{name}: 连接成功且逐跳 banner 正确", got == expected, f"expected={expected} got={got} resp={resp}")
    return resp


# ------------------------------------------------------------ async terminal
async def ws_roundtrip(session_id: str, commands: list[str], timeout: float = 15.0) -> bytes:
    import aiohttp

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

            await asyncio.sleep(0.3)
            for command, marker in commands:
                await ws.send_json(
                    {
                        "method": "send",
                        "channel": f"term:{session_id}",
                        "data": {"type": "input", "data": base64.b64encode((command + "\n").encode()).decode()},
                    }
                )
                await pump(marker.encode())
            await ws.send_json({"method": "close", "channel": f"term:{session_id}"})
    return output


def run_async(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ UI check
async def ui_jump_dialog(asset_id: str, asset_name: str, cycle_name: str, hop1_id: str) -> None:
    proc, ws_url = await launch(BASE + "/")
    cdp = CDP(ws_url)
    errors: list = []
    try:
        await cdp.connect()
        await cdp.send("Runtime.enable")
        await cdp.send("Page.enable")
        await cdp.send("Log.enable")
        for _ in range(80):
            rows = await cdp.evaluate("document.querySelectorAll('.asset-panel .tree-row').length")
            if rows:
                break
            await asyncio.sleep(0.2)

        opened = await cdp.evaluate(
            """(() => {
              const name = %s;
              const el = [...document.querySelectorAll('.asset-panel .tree-row')]
                .find(e => (e.innerText || '').includes(name));
              if (!el) return 'not-found';
              const r = el.getBoundingClientRect();
              el.dispatchEvent(new MouseEvent('contextmenu', {
                bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 8
              }));
              return 'ok';
            })()"""
            % json.dumps(asset_name)
        )
        check("UI: 打开资产右键菜单", opened == "ok", str(opened))
        for _ in range(30):
            if await cdp.evaluate("!!document.querySelector('.ctx-menu')"):
                break
            await asyncio.sleep(0.1)
        edit_clicked = await cdp.evaluate(
            """(() => {
              const el = [...document.querySelectorAll('.ctx-menu .ctx-item')]
                .find(e => (e.innerText || '').trim() === '编辑');
              if (!el) return 'not-found';
              el.click();
              return 'ok';
            })()"""
        )
        check("UI: 点击编辑打开资产对话框", edit_clicked == "ok", str(edit_clicked))
        for _ in range(40):
            if await cdp.evaluate("!!document.querySelector('.modal select')"):
                break
            await asyncio.sleep(0.1)

        info = await cdp.evaluate(
            """(() => {
              const sel = [...document.querySelectorAll('.modal select')]
                .find(s => [...s.options].some(o => (o.textContent || '').includes('不使用跳板机')));
              if (!sel) return null;
              return {
                value: sel.value,
                options: [...sel.options].map(o => ({ value: o.value, text: (o.textContent || '').trim(), disabled: o.disabled })),
                text: document.querySelector('.modal')?.innerText || '',
              };
            })()"""
        )
        check("UI: 跳板机下拉框存在", bool(info), str(info)[:300])
        if info:
            options = info.get("options") or []
            check(
                "UI: 禁止自引用（当前资产不在可选列表）",
                all(option.get("value") != asset_id for option in options),
                str(options)[:400],
            )
            cycle_options = [o for o in options if cycle_name in (o.get("text") or "")]
            check(
                "UI: 循环跳板选项被禁用",
                bool(cycle_options) and all(o.get("disabled") for o in cycle_options),
                str(cycle_options)[:400],
            )
            check("UI: 显示循环提示", "循环" in (info.get("text") or ""), (info.get("text") or "")[:300])
            changed = await cdp.evaluate(
                """(() => {
                  const id = %s;
                  const sel = [...document.querySelectorAll('.modal select')]
                    .find(s => [...s.options].some(o => (o.textContent || '').includes('不使用跳板机')));
                  if (!sel) return 'no-select';
                  if (![...sel.options].some(o => o.value === id)) return 'no-option';
                  sel.value = id;
                  sel.dispatchEvent(new Event('change', { bubbles: true }));
                  return 'ok';
                })()"""
                % json.dumps(hop1_id)
            )
            check("UI: 选择安全跳板", changed == "ok", str(changed))
            text = ""
            for _ in range(25):
                text = await cdp.evaluate("document.querySelector('.modal')?.innerText || ''")
                if "跳板链（由外到内）" in text:
                    break
                await asyncio.sleep(0.15)
            preview_line = next(
                (line for line in text.splitlines() if "跳板链（由外到内）" in line), ""
            )
            check(
                "UI: 显示由外到内的跳板链",
                "zz-jump-hop-1" in preview_line
                and "zz-jump-hop-3" in preview_line
                and preview_line.index("zz-jump-hop-3") < preview_line.index("zz-jump-hop-1"),
                preview_line or text[-500:],
            )
            await cdp.evaluate(
                "document.querySelector('.modal-head .icon-btn')?.click()"
            )
        errors = await cdp.evaluate(
            """(() => {
              return (window.__h1khubUiErrors || []).slice(0, 5);
            })()"""
        )
    finally:
        for ev in cdp.events:
            if ev.get("method") == "Runtime.exceptionThrown":
                errors.append(ev["params"]["exceptionDetails"].get("text") or "exception")
            elif ev.get("method") == "Runtime.consoleAPICalled" and ev["params"]["type"] == "error":
                errors.append("console: " + " ".join(str(a.get("value") or a.get("description")) for a in ev["params"]["args"]))
            elif ev.get("method") == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
                errors.append("log: " + ev["params"]["entry"]["text"])
        try:
            await cdp.close()
        except Exception:
            pass
        proc.kill()
    check("UI: 资产对话框无 console error", not errors, str(errors)[:600])


# ------------------------------------------------------------------- cleanup
def cleanup(initial_assets: int, initial_sessions: set[str], initial_tunnels: set[str]) -> None:
    for session_id in SESSIONS:
        try:
            req(f"/api/sessions/{session_id}/close")
        except Exception:
            pass
    for tunnel_id in TUNNELS:
        try:
            req(f"/api/tunnels/{tunnel_id}/stop")
        except Exception:
            pass
    for asset_id in reversed(CREATED_ASSETS):
        delete_asset(asset_id)
    for proc in EXTRA_PROCS:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    for proc in SSHD_PROCS:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    clean_known_hosts(PORTS)
    shutil.rmtree(LAB, ignore_errors=True)
    # Best-effort assertion helper output for the final report.
    leftovers = [name for name in find_assets() if name.startswith("zz-")]
    no_leftovers = not leftovers
    check("清理: 无 zz- 测试资产残留", no_leftovers, str(leftovers)[:300])
    final_count = asset_count()
    check("清理: 资产数恢复初始值", final_count == initial_assets, f"{initial_assets} -> {final_count}")
    tunnels_now = {t["id"] for t in (data_of(req("/api/tunnels", method="GET")) or {}).get("tunnels", [])}
    sessions_now = {s["id"] for s in (data_of(req("/api/sessions", method="GET")) or {}).get("sessions", [])}
    check("清理: 无新增隧道", tunnels_now == initial_tunnels, f"{sorted(tunnels_now)} vs {sorted(initial_tunnels)}")
    check("清理: 无新增会话", sessions_now == initial_sessions, f"{sorted(sessions_now)} vs {sorted(initial_sessions)}")
    check("清理: 临时 known_hosts 条目已删除", all(not known_hosts_has(port) for port in PORTS))


# ------------------------------------------------------------------- scenario
def run_suite(initial_assets: int) -> None:
    global KEY_ASSET, TARGET, HOPS
    print("\n[lab]")
    try:
        setup_lab()
    except Exception as exc:
        check("lab: 启动临时 sshd", False, str(exc))
        return
    check("lab: 5 个临时 sshd 已监听", all(port_open(port) for port in PORTS), str(PORTS))

    print("\n[assets]")
    client_key = open(f"{LAB}/client_key", encoding="utf-8").read()
    KEY_ASSET = create_asset(
        f"zz-jump-key-{SUFFIX}",
        "SSHPrivateKey",
        {"privateKey": client_key, "passphrase": ""},
    )
    names = ["zz-jump-hop-1", "zz-jump-hop-2", "zz-jump-hop-3", "zz-jump-hop-4", "zz-jump-target"]
    for index, name in enumerate(names):
        asset_id = create_asset(
            f"{name}-{SUFFIX}",
            "SSH",
            {
                "host": "127.0.0.1",
                "port": PORTS[index],
                "username": getpass.getuser(),
                "authType": "key",
                "privateKeyAssetId": KEY_ASSET,
                "connectTimeout": 8,
            },
        )
        if index < 4:
            HOPS.append(asset_id)
        else:
            TARGET = asset_id
    check("assets: zz- 测试资产创建完成", len(HOPS) == 4 and bool(TARGET), str(HOPS))

    # ---------------------------------------------------------------- layers
    print("\n[layers]")
    set_chain(0)
    resp = test_conn(TARGET)
    check("无跳板: 连接成功", bool(data_of(resp)), str(resp)[:300])
    check("无跳板: 目标 banner", banner_chain(resp) == ["TARGET-5"], str(banner_chain(resp)))
    check("无跳板: 首次 hostKey=new", (data_of(resp) or {}).get("hostKey") == "new", str(data_of(resp))[:200])
    check("无跳板: known_hosts 已写入", known_hosts_has(PORTS[4]), host_key_entry(PORTS[4]))
    resp = test_conn(TARGET)
    check("无跳板: 第二次 hostKey=known", (data_of(resp) or {}).get("hostKey") == "known", str(data_of(resp))[:200])

    for layers in (1, 2, 3, 4):
        set_chain(layers)
        layer_resp = expect_chain(f"{layers} 层跳板", layers)
        states = hop_states(layer_resp)
        expected_new = [BANNERS[index] for index in range(layers - 1, -1, -1)]
        # Each newly reached outermost hop must be "new", inner hops already known.
        check(
            f"{layers} 层跳板: 最外层首次 hostKey=new",
            states and states[0] == "new",
            f"states={states} expected_outer={expected_new}",
        )
        check(
            f"{layers} 层跳板: 内层 hostKey 已缓存",
            len(states) >= 2 and all(state == "known" for state in states[1:]),
            str(states),
        )

    print("\n[known_hosts]")
    missing = [port for port in PORTS if not known_hosts_has(port)]
    check("known_hosts: 4 层链每一跳均已记录 [host]:port", not missing, str(missing))

    # ------------------------------------------------------- DB/Redis/Docker
    print("\n[db/redis/docker over 3-layer chain]")
    set_chain(3)
    db_id = create_asset(
        f"zz-jump-db-{SUFFIX}",
        "DB",
        {
            "dbType": "mysql",
            "host": "127.0.0.1",
            "port": 3307,
            "username": "h1khub",
            "password": "hexhub123",
            "database": "demo",
            "sshAssetId": TARGET,
        },
    )
    db_resp = req("/api/db/query", {"assetId": db_id, "sql": "SELECT 1 AS ok", "database": "demo"})
    db_data = data_of(db_resp) or {}
    rows = ((db_data.get("results") or [{}])[0]).get("rows")
    check("DB: sshAssetId 走 3 层链查询成功", rows == [[1]], str(db_resp)[:500])

    redis_id = create_asset(
        f"zz-jump-redis-{SUFFIX}",
        "Redis",
        {"host": "127.0.0.1", "port": 6380, "db": 0, "sshAssetId": TARGET},
    )
    redis_resp = req("/api/redis/test", {"assetId": redis_id})
    redis_data = data_of(redis_resp) or {}
    check("Redis: sshAssetId 走 3 层链连接成功", bool(redis_data.get("version")), str(redis_resp)[:400])

    docker_id = create_asset(
        f"zz-jump-docker-{SUFFIX}",
        "Docker",
        {"mode": "ssh", "sshAssetId": TARGET, "socketPath": "/var/run/docker.sock"},
    )
    docker_resp = req("/api/docker/test", {"assetId": docker_id})
    docker_data = data_of(docker_resp) or {}
    check("Docker: SSH 模式走 3 层链连接成功", bool(docker_data.get("version")), str(docker_resp)[:500])

    # ---------------------------------------------------- session/SFTP/SCP
    print("\n[session/sftp/scp over 3-layer chain]")
    started = req("/api/sessions/start", {"assetId": TARGET, "cols": 100, "rows": 30})
    session = (data_of(started) or {}).get("session")
    check("会话: 3 层链终端启动成功", bool(session), str(started)[:500])
    if session:
        SESSIONS.append(session["id"])
        meta = session.get("meta") or {}
        check(
            "会话: meta 记录 3 层跳板链",
            len(meta.get("jumpChain") or []) == 3,
            str(meta)[:400],
        )
        output = run_async(ws_roundtrip(session["id"], [("echo zz-jump-shell-ok", "zz-jump-shell-ok")]))
        check("会话: 多层链内命令执行成功", b"zz-jump-shell-ok" in output, output.decode(errors="replace")[-400:])

        sftp_resp = req("/api/sftp/list", {"sessionId": session["id"], "path": "/tmp"})
        sftp_data = data_of(sftp_resp) or {}
        check("SFTP: 3 层链目录列表成功", isinstance(sftp_data.get("entries"), list), str(sftp_resp)[:400])
        req(f"/api/sessions/{session['id']}/close")
        SESSIONS.remove(session["id"])

    remote_path = f"/tmp/zz-jump-scp-{SUFFIX}.txt"
    payload = b"zz-jump-scp-ok\n"
    scp_url = "/api/scp/upload?" + urllib.parse.urlencode({"assetId": TARGET, "path": remote_path})
    scp_resp = req(scp_url, raw_body=payload, method="POST")
    check("SCP: 3 层链上传成功", bool(data_of(scp_resp)), str(scp_resp)[:400])
    try:
        with open(remote_path, "rb") as fh:
            uploaded = fh.read()
        check("SCP: 上传内容一致", uploaded == payload, uploaded[:100])
    except OSError as exc:
        check("SCP: 上传内容一致", False, str(exc))
    try:
        os.unlink(remote_path)
    except OSError:
        pass

    src_path = f"/tmp/zz-jump-transfer-src-{SUFFIX}.txt"
    dst_path = f"/tmp/zz-jump-transfer-dst-{SUFFIX}.txt"
    with open(src_path, "wb") as fh:
        fh.write(b"zz-jump-transfer-ok\n")
    try:
        transfer_resp = req(
            "/api/transfer",
            {
                "fromAssetId": TARGET,
                "toAssetId": TARGET,
                "fromPath": src_path,
                "toPath": dst_path,
                "overwrite": True,
            },
        )
        stats = data_of(transfer_resp) or {}
        check("SFTP: hub.sftp 跨服务器传输走 3 层链", stats.get("files") == 1, str(transfer_resp)[:400])
        try:
            with open(dst_path, "rb") as fh:
                transferred = fh.read()
            check("SFTP: 传输内容一致", transferred == b"zz-jump-transfer-ok\n", transferred[:100])
        except OSError as exc:
            check("SFTP: 传输内容一致", False, str(exc))
    finally:
        for path in (src_path, dst_path):
            try:
                os.unlink(path)
            except OSError:
                pass

    # ------------------------------------------------ managed SSHTunnel
    print("\n[managed tunnel over 3-layer chain]")
    tunnel_asset = create_asset(
        f"zz-jump-tunnel-{SUFFIX}",
        "SSHTunnel",
        {"type": "dynamic", "sshAssetId": TARGET, "bindHost": "127.0.0.1", "bindPort": 0},
    )
    tunnel_resp = req("/api/tunnels/start", {"assetId": tunnel_asset})
    tunnel_status = data_of(tunnel_resp) or {}
    check("隧道: 3 层链 dynamic 隧道启动", bool(tunnel_status.get("bindPort")), str(tunnel_resp)[:400])
    if tunnel_status.get("bindPort"):
        TUNNELS.append(tunnel_status["id"])
        try:
            with socket.create_connection(("127.0.0.1", int(tunnel_status["bindPort"])), timeout=8) as sock:
                sock.settimeout(8)
                sock.sendall(b"\x05\x01\x00")
                greeting = sock.recv(2)
                sock.sendall(
                    b"\x05\x01\x00\x01"
                    + socket.inet_aton("127.0.0.1")
                    + int(PORTS[4]).to_bytes(2, "big")
                )
                reply = sock.recv(10)
                banner = sock.recv(200)
            check("隧道: SOCKS5 握手", greeting == b"\x05\x00", str(greeting))
            check("隧道: SOCKS5 CONNECT 成功", len(reply) >= 2 and reply[1] == 0, str(reply))
            check("隧道: 经 3 层链取到目标 banner", banner.startswith(b"SSH-2.0"), str(banner[:80]))
        except OSError as exc:
            check("隧道: SOCKS5 探测", False, str(exc))
        stopped = req(f"/api/tunnels/{tunnel_status['id']}/stop")
        check("隧道: 停止成功", (data_of(stopped) or {}).get("stopped") is True, str(stopped)[:200])
        TUNNELS.remove(tunnel_status["id"])
    tunnels_now = (data_of(req("/api/tunnels", method="GET")) or {}).get("tunnels") or []
    check(
        "隧道: 停止后列表不含该隧道",
        all(t.get("id") != tunnel_status.get("id") for t in tunnels_now),
        str(tunnels_now)[:300],
    )

    # ------------------------------------------------------------- proxy
    print("\n[outermost proxy]")
    proxy_port = free_port()
    proxy = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "test_servers.py"), "socks5-proxy", "--port", str(proxy_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    EXTRA_PROCS.append(proxy)
    if not wait_port(proxy_port):
        check("代理: SOCKS5 fixture 启动", False, str(proxy_port))
    else:
        update_asset(HOPS[2], {"proxy": {"enabled": True, "type": "socks5", "host": "127.0.0.1", "port": proxy_port}})
        proxy_resp = test_conn(TARGET)
        check("代理: 最外层跳板经 SOCKS5 连接成功", bool(data_of(proxy_resp)), str(proxy_resp)[:400])
        check(
            "代理: 链路 banner 仍逐跳正确",
            banner_chain(proxy_resp) == ["HOP-3", "HOP-2", "HOP-1", "TARGET-5"],
            str(banner_chain(proxy_resp)),
        )
        proxy.terminate()
        try:
            proxy.wait(timeout=3)
        except Exception:
            proxy.kill()
        wait_port(proxy_port, opened=False, timeout=3)
        down_resp = test_conn(TARGET)
        check(
            "代理: 代理停止后连接失败且指出第 1 跳",
            down_resp.get("__http_error") == 400 and "第 1 跳" in error_of(down_resp),
            str(down_resp)[:400],
        )
        update_asset(HOPS[2], {"proxy": {"enabled": False}})

    # -------------------------------------------------------------- cycle
    print("\n[cycle]")
    suffix = SUFFIX
    cycle_a = create_asset(
        f"zz-jump-cycle-a-{suffix}",
        "SSH",
        {"host": "127.0.0.1", "port": PORTS[0], "username": getpass.getuser(), "authType": "key", "privateKeyAssetId": KEY_ASSET},
    )
    cycle_b = create_asset(
        f"zz-jump-cycle-b-{suffix}",
        "SSH",
        {
            "host": "127.0.0.1",
            "port": PORTS[0],
            "username": getpass.getuser(),
            "authType": "key",
            "privateKeyAssetId": KEY_ASSET,
            "jumpAssetId": cycle_a,
        },
    )
    update_asset(cycle_a, {"jumpAssetId": cycle_b})
    started_at = time.perf_counter()
    cycle_resp = test_conn(cycle_a)
    elapsed = time.perf_counter() - started_at
    cycle_error = error_of(cycle_resp)
    check("循环: 返回 400", cycle_resp.get("__http_error") == 400, str(cycle_resp)[:400])
    check(
        "循环: 错误包含完整链路",
        "循环" in cycle_error and f"zz-jump-cycle-a-{suffix}" in cycle_error and f"zz-jump-cycle-b-{suffix}" in cycle_error,
        cycle_error[:400],
    )
    check("循环: 快速失败（<2s）", elapsed < 2.0, f"{elapsed:.2f}s")
    update_asset(cycle_a, {"jumpAssetId": ""})

    # -------------------------------------------------------------- depth
    print("\n[depth limit]")
    depth_assets = []
    for index in range(1, 11):
        depth_assets.append(
            create_asset(
                f"zz-jump-depth-{index:02d}-{suffix}",
                "SSH",
                {
                    "host": "127.0.0.1",
                    "port": PORTS[0],
                    "username": getpass.getuser(),
                    "authType": "key",
                    "privateKeyAssetId": KEY_ASSET,
                },
            )
        )
    for index, asset_id in enumerate(depth_assets):
        update_asset(asset_id, {"jumpAssetId": depth_assets[index + 1] if index + 1 < len(depth_assets) else ""})
    update_asset(TARGET, {"jumpAssetId": depth_assets[0]})
    depth_resp = test_conn(TARGET)
    check("深度: 10 层链返回 400", depth_resp.get("__http_error") == 400, str(depth_resp)[:300])
    check("深度: 错误包含层数上限", "跳板层数超过上限（8）" in error_of(depth_resp), error_of(depth_resp)[:300])
    set_chain(3)

    # ------------------------------------------------- failure / no leak
    print("\n[failure cleanup]")
    dead = create_asset(
        f"zz-jump-dead-{suffix}",
        "SSH",
        {"host": "127.0.0.1", "port": DEAD_PORT, "username": getpass.getuser(), "authType": "key", "privateKeyAssetId": KEY_ASSET, "connectTimeout": 3},
    )
    update_asset(dead, {"jumpAssetId": HOPS[2]})
    update_asset(TARGET, {"jumpAssetId": dead})
    sessions_before = len((data_of(req("/api/sessions", method="GET")) or {}).get("sessions") or [])
    fail_resp = test_conn(TARGET)
    check("失败: 中间跳端口关闭返回 400", fail_resp.get("__http_error") == 400, str(fail_resp)[:300])
    check("失败: 错误指出第 2 跳", "第 2 跳" in error_of(fail_resp), error_of(fail_resp)[:300])

    failed_session = req("/api/sessions/start", {"assetId": TARGET, "cols": 90, "rows": 24})
    check("失败: 终端会话启动被拒绝", failed_session.get("__http_error") == 400, str(failed_session)[:300])
    time.sleep(1.2)
    established = subprocess.run(["ss", "-tn", "state", "established"], capture_output=True, text=True, timeout=10).stdout
    leaked = [line for line in established.splitlines() if any(f":{port}" in line for port in PORTS + [DEAD_PORT])]
    check("失败: 无残留 TCP 连接", not leaked, "\n".join(leaked[:5]))
    sessions_after = (data_of(req("/api/sessions", method="GET")) or {}).get("sessions") or []
    check("失败: 无新增会话", len(sessions_after) == sessions_before, f"{sessions_before} -> {len(sessions_after)}")
    check("失败: 无残留隧道", (data_of(req("/api/tunnels", method="GET")) or {}).get("tunnels") == [])
    update_asset(dead, {"jumpAssetId": ""})
    set_chain(3)

    # ----------------------------------------------------------- host key tamper
    print("\n[host key change]")
    set_chain(4)
    good = test_conn(TARGET)
    check("主机密钥: 变更前连接成功", bool(data_of(good)), str(good)[:300])
    original_key = restart_sshd_with_new_key(3)  # HOP-4 is outermost in a 4-layer chain
    try:
        tampered = test_conn(TARGET)
        tampered_error = error_of(tampered)
        check("主机密钥: 变更后返回 400", tampered.get("__http_error") == 400, str(tampered)[:300])
        check(
            "主机密钥: 错误指出第 1 跳与密钥不匹配",
            "第 1 跳" in tampered_error and "主机密钥" in tampered_error,
            tampered_error[:400],
        )
    finally:
        restore_sshd_key(3, original_key)
    restored = test_conn(TARGET)
    check("主机密钥: 恢复后连接成功", bool(data_of(restored)), str(restored)[:300])

    # ----------------------------------------------------------------- UI
    print("\n[ui]")
    set_chain(3)
    try:
        run_async(ui_jump_dialog(cycle_a, f"zz-jump-cycle-a-{suffix}", f"zz-jump-cycle-b-{suffix}", HOPS[0]))
    except Exception as exc:
        check("UI: 资产对话框跳板链验证", False, f"{type(exc).__name__}: {exc}")

    # sanity for the final report
    check("资产: 仍为 32 个基础资产（临时资产尚未清理）", initial_assets == 32, str(initial_assets))


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    initial_assets = asset_count()
    initial_sessions = {s["id"] for s in (data_of(req("/api/sessions", method="GET")) or {}).get("sessions", [])}
    initial_tunnels = {t["id"] for t in (data_of(req("/api/tunnels", method="GET")) or {}).get("tunnels", [])}
    print(f"base={BASE} initial_assets={initial_assets} sessions={len(initial_sessions)} tunnels={len(initial_tunnels)}")
    try:
        run_suite(initial_assets)
    except Exception as exc:
        check("suite: 未捕获异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        cleanup(initial_assets, initial_sessions, initial_tunnels)
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
