"""Professional Run lifecycle on the native Task/Run owners."""
from __future__ import annotations

from app import run_entity_storage as runs


_SOURCE_BY_TASK_KIND = {
    "work_summary": "task_agent_runs",
    "meeting_alignment": "meeting_alignment_runs",
    "okr_review": "okr_review_runs",
}
class LogicalRunStoreMixin:
    """Professional lifecycle API. Every identity accepted or returned is native."""

    @staticmethod
    def _professional_source(db, task_id: int | None = None, run_id: int | None = None):
        if task_id is not None:
            task = db.execute("select kind from tasks where id=?", (task_id,)).fetchone()
            return None if task is None else _SOURCE_BY_TASK_KIND.get(task[0])
        row = db.execute(
            "select source_type from run_provenance where run_id=?", (run_id,)
        ).fetchone()
        return None if row is None else row[0]

    def create_professional_run(
        self, task_id: int, *, status: str | None = None, session_id: str = "",
        transcript_start: int | None = None, transcript_end: int | None = None,
        error: str | None = None,
    ) -> int:
        with self._agent_run_write_transaction(None) as (db, _):
            source = self._professional_source(db, task_id=task_id)
            if source is None:
                raise ValueError("professional Run requires a Task Agent, Meeting, or OKR Task")
            if source == "task_agent_runs" and (transcript_start is not None or transcript_end is not None):
                raise ValueError("Task Agent Run has no transcript range")
            if source == "okr_review_runs" and (status is not None or error is not None):
                raise ValueError("OKR Run has no lifecycle fields")
            if source == "task_agent_runs" and status is None:
                status = "completed"
            now = db.execute("select current_timestamp").fetchone()[0]
            values = {"task_id": task_id, "session_id": session_id, "created_at": now}
            if source != "okr_review_runs":
                values.update(status=status, error="" if error is None else error,
                              completed_at="" if status == "running" else now,
                              updated_at=now)
            if source == "meeting_alignment_runs" or source == "okr_review_runs":
                values.update(transcript_start=0 if transcript_start is None else transcript_start,
                              transcript_end=0 if transcript_end is None else transcript_end)
            run = runs.create_run(db, values=values)
            runs.set_run_provenance(
                db, run_id=run["id"], source_type=source,
            )
            if source == "task_agent_runs":
                runs.set_run_projection(db, run_id=run["id"], projection_json="{}")
            return int(run["id"])

    def claim_professional_run(self, task_id: int) -> int:
        with self._agent_run_write_transaction(None) as (db, _):
            source = self._professional_source(db, task_id=task_id)
            if source == "okr_review_runs":
                raise ValueError("OKR Run is recorded after its native result")
            if source not in {"task_agent_runs", "meeting_alignment_runs"}:
                raise ValueError("professional Run Task does not exist")
            parent = db.execute("select status from tasks where id=?", (task_id,)).fetchone()
            if parent[0] != "processing":
                raise ValueError("professional Run Task is not processing")
            active = db.execute(
                "select r.id from runs r join run_provenance p on p.run_id=r.id "
                "where r.task_id=? and p.source_type=? and r.status='running' order by r.id desc limit 1",
                (task_id, source),
            ).fetchone()
            if active is not None:
                return int(active[0])
            meeting = source == "meeting_alignment_runs"
            now = db.execute("select current_timestamp").fetchone()[0]
            run = runs.create_run(db, values={
                "task_id": task_id, "status": "running", "session_id": "", "error": "",
                "completed_at": "", "created_at": now, "updated_at": now,
                **({"transcript_start": 0, "transcript_end": 0} if meeting else {}),
            })
            runs.set_run_provenance(db, run_id=run["id"], source_type=source)
            if source == "task_agent_runs":
                runs.set_run_projection(db, run_id=run["id"], projection_json="{}")
            return int(run["id"])

    def finish_professional_run(
        self, run_id: int, *, status: str, session_id: str = "",
        transcript_start: int | None = None, transcript_end: int | None = None,
        error: str | None = None, _db=None,
    ) -> None:
        def finish(db):
            source = self._professional_source(db, run_id=run_id)
            allowed = {
                "task_agent_runs": {"completed", "failed"},
                "meeting_alignment_runs": {"failed", "retry", "no_action", "ready_to_send"},
            }
            if source not in allowed:
                raise ValueError("Run has no finishable professional lifecycle")
            if status not in allowed[source]:
                raise ValueError("professional Run terminal status is invalid")
            if source == "task_agent_runs" and (transcript_start is not None or transcript_end is not None):
                raise ValueError("Task Agent Run has no transcript range")
            changes = {"status": status, "session_id": session_id,
                       "error": "" if error is None else error}
            if source == "meeting_alignment_runs":
                changes.update(
                    transcript_start=0 if transcript_start is None else transcript_start,
                    transcript_end=0 if transcript_end is None else transcript_end,
                )
            from app import run_entity_storage
            row = run_entity_storage.get_run(db, run_id)
            if row is None:
                raise ValueError("professional Run does not exist")
            if row["status"] != "running":
                if all(row[key] == value for key, value in changes.items()):
                    return
                raise ValueError("conflicting professional Run terminal rewrite")
            now = db.execute("select current_timestamp").fetchone()[0]
            changes.update(completed_at=now, updated_at=now)
            if run_entity_storage.update_run_cas(
                db, run_id=run_id, expected={"status": "running"}, changes=changes
            ) is None:
                raise ValueError("professional Run changed while finishing")
        if _db is not None:
            finish(_db)
            return
        with self._agent_run_write_transaction(None) as (db, _):
            finish(db)
