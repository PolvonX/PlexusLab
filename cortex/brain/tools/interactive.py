# cortex/brain/tools/interactive.py
"""Интерактивные сообщения мозга: кнопки для квизов, тестов, голосований.

Отдельный канал от risk-подтверждений (brain/risk.py, PendingActionStore):
там кнопки жёстко ✅/❌ и гейтят исполнение инструмента, здесь кнопок
сколько угодно с произвольными подписями, и нажатие просто возвращается
в разговор как обычное сообщение CEO (см. brain/choices.py,
telegram/brain_router.py::on_choice)."""

from __future__ import annotations

import uuid

from ...errors import ToolError
from ...models import Action, ToolResult
from ..choices import PendingChoice
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

#: Больше — телеграм-клавиатура перестаёт помещаться на экран разумно.
_MAX_OPTIONS = 10


class SendButtonsTool(BrainTool):
    name = "send_buttons"
    description = (
        "Отправить сообщение с кликабельными inline-кнопками в Telegram — "
        "для квизов, тестов, голосований, выбора варианта. Нажатие кнопки "
        "возвращается тебе как обычное сообщение CEO с текстом выбранного "
        "варианта, дальше отвечай как в обычном разговоре."
    )
    usage = (
        '{"tool": "send_buttons", "args": {"text": "Вопрос 1. Какой цвет?", '
        '"buttons": ["Красный", "Синий", "Зелёный"]}}'
    )
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        text = str(action.args.get("text") or "").strip()
        if not text:
            raise ToolError("не указан текст сообщения (args.text)")

        raw_buttons = action.args.get("buttons")
        if not isinstance(raw_buttons, list) or not raw_buttons:
            raise ToolError("не указаны варианты (args.buttons — непустой список строк)")
        options = [str(b).strip() for b in raw_buttons if str(b).strip()]
        if not options:
            raise ToolError("варианты (args.buttons) пусты после очистки")
        if len(options) > _MAX_OPTIONS:
            raise ToolError(f"слишком много вариантов ({len(options)}), максимум {_MAX_OPTIONS}")

        choice_id = uuid.uuid4().hex[:10]
        await ctx.deps.choices.add(
            PendingChoice(
                id=choice_id,
                chat_id=ctx.chat_id,
                message_id=None,
                requester_id=ctx.requester_id,
                options=options,
            )
        )
        await ctx.deps.gateway.ask_choice(
            chat_id=ctx.chat_id, choice_id=choice_id, text=text, options=options,
        )
        return ToolResult.success(
            "Кнопки отправлены", f"{len(options)} вариант(ов): {', '.join(options)}"
        )
