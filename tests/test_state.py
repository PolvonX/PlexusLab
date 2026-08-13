"""ChatState — устойчивость к повреждённому state.json."""

from __future__ import annotations

import json

from cortex.state import ChatState


def test_survives_missing_file(tmp_path):
    state = ChatState(tmp_path)
    assert state.active_project(123) is None


def test_survives_invalid_json(tmp_path):
    (tmp_path / "state.json").write_text("{not valid json", encoding="utf-8")
    state = ChatState(tmp_path)
    assert state.active_project(123) is None


def test_survives_json_array_as_root(tmp_path):
    """Живая находка ревью: json.loads("[]") не кидает JSONDecodeError, но
    .setdefault() на списке падает с AttributeError — раньше это ронял
    старт всего Cortex, если state.json оказывался повреждён именно так."""
    (tmp_path / "state.json").write_text("[]", encoding="utf-8")
    state = ChatState(tmp_path)
    assert state.active_project(123) is None


def test_survives_json_primitive_as_root(tmp_path):
    (tmp_path / "state.json").write_text("123", encoding="utf-8")
    state = ChatState(tmp_path)
    assert state.active_project(123) is None


async def test_still_works_normally_after_recovering_from_bad_json(tmp_path):
    (tmp_path / "state.json").write_text("null", encoding="utf-8")
    state = ChatState(tmp_path)
    await state.set_active_project(123, "sports_api")
    assert state.active_project(123) == "sports_api"

    reloaded = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert reloaded["chat_projects"]["123"] == "sports_api"
