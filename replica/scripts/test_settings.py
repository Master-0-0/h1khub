#!/usr/bin/env python3
"""Terminal log-session setting and settings persistence tests."""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request
BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]
HOME=os.path.expanduser("~/.h1khub")

def load_token(): return json.load(open(os.path.join(HOME,"runtime.json"),encoding="utf-8"))["token"]
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
    original=data_of(req("/api/settings",method="GET"))
    session=None
    try:
        saved=data_of(req("/api/settings",{"terminal":{"logSessions":True},"editor":{"fontSize":16,"tabSize":4,"wordWrap":True},"general":{"hotkeys":{"search":"Ctrl+J","settings":"Ctrl+,","localTerminal":"Ctrl+`","closeTab":"Ctrl+W","history":"Ctrl+E","togglePanel":"Ctrl+Shift+E"}}},method="PUT"))
        check("settings: saved", bool(saved and saved["terminal"].get("logSessions") is True), str(saved)[:300] if saved else "")
        check("settings: editor persisted", bool(saved and saved["editor"].get("fontSize")==16 and saved["editor"].get("wordWrap")))
        check("settings: hotkey persisted", bool(saved and saved["general"]["hotkeys"].get("search")=="Ctrl+J"))

        assets={x["name"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
        st=data_of(req("/api/sessions/start",{"assetId":assets["SSH hexlab"]["id"],"cols":80,"rows":24}))
        session=(st or {}).get("session")
        check("settings: session started", bool(session), str(st)[:200])
        if session:
            time.sleep(1.5)
            req(f"/api/sessions/{session['id']}/input",{"data":"echo log-check-ok"})
            time.sleep(1.0)
            closed=data_of(req(f"/api/sessions/{session['id']}/close",{}))
            log_path=(closed or {}).get("logPath")
            check("settings: log file written", bool(log_path and os.path.isfile(log_path)), str(closed)[:300])
            if log_path:
                st_mode=os.stat(log_path).st_mode & 0o777
                check("settings: log mode 0600", st_mode==0o600, oct(st_mode))
                content=open(log_path,encoding="utf-8").read()
                check("settings: log content", "log-check-ok" in content, content[-200:])
            logs=data_of(req("/api/session-logs",method="GET")).get("logs") or []
            check("settings: log listed", any(x["name"]==os.path.basename(log_path or "") for x in logs), str(logs)[:300])
            if log_path:
                raw=urllib.request.urlopen(urllib.request.Request(BASE+f"/api/session-logs/{urllib.parse.quote(os.path.basename(log_path))}",headers={"X-H1KHub-Token":TOKEN}),timeout=10).read()
                check("settings: log download", b"log-check-ok" in raw, str(raw[:80]))
            session=None
    finally:
        if session: req(f"/api/sessions/{session['id']}/close",{})
        if original:
            req("/api/settings",{"terminal":original.get("terminal",{}),"editor":original.get("editor",{}),"general":original.get("general",{})},method="PUT")
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0

if __name__=="__main__": sys.exit(main())
