"""Сквозной тест: задача → сабагент → <action> → инструмент → ответ в чат.

Telegram подменён заглушкой, сабагент — детерминированным скриптом.
Проверяется именно склейка компонентов, а не каждый из них по отдельности.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cortex.config import Config
from cortex.context import ChatHistory
from cortex.models import Employee
from cortex.orchestrator import Orchestrator
from cortex.registry import EmployeeRegistry
from cortex.runtime import AgentRunner, TaskScheduler
from cortex.tools import ToolRegistry
from cortex.tools.execute_command import ExecuteCommandTool
from cortex.tools.send_file import SendFileTool
from cortex.workspace import WorkspaceManager

ECHO_AGENT = Path(__file__).resolve().parent / "fixtures" / "echo_agent.py"
CHAT = -100500


class FakeBotPool:
    """Записывает всё, что Cortex попытался отправить в Telegram."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def say(self, employee, chat_id, text, *, reply_to=None, silent=False):
        self.messages.append((employee.name, text))

    async def typing(self, employee, chat_id):
        return None

    async def get(self, employee):
        return object()

    def texts(self) -> str:
        return "\n".join(text for _, text in self.messages)


@pytest.fixture()
def env(tmp_path, secrets):
    command = f'"{sys.executable}" "{ECHO_AGENT}" --prompt-file {{prompt_file}}'
    raw = {
        "company": {"name": "Plexus Lab", "ceo_name": "Abdulloh Abbosov"},
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "test",
            "drivers": {"test": {"command": command}},
            "timeout_seconds": 60,
        },
        "context": {"include_workspace_tree": True},
        "telegram": {"typing_interval": 60},
        "security": {"command_blocklist": [r"(?i)\brm\s+-rf\s+/"]},
        "tools": {"default": ["execute_command", "send_file"]},
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)

    registry = EmployeeRegistry(cfg.registry_path, cfg.prompts_dir)
    registry.load()
    workspaces = WorkspaceManager(cfg.projects_dir)
    tools = ToolRegistry(cfg)
    tools.register_all([ExecuteCommandTool(), SendFileTool()])
    bots = FakeBotPool()

    orchestrator = Orchestrator(
        config=cfg,
        registry=registry,
        workspaces=workspaces,
        history=ChatHistory(cfg.data_dir, limit=10),
        runner=AgentRunner(cfg),
        scheduler=TaskScheduler(max_parallel=2, serialize_per_project=True),
        tools=tools,
        bots=bots,
    )
    return cfg, registry, workspaces, bots, orchestrator


@pytest.fixture()
async def employee(env):
    _cfg, registry, _ws, _bots, _orc = env
    emp = Employee(
        name="Frontend_Dev",
        role="Senior Frontend Engineer",
        token="222:FAKE",
        prompt_path="prompts/frontend_dev.md",
    )
    await registry.add(emp)
    return emp


