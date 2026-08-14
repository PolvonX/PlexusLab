# tests/test_brain_tools_custom.py
from __future__ import annotations

import sys
from dataclasses import dataclass, field

import pytest

from cortex.brain.risk import RiskTier
from cortex.brain.tools.base import BrainToolContext, BrainToolRegistry
from cortex.brain.tools.custom import CreateToolTool, RunCustomToolTool
from cortex.brain.tools.custom_store import CustomToolRecord, CustomToolStore
from cortex.errors import ToolError
from cortex.models import Action

CHAT = -100500


@dataclass
class _FakeConfig:
    command_blocklist: list = field(default_factory=list)
    max_command_timeout: int = 30
    data_dir: object = None


@dataclass
class _FakeDeps:
    config: object


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


@pytest.fixture()
def store(tmp_path):
    return CustomToolStore(
        scripts_dir=tmp_path / "brain_tools", registry_path=tmp_path / "custom_tools.json"
    )


@pytest.fixture()
def registry():
    return BrainToolRegistry()


async def test_create_tool_writes_script_and_registers_it(store, registry, tmp_path):
    deps = _FakeDeps(config=_FakeConfig(data_dir=tmp_path))
    tool = CreateToolTool(store=store, registry=registry)

    result = await tool.execute(
        Action(
            tool="create_tool",
            args={
                "name": "count_words",
                "description": "Считает слова в файле",
                "code": "print('hi')",
            },
        ),
        _ctx(deps),
    )

    assert result.ok
    script = tmp_path / "brain_tools" / "count_words.py"
    assert script.read_text(encoding="utf-8") == "print('hi')"
    assert registry.get("count_words") is not None
    assert len(store.all()) == 1


async def test_create_tool_is_risky():
    assert CreateToolTool.risk == RiskTier.RISKY


async def test_create_tool_rejects_invalid_name(store, registry, tmp_path):
    deps = _FakeDeps(config=_FakeConfig(data_dir=tmp_path))
    tool = CreateToolTool(store=store, registry=registry)

    with pytest.raises(ToolError):
        await tool.execute(
            Action(
                tool="create_tool",
                args={"name": "Not Valid!", "description": "x", "code": "pass"},
            ),
            _ctx(deps),
        )


async def test_create_tool_rejects_name_collision(store, registry, tmp_path):
    deps = _FakeDeps(config=_FakeConfig(data_dir=tmp_path))
    registry.register(CreateToolTool(store=store, registry=registry))

    with pytest.raises(ToolError):
        await CreateToolTool(store=store, registry=registry).execute(
            Action(
                tool="create_tool",
                args={"name": "create_tool", "description": "x", "code": "pass"},
            ),
            _ctx(deps),
        )


async def test_create_tool_rejects_empty_code(store, registry, tmp_path):
    deps = _FakeDeps(config=_FakeConfig(data_dir=tmp_path))
    tool = CreateToolTool(store=store, registry=registry)

    with pytest.raises(ToolError):
        await tool.execute(
            Action(tool="create_tool", args={"name": "valid_name", "description": "x", "code": "  "}),
            _ctx(deps),
        )


async def test_create_tool_rejects_oversized_code(store, registry, tmp_path):
    deps = _FakeDeps(config=_FakeConfig(data_dir=tmp_path))
    tool = CreateToolTool(store=store, registry=registry)

    with pytest.raises(ToolError):
        await tool.execute(
            Action(
                tool="create_tool",
                args={"name": "valid_name", "description": "x", "code": "x = 1\n" * 20_000},
            ),
            _ctx(deps),
        )


async def test_run_custom_tool_executes_script_and_returns_stdout(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = tmp_path / "echo_args.py"
    script.write_text(
        "import sys, json\n"
        "with open(sys.argv[1], encoding='utf-8') as fh:\n"
        "    args = json.load(fh)\n"
        "print('got', args.get('name'))\n",
        encoding="utf-8",
    )
    record = CustomToolRecord(
        name="echo_args", description="эхо", usage="", script_path=str(script), created_by="1001",
    )
    deps = _FakeDeps(config=_FakeConfig(data_dir=workspace))
    tool = RunCustomToolTool(record)

    result = await tool.execute(
        Action(tool="echo_args", args={"name": "мир"}), _ctx(deps)
    )

    assert result.ok
    assert "got мир" in result.detail


async def test_run_custom_tool_reports_failure_on_nonzero_exit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = tmp_path / "broken.py"
    script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    record = CustomToolRecord(
        name="broken", description="ломается", usage="", script_path=str(script), created_by="1001",
    )
    deps = _FakeDeps(config=_FakeConfig(data_dir=workspace))
    tool = RunCustomToolTool(record)

    result = await tool.execute(Action(tool="broken", args={}), _ctx(deps))

    assert not result.ok


async def test_run_custom_tool_is_risky():
    record = CustomToolRecord(
        name="x", description="x", usage="", script_path="x.py", created_by="1001",
    )
    assert RunCustomToolTool(record).risk == RiskTier.RISKY
