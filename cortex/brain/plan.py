# cortex/brain/plan.py
"""Мини-PM движок для декомпозиции сложных задач: roadmap подзадач + общий
scratchpad, чтобы шаг N мог прочитать то, что нашёл шаг N-1 (например,
номер телефона или токен). Один активный план на чат — новый create_plan
заменяет старый, а не копится параллельно с ним."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import utcnow_iso

VALID_STATUSES = ("pending", "in_progress", "completed", "failed")

_STATUS_ICONS = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌"}


@dataclass(slots=True)
class Subtask:
    id: str
    description: str
    status: str = "pending"
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "description": self.description,
            "status": self.status, "result": self.result,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Subtask":
        return cls(
            id=raw["id"],
            description=raw.get("description", ""),
            status=raw.get("status", "pending"),
            result=raw.get("result", ""),
        )


@dataclass(slots=True)
class Plan:
    chat_id: int
    goal: str
    subtasks: list[Subtask]
    scratchpad: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "goal": self.goal,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "scratchpad": self.scratchpad,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Plan":
        return cls(
            chat_id=int(raw["chat_id"]),
            goal=raw.get("goal", ""),
            subtasks=[Subtask.from_dict(s) for s in raw.get("subtasks", [])],
            scratchpad=dict(raw.get("scratchpad") or {}),
            created_at=raw.get("created_at") or utcnow_iso(),
        )

    def get(self, subtask_id: str) -> Subtask | None:
        return next((s for s in self.subtasks if s.id == subtask_id), None)

    def render_plain(self) -> str:
        """Текстовый roadmap для самого агента (get_plan_status/ToolResult) —
        без HTML, это уходит обратно в промпт claude, не в Telegram."""
        lines = [f"Цель: {self.goal}", ""]
        for s in self.subtasks:
            icon = _STATUS_ICONS.get(s.status, "⬜")
            lines.append(f"{icon} [{s.id}] {s.description} — {s.status}")
            if s.result:
                lines.append(f"    результат: {s.result}")
        if self.scratchpad:
            lines += ["", "Общая память (scratchpad):"]
            for key, value in self.scratchpad.items():
                lines.append(f"  {key} = {value}")
        return "\n".join(lines)


class PlanStore:
    """Один активный план на чат — тот же паттерн tmp+os.replace, что у
    остальных brain-сторов (PendingActionStore, PendingChoiceStore,
    CustomToolStore)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._plans: dict[int, Plan] = {}
        self._lock = asyncio.Lock()
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self._path.exists():
            self._plans = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._plans = {}
            return
        if not isinstance(raw, dict):
            self._plans = {}
            return
        self._plans = {
            int(entry["chat_id"]): Plan.from_dict(entry) for entry in raw.get("plans", [])
        }

    def get(self, chat_id: int) -> Plan | None:
        return self._plans.get(chat_id)

    # ------------------------------------------------------------------
    async def set(self, plan: Plan) -> None:
        async with self._lock:
            self._plans[plan.chat_id] = plan
            self._write_unlocked()

    async def clear(self, chat_id: int) -> None:
        async with self._lock:
            self._plans.pop(chat_id, None)
            self._write_unlocked()

    def _write_unlocked(self) -> None:
        payload = {"plans": [p.to_dict() for p in self._plans.values()]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)
