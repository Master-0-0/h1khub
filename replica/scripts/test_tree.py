#!/usr/bin/env python3
"""Asset alias and drag-order move tests."""
from __future__ import annotations
import argparse, json, os, random, sys, urllib.error, urllib.request
BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]
def load_token():
    home=os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    return json.load(open(os.path.join(home,"runtime.json"),encoding="utf-8"))["token"]
def req(path,body=None,method="POST"):
    data=None if body is None else json.dumps(body).encode()
    r=urllib.request.Request(BASE+path,data=data,headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/json"},method=method)
    try:
        with urllib.request.urlopen(r,timeout=30) as resp: return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try: return {"__http_error":exc.code,"detail":json.loads(exc.read().decode())}
        except Exception: return {"__http_error":exc.code}
def check(name,cond,detail=""):
    global PASS,FAIL
    if cond: PASS+=1; print(f"  PASS  {name}")
    else: FAIL+=1; FAILURES.append(name); print(f"  FAIL  {name}  {detail}")
def data_of(r): return r.get("data") if isinstance(r,dict) and r.get("ok") else None
def main():
    global BASE,TOKEN
    ap=argparse.ArgumentParser(); ap.add_argument("--base",default=BASE); ap.add_argument("--token",default="")
    a=ap.parse_args(); BASE=a.base.rstrip("/"); TOKEN=a.token or load_token()
    sfx="%04x"%random.randint(0,0xffff); ids=[]
    try:
        folder=data_of(req("/api/assets",{"typeName":"Dir","name":f"树测试 {sfx}"}))["asset"]["id"]; ids.append(folder)
        a1=data_of(req("/api/assets",{"typeName":"QuickCmd","name":f"命令A {sfx}","alias":"别名A","parentId":folder,"config":{"command":"echo a"}}))["asset"]
        a2=data_of(req("/api/assets",{"typeName":"QuickCmd","name":f"命令B {sfx}","alias":"别名B","parentId":folder,"config":{"command":"echo b"}}))["asset"]
        ids += [a1["id"], a2["id"]]
        assets={x["id"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
        check("tree: alias on create", assets[a1["id"]].get("alias")=="别名A", str(assets[a1["id"]])[:200])
        upd=data_of(req(f"/api/assets/{a1['id']}",{"alias":f"改名-{sfx}"},method="PUT"))
        check("tree: alias update", ((upd or {}).get("asset") or {}).get("alias")==f"改名-{sfx}", str(upd)[:200])
        # move A before B (both under folder) -> A.sort < B.sort
        req(f"/api/assets/{a1['id']}/move",{"parentId":folder,"beforeId":a2["id"]})
        assets={x["id"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
        check("tree: move before sorts earlier", assets[a1["id"]]["sort"] < assets[a2["id"]]["sort"], f"{assets[a1['id']]['sort']} vs {assets[a2['id']]['sort']}")
        # move to root (no parent)
        req(f"/api/assets/{a1['id']}/move",{"parentId":None})
        assets={x["id"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
        check("tree: move to root", assets[a1["id"]]["parentId"] is None, str(assets[a1["id"]])[:200])
    finally:
        for i in reversed(ids):
            try: req(f"/api/assets/{i}",method="DELETE")
            except Exception: pass
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0
if __name__=="__main__": sys.exit(main())
