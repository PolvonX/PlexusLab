# Brain photo vision — design

**Дата:** 2026-08-17
**Статус:** approved

## Проблема

`cortex/telegram/brain_router.py:92-100` (`on_text`) забирает у сообщения только
`message.text or message.caption` — само фото никогда не разбирается (комментарий в
коде прямо это фиксирует: «Само фото не разбираем, только подпись»). Живой инцидент:
CEO прислал фото таблицы с подписью «создай такой excel файл и отправь», мозг получил
только текст подписи без вложения и честно ответил, что не видит примера. Голое фото
без подписи вообще не доходит до мозга — `F.text | F.caption` его не ловит.

Мозг (`claude` driver, `config.yaml:81-107`) работает с `--tools ""` — без единого
инструмента, включая `Read`. Это осознанная граница безопасности: мозг крутит
многочасовую `--resume`-сессию и получает в контекст пересланные сообщения, результаты
`web_research` и прочий текст, которому нельзя доверять. Любое решение должно эту
границу сохранить.

### Почему не `--tools "Read"`

Проверено вживую (см. транскрипт исследования в диалоге): `claude.cmd --tools "Read"`
без `--dangerously-skip-permissions` и без `--add-dir` читает **любой файл на диске
без запроса разрешения** — подтверждено на `PlexusLab\.env` (секреты) и
`C:\Windows\System32\drivers\etc\hosts`. Никакого встроенного скоупинга по рабочей
директории нет. Дать мозгу `Read` ради одной картинки — значит открыть путь к чтению
произвольного файла на сервере при удачной текстовой инъекции. Отклонено.

### Проверенная безопасная альтернатива

`claude.cmd -p --input-format stream-json --output-format stream-json --tools ""`
принимает inline base64-картинку как content-блок (тот же формат, что Messages API:
`{"type":"image","source":{"type":"base64","media_type":...,"data":...}}`) и корректно
её распознаёт — **без единого инструмента**. Проверено вживую: модель верно назвала
цвет тестового изображения при `--tools ""`. Это тот же уровень изоляции, что у мозга
сегодня.

## Требования

- CEO присылает фото (с подписью или без) — мозг получает по нему осмысленный текст,
  без ручных действий CEO.
- Основная резюмируемая сессия мозга (`BrainSession`, `--resume`) остаётся
  полностью toolless — `--tools ""` без исключений. Никакой новый код не даёт мозгу
  прямой доступ к файлам.
- Приоритет — полнота переноса данных в текст (таблица → все ячейки, скриншот → весь
  видимый текст), а не общее «описание» картинки: живой кейс требовал точных данных
  для Excel-файла, не пересказа.
- Сбой распознавания (таймаут, ошибка `claude.cmd`, неподдерживаемый формат) не должен
  ронять обработку сообщения — мозг получает честную пометку «фото приложено,
  распознать не удалось» вместо тишины или выдумки.
- Публичный API новой части — одна функция, без побочных состояний на диске
  (в отличие от `BrainSession` транскрибация не сохраняет и не резюмирует сессии).

## Решение

### Новый одноразовый vision-driver — `config.yaml`

По образцу существующих `claude`/`claude_haiku` (`config.yaml:81-122`), в
`agent_runner.drivers`:

```yaml
claude_vision:
  # Одноразовая транскрипция фото в текст — НЕ сессия мозга, НЕ --resume.
  # --tools "" — тот же контур безопасности, что у мозга. --no-session-persistence —
  # транскрипция не оставляет сессионных файлов на диске.
  command: >
    claude.cmd -p --input-format stream-json --output-format stream-json
    --system-prompt-file "{system_prompt_file}" --tools ""
    --model sonnet --no-session-persistence
  prompt_via_stdin: true
  env: {}

mock_vision:
  command: 'python "{root}/scripts/mock_vision.py" --prompt-file "{prompt_file}"'
  prompt_via_stdin: false
  env: {}
```

Модель — **sonnet, не haiku**: неверная транскрипция таблицы выглядит как
правдоподобные, но неверные данные — это дороже ошибки, чем стоимость лишнего
вызова sonnet на редких фото.

