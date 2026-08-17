# cortex/brain/pending.py
"""Действия мозга, ждущие подтверждения CEO кнопками в Telegram.

Тот же паттерн атомарной записи, что у ChatState/EmployeeRegistry: tmp +
os.replace под asyncio.Lock, переживает перезапуск процесса.
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
class PendingAction:
    id: str
    chat_id: int
    message_id: int | None
    requester_id: int
    tool: str
    args: dict[str, Any]
    risk: str
    summary: str
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "requester_id": self.requester_id,
            "tool": self.tool,
            "args": self.args,
            "risk": self.risk,
            "summary": self.summary,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PendingAction":
        return cls(
            id=raw["id"],
            chat_id=raw["chat_id"],
            message_id=raw.get("message_id"),
            requester_id=raw["requester_id"],
            tool=raw["tool"],
            args=raw.get("args") or {},
            risk=raw.get("risk", "risky"),
            summary=raw.get("summary", ""),
            created_at=raw.get("created_at") or utcnow_iso(),
        )


class PendingActionStore:
    """Реестр отложенных действий — маленький аналог EmployeeRegistry."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._actions: dict[str, PendingAction] = {}
        self._lock = asyncio.Lock()
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self._actions = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._actions = {}
            return
        if not isinstance(raw, dict):
            self._actions = {}
            return
        self._actions = {
            entry["id"]: PendingAction.from_dict(entry) for entry in raw.get("actions", [])
        }

    def get(self, action_id: str) -> PendingAction | None:
        return self._actions.get(action_id)

    # ------------------------------------------------------------------
    async def add(self, action: PendingAction) -> None:
        async with self._lock:
            self._actions[action.id] = action
            self._write_unlocked()

    async def pop(self, action_id: str) -> PendingAction | None:
        async with self._lock:
            action = self._actions.pop(action_id, None)
            if action is not None:
                self._write_unlocked()
            return action


    async def clear_by_chat(self, chat_id: int) -> None:
        async with self._lock:
            original_len = len(self._actions)
            self._actions = {k: v for k, v in self._actions.items() if v.chat_id != chat_id}
            if len(self._actions) != original_len:
                self._write_unlocked()

    # ------------------------------------------------------------------
    def _write_unlocked(self) -> None:
        payload = {"actions": [a.to_dict() for a in self._actions.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
