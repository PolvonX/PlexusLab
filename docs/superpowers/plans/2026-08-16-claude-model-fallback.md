# Claude-Family Model Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a retryable (quota-classified) `AgentRunError` hits, try a configured Claude-family fallback model immediately (no cooldown wait) before falling back to the existing wait-and-retry-primary behavior — covering both the brain's own calls (currently zero resilience) and employee/self-task dispatch (already has cooldown-retry).

**Architecture:** Reuse the existing named-driver abstraction (`agent_runner.drivers`) instead of inventing a model-swap mechanism — a fallback is just another driver entry (e.g. `claude_haiku`) tried via the same `AgentRunner.run(driver=...)` parameter that already exists. Fallback attempts never use `--resume` (fresh session only).

**Tech Stack:** Python 3.11, existing PlexusLab/Cortex codebase (aiogram, asyncio subprocess).

## Global Constraints

- Fallback models are Claude-family only (e.g. sonnet → haiku). Do not add cross-vendor entries — explicitly rejected in the design (spec: `docs/superpowers/specs/2026-08-16-claude-model-fallback-design.md`).
- Reuse the existing `AgentRunError.retry_after` classification from `cortex/runtime/runner.py::_parse_retry_after` — do not build a new error-classification mechanism or widen that regex.
- Fallback attempts must not pass a `--resume` session flag — always a fresh `--session-id`.
- Do not touch `cortex/brain/risk.py` or `cortex/context/builder.py` — out of scope, explicitly rejected earlier in this project's history.
- If the fallback list is empty or exhausted, behavior must be unchanged from today (orchestrator: cooldown-retry primary up to `_MAX_AUTO_RETRIES`; brain: report failure).

---

### Task 1: Config — `claude_haiku` driver entry and `fallback_drivers` properties

**Files:**
- Modify: `config.yaml`
- Modify: `cortex/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.runner_fallback_drivers -> list[RunnerDriver]` (employee/self-task fallback chain, empty list if unset)
- Produces: `Config.brain_fallback_drivers -> list[RunnerDriver]` (brain fallback chain, empty list if unset)
- Consumes: existing `Config._load_driver(name: str) -> RunnerDriver` (`cortex/config.py:154-166`), existing `RunnerDriver` dataclass (`cortex/config.py:70-75`)

- [ ] **Step 1: Add the `claude_haiku` driver and `fallback_drivers` keys to config.yaml**

In `config.yaml`, inside `agent_runner.drivers`, add a new entry right after the existing `claude:` entry (which currently reads `command: > claude.cmd -p --output-format text --system-prompt-file "{system_prompt_file}" --tools "" --model sonnet {session_flag}`):

```yaml
    claude_haiku:
      # Fallback-модель для мозга/сотрудников при исчерпании квоты sonnet —
      # тот же контракт, что у claude, только модель полегче.
      command: >
        claude.cmd -p --output-format text
        --system-prompt-file "{system_prompt_file}" --tools ""
        --model haiku {session_flag}
      prompt_via_stdin: true
      env: {}
```

Then, inside the `brain:` section, add:

```yaml
  # Модели, которые пробуем сразу (без ожидания кулдауна), если sonnet упёрся
  # в квоту — до перехода на старое поведение "ждать и повторить sonnet".
  fallback_drivers: []
```

And inside `agent_runner:` (top level, alongside `driver:` and `drivers:`), add:

```yaml
  # То же самое для сотрудников/self_execute_task — пусто по умолчанию,
  # чтобы поведение не менялось, пока CEO явно не включит.
  fallback_drivers: []
```

Leave both lists empty — this task only adds the *capability*; nothing switches drivers until a fallback name is added to one of these lists.

- [ ] **Step 2: Add `runner_fallback_drivers` and `brain_fallback_drivers` properties to `cortex/config.py`**

In `cortex/config.py`, right after the existing `brain_driver` property (ends at line 179 with `return self._load_driver(name)`), add:

```python
    @property
    def runner_fallback_drivers(self) -> list[RunnerDriver]:
        """Fallback-драйверы для сотрудников/self_execute_task, в порядке
        попытки — пробуются сразу при retryable-ошибке, без ожидания
        кулдауна. Пусто по умолчанию (поведение не меняется)."""
        names = self.section("agent_runner").get("fallback_drivers") or []
        return [self._load_driver(str(n)) for n in names]

    @property
    def brain_fallback_drivers(self) -> list[RunnerDriver]:
        """Fallback-драйверы для мозга — см. runner_fallback_drivers."""
        names = self.brain.get("fallback_drivers") or []
        return [self._load_driver(str(n)) for n in names]
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_config.py`:

