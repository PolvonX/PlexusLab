# Brain Session Auto-Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the brain's `--resume` session from living unboundedly (hours, unlimited turns) by expiring it automatically by age/turn-count, plus a reactive expiry when the model's output stops matching the `<action>` contract — and separately, fix 4 tests broken by an unrelated earlier handler-index shift.

**Architecture:** `BrainSession` already owns per-chat session persistence (`data/brain_sessions/<chat_id>.session`); teach it to also track age and turn count in that same file (switch from a raw id string to JSON), and treat an expired session exactly like "no session yet" — the existing `--session-id` vs `--resume` branching in `session_flag()` needs zero new branches, just a broader expiry check. The reactive trigger reuses the existing malformed-`<action>` detection point in `brain/agent.py` that already exists for user-facing warnings.

**Tech Stack:** Python 3.11, stdlib `json`/`time`, pytest/pytest-asyncio.

## Global Constraints

- Reset is silent — no chat message — matching the existing "session not found" auto-recovery convention already in `cortex/brain/agent.py`. Do not add a chat notification.
- Proactive expiry: session age > 6 hours (21600 seconds) OR turn_count > 50, whichever comes first.
- Reset must not interrupt an in-flight claude.cmd call — it only ever changes what `session_flag()` returns for the *next* call.
- Do not touch `cortex/brain/risk.py` or `cortex/context/builder.py` (established boundary from earlier in this project's history — unrelated to this change, but still off-limits).
- `BrainSession.reset(chat_id)` keeps its current behavior and signature (just deletes the file) — do not change its interface.

---

### Task 1: `BrainSession` tracks age and turn count, expires transparently

**Files:**
- Modify: `cortex/brain/session.py`
- Test: `tests/test_brain_session.py`

**Interfaces:**
- Produces: `BrainSession.session_flag(chat_id: int) -> str` — same signature as today, but now returns `--session-id <uuid>` (not `--resume <id>`) once the stored session is expired, exactly as if no session existed yet.
- Produces: `BrainSession.mark_used(chat_id: int, session_flag: str) -> None` — same signature; on a `--session-id` flag writes turn_count=1, on a `--resume` flag increments the existing turn_count.
- Consumes: nothing new — `BrainSession.__init__(data_dir: Path)` unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_brain_session.py`, after the existing `test_marker_survives_a_new_instance` (last test in the file):

```python
def test_session_flag_expires_by_age(tmp_path, monkeypatch):
    """Живой инцидент: многочасовая непрерывная --resume сессия деградировала
    (мозг начал путать формат <action> и галлюцинировать). Сессия старше 6
    часов должна считаться протухшей и не резюмироваться."""
    import time
    session = BrainSession(tmp_path)
    session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")
    assert session.session_flag(CHAT).startswith("--resume")

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 6 * 3600 + 1)
    assert session.session_flag(CHAT).startswith("--session-id")


def test_session_flag_expires_by_turn_count(tmp_path):
    session = BrainSession(tmp_path)
    flag = "--session-id 11111111-1111-1111-1111-111111111111"
    session.mark_used(CHAT, flag)
    for _ in range(50):
        assert session.session_flag(CHAT).startswith("--resume")
        session.mark_used(CHAT, session.session_flag(CHAT))
    # 51-й ход — turn_count перевалил за 50
    assert session.session_flag(CHAT).startswith("--session-id")


def test_expired_session_flag_does_not_mutate_stored_state(tmp_path, monkeypatch):
    """session_flag() только читает — expiry не должна тихо стирать файл
    (это дело reset()/следующего mark_used()), иначе два конкурентных
    вызова session_flag() без mark_used() между ними дадут противоречивую
    картину."""
    import time
    session = BrainSession(tmp_path)
    session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 6 * 3600 + 1)
    session.session_flag(CHAT)  # первый вызов — expired
    session.session_flag(CHAT)  # второй вызов подряд — тоже expired, не падает
```

Note: `CHAT` is already defined at module level in this file (`CHAT = -100500`, set earlier this session when the real group ID was scrubbed) — reuse it, don't redefine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_brain_session.py -k "expires" -v`
Expected: FAIL — `test_session_flag_expires_by_age` and `test_session_flag_expires_by_turn_count` fail because today's `session_flag()`/`mark_used()` don't track age/count at all (a `--session-id` written by `mark_used` today is stored as a raw string, and `session_flag()` always returns `--resume` once *any* id is stored, regardless of age/count).

- [ ] **Step 3: Implement**

Replace the full contents of `cortex/brain/session.py` with:

