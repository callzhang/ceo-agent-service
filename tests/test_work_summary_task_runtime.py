from app.run_entity_storage import get_run
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier

from app.task_run_entity_assembly import task_run_entity_assembly_objects
from app.task_agent import TaskAgentRunner, process_work_item
from app.task_models import WorkItem
from app.store import AutoReplyStore


def _native_store(tmp_path):
    path = tmp_path / "native.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    from app.task8_storage import register_task8_functions
    register_task8_functions(db)
    objects = task_run_entity_assembly_objects()
    for ddl in objects.values():
        if ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    for ddl in objects.values():
        if not ddl.lstrip().lower().startswith("create table"):
            db.execute(ddl)
    db.commit()
    db.close()
    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar("test_snapshot", default=None)
    store._reused_connection = ContextVar("test_reused", default=None)
    return store


def test_concurrent_work_summary_enqueue_returns_one_canonical_task(tmp_path):
    store = _native_store(tmp_path)
    start = Barrier(2)

    def enqueue(payload):
        start.wait(timeout=5)
        return store.enqueue_work_summary_input("local_file", "same-source", payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(enqueue, ('{"summary":"first"}', '{"summary":"second"}')))

    assert results[0] == results[1]
    with store._connect() as db:
        assert db.execute("select count(*) from tasks where kind='work_summary'").fetchone()[0] == 1
        assert db.execute("select count(*) from work_summary_inputs").fetchone()[0] == 1


def test_process_work_item_applies_business_fact_and_native_run_projection(tmp_path, monkeypatch):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = _native_store(tmp_path)
    item = WorkItem.model_validate({
        "source": {
            "type": "reply_attempt", "ref": "message:runtime-chain",
            "title": "报价待补来源链接", "conversation_id": "conversation:runtime-chain",
            "conversation_title": "客户群", "created_at": "2026-10-23T09:00:00+08:00",
        },
        "summary": "补齐来源链接；owner 是 Alex。",
        "context": {
            "sender": "Avery", "sender_user_id": "avery-id",
            "source_conversation_kind": "group",
        },
    })
    task_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    [claimed] = store.claim_work_summary_inputs(limit=1)

    decision = {
        "project_decisions": [], "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [{
            "action": "record_candidate", "transition": "none",
            "source_excerpt": "补齐来源链接", "source_ref": item.source.ref,
            "title": "补齐报价来源链接", "missing_evidence": ["owner"],
        }],
    }

    class SubstituteCodex:
        last_session_id = "fixture-session"

        def decide(self, **_kwargs):
            from app.task_agent import TaskAgentDecision
            return TaskAgentDecision.model_validate(decision)

    process_work_item(store, TaskAgentRunner(SubstituteCodex()), claimed)

    terminal = store.get_work_summary_input(task_id)
    business_tasks = store.list_business_tasks()
    assert terminal.status.value == "done"
    assert terminal.body_compacted == 1
    assert len(business_tasks) == 1
    assert business_tasks[0].title == "补齐报价来源链接"
    with store._connect() as db:
        [run] = db.execute(
            "select r.id,r.task_id,r.status,r.session_id,p.source_type,proj.projection_json "
            "from runs r join run_provenance p on p.run_id=r.id "
            "join run_projections proj on proj.run_id=r.id where r.task_id=?",
            (task_id,),
        ).fetchall()
    assert run["task_id"] == task_id
    assert run["status"] == "completed"
    assert run["session_id"] == "fixture-session"
    assert run["source_type"] == "task_agent_runs"
    assert run["projection_json"] != "{}"


