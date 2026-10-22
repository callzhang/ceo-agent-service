"""Historical Email validator parity against genuinely migrated final copies."""

from contextlib import closing
import inspect
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from app.email_store import EmailStore, EmailPersistenceCorruption
from tests.test_email_action_plan_direct_dao import (
    converted_pair as _converted_pair,
    _converted_templates as _converted_templates,
)
from tests.test_email_action_reply_dao import (
    reply_pair as _reply_pair,
    _reply_arguments,
    _receipt_arguments,
)
from tests.test_email_action_unsubscribe_dao import (
    unsubscribe_pair as _unsubscribe_pair,
    _authorization,
)


@pytest.fixture
def converted_pair(tmp_path, request):
    return _converted_pair.__wrapped__(tmp_path, request)


@pytest.fixture
def reply_pair(tmp_path, request):
    return _reply_pair.__wrapped__(tmp_path, request)


@pytest.fixture
def unsubscribe_pair(tmp_path, request):
    return _unsubscribe_pair.__wrapped__(tmp_path, request)


def _outcome(store, db, method):
    try:
        getattr(store, method)(db)
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}
    return {"accepted": True}


def _source(pair, method, statements=()):
    _, _, _, source, old = pair
    code = (
        "import sqlite3,json,sys\nfrom app.email_store import EmailStore\n"
        + inspect.getsource(_outcome)
        + "\ns=object.__new__(EmailStore)\n"
        + "with sqlite3.connect(sys.argv[1]) as db:\n"
        + " db.row_factory=sqlite3.Row\n"
        + " db.execute('begin')\n"
        + " for sql,parameters in json.loads(sys.argv[3]):db.execute(sql,parameters)\n"
        + " result=_outcome(s,db,sys.argv[2]);db.rollback()\n"
        + " print(json.dumps(result))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(source), method, json.dumps(statements)],
        cwd=old,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "method",
    ["_validate_schema_shape", "_validate_durable_rows", "_validate_durable_state"],
)
def test_current_validators_accept_genuine_source_facts_on_explicit_target(
    converted_pair, method
):
    expected = _source(converted_pair, method)
    assert expected == {"accepted": True}
    store = converted_pair[0]
    with store._connect() as db:
        assert _outcome(store, db, method) == expected


def test_retained_required_contract_and_optional_history_coverage_match_source(
    converted_pair,
):
    store = converted_pair[0]
    cases = (
        (("drop index idx_email_messages_account_folder_uid",),
         ("drop index idx_emails_message_account_locator",), False),
        (("drop trigger trg_email_model_promotion_configs_immutable_update",),
         ("drop trigger trg_configs_promotion_update",), False),
        (("drop table email_classifier_runtime_samples",),
         ("drop table runs",), False),
        (("pragma writable_schema=on",
          "update sqlite_schema set sql=replace(sql,'email_address text not null','email_address blob not null') where name='email_accounts'"),
         ("pragma writable_schema=on",
          "update sqlite_schema set sql=replace(sql,'payload_json text not null','payload_json blob not null') where name='configs'"), False),
    )
    # Derive a genuine required native index name without assuming its spelling.
    from app.email_schema_history import _REQUIRED_INDEXES

    native_index = next(
        name
        for name, (table, _) in _REQUIRED_INDEXES.items()
        if table == "email_messages"
    )
    cases = (((f"drop index {native_index}",), ("drop index idx_emails_message_account_locator",), False), *cases[1:])
    for source_statements, target_statements, same_message in cases:
        expected = _source(
            converted_pair, "_validate_schema_shape", [(sql, []) for sql in source_statements]
        )
        assert expected["error"] == "EmailPersistenceCorruption"
        with store._connect() as db:
            db.execute("begin")
            for sql in target_statements:
                db.execute(sql)
            actual = _outcome(store, db, "_validate_schema_shape")
            if same_message:
                assert actual == expected
            else:
                assert actual["error"] == expected["error"]
            db.rollback()
    optional = [
        (f"drop table {name}", [])
        for name in (
            "email_historical_traversals",
            "email_historical_operations",
            "email_historical_candidates",
        )
    ]
    assert _source(converted_pair, "_validate_schema_shape", optional) == {
        "accepted": True
    }
    with store._connect() as db:
        # These optional physical containers were retired into shared domains.
        # The source can omit them; the final schema has no legacy containers.
        for sql, _ in optional:
            assert db.execute("select 1 from sqlite_schema where name=?", (sql.removeprefix("drop table "),)).fetchone() is None
        assert _outcome(store, db, "_validate_schema_shape") == {"accepted": True}


