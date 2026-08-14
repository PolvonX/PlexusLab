#!/usr/bin/env python
"""Тестовая заглушка «claude»: всегда предлагает вызвать один и тот же
(заведомо падающий) кастомный инструмент, независимо от того, что пришло
в промпте. Считает собственные вызовы в counter-file, чтобы тест мог
проверить, что self-healing loop остановился ровно на лимите попыток, а
не продолжает дёргать модель бесконечно."""

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
    count = int(counter.read_text(encoding="utf-8")) + 1 if counter.exists() else 1
    counter.write_text(str(count), encoding="utf-8")

    sys.stdout.write(
        f"Пробую снова (вызов {count}).\n\n"
        '<action>\n{"tool": "broken_tool", "args": {}}\n</action>\n'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
