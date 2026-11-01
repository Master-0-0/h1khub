#!/usr/bin/env python3
"""Round-22 screen-lock tests: API contract, hashing at rest, backoff, idle
expiry, refresh/new-tab persistence, and the CDP overlay.

What is proven:
  * POST /api/lock/verify is implemented (no longer the round-21 501 stub);
  * only a PBKDF2-HMAC-SHA256 record + random salt is stored, never plaintext,
    and the hash is not returned by GET /api/settings;
  * a wrong password increments a failure counter and triggers exponential
    backoff; a correct password resets it;
  * idle auto-lock is evaluated on the server, so a page refresh or a second
    browser (new tab) still shows the overlay;
  * the settings UI can configure the lock and still marks the system tray as
    unavailable (browser form).

Usage: python3 scripts/test_lock.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sqlite3
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
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
DB = os.path.join(HOME, "data.db")

PASSWORD_A = "S3cret!Pass-22A"
PASSWORD_B = "S3cret!Pass-22B"
WRONG = "definitely-wrong-22"


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


def req_no_token(path: str, body=None, method="GET"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


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


def lock_status():
    code, payload = req("/api/lock/status")
    return code, data_of(payload) or {}, payload


def db_lock_setting() -> dict:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='lock'").fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def parse_hash(value: str):
    parts = (value or "").split("$")
    if len(parts) != 4:
        return None
    return {
        "scheme": parts[0],
        "iterations": int(parts[1]),
        "salt": parts[2],
        "digest": parts[3],
    }


def raw_db_contains(secret: str) -> bool:
    for path in (DB, DB + "-wal"):
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            if secret.encode("utf-8") in fh.read():
                return True
    return False


def cleanup_known_password() -> bool:
    """Best-effort reset so a previous failed run cannot wedge this one.

    Returns False when a password exists but is not one of this suite's known
    passwords; the caller must stop instead of producing a cascade of false
    failures.
    """
    _, status, _ = lock_status()
    if not status.get("hasPassword"):
        req("/api/lock/config", {"enabled": False, "idleMinutes": 0}, method="PUT")
        return True
    for candidate in (PASSWORD_B, PASSWORD_A):
        code, _ = req("/api/lock/password", {"oldPassword": candidate}, method="DELETE")
        if code == 200:
            req("/api/lock/config", {"enabled": False, "idleMinutes": 0}, method="PUT")
            return True
    return False


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


async def set_input(cdp, selector: str, value: str):
    return await js(
        cdp,
        f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return 'not-found';
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(el, {json.dumps(value)});
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
          return 'ok';
        }})()""",
    )


async def overlay_state(cdp):
    return await js(
        cdp,
        """(() => {
          const el = document.querySelector('.lock-screen');
          if (!el) return { present: false };
          const rect = el.getBoundingClientRect();
          const body = document.querySelector('.app-body');
          return {
            present: true,
            fullScreen: rect.width >= window.innerWidth - 1 && rect.height >= window.innerHeight - 1,
            fixed: getComputedStyle(el).position === 'fixed',
            zIndex: Number(getComputedStyle(el).zIndex) || 0,
            input: !!el.querySelector('.lock-password'),
            bodyInert: !!body && body.inert === true,
            text: el.innerText.slice(0, 160),
          };
        })()""",
    )


async def open_settings_security(cdp):
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
    await wait_for(cdp, "!!document.querySelector('.settings-layout')", timeout=15)
    r = await click_nav(cdp, "安全")
    await asyncio.sleep(0.6)
    return r


