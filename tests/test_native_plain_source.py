"""Direct prompt text needs explicit native coordinates, never a body archive."""

import json

import pytest

from app import native_trajectory as native


SESSION = "d1b35850-a2b0-4899-a869-c53dac5fb481"
BODY = '会议🙂\n正文 "quoted" \\ literal\\n\n'


@pytest.fixture(params=["codex_cli", "claude_cli"])
def plain_source(tmp_path, monkeypatch, request):
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"message": {"content": [
        {"text": "before " + BODY + " after"},
    ]}}, ensure_ascii=False) + "\n")
    monkeypatch.setattr(native, "find_codex_session_path", lambda *a, **kw: path)
    monkeypatch.setattr(native, "claude_session_path", lambda *a, **kw: path)
    return path, request.param


def locate(runtime, body=BODY, **kwargs):
    return native.locate_native_source_body(
        body, runtime_kind=runtime, session_uuid=SESSION,
        start_line=0, end_line=1, literal_encoding_mode="plain_utf8", **kwargs,
    )


def test_plain_prompt_roundtrip_is_explicit_and_does_not_copy_source(plain_source):
    path, runtime = plain_source
    before = path.read_bytes()
    ref = locate(runtime)
    assert ref["leaf_path"] == ["message", "content", 0, "text"]
    assert ref["substring_start_character_zero_based"] == len("before ")
    assert ref["decoded_utf8_bytes"] == len(BODY.encode())
    assert BODY not in json.dumps(ref, ensure_ascii=False)
    assert native.read_native_source_body(ref, literal_encoding_mode="plain_utf8") == BODY
    with pytest.raises(ValueError, match="encoding"):
        native.read_native_source_body(ref)
    assert path.read_bytes() == before


def test_plain_source_cannot_substitute_a_partial_body(plain_source):
    _, runtime = plain_source
    with pytest.raises(native.NativeSourceMissing):
        locate(runtime, BODY + "more")


def test_plain_source_line_version_change_is_rejected(plain_source):
    path, runtime = plain_source
    ref = locate(runtime)
    path.write_bytes(path.read_bytes().replace(b"before", b"changed"))
    with pytest.raises(native.NativeSourceVersionChanged, match="line version"):
        native.read_native_source_body(ref, literal_encoding_mode="plain_utf8")


def test_plain_source_does_not_search_outside_saved_range(plain_source):
    path, runtime = plain_source
    path.write_bytes(b'{"text":"different"}\n' + path.read_bytes())
    with pytest.raises(native.NativeSourceMissing):
        locate(runtime)


@pytest.mark.parametrize("field", ["decoded_utf8_sha256", "substring_start_character_zero_based"])
def test_plain_source_requires_exact_original_hash_and_slice(plain_source, field):
    _, runtime = plain_source
    ref = locate(runtime)
    ref[field] = "0" * 64 if field == "decoded_utf8_sha256" else 0
    with pytest.raises(native.NativeSourceVersionChanged, match="body version"):
        native.read_native_source_body(ref, literal_encoding_mode="plain_utf8")


def test_unknown_encoding_mode_is_rejected(plain_source):
    _, runtime = plain_source
    with pytest.raises(ValueError, match="encoding"):
        native.locate_native_source_body(
            BODY, runtime_kind=runtime, session_uuid=SESSION, start_line=0,
            end_line=1, literal_encoding_mode="unknown",
        )
