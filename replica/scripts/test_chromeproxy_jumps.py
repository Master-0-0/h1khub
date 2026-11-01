#!/usr/bin/env python3
"""Real-browser check: Chrome through a 3-jump SSH SOCKS5 chain.

Builds a throwaway lab with four OpenSSH daemons on loopback:

    Chrome -> SOCKS5 (our tunnel)
              -> hop0 (outermost)
                 -> hop1
                    -> hop2
                       -> target -> local HTTP fixture

A real Chromium is launched with the proxy and the fixture URL.  The test proves
that the request really came from Chrome *through the SOCKS5 tunnel* by checking
the tunnel's connection counter and the User-Agent seen by the fixture, and
proves the chain by checking every sshd log for a new authenticated connection.
A screenshot of the browser window is captured when ImageMagick/xdotool are
available.

Usage: python3 scripts/test_chromeproxy_jumps.py
"""
from __future__ import annotations

import getpass
import http.server
import json
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(ROOT, "docs", "screenshots", "43-chrome-proxy-multihop.png")
SUFFIX = "%04x" % int.from_bytes(os.urandom(2), "big")
PASS = 0
FAIL = 0
FAILURES: list[str] = []

HOME = ""
LAB = ""
BASE = ""
TOKEN = "chromeproxy-jumps-token"
PORT = 0
PORTS: list[int] = []
SSHD_PROCS: list[subprocess.Popen] = []
SERVER_PROC: subprocess.Popen | None = None
HTTP_SERVER = None
HTTP_PORT = 0
PROFILE_DIRS: set[str] = set()
CREATED_ASSETS: list[str] = []


class Fixture:
    hit = threading.Event()
    headers: dict[str, str] = {}
    paths: list[str] = []


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        Fixture.paths.append(self.path)
        Fixture.headers = {key.lower(): value for key, value in self.headers.items()}
        body = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>H1KHub Multi-hop Chrome OK</title></head>"
            "<body style='font-family: sans-serif; padding: 32px'>"
            "<h1>MULTIHOP-CHROME-OK</h1>"
            "<p>This page was loaded by Chrome through a 3-jump SSH SOCKS5 chain.</p>"
            "</body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        Fixture.hit.set()

    def log_message(self, *args) -> None:
        pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ------------------------------------------------------------------ helpers
def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail[:700]}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, opened: bool = True, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port) == opened:
            return True
        time.sleep(0.1)
    return False


