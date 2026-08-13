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
from . import formatting as fmt

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("brain_router")

#: Как в handlers.py — фоновые задачи держим за ссылку.
_BACKGROUND: set[asyncio.Task] = set()


def build_brain_router(deps: "Deps") -> Router:
    router = Router(name="brain")

    # ------------------------------------------------------------------
    @router.message(StateFilter(None), F.text)
    async def on_text(message: Message) -> None:
        text = message.text or ""
        if text.startswith("/"):
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
            background.add_done_callback(_BACKGROUND.discard)
            return

        # Не адресовано конкретному сотруднику — решает мозг. Только CEO:
        # чужие сообщения (в т.ч. от других ботов компании) не запускают его.
        if not message.from_user or message.from_user.id != deps.config.secrets.ceo_id:
            return

        background = asyncio.create_task(
            deps.brain.handle_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=text,
                requester_id=message.from_user.id,
            ),
            name="brain-message",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

    # ------------------------------------------------------------------
    @router.callback_query(F.data.startswith("brain:"))
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
            deps.brain.resolve_pending(action_id, approved=approved), name="brain-resolve"
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

    return router
