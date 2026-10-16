# Exact Native Scalar Codec Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every finite SQLite REAL when the unpublished native-source kinds move into typed JSON storage, before State reuses that storage.

**Architecture:** Add one exact encoder and payload helper beside the existing encoder. The native-source kinds use the exact helpers and shared SQL decoder/check. Published Reply keeps its existing declaration and payload codec byte-identical so the frozen predecessor remains exactly restorable. No fallback, compatibility tables, retained body snapshots or runtime format routing is added.

**Tech Stack:** Python sqlite3, SQLite JSON/unhex and built-in pow, pytest. Verified interpreter: `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python`, Python3.13.12/SQLite3.50.4 with ENABLE_MATH_FUNCTIONS.

---

Worktree: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service`; baseline `673a6389`. Root evidence: `../specs/2026-10-16-state-real-codec-preflight.json`. All below paths are relative to that worktree. The original full10+ledger goal remains active.

## Task 1: Native scalar codec and complete writer closure

- [x] Claim the exact files and own documentation hunks. No actual database, provider, native CLI, network, production, main, push or deploy. Read `docs/architecture.md` and `docs/runtime-mechanism.md` before app changes.
- [x] Create `tests/test_native_scalar_codec.py` with the complete tests below. Run the Configs regression alone before app changes and preserve its semantic failure evidence.

```python
"""Lossless unpublished native-source scalars; published Reply is separate."""
from contextlib import closing
import json
import math
import random
import sqlite3
import struct

import pytest

from app import action_storage as storage
from app.action_email_definitions import _encoded_value, _encoded_value_check
from app.configs_definitions import CONFIG_PROJECTIONS
from app.configs_storage import create_configs_schema, insert_config, update_config
from app.schedules_storage import create_schedules_schema
from tests.configs_source_fixtures import source_db, insert


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("field,value", (
    ("enabled", -2.2101102838387424e-85),
    ("display_name", 7.294214896064974e-137),
))
def test_configs_native_real_insert_and_update_match_literal_source(variant, field, value):
    values = dict(account_id="synthetic", target_type="direct", target_id="target",
                  display_name="test", trigger_mode="all", enabled=1)
    values[field] = value
    with source_db(variant) as source, closing(sqlite3.connect(":memory:")) as target:
        insert(source, "wechat_reply_scopes", values)
        storage.create_action_schema(target, include_reply=True,
            reply_variant="fresh_memory_fk", include_email=True,
            include_followup_sync=True, include_skill_config_receipts=True)
        create_schedules_schema(target)
        create_configs_schema(target)
        target.execute("begin")
        rowid = insert_config(target, "wechat_reply_scopes", values).lastrowid
        for update, next_value in ((False, value), (True, -value)):
            if update:
                source.execute(f"update wechat_reply_scopes set {field}=?", (next_value,))
                assert update_config(target, "wechat_reply_scopes", rowid, {field: next_value}) == 1
            expected = source.execute(f"select {field},typeof({field}) from wechat_reply_scopes").fetchone()
            actual = target.execute(f"select {field},typeof({field}) from (" +
                CONFIG_PROJECTIONS["wechat_reply_scopes"] + ")").fetchone()
            assert actual == expected


def test_finite_binary_real_codec_matches_native_affinity_and_bits():
    values = [0.0, -0.0, 5e-324, -5e-324, 2.2250738585072014e-308,
              1.7976931348623157e308, -2.2101102838387424e-85,
              7.294214896064974e-137, 1.0, 1.5]
    rng = random.Random(20261016)
    while len(values) < 5010:
        value = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
    decoded = _encoded_value("payload", "$.v")
    check = _encoded_value_check("payload", "$.v")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table native(t text,i integer)")
        db.execute(f"create table target(payload text,raw generated always as ({decoded}) virtual,"
                   f"t text generated always as ({decoded}) virtual,"
                   f"i integer generated always as ({decoded}) virtual,check({check}))")
        for value in values:
            payload = json.dumps({"v": storage._encoded_native_sqlite_value(value)}, allow_nan=False)
            db.execute("delete from native")
            db.execute("delete from target")
            db.execute("insert into native values(?,?)", (value, value))
            db.execute("insert into target(payload) values(?)", (payload,))
            raw = db.execute("select raw from target").fetchone()[0]
            assert struct.pack(">d", raw) == struct.pack(">d", value)
            assert db.execute("select t,typeof(t),i,typeof(i) from target").fetchone() == db.execute(
                "select t,typeof(t),i,typeof(i) from native").fetchone()
        assert db.execute("pragma quick_check").fetchone() == ("ok",)


