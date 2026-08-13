# tests/test_brain_session.py
from __future__ import annotations

from cortex.brain.session import BrainSession


def test_session_id_is_deterministic_per_chat(tmp_path):
    session = BrainSession(tmp_path)
    first = session.session_id(-1003881673794)
    second = session.session_id(-1003881673794)
    assert first == second
    assert session.session_id(12345) != first


def test_first_call_uses_session_id_flag(tmp_path):
    session = BrainSession(tmp_path)
    flag = session.session_flag(-1003881673794)
    assert flag.startswith("--session-id ")
    assert session.session_id(-1003881673794) in flag


def test_second_call_resumes(tmp_path):
    session = BrainSession(tmp_path)
    chat_id = -1003881673794
    session.session_flag(chat_id)
    session.mark_used(chat_id)

    flag = session.session_flag(chat_id)
    assert flag.startswith("--resume ")


def test_reset_goes_back_to_session_id(tmp_path):
    session = BrainSession(tmp_path)
    chat_id = -1003881673794
    session.mark_used(chat_id)
    assert session.session_flag(chat_id).startswith("--resume ")

    session.reset(chat_id)
    assert session.session_flag(chat_id).startswith("--session-id ")


def test_marker_survives_a_new_instance(tmp_path):
    chat_id = 42
    BrainSession(tmp_path).mark_used(chat_id)

    fresh = BrainSession(tmp_path)
    assert fresh.session_flag(chat_id).startswith("--resume ")
