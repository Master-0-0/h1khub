#!/usr/bin/env python3
"""Regression test for RedisJSON support (h1khub_json module + UI editor).

Checks the module commands, the key-detail payload, JSON.SET through the API and
the terminal-side editor flow (open key, edit document, save, verify).

Usage: python3 scripts/test_redisjson.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from cdp_check import CDP, launch  # noqa: E402

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
ASSET = "Redis JSON"
KEY = "json:ui:test"


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def api(path: str, body=None, method="POST", raw=False):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        return {"__http_error": exc.code, "detail": exc.read().decode(errors="replace")[:400]}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def data_of(resp):
    return resp.get("data") if isinstance(resp, dict) and resp.get("ok") else None


async def js(cdp, expr):
    return await cdp.evaluate(expr)


async def wait_for(predicate, timeout=20.0, interval=0.4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await predicate():
                return True
        except Exception:
            pass
        await asyncio.sleep(interval)
    return False


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    assets = api("/api/assets", method="GET")["data"]["assets"]
    asset = next((a for a in assets if a["name"] == ASSET), None)
    check("redis json asset present", bool(asset), ASSET)
    if not asset:
        print(f"PASS={PASS} FAIL={FAIL}")
        return 1
    aid = asset["id"]

    print("\n[redisjson] module & API")
    mod = data_of(api("/api/redis/console", {"assetId": aid, "db": 0, "command": "MODULE LIST"}))
    text = json.dumps(mod, ensure_ascii=False)
    # Accept modules built before the rebrand (hexhub_json) so an existing lab
    # install does not fail the check until it is rebuilt.
    check("module loaded", "h1khub_json" in text or "hexhub_json" in text, text[:300])

    doc = {"name": "H1KHub", "version": "5.1.9", "features": ["db", "ssh"], "ok": True, "score": 4.5}
    set_resp = api("/api/redis/json-set", {"assetId": aid, "db": 0, "key": KEY, "value": json.dumps(doc)})
    check("json-set api", bool(data_of(set_resp)) or set_resp.get("ok"), str(set_resp)[:200])

    key = data_of(api("/api/redis/key", {"assetId": aid, "db": 0, "key": KEY}))
    check("key type is ReJSON-RL", (key or {}).get("info", {}).get("type") == "ReJSON-RL", str(key)[:300])
    check("json value parsed", (key or {}).get("value", {}).get("value") == doc, str((key or {}).get("value"))[:300])

    typed = data_of(api("/api/redis/console", {"assetId": aid, "db": 0, "command": "JSON.TYPE " + KEY}))
    check("JSON.TYPE object", "object" in json.dumps(typed, ensure_ascii=False), json.dumps(typed)[:200])
    bad = api("/api/redis/json-set", {"assetId": aid, "db": 0, "key": KEY, "value": "{not json"})
    check("invalid json rejected", bool(bad.get("error") or bad.get("__http_error")), str(bad)[:200])

    print("\n[redisjson] UI editor")
    rc = asyncio.run(ui_part(aid))
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES: " + ", ".join(FAILURES))
    return 0 if FAIL == 0 and rc == 0 else 1


async def ui_part(asset_id: str) -> int:
    proc, ws = await launch(BASE)
    cdp = CDP(ws)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    try:
        await asyncio.sleep(1.0)
        for _ in range(40):
            if await js(cdp, "document.querySelectorAll('.asset-panel .tree-row').length") > 0:
                break
            await asyncio.sleep(0.25)
        for text, wait_s in (("生产环境", 0.5), ("Redis JSON", 3)):
            await js(
                cdp,
                "(()=>{const t=%s;const els=[...document.querySelectorAll('.asset-panel .tree-row')];"
                "const el=els.find(e=>(e.innerText||'').includes(t));"
                "if(el)el.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));return !!el})()" % json.dumps(text),
            )
            await asyncio.sleep(wait_s)
        await wait_for(lambda: js(cdp, "document.querySelectorAll('.key-row').length > 0"), timeout=12)
        row = await js(
            cdp,
            "(()=>{const els=[...document.querySelectorAll('.key-row')];"
            "const el=els.find(e=>(e.innerText||'').includes(%s));"
            "if(!el)return 'not-found';el.click();return 'ok'})()" % json.dumps(KEY),
        )
        check("key row clicked", row == "ok", str(row))
        shown = await wait_for(
            lambda: js(cdp, "(()=>{const t=[...document.querySelectorAll('textarea')].find(x=>x.value.includes('\"name\"'));return !!t})()"),
            timeout=12,
        )
        check("json editor shows document", shown)

        updated = {"name": "H1KHub", "version": "5.1.9", "features": ["db", "ssh", "redis"], "ok": True, "score": 5}
        payload = json.dumps(updated)
        await js(
            cdp,
            "(()=>{const ta=[...document.querySelectorAll('textarea')].find(x=>x.value.includes('\"name\"'));"
            "if(!ta)return false;const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;"
            "setter.call(ta,%s);ta.dispatchEvent(new Event('input',{bubbles:true}));return true})()" % json.dumps(payload),
        )
        clicked = await js(
            cdp,
            "(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').includes('保存 JSON'));"
            "if(!b)return 'not-found';b.click();return 'ok'})()",
        )
        check("save button clicked", clicked == "ok", str(clicked))

        async def saved():
            key = data_of(api("/api/redis/key", {"assetId": asset_id, "db": 0, "key": KEY}))
            return (key or {}).get("value", {}).get("value") == updated

        check("save persisted via JSON.SET", await wait_for(saved, timeout=15))
    finally:
        await cdp.close()
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
