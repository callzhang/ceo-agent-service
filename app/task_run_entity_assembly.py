"""Deterministic, unactivated Task/Run target schema assembly.

Current native declarations supply unchanged prerequisites. Task, Run and
adoption owners are replaced by their pinned target definitions, and the
Action declaration is regenerated with only the adopted-result source kinds
retired.
"""
from __future__ import annotations

from app.run_adoption_definitions import (
    INDEXES as ADOPTION_INDEXES,
    TABLES as ADOPTION_TABLES,
    TRIGGERS as ADOPTION_TRIGGERS,
    receipt_schema_objects,
)
from app.run_entity_definitions import run_entity_objects
from app.task_run_entity_definitions import task_run_entity_objects

ADOPTED_ACTION_KINDS = (
    "review_candidates", "candidate_reviews", "candidate_selections",
    "candidate_supplements", "candidate_executions", "candidate_execution_alias",
)
RETIRED_ACTION_KINDS = ADOPTED_ACTION_KINDS + ("candidate_action_attempts",)
RETIRED_ACTION_INDEXES = frozenset({
    "idx_actions_consumer_run", "idx_actions_candidates_task", "idx_actions_audit_run",
    "idx_actions_reviews_candidate", "idx_actions_selection_candidate",
    "idx_actions_execution_binding", "idx_actions_execution_alias_candidate",
    "idx_actions_parent_candidate", "idx_actions_parent_review",
    "idx_actions_execution_candidate",
    "idx_actions_attempt_sequence", "idx_actions_external_key",
})

