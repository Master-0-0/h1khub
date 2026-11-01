"""Capture the documentation screenshot set from a running H1KHub replica."""
import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import aiohttp
from cdp_check import CDP, launch  # noqa: E402

OUT = os.environ.get("SHOT_DIR", os.path.join(os.path.dirname(__file__), "..", "docs", "screenshots"))
BASE = os.environ.get("H1KHUB_BASE", "http://127.0.0.1:38110/")


async def wait(ms=600):
    await asyncio.sleep(ms / 1000)


async def js(cdp, expr):
    return await cdp.evaluate(expr)


async def click_tree(cdp, text, event="click"):
    return await js(
        cdp,
        f"""(() => {{
          const el = [...document.querySelectorAll('.tree-row')].find(e => (e.innerText || '').trim().includes({json.dumps(text)}));
          if (!el) return 'not-found';
          el.dispatchEvent(new MouseEvent({json.dumps(event)}, {{bubbles: true, cancelable: true}}));
          return 'ok';
        }})()""",
    )


async def click_text(cdp, selector, text):
    return await js(
        cdp,
        f"""(() => {{
          const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => (e.innerText || '').includes({json.dumps(text)}));
          if (!el) return 'not-found';
          el.click();
          return 'ok';
        }})()""",
    )


async def click_title(cdp, title):
    return await js(
        cdp,
        f"""(() => {{
          const el = document.querySelector(`[title=${{JSON.stringify({json.dumps(title)})}}]`);
          if (!el) return 'not-found';
          el.click(); return 'ok';
        }})()""",
    )


async def type_terminal(cdp, text):
    await js(cdp, "document.querySelector('.xterm-helper-textarea')?.focus()")
    await cdp.send("Input.insertText", {"text": text})
    await wait(250)
    await cdp.send(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
    )
    await cdp.send(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
    )


async def send_session(text):
    """Send input to the newest SSH session through the local API (stable screenshots)."""
    home = os.environ.get("H1KHUB_HOME", os.path.expanduser("~/.h1khub"))
    token = json.load(open(os.path.join(home, "runtime.json")))["token"]
    headers = {"X-H1KHub-Token": token}
    async with aiohttp.ClientSession() as sess:
        async with sess.get(f"{BASE}api/sessions", headers=headers) as resp:
            data = await resp.json()
        sessions = [x for x in data["data"]["sessions"] if x["kind"] == "ssh" and not x["closed"]]
        if not sessions:
            return "no-session"
        sid = sessions[-1]["id"]
        payload = {"encoding": "base64", "data": base64.b64encode(text.encode()).decode()}
        async with sess.post(f"{BASE}api/sessions/{sid}/input", headers=headers, json=payload) as resp:
            await resp.read()
    return "ok"


async def shot(cdp, name):
    path = os.path.join(OUT, name)
    await cdp.screenshot(path)
    print("shot", path)


async def main():
    os.makedirs(OUT, exist_ok=True)
    proc, ws = await launch(BASE)
    cdp = CDP(ws)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    await cdp.send("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    await wait(4500)  # the boot splash is removed only once Vue has mounted
    await shot(cdp, "01-home.png")

    # expand the demo folder so seeded assets are visible
    print("expand folder:", await click_tree(cdp, "生产环境", "dblclick"))
    await wait(500)
    print("open ssh:", await click_tree(cdp, "SSH hexlab", "dblclick"))
    await wait(3800)
    print("send command:", await send_session('echo "H1KHub replica · $(date +%F)" && uname -sr && df -h | head -3\n'))
    await wait(1600)
    await shot(cdp, "02-ssh-sftp.png")

    print("open mysql:", await click_tree(cdp, "MySQL demo", "dblclick"))
    await wait(2200)
    print("open users:", await click_tree(cdp, "users"))
    await wait(1800)
    await shot(cdp, "03-mysql-table.png")

    print("open saved query:", await js(cdp, """(() => {
      const el = [...document.querySelectorAll('.workspace .asset-panel .tree-row')].find(e => (e.innerText || '').includes('用户列表查询'));
      if (!el) return 'not-found';
      el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
      return 'ok';
    })()"""))
    await wait(1600)
    print("run query:", await click_text(cdp, ".workspace .toolbar .btn", "执行"))
    await wait(2000)
    await shot(cdp, "04-sql-editor.png")

    print("open redis:", await click_tree(cdp, "Redis local", "dblclick"))
    await wait(2200)
    await js(cdp, "[...document.querySelectorAll('.key-row')].find(e=>e.innerText.includes('user:1'))?.click()")
    await wait(1200)
    await shot(cdp, "05-redis.png")

    print("open docker:", await click_tree(cdp, "Docker local", "dblclick"))
    await wait(2800)
    await shot(cdp, "06-docker.png")

    await click_title(cdp, "批量执行")
    await wait(700)
    await click_text(cdp, ".modal .btn", "开始执行")
    await wait(3500)
    await js(cdp, "document.querySelectorAll('.modal .row')[3]?.click()")
    await wait(300)
    await shot(cdp, "07-batch-exec.png")
    await js(cdp, "document.querySelectorAll('.modal-foot .btn')[0]?.click()")

    await js(cdp, "document.querySelectorAll('.titlebar .icon-btn')[2]?.click()")
    await wait(700)
    await shot(cdp, "08-settings.png")
    await click_text(cdp, ".settings-nav button", "AI 与模型")
    await wait(500)
    await shot(cdp, "09-settings-ai.png")
    await js(cdp, "document.querySelector('.settings-layout .modal-head .icon-btn')?.click()")
    await wait(400)

    # light theme variant of the table view
    await js(cdp, "document.querySelector('[title=\"切换到亮色主题\"]')?.click()")
    await wait(1200)
    await click_text(cdp, ".tab", "MySQL demo")
    await wait(1200)
    await shot(cdp, "10-light-theme.png")
    await js(cdp, "document.querySelector('[title=\"切换到暗色主题\"]')?.click()")
    await wait(900)

    await click_title(cdp, "AI 助手")
    await wait(1200)
    await shot(cdp, "11-ai-empty.png")

    errors = []
    for ev in cdp.events:
        if ev.get("method") == "Runtime.exceptionThrown":
            errors.append(ev["params"]["exceptionDetails"].get("text"))
    print("errors:", json.dumps(errors, ensure_ascii=False))
    await cdp.close()
    proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
