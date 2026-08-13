# tests/test_brain_tools_interactive.py
from __future__ import annotations

from dataclasses import dataclass

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.interactive import SendButtonsTool
from cortex.errors import ToolError
from cortex.models import Action

CHAT = -100500


class _FakeGateway:
    def __init__(self) -> None:
        self.asked: list[tuple] = []

    async def ask_choice(self, *, chat_id, choice_id, text, options):
        self.asked.append((chat_id, choice_id, text, options))


@dataclass
class _FakeDeps:
    gateway: object
    choices: object


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_sends_one_button_per_option_and_stores_pending_choice(choices):
    gateway = _FakeGateway()
    deps = _FakeDeps(gateway=gateway, choices=choices)

    result = await SendButtonsTool().execute(
        Action(
            tool="send_buttons",
            args={"text": "Вопрос 1?", "buttons": ["A) раз", "B) два", "C) три"]},
        ),
        _ctx(deps),
    )

    assert result.ok
    assert len(gateway.asked) == 1
    chat_id, choice_id, text, options = gateway.asked[0]
    assert chat_id == CHAT
    assert text == "Вопрос 1?"
    assert options == ["A) раз", "B) два", "C) три"]

    popped = await choices.pop(choice_id)
    assert popped is not None
    assert popped.chat_id == CHAT
    assert popped.options == ["A) раз", "B) два", "C) три"]
    assert popped.requester_id == 1001


async def test_missing_text_is_rejected(choices):
    deps = _FakeDeps(gateway=_FakeGateway(), choices=choices)
    with pytest.raises(ToolError):
        await SendButtonsTool().execute(
            Action(tool="send_buttons", args={"buttons": ["A", "B"]}), _ctx(deps)
        )


async def test_missing_buttons_is_rejected(choices):
    deps = _FakeDeps(gateway=_FakeGateway(), choices=choices)
    with pytest.raises(ToolError):
        await SendButtonsTool().execute(
            Action(tool="send_buttons", args={"text": "Вопрос?"}), _ctx(deps)
        )


async def test_too_many_buttons_is_rejected(choices):
    deps = _FakeDeps(gateway=_FakeGateway(), choices=choices)
    with pytest.raises(ToolError):
        await SendButtonsTool().execute(
            Action(
                tool="send_buttons",
                args={"text": "Вопрос?", "buttons": [f"опция {i}" for i in range(20)]},
            ),
            _ctx(deps),
        )
