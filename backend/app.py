"""H1KHub replica backend: local HTTP + WebSocket RPC server."""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import logging
import mimetypes
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import sys
import tempfile
import time
import webbrowser
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import paramiko
from aiohttp import WSMsgType, web

import fsops
import sshlib
import agent_backup
import ai
import archive_svc
import assetmap_svc
import capabilities
from ai import AIError, ChatRunner, build_system_prompt, provider_urls
from db import DatabaseError, is_transient_reset, make_adapter
import dbsessions
import dbsync
import key_svc
import lock_svc
import rpcproto
import tasks as task_svc
import transfer_svc
import trzsz_svc
import update_svc
import registry_svc
from ftp_svc import FTPClient, FTPError
from scp_svc import SCPClient, SCPError
from transfer_svc import TransferError
from tunnel_svc import TunnelError, TunnelManager
from chrome_proxy_svc import ChromeProxyError, ChromeProxyManager
from docker_svc import DockerError
from hub import Hub
from redis_svc import RedisError, RedisService
from sessions import (
    LocalShellSession,
    PTYCommandSession,
    SSHSession,
    SerialSession,
    SessionError,
    TelnetSession,
)
from store import ASSET_TYPES, Store
from util import APP_BUILD, APP_NAME, APP_VERSION, clamp_text, human_bytes, new_id, now_iso, safe_filename
from util import b64decode_str, strip_ansi

log = logging.getLogger("h1khub")

# Legacy data directory of builds released before the H1KHub rebrand.  It is
# only consulted to move an existing install once; nothing is written to it.
LEGACY_HOME_DIR = "~/.hexhub-replica"


def _default_home() -> str:
    override = os.environ.get("H1KHUB_HOME")
    if override:
        return os.path.expanduser(override)
    home = os.path.expanduser("~/.h1khub")
    legacy = os.path.expanduser(LEGACY_HOME_DIR)
    if not os.path.exists(home) and os.path.isdir(legacy):
        try:
            os.rename(legacy, home)
            print(f"migrated data directory {legacy} -> {home}", file=sys.stderr)
        except OSError:
            return legacy
    return home


HOME = _default_home()
DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "dist")
DIST_DIR = os.path.abspath(DIST_DIR)
PUBLIC_PATHS = {"/api/public/info", "/api/auth/token"}

REGISTRY = registry_svc.LocalRegistry(HOME)

RPC_PACKET_VERSION = 1


def json_response(data: Any, status: int = 200, **kwargs) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=str), **kwargs)


class AppState:
    def __init__(self) -> None:
        self.store: Store
        self.hub: Hub
        self.token: str = ""
        self.port: int = 0
        self.host: str = "127.0.0.1"
        self.started_at = now_iso()
        self.chat_runners: dict[str, ChatRunner] = {}
        self.dbsessions: dbsessions.DbSessionManager
        self.tunnels: TunnelManager
        self.chrome_proxy: ChromeProxyManager
        self.tasks: task_svc.TaskManager
        self.lock: lock_svc.LockService
        self.db_pids: dict[str, int] = {}


STATE = AppState()


def token_of(request: web.Request) -> str:
    token = request.headers.get("X-H1KHub-Token") or request.query.get("token") or ""
    if not token:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    return token


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if request.method == "OPTIONS":
        return web.Response(status=204)
    if path.startswith("/api/"):
        if path in PUBLIC_PATHS:
            return await handler(request)
        token = token_of(request)
        if not token or not secrets.compare_digest(token, STATE.token):
            return web.Response(status=401, text="<h1>session invalid</h1>", content_type="text/html")
    return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        response = await handler(request)
        return response
    except web.HTTPException:
        raise
    except (DatabaseError, RedisError, DockerError, SessionError, fsops.FileOpError, sshlib.SSHError, FTPError, SCPError, TunnelError, TransferError, ChromeProxyError, key_svc.KeyError_, archive_svc.ArchiveError) as exc:
        return json_response({"error": str(exc)}, status=400)
    except (ValueError, KeyError) as exc:
        return json_response({"error": str(exc)}, status=400)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("unhandled error on %s", request.path)
        return json_response({"error": f"内部错误：{type(exc).__name__}: {exc}"}, status=500)


def ok(data: Any = None, **extra) -> web.Response:
    payload: dict[str, Any] = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return json_response(payload)


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ public
PROTO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "proto-recovered"
)


async def proto_file(request: web.Request) -> web.Response:
    """Serve the protobuf schemas recovered from the original app (as it does)."""
    name = request.match_info.get("name") or ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.proto", name):
        raise web.HTTPNotFound()
    path = os.path.join(PROTO_DIR, name)
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "text/plain; charset=utf-8"})


async def public_info(request: web.Request) -> web.Response:
    return ok(
        {
            "name": APP_NAME,
            "version": APP_VERSION,
            "build": APP_BUILD,
            "os": platform.system(),
            "arch": platform.machine(),
            "hostname": socket.gethostname(),
            "port": STATE.port,
            "useIpc": False,
            "transport": "websocket",
            "startedAt": STATE.started_at,
            "dataHome": HOME,
        }
    )


# ------------------------------------------------- reserved capabilities (r21)
# The registry is the single source of truth for implemented, reserved and
# unavailable capabilities.  Every placeholder route answers HTTP 501 with the
# machine-readable FEATURE_RESERVED body and never performs work.  Round 23
# implemented update.check/update.channel, so the remaining reserved settings
# keys (plugins/mcp/cloud) still have no gate and cannot activate anything.
async def capabilities_list(request: web.Request) -> web.Response:
    return ok({"capabilities": capabilities.capabilities_public()})


def update_error(exc: "update_svc.UpdateError", feature: str) -> web.Response:
    """Uniform update error envelope (HTTP 400, never a 500)."""
    docs = update_svc.DOCS_CHECK if feature == "update.check" else update_svc.DOCS_CHANNEL
    return json_response(
        {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "feature": feature,
                "docs": docs,
            },
        },
        status=exc.status,
    )


async def update_check(request: web.Request) -> web.Response:
    """Read the local manifest and compare it with the running version.

    Read-only: no download, no execution, no asset-store write, no cloud call.
    """
    try:
        return ok(await update_svc.check(STATE.store.get_settings()))
    except update_svc.UpdateError as exc:
        return update_error(exc, "update.check")


async def update_start(request: web.Request) -> web.Response:
    return capabilities.reserved_response("update.hot")


async def update_status(request: web.Request) -> web.Response:
    return capabilities.reserved_response("update.hot")


async def update_channel(request: web.Request) -> web.Response:
    """Persist stable/experimental + production/test; never downloads."""
    data = await body(request)
    try:
        return ok(update_svc.set_channel(STATE.store, data))
    except update_svc.UpdateError as exc:
        return update_error(exc, "update.channel")


# ------------------------------------------------------------ screen lock (r22)
# The password hash lives in settings (stripped from every HTTP response), the
# lock state lives in this process, and idle expiry is evaluated server-side so
# a refresh or a new tab cannot bypass the overlay.  Errors keep the uniform
# {"ok": false, "error": {...}} envelope.
async def lock_status(request: web.Request) -> web.Response:
    return ok(STATE.lock.status())


async def lock_config(request: web.Request) -> web.Response:
    data = await body(request)
    try:
        return ok(
            STATE.lock.configure(
                enabled=data.get("enabled"),
                idle_minutes=data.get("idleMinutes"),
            )
        )
    except lock_svc.LockError as exc:
        return lock_error(exc)


async def lock_password(request: web.Request) -> web.Response:
    data = await body(request)
    try:
        return ok(
            STATE.lock.set_password(
                str(data.get("oldPassword") or ""),
                data.get("newPassword") or "",
            )
        )
    except lock_svc.LockError as exc:
        return lock_error(exc)


async def lock_password_clear(request: web.Request) -> web.Response:
    data = await body(request)
    try:
        return ok(STATE.lock.clear_password(str(data.get("oldPassword") or "")))
    except lock_svc.LockError as exc:
        return lock_error(exc)


async def lock_now(request: web.Request) -> web.Response:
    try:
        return ok(STATE.lock.lock_now())
    except lock_svc.LockError as exc:
        return lock_error(exc)


async def lock_verify(request: web.Request) -> web.Response:
    data = await body(request)
    try:
        return ok(STATE.lock.verify(str(data.get("password") or "")))
    except lock_svc.LockError as exc:
        return lock_error(exc)


async def lock_activity(request: web.Request) -> web.Response:
    return ok(STATE.lock.activity())


def lock_error(exc: lock_svc.LockError) -> web.Response:
    error: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
        "feature": "lock.screen",
        "docs": "docs/reserved-slots.md#90-锁屏与托盘",
    }
    error.update(exc.details)
    return json_response({"ok": False, "error": error}, status=exc.status)


async def mcp_info(request: web.Request) -> web.Response:
    return capabilities.reserved_response("ext.mcp")


async def plugins_list(request: web.Request) -> web.Response:
    return capabilities.reserved_response("ext.plugins")


async def issue_token(request: web.Request) -> web.Response:
    # Loopback-only: the SPA served by this process needs a session token.
    peer = request.remote
    if peer not in ("127.0.0.1", "::1", "localhost"):
        return json_response({"error": "forbidden"}, status=403)
    return ok({"token": STATE.token, "expiresIn": 0})


# ------------------------------------------------------------------ assets
async def assets_list(request: web.Request) -> web.Response:
    assets = STATE.store.list_assets()
    return ok({"assets": assets, "types": ASSET_TYPES})


async def asset_map(request: web.Request) -> web.Response:
    """Read-only topology for the asset map view (connection assets only)."""
    return ok(assetmap_svc.build_asset_map(STATE.store.list_assets()))


async def assets_create(request: web.Request) -> web.Response:
    data = await body(request)
    type_name = data.get("typeName") or "Dir"
    type_id = ASSET_TYPES.get(type_name)
    if type_id is None:
        type_id = int(data.get("type") or 0)
    name = (data.get("name") or "").strip()
    if not name:
        return json_response({"error": "名称不能为空"}, status=400)
    parent = data.get("parentId") or None
    if parent and not STATE.store.get_asset(parent):
        return json_response({"error": "父目录不存在"}, status=400)
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    asset = STATE.store.create_asset(type_id, name, parent, config, data.get("color"), data.get("alias"))
    return ok({"asset": asset})


async def assets_update(request: web.Request) -> web.Response:
    asset_id = request.match_info["asset_id"]
    data = await body(request)
    if data.get("parentId") == asset_id:
        return json_response({"error": "不能把资产移动到自身下面"}, status=400)
    asset = STATE.store.update_asset(asset_id, data)
    if not asset:
        return json_response({"error": "资产不存在"}, status=404)
    return ok({"asset": asset})


async def assets_delete(request: web.Request) -> web.Response:
    asset_id = request.match_info["asset_id"]
    count = STATE.store.delete_asset(asset_id)
    return ok({"deleted": count})


async def assets_duplicate(request: web.Request) -> web.Response:
    asset_id = request.match_info["asset_id"]
    asset = STATE.store.duplicate_asset(asset_id)
    if not asset:
        return json_response({"error": "资产不存在"}, status=404)
    return ok({"asset": asset})


async def assets_move(request: web.Request) -> web.Response:
    data = await body(request)
    STATE.store.move_asset(request.match_info["asset_id"], data.get("parentId") or None, data.get("beforeId"))
    return ok()


async def assets_export(request: web.Request) -> web.Response:
    ids = request.query.get("ids")
    payload = STATE.store.export_assets(ids.split(",") if ids else None)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return web.Response(
        text=text,
        content_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename("h1khub-assets.json")}"'},
    )


async def assets_import(request: web.Request) -> web.Response:
    data = await body(request)
    mode = data.get("mode") or "merge"
    payload = data.get("payload") or data
    result = STATE.store.import_assets(payload, mode)
    return ok(result)


# --------------------------------------------------------------- connections
async def asset_config_for_test(request: web.Request) -> tuple[dict, str, int, str | None]:
    """Returns (config, typeName, typeId, assetId) for saved assets or inline config."""
    data = await body(request)
    asset_id = data.get("assetId")
    if asset_id:
        resolved = STATE.hub.resolved(asset_id)
        asset = resolved["asset"]
        cfg = resolved["config"]
        inline = data.get("config")
        if isinstance(inline, dict) and inline:
            # merge in unsaved edits, preserving stored secrets when fields are blank
            cfg = STATE.store._merge_config(cfg, inline)
        return cfg, asset["typeName"], asset["type"], asset_id
    cfg = data.get("config") or {}
    type_name = data.get("typeName") or data.get("type") or "SSH"
    if isinstance(type_name, int):
        from store import TYPE_NAME

        type_name = TYPE_NAME.get(type_name, "SSH")
    return cfg, str(type_name), ASSET_TYPES.get(str(type_name), 0), None


async def test_connection(request: web.Request) -> web.Response:
    cfg, type_name, type_id, asset_id = await asset_config_for_test(request)
    try:
        if type_id == 3:
            protocol = (cfg.get("protocol") or "sftp").lower()
            if protocol != "sftp":
                client = FTPClient(cfg)
                try:
                    info = await asyncio.to_thread(client.test)
                finally:
                    await asyncio.to_thread(client.close)
                return ok(
                    {
                        "message": f"连接成功 · {info.get('protocol', 'ftp').upper()}",
                        "home": info.get("home"),
                        **info,
                    }
                )
            chain = await STATE.hub.open_ssh_chain_from_config(cfg, asset_id)
            try:
                sftp = await asyncio.to_thread(paramiko.SFTPClient.from_transport, chain.transport)
                try:
                    home = await asyncio.to_thread(sftp.normalize, ".")
                finally:
                    sftp.close()
                return ok({"message": "SFTP 连接成功", "home": home, "chain": chain.hops()})
            finally:
                chain.close()
        if type_id == 2:
            chain = await STATE.hub.open_ssh_chain_from_config(cfg, asset_id)
            try:
                hops = chain.hops()
                for hop, transport in zip(hops, chain.transports):
                    try:
                        hop["banner"] = transport.get_banner().decode("utf-8", "replace").strip()
                    except Exception:
                        hop["banner"] = ""
                banner = hops[-1]["banner"] if hops else ""
                return ok(
                    {
                        "message": "连接成功",
                        "hostKey": chain.target_state,
                        "banner": banner,
                        "chain": hops,
                    }
                )
            finally:
                chain.close()
        if type_id == 4:
            adapter = make_adapter(cfg)
            info = await asyncio.to_thread(adapter.test)
            adapter.close()
            return ok({"message": f"连接成功 · {info.get('version')}", **info})
        if type_id == 15:
            svc = await asyncio.to_thread(RedisService, cfg, None)
            try:
                info = await asyncio.to_thread(svc.test)
            finally:
                svc.close()
            return ok({"message": f"连接成功 · Redis {info.get('version')}", **info})
        if type_id == 7:
            from docker_svc import DockerService

            service = DockerService(cfg)
            info = await asyncio.to_thread(service.test)
            return ok({"message": f"连接成功 · Docker {info.get('version')}", **info})
        if type_id == 16:
            shell = (cfg.get("shell") or os.environ.get("SHELL") or "/bin/bash").strip()
            if not os.path.exists(shell):
                raise ValueError(f"Shell 不存在：{shell}")
            return ok({"message": "Shell 可用", "shell": shell})
        if type_id == 13:  # VNC
            host = cfg.get("host") or "127.0.0.1"
            port = int(cfg.get("port") or 5900)
            await asyncio.open_connection(host, port)
            return ok({"message": f"VNC 端口可达 · {host}:{port}", "host": host, "port": port})
        if type_id == 17:
            port = (cfg.get("port") or cfg.get("device") or "").strip()
            if not port:
                raise ValueError("串口设备路径为空")
            import serial

            ser = await asyncio.to_thread(serial.Serial, port, int(cfg.get("baudRate") or 9600), timeout=1)
            try:
                ser.close()
            except Exception:
                pass
            return ok({"message": f"串口可用 · {port}", "port": port, "baudRate": int(cfg.get("baudRate") or 9600)})
        if type_id == 18:
            host = cfg.get("host") or "127.0.0.1"
            port = int(cfg.get("port") or 23)
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
            writer.close()
            return ok({"message": "Telnet 连接成功"})
        raise ValueError(f"{type_name} 暂不支持测试连接")
    except Exception as exc:
        return json_response({"error": str(exc)}, status=400)


