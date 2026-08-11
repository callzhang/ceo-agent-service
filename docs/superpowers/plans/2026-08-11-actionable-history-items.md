# Actionable History Items Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every History item that requires manager intervention show a persisted reason, external-effect state, and actionable choices inline, while retryable failures show their persisted exponential-backoff plan without human-action buttons.

**Architecture:** Add one presentation-only state model that consumes persisted attempt, task, and agent-decision data without making business decisions from message text. The History list and attempt detail render the same model. Existing rerun and human-decision paths remain the only execution paths; the list submits to those paths so idempotency and external-action reconciliation stay centralized.

**Tech Stack:** Python 3.12, Pydantic models, SQLite, FastAPI server-rendered HTML, pytest.

---

## File structure

- Create `app/history_actions.py`: pure derivation of manager-attention state from persisted records for replies, meetings, and task/follow-up entries.
- Modify `app/audit_web.py`: load required task/agent-run data once, render the shared state in History and detail, and expose existing actions inline.
- Modify `app/store.py`: provide one atomic method for accepting an explicit manager decision on an actionable attempt and requeueing the existing task.
- Modify `tests/test_history_actions.py`: focused classification and retry-plan regression tests.
- Modify `tests/test_audit_web.py`: History HTML and POST action integration tests.
- Modify `tests/test_store.py`: atomicity and idempotency tests for manager decisions.
- Modify `docs/reply-worker-reliability.md`: document History attention states and their retry/decision boundary.

### Task 1: Derive one persisted History attention state

**Files:**
- Create: `app/history_actions.py`
- Create: `tests/test_history_actions.py`

- [ ] **Step 1: Write the failing classifier tests**

```python
from app.history_actions import reply_history_attention
from app.store import ReplyAttempt, ReplyTask


def test_pending_retry_renders_persisted_backoff_without_human_actions():
    attempt = reply_attempt(send_status="failed", codex_reason="Codex provider unavailable")
    task = reply_task(status="pending", attempts=2, available_at="2026-08-11 05:14:00")

    state = reply_history_attention(
        attempt, task=task, decision_options=(), side_effect_state="none"
    )

    assert state.kind == "automatic_recovery"
    assert state.reason == "Codex provider unavailable"
    assert state.retry_attempt == 2
    assert state.retry_limit == 3
    assert state.retry_at == "2026-08-11 05:14:00"
    assert [action.key for action in state.actions] == ["details"]


def test_exhausted_failure_requires_manager_and_offers_safe_choices():
    attempt = reply_attempt(send_status="failed", audit_summary="Current task did not complete")
    task = reply_task(status="failed", attempts=3, available_at="")

    state = reply_history_attention(
        attempt, task=task, decision_options=(), side_effect_state="none"
    )

    assert state.kind == "needs_manager"
    assert state.reason == "Current task did not complete"
    assert [action.key for action in state.actions] == ["retry", "defer", "manual", "details"]


def test_agent_supplied_choices_are_preserved_for_general_needs_human():
    attempt = reply_attempt(send_status="needs_human", audit_summary="Two valid plans change external state")
    options = (decision_option(key="A", label="Use plan A", instruction="Use plan A", consequence="Publishes plan A"),)

    state = reply_history_attention(
        attempt, task=None, decision_options=options, side_effect_state="none"
    )

    assert state.kind == "needs_manager"
    assert state.actions[0].label == "Use plan A"
    assert state.actions[0].instruction == "Use plan A"


def test_unknown_external_effect_never_offers_replay():
    attempt = reply_attempt(send_status="failed", audit_summary="Execution receipt is unknown")

    state = reply_history_attention(
        attempt, task=None, decision_options=(), side_effect_state="unknown"
    )

    assert state.external_effect == "执行结果未知，不能安全重放"
    assert [action.key for action in state.actions] == ["manual", "details"]
```

- [ ] **Step 2: Run the classifier tests and verify RED**

Run: `.venv/bin/pytest tests/test_history_actions.py -q`

Expected: FAIL because `app.history_actions` does not exist.

- [ ] **Step 3: Implement the pure state model**

