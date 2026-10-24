"""Direct Email DAO parity after migrating genuine old facts to the final schema."""

from contextlib import closing
import sqlite3

import pytest

from app.action_storage import insert_email_action
from tests.test_email_action_storage import _cases
from tests.test_email_action_writes import _databases


def test_operation_conflict_target_ignores_only_existing_plan_type():
    source, target = _databases(compact_payload=True)
    with closing(source), closing(target):
        for db in (source, target):
            values = _cases()["email_action_plans"]
            if db is source:
                db.execute(
                    f"insert into email_action_plans({','.join(values)}) values({','.join('?' for _ in values)})",
                    tuple(values.values()),
                )
            else:
                insert_email_action(db, "email_action_plans", values)
        values = _cases()["email_actions"]

        def write(db, values, *, source_rowid=None):
            if db is source:
                if source_rowid is not None:
                    values = {"rowid": source_rowid, **values}
                return db.execute(
                    f"insert into email_actions({','.join(values)}) values({','.join('?' for _ in values)}) on conflict(action_plan_id,action_type) do nothing",
                    tuple(values.values()),
                )
            return insert_email_action(
                db,
                "email_actions",
                values,
                source_rowid=source_rowid,
                ignore_plan_action_conflict=True,
            )

        for db in (source, target):
            assert write(db, values).rowcount == 1
            assert (
                write(db, values | {"action_id": "different-public-key"}).rowcount == 0
            )
            assert write(db, values, source_rowid=2**63 - 1).rowcount == 0
            for changed in (
                {
                    "action_type": "mark_read"
                },  # Different chosen key; duplicate public PK.
                {"status": "discarded"},  # CHECK precedes chosen-key conflict.
                {"created_at": None},  # NOT NULL precedes chosen-key conflict.
                {"action_id": "missing-parent", "action_plan_id": "absent"},
            ):
                with pytest.raises(sqlite3.IntegrityError):
                    write(db, values | changed)
            appended = write(
                db,
                values | {"action_id": "fresh-operation", "action_type": "mark_read"},
            )
            assert appended.rowcount == 1 and appended.lastrowid == 2


