"""Current business metadata and explicit historical source factories."""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app import business_definitions as business
from app.action_followup_sync_definitions import sync_action_projection
from app.current_schema_metadata import BUSINESS_FIELDS as CURRENT_BUSINESS_FIELDS
from app.current_schema_metadata import SYNC_FIELDS as CURRENT_SYNC_FIELDS


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
    from app.current_schema_declarations import BUSINESS_DECLARATION
    return BUSINESS_DECLARATION


@lru_cache(maxsize=2)
def current_action_objects(reply_variant):
    from app import action_storage as actions
    from app.historical_action_options import FINAL_ACTION_OPTIONS
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
