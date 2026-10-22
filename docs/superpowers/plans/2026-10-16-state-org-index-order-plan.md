# State organization-index source-order amendment

> Required workflow: subagent-driven-development; fresh SPEC followed by QUALITY after implementation.

**Problem:** SPEC of the 27-method Store DAO proposal reproduced a native-order difference on both actual/fresh sources. Original nonunique organization indexes implicitly use the original org ROWID as the final key. Target State indexes currently use physical state.id. The maximum source ROWID allocator and explicit lower-rowid insertion can reverse these orders. Open-ID first-row lookup changes the returned user; name lookup ordered by nullable user_id changes equal-key encounter order.

**Scope:** Change only the two nonunique org State index declarations, the unselected release target fingerprint, a new regression module and scoped behavior documentation. Do not change unique source-PK indexes, the native source capture/metadata/hash, public DAO ordering, source/current callers or runtime/release selection. The original native index/order is the reference. This is a permanent foundation correction; do not sort a chosen user in a caller or invent a new uniqueness/ordering policy.

## Exact changes

In app/state_definitions.py replace the two complete index strings with:

```python
"create index idx_state_org_name on state(name,cast(legacy_key as integer)) where record_type='org_user_profiles'",
"create index idx_state_org_open_id on state(open_dingtalk_id,cast(legacy_key as integer)) where record_type='org_user_profiles'",
```

Both remain nonunique. No added field or index. The other18 indexes and table DDL are unchanged.

In app/state_schema_release.py replace only TARGET_HASH:

```python
TARGET_HASH = "327c94f68a9354f8766dfebb9ef7c85c9d03c2a4b27f95387eb88cc32fabc1f2"
```

Root computed this literal using the exact target SQLite declaration and existing _fingerprint(_source_declaration(db,"state")), on Python3.13.12 / SQLite3.50.4. Source hashes remain unchanged. The old ae25ae... target-only receipts are historical pre-amendment proof; a combined/final copied-data proof must use the new fingerprint.

## Complete new tests/test_state_org_index_order.py

```python
"""Native organization secondary-index ties use original SQLite ROWID."""
import pytest

from app.state_definitions import state_projection
from app.state_storage import insert_state
from tests.test_state_storage import native_insert, pair as pair, required


@pytest.mark.parametrize("field", ("open_dingtalk_id", "name"))
@pytest.mark.parametrize("allocation", ("reverse_explicit", "max_auto"))
def test_org_lookup_ties_follow_source_rowid(pair, field, allocation):
    source, target, variant = pair
    maximum = 2**63 - 1
    kind = "org_user_profiles"
    for position in range(2):
        values = required(variant, kind, str(position)) | {
            "user_id": None if field == "name" else str(position),
            "name": "duplicate-name", "open_dingtalk_id": "duplicate-open",
            "title": "old" if position == 0 else "new",
        }
        if position == 0:
            native_insert(source, kind, values, maximum)
            insert_state(target, kind, values, source_rowid=maximum)
        elif allocation == "reverse_explicit":
            native_insert(source, kind, values, 7)
            insert_state(target, kind, values, source_rowid=7)
        else:
            assert native_insert(source, kind, values).lastrowid < maximum
            assert insert_state(target, kind, values).lastrowid < maximum
    projection = "(" + state_projection(kind) + ")"
    suffix = " order by user_id" if field == "name" else ""
    value = "duplicate-name" if field == "name" else "duplicate-open"
    sql = "select title from {} where " + field + "=?" + suffix
    native = source.execute(sql.format(kind), (value,)).fetchall()
    current = target.execute(sql.format(projection), (value,)).fetchall()
    assert native == [("new",), ("old",)]
    assert current == native
    plan = target.execute(
        "explain query plan " + sql.format(projection), (value,)
    ).fetchall()
    index = "idx_state_org_name" if field == "name" else "idx_state_org_open_id"
    assert any(index in row[3] for row in plan)

```

## Root executable preflight

The exact new8-case module fails all8 with the original State index declarations. An owned temporary test-process overlay changes only the two declaration strings and release target hash, then runs the new module plus test_state_storage.py, test_state_upsert.py, test_state_schema_release.py, test_native_scalar_codec.py and test_config_state_native_source.py. **314 passed in17.47s**; Ruff passed. TemporaryDirectory under /tmp automatically removed and absence verified. One earlier invocation selected a nonexistent adjacent test path and exited before green collection; it is not passing evidence. No actual/copied source or runtime file was changed by preflight.

Coverage crosses actual/fresh, open-ID/name, reverse explicit/MAX-auto allocation. The MAX-auto source/target randomly generated numeric rowids need not be identical; both must lie below MAX and the selected identities/order must agree. Native and target query plans must use the respective nonunique lookup index. Existing unique source-PK constraints stay untouched.

- [x] Reproduce independent SPEC blocker; write complete correction and regression module before runtime file edits.
- [x] Root all8 RED, corrected314 GREEN, Ruff and own temporary cleanup.
- [x] Implement exact2 strings/hash/new8 cases; add scoped paragraphs in docs/architecture.md and docs/runtime-mechanism.md marking unselected foundation amendment.
- [x] Run narrow focused matrix; compile, Ruff, confirm native source metadata SHA604496... unchanged and only2 of20 index declarations changed.
- [x] Fresh SPEC314/24.40s then QUALITY314/29.11s; SPEC8 exact proposed org DAO counterexamples now match native. Full27-method DAO acceptance remains pending.
- [ ] Re-establish target-copy acceptance with this fingerprint during State atomic caller/publication integration. Existing pure source-row fact proof remains historical; do not claim current full-candidate acceptance.
