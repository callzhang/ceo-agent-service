from datetime import datetime, timezone
import json

import pytest

from app.email_experiment_snapshot import (
    SNAPSHOT_VERSION,
    EmailExperimentSnapshotError,
    build_snapshot,
    deterministic_payload_digest,
    load_snapshot,
    save_snapshot,
)


CAPTURED_AT = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)


def _examples():
    return [
        {
            "message_id": "account-a:imap:INBOX:1:101",
            "model_text": "__from_domain__billing.example.test __subject__发票 确认",
            "label": "billing",
            "received_at": "2026-08-30T12:00:00+00:00",
            "source_group": "billing.example.test",
        },
        {
            "message_id": "account-b:imap:INBOX:1:202",
            "model_text": "__from_domain__alerts.example.test __subject__安全 告警",
            "label": "notification",
            "received_at": "2026-08-31T08:00:00+00:00",
            "source_group": "alerts.example.test",
        },
    ]


def test_snapshot_round_trips_only_redacted_training_fields(tmp_path):
    snapshot = build_snapshot(
        _examples(),
        captured_at=CAPTURED_AT,
        label_source="assistant_authorized_manual_annotation",
    )
    path = tmp_path / "experiment.json"

    save_snapshot(path, snapshot)
    loaded = load_snapshot(path)

    assert loaded == snapshot
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["snapshot_version"] == SNAPSHOT_VERSION
    assert payload["label_source"] == "assistant_authorized_manual_annotation"
    assert payload["sample_count"] == 2
    assert payload["snapshot_digest"] == snapshot.snapshot_digest
    serialized = json.dumps(payload, ensure_ascii=False)
    assert '"message_id":' not in serialized
    assert '"source_group":' not in serialized
    assert payload["examples"][0] == {
        "label": "billing",
        "model_text": "__from_domain__billing.example.test __subject__发票 确认",
        "received_date": "2026-08-30",
        "sample_id_digest": payload["examples"][0]["sample_id_digest"],
        "source_group_digest": payload["examples"][0]["source_group_digest"],
    }


def test_snapshot_rejects_raw_provider_fields_and_unredacted_features():
    with pytest.raises(EmailExperimentSnapshotError, match="unsupported field"):
        build_snapshot(
            [
                {
                    **_examples()[0],
                    "subject": "raw subject",
                }
            ],
            captured_at=CAPTURED_AT,
            label_source="test",
        )

    with pytest.raises(EmailExperimentSnapshotError, match="redacted"):
        build_snapshot(
            [
                {
                    **_examples()[0],
                    "model_text": "sender@example.com https://private.example/message",
                }
            ],
            captured_at=CAPTURED_AT,
            label_source="test",
        )

    with pytest.raises(EmailExperimentSnapshotError, match="redacted"):
        build_snapshot(
            [
                {
                    **_examples()[0],
                    "model_text": "__subject__access token qrp_ExampleSecret123456",
                }
            ],
            captured_at=CAPTURED_AT,
            label_source="test",
        )


def test_snapshot_rejects_tampered_digest(tmp_path):
    snapshot = build_snapshot(
        _examples(),
        captured_at=CAPTURED_AT,
        label_source="test",
    )
    path = tmp_path / "experiment.json"
    save_snapshot(path, snapshot)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["examples"][0]["label"] = "junk"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EmailExperimentSnapshotError, match="digest"):
        load_snapshot(path)


def test_deterministic_payload_digest_ignores_mapping_insertion_order():
    assert deterministic_payload_digest({"b": 2, "a": [1, {"d": 4, "c": 3}]}) == (
        deterministic_payload_digest({"a": [1, {"c": 3, "d": 4}], "b": 2})
    )
