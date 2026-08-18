# cortex/brain/tools/work.py
"""Делегирование инженерной работы, персональные листенеры, файлы, дайджест.

assign_task — единственная точка, где мозг передаёт эстафету agy: дальше
работает штатный Orchestrator, Claude в это уже не вовлечён.
"""

from __future__ import annotations

import asyncio

from ...errors import ToolError, WorkspaceError
from ...models import Action, ToolResult
from ...workspace import Project
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

#: Ссылки на фоновые задачи — иначе сборщик мусора может прибить корутину
#: на полпути (тот же приём, что в telegram/handlers.py).
_BACKGROUND: set[asyncio.Task] = set()


def _resolve_project(ctx: BrainToolContext, employee, given: str) -> Project:
    if given:
        return ctx.deps.workspaces.require(given)

    active = ctx.deps.state.active_project(ctx.chat_id)
    if active and ctx.deps.workspaces.get(active):
        return ctx.deps.workspaces.require(active)

    if employee.default_project and ctx.deps.workspaces.get(employee.default_project):
        return ctx.deps.workspaces.require(employee.default_project)

    projects = ctx.deps.workspaces.list()
    if len(projects) == 1:
        return projects[0]

    raise WorkspaceError(
        "Непонятно, над каким проектом работать — укажи project явно, "
        "закрепи его за чатом (set_chat_project) или задай сотруднику "
        "default_project. "
        f"Доступны: {', '.join(p.name for p in projects) or 'ни одного'}"
    )


class AssignTaskTool(BrainTool):
    name = "assign_task"
    description = "Поставить инженерную задачу сотруднику — она уйдёт на agy, не на тебя."
    usage = '{"tool": "assign_task", "args": {"employee": "Frontend_Dev", "project": "sports_api", "task": "почини хедер"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        employee_name = str(action.args.get("employee") or "").strip()
        task_text = str(action.args.get("task") or "").strip()
        if not (employee_name and task_text):
            raise ToolError("нужны employee и task (args.employee, args.task)")

        employee = ctx.deps.registry.require(employee_name)
        try:
            project = _resolve_project(ctx, employee, str(action.args.get("project") or "").strip())
        except WorkspaceError as exc:
            return ToolResult.failure(str(exc))

        deps = ctx.deps
        task = deps.orchestrator.new_task(
            employee=employee,
            project_name=project.name,
            instruction=task_text,
            chat_id=ctx.chat_id,
            message_id=0,
            requester="Cortex",
        )

        async def _background_wait():
            try:
                result_text = await deps.orchestrator.dispatch(task, requester_id=ctx.requester_id)
                if result_text:
                    notification = (
                        f"[Системное уведомление] Задача {task.task_id} "
                        f"(сотрудник @{employee.name}) завершена.\n\nРезультат:\n{result_text}"
                    )
                    await deps.brain.handle_message(
                        chat_id=ctx.chat_id,
                        message_id=0,
                        text=notification,
                        requester_id=0  # 0 означает систему
                    )
            except Exception:
                import logging
                logging.getLogger("assign_task").exception("Ошибка в фоновой задаче")

        background = asyncio.create_task(
            _background_wait(),
            name=f"brain-task:{getattr(task, 'task_id', 'mock')}",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

        return ToolResult.success(
            f"Задача передана @{employee.name}",
            f"Проект: {project.name}. Я (Cortex) получу системное уведомление, когда задача будет завершена. Можешь пока заняться другими делами или подождать.",
        )


class SetListenTool(BrainTool):
    name = "set_listen"
    description = "Включить/выключить персональный polling-листенер сотрудника."
    usage = '{"tool": "set_listen", "args": {"name": "Frontend_Dev", "on": true}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name or "on" not in action.args:
            raise ToolError("нужны name и on (args.name, args.on)")
        turn_on = bool(action.args["on"])

        employee = ctx.deps.registry.require(name)
        if ctx.deps.gateway is None:
            raise ToolError("шлюз ещё не поднят — попробуй через пару секунд")

        await ctx.deps.registry.update(employee.name, listen=turn_on)
        if turn_on:
            started = await ctx.deps.gateway.start_listener(employee)
            return ToolResult.success(
                f"@{employee.name} " + ("теперь слушает чат сам" if started else "уже слушал"),
                "Не забудь выключить ему privacy mode в BotFather.",
            )
        stopped = await ctx.deps.gateway.stop_listener(employee.name)
        return ToolResult.success(
            f"@{employee.name} " + ("больше не слушает" if stopped else "и так не слушал")
        )


class SendFileTool(BrainTool):
    name = "send_file"
    description = "Отправить файл из папки проекта в текущий чат от лица Cortex."
    usage = '{"tool": "send_file", "args": {"project": "sports_api", "path": "reports/audit.pdf"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        from aiogram.types import FSInputFile

        project_name = str(action.args.get("project") or "").strip()
        raw_path = str(action.args.get("path") or "").strip()
        if not (project_name and raw_path):
            raise ToolError("нужны project и path (args.project, args.path)")

        if ctx.deps.gateway is None:
            raise ToolError("шлюз ещё не поднят")

        project = ctx.deps.workspaces.require(project_name)
        path = ctx.deps.workspaces.resolve_path(
            project, raw_path, allow_escape=ctx.deps.config.allow_escape_workspace
        )
        if not path.exists() or path.is_dir():
            raise ToolError(f"файла '{raw_path}' нет в проекте {project.name}")

        caption = str(action.args.get("caption") or "")[:1000] or None
        try:
            await ctx.deps.gateway.gateway_bot.send_document(
                chat_id=ctx.chat_id,
                document=FSInputFile(path, filename=path.name),
                caption=caption,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Telegram не принял файл: {exc}") from exc

        return ToolResult.success(f"Файл {path.name} отправлен")


class RequestDigestTool(BrainTool):
    name = "request_digest"
    description = "Попросить Synapse собрать сводку инноваций (HackerNews) — по теме или общую."
    usage = '{"tool": "request_digest", "args": {"query": "rust wasm"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        deps = ctx.deps
        synapse = deps.registry.get(deps.config.synapse_name)
        if synapse is None or not synapse.active:
            raise ToolError(
                f"в штате нет активного {deps.config.synapse_name} — сначала найми его"
            )

        query = str(action.args.get("query") or "").strip()
        if query:
            stories = await deps.synapse.hackernews_search(query, limit=10)
            heading = f"Разведка Synapse: «{query}»"
        else:
            stories = await deps.synapse.hackernews_top()
            heading = "Сводка инноваций от Synapse"

        digest = deps.synapse.render_digest(stories, heading=heading)
        target_chat = (
            deps.config.secrets.ceo_dm_chat_id
            if deps.config.synapse.get("digest_target", "ceo_dm") == "ceo_dm"
            else ctx.chat_id
        )
        await deps.bots.say(synapse, target_chat, digest)

        return ToolResult.success(
            f"Synapse отправил сводку ({len(stories)} историй)",
            "В личку CEO" if target_chat != ctx.chat_id else "В этот чат",
        )
