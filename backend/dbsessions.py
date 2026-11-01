"""Persistent database sessions plus schema editing, import, batch and blob helpers.

A *session* keeps one live adapter (and its SSH tunnel) open across HTTP requests so
that `begin / commit / rollback` operate on the same server-side transaction instead
of a fresh connection per request.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import re
import threading
import time
from typing import Any

from db import BLOB_INLINE_LIMIT, DatabaseError, _split_statements
from util import Timer, new_id, now_iso

SESSION_IDLE_SECONDS = 900.0
MAX_SCRIPT_BYTES = 16 * 1024 * 1024
MAX_SCRIPT_STATEMENTS = 5000
MAX_BATCH_ROWS = 2000

_IDENT_RE = re.compile(r"^[^\x00-\x1f]{1,128}$")


def identifier(name: Any) -> str:
    text = str(name or "").strip()
    if not text or not _IDENT_RE.match(text) or "\x00" in text:
        raise DatabaseError(f"非法标识符：{name!r}")
    return text


def safe_default(value: Any) -> str:
    """Accept SQL literals / a small allow-list of functions, reject statement breaks."""
    if value is None:
        return "NULL"
    text = str(value).strip()
    if text.upper() == "NULL":
        return "NULL"
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    if re.fullmatch(r"(?i)(CURRENT_TIMESTAMP|CURRENT_DATE|CURRENT_TIME|NOW\(\)|TRUE|FALSE)", text):
        return text.upper() if text.upper() in ("TRUE", "FALSE") else text
    if re.fullmatch(r"(?i)(nextval|uuid_generate_v4|gen_random_uuid|json_object)\([^;]*\)", text):
        return text
    if ";" in text or "--" in text or "/*" in text:
        raise DatabaseError("默认值包含不允许的 SQL 片段")
    return "'" + text.replace("'", "''") + "'"


def table_ref(adapter, database: str | None, schema: str | None, table: str) -> str:
    name = identifier(table)
    if adapter.dialect == "mysql":
        db = identifier(database or adapter._dbname)
        return f"{adapter.quote(db)}.{adapter.quote(name)}"
    if adapter.dialect == "postgresql":
        sch = identifier(schema or "public")
        return f"{adapter.quote(sch)}.{adapter.quote(name)}"
    db = identifier(database or adapter._dbname or "main")
    return f"{adapter.quote(db)}.{adapter.quote(name)}"


def _column_ddl(adapter, col: dict, *, include_comment: bool = True) -> str:
    name = identifier(col.get("name"))
    ctype = str(col.get("type") or "").strip()
    if not ctype or ";" in ctype or "--" in ctype or "/*" in ctype:
        raise DatabaseError(f"字段 {name} 的类型无效")
    parts = [adapter.quote(name), ctype]
    nullable = col.get("nullable", True)
    if col.get("primary"):
        parts.append("PRIMARY KEY")
    elif not nullable:
        parts.append("NOT NULL")
    default = col.get("default")
    if default not in (None, ""):
        parts.append("DEFAULT " + safe_default(default))
    if col.get("autoIncrement") and adapter.dialect == "mysql":
        parts.append("AUTO_INCREMENT")
    if include_comment and col.get("comment") and adapter.dialect == "mysql":
        parts.append("COMMENT '" + str(col["comment"]).replace("'", "''") + "'")
    return " ".join(parts)


def _pg_alter_column(adapter, ref: str, col: dict) -> list[str]:
    name = identifier(col.get("name"))
    qname = adapter.quote(name)
    out: list[str] = []
    if col.get("oldName") and col["oldName"] != col.get("name"):
        out.append(f"ALTER TABLE {ref} RENAME COLUMN {adapter.quote(identifier(col['oldName']))} TO {qname}")
    if col.get("type"):
        out.append(f"ALTER TABLE {ref} ALTER COLUMN {qname} TYPE {str(col['type']).strip()}")
    if col.get("nullable") is not None:
        out.append(
            f"ALTER TABLE {ref} ALTER COLUMN {qname} "
            + ("DROP NOT NULL" if col.get("nullable") else "SET NOT NULL")
        )
    if "default" in col:
        if col.get("default") not in (None, ""):
            out.append(f"ALTER TABLE {ref} ALTER COLUMN {qname} SET DEFAULT {safe_default(col['default'])}")
        else:
            out.append(f"ALTER TABLE {ref} ALTER COLUMN {qname} DROP DEFAULT")
    if "comment" in col:
        text = str(col.get("comment") or "").replace("'", "''")
        out.append(f"COMMENT ON COLUMN {ref}.{qname} IS '{text}'")
    return out


def build_alter_sql(adapter, database, schema, table, ops: list[dict]) -> list[str]:
    """Translate a UI operation list into executable DDL statements."""
    ref = table_ref(adapter, database, schema, table)
    tsql: list[str] = []
    for op in ops or []:
        kind = (op.get("kind") or "").lower()
        if kind == "add":
            col = op.get("column") or {}
            if adapter.dialect == "mysql":
                stmt = f"ALTER TABLE {ref} ADD COLUMN {_column_ddl(adapter, col)}"
                if col.get("after"):
                    stmt += f" AFTER {adapter.quote(identifier(col['after']))}"
                tsql.append(stmt)
            else:
                tsql.append(f"ALTER TABLE {ref} ADD COLUMN {_column_ddl(adapter, col)}")
                if col.get("comment") and adapter.dialect == "postgresql":
                    tsql.extend(_pg_alter_column(adapter, ref, {"name": col["name"], "comment": col["comment"]}))
        elif kind == "modify":
            col = op.get("column") or {}
            if adapter.dialect == "sqlite":
                raise DatabaseError("SQLite 不支持修改字段类型/约束，请重建表")
            if adapter.dialect == "mysql":
                keyword = "CHANGE COLUMN" if col.get("oldName") and col["oldName"] != col.get("name") else "MODIFY COLUMN"
                if keyword == "CHANGE COLUMN":
                    tsql.append(
                        f"ALTER TABLE {ref} CHANGE COLUMN {adapter.quote(identifier(col['oldName']))} "
                        f"{_column_ddl(adapter, col)}"
                    )
                else:
                    tsql.append(f"ALTER TABLE {ref} MODIFY COLUMN {_column_ddl(adapter, col)}")
            else:
                tsql.extend(_pg_alter_column(adapter, ref, col))
        elif kind == "drop":
            if adapter.dialect == "sqlite":
                tsql.append(f"ALTER TABLE {ref} DROP COLUMN {adapter.quote(identifier(op.get('name')))}")
            else:
                tsql.append(f"ALTER TABLE {ref} DROP COLUMN {adapter.quote(identifier(op.get('name')))}")
        elif kind == "add-index":
            idx = op.get("index") or {}
            name = identifier(idx.get("name"))
            cols = ", ".join(adapter.quote(identifier(c)) for c in idx.get("columns") or [])
            if not cols:
                raise DatabaseError("索引至少需要一列")
            unique = "UNIQUE " if idx.get("unique") else ""
            if adapter.dialect == "mysql":
                tsql.append(f"ALTER TABLE {ref} ADD {unique}INDEX {adapter.quote(name)} ({cols})")
            else:
                tsql.append(f"CREATE {unique}INDEX {adapter.quote(name)} ON {ref} ({cols})")
        elif kind == "drop-index":
            name = identifier(op.get("name"))
            if adapter.dialect == "mysql":
                tsql.append(f"ALTER TABLE {ref} DROP INDEX {adapter.quote(name)}")
            else:
                tsql.append(f"DROP INDEX {adapter.quote(name)}")
        elif kind == "add-fk":
            fk = op.get("fk") or {}
            if adapter.dialect == "sqlite":
                raise DatabaseError("SQLite 不支持在线添加外键，请重建表")
            name = identifier(fk.get("name"))
            cols = ", ".join(adapter.quote(identifier(c)) for c in fk.get("columns") or [])
            ref_table = fk.get("refTable")
            ref_cols = ", ".join(adapter.quote(identifier(c)) for c in fk.get("refColumns") or [])
            if not cols or not ref_table or not ref_cols:
                raise DatabaseError("外键需要本表列、引用表与引用列")
            if adapter.dialect == "mysql":
                ref_full = table_ref(adapter, database, schema, ref_table)
            else:
                ref_full = table_ref(adapter, database, schema, ref_table)
            clause = f"ALTER TABLE {ref} ADD CONSTRAINT {adapter.quote(name)} FOREIGN KEY ({cols}) REFERENCES {ref_full} ({ref_cols})"
            if fk.get("onDelete"):
                clause += f" ON DELETE {_fk_action(fk['onDelete'])}"
            if fk.get("onUpdate"):
                clause += f" ON UPDATE {_fk_action(fk['onUpdate'])}"
            tsql.append(clause)
        elif kind == "drop-fk":
            name = identifier(op.get("name"))
            if adapter.dialect == "mysql":
                tsql.append(f"ALTER TABLE {ref} DROP FOREIGN KEY {adapter.quote(name)}")
            elif adapter.dialect == "postgresql":
                tsql.append(f"ALTER TABLE {ref} DROP CONSTRAINT {adapter.quote(name)}")
            else:
                raise DatabaseError("SQLite 不支持删除外键")
        elif kind == "comment":
            text = str(op.get("text") or "").replace("'", "''")
            if adapter.dialect == "mysql":
                tsql.append(f"ALTER TABLE {ref} COMMENT='{text}'")
            elif adapter.dialect == "postgresql":
                tsql.append(f"COMMENT ON TABLE {ref} IS '{text}'")
        else:
            raise DatabaseError(f"未知的表结构操作：{kind}")
    return tsql


def _fk_action(action: str) -> str:
    text = str(action).strip().upper().replace("_", " ")
    if text not in ("CASCADE", "RESTRICT", "SET NULL", "NO ACTION", "SET DEFAULT"):
        raise DatabaseError(f"非法外键动作：{action}")
    return text


def build_create_table_sql(adapter, database, schema, table, spec: dict) -> list[str]:
    ref = table_ref(adapter, database, schema, table)
    cols = spec.get("columns") or []
    if not cols:
        raise DatabaseError("至少需要一个字段")
    pieces = []
    for col in cols:
        pieces.append("  " + _column_ddl(adapter, col, include_comment=(adapter.dialect == "mysql")))
    pk = [c["name"] for c in cols if c.get("primary") and not (adapter.dialect == "mysql")]
    if pk and len(pk) > 1:
        pieces.append("  PRIMARY KEY (" + ", ".join(adapter.quote(identifier(c)) for c in pk) + ")")
    for fk in spec.get("fks") or []:
        if adapter.dialect == "sqlite":
            break
        cols_sql = ", ".join(adapter.quote(identifier(c)) for c in fk.get("columns") or [])
        rt = table_ref(adapter, database, schema, fk.get("refTable"))
        rc = ", ".join(adapter.quote(identifier(c)) for c in fk.get("refColumns") or [])
        pieces.append(
            f"  CONSTRAINT {adapter.quote(identifier(fk.get('name') or (table + '_fk')))}"
            f" FOREIGN KEY ({cols_sql}) REFERENCES {rt} ({rc})"
        )
    sql = f"CREATE TABLE {ref} (\n" + ",\n".join(pieces) + "\n)"
    if adapter.dialect == "mysql" and spec.get("comment"):
        sql += " COMMENT='" + str(spec["comment"]).replace("'", "''") + "'"
    stmts = [sql]
    if adapter.dialect == "mysql":
        for idx in spec.get("indexes") or []:
            cols_sql = ", ".join(adapter.quote(identifier(c)) for c in idx.get("columns") or [])
            unique = "UNIQUE " if idx.get("unique") else ""
            stmts.append(f"CREATE {unique}INDEX {adapter.quote(identifier(idx.get('name')))} ON {ref} ({cols_sql})")
    else:
        for idx in spec.get("indexes") or []:
            cols_sql = ", ".join(adapter.quote(identifier(c)) for c in idx.get("columns") or [])
            unique = "UNIQUE " if idx.get("unique") else ""
            stmts.append(f"CREATE {unique}INDEX {adapter.quote(identifier(idx.get('name')))} ON {ref} ({cols_sql})")
        if spec.get("comment") and adapter.dialect == "postgresql":
            stmts.append(f"COMMENT ON TABLE {ref} IS '{str(spec['comment']).replace(chr(39), chr(39)*2)}'")
    return stmts


def run_script(adapter, sql: str, *, database=None, stop_on_error=False, transactional=True) -> dict:
    """Execute a .sql script statement by statement and report each outcome."""
    raw = sql.encode("utf-8")
    if len(raw) > MAX_SCRIPT_BYTES:
        raise DatabaseError(f"脚本过大（{len(raw)} 字节，上限 {MAX_SCRIPT_BYTES}）")
    statements = [s for s in _split_statements(sql) if s.strip()]
    if not statements:
        raise DatabaseError("脚本为空")
    if len(statements) > MAX_SCRIPT_STATEMENTS:
        raise DatabaseError(f"语句过多（{len(statements)}，上限 {MAX_SCRIPT_STATEMENTS}）")
    if database and database != adapter._dbname:
        adapter.reconnect(database)
    results: list[dict] = []
    timer = Timer()
    started_tx = False
    if transactional and adapter.dialect != "sqlite":
        # MySQL DDL implicitly commits, so transactions are best-effort there;
        # still useful for pure-DML scripts.
        try:
            adapter.begin()
            started_tx = True
        except DatabaseError:
            started_tx = False
    for index, stmt in enumerate(statements):
        entry = {"index": index + 1, "sql": stmt[:500], "ok": True}
        stmt_timer = Timer()
        try:
            out = adapter.query(stmt, None)
            first = (out.get("results") or [{}])[0]
            entry["affectedRows"] = first.get("affectedRows")
            entry["rowCount"] = first.get("rowCount")
            entry["elapsed"] = stmt_timer.ms()
        except DatabaseError as exc:
            entry.update({"ok": False, "error": str(exc), "elapsed": stmt_timer.ms()})
            results.append(entry)
            if stop_on_error:
                if started_tx:
                    try:
                        adapter.rollback()
                    except Exception:
                        pass
                return {
                    "statements": results,
                    "executed": len(results),
                    "failed": len([r for r in results if not r["ok"]]),
                    "total": len(statements),
                    "rolledBack": True,
                    "elapsed": timer.ms(),
                }
            continue
        results.append(entry)
    if started_tx:
        succeeded = all(r["ok"] for r in results)
        if succeeded:
            try:
                adapter.commit()
            except DatabaseError:
                pass
        else:
            try:
                adapter.rollback()
            except Exception:
                pass
    return {
        "statements": results,
        "executed": len(results),
        "failed": len([r for r in results if not r["ok"]]),
        "total": len(statements),
        "elapsed": timer.ms(),
    }


def batch_rows(adapter, database, schema, table, action: str, rows: list[dict], values: dict | None = None) -> dict:
    if not rows:
        raise DatabaseError("未选择任何行")
    if len(rows) > MAX_BATCH_ROWS:
        raise DatabaseError(f"单次批量操作最多 {MAX_BATCH_ROWS} 行")
    if action not in ("delete", "update"):
        raise DatabaseError(f"未知批量操作：{action}")
    if action == "update" and not values:
        raise DatabaseError("批量更新需要提供字段值")
    timer = Timer()
    affected = 0
    adapter.begin()
    try:
        for keys in rows:
            if not keys:
                raise DatabaseError("行定位键为空")
            if action == "delete":
                affected += int(adapter.delete_row(database, schema, table, keys).get("affected") or 0)
            else:
                affected += int(adapter.update_row(database, schema, table, keys, values).get("affected") or 0)
        adapter.commit()
    except Exception:
        try:
            adapter.rollback()
        except Exception:
            pass
        raise
    return {"affected": affected, "rows": len(rows), "action": action, "elapsed": timer.ms()}


def _where_clause(adapter, keys: dict) -> tuple[str, list]:
    parts, params = [], []
    placeholder = "?" if adapter.dialect == "sqlite" else "%s"
    for key, value in keys.items():
        parts.append(f"{adapter.quote(identifier(key))}={placeholder}")
        params.append(value)
    if not parts:
        raise DatabaseError("缺少行定位键")
    return " AND ".join(parts), params


def read_blob(adapter, database, schema, table, column, keys: dict) -> dict:
    ref = table_ref(adapter, database, schema, table)
    where, params = _where_clause(adapter, keys)
    col = adapter.quote(identifier(column))
    cur = adapter._cursor()
    try:
        cur.execute(f"SELECT {col} FROM {ref} WHERE {where} LIMIT 1", params)
        row = cur.fetchone()
    except Exception as exc:
        raise DatabaseError(f"读取 Blob 失败：{exc}") from exc
    if row is None:
        raise DatabaseError("未找到对应行")
    value = row[0]
    if value is None:
        return {"null": True, "size": 0, "base64": None, "text": None, "hex": None}
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, str):
        value = value.encode("utf-8", "replace")
    if not isinstance(value, (bytes, bytearray)):
        text = str(value)
        return {"null": False, "size": len(text), "base64": base64.b64encode(text.encode()).decode(), "text": text, "hex": text.encode().hex()[:4096], "kind": "text"}
    data = bytes(value)
    printable = sum(1 for b in data[:4096] if 32 <= b < 127 or b in (9, 10, 13))
    ratio = printable / max(1, min(len(data), 4096))
    text_preview = None
    if ratio > 0.85:
        text_preview = data[:8192].decode("utf-8", "replace")
    return {
        "null": False,
        "size": len(data),
        "base64": base64.b64encode(data[:BLOB_INLINE_LIMIT]).decode() if len(data) > 0 else "",
        "truncated": len(data) > BLOB_INLINE_LIMIT,
        "text": text_preview,
        "hex": data[:2048].hex(),
        "kind": "text" if text_preview is not None else "binary",
    }


def blob_bytes(adapter, database, schema, table, column, keys: dict) -> bytes:
    ref = table_ref(adapter, database, schema, table)
    where, params = _where_clause(adapter, keys)
    cur = adapter._cursor()
    cur.execute(f"SELECT {adapter.quote(identifier(column))} FROM {ref} WHERE {where} LIMIT 1", params)
    row = cur.fetchone()
    if row is None or row[0] is None:
        raise DatabaseError("未找到 Blob 数据")
    value = row[0]
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode()
    return bytes(value)


def realtime_info(adapter) -> dict:
    info: dict[str, Any] = {"dialect": adapter.dialect, "collectedAt": now_iso()}
    cur = None if adapter.dialect in ("clickhouse", "mongodb") else adapter._cursor()
    if adapter.dialect == "mysql":
        cur.execute("SHOW GLOBAL STATUS")
        status = {r[0]: r[1] for r in cur.fetchall()}
        wanted = {
            "Uptime": "uptime",
            "Threads_connected": "threadsConnected",
            "Threads_running": "threadsRunning",
            "Queries": "queries",
            "Com_select": "selects",
            "Com_insert": "inserts",
            "Com_update": "updates",
            "Com_delete": "deletes",
            "Innodb_buffer_pool_reads": "bufferPoolReads",
            "Innodb_buffer_pool_read_requests": "bufferPoolReadRequests",
            "Slow_queries": "slowQueries",
            "Aborted_connects": "abortedConnects",
            "Bytes_received": "bytesReceived",
            "Bytes_sent": "bytesSent",
        }
        for src, dst in wanted.items():
            if src in status:
                info[dst] = int(status[src]) if str(status[src]).isdigit() else status[src]
        if "Innodb_buffer_pool_read_requests" in status and int(status["Innodb_buffer_pool_read_requests"] or 0):
            reqs = int(status["Innodb_buffer_pool_read_requests"])
            reads = int(status.get("Innodb_buffer_pool_reads", 0))
            info["bufferPoolHitRate"] = round((1 - reads / reqs) * 100, 2)
        cur.execute("SHOW VARIABLES LIKE 'max_connections'")
        row = cur.fetchone()
        if row:
            info["maxConnections"] = int(row[1])
        cur.execute("SELECT COUNT(*) FROM information_schema.PROCESSLIST")
        info["processCount"] = int(cur.fetchone()[0])
    elif adapter.dialect == "postgresql":
        cur.execute("SELECT count(*), count(*) FILTER (WHERE state='active') FROM pg_stat_activity")
        total, active = cur.fetchone()
        info.update({"connections": int(total), "active": int(active)})
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        info["databaseSize"] = cur.fetchone()[0]
        cur.execute("SELECT sum(xact_commit), sum(xact_rollback) FROM pg_stat_database WHERE datname=current_database()")
        commits, rollbacks = cur.fetchone()
        info.update({"commits": int(commits or 0), "rollbacks": int(rollbacks or 0)})
        cur.execute("SELECT sum(blks_hit), sum(blks_read) FROM pg_stat_database WHERE datname=current_database()")
        hit, read = cur.fetchone()
        if hit and read:
            info["cacheHitRate"] = round(float(hit) / max(1, float(hit) + float(read)) * 100, 2)
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'")
        info["lockWaiters"] = int(cur.fetchone()[0])
        cur.execute("SELECT setting FROM pg_settings WHERE name='max_connections'")
        row = cur.fetchone()
        if row:
            info["maxConnections"] = int(row[0])
    elif adapter.dialect == "clickhouse":
        client = adapter._conn
        info["uptime"] = int(client.execute("SELECT uptime()")[0][0])
        try:
            info["connections"] = int(
                client.execute("SELECT value FROM system.metrics WHERE metric='TCPConnection'")[0][0]
            )
        except Exception:
            pass
        try:
            info["databaseSize"] = int(
                client.execute(
                    "SELECT sum(bytes_on_disk) FROM system.parts WHERE active AND database=%(db)s",
                    {"db": adapter._dbname},
                )[0][0]
                or 0
            )
        except Exception:
            info["databaseSize"] = 0
        wanted = {"Query": "queries", "SelectQuery": "selects", "InsertQuery": "inserts", "FailedQuery": "failedQueries"}
        try:
            for metric, value in client.execute(
                "SELECT event, value FROM system.events WHERE event IN ('Query','SelectQuery','InsertQuery','FailedQuery')"
            ):
                if metric in wanted:
                    info[wanted[metric]] = int(value)
        except Exception:
            pass
        try:
            info["processCount"] = int(client.execute("SELECT count() FROM system.processes")[0][0])
        except Exception:
            pass
    elif adapter.dialect == "mongodb":
        status = adapter._conn.admin.command("serverStatus")
        connections = status.get("connections") or {}
        info["connections"] = int(connections.get("current") or 0)
        info["availableConnections"] = int(connections.get("available") or 0)
        info["uptime"] = int(status.get("uptime") or 0)
        counters = status.get("opcounters") or {}
        info.update(
            {
                "queries": int(counters.get("query") or 0),
                "inserts": int(counters.get("insert") or 0),
                "updates": int(counters.get("update") or 0),
                "deletes": int(counters.get("delete") or 0),
            }
        )
        try:
            stats = adapter._db().command("dbStats")
            info["databaseSize"] = int(stats.get("dataSize") or 0)
            info["objects"] = int(stats.get("objects") or 0)
            info["collections"] = int(stats.get("collections") or 0)
            info["indexes"] = int(stats.get("indexes") or 0)
        except Exception:
            pass
    elif adapter.dialect == "sqlserver":
        # DMV access needs VIEW SERVER STATE; each probe degrades to "unknown".
        for key, sql in (
            ("connections", "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1"),
            ("active", "SELECT COUNT(*) FROM sys.dm_exec_requests WHERE session_id <> @@SPID"),
            ("lockWaiters", "SELECT COUNT(*) FROM sys.dm_os_waiting_tasks WHERE waiting_task_address IS NOT NULL"),
            ("databaseSize", "SELECT CAST(SUM(size) AS bigint) * 8192 FROM sys.master_files WHERE database_id = DB_ID()"),
            ("uptime", "SELECT DATEDIFF(SECOND, sqlserver_start_time, GETDATE()) FROM sys.dm_os_sys_info"),
        ):
            try:
                cur.execute(sql)
                row = cur.fetchone()
                if row and row[0] is not None:
                    info[key] = int(row[0])
            except Exception:
                pass
        try:
            cur.execute(
                "SELECT cntr_value FROM sys.dm_os_performance_counters"
                " WHERE counter_name = 'Batch Requests/sec' AND instance_name = ''"
            )
            row = cur.fetchone()
            if row:
                info["batchRequests"] = int(row[0] or 0)
        except Exception:
            pass
        try:
            cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(64))")
            row = cur.fetchone()
            if row and row[0]:
                info["version"] = str(row[0])
        except Exception:
            pass
    elif adapter.dialect == "oracle":
        for key, sql in (
            ("connections", "SELECT COUNT(*) FROM v$session WHERE type = 'USER'"),
            ("active", "SELECT COUNT(*) FROM v$session WHERE type = 'USER' AND status = 'ACTIVE'"),
            ("databaseSize", "SELECT SUM(bytes) FROM user_segments"),
            ("processCount", "SELECT COUNT(*) FROM v$process"),
        ):
            try:
                cur.execute(sql)
                row = cur.fetchone()
                if row and row[0] is not None:
                    info[key] = int(row[0])
            except Exception:
                pass
        try:
            cur.execute(
                "SELECT NVL(SUM(CASE WHEN n.name = 'session logical reads' THEN s.value END), 0),"
                " NVL(SUM(CASE WHEN n.name = 'user commits' THEN s.value END), 0)"
                " FROM v$sesstat s JOIN v$statname n ON n.statistic# = s.statistic#"
            )
            row = cur.fetchone()
            if row:
                info["logicalReads"] = int(row[0] or 0)
                info["commits"] = int(row[1] or 0)
        except Exception:
            pass
    elif adapter.dialect == "dameng":
        for key, sql in (
            ("connections", "SELECT COUNT(*) FROM v$sessions"),
            ("active", "SELECT COUNT(*) FROM v$sessions WHERE state = 'ACTIVE'"),
        ):
            try:
                cur.execute(sql)
                row = cur.fetchone()
                if row and row[0] is not None:
                    info[key] = int(row[0])
            except Exception:
                pass
    else:
        cur.execute("PRAGMA page_count")
        pages = int(cur.fetchone()[0])
        cur.execute("PRAGMA page_size")
        size = int(cur.fetchone()[0])
        info.update({"pageCount": pages, "pageSize": size, "databaseSize": pages * size})
        cur.execute("PRAGMA journal_mode")
        info["journalMode"] = cur.fetchone()[0]
        cur.execute("PRAGMA freelist_count")
        info["freePages"] = int(cur.fetchone()[0])
        cur.execute("PRAGMA cache_size")
        info["cacheSize"] = int(cur.fetchone()[0])
    return info


def processlist(adapter) -> list[dict]:
    cur = None if adapter.dialect in ("clickhouse", "mongodb") else adapter._cursor()
    rows: list[dict] = []
    if adapter.dialect == "clickhouse":
        try:
            for query_id, user, addr, db, kind, elapsed, query in adapter._conn.execute(
                "SELECT query_id, user, address, initial_database, query_kind,"
                " formatReadableTimeDelta(elapsed), query FROM system.processes"
            ):
                rows.append(
                    {
                        "id": query_id, "user": user, "host": str(addr), "db": db,
                        "command": kind, "time": elapsed, "state": "running", "info": query,
                    }
                )
        except Exception:
            pass
        return rows
    if adapter.dialect == "mongodb":
        try:
            ops = adapter._conn.admin.aggregate(
                [
                    {"$currentOp": {"allUsers": True, "idleSessions": False}},
                    {"$limit": 50},
                ]
            )
            for op in ops:
                rows.append(
                    {
                        "id": str(op.get("opid") or ""),
                        "user": str(op.get("effectiveUsers") or ""),
                        "host": str(op.get("client") or ""),
                        "db": str(op.get("ns") or ""),
                        "command": str(op.get("op") or ""),
                        "time": int((op.get("microsecs_running") or 0) / 1_000_000),
                        "state": str(op.get("desc") or ""),
                        "info": str(op.get("command") or "")[:500],
                    }
                )
        except Exception:
            pass
        return rows
    if adapter.dialect == "mysql":
        cur.execute(
            "SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE, LEFT(COALESCE(INFO,''),500)"
            " FROM information_schema.PROCESSLIST ORDER BY TIME DESC"
        )
        for r in cur.fetchall():
            rows.append(
                {
                    "id": r[0], "user": r[1], "host": r[2], "db": r[3], "command": r[4],
                    "time": r[5], "state": r[6], "info": r[7],
                }
            )
    elif adapter.dialect == "postgresql":
        cur.execute(
            "SELECT pid, usename, client_addr::text, datname, state,"
            " EXTRACT(EPOCH FROM (now()-query_start))::int, wait_event_type, LEFT(query,500)"
            " FROM pg_stat_activity WHERE pid <> pg_backend_pid() ORDER BY query_start NULLS LAST"
        )
        for r in cur.fetchall():
            rows.append(
                {
                    "id": r[0], "user": r[1], "host": r[2], "db": r[3], "command": r[4],
                    "time": r[5], "state": r[6], "info": r[7],
                }
            )
    if adapter.dialect == "sqlserver":
        try:
            cur.execute(
                "SELECT s.session_id, s.login_name, s.host_name, DB_NAME(s.database_id),"
                " COALESCE(r.status, s.status),"
                " DATEDIFF(SECOND, r.start_time, GETDATE()), r.wait_type,"
                " LEFT(COALESCE(r.command, ''), 200), LEFT(COALESCE(t.text, ''), 500)"
                " FROM sys.dm_exec_sessions s"
                " LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id"
                " OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t"
                " WHERE s.is_user_process = 1 ORDER BY r.total_elapsed_time DESC"
            )
            for r in cur.fetchall():
                rows.append(
                    {
                        "id": r[0], "user": r[1], "host": r[2], "db": r[3],
                        "command": r[7] or r[4] or "",
                        "time": int(r[5] or 0), "state": r[6] or r[4] or "", "info": r[8] or "",
                    }
                )
        except Exception:
            pass
        return rows
    if adapter.dialect == "oracle":
        try:
            cur.execute(
                "SELECT s.sid, s.username, s.machine, s.service_name, s.status,"
                " NVL(s.last_call_et, 0), s.event, s.program, s.sql_id, s.status"
                " FROM v$session s WHERE s.type = 'USER' ORDER BY s.last_call_et DESC"
            )
            for r in cur.fetchall():
                rows.append(
                    {
                        "id": r[0], "user": r[1], "host": r[2], "db": r[3],
                        "command": r[7] or "", "time": int(r[5] or 0),
                        "state": r[6] or r[4] or "", "info": f"sql_id={r[8]}" if r[8] else "",
                    }
                )
        except Exception:
            pass
        return rows
    if adapter.dialect == "dameng":
        try:
            cur.execute(
                "SELECT sess_id, user_name, clnt_host, state, sql_text FROM v$sessions"
            )
            for r in cur.fetchall():
                rows.append(
                    {
                        "id": r[0], "user": r[1], "host": r[2], "db": adapter._dbname,
                        "command": "", "time": 0, "state": r[3] or "", "info": str(r[4] or "")[:500],
                    }
                )
        except Exception:
            pass
        return rows
    else:
        rows.append({"id": 0, "user": "sqlite", "host": "local", "db": adapter._dbname, "command": "self", "time": 0, "state": "connected", "info": ""})
    return rows


# --------------------------------------------------------------------------
# Session manager
# --------------------------------------------------------------------------
class DbSession:
    def __init__(self, asset_id: str, adapter) -> None:
        self.id = new_id("dbs")
        self.asset_id = asset_id
        self.adapter = adapter
        self.created_at = time.time()
        self.last_used = time.time()
        self.lock = threading.RLock()
        self.autocommit = True
        self.in_tx = False
        self.active_pid = 0

    def touch(self) -> None:
        self.last_used = time.time()

    def status(self) -> dict:
        return {
            "id": self.id,
            "assetId": self.asset_id,
            "dialect": self.adapter.dialect,
            "autocommit": self.autocommit,
            "inTransaction": self.in_tx,
            "createdAt": self.created_at,
            "idleSeconds": int(time.time() - self.last_used),
        }

    def close(self) -> None:
        try:
            if self.in_tx:
                self.adapter.rollback()
        except Exception:
            pass
        try:
            self.adapter.close()
        except Exception:
            pass


class DbSessionManager:
    def __init__(self, hub, idle_seconds: float = SESSION_IDLE_SECONDS) -> None:
        self.hub = hub
        self.sessions: dict[str, DbSession] = {}
        self.idle_seconds = idle_seconds

    async def open(self, asset_id: str, autocommit: bool = True) -> DbSession:
        adapter = await self.hub.open_db(asset_id)
        session = DbSession(asset_id, adapter)
        try:
            if not autocommit:
                adapter.set_autocommit(False)
                session.autocommit = False
        except DatabaseError:
            adapter.close()
            raise
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> DbSession:
        session = self.sessions.get(session_id)
        if not session:
            raise DatabaseError("数据库会话不存在或已过期，请重新连接")
        session.touch()
        return session

    def close(self, session_id: str) -> bool:
        session = self.sessions.pop(session_id, None)
        if session:
            session.close()
            return True
        return False

    def close_all(self) -> None:
        for session in list(self.sessions.values()):
            session.close()
        self.sessions.clear()

    def sweep(self) -> int:
        now = time.time()
        expired = [sid for sid, s in self.sessions.items() if now - s.last_used > self.idle_seconds]
        for sid in expired:
            self.close(sid)
        return len(expired)


def table_schema(adapter, database, schema, table) -> dict:
    """Return columns, indexes, foreign keys and table comment for the schema editor."""
    columns: list[dict] = []
    indexes: list[dict] = []
    foreign_keys: list[dict] = []
    comment = ""
    if adapter.dialect == "clickhouse":
        columns = adapter._describe(database or adapter._dbname, table)
        return {
            "table": table, "database": database, "schema": schema, "dialect": adapter.dialect,
            "columns": columns, "indexes": [], "foreignKeys": [], "comment": "",
            "primaryKey": [c["name"] for c in columns if c.get("primary")],
        }
    if adapter.dialect == "mongodb":
        columns = adapter._columns(database, table)
        return {
            "table": table, "database": database, "schema": schema, "dialect": adapter.dialect,
            "columns": columns, "indexes": [], "foreignKeys": [], "comment": "文档集合：字段按样本文档推断",
            "primaryKey": ["_id"],
        }
    if adapter.dialect == "sqlserver":
        columns = adapter._columns(schema, table)
        cur = adapter._cursor()
        ref = adapter._object_ref(schema or "dbo", table)
        try:
            cur.execute(
                "SELECT i.name, i.is_unique, i.is_primary_key, c.name, ic.key_ordinal"
                " FROM sys.indexes i"
                " JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id"
                " JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id"
                " WHERE i.object_id = OBJECT_ID(%s) ORDER BY i.name, ic.key_ordinal",
                (ref,),
            )
            idx_map: dict[str, dict] = {}
            for iname, uniq, prim, cname, _ord in cur.fetchall():
                idx = idx_map.setdefault(
                    iname,
                    {"name": iname, "unique": bool(uniq), "primary": bool(prim), "columns": []},
                )
                idx["columns"].append(cname)
            indexes = list(idx_map.values())
        except Exception:
            indexes = []
        try:
            cur.execute(
                "SELECT fk.name, pc.name, rt.name, rc.name FROM sys.foreign_keys fk"
                " JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id"
                " JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id"
                " JOIN sys.tables rt ON rt.object_id = fkc.referenced_object_id"
                " JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id"
                " WHERE fk.parent_object_id = OBJECT_ID(%s) ORDER BY fk.name, fkc.constraint_column_id",
                (ref,),
            )
            fk_map: dict[str, dict] = {}
            for fname, pcol, rtable, rcol in cur.fetchall():
                fk = fk_map.setdefault(
                    fname,
                    {"name": fname, "columns": [], "refTable": rtable, "refColumns": [], "onDelete": "", "onUpdate": ""},
                )
                fk["columns"].append(pcol)
                fk["refColumns"].append(rcol)
            foreign_keys = list(fk_map.values())
        except Exception:
            foreign_keys = []
        return {
            "table": table, "database": database, "schema": schema, "dialect": adapter.dialect,
            "columns": columns, "indexes": indexes, "foreignKeys": foreign_keys, "comment": comment,
            "primaryKey": [c["name"] for c in columns if c.get("primary")],
        }
    if adapter.dialect in ("oracle", "dameng"):
        columns = adapter._columns(schema, table)
        cur = adapter._cursor()
        owner = (schema or adapter._current_schema()).upper()
        ph1, ph2 = ("?", "?") if adapter.dialect == "dameng" else (":1", ":2")
        try:
            cur.execute(
                "SELECT i.index_name, i.uniqueness, c.column_name, c.column_position"
                " FROM all_indexes i JOIN all_ind_columns c"
                " ON c.index_owner = i.owner AND c.index_name = i.index_name"
                f" WHERE i.table_owner = {ph1} AND i.table_name = {ph2}"
                " ORDER BY i.index_name, c.column_position",
                (owner, table.upper()),
            )
            idx_map = {}
            for iname, uniq, cname, _pos in cur.fetchall():
                idx = idx_map.setdefault(
                    iname,
                    {"name": iname, "unique": str(uniq).upper().startswith("UNIQUE"),
                     "primary": str(iname).upper().startswith("PK"), "columns": []},
                )
                idx["columns"].append(cname)
            indexes = list(idx_map.values())
        except Exception:
            indexes = []
        try:
            cur.execute(
                "SELECT c.constraint_name, cc.column_name, r.table_name, rcc.column_name"
                " FROM all_constraints c"
                " JOIN all_cons_columns cc ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name"
                " JOIN all_constraints r ON r.owner = c.r_owner AND r.constraint_name = c.r_constraint_name"
                " JOIN all_cons_columns rcc ON rcc.owner = r.owner AND rcc.constraint_name = r.constraint_name"
                " AND rcc.position = cc.position"
                f" WHERE c.constraint_type = 'R' AND c.owner = {ph1} AND c.table_name = {ph2}",
                (owner, table.upper()),
            )
            fk_map = {}
            for fname, pcol, rtable, rcol in cur.fetchall():
                fk = fk_map.setdefault(
                    fname,
                    {"name": fname, "columns": [], "refTable": rtable, "refColumns": [], "onDelete": "", "onUpdate": ""},
                )
                fk["columns"].append(pcol)
                fk["refColumns"].append(rcol)
            foreign_keys = list(fk_map.values())
        except Exception:
            foreign_keys = []
        return {
            "table": table, "database": database, "schema": schema, "dialect": adapter.dialect,
            "columns": columns, "indexes": indexes, "foreignKeys": foreign_keys, "comment": comment,
            "primaryKey": [c["name"] for c in columns if c.get("primary")],
        }
    cur = adapter._cursor()
    if adapter.dialect == "mysql":
        db = database or adapter._dbname
        columns = adapter._columns(db, table)
        cur.execute(
            "SELECT TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (db, table),
        )
        row = cur.fetchone()
        comment = (row[0] if row else "") or ""
        cur.execute(
            "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME, SEQ_IN_INDEX FROM information_schema.STATISTICS"
            " WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (db, table),
        )
        grouped: dict[str, dict] = {}
        for name, non_unique, column, seq in cur.fetchall():
            entry = grouped.setdefault(name, {"name": name, "unique": not int(non_unique), "columns": []})
            entry["columns"].append(column)
        indexes = list(grouped.values())
        cur.execute(
            """
            SELECT k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME,
                   k.ORDINAL_POSITION, r.DELETE_RULE, r.UPDATE_RULE
            FROM information_schema.KEY_COLUMN_USAGE k
            LEFT JOIN information_schema.REFERENTIAL_CONSTRAINTS r
              ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME
            WHERE k.TABLE_SCHEMA=%s AND k.TABLE_NAME=%s AND k.REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY k.CONSTRAINT_NAME, k.ORDINAL_POSITION
            """,
            (db, table),
        )
        fk_map: dict[str, dict] = {}
        for name, column, ref_table, ref_col, pos, del_rule, upd_rule in cur.fetchall():
            fk = fk_map.setdefault(
                name,
                {"name": name, "columns": [], "refTable": ref_table, "refColumns": [], "onDelete": del_rule, "onUpdate": upd_rule},
            )
            fk["columns"].append(column)
            fk["refColumns"].append(ref_col)
        foreign_keys = list(fk_map.values())
    elif adapter.dialect == "postgresql":
        sch = schema or "public"
        columns = adapter._columns(sch, table)
        cur.execute(
            "SELECT obj_description((quote_ident(%s) || '.' || quote_ident(%s))::regclass, 'pg_class')",
            (sch, table),
        )
        row = cur.fetchone()
        comment = (row[0] if row else "") or ""
        cur.execute(
            """
            SELECT i.relname AS index_name, ix.indisunique, ix.indisprimary,
                   array_agg(a.attname ORDER BY k.ord) AS columns
            FROM pg_index ix
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
            WHERE n.nspname=%s AND t.relname=%s
            GROUP BY i.relname, ix.indisunique, ix.indisprimary
            ORDER BY i.relname
            """,
            (sch, table),
        )
        for name, unique, primary, cols in cur.fetchall():
            indexes.append({"name": name, "unique": bool(unique), "columns": list(cols), "primary": bool(primary)})
        cur.execute(
            """
            SELECT tc.constraint_name, kcu.column_name, ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = tc.constraint_name AND kcu.constraint_schema = tc.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name AND ccu.constraint_schema = tc.constraint_schema
            WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema=%s AND tc.table_name=%s
            ORDER BY tc.constraint_name
            """,
            (sch, table),
        )
        fk_map = {}
        for name, column, ref_table, ref_col in cur.fetchall():
            fk = fk_map.setdefault(name, {"name": name, "columns": [], "refTable": ref_table, "refColumns": [], "onDelete": "", "onUpdate": ""})
            fk["columns"].append(column)
            fk["refColumns"].append(ref_col)
        foreign_keys = list(fk_map.values())
    else:
        db = database or adapter._dbname or "main"
        columns = adapter._table_info(db, table)
        cur.execute(f"PRAGMA {adapter.quote(db)}.index_list({adapter._quote_string(table)})")
        for seq, name, unique, origin, partial in cur.fetchall():
            cur.execute(f"PRAGMA {adapter.quote(db)}.index_info({adapter._quote_string(name)})")
            cols = [r[2] for r in cur.fetchall()]
            indexes.append({"name": name, "unique": bool(unique), "columns": cols, "primary": origin == "pk"})
        cur.execute(f"PRAGMA {adapter.quote(db)}.foreign_key_list({adapter._quote_string(table)})")
        fk_map = {}
        for row in cur.fetchall():
            fk_id, seq, ref_table, from_col, to_col = row[0], row[1], row[2], row[3], row[4]
            fk = fk_map.setdefault(fk_id, {"name": f"fk_{table}_{fk_id}", "columns": [], "refTable": ref_table, "refColumns": [], "onDelete": row[6], "onUpdate": row[5]})
            fk["columns"].append(from_col)
            fk["refColumns"].append(to_col or "")
        foreign_keys = list(fk_map.values())
    return {
        "table": table,
        "database": database,
        "schema": schema,
        "dialect": adapter.dialect,
        "columns": columns,
        "indexes": indexes,
        "foreignKeys": foreign_keys,
        "comment": comment,
        "primaryKey": [c["name"] for c in columns if c.get("primary")],
    }
