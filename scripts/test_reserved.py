#!/usr/bin/env python3
"""Reserved-slot tests (round 23): registry, HTTP 501 contract, inert settings, UI.

Round 22 moved lock.screen to ``done``; round 23 moved update.check (#91) and
update.channel (#93) to ``done``.  The remaining reserved entries are
update.hot (#92), ext.mcp (#98) and ext.plugins (#99); cloud.encryption and
lock.tray are ``unavailable``.  The point of this suite is to prove three
things and *not* to pretend the features exist:

  1. every reserved capability is declared exactly once and documented;
  2. every placeholder route answers HTTP 501 + FEATURE_RESERVED and never
     returns ok:true (a reserved feature must not appear to work);
  3. the still-reserved settings keys round-trip through PUT/GET but change
     nothing (no gate), while the implemented update check/channel do not
     answer 501 and hot update keeps answering 501.

It also checks the UI: update check/channel/autoCheck are configurable, hot
update stays disabled, and the About check button is enabled.

Usage: python3 scripts/test_reserved.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import cdp_check  # noqa: E402
from cdp_check import CDP, launch  # noqa: E402

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "reserved-slots.md")

EXPECTED = {
    "lock.screen": (90, "done"),
    "lock.tray": (90, "unavailable"),
    "update.check": (91, "done"),
    "update.hot": (92, "reserved"),
    "update.channel": (93, "done"),
    "cloud.encryption": (95, "unavailable"),
    "ext.mcp": (98, "reserved"),
    "ext.plugins": (99, "reserved"),
}
# 501 routes that remain: hot update (start/status), MCP, plugins.
RESERVED_ROUTES = [
    ("POST", "/api/update/start", "update.hot"),
    ("GET", "/api/update/status", "update.hot"),
    ("GET", "/api/mcp/info", "ext.mcp"),
    ("GET", "/api/plugins", "ext.plugins"),
]
RESERVED_SETTINGS = {
    "plugins": {"enabled": True, "dir": "/tmp/h1khub-plugins-probe"},
    "mcp": {"enabled": True, "transport": "stdio"},
    "cloud": {"encryption": True},
}


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
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


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}".rstrip())
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}".rstrip())


def doc_rows() -> list[tuple[str, int]]:
    """Parse the 预留位总表 table: | oid | `id` | ... |"""
    rows: list[tuple[str, int]] = []
    with open(DOC, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\|\s*(\d+)\s*\|\s*`([a-z.]+)`\s*\|", line)
            if m:
                rows.append((m.group(2), int(m.group(1))))
    return rows


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


async def section_state(cdp):
    return await js(
        cdp,
        """(() => {
          const section = document.querySelector('.settings-section');
          const rows = [...document.querySelectorAll('.settings-section .settings-row')];
          const buttons = [...document.querySelectorAll('.settings-section button')];
          return {
            labels: rows.map(r => (r.querySelector('.label')?.innerText || '').trim()),
            disabled: buttons.filter(b => b.disabled).map(b => b.getAttribute('title') || ''),
            enabled: buttons.filter(b => !b.disabled).length,
            text: section ? section.innerText : '',
          };
        })()""",
    )


async def ui_checks() -> None:
    proc, ws_url = await launch(BASE + "/")
    cdp = CDP(ws_url)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    try:
        await wait_for(cdp, "!!document.querySelector('.workspace')", timeout=25)
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

        labels = await js(cdp, "[...document.querySelectorAll('.settings-nav button')].map(b => (b.innerText||'').trim())")
        check(
            "新增 3 个数据驱动设置节",
            all(x in labels for x in ("更新", "安全", "扩展")),
            json.dumps(labels, ensure_ascii=False),
        )

        r = await click_nav(cdp, "更新")
        await wait_for(cdp, "document.querySelectorAll('.settings-section .settings-row').length >= 1", timeout=10)
        st = await section_state(cdp)
        check(
            "更新节列出 91/92/93",
            all(any(k in lab for lab in st["labels"]) for k in ("版本检查", "热更新", "更新通道")),
            json.dumps(st["labels"], ensure_ascii=False),
        )
        upd = await js(
            cdp,
            """(() => {
              const q = (s) => document.querySelector(s);
              const hot = q('.update-hot');
              const checkBtn = q('.update-check-btn');
              return {
                checkBtn: !!checkBtn,
                checkEnabled: !!checkBtn && !checkBtn.disabled,
                channel: !!q('.update-channel'),
                channelEnabled: !!q('.update-channel') && !q('.update-channel').disabled,
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
            "更新节版本检查/通道/autoCheck/manifest 可配置",
            upd["checkBtn"] and upd["checkEnabled"] and upd["channel"] and upd["channelEnabled"]
            and upd["env"] and upd["auto"] and upd["manifest"],
            json.dumps(upd, ensure_ascii=False)[:260],
        )
        check(
            "更新节热更新仍禁用且 title=预留（未实现）",
            upd["hot"] and upd["hotDisabled"] and upd["hotTitle"] == "预留（未实现）",
            json.dumps({k: upd[k] for k in ("hot", "hotDisabled", "hotTitle")}, ensure_ascii=False),
        )

        await click_nav(cdp, "安全")
        await asyncio.sleep(0.6)
        st = await section_state(cdp)
        check(
            "安全节列出锁屏与系统托盘",
            any("锁屏" in x for x in st["labels"]) and any("系统托盘" in x for x in st["labels"]),
            json.dumps(st["labels"], ensure_ascii=False),
        )
        sec = await js(
            cdp,
            """(() => {
              const q = (s) => document.querySelector(s);
              return {
                switchFound: !!q('.lock-enabled'),
                idleFound: !!q('.lock-idle'),
                newPw: !!q('.lock-new-password'),
                savePw: !!q('.lock-save-password'),
                lockNow: !!q('.lock-now'),
                lockNowDisabled: !!q('.lock-now') && q('.lock-now').disabled,
                trayDisabled: [...document.querySelectorAll('.lock-tray button')].every(b => b.disabled),
                text: q('.settings-section')?.innerText || '',
              };
            })()""",
        )
        check(
            "安全节改为可配置：开关/空闲选择/口令输入/保存/立即锁屏",
            sec["switchFound"] and sec["idleFound"] and sec["newPw"] and sec["savePw"] and sec["lockNow"],
            json.dumps(sec, ensure_ascii=False)[:240],
        )
        check("未设置口令时立即锁屏禁用", sec["lockNowDisabled"], str(sec["lockNowDisabled"]))
        check("系统托盘保持不可用", sec["trayDisabled"] and "系统托盘" in sec["text"] and "不可用" in sec["text"], sec["text"][:120])
        check("设置页不回传口令哈希", "passwordHash" not in sec["text"] and "pbkdf2_sha256$" not in sec["text"])
        check("95 云加密无 UI 入口", "云加密" not in st["text"], st["text"][:80])

        await click_nav(cdp, "扩展")
        await asyncio.sleep(0.4)
        st = await section_state(cdp)
        check(
            "扩展节列出 MCP 与插件市场",
            any("MCP" in x for x in st["labels"]) and any("插件市场" in x for x in st["labels"]),
            json.dumps(st["labels"], ensure_ascii=False),
        )
        check("扩展节 2 个禁用控件", st["enabled"] == 0 and len(st["disabled"]) == 2, f"disabled={st['disabled']}")

        await click_nav(cdp, "关于")
        await asyncio.sleep(0.4)
        about = await js(
            cdp,
            """(() => {
              const sec = document.querySelector('.settings-section');
              const btn = [...document.querySelectorAll('.settings-section button')]
                .find(b => (b.innerText || '').includes('检查更新'));
              return {
                found: !!btn,
                disabled: !!btn && btn.disabled,
                reservedText: (sec?.innerText || '').includes('检查更新（预留）'),
                version: (sec?.innerText || ''),
              };
            })()""",
        )
        check(
            "关于页检查更新入口可用（不再禁用）",
            about["found"] and not about["disabled"] and not about["reservedText"] and "5.1.9" in about["version"],
            json.dumps(about, ensure_ascii=False)[:160],
        )

        errors = []
        for ev in cdp.events:
            if ev.get("method") == "Runtime.exceptionThrown":
                errors.append(ev["params"]["exceptionDetails"].get("text"))
            if ev.get("method") == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
                errors.append(ev["params"]["entry"]["text"])
        check("设置对话框无 JS 异常/错误", not errors, json.dumps(errors, ensure_ascii=False)[:300])
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

    print("[1] 能力注册表")
    code, payload = req("/api/capabilities")
    caps = (((payload or {}).get("data") or {}).get("capabilities") or []) if code == 200 else []
    check("GET /api/capabilities 200 + ok", code == 200 and payload.get("ok") is True, f"{code}")
    check("注册表 8 条", len(caps) == 8, f"n={len(caps)}")
    ids = {c.get("id") for c in caps}
    check("8 个 id 齐全", ids == set(EXPECTED), json.dumps(sorted(ids), ensure_ascii=False))
    check(
        "状态三值与预期一致",
        {c["id"]: c["status"] for c in caps} == {k: v[1] for k, v in EXPECTED.items()},
        json.dumps({c["id"]: c["status"] for c in caps}, ensure_ascii=False),
    )
    check(
        "oid 与预期一致",
        {c["id"]: c["oid"] for c in caps} == {k: v[0] for k, v in EXPECTED.items()},
        json.dumps({c["id"]: c["oid"] for c in caps}, ensure_ascii=False),
    )
    reserved = [c for c in caps if c["status"] == "reserved"]
    unavailable = [c for c in caps if c["status"] == "unavailable"]
    done = [c for c in caps if c["status"] == "done"]
    check(
        "reserved 有入口、unavailable 无入口",
        all(c.get("entry") for c in reserved) and all(not c.get("entry") for c in unavailable),
        f"reserved={len(reserved)} unavailable={len(unavailable)}",
    )
    check(
        "已实现 3 项：lock.screen / update.check / update.channel",
        {c["id"] for c in done} == {"lock.screen", "update.check", "update.channel"},
        f"done={[c['id'] for c in done]}",
    )
    check(
        "仍不可用 2 项：lock.tray / cloud.encryption",
        {c["id"] for c in unavailable} == {"lock.tray", "cloud.encryption"},
        f"unavailable={[c['id'] for c in unavailable]}",
    )

    doc_text = open(DOC, encoding="utf-8").read()
    anchors_ok = True
    for cap in caps:
        anchor = (cap.get("docs") or "").split("#", 1)[-1]
        if not cap.get("docs", "").startswith("docs/reserved-slots.md#") or not re.search(
            rf"^## {cap['oid']} ", doc_text, re.M
        ):
            anchors_ok = False
    check("docs 锚点均指向文档中对应 oid 小节", anchors_ok)

    rows = doc_rows()
    check(
        "文档总表与注册表一一对应（8 行 / id+oid）",
        len(rows) == 8 and sorted(rows) == sorted((c["id"], c["oid"]) for c in caps),
        json.dumps(rows, ensure_ascii=False),
    )

    print("[2] 501 占位契约")
    for method, path, feature in RESERVED_ROUTES:
        code, payload = req(path, {} if method == "POST" else None, method=method)
        err = ((payload or {}).get("error") or {}) if isinstance(payload, dict) else {}
        ok_body = (
            code == 501
            and payload.get("ok") is False
            and err.get("code") == "FEATURE_RESERVED"
            and err.get("feature") == feature
            and err.get("reservedIn") == "round21"
            and err.get("docs", "").startswith("docs/reserved-slots.md#")
            and bool(err.get("message"))
        )
        check(f"{method} {path} → 501/{feature}", ok_body, f"{code} {json.dumps(err, ensure_ascii=False)[:120]}")

    code, payload = req("/api/lock/verify", {}, method="POST")
    err = ((payload or {}).get("error") or {}) if isinstance(payload, dict) else {}
    check(
        "POST /api/lock/verify 已实现（不再 501）",
        code != 501 and err.get("code") in ("LOCK_NOT_LOCKED", "LOCK_NOT_CONFIGURED"),
        f"{code} {json.dumps(err, ensure_ascii=False)[:120]}",
    )

    # #91/#93 are implemented: they must not answer 501 anymore.  A missing
    # manifest is a normal 400 UPDATE_MANIFEST_NOT_CONFIGURED for the check.
    code, payload = req("/api/update/check")
    err = ((payload or {}).get("error") or {}) if isinstance(payload, dict) else {}
    check(
        "GET /api/update/check 已实现（400/200，不再是 501）",
        code in (200, 400) and code != 501,
        f"{code} {json.dumps(err, ensure_ascii=False)[:120]}",
    )
    code, payload = req("/api/update/channel", {"channel": "stable"}, method="POST")
    check(
        "POST /api/update/channel 已实现（200，不再是 501）",
        code == 200 and ((payload or {}).get("data") or {}).get("channel") == "stable",
        f"{code}",
    )

    print("[3] unavailable 无路由")
    for path in ("/api/cloud/encryption", "/api/lock/tray"):
        code, _ = req(path)
        check(f"{path} 未注册（404）", code == 404, f"{code}")

    print("[4] 配置可持久化但不激活")
    original = {}
    original_update = {}
    code, current = req("/api/settings")
    if code == 200 and isinstance(current, dict):
        data = current.get("data") or {}
        original = {k: data.get(k, {}) for k in RESERVED_SETTINGS}
        original_update = data.get("update", {}) or {}
    try:
        code, saved = req("/api/settings", RESERVED_SETTINGS, method="PUT")
        data = (saved or {}).get("data") or {}
        check("预留配置 PUT 成功", code == 200 and saved.get("ok") is True, f"{code}")
        check(
            "预留配置 GET 读回一致",
            all(data.get(k, {}).get(key) == val for k, obj in RESERVED_SETTINGS.items() for key, val in obj.items()),
            json.dumps({k: data.get(k) for k in RESERVED_SETTINGS}, ensure_ascii=False),
        )
        code, payload = req("/api/update/status")
        check(
            "写入其余预留配置后热更新仍 501",
            code == 501 and ((payload.get("error") or {}).get("code") == "FEATURE_RESERVED"),
            f"{code}",
        )
        code, payload = req("/api/mcp/info")
        check(
            "写入 mcp.enabled=true 后仍 501",
            code == 501 and ((payload.get("error") or {}).get("code") == "FEATURE_RESERVED"),
            f"{code}",
        )

        print("[5] 普通功能不受 gate 影响")
        _, assets = req("/api/assets")
        items = ((assets or {}).get("data") or {}).get("assets") or []
        sqlite = next((a for a in items if a.get("name") == "SQLite demo"), None)
        if sqlite is None:
            sqlite = next((a for a in items if a.get("type") == 4), None)
        check("找到数据库资产", bool(sqlite), str(sqlite)[:120])
        if sqlite:
            code, overview = req("/api/db/overview", {"assetId": sqlite["id"], "refresh": True}, method="POST")
            d = (overview or {}).get("data") or {}
            check(
                "打开 DB 资产 /api/db/overview 正常",
                code == 200 and overview.get("ok") is True and "databases" in d,
                f"{code} dialect={d.get('dialect')}",
            )
    finally:
        if original:
            req("/api/settings", original, method="PUT")
    code, restored = req("/api/settings")
    rd = (restored or {}).get("data") or {}
    update_ok = all(rd.get("update", {}).get(k) == v for k, v in original_update.items())
    check(
        "预留配置已恢复默认",
        code == 200 and update_ok and rd.get("lock", {}).get("enabled") is False,
        json.dumps({k: rd.get(k) for k in ("update", "lock")}, ensure_ascii=False),
    )

    print("[6] 前端关闭态入口（CDP）")
    asyncio.run(ui_checks())

    elapsed = time.time() - started
    print(f"\nPASS={PASS} FAIL={FAIL}")
    print(f"ELAPSED={elapsed:.1f}")
    if FAILURES:
        print("FAILURES=" + json.dumps(FAILURES, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
