# cortex/brain/context.py
"""Сборка промпта для мозга: персона + состояние компании + инструменты +
история + сообщение CEO. Тот же принцип, что у context/builder.py для
сотрудников: порядок блоков важен, задача идёт последней.
"""

from __future__ import annotations

from ..config import Config
from ..models import ToolResult
from ..registry import EmployeeRegistry
from ..state import ChatState
from ..workspace import WorkspaceManager
from .tools.base import BrainToolRegistry

_PERSONA_FILE = "cortex_brain.md"

_FALLBACK_PERSONA = (
    "Ты — Cortex, цифровой директор Plexus Lab. Разговаривай с CEO свободно "
    "и управляй компанией через доступные инструменты."
)

_ACTION_CONTRACT = """\
## Формат действия

Чтобы что-то сделать, вставь в ответ блок:

<action>
{"tool": "list_staff", "args": {}}
</action>

Правила:
1. Внутри <action> — строго один JSON-объект: {"tool": "...", "args": {...}}.
2. Блоков может быть несколько, выполняются по порядку, до первой неудачи.
3. Текст вне блоков — твоя реплика CEO. Пиши коротко.
4. Не нужно действие — просто ответь текстом."""


class BrainPromptBuilder:
    def __init__(
        self,
        config: Config,
        registry: EmployeeRegistry,
        workspaces: WorkspaceManager,
        state: ChatState,
        tools: BrainToolRegistry,
    ) -> None:
        self.config = config
        self.registry = registry
        self.workspaces = workspaces
        self.state = state
        self.tools = tools

    # ------------------------------------------------------------------
    def persona(self) -> str:
        """Статичная личность Cortex — идёт в --system-prompt, не в -p."""
        path = self.config.prompts_dir / _PERSONA_FILE
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return _FALLBACK_PERSONA

    def _state_block(self, chat_id: int) -> str:
        employees = self.registry.all()
        staff = (
            ", ".join(f"@{e.name} ({e.role})" for e in employees) if employees else "пока никого"
        )
        projects = self.workspaces.list()
        project_names = ", ".join(p.name for p in projects) if projects else "ни одного"
        active = self.state.active_project(chat_id) or "не закреплён"

        return (
            f"## Состояние компании\n\n"
            f"Штат: {staff}\n"
            f"Проекты: {project_names}\n"
            f"Активный проект этого чата: {active}"
        )

    # ------------------------------------------------------------------
    def build_initial(self, *, chat_id: int, history_block: str, message_text: str) -> str:
        blocks = [
            self._state_block(chat_id),
            "",
            "## Доступные инструменты",
            "",
            self.tools.docs(),
            "",
            _ACTION_CONTRACT,
            "",
            "---",
            "",
            "## Последние сообщения чата",
            "",
            history_block.strip(),
            "",
            "---",
            "",
            "## Сообщение CEO",
            "",
            message_text.strip(),
        ]
        return "\n".join(blocks)

    def build_followup(self, *, tool_name: str, result: ToolResult) -> str:
        status = "успех" if result.ok else "ошибка"
        parts = [f"Результат {tool_name} ({status}): {result.summary}"]
        if result.detail:
            parts += ["", result.detail]
        return "\n".join(parts)

    def build_custom_tool_failure_followup(
        self, *, tool_name: str, result: ToolResult, attempt: int, max_attempts: int,
    ) -> str:
        """Отдельный от build_followup формат: полный traceback без обрезки
        по summary, явное напоминание про create_tool и честный счётчик
        попыток — агент должен понимать, что лимит не бесконечный."""
        remaining = max_attempts - attempt
        parts = [
            f"Инструмент '{tool_name}' упал (попытка {attempt} из {max_attempts}).",
            "",
            f"Причина: {result.summary}",
        ]
        if result.detail:
            parts += ["", "Полный вывод/traceback:", result.detail]
        parts += [
            "",
            f"Осталось попыток: {remaining}." if remaining > 0 else "Это была последняя попытка.",
            f"Если ошибка в самом коде инструмента (опечатка, незапущенный "
            f"импорт, неверная логика) — перепиши его через create_tool с "
            f"именем '{tool_name}' ещё раз, новый код заменит старый. Если "
            f"дело не в коде — попробуй другой подход.",
        ]
        return "\n".join(parts)
