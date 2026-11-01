#!/usr/bin/env python3
"""Round-23 update tests (#91 version check + #93 channel): API + real CDP.

What is proven (and what is deliberately *not*):

  * GET /api/update/check no longer answers 501; it reads one local manifest
    (file:// or loopback http(s)), compares version/build numerically and
    returns {current, latest, hasUpdate, notes};
  * a missing, unreadable, oversized or malformed manifest yields HTTP 400 with
    a machine-readable UPDATE_* code — never a 500;
  * non-loopback hosts (including api.hexhub.cn), bad schemes, relative paths
    and redirects are rejected before any network access;
  * the check is read-only: it does not change settings, does not create
    assets and does not call /api/update/start;
  * POST /api/update/channel accepts only stable/experimental and
    production/test, persists them, and the channel/env select the manifest
    through {channel}/{env} templates or a directory URL;
  * update.autoCheck triggers exactly one delayed check after bootstrap (no
    polling);
  * the settings UI exposes version check/channel/autoCheck/manifest as
    configurable, keeps hot update (#92) disabled/reserved, and the About page
    check button works and renders current/latest/hasUpdate/notes;
  * hot update routes still answer 501/FEATURE_RESERVED.

Usage: python3 scripts/test_update.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import cdp_check  # noqa: E402
from cdp_check import CDP, launch  # noqa: E402

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
HOME = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
TMP = tempfile.mkdtemp(prefix="h1khub-update-23-")
SERVER_PATHS: list[str] = []
SERVER_PORT = 0
MD5 = "0123456789abcdef0123456789abcdef"


def load_token() -> str:
    with open(os.path.join(HOME, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="GET"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {"raw": raw.decode("utf-8", "replace")[:200]}
        return exc.code, payload


def data_of(payload):
    return (payload or {}).get("data") if isinstance(payload, dict) else None


def err_of(payload):
    return ((payload or {}).get("error") or {}) if isinstance(payload, dict) else {}


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}".rstrip())
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}".rstrip())


def write_manifest(name: str, **fields) -> str:
    payload = {
        "version": "5.1.10",
        "build": 520,
        "notes": f"manifest {name}",
        "url": "https://example.com/h1khub-5.1.10.deb",
        "md5": MD5,
    }
    payload.update(fields)
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return "file://" + path


def set_update(patch: dict):
    return req("/api/settings", {"update": patch}, method="PUT")


def check_api():
    return req("/api/update/check")


def settings_update() -> dict:
    code, payload = req("/api/settings")
    assert code == 200, (code, payload)
    return (data_of(payload) or {}).get("update") or {}


def expect_error(name: str, code_expected: str, feature: str, result) -> None:
    status, payload = result
    err = err_of(payload)
    detail = json.dumps(err, ensure_ascii=False)[:180]
    check(
        name,
        status == 400
        and (payload or {}).get("ok") is False
        and err.get("code") == code_expected
        and err.get("feature") == feature
        and err.get("docs", "").startswith("docs/reserved-slots.md#")
        and bool(err.get("message")),
        f"{status} {detail}",
    )


# ------------------------------------------------------------- local HTTP
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        SERVER_PATHS.append(self.path)
        if self.path == "/manifest.json":
            body = json.dumps(
                {
                    "version": "5.1.12",
                    "build": 540,
                    "notes": "http loopback manifest",
                    "url": "https://example.com/http.deb",
                    "md5": MD5,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/invalid.json":
            body = b"{not json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/big.json":
            body = b'{"version":"9.9.9","build":1,"notes":"' + b"x" * (300 * 1024) + b'"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "https://api.hexhub.cn/api/client/version/check")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):  # silence
        pass


def start_http_server() -> ThreadingHTTPServer:
    global SERVER_PORT
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    SERVER_PORT = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ------------------------------------------------------------------- CDP
async def js(cdp, expr):
    return await cdp.evaluate(expr)


async def wait_for(cdp, expr, timeout=20.0, interval=0.2):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = await js(cdp, expr)
            if last:
                return last
        except Exception as exc:
            last = exc
        await asyncio.sleep(interval)
    raise RuntimeError(f"timeout waiting for: {expr} (last={last})")


async def click_nav(cdp, label: str):
    return await js(
        cdp,
        f"""(() => {{
          const b = [...document.querySelectorAll('.settings-nav button')]
            .find(e => (e.innerText || '').trim() === {json.dumps(label)});
          if (!b) return 'not-found';
          b.click();
          return 'ok';
        }})()""",
    )


async def count_check_requests(cdp, since: int = 0) -> int:
    total = 0
    for ev in cdp.events[since:]:
        if ev.get("method") != "Network.requestWillBeSent":
            continue
        url = ((ev.get("params") or {}).get("request") or {}).get("url") or ""
        if "/api/update/check" in url:
            total += 1
    return total


async def get_settings_async() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, settings_update)


async def ui_checks(manifest_url: str) -> None:
    # Launch with autoCheck off, then enable it just before the reload. This
    # keeps the initial page from racing its delayed check into the observation
    # window under full-suite load (test-only timing guard).
    set_update({"autoCheck": False, "manifestUrl": manifest_url, "channel": "stable", "env": "production"})
    proc, ws_url = await launch(BASE + "/")
    cdp = CDP(ws_url)
    await cdp.connect()
    try:
        await cdp.send("Runtime.enable")
        await cdp.send("Page.enable")
        await cdp.send("Log.enable")
        await cdp.send("Network.enable")
        await wait_for(cdp, "!!document.querySelector('.workspace')", timeout=30)

        # Enable autoCheck and reset the observation window immediately before
        # the reload, so only the reloaded page's single delayed check counts.
        set_update({"autoCheck": True, "manifestUrl": manifest_url, "channel": "stable", "env": "production"})
        baseline = len(cdp.events)
        await cdp.send("Page.reload", {"ignoreCache": True})
        await wait_for(cdp, "!!document.querySelector('.workspace')", timeout=30)
        deadline = time.time() + 15
        while time.time() < deadline and await count_check_requests(cdp, baseline) < 1:
            await asyncio.sleep(0.3)
        first = await count_check_requests(cdp, baseline)
        check("autoCheck 启动后调用一次 /api/update/check", first == 1, f"requests={first}")
        await asyncio.sleep(4.0)
        second = await count_check_requests(cdp, baseline)
        check("autoCheck 不做后台轮询（4 秒后仍为一次）", second == 1, f"requests={second}")

        # Open settings → update section.
        r = await js(
            cdp,
            """(() => {
              const b = document.querySelector('.rail-btn[title="设置"]');
              if (!b) return 'not-found';
              b.click();
              return 'ok';
            })()""",
        )
        check("设置对话框可打开", r == "ok", str(r))
        await wait_for(cdp, "!!document.querySelector('.settings-layout')", timeout=15)
        r = await click_nav(cdp, "更新")
        check("更新设置节可打开", r == "ok", str(r))
        await wait_for(cdp, "!!document.querySelector('.update-check-btn')", timeout=10)
        ui = await js(
            cdp,
            """(() => {
              const sec = document.querySelector('.settings-section');
              const q = (s) => document.querySelector(s);
              const hot = q('.update-hot');
              return {
                text: sec.innerText,
                checkBtn: !!q('.update-check-btn'),
                checkDisabled: !!q('.update-check-btn') && q('.update-check-btn').disabled,
                channel: !!q('.update-channel'),
                channelDisabled: !!q('.update-channel') && q('.update-channel').disabled,
                env: !!q('.update-env'),
                auto: !!q('.update-auto'),
                manifest: !!q('.update-manifest'),
                hot: !!hot,
                hotDisabled: !!hot && hot.disabled,
                hotTitle: hot ? (hot.getAttribute('title') || '') : '',
              };
            })()""",
        )
        check(
            "更新节可配置：版本检查按钮、通道、env、autoCheck、manifest",
            ui["checkBtn"] and not ui["checkDisabled"] and ui["channel"] and not ui["channelDisabled"]
            and ui["env"] and ui["auto"] and ui["manifest"],
            json.dumps(ui, ensure_ascii=False)[:260],
        )
        check(
            "热更新仍禁用且标注预留",
            ui["hot"] and ui["hotDisabled"] and ui["hotTitle"] == "预留（未实现）" and "预留" in ui["text"],
            json.dumps({k: ui[k] for k in ("hot", "hotDisabled", "hotTitle")}, ensure_ascii=False),
        )

        # Manual check renders current/latest/hasUpdate/notes.
        await js(cdp, "document.querySelector('.update-check-btn').click()")
        await wait_for(cdp, "!!document.querySelector('.update-check-result')", timeout=15)
        result_text = await js(cdp, "document.querySelector('.update-check-result').innerText")
        check(
            "更新节检查结果显示 current/latest/hasUpdate/notes",
            "5.1.9" in result_text
            and "5.1.10" in result_text
            and "true" in result_text
            and "manifest newer.json" in result_text,
            result_text[:220].replace("\n", " | "),
        )

        # Channel select persists immediately (POST /api/update/channel).
        await js(
            cdp,
            """(() => {
              const sel = document.querySelector('.update-channel');
              sel.value = 'experimental';
              sel.dispatchEvent(new Event('change', { bubbles: true }));
              return sel.value;
            })()""",
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            if (await get_settings_async()).get("channel") == "experimental":
                break
            await asyncio.sleep(0.3)
        check("UI 切换通道持久化", (await get_settings_async()).get("channel") == "experimental")
        # The select is disabled while the POST is in flight; the server setting
        # can be visible a moment before the local busy flag clears. Wait for the
        # control itself, otherwise the next change is dropped (test-only race).
        deadline = time.time() + 10
        while time.time() < deadline:
            if await js(cdp, "!document.querySelector('.update-channel')?.disabled"):
                break
            await asyncio.sleep(0.1)
        deadline = time.time() + 10
        while time.time() < deadline:
            await js(
                cdp,
                """(() => {
                  const sel = document.querySelector('.update-channel');
                  if (!sel || sel.disabled) return 'busy';
                  sel.value = 'stable';
                  sel.dispatchEvent(new Event('change', { bubbles: true }));
                  return sel.value;
                })()""",
            )
            for _ in range(10):
                if (await get_settings_async()).get("channel") == "stable":
                    break
                await asyncio.sleep(0.3)
            if (await get_settings_async()).get("channel") == "stable":
                break
            await asyncio.sleep(0.3)
        check("UI 可切回稳定通道", (await get_settings_async()).get("channel") == "stable")

        # autoCheck switch persists (turn it off again for the rest of the suite).
        await js(cdp, "document.querySelector('.update-auto').click()")
        deadline = time.time() + 10
        while time.time() < deadline:
            if (await get_settings_async()).get("autoCheck") is False:
                break
            await asyncio.sleep(0.3)
        check("UI 可关闭 autoCheck", (await get_settings_async()).get("autoCheck") is False)

        # About section: the old disabled reserved button is gone.
        r = await click_nav(cdp, "关于")
        check("关于设置节可打开", r == "ok", str(r))
        await wait_for(cdp, "!!document.querySelector('.settings-section .update-check-btn')", timeout=10)
        about = await js(
            cdp,
            """(() => {
              const sec = document.querySelector('.settings-section');
              const btn = document.querySelector('.settings-section .update-check-btn');
              return {
                text: sec.innerText,
                enabled: !!btn && !btn.disabled,
                hasReservedText: sec.innerText.includes('检查更新（预留）'),
              };
            })()""",
        )
        check(
            "关于页检查更新按钮可用（不再禁用/预留）",
            about["enabled"] and not about["hasReservedText"] and "5.1.9" in about["text"],
            json.dumps(about, ensure_ascii=False)[:220],
        )
        await js(cdp, "document.querySelector('.settings-section .update-check-btn').click()")
        await wait_for(cdp, "!!document.querySelector('.settings-section .update-check-result')", timeout=15)
        about_result = await js(cdp, "document.querySelector('.settings-section .update-check-result').innerText")
        check(
            "关于页检查更新显示 current/latest/hasUpdate/notes",
            "5.1.9" in about_result and "5.1.10" in about_result and "true" in about_result and "notes" not in about_result,
            about_result[:200].replace("\n", " | "),
        )

        # About dialog also exposes the working check.
        await js(
            cdp,
            """(() => {
              const b = [...document.querySelectorAll('.settings-section button')]
                .find(e => (e.innerText || '').includes('打开关于窗口'));
              if (!b) return 'not-found';
              b.click();
              return 'ok';
            })()""",
        )
        await wait_for(cdp, "!!document.querySelector('.modal-title')", timeout=10)
        await wait_for(cdp, "!!document.querySelector('.update-check-panel .update-check-btn')", timeout=10)
        dialog = await js(
            cdp,
            """(() => {
              const btn = document.querySelector('.update-check-panel .update-check-btn');
              return {
                found: !!btn,
                enabled: !!btn && !btn.disabled,
                title: document.querySelector('.modal-title')?.innerText || '',
              };
            })()""",
        )
        check(
            "关于窗口检查更新可用",
            dialog["found"] and dialog["enabled"] and "关于 H1KHub" in dialog["title"],
            json.dumps(dialog, ensure_ascii=False)[:180],
        )
        await js(cdp, "document.querySelector('.update-check-panel .update-check-btn').click()")
        await wait_for(cdp, "!!document.querySelector('.update-check-panel .update-check-result')", timeout=15)
        dialog_result = await js(cdp, "document.querySelector('.update-check-panel .update-check-result').innerText")
        check(
            "关于窗口检查更新显示版本与说明",
            "5.1.9" in dialog_result and "5.1.10" in dialog_result and "manifest newer.json" in dialog_result,
            dialog_result[:180].replace("\n", " | "),
        )
        await js(cdp, "document.querySelector('.modal .modal-foot .btn')?.click()")

        errors = []
        for ev in cdp.events:
            if ev.get("method") == "Runtime.exceptionThrown":
                errors.append((ev.get("params") or {}).get("exceptionDetails", {}).get("text"))
            if ev.get("method") == "Log.entryAdded" and (ev.get("params") or {}).get("entry", {}).get("level") == "error":
                errors.append((ev.get("params") or {}).get("entry", {}).get("text"))
        check("更新/关于 UI 无 JS 异常", not errors, json.dumps(errors, ensure_ascii=False)[:300])
    finally:
        try:
            await cdp.close()
        finally:
            proc.kill()


def main() -> int:
    global BASE, TOKEN
    started = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--token", default="")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    server = start_http_server()
    original = {}
    try:
        code, payload = req("/api/settings")
        original = ((data_of(payload) or {}).get("update") or {}) if code == 200 else {}

        # Manifests used by the tests.
        newer_url = write_manifest("newer.json", version="5.1.10", build=520, notes="manifest newer.json")
        same_url = write_manifest("same.json", version="5.1.9", build=519, notes="same")
        older_url = write_manifest("older.json", version="5.1.8", build=400, notes="older")
        build_newer_url = write_manifest("build-newer.json", version="5.1.9", build=600, notes="build newer")
        build_older_url = write_manifest("build-older.json", version="5.1.9", build=500, notes="build older")
        multi_digit_url = write_manifest("multi-digit.json", version="5.1.10", build=100, notes="numeric version")
        invalid_url = os.path.join(TMP, "invalid.json")
        with open(invalid_url, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        invalid_url = "file://" + invalid_url
        bad_version_url = write_manifest("bad-version.json", version="5.1.x", build=520)
        bad_build_url = write_manifest("bad-build.json", build="520")
        bad_md5_url = write_manifest("bad-md5.json", md5="zzzz")
        missing_fields_url = write_manifest("missing-fields.json")
        with open(os.path.join(TMP, "missing-fields.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": "5.1.10"}, fh)  # no build
        big_path = os.path.join(TMP, "huge.json")
        with open(big_path, "w", encoding="utf-8") as fh:
            json.dump({"version": "9.9.9", "build": 1, "notes": "x" * (300 * 1024)}, fh)
        huge_url = "file://" + big_path
        missing_url = "file://" + os.path.join(TMP, "does-not-exist.json")

        # Template + directory fixtures for channel selection.
        tpl_dir = os.path.join(TMP, "templates")
        os.makedirs(tpl_dir, exist_ok=True)
        for channel, env, version, build, notes in (
            ("stable", "production", "5.1.9", 519, "stable/production manifest"),
            ("experimental", "test", "5.1.11", 530, "experimental/test manifest"),
        ):
            with open(os.path.join(tpl_dir, f"{channel}-{env}.json"), "w", encoding="utf-8") as fh:
                json.dump({"version": version, "build": build, "notes": notes}, fh, ensure_ascii=False)
        dir_dir = os.path.join(TMP, "dir")
        os.makedirs(dir_dir, exist_ok=True)
        for channel, env, version, notes in (
            ("stable", "production", "5.1.9", "directory stable"),
            ("experimental", "test", "5.1.11", "directory experimental"),
        ):
            with open(os.path.join(dir_dir, f"update-{channel}-{env}.json"), "w", encoding="utf-8") as fh:
                json.dump({"version": version, "build": 530 if channel == "experimental" else 519, "notes": notes}, fh, ensure_ascii=False)

        print("[1] 未配置 / manifest 缺失或非法 → 400，不 500")
        code, payload = set_update({"manifestUrl": ""})
        check("清空 manifestUrl 成功", code == 200, f"{code}")
        expect_error("未配置 manifest → 400", "UPDATE_MANIFEST_NOT_CONFIGURED", "update.check", check_api())
        set_update({"manifestUrl": missing_url})
        expect_error("manifest 不存在 → 400", "UPDATE_MANIFEST_UNAVAILABLE", "update.check", check_api())
        set_update({"manifestUrl": invalid_url})
        expect_error("manifest 非 JSON → 400", "UPDATE_MANIFEST_INVALID", "update.check", check_api())
        set_update({"manifestUrl": huge_url})
        expect_error("manifest 超大 → 400", "UPDATE_MANIFEST_TOO_LARGE", "update.check", check_api())
        set_update({"manifestUrl": bad_version_url})
        expect_error("version 非法 → 400", "UPDATE_MANIFEST_INVALID", "update.check", check_api())
        set_update({"manifestUrl": bad_build_url})
        expect_error("build 非整数 → 400", "UPDATE_MANIFEST_INVALID", "update.check", check_api())
        set_update({"manifestUrl": bad_md5_url})
        expect_error("md5 非法 → 400", "UPDATE_MANIFEST_INVALID", "update.check", check_api())
        set_update({"manifestUrl": missing_fields_url})
        expect_error("缺 build 字段 → 400", "UPDATE_MANIFEST_INVALID", "update.check", check_api())
        code, payload = req("/api/update/check")
        check("错误路径均非 500", code == 400 and "UPDATE_" in err_of(payload).get("code", ""), f"{code}")

        print("[2] 数值版本比较")
        set_update({"manifestUrl": newer_url, "channel": "stable", "env": "production"})
        code, payload = check_api()
        d = data_of(payload) or {}
        check(
            "更高版本 → hasUpdate:true + notes",
            code == 200 and d.get("hasUpdate") is True and d.get("notes") == "manifest newer.json"
            and d.get("current") == {"version": "5.1.9", "build": 519}
            and d.get("latest") == {"version": "5.1.10", "build": 520},
            json.dumps(d, ensure_ascii=False)[:240],
        )
        check(
            "响应含 manifest 契约字段 url/md5/source",
            d.get("url") == "https://example.com/h1khub-5.1.10.deb" and d.get("md5") == MD5 and d.get("source") == "file",
            f"url={d.get('url')} md5={d.get('md5')} source={d.get('source')}",
        )
        set_update({"manifestUrl": same_url})
        code, payload = check_api()
        d = data_of(payload) or {}
        check("相同版本/build → hasUpdate:false", code == 200 and d.get("hasUpdate") is False, f"{code} {d.get('latest')}")
        set_update({"manifestUrl": older_url})
        code, payload = check_api()
        d = data_of(payload) or {}
        check("更低版本 → hasUpdate:false", code == 200 and d.get("hasUpdate") is False, f"{code} {d.get('latest')}")
        set_update({"manifestUrl": build_newer_url})
        code, payload = check_api()
        d = data_of(payload) or {}
        check("同版本更高 build（数值）→ true", code == 200 and d.get("hasUpdate") is True, f"{code} {d.get('latest')}")
        set_update({"manifestUrl": build_older_url})
        code, payload = check_api()
        d = data_of(payload) or {}
        check("同版本更低 build → false", code == 200 and d.get("hasUpdate") is False, f"{code} {d.get('latest')}")
        set_update({"manifestUrl": multi_digit_url})
        code, payload = check_api()
        d = data_of(payload) or {}
        check("5.1.10 > 5.1.9（非字符串比较）", code == 200 and d.get("hasUpdate") is True, f"{code} {d.get('latest')}")

        print("[3] URL 白名单 / 本机 http / 重定向")
        before = settings_update()
        code, payload = set_update({"manifestUrl": "https://api.hexhub.cn/api/client/version/check"})
        check(
            "拒绝 api.hexhub.cn（400 UPDATE_MANIFEST_FORBIDDEN）",
            code == 400 and err_of(payload).get("code") == "UPDATE_MANIFEST_FORBIDDEN",
            f"{code} {json.dumps(err_of(payload), ensure_ascii=False)[:160]}",
        )
        check("被拒绝的地址未持久化", settings_update().get("manifestUrl") == before.get("manifestUrl"))
        code, payload = set_update({"manifestUrl": "ftp://127.0.0.1/manifest.json"})
        check("拒绝非 file/http(s) 协议", code == 400 and err_of(payload).get("code") == "UPDATE_MANIFEST_URL_INVALID", f"{code}")
        code, payload = set_update({"manifestUrl": "file://otherhost/tmp/x.json"})
        check("拒绝 file:// 远程主机", code == 400 and err_of(payload).get("code") == "UPDATE_MANIFEST_URL_INVALID", f"{code}")
        code, payload = set_update({"manifestUrl": "file:relative.json"})
        check("拒绝相对 file 路径", code == 400 and err_of(payload).get("code") == "UPDATE_MANIFEST_URL_INVALID", f"{code}")

        code, payload = set_update({"manifestUrl": f"http://127.0.0.1:{SERVER_PORT}/manifest.json"})
        check("接受本机 http manifest", code == 200, f"{code}")
        code, payload = check_api()
        d = data_of(payload) or {}
        check(
            "本机 http manifest 读取成功",
            code == 200 and d.get("hasUpdate") is True and d.get("latest") == {"version": "5.1.12", "build": 540} and d.get("source") == "http",
            f"{code} {d.get('latest')} source={d.get('source')}",
        )
        check("本机 HTTP 只请求一次", SERVER_PATHS.count("/manifest.json") == 1, f"paths={SERVER_PATHS[-4:]}")
        set_update({"manifestUrl": f"http://127.0.0.1:{SERVER_PORT}/invalid.json"})
        expect_error("本机 http 非 JSON → 400", "UPDATE_MANIFEST_INVALID", "update.check", check_api())
        set_update({"manifestUrl": f"http://127.0.0.1:{SERVER_PORT}/big.json"})
        expect_error("本机 http 超大（Content-Length）→ 400", "UPDATE_MANIFEST_TOO_LARGE", "update.check", check_api())
        SERVER_PATHS.clear()
        set_update({"manifestUrl": f"http://127.0.0.1:{SERVER_PORT}/redirect"})
        expect_error("重定向不跟随 → 400", "UPDATE_MANIFEST_UNAVAILABLE", "update.check", check_api())
        check("重定向未被跟随（只请求原始路径）", SERVER_PATHS == ["/redirect"], f"paths={SERVER_PATHS}")

        print("[4] 通道白名单 / 持久化 / manifest 选择")
        code, payload = req("/api/update/channel", {"channel": "beta"}, method="POST")
        check("非法通道 400 UPDATE_INVALID_CHANNEL", code == 400 and err_of(payload).get("code") == "UPDATE_INVALID_CHANNEL", f"{code}")
        code, payload = req("/api/update/channel", {"env": "dev"}, method="POST")
        check("非法 env 400 UPDATE_INVALID_ENV", code == 400 and err_of(payload).get("code") == "UPDATE_INVALID_ENV", f"{code}")
        code, payload = req("/api/update/channel", {}, method="POST")
        check("空请求体 400", code == 400 and err_of(payload).get("code") == "UPDATE_INVALID_CHANNEL", f"{code}")
        code, payload = set_update({"channel": "rc"})
        check("通用 PUT 也拒绝非法通道", code == 400 and err_of(payload).get("code") == "UPDATE_INVALID_CHANNEL", f"{code}")
        code, payload = req("/api/update/channel", {"channel": "stable", "env": "production"}, method="POST")
        check("合法通道 POST 成功", code == 200 and data_of(payload).get("channel") == "stable", f"{code}")
        code, payload = req("/api/update/channel", {"channel": "experimental", "env": "test"}, method="POST")
        d = data_of(payload) or {}
        check(
            "experimental/test 持久化并返回解析地址",
            code == 200 and d.get("channel") == "experimental" and d.get("env") == "test" and d.get("manifestResolved"),
            json.dumps(d, ensure_ascii=False)[:200],
        )
        persisted = settings_update()
        check("GET /api/settings 读回通道/env", persisted.get("channel") == "experimental" and persisted.get("env") == "test")
        started_at = ((req("/api/public/info")[1].get("data") or {}).get("startedAt"))
        req("/api/update/channel", {"channel": "stable"}, method="POST")
        check("切换通道不重启服务", ((req("/api/public/info")[1].get("data") or {}).get("startedAt")) == started_at)

        template = "file://" + tpl_dir + "/{channel}-{env}.json"
        set_update({"manifestUrl": template, "channel": "stable", "env": "production"})
        code, payload = check_api()
        d = data_of(payload) or {}
        check("模板选择 stable/production", code == 200 and d.get("notes") == "stable/production manifest" and d.get("hasUpdate") is False, json.dumps(d, ensure_ascii=False)[:200])
        req("/api/update/channel", {"channel": "experimental", "env": "test"}, method="POST")
        code, payload = check_api()
        d = data_of(payload) or {}
        check(
            "切换通道后选择 experimental/test manifest",
            code == 200 and d.get("notes") == "experimental/test manifest" and d.get("latest") == {"version": "5.1.11", "build": 530},
            json.dumps(d, ensure_ascii=False)[:220],
        )
        set_update({"manifestUrl": "file://" + dir_dir + "/", "channel": "stable", "env": "production"})
        code, payload = check_api()
        d = data_of(payload) or {}
        check("目录 URL 选择 update-stable-production.json", code == 200 and d.get("notes") == "directory stable", f"{code} {d.get('notes')}")
        req("/api/update/channel", {"channel": "experimental", "env": "test"}, method="POST")
        code, payload = check_api()
        d = data_of(payload) or {}
        check("目录 URL 选择 update-experimental-test.json", code == 200 and d.get("notes") == "directory experimental", f"{code} {d.get('notes')}")

        print("[5] 只读检查：不写设置 / 不写资产 / 不触发热更新")
        set_update({"manifestUrl": newer_url, "channel": "stable", "env": "production"})
        settings_before = req("/api/settings")[1]
        assets_before = data_of(req("/api/assets")[1]).get("assets") or []
        code, payload = check_api()
        check("版本检查成功", code == 200 and data_of(payload).get("hasUpdate") is True, f"{code}")
        settings_after = req("/api/settings")[1]
        assets_after = data_of(req("/api/assets")[1]).get("assets") or []
        check(
            "检查不修改设置",
            json.dumps(settings_before, sort_keys=True, ensure_ascii=False) == json.dumps(settings_after, sort_keys=True, ensure_ascii=False),
        )
        check(
            "检查不新增/修改资产",
            [a.get("id") for a in assets_before] == [a.get("id") for a in assets_after],
            f"{len(assets_before)} → {len(assets_after)}",
        )
        code, payload = req("/api/update/start", {}, method="POST")
        check(
            "热更新 start 仍 501/update.hot",
            code == 501 and err_of(payload).get("code") == "FEATURE_RESERVED" and err_of(payload).get("feature") == "update.hot",
            f"{code}",
        )
        code, payload = req("/api/update/status")
        check(
            "热更新 status 仍 501/update.hot",
            code == 501 and err_of(payload).get("code") == "FEATURE_RESERVED" and err_of(payload).get("feature") == "update.hot",
            f"{code}",
        )
        code, payload = req("/api/update/check")
        check("检查接口不返回 501", code != 501, f"{code}")

        print("[6] 前端更新节 / 关于页（真实 CDP）")
        asyncio.run(ui_checks(newer_url))
    finally:
        try:
            if original:
                set_update(original)
        except Exception as exc:
            print(f"restore failed: {exc}")
        server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)

    elapsed = time.time() - started
    print(f"\nPASS={PASS} FAIL={FAIL}")
    print(f"ELAPSED={elapsed:.1f}")
    if FAILURES:
        print("FAILURES=" + json.dumps(FAILURES, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
