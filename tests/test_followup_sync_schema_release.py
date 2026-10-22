from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app import action_email_schema_release as release_module
from app.action_storage import action_schema_is_current, insert_sync_action
from tests.test_email_action_schema_release import (
    _fingerprint,
    _prepare,
    source as paired_email_source,
)
from test_followup_sync_source import CAPTURE as SYNC_SOURCE_CAPTURE


SOURCE_TABLES = (
    "work_updates",
    "follow_up_drafts",
    "follow_up_send_attempts",
    "business_task_follow_ups",
    "business_task_follow_up_send_attempts",
    "task_todo_sync_outbox",
    "business_task_todo_sync_outbox",
    "work_todo_dingtalk_links",
    "business_task_dingtalk_links",
    "business_task_events",
)


@pytest.fixture(params=("actual", "fresh_candidate"))
def paired_source(tmp_path, request):
    path = paired_email_source.__wrapped__(
        tmp_path, SimpleNamespace(param=request.param)
    )
    variant = "actual" if request.param == "actual" else "fresh_candidate"
    snapshot = (SYNC_SOURCE_CAPTURE["actual"] if variant == "actual" else
                SYNC_SOURCE_CAPTURE["fresh_production_constructor"]["snapshot"])
    event_source = json.loads(
        (Path(__file__).parents[1] / "docs/superpowers/specs/"
         "2026-10-17-action-task-events-source-extension.json").read_text()
    )[variant]
    with closing(sqlite3.connect(path)) as db:
        # The approved Business slice retires this cache body. Keep this
        # release fixture on the empty historical value so its inverse oracle
        # measures only schema and retained facts.
        db.execute("update work_projects set memory_context_json='{}'")
        for name in SOURCE_TABLES:
            db.execute(f'drop table if exists "{name}"')
        for name in SOURCE_TABLES:
            declaration = (event_source if name == "business_task_events"
                           else snapshot["tables"][name])
            db.execute(declaration["sql"])
        for name in SOURCE_TABLES:
            declaration = (event_source if name == "business_task_events"
                           else snapshot["tables"][name])
            for index in reversed(declaration["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        # Config migration creates these namespaced Actions allocators before
        # the follow-up source capture. Keep real rows so the paired release
        # exercises their inverse watermarks as well as their DDL.
        db.execute(
            "insert into managed_skills(id,name,display_name,created_at) "
            "values(20,'sync-proof','Sync proof','2026-10-17T00:00:00Z')"
        )
        db.execute(
            "insert into managed_skill_revisions(id,skill_id,revision_number,content,"
            "sha256,parent_revision_id,source,created_at) "
            "values(200,20,1,'body','digest',null,'test','2026-10-17T00:00:00Z')"
        )
        db.execute(
            "insert into runtime_skill_configs(id,parent_id,status,created_at) "
            "values(300,null,'pending_restart','2026-10-17T00:00:00Z')"
        )
        db.execute(
            "insert into managed_skill_export_receipts(id,revision_id,sha256,path,created_at) "
            "values(7001,200,'digest','/skill','2026-10-17T00:00:00Z')"
        )
        db.execute(
            "insert into runtime_skill_load_receipts(id,config_id,pid,loaded_json,error,created_at) "
            "values(9001,300,7,'{}','','2026-10-17T00:00:00Z')"
        )
        db.commit()
    return path


@pytest.fixture
def paired_actual_business_source(tmp_path):
    """Exercise the real Business actual declaration in the paired release."""
    path = paired_source.__wrapped__(tmp_path, SimpleNamespace(param="actual"))
    from app.business_schema_release import _drop_source, _recreate_source
    from tests.test_business_schema_release import _source_declarations

    business_source = _source_declarations("actual")
    frozen = SYNC_SOURCE_CAPTURE["actual"]
    event_source = json.loads(
        (Path(__file__).parents[1] / "docs/superpowers/specs/"
         "2026-10-17-action-task-events-source-extension.json").read_text()
    )["actual"]
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        _drop_source(db)
        _recreate_source(db, business_source)
        for name in SOURCE_TABLES:
            db.execute(f'drop table if exists "{name}"')
        for name in SOURCE_TABLES:
            declaration = event_source if name == "business_task_events" else frozen["tables"][name]
            db.execute(declaration["sql"])
        for name in SOURCE_TABLES:
            declaration = event_source if name == "business_task_events" else frozen["tables"][name]
            for index in reversed(declaration["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        db.commit()
    return path


def test_paired_release_migrates_all_sync_families_and_exactly_inverses(
    paired_source,
) -> None:
    from app.business_schema_release import business_schema_is_current

    original = _fingerprint(paired_source)
    release = _prepare(paired_source)

    release.apply()

    with closing(sqlite3.connect(paired_source)) as db:
        assert business_schema_is_current(db, include_followup_sync=True)
        assert action_schema_is_current(
            db,
            include_reply=True,
            reply_variant=release.source_schema["action_variant"],
            include_email=True,
            include_skill_config_receipts=True,
            include_followup_sync=True,
            include_business_sync=True,
        )
        assert not db.execute(
            "select 1 from sqlite_schema where type='table' and name in ("
            + ",".join("?" for _ in SOURCE_TABLES)
            + ") limit 1",
            SOURCE_TABLES,
        ).fetchone()
        assert tuple(db.execute(
            "select name,seq from sqlite_sequence where name in "
            "('actions:managed_skill_export_receipts','actions:runtime_skill_load_receipts') "
            "order by name"
        )) == (
            ("actions:managed_skill_export_receipts", 7001),
            ("actions:runtime_skill_load_receipts", 9001),
        )

    release.rollback()

    assert _fingerprint(paired_source) == original


def test_paired_release_accepts_actual_business_source_and_exactly_inverses(
    paired_actual_business_source,
) -> None:
    from app.business_schema_release import business_schema_is_current

    original = _fingerprint(paired_actual_business_source)
    release = _prepare(paired_actual_business_source)
    assert release.source_schema["business"]["variant"] == "actual"

    release.apply()

    with closing(sqlite3.connect(paired_actual_business_source)) as db:
        assert business_schema_is_current(db, "actual", include_followup_sync=True)
        assert business_schema_is_current(db, include_followup_sync=True)

    release.rollback()
    assert _fingerprint(paired_actual_business_source) == original


def test_paired_release_inverse_keeps_a_new_sync_fact(paired_source) -> None:
    release = _prepare(paired_source)
    release.apply()
    with closing(sqlite3.connect(paired_source)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        action_id = insert_sync_action(
            db,
            "work_updates",
            {
                "id": 9017,
                "project_id": 0,
                "source_type": "console",
                "source_ref": "paired-current-fact",
                "summary": "current fact",
                "changes_json": "{}",
                "merge_reason": "",
                "confidence": 0.5,
                "created_at": "2026-10-17T00:00:00Z",
            },
        ).lastrowid
        db.commit()

    release.rollback()

    with closing(sqlite3.connect(paired_source)) as db:
        row = db.execute(
            "select id,source_type,source_ref,summary from work_updates where id=?",
            (int(action_id),),
        ).fetchone()
    assert row == (9017, "console", "paired-current-fact", "current fact")


def test_paired_release_rolls_back_when_sync_conversion_fails(
    paired_source, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _fingerprint(paired_source)
    release = _prepare(paired_source)

    def fail_after_sync_source_capture(_db, _source):
        raise RuntimeError("injected follow-up conversion failure")

    monkeypatch.setattr(
        release_module, "migrate_followup_sync_schema", fail_after_sync_source_capture
    )
    with pytest.raises(RuntimeError, match="injected follow-up conversion failure"):
        release.apply()

    assert _fingerprint(paired_source) == original
