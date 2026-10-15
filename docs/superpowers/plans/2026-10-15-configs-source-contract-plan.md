# Configs Source Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish an unselected, executable source behavior oracle for the 15 existing config families and all 118 captured fields before designing their target storage.

**Architecture:** Reconstruct both literal actual and published-fresh SQLite declarations from the accepted metadata capture, then exercise native DML and the genuine published `7dadc492` Store/Email callers on owned synthetic databases. The oracle records existing identity, affinity, constraint, mutation and transaction behavior; it does not create a target `configs` declaration or select a migration.

**Tech Stack:** Python `sqlite3`, pytest, the existing exact published-code archive fixture, `git show`, Ruff. Use only focused tests in this live development tree.

---

## Authority and boundaries

The approved ten-core-plus-ledger design is `docs/superpowers/specs/2026-10-11-database-redesign-design.md`. The field map is `docs/superpowers/specs/2026-10-11-database-redesign-field-map.csv`. The literal `actual` and `fresh` source is `docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json`, bound to published commit `7dadc492c1c62d5b0624f2f97dd0c89f4f358646` and Store `2026-10-13.5`/Email `51`. `tests/test_config_state_native_source.py` already proves six metadata/reconstruction cases and was independently accepted at `3e171e85`; do not recapture or rewrite it. `tests/test_provider_state_source.py` provides the adjacent source-oracle style. The source capture contains metadata, never actual config row contents.

The 15 names, in capture order, are `email_accounts`, `email_category_configs`, `email_category_description_revisions`, `email_category_folder_bindings`, `email_model_mode_transitions`, `email_model_promotion_configs`, `email_training_snapshots`, `managed_skill_revisions`, `managed_skills`, `runtime_feedback_iteration_capabilities`, `runtime_skill_bindings`, `runtime_skill_configs`, `scheduled_task_config_versions`, `scheduled_task_skill_refs`, `wechat_reply_scopes`. The existing 118 fields are already mapped to `configs`; this unit tests source facts, not a new field disposition. `scheduled_tasks` and `runs` are separate future closure members. `managed_skill_export_receipts` and `runtime_skill_load_receipts` are future `actions` children. The approved empty `email_training_snapshot_observations` FK/trigger retirement is a later release decision. None of these gets a replacement table under its existing name in this unit.

The current candidate has already translated the six source account blocker triggers to typed `actions` predicates in `app/action_email_storage.py:246-285`. Treat the six genuine `7dad` predicates as predecessor evidence and the candidate predicates as a separate future expectation. Never install the old blocker SQL over a current candidate database. No source capture edit, app code, target DDL, migration, Store/Email version, caller conversion, retention change, real database, provider, native Agent CLI, push, main merge or deploy belongs here.

## File map

| File | Responsibility |
| --- | --- |
| `tests/configs_source_fixtures.py` (new) | Open and close owned in-memory literal-source connections, execute captured table/index/trigger SQL, and insert synthetic rows by named fields. |
| `tests/test_configs_source_contract.py` (new) | Native DML identity, affinity, constraints, FK, immutability, caller and transaction contract. |
| `docs/database-redesign-config-state-source-preparation.md` | Append evidence after the tests run, distinguishing source oracle from target readiness. |
| `docs/agent-claims.md` | Claim exactly these files before editing and stage only the implementer's hunks. |

No `app/`, `scripts/`, existing tests, source-binding artifact or captured JSON/CSV is edited. The accepted `old_email_copy` fixture in `tests/test_native_copy_lifecycle_harness.py:72-96` provides the genuine `git archive 7dadc492` constructor for the archived public-call tests. It creates a private database and does not invoke a native Agent CLI. Register that fixture module through `pytest_plugins`, not a direct fixture import, to avoid Ruff F401/F811.

### Task 1: Literal fixture and source identity

**Files:** Create `tests/configs_source_fixtures.py`; create `tests/test_configs_source_contract.py`.

- [ ] **Step 1: Claim these two test files and their later documentation appendix.** Append a unique claim row to `docs/agent-claims.md` before edits. Stage only that row and the files this plan names.

- [ ] **Step 2: Write the first test, importing a fixture that does not yet exist.** Create `tests/test_configs_source_contract.py` with this complete initial content:

```python
"""Unselected native source behavior for the 15 config families."""
import pytest

from tests.configs_source_fixtures import CONFIGS, capture, source_db

pytest_plugins = ("test_native_copy_lifecycle_harness",)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_fifteen_config_identities_and_118_fields(variant):
    source = capture()
    assert tuple(source["groups"]["configs"]) == CONFIGS
    definitions = source[variant]["tables"]
    assert sum(len(definitions[name]["columns"]) for name in CONFIGS) == 118
    with source_db(variant) as db:
        assert [name for name in CONFIGS if definitions[name]["identity"]["autoincrement"]] == [
            "managed_skill_revisions", "managed_skills", "runtime_skill_configs",
        ]
        assert [name for name in CONFIGS if definitions[name]["identity"]["without_rowid"]] == [
            "email_category_description_revisions", "email_model_mode_transitions",
            "email_model_promotion_configs",
        ]
        for name in CONFIGS:
            expected = [column["name"] for column in definitions[name]["columns"]]
            assert [column[1] for column in db.execute(f"pragma table_xinfo({name})")] == expected
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
```

