#!/usr/bin/env python3
"""Regression test for the binary protobuf RPC transport (original framing).

Verifies, against the live replica:
  1. XOR'd Packet framing (u16 methodLen | u32 dataLen | u32 id | method | data)
  2. request/reply with a protobuf TableDataResponse (db.query)
  3. ChannelOpen / ChannelSend / ChannelClose on a terminal channel
  4. the UI query path actually uses the protobuf RPC

Usage: python3 scripts/test_rpc_proto.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import sys
import time
import urllib.request

import websockets

sys.path.insert(0, os.path.dirname(__file__))
from cdp_check import CDP, launch  # noqa: E402

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
WS_URL = ""


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def api(path: str, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def xor(buf: bytes) -> bytes:
    return bytes(b ^ (i & 0xFF) for i, b in enumerate(buf))


def encode_packet(method: str, msg_id: int, data: bytes = b"") -> bytes:
    name = method.encode()
    return xor(struct.pack(">HII", len(name), len(data), msg_id) + name + data)


def decode_packet(buf: bytes) -> dict:
    raw = xor(buf)
    mlen, dlen, mid = struct.unpack(">HII", raw[:10])
    return {"method": raw[10 : 10 + mlen].decode(), "id": mid, "data": raw[10 + mlen : 10 + mlen + dlen]}


def read_varint(buf, pos):
    value = 0
    shift = 0
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        value += (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return value, pos


def read_fields(buf):
    out = []
    pos = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = read_varint(buf, pos)
        elif wire == 2:
            length, pos = read_varint(buf, pos)
            value = buf[pos : pos + length]
            pos += length
        else:
            break
        out.append((field, wire, value))
    return out


def decode_table_data(buf: bytes) -> dict:
    """Mirror of proto_model.TableDataResponse."""
    cols, rows, offset, total = [], [], 0, 0
    for field, wire, value in read_fields(buf):
        if field == 1 and wire == 2:
            for wf, ww, wv in read_fields(value):
                if wf == 1 and ww == 2:
                    col = {"name": "", "type": ""}
                    for cf, cw, cv in read_fields(wv):
                        if cf == 1:
                            col["name"] = cv.decode()
                        elif cf == 2:
                            col["type"] = cv.decode()
                    cols.append(col)
                elif wf == 2 and ww == 2:
                    nulls, cells = [], []
                    for rf, rw, rv in read_fields(wv):
                        if rf == 1:
                            if rw == 2:
                                p = 0
                                while p < len(rv):
                                    v, p = read_varint(rv, p)
                                    nulls.append(bool(v))
                            else:
                                nulls.append(bool(rv))
                        elif rf == 2:
                            cells.append(rv.decode())
                    rows.append([None if (i < len(nulls) and nulls[i]) else v for i, v in enumerate(cells)])
        elif field == 2 and wire == 0:
            offset = value
        elif field == 3 and wire == 0:
            total = value
    return {"columns": cols, "rows": rows, "offset": offset, "totalCount": total}


async def wire_part(asset_id: str) -> None:
    print("\n[rpc] binary framing & protobuf")
    async with websockets.connect(WS_URL, max_size=16 * 1024 * 1024) as ws:
        # welcome arrives as JSON text before the socket switches to binary
        first = await asyncio.wait_for(ws.recv(), timeout=5)
        check("welcome packet", isinstance(first, str) and "welcome" in first, str(first)[:120])

        async def recv_packet(timeout=5.0):
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(msg, str):
                return {"method": "text", "id": -1, "data": msg.encode()}
            return decode_packet(msg)

        # request/reply + protobuf
        await ws.send(encode_packet("db.query", 77, json.dumps({"assetId": asset_id, "database": "demo", "sql": "select count(*) as n from users"}).encode()))
        reply = None
        for _ in range(20):
            pkt = await recv_packet(10)
            if pkt["id"] == 77:
                reply = pkt
                break
        check("db.query reply id", bool(reply), "no reply")
        if reply:
            table = decode_table_data(reply["data"])
            check("protobuf columns", [c["name"] for c in table["columns"]] == ["n"], str(table)[:200])
            check("protobuf row value", table["rows"] == [["3"]], str(table["rows"])[:200])

        # channel open/send/close on a terminal
        ssh = next(a for a in api("/api/assets", method="GET")["data"]["assets"] if a["name"] == "SSH hexlab")
        session = api("/api/sessions/start", {"assetId": ssh["id"], "title": "rpc-proto-test"})["data"]["session"]
        try:
            channel = f"term:{session['id']}"
            await ws.send(encode_packet("ChannelOpen", 0, json.dumps({"method": channel, "args": {}}).encode()))
            opened = None
            for _ in range(30):
                pkt = await recv_packet(15)
                if pkt["method"] == "ChannelOpen":
                    opened = pkt
                    break
            check("ChannelOpen reply", bool(opened), "no open")
            if opened:
                body = json.loads(opened["data"] or b"{}")
                check("ChannelOpen names channel", body.get("method") == channel, str(body)[:200])
                cid = int(body.get("channelId") or opened["id"])

                await asyncio.sleep(0.6)
                await ws.send(encode_packet("ChannelSend", cid, json.dumps({"type": "resize", "cols": 120, "rows": 32}).encode()))
                await ws.send(encode_packet("ChannelSend", cid, json.dumps({"type": "input", "data": __import__("base64").b64encode(b"echo RPC_BINARY_OK\n").decode()}).encode()))
                seen = ""
                deadline = time.time() + 15
                while time.time() < deadline:
                    try:
                        pkt = await recv_packet(2)
                    except asyncio.TimeoutError:
                        continue
                    if pkt["method"] == "ChannelSend" and pkt["id"] == cid:
                        payload = json.loads(pkt["data"] or b"{}")
                        if payload.get("type") in ("data", "history"):
                            import base64

                            seen += base64.b64decode(payload.get("data") or "").decode(errors="replace")
                        if "RPC_BINARY_OK" in seen:
                            break
                check("ChannelSend terminal echo", "RPC_BINARY_OK" in seen, seen[-200:].replace("\n", " | "))
                await ws.send(encode_packet("ChannelClose", cid, b"{}"))
        finally:
            api(f"/api/sessions/{session['id']}/close")


async def ui_part() -> None:
    print("\n[rpc] UI query uses protobuf RPC")
    proc, ws = await launch(BASE)
    cdp = CDP(ws)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    try:
        await asyncio.sleep(1.0)
        for _ in range(40):
            if await cdp.evaluate("document.querySelectorAll('.asset-panel .tree-row').length") > 0:
                break
            await asyncio.sleep(0.25)
        for text, wait_s in (("生产环境", 0.5), ("MySQL demo", 3)):
            await cdp.evaluate(
                "(()=>{const t=%s;const els=[...document.querySelectorAll('.asset-panel .tree-row')];"
                "const el=els.find(e=>(e.innerText||'').includes(t));"
                "if(el)el.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));return !!el})()" % json.dumps(text)
            )
            await asyncio.sleep(wait_s)
        await cdp.evaluate(
            "(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').includes('新建查询'));"
            "if(!b)return 'nf';b.click();return 'ok'})()"
        )
        await asyncio.sleep(1.5)
        await cdp.evaluate("document.querySelector('.cm-content')?.focus()")
        await cdp.send("Input.insertText", {"text": "select * from users"})
        await asyncio.sleep(0.4)
        await cdp.evaluate(
            "(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim().startsWith('执行')&&!x.disabled);"
            "if(!b)return 'nf';b.click();return 'ok'})()"
        )
        await asyncio.sleep(3.5)
        state = await cdp.evaluate("JSON.stringify(window.__h1khubRpc||null)")
        rows = await cdp.evaluate("document.querySelectorAll('.grid tbody tr').length")
        print("  rpc state:", state)
        check("UI reports binary mode", bool(state) and '"mode":"binary"' in state, str(state))
        check("UI decoded protobuf reply", bool(state) and '"protoQuery"' in state, str(state))
        check("UI grid rows", rows == 3, str(rows))
    finally:
        await cdp.close()
        proc.kill()


def main() -> int:
    global BASE, TOKEN, WS_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    WS_URL = BASE.replace("http", "ws", 1) + f"/rpc?token={TOKEN}"

    assets = api("/api/assets", method="GET")["data"]["assets"]
    mysql = next((a for a in assets if a["name"] == "MySQL demo"), None)
    check("mysql asset present", bool(mysql))
    if not mysql:
        print(f"PASS={PASS} FAIL={FAIL}")
        return 1

    asyncio.run(wire_part(mysql["id"]))
    asyncio.run(ui_part())
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES: " + ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
