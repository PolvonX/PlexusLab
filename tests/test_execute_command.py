# tests/test_execute_command.py
"""Юнит-покрытие общего раннера консольных команд (cortex/tools/shell.py) —
используется и employee execute_command, и его brain-версией (Task 12)."""

from __future__ import annotations

import re
import sys

import pytest

from cortex.errors import ToolError
from cortex.tools.shell import assert_command_allowed, resolve_timeout, run_shell_command

_BLOCKLIST = [re.compile(r"(?i)\brm\s+-rf\s+/")]


def test_blocklist_rejects_matching_command():
    with pytest.raises(ToolError, match="заблокирована"):
        assert_command_allowed("rm -rf /", _BLOCKLIST)


def test_blocklist_allows_safe_command():
    assert_command_allowed("git status", _BLOCKLIST)  # no raise


def test_resolve_timeout_clamps_to_max():
    assert resolve_timeout(99999, max_timeout=600) == 600


def test_resolve_timeout_falls_back_on_garbage():
    assert resolve_timeout("not-a-number", max_timeout=600, default=180) == 180


async def test_run_shell_command_success(tmp_path):
    result = await run_shell_command(
        "echo plexus", cwd=tmp_path, timeout=30, blocklist=[], log_tag="test"
    )
    assert result.ok
    assert "plexus" in result.detail


async def test_run_shell_command_nonzero_exit_is_failure_not_exception(tmp_path):
    cmd = "exit 3" if sys.platform == "win32" else "python -c \"import sys; sys.exit(3)\""
    result = await run_shell_command(
        cmd, cwd=tmp_path, timeout=30, blocklist=[], log_tag="test",
    )
    assert not result.ok
    assert "3" in result.summary


async def test_run_shell_command_timeout_is_reported(tmp_path):
    cmd = "ping -n 6 127.0.0.1 > nul" if sys.platform == "win32" else 'python -c "import time; time.sleep(5)"'
    result = await run_shell_command(
        cmd, cwd=tmp_path, timeout=1, blocklist=[], log_tag="test",
    )
    assert not result.ok
    assert "не уложилась" in result.summary
