# Native SQLite integer-binding correction

> Required workflow: subagent-driven-development; fresh SPEC then fresh QUALITY before accepting this unit.

**Goal:** Remove the confirmed shared scalar codec discrepancy that blocks the State dispatcher conversion. Native sqlite3 binds the underlying PyLong value and rejects signed64 overflow even for int subclasses. Existing encoder tests type(value) is int only, then JSON accepts an out-of-range subclass and target affinity converts it to REAL.

**Decision:** Correct the shared encoder once. Do not add caller-specific SELECT/overflow branches. Process integer values before overloaded equality/infinity comparisons; use int.__int__ to inspect the underlying value, matching native binder behavior despite subclass __int__/comparison overrides. Return the original legal value to preserve published bool JSON representation. No new accepted types, scalar tag/format, field, schema, marker, policy, runtime caller or migration is introduced.

## Complete replacement function

Replace only _encoded_sqlite_value in app/action_storage.py. All other module AST/function bodies and published/native encoders remain unchanged.

```python
def _encoded_sqlite_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"t": "blob", "v": bytes(value).hex()}
    if isinstance(value, int):
        # sqlite3 binds the underlying PyLong, ignoring subclass conversions.
        if not -(2**63) <= int.__int__(value) < 2**63:
            raise OverflowError("Python int too large to convert to SQLite INTEGER")
        return value
    if isinstance(value, float) and math.isnan(value):
        # sqlite3 binds NaN as NULL before the source table applies NOT NULL.
        return None
    if value == float("inf"):
        return {"t": "inf"}
    if value == float("-inf"):
        return {"t": "-inf"}
    if value is None or isinstance(value, (str, float)):
        return value
    raise sqlite3.ProgrammingError("unsupported SQLite source value")
```

## Complete new tests/test_native_integer_binding.py

```python
"""JSON scalar integers must match the native sqlite3 parameter binder."""
from contextlib import closing
import json
import sqlite3

import pytest

from app import action_storage as codec
from app.state_storage import state_json_field, upsert_state
from tests.test_state_storage import facts, native_insert, pair as pair, required


class NativeInt(int):
    pass


class MaskedInt(int):
    def __int__(self):
        return 0

    def __eq__(self, other):
        return True

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return False


@pytest.mark.parametrize("integer", (int, NativeInt, MaskedInt))
@pytest.mark.parametrize("number", (-(2**63)-1, -(2**63), 0, 1, 2**63-1, 2**63))
def test_integer_codec_matches_native_binder(integer, number):
    value = integer(number)
    with closing(sqlite3.connect(":memory:")) as db:
        try:
            native = db.execute("select ?", (value,)).fetchone()[0]
        except OverflowError:
            for encode in (codec._encoded_sqlite_value, codec._encoded_native_sqlite_value):
                with pytest.raises(OverflowError):
                    encode(value)
            with pytest.raises(OverflowError):
                codec._native_payload({"owner_pid": value})
        else:
            assert native == number
            for encode in (codec._encoded_sqlite_value, codec._encoded_native_sqlite_value):
                assert json.loads(json.dumps(encode(value))) == native
            assert json.loads(codec._native_payload({"owner_pid": value})) == {"owner_pid": native}


@pytest.mark.parametrize("value", (False, True))
def test_published_boolean_json_representation_is_unchanged(value):
    assert codec._encoded_sqlite_value(value) is value
    assert codec._encoded_native_sqlite_value(value) is value
    assert json.loads(codec._native_payload({"value": value}))["value"] is value


@pytest.mark.parametrize("integer", (NativeInt, MaskedInt))
@pytest.mark.parametrize("number", (-(2**63)-1, 2**63))
def test_invalid_integer_lease_candidate_preserves_outer_work(pair, integer, number):
    source, target, variant = pair
    kind = "dispatcher_claim_leases"
    sentinel = {"key": "caller", "value": "pending"}
    native_insert(source, "service_state", sentinel)
    # The target prior write is owned by this same outer transaction.
    upsert_state(target, "service_state", sentinel, {"value": state_json_field("value")})
    values = required(variant, kind, "binding") | {"owner_pid": integer(number)}
    with pytest.raises(OverflowError):
        native_insert(source, kind, values)
    with pytest.raises(OverflowError):
        upsert_state(target, kind, values, {"owner_pid": state_json_field("owner_pid")})
    assert facts(source, kind) == facts(target, kind, True) == []
    assert facts(source, "service_state") == facts(target, "service_state", True)
    assert source.in_transaction and target.in_transaction
```

## Executable verification and integration boundary

- [x] Root native source reproduction and complete test-process proposal preflight: new28 cases yield16fail/12pass before correction; after correction new28 plus314 adjacent cases yield342pass in17.25s. Ruff passes; own /tmp TemporaryDirectory automatically removed and absence verified.
- [x] Implement exact function/new28 cases; run the same focused342 matrix, Ruff and plain diff check. Canonical code equality and every other module AST must remain unchanged.
- [x] Add scoped behavior paragraphs to docs/architecture.md and docs/runtime-mechanism.md in the implementation commit; mark shared correction only, no State runtime activation.
- [x] Fresh independent SPEC342/18.61s plus14 probes, then fresh QUALITY342/18.22s plus109 native-binding/16 JSON-byte/12 existing-lease probes.
- [x] After acceptance, remove the redundant db.execute("select ?", (generation,)) from the unselected dispatcher proposal only; source-native overflow now comes from shared codec before writer lock. Re-review the exact dispatcher proposal and regenerate its artifact hashes.
- [ ] Continue complete atomic State caller/constructor/version/progress/publication closure and full copied-data/genuine-old-code/native-CLI evidence before selecting the release.

Root preflight did not edit runtime/test files or any actual database. Source metadata remains immutable; no source retirement, provider/native/send, main/push/deploy or production action. Legal plain integers and booleans keep their existing encoded values; original finite REAL and published Reply format tests remain in the adjacent matrix. The canonical test distinguishes bound underlying integers from overloaded comparison/conversion methods using the real sqlite3 SELECT binder, and verifies invalid State lease candidates preserve prior caller writes and outer transaction ownership across both actual/fresh native DDL variants.

The unresolved denied cleanup /private/tmp/state-upsert-impl.694bXO remains untouched.

Acceptance:756a6c7e core and b4ff0720 trimmed unselected dispatcher proposal pass sequential independent review; receipt ../specs/2026-10-16-state-caller-readiness.json.
