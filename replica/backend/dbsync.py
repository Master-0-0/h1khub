"""Schema and data synchronization between two database assets."""
from __future__ import annotations

import re
from typing import Any

from db import DatabaseError
from dbsessions import build_alter_sql, build_create_table_sql, table_schema


_TYPE_ALIASES = {
    "int": "integer",
    "int4": "integer",
    "int8": "bigint",
    "int2": "smallint",
    "varchar": "character varying",
    "char": "character",
    "datetime": "timestamp",
    "bool": "boolean",
    "numeric": "decimal",
    "double": "double precision",
    "float": "double precision",
    "blob": "bytea",
    "longblob": "bytea",
    "mediumblob": "bytea",
}


def normalize_type(value: str) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    base = re.sub(r"\(.*\)", "", text).strip()
    base = _TYPE_ALIASES.get(base, base)
    match = re.search(r"\(([^)]*)\)", text)
    if match and base in ("character varying", "character", "decimal", "numeric"):
        return f"{base}({match.group(1)})"
    return base


def normalize_default(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("null", ""):
        return ""
    # generated sequences differ by dialect; treat as a server-side default
    if "nextval" in text.lower():
        return "<sequence>"
    return text.strip("'\"").lower()


def _column_spec(col: dict) -> dict:
    spec = {
        "name": col["name"],
        "type": col.get("type") or "",
        "nullable": bool(col.get("nullable", True)),
        "primary": bool(col.get("primary")),
    }
    if col.get("default") not in (None, ""):
        spec["default"] = col["default"]
    if col.get("comment"):
        spec["comment"] = col["comment"]
    return spec


def usable_index(idx: dict) -> bool:
    return not idx.get("primary") and idx.get("name") not in ("PRIMARY",)


def _table_create_spec(src: dict) -> dict:
    columns = []
    for col in src["columns"]:
        item = _column_spec(col)
        if item["primary"]:
            item["primary"] = True
        columns.append(item)
    indexes = [
        {"name": idx["name"], "columns": idx["columns"], "unique": bool(idx.get("unique"))}
        for idx in src.get("indexes") or []
        if usable_index(idx)
    ]
    return {
        "columns": columns,
        "indexes": indexes,
        "fks": [fk for fk in src.get("foreignKeys") or [] if fk.get("name")],
        "comment": src.get("comment") or "",
    }


def diff_schema(src_adapter, src_db, src_schema, src_table, dst_adapter, dst_db, dst_schema, dst_table) -> dict:
    source = table_schema(src_adapter, src_db, src_schema, src_table)
    target = table_schema(dst_adapter, dst_db, dst_schema, dst_table)
    result: dict[str, Any] = {
        "source": {"table": src_table, "columns": len(source["columns"]), "indexes": len(source.get("indexes") or [])},
        "target": {"table": dst_table, "columns": len(target["columns"]), "indexes": len(target.get("indexes") or [])},
        "exists": bool(target["columns"]),
        "statements": [],
        "changes": [],
        "warnings": [],
    }
    if not target["columns"]:
        spec = _table_create_spec(source)
        result["statements"] = build_create_table_sql(dst_adapter, dst_db, dst_schema, dst_table, spec)
        result["changes"].append({"kind": "create-table", "table": dst_table, "columns": len(source["columns"])})
        return result

    src_cols = {c["name"]: c for c in source["columns"]}
    dst_cols = {c["name"]: c for c in target["columns"]}
    ops: list[dict] = []
    for name, col in src_cols.items():
        if name not in dst_cols:
            ops.append({"kind": "add", "column": _column_spec(col)})
            result["changes"].append({"kind": "add-column", "column": name, "type": col.get("type")})
            continue
        other = dst_cols[name]
        changes = {}
        if normalize_type(col.get("type")) != normalize_type(other.get("type")):
            changes["type"] = col.get("type")
        if bool(col.get("nullable")) != bool(other.get("nullable")):
            changes["nullable"] = bool(col.get("nullable"))
        if normalize_default(col.get("default")) != normalize_default(other.get("default")):
            changes["default"] = col.get("default")
        if (col.get("comment") or "") != (other.get("comment") or ""):
            changes["comment"] = col.get("comment") or ""
        if not changes:
            continue
        spec = {"name": name, "oldName": name}
        if dst_adapter.dialect == "mysql":
            spec.update({"type": col.get("type"), "nullable": bool(col.get("nullable"))})
            if col.get("default") not in (None, ""):
                spec["default"] = col.get("default")
            if col.get("comment"):
                spec["comment"] = col.get("comment")
        else:
            spec.update(changes)
            if dst_adapter.dialect == "sqlite":
                result["warnings"].append(f"SQLite 不支持修改字段 {name}，已跳过")
                continue
        ops.append({"kind": "modify", "column": spec})
        result["changes"].append({"kind": "modify-column", "column": name, "changes": changes})
    for name in dst_cols:
        if name not in src_cols:
            ops.append({"kind": "drop", "name": name})
            result["changes"].append({"kind": "drop-column", "column": name})

    def usable(idx: dict) -> bool:
        return not idx.get("primary") and idx.get("name") not in ("PRIMARY",)

    src_idx = {i["name"]: i for i in source.get("indexes") or [] if usable(i)}
    dst_idx = {i["name"]: i for i in target.get("indexes") or [] if usable(i)}
    for name, idx in src_idx.items():
        if name not in dst_idx:
            ops.append({"kind": "add-index", "index": idx})
            result["changes"].append({"kind": "add-index", "index": name})
        elif idx.get("columns") != dst_idx[name].get("columns") or bool(idx.get("unique")) != bool(dst_idx[name].get("unique")):
            ops.append({"kind": "drop-index", "name": name})
            ops.append({"kind": "add-index", "index": idx})
            result["changes"].append({"kind": "rebuild-index", "index": name})
    for name in dst_idx:
        if name not in src_idx:
            ops.append({"kind": "drop-index", "name": name})
            result["changes"].append({"kind": "drop-index", "index": name})

    src_fk = {fk.get("name"): fk for fk in source.get("foreignKeys") or []}
    dst_fk = {fk.get("name"): fk for fk in target.get("foreignKeys") or []}
    for name, fk in src_fk.items():
        if name not in dst_fk:
            ops.append({"kind": "add-fk", "fk": fk})
            result["changes"].append({"kind": "add-fk", "name": name})
    for name in dst_fk:
        if name not in src_fk:
            ops.append({"kind": "drop-fk", "name": name})
            result["changes"].append({"kind": "drop-fk", "name": name})

    if ops:
        try:
            result["statements"] = build_alter_sql(dst_adapter, dst_db, dst_schema, dst_table, ops)
        except DatabaseError as exc:
            result["warnings"].append(str(exc))
    return result


def apply_schema(src_adapter, src_db, src_schema, src_table, dst_adapter, dst_db, dst_schema, dst_table) -> dict:
    diff = diff_schema(src_adapter, src_db, src_schema, src_table, dst_adapter, dst_db, dst_schema, dst_table)
    executed = []
    for stmt in diff["statements"]:
        dst_adapter.query(stmt, None)
        executed.append(stmt)
    return {"executed": executed, "changes": diff["changes"], "warnings": diff["warnings"]}


def columns_common(src_adapter, dst_adapter, src_db, src_schema, src_table, dst_db, dst_schema, dst_table) -> list[str]:
    src = table_schema(src_adapter, src_db, src_schema, src_table)
    dst = table_schema(dst_adapter, dst_db, dst_schema, dst_table)
    src_names = [c["name"] for c in src["columns"]]
    dst_names = {c["name"] for c in dst["columns"]}
    return [name for name in src_names if name in dst_names]


def preview_data(src_adapter, dst_adapter, source: dict, target: dict) -> dict:
    cols = columns_common(
        src_adapter, dst_adapter,
        source.get("database"), source.get("schema"), source["table"],
        target.get("database"), target.get("schema"), target["table"],
    )
    src_count = _count(src_adapter, source)
    dst_count = _count(dst_adapter, target)
    return {
        "columns": cols,
        "sourceRows": src_count,
        "targetRows": dst_count,
        "onlyInSource": [c for c in _columns(src_adapter, source) if c not in cols],
        "onlyInTarget": [c for c in _columns(dst_adapter, target) if c not in cols],
    }


def _columns(adapter, spec: dict) -> list[str]:
    data = table_schema(adapter, spec.get("database"), spec.get("schema"), spec["table"])
    return [c["name"] for c in data["columns"]]


def _count(adapter, spec: dict) -> int:
    from dbsessions import table_ref

    ref = table_ref(adapter, spec.get("database"), spec.get("schema"), spec["table"])
    cur = adapter._cursor()
    cur.execute(f"SELECT COUNT(*) FROM {ref}")
    return int(cur.fetchone()[0])


def copy_data(
    src_adapter,
    dst_adapter,
    source: dict,
    target: dict,
    mode: str = "append",
    limit: int = 10000,
    where: str | None = None,
) -> dict:
    if mode not in ("append", "replace"):
        raise DatabaseError(f"未知同步模式：{mode}")
    limit = max(1, min(int(limit or 10000), 200000))
    cols = columns_common(
        src_adapter, dst_adapter,
        source.get("database"), source.get("schema"), source["table"],
        target.get("database"), target.get("schema"), target["table"],
    )
    if not cols:
        raise DatabaseError("源表与目标表没有同名列，无法同步")
    from dbsessions import table_ref

    src_ref = table_ref(src_adapter, source.get("database"), source.get("schema"), source["table"])
    dst_ref = table_ref(dst_adapter, target.get("database"), target.get("schema"), target["table"])
    select_cols = ", ".join(src_adapter.quote(c) for c in cols)
    sql = f"SELECT {select_cols} FROM {src_ref}"
    if where:
        sql += f" WHERE {where}"
    sql += f" LIMIT {limit}"
    src_cur = src_adapter._cursor()
    src_cur.execute(sql)
    rows = src_cur.fetchall()
    placeholder = "?" if dst_adapter.dialect == "sqlite" else "%s"
    insert_sql = (
        f"INSERT INTO {dst_ref} ({', '.join(dst_adapter.quote(c) for c in cols)})"
        f" VALUES ({', '.join(placeholder for _ in cols)})"
    )
    clean_rows = []
    for row in rows:
        clean = []
        for value in row:
            if isinstance(value, memoryview):
                value = value.tobytes()
            clean.append(value)
        clean_rows.append(tuple(clean))
    dst_cur = dst_adapter._cursor()
    deleted = 0
    try:
        if mode == "replace":
            dst_cur.execute(f"DELETE FROM {dst_ref}")
            deleted = dst_cur.rowcount if dst_cur.rowcount and dst_cur.rowcount > 0 else 0
        batch = 500
        for start in range(0, len(clean_rows), batch):
            dst_cur.executemany(insert_sql, clean_rows[start:start + batch])
        dst_adapter._conn.commit()
    except Exception as exc:
        try:
            dst_adapter._conn.rollback()
        except Exception:
            pass
        raise DatabaseError(f"数据同步失败：{exc}") from exc
    return {
        "inserted": len(clean_rows),
        "deleted": deleted,
        "mode": mode,
        "columns": cols,
        "sourceRows": _count(src_adapter, source),
        "targetRows": _count(dst_adapter, target),
    }
