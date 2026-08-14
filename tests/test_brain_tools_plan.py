# tests/test_brain_tools_plan.py
from __future__ import annotations

from dataclasses import dataclass

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.plan_tools import CreatePlanTool, GetPlanStatusTool, UpdateSubtaskTool
from cortex.errors import ToolError
from cortex.models import Action

CHAT = -100500


class _FakeGateway:
    def __init__(self) -> None:
        self.replies: list[tuple] = []

    async def reply(self, chat_id, text, *, reply_to=None):
        self.replies.append((chat_id, text))


@dataclass
class _FakeDeps:
    gateway: object


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


_TASKS = [
    {"id": "1", "description": "Купить номер"},
    {"id": "2", "description": "Получить код по смс"},
    {"id": "3", "description": "Заполнить форму"},
]


async def test_create_plan_stores_plan_and_notifies_ceo(plan_store):
    gateway = _FakeGateway()
    deps = _FakeDeps(gateway=gateway)

    result = await CreatePlanTool(store=plan_store).execute(
        Action(tool="create_plan", args={"goal": "Зарегистрировать аккаунт", "tasks": _TASKS}),
        _ctx(deps),
    )

    assert result.ok
    plan = plan_store.get(CHAT)
    assert plan is not None
    assert len(plan.subtasks) == 3
    assert all(s.status == "pending" for s in plan.subtasks)

    assert len(gateway.replies) == 1
    chat_id, text = gateway.replies[0]
    assert chat_id == CHAT
    assert "Купить номер" in text
    assert "План" in text


async def test_create_plan_requires_goal(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    with pytest.raises(ToolError):
        await CreatePlanTool(store=plan_store).execute(
            Action(tool="create_plan", args={"tasks": _TASKS}), _ctx(deps)
        )


async def test_create_plan_rejects_empty_tasks(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    with pytest.raises(ToolError):
        await CreatePlanTool(store=plan_store).execute(
            Action(tool="create_plan", args={"goal": "x", "tasks": []}), _ctx(deps)
        )


async def test_create_plan_rejects_duplicate_ids(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    with pytest.raises(ToolError):
        await CreatePlanTool(store=plan_store).execute(
            Action(
                tool="create_plan",
                args={"goal": "x", "tasks": [
                    {"id": "1", "description": "a"}, {"id": "1", "description": "b"},
                ]},
            ),
            _ctx(deps),
        )


async def test_create_plan_replaces_existing_plan(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    tool = CreatePlanTool(store=plan_store)
    await tool.execute(Action(tool="create_plan", args={"goal": "первая", "tasks": _TASKS}), _ctx(deps))
    await tool.execute(
        Action(tool="create_plan", args={"goal": "вторая", "tasks": [{"id": "1", "description": "x"}]}),
        _ctx(deps),
    )

    plan = plan_store.get(CHAT)
    assert plan.goal == "вторая"
    assert len(plan.subtasks) == 1


async def test_update_subtask_requires_existing_plan(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    with pytest.raises(ToolError):
        await UpdateSubtaskTool(store=plan_store).execute(
            Action(tool="update_subtask", args={"task_id": "1", "status": "completed"}), _ctx(deps)
        )


async def test_update_subtask_requires_known_task_id(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    await CreatePlanTool(store=plan_store).execute(
        Action(tool="create_plan", args={"goal": "x", "tasks": _TASKS}), _ctx(deps)
    )
    with pytest.raises(ToolError):
        await UpdateSubtaskTool(store=plan_store).execute(
            Action(tool="update_subtask", args={"task_id": "does-not-exist", "status": "completed"}),
            _ctx(deps),
        )


async def test_update_subtask_rejects_invalid_status(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    await CreatePlanTool(store=plan_store).execute(
        Action(tool="create_plan", args={"goal": "x", "tasks": _TASKS}), _ctx(deps)
    )
    with pytest.raises(ToolError):
        await UpdateSubtaskTool(store=plan_store).execute(
            Action(tool="update_subtask", args={"task_id": "1", "status": "banana"}), _ctx(deps)
        )


async def test_scratchpad_flows_from_one_subtask_to_the_next(plan_store):
    """Ключевое требование CEO: шаг 2 должен видеть, что нашёл шаг 1."""
    deps = _FakeDeps(gateway=_FakeGateway())
    await CreatePlanTool(store=plan_store).execute(
        Action(tool="create_plan", args={"goal": "Зарегистрировать аккаунт", "tasks": _TASKS}),
        _ctx(deps),
    )

    await UpdateSubtaskTool(store=plan_store).execute(
        Action(
            tool="update_subtask",
            args={
                "task_id": "1", "status": "completed", "result": "Номер куплен",
                "remember": {"phone": "+1234567890"},
            },
        ),
        _ctx(deps),
    )

    status = await GetPlanStatusTool(store=plan_store).execute(
        Action(tool="get_plan_status", args={}), _ctx(deps)
    )
    assert status.ok
    assert "+1234567890" in status.detail
    assert "completed" in status.detail
    assert "Получить код по смс" in status.detail  # шаг 2 всё ещё виден как pending


async def test_get_plan_status_without_a_plan_fails_clearly(plan_store):
    deps = _FakeDeps(gateway=_FakeGateway())
    result = await GetPlanStatusTool(store=plan_store).execute(
        Action(tool="get_plan_status", args={}), _ctx(deps)
    )
    assert not result.ok