- [ ] **Step 3: Run the RED check.** Run `python -m pytest -q tests/test_configs_source_contract.py::test_fifteen_config_identities_and_118_fields`. Expected: collection fails with `ModuleNotFoundError: No module named 'tests.configs_source_fixtures'`.

- [ ] **Step 4: Add the literal-only fixture.** Create `tests/configs_source_fixtures.py` with this complete content. It executes no constructor, fabricated parent or target DDL. The 34 captured tables include the genuine `scheduled_tasks` parent. The external runtime receipt is exercised through the archived constructor in Task 3. Task 3's six account-blocker predicate comparison is declaration-only; the separately existing focused source DML gate reconstructs the captured Email action parents/children and all 13 external triggers with narrow account/message/classification stubs.

```python
"""Owned SQLite fixtures from the frozen actual/published-fresh source DDL."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json"
CONFIGS = (
    "email_accounts", "email_category_configs",
    "email_category_description_revisions", "email_category_folder_bindings",
    "email_model_mode_transitions", "email_model_promotion_configs",
    "email_training_snapshots", "managed_skill_revisions", "managed_skills",
    "runtime_feedback_iteration_capabilities", "runtime_skill_bindings",
    "runtime_skill_configs", "scheduled_task_config_versions",
    "scheduled_task_skill_refs", "wechat_reply_scopes",
)


def capture():
    return json.loads(CAPTURE.read_text())


@contextmanager
def source_db(variant):
    assert variant in ("actual", "fresh")
    tables = capture()[variant]["tables"]
    db = sqlite3.connect(":memory:")
    try:
        db.execute("pragma foreign_keys=on")
        for spec in tables.values():
            db.execute(spec["sql"])
        for spec in tables.values():
            for index in reversed(spec["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in spec["triggers"]:
                db.execute(trigger["sql"])
        yield db
    finally:
        db.close()


def insert(db, table, values, *, ignore=False):
    assert table in CONFIGS
    columns = tuple(values)
    return db.execute(
        f"insert {'or ignore ' if ignore else ''}into {table} "
        f"({','.join(columns)}) values ({','.join('?' for _ in columns)})",
        tuple(values.values()),
    )
```

- [ ] **Step 5: Run the GREEN check and commit the self-contained fixture checkpoint.** Run `python -m pytest -q tests/test_configs_source_contract.py::test_fifteen_config_identities_and_118_fields tests/test_config_state_native_source.py`. Expected: the two parameterized cases plus the six accepted metadata cases pass. Run `ruff check tests/configs_source_fixtures.py tests/test_configs_source_contract.py` and `git diff --check`. Commit only the two new test files and the claim row with `git add tests/configs_source_fixtures.py tests/test_configs_source_contract.py docs/agent-claims.md` then `git commit -m "test: reconstruct configs source behavior fixture"`.

### Task 2: Native keys, allocator and constraints

**Files:** Modify `tests/test_configs_source_contract.py` only. Append the following complete tests after Task 1's function. Run each named node as RED immediately after adding it if its assertion fails; fix an inaccurate expectation against the frozen literal SQL, not by changing the capture or native helper. Then rerun as GREEN. The precise expected behaviors below follow the captured declarations.

- [ ] **Step 1: Add `import sqlite3` and `insert` to the existing fixture import, then add identity and affinity cases.** The fixture import becomes `from tests.configs_source_fixtures import CONFIGS, capture, insert, source_db`.

```python
@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_text_keys_keep_source_null_and_binary_rules(variant):
    with source_db(variant) as db:
        account = {
            "account_id": None, "display_name": "A", "email_address": "a@example.invalid",
            "imap_host": "imap", "imap_port": "993", "imap_tls": "1",
            "imap_username": "a", "imap_secret_reference": "secret-ref",
            "smtp_host": "smtp", "smtp_port": 465, "smtp_tls": 1,
            "smtp_username": "a", "smtp_secret_reference": "secret-ref",
            "enabled": 1, "scan_folders_json": "[]", "scan_interval_seconds": 60,
            "created_at": "t0", "updated_at": "t0",
        }
        insert(db, "email_accounts", account)
        insert(db, "email_accounts", account | {"email_address": "b@example.invalid"})
        assert db.execute("select count(*) from email_accounts where account_id is null").fetchone() == (2,)
        assert db.execute("select typeof(imap_port),typeof(imap_tls) from email_accounts limit 1").fetchone() == ("integer", "integer")
        insert(db, "email_accounts", account | {"account_id": "Account"})
        insert(db, "email_accounts", account | {"account_id": "account"})
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_accounts", account | {"account_id": "Account"})
        insert(db, "email_category_configs", {
            "category_key": None, "display_name": "A", "core_description": "A",
            "include_json": '["a"]', "exclude_json": '["b"]', "threshold": 0.5,
            "actions_json": "[]", "action_parameters_json": "{}", "enabled": 1,
            "description_version": "d1", "config_version": "c1", "updated_at": "t0",
        })
        assert db.execute("select category_key from email_category_configs").fetchone() == (None,)
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_configs", {
                "category_key": " ", "display_name": "A", "core_description": "A",
                "include_json": '["a"]', "exclude_json": '["b"]', "threshold": 0.5,
                "actions_json": "[]", "action_parameters_json": "{}", "enabled": 1,
                "description_version": "d1", "config_version": "c1", "updated_at": "t0",
            })


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_three_autoincrement_sequences_and_three_without_rowid_tables(variant):
    with source_db(variant) as db:
        for table, columns in (
            ("managed_skills", {"name": "s", "display_name": "S"}),
            ("managed_skill_revisions", {"skill_id": 700, "revision_number": 1,
                                         "content": "x", "sha256": "digest", "source": "test"}),
            ("runtime_skill_configs", {"status": "pending_restart"}),
        ):
            # The revision's FK parent is inserted first; explicit skill id 700 is valid.
            if table == "managed_skills":
                insert(db, table, {"id": 700, **columns})
                db.execute("delete from managed_skills where id=700")
                insert(db, table, {"id": 700, **columns})
            else:
                if table == "managed_skill_revisions":
                    columns = {**columns, "skill_id": 700}
                insert(db, table, {"id": 900, **columns})
                if table == "runtime_skill_configs":
                    # Its immutable delete guard makes the high-water test use
                    # INSERT OR IGNORE on a failed CHECK instead of deletion.
                    assert insert(db, table, {"id": 901, "status": "bad"}, ignore=True).rowcount == 0
                else:
                    assert insert(db, table, {"id": 901, **columns, "revision_number": -1}, ignore=True).rowcount == 0
            assert db.execute("select seq from sqlite_sequence where name=?", (table,)).fetchone()[0] >= (901 if table != "managed_skills" else 700)
        for table in ("email_category_description_revisions", "email_model_mode_transitions", "email_model_promotion_configs"):
            with pytest.raises(sqlite3.OperationalError, match="no such column: rowid"):
                db.execute(f"select rowid from {table}").fetchall()
```

