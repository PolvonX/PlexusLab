# cortex/brain/tools/read.py
"""Инструменты только для чтения — RiskTier.SAFE, исполняются без
подтверждения при любом уровне autonomy."""

from __future__ import annotations

from ...errors import RegistryError
from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext


class ListStaffTool(BrainTool):
    name = "list_staff"
    description = "Список всех сотрудников: тег, должность, проект, статус."
    usage = '{"tool": "list_staff", "args": {}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        employees = ctx.deps.registry.all(include_inactive=True)
        if not employees:
            return ToolResult.success("Штат пуст", "В компании пока нет ни одного сотрудника.")

        lines = [
            f"- @{e.name} ({e.role}), проект по умолчанию: {e.default_project or '—'}, "
            f"статус: {'в строю' if e.active else 'уволен'}"
            for e in employees
        ]
        return ToolResult.success(f"В штате {len(employees)} сотрудник(ов)", "\n".join(lines))


class GetEmployeeTool(BrainTool):
    name = "get_employee"
    description = "Карточка одного сотрудника по тегу."
    usage = '{"tool": "get_employee", "args": {"name": "Frontend_Dev"}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        try:
            employee = ctx.deps.registry.require(name)
        except RegistryError as err:
            return ToolResult.failure(f"Сотрудник '{name}' не найден", str(err))

        allowed = ctx.deps.tools.allowed_names(employee)
        return ToolResult.success(
            f"Карточка @{employee.name}",
            f"Должность: {employee.role}\n"
            f"Статус: {'в строю' if employee.active else 'уволен'}\n"
            f"Бот: @{employee.username or '?'}\n"
            f"Проект по умолчанию: {employee.default_project or '—'}\n"
            f"Инструменты для задач через agy: {', '.join(allowed) or 'нет'}\n"
            f"Нанят: {employee.hired_at}",
        )


class ListProjectsTool(BrainTool):
    name = "list_projects"
    description = "Список рабочих сред и их статус (своя среда/подключённая папка)."
    usage = '{"tool": "list_projects", "args": {}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        projects = ctx.deps.workspaces.list()
        if not projects:
            return ToolResult.success("Проектов нет", "Ни одной рабочей среды ещё не создано.")

        active = ctx.deps.state.active_project(ctx.chat_id)
        lines = []
        for p in projects:
            marker = " (активный в этом чате)" if p.name == active else ""
            kind = f"подключён из {p.real_path}" if p.linked else "своя среда"
            lines.append(f"- {p.name}{marker}: {kind}")
        return ToolResult.success(f"{len(projects)} проект(ов)", "\n".join(lines))


class GetStatusTool(BrainTool):
    name = "get_status"
    description = "Что сейчас выполняется, аптайм, активный драйвер сабагентов."
    usage = '{"tool": "get_status", "args": {}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        deps = ctx.deps
        active = deps.scheduler.active
        uptime = deps.uptime_seconds
        hours, remainder = divmod(int(uptime), 3600)
        minutes = remainder // 60

        try:
            driver_name = deps.config.runner_driver.name
        except Exception:
            driver_name = "agy"

        lines = [
            f"Аптайм: {hours} ч {minutes} мин",
            f"Штат: {len(deps.registry.all())} · Проектов: {len(deps.workspaces.list())}",
            f"Драйвер сабагентов: {driver_name}",
        ]
        if not active:
            lines.append("Активных задач нет.")
        else:
            lines.append(f"В работе ({len(active)}):")
            lines += [
                f"  {t.task_id} @{t.agent} -> {t.project} · {t.state} · {t.elapsed:.0f} с"
                for t in active
            ]
        return ToolResult.success("Статус Plexus Lab", "\n".join(lines))
