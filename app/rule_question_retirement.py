"""Explicitly retire one obsolete task-class question; never decide its business."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_contracts import DecisionBasis
from app.agent_result import AgentError
from app.config import worker_db_path
from app.database_backup import backup_is_complete
from app.store import AutoReplyStore


class _RuleOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    consequence: str = Field(min_length=1)
    applies_to: Literal["task_class"]


class _RuleQuestion(BaseModel):
    # This model is a migration input, never a runtime result parser.
    model_config = ConfigDict(strict=True)
    outcome: Literal["needs_human"]
    needs_human_reason: str = Field(min_length=1)
    decision_options: list[_RuleOption] = Field(min_length=2)
    decision_basis: DecisionBasis
    error: AgentError
    proposal: None = None


def retire_rule_question(
    store: AutoReplyStore, attempt_id: int, *, authority: str, apply: bool = False,
) -> dict:
    """Preview by default; resolve one proven obsolete question atomically."""
    if not authority.strip():
        raise ValueError("retirement requires the confirmed contract authority")
    connection = store._immediate_write_transaction() if apply else store._connect()
    with connection as db:
        return _retire_in_connection(db, attempt_id, authority=authority, apply=apply)


def _retire_in_connection(db: sqlite3.Connection, attempt_id: int, *, authority: str, apply: bool) -> dict:
    row = db.execute("""
        select a.*, r.reply_task_id, r.execution_generation as run_generation,
               r.role, r.status as run_status, r.final_result_json,
               t.status as task_status, t.execution_generation as task_generation
        from reply_attempts a join agent_runs r on r.id=a.agent_run_id
        join reply_tasks t on t.id=r.reply_task_id
        join business_object_tasks current_object
          on current_object.business_object_key=t.business_object_key
         and current_object.reply_task_id=t.id
        where a.id=? and a.channel=t.channel
          and a.conversation_id=t.conversation_id and a.trigger_message_id=t.trigger_message_id
          and not exists(select 1 from reply_attempts newer
            where newer.channel=a.channel and newer.conversation_id=a.conversation_id
              and newer.trigger_message_id=a.trigger_message_id and newer.id>a.id)
          and not exists(select 1 from review_candidates c where c.task_id=t.id)
    """, (attempt_id,)).fetchone()
    if row is None or not (
        row["send_status"] == "needs_human" and row["reviewed_at"] is None
        and row["role"] == "consumer" and row["run_status"] == "completed"
        and row["task_status"] == "done"
        and row["run_generation"] == row["task_generation"]
    ):
        raise ValueError("attempt is not an obsolete completed Consumer rule question")
    result = json.loads(row["final_result_json"])
    question = _RuleQuestion.model_validate(result)
    if any(question.error.model_dump().values()):
        raise ValueError("technical failures cannot be retired as rule questions")
    digest = hashlib.sha256(row["final_result_json"].encode()).hexdigest()
    key = f"rule_question_retirement:{attempt_id}"
    saved = db.execute("select value from service_state where key=?", (key,)).fetchone()
    if row["resolved_at"]:
        if saved is None:
            raise ValueError("attempt already resolved without this retirement receipt")
        receipt = json.loads(saved[0])
        if receipt["authority"] != authority.strip() or receipt["original_result_sha256"] != digest:
            raise ValueError("retirement receipt does not match the authority and original result")
        return receipt
    receipt = {
        "transition": "retired_rule_question", "attempt_id": attempt_id,
        "task_id": row["reply_task_id"], "agent_run_id": row["agent_run_id"],
        "execution_generation": row["run_generation"], "authority": authority.strip(),
        "original_result_sha256": digest, "applied": apply,
    }
    if apply:
        receipt["resolved_at"] = db.execute("select current_timestamp").fetchone()[0]
        resolution = (
            "旧长期规则问题已按当前实例决策契约退役；未作出业务决策，也未执行新动作。"
            f"依据：{authority.strip()}；原结果 SHA256：{digest}"
        )
        db.execute("update reply_attempts set resolved_at=?, resolution=? where id=?",
                   (receipt["resolved_at"], resolution, attempt_id))
        db.execute("insert into service_state(key,value) values (?,?)",
                   (key, json.dumps(receipt, ensure_ascii=False, sort_keys=True)))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=worker_db_path())
    parser.add_argument("--attempt-id", type=int, required=True)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verified-backup", type=Path)
    args = parser.parse_args()
    if args.apply and (args.verified_backup is None or not backup_is_complete(args.verified_backup)):
        parser.error("apply requires an existing verified database backup")
    # Existing database only: this command does not create or migrate a store.
    if not args.authority.strip():
        parser.error("retirement requires the confirmed contract authority")
    mode = "rw" if args.apply else "ro"
    with closing(sqlite3.connect(args.database.resolve().as_uri() + f"?mode={mode}", uri=True,
                                timeout=30)) as db:
        db.row_factory = sqlite3.Row
        with db:
            db.execute("begin immediate" if args.apply else "pragma query_only=1")
            receipt = _retire_in_connection(db, args.attempt_id,
                       authority=args.authority, apply=args.apply)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