```python
from dataclasses import dataclass

from app.agent_contracts import DecisionOption
from app.store import ReplyAttempt, ReplyTask
from app.worker import MAX_REPLY_TASK_ATTEMPTS


@dataclass(frozen=True)
class HistoryAction:
    key: str
    label: str
    instruction: str = ""
    consequence: str = ""


@dataclass(frozen=True)
class HistoryAttention:
    kind: str
    reason: str
    external_effect: str
    retry_attempt: int = 0
    retry_limit: int = 0
    retry_at: str = ""
    actions: tuple[HistoryAction, ...] = ()


def reply_history_attention(
    attempt: ReplyAttempt,
    *,
    task: ReplyTask | None,
    decision_options: tuple[DecisionOption, ...],
    side_effect_state: str,
) -> HistoryAttention | None:
    status = attempt.send_status.strip().lower()
    reason = (attempt.audit_summary or attempt.codex_reason or attempt.send_error or "处理未完成").strip()
    external_effect = {
        "none": "未执行任何外部动作",
        "confirmed": "已确认产生外部动作",
        "unknown": "执行结果未知，不能安全重放",
    }[side_effect_state]
    if (
        status == "failed"
        and side_effect_state == "none"
        and task is not None
        and task.status in {"pending", "processing"}
    ):
        return HistoryAttention(
            kind="automatic_recovery",
            reason=reason,
            external_effect=external_effect,
            retry_attempt=task.attempts,
            retry_limit=MAX_REPLY_TASK_ATTEMPTS,
            retry_at=task.available_at,
            actions=(HistoryAction("details", "技术详情"),),
        )
    if status == "needs_human":
        choices = tuple(
            HistoryAction(option.key, option.label, option.instruction, option.consequence)
            for option in decision_options
        )
        return HistoryAttention(
            kind="needs_manager",
            reason=reason,
            external_effect=external_effect,
            actions=choices + _management_tail(include_retry=False),
        )
    if status == "failed":
        return HistoryAttention(
            kind="needs_manager",
            reason=reason,
            external_effect=external_effect,
            actions=(
                _management_tail(include_retry=True)
                if side_effect_state == "none"
                else _management_tail(include_retry=False, include_defer=False)
            ),
        )
    return None
```

Add the complete action-tail helper in the same file:

```python
def _management_tail(
    *,
    include_retry: bool,
    include_defer: bool = True,
) -> tuple[HistoryAction, ...]:
    actions: list[HistoryAction] = []
    if include_retry:
        actions.append(HistoryAction("retry", "重试当前任务"))
    if include_defer:
        actions.append(
            HistoryAction(
                "defer",
                "暂不处理",
                "暂不处理当前事项。审批类事项必须通知实际申请人仍待处理、缺少的材料或事实，以及下一步需要做什么。",
            )
        )
    actions.extend(
        (HistoryAction("manual", "人工处理"), HistoryAction("details", "技术详情"))
    )
    return tuple(actions)
```

Validate `side_effect_state` against the persisted values `none`, `confirmed`, and `unknown`; do not infer it from error text. Terminal statuses and `pending_reconciliation` return `None` because they do not require manager intervention.

- [ ] **Step 4: Run classifier tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_history_actions.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the state model**

```bash
git add app/history_actions.py tests/test_history_actions.py
git commit -m "feat: derive actionable history states"
```

### Task 2: Render reasons, effects, retry plans, and choices in History

**Files:**
- Modify: `app/audit_web.py:4107-4260`
- Modify: `app/audit_web.py:8587-8643`
- Modify: `app/audit_web.py:9061-9151`
- Modify: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing History rendering tests**

```python
def test_history_failed_item_shows_reason_effect_and_actions_inline(tmp_path: Path):
    store = actionable_failed_attempt_store(tmp_path)
    html = render_attempt_list(store, include_chart=False)

    assert "状态：需要你处理" in html
    assert "原因：Current task did not complete" in html
    assert "外部副作用：尚未确认执行任何外部动作" in html
    assert ">重试当前任务</button>" in html
    assert ">暂不处理</button>" in html
    assert ">人工处理</a>" in html
    assert ">技术详情</a>" in html


def test_history_retrying_item_shows_persisted_plan_without_choices(tmp_path: Path):
    store = retrying_attempt_store(tmp_path, available_at="2026-08-11 05:14:00")
    html = render_attempt_list(store, include_chart=False)

    assert "状态：系统失败，正在自动恢复" in html
    assert "第 2/3 次" in html
    assert audit_web_module._format_local_time("2026-08-11 05:14:00") in html
    assert ">重试当前任务</button>" not in html
    assert ">暂不处理</button>" not in html


def test_history_needs_human_item_shows_agent_choices_inline(tmp_path: Path):
    store, attempt_id = needs_human_store_with_agent_options(tmp_path)
    html = render_attempt_list(store, include_chart=False)

    assert "A. 同意当前方案" in html
    assert f'action="/attempts/{attempt_id}/human-decision?return_to=/"' in html
```

