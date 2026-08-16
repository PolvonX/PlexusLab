"""Иерархия ошибок Cortex.

Любая ошибка, наследующая CortexError, считается «ожидаемой»: Cortex
превращает её в аккуратный отчёт в чат, а не в трейсбек в логе.
"""

from __future__ import annotations


class CortexError(Exception):
    """Базовая ошибка домена Cortex."""

    #: Человекочитаемый заголовок для отчёта в Telegram.
    title = "Ошибка Cortex"


class ConfigError(CortexError):
    title = "Ошибка конфигурации"


class RegistryError(CortexError):
    title = "Ошибка реестра сотрудников"


class SecurityError(CortexError):
    title = "Отказано в доступе"


class WorkspaceError(CortexError):
    title = "Ошибка рабочей среды"


class ToolError(CortexError):
    title = "Ошибка инструмента"


class AgentRunError(CortexError):
    """Падение или таймаут процесса `agy`."""

    title = "Сабагент не справился"

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr: str = "",
        command: str = "",
        duration: float = 0.0,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.command = command
        self.duration = duration
        #: Секунды до сброса лимита, если stderr опознан как временная
        #: (retryable) ошибка вроде квоты agy — None, если ошибка фатальная
        #: или паттерн не распознан. См. runtime/runner.py::_parse_retry_after.
        self.retry_after = retry_after
