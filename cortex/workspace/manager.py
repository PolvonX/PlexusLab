"""Multi-Project Workspace.

Каждый проект Plexus Lab — отдельная папка под projects/. Агент получает
cwd ровно своего проекта и физически не видит чужой код. Все пути,
пришедшие от агента, проходят через resolve_path(): выход за пределы
песочницы блокируется до того, как что-то будет исполнено.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..errors import WorkspaceError
from ..logging_setup import get_logger

log = get_logger("workspace")

PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9_\-]{1,47}$")

#: Папки, которые не показываем агенту в дереве файлов. Каждая строка
#: здесь — сэкономленный контекст: агенту нужен код проекта, а не
#: служебные каталоги инструментов, которыми проект обслуживают.
_TREE_SKIP = {
    # системы контроля версий и сборка
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "env", "dist", "build", "out", ".next", ".nuxt", "target", ".gradle",
    # кэши инструментов
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".turbo", ".parcel-cache",
    ".cache", "coverage", ".nyc_output",
    # локальные хранилища данных: у J.A.R.V.I.S это гигабайты бинарей
    "chroma_db", ".chroma", ".ollama",
    # IDE и ИИ-ассистенты: у Basehub этим забита половина верхнего уровня
    ".idea", ".vscode", ".vs", ".claude", ".superpowers", ".cursor",
    ".worktrees", ".antigravity", ".windsurf",
}

_MANIFEST = ".plexus.json"


@dataclass(slots=True)
class Project:
    name: str
    path: Path
    description: str = ""
    created_at: str = ""

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    @property
    def real_path(self) -> Path:
        """Куда путь ведёт на самом деле (для junction — в целевую папку)."""
        try:
            return self.path.resolve()
        except OSError:
            return self.path

    @property
    def linked(self) -> bool:
        """True, если это junction на папку вне projects/, а не своя среда."""
        return self.real_path != self.path


class WorkspaceManager:
    """Создаёт, перечисляет и охраняет рабочие среды проектов."""

    def __init__(self, projects_dir: Path) -> None:
        self.root = projects_dir
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    @staticmethod
    def normalize(name: str) -> str:
        slug = name.strip().lower().replace(" ", "_")
        slug = re.sub(r"[^a-z0-9_\-]", "", slug)
        return slug

    def validate_name(self, name: str) -> str:
        # Путь в имени проекта — либо опечатка, либо попытка вылезти из
        # песочницы. Молча вычищать такое опаснее, чем отказать.
        raw = (name or "").strip()
        if any(token in raw for token in ("/", "\\", "..", ":")):
            raise WorkspaceError(
                f"Имя проекта '{name}' содержит путь. Проект — это имя, а не путь: "
                "sports_api, basehub_web."
            )

        slug = self.normalize(raw)
        if not PROJECT_NAME_RE.match(slug):
            raise WorkspaceError(
                f"Некорректное имя проекта '{name}'. Нужны строчные латинские буквы, "
                "цифры, '_' и '-', длина 2–48. Пример: sports_api"
            )
        return slug

    # ------------------------------------------------------------------
    def list(self) -> list[Project]:
        projects: list[Project] = []
        for entry in sorted(self.root.iterdir() if self.root.exists() else []):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            projects.append(self._read_project(entry))
        return projects

    def get(self, name: str) -> Project | None:
        slug = self.normalize(name)
        path = self.root / slug
        if not path.is_dir():
            return None
        return self._read_project(path)

    def require(self, name: str) -> Project:
        project = self.get(name)
        if project is None:
            known = ", ".join(p.name for p in self.list()) or "— пока ни одного"
            raise WorkspaceError(
                f"Проект '{name}' не существует. Доступны: {known}. "
                "Создать: /project new <имя>"
            )
        return project

    def _read_project(self, path: Path) -> Project:
        manifest = path / _MANIFEST
        description, created = "", ""
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                description = data.get("description", "")
                created = data.get("created_at", "")
            except json.JSONDecodeError:
                log.warning("Манифест %s повреждён — игнорирую", manifest)
        return Project(name=path.name, path=path, description=description, created_at=created)

    # ------------------------------------------------------------------
    def create(self, name: str, description: str = "") -> Project:
        slug = self.validate_name(name)
        path = self.root / slug
        if path.exists():
            raise WorkspaceError(f"Проект '{slug}' уже существует")

        path.mkdir(parents=True)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (path / _MANIFEST).write_text(
            json.dumps(
                {"name": slug, "description": description, "created_at": created_at},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (path / "README.md").write_text(
            f"# {slug}\n\n{description or 'Проект Plexus Lab.'}\n\n"
            "Эта папка — изолированная рабочая среда. Агенты Plexus Lab работают\n"
            "здесь и не имеют доступа к другим проектам.\n",
            encoding="utf-8",
        )
        log.info("Создан проект %s", slug)
        return Project(name=slug, path=path, description=description, created_at=created_at)

    def link(self, name: str, target: str | Path, description: str = "") -> Project:
        """Подключить существующую папку как проект через directory junction.

        Файлы остаются на месте — ни копирования, ни переезда. Манифест в
        целевую папку НЕ пишется: это чужой репозиторий, засорять его
        служебными файлами Cortex не должен.
        """
        slug = self.validate_name(name)
        path = self.root / slug
        if path.exists():
            raise WorkspaceError(f"Проект '{slug}' уже существует")

        try:
            target_path = Path(target).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(f"Папка '{target}' недоступна: {exc}") from exc

        if not target_path.is_dir():
            raise WorkspaceError(f"'{target}' — не папка")

        # Подключить предка projects/ значит получить папку внутри самой
        # себя: обход дерева уйдёт в бесконечность.
        root_resolved = self.root.resolve()
        if root_resolved == target_path or root_resolved.is_relative_to(target_path):
            raise WorkspaceError(
                f"'{target_path}' содержит саму папку проектов Plexus Lab — "
                "подключение создало бы рекурсию. Укажи конкретный проект, "
                "а не каталог целиком."
            )

        self._make_junction(path, target_path)
        log.info("Проект %s подключён junction → %s", slug, target_path)
        return Project(
            name=slug,
            path=path,
            description=description or f"Подключён из {target_path}",
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def unlink(self, name: str) -> Path:
        """Отключить junction. Целевая папка остаётся нетронутой."""
        project = self.require(name)
        if not project.linked:
            raise WorkspaceError(
                f"'{project.name}' — собственная рабочая среда, а не подключённая "
                "папка. Для неё используй архивирование, а не отключение."
            )

        target = project.real_path
        # ТОЛЬКО rmdir: он снимает саму точку соединения. rmtree прошёл бы
        # сквозь junction и снёс реальный проект.
        try:
            os.rmdir(project.path)
        except OSError as exc:
            raise WorkspaceError(
                f"Не удалось снять подключение '{project.name}': {exc}"
            ) from exc

        log.info("Проект %s отключён, папка %s не тронута", project.name, target)
        return target

    @staticmethod
    def _make_junction(link: Path, target: Path) -> None:
        """Directory junction на Windows, symlink на остальных ОС.

        Junction выбран намеренно: в отличие от symlink он не требует прав
        администратора или включённого developer mode.
        """
        if sys.platform == "win32":
            result = subprocess.run(
                ["cmd", "/d", "/s", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise WorkspaceError(f"mklink не смог создать junction: {detail}")
            return

        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            raise WorkspaceError(f"Не удалось создать symlink: {exc}") from exc

    def archive(self, name: str, archive_dir: Path) -> Path:
        """Убрать проект из активных, не удаляя данные."""
        project = self.require(name)
        if project.linked:
            raise WorkspaceError(
                f"'{project.name}' — подключённая папка ({project.real_path}). "
                "Архивирование переместило бы чужой проект. Используй "
                f"/project unlink {project.name}."
            )
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = archive_dir / f"{project.name}-{stamp}"
        shutil.move(str(project.path), str(target))
        log.info("Проект %s заархивирован в %s", name, target)
        return target

    # ------------------------------------------------------------------
    # Песочница путей
    # ------------------------------------------------------------------
    def resolve_path(self, project: Project, relative: str, *, allow_escape: bool = False) -> Path:
        """Превратить путь от агента в абсолютный, не выпуская из проекта."""
        candidate = Path(relative)
        base = project.path.resolve()
        target = candidate if candidate.is_absolute() else (base / candidate)

        try:
            resolved = target.resolve()
        except OSError as exc:
            raise WorkspaceError(f"Некорректный путь '{relative}': {exc}") from exc

        if allow_escape:
            return resolved

        if resolved != base and base not in resolved.parents:
            raise WorkspaceError(
                f"Путь '{relative}' выходит за пределы проекта '{project.name}'. "
                "Агенты Plexus Lab не ходят в чужие рабочие среды."
            )
        return resolved

    # ------------------------------------------------------------------
    def tree(self, project: Project, max_entries: int = 200, max_depth: int = 3) -> str:
        """Компактное дерево файлов для промпта агента."""
        lines: list[str] = []
        base = project.path

        def walk(directory: Path, prefix: str, depth: int) -> None:
            if depth > max_depth or len(lines) >= max_entries:
                return
            try:
                entries = sorted(
                    directory.iterdir(),
                    key=lambda p: (p.is_file(), p.name.lower()),
                )
            except OSError:
                return
            for entry in entries:
                if len(lines) >= max_entries:
                    lines.append(f"{prefix}… (обрезано)")
                    return
                if entry.name in _TREE_SKIP or entry.name == _MANIFEST:
                    continue
                if entry.is_dir():
                    lines.append(f"{prefix}{entry.name}/")
                    walk(entry, prefix + "  ", depth + 1)
                else:
                    lines.append(f"{prefix}{entry.name}")

        walk(base, "", 1)
        return "\n".join(lines) if lines else "(пустой проект)"