# ---------------------------------------------------------------- sessions
async def session_start(request: web.Request) -> web.Response:
    data = await body(request)
    cols = int(data.get("cols") or 120)
    rows = int(data.get("rows") or 32)
    asset_id = data.get("assetId")
    kind = data.get("kind")
    title = data.get("title") or "终端"

    if kind == "docker-exec":
        container = (data.get("container") or "").strip()
        if not container:
            return json_response({"error": "缺少容器 ID"}, status=400)
        docker_asset = data.get("assetId")
        mode = "local"
        socket_path = "/var/run/docker.sock"
        docker_host = None
        if docker_asset:
            resolved = STATE.hub.resolved(docker_asset)
            dcfg = resolved["config"]
            mode = (dcfg.get("mode") or "local").lower()
            socket_path = dcfg.get("socketPath") or socket_path
            if mode == "tcp":
                docker_host = f"tcp://{dcfg.get('host') or '127.0.0.1'}:{int(dcfg.get('port') or 2375)}"
            if mode == "ssh":
                ssh_id = dcfg.get("sshAssetId")
                if not ssh_id:
                    return json_response({"error": "远程 Docker 资产缺少 SSH 连接配置"}, status=400)
                ssh_cfg = STATE.hub.ssh_config(ssh_id)
                ssh_cfg["initCommand"] = f"docker exec -it {shlex.quote(container)} sh"
                session = SSHSession(f"{title} · {container[:12]}", ssh_cfg, STATE.hub.resolver, docker_asset, cols, rows)
                session.meta["remoteDocker"] = True
                session.title = f"容器 {container[:12]}（远程）"
                STATE.hub.sessions.register(session)
                try:
                    await session.start()
                except Exception as exc:
                    STATE.hub.sessions.sessions.pop(session.id, None)
                    return json_response({"error": str(exc)}, status=400)
                return ok({"session": session.info()})
        session = PTYCommandSession(
            f"{title} · {container[:12]}",
            ["docker", "exec", "-it", container, "/bin/sh", "-c", "command -v bash >/dev/null && exec bash || exec sh"],
            env={"DOCKER_HOST": docker_host or f"unix://{socket_path}", "TERM": "xterm-256color"},
            cols=cols,
            rows=rows,
        )
        session.title = f"容器 {container[:12]}"
        STATE.hub.sessions.register(session)
        try:
            await session.start()
        except Exception as exc:
            STATE.hub.sessions.sessions.pop(session.id, None)
            return json_response({"error": str(exc)}, status=400)
        return ok({"session": session.info()})

    if not asset_id:
        # ad-hoc local shell
        session = LocalShellSession(title or "本地终端", data.get("config") or {}, None, cols, rows)
        STATE.hub.sessions.register(session)
        await session.start()
        return ok({"session": session.info()})

    asset = STATE.store.get_asset(asset_id)
    if not asset:
        return json_response({"error": "资产不存在"}, status=404)
    type_id = asset["type"]
    if type_id == 16:  # Shell
        cfg = STATE.hub.resolved(asset_id)["config"]
        session = LocalShellSession(asset["name"], cfg, asset_id, cols, rows)
    elif type_id in (2, 3):  # SSH / SFTP
        cfg = STATE.hub.ssh_config(asset_id)
        session = SSHSession(asset["name"], cfg, STATE.hub.resolver, asset_id, cols, rows)
    elif type_id == 17:  # Serial
        cfg = STATE.hub.resolved(asset_id)["config"]
        session = SerialSession(asset["name"], cfg, asset_id, cols, rows)
    elif type_id == 18:  # Telnet
        cfg = STATE.hub.resolved(asset_id)["config"]
        session = TelnetSession(asset["name"], cfg, asset_id, cols, rows)
    elif type_id == 7:  # Docker -> local shell in container handled separately
        return json_response({"error": "请通过容器列表打开容器终端"}, status=400)
    else:
        return json_response({"error": "该资产不支持终端会话"}, status=400)
    STATE.hub.sessions.register(session)
    try:
        await session.start()
    except Exception as exc:
        STATE.hub.sessions.sessions.pop(session.id, None)
        await session.close()
        return json_response({"error": str(exc)}, status=400)
    STATE.store.add_history("open", asset["name"], asset_id, session.kind)
    return ok({"session": session.info()})


async def sessions_list(request: web.Request) -> web.Response:
    return ok({"sessions": STATE.hub.sessions.list()})


def _save_session_log(session) -> str | None:
    try:
        term = (STATE.store.get_settings().get("terminal") or {})
    except Exception:
        return None
    if not term.get("logSessions"):
        return None
    try:
        os.makedirs(os.path.join(HOME, "logs"), mode=0o700, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_filename(session.title)}-{session.id}.log"
        path = os.path.join(HOME, "logs", name)
        text = strip_ansi(bytes(session.buffer).decode("utf-8", "replace"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o600)
        return path
    except Exception:
        log.exception("failed to save session log")
        return None


async def session_close(request: web.Request) -> web.Response:
    sid = request.match_info["session_id"]
    session = STATE.hub.sessions.get(sid)
    closed = await STATE.hub.sessions.close(sid)
    path = _save_session_log(session) if (closed and session) else None
    return ok({"closed": closed, "logPath": path})


async def session_logs_list(request: web.Request) -> web.Response:
    logs_dir = os.path.join(HOME, "logs")
    items = []
    try:
        for name in sorted(os.listdir(logs_dir), reverse=True)[:200]:
            path = os.path.join(logs_dir, name)
            if not os.path.isfile(path):
                continue
            items.append({"name": name, "size": os.path.getsize(path), "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(path)))})
    except OSError:
        pass
    return ok({"logs": items, "dir": logs_dir})


async def session_log_download(request: web.Request) -> web.StreamResponse:
    name = os.path.basename(request.match_info["name"])
    path = os.path.join(HOME, "logs", name)
    if not os.path.isfile(path):
        return json_response({"error": "日志不存在"}, status=404)
    return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{safe_filename(name)}"', "Content-Type": "text/plain; charset=utf-8"})


async def session_read(request: web.Request) -> web.Response:
    sid = request.match_info["session_id"]
    session = STATE.hub.sessions.get(sid)
    if not session:
        return json_response({"error": "会话不存在"}, status=404)
    data = await body(request)
    text = bytes(session.buffer).decode("utf-8", "replace")
    lines = int(data.get("lines") or 200)
    tail = "\n".join(text.splitlines()[-lines:])
    return ok({"session": session.info(), "content": clamp_text(tail, 200_000), "bytes": len(session.buffer)})


async def session_input(request: web.Request) -> web.Response:
    data = await body(request)
    session = STATE.hub.sessions.get(request.match_info["session_id"])
    if not session:
        return json_response({"error": "会话不存在"}, status=404)
    raw = data.get("data") or ""
    payload = b64decode_str(raw) if data.get("encoding") == "base64" else str(raw).encode()
    await session.write(payload)
    return ok()


# ------------------------------------------------------------- trzsz transfer
def _trzsz_session(request: web.Request) -> SSHSession:
    sid = request.query.get("sessionId") or ""
    session = STATE.hub.sessions.get(sid)
    if not isinstance(session, SSHSession):
        raise trzsz_svc.TrzszError("该会话不是已连接的 SSH 会话")
    if not session.transport or session.closed:
        raise trzsz_svc.TrzszError("SSH 会话未连接")
    return session


def _safe_remote_name(name: str) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if not base or base in (".", ".."):
        raise trzsz_svc.TrzszError("文件名无效")
    if any(ch in base for ch in ("\x00", "\n", "\r")):
        raise trzsz_svc.TrzszError("文件名包含非法字符")
    return base


async def terminal_trzsz_upload(request: web.Request) -> web.Response:
    """Upload one local file to the SSH host through the real trzsz protocol."""
    try:
        session = _trzsz_session(request)
        name = _safe_remote_name(request.query.get("name") or "")
        dest = (request.query.get("dest") or ".").strip() or "."
        raw = await request.read()
        if len(raw) > 512 * 1024 * 1024:
            return json_response({"error": "文件超过 512 MiB 上限"}, status=413)
        result = await asyncio.to_thread(
            trzsz_svc.TrzszClient(session.transport).upload, dest, [(name, raw)]
        )
        result["name"] = name
        result["dest"] = dest
        result["size"] = len(raw)
        return ok(result)
    except trzsz_svc.TrzszError as exc:
        return json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("trzsz upload failed")
        return json_response({"error": f"trzsz 上传失败：{exc}"}, status=500)


async def terminal_trzsz_download(request: web.Request) -> web.StreamResponse:
    """Download one remote file through trzsz and stream it back to the browser."""
    data = await body(request)
    sid = data.get("sessionId") or ""
    session = STATE.hub.sessions.get(sid)
    if not isinstance(session, SSHSession) or not session.transport or session.closed:
        return json_response({"error": "该会话不是已连接的 SSH 会话"}, status=400)
    path = (data.get("path") or "").strip()
    if not path:
        return json_response({"error": "远端路径不能为空"}, status=400)
    tmp = tempfile.mkdtemp(prefix="h1khub-trzsz-")
    try:
        result = await asyncio.to_thread(
            trzsz_svc.download_to_dir, session.transport, [path], tmp
        )
        saved = result.get("saved") or []
        if not saved:
            return json_response({"error": "远端没有返回文件"}, status=400)
        item = saved[0]
        if item["size"] > 512 * 1024 * 1024:
            return json_response({"error": "文件超过 512 MiB 上限"}, status=413)
        with open(item["path"], "rb") as fh:
            payload = fh.read()
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(item['name'])}",
            "X-Trzsz-Message": quote(result.get("message") or ""),
            "X-Trzsz-Filename": quote(item["name"]),
        }
        return web.Response(body=payload, headers=headers)
    except trzsz_svc.TrzszError as exc:
        return json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("trzsz download failed")
        return json_response({"error": f"trzsz 下载失败：{exc}"}, status=500)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------- sftp/local
def _session_sftp(request: web.Request):
    data = request.get("json_body") or {}
    raise NotImplementedError


async def sftp_session(request: web.Request) -> SSHSession:
    data = await body(request)
    sid = data.get("sessionId")
    session = STATE.hub.sessions.get(sid or "")
    if not isinstance(session, SSHSession):
        raise ValueError("该会话不是 SSH/SFTP 会话")
    return session


async def sftp_list(request: web.Request) -> web.Response:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    result = await asyncio.to_thread(fsops.sftp_list, sftp, data.get("path") or "~", data.get("showHidden", True))
    return ok(result)


async def sftp_mkdir(request: web.Request) -> web.Response:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    await asyncio.to_thread(fsops.sftp_mkdir, sftp, data["path"])
    return ok()


async def sftp_rename(request: web.Request) -> web.Response:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    await asyncio.to_thread(fsops.sftp_rename, sftp, data["path"], data["target"])
    return ok()


async def sftp_delete(request: web.Request) -> web.Response:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    await asyncio.to_thread(fsops.sftp_delete, sftp, data["path"])
    return ok()


async def sftp_chmod(request: web.Request) -> web.Response:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    await asyncio.to_thread(fsops.sftp_chmod, sftp, data["path"], data["mode"])
    return ok()


async def sftp_read(request: web.Request) -> web.Response:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    result = await asyncio.to_thread(fsops.sftp_read_text, sftp, data["path"])
    return ok(result)


async def sftp_write(request: web.Request) -> web.Response:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    result = await asyncio.to_thread(
        fsops.sftp_write_text, sftp, data["path"], data.get("content") or "", data.get("encoding") or "utf-8"
    )
    return ok(result)


async def sftp_download(request: web.Request) -> web.StreamResponse:
    data = await body(request)
    session = await sftp_session(request)
    sftp = await asyncio.to_thread(session.get_sftp)
    remote = data["path"]
    st = await asyncio.to_thread(sftp.stat, remote)
    name = os.path.basename(remote.rstrip("/")) or "download"
    resp = web.StreamResponse(
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f"attachment; filename=\"{safe_filename(name)}\"",
            "Content-Length": str(int(st.st_size or 0)),
        }
    )
    await resp.prepare(request)
    fh = await asyncio.to_thread(sftp.open, remote, "rb")
    try:
        await asyncio.to_thread(fh.set_pipelined, True)
        while True:
            chunk = await asyncio.to_thread(fh.read, 262144)
            if not chunk:
                break
            await resp.write(chunk)
    finally:
        try:
            await asyncio.to_thread(fh.close)
        except Exception:
            pass
    await resp.write_eof()
    return resp


async def sftp_upload(request: web.Request) -> web.Response:
    session_id = request.query.get("sessionId")
    remote = request.query.get("path")
    session = STATE.hub.sessions.get(session_id or "")
    if not isinstance(session, SSHSession) or not remote:
        return json_response({"error": "参数错误"}, status=400)
    sftp = await asyncio.to_thread(session.get_sftp)
    fh = await asyncio.to_thread(sftp.open, remote, "wb")
    total = 0
    try:
        await asyncio.to_thread(fh.set_pipelined, True)
        async for chunk in request.content.iter_chunked(262144):
            await asyncio.to_thread(fh.write, chunk)
            total += len(chunk)
    finally:
        try:
            await asyncio.to_thread(fh.close)
        except Exception:
            pass
    return ok({"path": remote, "size": total})


# ------------------------------------------------------- archives (r26)
@asynccontextmanager
async def archive_ssh_target(data: dict):
    """Yield ``(sftp, exec_fn)`` for an SSH session or a saved SSH asset.

    A live session reuses its transport, so archive commands share the
    already-authenticated connection instead of opening another one.  Without a
    session an asset opens a temporary chain that is closed on exit.
    """
    session = STATE.hub.sessions.get(data.get("sessionId") or "")
    if isinstance(session, SSHSession):
        sftp = await asyncio.to_thread(session.get_sftp)

        def run(command: str, timeout: int) -> dict:
            return session.run_command(command, timeout)

        yield sftp, run
        return
    asset_id = str(data.get("assetId") or "")
    if not asset_id:
        raise ValueError("需要 SSH 会话或 SSH 资产")
    async with STATE.hub.ssh_transport(asset_id) as transport:
        sftp = await asyncio.to_thread(paramiko.SFTPClient.from_transport, transport)
        try:
            sftp.get_channel().settimeout(30)

            def run(command: str, timeout: int) -> dict:
                return sshlib.exec_on_transport(transport, command, timeout)

            yield sftp, run
        finally:
            try:
                await asyncio.to_thread(sftp.close)
            except Exception:
                pass


def archive_history(data: dict, title: str, detail: str) -> None:
    asset_id = data.get("assetId")
    if not asset_id:
        session = STATE.hub.sessions.get(data.get("sessionId") or "")
        asset_id = getattr(session, "asset_id", None)
    try:
        STATE.store.add_history("archive", title, asset_id, detail)
    except Exception:
        log.debug("archive history write failed", exc_info=True)


async def sftp_archive_formats(request: web.Request) -> web.Response:
    data = await body(request)
    async with archive_ssh_target(data) as (_sftp, run):
        tools = await asyncio.to_thread(archive_svc.probe_tools, run)
    return ok(
        {
            "formats": archive_svc.formats_for(tools),
            "tools": sorted(tools),
            "sevenZip": archive_svc.seven_zip_name(tools),
        }
    )


