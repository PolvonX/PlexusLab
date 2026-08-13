"""update_telegram_profile — бот сам правит себе имя и био.

Сотрудник управляет ТОЛЬКО своим профилем: используется его собственный
токен, поэтому чужой профиль недостижим физически, а не по договорённости.
Изменённое имя синхронизируется обратно в employees_registry.json.
"""

from __future__ import annotations

from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import Action, ToolResult
from .base import Tool, ToolContext

log = get_logger("tools.profile")

_NAME_LIMIT = 64
_SHORT_LIMIT = 120
_DESC_LIMIT = 512


class UpdateTelegramProfileTool(Tool):
    name = "update_telegram_profile"
    description = (
        "Обновить свой профиль в Telegram: отображаемое имя (name), краткое "
        "описание в карточке (short_description) и био (description)."
    )
    usage = (
        '{"tool": "update_telegram_profile", "args": {"name": "Frontend Dev", '
        '"description": "Верстаю интерфейсы Plexus Lab"}}'
    )

    async def execute(self, action: Action, ctx: ToolContext) -> ToolResult:
        args = action.args
        name = ctx.arg(args, "name", "title", "display_name")
        description = ctx.arg(args, "description", "bio", "about")
        short = ctx.arg(args, "short_description", "short", "summary")

        if not any((name, description, short)):
            raise ToolError(
                "нечего менять: укажи хотя бы одно из полей name, description, "
                "short_description"
            )

        changed: list[str] = []

        if name:
            value = self._trim(str(name), _NAME_LIMIT, "name")
            await self._call(ctx.bot.set_my_name, name=value)
            await ctx.registry.update(ctx.employee.name, display_name=value)
            changed.append(f"имя → «{value}»")

        if short:
            value = self._trim(str(short), _SHORT_LIMIT, "short_description")
            await self._call(ctx.bot.set_my_short_description, short_description=value)
            changed.append("краткое описание обновлено")

        if description:
            value = self._trim(str(description), _DESC_LIMIT, "description")
            await self._call(ctx.bot.set_my_description, description=value)
            changed.append("био обновлено")

        log.info("[%s] профиль обновлён: %s", ctx.employee.name, "; ".join(changed))
        return ToolResult.success(
            f"Профиль {ctx.employee.title} обновлён", "\n".join(f"• {c}" for c in changed)
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _trim(value: str, limit: int, field: str) -> str:
        value = value.strip()
        if not value:
            raise ToolError(f"поле '{field}' пустое")
        if len(value) > limit:
            raise ToolError(f"поле '{field}' длиннее {limit} символов (Telegram не примет)")
        return value

    @staticmethod
    async def _call(method, **kwargs) -> None:
        try:
            await method(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Telegram отклонил изменение: {exc}") from exc
