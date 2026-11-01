#!/usr/bin/env python3
"""Create the SQL Server demo database/tables for the replica lab.

Idempotent: waits for the TDS port, then creates ``hexhub_demo`` with
``dbo.users`` / ``dbo.orders`` and two seed rows each. Re-running keeps data.

Usage: python3 scripts/lab_mssql_setup.py [--host 127.0.0.1] [--port 14330]
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

PASSWORD = os.environ.get("H1KHUB_DEMO_SQLSERVER_PASSWORD", "Hexhub@12345")

USERS_DDL = """
IF OBJECT_ID('dbo.users') IS NULL
CREATE TABLE dbo.users (
    id         INT IDENTITY(1,1) PRIMARY KEY,
    name       NVARCHAR(50)  NOT NULL,
    email      NVARCHAR(120) NULL,
    role       NVARCHAR(20)  NOT NULL CONSTRAINT df_users_role DEFAULT 'user',
    created_at DATETIME2(0)  NOT NULL CONSTRAINT df_users_created DEFAULT SYSUTCDATETIME()
)
"""

ORDERS_DDL = """
IF OBJECT_ID('dbo.orders') IS NULL
CREATE TABLE dbo.orders (
    id         INT IDENTITY(1,1) PRIMARY KEY,
    user_id    INT NOT NULL REFERENCES dbo.users(id),
    amount     DECIMAL(10,2) NOT NULL,
    status     NVARCHAR(16) NOT NULL CONSTRAINT df_orders_status DEFAULT 'paid',
    created_at DATETIME2(0) NOT NULL CONSTRAINT df_orders_created DEFAULT SYSUTCDATETIME()
)
"""


def wait_port(host: str, port: int, timeout: int = 240) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(3)
    raise SystemExit(f"SQL Server 端口 {host}:{port} 在 {timeout}s 内未就绪")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=14330)
    parser.add_argument("--wait", type=int, default=240)
    args = parser.parse_args()

    wait_port(args.host, args.port, args.wait)
    import pymssql

    last: Exception | None = None
    conn = None
    for attempt in range(30):
        try:
            conn = pymssql.connect(
                server=args.host,
                port=str(args.port),
                user="sa",
                password=PASSWORD,
                database="master",
                charset="UTF-8",
                login_timeout=15,
                autocommit=True,
            )
            break
        except Exception as exc:  # server may still be initialising
            last = exc
            time.sleep(5)
    if conn is None:
        raise SystemExit(f"SQL Server 登录失败：{last}")

    cur = conn.cursor()
    cur.execute("SELECT @@VERSION")
    banner = str(cur.fetchone()[0]).splitlines()[0]
    print("connected:", banner)

    cur.execute("IF DB_ID('hexhub_demo') IS NULL CREATE DATABASE hexhub_demo")
    conn.commit()
    cur.execute("USE hexhub_demo")
    cur.execute(USERS_DDL)
    cur.execute(ORDERS_DDL)
    cur.execute("SELECT COUNT(*) FROM dbo.users")
    if int(cur.fetchone()[0]) == 0:
        cur.execute(
            "INSERT INTO dbo.users (name, email, role) VALUES"
            " (N'Alice', N'alice@example.com', N'admin'),"
            " (N'Bob', N'bob@example.com', N'user')"
        )
        cur.execute(
            "INSERT INTO dbo.orders (user_id, amount, status) VALUES"
            " (1, 19.90, N'paid'), (2, 5.50, N'pending')"
        )
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM dbo.users")
    users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dbo.orders")
    orders = cur.fetchone()[0]
    cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(64))")
    print(f"product version: {cur.fetchone()[0]}")
    print(f"hexhub_demo ready: users={users} orders={orders}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
