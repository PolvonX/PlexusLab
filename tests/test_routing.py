"""Маршрутизация: кому задача и в каком проекте."""

from __future__ import annotations

import pytest

from cortex.errors import WorkspaceError
from cortex.telegram.routing import MentionRouter

CHAT = -100500


@pytest.fixture()
def router(registry, workspaces, state):
    return MentionRouter(registry, workspaces, state)


@pytest.fixture()
async def hired(registry, frontend):
    await registry.add(frontend)
    return frontend


async def test_mention_by_tag(router, workspaces, hired):
    workspaces.create("sports_api")

    routed = router.route("@Frontend_Dev почини хедер", CHAT)

    assert routed.employee.name == "Frontend_Dev"
    assert routed.instruction == "почини хедер"


async def test_mention_by_bot_username(router, workspaces, hired):
    workspaces.create("sports_api")

    routed = router.route("@frontend_dev_bot глянь стили", CHAT)

    assert routed.employee.name == "Frontend_Dev"


async def test_no_mention_returns_none(router, workspaces, hired):
    workspaces.create("sports_api")

    assert router.route("просто болтаем в чате", CHAT) is None


async def test_unknown_mention_ignored(router, workspaces, hired):
    workspaces.create("sports_api")

    assert router.route("@Somebody_Else сделай что-нибудь", CHAT) is None


async def test_project_tag_wins(router, workspaces, hired, state):
    workspaces.create("sports_api")
    workspaces.create("basehub_web")
    await state.set_active_project(CHAT, "sports_api")

    routed = router.route("@Frontend_Dev #basehub_web правь лендинг", CHAT)

    assert routed.project == "basehub_web"
    assert "#basehub_web" not in routed.instruction


async def test_active_chat_project_used(router, workspaces, hired, state):
    workspaces.create("sports_api")
    workspaces.create("basehub_web")
    await state.set_active_project(CHAT, "sports_api")

    assert router.route("@Frontend_Dev задача", CHAT).project == "sports_api"


async def test_employee_default_project(router, workspaces, registry, hired):
    workspaces.create("sports_api")
    workspaces.create("basehub_web")
    await registry.update("Frontend_Dev", default_project="basehub_web")

    assert router.route("@Frontend_Dev задача", CHAT).project == "basehub_web"


async def test_single_project_is_implicit(router, workspaces, hired):
    workspaces.create("sports_api")

    assert router.route("@Frontend_Dev задача", CHAT).project == "sports_api"


async def test_ambiguous_project_raises(router, workspaces, hired):
    workspaces.create("sports_api")
    workspaces.create("basehub_web")

    with pytest.raises(WorkspaceError, match="Непонятно"):
        router.route("@Frontend_Dev задача", CHAT)


async def test_empty_instruction_gets_fallback(router, workspaces, hired):
    workspaces.create("sports_api")

    routed = router.route("@Frontend_Dev", CHAT)

    assert "доложи" in routed.instruction


async def test_fired_employee_not_routed(router, workspaces, registry, hired):
    workspaces.create("sports_api")
    await registry.fire("Frontend_Dev")

    assert router.route("@Frontend_Dev задача", CHAT) is None