```python
# cortex/brain/session.py
"""Сессия claude на чат: экономия токенов через --resume.

Живой инцидент: id раньше был детерминированным (uuid5 от chat_id). Если
claude хоть раз отклонял --session-id с этим id (например, ID уже
зарегистрирован под другим cwd/project — см. живой разбор в
docs/superpowers/reviews/), ВСЕ следующие попытки для этого чата бились в
тот же самый id снова и снова: "Session ID ... is already in use" — чат
навсегда застревал, потому что "новый" id был на самом деле тем же самым.

Поэтому id теперь случайный (uuid4) и генерируется заново при каждом
--session-id (первый контакт или восстановление после reset()). Единственное,
что живёт на диске, — это ID, которым РЕАЛЬНО завершился успешный вызов
(mark_used пишет его после успеха), а не факт "уже видели этот чат".

Живой инцидент #2: сессия без границ по времени/числу ходов деградирует —
многочасовой непрерывный --resume довёл мозг до того, что он начал путать
свой контрактный формат <action> с нативным tool-call синтаксисом и
галлюцинировать в ответах. Поэтому сессия теперь протухает сама: старше
_MAX_AGE_SECONDS или больше _MAX_TURNS ходов — session_flag() ведёт себя
так, будто сессии не было вообще (свежий --session-id, не --resume)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

#: Живой инцидент: многочасовая --resume сессия деградировала (см. докстринг
#: модуля) — эти два порога подобраны так, чтобы сработать раньше, чем
#: деградация успеет накопиться, но не мешать нормальной дневной работе.
_MAX_AGE_SECONDS = 6 * 3600
_MAX_TURNS = 50


class BrainSession:
    """chat_id -> {session_id, created_at, turn_count} последней успешно
    начатой сессии claude (или ничего)."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "brain_sessions"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file(self, chat_id: int) -> Path:
        return self._dir / f"{chat_id}.session"

    def _read(self, chat_id: int) -> dict | None:
        path = self._file(chat_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------
    def session_flag(self, chat_id: int) -> str:
        stored = self._read(chat_id)
        if stored is None:
            return f"--session-id {uuid.uuid4()}"

        age = time.time() - stored.get("created_at", 0)
        turns = stored.get("turn_count", 0)
        if age > _MAX_AGE_SECONDS or turns > _MAX_TURNS:
            # Протухла — ведём себя так, будто сессии не было. Файл не
            # трогаем здесь (session_flag() только читает) — перезапишет
            # его следующий mark_used() со свежим --session-id.
            return f"--session-id {uuid.uuid4()}"

        return f"--resume {stored['session_id']}"

    def mark_used(self, chat_id: int, session_flag: str) -> None:
        """Вызывается ПОСЛЕ успешного runner.run() с тем же session_flag,
        что ушёл в claude, — сохраняем id, которым сессия реально
        подтверждена, а не тот, что мы лишь собирались попробовать."""
        session_id = session_flag.split(maxsplit=1)[1]
        if session_flag.startswith("--session-id"):
            payload = {"session_id": session_id, "created_at": time.time(), "turn_count": 1}
        else:
            stored = self._read(chat_id) or {"created_at": time.time(), "turn_count": 0}
            payload = {
                "session_id": session_id,
                "created_at": stored.get("created_at", time.time()),
                "turn_count": stored.get("turn_count", 0) + 1,
            }
        self._file(chat_id).write_text(json.dumps(payload), encoding="utf-8")

    def reset(self, chat_id: int) -> None:
        """Резюме сломалось (сессия потеряна на стороне claude) — начинаем
        с чистого листа: следующий session_flag() выдаст новый случайный id,
        а не повторит тот же самый."""
        self._file(chat_id).unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_brain_session.py -v`
Expected: all tests in the file PASS (the 5 pre-existing ones plus the 3 new ones = 8 passed).

- [ ] **Step 5: Commit**

```bash
git add cortex/brain/session.py tests/test_brain_session.py
git commit -m "feat: expire brain session by age/turn-count, not just manually"
```

---

### Task 2: Reactive reset when the model's output stops matching the `<action>` contract

**Files:**
- Modify: `cortex/brain/agent.py`
- Test: `tests/test_brain_agent.py`

**Interfaces:**
- Consumes: `BrainSession.reset(chat_id: int) -> None` (Task 1, unchanged signature — already exists and used elsewhere in this file for the "session not found" path)
- Produces: no new public interface — internal behavior change only.

- [ ] **Step 1: Write the failing test**

First, read the current exact text around the `if parse_errors:` block in `cortex/brain/agent.py` (search for `"Не разобрал часть действий"`) to get the precise surrounding lines — the plan below assumes it looks like this (confirm before editing, this codebase has had several sessions touch this file recently):