async def ui_checks() -> None:
    # Make sure the overlay starts hidden and the password is known.
    req("/api/lock/config", {"enabled": True, "idleMinutes": 0}, method="PUT")
    code, payload = req("/api/lock/lock", {}, method="POST")
    check("UI 前置：API 锁定成功", code == 200 and (data_of(payload) or {}).get("locked") is True, f"{code}")

    proc, ws_url = await launch(BASE + "/")
    cdp = CDP(ws_url)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    second = None
    try:
        st = await wait_for(cdp, "(() => { const s = document.querySelector('.lock-screen'); return s ? {present:true, fullScreen:s.getBoundingClientRect().width>=window.innerWidth-1, fixed:getComputedStyle(s).position==='fixed', zIndex:Number(getComputedStyle(s).zIndex)||0, input:!!s.querySelector('.lock-password'), bodyInert:!!document.querySelector('.app-body')?.inert, text:s.innerText.slice(0,160) } : null })()", timeout=25)
        check("锁定后新页面显示全屏遮罩", st["present"] and st["fullScreen"], json.dumps(st, ensure_ascii=False)[:200])
        check("遮罩 fixed 且 z-index 高于其他浮层", st["fixed"] and st["zIndex"] >= 1000, f"z={st['zIndex']}")
        check("遮罩包含口令输入框，背景工作区 inert", st["input"] and st["bodyInert"], json.dumps(st, ensure_ascii=False)[:160])

        # Wrong password through the real form.
        await set_input(cdp, ".lock-password", WRONG)
        await asyncio.sleep(0.1)
        r = await js(cdp, "document.querySelector('.lock-unlock')?.click(); 'ok'")
        await wait_for(cdp, "!!document.querySelector('.lock-error')", timeout=10)
        err = await js(cdp, "document.querySelector('.lock-error')?.innerText || ''")
        meta = await js(cdp, "document.querySelector('.lock-meta')?.innerText || ''")
        check("UI 错误口令显示错误且不解除遮罩", "不正确" in err and bool(await js(cdp, "!!document.querySelector('.lock-screen')")), err[:80])
        check("UI 显示实时错误尝试计数", "1" in meta, meta[:80])

        # Correct password through the form.
        await set_input(cdp, ".lock-password", PASSWORD_B)
        await asyncio.sleep(0.1)
        await js(cdp, "document.querySelector('.lock-unlock')?.click(); 'ok'")
        await wait_for(cdp, "!document.querySelector('.lock-screen')", timeout=15)
        check("UI 正确口令解除遮罩", True)

        # Settings security section reflects the server state and stays configurable.
        r = await open_settings_security(cdp)
        sec = await js(
            cdp,
            """(() => {
              const q = (s) => document.querySelector(s);
              const text = q('.settings-section')?.innerText || '';
              return {
                nav: !!q('.settings-nav'),
                switchOn: !!q('.lock-enabled') && q('.lock-enabled').classList.contains('on'),
                idleValue: q('.lock-idle')?.value,
                savePw: !!q('.lock-save-password'),
                lockNow: !!q('.lock-now'),
                lockNowEnabled: !!q('.lock-now') && !q('.lock-now').disabled,
                trayDisabled: [...document.querySelectorAll('.lock-tray button')].every(b => b.disabled),
                text,
              };
            })()""",
        )
        check(
            "安全节可配置：开关为开、空闲选择、口令保存、立即锁屏可用",
            r == "ok" and sec["switchOn"] and sec["savePw"] and sec["lockNow"] and sec["lockNowEnabled"],
            json.dumps(sec, ensure_ascii=False)[:260],
        )
        check("安全节无口令哈希泄露", "passwordHash" not in sec["text"] and "pbkdf2_sha256$" not in sec["text"])
        check("系统托盘仍显示不可用", sec["trayDisabled"] and "系统托盘" in sec["text"] and "不可用" in sec["text"])

        # Change idle through the UI select, then confirm the server took it.
        await js(
            cdp,
            """(() => {
              const el = document.querySelector('.lock-idle');
              if (!el) return 'not-found';
              el.value = '1';
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return 'ok';
            })()""",
        )
        await asyncio.sleep(0.8)
        _, status, _ = lock_status()
        check("UI 修改空闲自动锁屏时间并持久化", status.get("idleMinutes") == 1, json.dumps(status, ensure_ascii=False)[:160])
        req("/api/lock/config", {"idleMinutes": 0}, method="PUT")

        # Lock now from the settings button.
        await js(cdp, "document.querySelector('.lock-now')?.click(); 'ok'")
        await wait_for(cdp, "!!document.querySelector('.lock-screen')", timeout=10)
        check("设置页「立即锁屏」触发遮罩", True)
        await set_input(cdp, ".lock-password", PASSWORD_B)
        await asyncio.sleep(0.1)
        await js(cdp, "document.querySelector('.lock-unlock')?.click(); 'ok'")
        await wait_for(cdp, "!document.querySelector('.lock-screen')", timeout=15)

        # Refresh while locked: the server-side state survives a page reload.
        req("/api/lock/lock", {}, method="POST")
        await cdp.send("Page.reload", {"ignoreCache": True})
        st = await wait_for(cdp, "(() => { const s = document.querySelector('.lock-screen'); return s ? {present:true} : null })()", timeout=25)
        check("刷新页面后仍保持锁定", st["present"] is True, json.dumps(st))
        await set_input(cdp, ".lock-password", PASSWORD_B)
        await asyncio.sleep(0.1)
        await js(cdp, "document.querySelector('.lock-unlock')?.click(); 'ok'")
        await wait_for(cdp, "!document.querySelector('.lock-screen')", timeout=15)

        # A second browser (new tab) also sees the lock.
        req("/api/lock/lock", {}, method="POST")
        second_proc, second_ws = await launch(BASE + "/")
        second = CDP(second_ws)
        await second.connect()
        await second.send("Runtime.enable")
        await second.send("Page.enable")
        st2 = await wait_for(second, "(() => { const s = document.querySelector('.lock-screen'); return s ? {present:true, input:!!s.querySelector('.lock-password')} : null })()", timeout=25)
        check("新开浏览器/标签仍显示锁定遮罩", st2["present"] and st2["input"], json.dumps(st2))
        code, _ = req("/api/lock/verify", {"password": PASSWORD_B}, method="POST")
        check("新标签场景可通过 API 解锁（清理）", code == 200, f"{code}")

        errors = []
        for ev in cdp.events:
            if ev.get("method") == "Runtime.exceptionThrown":
                errors.append(ev["params"]["exceptionDetails"].get("text"))
            if ev.get("method") == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
                entry = ev["params"]["entry"]
                text = entry.get("text") or ""
                # A deliberately wrong password produces a network-level 401
                # in the DevTools log; that is the expected contract, not a
                # JavaScript error.  Any other error still fails the check.
                if entry.get("url", "").endswith("/api/lock/verify") and ("status of 401" in text or "status of 429" in text):
                    continue
                errors.append(text)
        check("锁屏流程无 JS 异常/非预期控制台错误", not errors, json.dumps(errors, ensure_ascii=False)[:300])
    finally:
        try:
            await cdp.close()
        finally:
            proc.kill()
        if second is not None:
            try:
                await second.close()
            finally:
                second_proc.kill()


