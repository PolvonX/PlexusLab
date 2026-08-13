"""Динамический HR: реестр сотрудников Plexus Lab.

Единственный владелец employees_registry.json. Все изменения идут через
этот класс, атомарной записью (tmp + os.replace), под asyncio-локом —
иначе два одновременных найма затрут друг друга.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Iterable

from .errors import RegistryError
from .logging_setup import get_logger
from .models import NAME_RE, Employee

log = get_logger("registry")

_SCHEMA_VERSION = 1


class EmployeeRegistry:
    """Реестр сотрудников с горячей перезагрузкой и атомарной записью."""

    def __init__(self, path: Path, prompts_dir: Path) -> None:
        self.path = path
        self.prompts_dir = prompts_dir
        self._employees: dict[str, Employee] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Синхронная загрузка при старте (и после ручной правки файла)."""
        if not self.path.exists():
            log.warning("Реестр %s не найден — создаю пустой", self.path)
            self._employees = {}
            self._write_unlocked()
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError(
                f"employees_registry.json повреждён ({exc}). Cortex не станет его "
                "перезаписывать — почини файл вручную."
            ) from exc

        entries: Iterable[dict] = raw.get("employees", []) if isinstance(raw, dict) else raw
        employees: dict[str, Employee] = {}
        for entry in entries:
            try:
                employee = Employee.from_dict(entry)
            except KeyError as exc:
                raise RegistryError(f"В реестре запись без поля {exc}") from exc
            employees[employee.name.lower()] = employee

        self._employees = employees
        log.info("Реестр загружен: %d сотрудник(ов)", len(employees))

    # ------------------------------------------------------------------
    def all(self, *, include_inactive: bool = False) -> list[Employee]:
        items = self._employees.values()
        if not include_inactive:
            items = [e for e in items if e.active]
        return sorted(items, key=lambda e: e.name.lower())

    def get(self, name: str) -> Employee | None:
        return self._employees.get(name.strip().lstrip("@").lower())

    def require(self, name: str) -> Employee:
        employee = self.get(name)
        if employee is None:
            known = ", ".join(e.mention for e in self.all()) or "— никого"
            raise RegistryError(f"Сотрудник '{name}' не найден. В штате: {known}")
        return employee

    def by_bot_id(self, bot_id: int) -> Employee | None:
        for employee in self._employees.values():
            if employee.bot_id == bot_id:
                return employee
        return None

    def bot_ids(self) -> set[int]:
        return {e.bot_id for e in self._employees.values() if e.bot_id}

    def names(self) -> list[str]:
        return [e.name for e in self.all()]

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def token_in_use(self, token: str) -> Employee | None:
        for employee in self._employees.values():
            if employee.token == token:
                return employee
        return None

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------
    async def add(self, employee: Employee) -> Employee:
        """Нанять сотрудника. Валидирует уникальность имени и токена."""
        if not NAME_RE.match(employee.name):
            raise RegistryError(
                f"Имя '{employee.name}' некорректно. Разрешены латиница, цифры и '_', "
                "длина 3–32, первая буква — латинская. Пример: Frontend_Dev"
            )

        async with self._lock:
            if employee.name.lower() in self._employees:
                raise RegistryError(f"Сотрудник {employee.mention} уже в штате")
            clash = self.token_in_use(employee.token)
            if clash:
                raise RegistryError(
                    f"Этот токен уже закреплён за {clash.mention}. Один бот — один сотрудник."
                )
            self._employees[employee.name.lower()] = employee
            self._write_unlocked()

        log.info("Нанят %s (%s), токен %s", employee.name, employee.role, employee.token_hint)
        return employee

    async def update(self, name: str, **changes) -> Employee:
        async with self._lock:
            employee = self.require(name)
            for key, value in changes.items():
                if not hasattr(employee, key):
                    raise RegistryError(f"У сотрудника нет поля '{key}'")
                setattr(employee, key, value)
            self._write_unlocked()
            return employee

    async def fire(self, name: str, *, hard: bool = False) -> Employee:
        """Мягкое увольнение (active=False) или полное удаление из реестра."""
        async with self._lock:
            employee = self.require(name)
            if hard:
                self._employees.pop(employee.name.lower(), None)
            else:
                employee.active = False
            self._write_unlocked()
        log.info("Уволен %s (hard=%s)", employee.name, hard)
        return employee

    async def reload(self) -> None:
        async with self._lock:
            self.load()

    # ------------------------------------------------------------------
    def _write_unlocked(self) -> None:
        payload = {
            "version": _SCHEMA_VERSION,
            "employees": [e.to_dict() for e in self.all(include_inactive=True)],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if self.path.exists():  # страховка от потери токенов
            shutil.copy2(self.path, self.path.with_suffix(".json.bak"))

        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # Промпты (должностные инструкции)
    # ------------------------------------------------------------------
    def prompt_file(self, employee: Employee) -> Path:
        path = Path(employee.prompt_path)
        if not path.is_absolute():
            # prompt_path хранится относительно корня проекта
            path = (self.prompts_dir.parent / path).resolve()
        return path

    def read_prompt(self, employee: Employee) -> str:
        path = self.prompt_file(employee)
        if not path.exists():
            log.warning("Промпт %s отсутствует для %s", path, employee.name)
            return (
                f"Ты — {employee.title}, {employee.role} в Plexus Lab. "
                "Твоя должностная инструкция утеряна — работай по здравому смыслу "
                "и попроси CEO её восстановить."
            )
        return path.read_text(encoding="utf-8")

    def write_prompt(self, employee: Employee, content: str, *, backup_dir: Path) -> Path:
        """Перезаписать инструкцию с бэкапом предыдущей версии."""
        path = self.prompt_file(employee)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime, timezone

            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, backup_dir / f"{employee.name}-{stamp}.md")

        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        log.info("Промпт %s обновлён (%d символов)", employee.name, len(content))
        return path
