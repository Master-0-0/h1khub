"""Local update manifest checks (#91) and update channels (#93), round 23.

This module deliberately implements *checks only*:

* it reads one JSON manifest that the user configured through
  ``update.manifestUrl`` — either a ``file://`` path or a loopback
  ``http(s)://`` URL — and compares its numeric ``version``/``build`` with the
  running ``APP_VERSION``/``APP_BUILD``;
* it never downloads a package, never executes anything, never writes the asset
  store and never contacts the cloud (``api.hexhub.cn`` is not reachable by
  construction: only loopback hosts are accepted);
* hot update (#92: download → MD5 → 7z → runtime → restart) stays a reserved
  501 capability and is *not* implemented here.

Manifest contract
-----------------
Canonical shape (UTF-8 JSON object, at most ``MAX_MANIFEST_BYTES``)::

    {
      "version": "5.1.10",           # required, dotted numeric, e.g. 5.1.9
      "build": 520,                  # required, integer (never a string)
      "notes": "…",                  # optional string (truncated to 4000 chars)
      "url": "https://…/pkg.deb",    # optional string, http/https/file only
      "md5": "0123456789abcdef0123456789abcdef"  # optional 32 hex chars
    }

``version``/``build`` are required; the remaining three fields are validated
when present and default to an empty string otherwise.  Version comparison is
numeric (``5.1.10 > 5.1.9``); a newer version wins, and on an equal version a
higher build wins.  The manifest is untrusted input: it is size-capped, decoded
strictly, schema-checked and its notes are truncated for the response.

URL selection (#93)
-------------------
``update.channel`` (``stable`` / ``experimental``) and ``update.env``
(``production`` / ``test``) are orthogonal.  ``update.manifestUrl`` may be:

* a template containing ``{channel}`` and/or ``{env}`` — substituted;
* a directory URL ending in ``/`` — ``update-<channel>-<env>.json`` is appended;
* a plain file URL — used as-is for every channel/env (a single-manifest setup).

Only ``file`` (absolute local path, empty/localhost authority) and loopback
``http``/``https`` URLs are accepted.  Redirects are not followed, the response
is streamed with a byte cap and the request times out quickly.  This is the
SSRF/supply-chain guard for the *check* path; the download path (#92) does not
exist.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp

from util import APP_BUILD, APP_VERSION, now_iso

MAX_MANIFEST_BYTES = 256 * 1024
MAX_MANIFEST_URL_CHARS = 2048
MAX_NOTES_CHARS = 4000
FETCH_TIMEOUT_SECONDS = 5.0

CHANNELS = ("stable", "experimental")
ENVS = ("production", "test")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_SCHEMES = {"file", "http", "https"}
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
VERSION_RE = re.compile(r"^\d{1,6}(?:\.\d{1,6}){0,3}$")
MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")

DOCS_CHECK = "docs/reserved-slots.md#91-版本检查"
DOCS_CHANNEL = "docs/reserved-slots.md#93-更新通道"


class UpdateError(Exception):
    """A user-visible update error (HTTP 400, never 500)."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _err(code: str, message: str, status: int = 400) -> UpdateError:
    return UpdateError(code, message, status)


# ------------------------------------------------------------------ settings
def normalize_channel(value: Any) -> str:
    if not isinstance(value, str) or value not in CHANNELS:
        raise _err(
            "UPDATE_INVALID_CHANNEL",
            f"更新通道只接受 {'/'.join(CHANNELS)}",
        )
    return value


def normalize_env(value: Any) -> str:
    if not isinstance(value, str) or value not in ENVS:
        raise _err(
            "UPDATE_INVALID_ENV",
            f"发布环境只接受 {'/'.join(ENVS)}",
        )
    return value


def update_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Current update settings with defensive defaults."""
    update = settings.get("update") if isinstance(settings, dict) else None
    if not isinstance(update, dict):
        update = {}
    return {
        "channel": update.get("channel") or "stable",
        "env": update.get("env") or "production",
        "manifestUrl": update.get("manifestUrl") or "",
        "autoCheck": bool(update.get("autoCheck")),
    }


def validate_settings_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate the ``update`` object of a generic ``PUT /api/settings``.

    The dedicated channel endpoint is the canonical way to switch channels, but
    the settings dialog saves the whole draft, so the generic path must reject
    exactly the same bad values instead of persisting an invalid channel.
    """
    if not isinstance(patch, dict):
        raise _err("UPDATE_INVALID_SETTINGS", "update 配置必须是对象")
    clean: dict[str, Any] = {}
    if "channel" in patch:
        clean["channel"] = normalize_channel(patch.get("channel"))
    if "env" in patch:
        clean["env"] = normalize_env(patch.get("env"))
    if "autoCheck" in patch:
        if not isinstance(patch.get("autoCheck"), bool):
            raise _err("UPDATE_INVALID_SETTINGS", "update.autoCheck 必须是布尔值")
        clean["autoCheck"] = patch["autoCheck"]
    if "manifestUrl" in patch:
        url = patch.get("manifestUrl")
        if not isinstance(url, str):
            raise _err("UPDATE_INVALID_SETTINGS", "update.manifestUrl 必须是字符串")
        url = url.strip()
        if len(url) > MAX_MANIFEST_URL_CHARS:
            raise _err("UPDATE_MANIFEST_URL_INVALID", "manifest 地址过长")
        # Validate the concrete URL now (bad values are rejected before they are
        # stored), unless it is empty (meaning "not configured").
        if url:
            resolve_manifest_url(url, clean.get("channel", "stable"), clean.get("env", "production"))
        clean["manifestUrl"] = url
    return clean


