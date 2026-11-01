#!/usr/bin/env python3
"""End-to-end test for "通过服务器代理 Chrome" (Chrome via an SSH SOCKS5 tunnel).

The suite is self-contained: it starts a throwaway replica instance with a fake
Chrome executable (so no GUI window is opened), a throwaway OpenSSH daemon on a
random loopback port, and a local HTTP fixture.  It then verifies:

  * Chrome discovery/status API and the exact command line derived from the
    original product (proxy, throwaway profile, no-first-run, bypass list, URL);
  * the SOCKS5 listener really tunnels traffic through the SSH asset;
  * the asset right-click menu appears only for SSH assets and launches Chrome;
  * the management dialog lists sessions and stops one;
  * a missing Chrome and invalid inputs fail with clear 400s;
  * closing Chrome by itself stops the tunnel and removes the profile;
  * stopping a session tears down the process, SOCKS listener and profile.

Usage: python3 scripts/test_chromeproxy.py
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp_check  # noqa: E402
from cdp_check import CDP, launch  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUFFIX = "%04x" % int.from_bytes(os.urandom(2), "big")
PASS = 0
FAIL = 0
FAILURES: list[str] = []

HOME = ""
HOME2 = ""
HOME3 = ""
BASE = ""
BASE2 = ""
BASE3 = ""
TOKEN = "chromeproxy-test-token"
PORT = 0
PORT2 = 0
PORT3 = 0
LAB = ""
SSHD_PORT = 0
SSHD_PROC: subprocess.Popen | None = None
HTTP_PORT = 0
HTTP_SERVER = None
SERVER_PROC: subprocess.Popen | None = None
SERVER2_PROC: subprocess.Popen | None = None
SERVER3_PROC: subprocess.Popen | None = None
MOCK = ""
MOCK_LOG = ""
MOCK_PIDS: set[int] = set()
PROFILE_DIRS: set[str] = set()
CREATED_ASSETS: list[str] = []


# ------------------------------------------------------------------ helpers
def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail[:600]}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, opened: bool = True, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port) == opened:
            return True
        time.sleep(0.1)
    return False


def wait_until(fn, timeout: float = 10.0, interval: float = 0.15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # page may still be settling
            last = exc
        time.sleep(interval)
    return last


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr or proc.stdout}")


def req(path: str, body=None, method: str = "POST", base: str | None = None):
    base = base or BASE
    data = None
    headers = {"X-H1KHub-Token": TOKEN}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            return json.loads(resp.read())
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


def error_text(resp) -> str:
    detail = resp.get("detail") if isinstance(resp, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("error") or detail)
    return str(detail or resp)


def create_asset(name: str, type_name: str, config: dict, base: str | None = None) -> str:
    resp = req("/api/assets", {"typeName": type_name, "name": name, "config": config}, base=base)
    data = data_of(resp)
    if not data:
        raise RuntimeError(f"create asset failed: {resp}")
    asset_id = data["asset"]["id"]
    CREATED_ASSETS.append(asset_id)
    return asset_id


def delete_assets(base: str | None = None) -> None:
    for asset_id in list(CREATED_ASSETS):
        try:
            req(f"/api/assets/{asset_id}", method="DELETE", base=base)
        except Exception:
            pass
    CREATED_ASSETS.clear()


def sessions(base: str | None = None) -> list[dict]:
    data = data_of(req("/api/chrome-proxy", method="GET", base=base)) or {}
    return data.get("sessions") or []


def stop_session(session_id: str, base: str | None = None):
    return req(f"/api/chrome-proxy/{session_id}/stop", {}, base=base)


def mock_args() -> list[str]:
    try:
        with open(MOCK_LOG, encoding="utf-8") as fh:
            return [line.rstrip("\n") for line in fh if line.strip()]
    except FileNotFoundError:
        return []


# ------------------------------------------------------------------- lab
def setup_lab() -> None:
    global LAB, SSHD_PORT, SSHD_PROC, HTTP_PORT, HTTP_SERVER, MOCK, MOCK_LOG
    LAB = tempfile.mkdtemp(prefix="h1khub-chrome-lab-")
    os.chmod(LAB, 0o700)
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", os.path.join(LAB, "client_key")])
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", os.path.join(LAB, "host_key")])

    SSHD_PORT = free_port()
    cfg = os.path.join(LAB, "sshd.conf")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(
            f"""Port {SSHD_PORT}
