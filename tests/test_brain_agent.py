# tests/test_brain_agent.py
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cortex.brain.agent import BrainAgent
from cortex.brain.context import BrainPromptBuilder
from cortex.brain.pending import PendingAction, PendingActionStore
from cortex.brain.session import BrainSession
from cortex.brain.tools.base import BrainToolRegistry
from cortex.brain.tools.hr import FireEmployeeTool
from cortex.brain.tools.read import GetEmployeeTool, GetStatusTool, ListProjectsTool, ListStaffTool
from cortex.config import Config
from cortex.context import ChatHistory
from cortex.errors import AgentRunError
from cortex.hr import HRService
from cortex.models import AgentResult
from cortex.runtime import AgentRunner
from cortex.telegram.bot_pool import BotPool

ECHO_BRAIN = Path(__file__).resolve().parent / "fixtures" / "echo_brain.py"
CHAT = -100500


class _FakeGateway:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.confirmations: list[tuple] = []

    async def reply(self, chat_id: int, text: str, *, reply_to=None) -> None:
        self.messages.append(text)

    async def ask_confirmation(self, *, chat_id, action_id, summary, risk) -> None:
        self.confirmations.append((chat_id, action_id, summary, risk))


@dataclass
class _FakeScheduler:
    active: list = field(default_factory=list)


@dataclass
class _FakeDeps:
    config: Config
    registry: object
    workspaces: object
    state: object
    history: object
    runner: object
    gateway: object
    hr: object
    scheduler: object = field(default_factory=_FakeScheduler)
    tools: object = None

    @property
    def uptime_seconds(self) -> float:
        return 0.0


def _make_deps(cfg, registry, workspaces, state, gateway) -> _FakeDeps:
    """hr нужен FireEmployeeTool — реальный HRService/BotPool безопасны здесь:
    bots.drop() на токен, для которого никогда не открывался Bot, просто
    ничего не делает (BotPool.drop проверяет наличие в своём кэше)."""
    return _FakeDeps(
        config=cfg, registry=registry, workspaces=workspaces, state=state,
        history=ChatHistory(cfg.data_dir, limit=10), runner=AgentRunner(cfg), gateway=gateway,
        hr=HRService(cfg, registry, BotPool(registry)),
    )


def _config_with_brain_driver(tmp_path, secrets, counter_file: Path) -> Config:
    command = (
        f'"{sys.executable}" "{ECHO_BRAIN}" --prompt-file {{prompt_file}} '
        f'--counter-file "{counter_file}"'
    )
    raw = {
        "company": {"name": "Plexus Lab", "ceo_name": "Abdulloh Abbosov"},
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "claude",
            "drivers": {"claude": {"command": command}},
            "timeout_seconds": 30,
        },
        "brain": {"autonomy": "balanced", "max_iterations": 5},
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _agent(deps, tools=None) -> BrainAgent:
    registry = BrainToolRegistry()
    registry.register_all(
        tools or [ListStaffTool(), GetEmployeeTool(), ListProjectsTool(), GetStatusTool(), FireEmployeeTool()]
    )
    prompts = BrainPromptBuilder(deps.config, deps.registry, deps.workspaces, deps.state, registry)
    session = BrainSession(deps.config.data_dir)
    pending = PendingActionStore(deps.config.data_dir / "pending_actions.json")
    return BrainAgent(deps=deps, tools=registry, prompts=prompts, session=session, pending=pending)


async def test_two_turn_conversation_ends_with_plain_text(tmp_path, secrets, registry, workspaces, state, frontend):
    await registry.add(frontend)
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)

    agent = _agent(deps)
    await agent.handle_message(chat_id=CHAT, message_id=1, text="кто у нас в штате?", requester_id=1001)

    assert any("Сейчас посмотрю" in m for m in gateway.messages)
    assert any("Frontend_Dev" in m for m in gateway.messages)


async def test_risky_action_asks_for_confirmation_first(tmp_path, secrets, registry, workspaces, state, frontend):
    await registry.add(frontend)
    counter = tmp_path / "counter_fire.txt"
    command = f'"{sys.executable}" "{ECHO_BRAIN}" --prompt-file {{prompt_file}} --counter-file "{counter}"'
    # Переиспользуем ту же заглушку, но подменим её первый ответ на fire_employee
    # через отдельный скрипт — проще: пишем его прямо в тесте.
    fire_script = tmp_path / "echo_fire.py"
    fire_script.write_text(
        "import sys\n"
        "sys.stdout.write('<action>\\n{\"tool\": \"fire_employee\", \"args\": {\"name\": \"Frontend_Dev\"}}\\n</action>\\n')\n",
        encoding="utf-8",
    )
    cfg = _config_with_brain_driver(tmp_path, secrets, counter)
    cfg.raw["agent_runner"]["drivers"]["claude"]["command"] = f'"{sys.executable}" "{fire_script}"'
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)

    agent = _agent(deps)
    await agent.handle_message(chat_id=CHAT, message_id=1, text="уволь Frontend_Dev", requester_id=1001)

    # действие НЕ выполнено — сотрудник всё ещё в штате, а CEO увидел кнопки
    assert registry.get("Frontend_Dev") is not None
    assert registry.get("Frontend_Dev").active is True
    assert len(gateway.confirmations) == 1
    assert gateway.confirmations[0][3] == "risky"


