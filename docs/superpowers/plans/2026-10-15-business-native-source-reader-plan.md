# Business Native Source Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one unselected reader that reconstructs an exact historical source body from its already proved native CLI provenance without storing another body copy.

**Architecture:** Add `read_native_source_body(native_provenance)` to the existing `app/native_trajectory.py`, which already owns Codex/Claude path resolution and native transcript reads. Resolve the recorded runtime/session with its existing finder, read the recorded raw JSONL line in binary mode, verify its exact version, follow the recorded decoded JSON leaf and character slice, decode the observed JSON string literal, then verify the complete returned UTF-8 body. The helper has no database access or production caller; existing source identity, quote checking and projection remain unchanged.

**Tech Stack:** Python 3.12+, standard-library hashlib/json/itertools/collections.abc, existing native path finders, pytest tmp_path/monkeypatch.

---

## Binding and evidence boundary

Planning base: `de8be799bad471eb3f79fd76beb51eecd3134796`, branch `codex/database-redesign-business-source-20261015`, worktree `/Users/derek/.codex/worktrees/database-redesign-business-source/ceo-agent-service`.

Read `/Users/derek/.agents/AGENTS.md`, the repository instructions, `docs/agent-claims.md`, `docs/architecture.md` and `docs/runtime-mechanism.md` before execution. Claim the exact implementation files before editing; this planning claim owns only this plan and its claim row. Coordinate any overlapping claim rather than editing another worker's hunks.

Approved whole-database direction remains `docs/superpowers/specs/2026-10-11-database-redesign-design.md`: ten core tables plus one migration ledger, independent necessary business facts retained, unique-source provenance inline and only genuinely shared source versions in provenance, with no copied source body or native trajectory. This plan implements only a prerequisite for later exact-source reads. It neither maps the 84 business source fields nor designs/selects/activates a migration or retires any body.

The committed metadata-only `docs/superpowers/specs/2026-10-15-database-redesign-business-native-source-recovery.json` proves a lower bound of 30/188 bodies, 672,297 UTF-8 bytes: seven Codex bodies/173,779 bytes and 23 Claude bodies/498,518 bytes. All 30 actual `native_provenance` entries use `json_string_literal_utf8`; therefore this reader supports only that codec. The probe's attempted plain/ascii candidates do not authorize extra format branches. The remaining 158 bodies remain unproved. The 24 absent Codex UUID files were absent both at indexed paths and in the existing native path inventory. None of these observations establishes that all sources are recoverable or authorizes retirement. Do not reopen the old temporary source DB path recorded in the evidence artifact; that copy is gone.

Consume the actual nested `native_provenance` mapping, not the enclosing source-document record. Required fields are `runtime_kind`, `session_uuid`, `line_index_zero_based`, `raw_line_sha256`, `leaf_path`, `substring_start_character_zero_based`, `substring_end_character_exclusive`, `literal_encoding_mode`, `decoded_utf8_sha256`, `decoded_utf8_bytes`. Existing artifact fields `path`, `line_number_one_based` and `raw_line_bytes` remain diagnostic metadata and need no new persistence or reader argument. Resolve the source through the existing native finders rather than trusting the old physical path. `raw_line_sha256` includes line-ending bytes. Offsets count Python Unicode characters in the decoded leaf, not raw bytes or JSONL-file character positions. Native transcripts can append: the exact recorded line hash is the source-version check, not a whole-file hash.

Use built-in `LookupError` for unavailable native file/line or changed line/body version, and `ValueError` for malformed metadata, JSON, paths, ranges or unsupported runtime/codec. These types already exist in Python and require no new exception framework. A narrow `FileNotFoundError` translation covers disappearance after native path resolution; it returns no fallback. Other I/O failures remain their actual `OSError`. This is exact source-version validation under the approved source rule, not new audit, authorization, review, retry or confirmation policy.

## File responsibilities

