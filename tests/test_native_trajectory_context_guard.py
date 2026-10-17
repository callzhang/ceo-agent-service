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


def _write_session(path, records):
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def _guard(path, monkeypatch, *, start=0, end=1, session_end=None):
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
        [
            _user_message(
                "full task context\n\n"
                "<!-- consumer-source-context-sha256:abc123 -->"
            )
        ],
    )

    assert _guard(path, monkeypatch)


def test_later_user_prompt_invalidates_context_reuse(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        [
            _user_message(
                "full task context\n\n"
                "<!-- consumer-source-context-sha256:abc123 -->"
            ),
            _user_message("later input in same invocation"),
        ],
    )

    assert not _guard(path, monkeypatch, end=2)


def test_context_reuse_fails_after_native_compaction(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    _write_session(
        path,
        [
            _user_message(
                "full task context\n\n"
                "<!-- consumer-source-context-sha256:abc123 -->"
            ),
            {"type": "compacted", "payload": {"thread_id": "session-a"}},
        ],
    )

    assert not _guard(path, monkeypatch, end=1, session_end=2)