The allocator case must be checked against native behavior during RED/GREEN. In particular, `managed_skill_revisions` has immutable deletion, so no test may bypass that trigger. If SQLite's ignored `CHECK` changes `sqlite_sequence` on the installed SQLite version, assert the observed native high water and keep both actual/fresh variants equal; do not turn it into a proposed target allocator policy. The first `managed_skills` explicit delete/reinsert is permitted only before a revision references that ID.

- [ ] **Step 2: Run the two nodes and reconcile any native version result.** Run `python -m pytest -q tests/test_configs_source_contract.py::test_native_text_keys_keep_source_null_and_binary_rules tests/test_configs_source_contract.py::test_three_autoincrement_sequences_and_three_without_rowid_tables`. Expected after reconciliation: four parameterized cases pass. Keep literal `sqlite_sequence` and `WITHOUT ROWID` evidence in assertions.

- [ ] **Step 3: Add representative unique/CHECK/FK/cascade tests.** The exact row values below are synthetic and exercise the captured 15-table names; no replacement parent table is created.

```python
@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_category_binding_foreign_keys_and_cascades(variant):
    with source_db(variant) as db:
        values = {"account_id": "acct", "display_name": "A", "email_address": "a@example.invalid",
                  "imap_host": "imap", "imap_port": 993, "imap_tls": 1,
                  "imap_username": "a", "imap_secret_reference": "ref", "smtp_host": "smtp",
                  "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
                  "smtp_secret_reference": "ref", "enabled": 1, "scan_folders_json": "[]",
                  "scan_interval_seconds": 60, "created_at": "t", "updated_at": "t"}
        category = {"category_key": "legal", "display_name": "Legal",
                    "core_description": "Legal", "include_json": '["legal"]',
                    "exclude_json": '["other"]', "threshold": 0.5,
                    "actions_json": "[]", "action_parameters_json": "{}",
                    "enabled": 1, "description_version": "d1", "config_version": "c1",
                    "updated_at": "t"}
        binding = {"account_id": "acct", "category_key": "legal",
                   "provider_folder_id": "folder", "provider_folder_name": "Folder",
                   "binding_status": "active", "last_verified_at": "t"}
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding)
        insert(db, "email_accounts", values)
        insert(db, "email_category_configs", category)
        insert(db, "email_category_folder_bindings", binding)
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding)
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding | {"account_id": "missing"})
        db.execute("delete from email_category_configs where category_key='legal'")
        assert db.execute("select count(*) from email_category_folder_bindings").fetchone() == (0,)
        assert db.execute("pragma foreign_key_check").fetchall() == []
```

- [ ] **Step 3a: Add direct invalid-child and uniqueness cases.** This gives DML coverage for the captured internal FK families without inventing external parent declarations; the larger valid Skill/runtime chain is in Task 3.

