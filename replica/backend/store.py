"""Local asset store, encrypted at rest, modelled after HexHub's asset tree."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from util import new_id, now_iso, dumps

# Associated data used for at-rest field encryption.  LEGACY_AAD is the value
# written by builds that shipped before the H1KHub rebrand: it is only used to
# read existing rows, never to seal new ones.
AAD = b"h1khub-asset"
LEGACY_AAD = b"hexhub-asset"

# Asset import/export package tag.  LEGACY_ASSET_PACKAGE_FORMAT keeps export
# files produced before the rebrand importable.
ASSET_PACKAGE_FORMAT = "h1khub-assets"
LEGACY_ASSET_PACKAGE_FORMAT = "hexhub-assets"

# Asset type enum, identical values to the original frontend (asset-C5lH6XV7.js).
ASSET_TYPES = {
    "Dir": 0,
    "SSHPrivateKey": 1,
    "SSH": 2,
    "FTP": 3,
    "DB": 4,
    "QuickCmd": 5,
    "DBQuery": 6,
    "Docker": 7,
    "DockerRegistry": 8,
    "Setting": 9,
    "Hotkey": 10,
    "QuickCmdGroup": 11,
    "RDP": 12,
    "VNC": 13,
    "RepositorySource": 14,
    "Redis": 15,
    "Shell": 16,
    "Serial": 17,
    "Telnet": 18,
    "SSHPasswordPair": 19,
    "SSHTunnel": 20,
    "LLMProviderConfig": 21,
}
TYPE_NAME = {v: k for k, v in ASSET_TYPES.items()}

SECRET_KEYS = {
    "password",
    "privateKey",
    "passphrase",
    "keyPassword",
    "secretAccessKey",
    "accessKeySecret",
    "apiKey",
    "token",
    "proxyPassword",
    "sshPassword",
    "passwd",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "appearance": {
        "theme": "dark",
        "accent": "blue",
        "fontSize": 13,
        "terminalFontSize": 13,
        "terminalFontFamily": "JetBrains Mono, Menlo, Consolas, monospace",
        "language": "zh-CN",
        "uiScale": 100,
    },
    "terminal": {
        "scrollback": 5000,
        "cursorStyle": "block",
        "cursorBlink": True,
        "copyOnSelect": False,
        "rightClick": "menu",
        "webgl": True,
        "confirmClose": True,
        "reconnect": True,
    },
    "editor": {"fontSize": 13, "tabSize": 2, "wordWrap": False, "autoSave": False},
    "agent": {
        "allowSilentFileRead": True,
        "allowSilentFileWrite": False,
        "allowSilentCommand": False,
        "allowSilentReadonlySql": True,
        "blockLocal": False,
        "blockUnreferencedAssetExecution": False,
        "maxToolRounds": 12,
    },
    "ai": {
        "providers": [],
        "activeProviderId": "",
        "activeModel": "",
        "systemPrompt": "",
    },
    "general": {"confirmOnExit": True, "recentLimit": 30},
    # Reserved (round 21): registered so PUT /api/settings can persist them,
    # but no code reads them; the corresponding endpoints stay HTTP 501.
    "update": {"channel": "stable", "env": "production", "manifestUrl": "", "autoCheck": False},
    "lock": {"enabled": False, "idleMinutes": 0, "passwordHash": ""},
    "plugins": {"enabled": False, "dir": ""},
    "mcp": {"enabled": False, "transport": "stdio"},
    "cloud": {"encryption": False},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    type INTEGER NOT NULL,
    name TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0,
    config TEXT NOT NULL DEFAULT '',
    color TEXT,
    alias TEXT,
    favorite INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_parent ON assets(parent_id, sort);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    asset_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    asset_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    meta TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
CREATE TABLE IF NOT EXISTS command_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT,
    source TEXT,
    command TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _walk_secrets(config: Any, prefix: str = ""):
    """Yield dotted paths of secret fields."""
    if isinstance(config, dict):
        for k, v in config.items():
            path = f"{prefix}.{k}" if prefix else k
            if k in SECRET_KEYS and isinstance(v, str) and v:
                yield path
            elif isinstance(v, (dict, list)):
                yield from _walk_secrets(v, path)
    elif isinstance(config, list):
        for idx, item in enumerate(config):
            yield from _walk_secrets(item, f"{prefix}[{idx}]")


def _strip_secrets(config: Any) -> Any:
    if isinstance(config, dict):
        return {k: _strip_secrets(v) for k, v in config.items() if k not in SECRET_KEYS}
    if isinstance(config, list):
        return [_strip_secrets(v) for v in config]
    return config


def _set_path(config: dict, path: str, value: Any) -> None:
    node: Any = config
    parts = path.split(".")
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


class SecretBox:
    def __init__(self, home: str) -> None:
        self.key_path = os.path.join(home, "secret.key")
        os.makedirs(home, mode=0o700, exist_ok=True)
        try:
            os.chmod(home, 0o700)
        except OSError:
            pass
        if os.path.exists(self.key_path):
            with open(self.key_path, "rb") as fh:
                key = fh.read()
            if len(key) != 32:
                raise RuntimeError("corrupted secret.key")
        else:
            key = AESGCM.generate_key(bit_length=256)
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
        self._aead = AESGCM(key)

    def encrypt(self, data: bytes) -> str:
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self._aead.encrypt(nonce, data, AAD)).decode("ascii")

    def decrypt(self, token: str) -> bytes:
        raw = base64.b64decode(token.encode("ascii"))
        try:
            return self._aead.decrypt(raw[:12], raw[12:], AAD)
        except InvalidTag:
            # Row sealed by a pre-rename build; still readable, never re-sealed
            # with the old AAD.
            return self._aead.decrypt(raw[:12], raw[12:], LEGACY_AAD)


class Store:
    def __init__(self, home: str) -> None:
        self.home = home
        os.makedirs(home, exist_ok=True)
        self.box = SecretBox(home)
        self.path = os.path.join(home, "data.db")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        cols = [row[1] for row in self._conn.execute("PRAGMA table_info(assets)")]
        if "alias" not in cols:
            self._conn.execute("ALTER TABLE assets ADD COLUMN alias TEXT")
        self._conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._ensure_defaults()

    # ------------------------------------------------------------------ core
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_defaults(self) -> None:
        with self._lock:
            for key, value in DEFAULT_SETTINGS.items():
                row = self._conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone()
                if not row:
                    self._conn.execute(
                        "INSERT INTO settings(key, value) VALUES(?, ?)",
                        (key, dumps(value)),
                    )
            self._conn.commit()

    # ---------------------------------------------------------------- assets
    def _decode_config(self, blob: str) -> dict:
        if not blob:
            return {}
        try:
            return json.loads(self.box.decrypt(blob).decode("utf-8"))
        except Exception:
            try:  # tolerate rows written before encryption existed
                return json.loads(blob)
            except Exception:
                return {}

    def _encode_config(self, config: dict) -> str:
        return self.box.encrypt(dumps(config or {}).encode("utf-8"))

    def _row_to_asset(self, row: sqlite3.Row, include_secrets: bool = False) -> dict:
        config = self._decode_config(row["config"])
        out = {
            "id": row["id"],
            "parentId": row["parent_id"],
            "type": row["type"],
            "typeName": TYPE_NAME.get(row["type"], "Unknown"),
            "name": row["name"],
            "alias": row["alias"] if "alias" in row.keys() else "",
            "sort": row["sort"],
            "color": row["color"],
            "favorite": bool(row["favorite"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        if include_secrets:
            out["config"] = config
        else:
            out["config"] = _strip_secrets(config)
            out["secrets"] = sorted(set(_walk_secrets(config)))
        return out

    def list_assets(self, include_secrets: bool = False) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM assets ORDER BY sort, name").fetchall()
        return [self._row_to_asset(r, include_secrets) for r in rows]

    def get_asset(self, asset_id: str, include_secrets: bool = False) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        return self._row_to_asset(row, include_secrets) if row else None

    def create_asset(
        self,
        type_id: int,
        name: str,
        parent_id: str | None = None,
        config: dict | None = None,
        color: str | None = None,
        alias: str | None = None,
    ) -> dict:
        asset_id = new_id("a_")
        ts = now_iso()
        with self._lock:
            max_sort = self._conn.execute(
                "SELECT COALESCE(MAX(sort), 0) FROM assets WHERE parent_id IS ?", (parent_id,)
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO assets(id,parent_id,type,name,alias,sort,config,color,favorite,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,0,?,?)",
                (
                    asset_id,
                    parent_id,
                    int(type_id),
                    name,
                    (alias or "").strip() or None,
                    int(max_sort) + 10,
                    self._encode_config(config or {}),
                    color,
                    ts,
                    ts,
                ),
            )
            self._conn.commit()
        return self.get_asset(asset_id)  # type: ignore[return-value]

    def update_asset(self, asset_id: str, patch: dict) -> dict | None:
        current = self.get_asset(asset_id, include_secrets=True)
        if not current:
            return None
        fields = []
        params: list[Any] = []
        if "name" in patch and patch["name"]:
            fields.append("name=?")
            params.append(str(patch["name"])[:200])
        if "parentId" in patch:
            parent = patch["parentId"]
            if parent == asset_id:
                raise ValueError("cannot parent an asset to itself")
            fields.append("parent_id=?")
            params.append(parent)
        if "alias" in patch:
            fields.append("alias=?")
            params.append((str(patch["alias"]).strip() or None) if patch["alias"] is not None else None)
        if "color" in patch:
            fields.append("color=?")
            params.append(patch["color"])
        if "favorite" in patch:
            fields.append("favorite=?")
            params.append(1 if patch["favorite"] else 0)
        if "sort" in patch:
            fields.append("sort=?")
            params.append(int(patch["sort"]))
        if "config" in patch and isinstance(patch["config"], dict):
            merged = self._merge_config(current.get("config") or {}, patch["config"])
            fields.append("config=?")
            params.append(self._encode_config(merged))
        if fields:
            fields.append("updated_at=?")
            params.append(now_iso())
            params.append(asset_id)
            with self._lock:
                self._conn.execute(f"UPDATE assets SET {', '.join(fields)} WHERE id=?", params)
                self._conn.commit()
        return self.get_asset(asset_id)

    def _merge_config(self, old: dict, new: dict) -> dict:
        """Merge config preserving stored secrets when the client omits them."""
        merged = _deep_merge(old, {})
        secrets = set(_walk_secrets(old))

        def walk(node_new: Any, node_old: Any, prefix: str = ""):
            if not isinstance(node_new, dict):
                return node_new
            out = dict(node_old) if isinstance(node_old, dict) else {}
            for k, v in node_new.items():
                path = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    out[k] = walk(v, out.get(k), path)
                elif isinstance(v, list):
                    out[k] = v
                elif k in SECRET_KEYS:
                    if v == "__CLEAR__":
                        out.pop(k, None)
                    elif v is None or v == "":
                        if path in secrets:
                            continue  # keep stored value
                        out.pop(k, None)
                    else:
                        out[k] = v
                else:
                    out[k] = v
            return out

        return walk(new, merged)

    def delete_asset(self, asset_id: str, recursive: bool = True) -> int:
        ids = [asset_id]
        if recursive:
            frontier = [asset_id]
            while frontier:
                placeholders = ",".join("?" for _ in frontier)
                rows = self._conn.execute(
                    f"SELECT id FROM assets WHERE parent_id IN ({placeholders})", frontier
                ).fetchall()
                nxt = [r["id"] for r in rows]
                ids.extend(nxt)
                frontier = nxt
        with self._lock:
            placeholders = ",".join("?" for _ in ids)
            cur = self._conn.execute(f"DELETE FROM assets WHERE id IN ({placeholders})", ids)
            self._conn.commit()
        return cur.rowcount

    def move_asset(self, asset_id: str, parent_id: str | None, before_id: str | None = None) -> None:
        with self._lock:
            if before_id:
                row = self._conn.execute("SELECT sort FROM assets WHERE id=?", (before_id,)).fetchone()
                sort = (row["sort"] if row else 0) - 5
            else:
                sort = self._conn.execute(
                    "SELECT COALESCE(MAX(sort), 0) + 10 FROM assets WHERE parent_id IS ?", (parent_id,)
                ).fetchone()[0]
            self._conn.execute(
                "UPDATE assets SET parent_id=?, sort=?, updated_at=? WHERE id=?",
                (parent_id, sort, now_iso(), asset_id),
            )
            self._conn.commit()

    def duplicate_asset(self, asset_id: str) -> dict | None:
        src = self.get_asset(asset_id, include_secrets=True)
        if not src:
            return None
        return self.create_asset(
            src["type"],
            f"{src['name']} copy",
            src["parentId"],
            self._rekey(src.get("config") or {}),
            src.get("color"),
        )

    @staticmethod
    def _rekey(config: dict) -> dict:
        return json.loads(json.dumps(config))

    def export_assets(self, ids: list[str] | None = None) -> dict:
        assets = self.list_assets(include_secrets=True)
        if ids:
            keep = set(ids)
            # include descendants of selected ids
            changed = True
            while changed:
                changed = False
                for a in assets:
                    if a["parentId"] in keep and a["id"] not in keep:
                        keep.add(a["id"])
                        changed = True
            assets = [a for a in assets if a["id"] in keep]
        return {
            "format": ASSET_PACKAGE_FORMAT,
            "version": 1,
            "exportedAt": now_iso(),
            "assets": assets,
        }

    def import_assets(self, payload: dict, mode: str = "merge") -> dict:
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise ValueError("invalid asset package")
        # Packages exported by pre-rename builds carry the legacy format tag;
        # they stay importable so a user's earlier backups are not orphaned.
        fmt = str(payload.get("format") or "")
        if fmt and fmt not in (ASSET_PACKAGE_FORMAT, LEGACY_ASSET_PACKAGE_FORMAT):
            raise ValueError("unsupported asset package format")
        id_map: dict[str, str] = {}
        existing = {a["name"]: a for a in self.list_assets()}
        count = 0
        # create in parent-first order
        pending = list(assets)
        guard = 0
        while pending and guard < 50:
            guard += 1
            rest = []
            for item in pending:
                parent = item.get("parentId")
                if parent and parent not in id_map:
                    if any(a.get("id") == parent for a in assets):
                        rest.append(item)
                        continue
                    parent = None
                name = str(item.get("name") or "imported")
                if mode == "merge" and parent is None and name in existing:
                    target = existing[name]
                    id_map[item.get("id", "")] = target["id"]
                    continue
                created = self.create_asset(
                    int(item.get("type", 0)),
                    name,
                    id_map.get(parent) if parent else None,
                    item.get("config") or {},
                    item.get("color"),
                )
                id_map[item.get("id", "")] = created["id"]
                count += 1
            pending = rest
        return {"imported": count, "idMap": id_map}

    # -------------------------------------------------------------- settings
    def get_settings(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        out = json.loads(json.dumps(DEFAULT_SETTINGS))
        for row in rows:
            try:
                out[row["key"]] = _deep_merge(out.get(row["key"], {}), json.loads(row["value"]))
            except Exception:
                out[row["key"]] = json.loads(row["value"])
        return out

    def update_settings(self, patch: dict) -> dict:
        with self._lock:
            for key, value in (patch or {}).items():
                if key not in DEFAULT_SETTINGS:
                    continue
                merged = _deep_merge(self.get_settings().get(key, {}), value or {})
                self._conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, dumps(merged)),
                )
            self._conn.commit()
        return self.get_settings()

    # --------------------------------------------------------------- history
    def add_history(self, kind: str, title: str, asset_id: str | None = None, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO history(kind,title,asset_id,detail,created_at) VALUES(?,?,?,?,?)",
                (kind, title[:200], asset_id, detail[:1000], now_iso()),
            )
            self._conn.execute(
                "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 500)"
            )
            self._conn.commit()

    def list_history(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM history ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_history(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    def add_command(self, command: str, asset_id: str | None, source: str = "shell") -> None:
        command = (command or "").strip()
        if not command:
            return
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM command_history WHERE command=? AND (asset_id IS ?) ORDER BY id DESC LIMIT 1",
                (command, asset_id),
            ).fetchone()
            if not exists:
                self._conn.execute(
                    "INSERT INTO command_history(asset_id, source, command, created_at) VALUES(?,?,?,?)",
                    (asset_id, source, command[:2000], now_iso()),
                )
            self._conn.commit()

    def list_commands(self, asset_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            if asset_id:
                rows = self._conn.execute(
                    "SELECT * FROM command_history WHERE asset_id=? ORDER BY id DESC LIMIT ?",
                    (asset_id, int(limit)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM command_history ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ chat store
    def create_chat(self, title: str = "新对话", asset_id: str | None = None) -> dict:
        cid = new_id("c_")
        ts = now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_sessions(id,title,asset_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                (cid, title[:120], asset_id, ts, ts),
            )
            self._conn.commit()
        return {"id": cid, "title": title, "assetId": asset_id, "createdAt": ts, "updatedAt": ts}

    def list_chats(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "assetId": r["asset_id"],
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
            }
            for r in rows
        ]

    def rename_chat(self, chat_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?",
                (title[:120], now_iso(), chat_id),
            )
            self._conn.commit()

    def delete_chat(self, chat_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chat_messages WHERE session_id=?", (chat_id,))
            self._conn.execute("DELETE FROM chat_sessions WHERE id=?", (chat_id,))
            self._conn.commit()

    def add_chat_message(
        self, chat_id: str, role: str, content: str, meta: dict | None = None
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chat_messages(session_id,role,content,meta,created_at) VALUES(?,?,?,?,?)",
                (chat_id, role, content, dumps(meta or {}), now_iso()),
            )
            self._conn.execute(
                "UPDATE chat_sessions SET updated_at=? WHERE id=?", (now_iso(), chat_id)
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_chat_messages(self, chat_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_messages WHERE session_id=? ORDER BY id", (chat_id,)
            ).fetchall()
        out = []
        for r in rows:
            try:
                meta = json.loads(r["meta"] or "{}")
            except Exception:
                meta = {}
            out.append(
                {
                    "id": r["id"],
                    "role": r["role"],
                    "content": r["content"],
                    "meta": meta,
                    "createdAt": r["created_at"],
                }
            )
        return out
