"""Плумбинг запуска сабагента — на mock-драйвере, без установленного agy."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cortex.config import Config
from cortex.errors import AgentRunError
from cortex.runtime import AgentRunner
from cortex.tools.parser import extract_actions, strip_actions

MOCK = Path(__file__).resolve().parent.parent / "scripts" / "mock_agy.py"


def _config_with(tmp_path, secrets, command: str, timeout: int = 60) -> Config:
    raw = {
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "mock",
            "drivers": {"mock": {"command": command}},
            "timeout_seconds": timeout,
        },
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


async def test_agent_output_is_captured(tmp_path, secrets):
    cfg = _config_with(
        tmp_path, secrets, f'"{sys.executable}" "{MOCK}" --prompt-file {{prompt_file}} --delay 0'
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="## Задача\n\nсоздай файл README",
        workspace=workspace,
        agent="Frontend_Dev",
        project="sports_api",
    )

    assert result.returncode == 0
    assert "Frontend_Dev" in result.stdout
    assert "sports_api" in result.stdout


async def test_actions_survive_the_round_trip(tmp_path, secrets):
    """Вывод mock-агента должен разбираться штатным парсером."""
    cfg = _config_with(
        tmp_path, secrets, f'"{sys.executable}" "{MOCK}" --prompt-file {{prompt_file}} --delay 0'
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="## Задача\n\nзапусти git status",
        workspace=workspace,
        agent="Cortex",
        project="sports_api",
    )

    actions, errors = extract_actions(result.stdout)
    assert errors == []
    assert actions and actions[0].tool == "execute_command"
    assert strip_actions(result.stdout).strip()


async def test_crash_is_reported_with_stderr(tmp_path, secrets):
    cfg = _config_with(
        tmp_path,
        secrets,
        f'"{sys.executable}" "{MOCK}" --prompt-file {{prompt_file}} --delay 0 --fail',
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRunner(cfg).run(
            prompt="что угодно", workspace=workspace, agent="QA", project="sports_api"
        )

    assert exc_info.value.returncode == 1
    assert "mock-агент упал" in exc_info.value.stderr


async def test_timeout_kills_the_process(tmp_path, secrets):
    cfg = _config_with(
        tmp_path,
        secrets,
        f'"{sys.executable}" "{MOCK}" --prompt-file {{prompt_file}} --delay 30',
        timeout=2,
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    with pytest.raises(AgentRunError, match="не уложился"):
        await AgentRunner(cfg).run(
            prompt="долгая задача", workspace=workspace, agent="QA", project="sports_api"
        )


async def test_missing_binary_gives_readable_error(tmp_path, secrets):
    cfg = _config_with(tmp_path, secrets, "agy_which_does_not_exist --prompt-file {prompt_file}")
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    with pytest.raises(AgentRunError, match="не найден"):
        await AgentRunner(cfg).run(
            prompt="x", workspace=workspace, agent="QA", project="sports_api"
        )


async def test_prompt_file_is_cleaned_up(tmp_path, secrets):
    cfg = _config_with(
        tmp_path, secrets, f'"{sys.executable}" "{MOCK}" --prompt-file {{prompt_file}} --delay 0'
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    await AgentRunner(cfg).run(
        prompt="x", workspace=workspace, agent="QA", project="sports_api"
    )

    assert list((cfg.data_dir / "prompts_tmp").glob("*.md")) == []


async def test_system_prompt_is_passed_through_safely(tmp_path, secrets):
    """system_prompt reaches the child process even with spaces/quotes, same
    trick as {prompt}: it must not be shell-tokenized."""
    echo_argv = tmp_path / "echo_argv.py"
    echo_argv.write_text(
        "import sys\n"
        "print('ARGC=' + str(len(sys.argv)))\n"
        "print('SYS=' + sys.argv[sys.argv.index('--system-prompt') + 1])\n",
        encoding="utf-8",
    )
    cfg = _config_with(
        tmp_path,
        secrets,
        f'"{sys.executable}" "{echo_argv}" --system-prompt "{{system_prompt}}"',
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="x",
        workspace=workspace,
        agent="Cortex",
        project="sports_api",
        system_prompt='Ты Cortex. Говори "коротко и по делу".\nВторая строка.',
    )

    assert 'SYS=Ты Cortex. Говори "коротко и по делу".' in result.stdout


async def test_session_flag_is_appended_as_plain_tokens(tmp_path, secrets):
    echo_argv = tmp_path / "echo_argv.py"
    echo_argv.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n", encoding="utf-8")
    cfg = _config_with(
        tmp_path, secrets, f'"{sys.executable}" "{echo_argv}" {{session_flag}}'
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="x",
        workspace=workspace,
        agent="Cortex",
        project="sports_api",
        session_flag="--resume 11111111-1111-1111-1111-111111111111",
    )

    assert "--resume 11111111-1111-1111-1111-111111111111" in result.stdout


async def test_explicit_driver_overrides_the_configured_default(tmp_path, secrets):
    """The brain passes its own driver (Config.brain_driver) — the default
    agent_runner.driver must not be consulted when one is given explicitly."""
    echo_argv = tmp_path / "echo_argv.py"
    echo_argv.write_text("import sys\nprint('OTHER_DRIVER_RAN')\n", encoding="utf-8")

    cfg = _config_with(tmp_path, secrets, "this-command-does-not-exist")
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    from cortex.config import RunnerDriver

    override = RunnerDriver(
        name="other", command=f'"{sys.executable}" "{echo_argv}"', prompt_via_stdin=False, env={}
    )

    result = await AgentRunner(cfg).run(
        prompt="x", workspace=workspace, agent="Cortex", project="sports_api", driver=override
    )

    assert "OTHER_DRIVER_RAN" in result.stdout