def test_target_manifest_and_translated_blockers_refuse_corrupt_objects(converted_pair):
    store = converted_pair[0]
    with store._connect() as db:
        index = db.execute(
            "select name from sqlite_schema where type='index' and tbl_name='actions' and name like 'idx_actions_email_%' limit 1"
        ).fetchone()[0]
        trigger = db.execute(
            "select name from sqlite_schema where type='trigger' and tbl_name='actions' limit 1"
        ).fetchone()[0]
        table_sql = db.execute(
            "select sql from sqlite_schema where name='actions'"
        ).fetchone()[0]
        name = "trg_emails_direct_action_blocks_plan_switch"
        sql = db.execute("select sql from sqlite_schema where type='trigger' and name=?", (name,)).fetchone()[0]
        cases = (
            [f"drop index {index}"],
            [f"drop trigger {trigger}"],
            ["drop trigger trg_configs_direct_account_delete"],
            [f"drop trigger {name}"],
            [
                f"drop trigger {name}",
                sql.replace("email_status='processing'", "email_status='pending'"),
            ],
            [
                "pragma writable_schema=on",
                "update sqlite_schema set sql=replace(sql,'email_public_key','wrong_public_key') where name='actions'",
            ],
            [
                "pragma writable_schema=on",
                "update sqlite_schema set sql=replace(sql,'email_classification_id','wrong_classification_id') where name='actions'",
            ],
        )
        for statements in cases:
            db.execute("savepoint corrupt_shape")
            for statement in statements:
                db.execute(statement)
            result = _outcome(store, db, "_validate_schema_shape")
            assert result["error"] == "EmailPersistenceCorruption", result
            db.execute("rollback to corrupt_shape")
            db.execute("release corrupt_shape")
        assert (
            db.execute("select sql from sqlite_schema where name='actions'").fetchone()[
                0
            ]
            == table_sql
        )
        assert _outcome(store, db, "_validate_schema_shape") == {"accepted": True}


def _row_outcome(pair, kind, values, *, accepted=False):
    """Native legal storage mutations; compare original strict row policy."""
    from app.action_storage import update_email_action, update_action
    from app.action_email_definitions import EMAIL_AUTOINCREMENT_TYPES

    store, _, _, source, _ = pair
    with closing(sqlite3.connect(source)) as original:
        rowid = original.execute(f"select rowid from {kind} limit 1").fetchone()[0]
    sql = (
        f"update {kind} set "
        + ",".join(f"{name}=?" for name in values)
        + " where rowid=?"
    )
    expected = _source(
        pair, "_validate_durable_rows", [(sql, [*values.values(), rowid])]
    )
    assert (
        expected == {"accepted": True}
        if accepted
        else expected["error"] == "EmailPersistenceCorruption"
    ), expected
    with store._connect() as db:
        db.execute("begin immediate")
        writer = (
            update_action if kind in EMAIL_AUTOINCREMENT_TYPES else update_email_action
        )
        assert writer(db, kind, rowid, values).rowcount == 1
        assert _outcome(store, db, "_validate_durable_rows") == expected
        db.rollback()


def test_plan_and_direct_native_legal_corruption_keeps_original_refusals(
    converted_pair,
):
    for kind, values in (
        ("email_action_plans", {"actions_json": "{}"}),
        ("email_actions", {"status": "done"}),
        ("email_actions", {"parameters_json": "[]"}),
    ):
        _row_outcome(converted_pair, kind, values)


@pytest.mark.parametrize("reply_pair", ["two_claims"], indirect=True)
def test_reply_claim_owner_snapshot_and_missing_receipt_refusals(reply_pair):
    assert _source(reply_pair, "_validate_durable_state") == {"accepted": True}
    with reply_pair[0]._connect() as db:
        assert _outcome(reply_pair[0], db, "_validate_durable_state") == {
            "accepted": True
        }
    for values in (
        {"account_snapshot_json": "{}"},
        {"status": "done"},
        {"owner_id": "bad\nowner"},
    ):
        _row_outcome(reply_pair, "email_reply_dispatch_claims", values)


