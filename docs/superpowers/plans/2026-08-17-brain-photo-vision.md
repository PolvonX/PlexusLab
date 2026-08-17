# Brain Photo Vision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the CEO send a photo (with or without a caption) to Cortex and have the
brain actually see its content — right now `brain_router.py` only forwards the caption,
the image itself is discarded.

**Architecture:** A new, separate one-shot `claude_vision` driver transcribes the photo
to text (`cortex/vision/describe.py`) via `claude.cmd --input-format stream-json --tools
""` — the SAME tool-less isolation the brain's own `--resume` session already runs
under. The transcript is appended as plain text to the message before it reaches the
brain. The brain's resumed session is not touched at all.

**Tech Stack:** Python 3.11, asyncio, aiogram 3.x (`Bot.download`), `claude.cmd` CLI
(`--input-format stream-json --output-format stream-json`), pytest/pytest-asyncio.

## Global Constraints

- The brain's `--resume` session must remain 100% tool-less (`--tools ""`) — nothing in
  this plan gives it filesystem access. Verified live: `--tools "Read"` alone reads any
  file on disk (confirmed against `.env` and `C:\Windows\System32\drivers\etc\hosts`),
  which is unacceptable for a process that ingests untrusted chat text.
- Vision transcription runs as a **separate** one-shot `claude.cmd` process — never
  inside the brain's resumed session.
