"""send_file — отправка файла из рабочей среды в Telegram-чат.

Файл уходит от лица самого сотрудника (его токеном), а не от Cortex:
в чате должно быть видно, кто именно принёс результат.
"""

from __future__ import annotations

from aiogram.types import FSInputFile

from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import Action, ToolResult
from .base import Tool, ToolContext

log = get_logger("tools.file")

#: Telegram отказывает ботам на файлах больше 50 МБ.
_MAX_BYTES = 50 * 1024 * 1024


class SendFileTool(Tool):
    name = "send_file"
    description = "Отправить файл из папки проекта в текущий чат Telegram."
    usage = '{"tool": "send_file", "args": {"path": "reports/audit.pdf", "caption": "Отчёт готов"}}'

    async def execute(self, action: Action, ctx: ToolContext) -> ToolResult:
        raw_path = ctx.arg(action.args, "path", "file", "filename", required=True)
        caption = ctx.arg(action.args, "caption", "text", default="")

        path = ctx.workspaces.resolve_path(
            ctx.project,
            str(raw_path),
            allow_escape=ctx.config.allow_escape_workspace,
        )

        if not path.exists():
            raise ToolError(f"файла '{raw_path}' нет в проекте {ctx.project.name}")
        if path.is_dir():
            raise ToolError(
                f"'{raw_path}' — это папка. Заархивируй её через execute_command "
                "и пришли архив."
            )

        size = path.stat().st_size
        if size == 0:
            raise ToolError(f"файл '{raw_path}' пустой — Telegram такие не принимает")
        if size > _MAX_BYTES:
            raise ToolError(
                f"файл весит {size / 1024 / 1024:.1f} МБ, лимит Telegram для ботов — 50 МБ"
            )

        caption_text = str(caption)[:1000] if caption else None
        try:
            await ctx.bot.send_document(
                chat_id=ctx.chat_id,
                document=FSInputFile(path, filename=path.name),
                caption=caption_text,
                reply_to_message_id=ctx.message_id,
            )
        except Exception as exc:  # noqa: BLE001 — Telegram отвечает десятком разных ошибок
            log.warning("send_document упал: %s", exc)
            raise ToolError(f"Telegram не принял файл: {exc}") from exc

        log.info("[%s] отправлен файл %s (%d Б)", ctx.employee.name, path.name, size)
        return ToolResult.success(
            f"Файл {path.name} отправлен ({size / 1024:.1f} КБ)"
        )
