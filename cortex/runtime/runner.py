"""AgentRunner — запуск CLI-сабагента (`agy`) как асинхронного процесса.

Отдельный модуль намеренно: логика консоли не должна знать ничего про
Telegram, а Telegram — ничего про subprocess. Команда описывается
шаблоном в config.yaml, поэтому смена синтаксиса agy не требует правок кода.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import time
import uuid
from pathlib import Path

from ..config import Config, RunnerDriver
from ..errors import AgentRunError
from ..logging_setup import get_logger
from ..models import AgentResult

log = get_logger("runner")

def _looks_garbled(text: str) -> bool:
    """decode("utf-8", errors="replace") вставляет U+FFFD на каждом байте,
    который не складывается в валидный UTF-8, — а cp866-кириллица (пробелы
    и латиница пополам с русскими буквами) на практике всегда даёт хотя бы
    один такой байт (проверено на всех реальных ошибках claude.cmd/Windows,
    что мы видели). НЕ судим по доле кириллицы/ASCII среди букв: у мозга и
    сотрудников в выводе законно бывает не-русский, не-ASCII текст (китайский
    из web_research, математические символы и т.п.) — по такой эвристике
    он ошибочно принимался за кракозябры и портился перекодированием."""
    return "�" in text


def _decode_console_bytes(data: bytes) -> str:
    """Windows иногда пишет диагностику (свою или узла claude.cmd) в OEM
    cp866, а не UTF-8. decode("utf-8", errors="replace") на таких байтах не
    падает — валидные cp866-байты случайно складываются в валидные
    многобайтовые UTF-8 последовательности, и получается нечитаемая, но
    "успешно декодированная" кракозябра (см. живой инцидент: --resume упал
    с текстом вида "���誮� ������� ...", хотя на диске у claude всё в
    порядке). Проверяем результат и при подозрении перекодируем как cp866."""
    utf8 = data.decode("utf-8", errors="replace")
    if not _looks_garbled(utf8):
        return utf8
    try:
        return data.decode("cp866")
    except UnicodeDecodeError:
        return utf8

#: Промпт подставляется в argv не через строку команды, а по метке: иначе
#: кавычки и переводы строк внутри промпта разнесли бы shlex.
_PROMPT_MARK = "\x00PLEXUS_PROMPT\x00"
_SYSTEM_PROMPT_MARK = "\x00PLEXUS_SYSTEM_PROMPT\x00"

#: Предел командной строки Windows (CreateProcess) — 32 767 символов.
#: Держим запас: превысить его значит получить невнятный OSError.
#: ВАЖНО: это лимит для настоящих .exe. Для драйверов, чья команда — это
#: .cmd/.bat (как claude.cmd), реальный потолок — ~8191 символов: такие
#: файлы всегда идут через cmd.exe /c, а не напрямую через CreateProcess
#: (см. живой инцидент — claude.cmd падал с "Слишком длинная командная
#: строка" на промпте+system_prompt всего ~8.5к символов). Для таких
#: драйверов не полагайся на этот лимит — убирай большие значения из argv
#: вовсе (prompt_via_stdin для {prompt}, {system_prompt_file} для system
#: prompt), как это сделано в config.yaml для claude.
_ARGV_LIMIT = 30_000

#: Живой инцидент: agy падает с "Individual quota reached. Please upgrade
#: your subscription to increase your limits. Resets in 20m29s." — Cortex
#: раньше репортил это как обычный фатальный крах и молча бросал задачу.
#: Паттерн ищем узко (quota + resets in рядом), чтобы не принять за
#: временную ошибку что-то незнакомое, где авто-ретрай не к месту.
_QUOTA_COOLDOWN_RE = re.compile(
    r"quota\s+reached.*?resets?\s+in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?",
    re.IGNORECASE | re.DOTALL,
)


def _parse_retry_after(stderr: str) -> float | None:
    """Секунды до сброса квоты agy, если stderr опознан как временная
    ошибка — иначе None (ошибка считается фатальной, без авто-ретрая)."""
    match = _QUOTA_COOLDOWN_RE.search(stderr)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


class AgentRunner:
    """Исполняет промпт в изолированной рабочей среде проекта."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._tmp_dir = config.data_dir / "prompts_tmp"
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _build_argv(
        self,
        *,
        driver: RunnerDriver,
        prompt: str,
        prompt_file: Path,
        system_prompt_file: Path,
        workspace: Path,
        agent: str,
        project: str,
        system_prompt: str,
        session_flag: str,
    ) -> list[str]:
        rendered = driver.command.format(
            prompt_file=str(prompt_file),
            system_prompt_file=str(system_prompt_file),
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

    # ------------------------------------------------------------------
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
        system_prompt_file = self._tmp_dir / f"{agent}-{uuid.uuid4().hex[:8]}.system.md"

        started = time.monotonic()
        process: asyncio.subprocess.Process | None = None
        try:
            # Запись temp-файлов и сборка argv — внутри try: иначе сбой
            # _build_argv (например, превышение лимита командной строки)
            # оставлял бы на диске temp-файлы с реальным содержимым
            # промпта/системного промпта навсегда — finally ниже до них
            # просто не долетал бы.
            prompt_file.write_text(prompt, encoding="utf-8")
            system_prompt_file.write_text(system_prompt or "", encoding="utf-8")

            argv = self._build_argv(
                driver=driver,
                prompt=prompt,
                prompt_file=prompt_file,
                system_prompt_file=system_prompt_file,
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
            log.info("[%s/%s] запуск: %s", project, agent, printable)

            env = {**os.environ, **driver.env}
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env["PLEXUS_AGENT"] = agent
            env["PLEXUS_PROJECT"] = project

            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=str(workspace),
                    env=env,
                    stdin=asyncio.subprocess.PIPE if driver.prompt_via_stdin else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise AgentRunError(
                    f"Исполняемый файл '{argv[0]}' не найден. Проверь, что Google "
                    f"Antigravity CLI установлен и доступен в PATH, либо поправь "
                    f"agent_runner.drivers.{driver.name}.command в config.yaml.",
                    command=printable,
                ) from exc
            except OSError as exc:
                raise AgentRunError(
                    f"Не удалось запустить сабагента: {exc}", command=printable
                ) from exc

            stdin_payload = prompt.encode("utf-8") if driver.prompt_via_stdin else None

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(stdin_payload), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._terminate(process)
                duration = time.monotonic() - started
                raise AgentRunError(
                    f"Сабагент не уложился в {timeout} с и был снят.",
                    returncode=None,
                    command=printable,
                    duration=duration,
                ) from None

            duration = time.monotonic() - started
            stdout = _decode_console_bytes(stdout_b)
            stderr = _decode_console_bytes(stderr_b)

            truncated = False
            limit = self.config.runner_max_output
            if len(stdout) > limit:
                stdout = stdout[:limit]
                truncated = True

            result = AgentResult(
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode or 0,
                duration=duration,
                command=printable,
                truncated=truncated,
            )

            if result.returncode != 0:
                log.error(
                    "[%s/%s] agy упал (код %s) за %.1f с",
                    project, agent, result.returncode, duration,
                )
                raise AgentRunError(
                    f"Процесс завершился с кодом {result.returncode}.",
                    returncode=result.returncode,
                    stderr=stderr,
                    command=printable,
                    duration=duration,
                    retry_after=_parse_retry_after(stderr),
                )

            log.info("[%s/%s] готово за %.1f с, %d символов", project, agent, duration, len(stdout))
            return result

        finally:
            prompt_file.unlink(missing_ok=True)
            system_prompt_file.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """Мягко просим уйти, через 5 секунд убиваем."""
        if process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                process.kill()
            except ProcessLookupError:
                pass
        except Exception as exc:  # noqa: BLE001 — снятие процесса не должно ронять Cortex
            log.warning("Не удалось снять процесс: %s", exc)
