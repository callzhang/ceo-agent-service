# Native scalar binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development, fresh SPEC then QUALITY.

**Goal:** Encode the same underlying supported Python scalar that sqlite3 binds, preserving native point-write values and constraints after removal of the default preselection query.
**Architecture:** Normalize only the unpublished _encoded_native_sqlite_value input through base float/str methods and the buffer protocol. Reject non-C-contiguous buffers exactly as sqlite3 binding does. Keep the published _encoded_sqlite_value and Reply format byte-identical; no SQL preselection, caller special cases or source-field additions.
**Tech Stack:** Python3.13.12/SQLite3.50.4.

Independent point-clock QUALITY confirmed native/previous State point INSERT stored Real(1.5) as TEXT1.5 and Blob(original) as original bytes, while direct candidate encoding honored overridden conversion methods. A negative Real(-1.5) with spoofed ratio could bypass generation>=0. Noncontiguous memoryview also became incorrectly accepted. The generic native encoder must follow the SQLite bound value on all native-source consumers.

Modify only app/action_storage.py _encoded_native_sqlite_value by replacing its exact AST span with this complete block. Base module SHAaa5a5edd99c70bd099d345d4c073e718ed70f387ca10c84f82ac8aefb5b4670f; candidate SHA3710ce46766226e5e315e5b2922533e3e63e225290d776a85bdc50737937accc. All other function/import/declaration ASTs and published Reply encoder bytes remain unchanged.

```python
def _encoded_native_sqlite_value(value):
    """Encode the underlying scalar that sqlite3 binds for native-source kinds."""
    if isinstance(value, float):
        value = float.__float__(value)
    elif isinstance(value, str):
        value = str.__str__(value)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        view = memoryview(value)
        if not view.c_contiguous:
            raise BufferError("memoryview: underlying buffer is not C-contiguous")
        return {"t": "blob", "v": view.tobytes().hex()}
    elif value is not None and not isinstance(value, int):
        raise sqlite3.ProgrammingError("unsupported SQLite source value")
    if isinstance(value, float) and math.isfinite(value):
        numerator, denominator = value.as_integer_ratio()
        shift = max(0, abs(numerator).bit_length() - 53)
        significand = numerator >> shift
        exponent = shift - (denominator.bit_length() - 1)
        if value == 0.0:
            significand, exponent = math.copysign(0.0, value), 0
        return {"t": "real", "m": significand, "e": exponent}
    return _encoded_sqlite_value(value)

```