- Vision model is **sonnet**, not haiku — a wrong table transcription is worse than the
  cost of an extra sonnet call on a rare event (photos aren't sent every turn).
- Any transcription failure must degrade to an honest text note in the brain's prompt,
  never silence or a crash.
- Follow existing project conventions exactly: `RunnerDriver`/`Config._load_driver`
  pattern (`cortex/config.py`), `FakeRunner`-style test doubles (`tests/test_brain_agent.py`),
  handler-lookup-by-name in router tests (`tests/test_brain_router.py::_get_full_handler`).

---

## Task 1: Vision driver wiring (config.yaml + Config.vision_driver + mock_vision.py)

**Files:**
- Modify: `cortex/config.py:174-179` (insert new property right after `brain_driver`)
- Modify: `config.yaml:109-124` (insert two new driver entries between `claude_haiku`
  and `timeout_seconds`)
- Create: `scripts/mock_vision.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.vision_driver -> RunnerDriver` property, used by Task 2.
- Produces: driver name `"claude_vision"` (real) and `"mock_vision"` (manual testing
  without a live `claude.cmd` subscription) registered in `config.yaml`.

- [ ] **Step 1: Write the failing tests**

Open `tests/test_config.py` and add these two tests at the end of the file:

```python
def test_vision_driver_defaults_to_claude_vision(tmp_path, secrets):
    raw = _raw()
    raw["agent_runner"]["drivers"]["claude_vision"] = {
        "command": 'claude.cmd -p --input-format stream-json --output-format stream-json --tools ""'
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)

    driver = cfg.vision_driver

    assert driver.name == "claude_vision"
    assert "stream-json" in driver.command


def test_vision_driver_env_override(tmp_path, secrets, monkeypatch):
    raw = _raw()
    raw["agent_runner"]["drivers"]["mock_vision"] = {
        "command": 'python "{root}/scripts/mock_vision.py" --prompt-file "{prompt_file}"'
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    monkeypatch.setenv("PLEXUS_VISION_DRIVER", "mock_vision")

    driver = cfg.vision_driver

    assert driver.name == "mock_vision"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -k vision_driver -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'vision_driver'`

- [ ] **Step 3: Implement `Config.vision_driver`**

In `cortex/config.py`, right after the `brain_driver` property (currently lines
174-179, ends with `return self._load_driver(name)`), insert:

```python

    @property
    def vision_driver(self) -> RunnerDriver:
        """Одноразовый драйвер транскрипции фото в текст — НЕ сессия мозга.
        PLEXUS_VISION_DRIVER=mock_vision — для отладки без живого claude."""
        name = os.getenv("PLEXUS_VISION_DRIVER") or "claude_vision"
        return self._load_driver(name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: all PASS (including the two new vision_driver tests and every pre-existing
test in this file — nothing else in this file changed).

- [ ] **Step 5: Add the real driver entries to `config.yaml`**

In `config.yaml`, find the `claude_haiku:` driver block (currently lines 114-122):

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

  timeout_seconds: 900
```

Insert two new driver entries between `claude_haiku`'s block and `timeout_seconds`:

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

    claude_vision:
      # Одноразовая транскрипция фото в текст (cortex/vision/describe.py) — НЕ
      # сессия мозга, НЕ --resume. --tools "" — тот же контур безопасности, что
      # у мозга (проверено вживую: --tools "Read" читает любой файл на диске
      # без ограничения, поэтому мозг и транскрипция остаются toolless).
      # stream-json input/output — так фото уходит как inline base64 content-блок,
      # без файлового пути, без Read. --no-session-persistence — транскрипция не
      # оставляет сессионных файлов на диске. Модель — sonnet, не haiku: неверная
      # транскрипция таблицы — это молчаливо неверные данные, дороже лишнего вызова.
      command: >
        claude.cmd -p --input-format stream-json --output-format stream-json
        --system-prompt-file "{system_prompt_file}" --tools ""
        --model sonnet --no-session-persistence
      prompt_via_stdin: true
      env: {}

    mock_vision:
      command: 'python "{root}/scripts/mock_vision.py" --prompt-file "{prompt_file}"'
      prompt_via_stdin: false
      env: {}

  timeout_seconds: 900
```

- [ ] **Step 6: Create `scripts/mock_vision.py`**

```python
#!/usr/bin/env python
"""Заглушка vision-driver — проверка transcribe_photo() и всего бота целиком
без реального claude.cmd/подписки.

Включается через PLEXUS_VISION_DRIVER=mock_vision (см. Config.vision_driver
в cortex/config.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock vision CLI for photo-transcription testing")
    parser.add_argument("--prompt-file", dest="prompt_file")
    args, _unknown = parser.parse_known_args()

    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as fh:
            fh.read()  # содержимое (base64-картинка) не нужно заглушке, просто выходим

    result = {"type": "result", "result": "Mock-транскрипция фото (реального claude.cmd тут нет)."}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Verify config.yaml parses and both drivers resolve for real**

Run:
```bash
.venv/Scripts/python.exe -c "
from cortex.config import Config
cfg = Config.load()
d = cfg._load_driver('claude_vision')
print('claude_vision OK:', 'stream-json' in d.command)
d = cfg._load_driver('mock_vision')
print('mock_vision OK:', d.name)
"
```
Expected output:
```
claude_vision OK: True
mock_vision OK: mock_vision
```

- [ ] **Step 8: Commit**

```bash
git add cortex/config.py config.yaml scripts/mock_vision.py tests/test_config.py
git commit -m "feat: add one-shot vision driver for photo transcription"
```

---

## Task 2: `cortex/vision/describe.py` — photo transcription

**Files:**
- Create: `cortex/vision/describe.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Consumes: `Config.vision_driver` (Task 1), `Config.data_dir` (existing), `AgentRunner.run(*, prompt, workspace, agent, project, system_prompt=None, driver=None, ...) -> AgentResult` (existing, `cortex/runtime/runner.py:148-159`), `AgentResult.stdout: str` (existing, `cortex/models.py:139-147`), `AgentRunError` (existing, `cortex/errors.py`).
- Produces: `async def transcribe_photo(*, image_bytes: bytes, deps: "Deps") -> str | None` —
  used by Task 3. Never raises; returns `None` on any failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vision.py`:

```python
# tests/test_vision.py
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from cortex.config import Config
from cortex.errors import AgentRunError
from cortex.models import AgentResult
from cortex.vision.describe import transcribe_photo


def _config_with_vision_driver(tmp_path, secrets, command: str = "unused") -> Config:
    raw = {
        "company": {"name": "Plexus Lab", "ceo_name": "Abdulloh Abbosov"},
        "paths": {"data_dir": "data", "prompts_dir": "prompts", "projects_dir": "projects"},
        "agent_runner": {
            "driver": "agy",
            "drivers": {
                "agy": {"command": "unused"},
                "claude_vision": {"command": command},
            },
        },
        "brain": {},
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@dataclass
class _FakeDeps:
    config: Config
    runner: object


class _RecordingRunner:
    """Записывает kwargs каждого вызова .run() и либо возвращает заданный
    AgentResult, либо бросает заданное исключение — тот же паттерн, что
    _RecordingRunner/_ExplodingRunner в tests/test_brain_agent.py."""

    def __init__(self, *, result: AgentResult | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    async def run(self, **kwargs) -> AgentResult:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _stream_json_result(text: str) -> str:
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}),
        json.dumps({"type": "result", "result": text}),
    ]
    return "\n".join(lines) + "\n"


async def test_transcribe_photo_returns_result_text_from_valid_stream_json(tmp_path, secrets):
    cfg = _config_with_vision_driver(tmp_path, secrets)
    result = AgentResult(
        stdout=_stream_json_result("| A | B |\n|---|---|\n| 1 | 2 |"),
        stderr="", returncode=0, duration=1.2, command="claude.cmd",
    )
    deps = _FakeDeps(config=cfg, runner=_RecordingRunner(result=result))

    text = await transcribe_photo(image_bytes=b"\xff\xd8\xff\xe0fake-jpeg", deps=deps)

    assert text == "| A | B |\n|---|---|\n| 1 | 2 |"


async def test_transcribe_photo_returns_none_when_no_result_line(tmp_path, secrets):
    cfg = _config_with_vision_driver(tmp_path, secrets)
    result = AgentResult(
        stdout=json.dumps({"type": "system", "subtype": "init"}) + "\n",
        stderr="", returncode=0, duration=0.5, command="claude.cmd",
    )
    deps = _FakeDeps(config=cfg, runner=_RecordingRunner(result=result))

    text = await transcribe_photo(image_bytes=b"fake", deps=deps)

    assert text is None


async def test_transcribe_photo_returns_none_on_agent_run_error(tmp_path, secrets):
    cfg = _config_with_vision_driver(tmp_path, secrets)
    runner = _RecordingRunner(error=AgentRunError("boom", returncode=1, duration=0.1))
    deps = _FakeDeps(config=cfg, runner=runner)

    text = await transcribe_photo(image_bytes=b"fake", deps=deps)

    assert text is None


async def test_transcribe_photo_sends_base64_image_and_uses_vision_driver(tmp_path, secrets):
    cfg = _config_with_vision_driver(tmp_path, secrets)
    result = AgentResult(
        stdout=_stream_json_result("ok"), stderr="", returncode=0, duration=0.1, command="claude.cmd",
    )
    runner = _RecordingRunner(result=result)
    deps = _FakeDeps(config=cfg, runner=runner)
    raw_bytes = b"\x89PNG\r\nfake-bytes"

    await transcribe_photo(image_bytes=raw_bytes, deps=deps)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["agent"] == "Vision"
    assert call["project"] == "__vision__"
    assert call["driver"].name == "claude_vision"

    payload = json.loads(call["prompt"])
    content = payload["message"]["content"]
    image_block = next(b for b in content if b["type"] == "image")
    assert image_block["source"]["data"] == base64.b64encode(raw_bytes).decode("ascii")
    assert image_block["source"]["media_type"] == "image/jpeg"
    text_block = next(b for b in content if b["type"] == "text")
    assert text_block["text"]


async def test_transcribe_photo_workspace_is_isolated_not_project_root(tmp_path, secrets):
    cfg = _config_with_vision_driver(tmp_path, secrets)
    result = AgentResult(
        stdout=_stream_json_result("ok"), stderr="", returncode=0, duration=0.1, command="claude.cmd",
    )
    deps = _FakeDeps(config=cfg, runner=_RecordingRunner(result=result))

    await transcribe_photo(image_bytes=b"fake", deps=deps)

    workspace = deps.runner.calls[0]["workspace"]
    assert workspace != cfg.root
    assert workspace.name == "vision_workspace"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cortex.vision'`

- [ ] **Step 3: Implement `cortex/vision/describe.py`**

```python
# cortex/vision/describe.py
"""Одноразовая транскрипция фото в текст — НЕ сессия мозга.

Мозг работает с --tools "" (см. cortex/brain/agent.py::_brain_workspace) и не
должен получать файловый доступ ради одной картинки: --tools "Read" читает
любой файл на диске без ограничения (проверено вживую на .env и hosts), а
inline base64-картинка через stream-json input работает и без единого
инструмента. Эта функция изолирована от BrainAgent намеренно — сбой
транскрипции не должен ронять ход мозга, поэтому она никогда не бросает
исключений наружу.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

from ..logging_setup import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("vision")

_SYSTEM_PROMPT = (
    "Перед тобой изображение. Перенеси его содержимое в текст максимально "
    "полно и дословно: таблицы — markdown-таблицей со всеми ячейками, весь "
    "видимый текст — как есть, с сохранением структуры (заголовки, списки, "
    "подписи). Не интерпретируй, не сокращай, не добавляй ничего от себя. "
    "Если на фото нет текста/таблицы — опиши одним предложением, что на нём "
    "изображено."
)

_TRANSCRIBE_INSTRUCTION = "Перенеси содержимое этого изображения в текст."


def _build_prompt(image_b64: str) -> str:
    message = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                },
                {"type": "text", "text": _TRANSCRIBE_INSTRUCTION},
            ],
        },
    }
    return json.dumps(message, ensure_ascii=False)


