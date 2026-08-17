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
