"""The same provider-free business calls executed by actual source and target DAOs."""
from pathlib import Path

from app import native_trajectory
from app.store import AutoReplyStore, AgentRuntimeAttemptStartConflictError, AgentRuntimeAttemptLeaseLostError
from app.run_storage import RUNTIME_ATTEMPT_SQL


class ClockStore(AutoReplyStore):
    def _open_connection(self):
        db = super()._open_connection()
        db.create_function("current_timestamp", 0, lambda: "2026-10-20 12:00:00")
        return db


def exercise(path, owned):
    native_trajectory.find_codex_session_path = lambda *a, **k: None
    native_trajectory.claude_session_path = lambda *a, **k: None
    store = ClockStore(Path(path))
    observed = []

    def claim(source, parent):
        if owned:
            return store.claim_logical_run(source, parent)
        return {"task_agent_runs": store.begin_task_agent_run,
                "meeting_alignment_runs": store.begin_meeting_alignment_run}[source](parent)

    def finish(source, run, **values):
        if owned:
            return store.finish_logical_run(source, run, **values)
        return {"task_agent_runs": store.finish_task_agent_run,
                "meeting_alignment_runs": store.finish_meeting_alignment_run}[source](run, **values)

    def read(source, run):
        if owned:
            result = store.get_logical_run(source, run)
        else:
            result = {"task_agent_runs": store.get_task_agent_run,
                      "meeting_alignment_runs": store.get_meeting_alignment_run,
                      "okr_review_runs": store.get_okr_review_run}[source](run)
        values = result if isinstance(result, dict) else result.model_dump(mode="json")
        return {k: v for k, v in values.items()
                if k not in {"common_run_id", "source_type", "source_key", "task_id"}}

    task = store.enqueue_work_summary_input("local_file", "dao-task", "{}")
    store.claim_work_summary_inputs(10)
    meeting = store.upsert_meeting_alignment_job(
        meeting_id="dao-meeting", title="DAO", source_json="{}", participants_json="[]",
        ended_at="", eligible_at="", status="processing",
    )
    for source, parent, kind in (("task_agent_runs", task, "task"),
                                  ("meeting_alignment_runs", meeting, "meeting")):
        run = claim(source, parent)
        assert claim(source, parent) == run
        observed.append(["claim", source, run])
        with store._connect() as db:
            # The exact validator grammar is shared, including repair suffixes;
            # unrelated old Todo/Project operations cannot become logical Runs.
            keys = [str(run)]
            if kind == "task":
                keys += [f"{run}:decision_repair.1", f"{run}:deadline_backfill",
                         f"{run}:deadline_backfill.1", f"{run}:memory_backfill"]
            for key in keys:
                validated = store._validate_runtime_operation_workload(kind, key)
                observed.append(["parent", *validated,
                                 store._runtime_operation_parent_exists(db, *validated)])
            for key in [f"{run}:decision_repair", f"{run}:decision_repair.x", f"{run}:other", "0001"]:
                try:
                    observed.append(["grammar", store._validate_runtime_operation_workload(kind, key)])
                except (ValueError, TypeError) as exc:
                    observed.append(["grammar", type(exc).__name__, str(exc)])
        attempt = store.claim_runtime_operation_attempt(
            kind, str(run), "source-route", "codex_cli", "local_oauth", "source-model",
            owner="owner", lease_seconds=60, now="2026-10-20 12:00:00",
        )
        assert attempt.agent_run_id is None
        try:
            store.mark_agent_runtime_attempt_running_once(
                attempt.id, owner="wrong-owner", now="2026-10-20 12:00:01",
            )
        except AgentRuntimeAttemptStartConflictError as exc:
            observed.append(["start-fence", type(exc).__name__])
        else:
            raise AssertionError("operation lease owner fence bypassed")
        store.mark_agent_runtime_attempt_running_once(
            attempt.id, owner="owner", now="2026-10-20 12:00:01",
        )
        try:
            store.complete_agent_runtime_attempt(
                attempt.id, "unavailable-session", "ref", 1, 3,
                owner="wrong-owner", now="2026-10-20 12:00:02",
            )
        except AgentRuntimeAttemptLeaseLostError as exc:
            observed.append(["completion-fence", type(exc).__name__])
        else:
            raise AssertionError("completion lease owner fence bypassed")
        store.complete_agent_runtime_attempt(
            attempt.id, "unavailable-session", "ref", 1, 3,
            owner="owner", now="2026-10-20 12:00:02",
        )
        values = dict(status="completed" if kind == "task" else "ready_to_send",
                      codex_session_id="unavailable-session", error="original free text")
        if kind == "meeting":
            values.update(codex_transcript_start_line=1, codex_transcript_end_line=3)
        finish(source, run, **values)
        finish(source, run, **values)
        try:
            finish(source, run, **(values | {"error": "changed"}))
        except ValueError:
            observed.append(["conflict", source])
        else:
            raise AssertionError("terminal history overwritten")
        observed.append(read(source, run))
    request = store.create_okr_review_request(
        conversation_id="dao", conversation_title="DAO", trigger_message_id="dao-request",
        trigger_sender="sender", trigger_sender_user_id="owner", trigger_text="review",
        period_label="dao", period_start="", period_end="", okr_source_json="{}",
    )
    for start, end in ((0, 0), (1, 9)):
        values = dict(codex_session_id="unavailable-okr", codex_transcript_start_line=start,
                      codex_transcript_end_line=end)
        run = (store.create_logical_run("okr_review_runs", request, **values) if owned
               else store.record_okr_review_run(request_id=request, **values,
                    envelope_json="{}", audit_tool_events_json="[]", audit_summary=""))
        observed.append(read("okr_review_runs", run))
    with store._connect() as db:
        observed.append([dict(row) for row in db.execute(f"select * from ({RUNTIME_ATTEMPT_SQL}) order by id")])
    return observed
