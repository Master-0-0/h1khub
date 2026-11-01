#!/usr/bin/env python3
"""Regression test for the SQL Server adapter (pymssql / TDS).

Covers connection test, version, database+schema object tree, table detail,
SQL query, SHOWPLAN_XML explain, generated DDL, schema metadata, row
insert/update/delete with read-back, realtime metrics, processlist, CSV export
and a headless-UI render check.

Only creates/drops ``zz_ss_probe`` inside the ``hexhub_demo`` database.

Usage: python3 scripts/test_sqlserver.py [--base http://127.0.0.1:38110] [--token TOKEN]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []

ASSET_NAME = "SQL Server demo"
DB = "hexhub_demo"
PROBE = "zz_ss_probe"


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST", raw=False):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            detail = json.loads(text)
        except Exception:
            detail = text
        return {"__http_error": exc.code, "detail": detail}


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


CODE_PATH = {
    "class": "SQLServerAdapter",
    "port": 1433,
    "placeholder": "%s",
    "quote": "[users]",
    "aliases": ("mssql", "sql_server"),
    "user": "sa",
    "database": "master",
    "error_token": "SQL Server",
}


def offline_mode() -> int:
    """No lab instance: verify only the adapter code path and say so."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from db_adapter_check import code_path_checks

    print(f"MODE=code-path（未找到资产 {ASSET_NAME}，仅验证 adapter 代码路径）")
    passed, failed, failures = code_path_checks("sqlserver", CODE_PATH)
    print(f"\nPASS={passed} FAIL={failed}")
    if failures:
        print("FAILURES:", failures)
    print("LIMIT: 无真实 SQL Server 实例，库/表/查询/写入均未实测")
    return 0 if failed == 0 else 1


