"""Кольцевой буфер истории корпоративного чата.

Держим в памяти последние N реплик на чат и дублируем на диск в JSONL —
после рестарта Cortex не должен приходить в чат с амнезией.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque

from ..logging_setup import get_logger
from ..models import ChatMessage

log = get_logger("history")


class ChatHistory:
    """История по chat_id: память + append-only лог на диске."""

    def __init__(self, data_dir: Path, limit: int = 30) -> None:
        self.dir = data_dir / "history"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.limit = limit
        self._buffers: dict[int, Deque[ChatMessage]] = defaultdict(
            lambda: deque(maxlen=self.limit)
        )
        self._loaded: set[int] = set()

    # ------------------------------------------------------------------
    def _file(self, chat_id: int) -> Path:
        return self.dir / f"chat_{chat_id}.jsonl"

    def _ensure_loaded(self, chat_id: int) -> None:
        if chat_id in self._loaded:
            return
        self._loaded.add(chat_id)

        path = self._file(chat_id)
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("Не читается история %s: %s", path, exc)
            return

        buffer = self._buffers[chat_id]
        for line in lines[-self.limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                buffer.append(ChatMessage.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                continue

    # ------------------------------------------------------------------
    def add(self, message: ChatMessage) -> None:
        self._ensure_loaded(message.chat_id)
        self._buffers[message.chat_id].append(message)
        try:
            with self._file(message.chat_id).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:  # диск переполнен — не роняем бота
            log.warning("Не пишется история чата %s: %s", message.chat_id, exc)

    def recent(self, chat_id: int, limit: int | None = None) -> list[ChatMessage]:
        self._ensure_loaded(chat_id)
        messages = list(self._buffers[chat_id])
        if limit is not None:
            messages = messages[-limit:]
        return messages

    def render(self, chat_id: int, *, limit: int | None = None, budget: int = 8000) -> str:
        """История в виде текстового блока, обрезанная по бюджету символов."""
        messages = self.recent(chat_id, limit)
        if not messages:
            return "(история чата пуста)"

        rendered: list[str] = []
        total = 0
        for message in reversed(messages):  # набираем с конца — свежее важнее
            text = (message.text or "").strip()
            if not text:
                continue
            line = f"[{message.author}]: {text}"
            if total + len(line) > budget:
                break
            rendered.append(line)
            total += len(line)

        rendered.reverse()
        return "\n".join(rendered) if rendered else "(история чата пуста)"

    def clear(self, chat_id: int) -> None:
        self._buffers.pop(chat_id, None)
        self._loaded.discard(chat_id)
        path = self._file(chat_id)
        if path.exists():
            path.unlink()
