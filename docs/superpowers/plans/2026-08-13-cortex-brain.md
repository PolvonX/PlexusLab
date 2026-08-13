# Cortex Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Cortex an LLM brain (Claude, via local `claude` CLI subprocess) that understands free-form natural language from the CEO and drives HR/project management through meta-tools, fully replacing the current slash-command interface.

**Architecture:** A third `agent_runner` driver (`claude`) reuses the existing `AgentRunner` subprocess machinery. A new `cortex/brain/` package builds context, runs the think→act loop, and dispatches a new set of meta-tools (HR, projects, task delegation) through a parallel `BrainToolRegistry` that mirrors the existing `cortex/tools/` pattern. `telegram/handlers.py` and `telegram/hiring.py` are removed; their logic moves into brain tools. `@Tag` addressing to a specific employee still bypasses the brain entirely and goes straight to `agy` — engineering work never touches Claude.

**Tech Stack:** Python 3.11, aiogram 3, existing Plexus Lab stack (no new dependencies — `claude` CLI is already installed and authenticated on this machine).

Spec: `docs/superpowers/specs/2026-08-13-cortex-brain-design.md`

## Global Constraints

- No new third-party Python packages. Everything reuses `aiogram`, `httpx`, `pyyaml`, `python-dotenv` already in `requirements.txt`.
- `claude` invocations never use `--bare` or `ANTHROPIC_API_KEY` — auth is the existing OAuth session on this machine.
- `claude` invocations always pass `--tools ""` — the brain never gets Claude Code's built-in Bash/Edit/Read tools, only our `<action>` contract.
- Every new module follows the existing layering rule: `cortex/brain/` may import `cortex/*` service modules but nothing in `cortex/runtime/` or `cortex/tools/parser.py` may import anything from `cortex/brain/` or `aiogram`.
- All new persistent state (`data/pending_actions.json`, `data/brain_sessions/`) is written atomically (`tmp` + `os.replace`), matching `EmployeeRegistry`/`ChatState`.
- Tests use `pytest -q` from the project root (`.venv/Scripts/python.exe -m pytest -q`); the suite currently has 70 passing tests and must stay green after every task.
- Russian-language user-facing strings and comments only where non-obvious, matching existing code style — no comments that restate the code.

---

## File Structure

```
config.yaml                          + brain: section, agent_runner.drivers.claude/mock_claude
cortex/config.py                     + Config.brain_* properties
cortex/runtime/runner.py             ~ AgentRunner.run() gains system_prompt/session_flag kwargs
cortex/tools/shell.py                NEW shared shell-exec helper (extracted from execute_command.py)
cortex/tools/execute_command.py      ~ refactored to use tools/shell.py
cortex/brain/__init__.py             NEW
cortex/brain/risk.py                 NEW RiskTier, Autonomy, requires_confirmation()
cortex/brain/session.py              NEW BrainSession (chat_id -> claude --session-id/--resume)
cortex/brain/pending.py              NEW PendingAction, PendingActionStore
cortex/brain/context.py              NEW BrainPromptBuilder
cortex/brain/agent.py                NEW BrainAgent (the think/act loop)
cortex/brain/tools/__init__.py       NEW
cortex/brain/tools/base.py           NEW BrainToolContext, BrainTool, BrainToolRegistry
cortex/brain/tools/read.py           NEW list_staff, get_employee, list_projects, get_status
cortex/brain/tools/hr.py             NEW hire_employee, write_job_description, fire_employee
cortex/brain/tools/projects.py       NEW create_project, link_project, unlink_project, archive_project, set_chat_project
cortex/brain/tools/work.py           NEW assign_task, set_listen, send_file, request_digest
cortex/brain/tools/shell_tool.py     NEW execute_command (brain-scoped, project-sandboxed)
prompts/cortex_brain.md              NEW static persona for the brain
cortex/telegram/gateway.py           ~ + Gateway.reply(), swap routers
cortex/telegram/brain_router.py      NEW message + callback_query handlers
cortex/telegram/handlers.py          DELETE (logic moved to brain tools)
cortex/telegram/hiring.py            DELETE (logic moved to brain/tools/hr.py)
cortex/deps.py                       ~ + brain, pending, session fields
cortex/app.py                        ~ wire brain tools + BrainAgent, drop handlers/hiring imports
scripts/mock_claude.py               NEW deterministic claude CLI stand-in for tests
tests/test_risk.py                   NEW
tests/test_brain_session.py          NEW
tests/test_pending.py                NEW
tests/test_brain_tools_read.py       NEW
tests/test_brain_tools_hr.py         NEW
tests/test_brain_tools_projects.py   NEW
tests/test_brain_tools_work.py       NEW
tests/test_brain_tools_shell.py      NEW
tests/test_brain_context.py          NEW
tests/test_brain_agent.py            NEW
tests/test_runner.py                 ~ + system_prompt/session_flag coverage
tests/test_execute_command.py        NEW (moved coverage for the shared shell helper)
tests/fixtures/echo_brain.py         NEW deterministic "claude" for agent-loop tests
```

---

### Task 1: Config — brain settings + `claude`/`mock_claude` drivers

**Files:**
- Modify: `config.yaml`
- Modify: `cortex/config.py:70-261` (add properties near the other `agent_runner`/`tools` properties)
- Test: `tests/test_config_brain.py`

**Interfaces:**
- Produces: `Config.brain_autonomy -> str`, `Config.brain_max_iterations -> int`, `Config.brain_risk_overrides -> dict[str, str]`, `Config.brain_model -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_brain.py
from __future__ import annotations

from cortex.config import Config


def _cfg(tmp_path, secrets, brain: dict | None = None) -> Config:
    raw = {
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "brain": brain or {},
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def test_brain_defaults(tmp_path, secrets):
    cfg = _cfg(tmp_path, secrets)
    assert cfg.brain_autonomy == "balanced"
    assert cfg.brain_max_iterations == 5
    assert cfg.brain_risk_overrides == {}
    assert cfg.brain_model == "sonnet"


def test_brain_overrides(tmp_path, secrets):
    cfg = _cfg(
        tmp_path,
        secrets,
        {
            "autonomy": "cautious",
            "max_iterations": 3,
            "model": "opus",
            "risk_overrides": {"execute_command": "risky"},
        },
    )
    assert cfg.brain_autonomy == "cautious"
    assert cfg.brain_max_iterations == 3
    assert cfg.brain_model == "opus"
    assert cfg.brain_risk_overrides == {"execute_command": "risky"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_brain.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'brain_autonomy'`

- [ ] **Step 3: Add the properties**

In `cortex/config.py`, add after the existing `synapse_name` property (end of class):

```python
    # --- brain (Cortex-на-Claude) ---
    @property
    def brain(self) -> dict[str, Any]:
        return self.section("brain")

    @property
    def brain_autonomy(self) -> str:
        return str(self.brain.get("autonomy", "balanced"))

    @property
    def brain_max_iterations(self) -> int:
        return int(self.brain.get("max_iterations", 5))

    @property
    def brain_model(self) -> str:
        return str(self.brain.get("model", "sonnet"))

    @property
    def brain_risk_overrides(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.brain.get("risk_overrides") or {}).items()}
```

**Why a separate driver lookup:** employees always run on `agy`, the brain
always runs on `claude` — these must be selectable independently, not one
global `agent_runner.driver` switch. Replace the existing `runner_driver`
property with a version that delegates to a shared `_load_driver` helper,
and add `brain_driver` next to it (same file, `cortex/config.py`):

```python
    # --- runner: делится по назначению, не по одному глобальному переключателю ---
    def _load_driver(self, name: str) -> RunnerDriver:
        drivers = self.section("agent_runner").get("drivers", {}) or {}
        spec = drivers.get(name)
        if not spec:
            raise ConfigError(f"Драйвер '{name}' не описан в agent_runner.drivers")
        if not spec.get("command"):
            raise ConfigError(f"У драйвера '{name}' не задан command")
        return RunnerDriver(
            name=name,
            command=spec["command"],
            prompt_via_stdin=bool(spec.get("prompt_via_stdin", False)),
            env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
        )

    @property
    def runner_driver(self) -> RunnerDriver:
        """Драйвер сотрудников (agy). PLEXUS_FORCE_DRIVER=mock — для отладки."""
        name = os.getenv("PLEXUS_FORCE_DRIVER") or self.section("agent_runner").get("driver", "agy")
        return self._load_driver(name)

    @property
    def brain_driver(self) -> RunnerDriver:
        """Драйвер мозга (claude), независим от драйвера сотрудников.
        PLEXUS_BRAIN_DRIVER=mock_claude — для отладки без живого claude."""
        name = os.getenv("PLEXUS_BRAIN_DRIVER") or "claude"
        return self._load_driver(name)
```

This replaces the body of the existing `runner_driver` property in place —
delete its old body (the inline lookup) and keep only the one-line version
above; the raised-error behavior for a missing/misconfigured driver is
unchanged, just shared between both properties now.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_brain.py -v`
Expected: PASS (2 tests)

- [ ] **Step 4b: Add a regression test for the driver split**

Append to `tests/test_config_brain.py`:

```python
def test_brain_driver_is_independent_of_employee_driver(tmp_path, secrets):
    raw = {
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "agy",
            "drivers": {
                "agy": {"command": "agy -p {prompt}"},
                "claude": {"command": "claude -p {prompt}"},
            },
        },
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    assert cfg.runner_driver.name == "agy"
    assert cfg.brain_driver.name == "claude"
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_brain.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add `brain:` section and `claude`/`mock_claude` drivers to `config.yaml`**

Add after the `synapse:` section at the end of `config.yaml`:

```yaml
# --- Мозг Cortex (Claude поверх claude CLI) --------------------
brain:
  # cautious | balanced | autonomous — см. docs/superpowers/specs/2026-08-13-cortex-brain-design.md
  autonomy: "balanced"
  max_iterations: 5
  model: "sonnet"
  risk_overrides: {}
```

Add two new entries under `agent_runner.drivers` (next to `agy` and `mock`):

```yaml
    claude:
      # {session_flag} = "--session-id <uuid>" на первом обращении из чата,
      # "--resume <uuid>" на последующих — подставляет cortex/brain/session.py.
      # {system_prompt} идёт через ту же безопасную подстановку argv, что и {prompt}.
      command: >
        claude -p "{prompt}" --output-format text
        --system-prompt "{system_prompt}" --tools ""
        --model sonnet {session_flag}
      prompt_via_stdin: false
      env: {}

    mock_claude:
      command: 'python "{root}/scripts/mock_claude.py" --prompt-file "{prompt_file}"'
      prompt_via_stdin: false
      env: {}
```

- [ ] **Step 6: Run full suite to confirm nothing broke**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass (72 total)

- [ ] **Step 7: Commit**

```bash
git add config.yaml cortex/config.py tests/test_config_brain.py
git commit -m "feat: add brain config section and claude/mock_claude runner drivers"
```

---

### Task 2: Runner — `system_prompt` and `session_flag` support

**Files:**
- Modify: `cortex/runtime/runner.py`
- Test: `tests/test_runner.py` (append)

**Interfaces:**
- Consumes: existing `AgentRunner.__init__(config: Config)`.
- Produces: `AgentRunner.run(*, prompt: str, workspace: Path, agent: str, project: str, timeout: int | None = None, system_prompt: str | None = None, session_flag: str = "") -> AgentResult`. Existing callers (`orchestrator.py`) that don't pass the two new kwargs are unaffected — both default to "no-op" values.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runner.py`:

```python
async def test_system_prompt_is_passed_through_safely(tmp_path, secrets):
    """system_prompt reaches the child process even with spaces/quotes, same
    trick as {prompt}: it must not be shell-tokenized."""
    echo_argv = tmp_path / "echo_argv.py"
    echo_argv.write_text(
        "import sys\n"
        "print('ARGC=' + str(len(sys.argv)))\n"
        "print('SYS=' + sys.argv[sys.argv.index('--system-prompt') + 1])\n",
        encoding="utf-8",
    )
    cfg = _config_with(
        tmp_path,
        secrets,
        f'"{sys.executable}" "{echo_argv}" --system-prompt "{{system_prompt}}"',
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="x",
        workspace=workspace,
        agent="Cortex",
        project="sports_api",
        system_prompt='Ты Cortex. Говори "коротко и по делу".\nВторая строка.',
    )

    assert 'SYS=Ты Cortex. Говори "коротко и по делу".' in result.stdout


async def test_session_flag_is_appended_as_plain_tokens(tmp_path, secrets):
    echo_argv = tmp_path / "echo_argv.py"
    echo_argv.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n", encoding="utf-8")
    cfg = _config_with(
        tmp_path, secrets, f'"{sys.executable}" "{echo_argv}" {{session_flag}}'
    )
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    result = await AgentRunner(cfg).run(
        prompt="x",
        workspace=workspace,
        agent="Cortex",
        project="sports_api",
        session_flag="--resume 11111111-1111-1111-1111-111111111111",
    )

    assert "--resume 11111111-1111-1111-1111-111111111111" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_runner.py -k "system_prompt or session_flag" -v`
Expected: FAIL with `TypeError: run() got an unexpected keyword argument 'system_prompt'`

- [ ] **Step 3: Extend `AgentRunner`**

In `cortex/runtime/runner.py`, add a second mark constant near `_PROMPT_MARK`, and import `RunnerDriver` alongside the existing `Config` import:

```python
from ..config import Config, RunnerDriver
```

```python
_SYSTEM_PROMPT_MARK = "\x00PLEXUS_SYSTEM_PROMPT\x00"
```

Replace `_build_argv` with a version that also substitutes `system_prompt`
post-split, accepts `session_flag` as a normal (pre-split) placeholder, and
takes the driver explicitly instead of always reading
`self.config.runner_driver` — the brain passes its own driver (Task 1's
`Config.brain_driver`), employees keep getting the default:

```python
    def _build_argv(
        self,
        *,
        driver: RunnerDriver,
        prompt: str,
        prompt_file: Path,
        workspace: Path,
        agent: str,
        project: str,
        system_prompt: str,
        session_flag: str,
    ) -> list[str]:
        rendered = driver.command.format(
            prompt_file=str(prompt_file),
            prompt=_PROMPT_MARK,
            system_prompt=_SYSTEM_PROMPT_MARK,
            session_flag=session_flag,
            workspace=str(workspace),
            # cwd процесса — папка проекта, поэтому относительные пути к
            # скриптам самого Cortex здесь не работают: нужен {root}.
            root=str(self.config.root),
            agent=agent,
            project=project,
            model=os.getenv("AGY_MODEL", ""),
        )
        argv = shlex.split(rendered, posix=False)
        # shlex в non-posix режиме оставляет кавычки — снимаем их вручную.
        argv = [arg.strip('"') for arg in argv if arg]

        replacements = {_PROMPT_MARK: prompt, _SYSTEM_PROMPT_MARK: system_prompt}
        if not any(arg in replacements for arg in argv):
            return argv

        total = sum(len(a) for a in argv) + len(prompt) + len(system_prompt) + len(argv)
        if total > _ARGV_LIMIT:
            raise AgentRunError(
                f"Промпт не помещается в командную строку: {total} символов при "
                f"лимите Windows ~{_ARGV_LIMIT}. Уменьши context.history_chars_budget "
                "или context.workspace_tree_max_entries в config.yaml, либо переведи "
                "драйвер на prompt_via_stdin.",
                command=" ".join(a for a in argv if a not in replacements),
            )

        return [replacements.get(arg, arg) for arg in argv]
```

Update `run()` signature and its call to `_build_argv` — it now accepts an
optional `driver` override; when omitted it falls back to the existing
employee default, exactly preserving every current call site's behavior:

```python
    async def run(
        self,
        *,
        prompt: str,
        workspace: Path,
        agent: str,
        project: str,
        timeout: int | None = None,
        system_prompt: str | None = None,
        session_flag: str = "",
        driver: RunnerDriver | None = None,
    ) -> AgentResult:
        driver = driver or self.config.runner_driver
        timeout = timeout or self.config.runner_timeout

        prompt_file = self._tmp_dir / f"{agent}-{uuid.uuid4().hex[:8]}.md"
        prompt_file.write_text(prompt, encoding="utf-8")

        argv = self._build_argv(
            driver=driver,
            prompt=prompt,
            prompt_file=prompt_file,
            workspace=workspace,
            agent=agent,
            project=project,
            system_prompt=system_prompt or "",
            session_flag=session_flag,
        )
        # В лог и в отчёт об ошибке идёт команда без тела промпта.
        printable = " ".join(
            f"<промпт {len(prompt)} симв.>" if arg == prompt
            else f"<system_prompt {len(system_prompt or '')} симв.>" if system_prompt and arg == system_prompt
            else arg
            for arg in argv
        )
```

