# cortex/brain/tools/custom.py
"""create_tool — мозг пишет и регистрирует себе новые инструменты.

Сгенерированный код никогда не получает доступа к deps/секретам Cortex
напрямую: он сохраняется отдельным .py-файлом и всегда запускается
подпроцессом в изолированной cwd. Даже если сгенерированный код выйдет
неудачным или опасным, его возможности не шире, чем у обычного скрипта в
песочнице проекта.

Инструмент запускается НАПРЯМУЮ (create_subprocess_exec с явным argv), а
не через cmd.exe, как execute_command — здесь нет пользовательской shell-
строки с пайпами/&&, всегда фиксированные три аргумента (python, script,
args-файл), так что оболочка не нужна вообще. Это не только проще, но и
обходит живую находку: cmd.exe с /S ломает командную строку, когда она
сама начинается и заканчивается кавычкой (наш "python.exe" "script.py"
"args.json" — ровно такой случай), в отдельных сценариях либо зависая,
либо возвращая "не является внутренней или внешней командой".

create_tool сам по себе risky (создание нового кода — не мелочь), и каждый
запуск уже созданного инструмента — тоже risky по умолчанию: агент здесь
пишет код сам себе, без ревью человека, так что "не исполнять без
подтверждения" — разумный дефолт, а не блокер. Понизить риск для
конкретного инструмента можно через brain.risk_overrides в config.yaml —
этот механизм уже существует, отдельного трогать не нужно.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid

from ...errors import ToolError
from ...models import Action, ToolResult
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext, BrainToolRegistry
from .custom_store import CustomToolRecord, CustomToolStore
from ...runtime.sandbox import SandboxExecutor

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_MAX_CODE_CHARS = 20_000
_MAX_OUTPUT_CHARS = 2500
_DEFAULT_TIMEOUT = 120


class CreateToolTool(BrainTool):
    name = "create_tool"
    description = (
        "Написать и сохранить себе новый переиспользуемый инструмент на "
        "Python — когда встроенных инструментов не хватает для задачи. "
        "Требует подтверждения CEO: новый код появляется в системе не "
        "бесследно. После подтверждения инструмент сразу доступен по имени "
        "в следующем же ходу — перезапуск сервера не нужен. Контракт: "
        "sys.argv[1] — путь к JSON-файлу с args, вывод — через stdout. "
        "Если СВОЙ ЖЕ ранее созданный инструмент падает с ошибкой в коде — "
        "вызови create_tool с тем же именем ещё раз: код перезапишется, "
        "старая версия не сохраняется. Встроенные инструменты (не твои) "
        "переопределить нельзя."
    )
    usage = (
        '{"tool": "create_tool", "args": {"name": "count_words", '
        '"description": "Считает слова в текстовом файле", '
        '"code": "import sys, json\\nwith open(sys.argv[1]) as f: args = json.load(f)\\n'
        'print(len(open(args[\\"path\\"]).read().split()))"}}'
    )
    risk = RiskTier.RISKY

    def __init__(self, *, store: CustomToolStore, registry: BrainToolRegistry) -> None:
        self._store = store
        self._registry = registry

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        name = str(action.args.get("name") or "").strip().lower()
        description = str(action.args.get("description") or "").strip()
        code = str(action.args.get("code") or "")

        if not _NAME_RE.match(name):
            raise ToolError(
                "недопустимое имя (строчные латинские буквы/цифры/_, "
                "3-40 символов, начинается с буквы)"
            )
        existing = self._registry.get(name)
        if existing is not None and not existing.is_custom:
            raise ToolError(f"'{name}' — встроенный инструмент, его нельзя переопределить")
        if not description:
            raise ToolError("нужно описание (args.description)")
        if not code.strip():
            raise ToolError("пустой код (args.code)")
        if len(code) > _MAX_CODE_CHARS:
            raise ToolError(
                f"код слишком большой ({len(code)} символов, максимум {_MAX_CODE_CHARS})"
            )

        was_update = existing is not None
        script_path = self._store.save_script(name, code)
        record = CustomToolRecord(
            name=name,
            description=description,
            usage=str(action.args.get("usage") or "").strip(),
            script_path=str(script_path),
            created_by=str(ctx.requester_id),
        )
        await self._store.add(record)
        self._registry.register(RunCustomToolTool(record))

        verb = "переписан" if was_update else "создан"
        return ToolResult.success(
            f"Инструмент '{name}' {verb} и зарегистрирован",
            f"Файл: {script_path}. Доступен для вызова со следующего хода.",
        )


class RunCustomToolTool(BrainTool):
    """Один экземпляр на каждый созданный инструмент — .name/.description
    берутся из записи в сторе, поэтому в реестре у каждого своё имя, как у
    обычного встроенного инструмента."""

    risk = RiskTier.RISKY
    is_custom = True

    def __init__(self, record: CustomToolRecord) -> None:
        self.name = record.name
        self.description = record.description
        self.usage = record.usage or f'{{"tool": "{record.name}", "args": {{}}}}'
        self._script_path = record.script_path

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        workspace = ctx.deps.config.data_dir / "brain_workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        args_file = workspace / f".custom_tool_args_{uuid.uuid4().hex[:8]}.json"
        args_file.write_text(json.dumps(action.args, ensure_ascii=False), encoding="utf-8")
        try:
            executor = SandboxExecutor(
                max_output_chars=_MAX_OUTPUT_CHARS,
                max_timeout=ctx.deps.config.max_command_timeout,
            )
            return await executor.execute(
                script_path=str(self._script_path),
                args_path=str(args_file),
                timeout=action.args.get("timeout"),
                log_tag=f"custom/{self.name}",
            )
        finally:
            args_file.unlink(missing_ok=True)