@pytest.mark.parametrize("tag", (
    {"t": "real", "m": 1, "e": -1075},
    {"t": "real", "m": 1, "e": 972},
    {"t": "real", "m": 2**53, "e": 0},
    {"t": "real", "m": -(2**53), "e": 0},
    {"t": "real", "m": 1.5, "e": 0},
    {"t": "real", "m": "1", "e": 0},
    {"t": "real", "m": 1, "e": 0.0},
    {"t": "real", "m": 1, "e": 0, "extra": 1},
    {"t": "real", "m": 1},
    {"t": "real", "e": 0},
))
def test_malformed_finite_real_tags_are_refused(tag):
    with closing(sqlite3.connect(":memory:")) as db:
        check = _encoded_value_check("payload", "$.v")
        db.execute(f"create table target(payload text,check({check}))")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into target values(?)", (json.dumps({"v": tag}),))
```

Run with `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, owned temporary `--basetemp` removed afterward. Initial `-k configs_native_real` must fail on value mismatch, not an import error.

- [x] Add the following helpers to `app/action_storage.py` immediately before `_reply_payload`. Leave the existing `_encoded_sqlite_value` and `_reply_payload` bodies unchanged.

```python
def _encoded_native_sqlite_value(value):
    """Preserve finite REAL bits for the unpublished native-source kinds."""
    if isinstance(value, float) and math.isfinite(value):
        numerator, denominator = value.as_integer_ratio()
        shift = max(0, abs(numerator).bit_length() - 53)
        significand = numerator >> shift
        exponent = shift - (denominator.bit_length() - 1)
        if value == 0.0:
            significand, exponent = math.copysign(0.0, value), 0
        return {"t": "real", "m": significand, "e": exponent}
    return _encoded_sqlite_value(value)


def _native_payload(values):
    return json.dumps({field: _encoded_native_sqlite_value(value)
                       for field, value in values.items()},
                      ensure_ascii=False, allow_nan=False, separators=(",", ":"))
```

- [x] Replace only the shared decoder and check in `app/action_email_definitions.py` with these complete bodies. Existing scalar/BLOB/nonfinite representation remains valid. The REAL tag is a finite source scalar representation, not a new business field.

```python
def _encoded_value(document, path):
    tag = f"json_extract({document},'{path}.t')"
    return (f"case when json_type({document},'{path}')='object' then "
            f"case {tag} when 'blob' then unhex(json_extract({document},'{path}.v')) "
            f"when 'real' then json_extract({document},'{path}.m') "
            f"* pow(2.0,json_extract({document},'{path}.e')) "
            "when 'inf' then 9e999 when '-inf' then -9e999 end "
            f"else json_extract({document},'{path}') end")


def _encoded_value_check(document, path):
    value_type = f"json_type({document},'{path}')"
    tag = f"json_extract({document},'{path}.t')"
    obj = f"json_extract({document},'{path}')"
    blob_value = f"json_extract({document},'{path}.v')"
    blob = (f"({tag}='blob' and json_type({document},'{path}.v')='text' "
            f"and length({blob_value})%2=0 and unhex({blob_value}) is not null "
            f"and json_remove({obj},'$.t','$.v')='{{}}')")
    nonfinite = f"({tag} in ('inf','-inf') and json_remove({obj},'$.t')='{{}}')"
    mantissa = f"json_extract({document},'{path}.m')"
    exponent = f"json_extract({document},'{path}.e')"
    finite = (f"({tag}='real' and json_type({document},'{path}.e')='integer' "
              f"and {exponent} between -1074 and 971 and "
              f"((json_type({document},'{path}.m')='integer' and {mantissa} "
              "between -9007199254740991 and 9007199254740991) or "
              f"(json_type({document},'{path}.m')='real' and {mantissa}=0)) "
              f"and json_remove({obj},'$.t','$.m','$.e')='{{}}')")
    return (f"case when {value_type} in ('null','text','integer','real','true','false') then 1 "
            f"when {value_type}='object' then coalesce((json_type({document},'{path}.t')='text' "
            f"and ({blob} or {nonfinite} or {finite})),0) else 0 end")
```

- [x] Make the entire native writer closure exact with this complete edit script. It deliberately excludes published Reply operations. Review the resulting diff before tests; do not change frozen source hashes.