# These retained Action references previously depended on Task/Run discriminator
# columns that do not exist in the unified native owners.
ACTION_PARENT_GUARDS = {
    "trg_actions_agent_run_id_insert": """CREATE TRIGGER trg_actions_agent_run_id_insert BEFORE INSERT ON actions
WHEN NEW.agent_run_id IS NOT NULL AND EXISTS(
 SELECT 1 FROM runs r JOIN run_provenance p ON p.run_id=r.id WHERE r.id=NEW.agent_run_id)
BEGIN SELECT RAISE(ABORT,'action parent type mismatch'); END""",
    "trg_actions_agent_run_id_update": """CREATE TRIGGER trg_actions_agent_run_id_update BEFORE UPDATE OF agent_run_id ON actions
WHEN NEW.agent_run_id IS NOT NULL AND EXISTS(
 SELECT 1 FROM runs r JOIN run_provenance p ON p.run_id=r.id WHERE r.id=NEW.agent_run_id)
BEGIN SELECT RAISE(ABORT,'action parent type mismatch'); END""",
    "trg_actions_fb_decision_id_parent_insert": """CREATE TRIGGER trg_actions_fb_decision_id_parent_insert BEFORE INSERT ON actions
WHEN NEW._kind=49 AND EXISTS(
 SELECT 1 FROM task_run_identity_map WHERE physical_id=NEW.fb_decision_id
 AND source_kind<>'feedback_iteration_decisions')
BEGIN SELECT RAISE(ABORT,'feedback parent type mismatch'); END""",
    "trg_actions_fb_decision_id_parent_update": """CREATE TRIGGER trg_actions_fb_decision_id_parent_update BEFORE UPDATE OF fb_decision_id ON actions
WHEN NEW._kind=49 AND EXISTS(
 SELECT 1 FROM task_run_identity_map WHERE physical_id=NEW.fb_decision_id
 AND source_kind<>'feedback_iteration_decisions')
BEGIN SELECT RAISE(ABORT,'feedback parent type mismatch'); END""",
    "trg_actions_fb_feedback_key_parent_insert": """CREATE TRIGGER trg_actions_fb_feedback_key_parent_insert BEFORE INSERT ON actions
WHEN NEW._kind=49 AND EXISTS(
 SELECT 1 FROM feedback_processing_items i WHERE i.feedback_key=NEW.fb_feedback_key
 AND NOT EXISTS(SELECT 1 FROM task_run_identity_map m WHERE m.source_kind='feedback_processing_items' AND m.public_key=i.feedback_key))
BEGIN SELECT RAISE(ABORT,'feedback parent type mismatch'); END""",
    "trg_actions_fb_feedback_key_parent_update": """CREATE TRIGGER trg_actions_fb_feedback_key_parent_update BEFORE UPDATE OF fb_feedback_key ON actions
WHEN NEW._kind=49 AND EXISTS(
 SELECT 1 FROM feedback_processing_items i WHERE i.feedback_key=NEW.fb_feedback_key
 AND NOT EXISTS(SELECT 1 FROM task_run_identity_map m WHERE m.source_kind='feedback_processing_items' AND m.public_key=i.feedback_key))
BEGIN SELECT RAISE(ABORT,'feedback parent type mismatch'); END""",
    "trg_actions_fb_round_id_parent_insert": """CREATE TRIGGER trg_actions_fb_round_id_parent_insert BEFORE INSERT ON actions
WHEN NEW._kind=49 AND EXISTS(
 SELECT 1 FROM task_run_identity_map WHERE physical_id=NEW.fb_round_id
 AND source_kind<>'feedback_processing_rounds')
BEGIN SELECT RAISE(ABORT,'feedback parent type mismatch'); END""",
    "trg_actions_fb_round_id_parent_update": """CREATE TRIGGER trg_actions_fb_round_id_parent_update BEFORE UPDATE OF fb_round_id ON actions
WHEN NEW._kind=49 AND EXISTS(
 SELECT 1 FROM task_run_identity_map WHERE physical_id=NEW.fb_round_id
 AND source_kind<>'feedback_processing_rounds')
BEGIN SELECT RAISE(ABORT,'feedback parent type mismatch'); END""",
    "trg_actions_td_meeting_alignment_delivery_claims_parent_insert": """CREATE TRIGGER trg_actions_td_meeting_alignment_delivery_claims_parent_insert BEFORE INSERT ON actions
WHEN NEW._kind=40 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_claim_job_id AND kind<>'meeting_alignment')
BEGIN SELECT RAISE(ABORT,'task parent must be a meeting job'); END""",
    "trg_actions_td_meeting_alignment_delivery_claims_parent_update": """CREATE TRIGGER trg_actions_td_meeting_alignment_delivery_claims_parent_update BEFORE UPDATE OF td_claim_job_id ON actions
WHEN NEW._kind=40 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_claim_job_id AND kind<>'meeting_alignment')
BEGIN SELECT RAISE(ABORT,'task parent must be a meeting job'); END""",
    "trg_actions_td_meeting_memory_write_events_parent_insert": """CREATE TRIGGER trg_actions_td_meeting_memory_write_events_parent_insert BEFORE INSERT ON actions
WHEN NEW._kind=41 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_meeting_job_id AND kind<>'meeting_alignment')
BEGIN SELECT RAISE(ABORT,'task parent must be a meeting job'); END""",
    "trg_actions_td_meeting_memory_write_events_parent_update": """CREATE TRIGGER trg_actions_td_meeting_memory_write_events_parent_update BEFORE UPDATE OF td_meeting_job_id ON actions
WHEN NEW._kind=41 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_meeting_job_id AND kind<>'meeting_alignment')
BEGIN SELECT RAISE(ABORT,'task parent must be a meeting job'); END""",
    "trg_actions_td_task_memory_write_events_parent_insert": """CREATE TRIGGER trg_actions_td_task_memory_write_events_parent_insert BEFORE INSERT ON actions
WHEN NEW._kind=42 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_reply_task_id AND kind<>'reply')
BEGIN SELECT RAISE(ABORT,'task parent must be a reply task'); END""",
    "trg_actions_td_task_memory_write_events_parent_update": """CREATE TRIGGER trg_actions_td_task_memory_write_events_parent_update BEFORE UPDATE OF td_reply_task_id ON actions
WHEN NEW._kind=42 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_reply_task_id AND kind<>'reply')
BEGIN SELECT RAISE(ABORT,'task parent must be a reply task'); END""",
    "trg_actions_td_wechat_deliveries_parent_insert": """CREATE TRIGGER trg_actions_td_wechat_deliveries_parent_insert BEFORE INSERT ON actions
WHEN NEW._kind=43 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_reply_task_id AND kind<>'reply')
BEGIN SELECT RAISE(ABORT,'task parent must be a reply task'); END""",
    "trg_actions_td_wechat_deliveries_parent_update": """CREATE TRIGGER trg_actions_td_wechat_deliveries_parent_update BEFORE UPDATE OF td_reply_task_id ON actions
WHEN NEW._kind=43 AND EXISTS(SELECT 1 FROM tasks WHERE id=NEW.td_reply_task_id AND kind<>'reply')
BEGIN SELECT RAISE(ABORT,'task parent must be a reply task'); END""",
}


def _object_name(sql: str) -> str:
    """Read a CREATE object's declared name and validate it through SQLite."""
    tokens = sql.strip().split()
    upper = [token.upper() for token in tokens]
    if upper[:2] == ["CREATE", "TABLE"] or upper[:2] == ["CREATE", "TRIGGER"]:
        return tokens[2]
    if upper[:3] == ["CREATE", "UNIQUE", "INDEX"] or upper[:2] == ["CREATE", "INDEX"]:
        return tokens[3] if upper[:2] == ["CREATE", "UNIQUE"] else tokens[2]
    raise ValueError(f"unsupported schema declaration: {tokens[:3]}")


def _unique_declared_objects(families):
    """Build target declarations without hiding duplicate actual SQLite names."""
    declarations = {}
    for family in families:
        entries = family.items() if hasattr(family, "items") else family
        for declared_name, sql in entries:
            actual_name = _object_name(sql)
            if declared_name != actual_name:
                raise ValueError(f"declared object name mismatch: {declared_name} != {actual_name}")
            if actual_name in declarations:
                raise ValueError(f"duplicate target schema object: {actual_name}")
            declarations[actual_name] = sql
    return declarations


