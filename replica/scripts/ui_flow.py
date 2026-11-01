"""Drive the H1KHub replica UI over CDP and capture screenshots."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cdp_check import CDP, launch  # noqa: E402

BASE = os.environ.get("H1KHUB_BASE", "http://127.0.0.1:38110/")


async def wait(ms=800):
    await asyncio.sleep(ms / 1000)


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
          return 'ok:' + (el.innerText || '').trim().slice(0, 40);
        }})()""",
    )


async def click_by_title(cdp, title):
    return await js(
        cdp,
        f"""(() => {{
          const el = document.querySelector(`[title=${{JSON.stringify({json.dumps(title)})}}]`);
          if (!el) return 'not-found';
          el.click();
          return 'ok';
        }})()""",
    )


async def type_text(cdp, text, enter=True):
    await js(cdp, "document.querySelector('.xterm-helper-textarea')?.focus()")
    await cdp.send("Input.insertText", {"text": text})
    await wait(300)
    if enter:
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})


async def collect_errors(cdp):
    errors = []
    for ev in cdp.events:
        if ev.get("method") == "Runtime.exceptionThrown":
            details = ev["params"]["exceptionDetails"]
            errors.append(details.get("exception", {}).get("description") or details.get("text"))
        elif ev.get("method") == "Runtime.consoleAPICalled" and ev["params"]["type"] == "error":
            errors.append("console: " + " ".join(str(a.get("value") or a.get("description")) for a in ev["params"]["args"]))
        elif ev.get("method") == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
            errors.append("log: " + ev["params"]["entry"]["text"])
    return errors


