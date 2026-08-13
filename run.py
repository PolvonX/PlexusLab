#!/usr/bin/env python
"""Точка входа Plexus Lab.

    python run.py

Перед первым запуском: скопируй .env.example в .env и заполни его.
"""

from __future__ import annotations

from cortex.app import main

if __name__ == "__main__":
    # ВАЖНО: не переключать event loop на WindowsSelectorEventLoopPolicy.
    # SelectorEventLoop на Windows не умеет запускать subprocess вообще —
    # asyncio.create_subprocess_exec() падает с NotImplementedError, а это
    # ровно то, чем runtime/runner.py вызывает и agy, и claude. Дефолтный
    # ProactorEventLoop иногда шумит предупреждением от aiohttp при закрытии
    # сессии на остановке сервера — это косметика, не сравнить с полностью
    # неработающими сабагентами.
    raise SystemExit(main())