def row_action(asset_id: str, action: str, **extra):
    body = {"assetId": asset_id, "database": DB, "table": PROBE, "action": action, **extra}
    return data_of(req("/api/db/row", body))


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
    print(f"asset {ASSET_NAME} = {asset_id}")

    test = data_of(req("/api/test-connection", {"assetId": asset_id}))
    check("连接测试", bool(test and test.get("ok")), str(test)[:300])
    version = str((test or {}).get("version") or "")
    check("版本来自实例且为 SQL Server", "SQL Server" in version and any(ch.isdigit() for ch in version), version)

    overview = data_of(req("/api/db/overview", {"assetId": asset_id})) or {}
    check("overview dialect", overview.get("dialect") == "sqlserver", str(overview.get("dialect")))
    names = [d.get("name") for d in overview.get("databases") or []]
    check("数据库树含 hexhub_demo", DB in names, str(names)[:200])

    schema_names = [str(s) for s in (overview.get("schemas") or [])]
    check("schema 列表含 dbo", "dbo" in schema_names, str(schema_names)[:160])

    objects = data_of(req("/api/db/objects", {"assetId": asset_id, "database": DB, "schema": "dbo"})) or {}
    tables = [t.get("name") for t in objects.get("tables") or []]
    check("对象树含 users", "users" in tables, str(tables)[:200])
    check("对象树含 orders", "orders" in tables, str(tables)[:200])

    detail = data_of(
        req("/api/db/table", {"assetId": asset_id, "database": DB, "schema": "dbo", "table": "users", "limit": 10})
    ) or {}
    check("表数据 total=2", detail.get("total") == 2, str(detail.get("total")))
    check("主键为 id", detail.get("primaryKey") == ["id"], str(detail.get("primaryKey")))
    col_names = [c.get("name") for c in detail.get("columns") or []]
    check("列元数据 5 列", len(col_names) == 5, str(col_names))
    check("列含 name/email/role", {"name", "email", "role"} <= set(col_names), str(col_names))
    check("可编辑标记", detail.get("editable") is True, str(detail.get("editable")))

    result = query("SELECT count(*) AS n FROM users", asset_id) or {}
    check("聚合查询 count=2", rows_of(result) == [[2]], str(rows_of(result)))
    typed = query("SELECT name, role FROM users ORDER BY id", asset_id) or {}
    check("行数据读回", rows_of(typed) == [["Alice", "admin"], ["Bob", "user"]], str(rows_of(typed)))

    explain = data_of(req("/api/db/explain", {"assetId": asset_id, "database": DB, "sql": "SELECT * FROM users"})) or {}
    check("SHOWPLAN_XML 计划", explain.get("format") == "xml" and "RelOp" in str(explain.get("plan")), str(explain)[:200])

    ddl = (data_of(req("/api/db/ddl", {"assetId": asset_id, "database": DB, "schema": "dbo", "table": "users"})) or {}).get("ddl") or ""
    check("DDL 生成", "CREATE TABLE" in ddl and "[users]" in ddl, ddl[:160])

    schema = data_of(
        req("/api/db/table-schema", {"assetId": asset_id, "database": DB, "schema": "dbo", "table": "users"})
    ) or {}
    check("schema 列数=5", len(schema.get("columns") or []) == 5, str(len(schema.get("columns") or [])))
    check("schema 主键", schema.get("primaryKey") == ["id"], str(schema.get("primaryKey")))

    # ---- writes on a probe table -----------------------------------------
    query(f"IF OBJECT_ID('dbo.{PROBE}') IS NOT NULL DROP TABLE dbo.{PROBE}", asset_id)
    created = query(
        f"CREATE TABLE dbo.{PROBE} (id INT PRIMARY KEY, name NVARCHAR(50), score FLOAT)",
        asset_id,
    )
    check("创建探针表", created is not None, str(created))
    check("插入行 1", bool(row_action(asset_id, "insert", values={"id": 1, "name": "alpha", "score": 1.5})), "")
    check("插入行 2", bool(row_action(asset_id, "insert", values={"id": 2, "name": "beta", "score": 2.5})), "")
    check("探针表行数=2", rows_of(query(f"SELECT count(*) FROM {PROBE}", asset_id)) == [[2]], "")

    updated = row_action(asset_id, "update", keys={"id": 1}, values={"name": "alpha2"})
    check("UPDATE 行", bool(updated), str(updated))
    check(
        "UPDATE 读回一致",
        rows_of(query(f"SELECT name FROM {PROBE} WHERE id=1", asset_id)) == [["alpha2"]],
        "",
    )

    deleted = row_action(asset_id, "delete", keys={"id": 2})
    check("DELETE 行", bool(deleted), str(deleted))
    check("DELETE 读回一致", rows_of(query(f"SELECT count(*) FROM {PROBE}", asset_id)) == [[1]], "")

    realtime = data_of(req("/api/db/realtime", {"assetId": asset_id})) or {}
    info = realtime.get("info") or {}
    check("realtime 指标", info.get("dialect") == "sqlserver" and int(info.get("connections") or 0) >= 1, str(info)[:200])

    procs = data_of(req("/api/db/processlist", {"assetId": asset_id})) or {}
    check("processlist 可用", isinstance(procs.get("processes"), list), str(procs)[:160])

    export = req(
        "/api/db/export",
        {"assetId": asset_id, "database": DB, "sql": "SELECT id,name FROM users ORDER BY id", "format": "csv"},
        raw=True,
    )
    text = export.decode("utf-8", "replace") if isinstance(export, bytes) else ""
    check("CSV 导出含数据", "Alice" in text and "Bob" in text, text[:120])

    query(f"IF OBJECT_ID('dbo.{PROBE}') IS NOT NULL DROP TABLE dbo.{PROBE}", asset_id)

    if not args.skip_ui:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from db_ui_probe import probe

            ui = asyncio.run(probe(ASSET_NAME, "users", BASE + "/"))
            check("UI 打开资产", ui.get("assetClicked") is True, str(ui.get("tree"))[:160])
            check("UI 对象树含 users", "users" in str(ui.get("tree")), str(ui.get("tree"))[:160])
            check("UI 表格渲染行", int(ui.get("rows") or 0) >= 2, f"rows={ui.get('rows')}")
        except Exception as exc:  # noqa: BLE001 - UI probe is best-effort evidence
            check("UI 探针执行", False, f"{type(exc).__name__}: {exc}")

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES:", FAILURES)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
