# tests/test_formatting.py
"""Мозг обучен на Markdown и пишет **жирным**/`код`, а сообщения уходят с
parse_mode=HTML — без конвертации звёздочки долетают до CEO буквально
(живой инцидент, см. commit message)."""

from __future__ import annotations

from cortex.telegram.formatting import markdown_to_html


def test_bold_becomes_html_tag():
    assert markdown_to_html("это **важно** для тебя") == "это <b>важно</b> для тебя"


def test_inline_code_becomes_html_tag():
    assert markdown_to_html("вызови `hire_employee`") == "вызови <code>hire_employee</code>"


def test_single_asterisk_becomes_italic():
    assert markdown_to_html("это *подчёркнуто*") == "это <i>подчёркнуто</i>"


def test_plain_text_is_untouched():
    assert markdown_to_html("просто текст без разметки") == "просто текст без разметки"


def test_html_special_chars_are_escaped_before_conversion():
    assert markdown_to_html("if a < b and **c**") == "if a &lt; b and <b>c</b>"


def test_underscored_identifiers_are_not_treated_as_italic():
    assert markdown_to_html("вызови self_execute_task") == "вызови self_execute_task"


def test_bold_does_not_span_across_lines():
    """** без пары на той же строке — не разметка, оставляем как есть,
    иначе один непарный ** случайно склеит два несвязанных абзаца в тег."""
    text = "**начало\nконец**"
    assert markdown_to_html(text) == "**начало\nконец**"


def test_agent_fallback_notice_names_the_fallback_driver():
    from cortex.telegram import formatting as fmt

    text = fmt.agent_fallback_notice(
        agent="Frontend_Dev", driver_name="claude_haiku", attempt=1, total=1
    )
    assert "Frontend_Dev" in text
    assert "claude_haiku" in text
