"""Validate that the copy verifier checks newly created facts at every boundary."""
import sqlite3

import pytest

from app.email_action_owner_release import prepare_email_action_owner_release
from app.shared_action_lifecycle import _update
from tests.support.store29_2_source import source_database
from tests.test_email_action_owner_release import _seed_source_action
from tools.native_copy_validation import _identities
from tools.validate_email_action_owner_copy import fingerprint, require_cohort, seed


@pytest.mark.parametrize('fact', ['plan', 'action', 'receipt'])
def test_seeded_cohort_detects_each_changed_fact(tmp_path, fact):
    path = tmp_path / 'cohort.sqlite3'
    source_database(path)
    _seed_source_action(path)
    prepare_email_action_owner_release(path).publish()
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
