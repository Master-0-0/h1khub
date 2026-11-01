"""Interactive terminal sessions: local PTY, SSH, Telnet and Docker exec."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import logging
import os
import pty
import shlex
import signal
import socket
import struct
import subprocess
import termios
import threading
import time
from typing import Any

import paramiko

from sshlib import SSHChain, SSHError, connect_chain, exec_on_transport, HostKeyResolver
from util import b64encode_bytes, new_id, now_iso

log = logging.getLogger("h1khub.session")


class SessionError(Exception):
    pass


X11_AUTH_FILE = os.path.expanduser("~/.Xauthority")


def _x11_cookie(display: str) -> str:
    """Read the MIT-MAGIC-COOKIE-1 value for a local display from Xauthority."""
    try:
        out = subprocess.run(
            ["xauth", "-f", X11_AUTH_FILE, "list", display],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in reversed((out.stdout or "").strip().splitlines()):
            parts = line.split()
            if len(parts) >= 3 and parts[1].lower() == "mit-magic-cookie-1":
                return parts[2]
    except Exception:
        pass
    return ""


def _pick_x11_display() -> str:
    """Prefer $DISPLAY, then :1/:0 if Xauthority has a cookie for them."""
    env = os.environ.get("DISPLAY", "")
    candidates = [env] if env else []
    candidates += [":1", ":0"]
    for display in candidates:
        if not display:
            continue
        if _x11_cookie(display.split(".")[0]):
            return display
    return env or ":0"



class BaseSession:
    kind = "base"

    def __init__(self, title: str, asset_id: str | None = None, cols: int = 120, rows: int = 32) -> None:
        self.id = new_id("s_")
        self.kind = self.__class__.kind
        self.title = title
        self.asset_id = asset_id
        self.cols = max(2, int(cols or 120))
        self.rows = max(2, int(rows or 32))
        self.created_at = now_iso()
        self.closed = False
        self.buffer = bytearray()
        self.subscribers: set[asyncio.Queue] = set()
        self.loop = asyncio.get_event_loop()
        self.exit_code: int | None = None
        self.error: str | None = None
        self.meta: dict[str, Any] = {}

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        raise NotImplementedError

    async def write(self, data: bytes) -> None:
        raise NotImplementedError

    async def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(2, cols), max(2, rows)

    async def close(self) -> None:
        self.closed = True

    # -------------------------------------------------------------- plumbing
    def emit(self, event: dict) -> None:
        """Thread-safe broadcast to attached websocket queues."""
        self.loop.call_soon_threadsafe(self._emit_now, event)

    def _emit_now(self, event: dict) -> None:
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self.buffer.extend(data)
        if len(self.buffer) > 512 * 1024:
            del self.buffer[: len(self.buffer) - 512 * 1024]
        # Called from reader threads (SSH/local PTY); schedule on the session loop.
        self.emit({"type": "data", "data": base64.b64encode(data).decode("ascii")})

    def emit_exit(self, code: int | None = None, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        self.exit_code = code
        self._emit_now({"type": "exit", "code": code, "reason": reason})

    def attach(self, queue: asyncio.Queue) -> None:
        self.subscribers.add(queue)
        if self.buffer:
            queue.put_nowait(
                {"type": "history", "data": base64.b64encode(bytes(self.buffer)).decode("ascii")}
            )
        if self.closed:
            queue.put_nowait({"type": "exit", "code": self.exit_code, "reason": self.error or ""})

    def detach(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def info(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "assetId": self.asset_id,
            "cols": self.cols,
            "rows": self.rows,
            "closed": self.closed,
            "createdAt": self.created_at,
            "exitCode": self.exit_code,
            "error": self.error,
            "meta": self.meta,
        }


class LocalShellSession(BaseSession):
    kind = "local"

    def __init__(self, title: str, cfg: dict, asset_id: str | None = None, cols: int = 120, rows: int = 32) -> None:
        super().__init__(title, asset_id, cols, rows)
        self.cfg = cfg or {}
        self.proc: subprocess.Popen | None = None
        self.master_fd: int = -1
        self.reader_installed = False

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("TERM", self.cfg.get("termType") or "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")
        env.setdefault("LANG", env.get("LANG") or "C.UTF-8")
        env.update({str(k): str(v) for k, v in (self.cfg.get("env") or {}).items()})
        return env

    async def start(self) -> None:
        shell = (self.cfg.get("shell") or "").strip()
        if not shell:
            shell = os.environ.get("SHELL") or "/bin/bash"
        if not os.path.isabs(shell) or not os.access(shell, os.X_OK):
            raise SessionError(f"Shell 不存在或不可执行：{shell}")
        cwd = (self.cfg.get("cwd") or "").strip() or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")
        master_fd, slave_fd = pty.openpty()
        try:
            self.proc = subprocess.Popen(
                [shell, "-l"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=self._build_env(),
                preexec_fn=os.setsid,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)
        self.master_fd = master_fd
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        await self.resize(self.cols, self.rows)
        self.loop.add_reader(master_fd, self._on_readable)
        self.reader_installed = True
        self.meta["pid"] = self.proc.pid
        self.meta["shell"] = shell

    def _on_readable(self) -> None:
        if self.master_fd < 0:
            return
        try:
            data = os.read(self.master_fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            self._finalize(self.proc.poll() if self.proc else None, "pty closed")
            return
        if not data:
            self._finalize(self.proc.poll() if self.proc else None, "eof")
            return
        self.feed(data)

    def _finalize(self, code: int | None, reason: str) -> None:
        if self.reader_installed and self.master_fd >= 0:
            try:
                self.loop.remove_reader(self.master_fd)
            except Exception:
                pass
            self.reader_installed = False
        self.emit_exit(code, reason)

    async def write(self, data: bytes) -> None:
        if self.master_fd < 0 or self.closed:
            return
        try:
            os.write(self.master_fd, data)
        except OSError as exc:
            log.debug("pty write failed: %s", exc)

    async def resize(self, cols: int, rows: int) -> None:
        await super().resize(cols, rows)
        if self.master_fd >= 0:
            try:
                fcntl.ioctl(
                    self.master_fd,
                    termios.TIOCSWINSZ,
                    struct.pack("HHHH", self.rows, self.cols, 0, 0),
                )
            except OSError:
                pass

    async def close(self) -> None:
        if self.closed:
            return
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGHUP)
            except OSError:
                pass
            await asyncio.sleep(0.25)
            if self.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except OSError:
                    pass
            try:
                self.proc.wait(timeout=2)
            except Exception:
                pass
        if self.reader_installed and self.master_fd >= 0:
            try:
                self.loop.remove_reader(self.master_fd)
            except Exception:
                pass
            self.reader_installed = False
        if self.master_fd >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = -1
        self.emit_exit(self.proc.returncode if self.proc else None, "closed")


class PTYCommandSession(LocalShellSession):
    """A PTY-backed arbitrary command (used by docker exec)."""

    kind = "docker"

    def __init__(
        self,
        title: str,
        argv: list[str],
        env: dict[str, str] | None = None,
        asset_id: str | None = None,
        cols: int = 120,
        rows: int = 32,
        cwd: str | None = None,
    ) -> None:
        super().__init__(title, {}, asset_id, cols, rows)
        self.argv = argv
        self.extra_env = env or {}
        self.cwd_override = cwd

    async def start(self) -> None:
        master_fd, slave_fd = pty.openpty()
        env = os.environ.copy()
        env.update(self.extra_env)
        env.setdefault("TERM", "xterm-256color")
        try:
            self.proc = subprocess.Popen(
                self.argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.cwd_override or os.path.expanduser("~"),
                env=env,
                preexec_fn=os.setsid,
                close_fds=True,
            )
        except Exception as exc:
            os.close(master_fd)
            os.close(slave_fd)
            raise SessionError(f"命令启动失败：{exc}") from exc
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        self.master_fd = master_fd
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        await self.resize(self.cols, self.rows)
        self.loop.add_reader(master_fd, self._on_readable)
        self.reader_installed = True


class SSHSession(BaseSession):
    kind = "ssh"

    def __init__(
        self,
        title: str,
        cfg: dict,
        resolver: HostKeyResolver,
        asset_id: str | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        super().__init__(title, asset_id, cols, rows)
        self.cfg = cfg or {}
        self.jump_chain = list(self.cfg.pop("__jump_chain", None) or [])
        self.chain_cfgs = [*self.jump_chain, self.cfg]
        self.resolver = resolver
        self.chain: SSHChain | None = None
        self.transport: paramiko.Transport | None = None
        self.channel: paramiko.Channel | None = None
        self.reader_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sftp: paramiko.SFTPClient | None = None
        self._sftp_lock = threading.Lock()

    async def start(self) -> None:
        cfg = self.cfg
        try:
            chain = await asyncio.to_thread(connect_chain, self.chain_cfgs, self.resolver)
        except Exception as exc:
            self.error = str(exc)
            raise SessionError(str(exc)) from exc
        self.chain = chain
        transport = chain.transport
        self.transport = transport
        self.meta["hostKey"] = chain.target_state
        self.meta["host"] = cfg.get("host")
        self.meta["port"] = cfg.get("port")
        self.meta["username"] = cfg.get("username")
        if self.jump_chain:
            self.meta["jumpChain"] = [f"{hop.get('host')}:{hop.get('port')}" for hop in self.jump_chain]
        term = cfg.get("termType") or "xterm-256color"
        try:
            chan = transport.open_session(timeout=15)
            chan.get_pty(term=term, width=self.cols, height=self.rows, width_pixels=0, height_pixels=0)
            if cfg.get("env"):
                try:
                    chan.update_environment({str(k): str(v) for k, v in cfg["env"].items()})
                except Exception:
                    pass
            if cfg.get("agentForward"):
                try:
                    transport.set_agent_forwarding(True)
                except Exception:
                    pass
            if cfg.get("x11"):
                self._start_x11(chan)
            chan.invoke_shell()
            chan.settimeout(0.0)
        except Exception as exc:
            chain.close()
            self.chain = None
            raise SessionError(f"打开 SSH 会话失败：{exc}") from exc
        self.channel = chan
        self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.reader_thread.start()
        init = (cfg.get("initCommand") or "").strip()
        if init:
            chan.send((init + "\n").encode())

    # X11 forwarding -------------------------------------------------------
    def _start_x11(self, chan: paramiko.Channel) -> None:
        display = str(self.cfg.get("x11Display") or _pick_x11_display())
        cookie = _x11_cookie(display.split(".")[0])
        if not cookie:
            self.meta["x11Error"] = f"未找到 {display} 的 Xauthority cookie"
            return
        try:
            screen = int(display.split(".", 1)[1]) if "." in display else 0
        except ValueError:
            screen = 0
        try:
            chan.request_x11(screen_number=screen, auth_cookie=cookie, handler=self._x11_handler)
            self.meta["x11"] = display
        except Exception as exc:  # pragma: no cover - server dependent
            self.meta["x11Error"] = str(exc)

    def _x11_handler(self, chan: paramiko.Channel, origin: tuple[str, int]) -> None:
        """Proxy one forwarded X11 connection to the local display."""
        display = str(self.meta.get("x11") or ":0")
        try:
            number = int(display.split(":")[-1].split(".")[0])
            sock = socket.create_connection(("127.0.0.1", 6000 + number), timeout=10)
        except Exception as exc:
            self.meta["x11Error"] = f"连接本地 X 服务器失败：{exc}"
            try:
                chan.close()
            except Exception:
                pass
            return

        def pump(src, dst, close_src, close_dst) -> None:
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                for closer in (close_src, close_dst):
                    try:
                        closer()
                    except Exception:
                        pass

        threading.Thread(target=pump, args=(chan, sock, chan.close, sock.close), daemon=True).start()
        threading.Thread(target=pump, args=(sock, chan, sock.close, chan.close), daemon=True).start()

    def _read_loop(self) -> None:
        chan = self.channel
        assert chan is not None
        while not self._stop.is_set():
            try:
                if chan.recv_ready():
                    data = chan.recv(65536)
                    if not data:
                        break
                    self.feed(data)
                    continue
                if chan.exit_status_ready() and not chan.recv_ready():
                    break
                time.sleep(0.02)
            except socket.timeout:
                continue
            except Exception as exc:
                self.error = str(exc)
                break
        code = None
        try:
            if chan.exit_status_ready():
                code = chan.recv_exit_status()
        except Exception:
            pass
        try:
            chan.close()
        except Exception:
            pass
        self.emit_exit(code, "ssh closed")

    async def write(self, data: bytes) -> None:
        if not self.channel or self.closed:
            return
        try:
            self.channel.send(data)
        except Exception as exc:
            log.debug("ssh write failed: %s", exc)

    async def resize(self, cols: int, rows: int) -> None:
        await super().resize(cols, rows)
        if self.channel:
            try:
                self.channel.resize_pty(width=self.cols, height=self.rows)
            except Exception:
                pass

    def get_sftp(self) -> paramiko.SFTPClient:
        with self._sftp_lock:
            if self._sftp is not None:
                return self._sftp
            if not self.transport:
                raise SessionError("SSH 会话未连接")
            self._sftp = paramiko.SFTPClient.from_transport(self.transport)
            self._sftp.get_channel().settimeout(30)
            return self._sftp

    def run_command(self, command: str, timeout: int = 60) -> dict:
        """Run a one-off command over this session's transport.

        Uses a dedicated channel so the interactive shell, its TTY and its
        output stream are untouched.  Blocking; call it from a worker thread.
        """
        if not self.transport or self.closed:
            raise SessionError("SSH 会话已关闭")
        return exec_on_transport(self.transport, command, timeout)

    async def close(self) -> None:
        if self.closed:
            return
        self._stop.set()
        try:
            if self._sftp:
                self._sftp.close()
        except Exception:
            pass
        try:
            if self.channel:
                self.channel.close()
        except Exception:
            pass
        try:
            if self.chain:
                self.chain.close()
                self.chain = None
            elif self.transport:
                self.transport.close()
        except Exception:
            pass
        self.emit_exit(None, "closed")


class TelnetSession(BaseSession):
    kind = "telnet"

    def __init__(
        self,
        title: str,
        cfg: dict,
        asset_id: str | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        super().__init__(title, asset_id, cols, rows)
        self.cfg = cfg or {}
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.task: asyncio.Task | None = None
        self._naws = False
        self._iac_buffer = bytearray()

    async def start(self) -> None:
        host = (self.cfg.get("host") or "").strip()
        port = int(self.cfg.get("port") or 23)
        if not host:
            raise SessionError("Telnet 主机地址为空")
        timeout = min(float(self.cfg.get("connectTimeout") or 15), 120)
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
        except Exception as exc:
            raise SessionError(f"Telnet 连接失败：{exc}") from exc
        self.meta["host"] = host
        self.meta["port"] = port
        # Negotiate a sane terminal from the client side.
        await self._send(b"\xff\xfd\x18")  # DO TERMINAL-TYPE
        await self._send(b"\xff\xfd\x20")  # DO TERMINAL-SPEED
        await self._send(b"\xff\xfd\x23")  # DO XDISPLOC
        await self._send(b"\xff\xfd\x27")  # DO NEW-ENVIRON
        await self._send(b"\xff\xfb\x01")  # WILL ECHO
        await self._send(b"\xff\xfb\x03")  # WILL SUPPRESS-GO-AHEAD
        await self._send_naws()
        self.task = asyncio.create_task(self._read_loop())

    async def _send(self, data: bytes) -> None:
        if self.writer:
            self.writer.write(data)
            await self.writer.drain()

    async def _send_naws(self) -> None:
        payload = struct.pack("!HH", self.cols, self.rows)
        escaped = payload.replace(b"\xff", b"\xff\xff")
        await self._send(b"\xff\xfa\x1f\x00" + escaped + b"\xff\xf0")
        self._naws = True

    async def _handle_iac(self, data: bytes) -> bytes:
        """Strip telnet negotiation, answer options, return remaining payload."""
        out = bytearray()
        buf = self._iac_buffer + data
        self._iac_buffer.clear()
        i = 0
        replies = bytearray()

        def reply(*items: int) -> None:
            replies.extend(bytes(items))

        while i < len(buf):
            b = buf[i]
            if b != 0xFF:
                out.append(b)
                i += 1
                continue
            if i + 1 >= len(buf):
                self._iac_buffer.extend(buf[i:])
                break
            cmd = buf[i + 1]
            if cmd == 0xFF:
                out.append(0xFF)
                i += 2
                continue
            if cmd in (0xFB, 0xFC, 0xFD, 0xFE):
                if i + 2 >= len(buf):
                    self._iac_buffer.extend(buf[i:])
                    break
                opt = buf[i + 2]
                if cmd == 0xFD:  # DO
                    if opt in (0x18, 0x20, 0x23, 0x27):
                        reply(0xFF, 0xFB, opt)  # WILL
                    else:
                        reply(0xFF, 0xFC, opt)  # WONT
                elif cmd == 0xFB:  # WILL
                    if opt in (0x01, 0x03):
                        reply(0xFF, 0xFD, opt)  # DO
                    else:
                        reply(0xFF, 0xFE, opt)  # DONT
                elif cmd == 0xFE:  # DONT
                    reply(0xFF, 0xFC, opt)  # WONT
                else:  # WONT
                    pass
                i += 3
                continue
            if cmd == 0xFA:  # SB
                end = buf.find(b"\xff\xf0", i)
                if end < 0:
                    self._iac_buffer.extend(buf[i:])
                    break
                i = end + 2
                continue
            i += 2
        if replies:
            await self._send(bytes(replies))
        return bytes(out)

    async def _read_loop(self) -> None:
        assert self.reader is not None
        try:
            while not self.closed:
                data = await self.reader.read(65536)
                if not data:
                    break
                payload = await self._handle_iac(data)
                if payload:
                    self.feed(payload)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.emit_exit(None, "telnet closed")

    async def write(self, data: bytes) -> None:
        if not self.writer or self.closed:
            return
        # Escape IAC bytes in user data
        try:
            self.writer.write(data.replace(b"\xff", b"\xff\xff"))
            await self.writer.drain()
        except Exception:
            pass

    async def resize(self, cols: int, rows: int) -> None:
        await super().resize(cols, rows)
        if self._naws and self.writer:
            await self._send_naws()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.task:
            self.task.cancel()
        try:
            if self.writer:
                self.writer.close()
        except Exception:
            pass
        self._emit_now({"type": "exit", "code": None, "reason": "closed"})


class SerialSession(BaseSession):
    kind = "serial"

    def __init__(self, title: str, cfg: dict, asset_id: str | None = None, cols: int = 120, rows: int = 32) -> None:
        super().__init__(title, asset_id, cols, rows)
        self.cfg = cfg or {}
        self.ser = None
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()

    async def start(self) -> None:
        import serial

        port = (self.cfg.get("port") or self.cfg.get("device") or "").strip()
        if not port:
            raise SessionError("串口设备路径为空")
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=int(self.cfg.get("baudRate") or self.cfg.get("baudrate") or 9600),
                bytesize=int(self.cfg.get("dataBits") or 8),
                parity=str(self.cfg.get("parity") or "N")[0].upper(),
                stopbits=float(self.cfg.get("stopBits") or 1),
                timeout=0.05,
                write_timeout=5,
            )
        except Exception as exc:
            raise SessionError(f"串口打开失败：{exc}") from exc
        self.meta.update(
            {
                "port": port,
                "baudRate": self.ser.baudrate,
                "dataBits": self.ser.bytesize,
                "parity": self.ser.parity,
                "stopBits": self.ser.stopbits,
            }
        )
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self.ser.read(4096) if self.ser else b""
            except Exception as exc:
                self.error = str(exc)
                break
            if data:
                self.feed(data)
            else:
                time.sleep(0.02)
        self.emit_exit(None, "serial closed")

    async def write(self, data: bytes) -> None:
        if not self.ser or self.closed:
            return
        try:
            self.ser.write(data)
            self.ser.flush()
        except Exception as exc:
            log.debug("serial write failed: %s", exc)

    async def close(self) -> None:
        self._stop.set()
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.closed = True


class SessionManager:
    def __init__(self, resolver: HostKeyResolver) -> None:
        self.resolver = resolver
        self.sessions: dict[str, BaseSession] = {}

    def get(self, session_id: str) -> BaseSession | None:
        return self.sessions.get(session_id)

    def list(self) -> list[dict]:
        return [s.info() for s in self.sessions.values() if not s.closed]

    def register(self, session: BaseSession) -> None:
        session.loop = asyncio.get_event_loop()
        self.sessions[session.id] = session

    async def close(self, session_id: str) -> bool:
        session = self.sessions.pop(session_id, None)
        if not session:
            return False
        await session.close()
        return True

    async def close_all(self) -> None:
        for sid in list(self.sessions):
            await self.close(sid)