`Config` (`cortex/config.py:174-179`, по образцу `brain_driver`) получает:

```python
@property
def vision_driver(self) -> RunnerDriver:
    """Драйвер одноразовой транскрипции фото. PLEXUS_VISION_DRIVER=mock_vision —
    для отладки без живого claude."""
    name = os.getenv("PLEXUS_VISION_DRIVER") or "claude_vision"
    return self._load_driver(name)
```

### `cortex/vision/describe.py` — новый модуль

Одна публичная async-функция:

```python
async def transcribe_photo(
    *, image_bytes: bytes, deps: "Deps",
) -> str | None:
    """Переносит содержимое фото в текст через одноразовый vision-driver.
    Возвращает None при любой ошибке — вызывающий код обязан справиться
    с этим сам (см. VISION_FAILURE_NOTE в brain_router.py)."""
```

Логика:
1. Base64-кодирует `image_bytes` (Telegram-фото всегда JPEG → `media_type` фиксирован
   как `"image/jpeg"`, определять по содержимому не нужно).
2. Собирает JSONL-строку по формату Claude Code stream-json input:
   `{"type":"user","message":{"role":"user","content":[{"type":"image","source":
   {"type":"base64","media_type":"image/jpeg","data":"<b64>"}},{"type":"text",
   "text":"<инструкция транскрипции>"}]}}` — это и есть `prompt`, который
   `runner.run()` (`cortex/runtime/runner.py:223`) отправит в stdin как есть.
3. Зовёт `deps.runner.run(prompt=..., workspace=<см. ниже>, agent="Vision",
   project="__vision__", system_prompt=<см. ниже>, driver=deps.config.vision_driver)`.
   `workspace` — `deps.config.data_dir / "vision_workspace"` (создать `mkdir(parents=True,
   exist_ok=True)` при первом обращении), тем же приёмом defense-in-depth, что
   `BrainAgent._brain_workspace()` (`cortex/brain/agent.py:89-100`): `--tools ""` и без
   того не даёт `claude.cmd` трогать файлы, но cwd процесса всё равно не должен
   указывать на дерево с `.env`. Отдельная папка, не общая с `__brain__` — транскрипция
   не относится к сессии мозга ни логически, ни по изоляции workspace.
4. Разбирает `result.stdout` построчно как JSONL (`--output-format stream-json` пишет
   по объекту в строку — system-события, ассистентские сообщения, финальный
   `{"type":"result","result":"<текст>",...}`), находит последнюю строку с
   `"type": "result"` и возвращает поле `result`. Не-JSON строки и строки без
   ожидаемых полей пропускает молча (защита от логов/варнингов в stdout).
5. Любое исключение (`AgentRunError`, `json.JSONDecodeError`, отсутствие `result`-строки)
   ловится внутри функции, логируется через `get_logger("vision")` и возвращает `None`
   — не пробрасывается наружу.

Системный промпт транскрипции (константа в `describe.py`, не файл в `prompts/` — она
не описывает персону, а инструктирует разовую утилитарную задачу):

```
Перед тобой изображение. Перенеси его содержимое в текст максимально полно и
дословно: таблицы — markdown-таблицей со всеми ячейками, весь видимый текст —
как есть, с сохранением структуры (заголовки, списки, подписи). Не интерпретируй,
не сокращай, не добавляй ничего от себя. Если на фото нет текста/таблицы — опиши
одним предложением, что на нём изображено.
```

### `cortex/telegram/brain_router.py::on_text` — интеграция

Изменения в существующем хендлере (`brain_router.py:92-100`):

1. Фильтр хендлера: `F.text | F.caption` → `F.text | F.caption | F.photo` (голое
   фото без подписи начинает доходить).
