#!/usr/bin/env python3
"""Regression test for the MongoDB adapter (document store).

Covers connection test, database/collection tree, collection data, document
queries (JSON command + shell subset), count, explain, createCollection hint,
schema inference, insert/update/delete, realtime metrics and CSV export.

Only touches the ``zz_mongo_probe`` collection in the ``demo`` database.

Usage: python3 scripts/test_mongodb.py [--base http://127.0.0.1:38110] [--token TOKEN]
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

ASSET_NAME = "MongoDB demo"
DB = "demo"
PROBE = "zz_mongo_probe"


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


def run_cmd(cmd: str, asset_id: str):
    return data_of(req("/api/db/query", {"assetId": asset_id, "database": DB, "sql": cmd}))


def first_rows(resp):
    return ((resp or {}).get("results") or [{}])[0]


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
    check("版本为 MongoDB", "MongoDB" in str((test or {}).get("version")), str((test or {}).get("version")))

    overview = data_of(req("/api/db/overview", {"assetId": asset_id})) or {}
    check("overview dialect", overview.get("dialect") == "mongodb", str(overview.get("dialect")))
    check("database 树含 demo", "demo" in [d.get("name") for d in overview.get("databases") or []], str(overview.get("databases")))

    objects = data_of(req("/api/db/objects", {"assetId": asset_id, "database": DB})) or {}
    tables = [t.get("name") for t in objects.get("tables") or []]
    check("集合树含 users", "users" in tables, str(tables))
    check("集合树含 orders", "orders" in tables, str(tables))

    detail = data_of(req("/api/db/table", {"assetId": asset_id, "database": DB, "table": "users", "limit": 10})) or {}
    check("集合文档数=2", detail.get("total") == 2, str(detail.get("total")))
    check("主键为 _id", detail.get("primaryKey") == ["_id"], str(detail.get("primaryKey")))
    check("字段推断含 role", any(c.get("name") == "role" for c in detail.get("columns") or []), "")

    found = first_rows(run_cmd('db.users.find({"role":"admin"})', asset_id))
    check("shell 查询命中 Alice", found.get("rowCount") == 1 and found.get("rows") and found["rows"][0][1] == "Alice", str(found)[:200])

    counted = first_rows(run_cmd('{"collection":"users","op":"count"}', asset_id))
    check("JSON count=2", counted.get("rows") == [[2]], str(counted.get("rows")))

    agg = first_rows(run_cmd('{"collection":"orders","op":"aggregate","pipeline":[{"$group":{"_id":null,"total":{"$sum":"$qty"}}}]}', asset_id))
    check("aggregate 求和=3", agg.get("rows") == [[None, 3]] or agg.get("rows") == [[3]], str(agg.get("rows")))

    explain = data_of(req("/api/db/explain", {"assetId": asset_id, "database": DB, "sql": "db.users.find({})"})) or {}
    check("explain 返回 JSON 计划", explain.get("format") == "json" and "queryPlanner" in str(explain.get("plan")), str(explain)[:160])

    ddl = (data_of(req("/api/db/ddl", {"assetId": asset_id, "database": DB, "table": "users"})) or {}).get("ddl") or ""
    check("createCollection 提示", "createCollection" in ddl, ddl[:120])

    schema = data_of(req("/api/db/table-schema", {"assetId": asset_id, "database": DB, "table": "users"})) or {}
    check("schema 含 _id", any(c.get("name") == "_id" for c in schema.get("columns") or []), str(schema.get("columns")))

    # ---- document writes on a probe collection ---------------------------
    run_cmd(f'{{"collection":"{PROBE}","op":"deleteMany","filter":{{}}}}', asset_id)
    ins = first_rows(run_cmd(f'{{"collection":"{PROBE}","op":"insertOne","document":{{"name":"gamma","n":1}}}}', asset_id))
    check("insertOne", ins.get("rowCount") == 1, str(ins)[:160])
    cnt = first_rows(run_cmd(f'{{"collection":"{PROBE}","op":"count"}}', asset_id))
    check("探针集合数=1", cnt.get("rows") == [[1]], str(cnt.get("rows")))

    upd = first_rows(run_cmd(f'{{"collection":"{PROBE}","op":"updateMany","filter":{{"name":"gamma"}},"update":{{"n":2}}}}', asset_id))
    check("updateMany matched=1", upd.get("rows") == [[1, 1]], str(upd.get("rows")))
    got = first_rows(run_cmd(f'db.{PROBE}.find({{"name":"gamma"}})', asset_id))
    values = got.get("rows") or [[]]
    check("更新值生效", values and values[0][-1] == 2, str(values))

    dele = first_rows(run_cmd(f'{{"collection":"{PROBE}","op":"deleteMany","filter":{{"name":"gamma"}}}}', asset_id))
    check("deleteMany=1", dele.get("affectedRows") == 1, str(dele)[:160])
    cnt = first_rows(run_cmd(f'{{"collection":"{PROBE}","op":"count"}}', asset_id))
    check("探针集合清空", cnt.get("rows") == [[0]], str(cnt.get("rows")))
    dropped = first_rows(run_cmd(f'{{"collection":"{PROBE}","op":"drop"}}', asset_id))
    check("删除探针集合", dropped.get("affectedRows") == 1, str(dropped)[:160])

    realtime = data_of(req("/api/db/realtime", {"assetId": asset_id})) or {}
    info = realtime.get("info") or {}
    check("realtime 连接数", "connections" in info and "uptime" in info, str(info)[:160])

    export = req("/api/db/export", {"assetId": asset_id, "database": DB, "sql": 'db.users.find({"role":"admin"})', "format": "csv"}, raw=True)
    text = export.decode("utf-8", "replace") if isinstance(export, bytes) else ""
    check("CSV 导出含 Alice", "Alice" in text, text[:120])

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILURES:", FAILURES)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
