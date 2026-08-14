# tests/test_custom_tool_store.py
from __future__ import annotations

import json

import pytest

from cortex.brain.tools.custom_store import CustomToolRecord, CustomToolStore


def _record(name="count_words") -> CustomToolRecord:
    return CustomToolRecord(
        name=name,
        description="Считает слова в файле",
        usage='{"tool": "count_words", "args": {"path": "notes.txt"}}',
        script_path=f"data/brain_tools/{name}.py",
        created_by="1001",
    )


def test_save_script_writes_file(tmp_path):
    store = CustomToolStore(
        scripts_dir=tmp_path / "brain_tools", registry_path=tmp_path / "custom_tools.json"
    )
    path = store.save_script("count_words", "print('hi')")

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "print('hi')"


async def test_add_persists_to_disk(tmp_path):
    store = CustomToolStore(
        scripts_dir=tmp_path / "brain_tools", registry_path=tmp_path / "custom_tools.json"
    )
    await store.add(_record())

    raw = json.loads((tmp_path / "custom_tools.json").read_text(encoding="utf-8"))
    assert raw["tools"][0]["name"] == "count_words"


async def test_state_survives_reload(tmp_path):
    scripts_dir = tmp_path / "brain_tools"
    registry_path = tmp_path / "custom_tools.json"
    store = CustomToolStore(scripts_dir=scripts_dir, registry_path=registry_path)
    await store.add(_record())

    reloaded = CustomToolStore(scripts_dir=scripts_dir, registry_path=registry_path)
    names = [r.name for r in reloaded.all()]
    assert names == ["count_words"]


@pytest.mark.parametrize("bad_root", ["[]", "123", "null", '"just a string"'])
def test_survives_non_object_root(tmp_path, bad_root):
    registry_path = tmp_path / "custom_tools.json"
    registry_path.write_text(bad_root, encoding="utf-8")
    store = CustomToolStore(scripts_dir=tmp_path / "brain_tools", registry_path=registry_path)
    assert store.all() == []
