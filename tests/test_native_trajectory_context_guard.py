import json

import app.native_trajectory as native_trajectory


def _user_message(text):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["user.text"]
            },
        },
    }


def _completed_answer(text):
    return [
        _user_message(text),
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Complete candidate"}],
            },
        },
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]


def _write_session(path, records):
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def _guard(path, monkeypatch, *, start=0, end=3, session_end=None):
    monkeypatch.setattr(
        native_trajectory,
        "find_codex_session_path",
        lambda *_args, **_kwargs: path,
    )
    return native_trajectory.codex_session_has_final_user_marker_without_compaction(
        "session-a",
        "consumer-source-context-sha256:abc123",
        context_start_line=0,
        attempt_start_line=start,
        attempt_end_line=end,
        session_end_line=end if session_end is None else session_end,
    )


def test_context_reuse_requires_exact_single_final_user_prompt(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        _completed_answer(
            "full task context\n\n<!-- consumer-source-context-sha256:abc123 -->"
        ),
    )

    assert _guard(path, monkeypatch)


def test_context_reuse_rejects_missing_native_answer(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        [
            _user_message("full task context\n\n<!-- consumer-source-context-sha256:abc123 -->"),
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ],
    )

    assert not _guard(path, monkeypatch, end=2)


def test_context_reuse_rejects_unfinished_native_answer(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        _completed_answer(
            "full task context\n\n<!-- consumer-source-context-sha256:abc123 -->"
        )[:-1],
    )

    assert not _guard(path, monkeypatch, end=2)


def test_context_reuse_rejects_answer_before_bound_user_input(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    complete = _completed_answer(
        "full task context\n\n<!-- consumer-source-context-sha256:abc123 -->"
    )
    _write_session(path, [complete[1], complete[0], complete[2]])

    assert not _guard(path, monkeypatch)


def test_later_user_prompt_invalidates_context_reuse(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        [
            *_completed_answer(
                "full task context\n\n<!-- consumer-source-context-sha256:abc123 -->"
            ),
            _user_message("later input in same invocation"),
        ],
    )

    assert not _guard(path, monkeypatch, end=4)


def test_context_reuse_fails_after_native_compaction(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        [
            *_completed_answer(
                "full task context\n\n<!-- consumer-source-context-sha256:abc123 -->"
            ),
            {"type": "compacted", "payload": {"thread_id": "session-a"}},
        ],
    )

    assert not _guard(path, monkeypatch, end=3, session_end=4)


def test_context_reuse_reads_the_explicit_codex_home_for_duplicate_session_ids(
    tmp_path, monkeypatch
):
    configured_home = tmp_path / "configured"
    default_path = tmp_path / "default" / "sessions" / "session-a.jsonl"
    configured_path = configured_home / "sessions" / "session-a.jsonl"
    default_path.parent.mkdir(parents=True)
    configured_path.parent.mkdir(parents=True)
    _write_session(default_path, [_user_message("stale default-home copy")])
    _write_session(
        configured_path,
        _completed_answer(
            "full task context\n\n<!-- consumer-source-context-sha256:abc123 -->"
        ),
    )
    resolved_homes = []

    def find_session(session_id, *, codex_home=None):
        resolved_homes.append(codex_home)
        if codex_home == configured_home:
            return configured_path
        if codex_home is None:
            return default_path
        return None

    monkeypatch.setattr(native_trajectory, "find_codex_session_path", find_session)

    assert native_trajectory.codex_session_has_final_user_marker_without_compaction(
        "session-a",
        "consumer-source-context-sha256:abc123",
        context_start_line=0,
        attempt_start_line=0,
        attempt_end_line=3,
        session_end_line=3,
        codex_home=configured_home,
    )
    assert resolved_homes == [configured_home, configured_home]
