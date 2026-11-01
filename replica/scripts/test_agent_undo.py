#!/usr/bin/env python3
"""AI write_file diff preview + backup/rollback test (mock LLM)."""
from __future__ import annotations
import argparse, asyncio, json, os, random, socket, subprocess, sys, time, urllib.error, urllib.request
import aiohttp
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
def port_open(p):
    with socket.socket() as s:
        s.settimeout(0.3); return s.connect_ex(("127.0.0.1",p))==0

async def chat(prompt: str, timeout: float = 45.0):
    url=BASE.replace("http://","ws://")+f"/rpc?token={TOKEN}"
    events=[]; approval=None
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url,heartbeat=10) as ws:
            await ws.send_json({"method":"open","channel":"ai"})
            deadline=time.time()+timeout
            while time.time()<deadline:
                msg=await asyncio.wait_for(ws.receive(),timeout=max(0.5,deadline-time.time()))
                if msg.type!=aiohttp.WSMsgType.TEXT: break
                packet=json.loads(msg.data)
                if packet.get("method")=="open":
                    await ws.send_json({"method":"send","channel":"ai","data":{"action":"chat","prompt":prompt,"providerId":"mock","model":"mock-1"}})
                    continue
                if packet.get("method")!="event": continue
                event=packet.get("data") or {}; events.append(event)
                if event.get("type")=="approval":
                    approval=event
                    await ws.send_json({"method":"send","channel":"ai","data":{"action":"approval","id":event.get("id"),"approve":True}})
                if event.get("type") in ("done","error"): break
            await ws.send_json({"method":"close","channel":"ai"})
    return events, approval

def main():
    global BASE,TOKEN
    ap=argparse.ArgumentParser(); ap.add_argument("--base",default=BASE); ap.add_argument("--token",default="")
    a=ap.parse_args(); BASE=a.base.rstrip("/"); TOKEN=a.token or load_token()
    sfx="%04x"%random.randint(0,0xffff); path=f"/tmp/h1khub-undo-{sfx}.txt"
    old="line1\nline2\n"; new="line1\nCHANGED\n"
    mock=None
    orig=data_of(req("/api/settings",method="GET"))
    try:
        if not port_open(39001):
            mock=subprocess.Popen([sys.executable,os.path.join(os.path.dirname(__file__),"mock_llm.py")],stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
            # The mock server needs ~1 s to bind; poll instead of a fixed sleep,
            # otherwise the first AI request races it and fails with
            # ClientConnectorError on a loaded machine.
            for _ in range(60):
                if port_open(39001): break
                time.sleep(0.25)
        if not port_open(39001):
            check("undo: mock LLM 启动", False, "127.0.0.1:39001 未监听")
        mock_prov={"id":"mock","name":"Mock","protocol":"openai","baseUrl":"http://127.0.0.1:39001/v1","apiKey":"x","models":["mock-1"]}
        req("/api/settings",{"ai":{"providers":[mock_prov],"activeProviderId":"mock","activeModel":"mock-1"}},method="PUT")
        with open(path,"w",encoding="utf-8") as fh: fh.write(old)
        arg=json.dumps({"path":path,"content":new},ensure_ascii=False)
        events,approval=asyncio.run(chat(f"@tool write_file {arg}"))
        diff=(approval or {}).get("detail",{}).get("diff","")
        check("undo: approval diff", "-line2" in diff and "+CHANGED" in diff, diff[:300])
        result=next((e for e in events if e.get("type")=="tool_result"),None)
        parsed={}
        try: parsed=json.loads((result or {}).get("output") or "{}")
        except Exception: pass
        check("undo: backupId in result", bool(parsed.get("backupId")), str(result)[:300])
        check("undo: file written", open(path,encoding="utf-8").read()==new)
        backups=data_of(req("/api/agent/backups",method="GET")).get("backups") or []
        check("undo: backup listed", any(b.get("id")==parsed.get("backupId") for b in backups), str(backups)[:300])
        rolled=data_of(req("/api/agent/rollback",{"id":parsed.get("backupId")}))
        check("undo: rollback ok", bool(rolled), str(rolled)[:300])
        check("undo: content restored", open(path,encoding="utf-8").read()==old)
        bad=req("/api/agent/rollback",{"id":"bk_nope"})
        check("undo: missing 404", bad.get("__http_error")==404, str(bad)[:200])
    finally:
        try: os.unlink(path)
        except OSError: pass
        if orig: req("/api/settings",{"ai":orig.get("ai",{})},method="PUT")
        if mock: mock.terminate()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0
if __name__=="__main__": sys.exit(main())
