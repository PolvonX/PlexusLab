"""Общие фикстуры: собираем Config в обход .env и config.yaml."""

from __future__ import annotations

import pytest

from cortex.brain.choices import PendingChoiceStore
from cortex.brain.plan import PlanStore
from cortex.config import Config, Secrets
from cortex.models import Employee
from cortex.registry import EmployeeRegistry
from cortex.state import ChatState
from cortex.workspace import WorkspaceManager

CEO_ID = 1001
GROUP_ID = -100500
BOT_ID = 777


@pytest.fixture()
def secrets():
    return Secrets(
        cortex_token="1:AAA",
        ceo_id=CEO_ID,
        corp_group_id=GROUP_ID,
        ceo_dm_chat_id=CEO_ID,
        log_level="INFO",
    )


@pytest.fixture()
def config(tmp_path, secrets):
    raw = {
        "company": {"name": "Plexus Lab", "ceo_name": "Abdulloh Abbosov"},
        "paths": {
            "registry": "employees_registry.json",
            "prompts_dir": "prompts",
            "projects_dir": "projects",
            "data_dir": "data",
        },
        "security": {
            "command_blocklist": [r"(?i)\brm\s+-rf\s+/"],
            "allow_bot_senders": True,
        },
        "tools": {
            "default": ["execute_command", "send_file"],
            "per_employee": {"Synapse": ["web_research"]},
        },
    }
    cfg = Config(root=tmp_path, raw=raw, secrets=secrets)
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture()
def registry(config):
    reg = EmployeeRegistry(config.registry_path, config.prompts_dir)
    reg.load()
    return reg


@pytest.fixture()
def workspaces(config):
    return WorkspaceManager(config.projects_dir)


@pytest.fixture()
def state(config):
    return ChatState(config.data_dir)


@pytest.fixture()
def choices(config):
    return PendingChoiceStore(config.data_dir / "pending_choices.json")


@pytest.fixture()
def plan_store(config):
    return PlanStore(config.data_dir / "plans.json")


@pytest.fixture()
def frontend():
    return Employee(
        name="Frontend_Dev",
        role="Senior Frontend Engineer",
        token="222:BBB",
        prompt_path="prompts/frontend_dev.md",
        bot_id=BOT_ID,
        username="frontend_dev_bot",
    )
