"""Периметр: реагируем только на CEO и на ботов из реестра."""

from __future__ import annotations

import pytest

from cortex.errors import SecurityError
from cortex.security import Principal, SecurityGuard
from tests.conftest import BOT_ID, CEO_ID, GROUP_ID

STRANGER_ID = 4242


@pytest.fixture()
def guard(config, registry):
    return SecurityGuard(config, registry)


def test_ceo_in_group_allowed(guard):
    decision = guard.authorize(user_id=CEO_ID, chat_id=GROUP_ID, is_bot=False)

    assert decision.allowed
    assert decision.principal is Principal.CEO


def test_ceo_in_private_allowed(guard):
    assert guard.authorize(user_id=CEO_ID, chat_id=CEO_ID, is_bot=False).allowed


def test_stranger_rejected(guard):
    decision = guard.authorize(user_id=STRANGER_ID, chat_id=GROUP_ID, is_bot=False)

    assert not decision.allowed
    assert decision.principal is Principal.STRANGER


def test_foreign_chat_rejected_even_for_ceo(guard):
    """CEO пишет из постороннего чата — Cortex туда не отвечает."""
    assert not guard.authorize(user_id=CEO_ID, chat_id=-999, is_bot=False).allowed


async def test_registered_bot_allowed(guard, registry, frontend):
    await registry.add(frontend)

    decision = guard.authorize(user_id=BOT_ID, chat_id=GROUP_ID, is_bot=True)

    assert decision.allowed
    assert decision.principal is Principal.EMPLOYEE_BOT


def test_unknown_bot_rejected(guard):
    decision = guard.authorize(user_id=999999, chat_id=GROUP_ID, is_bot=True)

    assert not decision.allowed
    assert "не числится" in decision.reason


async def test_fired_bot_loses_access(guard, registry, frontend):
    await registry.add(frontend)
    await registry.fire(frontend.name)

    assert not guard.authorize(user_id=BOT_ID, chat_id=GROUP_ID, is_bot=True).allowed


def test_require_ceo_raises_for_others(guard):
    guard.require_ceo(CEO_ID)  # не бросает

    with pytest.raises(SecurityError):
        guard.require_ceo(STRANGER_ID)


def test_anonymous_sender_rejected(guard):
    assert not guard.authorize(user_id=None, chat_id=GROUP_ID, is_bot=False).allowed
