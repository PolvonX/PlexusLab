# cortex/tools/execute_command.py
"""execute_command — запуск консольных команд в песочнице проекта.

Самый опасный инструмент компании, поэтому три уровня защиты:
  1. blocklist регексов из config.yaml (rm -rf /, format C:, shutdown…);
  2. cwd жёстко прибит к папке проекта, выход наружу блокируется;
  3. таймаут и обрезка вывода — чтобы `npm install` не съел чат и память.

Сам запуск — в tools/shell.py, общий с brain-версией этого инструмента.
"""

from __future__ import annotations

from ..errors import ToolError
from ..models import Action, ToolResult
from .base import Tool, ToolContext
from .shell import resolve_timeout, run_shell_command


class ExecuteCommandTool(Tool):
    name = "execute_command"
    description = (
        "Выполнить команду в терминале внутри папки твоего проекта: git, npm, "
        "python, создание файлов, yt-dlp и прочее."
    )
    usage = '{"tool": "execute_command", "args": {"command": "git status", "timeout": 120}}'

    async def execute(self, action: Action, ctx: ToolContext) -> ToolResult:
        command = ctx.arg(action.args, "command", "cmd", "shell", required=True)
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        command = str(command).strip()
        if not command:
            raise ToolError("пустая команда")

        cwd = ctx.workspaces.resolve_path(
            ctx.project,
            str(ctx.arg(action.args, "cwd", "dir", "workdir", default=".")),
            allow_escape=ctx.config.allow_escape_workspace,
        )
        timeout = resolve_timeout(
            ctx.arg(action.args, "timeout", "timeout_seconds", default=180),
            max_timeout=ctx.config.max_command_timeout,
        )

        return await run_shell_command(
            command,
            cwd=cwd,
            timeout=timeout,
            blocklist=ctx.config.command_blocklist,
            log_tag=f"{ctx.project.name}/{ctx.employee.name}",
        )