ListenAddress 127.0.0.1
HostKey {LAB}/host_key
PidFile {LAB}/sshd.pid
AuthorizedKeysFile {LAB}/client_key.pub
AllowUsers {getpass.getuser()}
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
StrictModes no
UseDNS no
LogLevel ERROR
"""
        )
    run(["/usr/sbin/sshd", "-t", "-f", cfg])
    SSHD_PROC = subprocess.Popen(
        ["/usr/sbin/sshd", "-D", "-f", cfg, "-E", os.path.join(LAB, "sshd.log")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    if not wait_port(SSHD_PORT):
        raise RuntimeError(f"sshd did not listen on {SSHD_PORT}")

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                self.request.recv(4096)
                body = b"CHROME-PROXY-SOCKS-OK\n"
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
                )
            except Exception:
                pass

    HTTP_SERVER = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    HTTP_SERVER.daemon_threads = True
    HTTP_PORT = HTTP_SERVER.server_address[1]
    threading.Thread(target=HTTP_SERVER.serve_forever, daemon=True).start()

    MOCK = os.path.join(HOME, "mock-chrome.sh")
    MOCK_LOG = os.path.join(HOME, "mock-chrome-args.log")
    with open(MOCK, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/bin/sh\n"
            'printf "%s\\n" "$@" > "$MOCK_LOG"\n'
            'echo "$$" >> "$MOCK_LOG.pid"\n'
            "exec sleep 300\n"
        )
    os.chmod(MOCK, 0o755)


def teardown_lab() -> None:
    global SSHD_PROC, HTTP_SERVER
    if HTTP_SERVER is not None:
        try:
            HTTP_SERVER.shutdown()
            HTTP_SERVER.server_close()
        except Exception:
            pass
        HTTP_SERVER = None
    if SSHD_PROC is not None:
        try:
            SSHD_PROC.terminate()
            SSHD_PROC.wait(timeout=4)
        except Exception:
            try:
                SSHD_PROC.kill()
            except Exception:
                pass
        SSHD_PROC = None
    for pid in list(MOCK_PIDS):
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    MOCK_PIDS.clear()
    if MOCK:
        # The mock path is unique to this run; make sure no orphan survives a
        # failed teardown of the replica instance.
        try:
            subprocess.run(["pkill", "-f", MOCK], capture_output=True, timeout=10)
        except Exception:
            pass
    for path in list(PROFILE_DIRS):
        shutil.rmtree(path, ignore_errors=True)
    PROFILE_DIRS.clear()


def start_server(home: str, port: int, chrome_path: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["H1KHUB_HOME"] = home
    env["H1KHUB_CHROME_PATH"] = chrome_path
    env["MOCK_LOG"] = MOCK_LOG
    log = open(os.path.join(home, "server-stdout.log"), "wb")
    proc = subprocess.Popen(
        [
            sys.executable,
            os.path.join(ROOT, "backend", "app.py"),
            "--port",
            str(port),
            "--home",
            home,
            "--no-open",
            "--token",
            TOKEN,
            "--log-level",
            "WARNING",
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    proc._test_log = log  # type: ignore[attr-defined]
    if not wait_port(port, timeout=20):
        raise RuntimeError(f"replica did not listen on {port}")
    return proc


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    log = getattr(proc, "_test_log", None)
    if log is not None:
        try:
            log.close()
        except Exception:
            pass


# ------------------------------------------------------------------ SOCKS
def recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def socks_http_get(proxy_port: int, host: str, port: int, path: str = "/") -> bytes:
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=8) as sock:
        sock.settimeout(8)
        sock.sendall(b"\x05\x01\x00")
        greeting = recv_exact(sock, 2)
        if greeting != b"\x05\x00":
            raise RuntimeError(f"bad SOCKS greeting: {greeting!r}")
        sock.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(host) + struct.pack(">H", port))
        reply = recv_exact(sock, 10)
        if reply[:2] != b"\x05\x00":
            raise RuntimeError(f"SOCKS connect failed: {reply!r}")
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode())
        chunks = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)


# -------------------------------------------------------------------- UI
async def js(cdp, expr):
    return await cdp.evaluate(expr)


async def wait_for_js(cdp, expr, timeout=20.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = await js(cdp, expr)
            if last:
                return last
        except Exception as exc:
            last = exc
        await asyncio.sleep(0.2)
    raise RuntimeError(f"timeout waiting for {expr} (last={last})")


async def context_menu_row(cdp, text):
    return await js(
        cdp,
        f"""(() => {{
          const el = [...document.querySelectorAll('.asset-panel .tree-row')]
            .find(e => (e.innerText || '').includes({json.dumps(text)}));
          if (!el) return 'not-found';
          const r = el.getBoundingClientRect();
          el.dispatchEvent(new MouseEvent('contextmenu', {{
            bubbles: true, cancelable: true,
            clientX: r.left + 20, clientY: r.top + 6,
          }}));
          return 'ok';
        }})()""",
    )


async def click_menu(cdp, text):
    return await js(
        cdp,
        f"""(() => {{
          const item = [...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(e => (e.innerText || '').includes({json.dumps(text)}));
          if (!item) return 'not-found';
          if (item.classList.contains('disabled')) return 'disabled';
          item.click();
          return 'ok';
        }})()""",
    )


def menu_texts(cdp):
    return js(cdp, "[...document.querySelectorAll('.ctx-menu .ctx-item')].map(e => (e.innerText || '').trim())")


async def ui_checks(ssh_id: str, dir_id: str) -> None:
    print("[9] UI：右键 SSH 资产启动 Chrome")
    proc, ws_url = await launch(BASE + "/")
    cdp = CDP(ws_url)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Log.enable")
    try:
        await wait_for_js(
            cdp,
            "!!document.querySelector('.asset-panel') && document.querySelectorAll('.asset-panel .tree-row').length >= 2",
            timeout=30,
        )
        # Make sure the fresh instance is on the asset list, not history.
        title = await js(cdp, "document.querySelector('.panel-title')?.innerText || ''")
        if "最近历史" in title:
            await js(
                cdp,
                "[...document.querySelectorAll('.panel-foot .btn')].find(e => e.innerText.includes('返回资产'))?.click()",
            )
            await asyncio.sleep(0.3)
        ssh_name = f"zz-chrome-ssh-{SUFFIX}"
        dir_name = f"zz-chrome-dir-{SUFFIX}"

        r = await context_menu_row(cdp, ssh_name)
        check("SSH 资产右键菜单打开", r == "ok", str(r))
        await wait_for_js(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        items = await menu_texts(cdp)
        check("菜单含「通过服务器代理 Chrome」", any("通过服务器代理 Chrome" in (x or "") for x in items), json.dumps(items, ensure_ascii=False)[:200])

        r = await click_menu(cdp, "通过服务器代理 Chrome")
        check("点击代理项", r == "ok", str(r))
        toast = await wait_for_js(
            cdp,
            "[...document.querySelectorAll('.toast .t-msg')].map(e => e.innerText).find(x => x.includes('SOCKS5')) || ''",
            timeout=30,
        )
        check("启动成功 toast 含 SOCKS5", "SOCKS5" in (toast or ""), str(toast)[:200])
        running = []
        deadline = time.time() + 10
        while time.time() < deadline:
            running = sessions()
            if len(running) >= 1:
                break
            await asyncio.sleep(0.2)
        check("UI 启动后出现运行会话", len(running) >= 1, f"{running}")
        check("UI 会话属于当前 SSH 资产", running and running[0].get("assetName") == ssh_name, json.dumps(running, ensure_ascii=False)[:300])

        print("[9b] UI：再次右键 SSH 资产，直接停止代理")
        await js(cdp, "document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))")
        await asyncio.sleep(0.2)
        r = await context_menu_row(cdp, ssh_name)
        check("运行中时再次打开右键菜单", r == "ok", str(r))
        await wait_for_js(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        items = await menu_texts(cdp)
        check("菜单出现「停止通过服务器代理的 Chrome」", any("停止通过服务器代理的 Chrome" in (x or "") for x in items), json.dumps(items, ensure_ascii=False)[:200])
        r = await click_menu(cdp, "停止通过服务器代理的 Chrome")
        check("点击停止代理项", r == "ok", str(r))
        toast = await wait_for_js(
            cdp,
            "[...document.querySelectorAll('.toast .t-msg')].map(e => e.innerText).find(x => x.includes('已停止')) || ''",
            timeout=15,
        )
        check("直接停止成功 toast", "已停止" in (toast or ""), str(toast)[:200])
        deadline = time.time() + 10
        left = 1
        while time.time() < deadline:
            left = len(sessions())
            if left == 0:
                break
            await asyncio.sleep(0.2)
        check("直接停止后会话为空", left == 0, f"left={left}")

        r = await context_menu_row(cdp, ssh_name)
        check("无会话时右键菜单仍可打开", r == "ok", str(r))
        await wait_for_js(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        items = await menu_texts(cdp)
        check("无会话时不再显示停止项", not any("停止通过服务器代理的 Chrome" in (x or "") for x in items), json.dumps(items, ensure_ascii=False)[:200])
        await js(cdp, "document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))")
        await asyncio.sleep(0.2)

        # Start one session through the API so the dialog test has a target.
        resp = req("/api/chrome-proxy/start", {"assetId": ssh_id})
        dialog_session = (data_of(resp) or {}).get("session") or {}
        check("对话框测试会话已启动", bool(dialog_session.get("id")), error_text(resp))

        # A directory must not offer the Chrome action.
        r = await context_menu_row(cdp, dir_name)
        check("目录行右键菜单打开", r == "ok", str(r))
        await wait_for_js(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        items = await menu_texts(cdp)
        check("目录菜单不含代理项", not any("通过服务器代理 Chrome" in (x or "") for x in items), json.dumps(items, ensure_ascii=False)[:200])
        await js(cdp, "document.body.click()")

        print("[10] UI：Chrome 代理管理对话框")
        await js(cdp, "document.querySelector('.panel-actions button[title=\"更多\"]')?.click()")
        await wait_for_js(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        r = await click_menu(cdp, "Chrome 代理")
        check("更多菜单打开管理对话框", r == "ok", str(r))
        await wait_for_js(cdp, "!!document.querySelector('.modal-title')", timeout=8)
        dialog_title = await js(cdp, "document.querySelector('.modal-title')?.innerText || ''")
        check("对话框标题正确", "Chrome 服务器代理" in dialog_title, dialog_title)
        rows = await js(cdp, "document.querySelectorAll('.chrome-session').length")
        check("对话框列出运行中会话", rows >= 1, f"rows={rows}")
        browser = await js(cdp, "document.querySelector('.modal-body')?.innerText || ''")
        check("对话框显示浏览器路径", MOCK in browser, browser[:200])

        # Stop the newest session from the dialog.
        before = len(sessions())
        await js(cdp, "document.querySelector('.chrome-session .btn')?.click()")
        after = before
        deadline = time.time() + 12
        while time.time() < deadline:
            after = len(sessions())
            if after == before - 1:
                break
            await asyncio.sleep(0.2)
        check("对话框停止按钮减少会话", after == before - 1, f"{before} -> {after}")
        toast = await wait_for_js(
            cdp,
            "[...document.querySelectorAll('.toast .t-msg')].map(e => e.innerText).find(x => x.includes('已停止')) || ''",
            timeout=6,
        )
        check("停止后 toast 提示", "已停止" in (toast or ""), str(toast)[:200])
        await js(cdp, "document.querySelector('.modal-foot .btn')?.click()")
        await asyncio.sleep(0.3)
        check("对话框已关闭", not await js(cdp, "!!document.querySelector('.chrome-session')"), "")
    finally:
        await cdp.close()
        proc.kill()


# --------------------------------------------------------------- API checks
def test_api(ssh_id: str, key_id: str, dir_id: str) -> None:
    print("[1] 能力探测")
    info = data_of(req("/api/chrome-proxy", method="GET")) or {}
    check("Chrome 可用", info.get("available") is True, json.dumps(info, ensure_ascii=False)[:300])
    check("返回 Chrome 路径", info.get("path") == MOCK, str(info.get("path")))
    check("返回尝试路径列表", isinstance(info.get("tried"), list) and len(info["tried"]) >= 1, "")
    check("初始无运行会话", info.get("sessions") == [], str(info.get("sessions")))
    check("返回并发上限", int(info.get("maxSessions") or 0) >= 1, str(info.get("maxSessions")))

    print("[2] 启动：SOCKS5 隧道 + Chrome 参数")
    resp = req("/api/chrome-proxy/start", {"assetId": ssh_id})
    data = data_of(resp)
    check("启动返回 200", data is not None, error_text(resp))
    session = (data or {}).get("session") or {}
    if not session:
        return
    session_id = session["id"]
    port = int(session.get("port") or 0)
    pid = int(session.get("pid") or 0)
    profile = session.get("profileDir") or ""
    if profile:
        PROFILE_DIRS.add(profile)
    check("会话运行中", session.get("running") is True, str(session))
    check("资产信息正确", session.get("assetName") == f"zz-chrome-ssh-{SUFFIX}", str(session.get("assetName")))
    check("代理为 socks5://127.0.0.1:port", session.get("proxy") == f"socks5://127.0.0.1:{port}" and port > 0, str(session.get("proxy")))
    check("会话有 pid", pid > 0, str(pid))
    if pid:
        MOCK_PIDS.add(pid)
    check("SOCKS 监听端口已打开", wait_port(port, True, 5), str(port))

    args = wait_until(lambda: mock_args() or False, timeout=5) or []
    check("Chrome 收到 --proxy-server", f"--proxy-server=socks5://127.0.0.1:{port}" in args, json.dumps(args, ensure_ascii=False))
    check("Chrome 收到独立 --user-data-dir", f"--user-data-dir={profile}" in args, "")
    check("Chrome 收到 --no-first-run", "--no-first-run" in args, "")
    check("Chrome 收到 --no-default-browser-check", "--no-default-browser-check" in args, "")
    check("Chrome 收到 --proxy-bypass-list=<-loopback>", "--proxy-bypass-list=<-loopback>" in args, "")
    check("Chrome 未收到多余参数", len(args) == 5, json.dumps(args, ensure_ascii=False))
    check("临时配置目录存在", bool(profile) and os.path.isdir(profile), profile)
    if profile and os.path.isdir(profile):
        mode = os.stat(profile).st_mode & 0o777
        check("临时配置目录权限 0700", mode == 0o700, oct(mode))

    print("[3] SOCKS5 实际转发")
    try:
        raw = socks_http_get(port, "127.0.0.1", HTTP_PORT)
        text = raw.decode("utf-8", "replace")
        check("经 SOCKS5 访问本地 HTTP", "CHROME-PROXY-SOCKS-OK" in text, text[:200])
        check("HTTP 200 经隧道返回", "200 OK" in text, text[:200])
    except Exception as exc:
        check("经 SOCKS5 访问本地 HTTP", False, f"{type(exc).__name__}: {exc}")

    print("[4] 会话列表与历史")
    running = sessions()
    check("列表中可见运行会话", any(s.get("id") == session_id and s.get("running") for s in running), json.dumps(running, ensure_ascii=False)[:300])
    live = next((s for s in running if s.get("id") == session_id), {})
    targets = live.get("recentConnections") or []
    check("隧道记录到 fixture 目标", any(str(HTTP_PORT) in str(t) for t in targets), json.dumps(targets, ensure_ascii=False)[:300])
    check("连接计数至少 1", int(live.get("connections") or 0) >= 1, f"connections={live.get('connections')}")
    hist = data_of(req("/api/history?limit=20", method="GET")) or {}
    entries = hist.get("history") or []
    check("历史记录 kind=chrome", any(x.get("kind") == "chrome" for x in entries), json.dumps(entries[:3], ensure_ascii=False)[:300])

    print("[5] URL 参数与校验")
    resp = req("/api/chrome-proxy/start", {"assetId": ssh_id, "url": "https://example.com/hello"})
    data = data_of(resp)
    session_url = ((data or {}).get("session") or {})
    check("带 URL 启动成功", data is not None, error_text(resp))
    if session_url:
        PROFILE_DIRS.add(session_url.get("profileDir") or "")
        args = wait_until(lambda: mock_args() or False, timeout=5) or []
        check("URL 作为最后一个参数", args and args[-1] == "https://example.com/hello", json.dumps(args, ensure_ascii=False))
    url_sid = session_url.get("id")
    if url_sid:
        stop_session(url_sid)

    count_before = len(sessions())
    resp = req("/api/chrome-proxy/start", {"assetId": ssh_id, "url": "file:///etc/passwd"})
    check("拒绝 file:// URL", resp.get("__http_error") == 400, json.dumps(resp, ensure_ascii=False)[:300])
    check("拒绝 URL 时没有新增会话", len(sessions()) == count_before, f"{count_before} -> {len(sessions())}")

    print("[6] 非 SSH 资产 / 未知会话")
    resp = req("/api/chrome-proxy/start", {"assetId": dir_id})
    check("目录资产拒绝启动", resp.get("__http_error") == 400, json.dumps(resp, ensure_ascii=False)[:300])
    check("错误信息说明仅 SSH", "SSH" in error_text(resp), error_text(resp))
    resp = req("/api/chrome-proxy/cp_missing/stop", {})
    check("停止未知会话返回 404", resp.get("__http_error") == 404, json.dumps(resp, ensure_ascii=False)[:200])

    print("[7] 关闭 Chrome 进程 → 自动清理")
    resp = req("/api/chrome-proxy/start", {"assetId": ssh_id})
    auto_session = (data_of(resp) or {}).get("session") or {}
    auto_sid = auto_session.get("id")
    auto_port = int(auto_session.get("port") or 0)
    auto_pid = int(auto_session.get("pid") or 0)
    auto_profile = auto_session.get("profileDir") or ""
    check("自动清理用会话已启动", bool(auto_sid), error_text(resp))
    if auto_sid:
        PROFILE_DIRS.add(auto_profile)
        MOCK_PIDS.add(auto_pid)
        try:
            os.kill(auto_pid, signal.SIGTERM)
        except Exception:
            pass
        gone = wait_until(lambda: not any(s.get("id") == auto_sid for s in sessions()), timeout=12)
        check("浏览器退出后会话消失", bool(gone), json.dumps(sessions(), ensure_ascii=False)[:300])
        check("浏览器退出后 SOCKS 端口关闭", wait_port(auto_port, False, 6), str(auto_port))
        removed = wait_until(lambda: not os.path.isdir(auto_profile), timeout=6)
        check("浏览器退出后配置目录删除", bool(removed), auto_profile)

    print("[8] 停止会话 → 进程/隧道/配置清理")
    check("首会话仍在运行", any(s.get("id") == session_id for s in sessions()), json.dumps(sessions(), ensure_ascii=False)[:300])
    resp = stop_session(session_id)
    check("停止接口返回 200", data_of(resp) is not None, error_text(resp))
    check("停止后会话列表为空", sessions() == [], json.dumps(sessions(), ensure_ascii=False)[:300])
    check("停止后 SOCKS 端口关闭", wait_port(port, False, 6), str(port))
    check("停止后配置目录删除", wait_until(lambda: not os.path.isdir(profile), timeout=6), profile)
    if pid:
        check("停止后 Chrome 进程退出", wait_until(lambda: not path_exists(f"/proc/{pid}"), timeout=6), str(pid))


def path_exists(path: str) -> bool:
    return os.path.exists(path)


# ------------------------------------------------------ missing chrome case
def test_missing_chrome() -> None:
    global BASE2, HOME2, PORT2, SERVER2_PROC
    print("[11] 未安装 Chrome 的错误路径")
    PORT2 = free_port()
    HOME2 = tempfile.mkdtemp(prefix="h1khub-chrome-missing-")
    missing = os.path.join(HOME2, "no-such-chrome")
    SERVER2_PROC = start_server(HOME2, PORT2, missing)
    BASE2 = f"http://127.0.0.1:{PORT2}"
    info = data_of(req("/api/chrome-proxy", method="GET", base=BASE2)) or {}
    check("缺失 Chrome 时 available=false", info.get("available") is False, json.dumps(info, ensure_ascii=False)[:300])
    check("缺失 Chrome 时返回尝试路径", missing in (info.get("tried") or []), str(info.get("tried")))

    ssh_id = create_asset(
        f"zz-missing-chrome-ssh-{SUFFIX}",
        "SSH",
        {
            "host": "127.0.0.1",
            "port": SSHD_PORT,
            "username": getpass.getuser(),
            "authType": "key",
            "privateKey": open(os.path.join(LAB, "client_key"), encoding="utf-8").read(),
        },
        base=BASE2,
    )
    resp = req("/api/chrome-proxy/start", {"assetId": ssh_id}, base=BASE2)
    check("缺失 Chrome 时启动返回 400", resp.get("__http_error") == 400, json.dumps(resp, ensure_ascii=False)[:300])
    message = error_text(resp)
    check("错误信息含未找到 Chrome", "未找到 Chrome" in message, message[:300])
    check("错误信息含尝试路径", missing in message, message[:400])
    check("缺失 Chrome 时没有会话", sessions(BASE2) == [], "")


# -------------------------------------------------- immediate-exit browser
def test_browser_exits_immediately() -> None:
    global BASE3, HOME3, PORT3, SERVER3_PROC
    print("[12] 浏览器启动后立即退出：错误与清理")
    PORT3 = free_port()
    HOME3 = tempfile.mkdtemp(prefix="h1khub-chrome-exit-")
    bad = os.path.join(HOME3, "exit-chrome.sh")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 7\n")
    os.chmod(bad, 0o755)
    SERVER3_PROC = start_server(HOME3, PORT3, bad)
    BASE3 = f"http://127.0.0.1:{PORT3}"
    ssh_id = create_asset(
        f"zz-exit-chrome-ssh-{SUFFIX}",
        "SSH",
        {
            "host": "127.0.0.1",
            "port": SSHD_PORT,
            "username": getpass.getuser(),
            "authType": "key",
            "privateKey": open(os.path.join(LAB, "client_key"), encoding="utf-8").read(),
        },
        base=BASE3,
    )
    resp = req("/api/chrome-proxy/start", {"assetId": ssh_id}, base=BASE3)
    check("立即退出返回 400", resp.get("__http_error") == 400, json.dumps(resp, ensure_ascii=False)[:300])
    message = error_text(resp)
    check("错误信息含退出码", "exit=7" in message, message[:300])
    check("错误信息提示图形会话", "DISPLAY" in message, message[:300])
    check("立即退出后没有残留会话", sessions(BASE3) == [], "")


# ------------------------------------------------------------------- main
def main() -> int:
    global HOME, PORT, BASE, SERVER_PROC
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep temp homes and print paths")
    args = ap.parse_args()

    started = time.time()
    HOME = tempfile.mkdtemp(prefix="h1khub-chrome-home-")
    PORT = free_port()
    BASE = f"http://127.0.0.1:{PORT}"
    try:
        print(f"lab: home={HOME} replica={BASE}")
        setup_lab()
        SERVER_PROC = start_server(HOME, PORT, MOCK)
        print(f"lab: sshd=127.0.0.1:{SSHD_PORT} http=127.0.0.1:{HTTP_PORT} chrome={MOCK}")

        key = open(os.path.join(LAB, "client_key"), encoding="utf-8").read()
        key_id = create_asset(f"zz-chrome-key-{SUFFIX}", "SSHPrivateKey", {"privateKey": key, "passphrase": ""})
        ssh_id = create_asset(
            f"zz-chrome-ssh-{SUFFIX}",
            "SSH",
            {
                "host": "127.0.0.1",
                "port": SSHD_PORT,
                "username": getpass.getuser(),
                "authType": "key",
                "privateKeyAssetId": key_id,
                "connectTimeout": 8,
            },
        )
        dir_id = create_asset(f"zz-chrome-dir-{SUFFIX}", "Dir", {})

        test_api(ssh_id, key_id, dir_id)
        asyncio.run(ui_checks(ssh_id, dir_id))
        test_missing_chrome()
        test_browser_exits_immediately()

        # Leave no sessions behind.
        for session in sessions():
            if session.get("id"):
                stop_session(session["id"])
        for session in sessions(BASE2):
            if session.get("id"):
                stop_session(session["id"], base=BASE2)
        for session in sessions(BASE3):
            if session.get("id"):
                stop_session(session["id"], base=BASE3)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        check("测试执行未抛出异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        delete_assets()
        stop_server(SERVER_PROC)
        stop_server(SERVER2_PROC)
        stop_server(SERVER3_PROC)
        teardown_lab()
        if not args.keep:
            shutil.rmtree(HOME, ignore_errors=True)
            if HOME2:
                shutil.rmtree(HOME2, ignore_errors=True)
            if HOME3:
                shutil.rmtree(HOME3, ignore_errors=True)
            shutil.rmtree(LAB, ignore_errors=True)

    elapsed = time.time() - started
    print(f"\nPASS={PASS} FAIL={FAIL}  ({elapsed:.1f}s)")
    if FAILURES:
        print("failures: " + "; ".join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