def test_work_summary_uses_canonical_task_id_and_keeps_terminal_input_immutable(tmp_path):
    store = _native_store(tmp_path)
    first = store.enqueue_work_summary_input("local_file", "42", '{"summary":"first"}')
    assert first == 1 and first != 42
    assert store.enqueue_work_summary_input("local_file", "42", '{"summary":"replacement"}') == first
    [claimed] = store.claim_work_summary_inputs(1)
    assert claimed.id == first
    assert claimed.status == "processing"
    assert claimed.attempts == 1
    assert claimed.payload_json == '{"summary":"replacement"}'

    run_id = store.claim_professional_run(claimed.id)
    with store._connect() as db:
        assert get_run(db, run_id)["task_id"] == claimed.id
    store.finish_professional_run(run_id, status="completed", session_id="fixture-session")
    with store._connect() as db:
        finished = get_run(db, run_id)
    assert (finished["status"], finished["session_id"]) == ("completed", "fixture-session")

    assert store.enqueue_work_summary_input("local_file", "42", '{"summary":"late"}') == first
    assert store.get_work_summary_input(first).payload_json == '{"summary":"replacement"}'
    store.mark_work_summary_input_done(first)
    terminal = store.get_work_summary_input(first)
    assert terminal.status == "done"
    assert terminal.body_compacted == 1
    assert terminal.body_sha256
    assert terminal.body_bytes == len('{"summary":"replacement"}'.encode())
    assert terminal.payload_json == "{}"

    skipped_id = store.enqueue_work_summary_input("local_file", "skip-me", '{"summary":"skip"}')
    store.claim_work_summary_inputs(1)
    store.mark_work_summary_input_skipped(skipped_id, "no actionable decision")
    skipped = store.get_work_summary_input(skipped_id)
    assert (skipped.status, skipped.error, skipped.body_compacted, skipped.payload_json) == (
        "skipped", "no actionable decision", 1, "{}"
    )


def test_work_summary_retry_and_capacity_keep_native_task_lifecycle(tmp_path):
    store = _native_store(tmp_path)
    task_id = store.enqueue_work_summary_input("local_file", "source-B", "{}")
    store.claim_work_summary_inputs(1)
    store.mark_work_summary_input_failed(task_id, "temporary")
    assert store.requeue_failed_work_summary_input(task_id, "reviewed")
    pending = store.get_work_summary_input(task_id)
    assert (pending.status, pending.attempts, pending.error) == ("pending", 0, "reviewed")
    store.claim_work_summary_inputs(1)
    store.defer_work_summary_input_for_capacity(task_id, "busy", available_at="2099-01-01 00:00:00")
    deferred = store.get_work_summary_input(task_id)
    assert (deferred.status, deferred.attempts, deferred.error) == ("pending", 0, "busy")


def test_work_summary_reset_closes_only_native_attempts_without_effects(tmp_path):
    store = _native_store(tmp_path)
    task_id = store.enqueue_work_summary_input("local_file", "recovery", "{}")
    store.claim_work_summary_inputs(1)
    run_id = store.claim_professional_run(task_id)

    def attempt(workload_key, first_effect_started_at):
        return {
            "parent_id": run_id, "workload_kind": "task", "workload_key": workload_key,
            "attempt_number": 1, "route_name": "fixture", "runtime_kind": "codex",
            "credential_mode": "native", "model": "fixture", "session_mode": "fresh",
            "source_session_id": "", "attempt_purpose": "normal",
            "validation_retry_policy_id": "", "validation_result_schema_id": "",
            "session_id": "", "transcript_reference": "", "transcript_start": 0,
            "transcript_end": 0, "status": "running", "failure_class": "",
            "failure_code": "", "failover_permitted": 0, "lease_owner": "worker",
            "lease_expires_at": "2099-01-01 00:00:00",
            "first_effect_started_at": first_effect_started_at, "result_schema_id": "",
            "started_at": "2026-10-23 00:00:00", "finished_at": "",
            "created_at": "2026-10-23 00:00:00", "updated_at": "2026-10-23 00:00:00",
        }

    from app import run_entity_storage

    with store._connect() as db:
        no_effect = run_entity_storage.create_runtime_attempt(
            db, values=attempt(f"{run_id}:no-effect", "")
        )["id"]
        with_effect = run_entity_storage.create_runtime_attempt(
            db, values=attempt(f"{run_id}:effect", "2026-10-23 00:00:01")
        )["id"]
        store._close_task_runs_leaving_processing(db, [task_id])
        no_effect_status = run_entity_storage.get_runtime_attempt(db, no_effect)["status"]
        effect_status = run_entity_storage.get_runtime_attempt(db, with_effect)["status"]
        run_status = run_entity_storage.get_run(db, run_id)["status"]
    assert (run_status, no_effect_status, effect_status) == ("failed", "failed", "running")
