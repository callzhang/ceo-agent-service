import contextlib
import hashlib
import json
import sqlite3
from types import SimpleNamespace

from app.task_run_entity_assembly import task_run_entity_assembly_objects
from app import run_entity_storage, task_entity_storage
from app.store import AutoReplyStore


def _native_store(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=on")
    objects = task_run_entity_assembly_objects()
    for sql in objects.values():
        if sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    for sql in objects.values():
        if not sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    store = AutoReplyStore.__new__(AutoReplyStore)

    @contextlib.contextmanager
    def connect():
        with db:
            yield db

    monkeypatch.setattr(store, "_connect", connect, raising=False)
    task = task_entity_storage.create_task(db, values={
        "kind": "reply", "status": "pending", "execution_generation": "gen-1",
        "created_at": "now", "updated_at": "now",
    }, parameters={})
    input_row, _ = task_entity_storage.append_task_input(db, task_id=task["id"], values={
        "channel": "dingtalk", "conversation_id": "native", "conversation_title": "Native",
        "single_chat": 1, "trigger_message_id": "m1", "trigger_create_time": "now",
        "trigger_sender": "Derek", "trigger_text": "review", "business_object_key": "bk-1",
        "input_revision_key": "input-1",
    })
    task_entity_storage.update_task_cas(db, task_id=task["id"], expected={"status": "pending"},
        changes={"current_input_id": input_row["id"]})
    db.execute("insert into task_business_keys(task_id,business_key) values(?,?)", (task["id"], "bk-1"))
    consumer = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "execution_generation": "gen-1", "role": "consumer",
        "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    db.execute("insert into task_business_keys(task_id,business_key) values(?,?)", (task["id"], "bk-1-alternate"))
    db.commit()
    return store, db, task, consumer


def test_native_store_adopts_candidate_review_selection_and_supplement(monkeypatch, tmp_path):
    from app import native_trajectory

    store, db, task, consumer = _native_store(monkeypatch)
    body = {
        "outcome": "needs_human", "summary": "review",
        "stage_index": 0, "predecessor_review_id": None,
        "decision_options": [
            {"key": "execute", "plan": {"actions": [{"operation": "send", "target": {"user_id": "u1"}}]}},
            {"key": "stop", "terminal_outcome": "skipped", "reason": "stop"},
        ],
    }
    with db:
        adopted = store._adopt_review_candidate_in_connection(db, consumer, body)
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert adopted["id"] == consumer["id"]
    assert adopted["business_object_key"] == "bk-1"
    assert adopted["candidate_json"] == json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert adopted["candidate_digest"] == digest
    assert store.persist_review_candidate(SimpleNamespace(id=task["id"], execution_generation="gen-1"),
        SimpleNamespace(id=consumer["id"]), body)["id"] == consumer["id"]

    with db:
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
    review_body = {"outcome": "approve", "candidate_digest": digest}
    native = tmp_path / "audit.jsonl"
    native.write_text(json.dumps({"type": "response_item", "payload": {"type": "message",
        "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(review_body)}]}}) + "\n")
    with db:
        run_entity_storage.create_runtime_attempt(db, values={
        "parent_id": audit["id"], "workload_kind": "agent_run", "workload_key": str(audit["id"]),
        "attempt_number": 1, "route_name": "fixture", "runtime_kind": "codex_cli",
        "credential_mode": "local_oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "native-session", "transcript_reference": "fixture",
        "transcript_start": 0, "transcript_end": 1, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "now",
        "finished_at": "now", "created_at": "now", "updated_at": "now",
        })
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: native)
    review = store.record_candidate_review(consumer["id"], audit["id"], review_body)
    assert review["id"] != audit["id"]
    assert review["audit_run_id"] == audit["id"]
    assert store.record_candidate_review(consumer["id"], audit["id"], review_body) == review
    assert store.get_candidate_review_for_audit_run(audit["id"])["candidate_id"] == consumer["id"]
    assert store.reviewed_candidate_for_audit_run(audit["id"])["candidate_id"] == consumer["id"]
    assert store.current_reviewed_candidate(task["id"], "gen-1")["review_id"] == review["id"]
    assert store.current_reviewed_candidate(task["id"], "gen-1")["audit_run_id"] == audit["id"]

    later_reviews = []
    for decision in ("return", "reject"):
        with db:
            another_audit = run_entity_storage.create_run(db, values={
                "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
                "role": "audit", "proposal_revision": 0, "turn_attempt": len(later_reviews) + 1,
                "status": "completed", "created_at": "now", "updated_at": "now",
            })
            result = {"outcome": decision, "candidate_digest": digest}
            stored = store._adopt_candidate_review_in_connection(db, consumer["id"], another_audit["id"], result)
        later_reviews.append((another_audit, result, stored))
        assert stored["id"] != another_audit["id"]
        assert stored["audit_run_id"] == another_audit["id"]
        assert stored["candidate_id"] == consumer["id"]
        assert store.get_candidate_review_for_audit_run(another_audit["id"])["decision"] == decision
    with db:
        try:
            store._adopt_candidate_review_in_connection(
                db, consumer["id"], later_reviews[0][0]["id"],
                {"outcome": "reject", "candidate_digest": digest},
            )
        except ValueError as exc:
            assert str(exc) == "review content conflict"
        else:
            raise AssertionError("conflicting Audit review was accepted")

    selected = store.select_candidate_option(consumer["id"], review["id"], "execute")
    assert selected["candidate_id"] == consumer["id"]
    assert selected["review_id"] == review["id"]
    assert selected["audit_run_id"] == audit["id"]
    assert json.loads(selected["branch_json"])["key"] == "execute"
    assert store.select_candidate_option(consumer["id"], review["id"], "execute")["id"] == selected["id"]
    current_selection = store.get_candidate_selection(consumer["id"])
    assert current_selection["review_id"] == review["id"]
    assert current_selection["audit_run_id"] == audit["id"]
    assert store.get_candidate_execution(consumer["id"]) is None

    db.execute("""insert into run_execution_progress
        (consumer_run_id,approved_audit_run_id,selected_audit_run_id,status,lease_owner,
         lease_expires_at,result_json,created_at,updated_at)
        values(?,?,?,'done','','','{}','now','now')""", (consumer["id"], audit["id"], audit["id"]))
    db.commit()
    current_execution = store.get_candidate_execution(consumer["id"])
    assert current_execution["review_id"] == review["id"]
    assert current_execution["audit_run_id"] == audit["id"]
    later = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 1, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    later_body = {**body, "stage_index": 1, "predecessor_review_id": review["id"]}
    with db:
        later_adoption = store._adopt_review_candidate_in_connection(db, later, later_body)
    assert later_adoption["predecessor_review_id"] == review["id"]
    later_digest = later_adoption["candidate_digest"]
    with db:
        later_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": later["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 1, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        store._adopt_candidate_review_in_connection(db, later["id"], later_audit["id"],
            {"outcome": "return", "candidate_digest": later_digest})

    historical_body = {**body, "stage_index": 1, "predecessor_review_id": review["id"]}
    historical = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": later_audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 2, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    historical_json = json.dumps(historical_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    historical_digest = hashlib.sha256(historical_json.encode()).hexdigest()
    db.execute("""insert into run_adopted_results
        (consumer_run_id,stage_index,predecessor_review_id,candidate_digest,candidate_json,
         invalidated_at,invalidation_reason,created_at)
        values(?,1,?,?,?,'','',current_timestamp)""",
        (historical["id"], review["id"], historical_digest, historical_json))
    db.commit()
    historical_adoption = store.get_review_candidate(historical["id"])
    assert historical_adoption["predecessor_review_id"] == review["id"]
    assert json.loads(historical_adoption["candidate_json"])["predecessor_review_id"] == review["id"]
    assert historical_adoption["candidate_json"] == historical_json
    assert historical_adoption["candidate_digest"] == historical_digest

    with db:
        historical_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": historical["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 2, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        store._adopt_candidate_review_in_connection(db, historical["id"], historical_audit["id"],
            {"outcome": "return", "candidate_digest": historical_digest})

    # New same-stage revisions name the current relational predecessor, even
    # when an immutable migrated candidate body still contains the old ID.
    revised = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": historical_audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 3, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    revised_body = {**body, "stage_index": 1, "predecessor_review_id": review["id"]}
    with db:
        revised_adoption = store._adopt_review_candidate_in_connection(db, revised, revised_body)
    assert revised_adoption["stage_index"] == 1
    assert revised_adoption["predecessor_review_id"] == review["id"]
    assert json.loads(revised_adoption["candidate_json"])["predecessor_review_id"] == review["id"]

    bad_revision = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": historical_audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 4, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    db.commit()
    try:
        with db:
            store._adopt_review_candidate_in_connection(
                db, bad_revision, {**revised_body, "predecessor_review_id": audit["id"]})
    except ValueError as exc:
        assert str(exc) == "candidate predecessor mismatch"
    else:
        raise AssertionError("new revision accepted a migrated predecessor ID")

    from app.agent_orchestrator import AgentOrchestrator, _NextExecution
    from app.agent_contracts import AuditOutcome
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator.store = store
    orchestrator._consumer_result = lambda _run: SimpleNamespace(continue_after_execution=False, purpose_output=None)
    orchestrator._audit_result = lambda _run: SimpleNamespace(outcome=AuditOutcome.RETURN)
    store.get_agent_run = lambda run_id, load_events=False: SimpleNamespace(id=run_id)
    business_revision = orchestrator._business_revision(_NextExecution(
        SimpleNamespace(id=historical["id"], proposal_revision=2),
        SimpleNamespace(id=historical_audit["id"]), None, None, historical["id"], historical_audit["id"], 0))
    assert business_revision.stage_index == 1
    assert business_revision.predecessor_review_id == review["id"]
    stage_context = orchestrator._stage_for_parent(
        SimpleNamespace(id=historical_audit["id"], parent_agent_run_id=historical["id"]))
    assert stage_context == {"stage_index": 1, "predecessor_review_id": review["id"]}
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: None)
    assert store.get_review_candidate(historical["id"])["candidate_json"] == historical_json

    supplement = store.record_candidate_supplement(consumer["id"], "new fact")
    assert supplement["candidate_id"] == consumer["id"]
    assert store.get_review_candidate(consumer["id"])["invalidation_reason"] == "supplement"
    assert db.execute("select count(*) from actions where record_type in ('review_candidates','candidate_reviews','candidate_selections','candidate_supplements')").fetchone()[0] == 0
    assert [row[0] for row in db.execute("select consumer_run_id from run_adopted_results order by consumer_run_id")] == [consumer["id"], later["id"], historical["id"], revised["id"]]


def test_native_store_rejects_stale_generation_and_rolls_back_child_rowids(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    body = {"outcome": "needs_human", "stage_index": 0, "predecessor_review_id": None,
        "decision_options": [{"key": "stop", "terminal_outcome": "skipped", "reason": "stop"}]}
    with db:
        candidate = store._adopt_review_candidate_in_connection(db, consumer, body)
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        review_body = {"outcome": "approve", "candidate_digest": candidate["candidate_digest"]}
        review = store._adopt_candidate_review_in_connection(db, consumer["id"], audit["id"], review_body)
    db.commit()
    task_entity_storage.update_task_cas(db, task_id=task["id"],
        expected={"execution_generation": "gen-1"}, changes={"execution_generation": "gen-2"})
    db.commit()
    for historical in (store.get_review_candidate(consumer["id"]),
                       store.adopted_candidate_for_consumer_run(consumer["id"])):
        assert historical["candidate_json"] == candidate["candidate_json"]
        assert historical["candidate_digest"] == candidate["candidate_digest"]
        assert historical["execution_generation"] == "gen-1"
    assert store.get_review_candidate(-1) is None
    assert review["id"] != review["audit_run_id"]
    assert store.current_reviewed_candidate(task["id"], "gen-1") is None
    try:
        store.select_candidate_option(consumer["id"], review["id"], "stop")
    except ValueError as exc:
        assert str(exc) == "candidate generation mismatch"
    else:
        raise AssertionError("stale generation candidate remained actionable")

    # The Store's in-transaction adoption write and child rows share the
    # caller's transaction; rollback leaves neither rows nor consumed ROWIDs.
    db.execute("begin")
    revision = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "execution_generation": "gen-2", "role": "consumer",
        "proposal_revision": 1, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    store._adopt_review_candidate_in_connection(db, revision, body)
    db.execute("insert into run_adopted_supplements(consumer_run_id,instruction,created_at) values(?,?,?)",
        (consumer["id"], "rolled back", "now"))
    db.rollback()
    assert db.execute("select count(*) from run_adopted_results where consumer_run_id=?", (revision["id"],)).fetchone()[0] == 0
    assert db.execute("select count(*) from run_adopted_supplements").fetchone()[0] == 0
    db.execute("insert into run_adopted_supplements(consumer_run_id,instruction,created_at) values(?,?,?)",
        (consumer["id"], "kept", "now"))
    assert db.execute("select id from run_adopted_supplements").fetchone()[0] == 1
    assert db.execute("select count(*) from sqlite_sequence where name like 'run_adopted_%'").fetchone()[0] == 0


def test_native_run_result_reader_uses_native_fields_without_attempt(monkeypatch, tmp_path):
    from app import native_trajectory

    _store, db, _task, run = _native_store(monkeypatch)
    body = {"outcome": "approve", "candidate_digest": "native-digest"}
    transcript = tmp_path / "native.jsonl"
    transcript.write_text(json.dumps({"type": "response_item", "payload": {"type": "message",
        "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(body)}]}}) + "\n")
    run = run_entity_storage.update_run_cas(db, run_id=run["id"], expected={"status": "completed"},
        changes={"session_id": "native-session", "transcript_start": 0, "transcript_end": 1})
    db.commit()
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: transcript)
    assert json.loads(native_trajectory.read_native_result_json(db, run)) == body
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: None)
    assert native_trajectory.read_native_result_json(db, run) == ""


def test_current_review_projection_follows_review_id_when_audit_adoption_is_reversed(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    body = {"outcome": "proposal", "summary": "review order",
            "stage_index": 0, "predecessor_review_id": None,
            "decision_options": [{"key": "stop", "terminal_outcome": "skipped", "reason": "stop"}]}
    with db:
        store._adopt_review_candidate_in_connection(db, consumer, body)
        first_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        last_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 1, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        digest = store.get_review_candidate(consumer["id"])["candidate_digest"]
        last_review = store._adopt_candidate_review_in_connection(db, consumer["id"], last_audit["id"],
            {"outcome": "approve", "candidate_digest": digest})
        first_review = store._adopt_candidate_review_in_connection(db, consumer["id"], first_audit["id"],
            {"outcome": "approve", "candidate_digest": digest})

    assert first_audit["id"] < last_audit["id"]
    assert last_review["id"] < first_review["id"]
    current = store.current_reviewed_candidate(task["id"], "gen-1")
    assert current["review_id"] == first_review["id"]
    assert current["audit_run_id"] == first_audit["id"]