```python
@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_internal_fk_and_unique_rejections(variant):
    with source_db(variant) as db:
        missing_parents = (
            ("managed_skill_revisions", {"skill_id": 404, "revision_number": 1,
                                         "content": "x", "sha256": "h", "source": "test"}),
            ("runtime_feedback_iteration_capabilities", {"config_id": 404, "enabled": 1}),
            ("runtime_skill_bindings", {"config_id": 404, "skill_id": 404,
                                        "revision_id": 404, "enabled": 1, "load_order": 0}),
            ("scheduled_task_skill_refs", {"scheduled_task_id": 404,
                                           "skill_source": "managed", "skill_name": "x",
                                           "managed_skill_id": 404,
                                           "managed_revision_id": 404, "position": 0}),
            ("email_model_mode_transitions", {"request_id": "r", "actor": "a",
                                              "from_mode": "agent_primary", "to_mode": "model_primary",
                                              "promotion_config_version": "missing",
                                              "status": "applied", "reason": "", "created_at": "t"}),
        )
        for table, values in missing_parents:
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
                insert(db, table, values)
        insert(db, "scheduled_task_config_versions", {"snapshot_json": '{"a":1}'})
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            insert(db, "scheduled_task_config_versions", {"snapshot_json": '{"a":1}'})
        # Native source accepts any TEXT body here; JSON validation is a caller contract.
        insert(db, "scheduled_task_config_versions", {"snapshot_json": "invalid-json"})
        scope = {"account_id": "a", "target_type": "direct", "target_id": "person",
                 "display_name": "Person", "trigger_mode": "every_inbound_text"}
        insert(db, "wechat_reply_scopes", scope)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            insert(db, "wechat_reply_scopes", scope)
        insert(db, "wechat_reply_scopes", scope | {"target_type": "group"})
        assert db.execute("select count(*) from wechat_reply_scopes").fetchone() == (2,)
        assert db.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_category_and_binding_checks(variant):
    with source_db(variant) as db:
        base = {"category_key": "legal", "display_name": "Legal",
                "core_description": "Legal", "include_json": '["legal"]',
                "exclude_json": '["other"]', "threshold": 0.5,
                "actions_json": "[]", "action_parameters_json": "{}",
                "enabled": 1, "description_version": "d1", "config_version": "c1",
                "updated_at": "t"}
        for changed in ({"include_json": "[]"}, {"exclude_json": "{}"},
                        {"threshold": 1.1}, {"enabled": 2},
                        {"actions_json": "bad"}):
            with pytest.raises(sqlite3.IntegrityError):
                insert(db, "email_category_configs", base | changed)
        insert(db, "email_category_configs", base)
        account = {"account_id": "acct", "display_name": "A",
                   "email_address": "a@example.invalid", "imap_host": "imap",
                   "imap_port": 993, "imap_tls": 1, "imap_username": "a",
                   "imap_secret_reference": "ref", "smtp_host": "smtp",
                   "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
                   "smtp_secret_reference": "ref", "enabled": 1,
                   "scan_folders_json": "[]", "scan_interval_seconds": 60,
                   "created_at": "t", "updated_at": "t"}
        insert(db, "email_accounts", account)
        binding = {"account_id": "acct", "category_key": "legal",
                   "provider_folder_id": "", "provider_folder_name": "Folder",
                   "binding_status": "active", "last_verified_at": "t"}
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding)
        insert(db, "email_category_folder_bindings", binding | {"binding_status": "missing"})
```

The `email_model_mode_transitions` FK declares `ON DELETE RESTRICT`, while the promotion parent's immutable-delete trigger blocks a real deletion first. The next task asserts both facts. The managed revision and runtime binding cases in Task 3 exercise both unique indexes for each family. The archived scheduled caller below exercises a valid positional Skill reference and its duplicate-position rejection.

- [ ] **Step 4: Run focused GREEN and commit.** Run `python -m pytest -q tests/test_configs_source_contract.py tests/test_config_state_native_source.py`, then `ruff check tests/configs_source_fixtures.py tests/test_configs_source_contract.py` and `git diff --check`. Expected: all focused source cases pass. Commit only `tests/test_configs_source_contract.py` with `git add tests/test_configs_source_contract.py` and `git commit -m "test: pin native configs source constraints"`.

### Task 3: Immutable exception, receipt match and source caller semantics

**Files:** Modify `tests/test_configs_source_contract.py`. Use `old_email_copy` from `tests/test_native_copy_lifecycle_harness.py`; it constructs a genuine published `7dad` Store/Email database in `tmp_path`. The subprocess working directory is `path.parent / "published-old"`, with `PYTHONPATH` set to that archive. Public method signatures verified in published source: `EmailStore.update_category_descriptions(category_key, *, core_description, include, exclude, threshold, enabled, description_version, config_version, expected_current_version=None)`; `AutoReplyStore.create_runtime_skill_config(bindings, *, expected_parent_id, feedback_iteration_enabled=None)`; `AutoReplyStore.create_scheduled_task(*, name, cron_expression, timezone_name, ...)`; `AutoReplyStore.create_scheduled_task_run(task_id, *, trigger_kind, scheduled_for, ...)`; `AutoReplyStore.advance_wechat_scope_watermark(account_id,target_type,target_id,sent_at)`.

- [ ] **Step 1: Add `import json` to the test imports, then add native immutable exceptions, uniqueness and the receipt match test.** Append these complete functions. The receipt table is supplied by `old_email_copy`'s genuine archived constructor, never fabricated under its existing name.

