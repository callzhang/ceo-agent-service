"""Current legacy history fields; historical declarations remain immutable.

These nine controls/assessments were explicitly retired without value backups.
Only schema inverse supplies the approved neutral defaults.
"""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app import business_definitions as business
from app.action_followup_sync_definitions import SYNC_FIELDS, sync_action_projection


RETIRED_DEFAULTS = {
    "work_project": {
        "priority": "none", "risk_level": "none", "needs_derek_attention": 0,
        "next_follow_up_at": "", "follow_up_mode": "none",
    },
    "work_todo": {"priority": "none", "next_follow_up_at": ""},
    "work_updates": {"merge_reason": "", "confidence": 0},
}
RETIRED_INDEXES = {
    "idx_business_work_project_status_priority", "idx_business_work_todo_follow_up",
}
CURRENT_BUSINESS_FIELDS = {
    kind: tuple(field for field in fields if field not in RETIRED_DEFAULTS.get(kind, {}))
    for kind, fields in business.BUSINESS_FIELDS.items()
}
CURRENT_SYNC_FIELDS = {
    kind: tuple(field for field in fields if field not in RETIRED_DEFAULTS.get(kind, {}))
    for kind, fields in SYNC_FIELDS.items()
}
CURRENT_BUSINESS_PROJECTIONS = {
    kind: business._projection(kind, fields) for kind, fields in CURRENT_BUSINESS_FIELDS.items()
}


def current_sync_projection(kind, *, indexed_public_id=False):
    return sync_action_projection(kind, indexed_public_id=indexed_public_id,
                                  fields=CURRENT_SYNC_FIELDS[kind])


CURRENT_SYNC_PROJECTIONS = {kind: current_sync_projection(kind) for kind in CURRENT_SYNC_FIELDS}


def current_business_payload_fields(kind):
    return tuple(field for field in CURRENT_BUSINESS_FIELDS[kind]
                 if field != "id" and field not in business.BUSINESS_COMMON)


def retained_action_payloads(source):
    from app.action_payload import ActionPayloads
    from app.storage_payload import PayloadLayout
    layouts = dict(source.layouts)
    layouts["work_updates"] = PayloadLayout(CURRENT_SYNC_FIELDS["work_updates"][1:])
    return ActionPayloads(layouts, source.record_types)


@lru_cache(maxsize=1)
def current_business_declaration():
    from app.action_reply_storage import _source_declaration
    from app.business_legacy_owner_storage import OWNER_COLUMN
    table, indexes, triggers = business._definitions(
        include_candidates=True, include_business_actions=True, fields=CURRENT_BUSINESS_FIELDS,
    )
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(table)
        for sql in (*indexes, *triggers):
            if sql.split(" on business", 1)[0].split()[-1] not in RETIRED_INDEXES:
                db.execute(sql)
        db.execute("alter table business add column " + OWNER_COLUMN)
        return _source_declaration(db, "business")


@lru_cache(maxsize=2)
def current_action_objects(reply_variant):
    from app import action_storage as actions
    from app.current_database_schema import FINAL_ACTION_OPTIONS
    options = {key: value for key, value in FINAL_ACTION_OPTIONS.items() if key != "canonical_email"}
    payloads = retained_action_payloads(actions.action_payloads(
        reply_variant, physical_provenance=True, selection_references=True,
        execution_owner=True, shared_lifecycle=True, specialized_claims=True, postfix_owner=True,
    ))
    table, indexes, triggers = actions._action_definitions(
        reply_variant=reply_variant, **options, payloads=payloads, sync_fields=CURRENT_SYNC_FIELDS,
    )
    table = table.replace("references email_classifications(id)", "references emails(classification_id)")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table _allocator(id integer primary key autoincrement)")
        db.execute("drop table _allocator")
        for sql in (table, *indexes, *triggers):
            db.execute(sql)
        return actions._objects(db, "actions")
