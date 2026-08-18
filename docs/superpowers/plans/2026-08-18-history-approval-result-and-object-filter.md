# History Approval Result and Object Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the verified business result of every approval item as a pill in History and replace the object-type checkboxes with one dropdown.

**Architecture:** Add one pure resolver that converts existing structured Consumer/Audit records, direct OA receipts, and workflow states into a typed approval result without parsing prose or adding database state. The server-rendered History list uses that resolver only for approval cards; the existing detail page and non-approval cards retain their current rendering. The History object filter becomes a singular UI and query parameter while the store continues receiving the tuple it already understands.

**Tech Stack:** Python 3.12, Pydantic v2 models, FastAPI server-rendered HTML, SQLite, pytest.

---

## File Map

- Create `app/approval_history.py`: typed approval-result values and pure structured resolver.
- Create `tests/test_approval_history.py`: unit coverage for structured Audit, direct OA, workflow, malformed, and ambiguous states.
- Modify `app/audit_web.py`: approval-result pill rendering, approval Agent-run loading, and singular object dropdown/query behavior.
- Modify `tests/test_audit_web.py`: History integration, dropdown filtering, query preservation, and non-approval regression coverage.
- Modify `CHANGELOG.md`: user-visible History behavior after all tests pass.

The resolver is kept outside `app/audit_web.py` so structured evidence interpretation is independent from HTML and can be tested without a store or web app.

### Task 1: Resolve approval results from structured evidence

**Files:**
- Create: `app/approval_history.py`
- Create: `tests/test_approval_history.py`

- [ ] **Step 1: Write the failing resolver tests**

Create `tests/test_approval_history.py` with compact builders for `ReplyAttempt` and `AgentRun`. Cover confirmed proposal actions, direct OA actions, workflow states, invalid JSON, unconfirmed proposals, and conflicting actions.

