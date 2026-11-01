#!/usr/bin/env python3
"""Background task queue tests (transfer + SQL import)."""
from __future__ import annotations
import argparse, json, os, random, sys, time, urllib.error, urllib.request

BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]

def load_token():
    home=os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    return json.load(open(os.path.join(home,"runtime.json"),encoding="utf-8"))["token"]

def req(path, body=None, method="POST", timeout=60):
    data=None if body is None else json.dumps(body).encode()
    r=urllib.request.Request(BASE+path,data=data,headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/json"},method=method)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try: return {"__http_error":exc.code,"detail":json.loads(exc.read().decode())}
        except Exception: return {"__http_error":exc.code}

def check(name, cond, detail=""):
    global PASS,FAIL
    if cond: PASS+=1; print(f"  PASS  {name}")
    else: FAIL+=1; FAILURES.append(name); print(f"  FAIL  {name}  {detail}")

def data_of(resp): return resp.get("data") if isinstance(resp,dict) and resp.get("ok") else None

def wait_task(task_id, timeout=40):
    end=time.time()+timeout
    while time.time()<end:
        t=data_of(req(f"/api/tasks/{task_id}",method="GET"))
        if t and t["task"]["status"] in ("success","failed","cancelled"): return t["task"]
        time.sleep(0.25)
    return None

def ssh_exec(asset_id, cmd):
    r=req("/api/batch/exec",{"assetIds":[asset_id],"command":cmd})
    return ((data_of(r) or {}).get("results") or [{}])[0]

def main():
    global BASE,TOKEN
    ap=argparse.ArgumentParser(); ap.add_argument("--base",default=BASE); ap.add_argument("--token",default="")
    a=ap.parse_args(); BASE=a.base.rstrip("/"); TOKEN=a.token or load_token()
    suffix="%04x"%random.randint(0,0xFFFF)
    assets={x["name"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
    ssh=assets["SSH hexlab"]["id"]; jump=assets["SSH 跳板演示"]["id"]; mysql=assets["MySQL demo"]["id"]
    src=f"/tmp/h1khub-task-src-{suffix}"; dst=f"/tmp/h1khub-task-dst-{suffix}"; table=f"zz_task_{suffix}"
    try:
        print("[background transfer]")
        ssh_exec(ssh,f"rm -rf {src} {dst}; mkdir -p {src}; printf 'task-a' > {src}/a.txt; printf 'task-b' > {src}/b.txt")
        r=data_of(req("/api/transfer",{"fromAssetId":ssh,"fromPath":src,"toAssetId":jump,"toPath":dst,"background":True}))
        check("tasks: transfer submitted", bool(r and r.get("task",{}).get("id")), str(r)[:300])
        task=wait_task(r["task"]["id"]) if r else None
        check("tasks: transfer success", bool(task and task["status"]=="success"), str(task)[:400])
        check("tasks: transfer result", bool(task and task.get("result",{}).get("files")==2), str(task)[:300] if task else "")
        check("tasks: target bytes", (ssh_exec(jump,f"cat {dst}/a.txt").get("stdout") or "").strip()=="task-a")

        print("[background import]")
        script=f"DROP TABLE IF EXISTS {table}; CREATE TABLE {table} (id INT PRIMARY KEY, v VARCHAR(20)); INSERT INTO {table} VALUES (1,'ok'),(2,'ok');"
        r=data_of(req("/api/db/import",{"assetId":mysql,"database":"demo","sql":script,"background":True,"filename":"task.sql"}))
        check("tasks: import submitted", bool(r and r.get("task",{}).get("id")), str(r)[:300])
        task=wait_task(r["task"]["id"]) if r else None
        check("tasks: import success", bool(task and task["status"]=="success"), str(task)[:400])
        q=data_of(req("/api/db/query",{"assetId":mysql,"database":"demo","sql":f"SELECT COUNT(*) FROM {table}"}))
        rows=((q or {}).get("results") or [{}])[0].get("rows")
        check("tasks: import rows", rows==[[2]], str(q)[:300])

        listing=data_of(req("/api/tasks",method="GET"))["tasks"]
        check("tasks: list contains both", len([t for t in listing if t["kind"] in ("transfer","db-import")])>=2)
        check("tasks: missing task 404", req("/api/tasks/task_nope",method="GET").get("__http_error")==404)
        check("tasks: cancel unknown false", (data_of(req("/api/tasks/task_nope/cancel",{})) or {}).get("cancelled") is False)
    finally:
        ssh_exec(ssh,f"rm -rf {src}"); ssh_exec(jump,f"rm -rf {dst}")
        try: req("/api/db/query",{"assetId":mysql,"database":"demo","sql":f"DROP TABLE IF EXISTS {table}"})
        except Exception: pass
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0

if __name__=="__main__": sys.exit(main())