@pytest.mark.parametrize("reply_pair", ["receipt"], indirect=True)
def test_reply_receipt_json_refusal_and_original_native_null_acceptance(reply_pair):
    assert _source(reply_pair, "_validate_durable_state") == {"accepted": True}
    with reply_pair[0]._connect() as db:
        assert _outcome(reply_pair[0], db, "_validate_durable_state") == {
            "accepted": True
        }
    for values in ({"provider_receipt_json": "[]"}, {"action_identity": None}):
        _row_outcome(
            reply_pair,
            "email_reply_receipts",
            values,
            accepted="action_identity" in values,
        )


def _unsubscribe_lifecycle_validation(store, classification):
    a, owner = _authorization(classification)
    identity = a["action_identity"]
    store.claim_email_unsubscribe_write(**a, owner=owner)
    with store._connect() as db:
        store._validate_durable_state(db)
    store.persist_email_unsubscribe_continuation(
        **a,
        owner=owner,
        controls=[
            dict(
                reference="unsubscribe-control:" + "c" * 64,
                kind="button",
                intent="confirm",
            )
        ],
        observation_reference="unsubscribe-state:" + "d" * 64,
        final_step=dict(
            sequence=1,
            operation="open_entry",
            state="action_required",
            reference="observation",
        ),
    )
    with store._connect() as db:
        store._validate_durable_state(db)
    from app.email_store import email_unsubscribe_effect_digest

    a = a | dict(
        previous_effect_digest=a["effect_digest"],
        operations=a["operations"]
        + [
            dict(
                operation_reference="step-2",
                kind="click_confirmation",
                target_reference="unsubscribe-control:" + "c" * 64,
            )
        ],
    )
    a["effect_digest"] = email_unsubscribe_effect_digest(
        **{k: v for k, v in a.items() if k != "effect_digest"}
    )
    store.claim_email_unsubscribe_write(
        **a, owner=owner | dict(generation=22, lease_token="next-lease")
    )
    store.persist_email_unsubscribe_terminal(
        **a,
        outcome="done",
        receipt_id="receipt",
        evidence="terminal-page",
        claim_owner=owner | dict(generation=22, lease_token="next-lease"),
    )
    with store._connect() as db:
        store._validate_durable_state(db)
    return store.get_email_unsubscribe_receipt(identity)


def test_unsubscribe_effect_continuation_journal_terminal_validation(unsubscribe_pair):
    from tests.test_email_action_unsubscribe_dao import _old_result
    from tests.test_email_action_plan_direct_dao import _normalized

    # The same real lifecycle is independently executed by the archived module.
    expected = _old_result(unsubscribe_pair, _unsubscribe_lifecycle_validation)
    assert (
        _normalized(_unsubscribe_lifecycle_validation(*unsubscribe_pair[:2]))
        == expected
    )
    for kind, values in (
        ("email_unsubscribe_claims", {"operations_json": "[]"}),
        ("email_unsubscribe_steps", {"sequence": 3}),
        ("email_unsubscribe_receipts", {"effect_digest": "incorrect-effect"}),
    ):
        _row_outcome(unsubscribe_pair, kind, values)


def test_read_validation_preserves_caller_savepoint_and_all_native_facts(
    converted_pair,
):
    store = converted_pair[0]
    with store._connect() as db:
        db.execute("create table caller_note(value)")
        db.execute("begin immediate")
        db.execute("insert into caller_note values('keep')")
        db.execute("savepoint validation")
        before = tuple(db.iterdump())
        changes = db.total_changes
        assert _outcome(store, db, "_validate_durable_state") == {"accepted": True}
        assert db.in_transaction and db.total_changes == changes
        assert tuple(db.iterdump()) == before
        db.execute("rollback to validation")
        db.execute("release validation")
        assert db.execute("select value from caller_note").fetchone()[0] == "keep"
        db.rollback()