```python
import json

import pytest

from app.approval_history import (
    ApprovalHistoryResult,
    resolve_approval_history_result,
)
from app.store import AgentRole, AgentRun, ReplyAttempt


def _attempt(**updates: object) -> ReplyAttempt:
    values: dict[str, object] = {
        "id": 1,
        "conversation_id": "cid-approval",
        "conversation_title": "审批通知",
        "trigger_message_id": "msg-approval",
        "trigger_sender": "OA审批",
        "trigger_text": "请处理审批",
        "action": "agent_run",
        "sensitivity_kind": "internal_personnel",
        "agent_run_id": 12,
        "codex_reason": "",
        "draft_reply_text": "",
        "final_reply_text": "",
        "permission_action": "",
        "permission_reason": "",
        "send_status": "completed",
        "send_error": "",
        "retry_count": 0,
        "oa_process_instance_id": "process-1",
        "created_at": "2026-08-18 10:00:00",
        "updated_at": "2026-08-18 10:00:00",
    }
    values.update(updates)
    return ReplyAttempt.model_validate(values)


def _run(
    run_id: int,
    role: AgentRole,
    result: dict[str, object] | str,
    *,
    parent_agent_run_id: int | None = None,
) -> AgentRun:
    payload = result if isinstance(result, str) else json.dumps(result)
    return AgentRun(
        id=run_id,
        reply_task_id=20,
        execution_generation="initial",
        role=role,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent_agent_run_id,
        operation_id="",
        status="completed",
        final_result_json=payload,
        created_at="2026-08-18 10:00:00",
        updated_at="2026-08-18 10:00:00",
    )


def _consumer(*operations: str, outcome: str = "proposal") -> AgentRun:
    proposal = None
    if outcome == "proposal":
        proposal = {
            "objective": "处理审批并通知申请人",
            "actions": [
                {
                    "description": operation,
                    "capability": "agent_cli.dws",
                    "operation": operation,
                    "target": {"process_instance_id": "process-1"},
                    "payload": {"argv": ["dws", *operation.split()]},
                    "expected_verification": "读回审批记录",
                }
                for operation in operations
            ],
            "sourced_facts": [],
            "authored_judgment": "结构化审批判断",
        }
    return _run(
        11,
        AgentRole.CONSUMER,
        {
            "outcome": outcome,
            "summary": "结构化 Consumer 结果",
            "proposal": proposal,
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        },
    )


def _confirmed_audit() -> AgentRun:
    return _run(
        12,
        AgentRole.AUDIT,
        {
            "outcome": "executed",
            "summary": "已核验执行",
            "proposal_revision": 0,
            "side_effect_state": "confirmed",
            "feedback": None,
            "external_result": {
                "operation_id": "agent-task:20:initial:proposal:0",
                "verification_summary": "结构化读回已确认",
                "live_result_reference": {"process_instance_id": "process-1"},
            },
            "reconciliation": [],
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        },
        parent_agent_run_id=11,
    )


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("oa approval approve", ApprovalHistoryResult.APPROVED),
        ("oa approval return", ApprovalHistoryResult.RETURNED),
        ("oa approval reject", ApprovalHistoryResult.REJECTED),
        ("oa approval comment", ApprovalHistoryResult.COMMENTED_PENDING),
    ],
)
def test_confirmed_structured_approval_action_resolves_business_result(
    operation: str,
    expected: ApprovalHistoryResult,
) -> None:
    assert resolve_approval_history_result(
        _attempt(), [_consumer(operation), _confirmed_audit()]
    ) is expected


@pytest.mark.parametrize(
    ("oa_action", "expected"),
    [
        ("同意", ApprovalHistoryResult.APPROVED),
        ("退回", ApprovalHistoryResult.RETURNED),
        ("拒绝", ApprovalHistoryResult.REJECTED),
        ("评论", ApprovalHistoryResult.COMMENTED_PENDING),
    ],
)
def test_successful_direct_oa_action_resolves_business_result(
    oa_action: str,
    expected: ApprovalHistoryResult,
) -> None:
    attempt = _attempt(
        action="oa_approval",
        agent_run_id=None,
        oa_action=oa_action,
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="commented",
    )
    assert resolve_approval_history_result(attempt, []) is expected


@pytest.mark.parametrize(
    ("send_status", "expected"),
    [
        ("needs_human", ApprovalHistoryResult.NEEDS_HUMAN),
        ("pending_reconciliation", ApprovalHistoryResult.PROCESSING),
        ("processing", ApprovalHistoryResult.PROCESSING),
        ("failed", ApprovalHistoryResult.FAILED),
        ("blocked", ApprovalHistoryResult.FAILED),
    ],
)
def test_unconfirmed_workflow_state_resolves_without_guessing(
    send_status: str,
    expected: ApprovalHistoryResult,
) -> None:
    assert resolve_approval_history_result(
        _attempt(send_status=send_status), []
    ) is expected


def test_structured_no_action_resolves_no_action() -> None:
    assert resolve_approval_history_result(
        _attempt(send_status="skipped", agent_run_id=11),
        [_consumer(outcome="no_action")],
    ) is ApprovalHistoryResult.NO_ACTION


def test_unconfirmed_or_malformed_evidence_is_unknown() -> None:
    assert resolve_approval_history_result(
        _attempt(), [_consumer("oa approval approve")]
    ) is ApprovalHistoryResult.UNKNOWN
    assert resolve_approval_history_result(
        _attempt(), [_run(11, AgentRole.CONSUMER, "not-json")]
    ) is ApprovalHistoryResult.UNKNOWN


def test_conflicting_confirmed_approval_actions_are_unknown() -> None:
    assert resolve_approval_history_result(
        _attempt(),
        [
            _consumer("oa approval approve", "oa approval reject"),
            _confirmed_audit(),
        ],
    ) is ApprovalHistoryResult.UNKNOWN


def test_non_approval_attempt_has_no_approval_result() -> None:
    assert resolve_approval_history_result(
        _attempt(oa_process_instance_id="", action="send_reply"), []
    ) is None
```

- [ ] **Step 2: Run the tests and verify the import failure**

Run:

```bash
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest tests/test_approval_history.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.approval_history'`.

- [ ] **Step 3: Implement the typed resolver**

Create `app/approval_history.py`. Treat operation names and direct OA action values as protocol values, never search prose fields.

