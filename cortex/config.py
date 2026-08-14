"""Загрузка конфигурации: config.yaml (поведение) + .env (секреты).

Секреты никогда не попадают в config.yaml, а поведение — в .env.
Разделение намеренное: config.yaml коммитится, .env — нет.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .errors import ConfigError


def _as_int(value: Any, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} должен быть целым числом, получено: {value!r}") from exc


@dataclass(slots=True)
class Secrets:
    cortex_token: str
    ceo_id: int
    corp_group_id: int | None
    ceo_dm_chat_id: int
    log_level: str

    @classmethod
    def load(cls, root: Path) -> "Secrets":
        load_dotenv(root / ".env")

        token = (os.getenv("CORTEX_BOT_TOKEN") or "").strip()
        if not token:
            raise ConfigError(
                "CORTEX_BOT_TOKEN не задан. Скопируй .env.example в .env и вставь "
                "токен бота-шлюза от @BotFather."
            )
        if ":" not in token:
            raise ConfigError("CORTEX_BOT_TOKEN не похож на токен Telegram (нет ':').")

        ceo_id = _as_int(os.getenv("CEO_TELEGRAM_ID"), "CEO_TELEGRAM_ID")
        if not ceo_id:
            raise ConfigError(
                "CEO_TELEGRAM_ID не задан. Без него Cortex не сможет отличить CEO "
                "от постороннего и откажется работать."
            )

        group_id = _as_int(os.getenv("CORP_GROUP_ID"), "CORP_GROUP_ID")
        dm_id = _as_int(os.getenv("CEO_DM_CHAT_ID"), "CEO_DM_CHAT_ID") or ceo_id

        return cls(
            cortex_token=token,
            ceo_id=ceo_id,
            corp_group_id=group_id,
            ceo_dm_chat_id=dm_id,
            log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        )


@dataclass(slots=True)
class RunnerDriver:
    name: str
    command: str
    prompt_via_stdin: bool = False
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Config:
    """Разобранный config.yaml + вычисленные абсолютные пути."""

    root: Path
    raw: dict[str, Any]
    secrets: Secrets

    # --- пути ---
    registry_path: Path = field(init=False)
    prompts_dir: Path = field(init=False)
    projects_dir: Path = field(init=False)
    data_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        paths = self.raw.get("paths", {}) or {}
        self.registry_path = self._abs(paths.get("registry", "employees_registry.json"))
        self.prompts_dir = self._abs(paths.get("prompts_dir", "prompts"))
        self.projects_dir = self._abs(paths.get("projects_dir", "projects"))
        self.data_dir = self._abs(paths.get("data_dir", "data"))

    def _abs(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (self.root / p).resolve()

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, root: Path | str | None = None) -> "Config":
        root_path = Path(root) if root else Path(__file__).resolve().parent.parent
        root_path = root_path.resolve()

        cfg_file = root_path / "config.yaml"
        if not cfg_file.exists():
            raise ConfigError(f"Не найден config.yaml по пути {cfg_file}")

        try:
            raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"config.yaml не парсится: {exc}") from exc

        return cls(root=root_path, raw=raw, secrets=Secrets.load(root_path))

    # --- типизированные срезы конфига ---------------------------------
    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {}) or {}

    @property
    def company_name(self) -> str:
        return self.section("company").get("name", "Plexus Lab")

    @property
    def ceo_name(self) -> str:
        return self.section("company").get("ceo_name", "CEO")

    @property
    def orchestrator_name(self) -> str:
        return self.section("company").get("orchestrator_name", "Cortex")

    # --- telegram ---
    @property
    def max_message_length(self) -> int:
        return int(self.section("telegram").get("max_message_length", 3800))

    @property
    def polling_timeout(self) -> int:
        return int(self.section("telegram").get("polling_timeout", 30))

    @property
    def typing_interval(self) -> float:
        return float(self.section("telegram").get("typing_interval", 4.0))

    @property
    def ack_task_start(self) -> bool:
        return bool(self.section("telegram").get("ack_task_start", True))

    # --- runner: делится по назначению, не по одному глобальному переключателю ---
    def _load_driver(self, name: str) -> RunnerDriver:
        drivers = self.section("agent_runner").get("drivers", {}) or {}
        spec = drivers.get(name)
        if not spec:
            raise ConfigError(f"Драйвер '{name}' не описан в agent_runner.drivers")
        if not spec.get("command"):
            raise ConfigError(f"У драйвера '{name}' не задан command")
        return RunnerDriver(
            name=name,
            command=spec["command"],
            prompt_via_stdin=bool(spec.get("prompt_via_stdin", False)),
            env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
        )

    @property
    def runner_driver(self) -> RunnerDriver:
        """Драйвер сотрудников (agy). PLEXUS_FORCE_DRIVER=mock — для отладки."""
        name = os.getenv("PLEXUS_FORCE_DRIVER") or self.section("agent_runner").get("driver", "agy")
        return self._load_driver(name)

    @property
    def brain_driver(self) -> RunnerDriver:
        """Драйвер мозга (claude), независим от драйвера сотрудников.
        PLEXUS_BRAIN_DRIVER=mock_claude — для отладки без живого claude."""
        name = os.getenv("PLEXUS_BRAIN_DRIVER") or "claude"
        return self._load_driver(name)

    @property
    def runner_timeout(self) -> int:
        return int(self.section("agent_runner").get("timeout_seconds", 900))

    @property
    def runner_max_output(self) -> int:
        return int(self.section("agent_runner").get("max_output_chars", 400000))

    @property
    def stderr_report_chars(self) -> int:
        return int(self.section("agent_runner").get("stderr_report_chars", 1500))

    # --- context ---
    @property
    def history_messages(self) -> int:
        return int(self.section("context").get("history_messages", 30))

    @property
    def history_chars_budget(self) -> int:
        return int(self.section("context").get("history_chars_budget", 8000))

    @property
    def include_workspace_tree(self) -> bool:
        return bool(self.section("context").get("include_workspace_tree", True))

    @property
    def workspace_tree_max_entries(self) -> int:
        return int(self.section("context").get("workspace_tree_max_entries", 200))

    # --- concurrency ---
    @property
    def max_parallel_tasks(self) -> int:
        return max(1, int(self.section("concurrency").get("max_parallel_tasks", 3)))

    @property
    def serialize_per_project(self) -> bool:
        return bool(self.section("concurrency").get("serialize_per_project", True))

    # --- security ---
    @property
    def extra_allowed_chat_ids(self) -> list[int]:
        values = self.section("security").get("extra_allowed_chat_ids") or []
        return [int(v) for v in values]

    @property
    def allow_bot_senders(self) -> bool:
        return bool(self.section("security").get("allow_bot_senders", True))

    @property
    def command_blocklist(self) -> list[re.Pattern[str]]:
        patterns = self.section("security").get("command_blocklist") or []
        compiled: list[re.Pattern[str]] = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                raise ConfigError(
                    f"Некорректный регекс в security.command_blocklist: {pattern!r} ({exc})"
                ) from exc
        return compiled

    @property
    def max_command_timeout(self) -> int:
        return int(self.section("security").get("max_command_timeout", 600))

    @property
    def allow_escape_workspace(self) -> bool:
        return bool(self.section("security").get("allow_escape_workspace", False))

    # --- tools ---
    def tools_for(self, employee_name: str) -> list[str]:
        tools = self.section("tools")
        per_employee = tools.get("per_employee", {}) or {}
        if employee_name in per_employee:
            return list(per_employee[employee_name])
        return list(tools.get("default") or [])

    # --- synapse ---
    @property
    def synapse(self) -> dict[str, Any]:
        return self.section("synapse")

    @property
    def synapse_name(self) -> str:
        return self.synapse.get("employee_name", "Synapse")

    # --- brain (Cortex-на-Claude) ---
    @property
    def brain(self) -> dict[str, Any]:
        return self.section("brain")

    @property
    def brain_autonomy(self) -> str:
        return str(self.brain.get("autonomy", "balanced"))

    @property
    def brain_max_iterations(self) -> int:
        return int(self.brain.get("max_iterations", 5))

    @property
    def brain_model(self) -> str:
        return str(self.brain.get("model", "sonnet"))

    @property
    def brain_risk_overrides(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.brain.get("risk_overrides") or {}).items()}

    @property
    def brain_debounce_seconds(self) -> float:
        """Окно тишины перед тем, как отдать накопленные сообщения мозгу
        одним ходом — иначе десяток пересланных подряд сообщений превращается
        в десяток независимых ответов Cortex вместо одного связного."""
        return float(self.brain.get("debounce_seconds", 2.5))
