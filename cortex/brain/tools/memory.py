from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ...errors import ToolError
from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

if TYPE_CHECKING:
    from ...memory.vector_store import VectorStore


class MemorizeFactTool(BrainTool):
    name = "memorize_fact"
    description = (
        "Сохранить важный факт, выжимку или результаты ресерча в долгосрочную семантическую память "
        "для обхода лимитов контекста. Если находишь важное архитектурное решение или ключ — сохрани."
    )
    usage = (
        '{"tool": "memorize_fact", "args": {"topic": "Архитектура", '
        '"content": "Используем FastAPI для бэкенда", "tags": ["backend", "fastapi"]}}'
    )
    risk = RiskTier.NORMAL

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        topic = str(action.args.get("topic") or "").strip()
        content = str(action.args.get("content") or "").strip()
        tags = action.args.get("tags") or []
        
        if not topic or not content:
            raise ToolError("нужны topic и content (args.topic, args.content)")
            
        if not isinstance(tags, list):
            tags = [str(tags)]
        else:
            tags = [str(t) for t in tags]

        doc_id = uuid.uuid4().hex
        
        try:
            self._store.memorize(doc_id, topic, content, tags)
        except Exception as exc:
            raise ToolError(f"не удалось сохранить в память: {exc}")
            
        return ToolResult.success("Успешно сохранено в долгосрочную память.")


class RecallMemoryTool(BrainTool):
    name = "recall_memory"
    description = (
        "Найти информацию в долгосрочной памяти по смысловому запросу (RAG). "
        "Полезно, чтобы вспомнить старые решения или забытый контекст."
    )
    usage = '{"tool": "recall_memory", "args": {"query": "какой фреймворк мы используем для бэкенда?"}}'
    risk = RiskTier.NORMAL

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        query = str(action.args.get("query") or "").strip()
        if not query:
            raise ToolError("нужен query (args.query)")
            
        try:
            results = self._store.recall(query, n_results=3)
        except Exception as exc:
            raise ToolError(f"ошибка поиска в памяти: {exc}")
            
        if not results:
            return ToolResult.success("По твоему запросу ничего не найдено в памяти.")
            
        report = ["Найденные фрагменты в памяти:\n"]
        for i, res in enumerate(results, 1):
            meta = res.get("metadata", {})
            topic = meta.get("topic", "Без темы")
            tags = meta.get("tags", "")
            tags_str = f" [{tags}]" if tags else ""
            content = res.get("content", "")
            
            report.append(f"{i}. Тема: {topic}{tags_str}\n{content}\n")
            
        return ToolResult.success("Найдена информация:", "\n".join(report))
