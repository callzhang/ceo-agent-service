"""Durable, generation-bound Consumer candidates and system execution progress."""

from app.task20_definitions import task20_column, task20_projection
from app.task8_storage import task8_set_parts
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from app.logical_run_storage import AGENT_RUN_SQL
from app.model_action_schema import candidate_option, selected_branch
from app import shared_action_lifecycle
from app.execution_owner_storage import create_plan_execution, update_plan_execution
from app.action_storage import (
    REVIEW_CANDIDATE_SQL,
    CANDIDATE_REVIEW_SQL,
    CANDIDATE_SELECTION_SQL,
    CANDIDATE_SUPPLEMENT_SQL,
    CANDIDATE_EXECUTION_SQL,
    CANDIDATE_ACTION_ATTEMPT_SQL, candidate_action_attempt_row,
    EXTERNAL_ACTION_RESULT_SQL,
    insert_action,
)


REPLY_TASK_SQL = task20_projection('reply_tasks')




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


def _derived_selection(db, row, *, selection_key="selection_id"):
    """Preserve the response key; derive its value from the exact candidate FK."""
    result = _row(row)
    if result is not None:
        result["branch_json"] = (_json(selected_branch(db, result[selection_key]))
                                 if result[selection_key] is not None else None)
    return result


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _clock(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _db_timestamp(db: sqlite3.Connection) -> str:
    return db.execute("select current_timestamp").fetchone()[0]


class ReviewedCandidateStoreMixin:
    def _candidate_context(self, db: sqlite3.Connection, candidate_id: int) -> sqlite3.Row:
        row = db.execute(f"""select c.*, t.execution_generation as current_generation,
            t.business_object_key from ({REVIEW_CANDIDATE_SQL}) c join ({REPLY_TASK_SQL}) t on t.id=c.task_id
            where c.id=?""", (candidate_id,)).fetchone()
        if row is None:
            raise ValueError("candidate does not exist")
        if row["execution_generation"] != row["current_generation"]:
            raise ValueError("candidate generation mismatch")
        return row

    def _review_context(self, db: sqlite3.Connection, candidate_id: int, review_id: int) -> tuple[sqlite3.Row, sqlite3.Row]:
        candidate = self._candidate_context(db, candidate_id)
        review = db.execute(f"select * from ({CANDIDATE_REVIEW_SQL}) where id=? and candidate_id=?", (review_id, candidate_id)).fetchone()
        if review is None or review["decision"] != "approve" or review["candidate_digest"] != candidate["candidate_digest"]:
            raise ValueError("approved review mismatch")
        if candidate["invalidated_at"]:
            raise ValueError("candidate invalidated")
        return candidate, review

    def _check_historical_runtime_refusal(self, db: sqlite3.Connection, business_object_key: str) -> None:
        rows = db.execute(f"""select r.structured_error_json from ({AGENT_RUN_SQL}) r
            join ({REPLY_TASK_SQL}) t on t.id=r.reply_task_id
            where t.business_object_key=? and r.structured_error_json<>''""", (business_object_key,))
        for row in rows:
            try:
                error = json.loads(row[0])
            except json.JSONDecodeError:
                continue
            if isinstance(error, dict) and (error.get("code") == "provider_risk_rejected" or error.get("source_code") == "provider_risk_rejected"):
                raise ValueError("historical_runtime_risk_refusal")

    def _adopt_review_candidate_in_connection(self, db, run, prepared_result):
        """Freeze the prepared plan in the same transaction as Consumer completion."""
        body = _json(prepared_result)
        digest = hashlib.sha256(body.encode()).hexdigest()
        data = json.loads(body)
        task_row = db.execute(f"select execution_generation, channel from ({REPLY_TASK_SQL}) where id=?", (run["reply_task_id"],)).fetchone()
        if (task_row is not None and task_row["channel"] == "wechat"
            and "action" in data and "outcome" not in data):
            from app.dingtalk_models import CodexDecision

            CodexDecision.model_validate(data)
            if (task_row["execution_generation"] != run["execution_generation"]
                or run["role"] != "consumer" or run["status"] != "completed"):
                raise ValueError("completed WeChat decision mismatch")
            # The independent WeChat delivery pipeline does not adopt Audit plans.
            return
        if data.get("outcome") not in ("proposal", "needs_human", "no_action", "retry"):
            raise ValueError("result is not a reviewable candidate")
        if (task_row is None or run is None or task_row[0] != run["execution_generation"]
            or run["role"] != "consumer" or run["status"] != "completed"):
            raise ValueError("completed Consumer candidate mismatch")
        latest = db.execute(f"""select id from ({AGENT_RUN_SQL}) as agent_runs where reply_task_id=?
            and execution_generation=? and role='consumer' and status='completed'
            and proposal_revision=(select max(current_run.proposal_revision) from ({AGENT_RUN_SQL}) current_run
                where current_run.reply_task_id=? and current_run.execution_generation=?
                  and current_run.role='consumer')
            order by proposal_revision desc, turn_attempt desc, id desc limit 1""",
            (run["reply_task_id"], run["execution_generation"], run["reply_task_id"], run["execution_generation"])).fetchone()
        if latest is None or latest["id"] != run["id"]:
            raise ValueError("stale Consumer candidate")
        stage = data.get("stage_index", 0)
        predecessor = data.get("predecessor_review_id")
        if not isinstance(stage, int) or stage < 0:
            raise ValueError("invalid candidate stage")
        if predecessor is not None:
            prior = db.execute(f"""select c.task_id, c.execution_generation, c.stage_index,
                r.decision, e.status as execution_status
                from ({CANDIDATE_REVIEW_SQL}) r join ({REVIEW_CANDIDATE_SQL}) c on c.id=r.candidate_id
                left join ({CANDIDATE_EXECUTION_SQL}) e on e.review_id=r.id
                where r.id=?""", (predecessor,)).fetchone()
            if (prior is None or prior["task_id"] != run["reply_task_id"]
                or prior["execution_generation"] != run["execution_generation"]
                or prior["stage_index"] != stage - 1 or prior["decision"] != "approve"
                or prior["execution_status"] not in ("done", "skipped")):
                raise ValueError("candidate predecessor mismatch")
        shared_action_lifecycle.persist_plan(db, "reviewed", {
            "task_id": run["reply_task_id"], "execution_generation": run["execution_generation"],
            "consumer_run_id": run["id"], "stage_index": stage,
            "proposal_revision": run["proposal_revision"], "predecessor_review_id": predecessor,
            "candidate_digest": digest, "candidate_json": body,
        }, ignore=True)
        saved = db.execute(f"select * from ({REVIEW_CANDIDATE_SQL}) where consumer_run_id=?", (run["id"],)).fetchone()
        if saved["candidate_digest"] != digest or saved["candidate_json"] != body:
            raise ValueError("candidate content conflict")
        return dict(saved)

    def persist_review_candidate(self, task: Any, consumer_run: Any, prepared_result: Any) -> dict[str, Any]:
        """Validate the already adopted candidate; completed turns are immutable."""
        body = _json(prepared_result)
        with self._connect() as db:
            saved = db.execute(f"""select c.*, r.status as run_status, t.execution_generation as current_generation
                from ({REVIEW_CANDIDATE_SQL}) c join ({AGENT_RUN_SQL}) r on r.id=c.consumer_run_id
                join ({REPLY_TASK_SQL}) t on t.id=c.task_id where c.consumer_run_id=?""", (consumer_run.id,)).fetchone()
            if (saved is None or saved["task_id"] != task.id
                or saved["execution_generation"] != task.execution_generation
                or saved["current_generation"] != task.execution_generation
                or saved["run_status"] != "completed" or saved["candidate_json"] != body):
                raise ValueError("completed Consumer candidate mismatch")
            latest = db.execute(f"""select r.id from ({AGENT_RUN_SQL}) r join ({REVIEW_CANDIDATE_SQL}) c on c.consumer_run_id=r.id
                where r.reply_task_id=? and r.execution_generation=? and r.status='completed'
                  and r.proposal_revision=(select max(proposal_revision) from ({AGENT_RUN_SQL}) as agent_runs
                    where reply_task_id=? and execution_generation=? and role='consumer')
                order by r.proposal_revision desc, r.turn_attempt desc, r.id desc limit 1""",
                (task.id, task.execution_generation, task.id, task.execution_generation)).fetchone()
            if latest is None or latest["id"] != consumer_run.id:
                raise ValueError("stale Consumer candidate")
            return dict(saved)

    def adopted_candidate_for_consumer_run(self, run_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute(f"select * from ({REVIEW_CANDIDATE_SQL}) where consumer_run_id=?", (run_id,)).fetchone())

    def get_review_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute(f"select * from ({REVIEW_CANDIDATE_SQL}) where id=?", (candidate_id,)).fetchone())

    def reviewed_candidate_for_audit_run(self, audit_run_id: int) -> dict[str, Any] | None:
        """Read an old reviewed question for History without making it actionable."""
        with self._connect() as db:
            return _row(db.execute(f"""select c.*, r.id as review_id, r.audit_run_id, r.decision,
                s.id as selection_id, s.option_key, s.status as selection_status
                from ({CANDIDATE_REVIEW_SQL}) r join ({REVIEW_CANDIDATE_SQL}) c on c.id=r.candidate_id
                left join ({CANDIDATE_SELECTION_SQL}) s on s.candidate_id=c.id
                where r.audit_run_id=?""", (audit_run_id,)).fetchone())

    def _adopt_candidate_review_in_connection(self, db, candidate_id, audit_run_id, result):
        body = _json(result)
        data = json.loads(body)
        decision = data.get("outcome")
        if decision not in ("approve", "return", "reject"):
            raise ValueError("technical Audit failure is not a business review")
        candidate = self._candidate_context(db, candidate_id)
        if candidate["invalidated_at"]:
            raise ValueError("candidate invalidated")
        run = db.execute(f'select * from ({AGENT_RUN_SQL}) as agent_runs where id=?', (audit_run_id,)).fetchone()
        if (run is None or run["status"] != "completed" or run["role"] != "audit"
            or run["reply_task_id"] != candidate["task_id"] or run["execution_generation"] != candidate["execution_generation"]
            or run["proposal_revision"] != candidate["proposal_revision"] or run["parent_agent_run_id"] != candidate["consumer_run_id"]
            or data.get("candidate_digest") != candidate["candidate_digest"]):
            raise ValueError("completed Audit review mismatch")
        insert_action(db, "candidate_reviews", {
            "candidate_id": candidate_id, "audit_run_id": audit_run_id, "decision": decision,
            "candidate_digest": candidate["candidate_digest"], "result_json": body,
        }, ignore=True)
        saved = db.execute(f"select * from ({CANDIDATE_REVIEW_SQL}) where audit_run_id=?", (audit_run_id,)).fetchone()
        if saved["candidate_id"] != candidate_id or saved["decision"] != decision or saved["result_json"] != body:
            raise ValueError("review content conflict")
        return dict(saved)

    def record_candidate_review(self, candidate_id: int, audit_run_id: int, result: Any) -> dict[str, Any]:
        """Validate the adopted review or import an exactly matching native result."""
        body = _json(result)
        with self._immediate_write_transaction() as db:
            saved = db.execute(f"select * from ({CANDIDATE_REVIEW_SQL}) where audit_run_id=?", (audit_run_id,)).fetchone()
            run = db.execute(f'select * from ({AGENT_RUN_SQL}) as agent_runs where id=?', (audit_run_id,)).fetchone()
            from app.native_trajectory import read_native_result_json
            persisted_body = saved["result_json"] if saved else read_native_result_json(db, run) if run else ""
            if not persisted_body or _json(json.loads(persisted_body)) != body:
                raise ValueError("completed Audit review mismatch")
            return self._adopt_candidate_review_in_connection(db, candidate_id, audit_run_id, result)

    def current_reviewed_candidate(self, task_id: int, generation: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(f"""select c.*, r.id as review_id, r.audit_run_id, r.decision, s.id as selection_id,
                s.option_key, s.status as selection_status
                from ({REVIEW_CANDIDATE_SQL}) c join ({REPLY_TASK_SQL}) t on t.id=c.task_id
                join ({CANDIDATE_REVIEW_SQL}) r on r.candidate_id=c.id
                left join ({CANDIDATE_SELECTION_SQL}) s on s.candidate_id=c.id
                where c.task_id=? and c.execution_generation=? and t.execution_generation=?
                and c.invalidated_at='' and r.decision='approve'
                and c.id=(select max(id) from ({REVIEW_CANDIDATE_SQL}) where task_id=? and execution_generation=?)
                order by r.id desc limit 1""", (task_id, generation, generation, task_id, generation)).fetchone()
            return _derived_selection(db, row)

    def get_candidate_selection(self, candidate_id: int) -> dict[str, Any] | None:
        """Read the selection of this immutable candidate, including old stages."""
        with self._connect() as db:
            row = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where candidate_id=?",
                             (candidate_id,)).fetchone()
            return _derived_selection(db, row, selection_key="id")

    def select_candidate_option(self, candidate_id: int, review_id: int, option_key: str) -> dict[str, Any]:
        with self._immediate_write_transaction() as db:
            candidate, _ = self._review_context(db, candidate_id, review_id)
            if db.execute(f"select max(id) from ({REVIEW_CANDIDATE_SQL}) where task_id=? and execution_generation=?", (candidate["task_id"], candidate["execution_generation"])).fetchone()[0] != candidate_id:
                raise ValueError("stale candidate")
            branch = candidate_option(candidate["candidate_json"], option_key)
            if branch.get("plan") is None and branch.get("terminal_outcome") != "skipped":
                raise ValueError("unbound option")
            prior = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where candidate_id=?", (candidate_id,)).fetchone()
            if prior is not None:
                if prior["review_id"] != review_id or prior["option_key"] != option_key:
                    raise ValueError("selection_conflict")
                return _derived_selection(db, prior, selection_key="id")
            insert_action(db, "candidate_selections", {
                "candidate_id": candidate_id, "review_id": review_id,
                "option_key": option_key,
            }, selection_references=True)
            return _derived_selection(db, db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where candidate_id=?", (candidate_id,)).fetchone(), selection_key="id")

    def record_candidate_supplement(self, candidate_id: int, instruction: str) -> dict[str, Any]:
        if not instruction.strip():
            raise ValueError("instruction is empty")
        with self._immediate_write_transaction() as db:
            self._candidate_context(db, candidate_id)
            inserted = insert_action(db, "candidate_supplements", {
                "candidate_id": candidate_id, "instruction": instruction,
            })
            shared_action_lifecycle.update_plan(db, "reviewed", candidate_id, {
                "invalidated_at": _db_timestamp(db), "invalidation_reason": "supplement",
            }, where_sql="invalidated_at=''")
            return dict(db.execute(f"select * from ({CANDIDATE_SUPPLEMENT_SQL}) where id=?", (inserted.lastrowid,)).fetchone())

    def invalidate_review_candidate(self, candidate_id: int, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("invalidation reason is empty")
        with self._immediate_write_transaction() as db:
            self._candidate_context(db, candidate_id)
            shared_action_lifecycle.update_plan(db, "reviewed", candidate_id, {
                "invalidated_at": _db_timestamp(db), "invalidation_reason": reason,
            }, where_sql="invalidated_at=''")
            return dict(db.execute(f"select * from ({REVIEW_CANDIDATE_SQL}) where id=?", (candidate_id,)).fetchone())

    def get_candidate_execution(self, candidate_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute(f"select * from ({CANDIDATE_EXECUTION_SQL}) where candidate_id=?", (candidate_id,)).fetchone())

    def wake_selected_candidate_execution(self, candidate_id: int, review_id: int) -> dict[str, Any]:
        """Queue the selected plan in the same task generation, without a new Agent turn."""
        with self._immediate_write_transaction() as db:
            candidate, _ = self._review_context(db, candidate_id, review_id)
            if json.loads(candidate["candidate_json"]).get("outcome") != "needs_human":
                raise ValueError("candidate is not a human decision")
            selection = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where candidate_id=? and review_id=?", (candidate_id,review_id)).fetchone()
            if selection is None:
                raise ValueError("candidate choice is missing")
            latest_id = db.execute(f"select max(id) from ({REVIEW_CANDIDATE_SQL}) where task_id=? and execution_generation=?", (candidate["task_id"],candidate["execution_generation"])).fetchone()[0]
            if latest_id != candidate_id:
                raise ValueError("stale candidate")
            task = db.execute(f"select * from ({REPLY_TASK_SQL}) where id=?", (candidate["task_id"],)).fetchone()
            if task["status"] == "needs_human":
                db.execute(f"""update tasks set {task8_set_parts('reply_tasks', {}, update_sql={'status': "'pending'", 'available_at': "''", 'locked_at': 'null', 'updated_at': 'current_timestamp'}).sql} where record_type='reply_tasks' and id=? and {task20_column('reply_tasks', 'execution_generation')}=? and {task20_column('reply_tasks', 'status')}='needs_human'""",
                    (candidate["task_id"],candidate["execution_generation"]))
            elif task["status"] not in ("pending", "processing", "done", "skipped"):
                raise ValueError("selected candidate task is unavailable")
            return dict(db.execute(f"select * from ({REPLY_TASK_SQL}) where id=?", (candidate["task_id"],)).fetchone())

    def claim_candidate_execution(self, candidate_id: int, review_id: int, owner: str, lease_seconds: int) -> dict[str, Any] | None:
        if not owner.strip() or lease_seconds <= 0:
            raise ValueError("invalid execution lease")
        with self._immediate_write_transaction() as db:
            if self._candidate_context(db, candidate_id)["invalidated_at"]:
                return None
            candidate, _ = self._review_context(db, candidate_id, review_id)
            if db.execute(f"select max(id) from ({REVIEW_CANDIDATE_SQL}) where task_id=? and execution_generation=?", (candidate["task_id"],candidate["execution_generation"])).fetchone()[0] != candidate_id:
                return None
            data = json.loads(candidate["candidate_json"])
            selection = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where candidate_id=?", (candidate_id,)).fetchone()
            if data.get("outcome") == "needs_human" and selection is None:
                return None
            branch = selected_branch(db, selection["id"]) if selection else None
            is_stop = data.get("outcome") == "no_action" or (
                isinstance(branch, dict)
                and branch.get("terminal_outcome") == "skipped"
            )
            if not is_stop:
                self._check_historical_runtime_refusal(db, candidate["business_object_key"])
            now = _utc()
            expiry = _clock(now + timedelta(seconds=lease_seconds))
            now_text = _clock(now)
            old = db.execute(f"select * from ({CANDIDATE_EXECUTION_SQL}) where candidate_id=?", (candidate_id,)).fetchone()
            if old is not None:
                if old["review_id"] != review_id or old["selection_id"] != (selection["id"] if selection else None):
                    raise ValueError("execution plan mismatch")
                if old["status"] in ("done","skipped","failed") or (old["status"] == "running" and old["lease_expires_at"] > now_text and old["lease_owner"] != owner):
                    return None
                update_plan_execution(db, old["id"], {
                    "status": "running", "lease_owner": owner, "lease_expires_at": expiry,
                    "updated_at": _db_timestamp(db),
                })
            else:
                create_plan_execution(db, candidate_id=candidate_id, review_id=review_id,
                    selection_id=selection["id"] if selection else None,
                    status="running", lease_owner=owner, lease_expires_at=expiry)
            return dict(db.execute(f"select * from ({CANDIDATE_EXECUTION_SQL}) where candidate_id=?", (candidate_id,)).fetchone())

    def renew_candidate_execution_claim(self, execution_id: int, owner: str, lease_seconds: int) -> dict[str, Any]:
        if lease_seconds <= 0:
            raise ValueError("invalid lease")
        with self._immediate_write_transaction() as db:
            self._leased_execution(db, execution_id, owner)
            update_plan_execution(db, execution_id, {
                "lease_expires_at": _clock(_utc()+timedelta(seconds=lease_seconds)),
                "updated_at": _db_timestamp(db),
            })
            return dict(db.execute(f"select * from ({CANDIDATE_EXECUTION_SQL}) where id=?", (execution_id,)).fetchone())

    def _leased_execution(self, db: sqlite3.Connection, execution_id: int, owner: str) -> sqlite3.Row:
        """Validate a dispatch lease, including after its candidate is superseded.

        The provider may return a receipt after invalidation or a new generation.
        That receipt must still be recorded against the original dispatched action.
        """
        execution = db.execute(f"select * from ({CANDIDATE_EXECUTION_SQL}) where id=?", (execution_id,)).fetchone()
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
            candidate = db.execute(f"select * from ({REVIEW_CANDIDATE_SQL}) where id=?", (execution["candidate_id"],)).fetchone()
            data = json.loads(candidate["candidate_json"])
            selection = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?", (execution["selection_id"],)).fetchone() if execution["selection_id"] else None
            branch = selected_branch(db, selection["id"]) if selection else None
            if status == "skipped" and not (
                (branch is not None and branch.get("terminal_outcome") == "skipped")
                or (branch is None and data.get("outcome") == "no_action")
            ):
                raise ValueError("skipped requires selected stop branch")
            if status == "done":
                actions = shared_action_lifecycle.reviewed_action_steps(db, candidate, selection=selection)
                attempts = db.execute(f"select action_index,status from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=? order by action_index", (execution_id,)).fetchall()
                if not actions or len(attempts) != len(actions) or any(row["action_index"] != index or row["status"] != "verified" for index,row in enumerate(attempts)):
                    raise ValueError("execution actions are not verified")
            update_plan_execution(db, execution_id, {
                "status": status, "result_json": body, "lease_owner": "", "lease_expires_at": "",
                "updated_at": _db_timestamp(db),
            })
            if invalidate_reason:
                shared_action_lifecycle.update_plan(db, "reviewed", execution["candidate_id"], {
                    "invalidated_at": _db_timestamp(db), "invalidation_reason": invalidate_reason,
                }, where_sql="invalidated_at=''")
            return dict(db.execute(f"select * from ({CANDIDATE_EXECUTION_SQL}) where id=?", (execution_id,)).fetchone())

    def begin_candidate_action(
        self, execution_id: int, owner: str, action_index: int,
        external_action_key: str, *, preparation_owner: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if action_index < 0 or not external_action_key.strip():
            raise ValueError("invalid action identity")
        with self._immediate_write_transaction() as db:
            execution = self._owned_execution(db, execution_id, owner)
            candidate = self._candidate_context(db, execution["candidate_id"])
            selection = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?", (execution["selection_id"],)).fetchone() if execution["selection_id"] else None
            actions = shared_action_lifecycle.reviewed_action_steps(db, candidate, selection=selection)
            if action_index >= len(actions):
                raise ValueError("action index outside approved plan")
            prior_actions = db.execute(f"select action_index,status from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=? and action_index<?", (execution_id,action_index)).fetchall()
            if len(prior_actions) != action_index or any(row["status"] != "verified" for row in prior_actions):
                raise ValueError("preceding action is not verified")
            created_at = db.execute("select strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
            return shared_action_lifecycle.begin_reviewed_action(
                db, execution_id=execution_id, action_index=action_index,
                external_action_key=external_action_key, action=actions[action_index],
                candidate=candidate, preparation_owner=preparation_owner,
                created_at=created_at,
                updated_at=_db_timestamp(db),
            )

    def get_prior_candidate_action_attempt(
        self, external_action_key: str, execution_id: int,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) "
                "where external_action_key=? and execution_id<>? order by id desc limit 1",
                (external_action_key, execution_id),
            ).fetchone()
            return candidate_action_attempt_row(row)

    def record_prior_candidate_action_reconciliation(
        self, execution_id: int, owner: str, action_index: int,
        external_action_key: str, status: str, result: Any,
    ) -> dict[str, Any]:
        if status not in ("verified", "confirmed_no_effect"):
            raise ValueError("invalid prior action reconciliation")
        result_json = _json(result)
        with self._immediate_write_transaction() as db:
            execution = self._leased_execution(db, execution_id, owner)
            candidate = self._candidate_context(db, execution["candidate_id"])
            prior = candidate_action_attempt_row(db.execute(
                f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) "
                "where external_action_key=? and execution_id<>? order by id desc limit 1",
                (external_action_key, execution_id),
            ).fetchone())
            if prior is None or prior["status"] not in ("failed", "uncertain", "dispatched"):
                raise ValueError("prior action is not reconcilable")
            prior_result = json.loads(prior["result_json"])
            if prior_result.get("reason") != "reviewed_message_preparation_mismatch":
                raise ValueError("prior action failure is outside this reconciliation")
            prior_execution = db.execute(
                f"select * from ({CANDIDATE_EXECUTION_SQL}) where id=?",
                (prior["execution_id"],),
            ).fetchone()
            prior_candidate = self._candidate_context(db, prior_execution["candidate_id"])
            if prior_candidate["business_object_key"] != candidate["business_object_key"]:
                raise ValueError("prior action belongs to another business object")
            prior_selection = db.execute(
                f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?",
                (prior_execution["selection_id"],),
            ).fetchone() if prior_execution["selection_id"] else None
            prior_actions = shared_action_lifecycle.reviewed_action_steps(db, prior_candidate, selection=prior_selection)
            if prior["action_index"] >= len(prior_actions):
                raise ValueError("prior action is outside its approved plan")
            prior_action = prior_actions[prior["action_index"]]
            selection = db.execute(
                f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?",
                (execution["selection_id"],),
            ).fetchone() if execution["selection_id"] else None
            actions = shared_action_lifecycle.reviewed_action_steps(db, candidate, selection=selection)
            if action_index >= len(actions):
                raise ValueError("action index outside approved plan")
            action = actions[action_index]
            if (prior_action.get("action_identity") != action.get("action_identity")
                    or prior_action.get("operation") != action.get("operation")
                    or prior_action.get("target") != action.get("target")):
                raise ValueError("prior action identity differs from the current approved action")
            if status == "verified":
                target_json = _json(action.get("target", {}))
                provider_json = result_json
                result_digest = hashlib.sha256(provider_json.encode()).hexdigest()
                saved = db.execute(
                    f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
                    (external_action_key,),
                ).fetchone()
                if saved is not None and (saved["business_object_key"] != candidate["business_object_key"]
                        or saved["action_identity"] != action["action_identity"]
                        or saved["operation"] != action["operation"]
                        or saved["target_identifiers_json"] != target_json):
                    raise ValueError("conflicting external action result")
                provider_receipt_id = (saved["provider_receipt_id"]
                                       if saved is not None and saved["provider_result_json"] == provider_json
                                       else shared_action_lifecycle.record_reviewed_provider_receipt(
                                           db, external_action_key=external_action_key,
                                           operation=action["operation"], target_json=target_json,
                                           provider_json=provider_json, result_digest=result_digest,
                                           agent_run_id=candidate["consumer_run_id"],
                                           observed_at=_db_timestamp(db),
                                           supersedes_provider_receipt_id=(saved["provider_receipt_id"] if saved else None),
                                       ))
                result_json = None
            else:
                provider_receipt_id = None
            shared_action_lifecycle.record_reviewed_action_outcome(
                db, attempt=prior, status=status, result_json=result_json,
                provider_receipt_id=provider_receipt_id,
                agent_run_id=prior_candidate["consumer_run_id"],
                updated_at=_db_timestamp(db),
            )
            return candidate_action_attempt_row(db.execute(
                f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where id=?",
                (prior["id"],),
            ).fetchone())

    def record_candidate_action_outcome(self, execution_id: int, owner: str, action_index: int, status: str, result: Any) -> dict[str, Any]:
        if status not in ("verified","confirmed_no_effect","uncertain","failed"):
            raise ValueError("invalid action outcome")
        body = _json(result)
        with self._immediate_write_transaction() as db:
            execution = self._leased_execution(db, execution_id, owner)
            candidate = self._candidate_context(db, execution["candidate_id"])
            attempt = candidate_action_attempt_row(db.execute(f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=? and action_index=?", (execution_id,action_index)).fetchone())
            if attempt is None:
                raise ValueError("action was not dispatched")
            if status == "verified":
                receipt = db.execute(f"select provider_result_json,provider_receipt_id from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?", (attempt["external_action_key"],)).fetchone()
                if receipt is None:
                    raise ValueError("verified action lacks successful ledger receipt")
                if body == receipt["provider_result_json"]:
                    body = None
                provider_receipt_id = receipt["provider_receipt_id"]
            else:
                provider_receipt_id = None
            shared_action_lifecycle.record_reviewed_action_outcome(
                db, attempt=attempt, status=status, result_json=body,
                provider_receipt_id=provider_receipt_id,
                agent_run_id=candidate["consumer_run_id"],
                updated_at=_db_timestamp(db),
            )
            return candidate_action_attempt_row(db.execute(f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where id=?", (attempt["id"],)).fetchone())

    def list_candidate_action_attempts(self, execution_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [candidate_action_attempt_row(row) for row in db.execute(f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=? order by action_index", (execution_id,))]

    def record_candidate_external_action(self, execution_id: int, owner: str, action_index: int, external_action_key: str, operation: str, target_identifiers: dict[str, Any], provider_result: dict[str, Any]) -> dict[str, Any]:
        target_json = _json(target_identifiers)
        provider_json = _json(provider_result)
        with self._immediate_write_transaction() as db:
            execution = self._leased_execution(db, execution_id, owner)
            candidate = db.execute(f"""select c.*, t.business_object_key from ({REVIEW_CANDIDATE_SQL}) c
                join ({REPLY_TASK_SQL}) t on t.id=c.task_id where c.id=?""", (execution["candidate_id"],)).fetchone()
            attempt = candidate_action_attempt_row(db.execute(f"select * from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=? and action_index=? and external_action_key=?", (execution_id,action_index,external_action_key)).fetchone())
            if attempt is None:
                raise ValueError("action dispatch identity mismatch")
            selection = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?", (execution["selection_id"],)).fetchone() if execution["selection_id"] else None
            action = shared_action_lifecycle.reviewed_action_steps(db, candidate, selection=selection)[action_index]
            if action.get("operation") != operation or action.get("target") != target_identifiers:
                raise ValueError("approved action mismatch")
            saved = db.execute(f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?", (external_action_key,)).fetchone()
            if saved is not None and (saved["business_object_key"] != candidate["business_object_key"] or saved["action_identity"] != action["action_identity"]
                or saved["operation"] != operation or saved["target_identifiers_json"] != target_json):
                raise ValueError("conflicting external action result")
            provider_receipt_id = saved["provider_receipt_id"] if saved is not None else None
            if saved is None or saved["provider_result_json"] != provider_json:
                supersedes_id = provider_receipt_id
            else:
                supersedes_id = None
            if saved is not None and saved["provider_result_json"] != provider_json:
                previous = json.loads(saved["provider_result_json"])
                if (previous.get("verification", {}).get("state") != "ambiguous"
                    or provider_result.get("verification", {}).get("state") != "sent"
                    or previous.get("provider_result") != provider_result.get("provider_result")):
                    raise ValueError("conflicting external action result")
                updated = {**previous, **provider_result}
                provider_json = _json(updated)
                provider_receipt_id = None
            if provider_receipt_id is None or provider_json != saved["provider_result_json"]:
                provider_receipt_id = shared_action_lifecycle.record_reviewed_provider_receipt(
                    db, external_action_key=external_action_key, operation=operation,
                    target_json=target_json, provider_json=provider_json,
                    result_digest=hashlib.sha256(provider_json.encode()).hexdigest(),
                    agent_run_id=candidate["consumer_run_id"], observed_at=_db_timestamp(db),
                    supersedes_provider_receipt_id=supersedes_id,
                )
            shared_action_lifecycle.record_reviewed_action_outcome(
                db, attempt=attempt, status="verified", result_json=None,
                provider_receipt_id=provider_receipt_id,
                agent_run_id=candidate["consumer_run_id"], updated_at=_db_timestamp(db),
            )
            saved = db.execute(f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?", (external_action_key,)).fetchone()
            return dict(saved)

    def get_candidate_external_action(self, external_action_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute(f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?", (external_action_key,)).fetchone())

    def get_verified_action_source(self, external_action_key: str) -> dict[str, Any] | None:
        """Resolve a successful ledger key to the branch actually dispatched."""
        with self._connect() as db:
            return _derived_selection(db, db.execute(f"""select c.id as candidate_id, c.candidate_json,
                    a.action_index, s.option_key, e.selection_id
                from ({EXTERNAL_ACTION_RESULT_SQL}) r
                join ({REVIEW_CANDIDATE_SQL}) c on c.consumer_run_id=r.first_agent_run_id
                join ({CANDIDATE_EXECUTION_SQL}) e on e.candidate_id=c.id
                join ({CANDIDATE_ACTION_ATTEMPT_SQL}) a on a.execution_id=e.id
                    and a.external_action_key=r.external_action_key and a.status='verified'
                left join ({CANDIDATE_SELECTION_SQL}) s on s.id=e.selection_id
                where r.external_action_key=? limit 1""", (external_action_key,)).fetchone())

    def get_candidate_review_for_audit_run(self, audit_run_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            return _row(db.execute(f"select * from ({CANDIDATE_REVIEW_SQL}) where audit_run_id=?", (audit_run_id,)).fetchone())

    def list_verified_candidate_actions(self, task_id: int) -> list[dict[str, Any]]:
        """Return owned receipt results for the business object's verified stages."""
        with self._connect() as db:
            rows = db.execute(f"""select a.*, c.task_id, c.stage_index, c.id as candidate_id,
                c.execution_generation, r.operation, r.action_identity,
                r.target_identifiers_json, r.provider_result_json,
                r.first_agent_run_id as receipt_first_agent_run_id
                from ({CANDIDATE_ACTION_ATTEMPT_SQL}) a
                join ({CANDIDATE_EXECUTION_SQL}) e on e.id=a.execution_id
                join ({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id
                left join ({EXTERNAL_ACTION_RESULT_SQL}) r on r.external_action_key=a.external_action_key
                where c.task_id=? and a.status='verified'
                  and (r.external_action_key is not null or a.result_json is null)
                order by c.id, a.action_index""", (task_id,)).fetchall()
            return [candidate_action_attempt_row(row) for row in rows]

    def list_human_decision_evidence(self, task_id: int) -> list[dict[str, Any]]:
        """Read immutable answers/new facts for this business object across generations.

        The returned branch is the exact historical selection, not an approval
        to execute it after the candidate has changed or been invalidated.
        """
        with self._connect() as db:
            task = db.execute(f"select business_object_key from ({REPLY_TASK_SQL}) where id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task does not exist")
            rows = db.execute(f"""select * from (
                select 'selection' as kind, s.id as source_record_id,
                s.created_at, s.option_key, '' as instruction,
                c.id as candidate_id, c.execution_generation, c.stage_index,
                c.candidate_digest, c.candidate_json, c.invalidated_at,
                c.invalidation_reason, r.id as review_id, r.audit_run_id
                from ({CANDIDATE_SELECTION_SQL}) s
                join ({REVIEW_CANDIDATE_SQL}) c on c.id=s.candidate_id
                join ({CANDIDATE_REVIEW_SQL}) r on r.id=s.review_id and r.candidate_id=c.id
                  and r.decision='approve' and r.candidate_digest=c.candidate_digest
                join ({REPLY_TASK_SQL}) t on t.id=c.task_id
                where t.business_object_key=?
                union all
                select 'supplement' as kind, supplement.id as source_record_id,
                supplement.created_at, '' as option_key,
                supplement.instruction,
                c.id as candidate_id, c.execution_generation, c.stage_index,
                c.candidate_digest, c.candidate_json, c.invalidated_at,
                c.invalidation_reason, r.id as review_id, r.audit_run_id
                from ({CANDIDATE_SUPPLEMENT_SQL}) supplement
                join ({REVIEW_CANDIDATE_SQL}) c on c.id=supplement.candidate_id
                join ({CANDIDATE_REVIEW_SQL}) r on r.id=(select max(approved.id)
                    from ({CANDIDATE_REVIEW_SQL}) approved where approved.candidate_id=c.id
                      and approved.decision='approve'
                      and approved.candidate_digest=c.candidate_digest)
                join ({REPLY_TASK_SQL}) t on t.id=c.task_id
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
                "selected_branch": candidate_option(row["candidate_json"], row["option_key"]) if row["kind"] == "selection" else None,
                "instruction": row["instruction"] or None,
                "invalidated_at": row["invalidated_at"],
                "invalidation_reason": row["invalidation_reason"],
            })
        return evidence