```python
from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum

from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
)
from app.agent_result import SideEffectState
from app.store import AgentRole, AgentRun, ReplyAttempt


class ApprovalHistoryResult(StrEnum):
    APPROVED = "approved"
    RETURNED = "returned"
    REJECTED = "rejected"
    COMMENTED_PENDING = "commented_pending"
    NO_ACTION = "no_action"
    NEEDS_HUMAN = "needs_human"
    PROCESSING = "processing"
    FAILED = "failed"
    UNKNOWN = "unknown"


_OPERATION_RESULTS = {
    "oa approval approve": ApprovalHistoryResult.APPROVED,
    "oa approval return": ApprovalHistoryResult.RETURNED,
    "oa approval reject": ApprovalHistoryResult.REJECTED,
    "oa approval comment": ApprovalHistoryResult.COMMENTED_PENDING,
}
_DIRECT_ACTION_RESULTS = {
    "approve": ApprovalHistoryResult.APPROVED,
    "approved": ApprovalHistoryResult.APPROVED,
    "同意": ApprovalHistoryResult.APPROVED,
    "通过": ApprovalHistoryResult.APPROVED,
    "return": ApprovalHistoryResult.RETURNED,
    "returned": ApprovalHistoryResult.RETURNED,
    "退回": ApprovalHistoryResult.RETURNED,
    "reject": ApprovalHistoryResult.REJECTED,
    "rejected": ApprovalHistoryResult.REJECTED,
    "拒绝": ApprovalHistoryResult.REJECTED,
    "comment": ApprovalHistoryResult.COMMENTED_PENDING,
    "commented": ApprovalHistoryResult.COMMENTED_PENDING,
    "评论": ApprovalHistoryResult.COMMENTED_PENDING,
    "留言": ApprovalHistoryResult.COMMENTED_PENDING,
}
_DIRECT_TERMINAL_STATUSES = {"sent", "commented", "completed"}


def resolve_approval_history_result(
    attempt: ReplyAttempt,
    agent_runs: Sequence[AgentRun],
) -> ApprovalHistoryResult | None:
    if not (
        attempt.action.strip().lower() == "oa_approval"
        or attempt.oa_process_instance_id.strip()
    ):
        return None

    confirmed = _confirmed_proposal_result(agent_runs)
    if confirmed is not None:
        return confirmed

    direct = _confirmed_direct_result(attempt)
    if direct is not None:
        return direct

    status = attempt.send_status.strip().lower()
    if status == "needs_human":
        return ApprovalHistoryResult.NEEDS_HUMAN
    if status in {"pending", "processing", "pending_reconciliation"}:
        return ApprovalHistoryResult.PROCESSING
    if status in {"failed", "blocked"}:
        return ApprovalHistoryResult.FAILED

    consumer_result = _latest_consumer_result(agent_runs)
    if (
        consumer_result is not None
        and consumer_result.outcome is ConsumerOutcome.NO_ACTION
    ):
        return ApprovalHistoryResult.NO_ACTION
    return ApprovalHistoryResult.UNKNOWN


def _confirmed_proposal_result(
    agent_runs: Sequence[AgentRun],
) -> ApprovalHistoryResult | None:
    runs_by_id = {run.id: run for run in agent_runs}
    for audit_run in reversed(agent_runs):
        if audit_run.role is not AgentRole.AUDIT or not audit_run.final_result_json:
            continue
        try:
            audit = AuditAgentResult.model_validate_json(audit_run.final_result_json)
        except ValueError:
            continue
        if (
            audit.outcome is not AuditOutcome.EXECUTED
            or audit.side_effect_state is not SideEffectState.CONFIRMED
            or audit_run.parent_agent_run_id is None
        ):
            continue
        consumer_run = runs_by_id.get(audit_run.parent_agent_run_id)
        if consumer_run is None or consumer_run.role is not AgentRole.CONSUMER:
            return ApprovalHistoryResult.UNKNOWN
        try:
            consumer = ConsumerAgentResult.model_validate_json(
                consumer_run.final_result_json
            )
        except ValueError:
            return ApprovalHistoryResult.UNKNOWN
        if consumer.proposal is None:
            return ApprovalHistoryResult.UNKNOWN
        results = {
            _OPERATION_RESULTS[action.operation.strip().lower()]
            for action in consumer.proposal.actions
            if action.operation.strip().lower() in _OPERATION_RESULTS
        }
        return results.pop() if len(results) == 1 else ApprovalHistoryResult.UNKNOWN
    return None


def _latest_consumer_result(
    agent_runs: Sequence[AgentRun],
) -> ConsumerAgentResult | None:
    for run in reversed(agent_runs):
        if run.role is not AgentRole.CONSUMER or not run.final_result_json:
            continue
        try:
            return ConsumerAgentResult.model_validate_json(run.final_result_json)
        except ValueError:
            return None
    return None


def _confirmed_direct_result(
    attempt: ReplyAttempt,
) -> ApprovalHistoryResult | None:
    result = _DIRECT_ACTION_RESULTS.get(attempt.oa_action.strip().lower())
    if result is None:
        return None
    if attempt.send_status.strip().lower() in _DIRECT_TERMINAL_STATUSES:
        return result
    return result if _oa_receipt_succeeded(attempt.oa_action_result_json) else None


def _oa_receipt_succeeded(raw: str) -> bool:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    nested_result = payload.get("result")
    nested_dws = payload.get("dws_action_result")
    candidates = [
        payload.get("success"),
        nested_result.get("success") if isinstance(nested_result, dict) else None,
        nested_dws.get("success") if isinstance(nested_dws, dict) else None,
        payload.get("errcode") == 0 if "errcode" in payload else None,
    ]
    return any(value is True or value == 1 for value in candidates)
```

