# tests/test_brain_context.py
from __future__ import annotations

from cortex.brain.context import BrainPromptBuilder
from cortex.brain.tools.base import BrainTool, BrainToolContext, BrainToolRegistry
from cortex.brain.risk import RiskTier
from cortex.models import Action, ToolResult


class _Echo(BrainTool):
    name = "list_staff"
    description = "список сотрудников"
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        return ToolResult.success("ok")


def _builder(config, registry, workspaces, state) -> BrainPromptBuilder:
    tools = BrainToolRegistry()
    tools.register(_Echo())
    return BrainPromptBuilder(config, registry, workspaces, state, tools)


async def test_persona_is_separate_from_the_turn_content(config, registry, workspaces, state):
    """persona() идёт в --system-prompt отдельно от -p — см. Task 15."""
    builder = _builder(config, registry, workspaces, state)
    assert "цифровой директор" in builder.persona()


async def test_initial_prompt_contains_state_tools_and_message_but_not_persona(
    config, registry, workspaces, state, frontend
):
    await registry.add(frontend)
    workspaces.create("sports_api")
    builder = _builder(config, registry, workspaces, state)

    prompt = builder.build_initial(
        chat_id=-100500, history_block="(история чата пуста)", message_text="кто в штате?"
    )

    assert "цифровой директор" not in prompt  # это в persona(), не здесь
    assert "Frontend_Dev" in prompt
    assert "sports_api" in prompt
    assert "list_staff" in prompt
    assert "кто в штате?" in prompt
    assert "<action>" in prompt


def test_initial_prompt_has_no_stray_format_braces(config, registry, workspaces, state):
    """JSON-примеры в контракте не должны ломать сборку промпта."""
    builder = _builder(config, registry, workspaces, state)
    prompt = builder.build_initial(chat_id=-100500, history_block="", message_text="привет")
    assert '{"tool"' in prompt


def test_followup_prompt_reports_success():
    builder = BrainPromptBuilder.__new__(BrainPromptBuilder)  # чистая функция, deps не нужны
    text = BrainPromptBuilder.build_followup(
        builder, tool_name="list_staff", result=ToolResult.success("В штате 1", "detail here")
    )
    assert "list_staff" in text
    assert "успех" in text
    assert "detail here" in text


def test_followup_prompt_reports_failure():
    builder = BrainPromptBuilder.__new__(BrainPromptBuilder)
    text = BrainPromptBuilder.build_followup(
        builder, tool_name="fire_employee", result=ToolResult.failure("не найден")
    )
    assert "ошибка" in text
    assert "не найден" in text
