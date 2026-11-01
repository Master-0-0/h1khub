#!/usr/bin/env python3
"""Redis smoke + batch-operation tests (scan, rename, set, expire, delete).

Only keys prefixed with ``zz_rbatch_<suffix>`` are created and removed.

Usage: python3 scripts/test_redis.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, headers={"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return {"__http_error": exc.code, "detail": json.loads(text)}
        except Exception:
            return {"__http_error": exc.code, "detail": text}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def data_of(resp):
    if isinstance(resp, dict) and resp.get("ok") and "data" in resp:
        return resp["data"]
    return None


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    prefix = f"zz_rbatch_{suffix}"
    assets = {a["name"]: a for a in data_of(req("/api/assets", method="GET"))["assets"]}
    asset_id = assets["Redis local"]["id"]
    db = 0

    def call(action, payload=None):
        return data_of(req(f"/api/redis/{action}", {"assetId": asset_id, "db": db, **(payload or {})}))

    keys = [f"{prefix}:{i}" for i in range(1, 4)]
    try:
        print("[redis base]")
        check("redis: test", bool(call("test")))
        check("redis: databases", len(call("databases") or []) >= 1)

        for key in keys:
            call("set-string", {"key": key, "value": "v1"})
        scanned = call("scan-keys", {"pattern": f"{prefix}:*", "limit": 100})
        check("redis: scan pattern", sorted((scanned or {}).get("keys") or []) == sorted(keys), str(scanned))

        renamed = call("batch-rename", {"keys": keys, "rule": {"mode": "prefix", "value": "new-"}})
        new_keys = [f"new-{k}" for k in keys]
        check("redis: batch rename", (renamed or {}).get("renamed") == 3, str(renamed)[:400])
        check("redis: renamed keys exist", sorted((call("scan-keys", {"pattern": f"new-{prefix}:*"}) or {}).get("keys") or []) == sorted(new_keys))

        updated = call("batch-set", {"keys": new_keys, "value": "v2", "ttl": 120})
        check("redis: batch set", (updated or {}).get("updated") == 3, str(updated)[:400])
        info = call("key", {"key": new_keys[0]})
        check("redis: batch set value", (info or {}).get("value", {}).get("value") == "v2", str(info)[:300])

        expired = call("batch-expire", {"keys": new_keys, "ttl": 300})
        check("redis: batch expire", (expired or {}).get("updated") == 3, str(expired)[:300])
        ttl = call("key", {"key": new_keys[0]})
        check("redis: ttl applied", 0 < int((ttl or {}).get("info", {}).get("ttl") or 0) <= 300, str(ttl)[:200])
        persisted = call("batch-expire", {"keys": new_keys[:1], "ttl": 0})
        check("redis: batch persist", (persisted or {}).get("updated") == 1, str(persisted)[:200])

        # batch-set must reject non-string keys
        call("list-push", {"key": f"{prefix}:list", "value": "a"})
        rejected = call("batch-set", {"keys": [f"{prefix}:list"], "value": "x"})
        check("redis: batch set rejects list", (rejected or {}).get("failed") == 1, str(rejected)[:300])
        call("delete", {"keys": [f"{prefix}:list"]})

        deleted = call("batch-delete", {"keys": new_keys})
        check("redis: batch delete", (deleted or {}).get("deleted") == 3, str(deleted)[:300])
        check("redis: deleted keys gone", (call("scan-keys", {"pattern": f"new-{prefix}:*"}) or {}).get("keys") == [])
        empty = call("batch-delete", {"keys": []})
        check("redis: batch delete empty rejected", empty is None)

        print("[redis realtime]")
        rt = call("realtime")
        check("redis: realtime info", bool(rt and "keyspace" in rt), str(rt)[:200])
    finally:
        leftovers = (call("scan-keys", {"pattern": f"*{prefix}*", "limit": 100}) or {}).get("keys") or []
        leftovers += (call("scan-keys", {"pattern": f"new-{prefix}*", "limit": 100}) or {}).get("keys") or []
        if leftovers:
            call("batch-delete", {"keys": leftovers})

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