2. Сразу после `text = message.text or message.caption or ""` (`brain_router.py:98`),
   но ДО проверки `if not text or text.startswith("/"): return` (`brain_router.py:99`)
   — если `message.photo` не пусто:
   ```python
   if message.photo:
       largest = max(
           (p for p in message.photo if not p.file_size or p.file_size <= 3_500_000),
           default=message.photo[-1],
           key=lambda p: p.file_size or 0,
       )
       buf = await deps.gateway.gateway_bot.download(largest.file_id)
       image_bytes = buf.getvalue() if buf else b""
       transcript = await transcribe_photo(image_bytes=image_bytes, deps=deps) if image_bytes else None
       photo_block = (
           f"[Фото распознано]:\n{transcript}" if transcript
           else "[Фото приложено, распознать не удалось]"
       )
       text = f"{text}\n\n{photo_block}" if text else photo_block
   ```
   Лимит 3.5 МБ — Anthropic принимает изображения до ~5 МБ, base64 раздувает объём на
   треть; берём самый крупный вариант, укладывающийся в лимит после кодирования,
   иначе (все варианты крупнее) — largest доступный, transcribe_photo сам вернёт
   `None` при отказе API, деградация та же, что и любая другая ошибка.
3. `text.startswith("/")` проверка ниже (`brain_router.py:99`) не трогается — фото не
   бывает слэш-командой, но `text` на этой строке уже может быть непустым из-за
   фото-блока, даже если `message.text` было пустым (голое фото) — соответствует
   требованию «голое фото доходит».

Транскрипция выполняется синхронно внутри `on_text`, ДО передачи в `debouncer.add()`.
Причины: (а) фото — редкое событие, задержка в пару секунд перед началом debounce-окна
не меняет ощущаемую отзывчивость на фоне уже идущего «печатает…»; (b) debouncer
(`cortex/telegram/debounce.py`) работает только с готовыми строками текста, менять его
интерфейс ради редкого кейса — лишняя связанность на пустом месте.

## Обработка ошибок

Единственная точка отказа снаружи `transcribe_photo` — она уже не бросает исключений.
`on_text` не оборачивает вызов в `try` — как и остальной код хендлера, полагается на
`_on_background_done` (`brain_router.py:34-46`) как последний рубеж для того, что
реально может неожиданно упасть (например, `gateway_bot.download` при отозванном
Telegram-токене файла).

## Тестирование

- `tests/test_vision.py` (новый) — модульные тесты `transcribe_photo` с
  `FakeRunner`-двойником `deps.runner` (тот же паттерн, что `tests/test_brain_agent.py`
  использует для quota/fallback-тестов): валидный `stream-json` stdout → текст;
  stdout без `result`-строки → `None`; `AgentRunError` от раннера → `None`, без
  исключения наружу.
- `tests/test_brain_router.py` — новые кейсы для `on_text`: фото с подписью → в
  `deps.brain.handle_message` уходит текст, включающий и подпись, и транскрипт; голое
  фото (без caption) → хендлер больше не выходит по `if not text: return`, транскрипт
  доходит один; сбой `transcribe_photo` (замокан на `None`) → доходит текст с пометкой
  «распознать не удалось», сообщение не теряется молча.
- `scripts/mock_vision.py` (новый, по образцу `scripts/mock_claude.py`) — читает
  `--prompt-file`, отдаёт на stdout валидную `stream-json` строку
  `{"type":"result","result":"<канонический mock-текст>"}\n`, чтобы вручную гонять
  бота целиком (`PLEXUS_VISION_DRIVER=mock_vision`) без реального `claude.cmd`.

## Вне рамок

- Fallback на `claude_haiku`-класс модель для `claude_vision` при исчерпанной квоте —
  сейчас честная деградация («распознать не удалось») и так есть; добавить fallback
  можно тремя строками по готовому образцу `brain_fallback_drivers`
  (`cortex/config.py:189-193`), но это отдельное расширение, не часть этой фичи.
- Сохранение фото на диск для повторных обращений мозга к тому же изображению (мозг
  видит только готовый текст один раз, в момент присылки).
- Медиа-группы (альбомы из нескольких фото одним сообщением) — обрабатывается только
  одно фото на сообщение, как и раньше для подписи.
- Передача картинки напрямую в `--resume`-сессию мозга (альтернативный вариант B,
  рассмотренный и отклонённый: требует второго парсера вывода в `_run_loop`, самой
  чувствительной части проекта, ради непроверенного допущения о совместимости
  text- и stream-json-ходов в одной сессии).
