#!/usr/bin/env python3
"""Docker image build/history/save/load/remove/prune tests (temporary image)."""
from __future__ import annotations
import argparse, io, json, os, random, shutil, subprocess, sys, tarfile, tempfile, urllib.error, urllib.request

BASE="http://127.0.0.1:38110"; TOKEN=""; PASS=0; FAIL=0; FAILURES=[]
BASE_IMAGE=os.environ.get("H1KHUB_TEST_IMAGE","agent-compose-ui:latest")

def load_token():
    home=os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    return json.load(open(os.path.join(home,"runtime.json"),encoding="utf-8"))["token"]

def req(path, body=None, method="POST", raw=False, query=None, binary=False, timeout=600):
    import urllib.parse
    url=BASE+path+(("?"+urllib.parse.urlencode(query)) if query else "")
    if binary:
        data=body; headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/octet-stream"}
    else:
        data=None if body is None else json.dumps(body).encode(); headers={"X-H1KHub-Token":TOKEN,"Content-Type":"application/json"}
    r=urllib.request.Request(url,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp:
            payload=resp.read(); return payload if raw else json.loads(payload)
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
    tag=f"h1khub-e2e-img:{suffix}"
    ctx=tempfile.mkdtemp(prefix="h1khub-build-")
    tar_path=os.path.join(ctx,"image.tar")
    assets={x["name"]:x for x in data_of(req("/api/assets",method="GET"))["assets"]}
    aid=assets["Docker local"]["id"]
    try:
        with open(os.path.join(ctx,"Dockerfile"),"w") as fh:
            fh.write(f"FROM {BASE_IMAGE}\nRUN echo h1khub-build-{suffix} > /build-marker.txt\n")
        built=data_of(req("/api/docker/image-build",{"assetId":aid,"tag":tag,"dir":ctx}))
        check("images: build", bool(built and built.get("tag")==tag), str(built)[:400] if built else "")
        listing=data_of(req("/api/docker/images",{"assetId":aid}))["images"]
        check("images: build visible", any(i.get("repository")+":"+i.get("tag")==tag for i in listing), str(listing)[:300])
        hist=data_of(req("/api/docker/image-history",{"assetId":aid,"image":tag}))["history"]
        check("images: history rows", len(hist)>=2, str(hist)[:300])
        blob=req("/api/docker/image/save",{"assetId":aid,"image":tag},raw=True,timeout=600)
        check("images: save tar", isinstance(blob,bytes) and len(blob)>1024, f"len={len(blob)}")
        ok_tar=False
        try:
            with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
                names=tf.getnames()
                ok_tar="manifest.json" in names
        except Exception as exc:
            ok_tar=False
        check("images: tar has manifest", ok_tar)
        loaded=req("/api/docker/image/load",blob,query={"assetId":aid},binary=True,timeout=600)
        check("images: load", bool(data_of(loaded)), str(loaded)[:300])
        removed=data_of(req("/api/docker/image-remove",{"assetId":aid,"image":tag}))
        check("images: remove", bool(removed), str(removed)[:200])
        listing=data_of(req("/api/docker/images",{"assetId":aid}))["images"]
        check("images: removed from list", not any(i.get("repository")+":"+i.get("tag")==tag for i in listing))
        pruned=data_of(req("/api/docker/image-prune",{"assetId":aid}))
        check("images: prune", bool(pruned), str(pruned)[:200])
        bad=req("/api/docker/image-build",{"assetId":aid,"tag":"","dir":ctx})
        check("images: build validates tag", bad.get("__http_error")==400, str(bad)[:200])
    finally:
        subprocess.run(["docker","rmi","-f",tag],capture_output=True,timeout=120)
        shutil.rmtree(ctx,ignore_errors=True)
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES: print("FAILED:",", ".join(FAILURES)); return 1
    return 0

if __name__=="__main__": sys.exit(main())
