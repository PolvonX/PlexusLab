# tests/test_brain_tools_read.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.read import GetEmployeeTool, GetStatusTool, ListProjectsTool, ListStaffTool
from cortex.errors import RegistryError
from cortex.models import Action


@dataclass
class _FakeDeps:
    registry: object
    workspaces: object
    state: object
    scheduler: object
    config: object
    tools: object
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def uptime_seconds(self) -> float:
        return 0.0


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=-100500, requester_id=1001)


async def test_list_staff_reports_empty(config, registry, workspaces, state):
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)
    result = await ListStaffTool().execute(Action(tool="list_staff", args={}), _ctx(deps))
    assert result.ok
    assert "пуст" in result.summary


async def test_list_staff_lists_employees(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)

    result = await ListStaffTool().execute(Action(tool="list_staff", args={}), _ctx(deps))
    assert "Frontend_Dev" in result.detail


async def test_get_employee_not_found_is_a_failure_not_a_crash(config, registry, workspaces, state):
    from cortex.tools import ToolRegistry

    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=ToolRegistry(config))
    result = await GetEmployeeTool().execute(
        Action(tool="get_employee", args={"name": "Ghost"}), _ctx(deps)
    )
    assert not result.ok
    assert "не найден" in result.summary


async def test_get_employee_found(config, registry, workspaces, state, frontend):
    from cortex.tools import ToolRegistry

    await registry.add(frontend)
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=ToolRegistry(config))

    result = await GetEmployeeTool().execute(
        Action(tool="get_employee", args={"name": "Frontend_Dev"}), _ctx(deps)
    )
    assert result.ok
    assert "Senior Frontend Engineer" in result.detail


async def test_list_projects_reports_empty(config, registry, workspaces, state):
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)
    result = await ListProjectsTool().execute(Action(tool="list_projects", args={}), _ctx(deps))
    assert "Ни одной" in result.detail


async def test_list_projects_marks_active_project_for_chat(config, registry, workspaces, state):
    workspaces.create("sports_api")
    await state.set_active_project(-100500, "sports_api")
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)

    result = await ListProjectsTool().execute(Action(tool="list_projects", args={}), _ctx(deps))
    assert "активный в этом чате" in result.detail


class _FakeScheduler:
    active: list = []


async def test_get_status_reports_no_active_tasks(config, registry, workspaces, state):
    deps = _FakeDeps(registry, workspaces, state, scheduler=_FakeScheduler(), config=config, tools=None)
    result = await GetStatusTool().execute(Action(tool="get_status", args={}), _ctx(deps))
    assert "Активных задач нет" in result.detail