| File | Change |
| --- | --- |
| `app/native_trajectory.py` | Add three standard-library imports and one standalone exact source reader immediately after `claude_session_path`, before `count_claude_session_lines`. Reuse both existing finders. |
| `tests/test_native_source_documents.py` | New self-contained synthetic JSONL tests. Monkeypatch both native finders before every reader call; no real home, provider, quota, native CLI or DB fixture. |
| `docs/architecture.md` | Prepend the exact candidate paragraph in Task 3, keeping existing deployed source-document statements true. |
| `docs/runtime-mechanism.md` | Prepend the exact candidate paragraph in Task 3. |
| `docs/agent-claims.md` | Implementation ownership and verified completion status only, preserving other rows. |

Do not modify `app/task_source_documents.py`, `app/task_storage.py`, schema declarations, migrations, versions, constructor selection, provider code or recovery/audit rules. No fallback source, semantic normalization, partial-chunk stitching, search, body cache, persistent derived copy, credentials, proxy, new config, byte-offset index or format framework. Native files remain CLI-owned. Reading earlier raw lines to reach a JSONL line index is necessary; parse and select only the one recorded line. Existing path-inventory caching is reused unchanged and never contains bodies. No file-size bound or benchmark is added speculatively; a measured issue can motivate a separate bounded improvement.

## Task 1: Specify exact source reconstruction and refusal behavior

**Files:** Create `tests/test_native_source_documents.py`.

- [ ] **Step 1: Add the complete synthetic fixture and tests below.**

```python
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
```

- [ ] **Step 2: Run the new focused test file and prove RED.** Run this complete shell block from the worktree; TemporaryDirectory cleans its own root even on test failure.

```sh
python - <<'PY'
import subprocess
import tempfile
with tempfile.TemporaryDirectory(prefix="business-native-reader-red-", dir="/tmp") as root:
    result = subprocess.run(["python", "-m", "pytest", "-q", "tests/test_native_source_documents.py", "--basetemp=" + root + "/pytest"])
    assert result.returncode == 1, result.returncode
PY
```

Expected: failures identify missing `native_trajectory.read_native_source_body`; collection must succeed. No test may read the recovery artifact's real native paths or the removed source DB. Inspect the failure reason, not merely the exit code.

## Task 2: Implement one native source reader

**Files:** Modify `app/native_trajectory.py` imports and insert the function after `claude_session_path` (base lines 218–227). Do not reuse `_native_records`: it parses a range and returns an empty list on failure, losing the exact raw-line version/error contract required here.

- [ ] **Step 1: Add these imports alongside existing standard-library imports.**

```python
import hashlib
from collections.abc import Mapping
from itertools import islice
```

- [ ] **Step 2: Insert this complete function.**

```python
def read_native_source_body(native_provenance: Mapping[str, object]) -> str:
    """Read one exact source body from its recorded CLI-owned JSONL version."""
    if not isinstance(native_provenance, Mapping):
        raise ValueError("native source provenance must be a mapping")
    runtime = native_provenance.get("runtime_kind")
    session_id = native_provenance.get("session_uuid")
    if runtime not in ("codex_cli", "claude_cli"):
        raise ValueError("unsupported native source runtime")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("native source session UUID is required")
    if native_provenance.get("literal_encoding_mode") != "json_string_literal_utf8":
        raise ValueError("unsupported native source literal encoding")
    for field in (
        "line_index_zero_based",
        "substring_start_character_zero_based",
        "substring_end_character_exclusive",
        "decoded_utf8_bytes",
    ):
        value = native_provenance.get(field)
        if type(value) is not int or value < 0:
            raise ValueError(f"invalid native source {field}")
    for field in ("raw_line_sha256", "decoded_utf8_sha256"):
        value = native_provenance.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"invalid native source {field}")
    leaf_path = native_provenance.get("leaf_path")
    if not isinstance(leaf_path, list) or not leaf_path:
        raise ValueError("native source leaf path must be a nonempty list")
    start = native_provenance["substring_start_character_zero_based"]
    end = native_provenance["substring_end_character_exclusive"]
    if end < start:
        raise ValueError("invalid native source character range")
    if runtime == "codex_cli":
        path = find_codex_session_path(session_id)
    else:
        path = claude_session_path(session_id)
    if path is None:
        raise LookupError("native source file unavailable")
    line_index = native_provenance["line_index_zero_based"]
    try:
        with path.open("rb") as stream:
            raw_line = next(islice(stream, line_index, line_index + 1), b"")
    except FileNotFoundError as exc:
        raise LookupError("native source file unavailable") from exc
    if not raw_line:
        raise LookupError("native source line unavailable")
    if hashlib.sha256(raw_line).hexdigest() != native_provenance["raw_line_sha256"]:
        raise LookupError("native source line version changed")
    leaf = json.loads(raw_line)
    for component in leaf_path:
        if isinstance(leaf, dict) and isinstance(component, str) and component in leaf:
            leaf = leaf[component]
        elif (
            isinstance(leaf, list)
            and type(component) is int
            and 0 <= component < len(leaf)
        ):
            leaf = leaf[component]
        else:
            raise ValueError("invalid native source leaf path")
    if not isinstance(leaf, str):
        raise ValueError("native source leaf must be a string")
    if end > len(leaf) or start > len(leaf):
        raise ValueError("invalid native source character range")
    body = json.loads(leaf[start:end])
    if not isinstance(body, str):
        raise ValueError("native source literal must decode to a string")
    encoded = body.encode("utf-8")
    if (
        len(encoded) != native_provenance["decoded_utf8_bytes"]
        or hashlib.sha256(encoded).hexdigest() != native_provenance["decoded_utf8_sha256"]
    ):
        raise LookupError("native source body version changed")
    return body
```