def _genuine_old_source(tmp_path, request):
    """Seed exact archived-code facts without preparing an unused target."""
    import io
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tarfile

    from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
    from tests.test_email_store import _classification

    old = tmp_path / "published-old"
    old.mkdir()
    archive = subprocess.check_output(
        ["git", "archive", "c5990cab36237f98d5220600d2afefb2bfd25ed1", "app"], cwd=Path(__file__).resolve().parents[1]
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    source = tmp_path / "source.sqlite3"
    fixture_actions = (
        (EmailAction.TRASH,)
        if getattr(request, "param", "") == "trash"
        else (EmailAction.ARCHIVE, EmailAction.MARK_READ)
    )
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        classification_id=11,
        actions=fixture_actions,
        action_parameters={},
    )
    pending = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        classification_id=12,
        message_id="pending",
    )
    from tests.test_email_store import _task2_account_values

    account = _task2_account_values("dingtalk-account", enabled=False)
    code = """
import json,sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
from app.action_storage import update_email_action
p=Path(sys.argv[1]); AutoReplyStore(p); s=EmailStore(p)
c=EmailClassification.model_validate_json(sys.argv[2]); s.persist_scan_result(c,model_text='fixture features')
s.persist_scan_result(EmailClassification.model_validate_json(sys.argv[3]),model_text='pending features')
s.create_account(json.loads(sys.argv[4]))
ids=s.direct_action_ids_for_plan(c.action_plan.action_plan_id)
with s._connect() as db:
    rowid=db.execute("select cast(legacy_key as integer) from actions where record_type='email_actions' and json_extract(payload_json,'$[0]')=?",(ids[0],)).fetchone()[0]
    update_email_action(db,'email_actions',rowid,{},new_source_rowid=-7)
print(json.dumps(ids))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(source),
            classification.model_dump_json(),
            pending.model_dump_json(),
            json.dumps(account),
        ],
        cwd=old,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return classification, json.loads(result.stdout), source, old


def _build_converted_pair(tmp_path, request):
    from pathlib import Path
    import subprocess

    from app.store_schema_release import prepare_database_schema_release
    from app.email_store import EmailStore

    classification, source_ids, source, old = _genuine_old_source(tmp_path, request)
    target = tmp_path / "target.sqlite3"
    with (
        closing(sqlite3.connect(source)) as original,
        closing(sqlite3.connect(target)) as db,
    ):
        original.backup(db)
    release = prepare_database_schema_release(Path(__file__).resolve().parents[1], target)
    try:
        release.publish()
    except subprocess.CalledProcessError as error:
        error.add_note(error.stderr or "paired child returned no stderr")
        raise
    store = EmailStore(target)
    store._now = lambda: "2026-10-14T10:00:00+00:00"
    return store, classification, source_ids, source, old


@pytest.fixture(scope="session")
def _converted_templates(pytestconfig):
    # Imported fixture definitions share the session's immutable templates.
    # Templates never reach a DAO caller; each test gets independent files.
    # pytest imports this fixture under both bare and tests.* module names.
    return vars(pytestconfig).setdefault("_ceo_email_direct_templates", {})


@pytest.fixture
def converted_pair(tmp_path, request):
    from pathlib import Path
    from app.email_store import EmailStore

    templates = request.getfixturevalue("_converted_templates")
    variant = getattr(request, "param", "")
    if variant not in templates:
        template_path = request.getfixturevalue("tmp_path_factory").mktemp("direct-template")
        templates[variant] = _build_converted_pair(template_path, request)
    template_store, classification, source_ids, template_source, old = templates[variant]
    target = tmp_path / "target.sqlite3"
    source = tmp_path / "source.sqlite3"
    for original_path, copied_path in ((template_store.path, target), (template_source, source)):
        with closing(sqlite3.connect(original_path)) as original, closing(sqlite3.connect(copied_path)) as copied:
            original.backup(copied)
    store = EmailStore(target)
    store._now = lambda: "2026-10-14T10:00:00+00:00"
    return store, type(classification).model_validate_json(classification.model_dump_json()), list(source_ids), source, Path(old)


def test_real_direct_plan_read_uses_public_action_ids_on_explicit_converted_copy(
    converted_pair,
):
    store, classification, source_ids, _, _ = converted_pair
    current_ids = store.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id
    )
    assert _canonical_current_action_ids(converted_pair, {"ids": current_ids})["ids"] == source_ids


def _normalized(value):
    import dataclasses
    from collections.abc import Mapping
    from enum import Enum
    import json

    def encode(item):
        if dataclasses.is_dataclass(item):
            return {
                field.name: getattr(item, field.name)
                for field in dataclasses.fields(item)
            }
        if isinstance(item, Mapping):
            return dict(item)
        if isinstance(item, Enum):
            return item.value
        raise TypeError(type(item).__name__)

    return json.loads(json.dumps(value, default=encode))


def _canonical_current_action_ids(pair, value, *, store=None):
    """Compare converted Action owner IDs through their retained source identity."""
    from app.email_store import DIRECT_EMAIL_ACTION_SQL, _direct_action_id
    from app.email_classifier_contracts import EmailAction

    store = store or pair[0]
    with store._connect() as db:
        rows = db.execute(
            "select action_id, action_plan_id, action_type "
            f"from ({DIRECT_EMAIL_ACTION_SQL})"
        ).fetchall()
    owner_to_source = {
        int(row["action_id"]): _direct_action_id(
            str(row["action_plan_id"]), EmailAction(row["action_type"])
        )
        for row in rows
    }

    def canonical(item, field=None):
        if isinstance(item, dict):
            if field == "statuses":
                return {
                    owner_to_source.get(int(key), key) if str(key).lstrip("-").isdigit() else key:
                    canonical(child, key)
                    for key, child in item.items()
                }
            return {key: canonical(child, key) for key, child in item.items()}
        if isinstance(item, list):
            if field in {"ids", "direct_ids", "current_ids", "retired_ids", "original_ids", "new_ids"}:
                return [owner_to_source.get(entry, entry) if isinstance(entry, int) else entry for entry in item]
            return [canonical(child, field) for child in item]
        if field in {"action_id", "candidate"} and isinstance(item, int):
            return owner_to_source.get(item, item)
        return item

    return canonical(_normalized(value))


def _genuine_old_result(pair, operation):
    import inspect
    import os
    import subprocess
    import sys

    _, classification, _, source, old = pair
    code = (
        """
