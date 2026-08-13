"""Единая настройка логирования.

Пишем и в консоль, и в data/cortex.log — чтобы после падения ночью
можно было понять, что произошло.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-24s | %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    if root.handlers:  # повторный вызов — не плодим хендлеры
        return

    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # Логи на русском, а консоль Windows по умолчанию в cp866 —
    # без этого часть сообщений уходит в кракозябры или падает.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "cortex.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # aiohttp/aiogram болтливы на DEBUG — приглушаем.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"cortex.{name}")
