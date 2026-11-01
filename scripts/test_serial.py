#!/usr/bin/env python3
"""Serial session test using a socat PTY pair (no hardware required)."""
from __future__ import annotations
import argparse, json, os, random, subprocess, sys, time, urllib.error, urllib.request
import serial

BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]
A="/tmp/hx-serial-a"; B="/tmp/hx-serial-b"

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

def main():
    global BASE,TOKEN
    ap=argparse.ArgumentParser(); ap.add_argument("--base",default=BASE); ap.add_argument("--token",default="")
    a=ap.parse_args(); BASE=a.base.rstrip("/"); TOKEN=a.token or load_token()
    suffix="%04x"%random.randint(0,0xFFFF)
    for p in (A,B):
        try: os.unlink(p)
        except OSError: pass
    proc=subprocess.Popen(["socat","-d","-d",f"pty,raw,echo=0,link={A}",f"pty,raw,echo=0,link={B}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    asset=None; session=None
    try:
        for _ in range(50):
            if os.path.exists(A) and os.path.exists(B): break
            time.sleep(0.1)
        check("serial: pty pair created", os.path.exists(A) and os.path.exists(B))
        asset=data_of(req("/api/assets",{"typeName":"Serial","name":f"串口测试 {suffix}","config":{"port":A,"baudRate":115200,"dataBits":8,"parity":"N","stopBits":1}}))["asset"]["id"]
        test=req("/api/test-connection",{"assetId":asset})
        check("serial: connection test", bool(data_of(test)), str(test)[:200])
        st=data_of(req("/api/sessions/start",{"assetId":asset,"cols":80,"rows":24}))
        session=(st or {}).get("session")
        check("serial: session started", bool(session), str(st)[:200])
        if not session: return 1
        time.sleep(0.5)
        with serial.Serial(B,115200,timeout=1) as peer:
            peer.write(b"hello-from-peer\n"); peer.flush()
            buf=""
            for _ in range(30):
                r=data_of(req(f"/api/sessions/{session['id']}/read",{"lines":50}))
                buf=(r or {}).get("content") or ""
                if "hello-from-peer" in buf: break
                time.sleep(0.1)
            check("serial: peer -> session", "hello-from-peer" in buf, buf[-200:])
            req(f"/api/sessions/{session['id']}/input",{"data":"hello-from-h1khub\n"})
            received=b""
            end=time.time()+3
            while time.time()<end and b"hello-from-h1khub" not in received:
                received+=peer.read(64)
            check("serial: session -> peer", b"hello-from-h1khub" in received, repr(received[:120]))
    finally:
        if session: req(f"/api/sessions/{session['id']}/close")
        if asset: req(f"/api/assets/{asset}",method="DELETE")
        proc.terminate()
        try: proc.wait(timeout=3)
        except Exception: proc.kill()
        for p in (A,B):
            try: os.unlink(p)
            except OSError: pass
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0

if __name__=="__main__": sys.exit(main())
