"""Периметр безопасности Cortex.

Правило простое и жёсткое: система выполняет команды ТОЛЬКО от CEO
(Abdulloh Abbosov) и от ботов, которые числятся в реестре. Всё остальное
молча игнорируется — отвечать незнакомцам «доступ запрещён» значит
подтверждать им, что система жива.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import Config
from .errors import SecurityError
from .logging_setup import get_logger
from .registry import EmployeeRegistry

log = get_logger("security")


class Principal(str, Enum):
    CEO = "ceo"
    EMPLOYEE_BOT = "employee_bot"
    STRANGER = "stranger"


@dataclass(slots=True)
class AuthDecision:
    allowed: bool
    principal: Principal
    reason: str = ""

    @property
    def is_ceo(self) -> bool:
        return self.principal is Principal.CEO


class SecurityGuard:
    """Валидация отправителя и чата на каждом входящем апдейте."""

    def __init__(self, config: Config, registry: EmployeeRegistry) -> None:
        self.config = config
        self.registry = registry

    # ------------------------------------------------------------------
    def allowed_chat_ids(self) -> set[int]:
        ids: set[int] = {self.config.secrets.ceo_id, self.config.secrets.ceo_dm_chat_id}
        if self.config.secrets.corp_group_id:
            ids.add(self.config.secrets.corp_group_id)
        ids.update(self.config.extra_allowed_chat_ids)
        return ids

    def check_chat(self, chat_id: int) -> bool:
        """Разрешён ли чат. Если CORP_GROUP_ID не задан — группы не проходят."""
        return chat_id in self.allowed_chat_ids()

    # ------------------------------------------------------------------
    def authorize(
        self,
        *,
        user_id: int | None,
        chat_id: int,
        is_bot: bool,
        username: str | None = None,
    ) -> AuthDecision:
        if user_id is None:
            return AuthDecision(False, Principal.STRANGER, "нет отправителя")

        if not self.check_chat(chat_id):
            return AuthDecision(
                False,
                Principal.STRANGER,
                f"чат {chat_id} не в списке разрешённых",
            )

        if user_id == self.config.secrets.ceo_id:
            return AuthDecision(True, Principal.CEO, "CEO")

        if is_bot and self.config.allow_bot_senders:
            employee = self.registry.by_bot_id(user_id)
            if employee and employee.active:
                return AuthDecision(True, Principal.EMPLOYEE_BOT, f"бот {employee.name}")
            return AuthDecision(
                False,
                Principal.STRANGER,
                f"бот {username or user_id} не числится в реестре",
            )

        return AuthDecision(
            False,
            Principal.STRANGER,
            f"пользователь {username or user_id} — не CEO",
        )

    # ------------------------------------------------------------------
    def require_ceo(self, user_id: int | None) -> None:
        """Для операций уровня CEO: найм, увольнение, создание проектов."""
        if user_id != self.config.secrets.ceo_id:
            raise SecurityError(
                "Эта операция доступна только CEO Plexus Lab."
            )
