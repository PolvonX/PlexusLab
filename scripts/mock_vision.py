#!/usr/bin/env python
"""Заглушка vision-driver — проверка transcribe_photo() и всего бота целиком
без реального claude.cmd/подписки.

Включается через PLEXUS_VISION_DRIVER=mock_vision (см. Config.vision_driver
в cortex/config.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock vision CLI for photo-transcription testing")
    parser.add_argument("--prompt-file", dest="prompt_file")
    args, _unknown = parser.parse_known_args()

    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as fh:
            fh.read()  # содержимое (base64-картинка) не нужно заглушке, просто выходим

    result = {"type": "result", "result": "Mock-транскрипция фото (реального claude.cmd тут нет)."}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
