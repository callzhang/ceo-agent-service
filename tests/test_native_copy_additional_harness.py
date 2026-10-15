"""Offline additional native cases use a published pair and immutable copy baseline."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts import validate_native_copy_additional as harness
from test_native_copy_lifecycle_harness import old_email_copy  # noqa: F401


@pytest.fixture
def owned_old_copy(request):
    _, historical = request.getfixturevalue("old_email_copy")
    with TemporaryDirectory(prefix="ceo-native-copy.", dir="/tmp") as owned:
        root = Path(owned)
        source = root / "source.sqlite3"
        shutil.copyfile(historical, source)
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        (root / "source-meta.json").write_text(json.dumps({"sha256": original_hash}))
        with patch.dict(os.environ):
            yield root, original_hash
    assert not root.exists()


def test_prepare_publishes_current_pair_before_store_open_and_reserves_task_ids(owned_old_copy):
    from app.email_store import EmailStore
    from app.store import AutoReplyStore

    root, original_hash = owned_old_copy
    store, _, _, metadata, preparation = harness.prepare(root)
    assert metadata["sha256"] == original_hash
    assert preparation["published_pair"] == {
        "store": "2026-10-14.1", "email": 52,
        "action_types": 29, "retired_email_tables": 13,
        "retired_business_parents": 4,
        "retired_provider_tables": 2,
    }
    assert AutoReplyStore(store.path) and EmailStore(store.path)
    baseline = root / "pre-native-target.sqlite3"
    assert baseline.exists()
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == preparation["target_baseline_sha256"]
    with sqlite3.connect(baseline) as db:
        assert db.execute("select max(version) from email_schema_migrations").fetchone() == (52,)
        assert db.execute("select count(*) from sqlite_schema where name='email_actions'").fetchone() == (0,)
    with sqlite3.connect(store.path) as db:
        task_id = db.execute("select seq from sqlite_sequence where name='tasks'").fetchone()[0]
    assert task_id == preparation["task_id_reservation"]
    assert 2**52 <= task_id < 2**53
    assert hashlib.sha256((root / "source.sqlite3").read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize("case", ("scheduled", "work_summary", "workbench"))
def test_main_uses_current_target_baseline_and_high_candidate_task_ids(
    owned_old_copy, monkeypatch, case,
):
    root, original_hash = owned_old_copy

    def fake_case(store, _workspace, _config):
        task = store.ensure_reply_task(
            conversation_id="fixture-" + case, conversation_title="Fixture", single_chat=False,
            trigger_message_id="fixture-" + case,
            trigger_create_time="2026-10-15T00:00:00Z", trigger_sender="Fixture",
            trigger_text="Synthetic offline task",
        )
        assert 2**52 < task.id < 2**53
        return {"case": case, "task_id": task.id, "status": "done"}

    monkeypatch.setattr(harness, case, fake_case)
    monkeypatch.setattr(sys, "argv", ["validate_native_copy_additional", "--owned-temp-root", str(root), "--case", case])
    harness.main()
    result = json.loads((root / "native-result.json").read_text())
    assert result["passed"] is True
    assert result["case"] == case
    assert result["original_tables_unchanged"] > 0
    assert result["published_pair"]["email"] == 52
    assert hashlib.sha256((root / "pre-native-target.sqlite3").read_bytes()).hexdigest() == result["target_baseline_sha256"]
    assert hashlib.sha256((root / "source.sqlite3").read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize("damage", ("change", "delete"))
def test_main_rejects_mutated_or_lost_target_baseline_row(owned_old_copy, monkeypatch, damage):
    root, _ = owned_old_copy

    def corrupt_target(store, _workspace, _config):
        with store._connect() as db:
            if damage == "change":
                db.execute("update service_state set value='damaged' where key='store_schema_version'")
            else:
                db.execute("delete from service_state where key='store_schema_version'")
        return {"case": "workbench"}

    monkeypatch.setattr(harness, "workbench", corrupt_target)
    monkeypatch.setattr(sys, "argv", ["validate_native_copy_additional", "--owned-temp-root", str(root), "--case", "workbench"])
    with pytest.raises(AssertionError, match="service_state"):
        harness.main()
    assert not (root / "native-result.json").exists()
