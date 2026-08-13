"""Middleware Telegram-слоя.

SecurityMiddleware — единственная точка, где решается «пускать или нет».
Она стоит перед всеми обработчиками, поэтому ни один хендлер не может
случайно оказаться публичным.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from ..logging_setup import get_logger
from ..security import SecurityGuard

log = get_logger("middleware")

#: Чтобы не засорять лог при спаме от постороннего.
_LOGGED_STRANGERS: set[int] = set()


class SecurityMiddleware(BaseMiddleware):
    """Пропускает дальше только CEO и ботов из реестра."""

    def __init__(self, guard: SecurityGuard) -> None:
        self.guard = guard

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user = event.from_user
        decision = self.guard.authorize(
            user_id=user.id if user else None,
            chat_id=event.chat.id,
            is_bot=bool(user and user.is_bot),
            username=user.username if user else None,
        )

        if not decision.allowed:
            user_id = user.id if user else 0
            if user_id not in _LOGGED_STRANGERS:
                _LOGGED_STRANGERS.add(user_id)
                log.warning(
                    "Отклонено: chat=%s user=%s (%s) — %s",
                    event.chat.id,
                    user_id,
                    user.username if user else "?",
                    decision.reason,
                )
            # Молча. Отвечать незнакомцу = подтверждать, что система жива.
            return None

        data["auth"] = decision
        return await handler(event, data)


class ChatLoggerMiddleware(BaseMiddleware):
    """Пишет каждую разрешённую реплику в историю чата."""

    def __init__(self, history) -> None:
        self.history = history

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            from ..models import ChatMessage

            user = event.from_user
            author = (user.full_name or user.username or str(user.id)) if user else "unknown"
            self.history.add(
                ChatMessage(
                    chat_id=event.chat.id,
                    message_id=event.message_id,
                    author=author,
                    text=event.text,
                    is_agent=bool(user and user.is_bot),
                )
            )
        return await handler(event, data)
