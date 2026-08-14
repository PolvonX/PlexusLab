# tests/test_plan_store.py
from __future__ import annotations

import json

import pytest

from cortex.brain.plan import Plan, PlanStore, Subtask

CHAT = -100500


def _plan(chat_id=CHAT) -> Plan:
    return Plan(
        chat_id=chat_id,
        goal="Зарегистрировать аккаунт",
        subtasks=[
            Subtask(id="1", description="Купить номер"),
            Subtask(id="2", description="Получить код по смс"),
        ],
    )


async def test_set_persists_to_disk(tmp_path):
    store = PlanStore(tmp_path / "plans.json")
    await store.set(_plan())

    raw = json.loads((tmp_path / "plans.json").read_text(encoding="utf-8"))
    assert raw["plans"][0]["goal"] == "Зарегистрировать аккаунт"
    assert len(raw["plans"][0]["subtasks"]) == 2


async def test_get_returns_stored_plan(tmp_path):
    store = PlanStore(tmp_path / "plans.json")
    await store.set(_plan())

    plan = store.get(CHAT)
    assert plan is not None
    assert plan.get("1").description == "Купить номер"
    assert plan.get("does-not-exist") is None


async def test_set_replaces_existing_plan_for_same_chat(tmp_path):
    store = PlanStore(tmp_path / "plans.json")
    await store.set(_plan())
    await store.set(Plan(chat_id=CHAT, goal="Новая цель", subtasks=[Subtask(id="1", description="x")]))

    plan = store.get(CHAT)
    assert plan.goal == "Новая цель"
    assert len(plan.subtasks) == 1


async def test_clear_removes_plan(tmp_path):
    store = PlanStore(tmp_path / "plans.json")
    await store.set(_plan())
    await store.clear(CHAT)

    assert store.get(CHAT) is None


async def test_state_survives_reload(tmp_path):
    path = tmp_path / "plans.json"
    store = PlanStore(path)
    plan = _plan()
    plan.scratchpad["phone"] = "+1234567890"
    await store.set(plan)

    reloaded = PlanStore(path)
    got = reloaded.get(CHAT)
    assert got is not None
    assert got.scratchpad == {"phone": "+1234567890"}


@pytest.mark.parametrize("bad_root", ["[]", "123", "null", '"just a string"'])
def test_survives_non_object_root(tmp_path, bad_root):
    path = tmp_path / "plans.json"
    path.write_text(bad_root, encoding="utf-8")
    store = PlanStore(path)
    assert store.get(CHAT) is None
