#!/usr/bin/env python3
"""UI verification for the archive (compress/extract) feature over CDP.

Opens the SSH asset, expands the SFTP pane, multi-selects two files, creates a
tar.gz through the dialog, then extracts it through the extract dialog and
checks the produced folder.  Saves screenshots of both dialogs.

Usage: python3 scripts/test_archive_ui.py
"""
import asyncio
import json
import os
import random
import shlex
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cdp_check import CDP, launch  # noqa: E402

BASE = os.environ.get("H1KHUB_BASE", "http://127.0.0.1:38110/")
API = "http://127.0.0.1:38110"
TOKEN = ""
SSH_ASSET = "a_d635f8af4797"
REMOTE_ROOT = ""
PASS = 0
FAIL = 0
FAILURES = []
SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "screenshots")


def api(path, body=None, method="POST", timeout=120):
    import urllib.error
    import urllib.request

    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        API + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"__http_error": exc.code, "detail": exc.read().decode(errors="replace")}


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


async def js(cdp, expression, await_promise=False):
    return await cdp.evaluate(expression, await_promise=await_promise)


async def wait(ms=300):
    await asyncio.sleep(ms / 1000)


async def wait_until(cdp, expression, timeout=12, interval=0.3):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        value = await js(cdp, expression)
        if value:
            return value
        await wait(interval * 1000)
    return None


async def click_text(cdp, selector, text, event="click"):
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


ROW_FIND = """
  const rows = [...document.querySelectorAll('.file-pane .file-row')];
  const row = rows.find(r => ((r.querySelector('.file-name')?.innerText) || '').trim() === NAME);
"""


async def row_click(cdp, name, ctrl=False):
    return await js(
        cdp,
        f"""(() => {{
          const NAME = {json.dumps(name)};
          {ROW_FIND}
          if (!row) return 'not-found';
          row.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true, ctrlKey: {str(ctrl).lower()}}}));
          return 'ok';
        }})()""",
    )


async def row_dblclick(cdp, name):
    return await js(
        cdp,
        f"""(() => {{
          const NAME = {json.dumps(name)};
          {ROW_FIND}
          if (!row) return 'not-found';
          row.dispatchEvent(new MouseEvent('dblclick', {{bubbles: true, cancelable: true}}));
          return 'ok';
        }})()""",
    )


async def row_exists(cdp, name):
    return await js(
        cdp,
        f"""(() => {{
          const NAME = {json.dumps(name)};
          {ROW_FIND}
          return !!row;
        }})()""",
    )


async def set_path(cdp, path):
    return await js(
        cdp,
        f"""(() => {{
          const input = document.querySelector('.file-pane .file-path input');
          if (!input) return 'not-found';
          input.value = {json.dumps(path)};
          input.dispatchEvent(new Event('input', {{bubbles: true}}));
          input.dispatchEvent(new KeyboardEvent('keyup', {{key: 'Enter', bubbles: true}}));
          return 'ok';
        }})()""",
    )


async def fill_archive_name(cdp, name):
    return await js(
        cdp,
        f"""(() => {{
          const inputs = [...document.querySelectorAll('.modal .modal-body input.input')];
          if (!inputs.length) return 'not-found';
          inputs[0].value = {json.dumps(name)};
          inputs[0].dispatchEvent(new Event('input', {{bubbles: true}}));
          return 'ok';
        }})()""",
    )


async def click_primary(cdp):
    return await js(
        cdp,
        """(() => {
          const btn = document.querySelector('.modal .modal-foot .btn.primary');
          if (!btn) return 'not-found';
          if (btn.disabled) return 'disabled';
          btn.click();
          return 'ok';
        })()""",
    )


