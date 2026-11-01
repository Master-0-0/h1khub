"""Shared headless-UI probe used by the per-dialect database tests.

Opens one DB asset by name in the replica UI, expands the object tree, opens a
table and reports what actually rendered. Used only by the test scripts.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_check import CDP, launch  # noqa: E402


async def _click(cdp, selector, text, event="click"):
    return await cdp.evaluate(
        f"""(() => {{
          const els = [...document.querySelectorAll({json.dumps(selector)})];
          const el = els.find(e => (e.innerText || '').trim().includes({json.dumps(text)}));
          if (!el) return 'not-found';
          el.dispatchEvent(new MouseEvent({json.dumps(event)}, {{bubbles: true, cancelable: true}}));
          return 'ok';
        }})()"""
    )


async def _click_title(cdp, title, event="click"):
    """Click a tree row by its ``title`` attribute; table rows carry the row
    count in their text (``EMP`` + badge ``14``), so text matching is unsafe."""
    sel = f'.workspace .asset-panel .tree-row[title="{title}"]'
    return await cdp.evaluate(
        f"""(() => {{
          const el = document.querySelector({json.dumps(sel)});
          if (!el) return 'not-found';
          el.dispatchEvent(new MouseEvent({json.dumps(event)}, {{bubbles: true, cancelable: true}}));
          return 'ok';
        }})()"""
    )


async def _wait_for(cdp, expr, timeout: float = 60.0, step: float = 0.5):
    """Poll a JS expression until it is truthy; remote DBs need seconds to connect."""
    loops = int(timeout / step)
    for _ in range(loops):
        try:
            if await cdp.evaluate(expr):
                return True
        except Exception:
            pass
        await asyncio.sleep(step)
    return False


async def probe(
    asset_name: str,
    table_name: str,
    base: str | None = None,
    *,
    min_rows: int = 1,
    wait_timeout: float = 90.0,
) -> dict:
    url = base or os.environ.get("H1KHUB_BASE", "http://127.0.0.1:38110/")
    proc, ws = await launch(url)
    cdp = CDP(ws)
    result = {"assetClicked": False, "tree": "", "tableClicked": False, "tableText": "", "rows": 0}
    try:
        await cdp.connect()
        await cdp.send("Runtime.enable")
        await _wait_for(cdp, "document.querySelectorAll('.asset-panel .tree-row').length")
        # Demo assets live inside the "生产环境" folder; expand it first so the
        # asset row exists before we try to open it.
        await _click(cdp, ".asset-panel .tree-row", "生产环境", "dblclick")
        await asyncio.sleep(1.2)
        clicked = await _click(cdp, ".asset-panel .tree-row", asset_name, "dblclick")
        result["assetClicked"] = clicked == "ok"
        result["sidebar"] = (
            await cdp.evaluate("document.querySelector('.asset-panel')?.innerText || ''")
        )[:400]
        # A remote instance needs the overview round-trip plus the object-tree
        # round-trip before any table row exists; poll instead of a fixed sleep.
        # Rows are matched by ``title`` because the text includes the row badge.
        # The shared Oracle training instance also drops a whole tree load every
        # few minutes, so fall back to the panel's own refresh button (the exact
        # action a user takes) before declaring the tree empty.
        row_sel = f'.workspace .asset-panel .tree-row[title="{table_name}"]'
        found = await _wait_for(
            cdp, f"!!document.querySelector({json.dumps(row_sel)})", timeout=wait_timeout
        )
        refreshes = 0
        while not found and refreshes < 2:
            refreshes += 1
            await cdp.evaluate(
                """(() => {
                  const b = document.querySelector('.workspace .asset-panel button[title="刷新"]');
                  if (!b) return 'not-found';
                  b.click();
                  return 'ok';
                })()"""
            )
            found = await _wait_for(
                cdp, f"!!document.querySelector({json.dumps(row_sel)})", timeout=wait_timeout
            )
        result["refreshRetries"] = refreshes
        result["tree"] = (
            await cdp.evaluate("document.querySelector('.workspace .asset-panel')?.innerText || ''")
        )[:400]
        result["treeFull"] = (
            await cdp.evaluate("document.querySelector('.workspace .asset-panel')?.innerText || ''")
        )[:4000]
        table = await _click_title(cdp, table_name)
        result["tableClicked"] = table == "ok"
        rows_ready = await _wait_for(
            cdp,
            "document.querySelectorAll('.workspace .data-grid tbody tr, .workspace table tbody tr'"
            f").length >= {int(min_rows)}",
            timeout=wait_timeout,
        )
        if not rows_ready and table == "ok":
            # The row load races the same transient window; opening the table a
            # second time is enough to recover once the instance answers again.
            await _click_title(cdp, table_name)
            await _wait_for(
                cdp,
                "document.querySelectorAll('.workspace .data-grid tbody tr, .workspace table tbody tr'"
                f").length >= {int(min_rows)}",
                timeout=wait_timeout,
            )
        result["tableText"] = (await cdp.evaluate("document.body.innerText || ''"))[:2000]
        result["rows"] = await cdp.evaluate(
            "document.querySelectorAll('.workspace .data-grid tbody tr, .workspace table tbody tr').length"
        )
    finally:
        try:
            await cdp.close()
        except Exception:
            pass
        proc.kill()
    return result