```python
@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_immutable_histories_and_snapshot_freeze_exception(variant):
    with source_db(variant) as db:
        insert(db, "email_category_description_revisions", {
            "revision_id": "rev", "category_key": "legal", "config_version": "c1",
            "config_json": "{}", "created_at": "t"})
        insert(db, "email_model_promotion_configs", {
            "config_version": "promote", "micro_f1_min": 0.8,
            "category_precision_min": 0.8, "category_validation_samples_min": 1,
            "p95_latency_max_ms": 100.0, "created_at": "t"})
        insert(db, "email_model_mode_transitions", {
            "request_id": "request", "actor": "tester",
            "from_mode": "agent_primary", "to_mode": "model_primary",
            "promotion_config_version": "promote", "status": "applied",
            "reason": "", "created_at": "t"})
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            insert(db, "email_category_description_revisions", {
                "revision_id": None, "category_key": "legal", "config_version": "c2",
                "config_json": "{}", "created_at": "t"})
        for table, key, value, column in (
            ("email_category_description_revisions", "revision_id", "rev", "config_json"),
            ("email_model_promotion_configs", "config_version", "promote", "created_at"),
            ("email_model_mode_transitions", "request_id", "request", "reason"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable model control history"):
                db.execute(f"update {table} set {column}={column} where {key}=?", (value,))
            with pytest.raises(sqlite3.IntegrityError, match="immutable model control history"):
                db.execute(f"delete from {table} where {key}=?", (value,))
        promotion_fk = capture()[variant]["tables"]["email_model_mode_transitions"]["foreign_keys"]
        assert promotion_fk[0]["on_delete"] == "RESTRICT"
        snapshot = {"snapshot_id": "snap", "snapshot_version": "v1",
                    "description_version": "d1", "input_schema_version": "i1", "seed": 0,
                    "observed_at": "t", "snapshot_digest": "a" * 64,
                    "manifest_json": "{}", "frozen": 0, "created_at": "t"}
        insert(db, "email_training_snapshots", snapshot)
        insert(db, "email_training_snapshots", snapshot | {
            "snapshot_id": None, "snapshot_digest": "b" * 64})
        insert(db, "email_training_snapshots", snapshot | {
            "snapshot_id": None, "snapshot_digest": "c" * 64})
        assert db.execute("select count(*) from email_training_snapshots where snapshot_id is null").fetchone() == (2,)
        db.execute("update email_training_snapshots set frozen=1 where snapshot_id='snap'")
        assert db.execute("select frozen from email_training_snapshots").fetchone() == (1,)
        for sql in (
            "update email_training_snapshots set frozen=0 where snapshot_id='snap'",
            "update email_training_snapshots set folder_label_watermark=1 where snapshot_id='snap'",
            "delete from email_training_snapshots where snapshot_id='snap'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="email training snapshot is immutable"):
                db.execute(sql)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_managed_revision_and_runtime_binding_unique_immutable_rules(variant):
    with source_db(variant) as db:
        skill = insert(db, "managed_skills", {"name": "contract", "display_name": "Contract"}).lastrowid
        revision = insert(db, "managed_skill_revisions", {
            "skill_id": skill, "revision_number": 1, "content": "body",
            "sha256": "digest", "source": "test"}).lastrowid
        for changed in ({"revision_number": 1, "sha256": "other"},
                        {"revision_number": 2, "sha256": "digest"}):
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
                insert(db, "managed_skill_revisions", {
                    "skill_id": skill, "content": "body", "source": "test", **changed})
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            insert(db, "managed_skill_revisions", {
                "skill_id": skill, "revision_number": 2, "content": "body2",
                "sha256": "digest2", "parent_revision_id": 404, "source": "test"})
        child_revision = insert(db, "managed_skill_revisions", {
            "skill_id": skill, "revision_number": 2, "content": "body2",
            "sha256": "digest2", "parent_revision_id": revision,
            "source": "test"}).lastrowid
        assert db.execute("select parent_revision_id from managed_skill_revisions where id=?", (child_revision,)).fetchone() == (revision,)
        with pytest.raises(sqlite3.IntegrityError, match="managed Skill revisions are immutable"):
            db.execute("update managed_skill_revisions set content=content where id=?", (revision,))
        with pytest.raises(sqlite3.IntegrityError, match="managed Skill revisions are immutable"):
            db.execute("delete from managed_skill_revisions where id=?", (revision,))
        config = insert(db, "runtime_skill_configs", {"status": "pending_restart"}).lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            insert(db, "runtime_skill_configs", {"parent_id": 404, "status": "pending_restart"})
        child_config = insert(db, "runtime_skill_configs", {
            "parent_id": config, "status": "pending_restart"}).lastrowid
        assert db.execute("select parent_id from runtime_skill_configs where id=?", (child_config,)).fetchone() == (config,)
        binding = {"config_id": config, "skill_id": skill, "revision_id": revision,
                   "enabled": 1, "load_order": 0}
        insert(db, "runtime_skill_bindings", binding)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            insert(db, "runtime_skill_bindings", binding)
        second = insert(db, "managed_skills", {"name": "second", "display_name": "Second"}).lastrowid
        second_revision = insert(db, "managed_skill_revisions", {
            "skill_id": second, "revision_number": 1, "content": "body2",
            "sha256": "digest2", "source": "test"}).lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            insert(db, "runtime_skill_bindings", binding | {
                "skill_id": second, "revision_id": second_revision})
        insert(db, "runtime_feedback_iteration_capabilities", {
            "config_id": config, "enabled": 1})
        for table, where in (
            ("runtime_skill_bindings", f"config_id={config}"),
            ("runtime_feedback_iteration_capabilities", f"config_id={config}"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"update {table} set enabled=enabled where {where}")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"delete from {table} where {where}")


def test_runtime_activation_requires_exact_genuine_load_receipt(old_email_copy):
    _repo, path = old_email_copy
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=on")
        skill = db.execute("insert into managed_skills(name,display_name) values('receipt-contract','Receipt')").lastrowid
        revision = db.execute("insert into managed_skill_revisions(skill_id,revision_number,content,sha256,source) values(?,1,'body','exact-digest','test')", (skill,)).lastrowid
        config = db.execute("insert into runtime_skill_configs(status) values('pending_restart')").lastrowid
        db.execute("insert into runtime_skill_bindings(config_id,skill_id,revision_id,enabled,load_order) values(?,?,?,?,0)", (config, skill, revision, 1))
        for loaded in (None, {str(skill): "wrong"}, {str(skill): "exact-digest", "999": "extra"}):
            if loaded is not None:
                db.execute("insert into runtime_skill_load_receipts(config_id,pid,loaded_json,error) values(?,?,?,'')", (config, 7, json.dumps(loaded)))
            with pytest.raises(sqlite3.IntegrityError, match="runtime Skill activation requires matching load receipt"):
                db.execute("update runtime_skill_configs set status='active' where id=?", (config,))
        db.execute("insert into runtime_skill_load_receipts(config_id,pid,loaded_json,error) values(?,?,?,'')", (config, 7, json.dumps({str(skill): "exact-digest"})))
        db.execute("update runtime_skill_configs set status='active' where id=?", (config,))
        with pytest.raises(sqlite3.IntegrityError, match="runtime Skill activation requires matching load receipt"):
            db.execute("update runtime_skill_configs set status='pending_restart' where id=?", (config,))
        failed = db.execute("insert into runtime_skill_configs(status) values('pending_restart')").lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="runtime Skill activation requires matching load receipt"):
            db.execute("update runtime_skill_configs set status='load_failed' where id=?", (failed,))
        db.execute("insert into runtime_skill_load_receipts(config_id,pid,loaded_json,error) values(?,7,'{}','failure')", (failed,))
        db.execute("update runtime_skill_configs set status='load_failed' where id=?", (failed,))
        assert db.execute("select status from runtime_skill_configs where id=?", (failed,)).fetchone() == ("load_failed",)
```