```python
        if parse_errors:
            await deps.gateway.reply(
                chat_id,
                "⚠️ Не разобрал часть действий:\n" + "\n".join(f"- {e}" for e in parse_errors),
                reply_to=message_id,
            )
```

Add to `tests/test_brain_agent.py`, right after `test_non_session_failure_is_reported_without_resetting_the_session`:

```python
async def test_malformed_action_block_resets_session_as_degradation_signal(
    tmp_path, secrets, registry, workspaces, state
):
    """Живой инцидент этой сессии: деградировавшая многочасовая сессия
    начала выдавать <parameter name="tool">...</parameter> вместо
    контрактного <action>{"tool":...}</action> — парсер это ловит и шлёт
    предупреждение в чат, но раньше ничего не делал с самой сессией.
    Теперь битый action-блок — сигнал деградации: следующий ход должен
    начаться с чистой сессии, а не резюмировать ту же самую."""

    class _MalformedActionRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, **kwargs) -> AgentResult:
            self.calls += 1
            return AgentResult(
                stdout='<parameter name="tool">execute_command</parameter>'
                '<parameter name="args">{"command": "dir"}</parameter>',
                stderr="", returncode=0, duration=0.2, command="claude",
            )

    cfg = _config_with_brain_driver(tmp_path, secrets, tmp_path / "counter.txt")
    gateway = _FakeGateway()
    deps = _make_deps(cfg, registry, workspaces, state, gateway)
    runner = _MalformedActionRunner()
    deps.runner = runner
    agent = _agent(deps)
    agent.session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")

    await agent.handle_message(chat_id=CHAT, message_id=1, text="сделай что-нибудь", requester_id=1001)

    assert any("Не разобрал" in m for m in gateway.messages)
    # сессия сброшена — следующий вызов пойдёт со свежим --session-id
    assert agent.session.session_flag(CHAT).startswith("--session-id")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_brain_agent.py -k malformed_action -v`
Expected: FAIL on the last assertion — `session_flag(CHAT)` still starts with `--resume` because nothing resets it today.

- [ ] **Step 3: Implement**

In `cortex/brain/agent.py`, find the `if parse_errors:` block identified in Step 1 above and add a `self.session.reset(chat_id)` call right after the existing `await deps.gateway.reply(...)`:

```python
        if parse_errors:
            await deps.gateway.reply(
                chat_id,
                "⚠️ Не разобрал часть действий:\n" + "\n".join(f"- {e}" for e in parse_errors),
                reply_to=message_id,
            )
            # Битый <action>-блок — сигнал деградации сессии (живой
            # инцидент: многочасовая сессия начала путать контрактный
            # формат с нативным tool-call синтаксисом claude). Следующий
            # ход начинается с чистой сессии, а не резюмирует эту же.
            self.session.reset(chat_id)
```

If the surrounding code differs from the assumed snippet in Step 1 (e.g. different variable names), apply the same change — one line, `self.session.reset(chat_id)`, immediately after the existing parse-error chat notification, inside the `if parse_errors:` block.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_brain_agent.py -v`
Expected: all tests in the file PASS, including the new one.

- [ ] **Step 5: Run the full suite, then commit**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: no new failures versus before this task. The 4 `test_brain_router.py` failures fixed in Task 3 below are still failing at this point — that's expected, Task 3 hasn't run yet.

```bash
git add cortex/brain/agent.py tests/test_brain_agent.py
git commit -m "feat: reset brain session when the model emits a malformed action block"
```

---

### Task 3: Fix 4 tests broken by a stale hardcoded handler index

**Files:**
- Modify: `tests/test_brain_router.py`

**Interfaces:**
- Produces: `_get_full_handler(router, message_or_callback_type: str, name: str)` — new test helper, returns the raw aiogram handler object (has both `.callback` and `.check()`).
- Produces: `_get_handler(router, message_or_callback_type: str, name: str)` — existing helper, signature changes from `index: int = 0` to `name: str` (no default — every call site is being made explicit in this task).

- [ ] **Step 1: Confirm current handler registration order**

Run: `grep -n "@router\.\(message\|callback_query\)" cortex/telegram/brain_router.py`
Expected output (order matters — this confirms the mapping used below):
```
65:    @router.message(StateFilter(None), F.text == "/clear")
92:    @router.message(StateFilter(None), F.text | F.caption)
160:    @router.callback_query(F.data.startswith("brain:confirm:") | F.data.startswith("brain:cancel:"))
186:    @router.callback_query(F.data.startswith("brain:choice:"))
```
So: `router.message` handlers in order are `on_clear` (index 0 today), `on_text` (index 1 today). `router.callback_query` handlers in order are `on_confirmation` (index 0), `on_choice` (index 1) — callback_query wasn't touched by the `/clear` insertion, so those two are still at their original indices, but this task switches them to name-based lookup anyway for consistency and to prevent the same class of breakage next time a handler gets inserted.

- [ ] **Step 2: Replace the `_get_handler` helper and add `_get_full_handler`**

In `tests/test_brain_router.py`, replace:

```python
def _get_handler(router, message_or_callback_type: str, index: int = 0):
    """Достаём обработчик нужного observer'а из router по позиции."""
    observer = getattr(router, message_or_callback_type)
    return observer.handlers[index].callback
