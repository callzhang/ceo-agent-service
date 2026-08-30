from pathlib import Path

import pytest

from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_contracts import EmailCategory
from app.email_classifier_scan import EmailScanConfig
from app.email_store import EmailStore
from app.email_classifier_runtime import (
    EmailClassifierUnavailable,
    load_active_classifier,
    scan_with_active_model,
)


def _model() -> CpuTfidfLogisticClassifier:
    return CpuTfidfLogisticClassifier(model_version="runtime-test").fit(
        ["work project", "work meeting", "junk promotion", "junk offer"],
        ["work", "work", "junk", "junk"],
    )


def test_runtime_prefers_active_model(tmp_path: Path):
    active = tmp_path / "model.active.pkl"
    previous = tmp_path / "model.previous.pkl"
    _model().save(active)
    _model().save(previous)

    loaded = load_active_classifier(active, previous)

    assert loaded.path == active
    assert loaded.used_previous is False
    assert loaded.classifier.model_version == "runtime-test"


def test_runtime_falls_back_to_previous_when_active_is_invalid(tmp_path: Path):
    active = tmp_path / "model.active.pkl"
    previous = tmp_path / "model.previous.pkl"
    active.write_bytes(b"not a model")
    _model().save(previous)

    loaded = load_active_classifier(active, previous)

    assert loaded.path == previous
    assert loaded.used_previous is True


def test_runtime_fails_explicitly_when_no_model_is_valid(tmp_path: Path):
    with pytest.raises(EmailClassifierUnavailable, match="no valid"):
        load_active_classifier(
            tmp_path / "model.active.pkl", tmp_path / "model.previous.pkl"
        )


class FakeReadonlySource:
    def fetch_recent(self, mailbox: str = "INBOX", *, limit: int = 50):
        return [
            {
                "messageId": "message-1",
                "accountId": "test-account",
                "folder": mailbox,
                "uidValidity": 1,
                "uid": 1,
                "from": {"email": "team@example.com"},
                "subject": "work project",
                "textBody": "work meeting",
            }
        ]


def test_runtime_loads_model_and_runs_only_readonly_scan(tmp_path: Path):
    active = tmp_path / "model.active.pkl"
    _model().save(active)
    config = EmailScanConfig(
        config_version="runtime-scan-v1",
        thresholds={category: 0.0 for category in EmailCategory},
        actions={},
    )

    result = scan_with_active_model(
        FakeReadonlySource(),
        EmailStore(tmp_path / "email.sqlite3"),
        config,
        active_path=active,
        previous_path=tmp_path / "model.previous.pkl",
        limit=1,
    )

    assert result.loaded.path == active
    assert result.scan.persisted_count == 1
