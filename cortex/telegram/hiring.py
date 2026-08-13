"""Диалог найма: /hire.

Cortex не умеет создавать ботов — Telegram такого API не даёт. Поэтому
процесс диалоговый: Cortex ведёт CEO через BotFather, принимает токен,
проверяет его, пишет должностную инструкцию и включает сотрудника
без перезапуска сервера.

Токен приходит в чат открытым текстом, поэтому сразу после успешного
найма Cortex просит CEO удалить это сообщение.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from ..errors import CortexError
from ..hr import HireRequest
from ..logging_setup import get_logger
from ..models import NAME_RE
from . import formatting as fmt

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("hiring")

_TOKEN_HINT = "1234567890:AAH..."


class Hiring(StatesGroup):
    name = State()
    role = State()
    token = State()


def build_hiring_router(deps: "Deps") -> Router:
    """Роутер найма. Создаётся заново на каждый Dispatcher."""
    router = Router(name="hiring")

    # Найм — операция уровня CEO и только в личке: в токен не должен
    # заглядывать никто, включая коллег по группе.
    @router.message(Command("hire"))
    async def start_hire(message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.from_user.id != deps.config.secrets.ceo_id:
            return
        if message.chat.type != "private":
            await message.reply(
                "🔒 Найм ведём в личке — в токен бота не должен смотреть никто. "
                "Напиши мне /hire в приватном чате."
            )
            return

        await state.set_state(Hiring.name)
        await message.answer(
            "🧬 <b>Найм нового сотрудника в Plexus Lab</b>\n\n"
            "Шаг 1 из 3. Как его будут звать в чате?\n"
            "Формат тега: латиница, цифры и <code>_</code>, например "
            "<code>Frontend_Dev</code> или <code>Data_Analyst</code>.\n\n"
            "Отмена — /cancel"
        )

    # Регистрируется раньше текстовых шагов: иначе /cancel будет съеден
    # обработчиком текущего шага как обычный текст.
    @router.message(Command("cancel"), StateFilter(Hiring))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Найм отменён.")

    # ------------------------------------------------------------------
    @router.message(StateFilter(Hiring.name), F.text)
    async def take_name(message: Message, state: FSMContext) -> None:
        name = (message.text or "").strip().lstrip("@")

        if not NAME_RE.match(name):
            await message.reply(
                "Тег не годится. Нужны латинские буквы, цифры и <code>_</code>, "
                "длина 3–32, первый символ — буква. Попробуй ещё раз."
            )
            return
        if deps.registry.exists(name):
            await message.reply(f"@{fmt.esc(name)} уже в штате. Придумай другой тег.")
            return

        await state.update_data(name=name)
        await state.set_state(Hiring.role)
        await message.answer(
            f"Принято: <b>@{fmt.esc(name)}</b>\n\n"
            "Шаг 2 из 3. Какая у него должность? Одной строкой — она ляжет в "
            "должностную инструкцию.\n"
            "Например: <i>Senior Frontend Engineer</i> или <i>DevOps Engineer</i>."
        )

    # ------------------------------------------------------------------
    @router.message(StateFilter(Hiring.role), F.text)
    async def take_role(message: Message, state: FSMContext) -> None:
        role = (message.text or "").strip()
        if len(role) < 3:
            await message.reply("Слишком коротко. Опиши должность внятно.")
            return
        if len(role) > 200:
            await message.reply("Слишком длинно — уложись в 200 символов.")
            return

        data = await state.update_data(role=role)
        await state.set_state(Hiring.token)
        await message.answer(
            f"Должность: <b>{fmt.esc(role)}</b>\n\n"
            "Шаг 3 из 3. Теперь создай для него бота — я этого сделать не могу, "
            "Telegram не даёт ботам создавать ботов.\n\n"
            "1. Открой @BotFather → <code>/newbot</code>\n"
            f"2. Имя: <b>{fmt.esc(data['name'].replace('_', ' '))}</b>, "
            "юзернейм — любой свободный\n"
            "3. Выключи privacy mode: <code>/mybots</code> → бот → "
            "<i>Bot Settings</i> → <i>Group Privacy</i> → <i>Turn off</i>\n"
            "4. Добавь бота в рабочую группу\n\n"
            f"Пришли мне сюда его токен (<code>{_TOKEN_HINT}</code>)."
        )

    # ------------------------------------------------------------------
    @router.message(StateFilter(Hiring.token), F.text)
    async def take_token(message: Message, state: FSMContext) -> None:
        token = (message.text or "").strip()
        if ":" not in token or len(token) < 30:
            await message.reply(
                "Это не похоже на токен. Он выглядит так: "
                f"<code>{_TOKEN_HINT}</code>. Пришли ещё раз или /cancel."
            )
            return

        data = await state.get_data()
        await state.clear()

        status = await message.answer("⏳ Проверяю токен и оформляю сотрудника…")

        try:
            employee = await deps.hr.hire(
                HireRequest(name=data["name"], role=data["role"], token=token)
            )
        except CortexError as exc:
            await status.edit_text(fmt.error_report(exc, context="найм отменён"))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Найм %s сорвался", data.get("name"))
            await status.edit_text(fmt.error_report(exc, context="найм отменён"))
            return

        # Горячее включение: сотрудник начинает работать сразу, без
        # перезапуска сервера Plexus Lab.
        hot_note = "слушатель шлюза подхватил его сразу"
        if employee.listen and deps.gateway is not None:
            await deps.gateway.start_listener(employee)
            hot_note = "поднят персональный polling-листенер"

        tools = deps.tools.allowed_names(employee)
        await status.edit_text(
            "✅ <b>Сотрудник принят в Plexus Lab</b>\n\n"
            + fmt.employee_card(employee, tools=tools)
            + "\n\n"
            f"Инструкция сгенерирована, {hot_note} — перезапуск не нужен.\n"
            f"Зови его в группе: <code>@{fmt.esc(employee.name)} задача…</code>\n\n"
            "🔐 <b>Удали сообщение с токеном из этого чата.</b>"
        )
        log.info("CEO нанял %s", employee.name)

    return router
