# cortex/brain/risk.py
"""Риск-политика мозга Cortex: какие действия исполняются сразу, а какие
ждут подтверждения CEO кнопками в Telegram.

Порог — не бинарный переключатель, а настройка (brain.autonomy в
config.yaml), см. docs/superpowers/specs/2026-08-13-cortex-brain-design.md.
"""

from __future__ import annotations

from enum import Enum


class RiskTier(str, Enum):
    SAFE = "safe"
    NORMAL = "normal"
    RISKY = "risky"


class Autonomy(str, Enum):
    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    AUTONOMOUS = "autonomous"


#: Какие уровни риска исполняются БЕЗ подтверждения в каждом режиме.
_AUTO_EXECUTE: dict[Autonomy, frozenset[RiskTier]] = {
    Autonomy.CAUTIOUS: frozenset({RiskTier.SAFE}),
    Autonomy.BALANCED: frozenset({RiskTier.SAFE, RiskTier.NORMAL}),
    Autonomy.AUTONOMOUS: frozenset({RiskTier.SAFE, RiskTier.NORMAL, RiskTier.RISKY}),
}


def parse_autonomy(value: str) -> Autonomy:
    try:
        return Autonomy(value.strip().lower())
    except ValueError:
        return Autonomy.BALANCED


def requires_confirmation(risk: RiskTier, autonomy: Autonomy) -> bool:
    return risk not in _AUTO_EXECUTE[autonomy]


def resolve_risk(tool_name: str, default_risk: RiskTier, overrides: dict[str, str]) -> RiskTier:
    """Персональный override из config.yaml важнее риска по умолчанию у
    инструмента. Некорректное значение override молча игнорируется —
    подставлять случайный риск опаснее, чем упасть на дефолт."""
    raw = overrides.get(tool_name)
    if raw is None:
        return default_risk
    try:
        return RiskTier(raw.strip().lower())
    except ValueError:
        return default_risk