# ------------------------------------------------------------------ manifest
def manifest_filename(channel: str, env: str) -> str:
    return f"update-{channel}-{env}.json"


def resolve_manifest_url(template: str, channel: str, env: str) -> str:
    """Resolve the configured manifest URL for *channel*/*env*.

    Templates ({channel}/{env}), directory URLs (trailing ``/``) and plain file
    URLs are supported; see the module docstring.  The result is validated but
    not fetched.
    """
    raw = (template or "").strip()
    if not raw:
        raise _err("UPDATE_MANIFEST_NOT_CONFIGURED", "尚未配置更新 manifest 地址")
    if len(raw) > MAX_MANIFEST_URL_CHARS:
        raise _err("UPDATE_MANIFEST_URL_INVALID", "manifest 地址过长")
    resolved = raw.replace("{channel}", channel).replace("{env}", env)
    if resolved.endswith("/"):
        resolved += manifest_filename(channel, env)
    _validate_url(resolved)
    return resolved


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise _err("UPDATE_MANIFEST_URL_INVALID", "manifest 只支持 file/http/https 地址")
    if scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            raise _err("UPDATE_MANIFEST_URL_INVALID", "file 地址不允许远程主机")
        path = unquote(parsed.path)
        if not path:
            raise _err("UPDATE_MANIFEST_URL_INVALID", "file 地址缺少路径")
        if WINDOWS_DRIVE_RE.match(path):
            path = path.replace("/", os.sep)
        if not os.path.isabs(path):
            raise _err("UPDATE_MANIFEST_URL_INVALID", "manifest 必须是绝对路径")
        return
    host = (parsed.hostname or "").lower()
    if not host:
        raise _err("UPDATE_MANIFEST_URL_INVALID", "manifest 地址缺少主机")
    if host not in LOOPBACK_HOSTS:
        # This also blocks api.hexhub.cn and any other cloud/network endpoint.
        raise _err("UPDATE_MANIFEST_FORBIDDEN", "manifest 只允许本机 http(s)（127.0.0.1/localhost）")
    if parsed.username or parsed.password:
        raise _err("UPDATE_MANIFEST_URL_INVALID", "manifest 地址不允许携带凭据")


def _file_path(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if WINDOWS_DRIVE_RE.match(path):
        path = path.replace("/", os.sep)
    return path


def validate_manifest(payload: Any) -> dict[str, Any]:
    """Schema-check an untrusted manifest and return its safe projection."""
    if not isinstance(payload, dict):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest 必须是 JSON 对象")
    version = payload.get("version")
    if not isinstance(version, str):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.version 必须是字符串")
    version = version.strip()
    if not VERSION_RE.match(version):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.version 必须是点分数字（如 5.1.9）")
    build = payload.get("build")
    if isinstance(build, bool) or not isinstance(build, int):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.build 必须是整数")
    if build < 0 or build > 10**9:
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.build 超出范围")
    notes = payload.get("notes", "")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.notes 必须是字符串")
    url = payload.get("url", "")
    if url is None:
        url = ""
    if not isinstance(url, str):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.url 必须是字符串")
    url = url.strip()
    if len(url) > MAX_MANIFEST_URL_CHARS:
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.url 过长")
    if url:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            raise _err("UPDATE_MANIFEST_INVALID", "manifest.url 只支持 file/http/https")
    md5 = payload.get("md5", "")
    if md5 is None:
        md5 = ""
    if not isinstance(md5, str):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.md5 必须是字符串")
    md5 = md5.strip()
    if md5 and not MD5_RE.match(md5):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest.md5 必须是 32 位十六进制")
    return {
        "version": version,
        "build": build,
        "notes": notes[:MAX_NOTES_CHARS],
        "url": url,
        "md5": md5.lower(),
    }


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def is_newer(
    latest_version: str,
    latest_build: int,
    current_version: str = APP_VERSION,
    current_build: int = APP_BUILD,
) -> bool:
    """Numeric comparison: newer version wins; equal version compares build."""
    latest = version_tuple(latest_version)
    current = version_tuple(current_version)
    width = max(len(latest), len(current))
    latest += (0,) * (width - len(latest))
    current += (0,) * (width - len(current))
    if latest != current:
        return latest > current
    return latest_build > current_build


# ------------------------------------------------------------------- reading
async def _read_file(path: str) -> bytes:
    try:
        size = os.path.getsize(path)
    except OSError:
        raise _err("UPDATE_MANIFEST_UNAVAILABLE", "manifest 文件不存在或不可读")
    if size > MAX_MANIFEST_BYTES:
        raise _err("UPDATE_MANIFEST_TOO_LARGE", f"manifest 超过 {MAX_MANIFEST_BYTES} 字节上限")
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_MANIFEST_BYTES + 1)
    except OSError:
        raise _err("UPDATE_MANIFEST_UNAVAILABLE", "manifest 文件不存在或不可读")
    if len(data) > MAX_MANIFEST_BYTES:
        raise _err("UPDATE_MANIFEST_TOO_LARGE", f"manifest 超过 {MAX_MANIFEST_BYTES} 字节上限")
    return data


