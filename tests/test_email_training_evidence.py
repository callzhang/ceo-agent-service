from types import SimpleNamespace

from app import email_classifier_training as training


def test_test_support_counts_missed_and_rejected_positives():
    rows = [
        {"category_key": "work", "group_key": "a"},
        {"category_key": "work", "group_key": "a"},
        {"category_key": "work", "group_key": "b"},
        {"category_key": "legal", "group_key": "c"},
    ]
    predictions = [
        SimpleNamespace(category=category, category_probability=probability)
        for category, probability in [("work", .99), ("work", .1), ("legal", .99), ("work", .99)]
    ]
    result = training._category_acceptance_metrics(
        category="work", rows=rows, predictions=predictions, threshold=.95
    )
    assert result["support"] == 3
    assert result["test_independent_groups"] == 2
    assert result["accepted_hits"] == 1
    assert result["independent_groups"] == 1


def test_heldout_digest_tracks_membership_labels_and_inputs_not_snapshot():
    rows = [dict(account_id="a", stable_message_identity=str(index),
                 category_key="work", important=False, group_key="g",
                 normalized_model_input_hash="a" * 64, snapshot_id="old")
            for index in range(2)]
    digest = training._heldout_test_digest(rows)
    assert digest == training._heldout_test_digest(list(reversed(rows)))
    assert digest == training._heldout_test_digest([{**row, "snapshot_id": "new"} for row in rows])
    assert digest != training._heldout_test_digest(rows[:1])
    for key, value in [("account_id", "b"), ("category_key", "legal"),
                       ("important", True), ("group_key", "other"),
                       ("stable_message_identity", "other"),
                       ("normalized_model_input_hash", "b" * 64)]:
        assert digest != training._heldout_test_digest([{**rows[0], key: value}, rows[1]])
