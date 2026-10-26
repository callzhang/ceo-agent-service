from tests.test_email_legacy_schema import published_source as published_source
import sqlite3

import pytest

from app.store import AutoReplyStore
from tests.workbench_schema_fixtures import fingerprint, SOURCE


@pytest.mark.parametrize("marker", [None, "unknown"])
@pytest.mark.parametrize(
    "table,insert_sql",
    [
        (
            "scheduled_task_runs",
            "insert into scheduled_task_runs(event_id,scheduled_task_id,trigger_kind,scheduled_for) values('kept',7,'manual','instant')",
        ),
        (
            "workbench_tasks",
            "insert into workbench_tasks(id,title,runtime_kind) values('01','kept','codex')",
        ),
        (
            "workbench_turns",
            "insert into workbench_turns(id,task_id,client_request_id,task_sequence,user_text,status) values('01','parent','kept',1,x'00ff','completed')",
        ),
        (
            "workbench_events",
            "insert into workbench_events(turn_id,sequence,event_type,payload_json) values('turn',0,'unknown',x'00ff')",
        ),
    ],
    ids=[
        "scheduled_task_runs",
        "workbench_tasks",
        "workbench_turns",
        "workbench_events",
    ],
)
def test_partial_retired_source_without_known_marker_refuses_before_bootstrap(
    tmp_path, table, insert_sql, marker
):
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as db:
        declarations = SOURCE["tables"][table]["objects"]
        for kind, _, sql in declarations:
            if kind == "table":
                db.execute(sql)
        for kind, _, sql in declarations:
            if kind != "table" and sql:
                db.execute(sql)
        db.execute(insert_sql)
        if marker is not None:
            db.execute(
                "create table service_state(key text primary key,value text,updated_at text)"
            )
            db.execute(
                "insert into service_state values('store_schema_version',?,'kept time')",
                (marker,),
            )
        before = fingerprint(db)
    expected_diagnostic = "unsupported or malformed"
    with pytest.raises(sqlite3.IntegrityError, match=expected_diagnostic):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert fingerprint(db) == before