```python
# tests/test_config.py
from __future__ import annotations

from cortex.config import Config


def _raw(**overrides):
    base = {
        "company": {"name": "Plexus Lab", "ceo_name": "Abdulloh Abbosov"},
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "agy",
            "drivers": {
                "agy": {"command": "agy -p {prompt}"},
                "claude_haiku": {"command": "claude.cmd -p --model haiku {session_flag}"},
            },
        },
        "brain": {},
    }
    base.update(overrides)
    return base


def test_runner_fallback_drivers_empty_by_default(tmp_path, secrets):
    cfg = Config(root=tmp_path, raw=_raw(), secrets=secrets)
    assert cfg.runner_fallback_drivers == []


def test_runner_fallback_drivers_resolves_named_drivers(tmp_path, secrets):
    raw = _raw()
    raw["agent_runner"]["fallback_drivers"] = ["claude_haiku"]
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    fallbacks = cfg.runner_fallback_drivers
    assert len(fallbacks) == 1
    assert fallbacks[0].name == "claude_haiku"
    assert "haiku" in fallbacks[0].command


def test_brain_fallback_drivers_empty_by_default(tmp_path, secrets):
    cfg = Config(root=tmp_path, raw=_raw(), secrets=secrets)
    assert cfg.brain_fallback_drivers == []


def test_brain_fallback_drivers_resolves_named_drivers(tmp_path, secrets):
    raw = _raw()
    raw["brain"]["fallback_drivers"] = ["claude_haiku"]
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    fallbacks = cfg.brain_fallback_drivers
    assert len(fallbacks) == 1
    assert fallbacks[0].name == "claude_haiku"


def test_unknown_fallback_driver_name_raises_config_error(tmp_path, secrets):
    from cortex.errors import ConfigError

    raw = _raw()
    raw["agent_runner"]["fallback_drivers"] = ["does_not_exist"]
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    try:
        cfg.runner_fallback_drivers
        assert False, "expected ConfigError"
    except ConfigError:
        pass
```

Check `tests/conftest.py` for the `secrets` fixture before running — it's already used by every other test file in this suite (e.g. `tests/test_orchestrator.py`), so no new fixture is needed.

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'runner_fallback_drivers'` (Step 2 not yet applied) or `ModuleNotFoundError` if run before Step 1/2.

- [ ] **Step 5: Apply Steps 1-2 above, then run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: `5 passed`

- [ ] **Step 6: Run the full suite to confirm no regressions, then commit**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: same pass/fail counts as before this task (this change is additive-only: new optional config keys, new properties, no existing code path touched).

```bash
git add config.yaml cortex/config.py tests/test_config.py
git commit -m "feat: add claude_haiku fallback driver and fallback_drivers config"
```

---

### Task 2: Formatting — fallback notice for employee dispatch

**Files:**
- Modify: `cortex/telegram/formatting.py`
- Test: `tests/test_formatting.py`

**Interfaces:**
- Consumes: nothing new (pure string formatting, same style as existing `agent_retry_notice` in the same file)
- Produces: `fmt.agent_fallback_notice(*, agent: str, driver_name: str, attempt: int, total: int) -> str`

- [ ] **Step 1: Write the failing test**

Check `tests/test_formatting.py` first for the exact test style used for `agent_retry_notice` (added earlier this session) and match it. Add:

```python
def test_agent_fallback_notice_names_the_fallback_driver():
    text = fmt.agent_fallback_notice(
        agent="Frontend_Dev", driver_name="claude_haiku", attempt=1, total=1
    )
    assert "Frontend_Dev" in text
    assert "claude_haiku" in text
```

(Use the same import style already at the top of `tests/test_formatting.py`, e.g. `from cortex.telegram import formatting as fmt`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_formatting.py -k fallback_notice -v`
Expected: FAIL — `AttributeError: module 'cortex.telegram.formatting' has no attribute 'agent_fallback_notice'`

- [ ] **Step 3: Implement**

In `cortex/telegram/formatting.py`, right after the existing `agent_retry_notice` function, add:

