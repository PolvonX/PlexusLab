"""Лёгкое персистентное состояние Cortex.

Пока хранит одно: какой проект «активен» в каком чате. Отдельный файл, а
не реестр сотрудников — чтобы случайная порча состояния не утащила за
собой боевые токены ботов.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

log = get_logger("state")


class ChatState:
    """chat_id → активный проект + произвольные заметки."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "state.json"
        self._data: dict[str, Any] = {"chat_projects": {}}
        self._lock = asyncio.Lock()
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("state.json не читается (%s) — начинаю с чистого листа", exc)
            self._data = {"chat_projects": {}}
        self._data.setdefault("chat_projects", {})

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    def active_project(self, chat_id: int) -> str | None:
        return self._data["chat_projects"].get(str(chat_id))

    async def set_active_project(self, chat_id: int, project: str | None) -> None:
        async with self._lock:
            if project is None:
                self._data["chat_projects"].pop(str(chat_id), None)
            else:
                self._data["chat_projects"][str(chat_id)] = project
            self._flush()
        log.info("Чат %s теперь работает над проектом %s", chat_id, project)
