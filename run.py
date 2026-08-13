#!/usr/bin/env python
"""Точка входа Plexus Lab.

    python run.py

Перед первым запуском: скопируй .env.example в .env и заполни его.
"""

from __future__ import annotations

import sys

from cortex.app import main

if __name__ == "__main__":
    if sys.platform == "win32":
        import asyncio

        # На Windows aiohttp плохо дружит с ProactorEventLoop при завершении
        # процессов — Selector надёжнее для нашей нагрузки.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    raise SystemExit(main())