def _action_objects():
    from app import action_storage as actions
    from app.action_payload import ActionPayloads
    from app.business_legacy_fields import CURRENT_SYNC_FIELDS
    from app.current_action_schema import current_action_payloads, RETIRED_ACTION_TYPES
    from app.current_database_schema import FINAL_ACTION_OPTIONS

    options = {key: value for key, value in FINAL_ACTION_OPTIONS.items()
               if key not in {"canonical_email", "sent_result_references"}}
    retired_types = RETIRED_ACTION_TYPES | frozenset(RETIRED_ACTION_KINDS)
    original_payloads = current_action_payloads()
    base_types = original_payloads.record_types

    class RetainedActionTypes:
        names = tuple(name for name in base_types.names if name not in retired_types)

        @staticmethod
        def code(name):
            return base_types.code(name)

        @staticmethod
        def declarations(kinds=None):
            return base_types.declarations(RetainedActionTypes.names if kinds is None else kinds)

        @staticmethod
        def trigger_sql(sql):
            return base_types.trigger_sql(sql)

    payloads = ActionPayloads(
        {kind: layout for kind, layout in original_payloads.layouts.items() if kind not in retired_types},
        RetainedActionTypes,
    )
    options["execution_owner"] = True
    table, indexes, triggers = actions._action_definitions(
        reply_variant="fresh_memory_fk", **options, payloads=payloads,
        sync_fields=CURRENT_SYNC_FIELDS, canonical_attempt_fields=True,
        retired_types=retired_types,
    )
    table = table.replace("references email_classifications(id)", "references emails(classification_id)")
    table = table.replace(
        'references tasks("feedback_iteration_decisions__id")',
        "references feedback_iteration_decisions(id)",
    ).replace(
        'references tasks("feedback_processing_items__feedback_key")',
        "references feedback_processing_items(feedback_key)",
    ).replace(
        'references tasks("feedback_processing_rounds__id")',
        "references feedback_processing_rounds(id)",
    )
    rows = [("actions", table)]
    rows.extend((_object_name(sql), sql) for sql in (*indexes, *triggers))
    rows = [(name, sql) for name, sql in rows if name not in RETIRED_ACTION_INDEXES]
    for name, sql in ACTION_PARENT_GUARDS.items():
        rows = [(key, value) for key, value in rows if key != name]
        rows.append((name, sql))
    return dict(rows)


def task_run_entity_assembly_objects():
    """Return target DDL plus actual retained-owner declarations.

    The scratch schema uses only target Task/Run declarations and the native
    individual creators for unchanged owners. It deliberately does not call
    the current whole-database factory or source manifest, both of which
    instantiate the old Task/Run factories.
    """
    import sqlite3
    from app.action_entity_definitions import ACTION_IDENTITY_MAP_DDL
    from app.business_legacy_fields import current_business_declaration
    from app.business_schema_release import _create_auxiliary
    from app.configs_storage import create_configs_schema
    from app.email_canonical_storage import _create_emails
    from app.current_schema_manifest import EMAIL_LEDGER_SQL
    from app.provenance_storage import create_provenance_schema
    from app.provider_state_storage import create_provider_state_schema
    from app.schedules_storage import create_schedules_schema
    from app.state_storage import create_state_schema
    from app.task8_storage import register_task8_functions

    declarations = _unique_declared_objects((
        task_run_entity_objects(), run_entity_objects(), ADOPTION_TABLES,
        ADOPTION_INDEXES, ADOPTION_TRIGGERS, receipt_schema_objects(), _action_objects(),
        ((_object_name(sql), sql) for sql in ACTION_IDENTITY_MAP_DDL),
    ))
    db = sqlite3.connect(":memory:")
    register_task8_functions(db)
    # Install target-owned tables first so retained native creators can keep
    # their real FK declarations without substituting fake owner tables.
    for name, sql in declarations.items():
        if sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
    # Retained native owner factories from current_database_schema.py, with
    # only its Task/Run factories and their child guards intentionally omitted.
    create_schedules_schema(db)
    create_configs_schema(db)
    create_state_schema(db)
    create_provider_state_schema(db)
    create_provenance_schema(db)
    _create_emails(db, metadata_owner=True, action_owner=True)
    business = current_business_declaration()
    db.execute(business["sql"])
    _create_auxiliary(db, business)
    db.execute(EMAIL_LEDGER_SQL)
    # Install the target-owned indexes and triggers after every owner exists.
    for name, sql in declarations.items():
        if not sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
    rows = db.execute(
        "SELECT name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
    ).fetchall()
    result = {name: sql for name, sql in rows}
    db.close()
    return result


def validate_assembled_object_names(db):
    actual = {name for (name,) in db.execute(
        "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'")}
    expected = set(task_run_entity_assembly_objects())
    if actual != expected:
        raise ValueError("assembled DDL object names differ from executed SQLite schema")
