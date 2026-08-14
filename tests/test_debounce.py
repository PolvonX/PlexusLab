# tests/test_debounce.py
"""Живой инцидент: пересылка нескольких сообщений подряд запускала мозг
на каждое отдельно — вместо одного связного ответа CEO получал шквал
независимых реплик (в одном случае мозг принял свои же прошлые ответы,
пришедшие как форварды, за "эхо" и зациклился на этом). MessageDebouncer
схлопывает всплеск быстрых сообщений одного чата в один ход."""

from __future__ import annotations

import asyncio

from cortex.telegram.debounce import MessageDebouncer

CHAT = -100500
OTHER_CHAT = -100600


async def test_single_message_flushes_once_after_delay():
    flushed = []

    async def flush(**kwargs):
        flushed.append(kwargs)

    debouncer = MessageDebouncer(delay=0.05, flush=flush)
    debouncer.add(chat_id=CHAT, text="привет", message_id=1, requester_id=1001)

    await asyncio.sleep(0.15)

    assert flushed == [{"chat_id": CHAT, "text": "привет", "message_id": 1, "requester_id": 1001}]


async def test_quick_messages_are_combined_into_one_flush():
    flushed = []

    async def flush(**kwargs):
        flushed.append(kwargs)

    debouncer = MessageDebouncer(delay=0.08, flush=flush)
    debouncer.add(chat_id=CHAT, text="сообщение 1", message_id=1, requester_id=1001)
    await asyncio.sleep(0.02)
    debouncer.add(chat_id=CHAT, text="сообщение 2", message_id=2, requester_id=1001)
    await asyncio.sleep(0.02)
    debouncer.add(chat_id=CHAT, text="сообщение 3", message_id=3, requester_id=1001)

    await asyncio.sleep(0.2)

    assert len(flushed) == 1
    assert flushed[0]["text"] == "сообщение 1\n\nсообщение 2\n\nсообщение 3"
    assert flushed[0]["message_id"] == 3  # анкерим на последнее сообщение батча


async def test_new_message_pushes_the_timer_back():
    flushed = []

    async def flush(**kwargs):
        flushed.append(kwargs)

    debouncer = MessageDebouncer(delay=0.08, flush=flush)
    debouncer.add(chat_id=CHAT, text="a", message_id=1, requester_id=1001)
    await asyncio.sleep(0.05)  # меньше delay — флаша ещё не должно быть
    assert flushed == []
    debouncer.add(chat_id=CHAT, text="b", message_id=2, requester_id=1001)
    await asyncio.sleep(0.05)  # снова меньше delay от последнего сообщения
    assert flushed == []

    await asyncio.sleep(0.1)
    assert len(flushed) == 1
    assert flushed[0]["text"] == "a\n\nb"


async def test_different_chats_do_not_interfere():
    flushed = []

    async def flush(**kwargs):
        flushed.append(kwargs)

    debouncer = MessageDebouncer(delay=0.05, flush=flush)
    debouncer.add(chat_id=CHAT, text="chat1", message_id=1, requester_id=1001)
    debouncer.add(chat_id=OTHER_CHAT, text="chat2", message_id=2, requester_id=2002)

    await asyncio.sleep(0.15)

    assert len(flushed) == 2
    by_chat = {f["chat_id"]: f["text"] for f in flushed}
    assert by_chat == {CHAT: "chat1", OTHER_CHAT: "chat2"}