- [ ] **Step 2: Add the archived public caller test as a single subprocess with JSON output.** The code block below is the complete runner shape; use the existing `old_email_copy` fixture and append it to the new test file. The archived code is imported only in the subprocess, so current unpublished candidate classes cannot silently rewrite the old schema.

```python
def test_published_category_cas_and_scheduled_intern(old_email_copy):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    _repo, db_path = old_email_copy
    old_root = Path(db_path).parent / "published-old"
    script = r'''
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.agent_cron.models import ScheduledTaskSkillRef
from app.email_store import EmailFolderBindingConflict, EmailStore
from app.store import AutoReplyStore
from app.scheduled_config_storage import intern_scheduled_config, migrate_scheduled_configs
from app.wechat.models import WechatReplyScope
p = Path(sys.argv[1]); email = EmailStore(p); store = AutoReplyStore(p)
with sqlite3.connect(p) as db:
    row = db.execute("select category_key,core_description,include_json,exclude_json,threshold,config_version from email_category_configs order by category_key limit 1").fetchone()
key, description, include_json, exclude_json, threshold, before = row
changed = email.update_category_descriptions(
    key, core_description=description, include=json.loads(include_json),
    exclude=json.loads(exclude_json), threshold=threshold, enabled=False,
    description_version="source-contract-d2", config_version="source-contract-c2",
    expected_current_version=before)
assert changed["config_version"] == "source-contract-c2"
try:
    email.update_category_descriptions(
        key, core_description=description, include=json.loads(include_json),
        exclude=json.loads(exclude_json), threshold=threshold, enabled=False,
        description_version="source-contract-d3", config_version="source-contract-c3",
        expected_current_version=before)
except EmailFolderBindingConflict as exc:
    assert str(exc) == "category config changed"
else:
    raise AssertionError("stale category version was accepted")
skill = store.create_managed_skill("source-contract", "Source Contract")
revision = store.create_managed_skill_revision(
    skill.id,
    "---\nname: source-contract\ndescription: Test\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n# Test\n",
    source="settings")
with sqlite3.connect(p) as db:
    latest = db.execute("select id from runtime_skill_configs order by id desc limit 1").fetchone()
parent = latest[0] if latest else None
runtime = store.create_runtime_skill_config({skill.id: revision.id}, expected_parent_id=parent)
try:
    store.create_runtime_skill_config({}, expected_parent_id=parent)
except ValueError as exc:
    assert str(exc) == "runtime Skill configuration parent conflict"
else:
    raise AssertionError("stale runtime Skill parent was accepted")
try:
    store.record_runtime_skill_load(runtime.id, pid=7, loaded={skill.id: "wrong"})
except ValueError as exc:
    assert str(exc) == "runtime Skill load receipt does not match bindings"
else:
    raise AssertionError("mismatched runtime Skill load was accepted")
store.record_runtime_skill_load(runtime.id, pid=7, loaded={skill.id: revision.sha256})
assert store.get_runtime_skill_config(runtime.id).status == "active"
ref = ScheduledTaskSkillRef(skill_source="managed", skill_name=skill.name,
    managed_skill_id=skill.id, managed_revision_id=revision.id, position=0)
task = store.create_scheduled_task(
    name="source contract", prompt="Use $source-contract for a local fixture.",
    cron_expression="0 0 20 * * *", timezone_name="UTC", runtime_id="codex_oauth",
    runtime_options={"model": "gpt-5.5", "reasoning_effort": "high"},
    working_directory="/tmp/ceo-agent-service", skill_refs=(ref,))
now = datetime(2026, 10, 15, tzinfo=timezone.utc)
first = store.create_scheduled_task_run(task.id, trigger_kind="manual", scheduled_for=now)
second = store.create_scheduled_task_run(task.id, trigger_kind="manual", scheduled_for=now + timedelta(minutes=1))
with sqlite3.connect(p) as db:
    a, b = db.execute("select snapshot_id from runs where id in (?,?) order by id", (first.id, second.id)).fetchall()
    same = a[0] == b[0]
    internal = intern_scheduled_config(db, '{"source-contract":1}')
    repeated = intern_scheduled_config(db, '{"source-contract":1}')
    distinct = intern_scheduled_config(db, '{"source-contract":2}')
    assert internal == repeated and internal != distinct
    assert db.execute("select count(*) from scheduled_task_config_versions where id=?", (a[0],)).fetchone() == (1,)
    assert db.execute("select managed_skill_id,managed_revision_id,position from scheduled_task_skill_refs where scheduled_task_id=?", (task.id,)).fetchone() == (skill.id, revision.id, 0)
    try:
        db.execute("insert into scheduled_task_skill_refs(scheduled_task_id,skill_source,skill_name,managed_skill_id,managed_revision_id,position) values(?,?,?,?,?,0)", (task.id, "managed", skill.name, skill.id, revision.id))
    except sqlite3.IntegrityError as exc:
        assert "UNIQUE constraint failed" in str(exc)
    else:
        raise AssertionError("duplicate scheduled Skill position was accepted")
    old_snapshot = intern_scheduled_config(db, '{"name":"old"}')
    db.execute("update runs set snapshot_id=? where id=?", (old_snapshot, first.id))
    migrate_scheduled_configs(db)
    repointed = db.execute("select snapshot_id from runs where id=?", (first.id,)).fetchone()[0]
    assert repointed != old_snapshot
    assert db.execute("select count(*) from scheduled_task_config_versions where id=?", (old_snapshot,)).fetchone() == (0,)
    normalized = db.execute("select snapshot_json from scheduled_task_config_versions where id=?", (repointed,)).fetchone()[0]
    assert json.loads(normalized) == {"name": "old", "description": "old",
                                      "required_runtime_capabilities": [], "command": ""}
    assert db.execute("pragma foreign_key_check").fetchall() == []
with sqlite3.connect(p) as db:
    db.execute("pragma foreign_keys=on")
    original = db.execute("select snapshot_id from runs where id=?", (first.id,)).fetchone()[0]
    db.execute("begin immediate")
    candidate = intern_scheduled_config(db, '{"rollback":1}')
    db.execute("update runs set snapshot_id=? where id=?", (candidate, first.id))
    try:
        db.execute("insert into scheduled_task_config_versions(snapshot_json) values(?)", ('{"rollback":1}',))
    except sqlite3.IntegrityError:
        db.rollback()
    else:
        raise AssertionError("duplicate scheduled snapshot was accepted")
    assert db.execute("select snapshot_id from runs where id=?", (first.id,)).fetchone() == (original,)
    assert db.execute("select count(*) from scheduled_task_config_versions where id=?", (candidate,)).fetchone() == (0,)
scope = WechatReplyScope(account_id="acct", target_type="direct", target_id="person",
    display_name="Person", trigger_mode="every_inbound_text")
store.replace_wechat_reply_scopes("acct", [scope])
assert store.advance_wechat_scope_watermark("acct", "direct", "person", "2099-01-02T00:00:00+00:00")
assert not store.advance_wechat_scope_watermark("acct", "direct", "person", "2099-01-01T00:00:00+00:00")
with sqlite3.connect(p) as db:
    assert db.execute("select last_discovered_at from wechat_reply_scopes where account_id='acct' and target_type='direct' and target_id='person'").fetchone() == ("2099-01-02T00:00:00+00:00",)
print(json.dumps({"cas": changed["config_version"], "shared_snapshot": same,
                  "intern_same": internal == repeated, "intern_distinct": internal != distinct,
                  "runtime_active": store.get_runtime_skill_config(runtime.id).status == "active",
                  "wechat_watermark": True}))
'''
    proc = subprocess.run(
        [sys.executable, "-c", script, str(db_path)], cwd=old_root,
        env={**os.environ, "PYTHONPATH": str(old_root)}, capture_output=True,
        text=True, check=True, timeout=300,
    )
    assert json.loads(proc.stdout) == {
        "cas": "source-contract-c2", "shared_snapshot": True,
        "intern_same": True, "intern_distinct": True,
        "runtime_active": True, "wechat_watermark": True,
    }
```

