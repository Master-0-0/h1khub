#!/usr/bin/env python3
"""Smoke/regression test for the replica database features added for the audit.

Covers (per supported dialect): persistent sessions, BEGIN/COMMIT/ROLLBACK,
schema editing (add/modify/column/index/foreign key), .sql import, batch row
operations, blob read/download, exports (xlsx/txt/csv/json/sql) and realtime info.

It only creates and drops tables prefixed with ``zz_probe_``.

Usage: python3 scripts/test_db.py [--base http://127.0.0.1:38110] [--token TOKEN]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []


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
        with urllib.request.urlopen(r, timeout=60) as resp:
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


def sql(session_id: str, statement: str, database=None):
    resp = req("/api/db/session/query", {"sessionId": session_id, "sql": statement, "database": database})
    d = data_of(resp)
    if d is None:
        raise RuntimeError(f"SQL failed: {statement!r} -> {resp}")
    first = (d.get("results") or [{}])[0]
    return first


def scalar(session_id: str, statement: str, database=None):
    first = sql(session_id, statement, database)
    rows = first.get("rows") or []
    return rows[0][0] if rows else None


def find_assets() -> dict:
    resp = req("/api/assets", method="GET")
    return {a["name"]: a["id"] for a in resp["data"]["assets"]}


def test_transactions(label: str, asset_id: str, probe: str) -> tuple[str, str]:
    print(f"\n[{label}] transactions")
    opened = data_of(req("/api/db/session/open", {"assetId": asset_id}))
    check(f"{label}: session open", bool(opened and opened.get("id")), str(opened))
    sid = opened["id"]
    try:
        # clean slate
        sql(sid, f"DROP TABLE IF EXISTS {probe}")
        create = req(
            "/api/db/schema",
            {
                "assetId": asset_id,
                "database": "demo",
                "schema": "public",
                "table": probe,
                "action": "create-table",
                "spec": {
                    "columns": [
                        {"name": "id", "type": "INT", "primary": True, "nullable": False},
                        {"name": "name", "type": "VARCHAR(64)", "nullable": False},
                        {"name": "score", "type": "INT", "nullable": True, "default": "0"},
                    ]
                },
            },
        )
        check(f"{label}: create-table", bool(data_of(create)), str(create))

        # rollback path
        req("/api/db/tx", {"sessionId": sid, "action": "begin"})
        sql(sid, f"INSERT INTO {probe} (id, name, score) VALUES (1, 'alpha', 10)")
        in_tx = scalar(sid, f"SELECT COUNT(*) FROM {probe}")
        status = data_of(req("/api/db/tx", {"sessionId": sid, "action": "rollback"}))
        after = scalar(sid, f"SELECT COUNT(*) FROM {probe}")
        check(f"{label}: rollback hides row", in_tx == 1 and after == 0, f"in_tx={in_tx} after={after}")
        check(f"{label}: tx status after rollback", status and status.get("inTransaction") is False, str(status))

        # commit path
        req("/api/db/tx", {"sessionId": sid, "action": "begin"})
        sql(sid, f"INSERT INTO {probe} (id, name, score) VALUES (1, 'alpha', 10)")
        req("/api/db/tx", {"sessionId": sid, "action": "commit"})
        after = scalar(sid, f"SELECT COUNT(*) FROM {probe}")
        check(f"{label}: commit persists row", after == 1, f"count={after}")

        # session is reused: a second request sees the committed row on the same connection
        persisted = scalar(sid, f"SELECT name FROM {probe} WHERE id=1")
        check(f"{label}: session connection persists", persisted == "alpha", str(persisted))

        # autocommit mode: insert without begin is immediately durable
        sql(sid, f"INSERT INTO {probe} (id, name, score) VALUES (2, 'beta', 20)")
        check(f"{label}: autocommit insert visible", scalar(sid, f"SELECT COUNT(*) FROM {probe}") == 2)

        # ---- schema editing: add column, index, modify, drop
        alter = req(
            "/api/db/schema",
            {
                "assetId": asset_id,
                "database": "demo",
                "schema": "public",
                "table": probe,
                "action": "alter",
                "ops": [
                    {"kind": "add", "column": {"name": "note", "type": "VARCHAR(120)", "nullable": True, "default": "'n/a'", "comment": "备注"}},
                    {"kind": "modify", "column": {"name": "score", "type": "BIGINT", "nullable": True, "default": "0"}},
                    {"kind": "add-index", "index": {"name": f"idx_{probe}_name", "columns": ["name"], "unique": False}},
                    {"kind": "drop", "name": "note"},
                ],
            },
        )
        check(f"{label}: alter table (add/modify/index/drop)", bool(data_of(alter)), str(alter)[:400])
        cols = [r[1] for r in (sql(sid, f"SELECT * FROM {probe} LIMIT 0").get("columns") and [] or [])]
        if not cols:
            if label == "postgresql":
                cols = [
                    r[0]
                    for r in (sql(sid, "SELECT column_name FROM information_schema.columns WHERE table_name='%s' ORDER BY ordinal_position" % probe).get("rows") or [])
                ]
            else:
                cols = [
                    r[0]
                    for r in (sql(sid, "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='demo' AND TABLE_NAME='%s' ORDER BY ORDINAL_POSITION" % probe).get("rows") or [])
                ]
        check(f"{label}: added column then dropped", cols == ["id", "name", "score"], str(cols))

        # ---- batch rows
        batch = req(
            "/api/db/rows",
            {
                "assetId": asset_id,
                "database": "demo",
                "schema": "public",
                "table": probe,
                "action": "update",
                "rows": [{"id": 1}, {"id": 2}],
                "values": {"score": 99},
            },
        )
        check(f"{label}: batch update 2 rows", (data_of(batch) or {}).get("affected") == 2, str(batch))
        check(f"{label}: batch update value", scalar(sid, f"SELECT MIN(score) FROM {probe}") == 99)
        batch_del = req(
            "/api/db/rows",
            {"assetId": asset_id, "database": "demo", "schema": "public", "table": probe, "action": "delete", "rows": [{"id": 2}]},
        )
        check(f"{label}: batch delete", (data_of(batch_del) or {}).get("affected") == 1, str(batch_del))
        check(f"{label}: batch delete count", scalar(sid, f"SELECT COUNT(*) FROM {probe}") == 1)

        # ---- import a .sql script (creates its own probe table)
        import_table = probe + "_imp"
        script = (
            f"DROP TABLE IF EXISTS {import_table};"
            f"CREATE TABLE {import_table} (id INT PRIMARY KEY, label VARCHAR(40));"
            f"INSERT INTO {import_table} VALUES (1,'one'),(2,'two'),(3,'three');"
        )
        imported = req("/api/db/import", {"assetId": asset_id, "database": "demo", "schema": "public", "sql": script, "stopOnError": True})
        imp = data_of(imported)
        check(f"{label}: SQL import", bool(imp) and imp.get("failed") == 0 and imp.get("executed") == 3, str(imported)[:400])
        check(f"{label}: import rows", scalar(sid, f"SELECT COUNT(*) FROM {import_table}") == 3)
        # import error reporting
        bad = req("/api/db/import", {"assetId": asset_id, "database": "demo", "sql": "SELECT 1; SELECT * FROM no_such_table_zz;", "stopOnError": True})
        bad_data = data_of(bad)
        check(f"{label}: import reports failure", bool(bad_data) and bad_data.get("failed") == 1, str(bad)[:300])
        sql(sid, f"DROP TABLE IF EXISTS {import_table}")

        # ---- realtime info
        rt = data_of(req("/api/db/realtime", {"assetId": asset_id}))
        check(f"{label}: realtime info", bool(rt and rt.get("info", {}).get("dialect") == label), str(rt)[:200])
        return sid, probe
    finally:
        pass


def test_exports(label: str, asset_id: str, probe: str) -> None:
    for fmt, magic in (("csv", b"id,name"), ("txt", b"id\tname"), ("json", b"["), ("sql", b"-- H1KHub export")):
        payload = req(
            "/api/db/export",
            {"assetId": asset_id, "database": "demo", "schema": "public", "table": probe, "format": fmt},
            raw=True,
        )
        check(f"{label}: export {fmt}", isinstance(payload, bytes) and magic in payload[:200], str(payload)[:200])
    xlsx = req(
        "/api/db/export",
        {"assetId": asset_id, "database": "demo", "schema": "public", "table": probe, "format": "xlsx"},
        raw=True,
    )
    check(f"{label}: export xlsx is a zip", isinstance(xlsx, bytes) and xlsx[:2] == b"PK", str(xlsx)[:120])


def test_blob(asset_id: str, probe: str) -> None:
    print("\n[mysql] blob")
    sid = data_of(req("/api/db/session/open", {"assetId": asset_id}))["id"]
    try:
        sql(sid, f"DROP TABLE IF EXISTS {probe}_blob")
        sql(sid, f"CREATE TABLE {probe}_blob (id INT PRIMARY KEY, payload LONGBLOB, note VARCHAR(20))")
        sql(sid, f"INSERT INTO {probe}_blob VALUES (1, UNHEX('48656C6C6F20426C6F6221'), 'hello')")
        blob = data_of(
            req("/api/db/blob", {"assetId": asset_id, "table": f"{probe}_blob", "column": "payload", "keys": {"id": 1}})
        )
        check("mysql: blob text preview", bool(blob) and blob.get("text") == "Hello Blob!", str(blob)[:300])
        check("mysql: blob size", bool(blob) and blob.get("size") == 11, str(blob)[:200])
        raw = req("/api/db/blob/download", {"assetId": asset_id, "table": f"{probe}_blob", "column": "payload", "keys": {"id": 1}}, raw=True)
        check("mysql: blob download bytes", raw == b"Hello Blob!", str(raw)[:100])
        sql(sid, f"DROP TABLE IF EXISTS {probe}_blob")
    finally:
        req("/api/db/session/close", {"sessionId": sid})


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()

    assets = find_assets()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    print(f"probe suffix: {suffix}")

    pg_probe = f"zz_probe_pg_{suffix}"
    my_probe = f"zz_probe_my_{suffix}"
    sessions = []
    try:
        sid, _ = test_transactions("postgresql", assets["PostgreSQL demo"], pg_probe)
        sessions.append(sid)
        sid, _ = test_transactions("mysql", assets["MySQL demo"], my_probe)
        sessions.append(sid)
        # regression: the PostgreSQL editor must not call cursor.nextset()
        pg_resp = req(
            "/api/db/query",
            {"assetId": assets["PostgreSQL demo"], "database": "demo", "sql": "select count(*) as n from users"},
        )
        pg_rows = ((data_of(pg_resp) or {}).get("results") or [{}])[0].get("rows")
        check("postgresql: regression count(*)=2", pg_rows == [[2]], str(pg_resp)[:300])

        test_exports("postgresql", assets["PostgreSQL demo"], pg_probe)
        test_exports("mysql", assets["MySQL demo"], my_probe)
        test_blob(assets["MySQL demo"], my_probe)
    finally:
        # cleanup (drop probe tables, close sessions)
        for sid, probe_list in zip(sessions, [pg_probe, my_probe]):
            try:
                for t in (probe_list, probe_list + "_imp"):
                    req("/api/db/session/query", {"sessionId": sid, "sql": f"DROP TABLE IF EXISTS {t}"})
            except Exception:
                pass
        for sid in sessions:
            req("/api/db/session/close", {"sessionId": sid})

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
