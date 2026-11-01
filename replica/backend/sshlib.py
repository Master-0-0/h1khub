"""SSH transport helpers: auth, host-key trust, jump hosts, proxies, tunnels."""
from __future__ import annotations

import os
import socket
import socketserver
import threading
import time
from typing import Any

import paramiko

from util import parse_bool


class SSHError(Exception):
    pass


class HostKeyMismatch(SSHError):
    pass


class SSHChain:
    """A live SSH connection chain, ordered outermost jump host -> target.

    ``transports``/``host_states`` are aligned with ``configs``. Closing the
    chain closes every hop, target first and outermost last.
    """

    def __init__(
        self,
        configs: list[dict],
        transports: list[paramiko.Transport],
        host_states: list[str],
    ) -> None:
        self.configs = configs
        self.transports = transports
        self.host_states = host_states

    @property
    def transport(self) -> paramiko.Transport:
        return self.transports[-1]

    @property
    def target_state(self) -> str:
        return self.host_states[-1] if self.host_states else ""

    def close(self) -> None:
        for transport in reversed(self.transports):
            try:
                transport.close()
            except Exception:
                pass

    def hops(self) -> list[dict]:
        return [
            {
                "host": cfg.get("host"),
                "port": cfg.get("port"),
                "username": cfg.get("username"),
                "hostKey": state,
            }
            for cfg, state in zip(self.configs, self.host_states)
        ]


def _key_paths() -> str:
    return os.path.expanduser("~/.ssh")


class HostKeyResolver:
    """Trust-on-first-use host key store, mirroring HexHub's known_hosts behaviour."""

    def __init__(self, store_path: str) -> None:
        self.store_path = store_path
        os.makedirs(os.path.dirname(store_path), exist_ok=True)
        self.host_keys = paramiko.HostKeys()
        if os.path.exists(store_path):
            try:
                self.host_keys.load(store_path)
            except Exception:
                pass
        try:
            if os.path.exists(store_path):
                os.chmod(store_path, 0o600)
        except OSError:
            pass
        self._lock = threading.Lock()

    @staticmethod
    def _entry(host: str, port: int) -> str:
        return host if port == 22 else f"[{host}]:{port}"

    def lookup(self, host: str, port: int) -> dict:
        entry = self.host_keys.lookup(self._entry(host, port))
        return dict(entry) if entry else {}

    def _save(self) -> None:
        self.host_keys.save(self.store_path)
        try:
            os.chmod(self.store_path, 0o600)
        except OSError:
            pass

    def verify(self, host: str, port: int, server_key: paramiko.PKey) -> str:
        """Returns 'new' | 'known'. Raises HostKeyMismatch when the stored key changed."""
        entry = self._entry(host, port)
        with self._lock:
            known_map = self.host_keys.lookup(entry)
            key_type = server_key.get_name()
            if known_map:
                stored = known_map.get(key_type)
                if stored is not None:
                    if stored.get_base64() != server_key.get_base64():
                        raise HostKeyMismatch(
                            f"主机密钥不匹配：{entry} 的 {key_type} 指纹已变更（可能是中间人攻击或服务器重装）。"
                            f"已知 {stored.fingerprint}，本次 {server_key.fingerprint}。"
                            f"如确认服务器已重装，请删除 ~/.h1khub/known_hosts 后重试。"
                        )
                    return "known"
                # a different key type is stored; add this one alongside
                self.host_keys.add(entry, key_type, server_key)
                self._save()
                return "new"
            self.host_keys.add(entry, key_type, server_key)
            self._save()
            return "new"

    def fingerprint(self, host: str, port: int) -> str | None:
        keys = self.lookup(host, port)
        if not keys:
            return None
        return "; ".join(f"{k.get_name()} {k.fingerprint}" for k in keys.values())