def _extract_result_text(stdout: str) -> str | None:
    """--output-format stream-json пишет по JSON-объекту в строку — system-события,
    ассистентские сообщения, финальный {"type": "result", "result": "..."}. Не-JSON
    строки и строки без ожидаемых полей пропускаем молча."""
    result_text: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "result":
            value = payload.get("result")
            if isinstance(value, str):
                result_text = value
    return result_text


async def transcribe_photo(*, image_bytes: bytes, deps: "Deps") -> str | None:
    """Переносит содержимое фото в текст через одноразовый vision-driver.
    Возвращает None при любой ошибке — вызывающий код (brain_router.py)
    сам решает, как деградировать (честная пометка в тексте, не тишина)."""
    workspace = deps.config.data_dir / "vision_workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    prompt = _build_prompt(image_b64)

    try:
        result = await deps.runner.run(
            prompt=prompt,
            workspace=workspace,
            agent="Vision",
            project="__vision__",
            system_prompt=_SYSTEM_PROMPT,
            driver=deps.config.vision_driver,
        )
    except Exception:  # noqa: BLE001 — транскрипция не должна ронять ход мозга
        log.exception("Транскрипция фото упала")
        return None

    text = _extract_result_text(result.stdout)
    if not text:
        log.warning("Vision-driver не вернул result-строку: %r", result.stdout[:500])
        return None
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vision.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add cortex/vision/describe.py tests/test_vision.py
git commit -m "feat: add one-shot photo-to-text transcription (cortex/vision)"
```

---

## Task 3: Wire photo transcription into `on_text`

**Files:**
- Modify: `cortex/telegram/brain_router.py:23` (add import), `:91-100` (filter + body)
- Modify: `tests/test_brain_router.py`

**Interfaces:**
- Consumes: `transcribe_photo(*, image_bytes: bytes, deps) -> str | None` (Task 2, imported
  as `from ..vision.describe import transcribe_photo`), `Bot.download(file_id: str) ->
  BinaryIO | None` (aiogram, existing — confirmed signature: passing a bare `file_id`
  string works, returns `BytesIO` when no destination given).

- [ ] **Step 1: Update shared test fixtures (no behavior change yet)**

In `tests/test_brain_router.py`, three fixture-only edits so existing tests keep
passing once `on_text` starts reading `message.photo`:

**1a.** Replace the `_message()` helper (currently lines 87-95):

```python
def _message(text: str, user_id: int = CEO_ID, *, photo=None):
    return SimpleNamespace(
        text=text,
        photo=photo,
        chat=SimpleNamespace(id=CHAT),
        message_id=7,
        from_user=SimpleNamespace(id=user_id, full_name="Someone", is_bot=False),
        reply=_noop_reply,
        answer=_noop_reply,
    )
