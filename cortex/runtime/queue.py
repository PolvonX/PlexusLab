"""Планировщик задач Plexus Lab.

Две гарантии:
  1. Глобальный семафор — не больше N процессов `agy` одновременно,
     иначе сервер ляжет от трёх параллельных сборок.
  2. Пер-проектный лок — два агента не правят один репозиторий в один
     момент. Разные проекты при этом идут параллельно.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

from ..logging_setup import get_logger

log = get_logger("scheduler")

T = TypeVar("T")


@dataclass(slots=True)
class TaskInfo:
    task_id: str
    agent: str
    project: str
    instruction: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    state: str = "queued"

    @property
    def elapsed(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


class TaskScheduler:
    """Ограничивает параллелизм и ведёт список активных задач для /status."""

    def __init__(self, *, max_parallel: int, serialize_per_project: bool) -> None:
        self._global = asyncio.Semaphore(max_parallel)
        self._serialize = serialize_per_project
        self._project_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._active: dict[str, TaskInfo] = {}

    # ------------------------------------------------------------------
    @property
    def active(self) -> list[TaskInfo]:
        return sorted(self._active.values(), key=lambda t: t.started_at)

    def is_busy(self, project: str) -> bool:
        return any(t.project == project and t.state == "running" for t in self._active.values())

    # ------------------------------------------------------------------
    async def submit(
        self,
        info: TaskInfo,
        coro_factory: Callable[[], Awaitable[T]],
    ) -> T:
        """Поставить задачу в очередь и дождаться результата."""
        self._active[info.task_id] = info
        try:
            project_lock = (
                self._project_locks[info.project]
                if self._serialize
                else _NullLock()
            )
            async with project_lock:
                async with self._global:
                    info.state = "running"
                    info.started_at = datetime.now(timezone.utc)
                    log.info(
                        "Задача %s → %s @%s стартовала",
                        info.task_id, info.project, info.agent,
                    )
                    return await coro_factory()
        finally:
            self._active.pop(info.task_id, None)
            log.debug("Задача %s снята с учёта", info.task_id)


class _NullLock:
    """Заглушка, когда сериализация по проектам отключена."""

    async def __aenter__(self) -> "_NullLock":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False