- [ ] **Step 4: Run the resolver tests and verify they pass**

Run:

```bash
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest tests/test_approval_history.py -q
```

Expected: all tests in `tests/test_approval_history.py` pass.

- [ ] **Step 5: Commit the resolver**

```bash
git add app/approval_history.py tests/test_approval_history.py
git commit -m "feat: resolve approval history results"
```

### Task 2: Render business-result pills in History

**Files:**
- Modify: `app/audit_web.py:80-110`
- Modify: `app/audit_web.py:4360-4510`
- Modify: `app/audit_web.py:9640-9720`
- Modify: `app/audit_web.py:9970-10010`
- Modify: `tests/test_audit_web.py`

- [ ] **Step 1: Add failing History integration tests**

Add tests that render a confirmed structured approval, a direct return, an unknown approval, and a normal reply. Use a monkeypatch for `_agent_runs_for_attempt` so the test exercises the History renderer without coupling to Agent-run persistence setup.

```python
def test_history_approval_card_shows_business_result_not_agent_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-approval-pill",
        conversation_title="员工请假审批",
        trigger_message_id="msg-approval-pill",
        trigger_sender="OA审批",
        trigger_text="请处理员工请假审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        agent_run_id=12,
        oa_process_instance_id="process-pill",
        oa_task_id="task-pill",
        oa_action="review",
        send_status="completed",
    )
    consumer = _approval_history_consumer_run(
        operation="oa approval approve",
        process_instance_id="process-pill",
    )
    audit = _approval_history_confirmed_audit_run(
        parent_agent_run_id=consumer.id,
        process_instance_id="process-pill",
    )
    monkeypatch.setattr(
        audit_web_module,
        "_agent_runs_for_attempt",
        lambda *_args, **_kwargs: [consumer, audit],
    )

    html = render_attempt_list(store, search_object_type="approval")

    assert f'href="/attempts/{attempt_id}"' in html
    assert "✓ 已同意" in html
    assert "💬 Completed" not in html
    assert "🧾 review" not in html


def test_history_direct_return_and_unknown_approval_have_result_pills(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-return",
        conversation_title="材料审批",
        trigger_message_id="msg-return",
        trigger_sender="OA审批",
        trigger_text="材料不足",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        oa_process_instance_id="process-return",
        oa_action="退回",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="commented",
    )
    store.record_reply_attempt(
        conversation_id="cid-unknown",
        conversation_title="未知结果审批",
        trigger_message_id="msg-unknown",
        trigger_sender="OA审批",
        trigger_text="记录不完整",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        oa_process_instance_id="process-unknown",
        oa_action="review",
        send_status="completed",
    )

    html = render_attempt_list(store, search_object_type="approval")

    assert "↩ 已退回" in html
    assert "结果未知" in html


def test_history_non_approval_card_keeps_existing_status_pill(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-reply",
        conversation_title="普通回复",
        trigger_message_id="msg-reply",
        trigger_sender="Mina",
        trigger_text="请回复",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    html = render_attempt_list(store, search_object_type="replay")

    assert "💬 Sent" in html
```