async def sftp_archive_inspect(request: web.Request) -> web.Response:
    data = await body(request)
    async with archive_ssh_target(data) as (sftp, run):
        result = await asyncio.to_thread(
            archive_svc.remote_inspect,
            sftp,
            run,
            data.get("path") or "",
            data.get("timeout") or 120,
        )
    return ok(result)


async def sftp_archive_compress(request: web.Request) -> web.Response:
    data = await body(request)
    async with archive_ssh_target(data) as (sftp, run):
        result = await asyncio.to_thread(
            archive_svc.remote_compress,
            sftp,
            run,
            data.get("paths") or [],
            data.get("targetDir") or "",
            data.get("name") or "",
            data.get("format") or "tar.gz",
            bool(data.get("overwrite")),
            data.get("timeout") or 600,
        )
    archive_history(
        data,
        f"压缩 · {result['name']}",
        f"{result['count']} 项 → {result['path']}（{result['sizeText']}）",
    )
    return ok(result)


async def sftp_archive_extract(request: web.Request) -> web.Response:
    data = await body(request)
    async with archive_ssh_target(data) as (sftp, run):
        result = await asyncio.to_thread(
            archive_svc.remote_extract,
            sftp,
            run,
            data.get("path") or "",
            data.get("targetDir") or "",
            data.get("create", True),
            data.get("overwrite", True),
            data.get("timeout") or 600,
        )
    archive_history(
        data,
        f"解压 · {os.path.basename(result['path'])}",
        f"{result['members']} 项 → {result['targetDir']}",
    )
    return ok(result)


async def local_archive_formats(request: web.Request) -> web.Response:
    return ok({"formats": archive_svc.local_formats()})


async def local_archive_inspect(request: web.Request) -> web.Response:
    data = await body(request)
    result = await asyncio.to_thread(archive_svc.local_inspect, data.get("path") or "")
    return ok(result)


async def local_archive_compress(request: web.Request) -> web.Response:
    data = await body(request)
    result = await asyncio.to_thread(
        archive_svc.local_compress,
        data.get("paths") or [],
        data.get("targetDir") or "",
        data.get("name") or "",
        data.get("format") or "tar.gz",
        bool(data.get("overwrite")),
    )
    archive_history(
        data,
        f"压缩 · {result['name']}",
        f"{result['count']} 项 → {result['path']}（{result['sizeText']}）",
    )
    return ok(result)


async def local_archive_extract(request: web.Request) -> web.Response:
    data = await body(request)
    result = await asyncio.to_thread(
        archive_svc.local_extract,
        data.get("path") or "",
        data.get("targetDir") or "",
        data.get("create", True),
        data.get("overwrite", True),
    )
    archive_history(
        data,
        f"解压 · {os.path.basename(result['path'])}",
        f"{result['members']} 项 → {result['targetDir']}",
    )
    return ok(result)


async def local_home(request: web.Request) -> web.Response:
    return ok(fsops.local_home())


async def local_list(request: web.Request) -> web.Response:
    data = await body(request)
    result = await asyncio.to_thread(fsops.local_list, data.get("path") or "~", data.get("showHidden", True))
    return ok(result)


async def local_mkdir(request: web.Request) -> web.Response:
    data = await body(request)
    await asyncio.to_thread(fsops.local_mkdir, data["path"])
    return ok()


async def local_rename(request: web.Request) -> web.Response:
    data = await body(request)
    await asyncio.to_thread(fsops.local_rename, data["path"], data["target"])
    return ok()


async def local_delete(request: web.Request) -> web.Response:
    data = await body(request)
    await asyncio.to_thread(fsops.local_delete, data["path"])
    return ok()


async def local_chmod(request: web.Request) -> web.Response:
    data = await body(request)
    await asyncio.to_thread(fsops.local_chmod, data["path"], data["mode"])
    return ok()


async def local_read(request: web.Request) -> web.Response:
    data = await body(request)
    result = await asyncio.to_thread(fsops.local_read_text, data["path"])
    return ok(result)


async def local_write(request: web.Request) -> web.Response:
    data = await body(request)
    result = await asyncio.to_thread(
        fsops.local_write_text, data["path"], data.get("content") or "", data.get("encoding") or "utf-8"
    )
    return ok(result)


async def local_download(request: web.Request) -> web.StreamResponse:
    data = await body(request)
    path = os.path.expanduser(data["path"])
    if not os.path.isfile(path):
        raise ValueError("文件不存在")
    return web.FileResponse(
        path,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename(os.path.basename(path))}"'},
    )


async def local_upload(request: web.Request) -> web.Response:
    path = request.query.get("path")
    if not path:
        return json_response({"error": "缺少目标路径"}, status=400)
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    total = 0
    with open(path, "wb") as fh:
        async for chunk in request.content.iter_chunked(262144):
            fh.write(chunk)
            total += len(chunk)
    return ok({"path": path, "size": total})


# ------------------------------------------------------------------- db API
async def _with_db(asset_id: str):
    return await STATE.hub.open_db(asset_id)


async def db_overview(request: web.Request) -> web.Response:
    data = await body(request)
    asset_id = data["assetId"]
    cache_key = f"{asset_id}|overview|{data.get('database') or ''}"
    if not data.get("refresh"):
        cached = _cache_get(cache_key)
        if cached is not None:
            return ok(cached)
    adapter = await _with_db(asset_id)
    try:
        databases = await asyncio.to_thread(adapter.list_databases)
        schemas: list[str] = []
        try:
            schemas = await asyncio.to_thread(adapter.list_schemas, data.get("database"))
        except Exception:
            schemas = []
        payload = {
            "databases": databases,
            "schemas": schemas,
            "dialect": adapter.dialect,
            "version": await asyncio.to_thread(adapter.version),
        }
        return ok(_cache_put(cache_key, payload))
    except Exception as exc:
        # A shared/remote instance occasionally drops the session for a whole
        # request window. Reading the tree is not worth an error when the last
        # good answer is still around, so serve it instead of an empty panel.
        stale = _cache_stale(cache_key)
        if stale is not None and is_transient_reset(exc):
            return ok(stale)
        raise
    finally:
        adapter.close()


async def db_objects(request: web.Request) -> web.Response:
    data = await body(request)
    asset_id = data["assetId"]
    cache_key = f"{asset_id}|objects|{data.get('database') or ''}|{data.get('schema') or ''}"
    if not data.get("refresh"):
        cached = _cache_get(cache_key)
        if cached is not None:
            return ok(cached)
    adapter = await _with_db(asset_id)
    try:
        result = await asyncio.to_thread(
            adapter.list_objects, data.get("database"), data.get("schema")
        )
        return ok(_cache_put(cache_key, result))
    except Exception as exc:
        stale = _cache_stale(cache_key)
        if stale is not None and is_transient_reset(exc):
            return ok(stale)
        raise
    finally:
        adapter.close()


async def db_attach(request: web.Request) -> web.Response:
    """Persist an extra DuckDB database file in the asset config and ATTACH it."""
    data = await body(request)
    asset_id = data["assetId"]
    asset = STATE.store.get_asset(asset_id)
    if not asset:
        return json_response({"error": "资产不存在"}, status=404)
    cfg = asset.get("config") or {}
    if (cfg.get("dbType") or "").lower() != "duckdb":
        return json_response({"error": "仅 DuckDB 支持附加数据库文件"}, status=400)
    path = os.path.expanduser(str(data.get("path") or "").strip())
    if not path or not os.path.exists(path):
        return json_response({"error": f"文件不存在：{path or '(空)'}"}, status=400)
    alias = re.sub(r"[^0-9A-Za-z_]", "_", str(data.get("alias") or os.path.splitext(os.path.basename(path))[0]))
    alias = alias.strip("_") or "attached"
    attached = [a for a in (cfg.get("attached") or []) if isinstance(a, dict)]
    attached = [a for a in attached if a.get("alias") != alias and a.get("path") != path]
    attached.append({"path": path, "alias": alias, "readOnly": bool(data.get("readOnly"))})
    STATE.store.update_asset(asset_id, {"config": {"attached": attached}})
    _cache_invalidate(asset_id)
    adapter = await _with_db(asset_id)
    try:
        databases = await asyncio.to_thread(adapter.list_databases)
    finally:
        adapter.close()
    return ok({"attached": attached, "databases": databases})


async def db_table(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        result = await asyncio.to_thread(
            adapter.table_detail,
            data.get("database"),
            data.get("schema"),
            data["table"],
            int(data.get("limit") or 200),
            int(data.get("offset") or 0),
            data.get("orderBy"),
            data.get("orderDir") or "ASC",
            data.get("where"),
        )
        return ok(result)
    finally:
        adapter.close()


async def db_ddl(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        ddl = await asyncio.to_thread(adapter.ddl, data.get("database"), data.get("schema"), data["table"])
        return ok({"ddl": ddl})
    finally:
        adapter.close()


async def db_query(request: web.Request) -> web.Response:
    data = await body(request)
    sql = (data.get("sql") or "").strip()
    if not sql:
        return json_response({"error": "SQL 为空"}, status=400)
    adapter = await _with_db(data["assetId"])
    try:
        STATE.db_pids[data["assetId"]] = adapter.backend_pid()
        result = await asyncio.to_thread(adapter.query, sql, data.get("database"))
        return ok(result)
    finally:
        STATE.db_pids.pop(data["assetId"], None)
        adapter.close()


async def db_explain(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        result = await asyncio.to_thread(adapter.explain, data.get("sql") or "", data.get("database"))
        return ok(result)
    finally:
        adapter.close()


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and value.get("__type") == "blob" and value.get("base64"):
        return base64.b64decode(value["base64"])
    return value


async def db_row(request: web.Request) -> web.Response:
    data = await body(request)
    action = data.get("action")
    keys = {k: _decode_value(v) for k, v in (data.get("keys") or {}).items()}
    values = {k: _decode_value(v) for k, v in (data.get("values") or {}).items()}
    adapter = await _with_db(data["assetId"])
    try:
        if action == "update":
            result = await asyncio.to_thread(
                adapter.update_row, data.get("database"), data.get("schema"), data["table"], keys, values
            )
        elif action == "insert":
            result = await asyncio.to_thread(
                adapter.insert_row, data.get("database"), data.get("schema"), data["table"], values
            )
        elif action == "delete":
            result = await asyncio.to_thread(
                adapter.delete_row, data.get("database"), data.get("schema"), data["table"], keys
            )
        else:
            return json_response({"error": "未知操作"}, status=400)
        return ok(result)
    finally:
        adapter.close()


async def db_export(request: web.Request) -> web.Response:
    data = await body(request)
    fmt = (data.get("format") or "sql").lower()
    adapter = await _with_db(data["assetId"])
    try:
        if data.get("sql"):
            result = await asyncio.to_thread(adapter.query, data["sql"], data.get("database"))
            columns = result["results"][0]["columns"] if result["results"] else []
            rows = result["results"][0]["rows"] if result["results"] else []
            table_name = "query"
        else:
            detail = await asyncio.to_thread(
                adapter.table_detail,
                data.get("database"),
                data.get("schema"),
                data["table"],
                int(data.get("limit") or 10000),
                0,
                None,
                "ASC",
                data.get("where"),
            )
            columns = [c["name"] for c in detail["columns"]]
            rows = detail["rows"]
            table_name = data["table"]
        if fmt == "xlsx":
            payload = _render_xlsx(table_name, columns, rows)
            return web.Response(
                body=payload,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_filename(table_name)}.xlsx"'
                },
            )
        content = _render_export(fmt, table_name, columns, rows, adapter.dialect)
        ext = {"sql": "sql", "csv": "csv", "json": "json", "txt": "txt"}.get(fmt, "txt")
        ctype = {
            "csv": "text/csv",
            "json": "application/json",
            "sql": "application/sql",
            "txt": "text/plain",
        }.get(fmt, "text/plain")
        if fmt in ("csv", "txt"):
            ctype += "; charset=utf-8"
        return web.Response(
            body=content.encode("utf-8"),
            content_type=ctype.split(";")[0],
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename(table_name)}.{ext}"'
            },
        )
    finally:
        adapter.close()