- [ ] **Step 2: Run the three tests and verify RED**

Run: `.venv/bin/pytest tests/test_audit_web.py -q -k 'history_failed_item_shows_reason or history_retrying_item_shows_persisted_plan or history_needs_human_item_shows_agent_choices'`

Expected: FAIL because History cards currently render only pills and the detail link.

- [ ] **Step 3: Add one shared HTML renderer**

Add `_history_attention_html(attention, *, actions_html)` to `app/audit_web.py`. It must:

```python
def _history_attention_html(attention, *, actions_html: str) -> str:
    if attention is None:
        return ""
    heading = "需要你处理" if attention.kind == "needs_manager" else "系统失败，正在自动恢复"
    retry = ""
    if attention.retry_at:
        retry = (
            f'<div><strong>重试计划：</strong>第 {attention.retry_attempt}/{attention.retry_limit} 次；'
            f'将在 {escape(_format_local_time(attention.retry_at))} 自动重试；'
            '耗尽后转为“需要你处理”。</div>'
        )
    return (
        '<section class="history-attention">'
        f'<div><strong>状态：</strong>{escape(heading)}</div>'
        f'<div><strong>原因：</strong>{escape(attention.reason)}</div>'
        f'<div><strong>外部副作用：</strong>{escape(attention.external_effect)}</div>'
        f'{retry}{actions_html}'
        '</section>'
    )
```

`_reply_history_attention_actions()` maps structured action keys to existing routes:

- `retry` → `POST /attempts/{id}/rerun?return_to=/`
- decision option and `defer` → `POST /attempts/{id}/human-decision?return_to=/`
- `manual` → the existing DingTalk popup or attempt detail
- `details` → `/attempts/{id}`

Do not infer an action from `trigger_text`, conversation title, or OA applicant text.

- [ ] **Step 4: Load persisted task and agent options for each reply item**

Reuse the existing `reply_task_cache`. For `needs_human` attempts, resolve the recorded `agent_run_id` and call `list_agent_runs_for_task_generation()` once for that task generation, then call `_needs_human_decision_options()`. Pass the resulting `HistoryAttention` to `_history_attention_html()` immediately after `.attempt-lines` so the choices are visible without opening details.

Replace the detail-only `_attempt_status_card()` messages with the same renderer inside a `card compact-card attempt-status-card` wrapper. Keep `_needs_human_decision_card()` only for its free-text “其他处理指令” form; remove duplicate option forms there.

- [ ] **Step 5: Add scoped CSS and verify GREEN**

Add `.history-attention` and `.history-attention-actions` styles beside the existing `.attempt-foot` styles. Buttons remain compact and wrap on narrow screens.

Run: `.venv/bin/pytest tests/test_audit_web.py -q -k 'history_failed_item_shows_reason or history_retrying_item_shows_persisted_plan or history_needs_human_item_shows_agent_choices or needs_human_detail_renders_agent_supplied_choices or render_attempt_detail_shows_rerun'`

Expected: PASS.

- [ ] **Step 6: Commit the shared rendering**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: expose actions in history items"
```

### Task 3: Apply the same attention state to meeting and task History items

**Files:**
- Modify: `app/history_actions.py`
- Modify: `app/audit_web.py:4445-4530`
- Modify: `tests/test_history_actions.py`
- Modify: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing cross-kind tests**

```python
def test_retrying_meeting_shows_persisted_plan_without_manager_actions(tmp_path: Path):
    store, run_id, job = retrying_meeting_store(tmp_path)
    html = render_attempt_list(store, include_chart=False)

    assert f"#meeting-{run_id}" in html
    assert "状态：系统失败，正在自动恢复" in html
    assert f"第 {job.attempts}/3 次" in html
    assert audit_web_module._format_local_time(job.available_at) in html
    assert f'/meeting-attempts/{run_id}">技术详情</a>' in html
    assert ">重试当前任务</button>" not in html


