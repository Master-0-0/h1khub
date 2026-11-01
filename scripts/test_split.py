#!/usr/bin/env python3
"""UI regression test for the multi-pane "split view" feature (原版 多窗口/分屏).

Reproduces the original's 视图拆分 semantics in browser form:
  · 垂直分屏 / 水平分屏（标签右键菜单 + 快捷键 Ctrl+Shift+= / Ctrl+Shift+-）
  · 拖动分隔条调整面板比例
  · Ctrl+←/→/↑/↓ 在面板间移动焦点
  · 每个面板独立标签集，最多 4 个面板
  · 资产右键「同屏打开（新分屏）」
  · 关闭分屏按钮回收面板

Pure UI assertions over CDP plus a sessions API cross-check. No DB writes.

Usage: python3 scripts/test_split.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import cdp_check  # noqa: E402
from cdp_check import CDP, launch  # noqa: E402

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []
SHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "screenshots")


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def api_get(path: str):
    req = urllib.request.Request(BASE + path, headers={"X-H1KHub-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}".rstrip())
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}".rstrip())


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
        except Exception as exc:  # page may still be settling
            last = exc
        await asyncio.sleep(interval)
    raise RuntimeError(f"timeout waiting for: {expr} (last={last})")


MOD = {"alt": 1, "ctrl": 2, "meta": 4, "shift": 8}


async def key(cdp, key_name, code, vk, mods=()):
    mod = 0
    for m in mods:
        mod |= MOD[m]
    for kind in ("keyDown", "keyUp"):
        await cdp.send(
            "Input.dispatchKeyEvent",
            {
                "type": kind,
                "key": key_name,
                "code": code,
                "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk,
                "modifiers": mod,
            },
        )
    await asyncio.sleep(0.25)


async def click_selector(cdp, selector, index=0):
    return await js(
        cdp,
        f"""(() => {{
          const els = [...document.querySelectorAll({json.dumps(selector)})];
          const el = els[{index}];
          if (!el) return 'not-found';
          el.scrollIntoView({{block: 'nearest'}});
          el.click();
          return 'ok';
        }})()""",
    )


async def context_menu(cdp, selector, index=0):
    return await js(
        cdp,
        f"""(() => {{
          const els = [...document.querySelectorAll({json.dumps(selector)})];
          const el = els[{index}];
          if (!el) return 'not-found';
          const r = el.getBoundingClientRect();
          el.dispatchEvent(new MouseEvent('contextmenu', {{
            bubbles: true, cancelable: true,
            clientX: r.left + Math.max(4, r.width / 2),
            clientY: r.top + Math.max(4, r.height / 2),
          }}));
          return 'ok';
        }})()""",
    )


async def menu_click(cdp, text):
    return await js(
        cdp,
        f"""(() => {{
          const item = [...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(e => (e.innerText || '').trim().includes({json.dumps(text)}));
          if (!item) return 'not-found';
          if (item.classList.contains('disabled')) return 'disabled';
          item.click();
          return 'ok';
        }})()""",
    )


def list_pages():
    data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp_check.PORT}/json/list", timeout=3))
    return [p for p in data if p.get("type") == "page" and p.get("webSocketDebuggerUrl")]


async def pane_state(cdp):
    return await js(
        cdp,
        """(() => {
          const ws = document.querySelector('.workspace');
          const panes = [...document.querySelectorAll('.workspace > .pane')];
          return {
            count: panes.length,
            dir: ws?.classList.contains('horizontal') ? 'horizontal' : (ws?.classList.contains('panes') ? 'vertical' : 'single'),
            hasPanesClass: !!ws?.classList.contains('panes'),
            handles: document.querySelectorAll('.workspace > .pane-handle').length,
            focused: panes.findIndex(p => p.classList.contains('focused')),
            tabs: panes.map(p => p.querySelectorAll('.tabbar .tab').length),
            terms: panes.map(p => p.querySelectorAll('.xterm').length),
            widths: panes.map(p => Math.round(p.getBoundingClientRect().width)),
            heights: panes.map(p => Math.round(p.getBoundingClientRect().height)),
            sessionIds: panes.map(p => p.querySelector('.tabbar .tab')?.getAttribute('title') || ''),
          };
        })()""",
    )


async def drag_handle(cdp, dx):
    box = await js(
        cdp,
        """(() => {
          const h = document.querySelector('.workspace > .pane-handle');
          if (!h) return null;
          const r = h.getBoundingClientRect();
          return { x: r.left + r.width / 2, y: r.top + Math.min(120, r.height / 2) };
        })()""",
    )
    if not box:
        return False
    await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1})
    for step in (0.4, 0.8, 1.0):
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": box["x"] + dx * step, "y": box["y"], "button": "left"})
        await asyncio.sleep(0.05)
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": box["x"] + dx, "y": box["y"], "button": "left"})
    await asyncio.sleep(0.3)
    return True


async def main() -> int:
    global BASE, TOKEN
    started = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base
    TOKEN = load_token()

    proc, ws_url = await launch(BASE + "/")
    cdp = CDP(ws_url)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    try:
        await wait_for(cdp, "!!document.querySelector('.workspace') && !!document.querySelector('.icon-rail, .rail, .sidebar, .asset-panel')", timeout=25)

        print("[1] 单面板基线")
        st = await pane_state(cdp)
        check("初始单面板", st["count"] == 1, f"panes={st['count']}")
        check("初始无分屏样式", st["hasPanesClass"] is False and st["dir"] == "single", f"dir={st['dir']}")

        print("[2] 打开本地终端")
        sessions_before = len(api_get("/api/sessions").get("data", {}).get("sessions", []))
        await click_selector(cdp, '.tabbar [title="新建本地终端"], .tabbar-tools [title="新建本地终端"]')
        await wait_for(cdp, "document.querySelectorAll('.pane .xterm').length >= 1", timeout=25)
        st = await pane_state(cdp)
        check("终端标签已打开", st["tabs"] == [1], f"tabs={st['tabs']}")
        check("终端渲染 xterm", st["terms"] == [1], f"terms={st['terms']}")

        print("[3] 右键标签 → 垂直分屏")
        r = await context_menu(cdp, ".pane .tabbar .tab")
        check("标签右键菜单打开", r == "ok", str(r))
        await wait_for(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        items = await js(cdp, "[...document.querySelectorAll('.ctx-menu .ctx-item')].map(e => (e.innerText||'').trim())")
        check("菜单含垂直分屏", any("垂直分屏" in (x or "") for x in items), json.dumps(items, ensure_ascii=False)[:120])
        check("菜单含水平分屏", any("水平分屏" in (x or "") for x in items), "")
        r = await menu_click(cdp, "垂直分屏")
        check("点击垂直分屏", r == "ok", str(r))
        await wait_for(cdp, "document.querySelectorAll('.workspace > .pane').length === 2", timeout=10)
        st = await pane_state(cdp)
        check("分屏后 2 面板", st["count"] == 2, f"panes={st['count']}")
        check("布局为垂直(左右)", st["dir"] == "vertical", f"dir={st['dir']}")
        check("分隔条存在", st["handles"] == 1, f"handles={st['handles']}")
        check("两个面板各有 1 标签", st["tabs"] == [1, 1], f"tabs={st['tabs']}")
        check("两个面板各有终端", st["terms"] == [1, 1], f"terms={st['terms']}")
        check("新面板获得焦点", st["focused"] == 1, f"focused={st['focused']}")

        sessions_after = len(api_get("/api/sessions").get("data", {}).get("sessions", []))
        check("分屏新建独立会话", sessions_after == sessions_before + 2, f"{sessions_before} → {sessions_after}")

        print("[4] 拖动分隔条")
        before = (await pane_state(cdp))["widths"]
        ok = await drag_handle(cdp, 140)
        check("找到分隔条并拖动", ok, "")
        after = (await pane_state(cdp))["widths"]
        grew = after[0] - before[0]
        total_ok = abs((after[0] + after[1]) - (before[0] + before[1])) <= 3
        check("左侧面板变宽", grew > 60, f"{before} → {after} (Δ={grew})")
        check("总宽度守恒", total_ok, f"{before[0] + before[1]} vs {after[0] + after[1]}")
        ratio = after[0] / max(1, after[0] + after[1])
        check("比例在 15%~85% 内", 0.15 <= ratio <= 0.85, f"ratio={ratio:.2f}")

        print("[5] Ctrl+方向键移动焦点")
        st = await pane_state(cdp)
        check("焦点在右面板", st["focused"] == 1, f"focused={st['focused']}")
        await js(cdp, "document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        await key(cdp, "ArrowLeft", "ArrowLeft", 37, ("ctrl",))
        st = await pane_state(cdp)
        check("Ctrl+← 焦点左移", st["focused"] == 0, f"focused={st['focused']}")
        await key(cdp, "ArrowRight", "ArrowRight", 39, ("ctrl",))
        st = await pane_state(cdp)
        check("Ctrl+→ 焦点右移", st["focused"] == 1, f"focused={st['focused']}")
        await key(cdp, "ArrowRight", "ArrowRight", 39, ("ctrl",))
        st = await pane_state(cdp)
        check("最右不再右移", st["focused"] == 1, f"focused={st['focused']}")

        print("[6] 资产右键同屏打开")
        r = await context_menu(cdp, '.asset-panel .tree-row[title="本地终端"]')
        if r != "ok":
            r = await context_menu(cdp, '.asset-panel .tree-row')
        check("资产右键菜单打开", r == "ok", str(r))
        await wait_for(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        r = await menu_click(cdp, "同屏打开")
        check("点击同屏打开", r == "ok", str(r))
        await wait_for(cdp, "document.querySelectorAll('.workspace > .pane').length === 3", timeout=10)
        st = await pane_state(cdp)
        check("同屏打开新增面板", st["count"] == 3, f"panes={st['count']}")
        check("新面板有终端", st["terms"][-1] == 1, f"terms={st['terms']}")

        print("[7] 快捷键分屏至 4 面板上限")
        active = await js(cdp, "document.querySelectorAll('.pane.focused .tabbar .tab.active').length")
        check("聚焦面板有活动标签", active == 1, f"active tabs in focused pane={active}")
        await key(cdp, "=", "Equal", 187, ("ctrl", "shift"))
        await wait_for(cdp, "document.querySelectorAll('.workspace > .pane').length === 4", timeout=10)
        st = await pane_state(cdp)
        check("Ctrl+Shift+= 分屏到 4", st["count"] == 4, f"panes={st['count']}")
        await key(cdp, "-", "Minus", 189, ("ctrl", "shift"))
        await asyncio.sleep(0.6)
        st = await pane_state(cdp)
        check("超过 4 个被拒绝", st["count"] == 4, f"panes={st['count']}")
        toast = await js(cdp, "[...document.querySelectorAll('.toast')].map(e => (e.innerText||'').trim()).join(' | ')")
        check("提示最多 4 个分屏", "4" in (toast or ""), (toast or "")[:80])

        print("[8] 面板内标签相互独立")
        st = await pane_state(cdp)
        await click_selector(cdp, '.rail-btn[title="Docker"], .icon-rail [title="Docker"]')
        await asyncio.sleep(1.2)
        st2 = await pane_state(cdp)
        check("仅聚焦面板新增标签", sum(st2["tabs"]) == sum(st["tabs"]) + 1, f"{st['tabs']} → {st2['tabs']}")
        check("其他面板标签数不变", st2["tabs"][:-1] == st["tabs"][:-1], f"{st2['tabs']}")
        view_ok = await js(cdp, "!!document.querySelector('.pane.focused .pane-body .view')")
        check("聚焦面板渲染独立视图", view_ok is True, f"view={view_ok}")
        widths = st2["widths"]
        balanced = max(widths) - min(widths) <= 6
        check("分屏宽度均分", balanced, f"widths={widths}")

        print("[8b] 多窗口：在新窗口打开")
        await context_menu(cdp, ".pane.focused .tabbar .tab")
        await wait_for(cdp, "!!document.querySelector('.ctx-menu')", timeout=6)
        seen = {p["webSocketDebuggerUrl"] for p in list_pages()}
        ok = await cdp.send(
            "Runtime.evaluate",
            {
                "userGesture": True,
                "returnByValue": True,
                "expression": """(() => {
                  const item = [...document.querySelectorAll('.ctx-menu .ctx-item')]
                    .find(e => (e.innerText || '').includes('在新窗口打开'));
                  if (!item) return 'not-found';
                  item.click();
                  return 'ok';
                })()""",
            },
        )
        check("点击在新窗口打开", ok.get("result", {}).get("value") == "ok", str(ok.get("result", {}).get("value")))
        new_page = None
        deadline = time.time() + 15
        while time.time() < deadline and new_page is None:
            for page in list_pages():
                if page["webSocketDebuggerUrl"] not in seen:
                    new_page = page
                    break
            if new_page is None:
                await asyncio.sleep(0.3)
        check("新窗口已创建", new_page is not None, (new_page or {}).get("url", "")[:120])
        if new_page:
            cdp2 = CDP(new_page["webSocketDebuggerUrl"])
            await cdp2.connect()
            try:
                await cdp2.send("Runtime.enable")
                await cdp2.send("Page.enable")
                ok = False
                for _ in range(60):
                    try:
                        n = await cdp2.evaluate("document.querySelectorAll('.workspace > .pane').length")
                        tabs = await cdp2.evaluate("document.querySelectorAll('.tabbar .tab').length")
                        if n == 1 and tabs == 1:
                            ok = True
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
                check("新窗口独立单面板", ok, f"panes={n} tabs={tabs}")
                title = await cdp2.evaluate("document.querySelector('.tabbar .tab')?.getAttribute('title') || ''")
                check("新窗口打开目标资产", "终端" in (title or ""), title)
                await cdp2.screenshot(os.path.join(SHOT_DIR, "33-multi-window.png"))
            finally:
                await cdp2.close()

        print("[9] 关闭分屏")
        await cdp.screenshot(os.path.join(SHOT_DIR, "32-split-view.png"))
        before = sum(st2["tabs"])
        r = await click_selector(cdp, ".workspace > .pane .pane-close", 2)
        check("点击关闭分屏", r == "ok", str(r))
        await wait_for(cdp, "document.querySelectorAll('.workspace > .pane').length === 3", timeout=10)
        st = await pane_state(cdp)
        check("面板数 4 → 3", st["count"] == 3, f"panes={st['count']}")
        check("该面板标签一并关闭", sum(st["tabs"]) == before - 1, f"{before} → {sum(st['tabs'])}")

        for _ in range(6):
            r = await click_selector(cdp, ".workspace > .pane .pane-close", 1)
            if r == "not-found":
                break
            await asyncio.sleep(0.4)
            stx = await pane_state(cdp)
            if stx["count"] == 1:
                break
        st = await pane_state(cdp)
        check("回收为单面板", st["count"] == 1, f"panes={st['count']}")
        check("单面板无分屏样式", st["hasPanesClass"] is False, f"hasPanesClass={st['hasPanesClass']}")

        print("[10] 控制台无异常")
        errors = []
        for ev in cdp.events:
            if ev.get("method") == "Runtime.exceptionThrown":
                errors.append(ev["params"]["exceptionDetails"].get("text"))
            if ev.get("method") == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
                errors.append(ev["params"]["entry"]["text"])
        check("无 JS 异常/错误日志", not errors, json.dumps(errors, ensure_ascii=False)[:300])

        elapsed = time.time() - started
        print(f"\nPASS={PASS} FAIL={FAIL}")
        print(f"ELAPSED={elapsed:.1f}")
        if FAILURES:
            print("FAILURES=" + json.dumps(FAILURES, ensure_ascii=False))
        return 0 if FAIL == 0 else 1
    finally:
        try:
            await cdp.close()
        finally:
            proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