```python
from pathlib import Path

p = Path("app/action_storage.py")
text = p.read_text()
old = "    email_schema_parts,\n"
assert text.count(old) == 1
text = text.replace(old, "    _encoded_value_check, email_schema_parts,\n")
old = "email_schema_parts(_reply_value_check)"
assert text.count(old) == 1
text = text.replace(old, 'email_schema_parts(\n            lambda field: _encoded_value_check("payload_json", "$." + field))')
for name, old, new in (
    ("_email_payload", "_reply_payload", "_native_payload"),
    ("_insert_email_action", "_encoded_sqlite_value", "_encoded_native_sqlite_value"),
    ("_update_email_action", "_encoded_sqlite_value", "_encoded_native_sqlite_value"),
    ("_sync_payload", "_reply_payload", "_native_payload"),
    ("update_sync_action", "_encoded_sqlite_value", "_encoded_native_sqlite_value"),
):
    start = text.index("\ndef " + name + "(")
    end = text.find("\ndef ", start + 1)
    if end < 0:
        end = len(text)
    part = text[start:end]
    assert old in part, name
    text = text[:start] + part.replace(old, new) + text[end:]
p.write_text(text)
for filename in (
    "business_storage.py", "business_schema_release.py", "configs_storage.py",
    "action_skill_config_storage.py", "provider_state_storage.py",
):
    p = Path("app") / filename
    text = p.read_text()
    assert "_encoded_sqlite_value" in text
    p.write_text(text.replace("_encoded_sqlite_value", "_encoded_native_sqlite_value"))
for filename in ("action_email_storage.py", "action_followup_sync_storage.py"):
    p = Path("app") / filename
    text = p.read_text()
    assert "storage._reply_payload" in text
    p.write_text(text.replace("storage._encoded_sqlite_value", "storage._encoded_native_sqlite_value")
                    .replace("storage._reply_payload", "storage._native_payload"))
```

- [x] Regenerate **only** `app/provider_state_schema_release.py:TARGET_FINGERPRINT` from the new canonical Provider declaration. All source hashes and the published Reply factory stay unchanged.

```python
from pathlib import Path
import sqlite3
from contextlib import closing
from app.action_reply_storage import _fingerprint, _source_declaration
from app.provider_state_storage import create_provider_state_schema
from app.provider_state_schema_release import TARGET_FINGERPRINT

with closing(sqlite3.connect(":memory:")) as db:
    create_provider_state_schema(db)
    fingerprint = _fingerprint(_source_declaration(db, "provider_state"))
assert fingerprint != TARGET_FINGERPRINT
p = Path("app/provider_state_schema_release.py")
text = p.read_text()
assert text.count('TARGET_FINGERPRINT = "' + TARGET_FINGERPRINT + '"') == 1
p.write_text(text.replace('TARGET_FINGERPRINT = "' + TARGET_FINGERPRINT + '"',
                          'TARGET_FINGERPRINT = "' + fingerprint + '"'))
```

## Task 2: Forward/current-write/inverse acceptance

- [x] Add a paired regression to `tests/test_native_scalar_codec.py`, reusing the existing parametrized source fixture. This complete test binds both source variants, exact schema inverse, native Email/Provider/Configs copy and current-write inverse. It preserves unrelated published Reply behavior. Place the two imports with the module imports at the top of the file, then append the test function.

```python
from tests.test_email_action_schema_release import source as source, _prepare
from app.provider_state_storage import update_important_signals


def test_native_reals_survive_paired_forward_current_writes_and_inverse(source):
    first = 2.2101102838387424e-85
    second = -first
    with closing(sqlite3.connect(source)) as db:
        db.execute("update email_action_plans set confidence=?", (first,))
        db.execute("insert into wechat_reply_scopes(account_id,target_type,target_id,"
                   "display_name,trigger_mode,enabled) values('synthetic','direct','target','test','all',?)",
                   (first,))
        db.execute("insert into email_provider_observations(account_id,stable_message_identity,"
                   "state,provider_folder_id,important,important_signals_json,observed_at) "
                   "values('synthetic','message','available','inbox',1,?,'time')", (first,))
        source_provider_signal = db.execute("select important_signals_json from email_provider_observations "
                                          "where account_id='synthetic'").fetchone()[0]
        original_schema = db.execute("select type,name,tbl_name,sql from sqlite_schema "
                                     "where sql is not null order by type,name").fetchall()
        db.commit()
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select email_confidence,typeof(email_confidence) from actions "
                          "where record_type='email_action_plans'").fetchone() == (first, "real")
        assert db.execute("select important_signals_json,typeof(important_signals_json) from provider_state "
                          "where account_id='synthetic'").fetchone() == (source_provider_signal, "text")
        assert db.execute("select enabled,typeof(enabled) from (" +
            CONFIG_PROJECTIONS["wechat_reply_scopes"] + ")").fetchone() == (first, "real")
        db.execute("begin immediate")
        identity = db.execute("select cast(legacy_key as integer) from actions "
                              "where record_type='email_action_plans'").fetchone()[0]
        storage.update_email_action(db, "email_action_plans", identity, {"confidence": first * 3})
        key = db.execute("select cast(legacy_key as integer) from configs "
                         "where record_type='wechat_reply_scopes'").fetchone()[0]
        assert update_config(db, "wechat_reply_scopes", key, {"enabled": second}) == 1
        assert update_important_signals(db, "synthetic", "message", 1, second)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select confidence,typeof(confidence) from email_action_plans").fetchone() == (first * 3, "real")
        assert db.execute("select enabled,typeof(enabled) from wechat_reply_scopes").fetchone() == (second, "real")
        expected_provider_signal = db.execute("select cast(? as text)", (second,)).fetchone()[0]
        assert db.execute("select important_signals_json,typeof(important_signals_json),important "
                          "from email_provider_observations where account_id='synthetic'").fetchone() == (
                              expected_provider_signal, "text", 1)
        assert db.execute("select type,name,tbl_name,sql from sqlite_schema "
                          "where sql is not null order by type,name").fetchall() == original_schema
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
    release.apply()
    release.rollback()
```

