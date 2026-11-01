#!/usr/bin/env python3
"""Seed the local H1KHub replica with demo assets matching the lab environment.

Usage: python3 scripts/seed_demo.py [--home ~/.h1khub] [--reset-settings]

The demo targets the local lab services used during development:
  SSH        127.0.0.1:22    hexlab / Hexlab@123
  MariaDB    127.0.0.1:3307  hexhub / hexhub123 (database demo)
  PostgreSQL 127.0.0.1:5433  hexhub / hexhub123 (database demo)
  ClickHouse 127.0.0.1:9000  default / (empty)   (database demo)
  MongoDB    127.0.0.1:27017 (database demo)
  Redis      127.0.0.1:6380  no password
  SQLite     /home/kali/hexhub-lab/demo.db
  Docker     /var/run/docker.sock
  Dameng     127.0.0.1:52360  HEXHUB / Hexhub@12345 (hexhub-dm8 container)
  Oracle     ecourse.org:1521/ol8.ecourse.org  scott / tiger (shared Oracle 19c
             training instance; override with H1KHUB_DEMO_ORACLE_HOST /
             _PORT / _SERVICE / _USER / _PASSWORD)
  SQL Server 127.0.0.1:14330  sa / Hexhub@12345 (hexhub-mssql:2019 container;
             override with H1KHUB_DEMO_SQLSERVER_HOST / _PORT / _USER /
             _PASSWORD / _DATABASE)
Edit or delete them from the UI if your environment differs.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from store import Store  # noqa: E402

SSH_PASSWORD = os.environ.get("H1KHUB_DEMO_SSH_PASSWORD", "Hexlab@123")
DB_PASSWORD = os.environ.get("H1KHUB_DEMO_DB_PASSWORD", "hexhub123")
SQLITE_PATH = os.environ.get("H1KHUB_DEMO_SQLITE", "/home/kali/hexhub-lab/demo.db")
DUCKDB_PATH = os.environ.get("H1KHUB_DEMO_DUCKDB", "/home/kali/hexhub-lab/analytics.duckdb")
SQLSERVER_PASSWORD = os.environ.get("H1KHUB_DEMO_SQLSERVER_PASSWORD", "Hexhub@12345")
# The SQL Server demo points at the local lab instance started by
# ``scripts/start_lab_services.sh`` (hexhub-mssql:2019 on 127.0.0.1:14330).
# Override the endpoint with H1KHUB_DEMO_SQLSERVER_* when your own instance
# differs; the asset is still only created when the port answers.
SQLSERVER_HOST = os.environ.get("H1KHUB_DEMO_SQLSERVER_HOST", "127.0.0.1")
SQLSERVER_PORT = int(os.environ.get("H1KHUB_DEMO_SQLSERVER_PORT", "14330"))
SQLSERVER_USER = os.environ.get("H1KHUB_DEMO_SQLSERVER_USER", "sa")
SQLSERVER_DATABASE = os.environ.get("H1KHUB_DEMO_SQLSERVER_DATABASE", "hexhub_demo")
# The Oracle demo points at a real, shared Oracle 19c instance (public training
# database), so the read path runs against genuine EMP/DEPT data. Override the
# endpoint with H1KHUB_DEMO_ORACLE_* when your own instance differs.
ORACLE_HOST = os.environ.get("H1KHUB_DEMO_ORACLE_HOST", "ecourse.org")
ORACLE_PORT = int(os.environ.get("H1KHUB_DEMO_ORACLE_PORT", "1521"))
ORACLE_SERVICE = os.environ.get("H1KHUB_DEMO_ORACLE_SERVICE", "ol8.ecourse.org")
ORACLE_USER = os.environ.get("H1KHUB_DEMO_ORACLE_USER", "scott")
ORACLE_PASSWORD = os.environ.get("H1KHUB_DEMO_ORACLE_PASSWORD", "tiger")
DAMENG_PASSWORD = os.environ.get("H1KHUB_DEMO_DAMENG_PASSWORD", "Hexhub@12345")


def _tcp_open(host: str, port: int, timeout: float = 0.6) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=os.path.expanduser("~/.h1khub"))
    parser.add_argument("--reset-settings", action="store_true")
    args = parser.parse_args()

    store = Store(os.path.abspath(os.path.expanduser(args.home)))
    assets = store.list_assets()
    by_name = {a["name"]: a for a in assets}

    def ensure(type_id: int, name: str, config: dict, parent: str | None = None, color: str | None = None):
        if name in by_name:
            print(f"skip  {name}")
            return by_name[name]
        asset = store.create_asset(type_id, name, parent, config, color)
        by_name[name] = asset
        print(f"create {name}")
        return asset

    folder = ensure(0, "生产环境", {})
    ssh = ensure(
        2,
        "SSH hexlab",
        {
            "host": "127.0.0.1",
            "port": 22,
            "username": "hexlab",
            "authType": "password",
            "password": SSH_PASSWORD,
            "termType": "xterm-256color",
            "connectTimeout": 15,
            "keepalive": 30,
        },
        folder["id"],
        "#7f9cf5",
    )
    ensure(
        2,
        "SSH 跳板演示",
        {
            "host": "127.0.0.1",
            "port": 22,
            "username": "hexlab",
            "authType": "password",
            "password": SSH_PASSWORD,
            "jumpAssetId": ssh["id"],
            "termType": "xterm-256color",
        },
        folder["id"],
        "#9aa0a6",
    )
    mysql = ensure(
        4,
        "MySQL demo",
        {
            "dbType": "mysql",
            "host": "127.0.0.1",
            "port": 3307,
            "username": "hexhub",
            "password": DB_PASSWORD,
            "database": "demo",
            "charset": "utf8mb4",
        },
        folder["id"],
        "#5aa9e6",
    )
    ensure(
        4,
        "PostgreSQL demo",
        {
            "dbType": "postgresql",
            "host": "127.0.0.1",
            "port": 5433,
            "username": "hexhub",
            "password": DB_PASSWORD,
            "database": "demo",
            "ssl": False,
        },
        folder["id"],
        "#5aa9e6",
    )
    ensure(
        4,
        "SQLite demo",
        {"dbType": "sqlite", "file": SQLITE_PATH},
        folder["id"],
        "#5aa9e6",
    )
    ensure(
        4,
        "DuckDB demo",
        {"dbType": "duckdb", "file": DUCKDB_PATH},
        folder["id"],
        "#5aa9e6",
    )
    ensure(
        4,
        "ClickHouse demo",
        {
            "dbType": "clickhouse",
            "host": "127.0.0.1",
            "port": 9000,
            "username": "default",
            "password": "",
            "database": "demo",
        },
        folder["id"],
        "#5aa9e6",
    )
    ensure(
        4,
        "MongoDB demo",
        {
            "dbType": "mongodb",
            "host": "127.0.0.1",
            "port": 27017,
            "database": "demo",
        },
        folder["id"],
        "#5aa9e6",
    )
    # SQL Server / Oracle / Dameng are only seeded when a lab instance answers,
    # so a missing container never leaves a broken demo asset behind.
    for name, cfg in (
        (
            "SQL Server demo",
            {
                "dbType": "sqlserver",
                "host": SQLSERVER_HOST,
                "port": SQLSERVER_PORT,
                "username": SQLSERVER_USER,
                "password": SQLSERVER_PASSWORD,
                "database": SQLSERVER_DATABASE,
            },
        ),
        (
            "Oracle demo",
            {
                "dbType": "oracle",
                "host": ORACLE_HOST,
                "port": ORACLE_PORT,
                "username": ORACLE_USER,
                "password": ORACLE_PASSWORD,
                "database": ORACLE_SERVICE,
            },
        ),
        (
            "达梦 demo",
            {
                "dbType": "dameng",
                "host": "127.0.0.1",
                "port": 52360,
                "username": "HEXHUB",
                "password": DAMENG_PASSWORD,
                "database": "DAMENG",
            },
        ),
    ):
        # A remote instance needs a real network round-trip, not a loopback probe.
        reach_timeout = 3.0 if cfg["host"] not in ("127.0.0.1", "localhost", "::1") else 0.6
        if not _tcp_open(cfg["host"], cfg["port"], reach_timeout):
            print(f"skip  {name}（实例 {cfg['host']}:{cfg['port']} 未监听）")
            continue
        ensure(4, name, cfg, folder["id"], "#5aa9e6")
    ensure(
        15,
        "Redis local",
        {"host": "127.0.0.1", "port": 6380, "db": 0, "tls": False},
        folder["id"],
        "#e05d5d",
    )
    ensure(
        15,
        "Redis JSON",
        {"host": "127.0.0.1", "port": 6385, "db": 0, "tls": False},
        folder["id"],
        "#e05d5d",
    )
    ensure(
        7,
        "Docker local",
        {"mode": "local", "socketPath": "/var/run/docker.sock"},
        folder["id"],
        "#4c9aff",
    )
    ensure(
        16,
        "本地终端",
        {"shell": "/bin/bash", "cwd": os.path.expanduser("~")},
        None,
        "#37c2b1",
    )
    ensure(
        5,
        "磁盘巡检",
        {"assetId": ssh["id"], "command": "df -h && free -m && uptime"},
        folder["id"],
        "#f0a35e",
    )
    ensure(
        6,
        "用户列表查询",
        {"assetId": mysql["id"], "database": "demo", "sql": "SELECT * FROM users ORDER BY id LIMIT 100;"},
        folder["id"],
        "#7ec699",
    )

    if args.reset_settings or not store.get_settings().get("appearance", {}).get("theme"):
        store.update_settings(
            {
                "appearance": {
                    "theme": "dark",
                    "accent": "blue",
                    "fontSize": 13,
                    "terminalFontSize": 13,
                    "language": "zh-CN",
                }
            }
        )
    store.close()
    print(f"\nDone. Data home: {os.path.abspath(os.path.expanduser(args.home))}")


if __name__ == "__main__":
    main()
