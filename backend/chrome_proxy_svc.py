"""Proxy a local Chrome through an SSH asset.

This is the replica's port of the original product's *通过服务器代理 Chrome*
action (``startTunnelForWebBrowser`` -> ``executil.OpenChrome``):

* open a dynamic SOCKS5 listener on ``127.0.0.1`` over the SSH asset (jump-host
  chains included, because it uses the same ``hub.open_ssh_transport`` path);
* launch Chrome with ``--proxy-server=socks5://127.0.0.1:<port>`` and a
  throwaway profile directory;
* tear the tunnel down and delete the profile when Chrome exits.

The original's Chrome discovery paths and command-line flags are kept, with a
``H1KHUB_CHROME_PATH`` override (useful for tests and non-standard installs).
Everything stays on the local machine: no cloud calls, no remote Chrome.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

from tunnel_svc import ManagedTunnel
from util import new_id, now_iso

log = logging.getLogger("h1khub.chrome")

SSH = 2
MAX_SESSIONS = 8
PROXY_SCHEMES = ("socks5://", "socks4://", "http://", "https://")


class ChromeProxyError(Exception):
    pass


# ------------------------------------------------------------------ discovery
def _linux_candidates() -> list[str]:
    return [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]


def _mac_candidates() -> list[str]:
    return [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    ]


def _windows_candidates() -> list[str]:
    roots = [
        os.environ.get("PROGRAMFILES") or r"C:\Program Files",
        os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)",
        os.environ.get("LOCALAPPDATA") or "",
    ]
    out: list[str] = []
    for root in roots:
        if not root:
            continue
        out.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
        out.append(os.path.join(root, "Chromium", "Application", "chrome.exe"))
    return out


def chrome_candidates() -> list[str]:
    """Ordered absolute candidates, or the single explicit override."""
    override = (os.environ.get("H1KHUB_CHROME_PATH") or "").strip()
    if override:
        return [override]
    system = platform.system()
    if system == "Darwin":
        return _mac_candidates()
    if system == "Windows":
        return _windows_candidates()
    return _linux_candidates()


def find_chrome() -> tuple[str | None, list[str]]:
    """Return ``(path, tried)``; ``path`` is None when nothing is executable."""
    candidates = chrome_candidates()
    tried: list[str] = []
    override = (os.environ.get("H1KHUB_CHROME_PATH") or "").strip()
    for candidate in candidates:
        tried.append(candidate)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate, tried
    if override:
        # An explicit override must not silently fall back to another browser.
        return None, tried
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        tried.append(name)
        if found:
            return found, tried
    return None, tried


def chrome_status() -> dict:
    path, tried = find_chrome()
    return {
        "available": bool(path),
        "path": path or "",
        "tried": tried,
        "override": (os.environ.get("H1KHUB_CHROME_PATH") or "").strip(),
        "platform": platform.system(),
    }


# ---------------------------------------------------------------------- args
def normalize_proxy(value: str) -> str:
    proxy = (value or "").strip()
    if not proxy:
        raise ChromeProxyError("缺少代理地址")
    if not proxy.lower().startswith(PROXY_SCHEMES):
        proxy = "socks5://" + proxy
    return proxy


def build_chrome_args(proxy: str, profile_dir: str, urls: list[str] | None = None) -> list[str]:
    """Flags used by the original ``executil.buildChromeArgs``."""
    args = [
        f"--proxy-server={normalize_proxy(proxy)}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--proxy-bypass-list=<-loopback>",
    ]
    for url in urls or []:
        args.append(url)
    return args


def validate_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""
    if len(url) > 2048:
        raise ChromeProxyError("URL 过长（最多 2048 字符）")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ChromeProxyError(f"URL 不合法：{exc}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ChromeProxyError("URL 必须以 http:// 或 https:// 开头")
    return url


# ------------------------------------------------------------------- session
class ChromeProxySession:
    def __init__(
        self,
        asset_id: str,
        asset_name: str,
        chrome_path: str,
        tunnel: ManagedTunnel,
        proc: subprocess.Popen,
        profile_dir: str,
        url: str = "",
        on_exit=None,
    ) -> None:
        self.id = new_id("cp")
        self.asset_id = asset_id
        self.asset_name = asset_name
        self.chrome_path = chrome_path
        self.tunnel = tunnel
        self.proc = proc
        self.profile_dir = profile_dir
        self.url = url
        self.on_exit = on_exit
        self.started_at = now_iso()
        self.started_ts = time.time()
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.running = True
        self._lock = threading.Lock()
        self._torn_down = False
        self._finished = threading.Event()
        self._thread = threading.Thread(target=self._wait, daemon=True, name=f"chrome-proxy-{self.id}")
        self._thread.start()

    # ------------------------------------------------------------- status
    @property
    def proxy(self) -> str:
        return f"socks5://127.0.0.1:{self.tunnel.bind_port}"

    def status(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "assetId": self.asset_id,
                "assetName": self.asset_name,
                "chromePath": self.chrome_path,
                "proxy": self.proxy,
                "host": self.tunnel.bind_host,
                "port": self.tunnel.bind_port,
                "pid": self.proc.pid,
                "profileDir": self.profile_dir,
                "url": self.url,
                "connections": self.tunnel.connections,
                "recentConnections": list(self.tunnel.recent_connections)[-10:],
                "running": self.running,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "exitCode": self.exit_code,
                "elapsedMs": int((time.time() - self.started_ts) * 1000),
            }

    # --------------------------------------------------------- lifecycle
    def _wait(self) -> None:
        try:
            code = self.proc.wait()
        except Exception:  # pragma: no cover - defensive
            code = None
        with self._lock:
            self.running = False
            self.exit_code = code
            self.finished_at = now_iso()
        log.info("chrome proxy %s exited (code=%s)", self.id, code)
        self._teardown()
        if self.on_exit:
            try:
                self.on_exit(self.id)
            except Exception:  # pragma: no cover - defensive
                log.exception("chrome proxy on_exit callback failed")

    def _teardown(self) -> None:
        with self._lock:
            if self._torn_down:
                return
            self._torn_down = True
        try:
            self.tunnel.stop()
        except Exception:
            log.exception("failed to stop chrome proxy tunnel %s", self.id)
        # The original waits 500 ms after the browser exits before removing the
        # profile, giving Chrome's helpers time to release their handles.
        time.sleep(0.5)
        shutil.rmtree(self.profile_dir, ignore_errors=True)
        self._finished.set()

    def _signal(self, sig: int) -> None:
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(self.proc.pid), sig)
            else:  # pragma: no cover - Windows
                self.proc.send_signal(sig)
        except Exception:
            try:
                self.proc.send_signal(sig)
            except Exception:
                pass

    def stop(self, timeout: float = 5.0) -> dict:
        if self.proc.poll() is None:
            self._signal(15)
            deadline = time.time() + 3.0
            while time.time() < deadline and self.proc.poll() is None:
                time.sleep(0.05)
            if self.proc.poll() is None:
                self._signal(9)
                try:
                    self.proc.wait(timeout=2)
                except Exception:
                    pass
        self._finished.wait(timeout)
        # Idempotent: if the wait thread has not finished cleanup yet, do it now
        # so a caller that stops a session never leaks the tunnel.
        self._teardown()
        with self._lock:
            self.running = False
            if not self.finished_at:
                self.finished_at = now_iso()
        return self.status()


# ------------------------------------------------------------------- manager
class ChromeProxyManager:
    """Owns running Chrome-via-SSH sessions for one app instance."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self.sessions: dict[str, ChromeProxySession] = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------- queries
    def list(self) -> list[dict]:
        with self.lock:
            sessions = list(self.sessions.values())
        return [session.status() for session in sessions if session.running]

    def get(self, session_id: str) -> ChromeProxySession | None:
        with self.lock:
            return self.sessions.get(session_id)

    def info(self) -> dict:
        return {**chrome_status(), "sessions": self.list(), "maxSessions": MAX_SESSIONS}

    def _drop(self, session_id: str) -> None:
        with self.lock:
            self.sessions.pop(session_id, None)

    # -------------------------------------------------------------- start
    async def start(self, asset_id: str, url: str = "") -> dict:
        asset_id = (asset_id or "").strip()
        if not asset_id:
            raise ChromeProxyError("缺少资产 ID")
        resolved = self.hub.resolved(asset_id)
        asset = resolved["asset"]
        if asset["type"] != SSH:
            raise ChromeProxyError("只有 SSH 资产支持通过服务器代理 Chrome")

        chrome_path, tried = find_chrome()
        if not chrome_path:
            raise ChromeProxyError(
                "未找到 Chrome（已尝试：" + "、".join(tried) + "）。"
                "请安装 Chrome/Chromium，或用环境变量 H1KHUB_CHROME_PATH 指定可执行文件。"
            )
        url = validate_url(url)

        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                raise ChromeProxyError(f"同时运行的 Chrome 代理实例已达上限（{MAX_SESSIONS}）")

        transport, transports = await self.hub.open_ssh_transport(asset_id)
        tunnel = ManagedTunnel(
            asset_id,
            asset["name"],
            "dynamic",
            {
                "type": "dynamic",
                "bindHost": "127.0.0.1",
                "bindPort": 0,
                "sshAssetId": asset_id,
            },
        )
        tunnel.transports = list(transports)
        try:
            await asyncio.to_thread(tunnel.start, transport)
        except Exception as exc:
            await asyncio.to_thread(tunnel.stop)
            raise ChromeProxyError(f"建立 SOCKS5 隧道失败：{exc}") from exc

        profile_dir = ""
        try:
            profile_dir = tempfile.mkdtemp(prefix="chrome-profile-")
            proxy = f"socks5://127.0.0.1:{tunnel.bind_port}"
            args = build_chrome_args(proxy, profile_dir, [url] if url else [])
            popen_kwargs: dict = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "posix":
                # Own process group so stopping a session also reaps Chrome's
                # helpers; not supported by Popen on Windows.
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen([chrome_path, *args], **popen_kwargs)
        except Exception as exc:
            await asyncio.to_thread(tunnel.stop)
            if profile_dir:
                shutil.rmtree(profile_dir, ignore_errors=True)
            raise ChromeProxyError(f"启动 Chrome 失败：{exc}") from exc

        try:
            session = ChromeProxySession(
                asset_id=asset_id,
                asset_name=asset["name"],
                chrome_path=chrome_path,
                tunnel=tunnel,
                proc=proc,
                profile_dir=profile_dir,
                url=url,
                on_exit=self._drop,
            )
        except Exception as exc:
            try:
                proc.kill()
            except Exception:
                pass
            await asyncio.to_thread(tunnel.stop)
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise ChromeProxyError(f"启动 Chrome 失败：{exc}") from exc
        with self.lock:
            self.sessions[session.id] = session
        log.info(
            "chrome proxy %s started: asset=%s pid=%s socks=%s:%s",
            session.id,
            asset["name"],
            proc.pid,
            tunnel.bind_host,
            tunnel.bind_port,
        )

        # Catch an immediate failure (missing DISPLAY, sandbox refusal, a bad
        # override).  A healthy browser stays alive well past this grace.
        await asyncio.sleep(0.6)
        if proc.poll() is not None:
            status = await asyncio.to_thread(session.stop, 3.0)
            raise ChromeProxyError(
                f"Chrome 启动后立即退出（exit={status.get('exitCode')}）。"
                "如果后端运行在无图形界面的环境，请确认 DISPLAY/WAYLAND_DISPLAY 可用。"
            )
        return session.status()

    # --------------------------------------------------------------- stop
    async def stop(self, session_id: str) -> dict | None:
        session = self.get(session_id)
        if not session:
            return None
        status = await asyncio.to_thread(session.stop)
        self._drop(session_id)
        return status

    async def stop_all(self) -> None:
        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            await asyncio.to_thread(session.stop)
        with self.lock:
            self.sessions.clear()
