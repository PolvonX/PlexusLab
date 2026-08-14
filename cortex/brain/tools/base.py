# cortex/brain/tools/base.py
"""Каркас мета-инструментов мозга — аналог cortex/tools/base.py, но без
персональной политики доступа: любой инструмент доступен CEO единообразно,
а решение «выполнить сразу или спросить» принимает risk.py на уровне
brain/agent.py, не сам реестр.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from ...errors import CortexError, ToolError
from ...logging_setup import get_logger
from ...models import Action, ToolResult
from ..risk import RiskTier

if TYPE_CHECKING:  # pragma: no cover
    from ...deps import Deps

log = get_logger("brain.tools")


@dataclass(slots=True)
class BrainToolContext:
    """Всё, что мета-инструменту нужно знать о вызове."""

    deps: "Deps"
    chat_id: int
    requester_id: int


class BrainTool(ABC):
    name: str = ""
    description: str = ""
    usage: str = ""
    #: Риск по умолчанию — переопределяется per-tool через
    #: config.yaml -> brain.risk_overrides (см. cortex/brain/risk.py).
    risk: RiskTier = RiskTier.NORMAL
    #: True только у RunCustomToolTool (brain/tools/custom.py) — отличает
    #: сгенерированный агентом инструмент от встроенного, чтобы agent.py мог
    #: включить для него self-healing retry-loop, не трогая остальные.
    is_custom: bool = False

    @abstractmethod
    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        ...

    def doc(self) -> str:
        lines = [f"- **{self.name}** ({self.risk.value}) — {self.description}"]
        if self.usage:
            lines.append(f"  Пример: `{self.usage}`")
        return "\n".join(lines)


class BrainToolRegistry:
    """Набор мета-инструментов мозга."""

    def __init__(self) -> None:
        self._tools: dict[str, BrainTool] = {}

    def register(self, tool: BrainTool) -> None:
        if not tool.name:
            raise ToolError(f"У инструмента {type(tool).__name__} не задано имя")
        self._tools[tool.name] = tool
        log.debug("Зарегистрирован инструмент мозга %s (%s)", tool.name, tool.risk.value)

    def register_all(self, tools: Iterable[BrainTool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> BrainTool | None:
        return self._tools.get(name)

    def risk_of(self, name: str) -> RiskTier | None:
        tool = self._tools.get(name)
        return tool.risk if tool else None

    def docs(self) -> str:
        if not self._tools:
            return "- (инструментов нет)"
        return "\n".join(t.doc() for t in sorted(self._tools.values(), key=lambda t: t.name))

    # ------------------------------------------------------------------
    async def dispatch(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        tool = self._tools.get(action.tool)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "нет ни одного"
            return ToolResult.failure(
                f"Инструмент '{action.tool}' не существует", f"Доступные инструменты мозга: {known}"
            )
        try:
            return await tool.execute(action, ctx)
        except CortexError as exc:
            # ToolError и родня (RegistryError, WorkspaceError...) — ожидаемый
            # исход: инструменты вызывают registry.require()/workspaces.require()
            # напрямую и полагаются на то, что их сообщение дойдёт до чата как есть.
            return ToolResult.failure(f"{action.tool}: {exc}")
        except Exception as exc:  # noqa: BLE001 — падение инструмента не роняет Cortex
            log.exception("Инструмент мозга %s упал", action.tool)
            return ToolResult.failure(
                f"{action.tool} упал: {type(exc).__name__}", str(exc)[:500]
            )
