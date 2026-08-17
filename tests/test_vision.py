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
