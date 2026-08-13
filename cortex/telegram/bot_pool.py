"""Пул Telegram-ботов Plexus Lab.

Один Bot-инстанс на сотрудника, создаётся лениво и живёт до остановки
сервера — переоткрывать HTTP-сессию на каждое сообщение дорого. Именно
через пул реализуется «ответ от лица тегнутого бота».
"""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from ..errors import CortexError
from ..logging_setup import get_logger
from ..models import Employee
from ..registry import EmployeeRegistry
from .formatting import split_message

log = get_logger("bot_pool")


class BotPool:
    """Кэш Bot-инстансов по токену + отправка от лица сотрудника."""

    def __init__(self, registry: EmployeeRegistry, max_message_length: int = 3800) -> None:
        self.registry = registry
        self.max_message_length = max_message_length
        self._bots: dict[str, Bot] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def _make_bot(token: str) -> Bot:
        return Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    async def get(self, employee: Employee) -> Bot:
        async with self._lock:
            bot = self._bots.get(employee.token)
            if bot is None:
                bot = self._make_bot(employee.token)
                self._bots[employee.token] = bot
                log.debug("Поднят Bot-инстанс для %s", employee.name)
            return bot

    # ------------------------------------------------------------------
    async def verify(self, employee: Employee) -> Employee:
        """Проверить токен через getMe и записать username/bot_id в реестр."""
        bot = await self.get(employee)
        try:
            me = await bot.get_me()
        except Exception as exc:  # noqa: BLE001 — TelegramUnauthorizedError и родня
            raise CortexError(
                f"Токен для {employee.mention} не принят Telegram: {exc}"
            ) from exc

        await self.registry.update(employee.name, username=me.username, bot_id=me.id)
        log.info("Токен %s подтверждён: @%s (id=%s)", employee.name, me.username, me.id)
        return employee

    # ------------------------------------------------------------------
    async def say(
        self,
        employee: Employee,
        chat_id: int,
        text: str,
        *,
        reply_to: int | None = None,
        silent: bool = False,
    ) -> None:
        """Отправить сообщение от лица сотрудника, разрезав по лимиту."""
        bot = await self.get(employee)
        chunks = split_message(text, self.max_message_length)
        for index, chunk in enumerate(chunks):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to if index == 0 else None,
                    disable_notification=silent,
                    disable_web_page_preview=True,
                )
            except Exception as exc:  # noqa: BLE001
                # Чаще всего — битый HTML в ответе агента. Отправляем как есть.
                log.warning("HTML не принят Telegram (%s), шлю без разметки", exc)
                await bot.send_message(
                    chat_id=chat_id,
                    text=_strip_tags(chunk),
                    parse_mode=None,
                    disable_notification=silent,
                    disable_web_page_preview=True,
                )

    async def typing(self, employee: Employee, chat_id: int) -> None:
        bot = await self.get(employee)
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:  # noqa: BLE001 — индикатор набора не критичен
            pass

    # ------------------------------------------------------------------
    async def close(self) -> None:
        async with self._lock:
            for token, bot in list(self._bots.items()):
                try:
                    await bot.session.close()
                except Exception:  # noqa: BLE001
                    pass
                self._bots.pop(token, None)
        log.info("Пул ботов закрыт")

    async def drop(self, token: str) -> None:
        """Выкинуть бота из пула (увольнение, смена токена)."""
        async with self._lock:
            bot = self._bots.pop(token, None)
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:  # noqa: BLE001
                pass


def _strip_tags(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text)