The `pytest_plugins` registration from Task 1 supplies `old_email_copy` to both archived tests without an unused or duplicate import. The block above verifies the caller-owned `BEGIN IMMEDIATE` group: the new snapshot and its temporary run reference both disappear on rollback. The later target plan must retain that atomic boundary.

- [ ] **Step 3: Pin the six predecessor blocker owners and current candidate predicate boundary.** Add `from app.action_email_storage import finite_target_external_sql` to the new test's imports, then append this complete test. It compares declaration predicates only; it neither reinstalls old trigger SQL nor claims a target configs implementation.

```python
def test_six_account_blockers_have_distinct_old_and_current_action_owners():
    expected = {
        "trg_email_direct_action_blocks_account_delete": ("email_actions", "processing", "email_direct_action_in_flight"),
        "trg_email_direct_action_blocks_account_update": ("email_actions", "processing", "email_direct_action_in_flight"),
        "trg_email_unsubscribe_blocks_account_delete": ("email_unsubscribe_claims", "dispatching", "email_unsubscribe_write_in_flight"),
        "trg_email_unsubscribe_blocks_account_update": ("email_unsubscribe_claims", "dispatching", "email_unsubscribe_write_in_flight"),
        "trg_email_reply_dispatch_blocks_account_delete": ("email_reply_dispatch_claims", "dispatching", "email_reply_dispatch_in_flight"),
        "trg_email_reply_dispatch_blocks_account_update": ("email_reply_dispatch_claims", "dispatching", "email_reply_dispatch_in_flight"),
    }
    current = finite_target_external_sql()
    for variant in ("actual", "fresh"):
        captured = {trigger["name"]: trigger["sql"] for trigger in
                    capture()[variant]["tables"]["email_accounts"]["triggers"]}
        assert set(captured) == set(expected)
        for name, (old_child, status, error) in expected.items():
            assert f"from {old_child}" in captured[name]
            assert f"status='{status}'" in captured[name]
            assert error in captured[name]
            assert "on main.email_accounts" in current[name]
            assert "from main.actions a" in current[name]
            assert f"a.record_type='{old_child}'" in current[name]
            assert f"a.email_status='{status}'" in current[name]
            assert error in current[name]
```

