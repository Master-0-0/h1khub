#!/usr/bin/env python3
"""End-to-end test for ZMODEM (sz / rz) inside the SSH terminal.

Download: the test types `sz <file>` in the terminal and checks that the browser
          saved the offered file with the right bytes.
Upload:   the test types `rz -y`, sets the file on the ZMODEM upload input via
          CDP, sends it, and reads the written remote file through the SFTP API.

Usage: python3 scripts/test_zmodem.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from cdp_check import CDP, launch  # noqa: E402

BASE = os.environ.get("H1KHUB_BASE", "http://127.0.0.1:38110")
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []

REMOTE_DL = "/tmp/h1khub-zm-dl.txt"
REMOTE_UP = "/tmp/h1khub-zm-up.txt"


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


async def click_by_text(cdp, selector, text, event="click"):
    return await js(
        cdp,
        f"""(() => {{
          const els = [...document.querySelectorAll({json.dumps(selector)})];
          const el = els.find(e => (e.innerText || '').trim().includes({json.dumps(text)}));
          if (!el) return 'not-found';
          el.dispatchEvent(new MouseEvent({json.dumps(event)}, {{bubbles: true, cancelable: true}}));
          return 'ok';
        }})()""",
    )


async def type_text(cdp, text, enter=True):
    await js(cdp, "document.querySelector('.xterm-helper-textarea')?.focus()")
    await cdp.send("Input.insertText", {"text": text})
    await asyncio.sleep(0.3)
    if enter:
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


async def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    download_dir = tempfile.mkdtemp(prefix="h1khub-zm-dl-")
    local_upload = os.path.join(tempfile.mkdtemp(prefix="h1khub-zm-up-"), "h1khub-zm-up.txt")
    token_dl = f"zmodem-download-{random.randint(100000, 999999)}"
    token_up = f"zmodem-upload-{random.randint(100000, 999999)}"
    with open(local_upload, "w", encoding="utf-8") as fh:
        fh.write(token_up)

    proc, ws = await launch(BASE)
    cdp = CDP(ws)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("DOM.enable")
    await cdp.send("Log.enable")
    try:
        await cdp.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": download_dir})
    except Exception as exc:
        print("  WARN download behavior:", exc)
    await asyncio.sleep(1.0)
    for _ in range(40):
        if await js(cdp, "document.querySelectorAll('.asset-panel .tree-row').length") > 0:
            break
        await asyncio.sleep(0.25)

    session_id = None
    try:
        print("expand folder:", await click_by_text(cdp, ".asset-panel .tree-row", "生产环境", "dblclick"))
        await asyncio.sleep(0.5)
        print("open ssh:", await click_by_text(cdp, ".asset-panel .tree-row", "SSH hexlab", "dblclick"))

        async def connected():
            return await js(cdp, "document.querySelector('.term-status')?.innerText") == "已连接"

        await wait_for(connected, timeout=15)
        check("ssh terminal connected", await js(cdp, "document.querySelector('.term-status')?.innerText") == "已连接")
        check("zmodem sentry installed", await js(cdp, "typeof window.Zmodem === 'object'"))

        # ---------------- download (remote `sz`) ----------------
        print("\n[zmodem] download via sz")
        await type_text(cdp, f"printf '%s' '{token_dl}' > {REMOTE_DL} && sz {REMOTE_DL}")
        target = os.path.join(download_dir, "h1khub-zm-dl.txt")

        async def downloaded():
            if not os.path.exists(target):
                return False
            try:
                with open(target, encoding="utf-8", errors="replace") as fh:
                    return fh.read() == token_dl
            except Exception:
                return False

        ok = await wait_for(downloaded, timeout=25)
        check("sz download saved", ok, f"dir={os.listdir(download_dir)}")
        last = await js(cdp, "document.querySelector('.terminal-host')?.dataset.lastDownload")
        check("download recorded on host", last == "h1khub-zm-dl.txt", str(last))

        # ---------------- upload (remote `rz`) ----------------
        print("\n[zmodem] upload via rz")
        # Let the previous ZMODEM session fully tear down before starting rz;
        # under full-suite load the sentry otherwise occasionally misses it.
        await asyncio.sleep(0.8)
        await type_text(cdp, f"cd /tmp && rm -f h1khub-zm-up.txt && rz -y")
        async def prompt_shown():
            return await js(cdp, "!!document.querySelector('.modal input[type=file]')")

        appeared = await wait_for(prompt_shown, timeout=40)
        check("rz upload prompt shown", appeared)
        if appeared:
            doc = await cdp.send("DOM.getDocument", {"depth": -1})
            node = await cdp.send(
                "DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": ".modal input[type=file]"}
            )
            await cdp.send("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [local_upload]})
            await asyncio.sleep(0.4)
            print("send click:", await click_by_text(cdp, ".modal-foot .btn", "发送"))
            await asyncio.sleep(0.5)

            assets = api("/api/assets", method="GET")["data"]["assets"]
            ssh_id = next(a["id"] for a in assets if a["name"] == "SSH hexlab")
            session = api("/api/sessions/start", {"assetId": ssh_id, "title": "zmodem-test"})["data"]["session"]
            session_id = session["id"]

            async def uploaded():
                try:
                    res = api("/api/sftp/read", {"sessionId": session_id, "path": REMOTE_UP})
                    return (res.get("data") or {}).get("content") == token_up
                except urllib.error.HTTPError:
                    return False

            ok_up = await wait_for(uploaded, timeout=30)
            check("rz upload wrote remote file", ok_up)
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


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
