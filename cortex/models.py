"""Доменные модели Plexus Lab.

Намеренно на dataclass'ах, без ORM: реестр — это JSON-файл, который CEO
должен иметь возможность открыть и починить руками.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: Тег сотрудника в чате: @Frontend_Dev
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Employee:
    """Сотрудник Plexus Lab — Telegram-бот с должностной инструкцией."""

    name: str                       # тег в чате, уникальный: Frontend_Dev
    role: str                       # должность: Senior Frontend Engineer
    token: str                      # Telegram Bot API token
    prompt_path: str                # путь к .md с системным промптом
    display_name: str | None = None
    username: str | None = None     # @frontend_dev_bot, заполняется автоматом
    bot_id: int | None = None       # numeric id бота, для валидации отправителя
    default_project: str | None = None
    tools: list[str] = field(default_factory=list)  # пусто = политика из config
    listen: bool = False            # поднимать ли отдельный polling-листенер
    active: bool = True
    hired_at: str = field(default_factory=utcnow_iso)
    notes: str = ""

    @property
    def title(self) -> str:
        return self.display_name or self.name.replace("_", " ")

    @property
    def mention(self) -> str:
        return f"@{self.name}"

    @property
    def token_hint(self) -> str:
        """Безопасный для логов огрызок токена."""
        if not self.token or ":" not in self.token:
            return "<invalid>"
        head, _, tail = self.token.partition(":")
        return f"{head}:{tail[:4]}…{tail[-2:]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "role": self.role,
            "token": self.token,
            "prompt_path": self.prompt_path,
            "username": self.username,
            "bot_id": self.bot_id,
            "default_project": self.default_project,
            "tools": list(self.tools),
            "listen": self.listen,
            "active": self.active,
            "hired_at": self.hired_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Employee":
        return cls(
            name=raw["name"],
            role=raw.get("role", ""),
            token=raw.get("token", ""),
            prompt_path=raw.get("prompt_path", ""),
            display_name=raw.get("display_name"),
            username=raw.get("username"),
            bot_id=raw.get("bot_id"),
            default_project=raw.get("default_project"),
            tools=list(raw.get("tools") or []),
            listen=bool(raw.get("listen", False)),
            active=bool(raw.get("active", True)),
            hired_at=raw.get("hired_at") or utcnow_iso(),
            notes=raw.get("notes", ""),
        )


@dataclass(slots=True)
class ChatMessage:
    """Одна реплика корпоративного чата в истории."""

    chat_id: int
    message_id: int
    author: str
    text: str
    ts: str = field(default_factory=utcnow_iso)
    is_agent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "author": self.author,
            "text": self.text,
            "ts": self.ts,
            "is_agent": self.is_agent,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ChatMessage":
        return cls(
            chat_id=raw["chat_id"],
            message_id=raw.get("message_id", 0),
            author=raw.get("author", "unknown"),
            text=raw.get("text", ""),
            ts=raw.get("ts") or utcnow_iso(),
            is_agent=bool(raw.get("is_agent", False)),
        )


@dataclass(slots=True)
class AgentTask:
    """Единица работы: «кому», «что», «в каком проекте»."""

    employee: Employee
    project: str
    instruction: str
    chat_id: int
    message_id: int
    requester: str
    task_id: str


@dataclass(slots=True)
class AgentResult:
    """Сырой результат процесса `agy`."""

    stdout: str
    stderr: str
    returncode: int
    duration: float
    command: str
    truncated: bool = False


@dataclass(slots=True)
class Action:
    """Разобранный блок <action>{...}</action> из ответа агента."""

    tool: str
    args: dict[str, Any]
    raw: str = ""


@dataclass(slots=True)
class ToolResult:
    """Итог исполнения инструмента — уходит в чат как отчёт."""

    ok: bool
    summary: str
    detail: str = ""

    @classmethod
    def success(cls, summary: str, detail: str = "") -> "ToolResult":
        return cls(True, summary, detail)

    @classmethod
    def failure(cls, summary: str, detail: str = "") -> "ToolResult":
        return cls(False, summary, detail)