(The rest of `run()` is unchanged — `printable` is only used for logging/error reports.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_runner.py -v`
Expected: PASS (all runner tests, including the two new ones)

- [ ] **Step 4b: Add a regression test for the explicit `driver` override**

Append to `tests/test_runner.py`:

```python
async def test_explicit_driver_overrides_the_configured_default(tmp_path, secrets):
    """The brain passes its own driver (Config.brain_driver) — the default
    agent_runner.driver must not be consulted when one is given explicitly."""
    echo_argv = tmp_path / "echo_argv.py"
    echo_argv.write_text("import sys\nprint('OTHER_DRIVER_RAN')\n", encoding="utf-8")

    cfg = _config_with(tmp_path, secrets, "this-command-does-not-exist")
    workspace = tmp_path / "projects" / "sports_api"
    workspace.mkdir(parents=True)

    other = cfg.runner_driver  # copy the shape, swap the command
    from cortex.config import RunnerDriver

    override = RunnerDriver(
        name="other", command=f'"{sys.executable}" "{echo_argv}"', prompt_via_stdin=False, env={}
    )

    result = await AgentRunner(cfg).run(
        prompt="x", workspace=workspace, agent="Cortex", project="sports_api", driver=override
    )

    assert "OTHER_DRIVER_RAN" in result.stdout
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_runner.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add cortex/runtime/runner.py tests/test_runner.py
git commit -m "feat: let AgentRunner pass a safe system_prompt and session_flag to drivers"
```

---

### Task 3: `brain/risk.py` — risk tiers and the autonomy policy

**Files:**
- Create: `cortex/brain/__init__.py`
- Create: `cortex/brain/risk.py`
- Test: `tests/test_risk.py`

**Interfaces:**
- Produces: `RiskTier` (str Enum: `SAFE`, `NORMAL`, `RISKY`), `Autonomy` (str Enum: `CAUTIOUS`, `BALANCED`, `AUTONOMOUS`), `requires_confirmation(risk: RiskTier, autonomy: Autonomy) -> bool`, `resolve_risk(tool_name: str, default_risk: RiskTier, overrides: dict[str, str]) -> RiskTier`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_risk.py
from __future__ import annotations

import pytest

from cortex.brain.risk import Autonomy, RiskTier, requires_confirmation, resolve_risk


@pytest.mark.parametrize(
    "autonomy,risk,expected",
    [
        (Autonomy.CAUTIOUS, RiskTier.SAFE, False),
        (Autonomy.CAUTIOUS, RiskTier.NORMAL, True),
        (Autonomy.CAUTIOUS, RiskTier.RISKY, True),
        (Autonomy.BALANCED, RiskTier.SAFE, False),
        (Autonomy.BALANCED, RiskTier.NORMAL, False),
        (Autonomy.BALANCED, RiskTier.RISKY, True),
        (Autonomy.AUTONOMOUS, RiskTier.SAFE, False),
        (Autonomy.AUTONOMOUS, RiskTier.NORMAL, False),
        (Autonomy.AUTONOMOUS, RiskTier.RISKY, False),
    ],
)
def test_requires_confirmation_matrix(autonomy, risk, expected):
    assert requires_confirmation(risk, autonomy) is expected


def test_resolve_risk_uses_default_without_override():
    assert resolve_risk("hire_employee", RiskTier.NORMAL, {}) is RiskTier.NORMAL


def test_resolve_risk_applies_override():
    overrides = {"execute_command": "risky"}
    assert resolve_risk("execute_command", RiskTier.NORMAL, overrides) is RiskTier.RISKY


def test_resolve_risk_ignores_invalid_override():
    overrides = {"execute_command": "not_a_tier"}
    assert resolve_risk("execute_command", RiskTier.NORMAL, overrides) is RiskTier.NORMAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_risk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain'`

- [ ] **Step 3: Create the package and the module**

```python
# cortex/brain/__init__.py
"""Мозг Cortex: понимание естественного языка поверх claude CLI."""
```

```python
# cortex/brain/risk.py
"""Риск-политика мозга Cortex: какие действия исполняются сразу, а какие
ждут подтверждения CEO кнопками в Telegram.

Порог — не бинарный переключатель, а настройка (brain.autonomy в
config.yaml), см. docs/superpowers/specs/2026-08-13-cortex-brain-design.md.
"""

from __future__ import annotations

from enum import Enum


class RiskTier(str, Enum):
    SAFE = "safe"
    NORMAL = "normal"
    RISKY = "risky"


class Autonomy(str, Enum):
    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    AUTONOMOUS = "autonomous"


#: Какие уровни риска исполняются БЕЗ подтверждения в каждом режиме.
_AUTO_EXECUTE: dict[Autonomy, frozenset[RiskTier]] = {
    Autonomy.CAUTIOUS: frozenset({RiskTier.SAFE}),
    Autonomy.BALANCED: frozenset({RiskTier.SAFE, RiskTier.NORMAL}),
    Autonomy.AUTONOMOUS: frozenset({RiskTier.SAFE, RiskTier.NORMAL, RiskTier.RISKY}),
}


def parse_autonomy(value: str) -> Autonomy:
    try:
        return Autonomy(value.strip().lower())
    except ValueError:
        return Autonomy.BALANCED


def requires_confirmation(risk: RiskTier, autonomy: Autonomy) -> bool:
    return risk not in _AUTO_EXECUTE[autonomy]


def resolve_risk(tool_name: str, default_risk: RiskTier, overrides: dict[str, str]) -> RiskTier:
    """Персональный override из config.yaml важнее риска по умолчанию у
    инструмента. Некорректное значение override молча игнорируется —
    подставлять случайный риск опаснее, чем упасть на дефолт."""
    raw = overrides.get(tool_name)
    if raw is None:
        return default_risk
    try:
        return RiskTier(raw.strip().lower())
    except ValueError:
        return default_risk
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_risk.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add cortex/brain/__init__.py cortex/brain/risk.py tests/test_risk.py
git commit -m "feat: add brain risk-tier policy"
```

---

### Task 4: `brain/session.py` — session id per chat

**Files:**
- Create: `cortex/brain/session.py`
- Test: `tests/test_brain_session.py`

**Interfaces:**
- Produces: `BrainSession(data_dir: Path)` with `.session_id(chat_id: int) -> str`, `.session_flag(chat_id: int) -> str` (returns `"--session-id <uuid>"` first time, `"--resume <uuid>"` after `.mark_used()`), `.mark_used(chat_id: int) -> None`, `.reset(chat_id: int) -> None` (for the resume-failed fallback path).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_session.py
from __future__ import annotations

from cortex.brain.session import BrainSession


def test_session_id_is_deterministic_per_chat(tmp_path):
    session = BrainSession(tmp_path)
    first = session.session_id(-1003881673794)
    second = session.session_id(-1003881673794)
    assert first == second
    assert session.session_id(12345) != first


def test_first_call_uses_session_id_flag(tmp_path):
    session = BrainSession(tmp_path)
    flag = session.session_flag(-1003881673794)
    assert flag.startswith("--session-id ")
    assert session.session_id(-1003881673794) in flag


def test_second_call_resumes(tmp_path):
    session = BrainSession(tmp_path)
    chat_id = -1003881673794
    session.session_flag(chat_id)
    session.mark_used(chat_id)

    flag = session.session_flag(chat_id)
    assert flag.startswith("--resume ")


def test_reset_goes_back_to_session_id(tmp_path):
    session = BrainSession(tmp_path)
    chat_id = -1003881673794
    session.mark_used(chat_id)
    assert session.session_flag(chat_id).startswith("--resume ")

    session.reset(chat_id)
    assert session.session_flag(chat_id).startswith("--session-id ")


def test_marker_survives_a_new_instance(tmp_path):
    chat_id = 42
    BrainSession(tmp_path).mark_used(chat_id)

    fresh = BrainSession(tmp_path)
    assert fresh.session_flag(chat_id).startswith("--resume ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.session'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/session.py
"""Сессия claude на чат: экономия токенов через --resume.

id детерминированный (uuid5 от chat_id) — хранить нечего, кроме одного
факта: обращались ли к этому чату раньше. Он живёт в файле-метке рядом с
остальным состоянием Cortex, а не в памяти процесса — иначе рестарт сервера
заставил бы claude --resume биться о несуществующую (для процесса) сессию,
хотя на диске у claude она есть.
"""

from __future__ import annotations

import uuid
from pathlib import Path

#: Фиксированный namespace — иначе один и тот же chat_id давал бы разные
#: uuid между запусками процесса (uuid5 без namespace не детерминирован).
_NAMESPACE = uuid.UUID("6f2b6b3e-6d0a-4b1a-9f0a-2f1e8c9d7a10")


class BrainSession:
    """chat_id -> детерминированный session id claude + флаг «уже начата»."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "brain_sessions"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def session_id(self, chat_id: int) -> str:
        return str(uuid.uuid5(_NAMESPACE, str(chat_id)))

    def _marker(self, chat_id: int) -> Path:
        return self._dir / f"{chat_id}.seen"

    # ------------------------------------------------------------------
    def session_flag(self, chat_id: int) -> str:
        sid = self.session_id(chat_id)
        flag = "--resume" if self._marker(chat_id).exists() else "--session-id"
        return f"{flag} {sid}"

    def mark_used(self, chat_id: int) -> None:
        self._marker(chat_id).touch()

    def reset(self, chat_id: int) -> None:
        """Резюме сломалось (сессия потеряна на стороне claude) — начинаем
        с чистого листа и полной пересборки контекста из data/history/."""
        self._marker(chat_id).unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_session.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add cortex/brain/session.py tests/test_brain_session.py
git commit -m "feat: add per-chat claude session tracking for the brain"
```

---

### Task 5: `brain/pending.py` — confirmable actions store

**Files:**
- Create: `cortex/brain/pending.py`
- Test: `tests/test_pending.py`

**Interfaces:**
- Produces: `PendingAction` (dataclass: `id: str`, `chat_id: int`, `message_id: int | None`, `requester_id: int`, `tool: str`, `args: dict`, `risk: str`, `summary: str`, `created_at: str`), `PendingActionStore(path: Path)` with `.load() -> None`, `async .add(action: PendingAction) -> None`, `.get(action_id: str) -> PendingAction | None`, `async .pop(action_id: str) -> PendingAction | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pending.py
from __future__ import annotations

import json

import pytest

from cortex.brain.pending import PendingAction, PendingActionStore


def _action(action_id="p1", chat_id=-100500) -> PendingAction:
    return PendingAction(
        id=action_id,
        chat_id=chat_id,
        message_id=42,
        requester_id=1001,
        tool="fire_employee",
        args={"name": "Frontend_Dev"},
        risk="risky",
        summary="Уволить @Frontend_Dev",
    )


async def test_add_persists_to_disk(tmp_path):
    store = PendingActionStore(tmp_path / "pending_actions.json")
    await store.add(_action())

    raw = json.loads((tmp_path / "pending_actions.json").read_text(encoding="utf-8"))
    assert raw["actions"][0]["id"] == "p1"


async def test_get_and_pop(tmp_path):
    store = PendingActionStore(tmp_path / "pending_actions.json")
    await store.add(_action())

    assert store.get("p1") is not None
    popped = await store.pop("p1")
    assert popped.tool == "fire_employee"
    assert store.get("p1") is None


async def test_pop_missing_returns_none(tmp_path):
    store = PendingActionStore(tmp_path / "pending_actions.json")
    assert await store.pop("does-not-exist") is None


async def test_state_survives_reload(tmp_path):
    path = tmp_path / "pending_actions.json"
    store = PendingActionStore(path)
    await store.add(_action())

    reloaded = PendingActionStore(path)
    reloaded.load()
    assert reloaded.get("p1") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pending.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.pending'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/pending.py
"""Действия мозга, ждущие подтверждения CEO кнопками в Telegram.

Тот же паттерн атомарной записи, что у ChatState/EmployeeRegistry: tmp +
os.replace под asyncio.Lock, переживает перезапуск процесса.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import utcnow_iso


@dataclass(slots=True)
class PendingAction:
    id: str
    chat_id: int
    message_id: int | None
    requester_id: int
    tool: str
    args: dict[str, Any]
    risk: str
    summary: str
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "requester_id": self.requester_id,
            "tool": self.tool,
            "args": self.args,
            "risk": self.risk,
            "summary": self.summary,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PendingAction":
        return cls(
            id=raw["id"],
            chat_id=raw["chat_id"],
            message_id=raw.get("message_id"),
            requester_id=raw["requester_id"],
            tool=raw["tool"],
            args=raw.get("args") or {},
            risk=raw.get("risk", "risky"),
            summary=raw.get("summary", ""),
            created_at=raw.get("created_at") or utcnow_iso(),
        )


class PendingActionStore:
    """Реестр отложенных действий — маленький аналог EmployeeRegistry."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._actions: dict[str, PendingAction] = {}
        self._lock = asyncio.Lock()
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self._actions = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._actions = {}
            return
        self._actions = {
            entry["id"]: PendingAction.from_dict(entry) for entry in raw.get("actions", [])
        }

    def get(self, action_id: str) -> PendingAction | None:
        return self._actions.get(action_id)

    # ------------------------------------------------------------------
    async def add(self, action: PendingAction) -> None:
        async with self._lock:
            self._actions[action.id] = action
            self._write_unlocked()

    async def pop(self, action_id: str) -> PendingAction | None:
        async with self._lock:
            action = self._actions.pop(action_id, None)
            if action is not None:
                self._write_unlocked()
            return action

    # ------------------------------------------------------------------
    def _write_unlocked(self) -> None:
        payload = {"actions": [a.to_dict() for a in self._actions.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pending.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cortex/brain/pending.py tests/test_pending.py
git commit -m "feat: add persistent pending-action store for risky brain tools"
```

---

### Task 6: Extract a shared shell runner (`tools/shell.py`)

The brain's `execute_command` tool needs the same blocklist + subprocess +
truncation logic as the employee-side one. Extracting it now avoids
duplicating ~60 lines when Task 12 adds the brain version.

**Files:**
- Create: `cortex/tools/shell.py`
- Modify: `cortex/tools/execute_command.py` (shrinks to arg-resolution + delegation)
- Test: `tests/test_execute_command.py`

**Interfaces:**
- Produces: `assert_command_allowed(command: str, blocklist: list[re.Pattern[str]]) -> None` (raises `ToolError`), `shell_argv(command: str) -> list[str]`, `resolve_timeout(requested: Any, *, max_timeout: int, default: int = 180) -> int`, `async run_shell_command(command: str, *, cwd: Path, timeout: int, blocklist: list[re.Pattern[str]], log_tag: str) -> ToolResult`.
- Consumes (Task 12 will consume the same four names from `cortex.tools.shell`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execute_command.py
"""Юнит-покрытие общего раннера консольных команд (cortex/tools/shell.py) —
используется и employee execute_command, и его brain-версией (Task 12)."""

from __future__ import annotations

import re

import pytest

from cortex.errors import ToolError
from cortex.tools.shell import assert_command_allowed, resolve_timeout, run_shell_command

_BLOCKLIST = [re.compile(r"(?i)\brm\s+-rf\s+/")]


def test_blocklist_rejects_matching_command():
    with pytest.raises(ToolError, match="заблокирована"):
        assert_command_allowed("rm -rf /", _BLOCKLIST)


def test_blocklist_allows_safe_command():
    assert_command_allowed("git status", _BLOCKLIST)  # no raise


def test_resolve_timeout_clamps_to_max():
    assert resolve_timeout(99999, max_timeout=600) == 600


def test_resolve_timeout_falls_back_on_garbage():
    assert resolve_timeout("not-a-number", max_timeout=600, default=180) == 180


async def test_run_shell_command_success(tmp_path):
    result = await run_shell_command(
        "echo plexus", cwd=tmp_path, timeout=30, blocklist=[], log_tag="test"
    )
    assert result.ok
    assert "plexus" in result.detail


async def test_run_shell_command_nonzero_exit_is_failure_not_exception(tmp_path):
    result = await run_shell_command(
        "exit 3" if False else "python -c \"import sys; sys.exit(3)\"",
        cwd=tmp_path, timeout=30, blocklist=[], log_tag="test",
    )
    assert not result.ok
    assert "3" in result.summary


async def test_run_shell_command_timeout_is_reported(tmp_path):
    result = await run_shell_command(
        'python -c "import time; time.sleep(5)"',
        cwd=tmp_path, timeout=1, blocklist=[], log_tag="test",
    )
    assert not result.ok
    assert "не уложилась" in result.summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_execute_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.tools.shell'`

- [ ] **Step 3: Create `cortex/tools/shell.py`**

```python
# cortex/tools/shell.py
"""Общий раннер консольных команд.

Используется employee-версией execute_command и её brain-аналогом
(cortex/brain/tools/shell_tool.py): blocklist, запуск, таймаут, обрезка
вывода — одна и та же логика, разное только то, откуда берётся cwd.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..logging_setup import get_logger
from ..models import ToolResult

log = get_logger("tools.shell")

_MAX_REPORT_CHARS = 2500


def assert_command_allowed(command: str, blocklist: list[re.Pattern[str]]) -> None:
    for pattern in blocklist:
        if pattern.search(command):
            log.warning("Заблокирована команда: %s", command)
            raise ToolError(
                "команда заблокирована политикой безопасности Plexus Lab "
                f"(правило: {pattern.pattern})"
            )


def shell_argv(command: str) -> list[str]:
    """Запускаем через системную оболочку — агенты пишут пайпы и &&."""
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", command]
    shell = os.environ.get("SHELL", "/bin/sh")
    return [shell, "-c", command]


def resolve_timeout(requested: Any, *, max_timeout: int, default: int = 180) -> int:
    try:
        timeout = int(requested)
    except (TypeError, ValueError):
        timeout = default
    return max(1, min(timeout, max_timeout))


async def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    blocklist: list[re.Pattern[str]],
    log_tag: str,
) -> ToolResult:
    """Blocklist -> запуск -> обрезка вывода -> ToolResult.

    Падение самой команды (ненулевой код, таймаут) — ожидаемый исход и
    возвращается как ToolResult.failure, не как исключение. Исключение
    (ToolError) — только если команда в blocklist или процесс не запустился.
    """
    assert_command_allowed(command, blocklist)

    argv = shell_argv(command)
    log.info("[%s] $ %s", log_tag, command)

    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except OSError as exc:
        raise ToolError(f"не удалось запустить команду: {exc}") from exc

    try:
        output_b, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return ToolResult.failure(
            f"Команда не уложилась в {timeout} с и была снята", f"$ {command}"
        )

    duration = time.monotonic() - started
    output = output_b.decode("utf-8", errors="replace").strip()
    truncated = len(output) > _MAX_REPORT_CHARS
    if truncated:
        output = output[:_MAX_REPORT_CHARS] + "\n… (вывод обрезан)"

    detail = f"$ {command}\n\n{output or '(пустой вывод)'}"
    if process.returncode == 0:
        return ToolResult.success(f"Команда выполнена за {duration:.1f} с", detail)
    return ToolResult.failure(f"Команда завершилась с кодом {process.returncode}", detail)
```

- [ ] **Step 4: Refactor `cortex/tools/execute_command.py` to delegate to it**

Replace the whole file:

```python
# cortex/tools/execute_command.py
"""execute_command — запуск консольных команд в песочнице проекта.

Самый опасный инструмент компании, поэтому три уровня защиты:
  1. blocklist регексов из config.yaml (rm -rf /, format C:, shutdown…);
  2. cwd жёстко прибит к папке проекта, выход наружу блокируется;
  3. таймаут и обрезка вывода — чтобы `npm install` не съел чат и память.

Сам запуск — в tools/shell.py, общий с brain-версией этого инструмента.
"""

from __future__ import annotations

from ..errors import ToolError
from ..models import Action, ToolResult
from .base import Tool, ToolContext
from .shell import resolve_timeout, run_shell_command


class ExecuteCommandTool(Tool):
    name = "execute_command"
    description = (
        "Выполнить команду в терминале внутри папки твоего проекта: git, npm, "
        "python, создание файлов, yt-dlp и прочее."
    )
    usage = '{"tool": "execute_command", "args": {"command": "git status", "timeout": 120}}'

    async def execute(self, action: Action, ctx: ToolContext) -> ToolResult:
        command = ctx.arg(action.args, "command", "cmd", "shell", required=True)
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        command = str(command).strip()
        if not command:
            raise ToolError("пустая команда")

        cwd = ctx.workspaces.resolve_path(
            ctx.project,
            str(ctx.arg(action.args, "cwd", "dir", "workdir", default=".")),
            allow_escape=ctx.config.allow_escape_workspace,
        )
        timeout = resolve_timeout(
            ctx.arg(action.args, "timeout", "timeout_seconds", default=180),
            max_timeout=ctx.config.max_command_timeout,
        )

        return await run_shell_command(
            command,
            cwd=cwd,
            timeout=timeout,
            blocklist=ctx.config.command_blocklist,
            log_tag=f"{ctx.project.name}/{ctx.employee.name}",
        )
```

- [ ] **Step 5: Run the new test and the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_execute_command.py -v`
Expected: PASS (7 tests)

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass, including `test_orchestrator.py`'s `execute_command`-dependent tests (behavior is unchanged, only relocated)

- [ ] **Step 6: Commit**

```bash
git add cortex/tools/shell.py cortex/tools/execute_command.py tests/test_execute_command.py
git commit -m "refactor: extract shared shell runner out of execute_command"
```

---

### Task 7: `brain/tools/base.py` — BrainTool contract and registry

Mirrors `cortex/tools/base.py` but for the brain: no per-employee tool
policy (every meta-tool is available to the CEO uniformly), and each tool
declares its own default `RiskTier` instead of getting one from config.

**Files:**
- Create: `cortex/brain/tools/__init__.py`
- Create: `cortex/brain/tools/base.py`
- Test: `tests/test_brain_tools_base.py`

**Interfaces:**
- Consumes: `RiskTier` from `cortex.brain.risk` (Task 3), `Action`/`ToolResult` from `cortex.models`, `ToolError` from `cortex.errors`.
- Produces: `BrainToolContext` (dataclass: `deps: "Deps"`, `chat_id: int`, `requester_id: int`), `BrainTool` (ABC: `name: str`, `description: str`, `usage: str`, `risk: RiskTier`, `async execute(action, ctx) -> ToolResult`, `doc() -> str`), `BrainToolRegistry` (`.register`, `.register_all`, `.get(name) -> BrainTool | None`, `.risk_of(name) -> RiskTier | None`, `.docs() -> str`, `async .dispatch(action, ctx) -> ToolResult`). All subsequent brain-tool tasks (8-12) import from here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_tools_base.py
from __future__ import annotations

from cortex.brain.risk import RiskTier
from cortex.brain.tools.base import BrainTool, BrainToolContext, BrainToolRegistry
from cortex.models import Action, ToolResult


class _Echo(BrainTool):
    name = "echo"
    description = "test tool"
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        return ToolResult.success(f"echo: {action.args.get('text')}")


class _Boom(BrainTool):
    name = "boom"
    description = "always fails"
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        raise RuntimeError("kaboom")


def _ctx() -> BrainToolContext:
    return BrainToolContext(deps=object(), chat_id=-100500, requester_id=1001)


async def test_dispatch_runs_registered_tool():
    registry = BrainToolRegistry()
    registry.register(_Echo())

    result = await registry.dispatch(Action(tool="echo", args={"text": "hi"}), _ctx())
    assert result.ok
    assert "hi" in result.summary


async def test_dispatch_unknown_tool_fails_gracefully():
    registry = BrainToolRegistry()
    result = await registry.dispatch(Action(tool="nope", args={}), _ctx())
    assert not result.ok
    assert "не существует" in result.summary


async def test_dispatch_catches_exceptions():
    registry = BrainToolRegistry()
    registry.register(_Boom())

    result = await registry.dispatch(Action(tool="boom", args={}), _ctx())
    assert not result.ok
    assert "boom" in result.summary


async def test_dispatch_turns_domain_errors_into_failure_not_crash():
    """RegistryError/WorkspaceError и родня (CortexError) — ожидаемый исход,
    не падение инструмента: тулзы вроде get_employee вызывают
    registry.require() напрямую и полагаются на то, что его сообщение дойдёт
    до чата как есть, а не утонет в трейсбеке."""
    from cortex.errors import RegistryError

    class _NotFound(BrainTool):
        name = "not_found"
        description = "raises a domain error"
        risk = RiskTier.SAFE

        async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
            raise RegistryError("Сотрудник 'Ghost' не найден")

    registry = BrainToolRegistry()
    registry.register(_NotFound())

    result = await registry.dispatch(Action(tool="not_found", args={}), _ctx())
    assert not result.ok
    assert "не найден" in result.summary


def test_risk_of_returns_declared_tier():
    registry = BrainToolRegistry()
    registry.register(_Boom())
    assert registry.risk_of("boom") is RiskTier.RISKY
    assert registry.risk_of("missing") is None


def test_docs_lists_registered_tools():
    registry = BrainToolRegistry()
    registry.register(_Echo())
    assert "echo" in registry.docs()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.tools'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/tools/__init__.py
"""Мета-инструменты мозга Cortex: HR, проекты, делегирование задач."""
```

```python
# cortex/brain/tools/base.py
"""Каркас мета-инструментов мозга — аналог cortex/tools/base.py, но без
персональной политики доступа: любой инструмент доступен CEO единообразно,
а решение «выполнить сразу или спросить» принимает risk.py на уровне
brain/agent.py, не сам реестр.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from ...errors import CortexError, ToolError
from ...logging_setup import get_logger
from ...models import Action, ToolResult
from ..risk import RiskTier

if TYPE_CHECKING:  # pragma: no cover
    from ...deps import Deps

log = get_logger("brain.tools")


@dataclass(slots=True)
class BrainToolContext:
    """Всё, что мета-инструменту нужно знать о вызове."""

    deps: "Deps"
    chat_id: int
    requester_id: int


class BrainTool(ABC):
    name: str = ""
    description: str = ""
    usage: str = ""
    #: Риск по умолчанию — переопределяется per-tool через
    #: config.yaml -> brain.risk_overrides (см. cortex/brain/risk.py).
    risk: RiskTier = RiskTier.NORMAL

    @abstractmethod
    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        ...

    def doc(self) -> str:
        lines = [f"- **{self.name}** ({self.risk.value}) — {self.description}"]
        if self.usage:
            lines.append(f"  Пример: `{self.usage}`")
        return "\n".join(lines)


class BrainToolRegistry:
    """Набор мета-инструментов мозга."""

    def __init__(self) -> None:
        self._tools: dict[str, BrainTool] = {}

    def register(self, tool: BrainTool) -> None:
        if not tool.name:
            raise ToolError(f"У инструмента {type(tool).__name__} не задано имя")
        self._tools[tool.name] = tool
        log.debug("Зарегистрирован инструмент мозга %s (%s)", tool.name, tool.risk.value)

    def register_all(self, tools: Iterable[BrainTool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> BrainTool | None:
        return self._tools.get(name)

    def risk_of(self, name: str) -> RiskTier | None:
        tool = self._tools.get(name)
        return tool.risk if tool else None

    def docs(self) -> str:
        if not self._tools:
            return "- (инструментов нет)"
        return "\n".join(t.doc() for t in sorted(self._tools.values(), key=lambda t: t.name))

    # ------------------------------------------------------------------
    async def dispatch(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        tool = self._tools.get(action.tool)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "нет ни одного"
            return ToolResult.failure(
                f"Инструмент '{action.tool}' не существует", f"Доступные инструменты мозга: {known}"
            )
        try:
            return await tool.execute(action, ctx)
        except CortexError as exc:
            # ToolError и родня (RegistryError, WorkspaceError...) — ожидаемый
            # исход: инструменты вызывают registry.require()/workspaces.require()
            # напрямую и полагаются на то, что их сообщение дойдёт до чата как есть.
            return ToolResult.failure(f"{action.tool}: {exc}")
        except Exception as exc:  # noqa: BLE001 — падение инструмента не роняет Cortex
            log.exception("Инструмент мозга %s упал", action.tool)
            return ToolResult.failure(
                f"{action.tool} упал: {type(exc).__name__}", str(exc)[:500]
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_base.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add cortex/brain/tools/__init__.py cortex/brain/tools/base.py tests/test_brain_tools_base.py
git commit -m "feat: add BrainTool/BrainToolRegistry scaffolding"
```

---

### Task 8: `brain/tools/read.py` — safe read-only tools

**Files:**
- Create: `cortex/brain/tools/read.py`
- Test: `tests/test_brain_tools_read.py`

**Interfaces:**
- Consumes: `BrainTool`/`BrainToolContext` (Task 7), `Deps.registry`/`.workspaces`/`.state`/`.scheduler`/`.config`/`.uptime_seconds`/`.tools` (all pre-existing on `Deps`).
- Produces: `ListStaffTool`, `GetEmployeeTool`, `ListProjectsTool`, `GetStatusTool` — registered in Task 18.

A lightweight fake `Deps` (plain object with the needed attributes) is used
in the test instead of the real one, to keep this test free of Telegram/
subprocess setup — same spirit as `FakeBotPool` in `tests/test_orchestrator.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_tools_read.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.read import GetEmployeeTool, GetStatusTool, ListProjectsTool, ListStaffTool
from cortex.errors import RegistryError
from cortex.models import Action


@dataclass
class _FakeDeps:
    registry: object
    workspaces: object
    state: object
    scheduler: object
    config: object
    tools: object
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def uptime_seconds(self) -> float:
        return 0.0


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=-100500, requester_id=1001)


async def test_list_staff_reports_empty(config, registry, workspaces, state):
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)
    result = await ListStaffTool().execute(Action(tool="list_staff", args={}), _ctx(deps))
    assert result.ok
    assert "пуст" in result.summary


async def test_list_staff_lists_employees(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)

    result = await ListStaffTool().execute(Action(tool="list_staff", args={}), _ctx(deps))
    assert "Frontend_Dev" in result.detail


async def test_get_employee_not_found_is_a_failure_not_a_crash(config, registry, workspaces, state):
    from cortex.tools import ToolRegistry

    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=ToolRegistry(config))
    result = await GetEmployeeTool().execute(
        Action(tool="get_employee", args={"name": "Ghost"}), _ctx(deps)
    )
    assert not result.ok
    assert "не найден" in result.summary


async def test_get_employee_found(config, registry, workspaces, state, frontend):
    from cortex.tools import ToolRegistry

    await registry.add(frontend)
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=ToolRegistry(config))

    result = await GetEmployeeTool().execute(
        Action(tool="get_employee", args={"name": "Frontend_Dev"}), _ctx(deps)
    )
    assert result.ok
    assert "Senior Frontend Engineer" in result.detail


async def test_list_projects_reports_empty(config, registry, workspaces, state):
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)
    result = await ListProjectsTool().execute(Action(tool="list_projects", args={}), _ctx(deps))
    assert "Ни одной" in result.detail


async def test_list_projects_marks_active_project_for_chat(config, registry, workspaces, state):
    workspaces.create("sports_api")
    await state.set_active_project(-100500, "sports_api")
    deps = _FakeDeps(registry, workspaces, state, scheduler=None, config=config, tools=None)

    result = await ListProjectsTool().execute(Action(tool="list_projects", args={}), _ctx(deps))
    assert "активный в этом чате" in result.detail


class _FakeScheduler:
    active: list = []


async def test_get_status_reports_no_active_tasks(config, registry, workspaces, state):
    deps = _FakeDeps(registry, workspaces, state, scheduler=_FakeScheduler(), config=config, tools=None)
    result = await GetStatusTool().execute(Action(tool="get_status", args={}), _ctx(deps))
    assert "Активных задач нет" in result.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_read.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.tools.read'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/tools/read.py
"""Инструменты только для чтения — RiskTier.SAFE, исполняются без
подтверждения при любом уровне autonomy."""

from __future__ import annotations

from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext


class ListStaffTool(BrainTool):
    name = "list_staff"
    description = "Список всех сотрудников: тег, должность, проект, статус."
    usage = '{"tool": "list_staff", "args": {}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        employees = ctx.deps.registry.all(include_inactive=True)
        if not employees:
            return ToolResult.success("Штат пуст", "В компании пока нет ни одного сотрудника.")

        lines = [
            f"- @{e.name} ({e.role}), проект по умолчанию: {e.default_project or '—'}, "
            f"статус: {'в строю' if e.active else 'уволен'}"
            for e in employees
        ]
        return ToolResult.success(f"В штате {len(employees)} сотрудник(ов)", "\n".join(lines))


class GetEmployeeTool(BrainTool):
    name = "get_employee"
    description = "Карточка одного сотрудника по тегу."
    usage = '{"tool": "get_employee", "args": {"name": "Frontend_Dev"}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        employee = ctx.deps.registry.require(str(action.args.get("name") or "").strip())
        allowed = ctx.deps.tools.allowed_names(employee)
        return ToolResult.success(
            f"Карточка @{employee.name}",
            f"Должность: {employee.role}\n"
            f"Статус: {'в строю' if employee.active else 'уволен'}\n"
            f"Бот: @{employee.username or '?'}\n"
            f"Проект по умолчанию: {employee.default_project or '—'}\n"
            f"Инструменты для задач через agy: {', '.join(allowed) or 'нет'}\n"
            f"Нанят: {employee.hired_at}",
        )


class ListProjectsTool(BrainTool):
    name = "list_projects"
    description = "Список рабочих сред и их статус (своя среда/подключённая папка)."
    usage = '{"tool": "list_projects", "args": {}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        projects = ctx.deps.workspaces.list()
        if not projects:
            return ToolResult.success("Проектов нет", "Ни одной рабочей среды ещё не создано.")

        active = ctx.deps.state.active_project(ctx.chat_id)
        lines = []
        for p in projects:
            marker = " (активный в этом чате)" if p.name == active else ""
            kind = f"подключён из {p.real_path}" if p.linked else "своя среда"
            lines.append(f"- {p.name}{marker}: {kind}")
        return ToolResult.success(f"{len(projects)} проект(ов)", "\n".join(lines))


class GetStatusTool(BrainTool):
    name = "get_status"
    description = "Что сейчас выполняется, аптайм, активный драйвер сабагентов."
    usage = '{"tool": "get_status", "args": {}}'
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        deps = ctx.deps
        active = deps.scheduler.active
        uptime = deps.uptime_seconds
        hours, remainder = divmod(int(uptime), 3600)
        minutes = remainder // 60

        lines = [
            f"Аптайм: {hours} ч {minutes} мин",
            f"Штат: {len(deps.registry.all())} · Проектов: {len(deps.workspaces.list())}",
            f"Драйвер сабагентов: {deps.config.runner_driver.name}",
        ]
        if not active:
            lines.append("Активных задач нет.")
        else:
            lines.append(f"В работе ({len(active)}):")
            lines += [
                f"  {t.task_id} @{t.agent} -> {t.project} · {t.state} · {t.elapsed:.0f} с"
                for t in active
            ]
        return ToolResult.success("Статус Plexus Lab", "\n".join(lines))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_read.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/brain/tools/read.py tests/test_brain_tools_read.py
git commit -m "feat: add read-only brain tools (list_staff, get_employee, list_projects, get_status)"
```

---

### Task 9: `brain/tools/hr.py` — hire_employee, write_job_description, fire_employee

**Files:**
- Create: `cortex/brain/tools/hr.py`
- Test: `tests/test_brain_tools_hr.py`

**Interfaces:**
- Consumes: `Deps.hr: HRService` (`.hire(HireRequest) -> Employee`, `.fire(name, hard) -> Employee`, both pre-existing, see `cortex/hr.py`), `Deps.registry`, `Deps.gateway` (`Gateway | None`, `.start_listener(employee) -> bool`).
- Produces: `HireEmployeeTool` (risk=NORMAL), `WriteJobDescriptionTool` (risk=NORMAL), `FireEmployeeTool` (risk=RISKY).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_tools_hr.py
from __future__ import annotations

from dataclasses import dataclass

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.hr import FireEmployeeTool, HireEmployeeTool, WriteJobDescriptionTool
from cortex.hr import HRService
from cortex.models import Action
from cortex.telegram.bot_pool import BotPool


class _FakeBot:
    async def get_me(self):
        @dataclass
        class Me:
            id: int = 777
            username: str = "frontend_dev_bot"

        return Me()

    async def session_close(self):
        return None


class _FakeBotPool(BotPool):
    def __init__(self, registry):
        super().__init__(registry)

    @staticmethod
    def _make_bot(token: str):
        return _FakeBot()


@dataclass
class _FakeDeps:
    registry: object
    hr: object
    gateway: object = None


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=-100500, requester_id=1001)


async def test_hire_employee_creates_and_verifies(config, registry, tmp_path):
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await HireEmployeeTool().execute(
        Action(
            tool="hire_employee",
            args={"name": "Frontend_Dev", "role": "Senior Frontend Engineer", "token": "1:AAA"},
        ),
        _ctx(deps),
    )

    assert result.ok
    assert registry.require("Frontend_Dev").role == "Senior Frontend Engineer"


async def test_hire_employee_missing_args_is_a_failure(config, registry):
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await HireEmployeeTool().execute(
        Action(tool="hire_employee", args={"name": "Frontend_Dev"}), _ctx(deps)
    )
    assert not result.ok


async def test_write_job_description_replace(config, registry, frontend):
    await registry.add(frontend)
    deps = _FakeDeps(registry=registry, hr=None)

    content = "# Frontend Dev\n\n" + "x" * 100
    result = await WriteJobDescriptionTool().execute(
        Action(
            tool="write_job_description",
            args={"name": "Frontend_Dev", "mode": "replace", "content": content},
        ),
        _ctx(deps),
    )
    assert result.ok
    assert registry.read_prompt(frontend).startswith("# Frontend Dev")


async def test_write_job_description_append(config, registry, frontend):
    await registry.add(frontend)
    registry.write_prompt(frontend, "# Версия 1\n" + "x" * 100, backup_dir=config.data_dir / "b")
    deps = _FakeDeps(registry=registry, hr=None)

    result = await WriteJobDescriptionTool().execute(
        Action(
            tool="write_job_description",
            args={"name": "Frontend_Dev", "mode": "append", "content": "## Урок\nПиши тесты."},
        ),
        _ctx(deps),
    )
    assert result.ok
    updated = registry.read_prompt(frontend)
    assert "# Версия 1" in updated
    assert "## Урок" in updated


async def test_write_job_description_too_short_is_rejected(config, registry, frontend):
    await registry.add(frontend)
    deps = _FakeDeps(registry=registry, hr=None)

    result = await WriteJobDescriptionTool().execute(
        Action(
            tool="write_job_description",
            args={"name": "Frontend_Dev", "mode": "replace", "content": "too short"},
        ),
        _ctx(deps),
    )
    assert not result.ok


async def test_fire_employee_soft(config, registry, frontend):
    await registry.add(frontend)
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await FireEmployeeTool().execute(
        Action(tool="fire_employee", args={"name": "Frontend_Dev"}), _ctx(deps)
    )
    assert result.ok
    assert "Frontend_Dev" not in [e.name for e in registry.all()]
    assert "Frontend_Dev" in [e.name for e in registry.all(include_inactive=True)]


async def test_fire_employee_hard(config, registry, frontend):
    await registry.add(frontend)
    bots = _FakeBotPool(registry)
    hr = HRService(config, registry, bots)
    deps = _FakeDeps(registry=registry, hr=hr)

    result = await FireEmployeeTool().execute(
        Action(tool="fire_employee", args={"name": "Frontend_Dev", "hard": True}), _ctx(deps)
    )
    assert result.ok
    assert registry.get("Frontend_Dev") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_hr.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.tools.hr'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/tools/hr.py
"""HR-инструменты мозга: найм, обучение (должностная инструкция), увольнение.

Найм остаётся диалогом по сути — Telegram не даёт ботам создавать ботов,
шаг с BotFather никуда не девается. Разница с прежним /hire в том, что
последовательность вопросов ведёт сам Claude по истории чата, а не
жёсткий FSM: он вызывает hire_employee одним действием, когда в переписке
уже есть тег, должность и токен.
"""

from __future__ import annotations

from ...errors import ToolError
from ...hr import HireRequest
from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

_MIN_LENGTH = 80
_MAX_LENGTH = 40_000


class HireEmployeeTool(BrainTool):
    name = "hire_employee"
    description = (
        "Нанять сотрудника: проверить токен через Telegram, сгенерировать "
        "должностную инструкцию, включить его на горячую. Токен получаешь у "
        "CEO после того, как он создаст бота в @BotFather."
    )
    usage = (
        '{"tool": "hire_employee", "args": {"name": "Frontend_Dev", '
        '"role": "Senior Frontend Engineer", "token": "123:ABC"}}'
    )
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        args = action.args
        name = str(args.get("name") or "").strip()
        role = str(args.get("role") or "").strip()
        token = str(args.get("token") or "").strip()
        if not (name and role and token):
            raise ToolError(
                "не хватает данных для найма: нужны name, role и token "
                f"(получено: {', '.join(k for k in ('name', 'role', 'token') if not str(args.get(k) or '').strip()) or 'ничего'} отсутствует)"
            )

        employee = await ctx.deps.hr.hire(HireRequest(name=name, role=role, token=token))

        hot_note = "слушатель шлюза подхватил его сразу"
        if employee.listen and ctx.deps.gateway is not None:
            await ctx.deps.gateway.start_listener(employee)
            hot_note = "поднят персональный polling-листенер"

        return ToolResult.success(
            f"Нанят @{employee.name} ({employee.role})",
            f"Инструкция сгенерирована, {hot_note} — перезапуск сервера не нужен. "
            f"Позвать его в группе: @{employee.name} задача…\n\n"
            "Скажи CEO удалить сообщение с токеном из чата.",
        )


class WriteJobDescriptionTool(BrainTool):
    name = "write_job_description"
    description = (
        "Обучить сотрудника: переписать (replace) или дополнить (append) его "
        "должностную инструкцию."
    )
    usage = (
        '{"tool": "write_job_description", "args": {"name": "Frontend_Dev", '
        '"mode": "append", "content": "## Урок\\nПеред коммитом гоняй тесты."}}'
    )
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        args = action.args
        name = str(args.get("name") or "").strip()
        if not name:
            raise ToolError("не указан сотрудник (args.name)")
        content = str(args.get("content") or "").strip()
        if not content:
            raise ToolError("пустое содержимое инструкции (args.content)")
        mode = str(args.get("mode") or "replace").lower()
        if mode not in ("replace", "append"):
            raise ToolError(f"неизвестный режим '{mode}', допустимы replace и append")

        employee = ctx.deps.registry.require(name)

        if mode == "append":
            existing = ctx.deps.registry.read_prompt(employee).rstrip()
            content = f"{existing}\n\n{content}"

        if len(content) < _MIN_LENGTH:
            raise ToolError(
                f"инструкция короче {_MIN_LENGTH} символов — это похоже на ошибку"
            )
        if len(content) > _MAX_LENGTH:
            raise ToolError(f"инструкция длиннее {_MAX_LENGTH} символов")

        backup_dir = ctx.deps.config.data_dir / "prompt_backups"
        path = ctx.deps.registry.write_prompt(employee, content, backup_dir=backup_dir)

        return ToolResult.success(
            f"Инструкция @{employee.name} обновлена ({mode})",
            f"Файл: {path.name}. Итоговый размер: {len(content)} символов. "
            "Предыдущая версия сохранена в data/prompt_backups.",
        )


class FireEmployeeTool(BrainTool):
    name = "fire_employee"
    description = (
        "Уволить сотрудника: мягко (остаётся в реестре, active=false) или "
        "жёстко (hard=true, запись и токен удаляются насовсем)."
    )
    usage = '{"tool": "fire_employee", "args": {"name": "Frontend_Dev", "hard": false}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            raise ToolError("не указан сотрудник (args.name)")
        hard = bool(action.args.get("hard", False))

        employee = await ctx.deps.hr.fire(name, hard=hard)
        return ToolResult.success(
            f"@{employee.name} уволен",
            "Запись удалена из реестра." if hard else "Переведён в неактивные, запись сохранена.",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_hr.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/brain/tools/hr.py tests/test_brain_tools_hr.py
git commit -m "feat: add hire/write_job_description/fire brain tools"
```

---

### Task 10: `brain/tools/projects.py` — project lifecycle tools

**Files:**
- Create: `cortex/brain/tools/projects.py`
- Test: `tests/test_brain_tools_projects.py`

**Interfaces:**
- Consumes: `Deps.workspaces: WorkspaceManager` (`.create`, `.link`, `.unlink`, `.archive`, `.require`, all pre-existing), `Deps.state: ChatState` (`.active_project`, `.set_active_project`), `Deps.config.data_dir`.
- Produces: `CreateProjectTool` (NORMAL), `LinkProjectTool` (NORMAL), `SetChatProjectTool` (NORMAL), `UnlinkProjectTool` (RISKY), `ArchiveProjectTool` (RISKY).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_tools_projects.py
from __future__ import annotations

from dataclasses import dataclass

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.projects import (
    ArchiveProjectTool,
    CreateProjectTool,
    LinkProjectTool,
    SetChatProjectTool,
    UnlinkProjectTool,
)
from cortex.models import Action

CHAT = -100500


@dataclass
class _FakeDeps:
    workspaces: object
    state: object
    config: object


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_create_project_sets_active(config, workspaces, state):
    deps = _FakeDeps(workspaces, state, config)
    result = await CreateProjectTool().execute(
        Action(tool="create_project", args={"name": "sports_api", "description": "API"}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("sports_api") is not None
    assert state.active_project(CHAT) == "sports_api"


async def test_link_project(config, workspaces, state, tmp_path):
    target = tmp_path / "external_repo"
    target.mkdir()
    deps = _FakeDeps(workspaces, state, config)

    result = await LinkProjectTool().execute(
        Action(tool="link_project", args={"name": "basehub", "path": str(target)}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("basehub").linked


async def test_set_chat_project(config, workspaces, state):
    workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, state, config)

    result = await SetChatProjectTool().execute(
        Action(tool="set_chat_project", args={"project": "sports_api"}), _ctx(deps)
    )
    assert result.ok
    assert state.active_project(CHAT) == "sports_api"


async def test_set_chat_project_clears_when_empty(config, workspaces, state):
    workspaces.create("sports_api")
    await state.set_active_project(CHAT, "sports_api")
    deps = _FakeDeps(workspaces, state, config)

    result = await SetChatProjectTool().execute(
        Action(tool="set_chat_project", args={"project": ""}), _ctx(deps)
    )
    assert result.ok
    assert state.active_project(CHAT) is None


async def test_unlink_project_clears_active_if_matching(config, workspaces, state, tmp_path):
    target = tmp_path / "external_repo"
    target.mkdir()
    workspaces.link("basehub", str(target))
    await state.set_active_project(CHAT, "basehub")
    deps = _FakeDeps(workspaces, state, config)

    result = await UnlinkProjectTool().execute(
        Action(tool="unlink_project", args={"name": "basehub"}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("basehub") is None
    assert target.exists()  # исходная папка цела
    assert state.active_project(CHAT) is None


async def test_archive_project(config, workspaces, state):
    workspaces.create("sports_api")
    await state.set_active_project(CHAT, "sports_api")
    deps = _FakeDeps(workspaces, state, config)

    result = await ArchiveProjectTool().execute(
        Action(tool="archive_project", args={"name": "sports_api"}), _ctx(deps)
    )
    assert result.ok
    assert workspaces.get("sports_api") is None
    assert state.active_project(CHAT) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_projects.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.tools.projects'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/tools/projects.py
"""Управление рабочими средами проектов из мозга."""

from __future__ import annotations

from ...errors import ToolError
from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext


class CreateProjectTool(BrainTool):
    name = "create_project"
    description = "Создать новую изолированную рабочую среду с нуля и закрепить её за этим чатом."
    usage = '{"tool": "create_project", "args": {"name": "sports_api", "description": "API спортивного сервиса"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            raise ToolError("не указано имя проекта (args.name)")
        description = str(action.args.get("description") or "")

        project = ctx.deps.workspaces.create(name, description)
        await ctx.deps.state.set_active_project(ctx.chat_id, project.name)
        return ToolResult.success(
            f"Проект {project.name} создан", f"Путь: {project.path}. Закреплён за этим чатом."
        )


class LinkProjectTool(BrainTool):
    name = "link_project"
    description = "Подключить существующую папку как проект (без копирования файлов)."
    usage = '{"tool": "link_project", "args": {"name": "basehub", "path": "C:\\\\Projects\\\\Basehub"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        path = str(action.args.get("path") or "").strip().strip('"')
        if not (name and path):
            raise ToolError("нужны name и path (args.name, args.path)")

        project = ctx.deps.workspaces.link(name, path, str(action.args.get("description") or ""))
        await ctx.deps.state.set_active_project(ctx.chat_id, project.name)
        return ToolResult.success(
            f"Проект {project.name} подключён", f"{project.path} -> junction -> {project.real_path}"
        )


class SetChatProjectTool(BrainTool):
    name = "set_chat_project"
    description = (
        "Закрепить проект за этим чатом (или снять закрепление пустым project) — "
        "задачи без явного #тега пойдут сюда."
    )
    usage = '{"tool": "set_chat_project", "args": {"project": "sports_api"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        raw = str(action.args.get("project") or "").strip()
        if not raw:
            await ctx.deps.state.set_active_project(ctx.chat_id, None)
            return ToolResult.success("Активный проект чата снят")

        project = ctx.deps.workspaces.require(raw)
        await ctx.deps.state.set_active_project(ctx.chat_id, project.name)
        return ToolResult.success(f"Чат закреплён за проектом {project.name}")


class UnlinkProjectTool(BrainTool):
    name = "unlink_project"
    description = "Отключить подключённую папку (junction). Файлы остаются на месте."
    usage = '{"tool": "unlink_project", "args": {"name": "basehub"}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            raise ToolError("не указано имя проекта (args.name)")

        target = ctx.deps.workspaces.unlink(name)
        if ctx.deps.state.active_project(ctx.chat_id) == name:
            await ctx.deps.state.set_active_project(ctx.chat_id, None)
        return ToolResult.success(
            f"Проект {name} отключён", f"Папка {target} не тронута — удалена только ссылка."
        )


class ArchiveProjectTool(BrainTool):
    name = "archive_project"
    description = "Убрать СВОЙ проект (не подключённую папку) в архив. Данные не удаляются."
    usage = '{"tool": "archive_project", "args": {"name": "sports_api"}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name:
            raise ToolError("не указано имя проекта (args.name)")

        target = ctx.deps.workspaces.archive(name, ctx.deps.config.data_dir / "archive")
        if ctx.deps.state.active_project(ctx.chat_id) == name:
            await ctx.deps.state.set_active_project(ctx.chat_id, None)
        return ToolResult.success(f"Проект {name} в архиве", f"Путь: {target}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_projects.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/brain/tools/projects.py tests/test_brain_tools_projects.py
git commit -m "feat: add project lifecycle brain tools"
```

---

### Task 11: `Gateway.reply()` — Cortex's own voice

The brain's own conversational text (and, in Task 12, `send_file`) must go
out through Cortex's own bot identity (`@TheCortexAI_bot`), not an
employee's token. `Gateway` already owns that bot instance (`_gateway_bot`,
used today only by `announce()`); this task exposes it properly.

**Files:**
- Modify: `cortex/telegram/gateway.py`
- Test: `tests/test_gateway_reply.py`

**Interfaces:**
- Produces: `Gateway.gateway_bot -> Bot` (property, raises `RuntimeError` if called before `start()`), `async Gateway.reply(chat_id: int, text: str, *, reply_to: int | None = None) -> None`, `async Gateway.ask_confirmation(*, chat_id: int, action_id: str, summary: str, risk: str) -> None`.

`ask_confirmation` takes primitive arguments, not a `PendingAction` —
`cortex/telegram/` must not import from `cortex/brain/` (that dependency
only ever points the other way, brain depending on telegram, matching
`orchestrator.py`'s existing relationship with `telegram/`). This keeps
`aiogram.types.InlineKeyboardMarkup` fully inside `telegram/`; `brain/agent.py`
(Task 15) never imports `aiogram` at all.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gateway_reply.py
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from cortex.telegram.gateway import Gateway


class _FakeGatewayBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.sent_with_markup: list[tuple] = []

    async def send_message(
        self, *, chat_id, text, reply_to_message_id=None, disable_web_page_preview=True,
        parse_mode=None, reply_markup=None, **_
    ):
        if reply_markup is not None:
            self.sent_with_markup.append((chat_id, text, reply_markup))
            return
        self.sent.append((chat_id, text, reply_to_message_id, parse_mode))


@dataclass
class _FakeConfig:
    max_message_length: int = 3800


@dataclass
class _FakeDeps:
    config: object = field(default_factory=_FakeConfig)


async def test_reply_before_start_raises():
    gateway = Gateway(_FakeDeps())
    with pytest.raises(RuntimeError):
        await gateway.reply(-100500, "hi")


async def test_reply_sends_via_gateway_bot():
    gateway = Gateway(_FakeDeps())
    fake_bot = _FakeGatewayBot()
    gateway._gateway_bot = fake_bot  # обходим start(): не поднимаем реальный aiogram.Bot

    await gateway.reply(-100500, "Привет, это Cortex", reply_to=42)

    assert fake_bot.sent == [(-100500, "Привет, это Cortex", 42, None)]


def test_gateway_bot_property_before_start_raises():
    gateway = Gateway(_FakeDeps())
    with pytest.raises(RuntimeError):
        _ = gateway.gateway_bot


def test_gateway_bot_property_after_start_like_assignment():
    gateway = Gateway(_FakeDeps())
    fake_bot = _FakeGatewayBot()
    gateway._gateway_bot = fake_bot
    assert gateway.gateway_bot is fake_bot


async def test_ask_confirmation_sends_buttons_with_action_id_in_callback_data():
    gateway = Gateway(_FakeDeps())
    fake_bot = _FakeGatewayBot()
    gateway._gateway_bot = fake_bot

    await gateway.ask_confirmation(
        chat_id=-100500, action_id="abc123", summary="Уволить @Frontend_Dev", risk="risky"
    )

    assert len(fake_bot.sent_with_markup) == 1
    chat_id, text, markup = fake_bot.sent_with_markup[0]
    assert "Уволить" in text
    buttons = [b for row in markup.inline_keyboard for b in row]
    callback_data = {b.callback_data for b in buttons}
    assert callback_data == {"brain:confirm:abc123", "brain:cancel:abc123"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gateway_reply.py -v`
Expected: FAIL with `AttributeError: 'Gateway' object has no attribute 'reply'`

- [ ] **Step 3: Implement**

In `cortex/telegram/gateway.py`, add to the imports:

```python
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .formatting import esc, split_message
```

Add these three members to the `Gateway` class (right after `announce`):

```python
    # ------------------------------------------------------------------
    @property
    def gateway_bot(self) -> Bot:
        """Собственный бот Cortex — им пользуется мозг, а не BotPool."""
        if self._gateway_bot is None:
            raise RuntimeError("Gateway.gateway_bot запрошен до Gateway.start()")
        return self._gateway_bot

    async def reply(self, chat_id: int, text: str, *, reply_to: int | None = None) -> None:
        """Ответ от лица самого Cortex (не сотрудника) — используется мозгом
        для своей реплики, а не отчёта об инструменте."""
        bot = self.gateway_bot
        chunks = split_message(text, self.deps.config.max_message_length)
        for index, chunk in enumerate(chunks):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to if index == 0 else None,
                    disable_web_page_preview=True,
                )
            except Exception as exc:  # noqa: BLE001 — битый HTML в ответе мозга
                log.warning("HTML не принят Telegram (%s), шлю без разметки", exc)
                await bot.send_message(
                    chat_id=chat_id, text=chunk, parse_mode=None, disable_web_page_preview=True
                )

    async def ask_confirmation(self, *, chat_id: int, action_id: str, summary: str, risk: str) -> None:
        """Кнопки подтверждения для рискованного действия мозга. Берёт
        только примитивы (не PendingAction) — telegram/ не должен знать про
        cortex/brain/, зависимость смотрит в обратную сторону."""
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ Выполнить", callback_data=f"brain:confirm:{action_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"brain:cancel:{action_id}"),
            ]]
        )
        await self.gateway_bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Подтверди действие ({esc(risk)}): {esc(summary)}",
            reply_markup=keyboard,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gateway_reply.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/telegram/gateway.py tests/test_gateway_reply.py
git commit -m "feat: let Gateway speak as Cortex itself (reply/gateway_bot)"
```

---

### Task 12: `brain/tools/work.py` — assign_task, set_listen, send_file, request_digest

**Files:**
- Create: `cortex/brain/tools/work.py`
- Test: `tests/test_brain_tools_work.py`

**Interfaces:**
- Consumes: `Deps.orchestrator` (`.new_task`, `.dispatch` — pre-existing, `cortex/orchestrator.py`), `Deps.workspaces`, `Deps.state`, `Deps.registry`, `Deps.gateway` (`.start_listener`, `.stop_listener`, `.gateway_bot` from Task 11), `Deps.synapse`, `Deps.bots`, `Deps.config`.
- Produces: `AssignTaskTool`, `SetListenTool`, `SendFileTool` (brain), `RequestDigestTool` — all `RiskTier.NORMAL`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_tools_work.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.work import AssignTaskTool, RequestDigestTool, SendFileTool, SetListenTool
from cortex.errors import ToolError
from cortex.models import Action, Employee

CHAT = -100500


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.dispatched: list[tuple] = []

    def new_task(self, **kwargs):
        return kwargs

    async def dispatch(self, task, *, requester_id):
        self.dispatched.append((task, requester_id))


class _FakeGateway:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def start_listener(self, employee) -> bool:
        self.started.append(employee.name)
        return True

    async def stop_listener(self, name: str) -> bool:
        self.stopped.append(name)
        return True


@dataclass
class _FakeDeps:
    orchestrator: object = None
    workspaces: object = None
    state: object = None
    registry: object = None
    gateway: object = None
    synapse: object = None
    bots: object = None
    config: object = None


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_assign_task_uses_explicit_project(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    workspaces.create("sports_api")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state, registry=registry)

    result = await AssignTaskTool().execute(
        Action(
            tool="assign_task",
            args={"employee": "Frontend_Dev", "project": "sports_api", "task": "почини хедер"},
        ),
        _ctx(deps),
    )
    await asyncio.sleep(0)  # дать шанс фоновой asyncio.create_task(...) выполниться
    assert result.ok
    assert len(orchestrator.dispatched) == 1


async def test_assign_task_falls_back_to_chat_active_project(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    workspaces.create("sports_api")
    await state.set_active_project(CHAT, "sports_api")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state, registry=registry)

    result = await AssignTaskTool().execute(
        Action(tool="assign_task", args={"employee": "Frontend_Dev", "task": "почини хедер"}),
        _ctx(deps),
    )
    assert result.ok


async def test_assign_task_without_any_project_hint_fails_clearly(config, registry, workspaces, state, frontend):
    await registry.add(frontend)
    workspaces.create("sports_api")
    workspaces.create("basehub")
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(orchestrator=orchestrator, workspaces=workspaces, state=state, registry=registry)

    result = await AssignTaskTool().execute(
        Action(tool="assign_task", args={"employee": "Frontend_Dev", "task": "почини хедер"}),
        _ctx(deps),
    )
    assert not result.ok
    assert "Непонятно" in result.summary or "проект" in result.summary.lower()


async def test_set_listen_on_calls_gateway(config, registry, frontend):
    await registry.add(frontend)
    gateway = _FakeGateway()
    deps = _FakeDeps(registry=registry, gateway=gateway)

    result = await SetListenTool().execute(
        Action(tool="set_listen", args={"name": "Frontend_Dev", "on": True}), _ctx(deps)
    )
    assert result.ok
    assert gateway.started == ["Frontend_Dev"]
    assert registry.require("Frontend_Dev").listen is True


async def test_send_file_rejects_missing_file(config, workspaces, tmp_path):
    workspaces.create("sports_api")
    gateway = _FakeGateway()
    gateway.gateway_bot = object()
    deps = _FakeDeps(workspaces=workspaces, gateway=gateway, config=config)

    with pytest.raises(ToolError):
        await SendFileTool().execute(
            Action(tool="send_file", args={"project": "sports_api", "path": "nope.txt"}), _ctx(deps)
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_work.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.tools.work'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/tools/work.py
"""Делегирование инженерной работы, персональные листенеры, файлы, дайджест.

assign_task — единственная точка, где мозг передаёт эстафету agy: дальше
работает штатный Orchestrator, Claude в это уже не вовлечён.
"""

from __future__ import annotations

import asyncio

from ...errors import ToolError, WorkspaceError
from ...models import Action, ToolResult
from ...workspace import Project
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

#: Ссылки на фоновые задачи — иначе сборщик мусора может прибить корутину
#: на полпути (тот же приём, что в telegram/handlers.py).
_BACKGROUND: set[asyncio.Task] = set()


def _resolve_project(ctx: BrainToolContext, employee, given: str) -> Project:
    if given:
        return ctx.deps.workspaces.require(given)

    active = ctx.deps.state.active_project(ctx.chat_id)
    if active and ctx.deps.workspaces.get(active):
        return ctx.deps.workspaces.require(active)

    if employee.default_project and ctx.deps.workspaces.get(employee.default_project):
        return ctx.deps.workspaces.require(employee.default_project)

    projects = ctx.deps.workspaces.list()
    if len(projects) == 1:
        return projects[0]

    raise WorkspaceError(
        "Непонятно, над каким проектом работать — укажи project явно, "
        "закрепи его за чатом (set_chat_project) или задай сотруднику "
        "default_project. "
        f"Доступны: {', '.join(p.name for p in projects) or 'ни одного'}"
    )


class AssignTaskTool(BrainTool):
    name = "assign_task"
    description = "Поставить инженерную задачу сотруднику — она уйдёт на agy, не на тебя."
    usage = '{"tool": "assign_task", "args": {"employee": "Frontend_Dev", "project": "sports_api", "task": "почини хедер"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        employee_name = str(action.args.get("employee") or "").strip()
        task_text = str(action.args.get("task") or "").strip()
        if not (employee_name and task_text):
            raise ToolError("нужны employee и task (args.employee, args.task)")

        employee = ctx.deps.registry.require(employee_name)
        project = _resolve_project(ctx, employee, str(action.args.get("project") or "").strip())

        deps = ctx.deps
        task = deps.orchestrator.new_task(
            employee=employee,
            project_name=project.name,
            instruction=task_text,
            chat_id=ctx.chat_id,
            message_id=0,
            requester="Cortex",
        )

        background = asyncio.create_task(
            deps.orchestrator.dispatch(task, requester_id=ctx.requester_id),
            name=f"brain-task:{getattr(task, 'task_id', 'mock')}",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

        return ToolResult.success(
            f"Задача передана @{employee.name}",
            f"Проект: {project.name}. Ответит сам, когда закончит.",
        )


class SetListenTool(BrainTool):
    name = "set_listen"
    description = "Включить/выключить персональный polling-листенер сотрудника."
    usage = '{"tool": "set_listen", "args": {"name": "Frontend_Dev", "on": true}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip()
        if not name or "on" not in action.args:
            raise ToolError("нужны name и on (args.name, args.on)")
        turn_on = bool(action.args["on"])

        employee = ctx.deps.registry.require(name)
        if ctx.deps.gateway is None:
            raise ToolError("шлюз ещё не поднят — попробуй через пару секунд")

        await ctx.deps.registry.update(employee.name, listen=turn_on)
        if turn_on:
            started = await ctx.deps.gateway.start_listener(employee)
            return ToolResult.success(
                f"@{employee.name} " + ("теперь слушает чат сам" if started else "уже слушал"),
                "Не забудь выключить ему privacy mode в BotFather.",
            )
        stopped = await ctx.deps.gateway.stop_listener(employee.name)
        return ToolResult.success(
            f"@{employee.name} " + ("больше не слушает" if stopped else "и так не слушал")
        )


class SendFileTool(BrainTool):
    name = "send_file"
    description = "Отправить файл из папки проекта в текущий чат от лица Cortex."
    usage = '{"tool": "send_file", "args": {"project": "sports_api", "path": "reports/audit.pdf"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        from aiogram.types import FSInputFile

        project_name = str(action.args.get("project") or "").strip()
        raw_path = str(action.args.get("path") or "").strip()
        if not (project_name and raw_path):
            raise ToolError("нужны project и path (args.project, args.path)")

        if ctx.deps.gateway is None:
            raise ToolError("шлюз ещё не поднят")

        project = ctx.deps.workspaces.require(project_name)
        path = ctx.deps.workspaces.resolve_path(
            project, raw_path, allow_escape=ctx.deps.config.allow_escape_workspace
        )
        if not path.exists() or path.is_dir():
            raise ToolError(f"файла '{raw_path}' нет в проекте {project.name}")

        caption = str(action.args.get("caption") or "")[:1000] or None
        try:
            await ctx.deps.gateway.gateway_bot.send_document(
                chat_id=ctx.chat_id,
                document=FSInputFile(path, filename=path.name),
                caption=caption,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Telegram не принял файл: {exc}") from exc

        return ToolResult.success(f"Файл {path.name} отправлен")


class RequestDigestTool(BrainTool):
    name = "request_digest"
    description = "Попросить Synapse собрать сводку инноваций (HackerNews) — по теме или общую."
    usage = '{"tool": "request_digest", "args": {"query": "rust wasm"}}'
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        deps = ctx.deps
        synapse = deps.registry.get(deps.config.synapse_name)
        if synapse is None or not synapse.active:
            raise ToolError(
                f"в штате нет активного {deps.config.synapse_name} — сначала найми его"
            )

        query = str(action.args.get("query") or "").strip()
        if query:
            stories = await deps.synapse.hackernews_search(query, limit=10)
            heading = f"Разведка Synapse: «{query}»"
        else:
            stories = await deps.synapse.hackernews_top()
            heading = "Сводка инноваций от Synapse"

        digest = deps.synapse.render_digest(stories, heading=heading)
        target_chat = (
            deps.config.secrets.ceo_dm_chat_id
            if deps.config.synapse.get("digest_target", "ceo_dm") == "ceo_dm"
            else ctx.chat_id
        )
        await deps.bots.say(synapse, target_chat, digest)

        return ToolResult.success(
            f"Synapse отправил сводку ({len(stories)} историй)",
            "В личку CEO" if target_chat != ctx.chat_id else "В этот чат",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_work.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/brain/tools/work.py tests/test_brain_tools_work.py
git commit -m "feat: add assign_task/set_listen/send_file/request_digest brain tools"
```

---

### Task 13: `brain/tools/shell_tool.py` — brain-scoped execute_command

The brain is not sandboxed to one project the way an employee is, so this
tool requires an explicit `project` argument and reuses the exact sandbox
(`WorkspaceManager.resolve_path`) and shell runner (`tools/shell.py`, Task 6)
that the employee-side tool uses — no new security surface.

**Files:**
- Create: `cortex/brain/tools/shell_tool.py`
- Test: `tests/test_brain_tools_shell.py`

**Interfaces:**
- Consumes: `run_shell_command`, `resolve_timeout` from `cortex.tools.shell` (Task 6), `Deps.workspaces`, `Deps.config.command_blocklist`/`.max_command_timeout`/`.allow_escape_workspace`.
- Produces: `ExecuteCommandBrainTool` (`RiskTier.RISKY`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_tools_shell.py
from __future__ import annotations

from dataclasses import dataclass

import pytest

from cortex.brain.tools.base import BrainToolContext
from cortex.brain.tools.shell_tool import ExecuteCommandBrainTool
from cortex.errors import ToolError
from cortex.models import Action

CHAT = -100500


@dataclass
class _FakeDeps:
    workspaces: object
    config: object


def _ctx(deps) -> BrainToolContext:
    return BrainToolContext(deps=deps, chat_id=CHAT, requester_id=1001)


async def test_requires_project_arg(config, workspaces):
    deps = _FakeDeps(workspaces, config)
    with pytest.raises(ToolError, match="project"):
        await ExecuteCommandBrainTool().execute(
            Action(tool="execute_command", args={"command": "echo hi"}), _ctx(deps)
        )


async def test_runs_inside_project_sandbox(config, workspaces):
    project = workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, config)

    result = await ExecuteCommandBrainTool().execute(
        Action(
            tool="execute_command",
            args={"project": "sports_api", "command": "echo plexus > created.txt"},
        ),
        _ctx(deps),
    )
    assert result.ok
    assert (project.path / "created.txt").exists()


async def test_blocklist_still_applies(config, workspaces):
    workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, config)

    with pytest.raises(ToolError, match="заблокирована"):
        await ExecuteCommandBrainTool().execute(
            Action(tool="execute_command", args={"project": "sports_api", "command": "rm -rf /"}),
            _ctx(deps),
        )


async def test_cannot_escape_project_sandbox(config, workspaces):
    workspaces.create("sports_api")
    deps = _FakeDeps(workspaces, config)

    with pytest.raises(ToolError, match="за пределы"):
        await ExecuteCommandBrainTool().execute(
            Action(
                tool="execute_command",
                args={"project": "sports_api", "command": "echo x", "cwd": "../../"},
            ),
            _ctx(deps),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_shell.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.tools.shell_tool'`

- [ ] **Step 3: Implement**

```python
# cortex/brain/tools/shell_tool.py
"""execute_command мозга — тот же раннер и та же песочница, что у
сотрудников (cortex/tools/shell.py, cortex/workspace/manager.py), только
проект называется явно: у мозга нет своего "текущего" проекта."""

from __future__ import annotations

from ...errors import ToolError
from ...models import Action, ToolResult
from ...tools.shell import resolve_timeout, run_shell_command
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext


class ExecuteCommandBrainTool(BrainTool):
    name = "execute_command"
    description = (
        "Выполнить команду в терминале внутри папки указанного проекта. "
        "Используй только для быстрой проверки — инженерную работу делегируй "
        "через assign_task."
    )
    usage = '{"tool": "execute_command", "args": {"project": "sports_api", "command": "git status"}}'
    risk = RiskTier.RISKY

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        project_name = str(action.args.get("project") or "").strip()
        if not project_name:
            raise ToolError("нужен project — у мозга нет своего проекта по умолчанию")

        command = str(action.args.get("command") or "").strip()
        if not command:
            raise ToolError("пустая команда")

        project = ctx.deps.workspaces.require(project_name)
        cwd = ctx.deps.workspaces.resolve_path(
            project,
            str(action.args.get("cwd") or "."),
            allow_escape=ctx.deps.config.allow_escape_workspace,
        )
        timeout = resolve_timeout(
            action.args.get("timeout"), max_timeout=ctx.deps.config.max_command_timeout
        )

        return await run_shell_command(
            command,
            cwd=cwd,
            timeout=timeout,
            blocklist=ctx.deps.config.command_blocklist,
            log_tag=f"{project.name}/Cortex",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_tools_shell.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/brain/tools/shell_tool.py tests/test_brain_tools_shell.py
git commit -m "feat: add brain-scoped execute_command (risky)"
```

---

### Task 14: `prompts/cortex_brain.md` + `brain/context.py`

**Files:**
- Create: `prompts/cortex_brain.md`
- Create: `cortex/brain/context.py`
- Test: `tests/test_brain_context.py`

**Interfaces:**
- Consumes: `Config.prompts_dir`/`.company_name`/`.ceo_name`, `EmployeeRegistry.all()`, `WorkspaceManager.list()`, `ChatState.active_project()`, `BrainToolRegistry.docs()` (Task 7).
- Produces: `BrainPromptBuilder(config, registry, workspaces, state, tools: BrainToolRegistry)` with `.persona() -> str`, `.build_initial(*, chat_id: int, history_block: str, message_text: str) -> str` and `.build_followup(*, tool_name: str, result: ToolResult) -> str`. Consumed by `brain/agent.py` in Task 15.

**Why `persona()` is separate from `build_initial()`:** `claude -p` takes
the *turn* content; `--system-prompt` takes the persona once and Claude
Code treats it as session-level, not per-turn. If the persona were folded
into `build_initial()`'s return value (as one might naively do, mirroring
`context/builder.py`'s employee `PromptBuilder`), the `{system_prompt}`
placeholder in `config.yaml`'s `claude` driver (Task 1) would always
receive an empty string — `brain/agent.py` (Task 15) calls both
`.persona()` and `.build_initial()`/`.build_followup()` and passes the
former as `system_prompt=` on every `runner.run()` call, initial and
followup alike.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_context.py
from __future__ import annotations

from cortex.brain.context import BrainPromptBuilder
from cortex.brain.tools.base import BrainTool, BrainToolContext, BrainToolRegistry
from cortex.brain.risk import RiskTier
from cortex.models import Action, ToolResult


class _Echo(BrainTool):
    name = "list_staff"
    description = "список сотрудников"
    risk = RiskTier.SAFE

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        return ToolResult.success("ok")


def _builder(config, registry, workspaces, state) -> BrainPromptBuilder:
    tools = BrainToolRegistry()
    tools.register(_Echo())
    return BrainPromptBuilder(config, registry, workspaces, state, tools)


async def test_persona_is_separate_from_the_turn_content(config, registry, workspaces, state):
    """persona() идёт в --system-prompt отдельно от -p — см. Task 15."""
    builder = _builder(config, registry, workspaces, state)
    assert "цифровой директор" in builder.persona()


async def test_initial_prompt_contains_state_tools_and_message_but_not_persona(
    config, registry, workspaces, state, frontend
):
    await registry.add(frontend)
    workspaces.create("sports_api")
    builder = _builder(config, registry, workspaces, state)

    prompt = builder.build_initial(
        chat_id=-100500, history_block="(история чата пуста)", message_text="кто в штате?"
    )

    assert "цифровой директор" not in prompt  # это в persona(), не здесь
    assert "Frontend_Dev" in prompt
    assert "sports_api" in prompt
    assert "list_staff" in prompt
    assert "кто в штате?" in prompt
    assert "<action>" in prompt


def test_initial_prompt_has_no_stray_format_braces(config, registry, workspaces, state):
    """JSON-примеры в контракте не должны ломать сборку промпта."""
    builder = _builder(config, registry, workspaces, state)
    prompt = builder.build_initial(chat_id=-100500, history_block="", message_text="привет")
    assert '{"tool"' in prompt


def test_followup_prompt_reports_success():
    builder = BrainPromptBuilder.__new__(BrainPromptBuilder)  # чистая функция, deps не нужны
    text = BrainPromptBuilder.build_followup(
        builder, tool_name="list_staff", result=ToolResult.success("В штате 1", "detail here")
    )
    assert "list_staff" in text
    assert "успех" in text
    assert "detail here" in text


def test_followup_prompt_reports_failure():
    builder = BrainPromptBuilder.__new__(BrainPromptBuilder)
    text = BrainPromptBuilder.build_followup(
        builder, tool_name="fire_employee", result=ToolResult.failure("не найден")
    )
    assert "ошибка" in text
    assert "не найден" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.context'`

- [ ] **Step 3: Create the static persona**

```markdown
# cortex_brain.md
# Cortex — цифровой директор Plexus Lab

Ты — Cortex, оркестратор и цифровой директор компании Plexus Lab. Ты
разговариваешь с CEO Abdulloh Abbosov свободно, на человеческом языке — без
командного синтаксиса. Твоя работа — управлять компанией: нанимать и
увольнять сотрудников, заводить и подключать проекты, ставить задачи
инженерам и следить за состоянием системы.

## Кто вокруг

- CEO — Abdulloh Abbosov. Единственный, кто пишет тебе напрямую (охрана
  периметра стоит перед тобой — если ты читаешь это сообщение, значит оно
  от него).
- Сотрудники — Telegram-боты, за каждым сессия `agy` (Google Antigravity).
  Они пишут код. Ты — нет: инженерную работу делегируешь через assign_task,
  а не лезешь в execute_command по любому поводу.

## Как ты работаешь

1. Читаешь историю чата и новое сообщение CEO, понимаешь намерение.
2. Нужны факты для ответа (кто в штате, какие проекты, что выполняется) —
   сначала вызови нужный инструмент, потом отвечай на их основе.
3. Намерение понятно и данных достаточно — вызови инструмент действия.
   Не спрашивай то, что уже есть в истории чата.
4. Данных не хватает (например, просят нанять, но не назвали должность) —
   спроси коротко, одним вопросом, как коллега в переписке.
5. Не делай вид, что действие выполнено, если ты не вызвал инструмент.

## Наём

Ты не можешь создать бота сам — Telegram не даёт ботам создавать ботов.
Когда CEO просит нанять: узнай тег и должность, если не назвал — спроси;
проведи его через @BotFather словами (/newbot, выключить Group Privacy,
добавить бота в рабочую группу); дождись токена; вызови hire_employee.
Жёсткого сценария из пронумерованных шагов нет — веди диалог как человек,
опираясь на то, что уже есть в истории чата.

## Как ты говоришь

Коротко, по-деловому, без канцелярита. Ты пишешь в рабочий чат, а не отчёт
для аудита. Сделал что-то — скажи, что именно. Не можешь — скажи, чего не
хватает.
```

- [ ] **Step 4: Implement `brain/context.py`**

```python
# cortex/brain/context.py
"""Сборка промпта для мозга: персона + состояние компании + инструменты +
история + сообщение CEO. Тот же принцип, что у context/builder.py для
сотрудников: порядок блоков важен, задача идёт последней.
"""

from __future__ import annotations

from ..config import Config
from ..models import ToolResult
from ..registry import EmployeeRegistry
from ..state import ChatState
from ..workspace import WorkspaceManager
from .tools.base import BrainToolRegistry

_PERSONA_FILE = "cortex_brain.md"

_FALLBACK_PERSONA = (
    "Ты — Cortex, цифровой директор Plexus Lab. Разговаривай с CEO свободно "
    "и управляй компанией через доступные инструменты."
)

_ACTION_CONTRACT = """\
## Формат действия

Чтобы что-то сделать, вставь в ответ блок:

<action>
{"tool": "list_staff", "args": {}}
</action>

Правила:
1. Внутри <action> — строго один JSON-объект: {"tool": "...", "args": {...}}.
2. Блоков может быть несколько, выполняются по порядку, до первой неудачи.
3. Текст вне блоков — твоя реплика CEO. Пиши коротко.
4. Не нужно действие — просто ответь текстом."""


class BrainPromptBuilder:
    def __init__(
        self,
        config: Config,
        registry: EmployeeRegistry,
        workspaces: WorkspaceManager,
        state: ChatState,
        tools: BrainToolRegistry,
    ) -> None:
        self.config = config
        self.registry = registry
        self.workspaces = workspaces
        self.state = state
        self.tools = tools

    # ------------------------------------------------------------------
    def persona(self) -> str:
        """Статичная личность Cortex — идёт в --system-prompt, не в -p."""
        path = self.config.prompts_dir / _PERSONA_FILE
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return _FALLBACK_PERSONA

    def _state_block(self, chat_id: int) -> str:
        employees = self.registry.all()
        staff = (
            ", ".join(f"@{e.name} ({e.role})" for e in employees) if employees else "пока никого"
        )
        projects = self.workspaces.list()
        project_names = ", ".join(p.name for p in projects) if projects else "ни одного"
        active = self.state.active_project(chat_id) or "не закреплён"

        return (
            f"## Состояние компании\n\n"
            f"Штат: {staff}\n"
            f"Проекты: {project_names}\n"
            f"Активный проект этого чата: {active}"
        )

    # ------------------------------------------------------------------
    def build_initial(self, *, chat_id: int, history_block: str, message_text: str) -> str:
        blocks = [
            self._state_block(chat_id),
            "",
            "## Доступные инструменты",
            "",
            self.tools.docs(),
            "",
            _ACTION_CONTRACT,
            "",
            "---",
            "",
            "## Последние сообщения чата",
            "",
            history_block.strip(),
            "",
            "---",
            "",
            "## Сообщение CEO",
            "",
            message_text.strip(),
        ]
        return "\n".join(blocks)

    def build_followup(self, *, tool_name: str, result: ToolResult) -> str:
        status = "успех" if result.ok else "ошибка"
        parts = [f"Результат {tool_name} ({status}): {result.summary}"]
        if result.detail:
            parts += ["", result.detail]
        return "\n".join(parts)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_context.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add prompts/cortex_brain.md cortex/brain/context.py tests/test_brain_context.py
git commit -m "feat: add Cortex brain persona and prompt builder"
```

---

### Task 15: `brain/agent.py` — the think/act loop

**Design notes carried into the code below (read before implementing):**
- The loop processes **one action per Claude turn**. If a response contains
  multiple `<action>` blocks, only the first is executed and the rest are
  logged and dropped — this keeps the confirmation gate unambiguous (never
  more than one pending action per turn). Employees (via `agy`) keep their
  existing multi-action-per-turn behavior; this restriction is brain-only.
- A per-chat `asyncio.Lock` wraps each individual `runner.run()` call (not
  the whole request) so two Telegram updates for the same chat can never
  launch overlapping `claude --resume <same-uuid>` processes. It is *not*
  held across a pending confirmation wait — that would block the chat
  indefinitely if the CEO takes a while to click a button. This is a
  conscious, minimal answer to the concurrency question the spec didn't
  pin down; a stronger guarantee is not needed for a single-CEO chat.
- Unknown tool names skip the risk gate entirely and go straight to
  `dispatch()`, which fails fast with "инструмент не существует" — asking
  the CEO to confirm something that cannot possibly succeed would be noise.

**Files:**
- Create: `cortex/brain/agent.py`
- Create: `tests/fixtures/echo_brain.py`
- Test: `tests/test_brain_agent.py`

**Interfaces:**
- Consumes: `BrainToolRegistry`/`BrainToolContext` (Task 7), `BrainPromptBuilder` (Task 14, including `.persona()`), `BrainSession` (Task 4), `PendingActionStore`/`PendingAction` (Task 5), `RiskTier`/`Autonomy`/`requires_confirmation`/`resolve_risk`/`parse_autonomy` (Task 3), `Gateway.reply`/`.ask_confirmation` (Task 11 — `agent.py` never imports `aiogram` itself), existing `AgentRunner.run(..., system_prompt=, session_flag=, driver=)` (Task 2), existing `ChatHistory`, `extract_actions`/`strip_actions` (`cortex/tools/parser.py`, unchanged).
- Produces: `BrainAgent(*, deps: "Deps", tools: BrainToolRegistry, prompts: BrainPromptBuilder, session: BrainSession, pending: PendingActionStore)` with `async .handle_message(*, chat_id: int, message_id: int | None, text: str, requester_id: int) -> None` and `async .resolve_pending(action_id: str, *, approved: bool) -> None`. Both are consumed by `telegram/brain_router.py` in Task 16.

- [ ] **Step 1: Write the fixture — a stateful fake `claude` for a two-turn conversation**

```python
# tests/fixtures/echo_brain.py
#!/usr/bin/env python
"""Детерминированный «claude» для теста агентного цикла (brain/agent.py).

Первый вызов (--counter-file ещё не существует) — просит список штата.
Второй вызов (после результата list_staff) — финальный текстовый ответ,
без действий, цикл на этом останавливается.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file")
    parser.add_argument("--counter-file", required=True)
    args, _ = parser.parse_known_args()

    counter = Path(args.counter_file)
    if not counter.exists():
        counter.write_text("1", encoding="utf-8")
        sys.stdout.write(
            "Сейчас посмотрю.\n\n<action>\n{\"tool\": \"list_staff\", \"args\": {}}\n</action>\n"
        )
        return 0

    sys.stdout.write("В штате один сотрудник: Frontend_Dev, Senior Frontend Engineer.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write the failing test**

```python
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
from cortex.hr import HRService
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

    await agent.resolve_pending("p1", approved=True)

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

    await agent.resolve_pending("p2", approved=False)

    assert registry.get("Frontend_Dev").active is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.brain.agent'`

- [ ] **Step 4: Implement**

```python
# cortex/brain/agent.py
"""Мозг Cortex: цикл «контекст -> claude -> действие -> результат -> …».

Один инструмент за ход (см. docs/superpowers/plans/2026-08-13-cortex-brain.md,
Task 15) — так подтверждение рискованного действия остаётся однозначным.
Разговор продолжается через claude --resume, поэтому каждый следующий ход —
не пересборка всего контекста, а только результат предыдущего действия.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from ..errors import AgentRunError
from ..logging_setup import get_logger
from ..models import Action, ChatMessage, ToolResult
from ..telegram import formatting as fmt
from ..tools.parser import extract_actions, strip_actions
from .context import BrainPromptBuilder
from .pending import PendingAction, PendingActionStore
from .risk import parse_autonomy, requires_confirmation, resolve_risk
from .session import BrainSession
from .tools.base import BrainToolContext, BrainToolRegistry

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("brain.agent")

_BRAIN_PROJECT = "__brain__"


def _describe(action: Action) -> str:
    args_preview = ", ".join(f"{k}={v!r}" for k, v in action.args.items())
    return f"{action.tool}({args_preview})"


class BrainAgent:
    def __init__(
        self,
        *,
        deps: "Deps",
        tools: BrainToolRegistry,
        prompts: BrainPromptBuilder,
        session: BrainSession,
        pending: PendingActionStore,
    ) -> None:
        self.deps = deps
        self.tools = tools
        self.prompts = prompts
        self.session = session
        self.pending = pending
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------
    async def handle_message(
        self, *, chat_id: int, message_id: int | None, text: str, requester_id: int
    ) -> None:
        deps = self.deps
        history_block = deps.history.render(
            chat_id, limit=deps.config.history_messages, budget=deps.config.history_chars_budget
        )
        prompt = self.prompts.build_initial(
            chat_id=chat_id, history_block=history_block, message_text=text
        )
        await self._run_loop(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            prompt=prompt, iteration=1,
        )

    # ------------------------------------------------------------------
    async def _run_loop(
        self, *, chat_id: int, message_id: int | None, requester_id: int, prompt: str, iteration: int
    ) -> None:
        deps = self.deps
        if iteration > deps.config.brain_max_iterations:
            await deps.gateway.reply(
                chat_id,
                "Кажется, я зациклился — остановлюсь здесь и подожду новых указаний.",
                reply_to=message_id,
            )
            return

        session_flag = self.session.session_flag(chat_id)
        try:
            async with self._locks[chat_id]:
                result = await deps.runner.run(
                    prompt=prompt,
                    workspace=deps.config.root,
                    agent="Cortex",
                    project=_BRAIN_PROJECT,
                    timeout=deps.config.runner_timeout,
                    system_prompt=self.prompts.persona(),
                    session_flag=session_flag,
                    driver=deps.config.brain_driver,
                )
        except AgentRunError as exc:
            await deps.gateway.reply(
                chat_id,
                fmt.agent_error_report(
                    agent="Cortex", project=_BRAIN_PROJECT, error=exc,
                    stderr_limit=deps.config.stderr_report_chars,
                ),
                reply_to=message_id,
            )
            return

        self.session.mark_used(chat_id)

        actions, parse_errors = extract_actions(result.stdout)
        reply_text = strip_actions(result.stdout)

        if reply_text:
            await deps.gateway.reply(chat_id, fmt.esc(reply_text), reply_to=message_id)
            deps.history.add(
                ChatMessage(
                    chat_id=chat_id, message_id=message_id or 0, author="Cortex",
                    text=reply_text, is_agent=True,
                )
            )

        if parse_errors:
            await deps.gateway.reply(
                chat_id,
                "⚠️ Не разобрал часть действий:\n" + "\n".join(f"- {e}" for e in parse_errors),
            )

        if not actions:
            return

        if len(actions) > 1:
            log.debug("Мозг вернул %d действий за ход — беру первое, остальные отбрасываю", len(actions))

        await self._process_action(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            action=actions[0], iteration=iteration,
        )

    # ------------------------------------------------------------------
    async def _process_action(
        self, *, chat_id: int, message_id: int | None, requester_id: int, action: Action, iteration: int
    ) -> None:
        deps = self.deps
        default_tier = self.tools.risk_of(action.tool)

        if default_tier is not None:
            tier = resolve_risk(action.tool, default_tier, deps.config.brain_risk_overrides)
            autonomy = parse_autonomy(deps.config.brain_autonomy)
            if requires_confirmation(tier, autonomy):
                pending = PendingAction(
                    id=uuid.uuid4().hex[:10],
                    chat_id=chat_id,
                    message_id=message_id,
                    requester_id=requester_id,
                    tool=action.tool,
                    args=action.args,
                    risk=tier.value,
                    summary=_describe(action),
                )
                await self.pending.add(pending)
                await self._ask_confirmation(pending)
                return

        ctx = BrainToolContext(deps=deps, chat_id=chat_id, requester_id=requester_id)
        result = await self.tools.dispatch(action, ctx)
        await self._continue_after_result(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            tool_name=action.tool, result=result, iteration=iteration,
        )

    # ------------------------------------------------------------------
    async def _ask_confirmation(self, pending: PendingAction) -> None:
        await self.deps.gateway.ask_confirmation(
            chat_id=pending.chat_id,
            action_id=pending.id,
            summary=pending.summary,
            risk=pending.risk,
        )

    # ------------------------------------------------------------------
    async def resolve_pending(self, action_id: str, *, approved: bool) -> None:
        pending = await self.pending.pop(action_id)
        if pending is None:
            return

        if not approved:
            result = ToolResult.failure("Действие отменено CEO")
        else:
            ctx = BrainToolContext(
                deps=self.deps, chat_id=pending.chat_id, requester_id=pending.requester_id
            )
            action = Action(tool=pending.tool, args=pending.args)
            result = await self.tools.dispatch(action, ctx)

        await self._continue_after_result(
            chat_id=pending.chat_id, message_id=pending.message_id,
            requester_id=pending.requester_id, tool_name=pending.tool, result=result, iteration=1,
        )

    # ------------------------------------------------------------------
    async def _continue_after_result(
        self, *, chat_id: int, message_id: int | None, requester_id: int,
        tool_name: str, result: ToolResult, iteration: int,
    ) -> None:
        followup = self.prompts.build_followup(tool_name=tool_name, result=result)
        await self._run_loop(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            prompt=followup, iteration=iteration + 1,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_agent.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/brain/agent.py tests/fixtures/echo_brain.py tests/test_brain_agent.py
git commit -m "feat: add BrainAgent think/act loop with confirmation gate"
```

---

### Task 16: `telegram/brain_router.py`

Replaces `telegram/handlers.py` entirely. `@Tag` mention routing is
preserved verbatim (same `MentionRouter`, same direct-to-`agy` path, brain
never sees it) — it just now lives in this file instead of `handlers.py`.
Everything else — any CEO text that isn't a mention — goes to `deps.brain`.
Non-CEO senders (employee bots posting their own updates) are ignored by
the brain path; they're still recorded in chat history by the existing
`ChatLoggerMiddleware`, unchanged.

**Files:**
- Create: `cortex/telegram/brain_router.py`
- Test: `tests/test_brain_router.py`

**Interfaces:**
- Consumes: `MentionRouter.route()` (unchanged, `cortex/telegram/routing.py`), `Deps.brain: BrainAgent` (Task 18), `Deps.mentions`, `Deps.orchestrator`, `Deps.config.secrets.ceo_id`.
- Produces: `build_brain_router(deps: "Deps") -> Router` (registered in `gateway.py`, Task 18).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_router.py
"""Роутинг верхнего уровня: @Tag идёт в agy напрямую, всё остальное — в
мозг. Тест дёргает построенный Router через aiogram's feed_update-подобный
путь было бы тяжеловесно; вместо этого проверяем маршрутизацию через
прямой вызов обработчика, извлечённого из router.message.handlers —
тот же приём, которым в проекте пока не пользовались, поэтому здесь он
локальный для этого файла."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from cortex.telegram.brain_router import build_brain_router

CEO_ID = 1001
CHAT = -100500


class _FakeBrain:
    def __init__(self) -> None:
        self.handled: list[tuple] = []
        self.resolved: list[tuple] = []

    async def handle_message(self, *, chat_id, message_id, text, requester_id):
        self.handled.append((chat_id, message_id, text, requester_id))

    async def resolve_pending(self, action_id, *, approved):
        self.resolved.append((action_id, approved))


@dataclass
class _FakeTask:
    task_id: str = "t1"


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.dispatched = []

    def new_task(self, **kwargs):
        return _FakeTask()

    async def dispatch(self, task, *, requester_id):
        self.dispatched.append(task)


@dataclass
class _FakeConfigSecrets:
    ceo_id: int = CEO_ID


@dataclass
class _FakeConfig:
    secrets: object = field(default_factory=_FakeConfigSecrets)
    ack_task_start: bool = False


@dataclass
class _FakeDeps:
    brain: object
    mentions: object
    orchestrator: object
    config: object = field(default_factory=_FakeConfig)
    scheduler: object = None


def _message(text: str, user_id: int = CEO_ID):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=CHAT),
        message_id=7,
        from_user=SimpleNamespace(id=user_id, full_name="Someone", is_bot=False),
        reply=_noop_reply,
        answer=_noop_reply,
    )


async def _noop_reply(*args, **kwargs):
    return None


def _get_handler(router, message_or_callback_type: str):
    """Достаём единственный обработчик нужного observer'а из router."""
    observer = getattr(router, message_or_callback_type)
    return observer.handlers[0].callback


async def test_mention_bypasses_brain(config, registry, workspaces, frontend):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    await registry.add(frontend)
    workspaces.create("sports_api")
    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)

    brain = _FakeBrain()
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=orchestrator)

    router = build_brain_router(deps)
    handler = _get_handler(router, "message")

    await handler(_message("@Frontend_Dev почини хедер #sports_api"))
    await asyncio.sleep(0)  # дать шанс фоновой asyncio.create_task(...) выполниться

    assert len(orchestrator.dispatched) == 1
    assert brain.handled == []