def prepare_remote():
    token = json.load(open(os.path.expanduser("~/.h1khub/runtime.json")))["token"]
    global TOKEN, REMOTE_ROOT, SSH_ASSET
    TOKEN = token
    suffix = "%04x" % random.randint(0, 0xFFFF)
    REMOTE_ROOT = f"/tmp/h1khub-ui-archive-{suffix}"
    root = REMOTE_ROOT
    proj = f"{root}/proj"
    command = "\n".join(
        [
            f"rm -rf {shlex.quote(root)}",
            f"mkdir -p {shlex.quote(proj + '/sub dir')}",
            f"printf 'ui-one' > {shlex.quote(proj + '/a.txt')}",
            f"printf 'ui-two' > {shlex.quote(proj + '/b.txt')}",
            f"printf 'ui-three' > {shlex.quote(proj + '/sub dir/c.txt')}",
            f"printf 'ui-four' > {shlex.quote(proj + '/z.txt')}",
        ]
    )
    assets = api("/api/assets", method="GET")["data"]["assets"]
    for asset in assets:
        if asset.get("name") == "SSH hexlab":
            SSH_ASSET = asset["id"]
            break
    resp = api("/api/batch/exec", {"assetIds": [SSH_ASSET], "command": command})
    result = (resp.get("data") or {}).get("results", [{}])[0]
    if not result.get("ok"):
        raise RuntimeError(f"fixture setup failed: {resp}")


def cleanup_remote():
    if not REMOTE_ROOT:
        return
    try:
        api(
            "/api/batch/exec",
            {"assetIds": [SSH_ASSET], "command": f"rm -rf {shlex.quote(REMOTE_ROOT)}"},
        )
    except Exception:
        pass


def collect_errors(cdp):
    errors = []
    for event in cdp.events:
        if event.get("method") == "Runtime.exceptionThrown":
            details = event["params"]["exceptionDetails"]
            errors.append(details.get("exception", {}).get("description") or details.get("text"))
        elif event.get("method") == "Runtime.consoleAPICalled" and event["params"]["type"] == "error":
            errors.append(
                "console: "
                + " ".join(str(arg.get("value") or arg.get("description")) for arg in event["params"]["args"])
            )
        elif event.get("method") == "Log.entryAdded" and event["params"]["entry"]["level"] == "error":
            errors.append("log: " + event["params"]["entry"]["text"])
    return errors


