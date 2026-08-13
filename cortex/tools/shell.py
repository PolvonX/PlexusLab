# cortex/tools/shell.py
"""Общий раннер консольных команд.

Используется employee-версией execute_command и её brain-аналогом
(cortex/brain/tools/shell_tool.py): blocklist, запуск, таймаут, обрезка
вывода — одна и та же логика, разное только то, откуда берётся cwd.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import ToolResult

log = get_logger("tools.shell")

_MAX_REPORT_CHARS = 2500


def assert_command_allowed(command: str, blocklist: list[re.Pattern[str]]) -> None:
    for pattern in blocklist:
        if pattern.search(command):
            log.warning("Заблокирована команда: %s", command)
            raise ToolError(
                "команда заблокирована политикой безопасности Plexus Lab "
                f"(правило: {pattern.pattern})"
            )


def shell_argv(command: str) -> list[str]:
    """Запускаем через системную оболочку — агенты пишут пайпы и &&."""
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", command]
    shell = os.environ.get("SHELL", "/bin/sh")
    return [shell, "-c", command]


def resolve_timeout(requested: Any, *, max_timeout: int, default: int = 180) -> int:
    try:
        timeout = int(requested)
    except (TypeError, ValueError):
        timeout = default
    return max(1, min(timeout, max_timeout))


async def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    blocklist: list[re.Pattern[str]],
    log_tag: str,
) -> ToolResult:
    """Blocklist -> запуск -> обрезка вывода -> ToolResult.

    Падение самой команды (ненулевой код, таймаут) — ожидаемый исход и
    возвращается как ToolResult.failure, не как исключение. Исключение
    (ToolError) — только если команда в blocklist или процесс не запустился.
    """
    assert_command_allowed(command, blocklist)

    argv = shell_argv(command)
    log.info("[%s] $ %s", log_tag, command)

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
            f"Команда не уложилась в {timeout} с и была снята", f"$ {command}"
        )

    duration = time.monotonic() - started
    output = output_b.decode("utf-8", errors="replace").strip()
    truncated = len(output) > _MAX_REPORT_CHARS
    if truncated:
        output = output[:_MAX_REPORT_CHARS] + "\n… (вывод обрезан)"

    detail = f"$ {command}\n\n{output or '(пустой вывод)'}"
    if process.returncode == 0:
        return ToolResult.success(f"Команда выполнена за {duration:.1f} с", detail)
    return ToolResult.failure(f"Команда завершилась с кодом {process.returncode}", detail)
