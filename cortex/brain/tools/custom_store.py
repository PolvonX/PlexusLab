# cortex/brain/tools/custom_store.py
"""Персистентный реестр инструментов, которые мозг сам написал через
create_tool — переживает перезапуск: и .py-файл, и запись о нём (имя,
описание для промпта) должны быть восстановлены при следующем старте,
иначе мозг "забудет", что уже написал этот инструмент, и попытается
сделать это заново. Тот же паттерн, что у PendingActionStore/
PendingChoiceStore: tmp + os.replace под asyncio.Lock."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...models import utcnow_iso


@dataclass(slots=True)
class CustomToolRecord:
    name: str
    description: str
    usage: str
    script_path: str
    created_by: str
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "usage": self.usage,
            "script_path": self.script_path,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CustomToolRecord":
        return cls(
            name=raw["name"],
            description=raw.get("description", ""),
            usage=raw.get("usage", ""),
            script_path=raw["script_path"],
            created_by=raw.get("created_by", ""),
            created_at=raw.get("created_at") or utcnow_iso(),
        )


class CustomToolStore:
    """Хранит сгенерированные .py-файлы в scripts_dir, метаданные — в JSON
    рядом (registry_path)."""

    def __init__(self, *, scripts_dir: Path, registry_path: Path) -> None:
        self._scripts_dir = scripts_dir
        self._scripts_dir.mkdir(parents=True, exist_ok=True)
        self._path = registry_path
        self._records: dict[str, CustomToolRecord] = {}
        self._lock = asyncio.Lock()
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self._path.exists():
            self._records = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._records = {}
            return
        if not isinstance(raw, dict):
            self._records = {}
            return
        self._records = {
            entry["name"]: CustomToolRecord.from_dict(entry) for entry in raw.get("tools", [])
        }

    def all(self) -> list[CustomToolRecord]:
        return list(self._records.values())

    def save_script(self, name: str, code: str) -> Path:
        path = self._scripts_dir / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    async def add(self, record: CustomToolRecord) -> None:
        async with self._lock:
            self._records[record.name] = record
            self._write_unlocked()

    def _write_unlocked(self) -> None:
        payload = {"tools": [r.to_dict() for r in self._records.values()]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)