def load_private_key(text: str, passphrase: str | None) -> paramiko.PKey:
    import io

    last_err: Exception | None = None
    classes = [paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey]
    try:
        classes.insert(0, paramiko.DSSKey)  # optional in newer paramiko
    except AttributeError:
        pass
    for cls in classes:
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except Exception as exc:  # try next key type
            last_err = exc
    try:
        return paramiko.PKey.from_private_key(io.StringIO(text), password=passphrase or None)
    except Exception:
        pass
    raise SSHError(f"无法解析私钥：{last_err}")


def _proxy_socket(proxy: dict, host: str, port: int, timeout: float) -> socket.socket:
    kind = (proxy.get("type") or "socks5").lower()
    phost = proxy.get("host") or "127.0.0.1"
    pport = int(proxy.get("port") or 1080)
    username = proxy.get("username") or ""
    password = proxy.get("password") or ""
    sock = socket.create_connection((phost, pport), timeout=timeout)
    sock.settimeout(timeout)
    if kind in ("http", "https"):
        auth = ""
        if username or password:
            import base64

            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth = f"Proxy-Authorization: Basic {token}\r\n"
        req = (
            f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"{auth}Proxy-Connection: keep-alive\r\n\r\n"
        )
        sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1024)
            if not chunk:
                raise SSHError("代理连接被关闭")
            head += chunk
            if len(head) > 65536:
                raise SSHError("代理响应异常")
        status = head.split(b"\r\n", 1)[0]
        if b"200" not in status:
            raise SSHError(f"HTTP 代理拒绝：{status.decode('latin1')}")
        return sock
    # SOCKS5
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        sock.close()
        raise SSHError("SOCKS5 代理握手失败")
    if username or password:
        sock.sendall(b"\x05\x01\x02")
        if sock.recv(2) != b"\x05\x02":
            sock.close()
            raise SSHError("SOCKS5 代理不接受用户名口令")
        u = username.encode()
        p = password.encode()
        sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        if sock.recv(2)[1:] != b"\x00":
            sock.close()
            raise SSHError("SOCKS5 认证失败")
    host_b = host.encode("idna") if not _is_ip(host) else host.encode()
    req = b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + int(port).to_bytes(2, "big")
    sock.sendall(req)
    resp = sock.recv(10)
    if len(resp) < 2 or resp[1] != 0:
        sock.close()
        raise SSHError(f"SOCKS5 连接失败（code={resp[1] if len(resp) > 1 else '?'}）")
    return sock


def _is_ip(host: str) -> bool:
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return ":" in host


