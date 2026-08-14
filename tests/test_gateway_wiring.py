# tests/test_gateway_wiring.py
"""handlers.py/hiring.py/synapse_handlers.py стали историей — фиксируем,
что Gateway больше на них не ссылается и что модули реально удалены."""

from __future__ import annotations

import importlib

import pytest

from cortex.telegram import gateway


def test_deleted_modules_are_gone():
    for name in ("cortex.telegram.handlers", "cortex.telegram.hiring", "cortex.telegram.synapse_handlers"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_gateway_imports_only_brain_router():
    source = importlib.import_module("cortex.telegram.gateway").__file__
    text = open(source, encoding="utf-8").read()
    assert "build_brain_router" in text
    assert "build_command_router" not in text
    assert "build_hiring_router" not in text
    assert "build_synapse_router" not in text


def test_polling_subscribes_to_callback_query():
    """Живой инцидент: allowed_updates перечислял только "message" и
    "edited_message" — Telegram никогда не доставлял боту нажатия кнопок
    (ни risk-подтверждения, ни квизы), и клиент вечно крутил "часики" на
    кнопке, потому что бот даже не видел callback, не то что отвечал на
    него через answerCallbackQuery."""
    source = importlib.import_module("cortex.telegram.gateway").__file__
    text = open(source, encoding="utf-8").read()
    assert "callback_query" in text
