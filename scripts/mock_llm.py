"""Minimal OpenAI-compatible mock used to verify the AI agent loop end to end."""
import asyncio
import json
import re
import time

from aiohttp import web

PORT = 39001


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def chunk(delta, finish=None):
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock-1",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


async def models(request):
    return web.json_response({"object": "list", "data": [{"id": "mock-1", "object": "model"}]})


async def chat(request):
    body = await request.json()
    messages = body.get("messages") or []
    user_text = ""
    for m in messages:
        if m.get("role") == "user":
            last = m.get("content")
            if isinstance(last, str):
                user_text = last
    has_tool_result = any(m.get("role") == "tool" for m in messages)
    tools = body.get("tools") or []
    tool_names = [t.get("function", {}).get("name") for t in tools]
    wants_shell = "shell" in user_text.lower() and "shell" in tool_names
    wants_list = ("asset" in user_text.lower() or "资产" in user_text) and "list_assets" in tool_names

    resp = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
    await resp.prepare(request)

    async def emit(obj):
        await resp.write(sse(obj))
        await asyncio.sleep(0.02)

    if has_tool_result:
        tool_msg = next((m for m in reversed(messages) if m.get("role") == "tool"), {})
        output = str(tool_msg.get("content") or "")[:1200]
        text = f"工具已执行完成 ✅\n\n```\n{output}\n```\n\n以上是真实调用结果，你可以继续让我检查其他内容。"
        for i in range(0, len(text), 12):
            await emit(chunk({"content": text[i : i + 12]}))
        await emit(chunk({}, "stop"))
        await emit({"id": "chatcmpl-mock", "object": "chat.completion.chunk", "choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}})
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    match = re.search(r"@tool\s+([a-zA-Z_]+)\s*(\{.*\})", user_text, re.S)
    if match and match.group(1) in tool_names:
        name = match.group(1)
        arguments = match.group(2)
        await emit(chunk({"content": f"调用工具 {name}。"}))
        await emit(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_{name}",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ]
                }
            )
        )
        await emit(chunk({}, "tool_calls"))
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    if wants_shell:
        await emit(chunk({"content": "我来执行一条命令验证终端能力。"}))
        await emit(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_shell_1",
                            "type": "function",
                            "function": {"name": "shell", "arguments": ""},
                        }
                    ]
                }
            )
        )
        for part in ['{"command":"echo hello-from-h1khub-agent && uname -s",', '"timeout":20}']:
            await emit(
                chunk(
                    {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": part}}
                        ]
                    }
                )
            )
        await emit(chunk({}, "tool_calls"))
    elif wants_list:
        await emit(chunk({"content": "先看一下当前保存的资产。"}))
        await emit(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_list_1",
                            "type": "function",
                            "function": {"name": "list_assets", "arguments": "{}"},
                        }
                    ]
                }
            )
        )
        await emit(chunk({}, "tool_calls"))
    else:
        text = "你好！我是 H1KHub 复刻版的 AI 助手，可以调用工具操作 SSH、数据库、Docker 和文件。"
        for i in range(0, len(text), 8):
            await emit(chunk({"content": text[i : i + 8]}))
        await emit(chunk({}, "stop"))

    await resp.write(b"data: [DONE]\n\n")
    await resp.write_eof()
    return resp


app = web.Application()
app.router.add_get("/v1/models", models)
app.router.add_post("/v1/chat/completions", chat)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
