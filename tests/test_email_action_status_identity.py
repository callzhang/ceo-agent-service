"""Email status maps retain the native Action identity used by plan replay."""

import pytest

from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailStore
from tests.support.native_email_actions import complete_action, persist_classification


@pytest.mark.parametrize("completed", [False, True])
def test_status_map_keys_match_exact_plan_action_ids(tmp_path, completed):
    store = EmailStore(tmp_path / "native-status.sqlite3")
    plan_id = persist_classification(
        store, classification_id=101, status=EmailClassificationStatus.PROCESSED,
        action=EmailAction.MARK_READ,
    )
    ids = store.direct_action_ids_for_plan(plan_id)
    assert len(ids) == 1 and type(ids[0]) is int
    if completed:
        complete_action(store, ids[0])
    statuses = store.direct_action_statuses_for_plan(plan_id)
    assert tuple(statuses) == ids
    assert all(type(identity) is int for identity in statuses)
    assert statuses[ids[0]] == ("done" if completed else "pending")
