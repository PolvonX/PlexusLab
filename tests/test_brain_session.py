# tests/test_brain_session.py
from __future__ import annotations

from cortex.brain.session import BrainSession

CHAT = -100500


def test_first_call_uses_session_id_flag(tmp_path):
    session = BrainSession(tmp_path)
    flag = session.session_flag(CHAT)
    assert flag.startswith("--session-id ")


def test_fresh_candidates_are_random_not_deterministic(tmp_path):
    """Живой инцидент: старый uuid5(chat_id) был детерминированным, так что
    если claude хоть раз отклонял --session-id с этим id (например, "already
    in use" из-за смены cwd), КАЖДАЯ следующая попытка для чата возвращала
    ровно тот же самый id и билась в ту же ошибку навсегда. Кандидаты для
    --session-id теперь обязаны быть разными между вызовами без mark_used."""
    session = BrainSession(tmp_path)
    first = session.session_flag(CHAT)
    second = session.session_flag(CHAT)
    assert first != second


def test_second_call_resumes_the_id_that_was_actually_used(tmp_path):
    session = BrainSession(tmp_path)
    chat_id = CHAT
    first_flag = session.session_flag(chat_id)
    session.mark_used(chat_id, first_flag)

    resumed = session.session_flag(chat_id)
    assert resumed == f"--resume {first_flag.split(maxsplit=1)[1]}"


def test_reset_forgets_the_session_and_the_next_id_is_different(tmp_path):
    session = BrainSession(tmp_path)
    chat_id = CHAT
    first_flag = session.session_flag(chat_id)
    session.mark_used(chat_id, first_flag)
    assert session.session_flag(chat_id).startswith("--resume ")

    session.reset(chat_id)
    after_reset = session.session_flag(chat_id)
    assert after_reset.startswith("--session-id ")
    assert after_reset != first_flag  # не тот же "занятый" id, что раньше


def test_marker_survives_a_new_instance(tmp_path):
    chat_id = 42
    session = BrainSession(tmp_path)
    used = session.session_flag(chat_id)
    session.mark_used(chat_id, used)

    fresh = BrainSession(tmp_path)
    resumed = fresh.session_flag(chat_id)
    assert resumed == f"--resume {used.split(maxsplit=1)[1]}"
