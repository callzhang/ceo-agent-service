"""Durable, generation-bound Consumer candidates and system execution progress."""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


REVIEWED_CANDIDATE_DDL = """
create index if not exists idx_reply_tasks_business_object_key on reply_tasks(business_object_key);
create table if not exists review_candidates (
 id integer primary key, task_id integer not null references reply_tasks(id),
 execution_generation text not null, consumer_run_id integer not null unique references agent_runs(id),
 stage_index integer not null, proposal_revision integer not null,
 predecessor_review_id integer references candidate_reviews(id),
 candidate_digest text not null, candidate_json text not null,
 invalidated_at text not null default '', invalidation_reason text not null default '',
 created_at text not null default current_timestamp
);
create index if not exists idx_review_candidates_task on review_candidates(task_id,execution_generation,id);
create table if not exists candidate_reviews (
 id integer primary key, candidate_id integer not null references review_candidates(id),
 audit_run_id integer not null unique references agent_runs(id),
 decision text not null check(decision in ('approve','return','reject')),
 candidate_digest text not null, result_json text not null,
 created_at text not null default current_timestamp
);
create index if not exists idx_candidate_reviews_candidate on candidate_reviews(candidate_id,id);
create table if not exists candidate_selections (
 id integer primary key, candidate_id integer not null unique references review_candidates(id),
 review_id integer not null references candidate_reviews(id), option_key text not null,
 branch_json text not null, status text not null default 'selected',
 created_at text not null default current_timestamp
);
create table if not exists candidate_supplements (
 id integer primary key, candidate_id integer not null references review_candidates(id),
 instruction text not null, created_at text not null default current_timestamp
);
create table if not exists candidate_executions (
 id integer primary key, candidate_id integer not null unique references review_candidates(id),
 review_id integer not null references candidate_reviews(id), selection_id integer references candidate_selections(id),
 status text not null check(status in ('running','done','skipped','failed','retry','uncertain')),
 lease_owner text not null default '', lease_expires_at text not null default '',
 result_json text not null default '{}', created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp
);
create table if not exists candidate_action_attempts (
 id integer primary key, execution_id integer not null references candidate_executions(id),
 action_index integer not null, external_action_key text not null,
 status text not null check(status in ('dispatched','verified','confirmed_no_effect','uncertain','failed')),
 result_json text not null default '{}', created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp,
 unique(execution_id,action_index)
);
create index if not exists idx_candidate_action_key on candidate_action_attempts(external_action_key);
"""
REVIEWED_CANDIDATE_TABLES = (
    "review_candidates", "candidate_reviews", "candidate_selections",
    "candidate_supplements", "candidate_executions", "candidate_action_attempts",
)
REVIEWED_CANDIDATE_INDEXES = (
    "idx_reply_tasks_business_object_key", "idx_review_candidates_task",
    "idx_candidate_reviews_candidate", "idx_candidate_action_key",
)


