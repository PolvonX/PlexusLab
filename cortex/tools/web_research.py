"""web_research — глобальный поиск. Выдаётся только Synapse.

Единственный инструмент компании, который ходит в интернет. Поэтому он
живёт отдельно, не имеет доступа к файловой системе и не умеет ничего
исполнять — только читать.
"""

from __future__ import annotations

from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import Action, ToolResult
from ..agents.synapse import SynapseService
from .base import Tool, ToolContext

log = get_logger("tools.research")

_MAX_DETAIL = 3000


class WebResearchTool(Tool):
    name = "web_research"
    description = (
        "Разведка внешнего мира: топ HackerNews, поиск по HackerNews или чтение "
        "конкретной веб-страницы. Источники: hackernews | search | url."
    )
    usage = (
        '{"tool": "web_research", "args": {"source": "hackernews", "limit": 10}} '
        'или {"tool": "web_research", "args": {"source": "url", "url": "https://..."}}'
    )

    def __init__(self, service: SynapseService) -> None:
        self.service = service

    async def execute(self, action: Action, ctx: ToolContext) -> ToolResult:
        args = action.args
        source = str(ctx.arg(args, "source", "type", "mode", default="hackernews")).lower()
        limit = self._as_int(ctx.arg(args, "limit", "count", default=10), 10)

        if source in ("hackernews", "hn", "top", "frontpage"):
            stories = await self.service.hackernews_top(limit=limit)
            if not stories:
                return ToolResult.success("HackerNews: ничего выше порога значимости")
            return ToolResult.success(
                f"HackerNews: {len(stories)} значимых историй",
                "\n".join(story.as_text() for story in stories)[:_MAX_DETAIL],
            )

        if source in ("search", "query", "hn_search"):
            query = ctx.arg(args, "query", "q", "text", required=True)
            stories = await self.service.hackernews_search(str(query), limit=limit)
            if not stories:
                return ToolResult.success(f"По запросу «{query}» ничего не найдено")
            return ToolResult.success(
                f"Найдено {len(stories)} материалов по «{query}»",
                "\n".join(story.as_text() for story in stories)[:_MAX_DETAIL],
            )

        if source in ("url", "page", "web", "fetch"):
            url = ctx.arg(args, "url", "link", "address", required=True)
            text = await self.service.read_url(str(url))
            return ToolResult.success(
                f"Страница прочитана ({len(text)} символов)", text[:_MAX_DETAIL]
            )

        raise ToolError(
            f"неизвестный источник '{source}'. Допустимо: hackernews, search, url"
        )

    @staticmethod
    def _as_int(value, fallback: int) -> int:
        try:
            return max(1, min(int(value), 30))
        except (TypeError, ValueError):
            return fallback