```python
def agent_fallback_notice(*, agent: str, driver_name: str, attempt: int, total: int) -> str:
    """Переключаемся на резервную Claude-модель сразу, без ожидания кулдауна."""
    return (
        f"🔀 <b>{esc(agent)}</b> упёрся в лимит запросов — пробую резервную "
        f"модель (<code>{esc(driver_name)}</code>), попытка {attempt} из {total}."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_formatting.py -k fallback_notice -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cortex/telegram/formatting.py tests/test_formatting.py
git commit -m "feat: add agent_fallback_notice formatting for model fallback"
```

---

### Task 3: Orchestrator — try fallback drivers before cooldown-retry

**Files:**
- Modify: `cortex/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Config.runner_fallback_drivers` (Task 1), `fmt.agent_fallback_notice` (Task 2), existing `AgentRunner.run(..., driver: RunnerDriver | None = None)` (already accepts this param — no runner.py change needed), existing `AgentRunError.retry_after` (already exists from earlier this session)
- Produces: `Orchestrator.dispatch(task, *, requester_id, _retries=0, _fallback_attempt=0)` — new keyword-only param `_fallback_attempt`; `Orchestrator._execute(task, *, requester_id, driver=None)` — new keyword-only param `driver`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_orchestrator.py`, right after `test_quota_error_triggers_auto_retry`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py -k "fallback or cooldown_when_no_fallback" -v`
Expected: `test_quota_error_falls_back_to_configured_driver_immediately` FAILS (times out or hits the 999s cooldown path — the fallback driver is never tried yet); `test_quota_error_uses_cooldown_when_no_fallback_configured` currently PASSES already (it's exercising only pre-existing behavior) — that's fine, it becomes a regression guard once Task 3 lands.

- [ ] **Step 3: Implement — modify `cortex/orchestrator.py`**

Replace the current `dispatch` method (currently reads, from the fix earlier this session):

```python
    async def dispatch(
        self, task: AgentTask, *, requester_id: int, _retries: int = 0
    ) -> None:
        """Поставить задачу в очередь. Ошибки уходят в чат, не в трейсбек.

        `_retries` — внутренний счётчик авто-ретраев retryable-ошибок
        (квота/rate-limit); внешние вызовы его не передают."""
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
            if exc.retry_after is not None and _retries < _MAX_AUTO_RETRIES:
                await self._retry_after_cooldown(
                    task, exc, requester_id=requester_id, retries=_retries + 1
                )
            else:
                await self._report_agent_failure(task, exc)
        except CortexError as exc:
```

with:

```python
    async def dispatch(
        self, task: AgentTask, *, requester_id: int, _retries: int = 0, _fallback_attempt: int = 0
    ) -> None:
        """Поставить задачу в очередь. Ошибки уходят в чат, не в трейсбек.

        `_retries` — внутренний счётчик авто-ретраев retryable-ошибок после
        кулдауна; `_fallback_attempt` — сколько fallback-драйверов уже
        испробовано (0 = основной драйвер). Внешние вызовы их не передают."""
        info = TaskInfo(
            task_id=task.task_id,
            agent=task.employee.name,
            project=task.project,
            instruction=task.instruction[:200],
        )
        fallbacks = self.config.runner_fallback_drivers
        driver = fallbacks[_fallback_attempt - 1] if _fallback_attempt > 0 else None
        try:
            await self.scheduler.submit(
                info, lambda: self._execute(task, requester_id=requester_id, driver=driver)
            )
        except AgentRunError as exc:
            if exc.retry_after is not None and _fallback_attempt < len(fallbacks):
                await self._retry_with_fallback(
                    task, requester_id=requester_id,
                    retries=_retries, fallback_attempt=_fallback_attempt + 1,
                )
            elif exc.retry_after is not None and _retries < _MAX_AUTO_RETRIES:
                await self._retry_after_cooldown(
                    task, exc, requester_id=requester_id, retries=_retries + 1
                )
            else:
                await self._report_agent_failure(task, exc)
        except CortexError as exc:
```

Then find `_execute` (currently `async def _execute(self, task: AgentTask, *, requester_id: int) -> None:`) and change its signature and the `runner.run(...)` call:

```python
    async def _execute(
        self, task: AgentTask, *, requester_id: int, driver=None
    ) -> None:
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
                driver=driver,
            )
        finally:
            typing.cancel()

        await self._deliver(task, project_name=project.name, raw_output=result.stdout,
                            stderr=result.stderr, requester_id=requester_id)
```

(Only the `def _execute(...)` line and the `driver=driver` line added to the `self.runner.run(...)` call are new — the rest of the method body is unchanged, shown in full so the diff is unambiguous.)

Finally, add a new method right after `_retry_after_cooldown` (which already exists from earlier this session):

```python
    async def _retry_with_fallback(
        self, task: AgentTask, *, requester_id: int, retries: int, fallback_attempt: int
    ) -> None:
        """Retryable-ошибка и есть неиспробованный fallback-драйвер — пробуем
        его сразу, без ожидания кулдауна (в отличие от _retry_after_cooldown)."""
        fallbacks = self.config.runner_fallback_drivers
        driver = fallbacks[fallback_attempt - 1]
        log.info(
            "Задача %s: retryable-ошибка, пробую fallback-драйвер %s (%d/%d)",
            task.task_id, driver.name, fallback_attempt, len(fallbacks),
        )
        await self.bots.say(
            task.employee,
            task.chat_id,
            fmt.agent_fallback_notice(
                agent=task.employee.title,
                driver_name=driver.name,
                attempt=fallback_attempt,
                total=len(fallbacks),
            ),
            reply_to=task.message_id,
        )
        await self.dispatch(
            task, requester_id=requester_id, _retries=retries, _fallback_attempt=fallback_attempt
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py -v`
Expected: all tests in the file PASS, including the two new ones.

- [ ] **Step 5: Run the full suite, then commit**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: no new failures versus before this task.

```bash
git add cortex/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: try Claude-family fallback driver before cooldown-retry in dispatch"
```

---

### Task 4: Brain — fallback for the actual bottleneck (`_run_loop`)

**Files:**
- Modify: `cortex/brain/agent.py`
- Test: `tests/test_brain_agent.py`

**Interfaces:**
- Consumes: `Config.brain_fallback_drivers` (Task 1), existing `AgentRunner.run(..., driver=..., session_flag=...)`, existing `AgentRunError.retry_after`
- Produces: `BrainAgent._run_loop(..., fallback_attempt: int = 0)` — new keyword-only param

- [ ] **Step 1: Write the failing test**

Add to `tests/test_brain_agent.py`, right after `test_non_session_failure_is_reported_without_resetting_the_session` (uses the same `_QuotaExhaustedRunner`-style pattern already in that file):

```python
async def test_quota_error_falls_back_to_configured_driver_with_fresh_session(
    tmp_path, secrets, registry, workspaces, state
):
    """Живой инцидент этой сессии: мозг — единственная точка входа для
    свободных сообщений CEO, и раньше квота на sonnet просто убивала каждый
    следующий ход без всякого восстановления. С fallback_drivers мозг сразу
    пробует резервную модель, БЕЗ --resume (свежая сессия)."""

    class _QuotaThenFallbackRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []  # (driver_name, session_flag)

        async def run(self, *, driver, session_flag: str, **kwargs) -> AgentResult:
            name = driver.name if driver else "primary"
            self.calls.append((name, session_flag))
            if name != "claude_haiku":
                raise AgentRunError(
                    "Процесс завершился с кодом 1.", returncode=1, duration=0.1,
                    stderr="Individual quota reached. Please upgrade your "
                    "subscription to increase your limits. Resets in 999s.",
                    retry_after=999.0,
                )
            return AgentResult(
                stdout="Отвечаю с резервной модели.", stderr="",
                returncode=0, duration=0.3, command="claude",
            )

    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    cfg.raw["brain"]["fallback_drivers"] = ["claude_haiku"]
    cfg.raw["agent_runner"]["drivers"]["claude_haiku"] = {"command": "unused"}
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    runner = _QuotaThenFallbackRunner()
    deps.runner = runner
    agent = _agent(deps)
    agent.session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")

    await agent.handle_message(chat_id=CHAT, message_id=1, text="как ты там?", requester_id=1001)

    assert len(runner.calls) == 2
    assert runner.calls[0][0] == "primary"
    assert runner.calls[0][1] == "--resume 11111111-1111-1111-1111-111111111111"
    assert runner.calls[1][0] == "claude_haiku"
    assert runner.calls[1][1].startswith("--session-id")  # fresh session, no --resume
    assert any("резервную модель" in m or "резервной модели" in m for m in gateway.messages)
    # исходная сессия для primary НЕ тронута — следующий обычный ход снова резюмирует sonnet
    assert agent.session.session_flag(CHAT) == "--resume 11111111-1111-1111-1111-111111111111"
```

Note: this test relies on `driver.name` being accessible inside the fake runner — check that `_config_with_brain_driver`'s `agent_runner.drivers` dict in `tests/test_brain_agent.py` (around line 83-87) already defines a `claude` entry; the `claude_haiku` entry added above only needs a `command` key (the fake runner never actually executes it — it inspects `driver` directly and returns a canned result).

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_brain_agent.py -k quota_error_falls_back -v`
Expected: FAIL — either `TypeError` (current `_run_loop` doesn't pass `driver=` to `runner.run()` at all, so the fake runner's `driver` kwarg lookup / behavior won't match), or the test hangs/reports failure via `gateway.messages` containing "не справился" instead of the fallback text.

- [ ] **Step 3: Implement — modify `cortex/brain/agent.py`**

Find the current `_run_loop` signature and body (from `async def _run_loop(` through the `except AgentRunError as exc:` block — this is the section shown earlier when this design was investigated, lines ~155-234 as of this session's last edit to this file). Replace:

```python
        session_flag = self.session.session_flag(chat_id)
        is_resume = session_flag.startswith("--resume")
        typing = asyncio.create_task(self._keep_typing(chat_id))
        result = None
        try:
            async with self._locks[chat_id]:
                result = await deps.runner.run(
                    prompt=prompt,
                    workspace=self._brain_workspace(),
                    agent="Cortex",
                    project=_BRAIN_PROJECT,
                    timeout=deps.config.runner_timeout,
                    system_prompt=self.prompts.persona(),
                    session_flag=session_flag,
                    driver=deps.config.brain_driver,
                )
        except AgentRunError as exc:
            if (
                is_resume and not resume_retry_done and iteration == 1
                and _looks_like_missing_session(exc)
            ):
                log.warning(
                    "Мозг: --resume не нашёл сессию чата %s, начинаю новую: %s", chat_id, exc
                )
                self.session.reset(chat_id)
                # Небольшая пауза перед повтором: живой инцидент показал, что
                # два запуска claude.cmd почти впритык друг за другом иногда
                # оба падают — похоже на коллизию npm-обёртки/её временных
                # файлов при повторном старте процесса без паузы.
                await asyncio.sleep(1.5)
            else:
                await deps.gateway.reply(
                    chat_id,
                    fmt.agent_error_report(
                        agent="Cortex", project=_BRAIN_PROJECT, error=exc,
                        stderr_limit=deps.config.stderr_report_chars,
                    ),
                    reply_to=message_id,
                )
                return
        finally:
            typing.cancel()

        if result is None:
            # Сюда попадаем только после сброса сессии на строке выше —
            # тот же prompt, но уже со свежим --session-id.
            await self._run_loop(
                chat_id=chat_id, message_id=message_id, requester_id=requester_id,
                prompt=prompt, iteration=iteration, resume_retry_done=True,
                original_text=original_text,
            )
            return

        self.session.mark_used(chat_id, session_flag)
```

with:

```python
        fallbacks = deps.config.brain_fallback_drivers
        if fallback_attempt > 0:
            driver = fallbacks[fallback_attempt - 1]
            session_flag = f"--session-id {uuid.uuid4()}"
        else:
            driver = deps.config.brain_driver
            session_flag = self.session.session_flag(chat_id)
        is_resume = session_flag.startswith("--resume")
        typing = asyncio.create_task(self._keep_typing(chat_id))
        result = None
        try:
            async with self._locks[chat_id]:
                result = await deps.runner.run(
                    prompt=prompt,
                    workspace=self._brain_workspace(),
                    agent="Cortex",
                    project=_BRAIN_PROJECT,
                    timeout=deps.config.runner_timeout,
                    system_prompt=self.prompts.persona(),
                    session_flag=session_flag,
                    driver=driver,
                )
        except AgentRunError as exc:
            if (
                is_resume and not resume_retry_done and iteration == 1
                and _looks_like_missing_session(exc)
            ):
                log.warning(
                    "Мозг: --resume не нашёл сессию чата %s, начинаю новую: %s", chat_id, exc
                )
                self.session.reset(chat_id)
                # Небольшая пауза перед повтором: живой инцидент показал, что
                # два запуска claude.cmd почти впритык друг за другом иногда
                # оба падают — похоже на коллизию npm-обёртки/её временных
                # файлов при повторном старте процесса без паузы.
                await asyncio.sleep(1.5)
            elif exc.retry_after is not None and fallback_attempt < len(fallbacks):
                next_driver = fallbacks[fallback_attempt]
                log.warning(
                    "Мозг: retryable-ошибка (%s), пробую fallback-драйвер %s чата %s",
                    exc, next_driver.name, chat_id,
                )
                typing.cancel()
                await self._run_loop(
                    chat_id=chat_id, message_id=message_id, requester_id=requester_id,
                    prompt=prompt, iteration=iteration, resume_retry_done=resume_retry_done,
                    original_text=original_text, fallback_attempt=fallback_attempt + 1,
                )
                return
            else:
                await deps.gateway.reply(
                    chat_id,
                    fmt.agent_error_report(
                        agent="Cortex", project=_BRAIN_PROJECT, error=exc,
                        stderr_limit=deps.config.stderr_report_chars,
                    ),
                    reply_to=message_id,
                )
                return
        finally:
            typing.cancel()

        if result is None:
            # Сюда попадаем только после сброса сессии на строке выше —
            # тот же prompt, но уже со свежим --session-id.
            await self._run_loop(
                chat_id=chat_id, message_id=message_id, requester_id=requester_id,
                prompt=prompt, iteration=iteration, resume_retry_done=True,
                original_text=original_text,
            )
            return

        if fallback_attempt == 0:
            # Fallback-ответы не персистятся как resume-сессия основной
            # модели — следующий обычный ход снова пробует основную модель
            # с её собственной (нетронутой) сохранённой сессией.
            self.session.mark_used(chat_id, session_flag)
```

`typing.cancel()` is called twice on the fallback path (once explicitly before the recursive call, once in `finally`) — that's safe: `asyncio.Task.cancel()` is a no-op if the task is already cancelled/done, matching the existing pattern already used elsewhere in this method for early returns inside `except`.

Now update the method signature. Find:

```python
    async def _run_loop(
        self, *, chat_id: int, message_id: int | None, requester_id: int, prompt: str,
        iteration: int, resume_retry_done: bool = False, original_text: str | None = None,
    ) -> None:
```

(exact parameter list/formatting may differ slightly — match whatever the current signature is, adding one parameter) and add `fallback_attempt: int = 0` to the keyword-only parameters, e.g.:

```python
    async def _run_loop(
        self, *, chat_id: int, message_id: int | None, requester_id: int, prompt: str,
        iteration: int, resume_retry_done: bool = False, original_text: str | None = None,
        fallback_attempt: int = 0,
    ) -> None:
```

Confirm `uuid` is already imported at the top of `cortex/brain/agent.py` (it's used elsewhere in this file for `PendingAction` ids per earlier code seen this session — `import uuid` should already be present; if not, add it next to the other stdlib imports at the top of the file).

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_brain_agent.py -v`
Expected: all tests in the file PASS, including the new one.

- [ ] **Step 5: Run the full suite, then commit**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: no new failures versus before this task (the 4 pre-existing `test_brain_router.py` failures from the other session's unfinished `/clear` work are unrelated and expected to remain — do not try to fix them as part of this task).

```bash
git add cortex/brain/agent.py tests/test_brain_agent.py
git commit -m "feat: brain tries Claude-family fallback driver on quota before giving up"
```

---

## Self-Review Notes

- **Spec coverage:** config/driver reuse (Task 1), immediate fallback without cooldown wait (Tasks 3-4), both call sites covered — brain (Task 4, the actual bottleneck per spec) and orchestrator (Task 3), no `--resume` on fallback (Task 4), graceful degrade to existing behavior when fallback list empty/exhausted (Tasks 3-4, explicit tests). `risk.py`/`context/builder.py` untouched — confirmed, no task references them.
- **No placeholders:** all code blocks are complete; test code is runnable as written, matching existing fixture patterns (`env`, `secrets`, `_config_with_brain_driver`, `_make_deps`) already present in the two test files.
- **Type/name consistency:** `_fallback_attempt` (orchestrator) and `fallback_attempt` (brain) are deliberately named per-file to match each file's existing leading-underscore convention for `dispatch`'s internal params (`_retries`) vs. `_run_loop`'s non-underscored internal params (`resume_retry_done`, `original_text`) — not a typo.
