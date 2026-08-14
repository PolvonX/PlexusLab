# cortex/telegram/brain_router.py
"""Верхний уровень маршрутизации: @Tag идёт напрямую в agy (как раньше в
telegram/handlers.py::build_mention_router — эта логика перенесена сюда
без изменений), всё остальное свободным текстом от CEO — в мозг.

Заменяет handlers.py и hiring.py целиком: слэш-команд больше нет, найм —
разговор с мозгом (см. cortex/brain/tools/hr.py).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from ..errors import CortexError
from ..logging_setup import get_logger
from ..models import ChatMessage
from . import formatting as fmt
from .debounce import MessageDebouncer

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("brain_router")

#: Как в handlers.py — фоновые задачи держим за ссылку.
_BACKGROUND: set[asyncio.Task] = set()


def _on_background_done(task: asyncio.Task) -> None:
    """orchestrator.dispatch/brain.* уже сами ловят и репортят в чат все
    свои ошибки — но если что-то сломается ВНУТРИ их же except-блоков
    (например, сам gateway.reply не достучится до Telegram), исключение
    долетит досюда. discard() его не читает, так что раньше такой сбой
    исчезал молча (до "Task exception was never retrieved" от сборщика
    мусора) — теперь он хотя бы попадёт в лог."""
    _BACKGROUND.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("Фоновая задача %s упала", task.get_name(), exc_info=exc)


def build_brain_router(deps: "Deps") -> Router:
    router = Router(name="brain")

    async def _flush_to_brain(*, chat_id: int, text: str, message_id: int, requester_id: int) -> None:
        background = asyncio.create_task(
            deps.brain.handle_message(
                chat_id=chat_id, message_id=message_id, text=text, requester_id=requester_id,
            ),
            name="brain-message",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_on_background_done)

    debouncer = MessageDebouncer(delay=deps.config.brain_debounce_seconds, flush=_flush_to_brain)

    # ------------------------------------------------------------------
    @router.message(StateFilter(None), F.text | F.caption)
    async def on_text(message: Message) -> None:
        # message.text — для обычного текста, caption — для фото/файлов с
        # подписью (живой инцидент: CEO прислал скриншот-жалобу с подписью,
        # F.text один её не пропускал вообще, бот молчал — выглядело как
        # падение). Само фото не разбираем, только подпись.
        text = message.text or message.caption or ""
        if not text or text.startswith("/"):
            return  # слэш-команд больше нет — не отвечаем на призраков старого UX

        try:
            routed = deps.mentions.route(text, message.chat.id)
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return

        if routed is not None:
            requester = "CEO"
            if message.from_user and message.from_user.id != deps.config.secrets.ceo_id:
                requester = message.from_user.full_name or str(message.from_user.id)

            task = deps.orchestrator.new_task(
                employee=routed.employee,
                project_name=routed.project,
                instruction=routed.instruction,
                chat_id=message.chat.id,
                message_id=message.message_id,
                requester=requester,
            )

            if deps.config.ack_task_start:
                queued = deps.scheduler.is_busy(routed.project) if deps.scheduler else False
                note = " (встал в очередь — проект занят)" if queued else ""
                await message.reply(
                    f"📥 <b>{fmt.esc(routed.employee.title)}</b> взял задачу "
                    f"<code>{task.task_id}</code> в проекте "
                    f"<code>{fmt.esc(routed.project)}</code>{note}",
                    disable_notification=True,
                )

            background = asyncio.create_task(
                deps.orchestrator.dispatch(
                    task, requester_id=message.from_user.id if message.from_user else 0
                ),
                name="mention-task",
            )
            _BACKGROUND.add(background)
            background.add_done_callback(_on_background_done)
            return

        # Не адресовано конкретному сотруднику — решает мозг. Только CEO:
        # чужие сообщения (в т.ч. от других ботов компании) не запускают его.
        if not message.from_user or message.from_user.id != deps.config.secrets.ceo_id:
            return

        # Через debouncer, а не напрямую: живой инцидент — CEO переслал
        # разом несколько сообщений, каждое ушло мозгу отдельным ходом,
        # и в одном случае мозг принял свои же прошлые реплики (пришедшие
        # форвардом) за дублирующуюся доставку и зациклился на "это эхо".
        # Короткое окно тишины схлопывает всплеск в один связный ход.
        debouncer.add(
            chat_id=message.chat.id,
            text=text,
            message_id=message.message_id,
            requester_id=message.from_user.id,
        )

    # ------------------------------------------------------------------
    @router.callback_query(F.data.startswith("brain:confirm:") | F.data.startswith("brain:cancel:"))
    async def on_confirmation(callback: CallbackQuery) -> None:
        if not callback.from_user or callback.from_user.id != deps.config.secrets.ceo_id:
            await callback.answer("Только CEO может это подтвердить.", show_alert=True)
            return

        _, verdict, action_id = (callback.data or "").split(":", maxsplit=2)
        approved = verdict == "confirm"

        if callback.message is not None:
            await callback.message.edit_text(
                "✅ Подтверждено, выполняю…" if approved else "❌ Отменено."
            )
        await callback.answer()

        background = asyncio.create_task(
            deps.brain.resolve_pending(
                action_id, chat_id=callback.message.chat.id if callback.message else callback.from_user.id,
                approved=approved,
            ),
            name="brain-resolve",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_on_background_done)

    # ------------------------------------------------------------------
    @router.callback_query(F.data.startswith("brain:choice:"))
    async def on_choice(callback: CallbackQuery) -> None:
        """Кнопка квиза/теста (brain/tools/interactive.py) — в отличие от
        on_confirmation вариантов много и они не гейтят исполнение
        инструмента, поэтому выбор просто возвращается в разговор как
        обычное сообщение CEO (тем же путём, что on_text)."""
        if not callback.from_user or callback.from_user.id != deps.config.secrets.ceo_id:
            await callback.answer("Только CEO может отвечать.", show_alert=True)
            return

        _, _, choice_id, index_raw = (callback.data or "").split(":", maxsplit=3)
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id

        pending = await deps.choices.pop(choice_id)
        if pending is None:
            await callback.answer("Этот вопрос уже отвечен или устарел.", show_alert=True)
            if callback.message is not None:
                await callback.message.edit_reply_markup(reply_markup=None)
            return

        try:
            option = pending.options[int(index_raw)]
        except (ValueError, IndexError):
            await callback.answer("Не разобрал выбор.", show_alert=True)
            return

        if callback.message is not None:
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer(option[:200])

        deps.history.add(
            ChatMessage(
                chat_id=chat_id,
                message_id=pending.message_id or 0,
                author=deps.config.ceo_name,
                text=option,
                is_agent=False,
            )
        )

        background = asyncio.create_task(
            deps.brain.handle_message(
                chat_id=chat_id, message_id=pending.message_id,
                text=option, requester_id=pending.requester_id,
            ),
            name="brain-choice",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_on_background_done)

    return router
