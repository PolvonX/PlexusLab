"""Изоляция проектов — главный барьер между агентами."""

from __future__ import annotations

import pytest

from cortex.errors import WorkspaceError
from cortex.workspace import WorkspaceManager


@pytest.fixture()
def workspaces(tmp_path):
    return WorkspaceManager(tmp_path / "projects")


def test_create_and_list(workspaces):
    workspaces.create("sports_api", "API спортивного сервиса")
    workspaces.create("basehub_web")

    names = [p.name for p in workspaces.list()]
    assert names == ["basehub_web", "sports_api"]
    assert workspaces.require("sports_api").description == "API спортивного сервиса"


def test_duplicate_project_rejected(workspaces):
    workspaces.create("sports_api")
    with pytest.raises(WorkspaceError):
        workspaces.create("sports_api")


def test_invalid_name_rejected(workspaces):
    with pytest.raises(WorkspaceError):
        workspaces.create("../../etc")


def test_missing_project_reports_available(workspaces):
    workspaces.create("sports_api")
    with pytest.raises(WorkspaceError, match="sports_api"):
        workspaces.require("nope")


def test_path_stays_inside_project(workspaces):
    project = workspaces.create("sports_api")
    resolved = workspaces.resolve_path(project, "src/app.py")

    assert resolved.parent.parent == project.path.resolve()


def test_escape_attempt_blocked(workspaces):
    project = workspaces.create("sports_api")
    workspaces.create("basehub_web")

    with pytest.raises(WorkspaceError, match="выходит за пределы"):
        workspaces.resolve_path(project, "../basehub_web/secret.env")


def test_absolute_path_blocked(workspaces, tmp_path):
    project = workspaces.create("sports_api")

    with pytest.raises(WorkspaceError):
        workspaces.resolve_path(project, str(tmp_path / "outside.txt"))


def test_escape_allowed_when_explicitly_configured(workspaces):
    project = workspaces.create("sports_api")
    other = workspaces.create("basehub_web")

    resolved = workspaces.resolve_path(
        project, f"../{other.name}", allow_escape=True
    )
    assert resolved == other.path.resolve()


# --- подключение существующих папок (junction) ------------------------
def test_link_points_at_existing_folder(workspaces, tmp_path):
    real = tmp_path / "Basehub"
    (real / "src").mkdir(parents=True)
    (real / "src" / "app.ts").write_text("export const x = 1", encoding="utf-8")

    project = workspaces.link("basehub", real)

    assert project.linked
    assert project.real_path == real.resolve()
    assert (project.path / "src" / "app.ts").exists()
    assert [p.name for p in workspaces.list()] == ["basehub"]


def test_link_does_not_pollute_target(workspaces, tmp_path):
    """В чужой репозиторий Cortex служебных файлов не пишет."""
    real = tmp_path / "Basehub"
    real.mkdir()

    workspaces.link("basehub", real)

    assert list(real.iterdir()) == []


def test_unlink_keeps_target_intact(workspaces, tmp_path):
    """Самый важный тест: отключение не должно стирать реальный проект."""
    real = tmp_path / "Basehub"
    (real / "src").mkdir(parents=True)
    (real / "src" / "app.ts").write_text("важный код", encoding="utf-8")

    workspaces.link("basehub", real)
    returned = workspaces.unlink("basehub")

    assert returned == real.resolve()
    assert real.is_dir()
    assert (real / "src" / "app.ts").read_text(encoding="utf-8") == "важный код"
    assert workspaces.get("basehub") is None


def test_link_rejects_ancestor_of_projects_dir(workspaces, tmp_path):
    """Подключение родителя projects/ создало бы бесконечную рекурсию."""
    with pytest.raises(WorkspaceError, match="рекурсию"):
        workspaces.link("everything", tmp_path)


def test_link_rejects_missing_target(workspaces, tmp_path):
    with pytest.raises(WorkspaceError, match="недоступна"):
        workspaces.link("ghost", tmp_path / "no_such_folder")


def test_link_rejects_duplicate_name(workspaces, tmp_path):
    real = tmp_path / "Basehub"
    real.mkdir()
    workspaces.create("basehub")

    with pytest.raises(WorkspaceError, match="уже существует"):
        workspaces.link("basehub", real)


def test_sandbox_holds_inside_linked_project(workspaces, tmp_path):
    """Песочница путей должна работать и через junction."""
    real = tmp_path / "Basehub"
    (real / "src").mkdir(parents=True)
    (tmp_path / "Financier").mkdir()

    project = workspaces.link("basehub", real)

    inside = workspaces.resolve_path(project, "src/app.ts")
    assert inside == (real / "src" / "app.ts").resolve()

    with pytest.raises(WorkspaceError, match="выходит за пределы"):
        workspaces.resolve_path(project, "../Financier/secret.env")


def test_archive_refuses_linked_project(workspaces, tmp_path):
    """Архивирование junction утащило бы чужой проект — должно быть отказано."""
    real = tmp_path / "Basehub"
    real.mkdir()
    workspaces.link("basehub", real)

    with pytest.raises(WorkspaceError, match="unlink"):
        workspaces.archive("basehub", tmp_path / "archive")

    assert real.is_dir()


def test_unlink_refuses_own_workspace(workspaces):
    workspaces.create("sports_api")

    with pytest.raises(WorkspaceError, match="собственная"):
        workspaces.unlink("sports_api")


def test_tree_skips_noise(workspaces):
    project = workspaces.create("sports_api")
    (project.path / "node_modules").mkdir()
    (project.path / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
    (project.path / "src").mkdir()
    (project.path / "src" / "main.py").write_text("print(1)", encoding="utf-8")

    tree = workspaces.tree(project)

    assert "main.py" in tree
    assert "node_modules" not in tree