def wait_event(event: threading.Event, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if event.wait(0.2):
            return True
    return False


def wait_cond(fn, timeout: float = 10.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception:
            pass
        time.sleep(0.15)
    return last


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr or proc.stdout}")


def req(path: str, body=None, method: str = "POST"):
    data = None
    headers = {"X-H1KHub-Token": TOKEN}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
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


def create_asset(name: str, type_name: str, config: dict) -> str:
    resp = req("/api/assets", {"typeName": type_name, "name": name, "config": config})
    data = data_of(resp)
    if not data:
        raise RuntimeError(f"create asset failed: {resp}")
    CREATED_ASSETS.append(data["asset"]["id"])
    return data["asset"]["id"]


def sessions() -> list[dict]:
    return (data_of(req("/api/chrome-proxy", method="GET")) or {}).get("sessions") or []


def real_chrome() -> str:
    candidates = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return ""


# --------------------------------------------------------------------- lab
def sshd_config(index: int) -> str:
    return f"""Port {PORTS[index]}
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
    if not wait_port(PORTS[index]):
        raise RuntimeError(f"sshd {index} did not listen on {PORTS[index]}")


def setup_lab() -> None:
    global LAB, PORTS, HTTP_SERVER, HTTP_PORT
    LAB = tempfile.mkdtemp(prefix="h1khub-chrome-jumps-")
    os.chmod(LAB, 0o700)
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", f"{LAB}/client_key"])
    for index in range(4):
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", f"{LAB}/host_{index}"])
    while len(PORTS) < 4:
        candidate = free_port()
        if candidate not in PORTS:
            PORTS.append(candidate)
    for index in range(4):
        start_sshd(index)

    HTTP_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    HTTP_PORT = HTTP_SERVER.server_address[1]
    threading.Thread(target=HTTP_SERVER.serve_forever, daemon=True).start()


def stop_lab() -> None:
    global HTTP_SERVER
    if HTTP_SERVER is not None:
        try:
            HTTP_SERVER.shutdown()
            HTTP_SERVER.server_close()
        except Exception:
            pass
        HTTP_SERVER = None
    for proc in SSHD_PROCS:
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    SSHD_PROCS.clear()
    for profile in list(PROFILE_DIRS):
        shutil.rmtree(profile, ignore_errors=True)


def start_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["H1KHUB_HOME"] = HOME
    env.pop("H1KHUB_CHROME_PATH", None)  # use the real browser for this test
    log = open(os.path.join(HOME, "server-stdout.log"), "wb")
    proc = subprocess.Popen(
        [
            sys.executable,
            os.path.join(ROOT, "backend", "app.py"),
            "--port",
            str(PORT),
            "--home",
            HOME,
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
    if not wait_port(PORT, timeout=25):
        raise RuntimeError("replica did not listen")
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


def accepted_count(index: int) -> int:
    try:
        with open(f"{LAB}/sshd_{index}.log", encoding="utf-8", errors="replace") as fh:
            return fh.read().count("Accepted publickey")
    except FileNotFoundError:
        return 0


def capture_browser_window(path: str) -> tuple[bool, str]:
    if not shutil.which("xdotool") or not shutil.which("import"):
        return False, "xdotool/import unavailable"
    found = subprocess.run(
        ["xdotool", "search", "--name", "H1KHub Multi-hop Chrome OK"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    ids = [line.strip() for line in found.stdout.splitlines() if line.strip().isdigit()]
    if not ids:
        return False, "browser window not found"
    window = ids[-1]
    subprocess.run(["xdotool", "windowactivate", "--sync", window], capture_output=True, timeout=10)
    time.sleep(0.8)
    shot = subprocess.run(["import", "-window", window, path], capture_output=True, text=True, timeout=20)
    if shot.returncode != 0 or not os.path.isfile(path):
        return False, (shot.stderr or "import failed").strip()[:300]
    if os.path.getsize(path) < 5000:
        return False, f"screenshot too small: {os.path.getsize(path)} bytes"
    return True, path


# --------------------------------------------------------------------- main
def main() -> int:
    global HOME, PORT, BASE, SERVER_PROC
    started = time.time()
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("SKIP: no graphical session (DISPLAY/WAYLAND_DISPLAY missing)")
        return 0
    browser = real_chrome()
    if not browser:
        print("SKIP: no Chrome/Chromium executable found")
        return 0

    HOME = tempfile.mkdtemp(prefix="h1khub-chrome-jumps-home-")
    PORT = free_port()
    BASE = f"http://127.0.0.1:{PORT}"
    try:
        print(f"lab: home={HOME} replica={BASE} browser={browser}")
        setup_lab()
        SERVER_PROC = start_server()
        print(f"lab: sshd ports={PORTS} fixture=127.0.0.1:{HTTP_PORT}")

        print("[1] 三跳 SSH 链 + 真实 Chrome")
        key = open(f"{LAB}/client_key", encoding="utf-8").read()
        key_id = create_asset(f"zz-mh-key-{SUFFIX}", "SSHPrivateKey", {"privateKey": key, "passphrase": ""})
        chain_ids: list[str] = []
        parent = ""
        for index in range(4):
            cfg = {
                "host": "127.0.0.1",
                "port": PORTS[index],
                "username": getpass.getuser(),
                "authType": "key",
                "privateKeyAssetId": key_id,
                "connectTimeout": 10,
            }
            if parent:
                cfg["jumpAssetId"] = parent
            parent = create_asset(f"zz-mh-hop-{index}-{SUFFIX}", "SSH", cfg)
            chain_ids.append(parent)
        target = chain_ids[-1]
        check("创建 4 个 SSH 资产（3 跳 + 目标）", len(chain_ids) == 4, str(chain_ids))

        conn = data_of(req("/api/test-connection", {"assetId": target})) or {}
        hops = conn.get("chain") or []
        check("连接测试返回 4 跳链", len(hops) == 4, json.dumps(hops, ensure_ascii=False)[:400])
        check("每跳主机密钥正常", all(hop.get("hostKey") in ("new", "known") for hop in hops), str([h.get("hostKey") for h in hops]))

        baseline = [accepted_count(i) for i in range(4)]
        info = data_of(req("/api/chrome-proxy", method="GET")) or {}
        check("检测到真实 Chrome", info.get("available") is True and info.get("path") != "", json.dumps(info, ensure_ascii=False)[:300])
        check("启动前没有会话", info.get("sessions") == [], str(info.get("sessions")))

        url = f"http://127.0.0.1:{HTTP_PORT}/"
        resp = req("/api/chrome-proxy/start", {"assetId": target, "url": url})
        session = (data_of(resp) or {}).get("session") or {}
        check("经三跳链启动 Chrome 返回 200", bool(session.get("id")), error_text(resp))
        if not session:
            return 1
        sid = session["id"]
        proxy_port = int(session.get("port") or 0)
        profile = session.get("profileDir") or ""
        if profile:
            PROFILE_DIRS.add(profile)
        check("代理地址为 socks5://127.0.0.1", str(session.get("proxy", "")).startswith("socks5://127.0.0.1:"), str(session.get("proxy")))
        check("SOCKS5 端口已监听", wait_port(proxy_port, True, 6), str(proxy_port))

        print("[2] Chrome 实际经代理加载页面")
        hit = wait_event(Fixture.hit, 45)
        check("Chrome 请求到达本地 fixture", bool(hit), f"paths={Fixture.paths[:5]}")
        if hit:
            ua = Fixture.headers.get("user-agent", "")
            check("User-Agent 为真实 Chrome", "Chrome/" in ua or "Chromium/" in ua, ua[:300])
            check("请求的是指定 URL", any(path.startswith("/") for path in Fixture.paths), str(Fixture.paths[:5]))
        live = wait_cond(lambda: next((s for s in sessions() if s.get("id") == sid), None), timeout=10) or {}
        check("会话连接计数 > 0（证明流量走 SOCKS5）", int(live.get("connections") or 0) >= 1, f"connections={live.get('connections')}")
        targets = live.get("recentConnections") or []
        check(
            "SOCKS5 目标包含 fixture 地址（页面确实走代理）",
            any(str(HTTP_PORT) in str(target) for target in targets),
            json.dumps(targets, ensure_ascii=False)[:300],
        )

        print("[3] 每一跳都建立了新的 SSH 认证")
        after: list[int] = []
        deadline = time.time() + 15
        while time.time() < deadline:
            after = [accepted_count(i) for i in range(4)]
            if all(after[i] > baseline[i] for i in range(4)):
                break
            time.sleep(0.3)
        for index in range(4):
            label = "目标" if index == 3 else f"跳板 {index}"
            check(f"{label} 新增认证连接", after[index] > baseline[index], f"{baseline[index]} -> {after[index]}")

        print("[4] 浏览器窗口截图")
        shot, detail = capture_browser_window(SHOT)
        check("捕获 Chrome 窗口截图", bool(shot), detail)

        print("[5] 停止并清理")
        stop_resp = req(f"/api/chrome-proxy/{sid}/stop", {})
        check("停止接口返回成功", data_of(stop_resp) is not None, error_text(stop_resp))
        check("停止后会话为空", sessions() == [], str(sessions()))
        check("停止后 SOCKS 端口关闭", wait_port(proxy_port, False, 8), str(proxy_port))
        check("停止后临时配置删除", wait_cond(lambda: not os.path.isdir(profile), timeout=8), profile)
        check("停止后 Chrome 进程退出", wait_cond(lambda: not os.path.exists(f"/proc/{session.get('pid')}"), timeout=8), str(session.get("pid")))
    except Exception as exc:
        import traceback

        traceback.print_exc()
        check("测试执行未抛出异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        for asset_id in list(CREATED_ASSETS):
            try:
                req(f"/api/assets/{asset_id}", method="DELETE")
            except Exception:
                pass
        CREATED_ASSETS.clear()
        stop_server(SERVER_PROC)
        stop_lab()
        if HOME:
            shutil.rmtree(HOME, ignore_errors=True)
        if LAB:
            shutil.rmtree(LAB, ignore_errors=True)

    elapsed = time.time() - started
    print(f"\nPASS={PASS} FAIL={FAIL}  ({elapsed:.1f}s)")
    if FAILURES:
        print("failures: " + "; ".join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
