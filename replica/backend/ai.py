"""Built-in AI agent: OpenAI-compatible / Anthropic streaming, tools and approvals."""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import subprocess
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

from db import DatabaseError
from docker_svc import DockerError
from ftp_svc import FTPError
from redis_svc import RedisError
import agent_backup
from util import b64decode_str, clamp_text, human_bytes, jsonable, new_id, strip_ansi

MAX_TOOL_OUTPUT = 100_000

SYSTEM_PROMPT = """你是 H1KHub（数据库 / SSH / SFTP / Docker 一体化桌面工具）内置的 AI 助手，运行在用户本机的 H1KHub 客户端里。

你可以调用工具真实地操作本机与已保存的资产：
- 列出资产、浏览目录、读写文件（本机或远程 SFTP）
- 执行 shell 命令（本机或 SSH 远程）
- 查询数据库（MySQL / PostgreSQL / SQLite）、执行 SQL
- 执行 Redis 命令、查看 Docker 容器与日志、启动/停止/重启 Docker 容器
- 浏览 FTP 目录，上传/下载/删除/重命名 FTP 文件
- 读取当前打开终端的内容
- 联网搜索网页（websearch；必须由用户显式配置端点，默认关闭，默认每次调用都需要审批，可在设置中关闭）

行为准则：
1. 先用工具确认事实，再下结论；不要编造文件内容、命令输出或查询结果。
2. 危险或破坏性操作必须先说明影响，并等待审批；绝不绕过审批。
3. 不泄露口令、私钥、API Key 等敏感信息；引用资产时用 ID 与名称，不回显密钥。
4. 对本地系统与远程服务器保持最小改动原则；删除/覆盖前先确认。
5. 使用用户当前的语言回答，回答简洁、可执行，必要时给出命令与回滚方式。
6. 数据库标识符遵循目标方言的引号规则；大结果集先分页或加 LIMIT。
7. 联网搜索只能使用 websearch 工具并等待审批；未配置时不要用 shell + curl/wget 或其他方式绕过。
"""


class AIError(Exception):
    pass


# --------------------------------------------------------------------- tools
def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
    }