@pytest.mark.parametrize("variant", ["actual_no_memory_fk", "fresh_memory_fk"])
def test_current_typed_manifest_accepts_both_exact_action_variants(variant, monkeypatch):
    from app import current_database_schema as current
    from app.email_current_schema import create_current_email_migration_ledger

    create_actions = current.create_action_schema

    def create_variant(db, **options):
        options["reply_variant"] = variant
        create_actions(db, **options)

    monkeypatch.setattr(current, "create_action_schema", create_variant)
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        current.create_current_database_schema(db)
        create_current_email_migration_ledger(db)
        db.commit()
        EmailStore._validate_schema_shape(db)
        name = "trg_emails_direct_action_blocks_plan_switch"
        sql = db.execute("select sql from sqlite_schema where type='trigger' and name=?", (name,)).fetchone()[0]
        db.execute("savepoint corrupt_shape")
        db.execute(f"drop trigger {name}")
        with pytest.raises(EmailPersistenceCorruption, match="required canonical Email declaration is missing or malformed"):
            EmailStore._validate_schema_shape(db)
        db.execute("rollback to corrupt_shape")
        db.execute("release corrupt_shape")
        EmailStore._validate_schema_shape(db)
        db.execute("savepoint corrupt_shape")
        db.execute(f"drop trigger {name}")
        corrupt = sql.replace("email_status='processing'", "email_status='pending'")
        assert corrupt != sql
        db.execute(corrupt)
        with pytest.raises(EmailPersistenceCorruption, match="required canonical Email declaration is missing or malformed"):
            EmailStore._validate_schema_shape(db)
        db.execute("rollback to corrupt_shape")
        db.execute("release corrupt_shape")
        EmailStore._validate_schema_shape(db)


@pytest.mark.parametrize("variant", ["actual_no_memory_fk", "fresh_memory_fk"])
def test_source_phase_external_installer_is_transactional_idempotent_and_nonrepairing(variant):
    from app.action_storage import create_action_schema
    from app.action_email_storage import (
        create_email_action_external_triggers,
        finite_target_external_sql,
    )
    from app.email_current_schema import create_current_email_non_action_schema

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text, legacy_key text)")
        create_current_email_non_action_schema(db)
        create_action_schema(db, include_reply=True, reply_variant=variant, include_email=True)
        db.commit()
        external = finite_target_external_sql()
        assert all(db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone() is None
                   for name in external)
        db.execute("begin")
        db.execute("savepoint install")
        create_email_action_external_triggers(db)
        assert {name: db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()[0]
                for name in external} == external
        db.execute("rollback to install")
        db.execute("release install")
        assert all(db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone() is None
                   for name in external)
        create_email_action_external_triggers(db)
        before = tuple(db.execute("select name,sql from sqlite_schema where type='trigger'"))
        create_email_action_external_triggers(db)
        assert tuple(db.execute("select name,sql from sqlite_schema where type='trigger'")) == before
        name = "trg_email_direct_action_blocks_account_delete"
        db.execute(f"drop trigger {name}")
        corrupt = external[name].replace("email_status='processing'", "email_status='pending'")
        assert corrupt != external[name]
        db.execute(corrupt)
        create_email_action_external_triggers(db)
        assert db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()[0] == corrupt
        assert corrupt != external[name]
        db.rollback()


def _preview(store, classification):
    from app.store import REPLY_TASK_CURRENT_SQL
    from app.email_store import EmailStore

    with store._connect() as db:
        row = db.execute(f"select * from ({REPLY_TASK_CURRENT_SQL}) limit 1").fetchone()
        args = dict(
            task_id=row["id"],
            expected_action_identity=row["trigger_message_id"],
            expected_execution_generation=row["execution_generation"],
            expected_input_version=row["input_version"],
            reason="Keep subscription.",
        )
    EmailStore._now = staticmethod(lambda: "2026-10-14T10:00:00+00:00")
    return EmailStore.preview_failed_email_unsubscribe_task(store.path, **args)


