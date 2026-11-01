#!/usr/bin/env python3
"""Redis cluster tests against a local 3-node cluster (127.0.0.1:7001-7003)."""
from __future__ import annotations
import argparse, json, os, random, socket, subprocess, sys, time, urllib.error, urllib.request
BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]; PORTS=(7001,7002,7003)

def load_token():
    home=os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    return json.load(open(os.path.join(home,"runtime.json"),encoding="utf-8"))["token"]
def req(path, body=None, method="POST"):
    data=None if body is None else json.dumps(body).encode()
    r=urllib.request.Request(BASE+path,data=data,headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/json"},method=method)
    try:
        with urllib.request.urlopen(r,timeout=30) as resp: return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try: return {"__http_error":exc.code,"detail":json.loads(exc.read().decode())}
        except Exception: return {"__http_error":exc.code}
def check(name, cond, detail=""):
    global PASS,FAIL
    if cond: PASS+=1; print(f"  PASS  {name}")
    else: FAIL+=1; FAILURES.append(name); print(f"  FAIL  {name}  {detail}")
def data_of(r): return r.get("data") if isinstance(r,dict) and r.get("ok") else None
def port_open(p):
    with socket.socket() as s:
        s.settimeout(0.3); return s.connect_ex(("127.0.0.1",p))==0

def ensure_cluster():
    if all(port_open(p) for p in PORTS): return
    root="/tmp/h1khub-redis-cluster"
    for p in PORTS:
        d=os.path.join(root,str(p)); os.makedirs(d,exist_ok=True)
        subprocess.run(["redis-server","--port",str(p),"--bind","127.0.0.1","--cluster-enabled","yes",
            "--cluster-config-file","nodes.conf","--cluster-node-timeout","5000","--appendonly","no",
            "--daemonize","yes","--dir",d,"--logfile",os.path.join(root,f"{p}.log")],check=True,timeout=10)
    time.sleep(1)
    subprocess.run(["redis-cli","--cluster","create",*[f"127.0.0.1:{p}" for p in PORTS],
        "--cluster-replicas","0","--cluster-yes"],check=True,timeout=60,capture_output=True)
    time.sleep(2)

def main():
    global BASE,TOKEN
    ap=argparse.ArgumentParser(); ap.add_argument("--base",default=BASE); ap.add_argument("--token",default="")
    a=ap.parse_args(); BASE=a.base.rstrip("/"); TOKEN=a.token or load_token()
    suffix="%04x"%random.randint(0,0xFFFF)
    ensure_cluster()
    assets={x["name"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
    if "Redis Cluster 测试" in assets:
        aid=assets["Redis Cluster 测试"]["id"]
    else:
        aid=data_of(req("/api/assets",{"typeName":"Redis","name":"Redis Cluster 测试","config":{"cluster":True,"nodes":",".join(f"127.0.0.1:{p}" for p in PORTS),"db":0}}))["asset"]["id"]
    prefix=f"zz_cluster_{suffix}"
    keys=[f"{prefix}:{i}" for i in range(3)]
    def call(action,payload=None): return data_of(req(f"/api/redis/{action}",{"assetId":aid,"db":0,**(payload or {})}))
    try:
        print("[redis cluster]")
        test=call("test"); check("cluster: test mode", bool(test and test.get("mode")=="cluster"), str(test)[:200])
        dbs=(call("databases") or {}).get("databases") or []; check("cluster: db0 only", len(dbs)==1 and dbs[0]["index"]==0, str(dbs)[:200])
        for k in keys: call("set-string",{"key":k,"value":"v1"})
        info=call("key",{"key":keys[0]}); check("cluster: string read", (info or {}).get("value",{}).get("value")=="v1", str(info)[:200])
        call("hash-set",{"key":f"{prefix}:hash","field":"f","value":"h"}); info=call("key",{"key":f"{prefix}:hash"})
        check("cluster: hash read", (info or {}).get("info",{}).get("type")=="hash", str(info)[:200])
        scan=call("scan",{"pattern":f"{prefix}:*"}); found=sorted(x["key"] for x in (scan or {}).get("keys",[]))
        check("cluster: scan", set(keys+[f"{prefix}:hash"]).issubset(set(found)), str(found)[:300])
        renamed=call("batch-rename",{"keys":keys,"rule":{"mode":"prefix","value":"new-"}})
        check("cluster: batch rename", (renamed or {}).get("renamed")==3, str(renamed)[:300])
        newkeys=[f"new-{k}" for k in keys]
        updated=call("batch-set",{"keys":newkeys,"value":"v2"}); check("cluster: batch set", (updated or {}).get("updated")==3, str(updated)[:300])
        expired=call("batch-expire",{"keys":newkeys,"ttl":120}); check("cluster: batch expire", (expired or {}).get("updated")==3, str(expired)[:200])
        console=call("console",{"command":"INFO server"}); check("cluster: console", bool(console and "redis_version" in json.dumps(console)), str(console)[:200])
        rt=call("realtime"); check("cluster: realtime", bool(rt and "keyspace" in rt), str(rt)[:200])
        deleted=call("batch-delete",{"keys":newkeys+[f"{prefix}:hash"]}); check("cluster: batch delete", (deleted or {}).get("deleted")>=4, str(deleted)[:200])
        bad=req("/api/redis/scan",{"assetId":aid,"db":1,"pattern":"*"}); check("cluster: db1 rejected", bad.get("__http_error")==400, str(bad)[:200])
    finally:
        try: call("batch-delete",{"keys":[f"{prefix}:hash"]})
        except Exception: pass
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0

if __name__=="__main__": sys.exit(main())