async def test_confirmed_pending_action_executes_and_reports(tmp_path, secrets, registry, workspaces, state, frontend):
    await registry.add(frontend)
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    agent = _agent(deps)

    pending = PendingAction(
        id="p1", chat_id=CHAT, message_id=1, requester_id=1001,
        tool="fire_employee", args={"name": "Frontend_Dev"}, risk="risky", summary="Уволить",
    )
    await agent.pending.add(pending)

    await agent.resolve_pending("p1", chat_id=CHAT, approved=True)

    assert registry.get("Frontend_Dev") is None or registry.get("Frontend_Dev").active is False


async def test_declined_pending_action_does_not_execute(tmp_path, secrets, registry, workspaces, state, frontend):
    await registry.add(frontend)
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    agent = _agent(deps)

    pending = PendingAction(
        id="p2", chat_id=CHAT, message_id=1, requester_id=1001,
        tool="fire_employee", args={"name": "Frontend_Dev"}, risk="risky", summary="Уволить",
    )
    await agent.pending.add(pending)

    await agent.resolve_pending("p2", chat_id=CHAT, approved=False)

    assert registry.get("Frontend_Dev").active is True


async def test_stale_confirmation_click_tells_ceo_instead_of_staying_silent(
    tmp_path, secrets, registry, workspaces, state, frontend
):
    """Ревью нашло реальный UX-баг: Telegram-сообщение уже переписано на
    '✅ Подтверждено' до вызова resolve_pending (см. brain_router.py) — если
    действие тем временем пропало (двойной клик, рестарт), CEO раньше не
    получал вообще никакого сигнала о том, что на самом деле ничего не
    произошло."""
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    agent = _agent(deps)

    await agent.resolve_pending("no-such-action", chat_id=CHAT, approved=True)

    assert any("уже обработано" in m for m in gateway.messages)


class _ExplodingRunner:
    """Симулирует то, что реально произошло на живом сервере: subprocess
    падает с NotImplementedError (SelectorEventLoop не умеет их запускать
    на Windows) — исключением, которое НЕ является AgentRunError."""

    async def run(self, **kwargs):
        raise NotImplementedError("subprocess не поддерживается этим event loop")


async def test_unexpected_exception_is_reported_not_swallowed(
    tmp_path, secrets, registry, workspaces, state, frontend
):
    """Регрессия: до фикса такое исключение тонуло в 'Task exception was
    never retrieved' и CEO не получал вообще никакого ответа."""
    await registry.add(frontend)
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    deps.runner = _ExplodingRunner()
    agent = _agent(deps)

    await agent.handle_message(chat_id=CHAT, message_id=1, text="привет", requester_id=1001)

    assert len(gateway.messages) == 1
    assert "мозг Cortex споткнулся" in gateway.messages[0]


class _RecordingRunner:
    """Записывает kwargs каждого вызова .run() — не запускает ничего реально."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        raise NotImplementedError("не нужен реальный вызов для этого теста")


async def test_brain_never_runs_claude_with_project_root_as_cwd(
    tmp_path, secrets, registry, workspaces, state, frontend
):
    """Защита в глубину: даже если --tools "" когда-нибудь перестанет
    работать как задумано, claude не должен физически видеть .env,
    employees_registry.json и исходники Cortex через cwd."""
    await registry.add(frontend)
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    recorder = _RecordingRunner()
    deps.runner = recorder
    agent = _agent(deps)

    await agent.handle_message(chat_id=CHAT, message_id=1, text="привет", requester_id=1001)

    assert len(recorder.calls) == 1
    workspace = recorder.calls[0]["workspace"]
    assert workspace != cfg.root
    assert workspace.name == "brain_workspace"


class _ResumeFailsOnceRunner:
    """Воспроизводит то, что реально происходит с claude в этой среде:
    --resume регулярно не находит сессию, созданную через --session-id
    (см. Task 4 «Открытый вопрос» в плане). Второй вызов, уже со свежим
    --session-id, отрабатывает нормально."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, *, session_flag: str, **kwargs) -> AgentResult:
        self.calls.append(session_flag)
        if session_flag.startswith("--resume"):
            raise AgentRunError(
                "No conversation found with session ID: ...", returncode=1, duration=0.1
            )
        return AgentResult(
            stdout="Всё в порядке, продолжаю.", stderr="", returncode=0, duration=0.5, command="claude"
        )


