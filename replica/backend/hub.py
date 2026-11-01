"""Service hub: resolves asset configs and opens connections (SSH, DB, Redis, Docker)."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import paramiko

from db import DatabaseError, make_adapter
from docker_svc import DockerError, DockerService
from ftp_svc import FTPClient, FTPError
from redis_svc import RedisError, RedisService
from sessions import SessionManager
from sshlib import HostKeyResolver, SSHChain, SSHError, TunnelHandle, connect_chain, exec_on_transport

# Defensive limit for jump-host recursion (not a performance recommendation).
MAX_JUMP_DEPTH = 8

# Networks with an explicit end-to-end chain are always applied from the
# outermost node inwards: client -> Jn -> ... -> J1 -> target.


# Asset type ids (mirrors the original enum)
DIR = 0
SSH_PRIVATE_KEY = 1
SSH = 2
FTP = 3
DB = 4
QUICK_CMD = 5
DB_QUERY = 6
DOCKER = 7
DOCKER_REGISTRY = 8
SETTING = 9
HOTKEY = 10
QUICK_CMD_GROUP = 11
RDP = 12
VNC = 13
REPOSITORY_SOURCE = 14
REDIS = 15
SHELL = 16
SERIAL = 17
TELNET = 18
SSH_PASSWORD_PAIR = 19
SSH_TUNNEL = 20
LLM_PROVIDER = 21


class TransportChain:
    """Closeable handle for a live SSH chain owned by a caller (e.g. Docker)."""

    def __init__(self, transports: list[paramiko.Transport]) -> None:
        self.transports = list(transports)

    def close(self) -> None:
        for transport in reversed(self.transports):
            try:
                transport.close()
            except Exception:
                pass
        self.transports = []

    def __bool__(self) -> bool:
        return bool(self.transports)


class Tunnel:
    def __init__(
        self,
        addr: tuple[str, int],
        handle: TunnelHandle,
        transports: list[paramiko.Transport],
    ) -> None:
        self.addr = addr
        self.handle = handle
        self.transports = list(transports)
        self.transport = self.transports[-1] if self.transports else None

    def stop(self) -> None:
        try:
            self.handle.stop()
        except Exception:
            pass
        for transport in reversed(self.transports):
            try:
                transport.close()
            except Exception:
                pass
        self.transports = []


class Hub:
    def __init__(self, store, home: str) -> None:
        self.store = store
        self.home = home
        self.known_hosts = os.path.join(home, "known_hosts")
        self.resolver = HostKeyResolver(self.known_hosts)
        self.sessions = SessionManager(self.resolver)

    # ------------------------------------------------------------- assets
    def asset(self, asset_id: str, secrets: bool = True) -> dict:
        asset = self.store.get_asset(asset_id, include_secrets=secrets)
        if not asset:
            raise ValueError(f"资产不存在：{asset_id}")
        return asset

    def resolved(self, asset_id: str) -> dict:
        asset = self.asset(asset_id, secrets=True)
        cfg = dict(asset.get("config") or {})
        # resolve referenced private key asset
        key_ref = cfg.get("privateKeyAssetId")
        if key_ref:
            try:
                key_asset = self.asset(key_ref, secrets=True)
                key_cfg = key_asset.get("config") or {}
                cfg.setdefault("privateKey", key_cfg.get("privateKey") or "")
                if not cfg.get("passphrase"):
                    cfg["passphrase"] = key_cfg.get("passphrase") or ""
            except ValueError:
                pass
        # resolve password pair asset
        pair_ref = cfg.get("passwordPairAssetId")
        if pair_ref:
            try:
                pair = self.asset(pair_ref, secrets=True).get("config") or {}
                cfg.setdefault("password", pair.get("password") or "")
            except ValueError:
                pass
        return {"asset": asset, "config": cfg}

    def _asset_label(self, asset_id: str) -> str:
        try:
            asset = self.store.get_asset(asset_id, include_secrets=False)
        except Exception:
            asset = None
        return (asset or {}).get("name") or asset_id

    @staticmethod
    def _normalize_hop(cfg: dict) -> None:
        cfg.pop("__jump", None)
        cfg.pop("__jump_chain", None)
        cfg.setdefault("username", "root")
        cfg.setdefault("port", 22)

    def _chain_from(self, start_cfg: dict, start_id: str | None = None) -> list[dict]:
        """Resolve a target config plus its recursive jumpAssetId chain.

        Returns configs ordered outermost jump host -> target. Cycle/depth
        errors are raised before any network connection is attempted.
        """
        chain: list[dict] = []
        seen: set[str] = set()
        labels: list[str] = []
        current = dict(start_cfg)
        current_id = start_id
        while True:
            self._normalize_hop(current)
            jump_id = (current.get("jumpAssetId") or "").strip()
            current.pop("jumpAssetId", None)
            chain.append(current)
            if current_id:
                seen.add(current_id)
                labels.append(self._asset_label(current_id))
            if not jump_id:
                break
            if jump_id in seen:
                labels.append(self._asset_label(jump_id))
                raise SSHError("跳板链存在循环：" + " -> ".join(labels))
            if len(chain) - 1 >= MAX_JUMP_DEPTH:
                raise SSHError(f"跳板层数超过上限（{MAX_JUMP_DEPTH}）")
            try:
                resolved = self.resolved(jump_id)
            except ValueError as exc:
                raise SSHError(f"跳板资产不可用：{exc}") from exc
            current = resolved["config"]
            current_id = jump_id
        chain.reverse()
        return chain

    def ssh_chain(self, asset_id: str) -> list[dict]:
        """Return configs ordered outermost jump host -> target."""
        resolved = self.resolved(asset_id)
        return self._chain_from(resolved["config"], asset_id)

    def ssh_chain_from_config(self, cfg: dict, asset_id: str | None = None) -> list[dict]:
        """Build a chain from an inline/unsaved target config."""
        return self._chain_from(cfg, asset_id)

    def ssh_config(self, asset_id: str) -> dict:
        """Target config; outer hops are exposed as an internal __jump_chain list."""
        chain = self.ssh_chain(asset_id)
        cfg = dict(chain[-1])
        if len(chain) > 1:
            cfg["__jump_chain"] = [dict(hop) for hop in chain[:-1]]
        return cfg

    # ------------------------------------------------------- chain opening
    async def open_ssh_chain(self, asset_id: str) -> SSHChain:
        chain_cfgs = self.ssh_chain(asset_id)
        return await asyncio.to_thread(connect_chain, chain_cfgs, self.resolver)

    async def open_ssh_chain_from_config(self, cfg: dict, asset_id: str | None = None) -> SSHChain:
        chain_cfgs = self.ssh_chain_from_config(cfg, asset_id)
        return await asyncio.to_thread(connect_chain, chain_cfgs, self.resolver)

    # ------------------------------------------------------------ tunnels
    def _open_tunnel_sync(self, chain_cfgs: list[dict], host: str, port: int) -> Tunnel:
        chain = connect_chain(chain_cfgs, self.resolver)
        try:
            handle = TunnelHandle(chain.transport, host, port)
            local_port = handle.start()
        except Exception:
            chain.close()
            raise
        return Tunnel(("127.0.0.1", local_port), handle, chain.transports)

    async def tunnel(self, ssh_asset_id: str, host: str, port: int) -> Tunnel:
        chain_cfgs = self.ssh_chain(ssh_asset_id)
        return await asyncio.to_thread(self._open_tunnel_sync, chain_cfgs, host, port)

    @asynccontextmanager
    async def sftp(self, asset_id: str):
        resolved = self.resolved(asset_id)
        asset = resolved["asset"]
        if asset["type"] not in (SSH, FTP):
            raise ValueError("该资产不是 SSH/SFTP 连接")
        cfg = resolved["config"]
        if asset["type"] == FTP and (cfg.get("protocol") or "sftp") == "ftp":
            raise ValueError("FTP 协议暂未实现，请使用 SFTP")
        chain = await self.open_ssh_chain(asset_id)
        try:
            sftp = await asyncio.to_thread(paramiko.SFTPClient.from_transport, chain.transport)
            try:
                sftp.get_channel().settimeout(30)
                yield sftp
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
        finally:
            chain.close()

    # ------------------------------------------------------------ scp/exec
    async def open_ssh_transport(self, asset_id: str):
        """Open the target transport plus every outer hop (ownership transferred)."""
        chain = await self.open_ssh_chain(asset_id)
        return chain.transport, chain.transports

    @asynccontextmanager
    async def ssh_transport(self, asset_id: str):
        """Open a raw SSH transport (with jump-host support) for SCP work."""
        chain = await self.open_ssh_chain(asset_id)
        try:
            yield chain.transport
        finally:
            chain.close()

    # ---------------------------------------------------------------- ftp
    async def open_ftp(self, asset_id: str) -> FTPClient:
        resolved = self.resolved(asset_id)
        asset = resolved["asset"]
        cfg = resolved["config"]
        if asset["type"] != FTP:
            raise FTPError("资产不是 FTP 连接")
        protocol = (cfg.get("protocol") or "sftp").lower()
        if protocol == "sftp":
            raise FTPError("该资产使用 SFTP，请通过 SSH 会话打开文件面板")
        client = FTPClient(cfg)
        await asyncio.to_thread(client.connect)
        return client

    # ---------------------------------------------------------------- db
    async def open_db(self, asset_id: str):
        resolved = self.resolved(asset_id)
        cfg = resolved["config"]
        asset = resolved["asset"]
        if asset["type"] != DB:
            raise DatabaseError("资产不是数据库连接")
        adapter = make_adapter(cfg)
        tunnel = None
        if cfg.get("sshAssetId") and cfg.get("dbType") not in ("sqlite", "duckdb"):
            host = cfg.get("host") or "127.0.0.1"
            port = int(cfg.get("port") or adapter.default_port)
            tunnel = await self.tunnel(cfg["sshAssetId"], host, port)
            adapter.tunnel_addr = tunnel.addr
            adapter.tunnel_handle = tunnel
        try:
            await asyncio.to_thread(adapter.connect, cfg.get("database") or None)
        except Exception:
            if tunnel:
                tunnel.stop()
            raise
        return adapter

    # -------------------------------------------------------------- redis
    async def open_redis(self, asset_id: str):
        resolved = self.resolved(asset_id)
        cfg = resolved["config"]
        asset = resolved["asset"]
        if asset["type"] != REDIS:
            raise RedisError("资产不是 Redis 连接")
        tunnel = None
        if cfg.get("sshAssetId"):
            host = cfg.get("host") or "127.0.0.1"
            port = int(cfg.get("port") or 6379)
            tunnel = await self.tunnel(cfg["sshAssetId"], host, port)
        try:
            svc = await asyncio.to_thread(RedisService, cfg, tunnel.addr if tunnel else None)
        except Exception:
            if tunnel:
                tunnel.stop()
            raise
        return svc, tunnel

    # ------------------------------------------------------------- docker
    async def docker(self, asset_id: str | None):
        transport_chain = None
        remote_exec = None
        cfg: dict = {"mode": "local", "socketPath": "/var/run/docker.sock"}
        if asset_id:
            resolved = self.resolved(asset_id)
            cfg = resolved["config"] or cfg
            asset = resolved["asset"]
            if asset["type"] != DOCKER:
                raise DockerError("资产不是 Docker 连接")
            if (cfg.get("mode") or "local") == "ssh" and cfg.get("sshAssetId"):
                transport_chain = await self.open_ssh_chain(cfg["sshAssetId"])
                ssh_transport = transport_chain.transport

                def remote_exec(cmd: str):
                    chan = ssh_transport.open_session(timeout=30)
                    try:
                        chan.settimeout(120)
                        chan.exec_command(cmd)
                        out = bytearray()
                        err = bytearray()
                        deadline = time.time() + 600
                        while True:
                            if chan.recv_ready():
                                out.extend(chan.recv(65536))
                            if chan.recv_stderr_ready():
                                err.extend(chan.recv_stderr(65536))
                            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                                break
                            if time.time() > deadline:
                                raise DockerError("远程 docker 命令超时")
                            time.sleep(0.02)
                        return out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
                    finally:
                        try:
                            chan.close()
                        except Exception:
                            pass

        try:
            service = DockerService(cfg, ssh_exec=remote_exec)
        except Exception:
            if transport_chain:
                transport_chain.close()
            raise
        return service, TransportChain(transport_chain.transports) if transport_chain else None

    # ----------------------------------------------------------- remote exec
    async def ssh_exec(self, asset_id: str, command: str, timeout: int = 60) -> dict:
        """Run a command on a remote host over SSH (blocking I/O off the loop)."""
        chain_cfgs = self.ssh_chain(asset_id)

        def run() -> dict:
            chain = connect_chain(chain_cfgs, self.resolver)
            try:
                return exec_on_transport(chain.transport, command, timeout)
            finally:
                chain.close()

        return await asyncio.to_thread(run)

    # --------------------------------------------------------------- sfpt
    def ftp_supported(self, asset: dict) -> bool:
        cfg = asset.get("config") or {}
        return asset["type"] == SSH or cfg.get("protocol") == "sftp"

    async def close(self) -> None:
        await self.sessions.close_all()
