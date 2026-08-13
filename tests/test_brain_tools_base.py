# tests/test_brain_tools_base.py
from __future__ import annotations

from cortex.brain.risk import RiskTier
from cortex.brain.tools.base import BrainTool, BrainToolContext, BrainToolRegistry
from cortex.models import Action, ToolResult


class _Echo(BrainTool):
    name = "echo"
    description = "test tool"
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        return ToolResult.success(f"echo: {action.args.get('text')}")


class _Boom(BrainTool):
    name = "boom"
    description = "always fails"
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        raise RuntimeError("kaboom")


def _ctx() -> BrainToolContext:
    return BrainToolContext(deps=object(), chat_id=-100500, requester_id=1001)


async def test_dispatch_runs_registered_tool():
    registry = BrainToolRegistry()
    registry.register(_Echo())

    result = await registry.dispatch(Action(tool="echo", args={"text": "hi"}), _ctx())
    assert result.ok
    assert "hi" in result.summary


async def test_dispatch_unknown_tool_fails_gracefully():
    registry = BrainToolRegistry()
    result = await registry.dispatch(Action(tool="nope", args={}), _ctx())
    assert not result.ok
    assert "не существует" in result.summary


async def test_dispatch_catches_exceptions():
    registry = BrainToolRegistry()
    registry.register(_Boom())

    result = await registry.dispatch(Action(tool="boom", args={}), _ctx())
    assert not result.ok
    assert "boom" in result.summary


async def test_dispatch_turns_domain_errors_into_failure_not_crash():
    """RegistryError/WorkspaceError и родня (CortexError) — ожидаемый исход,
    не падение инструмента: тулзы вроде get_employee вызывают
    registry.require() напрямую и полагаются на то, что его сообщение дойдёт
    до чата как есть, а не утонет в трейсбеке."""
    from cortex.errors import RegistryError

    class _NotFound(BrainTool):
        name = "not_found"
        description = "raises a domain error"
        risk = RiskTier.SAFE

        async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
            raise RegistryError("Сотрудник 'Ghost' не найден")

    registry = BrainToolRegistry()
    registry.register(_NotFound())

    result = await registry.dispatch(Action(tool="not_found", args={}), _ctx())
    assert not result.ok
    assert "не найден" in result.summary


def test_risk_of_returns_declared_tier():
    registry = BrainToolRegistry()
    registry.register(_Boom())
    assert registry.risk_of("boom") is RiskTier.RISKY
    assert registry.risk_of("missing") is None


def test_docs_lists_registered_tools():
    registry = BrainToolRegistry()
    registry.register(_Echo())
    assert "echo" in registry.docs()
