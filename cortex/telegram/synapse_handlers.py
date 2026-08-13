"""Команды Synapse — COO / Директор по инновациям.

Synapse — единственный сотрудник, которого можно дёрнуть напрямую
командой, минуя постановку задачи в проекте: сводка инноваций не
привязана ни к какому репозиторию.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..errors import CortexError
from ..logging_setup import get_logger
from . import formatting as fmt

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("synapse.router")


def build_synapse_router(deps: "Deps") -> Router:
    router = Router(name="synapse")

    @router.message(Command("digest", "innovations"))
    async def cmd_digest(message: Message, command: CommandObject) -> None:
        if message.from_user and message.from_user.id != deps.config.secrets.ceo_id:
            return

        synapse = deps.registry.get(deps.config.synapse_name)
        if synapse is None or not synapse.active:
            await message.reply(
                f"В штате нет активного <b>{fmt.esc(deps.config.synapse_name)}</b>. "
                "Найми его: /hire (в личке), тег "
                f"<code>{fmt.esc(deps.config.synapse_name)}</code>."
            )
            return

        query = (command.args or "").strip()
        status = await message.answer("🛰 Synapse сканирует внешний мир…")

        try:
            if query:
                stories = await deps.synapse.hackernews_search(query, limit=10)
                heading = f"Разведка Synapse: «{query}»"
            else:
                stories = await deps.synapse.hackernews_top()
                heading = "Сводка инноваций от Synapse"
        except CortexError as exc:
            await status.edit_text(fmt.error_report(exc, context="разведка не удалась"))
            return

        digest = deps.synapse.render_digest(stories, heading=heading)

        target_chat = (
            deps.config.secrets.ceo_dm_chat_id
            if deps.config.synapse.get("digest_target", "ceo_dm") == "ceo_dm"
            else message.chat.id
        )

        # Дайджест приходит от лица самого Synapse, а не от Cortex.
        await deps.bots.say(synapse, target_chat, digest)
        log.info("Synapse отправил дайджест (%d историй) в чат %s", len(stories), target_chat)

        if target_chat != message.chat.id:
            await status.edit_text("📬 Сводка ушла в личку CEO.")
        else:
            await status.delete()

    return router
