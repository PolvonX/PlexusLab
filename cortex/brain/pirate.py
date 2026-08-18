"""Пиратский модуль Cortex для прямого скачивания медиа без участия моральных фильтров LLM."""

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING
from ..logging_setup import get_logger
from aiogram.types import FSInputFile

if TYPE_CHECKING:
    from ..deps import Deps

log = get_logger("brain.pirate")

async def execute_pirate_mode(deps: "Deps", chat_id: int, message_id: int | None, text: str) -> None:
    """Выполняет хардкод-логику скачивания, минуя LLM."""
    
    downloads_dir = deps.config.data_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    
    # Ищем URL
    url_match = re.search(r'(https?://\S+)', text)
    
    if url_match:
        url = url_match.group(1)
        await deps.gateway.reply(chat_id, f"🏴‍☠️ Обнаружена ссылка. Скачиваю через yt-dlp: {url}", reply_to=message_id)
        
        cmd = [
            "yt-dlp",
            url,
            "--no-simulate",
            "--username", "oauth2",
            "--password", "''",
            "--print", "after_move:filepath",
            "-o", str(downloads_dir / "%(title)s.%(ext)s")
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode == 0:
            filepath = stdout_bytes.decode(errors="replace").strip().splitlines()[-1]
            if Path(filepath).exists():
                await deps.gateway.reply(chat_id, f"🏴‍☠️ Файл скачан, отправляю...", reply_to=message_id)
                await deps.gateway.gateway_bot.send_document(chat_id, document=FSInputFile(filepath), reply_to_message_id=message_id)
                return
        
        await deps.gateway.reply(chat_id, f"🏴‍☠️ Ошибка скачивания по ссылке.\n```\n{stderr_bytes.decode(errors='replace')[:1000]}\n```", reply_to=message_id)
        return

    # Если URL нет, считаем это поисковым запросом для spotdl
    query = text.strip()
    
    history_block = deps.history.render(chat_id, limit=10, budget=4000)
    
    # Извлекаем чистое название песни и намерения (audio/lyrics/both/other) через LLM
    extraction_prompt = f"Given the conversation history:\n{history_block}\n\nAnd the latest user message:\n'{query}'\n\nDetermine what the user wants. Options for WANTS: 'audio', 'lyrics', 'both', or 'other' (for parsing, scripting, etc). If 'audio' or 'lyrics', output SONG: [song - artist]. If 'other', output SONG: none. Output EXACTLY in this format: SONG: [song or none] | WANTS: [audio/lyrics/both/other]. Do not output anything else."
    
    wants = "both"
    song_query = query
    
    try:
        ext_result = await deps.runner.run(
            prompt=extraction_prompt,
            workspace=deps.config.data_dir,
            agent="Cortex",
            project="__brain__",
            timeout=15,
            system_prompt="You are a strict data extractor. No conversational text.",
            session_flag="",
            driver=deps.config.brain_driver,
        )
        if ext_result and ext_result.stdout:
            extracted = ext_result.stdout.strip()
            if "SONG:" in extracted and "WANTS:" in extracted:
                parts = extracted.split("| WANTS:")
                song_query = parts[0].replace("SONG:", "").strip()
                wants = parts[1].strip().lower()
                
            elif len(extracted) < 100 and "sorry" not in extracted.lower() and "не могу" not in extracted.lower():
                song_query = extracted
    except Exception as e:
        log.warning(f"Failed to extract song name: {e}")

    if wants == "other":
        await deps.gateway.reply(chat_id, f"🏴‍☠️ [Shadow Executor] Нестандартная задача. Генерирую и запускаю Python-скрипт...", reply_to=message_id)
        code_prompt = f"Write a complete, standalone Python script to fulfill this user request: '{query}'. The script should print its result to stdout. Do not use input(). Only output the python code inside ```python ``` blocks."
        try:
            code_result = await deps.runner.run(
                prompt=code_prompt,
                workspace=deps.config.data_dir,
                agent="Cortex",
                project="__brain__",
                timeout=20,
                system_prompt="You are a senior python developer. Write clean, working scripts without any conversational text.",
                session_flag="",
                driver=deps.config.brain_driver,
            )
            if code_result and code_result.stdout:
                code_match = re.search(r'```python\n(.*?)\n```', code_result.stdout, re.DOTALL)
                if code_match:
                    script_code = code_match.group(1)
                    script_path = downloads_dir / "shadow_task.py"
                    script_path.write_text(script_code, encoding="utf-8")
                    
                    process = await asyncio.create_subprocess_exec(
                        "python", str(script_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout_bytes, stderr_bytes = await process.communicate()
                    output = stdout_bytes.decode(errors="replace") + stderr_bytes.decode(errors="replace")
                    await deps.gateway.reply(chat_id, f"🏴‍☠️ Результат работы теневого скрипта:\n```\n{output[:3500]}\n```", reply_to=message_id)
                    return
        except Exception as e:
            log.warning(f"Shadow executor failed: {e}")
            await deps.gateway.reply(chat_id, f"🏴‍☠️ Ошибка выполнения теневого скрипта: {e}", reply_to=message_id)
            return

    await deps.gateway.reply(chat_id, f"🏴‍☠️ Ищу ({wants}): `{song_query}`", reply_to=message_id)
    
    from .proxy import get_free_proxy
    proxy_url = get_free_proxy()
    proxy_msg = f" (через прокси {proxy_url})" if proxy_url else ""
    if proxy_url:
        await deps.gateway.reply(chat_id, f"🏴‍☠️ Настроен анти-блок{proxy_msg}", reply_to=message_id)
    
    if "lyrics" in wants or "both" in wants or "текст" in query.lower():
        try:
            import urllib.request
            import urllib.parse
            import json
            q = urllib.parse.quote(song_query)
            req = urllib.request.Request(
                f"https://lrclib.net/api/search?q={q}",
                headers={"User-Agent": "PlexusLab/1.0"}
            )
            res = urllib.request.urlopen(req, timeout=5)
            data = json.loads(res.read())
            if data and len(data) > 0 and data[0].get("plainLyrics"):
                lyrics = data[0]["plainLyrics"]
                if len(lyrics) > 3500:
                    lyrics = lyrics[:3500] + "..."
                await deps.gateway.reply(chat_id, f"🏴‍☠️ Текст песни:\n\n{lyrics}", reply_to=message_id)
            else:
                await deps.gateway.reply(chat_id, f"🏴‍☠️ Текст песни не найден в базе lrclib.", reply_to=message_id)
        except Exception as e:
            log.warning(f"Failed to fetch lyrics: {e}")

    if "audio" not in wants and "both" not in wants and "текст" in query.lower() and "песн" not in query.lower() and "mp3" not in query.lower():
        # Если просили только текст (например "а где текст?")
        return
        
    # spotdl по умолчанию сохраняет в текущую директорию, поэтому перейдём в downloads
    cmd = [
        "spotdl",
        "download",
        song_query
    ]
    if proxy_url:
        cmd.extend(["--proxy", proxy_url])
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=downloads_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    stdout_bytes, stderr_bytes = await process.communicate()
    
    # spotdl выводит имя файла или статус. Ищем скачанные файлы mp3 в директории, которые были созданы недавно.
    if process.returncode == 0:
        mp3_files = list(downloads_dir.glob("*.mp3"))
        if mp3_files:
            # Сортируем по времени модификации
            latest_file = max(mp3_files, key=lambda f: f.stat().st_mtime)
            await deps.gateway.reply(chat_id, f"🏴‍☠️ Трек скачан! Отправляю...", reply_to=message_id)
            await deps.gateway.gateway_bot.send_audio(chat_id, audio=FSInputFile(str(latest_file)), reply_to_message_id=message_id)
            return
            
    # Если spotdl упал (бан от YouTube Music), пробуем сырой yt-dlp поиск
    await deps.gateway.reply(chat_id, f"🏴‍☠️ spotdl заблокирован (ban/403). Пробую сырой поиск yt-dlp...", reply_to=message_id)
    
    cmd = [
        "yt-dlp",
        f"ytsearch1:{song_query}",
        "-x",
        "--audio-format", "mp3",
        "--ffmpeg-location", str(Path.home() / ".spotdl" / "ffmpeg.exe"),
        "--print", "after_move:filepath",
        "-o", str(downloads_dir / "%(title)s.%(ext)s")
    ]
    if proxy_url:
        cmd.extend(["--proxy", proxy_url])
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    stdout_bytes, stderr_bytes = await process.communicate()
    if process.returncode == 0:
        filepath = stdout_bytes.decode(errors="replace").strip().splitlines()[-1]
        if Path(filepath).exists():
            await deps.gateway.reply(chat_id, f"🏴‍☠️ Трек найден через YouTube! Отправляю...", reply_to=message_id)
            await deps.gateway.gateway_bot.send_audio(chat_id, audio=FSInputFile(filepath), reply_to_message_id=message_id)
            return

    await deps.gateway.reply(chat_id, f"🏴‍☠️ Ошибка: все методы скачивания заблокированы по IP.\n```\n{stderr_bytes.decode(errors='replace')[:1000]}\n```", reply_to=message_id)
