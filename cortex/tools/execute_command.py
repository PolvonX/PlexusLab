"""execute_command — запуск консольных команд в песочнице проекта.

Самый опасный инструмент компании, поэтому три уровня защиты:
  1. blocklist регексов из config.yaml (rm -rf /, format C:, shutdown…);
  2. cwd жёстко прибит к папке проекта, выход наружу блокируется;
  3. таймаут и обрезка вывода — чтобы `npm install` не съел чат и память.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import Action, ToolResult
from .base import Tool, ToolContext

log = get_logger("tools.exec")

_MAX_REPORT_CHARS = 2500


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

        self._assert_allowed(command, ctx)

        cwd = self._resolve_cwd(action, ctx)
        timeout = self._resolve_timeout(action, ctx)

        argv = self._shell_argv(command)
        log.info("[%s/%s] $ %s", ctx.project.name, ctx.employee.name, command)

        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except OSError as exc:
            raise ToolError(f"не удалось запустить команду: {exc}") from exc

        try:
            output_b, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult.failure(
                f"Команда не уложилась в {timeout} с и была снята",
                f"$ {command}",
            )

        duration = time.monotonic() - started
        output = output_b.decode("utf-8", errors="replace").strip()
        truncated = len(output) > _MAX_REPORT_CHARS
        if truncated:
            output = output[:_MAX_REPORT_CHARS] + "\n… (вывод обрезан)"

        detail = f"$ {command}\n\n{output or '(пустой вывод)'}"
        if process.returncode == 0:
            return ToolResult.success(
                f"Команда выполнена за {duration:.1f} с", detail
            )
        return ToolResult.failure(
            f"Команда завершилась с кодом {process.returncode}", detail
        )

    # ------------------------------------------------------------------
    def _assert_allowed(self, command: str, ctx: ToolContext) -> None:
        for pattern in ctx.config.command_blocklist:
            if pattern.search(command):
                log.warning(
                    "Заблокирована команда от %s: %s", ctx.employee.name, command
                )
                raise ToolError(
                    "команда заблокирована политикой безопасности Plexus Lab "
                    f"(правило: {pattern.pattern})"
                )

    def _resolve_cwd(self, action: Action, ctx: ToolContext):
        relative = ctx.arg(action.args, "cwd", "dir", "workdir", default=".")
        return ctx.workspaces.resolve_path(
            ctx.project,
            str(relative),
            allow_escape=ctx.config.allow_escape_workspace,
        )

    def _resolve_timeout(self, action: Action, ctx: ToolContext) -> int:
        requested = ctx.arg(action.args, "timeout", "timeout_seconds", default=180)
        try:
            timeout = int(requested)
        except (TypeError, ValueError):
            timeout = 180
        return max(1, min(timeout, ctx.config.max_command_timeout))

    @staticmethod
    def _shell_argv(command: str) -> list[str]:
        """Запускаем через системную оболочку — агенты пишут пайпы и &&."""
        if sys.platform == "win32":
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            return [comspec, "/d", "/s", "/c", command]
        shell = os.environ.get("SHELL", "/bin/sh")
        return [shell, "-c", command]
