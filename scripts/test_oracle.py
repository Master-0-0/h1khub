#!/usr/bin/env python3
"""Regression test for the Oracle adapter against a real Oracle instance.

Target: the shared Oracle 19c SE2 training database
``ecourse.org:1521/ol8.ecourse.org`` (user SCOTT), reached through the replica
API. Every check below was calibrated against that instance, so no fixture is
invented:

  read path   EMP (14 rows / 8 cols / PK EMPNO) and DEPT (4 rows)
  plan        EXPLAIN PLAN FOR + DBMS_XPLAN.DISPLAY
  write path  only the self-created ``ZZ_ORA_PROBE`` table, always dropped again

SCOTT cannot read v$session / v$pdbs on this instance, so overview databases
fall back to the service name, realtime exposes only user_segments, and the
processlist is legitimately empty -- those checks must not demand more.

The public instance resets fresh connections now and then (DPY-4011); a failed
handshake never ran a statement, so reads and writes are retried a few times
with a delay.

Usage: python3 scripts/test_oracle.py [--base http://127.0.0.1:38110] [--token TOKEN]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []

ASSET_NAME = "Oracle demo"
DB = "ol8.ecourse.org"
SCHEMA = "SCOTT"
PROBE = "ZZ_ORA_PROBE"
EMP_COLUMNS = ["EMPNO", "ENAME", "JOB", "MGR", "HIREDATE", "SAL", "COMM", "DEPTNO"]

# python-oracledb transient network failures seen on this public instance.
TRANSIENT = ("DPY-4011", "DPY-6005", "DPY-4024", "DPI-1080")

CODE_PATH = {
    "class": "OracleAdapter",
    "port": 1521,
    "placeholder": ":1",
    "quote": '"USERS"',
    "aliases": ("oracledb",),
    "user": "scott",
    "database": "ol8.ecourse.org",
    "error_token": "Oracle",
}


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def _error_text(payload) -> str:
    if isinstance(payload, bytes):
        return payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)


def _is_transient(payload) -> bool:
    return any(token in _error_text(payload) for token in TRANSIENT)


def _req_once(path: str, body=None, method="POST", raw=False):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=240) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            detail = json.loads(text)
        except Exception:
            detail = text
        return {"__http_error": exc.code, "detail": detail}


def req(path: str, body=None, method="POST", raw=False, attempts: int = 4, delay: float = 2.0, retry: str = "transient"):
    """Call the API, retrying transient connect resets with a breathing gap.

    ``retry="transient"`` (default) re-runs read-only calls after any DPY-4xxx
    reset. Writes pass ``retry="connect"`` so only a failed *handshake* is
    retried -- then no statement reached the server, so no row can be doubled.
    """
    last = None
    for attempt in range(attempts):
        payload = _req_once(path, body, method=method, raw=raw)
        if raw or not isinstance(payload, dict) or not payload.get("__http_error"):
            return payload
        last = payload
        text = _error_text(payload)
        retryable = _is_transient(payload) if retry == "transient" else "Oracle 连接失败" in text
        if not retryable or attempt + 1 >= attempts:
            return payload
        print(f"    · transient {text[:90]} … retry {attempt + 1}/{attempts - 1}")
        time.sleep(delay)
    return last


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def data_of(resp):
    if isinstance(resp, dict) and resp.get("ok") and "data" in resp:
        return resp["data"]
    return None


def query(sql: str, asset_id: str):
    return data_of(req("/api/db/query", {"assetId": asset_id, "database": DB, "sql": sql}))


def rows_of(resp):
    try:
        return (resp.get("results") or [{}])[0].get("rows")
    except Exception:
        return None


def find_asset():
    data = data_of(req("/api/assets", method="GET")) or {}
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            return asset["id"]
    return None


def offline_mode() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from db_adapter_check import code_path_checks

    print(f"MODE=code-path（未找到资产 {ASSET_NAME}，仅验证 adapter 代码路径）")
    passed, failed, failures = code_path_checks("oracle", CODE_PATH)
    print(f"\nPASS={passed} FAIL={failed}")
    if failures:
        print("FAILURES:", failures)
    print("LIMIT: 未配置真实 Oracle 实例，库/表/查询/写入均未实测")
    return 0 if failed == 0 else 1


def row_action(asset_id: str, action: str, **extra):
    body = {
        "assetId": asset_id,
        "database": DB,
        "schema": SCHEMA,
        "table": PROBE,
        "action": action,
        **extra,
    }
    return data_of(req("/api/db/row", body, retry="connect"))


def drop_probe(asset_id: str) -> None:
    query(
        f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {SCHEMA}.{PROBE}'; "
        "EXCEPTION WHEN OTHERS THEN NULL; END;",
        asset_id,
    )


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    parser.add_argument("--skip-ui", action="store_true")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    asset_id = find_asset()
    if not asset_id:
        return offline_mode()
    print(f"asset {ASSET_NAME} = {asset_id}  target=scott@{DB}")

    # ---- connection ------------------------------------------------------
    test = data_of(req("/api/test-connection", {"assetId": asset_id}))
    check("连接测试", bool(test and test.get("ok")), str(test)[:300])
    version = str((test or {}).get("version") or "")
    check("版本来自实例 (Oracle Database 19c)", "Oracle Database 19c" in version, version[:160])
    check("版本含 SE2 版本串", "19." in version, version[:160])

    # ---- overview --------------------------------------------------------
    overview = data_of(req("/api/db/overview", {"assetId": asset_id})) or {}
    check("overview dialect", overview.get("dialect") == "oracle", str(overview.get("dialect")))
    dbs = [str(d.get("name")) for d in (overview.get("databases") or [])]
    check("databases 回退为服务名（v$pdbs 无权限）", dbs == [DB], str(dbs))
    names = [str(s).upper() for s in (overview.get("schemas") or [])]
    check("schema 列表含 SCOTT", SCHEMA in names, str(names)[:200])

    # ---- object tree -----------------------------------------------------
    objects = data_of(req("/api/db/objects", {"assetId": asset_id, "database": DB, "schema": SCHEMA})) or {}
    tables = [t.get("name") for t in objects.get("tables") or []]
    views = [v.get("name") for v in objects.get("views") or []]
    check("对象树含 EMP", "EMP" in tables, str(tables)[:200])
    check("对象树含 DEPT", "DEPT" in tables, str(tables)[:200])
    check("对象树规模 >= 80 表", len(tables) >= 80, f"tables={len(tables)}")
    check("对象树含视图", len(views) >= 1, str(views)[:120])
    emp_entry = next((t for t in objects.get("tables") or [] if t.get("name") == "EMP"), {})
    check("EMP 统计行数=14", int(emp_entry.get("rows") or 0) == 14, str(emp_entry)[:160])

    # ---- table data ------------------------------------------------------
    detail = data_of(
        req("/api/db/table", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "EMP", "limit": 20})
    ) or {}
    check("EMP total=14", detail.get("total") == 14, str(detail.get("total")))
    check("EMP 主键 EMPNO", detail.get("primaryKey") == ["EMPNO"], str(detail.get("primaryKey")))
    col_names = [c.get("name") for c in detail.get("columns") or []]
    check("EMP 8 列且名称一致", col_names == EMP_COLUMNS, str(col_names))
    check("EMP 可编辑标记", detail.get("editable") is True, str(detail.get("editable")))
    check("EMP 返回 14 行数据", len(detail.get("rows") or []) == 14, str(len(detail.get("rows") or [])))
    first = (detail.get("rows") or [[None]])[0]
    check("EMP 首行 SMITH", "SMITH" in [str(v) for v in first], str(first)[:160])

    dept = data_of(
        req("/api/db/table", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "DEPT", "limit": 10})
    ) or {}
    check("DEPT total=4", dept.get("total") == 4, str(dept.get("total")))

    # ---- SQL -------------------------------------------------------------
    check("count(emp)=14", rows_of(query("SELECT count(*) FROM emp", asset_id)) == [[14]], "")
    check("count(dept)=4", rows_of(query("SELECT count(*) FROM dept", asset_id)) == [[4]], "")
    check(
        "join emp/dept = 14",
        rows_of(
            query(
                "SELECT count(*) FROM emp e JOIN dept d ON e.deptno = d.deptno",
                asset_id,
            )
        )
        == [[14]],
        "",
    )
    named = query("SELECT empno, ename FROM emp WHERE empno = 7369", asset_id) or {}
    check(
        "行数据读回 SMITH",
        rows_of(named) == [[7369, "SMITH"]],
        str(rows_of(named)),
    )

    # ---- explain ---------------------------------------------------------
    explain = data_of(
        req("/api/db/explain", {"assetId": asset_id, "database": DB, "sql": "SELECT * FROM emp"})
    ) or {}
    plan = str(explain.get("plan") or "")
    check(
        "DBMS_XPLAN 计划",
        explain.get("format") == "text" and ("Plan hash value" in plan or "SELECT STATEMENT" in plan.upper()),
        plan[:200],
    )

    # ---- DDL -------------------------------------------------------------
    ddl = (
        data_of(
            req("/api/db/ddl", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "EMP"})
        )
        or {}
    ).get("ddl") or ""
    check("DDL 生成", "CREATE TABLE" in ddl and '"SCOTT"."EMP"' in ddl, ddl[:200])
    check("DDL 含 8 列定义", all(f'"{c}"' in ddl for c in EMP_COLUMNS), ddl[:200])

    # ---- table schema ----------------------------------------------------
    schema = data_of(
        req("/api/db/table-schema", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "EMP"})
    ) or {}
    check("schema 列数=8", len(schema.get("columns") or []) == 8, str(len(schema.get("columns") or [])))
    check("schema 主键 EMPNO", schema.get("primaryKey") == ["EMPNO"], str(schema.get("primaryKey")))
    check("schema 索引非空", bool(schema.get("indexes")), str(schema.get("indexes"))[:200])

    # ---- writes on the disposable probe table ----------------------------
    drop_probe(asset_id)
    created = data_of(
        req(
            "/api/db/query",
            {
                "assetId": asset_id,
                "database": DB,
                "sql": f"CREATE TABLE {PROBE} (id NUMBER(10) PRIMARY KEY, name VARCHAR2(50), score NUMBER(10,2))",
            },
            retry="connect",
        )
    )
    check("创建探针表", created is not None, str(created)[:200])
    probe_detail = data_of(
        req("/api/db/table", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": PROBE, "limit": 10})
    ) or {}
    check("探针表 3 列", len(probe_detail.get("columns") or []) == 3, str(len(probe_detail.get("columns") or [])))
    check("探针表主键 ID", probe_detail.get("primaryKey") == ["ID"], str(probe_detail.get("primaryKey")))

    check(
        "插入行 1",
        bool(row_action(asset_id, "insert", values={"id": 1, "name": "alpha", "score": 1.5})),
        "",
    )
    check(
        "插入行 2",
        bool(row_action(asset_id, "insert", values={"id": 2, "name": "beta", "score": 2.5})),
        "",
    )
    check("探针表行数=2", rows_of(query(f"SELECT count(*) FROM {PROBE}", asset_id)) == [[2]], "")

    updated = row_action(asset_id, "update", keys={"id": 1}, values={"name": "alpha2"})
    check("UPDATE 行", bool(updated), str(updated)[:160])
    check(
        "UPDATE 读回一致",
        rows_of(query(f"SELECT name FROM {PROBE} WHERE id=1", asset_id)) == [["alpha2"]],
        str(rows_of(query(f"SELECT name FROM {PROBE} WHERE id=1", asset_id))),
    )

    deleted = row_action(asset_id, "delete", keys={"id": 2})
    check("DELETE 行", bool(deleted), str(deleted)[:160])
    check("DELETE 读回一致", rows_of(query(f"SELECT count(*) FROM {PROBE}", asset_id)) == [[1]], "")

    probe_ddl = (
        data_of(
            req("/api/db/ddl", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": PROBE})
        )
        or {}
    ).get("ddl") or ""
    check("探针表 DDL", f'"{PROBE}"' in probe_ddl and "CREATE TABLE" in probe_ddl, probe_ddl[:160])

    drop_probe(asset_id)
    left = rows_of(
        query(f"SELECT count(*) FROM all_tables WHERE owner='{SCHEMA}' AND table_name='{PROBE}'", asset_id)
    )
    check("探针表已删除（共享库无残留）", left == [[0]], str(left))

    # ---- realtime / processlist (no v$ grants on this instance) ----------
    realtime = data_of(req("/api/db/realtime", {"assetId": asset_id})) or {}
    info = realtime.get("info") or {}
    check("realtime dialect=oracle", info.get("dialect") == "oracle", str(info)[:200])
    check("realtime 采集时间戳", bool(info.get("collectedAt")), str(info)[:160])
    check("processlist 为列表（允许为空）", isinstance(realtime.get("processes"), list), str(realtime)[:160])
    procs = data_of(req("/api/db/processlist", {"assetId": asset_id})) or {}
    check("processlist 端点可用", isinstance(procs.get("processes"), list), str(procs)[:160])

    # ---- CSV export ------------------------------------------------------
    export = req(
        "/api/db/export",
        {"assetId": asset_id, "database": DB, "sql": "SELECT empno,ename FROM emp ORDER BY empno", "format": "csv"},
        raw=True,
    )
    text = export.decode("utf-8", "replace") if isinstance(export, bytes) else str(export)
    check("CSV 导出含 SMITH", "SMITH" in text, text[:160])
    check("CSV 导出行数 14", len([ln for ln in text.splitlines() if ln.strip()]) >= 15, text[:160])

    # ---- headless UI -----------------------------------------------------
    if not args.skip_ui:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from db_ui_probe import probe

            ui = asyncio.run(probe(ASSET_NAME, "EMP", BASE + "/", min_rows=14))
            tree = str(ui.get("treeFull") or ui.get("tree") or "")
            check("UI 打开资产", ui.get("assetClicked") is True, str(ui.get("tree"))[:160])
            check("UI 对象树含 EMP", "EMP" in tree, tree[:200])
            check("UI 对象树含 DEPT", "DEPT" in tree, tree[:200])
            check("UI 表格渲染 14 行", int(ui.get("rows") or 0) >= 14, f"rows={ui.get('rows')}")
        except Exception as exc:  # noqa: BLE001 - UI probe is best-effort evidence
            check("UI 探针执行", False, f"{type(exc).__name__}: {exc}")

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES:", FAILURES)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
