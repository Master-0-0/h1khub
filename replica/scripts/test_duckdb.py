#!/usr/bin/env python3
"""Regression test for the DuckDB adapter (embedded analytics database).

Covers connection test, catalog/object tree, table detail, SQL query, EXPLAIN,
DDL, session transactions (rollback/commit), row writes, exports, and ATTACH of
a second database file (including the bundled sqlite_scanner).

Only creates/drops tables prefixed with ``zz_duck_probe``.

Usage: python3 scripts/test_duckdb.py [--base http://127.0.0.1:38110] [--token TOKEN]
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

ASSET_NAME = "DuckDB demo"
PROBE = "zz_duck_probe"
DB = "analytics"
SCHEMA = "main"


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
            if raw:
                return payload
            return json.loads(payload)
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


def session_sql(session_id: str, statement: str):
    resp = req("/api/db/session/query", {"sessionId": session_id, "sql": statement, "database": DB})
    d = data_of(resp)
    if d is None:
        raise RuntimeError(f"SQL failed: {statement!r} -> {resp}")
    return (d.get("results") or [{}])[0]


def scalar(session_id: str, statement: str):
    rows = session_sql(session_id, statement).get("rows") or []
    return rows[0][0] if rows else None


def find_asset() -> str | None:
    resp = req("/api/assets", method="GET")
    for asset in (data_of(resp) or {}).get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return asset["id"]
    return None


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    print(f"[duckdb] {BASE}")
    asset_id = find_asset()
    check("asset present", bool(asset_id), ASSET_NAME)
    if not asset_id:
        print(f"PASS={PASS} FAIL={FAIL}")
        return 1

    print("\n[duckdb] connection & catalog")
    test = data_of(req("/api/test-connection", {"assetId": asset_id, "typeName": "DB", "config": {}}))
    check("test-connection", bool(test) and test.get("ok"), str(test)[:300])
    overview = data_of(req("/api/db/overview", {"assetId": asset_id, "refresh": True}))
    check("overview dialect", bool(overview) and overview.get("dialect") == "duckdb", str(overview)[:200])
    check("overview version", bool(overview) and "DuckDB" in (overview.get("version") or ""), str(overview)[:200])
    names = [d.get("name") for d in (overview or {}).get("databases", [])]
    check("overview databases", DB in names, str(names))
    check("overview schemas", (overview or {}).get("schemas") == [SCHEMA], str(overview)[:200])

    objects = data_of(req("/api/db/objects", {"assetId": asset_id, "database": DB, "schema": SCHEMA}))
    table_names = [t["name"] for t in (objects or {}).get("tables", [])]
    view_names = [v["name"] for v in (objects or {}).get("views", [])]
    check("objects tables", {"events", "dim_users"}.issubset(set(table_names)), str(table_names))
    check("objects views", "big_purchases" in view_names, str(view_names))

    detail = data_of(req("/api/db/table", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "events"}))
    d = detail or {}
    cols = [c["name"] for c in d.get("columns", [])]
    check("table columns", cols == ["id", "name", "ts", "amount"], str(cols))
    check("table primary key", d.get("primaryKey") == ["id"], str(d.get("primaryKey")))
    check("table rows", len(d.get("rows") or []) == 3, str(d.get("rows"))[:200])

    query = data_of(
        req(
            "/api/db/query",
            {
                "assetId": asset_id,
                "database": DB,
                "sql": "SELECT name, SUM(amount) AS total FROM events GROUP BY 1 ORDER BY 2 DESC",
            },
        )
    )
    q = (query or {}).get("results", [{}])[0]
    check("query aggregation", q.get("rows") == [["purchase", "49.90"], ["signup", "0.00"], ["refund", "-9.90"]], str(q)[:300])
    check("query column types", bool(q.get("columnTypes")) and "DECIMAL" in q["columnTypes"][1], str(q.get("columnTypes")))

    explain = data_of(req("/api/db/explain", {"assetId": asset_id, "database": DB, "sql": "SELECT * FROM events WHERE id = 1"}))
    check("explain plan", bool(explain) and "Scan" in (explain.get("plan") or ""), str(explain)[:200])

    ddl = data_of(req("/api/db/ddl", {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "events"}))
    check("ddl", "CREATE TABLE" in (ddl.get("ddl") or ""), str(ddl)[:200])

    print("\n[duckdb] transactions & writes")
    opened = data_of(req("/api/db/session/open", {"assetId": asset_id, "database": DB}))
    check("session open", bool(opened and opened.get("id")), str(opened)[:200])
    sid = (opened or {}).get("id")
    try:
        session_sql(sid, f"DROP TABLE IF EXISTS {PROBE}")
        session_sql(sid, f"CREATE TABLE {PROBE} (id INTEGER PRIMARY KEY, name VARCHAR, score INTEGER)")
        check("create probe table", scalar(sid, f"SELECT COUNT(*) FROM {PROBE}") == 0)

        req("/api/db/tx", {"sessionId": sid, "action": "begin"})
        session_sql(sid, f"INSERT INTO {PROBE} VALUES (1, 'alpha', 10)")
        inside = scalar(sid, f"SELECT COUNT(*) FROM {PROBE}")
        status = data_of(req("/api/db/tx", {"sessionId": sid, "action": "rollback"}))
        after = scalar(sid, f"SELECT COUNT(*) FROM {PROBE}")
        check("rollback hides row", inside == 1 and after == 0, f"inside={inside} after={after}")
        check("tx status after rollback", bool(status) and status.get("inTransaction") is False, str(status))

        req("/api/db/tx", {"sessionId": sid, "action": "begin"})
        session_sql(sid, f"INSERT INTO {PROBE} VALUES (1, 'alpha', 10)")
        req("/api/db/tx", {"sessionId": sid, "action": "commit"})
        check("commit persists row", scalar(sid, f"SELECT COUNT(*) FROM {PROBE}") == 1)
        check("session connection persists", scalar(sid, f"SELECT name FROM {PROBE} WHERE id = 1") == "alpha")

        session_sql(sid, f"INSERT INTO {PROBE} VALUES (2, 'beta', 20)")
        check("autocommit insert visible", scalar(sid, f"SELECT COUNT(*) FROM {PROBE}") == 2)

        upd = data_of(
            req(
                "/api/db/row",
                {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": PROBE, "action": "update",
                 "keys": {"id": 2}, "values": {"score": 25}},
            )
        )
        check("update row", scalar(sid, f"SELECT score FROM {PROBE} WHERE id = 2") == 25, str(upd)[:200])

        ins = data_of(
            req(
                "/api/db/row",
                {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": PROBE, "action": "insert",
                 "values": {"id": 3, "name": "gamma", "score": 30}},
            )
        )
        check("insert row", scalar(sid, f"SELECT COUNT(*) FROM {PROBE}") == 3, str(ins)[:200])

        dele = data_of(
            req(
                "/api/db/row",
                {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": PROBE, "action": "delete",
                 "keys": {"id": 3}},
            )
        )
        check("delete row", scalar(sid, f"SELECT COUNT(*) FROM {PROBE}") == 2, str(dele)[:200])
    finally:
        try:
            session_sql(sid, f"DROP TABLE IF EXISTS {PROBE}")
        except Exception:
            pass
        req("/api/db/session/close", {"sessionId": sid})

    print("\n[duckdb] exports")
    for fmt, magic in (("csv", b"id,name"), ("json", b"["), ("sql", b"-- H1KHub export")):
        payload = req(
            "/api/db/export",
            {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "events", "format": fmt},
            raw=True,
        )
        check(f"export {fmt}", isinstance(payload, bytes) and magic in payload[:200], str(payload)[:160])
    xlsx = req(
        "/api/db/export",
        {"assetId": asset_id, "database": DB, "schema": SCHEMA, "table": "events", "format": "xlsx"},
        raw=True,
    )
    check("export xlsx", isinstance(xlsx, bytes) and xlsx[:2] == b"PK", str(xlsx)[:120])

    print("\n[duckdb] attach database file")
    sqlite_path = os.environ.get("H1KHUB_DEMO_SQLITE", "/home/kali/hexhub-lab/demo.db")
    attached = data_of(req("/api/db/attach", {"assetId": asset_id, "path": sqlite_path, "alias": "extra", "readOnly": True}))
    check("attach returns alias", bool(attached) and any(a.get("alias") == "extra" for a in attached.get("attached", [])), str(attached)[:300])
    dbs = [d.get("name") for d in (attached or {}).get("databases", [])]
    check("attach visible in catalogs", "extra" in dbs, str(dbs))
    extra_objs = data_of(req("/api/db/objects", {"assetId": asset_id, "database": "extra", "schema": SCHEMA}))
    extra_tables = [t["name"] for t in (extra_objs or {}).get("tables", [])]
    check("attached tables listed", {"users", "products"}.issubset(set(extra_tables)), str(extra_tables))
    cross = data_of(req("/api/db/query", {"assetId": asset_id, "sql": "SELECT COUNT(*) AS n FROM extra.main.users"}))
    n = ((cross or {}).get("results") or [{}])[0].get("rows", [[None]])[0][0]
    check("cross-database query", n == 2, str(cross)[:200])

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES: " + ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
