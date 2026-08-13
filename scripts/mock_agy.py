#!/usr/bin/env python
"""Заглушка Google Antigravity CLI для отладки Cortex без установленного agy.

Включается переключателем в config.yaml:

    agent_runner:
      driver: "mock"

Читает промпт, печатает правдоподобный ответ и — если в задаче встречается
ключевое слово — демонстрирует блок <action>, чтобы проверить Tool Use.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def build_reply(prompt: str) -> str:
    agent = os.getenv("PLEXUS_AGENT", "Agent")
    project = os.getenv("PLEXUS_PROJECT", "unknown")

    task = prompt.rsplit("## Задача", 1)[-1].strip()
    lowered = task.lower()

    lines = [
        f"Принял. Смотрю проект {project}.",
        "",
        f"Задача, как я её понял: {task.splitlines()[-1][:160] if task else '(пусто)'}",
    ]

    if any(word in lowered for word in ("файл", "создай", "напиши", "тест", "запусти", "git")):
        lines += [
            "",
            "Начинаю с осмотра рабочей среды.",
            "",
            "<action>",
            '{"tool": "execute_command", "args": {"command": "git status"}}',
            "</action>",
        ]
    elif "профиль" in lowered or "био" in lowered:
        lines += [
            "",
            "<action>",
            '{"tool": "update_telegram_profile", "args": '
            f'{{"description": "Сотрудник Plexus Lab, проект {project}"}}}}',
            "</action>",
        ]
    else:
        lines += ["", "Действий не требуется — отвечаю текстом."]

    lines += ["", f"— {agent} (mock-режим, реального agy тут нет)"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock Antigravity CLI")
    parser.add_argument("--prompt-file", dest="prompt_file")
    parser.add_argument("--cwd", dest="cwd", default=".")
    parser.add_argument("--fail", action="store_true", help="Симулировать падение")
    parser.add_argument("--delay", type=float, default=1.0)
    args, _unknown = parser.parse_known_args()

    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    else:
        prompt = sys.stdin.read()

    time.sleep(max(0.0, args.delay))

    if args.fail:
        print("Traceback (most recent call last):", file=sys.stderr)
        print("  RuntimeError: mock-агент упал по требованию", file=sys.stderr)
        return 1

    sys.stdout.write(build_reply(prompt))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
