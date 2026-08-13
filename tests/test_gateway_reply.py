# tests/test_gateway_reply.py
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from cortex.telegram.gateway import Gateway


class _FakeGatewayBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.sent_with_markup: list[tuple] = []

    async def send_message(
        self, *, chat_id, text, reply_to_message_id=None, disable_web_page_preview=True,
        parse_mode=None, reply_markup=None, **_
    ):
        if reply_markup is not None:
            self.sent_with_markup.append((chat_id, text, reply_markup))
            return
        self.sent.append((chat_id, text, reply_to_message_id, parse_mode))


@dataclass
class _FakeConfig:
    max_message_length: int = 3800


@dataclass
class _FakeDeps:
    config: object = field(default_factory=_FakeConfig)


async def test_reply_before_start_raises():
    gateway = Gateway(_FakeDeps())
    with pytest.raises(RuntimeError):
        await gateway.reply(-100500, "hi")


async def test_reply_sends_via_gateway_bot():
    gateway = Gateway(_FakeDeps())
    fake_bot = _FakeGatewayBot()
    gateway._gateway_bot = fake_bot  # обходим start(): не поднимаем реальный aiogram.Bot

    await gateway.reply(-100500, "Привет, это Cortex", reply_to=42)

    assert fake_bot.sent == [(-100500, "Привет, это Cortex", 42, None)]


def test_gateway_bot_property_before_start_raises():
    gateway = Gateway(_FakeDeps())
    with pytest.raises(RuntimeError):
        _ = gateway.gateway_bot


def test_gateway_bot_property_after_start_like_assignment():
    gateway = Gateway(_FakeDeps())
    fake_bot = _FakeGatewayBot()
    gateway._gateway_bot = fake_bot
    assert gateway.gateway_bot is fake_bot


async def test_ask_confirmation_sends_buttons_with_action_id_in_callback_data():
    gateway = Gateway(_FakeDeps())
    fake_bot = _FakeGatewayBot()
    gateway._gateway_bot = fake_bot

    await gateway.ask_confirmation(
        chat_id=-100500, action_id="abc123", summary="Уволить @Frontend_Dev", risk="risky"
    )

    assert len(fake_bot.sent_with_markup) == 1
    chat_id, text, markup = fake_bot.sent_with_markup[0]
    assert "Уволить" in text
    buttons = [b for row in markup.inline_keyboard for b in row]
    callback_data = {b.callback_data for b in buttons}
    assert callback_data == {"brain:confirm:abc123", "brain:cancel:abc123"}