async def test_full_cycle_creates_file_and_reports(env, employee):
    _cfg, _registry, workspaces, bots, orchestrator = env
    project = workspaces.create("sports_api", "API спортивного сервиса")

    task = orchestrator.new_task(
        employee=employee,
        project_name="sports_api",
        instruction="Создай файл-маркер",
        chat_id=CHAT,
        message_id=1,
        requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    # 1. действие исполнено в песочнице проекта
    created = project.path / "created.txt"
    assert created.exists()
    assert "plexus" in created.read_text(encoding="utf-8")

    # 2. реплика агента ушла в чат
    assert "Осмотрелся в проекте" in bots.texts()

    # 3. отчёт об инструменте ушёл в чат
    assert "execute_command" in bots.texts()
    assert "✅" in bots.texts()

    # 4. всё отправлено от лица сотрудника, а не Cortex
    assert {name for name, _ in bots.messages} == {"Frontend_Dev"}


async def test_action_blocks_do_not_leak_into_chat(env, employee):
    _cfg, _registry, workspaces, bots, orchestrator = env
    workspaces.create("sports_api")

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Сделай",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    reply = bots.messages[0][1]
    assert "<action>" not in reply
    assert "execute_command" not in reply


async def test_agent_markdown_reply_is_converted_to_telegram_html(env, employee, tmp_path):
    """Живой инцидент: self_execute_task (и вообще любой сотрудник) шлёт
    ответ через тот же _deliver, что и раньше только fmt.esc() — модель
    пишет **жирным**/### заголовками, CEO видел звёздочки и решётки
    буквально. Мозг это уже чинили (agent.py), тут та же дыра осталась."""
    cfg, _registry, workspaces, bots, orchestrator = env
    workspaces.create("sports_api")

    markdown_agent = tmp_path / "markdown_agent.py"
    markdown_agent.write_text(
        "import sys\n"
        "sys.stdout.write('### Вариант 1\\n\\n**Плюсы:** быстро.\\n')\n",
        encoding="utf-8",
    )
    cfg.raw["agent_runner"]["drivers"]["test"]["command"] = f'"{sys.executable}" "{markdown_agent}"'

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Исследуй",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    reply = bots.texts()
    assert "<b>Плюсы:</b>" in reply
    assert "**" not in reply


async def test_agent_reply_lands_in_history(env, employee):
    _cfg, _registry, workspaces, _bots, orchestrator = env
    workspaces.create("sports_api")

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Сделай",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    rendered = orchestrator.history.render(CHAT)
    assert "Frontend_Dev" in rendered


async def test_crashed_agent_produces_readable_report(env, employee, tmp_path):
    """stderr перехвачен и превращён в отчёт, Cortex не падает."""
    cfg, _registry, workspaces, bots, orchestrator = env
    workspaces.create("sports_api")

    broken = tmp_path / "broken_agent.py"
    broken.write_text(
        "import sys\nsys.stderr.write('ModuleNotFoundError: no antigravity\\n')\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    cfg.raw["agent_runner"]["drivers"]["test"]["command"] = (
        f'"{sys.executable}" "{broken}"'
    )

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Сломайся",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    report = bots.texts()
    assert "не справился" in report
    assert "no antigravity" in report
    assert "3" in report


async def test_silent_success_surfaces_stderr_reason(env, employee, tmp_path):
    """Живой инцидент: agy требует "command"-разрешение, в headless-режиме
    подтвердить его некому — agy тихо завершается кодом 0 с пустым stdout,
    а причину пишет только в stderr, который раньше нигде не показывался
    при успехе. CEO видел безликое "🤷" и не понимал, что вообще случилось."""
    cfg, _registry, workspaces, bots, orchestrator = env
    workspaces.create("sports_api")

    silent = tmp_path / "silent_agent.py"
    silent.write_text(
        "import sys\n"
        "sys.stderr.write('jetski: no output produced — a tool required the "
        "\"command\" permission that headless mode cannot prompt for, so it "
        "was auto-denied.\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    cfg.raw["agent_runner"]["drivers"]["test"]["command"] = f'"{sys.executable}" "{silent}"'

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Скачай файл",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    report = bots.texts()
    assert "🤷" in report
    assert "command" in report and "permission" in report


async def test_quota_error_triggers_auto_retry(env, employee, tmp_path):
    """Живой инцидент: agy падает с 'Individual quota reached ... Resets in
    Xs' — это временная ошибка, Cortex должен сам подождать и повторить
    задачу, а не бросить её на CEO с 'оставил задачу незакрытой'."""
    cfg, _registry, workspaces, bots, orchestrator = env
    workspaces.create("sports_api")

    marker = tmp_path / "retried.marker"
    quota_agent = tmp_path / "quota_agent.py"
    quota_agent.write_text(
        "import sys, pathlib\n"
        f"marker = pathlib.Path(r'{marker}')\n"
        "if marker.exists():\n"
        "    sys.stdout.write('Всё восстановилось.\\n')\n"
        "else:\n"
        "    marker.write_text('x')\n"
        "    sys.stderr.write('Error: Individual quota reached. Please "
        "upgrade your subscription to increase your limits. "
        "Resets in 0s.\\n')\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    cfg.raw["agent_runner"]["drivers"]["test"]["command"] = (
        f'"{sys.executable}" "{quota_agent}"'
    )

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Скачай mp3",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    report = bots.texts()
    assert "упёрся в лимит" in report
    assert "Всё восстановилось" in report
    assert "оставил задачу незакрытой" not in report


async def test_quota_error_falls_back_to_configured_driver_immediately(env, employee, tmp_path):
    """Если настроен fallback-драйвер, retryable-ошибка сразу пробует его —
    без ожидания кулдауна, а не ждёт 'Resets in Xs' как обычный ретрай."""
    cfg, _registry, workspaces, bots, orchestrator = env
    workspaces.create("sports_api")

    quota_agent = tmp_path / "quota_agent.py"
    quota_agent.write_text(
        "import sys\n"
        "sys.stderr.write('Error: Individual quota reached. Please "
        "upgrade your subscription to increase your limits. "
        "Resets in 999s.\\n')\n"  # долгий кулдаун — если тест долетит до
        "sys.exit(1)\n",           # него, а не до fallback, тест зависнет
        encoding="utf-8",
    )
    fallback_agent = tmp_path / "fallback_agent.py"
    fallback_agent.write_text(
        "import sys\nsys.stdout.write('Ответила резервная модель.\\n')\n",
        encoding="utf-8",
    )
    cfg.raw["agent_runner"]["drivers"]["test"]["command"] = (
        f'"{sys.executable}" "{quota_agent}"'
    )
    cfg.raw["agent_runner"]["drivers"]["test_fallback"] = {
        "command": f'"{sys.executable}" "{fallback_agent}"'
    }
    cfg.raw["agent_runner"]["fallback_drivers"] = ["test_fallback"]

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Скачай mp3",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    report = bots.texts()
    assert "резервную модель" in report
    assert "Ответила резервная модель" in report
    assert "оставил задачу незакрытой" not in report
    assert "упёрся в лимит запросов, жду" not in report  # cooldown path not used


async def test_quota_error_uses_cooldown_when_no_fallback_configured(env, employee, tmp_path):
    """Без fallback_drivers поведение не меняется — старый cooldown-ретрай."""
    cfg, _registry, workspaces, bots, orchestrator = env
    workspaces.create("sports_api")

    marker = tmp_path / "retried.marker"
    quota_agent = tmp_path / "quota_agent2.py"
    quota_agent.write_text(
        "import sys, pathlib\n"
        f"marker = pathlib.Path(r'{marker}')\n"
        "if marker.exists():\n"
        "    sys.stdout.write('Восстановилось.\\n')\n"
        "else:\n"
        "    marker.write_text('x')\n"
        "    sys.stderr.write('Error: Individual quota reached. Please "
        "upgrade your subscription to increase your limits. "
        "Resets in 0s.\\n')\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    cfg.raw["agent_runner"]["drivers"]["test"]["command"] = (
        f'"{sys.executable}" "{quota_agent}"'
    )
    # no fallback_drivers set — cfg.raw["agent_runner"] has no such key

    task = orchestrator.new_task(
        employee=employee, project_name="sports_api", instruction="Скачай mp3",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    report = bots.texts()
    assert "упёрся в лимит запросов, жду" in report
    assert "Восстановилось" in report


async def test_unknown_project_is_reported_not_crashed(env, employee):
    _cfg, _registry, _ws, bots, orchestrator = env

    task = orchestrator.new_task(
        employee=employee, project_name="does_not_exist", instruction="Сделай",
        chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    assert "не существует" in bots.texts()


async def test_forbidden_tool_is_refused(env, employee):
    """Инструмент вне политики роли не исполняется, даже если агент его просит."""
    _cfg, registry_obj, workspaces, bots, orchestrator = env
    project = workspaces.create("sports_api")
    await registry_obj.update("Frontend_Dev", tools=["send_file"])

    task = orchestrator.new_task(
        employee=registry_obj.require("Frontend_Dev"), project_name="sports_api",
        instruction="Создай файл", chat_id=CHAT, message_id=1, requester="CEO",
    )
    await orchestrator.dispatch(task, requester_id=1001)

    assert not (project.path / "created.txt").exists()
    assert "не имеет доступа" in bots.texts()