def _render_xlsx(table: str, columns: list[str], rows: list[list[Any]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\\/*?:\[\]]", "_", table or "export")[:31] or "export"
    ws.append(list(columns))
    for row in rows:
        cells = []
        for value in row:
            if value is None:
                cells.append(None)
            elif isinstance(value, (dict, list)):
                cells.append(json.dumps(value, ensure_ascii=False, default=str))
            elif isinstance(value, (bytes, bytearray)):
                cells.append(f"<binary {len(value)} bytes>")
            else:
                cells.append(value)
        ws.append(cells)
    ws.freeze_panes = "A2"
    for idx, name in enumerate(columns, start=1):
        width = max(10, min(48, len(str(name)) + 4))
        ws.column_dimensions[get_column_letter(idx)].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_export(fmt: str, table: str, columns: list[str], rows: list[list[Any]], dialect: str) -> str:
    if fmt == "txt":
        lines = ["\t".join(str(c) for c in columns)]
        for row in rows:
            lines.append(
                "\t".join(
                    "" if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
                    for v in row
                )
            )
        return "\n".join(lines) + "\n"
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if v is None else (v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)) for v in row])
        return buf.getvalue()
    if fmt == "json":
        payload = [dict(zip(columns, row)) for row in rows]
        return json.dumps(payload, ensure_ascii=False, indent=2)
    quote = "`" if dialect == "mysql" else '"'
    qtable = quote + table.replace(quote, quote * 2) + quote

    def literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict) and value.get("__type") == "blob":
            return "X'" + (value.get("preview") or "") + "'"
        text = str(value).replace("'", "''")
        return "'" + text + "'"

    lines = [f"-- H1KHub export: {table} ({len(rows)} rows)", f"-- {now_iso()}"]
    for row in rows:
        cols = ", ".join(quote + c.replace(quote, quote * 2) + quote for c in columns)
        vals = ", ".join(literal(v) for v in row)
        lines.append(f"INSERT INTO {qtable} ({cols}) VALUES ({vals});")
    return "\n".join(lines) + "\n"


# --------------------------------------------------- db sessions & advanced
META_CACHE: dict[str, tuple[float, Any]] = {}
META_TTL = 20.0


def _cache_get(key: str):
    hit = META_CACHE.get(key)
    if hit and time.time() - hit[0] < META_TTL:
        return hit[1]
    return None


def _cache_stale(key: str):
    """Last cached value regardless of age, used when a remote reset drops a read."""
    hit = META_CACHE.get(key)
    return hit[1] if hit else None


def _cache_put(key: str, value: Any) -> Any:
    META_CACHE[key] = (time.time(), value)
    return value


def _cache_invalidate(asset_id: str) -> None:
    for key in [k for k in META_CACHE if k.startswith(asset_id + "|")]:
        META_CACHE.pop(key, None)


async def db_session_open(request: web.Request) -> web.Response:
    data = await body(request)
    session = await STATE.dbsessions.open(data["assetId"], bool(data.get("autocommit", True)))
    return ok(session.status())


async def db_session_close(request: web.Request) -> web.Response:
    data = await body(request)
    closed = STATE.dbsessions.close(data["sessionId"])
    return ok({"closed": closed})


async def db_session_status(request: web.Request) -> web.Response:
    data = await body(request)
    session = STATE.dbsessions.get(data["sessionId"])
    return ok(session.status())


async def db_tx(request: web.Request) -> web.Response:
    data = await body(request)
    action = (data.get("action") or "").lower()
    session = STATE.dbsessions.get(data["sessionId"])
    with session.lock:
        try:
            if action == "begin":
                await asyncio.to_thread(session.adapter.begin)
                session.in_tx = True
            elif action == "commit":
                await asyncio.to_thread(session.adapter.commit)
                session.in_tx = False
                await asyncio.to_thread(session.adapter.set_autocommit, session.autocommit)
            elif action == "rollback":
                await asyncio.to_thread(session.adapter.rollback)
                session.in_tx = False
                await asyncio.to_thread(session.adapter.set_autocommit, session.autocommit)
            elif action == "set-autocommit":
                enabled = bool(data.get("enabled", True))
                await asyncio.to_thread(session.adapter.set_autocommit, enabled)
                session.autocommit = enabled
                session.in_tx = False
            else:
                return json_response({"error": f"未知事务操作：{action}"}, status=400)
        finally:
            session.touch()
    return ok(session.status())


async def db_session_query(request: web.Request) -> web.Response:
    data = await body(request)
    sql = (data.get("sql") or "").strip()
    if not sql:
        return json_response({"error": "SQL 为空"}, status=400)
    session = STATE.dbsessions.get(data["sessionId"])
    database = data.get("database")
    if database and database != session.adapter._dbname:
        if session.in_tx:
            return json_response({"error": "事务进行中不能切换数据库，请先提交或回滚"}, status=400)
        await asyncio.to_thread(session.adapter.reconnect, database)
    with session.lock:
        try:
            session.active_pid = session.adapter.backend_pid()
            result = await asyncio.to_thread(session.adapter.query, sql, None)
        finally:
            session.active_pid = 0
            session.touch()
    result["session"] = session.status()
    return ok(result)


async def db_schema(request: web.Request) -> web.Response:
    data = await body(request)
    action = (data.get("action") or "").lower()
    asset_id = data["assetId"]
    database, schema, table = data.get("database"), data.get("schema"), data.get("table")
    adapter = await _with_db(asset_id)
    try:
        if action == "alter":
            statements = dbsessions.build_alter_sql(adapter, database, schema, table, data.get("ops") or [])
        elif action == "create-table":
            statements = dbsessions.build_create_table_sql(adapter, database, schema, table, data.get("spec") or {})
        elif action == "drop-table":
            ref = dbsessions.table_ref(adapter, database, schema, table)
            statements = [f"DROP TABLE {ref}"]
        elif action == "truncate":
            ref = dbsessions.table_ref(adapter, database, schema, table)
            if adapter.dialect == "sqlite":
                statements = [f"DELETE FROM {ref}"]
            else:
                statements = [f"TRUNCATE TABLE {ref}"]
        elif action == "rename-table":
            ref = dbsessions.table_ref(adapter, database, schema, table)
            new_name = dbsessions.identifier(data.get("newName"))
            if adapter.dialect == "mysql":
                statements = [f"RENAME TABLE {ref} TO {dbsessions.table_ref(adapter, database, schema, new_name)}"]
            else:
                statements = [f"ALTER TABLE {ref} RENAME TO {adapter.quote(new_name)}"]
        elif action == "create-database":
            name = dbsessions.identifier(data.get("name"))
            if adapter.dialect == "mysql":
                statements = [f"CREATE DATABASE {adapter.quote(name)}"]
            elif adapter.dialect == "postgresql":
                statements = [f'CREATE DATABASE "{name}"']
            else:
                raise DatabaseError("SQLite 不支持创建数据库")
        elif action == "drop-database":
            name = dbsessions.identifier(data.get("name"))
            if adapter.dialect == "sqlite":
                raise DatabaseError("SQLite 不支持删除数据库")
            statements = [f"DROP DATABASE {adapter.quote(name)}"]
        elif action == "preview":
            statements = dbsessions.build_alter_sql(adapter, database, schema, table, data.get("ops") or [])
            return ok({"sql": statements, "preview": True})
        else:
            return json_response({"error": f"未知结构操作：{action}"}, status=400)
        executed = []
        for stmt in statements:
            await asyncio.to_thread(adapter.query, stmt, None)
            executed.append(stmt)
        _cache_invalidate(asset_id)
        STATE.store.add_history("ddl", f"表结构变更 · {table or data.get('name') or ''}", asset_id, "\n".join(executed)[:500])
        return ok({"sql": executed, "count": len(executed)})
    finally:
        adapter.close()


async def db_import(request: web.Request) -> web.Response:
    data = await body(request)
    sql = data.get("sql") or ""
    if not sql.strip():
        return json_response({"error": "请提供 .sql 脚本内容"}, status=400)
    if data.get("background"):
        asset_id = data["assetId"]

        async def job():
            adapter = await _with_db(asset_id)
            try:
                result = await asyncio.to_thread(
                    dbsessions.run_script,
                    adapter,
                    sql,
                    database=data.get("database"),
                    stop_on_error=bool(data.get("stopOnError")),
                    transactional=bool(data.get("transactional", True)),
                )
                _cache_invalidate(asset_id)
                return result
            finally:
                adapter.close()

        label = data.get("filename") or f"{len(sql)} 字节"
        task = STATE.tasks.submit("db-import", f"SQL 导入 · {label}", job)
        return ok({"task": task})
    adapter = await _with_db(data["assetId"])
    try:
        result = await asyncio.to_thread(
            dbsessions.run_script,
            adapter,
            sql,
            database=data.get("database"),
            stop_on_error=bool(data.get("stopOnError")),
            transactional=bool(data.get("transactional", True)),
        )
        _cache_invalidate(data["assetId"])
        STATE.store.add_history("import", f"SQL 导入 · {result['executed']} 条语句", data["assetId"], (data.get("filename") or "")[:200])
        return ok(result)
    finally:
        adapter.close()


async def db_rows(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        result = await asyncio.to_thread(
            dbsessions.batch_rows,
            adapter,
            data.get("database"),
            data.get("schema"),
            data["table"],
            data.get("action") or "delete",
            [{k: _decode_value(v) for k, v in row.items()} for row in (data.get("rows") or [])],
            {k: _decode_value(v) for k, v in (data.get("values") or {}).items()} or None,
        )
        return ok(result)
    finally:
        adapter.close()


async def db_blob(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        keys = {k: _decode_value(v) for k, v in (data.get("keys") or {}).items()}
        result = await asyncio.to_thread(
            dbsessions.read_blob,
            adapter,
            data.get("database"),
            data.get("schema"),
            data["table"],
            data["column"],
            keys,
        )
        return ok(result)
    finally:
        adapter.close()


async def db_blob_download(request: web.Request) -> web.StreamResponse:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        keys = {k: _decode_value(v) for k, v in (data.get("keys") or {}).items()}
        payload = await asyncio.to_thread(
            dbsessions.blob_bytes,
            adapter,
            data.get("database"),
            data.get("schema"),
            data["table"],
            data["column"],
            keys,
        )
        name = safe_filename(data.get("filename") or f"{data['table']}-{data['column']}.bin")
        return web.Response(
            body=payload,
            content_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    finally:
        adapter.close()


async def db_table_schema(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        result = await asyncio.to_thread(
            dbsessions.table_schema,
            adapter,
            data.get("database"),
            data.get("schema"),
            data["table"],
        )
        return ok(result)
    finally:
        adapter.close()


async def db_realtime(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        info = await asyncio.to_thread(dbsessions.realtime_info, adapter)
        procs = await asyncio.to_thread(dbsessions.processlist, adapter)
        return ok({"info": info, "processes": procs[:50]})
    finally:
        adapter.close()


async def db_processlist(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        return ok({"processes": await asyncio.to_thread(dbsessions.processlist, adapter)})
    finally:
        adapter.close()


# ------------------------------------------------------------------- redis
async def redis_open(request: web.Request) -> tuple[RedisService, Any]:
    data = await body(request)
    svc, tunnel = await STATE.hub.open_redis(data["assetId"])
    return svc, tunnel


async def redis_api(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    data = await body(request)
    svc, tunnel = await STATE.hub.open_redis(data["assetId"])
    db = int(data.get("db") or 0)
    try:
        if action == "test":
            return ok(await asyncio.to_thread(svc.test))
        if action == "databases":
            return ok({"databases": await asyncio.to_thread(svc.databases)})
        if action == "scan":
            return ok(await asyncio.to_thread(svc.scan, db, data.get("cursor") or "0", data.get("pattern") or "*", int(data.get("count") or 200)))
        if action == "key":
            info, value = await asyncio.gather(
                asyncio.to_thread(svc.key_info, db, data["key"]),
                asyncio.to_thread(svc.value, db, data["key"]),
            )
            return ok({"info": info, "value": value})
        if action == "set-string":
            await asyncio.to_thread(svc.set_string, db, data["key"], data.get("value") or "", data.get("ttl"))
            return ok()
        if action == "json-set":
            await asyncio.to_thread(svc.set_json, db, data["key"], data.get("value") or "null")
            return ok()
        if action == "hash-set":
            await asyncio.to_thread(svc.hash_set, db, data["key"], data["field"], data.get("value") or "")
            return ok()
        if action == "hash-del":
            await asyncio.to_thread(svc.hash_del, db, data["key"], data["field"])
            return ok()
        if action == "list-push":
            await asyncio.to_thread(svc.list_push, db, data["key"], data.get("value") or "", bool(data.get("tail", True)))
            return ok()
        if action == "list-set":
            await asyncio.to_thread(svc.list_set, db, data["key"], int(data["index"]), data.get("value") or "")
            return ok()
        if action == "list-remove":
            await asyncio.to_thread(svc.list_remove, db, data["key"], int(data["index"]))
            return ok()
        if action == "set-add":
            await asyncio.to_thread(svc.set_add, db, data["key"], data.get("member") or "")
            return ok()
        if action == "set-remove":
            await asyncio.to_thread(svc.set_remove, db, data["key"], data.get("member") or "")
            return ok()
        if action == "zset-add":
            await asyncio.to_thread(svc.zset_add, db, data["key"], data.get("member") or "", float(data.get("score") or 0))
            return ok()
        if action == "zset-remove":
            await asyncio.to_thread(svc.zset_remove, db, data["key"], data.get("member") or "")
            return ok()
        if action == "create":
            await asyncio.to_thread(svc.create_key, db, data["key"], data.get("type") or "string")
            return ok()
        if action == "delete":
            return ok({"deleted": await asyncio.to_thread(svc.delete, db, data.get("keys") or [data.get("key")])})
        if action == "rename":
            await asyncio.to_thread(svc.rename, db, data["key"], data["target"])
            return ok()
        if action == "expire":
            await asyncio.to_thread(svc.expire, db, data["key"], int(data.get("ttl") or 0))
            return ok()
        if action == "scan-keys":
            return ok(
                {
                    "keys": await asyncio.to_thread(
                        svc.scan_keys, db, data.get("pattern") or "*", int(data.get("limit") or 1000)
                    )
                }
            )
        if action == "batch-delete":
            return ok(await asyncio.to_thread(svc.batch_delete, db, data.get("keys") or []))
        if action == "batch-rename":
            return ok(await asyncio.to_thread(svc.batch_rename, db, data.get("keys") or [], data.get("rule") or {}))
        if action == "batch-set":
            return ok(
                await asyncio.to_thread(
                    svc.batch_set, db, data.get("keys") or [], data.get("value") or "", data.get("ttl")
                )
            )
        if action == "batch-expire":
            return ok(await asyncio.to_thread(svc.batch_expire, db, data.get("keys") or [], int(data.get("ttl") or 0)))
        if action == "console":
            return ok(await asyncio.to_thread(svc.console, db, data.get("command") or ""))
        if action == "realtime":
            return ok(await asyncio.to_thread(svc.realtime))
        return json_response({"error": f"未知 Redis 操作：{action}"}, status=404)
    finally:
        svc.close()
        if tunnel:
            tunnel.stop()


# ------------------------------------------------------------ agent undo
async def agent_backups(request: web.Request) -> web.Response:
    return ok({"backups": agent_backup.list_backups(HOME, int(request.query.get("limit") or 50))})


async def agent_rollback(request: web.Request) -> web.Response:
    data = await body(request)
    try:
        meta, content = agent_backup.restore(HOME, data.get("id") or "")
    except FileNotFoundError:
        return json_response({"error": "备份不存在"}, status=404)
    from fsops import local_write_text, sftp_write_text

    if content is None:
        return ok({"restored": meta, "removed": False, "note": "写入前文件不存在，未自动删除"})
    if meta.get("assetId"):
        async with STATE.hub.sftp(meta["assetId"]) as sftp:
            result = await asyncio.to_thread(sftp_write_text, sftp, meta["path"], content)
    else:
        result = await asyncio.to_thread(local_write_text, meta["path"], content)
    return ok({"restored": meta, **result})



# ------------------------------------------------------------ db objects
def _obj_ref(adapter, database, schema, name):
    ident = dbsessions.identifier(name)
    if adapter.dialect == "mysql":
        return f"{adapter.quote(database or adapter._dbname)}.{adapter.quote(ident)}"
    if adapter.dialect == "postgresql":
        return f"{adapter.quote(schema or 'public')}.{adapter.quote(ident)}"
    return adapter.quote(ident)


def _object_definition(adapter, database, schema, name, kind, routine_type="PROCEDURE"):
    cur = adapter._cursor()
    if adapter.dialect == "mysql":
        if kind == "view":
            cur.execute(f"SHOW CREATE VIEW {_obj_ref(adapter, database, schema, name)}")
            row = cur.fetchone()
            return row[1] if row and len(row) > 1 else ""
        rtype = "FUNCTION" if str(routine_type).upper() == "FUNCTION" else "PROCEDURE"
        cur.execute(f"SHOW CREATE {rtype} {_obj_ref(adapter, database, schema, name)}")
        row = cur.fetchone()
        if not row:
            return ""
        for value in row:
            if isinstance(value, str) and value.upper().startswith("CREATE"):
                return value
        return row[-1] or ""
    if adapter.dialect == "postgresql":
        if kind == "view":
            cur.execute("SELECT pg_get_viewdef(%s::regclass, true)", (_obj_ref(adapter, database, schema, name),))
            row = cur.fetchone()
            return ("CREATE OR REPLACE VIEW " + _obj_ref(adapter, database, schema, name) + " AS\n" + (row[0] or "")) if row else ""
        cur.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace"
            " WHERE p.proname=%s AND n.nspname=%s LIMIT 1",
            (name, schema or "public"),
        )
        row = cur.fetchone()
        return row[0] if row else ""
    cur.execute("SELECT sql FROM sqlite_master WHERE name=? AND type IN ('view','table')", (name,))
    row = cur.fetchone()
    return (row[0] or "") if row else ""


async def db_object_definition(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        definition = await asyncio.to_thread(
            _object_definition,
            adapter,
            data.get("database"),
            data.get("schema"),
            data["name"],
            data.get("kind") or "view",
            data.get("routineType") or "PROCEDURE",
        )
        return ok({"definition": definition, "kind": data.get("kind") or "view", "name": data["name"]})
    finally:
        adapter.close()


async def db_object_save(request: web.Request) -> web.Response:
    data = await body(request)
    sql = (data.get("sql") or "").strip()
    kind = (data.get("kind") or "view").lower()
    name = dbsessions.identifier(data["name"])
    if not sql:
        return json_response({"error": "定义不能为空"}, status=400)
    head = sql.lstrip().split(None, 2)[0:3]
    if not sql.lstrip().upper().startswith("CREATE"):
        return json_response({"error": "定义必须以 CREATE 开头"}, status=400)
    adapter = await _with_db(data["assetId"])
    try:
        ref = _obj_ref(adapter, data.get("database"), data.get("schema"), name)
        statements = []
        if kind == "view":
            if adapter.dialect == "sqlite":
                statements.append(f"DROP VIEW IF EXISTS {ref}")
                statements.append(sql)
            else:
                body_sql = re.sub(r"(?is)^CREATE\s+(OR\s+REPLACE\s+)?VIEW", "CREATE OR REPLACE VIEW", sql, count=1)
                statements.append(body_sql)
        else:
            rtype = "FUNCTION" if str(data.get("routineType") or "").upper() == "FUNCTION" else "PROCEDURE"
            statements.append(f"DROP {rtype} IF EXISTS {ref}")
            statements.append(sql)
        for stmt in statements:
            adapter.query(stmt, None)
        _cache_invalidate(data["assetId"])
        return ok({"saved": name, "statements": statements})
    finally:
        adapter.close()


async def db_object_drop(request: web.Request) -> web.Response:
    data = await body(request)
    adapter = await _with_db(data["assetId"])
    try:
        name = dbsessions.identifier(data["name"])
        kind = (data.get("kind") or "view").lower()
        ref = _obj_ref(adapter, data.get("database"), data.get("schema"), name)
        if kind == "view":
            stmt = f"DROP VIEW IF EXISTS {ref}"
        else:
            rtype = "FUNCTION" if str(data.get("routineType") or "").upper() == "FUNCTION" else "PROCEDURE"
            stmt = f"DROP {rtype} IF EXISTS {ref}"
        adapter.query(stmt, None)
        _cache_invalidate(data["assetId"])
        return ok({"dropped": name, "statement": stmt})
    finally:
        adapter.close()



async def db_cancel(request: web.Request) -> web.Response:
    data = await body(request)
    pid = STATE.db_pids.get(data.get("assetId") or "")
    if not pid:
        return json_response({"error": "没有正在执行的查询"}, status=400)
    adapter = await _with_db(data["assetId"])
    try:
        await asyncio.to_thread(adapter.cancel_pid, pid)
    except Exception as exc:
        return json_response({"error": f"取消失败：{exc}"}, status=400)
    finally:
        adapter.close()
    return ok({"cancelled": True, "pid": pid})


async def db_session_cancel(request: web.Request) -> web.Response:
    data = await body(request)
    session = STATE.dbsessions.get(data.get("sessionId") or "")
    if not session or not session.active_pid:
        return json_response({"error": "没有正在执行的查询"}, status=400)
    adapter = await _with_db(session.asset_id)
    try:
        await asyncio.to_thread(adapter.cancel_pid, session.active_pid)
    except Exception as exc:
        return json_response({"error": f"取消失败：{exc}"}, status=400)
    finally:
        adapter.close()
    return ok({"cancelled": True, "pid": session.active_pid})



# ------------------------------------------------------------------- sync
async def _open_pair(source: dict, target: dict):
    src = await STATE.hub.open_db(source["assetId"])
    try:
        dst = await STATE.hub.open_db(target["assetId"])
    except Exception:
        src.close()
        raise
    return src, dst


async def db_sync_diff(request: web.Request) -> web.Response:
    data = await body(request)
    source, target = data["source"], data["target"]
    src, dst = await _open_pair(source, target)
    try:
        result = await asyncio.to_thread(
            dbsync.diff_schema,
            src, source.get("database"), source.get("schema"), source["table"],
            dst, target.get("database"), target.get("schema"), target["table"],
        )
        return ok(result)
    finally:
        src.close()
        dst.close()


async def db_sync_apply(request: web.Request) -> web.Response:
    data = await body(request)
    source, target = data["source"], data["target"]
    src, dst = await _open_pair(source, target)
    try:
        result = await asyncio.to_thread(
            dbsync.apply_schema,
            src, source.get("database"), source.get("schema"), source["table"],
            dst, target.get("database"), target.get("schema"), target["table"],
        )
        _cache_invalidate(target["assetId"])
        STATE.store.add_history("sync", f"结构同步 · {source['table']} → {target['table']}", target["assetId"], f"{len(result['executed'])} 条语句")
        return ok(result)
    finally:
        src.close()
        dst.close()


async def db_sync_data_preview(request: web.Request) -> web.Response:
    data = await body(request)
    source, target = data["source"], data["target"]
    src, dst = await _open_pair(source, target)
    try:
        return ok(await asyncio.to_thread(dbsync.preview_data, src, dst, source, target))
    finally:
        src.close()
        dst.close()


async def db_sync_data(request: web.Request) -> web.Response:
    data = await body(request)
    source, target = data["source"], data["target"]
    src, dst = await _open_pair(source, target)
    try:
        result = await asyncio.to_thread(
            dbsync.copy_data,
            src, dst, source, target,
            data.get("mode") or "append",
            int(data.get("limit") or 10000),
            data.get("where") or None,
        )
        STATE.store.add_history("sync", f"数据同步 · {source['table']} → {target['table']}", target["assetId"], f"{result['inserted']} 行")
        return ok(result)
    finally:
        src.close()
        dst.close()


# --------------------------------------------------------------- transfer
async def file_transfer(request: web.Request) -> web.Response:
    data = await body(request)
    from_asset = data.get("fromAssetId")
    to_asset = data.get("toAssetId")
    src_path = data.get("fromPath")
    dst_path = data.get("toPath")
    if not from_asset or not to_asset or not src_path or not dst_path:
        return json_response({"error": "缺少源/目标资产或路径"}, status=400)
    if from_asset == to_asset and src_path == dst_path:
        return json_response({"error": "源与目标相同"}, status=400)
    if data.get("background"):
        async def job():
            async with STATE.hub.sftp(from_asset) as src_sftp:
                async with STATE.hub.sftp(to_asset) as dst_sftp:
                    return await asyncio.to_thread(
                        transfer_svc.transfer,
                        src_sftp, dst_sftp, src_path, dst_path, bool(data.get("overwrite", True)),
                    )

        task = STATE.tasks.submit("transfer", f"跨服务器传输 · {os.path.basename(src_path)}", job)
        return ok({"task": task})
    async with STATE.hub.sftp(from_asset) as src_sftp:
        async with STATE.hub.sftp(to_asset) as dst_sftp:
            stats = await asyncio.to_thread(
                transfer_svc.transfer,
                src_sftp, dst_sftp, src_path, dst_path, bool(data.get("overwrite", True)),
            )
    STATE.store.add_history("transfer", f"跨服务器传输 · {os.path.basename(src_path)}", to_asset, f"{stats['files']} 个文件 / {stats['bytes']} 字节")
    return ok(stats)


# -------------------------------------------------------------- broadcast
async def sessions_broadcast(request: web.Request) -> web.Response:
    data = await body(request)
    session_ids = [sid for sid in (data.get("sessionIds") or []) if sid]
    if not session_ids:
        return json_response({"error": "请选择至少一个会话"}, status=400)
    if data.get("encoding") == "base64":
        payload = b64decode_str(data.get("data") or "")
    else:
        payload = str(data.get("command") or data.get("data") or "").encode()
        if data.get("addNewline", True) and payload and not payload.endswith(b"\n"):
            payload += b"\n"
    if not payload:
        return json_response({"error": "内容为空"}, status=400)
    results = []
    for sid in session_ids[:50]:
        session = STATE.hub.sessions.get(sid)
        if not session:
            results.append({"sessionId": sid, "ok": False, "error": "会话不存在或已结束"})
            continue
        try:
            await session.write(payload)
            results.append({"sessionId": sid, "ok": True, "title": session.title, "kind": session.kind})
        except Exception as exc:
            results.append({"sessionId": sid, "ok": False, "error": str(exc)})
    return ok({"results": results, "sent": sum(1 for r in results if r["ok"])})


# -------------------------------------------------------------- key mgmt
async def keys_generate(request: web.Request) -> web.Response:
    data = await body(request)
    result = await asyncio.to_thread(
        key_svc.generate_keypair,
        data.get("type") or "ed25519",
        int(data.get("bits") or 3072),
        data.get("comment") or "",
        data.get("passphrase") or None,
    )
    saved = None
    if data.get("saveAsset", True):
        name = data.get("name") or f"密钥 {result['fingerprint'][-12:]}"
        asset = STATE.store.create_asset(
            1,
            name,
            data.get("parentId"),
            {
                "privateKey": result["privateKey"],
                "publicKey": result["publicKey"],
                "keyType": result["type"],
                "fingerprint": result["fingerprint"],
                "passphrase": data.get("passphrase") or "",
            },
        )
        saved = {"id": asset["id"], "name": asset["name"]}
    return ok(
        {
            "publicKey": result["publicKey"],
            "fingerprint": result["fingerprint"],
            "type": result["type"],
            "asset": saved,
        }
    )


async def ssh_copy_id(request: web.Request) -> web.Response:
    data = await body(request)
    asset_id = data.get("assetId")
    if not asset_id:
        return json_response({"error": "缺少 SSH 资产"}, status=400)
    public_key = (data.get("publicKey") or "").strip()
    if not public_key and data.get("keyAssetId"):
        key_asset = STATE.store.get_asset(data["keyAssetId"], include_secrets=True)
        if not key_asset:
            return json_response({"error": "密钥资产不存在"}, status=404)
        cfg = key_asset.get("config") or {}
        public_key = cfg.get("publicKey") or ""
        if not public_key and cfg.get("privateKey"):
            public_key = await asyncio.to_thread(
                key_svc.public_from_private, cfg.get("privateKey") or "", cfg.get("passphrase") or None
            )
    if not public_key:
        return json_response({"error": "没有可复制的公钥"}, status=400)
    transport, transports = await STATE.hub.open_ssh_transport(asset_id)
    try:
        result = await asyncio.to_thread(key_svc.copy_public_key, transport, public_key)
    finally:
        for hop_transport in reversed(transports):
            try:
                hop_transport.close()
            except Exception:
                pass
    STATE.store.add_history("key", "复制公钥到主机", asset_id, "added" if result.get("added") else "exists")
    return ok(result)



# ------------------------------------------------------------------ tasks
async def tasks_list(request: web.Request) -> web.Response:
    return ok({"tasks": STATE.tasks.list(int(request.query.get("limit") or 50))})


async def task_get(request: web.Request) -> web.Response:
    task = STATE.tasks.get(request.match_info["task_id"])
    if not task:
        return json_response({"error": "任务不存在"}, status=404)
    return ok({"task": task})


async def task_cancel(request: web.Request) -> web.Response:
    return ok({"cancelled": STATE.tasks.cancel(request.match_info["task_id"])})



# ---------------------------------------------------------------- tunnels
async def tunnels_list(request: web.Request) -> web.Response:
    return ok({"tunnels": STATE.tunnels.list()})


async def tunnel_start(request: web.Request) -> web.Response:
    data = await body(request)
    status = await STATE.tunnels.start(data["assetId"])
    STATE.store.add_history("tunnel", f"启动隧道 · {status.get('name')}", data["assetId"], f"{status.get('type')} {status.get('bindHost')}:{status.get('bindPort')}")
    return ok(status)


async def tunnel_stop(request: web.Request) -> web.Response:
    closed = await STATE.tunnels.stop(request.match_info["tunnel_id"])
    return ok({"stopped": closed})


async def tunnel_probe(request: web.Request) -> web.Response:
    data = await body(request)
    tunnel = None
    if data.get("tunnelId"):
        tunnel = STATE.tunnels.get(data["tunnelId"])
        if not tunnel:
            return json_response({"error": "隧道不存在或未运行"}, status=404)
    host = data.get("host") or (tunnel.bind_host if tunnel else "127.0.0.1")
    port = int(data.get("port") or (tunnel.bind_port if tunnel else 0))
    if not port:
        return json_response({"error": "缺少端口"}, status=400)
    payload = (data.get("payload") or "").encode()
    timeout = float(data.get("timeout") or 5)

    def probe() -> dict:
        started = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            if payload:
                sock.sendall(payload)
            try:
                banner = sock.recv(4096)
            except socket.timeout:
                banner = b""
        return {
            "ok": True,
            "host": host,
            "port": port,
            "elapsed": int((time.perf_counter() - started) * 1000),
            "banner": banner.decode("utf-8", "replace")[:500],
            "bytes": len(banner),
        }

    try:
        return ok(await asyncio.to_thread(probe))
    except OSError as exc:
        return json_response({"error": f"无法连接 {host}:{port}：{exc}"}, status=400)


# --------------------------------------------------------- chrome proxy
async def chrome_proxy_list(request: web.Request) -> web.Response:
    return ok(STATE.chrome_proxy.info())


async def chrome_proxy_start(request: web.Request) -> web.Response:
    data = await body(request)
    asset_id = (data.get("assetId") or "").strip()
    if not asset_id:
        return json_response({"error": "缺少资产 ID"}, status=400)
    session = await STATE.chrome_proxy.start(asset_id, data.get("url") or "")
    STATE.store.add_history(
        "chrome",
        f"通过服务器代理 Chrome · {session.get('assetName')}",
        asset_id,
        session.get("proxy") or "",
    )
    return ok({"session": session})


async def chrome_proxy_stop(request: web.Request) -> web.Response:
    status = await STATE.chrome_proxy.stop(request.match_info["session_id"])
    if not status:
        return json_response({"error": "Chrome 代理实例不存在或已结束"}, status=404)
    return ok({"stopped": True, "session": status})



# ------------------------------------------------------------------- scp
async def scp_upload(request: web.Request) -> web.Response:
    asset_id = request.query.get("assetId")
    remote = request.query.get("path")
    if not asset_id or not remote:
        return json_response({"error": "参数错误（assetId / path）"}, status=400)
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-scp-up-", delete=False)
    total = 0
    try:
        async for chunk in request.content.iter_chunked(262144):
            tmp.write(chunk)
            total += len(chunk)
        tmp.close()
        async with STATE.hub.ssh_transport(asset_id) as transport:
            client = SCPClient(transport)
            result = await asyncio.to_thread(client.upload, tmp.name, remote)
        STATE.store.add_history("scp", f"SCP 上传 · {os.path.basename(remote)}", asset_id, f"{result.get('size', total)} bytes")
        return ok({"path": remote, "size": result.get("size", total)})
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def scp_download(request: web.Request) -> web.StreamResponse:
    data = await body(request)
    asset_id = data["assetId"]
    remote = data["path"]
    name = safe_filename(os.path.basename(remote.rstrip("/")) or "download")
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-scp-dl-", delete=False)
    tmp.close()
    try:
        async with STATE.hub.ssh_transport(asset_id) as transport:
            client = SCPClient(transport)
            result = await asyncio.to_thread(client.download, remote, tmp.name)
        size = os.path.getsize(tmp.name)
        STATE.store.add_history("scp", f"SCP 下载 · {name}", asset_id, f"{size} bytes")
        resp = web.StreamResponse(
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{name}"',
                "Content-Length": str(size),
            }
        )
        await resp.prepare(request)
        with open(tmp.name, "rb") as fh:
            while True:
                chunk = fh.read(262144)
                if not chunk:
                    break
                await resp.write(chunk)
        await resp.write_eof()
        return resp
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ------------------------------------------------------- docker image files
async def docker_image_save(request: web.Request) -> web.StreamResponse:
    data = await body(request)
    service, transport = await STATE.hub.docker(data.get("assetId"))
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-image-", suffix=".tar", delete=False)
    tmp.close()
    try:
        size = await asyncio.to_thread(service.save_image, data["image"], tmp.name)
        name = safe_filename((data.get("image") or "image").replace("/", "_").replace(":", "_") + ".tar")
        resp = web.StreamResponse(
            headers={
                "Content-Type": "application/x-tar",
                "Content-Disposition": f'attachment; filename="{name}"',
                "Content-Length": str(size),
            }
        )
        await resp.prepare(request)
        with open(tmp.name, "rb") as fh:
            while True:
                chunk = fh.read(262144)
                if not chunk:
                    break
                await resp.write(chunk)
        await resp.write_eof()
        return resp
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if transport:
            transport.close()


async def docker_image_load(request: web.Request) -> web.Response:
    asset_id = request.query.get("assetId") or None
    service, transport = await STATE.hub.docker(asset_id)
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-image-load-", suffix=".tar", delete=False)
    total = 0
    try:
        async for chunk in request.content.iter_chunked(262144):
            tmp.write(chunk)
            total += len(chunk)
        tmp.close()
        result = await asyncio.to_thread(service.load_image, tmp.name)
        return ok({"size": total, **result})
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if transport:
            transport.close()


# ------------------------------------------------------- docker file transfer
async def docker_fs_download(request: web.Request) -> web.StreamResponse:
    data = await body(request)
    service, transport = await STATE.hub.docker(data.get("assetId"))
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-docker-dl-", delete=False)
    tmp.close()
    try:
        container = data["container"]
        remote = data["path"]
        size = await asyncio.to_thread(service.fs_download_to, container, remote, tmp.name)
        name = safe_filename(os.path.basename(remote.rstrip("/")) or "download")
        resp = web.StreamResponse(
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{name}"',
                "Content-Length": str(size),
            }
        )
        await resp.prepare(request)
        with open(tmp.name, "rb") as fh:
            while True:
                chunk = fh.read(262144)
                if not chunk:
                    break
                await resp.write(chunk)
        await resp.write_eof()
        return resp
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if transport:
            transport.close()


async def docker_fs_upload(request: web.Request) -> web.Response:
    asset_id = request.query.get("assetId") or None
    container = request.query.get("container")
    remote = request.query.get("path")
    if not container or not remote:
        return json_response({"error": "参数错误（container / path）"}, status=400)
    service, transport = await STATE.hub.docker(asset_id)
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-docker-up-", delete=False)
    total = 0
    try:
        async for chunk in request.content.iter_chunked(262144):
            tmp.write(chunk)
            total += len(chunk)
        tmp.close()
        size = await asyncio.to_thread(service.fs_upload_from, container, tmp.name, remote)
        return ok({"path": remote, "size": size or total})
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if transport:
            transport.close()


# ------------------------------------------------------------------- ftp
async def _ftp_call(asset_id: str, fn, *args, **kwargs):
    client = await STATE.hub.open_ftp(asset_id)
    try:
        return await asyncio.to_thread(fn, client, *args, **kwargs)
    finally:
        await asyncio.to_thread(client.close)


async def ftp_list(request: web.Request) -> web.Response:
    data = await body(request)
    result = await _ftp_call(
        data["assetId"], FTPClient.list_dir, data.get("path") or "~", bool(data.get("showHidden", True))
    )
    return ok(result)


async def ftp_mkdir(request: web.Request) -> web.Response:
    data = await body(request)
    await _ftp_call(data["assetId"], FTPClient.mkdir, data["path"])
    return ok()


async def ftp_rename(request: web.Request) -> web.Response:
    data = await body(request)
    await _ftp_call(data["assetId"], FTPClient.rename, data["path"], data["target"])
    return ok()


async def ftp_delete(request: web.Request) -> web.Response:
    data = await body(request)
    await _ftp_call(data["assetId"], FTPClient.delete, data["path"])
    return ok()


async def ftp_read(request: web.Request) -> web.Response:
    data = await body(request)
    result = await _ftp_call(data["assetId"], FTPClient.read_text, data["path"])
    return ok(result)


async def ftp_write(request: web.Request) -> web.Response:
    data = await body(request)
    result = await _ftp_call(
        data["assetId"], FTPClient.write_text, data["path"], data.get("content") or "", data.get("encoding") or "utf-8"
    )
    return ok(result)


async def ftp_download(request: web.Request) -> web.StreamResponse:
    data = await body(request)
    asset_id = data["assetId"]
    remote = data["path"]
    name = safe_filename(os.path.basename(remote.rstrip("/")) or "download")
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-ftp-", delete=False)
    tmp.close()
    try:
        size = await _ftp_call(asset_id, FTPClient.download_to, remote, tmp.name)
        resp = web.StreamResponse(
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{name}"',
                "Content-Length": str(size),
            }
        )
        await resp.prepare(request)
        with open(tmp.name, "rb") as fh:
            while True:
                chunk = fh.read(262144)
                if not chunk:
                    break
                await resp.write(chunk)
        await resp.write_eof()
        return resp
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def ftp_upload(request: web.Request) -> web.Response:
    asset_id = request.query.get("assetId")
    remote = request.query.get("path")
    if not asset_id or not remote:
        return json_response({"error": "参数错误"}, status=400)
    tmp = tempfile.NamedTemporaryFile(prefix="h1khub-ftp-up-", delete=False)
    total = 0
    try:
        async for chunk in request.content.iter_chunked(262144):
            tmp.write(chunk)
            total += len(chunk)
        tmp.close()
        size = await _ftp_call(asset_id, FTPClient.upload_from, tmp.name, remote)
        return ok({"path": remote, "size": size or total})
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# -------------------------------------------------------------- batch exec
async def batch_exec(request: web.Request) -> web.Response:
    data = await body(request)
    asset_ids = [a for a in (data.get("assetIds") or []) if a][:50]
    command = (data.get("command") or "").strip()
    timeout = int(data.get("timeout") or 60)
    if not asset_ids:
        return json_response({"error": "请选择至少一个 SSH 资产"}, status=400)
    if not command:
        return json_response({"error": "命令不能为空"}, status=400)
    if len(command) > 4000:
        return json_response({"error": "命令过长"}, status=400)

    async def run_one(asset_id: str) -> dict:
        asset = STATE.store.get_asset(asset_id)
        if not asset:
            return {"assetId": asset_id, "name": asset_id, "ok": False, "error": "资产不存在"}
        if asset["type"] != 2:
            return {"assetId": asset_id, "name": asset["name"], "ok": False, "error": "不是 SSH 资产"}
        started = time.perf_counter()
        try:
            result = await STATE.hub.ssh_exec(asset_id, command, timeout)
            return {
                "assetId": asset_id,
                "name": asset["name"],
                "ok": result["exitCode"] == 0,
                "exitCode": result["exitCode"],
                "stdout": clamp_text(result["stdout"], 200_000),
                "stderr": clamp_text(result["stderr"], 200_000),
                "elapsed": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            return {
                "assetId": asset_id,
                "name": asset["name"],
                "ok": False,
                "error": str(exc),
                "elapsed": int((time.perf_counter() - started) * 1000),
            }

    results = await asyncio.gather(*[run_one(aid) for aid in asset_ids])
    STATE.store.add_history("batch", f"批量执行 · {len(results)} 台", None, command[:200])
    return ok({"results": results, "command": command})


# ------------------------------------------------------------------ docker
async def docker_api(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    data = await body(request)
    service, transport = await STATE.hub.docker(data.get("assetId"))
    try:
        if action == "containers":
            return ok({"containers": await asyncio.to_thread(service.containers, bool(data.get("all", True)))})
        if action == "images":
            return ok({"images": await asyncio.to_thread(service.images)})
        if action == "volumes":
            return ok({"volumes": await asyncio.to_thread(service.volumes)})
        if action == "networks":
            return ok({"networks": await asyncio.to_thread(service.networks)})
        if action == "logs":
            return ok({"logs": await asyncio.to_thread(service.logs, data["container"], int(data.get("tail") or 500))})
        if action == "inspect":
            return ok({"inspect": await asyncio.to_thread(service.inspect, data["container"])})
        if action == "stats":
            return ok({"stats": await asyncio.to_thread(service.stats)})
        if action == "action":
            return ok(await asyncio.to_thread(service.action, data["container"], data["action"], bool(data.get("force"))))
        if action == "image-remove":
            return ok(await asyncio.to_thread(service.remove_image, data["image"]))
        if action == "pull":
            if data.get("background"):
                async def pull_job(asset_id=data.get("assetId"), image=data["image"]):
                    svc, tr = await STATE.hub.docker(asset_id)
                    try:
                        return await asyncio.to_thread(svc.pull, image)
                    finally:
                        if tr:
                            tr.close()

                return ok({"task": STATE.tasks.submit("docker-pull", f"拉取镜像 {data['image']}", pull_job)})
            return ok(await asyncio.to_thread(service.pull, data["image"]))
        if action == "test":
            return ok(await asyncio.to_thread(service.test))
        if action == "container-create":
            return ok(
                await asyncio.to_thread(
                    service.create,
                    data.get("image") or "",
                    data.get("name") or "",
                    data.get("command") or "",
                    data.get("ports") or [],
                    data.get("env") or {},
                    data.get("volumes") or [],
                    data.get("restart") or "",
                    data.get("network") or "",
                    bool(data.get("start", True)),
                )
            )
        if action == "container-rename":
            return ok(await asyncio.to_thread(service.rename, data["container"], data["name"]))
        if action == "container-top":
            return ok(await asyncio.to_thread(service.top, data["container"]))
        if action == "registry-login":
            return ok(
                await asyncio.to_thread(
                    service.registry_login,
                    data.get("registry") or "",
                    data.get("username") or "",
                    data.get("password") or "",
                )
            )
        if action == "compose-read":
            return ok(await asyncio.to_thread(service.compose_read, data.get("dir") or "", data.get("file") or "docker-compose.yml"))
        if action == "compose-write":
            return ok(
                await asyncio.to_thread(
                    service.compose_write,
                    data.get("dir") or "",
                    data.get("content") or "",
                    data.get("file") or "docker-compose.yml",
                )
            )
        if action == "compose-action":
            return ok(
                await asyncio.to_thread(
                    service.compose_action,
                    data.get("dir") or "",
                    data.get("action") or "up",
                    data.get("file") or "docker-compose.yml",
                    data.get("services") or [],
                )
            )
        if action == "compose-ps":
            return ok(await asyncio.to_thread(service.compose_ps, data.get("dir") or "", data.get("file") or "docker-compose.yml"))
        if action == "fs-list":
            return ok(await asyncio.to_thread(service.fs_list, data["container"], data.get("path") or "/"))
        if action == "fs-read":
            return ok(await asyncio.to_thread(service.fs_read, data["container"], data["path"]))
        if action == "fs-write":
            return ok(
                await asyncio.to_thread(
                    service.fs_write, data["container"], data["path"], data.get("content") or "", data.get("encoding") or "utf-8"
                )
            )
        if action == "fs-delete":
            return ok(await asyncio.to_thread(service.fs_delete, data["container"], data["path"]))
        if action == "fs-mkdir":
            return ok(await asyncio.to_thread(service.fs_mkdir, data["container"], data["path"]))
        if action == "fs-rename":
            return ok(await asyncio.to_thread(service.fs_rename, data["container"], data["path"], data["target"]))
        if action == "push":
            return ok(await asyncio.to_thread(service._exec, ["push", data["image"]], 900))
        if action == "image-build":
            return ok(
                await asyncio.to_thread(
                    service.build_image,
                    data.get("tag") or "",
                    data.get("dir") or "",
                    data.get("dockerfile") or "Dockerfile",
                    bool(data.get("noCache")),
                )
            )
        if action == "image-history":
            return ok({"history": await asyncio.to_thread(service.image_history, data["image"])})
        if action == "image-prune":
            return ok(await asyncio.to_thread(service.prune_images, bool(data.get("all"))))
        return json_response({"error": f"未知 Docker 操作：{action}"}, status=404)
    finally:
        if transport:
            transport.close()


# ------------------------------------------------------- local OCI registry
async def docker_local_registry(request: web.Request) -> web.Response:
    """Publish local images into a bundled OCI registry and pull them back."""
    data = await body(request)
    action = data.get("action") or "status"
    try:
        if action == "status":
            return ok(REGISTRY.status())
        if action == "start":
            port = int(data.get("port") or 5050)
            if not (1024 <= port <= 65535):
                return json_response({"error": "端口需在 1024-65535 之间"}, status=400)
            return ok(await asyncio.to_thread(REGISTRY.start, port))
        if action == "stop":
            return ok(await asyncio.to_thread(REGISTRY.stop))
        if action == "publish":
            return ok(
                await asyncio.to_thread(
                    REGISTRY.publish,
                    data.get("image") or "",
                    data.get("repo") or "",
                    data.get("tag") or "latest",
                )
            )
        if action == "pull":
            status = REGISTRY.status()
            if not status["running"]:
                return json_response({"error": "本地 registry 未启动"}, status=400)
            repo = (data.get("repo") or "").strip()
            tag = (data.get("tag") or "latest").strip() or "latest"
            if not repo:
                return json_response({"error": "仓库名不能为空"}, status=400)
            ref = REGISTRY.image_ref(repo, tag)
            service, transport = await STATE.hub.docker(data.get("assetId"))
            try:
                if getattr(service, "mode", "local") != "local":
                    return json_response(
                        {"error": "本地 registry 仅支持本机 Docker（远程 Docker 无法访问 127.0.0.1）"},
                        status=400,
                    )
                result = await asyncio.to_thread(service.pull, ref)
                inspect = await asyncio.to_thread(service._exec, ["image", "inspect", ref, "--format", "{{.Id}}|{{.Size}}"], 60)
                image_id, size = (inspect.strip().split("|", 1) + [""])[:2]
                return ok({"image": ref, "result": result, "imageId": image_id, "size": int(size or 0)})
            finally:
                if transport:
                    transport.close()
        return json_response({"error": f"未知本地仓库操作：{action}"}, status=404)
    except registry_svc.RegistryError as exc:
        return json_response({"error": str(exc)}, status=400)
    except DockerError as exc:
        return json_response({"error": str(exc)}, status=400)


# ---------------------------------------------------------------- settings
def _encrypt_secret(value: str) -> str:
    if not value or value.startswith("enc:v1:"):
        return value
    return "enc:v1:" + STATE.store.box.encrypt(value.encode())


def _decrypt_secret(value: str) -> str:
    if not value or not value.startswith("enc:v1:"):
        return value
    try:
        return STATE.store.box.decrypt(value[7:]).decode()
    except Exception:
        return ""


def ai_settings_public(settings: dict) -> dict:
    ai = settings.get("ai") or {}
    providers = []
    for p in ai.get("providers") or []:
        item = dict(p)
        item["hasKey"] = bool(p.get("apiKey"))
        item["apiKey"] = "" if p.get("apiKey") else ""
        providers.append(item)
    ai = dict(ai)
    ai["providers"] = providers
    settings = dict(settings)
    settings["ai"] = ai
    return settings


def settings_public(settings: dict) -> dict:
    """Wire-safe settings: strip every stored secret, including the lock hash.

    The lock password hash is never returned to any client; callers read
    ``hasPassword`` from GET /api/lock/status instead.
    """
    out = ai_settings_public(settings)
    lock = out.get("lock")
    if isinstance(lock, dict):
        out = dict(out)
        out["lock"] = {k: v for k, v in lock.items() if k != "passwordHash"}
    return out


def ai_settings_private(settings: dict) -> dict:
    """Decrypt provider keys for server-side use."""
    ai = dict(settings.get("ai") or {})
    providers = []
    for p in ai.get("providers") or []:
        item = dict(p)
        item["apiKey"] = _decrypt_secret(p.get("apiKey") or "")
        providers.append(item)
    ai["providers"] = providers
    settings["ai"] = ai
    return settings


async def settings_get(request: web.Request) -> web.Response:
    settings = STATE.store.get_settings()
    return ok(settings_public(settings))


async def settings_update(request: web.Request) -> web.Response:
    data = await body(request)
    if "ai" in data and isinstance(data["ai"], dict):
        ai_patch = dict(data["ai"])
        if isinstance(ai_patch.get("providers"), list):
            existing = {p.get("id"): p for p in (STATE.store.get_settings().get("ai", {}).get("providers") or [])}
            providers = []
            for p in ai_patch["providers"]:
                item = dict(p)
                key = item.get("apiKey") or ""
                if key:
                    item["apiKey"] = _encrypt_secret(key)
                else:
                    old = existing.get(item.get("id"))
                    item["apiKey"] = (old or {}).get("apiKey") or ""
                providers.append(item)
            ai_patch["providers"] = providers
        data["ai"] = ai_patch
    # Update channel/env/manifest/autoCheck are persisted here as well (the
    # settings dialog saves the whole draft), but only after the same
    # whitelist/scheme validation the dedicated /api/update/channel endpoint
    # uses.  An invalid channel can never be stored.
    if "update" in data and isinstance(data.get("update"), dict):
        try:
            clean_update = update_svc.validate_settings_patch(data["update"])
        except update_svc.UpdateError as exc:
            return update_error(exc, "update.channel")
        if clean_update:
            data["update"] = clean_update
        else:
            data.pop("update", None)
    # Lock configuration and the password hash have dedicated endpoints so
    # that changing a password always requires the old one.  A generic settings
    # PUT must never be able to set or clear the hash.
    data.pop("lock", None)
    settings = STATE.store.update_settings(data)
    return ok(settings_public(settings))


# ------------------------------------------------------------------ history
async def history_list(request: web.Request) -> web.Response:
    return ok({"history": STATE.store.list_history(int(request.query.get("limit") or 30))})


async def history_clear(request: web.Request) -> web.Response:
    STATE.store.clear_history()
    return ok()


async def commands_list(request: web.Request) -> web.Response:
    return ok({"commands": STATE.store.list_commands(request.query.get("assetId"), int(request.query.get("limit") or 100))})


async def commands_add(request: web.Request) -> web.Response:
    data = await body(request)
    STATE.store.add_command(data.get("command") or "", data.get("assetId"), data.get("source") or "shell")
    return ok()


# --------------------------------------------------------------------- chat
async def chats_list(request: web.Request) -> web.Response:
    return ok({"chats": STATE.store.list_chats()})


async def chats_create(request: web.Request) -> web.Response:
    data = await body(request)
    return ok({"chat": STATE.store.create_chat(data.get("title") or "新对话", data.get("assetId"))})


async def chats_delete(request: web.Request) -> web.Response:
    STATE.store.delete_chat(request.match_info["chat_id"])
    return ok()


async def chat_messages(request: web.Request) -> web.Response:
    chat_id = request.match_info["chat_id"]
    return ok({"messages": STATE.store.get_chat_messages(chat_id)})


# ----------------------------------------------------------------------- ai
async def ai_models(request: web.Request) -> web.Response:
    data = await body(request)
    settings = ai_settings_private(STATE.store.get_settings())
    provider = _provider_from_request(settings, data)
    if not provider:
        return json_response({"error": "未选择模型供应商"}, status=400)
    url, headers = provider_urls(provider)
    if provider.get("protocol") == "anthropic":
        return ok({"models": provider.get("models") or ["claude-sonnet-4-5", "claude-opus-4-1", "claude-3-5-haiku-latest"]})
    base = url.split("/chat/completions")[0].rstrip("/")
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(base + "/models", headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise AIError(f"获取模型列表失败（{resp.status}）：{text[:300]}")
                payload = await resp.json()
                models = [m.get("id") for m in payload.get("data", []) if m.get("id")]
                return ok({"models": models})
    except Exception as exc:
        return json_response({"error": str(exc)}, status=400)


async def ai_test(request: web.Request) -> web.Response:
    data = await body(request)
    settings = ai_settings_private(STATE.store.get_settings())
    provider = _provider_from_request(settings, data)
    if not provider:
        return json_response({"error": "未选择模型供应商"}, status=400)
    model = data.get("model") or provider.get("defaultModel") or (provider.get("models") or [None])[0]
    if not model:
        return json_response({"error": "未选择模型"}, status=400)
    url, headers = provider_urls(provider)
    import aiohttp

    if provider.get("protocol") == "anthropic":
        payload = {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "ping"}]}
    else:
        payload = {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "ping"}]}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise AIError(f"{resp.status}: {clamp_text(text, 500)}")
                return ok({"message": "模型连通正常", "model": model})
    except Exception as exc:
        return json_response({"error": str(exc)}, status=400)


def _provider_from_request(settings: dict, data: dict) -> dict | None:
    """Allow testing unsaved provider drafts by merging them over the stored entry."""
    inline = data.get("provider")
    if isinstance(inline, dict) and inline.get("id"):
        base = _find_provider(settings, inline["id"]) or {}
        merged = {**base, **inline}
        if not merged.get("apiKey"):
            merged["apiKey"] = base.get("apiKey") or ""
        return merged
    return _find_provider(settings, data.get("providerId"))


def _find_provider(settings: dict, provider_id: str | None) -> dict | None:
    providers = (settings.get("ai") or {}).get("providers") or []
    if not provider_id:
        provider_id = (settings.get("ai") or {}).get("activeProviderId")
    for p in providers:
        if p.get("id") == provider_id:
            return dict(p)
    return None


# ------------------------------------------------------------------ rpc ws
class RpcConnection:
    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws
        self.channels: dict[str, dict] = {}
        self.send_lock = asyncio.Lock()
        # Binary Packet transport (original framing); JSON stays supported.
        self.binary = False
        self.channel_ids: dict[str, int] = {}
        self.channel_names: dict[int, str] = {}
        self.next_channel_id = 1

    def bind_channel(self, name: str) -> int:
        cid = self.channel_ids.get(name)
        if cid:
            return cid
        cid = self.next_channel_id
        self.next_channel_id += 1
        self.channel_ids[name] = cid
        self.channel_names[cid] = name
        return cid

    async def send(self, payload: dict) -> None:
        async with self.send_lock:
            if self.ws.closed:
                return
            try:
                if self.binary:
                    await self.ws.send_bytes(self._binary_frame(payload))
                else:
                    await self.ws.send_str(json.dumps(payload, ensure_ascii=False, default=str))
            except ConnectionResetError:
                pass

    def _binary_frame(self, payload: dict) -> bytes:
        method = payload.get("method")
        if method == "welcome":
            return rpcproto.encode_packet("welcome", 0, json.dumps(payload.get("data") or {}, default=str).encode())
        if method == "open":
            name = payload.get("channel") or ""
            cid = self.bind_channel(name)
            body = {"method": name, "channelId": cid, "data": payload.get("data") or {}}
            return rpcproto.encode_packet("ChannelOpen", cid, json.dumps(body, default=str).encode())
        if method == "event":
            name = payload.get("channel") or ""
            cid = self.bind_channel(name)
            return rpcproto.encode_packet("ChannelSend", cid, json.dumps(payload.get("data") or {}, default=str).encode())
        if method == "error":
            name = payload.get("channel") or ""
            cid = self.channel_ids.get(name, 0)
            body = {"type": "error", "message": (payload.get("data") or {}).get("message") or ""}
            return rpcproto.encode_packet("ChannelSend", cid, json.dumps(body, default=str).encode())
        return rpcproto.encode_packet(str(method or "event"), int(payload.get("id") or 0), b"")

    async def send_packet(self, method: str, msg_id: int, data: bytes) -> None:
        async with self.send_lock:
            if self.ws.closed:
                return
            try:
                await self.ws.send_bytes(rpcproto.encode_packet(method, msg_id, data))
            except ConnectionResetError:
                pass

    async def close_channel(self, name: str) -> None:
        channel = self.channels.pop(name, None)
        if not channel:
            return
        task = channel.get("task")
        if task and not task.done():
            task.cancel()
        if channel.get("kind") == "term":
            queue = channel.get("queue")
            session = channel.get("session")
            if queue and session:
                session.detach(queue)
        if channel.get("kind") == "ai":
            runner = channel.get("runner")
            if runner:
                runner.cancel()


async def ws_ai_chat(conn: RpcConnection, channel_name: str, data: dict) -> None:
    channel = conn.channels.get(channel_name)
    if not channel:
        return

    async def emit(event: dict) -> None:
        await conn.send({"method": "event", "channel": channel_name, "data": event})

    settings_all = STATE.store.get_settings()
    private = ai_settings_private(settings_all)
    provider = _find_provider(private, data.get("providerId"))
    if not provider:
        await emit({"type": "error", "message": "未配置模型供应商，请在设置 → AI 中填写 API Key"})
        return
    model = data.get("model") or provider.get("defaultModel") or ""
    if not model:
        await emit({"type": "error", "message": "未选择模型"})
        return
    chat_id = data.get("chatId")
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        await emit({"type": "error", "message": "消息为空"})
        return
    if not chat_id:
        chat = STATE.store.create_chat(prompt[:40] or "新对话", (data.get("assetIds") or [None])[0])
        chat_id = chat["id"]
    else:
        existing = [c for c in STATE.store.list_chats(200) if c["id"] == chat_id]
        if not existing:
            chat = STATE.store.create_chat(prompt[:40] or "新对话", None)
            chat_id = chat["id"]
            await emit({"type": "chat", "chatId": chat_id})
        elif existing[0]["title"] in ("新对话", ""):
            STATE.store.rename_chat(chat_id, prompt[:40])

    history = STATE.store.get_chat_messages(chat_id)
    messages: list[dict] = [{"role": m["role"], "content": m["content"]} for m in history if m["role"] in ("user", "assistant")]
    messages.append({"role": "user", "content": prompt})
    STATE.store.add_chat_message(chat_id, "user", prompt)
    assets = STATE.store.list_assets()
    policy = (settings_all.get("agent") or {})
    assistant = (data.get("assistant") or "general").lower()
    extra_lines: list[str] = []
    asset_ids = [a for a in (data.get("assetIds") or []) if a]
    if asset_ids:
        extra_lines.append("用户本轮引用了以下资产：")
        for aid in asset_ids:
            asset = next((a for a in assets if a["id"] == aid), None)
            if asset:
                extra_lines.append(f"- {asset['name']} (id={aid}, type={asset['typeName']})")
    if assistant == "terminal":
        session = STATE.hub.sessions.get(data.get("sessionId") or "")
        if session:
            context = strip_ansi(bytes(session.buffer).decode("utf-8", "replace"))
            extra_lines.append(
                f"\n当前终端会话：{session.title} (id={session.id}, kind={session.kind})。最近输出：\n```\n"
                + context[-4000:]
                + "\n```"
            )
        extra_lines.append("\n可用终端会话（如需切换）：" + ", ".join(f"{s['id']}={s['title']}" for s in STATE.hub.sessions.list()[:20]))
    elif assistant == "sql":
        db_asset = next((a for a in assets if a["id"] == data.get("assetId")), None)
        if db_asset:
            extra_lines.append(
                f"\n当前数据库资产：{db_asset['name']} (id={db_asset['id']})，数据库：{data.get('database') or '(默认)'}"
            )
        if data.get("sql"):
            extra_lines.append("\n用户当前编辑的 SQL：\n```sql\n" + str(data["sql"])[:4000] + "\n```")
    extra = "\n".join(extra_lines)
    system_prompt = build_system_prompt(assets, policy, extra)
    if assistant in ai.ASSISTANT_PROMPTS:
        system_prompt += "\n\n" + ai.ASSISTANT_PROMPTS[assistant]
    if (settings_all.get("ai") or {}).get("systemPrompt"):
        system_prompt += "\n\n额外指令：\n" + (settings_all["ai"]["systemPrompt"])
    runner = ChatRunner(STATE.hub, STATE.store, emit, policy)
    channel["runner"] = runner
    channel["chatId"] = chat_id
    await conn.send({"method": "event", "channel": channel_name, "data": {"type": "chat", "chatId": chat_id}})
    try:
        result = await runner.run(
            provider,
            model,
            messages,
            system_prompt,
            int(policy.get("maxToolRounds") or 12),
            tools=ai.tools_for(assistant),
        )
        STATE.store.add_chat_message(
            chat_id,
            "assistant",
            result["content"],
            {"usage": result.get("usage"), "reasoning": result.get("reasoning"), "model": model},
        )
    except AIError as exc:
        if "取消" not in str(exc):
            await emit({"type": "error", "message": str(exc)})
    except asyncio.CancelledError:
        runner.cancel()
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("ai chat failed")
        await emit({"type": "error", "message": f"对话失败：{type(exc).__name__}: {exc}"})


async def _rpc_query(payload: dict):
    adapter = await _with_db(payload["assetId"])
    try:
        result = await asyncio.to_thread(adapter.query, payload.get("sql") or "", payload.get("database"))
    finally:
        adapter.close()
    first = (result.get("results") or [{}])[0]
    columns = first.get("columns") or []
    types = first.get("columnTypes") or [""] * len(columns)
    rows = first.get("rows") or []
    return columns, types, rows, first.get("rowCount") or len(rows), 0


async def _rpc_binary(conn: RpcConnection, packet: dict) -> None:
    """Handle one XOR'd binary Packet (original HexHub framing)."""
    method = packet["method"]
    msg_id = int(packet["id"])
    raw = packet["data"]
    try:
        if method == "ChannelOpen":
            name, args = "", {}
            try:
                body = json.loads(raw.decode() or "{}")
            except Exception:
                body = {}
            if isinstance(body, dict) and body.get("method"):
                name = str(body["method"])
                args = body.get("args") or {}
            else:
                inner = rpcproto.decode_packet(raw)
                name = inner["method"]
                try:
                    args = json.loads(inner["data"].decode() or "{}")
                except Exception:
                    args = {}
            conn.bind_channel(name)
            await handle_open(conn, name, args)
            return
        if method == "ChannelSend":
            name = conn.channel_names.get(msg_id, "")
            if not name:
                return
            try:
                payload = json.loads(raw.decode() or "{}")
            except Exception:
                payload = {}
            await handle_send(conn, name, payload)
            return
        if method == "ChannelClose":
            name = conn.channel_names.get(msg_id, "")
            if name:
                await conn.close_channel(name)
                conn.channel_names.pop(msg_id, None)
                conn.channel_ids.pop(name, None)
            return
        if method == "ping":
            await conn.send_packet("pong", msg_id, json.dumps({"ts": now_iso()}).encode())
            return
        if method == "db.query":
            payload = json.loads(raw.decode() or "{}")
            columns, types, rows, total, offset = await _rpc_query(payload)
            page = max(len(rows), 1)
            await conn.send_packet(
                "db.query", msg_id, rpcproto.encode_table_data(columns, types, rows, offset, total, page)
            )
            return
        if method == "db.tableData":
            payload = json.loads(raw.decode() or "{}")
            adapter = await _with_db(payload["assetId"])
            try:
                detail = await asyncio.to_thread(
                    adapter.table_detail,
                    payload.get("database"),
                    payload.get("schema"),
                    payload["table"],
                    int(payload.get("limit") or 500),
                    int(payload.get("offset") or 0),
                    payload.get("orderBy"),
                    payload.get("orderDir") or "ASC",
                    payload.get("where"),
                )
            finally:
                adapter.close()
            columns = [c["name"] for c in detail["columns"]]
            types = [c["type"] for c in detail["columns"]]
            body = rpcproto.encode_table_data(
                columns,
                types,
                detail["rows"],
                int(detail.get("offset") or 0),
                int(detail.get("total") or 0),
                int(payload.get("limit") or 500),
            )
            await conn.send_packet("db.tableData", msg_id, body)
            return
        await conn.send_packet(method, msg_id, b"")
    except Exception as exc:
        log.exception("binary rpc error")
        await conn.send_packet("error", msg_id, f"{type(exc).__name__}: {exc}".encode())


async def rpc_handler(request: web.Request) -> web.WebSocketResponse:
    token = request.query.get("token") or ""
    if not secrets.compare_digest(token, STATE.token):
        return web.Response(status=401, text="session invalid")
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=8 * 1024 * 1024)
    await ws.prepare(request)
    conn = RpcConnection(ws)
    await conn.send({"method": "welcome", "data": {"version": APP_VERSION, "packet": RPC_PACKET_VERSION}})
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                conn.binary = True
                try:
                    packet = rpcproto.decode_packet(bytes(msg.data))
                except Exception:
                    continue
                await _rpc_binary(conn, packet)
                continue
            if msg.type == WSMsgType.TEXT:
                try:
                    packet = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                method = packet.get("method")
                channel_name = packet.get("channel") or ""
                data = packet.get("data") or {}
                try:
                    if method == "open":
                        await handle_open(conn, channel_name, data)
                    elif method == "send":
                        await handle_send(conn, channel_name, data)
                    elif method == "close":
                        await conn.close_channel(channel_name)
                    elif method == "ping":
                        await conn.send({"method": "pong", "data": {"ts": now_iso(), "id": packet.get("id")}})
                except Exception as exc:
                    log.exception("rpc error")
                    await conn.send(
                        {"method": "error", "channel": channel_name, "data": {"message": f"{type(exc).__name__}: {exc}"}}
                    )
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        for name in list(conn.channels):
            await conn.close_channel(name)
    return ws


async def handle_open(conn: RpcConnection, name: str, data: dict) -> None:
    if name.startswith("term:"):
        session_id = name[5:]
        session = STATE.hub.sessions.get(session_id)
        if not session:
            await conn.send({"method": "error", "channel": name, "data": {"message": "会话不存在或已结束"}})
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        session.attach(queue)
        conn.channels[name] = {"kind": "term", "session": session, "queue": queue}
        await conn.send({"method": "open", "channel": name, "data": {"ok": True, "session": session.info()}})

        async def pump() -> None:
            try:
                while True:
                    event = await queue.get()
                    await conn.send({"method": "event", "channel": name, "data": event})
            except asyncio.CancelledError:
                return

        conn.channels[name]["task"] = asyncio.create_task(pump())
        return
    if name == "ai" or name.startswith("ai:"):
        conn.channels[name] = {"kind": "ai"}
        await conn.send({"method": "open", "channel": name, "data": {"ok": True}})
        return
    if name == "notifications":
        conn.channels[name] = {"kind": "notify"}
        await conn.send({"method": "open", "channel": name, "data": {"ok": True}})
        return
    await conn.send({"method": "error", "channel": name, "data": {"message": f"未知通道：{name}"}})


async def handle_send(conn: RpcConnection, name: str, data: dict) -> None:
    channel = conn.channels.get(name)
    if not channel:
        await conn.send({"method": "error", "channel": name, "data": {"message": "通道未打开"}})
        return
    if channel["kind"] == "term":
        session = channel["session"]
        msg_type = data.get("type")
        if msg_type == "input":
            payload = b64decode_str(data.get("data") or "")
            await session.write(payload)
        elif msg_type == "resize":
            await session.resize(int(data.get("cols") or 120), int(data.get("rows") or 32))
        elif msg_type == "close":
            await conn.close_channel(name)
        return
    if channel["kind"] == "ai":
        action = data.get("action")
        if action == "chat":
            task = channel.get("task")
            if task and not task.done():
                await conn.send({"method": "error", "channel": name, "data": {"message": "上一个请求仍在进行"}})
                return
            channel["task"] = asyncio.create_task(ws_ai_chat(conn, name, data))
        elif action == "approval":
            runner = channel.get("runner")
            if runner:
                await runner.resolve_approval(data.get("id") or "", bool(data.get("approve")), bool(data.get("remember")))
        elif action == "cancel":
            runner = channel.get("runner")
            if runner:
                runner.cancel()
            task = channel.get("task")
            if task and not task.done():
                task.cancel()
        return


# ------------------------------------------------------------------ static
async def index(request: web.Request) -> web.StreamResponse:
    index_path = os.path.join(DIST_DIR, "index.html")
    if not os.path.exists(index_path):
        return web.Response(
            text=(
                "<html><body style='background:#1a1b1d;color:#ddd;font-family:sans-serif;padding:40px'>"
                "<h2>H1KHub 前端尚未构建</h2><p>请运行 <code>cd frontend && npm install && npm run build</code>。</p>"
                "</body></html>"
            ),
            content_type="text/html",
        )
    return web.FileResponse(index_path, headers={"Cache-Control": "no-store"})


async def spa_fallback(request: web.Request) -> web.StreamResponse:
    if request.path.startswith("/api/") or request.path.startswith("/rpc"):
        return web.Response(status=404)
    candidate = os.path.join(DIST_DIR, request.path.lstrip("/"))
    if os.path.isfile(candidate):
        return web.FileResponse(candidate)
    return await index(request)


async def _sweep_db_sessions() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            expired = STATE.dbsessions.sweep()
            if expired:
                log.info("closed %s idle database session(s)", expired)
        except Exception:  # pragma: no cover - defensive
            log.exception("database session sweep failed")


async def on_startup(app: web.Application) -> None:
    log.info("data home: %s", HOME)
    log.info("listening on http://%s:%s", STATE.host, STATE.port)
    app["db_sweeper"] = asyncio.create_task(_sweep_db_sessions())


async def on_cleanup(app: web.Application) -> None:
    task = app.get("db_sweeper")
    if task:
        task.cancel()
    for session in list(STATE.hub.sessions.sessions.values()):
        _save_session_log(session)
    STATE.dbsessions.close_all()
    await STATE.chrome_proxy.stop_all()
    await STATE.tunnels.stop_all()
    await STATE.tasks.cancel_all()
    await STATE.hub.close()
    STATE.store.close()


def build_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware], client_max_size=1024 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.router.add_get("/api/public/info", public_info)
    app.router.add_get("/proto/{name}", proto_file)
    app.router.add_post("/api/auth/token", issue_token)
    app.router.add_get("/api/auth/token", issue_token)

    # Reserved capabilities (round 21): status registry + HTTP 501 placeholders.
    app.router.add_get("/api/capabilities", capabilities_list)
    app.router.add_get("/api/update/check", update_check)
    app.router.add_post("/api/update/start", update_start)
    app.router.add_get("/api/update/status", update_status)
    app.router.add_post("/api/update/channel", update_channel)
    app.router.add_get("/api/lock/status", lock_status)
    app.router.add_put("/api/lock/config", lock_config)
    app.router.add_post("/api/lock/password", lock_password)
    app.router.add_delete("/api/lock/password", lock_password_clear)
    app.router.add_post("/api/lock/lock", lock_now)
    app.router.add_post("/api/lock/verify", lock_verify)
    app.router.add_post("/api/lock/activity", lock_activity)
    app.router.add_get("/api/mcp/info", mcp_info)
    app.router.add_get("/api/plugins", plugins_list)

    app.router.add_get("/api/assets", assets_list)
    app.router.add_get("/api/asset-map", asset_map)
    app.router.add_post("/api/assets", assets_create)
    app.router.add_put("/api/assets/{asset_id}", assets_update)
    app.router.add_patch("/api/assets/{asset_id}", assets_update)
    app.router.add_delete("/api/assets/{asset_id}", assets_delete)
    app.router.add_post("/api/assets/{asset_id}/duplicate", assets_duplicate)
    app.router.add_post("/api/assets/{asset_id}/move", assets_move)
    app.router.add_get("/api/assets-export", assets_export)
    app.router.add_post("/api/assets-import", assets_import)
    app.router.add_post("/api/test-connection", test_connection)

    app.router.add_post("/api/sessions/start", session_start)
    app.router.add_get("/api/sessions", sessions_list)
    app.router.add_post("/api/sessions/{session_id}/close", session_close)
    app.router.add_post("/api/sessions/{session_id}/input", session_input)
    app.router.add_post("/api/sessions/{session_id}/read", session_read)
    app.router.add_post("/api/terminal/trzsz/upload", terminal_trzsz_upload)
    app.router.add_post("/api/terminal/trzsz/download", terminal_trzsz_download)
    app.router.add_get("/api/agent/backups", agent_backups)
    app.router.add_post("/api/agent/rollback", agent_rollback)
    app.router.add_get("/api/session-logs", session_logs_list)
    app.router.add_get("/api/session-logs/{name}", session_log_download)

    app.router.add_post("/api/sftp/list", sftp_list)
    app.router.add_post("/api/sftp/mkdir", sftp_mkdir)
    app.router.add_post("/api/sftp/rename", sftp_rename)
    app.router.add_post("/api/sftp/delete", sftp_delete)
    app.router.add_post("/api/sftp/chmod", sftp_chmod)
    app.router.add_post("/api/sftp/read", sftp_read)
    app.router.add_post("/api/sftp/write", sftp_write)
    app.router.add_post("/api/sftp/download", sftp_download)
    app.router.add_post("/api/sftp/upload", sftp_upload)
    app.router.add_post("/api/sftp/archive/formats", sftp_archive_formats)
    app.router.add_post("/api/sftp/archive/inspect", sftp_archive_inspect)
    app.router.add_post("/api/sftp/archive/compress", sftp_archive_compress)
    app.router.add_post("/api/sftp/archive/extract", sftp_archive_extract)

    app.router.add_get("/api/fs/home", local_home)
    app.router.add_post("/api/fs/list", local_list)
    app.router.add_post("/api/fs/mkdir", local_mkdir)
    app.router.add_post("/api/fs/rename", local_rename)
    app.router.add_post("/api/fs/delete", local_delete)
    app.router.add_post("/api/fs/chmod", local_chmod)
    app.router.add_post("/api/fs/read", local_read)
    app.router.add_post("/api/fs/write", local_write)
    app.router.add_post("/api/fs/download", local_download)
    app.router.add_post("/api/fs/upload", local_upload)
    app.router.add_post("/api/fs/archive/formats", local_archive_formats)
    app.router.add_post("/api/fs/archive/inspect", local_archive_inspect)
    app.router.add_post("/api/fs/archive/compress", local_archive_compress)
    app.router.add_post("/api/fs/archive/extract", local_archive_extract)

    app.router.add_post("/api/db/overview", db_overview)
    app.router.add_post("/api/db/objects", db_objects)
    app.router.add_post("/api/db/attach", db_attach)
    app.router.add_post("/api/db/table", db_table)
    app.router.add_post("/api/db/ddl", db_ddl)
    app.router.add_post("/api/db/query", db_query)
    app.router.add_post("/api/db/cancel", db_cancel)
    app.router.add_post("/api/db/session/cancel", db_session_cancel)
    app.router.add_post("/api/db/explain", db_explain)
    app.router.add_post("/api/db/row", db_row)
    app.router.add_post("/api/db/export", db_export)
    app.router.add_post("/api/db/session/open", db_session_open)
    app.router.add_post("/api/db/session/close", db_session_close)
    app.router.add_post("/api/db/session/status", db_session_status)
    app.router.add_post("/api/db/session/query", db_session_query)
    app.router.add_post("/api/db/tx", db_tx)
    app.router.add_post("/api/db/schema", db_schema)
    app.router.add_post("/api/db/import", db_import)
    app.router.add_post("/api/db/rows", db_rows)
    app.router.add_post("/api/db/blob", db_blob)
    app.router.add_post("/api/db/blob/download", db_blob_download)
    app.router.add_post("/api/db/table-schema", db_table_schema)
    app.router.add_post("/api/db/realtime", db_realtime)
    app.router.add_post("/api/db/processlist", db_processlist)

    app.router.add_post("/api/db/object-def", db_object_definition)
    app.router.add_post("/api/db/object-save", db_object_save)
    app.router.add_post("/api/db/object-drop", db_object_drop)

    app.router.add_post("/api/db/sync/diff", db_sync_diff)
    app.router.add_post("/api/db/sync/apply", db_sync_apply)
    app.router.add_post("/api/db/sync/data/preview", db_sync_data_preview)
    app.router.add_post("/api/db/sync/data", db_sync_data)
    app.router.add_post("/api/transfer", file_transfer)
    app.router.add_get("/api/tasks", tasks_list)
    app.router.add_get("/api/tasks/{task_id}", task_get)
    app.router.add_post("/api/tasks/{task_id}/cancel", task_cancel)
    app.router.add_post("/api/sessions/broadcast", sessions_broadcast)
    app.router.add_post("/api/keys/generate", keys_generate)
    app.router.add_post("/api/ssh/copy-id", ssh_copy_id)

    app.router.add_get("/api/tunnels", tunnels_list)
    app.router.add_post("/api/tunnels/start", tunnel_start)
    app.router.add_post("/api/tunnels/{tunnel_id}/stop", tunnel_stop)
    app.router.add_post("/api/tunnels/probe", tunnel_probe)
    app.router.add_get("/api/chrome-proxy", chrome_proxy_list)
    app.router.add_post("/api/chrome-proxy/start", chrome_proxy_start)
    app.router.add_post("/api/chrome-proxy/{session_id}/stop", chrome_proxy_stop)

    app.router.add_post("/api/scp/upload", scp_upload)
    app.router.add_post("/api/scp/download", scp_download)

    app.router.add_post("/api/docker/image/save", docker_image_save)
    app.router.add_post("/api/docker/image/load", docker_image_load)
    app.router.add_post("/api/docker/fs/download", docker_fs_download)
    app.router.add_post("/api/docker/fs/upload", docker_fs_upload)

    app.router.add_post("/api/ftp/list", ftp_list)
    app.router.add_post("/api/ftp/mkdir", ftp_mkdir)
    app.router.add_post("/api/ftp/rename", ftp_rename)
    app.router.add_post("/api/ftp/delete", ftp_delete)
    app.router.add_post("/api/ftp/read", ftp_read)
    app.router.add_post("/api/ftp/write", ftp_write)
    app.router.add_post("/api/ftp/download", ftp_download)
    app.router.add_post("/api/ftp/upload", ftp_upload)

    app.router.add_post("/api/batch/exec", batch_exec)
    app.router.add_post("/api/redis/{action}", redis_api)
    app.router.add_post("/api/docker/local-registry", docker_local_registry)
    app.router.add_post("/api/docker/{action}", docker_api)

    app.router.add_get("/api/settings", settings_get)
    app.router.add_put("/api/settings", settings_update)
    app.router.add_get("/api/history", history_list)
    app.router.add_delete("/api/history", history_clear)
    app.router.add_get("/api/commands", commands_list)
    app.router.add_post("/api/commands", commands_add)

    app.router.add_get("/api/chats", chats_list)
    app.router.add_post("/api/chats", chats_create)
    app.router.add_delete("/api/chats/{chat_id}", chats_delete)
    app.router.add_get("/api/chats/{chat_id}/messages", chat_messages)

    app.router.add_post("/api/ai/models", ai_models)
    app.router.add_post("/api/ai/test", ai_test)

    app.router.add_get("/rpc", rpc_handler)

    app.router.add_get("/", index)
    if os.path.isdir(DIST_DIR):
        assets_dir = os.path.join(DIST_DIR, "assets")
        if os.path.isdir(assets_dir):
            app.router.add_static("/assets", assets_dir, show_index=False)
    app.router.add_get("/{tail:.*}", spa_fallback)
    return app


def main() -> None:
    global HOME
    parser = argparse.ArgumentParser(description="H1KHub replica backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("H1KHUB_PORT") or 38110))
    parser.add_argument("--home", default=HOME)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--token", default="")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    HOME = os.path.abspath(os.path.expanduser(args.home))
    os.makedirs(HOME, exist_ok=True)

    os.makedirs(HOME, mode=0o700, exist_ok=True)
    log_path = os.path.join(HOME, "h1khub.log")
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass

    STATE.store = Store(HOME)
    STATE.lock = lock_svc.LockService(STATE.store)
    STATE.hub = Hub(STATE.store, HOME)
    STATE.dbsessions = dbsessions.DbSessionManager(STATE.hub)
    STATE.tunnels = TunnelManager(STATE.hub)
    STATE.hub.tunnels = STATE.tunnels
    STATE.chrome_proxy = ChromeProxyManager(STATE.hub)
    STATE.tasks = task_svc.TaskManager()
    STATE.token = args.token or secrets.token_urlsafe(32)
    STATE.host = args.host
    STATE.port = args.port

    runtime_path = os.path.join(HOME, "runtime.json")
    with open(runtime_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "port": args.port,
                "host": args.host,
                "token": STATE.token,
                "pid": os.getpid(),
                "startedAt": STATE.started_at,
                "version": APP_VERSION,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )
    os.chmod(runtime_path, 0o600)

    app = build_app()
    url = f"http://{args.host}:{args.port}/"
    log.info("%s %s listening on %s (data: %s)", APP_NAME, APP_VERSION, url, HOME)
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    web.run_app(app, host=args.host, port=args.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
