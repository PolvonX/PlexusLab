"""Динамический HR: найм сотрудника от токена до рабочего места.

Cortex не создаёт ботов сам — это делает CEO в BotFather. Задача HR:
принять токен, проверить его, сгенерировать должностную инструкцию,
завести сотрудника в реестре и включить его «на горячую».
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import RegistryError
from .logging_setup import get_logger
from .models import Employee
from .registry import EmployeeRegistry
from .telegram.bot_pool import BotPool

log = get_logger("hr")

_TEMPLATE_FILE = "_template_employee.md"

_FALLBACK_TEMPLATE = """\
# {title} — {role}

Ты — **{title}**, {role} в компании {company}.

## Кто вокруг
- CEO: {ceo}. Последнее слово всегда за ним.
- Оркестратор: {orchestrator} — он вызывает тебя, исполняет твои действия
  и относит ответ в корпоративный чат.
- Твой тег в чате: @{name}. Обращаются к тебе — работаешь ты.

## Что от тебя ждут
{role_brief}

## Как ты работаешь
1. Сначала смотришь на текущее состояние проекта, потом действуешь.
2. Делаешь ровно то, о чём попросили. Не расширяешь задачу молча.
3. Не уверен в развилке — спрашиваешь у CEO прямо в чате, коротко.
4. После изменений проверяешь результат: собирается, запускается, работает.

## Как ты говоришь
Ты пишешь в рабочий чат, а не отчёт для аудита. Коротко, по делу, без
канцелярита и без списков на пол-экрана. Сделал — сказал что сделал.
Сломалось — сказал что сломалось и что предлагаешь.

## Границы
- Ты работаешь только в своём проекте. Чужие рабочие среды тебе недоступны.
- Разрушительные операции (снос веток, force-push, удаление данных) — только
  после явного согласия CEO.
"""

#: Подсказки по типовым ролям — делают инструкцию осмысленной с первого дня.
_ROLE_BRIEFS = {
    "frontend": (
        "Ты отвечаешь за клиентскую часть: вёрстка, компоненты, состояние, "
        "доступность и то, чтобы интерфейс не разваливался на узких экранах."
    ),
    "backend": (
        "Ты отвечаешь за серверную часть: API, доменная логика, схема данных, "
        "миграции, устойчивость к нагрузке и внятные коды ошибок."
    ),
    "devops": (
        "Ты отвечаешь за инфраструктуру: сборки, деплой, окружения, логи и "
        "мониторинг. Твой главный критерий — воспроизводимость."
    ),
    "qa": (
        "Ты отвечаешь за качество: пишешь тесты, воспроизводишь баги, ловишь "
        "регрессии и говоришь «нет» релизу, когда это оправдано."
    ),
    "data": (
        "Ты отвечаешь за данные: сбор, чистка, витрины, аналитика. Каждый вывод "
        "подкрепляешь цифрами, а не ощущением."
    ),
    "design": (
        "Ты отвечаешь за то, как продукт выглядит и ощущается: макеты, типографика, "
        "иерархия, единообразие."
    ),
    "mobile": (
        "Ты отвечаешь за мобильные приложения: экраны, навигация, офлайн-режим, "
        "сборки под сторы."
    ),
    "security": (
        "Ты отвечаешь за безопасность: разбор угроз, аудит зависимостей, секреты, "
        "права доступа."
    ),
}

_DEFAULT_BRIEF = (
    "Ты ведёшь свою зону ответственности целиком: разбираешься в задаче, "
    "принимаешь инженерные решения и доводишь их до работающего результата."
)


@dataclass(slots=True)
class HireRequest:
    name: str
    role: str
    token: str
    default_project: str | None = None


class HRService:
    """Найм, генерация должностных инструкций, увольнение."""

    def __init__(
        self,
        config: Config,
        registry: EmployeeRegistry,
        bots: BotPool,
    ) -> None:
        self.config = config
        self.registry = registry
        self.bots = bots

    # ------------------------------------------------------------------
    def _template(self) -> str:
        path = self.config.prompts_dir / _TEMPLATE_FILE
        if path.exists():
            return path.read_text(encoding="utf-8")
        log.warning("Шаблон %s не найден — использую встроенный", path)
        return _FALLBACK_TEMPLATE

    @staticmethod
    def _brief_for(role: str) -> str:
        lowered = role.lower()
        for key, brief in _ROLE_BRIEFS.items():
            if key in lowered:
                return brief
        return _DEFAULT_BRIEF

    def render_job_description(self, *, name: str, role: str) -> str:
        title = name.replace("_", " ")
        return self._template().format(
            name=name,
            title=title,
            role=role,
            role_brief=self._brief_for(role),
            company=self.config.company_name,
            ceo=self.config.ceo_name,
            orchestrator=self.config.orchestrator_name,
        )

    # ------------------------------------------------------------------
    def prompt_path_for(self, name: str) -> Path:
        relative = Path(self.config.prompts_dir.name) / f"{name.lower()}.md"
        return relative

    async def hire(self, request: HireRequest) -> Employee:
        """Полный цикл найма. Кидает CortexError с внятным текстом при отказе."""
        if self.registry.exists(request.name):
            raise RegistryError(f"Сотрудник @{request.name} уже в штате")

        clash = self.registry.token_in_use(request.token)
        if clash:
            raise RegistryError(
                f"Этот токен уже принадлежит {clash.mention}. Создай в BotFather "
                "нового бота — один бот на одного сотрудника."
            )

        employee = Employee(
            name=request.name,
            role=request.role,
            token=request.token,
            prompt_path=str(self.prompt_path_for(request.name)).replace("\\", "/"),
            display_name=request.name.replace("_", " "),
            default_project=request.default_project,
        )

        # Сначала пишем инструкцию: сотрудник без инструкции бесполезен.
        description = self.render_job_description(name=employee.name, role=employee.role)
        self.registry.write_prompt(
            employee,
            description,
            backup_dir=self.config.data_dir / "prompt_backups",
        )

        await self.registry.add(employee)

        # Токен проверяем после записи в реестр: если Telegram его отвергнет,
        # откатываем найм, чтобы в штате не осталось мертвых душ.
        try:
            await self.bots.verify(employee)
        except Exception:
            await self.registry.fire(employee.name, hard=True)
            await self.bots.drop(employee.token)
            raise

        log.info("Найм завершён: %s (%s)", employee.name, employee.role)
        return employee

    # ------------------------------------------------------------------
    async def fire(self, name: str, *, hard: bool = False) -> Employee:
        employee = await self.registry.fire(name, hard=hard)
        await self.bots.drop(employee.token)
        return employee