import json,sys,dataclasses
from enum import Enum
from pathlib import Path
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
s=EmailStore(Path(sys.argv[1]))
s._now=lambda:'2026-10-14T10:00:00+00:00'
c=EmailClassification.model_validate_json(sys.argv[2])
"""
        + inspect.getsource(_normalized)
        + "\n"
        + inspect.getsource(operation)
        + f"\nprint(json.dumps(_normalized({operation.__name__}(s,c))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(source), classification.model_dump_json()],
        cwd=old,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    import json

    return json.loads(result.stdout)


def _direct_cycle(store, classification):
    from datetime import datetime, timezone
    from app.email_store import EmailActionAttemptConflict

    plan_id = classification.action_plan.action_plan_id
    first = store.claim_next_direct_action(claimed_at="2026-10-14T10:00:00+00:00")
    assert first is not None
    assert (
        store.claim_next_direct_action(claimed_at="2026-10-14T10:00:01+00:00") is None
    )
    failed = store.complete_direct_action_attempt(
        first,
        status="failed",
        provider_operation="apply",
        provider_target=first.locator.stable_message_identity,
        provider_result_id="",
        error="temporary",
        finished_at="2026-10-14T10:00:02+00:00",
    )
    assert (
        store.claim_direct_action(
            action_id=first.action_id, claimed_at="2026-10-14T10:00:03+00:00"
        )
        is None
    )
    retry = store.claim_next_direct_action(claimed_at="2026-10-14T11:00:00+00:00")
    assert retry is not None and retry.action_id == first.action_id
    done = store.complete_direct_action_attempt(
        retry,
        status="done",
        provider_operation="readback",
        provider_target=retry.locator.stable_message_identity,
        provider_result_id="verified-receipt",
        error="",
        finished_at="2026-10-14T11:00:02+00:00",
    )
    try:
        store.complete_direct_action_attempt(
            first,
            status="done",
            provider_operation="readback",
            provider_target=first.locator.stable_message_identity,
            provider_result_id="stale-receipt",
            error="",
            finished_at="2026-10-14T11:00:03+00:00",
        )
    except EmailActionAttemptConflict:
        stale_rejected = True
    else:
        stale_rejected = False
    sibling = store.claim_next_direct_action(claimed_at="2026-10-14T12:00:00+00:00")
    assert sibling is not None
    recovered = store.recover_stale_processing_actions(
        stale_before="2026-10-14T12:01:00+00:00",
        recovered_at="2026-10-14T12:02:00+00:00",
    )
    reconciliation = store.claim_failed_direct_action_for_reconciliation(
        action_id=str(sibling.action_id),
        expected_attempt_count=1,
        claimed_at="2026-10-14T12:03:00+00:00",
    )
    assert reconciliation is not None
    final = store.complete_direct_action_attempt(
        reconciliation,
        status="done",
        provider_operation="readback",
        provider_target=reconciliation.locator.stable_message_identity,
        provider_result_id="reconciled-receipt",
        error="",
        finished_at="2026-10-14T12:03:02+00:00",
    )
    return dict(
        first=first,
        failed=failed,
        retry=retry,
        done=done,
        stale_rejected=stale_rejected,
        recovered=recovered,
        final=final,
        statuses=store.direct_action_statuses_for_plan(plan_id),
        receipts=store.direct_action_receipts_for_plan(plan_id),
        attempts=store.list_action_attempts(first.action_id),
        mailbox=store.list_mailbox_action_states([11]),
        progress=store.email_processing_progress(
            now=datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
        ),
        flagged=store.list_flagged_important_between(
            datetime(2026, 10, 14, tzinfo=timezone.utc),
            datetime(2026, 10, 15, tzinfo=timezone.utc),
        ),
    )


def test_direct_poll_claim_retry_completion_recovery_matches_genuine_old(
    converted_pair,
):
    expected = _genuine_old_result(converted_pair, _direct_cycle)
    actual = _canonical_current_action_ids(
        converted_pair, _direct_cycle(converted_pair[0], converted_pair[1])
    )
    assert actual == expected
    assert actual["failed"]["id"] == 1
    assert actual["stale_rejected"] and actual["recovered"] == 1


def _feedback_and_plan_conflicts(store, classification):
    from datetime import datetime, timezone
    from app.email_store import EmailActionPlanConflict, EmailClassificationConflict

    plan = classification.action_plan
    with store._connect() as db:
        db.execute("begin immediate")
        store._persist_action_plan(db, plan, now=store._now())
    conflicts = []
    for changed in (
        {"category": "legal"},
        {"action_plan_id": "another-public-plan"},
        {"action_plan_id": "gap-plan", "action_plan_version": 3},
    ):
        try:
            with store._connect() as db:
                db.execute("begin immediate")
                store._persist_action_plan(
                    db, plan.model_copy(update=changed), now=store._now()
                )
        except EmailActionPlanConflict as error:
            conflicts.append(str(error))
    when = datetime(2026, 10, 14, 10, tzinfo=timezone.utc)
    applied = store.apply_human_classification(
        12,
        "work",
        feedback_request_id="first-feedback",
        expected_current_action_plan_id=None,
        created_at=when,
    )
    replay = store.apply_human_classification(
        12,
        "work",
        feedback_request_id="first-feedback",
        expected_current_action_plan_id=None,
        created_at=when,
    )
    for request, prior in (("first-feedback", "different"), ("stale-cas", "different")):
        try:
            store.apply_human_classification(
                12,
                "work",
                feedback_request_id=request,
                expected_current_action_plan_id=prior,
                created_at=when,
            )
        except EmailClassificationConflict as error:
            conflicts.append(str(error))
    correction = store.correct_training_label(
        12,
        "legal",
        feedback_request_id="correction",
        expected_current_action_plan_id=applied.resulting_action_plan_id,
        created_at=when,
    )
    # The native getter also joined body presentation. Final runtime identity
    # reads are metadata-only; the Console source reader owns original content.
    classification_state = {
        key: value for key, value in store.get_classification(12).items()
        if key not in ("message_text", "cc")
    }
    return dict(
        conflicts=conflicts,
        applied=applied,
        replay=replay,
        correction=correction,
        classification=classification_state,
        direct_ids=store.direct_action_ids_for_plan(plan.action_plan_id),
    )


def test_plan_idempotence_feedback_null_prior_replay_and_cas_match_genuine_old(
    converted_pair,
):
    expected = _genuine_old_result(converted_pair, _feedback_and_plan_conflicts)
    actual = _canonical_current_action_ids(
        converted_pair, _feedback_and_plan_conflicts(converted_pair[0], converted_pair[1])
    )
    assert actual == expected
    assert {"message_text", "cc"}.isdisjoint(converted_pair[0].get_classification(12))
    assert len(actual["conflicts"]) == 5
    assert actual["applied"]["applied"] and not actual["replay"]["applied"]


def _append_reapply_and_retired_poll(store, classification):
    from datetime import datetime, timezone
    from app.email_classifier_contracts import (
        EmailAction,
        EmailCategory,
        EmailProviderLocator,
        build_versioned_email_action_plan,
    )
    from app.email_store import EmailActionPlanConflict

    plan = build_versioned_email_action_plan(
        action_plan_version=2,
        classification_id=11,
        account_id=classification.provider_locator.account_id,
        category="work",
        classification_source="user",
        confidence=classification.confidence,
        model_id=classification.model_id,
        config_version="new-config",
        actions=(EmailAction.MARK_READ,),
        action_parameters={},
        created_at=datetime(2026, 10, 14, 9, tzinfo=timezone.utc),
    )
    appended = store.append_action_plan_version(11, plan, confirmed_category="work")
    assert (
        store.append_action_plan_version(11, plan, confirmed_category="work")
        == appended
    )
    try:
        store.append_action_plan_version(
            11, classification.action_plan, confirmed_category="work"
        )
    except EmailActionPlanConflict:
        retired_rejected = True
    else:
        retired_rejected = False
    with store._connect() as db:
        current = store._next_direct_action_row(db, "2026-10-14T10:00:00+00:00", None)
    assert current["action_plan_id"] == plan.action_plan_id
    ids = (
        *store.direct_action_ids_for_plan(classification.action_plan.action_plan_id),
        *store.direct_action_ids_for_plan(plan.action_plan_id),
    )
    for action_id in ids:
        store.append_action_attempt(
            action_id=action_id,
            attempt_number=1,
            status="done",
            provider_operation="readback",
            provider_target=classification.stable_message_identity,
            provider_result_id="fixture-receipt",
            error="",
            started_at="2026-10-14T10:00:00+00:00",
            finished_at="2026-10-14T10:00:02+00:00",
        )
    store.upsert_config(
        category=EmailCategory.WORK,
        description="Work",
        threshold=0.8,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        enabled=True,
        config_version="copy-config",
    )
    copy = store.reapply_classification_to_copy(
        classification.stable_message_identity,
        EmailProviderLocator(
            account_id=classification.provider_locator.account_id,
            folder="COPY",
            uidvalidity=42,
            uid=99,
            rfc_message_id=classification.provider_locator.rfc_message_id,
            thread_id=None,
        ),
        now=datetime(2026, 10, 14, 11, tzinfo=timezone.utc),
    )
    return dict(
        appended=appended,
        retired_rejected=retired_rejected,
        current_plan=current["action_plan_id"],
        copy=copy,
        original_ids=store.direct_action_ids_for_plan(
            classification.action_plan.action_plan_id
        ),
        new_ids=store.direct_action_ids_for_plan(copy["current_action_plan_id"]),
        original_attempts=store.list_action_attempts(ids[0]),
    )


def test_append_reapply_retired_plan_poll_and_public_attempt_ids_match_old(
    converted_pair,
):
    expected = _genuine_old_result(converted_pair, _append_reapply_and_retired_poll)
    actual = _canonical_current_action_ids(
        converted_pair, _append_reapply_and_retired_poll(converted_pair[0], converted_pair[1])
    )
    assert actual == expected
    assert actual["retired_rejected"] and actual["original_attempts"][0]["id"] == 1


def _explicit_retry(store, classification):
    from app.email_classifier_contracts import EmailAction
    from app.email_store import email_imap_configuration_snapshot

    first = store.claim_next_direct_action(claimed_at="2026-10-14T10:00:00+00:00")
    store.complete_direct_action_attempt(
        first,
        status="failed",
        provider_operation="READ",
        provider_target=first.locator.stable_message_identity,
        provider_result_id="",
        error="unavailable",
        finished_at="2026-10-14T10:00:02+00:00",
        retryable=False,
    )
    failed = store.list_failed_direct_actions(
        account_id=first.account_id, action_types=(EmailAction.TRASH,), limit=10
    )[0]
    snapshot = email_imap_configuration_snapshot(store.get_account(first.account_id))
    arguments = dict(
        source_locator=failed.action.locator,
        source_revision="verified-source-revision",
        requested_at="2026-10-14T11:00:00+00:00",
        account_configuration_snapshot=snapshot,
    )
    preview = store.release_source_verified_direct_action(
        failed, apply=False, **arguments
    )
    released = store.release_source_verified_direct_action(
        failed, apply=True, **arguments
    )
    repeated = store.release_source_verified_direct_action(
        failed, apply=True, **arguments
    )
    retry = store.claim_direct_action(
        action_id=first.action_id, claimed_at="2026-10-14T11:00:01+00:00"
    )
    assert retry is not None
    completed = store.complete_direct_action_attempt(
        retry,
        status="failed",
        provider_operation="READ",
        provider_target=retry.locator.stable_message_identity,
        provider_result_id="",
        error="still-unavailable",
        finished_at="2026-10-14T11:00:02+00:00",
        retryable=True,
    )
    assert (
        store.claim_direct_action(
            action_id=first.action_id, claimed_at="2026-10-14T15:00:00+00:00"
        )
        is None
    )
    return dict(
        preview=preview,
        released=released,
        repeated=repeated,
        completed=completed,
        attempts=store.list_action_attempts(first.action_id),
        mailbox=store.list_mailbox_action_states([11]),
    )


@pytest.mark.parametrize("converted_pair", ["trash"], indirect=True)
def test_explicit_source_verified_retry_is_immutable_and_has_no_automatic_replay(
    converted_pair,
):
    expected = _genuine_old_result(converted_pair, _explicit_retry)
    actual = _canonical_current_action_ids(
        converted_pair, _explicit_retry(converted_pair[0], converted_pair[1])
    )
    assert actual == expected
    assert actual["preview"] and actual["released"] and not actual["repeated"]


def _concurrent_claim(store, classification):
    from concurrent.futures import ThreadPoolExecutor

    action_id = store.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id
    )[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: store.claim_direct_action(
                    action_id=action_id, claimed_at="2026-10-14T10:00:00+00:00"
                ),
                range(2),
            )
        )
    return dict(
        winners=sum(value is not None for value in results),
        statuses=store.direct_action_statuses_for_plan(
            classification.action_plan.action_plan_id
        ),
    )


def test_concurrent_claim_has_same_single_winner_as_genuine_old(converted_pair):
    expected = _genuine_old_result(converted_pair, _concurrent_claim)
    actual = _canonical_current_action_ids(
        converted_pair, _concurrent_claim(converted_pair[0], converted_pair[1])
    )
    assert actual == expected and actual["winners"] == 1


def _cas_failure(store, classification, target=False):
    from app.email_store import EmailActionAttemptConflict

    first = store.claim_next_direct_action(claimed_at="2026-10-14T10:00:00+00:00")
    statuses_before = store.direct_action_statuses_for_plan(
        classification.action_plan.action_plan_id
    )
    with store._connect() as db:
        if target:
            db.execute(
                "create trigger fixture_action_cas_change after insert on action_receipts "
                "begin update actions set action_data=jsonb_set(action_data,'$.status',json(json_quote('pending'))) "
                "where id=new.action_id and record_type='action'; end"
            )
        else:
            db.execute(
                f"create trigger fixture_action_cas_change after insert on actions "
                f"when new.record_type='email_action_attempts' begin "
                "update actions set payload_json=jsonb_set(jsonb_set(payload_json,'$.status',json(json_quote('pending'))), '$.started_at',json(json_quote(''))) "
                f"where record_type='email_actions' and email_action_id='{first.action_id}'; end"
            )
    try:
        store.complete_direct_action_attempt(
            first,
            status="done",
            provider_operation="readback",
            provider_target=first.locator.stable_message_identity,
            provider_result_id="receipt",
            error="",
            finished_at="2026-10-14T10:00:02+00:00",
        )
    except EmailActionAttemptConflict as error:
        conflict = str(error)
    else:
        raise AssertionError("mutated source CAS was accepted")
    return dict(
        conflict=conflict,
        attempts=store.list_action_attempts(first.action_id),
        statuses_before=statuses_before,
        statuses=store.direct_action_statuses_for_plan(
            classification.action_plan.action_plan_id
        ),
    )


def test_completion_rechecks_cas_after_insert_and_rolls_back_entire_transaction(
    converted_pair,
):
    actual = _cas_failure(converted_pair[0], converted_pair[1], target=True)
    assert actual["attempts"] == []
    assert actual["statuses"] == actual["statuses_before"]


def test_current_plan_poll_keeps_typed_indexes_and_bounded_unrelated_volume(
    converted_pair, tmp_path,
):
    from app.email_store import EmailStore
    from tests.test_email_store import _persist_scan, _task2_account_values

    _, classification, _, _, _ = converted_pair
    expected = _genuine_old_result(converted_pair, _retired_current_poll)
    store = EmailStore(tmp_path / "selected-current12.sqlite3")
    store._now = lambda: "2026-10-14T10:00:00+00:00"
    _persist_scan(store, classification)
    store.create_account(_task2_account_values("dingtalk-account", enabled=False))
    current = _canonical_current_action_ids(
        converted_pair, _retired_current_poll(store, classification), store=store
    )
    assert current == expected

    def measure():
        statements = []
        steps = [0]
        with store._connect() as db:
            db.set_trace_callback(statements.append)
            db.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1) or 0, 1)
            row = store._next_direct_action_row(db, "2026-10-14T10:00:00+00:00", None)
            db.set_progress_handler(None, 0)
            db.set_trace_callback(None)
            plans = [
                tuple(
                    tuple(item)
                    for item in db.execute("explain query plan " + statement)
                )
                for statement in statements
                if statement.lstrip().lower().startswith("select")
            ]
        return row["action_id"], steps[0], plans

    before, before_steps, plans = measure()
    assert any("idx_actions_action_direct_email_plan_operation" in str(plan) for plan in plans), plans
    with store._connect() as db:
        for i in range(1, 3001):
            insert_email_action(
                db, "email_unsubscribe_cancellations",
                {"action_identity": f"unrelated-{i}", "decision_json": "{}"},
                source_rowid=i,
            )
    after, after_steps, after_plans = measure()
    assert before == after
    assert any("idx_actions_action_direct_email_plan_operation" in str(plan) for plan in after_plans), after_plans
    assert after_steps <= before_steps + 100, (before_steps, after_steps)
    source_candidate = _canonical_current_action_ids(
        converted_pair, {"candidate": before}, store=store
    )["candidate"]
    assert source_candidate in current["current_ids"]
    assert source_candidate not in current["retired_ids"]
    print(
        f"direct poll VM steps: {before_steps} -> {after_steps} with 3000 unrelated actions"
    )


def _retired_current_poll(store, classification):
    from datetime import datetime, timezone
    from app.email_classifier_contracts import (
        EmailAction,
        build_versioned_email_action_plan,
    )

    plan = build_versioned_email_action_plan(
        action_plan_version=2,
        classification_id=11,
        account_id=classification.provider_locator.account_id,
        category="work",
        classification_source="user",
        confidence=classification.confidence,
        model_id=classification.model_id,
        config_version="current-config",
        actions=(EmailAction.MARK_READ,),
        action_parameters={},
        created_at=datetime(2026, 10, 14, 9, tzinfo=timezone.utc),
    )
    store.append_action_plan_version(11, plan, confirmed_category="work")
    with store._connect() as db:
        row = store._next_direct_action_row(db, "2026-10-14T10:00:00+00:00", None)
    return dict(
        candidate=row["action_id"],
        plan_id=row["action_plan_id"],
        current_ids=store.direct_action_ids_for_plan(plan.action_plan_id),
        retired_ids=store.direct_action_ids_for_plan(
            classification.action_plan.action_plan_id
        ),
    )


def _classification_filter_results(store, classification, target=False):
    from app import email_store
    classifications = "(" + getattr(
        email_store, "EMAIL_CLASSIFICATION_SQL",
        email_store._EMAIL_CLASSIFICATION_SQL,
    ) + ")"
    results = {}
    for status in (
        "none",
        "failed",
        "failed_retriable",
        "failed_not_retriable",
        "pending",
        "done",
    ):
        where, args = store._classification_filters(
            category="", source="", action_status=status
        )
        with store._connect() as db:
            rows = db.execute(
                f"select classifications.id from {classifications} as classifications where 1=1"
                + where
                + " order by classifications.id",
                args,
            ).fetchall()
        results[status] = [row[0] for row in rows]
    return results


def test_classification_filter_sql_uses_same_current_plan_membership_as_old(
    converted_pair,
):
    expected = _genuine_old_result(converted_pair, _classification_filter_results)
    actual = _classification_filter_results(converted_pair[0], converted_pair[1], target=True)
    assert actual == expected


def test_direct_scalar_cas_preserves_nonmatching_row_without_mutation(converted_pair):
    from app.action_entity_storage import get_action, record_action_result
    from app.email_store import EMAIL_DIRECT_ACTION_SQL

    store, classification, ids, _, _ = converted_pair
    with store._connect() as db:
        db.execute("begin immediate")
        before = [tuple(row) for row in db.execute(EMAIL_DIRECT_ACTION_SQL)]
        action_id = before[0][0]
        action = get_action(db, action_id)
        assert action is not None
        for field, wrong_value in (
            ("status", "processing"),
            ("started_at", "changed"),
            ("action_plan_id", "different-plan"),
        ):
            assert record_action_result(
                db, action_id,
                expected={field: wrong_value},
                changes={"status": "done"},
            ) is None
        assert [tuple(row) for row in db.execute(EMAIL_DIRECT_ACTION_SQL)] == before


def _attempt_sequence_conflicts(store, classification):
    from app.email_store import EmailActionAttemptConflict

    claimed = store.claim_next_direct_action(
        claimed_at="2026-10-14T10:00:00+00:00"
    )
    assert claimed is not None
    action_id = claimed.action_id
    values = dict(
        action_id=action_id,
        attempt_number=1,
        status="done",
        provider_operation="readback",
        provider_target=classification.stable_message_identity,
        provider_result_id="receipt",
        error="",
        started_at="2026-10-14T10:00:00+00:00",
        finished_at="2026-10-14T10:00:02+00:00",
    )
    first = store.complete_direct_action_attempt(
        claimed,
        status="failed",
        provider_operation="readback",
        provider_target=classification.stable_message_identity,
        provider_result_id="",
        error="temporary failure",
        finished_at="2026-10-14T10:00:02+00:00",
        retryable=True,
    )
    errors = []
    for changed in (
        {},
        {"attempt_number": 3},
        {"attempt_number": 2, "status": "processing"},
    ):
        try:
            store.append_action_attempt(**(values | changed))
        except (EmailActionAttemptConflict, ValueError) as error:
            errors.append((type(error).__name__, str(error)))
    retry = store.claim_next_direct_action(
        claimed_at="2026-10-14T11:00:00+00:00"
    )
    assert retry is not None
    second = store.append_action_attempt(**(values | {"attempt_number": 2}))
    return dict(
        first=first,
        second=second,
        errors=errors,
        attempts=store.list_action_attempts(action_id),
    )


def test_attempt_append_sequence_conflicts_and_native_highwater_match_old(
    converted_pair,
):
    expected = _genuine_old_result(converted_pair, _attempt_sequence_conflicts)
    actual = _canonical_current_action_ids(
        converted_pair, _attempt_sequence_conflicts(converted_pair[0], converted_pair[1])
    )
    assert actual["attempts"] == expected["attempts"]
    assert actual["first"] == expected["first"]
    assert actual["second"] == expected["second"]
    assert [entry[0] for entry in actual["errors"]] == [
        entry[0] for entry in expected["errors"]
    ]
    assert actual["first"]["id"] < actual["second"]["id"]
    assert len(actual["errors"]) == 3
