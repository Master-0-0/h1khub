"""File snapshots for AI write_file, enabling diff preview and rollback."""
from __future__ import annotations

import json
import os
import time

from util import new_id, now_iso


def _base(home: str) -> str:
    path = os.path.join(home, "backups")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def snapshot(home: str, asset_id: str | None, path: str, old_content: str | None) -> str:
    base = _base(home)
    backup_id = time.strftime("%Y%m%d-%H%M%S-") + new_id("bk")[:10]
    meta_path = os.path.join(base, backup_id + ".json")
    meta = {
        "id": backup_id,
        "assetId": asset_id or None,
        "path": path,
        "createdAt": now_iso(),
        "existed": old_content is not None,
    }
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    os.chmod(meta_path, 0o600)
    if old_content is not None:
        data_path = os.path.join(base, backup_id + ".bak")
        with open(data_path, "w", encoding="utf-8") as fh:
            fh.write(old_content)
        os.chmod(data_path, 0o600)
    return backup_id


def _meta(home: str, backup_id: str) -> dict | None:
    backup_id = os.path.basename(str(backup_id))
    path = os.path.join(_base(home), backup_id + ".json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def list_backups(home: str, limit: int = 50) -> list[dict]:
    base = _base(home)
    items = []
    for name in sorted(os.listdir(base), reverse=True):
        if not name.endswith(".json"):
            continue
        meta = _meta(home, name[:-5])
        if meta:
            items.append(meta)
        if len(items) >= limit:
            break
    return items


def restore(home: str, backup_id: str) -> tuple[dict, str | None]:
    meta = _meta(home, backup_id)
    if not meta:
        raise FileNotFoundError("备份不存在")
    data_path = os.path.join(_base(home), os.path.basename(str(backup_id)) + ".bak")
    content = None
    if os.path.isfile(data_path):
        with open(data_path, encoding="utf-8") as fh:
            content = fh.read()
    return meta, content