def connect_transport(
    cfg: dict,
    hostkey_resolver: HostKeyResolver,
    *,
    timeout: float = 15.0,
    via: paramiko.Transport | None = None,
) -> tuple[paramiko.Transport, str]:
    """Create an authenticated paramiko Transport. Returns (transport, host_key_state)."""
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 22)
    if not host:
        raise SSHError("主机地址为空")
    ssh_timeout = min(float(cfg.get("connectTimeout") or timeout), 120.0)
    proxy = cfg.get("proxy") if isinstance(cfg.get("proxy"), dict) else None
    if proxy and not proxy.get("enabled", True):
        proxy = None
    if proxy and not proxy.get("host"):
        proxy = None

    raw_sock = None
    if via is not None:
        sock = via.open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=ssh_timeout)
        transport = paramiko.Transport(sock)
    elif proxy:
        raw_sock = _proxy_socket(proxy, host, port, ssh_timeout)
        transport = paramiko.Transport(raw_sock)
    else:
        transport = paramiko.Transport((host, port))
    transport.banner_timeout = ssh_timeout
    transport.handshake_timeout = ssh_timeout
    transport.auth_timeout = ssh_timeout
    try:
        transport.start_client(timeout=ssh_timeout)
    except Exception as exc:
        transport.close()
        if raw_sock:
            raw_sock.close()
        raise SSHError(f"SSH 握手失败：{exc}") from exc

    server_key = transport.get_remote_server_key()
    state = hostkey_resolver.verify(host, port, server_key)

    username = cfg.get("username") or os.environ.get("USER") or "root"
    auth_type = (cfg.get("authType") or "password").lower()
    password = cfg.get("password") or ""
    errors: list[str] = []

    def try_password() -> bool:
        if not password:
            return False
        try:
            transport.auth_password(username, password)
            return transport.is_authenticated()
        except paramiko.BadAuthenticationType as exc:
            errors.append(f"password: {exc}")
        except paramiko.AuthenticationException as exc:
            errors.append(f"password: {exc}")
        except Exception as exc:
            errors.append(f"password: {exc}")
        return False

    def try_key() -> bool:
        key_text = cfg.get("privateKey") or ""
        key_ref = cfg.get("privateKeyAssetId")
        if not key_text and key_ref:
            errors.append("privateKey: 引用的私钥资产需由上层解析")
            return False
        if not key_text:
            return False
        try:
            pkey = load_private_key(key_text, cfg.get("passphrase"))
            transport.auth_publickey(username, pkey)
            return transport.is_authenticated()
        except Exception as exc:
            errors.append(f"privateKey: {exc}")
        return False

    def try_agent() -> bool:
        sock_path = cfg.get("agentSocket") or os.environ.get("SSH_AUTH_SOCK")
        if not sock_path:
            errors.append("agent: 未配置 agentSocket 且 SSH_AUTH_SOCK 为空")
            return False
        agent = None
        agent_sock = None
        try:
            # paramiko.Agent() only reads SSH_AUTH_SOCK; connect explicitly so a
            # per-asset socket path works without mutating process state.
            from paramiko.agent import AgentSSH

            agent_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            agent_sock.connect(sock_path)
            agent = AgentSSH()
            agent._connect(agent_sock)
            keys = agent.get_keys()
            if not keys:
                errors.append("agent: agent 中没有可用密钥")
            for key in keys:
                try:
                    transport.auth_publickey(username, key)
                except Exception as exc:
                    errors.append(f"agent key {key.get_name()}: {exc}")
                    continue
                if transport.is_authenticated():
                    return True
        except Exception as exc:
            errors.append(f"agent: {exc}")
        finally:
            if agent is not None:
                try:
                    agent._close()
                except Exception:
                    pass
            elif agent_sock is not None:
                try:
                    agent_sock.close()
                except Exception:
                    pass
        return False

    def try_keyboard_interactive() -> bool:
        def handler(title, instructions, prompt_list):
            return [password for _ in prompt_list]

        try:
            transport.auth_interactive(username, handler)
            return transport.is_authenticated()
        except Exception as exc:
            errors.append(f"keyboard-interactive: {exc}")
        return False

    if auth_type == "key":
        ok = try_key() or try_keyboard_interactive() or try_password()
    elif auth_type == "agent":
        ok = try_agent() or try_keyboard_interactive()
    else:
        ok = try_password() or try_keyboard_interactive() or try_key()

    if not ok:
        transport.close()
        if raw_sock:
            raw_sock.close()
        detail = "; ".join(errors) or "无可用认证方式"
        raise SSHError(f"SSH 认证失败（{username}@{host}:{port}）：{detail}")

    try:
        transport.set_keepalive(int(cfg.get("keepalive") or 30))
    except Exception:
        pass
    return transport, state


def connect_chain(
    cfgs: list[dict],
    hostkey_resolver: HostKeyResolver,
    *,
    timeout: float = 15.0,
) -> SSHChain:
    """Connect an ordered chain of configs (outermost jump host -> target).

    Each hop is a full SSH authentication. Only the first (outermost) hop can
    use its own ``proxy``: when a hop has a parent transport, paramiko opens a
    ``direct-tcpip`` channel through it and the proxy field is ignored.

    On any failure every already-open transport is closed before raising.
    """
    configs = [dict(cfg) for cfg in cfgs]
    if not configs:
        raise SSHError("SSH 连接链为空")
    transports: list[paramiko.Transport] = []
    host_states: list[str] = []
    via: paramiko.Transport | None = None
    try:
        for index, cfg in enumerate(configs, start=1):
            host = (cfg.get("host") or "").strip()
            port = int(cfg.get("port") or 22)
            try:
                transport, state = connect_transport(cfg, hostkey_resolver, timeout=timeout, via=via)
            except HostKeyMismatch as exc:
                raise HostKeyMismatch(f"第 {index} 跳（{host}:{port}）主机密钥校验失败：{exc}") from exc
            except Exception as exc:
                raise SSHError(f"第 {index} 跳（{host}:{port}）连接失败：{exc}") from exc
            transports.append(transport)
            host_states.append(state)
            via = transport
    except Exception:
        for transport in reversed(transports):
            try:
                transport.close()
            except Exception:
                pass
        raise
    return SSHChain(configs, transports, host_states)


