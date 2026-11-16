"""Persistent asynchronous task workers and maintenance scheduling."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from ..config import ConfigManager
from ..db.database import Database
from ..db.repository import MemoryRepository, utcnow
from ..domain import TaskType

TaskHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class TaskRunner:
    """Poll SQLite for pending tasks and execute registered handlers."""

    def __init__(
        self,
        database: Database,
        config: ConfigManager,
        handlers: dict[str, TaskHandler] | None = None,
    ) -> None:
        self.database = database
        self.config = config
        self.handlers = handlers or {}
        self._workers: list[asyncio.Task] = []
        self._event = asyncio.Event()
        self._stopping = False

    def register(self, task_type: str, handler: TaskHandler) -> None:
        self.handlers[task_type] = handler

    def repository(self, session) -> MemoryRepository:
        return MemoryRepository(session, fts_enabled=self.database.fts_enabled)

    async def start(self) -> None:
        worker_count = max(0, int(self.config.get("tasks.workers", 2) or 0))
        if worker_count == 0:
            logger.info("Task workers disabled by configuration.")
            return
        async with self.database.session() as session:
            repository = self.repository(session)
            recovered = await repository.recover_running_tasks()
        if recovered:
            logger.warning("Re-queued {} task(s) left running by a previous process.", recovered)
        for index in range(worker_count):
            self._workers.append(
                asyncio.create_task(self._worker(index), name=f"memory-task-{index}")
            )
        logger.info("Started {} memory task worker(s).", worker_count)

    async def stop(self) -> None:
        self._stopping = True
        self._event.set()
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def notify(self) -> None:
        self._event.set()

    async def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        priority: int = 0,
        dedupe_key: str | None = None,
        scheduled_at=None,
    ) -> str | None:
        async with self.database.session() as session:
            repository = self.repository(session)
            task = await repository.enqueue_task(
                task_type,
                payload,
                priority=priority,
                dedupe_key=dedupe_key,
                scheduled_at=scheduled_at,
                max_attempts=int(self.config.get("tasks.max_attempts", 3) or 3),
            )
            task_id = task.id if task else None
        if task_id:
            await self.notify()
        return task_id

    async def _worker(self, index: int) -> None:
        poll_interval = max(0.1, float(self.config.get("tasks.poll_interval_seconds", 1.0) or 1.0))
        while not self._stopping:
            try:
                processed = await self.run_once()
                if processed:
                    continue
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=poll_interval)
                except TimeoutError:
                    pass
                self._event.clear()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Memory task worker {} crashed; continuing.", index)
                await asyncio.sleep(poll_interval)

    async def run_once(self) -> bool:
        """Claim and run one task. Returns ``True`` when work was processed."""
        task_id: str | None = None
        task_type = ""
        payload: dict[str, Any] = {}
        async with self.database.session() as session:
            repository = self.repository(session)
            task = await repository.claim_task()
            if task is None:
                return False
            task_id = task.id
            task_type = task.task_type
            payload = dict(task.payload or {})
        if task_id is None:
            return False

        handler = self.handlers.get(task_type)
        if handler is None:
            await self._fail(task_id, f"no handler registered for task type {task_type}")
            return True
        try:
            result = await handler(payload)
            async with self.database.session() as session:
                repository = self.repository(session)
                await repository.complete_task(
                    task_id,
                    result=result if isinstance(result, dict) else {"result": result},
                )
        except Exception as exc:  # noqa: BLE001 - task failures are recorded
            logger.exception("Memory task {} ({}) failed.", task_id, task_type)
            await self._fail(task_id, f"{type(exc).__name__}: {exc}")
        return True

    async def _fail(self, task_id: str, message: str) -> None:
        async with self.database.session() as session:
            repository = self.repository(session)
            await repository.fail_task(task_id, message)


class MaintenanceScheduler:
    """Periodically enqueue decay and consolidation work."""

    def __init__(
        self,
        database: Database,
        config: ConfigManager,
        runner: TaskRunner,
    ) -> None:
        self.database = database
        self.config = config
        self.runner = runner
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        if bool(self.config.get("tasks.run_maintenance_on_startup", True)):
            await self._enqueue_cycle()
        self._task = asyncio.create_task(self._loop(), name="memory-maintenance")
        logger.info("Memory maintenance scheduler started.")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        hours = max(1.0, float(self.config.get("tasks.maintenance_interval_hours", 24) or 24))
        while not self._stopping:
            try:
                await asyncio.sleep(hours * 3600)
                await self._enqueue_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Memory maintenance scheduling failed.")

    async def _enqueue_cycle(self) -> None:
        stamp = utcnow().strftime("%Y-%m-%d")
        if bool(self.config.get("memory.decay.enabled", True)):
            await self.runner.enqueue(
                TaskType.DECAY_SCAN,
                {},
                priority=-10,
                dedupe_key=f"maintenance:decay:{stamp}",
            )
        if bool(self.config.get("memory.consolidation.enabled", True)):
            await self.runner.enqueue(
                TaskType.CONSOLIDATE,
                {"principal": {"user_id": self.config.default_user_id, "role": "admin"}},
                priority=-10,
                dedupe_key=f"maintenance:consolidate:{stamp}",
            )
