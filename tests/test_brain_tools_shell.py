# tests/test_brain_tools_shell.py
from __future__ import annotations

from dataclasses import dataclass

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.shell_tool import ExecuteCommandBrainTool
from cortex.errors import ToolError
from cortex.models import Action

CHAT = -100500


@dataclass
class _FakeDeps:
    workspaces: object
    config: object


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_requires_project_arg(config, workspaces):
    deps = _FakeDeps(workspaces, config)
    with pytest.raises(ToolError, match="project"):
        await ExecuteCommandBrainTool().execute(
            Action(tool="execute_command", args={"command": "echo hi"}), _ctx(deps)
        )


async def test_runs_inside_project_sandbox(config, workspaces):
    project = workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, config)

    result = await ExecuteCommandBrainTool().execute(
        Action(
            tool="execute_command",
            args={"project": "sports_api", "command": "echo plexus > created.txt"},
        ),
        _ctx(deps),
    )
    assert result.ok
    assert (project.path / "created.txt").exists()


async def test_blocklist_still_applies(config, workspaces):
    workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, config)

    with pytest.raises(ToolError, match="заблокирована"):
        await ExecuteCommandBrainTool().execute(
            Action(tool="execute_command", args={"project": "sports_api", "command": "rm -rf /"}),
            _ctx(deps),
        )


async def test_cannot_escape_project_sandbox(config, workspaces):
    workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, config)

    with pytest.raises(ToolError, match="за пределы"):
        await ExecuteCommandBrainTool().execute(
            Action(
                tool="execute_command",
                args={"project": "sports_api", "command": "echo x", "cwd": "../../"},
            ),
            _ctx(deps),
        )
