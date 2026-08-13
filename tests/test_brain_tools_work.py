# tests/test_brain_tools_work.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.work import AssignTaskTool, RequestDigestTool, SendFileTool, SetListenTool
from cortex.errors import ToolError
from cortex.models import Action, Employee

CHAT = -100500


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.dispatched: list[tuple] = []

    def new_task(self, **kwargs):
        return kwargs

    async def dispatch(self, task, *, requester_id):
        self.dispatched.append((task, requester_id))


class _FakeGateway:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def start_listener(self, employee) -> bool:
        self.started.append(employee.name)
        return True

    async def stop_listener(self, name: str) -> bool:
        self.stopped.append(name)
        return True


@dataclass
class _FakeDeps:
    orchestrator: object = None
    workspaces: object = None
    state: object = None
    registry: object = None
    gateway: object = None
    synapse: object = None
    bots: object = None
    config: object = None


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_assign_task_uses_explicit_project(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    workspaces.create("sports_api")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state, registry=registry)

    result = await AssignTaskTool().execute(
        Action(
            tool="assign_task",
            args={"employee": "Frontend_Dev", "project": "sports_api", "task": "почини хедер"},
        ),
        _ctx(deps),
    )
    await asyncio.sleep(0)  # дать шанс фоновой asyncio.create_task(...) выполниться
    assert result.ok
    assert len(orchestrator.dispatched) == 1


async def test_assign_task_falls_back_to_chat_active_project(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    workspaces.create("sports_api")
    await state.set_active_project(CHAT, "sports_api")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state, registry=registry)

    result = await AssignTaskTool().execute(
        Action(tool="assign_task", args={"employee": "Frontend_Dev", "task": "почини хедер"}),
        _ctx(deps),
    )
    assert result.ok


async def test_assign_task_without_any_project_hint_fails_clearly(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    workspaces.create("sports_api")
    workspaces.create("basehub")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state, registry=registry)

    result = await AssignTaskTool().execute(
        Action(tool="assign_task", args={"employee": "Frontend_Dev", "task": "почини хедер"}),
        _ctx(deps),
    )
    assert not result.ok
    assert "Непонятно" in result.summary or "проект" in result.summary.lower()


async def test_set_listen_on_calls_gateway(config, registry, frontend):
    await registry.add(frontend)
    gateway = _FakeGateway()
    deps = _FakeDeps(registry=registry, gateway=gateway)

    result = await SetListenTool().execute(
        Action(tool="set_listen", args={"name": "Frontend_Dev", "on": True}), _ctx(deps)
    )
    assert result.ok
    assert gateway.started == ["Frontend_Dev"]
    assert registry.require("Frontend_Dev").listen is True


async def test_send_file_rejects_missing_file(config, workspaces, tmp_path):
    workspaces.create("sports_api")
    gateway = _FakeGateway()
    gateway.gateway_bot = object()
    deps = _FakeDeps(workspaces=workspaces, gateway=gateway, config=config)

    with pytest.raises(ToolError):
        await SendFileTool().execute(
            Action(tool="send_file", args={"project": "sports_api", "path": "nope.txt"}), _ctx(deps)
        )
