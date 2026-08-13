"""Каркас Tool Use: контекст, базовый класс и реестр инструментов.

Инструмент ничего не знает про Telegram-роутинг и про парсер — он получает
готовый ToolContext и возвращает ToolResult. Благодаря этому любой
инструмент тестируется в изоляции.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

from ..config import Config
from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import Action, Employee, ToolResult
from ..registry import EmployeeRegistry
from ..workspace import Project, WorkspaceManager

if TYPE_CHECKING:  # pragma: no cover
    from aiogram import Bot

log = get_logger("tools")


@dataclass(slots=True)
class ToolContext:
    """Всё, что инструменту нужно знать о вызове."""

    employee: Employee
    project: Project
    chat_id: int
    message_id: int | None
    bot: "Bot"                     # бот САМОГО сотрудника, не Cortex
    config: Config
    registry: EmployeeRegistry
    workspaces: WorkspaceManager
    requester_id: int

    def arg(self, args: dict[str, Any], *names: str, default: Any = None, required: bool = False) -> Any:
        """Достать аргумент по одному из синонимов."""
        for name in names:
            if name in args and args[name] not in (None, ""):
                return args[name]
        if required:
            raise ToolError(
                f"инструменту не хватает аргумента '{names[0]}' "
                f"(получено: {', '.join(args) or 'ничего'})"
            )
        return default


class Tool(ABC):
    """Базовый инструмент."""

    #: Имя, которое агент пишет в поле "tool".
    name: str = ""
    #: Однострочное описание для промпта.
    description: str = ""
    #: Пример вызова для промпта.
    usage: str = ""

    @abstractmethod
    async def execute(self, action: Action, ctx: ToolContext) -> ToolResult:
        ...

    def doc(self) -> str:
        lines = [f"- **{self.name}** — {self.description}"]
        if self.usage:
            lines.append(f"  Пример: `{self.usage}`")
        return "\n".join(lines)


class ToolRegistry:
    """Набор инструментов + политика доступа по сотрудникам."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ToolError(f"У инструмента {type(tool).__name__} не задано имя")
        self._tools[tool.name] = tool
        log.debug("Зарегистрирован инструмент %s", tool.name)

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    # ------------------------------------------------------------------
    def allowed_names(self, employee: Employee) -> list[str]:
        """Персональный список из реестра важнее политики в config.yaml."""
        names = employee.tools or self.config.tools_for(employee.name)
        return [n for n in names if n in self._tools]

    def is_allowed(self, employee: Employee, tool_name: str) -> bool:
        return tool_name in self.allowed_names(employee)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def docs_for(self, employee: Employee) -> str:
        allowed = self.allowed_names(employee)
        if not allowed:
            return "- (инструменты не выданы: ты можешь только отвечать текстом)"
        return "\n".join(self._tools[name].doc() for name in allowed)

    # ------------------------------------------------------------------
    async def dispatch(self, action: Action, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(action.tool)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "нет ни одного"
            return ToolResult.failure(
                f"Инструмент '{action.tool}' не существует",
                f"Доступные инструменты системы: {known}",
            )

        if not self.is_allowed(ctx.employee, action.tool):
            allowed = ", ".join(self.allowed_names(ctx.employee)) or "нет ни одного"
            log.warning(
                "%s пытался вызвать запрещённый '%s'", ctx.employee.name, action.tool
            )
            return ToolResult.failure(
                f"{ctx.employee.title} не имеет доступа к '{action.tool}'",
                f"Разрешено этой роли: {allowed}",
            )

        try:
            return await tool.execute(action, ctx)
        except ToolError as exc:
            return ToolResult.failure(f"{action.tool}: {exc}")
        except Exception as exc:  # noqa: BLE001 — падение инструмента не роняет Cortex
            log.exception("Инструмент %s упал", action.tool)
            return ToolResult.failure(
                f"{action.tool} упал: {type(exc).__name__}", str(exc)[:500]
            )
