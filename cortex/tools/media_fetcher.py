import asyncio
from typing import Any

from .base import Tool
from ..models import ToolResult
from ..logging_setup import get_logger

log = get_logger("media_fetcher")

_DEFAULT_TIMEOUT = 600  # yt-dlp может работать долго на больших видео


class MediaFetcherTool(Tool):
    """
    Инструмент для скачивания медиа и извлечения аудио с использованием yt-dlp.
    """
    name = "media_fetcher"

    async def execute(self, action_args: dict[str, Any], ctx: Any) -> ToolResult:
        url = action_args.get("url")
        if not url:
            return ToolResult.failure("Параметр 'url' обязателен.")

        extract_audio = action_args.get("extract_audio", False)

        # Директория для сохранения файлов
        downloads_dir = ctx.config.data_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        # Формируем команду для yt-dlp
        cmd = [
            "yt-dlp",
            url,
            "--no-simulate",
            "--print", "after_move:filepath",  # Вывести только итоговый путь к файлу
        ]

        if extract_audio:
            cmd.extend(["-x", "--audio-format", "mp3"])

        output_template = str(downloads_dir / "%(title)s.%(ext)s")
        cmd.extend(["-o", output_template])

        log.info("Запуск yt-dlp для URL: %s", url)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=_DEFAULT_TIMEOUT
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except OSError:
                    pass
                return ToolResult.failure(
                    f"yt-dlp не уложился в {_DEFAULT_TIMEOUT} с и был снят."
                )

            stdout = stdout_bytes.decode(errors="replace").strip()
            stderr = stderr_bytes.decode(errors="replace").strip()

            if process.returncode != 0:
                log.error("Ошибка yt-dlp (код %s): %s", process.returncode, stderr)
                return ToolResult.failure(
                    f"Не удалось скачать медиа (код {process.returncode}).",
                    detail=stderr,
                )

            # yt-dlp с флагом --print выведет абсолютный путь к скачанному файлу
            filepath = stdout.splitlines()[-1] if stdout else "Неизвестный путь"

            return ToolResult.success(
                f"Файл успешно скачан.\nПуть: {filepath}",
                detail=stdout,
            )

        except FileNotFoundError:
            return ToolResult.failure(
                "Утилита yt-dlp не найдена в системе. Убедитесь, что она установлена "
                "(pip install yt-dlp) и доступна в PATH."
            )
        except Exception as exc:
            log.exception("Непредвиденная ошибка в media_fetcher")
            return ToolResult.failure(f"Ошибка выполнения: {exc}")