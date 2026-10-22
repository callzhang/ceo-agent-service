from contextlib import closing
import sqlite3
import pytest
from app.action_storage import create_action_schema
from app.configs_definitions import CONFIG_FIELDS, CONFIG_PROJECTIONS, CONFIG_FRESH_PROJECTIONS, config_column
from app.configs_storage import create_configs_schema, insert_config
from app.schedules_storage import create_schedules_schema, insert_schedule
from tests.configs_source_fixtures import insert as source_insert, source_db, ROOT
import json

SYNTHETIC_CONFIG_ROWS = {
    "managed_skills": {"id": 20, "name": "skill-20", "display_name": "Skill 20", "created_at": "2026-10-15 00:00:00"},
    "managed_skill_revisions": {"id": 200, "skill_id": 20, "revision_number": 1,
        "content": "# Skill 20", "sha256": "digest-é", "parent_revision_id": None,
        "source": "test", "created_at": "2026-10-15 00:00:00"},
    "runtime_skill_configs": {"id": 300, "parent_id": None, "status": "pending_restart",
        "created_at": "2026-10-15 00:00:00"},
    "scheduled_task_config_versions": {"id": 400, "snapshot_json": "not-json"},
    "runtime_feedback_iteration_capabilities": {"config_id": 300, "enabled": 1,
        "created_at": "2026-10-15 00:00:00"},
    "runtime_skill_bindings": {"config_id": 300, "skill_id": 20, "revision_id": 200,
        "enabled": 1, "load_order": 0, "purpose": ""},
    "scheduled_task_skill_refs": {"scheduled_task_id": 500, "skill_source": "managed",
        "skill_name": "skill-20", "managed_skill_id": 20, "managed_revision_id": 200,
        "position": 0},
    "wechat_reply_scopes": {"account_id": "wx", "target_type": "group",
        "target_id": "group-1", "conversation_id": "", "display_name": "Group",
        "trigger_mode": "always", "enabled": 1, "binding_status": "unverified",
        "binding_evidence_json": "{}", "disabled_reason": "", "last_discovered_at": "",
        "updated_at": "2026-10-15 00:00:00"},
}
assert sum(map(len, SYNTHETIC_CONFIG_ROWS.values())) == 45


INSERT_ORDER = (
    "managed_skills", "managed_skill_revisions", "runtime_skill_configs",
    "scheduled_task_config_versions", "runtime_feedback_iteration_capabilities",
    "runtime_skill_bindings", "scheduled_task_skill_refs", "wechat_reply_scopes",
)
SCHEDULE_ROW = {"id": 500, "name": "cron-500", "prompt": "p",
                "cron_expression": "* * * * *", "timezone": "UTC",
                "runtime_id": "claude", "enabled": 1}

@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_eight_kinds_all_45_source_fields_and_storage_classes(variant):
    with source_db(variant) as source, closing(sqlite3.connect(":memory:")) as target:
        target.execute("pragma foreign_keys=on")
        create_schedules_schema(target)
        business = json.loads((ROOT / "docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json").read_text())["actual"]["tables"]
        for name in ("business_tasks", "business_task_signals"):
            target.execute(business[name]["sql"])
        create_action_schema(target, include_reply=True,
                             reply_variant="fresh_memory_fk", include_email=True,
                             include_followup_sync=True,
                             include_skill_config_receipts=True)
        create_configs_schema(target)
        source.execute("begin")
        target.execute("begin")
        cols = tuple(SCHEDULE_ROW)
        source.execute("insert into scheduled_tasks(" + ",".join(cols) + ") values(" +
                       ",".join("?" for _ in cols) + ")", tuple(SCHEDULE_ROW.values()))
        insert_schedule(target, SCHEDULE_ROW)
        for kind in INSERT_ORDER:
            row = SYNTHETIC_CONFIG_ROWS[kind]
            source_insert(source, kind, row)
            insert_config(target, kind, row)
            names = tuple(CONFIG_FIELDS[kind])
            projection = (CONFIG_PROJECTIONS if variant == "actual"
                          else CONFIG_FRESH_PROJECTIONS)[kind]
            expected = source.execute("select " + ",".join(names) + " from " + kind).fetchone()
            actual = target.execute(projection).fetchone()
            assert actual == expected, kind
            for field in names:
                source_type = source.execute("select typeof(" + field + ") from " + kind).fetchone()[0]
                target_type = target.execute(
                    "select typeof(" + config_column(kind, field) + ") from configs "
                    "where record_type=?", (kind,)
                ).fetchone()[0]
                assert target_type == source_type, (kind, field)
        source.rollback()
        target.rollback()

def test_all_fifteen_kind_columns():
    frozen = json.loads((ROOT / 'docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json').read_text())
    assert set(CONFIG_FIELDS) == set(frozen['groups']['configs'])
    assert sum(map(len, CONFIG_FIELDS.values())) == 118
    for kind, fields in CONFIG_FIELDS.items():
        assert fields == tuple(c['name'] for c in frozen['actual']['tables'][kind]['columns'])
        assert kind in CONFIG_PROJECTIONS
