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
            "--username", "oauth2",
            "--password", "''",
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

            stdout_lines = []
            stderr_lines = []
            
            async def read_stdout():
                if process.stdout:
                    async for line in process.stdout:
                        line_str = line.decode(errors="replace").strip()
                        if line_str:
                            stdout_lines.append(line_str)
                            
            async def read_stderr():
                if process.stderr:
                    async for line in process.stderr:
                        line_str = line.decode(errors="replace").strip()
                        if not line_str:
                            continue
                        stderr_lines.append(line_str)
                        log.debug("yt-dlp stderr: %s", line_str)
                        
                        # Detect OAuth2 prompt
                        if "google.com/device" in line_str and "code" in line_str:
                            try:
                                code_part = line_str.split("code")[-1].strip()
                                if code_part:
                                    msg = (
                                        f"⚠️ **YouTube требует авторизацию.**\n\n"
                                        f"1. Перейдите на: https://www.google.com/device\n"
                                        f"2. Введите код: `{code_part}`\n\n"
                                        f"Скачивание продолжится автоматически."
                                    )
                                    if hasattr(ctx, "bot") and hasattr(ctx, "chat_id"):
                                        await ctx.bot.send_message(ctx.chat_id, msg, parse_mode="Markdown")
                                    else:
                                        log.info("OAuth prompt detected, but no Telegram context: %s", msg)
                            except Exception as e:
                                log.error("Failed to parse OAuth code: %s", e)

            try:
                # Wait for both streams to finish reading and the process to exit
                await asyncio.wait_for(
                    asyncio.gather(read_stdout(), read_stderr(), process.wait()), 
                    timeout=_DEFAULT_TIMEOUT
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

            stdout = "\n".join(stdout_lines)
            stderr = "\n".join(stderr_lines)

            if process.returncode != 0:
                log.error("Ошибка yt-dlp (код %s): %s", process.returncode, stderr)
                return ToolResult.failure(
                    f"Не удалось скачать медиа (код {process.returncode}).",
                    detail=stderr,
                )

            # yt-dlp с флагом --print выведет абсолютный путь к скачанному файлу
            filepath = stdout_lines[-1] if stdout_lines else "Неизвестный путь"

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