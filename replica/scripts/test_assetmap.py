#!/usr/bin/env python3
"""End-to-end verification for the asset map (topology) feature.

API coverage: node/edge shape, jump chains, DB/tunnel SSH dependencies, layer
assignment, missing references and cycles. UI coverage: the map opens from the
left rail, renders nodes/edges, shows the full jump chain in the detail panel,
and the topology-only toggle removes isolated assets.

Usage: python3 scripts/test_assetmap.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_check import CDP, launch  # noqa: E402

BASE = "http://127.0.0.1:38110"
TOKEN = ""
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(REPO, "docs", "screenshots")
PASS = 0
FAIL = 0
FAILURES: list[str] = []
CREATED: list[str] = []
SUFFIX = "%04x" % random.randint(0, 0xFFFF)


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method: str = "POST"):
    data = None if body is None else json.dumps(body).encode()
    headers = {"X-H1KHub-Token": TOKEN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return {"__http_error": exc.code, "detail": json.loads(text)}
        except Exception:
            return {"__http_error": exc.code, "detail": text}


def data_of(resp):
    if isinstance(resp, dict) and resp.get("ok") and "data" in resp:
        return resp["data"]
    return None


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail[:500]}")


def create(name: str, type_name: str, config: dict) -> str:
    resp = req("/api/assets", {"typeName": type_name, "name": name, "config": config})
    data = data_of(resp)
    if not data:
        raise RuntimeError(f"create asset failed: {resp}")
    asset_id = data["asset"]["id"]
    CREATED.append(asset_id)
    return asset_id


def update(asset_id: str, config: dict) -> None:
    resp = req(f"/api/assets/{asset_id}", {"config": config}, "PUT")
    if resp.get("__http_error"):
        raise RuntimeError(f"update asset failed: {resp}")


def delete(asset_id: str) -> None:
    try:
        req(f"/api/assets/{asset_id}", method="DELETE")
    except Exception:
        pass


def asset_count() -> int:
    data = data_of(req("/api/assets", method="GET")) or {}
    return len(data.get("assets") or [])


def nodes_by_name(graph: dict) -> dict:
    return {node["name"]: node for node in graph.get("nodes", [])}


def find_edge(graph: dict, source_id: str, target_id: str, kind: str):
    for edge in graph.get("edges", []):
        if edge["from"] == source_id and edge["to"] == target_id and edge["kind"] == kind:
            return edge
    return None


def api_checks() -> tuple[dict, dict[str, str]]:
    print("\n[api]")
    graph = data_of(req("/api/asset-map", method="GET")) or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    stats = graph.get("stats") or {}
    by_name = nodes_by_name(graph)
    check("API: 返回节点与连线", len(nodes) >= 10 and len(edges) >= 1, f"nodes={len(nodes)} edges={len(edges)}")
    check("API: stats 与数组一致", stats.get("nodes") == len(nodes) and stats.get("edges") == len(edges), str(stats))
    check("API: 包含真实 SSH 资产", "SSH hexlab" in by_name and "SSH 跳板演示" in by_name, str(list(by_name))[:200])
    real_target = by_name.get("SSH 跳板演示") or {}
    real_jump = by_name.get("SSH hexlab") or {}
    check("API: 真实跳板链 depth=1", real_target.get("depth") == 1, str(real_target)[:300])
    check("API: 真实跳板边存在", find_edge(graph, real_target.get("id"), real_jump.get("id"), "jump") is not None, "")
    tunnel = by_name.get("隧道 local 测试") or {}
    check("API: 隧道 sshAssetId 生成 SSH 边", find_edge(graph, tunnel.get("id"), real_jump.get("id"), "ssh") is not None, "")

    print("\n[api: custom 2-hop chain + db/tunnel]")
    target = create(
        f"zz-map-target-{SUFFIX}",
        "SSH",
        {"host": "127.0.0.1", "port": 22, "username": "root", "authType": "password"},
    )
    hop_b = create(
        f"zz-map-hop-b-{SUFFIX}",
        "SSH",
        {"host": "127.0.0.1", "port": 22, "username": "root", "authType": "password"},
    )
    hop_a = create(
        f"zz-map-hop-a-{SUFFIX}",
        "SSH",
        {"host": "127.0.0.1", "port": 22, "username": "root", "authType": "password"},
    )
    update(target, {"jumpAssetId": hop_b})
    update(hop_b, {"jumpAssetId": hop_a})
    db = create(
        f"zz-map-db-{SUFFIX}",
        "DB",
        {"dbType": "mysql", "host": "127.0.0.1", "port": 3307, "database": "demo", "sshAssetId": target},
    )
    tunnel_asset = create(
        f"zz-map-tunnel-{SUFFIX}",
        "SSHTunnel",
        {"type": "dynamic", "sshAssetId": target},
    )
    graph2 = data_of(req("/api/asset-map", method="GET")) or {}
    by_name2 = nodes_by_name(graph2)
    node_target = by_name2.get(f"zz-map-target-{SUFFIX}") or {}
    node_b = by_name2.get(f"zz-map-hop-b-{SUFFIX}") or {}
    node_a = by_name2.get(f"zz-map-hop-a-{SUFFIX}") or {}
    check("API: 目标 depth=2", node_target.get("depth") == 2, str(node_target)[:300])
    check(
        "API: 链路顺序 inner->outer",
        node_target.get("chain") == [hop_b, hop_a] and node_target.get("chainNames") == [node_b.get("name"), node_a.get("name")],
        str(node_target.get("chain")) + " / " + str(node_target.get("chainNames")),
    )
    check("API: 跳板边 target->b", find_edge(graph2, target, hop_b, "jump") is not None, "")
    check("API: 跳板边 b->a", find_edge(graph2, hop_b, hop_a, "jump") is not None, "")
    check("API: DB sshAssetId 边", find_edge(graph2, db, target, "ssh") is not None, "")
    check("API: 隧道 sshAssetId 边", find_edge(graph2, tunnel_asset, target, "ssh") is not None, "")
    check("API: 分层 service=0", by_name2.get(f"zz-map-db-{SUFFIX}", {}).get("layer") == 0, str(by_name2.get(f"zz-map-db-{SUFFIX}")))
    check("API: 分层 target=1", node_target.get("layer") == 1, str(node_target))
    check("API: 分层 hop_b=2", node_b.get("layer") == 2, str(node_b))
    check("API: 分层 hop_a=3", node_a.get("layer") == 3, str(node_a))
    check("API: stats.maxDepth>=2", (graph2.get("stats") or {}).get("maxDepth", 0) >= 2, str(graph2.get("stats")))

    print("\n[api: missing reference]")
    missing = create(
        f"zz-map-missing-{SUFFIX}",
        "SSH",
        {"host": "127.0.0.1", "port": 22, "jumpAssetId": "a_missing_asset"},
    )
    graph3 = data_of(req("/api/asset-map", method="GET")) or {}
    missing_node = nodes_by_name(graph3).get(f"zz-map-missing-{SUFFIX}") or {}
    check(
        "API: 缺失引用记录 missing",
        any(item.get("field") == "jumpAssetId" and item.get("reason") == "not-found" for item in missing_node.get("missing", [])),
        str(missing_node.get("missing")),
    )
    check(
        "API: 缺失引用不生成连线",
        all(edge.get("to") != "a_missing_asset" for edge in graph3.get("edges", [])),
        "",
    )
    delete(missing)
    CREATED.remove(missing)

    print("\n[api: cycle]")
    cycle_a = create(f"zz-map-cycle-a-{SUFFIX}", "SSH", {"host": "127.0.0.1", "port": 22})
    cycle_b = create(f"zz-map-cycle-b-{SUFFIX}", "SSH", {"host": "127.0.0.1", "port": 22})
    update(cycle_a, {"jumpAssetId": cycle_b})
    update(cycle_b, {"jumpAssetId": cycle_a})
    started = time.perf_counter()
    graph4 = data_of(req("/api/asset-map", method="GET")) or {}
    elapsed = time.perf_counter() - started
    cycles = graph4.get("cycles") or []
    cycle_names = {name for cycle in cycles for name in cycle.get("names", [])}
    check("API: 检测到循环", f"zz-map-cycle-a-{SUFFIX}" in cycle_names and f"zz-map-cycle-b-{SUFFIX}" in cycle_names, str(cycles)[:400])
    check("API: 循环响应 <2s", elapsed < 2.0, f"{elapsed:.2f}s")
    check(
        "API: 循环边标记 cycle",
        any(edge.get("cycle") for edge in graph4.get("edges", []) if edge.get("from") in (cycle_a, cycle_b)),
        "",
    )
    check("API: 循环节点标记 cycle", all(nodes_by_name(graph4)[f"zz-map-cycle-{suffix}-{SUFFIX}"].get("cycle") for suffix in ("a", "b")), "")
    delete(cycle_a)
    delete(cycle_b)
    CREATED.remove(cycle_a)
    CREATED.remove(cycle_b)

    return graph2, {"target": target, "hop_a": hop_a, "hop_b": hop_b}


async def ui_checks(target_name: str, hop_a_name: str, hop_b_name: str) -> None:
    print("\n[ui]")
    proc = None
    cdp = None
    errors: list[str] = []
    try:
        proc, ws_url = await launch(BASE + "/")
        cdp = CDP(ws_url)
        await cdp.connect()
        await cdp.send("Runtime.enable")
        await cdp.send("Page.enable")
        await cdp.send("Log.enable")
        for _ in range(100):
            if await cdp.evaluate("document.querySelectorAll('.asset-panel .tree-row').length"):
                break
            await asyncio.sleep(0.2)
        opened = await cdp.evaluate(
            """(() => {
              const button = [...document.querySelectorAll('.rail-btn')]
                .find((item) => (item.title || '').includes('资产地图'));
              if (!button) return 'not-found';
              button.click();
              return 'ok';
            })()"""
        )
        check("UI: 左侧导航打开资产地图", opened == "ok", str(opened))
        for _ in range(80):
            if await cdp.evaluate("document.querySelectorAll('.map-node').length"):
                break
            await asyncio.sleep(0.2)
        node_count = await cdp.evaluate("document.querySelectorAll('.map-node').length")
        edge_count = await cdp.evaluate("document.querySelectorAll('.map-edge').length")
        jump_count = await cdp.evaluate("document.querySelectorAll('.map-edge.jump').length")
        check("UI: 节点已渲染", node_count >= 20, str(node_count))
        check("UI: 连线已渲染", edge_count >= 5, str(edge_count))
        check("UI: 至少 3 条跳板边", jump_count >= 3, str(jump_count))
        toolbar = await cdp.evaluate("document.querySelector('.map-toolbar')?.innerText || ''")
        check("UI: 工具栏显示节点/连线/跳板统计", "节点" in toolbar and "连线" in toolbar and "层跳板" in toolbar, toolbar[:300])

        await cdp.evaluate(
            """(() => {
              const node = [...document.querySelectorAll('.map-node')]
                .find((item) => (item.innerText || '').includes(%s));
              if (node) node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            })()""" % json.dumps(target_name)
        )
        await asyncio.sleep(0.5)
        detail = await cdp.evaluate("document.querySelector('.map-detail')?.innerText || ''")
        check("UI: 选中显示详情面板", target_name in detail, detail[:300])
        check("UI: 详情显示由外到内跳板链", "跳板链（由外到内）" in detail, detail[:400])
        chain_text = detail[detail.find("跳板链（由外到内）"):] if "跳板链（由外到内）" in detail else ""
        ordered = (
            hop_a_name in chain_text
            and chain_text.find(hop_a_name) < chain_text.find(hop_b_name) < chain_text.find(target_name)
        )
        check("UI: 链路顺序 outermost -> target", ordered, chain_text[:500] or detail[:500])
        await cdp.screenshot(os.path.join(SHOTS, "asset-map-chain.png"))

        await cdp.evaluate("document.querySelector('.map-toggle')?.click()")
        await asyncio.sleep(0.6)
        topology_only = await cdp.evaluate("document.querySelectorAll('.map-node').length")
        check("UI: 仅拓扑模式隐藏孤立资产", topology_only < node_count, f"{node_count} -> {topology_only}")
        await cdp.evaluate("document.querySelector('.map-toggle')?.click()")
        await asyncio.sleep(0.4)
    except Exception as exc:
        check("UI: 资产地图交互", False, f"{type(exc).__name__}: {exc}")
    finally:
        if cdp is not None:
            for event in cdp.events:
                if event.get("method") == "Runtime.exceptionThrown":
                    errors.append(event["params"]["exceptionDetails"].get("text") or "exception")
                elif event.get("method") == "Runtime.consoleAPICalled" and event["params"]["type"] == "error":
                    errors.append("console: " + " ".join(str(arg.get("value") or arg.get("description")) for arg in event["params"]["args"]))
                elif event.get("method") == "Log.entryAdded" and event["params"]["entry"]["level"] == "error":
                    errors.append("log: " + event["params"]["entry"]["text"])
            try:
                await cdp.close()
            except Exception:
                pass
        if proc is not None:
            proc.kill()
    check("UI: 无 console error", not errors, str(errors)[:600])


def main() -> int:
    global BASE, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    initial_assets = asset_count()
    print(f"base={BASE} initial_assets={initial_assets}")
    graph: dict = {}
    ids: dict[str, str] = {}
    try:
        graph, ids = api_checks()
        asyncio.run(
            ui_checks(
                f"zz-map-target-{SUFFIX}",
                f"zz-map-hop-a-{SUFFIX}",
                f"zz-map-hop-b-{SUFFIX}",
            )
        )
    except Exception as exc:
        check("suite: 未捕获异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        for asset_id in reversed(CREATED):
            delete(asset_id)
        check("清理: 资产数恢复初始值", asset_count() == initial_assets, f"{initial_assets} -> {asset_count()}")
        leftovers = [a["name"] for a in (data_of(req("/api/assets", method="GET")) or {}).get("assets", []) if a["name"].startswith("zz-map-")]
        check("清理: 无 zz-map 资产残留", not leftovers, str(leftovers)[:300])
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
