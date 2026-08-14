# tests/test_brain_agent_self_heal.py
"""Self-healing retry-loop: когда кастомный (сгенерированный create_tool)
инструмент падает, мозг не должен ни молча сдаваться на первой же ошибке,
ни зацикливаться бесконечно. Требования CEO: traceback читаемо
возвращается агенту, агент знает про возможность переписать инструмент
через create_tool, лимит 3 попытки, финальный отчёт CEO — с исходной
задачей и всем, что было испробовано."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from cortex.brain.agent import BrainAgent
from cortex.brain.context import BrainPromptBuilder
from cortex.brain.pending import PendingActionStore
from cortex.brain.session import BrainSession
from cortex.brain.tools.base import BrainToolRegistry
from cortex.brain.tools.custom import CreateToolTool, RunCustomToolTool
from cortex.brain.tools.custom_store import CustomToolRecord, CustomToolStore
from cortex.config import Config
from cortex.context import ChatHistory
from cortex.hr import HRService
from cortex.models import AgentResult
from cortex.telegram.bot_pool import BotPool

CHAT = -100500
ECHO_ALWAYS_RETRIES = Path(__file__).resolve().parent / "fixtures" / "echo_brain_always_retries.py"


class _FakeGateway:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def reply(self, chat_id: int, text: str, *, reply_to=None) -> None:
        self.messages.append(text)

    async def ask_confirmation(self, *, chat_id, action_id, summary, risk) -> None:
        raise AssertionError("custom-tool run/create не должны требовать доп. подтверждения в этих тестах")


class _ScriptedRunner:
    """Отдаёт по одному AgentResult на вызов из списка; последний
    результат переиспользуется, если вызовов окажется больше."""

    def __init__(self, results: list[AgentResult]) -> None:
        self.results = results
        self.calls: list[dict] = []

    async def run(self, **kwargs) -> AgentResult:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.results) - 1)
        return self.results[index]


def _config(tmp_path, secrets) -> Config:
    raw = {
        "company": {"name": "Plexus Lab", "ceo_name": "Abdulloh Abbosov"},
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {"driver": "claude", "drivers": {"claude": {"command": "claude"}}},
        "brain": {"autonomy": "autonomous", "max_iterations": 10},
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _agent_with_broken_tool(cfg, registry, workspaces, state, gateway, runner, *, script: str) -> BrainAgent:
    broken_script = cfg.data_dir / "broken_tool.py"
    broken_script.write_text(script, encoding="utf-8")

    tools = BrainToolRegistry()
    store = CustomToolStore(
        scripts_dir=cfg.data_dir / "brain_tools", registry_path=cfg.data_dir / "custom_tools.json"
    )
    tools.register(
        RunCustomToolTool(
            CustomToolRecord(
                name="broken_tool", description="ломается", usage="",
                script_path=str(broken_script), created_by="1001",
            )
        )
    )
    tools.register(CreateToolTool(store=store, registry=tools))

    deps = SimpleNamespace(
        config=cfg,
        registry=registry,
        workspaces=workspaces,
        state=state,
        history=ChatHistory(cfg.data_dir, limit=10),
        runner=runner,
        gateway=gateway,
        hr=HRService(cfg, registry, BotPool(registry)),
        tools=tools,
    )

    prompts = BrainPromptBuilder(cfg, registry, workspaces, state, tools)
    session = BrainSession(cfg.data_dir)
    pending = PendingActionStore(cfg.data_dir / "pending_actions.json")
    return BrainAgent(deps=deps, tools=tools, prompts=prompts, session=session, pending=pending)


_ALWAYS_FAILS_SCRIPT = "import sys\nsys.stderr.write('Traceback: NameError: undefined_var\\n')\nsys.exit(1)\n"
_FAILS_ONCE_SCRIPT = (
    "import sys\n"
    "from pathlib import Path\n"
    "marker = Path(__file__).with_suffix('.ran')\n"
    "if marker.exists():\n"
    "    print('ok now')\n"
    "else:\n"
    "    marker.write_text('1')\n"
    "    sys.stderr.write('Traceback: NameError: undefined_var\\n')\n"
    "    sys.exit(1)\n"
)


def _propose_broken_tool_result(text: str) -> AgentResult:
    return AgentResult(
        stdout=f"{text}\n\n<action>\n{{\"tool\": \"broken_tool\", \"args\": {{}}}}\n</action>\n",
        stderr="", returncode=0, duration=0.1, command="claude",
    )


def _plain_text_result(text: str) -> AgentResult:
    return AgentResult(stdout=text, stderr="", returncode=0, duration=0.1, command="claude")


async def test_failure_detail_is_readable_and_mentions_create_tool(tmp_path, secrets, registry, workspaces, state):
    runner = _ScriptedRunner([
        _propose_broken_tool_result("Пробую"),
        _plain_text_result("Понял, попробую другой подход."),
    ])
    cfg = _config(tmp_path, secrets)
    gateway = _FakeGateway()
    agent = _agent_with_broken_tool(cfg, registry, workspaces, state, gateway, runner, script=_ALWAYS_FAILS_SCRIPT)

    await agent.handle_message(chat_id=CHAT, message_id=1, text="почини мне штуку", requester_id=1001)

    assert len(runner.calls) == 2
    followup_prompt = runner.calls[1]["prompt"]
    assert "broken_tool" in followup_prompt
    assert "NameError" in followup_prompt  # traceback реально дошёл, не обрезан до общей фразы
    assert "create_tool" in followup_prompt  # агент знает про возможность переписать код
    assert "1" in followup_prompt and "3" in followup_prompt  # счётчик попытки виден


async def test_gives_up_after_three_attempts_with_goal_and_full_history(tmp_path, secrets, registry, workspaces, state):
    runner = _ScriptedRunner([_propose_broken_tool_result("Пробую снова")])
    cfg = _config(tmp_path, secrets)
    gateway = _FakeGateway()
    agent = _agent_with_broken_tool(cfg, registry, workspaces, state, gateway, runner, script=_ALWAYS_FAILS_SCRIPT)

    await agent.handle_message(
        chat_id=CHAT, message_id=1, text="почини мне очень важную штуку", requester_id=1001
    )

    assert len(runner.calls) == 3  # ровно лимит, не бесконечный цикл

    giveup = gateway.messages[-1]
    assert "почини мне очень важную штуку" in giveup  # исходная задача
    assert giveup.count("NameError") == 3  # видны все три попытки, не только последняя
    assert (CHAT, "broken_tool") not in agent._custom_tool_failures  # счётчик очищен


async def test_success_after_retry_clears_the_failure_counter(tmp_path, secrets, registry, workspaces, state):
    runner = _ScriptedRunner([
        _propose_broken_tool_result("Пробую"),
        _propose_broken_tool_result("Пробую снова"),
        _plain_text_result("Готово, получилось."),
    ])
    cfg = _config(tmp_path, secrets)
    gateway = _FakeGateway()
    agent = _agent_with_broken_tool(cfg, registry, workspaces, state, gateway, runner, script=_FAILS_ONCE_SCRIPT)

    await agent.handle_message(chat_id=CHAT, message_id=1, text="почини", requester_id=1001)

    assert (CHAT, "broken_tool") not in agent._custom_tool_failures
    assert any("Готово, получилось" in m for m in gateway.messages)


async def test_create_tool_allows_overwriting_its_own_broken_custom_tool(tmp_path, secrets, registry, workspaces):
    from cortex.brain.tools.base import BrainToolContext
    from cortex.models import Action

    cfg = _config(tmp_path, secrets)
    store = CustomToolStore(
        scripts_dir=cfg.data_dir / "brain_tools", registry_path=cfg.data_dir / "custom_tools.json"
    )
    tools = BrainToolRegistry()
    create = CreateToolTool(store=store, registry=tools)
    tools.register(create)

    class _Deps:
        pass

    ctx = BrainToolContext(deps=_Deps(), chat_id=CHAT, requester_id=1001)

    first = await create.execute(
        Action(tool="create_tool", args={"name": "helper", "description": "v1", "code": "print(1)"}),
        ctx,
    )
    assert first.ok

    second = await create.execute(
        Action(tool="create_tool", args={"name": "helper", "description": "v2 fixed", "code": "print(2)"}),
        ctx,
    )
    assert second.ok
    assert tools.get("helper").description == "v2 fixed"
