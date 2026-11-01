#!/usr/bin/env python3
"""Schema/data synchronization tests (MySQL + PostgreSQL).

Creates only temporary ``zz_sync_*`` tables and drops them afterwards.
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


def req(path: str, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return {"__http_error": exc.code, "detail": json.loads(text)}
        except Exception:
            return {"__http_error": exc.code, "detail": text}


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


def sql(asset_id, statement, database="demo"):
    resp = req("/api/db/query", {"assetId": asset_id, "database": database, "sql": statement})
    if data_of(resp) is None:
        raise RuntimeError(f"SQL failed: {statement[:80]} -> {str(resp)[:300]}")
    return data_of(resp)


def scalar(asset_id, statement, database="demo"):
    res = sql(asset_id, statement, database)
    rows = (res.get("results") or [{}])[0].get("rows") or []
    return rows[0][0] if rows else None


def schema(asset_id, table, database="demo", schema_name=None):
    return data_of(req("/api/db/table-schema", {"assetId": asset_id, "database": database, "schema": schema_name, "table": table})) or {}


def run_case(label: str, asset_id: str, suffix: str, dialect: str) -> None:
    print(f"\n[{label}]")
    src = f"zz_sync_{label}_src_{suffix}"
    dst = f"zz_sync_{label}_dst_{suffix}"
    schema_name = "public" if dialect == "postgresql" else None
    try:
        if dialect == "mysql":
            sql(asset_id, f"DROP TABLE IF EXISTS {src}")
            sql(asset_id, f"DROP TABLE IF EXISTS {dst}")
            sql(asset_id, f"CREATE TABLE {src} (id INT PRIMARY KEY, name VARCHAR(50) NOT NULL, qty INT DEFAULT 0, KEY idx_qty (qty))")
            sql(asset_id, f"CREATE TABLE {dst} (id INT PRIMARY KEY, name VARCHAR(100) NULL)")
        else:
            sql(asset_id, f"DROP TABLE IF EXISTS {src}")
            sql(asset_id, f"DROP TABLE IF EXISTS {dst}")
            sql(asset_id, f"CREATE TABLE {src} (id BIGINT PRIMARY KEY, name TEXT NOT NULL, qty INTEGER DEFAULT 0)")
            sql(asset_id, f"CREATE TABLE {dst} (id INTEGER PRIMARY KEY, name VARCHAR(50))")

        source = {"assetId": asset_id, "database": "demo", "schema": schema_name, "table": src}
        target = {"assetId": asset_id, "database": "demo", "schema": schema_name, "table": dst}

        diff = data_of(req("/api/db/sync/diff", {"source": source, "target": target}))
        kinds = {(c.get("kind"), c.get("column") or c.get("index")) for c in (diff or {}).get("changes", [])}
        check(f"{label}: diff detects added column", ("add-column", "qty") in kinds, str(kinds))
        check(f"{label}: diff detects modified name", ("modify-column", "name") in kinds, str(kinds))
        if dialect == "mysql":
            check(f"{label}: diff detects added index", ("add-index", "idx_qty") in kinds, str(kinds))
        check(f"{label}: statements generated", bool((diff or {}).get("statements")), str(diff)[:300])

        applied = data_of(req("/api/db/sync/apply", {"source": source, "target": target}))
        check(f"{label}: apply executed", bool(applied and applied.get("executed")), str(applied)[:300])
        dst_schema = schema(asset_id, dst, "demo", schema_name)
        names = [c["name"] for c in dst_schema.get("columns") or []]
        check(f"{label}: target has qty", "qty" in names, str(names))
        if dialect == "mysql":
            idx_names = [i["name"] for i in dst_schema.get("indexes") or []]
            check(f"{label}: target has index", "idx_qty" in idx_names, str(idx_names))

        # data sync
        if dialect == "mysql":
            sql(asset_id, f"INSERT INTO {src} (id, name, qty) VALUES (1,'a',10),(2,'b',20),(3,'c',30)")
        else:
            sql(asset_id, f"INSERT INTO {src} (id, name, qty) VALUES (1,'a',10),(2,'b',20),(3,'c',30)")
        preview = data_of(req("/api/db/sync/data/preview", {"source": source, "target": target}))
        check(f"{label}: preview source rows", (preview or {}).get("sourceRows") == 3, str(preview)[:200])
        check(f"{label}: preview common columns", "id" in ((preview or {}).get("columns") or []), str(preview)[:200])

        copied = data_of(req("/api/db/sync/data", {"source": source, "target": target, "mode": "append", "limit": 100}))
        check(f"{label}: data append", (copied or {}).get("inserted") == 3, str(copied)[:300])
        check(f"{label}: target row count", scalar(asset_id, f"SELECT COUNT(*) FROM {dst}") == 3)
        replaced = data_of(req("/api/db/sync/data", {"source": source, "target": target, "mode": "replace", "limit": 100}))
        check(f"{label}: data replace", (replaced or {}).get("inserted") == 3 and scalar(asset_id, f"SELECT COUNT(*) FROM {dst}") == 3, str(replaced)[:300])
    finally:
        for table in (src, dst):
            try:
                sql(asset_id, f"DROP TABLE IF EXISTS {table}")
            except Exception:
                pass


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    assets = {a["name"]: a for a in data_of(req("/api/assets", method="GET"))["assets"]}
    run_case("mysql", assets["MySQL demo"]["id"], suffix, "mysql")
    run_case("pg", assets["PostgreSQL demo"]["id"], suffix, "postgresql")
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
