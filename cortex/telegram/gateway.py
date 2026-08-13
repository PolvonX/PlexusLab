"""Gateway — асинхронный слушатель Telegram.

Основной режим (выбранный CEO): один бот-шлюз @Cortex с выключенным
privacy mode слушает корпоративную группу, а сотрудники отвечают своими
токенами через BotPool.

Дополнительно поддерживается «горячий» персональный листенер: если у
сотрудника в реестре стоит listen: true, Cortex поднимает для него
отдельный Dispatcher без перезапуска сервера. Каждому листенеру нужен
свой Dispatcher — один и тот же Router нельзя подключить дважды,
поэтому роутеры собираются фабриками.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..logging_setup import get_logger
from ..models import Employee
from .formatting import esc, split_message
from .handlers import build_command_router, build_mention_router
from .hiring import build_hiring_router
from .middleware import ChatLoggerMiddleware, SecurityMiddleware
from .synapse_handlers import build_synapse_router

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("gateway")


class Gateway:
    """Управляет polling-листенерами: шлюзом и персональными."""

    def __init__(self, deps: "Deps") -> None:
        self.deps = deps
        self._storage = MemoryStorage()
        self._tasks: dict[str, asyncio.Task] = {}
        self._dispatchers: dict[str, Dispatcher] = {}
        self._gateway_bot: Bot | None = None
        self._stopping = False

    # ------------------------------------------------------------------
    def _build_dispatcher(self, *, with_hiring: bool) -> Dispatcher:
        dispatcher = Dispatcher(storage=self._storage)

        dispatcher.message.middleware(SecurityMiddleware(self.deps.guard))
        dispatcher.message.middleware(ChatLoggerMiddleware(self.deps.history))

        if with_hiring:
            dispatcher.include_router(build_hiring_router(self.deps))
        dispatcher.include_router(build_command_router(self.deps))
        dispatcher.include_router(build_synapse_router(self.deps))
        dispatcher.include_router(build_mention_router(self.deps))
        return dispatcher

    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Поднять шлюз и всех сотрудников с listen: true."""
        self._gateway_bot = Bot(
            token=self.deps.config.secrets.cortex_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        me = await self._gateway_bot.get_me()
        log.info("Шлюз Cortex: @%s (id=%s)", me.username, me.id)

        await self._spawn("__gateway__", self._gateway_bot, with_hiring=True)

        for employee in self.deps.registry.all():
            if employee.listen:
                await self.start_listener(employee)

    # ------------------------------------------------------------------
    async def _spawn(self, key: str, bot: Bot, *, with_hiring: bool) -> None:
        dispatcher = self._build_dispatcher(with_hiring=with_hiring)
        self._dispatchers[key] = dispatcher

        async def run() -> None:
            try:
                await dispatcher.start_polling(
                    bot,
                    polling_timeout=self.deps.config.polling_timeout,
                    handle_signals=False,
                    allowed_updates=["message", "edited_message"],
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Листенер %s упал", key)

        self._tasks[key] = asyncio.create_task(run(), name=f"listener:{key}")
        log.info("Листенер %s запущен", key)

    # ------------------------------------------------------------------
    async def start_listener(self, employee: Employee) -> bool:
        """Поднять персональный листенер сотрудника на горячую."""
        key = employee.name.lower()
        if key in self._tasks and not self._tasks[key].done():
            log.debug("Листенер %s уже работает", employee.name)
            return False

        bot = await self.deps.bots.get(employee)
        await self._spawn(key, bot, with_hiring=False)
        return True

    async def stop_listener(self, name: str) -> bool:
        key = name.lower()
        dispatcher = self._dispatchers.pop(key, None)
        task = self._tasks.pop(key, None)
        if dispatcher is not None:
            try:
                await dispatcher.stop_polling()
            except Exception:  # noqa: BLE001
                pass
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            log.info("Листенер %s остановлен", name)
            return True
        return False

    # ------------------------------------------------------------------
    async def announce(self, text: str) -> None:
        """Служебное сообщение от Cortex в личку CEO."""
        if self._gateway_bot is None:
            return
        try:
            await self._gateway_bot.send_message(
                self.deps.config.secrets.ceo_dm_chat_id, text
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось доложить CEO: %s", exc)

    # ------------------------------------------------------------------
    @property
    def gateway_bot(self) -> Bot:
        """Собственный бот Cortex — им пользуется мозг, а не BotPool."""
        if self._gateway_bot is None:
            raise RuntimeError("Gateway.gateway_bot запрошен до Gateway.start()")
        return self._gateway_bot

    async def reply(self, chat_id: int, text: str, *, reply_to: int | None = None) -> None:
        """Ответ от лица самого Cortex (не сотрудника) — используется мозгом
        для своей реплики, а не отчёта об инструменте."""
        bot = self.gateway_bot
        chunks = split_message(text, self.deps.config.max_message_length)
        for index, chunk in enumerate(chunks):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to if index == 0 else None,
                    disable_web_page_preview=True,
                )
            except Exception as exc:  # noqa: BLE001 — битый HTML в ответе мозга
                log.warning("HTML не принят Telegram (%s), шлю без разметки", exc)
                await bot.send_message(
                    chat_id=chat_id, text=chunk, parse_mode=None, disable_web_page_preview=True
                )

    async def ask_confirmation(self, *, chat_id: int, action_id: str, summary: str, risk: str) -> None:
        """Кнопки подтверждения для рискованного действия мозга. Берёт
        только примитивы (не PendingAction) — telegram/ не должен знать про
        cortex/brain/, зависимость смотрит в обратную сторону."""
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ Выполнить", callback_data=f"brain:confirm:{action_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"brain:cancel:{action_id}"),
            ]]
        )
        await self.gateway_bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Подтверди действие ({esc(risk)}): {esc(summary)}",
            reply_markup=keyboard,
        )

    # ------------------------------------------------------------------
    async def wait(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        log.info("Останавливаю листенеры…")

        for dispatcher in self._dispatchers.values():
            try:
                await dispatcher.stop_polling()
            except Exception:  # noqa: BLE001
                pass

        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._dispatchers.clear()

        if self._gateway_bot is not None:
            try:
                await self._gateway_bot.session.close()
            except Exception:  # noqa: BLE001
                pass
        await self.deps.bots.close()
        log.info("Gateway остановлен")
