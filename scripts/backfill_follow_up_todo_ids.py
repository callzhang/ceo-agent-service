from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


EXACT_MATCH_SCORE = 1.0
EXISTING_TODO_SCORE_THRESHOLD = 0.75
OWNER_MATCH_SCORE_THRESHOLD = 0.55

MANUAL_EXISTING_TODO_BINDINGS = {
    12: 16,
    54: 79,
    62: 89,
    65: 89,
    101: 377,
    121: 177,
    122: 178,
    125: 188,
    142: 242,
    183: 447,
    186: 365,
    276: 570,
    291: 601,
    297: 608,
    299: 611,
    300: 613,
}


@dataclass(frozen=True)
class Candidate:
    todo_id: int
    score: float
    exact_question_match: bool
    owner_match: bool
    title: str
    question: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill follow_up_drafts.todo_id by binding each draft to a TODO."
    )
    parser.add_argument("--db", default="data/auto-reply.sqlite3")
    parser.add_argument("--audit", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    audit_path = Path(args.audit) if args.audit else None
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    decisions = _decide_bindings(conn)
    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("w", encoding="utf-8") as handle:
            for decision in decisions:
                handle.write(json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n")

    summary = _summary(decisions)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        return 0

    with conn:
        for decision in decisions:
            if decision["action"] == "bind_existing":
                conn.execute(
                    "update follow_up_drafts set todo_id=? where id=? and todo_id=0",
                    (decision["todo_id"], decision["draft_id"]),
                )
            elif decision["action"] == "create_todo":
                todo_id = _create_todo_for_draft(conn, decision)
                conn.execute(
                    "update follow_up_drafts set todo_id=? where id=? and todo_id=0",
                    (todo_id, decision["draft_id"]),
                )
            else:
                raise ValueError(f"unsupported decision: {decision['action']}")
    remaining = conn.execute(
        "select count(*) from follow_up_drafts where todo_id=0"
    ).fetchone()[0]
    broken = conn.execute(
        """
        select count(*)
        from follow_up_drafts f
        left join work_todos t on t.id=f.todo_id and t.project_id=f.project_id
        where f.todo_id!=0 and t.id is null
        """
    ).fetchone()[0]
    print(json.dumps({"remaining_unlinked": remaining, "broken_links": broken}))
    return 0


def _decide_bindings(conn: sqlite3.Connection) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    drafts = conn.execute(
        """
        select f.*, p.title as project_title, p.priority as project_priority
        from follow_up_drafts f
        join work_projects p on p.id=f.project_id
        where f.todo_id=0
        order by f.id
        """
    ).fetchall()
    for draft in drafts:
        candidates = _rank_candidates(conn, draft)
        best = candidates[0] if candidates else None
        manual_todo_id = MANUAL_EXISTING_TODO_BINDINGS.get(draft["id"])
        if manual_todo_id is not None:
            manual = _todo_candidate(conn, draft, manual_todo_id)
            decisions.append(
                {
                    "action": "bind_existing",
                    "draft_id": draft["id"],
                    "project_id": draft["project_id"],
                    "project_title": draft["project_title"],
                    "todo_id": manual.todo_id,
                    "owner_name": draft["owner_name"],
                    "question_text": draft["question_text"],
                    "status": draft["status"],
                    "reason": "manual: reviewed and bound to existing TODO",
                    "score": round(manual.score, 4),
                    "candidate_title": manual.title,
                    "candidate_question": manual.question,
                }
            )
            continue
        if best and _should_bind_existing(best):
            decisions.append(
                {
                    "action": "bind_existing",
                    "draft_id": draft["id"],
                    "project_id": draft["project_id"],
                    "project_title": draft["project_title"],
                    "todo_id": best.todo_id,
                    "owner_name": draft["owner_name"],
                    "question_text": draft["question_text"],
                    "status": draft["status"],
                    "reason": _existing_reason(best),
                    "score": round(best.score, 4),
                    "candidate_title": best.title,
                    "candidate_question": best.question,
                }
            )
            continue
        decisions.append(_create_todo_decision(draft, best))
    return decisions


def _rank_candidates(conn: sqlite3.Connection, draft: sqlite3.Row) -> list[Candidate]:
    todos = conn.execute(
        """
        select id, title, owner_name, owner_user_id, follow_up_question
        from work_todos
        where project_id=?
        order by id
        """,
        (draft["project_id"],),
    ).fetchall()
    candidates = []
    question = (draft["question_text"] or "").strip()
    for todo in todos:
        todo_question = (todo["follow_up_question"] or "").strip()
        exact = question == todo_question and bool(question)
        owner_match = _owner_matches(draft, todo)
        text = f"{todo['title'] or ''} {todo_question}"
        score = SequenceMatcher(None, question, text).ratio()
        if owner_match:
            score += 0.25
        if exact:
            score += EXACT_MATCH_SCORE
        candidates.append(
            Candidate(
                todo_id=todo["id"],
                score=score,
                exact_question_match=exact,
                owner_match=owner_match,
                title=todo["title"] or "",
                question=todo_question,
            )
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _todo_candidate(
    conn: sqlite3.Connection,
    draft: sqlite3.Row,
    todo_id: int,
) -> Candidate:
    todo = conn.execute(
        """
        select id, title, owner_name, owner_user_id, follow_up_question
        from work_todos
        where id=? and project_id=?
        """,
        (todo_id, draft["project_id"]),
    ).fetchone()
    if todo is None:
        raise ValueError(
            f"manual binding draft {draft['id']} points to missing project TODO {todo_id}"
        )
    question = (draft["question_text"] or "").strip()
    todo_question = (todo["follow_up_question"] or "").strip()
    exact = question == todo_question and bool(question)
    owner_match = _owner_matches(draft, todo)
    text = f"{todo['title'] or ''} {todo_question}"
    score = SequenceMatcher(None, question, text).ratio()
    if owner_match:
        score += 0.25
    if exact:
        score += EXACT_MATCH_SCORE
    return Candidate(
        todo_id=todo["id"],
        score=score,
        exact_question_match=exact,
        owner_match=owner_match,
        title=todo["title"] or "",
        question=todo_question,
    )


def _owner_matches(draft: sqlite3.Row, todo: sqlite3.Row) -> bool:
    draft_user_id = (draft["owner_user_id"] or "").strip()
    todo_user_id = (todo["owner_user_id"] or "").strip()
    if draft_user_id and todo_user_id and draft_user_id == todo_user_id:
        return True
    draft_owner = _normalize_owner_name(draft["owner_name"] or "")
    todo_owner = _normalize_owner_name(todo["owner_name"] or "")
    if not draft_owner or not todo_owner:
        return False
    return draft_owner == todo_owner or draft_owner in todo_owner or todo_owner in draft_owner


def _normalize_owner_name(value: str) -> str:
    return re.sub(r"[\s/()（）+]+", "", value)


def _should_bind_existing(candidate: Candidate) -> bool:
    if candidate.exact_question_match:
        return True
    if candidate.score >= EXISTING_TODO_SCORE_THRESHOLD:
        return True
    return candidate.owner_match and candidate.score >= OWNER_MATCH_SCORE_THRESHOLD


def _existing_reason(candidate: Candidate) -> str:
    if candidate.exact_question_match:
        return "manual: exact follow_up_question match"
    if candidate.owner_match:
        return "manual: same owner and same follow-up subject"
    return "manual: same project and same follow-up subject"


def _create_todo_decision(
    draft: sqlite3.Row,
    best: Candidate | None,
) -> dict[str, object]:
    question = (draft["question_text"] or "").strip()
    return {
        "action": "create_todo",
        "draft_id": draft["id"],
        "project_id": draft["project_id"],
        "project_title": draft["project_title"],
        "owner_user_id": draft["owner_user_id"] or "",
        "owner_name": draft["owner_name"] or "",
        "status": draft["status"],
        "question_text": question,
        "scheduled_at": draft["scheduled_at"] or "",
        "priority": draft["project_priority"] or "none",
        "todo_title": _todo_title_from_question(question),
        "reason": _create_reason(best),
        "best_candidate_todo_id": best.todo_id if best else None,
        "best_candidate_score": round(best.score, 4) if best else None,
        "best_candidate_title": best.title if best else "",
    }


def _create_reason(best: Candidate | None) -> str:
    if best is None:
        return "manual: no existing TODO in this project; created TODO from follow-up"
    return "manual: no existing TODO matched this follow-up; created TODO from follow-up"


def _todo_title_from_question(question: str) -> str:
    cleaned = re.sub(r"^@?[^，,：:]{1,32}[，,：:]\s*", "", question).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "跟进事项状态"
    return cleaned[:80]


def _create_todo_for_draft(conn: sqlite3.Connection, decision: dict[str, object]) -> int:
    cursor = conn.execute(
        """
        insert into work_todos (
            project_id,
            title,
            owner_user_id,
            owner_name,
            status,
            priority,
            next_follow_up_at,
            follow_up_question,
            blocker,
            completion_evidence_json,
            created_from_update_id
        ) values (?, ?, ?, ?, 'open', ?, ?, ?, '', '{}', 0)
        """,
        (
            decision["project_id"],
            decision["todo_title"],
            decision["owner_user_id"],
            decision["owner_name"],
            decision["priority"],
            decision["scheduled_at"],
            decision["question_text"],
        ),
    )
    return int(cursor.lastrowid)


def _summary(decisions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "total": len(decisions),
        "bind_existing": sum(1 for item in decisions if item["action"] == "bind_existing"),
        "create_todo": sum(1 for item in decisions if item["action"] == "create_todo"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