Define these helpers beside the existing test helpers:

```python
def _approval_history_consumer_run(
    *, operation: str, process_instance_id: str
) -> AgentRun:
    return AgentRun(
        id=11,
        reply_task_id=20,
        execution_generation="initial",
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        status="completed",
        final_result_json=json.dumps(
            {
                "outcome": "proposal",
                "summary": "准备处理审批",
                "proposal": {
                    "objective": "处理审批并通知申请人",
                    "actions": [
                        {
                            "description": "执行结构化审批动作",
                            "capability": "agent_cli.dws",
                            "operation": operation,
                            "target": {
                                "process_instance_id": process_instance_id
                            },
                            "payload": {"argv": ["dws", *operation.split()]},
                            "expected_verification": "读回审批记录",
                        }
                    ],
                    "sourced_facts": [],
                    "authored_judgment": "结构化审批判断",
                },
                "decision_options": [],
                "error": {
                    "code": "",
                    "retryable": False,
                    "authorization_required": False,
                },
            }
        ),
        created_at="2026-08-18 10:00:00",
        updated_at="2026-08-18 10:00:00",
    )


def _approval_history_confirmed_audit_run(
    *, parent_agent_run_id: int, process_instance_id: str
) -> AgentRun:
    return AgentRun(
        id=12,
        reply_task_id=20,
        execution_generation="initial",
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=parent_agent_run_id,
        operation_id="",
        status="completed",
        final_result_json=json.dumps(
            {
                "outcome": "executed",
                "summary": "已核验审批动作",
                "proposal_revision": 0,
                "side_effect_state": "confirmed",
                "feedback": None,
                "external_result": {
                    "operation_id": "agent-task:20:initial:proposal:0",
                    "verification_summary": "审批记录读回一致",
                    "live_result_reference": {
                        "process_instance_id": process_instance_id
                    },
                },
                "reconciliation": [],
                "decision_options": [],
                "error": {
                    "code": "",
                    "retryable": False,
                    "authorization_required": False,
                },
            }
        ),
        side_effect_state="confirmed",
        created_at="2026-08-18 10:00:00",
        updated_at="2026-08-18 10:00:00",
    )
```

- [ ] **Step 2: Run the integration tests and verify the old pills fail**

Run:

```bash
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest \
  tests/test_audit_web.py::test_history_approval_card_shows_business_result_not_agent_status \
  tests/test_audit_web.py::test_history_direct_return_and_unknown_approval_have_result_pills \
  tests/test_audit_web.py::test_history_non_approval_card_keeps_existing_status_pill -q
```

Expected: approval assertions fail because History still renders `Completed` and `review` and does not render the result labels.

- [ ] **Step 3: Add approval pill presentation and load structured runs**

Import the resolver and define the presentation mapping in `app/audit_web.py`:

```python
from app.approval_history import (
    ApprovalHistoryResult,
    resolve_approval_history_result,
)


_APPROVAL_RESULT_PRESENTATION = {
    ApprovalHistoryResult.APPROVED: ("✓ 已同意", "approved"),
    ApprovalHistoryResult.RETURNED: ("↩ 已退回", "returned"),
    ApprovalHistoryResult.REJECTED: ("× 已拒绝", "rejected"),
    ApprovalHistoryResult.COMMENTED_PENDING: ("✎ 已留言，仍待审批", "commented"),
    ApprovalHistoryResult.NO_ACTION: ("无需处理", "skipped"),
    ApprovalHistoryResult.NEEDS_HUMAN: ("待你处理", "needs-human"),
    ApprovalHistoryResult.PROCESSING: ("处理中", "processing"),
    ApprovalHistoryResult.FAILED: ("处理失败", "failed"),
    ApprovalHistoryResult.UNKNOWN: ("结果未知", "unknown"),
}


def _approval_history_result_pill(result: ApprovalHistoryResult) -> str:
    label, state = _APPROVAL_RESULT_PRESENTATION[result]
    return (
        f'<span class="pill status-action {_action_state_class(state)}">'
        f'{escape(label)}</span>'
    )
```

