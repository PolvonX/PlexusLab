"""self_execute_task — Cortex делает инженерную работу сам, когда нанимать
некого или CEO прямо попросил не нанимать, а сделать самому.

Синтетический, незарегистрированный Employee("Cortex", token=<токен
шлюза>, prompt_path="prompts/cortex.md") прогоняется через штатный
Orchestrator.dispatch() — тот же путь, что и у обычных сотрудников
(agy, PromptBuilder, employee-side ToolRegistry, BotPool). Реестр не
трогается: никто никого не "нанимает" по-настоящему.
"""

from __future__ import annotations

import asyncio

from ...errors import ToolError
from ...models import Action, Employee, ToolResult
from ...workspace import Project
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

#: Ссылки на фоновые задачи — иначе сборщик мусора может прибить корутину
#: на полпути (тот же приём, что в brain/tools/work.py::AssignTaskTool).
_BACKGROUND: set[asyncio.Task] = set()


def _resolve_project(ctx: BrainToolContext, given: str) -> Project:
    if given:
        return ctx.deps.workspaces.require(given)

    active = ctx.deps.state.active_project(ctx.chat_id)
    if active and ctx.deps.workspaces.get(active):
        return ctx.deps.workspaces.require(active)

    projects = ctx.deps.workspaces.list()
    if len(projects) == 1:
        return projects[0]

    from ...errors import WorkspaceError

    raise WorkspaceError(
        "Непонятно, над каким проектом работать — укажи project явно. "
        f"Доступны: {', '.join(p.name for p in projects) or 'ни одного — сперва создай проект'}"
    )


class SelfExecuteTaskTool(BrainTool):
    name = "self_execute_task"
    description = (
        "Сделать инженерную работу самому через agy, когда нанимать некого "
        "или CEO прямо попросил не нанимать. По умолчанию — для мелких "
        "разовых задач; для крупных сперва спроси CEO, нанять кого-то или "
        "сделать самому."
    )
    usage = (
        '{"tool": "self_execute_task", "args": {"project": "sports_api", '
        '"task": "скачай видео с ... в downloads/"}}'
    )
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        task_text = str(action.args.get("task") or "").strip()
        if not task_text:
            raise ToolError("не указана задача (args.task)")

        project = _resolve_project(ctx, str(action.args.get("project") or "").strip())

        deps = ctx.deps
        worker = Employee(
            name="Cortex",
            role=f"{deps.config.orchestrator_name} — Digital Director",
            token=deps.config.secrets.cortex_token,
            prompt_path="prompts/cortex.md",
        )

        task = deps.orchestrator.new_task(
            employee=worker,
            project_name=project.name,
            instruction=task_text,
            chat_id=ctx.chat_id,
            message_id=0,
            requester="Cortex (сам)",
        )

        async def _background_wait():
            try:
                result_text = await deps.orchestrator.dispatch(task, requester_id=ctx.requester_id)
                if result_text:
                    notification = (
                        f"[Системное уведомление] Моя задача (self_execute_task) завершена.\n\n"
                        f"Результат:\n{result_text}"
                    )
                    await deps.brain.handle_message(
                        chat_id=ctx.chat_id,
                        message_id=0,
                        text=notification,
                        requester_id=0
                    )
            except Exception:
                import logging
                logging.getLogger("self_work").exception("Ошибка в фоновой задаче")

        background = asyncio.create_task(
            _background_wait(),
            name=f"self-task:{getattr(task, 'task_id', 'mock')}",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

        return ToolResult.success(
            "Берусь сам", f"Проект: {project.name}. Я (Cortex) получу системное уведомление, когда задача будет завершена."
        )
