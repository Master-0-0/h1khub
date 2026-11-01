"""Database adapters (MySQL / MariaDB, PostgreSQL, SQLite) with a shared surface."""
from __future__ import annotations

import functools
import os
import re
import sqlite3
import time
from typing import Any

from util import Timer, jsonable, now_iso, parse_bool

ROW_LIMIT = 500
QUERY_ROW_LIMIT = 2000
BLOB_INLINE_LIMIT = 262144


class DatabaseError(Exception):
    pass


def _q_mysql(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _q_std(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script on top-level semicolons, respecting quotes/comments."""
    out, buf = [], []
    i, n = 0, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == quote:
                if nxt == quote:
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None
            elif ch == "\\" and quote in ("'", '"'):
                if nxt:
                    buf.append(nxt)
                    i += 2
                    continue
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            if end < 0:
                break
            buf.append(sql[i : end + 1])
            i = end + 1
            continue
        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            end = n if end < 0 else end + 2
            buf.append(sql[i:end])
            i = end
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


class BaseAdapter:
    dialect = "base"
    default_port = 0
    placeholder = "%s"
    quote = staticmethod(_q_std)

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.tunnel_addr: tuple[str, int] | None = None
        self.tunnel_handle = None
        self._conn = None
        self._dbname = cfg.get("database") or ""
        self.manual_tx = False
        self._in_tx = False

    # lifecycle ------------------------------------------------------------
    def connect(self, dbname: str | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        if self.tunnel_handle:
            try:
                self.tunnel_handle.stop()
            except Exception:
                pass
            self.tunnel_handle = None

    def reconnect(self, dbname: str) -> None:
        self.close()
        self.connect(dbname)

    # transactions ---------------------------------------------------------
    def set_autocommit(self, enabled: bool) -> None:
        """Switch the underlying connection between autocommit and manual commit."""
        self.manual_tx = not enabled
        if enabled:
            self._in_tx = False
        if self._conn is not None:
            try:
                if self.dialect == "sqlite":
                    self._conn.isolation_level = None
                    if enabled and self._in_tx:
                        self._conn.commit()
                elif self.dialect == "postgresql":
                    self._conn.autocommit = enabled
                elif self.dialect == "duckdb":
                    if enabled and self._in_tx:
                        self._conn.commit()
                else:
                    self._conn.autocommit(enabled)
            except Exception as exc:  # pragma: no cover - driver specific
                raise DatabaseError(f"切换自动提交失败：{exc}") from exc

    def begin(self) -> dict:
        try:
            self.set_autocommit(False)
            if not self._in_tx:
                cur = self._conn.cursor()
                cur.execute("BEGIN")
                self._in_tx = True
        except Exception as exc:
            raise DatabaseError(f"开启事务失败：{exc}") from exc
        return {"inTransaction": True}

    def commit(self) -> dict:
        try:
            assert self._conn is not None
            self._conn.commit()
            self._in_tx = False
        except Exception as exc:
            raise DatabaseError(f"提交事务失败：{exc}") from exc
        return {"inTransaction": False}

    def rollback(self) -> dict:
        try:
            assert self._conn is not None
            self._conn.rollback()
            self._in_tx = False
        except Exception as exc:
            raise DatabaseError(f"回滚事务失败：{exc}") from exc
        return {"inTransaction": False}

    # metadata -------------------------------------------------------------
    def list_databases(self) -> list[dict]:
        raise NotImplementedError

    def list_schemas(self, database: str | None = None) -> list[str]:
        return []

    def list_objects(self, database: str | None, schema: str | None) -> dict:
        raise NotImplementedError

    def table_detail(
        self,
        database: str | None,
        schema: str | None,
        table: str,
        limit: int = ROW_LIMIT,
        offset: int = 0,
        order_by: str | None = None,
        order_dir: str = "ASC",
        where: str | None = None,
    ) -> dict:
        raise NotImplementedError

    def ddl(self, database: str | None, schema: str | None, table: str) -> str:
        raise NotImplementedError

    def query(self, sql: str, database: str | None = None) -> dict:
        raise NotImplementedError

    def update_row(
        self,
        database: str | None,
        schema: str | None,
        table: str,
        keys: dict,
        values: dict,
    ) -> dict:
        raise NotImplementedError

    def insert_row(self, database: str | None, schema: str | None, table: str, values: dict) -> dict:
        raise NotImplementedError

    def delete_row(self, database: str | None, schema: str | None, table: str, keys: dict) -> dict:
        raise NotImplementedError

    def explain(self, sql: str, database: str | None = None) -> dict:
        raise NotImplementedError

    # helpers --------------------------------------------------------------
    def _qualified(self, database: str | None, schema: str | None, table: str) -> str:
        raise NotImplementedError

    def test(self) -> dict:
        timer = Timer()
        self.connect(self._dbname or None)
        version = self.version()
        return {"ok": True, "version": version, "elapsed": timer.ms()}

    def version(self) -> str:
        return ""

    @staticmethod
    def _readonly_sql(sql: str) -> bool:
        head = sql.strip().split(None, 1)[0].lower() if sql.strip() else ""
        return head in ("select", "show", "describe", "desc", "explain", "with", "pragma")


# --------------------------------------------------------------------- MySQL
class MySQLAdapter(BaseAdapter):
    dialect = "mysql"

    def backend_pid(self) -> int:
        try:
            return int(self._conn.thread_id())
        except Exception:
            return 0

    def cancel_pid(self, pid: int) -> None:
        cur = self._cursor()
        cur.execute(f"KILL QUERY {int(pid)}")
    default_port = 3306
    quote = staticmethod(_q_mysql)

    def connect(self, dbname: str | None = None) -> None:
        import pymysql
        from pymysql.constants import CLIENT

        host = self.cfg.get("host") or "127.0.0.1"
        port = int(self.cfg.get("port") or 3306)
        if self.tunnel_addr:
            host, port = self.tunnel_addr
        try:
            self._conn = pymysql.connect(
                host=host,
                port=port,
                user=self.cfg.get("username") or "root",
                password=self.cfg.get("password") or "",
                database=dbname or None,
                charset=self.cfg.get("charset") or "utf8mb4",
                connect_timeout=10,
                read_timeout=300,
                write_timeout=60,
                autocommit=True,
                cursorclass=pymysql.cursors.Cursor,
                client_flag=CLIENT.MULTI_STATEMENTS,
                ssl={"ssl": {}} if parse_bool(self.cfg.get("ssl")) else None,
            )
        except Exception as exc:
            raise DatabaseError(f"MySQL 连接失败：{exc}") from exc
        self._dbname = dbname or self.cfg.get("database") or ""

    def _cursor(self):
        assert self._conn is not None
        return self._conn.cursor()

    def version(self) -> str:
        cur = self._cursor()
        cur.execute("SELECT VERSION()")
        return str(cur.fetchone()[0])

    def list_databases(self) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            "SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME FROM information_schema.SCHEMATA"
            " ORDER BY SCHEMA_NAME"
        )
        rows = cur.fetchall()
        sizes: dict[str, int] = {}
        try:
            cur.execute(
                "SELECT TABLE_SCHEMA, COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0)"
                " FROM information_schema.TABLES GROUP BY TABLE_SCHEMA"
            )
            sizes = {r[0]: int(r[1] or 0) for r in cur.fetchall()}
        except Exception:
            pass
        return [
            {"name": r[0], "kind": "database", "charset": r[1], "size": sizes.get(r[0], 0)}
            for r in rows
        ]

    def list_objects(self, database: str | None, schema: str | None) -> dict:
        db = database or self._dbname
        if not db:
            raise DatabaseError("未选择数据库")
        cur = self._cursor()
        cur.execute(
            "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS, TABLE_COMMENT"
            " FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
            (db,),
        )
        tables, views = [], []
        for name, ttype, engine, rows, comment in cur.fetchall():
            item = {"name": name, "engine": engine, "rows": int(rows or 0), "comment": comment or ""}
            (views if ttype == "VIEW" else tables).append(item)
        routines = []
        try:
            cur.execute(
                "SELECT ROUTINE_NAME, ROUTINE_TYPE FROM information_schema.ROUTINES"
                " WHERE ROUTINE_SCHEMA=%s ORDER BY ROUTINE_TYPE, ROUTINE_NAME",
                (db,),
            )
            routines = [{"name": r[0], "kind": r[1]} for r in cur.fetchall()]
        except Exception:
            pass
        return {"tables": tables, "views": views, "routines": routines, "database": db}

    def _columns(self, database: str, table: str) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT, EXTRA,"
            " COLUMN_COMMENT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s"
            " ORDER BY ORDINAL_POSITION",
            (database, table),
        )
        return [
            {
                "name": r[0],
                "type": r[1],
                "nullable": r[2] == "YES",
                "primary": r[3] == "PRI",
                "key": r[3] or "",
                "default": jsonable(r[4]),
                "extra": r[5] or "",
                "comment": r[6] or "",
            }
            for r in cur.fetchall()
        ]

    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        db = database or self._dbname
        timer = Timer()
        columns = self._columns(db, table)
        pk = [c["name"] for c in columns if c["primary"]]
        sql = f"SELECT * FROM {self.quote(db)}.{self.quote(table)}"
        params: list[Any] = []
        if where and where.strip():
            sql += f" WHERE {where}"
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            sql += f" ORDER BY {self.quote(order_by)} {direction}"
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        cur = self._cursor()
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
            names = [d[0] for d in cur.description or []]
        except Exception as exc:
            raise DatabaseError(f"查询表数据失败：{exc}") from exc
        total = None
        try:
            cur.execute(f"SELECT COUNT(*) FROM {self.quote(db)}.{self.quote(table)}" + (f" WHERE {where}" if where else ""), params)
            total = int(cur.fetchone()[0])
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": pk,
            "rows": [[jsonable(v) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool(pk),
        }

    def ddl(self, database, schema, table) -> str:
        db = database or self._dbname
        cur = self._cursor()
        cur.execute(f"SHOW CREATE TABLE {self.quote(db)}.{self.quote(table)}")
        row = cur.fetchone()
        return row[1] if row and len(row) > 1 else ""

    def query(self, sql: str, database: str | None = None) -> dict:
        if database and database != self._dbname:
            self.reconnect(database)
        cur = self._cursor()
        timer = Timer()
        results: list[dict] = []
        first = True
        try:
            cur.execute(sql)
            while True:
                desc = cur.description
                if desc:
                    names = [d[0] for d in desc]
                    types = [self._type_name(d[1]) for d in desc]
                    rows = cur.fetchmany(QUERY_ROW_LIMIT + 1)
                    truncated = len(rows) > QUERY_ROW_LIMIT
                    rows = rows[:QUERY_ROW_LIMIT]
                    results.append(
                        {
                            "columns": names,
                            "columnTypes": types,
                            "rows": [[jsonable(v) for v in r] for r in rows],
                            "rowCount": len(rows),
                            "truncated": truncated,
                            "affectedRows": cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None,
                        }
                    )
                else:
                    results.append(
                        {
                            "columns": [],
                            "rows": [],
                            "rowCount": 0,
                            "affectedRows": cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None,
                            "message": "语句已执行",
                        }
                    )
                if not cur.nextset():
                    break
                first = False
        except Exception as exc:
            raise DatabaseError(f"SQL 执行失败：{exc}") from exc
        return {"results": results, "elapsed": timer.ms(), "sql": sql}

    @staticmethod
    def _type_name(code: int) -> str:
        return {
            0: "decimal", 1: "tinyint", 2: "smallint", 3: "int", 4: "float", 5: "double",
            7: "timestamp", 8: "bigint", 9: "mediumint", 10: "date", 11: "time",
            12: "datetime", 13: "year", 15: "varchar", 16: "bit", 245: "json",
            246: "newdecimal", 252: "blob", 253: "varstring", 254: "string",
        }.get(code, f"type:{code}")

    def _qualified(self, database, schema, table) -> str:
        db = database or self._dbname
        return f"{self.quote(db)}.{self.quote(table)}"

    def _pk_map(self, database, table) -> list[str]:
        return [c["name"] for c in self._columns(database or self._dbname, table) if c["primary"]]

    def update_row(self, database, schema, table, keys, values) -> dict:
        cur = self._cursor()
        sets = ", ".join(f"{self.quote(k)}={self.placeholder}" for k in values)
        wheres = " AND ".join(f"{self.quote(k)}=%s" for k in keys)
        params = list(values.values()) + list(keys.values())
        sql = f"UPDATE {self._qualified(database, schema, table)} SET {sets} WHERE {wheres}"
        cur.execute(sql, params)
        return {"affected": cur.rowcount, "sql": sql}

    def insert_row(self, database, schema, table, values) -> dict:
        cur = self._cursor()
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join("%s" for _ in values)
        sql = f"INSERT INTO {self._qualified(database, schema, table)} ({cols}) VALUES ({ph})"
        cur.execute(sql, list(values.values()))
        return {"affected": cur.rowcount, "insertId": cur.lastrowid, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        cur = self._cursor()
        wheres = " AND ".join(f"{self.quote(k)}=%s" for k in keys)
        sql = f"DELETE FROM {self._qualified(database, schema, table)} WHERE {wheres}"
        cur.execute(sql, list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    def explain(self, sql: str, database=None) -> dict:
        cur = self._cursor()
        try:
            cur.execute("EXPLAIN FORMAT=JSON " + sql)
            row = cur.fetchone()
            return {"format": "json", "plan": row[0] if row else ""}
        except Exception:
            cur.execute("EXPLAIN " + sql)
            names = [d[0] for d in cur.description or []]
            rows = cur.fetchall()
            header = " | ".join(names)
            body = "\n".join(" | ".join(str(c) for c in r) for r in rows)
            return {"format": "text", "plan": header + "\n" + body}


# ---------------------------------------------------------------- PostgreSQL
class PostgresAdapter(BaseAdapter):
    dialect = "postgresql"

    def backend_pid(self) -> int:
        try:
            return int(self._conn.get_backend_pid())
        except Exception:
            return 0

    def cancel_pid(self, pid: int) -> None:
        cur = self._cursor()
        cur.execute("SELECT pg_cancel_backend(%s)", (int(pid),))
        self._conn.commit()
    default_port = 5432

    def connect(self, dbname: str | None = None) -> None:
        import psycopg2
        import psycopg2.extras

        host = self.cfg.get("host") or "127.0.0.1"
        port = int(self.cfg.get("port") or 5432)
        if self.tunnel_addr:
            host, port = self.tunnel_addr
        try:
            self._conn = psycopg2.connect(
                host=host,
                port=port,
                user=self.cfg.get("username") or "postgres",
                password=self.cfg.get("password") or "",
                dbname=dbname or self.cfg.get("database") or "postgres",
                connect_timeout=10,
                sslmode=self.cfg.get("sslMode") or ("require" if parse_bool(self.cfg.get("ssl")) else "prefer"),
                application_name="H1KHub",
            )
            self._conn.autocommit = True
        except Exception as exc:
            raise DatabaseError(f"PostgreSQL 连接失败：{exc}") from exc
        self._dbname = dbname or self.cfg.get("database") or "postgres"

    def _cursor(self):
        assert self._conn is not None
        return self._conn.cursor()

    def version(self) -> str:
        cur = self._cursor()
        cur.execute("SELECT version()")
        return str(cur.fetchone()[0]).split(",")[0]

    def list_databases(self) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            "SELECT datname, pg_database_size(datname) FROM pg_database"
            " WHERE datallowconn AND NOT datistemplate ORDER BY datname"
        )
        return [{"name": r[0], "kind": "database", "size": int(r[1] or 0)} for r in cur.fetchall()]

    def list_schemas(self, database: str | None = None) -> list[str]:
        cur = self._cursor()
        cur.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%%'"
            " AND nspname <> 'information_schema' ORDER BY nspname"
        )
        return [r[0] for r in cur.fetchall()]

    def list_objects(self, database, schema) -> dict:
        db = database or self._dbname
        if database and database != self._dbname:
            self.reconnect(database)
            db = database
        schema = schema or "public"
        cur = self._cursor()
        cur.execute(
            "SELECT table_name, table_type, NULL FROM information_schema.tables"
            " WHERE table_schema=%s ORDER BY table_name",
            (schema,),
        )
        tables, views = [], []
        for name, ttype, _ in cur.fetchall():
            if ttype == "VIEW":
                views.append({"name": name, "engine": "VIEW", "rows": 0, "comment": ""})
            else:
                tables.append({"name": name, "engine": "TABLE", "rows": 0, "comment": ""})
        try:
            cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname=%s ORDER BY 1", (schema,))
            for (name,) in cur.fetchall():
                views.append({"name": name, "engine": "MATVIEW", "rows": 0, "comment": ""})
        except Exception:
            pass
        routines = []
        try:
            cur.execute(
                "SELECT routine_name, routine_type FROM information_schema.routines"
                " WHERE routine_schema=%s ORDER BY routine_type, routine_name",
                (schema,),
            )
            routines = [{"name": r[0], "kind": r[1]} for r in cur.fetchall()]
        except Exception:
            pass
        return {"tables": tables, "views": views, "routines": routines, "database": db, "schema": schema}

    def _columns(self, schema: str, table: str) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default, udt_name"
            " FROM information_schema.columns WHERE table_schema=%s AND table_name=%s"
            " ORDER BY ordinal_position",
            (schema, table),
        )
        pk = set(self._pk_columns(schema, table))
        cols = []
        for name, dtype, nullable, default, udt in cur.fetchall():
            cols.append(
                {
                    "name": name,
                    "type": dtype if dtype != "USER-DEFINED" else udt,
                    "nullable": nullable == "YES",
                    "primary": name in pk,
                    "key": "PRI" if name in pk else "",
                    "default": jsonable(default),
                    "extra": "",
                    "comment": "",
                }
            )
        return cols

    def _pk_columns(self, schema: str, table: str) -> list[str]:
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT a.attname FROM pg_index i"
                " JOIN pg_class c ON c.oid=i.indrelid"
                " JOIN pg_namespace n ON n.oid=c.relnamespace"
                " JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum = ANY(i.indkey)"
                " WHERE i.indisprimary AND n.nspname=%s AND c.relname=%s ORDER BY a.attnum",
                (schema, table),
            )
            return [r[0] for r in cur.fetchall()]
        except Exception:
            return []

    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        schema = schema or "public"
        timer = Timer()
        columns = self._columns(schema, table)
        pk = [c["name"] for c in columns if c["primary"]]
        sql = f"SELECT * FROM {self.quote(schema)}.{self.quote(table)}"
        if where and where.strip():
            sql += f" WHERE {where}"
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            sql += f" ORDER BY {self.quote(order_by)} {direction}"
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        cur = self._cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            names = [d[0] for d in cur.description or []]
        except Exception as exc:
            raise DatabaseError(f"查询表数据失败：{exc}") from exc
        total = None
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM {self.quote(schema)}.{self.quote(table)}" + (f" WHERE {where}" if where else "")
            )
            total = int(cur.fetchone()[0])
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": pk,
            "rows": [[jsonable(v) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool(pk),
        }

    def ddl(self, database, schema, table) -> str:
        schema = schema or "public"
        columns = self._columns(schema, table)
        pks = [c["name"] for c in columns if c["primary"]]
        lines = []
        for c in columns:
            line = f"  {self.quote(c['name'])} {c['type']}"
            if c.get("default") not in (None, ""):
                line += f" DEFAULT {c['default']}"
            if not c["nullable"]:
                line += " NOT NULL"
            lines.append(line)
        if pks:
            lines.append("  PRIMARY KEY (" + ", ".join(self.quote(p) for p in pks) + ")")
        body = ",\n".join(lines)
        return f"CREATE TABLE {self.quote(schema)}.{self.quote(table)} (\n{body}\n);"

    def query(self, sql: str, database: str | None = None) -> dict:
        if database and database != self._dbname:
            self.reconnect(database)
        cur = self._cursor()
        timer = Timer()
        try:
            cur.execute(sql)
            results = []
            # psycopg2 raises NotSupportedError for Cursor.nextset(), and the
            # simple-query protocol returns only one result set per execute.
            # Process the single available result instead of looping.
            if cur.description:
                names = [d[0] for d in cur.description]
                types = [str(d[1]) for d in cur.description]
                rows = cur.fetchmany(QUERY_ROW_LIMIT + 1)
                truncated = len(rows) > QUERY_ROW_LIMIT
                results.append(
                    {
                        "columns": names,
                        "columnTypes": types,
                        "rows": [[jsonable(v) for v in r] for r in rows[:QUERY_ROW_LIMIT]],
                        "rowCount": min(len(rows), QUERY_ROW_LIMIT),
                        "truncated": truncated,
                        "affectedRows": cur.rowcount if cur.rowcount >= 0 else None,
                    }
                )
            else:
                results.append(
                    {
                        "columns": [],
                        "rows": [],
                        "rowCount": 0,
                        "affectedRows": cur.rowcount if cur.rowcount >= 0 else None,
                        "message": "语句已执行",
                    }
                )
            return {"results": results, "elapsed": timer.ms(), "sql": sql}
        except Exception as exc:
            raise DatabaseError(f"SQL 执行失败：{exc}") from exc

    def _qualified(self, database, schema, table) -> str:
        return f"{self.quote(schema or 'public')}.{self.quote(table)}"

    def update_row(self, database, schema, table, keys, values) -> dict:
        cur = self._cursor()
        sets = ", ".join(f"{self.quote(k)}=%s" for k in values)
        wheres = " AND ".join(f"{self.quote(k)}=%s" for k in keys)
        sql = f"UPDATE {self._qualified(database, schema, table)} SET {sets} WHERE {wheres}"
        cur.execute(sql, list(values.values()) + list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    def insert_row(self, database, schema, table, values) -> dict:
        cur = self._cursor()
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join("%s" for _ in values)
        sql = (
            f"INSERT INTO {self._qualified(database, schema, table)} ({cols}) VALUES ({ph})"
            " RETURNING *"
        )
        cur.execute(sql, list(values.values()))
        row = cur.fetchone()
        return {"affected": 1, "row": [jsonable(v) for v in row] if row else None, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        cur = self._cursor()
        wheres = " AND ".join(f"{self.quote(k)}=%s" for k in keys)
        sql = f"DELETE FROM {self._qualified(database, schema, table)} WHERE {wheres}"
        cur.execute(sql, list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    def explain(self, sql: str, database=None) -> dict:
        cur = self._cursor()
        try:
            cur.execute("EXPLAIN (FORMAT JSON) " + sql)
            row = cur.fetchone()
            import json

            return {"format": "json", "plan": json.dumps(row[0], ensure_ascii=False, indent=2)}
        except Exception:
            cur.execute("EXPLAIN " + sql)
            return {"format": "text", "plan": "\n".join(str(r[0]) for r in cur.fetchall())}


# -------------------------------------------------------------------- SQLite
class SQLiteAdapter(BaseAdapter):
    dialect = "sqlite"
    default_port = 0

    def _path(self) -> str:
        path = self.cfg.get("file") or self.cfg.get("path") or ""
        return os.path.expanduser(path)

    def connect(self, dbname: str | None = None) -> None:
        path = self._path()
        if not path or not os.path.exists(path):
            raise DatabaseError(f"数据库文件不存在：{path or '(空)'}")
        try:
            self._conn = sqlite3.connect(
                path, timeout=10, check_same_thread=False, isolation_level=None
            )
            self._conn.row_factory = None
            self._conn.execute("PRAGMA foreign_keys=ON")
        except Exception as exc:
            raise DatabaseError(f"SQLite 打开失败：{exc}") from exc
        self._dbname = dbname or "main"

    def _cursor(self):
        assert self._conn is not None
        return self._conn.cursor()

    def version(self) -> str:
        cur = self._cursor()
        cur.execute("SELECT sqlite_version()")
        return "SQLite " + str(cur.fetchone()[0])

    def list_databases(self) -> list[dict]:
        cur = self._cursor()
        cur.execute("PRAGMA database_list")
        out = []
        for seq, name, path in cur.fetchall():
            size = 0
            try:
                if path:
                    size = os.path.getsize(path)
            except OSError:
                pass
            out.append({"name": name, "kind": "database", "file": path, "size": size})
        return out

    def list_objects(self, database, schema) -> dict:
        db = database or self._dbname or "main"
        cur = self._cursor()
        cur.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view')"
            " AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"
        )
        tables, views = [], []
        for name, ttype, sql in cur.fetchall():
            item = {"name": name, "engine": "VIEW" if ttype == "view" else "TABLE", "rows": 0, "comment": ""}
            (views if ttype == "view" else tables).append(item)
        triggers = []
        try:
            cur.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='trigger'")
            triggers = [{"name": r[0], "table": r[1]} for r in cur.fetchall()]
        except Exception:
            pass
        return {"tables": tables, "views": views, "routines": triggers, "database": db}

    def _table_info(self, db: str, table: str) -> list[dict]:
        cur = self._cursor()
        cur.execute(f"PRAGMA {self.quote(db)}.table_info({self._quote_string(table)})")
        cols = []
        for cid, name, ctype, notnull, default, pk in cur.fetchall():
            cols.append(
                {
                    "name": name,
                    "type": ctype or "",
                    "nullable": not notnull,
                    "primary": bool(pk),
                    "key": "PRI" if pk else "",
                    "default": jsonable(default),
                    "extra": "",
                    "comment": "",
                }
            )
        return cols

    @staticmethod
    def _quote_string(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        db = database or self._dbname or "main"
        timer = Timer()
        columns = self._table_info(db, table)
        pk = [c["name"] for c in columns if c["primary"]]
        qtable = f"{self.quote(db)}.{self.quote(table)}"
        sql = f"SELECT * FROM {qtable}"
        if where and where.strip():
            sql += f" WHERE {where}"
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            sql += f" ORDER BY {self.quote(order_by)} {direction}"
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        cur = self._cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            names = [d[0] for d in cur.description or []]
        except Exception as exc:
            raise DatabaseError(f"查询表数据失败：{exc}") from exc
        total = None
        try:
            cur.execute(f"SELECT COUNT(*) FROM {qtable}" + (f" WHERE {where}" if where else ""))
            total = int(cur.fetchone()[0])
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": pk,
            "rows": [[jsonable(v) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool(pk) or True,
            "rowidTable": True,
        }

    def ddl(self, database, schema, table) -> str:
        db = database or self._dbname or "main"
        cur = self._cursor()
        cur.execute(
            f"SELECT sql FROM {self.quote(db)}.sqlite_master WHERE name=? AND type IN ('table','view')",
            (table,),
        )
        row = cur.fetchone()
        return (row[0] if row else "") or ""

    def _execute_many(self, sql: str) -> dict:
        cur = self._cursor()
        timer = Timer()
        results = []
        for stmt in _split_statements(sql):
            try:
                cur.execute(stmt)
            except Exception as exc:
                raise DatabaseError(f"SQL 执行失败：{exc}\n语句：{stmt[:300]}") from exc
            if cur.description:
                names = [d[0] for d in cur.description]
                rows = cur.fetchmany(QUERY_ROW_LIMIT + 1)
                truncated = len(rows) > QUERY_ROW_LIMIT
                rows = rows[:QUERY_ROW_LIMIT]
                results.append(
                    {
                        "columns": names,
                        "columnTypes": ["" for _ in names],
                        "rows": [[jsonable(v) for v in r] for r in rows],
                        "rowCount": len(rows),
                        "truncated": truncated,
                        "affectedRows": None,
                    }
                )
            else:
                results.append(
                    {
                        "columns": [],
                        "rows": [],
                        "rowCount": 0,
                        "affectedRows": cur.rowcount if cur.rowcount >= 0 else None,
                        "message": "语句已执行",
                    }
                )
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"results": results, "elapsed": timer.ms(), "sql": sql}

    def query(self, sql: str, database=None) -> dict:
        return self._execute_many(sql)

    def _qualified(self, database, schema, table) -> str:
        db = database or self._dbname or "main"
        return f"{self.quote(db)}.{self.quote(table)}"

    def update_row(self, database, schema, table, keys, values) -> dict:
        cur = self._cursor()
        sets = ", ".join(f"{self.quote(k)}=?" for k in values)
        wheres = " AND ".join(f"{self.quote(k)}=?" for k in keys)
        sql = f"UPDATE {self._qualified(database, schema, table)} SET {sets} WHERE {wheres}"
        cur.execute(sql, list(values.values()) + list(keys.values()))
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"affected": cur.rowcount, "sql": sql}

    def insert_row(self, database, schema, table, values) -> dict:
        cur = self._cursor()
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join("?" for _ in values)
        sql = f"INSERT INTO {self._qualified(database, schema, table)} ({cols}) VALUES ({ph})"
        cur.execute(sql, list(values.values()))
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"affected": cur.rowcount, "insertId": cur.lastrowid, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        cur = self._cursor()
        wheres = " AND ".join(f"{self.quote(k)}=?" for k in keys)
        sql = f"DELETE FROM {self._qualified(database, schema, table)} WHERE {wheres}"
        cur.execute(sql, list(keys.values()))
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"affected": cur.rowcount, "sql": sql}

    def explain(self, sql: str, database=None) -> dict:
        cur = self._cursor()
        cur.execute("EXPLAIN QUERY PLAN " + sql)
        rows = cur.fetchall()
        return {
            "format": "text",
            "plan": "\n".join(f"{r[0]}  {r[1]}  {r[2]}  {r[3]}" for r in rows),
        }


# --------------------------------------------------------------------- DuckDB
class DuckDBAdapter(BaseAdapter):
    """Embedded DuckDB files: no server, one process per connection.

    Attached databases are real DuckDB catalogs, so `ATTACH 'other.duckdb' AS x`
    executed in the SQL console shows up in the object tree after refresh.
    """

    dialect = "duckdb"
    default_port = 0
    placeholder = "?"

    def _path(self) -> str:
        path = (
            self.cfg.get("file")
            or self.cfg.get("path")
            or self.cfg.get("database")
            or ""
        )
        path = os.path.expanduser(str(path))
        return path or ":memory:"

    def connect(self, dbname: str | None = None) -> None:
        try:
            import duckdb  # noqa: WPS433 - optional dependency
        except Exception as exc:  # pragma: no cover - driver missing
            raise DatabaseError(
                "未安装 duckdb Python 驱动，请执行 pip install duckdb"
            ) from exc
        path = self._path()
        try:
            self._conn = duckdb.connect(path, read_only=bool(self.cfg.get("readOnly")))
        except Exception as exc:
            raise DatabaseError(f"DuckDB 打开失败：{exc}") from exc
        self._dbname = dbname or self._current_database()
        self._attach_extras()

    def _attach_extras(self) -> None:
        """ATTACH database files persisted in the asset config.

        DuckDB attachments are per-connection, so they are replayed on every
        connect; a file that disappeared is skipped instead of failing the
        primary connection.
        """
        assert self._conn is not None
        for item in self.cfg.get("attached") or []:
            if not isinstance(item, dict):
                continue
            path = os.path.expanduser(str(item.get("path") or ""))
            if not path or not os.path.exists(path):
                continue
            alias = str(item.get("alias") or os.path.splitext(os.path.basename(path))[0])
            alias = re.sub(r"[^0-9A-Za-z_]", "_", alias).strip("_") or "attached"
            if alias == self._dbname:
                continue
            literal = "'" + path.replace("'", "''") + "'"
            clause = f"ATTACH {literal} AS {self.quote(alias)}"
            if item.get("readOnly"):
                clause += " (READ_ONLY)"
            try:
                self._conn.execute(clause)
            except Exception:
                continue

    def _current_database(self) -> str:
        try:
            return str(self._conn.execute("SELECT current_database()").fetchone()[0])
        except Exception:
            return "main"

    def _cursor(self):
        # DuckDB's connection.cursor() opens a *separate* connection, which
        # would break BEGIN/COMMIT inside a session; use the connection itself.
        assert self._conn is not None
        return self._conn

    def begin(self) -> dict:
        try:
            self.set_autocommit(False)
            if not self._in_tx:
                self._conn.execute("BEGIN")
                self._in_tx = True
        except Exception as exc:
            raise DatabaseError(f"开启事务失败：{exc}") from exc
        return {"inTransaction": True}

    def _fetch(self, sql: str, params: list | None = None) -> tuple[list[str], list[tuple]]:
        cur = self._cursor()
        cur.execute(sql, params or [])
        return [d[0] for d in (cur.description or [])], list(cur.fetchall())

    def version(self) -> str:
        try:
            cur = self._cursor()
            cur.execute("SELECT version()")
            return "DuckDB " + str(cur.fetchone()[0])
        except Exception:
            return "DuckDB"

    def backend_pid(self) -> int:
        # DuckDB is embedded: there is no server-side backend PID to report.
        return 0

    def cancel_pid(self, pid: int) -> None:
        raise DatabaseError("DuckDB 为嵌入式引擎，不支持按后端 PID 取消查询")

    def list_databases(self) -> list[dict]:
        out = []
        try:
            cols, rows = self._fetch("SELECT * FROM duckdb_databases()")
        except Exception:
            return out
        for row in rows:
            item = dict(zip(cols, row))
            name = item.get("database_name") or ""
            if not name or name in ("system", "temp"):
                continue
            path = item.get("path") or ""
            size = 0
            try:
                if path and os.path.exists(path):
                    size = os.path.getsize(path)
            except OSError:
                pass
            out.append(
                {
                    "name": name,
                    "kind": "database",
                    "file": path if path and path != ":memory:" else "",
                    "size": size,
                    "attached": bool(path),
                }
            )
        if not out:
            out.append({"name": self._dbname or "main", "kind": "database", "file": "", "size": 0})
        return out

    def list_schemas(self, database: str | None = None) -> list[str]:
        db = database or self._dbname or "main"
        try:
            _, rows = self._fetch(
                "SELECT schema_name FROM duckdb_schemas() WHERE database_name = ? ORDER BY schema_name",
                [db],
            )
            return [r[0] for r in rows]
        except Exception:
            return ["main"]

    def list_objects(self, database: str | None, schema: str | None) -> dict:
        db = database or self._dbname or "main"
        tables, views, routines = [], [], []
        try:
            cols, rows = self._fetch("SELECT * FROM duckdb_tables()")
            for row in rows:
                item = dict(zip(cols, row))
                if item.get("database_name") != db:
                    continue
                if schema and item.get("schema_name") != schema:
                    continue
                tables.append(
                    {
                        "name": item.get("table_name") or "",
                        "engine": "TABLE",
                        "schema": item.get("schema_name") or "main",
                        "rows": int(item.get("estimated_size") or 0),
                        "comment": item.get("comment") or "",
                    }
                )
        except Exception:
            pass
        try:
            cols, rows = self._fetch("SELECT * FROM duckdb_views()")
            for row in rows:
                item = dict(zip(cols, row))
                if item.get("database_name") != db:
                    continue
                if schema and item.get("schema_name") != schema:
                    continue
                views.append(
                    {
                        "name": item.get("view_name") or "",
                        "engine": "VIEW",
                        "schema": item.get("schema_name") or "main",
                        "rows": 0,
                        "comment": item.get("comment") or "",
                    }
                )
        except Exception:
            pass
        try:
            cols, rows = self._fetch(
                "SELECT DISTINCT function_name, schema_name FROM duckdb_functions()"
                " WHERE internal = false ORDER BY function_name"
            )
            for name, sch in rows:
                if schema and sch != schema:
                    continue
                routines.append({"name": name, "type": "FUNCTION", "schema": sch or "main"})
        except Exception:
            pass
        tables.sort(key=lambda x: x["name"])
        views.sort(key=lambda x: x["name"])
        return {"tables": tables, "views": views, "routines": routines, "database": db}

    def _columns(self, db: str, schema: str, table: str) -> list[dict]:
        cols, rows = self._fetch(
            "SELECT * FROM duckdb_columns() WHERE database_name = ? AND schema_name = ? AND table_name = ?",
            [db, schema, table],
        )
        items = sorted(
            (dict(zip(cols, row)) for row in rows), key=lambda x: int(x.get("column_index") or 0)
        )
        pk_cols: set[str] = set()
        try:
            _, consts = self._fetch(
                "SELECT constraint_column_names FROM duckdb_constraints()"
                " WHERE database_name = ? AND schema_name = ? AND table_name = ?"
                " AND constraint_type = 'PRIMARY KEY'",
                [db, schema, table],
            )
            for (names,) in consts:
                for name in names or []:
                    pk_cols.add(str(name))
        except Exception:
            pass
        out = []
        for item in items:
            name = item.get("column_name") or ""
            out.append(
                {
                    "name": name,
                    "type": item.get("data_type") or "",
                    "nullable": bool(item.get("is_nullable", True)),
                    "primary": name in pk_cols,
                    "key": "PRI" if name in pk_cols else "",
                    "default": jsonable(item.get("column_default")),
                    "extra": "",
                    "comment": item.get("comment") or "",
                }
            )
        return out

    def _qualified(self, database, schema, table) -> str:
        db = database or self._dbname or "main"
        parts = [self.quote(db)]
        if schema:
            parts.append(self.quote(schema))
        parts.append(self.quote(table))
        return ".".join(parts)

    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        db = database or self._dbname or "main"
        schema = schema or "main"
        timer = Timer()
        columns = self._columns(db, schema, table)
        pk = [c["name"] for c in columns if c["primary"]]
        ref = self._qualified(db, schema, table)
        sql = f"SELECT * FROM {ref}"
        if where and str(where).strip():
            sql += f" WHERE {where}"
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            sql += f" ORDER BY {self.quote(order_by)} {direction}"
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        cur = self._cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            names = [d[0] for d in cur.description or []]
        except Exception as exc:
            raise DatabaseError(f"查询表数据失败：{exc}") from exc
        total = None
        try:
            cur.execute(f"SELECT COUNT(*) FROM {ref}" + (f" WHERE {where}" if where else ""))
            total = int(cur.fetchone()[0])
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": pk,
            "rows": [[jsonable(v) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool(pk),
            "rowidTable": False,
        }

    def ddl(self, database, schema, table) -> str:
        db = database or self._dbname or "main"
        schema = schema or "main"
        for source, name_col in (("duckdb_tables", "table_name"), ("duckdb_views", "view_name")):
            try:
                cols, rows = self._fetch(
                    f"SELECT sql FROM {source}() WHERE database_name = ? AND schema_name = ?"
                    f" AND {name_col} = ?",
                    [db, schema, table],
                )
                if rows and rows[0][0]:
                    return str(rows[0][0])
            except Exception:
                continue
        return ""

    def _execute_many(self, sql: str) -> dict:
        cur = self._cursor()
        timer = Timer()
        results = []
        for stmt in _split_statements(sql):
            try:
                cur.execute(stmt)
            except Exception as exc:
                raise DatabaseError(f"SQL 执行失败：{exc}\n语句：{stmt[:300]}") from exc
            if cur.description:
                names = [d[0] for d in cur.description]
                types = [str(d[1]) if len(d) > 1 and d[1] is not None else "" for d in cur.description]
                rows = cur.fetchmany(QUERY_ROW_LIMIT + 1)
                truncated = len(rows) > QUERY_ROW_LIMIT
                rows = rows[:QUERY_ROW_LIMIT]
                results.append(
                    {
                        "columns": names,
                        "columnTypes": types,
                        "rows": [[jsonable(v) for v in r] for r in rows],
                        "rowCount": len(rows),
                        "truncated": truncated,
                        "affectedRows": None,
                    }
                )
            else:
                results.append(
                    {
                        "columns": [],
                        "rows": [],
                        "rowCount": 0,
                        "affectedRows": getattr(cur, "rowcount", None) if getattr(cur, "rowcount", -1) >= 0 else None,
                        "message": "语句已执行",
                    }
                )
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"results": results, "elapsed": timer.ms(), "sql": sql}

    def query(self, sql: str, database=None) -> dict:
        return self._execute_many(sql)

    def update_row(self, database, schema, table, keys, values) -> dict:
        cur = self._cursor()
        sets = ", ".join(f"{self.quote(k)} = ?" for k in values)
        wheres = " AND ".join(f"{self.quote(k)} = ?" for k in keys)
        sql = f"UPDATE {self._qualified(database, schema, table)} SET {sets} WHERE {wheres}"
        cur.execute(sql, list(values.values()) + list(keys.values()))
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"affected": getattr(cur, "rowcount", 1), "sql": sql}

    def insert_row(self, database, schema, table, values) -> dict:
        cur = self._cursor()
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join("?" for _ in values)
        sql = f"INSERT INTO {self._qualified(database, schema, table)} ({cols}) VALUES ({ph})"
        cur.execute(sql, list(values.values()))
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"affected": 1, "insertId": None, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        cur = self._cursor()
        wheres = " AND ".join(f"{self.quote(k)} = ?" for k in keys)
        sql = f"DELETE FROM {self._qualified(database, schema, table)} WHERE {wheres}"
        cur.execute(sql, list(keys.values()))
        assert self._conn is not None
        if not self.manual_tx:
            self._conn.commit()
        return {"affected": getattr(cur, "rowcount", 1), "sql": sql}

    def explain(self, sql: str, database=None) -> dict:
        cur = self._cursor()
        cur.execute("EXPLAIN " + sql)
        rows = cur.fetchall()
        cols = [d[0] for d in (cur.description or [])]
        if cols and len(cols) >= 2:
            plan = "\n".join(f"{r[0]}: {r[1]}" for r in rows)
        else:
            plan = "\n".join(str(r[0]) for r in rows)
        return {"format": "text", "plan": plan}


# --------------------------------------------------------------- ClickHouse
class _ClickHouseCursor:
    """Minimal DB-API-ish cursor over clickhouse-driver.

    ClickHouse has no transactions and no server-side cursor; this wrapper only
    exists so shared helpers (metadata dialogs, realtime info) can reuse the
    familiar ``execute``/``fetchall`` surface.
    """

    def __init__(self, client, database: str) -> None:
        self.client = client
        self.database = database
        self._rows: list[tuple] = []
        self._cols: list[tuple] = []
        self.rowcount = -1
        self.description = None

    def execute(self, sql: str, params=None):
        if self.database:
            try:
                self.client.execute(f"USE {_q_std(self.database)}")
            except Exception:
                pass
        if params:
            rows, cols = self.client.execute(sql, params, with_column_types=True)
        else:
            rows, cols = self.client.execute(sql, with_column_types=True)
        self._rows = list(rows)
        self._cols = list(cols or [])
        self.description = [(c[0], c[1], None, None, None, None, True) for c in self._cols] or None
        self.rowcount = len(self._rows) if self.description else -1
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchmany(self, size: int):
        return self._rows[:size]

    def close(self):
        self._rows = []


class ClickHouseAdapter(BaseAdapter):
    """ClickHouse over the native TCP protocol (clickhouse-driver).

    ClickHouse is an OLAP engine: there are no transactions, row updates are
    asynchronous ``ALTER TABLE ... UPDATE`` mutations, and the object tree is
    database -> table/view. Databases are exposed as the schema level.
    """

    dialect = "clickhouse"
    default_port = 9000
    placeholder = "%s"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._cursor_obj = None

    def connect(self, dbname: str | None = None) -> None:
        try:
            from clickhouse_driver import Client  # noqa: WPS433 - optional dependency
        except Exception as exc:  # pragma: no cover - driver missing
            raise DatabaseError(
                "未安装 clickhouse-driver，请执行 pip install clickhouse-driver"
            ) from exc
        host = self.cfg.get("host") or "127.0.0.1"
        port = int(self.cfg.get("port") or 9000)
        if self.tunnel_addr:
            host, port = self.tunnel_addr
        db = dbname or self.cfg.get("database") or "default"
        try:
            self._conn = Client(
                host=host,
                port=port,
                user=self.cfg.get("username") or "default",
                password=self.cfg.get("password") or "",
                database=db,
                connect_timeout=10,
                send_receive_timeout=60,
                secure=bool(parse_bool(self.cfg.get("ssl"))),
            )
            self._conn.execute("SELECT 1")
        except Exception as exc:
            raise DatabaseError(f"ClickHouse 连接失败：{exc}") from exc
        self._dbname = db
        self._cursor_obj = None

    def _cursor(self):
        assert self._conn is not None
        if self._cursor_obj is None or self._cursor_obj.database != self._dbname:
            self._cursor_obj = _ClickHouseCursor(self._conn, self._dbname)
        return self._cursor_obj

    def version(self) -> str:
        try:
            return "ClickHouse " + str(self._conn.execute("SELECT version()")[0][0])
        except Exception:
            return "ClickHouse"

    def backend_pid(self) -> int:
        return 0

    def cancel_pid(self, pid: int) -> None:
        raise DatabaseError("ClickHouse 不支持按连接 PID 取消查询")

    def set_autocommit(self, enabled: bool) -> None:
        # ClickHouse is always autocommit; keep the flag for the UI only.
        self.manual_tx = not enabled
        return None

    def begin(self) -> dict:
        raise DatabaseError("ClickHouse 不支持事务（每次查询自动提交）")

    def commit(self) -> dict:
        return {"inTransaction": False}

    def rollback(self) -> dict:
        return {"inTransaction": False}

    def list_databases(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT name FROM system.databases"
            " WHERE name NOT IN ('INFORMATION_SCHEMA','information_schema') ORDER BY name"
        )
        out = []
        for (name,) in rows:
            size = 0
            try:
                size = int(
                    self._conn.execute(
                        "SELECT sum(bytes_on_disk) FROM system.parts"
                        " WHERE active AND database = %(db)s",
                        {"db": name},
                    )[0][0]
                    or 0
                )
            except Exception:
                pass
            out.append({"name": name, "kind": "database", "size": size})
        return out

    def list_schemas(self, database: str | None = None) -> list[str]:
        return []

    def list_objects(self, database, schema) -> dict:
        db = database or self._dbname or "default"
        rows = self._conn.execute(
            "SELECT name, engine, if(endsWith(engine, 'View'), 1, 0), total_rows"
            " FROM system.tables WHERE database = %(db)s ORDER BY name",
            {"db": db},
        )
        tables, views = [], []
        for name, engine, is_view, total_rows in rows:
            item = {
                "name": name,
                "engine": engine or "TABLE",
                "rows": int(total_rows or 0),
                "comment": "",
            }
            if is_view:
                views.append(item)
            else:
                tables.append(item)
        return {"tables": tables, "views": views, "routines": [], "database": db, "schema": ""}

    def _describe(self, database: str, table: str) -> list[dict]:
        rows = self._conn.execute(f"DESCRIBE TABLE {self._qualified(database, None, table)}")
        pk = set(self._primary_key(database, table))
        cols = []
        for row in rows:
            name, ctype = row[0], row[1]
            default = row[3] if len(row) > 3 else ""
            cols.append(
                {
                    "name": name,
                    "type": ctype,
                    "nullable": str(ctype).startswith("Nullable"),
                    "primary": name in pk,
                    "key": "PRI" if name in pk else "",
                    "default": default,
                    "extra": "",
                    "comment": row[7] if len(row) > 7 else "",
                }
            )
        return cols

    def _primary_key(self, database: str, table: str) -> list[str]:
        try:
            text = self._conn.execute(
                "SELECT primary_key FROM system.tables WHERE database=%(db)s AND name=%(t)s",
                {"db": database, "t": table},
            )[0][0]
            return [c.strip() for c in str(text or "").split(",") if c.strip()]
        except Exception:
            return []

    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        db = database or self._dbname or "default"
        timer = Timer()
        columns = self._describe(db, table)
        ref = self._qualified(db, None, table)
        sql = f"SELECT * FROM {ref}"
        if where and str(where).strip():
            sql += f" WHERE {where}"
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            sql += f" ORDER BY {self.quote(order_by)} {direction}"
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        cur = self._cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            names = [d[0] for d in cur.description or []]
        except Exception as exc:
            raise DatabaseError(f"查询表数据失败：{exc}") from exc
        total = None
        try:
            total = int(
                self._conn.execute(
                    f"SELECT count(*) FROM {ref}" + (f" WHERE {where}" if where else "")
                )[0][0]
            )
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": [c["name"] for c in columns if c["primary"]],
            "rows": [[jsonable(v) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool([c for c in columns if c["primary"]]),
        }

    def ddl(self, database, schema, table) -> str:
        db = database or self._dbname or "default"
        rows = self._conn.execute(f"SHOW CREATE TABLE {self._qualified(db, None, table)}")
        if rows:
            return str(rows[0][0])
        return ""

    def query(self, sql: str, database: str | None = None) -> dict:
        if database and database != self._dbname:
            self.reconnect(database)
        timer = Timer()
        try:
            rows, cols = self._conn.execute(sql, with_column_types=True)
            if cols:
                names = [c[0] for c in cols]
                types = [c[1] for c in cols]
                data = list(rows)
                truncated = len(data) > QUERY_ROW_LIMIT
                return {
                    "results": [
                        {
                            "columns": names,
                            "columnTypes": types,
                            "rows": [[jsonable(v) for v in r] for r in data[:QUERY_ROW_LIMIT]],
                            "rowCount": min(len(data), QUERY_ROW_LIMIT),
                            "truncated": truncated,
                            "affectedRows": None,
                        }
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            return {
                "results": [{"columns": [], "rows": [], "rowCount": 0, "affectedRows": None, "message": "语句已执行"}],
                "elapsed": timer.ms(),
                "sql": sql,
            }
        except Exception as exc:
            raise DatabaseError(f"SQL 执行失败：{exc}") from exc

    def _qualified(self, database, schema, table) -> str:
        db = database or self._dbname or "default"
        return f"{self.quote(db)}.{self.quote(table)}"

    def insert_row(self, database, schema, table, values) -> dict:
        ref = self._qualified(database, schema, table)
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join(f"%({k})s" for k in values)
        sql = f"INSERT INTO {ref} ({cols}) VALUES ({ph})"
        self._conn.execute(sql, dict(values))
        return {"affected": 1, "row": None, "sql": sql}

    def update_row(self, database, schema, table, keys, values) -> dict:
        ref = self._qualified(database, schema, table)
        sets = ", ".join(f"{self.quote(k)} = %({k})s" for k in values)
        where = " AND ".join(f"{self.quote(k)} = %({k})s" for k in keys)
        sql = f"ALTER TABLE {ref} UPDATE {sets} WHERE {where} SETTINGS mutations_sync = 1"
        self._conn.execute(sql, {**values, **keys})
        return {"affected": None, "async": True, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        ref = self._qualified(database, schema, table)
        where = " AND ".join(f"{self.quote(k)} = %({k})s" for k in keys)
        sql = f"ALTER TABLE {ref} DELETE WHERE {where} SETTINGS mutations_sync = 1"
        self._conn.execute(sql, dict(keys))
        return {"affected": None, "async": True, "sql": sql}

    def explain(self, sql: str, database=None) -> dict:
        if database and database != self._dbname:
            self.reconnect(database)
        rows = self._conn.execute("EXPLAIN " + sql)
        plan = "\n".join(str(r[0]) for r in rows)
        return {"format": "text", "plan": plan}


# -------------------------------------------------------------------- MongoDB
def _mongo_oid(value):
    """Turn a 24-hex string back into an ObjectId; leave anything else alone."""
    try:
        from bson import ObjectId  # noqa: WPS433
        if isinstance(value, str) and len(value) == 24:
            int(value, 16)
            return ObjectId(value)
    except Exception:
        pass
    return value


class MongoAdapter(BaseAdapter):
    """MongoDB document store.

    There is no SQL dialect here: the object tree is database -> collection, a
    collection is rendered as a table whose columns are the sampled document
    keys, and the query console accepts either JSON commands
    (``{"collection":"users","op":"find","filter":{}}``) or the familiar
    ``db.users.find({...})`` shell subset.
    """

    dialect = "mongodb"
    default_port = 27017
    placeholder = "%s"

    def connect(self, dbname: str | None = None) -> None:
        try:
            import pymongo  # noqa: WPS433 - optional dependency
        except Exception as exc:  # pragma: no cover - driver missing
            raise DatabaseError("未安装 pymongo，请执行 pip install pymongo") from exc
        host = self.cfg.get("host") or "127.0.0.1"
        port = int(self.cfg.get("port") or 27017)
        if self.tunnel_addr:
            host, port = self.tunnel_addr
        db = dbname or self.cfg.get("database") or "test"
        kwargs = {
            "host": host,
            "port": port,
            "serverSelectionTimeoutMS": 8000,
            "connectTimeoutMS": 8000,
        }
        if self.cfg.get("username"):
            kwargs["username"] = self.cfg.get("username")
            kwargs["password"] = self.cfg.get("password") or ""
            kwargs["authSource"] = self.cfg.get("authSource") or "admin"
        try:
            self._conn = pymongo.MongoClient(**kwargs)
            self._conn.admin.command("ping")
        except Exception as exc:
            raise DatabaseError(f"MongoDB 连接失败：{exc}") from exc
        self._dbname = db

    def _db(self, database: str | None = None):
        assert self._conn is not None
        return self._conn[database or self._dbname or "test"]

    def version(self) -> str:
        try:
            info = self._conn.server_info()
            return "MongoDB " + str(info.get("version"))
        except Exception:
            return "MongoDB"

    def backend_pid(self) -> int:
        return 0

    def cancel_pid(self, pid: int) -> None:
        raise DatabaseError("MongoDB 不支持按连接 PID 取消查询")

    def set_autocommit(self, enabled: bool) -> None:
        self.manual_tx = not enabled

    def list_databases(self) -> list[dict]:
        system = {"admin", "config", "local"}
        out = []
        for name in sorted(self._conn.list_database_names()):
            if name in system:
                continue
            try:
                size = int(self._db(name).command("dbStats").get("dataSize") or 0)
            except Exception:
                size = 0
            out.append({"name": name, "kind": "database", "size": size})
        return out

    def list_schemas(self, database: str | None = None) -> list[str]:
        return []

    def list_objects(self, database, schema) -> dict:
        db = self._db(database)
        tables, views = [], []
        for name in sorted(db.list_collection_names()):
            try:
                info = db.command("listCollections", filter={"name": name}).get("cursor", {}).get("firstBatch", [])
                ctype = info[0].get("type") if info else "collection"
            except Exception:
                ctype = "collection"
            try:
                count = db[name].estimated_document_count()
            except Exception:
                count = 0
            item = {"name": name, "engine": ctype.upper(), "rows": int(count), "comment": ""}
            if ctype == "view":
                views.append(item)
            else:
                tables.append(item)
        return {"tables": tables, "views": views, "routines": [], "database": db.name, "schema": ""}

    def _columns(self, database, table) -> list[dict]:
        db = self._db(database)
        seen: dict[str, str] = {}
        order: list[str] = []
        try:
            for doc in db[table].find({}, limit=50):
                for key, value in doc.items():
                    if key not in seen:
                        seen[key] = _mongo_type(value)
                        order.append(key)
                    elif seen[key] == "unknown":
                        seen[key] = _mongo_type(value)
        except Exception:
            pass
        if "_id" not in seen:
            seen["_id"] = "ObjectId"
            order.insert(0, "_id")
        cols = []
        for key in order:
            cols.append(
                {
                    "name": key,
                    "type": seen[key],
                    "nullable": key != "_id",
                    "primary": key == "_id",
                    "key": "PRI" if key == "_id" else "",
                    "default": "",
                    "extra": "",
                    "comment": "",
                }
            )
        return cols

    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        db = self._db(database)
        timer = Timer()
        columns = self._columns(database, table)
        names = [c["name"] for c in columns]
        flt = _mongo_filter(where)
        cursor = db[table].find(flt)
        if order_by:
            cursor = cursor.sort(str(order_by), -1 if str(order_dir).upper() == "DESC" else 1)
        cursor = cursor.skip(int(offset)).limit(int(limit))
        rows = []
        try:
            for doc in cursor:
                rows.append([jsonable(doc[key]) if key in doc else None for key in names])
        except Exception as exc:
            raise DatabaseError(f"查询集合数据失败：{exc}") from exc
        total = None
        try:
            total = int(db[table].count_documents(flt))
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": ["_id"],
            "rows": rows,
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": f"db.{table}.find({flt}).skip({offset}).limit({limit})",
            "elapsed": timer.ms(),
            "editable": True,
        }

    def ddl(self, database, schema, table) -> str:
        db = self._db(database)
        try:
            info = db.command("listCollections", filter={"name": table}).get("cursor", {}).get("firstBatch", [])
            spec = info[0] if info else {}
            validator = spec.get("options", {}).get("validator")
        except Exception:
            validator = None
        payload = {"create": table}
        if validator:
            payload["validator"] = validator
        return f"db.createCollection({table!r}, {payload!r})"

    def query(self, sql: str, database: str | None = None) -> dict:
        db = self._db(database)
        timer = Timer()
        try:
            op, payload = _parse_mongo_command(sql)
            if op == "find":
                cursor = db[payload["collection"]].find(payload.get("filter") or {})
                if payload.get("sort"):
                    for key, direction in payload["sort"].items():
                        cursor = cursor.sort(key, int(direction))
                if payload.get("skip"):
                    cursor = cursor.skip(int(payload["skip"]))
                limit = int(payload.get("limit") or payload.get("batchSize") or QUERY_ROW_LIMIT)
                cursor = cursor.limit(limit + 1)
                docs = list(cursor)
                truncated = len(docs) > limit
                docs = docs[:limit]
                names: list[str] = []
                for doc in docs:
                    for key in doc:
                        if key not in names:
                            names.append(key)
                rows = [[jsonable(doc.get(k)) for k in names] for doc in docs]
                return {
                    "results": [
                        {
                            "columns": names,
                            "columnTypes": ["document"] * len(names),
                            "rows": rows,
                            "rowCount": len(rows),
                            "truncated": truncated,
                            "affectedRows": None,
                        }
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            if op == "count":
                n = int(db[payload["collection"]].count_documents(payload.get("filter") or {}))
                return {
                    "results": [
                        {
                            "columns": ["count"],
                            "columnTypes": ["long"],
                            "rows": [[n]],
                            "rowCount": 1,
                            "truncated": False,
                            "affectedRows": None,
                        }
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            if op == "aggregate":
                docs = list(db[payload["collection"]].aggregate(payload.get("pipeline") or []))
                docs = docs[:QUERY_ROW_LIMIT]
                names = []
                for doc in docs:
                    for key in doc:
                        if key not in names:
                            names.append(key)
                return {
                    "results": [
                        {
                            "columns": names,
                            "columnTypes": ["document"] * len(names),
                            "rows": [[jsonable(doc.get(k)) for k in names] for doc in docs],
                            "rowCount": len(docs),
                            "truncated": False,
                            "affectedRows": None,
                        }
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            if op == "insertOne":
                res = db[payload["collection"]].insert_one(payload.get("document") or {})
                return {
                    "results": [
                        {"columns": ["insertedId"], "columnTypes": ["ObjectId"], "rows": [[str(res.inserted_id)]], "rowCount": 1, "truncated": False, "affectedRows": 1}
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            if op == "deleteMany":
                res = db[payload["collection"]].delete_many(payload.get("filter") or {})
                return {
                    "results": [
                        {"columns": ["deletedCount"], "columnTypes": ["int"], "rows": [[int(res.deleted_count)]], "rowCount": 1, "truncated": False, "affectedRows": int(res.deleted_count)}
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            if op == "drop":
                db[payload["collection"]].drop()
                return {
                    "results": [
                        {"columns": ["dropped"], "columnTypes": ["string"], "rows": [[payload["collection"]]], "rowCount": 1, "truncated": False, "affectedRows": 1}
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            if op == "updateMany":
                res = db[payload["collection"]].update_many(
                    payload.get("filter") or {}, {"$set": payload.get("update") or {}}
                )
                return {
                    "results": [
                        {"columns": ["matched", "modified"], "columnTypes": ["int", "int"], "rows": [[int(res.matched_count), int(res.modified_count)]], "rowCount": 1, "truncated": False, "affectedRows": int(res.modified_count)}
                    ],
                    "elapsed": timer.ms(),
                    "sql": sql,
                }
            raise DatabaseError(f"不支持的 MongoDB 命令：{op}")
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(f"MongoDB 执行失败：{exc}") from exc

    def _qualified(self, database, schema, table) -> str:
        return f"{database or self._dbname}.{table}"

    def update_row(self, database, schema, table, keys, values) -> dict:
        db = self._db(database)
        flt = {k: _mongo_oid(v) for k, v in keys.items()}
        res = db[table].update_one(flt, {"$set": dict(values)})
        return {"affected": int(res.modified_count), "sql": f"db.{table}.updateOne(...)"}

    def insert_row(self, database, schema, table, values) -> dict:
        db = self._db(database)
        doc = dict(values)
        if "_id" in doc:
            doc["_id"] = _mongo_oid(doc["_id"])
        res = db[table].insert_one(doc)
        return {"affected": 1, "row": [str(res.inserted_id)], "sql": f"db.{table}.insertOne(...)"}

    def delete_row(self, database, schema, table, keys) -> dict:
        db = self._db(database)
        flt = {k: _mongo_oid(v) for k, v in keys.items()}
        res = db[table].delete_one(flt)
        return {"affected": int(res.deleted_count), "sql": f"db.{table}.deleteOne(...)"}

    def explain(self, sql: str, database=None) -> dict:
        import json as _json

        db = self._db(database)
        op, payload = _parse_mongo_command(sql)
        if op != "find":
            raise DatabaseError("MongoDB 仅支持对 find 命令 Explain")
        plan = db.command(
            "explain",
            {"find": payload["collection"], "filter": payload.get("filter") or {}},
        )
        return {"format": "json", "plan": _json.dumps(jsonable(plan), ensure_ascii=False, indent=2)}


def _mongo_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "long"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    name = type(value).__name__
    return {"ObjectId": "ObjectId", "datetime": "date", "bytes": "binData"}.get(name, name)


def _mongo_filter(where) -> dict:
    import json as _json

    if where in (None, ""):
        return {}
    if isinstance(where, dict):
        return where
    try:
        parsed = _json.loads(str(where))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _parse_mongo_command(text: str) -> tuple[str, dict]:
    """Accept a JSON command object or a ``db.<collection>.<op>(...)`` subset."""
    import json as _json
    import re as _re

    raw = str(text or "").strip().rstrip(";").strip()
    if not raw:
        raise DatabaseError("MongoDB 命令为空")
    if raw.startswith("{"):
        payload = _json.loads(raw)
        op = payload.get("op") or "find"
        if not payload.get("collection"):
            raise DatabaseError("JSON 命令必须包含 collection 字段")
        return op, payload
    match = _re.match(r"db\.([A-Za-z0-9_\-$]+)\.([A-Za-z]+)\s*\((.*)\)\s*$", raw, _re.S)
    if not match:
        raise DatabaseError(
            '无法解析 MongoDB 命令；请使用 {"collection":"users","op":"find","filter":{}}'
            " 或 db.users.find({...})"
        )
    collection, method, args_text = match.group(1), match.group(2), match.group(3)
    args: list = []
    if args_text.strip():
        dec = _json.JSONDecoder()
        idx = 0
        while idx < len(args_text):
            while idx < len(args_text) and args_text[idx] in " \t\r\n,":
                idx += 1
            if idx >= len(args_text):
                break
            value, end = dec.raw_decode(args_text, idx)
            args.append(value)
            idx = end
    mapping = {
        "find": "find",
        "findOne": "find",
        "count": "count",
        "countDocuments": "count",
        "aggregate": "aggregate",
        "insertOne": "insertOne",
        "deleteMany": "deleteMany",
        "updateMany": "updateMany",
        "drop": "drop",
        "dropCollection": "drop",
    }
    op = mapping.get(method)
    if not op:
        raise DatabaseError(f"暂不支持的 MongoDB 方法：{method}")
    payload = {"collection": collection}
    if op == "find":
        payload["filter"] = args[0] if args else {}
        if len(args) > 1 and isinstance(args[1], dict):
            payload["projection"] = args[1]
        if method == "findOne":
            payload["limit"] = 1
        for arg in args[1:]:
            if isinstance(arg, dict):
                for key in ("sort", "skip", "limit", "batchSize"):
                    if key in arg:
                        payload[key] = arg[key]
    elif op == "count":
        payload["filter"] = args[0] if args else {}
    elif op == "aggregate":
        payload["pipeline"] = args[0] if args else []
    elif op == "insertOne":
        payload["document"] = args[0] if args else {}
    elif op in ("deleteMany", "updateMany"):
        payload["filter"] = args[0] if args else {}
        if op == "updateMany":
            payload["update"] = args[1] if len(args) > 1 else {}
    return op, payload


# ------------------------------------------------------------------ SQL Server
def _q_mssql(name: str) -> str:
    return "[" + str(name).replace("]", "]]") + "]"


def _q_upper(name: str) -> str:
    """Oracle / Dameng fold unquoted identifiers to upper case."""
    text = str(name)
    if text.startswith('"') and text.endswith('"'):
        return text
    return '"' + text.upper().replace('"', '""') + '"'


def _lob_read(value):
    """Materialise Oracle/Dameng LOB values so the grid can render them."""
    if isinstance(value, (str, bytes, bytearray, memoryview, dict, list, tuple, int, float)):
        return value
    reader = getattr(value, "read", None)
    if callable(reader):
        try:
            data = reader()
        except Exception:
            return value
        if isinstance(data, bytes):
            return data
        if isinstance(data, str) and len(data) > BLOB_INLINE_LIMIT:
            return data[:BLOB_INLINE_LIMIT] + "…"
        return data
    return value


class SQLServerAdapter(BaseAdapter):
    """SQL Server / Azure SQL over TDS (pymssql).

    One instance exposes many databases; inside a database the tree is
    schema -> table/view/programmable object. ``database`` selects the initial
    catalogue and the same session switches it with ``USE`` so the three-part
    names stay short.
    """

    dialect = "sqlserver"
    default_port = 1433
    placeholder = "%s"
    quote = staticmethod(_q_mssql)

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._server_version = ""

    # lifecycle ------------------------------------------------------------
    def connect(self, dbname: str | None = None) -> None:
        try:
            import pymssql  # noqa: WPS433 - optional dependency
        except Exception as exc:  # pragma: no cover - driver missing
            raise DatabaseError("未安装 pymssql，请执行 pip install pymssql") from exc
        host = self.cfg.get("host") or "127.0.0.1"
        port = int(self.cfg.get("port") or 1433)
        if self.tunnel_addr:
            host, port = self.tunnel_addr
        db = dbname or self.cfg.get("database") or "master"
        try:
            self._conn = pymssql.connect(
                server=host,
                port=str(port),
                user=self.cfg.get("username") or "sa",
                password=self.cfg.get("password") or "",
                database=db,
                charset=self.cfg.get("charset") or "UTF-8",
                login_timeout=10,
                timeout=120,
                autocommit=True,
                appname="H1KHub",
            )
        except Exception as exc:
            raise DatabaseError(f"SQL Server 连接失败：{exc}") from exc
        self._dbname = db

    def _cursor(self):
        assert self._conn is not None
        return self._conn.cursor()

    def set_autocommit(self, enabled: bool) -> None:
        self.manual_tx = not enabled
        if enabled:
            self._in_tx = False
        try:
            self._conn.autocommit(enabled)
        except Exception as exc:  # pragma: no cover - driver specific
            raise DatabaseError(f"切换自动提交失败：{exc}") from exc

    def _ensure_db(self, database: str | None) -> str:
        target = database or self._dbname or "master"
        if self._conn is not None and target != self._dbname:
            cur = self._cursor()
            cur.execute(f"USE {self.quote(target)}")
            self._dbname = target
        return self._dbname

    def version(self) -> str:
        if not self._server_version:
            cur = self._cursor()
            try:
                cur.execute(
                    "SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(128)),"
                    " CAST(SERVERPROPERTY('ProductLevel') AS varchar(32)),"
                    " CAST(SERVERPROPERTY('Edition') AS varchar(160))"
                )
                row = cur.fetchone() or ()
                parts = [str(p) for p in row if p]
                self._server_version = "SQL Server " + " ".join(parts) if parts else "SQL Server"
            except Exception:
                self._server_version = "SQL Server"
        return self._server_version

    def backend_pid(self) -> int:
        try:
            cur = self._cursor()
            cur.execute("SELECT @@SPID")
            return int(cur.fetchone()[0])
        except Exception:
            return 0

    def cancel_pid(self, pid: int) -> None:
        cur = self._cursor()
        cur.execute(f"KILL {int(pid)}")

    # metadata -------------------------------------------------------------
    def list_databases(self) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            "SELECT d.name, CAST(ISNULL(SUM(mf.size), 0) AS bigint) * 8192"
            " FROM sys.databases d"
            " LEFT JOIN sys.master_files mf ON mf.database_id = d.database_id"
            " WHERE d.state_desc = 'ONLINE' GROUP BY d.name ORDER BY d.name"
        )
        return [{"name": r[0], "kind": "database", "size": int(r[1] or 0)} for r in cur.fetchall()]

    def list_schemas(self, database: str | None = None) -> list[str]:
        self._ensure_db(database)
        cur = self._cursor()
        cur.execute(
            "SELECT name FROM sys.schemas WHERE name NOT IN"
            " ('sys','INFORMATION_SCHEMA','guest','db_owner','db_accessadmin',"
            "'db_securityadmin','db_ddladmin','db_backupoperator','db_datareader',"
            "'db_datawriter','db_denydatareader','db_denydatawriter')"
            " ORDER BY name"
        )
        return [r[0] for r in cur.fetchall()]

    def list_objects(self, database: str | None, schema: str | None) -> dict:
        db = self._ensure_db(database)
        schema = schema or "dbo"
        cur = self._cursor()
        tables: list[dict] = []
        views: list[dict] = []
        routines: list[dict] = []
        try:
            cur.execute(
                "SELECT t.name, ISNULL((SELECT SUM(ps.row_count) FROM sys.dm_db_partition_stats ps"
                " WHERE ps.object_id = t.object_id AND ps.index_id IN (0,1)), 0)"
                " FROM sys.tables t WHERE SCHEMA_NAME(t.schema_id) = %s ORDER BY t.name",
                (schema,),
            )
            for name, nrows in cur.fetchall():
                tables.append({"name": name, "engine": "TABLE", "rows": int(nrows or 0), "comment": ""})
        except Exception:
            cur.execute(
                "SELECT t.name FROM sys.tables t WHERE SCHEMA_NAME(t.schema_id) = %s ORDER BY t.name",
                (schema,),
            )
            for (name,) in cur.fetchall():
                tables.append({"name": name, "engine": "TABLE", "rows": 0, "comment": ""})
        cur.execute(
            "SELECT v.name FROM sys.views v WHERE SCHEMA_NAME(v.schema_id) = %s ORDER BY v.name",
            (schema,),
        )
        views = [{"name": r[0], "engine": "VIEW", "rows": 0, "comment": ""} for r in cur.fetchall()]
        try:
            cur.execute(
                "SELECT o.name, o.type_desc FROM sys.objects o"
                " WHERE SCHEMA_NAME(o.schema_id) = %s AND o.type IN ('P','FN','IF','TF','AF')"
                " ORDER BY o.name",
                (schema,),
            )
            routines = [{"name": r[0], "kind": r[1]} for r in cur.fetchall()]
        except Exception:
            pass
        return {"tables": tables, "views": views, "routines": routines, "database": db, "schema": schema}

    @staticmethod
    def _type_text(dtype, char_len, num_prec, num_scale, dt_prec) -> str:
        text = str(dtype)
        low = text.lower()
        if low in ("varchar", "nvarchar", "char", "nchar", "varbinary", "binary"):
            if char_len is None:
                return text
            return f"{text}(max)" if int(char_len) == -1 else f"{text}({int(char_len)})"
        if low in ("decimal", "numeric") and num_prec is not None:
            return f"{text}({int(num_prec)},{int(num_scale or 0)})"
        if low in ("datetime2", "datetimeoffset", "time") and dt_prec is not None:
            return f"{text}({int(dt_prec)})"
        return text

    def _object_ref(self, schema: str, table: str) -> str:
        return f"{self.quote(schema)}.{self.quote(table)}"

    def _pk_columns(self, schema: str, table: str) -> list[str]:
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT c.name FROM sys.indexes i"
                " JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id"
                " JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id"
                " WHERE i.is_primary_key = 1 AND i.object_id = OBJECT_ID(%s)"
                " ORDER BY ic.key_ordinal",
                (self._object_ref(schema, table),),
            )
            return [r[0] for r in cur.fetchall()]
        except Exception:
            return []

    def _column_comments(self, schema: str, table: str) -> dict:
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT c.name, CAST(ep.value AS nvarchar(4000)) FROM sys.extended_properties ep"
                " JOIN sys.columns c ON c.object_id = ep.major_id AND c.column_id = ep.minor_id"
                " WHERE ep.major_id = OBJECT_ID(%s) AND ep.name = 'MS_Description'",
                (self._object_ref(schema, table),),
            )
            return {r[0]: str(r[1] or "") for r in cur.fetchall()}
        except Exception:
            return {}

    def _columns(self, schema: str, table: str) -> list[dict]:
        schema = schema or "dbo"
        cur = self._cursor()
        pk = set(self._pk_columns(schema, table))
        comments = self._column_comments(schema, table)
        cur.execute(
            "SELECT c.COLUMN_NAME, c.DATA_TYPE, c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION,"
            " c.NUMERIC_SCALE, c.DATETIME_PRECISION, c.IS_NULLABLE, c.COLUMN_DEFAULT,"
            " COLUMNPROPERTY(OBJECT_ID(QUOTENAME(c.TABLE_SCHEMA) + '.' + QUOTENAME(c.TABLE_NAME)),"
            " c.COLUMN_NAME, 'IsIdentity')"
            " FROM INFORMATION_SCHEMA.COLUMNS c"
            " WHERE c.TABLE_SCHEMA = %s AND c.TABLE_NAME = %s ORDER BY c.ORDINAL_POSITION",
            (schema, table),
        )
        cols = []
        for name, dtype, char_len, num_prec, num_scale, dt_prec, nullable, default, identity in cur.fetchall():
            cols.append(
                {
                    "name": name,
                    "type": self._type_text(dtype, char_len, num_prec, num_scale, dt_prec),
                    "nullable": str(nullable).upper() == "YES",
                    "primary": name in pk,
                    "key": "PRI" if name in pk else "",
                    "default": jsonable(default),
                    "extra": "identity" if identity else "",
                    "comment": comments.get(name, ""),
                }
            )
        return cols

    # data -----------------------------------------------------------------
    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        self._ensure_db(database)
        schema = schema or "dbo"
        timer = Timer()
        columns = self._columns(schema, table)
        pk = [c["name"] for c in columns if c["primary"]]
        ref = self._object_ref(schema, table)
        sql = f"SELECT * FROM {ref}"
        if where and str(where).strip():
            sql += f" WHERE {where}"
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            sql += f" ORDER BY {self.quote(order_by)} {direction}"
        elif pk:
            sql += " ORDER BY " + ", ".join(self.quote(p) for p in pk)
        else:
            sql += " ORDER BY (SELECT NULL)"
        sql += f" OFFSET {int(offset)} ROWS FETCH NEXT {int(limit)} ROWS ONLY"
        cur = self._cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            names = [d[0] for d in cur.description or []]
        except Exception as exc:
            raise DatabaseError(f"查询表数据失败：{exc}") from exc
        total = None
        try:
            cur.execute(f"SELECT COUNT(*) FROM {ref}" + (f" WHERE {where}" if where else ""))
            total = int(cur.fetchone()[0])
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": pk,
            "rows": [[jsonable(v) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool(pk),
        }

    def ddl(self, database, schema, table) -> str:
        self._ensure_db(database)
        schema = schema or "dbo"
        columns = self._columns(schema, table)
        pks = [c["name"] for c in columns if c["primary"]]
        lines = []
        for c in columns:
            line = f"  {self.quote(c['name'])} {c['type']}"
            if c.get("extra") == "identity":
                line += " IDENTITY(1,1)"
            if c.get("default") not in (None, ""):
                line += f" DEFAULT {c['default']}"
            if not c["nullable"]:
                line += " NOT NULL"
            lines.append(line)
        if pks:
            lines.append("  PRIMARY KEY (" + ", ".join(self.quote(p) for p in pks) + ")")
        body = ",\n".join(lines)
        return f"CREATE TABLE {self._object_ref(schema, table)} (\n{body}\n);"

    def query(self, sql: str, database: str | None = None) -> dict:
        self._ensure_db(database)
        cur = self._cursor()
        timer = Timer()
        results: list[dict] = []
        try:
            cur.execute(sql)
            while True:
                if cur.description:
                    names = [d[0] for d in cur.description]
                    types = [str(d[1]) for d in cur.description]
                    rows = cur.fetchmany(QUERY_ROW_LIMIT + 1)
                    truncated = len(rows) > QUERY_ROW_LIMIT
                    results.append(
                        {
                            "columns": names,
                            "columnTypes": types,
                            "rows": [[jsonable(v) for v in r] for r in rows[:QUERY_ROW_LIMIT]],
                            "rowCount": min(len(rows), QUERY_ROW_LIMIT),
                            "truncated": truncated,
                            "affectedRows": cur.rowcount if (cur.rowcount or -1) >= 0 else None,
                        }
                    )
                else:
                    affected = cur.rowcount if (cur.rowcount or -1) > 0 else None
                    results.append(
                        {
                            "columns": [],
                            "rows": [],
                            "rowCount": 0,
                            "affectedRows": affected,
                            "message": "语句已执行",
                        }
                    )
                if not cur.nextset():
                    break
        except Exception as exc:
            raise DatabaseError(f"SQL 执行失败：{exc}") from exc
        return {"results": results, "elapsed": timer.ms(), "sql": sql}

    def _qualified(self, database, schema, table) -> str:
        return self._object_ref(schema or "dbo", table)

    def insert_row(self, database, schema, table, values) -> dict:
        self._ensure_db(database)
        ref = self._qualified(database, schema, table)
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join("%s" for _ in values)
        sql = f"INSERT INTO {ref} ({cols}) OUTPUT INSERTED.* VALUES ({ph})"
        cur = self._cursor()
        cur.execute(sql, list(values.values()))
        row = cur.fetchone()
        return {"affected": 1, "row": [jsonable(v) for v in row] if row else None, "sql": sql}

    def update_row(self, database, schema, table, keys, values) -> dict:
        self._ensure_db(database)
        cur = self._cursor()
        sets = ", ".join(f"{self.quote(k)} = %s" for k in values)
        wheres = " AND ".join(f"{self.quote(k)} = %s" for k in keys)
        sql = f"UPDATE {self._qualified(database, schema, table)} SET {sets} WHERE {wheres}"
        cur.execute(sql, list(values.values()) + list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        self._ensure_db(database)
        cur = self._cursor()
        wheres = " AND ".join(f"{self.quote(k)} = %s" for k in keys)
        sql = f"DELETE FROM {self._qualified(database, schema, table)} WHERE {wheres}"
        cur.execute(sql, list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    def explain(self, sql: str, database=None) -> dict:
        self._ensure_db(database)
        cur = self._cursor()
        try:
            cur.execute("SET SHOWPLAN_XML ON")
        except Exception as exc:
            raise DatabaseError(f"开启 SQL Server 执行计划失败：{exc}") from exc
        try:
            cur.execute(sql)
            row = cur.fetchone()
            plan = str(row[0]) if row and row[0] is not None else ""
        except Exception as exc:
            raise DatabaseError(f"生成执行计划失败：{exc}") from exc
        finally:
            try:
                cur.execute("SET SHOWPLAN_XML OFF")
            except Exception:
                pass
        return {"format": "xml", "plan": plan}


# ---------------------------------------------------------------------- Oracle
# python-oracledb transient network/instance errors seen on shared instances: the
# server or an intermediary drops a fresh connection. A read-only call can simply
# reconnect and run again; a write cannot (the server may already have applied it).
_ORA_TRANSIENT = ("DPY-4011", "DPY-6005", "DPY-4024", "DPI-1080", "DPY-4014")


def _ora_transient(exc: BaseException) -> bool:
    text = str(exc)
    return any(code in text for code in _ORA_TRANSIENT)


def is_transient_reset(exc: BaseException) -> bool:
    """True when an error is a dropped session rather than a bad request.

    Exposed for the API layer: a read that fails this way may be answered from
    the last good cached result instead of surfacing an error to the tree.
    """
    return _ora_transient(exc)


def _ora_retry_read(method):
    """Reconnect and re-run a read-only adapter call after a transient reset.

    The shared training instance resets the first handshake now and then, and it
    can stay busy long enough that a reconnect fails too. A failed reconnect must
    not abort the retry budget: the next attempt opens a fresh session itself,
    otherwise one busy moment collapses the whole call immediately.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        last: Exception | None = None
        for attempt in range(4):
            try:
                if getattr(self, "_conn", None) is None:
                    self.connect(self._dbname)
                return method(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - re-raised when not transient
                last = exc
                if attempt >= 3 or not _ora_transient(exc):
                    raise
                try:
                    self._reconnect()
                except Exception:  # noqa: BLE001 - next attempt retries the connect
                    self._conn = None
                time.sleep(1.0 + attempt)
        raise last  # pragma: no cover - loop either returns or raises

    return wrapper


class OracleAdapter(BaseAdapter):
    """Oracle Database 12c+ through python-oracledb in thin mode.

    A connection is scoped to one service/PDB; ``schema`` is an Oracle user, so
    the tree is service -> user -> table/view/programmable object.
    """

    dialect = "oracle"
    default_port = 1521
    placeholder = ":1"
    quote = staticmethod(_q_upper)

    def connect(self, dbname: str | None = None) -> None:
        try:
            import oracledb  # noqa: WPS433 - optional dependency
        except Exception as exc:  # pragma: no cover - driver missing
            raise DatabaseError("未安装 oracledb，请执行 pip install oracledb") from exc
        host = self.cfg.get("host") or "127.0.0.1"
        port = int(self.cfg.get("port") or 1521)
        if self.tunnel_addr:
            host, port = self.tunnel_addr
        service = dbname or self.cfg.get("serviceName") or self.cfg.get("database") or "XEPDB1"
        user = self.cfg.get("username") or "system"
        password = self.cfg.get("password") or ""
        dsn = oracledb.makedsn(host, port, service_name=service)
        # ``autocommit`` is a Connection attribute, not a connect() keyword;
        # python-oracledb rejects it with a TypeError. Shared public instances
        # also reset the first handshake now and then (DPY-4011), so retry the
        # handshake a couple of times before reporting failure.
        transient = ("DPY-4011", "DPY-6005", "DPY-4024", "DPI-1080", "DPY-4014")
        remote = host not in ("127.0.0.1", "localhost", "::1")
        attempts = 3 if remote else 1
        conn = None
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                conn = oracledb.connect(user=user, password=password, dsn=dsn)
                break
            except Exception as exc:  # noqa: BLE001 - driver raises oracledb.Error
                last_exc = exc
                if attempt + 1 >= attempts or not any(t in str(exc) for t in transient):
                    raise DatabaseError(f"Oracle 连接失败：{exc}") from exc
                time.sleep(1.5)
        if conn is None:  # pragma: no cover - defensive
            raise DatabaseError(f"Oracle 连接失败：{last_exc}") from last_exc
        try:
            conn.autocommit = True
        except Exception:  # pragma: no cover - driver specific
            pass
        self._conn = conn
        self._dbname = service

    def _cursor(self):
        assert self._conn is not None
        return self._conn.cursor()

    def _reconnect(self) -> None:
        """Drop a half-dead session and open a fresh one for a read retry."""
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        self.connect(self._dbname)
        if self.manual_tx:
            try:
                self._conn.autocommit = False
            except Exception:  # pragma: no cover - driver specific
                pass

    def set_autocommit(self, enabled: bool) -> None:
        self.manual_tx = not enabled
        if enabled:
            self._in_tx = False
        try:
            self._conn.autocommit = enabled
        except Exception as exc:  # pragma: no cover - driver specific
            raise DatabaseError(f"切换自动提交失败：{exc}") from exc

    @_ora_retry_read
    def version(self) -> str:
        cur = self._cursor()
        for sql in (
            "SELECT banner_full FROM v$version WHERE ROWNUM = 1",
            "SELECT version_full FROM v$instance",
            "SELECT * FROM product_component_version WHERE product LIKE 'Oracle Database%'",
        ):
            try:
                cur.execute(sql)
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
            except Exception:
                continue
        return "Oracle"

    def backend_pid(self) -> int:
        try:
            cur = self._cursor()
            cur.execute("SELECT SYS_CONTEXT('USERENV','SID') FROM dual")
            return int(cur.fetchone()[0])
        except Exception:
            return 0

    def cancel_pid(self, pid: int) -> None:
        raise DatabaseError("Oracle 不支持按会话 SID 取消查询，请断开该会话")

    def _current_schema(self) -> str:
        try:
            cur = self._cursor()
            cur.execute("SELECT SYS_CONTEXT('USERENV','CURRENT_SCHEMA') FROM dual")
            return str(cur.fetchone()[0]).upper()
        except Exception:
            return "SYSTEM"

    @_ora_retry_read
    def list_databases(self) -> list[dict]:
        cur = self._cursor()
        out: list[dict] = []
        try:
            cur.execute("SELECT name FROM v$pdbs WHERE open_mode IS NOT NULL ORDER BY name")
            for (name,) in cur.fetchall():
                out.append({"name": name, "kind": "pdb", "size": 0})
        except Exception:
            pass
        if not out:
            out.append({"name": self._dbname or "XEPDB1", "kind": "service", "size": 0})
        return out

    @_ora_retry_read
    def list_schemas(self, database: str | None = None) -> list[str]:
        cur = self._cursor()
        cur.execute(
            "SELECT username FROM all_users WHERE oracle_maintained = 'N' ORDER BY username"
        )
        return [r[0] for r in cur.fetchall()]

    @_ora_retry_read
    def list_objects(self, database: str | None, schema: str | None) -> dict:
        owner = (schema or self._current_schema()).upper()
        cur = self._cursor()
        tables: list[dict] = []
        views: list[dict] = []
        routines: list[dict] = []
        cur.execute(
            "SELECT o.object_name, o.object_type, NVL(t.num_rows, 0)"
            " FROM all_objects o"
            " LEFT JOIN all_tables t ON t.owner = o.owner AND t.table_name = o.object_name"
            " WHERE o.owner = :1 AND o.object_name NOT LIKE 'BIN$%'"
            " AND o.object_type IN ('TABLE','VIEW','MATERIALIZED VIEW','PROCEDURE','FUNCTION','PACKAGE')"
            " ORDER BY o.object_name",
            (owner,),
        )
        for name, otype, num_rows in cur.fetchall():
            if otype == "TABLE":
                tables.append({"name": name, "engine": "TABLE", "rows": int(num_rows or 0), "comment": ""})
            elif otype in ("VIEW", "MATERIALIZED VIEW"):
                views.append({"name": name, "engine": otype.replace(" ", ""), "rows": 0, "comment": ""})
            else:
                routines.append({"name": name, "kind": otype})
        return {
            "tables": tables,
            "views": views,
            "routines": routines,
            "database": self._dbname,
            "schema": owner,
        }

    @staticmethod
    def _type_text(dtype, length, prec, scale) -> str:
        text = str(dtype).upper()
        if text in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW") and length:
            return f"{text}({int(length)})"
        if text == "NUMBER" and prec is not None:
            return f"{text}({int(prec)},{int(scale or 0)})"
        return text

    def _pk_columns(self, owner: str, table: str) -> list[str]:
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT kc.column_name FROM all_constraints k"
                " JOIN all_cons_columns kc ON kc.owner = k.owner AND kc.constraint_name = k.constraint_name"
                " WHERE k.constraint_type = 'P' AND k.owner = :1 AND k.table_name = :2"
                " ORDER BY kc.position",
                (owner, table),
            )
            return [r[0] for r in cur.fetchall()]
        except Exception:
            return []

    @_ora_retry_read
    def _columns(self, schema: str, table: str) -> list[dict]:
        owner = (schema or self._current_schema()).upper()
        table_name = table.upper()
        cur = self._cursor()
        pk = set(self._pk_columns(owner, table_name))
        cur.execute(
            "SELECT c.column_name, c.data_type, c.data_length, c.data_precision, c.data_scale,"
            " c.nullable, c.data_default"
            " FROM all_tab_columns c WHERE c.owner = :1 AND c.table_name = :2"
            " ORDER BY c.column_id",
            (owner, table_name),
        )
        cols = []
        for name, dtype, length, prec, scale, nullable, default in cur.fetchall():
            cols.append(
                {
                    "name": name,
                    "type": self._type_text(dtype, length, prec, scale),
                    "nullable": str(nullable).upper() == "Y",
                    "primary": name in pk,
                    "key": "PRI" if name in pk else "",
                    "default": jsonable(str(default).strip() if default is not None else None),
                    "extra": "",
                    "comment": "",
                }
            )
        return cols

    def _object_ref(self, schema: str, table: str) -> str:
        owner = (schema or self._current_schema()).upper()
        return f"{self.quote(owner)}.{self.quote(table)}"

    @_ora_retry_read
    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        timer = Timer()
        columns = self._columns(schema, table)
        pk = [c["name"] for c in columns if c["primary"]]
        ref = self._object_ref(schema, table)
        sql = f"SELECT * FROM {ref}"
        if where and str(where).strip():
            sql += f" WHERE {where}"
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            sql += f" ORDER BY {self.quote(order_by)} {direction}"
        elif pk:
            sql += " ORDER BY " + ", ".join(self.quote(p) for p in pk)
        sql += f" OFFSET {int(offset)} ROWS FETCH NEXT {int(limit)} ROWS ONLY"
        cur = self._cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            names = [d[0] for d in cur.description or []]
        except Exception as exc:
            raise DatabaseError(f"查询表数据失败：{exc}") from exc
        total = None
        try:
            cur.execute(f"SELECT COUNT(*) FROM {ref}" + (f" WHERE {where}" if where else ""))
            total = int(cur.fetchone()[0])
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": pk,
            "rows": [[jsonable(_lob_read(v)) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool(pk),
        }

    @_ora_retry_read
    def ddl(self, database, schema, table) -> str:
        columns = self._columns(schema, table)
        pks = [c["name"] for c in columns if c["primary"]]
        lines = []
        for c in columns:
            line = f"  {self.quote(c['name'])} {c['type']}"
            if c.get("default") not in (None, ""):
                line += f" DEFAULT {c['default']}"
            if not c["nullable"]:
                line += " NOT NULL"
            lines.append(line)
        if pks:
            lines.append("  PRIMARY KEY (" + ", ".join(self.quote(p) for p in pks) + ")")
        body = ",\n".join(lines)
        return f"CREATE TABLE {self._object_ref(schema, table)} (\n{body}\n);"

    @staticmethod
    def _type_name(desc) -> str:
        dtype = desc[1]
        name = getattr(dtype, "name", None)
        return str(name) if name else str(dtype)

    def query(self, sql: str, database: str | None = None) -> dict:
        # A SELECT is safe to re-run after a dropped session; a DML/DDL statement
        # is not, because the server may already have applied it. Only reads get
        # the reconnect retry.
        read_only = bool(re.match(r"(?is)\s*(select|with)\b", str(sql or "")))
        attempts = 3 if read_only else 1
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._query_once(sql)
            except DatabaseError as exc:
                last = exc
                if attempt + 1 >= attempts or not _ora_transient(exc):
                    raise
                self._reconnect()
                time.sleep(1.2)
        raise last  # pragma: no cover - loop either returns or raises

    def _query_once(self, sql: str) -> dict:
        cur = self._cursor()
        timer = Timer()
        try:
            cur.execute(sql)
            if cur.description:
                names = [d[0] for d in cur.description]
                types = [self._type_name(d) for d in cur.description]
                rows = cur.fetchmany(QUERY_ROW_LIMIT + 1)
                truncated = len(rows) > QUERY_ROW_LIMIT
                results = [
                    {
                        "columns": names,
                        "columnTypes": types,
                        "rows": [[jsonable(_lob_read(v)) for v in r] for r in rows[:QUERY_ROW_LIMIT]],
                        "rowCount": min(len(rows), QUERY_ROW_LIMIT),
                        "truncated": truncated,
                        "affectedRows": cur.rowcount if (cur.rowcount or -1) >= 0 else None,
                    }
                ]
            else:
                affected = cur.rowcount if (cur.rowcount or -1) > 0 else None
                results = [
                    {
                        "columns": [],
                        "rows": [],
                        "rowCount": 0,
                        "affectedRows": affected,
                        "message": "语句已执行",
                    }
                ]
        except Exception as exc:
            raise DatabaseError(f"SQL 执行失败：{exc}") from exc
        return {"results": results, "elapsed": timer.ms(), "sql": sql}

    def _qualified(self, database, schema, table) -> str:
        return self._object_ref(schema, table)

    def insert_row(self, database, schema, table, values) -> dict:
        ref = self._qualified(database, schema, table)
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join(f":{i}" for i in range(1, len(values) + 1))
        sql = f"INSERT INTO {ref} ({cols}) VALUES ({ph})"
        cur = self._cursor()
        cur.execute(sql, list(values.values()))
        return {"affected": 1, "row": None, "sql": sql}

    def update_row(self, database, schema, table, keys, values) -> dict:
        cur = self._cursor()
        args = list(values.values()) + list(keys.values())
        sets = ", ".join(f"{self.quote(k)} = :{i}" for i, k in enumerate(values, 1))
        base = len(values)
        wheres = " AND ".join(f"{self.quote(k)} = :{base + i}" for i, k in enumerate(keys, 1))
        sql = f"UPDATE {self._qualified(database, schema, table)} SET {sets} WHERE {wheres}"
        cur.execute(sql, args)
        return {"affected": cur.rowcount, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        cur = self._cursor()
        wheres = " AND ".join(f"{self.quote(k)} = :{i}" for i, k in enumerate(keys, 1))
        sql = f"DELETE FROM {self._qualified(database, schema, table)} WHERE {wheres}"
        cur.execute(sql, list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    @_ora_retry_read
    def explain(self, sql: str, database=None) -> dict:
        cur = self._cursor()
        try:
            cur.execute("EXPLAIN PLAN FOR " + sql)
            cur.execute(
                "SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY(NULL, NULL, 'BASIC +COST +ROWS'))"
            )
            plan = "\n".join(str(r[0]) for r in cur.fetchall())
        except Exception as exc:
            raise DatabaseError(f"生成执行计划失败：{exc}") from exc
        return {"format": "text", "plan": plan}


# ---------------------------------------------------------------------- Dameng
class DamengAdapter(BaseAdapter):
    """Dameng DM8 through dmPython.

    DM8 follows the Oracle model closely: one instance, ``schema`` is a user,
    identifiers fold to upper case and the data dictionary exposes ALL_USERS /
    ALL_OBJECTS / ALL_TAB_COLUMNS.
    """

    dialect = "dameng"
    default_port = 5236
    placeholder = "?"
    quote = staticmethod(_q_upper)

    def connect(self, dbname: str | None = None) -> None:
        try:
            import dmPython  # noqa: WPS433 - optional dependency
        except Exception as exc:  # pragma: no cover - driver missing
            raise DatabaseError(
                "未安装 dmPython，请执行 pip install dmPython（并安装 DM DPI 客户端库）"
            ) from exc
        host = self.cfg.get("host") or "127.0.0.1"
        port = int(self.cfg.get("port") or 5236)
        if self.tunnel_addr:
            host, port = self.tunnel_addr
        kwargs = {
            "user": self.cfg.get("username") or "SYSDBA",
            "password": self.cfg.get("password") or "",
            "server": host,
            "port": str(port),
        }
        conn = None
        try:
            conn = dmPython.connect(autoCommit=True, connection_timeout=10, **kwargs)
        except TypeError:
            conn = None
        except Exception as exc:
            raise DatabaseError(f"达梦连接失败：{exc}") from exc
        if conn is None:
            try:
                conn = dmPython.connect(**kwargs)
            except Exception as exc:
                raise DatabaseError(f"达梦连接失败：{exc}") from exc
        self._conn = conn
        self._dbname = dbname or self.cfg.get("database") or "DAMENG"

    def _cursor(self):
        assert self._conn is not None
        return self._conn.cursor()

    def set_autocommit(self, enabled: bool) -> None:
        self.manual_tx = not enabled
        if enabled:
            self._in_tx = False
        conn = self._conn
        try:
            setter = getattr(conn, "autocommit", None)
            if callable(setter):
                setter(enabled)
            else:
                conn.autoCommit = enabled
        except Exception as exc:  # pragma: no cover - driver specific
            raise DatabaseError(f"切换自动提交失败：{exc}") from exc

    def version(self) -> str:
        cur = self._cursor()
        for sql in ("SELECT banner FROM v$version", "SELECT * FROM v$version"):
            try:
                cur.execute(sql)
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
            except Exception:
                continue
        return "Dameng"

    def backend_pid(self) -> int:
        try:
            cur = self._cursor()
            cur.execute("SELECT SYS_CONTEXT('USERENV','SESSIONID')")
            return int(cur.fetchone()[0])
        except Exception:
            return 0

    def cancel_pid(self, pid: int) -> None:
        raise DatabaseError("达梦不支持按会话 ID 取消查询，请断开该会话")

    def _current_schema(self) -> str:
        try:
            cur = self._cursor()
            cur.execute("SELECT SF_GET_SCHEMA_NAME_BY_ID(CURRENT_SCHID)")
            return str(cur.fetchone()[0]).upper()
        except Exception:
            return (self.cfg.get("username") or "SYSDBA").upper()

    def list_databases(self) -> list[dict]:
        cur = self._cursor()
        try:
            cur.execute("SELECT name FROM v$database")
            rows = cur.fetchall()
            if rows:
                return [{"name": str(r[0]), "kind": "database", "size": 0} for r in rows]
        except Exception:
            pass
        return [{"name": self._dbname or "DAMENG", "kind": "database", "size": 0}]

    def list_schemas(self, database: str | None = None) -> list[str]:
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT username FROM all_users"
                " WHERE username NOT IN ('SYS','SYSAUDITOR','SYSSSO','CTISYS') ORDER BY username"
            )
            return [r[0] for r in cur.fetchall()]
        except Exception:
            try:
                cur.execute("SELECT DISTINCT owner FROM all_tables ORDER BY owner")
                return [r[0] for r in cur.fetchall()]
            except Exception:
                return []

    def list_objects(self, database: str | None, schema: str | None) -> dict:
        owner = (schema or self._current_schema()).upper()
        cur = self._cursor()
        tables: list[dict] = []
        views: list[dict] = []
        routines: list[dict] = []
        try:
            cur.execute(
                "SELECT object_name, object_type FROM all_objects"
                " WHERE owner = ? AND object_name NOT LIKE 'BIN$%'"
                " AND object_type IN ('TABLE','VIEW','MATERIALIZED VIEW','PROCEDURE','FUNCTION','PACKAGE')"
                " ORDER BY object_name",
                (owner,),
            )
            for name, otype in cur.fetchall():
                kind = str(otype).upper()
                if kind == "TABLE":
                    tables.append({"name": name, "engine": "TABLE", "rows": 0, "comment": ""})
                elif kind in ("VIEW", "MATERIALIZED VIEW"):
                    views.append({"name": name, "engine": kind.replace(" ", ""), "rows": 0, "comment": ""})
                else:
                    routines.append({"name": name, "kind": otype})
        except Exception:
            pass
        return {
            "tables": tables,
            "views": views,
            "routines": routines,
            "database": self._dbname,
            "schema": owner,
        }

    @staticmethod
    def _type_text(dtype, length, prec, scale) -> str:
        text = str(dtype).upper()
        if text in ("VARCHAR", "VARCHAR2", "CHAR", "NCHAR", "VARBINARY", "BINARY") and length:
            return f"{text}({int(length)})"
        if text in ("NUMBER", "DECIMAL", "NUMERIC") and prec is not None:
            return f"{text}({int(prec)},{int(scale or 0)})"
        return text

    def _pk_columns(self, owner: str, table: str) -> list[str]:
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT kc.column_name FROM all_constraints k"
                " JOIN all_cons_columns kc ON kc.owner = k.owner AND kc.constraint_name = k.constraint_name"
                " WHERE k.constraint_type = 'P' AND k.owner = ? AND k.table_name = ?"
                " ORDER BY kc.position",
                (owner, table),
            )
            return [r[0] for r in cur.fetchall()]
        except Exception:
            return []

    def _columns(self, schema: str, table: str) -> list[dict]:
        owner = (schema or self._current_schema()).upper()
        table_name = table.upper()
        cur = self._cursor()
        pk = set(self._pk_columns(owner, table_name))
        cur.execute(
            "SELECT column_name, data_type, data_length, data_precision, data_scale,"
            " nullable, data_default FROM all_tab_columns"
            " WHERE owner = ? AND table_name = ? ORDER BY column_id",
            (owner, table_name),
        )
        cols = []
        for name, dtype, length, prec, scale, nullable, default in cur.fetchall():
            cols.append(
                {
                    "name": name,
                    "type": self._type_text(dtype, length, prec, scale),
                    "nullable": str(nullable).upper() == "Y",
                    "primary": name in pk,
                    "key": "PRI" if name in pk else "",
                    "default": jsonable(str(default).strip() if default is not None else None),
                    "extra": "",
                    "comment": "",
                }
            )
        return cols

    def _object_ref(self, schema: str, table: str) -> str:
        owner = (schema or self._current_schema()).upper()
        return f"{self.quote(owner)}.{self.quote(table)}"

    def table_detail(self, database, schema, table, limit=ROW_LIMIT, offset=0, order_by=None, order_dir="ASC", where=None) -> dict:
        timer = Timer()
        columns = self._columns(schema, table)
        pk = [c["name"] for c in columns if c["primary"]]
        ref = self._object_ref(schema, table)
        base = f"SELECT * FROM {ref}"
        if where and str(where).strip():
            base += f" WHERE {where}"
        order = ""
        if order_by:
            direction = "DESC" if str(order_dir).upper() == "DESC" else "ASC"
            order = f" ORDER BY {self.quote(order_by)} {direction}"
        elif pk:
            order = " ORDER BY " + ", ".join(self.quote(p) for p in pk)
        sql = f"{base}{order} OFFSET {int(offset)} ROWS FETCH NEXT {int(limit)} ROWS ONLY"
        cur = self._cursor()
        try:
            cur.execute(sql)
        except Exception:
            sql = f"{base}{order} LIMIT {int(limit)} OFFSET {int(offset)}"
            cur.execute(sql)
        rows = cur.fetchall()
        names = [d[0] for d in cur.description or []]
        total = None
        try:
            cur.execute(f"SELECT COUNT(*) FROM {ref}" + (f" WHERE {where}" if where else ""))
            total = int(cur.fetchone()[0])
        except Exception:
            pass
        return {
            "columns": columns,
            "primaryKey": pk,
            "rows": [[jsonable(_lob_read(v)) for v in row] for row in rows],
            "rowNames": names,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sql": sql,
            "elapsed": timer.ms(),
            "editable": bool(pk),
        }

    def ddl(self, database, schema, table) -> str:
        columns = self._columns(schema, table)
        pks = [c["name"] for c in columns if c["primary"]]
        lines = []
        for c in columns:
            line = f"  {self.quote(c['name'])} {c['type']}"
            if c.get("default") not in (None, ""):
                line += f" DEFAULT {c['default']}"
            if not c["nullable"]:
                line += " NOT NULL"
            lines.append(line)
        if pks:
            lines.append("  PRIMARY KEY (" + ", ".join(self.quote(p) for p in pks) + ")")
        body = ",\n".join(lines)
        return f"CREATE TABLE {self._object_ref(schema, table)} (\n{body}\n);"

    def query(self, sql: str, database: str | None = None) -> dict:
        cur = self._cursor()
        timer = Timer()
        try:
            cur.execute(sql)
            if cur.description:
                names = [d[0] for d in cur.description]
                types = [str(d[1]) for d in cur.description]
                rows = cur.fetchmany(QUERY_ROW_LIMIT + 1)
                truncated = len(rows) > QUERY_ROW_LIMIT
                results = [
                    {
                        "columns": names,
                        "columnTypes": types,
                        "rows": [[jsonable(_lob_read(v)) for v in r] for r in rows[:QUERY_ROW_LIMIT]],
                        "rowCount": min(len(rows), QUERY_ROW_LIMIT),
                        "truncated": truncated,
                        "affectedRows": cur.rowcount if (cur.rowcount or -1) >= 0 else None,
                    }
                ]
            else:
                affected = cur.rowcount if (cur.rowcount or -1) > 0 else None
                results = [
                    {
                        "columns": [],
                        "rows": [],
                        "rowCount": 0,
                        "affectedRows": affected,
                        "message": "语句已执行",
                    }
                ]
        except Exception as exc:
            raise DatabaseError(f"SQL 执行失败：{exc}") from exc
        return {"results": results, "elapsed": timer.ms(), "sql": sql}

    def _qualified(self, database, schema, table) -> str:
        return self._object_ref(schema, table)

    def insert_row(self, database, schema, table, values) -> dict:
        ref = self._qualified(database, schema, table)
        cols = ", ".join(self.quote(k) for k in values)
        ph = ", ".join("?" for _ in values)
        sql = f"INSERT INTO {ref} ({cols}) VALUES ({ph})"
        cur = self._cursor()
        cur.execute(sql, list(values.values()))
        return {"affected": 1, "row": None, "sql": sql}

    def update_row(self, database, schema, table, keys, values) -> dict:
        cur = self._cursor()
        sets = ", ".join(f"{self.quote(k)} = ?" for k in values)
        wheres = " AND ".join(f"{self.quote(k)} = ?" for k in keys)
        sql = f"UPDATE {self._qualified(database, schema, table)} SET {sets} WHERE {wheres}"
        cur.execute(sql, list(values.values()) + list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    def delete_row(self, database, schema, table, keys) -> dict:
        cur = self._cursor()
        wheres = " AND ".join(f"{self.quote(k)} = ?" for k in keys)
        sql = f"DELETE FROM {self._qualified(database, schema, table)} WHERE {wheres}"
        cur.execute(sql, list(keys.values()))
        return {"affected": cur.rowcount, "sql": sql}

    def explain(self, sql: str, database=None) -> dict:
        # Dameng spells it ``EXPLAIN FOR <query>`` and returns the plan as a
        # result set (LEVEL_ID / OPERATION / TAB_NAME / IDX_NAME / ROW_NUMS ...).
        cur = self._cursor()
        try:
            cur.execute("EXPLAIN FOR " + sql)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchall()
        except Exception as exc:
            raise DatabaseError(f"生成执行计划失败：{exc}") from exc
        index = {name: pos for pos, name in enumerate(cols)}

        def cell(row, name, default=""):
            pos = index.get(name)
            if pos is None or pos >= len(row):
                return default
            value = row[pos]
            return "" if value is None or str(value).upper() == "NULL" else str(value)

        lines = ["DM8 执行计划（EXPLAIN FOR）"]
        for row in rows:
            try:
                level = int(cell(row, "LEVEL_ID", "0") or 0)
            except ValueError:
                level = 0
            parts = [cell(row, "OPERATION")]
            table = cell(row, "TAB_NAME")
            if table:
                parts.append(f"table={table}")
            idx_name = cell(row, "IDX_NAME")
            if idx_name:
                parts.append(f"index={idx_name}")
            scan = cell(row, "SCAN_TYPE")
            if scan:
                parts.append(f"scan={scan}")
            rows_n = cell(row, "ROW_NUMS")
            if rows_n:
                parts.append(f"rows={rows_n}")
            cost = cell(row, "COST")
            if cost:
                parts.append(f"cost={cost}")
            advice = cell(row, "ADVICE_INFO")
            if advice:
                parts.append(f"advice={advice}")
            lines.append("  " * level + " ".join(p for p in parts if p))
        return {"format": "text", "plan": "\n".join(lines)}


DIALECTS = {
    "mysql": MySQLAdapter,
    "postgresql": PostgresAdapter,
    "sqlite": SQLiteAdapter,
    "duckdb": DuckDBAdapter,
    "clickhouse": ClickHouseAdapter,
    "mongodb": MongoAdapter,
    "sqlserver": SQLServerAdapter,
    "oracle": OracleAdapter,
    "dameng": DamengAdapter,
}


def make_adapter(cfg: dict) -> BaseAdapter:
    dialect = (cfg.get("dbType") or "mysql").lower()
    if dialect in ("mariadb", "tidb", "mysql"):
        cls = MySQLAdapter
    elif dialect in ("postgres", "postgresql", "kingbase", "opengauss", "polardb"):
        cls = PostgresAdapter
    elif dialect in ("sqlite", "sqlite3"):
        cls = SQLiteAdapter
    elif dialect in ("duckdb",):
        cls = DuckDBAdapter
    elif dialect in ("clickhouse",):
        cls = ClickHouseAdapter
    elif dialect in ("mongodb", "mongo"):
        cls = MongoAdapter
    elif dialect in ("sqlserver", "mssql", "sql_server", "azure-sql"):
        cls = SQLServerAdapter
    elif dialect in ("oracle", "oracledb"):
        cls = OracleAdapter
    elif dialect in ("dameng", "dm", "dm8"):
        cls = DamengAdapter
    else:
        raise DatabaseError(f"暂不支持的数据库类型：{dialect}")
    return cls(cfg)


def with_tunnel(adapter: BaseAdapter, tunnel_host_port) -> BaseAdapter:
    if tunnel_host_port:
        adapter.tunnel = tunnel_host_port
    return adapter