TOOLS: list[dict] = [
    _tool("list_assets", "列出 H1KHub 中保存的所有资产（连接/目录/查询），返回 id、名称、类型和父子关系。"),
    _tool(
        "list_dir",
        "列出一个目录的内容。asset_id 为空表示本机文件系统，否则为对应的 SSH/SFTP 资产。",
        {
            "path": {"type": "string", "description": "目录路径，本机支持 ~"},
            "asset_id": {"type": "string", "description": "SSH 资产 ID，留空表示本机"},
        },
        ["path"],
    ),
    _tool(
        "read_file",
        "读取文本文件内容（最多 200KB）。asset_id 为空表示本机。",
        {
            "path": {"type": "string"},
            "asset_id": {"type": "string"},
            "max_bytes": {"type": "integer", "description": "最多读取字节数，默认 200000"},
        },
        ["path"],
    ),
    _tool(
        "write_file",
        "写入/覆盖文本文件（需要用户审批）。asset_id 为空表示本机。",
        {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "asset_id": {"type": "string"},
            "create_dirs": {"type": "boolean"},
        },
        ["path", "content"],
    ),
    _tool(
        "shell",
        "执行 shell 命令（需要用户审批，危险命令强制审批）。asset_id 为空时在本机执行，否则通过 SSH 在远程主机执行。",
        {
            "command": {"type": "string"},
            "asset_id": {"type": "string"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
        },
        ["command"],
    ),
    _tool(
        "list_db_objects",
        "列出数据库连接中的库/模式与表、视图、例程清单。",
        {"asset_id": {"type": "string", "description": "数据库资产 ID"}, "database": {"type": "string"}},
        ["asset_id"],
    ),
    _tool(
        "execute_sql",
        "在数据库连接上执行 SQL 并返回结果（写操作需要审批）。",
        {
            "asset_id": {"type": "string"},
            "sql": {"type": "string"},
            "database": {"type": "string"},
        },
        ["asset_id", "sql"],
    ),
    _tool(
        "redis_command",
        "在 Redis 连接上执行一条命令（写命令需要审批）。",
        {
            "asset_id": {"type": "string"},
            "command": {"type": "string"},
            "db": {"type": "integer", "description": "数据库序号，默认 0"},
        },
        ["asset_id", "command"],
    ),
    _tool(
        "docker_containers",
        "列出 Docker 容器（asset_id 为空时使用本机 Docker）。",
        {"asset_id": {"type": "string"}},
        [],
    ),
    _tool(
        "docker_logs",
        "读取 Docker 容器日志。",
        {
            "asset_id": {"type": "string"},
            "container": {"type": "string"},
            "tail": {"type": "integer", "description": "最后 N 行，默认 200"},
        },
        ["container"],
    ),
    _tool(
        "docker_container_action",
        "启动 / 停止 / 重启 Docker 容器（需要用户审批）。asset_id 为空时使用本机 Docker。",
        {
            "asset_id": {"type": "string", "description": "Docker 资产 ID，留空表示本机"},
            "container": {"type": "string", "description": "容器名称或 ID（需已存在）"},
            "action": {"type": "string", "enum": ["start", "stop", "restart"], "description": "容器动作"},
        },
        ["container", "action"],
    ),
    _tool(
        "list_sessions",
        "列出当前打开的终端会话（本地/SSH/Telnet/Docker）。",
    ),
    _tool(
        "read_terminal",
        "读取某个终端会话最近的内容（去除 ANSI 转义）。",
        {"session_id": {"type": "string"}, "lines": {"type": "integer"}},
        ["session_id"],
    ),
    _tool(
        "terminal_execute",
        "向当前终端会话注入一条命令（需要审批）。",
        {"session_id": {"type": "string"}, "command": {"type": "string"}},
        ["session_id", "command"],
    ),
    _tool(
        "scp_upload",
        "通过 SCP 把本机文件上传到 SSH 主机（需要审批）。",
        {
            "asset_id": {"type": "string", "description": "SSH 资产 ID"},
            "local_path": {"type": "string", "description": "本机文件路径"},
            "remote_path": {"type": "string", "description": "远端目标文件路径"},
        },
        ["asset_id", "local_path", "remote_path"],
    ),
    _tool(
        "scp_download",
        "通过 SCP 从 SSH 主机下载文件到本机（需要审批）。",
        {
            "asset_id": {"type": "string", "description": "SSH 资产 ID"},
            "remote_path": {"type": "string", "description": "远端文件路径"},
            "local_path": {"type": "string", "description": "本机保存路径"},
        },
        ["asset_id", "remote_path", "local_path"],
    ),
    _tool("list_tunnels", "列出当前运行中的 SSH 隧道（本地/远程/SOCKS5）。"),
    _tool(
        "start_tunnel",
        "启动一个 SSH 隧道资产（需要审批）。返回监听地址与端口。",
        {"asset_id": {"type": "string", "description": "SSH 隧道资产 ID"}},
        ["asset_id"],
    ),
    _tool(
        "stop_tunnel",
        "停止一个运行中的 SSH 隧道。",
        {"tunnel_id": {"type": "string"}},
        ["tunnel_id"],
    ),
    _tool(
        "ftp_list",
        "列出 FTP 资产目录中的文件。",
        {
            "asset_id": {"type": "string", "description": "FTP 资产 ID"},
            "path": {"type": "string", "description": "目录路径，默认 ~"},
        },
        ["asset_id"],
    ),
    _tool(
        "ftp_upload",
        "把本机文件上传到 FTP 资产（需要审批）；远端路径必须位于该 FTP 主目录内。",
        {
            "asset_id": {"type": "string", "description": "FTP 资产 ID"},
            "local_path": {"type": "string", "description": "本机文件路径"},
            "remote_path": {"type": "string", "description": "远端目标文件路径"},
        },
        ["asset_id", "local_path", "remote_path"],
    ),
    _tool(
        "ftp_download",
        "从 FTP 资产下载文件到本机（需要审批）；远端路径必须位于该 FTP 主目录内。",
        {
            "asset_id": {"type": "string", "description": "FTP 资产 ID"},
            "remote_path": {"type": "string", "description": "远端文件路径"},
            "local_path": {"type": "string", "description": "本机保存路径"},
        },
        ["asset_id", "remote_path", "local_path"],
    ),
    _tool(
        "ftp_delete",
        "删除 FTP 资产上的文件或目录（需要审批）；路径必须位于该 FTP 主目录内。",
        {
            "asset_id": {"type": "string", "description": "FTP 资产 ID"},
            "path": {"type": "string", "description": "要删除的远端路径"},
        },
        ["asset_id", "path"],
    ),
    _tool(
        "ftp_rename",
        "重命名/移动 FTP 资产上的文件或目录（需要审批）；源与目标都必须位于该 FTP 主目录内。",
        {
            "asset_id": {"type": "string", "description": "FTP 资产 ID"},
            "from_path": {"type": "string", "description": "原路径"},
            "to_path": {"type": "string", "description": "新路径"},
        },
        ["asset_id", "from_path", "to_path"],
    ),
    _tool(
        "web_fetch",
        "抓取一个 http(s) 页面并转为纯文本（最多 200KB）。",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _tool(
        "websearch",
        "联网搜索网页，返回结果标题、链接与摘要（默认需要用户审批）。默认关闭且没有默认端点；"
        "未配置时返回 websearch_not_configured，不会发起任何网络请求。",
        {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最多返回条数（1-10），默认取设置值"},
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选：仅保留这些域名（含子域）",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选：排除这些域名（含子域）",
            },
        },
        ["query"],
    ),
]

TERMINAL_TOOL_NAMES = {
    "list_assets", "list_sessions", "read_terminal", "terminal_execute", "shell",
    "list_dir", "read_file", "write_file", "scp_upload", "scp_download",
    "list_tunnels", "start_tunnel", "stop_tunnel",
}
SQL_TOOL_NAMES = {
    "list_assets", "list_db_objects", "execute_sql", "read_file", "write_file",
}

ASSISTANT_PROMPTS = {
    "terminal": (
        "你现在是「终端运维助手」。优先使用终端与 SSH 工具（read_terminal / terminal_execute / shell / "
        "scp_* / 隧道工具）来观察和执行；回答要给出具体命令、风险与回滚方式。"
        "只在用户明确要求时修改系统；危险命令必须先说明影响并等待审批。"
    ),
    "sql": (
        "你现在是「SQL 编辑器助手」。优先使用 list_db_objects 与 execute_sql 工具；"
        "写 SQL 前先说明影响并等待审批。给出可执行的 SQL、索引/执行计划建议与回滚方式，"
        "不要编造表名或字段，先查询元数据确认。"
    ),
}


def tools_for(assistant: str) -> list[dict]:
    if assistant == "terminal":
        names = TERMINAL_TOOL_NAMES
    elif assistant == "sql":
        names = SQL_TOOL_NAMES
    else:
        return TOOLS
    return [tool for tool in TOOLS if tool["name"] in names]


DANGEROUS_TOKENS = re.compile(
    r"(^|[\s;&|()])(rm|rmdir|unlink|shred|dd|mkfs\S*|fdisk|sfdisk|parted|wipefs|blkdiscard|"
    r"cryptsetup|lvremove|vgremove|pvremove|reboot|shutdown|halt|poweroff|init|telinit|kexec|"
    r"kill|killall|pkill|fuser|userdel|groupdel|passwd|chpasswd|setfacl|chmod\s+-R\s+777|"
    r"git\s+reset\s+--hard|git\s+push\s+--force|curl[^|]*\|\s*(ba)?sh|wget[^|]*\|\s*(ba)?sh)(\s|$)"
)
WRAPPER_TOKENS = re.compile(r"(^|[\s;&|()])(sudo|doas|exec|nohup|setsid|env)(\s|$)")

SQL_READ = re.compile(r"^\s*(select|show|describe|desc|explain|with|pragma|values)\b", re.I)
DOCKER_ACTIONS = ("start", "stop", "restart")

# ------------------------------------------------------------------ websearch
# 降级策略（与原版 NewWebSearchTool 的差异，务必保持）：
# - 原版由 Go 后端直接抓 DuckDuckGo HTML（searchDuckDuckGo + filterByDomain），
#   默认云地址是 https://api.hexhub.cn；复刻版不内置任何搜索后端，也不把
#   api.hexhub.cn 或 DuckDuckGo 作为回退。
# - 端点只能由用户在 settings.websearch 中显式配置；enabled=False 或 endpoint
#   为空时 tool_websearch 直接返回结构化错误，绝不创建 aiohttp 会话、绝不发起
#   DNS/HTTP 请求（测试用本地桩断言命中数为 0）。
# - 端点响应约定为 JSON：{"results": [{"title", "url", "snippet"}]}，也接受裸数组
#   以及 items / data 字段；单次响应最多读取 1 MB，默认 15 s 超时。
WEBSEARCH_MAX_RESULTS = 10
WEBSEARCH_MAX_QUERY_CHARS = 500
WEBSEARCH_MAX_RESPONSE_BYTES = 1_000_000
WEBSEARCH_DEFAULT_TIMEOUT = 15
WEBSEARCH_MAX_TIMEOUT = 30
WEBSEARCH_TITLE_LIMIT = 300
WEBSEARCH_SNIPPET_LIMIT = 1000
WEBSEARCH_CONFIG_HINT = (
    'PUT /api/settings {"websearch":{"enabled":true,'
    '"endpoint":"https://<searxng-host>/search?q={query}&format=json"}}'
)

REDIS_READ_COMMANDS = {
    "get", "mget", "strlen", "ttl", "pttl", "type", "exists", "keys", "scan", "dbsize",
    "hget", "hgetall", "hkeys", "hvals", "hlen", "hexists", "hmget",
    "lrange", "llen", "lindex", "smembers", "scard", "sismember",
    "zrange", "zrevrange", "zcard", "zscore", "zrangebyscore",
    "xrange", "xrevrange", "xlen", "info", "list", "scan",
}


def is_dangerous_command(command: str) -> bool:
    if DANGEROUS_TOKENS.search(command):
        return True
    if WRAPPER_TOKENS.search(command) and re.search(r"(^|\s)(sudo|doas)\s", command):
        return True
    if re.search(r"[;&|`$]\s*\(", command):
        return True
    return False


def build_system_prompt(assets: list[dict], policy: dict, extra: str = "") -> str:
    lines = [SYSTEM_PROMPT.strip(), "", "当前已保存的资产（不含任何凭据）："]
    if not assets:
        lines.append("（无资产）")
    for a in assets[:120]:
        lines.append(f"- id={a['id']} type={a.get('typeName')} name={a['name']}")
    lines += [
        "",
        "当前审批策略：",
        f"- 静默读取文件：{policy.get('allowSilentFileRead')}",
        f"- 静默写入文件：{policy.get('allowSilentFileWrite')}",
        f"- 静默执行命令：{policy.get('allowSilentCommand')}（危险命令始终需要审批）",
        f"- 静默只读 SQL：{policy.get('allowSilentReadonlySql')}",
        f"- 禁止本机操作：{policy.get('blockLocal')}",
    ]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


def provider_urls(provider: dict) -> tuple[str, dict]:
    protocol = (provider.get("protocol") or "openai").lower()
    base = (provider.get("baseUrl") or "").strip()
    if protocol == "anthropic":
        if not base:
            base = "https://api.anthropic.com"
        url = base.rstrip("/")
        if not url.endswith("/messages"):
            url = url + ("/messages" if url.endswith("/v1") else "/v1/messages")
        headers = {
            "x-api-key": provider.get("apiKey") or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if provider.get("extraHeaders"):
            headers.update({str(k): str(v) for k, v in provider["extraHeaders"].items()})
        return url, headers
    if not base:
        base = "https://api.openai.com/v1"
    url = base.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    headers = {"content-type": "application/json"}
    if provider.get("apiKey"):
        headers["Authorization"] = f"Bearer {provider['apiKey']}"
    if provider.get("extraHeaders"):
        headers.update({str(k): str(v) for k, v in provider["extraHeaders"].items()})
    return url, headers


class ChatRunner:
    def __init__(self, hub, store, emit, policy: dict) -> None:
        self.hub = hub
        self.store = store
        self.emit = emit
        self.policy = policy or {}
        self.pending: dict[str, asyncio.Future] = {}
        self.remembered: set[str] = set()
        self.cancelled = False
        self.final_content = ""
        self.reasoning = ""
        self.usage: dict[str, Any] = {}
        self.tool_rounds = 0

    # ------------------------------------------------------------- approval
    async def resolve_approval(self, approval_id: str, approve: bool, remember: bool = False) -> bool:
        future = self.pending.pop(approval_id, None)
        if not future or future.done():
            return False
        if approve and remember:
            self.remembered.add(approval_id.split(":")[0])
        future.set_result(bool(approve))
        return True

    def cancel(self) -> None:
        self.cancelled = True
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.set_result(False)

    async def _approve(self, kind: str, title: str, detail: dict) -> bool:
        if kind in self.remembered:
            return True
        approval_id = f"{kind}:{new_id()}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[approval_id] = future
        await self.emit(
            {
                "type": "approval",
                "id": approval_id,
                "kind": kind,
                "title": title,
                "detail": detail,
            }
        )
        try:
            return bool(await asyncio.wait_for(future, timeout=900))
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(approval_id, None)

    # ------------------------------------------------------------------ run
    async def run(
        self,
        provider: dict,
        model: str,
        messages: list[dict],
        system_prompt: str,
        max_rounds: int = 12,
        tools: list[dict] | None = None,
    ) -> dict:
        protocol = (provider.get("protocol") or "openai").lower()
        tools = tools or TOOLS
        await self.emit({"type": "start", "model": model, "provider": provider.get("name") or provider.get("id")})
        for round_index in range(max_rounds):
            if self.cancelled:
                break
            self.tool_rounds = round_index
            if protocol == "anthropic":
                result = await self._call_anthropic(provider, model, messages, system_prompt, tools)
            else:
                result = await self._call_openai(provider, model, messages, system_prompt, tools)
            content = result.get("content") or ""
            tool_calls = result.get("toolCalls") or []
            if content:
                self.final_content += content
            if result.get("reasoning"):
                self.reasoning += result["reasoning"]
            if result.get("usage"):
                for k, v in result["usage"].items():
                    if isinstance(v, (int, float)):
                        self.usage[k] = self.usage.get(k, 0) + v
            if not tool_calls:
                break
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
            assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            for call in tool_calls:
                if self.cancelled:
                    break
                args = call.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                await self.emit(
                    {
                        "type": "tool_start",
                        "id": call.get("id"),
                        "name": call.get("name"),
                        "arguments": args,
                    }
                )
                ok, output = await self._run_tool(call.get("name") or "", args)
                output_text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
                output_text = clamp_text(output_text, MAX_TOOL_OUTPUT)
                await self.emit(
                    {
                        "type": "tool_result",
                        "id": call.get("id"),
                        "name": call.get("name"),
                        "ok": ok,
                        "output": output_text,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": call.get("name"),
                        "content": output_text,
                    }
                )
        if self.usage:
            await self.emit({"type": "usage", "usage": self.usage})
        await self.emit({"type": "done", "content": self.final_content, "reasoning": self.reasoning})
        return {
            "content": self.final_content,
            "reasoning": self.reasoning,
            "usage": self.usage,
            "toolRounds": self.tool_rounds,
        }

    # --------------------------------------------------------- model clients
    @staticmethod
    def _openai_messages(messages: list[dict], system_prompt: str) -> list[dict]:
        out = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            if msg["role"] == "tool":
                out.append(
                    {"role": "tool", "tool_call_id": msg.get("tool_call_id"), "content": msg.get("content", "")}
                )
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or None,
                        "tool_calls": [
                            {
                                "id": c.get("id"),
                                "type": "function",
                                "function": {
                                    "name": c.get("name"),
                                    "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False),
                                },
                            }
                            for c in msg["tool_calls"]
                        ],
                    }
                )
            else:
                out.append({"role": msg["role"], "content": msg.get("content") or ""})
        return out

    async def _call_openai(self, provider, model, messages, system_prompt, tools) -> dict:
        url, headers = provider_urls(provider)
        body: dict[str, Any] = {
            "model": model,
            "messages": self._openai_messages(messages, system_prompt),
            "stream": True,
            "temperature": float(provider.get("temperature", 0.3)),
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]
        if provider.get("maxTokens"):
            body["max_tokens"] = int(provider["maxTokens"])
        content, reasoning = "", ""
        tool_calls: dict[int, dict] = {}
        usage: dict = {}
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise AIError(f"模型接口返回 {resp.status}：{clamp_text(text, 1200)}")
                async for raw in resp.content:
                    if self.cancelled:
                        raise AIError("已取消")
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content += piece
                        await self.emit({"type": "delta", "text": piece})
                    think = delta.get("reasoning_content") or delta.get("reasoning")
                    if think:
                        reasoning += think
                        await self.emit({"type": "reasoning", "text": think})
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index") or 0)
                        slot = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
        calls = []
        for idx in sorted(tool_calls):
            slot = tool_calls[idx]
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            calls.append(
                {
                    "id": slot["id"] or f"call_{new_id()}",
                    "name": slot["name"],
                    "arguments": args,
                }
            )
        usage_map = {}
        if usage:
            usage_map = {
                "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
                "total_tokens": usage.get("total_tokens") or 0,
            }
        return {"content": content, "reasoning": reasoning, "toolCalls": calls, "usage": usage_map}

    @staticmethod
    def _anthropic_messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for msg in messages:
            role = msg["role"]
            if role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id"),
                    "content": msg.get("content", ""),
                }
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif role == "assistant" and msg.get("tool_calls"):
                blocks = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for call in msg["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "input": call.get("arguments") or {},
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": role, "content": msg.get("content") or ""})
        return out

    async def _call_anthropic(self, provider, model, messages, system_prompt, tools) -> dict:
        url, headers = provider_urls(provider)
        body: dict[str, Any] = {
            "model": model,
            "system": system_prompt,
            "messages": self._anthropic_messages(messages),
            "max_tokens": int(provider.get("maxTokens") or 8192),
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                for t in tools
            ]
        content, reasoning = "", ""
        tool_calls: dict[int, dict] = {}
        current_index = -1
        usage: dict = {}
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise AIError(f"Claude 接口返回 {resp.status}：{clamp_text(text, 1200)}")
                async for raw in resp.content:
                    if self.cancelled:
                        raise AIError("已取消")
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "message_start":
                        u = (event.get("message") or {}).get("usage") or {}
                        usage["prompt_tokens"] = u.get("input_tokens", 0)
                    elif etype == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            current_index += 1
                            tool_calls[current_index] = {
                                "id": block.get("id") or f"toolu_{new_id()}",
                                "name": block.get("name"),
                                "arguments": "",
                            }
                    elif etype == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            content += delta.get("text") or ""
                            await self.emit({"type": "delta", "text": delta.get("text") or ""})
                        elif delta.get("type") == "thinking_delta":
                            reasoning += delta.get("thinking") or ""
                            await self.emit({"type": "reasoning", "text": delta.get("thinking") or ""})
                        elif delta.get("type") == "input_json_delta":
                            if current_index in tool_calls:
                                tool_calls[current_index]["arguments"] += delta.get("partial_json") or ""
                    elif etype == "message_delta":
                        u = event.get("usage") or {}
                        if u.get("output_tokens"):
                            usage["completion_tokens"] = u["output_tokens"]
        calls = []
        for idx in sorted(tool_calls):
            slot = tool_calls[idx]
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            calls.append({"id": slot["id"], "name": slot["name"], "arguments": args})
        return {"content": content, "reasoning": reasoning, "toolCalls": calls, "usage": usage}

    # ------------------------------------------------------------------ tools
    def _silent(self, kind: str, command: str = "", sql: str = "") -> bool:
        p = self.policy
        if kind == "read_file":
            return bool(p.get("allowSilentFileRead", True))
        if kind == "write_file":
            return bool(p.get("allowSilentFileWrite", False))
        if kind == "shell":
            if is_dangerous_command(command):
                return False
            return bool(p.get("allowSilentCommand", False))
        if kind == "sql_read":
            return bool(p.get("allowSilentReadonlySql", True))
        if kind == "sql_write":
            return False
        if kind == "redis_write":
            return False
        if kind == "docker_action":
            return False
        if kind == "ftp_write":
            return False
        if kind == "websearch":
            # 默认每次调用都审批；用户可在 settings.websearch.requireApproval
            # 显式关闭。关闭后仍需端点已配置，并继续受域名过滤/超时/结果截断约束。
            return not bool(self._websearch_config().get("requireApproval", True))
        return False

    async def _require(self, kind: str, title: str, detail: dict) -> bool:
        if self._silent(kind, detail.get("command", ""), detail.get("sql", "")):
            return True
        return await self._approve(kind, title, detail)

    async def _run_tool(self, name: str, args: dict) -> tuple[bool, Any]:
        handler = getattr(self, f"tool_{name}", None)
        if not handler:
            return False, f"未知工具：{name}"
        try:
            return await handler(args or {})
        except (DatabaseError, RedisError, DockerError, FTPError, ValueError, OSError) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # defensive: never crash the agent loop
            return False, f"工具执行异常：{type(exc).__name__}: {exc}"

    async def tool_list_assets(self, args: dict):
        assets = self.store.list_assets()
        return True, [
            {"id": a["id"], "name": a["name"], "type": a["type"], "typeName": a["typeName"], "parentId": a["parentId"]}
            for a in assets
        ]

    async def tool_list_dir(self, args: dict):
        from fsops import FileOpError, local_list, sftp_list

        path = args.get("path") or "~"
        asset_id = args.get("asset_id")
        if asset_id:
            async with self.hub.sftp(asset_id) as sftp:
                data = await asyncio.to_thread(sftp_list, sftp, path)
        else:
            data = await asyncio.to_thread(local_list, path)
        entries = [
            {"name": e["name"], "isDir": e["isDir"], "size": e["size"], "mtime": e["mtime"]}
            for e in data["entries"]
        ]
        return True, {"path": data["path"], "entries": entries[:500], "count": len(entries)}

    async def tool_read_file(self, args: dict):
        from fsops import local_read_text, sftp_read_text

        path = args.get("path") or ""
        asset_id = args.get("asset_id")
        limit = int(args.get("max_bytes") or 200_000)
        if not await self._require("read_file", f"读取文件 {path}", {"path": path}):
            return False, "用户拒绝读取该文件"
        if asset_id:
            async with self.hub.sftp(asset_id) as sftp:
                data = await asyncio.to_thread(sftp_read_text, sftp, path, limit)
        else:
            data = await asyncio.to_thread(local_read_text, path, limit)
        return True, {"path": data["path"], "content": clamp_text(data["content"], limit), "size": data["size"]}

    async def _read_old_content(self, path: str, asset_id: str | None) -> str | None:
        from fsops import local_read_text, sftp_read_text

        try:
            if asset_id:
                async with self.hub.sftp(asset_id) as sftp:
                    return (await asyncio.to_thread(sftp_read_text, sftp, path))["content"]
            return (await asyncio.to_thread(local_read_text, path))["content"]
        except Exception:
            return None

    async def tool_write_file(self, args: dict):
        from fsops import local_write_text, sftp_write_text

        path = args.get("path") or ""
        content = args.get("content") or ""
        asset_id = args.get("asset_id")
        old_content = await self._read_old_content(path, asset_id)
        preview = content[:4000]
        diff = ""
        if old_content is not None and old_content != content:
            diff = "\n".join(
                difflib.unified_diff(
                    old_content.splitlines(), content.splitlines(), fromfile=path, tofile=path, lineterm=""
                )
            )[:8000]
        if not await self._require(
            "write_file",
            f"写入文件 {path}",
            {
                "path": path,
                "asset_id": asset_id,
                "bytes": len(content.encode()),
                "preview": preview,
                "diff": diff,
                "existed": old_content is not None,
            },
        ):
            return False, "用户拒绝写入"
        backup_id = agent_backup.snapshot(self.store.home, asset_id, path, old_content)
        if asset_id:
            async with self.hub.sftp(asset_id) as sftp:
                data = await asyncio.to_thread(sftp_write_text, sftp, path, content)
        else:
            data = await asyncio.to_thread(local_write_text, path, content)
        return True, {"ok": True, **data, "backupId": backup_id}

    async def _ssh_exec(self, asset_id: str, command: str, timeout: int = 60) -> dict:
        result = await self.hub.ssh_exec(asset_id, command, timeout)
        return {
            "exitCode": result["exitCode"],
            "stdout": clamp_text(result["stdout"], MAX_TOOL_OUTPUT),
            "stderr": clamp_text(result["stderr"], MAX_TOOL_OUTPUT),
        }

    async def tool_shell(self, args: dict):
        command = args.get("command") or ""
        asset_id = args.get("asset_id")
        timeout = int(args.get("timeout") or 60)
        if self.policy.get("blockLocal") and not asset_id:
            return False, "策略禁止在本机执行命令"
        if not await self._require(
            "shell",
            f"执行命令：{command[:120]}",
            {
                "command": command,
                "asset_id": asset_id,
                "dangerous": is_dangerous_command(command),
                "target": "remote" if asset_id else "local",
            },
        ):
            return False, "用户拒绝执行命令"
        if asset_id:
            return True, await self._ssh_exec(asset_id, command, timeout)
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser("~"),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"命令超时（{timeout}s）"
        return True, {
            "exitCode": proc.returncode,
            "stdout": clamp_text(stdout.decode("utf-8", "replace"), MAX_TOOL_OUTPUT),
            "stderr": clamp_text(stderr.decode("utf-8", "replace"), MAX_TOOL_OUTPUT),
        }

    async def tool_list_db_objects(self, args: dict):
        asset_id = args.get("asset_id")
        if not asset_id:
            return False, "缺少 asset_id"
        adapter = await self.hub.open_db(asset_id)
        try:
            databases = await asyncio.to_thread(adapter.list_databases)
            db_name = args.get("database") or (databases[0]["name"] if databases else None)
            objects = await asyncio.to_thread(adapter.list_objects, db_name, None)
            schemas = []
            try:
                schemas = await asyncio.to_thread(adapter.list_schemas, db_name)
            except Exception:
                schemas = []
            return True, {"databases": databases, "schemas": schemas, "objects": objects}
        finally:
            adapter.close()

    async def tool_execute_sql(self, args: dict):
        asset_id = args.get("asset_id")
        sql = args.get("sql") or ""
        if not asset_id or not sql.strip():
            return False, "缺少 asset_id 或 sql"
        readonly = bool(SQL_READ.match(sql)) and not re.search(r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b", sql, re.I)
        if not await self._require(
            "sql_read" if readonly else "sql_write",
            "执行只读 SQL" if readonly else "执行写 SQL",
            {"asset_id": asset_id, "sql": sql[:4000]},
        ):
            return False, "用户拒绝执行 SQL"
        adapter = await self.hub.open_db(asset_id)
        try:
            result = await asyncio.to_thread(adapter.query, sql, args.get("database"))
            for res in result["results"]:
                res["rows"] = res["rows"][:200]
            return True, result
        finally:
            adapter.close()

    async def tool_redis_command(self, args: dict):
        asset_id = args.get("asset_id")
        command = (args.get("command") or "").strip()
        if not asset_id or not command:
            return False, "缺少 asset_id 或 command"
        cmd_name = command.split()[0].lower()
        readonly = cmd_name in REDIS_READ_COMMANDS
        if not await self._require(
            "read_file" if readonly else "redis_write",
            f"Redis 命令：{command[:120]}",
            {"command": command, "asset_id": asset_id, "readonly": readonly},
        ):
            return False, "用户拒绝执行 Redis 命令"
        svc, tunnel = await self.hub.open_redis(asset_id)
        try:
            return True, await asyncio.to_thread(svc.console, int(args.get("db") or 0), command)
        finally:
            svc.close()
            if tunnel:
                tunnel.stop()

    async def tool_docker_containers(self, args: dict):
        service, transport = await self.hub.docker(args.get("asset_id"))
        try:
            return True, await asyncio.to_thread(service.containers)
        finally:
            if transport:
                transport.close()

    async def tool_docker_logs(self, args: dict):
        if not args.get("container"):
            return False, "缺少 container"
        service, transport = await self.hub.docker(args.get("asset_id"))
        try:
            logs = await asyncio.to_thread(service.logs, args["container"], int(args.get("tail") or 200))
            return True, clamp_text(logs, MAX_TOOL_OUTPUT)
        finally:
            if transport:
                transport.close()

    async def tool_docker_container_action(self, args: dict):
        action = str(args.get("action") or "").strip().lower()
        container = str(args.get("container") or "").strip()
        if action not in DOCKER_ACTIONS:
            return False, {
                "error": {
                    "code": "invalid_action",
                    "action": action,
                    "message": f"action 必须是 {' / '.join(DOCKER_ACTIONS)} 之一",
                }
            }
        if not container:
            return False, {"error": {"code": "missing_container", "message": "缺少 container"}}
        if not await self._require(
            "docker_action",
            f"Docker 容器操作：{action} {container}",
            {
                "asset_id": args.get("asset_id"),
                "container": container,
                "action": action,
                "target": "remote" if args.get("asset_id") else "local",
            },
        ):
            return False, "用户拒绝执行 Docker 容器操作"
        transport = None
        try:
            service, transport = await self.hub.docker(args.get("asset_id"))
            containers = await asyncio.to_thread(service.containers)
            wanted = container.lower()
            match = None
            for item in containers:
                cid = str(item.get("id") or "").lower()
                name = str(item.get("name") or "")
                if container == name or (cid and (wanted == cid or cid.startswith(wanted))):
                    match = item
                    break
            if match is None:
                return False, {
                    "error": {
                        "code": "container_not_found",
                        "container": container,
                        "message": f"容器不存在：{container}（先用 docker_containers 确认名称或 ID）",
                    }
                }
            name = str(match.get("name") or container)
            try:
                result = await asyncio.to_thread(service.action, name, action)
            except DockerError as exc:
                return False, {
                    "error": {
                        "code": "docker_cli_error",
                        "action": action,
                        "container": name,
                        "message": str(exc),
                    }
                }
            return True, {
                "ok": True,
                "action": action,
                "container": name,
                "id": match.get("id"),
                "previousState": match.get("state"),
                "result": result,
            }
        except DockerError as exc:
            return False, {
                "error": {"code": "docker_error", "action": action, "container": container, "message": str(exc)}
            }
        finally:
            if transport:
                transport.close()

    async def tool_list_sessions(self, args: dict):
        return True, self.hub.sessions.list()

    async def tool_read_terminal(self, args: dict):
        session = self.hub.sessions.get(args.get("session_id") or "")
        if not session:
            return False, "会话不存在"
        text = strip_ansi(bytes(session.buffer).decode("utf-8", "replace"))
        lines = int(args.get("lines") or 120)
        tail = "\n".join(text.splitlines()[-lines:])
        return True, {"session": session.info(), "content": clamp_text(tail, MAX_TOOL_OUTPUT)}

    async def tool_terminal_execute(self, args: dict):
        session = self.hub.sessions.get(args.get("session_id") or "")
        if not session:
            return False, "会话不存在"
        command = args.get("command") or ""
        if not await self._require(
            "shell",
            f"向终端注入命令：{command[:120]}",
            {"command": command, "session": session.id, "dangerous": is_dangerous_command(command)},
        ):
            return False, "用户拒绝执行"
        await session.write((command + "\n").encode())
        return True, {"ok": True, "sessionId": session.id}

    async def tool_scp_upload(self, args: dict):
        from scp_svc import SCPClient

        asset_id = args.get("asset_id")
        local_path = os.path.expanduser(args.get("local_path") or "")
        remote_path = args.get("remote_path") or ""
        if not asset_id or not local_path or not remote_path:
            return False, "缺少 asset_id / local_path / remote_path"
        if not os.path.isfile(local_path):
            return False, f"本机文件不存在：{local_path}"
        size = os.path.getsize(local_path)
        if not await self._require(
            "write_file",
            f"SCP 上传 {local_path} → {remote_path}",
            {"asset_id": asset_id, "local_path": local_path, "remote_path": remote_path, "bytes": size},
        ):
            return False, "用户拒绝 SCP 上传"
        async with self.hub.ssh_transport(asset_id) as transport:
            client = SCPClient(transport)
            result = await asyncio.to_thread(client.upload, local_path, remote_path)
        return True, result

    async def tool_scp_download(self, args: dict):
        from scp_svc import SCPClient

        asset_id = args.get("asset_id")
        remote_path = args.get("remote_path") or ""
        local_path = os.path.expanduser(args.get("local_path") or "")
        if not asset_id or not remote_path or not local_path:
            return False, "缺少 asset_id / remote_path / local_path"
        if not await self._require(
            "write_file",
            f"SCP 下载 {remote_path} → {local_path}",
            {"asset_id": asset_id, "remote_path": remote_path, "local_path": local_path},
        ):
            return False, "用户拒绝 SCP 下载"
        async with self.hub.ssh_transport(asset_id) as transport:
            client = SCPClient(transport)
            result = await asyncio.to_thread(client.download, remote_path, local_path)
        return True, result

    async def tool_list_tunnels(self, args: dict):
        manager = getattr(self.hub, "tunnels", None)
        if not manager:
            return False, "隧道管理器不可用"
        return True, manager.list()

    async def tool_start_tunnel(self, args: dict):
        manager = getattr(self.hub, "tunnels", None)
        asset_id = args.get("asset_id")
        if not manager:
            return False, "隧道管理器不可用"
        if not asset_id:
            return False, "缺少 asset_id"
        if not await self._require(
            "shell", f"启动 SSH 隧道 {asset_id}", {"asset_id": asset_id, "tunnel": True}
        ):
            return False, "用户拒绝启动隧道"
        status = await manager.start(asset_id)
        return True, status

    async def tool_stop_tunnel(self, args: dict):
        manager = getattr(self.hub, "tunnels", None)
        tunnel_id = args.get("tunnel_id")
        if not manager or not tunnel_id:
            return False, "缺少 tunnel_id"
        if not await self._require("shell", f"停止 SSH 隧道 {tunnel_id}", {"tunnel_id": tunnel_id}):
            return False, "用户拒绝停止隧道"
        return True, {"stopped": await manager.stop(tunnel_id)}

    async def tool_ftp_list(self, args: dict):
        asset_id = args.get("asset_id")
        if not asset_id:
            return False, "缺少 asset_id"
        client = await self.hub.open_ftp(asset_id)
        try:
            data = await asyncio.to_thread(client.list_dir, args.get("path") or "~")
        finally:
            await asyncio.to_thread(client.close)
        return True, {
            "path": data["path"],
            "entries": data["entries"][:500],
            "count": len(data["entries"]),
            "protocol": data.get("protocol"),
        }

    async def tool_ftp_upload(self, args: dict):
        asset_id = args.get("asset_id")
        local_path = os.path.expanduser(str(args.get("local_path") or ""))
        remote_path = str(args.get("remote_path") or "").strip()
        if not asset_id or not local_path or not remote_path:
            return False, "缺少 asset_id / local_path / remote_path"
        if "\x00" in local_path or "\x00" in remote_path:
            return False, "路径包含非法字符"
        if not os.path.isfile(local_path):
            return False, f"本机文件不存在：{local_path}"
        size = os.path.getsize(local_path)
        if not await self._require(
            "ftp_write",
            f"FTP 上传 {local_path} → {remote_path}",
            {"asset_id": asset_id, "local_path": local_path, "remote_path": remote_path, "bytes": size},
        ):
            return False, "用户拒绝 FTP 上传"
        client = await self.hub.open_ftp(asset_id)
        try:
            target = client.safe_path(remote_path)
            uploaded = await asyncio.to_thread(client.upload_from, local_path, target)
        finally:
            await asyncio.to_thread(client.close)
        return True, {"ok": True, "path": target, "size": uploaded}

    async def tool_ftp_download(self, args: dict):
        asset_id = args.get("asset_id")
        remote_path = str(args.get("remote_path") or "").strip()
        local_path = os.path.expanduser(str(args.get("local_path") or ""))
        if not asset_id or not remote_path or not local_path:
            return False, "缺少 asset_id / remote_path / local_path"
        if "\x00" in remote_path or "\x00" in local_path:
            return False, "路径包含非法字符"
        if not await self._require(
            "ftp_write",
            f"FTP 下载 {remote_path} → {local_path}",
            {"asset_id": asset_id, "remote_path": remote_path, "local_path": local_path},
        ):
            return False, "用户拒绝 FTP 下载"
        client = await self.hub.open_ftp(asset_id)
        try:
            target = client.safe_path(remote_path)
            size = await asyncio.to_thread(client.download_to, target, local_path)
        finally:
            await asyncio.to_thread(client.close)
        return True, {"ok": True, "path": target, "localPath": local_path, "size": size}

    async def tool_ftp_delete(self, args: dict):
        asset_id = args.get("asset_id")
        path = str(args.get("path") or "").strip()
        if not asset_id or not path:
            return False, "缺少 asset_id / path"
        if "\x00" in path:
            return False, "路径包含非法字符"
        if not await self._require(
            "ftp_write", f"FTP 删除 {path}", {"asset_id": asset_id, "path": path}
        ):
            return False, "用户拒绝 FTP 删除"
        client = await self.hub.open_ftp(asset_id)
        try:
            target = client.safe_path(path)
            await asyncio.to_thread(client.delete, target)
        finally:
            await asyncio.to_thread(client.close)
        return True, {"ok": True, "deleted": target}

    async def tool_ftp_rename(self, args: dict):
        asset_id = args.get("asset_id")
        src = str(args.get("from_path") or "").strip()
        dst = str(args.get("to_path") or "").strip()
        if not asset_id or not src or not dst:
            return False, "缺少 asset_id / from_path / to_path"
        if "\x00" in src or "\x00" in dst:
            return False, "路径包含非法字符"
        if not await self._require(
            "ftp_write",
            f"FTP 重命名 {src} → {dst}",
            {"asset_id": asset_id, "from_path": src, "to_path": dst},
        ):
            return False, "用户拒绝 FTP 重命名"
        client = await self.hub.open_ftp(asset_id)
        try:
            src_abs = client.safe_path(src)
            dst_abs = client.safe_path(dst)
            await asyncio.to_thread(client.rename, src_abs, dst_abs)
        finally:
            await asyncio.to_thread(client.close)
        return True, {"ok": True, "from": src_abs, "to": dst_abs}

    async def tool_web_fetch(self, args: dict):
        url = (args.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            return False, "仅支持 http/https 地址"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                raw = await resp.read()
                text = raw[:400_000].decode("utf-8", "replace")
                content_type = resp.headers.get("Content-Type", "")
                if "html" in content_type or text.lstrip().startswith("<"):
                    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
                    text = re.sub(r"(?s)<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text)
                return True, {
                    "url": str(resp.url),
                    "status": resp.status,
                    "contentType": content_type,
                    "text": clamp_text(text, 200_000),
                }

    # ------------------------------------------------------------- websearch
    def _websearch_config(self) -> dict:
        if self.store is None:
            return {}
        try:
            settings = self.store.get_settings() or {}
        except Exception:
            return {}
        cfg = settings.get("websearch") if isinstance(settings, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    @staticmethod
    def _websearch_error(
        code: str, message: str, *, status: int = 400, configure: str | None = None
    ) -> dict:
        error: dict[str, Any] = {"code": code, "status": status, "message": message}
        if configure:
            error["configure"] = configure
        return {"error": error}

    @staticmethod
    def _websearch_domains(value: Any, field: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{field} 必须是字符串数组")
        out: list[str] = []
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError(f"{field} 必须是字符串数组")
            item = raw.strip().lower().rstrip(".")
            if not item:
                continue
            if item.startswith("*."):
                item = item[2:]
            item = item.lstrip(".")
            if (
                not item
                or len(item) > 253
                or any(ch in item for ch in "/\\:@?#")
                or any(ch.isspace() for ch in item)
                or "\x00" in item
            ):
                raise ValueError(f"{field} 含非法域名：{raw[:80]}")
            if item not in out:
                out.append(item)
        if len(out) > 20:
            raise ValueError(f"{field} 最多 20 个域名")
        return out

    @staticmethod
    def _websearch_match(host: str, domains: list[str]) -> bool:
        host = host.lower().rstrip(".")
        return any(host == item or host.endswith("." + item) for item in domains)

    @staticmethod
    def _websearch_url(endpoint: str, query: str) -> tuple[str | None, str | None]:
        raw = endpoint.strip()
        if not raw:
            return None, None
        if "{query}" in raw:
            url = raw.replace("{query}", quote(query, safe=""))
        else:
            url = raw + ("&" if "?" in raw else "?") + "q=" + quote(query, safe="")
        try:
            parts = urlsplit(url)
            host = parts.hostname
        except ValueError:
            return None, None
        if parts.scheme.lower() not in ("http", "https") or not host:
            return None, None
        return url, host

    async def tool_websearch(self, args: dict):
        query = str(args.get("query") or "").strip()
        if not query:
            return False, self._websearch_error("invalid_query", "query 不能为空")
        if any(ord(ch) < 32 for ch in query):
            return False, self._websearch_error("invalid_query", "query 不能包含控制字符")
        query = re.sub(r"\s+", " ", query)
        if len(query) > WEBSEARCH_MAX_QUERY_CHARS:
            return False, self._websearch_error(
                "invalid_query", f"query 不能超过 {WEBSEARCH_MAX_QUERY_CHARS} 个字符"
            )

        try:
            allowed = self._websearch_domains(args.get("allowed_domains"), "allowed_domains")
            blocked = self._websearch_domains(args.get("blocked_domains"), "blocked_domains")
        except ValueError as exc:
            return False, self._websearch_error("invalid_domains", str(exc))

        cfg = self._websearch_config()
        raw_max = args.get("max_results")
        if raw_max is None:
            raw_max = cfg.get("maxResults", 8)
        try:
            if isinstance(raw_max, bool):
                raise ValueError
            max_results = int(raw_max)
        except (TypeError, ValueError):
            return False, self._websearch_error("invalid_max_results", "max_results 必须是整数")
        max_results = max(1, min(WEBSEARCH_MAX_RESULTS, max_results))

        if not bool(cfg.get("enabled")):
            return False, self._websearch_error(
                "websearch_disabled",
                "联网搜索已关闭（settings.websearch.enabled=false），未发起任何网络请求。",
                status=501,
                configure=WEBSEARCH_CONFIG_HINT,
            )
        endpoint = str(cfg.get("endpoint") or "").strip()
        if not endpoint:
            return False, self._websearch_error(
                "websearch_not_configured",
                "联网搜索端点未配置（settings.websearch.endpoint 为空），未发起任何网络请求。",
                status=501,
                configure=WEBSEARCH_CONFIG_HINT,
            )
        url, host = self._websearch_url(endpoint, query)
        if not url or not host:
            return False, self._websearch_error(
                "websearch_endpoint_invalid",
                "联网搜索端点必须是带主机名的 http(s) URL。",
                status=400,
                configure=WEBSEARCH_CONFIG_HINT,
            )

        try:
            timeout_seconds = int(cfg.get("timeoutSeconds") or WEBSEARCH_DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout_seconds = WEBSEARCH_DEFAULT_TIMEOUT
        timeout_seconds = max(1, min(WEBSEARCH_MAX_TIMEOUT, timeout_seconds))

        if not await self._require(
            "websearch",
            f"联网搜索「{clamp_text(query, 80)}」",
            {
                "query": clamp_text(query, 200),
                "endpoint": host,
                "maxResults": max_results,
                "allowedDomains": allowed,
                "blockedDomains": blocked,
            },
        ):
            return False, "用户拒绝联网搜索"

        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(5, timeout_seconds))
        headers = {"Accept": "application/json", "User-Agent": "H1KHub/5.1.9 websearch"}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    if not 200 <= resp.status < 300:
                        return False, self._websearch_error(
                            "websearch_http_error",
                            f"搜索端点返回 HTTP {resp.status}",
                            status=502,
                        )
                    raw = await resp.content.read(WEBSEARCH_MAX_RESPONSE_BYTES + 1)
        except asyncio.TimeoutError:
            return False, self._websearch_error(
                "websearch_timeout", f"搜索超时（>{timeout_seconds}s）", status=504
            )
        except aiohttp.ClientError as exc:
            return False, self._websearch_error(
                "websearch_failed", f"{type(exc).__name__}: {exc}", status=502
            )

        if len(raw) > WEBSEARCH_MAX_RESPONSE_BYTES:
            return False, self._websearch_error(
                "websearch_response_too_large",
                f"搜索端点响应超过 {WEBSEARCH_MAX_RESPONSE_BYTES} 字节",
                status=502,
            )
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, self._websearch_error(
                "websearch_bad_response", "搜索端点返回的不是合法 JSON", status=502
            )

        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("results")
            if items is None:
                items = payload.get("items")
            if items is None:
                items = payload.get("data")
        else:
            items = None
        if not isinstance(items, list):
            return False, self._websearch_error(
                "websearch_bad_response", "搜索端点 JSON 缺少 results 数组", status=502
            )

        results: list[dict] = []
        seen: set[str] = set()
        clipped = False
        for item in items:
            if not isinstance(item, dict):
                continue
            title = next(
                (item[k] for k in ("title", "name") if isinstance(item.get(k), str)), ""
            )
            link = next(
                (item[k] for k in ("url", "link", "href") if isinstance(item.get(k), str)), ""
            )
            snippet = next(
                (
                    item[k]
                    for k in ("snippet", "description", "content", "summary")
                    if isinstance(item.get(k), str)
                ),
                "",
            )
            link = link.strip()
            parts = None
            try:
                parts = urlsplit(link)
                link_host = parts.hostname
            except ValueError:
                link_host = None
            if parts is None or parts.scheme.lower() not in ("http", "https") or not link_host:
                continue
            if allowed and not self._websearch_match(link_host, allowed):
                continue
            if blocked and self._websearch_match(link_host, blocked):
                continue
            if link in seen:
                continue
            seen.add(link)
            title_text = " ".join(title.split())
            snippet_text = " ".join(snippet.split())
            if len(title_text) > WEBSEARCH_TITLE_LIMIT or len(snippet_text) > WEBSEARCH_SNIPPET_LIMIT:
                clipped = True
            results.append(
                {
                    "title": clamp_text(title_text, WEBSEARCH_TITLE_LIMIT) or link_host,
                    "url": clamp_text(link, 2048),
                    "snippet": clamp_text(snippet_text, WEBSEARCH_SNIPPET_LIMIT),
                }
            )
            if len(results) >= max_results:
                break

        return True, {
            "query": query,
            "engine": host,
            "count": len(results),
            "truncated": clipped,
            "results": results,
        }
