# tests/test_sandbox_executor.py
"""Тесты для SandboxExecutor."""
from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from cortex.runtime.sandbox import SandboxExecutor


def _write_script(tmp_path: Path, code: str) -> str:
    """Записать Python-скрипт во временную директорию и вернуть путь."""
    script = tmp_path / "script.py"
    script.write_text(textwrap.dedent(code), encoding="utf-8")
    return str(script)


def _write_args(tmp_path: Path, args: dict) -> str:
    import json
    args_file = tmp_path / "args.json"
    args_file.write_text(json.dumps(args), encoding="utf-8")
    return str(args_file)


@pytest.mark.asyncio
async def test_success(tmp_path):
    script = _write_script(tmp_path, """\
        import sys, json
        args = json.load(open(sys.argv[1]))
        print("hello", args.get("name", "world"))
    """)
    args = _write_args(tmp_path, {"name": "cortex"})
    executor = SandboxExecutor()
    result = await executor.execute(script, args, log_tag="test")
    assert result.ok
    assert "hello cortex" in result.detail


@pytest.mark.asyncio
async def test_nonzero_exit(tmp_path):
    script = _write_script(tmp_path, """\
        import sys
        sys.exit(1)
    """)
    args = _write_args(tmp_path, {})
    executor = SandboxExecutor()
    result = await executor.execute(script, args, log_tag="test")
    assert not result.ok
    assert "1" in result.summary  # returncode в summary


@pytest.mark.asyncio
async def test_timeout(tmp_path):
    script = _write_script(tmp_path, """\
        import time
        time.sleep(60)
    """)
    args = _write_args(tmp_path, {})
    executor = SandboxExecutor(default_timeout=1)
    result = await executor.execute(script, args, log_tag="test")
    assert not result.ok
    assert "снят" in result.summary


@pytest.mark.asyncio
async def test_output_limit(tmp_path):
    """Превышение max_output_chars → обрезка + failure."""
    script = _write_script(tmp_path, """\
        print("X" * 10000)
    """)
    args = _write_args(tmp_path, {})
    executor = SandboxExecutor(max_output_chars=100)
    result = await executor.execute(script, args, log_tag="test")
    assert not result.ok
    assert "лимит" in result.summary or "лимит" in (result.detail or "")


@pytest.mark.asyncio
async def test_cwd_isolation(tmp_path):
    """Скрипт запускается в изолированной tmp cwd, а не в рабочей директории."""
    script = _write_script(tmp_path, """\
        import os
        print(os.getcwd())
    """)
    args = _write_args(tmp_path, {})
    executor = SandboxExecutor()
    result = await executor.execute(script, args, log_tag="test")
    assert result.ok
    # cwd скрипта — не директория где лежит скрипт (tmp_path)
    assert str(tmp_path) != result.detail.strip()


@pytest.mark.asyncio
async def test_empty_output(tmp_path):
    """Пустой stdout — не ошибка, возвращается '(пустой вывод)'."""
    script = _write_script(tmp_path, "pass")
    args = _write_args(tmp_path, {})
    executor = SandboxExecutor()
    result = await executor.execute(script, args, log_tag="test")
    assert result.ok
    assert "пустой" in result.detail
