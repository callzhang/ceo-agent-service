"""Exact current and immediately previous sources for the explicit deploy window.

Normal runtime readers use the current declaration. Historical publication tools
remain in their versioned release modules and are not runtime fallbacks.
"""

from app.current_schema_manifest import STORE_VERSION as TARGET_STORE_VERSION, EMAIL_VERSION as TARGET_EMAIL_VERSION, schema_is_current, schema_objects
from app.state_definitions import state_projection

PREVIOUS_STORE_VERSION = '2026-10-25.3'
PREVIOUS_STORE_VERSIONS = (PREVIOUS_STORE_VERSION,)


def _version(db, *, release_source_email_version=None):
    marker = db.execute(f"select value from ({state_projection('service_state')}) "
                        "where key='store_schema_version'").fetchone()
    version = marker[0] if marker else None
    ledger = db.execute("select 1 from sqlite_schema where type='table' and name='email_schema_migrations'").fetchone()
    email_version = (
        db.execute('select max(version) from email_schema_migrations').fetchone()[0]
        if ledger else None
    )
    if ledger and email_version != TARGET_EMAIL_VERSION and email_version != release_source_email_version:
        raise ValueError('unsupported mixed Email publication version')
    if version == TARGET_STORE_VERSION and schema_is_current(db):
        return version
    if version == PREVIOUS_STORE_VERSION:
        from app.scheduled_task_notification_schema_source import previous_current_objects
        email_ledger = ledger is not None
        if (email_version == TARGET_EMAIL_VERSION
                and schema_objects(db) == previous_current_objects(email_ledger=email_ledger)):
            return version
    raise ValueError('unsupported current publication declaration; explicit source migration required')


def current_email_actions_available(db):
    if _version(db) != TARGET_STORE_VERSION:
        raise ValueError('unsupported current Email publication declaration')
    return db.execute("select 1 from sqlite_schema where type='table' and name='email_schema_migrations'").fetchone() is not None


def publication_run_sources(db, *, release_source_email_version=None):
    version = _version(db, release_source_email_version=release_source_email_version)
    if version in (TARGET_STORE_VERSION, PREVIOUS_STORE_VERSION):
        from app.store import NATIVE_AGENT_RUN_SQL
        return NATIVE_AGENT_RUN_SQL, (
            "select runtime_attempts.*,runtime_attempts.parent_id as agent_run_id "
            "from runtime_attempts"
        )
    from app.logical_run_storage import AGENT_RUN_SQL
    from app.run_storage import RUNTIME_ATTEMPT_SQL
    return AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL


def publication_task_sources(db, *, release_source_email_version=None):
    version = _version(db, release_source_email_version=release_source_email_version)
    if version in (TARGET_STORE_VERSION, PREVIOUS_STORE_VERSION):
        from app.store import NATIVE_REPLY_TASK_CURRENT_SQL, WORK_SUMMARY_INPUT_SQL
        return NATIVE_REPLY_TASK_CURRENT_SQL, WORK_SUMMARY_INPUT_SQL
    from app.task20_definitions import task20_projection
    return task20_projection('reply_tasks'), task20_projection('work_summary_inputs')


def publication_deployment_task_sources(db, *, release_source_email_version=None):
    """Select quiet-window task sources for the declared Store release."""
    version = _version(db, release_source_email_version=release_source_email_version)
    if version in (TARGET_STORE_VERSION, PREVIOUS_STORE_VERSION):
        return (
            "select id,status,lease_owner,lease_expires_at from tasks where kind='workbench_turn'",
            "select id,status,lease_owner,lease_expires_at from tasks where kind='okr_analysis'",
            "select id,status,lease_owner,lease_expires_at from tasks where kind='email_classification'",
        )
    from app.task20_definitions import task20_projection
    email_classification_projection = task20_projection('email_agent_classification_tasks')
    return (task20_projection('workbench_turns'),
            task20_projection('weekly_okr_analysis_jobs'),
            "select task_id as id,status,owner as lease_owner,lease_expires_at "
            "from (" + email_classification_projection + ")")