async def test_resume_failure_falls_back_to_a_fresh_session_once(
    tmp_path, secrets, registry, workspaces, state
):
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    runner = _ResumeFailsOnceRunner()
    deps.runner = runner
    agent = _agent(deps)

    # Помечаем чат как "уже видели" — session_flag() сразу вернёт --resume,
    # как оно и происходит на втором сообщении в реальном разговоре.
    agent.session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")

    await agent.handle_message(chat_id=CHAT, message_id=1, text="как ты там?", requester_id=1001)

    assert len(runner.calls) == 2
    assert runner.calls[0].startswith("--resume")
    assert runner.calls[1].startswith("--session-id")
    assert any("Всё в порядке" in m for m in gateway.messages)


async def test_resume_failure_does_not_retry_forever(tmp_path, secrets, registry, workspaces, state):
    """Если и свежая сессия падает — сдаёмся с отчётом об ошибке, а не
    зацикливаемся: AgentRunError на --session-id уже не считается 'резюме
    не нашлось', это реальный сбой."""

    class _AlwaysFailsRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            raise AgentRunError(
                "No conversation found with session ID: ...", returncode=1, duration=0.1
            )

    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    runner = _AlwaysFailsRunner()
    deps.runner = runner
    agent = _agent(deps)
    agent.session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")

    await agent.handle_message(chat_id=CHAT, message_id=1, text="как ты там?", requester_id=1001)

    assert runner.calls == 2  # один --resume, один --session-id — и остановились
    assert any("не справился" in m for m in gateway.messages)


async def test_non_session_failure_is_reported_without_resetting_the_session(
    tmp_path, secrets, registry, workspaces, state
):
    """Живой вопрос CEO: что будет, если кончится квота Claude? Раньше ЛЮБОЙ
    сбой --resume (не только 'сессия не найдена') считался поводом сбросить
    сессию и попробовать заново с чистой — при исчерпанной квоте это значит
    бессмысленный сброс рабочей сессии (повтор тоже упрётся в ту же квоту) и
    CEO теряет контекст разговора без всякой пользы взамен."""

    class _QuotaExhaustedRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            raise AgentRunError(
                "Процесс завершился с кодом 1.", returncode=1, duration=0.1,
                stderr="Claude AI usage limit reached. Your limit will reset at 3pm.",
            )

    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    runner = _QuotaExhaustedRunner()
    deps.runner = runner
    agent = _agent(deps)
    agent.session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")

    await agent.handle_message(chat_id=CHAT, message_id=1, text="как ты там?", requester_id=1001)

    assert runner.calls == 1  # ни одной лишней попытки со сброшенной сессией
    assert any("не справился" in m for m in gateway.messages)
    # сессия НЕ сброшена — следующий вызов снова попробует --resume тем же id
    assert agent.session.session_flag(CHAT) == "--resume 11111111-1111-1111-1111-111111111111"


class _ResumeFailsOnSecondIterationRunner:
    """Первый ход (iteration 1, prompt = build_initial с полной историей)
    отдаёт вызов инструмента; второй ход (iteration 2, prompt = короткий
    build_followup) падает — имитирует сбой --resume НЕ на первом ходу."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, *, session_flag: str, **kwargs) -> AgentResult:
        self.calls.append(session_flag)
        if len(self.calls) == 1:
            return AgentResult(
                stdout='Смотрю штат.\n<action>{"tool": "list_staff", "args": {}}</action>',
                stderr="", returncode=0, duration=0.1, command="claude",
            )
        raise AgentRunError("сеть моргнула", returncode=1, duration=0.1)


async def test_resume_failure_past_first_iteration_is_reported_not_silently_retried(
    tmp_path, secrets, registry, workspaces, state
):
    """Реальная дыра, найденная ревью-агентом (не наблюдалась вживую): если
    --resume падает не на первом ходу разговора, а посреди многошагового
    цикла инструментов, старая логика откатывала бы на свежую сессию с тем
    же prompt — но на iteration > 1 prompt это только короткий результат
    последнего инструмента, без единого слова о том, какую задачу claude
    вообще решает. С iteration 2+ такой сбой обязан дойти до чата как
    честная ошибка, а не тихо продолжиться в сессии без контекста."""
    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    runner = _ResumeFailsOnSecondIterationRunner()
    deps.runner = runner
    agent = _agent(deps)
    agent.session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")  # чат уже "видели" — сразу --resume с первого хода

    await agent.handle_message(chat_id=CHAT, message_id=1, text="кто в штате?", requester_id=1001)

    assert len(runner.calls) == 2  # iteration 1 (успех) + iteration 2 (провал) — без тихого ретрая
    assert all(c.startswith("--resume") for c in runner.calls)
    assert any("не справился" in m for m in gateway.messages)
