# tests/test_choices.py
from __future__ import annotations

import json

import pytest

from cortex.brain.choices import PendingChoice, PendingChoiceStore


def _choice(choice_id="c1", chat_id=-100500) -> PendingChoice:
    return PendingChoice(
        id=choice_id,
        chat_id=chat_id,
        message_id=42,
        requester_id=1001,
        options=["A) Осьминог", "B) Кот", "C) Пчела", "D) Сова"],
    )


async def test_add_persists_to_disk(tmp_path):
    store = PendingChoiceStore(tmp_path / "pending_choices.json")
    await store.add(_choice())

    raw = json.loads((tmp_path / "pending_choices.json").read_text(encoding="utf-8"))
    assert raw["choices"][0]["id"] == "c1"
    assert raw["choices"][0]["options"] == ["A) Осьминог", "B) Кот", "C) Пчела", "D) Сова"]


async def test_pop_returns_and_removes(tmp_path):
    store = PendingChoiceStore(tmp_path / "pending_choices.json")
    await store.add(_choice())

    popped = await store.pop("c1")
    assert popped.options[1] == "B) Кот"
    assert await store.pop("c1") is None


async def test_pop_missing_returns_none(tmp_path):
    store = PendingChoiceStore(tmp_path / "pending_choices.json")
    assert await store.pop("does-not-exist") is None


async def test_state_survives_reload(tmp_path):
    path = tmp_path / "pending_choices.json"
    store = PendingChoiceStore(path)
    await store.add(_choice())

    reloaded = PendingChoiceStore(path)
    reloaded.load()
    popped = await reloaded.pop("c1")
    assert popped is not None
    assert popped.options[0] == "A) Осьминог"


@pytest.mark.parametrize("bad_root", ["[]", "123", "null", '"just a string"'])
def test_survives_non_object_root(tmp_path, bad_root):
    path = tmp_path / "pending_choices.json"
    path.write_text(bad_root, encoding="utf-8")
    store = PendingChoiceStore(path)
    assert store._choices == {}
