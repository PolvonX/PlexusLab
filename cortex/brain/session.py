# cortex/brain/session.py
"""Сессия claude на чат: экономия токенов через --resume.

id детерминированный (uuid5 от chat_id) — хранить нечего, кроме одного
факта: обращались ли к этому чату раньше. Он живёт в файле-метке рядом с
остальным состоянием Cortex, а не в памяти процесса — иначе рестарт сервера
заставил бы claude --resume биться о несуществующую (для процесса) сессию,
хотя на диске у claude она есть.
"""

from __future__ import annotations

import uuid
from pathlib import Path

#: Фиксированный namespace — иначе один и тот же chat_id давал бы разные
#: uuid между запусками процесса (uuid5 без namespace не детерминирован).
_NAMESPACE = uuid.UUID("6f2b6b3e-6d0a-4b1a-9f0a-2f1e8c9d7a10")


class BrainSession:
    """chat_id -> детерминированный session id claude + флаг «уже начата»."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "brain_sessions"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def session_id(self, chat_id: int) -> str:
        return str(uuid.uuid5(_NAMESPACE, str(chat_id)))

    def _marker(self, chat_id: int) -> Path:
        return self._dir / f"{chat_id}.seen"

    # ------------------------------------------------------------------
    def session_flag(self, chat_id: int) -> str:
        sid = self.session_id(chat_id)
        flag = "--resume" if self._marker(chat_id).exists() else "--session-id"
        return f"{flag} {sid}"

    def mark_used(self, chat_id: int) -> None:
        self._marker(chat_id).touch()

    def reset(self, chat_id: int) -> None:
        """Резюме сломалось (сессия потеряна на стороне claude) — начинаем
        с чистого листа и полной пересборки контекста из data/history/."""
        self._marker(chat_id).unlink(missing_ok=True)
