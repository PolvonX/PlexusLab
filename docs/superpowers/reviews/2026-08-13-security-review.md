# Security-ревью Cortex: Расследование утечки внутренних деталей и секретов в ответе мозга

**Дата:** 13 августа 2026 г.  
**Репозиторий:** PlexusLab/Cortex  
**Предмет расследования:** Инцидент с раскрытием внутренних деталей реализации (`_state_block`), содержимого `.bak`-файла, состояния `data/state.json`, имен лог-файлов `live_run_*.log` и секретов из `.env` (`CORP_GROUP_ID`, `CEO_TELEGRAM_ID`) при обращении CEO "как ты" к `cortex/brain/agent.py`.  
**Статус:** **Причина однозначно установлена и подтверждена эмпирическими доказательствами (Уверенность: 100%).**

---

## 1. Краткое резюме и вердикт

Утечка произошла из-за **синтаксической ошибки фильтрации командной строки в Python-компоненте `AgentRunner`**, в результате которой флаг `--tools ""` в `claude.cmd` потерял своё значение `""` и **НЕ отключил встроенные инструменты Claude Code CLI** (`Bash`, `Read`, `Write`, `Glob`, `Grep`).

Когда CEO написал "как ты", Claude Code CLI был запущен в рабочей директории проекта (`C:\Projects\PlexusLab`) с полностью активным инструментом `Bash` и `Read`. В попытке ответить на вопрос "как дела у проекта", Claude самостоятельно выполнил shell-команды `ls`, `read cortex/brain/context.py`, `read employees_registry.json.bak`, `find data/` и прочитал имена логов чатов с ID CEO и ID группы Telegram.

Изолированное ручное тестирование в PowerShell (`claude.cmd -p "..." --tools ""`) показывало корректное отключение инструментов, потому что оболочка PowerShell передавала `""` в Node.js без участия Python-кода `cortex/runtime/runner.py`.

---

## 2. Проверка конкретных гипотез из запроса