The indexed integer and character checks reject Python negative indexing/slicing and `bool`-as-`int` before selecting a body. The leaf walk only accesses literal dict keys/list indices; it never evaluates expressions. JSON decode and UTF-8 encoding failures are existing `ValueError` subclasses. `path`, line number and raw byte count are not independent authorities: raw line digest and decoded body length/digest supply the actual version proof. The one exception translation reports missing files and has no retry, alternate source or empty-body success path. Empty historical body is valid only when its exact JSON literal and empty UTF-8 digest/length match.

- [ ] **Step 3: Run the exact new tests and prove GREEN.**

```sh
python - <<'PY'
import subprocess
import tempfile
with tempfile.TemporaryDirectory(prefix="business-native-reader-green-", dir="/tmp") as root:
    subprocess.run(["python", "-m", "pytest", "-q", "tests/test_native_source_documents.py", "--basetemp=" + root + "/pytest"], check=True)
PY
```

Expected: all new parameter cases pass; no native CLI/provider subprocess runs. If a failure exposes a mistaken test or contract, repair it within this bounded behavior and rerun this file before broader verification.

## Task 3: Document the unselected helper and verify adjacent contracts

**Files:** Modify `docs/architecture.md`, `docs/runtime-mechanism.md`, own `docs/agent-claims.md` row.

- [ ] **Step 1: Prepend the following paragraph immediately after the H1 in each of the two behavior documents.** Keep the existing source-document runtime paragraphs: current readers still use immutable `business_source_documents.body` and no caller invokes the new helper.

```markdown
An unselected `read_native_source_body` helper in `app/native_trajectory.py`
can reconstruct an exact historical source body from already recorded Codex or
Claude native provenance. It resolves the native session through the existing
CLI path finder, verifies the recorded raw JSONL line hash including its line
ending, follows the recorded JSON leaf and Unicode character range, decodes the
observed `json_string_literal_utf8` value, and verifies the complete UTF-8 body
length and SHA-256. Missing or changed sources raise `LookupError`; malformed
metadata/JSON/paths/ranges and unsupported runtime/codec raise `ValueError`.
Other file I/O failures retain their native errors. No production caller selects
this helper, no body cache or database field is added, and existing source
identity, quote checks and projections remain unchanged. The recovery artifact
proves only 30 of 188 historical bodies (seven Codex and 23 Claude); 158 remain
unproved. This helper does not retire source bodies or activate the approved
ten-core-table-plus-ledger redesign. See
`docs/superpowers/plans/2026-10-15-business-native-source-reader-plan.md`.
```

