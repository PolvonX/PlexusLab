"""Synapse — COO / Директор по инновациям.

Единственный сотрудник Plexus Lab с выходом наружу. Он не пишет код: его
работа — читать мир (HackerNews, статьи, документацию) и приносить CEO
сжатые сводки с предложениями фич. Поэтому весь сетевой доступ компании
сосредоточен здесь, в одном модуле, а не размазан по инструментам.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Config
from ..errors import ToolError
from ..logging_setup import get_logger

log = get_logger("synapse")

_HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
_HN_SEARCH = "https://hn.algolia.com/api/v1/search"

_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")


@dataclass(slots=True)
class Story:
    title: str
    url: str
    score: int
    comments: int
    hn_url: str

    def as_line(self) -> str:
        return (
            f"• <b>{html.escape(self.title)}</b> — {self.score}↑ / {self.comments}💬\n"
            f"  {html.escape(self.url or self.hn_url)}"
        )

    def as_text(self) -> str:
        return f"- {self.title} ({self.score} points, {self.comments} comments)\n  {self.url or self.hn_url}"


class SynapseService:
    """Клиент внешнего мира: HackerNews + чтение произвольных страниц."""

    def __init__(self, config: Config) -> None:
        self.config = config
        settings = config.synapse
        self._timeout = float(settings.get("fetch_timeout", 20))
        self._max_chars = int(settings.get("max_page_chars", 12000))
        self._user_agent = settings.get("user_agent", "PlexusLab-Synapse/1.0")
        self._top_limit = int(settings.get("hackernews_top_limit", 12))
        self._min_score = int(settings.get("hackernews_min_score", 80))

    # ------------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": self._user_agent},
        )

    # ------------------------------------------------------------------
    async def hackernews_top(self, limit: int | None = None) -> list[Story]:
        limit = limit or self._top_limit
        async with self._client() as client:
            try:
                response = await client.get(_HN_TOP)
                response.raise_for_status()
                ids: list[int] = response.json()[: limit * 3]
            except (httpx.HTTPError, ValueError) as exc:
                raise ToolError(f"HackerNews недоступен: {exc}") from exc

            async def fetch(item_id: int) -> dict[str, Any] | None:
                try:
                    item = await client.get(_HN_ITEM.format(id=item_id))
                    item.raise_for_status()
                    return item.json()
                except (httpx.HTTPError, ValueError):
                    return None

            raw_items = await asyncio.gather(*(fetch(i) for i in ids))

        stories: list[Story] = []
        for item in raw_items:
            if not item or item.get("type") != "story" or item.get("dead"):
                continue
            score = int(item.get("score", 0))
            if score < self._min_score:
                continue
            stories.append(
                Story(
                    title=item.get("title", "без заголовка"),
                    url=item.get("url", ""),
                    score=score,
                    comments=int(item.get("descendants", 0)),
                    hn_url=f"https://news.ycombinator.com/item?id={item.get('id')}",
                )
            )
            if len(stories) >= limit:
                break

        log.info("HackerNews: отобрано %d историй", len(stories))
        return stories

    # ------------------------------------------------------------------
    async def hackernews_search(self, query: str, limit: int = 10) -> list[Story]:
        params = {"query": query, "tags": "story", "hitsPerPage": limit}
        async with self._client() as client:
            try:
                response = await client.get(_HN_SEARCH, params=params)
                response.raise_for_status()
                hits = response.json().get("hits", [])
            except (httpx.HTTPError, ValueError) as exc:
                raise ToolError(f"Поиск по HackerNews не удался: {exc}") from exc

        return [
            Story(
                title=hit.get("title") or hit.get("story_title") or "без заголовка",
                url=hit.get("url") or "",
                score=int(hit.get("points") or 0),
                comments=int(hit.get("num_comments") or 0),
                hn_url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            )
            for hit in hits
        ]

    # ------------------------------------------------------------------
    async def read_url(self, url: str) -> str:
        """Забрать страницу и вернуть очищенный от разметки текст."""
        if not url.lower().startswith(("http://", "https://")):
            raise ToolError(f"'{url}' не похож на http(s)-ссылку")

        async with self._client() as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ToolError(f"страница не открылась: {exc}") from exc

            content_type = response.headers.get("content-type", "")
            body = response.text

        if "application/json" in content_type:
            return body[: self._max_chars]

        text = _SCRIPT_RE.sub(" ", body)
        text = _TAG_RE.sub("\n", text)
        text = html.unescape(text)
        text = _WS_RE.sub(" ", text)
        text = _BLANK_RE.sub("\n\n", text)
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

        if len(text) > self._max_chars:
            text = text[: self._max_chars] + "\n… (страница обрезана)"
        return text

    # ------------------------------------------------------------------
    def render_digest(self, stories: list[Story], *, heading: str) -> str:
        """HTML-сводка для отправки в Telegram."""
        if not stories:
            return (
                f"<b>{html.escape(heading)}</b>\n\n"
                "Сегодня в мире тихо: ничего выше порога значимости не нашлось."
            )
        body = "\n\n".join(story.as_line() for story in stories)
        return (
            f"<b>{html.escape(heading)}</b>\n\n{body}\n\n"
            "<i>Synapse, COO Plexus Lab — какие из них берём в работу?</i>"
        )