async def main():
    proc, ws = await launch(BASE)
    cdp = CDP(ws)
    await cdp.connect()
    await cdp.send("Runtime.enable")
    await cdp.send("Page.enable")
    await cdp.send("Log.enable")
    await wait(1200)
    for _ in range(50):
        if await js(cdp, "document.querySelectorAll('.asset-panel .tree-row').length") > 0:
            break
        await wait(200)
    await cdp.screenshot("/tmp/shot-00-home.png")
    print("home ok")

    # expand demo folder
    print("expand folder:", await click_by_text(cdp, ".asset-panel .tree-row", "生产环境", "dblclick"))
    await wait(500)

    # Open MySQL
    print("open mysql:", await click_by_text(cdp, ".asset-panel .tree-row", "MySQL demo", "dblclick"))
    await wait(2200)
    await cdp.screenshot("/tmp/shot-01-db.png")
    print("db view text:", (await js(cdp, "document.querySelector('.workspace')?.innerText"))[:160].replace("\n", " | "))

    # Open users table (left tree)
    print("open table:", await click_by_text(cdp, ".workspace .asset-panel .tree-row", "users"))
    await wait(1800)
    await cdp.screenshot("/tmp/shot-02-table.png")
    print("grid rows:", await js(cdp, "document.querySelectorAll('.grid tbody tr').length"))

    # Open DuckDB (embedded analytics database)
    print("open duckdb:", await click_by_text(cdp, ".asset-panel .tree-row", "DuckDB demo", "dblclick"))
    await wait(2400)
    print("duckdb tree:", (await js(cdp, "document.querySelector('.workspace .asset-panel')?.innerText") or "")[:160].replace("\n", " | "))
    print("open duck table:", await click_by_text(cdp, ".workspace .asset-panel .tree-row", "events"))
    await wait(1800)
    print("duck grid rows:", await js(cdp, "document.querySelectorAll('.grid tbody tr').length"))
    await cdp.screenshot("/tmp/shot-02b-duckdb.png")

    # Open MongoDB (document store)
    print("open mongodb:", await click_by_text(cdp, ".asset-panel .tree-row", "MongoDB demo", "dblclick"))
    await wait(2400)
    print("mongo tree:", (await js(cdp, "document.querySelector('.workspace .asset-panel')?.innerText") or "")[:160].replace("\n", " | "))
    print("open mongo collection:", await click_by_text(cdp, ".workspace .asset-panel .tree-row", "users"))
    await wait(1800)
    print("mongo grid rows:", await js(cdp, "document.querySelectorAll('.grid tbody tr').length"))
    await cdp.screenshot("/tmp/shot-02c-mongodb.png")

    # Open ClickHouse (columnar analytics)
    print("open clickhouse:", await click_by_text(cdp, ".asset-panel .tree-row", "ClickHouse demo", "dblclick"))
    await wait(2400)
    print("clickhouse tree:", (await js(cdp, "document.querySelector('.workspace .asset-panel')?.innerText") or "")[:160].replace("\n", " | "))
    print("open clickhouse table:", await click_by_text(cdp, ".workspace .asset-panel .tree-row", "users"))
    await wait(1800)
    print("clickhouse grid rows:", await js(cdp, "document.querySelectorAll('.grid tbody tr').length"))
    await cdp.screenshot("/tmp/shot-02d-clickhouse.png")

    # Open Dameng DM8 (Oracle-compatible dialect, uppercase identifiers)
    print("open dameng:", await click_by_text(cdp, ".asset-panel .tree-row", "达梦 demo", "dblclick"))
    await wait(3000)
    print("dameng tree:", (await js(cdp, "document.querySelector('.workspace .asset-panel')?.innerText") or "")[:160].replace("\n", " | "))
    print("open dameng table:", await click_by_text(cdp, ".workspace .asset-panel .tree-row", "USERS"))
    await wait(1800)
    print("dameng grid rows:", await js(cdp, "document.querySelectorAll('.grid tbody tr').length"))
    await cdp.screenshot("/tmp/shot-02e-dameng.png")

    # Open Oracle 19c (shared remote instance: the tree needs a few seconds and
    # table rows are matched by title because the text carries a row badge).
    # A busy moment on the shared instance can drop the whole tree load, so
    # re-open the asset once before giving up.
    oracle_row = '.workspace .asset-panel .tree-row[title="EMP"]'
    for attempt in range(2):
        label = "open oracle:" if attempt == 0 else "reopen oracle:"
        print(label, await click_by_text(cdp, ".asset-panel .tree-row", "Oracle demo", "dblclick"))
        found = False
        for _ in range(90):
            if await js(cdp, f"!!document.querySelector({json.dumps(oracle_row)})"):
                found = True
                break
            await wait(500)
        if found:
            break
    print("oracle tree:", (await js(cdp, "document.querySelector('.workspace .asset-panel')?.innerText") or "")[:160].replace("\n", " | "))
    print(
        "open oracle table:",
        await js(cdp, f"""(() => {{
          const el = document.querySelector({json.dumps(oracle_row)});
          if (!el) return 'not-found';
          el.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true}}));
          return 'ok';
        }})()"""),
    )
    for _ in range(120):
        if await js(cdp, "document.querySelectorAll('.grid tbody tr').length >= 14"):
            break
        await wait(500)
    print("oracle grid rows:", await js(cdp, "document.querySelectorAll('.grid tbody tr').length"))
    await cdp.screenshot("/tmp/shot-02f-oracle.png")

    # Open SQL Server 2019 (local container; dbo schema, rows carry a badge so
    # the table row is matched by its title attribute)
    print("open sqlserver:", await click_by_text(cdp, ".asset-panel .tree-row", "SQL Server demo", "dblclick"))
    sqlserver_row = '.workspace .asset-panel .tree-row[title="users"]'
    for _ in range(120):
        if await js(cdp, f"!!document.querySelector({json.dumps(sqlserver_row)})"):
            break
        await wait(500)
    print("sqlserver tree:", (await js(cdp, "document.querySelector('.workspace .asset-panel')?.innerText") or "")[:160].replace("\n", " | "))
    print(
        "open sqlserver table:",
        await js(cdp, f"""(() => {{
          const el = document.querySelector({json.dumps(sqlserver_row)});
          if (!el) return 'not-found';
          el.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true}}));
          return 'ok';
        }})()"""),
    )
    for _ in range(120):
        if await js(cdp, "document.querySelectorAll('.grid tbody tr').length >= 2"):
            break
        await wait(500)
    print("sqlserver grid rows:", await js(cdp, "document.querySelectorAll('.grid tbody tr').length"))
    await cdp.screenshot("/tmp/shot-02g-sqlserver.png")

    # Open SSH asset
    print("open ssh:", await click_by_text(cdp, ".asset-panel .tree-row", "SSH hexlab", "dblclick"))
    await wait(4000)
    await cdp.screenshot("/tmp/shot-03-ssh.png")
    print("term status:", await js(cdp, "document.querySelector('.term-status')?.innerText"))
    print("term renderer:", await js(cdp, "document.querySelector('.terminal-host')?.dataset.renderer"))
    print("term canvases:", await js(cdp, "document.querySelectorAll('.terminal-host canvas').length"))
    print("trzsz buttons:", await js(cdp, "document.querySelectorAll('.term-toolbar [title*=\"trzsz\"]').length"))
    print("trzsz download entry:", await js(cdp, "(()=>{const b=[...document.querySelectorAll('.term-toolbar button')].find(e=>(e.title||'').includes('trzsz 下载'));if(!b)return 'not-found';b.click();return 'ok'})()"))
    await wait(500)
    print("trzsz prompt:", (await js(cdp, "document.querySelector('.modal')?.innerText") or "")[:90].replace("\n", " | "))
    await cdp.screenshot("/tmp/shot-04b-trzsz.png")
    await js(cdp, "document.querySelector('.modal-foot .btn')?.click()")
    await wait(300)
    await type_text(cdp, "whoami; hostname; uname -r")
    await wait(1600)
    await cdp.screenshot("/tmp/shot-04-ssh-cmd.png")

    # Docker
    print("open docker:", await click_by_title(cdp, "Docker"))
    await wait(2500)
    await cdp.screenshot("/tmp/shot-05-docker.png")
    print("docker cards:", await js(cdp, "document.querySelectorAll('.dcard').length"))

    # Local OCI registry dialog (published images + pull entry)
    print("open images pane:", await click_by_text(cdp, ".toolbar .seg button", "镜像"))
    await wait(900)
    print("open local registry:", await click_by_text(cdp, ".toolbar button", "本地仓库"))
    await wait(1200)
    print("local registry dialog:", (await js(cdp, "document.querySelector('.modal')?.innerText") or "")[:140].replace("\n", " | "))
    await cdp.screenshot("/tmp/shot-05b-local-registry.png")
    await js(cdp, "document.querySelector('.modal-foot .btn')?.click()")
    await wait(500)

    # Redis
    print("open redis:", await click_by_title(cdp, "Redis"))
    await wait(2500)
    await cdp.screenshot("/tmp/shot-06-redis.png")
    print("redis keys:", await js(cdp, "document.querySelectorAll('.key-row').length"))

    # AI
    print("open ai:", await click_by_title(cdp, "AI 助手"))
    await wait(1500)
    await cdp.screenshot("/tmp/shot-07-ai.png")

    # Settings
    await js(cdp, "document.querySelector('.titlebar')?.querySelectorAll('button')[2]?.click()")
    await wait(800)
    await cdp.screenshot("/tmp/shot-08-settings.png")
    await js(cdp, "document.querySelector('.modal-head .icon-btn')?.click()")

    errors = await collect_errors(cdp)
    print("ERRORS:", json.dumps(errors, ensure_ascii=False)[:2500])
    await cdp.close()
    proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
