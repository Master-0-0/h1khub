#!/usr/bin/env python3
"""VNC asset parity: CRUD, port probe, UI address (UI checked separately)."""
from __future__ import annotations
import argparse, json, os, random, socket, sys, threading, time, urllib.error, urllib.request
BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]
def load_token():
    home=os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    return json.load(open(os.path.join(home,"runtime.json"),encoding="utf-8"))["token"]
def req(path,body=None,method="POST"):
    data=None if body is None else json.dumps(body).encode()
    r=urllib.request.Request(BASE+path,data=data,headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/json"},method=method)
    try:
        with urllib.request.urlopen(r,timeout=15) as resp: return json.loads(resp.read())
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
    sfx="%04x"%random.randint(0,0xffff); port=5900+random.randint(1,99)
    srv=socket.socket(); srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); srv.bind(("127.0.0.1",port)); srv.listen(5)
    stop=threading.Event()
    def accept():
        while not stop.is_set():
            try:
                srv.settimeout(0.3); c,_=srv.accept(); c.close()
            except Exception: continue
    acc_thread=threading.Thread(target=accept,daemon=True); acc_thread.start()
    aid=None
    try:
        aid=data_of(req("/api/assets",{"typeName":"VNC","name":f"VNC 测试 {sfx}","config":{"host":"127.0.0.1","port":port,"password":"secret"}}))["asset"]["id"]
        check("vnc: asset created", bool(aid))
        ok=data_of(req("/api/test-connection",{"assetId":aid}))
        check("vnc: port reachable", bool(ok and ok.get("port")==port), str(ok)[:200])
        # Close the listener and *join* the accept thread before probing: closing
        # a listening socket from another thread while accept() is still blocked
        # can leave the fd briefly usable, which made this assertion flaky under
        # load (the backend then still saw a reachable port).
        stop.set()
        try: srv.close()
        except Exception: pass
        acc_thread.join(timeout=2.0)
        time.sleep(0.3)
        bad=req("/api/test-connection",{"assetId":aid})
        check("vnc: closed port fails", bad.get("__http_error")==400, str(bad)[:200])
        assets={x["id"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
        check("vnc: password stored/secret", bool(assets[aid]["secrets"]) or "password" in str(assets[aid].get("secrets")), str(assets[aid])[:200])
    finally:
        stop.set()
        try: srv.close()
        except Exception: pass
        try: acc_thread.join(timeout=2.0)
        except Exception: pass
        if aid: req(f"/api/assets/{aid}",method="DELETE")
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0
if __name__=="__main__": sys.exit(main())
