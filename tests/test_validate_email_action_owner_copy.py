"""Validate that the copy verifier checks newly created facts at every boundary."""
import sqlite3

import pytest

from app.email_action_owner_release import prepare_email_action_owner_release
from app.shared_action_lifecycle import _update
from tests.support.store29_2_source import source_database
from tests.test_email_action_owner_release import _seed_source_action
from tools.native_copy_validation import _identities
from tools.validate_email_action_owner_copy import fingerprint, require_cohort, seed


def _valid_target(path):
    from datetime import datetime, timezone
    from app.email_classifier_contracts import EmailAction, build_versioned_email_action_plan
    from app.email_store import EmailStore

    source_database(path)
    _seed_source_action(path)
    prepare_email_action_owner_release(path).publish()
    plan = build_versioned_email_action_plan(
        action_plan_version=1, classification_id=11, account_id="account", category="work",
        classification_source="model", confidence=0.5, model_id="model", config_version="config",
        actions=(EmailAction.ARCHIVE,), action_parameters={}, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute("delete from actions where _kind in (64,70)")
        EmailStore._persist_action_plan(EmailStore.__new__(EmailStore), db, plan, now=plan.created_at.isoformat())
        db.execute('update emails set class_current_action_plan_id=? where classification_id=11', (plan.action_plan_id,))


def test_seeded_plan_validates_as_native_immutable_snapshot(tmp_path):
    from app.email_store import EMAIL_PLAN_SQL, EmailStore

    path = tmp_path / "native-plan.sqlite3"
    _valid_target(path)
    created = seed(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("select * from (" + EMAIL_PLAN_SQL + ") where action_plan_id=?", (created["plan_id"],)).fetchone()
        assert EmailStore._stored_action_plan(row).action_plan_id == created["plan_id"]


@pytest.mark.parametrize('fact', ['plan', 'action', 'receipt'])
def test_seeded_cohort_detects_each_changed_fact(tmp_path, fact):
    path = tmp_path / 'cohort.sqlite3'
    _valid_target(path)
    created = seed(path)
    identities = _identities(path)
    facts = fingerprint(path, identities)
    require_cohort(path, identities, facts, exact_identities=True)
    with sqlite3.connect(path) as db:
        if fact == 'plan':
            rowid = db.execute("select cast(legacy_key as integer) from actions where record_type='email_action_plans' and email_action_plan_id=?", (created['plan_id'],)).fetchone()[0]
            db.execute('begin')
            _update(db, 'email_action_plans', rowid, {'action_parameters_json': '{"archive":{"changed":true}}'})
        elif fact == 'action':
            db.execute("update actions set action_data=jsonb_set(action_data,'$.status','failed') where id=?", (created['action_id'],))
        else:
            db.execute("update action_receipts set error_text='changed' where id=?", (created['receipt_id'],))
    with pytest.raises(ValueError, match='typed facts changed'):
        require_cohort(path, identities, facts, exact_identities=True)


def test_seeded_plan_allows_full_email_store_cold_validation(tmp_path):
    from app.email_classifier_contracts import EmailClassificationStatus
    from app.email_store import EmailStore
    from tests.test_email_store import _classification, _persist_scan

    path = tmp_path / 'native-store.sqlite3'
    store = EmailStore(path)
    _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED))
    before = _identities(path)
    created = seed(path)
    EmailStore(path)
    after = _identities(path)
    assert before['emails'] < after['emails']
    assert created['email_id'] in after['emails'] - before['emails']
    with sqlite3.connect(path) as db:
        assert db.execute('select classification_id from emails where id=?', (created['email_id'],)).fetchone()[0] == created['classification_id']
    assert created['action_id'] in after['actions'] - before['actions']
