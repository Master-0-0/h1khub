"""Standalone SSH tunnel assets: local/remote port forwarding and dynamic SOCKS5.

Each tunnel owns one SSH transport for its lifetime. Tunnels are started and
stopped from the API/UI; they are independent of database/Redis SSH tunnels.
"""
from __future__ import annotations

from collections import deque

import select
import socket
import socketserver
import struct
import threading
import time

import paramiko

from util import new_id, now_iso


class TunnelError(Exception):
    pass


def _bridge(sock: socket.socket, channel) -> None:
    try:
        while True:
            readable, _, _ = select.select([sock, channel], [], [], 1.0)
            if sock in readable:
                data = sock.recv(65536)
                if not data:
                    break
                channel.sendall(data)
            if channel in readable:
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


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("连接已关闭")
        buf += chunk
    return buf


class _BridgeHandler(socketserver.BaseRequestHandler):
    """Local listener -> SSH direct-tcpip channel."""

    def handle(self) -> None:
        target = self.server.tunnel  # type: ignore[attr-defined]
        try:
            channel = target.transport.open_channel(
                "direct-tcpip", (target.target_host, target.target_port), self.request.getpeername(), timeout=20
            )
        except Exception:
            return
        target.record_connection(str(self.request.getpeername()))
        _bridge(self.request, channel)


class _SocksHandler(socketserver.BaseRequestHandler):
    """Minimal SOCKS5 CONNECT server multiplexed over SSH direct-tcpip."""

    def handle(self) -> None:
        target = self.server.tunnel  # type: ignore[attr-defined]
        sock: socket.socket = self.request
        try:
            sock.settimeout(20)
            greeting = _recv_exact(sock, 2)
            if greeting[0] != 5:
                sock.close()
                return
            sock.recv(greeting[1])
            sock.sendall(b"\x05\x00")
            head = _recv_exact(sock, 4)
            if head[1] != 1:  # CONNECT only
                sock.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                sock.close()
                return
            atyp = head[3]
            if atyp == 1:
                host = socket.inet_ntoa(_recv_exact(sock, 4))
            elif atyp == 3:
                length = _recv_exact(sock, 1)[0]
                host = _recv_exact(sock, length).decode("utf-8", "replace")
            elif atyp == 4:
                host = socket.inet_ntop(socket.AF_INET6, _recv_exact(sock, 16))
            else:
                sock.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                sock.close()
                return
            port = struct.unpack(">H", _recv_exact(sock, 2))[0]
            try:
                channel = target.transport.open_channel("direct-tcpip", (host, port), sock.getpeername(), timeout=20)
            except Exception:
                sock.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
                sock.close()
                return
            sock.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            target.record_connection(f"{host}:{port}")
            _bridge(sock, channel)
        except Exception:
            try:
                sock.close()
            except Exception:
                pass


