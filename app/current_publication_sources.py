"""Exact current and immediately previous sources for the explicit deploy window.

Normal runtime readers use the current declaration. Historical publication tools
remain in their versioned release modules and are not runtime fallbacks.
"""

from app.current_schema_manifest import STORE_VERSION as TARGET_STORE_VERSION, EMAIL_VERSION as TARGET_EMAIL_VERSION, schema_is_current
from app.state_definitions import state_projection

PREVIOUS_STORE_VERSIONS = ('2026-10-19.11',)


def _version(db):
    marker = db.execute(f"select value from ({state_projection('service_state')}) "
                        "where key='store_schema_version'").fetchone()
    version = marker[0] if marker else None
    ledger = db.execute("select 1 from sqlite_schema where type='table' and name='email_schema_migrations'").fetchone()
    if ledger and db.execute('select max(version) from email_schema_migrations').fetchone()[0] != TARGET_EMAIL_VERSION:
        raise ValueError('unsupported mixed Email publication version')
    if version == TARGET_STORE_VERSION and schema_is_current(db):
        return version
    if version == PREVIOUS_STORE_VERSIONS[0]:
        from app.current_action_schema_release import source_objects
        from app.current_schema_manifest import schema_objects
        from app.action_storage import REPLY_VARIANTS
        if any(schema_objects(db) == source_objects(variant) for variant in REPLY_VARIANTS):
            return version
    raise ValueError('unsupported current publication declaration; explicit source migration required')


def current_email_actions_available(db):
    if _version(db) != TARGET_STORE_VERSION:
        raise ValueError('unsupported current Email publication declaration')
    return db.execute("select 1 from sqlite_schema where type='table' and name='email_schema_migrations'").fetchone() is not None


def publication_run_sources(db):
    _version(db)
    from app.logical_run_storage import AGENT_RUN_SQL
    from app.run_storage import RUNTIME_ATTEMPT_SQL
    return AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL


def publication_task_sources(db):
    _version(db)
    from app.task20_definitions import task20_projection
    return task20_projection('reply_tasks'), task20_projection('work_summary_inputs')


def publication_meeting_sources(db):
    _version(db)
    from app.task20_definitions import task20_projection
    from app.logical_run_storage import logical_run_projection
    return task20_projection('meeting_alignment_jobs'), logical_run_projection('meeting_alignment_runs')


def publication_scheduled_source(db):
    _version(db)
    from app.run_storage import SCHEDULED_RUN_SQL
    return SCHEDULED_RUN_SQL


def publication_dispatcher_source(db, **_publication_options):
    _version(db)
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
    _version(db)
    from app.action_storage import REVIEW_CANDIDATE_SQL
    return REVIEW_CANDIDATE_SQL


def publication_receipt_sources(db):
    _version(db)
    from app.action_storage import ACTION_PROJECTIONS, RECEIPT_ACTION_TYPES
    return {kind:ACTION_PROJECTIONS[kind] for kind in RECEIPT_ACTION_TYPES}


def publication_delivery_sources(db):
    _version(db)
    from app.action_storage import ACTION_PROJECTIONS, DELIVERY_ACTION_TYPES
    return {kind:ACTION_PROJECTIONS[kind] for kind in DELIVERY_ACTION_TYPES}


def publication_email_action_source(db):
    _version(db)
    from app.shared_action_definitions import email_projection
    return email_projection('email_actions')
