"""Common standalone logical history DAO, retaining native result provenance."""
import json

from app import logical_run_storage as logical
from app.logical_run_definitions import SOURCE_TASKS
from app.task_lifecycle_definitions import task20_column


class LogicalRunStoreMixin:
    def create_logical_run(self, source, task_public_id=None, *, status=None,
                           codex_session_id="", codex_transcript_start_line=0,
                           codex_transcript_end_line=0, error=""):
        if source == "task_agent_runs" and status is None:
            status = "completed"
        with self._connect() as db:
            return logical.create_logical_run(
                db, source, task_public_id, status=status, codex_session_id=codex_session_id,
                codex_transcript_start_line=codex_transcript_start_line,
                codex_transcript_end_line=codex_transcript_end_line, error=error,
            )

    def claim_logical_run(self, source, task_public_id):
        task_kind = SOURCE_TASKS[source]
        if source == "okr_review_runs":
            raise ValueError("OKR logical history is recorded after its native result")
        if task_public_id <= 0:
            raise ValueError("logical run parent id must be positive")
        with self._agent_run_write_transaction(None) as (db, _):
            parent = db.execute(
                "select id from tasks where record_type=? "
                "and record_type not in ('workbench_tasks','workbench_turns') "
                "and cast(legacy_key as integer)=? and "
                + task20_column(task_kind, "status") + "='processing'",
                (task_kind, task_public_id),
            ).fetchone()
            if parent is None:
                raise ValueError("logical run parent is not processing")
            active = db.execute(
                "select cast(legacy_key as integer) from runs "
                "where record_type='agent_runs' and source_type=? "
                "and task_id=? and status='running'", (source, parent[0])
            ).fetchone()
            if active is not None:
                return int(active[0])
            return logical.create_logical_run(db, source, task_public_id, status="running")

    def finish_logical_run(self, source, run_id, *, status, codex_session_id="",
                           codex_transcript_start_line=0, codex_transcript_end_line=0,
                           error="", _db=None):
        values = dict(status=status, codex_session_id=codex_session_id,
                      codex_transcript_start_line=codex_transcript_start_line,
                      codex_transcript_end_line=codex_transcript_end_line, error=error)
        if _db is not None:
            logical.finish_logical_run(_db, source, run_id, **values)
            return
        with self._agent_run_write_transaction(None) as (db, _):
            logical.finish_logical_run(db, source, run_id, **values)

    def get_logical_run(self, source, run_id, *, load_native=True):
        from app.native_standalone import (
            _meeting_tool_events, exact_standalone_ref, latest_standalone_ref,
            latest_task_ref, okr_envelope, task_decision,
        )
        with self._connect() as db:
            row = db.execute(
                f"select * from ({logical.logical_run_projection(source)}) where id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            if source == "task_agent_runs":
                ref = latest_task_ref(db, run_id) if load_native else None
            elif source == "meeting_alignment_runs":
                ref = (latest_standalone_ref(
                    db, workload_kind="meeting", workload_key=str(run_id), legacy_row=row
                ) if load_native else None)
            else:
                ref = (exact_standalone_ref(
                    db, workload_kind="structured", workload_key=str(row["request_id"]), row=row
                ) if load_native else None)
        if source == "meeting_alignment_runs":
            return self._meeting_alignment_run_from_row(row, ref)
        values = dict(row)
        if source == "task_agent_runs":
            native = task_decision(ref)
            values.update(
                decision_json=native.decision_json, audit_summary=native.audit_summary,
                memory_recall_used=int(native.memory_recall_used),
                native_available=native.available, native_reason=native.reason,
            )
        else:
            envelope_json, reason = okr_envelope(ref)
            events, available, event_reason = (
                _meeting_tool_events(ref) if not reason and ref is not None
                else ("[]", False, reason or "native_reference_unavailable")
            )
            values.update(
                envelope_json=envelope_json, audit_tool_events_json=events,
                audit_summary=json.loads(envelope_json)["audit"]["summary"] if not reason else "",
                native_available=not reason, native_reason=reason,
                tool_events_available=available, tool_events_reason=event_reason,
            )
        return values
