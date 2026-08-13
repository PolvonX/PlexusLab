# Code Review: Cortex Package (2026-08-13)

Результаты общего ревью кода пакета `cortex/`. Находки отсортированы по уровню серьёзности (самые опасные и критичные — в начале списка).

---

### 1. `cortex/workspace/manager.py:220` — Кроссплатформенный сбой `os.rmdir()` при отключении symlink/junction на Linux/macOS
* **Файл:строка**: [`cortex/workspace/manager.py:220`](file:///C:/Projects/PlexusLab/cortex/workspace/manager.py#L220)
* **Сценарий сбоя**: Вызов инструмента `unlink_project` или команды `/project unlink` в операционных системах семейства POSIX (Linux, macOS). Метод `WorkspaceManager.unlink()` вырывает подключение через `os.rmdir(project.path)`. На Linux/macOS подключённый проект является символической ссылкой (`symlink`), созданной через `link.symlink_to()`. Функция `os.rmdir()` на POSIX отклоняет удаление символической ссылки и выбрасывает `NotADirectoryError: [Errno 20] Not a directory`, ломая отвязку проекта.
* **Предлагаемый фикс**: Проверять тип точки монтирования и на POSIX/symlink вызывать `project.path.unlink()`, а `os.rmdir()` использовать только для Windows Junction:
  ```python
  if project.path.is_symlink():
      project.path.unlink()
  else:
      os.rmdir(project.path)
  ```

---

### 2. `cortex/runtime/runner.py:115-125` — Ложное блокирование задач из-за некорректного подсчёта длины командной строки Windows
* **Файл:строка**: [`cortex/runtime/runner.py:115-125`](file:///C:/Projects/PlexusLab/cortex/runtime/runner.py#L115-L125)
* **Сценарий сбоя**: Запуск драйвера с `prompt_via_stdin: true` (например, `claude`) с длиной промпта более 30 000 символов. Метод `_build_argv()` вычисляет переменную `total`, безусловно прибавляя `len(prompt)` к сумме длин аргументов `argv`, даже когда `{prompt}` отсутствует в строке команды (т.к. передаётся через stdin). Из-за этого `total > _ARGV_LIMIT` вызывает исключение `AgentRunError`, и задача отклоняется, несмотря на то что лимит `CreateProcess` фактически не превышен.
* **Предлагаемый фикс**: Прибавлять `len(prompt)` и `len(system_prompt)` к `total` только в том случае, если соответствующие метки (`_PROMPT_MARK`, `_SYSTEM_PROMPT_MARK`) действительно присутствуют в итоговом массиве `argv`.

---

### 3. `cortex/runtime/runner.py:143-150` — Утечка временных файлов промптов с приватными данными при ошибке формирования `argv`
* **Файл:строка**: [`cortex/runtime/runner.py:143-150`](file:///C:/Projects/PlexusLab/cortex/runtime/runner.py#L143-L150)
* **Сценарий сбоя**: В методе `AgentRunner.run()` файлы `prompt_file` и `system_prompt_file` создаются и записываются на диск ДО входа в блок `try: ... finally:`. Если вызов `_build_argv()` выбрасывает `AgentRunError` (например, при превышении лимита длины) или происходит сбой подстановки параметров, выполнение прерывается до блока `try`, и файлы временных промптов в `data/prompts_tmp/` останутся на диске навсегда.
* **Предлагаемый фикс**: Перенести создание и запись `prompt_file` и `system_prompt_file` внутрь блока `try:`, чтобы очистка в `finally:` гарантированно срабатывала при любых ошибках инициализации.

---

### 4. `cortex/tools/shell.py:87-94` — Утечка дочерних подпроцессов в Windows при таймауте команды
* **Файл:строка**: [`cortex/tools/shell.py:87-94`](file:///C:/Projects/PlexusLab/cortex/tools/shell.py#L87-L94)
* **Сценарий сбоя**: Выполнение команды в Windows через `execute_command`, которая превышает таймаут и запускает дочерние процессы (например, `python`, `node`, `git`, `yt-dlp`). При наступлении `asyncio.TimeoutError` вызывается `process.kill()`. В Windows Python завершает только процесс-оболочку `cmd.exe`, а дочерние процессы остаются висеть в памяти как сироты, нагружая CPU/RAM и блокируя файлы проекта.
* **Предлагаемый фикс**: В ОС Windows завершать дерево процессов через `taskkill /F /T /PID <pid>` или использовать Windows Job Objects с флагом `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.

---

### 5. `cortex/registry.py:67-71, 107-149` — Состояние гонки (Race Condition) при итерации по реестру сотрудников
* **Файл:строка**: [`cortex/registry.py:67-71`](file:///C:/Projects/PlexusLab/cortex/registry.py#L67-L71)
* **Сценарий сбоя**: Параллельное выполнение запроса информации о штате (`registry.all()`) и операции найма/жесткого увольнения (`registry.add()`, `registry.fire(hard=True)`). Метод `all()` обходит `self._employees.values()` напрямую без взятия `self._lock`. В этот момент `add()` или `fire()` модифицируют словарь `self._employees` под локом, что приводит к генерации `RuntimeError: dictionary changed size during iteration` во время итерации.
* **Предлагаемый фикс**: В методе `all()` выполнять итерацию по копии значений словаря или запрашивать `self._lock`:
  ```python
  def all(self, *, include_inactive: bool = False) -> list[Employee]:
      items = list(self._employees.values())
      if not include_inactive:
          items = [e for e in items if e.active]
      return sorted(items, key=lambda e: e.name.lower())
  ```

---

### 6. `cortex/brain/agent.py:272-295` — Выполнение подтверждённого действия вне блокировки чата и отсутствие фидбека при повторном клике
* **Файл:строка**: [`cortex/brain/agent.py:272-295`](file:///C:/Projects/PlexusLab/cortex/brain/agent.py#L272-L295)
* **Сценарий сбоя**: 1) При нажатии CEO кнопки подтверждения в Telegram метод `resolve_pending` вызывает `self.tools.dispatch(action, ctx)` до входа в `async with self._locks[chat_id]`. Это позволяет рискованному инструменту выполняться параллельно с новыми входящими сообщениями для того же чата. 2) При повторном клике или если действие уже удалено из `pending_actions.json`, `pending` возвращает `None` и метод тихо завершает работу (`return`), хотя интерфейс Telegram уже изменился на «✅ Подтверждено».
* **Предлагаемый фикс**: Обернуть выполнение `resolve_pending` в `async with self._locks[pending.chat_id]:`, а в случае `pending is None` отправлять сообщение пользователю, что действие уже обработано или устарело.

---

### 7. `cortex/hr.py:136` — Падение `render_job_description` при наличии фигурных скобок в шаблоне или данных
* **Файл:строка**: [`cortex/hr.py:136`](file:///C:/Projects/PlexusLab/cortex/hr.py#L136)
* **Сценарий сбоя**: Редактирование файла шаблона `_template_employee.md` или передача роли/имени, содержащих фигурные скобки `{}` (например, примеры JSON, блоков кода или регулярных выражений). Вызов `self._template().format(...)` интерпретирует скобки в тексте как подстановку аргументов и падает с `KeyError` или `ValueError: Single '{' encountered in format string`.
* **Предлагаемый фикс**: Заменить `str.format()` на явные точечные подстановки `.replace("{name}", name)...`, аналогично подходу в `cortex/context/builder.py`.

---

### 8. `cortex/telegram/routing.py:91-94` — Порча текста инструкции при совпадении префиксов имён ботов
* **Файл:строка**: [`cortex/telegram/routing.py:91-94`](file:///C:/Projects/PlexusLab/cortex/telegram/routing.py#L91-L94)
* **Сценарий сбоя**: Сообщение вида `@Frontend_Dev_bot обрати внимание на @Frontend_Dev`. Функция `find_employee` находит сотрудника `Frontend_Dev` с упоминанием `mention = "@Frontend_Dev"`. Функция `strip_mention` выполняет `text.replace("@Frontend_Dev", " ", 1)`, из-за чего первое упоминание преобразуется в ` _bot`, а целевое упоминание в конце текста остается невырезанным.
* **Предлагаемый фикс**: Использовать регулярные выражения с границами слов для точного вырезания упоминания:
  ```python
  def strip_mention(self, text: str, mention: str) -> str:
      cleaned = re.sub(rf"(?<![\w@]){re.escape(mention)}\b", " ", text or "", count=1)
      cleaned = _PROJECT_TAG_RE.sub(" ", cleaned)
      return re.sub(r"\s{2,}", " ", cleaned).strip()
  ```

---

### 9. `cortex/telegram/brain_router.py:72-80, 87-97` — «Поглощение» необработанных исключений в фоновых задачах `asyncio.create_task`
* **Файл:строка**: [`cortex/telegram/brain_router.py:78, 97`](file:///C:/Projects/PlexusLab/cortex/telegram/brain_router.py#L78)
* **Сценарий сбоя**: Возникновение неожиданного исключения внутри корутины фоновой задачи (например, если отправка отчёта об ошибке `self.bots.say()` в `orchestrator.dispatch` упадёт по таймауту сети). В `add_done_callback(_BACKGROUND.discard)` вызывается метод `discard`, который не запрашивает `task.exception()`. Исключение тихо исчезает из логов до момента сборки мусора (когда Python выведет `Task exception was never retrieved`).
* **Предлагаемый фикс**: Добавить обёртку для `add_done_callback`, проверяющую результат задачи:
  ```python
  def _on_task_done(task: asyncio.Task) -> None:
      _BACKGROUND.discard(task)
      if not task.cancelled() and task.exception():
          log.error("Фоновая задача %s завершилась с ошибкой", task.get_name(), exc_info=task.exception())
  ```

---

### 10. `cortex/state.py:36` и `cortex/brain/pending.py:76` — Аварийное завершение запуска при некорректном корневом типе JSON
* **Файл:строка**: [`cortex/state.py:36`](file:///C:/Projects/PlexusLab/cortex/state.py#L36), [`cortex/brain/pending.py:76`](file:///C:/Projects/PlexusLab/cortex/brain/pending.py#L76)
* **Сценарий сбоя**: Файл `state.json` или `pending_actions.json` повреждён или вручную отредактирован так, что корневым элементом является не объект `{}`, а массив `[]` или примитив (например, `123` или `null`). `json.loads()` возвращает `list`, после чего `self._data.setdefault("chat_projects", {})` вызывает `AttributeError: 'list' object has no attribute 'setdefault'`, что полностью блокирует старт приложения Plexus Lab.
* **Предлагаемый фикс**: Валидировать тип загруженного значения:
  ```python
  if not isinstance(self._data, dict):
      log.warning("Содержимое %s имеет неверный тип — сбрасываю", self.path)
      self._data = {"chat_projects": {}}
  ```

---

### 11. `cortex/brain/agent.py:57` — Постепенная утечка памяти объектами `asyncio.Lock` по `chat_id`
* **Файл:строка**: [`cortex/brain/agent.py:57`](file:///C:/Projects/PlexusLab/cortex/brain/agent.py#L57)
* **Сценарий сбоя**: Длительная работа бота с обращением пользователей из сотен разных чатов/групп. Словарь `self._locks = defaultdict(asyncio.Lock)` накапливает объекты `asyncio.Lock` для каждого уникального `chat_id` и никогда их не удаляет, вызывая постепенную утечку памяти.
* **Предлагаемый фикс**: Очищать неиспользуемые локи после завершения обработки или использовать LRU/слабые ссылки для хранения блокировок по чатам.
