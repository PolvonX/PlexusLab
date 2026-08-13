# tests/test_brain_router.py
"""Роутинг верхнего уровня: @Tag идёт в agy напрямую, всё остальное — в
мозг. Тест дёргает построенный Router через aiogram's feed_update-подобный
путь было бы тяжеловесно; вместо этого проверяем маршрутизацию через
прямой вызов обработчика, извлечённого из router.message.handlers —
тот же приём, которым в проекте пока не пользовались, поэтому здесь он
локальный для этого файла."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from cortex.telegram.brain_router import build_brain_router

CEO_ID = 1001
CHAT = -100500


class _FakeBrain:
    def __init__(self) -> None:
        self.handled: list[tuple] = []
        self.resolved: list[tuple] = []

    async def handle_message(self, *, chat_id, message_id, text, requester_id):
        self.handled.append((chat_id, message_id, text, requester_id))

    async def resolve_pending(self, action_id, *, approved):
        self.resolved.append((action_id, approved))


@dataclass
class _FakeTask:
    task_id: str = "t1"


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.dispatched = []

    def new_task(self, **kwargs):
        return _FakeTask()

    async def dispatch(self, task, *, requester_id):
        self.dispatched.append(task)


@dataclass
class _FakeConfigSecrets:
    ceo_id: int = CEO_ID


@dataclass
class _FakeConfig:
    secrets: object = field(default_factory=_FakeConfigSecrets)
    ack_task_start: bool = False


@dataclass
class _FakeDeps:
    brain: object
    mentions: object
    orchestrator: object
    config: object = field(default_factory=_FakeConfig)
    scheduler: object = None


def _message(text: str, user_id: int = CEO_ID):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=CHAT),
        message_id=7,
        from_user=SimpleNamespace(id=user_id, full_name="Someone", is_bot=False),
        reply=_noop_reply,
        answer=_noop_reply,
    )


async def _noop_reply(*args, **kwargs):
    return None


def _get_handler(router, message_or_callback_type: str):
    """Достаём единственный обработчик нужного observer'а из router."""
    observer = getattr(router, message_or_callback_type)
    return observer.handlers[0].callback


async def test_mention_bypasses_brain(config, registry, workspaces, frontend):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    await registry.add(frontend)
    workspaces.create("sports_api")
    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)

    brain = _FakeBrain()
    orchestrator = _FakeOrchestrator()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=orchestrator)

    router = build_brain_router(deps)
    handler = _get_handler(router, "message")

    await handler(_message("@Frontend_Dev почини хедер #sports_api"))
    await asyncio.sleep(0)  # дать шанс фоновой asyncio.create_task(...) выполниться

    assert len(orchestrator.dispatched) == 1
    assert brain.handled == []


async def test_ceo_free_text_goes_to_brain(config, registry, workspaces, frontend):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "message")

    await handler(_message("кто у нас в штате?"))
    await asyncio.sleep(0)

    assert brain.handled == [(CHAT, 7, "кто у нас в штате?", CEO_ID)]


async def test_non_ceo_free_text_is_ignored(config, registry, workspaces):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "message")

    await handler(_message("привет всем", user_id=999999))

    assert brain.handled == []


async def test_confirm_callback_resolves_pending(config, registry, workspaces):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "callback_query")

    callback = SimpleNamespace(
        data="brain:confirm:abc123",
        from_user=SimpleNamespace(id=CEO_ID),
        message=SimpleNamespace(edit_text=_noop_reply),
        answer=_noop_reply,
    )
    await handler(callback)
    await asyncio.sleep(0)

    assert brain.resolved == [("abc123", True)]


async def test_cancel_callback_resolves_pending_as_declined(config, registry, workspaces):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "callback_query")

    callback = SimpleNamespace(
        data="brain:cancel:abc123",
        from_user=SimpleNamespace(id=CEO_ID),
        message=SimpleNamespace(edit_text=_noop_reply),
        answer=_noop_reply,
    )
    await handler(callback)
    await asyncio.sleep(0)

    assert brain.resolved == [("abc123", False)]
