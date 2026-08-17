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


def test_session_flag_expires_by_age(tmp_path, monkeypatch):
    """Живой инцидент: многочасовая непрерывная --resume сессия деградировала
    (мозг начал путать формат <action> и галлюцинировать). Сессия старше 6
    часов должна считаться протухшей и не резюмироваться."""
    import time
    session = BrainSession(tmp_path)
    session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")
    assert session.session_flag(CHAT).startswith("--resume")

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 6 * 3600 + 1)
    assert session.session_flag(CHAT).startswith("--session-id")


def test_session_flag_expires_by_turn_count(tmp_path):
    session = BrainSession(tmp_path)
    flag = "--session-id 11111111-1111-1111-1111-111111111111"
    session.mark_used(CHAT, flag)
    for _ in range(50):
        assert session.session_flag(CHAT).startswith("--resume")
        session.mark_used(CHAT, session.session_flag(CHAT))
    # 51-й ход — turn_count перевалил за 50
    assert session.session_flag(CHAT).startswith("--session-id")


def test_expired_session_flag_does_not_mutate_stored_state(tmp_path, monkeypatch):
    """session_flag() только читает — expiry не должна тихо стирать файл
    (это дело reset()/следующего mark_used()), иначе два конкурентных
    вызова session_flag() без mark_used() между ними дадут противоречивую
    картину."""
    import time
    session = BrainSession(tmp_path)
    session.mark_used(CHAT, "--session-id 11111111-1111-1111-1111-111111111111")

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 6 * 3600 + 1)
    session.session_flag(CHAT)  # первый вызов — expired
    session.session_flag(CHAT)  # второй вызов подряд — тоже expired, не падает

