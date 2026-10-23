"""Direct Email claim compares the Action owner's stored started_at."""

from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailStore
from tests.test_email_store import _classification


def _pending_with_empty_started_at(path, classification_id):
    email = EmailStore(path)
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=classification_id,
        actions=(EmailAction.ARCHIVE,), action_parameters={},
    )
    email.persist_scan_result(classification, model_text="synthetic")
    action_id = email.direct_action_ids_for_plan(classification.action_plan.action_plan_id)[0]
    with email._connect() as db:
        # Rows written before 2026-10-22 11:33 UTC store '' instead of NULL.
        db.execute(
            "update actions set action_data=jsonb_set(action_data,'$.started_at',json('\"\"')) "
            "where record_type='action' and id=?", (action_id,),
        )
        assert db.execute(
            "select action_started_at from actions where id=?", (action_id,)
        ).fetchone()[0] == ""
    return email, action_id


def test_claim_next_takes_a_pending_action_stored_with_empty_started_at(tmp_path):
    email, action_id = _pending_with_empty_started_at(tmp_path / "next.sqlite3", 401)

    claimed = email.claim_next_direct_action(claimed_at="2026-10-23T12:00:00+00:00")

    assert claimed is not None and claimed.action_id == action_id


def test_claim_exact_takes_a_pending_action_stored_with_empty_started_at(tmp_path):
    email, action_id = _pending_with_empty_started_at(tmp_path / "exact.sqlite3", 402)

    claimed = email.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-23T12:00:00+00:00"
    )

    assert claimed is not None and claimed.action_id == action_id
