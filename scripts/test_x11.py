#!/usr/bin/env python3
"""Regression test for SSH X11 forwarding.

Enables x11 on the demo SSH asset, opens the terminal in the UI, then runs
`xdpyinfo` through the forwarded display and checks the X server answered.
The SSH session metadata must also report the forwarded local display.

Usage: python3 scripts/test_x11.py [--base http://127.0.0.1:38110]
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


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def api(path: str, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


async def js(cdp, expr):
    return await cdp.evaluate(expr)


async def term_text(cdp):
    return await js(
        cdp,
        "(()=>{const t=window.__h1khubTerm;if(!t)return '';const b=t.buffer.active;"
        "let o=[];for(let i=0;i<b.length;i++){const l=b.getLine(i);if(l)o.push(l.translateToString(true))}"
        "return o.join('\\n')})()",
    )


async def type_text(cdp, text):
    await js(cdp, "document.querySelector('.xterm-helper-textarea')?.focus()")
    await cdp.send("Input.insertText", {"text": text})
    await asyncio.sleep(0.25)
    for kind in ("keyDown", "keyUp"):
        await cdp.send(
            "Input.dispatchKeyEvent",
            {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
        )


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
    ssh = next((a for a in assets if a["name"] == "SSH hexlab"), None)
    check("ssh asset present", bool(ssh))
    if not ssh:
        print(f"PASS={PASS} FAIL={FAIL}")
        return 1
    updated = api(f"/api/assets/{ssh['id']}", {"config": {"x11": True, "x11Display": ":1"}}, method="PUT")
    check("x11 enabled on asset", bool(updated.get("data", {}).get("asset", {}).get("config", {}).get("x11")))

    return asyncio.run(ui_part(ssh["id"]))


async def ui_part(asset_id: str) -> int:
    proc, ws = await launch(BASE)
    cdp = CDP(ws)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    session_id = None
    try:
        await asyncio.sleep(1.0)
        for _ in range(40):
            if await js(cdp, "document.querySelectorAll('.asset-panel .tree-row').length") > 0:
                break
            await asyncio.sleep(0.25)
        for text, wait_s in (("生产环境", 0.6), ("SSH hexlab", 4)):
            await js(
                cdp,
                "(()=>{const t=%s;const els=[...document.querySelectorAll('.asset-panel .tree-row')];"
                "const el=els.find(e=>(e.innerText||'').includes(t));"
                "if(el)el.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));return !!el})()" % json.dumps(text),
            )
            await asyncio.sleep(wait_s)
        await type_text(cdp, "echo DISPLAY=$DISPLAY; xdpyinfo | head -4")
        await wait_for(lambda: _has_dpy(cdp), timeout=20)
        text = await term_text(cdp)
        display_ok = "DISPLAY=localhost:" in text
        dpy_ok = "name of display:    localhost:" in text and "vendor release number" in text
        check("DISPLAY is forwarded", display_ok, text[-300:].replace("\n", " | "))
        check("xdpyinfo reached X server", dpy_ok, text[-300:].replace("\n", " | "))

        # Session metadata reports the forwarded local display.
        sessions = api("/api/sessions", method="GET").get("data", {}).get("sessions", [])
        ssh_sessions = [s for s in sessions if s.get("kind") == "ssh"]
        check("ssh session exists", bool(ssh_sessions))
        if ssh_sessions:
            meta = ssh_sessions[-1].get("meta") or {}
            check("session meta x11", bool(meta.get("x11")), str(meta)[:200])
    finally:
        if session_id:
            try:
                api(f"/api/sessions/{session_id}/close")
            except Exception:
                pass
        await cdp.close()
        proc.kill()

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES: " + ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


async def _has_dpy(cdp) -> bool:
    text = await term_text(cdp)
    return "vendor release number" in text


if __name__ == "__main__":
    sys.exit(main())
