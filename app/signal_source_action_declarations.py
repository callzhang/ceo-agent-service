"""Historical Action declaration used only by Signal source/inverse tooling."""

from app.task_run_entity_assembly import _object_name

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
RETIRED_CANDIDATE_REFERENCE_GUARDS = frozenset({"trg_actions_public_reference_update"})

# These retained Action references previously depended on Task/Run discriminator
# columns that do not exist in the unified native owners. The Feedback parents
# are enforced by their translated native foreign keys and therefore have no
# source-identity-map guards in the assembled target.
RETIRED_SOURCE_FEEDBACK_PARENT_GUARDS = frozenset({
    "trg_actions_fb_decision_id_parent_insert",
    "trg_actions_fb_decision_id_parent_update",
    "trg_actions_fb_feedback_key_parent_insert",
    "trg_actions_fb_feedback_key_parent_update",
    "trg_actions_fb_round_id_parent_insert",
    "trg_actions_fb_round_id_parent_update",
})

ACTION_PARENT_GUARDS = {
    "trg_actions_agent_run_id_insert": """CREATE TRIGGER trg_actions_agent_run_id_insert BEFORE INSERT ON actions
WHEN NEW.agent_run_id IS NOT NULL AND EXISTS(
 SELECT 1 FROM runs r JOIN run_provenance p ON p.run_id=r.id WHERE r.id=NEW.agent_run_id)
BEGIN SELECT RAISE(ABORT,'action parent type mismatch'); END""",
    "trg_actions_agent_run_id_update": """CREATE TRIGGER trg_actions_agent_run_id_update BEFORE UPDATE OF agent_run_id ON actions
WHEN NEW.agent_run_id IS NOT NULL AND EXISTS(
 SELECT 1 FROM runs r JOIN run_provenance p ON p.run_id=r.id WHERE r.id=NEW.agent_run_id)
BEGIN SELECT RAISE(ABORT,'action parent type mismatch'); END""",
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


def source_action_objects():
    from app import action_storage as actions
    from app.action_payload import ActionPayloads
    from app.business_legacy_fields import CURRENT_SYNC_FIELDS
    from app.current_action_schema import (
        RETIRED_ACTION_TYPES,
        _signal_source_action_payloads,
    )
    from app.historical_action_options import FINAL_ACTION_OPTIONS

    options = {key: value for key, value in FINAL_ACTION_OPTIONS.items()
               if key not in {"canonical_email", "sent_result_references"}}
    retired_types = RETIRED_ACTION_TYPES | frozenset(RETIRED_ACTION_KINDS)
    original_payloads = _signal_source_action_payloads()
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
    rows = [(name, sql) for name, sql in rows
            if name not in (RETIRED_ACTION_INDEXES | RETIRED_SOURCE_FEEDBACK_PARENT_GUARDS
                            | RETIRED_CANDIDATE_REFERENCE_GUARDS)]
    for name, sql in ACTION_PARENT_GUARDS.items():
        rows = [(key, value) for key, value in rows if key != name]
        rows.append((name, sql))
    return dict(rows)


