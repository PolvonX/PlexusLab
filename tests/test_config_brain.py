# tests/test_config_brain.py
from __future__ import annotations

from cortex.config import Config


def _cfg(tmp_path, secrets, brain: dict | None = None) -> Config:
    raw = {
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "brain": brain or {},
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def test_brain_defaults(tmp_path, secrets):
    cfg = _cfg(tmp_path, secrets)
    assert cfg.brain_autonomy == "balanced"
    assert cfg.brain_max_iterations == 5
    assert cfg.brain_risk_overrides == {}
    assert cfg.brain_model == "sonnet"


def test_brain_overrides(tmp_path, secrets):
    cfg = _cfg(
        tmp_path,
        secrets,
        {
            "autonomy": "cautious",
            "max_iterations": 3,
            "model": "opus",
            "risk_overrides": {"execute_command": "risky"},
        },
    )
    assert cfg.brain_autonomy == "cautious"
    assert cfg.brain_max_iterations == 3
    assert cfg.brain_model == "opus"
    assert cfg.brain_risk_overrides == {"execute_command": "risky"}


def test_brain_driver_is_independent_of_employee_driver(tmp_path, secrets):
    raw = {
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "agy",
            "drivers": {
                "agy": {"command": "agy -p {prompt}"},
                "claude": {"command": "claude -p {prompt}"},
            },
        },
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    assert cfg.runner_driver.name == "agy"
    assert cfg.brain_driver.name == "claude"
