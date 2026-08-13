# tests/test_pending.py
from __future__ import annotations

import json

import pytest

from cortex.brain.pending import PendingAction, PendingActionStore


def _action(action_id="p1", chat_id=-100500) -> PendingAction:
    return PendingAction(
        id=action_id,
        chat_id=chat_id,
        message_id=42,
        requester_id=1001,
        tool="fire_employee",
        args={"name": "Frontend_Dev"},
        risk="risky",
        summary="Уволить @Frontend_Dev",
    )


async def test_add_persists_to_disk(tmp_path):
    store = PendingActionStore(tmp_path / "pending_actions.json")
    await store.add(_action())

    raw = json.loads((tmp_path / "pending_actions.json").read_text(encoding="utf-8"))
    assert raw["actions"][0]["id"] == "p1"


async def test_get_and_pop(tmp_path):
    store = PendingActionStore(tmp_path / "pending_actions.json")
    await store.add(_action())

    assert store.get("p1") is not None
    popped = await store.pop("p1")
    assert popped.tool == "fire_employee"
    assert store.get("p1") is None


async def test_pop_missing_returns_none(tmp_path):
    store = PendingActionStore(tmp_path / "pending_actions.json")
    assert await store.pop("does-not-exist") is None


async def test_state_survives_reload(tmp_path):
    path = tmp_path / "pending_actions.json"
    store = PendingActionStore(path)
    await store.add(_action())

    reloaded = PendingActionStore(path)
    reloaded.load()
    assert reloaded.get("p1") is not None