def _plain(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("result must be a JSON object")
    return value


def _json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _clock(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


class ReviewedCandidateStoreMixin:
    def _candidate_context(self, db: sqlite3.Connection, candidate_id: int) -> sqlite3.Row:
        row = db.execute("""select c.*, t.execution_generation as current_generation,
            t.business_object_key from review_candidates c join reply_tasks t on t.id=c.task_id
            where c.id=?""", (candidate_id,)).fetchone()
        if row is None:
            raise ValueError("candidate does not exist")
        if row["execution_generation"] != row["current_generation"]:
            raise ValueError("candidate generation mismatch")
        return row

    def _review_context(self, db: sqlite3.Connection, candidate_id: int, review_id: int) -> tuple[sqlite3.Row, sqlite3.Row]:
        candidate = self._candidate_context(db, candidate_id)
        review = db.execute("select * from candidate_reviews where id=? and candidate_id=?", (review_id, candidate_id)).fetchone()
        if review is None or review["decision"] != "approve" or review["candidate_digest"] != candidate["candidate_digest"]:
            raise ValueError("approved review mismatch")
        if candidate["invalidated_at"]:
            raise ValueError("candidate invalidated")
        return candidate, review

    def _check_historical_runtime_refusal(self, db: sqlite3.Connection, business_object_key: str) -> None:
        rows = db.execute("""select r.structured_error_json from agent_runs r
            join reply_tasks t on t.id=r.reply_task_id
            where t.business_object_key=? and r.structured_error_json<>''""", (business_object_key,))
        for row in rows:
            try:
                error = json.loads(row[0])
            except json.JSONDecodeError:
                continue
            if isinstance(error, dict) and (error.get("code") == "provider_risk_rejected" or error.get("source_code") == "provider_risk_rejected"):
                raise ValueError("historical_runtime_risk_refusal")

    def persist_review_candidate(self, task: Any, consumer_run: Any, prepared_result: Any) -> dict[str, Any]:
        body = _json(prepared_result)
        digest = hashlib.sha256(body.encode()).hexdigest()
        data = json.loads(body)
        if data.get("outcome") not in ("proposal", "needs_human", "no_action"):
            raise ValueError("result is not a reviewable candidate")
        with self._immediate_write_transaction() as db:
            task_row = db.execute("select execution_generation from reply_tasks where id=?", (task.id,)).fetchone()
            run = db.execute("select * from agent_runs where id=?", (consumer_run.id,)).fetchone()
            if (task_row is None or run is None or task_row[0] != task.execution_generation
                or run["reply_task_id"] != task.id or run["execution_generation"] != task.execution_generation
                or run["role"] != "consumer" or run["status"] != "completed"
                or run["final_result_json"] != body):
                raise ValueError("completed Consumer candidate mismatch")
            latest = db.execute("""select id from agent_runs where reply_task_id=?
                and execution_generation=? and role='consumer' and status='completed'
                and json_valid(final_result_json)
                and json_extract(final_result_json, '$.outcome') in ('proposal', 'needs_human', 'no_action')
                and proposal_revision=(select max(current_run.proposal_revision) from agent_runs current_run
                    where current_run.reply_task_id=? and current_run.execution_generation=?
                      and current_run.role='consumer')
                order by proposal_revision desc, turn_attempt desc, id desc limit 1""",
                (task.id, task.execution_generation, task.id, task.execution_generation)).fetchone()
            if latest is None or latest["id"] != consumer_run.id:
                raise ValueError("stale Consumer candidate")
            stage = data.get("stage_index", 0)
            predecessor = data.get("predecessor_review_id")
            if not isinstance(stage, int) or stage < 0:
                raise ValueError("invalid candidate stage")
            if predecessor is not None:
                prior = db.execute("""select c.task_id, c.execution_generation, c.stage_index,
                    r.decision, e.status as execution_status
                    from candidate_reviews r join review_candidates c on c.id=r.candidate_id
                    left join candidate_executions e on e.review_id=r.id
                    where r.id=?""", (predecessor,)).fetchone()
                if (prior is None or prior["task_id"] != task.id
                    or prior["execution_generation"] != task.execution_generation
                    or prior["stage_index"] != stage - 1 or prior["decision"] != "approve"
                    or prior["execution_status"] not in ("done", "skipped")):
                    raise ValueError("candidate predecessor mismatch")
            db.execute("""insert or ignore into review_candidates
                (task_id,execution_generation,consumer_run_id,stage_index,proposal_revision,predecessor_review_id,candidate_digest,candidate_json)
                values (?,?,?,?,?,?,?,?)""", (task.id, task.execution_generation, consumer_run.id, stage, run["proposal_revision"], predecessor, digest, body))
            saved = db.execute("select * from review_candidates where consumer_run_id=?", (consumer_run.id,)).fetchone()
            if saved["candidate_digest"] != digest or saved["candidate_json"] != body:
                raise ValueError("candidate content conflict")
            return dict(saved)

    def get_review_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute("select * from review_candidates where id=?", (candidate_id,)).fetchone())

    def reviewed_candidate_for_audit_run(self, audit_run_id: int) -> dict[str, Any] | None:
        """Read an old reviewed question for History without making it actionable."""
        with self._connect() as db:
            return _row(db.execute("""select c.*, r.id as review_id, r.audit_run_id, r.decision,
                s.id as selection_id, s.option_key, s.status as selection_status
                from candidate_reviews r join review_candidates c on c.id=r.candidate_id
                left join candidate_selections s on s.candidate_id=c.id
                where r.audit_run_id=?""", (audit_run_id,)).fetchone())

    def record_candidate_review(self, candidate_id: int, audit_run_id: int, result: Any) -> dict[str, Any]:
        body = _json(result)
        data = json.loads(body)
        decision = data.get("outcome")
        if decision not in ("approve", "return", "reject"):
            raise ValueError("technical Audit failure is not a business review")
        with self._immediate_write_transaction() as db:
            candidate = self._candidate_context(db, candidate_id)
            if candidate["invalidated_at"]:
                raise ValueError("candidate invalidated")
            run = db.execute("select * from agent_runs where id=?", (audit_run_id,)).fetchone()
            if (run is None or run["status"] != "completed" or run["role"] != "audit"
                or run["reply_task_id"] != candidate["task_id"] or run["execution_generation"] != candidate["execution_generation"]
                or run["proposal_revision"] != candidate["proposal_revision"] or run["parent_agent_run_id"] != candidate["consumer_run_id"]
                or run["final_result_json"] != body or data.get("candidate_digest") != candidate["candidate_digest"]):
                raise ValueError("completed Audit review mismatch")
            db.execute("insert or ignore into candidate_reviews (candidate_id,audit_run_id,decision,candidate_digest,result_json) values (?,?,?,?,?)", (candidate_id, audit_run_id, decision, candidate["candidate_digest"], body))
            saved = db.execute("select * from candidate_reviews where audit_run_id=?", (audit_run_id,)).fetchone()
            if saved["candidate_id"] != candidate_id or saved["decision"] != decision or saved["result_json"] != body:
                raise ValueError("review content conflict")
            return dict(saved)

    def current_reviewed_candidate(self, task_id: int, generation: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("""select c.*, r.id as review_id, r.audit_run_id, r.decision, s.id as selection_id,
                s.option_key, s.branch_json, s.status as selection_status
                from review_candidates c join reply_tasks t on t.id=c.task_id
                join candidate_reviews r on r.candidate_id=c.id
                left join candidate_selections s on s.candidate_id=c.id
                where c.task_id=? and c.execution_generation=? and t.execution_generation=?
                and c.invalidated_at='' and r.decision='approve'
                and c.id=(select max(id) from review_candidates where task_id=? and execution_generation=?)
                order by r.id desc limit 1""", (task_id, generation, generation, task_id, generation)).fetchone()
            return _row(row)

    def select_candidate_option(self, candidate_id: int, review_id: int, option_key: str) -> dict[str, Any]:
        with self._immediate_write_transaction() as db:
            candidate, _ = self._review_context(db, candidate_id, review_id)
            if db.execute("select max(id) from review_candidates where task_id=? and execution_generation=?", (candidate["task_id"], candidate["execution_generation"])).fetchone()[0] != candidate_id:
                raise ValueError("stale candidate")
            options = json.loads(candidate["candidate_json"]).get("decision_options", [])
            branch = next((option for option in options if option.get("key") == option_key), None)
            if branch is None or (branch.get("plan") is None and branch.get("terminal_outcome") != "skipped"):
                raise ValueError("unbound option")
            prior = db.execute("select * from candidate_selections where candidate_id=?", (candidate_id,)).fetchone()
            if prior is not None:
                if prior["review_id"] != review_id or prior["option_key"] != option_key:
                    raise ValueError("selection_conflict")
                return dict(prior)
            db.execute("insert into candidate_selections(candidate_id,review_id,option_key,branch_json) values (?,?,?,?)", (candidate_id,review_id,option_key,_json(branch)))
            return dict(db.execute("select * from candidate_selections where candidate_id=?", (candidate_id,)).fetchone())

    def record_candidate_supplement(self, candidate_id: int, instruction: str) -> dict[str, Any]:
        if not instruction.strip():
            raise ValueError("instruction is empty")
        with self._immediate_write_transaction() as db:
            self._candidate_context(db, candidate_id)
            db.execute("insert into candidate_supplements(candidate_id,instruction) values (?,?)", (candidate_id,instruction))
            db.execute("update review_candidates set invalidated_at=current_timestamp,invalidation_reason='supplement' where id=? and invalidated_at=''", (candidate_id,))
            return dict(db.execute("select * from candidate_supplements where id=last_insert_rowid()").fetchone())

    def invalidate_review_candidate(self, candidate_id: int, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("invalidation reason is empty")
        with self._immediate_write_transaction() as db:
            self._candidate_context(db, candidate_id)
            db.execute("update review_candidates set invalidated_at=current_timestamp,invalidation_reason=? where id=? and invalidated_at=''", (reason,candidate_id))
            return dict(db.execute("select * from review_candidates where id=?", (candidate_id,)).fetchone())

    def get_candidate_execution(self, candidate_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute("select * from candidate_executions where candidate_id=?", (candidate_id,)).fetchone())

    def wake_selected_candidate_execution(self, candidate_id: int, review_id: int) -> dict[str, Any]:
        """Queue the selected plan in the same task generation, without a new Agent turn."""
        with self._immediate_write_transaction() as db:
            candidate, _ = self._review_context(db, candidate_id, review_id)
            if json.loads(candidate["candidate_json"]).get("outcome") != "needs_human":
                raise ValueError("candidate is not a human decision")
            selection = db.execute("select * from candidate_selections where candidate_id=? and review_id=?", (candidate_id,review_id)).fetchone()
            if selection is None:
                raise ValueError("candidate choice is missing")
            latest_id = db.execute("select max(id) from review_candidates where task_id=? and execution_generation=?", (candidate["task_id"],candidate["execution_generation"])).fetchone()[0]
            if latest_id != candidate_id:
                raise ValueError("stale candidate")
            task = db.execute("select * from reply_tasks where id=?", (candidate["task_id"],)).fetchone()
            if task["status"] == "needs_human":
                db.execute("""update reply_tasks set status='pending',available_at='',locked_at=null,
                    updated_at=current_timestamp where id=? and execution_generation=? and status='needs_human'""",
                    (candidate["task_id"],candidate["execution_generation"]))
            elif task["status"] not in ("pending", "processing", "done", "skipped"):
                raise ValueError("selected candidate task is unavailable")
            return dict(db.execute("select * from reply_tasks where id=?", (candidate["task_id"],)).fetchone())

    def claim_candidate_execution(self, candidate_id: int, review_id: int, owner: str, lease_seconds: int) -> dict[str, Any] | None:
        if not owner.strip() or lease_seconds <= 0:
            raise ValueError("invalid execution lease")
        with self._immediate_write_transaction() as db:
            if self._candidate_context(db, candidate_id)["invalidated_at"]:
                return None
            candidate, _ = self._review_context(db, candidate_id, review_id)
            if db.execute("select max(id) from review_candidates where task_id=? and execution_generation=?", (candidate["task_id"],candidate["execution_generation"])).fetchone()[0] != candidate_id:
                return None
            data = json.loads(candidate["candidate_json"])
            selection = db.execute("select * from candidate_selections where candidate_id=?", (candidate_id,)).fetchone()
            if data.get("outcome") == "needs_human" and selection is None:
                return None
            selected_branch = json.loads(selection["branch_json"]) if selection else None
            is_stop = data.get("outcome") == "no_action" or (
                isinstance(selected_branch, dict)
                and selected_branch.get("terminal_outcome") == "skipped"
            )
            if not is_stop:
                self._check_historical_runtime_refusal(db, candidate["business_object_key"])
            now = _utc()
            expiry = _clock(now + timedelta(seconds=lease_seconds))
            now_text = _clock(now)
            old = db.execute("select * from candidate_executions where candidate_id=?", (candidate_id,)).fetchone()
            if old is not None:
                if old["review_id"] != review_id or old["selection_id"] != (selection["id"] if selection else None):
                    raise ValueError("execution plan mismatch")
                if old["status"] in ("done","skipped","failed") or (old["status"] == "running" and old["lease_expires_at"] > now_text and old["lease_owner"] != owner):
                    return None
                db.execute("update candidate_executions set status='running',lease_owner=?,lease_expires_at=?,updated_at=current_timestamp where id=?", (owner,expiry,old["id"]))
            else:
                db.execute("insert into candidate_executions(candidate_id,review_id,selection_id,status,lease_owner,lease_expires_at) values (?,?,?,'running',?,?)", (candidate_id,review_id,selection["id"] if selection else None,owner,expiry))
            return dict(db.execute("select * from candidate_executions where candidate_id=?", (candidate_id,)).fetchone())

    def renew_candidate_execution_claim(self, execution_id: int, owner: str, lease_seconds: int) -> dict[str, Any]:
        if lease_seconds <= 0:
            raise ValueError("invalid lease")
        with self._immediate_write_transaction() as db:
            self._leased_execution(db, execution_id, owner)
            db.execute("update candidate_executions set lease_expires_at=?,updated_at=current_timestamp where id=?", (_clock(_utc()+timedelta(seconds=lease_seconds)),execution_id))
            return dict(db.execute("select * from candidate_executions where id=?", (execution_id,)).fetchone())

    def _leased_execution(self, db: sqlite3.Connection, execution_id: int, owner: str) -> sqlite3.Row:
        """Validate a dispatch lease, including after its candidate is superseded.

        The provider may return a receipt after invalidation or a new generation.
        That receipt must still be recorded against the original dispatched action.
        """
        execution = db.execute("select * from candidate_executions where id=?", (execution_id,)).fetchone()
        if execution is None or execution["status"] != "running" or execution["lease_owner"] != owner or execution["lease_expires_at"] <= _clock(_utc()):
            raise ValueError("execution lease lost")
        return execution

    def _owned_execution(self, db: sqlite3.Connection, execution_id: int, owner: str) -> sqlite3.Row:
        execution = self._leased_execution(db, execution_id, owner)
        candidate, _ = self._review_context(db, execution["candidate_id"], execution["review_id"])
        self._check_historical_runtime_refusal(db, candidate["business_object_key"])
        return execution

    def finish_candidate_execution(
        self, execution_id: int, owner: str, status: str, result: Any,
        *, invalidate_reason: str = "",
    ) -> dict[str, Any]:
        if status not in ("done","skipped","failed","retry","uncertain"):
            raise ValueError("invalid execution status")
        body = _json(result)
        if invalidate_reason and (
            status != "failed" or invalidate_reason != "business_state_changed"
            or json.loads(body).get("error", {}).get("code") != invalidate_reason
        ):
            raise ValueError("execution invalidation does not match result")
        with self._immediate_write_transaction() as db:
            execution = self._leased_execution(db, execution_id, owner)
            candidate = db.execute("select * from review_candidates where id=?", (execution["candidate_id"],)).fetchone()
            data = json.loads(candidate["candidate_json"])
            selection = db.execute("select * from candidate_selections where id=?", (execution["selection_id"],)).fetchone() if execution["selection_id"] else None
            branch = json.loads(selection["branch_json"]) if selection else None
            plan = branch.get("plan") if branch else data.get("proposal")
            if status == "skipped" and not (
                (branch is not None and branch.get("terminal_outcome") == "skipped")
                or (branch is None and data.get("outcome") == "no_action")
            ):
                raise ValueError("skipped requires selected stop branch")
            if status == "done":
                actions = plan.get("actions", []) if isinstance(plan, dict) else []
                attempts = db.execute("select action_index,status from candidate_action_attempts where execution_id=? order by action_index", (execution_id,)).fetchall()
                if not actions or len(attempts) != len(actions) or any(row["action_index"] != index or row["status"] != "verified" for index,row in enumerate(attempts)):
                    raise ValueError("execution actions are not verified")
            db.execute("update candidate_executions set status=?,result_json=?,lease_owner='',lease_expires_at='',updated_at=current_timestamp where id=?", (status,body,execution_id))
            if invalidate_reason:
                db.execute("""update review_candidates set invalidated_at=current_timestamp,
                    invalidation_reason=? where id=? and invalidated_at=''""",
                    (invalidate_reason, execution["candidate_id"]))
            return dict(db.execute("select * from candidate_executions where id=?", (execution_id,)).fetchone())

    def begin_candidate_action(self, execution_id: int, owner: str, action_index: int, external_action_key: str) -> dict[str, Any]:
        if action_index < 0 or not external_action_key.strip():
            raise ValueError("invalid action identity")
        with self._immediate_write_transaction() as db:
            execution = self._owned_execution(db, execution_id, owner)
            candidate = self._candidate_context(db, execution["candidate_id"])
            data = json.loads(candidate["candidate_json"])
            selection = db.execute("select * from candidate_selections where id=?", (execution["selection_id"],)).fetchone() if execution["selection_id"] else None
            plan = json.loads(selection["branch_json"]).get("plan") if selection else data.get("proposal")
            actions = plan.get("actions", []) if isinstance(plan, dict) else []
            if action_index >= len(actions):
                raise ValueError("action index outside approved plan")
            prior_actions = db.execute("select action_index,status from candidate_action_attempts where execution_id=? and action_index<?", (execution_id,action_index)).fetchall()
            if len(prior_actions) != action_index or any(row["status"] != "verified" for row in prior_actions):
                raise ValueError("preceding action is not verified")
            existing_success = db.execute("select * from external_action_results where external_action_key=?", (external_action_key,)).fetchone()
            if existing_success is None:
                prior_key = db.execute("select execution_id,status from candidate_action_attempts where external_action_key=? and execution_id<>? order by id desc limit 1", (external_action_key,execution_id)).fetchone()
                if prior_key is not None and prior_key["status"] != "confirmed_no_effect":
                    raise ValueError("action_key_requires_reconciliation")
            if existing_success is not None:
                approved_action = actions[action_index]
                if (existing_success["business_object_key"] != candidate["business_object_key"]
                    or existing_success["action_identity"] != approved_action["action_identity"]
                    or existing_success["operation"] != approved_action["operation"]
                    or json.loads(existing_success["target_identifiers_json"]) != approved_action.get("target")):
                    raise ValueError("external action key belongs to another action")
            old = db.execute("select * from candidate_action_attempts where execution_id=? and action_index=?", (execution_id,action_index)).fetchone()
            if old is not None:
                if old["external_action_key"] != external_action_key:
                    raise ValueError("action key conflict")
                if existing_success is not None and old["status"] != "verified":
                    db.execute("update candidate_action_attempts set status='verified',updated_at=current_timestamp where id=?", (old["id"],))
                elif old["status"] == "dispatched":
                    db.execute("update candidate_action_attempts set status='uncertain',updated_at=current_timestamp where id=?", (old["id"],))
                elif old["status"] == "confirmed_no_effect":
                    db.execute("update candidate_action_attempts set status='dispatched',updated_at=current_timestamp where id=?", (old["id"],))
                return dict(db.execute("select * from candidate_action_attempts where id=?", (old["id"],)).fetchone())
            db.execute("insert into candidate_action_attempts(execution_id,action_index,external_action_key,status) values (?,?,?,?)", (execution_id,action_index,external_action_key,"verified" if existing_success else "dispatched"))
            return dict(db.execute("select * from candidate_action_attempts where id=last_insert_rowid()").fetchone())

    def record_candidate_action_outcome(self, execution_id: int, owner: str, action_index: int, status: str, result: Any) -> dict[str, Any]:
        if status not in ("verified","confirmed_no_effect","uncertain","failed"):
            raise ValueError("invalid action outcome")
        body = _json(result)
        with self._immediate_write_transaction() as db:
            self._leased_execution(db, execution_id, owner)
            attempt = db.execute("select * from candidate_action_attempts where execution_id=? and action_index=?", (execution_id,action_index)).fetchone()
            if attempt is None:
                raise ValueError("action was not dispatched")
            if status == "verified" and db.execute("select 1 from external_action_results where external_action_key=?", (attempt["external_action_key"],)).fetchone() is None:
                raise ValueError("verified action lacks successful ledger receipt")
            db.execute("update candidate_action_attempts set status=?,result_json=?,updated_at=current_timestamp where id=?", (status,body,attempt["id"]))
            return dict(db.execute("select * from candidate_action_attempts where id=?", (attempt["id"],)).fetchone())

    def list_candidate_action_attempts(self, execution_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("select * from candidate_action_attempts where execution_id=? order by action_index", (execution_id,))]

    def record_candidate_external_action(self, execution_id: int, owner: str, action_index: int, external_action_key: str, operation: str, target_identifiers: dict[str, Any], provider_result: dict[str, Any]) -> dict[str, Any]:
        target_json = _json(target_identifiers)
        provider_json = _json(provider_result)
        result_digest = hashlib.sha256(provider_json.encode()).hexdigest()
        with self._immediate_write_transaction() as db:
            execution = self._leased_execution(db, execution_id, owner)
            candidate = db.execute("""select c.*, t.business_object_key from review_candidates c
                join reply_tasks t on t.id=c.task_id where c.id=?""", (execution["candidate_id"],)).fetchone()
            attempt = db.execute("select * from candidate_action_attempts where execution_id=? and action_index=? and external_action_key=?", (execution_id,action_index,external_action_key)).fetchone()
            if attempt is None:
                raise ValueError("action dispatch identity mismatch")
            data = json.loads(candidate["candidate_json"])
            selection = db.execute("select * from candidate_selections where id=?", (execution["selection_id"],)).fetchone() if execution["selection_id"] else None
            plan = json.loads(selection["branch_json"]).get("plan") if selection else data.get("proposal")
            action = plan["actions"][action_index]
            if action.get("operation") != operation or action.get("target") != target_identifiers:
                raise ValueError("approved action mismatch")
            db.execute("""insert or ignore into external_action_results
                (external_action_key,business_object_key,action_identity,operation,target_identifiers_json,provider_result_json,result_digest,first_agent_run_id)
                values (?,?,?,?,?,?,?,?)""", (external_action_key,candidate["business_object_key"],action["action_identity"],operation,target_json,provider_json,result_digest,candidate["consumer_run_id"]))
            saved = db.execute("select * from external_action_results where external_action_key=?", (external_action_key,)).fetchone()
            if (saved["business_object_key"] != candidate["business_object_key"] or saved["action_identity"] != action["action_identity"]
                or saved["operation"] != operation or saved["target_identifiers_json"] != target_json or saved["provider_result_json"] != provider_json):
                raise ValueError("conflicting external action result")
            db.execute("update candidate_action_attempts set status='verified',result_json=?,updated_at=current_timestamp where id=?", (provider_json,attempt["id"]))
            return dict(saved)

    def get_candidate_external_action(self, external_action_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute("select * from external_action_results where external_action_key=?", (external_action_key,)).fetchone())

    def get_verified_action_source(self, external_action_key: str) -> dict[str, Any] | None:
        """Resolve a successful ledger key to the branch actually dispatched."""
        with self._connect() as db:
            return _row(db.execute("""select c.id as candidate_id, c.candidate_json,
                    a.action_index, s.branch_json, e.selection_id
                from external_action_results r
                join review_candidates c on c.consumer_run_id=r.first_agent_run_id
                join candidate_executions e on e.candidate_id=c.id
                join candidate_action_attempts a on a.execution_id=e.id
                    and a.external_action_key=r.external_action_key and a.status='verified'
                left join candidate_selections s on s.id=e.selection_id
                where r.external_action_key=? limit 1""", (external_action_key,)).fetchone())

    def get_candidate_review_for_audit_run(self, audit_run_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute("select * from candidate_reviews where audit_run_id=?", (audit_run_id,)).fetchone())

    def list_verified_candidate_actions(self, task_id: int) -> list[dict[str, Any]]:
        """Return immutable receipts for the business object's verified stages."""
        with self._connect() as db:
            rows = db.execute("""select a.*, c.stage_index, c.id as candidate_id,
                c.execution_generation, r.operation, r.action_identity,
                r.target_identifiers_json, r.provider_result_json
                from candidate_action_attempts a
                join candidate_executions e on e.id=a.execution_id
                join review_candidates c on c.id=e.candidate_id
                join external_action_results r on r.external_action_key=a.external_action_key
                where c.task_id=? and a.status='verified'
                order by c.id, a.action_index""", (task_id,)).fetchall()
            return [dict(row) for row in rows]

    def list_human_decision_evidence(self, task_id: int) -> list[dict[str, Any]]:
        """Read immutable answers/new facts for this business object across generations.

        The returned branch is the exact historical selection, not an approval
        to execute it after the candidate has changed or been invalidated.
        """
        with self._connect() as db:
            task = db.execute("select business_object_key from reply_tasks where id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task does not exist")
            rows = db.execute("""select * from (
                select 'selection' as kind, s.id as source_record_id,
                s.created_at, s.option_key, s.branch_json, '' as instruction,
                c.id as candidate_id, c.execution_generation, c.stage_index,
                c.candidate_digest, c.candidate_json, c.invalidated_at,
                c.invalidation_reason, r.id as review_id, r.audit_run_id
                from candidate_selections s
                join review_candidates c on c.id=s.candidate_id
                join candidate_reviews r on r.id=s.review_id and r.candidate_id=c.id
                  and r.decision='approve' and r.candidate_digest=c.candidate_digest
                join reply_tasks t on t.id=c.task_id
                where t.business_object_key=?
                union all
                select 'supplement' as kind, supplement.id as source_record_id,
                supplement.created_at, '' as option_key, '' as branch_json,
                supplement.instruction,
                c.id as candidate_id, c.execution_generation, c.stage_index,
                c.candidate_digest, c.candidate_json, c.invalidated_at,
                c.invalidation_reason, r.id as review_id, r.audit_run_id
                from candidate_supplements supplement
                join review_candidates c on c.id=supplement.candidate_id
                join candidate_reviews r on r.id=(select max(approved.id)
                    from candidate_reviews approved where approved.candidate_id=c.id
                      and approved.decision='approve'
                      and approved.candidate_digest=c.candidate_digest)
                join reply_tasks t on t.id=c.task_id
                where t.business_object_key=?
                ) as events
                order by events.created_at, events.candidate_id,
                    case when events.kind='selection' then 0 else 1 end,
                    events.source_record_id""",
                (task["business_object_key"], task["business_object_key"])).fetchall()
        evidence: list[dict[str, Any]] = []
        for row in rows:
            question = json.loads(row["candidate_json"])
            evidence.append({
                "kind": row["kind"], "source_record_id": row["source_record_id"],
                "created_at": row["created_at"], "candidate_id": row["candidate_id"],
                "review_id": row["review_id"], "audit_run_id": row["audit_run_id"],
                "candidate_digest": row["candidate_digest"],
                "execution_generation": row["execution_generation"],
                "stage_index": row["stage_index"],
                "question_summary": question.get("summary", ""),
                "needs_human_reason": question.get("needs_human_reason", ""),
                "decision_basis": question.get("decision_basis"),
                "option_key": row["option_key"] or None,
                "selected_branch": json.loads(row["branch_json"]) if row["branch_json"] else None,
                "instruction": row["instruction"] or None,
                "invalidated_at": row["invalidated_at"],
                "invalidation_reason": row["invalidation_reason"],
            })
        return evidence
