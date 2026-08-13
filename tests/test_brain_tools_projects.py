# tests/test_brain_tools_projects.py
from __future__ import annotations

from dataclasses import dataclass

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.projects import (
    ArchiveProjectTool,
    CreateProjectTool,
    LinkProjectTool,
    SetChatProjectTool,
    UnlinkProjectTool,
)
from cortex.models import Action

CHAT = -100500


@dataclass
class _FakeDeps:
    workspaces: object
    state: object
    config: object


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_create_project_sets_active(config, workspaces, state):
    deps = _FakeDeps(workspaces, state, config)
    result = await CreateProjectTool().execute(
        Action(tool="create_project", args={"name": "sports_api", "description": "API"}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("sports_api") is not None
    assert state.active_project(CHAT) == "sports_api"


async def test_link_project(config, workspaces, state, tmp_path):
    target = tmp_path / "external_repo"
    target.mkdir()
    deps = _FakeDeps(workspaces, state, config)

    result = await LinkProjectTool().execute(
        Action(tool="link_project", args={"name": "basehub", "path": str(target)}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("basehub").linked


async def test_set_chat_project(config, workspaces, state):
    workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, state, config)

    result = await SetChatProjectTool().execute(
        Action(tool="set_chat_project", args={"project": "sports_api"}), _ctx(deps)
    )
    assert result.ok
    assert state.active_project(CHAT) == "sports_api"


async def test_set_chat_project_clears_when_empty(config, workspaces, state):
    workspaces.create("sports_api")
    await state.set_active_project(CHAT, "sports_api")
    deps = _FakeDeps(workspaces, state, config)

    result = await SetChatProjectTool().execute(
        Action(tool="set_chat_project", args={"project": ""}), _ctx(deps)
    )
    assert result.ok
    assert state.active_project(CHAT) is None


async def test_unlink_project_clears_active_if_matching(config, workspaces, state, tmp_path):
    target = tmp_path / "external_repo"
    target.mkdir()
    workspaces.link("basehub", str(target))
    await state.set_active_project(CHAT, "basehub")
    deps = _FakeDeps(workspaces, state, config)

    result = await UnlinkProjectTool().execute(
        Action(tool="unlink_project", args={"name": "basehub"}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("basehub") is None
    assert target.exists()  # исходная папка цела
    assert state.active_project(CHAT) is None


async def test_archive_project(config, workspaces, state):
    workspaces.create("sports_api")
    await state.set_active_project(CHAT, "sports_api")
    deps = _FakeDeps(workspaces, state, config)

    result = await ArchiveProjectTool().execute(
        Action(tool="archive_project", args={"name": "sports_api"}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("sports_api") is None
    assert state.active_project(CHAT) is None
