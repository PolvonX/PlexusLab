"""Парсер <action> обязан переваривать неаккуратный вывод живых моделей."""

from __future__ import annotations

from cortex.tools.parser import extract_actions, strip_actions


def test_single_action():
    text = """Смотрю проект.
<action>
{"tool": "execute_command", "args": {"command": "git status"}}
</action>
Готово."""
    actions, errors = extract_actions(text)

    assert errors == []
    assert len(actions) == 1
    assert actions[0].tool == "execute_command"
    assert actions[0].args["command"] == "git status"


def test_several_actions_execute_in_order():
    text = """
<action>{"tool": "execute_command", "args": {"command": "npm ci"}}</action>
<action>{"tool": "send_file", "args": {"path": "dist/app.js"}}</action>
"""
    actions, errors = extract_actions(text)

    assert errors == []
    assert [a.tool for a in actions] == ["execute_command", "send_file"]


def test_json_wrapped_in_code_fence():
    text = """<action>
```json
{"tool": "send_file", "args": {"path": "report.pdf"}}
```
</action>"""
    actions, errors = extract_actions(text)

    assert errors == []
    assert actions[0].args["path"] == "report.pdf"


def test_trailing_comma_is_tolerated():
    text = '<action>{"tool": "execute_command", "args": {"command": "ls",},}</action>'
    actions, errors = extract_actions(text)

    assert errors == []
    assert actions[0].tool == "execute_command"


def test_flat_args_are_normalized():
    """Модель забыла обёртку args — аргументы всё равно должны собраться."""
    text = '<action>{"tool": "execute_command", "command": "pwd"}</action>'
    actions, _ = extract_actions(text)

    assert actions[0].args == {"command": "pwd"}


def test_action_key_synonym():
    text = '<action>{"action": "send_file", "params": {"path": "a.txt"}}</action>'
    actions, _ = extract_actions(text)

    assert actions[0].tool == "send_file"
    assert actions[0].args["path"] == "a.txt"


def test_broken_block_does_not_kill_valid_ones():
    text = """
<action>{сломанный json}</action>
<action>{"tool": "send_file", "args": {"path": "ok.txt"}}</action>
"""
    actions, errors = extract_actions(text)

    assert len(actions) == 1
    assert actions[0].args["path"] == "ok.txt"
    assert len(errors) == 1


def test_missing_tool_name_is_reported():
    text = '<action>{"args": {"command": "ls"}}</action>'
    actions, errors = extract_actions(text)

    assert actions == []
    assert "tool" in errors[0]


def test_strip_actions_leaves_chat_reply():
    text = """Починил хедер.

<action>{"tool": "execute_command", "args": {"command": "git add -A"}}</action>

Можно проверять."""
    assert strip_actions(text) == "Починил хедер.\n\nМожно проверять."


def test_plain_text_has_no_actions():
    actions, errors = extract_actions("Просто отвечаю текстом, ничего не делаю.")

    assert actions == []
    assert errors == []