def test_failed_meeting_and_follow_up_expose_reason_and_safe_choices(tmp_path: Path):
    store, meeting_run_id, follow_up_id = failed_cross_kind_history_store(tmp_path)
    html = render_attempt_list(store, include_chart=False)

    assert "Meeting delivery failed" in html
    assert f'/meeting-attempts/{meeting_run_id}">人工处理</a>' in html
    assert "Follow-up delivery failed" in html
    assert f'/tasks/1#follow-up-{follow_up_id}">人工处理</a>' in html
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest tests/test_audit_web.py -q -k 'retrying_meeting_shows_persisted_plan or failed_meeting_and_follow_up_expose_reason'`

Expected: FAIL because meeting/task cards currently show only their summary and “查看” link.

- [ ] **Step 3: Add cross-kind pure adapters**

Add these functions to `app/history_actions.py`:

```python
def meeting_history_attention(run, job) -> HistoryAttention | None:
    reason = (run.audit_summary or run.error or job.error or "会议任务未完成").strip()
    if job.status == "retry":
        return HistoryAttention(
            kind="automatic_recovery",
            reason=reason,
            external_effect="未确认发送会议对齐消息",
            retry_attempt=job.attempts,
            retry_limit=DEFAULT_MEETING_MAX_ATTEMPTS,
            retry_at=job.available_at,
            actions=(HistoryAction("details", "技术详情"),),
        )
    if job.status in {"failed", "quarantined"}:
        return HistoryAttention(
            kind="needs_manager",
            reason=reason,
            external_effect="未确认发送会议对齐消息",
            actions=(HistoryAction("manual", "人工处理"), HistoryAction("details", "技术详情")),
        )
    return None


def task_history_attention(item: HistoryItem) -> HistoryAttention | None:
    if item.status != "failed":
        return None
    return HistoryAttention(
        kind="needs_manager",
        reason=item.output_text.strip() or "任务动作未完成",
        external_effect="任务记录未确认完成",
        actions=(HistoryAction("manual", "人工处理"), HistoryAction("details", "技术详情")),
    )
```

These adapters read persisted queue/run fields only. They do not classify business content or add a replay endpoint where the subsystem has none.

- [ ] **Step 4: Render the adapters with the shared state card**

Change `_meeting_history_card(item)` to `_meeting_history_card(item, store)`, load its run and job, and pass `meeting_history_attention()` to `_history_attention_html()`. Change `_task_history_card(item)` to pass `task_history_attention(item)` to the same renderer. For both kinds, `manual` and `details` link to the existing detail URL; there is no unsafe synthetic retry POST.

- [ ] **Step 5: Run cross-kind and existing History tests**

Run: `.venv/bin/pytest tests/test_history_actions.py tests/test_audit_web.py -q -k 'history or meeting_history or task_history'`

Expected: PASS.

- [ ] **Step 6: Commit cross-kind support**

```bash
git add app/history_actions.py app/audit_web.py tests/test_history_actions.py tests/test_audit_web.py
git commit -m "feat: generalize actionable history states"
```

### Task 4: Make inline manager decisions atomic and idempotent

**Files:**
- Modify: `app/store.py:9668-9795`
- Modify: `app/audit_web.py:7226-7265`
- Modify: `app/audit_web.py:8015-8023`
- Modify: `tests/test_store.py`
- Modify: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing store tests**

```python
def test_actionable_attempt_decision_resolves_source_and_requeues_same_task_atomically(tmp_path: Path):
    store, source_id, task_id = actionable_failed_attempt(tmp_path)

    selected_id, task = store.record_actionable_attempt_decision(
        source_id,
        instruction="暂不处理当前事项；审批类事项通知实际申请人缺少的材料和下一步。",
    )

    assert task.id == task_id
    assert task.status == "pending"
    assert store.get_reply_attempt(source_id).send_status == "decision_selected"
    assert store.get_reply_attempt(selected_id).reviewer_feedback.endswith("下一步。")


def test_repeating_same_actionable_decision_is_idempotent(tmp_path: Path):
    store, source_id, _ = actionable_failed_attempt(tmp_path)
    first = store.record_actionable_attempt_decision(source_id, instruction="暂不处理")
    second = store.record_actionable_attempt_decision(source_id, instruction="暂不处理")

    assert second[0] == first[0]
    assert second[1].execution_generation == first[1].execution_generation
