"""Completed Email lineage reads retained provenance after input-body retirement."""

import json
import sqlite3

import pytest

from tests.test_email_web_api import _audited_email_detail_fixture, _audited_observability_event
from tests.support.email_source import stored_email_source as _stored_email_source  # noqa: F401


@pytest.mark.parametrize("source", ["valid", "wrong-identity", "malformed"])
def test_completed_lineage_uses_compacted_source_with_exact_bindings(tmp_path, source):
    fixture = _audited_email_detail_fixture(tmp_path)
    with sqlite3.connect(fixture.database) as db:
        row = db.execute(
            "select inputs.id,inputs.input_compacted,inputs.trigger_message_json,"
            "inputs.input_provenance_json from tasks join task_inputs inputs "
            "on inputs.id=tasks.current_input_id and inputs.task_id=tasks.id where tasks.id=?",
            (fixture.task.id,),
        ).fetchone()
        assert row[1:3] == (1, "")
        provenance = json.loads(row[3])
        assert provenance["source"]["action_identity"] == fixture.action_identity
        if source == "wrong-identity":
            provenance["source"]["action_identity"] = "different-action-identity"
        if source != "valid":
            db.execute("update task_inputs set input_provenance_json=? where id=?",
                       ("{malformed" if source == "malformed" else json.dumps(provenance), row[0]))
    event = _audited_observability_event(fixture)
    if source == "valid":
        assert event["consumer_run_ids"] == [fixture.consumer.id]
        assert event["audit_run_ids"] == [fixture.audit.id]
        assert event["task_id"] == fixture.task.id
    else:
        assert event["consumer_run_ids"] == []
        assert event["audit_run_ids"] == []
        assert "task_id" not in event
