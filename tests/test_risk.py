# tests/test_risk.py
from __future__ import annotations

import pytest

from cortex.brain.risk import Autonomy, RiskTier, requires_confirmation, resolve_risk


@pytest.mark.parametrize(
    "autonomy,risk,expected",
    [
        (Autonomy.CAUTIOUS, RiskTier.SAFE, False),
        (Autonomy.CAUTIOUS, RiskTier.NORMAL, True),
        (Autonomy.CAUTIOUS, RiskTier.RISKY, True),
        (Autonomy.BALANCED, RiskTier.SAFE, False),
        (Autonomy.BALANCED, RiskTier.NORMAL, False),
        (Autonomy.BALANCED, RiskTier.RISKY, True),
        (Autonomy.AUTONOMOUS, RiskTier.SAFE, False),
        (Autonomy.AUTONOMOUS, RiskTier.NORMAL, False),
        (Autonomy.AUTONOMOUS, RiskTier.RISKY, False),
    ],
)
def test_requires_confirmation_matrix(autonomy, risk, expected):
    assert requires_confirmation(risk, autonomy) is expected


def test_resolve_risk_uses_default_without_override():
    assert resolve_risk("hire_employee", RiskTier.NORMAL, {}) is RiskTier.NORMAL


def test_resolve_risk_applies_override():
    overrides = {"execute_command": "risky"}
    assert resolve_risk("execute_command", RiskTier.NORMAL, overrides) is RiskTier.RISKY


def test_resolve_risk_ignores_invalid_override():
    overrides = {"execute_command": "not_a_tier"}
    assert resolve_risk("execute_command", RiskTier.NORMAL, overrides) is RiskTier.NORMAL
