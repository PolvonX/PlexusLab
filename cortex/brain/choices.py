# cortex/brain/choices.py
"""Ожидающие ответа CEO квизы/выборы мозга с inline-кнопками.

Тот же паттерн, что у PendingActionStore (tmp + os.replace под
asyncio.Lock, переживает перезапуск) — но хранит не действие для
исполнения, а варианты текста: нажатие кнопки возвращает выбранный
вариант в разговор как обычное сообщение CEO (см. brain_router.py).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import utcnow_iso


@dataclass(slots=True)
class PendingChoice:
    id: str
    chat_id: int
    message_id: int | None
    requester_id: int
    options: list[str]
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "requester_id": self.requester_id,
            "options": self.options,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PendingChoice":
        return cls(
            id=raw["id"],
            chat_id=raw["chat_id"],
            message_id=raw.get("message_id"),
            requester_id=raw["requester_id"],
            options=list(raw.get("options") or []),
            created_at=raw.get("created_at") or utcnow_iso(),
        )


class PendingChoiceStore:
    """Реестр отложенных квизов/выборов — маленький аналог PendingActionStore."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._choices: dict[str, PendingChoice] = {}
        self._lock = asyncio.Lock()
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self._choices = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._choices = {}
            return
        if not isinstance(raw, dict):
            self._choices = {}
            return
        self._choices = {
            entry["id"]: PendingChoice.from_dict(entry) for entry in raw.get("choices", [])
        }

    # ------------------------------------------------------------------
    async def add(self, choice: PendingChoice) -> None:
        async with self._lock:
            self._choices[choice.id] = choice
            self._write_unlocked()

    async def pop(self, choice_id: str) -> PendingChoice | None:
        async with self._lock:
            choice = self._choices.pop(choice_id, None)
            if choice is not None:
                self._write_unlocked()
            return choice


    async def clear_by_chat(self, chat_id: int) -> None:
        async with self._lock:
            original_len = len(self._choices)
            self._choices = {k: v for k, v in self._choices.items() if v.chat_id != chat_id}
            if len(self._choices) != original_len:
                self._write_unlocked()

    # ------------------------------------------------------------------
    def _write_unlocked(self) -> None:
        payload = {"choices": [c.to_dict() for c in self._choices.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
