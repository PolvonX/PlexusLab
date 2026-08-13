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
