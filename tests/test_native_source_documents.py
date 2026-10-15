import hashlib
import json
from copy import deepcopy

import pytest

from app import native_trajectory


BODY = '  中文🙂 e\u0301 / slash\\ quote"\r\nsecond\n\tend  '
SESSION = "d1b35850-a2b0-4899-a869-c53dac5fb481"


@pytest.fixture
def source(tmp_path, monkeypatch):
    def make(runtime="codex_cli", body=BODY, line_ending=b"\n"):
        literal = json.dumps(body, ensure_ascii=False)
        prefix = "前缀🙂 "
        leaf = prefix + literal + " 后缀"
        if runtime == "codex_cli":
            record = {"payload": {"content": [{"text": leaf}]}}
            leaf_path = ["payload", "content", 0, "text"]
        else:
            record = {"content": leaf}
            leaf_path = ["content"]
        raw = json.dumps(record, ensure_ascii=False).encode("utf-8") + line_ending
        path = tmp_path / f"{runtime}.jsonl"
        path.write_bytes(b"invalid preceding record\n" + raw + b"invalid later record\n")
        provenance = {
            "runtime_kind": runtime,
            "session_uuid": SESSION,
            "path": str(tmp_path / "obsolete-not-used.jsonl"),
            "line_index_zero_based": 1,
            "line_number_one_based": 2,
            "raw_line_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_line_bytes": len(raw),
            "leaf_path": leaf_path,
            "substring_start_character_zero_based": len(prefix),
            "substring_end_character_exclusive": len(prefix) + len(literal),
            "literal_encoding_mode": "json_string_literal_utf8",
            "decoded_utf8_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "decoded_utf8_bytes": len(body.encode("utf-8")),
        }

        def codex(session_id, **kwargs):
            assert runtime == "codex_cli"
            assert session_id == SESSION
            return path

        def claude(session_id):
            assert runtime == "claude_cli"
            assert session_id == SESSION
            return path

        monkeypatch.setattr(native_trajectory, "find_codex_session_path", codex)
        monkeypatch.setattr(native_trajectory, "claude_session_path", claude)
        return path, provenance

    return make


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_cli"])
@pytest.mark.parametrize("body", [BODY, "", 'literal \\u4e2d and \\n remain literal'])
@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_exact_body_uses_recorded_leaf_range_and_codec(source, runtime, body, line_ending):
    path, provenance = source(runtime, body, line_ending)
    original = path.read_bytes()
    original_metadata = deepcopy(provenance)
    files = set(path.parent.iterdir())
    assert native_trajectory.read_native_source_body(provenance) == body
    assert path.read_bytes() == original
    assert provenance == original_metadata
    assert set(path.parent.iterdir()) == files


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_cli"])
def test_append_does_not_change_record_version(source, runtime):
    path, provenance = source(runtime)
    with path.open("ab") as stream:
        stream.write(b'{"later":"append"}\n')
    assert native_trajectory.read_native_source_body(provenance) == BODY


def test_missing_resolved_file_is_unavailable(source):
    path, provenance = source()
    path.unlink()
    with pytest.raises(LookupError, match="native source file unavailable"):
        native_trajectory.read_native_source_body(provenance)


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_cli"])
def test_missing_finder_result_is_unavailable(source, monkeypatch, runtime):
    _, provenance = source(runtime)
    name = "find_codex_session_path" if runtime == "codex_cli" else "claude_session_path"
    monkeypatch.setattr(native_trajectory, name, lambda *args, **kwargs: None)
    with pytest.raises(LookupError, match="native source file unavailable"):
        native_trajectory.read_native_source_body(provenance)


