from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.self_work import SelfExecuteTaskTool
from cortex.errors import ToolError, WorkspaceError
from cortex.models import Action

CHAT = -100500
CORTEX_TOKEN = "1:GATEWAY"


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.dispatched: list = []

    def new_task(self, **kwargs):
        return kwargs

    async def dispatch(self, task, *, requester_id):
        self.dispatched.append(task)


@dataclass
class _FakeSecrets:
    cortex_token: str = CORTEX_TOKEN


@dataclass
class _FakeConfig:
    orchestrator_name: str = "Cortex"
    secrets: object = field(default_factory=_FakeSecrets)


@dataclass
class _FakeDeps:
    orchestrator: object
    workspaces: object
    state: object
    config: object = field(default_factory=_FakeConfig)


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_self_execute_uses_explicit_project(workspaces, state):
    workspaces.create("sports_api")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state)

    result = await SelfExecuteTaskTool().execute(
        Action(tool="self_execute_task", args={"project": "sports_api", "task": "скачай видео в downloads/"}),
        _ctx(deps),
    )
    await asyncio.sleep(0)  # дать шанс фоновой asyncio.create_task(...) выполниться

    assert result.ok
    assert len(orchestrator.dispatched) == 1
    task = orchestrator.dispatched[0]
    assert task["employee"].name == "Cortex"
    assert task["employee"].token == CORTEX_TOKEN
    assert task["employee"].prompt_path == "prompts/cortex.md"
    assert task["project_name"] == "sports_api"


async def test_self_execute_falls_back_to_chat_active_project(workspaces, state):
    workspaces.create("sports_api")
    await state.set_active_project(CHAT, "sports_api")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state)

    result = await SelfExecuteTaskTool().execute(
        Action(tool="self_execute_task", args={"task": "прогони скрипт"}), _ctx(deps)
    )
    assert result.ok


async def test_self_execute_without_task_text_fails(workspaces, state):
    deps = _FakeDeps(orchestrator=_FakeOrchestrator(), workspaces=workspaces, state=state)
    with pytest.raises(ToolError):
        await SelfExecuteTaskTool().execute(
            Action(tool="self_execute_task", args={"project": "x"}), _ctx(deps)
        )


async def test_self_execute_without_any_project_hint_fails_clearly(workspaces, state):
    workspaces.create("sports_api")
    workspaces.create("basehub")
    deps = _FakeDeps(orchestrator=_FakeOrchestrator(), workspaces=workspaces, state=state)

    with pytest.raises(WorkspaceError):
        await SelfExecuteTaskTool().execute(
            Action(tool="self_execute_task", args={"task": "сделай что-нибудь"}), _ctx(deps)
        )