In the History loop, always load the current generation's Agent runs for approval items, reuse that same list for attention, resolve the approval result, and choose the approval pill instead of `_attempt_action_pills`:

```python
        history_type = _history_attempt_type(attempt)
        agent_runs = (
            _agent_runs_for_attempt(store, attempt, agent_runs_cache)
            if history_type[0] == "oa"
            else []
        )
        # Existing attention logic follows. Reuse agent_runs instead of loading again.
        approval_result = resolve_approval_history_result(attempt, agent_runs)
        action_pills = (
            _approval_history_result_pill(approval_result)
            if approval_result is not None
            else _attempt_action_pills(
                attempt,
                later_attempt=later_attempt,
                recovery_state=recovery_state,
            )
        )
```

Render `action_pills` in the card title. Keep `_attempt_action_pills` unchanged so the attempt detail page stays outside this change's scope.

Add a neutral theme rule using existing variables rather than a fixed light background:

```css
.action-state-unknown{background:var(--surface);color:var(--stone);border-color:var(--hairline)}
```

- [ ] **Step 4: Run the focused History tests**

Run the three new tests plus the existing approval-detail and attention tests:

```bash
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest \
  tests/test_audit_web.py -q -k 'history_approval or oa_approval or pending_reconciliation or needs_human'
```

Expected: selected tests pass, and non-approval cards retain their status pills.

- [ ] **Step 5: Commit the History pill integration**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: show approval outcomes in history"
```

### Task 3: Replace object-type checkboxes with one dropdown

**Files:**
- Modify: `app/audit_web.py:280-300`
- Modify: `app/audit_web.py:3380-3410`
- Modify: `app/audit_web.py:3990-4100`
- Modify: `app/audit_web.py:4290-4335`
- Modify: `app/audit_web.py:8260-8290`
- Modify: `tests/test_audit_web.py:879-1010`

- [ ] **Step 1: Replace the checkbox assertions with failing dropdown assertions**

Rename the existing checkbox test to `test_history_search_object_type_dropdown_controls_results`. Keep its seeded replay, approval, task, meeting, and WeChat data, but call `render_attempt_list` with the singular `search_object_type` argument and assert dropdown markup:

```python
    default_html = render_attempt_list(
        store,
        query="风险预算",
        query_embedding=[1.0, 0.0],
    )
    assert '<select name="object_type"' in default_html
    assert '<option value="" selected>对象：全部</option>' in default_html
    assert '<option value="approval">审批</option>' in default_html
    assert 'type="checkbox" name="object_type"' not in default_html

    approval_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="approval",
        query_embedding=[1.0, 0.0],
    )
    assert '<option value="approval" selected>审批</option>' in approval_only_html
    assert "Approval Search Group" in approval_only_html
    assert "History Search Group" not in approval_only_html
    assert "Task Search Group" not in approval_only_html
    assert "相似 Codex sessions" not in approval_only_html
```

Retain explicit assertions for every remaining selection:

```python
    replay_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="replay",
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" in replay_only_html
    assert "Approval Search Group" not in replay_only_html
    assert "Task Search Group" not in replay_only_html
    assert "相似 Codex sessions" not in replay_only_html

    wechat_only_html = render_attempt_list(
        store,
        query="channel filter",
        search_object_type="wechat",
    )
    assert "WeChat History Group" in wechat_only_html
    assert "DingTalk History Group" not in wechat_only_html

    task_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="task",
        query_embedding=[1.0, 0.0],
    )
    assert "Task Search Group" in task_only_html
    assert "History Search Group" not in task_only_html
    assert "相似 Codex sessions" not in task_only_html

    meeting_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="meeting",
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" not in meeting_only_html
    assert "Task Search Group" not in meeting_only_html
    assert "相似 Codex sessions" in meeting_only_html
    assert f"/meeting-attempts/{run_id}" in meeting_only_html