def test_public_readonly_cancellation_preview_closes_shape_dependency(
    unsubscribe_pair, monkeypatch, tmp_path
):
    from tests.test_email_action_unsubscribe_dao import _old_result

    expected = _old_result(unsubscribe_pair, _preview)
    target = tmp_path / "current-target.sqlite3"
    with closing(sqlite3.connect(unsubscribe_pair[0].path)) as source_db:
        with closing(sqlite3.connect(target)) as target_db:
            source_db.backup(target_db)
    store = EmailStore(target)
    monkeypatch.setattr(
        EmailStore, "_now", staticmethod(lambda: "2026-10-14T10:00:00+00:00")
    )
    before = target.read_bytes()
    with store._connect() as db:
        facts = tuple(db.iterdump())
    assert _preview(store, unsubscribe_pair[1]) == expected
    with store._connect() as db:
        assert tuple(db.iterdump()) == facts
    assert target.read_bytes() == before


def test_completed_direct_retry_and_feedback_ledgers_validate_original_semantics(
    converted_pair,
):
    from tests.test_email_action_plan_direct_dao import (
        _direct_cycle,
        _feedback_and_plan_conflicts,
        _genuine_old_result,
        _normalized,
    )

    for operation in (_direct_cycle, _feedback_and_plan_conflicts):
        expected = _genuine_old_result(converted_pair, operation)
        assert _normalized(operation(*converted_pair[:2])) == expected
        assert {"message_text", "cc"}.isdisjoint(converted_pair[0].get_classification(12))
        assert _source(converted_pair, "_validate_durable_state") == {"accepted": True}
        with converted_pair[0]._connect() as db:
            assert _outcome(converted_pair[0], db, "_validate_durable_state") == {
                "accepted": True
            }
    _row_outcome(
        converted_pair, "email_feedback_requests", {"feedback_request_id": None}
    )
    from app.action_storage import insert_action
    from app.email_store import EMAIL_RETRY_SQL, email_imap_configuration_snapshot

    _, _, _, source, _ = converted_pair
    with closing(sqlite3.connect(source)) as db:
        db.row_factory = sqlite3.Row
        action = db.execute(
            "select action_id,attempt_count from email_actions order by attempt_count desc limit 1"
        ).fetchone()
        account = dict(db.execute("select * from email_accounts limit 1").fetchone())
        attempt_count = action["attempt_count"]
        # Native storage boundary: the original startup validator checks this
        # immutable ledger's next-attempt bound and timestamp, not release proof.
        retry = dict(
            action_id=action["action_id"],
            attempt_number=attempt_count + 1,
            source_readback_digest="a" * 64,
            account_configuration_snapshot=email_imap_configuration_snapshot(account),
            requested_at="2026-10-14T10:00:00+00:00",
        )
    for changed in (
        {},
        dict(attempt_number=99),
        dict(attempt_number=attempt_count + 1, requested_at="invalid-timestamp"),
    ):
        values = retry | changed
        sql = (
            "insert into email_direct_retry_requests("
            + ",".join(values)
            + ") values("
            + ",".join("?" for _ in values)
            + ")"
        )
        expected = _source(
            converted_pair, "_validate_durable_state", [(sql, list(values.values()))]
        )
        if not changed:
            assert expected == {"accepted": True}
        elif "requested_at" in changed:
            # The original state validator does not wrap this timestamp ValueError.
            assert expected["error"] == "ValueError", expected
        else:
            assert expected["error"] == "EmailPersistenceCorruption", expected
        with converted_pair[0]._connect() as db:
            db.execute("begin immediate")
            insert_action(db, "email_direct_retry_requests", values)
            assert (
                db.execute(f"select count(*) from ({EMAIL_RETRY_SQL})").fetchone()[0]
                == 1
            )
            assert (
                _outcome(converted_pair[0], db, "_validate_durable_state") == expected
            )
            db.rollback()


def _null_public_receipt(store, classification):
    args, _ = _reply_arguments(store, classification)
    try:
        store.persist_email_reply_receipt(
            **(_receipt_arguments(args) | dict(action_identity=None))
        )
    except Exception as exc:
        return dict(error=type(exc).__name__, message=str(exc))
    raise AssertionError("public receipt API accepted NULL identity")


@pytest.mark.parametrize("reply_pair", ["receipt"], indirect=True)
def test_native_null_receipt_acceptance_does_not_change_public_input_refusal(
    reply_pair,
):
    from tests.test_email_action_reply_dao import _old_result

    expected = _old_result(reply_pair, _null_public_receipt)
    # These names are also supplied independently by the archived helper process.
    assert _null_public_receipt(*reply_pair[:2]) == expected
