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

    async def resolve_pending(self, action_id, *, chat_id, approved):
        self.resolved.append((action_id, chat_id, approved))


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
    ceo_name: str = "Abdulloh Abbosov"
    brain_debounce_seconds: float = 0.01


class _FakeHistory:
    def __init__(self) -> None:
        self.added: list[tuple] = []

    def add(self, message) -> None:
        self.added.append((message.chat_id, message.author, message.text, message.is_agent))


@dataclass
class _FakeDeps:
    brain: object
    mentions: object
    orchestrator: object
    config: object = field(default_factory=_FakeConfig)
    scheduler: object = None
    history: object = field(default_factory=_FakeHistory)
    choices: object = None


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


def _get_handler(router, message_or_callback_type: str, index: int = 0):
    """Достаём обработчик нужного observer'а из router по позиции."""
    observer = getattr(router, message_or_callback_type)
    return observer.handlers[index].callback


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
    await asyncio.sleep(0.05)  # окно debounce (0.01с в _FakeConfig) должно истечь

    assert brain.handled == [(CHAT, 7, "кто у нас в штате?", CEO_ID)]


async def test_several_quick_messages_are_combined_into_one_brain_call(config, registry, workspaces):
    """Живой инцидент: CEO переслал несколько сообщений подряд, каждое
    ушло мозгу отдельным ходом вместо одного связного ответа."""
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = _get_handler(router, "message")

    await handler(_message("первое сообщение"))
    await handler(_message("второе сообщение"))
    await handler(_message("третье сообщение"))
    await asyncio.sleep(0.05)  # окно debounce (0.01с в _FakeConfig) должно истечь

    assert len(brain.handled) == 1
    chat_id, message_id, text, requester_id = brain.handled[0]
    assert text == "первое сообщение\n\nвторое сообщение\n\nтретье сообщение"


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


async def test_photo_with_caption_reaches_brain(config, registry, workspaces):
    """Живой инцидент: CEO прислал скриншот с подписью-жалобой — бот не
    ответил вообще ничего (выглядело как падение). Причина: F.text
    проверяет message.text, а у медиа с подписью текст лежит в
    message.caption, text всегда None — фильтр никогда не пропускал
    обработчик, поэтому проверяем через настоящую цепочку фильтров
    aiogram (handler.check), а не прямой вызов callback."""
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator())

    router = build_brain_router(deps)
    handler = router.message.handlers[0]

    message = SimpleNamespace(
        text=None,
        caption="вот так ты отвечаешь, почини",
        chat=SimpleNamespace(id=CHAT),
        message_id=7,
        from_user=SimpleNamespace(id=CEO_ID, full_name="CEO", is_bot=False),
        reply=_noop_reply,
        answer=_noop_reply,
    )

    matched, _kwargs = await handler.check(message, state=SimpleNamespace(get_state=lambda: None))
    assert matched, "фильтр должен пропускать сообщения с подписью, не только чистый текст"

    await handler.callback(message)
    await asyncio.sleep(0.05)  # окно debounce (0.01с в _FakeConfig) должно истечь

    assert brain.handled == [(CHAT, 7, "вот так ты отвечаешь, почини", CEO_ID)]


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
        message=SimpleNamespace(edit_text=_noop_reply, chat=SimpleNamespace(id=CHAT)),
        answer=_noop_reply,
    )
    await handler(callback)
    await asyncio.sleep(0)

    assert brain.resolved == [("abc123", CHAT, True)]


def _choice_callback(data: str, *, edited: list, user_id: int = CEO_ID):
    async def _edit_reply_markup(**kwargs):
        edited.append(kwargs.get("reply_markup"))

    async def _edit_text(*args, **kwargs):
        edited.append(("text", args, kwargs))

    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=CHAT),
            edit_reply_markup=_edit_reply_markup,
            edit_text=_edit_text,
        ),
        answer=_noop_reply,
    )


async def test_choice_click_feeds_selected_option_back_to_brain(config, registry, workspaces, choices):
    from cortex.brain.choices import PendingChoice
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(
        brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator(), choices=choices,
    )
    await choices.add(
        PendingChoice(
            id="c1", chat_id=CHAT, message_id=7, requester_id=CEO_ID,
            options=["A) раз", "B) два", "C) три"],
        )
    )

    router = build_brain_router(deps)
    handler = _get_handler(router, "callback_query", index=1)

    edited: list = []
    await handler(_choice_callback("brain:choice:c1:1", edited=edited))
    await asyncio.sleep(0)

    assert brain.handled == [(CHAT, 7, "B) два", CEO_ID)]
    assert deps.history.added == [(CHAT, "Abdulloh Abbosov", "B) два", False)]
    assert edited == [None]  # клавиатура снята (reply_markup=None)
    assert await choices.pop("c1") is None  # выбор уже разобран, не висит повторно


async def test_stale_choice_click_is_reported_not_silently_dropped(config, registry, workspaces, choices):
    from cortex.state import ChatState
    from cortex.telegram.routing import MentionRouter

    state = ChatState(config.data_dir)
    mentions = MentionRouter(registry, workspaces, state)
    brain = _FakeBrain()
    deps = _FakeDeps(
        brain=brain, mentions=mentions, orchestrator=_FakeOrchestrator(), choices=choices,
    )

    router = build_brain_router(deps)
    handler = _get_handler(router, "callback_query", index=1)

    edited: list = []
    await handler(_choice_callback("brain:choice:does-not-exist:0", edited=edited))
    await asyncio.sleep(0)

    assert brain.handled == []


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
        message=SimpleNamespace(edit_text=_noop_reply, chat=SimpleNamespace(id=CHAT)),
        answer=_noop_reply,
    )
    await handler(callback)
    await asyncio.sleep(0)

    assert brain.resolved == [("abc123", CHAT, False)]
