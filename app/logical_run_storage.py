"""Finite common logical history storage, independent of model invocations.

The source namespace retains old public links and operation workload identities.
Only the physical agent_runs row owns lifecycle. Caller transactions are native.
"""
from app.logical_run_definitions import SOURCE_TASKS
from app.run_storage import AGENT_RUN_SQL as SOURCE_AGENT_RUN_SQL

# Consumer/Audit builders and adoption keep their original role-only contract.
AGENT_RUN_SQL = SOURCE_AGENT_RUN_SQL + " and source_type is null"

_PARENT_FIELDS = {
    "task_agent_runs": "summary_input_id",
    "meeting_alignment_runs": "job_id",
    "okr_review_runs": "request_id",
}


def logical_run_projection(source):
    task = SOURCE_TASKS[source]
    parent = ("case when p.id is null then r.job_id else cast(p.legacy_key as integer) end"
              if source == "meeting_alignment_runs" else "cast(p.legacy_key as integer)")
    fields = [
        "cast(r.legacy_key as integer) as id", "r.id as common_run_id",
        "r.source_type", "r.legacy_key as source_key", "r.task_id",
        parent + " as " + _PARENT_FIELDS[source],
        "r.session_id as codex_session_id", "r.created_at",
    ]
    if source != "task_agent_runs":
        fields += ["r.transcript_start as codex_transcript_start_line",
                   "r.transcript_end as codex_transcript_end_line"]
    if source != "okr_review_runs":
        fields += ["r.status", "r.error", "r.completed_at as finished_at", "r.updated_at"]
    if source == "task_agent_runs":
        fields += ["r.projection_json"]
    return ("select " + ",".join(fields) + " from runs r left join tasks p on p.id=r.task_id"
            + " and p.record_type=" + repr(task)
            + " where r.record_type='agent_runs' and r.source_type=" + repr(source))


def create_logical_run(db, source, parent_public_id, *, status=None,
                       codex_session_id="", codex_transcript_start_line=0,
                       codex_transcript_end_line=0, error=""):
    """Insert one source-present history fact using the existing runs allocator."""
    task_kind = SOURCE_TASKS[source]
    if source == "task_agent_runs" and (codex_transcript_start_line or codex_transcript_end_line):
        raise ValueError("Task transcript range is absent from source")
    if source == "okr_review_runs" and (status is not None or error):
        raise ValueError("OKR lifecycle fields are absent from source")
    parent = db.execute(
        "select id from tasks where record_type=? "
        "and record_type not in ('workbench_tasks','workbench_turns') "
        "and cast(legacy_key as integer)=?", (task_kind, parent_public_id)
    ).fetchone()
    if parent is None:
        raise ValueError("logical run parent does not exist")
    values = dict(record_type="agent_runs", source_type=source,
                  task_id=parent[0], session_id=codex_session_id)
    now = db.execute("select current_timestamp").fetchone()[0]
    values["created_at"] = now
    if source != "task_agent_runs":
        values.update(transcript_start=codex_transcript_start_line,
                      transcript_end=codex_transcript_end_line)
    if source != "okr_review_runs":
        values.update(status=status, error=error,
                      completed_at="" if status == "running" else now,
                      updated_at=now)
    if source == "task_agent_runs":
        values["projection_json"] = "{}"
    cursor = db.execute(
        "insert into runs(" + ",".join(values) + ") values("
        + ",".join("?" for _ in values) + ")", tuple(values.values())
    )
    return cursor.lastrowid
