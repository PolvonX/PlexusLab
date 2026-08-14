"""Сборка приложения Plexus Lab.

Единственное место, где компоненты знакомятся друг с другом. Всё
остальное принимает зависимости снаружи и потому тестируется по частям.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from .agents import SynapseService
from .brain.agent import BrainAgent
from .brain.choices import PendingChoiceStore
from .brain.context import BrainPromptBuilder
from .brain.pending import PendingActionStore
from .brain.session import BrainSession
from .brain.tools.base import BrainToolRegistry
from .brain.tools.custom import CreateToolTool, RunCustomToolTool
from .brain.tools.custom_store import CustomToolStore
from .brain.tools.hr import FireEmployeeTool, HireEmployeeTool, WriteJobDescriptionTool
from .brain.tools.interactive import SendButtonsTool
from .brain.tools.projects import (
    ArchiveProjectTool,
    CreateProjectTool,
    LinkProjectTool,
    SetChatProjectTool,
    UnlinkProjectTool,
)
from .brain.tools.read import GetEmployeeTool, GetStatusTool, ListProjectsTool, ListStaffTool
from .brain.tools.self_work import SelfExecuteTaskTool
from .brain.tools.shell_tool import ExecuteCommandBrainTool
from .brain.tools.work import AssignTaskTool, RequestDigestTool, SetListenTool
from .brain.tools.work import SendFileTool as BrainSendFileTool
from .config import Config
from .context import ChatHistory
from .deps import Deps
from .errors import CortexError
from .hr import HRService
from .logging_setup import get_logger, setup_logging
from .orchestrator import Orchestrator
from .registry import EmployeeRegistry
from .runtime import AgentRunner, TaskScheduler
from .security import SecurityGuard
from .state import ChatState
from .telegram.bot_pool import BotPool
from .telegram.gateway import Gateway
from .telegram.routing import MentionRouter
from .tools import ToolRegistry
from .tools.execute_command import ExecuteCommandTool
from .tools.send_file import SendFileTool
from .tools.update_system_prompt import UpdateSystemPromptTool
from .tools.update_telegram_profile import UpdateTelegramProfileTool
from .tools.web_research import WebResearchTool
from .workspace import WorkspaceManager

log = get_logger("app")

_BANNER = r"""
   ___         _
  / __|___ _ _| |_ _____ __
 | (__/ _ \ '_|  _/ -_) \ /
  \___\___/_|  \__\___/_\_\   Plexus Lab
"""


class PlexusLab:
    """Жизненный цикл сервера: сборка → старт → graceful shutdown."""

    def __init__(self, root: Path | None = None) -> None:
        self.config = Config.load(root)
        setup_logging(self.config.secrets.log_level, self.config.data_dir)
        self.deps: Deps | None = None
        self.gateway: Gateway | None = None

    # ------------------------------------------------------------------
    def build(self) -> Deps:
        cfg = self.config
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.prompts_dir.mkdir(parents=True, exist_ok=True)

        registry = EmployeeRegistry(cfg.registry_path, cfg.prompts_dir)
        registry.load()

        workspaces = WorkspaceManager(cfg.projects_dir)
        history = ChatHistory(cfg.data_dir, limit=cfg.history_messages)
        state = ChatState(cfg.data_dir)
        bots = BotPool(registry, max_message_length=cfg.max_message_length)
        guard = SecurityGuard(cfg, registry)
        synapse = SynapseService(cfg)

        tools = ToolRegistry(cfg)
        tools.register_all(
            [
                ExecuteCommandTool(),
                SendFileTool(),
                UpdateTelegramProfileTool(),
                UpdateSystemPromptTool(),
                WebResearchTool(synapse),
            ]
        )

        runner = AgentRunner(cfg)
        scheduler = TaskScheduler(
            max_parallel=cfg.max_parallel_tasks,
            serialize_per_project=cfg.serialize_per_project,
        )
        orchestrator = Orchestrator(
            config=cfg,
            registry=registry,
            workspaces=workspaces,
            history=history,
            runner=runner,
            scheduler=scheduler,
            tools=tools,
            bots=bots,
        )
        mentions = MentionRouter(registry, workspaces, state)
        hr = HRService(cfg, registry, bots)
        choices = PendingChoiceStore(cfg.data_dir / "pending_choices.json")

        self.deps = Deps(
            config=cfg,
            registry=registry,
            workspaces=workspaces,
            history=history,
            state=state,
            bots=bots,
            tools=tools,
            runner=runner,
            scheduler=scheduler,
            orchestrator=orchestrator,
            mentions=mentions,
            guard=guard,
            hr=hr,
            synapse=synapse,
            choices=choices,
        )

        brain_tools = BrainToolRegistry()
        brain_tools.register_all(
            [
                ListStaffTool(), GetEmployeeTool(), ListProjectsTool(), GetStatusTool(),
                HireEmployeeTool(), WriteJobDescriptionTool(), FireEmployeeTool(),
                CreateProjectTool(), LinkProjectTool(), SetChatProjectTool(),
                UnlinkProjectTool(), ArchiveProjectTool(),
                AssignTaskTool(), SetListenTool(), BrainSendFileTool(), RequestDigestTool(),
                ExecuteCommandBrainTool(), SelfExecuteTaskTool(), SendButtonsTool(),
            ]
        )

        custom_tools = CustomToolStore(
            scripts_dir=cfg.data_dir / "brain_tools",
            registry_path=cfg.data_dir / "custom_tools.json",
        )
        # Инструменты, которые мозг уже написал себе в прошлых сессиях,
        # должны пережить перезапуск — иначе он "забудет", что уже решал
        # эту задачу, и начнёт писать код заново.
        for record in custom_tools.all():
            brain_tools.register(RunCustomToolTool(record))
        brain_tools.register(CreateToolTool(store=custom_tools, registry=brain_tools))
        brain_prompts = BrainPromptBuilder(cfg, registry, workspaces, state, brain_tools)
        self.deps.brain = BrainAgent(
            deps=self.deps,
            tools=brain_tools,
            prompts=brain_prompts,
            session=BrainSession(cfg.data_dir),
            pending=PendingActionStore(cfg.data_dir / "pending_actions.json"),
        )
        return self.deps

    # ------------------------------------------------------------------
    async def run(self) -> None:
        print(_BANNER)
        deps = self.build()

        self.gateway = Gateway(deps)
        deps.gateway = self.gateway

        self._log_startup(deps)
        await self._verify_tokens(deps)

        stop_event = asyncio.Event()
        self._install_signal_handlers(stop_event)

        await self.gateway.start()
        await self.gateway.announce(
            f"🟢 <b>{deps.config.orchestrator_name}</b> в строю.\n"
            f"Штат: {len(deps.registry.all())} · "
            f"Проектов: {len(deps.workspaces.list())} · "
            f"Драйвер: <code>{deps.config.runner_driver.name}</code>"
        )

        waiter = asyncio.create_task(self.gateway.wait())
        stopper = asyncio.create_task(stop_event.wait())
        try:
            await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (waiter, stopper):
                task.cancel()
            await self.gateway.stop()
            log.info("Plexus Lab остановлен")

    # ------------------------------------------------------------------
    def _log_startup(self, deps: Deps) -> None:
        log.info("=" * 62)
        log.info("%s — %s", deps.config.company_name, deps.config.orchestrator_name)
        log.info("CEO: %s (id=%s)", deps.config.ceo_name, deps.config.secrets.ceo_id)
        log.info(
            "Группа: %s",
            deps.config.secrets.corp_group_id or "НЕ ЗАДАНА (только личка CEO)",
        )
        log.info("Проекты: %s", deps.config.projects_dir)
        log.info("Драйвер сабагентов: %s", deps.config.runner_driver.command)
        log.info(
            "Штат: %s",
            ", ".join(e.name for e in deps.registry.all()) or "пусто — начни с /hire",
        )
        log.info("=" * 62)

        if not deps.config.secrets.corp_group_id:
            log.warning(
                "CORP_GROUP_ID не задан: сообщения из групп будут игнорироваться. "
                "Отправь /id в группе и впиши значение в .env."
            )

    async def _verify_tokens(self, deps: Deps) -> None:
        """Прогреваем пул и подтягиваем username/bot_id — они нужны охране."""
        for employee in deps.registry.all():
            if employee.bot_id and employee.username:
                continue
            try:
                await deps.bots.verify(employee)
            except CortexError as exc:
                log.error("Токен %s недействителен: %s", employee.name, exc)

    def _install_signal_handlers(self, stop_event: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            log.info("Получен сигнал остановки")
            stop_event.set()

        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, request_stop)
            except NotImplementedError:
                # Windows: add_signal_handler не поддерживается, ловим через signal.
                signal.signal(sig, lambda *_: request_stop())


def main() -> int:
    try:
        app = PlexusLab()
    except CortexError as exc:
        print(f"\n[!] {getattr(exc, 'title', 'Ошибка')}: {exc}\n", file=sys.stderr)
        return 2

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    except CortexError as exc:
        print(f"\n[!] {getattr(exc, 'title', 'Ошибка')}: {exc}\n", file=sys.stderr)
        return 2
    return 0