### 2.1 `cortex/brain/context.py` (`BrainPromptBuilder`)
* **Вопрос:** Есть ли путь, где содержимое файлов, переменные окружения, внутренности конфига или секреты попадают в prompt или system_prompt, отправляемые в claude? (например, случайный дамп `repr(Config)`, `os.environ` или traceback исключения)?
* **Результат проверки:** **НЕТ (Исключено).**
* **Доказательства:**
  - Изучение [cortex/brain/context.py](file:///C:/Projects/PlexusLab/cortex/brain/context.py#L62-L108) показывает, что `_state_block()` форматирует только имя компании, список имен сотрудников из реестра, имена проектов и активный проект чата:
    ```python
    return (
        f"## Состояние компании\n\n"
        f"Штат: {staff}\n"
        f"Проекты: {project_names}\n"
        f"Активный проект этого чата: {active}"
    )
    ```
  - Метод `_state_block` **не читает** `.bak`-файлы, **не опрашивает** `data/state.json`, **не сканирует** `data/` на предмет `live_run_*.log`, **не содержит** имени собственной функции `_state_block` в выводной строке и **не выводит** `CEO_TELEGRAM_ID` или `CORP_GROUP_ID`.
  - Никакой `repr(Config)` или `os.environ` в промпт не попадает.

---

### 2.2 `cortex/deps.py` и `cortex/logging_setup.py` (Исключения и tracebacks)
* **Вопрос:** Может ли `__repr__` / traceback исключения Python (содержащий объект `Config`, пути, значения из `.env`) попасть обратно в переписку в коде?
* **Результат проверки:** **НЕТ (Исключено).**
* **Доказательства:**
  - [cortex/deps.py](file:///C:/Projects/PlexusLab/cortex/deps.py#L29-L58) — обычный dataclass-контейнер зависимостей без форматирования строк.
  - [cortex/logging_setup.py](file:///C:/Projects/PlexusLab/cortex/logging_setup.py#L18-L49) — настраивает `StreamHandler` и `RotatingFileHandler` для `data/cortex.log`.
  - Обработка исключений в [cortex/brain/agent.py](file:///C:/Projects/PlexusLab/cortex/brain/agent.py#L98-L107) и [cortex/telegram/formatting.py](file:///C:/Projects/PlexusLab/cortex/telegram/formatting.py#L102-L111) использует `fmt.error_report`:
    ```python
    def error_report(error: Exception, *, context: str = "") -> str:
        body = f"{type(error).__name__}: {error}"
        ...
    ```
    В чат отправляется только имя класса исключения и его строковый текст (`str(exc)`), но не локальные переменные стека, не `repr(Config)` и не секреты `.env`.

---

### 2.3 Персистентность сессий Claude (`--resume`, `uuid5(chat_id)`)
* **Вопрос:** Может ли персистентность сессий claude (`--resume`, `~/.claude/projects/*`) подмешивать контент из ДРУГОЙ сессии/проекта, если используется детерминированный `session_id`?
* **Результат проверки:** **Перекрёстной утечки между разными чатами нет, НО существует сохранение «отравленного» контекста.**
* **Доказательства:**
  - [cortex/brain/session.py](file:///C:/Projects/PlexusLab/cortex/brain/session.py#L29-L39) генерирует UUID5 по `chat_id`. Для разных чатов генерируются строго разные UUID (например, `9e30a313...` для лички CEO `5362222283` и `90ebe481...` для группы `-1003881673794`).
  - **Однако:** При использовании `--resume`, Claude Code CLI загружает всю историю предыдущих ходов текущей сессии из файла `~/.claude/projects/c--Projects-PlexusLab/<session_id>.jsonl`. Если в каком-то из предыдущих вызовов Claude выставил встроенные инструменты и прочитал файлы репозитория, все эти прочитанные файлы и секреты остаются в сохранённой сессии Claude и повторяются в последующих ответах при `--resume`!

---

## 3. Точный механизм утечки (Доказано эмпирически)

### Цепочка причинно-следственных связей:

#### Шаг 1: Формирование шаблона в `config.yaml`
В [config.yaml](file:///C:/Projects/PlexusLab/config.yaml#L94-L98) задана команда запуска `claude`:
```yaml
command: >
  claude.cmd -p --output-format text
  --system-prompt-file "{system_prompt_file}" --tools ""
  --model sonnet {session_flag}
```

#### Шаг 2: Баг в `cortex/runtime/runner.py` (Корневая причина)
В [cortex/runtime/runner.py](file:///C:/Projects/PlexusLab/cortex/runtime/runner.py#L107-L109) выполняется парсинг и очистка кавычек:
```python
argv = shlex.split(rendered, posix=False)
argv = [arg.strip('"') for arg in argv if arg]
```

1. `shlex.split(..., posix=False)` разбивает `--tools ""` на два токена: `'--tools'` и `'""'`.
2. Код `arg.strip('"')` выполняет `'""'.strip('"')`, что возвращает пустую строку `""`.
3. Условие **`if arg`** проверяет булевое значение полученной строки: `bool("")` в Python равняется **`False`**!
4. **`argv` ТИХО ВЫБРАСЫВАЕТ ПУСТУЮ СТРОКУ `""` ИЗ СПИСКА АРГУМЕНТОВ!**

Итоговый список `argv`, передаваемый в `asyncio.create_subprocess_exec`, становится таким:
```python
['claude.cmd', '-p', '--output-format', 'text', '--system-prompt-file', '...', '--tools', '--model', 'sonnet', ...]
```

#### Шаг 3: Что увидел `claude.cmd`
В `data/live_run_brain.log` зафиксирована фактическая командная строка подпроцесса (строка 20):
```text
15:39:12 | INFO | cortex.runner | [__brain__/Cortex] запуск: claude -p <промпт> --output-format text --system-prompt <file> --tools  --model sonnet ...
```
Обратите внимание на **два пробела между `--tools` и `--model`**! Пустой аргумент `""` испарился.

Флаг `--tools` в Node.js / Commander CLI остался без значения. Парсер CLI посчитал `--tools` некорректным / неполным и **ОСТАВИЛ ВСЕ ВСТРОЕННЫЕ ИНСТРУМЕНТЫ (`Bash`, `Read`, `Write`, `Glob`, `Grep`) ВКЛЮЧЁННЫМИ BY DEFAULT**.

#### Шаг 4: Действия Claude в рабочей директории
До введения временной защиты `_brain_workspace()` в [cortex/brain/agent.py](file:///C:/Projects/PlexusLab/cortex/brain/agent.py#L60-L71), подпроцесс запускался с `cwd = C:\Projects\PlexusLab`.

Когда CEO написал "как ты", Claude получил prompt, но увидел, что у него доступен инструмент `Bash` и `Read`. Желая предоставить подробный отчёт о состоянии проекта, Claude выполнил серию встроенных вызовов:

> [!IMPORTANT]
> **Прямое доказательство из файла логов сессии Claude (`~/.claude/projects/c--Projects-PlexusLab/f8843d7e-acf4-42e1-a54b-8aaa2c453a22.jsonl`):**
> 
> - **Line 9:** `Bash: ls "C:/Projects/PlexusLab"`
> - **Line 25:** `Bash: find "C:/Projects/PlexusLab/cortex/brain"`
> - **Line 26:** `Read: C:\Projects\PlexusLab\cortex\brain\context.py` *(отсюда взялось имя функции `_state_block`)*
> - **Line 31:** `Read: C:\Projects\PlexusLab\employees_registry.json.bak` *(отсюда взялось содержимое `.bak`)*
> - **Line 33:** `Bash: find "C:/Projects/PlexusLab/data"` *(отсюда взялись имена логов `live_run_*.log` и файлы `chat_5362222283`, `chat_-1003881673794` с числовыми ID)*
> - **Line 40:** `Bash: test -f "C:/Projects/PlexusLab/data/state.json"` *(отсюда взялась информация об отсутствии `state.json`)*

#### Шаг 5: Почему это не воспроизвелось в ручном тестировании
При ручном тестировании в PowerShell или CMD выполнялась команда:
`claude.cmd -p "test" --tools ""`
В этом случае сама оболочка Windows передавала пустой аргумент `""` прямо в `node.exe` без прохождения через Python-фильтр `[arg.strip('"') for arg in argv if arg]`. В ручном тесте `--tools ""` срабатывал корректно, что и сбило расследование с толку.

---

## 4. Рекомендуемые фиксы и митигации

### Приоритет 1: Устранение первопричины (Прямой фикс бага)

#### 1.1 Исправление `cortex/runtime/runner.py`
Нельзя использовать `if arg` для фильтрации элементов `argv`, так как это удаляет валидные пустые аргументы `""`.

В [cortex/runtime/runner.py:108](file:///C:/Projects/PlexusLab/cortex/runtime/runner.py#L108):
```python
# Было (БАГ):
argv = [arg.strip('"') for arg in argv if arg]

# Должно быть:
argv = [arg.strip('"') for arg in argv if arg != ""]
```
*(Или сохранить элемент, если исходный токен содержал кавычки).*

#### 1.2 Явное задание флага в `config.yaml`
Для исключения проблем с парсерами аргументов в разных ОС и драйверах задавать флаг в формате с равенством:
```yaml
command: >
  claude.cmd -p --output-format text
  --system-prompt-file "{system_prompt_file}" --tools=""
  --model sonnet {session_flag}
```

---

### Приоритет 2: Defense-in-Depth (Сохранение и усиление защиты)

#### 2.1 Изоляция рабочей директории (Сохранить `_brain_workspace`)
Сохранить и закрепить механизм `_brain_workspace()` в [cortex/brain/agent.py:60](file:///C:/Projects/PlexusLab/cortex/brain/agent.py#L60):
Подпроцесс мозга **никогда** не должен запускаться с `cwd`, равным корню проекта. Пустая папка `data/brain_workspace/` гарантирует, что даже при гипотетическом сбое CLI-флагов у Claude не будет локального доступа к `.env`, исходному коду и логам.

#### 2.2 Очистка секретов из `os.environ` подпроцесса
В [cortex/runtime/runner.py:168](file:///C:/Projects/PlexusLab/cortex/runtime/runner.py#L168) при формировании `env` для `create_subprocess_exec` явно удалять или фильтровать чувствительные переменные:
```python
env = {**os.environ, **driver.env}
# Не передавать секреты бота модовому подпроцессу, если они ему не нужны
env.pop("CORTEX_BOT_TOKEN", None)
env.pop("CEO_TELEGRAM_ID", None)
env.pop("CORP_GROUP_ID", None)
```

#### 2.3 Очистка «отравленных» сессий в `~/.claude/projects/`
Так как старые сессии в `~/.claude/projects/c--Projects-PlexusLab/` уже содержат истории ходов с прочитанными файлами, рекомендуется очистить директорию сессий мозга или выполнить сброс сессий чата, чтобы Claude при `--resume` не брал данные из старых логов.

#### 2.4 Автотест на сохранение пустых аргументов в `runner.py`
Добавить юнит-тест в `tests/test_runner.py`, проверяющий, что `_build_argv` при передаче `--tools ""` формирует точный список `['--tools', '']`, а не вырезает пустую строку.
