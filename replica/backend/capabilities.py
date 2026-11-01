"""Reserved-capability registry (round 21).

This module is documentation-as-data: it is the single source of truth for
features the replica has *not* implemented yet.  It describes status only and
provides the shared HTTP 501 contract used by the placeholder routes; nothing
here activates a feature, and no configuration key declared for a reserved
capability changes any behaviour.

Status values
-------------
reserved     Future work; a placeholder route exists and answers 501.
unavailable  Not reachable in this product form (browser) or depends on the
             cloud membership/RSA stack; no route is registered.
done         Genuinely implemented (lock.screen in round 22; update.check and
             update.channel in round 23).  A done entry keeps its route but
             leaves the 501/reserved path entirely.
"""
from __future__ import annotations

import json
from typing import Any

from aiohttp import web

RESERVED_IN = "round21"
DOCS_PATH = "docs/reserved-slots.md"

CAPABILITIES: list[dict[str, Any]] = [
    {
        "id": "lock.screen",
        "oid": 90,
        "group": "security",
        "status": "done",
        "name": "锁屏",
        "nameEn": "Screen lock",
        "entry": "POST /api/lock/verify",
        "docs": f"{DOCS_PATH}#90-锁屏与托盘",
        "summary": "本地口令门：PBKDF2-HMAC-SHA256 哈希 + 随机 salt、失败退避、立即锁定、空闲自动锁定；锁定状态在服务端，刷新/新标签仍保持锁定。",
        "reason": "",
        "implementedIn": "round22",
    },
    {
        "id": "lock.tray",
        "oid": 90,
        "group": "security",
        "status": "unavailable",
        "name": "系统托盘",
        "nameEn": "System tray",
        "entry": "",
        "docs": f"{DOCS_PATH}#90-锁屏与托盘",
        "summary": "托盘按钮位置与 setTrayMenu 依赖桌面壳（enableTray/setTrayMenu），浏览器内没有系统托盘。",
        "reason": "浏览器形态无系统托盘",
    },
    {
        "id": "update.check",
        "oid": 91,
        "group": "update",
        "status": "done",
        "name": "版本检查",
        "nameEn": "Version check",
        "entry": "GET /api/update/check",
        "docs": f"{DOCS_PATH}#91-版本检查",
        "summary": "读取本机 manifest（file:// 或 127.0.0.1 http(s)），按数值比较 version/build，返回 current/latest/hasUpdate/notes；只读检查，不下载。",
        "reason": "",
        "implementedIn": "round23",
    },
    {
        "id": "update.hot",
        "oid": 92,
        "group": "update",
        "status": "reserved",
        "name": "热更新",
        "nameEn": "Hot update",
        "entry": "POST /api/update/start",
        "docs": f"{DOCS_PATH}#92-热更新",
        "summary": "下载 7z → MD5 比对 → 解压（防 Zip-Slip）→ runtime/<version> → 重启后端；本地复刻未做分发链路。",
        "reason": "",
    },
    {
        "id": "update.channel",
        "oid": 93,
        "group": "update",
        "status": "done",
        "name": "更新通道",
        "nameEn": "Update channel",
        "entry": "POST /api/update/channel",
        "docs": f"{DOCS_PATH}#93-更新通道",
        "summary": "稳定/实验性通道 + production/test 发布环境，白名单校验并持久化；与 {channel}/{env} 模板共同决定版本检查读取的 manifest。",
        "reason": "",
        "implementedIn": "round23",
    },
    {
        "id": "cloud.encryption",
        "oid": 95,
        "group": "cloud",
        "status": "unavailable",
        "name": "云加密",
        "nameEn": "Cloud encryption",
        "entry": "",
        "docs": f"{DOCS_PATH}#95-云加密",
        "summary": "依赖云端会员 RSA 公钥分块加密体系；没有云端账号与密钥，本地无法验证，故不提供入口。",
        "reason": "依赖云端会员 RSA 体系，本地无法验证",
    },
    {
        "id": "ext.mcp",
        "oid": 98,
        "group": "extension",
        "status": "reserved",
        "name": "MCP",
        "nameEn": "MCP",
        "entry": "GET /api/mcp/info",
        "docs": f"{DOCS_PATH}#98-mcp",
        "summary": "Model Context Protocol 客户端/服务端；原版产物未发现 MCP，属于待新增能力而非复刻。",
        "reason": "",
        "note": "原版产物未发现 MCP",
    },
    {
        "id": "ext.plugins",
        "oid": 99,
        "group": "extension",
        "status": "reserved",
        "name": "插件市场",
        "nameEn": "Plugin marketplace",
        "entry": "GET /api/plugins",
        "docs": f"{DOCS_PATH}#99-插件市场",
        "summary": "原版为编译期内置 6 插件、无动态市场；动态插件加载需要单独的装机与隔离设计。",
        "reason": "",
        "note": "原版为编译期内置 6 插件，无动态市场",
    },
]

_BY_ID: dict[str, dict[str, Any]] = {cap["id"]: cap for cap in CAPABILITIES}


def capability(feature_id: str) -> dict[str, Any] | None:
    """Return the registry entry for *feature_id*, or None if unknown."""
    return _BY_ID.get(feature_id)


def capabilities_public() -> list[dict[str, Any]]:
    """Registry rows safe to publish over HTTP (no secrets live here)."""
    return json.loads(json.dumps(CAPABILITIES, ensure_ascii=False))


def reserved_response(feature_id: str) -> web.Response:
    """Build the uniform HTTP 501 body for a reserved capability.

    The route must be registered explicitly; this helper never falls back to a
    generic handler, so an unknown feature id cannot accidentally create a
    working endpoint.
    """
    cap = _BY_ID.get(feature_id)
    if cap is None or cap.get("status") != "reserved":
        raise ValueError(f"not a reserved capability: {feature_id}")
    payload = {
        "ok": False,
        "error": {
            "code": "FEATURE_RESERVED",
            "feature": feature_id,
            "reservedIn": RESERVED_IN,
            "docs": cap["docs"],
            "message": "该能力已预留扩展点，尚未实现",
        },
    }
    return web.json_response(
        payload,
        status=501,
        dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=str),
    )