async def test_ceo_free_text_goes_to_brain(config, registry, workspaces, frontend):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "message")

    await handler(_message("кто у нас в штате?"))
    await asyncio.sleep(0)

    assert brain.handled == [(CHAT, 7, "кто у нас в штате?", CEO_ID)]


async def test_non_ceo_free_text_is_ignored(config, registry, workspaces):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "message")

    await handler(_message("привет всем", user_id=999999))

    assert brain.handled == []


async def test_confirm_callback_resolves_pending(config, registry, workspaces):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "callback_query")

    callback = SimpleNamespace(
        data="brain:confirm:abc123",
        from_user=SimpleNamespace(id=CEO_ID),
        message=SimpleNamespace(edit_text=_noop_reply),
        answer=_noop_reply,
    )
    await handler(callback)
    await asyncio.sleep(0)

    assert brain.resolved == [("abc123", True)]


async def test_cancel_callback_resolves_pending_as_declined(config, registry, workspaces):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "callback_query")

    callback = SimpleNamespace(
        data="brain:cancel:abc123",
        from_user=SimpleNamespace(id=CEO_ID),
        message=SimpleNamespace(edit_text=_noop_reply),
        answer=_noop_reply,
    )
    await handler(callback)
    await asyncio.sleep(0)

    assert brain.resolved == [("abc123", False)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cortex.telegram.brain_router'`

- [ ] **Step 3: Implement**

```python
# cortex/telegram/brain_router.py
"""Верхний уровень маршрутизации: @Tag идёт напрямую в agy (как раньше в
telegram/handlers.py::build_mention_router — эта логика перенесена сюда
без изменений), всё остальное свободным текстом от CEO — в мозг.

Заменяет handlers.py и hiring.py целиком: слэш-команд больше нет, найм —
разговор с мозгом (см. cortex/brain/tools/hr.py).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from ..errors import CortexError
from ..logging_setup import get_logger
from . import formatting as fmt

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("brain_router")

#: Как в handlers.py — фоновые задачи держим за ссылку.
_BACKGROUND: set[asyncio.Task] = set()


def build_brain_router(deps: "Deps") -> Router:
    router = Router(name="brain")

    # ------------------------------------------------------------------
    @router.message(StateFilter(None), F.text)
    async def on_text(message: Message) -> None:
        text = message.text or ""
        if text.startswith("/"):
            return  # слэш-команд больше нет — не отвечаем на призраков старого UX

        try:
            routed = deps.mentions.route(text, message.chat.id)
        except CortexError as exc:
            await message.reply(fmt.error_report(exc))
            return

        if routed is not None:
            requester = "CEO"
            if message.from_user and message.from_user.id != deps.config.secrets.ceo_id:
                requester = message.from_user.full_name or str(message.from_user.id)

            task = deps.orchestrator.new_task(
                employee=routed.employee,
                project_name=routed.project,
                instruction=routed.instruction,
                chat_id=message.chat.id,
                message_id=message.message_id,
                requester=requester,
            )

            if deps.config.ack_task_start:
                queued = deps.scheduler.is_busy(routed.project) if deps.scheduler else False
                note = " (встал в очередь — проект занят)" if queued else ""
                await message.reply(
                    f"📥 <b>{fmt.esc(routed.employee.title)}</b> взял задачу "
                    f"<code>{task.task_id}</code> в проекте "
                    f"<code>{fmt.esc(routed.project)}</code>{note}",
                    disable_notification=True,
                )

            background = asyncio.create_task(
                deps.orchestrator.dispatch(
                    task, requester_id=message.from_user.id if message.from_user else 0
                ),
                name="mention-task",
            )
            _BACKGROUND.add(background)
            background.add_done_callback(_BACKGROUND.discard)
            return

        # Не адресовано конкретному сотруднику — решает мозг. Только CEO:
        # чужие сообщения (в т.ч. от других ботов компании) не запускают его.
        if not message.from_user or message.from_user.id != deps.config.secrets.ceo_id:
            return

        background = asyncio.create_task(
            deps.brain.handle_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=text,
                requester_id=message.from_user.id,
            ),
            name="brain-message",
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

    # ------------------------------------------------------------------
    @router.callback_query(F.data.startswith("brain:"))
    async def on_confirmation(callback: CallbackQuery) -> None:
        if not callback.from_user or callback.from_user.id != deps.config.secrets.ceo_id:
            await callback.answer("Только CEO может это подтвердить.", show_alert=True)
            return

        _, verdict, action_id = (callback.data or "").split(":", maxsplit=2)
        approved = verdict == "confirm"

        if callback.message is not None:
            await callback.message.edit_text(
                "✅ Подтверждено, выполняю…" if approved else "❌ Отменено."
            )
        await callback.answer()

        background = asyncio.create_task(
            deps.brain.resolve_pending(action_id, approved=approved), name="brain-resolve"
        )
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_router.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add cortex/telegram/brain_router.py tests/test_brain_router.py
git commit -m "feat: add top-level brain router (mentions + free text + confirmations)"
```

---

### Task 17: `scripts/mock_claude.py` — manual smoke-testing without spending the Claude subscription

Mirrors `scripts/mock_agy.py`. Wired via `PLEXUS_BRAIN_DRIVER=mock_claude`
(picked up by `Config.brain_driver`, Task 1) — does not touch `deploy.ps1`;
the existing `-Mock` flag stays employee-only (`PLEXUS_FORCE_DRIVER=mock`).

**Files:**
- Create: `scripts/mock_claude.py`
- Test: `tests/test_mock_claude.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mock_claude.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MOCK_CLAUDE = Path(__file__).resolve().parent.parent / "scripts" / "mock_claude.py"


def _run(prompt: str, tmp_path: Path) -> str:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(MOCK_CLAUDE), "--prompt-file", str(prompt_file)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def test_staff_question_triggers_list_staff_action(tmp_path):
    out = _run("## Сообщение CEO\n\nкто у нас в штате?", tmp_path)
    assert "<action>" in out
    assert "list_staff" in out


def test_followup_after_tool_result_ends_without_new_action(tmp_path):
    out = _run("Результат list_staff (успех): В штате 1 сотрудник(ов)", tmp_path)
    assert "<action>" not in out


def test_unrecognized_message_is_plain_text(tmp_path):
    out = _run("## Сообщение CEO\n\nпривет", tmp_path)
    assert "<action>" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mock_claude.py -v`
Expected: FAIL — `scripts/mock_claude.py` doesn't exist yet

- [ ] **Step 3: Implement**

```python
# scripts/mock_claude.py
#!/usr/bin/env python
"""Заглушка `claude` CLI — проверка мозга Cortex без реальной подписки.

Включается через переменную окружения PLEXUS_BRAIN_DRIVER=mock_claude
(см. Config.brain_driver в cortex/config.py) — работает независимо от
PLEXUS_FORCE_DRIVER, который управляет только драйвером сотрудников.
"""

from __future__ import annotations

import argparse
import os
import sys


def build_reply(prompt: str) -> str:
    lowered = prompt.lower()

    # Продолжение диалога (результат предыдущего инструмента) — финальный
    # текст без нового действия, иначе агентный цикл никогда не остановится.
    if lowered.lstrip().startswith("результат "):
        return "Готово — вот что получилось по твоему запросу (mock-режим)."

    if "штат" in lowered or "сотрудник" in lowered:
        return 'Сейчас посмотрю.\n\n<action>\n{"tool": "list_staff", "args": {}}\n</action>'
    if "проект" in lowered:
        return 'Секунду.\n\n<action>\n{"tool": "list_projects", "args": {}}\n</action>'
    if "статус" in lowered:
        return '<action>\n{"tool": "get_status", "args": {}}\n</action>'

    return "Понял (mock-режим, реального Claude тут нет). Что дальше?"


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock claude CLI for brain testing")
    parser.add_argument("--prompt-file", dest="prompt_file")
    args, _unknown = parser.parse_known_args()

    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    else:
        prompt = sys.stdin.read()

    sys.stdout.write(build_reply(prompt))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mock_claude.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`

```bash
git add scripts/mock_claude.py tests/test_mock_claude.py
git commit -m "feat: add mock claude driver for brain smoke-testing"
```

---

### Task 18: Wire the brain into `app.py`/`gateway.py`, delete the old command UX

**Files:**
- Modify: `cortex/deps.py`
- Modify: `cortex/app.py`
- Modify: `cortex/telegram/gateway.py`
- Delete: `cortex/telegram/handlers.py`
- Delete: `cortex/telegram/hiring.py`
- Delete: `cortex/telegram/synapse_handlers.py` (`/digest` is now the `request_digest` brain tool, Task 12)
- Test: `tests/test_gateway_wiring.py`

No new runtime behavior is unit-tested here beyond what Tasks 1-17 already
cover — this task is pure wiring. The regression check is: the full
existing suite plus one new test asserting `Gateway._build_dispatcher` no
longer references the deleted routers.

- [ ] **Step 1: `cortex/deps.py`** — add one field next to `gateway`

```python
    #: Проставляется после создания Gateway — он сам нуждается в Deps.
    gateway: Any = None
    #: Мозг Cortex — собирается в app.py после Deps, тем же приёмом, что gateway.
    brain: Any = None
    started_at: datetime = None  # type: ignore[assignment]
```

- [ ] **Step 2: `cortex/app.py`** — construct brain tools and `BrainAgent`

Add imports (next to the existing `.tools.*` imports):

```python
from .brain.agent import BrainAgent
from .brain.context import BrainPromptBuilder
from .brain.pending import PendingActionStore
from .brain.session import BrainSession
from .brain.tools.base import BrainToolRegistry
from .brain.tools.hr import FireEmployeeTool, HireEmployeeTool, WriteJobDescriptionTool
from .brain.tools.projects import (
    ArchiveProjectTool,
    CreateProjectTool,
    LinkProjectTool,
    SetChatProjectTool,
    UnlinkProjectTool,
)
from .brain.tools.read import GetEmployeeTool, GetStatusTool, ListProjectsTool, ListStaffTool
from .brain.tools.shell_tool import ExecuteCommandBrainTool
from .brain.tools.work import AssignTaskTool, RequestDigestTool, SetListenTool
from .brain.tools.work import SendFileTool as BrainSendFileTool
```

(`BrainSendFileTool` avoids clashing with the existing `from .tools.send_file import SendFileTool` import right below it — both stay, they're different classes for different registries.)

In `build()`, right before `return self.deps`, add:

```python
        brain_tools = BrainToolRegistry()
        brain_tools.register_all(
            [
                ListStaffTool(), GetEmployeeTool(), ListProjectsTool(), GetStatusTool(),
                HireEmployeeTool(), WriteJobDescriptionTool(), FireEmployeeTool(),
                CreateProjectTool(), LinkProjectTool(), SetChatProjectTool(),
                UnlinkProjectTool(), ArchiveProjectTool(),
                AssignTaskTool(), SetListenTool(), BrainSendFileTool(), RequestDigestTool(),
                ExecuteCommandBrainTool(),
            ]
        )
        brain_prompts = BrainPromptBuilder(cfg, registry, workspaces, state, brain_tools)
        self.deps.brain = BrainAgent(
            deps=self.deps,
            tools=brain_tools,
            prompts=brain_prompts,
            session=BrainSession(cfg.data_dir),
            pending=PendingActionStore(cfg.data_dir / "pending_actions.json"),
        )
        return self.deps
```

- [ ] **Step 3: `cortex/telegram/gateway.py`** — swap the router set

Replace the imports:

```python
from .handlers import build_command_router, build_mention_router
from .hiring import build_hiring_router
from .middleware import ChatLoggerMiddleware, SecurityMiddleware
from .synapse_handlers import build_synapse_router
```

with:

```python
from .brain_router import build_brain_router
from .middleware import ChatLoggerMiddleware, SecurityMiddleware
```

Replace `_build_dispatcher` (drops the now-meaningless `with_hiring` split —
there is no FSM left to gate on privacy):

```python
    def _build_dispatcher(self) -> Dispatcher:
        dispatcher = Dispatcher(storage=self._storage)

        dispatcher.message.middleware(SecurityMiddleware(self.deps.guard))
        dispatcher.message.middleware(ChatLoggerMiddleware(self.deps.history))

        dispatcher.include_router(build_brain_router(self.deps))
        return dispatcher
```

Update the two call sites that passed `with_hiring`:

```python
        await self._spawn("__gateway__", self._gateway_bot)
```

and, in `start_listener`:

```python
        await self._spawn(key, bot)
```

Update `_spawn`'s signature to drop the now-unused parameter:

```python
    async def _spawn(self, key: str, bot: Bot) -> None:
        dispatcher = self._build_dispatcher()
```

- [ ] **Step 4: Delete the three obsolete files**

```bash
git rm cortex/telegram/handlers.py cortex/telegram/hiring.py cortex/telegram/synapse_handlers.py
```

- [ ] **Step 5: Write the regression test**

```python
# tests/test_gateway_wiring.py
"""handlers.py/hiring.py/synapse_handlers.py стали историей — фиксируем,
что Gateway больше на них не ссылается и что модули реально удалены."""

from __future__ import annotations

import importlib

import pytest

from cortex.telegram import gateway


def test_deleted_modules_are_gone():
    for name in ("cortex.telegram.handlers", "cortex.telegram.hiring", "cortex.telegram.synapse_handlers"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_gateway_imports_only_brain_router():
    source = importlib.import_module("cortex.telegram.gateway").__file__
    text = open(source, encoding="utf-8").read()
    assert "build_brain_router" in text
    assert "build_command_router" not in text
    assert "build_hiring_router" not in text
    assert "build_synapse_router" not in text
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass (this is the first point where a leftover import
mistake in `gateway.py` or `app.py` would surface — read any `ImportError`
carefully, it names the exact missing/renamed symbol)

- [ ] **Step 7: Manual doctor check**

Run: `.venv/Scripts/python.exe scripts/doctor.py`
Expected: same output shape as before (bot/group/CEO/staff/agy/workspaces
sections) — `doctor.py` doesn't touch the router layer, this just confirms
`Config`/`app.py` still import cleanly end-to-end.

- [ ] **Step 8: Commit**

```bash
git add cortex/deps.py cortex/app.py cortex/telegram/gateway.py tests/test_gateway_wiring.py
git commit -m "feat: wire BrainAgent into the app, remove the slash-command UX"
```

---

### Task 19: Update `README.md` / `docs/ARCHITECTURE.md`, then a live smoke test

The docs currently document a command-driven bot that no longer exists
after Task 18 — leaving them as-is would actively mislead the next reader
(including a future you). This task brings them in line, then proves the
whole thing works against the real `claude` CLI already authenticated on
this machine.

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: `README.md` — replace the command-era sections**

Replace the "Команды CEO" section (the two tables: "Штат" and "Проекты",
plus "Работа") with:

```markdown
## Как говорить с Cortex

Никаких команд — пиши свободным текстом в личку боту или в рабочую группу.
Cortex понимает намерение и сам решает, какой инструмент вызвать:

- «кто у нас в штате?» → читает реестр и отвечает
- «найми фронтендера, тег Frontend_Dev» → ведёт через @BotFather словами,
  ждёт токен, нанимает
- «заведи проект sports_api» → создаёт рабочую среду
- «@Frontend_Dev почини хедер» → как и раньше, уходит прямо на `agy`,
  минуя мозг — это адресация конкретному сотруднику, не команда Cortex

Рискованные действия (увольнение, `execute_command`, архивирование,
отключение проекта) Cortex подтверждает кнопками «✅ Выполнить / ❌ Отмена»,
если не задано иное в `config.yaml -> brain.autonomy`.
```

Replace the "Найм" section body with:

```markdown
Найм — разговор, не мастер из шагов. Напиши Cortex в личке (токен не
должен попасть в общий чат): скажи, кого хочешь нанять и на какую роль.
Cortex попросит тебя создать бота в @BotFather (`/newbot`, выключить Group
Privacy, добавить в рабочую группу) и пришлёт токен — вставь его в чат.
Дальше Cortex сам проверит токен, сгенерирует должностную инструкцию и
включит сотрудника без перезапуска сервера.
```

In the "Структура" tree, replace the `telegram/` block's `handlers.py`,
`hiring.py`, `synapse_handlers.py` lines (they no longer exist) and add the
new `brain/` package:

```
│   ├── telegram/             ← только Telegram
│   │   ├── gateway.py        polling-листенеры, горячее включение
│   │   ├── bot_pool.py       пул ботов, ответ от лица сотрудника
│   │   ├── brain_router.py   @Tag напрямую в agy, всё остальное — в мозг
│   │   ├── routing.py        разбор «@кто #где что»
│   │   ├── middleware.py     охрана + запись истории
│   │   └── formatting.py     HTML, нарезка, отчёты об ошибках
│   │
│   ├── brain/                 ← мозг: понимание естественного языка
│   │   ├── agent.py          цикл think → act → observe
│   │   ├── context.py        промпт: персона + состояние компании
│   │   ├── session.py        claude --session-id/--resume на чат
│   │   ├── risk.py           autonomy: cautious | balanced | autonomous
│   │   ├── pending.py        действия, ждущие подтверждения
│   │   └── tools/            мета-инструменты: HR, проекты, assign_task
```

- [ ] **Step 2: `docs/ARCHITECTURE.md` — append a new decision section**

Add after section 11 ("Что осознанно не сделано"):

```markdown
---

## 12. Мозг: Cortex понимает естественный язык

**Решение.** Слэш-команды и FSM найма (`telegram/handlers.py`,
`telegram/hiring.py`) заменены мозгом на `claude` CLI: любое сообщение
CEO, не адресованное конкретному сотруднику через `@Tag`, идёт в
`brain/agent.py`, который вызывает `claude -p` через тот же `AgentRunner`,
что и `agy` — только с `--tools ""` (никаких встроенных Bash/Edit/Read,
только наш контракт `<action>`) и по драйверу `Config.brain_driver`,
независимому от драйвера сотрудников.

**Почему не отдельный API-ключ.** `claude` CLI без `--bare` использует уже
залогиненную OAuth-сессию Claude Code — расходуется существующая подписка,
не отдельный оплачиваемый Anthropic API. Тот же приём, каким `agy`
использует подписку Google Antigravity.

**Экономия токенов.** `@Tag задача` по-прежнему уходит прямо на `agy`,
минуя мозг — инженерная работа не тратит Claude. Мозг нужен только для
разговора с CEO и управленческих решений. Внутри одного разговора
`claude --session-id`/`--resume` (`brain/session.py`) не пересылает
контекст заново на каждой реплике.

**Один инструмент за ход.** Мозг не выполняет несколько действий из
одного ответа `claude`, как это делает исполнитель-сотрудник — только
первое; результат идёт в `--resume` как следующий ход. Так подтверждение
рискованного действия остаётся однозначным: не бывает ситуации «два
рискованных действия в одном ответе, а кнопка одна».

**Риск-политика вместо да/нет.** `brain/risk.py` — три уровня риска
инструмента (safe/normal/risky) и три режима автономии
(cautious/balanced/autonomous) в `config.yaml -> brain`, с точечными
`risk_overrides` per-tool. Подтверждение — inline-кнопки в Telegram, не
разбор слова «да» из свободного текста: it's unambiguous where a sentence
might not be.

**Без отдельного командного резерва.** Если `claude` недоступен, Cortex
сообщает об этом тем же путём, что и падение `agy` (`AgentRunError` →
отчёт в чат) — и не откатывается к командам. Это осознанный выбор CEO:
один интерфейс, не два.
```

- [ ] **Step 3: Commit the docs**

```bash
git add README.md docs/ARCHITECTURE.md
git commit -m "docs: describe the brain-driven flow, drop the command-era docs"
```

- [ ] **Step 4: Live smoke test against the real `claude` CLI (manual, not automated)**

This is the actual goal of the whole plan — confirm it works for real, not
just under `pytest`. `claude` is already authenticated on this machine
(confirmed earlier: `claude -p "ok"` answers without an auth prompt).

1. Start the server: `.\deploy.ps1` (or `.venv/Scripts/python.exe run.py`
   directly). Watch `data/cortex.log` for `Листенер __gateway__ запущен`
   with no errors.
2. DM the bot (`@TheCortexAI_bot`) in Telegram: **«кто у нас в штате?»**
   Expect a natural-language answer reflecting the real registry — empty
   ("штат пуст") if nothing's been hired yet.
3. DM: **«найми сотрудника, тег Frontend_Dev, роль Senior Frontend
   Engineer»**. Expect Cortex to walk you through @BotFather in its own
   words and ask for the token once you've created the bot. Paste the
   token back. Expect a success reply and the employee to show up in a
   follow-up "кто в штате?" without restarting the server.
4. In the corp group: **«@Frontend_Dev просто скажи привет»** (needs at
   least one project — say «заведи проект sandbox» to the brain first if
   none exists). Confirm the reply comes from `@Frontend_Dev`'s own bot
   token, not `@TheCortexAI_bot` — this is the `@Tag`-bypasses-the-brain
   path, unchanged by this whole plan.
5. Trigger a risky action: DM **«уволи Frontend_Dev»**. Expect an inline
   "✅ Выполнить / ❌ Отмена" prompt, *not* an immediate firing. Click
   "✅ Выполнить" and confirm Cortex reports it's done and a follow-up
   "кто в штате?" no longer lists them as active.
6. If anything in steps 2-5 doesn't match, capture `data/cortex.log`
   around the timestamp — every failure path in this plan (Task 15's
   `AgentRunError` handling, Task 16's confirmation routing) is designed
   to produce a readable chat message, so a silent hang or a raw
   traceback in the chat both point at a real bug, not expected behavior.

No `- [ ]` checkbox here — this step is exploratory verification with a
human in the loop, not a scripted pass/fail; note the actual outcome in
the PR/handoff instead of checking a box.

---

## Summary

19 tasks, each independently testable and committed on its own. Tasks 1-2
extend existing infrastructure (config, runner) without touching employee
behavior. Tasks 3-13 build the brain's own components bottom-up (risk
policy, session tracking, pending actions, tool contract, then the tools
themselves) with no Telegram or subprocess dependencies — the same
layering discipline `docs/ARCHITECTURE.md` already establishes. Tasks
14-17 assemble the think/act loop and its Telegram surface. Task 18 is
pure wiring — the moment the old command UX actually goes away. Task 19
closes the loop with real docs and a real conversation.
