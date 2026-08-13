# cortex/brain/tools/projects.py
"""Управление рабочими средами проектов из мозга."""

from __future__ import annotations

from ...errors import ToolError
from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext


class CreateProjectTool(BrainTool):
    name = "create_project"
    description = "Создать новую изолированную рабочую среду с нуля и закрепить её за этим чатом."
    usage = '{"tool": "create_project", "args": {"name": "sports_api", "description": "API спортивного сервиса"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            raise ToolError("не указано имя проекта (args.name)")
        description = str(action.args.get("description") or "")

        project = ctx.deps.workspaces.create(name, description)
        await ctx.deps.state.set_active_project(ctx.chat_id, project.name)
        return ToolResult.success(
            f"Проект {project.name} создан", f"Путь: {project.path}. Закреплён за этим чатом."
        )


class LinkProjectTool(BrainTool):
    name = "link_project"
    description = "Подключить существующую папку как проект (без копирования файлов)."
    usage = '{"tool": "link_project", "args": {"name": "basehub", "path": "C:\\\\Projects\\\\Basehub"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        path = str(action.args.get("path") or "").strip().strip('"')
        if not (name and path):
            raise ToolError("нужны name и path (args.name, args.path)")

        project = ctx.deps.workspaces.link(name, path, str(action.args.get("description") or ""))
        await ctx.deps.state.set_active_project(ctx.chat_id, project.name)
        return ToolResult.success(
            f"Проект {project.name} подключён", f"{project.path} -> junction -> {project.real_path}"
        )


class SetChatProjectTool(BrainTool):
    name = "set_chat_project"
    description = (
        "Закрепить проект за этим чатом (или снять закрепление пустым project) — "
        "задачи без явного #тега пойдут сюда."
    )
    usage = '{"tool": "set_chat_project", "args": {"project": "sports_api"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        raw = str(action.args.get("project") or "").strip()
        if not raw:
            await ctx.deps.state.set_active_project(ctx.chat_id, None)
            return ToolResult.success("Активный проект чата снят")

        project = ctx.deps.workspaces.require(raw)
        await ctx.deps.state.set_active_project(ctx.chat_id, project.name)
        return ToolResult.success(f"Чат закреплён за проектом {project.name}")


class UnlinkProjectTool(BrainTool):
    name = "unlink_project"
    description = "Отключить подключённую папку (junction). Файлы остаются на месте."
    usage = '{"tool": "unlink_project", "args": {"name": "basehub"}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            raise ToolError("не указано имя проекта (args.name)")

        target = ctx.deps.workspaces.unlink(name)
        if ctx.deps.state.active_project(ctx.chat_id) == name:
            await ctx.deps.state.set_active_project(ctx.chat_id, None)
        return ToolResult.success(
            f"Проект {name} отключён", f"Папка {target} не тронута — удалена только ссылка."
        )


class ArchiveProjectTool(BrainTool):
    name = "archive_project"
    description = "Убрать СВОЙ проект (не подключённую папку) в архив. Данные не удаляются."
    usage = '{"tool": "archive_project", "args": {"name": "sports_api"}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            raise ToolError("не указано имя проекта (args.name)")

        target = ctx.deps.workspaces.archive(name, ctx.deps.config.data_dir / "archive")
        if ctx.deps.state.active_project(ctx.chat_id) == name:
            await ctx.deps.state.set_active_project(ctx.chat_id, None)
        return ToolResult.success(f"Проект {name} в архиве", f"Путь: {target}")