def publication_meeting_sources(db, *, release_source_email_version=None):
    version = _version(db, release_source_email_version=release_source_email_version)
    if version in (TARGET_STORE_VERSION, PREVIOUS_STORE_VERSION):
        from app.store import NATIVE_MEETING_RUN_SQL
        return (
            "select tasks.* from tasks "
            "where tasks.kind='meeting_alignment'",
            NATIVE_MEETING_RUN_SQL,
        )
    from app.task20_definitions import task20_projection
    from app.logical_run_storage import logical_run_projection
    return task20_projection('meeting_alignment_jobs'), logical_run_projection('meeting_alignment_runs')


def publication_scheduled_source(db, *, release_source_email_version=None):
    version = _version(db, release_source_email_version=release_source_email_version)
    if version in (TARGET_STORE_VERSION, PREVIOUS_STORE_VERSION):
        from app.scheduled_occurrence_storage import occurrence_query
        return occurrence_query()
    from app.run_storage import SCHEDULED_RUN_SQL
    return SCHEDULED_RUN_SQL


def publication_dispatcher_source(db, **_publication_options):
    _version(
        db,
        release_source_email_version=_publication_options.get("release_source_email_version"),
    )
    from app.task_lifecycle_storage import claim_projection
    from app.task_lifecycle_definitions import DISPATCH_KINDS
    fields = ('adapter_name','source_id','owner','owner_pid','generation','lease_expires_at',
              'terminal_at','last_error','created_at','updated_at')
    columns = ','.join(fields)
    return ('select '+columns+',1 as claim_active from ('+claim_projection()+') '
            'union all select '+columns+",coalesce(terminal_at,'')='' as claim_active from ("
            + state_projection('dispatcher_claim_leases')+') where adapter_name not in ('
            + ','.join(map(repr,DISPATCH_KINDS))+')')


def publication_candidate_source(db):
    version = _version(db)
    if version in (TARGET_STORE_VERSION, PREVIOUS_STORE_VERSION):
        from app.store import NATIVE_REVIEW_CANDIDATE_SQL
        return NATIVE_REVIEW_CANDIDATE_SQL
    from app.action_storage import REVIEW_CANDIDATE_SQL
    return REVIEW_CANDIDATE_SQL


def publication_receipt_sources(db):
    version = _version(db)
    from app.action_storage import ACTION_PROJECTIONS, EXTERNAL_ACTION_RESULT_SQL, RECEIPT_ACTION_TYPES
    external_result = EXTERNAL_ACTION_RESULT_SQL
    if version in (TARGET_STORE_VERSION, PREVIOUS_STORE_VERSION):
        from app.action_storage import NATIVE_EXTERNAL_ACTION_RESULT_SQL
        external_result = NATIVE_EXTERNAL_ACTION_RESULT_SQL
    return {
        kind: (external_result if kind == 'external_action_results'
               else ACTION_PROJECTIONS[kind])
        for kind in RECEIPT_ACTION_TYPES
    }


def publication_delivery_sources(db):
    _version(db)
    from app.action_storage import (ACTION_PROJECTIONS, DELIVERY_ACTION_TYPES,
                                    SENT_REPLY_SQL, SENT_REPLY_OBSERVER_SQL)
    canonical = {'sent_replies': SENT_REPLY_SQL,
                 'sent_reply_observers': SENT_REPLY_OBSERVER_SQL}
    return {kind: canonical.get(kind, ACTION_PROJECTIONS[kind]) for kind in DELIVERY_ACTION_TYPES}


def publication_email_action_source(db, *, release_source_email_version=None):
    _version(db, release_source_email_version=release_source_email_version)
    return ("select action_status as status from actions where record_type='action' "
            "and action_action_plan_id is not null and action_operation in "
            "('archive','flag_important','mark_read','move','trash','label')")
