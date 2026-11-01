"""Read-only asset topology used by the asset map view.

The map is intentionally scoped to *connection* assets: SSH, databases, Redis,
Docker, SSH tunnels and the network endpoints (FTP/Telnet/VNC). Directories,
quick commands and credentials are not graph nodes; they remain in the asset
tree. Two edge kinds are produced:

* ``jump``  target SSH -> its jump SSH (``jumpAssetId``)
* ``ssh``   DB/Redis/Docker/SSHTunnel/FTP -> the SSH host (``sshAssetId``)

Edges point from the dependent asset to the asset it relies on, so the layer
number grows outwards through a jump chain: service -> target -> J1 -> J2.
"""
from __future__ import annotations

from typing import Any

from util import now_iso

# Node types shown on the map.
NODE_TYPES = {
    "SSH",
    "DB",
    "Redis",
    "Docker",
    "SSHTunnel",
    "FTP",
    "Telnet",
    "VNC",
}

# Assets that can be reached through an SSH host.
SSH_DEPENDENCY_TYPES = {"DB", "Redis", "Docker", "SSHTunnel", "FTP"}

TYPE_GROUP = {
    "SSH": "ssh",
    "DB": "db",
    "Redis": "redis",
    "Docker": "docker",
    "SSHTunnel": "tunnel",
    "FTP": "endpoint",
    "Telnet": "endpoint",
    "VNC": "endpoint",
}


