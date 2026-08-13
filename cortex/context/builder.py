"""Сборка финального промпта для процесса `agy`.

Порядок блоков важен: инструкция → компания → инструменты → проект →
история → задача. Задача идёт последней, чтобы не утонуть в контексте.
"""

from __future__ import annotations

from ..config import Config
from ..models import AgentTask
from ..registry import EmployeeRegistry
from ..workspace import Project, WorkspaceManager

_ACTION_CONTRACT = """\
## Как влиять на реальный мир

Ты не чат-бот, ты инженер Plexus Lab. Чтобы что-то сделать, вставь в свой
ответ блок действия. Cortex распарсит его и выполнит на сервере:

<action>
{"tool": "execute_command", "args": {"command": "git status"}}
</action>

Правила:
1. Внутри <action> — строго один валидный JSON-объект: {"tool": "...", "args": {...}}.
2. Блоков может быть несколько — они выполняются сверху вниз, по порядку.
3. Текст вне блоков <action> уходит в корпоративный чат как твоя реплика.
   Пиши коротко и по делу: это рабочая переписка, а не отчёт на 2 страницы.
4. Если действие не требуется — просто ответь текстом.
5. Все относительные пути считаются от корня ТВОЕГО проекта. Выйти за его
   пределы нельзя — Cortex заблокирует такую попытку.

### Доступные тебе инструменты
{tools}
"""


class PromptBuilder:
    """Превращает AgentTask в единый текстовый промпт для CLI-агента."""

    def __init__(
        self,
        config: Config,
        registry: EmployeeRegistry,
        workspaces: WorkspaceManager,
    ) -> None:
        self.config = config
        self.registry = registry
        self.workspaces = workspaces

    # ------------------------------------------------------------------
    def build(
        self,
        task: AgentTask,
        *,
        project: Project,
        history_block: str,
        tools_doc: str,
    ) -> str:
        employee = task.employee
        system_prompt = self.registry.read_prompt(employee).strip()

        blocks: list[str] = [
            system_prompt,
            "",
            "---",
            "",
            self._company_block(employee.name),
            "",
            _ACTION_CONTRACT.replace("{tools}", tools_doc.strip()),
            "",
            self._project_block(project),
            "",
            "## Последние сообщения корпоративного чата",
            "",
            history_block.strip(),
            "",
            "---",
            "",
            "## Задача",
            "",
            f"Поставил: {task.requester}",
            f"Проект: {project.name}",
            "",
            task.instruction.strip(),
            "",
            "Выполни задачу и ответь так, как ответил бы коллега в рабочем чате.",
        ]
        return "\n".join(blocks)

    # ------------------------------------------------------------------
    def _company_block(self, employee_name: str) -> str:
        return (
            f"## Где ты работаешь\n\n"
            f"Компания: {self.config.company_name} — "
            f"{self.config.section('company').get('tagline', 'R&D-центр')}.\n"
            f"CEO: {self.config.ceo_name} — последнее слово всегда за ним.\n"
            f"Оркестратор: {self.config.orchestrator_name} — он вызвал тебя, "
            f"он же исполнит твои действия и отнесёт ответ в чат.\n"
            f"Твой тег в чате: @{employee_name}.\n"
            f"Коллеги: "
            + (
                ", ".join(
                    f"@{e.name} ({e.role})"
                    for e in self.registry.all()
                    if e.name != employee_name
                )
                or "пока только ты"
            )
        )

    def _project_block(self, project: Project) -> str:
        lines = [
            "## Твоя рабочая среда",
            "",
            f"Проект: {project.name}",
            f"Корень: {project.path}",
        ]
        if project.description:
            lines.append(f"Описание: {project.description}")

        if self.config.include_workspace_tree:
            tree = self.workspaces.tree(
                project,
                max_entries=self.config.workspace_tree_max_entries,
            )
            lines += ["", "Файлы проекта:", "```", tree, "```"]

        lines += [
            "",
            "Ты видишь только этот проект. Код других проектов Plexus Lab тебе "
            "недоступен — так задумано.",
        ]
        return "\n".join(lines)
