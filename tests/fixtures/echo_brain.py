#!/usr/bin/env python
"""Детерминированный «claude» для теста агентного цикла (brain/agent.py).

Первый вызов (--counter-file ещё не существует) — просит список штата.
Второй вызов (после результата list_staff) — финальный текстовый ответ,
без действий, цикл на этом останавливается.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file")
    parser.add_argument("--counter-file", required=True)
    args, _ = parser.parse_known_args()

    counter = Path(args.counter_file)
    if not counter.exists():
        counter.write_text("1", encoding="utf-8")
        sys.stdout.write(
            "Сейчас посмотрю.\n\n<action>\n{\"tool\": \"list_staff\", \"args\": {}}\n</action>\n"
        )
        return 0

    sys.stdout.write("В штате один сотрудник: Frontend_Dev, Senior Frontend Engineer.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