@pytest.mark.parametrize("damage", ["rewrite", "truncate", "remove-line"])
def test_changed_or_missing_line_is_unavailable(source, damage):
    path, provenance = source()
    before, selected, after = path.read_bytes().splitlines(keepends=True)
    if damage == "rewrite":
        path.write_bytes(before + selected.replace(b"payload", b"changed") + after)
    elif damage == "truncate":
        path.write_bytes(before + selected[:len(selected) // 2])
    else:
        path.write_bytes(before)
    with pytest.raises(LookupError):
        native_trajectory.read_native_source_body(provenance)


def test_matching_body_elsewhere_cannot_replace_changed_record(source):
    path, provenance = source()
    before, selected, after = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(before + b'{"changed":true}\n' + after + selected)
    with pytest.raises(LookupError, match="native source line version changed"):
        native_trajectory.read_native_source_body(provenance)


def test_negative_slice_that_would_recover_exact_body_is_rejected(source):
    _, provenance = source()
    leaf_length = len("前缀🙂 ") + len(json.dumps(BODY, ensure_ascii=False)) + len(" 后缀")
    provenance["substring_start_character_zero_based"] -= leaf_length
    provenance["substring_end_character_exclusive"] -= leaf_length
    with pytest.raises(ValueError):
        native_trajectory.read_native_source_body(provenance)


@pytest.mark.parametrize("field,value", [
    ("line_index_zero_based", -1),
    ("line_index_zero_based", True),
    ("line_index_zero_based", 1.0),
    ("substring_start_character_zero_based", -1),
    ("substring_start_character_zero_based", True),
    ("substring_start_character_zero_based", 999999),
    ("substring_end_character_exclusive", -1),
    ("substring_end_character_exclusive", 0),
    ("substring_end_character_exclusive", 999999),
    ("substring_end_character_exclusive", "30"),
    ("decoded_utf8_bytes", -1),
    ("decoded_utf8_bytes", True),
    ("session_uuid", ""),
    ("session_uuid", None),
    ("leaf_path", []),
    ("leaf_path", "content"),
    ("leaf_path", ["payload", "missing"]),
    ("leaf_path", ["payload", "content", -1, "text"]),
    ("leaf_path", ["payload", "content", True, "text"]),
    ("leaf_path", ["payload", "content", 1, "text"]),
    ("leaf_path", ["payload", "content", "0", "text"]),
    ("leaf_path", ["payload", "content", 0, "text", "extra"]),
    ("leaf_path", ["payload", "content", 0]),
    ("raw_line_sha256", None),
    ("decoded_utf8_sha256", "short"),
    ("literal_encoding_mode", "plain_utf8"),
    ("literal_encoding_mode", "json_string_literal_ascii"),
    ("literal_encoding_mode", "unknown"),
    ("runtime_kind", "friday"),
])
def test_malformed_metadata_is_rejected(source, field, value):
    _, provenance = source()
    provenance[field] = value
    with pytest.raises(ValueError):
        native_trajectory.read_native_source_body(provenance)


@pytest.mark.parametrize("field", [
    "runtime_kind", "session_uuid", "line_index_zero_based", "raw_line_sha256",
    "leaf_path", "substring_start_character_zero_based",
    "substring_end_character_exclusive", "literal_encoding_mode",
    "decoded_utf8_sha256", "decoded_utf8_bytes",
])
def test_missing_required_metadata_is_rejected(source, field):
    _, provenance = source()
    del provenance[field]
    with pytest.raises(ValueError):
        native_trajectory.read_native_source_body(provenance)


def test_non_mapping_metadata_is_rejected():
    with pytest.raises(ValueError):
        native_trajectory.read_native_source_body(None)


@pytest.mark.parametrize("field,value", [
    ("raw_line_sha256", "0" * 64),
    ("decoded_utf8_sha256", "0" * 64),
    ("decoded_utf8_bytes", 0),
])
def test_version_or_complete_body_mismatch_is_unavailable(source, field, value):
    _, provenance = source()
    provenance[field] = value
    with pytest.raises(LookupError):
        native_trajectory.read_native_source_body(provenance)


@pytest.mark.parametrize("selected", [b"{broken\n", b"\xff\n"])
def test_malformed_selected_json_is_rejected_even_with_matching_line_hash(source, selected):
    path, provenance = source()
    path.write_bytes(b"preceding\n" + selected)
    provenance["raw_line_sha256"] = hashlib.sha256(selected).hexdigest()
    with pytest.raises(ValueError):
        native_trajectory.read_native_source_body(provenance)


@pytest.mark.parametrize("leaf", ['prefix "unterminated', "123", '{"x":"y"}', '"\\ud800"'])
def test_invalid_or_non_string_literal_is_rejected(source, leaf):
    path, provenance = source()
    selected = json.dumps({"payload": {"content": [{"text": leaf}]}}, ensure_ascii=True).encode() + b"\n"
    path.write_bytes(b"preceding\n" + selected)
    provenance.update({
        "raw_line_sha256": hashlib.sha256(selected).hexdigest(),
        "substring_start_character_zero_based": 0,
        "substring_end_character_exclusive": len(leaf),
    })
    with pytest.raises(ValueError):
        native_trajectory.read_native_source_body(provenance)


def test_valid_but_partial_body_cannot_pass_full_body_digest(source):
    path, provenance = source()
    partial = json.dumps(BODY[:4], ensure_ascii=False)
    leaf = "prefix " + partial + " suffix"
    selected = json.dumps({"payload": {"content": [{"text": leaf}]}}, ensure_ascii=False).encode() + b"\n"
    path.write_bytes(b"preceding\n" + selected)
    provenance.update({
        "raw_line_sha256": hashlib.sha256(selected).hexdigest(),
        "substring_start_character_zero_based": 7,
        "substring_end_character_exclusive": 7 + len(partial),
    })
    with pytest.raises(LookupError, match="native source body version changed"):
        native_trajectory.read_native_source_body(provenance)
