import asyncio
import os
import sys
import tempfile
import time
from typing import Optional

from ..errors import ToolError
from ..models import ToolResult


class SandboxExecutor:
    """Изолированная среда для безопасного выполнения сгенерированного кода.
    
    Обеспечивает:
    - Выполнение в отдельном процессе.
    - Временную директорию (cwd) для защиты ФС оркестратора.
    - Жесткие лимиты по времени выполнения (timeout).
    - Защиту от переполнения памяти (OOM) через потоковое чтение с ограничением.
    
    Доступ к сети (Network) открыт, так как он необходим для HTTP-клиентов агентов.
    """

    def __init__(
        self,
        max_output_chars: int = 2500,
        default_timeout: int = 60,
        max_timeout: int = 600,
    ) -> None:
        self.max_output_chars = max_output_chars
        self.max_output_bytes = max_output_chars * 4  # Запас для UTF-8
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout

    async def execute(
        self,
        script_path: str,
        args_path: str,
        timeout: Optional[int] = None,
        log_tag: str = "sandbox",
    ) -> ToolResult:
        actual_timeout = self._resolve_timeout(timeout)

        # Создаем временную директорию, которая автоматически удалится после выхода из блока
        with tempfile.TemporaryDirectory() as tmp_dir:
            return await self._run_in_process(
                sys.executable,
                script_path,
                args_path,
                cwd=tmp_dir,
                timeout=actual_timeout,
                log_tag=log_tag,
            )

    def _resolve_timeout(self, requested: Optional[int]) -> int:
        if requested is None:
            return self.default_timeout
        try:
            timeout = int(requested)
        except (TypeError, ValueError):
            timeout = self.default_timeout
        return max(1, min(timeout, self.max_timeout))

    async def _run_in_process(
        self, *argv: str, cwd: str, timeout: int, log_tag: str
    ) -> ToolResult:
        started = time.monotonic()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise ToolError(f"не удалось запустить инструмент: {exc}") from exc

        stdout_bytes = bytearray()
        output_limit_exceeded = False

        async def read_stream() -> None:
            nonlocal output_limit_exceeded
            if process.stdout is None:
                return
            
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break

                if not output_limit_exceeded:
                    stdout_bytes.extend(chunk)
                    # Проверяем на превышение лимита в байтах
                    if len(stdout_bytes) > self.max_output_bytes:
                        output_limit_exceeded = True
                        try:
                            process.kill()
                        except OSError:
                            pass
                else:
                    # Если лимит превышен, мы продолжаем читать из буфера ОС,
                    # чтобы дочерний процесс не заблокировался на записи (SIGPIPE на Windows нет),
                    # но просто отбрасываем прочитанное, защищая память оркестратора.
                    pass

        try:
            await asyncio.wait_for(
                asyncio.gather(process.wait(), read_stream()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except OSError:
                pass
            return ToolResult.failure(f"Инструмент не уложился в {timeout} с и был снят")

        duration = time.monotonic() - started
        output = stdout_bytes.decode("utf-8", errors="replace").strip()

        if output_limit_exceeded or len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + "\n… (вывод обрезан из-за превышения лимита)"
            return ToolResult.failure(f"[{log_tag}] превышен лимит вывода", output)

        if process.returncode == 0:
            return ToolResult.success(
                f"[{log_tag}] выполнено за {duration:.1f} с", output or "(пустой вывод)"
            )
        return ToolResult.failure(
            f"[{log_tag}] завершилось с кодом {process.returncode}",
            output or "(пустой вывод)",
        )
