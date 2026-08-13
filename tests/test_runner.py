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


async def test_cp866_stderr_is_decoded_readably_not_as_mojibake(tmp_path, secrets):
    """Живой инцидент: claude.cmd/Windows иногда пишет диагностику в OEM
    cp866, а не UTF-8. Наивный decode("utf-8", errors="replace") на таких
    байтах не падает — он молча превращает их в нечитаемые кракозябры
    (валидные, но бессмысленные не-ASCII символы), потому что cp866-байты
    случайно складываются в валидные многобайтовые UTF-8 последовательности.
    Раннер должен распознать это и перекодировать как cp866."""
    script = tmp_path / "cp866_stderr.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.buffer.write('Файл не найден'.encode('cp866'))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    cfg = _config_with(tmp_path, secrets, f'"{sys.executable}" "{script}"')
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRunner(cfg).run(
            prompt="x", workspace=workspace, agent="QA", project="sports_api"
        )

    assert "Файл не найден" in exc_info.value.stderr


async def test_legitimate_non_cyrillic_output_is_not_treated_as_garbled(tmp_path, secrets):
    """Ревью нашло реальный риск ложного срабатывания: детектор кракозябр
    раньше судил по доле кириллицы/ASCII среди букв, а у мозга и сотрудников
    в выводе законно бывает китайский (web_research), эмодзи, математические
    символы и т.п. — такой текст ошибочно перекодировался бы в cp866 и
    портился. Детектор должен полагаться только на настоящие U+FFFD от
    decode(errors="replace"), а не на состав алфавита."""
    script = tmp_path / "cjk_stdout.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write('已完成任务 🎉 α+β=γ'.encode('utf-8'))\n",
        encoding="utf-8",
    )
    cfg = _config_with(tmp_path, secrets, f'"{sys.executable}" "{script}"')
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="x", workspace=workspace, agent="QA", project="sports_api"
    )

    assert result.stdout == "已完成任务 🎉 α+β=γ"


async def test_prompt_temp_files_are_cleaned_up_even_when_argv_build_fails(tmp_path, secrets):
    """Ревью нашло реальный баг: prompt_file/system_prompt_file писались на
    диск ДО входа в try/finally, так что если _build_argv() падает (лимит
    командной строки), temp-файлы с реальным содержимым промпта/системного
    промпта — историей чата и т.п. — оставались в data/prompts_tmp/ навсегда."""
    cfg = _config_with(tmp_path, secrets, '"{prompt}" fixed-tail-that-does-not-shrink')
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    with pytest.raises(AgentRunError, match="не помещается в командную строку"):
        await AgentRunner(cfg).run(
            prompt="x" * 40_000, workspace=workspace, agent="QA", project="sports_api"
        )

    assert list((cfg.data_dir / "prompts_tmp").glob("*")) == []


async def test_system_prompt_file_is_written_passed_and_cleaned_up(tmp_path, secrets):
    """--system-prompt-file (claude.cmd driver) reads the system prompt from
    a temp file instead of argv — needed so it never counts against the
    cmd.exe command-line ceiling (see test below)."""
    echo_argv = tmp_path / "echo_argv.py"
    echo_argv.write_text(
        "import sys\n"
        "path = sys.argv[sys.argv.index('--system-prompt-file') + 1]\n"
        "print('CONTENT=' + open(path, encoding='utf-8').read())\n"
        "print('PATH=' + path)\n",
        encoding="utf-8",
    )
    cfg = _config_with(
        tmp_path, secrets, f'"{sys.executable}" "{echo_argv}" --system-prompt-file "{{system_prompt_file}}"'
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="x", workspace=workspace, agent="Cortex", project="sports_api",
        system_prompt="Ты Cortex. Строка первая.\nСтрока вторая.",
    )

    lines = result.stdout.splitlines()
    assert lines[0] == "CONTENT=Ты Cortex. Строка первая."
    assert lines[1] == "Строка вторая."
    written_path = Path(lines[2].removeprefix("PATH="))
    assert not written_path.exists()  # подчищено в finally


async def test_large_prompt_and_system_prompt_never_touch_argv(tmp_path, secrets):
    """Живой инцидент: prompt (~5.9к симв.) + system_prompt (~2.6к симв.)
    вместе перевалили за реальный потолок cmd.exe для .cmd-обёрток (~8191)
    и claude.cmd падал с "Слишком длинная командная строка". С
    prompt_via_stdin + --system-prompt-file ни один из них больше не
    участвует в argv, так что даже полностью нереалистично большие значения
    (в разы больше живого инцидента) не должны представлять никакой
    проблемы, ведь argv остаётся крошечным независимо от их размера."""
    echo_stdin = tmp_path / "echo_stdin.py"
    echo_stdin.write_text(
        "import sys\n"
        "sp = sys.argv[sys.argv.index('--system-prompt-file') + 1]\n"
        "print('PROMPT_LEN=' + str(len(sys.stdin.read())))\n"
        "print('SYS_LEN=' + str(len(open(sp, encoding='utf-8').read())))\n",
        encoding="utf-8",
    )
    cfg = _config_with(
        tmp_path,
        secrets,
        f'"{sys.executable}" "{echo_stdin}" --system-prompt-file "{{system_prompt_file}}"',
    )
    cfg.raw["agent_runner"]["drivers"]["mock"]["prompt_via_stdin"] = True
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    big_prompt = "п" * 20_000
    big_system_prompt = "с" * 20_000

    result = await AgentRunner(cfg).run(
        prompt=big_prompt, workspace=workspace, agent="Cortex", project="sports_api",
        system_prompt=big_system_prompt,
    )

    assert f"PROMPT_LEN={len(big_prompt)}" in result.stdout
    assert f"SYS_LEN={len(big_system_prompt)}" in result.stdout


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


