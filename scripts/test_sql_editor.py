#!/usr/bin/env python3
"""SQL editor cancellation (MySQL + PostgreSQL) and session/plain paths."""
from __future__ import annotations
import argparse, json, os, sys, threading, time, urllib.error, urllib.request
BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]
def load_token():
    home=os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    return json.load(open(os.path.join(home,"runtime.json"),encoding="utf-8"))["token"]
def req(path,body=None,method="POST",timeout=20):
    data=None if body is None else json.dumps(body).encode()
    r=urllib.request.Request(BASE+path,data=data,headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/json"},method=method)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try: return {"__http_error":exc.code,"detail":json.loads(exc.read().decode())}
        except Exception: return {"__http_error":exc.code}
def check(name,cond,detail=""):
    global PASS,FAIL
    if cond: PASS+=1; print(f"  PASS  {name}")
    else: FAIL+=1; FAILURES.append(name); print(f"  FAIL  {name}  {detail}")
def data_of(r): return r.get("data") if isinstance(r,dict) and r.get("ok") else None

def cancel_test(label, asset, sql, session=True):
    result={}
    def worker():
        t0=time.time()
        if session:
            r=req("/api/db/session/query",{"sessionId":result["sid"],"sql":sql},timeout=20)
        else:
            r=req("/api/db/query",{"assetId":asset,"sql":sql},timeout=20)
        result["elapsed"]=time.time()-t0; result["resp"]=r
    if session:
        st=data_of(req("/api/db/session/open",{"assetId":asset})) or {}
        result["sid"]=st.get("id") or (st.get("session") or {}).get("id")
        check(f"sql {label}: session", bool(result["sid"]), str(st)[:200])
    th=threading.Thread(target=worker); th.start(); time.sleep(1.0)
    if session:
        c=req("/api/db/session/cancel",{"sessionId":result["sid"]})
    else:
        c=req("/api/db/cancel",{"assetId":asset})
    check(f"sql {label}: cancel accepted", bool(data_of(c)), str(c)[:200])
    th.join(timeout=15)
    check(f"sql {label}: query interrupted fast", result.get("elapsed",99)<8, f"{result.get('elapsed'):.1f}s")
    if session: req("/api/db/session/close",{"sessionId":result["sid"]})

def main():
    global BASE,TOKEN
    ap=argparse.ArgumentParser(); ap.add_argument("--base",default=BASE); ap.add_argument("--token",default="")
    a=ap.parse_args(); BASE=a.base.rstrip("/"); TOKEN=a.token or load_token()
    assets={x["name"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
    cancel_test("mysql", assets["MySQL demo"]["id"], "SELECT SLEEP(10)")
    cancel_test("pg", assets["PostgreSQL demo"]["id"], "SELECT pg_sleep(10)")
    cancel_test("mysql-plain", assets["MySQL demo"]["id"], "SELECT SLEEP(10)", session=False)
    bad=req("/api/db/cancel",{"assetId":assets["MySQL demo"]["id"]})
    check("sql: cancel without query 400", bad.get("__http_error")==400, str(bad)[:200])
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0
if __name__=="__main__": sys.exit(main())
