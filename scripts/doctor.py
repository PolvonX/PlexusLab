#!/usr/bin/env python
"""Предполётная проверка Plexus Lab.

    python scripts/doctor.py

Ловит то, что иначе проявится молчанием в чате: включённый privacy mode,
бот не в группе, недействительный токен, ненайденный agy. Ничего не
меняет и не отправляет сообщений — только читает.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from cortex.config import Config  # noqa: E402
from cortex.errors import CortexError  # noqa: E402
from cortex.registry import EmployeeRegistry  # noqa: E402
from cortex.workspace import WorkspaceManager  # noqa: E402

OK, WARN, FAIL = "  [OK]  ", "  [!]   ", "  [X]   "

_problems = 0
_warnings = 0


def ok(text: str) -> None:
    print(OK + text)


def warn(text: str) -> None:
    global _warnings
    _warnings += 1
    print(WARN + text)


def fail(text: str) -> None:
    global _problems
    _problems += 1
    print(FAIL + text)


def head(text: str) -> None:
    print(f"\n{text}\n" + "-" * 62)


# ----------------------------------------------------------------------
async def check_gateway(config: Config) -> int | None:
    head("Бот-шлюз Cortex")

    bot = Bot(
        token=config.secrets.cortex_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        me = await bot.get_me()
    except Exception as exc:  # noqa: BLE001
        fail(f"токен не принят Telegram: {exc}")
        await bot.session.close()
        return None

    ok(f"@{me.username} (id={me.id}), имя «{me.full_name}»")

    if me.can_read_all_group_messages:
        ok("privacy mode выключен — видит сообщения в группе")
    else:
        fail(
            "privacy mode ВКЛЮЧЁН — Cortex не увидит сообщения в группе.\n"
            "          @BotFather → /mybots → бот → Bot Settings →\n"
            "          Group Privacy → Turn off, затем переподключи бота к группе"
        )

    if not me.can_join_groups:
        fail("боту запрещено вступать в группы (BotFather → Allow Groups)")

    # --- группа ---
    head("Корпоративная группа")
    group_id = config.secrets.corp_group_id
    if not group_id:
        warn("CORP_GROUP_ID не задан — будет работать только личка CEO")
    else:
        try:
            chat = await bot.get_chat(group_id)
            ok(f"«{chat.title}» ({chat.type}, id={chat.id})")
        except Exception as exc:  # noqa: BLE001
            fail(
                f"группа {group_id} недоступна: {exc}\n"
                "          проверь, что бот добавлен в группу и ID верный "
                "(команда /id в группе)"
            )
        else:
            try:
                member = await bot.get_chat_member(group_id, me.id)
                if member.status in ("member", "administrator", "creator"):
                    ok(f"бот в группе, статус: {member.status}")
                    if member.status == "member" and not me.can_read_all_group_messages:
                        warn(
                            "как обычный участник с privacy mode он глух; "
                            "сделать админом — тоже вариант"
                        )
                else:
                    fail(f"бот не участник группы (статус: {member.status})")
            except Exception as exc:  # noqa: BLE001
                warn(f"не удалось проверить членство: {exc}")

    # --- CEO ---
    head("CEO")
    try:
        ceo = await bot.get_chat(config.secrets.ceo_id)
        ok(f"{ceo.full_name or ceo.title} (id={ceo.id}) — чат найден")
        # getChat проходит и тогда, когда бот писать ещё не вправе:
        # Telegram запрещает боту начинать диалог первым. Проверить это
        # без реальной отправки нельзя, поэтому честно предупреждаем.
        print(
            "          если бот ни разу не получал от тебя сообщений, первая\n"
            "          отправка вернёт «can't initiate conversation». Напиши ему\n"
            "          /start в личку — это же нужно и для /hire."
        )
    except Exception as exc:  # noqa: BLE001
        warn(
            f"чат CEO {config.secrets.ceo_id} недоступен: {exc}\n"
            "          напиши боту /start в личку"
        )

    await bot.session.close()
    return me.id


# ----------------------------------------------------------------------
async def check_employees(config: Config) -> None:
    head("Штат")

    # load() создал бы пустой реестр — доктор ничего не меняет на диске.
    if not config.registry_path.exists():
        warn("реестра ещё нет — он создастся при первом запуске; начни с /hire в личке")
        return

    registry = EmployeeRegistry(config.registry_path, config.prompts_dir)
    try:
        registry.load()
    except CortexError as exc:
        fail(str(exc))
        return

    employees = registry.all(include_inactive=True)
    if not employees:
        warn("штат пуст — начни с /hire в личке боту")
        return

    for employee in employees:
        status = "" if employee.active else " (уволен)"
        bot = Bot(token=employee.token)
        try:
            me = await bot.get_me()
        except Exception as exc:  # noqa: BLE001
            fail(f"{employee.mention}: токен недействителен — {exc}")
            await bot.session.close()
            continue

        ok(f"{employee.mention}{status} → @{me.username} ({employee.role})")

        prompt_file = registry.prompt_file(employee)
        if not prompt_file.exists():
            fail(f"    инструкция отсутствует: {prompt_file}")
        else:
            size = prompt_file.stat().st_size
            if size < 200:
                warn(f"    инструкция подозрительно короткая ({size} Б)")

        if employee.listen and not me.can_read_all_group_messages:
            warn("    listen=true, но privacy mode включён — слушать не сможет")

        await bot.session.close()


# ----------------------------------------------------------------------
def check_runner(config: Config) -> None:
    head("Сабагенты")

    try:
        driver = config.runner_driver
    except CortexError as exc:
        fail(str(exc))
        return

    print(f"          драйвер: {driver.name}")
    print(f"          команда: {driver.command}")

    try:
        rendered = driver.command.format(
            prompt="X",
            prompt_file="X",
            workspace="X",
            root="X",
            agent="X",
            project="X",
            model="",
        )
    except KeyError as exc:
        fail(
            f"в команде драйвера неизвестный плейсхолдер {exc}.\n"
            "          Допустимы: {prompt} {prompt_file} {workspace} {root} "
            "{agent} {project} {model}"
        )
        return

    argv = shlex.split(rendered, posix=False)
    binary = argv[0].strip('"') if argv else ""
    resolved = shutil.which(binary)

    if resolved:
        ok(f"исполняемый файл найден: {resolved}")
    else:
        fail(
            f"'{binary}' не найден в PATH.\n"
            "          Либо установи Google Antigravity CLI, либо поправь\n"
            f"          agent_runner.drivers.{driver.name}.command в config.yaml,\n"
            "          либо запусти в отладке: .\\deploy.ps1 -Mock"
        )

    has_prompt = "{prompt}" in driver.command or "{prompt_file}" in driver.command
    if not has_prompt and not driver.prompt_via_stdin:
        fail(
            "в команде нет ни {prompt}, ни {prompt_file}, и prompt_via_stdin=false "
            "— агент не получит задачу"
        )

    if driver.name == "agy" and resolved:
        print("          agy требует однократного входа в Google:")
        print("          agy -p \"ok\"  → пройти по ссылке OAuth")
        print("          проверить это автоматически нельзя — вход интерактивный")


def check_projects(config: Config) -> None:
    head("Рабочие среды")

    workspaces = WorkspaceManager(config.projects_dir)
    projects = workspaces.list()
    if not projects:
        warn(
            "ни одного проекта — агентам негде работать.\n"
            "          создай: /project new sports_api Описание"
        )
        return
    for project in projects:
        # is_symlink() для Windows-junction возвращает False — сверяем
        # через resolve(), как это делает сам WorkspaceManager.
        if project.linked:
            ok(f"{project.name} 🔗→ {project.real_path}")
            if not project.real_path.is_dir():
                fail("    целевая папка исчезла — junction висит в пустоту")
        else:
            ok(f"{project.name} → {project.path}")


# ----------------------------------------------------------------------
async def main() -> int:
    try:  # логи Cortex здесь только мешают — доктор печатает сам
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    print("\nPlexus Lab :: предполётная проверка")
    print("=" * 62)

    try:
        config = Config.load()
    except CortexError as exc:
        print(f"\n{FAIL}{exc}\n")
        return 2

    print(f"          компания: {config.company_name}")
    print(f"          CEO: {config.ceo_name} (id={config.secrets.ceo_id})")

    await check_gateway(config)
    await check_employees(config)
    check_runner(config)
    check_projects(config)

    print("\n" + "=" * 62)
    if _problems:
        print(f"Проблем: {_problems}, предупреждений: {_warnings}. "
              "Стартовать рано — сначала почини отмеченное [X].")
        return 1
    if _warnings:
        print(f"Предупреждений: {_warnings}. Запускаться можно.")
        return 0
    print("Всё чисто. Можно запускать: .\\deploy.ps1")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
