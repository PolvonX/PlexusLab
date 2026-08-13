# tests/test_mock_claude.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MOCK_CLAUDE = Path(__file__).resolve().parent.parent / "scripts" / "mock_claude.py"


def _run(prompt: str, tmp_path: Path) -> str:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(MOCK_CLAUDE), "--prompt-file", str(prompt_file)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def test_staff_question_triggers_list_staff_action(tmp_path):
    out = _run("## Сообщение CEO\n\nкто у нас в штате?", tmp_path)
    assert "<action>" in out
    assert "list_staff" in out


def test_followup_after_tool_result_ends_without_new_action(tmp_path):
    out = _run("Результат list_staff (успех): В штате 1 сотрудник(ов)", tmp_path)
    assert "<action>" not in out


def test_unrecognized_message_is_plain_text(tmp_path):
    out = _run("## Сообщение CEO\n\nпривет", tmp_path)
    assert "<action>" not in out