```

**1b.** In `test_photo_with_caption_reaches_brain` (currently lines 200-234), the
manually-built `message = SimpleNamespace(...)` (lines 218-226) needs a `photo=None`
field added — this test is specifically about caption-filter pass-through, not photo
transcription (that's covered by the new tests below), so it stays a message with no
actual photo attached:

```python
    message = SimpleNamespace(
        text=None,
        caption="вот так ты отвечаешь, почини",
        photo=None,
        chat=SimpleNamespace(id=CHAT),
        message_id=7,
        from_user=SimpleNamespace(id=CEO_ID, full_name="CEO", is_bot=False),
        reply=_noop_reply,
        answer=_noop_reply,
    )
```

**1c.** Add a fake gateway (Telegram file download) and give `_FakeDeps` a `gateway`
field. Add right after the `_FakeHistory` class (currently ends at line 73, before the
`@dataclass` / `class _FakeDeps` block):

```python
class _FakeGatewayBot:
    def __init__(self, payload: bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes") -> None:
        self.payload = payload
        self.requested_file_ids: list[str] = []

    async def download(self, file_id: str):
        self.requested_file_ids.append(file_id)
        return io.BytesIO(self.payload)


@dataclass
class _FakeGateway:
    gateway_bot: object
```

Then update the `_FakeDeps` dataclass (currently lines 76-84) to add a `gateway` field:

```python
@dataclass
class _FakeDeps:
    brain: object
    mentions: object
    orchestrator: object
    config: object = field(default_factory=_FakeConfig)
    scheduler: object = None
    history: object = field(default_factory=_FakeHistory)
    choices: object = None
    gateway: object = field(default_factory=lambda: _FakeGateway(gateway_bot=_FakeGatewayBot()))
```

(`gateway` defaults to a working fake so every pre-existing test in this file keeps
constructing `_FakeDeps(...)` exactly as before, without needing to pass `gateway=`.)

Add `import io` to the imports at the top of the file (next to `import asyncio`).

- [ ] **Step 2: Run the full router test file to confirm nothing broke yet**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_router.py -v`
Expected: all PASS (9/9, same as before — Step 1 only touched fixtures, `on_text`
itself hasn't changed yet, so `message.photo` is never read in production code).

- [ ] **Step 3: Write the new failing tests**

Add these three tests at the end of `tests/test_brain_router.py`:

```python
def _photo_message(*, caption: str | None = None, user_id: int = CEO_ID):
    return SimpleNamespace(
        text=None,
        caption=caption,
        photo=[
            SimpleNamespace(file_id="thumb", file_size=1000),
            SimpleNamespace(file_id="full", file_size=80000),
        ],
        chat=SimpleNamespace(id=CHAT),
        message_id=7,
        from_user=SimpleNamespace(id=user_id, full_name="CEO", is_bot=False),
        reply=_noop_reply,
        answer=_noop_reply,
    )


async def test_photo_with_caption_and_transcript_reaches_brain_combined(
    config, registry, workspaces, monkeypatch
):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    async def _fake_transcribe(*, image_bytes, deps):
        assert image_bytes == b"\xff\xd8\xff\xe0fake-jpeg-bytes"
        return "| A | B |\n| 1 | 2 |"

    monkeypatch.setattr("cortex.telegram.brain_router.transcribe_photo", _fake_transcribe)

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    gateway_bot = _FakeGatewayBot()
    deps = _FakeDeps(
        brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator(),
        gateway=_FakeGateway(gateway_bot=gateway_bot),
    )

    router = build_brain_router(deps)
    handler = _get_handler(router, "message", "on_text")

    await handler(_photo_message(caption="создай такой excel файл"))
    await asyncio.sleep(0.05)  # окно debounce (0.01с в _FakeConfig) должно истечь

    assert gateway_bot.requested_file_ids == ["full"]  # взят самый крупный вариант
    assert brain.handled == [
        (CHAT, 7, "создай такой excel файл\n\n[Фото распознано]:\n| A | B |\n| 1 | 2 |", CEO_ID)
    ]


async def test_bare_photo_without_caption_reaches_brain(config, registry, workspaces, monkeypatch):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    async def _fake_transcribe(*, image_bytes, deps):
        return "Фото стола с ноутбуком."

    monkeypatch.setattr("cortex.telegram.brain_router.transcribe_photo", _fake_transcribe)

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(
        brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator(),
        gateway=_FakeGateway(gateway_bot=_FakeGatewayBot()),
    )

    router = build_brain_router(deps)
    handler = _get_full_handler(router, "message", "on_text")

    message = _photo_message(caption=None)
    matched, _kwargs = await handler.check(message, state=SimpleNamespace(get_state=lambda: None))
    assert matched, "фильтр должен пропускать голое фото без подписи"

    await handler.callback(message)
    await asyncio.sleep(0.05)

    # photo_block всегда несёт префикс "[Фото распознано]:" — даже без
    # исходного текста, ради единообразия с случаем "подпись + фото"
    assert brain.handled == [(CHAT, 7, "[Фото распознано]:\nФото стола с ноутбуком.", CEO_ID)]


async def test_transcription_failure_reaches_brain_as_honest_note(config, registry, workspaces, monkeypatch):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    async def _fake_transcribe(*, image_bytes, deps):
        return None

    monkeypatch.setattr("cortex.telegram.brain_router.transcribe_photo", _fake_transcribe)

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(
        brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator(),
        gateway=_FakeGateway(gateway_bot=_FakeGatewayBot()),
    )

    router = build_brain_router(deps)
    handler = _get_handler(router, "message", "on_text")

    await handler(_photo_message(caption="глянь"))
    await asyncio.sleep(0.05)

    assert brain.handled == [(CHAT, 7, "глянь\n\n[Фото приложено, распознать не удалось]", CEO_ID)]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_router.py -k photo -v`
Expected: FAIL — the bare-photo test fails the `assert matched` (filter doesn't include
`F.photo` yet); the other two fail because `brain.handled` stays empty (no caption ⇒
`on_text` returns early since `message.photo` is never inspected yet).

- [ ] **Step 5: Implement the `on_text` changes**

In `cortex/telegram/brain_router.py`, add the import (near the top, after the existing
`from .debounce import MessageDebouncer` line, currently line 23):

```python
from ..vision.describe import transcribe_photo
```

Then replace the `on_text` handler (currently lines 91-100):

```python
    # ------------------------------------------------------------------
    @router.message(StateFilter(None), F.text | F.caption)
    async def on_text(message: Message) -> None:
        # message.text — для обычного текста, caption — для фото/файлов с
        # подписью (живой инцидент: CEO прислал скриншот-жалобу с подписью,
        # F.text один её не пропускал вообще, бот молчал — выглядело как
        # падение). Само фото не разбираем, только подпись.
        text = message.text or message.caption or ""
        if not text or text.startswith("/"):
            return  # слэш-команд больше нет — не отвечаем на призраков старого UX
```

with:

```python
    # ------------------------------------------------------------------
    @router.message(StateFilter(None), F.text | F.caption | F.photo)
    async def on_text(message: Message) -> None:
        # message.text — для обычного текста, caption — для фото/файлов с
        # подписью (живой инцидент: CEO прислал скриншот-жалобу с подписью,
        # F.text один её не пропускал вообще, бот молчал — выглядело как
        # падение). Фото распознаётся отдельным одноразовым вызовом
        # (cortex/vision/describe.py) — сама сессия мозга остаётся toolless.
        text = message.text or message.caption or ""

        if message.photo:
            largest = max(
                (p for p in message.photo if not p.file_size or p.file_size <= 3_500_000),
                default=message.photo[-1],
                key=lambda p: p.file_size or 0,
            )
            buf = await deps.gateway.gateway_bot.download(largest.file_id)
            image_bytes = buf.getvalue() if buf else b""
            transcript = (
                await transcribe_photo(image_bytes=image_bytes, deps=deps)
                if image_bytes else None
            )
            photo_block = (
                f"[Фото распознано]:\n{transcript}" if transcript
                else "[Фото приложено, распознать не удалось]"
            )
            text = f"{text}\n\n{photo_block}" if text else photo_block

        if not text or text.startswith("/"):
            return  # слэш-команд больше нет — не отвечаем на призраков старого UX
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain_router.py -v`
Expected: all 12 tests PASS (9 pre-existing + 3 new)

- [ ] **Step 7: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests PASS, 0 failed (same or higher count than the 275 baseline from the
previous session — this plan only adds tests, never removes any).

- [ ] **Step 8: Commit**

```bash
git add cortex/telegram/brain_router.py tests/test_brain_router.py
git commit -m "feat: wire photo transcription into the brain router"
```

---

## Final step: log completion in AGENTS.md

- [ ] After all three tasks are committed, add one line to `AGENTS.md`'s
  `## История последних сессий` section (top of the list) summarizing what landed —
  follow the exact format of the existing entries in that file (date, author, one
  paragraph of what changed and why, any deviations from the plan). Commit it as its
  own commit: `git commit -m "docs: log photo-vision implementation in AGENTS.md"`.
