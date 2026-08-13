#!/usr/bin/env python
"""Заглушка `claude` CLI — проверка мозга Cortex без реальной подписки.

Включается через переменную окружения PLEXUS_BRAIN_DRIVER=mock_claude
(см. Config.brain_driver в cortex/config.py) — работает независимо от
PLEXUS_FORCE_DRIVER, который управляет только драйвером сотрудников.
"""

from __future__ import annotations

import argparse
import os
import sys


def build_reply(prompt: str) -> str:
    lowered = prompt.lower()

    # Продолжение диалога (результат предыдущего инструмента) — финальный
    # текст без нового действия, иначе агентный цикл никогда не остановится.
    if lowered.lstrip().startswith("результат "):
        return "Готово — вот что получилось по твоему запросу (mock-режим)."

    if "штат" in lowered or "сотрудник" in lowered:
        return 'Сейчас посмотрю.\n\n<action>\n{"tool": "list_staff", "args": {}}\n</action>'
    if "проект" in lowered:
        return 'Секунду.\n\n<action>\n{"tool": "list_projects", "args": {}}\n</action>'
    if "статус" in lowered:
        return '<action>\n{"tool": "get_status", "args": {}}\n</action>'

    return "Понял (mock-режим, реального Claude тут нет). Что дальше?"


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock claude CLI for brain testing")
    parser.add_argument("--prompt-file", dest="prompt_file")
    args, _unknown = parser.parse_known_args()

    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    else:
        prompt = sys.stdin.read()

    sys.stdout.write(build_reply(prompt))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
