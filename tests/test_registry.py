"""Реестр сотрудников: уникальность, персистентность, инструкции."""

from __future__ import annotations

import json

import pytest

from cortex.errors import RegistryError
from cortex.models import Employee
from cortex.registry import EmployeeRegistry


async def test_hire_persists_to_disk(config, registry, frontend):
    await registry.add(frontend)

    raw = json.loads(config.registry_path.read_text(encoding="utf-8"))
    assert raw["employees"][0]["name"] == "Frontend_Dev"

    reloaded = EmployeeRegistry(config.registry_path, config.prompts_dir)
    reloaded.load()
    assert reloaded.require("Frontend_Dev").role == "Senior Frontend Engineer"


async def test_lookup_is_case_insensitive_and_ignores_at(registry, frontend):
    await registry.add(frontend)

    assert registry.get("frontend_dev") is not None
    assert registry.get("@Frontend_Dev") is not None


async def test_duplicate_name_rejected(registry, frontend):
    await registry.add(frontend)

    twin = Employee(
        name="Frontend_Dev", role="Другой", token="333:CCC", prompt_path="p.md"
    )
    with pytest.raises(RegistryError, match="уже в штате"):
        await registry.add(twin)


async def test_duplicate_token_rejected(registry, frontend):
    await registry.add(frontend)

    other = Employee(
        name="Backend_Dev", role="Backend", token=frontend.token, prompt_path="p.md"
    )
    with pytest.raises(RegistryError, match="токен"):
        await registry.add(other)


async def test_invalid_name_rejected(registry):
    bad = Employee(name="2fast", role="X", token="1:A", prompt_path="p.md")

    with pytest.raises(RegistryError, match="некорректно"):
        await registry.add(bad)


async def test_soft_fire_keeps_record(registry, frontend):
    await registry.add(frontend)
    await registry.fire(frontend.name)

    assert registry.get(frontend.name) is not None
    assert frontend.name not in [e.name for e in registry.all()]
    assert frontend.name in [e.name for e in registry.all(include_inactive=True)]


async def test_hard_fire_removes_record(registry, frontend):
    await registry.add(frontend)
    await registry.fire(frontend.name, hard=True)

    assert registry.get(frontend.name) is None


async def test_prompt_write_and_backup(config, registry, frontend):
    await registry.add(frontend)
    backups = config.data_dir / "prompt_backups"

    registry.write_prompt(frontend, "# Версия 1\n" + "x" * 100, backup_dir=backups)
    registry.write_prompt(frontend, "# Версия 2\n" + "y" * 100, backup_dir=backups)

    assert registry.read_prompt(frontend).startswith("# Версия 2")
    assert len(list(backups.glob("Frontend_Dev-*.md"))) == 1


def test_corrupted_registry_refuses_to_load(config):
    config.registry_path.write_text("{ это не json", encoding="utf-8")
    broken = EmployeeRegistry(config.registry_path, config.prompts_dir)

    with pytest.raises(RegistryError, match="повреждён"):
        broken.load()


def test_missing_prompt_falls_back(registry, frontend):
    """Потеря файла инструкции не должна ронять задачу."""
    text = registry.read_prompt(frontend)

    assert "Frontend Dev" in text
