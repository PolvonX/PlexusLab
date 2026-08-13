# cortex/brain/session.py
"""Сессия claude на чат: экономия токенов через --resume.

Живой инцидент: id раньше был детерминированным (uuid5 от chat_id). Если
claude хоть раз отклонял --session-id с этим id (например, ID уже
зарегистрирован под другим cwd/project — см. живой разбор в
docs/superpowers/reviews/), ВСЕ следующие попытки для этого чата бились в
тот же самый id снова и снова: "Session ID ... is already in use" — чат
навсегда застревал, потому что "новый" id был на самом деле тем же самым.

Поэтому id теперь случайный (uuid4) и генерируется заново при каждом
--session-id (первый контакт или восстановление после reset()). Единственное,
что живёт на диске, — это ID, которым РЕАЛЬНО завершился успешный вызов
(mark_used пишет его после успеха), а не факт "уже видели этот чат".
"""

from __future__ import annotations

import uuid
from pathlib import Path


class BrainSession:
    """chat_id -> id последней успешно начатой сессии claude (или ничего)."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "brain_sessions"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file(self, chat_id: int) -> Path:
        return self._dir / f"{chat_id}.session"

    # ------------------------------------------------------------------
    def session_flag(self, chat_id: int) -> str:
        stored = self._file(chat_id)
        if stored.exists():
            return f"--resume {stored.read_text(encoding='utf-8').strip()}"
        # Новый случайный кандидат при каждом вызове без сохранённого id —
        # так повтор после reset() гарантированно не наткнётся на тот же
        # "занятый" id, что и до сброса.
        return f"--session-id {uuid.uuid4()}"

    def mark_used(self, chat_id: int, session_flag: str) -> None:
        """Вызывается ПОСЛЕ успешного runner.run() с тем же session_flag,
        что ушёл в claude, — сохраняем id, которым сессия реально
        подтверждена, а не тот, что мы лишь собирались попробовать."""
        session_id = session_flag.split(maxsplit=1)[1]
        self._file(chat_id).write_text(session_id, encoding="utf-8")

    def reset(self, chat_id: int) -> None:
        """Резюме сломалось (сессия потеряна на стороне claude) — начинаем
        с чистого листа: следующий session_flag() выдаст новый случайный id,
        а не повторит тот же самый."""
        self._file(chat_id).unlink(missing_ok=True)
