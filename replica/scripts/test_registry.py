#!/usr/bin/env python3
"""Private registry login success/failure using a local Basic-auth /v2/ endpoint."""
from __future__ import annotations
import argparse, base64, http.server, json, os, socket, subprocess, sys, threading, time, urllib.error, urllib.request
BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]
SEEN=[]

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        auth=self.headers.get("Authorization","")
        if not auth.startswith("Basic "):
            self.send_response(401); self.send_header("WWW-Authenticate",'Basic realm="Registry"'); self.end_headers(); return
        try:
            user,pw=base64.b64decode(auth[6:]).decode().split(":",1)
        except Exception:
            user,pw="",""
        SEEN.append(user)
        if user=="h1khub" and pw=="secret":
            self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
            self.wfile.write(b"{}")
        else:
            self.send_response(401); self.end_headers()

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
    srv=http.server.HTTPServer(("127.0.0.1",5006),Handler); threading.Thread(target=srv.serve_forever,daemon=True).start()
    time.sleep(0.3)
    assets={x["name"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
    aid=assets["Docker local"]["id"]
    try:
        ok=data_of(req("/api/docker/registry-login",{"assetId":aid,"registry":"127.0.0.1:5006","username":"h1khub","password":"secret"}))
        check("registry: login success", bool(ok), str(ok)[:200])
        check("registry: server saw auth", "h1khub" in SEEN, str(SEEN))
        bad=req("/api/docker/registry-login",{"assetId":aid,"registry":"127.0.0.1:5006","username":"h1khub","password":"wrong"})
        check("registry: bad password fails", bad.get("__http_error")==400, str(bad)[:300])
    finally:
        subprocess.run(["docker","logout","127.0.0.1:5006"],capture_output=True,timeout=20)
        srv.shutdown()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0
if __name__=="__main__": sys.exit(main())