async def main():
    prepare_remote()
    before = set()
    try:
        for session in (api("/api/sessions", method="GET").get("data") or {}).get("sessions", []):
            before.add(session.get("id"))
    except Exception:
        pass
    proc = ws = cdp = None
    os.makedirs(SHOTS, exist_ok=True)
    try:
        proc, ws = await launch(BASE)
        cdp = CDP(ws)
        await cdp.connect()
        await cdp.send("Runtime.enable")
        await cdp.send("Page.enable")
        await cdp.send("Log.enable")
        await wait(1200)
        check(
            "ui: asset tree loaded",
            await wait_until(cdp, "document.querySelectorAll('.asset-panel .tree-row').length > 0", 15),
        )
        check(
            "ui: expand folder",
            (await click_text(cdp, ".asset-panel .tree-row", "生产环境", "dblclick")) == "ok",
        )
        await wait(500)
        check(
            "ui: open SSH asset",
            (await click_text(cdp, ".asset-panel .tree-row", "SSH hexlab", "dblclick")) == "ok",
        )
        check(
            "ui: SFTP pane visible",
            await wait_until(cdp, "document.querySelectorAll('.file-pane .file-row').length > 0", 25),
        )
        check("ui: set remote path", (await set_path(cdp, f"{REMOTE_ROOT}/proj")) == "ok")
        check(
            "ui: fixture rows loaded",
            await wait_until(cdp, "document.querySelectorAll('.file-pane .file-row').length >= 4", 12),
            await js(cdp, "document.querySelector('.file-pane')?.innerText"),
        )

        check("ui: select first row", (await row_click(cdp, "a.txt")) == "ok")
        check("ui: ctrl-select second row", (await row_click(cdp, "b.txt", ctrl=True)) == "ok")
        check(
            "ui: two rows selected",
            await wait_until(cdp, "document.querySelectorAll('.file-pane .file-row.selected').length === 2", 5),
        )
        check(
            "ui: selection bar rendered",
            await wait_until(cdp, "!!document.querySelector('.file-selbar')", 5),
            await js(cdp, "document.querySelector('.file-pane')?.innerText"),
        )

        check(
            "ui: open compress dialog",
            (await click_text(cdp, ".file-selbar .btn", "压缩")) == "ok",
        )
        check(
            "ui: compress dialog visible",
            await wait_until(cdp, "document.querySelector('.modal .modal-title')?.innerText.includes('压缩选中项')", 8),
        )
        formats = await wait_until(
            cdp,
            """(() => {
              const select = document.querySelector('.modal .modal-body select');
              if (!select) return null;
              const opts = [...select.options].map(o => ({ value: o.value, disabled: o.disabled }));
              return opts.some(o => o.value === '7z' && !o.disabled) ? opts : null;
            })()""",
            12,
        )
        check("ui: format choices", len(formats or []) >= 5, str(formats))
        check("ui: 7z enabled", any(item["value"] == "7z" and not item["disabled"] for item in (formats or [])), str(formats))
        check("ui: fill archive name", (await fill_archive_name(cdp, "ui-bundle")) == "ok")
        await wait(250)
        await cdp.screenshot(os.path.join(SHOTS, "38-archive-compress.png"))
        check("ui: start compression", (await click_primary(cdp)) == "ok")
        check(
            "ui: archive created",
            await wait_until(cdp, "!document.querySelector('.modal')", 25)
            and await wait_until(cdp, "!!document.querySelector('.file-pane')", 2)
            and await wait_until(cdp, f"""(() => {{
              {ROW_FIND.replace('NAME', json.dumps('ui-bundle.tar.gz'))}
              return !!row;
            }})()""", 20),
            await js(cdp, "document.querySelector('.file-pane')?.innerText"),
        )

        check("ui: select archive", (await row_click(cdp, "ui-bundle.tar.gz")) == "ok")
        check(
            "ui: extract button offered",
            await wait_until(cdp, "!!document.querySelector('.file-selbar')", 4),
        )
        check(
            "ui: open extract dialog",
            (await click_text(cdp, ".file-selbar .btn", "解压")) == "ok",
        )
        check(
            "ui: archive info loaded",
            await wait_until(cdp, "!!document.querySelector('.modal .archive-info')", 12),
            await js(cdp, "document.querySelector('.modal .modal-body')?.innerText"),
        )
        info = await js(cdp, "document.querySelector('.modal .archive-info')?.innerText")
        check("ui: archive info badges", bool(info) and "tar.gz" in info, str(info))
        await wait(250)
        await cdp.screenshot(os.path.join(SHOTS, "39-archive-extract.png"))
        check("ui: start extraction", (await click_primary(cdp)) == "ok")
        check(
            "ui: extracted folder listed",
            await wait_until(cdp, "!document.querySelector('.modal')", 30)
            and await wait_until(cdp, f"""(() => {{
              {ROW_FIND.replace('NAME', json.dumps('ui-bundle'))}
              return !!row;
            }})()""", 20),
            await js(cdp, "document.querySelector('.file-pane')?.innerText"),
        )
        check("ui: enter extracted folder", (await row_dblclick(cdp, "ui-bundle")) == "ok")
        check(
            "ui: extracted content listed",
            await wait_until(cdp, f"""(() => {{
              {ROW_FIND.replace('NAME', json.dumps('a.txt'))}
              return !!row;
            }})()""", 10),
        )
        await wait(400)
        errors = collect_errors(cdp)
        check("ui: no console errors", not errors, json.dumps(errors, ensure_ascii=False)[:1200])
    finally:
        if cdp:
            await cdp.close()
        if proc:
            proc.kill()
        # The UI opens a real SSH session; close only the sessions this run
        # created, leaving any session the user already had untouched.
        try:
            for session in (api("/api/sessions", method="GET").get("data") or {}).get("sessions", []):
                if session.get("id") not in before and session.get("assetId") == SSH_ASSET:
                    api(f"/api/sessions/{session['id']}/close")
        except Exception:
            pass
        cleanup_remote()

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