def api_checks() -> None:
    print("[1] 初始状态与能力注册表")
    code, status, payload = lock_status()
    check("GET /api/lock/status 200 + ok", code == 200 and payload.get("ok") is True, f"{code}")
    check(
        "初始未启用、无口令、未锁定",
        status.get("enabled") is False and status.get("hasPassword") is False and status.get("locked") is False,
        json.dumps(status, ensure_ascii=False)[:200],
    )
    check(
        "锁屏端点仍受本地 Token 鉴权（无 token=401）",
        req_no_token("/api/lock/status") == 401 and req_no_token("/api/lock/verify", {"password": "x"}, "POST") == 401,
        f"status={req_no_token('/api/lock/status')}",
    )
    code, caps_payload = req("/api/capabilities")
    caps = {c["id"]: c for c in ((data_of(caps_payload) or {}).get("capabilities") or [])}
    check("capabilities: lock.screen=done", caps.get("lock.screen", {}).get("status") == "done", str(caps.get("lock.screen", {}).get("status")))
    check("capabilities: lock.tray=unavailable", caps.get("lock.tray", {}).get("status") == "unavailable", str(caps.get("lock.tray", {}).get("status")))

    print("[2] 口令设置与哈希落盘")
    code, payload = req("/api/lock/config", {"enabled": True}, method="PUT")
    check(
        "无口令时启用被拒绝（409 LOCK_PASSWORD_REQUIRED）",
        code == 409 and err_of(payload).get("code") == "LOCK_PASSWORD_REQUIRED",
        f"{code} {json.dumps(err_of(payload), ensure_ascii=False)[:120]}",
    )
    code, payload = req("/api/lock/password", {"newPassword": "123"}, method="POST")
    check("过短口令被拒绝（400）", code == 400 and err_of(payload).get("code") == "LOCK_PASSWORD_TOO_SHORT", f"{code}")

    code, payload = req("/api/lock/password", {"newPassword": PASSWORD_A}, method="POST")
    status = data_of(payload) or {}
    check("首次设置口令成功并自动启用", code == 200 and status.get("hasPassword") is True and status.get("enabled") is True, f"{code}")
    check("接口不回传口令哈希", "passwordHash" not in json.dumps(payload), json.dumps(payload, ensure_ascii=False)[:160])

    code, settings_payload = req("/api/settings")
    settings_lock = ((data_of(settings_payload) or {}).get("lock") or {})
    check(
        "GET /api/settings 不含 lock.passwordHash 或明文口令",
        code == 200 and "passwordHash" not in settings_lock and PASSWORD_A not in json.dumps(settings_payload, ensure_ascii=False),
        json.dumps(settings_lock, ensure_ascii=False)[:160],
    )

    stored = db_lock_setting()
    record = parse_hash(stored.get("passwordHash") or "")
    salt_ok = digest_ok = False
    if record:
        try:
            salt_ok = len(base64.b64decode(record["salt"], validate=True)) == 16
            digest_ok = len(base64.b64decode(record["digest"], validate=True)) == 32
        except Exception:
            salt_ok = digest_ok = False
    check(
        "data.db 仅存 PBKDF2 记录（scheme/iterations/16B salt/32B hash）",
        record is not None and record["scheme"] == "pbkdf2_sha256" and record["iterations"] == 210000 and salt_ok and digest_ok,
        json.dumps(record or {}, ensure_ascii=False)[:160],
    )
    check("data.db 不含明文口令 A", not raw_db_contains(PASSWORD_A))

    hash_a = stored.get("passwordHash") or ""
    salt_a = record["salt"] if record else ""

    print("[3] 修改口令（需旧口令）与新 salt")
    code, payload = req("/api/lock/password", {"oldPassword": WRONG, "newPassword": PASSWORD_B}, method="POST")
    check("旧口令错误时修改被拒绝（401）", code == 401 and err_of(payload).get("code") == "LOCK_BAD_OLD_PASSWORD", f"{code}")
    code, payload = req("/api/lock/password", {"oldPassword": PASSWORD_A, "newPassword": PASSWORD_B}, method="POST")
    status = data_of(payload) or {}
    check("旧口令正确时修改成功", code == 200 and status.get("hasPassword") is True, f"{code}")
    stored_b = db_lock_setting()
    record_b = parse_hash(stored_b.get("passwordHash") or "")
    check(
        "修改后哈希与 salt 都变化（随机 salt）",
        bool(record_b) and record_b["salt"] != salt_a and stored_b.get("passwordHash") != hash_a,
        json.dumps(record_b or {}, ensure_ascii=False)[:160],
    )
    check("data.db 不含明文口令 B", not raw_db_contains(PASSWORD_B))

    print("[4] 立即锁定、错误退避与解锁")
    code, payload = req("/api/lock/lock", {}, method="POST")
    check("POST /api/lock/lock 立即锁定", code == 200 and (data_of(payload) or {}).get("locked") is True, f"{code}")
    _, status, _ = lock_status()
    check("状态接口报告 locked=true / lockedAt", status.get("locked") is True and bool(status.get("lockedAt")), json.dumps(status, ensure_ascii=False)[:200])

    attempts = []
    for index in range(3):
        code, payload = req("/api/lock/verify", {"password": WRONG}, method="POST")
        attempts.append((code, err_of(payload)))
    check(
        "连续 3 次错误口令返回 401 且计数递增",
        all(code == 401 for code, _ in attempts) and [e.get("failedAttempts") for _, e in attempts] == [1, 2, 3],
        json.dumps([[c, e.get("failedAttempts"), e.get("retryAfter")] for c, e in attempts], ensure_ascii=False),
    )
    check("第 3 次错误后返回退避秒数", (attempts[-1][1].get("retryAfter") or 0) >= 1, str(attempts[-1][1].get("retryAfter")))
    code, payload = req("/api/lock/verify", {"password": PASSWORD_B}, method="POST")
    check(
        "退避期内尝试返回 429 LOCK_RATE_LIMITED",
        code == 429 and err_of(payload).get("code") == "LOCK_RATE_LIMITED" and (err_of(payload).get("retryAfter") or 0) >= 1,
        f"{code} {json.dumps(err_of(payload), ensure_ascii=False)[:140]}",
    )
    time.sleep((err_of(payload).get("retryAfter") or 1) + 0.5)
    code, payload = req("/api/lock/verify", {"password": PASSWORD_B}, method="POST")
    status = data_of(payload) or {}
    check("退避结束后正确口令解锁并清零失败计数", code == 200 and status.get("locked") is False and status.get("failedAttempts") == 0, f"{code}")

    code, payload = req("/api/lock/verify", {"password": PASSWORD_B}, method="POST")
    check("未锁定时验证返回 409 LOCK_NOT_LOCKED", code == 409 and err_of(payload).get("code") == "LOCK_NOT_LOCKED", f"{code}")

    req("/api/lock/lock", {}, method="POST")
    code, payload = req("/api/lock/verify", {"password": PASSWORD_A}, method="POST")
    check("旧口令 A 在修改后无法解锁", code == 401 and err_of(payload).get("code") == "LOCK_BAD_PASSWORD", f"{code}")
    code, payload = req("/api/lock/verify", {"password": PASSWORD_B}, method="POST")
    check("新口令 B 可解锁", code == 200 and (data_of(payload) or {}).get("locked") is False, f"{code}")

    print("[5] 空闲自动锁定（服务端判定）")
    code, payload = req("/api/lock/config", {"idleMinutes": 0.02}, method="PUT")
    check("配置 1.2 秒空闲阈值成功", code == 200 and (data_of(payload) or {}).get("idleMinutes") == 0.02, f"{code}")
    req("/api/lock/activity", {}, method="POST")
    _, status, _ = lock_status()
    check("活动心跳后未立即锁定", status.get("locked") is False, json.dumps(status, ensure_ascii=False)[:160])
    time.sleep(2.0)
    _, status, _ = lock_status()
    check("超过空闲阈值后服务器判定 locked=true", status.get("locked") is True, json.dumps(status, ensure_ascii=False)[:200])
    code, payload = req("/api/lock/verify", {"password": PASSWORD_B}, method="POST")
    check("空闲锁定后正确口令解锁", code == 200 and (data_of(payload) or {}).get("locked") is False, f"{code}")
    req("/api/lock/config", {"idleMinutes": 0}, method="PUT")

    print("[6] 配置边界与通用设置不可绕过口令")
    req("/api/lock/config", {"enabled": False}, method="PUT")
    code, payload = req("/api/lock/lock", {}, method="POST")
    check("关闭锁屏后立即锁定返回 409 LOCK_NOT_CONFIGURED", code == 409 and err_of(payload).get("code") == "LOCK_NOT_CONFIGURED", f"{code}")
    code, payload = req("/api/lock/config", {"enabled": True}, method="PUT")
    check("已有口令时可重新启用", code == 200 and (data_of(payload) or {}).get("enabled") is True, f"{code}")
    code, payload = req(
        "/api/settings",
        {"lock": {"enabled": False, "passwordHash": "injected-by-generic-settings"}},
        method="PUT",
    )
    _, status, _ = lock_status()
    check(
        "通用 PUT /api/settings 不能改锁定配置或注入哈希",
        code == 200 and status.get("enabled") is True and status.get("hasPassword") is True,
        f"{code} {json.dumps(status, ensure_ascii=False)[:160]}",
    )
    stored = db_lock_setting()
    check("通用设置写入未污染 passwordHash", stored.get("passwordHash") != "injected-by-generic-settings", str(stored.get("passwordHash"))[:40])