- [ ] **Step 2: Run the focused adjacent suite, including the accepted 39 source-oracle cases.** These files exercise the untouched identity/quote/projection and native reader contracts. Do not run the whole suite, make a large source copy, open a real source DB, start a service or invoke any provider.

```sh
python - <<'PY'
import subprocess
import tempfile
with tempfile.TemporaryDirectory(prefix="business-native-reader-adjacent-", dir="/tmp") as root:
    subprocess.run([
        "python", "-m", "pytest", "-q",
        "tests/test_native_source_documents.py",
        "tests/test_task_source_documents.py",
        "tests/test_storage_native_trajectory.py",
        "tests/test_business_core_source.py",
        "--basetemp=" + root + "/pytest",
    ], check=True)
PY
python -m ruff check app/native_trajectory.py tests/test_native_source_documents.py
python -m compileall -q app/native_trajectory.py tests/test_native_source_documents.py
git diff --check
```

Expected: all selected cases pass, including the existing 39 business source-oracle cases; Ruff and diff checks clean, compileall exit zero. Tests use their existing self-contained fixture styles. If an existing adjacent fixture itself requires a real native file or unavailable external state, stop and report that precise test dependency rather than changing its behavior or reading user files to make it pass. Cleanup is automatic for each task-owned TemporaryDirectory; verify no task-owned temporary roots remain after the commands.

- [ ] **Step 3: Self-review and update only the implementation claim row with observed commands/results.** Run:

```sh
git diff --stat
git diff -- app/native_trajectory.py tests/test_native_source_documents.py docs/architecture.md docs/runtime-mechanism.md
rg -n 'read_native_source_body' app tests docs/architecture.md docs/runtime-mechanism.md
git status --short
```

Expected: one new function, its tests and both precise candidate paragraphs; no production caller, DB/schema/constructor/provider/policy change. Existing `source_document_key`, `source_contains_quote` and `_document_projection` code remains untouched. No real native body or trajectory enters the diff. Do not interpret synthetic tests as live recovery evidence for the remaining 158 bodies.

- [ ] **Step 4: Commit only owned hunks after verification.** If the working tree has concurrent changes, stage the reviewed hunks interactively and inspect the index. With the claimed files otherwise unchanged, use:

```sh
git add app/native_trajectory.py tests/test_native_source_documents.py docs/architecture.md docs/runtime-mechanism.md
git add -p docs/agent-claims.md
git diff --cached --check
git diff --cached --stat
git diff --cached
git commit -m "feat: add exact native source body reader without runtime selection"
```

Expected: one green implementation commit containing code, focused tests and same-commit behavior docs, with only this worker's claim changes. Do not push, merge main, deploy, activate a runtime caller or retire source bodies. Root's existing sequential SPEC then QUALITY subagent review workflow runs after acceptance of this plan; no new execution-method question is needed.

## Plan self-review recorded before handoff

- Approved source-version rule maps to Task 2 raw-line hash, exact JSON leaf/range and decoded UTF-8 proof; line append behavior, changed/truncated line, malformed JSON/leaf/offsets, supported runtimes and unsupported codecs map to explicit Task 1 tests.
- Strict signed offsets, list bounds and bool rejection avoid accidental negative indexing even when metadata's body digest has been changed. Character ranges are checked before slicing; literal type and full-body length/hash are checked before returning. Unicode, combining sequences, trailing whitespace, CRLF, JSON escapes and empty body remain byte-exact.
- Existing finders and one existing module are reused; no new persistence, database column, provenance framework, fallback or runtime-selection branch is introduced. The single missing-file translation has no recovery behavior.
- All 30 observed coordinates use one codec; plain/ascii modes are explicitly rejected. No branch is justified by an unobserved provider or format.
- The 39-case source oracle, source identity/quote/projection behavior, adopted business facts, necessary current input and whole-database acceptance remain separate and unchanged. Full 10+1 completion and recoverability of the 158 bodies are unproved.
- All required implementation/test/doc content and exact commands are present; no unresolved product decision is delegated to an implementer. Future provenance persistence/caller integration and the separate runtime Audit issue remain outside this bounded plan.
