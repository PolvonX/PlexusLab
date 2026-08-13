"""Команды CEO и маршрутизация задач в корпоративной группе.

Роутеры собираются фабрикой, а не создаются на уровне модуля: один и тот
же Router нельзя подключить к двум Dispatcher'ам, а листенеров у нас
может быть несколько.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.types import BufferedInputFile, Message

from ..errors import CortexError
from ..logging_setup import get_logger
from . import formatting as fmt

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("handlers")

#: Фоновые задачи держим за ссылку: asyncio хранит их только слабо,
#: и без этого сборщик мусора может убить задачу на полпути.
_BACKGROUND: set[asyncio.Task] = set()


def _is_ceo(deps: "Deps", message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == deps.config.secrets.ceo_id)


def build_command_router(deps: "Deps") -> Router:
    router = Router(name="commands")

    # ------------------------------------------------------------------
    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(
            f"🧠 <b>{fmt.esc(deps.config.orchestrator_name)}</b> на связи.\n"
            f"{fmt.esc(deps.config.company_name)} — "
            f"{fmt.esc(deps.config.section('company').get('tagline', ''))}\n\n"
            f"В штате: {len(deps.registry.all())} сотрудник(ов).\n"
            f"Проектов: {len(deps.workspaces.list())}.\n\n"
            "Справка: /help"
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(
            "<b>Штат</b>\n"
            "/hire — нанять сотрудника (в личке)\n"
            "/staff — кто в штате\n"
            "/who Frontend_Dev — карточка сотрудника\n"
            "/prompt Frontend_Dev — прислать его должностную инструкцию файлом\n"
            "/fire Frontend_Dev [hard] — уволить\n"
            "/listen Frontend_Dev on|off — свой polling для бота (на горячую)\n\n"
            "<b>Проекты</b>\n"
            "/projects — список рабочих сред\n"
            "/project new sports_api Описание — создать\n"
            "/project archive sports_api — убрать в архив\n"
            "/use sports_api — закрепить проект за этим чатом\n\n"
            "<b>Работа</b>\n"
            "<code>@Frontend_Dev почини хедер</code> — поставить задачу\n"
            "<code>@Frontend_Dev #sports_api …</code> — задача в конкретном проекте\n"
            "/status — что сейчас выполняется\n"
            "/digest — сводка инноваций от Synapse\n"
            "/reload — перечитать реестр с диска\n"
            "/id — узнать ID чата и пользователя"
        )

    @router.message(Command("id"))
    async def cmd_id(message: Message) -> None:
        user = message.from_user
        await message.answer(
            "<b>Идентификаторы</b>\n"
            f"chat_id: <code>{message.chat.id}</code>\n"
            f"тип чата: <code>{message.chat.type}</code>\n"
            f"user_id: <code>{user.id if user else '?'}</code>\n\n"
            "<i>Впиши chat_id группы в CORP_GROUP_ID в .env.</i>"
        )

    # --- Штат ---------------------------------------------------------
    @router.message(Command("staff", "team"))
    async def cmd_staff(message: Message) -> None:
        employees = deps.registry.all()
        if not employees:
            await message.answer(
                "Штат пуст. Найми первого сотрудника: /hire (в личке)."
            )
            return

        lines = [f"👥 <b>Штат {fmt.esc(deps.config.company_name)}</b>", ""]
        for employee in employees:
            project = employee.default_project or "—"
            lines.append(
                f"• <b>{fmt.esc(employee.mention)}</b> — {fmt.esc(employee.role)}"
                f"\n  проект: <code>{fmt.esc(project)}</code>"
            )
        await message.answer("\n".join(lines))

    @router.message(Command("who"))
    async def cmd_who(message: Message, command: CommandObject) -> None:
        name = (command.args or "").strip()
        if not name:
            await message.reply("Кого показать? Пример: <code>/who Frontend_Dev</code>")
            return
        try:
            employee = deps.registry.require(name)
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return
        await message.answer(
            fmt.employee_card(employee, tools=deps.tools.allowed_names(employee))
        )

    @router.message(Command("prompt"))
    async def cmd_prompt(message: Message, command: CommandObject) -> None:
        if not _is_ceo(deps, message):
            return
        name = (command.args or "").strip()
        if not name:
            await message.reply("Чью инструкцию прислать? <code>/prompt Frontend_Dev</code>")
            return
        try:
            employee = deps.registry.require(name)
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return

        content = deps.registry.read_prompt(employee)
        await message.answer_document(
            BufferedInputFile(content.encode("utf-8"), filename=f"{employee.name}.md"),
            caption=f"Должностная инструкция {fmt.esc(employee.mention)} "
                    f"({len(content)} символов)",
        )

    @router.message(Command("fire"))
    async def cmd_fire(message: Message, command: CommandObject) -> None:
        if not _is_ceo(deps, message):
            return
        args = (command.args or "").split()
        if not args:
            await message.reply(
                "Кого увольняем? <code>/fire Frontend_Dev</code> — мягко "
                "(остаётся в реестре), <code>/fire Frontend_Dev hard</code> — "
                "с удалением записи."
            )
            return
        hard = len(args) > 1 and args[1].lower() in ("hard", "--hard", "совсем")
        try:
            employee = await deps.hr.fire(args[0], hard=hard)
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return
        await message.answer(
            f"👋 {fmt.esc(employee.mention)} "
            + ("удалён из реестра." if hard else "переведён в неактивные.")
        )

    @router.message(Command("listen"))
    async def cmd_listen(message: Message, command: CommandObject) -> None:
        """Горячее включение/выключение персонального листенера сотрудника."""
        if not _is_ceo(deps, message):
            return
        args = (command.args or "").split()
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            await message.reply(
                "<code>/listen Frontend_Dev on</code> — поднять для него отдельный "
                "polling (нужен, если бот должен слышать чат сам).\n"
                "<code>/listen Frontend_Dev off</code> — снять.\n\n"
                "<i>По умолчанию все слушают через шлюз Cortex.</i>"
            )
            return

        try:
            employee = deps.registry.require(args[0])
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return

        turn_on = args[1].lower() == "on"
        if deps.gateway is None:
            await message.reply("Шлюз ещё не поднят — попробуй через пару секунд.")
            return

        await deps.registry.update(employee.name, listen=turn_on)
        if turn_on:
            started = await deps.gateway.start_listener(employee)
            await message.answer(
                f"🎧 {fmt.esc(employee.mention)} "
                + ("слушает чат сам." if started else "уже слушал — ничего не изменилось.")
                + "\n<i>Не забудь выключить ему privacy mode в BotFather.</i>"
            )
        else:
            stopped = await deps.gateway.stop_listener(employee.name)
            await message.answer(
                f"🔇 {fmt.esc(employee.mention)} "
                + ("больше не слушает — работает через шлюз." if stopped else "и так не слушал.")
            )

    @router.message(Command("reload"))
    async def cmd_reload(message: Message) -> None:
        if not _is_ceo(deps, message):
            return
        try:
            await deps.registry.reload()
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return
        await message.answer(
            f"♻️ Реестр перечитан: {len(deps.registry.all())} сотрудник(ов) в строю."
        )

    # --- Проекты ------------------------------------------------------
    @router.message(Command("projects"))
    async def cmd_projects(message: Message) -> None:
        projects = deps.workspaces.list()
        if not projects:
            await message.answer(
                "Рабочих сред пока нет.\n"
                "Создать: <code>/project new sports_api API для спортивного сервиса</code>"
            )
            return

        active = deps.state.active_project(message.chat.id)
        lines = ["🗂 <b>Рабочие среды</b>", ""]
        for project in projects:
            marker = " ← активный в этом чате" if project.name == active else ""
            icon = "🔗" if project.linked else "•"
            lines.append(f"{icon} <code>{fmt.esc(project.name)}</code>{marker}")
            if project.linked:
                lines.append(f"  <code>{fmt.esc(str(project.real_path))}</code>")
            elif project.description:
                lines.append(f"  <i>{fmt.esc(project.description)}</i>")
        await message.answer("\n".join(lines))

    @router.message(Command("project"))
    async def cmd_project(message: Message, command: CommandObject) -> None:
        if not _is_ceo(deps, message):
            return
        args = (command.args or "").split(maxsplit=2)
        if not args:
            await message.reply(
                "<code>/project new &lt;имя&gt; [описание]</code> — создать с нуля\n"
                "<code>/project link &lt;имя&gt; &lt;путь&gt;</code> — подключить "
                "существующую папку\n"
                "<code>/project unlink &lt;имя&gt;</code> — отключить (файлы целы)\n"
                "<code>/project archive &lt;имя&gt;</code> — в архив\n"
                "<code>/projects</code> — список\n\n"
                "<i>Пример: /project link basehub C:\\Projects\\Basehub</i>"
            )
            return

        action = args[0].lower()
        try:
            if action in ("new", "create", "создать"):
                if len(args) < 2:
                    await message.reply("Как назовём проект? <code>/project new sports_api</code>")
                    return
                description = args[2] if len(args) > 2 else ""
                project = deps.workspaces.create(args[1], description)
                await deps.state.set_active_project(message.chat.id, project.name)
                await message.answer(
                    f"📁 Проект <code>{fmt.esc(project.name)}</code> создан и закреплён "
                    f"за этим чатом.\n<code>{fmt.esc(str(project.path))}</code>\n\n"
                    "Агенты, вызванные здесь, будут работать только внутри этой папки."
                )
            elif action in ("link", "подключить"):
                if len(args) < 3:
                    await message.reply(
                        "Нужны имя и путь:\n"
                        "<code>/project link basehub C:\\Projects\\Basehub</code>"
                    )
                    return
                project = deps.workspaces.link(args[1], args[2].strip().strip('"'))
                await deps.state.set_active_project(message.chat.id, project.name)
                await message.answer(
                    f"🔗 <code>{fmt.esc(project.name)}</code> подключён и закреплён "
                    f"за этим чатом.\n\n"
                    f"<code>{fmt.esc(str(project.path))}</code>\n"
                    f"    ↓ junction\n"
                    f"<code>{fmt.esc(str(project.real_path))}</code>\n\n"
                    "Файлы остались на месте, ничего не копировалось. Агенты видят "
                    "их как обычный проект, выход за пределы папки по-прежнему "
                    "заблокирован."
                )
            elif action in ("unlink", "отключить"):
                if len(args) < 2:
                    await message.reply("Какой проект отключить?")
                    return
                target = deps.workspaces.unlink(args[1])
                if deps.state.active_project(message.chat.id) == args[1]:
                    await deps.state.set_active_project(message.chat.id, None)
                await message.answer(
                    f"🔓 Подключение снято. Папка <code>{fmt.esc(str(target))}</code> "
                    "не тронута — удалена только ссылка."
                )
            elif action in ("archive", "архив"):
                if len(args) < 2:
                    await message.reply("Какой проект в архив?")
                    return
                target = deps.workspaces.archive(args[1], deps.config.data_dir / "archive")
                if deps.state.active_project(message.chat.id) == args[1]:
                    await deps.state.set_active_project(message.chat.id, None)
                await message.answer(f"📦 Проект убран в <code>{fmt.esc(str(target))}</code>")
            else:
                await message.reply(f"Не знаю действия '{fmt.esc(action)}'. Есть new и archive.")
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))

    @router.message(Command("use"))
    async def cmd_use(message: Message, command: CommandObject) -> None:
        if not _is_ceo(deps, message):
            return
        name = (command.args or "").strip()
        if not name:
            active = deps.state.active_project(message.chat.id) or "не задан"
            await message.reply(
                f"Активный проект чата: <code>{fmt.esc(active)}</code>\n"
                "Сменить: <code>/use sports_api</code>"
            )
            return
        try:
            project = deps.workspaces.require(name)
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return
        await deps.state.set_active_project(message.chat.id, project.name)
        await message.answer(
            f"🎯 Чат закреплён за проектом <code>{fmt.esc(project.name)}</code>. "
            "Задачи без явного #тега пойдут сюда."
        )

    # --- Состояние ----------------------------------------------------
    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        active = deps.scheduler.active
        uptime = deps.uptime_seconds
        hours, remainder = divmod(int(uptime), 3600)
        minutes = remainder // 60

        lines = [
            f"🧠 <b>{fmt.esc(deps.config.orchestrator_name)}</b>",
            f"Аптайм: {hours} ч {minutes} мин",
            f"Штат: {len(deps.registry.all())} · Проектов: {len(deps.workspaces.list())}",
            f"Драйвер сабагентов: <code>{fmt.esc(deps.config.runner_driver.name)}</code>",
            "",
        ]
        if not active:
            lines.append("Активных задач нет — все свободны.")
        else:
            lines.append(f"<b>В работе ({len(active)}):</b>")
            for task in active:
                lines.append(
                    f"• <code>{task.task_id}</code> @{fmt.esc(task.agent)} → "
                    f"<code>{fmt.esc(task.project)}</code> · {task.state} · "
                    f"{task.elapsed:.0f} с\n  <i>{fmt.esc(task.instruction[:90])}</i>"
                )
        await message.answer("\n".join(lines))

    return router


# ----------------------------------------------------------------------
def build_mention_router(deps: "Deps") -> Router:
    """Ловит теги сотрудников и запускает сабагентов."""
    router = Router(name="mentions")

    @router.message(StateFilter(None), F.text)
    async def on_text(message: Message) -> None:
        text = message.text or ""
        if text.startswith("/"):
            return

        try:
            routed = deps.mentions.route(text, message.chat.id)
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return

        if routed is None:
            return

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
            queued = deps.scheduler.is_busy(routed.project)
            note = " (встал в очередь — проект занят)" if queued else ""
            await message.reply(
                f"📥 <b>{fmt.esc(routed.employee.title)}</b> взял задачу "
                f"<code>{task.task_id}</code> в проекте "
                f"<code>{fmt.esc(routed.project)}</code>{note}",
                disable_notification=True,
            )

        log.info(
            "Задача %s: @%s → %s: %s",
            task.task_id, routed.employee.name, routed.project, routed.instruction[:120],
        )

        # Не блокируем polling: задача может идти минуты.
        background = asyncio.create_task(
            deps.orchestrator.dispatch(
                task, requester_id=message.from_user.id if message.from_user else 0
            ),
            name=f"task:{task.task_id}",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

    return router
