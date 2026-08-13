# cortex/brain/tools/hr.py
"""HR-инструменты мозга: найм, обучение (должностная инструкция), увольнение.

Найм остаётся диалогом по сути — Telegram не даёт ботам создавать ботов,
шаг с BotFather никуда не девается. Разница с прежним /hire в том, что
последовательность вопросов ведёт сам Claude по истории чата, а не
жёсткий FSM: он вызывает hire_employee одним действием, когда в переписке
уже есть тег, должность и токен.
"""

from __future__ import annotations

from ...errors import CortexError, ToolError
from ...hr import HireRequest
from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

_MIN_LENGTH = 80
_MAX_LENGTH = 40_000


class HireEmployeeTool(BrainTool):
    name = "hire_employee"
    description = (
        "Нанять сотрудника: проверить токен через Telegram, сгенерировать "
        "должностную инструкцию, включить его на горячую. Токен получаешь у "
        "CEO после того, как он создаст бота в @BotFather."
    )
    usage = (
        '{"tool": "hire_employee", "args": {"name": "Frontend_Dev", '
        '"role": "Senior Frontend Engineer", "token": "123:ABC"}}'
    )
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        args = action.args
        name = str(args.get("name") or "").strip()
        role = str(args.get("role") or "").strip()
        token = str(args.get("token") or "").strip()
        missing = [k for k in ("name", "role", "token") if not str(args.get(k) or "").strip()]
        if missing:
            return ToolResult.failure(
                "Не хватает данных для найма",
                f"Нужны name, role и token. Отсутствуют: {', '.join(missing)}",
            )

        try:
            employee = await ctx.deps.hr.hire(HireRequest(name=name, role=role, token=token))
        except CortexError as exc:
            return ToolResult.failure("Ошибка найма", str(exc))

        hot_note = "слушатель шлюза подхватил его сразу"
        if employee.listen and ctx.deps.gateway is not None:
            await ctx.deps.gateway.start_listener(employee)
            hot_note = "поднят персональный polling-листенер"

        return ToolResult.success(
            f"Нанят @{employee.name} ({employee.role})",
            f"Инструкция сгенерирована, {hot_note} — перезапуск сервера не нужен. "
            f"Позвать его в группе: @{employee.name} задача…\n\n"
            "Скажи CEO удалить сообщение с токеном из чата.",
        )


class WriteJobDescriptionTool(BrainTool):
    name = "write_job_description"
    description = (
        "Обучить сотрудника: переписать (replace) или дополнить (append) его "
        "должностную инструкцию."
    )
    usage = (
        '{"tool": "write_job_description", "args": {"name": "Frontend_Dev", '
        '"mode": "append", "content": "## Урок\\nПеред коммитом гоняй тесты."}}'
    )
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        args = action.args
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.failure("Не указан сотрудник", "Аргумент args.name обязателен")
        content = str(args.get("content") or "").strip()
        if not content:
            return ToolResult.failure("Пустая инструкция", "Аргумент args.content обязателен")
        mode = str(args.get("mode") or "replace").lower()
        if mode not in ("replace", "append"):
            return ToolResult.failure("Неизвестный режим", f"Режим '{mode}' недопустим, используйте replace или append")

        try:
            employee = ctx.deps.registry.require(name)
        except CortexError as exc:
            return ToolResult.failure("Сотрудник не найден", str(exc))

        if mode == "append":
            existing = ctx.deps.registry.read_prompt(employee).rstrip()
            content = f"{existing}\n\n{content}"

        if len(content) < _MIN_LENGTH:
            return ToolResult.failure(
                "Инструкция слишком короткая",
                f"Размер инструкции ({len(content)} символов) меньше минимума ({_MIN_LENGTH} символов)",
            )
        if len(content) > _MAX_LENGTH:
            return ToolResult.failure(
                "Инструкция слишком длинная",
                f"Размер инструкции ({len(content)} символов) превышает максимум ({_MAX_LENGTH} символов)",
            )

        config = getattr(ctx.deps, "config", None)
        if config is not None and getattr(config, "data_dir", None) is not None:
            backup_dir = config.data_dir / "prompt_backups"
        elif hasattr(ctx.deps.registry, "prompts_dir"):
            backup_dir = ctx.deps.registry.prompts_dir.parent / "data" / "prompt_backups"
        else:
            backup_dir = ctx.deps.registry.path.parent / "prompt_backups"

        path = ctx.deps.registry.write_prompt(employee, content, backup_dir=backup_dir)

        return ToolResult.success(
            f"Инструкция @{employee.name} обновлена ({mode})",
            f"Файл: {path.name}. Итоговый размер: {len(content)} символов. "
            "Предыдущая версия сохранена в data/prompt_backups.",
        )


class FireEmployeeTool(BrainTool):
    name = "fire_employee"
    description = (
        "Уволить сотрудника: мягко (остаётся в реестре, active=false) или "
        "жёстко (hard=true, запись и токен удаляются насовсем)."
    )
    usage = '{"tool": "fire_employee", "args": {"name": "Frontend_Dev", "hard": false}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            return ToolResult.failure("Не указан сотрудник", "Аргумент args.name обязателен")
        hard = bool(action.args.get("hard", False))

        try:
            employee = await ctx.deps.hr.fire(name, hard=hard)
        except CortexError as exc:
            return ToolResult.failure("Ошибка увольнения", str(exc))

        return ToolResult.success(
            f"@{employee.name} уволен",
            "Запись удалена из реестра." if hard else "Переведён в неактивные, запись сохранена.",
        )
