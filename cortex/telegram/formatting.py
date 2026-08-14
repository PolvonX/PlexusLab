"""Форматирование сообщений для Telegram.

Везде используется parse_mode=HTML: у MarkdownV2 нужно экранировать
полтора десятка символов, и любой ответ агента со скобкой ломает отправку.
"""

from __future__ import annotations

import html
import re
from typing import Iterable

from ..errors import AgentRunError, CortexError
from ..models import ToolResult

#: Telegram режет на 4096; берём запас на теги.
DEFAULT_LIMIT = 3800


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


_CODE_RE = re.compile(r"`([^`\n]+?)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def markdown_to_html(text: str) -> str:
    """Мозг (claude) обучен на Markdown и пишет **жирным**/`код`, а
    сообщения от его лица уходят с parse_mode=HTML (см. модульный докстринг)
    — без конвертации звёздочки и бэктики долетают до CEO буквально (живой
    инцидент). Не полный парсер — только то, что реально встречается в
    ответах мозга. `.` в regex не матчит перевод строки, поэтому непарный
    маркер не склеивает соседние абзацы в один тег."""
    result = esc(text)
    result = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", result)
    result = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", result)
    result = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", result)
    return result


def code_block(text: str, limit: int = 2500) -> str:
    text = str(text).strip()
    if len(text) > limit:
        text = text[:limit] + "\n… (обрезано)"
    return f"<pre>{esc(text)}</pre>"


def split_message(text: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Разрезать длинный ответ по границам абзацев/строк, не по символам."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remainder = text
    while len(remainder) > limit:
        window = remainder[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remainder[:cut].strip())
        remainder = remainder[cut:].strip()
    if remainder:
        chunks.append(remainder)
    return [c for c in chunks if c]


# ----------------------------------------------------------------------
# Отчёты
# ----------------------------------------------------------------------
def tool_report(results: Iterable[tuple[str, ToolResult]]) -> str:
    """Сводка выполненных действий — идёт отдельным сообщением после ответа."""
    lines: list[str] = []
    for tool_name, result in results:
        icon = "✅" if result.ok else "⚠️"
        lines.append(f"{icon} <b>{esc(tool_name)}</b> — {esc(result.summary)}")
        if result.detail:
            lines.append(code_block(result.detail, limit=1200))
    if not lines:
        return ""
    return "🛠 <b>Действия</b>\n\n" + "\n".join(lines)


def agent_error_report(
    *,
    agent: str,
    project: str,
    error: AgentRunError,
    stderr_limit: int = 1500,
) -> str:
    """Красивый отчёт о падении `agy` — вместо голого трейсбека в чат."""
    parts = [
        f"💥 <b>{esc(agent)}</b> не справился с задачей",
        "",
        f"<b>Проект:</b> {esc(project)}",
        f"<b>Причина:</b> {esc(str(error))}",
    ]
    if error.returncode is not None:
        parts.append(f"<b>Код возврата:</b> <code>{error.returncode}</code>")
    if error.duration:
        parts.append(f"<b>Время до отказа:</b> {error.duration:.1f} с")
    if error.command:
        parts += ["", "<b>Команда:</b>", code_block(error.command, limit=400)]

    stderr = (error.stderr or "").strip()
    if stderr:
        tail = stderr[-stderr_limit:]
        parts += ["", "<b>stderr:</b>", code_block(tail, limit=stderr_limit)]

    parts += ["", "<i>Cortex оставил задачу незакрытой. Что делаем, шеф?</i>"]
    return "\n".join(parts)


def custom_tool_giveup_report(*, tool_name: str, goal: str, attempts: list[str]) -> str:
    """Self-healing loop сдался после лимита попыток — CEO должен увидеть,
    какую задачу мозг решал и что конкретно пробовал на каждом шаге, а не
    голое "не получилось"."""
    parts = [f"❌ <b>Инструмент «{esc(tool_name)}» не заработал за {len(attempts)} попытки</b>"]
    if goal:
        parts += ["", f"<b>Исходная задача:</b> {esc(goal)}"]
    parts += ["", "<b>Что пробовал:</b>"]
    for index, detail in enumerate(attempts, start=1):
        parts += ["", f"<b>Попытка {index}:</b>", code_block(detail, limit=600)]
    parts += ["", "<i>Дальше без подсказки не пойдёт — что делаем?</i>"]
    return "\n".join(parts)


def error_report(error: Exception, *, context: str = "") -> str:
    title = getattr(error, "title", None) or "Сбой"
    if isinstance(error, CortexError):
        body = str(error)
    else:
        body = f"{type(error).__name__}: {error}"
    parts = [f"⚠️ <b>{esc(title)}</b>", "", esc(body)]
    if context:
        parts += ["", f"<i>{esc(context)}</i>"]
    return "\n".join(parts)


def employee_card(employee, *, tools: list[str]) -> str:
    status = "🟢 в строю" if employee.active else "⚪️ уволен"
    lines = [
        f"<b>{esc(employee.title)}</b> ({esc(employee.mention)})",
        f"Должность: {esc(employee.role)}",
        f"Статус: {status}",
    ]
    if employee.username:
        lines.append(f"Бот: @{esc(employee.username)}")
    if employee.default_project:
        lines.append(f"Проект по умолчанию: <code>{esc(employee.default_project)}</code>")
    lines.append(f"Инструменты: {esc(', '.join(tools) or 'нет')}")
    lines.append(f"Инструкция: <code>{esc(employee.prompt_path)}</code>")
    lines.append(f"Нанят: {esc(employee.hired_at)}")
    return "\n".join(lines)
