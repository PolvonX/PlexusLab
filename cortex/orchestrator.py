"""Ядро Cortex: полный цикл обработки задачи.

    тег в чате → контекст → agy → <action> → инструменты → ответ в чат

Модуль сознательно не знает, откуда пришла задача (группа, личка, крон)
и кто её принёс. Ему дают AgentTask — он возвращает результат в чат от
лица нужного сотрудника.
"""

from __future__ import annotations

import asyncio
import uuid

from .config import Config
from .context import ChatHistory, PromptBuilder
from .errors import AgentRunError, CortexError
from .logging_setup import get_logger
from .models import AgentTask, ChatMessage, Employee, ToolResult
from .registry import EmployeeRegistry
from .runtime import AgentRunner, TaskScheduler
from .runtime.queue import TaskInfo
from .telegram.bot_pool import BotPool
from .telegram import formatting as fmt
from .tools import ToolRegistry, extract_actions, strip_actions
from .tools.base import ToolContext
from .workspace import WorkspaceManager

log = get_logger("orchestrator")


class Orchestrator:
    """Дирижёр: собирает контекст, зовёт сабагента, исполняет его волю."""

    def __init__(
        self,
        *,
        config: Config,
        registry: EmployeeRegistry,
        workspaces: WorkspaceManager,
        history: ChatHistory,
        runner: AgentRunner,
        scheduler: TaskScheduler,
        tools: ToolRegistry,
        bots: BotPool,
    ) -> None:
        self.config = config
        self.registry = registry
        self.workspaces = workspaces
        self.history = history
        self.runner = runner
        self.scheduler = scheduler
        self.tools = tools
        self.bots = bots
        self.prompts = PromptBuilder(config, registry, workspaces)

    # ------------------------------------------------------------------
    def new_task(
        self,
        *,
        employee: Employee,
        project_name: str,
        instruction: str,
        chat_id: int,
        message_id: int,
        requester: str,
    ) -> AgentTask:
        return AgentTask(
            employee=employee,
            project=project_name,
            instruction=instruction,
            chat_id=chat_id,
            message_id=message_id,
            requester=requester,
            task_id=uuid.uuid4().hex[:8],
        )

    # ------------------------------------------------------------------
    async def dispatch(self, task: AgentTask, *, requester_id: int) -> None:
        """Поставить задачу в очередь. Ошибки уходят в чат, не в трейсбек."""
        info = TaskInfo(
            task_id=task.task_id,
            agent=task.employee.name,
            project=task.project,
            instruction=task.instruction[:200],
        )
        try:
            await self.scheduler.submit(
                info, lambda: self._execute(task, requester_id=requester_id)
            )
        except AgentRunError as exc:
            await self._report_agent_failure(task, exc)
        except CortexError as exc:
            await self.bots.say(
                task.employee,
                task.chat_id,
                fmt.error_report(exc, context=f"задача {task.task_id}"),
                reply_to=task.message_id,
            )
        except Exception as exc:  # noqa: BLE001 — последний рубеж
            log.exception("Задача %s развалилась", task.task_id)
            await self.bots.say(
                task.employee,
                task.chat_id,
                fmt.error_report(exc, context=f"задача {task.task_id}"),
                reply_to=task.message_id,
            )

    # ------------------------------------------------------------------
    async def _execute(self, task: AgentTask, *, requester_id: int) -> None:
        employee = task.employee
        project = self.workspaces.require(task.project)

        history_block = self.history.render(
            task.chat_id,
            limit=self.config.history_messages,
            budget=self.config.history_chars_budget,
        )
        prompt = self.prompts.build(
            task,
            project=project,
            history_block=history_block,
            tools_doc=self.tools.docs_for(employee),
        )

        log.info(
            "Задача %s: %s → %s (%d символов промпта)",
            task.task_id, employee.name, project.name, len(prompt),
        )

        typing = asyncio.create_task(self._keep_typing(employee, task.chat_id))
        try:
            result = await self.runner.run(
                prompt=prompt,
                workspace=project.path,
                agent=employee.name,
                project=project.name,
                timeout=self.config.runner_timeout,
            )
        finally:
            typing.cancel()

        await self._deliver(task, project_name=project.name, raw_output=result.stdout,
                            stderr=result.stderr, requester_id=requester_id)

    # ------------------------------------------------------------------
    async def _deliver(
        self,
        task: AgentTask,
        *,
        project_name: str,
        raw_output: str,
        stderr: str = "",
        requester_id: int,
    ) -> None:
        """Разобрать ответ агента: реплика в чат + исполнение действий."""
        employee = task.employee
        actions, parse_errors = extract_actions(raw_output)
        reply = strip_actions(raw_output)

        if reply:
            await self.bots.say(
                employee, task.chat_id, fmt.markdown_to_html(reply), reply_to=task.message_id
            )
            self.history.add(
                ChatMessage(
                    chat_id=task.chat_id,
                    message_id=task.message_id,
                    author=employee.name,
                    text=reply,
                    is_agent=True,
                )
            )
        elif not actions:
            # Известный кейс agy: тул потребовал разрешение, в headless-
            # режиме подтвердить его некому — agy молча завершается кодом 0
            # с пустым stdout, а причину пишет только в stderr (живой
            # инцидент). Без этого CEO видел голое "🤷" без единой зацепки.
            detail = stderr.strip()
            text = "🤷 Агент отработал, но не сказал ни слова и не выполнил действий."
            if detail:
                text += "\n\n" + fmt.code_block(detail, limit=800)
            await self.bots.say(employee, task.chat_id, text, reply_to=task.message_id)

        if parse_errors:
            await self.bots.say(
                employee,
                task.chat_id,
                "⚠️ <b>Cortex не разобрал часть блоков &lt;action&gt;</b>\n\n"
                + "\n".join(f"• {fmt.esc(err)}" for err in parse_errors),
                silent=True,
            )

        if not actions:
            return

        results = await self._run_actions(
            task, actions, project_name=project_name, requester_id=requester_id
        )
        report = fmt.tool_report(results)
        if report:
            await self.bots.say(employee, task.chat_id, report, silent=True)

    # ------------------------------------------------------------------
    async def _run_actions(
        self,
        task: AgentTask,
        actions: list,
        *,
        project_name: str,
        requester_id: int,
    ) -> list[tuple[str, ToolResult]]:
        project = self.workspaces.require(project_name)
        bot = await self.bots.get(task.employee)

        ctx = ToolContext(
            employee=task.employee,
            project=project,
            chat_id=task.chat_id,
            message_id=task.message_id,
            bot=bot,
            config=self.config,
            registry=self.registry,
            workspaces=self.workspaces,
            requester_id=requester_id,
        )

        results: list[tuple[str, ToolResult]] = []
        for action in actions:
            log.info(
                "Задача %s: %s выполняет %s", task.task_id, task.employee.name, action.tool
            )
            result = await self.tools.dispatch(action, ctx)
            results.append((action.tool, result))
            if not result.ok:
                # Дальнейшие действия обычно опираются на предыдущие —
                # продолжать после провала опаснее, чем остановиться.
                log.warning(
                    "Задача %s: %s провалился, остальные действия отменены",
                    task.task_id, action.tool,
                )
                break
        return results

    # ------------------------------------------------------------------
    async def _keep_typing(self, employee: Employee, chat_id: int) -> None:
        """Пока agy думает, в чате видно «печатает…»."""
        interval = self.config.typing_interval
        try:
            while True:
                await self.bots.typing(employee, chat_id)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _report_agent_failure(self, task: AgentTask, exc: AgentRunError) -> None:
        log.error("Задача %s: agy упал — %s", task.task_id, exc)
        await self.bots.say(
            task.employee,
            task.chat_id,
            fmt.agent_error_report(
                agent=task.employee.title,
                project=task.project,
                error=exc,
                stderr_limit=self.config.stderr_report_chars,
            ),
            reply_to=task.message_id,
        )