def _folder_path(asset: dict, by_id: dict[str, dict]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    current = asset
    while current and current.get("parentId"):
        parent = by_id.get(current["parentId"])
        if not parent or parent.get("typeName") != "Dir" or parent["id"] in seen:
            break
        seen.add(parent["id"])
        names.append(parent.get("name") or parent["id"])
        current = parent
    names.reverse()
    return names


def _public_node(asset: dict, by_id: dict[str, dict]) -> dict[str, Any]:
    cfg = asset.get("config") or {}
    type_name = asset.get("typeName") or ""
    return {
        "id": asset["id"],
        "name": asset.get("name") or asset["id"],
        "typeName": type_name,
        "type": asset.get("type"),
        "group": TYPE_GROUP.get(type_name, "endpoint"),
        "parentId": asset.get("parentId"),
        "folder": _folder_path(asset, by_id),
        "host": cfg.get("host") or "",
        "port": cfg.get("port"),
        "username": cfg.get("username") or "",
        "database": cfg.get("database") or "",
        "jumpAssetId": cfg.get("jumpAssetId") or "",
        "sshAssetId": cfg.get("sshAssetId") or "",
        "tunnelType": cfg.get("type") if type_name == "SSHTunnel" else "",
        "missing": [],
        "inbound": 0,
        "outbound": 0,
        "chain": [],
        "chainNames": [],
        "depth": 0,
        "cycle": False,
    }


def _dedupe_cycles(cycles: list[list[str]]) -> list[list[str]]:
    """Canonicalise a cycle by rotating it to its smallest id, then dedupe."""
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for cycle in cycles:
        if not cycle:
            continue
        start = min(range(len(cycle)), key=lambda index: cycle[index])
        rotated = tuple(cycle[start:] + cycle[:start])
        if rotated in seen:
            continue
        seen.add(rotated)
        out.append(list(rotated))
    return out


def build_asset_map(assets: list[dict]) -> dict[str, Any]:
    by_id = {asset["id"]: asset for asset in assets}
    nodes = [_public_node(asset, by_id) for asset in assets if asset.get("typeName") in NODE_TYPES]
    nodes_by_id = {node["id"]: node for node in nodes}

    edges: list[dict[str, Any]] = []

    def add_edge(source: dict, target_id: str, kind: str, field: str) -> None:
        target = nodes_by_id.get(target_id)
        if target is None or target["typeName"] != "SSH":
            reason = "not-found" if target is None else "not-ssh"
            source["missing"].append({"field": field, "assetId": target_id, "reason": reason})
            return
        edge_id = f"{kind}:{source['id']}:{target_id}"
        if any(edge["id"] == edge_id for edge in edges):
            return
        edges.append(
            {
                "id": edge_id,
                "from": source["id"],
                "to": target_id,
                "kind": kind,
                "label": "跳板" if kind == "jump" else "SSH",
                "cycle": False,
            }
        )
        source["outbound"] += 1
        target["inbound"] += 1

    for node in nodes:
        if node["typeName"] == "SSH" and node["jumpAssetId"]:
            add_edge(node, node["jumpAssetId"], "jump", "jumpAssetId")
        if node["typeName"] in SSH_DEPENDENCY_TYPES and node["sshAssetId"]:
            add_edge(node, node["sshAssetId"], "ssh", "sshAssetId")

    # Cycle detection over jump edges only (SSH dependencies cannot point back
    # into a service node, so they can never create a cycle on their own).
    jump_out: dict[str, list[str]] = {}
    for edge in edges:
        if edge["kind"] == "jump":
            jump_out.setdefault(edge["from"], []).append(edge["to"])
    state: dict[str, int] = {}
    stack: list[str] = []
    raw_cycles: list[list[str]] = []

    def visit(node_id: str) -> None:
        state[node_id] = 1
        stack.append(node_id)
        for target_id in jump_out.get(node_id, []):
            if state.get(target_id, 0) == 0:
                visit(target_id)
            elif state.get(target_id) == 1:
                index = stack.index(target_id)
                raw_cycles.append(stack[index:].copy())
        stack.pop()
        state[node_id] = 2

    for node in nodes:
        if node["typeName"] == "SSH" and state.get(node["id"], 0) == 0:
            visit(node["id"])

    cycles = _dedupe_cycles(raw_cycles)
    cycle_nodes: set[str] = set()
    cycle_edges: set[tuple[str, str]] = set()
    for cycle in cycles:
        cycle_nodes.update(cycle)
        for index, source_id in enumerate(cycle):
            cycle_edges.add((source_id, cycle[(index + 1) % len(cycle)]))
    for node in nodes:
        node["cycle"] = node["id"] in cycle_nodes
    for edge in edges:
        if edge["kind"] == "jump" and (edge["from"], edge["to"]) in cycle_edges:
            edge["cycle"] = True

    # Jump chain per SSH node, inner -> outer (J1, J2, ...). Stops at a missing
    # reference or a repeat; cycle membership is already known above.
    max_depth = 0
    for node in nodes:
        if node["typeName"] != "SSH":
            continue
        chain: list[str] = []
        seen = {node["id"]}
        current = node
        while current.get("jumpAssetId"):
            target_id = current["jumpAssetId"]
            target = nodes_by_id.get(target_id)
            if target is None or target["typeName"] != "SSH":
                break
            if target_id in seen:
                break
            chain.append(target_id)
            seen.add(target_id)
            current = target
        node["chain"] = chain
        node["chainNames"] = [nodes_by_id[target_id]["name"] for target_id in chain]
        node["depth"] = len(chain)
        max_depth = max(max_depth, len(chain))

    # Kahn layering: roots (services / standalone SSH) at layer 0, then inward.
    in_degree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        in_degree[edge["to"]] += 1
    layer = {node["id"]: 0 for node in nodes}
    queue = [node["id"] for node in nodes if in_degree[node["id"]] == 0]
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append(edge["to"])
    while queue:
        source_id = queue.pop(0)
        for target_id in outgoing.get(source_id, []):
            layer[target_id] = max(layer[target_id], layer[source_id] + 1)
            in_degree[target_id] -= 1
            if in_degree[target_id] == 0:
                queue.append(target_id)
    # Nodes left over are inside cycles: place them in one trailing column.
    trailing = max(layer.values(), default=0) + 1
    leftover = [node["id"] for node in nodes if in_degree[node["id"]] > 0]
    for node_id in leftover:
        layer[node_id] = trailing
    for node in nodes:
        node["layer"] = layer[node["id"]]

    group_counts: dict[str, int] = {}
    for node in nodes:
        group_counts[node["group"]] = group_counts.get(node["group"], 0) + 1

    return {
        "generatedAt": now_iso(),
        "nodes": nodes,
        "edges": edges,
        "cycles": [{"ids": cycle, "names": [nodes_by_id[node_id]["name"] for node_id in cycle]} for cycle in cycles],
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "jumpEdges": sum(1 for edge in edges if edge["kind"] == "jump"),
            "sshEdges": sum(1 for edge in edges if edge["kind"] == "ssh"),
            "maxDepth": max_depth,
            "chains": sum(1 for node in nodes if node["depth"] > 0),
            "cycles": len(cycles),
            "isolated": sum(1 for node in nodes if node["inbound"] == 0 and node["outbound"] == 0),
            "groups": group_counts,
        },
    }
