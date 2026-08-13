"""Telegram-слой Plexus Lab: шлюз, пул ботов, форматирование, найм.

Пакет намеренно ничего не реэкспортирует: `hr.py` использует `bot_pool`,
а `hiring.py` — `hr`, поэтому «удобный» реэкспорт Gateway на уровне пакета
замыкал бы импорты в кольцо. Импортируй подмодули напрямую:

    from cortex.telegram.bot_pool import BotPool
    from cortex.telegram.gateway import Gateway
"""
