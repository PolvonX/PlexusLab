# cortex/brain/tools/shell_tool.py
"""execute_command мозга — тот же раннер и та же песочница, что у
сотрудников (cortex/tools/shell.py, cortex/workspace/manager.py), только
проект называется явно: у мозга нет своего "текущего" проекта."""

from __future__ import annotations

from ...errors import ToolError, WorkspaceError
from ...models import Action, ToolResult
from ...tools.shell import resolve_timeout, run_shell_command
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext


class ExecuteCommandBrainTool(BrainTool):
    name = "execute_command"
    description = (
        "Выполнить команду в терминале внутри папки указанного проекта. "
        "Используй только для быстрой проверки — инженерную работу делегируй "
        "через assign_task."
    )
    usage = '{"tool": "execute_command", "args": {"project": "sports_api", "command": "git status"}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        project_name = str(action.args.get("project") or "").strip()
        if not project_name:
            raise ToolError("нужен project — у мозга нет своего проекта по умолчанию")

        command = str(action.args.get("command") or "").strip()
        if not command:
            raise ToolError("пустая команда")

        try:
            project = ctx.deps.workspaces.require(project_name)
            cwd = ctx.deps.workspaces.resolve_path(
                project,
                str(action.args.get("cwd") or "."),
                allow_escape=ctx.deps.config.allow_escape_workspace,
            )
        except WorkspaceError as exc:
            raise ToolError(str(exc)) from exc

        timeout = resolve_timeout(
            action.args.get("timeout"), max_timeout=ctx.deps.config.max_command_timeout
        )

        return await run_shell_command(
            command,
            cwd=cwd,
            timeout=timeout,
            blocklist=ctx.deps.config.command_blocklist,
            log_tag=f"{project.name}/Cortex",
        )

