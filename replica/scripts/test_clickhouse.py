#!/usr/bin/env python3
"""Regression test for the ClickHouse adapter (native TCP, clickhouse-driver).

Covers connection test, database/object tree, table detail, SQL query, EXPLAIN,
SHOW CREATE TABLE, schema metadata, row writes and asynchronous mutations with
mutations_sync, realtime metrics, processlist and export.

Only creates/drops tables prefixed with ``zz_ch_probe`` in the ``demo`` DB.

Usage: python3 scripts/test_clickhouse.py [--base http://127.0.0.1:38110] [--token TOKEN]
"""
from __future__ import annotations

import argparse
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

ASSET_NAME = "ClickHouse demo"
DB = "demo"
PROBE = "zz_ch_probe"


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
        with urllib.request.urlopen(r, timeout=90) as resp:
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


def find_asset() -> str:
    data = data_of(req("/api/assets", method="GET")) or {}
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            return asset["id"]
    raise SystemExit(f"未找到资产 {ASSET_NAME}")


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    asset_id = find_asset()
    print(f"asset {ASSET_NAME} = {asset_id}")

    test = data_of(req("/api/test-connection", {"assetId": asset_id}))
    check("连接测试", bool(test and test.get("ok")), str(test))
    check("版本为 ClickHouse", "ClickHouse" in str((test or {}).get("version")), str((test or {}).get("version")))

    overview = data_of(req("/api/db/overview", {"assetId": asset_id})) or {}
    check("overview dialect", overview.get("dialect") == "clickhouse", str(overview.get("dialect")))
    names = [d.get("name") for d in overview.get("databases") or []]
    check("database 树含 demo", "demo" in names, str(names))

    objects = data_of(req("/api/db/objects", {"assetId": asset_id, "database": DB})) or {}
    tables = [t.get("name") for t in objects.get("tables") or []]
    check("对象树含 users", "users" in tables, str(tables))
    check("对象树含 events", "events" in tables, str(tables))

    detail = data_of(req("/api/db/table", {"assetId": asset_id, "database": DB, "table": "users", "limit": 10})) or {}
    check("表数据行数=2", detail.get("total") == 2, str(detail.get("total")))
    check("主键为 id", detail.get("primaryKey") == ["id"], str(detail.get("primaryKey")))
    check("列元数据含 name", any(c.get("name") == "name" for c in detail.get("columns") or []), "")

    result = query("SELECT count(*) AS n FROM users", asset_id) or {}
    rows = (result.get("results") or [{}])[0].get("rows")
    check("聚合查询 count=2", rows == [[2]], str(rows))

    explain = data_of(req("/api/db/explain", {"assetId": asset_id, "database": DB, "sql": "SELECT * FROM users"})) or {}
    check("EXPLAIN 返回文本计划", explain.get("format") == "text" and "ReadFromMergeTree" in str(explain.get("plan")), str(explain)[:200])

    ddl = (data_of(req("/api/db/ddl", {"assetId": asset_id, "database": DB, "table": "users"})) or {}).get("ddl") or ""
    check("SHOW CREATE TABLE", "CREATE TABLE" in ddl and "MergeTree" in ddl, ddl[:120])

    schema = data_of(req("/api/db/table-schema", {"assetId": asset_id, "database": DB, "table": "users"})) or {}
    check("schema 列数=5", len(schema.get("columns") or []) == 5, str(len(schema.get("columns") or [])))

    # ---- row writes on a probe table -------------------------------------
    query(f"DROP TABLE IF EXISTS {PROBE}", asset_id)
    create = query(
        f"CREATE TABLE {PROBE} (id UInt32, name String, score Float64) ENGINE=MergeTree ORDER BY id",
        asset_id,
    )
    check("创建探针表", create is not None, str(create))
    ins = data_of(
        req(
            "/api/db/row",
            {
                "assetId": asset_id,
                "database": DB,
                "table": PROBE,
                "action": "insert",
                "values": {"id": 1, "name": "alpha", "score": 1.5},
            },
        )
    )
    check("插入行", bool(ins), str(ins))
    ins2 = data_of(
        req(
            "/api/db/row",
            {
                "assetId": asset_id,
                "database": DB,
                "table": PROBE,
                "action": "insert",
                "values": {"id": 2, "name": "beta", "score": 2.5},
            },
        )
    )
    check("插入第二行", bool(ins2), str(ins2))
    rows = (query(f"SELECT count(*) FROM {PROBE}", asset_id) or {}).get("results", [{}])[0].get("rows")
    check("探针表行数=2", rows == [[2]], str(rows))

    upd = data_of(
        req(
            "/api/db/row",
            {
                "assetId": asset_id,
                "database": DB,
                "table": PROBE,
                "action": "update",
                "keys": {"id": 1},
                "values": {"name": "alpha2"},
            },
        )
    )
    check("UPDATE mutation", bool(upd), str(upd))
    rows = (query(f"SELECT name FROM {PROBE} WHERE id=1", asset_id) or {}).get("results", [{}])[0].get("rows")
    check("UPDATE 生效", rows == [["alpha2"]], str(rows))

    dele = data_of(
        req(
            "/api/db/row",
            {
                "assetId": asset_id,
                "database": DB,
                "table": PROBE,
                "action": "delete",
                "keys": {"id": 2},
            },
        )
    )
    check("DELETE mutation", bool(dele), str(dele))
    rows = (query(f"SELECT count(*) FROM {PROBE}", asset_id) or {}).get("results", [{}])[0].get("rows")
    check("DELETE 生效", rows == [[1]], str(rows))

    realtime = data_of(req("/api/db/realtime", {"assetId": asset_id})) or {}
    info = realtime.get("info") or {}
    check("realtime 指标", int(info.get("uptime") or 0) >= 0 and "queries" in info, str(info)[:160])

    procs = data_of(req("/api/db/processlist", {"assetId": asset_id})) or {}
    check("processlist 可用", isinstance(procs.get("processes"), list), str(procs)[:120])

    export = req("/api/db/export", {"assetId": asset_id, "database": DB, "sql": "SELECT id,name FROM users ORDER BY id", "format": "csv"}, raw=True)
    text = export.decode("utf-8", "replace") if isinstance(export, bytes) else ""
    check("CSV 导出含数据", "Alice" in text and "Bob" in text, text[:120])

    query(f"DROP TABLE IF EXISTS {PROBE}", asset_id)
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES:", FAILURES)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