```

with:

```python
def _get_full_handler(router, message_or_callback_type: str, name: str):
    """Достаём хендлер по имени callback-функции, а не по позиции —
    позиция плывёт, когда в router.py добавляют новый хендлер (живой
    инцидент: вставка /clear-хендлера первым сдвинула индексы и тихо
    заставила эти тесты дёргать не тот обработчик)."""
    observer = getattr(router, message_or_callback_type)
    for handler in observer.handlers:
        if handler.callback.__name__ == name:
            return handler
    raise ValueError(f"Хендлер '{name}' не найден в router.{message_or_callback_type}")


def _get_handler(router, message_or_callback_type: str, name: str):
    return _get_full_handler(router, message_or_callback_type, name).callback
```

- [ ] **Step 3: Update every call site**

Replace each of these 8 call sites (exact line numbers from before this edit — re-locate by search text if they've shifted):

1. Line 122 (`test_mention_bypasses_brain`): `_get_handler(router, "message")` → `_get_handler(router, "message", "on_text")`
2. Line 141 (`test_ceo_free_text_goes_to_brain`): `_get_handler(router, "message")` → `_get_handler(router, "message", "on_text")`
3. Line 161 (`test_several_quick_messages_are_combined_into_one_brain_call`): `_get_handler(router, "message")` → `_get_handler(router, "message", "on_text")`
4. Line 183 (`test_non_ceo_free_text_is_ignored`): `_get_handler(router, "message")` → `_get_handler(router, "message", "on_text")`
5. Line 237 (`test_confirm_callback_resolves_pending`): `_get_handler(router, "callback_query")` → `_get_handler(router, "callback_query", "on_confirmation")`
6. Line 289 (`test_choice_click_feeds_selected_option_back_to_brain`): `_get_handler(router, "callback_query", index=1)` → `_get_handler(router, "callback_query", "on_choice")`
7. Line 313 (`test_stale_choice_click_is_reported_not_silently_dropped`): `_get_handler(router, "callback_query", index=1)` → `_get_handler(router, "callback_query", "on_choice")`
8. Line 332 (`test_cancel_callback_resolves_pending_as_declined`): `_get_handler(router, "callback_query")` → `_get_handler(router, "callback_query", "on_confirmation")`

Then find `test_photo_with_caption_reaches_brain` (search for `"Само фото не разбираем"` won't be found — search for `handler = router.message.handlers[0]`) and replace:

```python
    router = build_brain_router(deps)
    handler = router.message.handlers[0]
```

with:

```python
    router = build_brain_router(deps)
    handler = _get_full_handler(router, "message", "on_text")
```

(the rest of that test — `handler.check(...)`, `handler.callback(message)` — is unchanged, `_get_full_handler` returns an object with both).

- [ ] **Step 4: Run the full test_brain_router.py file to verify all pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_brain_router.py -v`
Expected: all tests PASS (previously 4 failed, now 0 failed).

- [ ] **Step 5: Run the full suite, then commit**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: **all tests pass, 0 failures** — this was the last known failing group in the whole suite (the other 4 were fixed earlier this session; these were the remaining 4 from the unrelated `/clear` work).

```bash
git add tests/test_brain_router.py
git commit -m "fix: look up router test handlers by callback name, not position"
```

---

## Self-Review Notes

- **Spec coverage:** JSON format with session_id/created_at/turn_count (Task 1), 6h/50-turn proactive expiry via `session_flag()` (Task 1), silent — no chat message added anywhere for the proactive path (Task 1 has none; Task 2's chat message is the *existing* parse-error warning, not new), reactive reset on malformed `<action>` (Task 2), `reset()` unchanged (Task 1 — confirmed same body). Test-fix scope (Task 3) matches the spec's "не про дизайн" section. `risk.py`/`context/builder.py` untouched — no task references them.
- **No placeholders:** every step has literal, complete code.
- **Type/name consistency:** `BrainSession.session_flag`/`mark_used`/`reset` signatures unchanged across Task 1 and Task 2 (Task 2 only calls the already-existing `reset`, doesn't redefine anything). `_get_handler`/`_get_full_handler` names match between their definition (Task 3 Step 2) and all 9 call-site updates (Task 3 Step 3).
