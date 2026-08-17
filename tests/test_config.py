# tests/test_config.py
from __future__ import annotations

from cortex.config import Config


def _raw(**overrides):
    base = {
        "company": {"name": "Plexus Lab", "ceo_name": "Abdulloh Abbosov"},
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "agy",
            "drivers": {
                "agy": {"command": "agy -p {prompt}"},
                "claude_haiku": {"command": "claude.cmd -p --model haiku {session_flag}"},
            },
        },
        "brain": {},
    }
    base.update(overrides)
    return base


def test_runner_fallback_drivers_empty_by_default(tmp_path, secrets):
    cfg = Config(root=tmp_path, raw=_raw(), secrets=secrets)
    assert cfg.runner_fallback_drivers == []


def test_runner_fallback_drivers_resolves_named_drivers(tmp_path, secrets):
    raw = _raw()
    raw["agent_runner"]["fallback_drivers"] = ["claude_haiku"]
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    fallbacks = cfg.runner_fallback_drivers
    assert len(fallbacks) == 1
    assert fallbacks[0].name == "claude_haiku"
    assert "haiku" in fallbacks[0].command


def test_brain_fallback_drivers_empty_by_default(tmp_path, secrets):
    cfg = Config(root=tmp_path, raw=_raw(), secrets=secrets)
    assert cfg.brain_fallback_drivers == []


def test_brain_fallback_drivers_resolves_named_drivers(tmp_path, secrets):
    raw = _raw()
    raw["brain"]["fallback_drivers"] = ["claude_haiku"]
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    fallbacks = cfg.brain_fallback_drivers
    assert len(fallbacks) == 1
    assert fallbacks[0].name == "claude_haiku"


def test_unknown_fallback_driver_name_raises_config_error(tmp_path, secrets):
    from cortex.errors import ConfigError

    raw = _raw()
    raw["agent_runner"]["fallback_drivers"] = ["does_not_exist"]
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    try:
        cfg.runner_fallback_drivers
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_vision_driver_defaults_to_claude_vision(tmp_path, secrets):
    raw = _raw()
    raw["agent_runner"]["drivers"]["claude_vision"] = {
        "command": 'claude.cmd -p --input-format stream-json --output-format stream-json --tools ""'
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    driver = cfg.vision_driver

    assert driver.name == "claude_vision"
    assert "stream-json" in driver.command


def test_vision_driver_env_override(tmp_path, secrets, monkeypatch):
    raw = _raw()
    raw["agent_runner"]["drivers"]["mock_vision"] = {
        "command": 'python "{root}/scripts/mock_vision.py" --prompt-file "{prompt_file}"'
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    monkeypatch.setenv("PLEXUS_VISION_DRIVER", "mock_vision")

    driver = cfg.vision_driver

    assert driver.name == "mock_vision"
