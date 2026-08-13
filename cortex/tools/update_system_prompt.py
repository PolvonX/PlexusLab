"""update_system_prompt — рекурсивное самоулучшение.

Агент (или Cortex) переписывает должностную инструкцию — свою или чужую.
Правка чужого промпта разрешена только тем, у кого в реестре стоит роль
руководителя; предыдущая версия всегда уезжает в data/prompt_backups,
чтобы неудачный «апгрейд интеллекта» можно было откатить.
"""

from __future__ import annotations

from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import Action, ToolResult
from .base import Tool, ToolContext

log = get_logger("tools.prompt")

_MIN_LENGTH = 80
_MAX_LENGTH = 40_000

#: Кто имеет право править чужие инструкции.
_SUPERVISOR_NAMES = {"cortex", "synapse"}


class UpdateSystemPromptTool(Tool):
    name = "update_system_prompt"
    description = (
        "Переписать свою должностную инструкцию (системный промпт), чтобы в "
        "следующий раз работать умнее. Режимы: replace (по умолчанию) и append."
    )
    usage = (
        '{"tool": "update_system_prompt", "args": {"mode": "append", '
        '"content": "## Урок\\nПеред коммитом всегда запускаю тесты."}}'
    )

    async def execute(self, action: Action, ctx: ToolContext) -> ToolResult:
        args = action.args
        content = ctx.arg(args, "content", "prompt", "text", "body", required=True)
        mode = str(ctx.arg(args, "mode", "op", default="replace")).lower()
        target_name = ctx.arg(args, "target", "employee", "who")

        target = self._resolve_target(ctx, target_name)
        content = str(content).strip()

        if mode not in ("replace", "append"):
            raise ToolError(f"неизвестный режим '{mode}', допустимы replace и append")

        if mode == "append":
            existing = ctx.registry.read_prompt(target).rstrip()
            content = f"{existing}\n\n{content}"

        if len(content) < _MIN_LENGTH:
            raise ToolError(
                f"инструкция короче {_MIN_LENGTH} символов — это похоже на ошибку, "
                "а не на осмысленный апгрейд"
            )
        if len(content) > _MAX_LENGTH:
            raise ToolError(f"инструкция длиннее {_MAX_LENGTH} символов")

        backup_dir = ctx.config.data_dir / "prompt_backups"
        path = ctx.registry.write_prompt(target, content, backup_dir=backup_dir)

        who = "себе" if target.name == ctx.employee.name else f"сотруднику {target.mention}"
        log.info("[%s] переписал инструкцию %s (%s)", ctx.employee.name, who, mode)
        return ToolResult.success(
            f"{ctx.employee.title} обновил инструкцию {who}",
            f"Режим: {mode}. Файл: {path.name}. Итоговый размер: {len(content)} символов. "
            f"Предыдущая версия сохранена в data/prompt_backups.",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_target(ctx: ToolContext, target_name):
        if not target_name:
            return ctx.employee

        target = ctx.registry.get(str(target_name))
        if target is None:
            raise ToolError(f"сотрудник '{target_name}' не найден в реестре")

        if target.name == ctx.employee.name:
            return target

        if ctx.employee.name.lower() not in _SUPERVISOR_NAMES:
            raise ToolError(
                f"{ctx.employee.title} не может переписывать инструкцию коллеги "
                f"{target.mention}. Каждый отвечает за свою голову."
            )
        return target