async def _read_http(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
    try:
        # trust_env=False keeps a user-level HTTP proxy out of the loopback
        # check; redirects are not followed (a redirect could escape loopback).
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status != 200:
                    raise _err("UPDATE_MANIFEST_UNAVAILABLE", f"manifest 返回 HTTP {resp.status}")
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_MANIFEST_BYTES:
                    raise _err("UPDATE_MANIFEST_TOO_LARGE", f"manifest 超过 {MAX_MANIFEST_BYTES} 字节上限")
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(16384):
                    total += len(chunk)
                    if total > MAX_MANIFEST_BYTES:
                        raise _err("UPDATE_MANIFEST_TOO_LARGE", f"manifest 超过 {MAX_MANIFEST_BYTES} 字节上限")
                    chunks.append(chunk)
                return b"".join(chunks)
    except UpdateError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError):
        raise _err("UPDATE_MANIFEST_UNAVAILABLE", "无法读取本机 manifest")


async def read_manifest(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme.lower() == "file":
        data = await _read_file(_file_path(url))
        source = "file"
    else:
        data = await _read_http(url)
        source = "http"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _err("UPDATE_MANIFEST_INVALID", "manifest 不是 UTF-8 文本")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise _err("UPDATE_MANIFEST_INVALID", "manifest 不是合法 JSON")
    manifest = validate_manifest(payload)
    manifest["source"] = source
    return manifest


# --------------------------------------------------------------------- public
async def check(settings: dict[str, Any]) -> dict[str, Any]:
    """Read the configured manifest and compare it with the running build."""
    cfg = update_settings(settings)
    channel = normalize_channel(cfg["channel"])
    env = normalize_env(cfg["env"])
    resolved = resolve_manifest_url(cfg["manifestUrl"], channel, env)
    manifest = await read_manifest(resolved)
    has_update = is_newer(manifest["version"], manifest["build"])
    return {
        "current": {"version": APP_VERSION, "build": APP_BUILD},
        "latest": {"version": manifest["version"], "build": manifest["build"]},
        "hasUpdate": has_update,
        "notes": manifest["notes"],
        "url": manifest["url"],
        "md5": manifest["md5"],
        "channel": channel,
        "env": env,
        "manifestUrl": resolved,
        "source": manifest["source"],
        "checkedAt": now_iso(),
    }


def set_channel(store: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """Persist channel/env (dedicated endpoint); never downloads or restarts."""
    if not isinstance(patch, dict):
        raise _err("UPDATE_INVALID_CHANNEL", "请求体必须是对象")
    clean: dict[str, Any] = {}
    if "channel" in patch:
        clean["channel"] = normalize_channel(patch.get("channel"))
    if "env" in patch:
        clean["env"] = normalize_env(patch.get("env"))
    if not clean:
        raise _err("UPDATE_INVALID_CHANNEL", "至少提供 channel 或 env")
    settings = store.update_settings({"update": clean})
    cfg = update_settings(settings)
    out = {
        "channel": cfg["channel"],
        "env": cfg["env"],
        "manifestUrl": cfg["manifestUrl"],
        "autoCheck": cfg["autoCheck"],
    }
    # Report the URL resolution for the new channel/env without fetching it.
    try:
        out["manifestResolved"] = (
            resolve_manifest_url(cfg["manifestUrl"], cfg["channel"], cfg["env"])
            if cfg["manifestUrl"]
            else ""
        )
    except UpdateError:
        out["manifestResolved"] = ""
    return out
