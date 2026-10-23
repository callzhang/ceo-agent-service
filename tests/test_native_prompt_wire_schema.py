import json

from app import native_prompt_input


def test_codex_session_wire_schema_reads_only_saved_developer_contract(monkeypatch, tmp_path):
    session = tmp_path / "session.jsonl"
    developer = (
        "## Runtime Invariants\nunchanged\n\n"
        "## Pydantic Wire Contract\n"
        '{"type":"object","properties":{"action":{"type":"string"}}}'
        "\n\n## Capability Instructions\nunchanged"
        "\n\n## Work Profile\nA quotation follows:\n## Pydantic Wire Contract\nnot a schema"
    )
    records = [
        {"type": "session_meta", "payload": {"session_id": "saved-session"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "developer",
            "content": [{"type": "input_text", "text": "Native preface"}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["generic.developer_instructions"]
            },
        }},
        {"type": "response_item", "payload": {
            "type": "message", "role": "developer",
            "content": [{"type": "input_text", "text": developer}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["generic.developer_instructions"]
            },
        }},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "ignore this task body"}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["user.text"]
            },
        }},
    ]
    session.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    monkeypatch.setattr(native_prompt_input.native, "find_codex_session_path", lambda *_args, **_kwargs: session)

    assert native_prompt_input.codex_session_wire_schema("saved-session") == {
        "type": "object", "properties": {"action": {"type": "string"}}
    }


def test_codex_session_wire_schema_does_not_infer_missing_developer(monkeypatch, tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps({"type": "session_meta", "payload": {"session_id": "old"}}) + "\n")
    monkeypatch.setattr(native_prompt_input.native, "find_codex_session_path", lambda *_args, **_kwargs: session)

    assert native_prompt_input.codex_session_wire_schema("old") is None


def test_codex_session_wire_schema_reads_native_schema_fingerprint(monkeypatch, tmp_path):
    session = tmp_path / "session.jsonl"
    developer = "## Native Output Schema SHA-256\n" + "a" * 64
    records = [
        {"type": "session_meta", "payload": {"session_id": "native-session"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "developer",
            "content": [{"type": "input_text", "text": developer}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["generic.developer_instructions"]
            },
        }},
    ]
    session.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    monkeypatch.setattr(native_prompt_input.native, "find_codex_session_path", lambda *_args, **_kwargs: session)

    assert native_prompt_input.codex_session_wire_schema("native-session") == {
        "$native_output_schema_sha256": "a" * 64
    }


def test_codex_session_wire_schema_rejects_malformed_native_fingerprint(monkeypatch, tmp_path):
    session = tmp_path / "session.jsonl"
    developer = "## Native Output Schema SHA-256\n" + "a" * 64 + "\nunexpected text"
    records = [
        {"type": "session_meta", "payload": {"session_id": "native-session"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "developer",
            "content": [{"type": "input_text", "text": developer}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["generic.developer_instructions"]
            },
        }},
    ]
    session.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    monkeypatch.setattr(native_prompt_input.native, "find_codex_session_path", lambda *_args, **_kwargs: session)

    assert native_prompt_input.codex_session_wire_schema("native-session") is None


def test_codex_session_wire_schema_rejects_non_object_native_record(monkeypatch, tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text("null\n")
    monkeypatch.setattr(native_prompt_input.native, "find_codex_session_path", lambda *_args, **_kwargs: session)

    assert native_prompt_input.codex_session_wire_schema("old") is None
