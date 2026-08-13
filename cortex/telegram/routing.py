"""Маршрутизация сообщений корпоративного чата.

Задача модуля — превратить свободный текст «@Frontend_Dev почини хедер
#basehub_web» в тройку (сотрудник, проект, инструкция). Никакой работы
с сетью или файлами здесь нет, поэтому логика легко тестируется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import WorkspaceError
from ..models import Employee
from ..registry import EmployeeRegistry
from ..state import ChatState
from ..workspace import WorkspaceManager

#: @Frontend_Dev или @frontend_dev_bot
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{2,63})")
#: #basehub_web — явное указание проекта
_PROJECT_TAG_RE = re.compile(r"(?<![\w#])#([a-z][a-z0-9_\-]{1,47})", re.IGNORECASE)


@dataclass(slots=True)
class Routed:
    employee: Employee
    project: str
    instruction: str


class MentionRouter:
    """Разбирает адресацию: кому задача, в каком проекте, что сделать."""

    def __init__(
        self,
        registry: EmployeeRegistry,
        workspaces: WorkspaceManager,
        state: ChatState,
    ) -> None:
        self.registry = registry
        self.workspaces = workspaces
        self.state = state

    # ------------------------------------------------------------------
    def find_employee(self, text: str) -> tuple[Employee | None, str | None]:
        """Первый упомянутый сотрудник + сырой тег, которым его позвали."""
        for match in _MENTION_RE.finditer(text or ""):
            handle = match.group(1)
            employee = self.registry.get(handle)
            if employee is None:
                # Позвали по username бота: @frontend_dev_bot
                employee = next(
                    (
                        e
                        for e in self.registry.all()
                        if e.username and e.username.lower() == handle.lower()
                    ),
                    None,
                )
            if employee is not None and employee.active:
                return employee, match.group(0)
        return None, None

    # ------------------------------------------------------------------
    def resolve_project(self, text: str, chat_id: int, employee: Employee) -> str:
        """Приоритет: #тег в сообщении → активный проект чата → проект сотрудника."""
        for match in _PROJECT_TAG_RE.finditer(text or ""):
            candidate = self.workspaces.normalize(match.group(1))
            if self.workspaces.get(candidate):
                return candidate

        active = self.state.active_project(chat_id)
        if active and self.workspaces.get(active):
            return active

        if employee.default_project and self.workspaces.get(employee.default_project):
            return employee.default_project

        projects = self.workspaces.list()
        if len(projects) == 1:
            return projects[0].name

        raise WorkspaceError(
            "Непонятно, над каким проектом работать. Укажи тегом (#sports_api), "
            "либо закрепи проект за чатом: /use <проект>. "
            f"Доступны: {', '.join(p.name for p in projects) or 'ни одного — создай через /project new'}"
        )

    # ------------------------------------------------------------------
    def strip_mention(self, text: str, mention: str) -> str:
        cleaned = (text or "").replace(mention, " ", 1)
        cleaned = _PROJECT_TAG_RE.sub(" ", cleaned)
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    # ------------------------------------------------------------------
    def route(self, text: str, chat_id: int) -> Routed | None:
        employee, mention = self.find_employee(text)
        if employee is None or mention is None:
            return None

        project = self.resolve_project(text, chat_id, employee)
        instruction = self.strip_mention(text, mention)
        if not instruction:
            instruction = (
                "Тебя позвали без конкретной задачи. Коротко доложи, чем занимаешься "
                "и что видишь в проекте."
            )
        return Routed(employee=employee, project=project, instruction=instruction)
