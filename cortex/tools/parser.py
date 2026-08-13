"""Парсер блоков <action>{...}</action> из ответа сабагента.

Реальные LLM-агенты неаккуратны: заворачивают JSON в ```json, ставят
трейлинг-запятые, иногда шлют голый JSON без обёртки. Парсер терпит всё
перечисленное и говорит внятно, если не смог разобрать.
"""

from __future__ import annotations

import json
import re

from ..logging_setup import get_logger
from ..models import Action

log = get_logger("parser")

_ACTION_RE = re.compile(r"<action\s*>(.*?)</action\s*>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```(?:json|jsonc)?\s*(.*?)\s*```\s*$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

#: Синонимы ключа с именем инструмента — модели путают их постоянно.
_TOOL_KEYS = ("tool", "action", "name", "tool_name", "command_name")
#: Синонимы контейнера аргументов.
_ARG_KEYS = ("args", "arguments", "params", "parameters", "input")


class ActionParseError(ValueError):
    """Блок <action> найден, но внутри не JSON."""


def _clean(payload: str) -> str:
    payload = payload.strip()
    fenced = _FENCE_RE.match(payload)
    if fenced:
        payload = fenced.group(1).strip()
    return _TRAILING_COMMA_RE.sub(r"\1", payload)


def _normalize(data: dict, raw: str) -> Action:
    tool = None
    for key in _TOOL_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            tool = value.strip()
            break
    if not tool:
        raise ActionParseError(
            "в объекте нет поля 'tool' с именем инструмента"
        )

    args: dict = {}
    for key in _ARG_KEYS:
        value = data.get(key)
        if isinstance(value, dict):
            args = value
            break
    else:
        # Плоский формат: {"tool": "execute_command", "command": "ls"}
        args = {
            k: v
            for k, v in data.items()
            if k not in _TOOL_KEYS and k not in _ARG_KEYS
        }

    return Action(tool=tool, args=args, raw=raw)


def extract_actions(text: str) -> tuple[list[Action], list[str]]:
    """Вернуть (действия, ошибки разбора).

    Ошибки не выбрасываются: агент мог прислать три блока, из которых
    сломан один — остальные два должны выполниться.
    """
    if not text:
        return [], []

    actions: list[Action] = []
    errors: list[str] = []

    for match in _ACTION_RE.finditer(text):
        raw = match.group(1)
        payload = _clean(raw)
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            snippet = payload[:200].replace("\n", " ")
            errors.append(f"невалидный JSON ({exc.msg}) в блоке: {snippet}")
            log.warning("Не разобран <action>: %s", exc)
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"ожидался JSON-объект, получен {type(item).__name__}")
                continue
            try:
                actions.append(_normalize(item, raw))
            except ActionParseError as exc:
                errors.append(str(exc))

    return actions, errors


def strip_actions(text: str) -> str:
    """Убрать блоки действий — остаётся реплика для чата."""
    cleaned = _ACTION_RE.sub("", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
