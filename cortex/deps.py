"""Контейнер зависимостей Cortex.

Собирается один раз в app.py и прокидывается в роутеры Telegram. Вынесен
в отдельный модуль, чтобы обработчики не импортировали приложение целиком
и не создавали циклов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .agents import SynapseService
from .config import Config
from .context import ChatHistory
from .hr import HRService
from .orchestrator import Orchestrator
from .registry import EmployeeRegistry
from .runtime import AgentRunner, TaskScheduler
from .security import SecurityGuard
from .state import ChatState
from .telegram.bot_pool import BotPool
from .telegram.routing import MentionRouter
from .tools import ToolRegistry
from .workspace import WorkspaceManager


@dataclass(slots=True)
class Deps:
    config: Config
    registry: EmployeeRegistry
    workspaces: WorkspaceManager
    history: ChatHistory
    state: ChatState
    bots: BotPool
    tools: ToolRegistry
    runner: AgentRunner
    scheduler: TaskScheduler
    orchestrator: Orchestrator
    mentions: MentionRouter
    guard: SecurityGuard
    hr: HRService
    synapse: SynapseService
    #: Проставляется после создания Gateway — он сам нуждается в Deps.
    gateway: Any = None
    #: Мозг Cortex — собирается в app.py после Deps, тем же приёмом, что gateway.
    brain: Any = None
    started_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc)

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()
