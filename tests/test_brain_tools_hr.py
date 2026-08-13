# tests/test_brain_tools_hr.py
from __future__ import annotations

from dataclasses import dataclass

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.hr import FireEmployeeTool, HireEmployeeTool, WriteJobDescriptionTool
from cortex.hr import HRService
from cortex.models import Action
from cortex.telegram.bot_pool import BotPool


class _FakeBot:
    async def get_me(self):
        @dataclass
        class Me:
            id: int = 777
            username: str = "frontend_dev_bot"

        return Me()

    async def session_close(self):
        return None


class _FakeBotPool(BotPool):
    def __init__(self, registry):
        super().__init__(registry)

    @staticmethod
    def _make_bot(token: str):
        return _FakeBot()


@dataclass
class _FakeDeps:
    registry: object
    hr: object
    gateway: object = None


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=-100500, requester_id=1001)


async def test_hire_employee_creates_and_verifies(config, registry, tmp_path):
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await HireEmployeeTool().execute(
        Action(
            tool="hire_employee",
            args={"name": "Frontend_Dev", "role": "Senior Frontend Engineer", "token": "1:AAA"},
        ),
        _ctx(deps),
    )

    assert result.ok
    assert registry.require("Frontend_Dev").role == "Senior Frontend Engineer"


async def test_hire_employee_missing_args_is_a_failure(config, registry):
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await HireEmployeeTool().execute(
        Action(tool="hire_employee", args={"name": "Frontend_Dev"}), _ctx(deps)
    )
    assert not result.ok


async def test_write_job_description_replace(config, registry, frontend):
    await registry.add(frontend)
    deps = _FakeDeps(registry=registry, hr=None)

    content = "# Frontend Dev\n\n" + "x" * 100
    result = await WriteJobDescriptionTool().execute(
        Action(
            tool="write_job_description",
            args={"name": "Frontend_Dev", "mode": "replace", "content": content},
        ),
        _ctx(deps),
    )
    assert result.ok
    assert registry.read_prompt(frontend).startswith("# Frontend Dev")


async def test_write_job_description_append(config, registry, frontend):
    await registry.add(frontend)
    registry.write_prompt(frontend, "# Версия 1\n" + "x" * 100, backup_dir=config.data_dir / "b")
    deps = _FakeDeps(registry=registry, hr=None)

    result = await WriteJobDescriptionTool().execute(
        Action(
            tool="write_job_description",
            args={"name": "Frontend_Dev", "mode": "append", "content": "## Урок\nПиши тесты."},
        ),
        _ctx(deps),
    )
    assert result.ok
    updated = registry.read_prompt(frontend)
    assert "# Версия 1" in updated
    assert "## Урок" in updated


async def test_write_job_description_too_short_is_rejected(config, registry, frontend):
    await registry.add(frontend)
    deps = _FakeDeps(registry=registry, hr=None)

    result = await WriteJobDescriptionTool().execute(
        Action(
            tool="write_job_description",
            args={"name": "Frontend_Dev", "mode": "replace", "content": "too short"},
        ),
        _ctx(deps),
    )
    assert not result.ok


async def test_fire_employee_soft(config, registry, frontend):
    await registry.add(frontend)
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await FireEmployeeTool().execute(
        Action(tool="fire_employee", args={"name": "Frontend_Dev"}), _ctx(deps)
    )
    assert result.ok
    assert "Frontend_Dev" not in [e.name for e in registry.all()]
    assert "Frontend_Dev" in [e.name for e in registry.all(include_inactive=True)]


async def test_fire_employee_hard(config, registry, frontend):
    await registry.add(frontend)
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await FireEmployeeTool().execute(
        Action(tool="fire_employee", args={"name": "Frontend_Dev", "hard": True}), _ctx(deps)
    )
    assert result.ok
    assert registry.get("Frontend_Dev") is None