class ManagedTunnel:
    def __init__(self, asset_id: str, name: str, kind: str, cfg: dict) -> None:
        self.id = new_id("tun")
        self.asset_id = asset_id
        self.name = name
        self.kind = kind
        self.cfg = cfg
        self.transport = None
        self.transports: list = []
        self.server = None
        self.thread = None
        self.remote_thread = None
        self.stopped = False
        self.created_at = time.time()
        self.bind_host = cfg.get("bindHost") or "127.0.0.1"
        self.bind_port = int(cfg.get("bindPort") or 0)
        self.target_host = cfg.get("targetHost") or ""
        self.target_port = int(cfg.get("targetPort") or 0)
        self.connections = 0
        self.recent_connections: deque[str] = deque(maxlen=20)
        self.error = ""

    # ------------------------------------------------------------- helpers
    def record_connection(self, detail: str = "") -> None:
        self.connections += 1
        if detail:
            self.recent_connections.append(detail)

    def status(self) -> dict:
        return {
            "id": self.id,
            "assetId": self.asset_id,
            "name": self.name,
            "type": self.kind,
            "bindHost": self.bind_host,
            "bindPort": self.bind_port,
            "targetHost": self.target_host,
            "targetPort": self.target_port,
            "connections": self.connections,
            "recentConnections": list(self.recent_connections)[-10:],
            "running": self.server is not None or self.remote_thread is not None,
            "createdAt": self.created_at,
            "error": self.error,
            "sshAssetId": self.cfg.get("sshAssetId") or "",
        }

    # --------------------------------------------------------------- start
    def start(self, transport) -> dict:
        self.transport = transport
        if not self.transports:
            self.transports = [transport]
        if self.kind == "local":
            if not self.target_host or not self.target_port:
                raise TunnelError("本地转发需要目标主机与端口")
            self._start_local()
        elif self.kind == "dynamic":
            self._start_socks()
        elif self.kind == "remote":
            self._start_remote()
        else:
            raise TunnelError(f"未知隧道类型：{self.kind}")
        return self.status()

    def _start_local(self) -> None:
        tunnel = self

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server((self.bind_host, self.bind_port), _BridgeHandler)
        self.server.tunnel = tunnel  # type: ignore[attr-defined]
        self.bind_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _start_socks(self) -> None:
        tunnel = self

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server((self.bind_host, self.bind_port), _SocksHandler)
        self.server.tunnel = tunnel  # type: ignore[attr-defined]
        self.bind_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _start_remote(self) -> None:
        tunnel = self

        def on_channel(channel, origin, server) -> None:
            tunnel.record_connection(f"{origin[0]}:{origin[1]}")
            try:
                local = socket.create_connection((self.target_host, self.target_port), timeout=15)
            except Exception:
                try:
                    channel.close()
                except Exception:
                    pass
                return
            _bridge(local, channel)
            try:
                local.close()
            except Exception:
                pass

        self.bind_port = self.transport.request_port_forward(self.bind_host, self.bind_port, handler=on_channel)

        def keepalive() -> None:
            while not self.stopped:
                time.sleep(0.5)

        self.remote_thread = threading.Thread(target=keepalive, daemon=True)
        self.remote_thread.start()

    # ---------------------------------------------------------------- stop
    def stop(self) -> None:
        self.stopped = True
        # NOTE: paramiko's cancel_port_forward() waits forever for OpenSSH's
        # global-request reply, so we deliberately skip it and close the
        # transport: that tears the remote listener down reliably.
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None
        for transport in reversed(self.transports):
            try:
                transport.close()
            except Exception:
                pass
        self.transports = []
        self.transport = None


class TunnelManager:
    def __init__(self, hub) -> None:
        self.hub = hub
        self.tunnels: dict[str, ManagedTunnel] = {}

    def list(self) -> list[dict]:
        return [t.status() for t in self.tunnels.values()]

    def get(self, tunnel_id: str) -> ManagedTunnel | None:
        return self.tunnels.get(tunnel_id)

    async def start(self, asset_id: str) -> dict:
        import asyncio

        resolved = self.hub.resolved(asset_id)
        asset = resolved["asset"]
        cfg = dict(resolved["config"] or {})
        if asset["type"] != 20:  # SSHTunnel
            raise TunnelError("资产不是 SSH 隧道")
        ssh_asset_id = cfg.get("sshAssetId")
        if not ssh_asset_id:
            raise TunnelError("请先在隧道资产中选择 SSH 连接")
        kind = (cfg.get("type") or "local").lower()
        # backward compatible field names
        cfg.setdefault("bindHost", cfg.get("localHost") or "127.0.0.1")
        cfg.setdefault("bindPort", cfg.get("localPort") or cfg.get("bindPort") or 0)
        if kind == "local":
            cfg.setdefault("targetHost", cfg.get("remoteHost") or "")
            cfg.setdefault("targetPort", cfg.get("remotePort") or 0)
        elif kind == "remote":
            cfg.setdefault("targetHost", cfg.get("localTargetHost") or "127.0.0.1")
            cfg.setdefault("targetPort", cfg.get("localTargetPort") or 0)
            cfg.setdefault("bindPort", cfg.get("remotePort") or cfg.get("bindPort") or 0)
        tunnel = ManagedTunnel(asset_id, asset["name"], kind, cfg)

        transport, transports = await self.hub.open_ssh_transport(ssh_asset_id)
        tunnel.transports = list(transports)
        try:
            await asyncio.to_thread(tunnel.start, transport)
        except Exception:
            await asyncio.to_thread(tunnel.stop)
            raise
        self.tunnels[tunnel.id] = tunnel
        return tunnel.status()

    async def stop(self, tunnel_id: str) -> bool:
        import asyncio

        tunnel = self.tunnels.pop(tunnel_id, None)
        if not tunnel:
            return False
        await asyncio.to_thread(tunnel.stop)
        return True

    async def stop_all(self) -> None:
        import asyncio

        for tunnel in list(self.tunnels.values()):
            await asyncio.to_thread(tunnel.stop)
        self.tunnels.clear()
