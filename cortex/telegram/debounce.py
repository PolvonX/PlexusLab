# cortex/telegram/debounce.py
"""Схлопывает всплеск быстро идущих сообщений одного чата в один ход
мозга — иначе десяток пересланных подряд сообщений превращается в
десяток независимых ответов Cortex вместо одного связного (живой
инцидент: пересылка нескольких сообщений подряд роняла бота в цикл
"это эхо моего сообщения" на каждое из них по отдельности)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class _Batch:
    texts: list[str] = field(default_factory=list)
    message_id: int | None = None
    requester_id: int = 0
    task: asyncio.Task | None = None


class MessageDebouncer:
    """Держит по чату короткое окно тишины после последнего сообщения,
    прежде чем отдать накопленный текст дальше одним вызовом flush().
    Каждое новое сообщение того же чата отодвигает таймер — флаш случится
    только когда поток затихнет хотя бы на delay секунд."""

    def __init__(self, *, delay: float, flush: Callable[..., Awaitable[Any]]) -> None:
        self._delay = delay
        self._flush = flush
        self._batches: dict[int, _Batch] = {}

    def add(self, *, chat_id: int, text: str, message_id: int, requester_id: int) -> None:
        batch = self._batches.setdefault(chat_id, _Batch())
        batch.texts.append(text)
        batch.message_id = message_id
        batch.requester_id = requester_id

        if batch.task is not None:
            batch.task.cancel()
        batch.task = asyncio.create_task(self._wait_and_flush(chat_id))

    # ------------------------------------------------------------------
    async def _wait_and_flush(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return  # прилетело новое сообщение раньше — новый таймер уже взял пачку на себя

        batch = self._batches.pop(chat_id, None)
        if batch is None:
            return
        await self._flush(
            chat_id=chat_id,
            text="\n\n".join(batch.texts),
            message_id=batch.message_id,
            requester_id=batch.requester_id,
        )