- [ ] **Step 4: Run scoped tests and commit.** Run `python -m pytest -q tests/test_configs_source_contract.py tests/test_config_state_native_source.py tests/test_provider_state_source.py tests/test_storage_scheduled_configs.py`. Expected: source contracts and adjacent scheduling/provider checks pass. Also run `python -m pytest -q tests/test_email_action_children_source.py::test_each_external_in_flight_trigger_blocks_source_mutation`; its 13 trigger cases over captured `actual` and `fresh_candidate` Email action declarations yield 26 source DML checks, including the six account update/delete blockers. That test uses narrow account/message/classification stubs and is a predecessor-behavior gate, not a genuine full constructor or new configs target test. Run `ruff check tests/configs_source_fixtures.py tests/test_configs_source_contract.py`, `python -m compileall -q tests/configs_source_fixtures.py tests/test_configs_source_contract.py`, and `git diff --check`. Commit only the new test file with `git add tests/test_configs_source_contract.py` and `git commit -m "test: pin configs caller and immutable source behavior"`.

### Task 4: Documentation and acceptance handoff

**Files:** Append `docs/database-redesign-config-state-source-preparation.md`; update own claim row in `docs/agent-claims.md`.

- [ ] **Step 1: Append the actual test command, count and outcome.** Say explicitly that all 118 fields have accepted literal actual/fresh metadata coverage, while this unselected oracle behaviorally samples named native constraints and callers through synthetic connections and a genuine `7dad` archived constructor. Record the separate 26-case predecessor blocker DML gate and its narrow-stub scope, plus any native-version-specific allocator result. Do not report universal DML proof for every field, target parity, migration/inverse, actual-copy or production readiness.
- [ ] **Step 2: Record the next atomic closure as future work:** all 15 config families, `scheduled_tasks` and `runs.snapshot_id`/`runs.scheduled_task_id`, `scheduled_task_skill_refs`, `managed_skill_export_receipts` and `runtime_skill_load_receipts` converted to typed `actions` references, the approved empty `email_training_snapshot_observations` FK/trigger retirement, and the six account blocker predicates against current typed `actions`. Distinguish each from this source-only test commit. No new version, audit rule or retention policy is proposed.
- [ ] **Step 3: Run `git diff --check` and a literal placeholder scan:** `rg -n 'TBD|TODO|implement later|fill in details|Add appropriate|Similar to Task' docs/database-redesign-config-state-source-preparation.md tests/test_configs_source_contract.py tests/configs_source_fixtures.py`. Expected: no plan placeholders in new prose/code. Stage only own appendix and claim row; commit `git add docs/database-redesign-config-state-source-preparation.md docs/agent-claims.md && git commit -m "docs: record configs source contract evidence"`.
- [ ] **Step 4: Hand off for fresh sequential SPEC then QUALITY review.** Provide commit SHAs and focused test receipts. No implementation unit begins until the source contract is accepted. The next separate plan is target declaration/storage, then pure migration/inverse, then current caller/paired publication with actual-copy gates.

## Self-review of scope

This plan covers every captured config family by accepted literal declaration and 118-field metadata, then behaviorally samples named native identities, SQLite storage/NULL behavior, allocator, uniqueness, CHECKs, FKs, immutable exceptions, runtime load receipt matching, category CAS, scheduled snapshot interning/references/transaction rollback, and WeChat's mutable watermark. It does not claim a DML proof for every field. The literal capture already verifies all indexes/triggers/column ordinals; this oracle adds behavior without repeating the six accepted metadata tests. The six predecessor account blockers receive declaration comparison here and a separate existing 26-case source DML gate on narrow stubs. The five external FK groups belong to later atomic closure; target equivalence, migration, caller selection and publication remain separate work.
