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

Живой инцидент #2: сессия без границ по времени/числу ходов деградирует —
многочасовой непрерывный --resume довёл мозг до того, что он начал путать
свой контрактный формат <action> с нативным tool-call синтаксисом и
галлюцинировать в ответах. Поэтому сессия теперь протухает сама: старше
_MAX_AGE_SECONDS или больше _MAX_TURNS ходов — session_flag() ведёт себя
так, будто сессии не было вообще (свежий --session-id, не --resume)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

#: Живой инцидент: многочасовая --resume сессия деградировала (см. докстринг
#: модуля) — эти два порога подобраны так, чтобы сработать раньше, чем
#: деградация успеет накопиться, но не мешать нормальной дневной работе.
_MAX_AGE_SECONDS = 6 * 3600
_MAX_TURNS = 50


class BrainSession:
    """chat_id -> {session_id, created_at, turn_count} последней успешно
    начатой сессии claude (или ничего)."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "brain_sessions"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file(self, chat_id: int) -> Path:
        return self._dir / f"{chat_id}.session"

    def _read(self, chat_id: int) -> dict | None:
        path = self._file(chat_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------
    def session_flag(self, chat_id: int) -> str:
        stored = self._read(chat_id)
        if stored is None:
            return f"--session-id {uuid.uuid4()}"

        age = time.time() - stored.get("created_at", 0)
        turns = stored.get("turn_count", 0)
        if age > _MAX_AGE_SECONDS or turns > _MAX_TURNS:
            # Протухла — ведём себя так, будто сессии не было. Файл не
            # трогаем здесь (session_flag() только читает) — перезапишет
            # его следующий mark_used() со свежим --session-id.
            return f"--session-id {uuid.uuid4()}"

        return f"--resume {stored['session_id']}"

    def mark_used(self, chat_id: int, session_flag: str) -> None:
        """Вызывается ПОСЛЕ успешного runner.run() с тем же session_flag,
        что ушёл в claude, — сохраняем id, которым сессия реально
        подтверждена, а не тот, что мы лишь собирались попробовать."""
        session_id = session_flag.split(maxsplit=1)[1]
        if session_flag.startswith("--session-id"):
            payload = {"session_id": session_id, "created_at": time.time(), "turn_count": 1}
        else:
            stored = self._read(chat_id) or {"created_at": time.time(), "turn_count": 0}
            payload = {
                "session_id": session_id,
                "created_at": stored.get("created_at", time.time()),
                "turn_count": stored.get("turn_count", 0) + 1,
            }
        self._file(chat_id).write_text(json.dumps(payload), encoding="utf-8")

    def reset(self, chat_id: int) -> None:
        """Резюме сломалось (сессия потеряна на стороне claude) — начинаем
        с чистого листа: следующий session_flag() выдаст новый случайный id,
        а не повторит тот же самый."""
        self._file(chat_id).unlink(missing_ok=True)