def connect_client(cfg: dict, resolver: HostKeyResolver) -> tuple[paramiko.Transport, str]:
    """Connect a single transport. Use :func:`connect_chain` for jump hosts."""
    return connect_transport(cfg, resolver)


def exec_on_transport(
    transport: paramiko.Transport,
    command: str,
    timeout: int = 60,
    max_bytes: int = 16 * 1024 * 1024,
) -> dict:
    """Run one command on a dedicated channel of an authenticated transport.

    The interactive shell channel (if any) is untouched, so this is safe to
    call while a terminal session is open.  Output is collected up to
    ``max_bytes`` and flagged ``truncated`` beyond that.  Raises TimeoutError
    when the deadline expires; the caller decides how to report it.
    """
    chan = transport.open_session(timeout=min(30, max(1, timeout)))
    try:
        chan.settimeout(timeout)
        chan.exec_command(command)
        stdout = bytearray()
        stderr = bytearray()
        truncated = False
        deadline = time.time() + timeout
        while True:
            if chan.recv_ready():
                chunk = chan.recv(65536)
                room = max_bytes - len(stdout)
                if room > 0:
                    stdout.extend(chunk[:room])
                if len(chunk) > room:
                    truncated = True
            if chan.recv_stderr_ready():
                chunk = chan.recv_stderr(65536)
                room = max_bytes - len(stderr)
                if room > 0:
                    stderr.extend(chunk[:room])
                if len(chunk) > room:
                    truncated = True
            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                break
            if time.time() > deadline:
                raise TimeoutError(f"命令超时（{timeout}s）")
            time.sleep(0.02)
        return {
            "exitCode": chan.recv_exit_status(),
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
            "truncated": truncated,
        }
    finally:
        try:
            chan.close()
        except Exception:
            pass


class TunnelHandle:
    """A local TCP listener forwarded through an SSH transport (direct-tcpip)."""

    def __init__(self, transport: paramiko.Transport, remote_host: str, remote_port: int) -> None:
        self.transport = transport
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.server: socketserver.ThreadingTCPServer | None = None
        self.thread: threading.Thread | None = None
        self.local_port = 0

    def start(self) -> int:
        target = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    channel = target.transport.open_channel(
                        "direct-tcpip",
                        (target.remote_host, target.remote_port),
                        self.request.getpeername(),
                        timeout=20,
                    )
                except Exception:
                    return
                self._bridge(self.request, channel)

            @staticmethod
            def _bridge(sock: socket.socket, channel: paramiko.Channel) -> None:
                import select

                try:
                    while True:
                        r, _, _ = select.select([sock, channel], [], [], 1.0)
                        if sock in r:
                            data = sock.recv(65536)
                            if not data:
                                break
                            channel.sendall(data)
                        if channel in r:
                            data = channel.recv(65536)
                            if not data:
                                break
                            sock.sendall(data)
                except Exception:
                    pass
                finally:
                    try:
                        channel.close()
                    except Exception:
                        pass

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler, bind_and_activate=True)
        self.server.daemon_threads = True
        self.local_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.local_port

    def stop(self) -> None:
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
        except Exception:
            pass
        try:
            self.transport.close()
        except Exception:
            pass


def flush_known_hosts(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