def cleanup() -> None:
    print("[7] 清理回默认状态")
    _, status, _ = lock_status()
    if status.get("locked"):
        for candidate in (PASSWORD_B, PASSWORD_A):
            code, _ = req("/api/lock/verify", {"password": candidate}, method="POST")
            if code == 200:
                break
    for candidate in (PASSWORD_B, PASSWORD_A):
        code, _ = req("/api/lock/password", {"oldPassword": candidate}, method="DELETE")
        if code == 200:
            break
    req("/api/lock/config", {"enabled": False, "idleMinutes": 0}, method="PUT")
    _, status, _ = lock_status()
    check(
        "清理后 enabled=false / hasPassword=false / locked=false",
        status.get("enabled") is False and status.get("hasPassword") is False and status.get("locked") is False,
        json.dumps(status, ensure_ascii=False)[:200],
    )


def main() -> int:
    global BASE, TOKEN
    started = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--token", default="")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    if not cleanup_known_password():
        print("  FAIL  前置清理：存在未知锁屏口令（不属于本套件的测试口令）")
        print("请停止服务后清除 data.db 中 settings.key='lock' 的 passwordHash，再运行本套件。")
        print("\nPASS=0 FAIL=1")
        return 1
    try:
        api_checks()
        print("[8] 前端锁屏（CDP）")
        asyncio.run(ui_checks())
    finally:
        cleanup()

    elapsed = time.time() - started
    print(f"\nPASS={PASS} FAIL={FAIL}")
    print(f"ELAPSED={elapsed:.1f}")
    if FAILURES:
        print("FAILURES=" + json.dumps(FAILURES, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
