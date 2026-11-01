"""Tiny CDP client for UI verification (headless Chromium)."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import websockets

CHROME = "/usr/bin/chromium"
PORT = int(os.environ.get("CDP_PORT", "9333"))


class CDP:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self.next_id = 0
        self.responses = {}
        self.events = []

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=64 * 1024 * 1024)
        self.task = asyncio.create_task(self._reader())

    async def _reader(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg:
                fut = self.responses.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg)
            else:
                self.events.append(msg)
                if len(self.events) > 1000:
                    self.events = self.events[-500:]

    async def send(self, method, params=None, timeout=20):
        self.next_id += 1
        mid = self.next_id
        fut = asyncio.get_event_loop().create_future()
        self.responses[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        result = await asyncio.wait_for(fut, timeout=timeout)
        if "error" in result:
            raise RuntimeError(f"{method}: {result['error']}")
        return result.get("result", {})

    async def evaluate(self, expression, await_promise=False):
        res = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
        )
        if "exceptionDetails" in res.get("result", {}):
            raise RuntimeError(res["result"]["exceptionDetails"])
        return res.get("result", {}).get("value")

    async def screenshot(self, path):
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        import base64

        with open(path, "wb") as fh:
            fh.write(base64.b64decode(res["data"]))
        return path

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass


async def launch(url):
    global PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        PORT = probe.getsockname()[1]
    profile = f"/tmp/chrome-h1khub-{int(time.time())}"
    proc = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--no-sandbox",
            # Software WebGL so xterm's WebGL renderer can be exercised headlessly.
            "--enable-unsafe-swiftshader",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--window-size=1440,900",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={PORT}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        try:
            data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1))
            pages = [p for p in data if p.get("type") == "page" and p.get("webSocketDebuggerUrl")]
            if pages:
                return proc, pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        await asyncio.sleep(0.25)
    proc.kill()
    raise RuntimeError("chromium did not start")


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:38110/"
    proc, ws = await launch(url)
    cdp = CDP(ws)
    await cdp.connect()
    errors = []
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    await asyncio.sleep(2.5)
    for ev in cdp.events:
        if ev.get("method") == "Runtime.exceptionThrown":
            errors.append(ev["params"]["exceptionDetails"].get("text"))
        if ev.get("method") == "Runtime.consoleAPICalled" and ev["params"]["type"] == "error":
            errors.append("console: " + str(ev["params"]["args"]))
        if ev.get("method") == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
            errors.append("log: " + ev["params"]["entry"]["text"])
    print("TITLE:", await cdp.evaluate("document.title"))
    print("PANEL WIDTH:", await cdp.evaluate("document.querySelector('.asset-panel')?.getBoundingClientRect().width"))
    print("BODY TEXT:", (await cdp.evaluate("document.body.innerText"))[:300].replace("\n", " | "))
    print("ERRORS:", json.dumps(errors, ensure_ascii=False)[:2000])
    await cdp.screenshot("/tmp/ui_home.png")
    await cdp.close()
    proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
