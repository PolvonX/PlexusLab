#!/usr/bin/env python
"""Детерминированный «сабагент» для сквозного теста оркестратора.

Печатает фиксированную реплику с блоком <action>, чтобы проверить всю
цепочку: промпт → процесс → парсер → инструмент → ответ в чат.
"""

from __future__ import annotations

import argparse
import sys

REPLY = """Осмотрелся в проекте, создаю файл.

<action>
{"tool": "execute_command", "args": {"command": "echo plexus > created.txt"}}
</action>

Готово, файл на месте."""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file")
    args, _ = parser.parse_known_args()

    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            fh.read()

    sys.stdout.write(REPLY + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
