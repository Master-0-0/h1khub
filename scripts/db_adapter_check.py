"""Adapter-level checks that need no live instance.

Used by the SQL Server / Oracle / Dameng test scripts when their lab container
is absent: they prove the adapter is registered, quotes and binds per dialect,
and fails closed with a readable error instead of pretending to connect.
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import db  # noqa: E402


def code_path_checks(dialect: str, expect: dict) -> tuple[int, int, list[str]]:
    """Return (pass_count, fail_count, failures) for the offline adapter checks."""
    passed = 0
    failed = 0
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            failures.append(name)
            print(f"  FAIL  {name}  {detail}")

    cls = db.DIALECTS.get(dialect)
    check(f"DIALECTS 注册 {dialect}", cls is not None, str(list(db.DIALECTS)))
    if cls is None:
        return passed, failed, failures

    adapter = db.make_adapter({"dbType": dialect})
    check("make_adapter 类型", type(adapter).__name__ == expect["class"], type(adapter).__name__)
    check("dialect 属性", adapter.dialect == dialect, adapter.dialect)
    check("默认端口", adapter.default_port == expect["port"], str(adapter.default_port))
    check("占位符", adapter.placeholder == expect["placeholder"], adapter.placeholder)
    check(
        "标识符引用",
        adapter.quote("users") == expect["quote"],
        f"{adapter.quote('users')} != {expect['quote']}",
    )

    for alias in expect.get("aliases", ()):
        try:
            alias_adapter = db.make_adapter({"dbType": alias})
            check(f"别名 {alias}", alias_adapter.dialect == dialect, alias_adapter.dialect)
        except Exception as exc:  # noqa: BLE001
            check(f"别名 {alias}", False, f"{type(exc).__name__}: {exc}")

    # A closed port must surface DatabaseError, never a raw driver error.
    try:
        probe = db.make_adapter(
            {
                "dbType": dialect,
                "host": "127.0.0.1",
                "port": "1",
                "username": expect.get("user", "u"),
                "password": "x",
                "database": expect.get("database", ""),
            }
        )
        probe.connect(None)
        check("关闭端口应报错", False, "connect() unexpectedly succeeded")
    except db.DatabaseError as exc:
        check("关闭端口报 DatabaseError", True)
        check("错误信息含方言名", expect["error_token"] in str(exc), str(exc)[:160])
    except Exception as exc:  # noqa: BLE001
        check("关闭端口报 DatabaseError", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    return passed, failed, failures