- [x] Run this exact focused matrix with the recorded interpreter and owned pytest root: `tests/test_native_scalar_codec.py tests/test_email_action_migration.py tests/test_email_action_schema_release.py tests/test_provider_state_schema_release.py tests/test_configs_schema_transfer.py tests/test_reply_attempt_action_storage.py`. Preserve original source-only fixtures and captured hashes. Existing Reply declaration/source hash and current-fact inverse cases must pass unchanged.
- [x] Add coverage for existing BLOB/nonfinite/NaN/boolean source values by running `tests/test_email_action_writes.py tests/test_business_storage.py tests/test_business_schema_release.py tests/test_provider_state_storage.py tests/test_configs_storage.py tests/test_configs_target_integrated.py tests/test_followup_sync_action_writes.py tests/test_followup_sync_action_migration.py tests/test_followup_sync_action_inverse.py`. These focused files cover separate raw/public/direct-update paths. No full suite.
- [x] Ruff only changed Python files, compile them without pycache, and run diff check. Run a negative control reverting the exact encoder's finite branch to scalar output in a process-local monkeypatch; the Configs reproducer must fail again. Restore no files because the mutant is process-local.
The scoped behavior paragraph for both architecture and runtime documents is:

> The unpublished native-source Email/Business/Provider/Configs/Skill/Sync storage encodes finite REAL values as an exact binary significand and exponent and decodes them with SQLite's built-in `pow`, preserving native TEXT/INTEGER affinity results. SQLite JSON, `unhex` and math functions are required. Published Reply keeps its existing declaration and scalar format, and frozen source fingerprints remain unchanged; native-source inverse consumes current decoded facts. This does not select State or publish a deployment.

- [x] Update scoped behavior paragraphs in `docs/architecture.md` and `docs/runtime-mechanism.md`, append concise source-preparation evidence, and commit only claimed files. State the explicit built-in math dependency, unchanged published Reply format and source hashes, unpublished status, and no production execution. Record actual test counts/timing, not predicted counts.
- [x] Obtain independent SPEC, then fresh QUALITY. Root then runs owned frozen-source copied-data forward/current-write/inverse proof. This helper change does not select State or complete the10+ledger goal.

## Root self-review

The minimal repro and exact candidate are independently evidenced. Complete call-path research includes raw Email/Sync conversion, separate Business copy, and Provider direct updates. The plan keeps the already-published Reply codec literal, avoiding both invented compatibility machinery and unrepresentable new Reply tags in its inverse. The native-source writer closure, generated checks and pinned Provider target fingerprint change atomically. No source hash widening or new business field is needed. Tests cover semantic RED, scalar shape/affinity/bits, current-fact inverse and unchanged source declarations; full copied-source acceptance remains separate.


Root proposal validation: all six Python blocks compile. The complete proposed test module extracted to an owned temporary directory produces exactly4 intended Configs value-mismatch failures/13 deselected in0.33s on the unchanged candidate. Failures cover both original variants and numeric REAL/TEXT formatting. The temporary directory was removed and absence verified. No app or repository test file was changed by this preflight.

Implementation note: The original paired sample chose `email_actions.attempt_count` and Provider `important` for tiny REAL values. Their existing attempt-count drift trigger and 0/1 CHECK correctly reject those values before the codec is exercised. The executable paired sample above instead uses valid Email plan `confidence` and Provider JSON-valid `important_signals_json`; it keeps actual/fresh forward, current-write, exact inverse, schema, and quick-check assertions.