Create tests/test_native_scalar_binding.py exactly (SHA8f2f1ffa52bede8d93c8ad4a89931d45ca0018176b359357b7f82fbb19cb0e29):
```python
"""Unpublished native scalar encoding follows sqlite3's underlying binding."""
import math
import sqlite3

import pytest

from app.action_storage import _encoded_native_sqlite_value
from app.state_storage import insert_state, update_state, upsert_state, update_state_rows, state_json_field
from tests.test_state_storage import facts, native_insert, pair as pair, required


class RatioFloat(float):
    def as_integer_ratio(self):
        return (1, 1)


class ConvertedFloat(float):
    def __float__(self):
        return 99.0

    def __eq__(self, other):
        return True


class ChangedBytes(bytes):
    def __bytes__(self):
        return b"changed"


class ChangedBytearray(bytearray):
    def __bytes__(self):
        return b"changed"


class ChangedString(str):
    def __str__(self):
        return "changed"

    def __eq__(self, other):
        return True


class Unsupported:
    def __eq__(self, other):
        return True


VALUES = (
    RatioFloat(1.5), RatioFloat(-1.5), RatioFloat(0.0), RatioFloat(-0.0),
    RatioFloat(math.inf), RatioFloat(-math.inf), RatioFloat(math.nan),
    ConvertedFloat(2.5), ConvertedFloat(0.0),
    ChangedBytes(b"original"), ChangedBytearray(b"original"),
    ChangedString("original"), memoryview(b"original"),
    memoryview(b"abcdef")[::2], Unsupported(),
)


@pytest.mark.parametrize("value", VALUES)
def test_encoder_matches_real_sqlite_bound_scalar(value):
    with sqlite3.connect(":memory:") as db:
        try:
            bound = db.execute("select ?", (value,)).fetchone()[0]
        except (BufferError, sqlite3.ProgrammingError) as exc:
            with pytest.raises(type(exc)):
                _encoded_native_sqlite_value(value)
        else:
            assert _encoded_native_sqlite_value(value) == _encoded_native_sqlite_value(bound)


@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("operation", ("insert", "point", "upsert", "bulk"))
def test_state_native_binding_and_failure_leave_prior_work(pair, value, operation):
    source, target, _variant = pair
    if operation != "insert":
        seed = {"key": "key", "value": "before", "updated_at": "t"}
        native_insert(source, "service_state", seed, -7)
        insert_state(target, "service_state", seed, source_rowid=-7)
    before = facts(source, "service_state")
    for db in (source, target):
        db.execute("create table caller_work(value)")
        db.execute("insert into caller_work values('keep')")

    def native():
        if operation == "insert":
            return source.execute(
                "insert into service_state(key,value,updated_at) values('key',?,'t')", (value,)
            ).rowcount
        if operation == "point":
            return source.execute(
                "update or replace service_state set value=? where rowid=-7", (value,)
            ).rowcount
        if operation == "upsert":
            return source.execute(
                "insert into service_state(key,value,updated_at) values('key',?,'t') "
                "on conflict(key) do update set value=excluded.value", (value,)
            ).rowcount
        return source.execute("update service_state set value=? where key='key'", (value,)).rowcount

    def candidate():
        if operation == "insert":
            return insert_state(target, "service_state", {
                "key": "key", "value": value, "updated_at": "t",
            }).rowcount
        if operation == "point":
            return update_state(target, "service_state", -7, {"value": value}, replace=True)
        if operation == "upsert":
            return upsert_state(target, "service_state", {
                "key": "key", "value": value, "updated_at": "t",
            }, {"value": state_json_field("value")})
        return update_state_rows(target, "service_state", {"value": value},
                                 where_sql="key='key'")
    try:
        count = native()
    except (BufferError, sqlite3.ProgrammingError, sqlite3.IntegrityError) as exc:
        with pytest.raises(type(exc)):
            candidate()
        assert facts(source, "service_state") == before == facts(target, "service_state", True)
    else:
        assert candidate() == count == 1
        assert facts(source, "service_state") == facts(target, "service_state", True)
    for db in (source, target):
        assert db.in_transaction
        assert db.execute("select value from caller_work").fetchone()[0] == "keep"


@pytest.mark.parametrize("operation", ("insert", "point"))
def test_subclass_cannot_change_native_generation_constraint(pair, operation):
    source, target, variant = pair
    values = required(variant, "dispatcher_claim_leases", "generation")
    if operation == "point":
        native_insert(source, "dispatcher_claim_leases", values, -7)
        insert_state(target, "dispatcher_claim_leases", values, source_rowid=-7)
    before = facts(source, "dispatcher_claim_leases")
    value = RatioFloat(-1.5)
    with pytest.raises(sqlite3.IntegrityError):
        if operation == "insert":
            native_insert(source, "dispatcher_claim_leases", values | {"generation": value}, -7)
        else:
            source.execute("update or replace dispatcher_claim_leases set generation=? where rowid=-7", (value,))
    with pytest.raises(sqlite3.IntegrityError):
        if operation == "insert":
            insert_state(target, "dispatcher_claim_leases", values | {"generation": value}, source_rowid=-7)
        else:
            update_state(target, "dispatcher_claim_leases", -7, {"generation": value}, replace=True)
    assert facts(source, "dispatcher_claim_leases") == before == facts(target, "dispatcher_claim_leases", True)

```

- [x] Root executable disposable preflight before app edits: baseline76 intended failures/63passes; complete candidate793 passed28.74s. Owned temp removed. Covered actual/fresh State insert, pointORREPLACE, UPSERT and bulk updates, native binder direct SELECT oracle, existing generation CHECK and failure-preserved prior caller work.
- [x] Exact function/test replacement, preserving every other function AST and source/State declarations. Run focused793 matrix: new test; test_state_point_statement_defaults, test_native_scalar_codec, test_native_integer_binding, test_state_storage, test_state_upsert, test_state_bulk_update, test_state_bulk_update_expressions, test_state_schema_release, test_state_target_index_history, test_state_org_index_order, test_state_upgrade_progress.
- [x] PinnedPython /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python; PYTHONDONTWRITEBYTECODE=1; pytest -p no:cacheprovider --basetemp inside owned tempfile.TemporaryDirectory(dir=/tmp). Ruff --no-cache/in-memorycompile/diffcheck; update scoped native codec paragraphs docs/architecture.md/runtime-mechanism.md after tests and own claim/plan. Commit own files/hunks only.
- [x] Fresh independent SPEC then QUALITY, retaining the18 accepted delayed-clock witnesses and rechecking subclass/buffer/native constraint failures. Re-review unchanged mixed23 proposal after foundation acceptance.

No current State caller/constructor/version/progress/publication, actual DB, native CLI/provider/network, main, push or deploy is selected.

Root independent acceptance recorded in ../specs/2026-10-16-state-point-defaults-native-binding-acceptance.json: pointSPEC637 and18 delayed clock witnesses, then originalQUALITY P2 closed by16paired scalar rechecks after genericnative bindingSPEC793/QUALITY543inputs+3740Statecases+637crossfamily checks. Currentcaller/constructor/release unselected; mixed23 has its own separate re-review.
