"""Opt-in native Codex checks for Task 7 work-tracking semantics."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from app.codex_runner import CodexRunner
from app.process_runner import run_process_with_idle_timeout
from app.task_models import TaskAgentDecision
from tests.support.native_codex_read_fixture import (
    assert_isolated_read_only_fixture_command,
    isolate_read_only_fixture_command,
)


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("CEO_LIVE_TASK7_SKILL_E2E") != "1",
        reason="set CEO_LIVE_TASK7_SKILL_E2E=1 to run native Task 7 Skill checks",
    ),
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPOSITORY_ROOT / "skills" / "ceo-work-tracking" / "SKILL.md"


@dataclass(frozen=True)
class SemanticCase:
    name: str
    current_data: dict[str, object]
    verify: Callable[[TaskAgentDecision, dict[str, object]], None]


def _todo_changes(decision: TaskAgentDecision, action: str):
    return [change for change in decision.todo_changes if change.action == action]


def _follow_up_changes(decision: TaskAgentDecision, *actions: str):
    return [change for change in decision.follow_up_changes if change.action in actions]


def _identity_tokens(*, user_id: object = "", name: object = "") -> set[str]:
    tokens: set[str] = set()
    if str(user_id or "").strip():
        tokens.add(f"user_id:{str(user_id).strip()}")
    if str(name or "").strip():
        tokens.add(f"name:{str(name).strip().casefold()}")
    return tokens


def _assigned_owner_identities(decision: TaskAgentDecision) -> set[str]:
    assigned: set[str] = set()
    for change in decision.todo_changes:
        assigned.update(
            _identity_tokens(
                user_id=change.owner_user_id,
                name=change.owner_name,
            )
        )
    for draft in decision.follow_up_drafts:
        assigned.update(
            _identity_tokens(
                user_id=draft.owner_user_id,
                name=draft.owner_name,
            )
        )
        for owner in draft.owners:
            assigned.update(
                _identity_tokens(
                    user_id=owner.get("user_id"),
                    name=owner.get("name"),
                )
            )
    for change in decision.follow_up_changes:
        assigned.update(
            _identity_tokens(
                user_id=change.owner_user_id,
                name=change.owner_name,
            )
        )
    if decision.project is not None:
        assigned.update(
            _identity_tokens(
                user_id=decision.project.owner_user_id,
                name=decision.project.owner_name,
            )
        )
    return assigned


def _supported_owner_identities(current_data: dict[str, object]) -> set[str]:
    evidence_items: list[object] = []
    for key in ("owner_evidence", "verified_owner_resolution"):
        value = current_data.get(key, [])
        evidence_items.extend(value if isinstance(value, list) else [value])
    supported: set[str] = set()
    for item in evidence_items:
        if isinstance(item, dict):
            supported.update(
                _identity_tokens(
                    user_id=item.get("user_id"),
                    name=item.get("name"),
                )
            )
    return supported


def _assert_assigned_owners_are_supported(
    decision: TaskAgentDecision,
    current_data: dict[str, object],
) -> None:
    assert _assigned_owner_identities(decision) <= _supported_owner_identities(
        current_data
    )


def _verify_discard(
    decision: TaskAgentDecision,
    _current_data: dict[str, object],
) -> None:
    assert decision.action == "discard"
    assert decision.todo_changes == []
    assert decision.follow_up_drafts == []


def _verify_owned_todo(
    decision: TaskAgentDecision,
    current_data: dict[str, object],
) -> None:
    creates = _todo_changes(decision, "create")
    assert creates
    assert all(change.owner_evidence for change in creates)
    _assert_assigned_owners_are_supported(decision, current_data)


def _verify_bound_follow_up(
    decision: TaskAgentDecision,
    current_data: dict[str, object],
) -> None:
    current_todos = current_data.get("current_todos", [])
    assert isinstance(current_todos, list)
    existing_ids = {
        int(item["id"])
        for item in current_todos
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }
    existing_refs = {
        str(item.get("todo_ref") or "").strip()
        for item in current_todos
        if isinstance(item, dict) and str(item.get("todo_ref") or "").strip()
    }
    created_refs = {
        change.todo_ref.strip()
        for change in _todo_changes(decision, "create")
        if change.todo_ref.strip()
    }
    for draft in decision.follow_up_drafts:
        if draft.todo_id is not None:
            assert draft.todo_id in existing_ids
        else:
            assert draft.todo_ref.strip() in existing_refs | created_refs
    if not decision.follow_up_drafts:
        asks_for_fact = decision.project is not None and any(
            value.strip()
            for value in (
                decision.project.blocker,
                decision.project.next_step,
                decision.project.current_state,
            )
        )
        assert (
            decision.action == "discard"
            or _todo_changes(decision, "create")
            or asks_for_fact
        )
    _assert_assigned_owners_are_supported(decision, current_data)


def _verify_speaker_not_owner(
    decision: TaskAgentDecision,
    current_data: dict[str, object],
) -> None:
    assert _supported_owner_identities(current_data) == set()
    _assert_assigned_owners_are_supported(decision, current_data)
    if decision.action != "discard":
        assert decision.follow_up_drafts == []
        waiting_for_owner = bool(decision.todo_changes) and all(
            change.status == "waiting_owner"
            and not change.owner_user_id.strip()
            and not change.owner_name.strip()
            for change in decision.todo_changes
        )
        clarifies_owner = decision.project is not None and any(
            value.strip()
            for value in (
                decision.project.blocker,
                decision.project.next_step,
                decision.project.current_state,
            )
        )
        assert waiting_for_owner or clarifies_owner


def _verify_open_todo_follow_up(
    decision: TaskAgentDecision,
    _current_data: dict[str, object],
) -> None:
    assert _todo_changes(decision, "create") == []
    assert all(draft.todo_id == 42 for draft in decision.follow_up_drafts)
    assert decision.follow_up_drafts or _follow_up_changes(
        decision, "keep_open", "reschedule"
    )


def _verify_completed_suppression(
    decision: TaskAgentDecision,
    _current_data: dict[str, object],
) -> None:
    assert _todo_changes(decision, "close")
    changes = _follow_up_changes(decision, "suppress", "close")
    assert changes and changes[0].follow_up_id == 8


def _verify_owner_correction(
    decision: TaskAgentDecision,
    _current_data: dict[str, object],
) -> None:
    updates = _todo_changes(decision, "update")
    assert updates and updates[0].todo_id == 42
    assert updates[0].owner_user_id == "uid-mina"
    changes = _follow_up_changes(decision, "suppress", "close", "reassign")
    assert changes and changes[0].follow_up_id == 8


def _verify_reply_updates_existing(
    decision: TaskAgentDecision,
    _current_data: dict[str, object],
) -> None:
    assert _todo_changes(decision, "create") == []
    assert decision.follow_up_drafts == []
    assert any(change.todo_id == 42 for change in decision.todo_changes)


def _verify_stale_suppression(
    decision: TaskAgentDecision,
    _current_data: dict[str, object],
) -> None:
    changes = _follow_up_changes(decision, "suppress", "close")
    assert changes and changes[0].follow_up_id == 8
    assert decision.follow_up_drafts == []


def _verify_sensitive_direct_target(
    decision: TaskAgentDecision,
    _current_data: dict[str, object],
) -> None:
    assert decision.follow_up_drafts
    assert all(draft.target_kind == "direct" for draft in decision.follow_up_drafts)
    assert all(
        draft.owner_user_id == "uid-alex"
        and draft.target_conversation_id == "direct-alex"
        for draft in decision.follow_up_drafts
    )


CASES = (
    SemanticCase(
        "routine_process_is_discarded",
        {
            "source": "weekly operations note",
            "summary": "The standard weekly dashboard was generated as usual.",
            "facts": [
                "No decision, commitment, exception, or deliverable was recorded."
            ],
        },
        _verify_discard,
    ),
    SemanticCase(
        "important_commitment_creates_todo_with_owner_evidence",
        {
            "source": "signed meeting decision",
            "summary": "Alex committed to deliver the customer acceptance plan by Friday.",
            "owner_evidence": [
                {
                    "name": "Alex",
                    "user_id": "uid-alex",
                    "source": "signed meeting decision",
                    "statement": "I own this and will deliver it by Friday.",
                }
            ],
        },
        _verify_owned_todo,
    ),
    SemanticCase(
        "follow_up_cannot_exist_without_todo",
        {
            "source": "approved launch decision",
            "summary": "Alex owns the launch checklist due Friday and needs a Thursday progress check.",
            "owner_evidence": [
                {
                    "name": "Alex",
                    "user_id": "uid-alex",
                    "source": "approved launch decision",
                }
            ],
            "current_todos": [],
        },
        _verify_bound_follow_up,
    ),
    SemanticCase(
        "participant_or_speaker_is_not_owner_evidence",
        {
            "source": "meeting transcript",
            "summary": "A delivery issue was discussed; the team will investigate.",
            "speaker": {"name": "Sam", "user_id": "uid-speaker"},
            "facts": [
                "Sam presented the slide but made no commitment and was not assigned."
            ],
            "owner_evidence": [],
            "verified_owner_resolution": [],
        },
        _verify_speaker_not_owner,
    ),
    SemanticCase(
        "due_follow_up_refreshes_live_todo_before_send",
        {
            "project": {"id": 7, "status": "active"},
            "todo": {"id": 42, "status": "open", "owner_user_id": "uid-alex"},
            "external_todo": {"task_id": "dt-42", "done": False, "pulled_at": "now"},
            "follow_up": {"id": 8, "todo_id": 42, "status": "draft", "due": True},
            "current_progress": "No completion evidence; ask only for blocker and ETA.",
        },
        _verify_open_todo_follow_up,
    ),
    SemanticCase(
        "completed_todo_suppresses_follow_up",
        {
            "project": {"id": 7, "status": "active"},
            "todo": {"id": 42, "status": "open"},
            "external_todo": {"task_id": "dt-42", "done": True, "pulled_at": "now"},
            "follow_up": {"id": 8, "todo_id": 42, "status": "draft"},
        },
        _verify_completed_suppression,
    ),
    SemanticCase(
        "owner_correction_updates_todo_and_suppresses_old_draft",
        {
            "todo": {"id": 42, "owner_user_id": "uid-alex", "status": "open"},
            "follow_up": {"id": 8, "todo_id": 42, "owner_user_id": "uid-alex"},
            "reply": "Correction: Mina owns this deliverable, not Alex.",
            "verified_identity": {"name": "Mina", "user_id": "uid-mina"},
        },
        _verify_owner_correction,
    ),
    SemanticCase(
        "follow_up_reply_updates_existing_work_item_instead_of_creating_duplicate",
        {
            "todo": {"id": 42, "status": "open", "owner_user_id": "uid-alex"},
            "follow_up": {"id": 8, "todo_id": 42, "status": "sent"},
            "reply": "The acceptance plan is complete and attached.",
            "completion_evidence": {
                "attachment": "acceptance-plan.pdf",
                "verified": True,
            },
        },
        _verify_reply_updates_existing,
    ),
    SemanticCase(
        "stale_follow_up_is_skipped",
        {
            "project": {"id": 7, "status": "archived"},
            "todo": {"id": 42, "status": "cancelled"},
            "external_todo": {"task_id": "dt-42", "done": False, "pulled_at": "now"},
            "follow_up": {
                "id": 8,
                "todo_id": 42,
                "status": "draft",
                "scheduled_at": "90 days ago",
            },
            "current_evidence": "The initiative was cancelled and archived after the draft was made.",
        },
        _verify_stale_suppression,
    ),
    SemanticCase(
        "sensitive_follow_up_uses_verified_direct_target",
        {
            "project": {"id": 7, "category": "HR", "status": "active"},
            "todo": {"id": 42, "status": "open", "owner_user_id": "uid-alex"},
            "sensitivity": "confidential personnel matter",
            "verified_identity": {
                "name": "Alex",
                "user_id": "uid-alex",
                "direct_conversation_id": "direct-alex",
            },
            "requested_check": "Ask privately for current blocker and ETA.",
        },
        _verify_sensitive_direct_target,
    ),
)


def _native_decision(tmp_path: Path, case: SemanticCase) -> TaskAgentDecision:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "fixture.json"
    log_path = tmp_path / "events.jsonl"
    config_path.write_text(
        json.dumps(
            {"skill_paths": [str(SKILL_PATH.resolve())], "operation_responses": []}
        ),
        encoding="utf-8",
    )
    prompt = (
        "Read the available work-tracking Skill, then decide the current work state "
        "from the supplied facts. Do not perform writes or external actions.\n\n"
        f"Skill path: {SKILL_PATH.resolve()}\n\n"
        f"Current data:\n{json.dumps(case.current_data, ensure_ascii=False, indent=2)}\n\n"
        "Memory connector status facts: unavailable in this isolated fixture.\n\n"
        "Return exactly one TaskAgentDecision JSON object satisfying this Pydantic "
        "schema:\n"
        f"{json.dumps(TaskAgentDecision.model_json_schema(), ensure_ascii=False, indent=2)}"
    )
    runner = CodexRunner(workspace=workspace)
    command = runner.build_command(
        prompt=prompt,
        session_id=None,
        use_output_schema=False,
        approval_policy="never",
        developer_instructions=(
            "You are the Task Agent. Use the loaded Skill for all work-tracking "
            "judgment and return only the requested JSON contract."
        ),
        use_approval_bypass=False,
    )
    command.insert(command.index("--cd"), "--skip-git-repo-check")
    command = isolate_read_only_fixture_command(
        command,
        server_command=sys.executable,
        server_args=(
            "-m",
            "tests.support.task6_read_fixture_mcp",
            str(config_path),
            str(log_path),
        ),
        server_cwd=str(REPOSITORY_ROOT),
    )
    assert_isolated_read_only_fixture_command(command)
    process = run_process_with_idle_timeout(
        command,
        prompt=prompt,
        env={"PATH": os.environ["PATH"]},
        total_timeout_seconds=300,
        idle_timeout_seconds=120,
        on_stdout_line=lambda _line: None,
    )
    assert process.returncode == 0, process.stderr
    records = [json.loads(line) for line in process.stdout.splitlines()]
    messages = [
        record["item"]["text"]
        for record in records
        if record.get("type") == "item.completed"
        and isinstance(record.get("item"), dict)
        and record["item"].get("type") == "agent_message"
    ]
    assert messages, process.stdout
    decision = TaskAgentDecision.model_validate_json(messages[-1])
    events = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    skill_events = [event for event in events if event["tool"] == "read_skill"]
    assert len(skill_events) == 1
    assert skill_events[0]["result"]["path"] == str(SKILL_PATH.resolve())
    assert (
        skill_events[0]["result"]["sha256"]
        == hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest()
    )
    return decision


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_native_work_tracking_semantics(tmp_path: Path, case: SemanticCase):
    decision = _native_decision(tmp_path, case)
    case.verify(decision, case.current_data)
