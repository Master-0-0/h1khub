"""In-process background task queue for long-running operations."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from util import new_id, now_iso

MAX_TASKS = 200


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.runners: dict[str, asyncio.Task] = {}

    def submit(self, kind: str, title: str, factory: Callable[[], Awaitable[Any]]) -> dict:
        task_id = new_id("task")
        record = {
            "id": task_id,
            "kind": kind,
            "title": title,
            "status": "queued",
            "createdAt": now_iso(),
            "startedAt": None,
            "finishedAt": None,
            "elapsedMs": None,
            "result": None,
            "error": None,
        }
        self.tasks[task_id] = record
        started = time.time()

        async def runner() -> None:
            record["status"] = "running"
            record["startedAt"] = now_iso()
            try:
                record["result"] = await factory()
                record["status"] = "success"
            except asyncio.CancelledError:
                record["status"] = "cancelled"
                record["error"] = "任务已取消"
                raise
            except Exception as exc:
                record["status"] = "failed"
                record["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                record["finishedAt"] = now_iso()
                record["elapsedMs"] = int((time.time() - started) * 1000)
                self.runners.pop(task_id, None)
                self._prune()

        self.runners[task_id] = asyncio.create_task(runner())
        return self._public(record)

    def _prune(self) -> None:
        if len(self.tasks) <= MAX_TASKS:
            return
        finished = sorted(
            (t for t in self.tasks.values() if t["status"] in ("success", "failed", "cancelled")),
            key=lambda t: t["finishedAt"] or "",
        )
        for item in finished[: len(self.tasks) - MAX_TASKS]:
            self.tasks.pop(item["id"], None)

    @staticmethod
    def _public(record: dict) -> dict:
        return dict(record)

    def list(self, limit: int = 50) -> list[dict]:
        items = sorted(self.tasks.values(), key=lambda t: t["id"], reverse=True)
        return [self._public(t) for t in items[: max(1, min(limit, 200))]]

    def get(self, task_id: str) -> dict | None:
        record = self.tasks.get(task_id)
        return self._public(record) if record else None

    def cancel(self, task_id: str) -> bool:
        runner = self.runners.get(task_id)
        if not runner or runner.done():
            return False
        runner.cancel()
        return True

    async def cancel_all(self) -> None:
        for runner in list(self.runners.values()):
            runner.cancel()
        for runner in list(self.runners.values()):
            try:
                await runner
            except Exception:
                pass
        self.runners.clear()