```

- [ ] **Step 2: Run store tests and verify RED**

Run: `.venv/bin/pytest tests/test_store.py -q -k 'actionable_attempt_decision'`

Expected: FAIL because `record_actionable_attempt_decision` does not exist.

- [ ] **Step 3: Implement one transactional store entry point**

Implement `record_actionable_attempt_decision(attempt_id, *, instruction)` by moving the transaction body shared with `record_reviewed_reply_rerun()` into one private in-connection helper. The method must:

```python
with self._connect() as db:
    db.execute("begin immediate")
    source = db.execute("select * from reply_attempts where id=?", (attempt_id,)).fetchone()
    if source is None:
        raise ValueError("attempt does not exist")
    if source["send_status"] not in {"failed", "needs_human"}:
        raise ValueError("attempt no longer requires a decision")
    selected_attempt_id, task = self._record_reviewed_reply_rerun_in_connection(
        db,
        source=source,
        reviewer_feedback=instruction.strip(),
    )
    db.execute(
        "update reply_attempts set send_status='decision_selected', send_error='', "
        "reviewer_feedback=?, reviewed_at=current_timestamp, updated_at=current_timestamp "
        "where id=? and send_status in ('failed','needs_human')",
        (instruction.strip(), attempt_id),
    )
    return selected_attempt_id, task
```

Before returning an existing selected attempt, match source attempt ID plus canonical instruction so a repeated POST returns the same task generation. Unknown external-effect reconciliation must continue to reject rotation through `_hold_generation_for_unknown_effects()`.

- [ ] **Step 4: Run store tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_store.py -q -k 'actionable_attempt_decision or reviewed_reply_rerun'`

Expected: PASS.

- [ ] **Step 5: Route History decisions through the atomic method**

Extend `handle_needs_human_decision_post()` to accept `return_to`, call `record_actionable_attempt_decision()`, and redirect through `_safe_action_return_to(return_to, attempt_id)`. Do not update the source attempt in a second transaction.

For the fixed `defer` action, submit this explicit instruction:

```text
暂不处理当前事项。审批类事项必须向实际申请人说明仍待处理、缺少的材料或事实，以及申请人下一步需要做什么；其他事项按当前业务上下文说明暂不处理的结果。
```

The instruction tells the agent what result to produce; it does not execute an external action in the web request.

- [ ] **Step 6: Write and run the route regression tests**

```python
def test_history_decision_redirects_back_to_history_and_reuses_task(tmp_path: Path):
    client, source_id, task_id = actionable_history_client(tmp_path)
    response = client.post(
        f"/attempts/{source_id}/human-decision?return_to=/",
        data={"instruction": "暂不处理"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert AutoReplyStore(tmp_path / "worker.sqlite3").get_reply_task(task_id).status == "pending"
```

Run: `.venv/bin/pytest tests/test_audit_web.py -q -k 'history_decision or needs_human_decision'`

Expected: PASS.

- [ ] **Step 7: Commit the decision path**

```bash
git add app/store.py app/audit_web.py tests/test_store.py tests/test_audit_web.py
git commit -m "fix: make history decisions idempotent"
```

### Task 5: Document, verify, deploy, and read back

**Files:**
- Modify: `docs/reply-worker-reliability.md`

- [ ] **Step 1: Run the focused regression suite**

Run: `.venv/bin/pytest tests/test_history_actions.py tests/test_audit_web.py tests/test_store.py -q`

Expected: PASS.

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/pytest -q`

Expected: PASS. If unrelated pre-existing failures remain, record their exact test names and keep them separate from this feature's focused green suite.

- [ ] **Step 3: Document runtime behavior**

Add a “History manager actions” section documenting:

```text
- needs_manager: reason, external-effect state, and choices appear in History and detail.
- automatic_recovery: no manager buttons; retry count and persisted available_at appear.
- terminal or reconciliation: no replay buttons; read-only result/status appears.
- retry and decisions reuse the existing reply_task identity and preserve external-action reconciliation.
```

- [ ] **Step 4: Commit documentation**

```bash
git add docs/reply-worker-reliability.md
git commit -m "docs: explain actionable history states"
```

- [ ] **Step 5: Restart the launchd service**

Run: `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`

Expected: the command succeeds and the prior service process is replaced.

- [ ] **Step 6: Verify new process, stable listener, and page content**

Run:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
lsof -nP -iTCP:8765 -sTCP:LISTEN
curl -sS http://127.0.0.1:8765/ | rg '状态：|原因：|外部副作用：|重试计划：|暂不处理|技术详情'
```

Expected: a new PID is running, one listener remains stable, and the live HTML contains the new state fields/actions.

- [ ] **Step 7: Verify resumability and backlog**

Query the existing store diagnostics for `processing` and `failed` reply tasks, unfinished agent runs, and pending reconciliation. Confirm no task created by History actions has a duplicate task identity or unknown external effect before reporting completion.