```

Add one route-level query-preservation test:

```python
def test_history_object_dropdown_preserves_search_status_limit_and_page(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    for index in range(25):
        store.record_reply_attempt(
            conversation_id=f"cid-{index}",
            conversation_title=f"Approval {index}",
            trigger_message_id=f"msg-{index}",
            trigger_sender="OA审批",
            trigger_text="风险预算审批",
            action="oa_approval",
            sensitivity_kind="general",
            oa_process_instance_id=f"process-{index}",
            oa_action="同意",
            oa_action_result_json='{"errcode":0}',
            send_status="sent",
        )
    response = TestClient(create_audit_app(db_path)).get(
        "/history?q=风险预算&type=sent&object_type=approval&limit=20&page=1"
    )
    assert response.status_code == 200
    assert "q=%E9%A3%8E%E9%99%A9%E9%A2%84%E7%AE%97" in response.text
    assert "type=sent" in response.text
    assert "object_type=approval" in response.text
    assert "limit=20" not in response.text
    assert "page=2" in response.text
```

- [ ] **Step 2: Run the dropdown tests and verify they fail against checkboxes**

Run:

```bash
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest \
  tests/test_audit_web.py::test_history_search_object_type_dropdown_controls_results \
  tests/test_audit_web.py::test_history_object_dropdown_preserves_search_status_limit_and_page -q
```

Expected: failures show checkbox markup and the missing singular `search_object_type` argument.

- [ ] **Step 3: Normalize one selected object type**

Replace the multi-value parser with a singular parser plus the tuple adapter required by `AutoReplyStore`:

```python
def _history_search_object_type(value: str) -> str:
    selected = value.strip().lower()
    return selected if selected in HISTORY_SEARCH_OBJECT_TYPES else ""


def _history_search_object_types(value: str) -> tuple[str, ...]:
    selected = _history_search_object_type(value)
    return (selected,) if selected else HISTORY_SEARCH_OBJECT_TYPES
```

Rename `search_object_types` parameters to `search_object_type` in `render_attempt_list`, `_history_table_header`, and `_history_page_href`. Inside `render_attempt_list`:

```python
    selected_object_type = _history_search_object_type(search_object_type)
    object_types = _history_search_object_types(selected_object_type)
```

Pass `selected_object_type` to toolbar and pagination helpers. In `_history_page_href`, emit only one value:

```python
    if search_object_type:
        params["object_type"] = search_object_type
```

Update the FastAPI route to read one value:

```python
                search_object_type=str(
                    request.query_params.get("object_type", "")
                ),
```

- [ ] **Step 4: Render the dropdown and remove checkbox-only CSS**

Replace `_history_search_object_type_checkboxes` with:

```python
def _history_search_object_type_select(search_object_type: str) -> str:
    labels = {
        "replay": "replay",
        "wechat": "wechat",
        "approval": "审批",
        "task": "task",
        "meeting": "meeting",
    }
    options = [
        f'<option value=""{" selected" if not search_object_type else ""}>'
        "对象：全部</option>"
    ]
    options.extend(
        f'<option value="{escape(value)}"'
        f'{" selected" if value == search_object_type else ""}>'
        f'{escape(label)}</option>'
        for value, label in labels.items()
    )
    return (
        '<select name="object_type" '
        'class="table-type-select history-object-type-select" '
        'aria-label="History object filter" onchange="this.form.submit()">'
        f'{"".join(options)}</select>'
    )
```

Call it beside `_history_type_select`. Remove `.history-object-type-filter`, `.history-object-type-option`, legend, and checkbox CSS. Keep the dropdown on existing `.table-type-select` theme variables so dark mode remains readable.

- [ ] **Step 5: Run all History web tests**

Run:

```bash
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest tests/test_audit_web.py -q
```

Expected: all `tests/test_audit_web.py` tests pass.

- [ ] **Step 6: Commit the dropdown change**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: use a dropdown for history objects"
```

### Task 4: Document, verify, deploy, and clean up

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the complete regression suite before documentation**

Run:

```bash
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest -q
```

Expected: the complete suite passes with only the repository's documented skips/deselections.

- [ ] **Step 2: Add the user-visible changelog entry**

Under `## Unreleased` → `### Audit visibility`, add:

```markdown
- Show each approval's verified business result directly on its History card
  instead of exposing normal Agent `Completed` or `Skipped` states. Replace
  the History object checkboxes with a single dropdown while preserving search,
  status, page-size, pagination, and meeting-session filtering.
```

- [ ] **Step 3: Run documentation and focused regression checks**

Run:

```bash
git diff --check
PYTHONPATH=. /Users/derek/Documents/Projects/ceo-agent-service/.venv/bin/pytest \
  tests/test_approval_history.py tests/test_audit_web.py -q
```

Expected: `git diff --check` prints nothing and all focused tests pass.

- [ ] **Step 4: Commit the documentation**

```bash
git add CHANGELOG.md
git commit -m "docs: record history approval results"
```

- [ ] **Step 5: Verify the feature branch and release checkout before merge**

Run:

```bash
git status --short --branch
git log -5 --oneline
git -C /Users/derek/Documents/Projects/ceo-agent-service-release status --short --branch
```

Expected: the feature branch has no tracked changes; only the known visual-companion `.superpowers/` directory may remain untracked. The release checkout is on `main`; preserve its known untracked `.venv` and stop if any unexpected tracked change appears.

- [ ] **Step 6: Merge into the release checkout**

Run:

```bash
git -C /Users/derek/Documents/Projects/ceo-agent-service-release merge --no-ff codex/actionable-recovery-backlog -m "merge: deploy history approval result pills"
```

Expected: merge succeeds without conflicts.

- [ ] **Step 7: Restart and verify the launchd service**

Capture the old PID, restart, and read back the new process:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

Expected: the service state is `running`, the PID changes, and the new process listens on `127.0.0.1:8765`.

- [ ] **Step 8: Verify queues and live History markup**

Run read-only checks:

```bash
sqlite3 -header -column "/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3" \
  "select status, count(*) as count from reply_tasks where status in ('failed','processing') group by status;"
curl -sS "http://127.0.0.1:8765/history?object_type=approval&limit=20" \
  | rg 'name="object_type"|type="checkbox" name="object_type"|已同意|已退回|已拒绝|无需处理|待你处理|处理中|处理失败|结果未知|Completed|Skipped'
```

Expected: no failed or stuck processing tasks; one `<select name="object_type">`; no object-type checkbox; approval business-result pills are present; normal approval cards do not expose `Completed` or `Skipped`.

Check every dropdown selection with one request each:

```bash
for object_type in replay wechat approval task meeting; do
  curl -sS "http://127.0.0.1:8765/history?object_type=${object_type}&limit=20" \
    | rg -m1 '<option value="'"${object_type}"'" selected>'
done
```

Expected: each requested option is selected in the returned HTML.

- [ ] **Step 9: Stop the visual companion and remove only this task's temporary files**

Stop the exact brainstorming session and delete only its session directory after confirming the server stopped:

```bash
/Users/derek/.agents/skills/brainstorming/scripts/stop-server.sh \
  /Users/derek/Documents/Projects/ceo-agent-service/.worktrees/actionable-recovery-backlog/.superpowers/brainstorm/66385-1787060309
test -f /Users/derek/Documents/Projects/ceo-agent-service/.worktrees/actionable-recovery-backlog/.superpowers/brainstorm/66385-1787060309/state/server-stopped
rm -rf /Users/derek/Documents/Projects/ceo-agent-service/.worktrees/actionable-recovery-backlog/.superpowers/brainstorm/66385-1787060309
```

Expected: the exact temporary session directory is gone; no other `.superpowers` session or user file is removed.

- [ ] **Step 10: Final readback**

Run:

```bash
git status --short --branch
git -C /Users/derek/Documents/Projects/ceo-agent-service-release status --short --branch
```

Expected: feature and release tracked worktrees are clean, with only known unrelated untracked files preserved. Report the focused and full test totals, merge commit, new service PID, queue state, live History result-pill evidence, dropdown evidence, and temporary-file cleanup.
