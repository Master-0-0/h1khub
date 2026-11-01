#!/usr/bin/env python3
"""View/routine definition and edit tests (MySQL + PostgreSQL)."""
from __future__ import annotations
import argparse, json, os, random, sys, urllib.error, urllib.request
BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]

def load_token():
    home=os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    return json.load(open(os.path.join(home,"runtime.json"),encoding="utf-8"))["token"]

def req(path, body=None, method="POST"):
    data=None if body is None else json.dumps(body).encode()
    r=urllib.request.Request(BASE+path,data=data,headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/json"},method=method)
    try:
        with urllib.request.urlopen(r,timeout=60) as resp: return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try: return {"__http_error":exc.code,"detail":json.loads(exc.read().decode())}
        except Exception: return {"__http_error":exc.code}

def check(name, cond, detail=""):
    global PASS,FAIL
    if cond: PASS+=1; print(f"  PASS  {name}")
    else: FAIL+=1; FAILURES.append(name); print(f"  FAIL  {name}  {detail}")

def data_of(r): return r.get("data") if isinstance(r,dict) and r.get("ok") else None

def q(asset, sql, db="demo"):
    return data_of(req("/api/db/query",{"assetId":asset,"database":db,"sql":sql}))

def run(label, asset, suffix, schema):
    view=f"zz_view_{label}_{suffix}"
    print(f"\n[{label}]")
    try:
        q(asset, f"DROP VIEW IF EXISTS {view}")
        q(asset, f"CREATE VIEW {view} AS SELECT id FROM users")
        objects=data_of(req("/api/db/objects",{"assetId":asset,"database":"demo","schema":schema,"refresh":True}))
        names=[v["name"] for v in (objects or {}).get("views",[])]
        check(f"{label}: view listed", view in names, str(names)[:200])
        definition=data_of(req("/api/db/object-def",{"assetId":asset,"database":"demo","schema":schema,"name":view,"kind":"view"}))
        check(f"{label}: definition loaded", bool(definition and definition.get("definition")), str(definition)[:200])
        new_sql=f"CREATE VIEW {view} AS SELECT id, name FROM users"
        saved=data_of(req("/api/db/object-save",{"assetId":asset,"database":"demo","schema":schema,"name":view,"kind":"view","sql":new_sql}))
        check(f"{label}: definition saved", bool(saved), str(saved)[:300])
        cols=(q(asset, f"SELECT * FROM {view} LIMIT 1") or {}).get("results",[{}])[0].get("columns")
        check(f"{label}: view updated", cols==["id","name"], str(cols))
        dropped=data_of(req("/api/db/object-drop",{"assetId":asset,"database":"demo","schema":schema,"name":view,"kind":"view"}))
        check(f"{label}: view dropped", bool(dropped), str(dropped)[:200])
        check(f"{label}: view gone", view not in [v["name"] for v in (data_of(req("/api/db/objects",{"assetId":asset,"database":"demo","schema":schema,"refresh":True})) or {}).get("views",[])])
        if label == "mysql":
            proc = f"zz_proc_{label}_{suffix}"
            q(asset, f"DROP PROCEDURE IF EXISTS {proc}")
            q(asset, f"CREATE PROCEDURE {proc}() BEGIN SELECT 43 AS n; END")
            rdef = data_of(req("/api/db/object-def", {"assetId": asset, "database": "demo", "name": proc, "kind": "routine", "routineType": "PROCEDURE"}))
            check(f"{label}: routine definition", bool(rdef and "CREATE" in (rdef.get("definition") or "").upper()), str(rdef)[:200])
            rsaved = data_of(req("/api/db/object-save", {"assetId": asset, "database": "demo", "name": proc, "kind": "routine", "routineType": "PROCEDURE", "sql": f"CREATE PROCEDURE {proc}() BEGIN SELECT 44 AS n; END"}))
            check(f"{label}: routine saved", bool(rsaved), str(rsaved)[:300])
            rows = (q(asset, f"CALL {proc}()") or {}).get("results", [{}])
            values = rows[0].get("rows") if rows else None
            check(f"{label}: routine updated", values == [[44]], str(values))
            rdropped = data_of(req("/api/db/object-drop", {"assetId": asset, "database": "demo", "name": proc, "kind": "routine", "routineType": "PROCEDURE"}))
            check(f"{label}: routine dropped", bool(rdropped), str(rdropped)[:200])
            q(asset, f"DROP PROCEDURE IF EXISTS {proc}")
    finally:
        try: q(asset, f"DROP VIEW IF EXISTS {view}")
        except Exception: pass
        if label == "mysql":
            try: q(asset, f"DROP PROCEDURE IF EXISTS zz_proc_mysql_{suffix}")
            except Exception: pass

def main():
    global BASE,TOKEN
    ap=argparse.ArgumentParser(); ap.add_argument("--base",default=BASE); ap.add_argument("--token",default="")
    a=ap.parse_args(); BASE=a.base.rstrip("/"); TOKEN=a.token or load_token()
    suffix="%04x"%random.randint(0,0xFFFF)
    assets={x["name"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
    run("mysql", assets["MySQL demo"]["id"], suffix, None)
    run("pg", assets["PostgreSQL demo"]["id"], suffix, "public")
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0

if __name__=="__main__": sys.exit(main())
