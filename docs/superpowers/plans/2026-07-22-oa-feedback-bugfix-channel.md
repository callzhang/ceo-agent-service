# OA Pending And Feedback Bugfix Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build our own replacement for PR #2 and #3: proactive OA pending handling with a useful OA detail page, plus a restricted feedback-to-service-bugfix queue that never accepts arbitrary code-change requests.

**Architecture:** Reuse the existing DingTalk reply/OA attempt flow instead of adding a generic development agent. OA pending scan produces normal `reply_tasks` with synthetic `oa-pending:<processInstanceId>` trigger IDs, while the UI adds an OA-centric detail page and history table over existing `reply_attempts`. Feedback bugfix intake records only service-bug candidates from explicit feedback and leaves execution manual/confirmed; it never auto-runs arbitrary Codex development.

**Tech Stack:** Python, FastAPI audit web, SQLite via `AutoReplyStore`, pytest, existing `DwsClient`, existing `OaApprovalSpecHandler`.

---

## File Structure

- Modify: `app/store.py`
  - Add query helpers for OA attempt history by `oa_process_instance_id`.
  - Add a small `service_bugfix_candidates` table and store methods for restricted feedback intake.
- Modify: `app/worker.py`
  - Add proactive OA pending scan using existing `list_pending_oa_approvals`.
  - Keep OA execution boundary: comment/退回 may use only `processInstanceId`; approve/reject still require current-user `taskId`.
  - Add restricted feedback bugfix intake from negative feedback events or explicit bug feedback triggers only.
- Modify: `app/audit_web.py`
  - Add `/oa-approvals/{process_instance_id}` detail page.
  - Link OA attempts from attempt detail and history to the OA detail page.
  - Add service bugfix candidates page or section in History.
- Modify: `tests/test_worker.py`
  - Cover OA pending enqueue, dedupe, scan interval, comment-only target, and real approval target requirement.
  - Cover rejection of arbitrary code-change requests.
- Modify: `tests/test_audit_web.py`
  - Cover OA detail route, skill-style decision explanation, and history table.
- Modify: `README.md` and `.env.example`
  - Document OA scan env vars and bugfix intake boundary.

## Core Rules To Preserve

- OA pending scan may create a trigger; it must not execute approval directly.
- `退回`/comment action requires `processInstanceId`.
- `通过`/`拒绝`/approval action requires `processInstanceId + taskId` and current Derek ownership.
- DWS unavailable or authorization-needed is blocking; do not ask the agent to infer OA decisions without materials.
- Feedback bugfix intake is not a general "agent code" channel. It only creates candidates for this service's bugs caused by actual auto-reply feedback.
- No automatic arbitrary code edit, no generic `codex_dev_tasks`, no `--ignore-rules`, no `--disable hooks`.

### Task 1: Store OA History Query

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write the failing test**

Add this test near the existing OA metadata tests in `tests/test_audit_web.py`:

```python
def test_store_lists_oa_attempt_history_by_process_instance(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="OA审批",
        trigger_text="审批 A",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        draft_reply_text="请补充预算来源。",
        send_status="commented",
        oa_process_instance_id="proc-1",
        oa_task_id="",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1",
        oa_action="退回",
        oa_remark="请补充预算来源。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
    )
    second_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-2",
        trigger_sender="OA审批",
        trigger_text="审批 A updated",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="通过",
        draft_reply_text="材料完整。",
        send_status="skipped",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        oa_action="通过",
        oa_remark="材料完整。",
        oa_action_result_json='{}',
    )

    rows = store.list_oa_attempt_history("proc-1")

    assert [row.id for row in rows] == [second_id, first_id]
    assert rows[0].oa_action == "通过"
    assert rows[1].send_status == "commented"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_store_lists_oa_attempt_history_by_process_instance -q
```

Expected: FAIL with `AttributeError: 'AutoReplyStore' object has no attribute 'list_oa_attempt_history'`.

- [ ] **Step 3: Write minimal implementation**

Add this method to `AutoReplyStore` in `app/store.py` near other attempt list/get helpers:

```python
    def list_oa_attempt_history(
        self,
        process_instance_id: str,
        *,
        limit: int = 50,
    ) -> list[ReplyAttempt]:
        normalized = process_instance_id.strip()
        if not normalized:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from reply_attempts
                where action='oa_approval'
                  and oa_process_instance_id=?
                order by id desc
                limit ?
                """,
                (normalized, max(1, limit)),
            ).fetchall()
        return [ReplyAttempt.model_validate(dict(row)) for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_store_lists_oa_attempt_history_by_process_instance -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_audit_web.py
git commit -m "feat: add OA attempt history query"
```

### Task 2: OA Detail Page

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write the failing route test**

Add this test after `test_attempt_detail_renders_oa_metadata`:

```python
def test_oa_detail_page_shows_skill_decision_and_history(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    attempt_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-oa-1",
        trigger_sender="OA审批",
        trigger_text="[Ding]系统提醒您审批采购申请",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        draft_reply_text="请补充预算来源。",
        audit_summary="oa-approval skill 判断材料缺少预算来源，因此只评论补材料，不执行通过。",
        send_status="commented",
        oa_process_instance_id="proc-1",
        oa_task_id="",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1",
        oa_action="退回",
        oa_remark="请补充预算来源。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
    )
    client = TestClient(create_audit_app(db_path))

    response = client.get("/oa-approvals/proc-1")

    assert response.status_code == 200
    assert "OA 任务详情" in response.text
    assert "proc-1" in response.text
    assert "oa-approval skill 判断材料缺少预算来源" in response.text
    assert "请补充预算来源。" in response.text
    assert f"/attempts/{attempt_id}" in response.text
    assert "<table" in response.text
    assert "commented" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_oa_detail_page_shows_skill_decision_and_history -q
```

Expected: FAIL with HTTP 404.

- [ ] **Step 3: Add renderer and route**

In `app/audit_web.py`, add this renderer near `_oa_metadata_card`:

```python
def _oa_skill_decision_text(attempt: ReplyAttempt) -> str:
    action = attempt.oa_action.strip() or attempt.codex_reason.strip() or "未给出动作"
    remark = attempt.oa_remark.strip() or attempt.draft_reply_text.strip()
    status = attempt.send_status.strip() or "unknown"
    if action == "退回":
        target = "只评论/退回补材料"
    elif attempt.oa_task_id.strip():
        target = "可执行审批动作"
    else:
        target = "缺少当前审批 taskId，不能执行审批动作"
    parts = [
        f"oa-approval skill 动作：{action}",
        f"处理边界：{target}",
        f"处理状态：{status}",
    ]
    if remark:
        parts.append(f"评论/理由：{remark}")
    if attempt.audit_summary.strip():
        parts.append(f"审阅摘要：{attempt.audit_summary.strip()}")
    if attempt.send_error.strip():
        parts.append(f"错误：{attempt.send_error.strip()}")
    return "\n".join(parts)


def _oa_attempt_history_table(attempts: list[ReplyAttempt]) -> str:
    if not attempts:
        return "<p class=\"muted\">没有历史处理记录。</p>"
    rows = []
    for attempt in attempts:
        rows.append(
            "<tr>"
            f"<td><a href=\"/attempts/{attempt.id}\">#{attempt.id}</a></td>"
            f"<td>{escape(_format_local_time(attempt.created_at))}</td>"
            f"<td>{escape(attempt.oa_action or attempt.codex_reason)}</td>"
            f"<td>{escape(attempt.send_status)}</td>"
            f"<td>{escape(attempt.oa_remark or attempt.draft_reply_text)}</td>"
            f"<td>{escape(attempt.oa_task_id)}</td>"
            f"<td>{escape(attempt.send_error)}</td>"
            "</tr>"
        )
    return (
        "<table class=\"attempt-table oa-history-table\">"
        "<thead><tr><th>Attempt</th><th>时间</th><th>动作</th>"
        "<th>状态</th><th>评论/理由</th><th>taskId</th><th>错误</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_oa_approval_detail(store: AutoReplyStore, process_instance_id: str) -> str:
    attempts = store.list_oa_attempt_history(process_instance_id)
    latest = attempts[0] if attempts else None
    if latest is None:
        body = (
            "<section class=\"card\"><h1>OA 任务详情</h1>"
            f"<p class=\"muted\">没有找到 processInstanceId: {escape(process_instance_id)}</p>"
            "</section>"
        )
    else:
        body = (
            "<section class=\"card\"><h1>OA 任务详情</h1>"
            f"<p><strong>processInstanceId:</strong> {escape(process_instance_id)}</p>"
            f"<p><strong>OA URL:</strong> {escape(latest.oa_url)}</p>"
            "<h2>oa-approval 处理意见</h2>"
            f"<pre class=\"mini-pre\">{escape(_oa_skill_decision_text(latest))}</pre>"
            "<h2>历史处理结果</h2>"
            f"{_oa_attempt_history_table(attempts)}</section>"
        )
    return render_page("OA 任务详情", body, active_nav="history")
```

In `create_audit_app`, add this route next to attempt detail routes:

```python
    @app.get("/oa-approvals/{process_instance_id}", response_class=HTMLResponse)
    def oa_approval_detail(process_instance_id: str) -> HTMLResponse:
        store = AutoReplyStore(db_path)
        return HTMLResponse(render_oa_approval_detail(store, process_instance_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_oa_detail_page_shows_skill_decision_and_history -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: add OA approval detail page"
```

### Task 3: Link Attempt Detail To OA Detail

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing test**

Extend `test_attempt_detail_renders_oa_metadata` with:

```python
    assert 'href="/oa-approvals/proc-1"' in html
    assert "查看 OA 任务详情" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_attempt_detail_renders_oa_metadata -q
```

Expected: FAIL because no OA detail link exists.

- [ ] **Step 3: Update `_oa_metadata_card`**

Change the start of `_oa_metadata_card` rows building in `app/audit_web.py` to include a link after process instance:

```python
    process_instance = attempt.oa_process_instance_id.strip()
    detail_link = (
        f'<a href="/oa-approvals/{escape(process_instance)}">查看 OA 任务详情</a>'
        if process_instance
        else ""
    )
    rows = "".join(
        f"<div class=\"muted\">{escape(label)}</div><div>{value}</div>"
        for label, value in (
            ("process instance", escape(attempt.oa_process_instance_id)),
            ("detail", detail_link),
            ("task id", escape(attempt.oa_task_id)),
            ("url", escape(attempt.oa_url)),
            ("action", escape(attempt.oa_action)),
            ("remark", escape(attempt.oa_remark)),
        )
    )
```

Keep the rest of `_oa_metadata_card` unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_attempt_detail_renders_oa_metadata -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: link OA attempts to OA detail"
```

### Task 4: Proactive OA Pending Scan

**Files:**
- Modify: `app/worker.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing tests**

Add these tests near existing OA tests in `tests/test_worker.py`:

```python
def test_pending_oa_approval_scan_enqueues_and_comments(tmp_path: Path, monkeypatch):
    dws = FakeDws([], {})
    dws.pending_oa_approvals = [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="宋述提交的背调结果说明",
            process_name="背调结果说明",
        )
    ]
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        dry_run=False,
        oa_approval_handler=ReturnOaApprovalHandler(),
    )

    queued = worker.produce_once()
    task = worker.store.claim_reply_tasks(limit=1)[0]

    assert queued == 1
    assert task.conversation_id == "oa-pending"
    assert task.trigger_message_id == "oa-pending:proc-1"
    assert "processInstanceId: proc-1" in task.trigger_text


def test_pending_oa_approval_scan_respects_interval(tmp_path: Path, monkeypatch):
    dws = FakeDws([], {})
    dws.pending_oa_approvals = [
        DwsOaApprovalCandidate(process_instance_id="proc-1", title="审批", process_name="审批")
    ]
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.set_service_state(
        worker_module.OA_PENDING_CHECKED_AT_STATE_KEY,
        fixed_worker_now().isoformat(),
    )

    queued = worker.produce_once()

    assert queued == 0
    assert worker.store.claim_reply_tasks(limit=1) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_pending_oa_approval_scan_enqueues_and_comments tests/test_worker.py::test_pending_oa_approval_scan_respects_interval -q
```

Expected: FAIL because constants/methods do not exist.

- [ ] **Step 3: Add worker implementation**

In `app/worker.py`, add constants near retry constants:

```python
OA_PENDING_SCAN_INTERVAL = env_duration(
    "CEO_OA_PENDING_SCAN_INTERVAL",
    timedelta(seconds=30),
)
OA_PENDING_CONVERSATION_ID = "oa-pending"
OA_PENDING_CONVERSATION_TITLE = "OA 待审批"
OA_PENDING_CHECKED_AT_STATE_KEY = "oa_pending_checked_at"
```

At the end of `produce_once`, before returning `queued_tasks`, add:

```python
        if queued_tasks == 0:
            queued_tasks += self._produce_pending_oa_approvals(max_tasks=max_tasks)
```

Add these methods to `DingTalkAutoReplyWorker`:

```python
    def _produce_pending_oa_approvals(self, max_tasks: int | None = None) -> int:
        if max_tasks == 0:
            return 0
        list_pending = getattr(self.dws, "list_pending_oa_approvals", None)
        if list_pending is None:
            return 0
        now = self._now()
        if not self._pending_oa_scan_due(now):
            return 0
        candidates = self._call_dws(
            "list_pending_oa_approvals",
            lambda: list_pending(page=1, size=30),
            default=[],
        )
        self.store.set_service_state(
            OA_PENDING_CHECKED_AT_STATE_KEY,
            now.astimezone(timezone.utc).isoformat(),
        )
        if not candidates:
            return 0
        conversation = DingTalkConversation(
            open_conversation_id=OA_PENDING_CONVERSATION_ID,
            title=OA_PENDING_CONVERSATION_TITLE,
            single_chat=False,
            unread_point=0,
        )
        self.store.upsert_conversation(
            conversation_id=conversation.open_conversation_id,
            title=conversation.title,
            single_chat=conversation.single_chat,
            codex_session_id=None,
        )
        queued = 0
        for candidate in candidates:
            if max_tasks is not None and queued >= max_tasks:
                break
            message = self._pending_oa_approval_message(candidate, now)
            if self.store.has_seen(message.open_message_id):
                continue
            if self._enqueue_reply_task(
                conversation,
                message,
                context_messages=[],
                replace_pending_single_chat=False,
            ):
                queued += 1
        return queued

    def _pending_oa_scan_due(self, now: datetime) -> bool:
        checked_at = self.store.get_service_state(OA_PENDING_CHECKED_AT_STATE_KEY)
        if not checked_at:
            return True
        last_checked = self._parse_service_state_datetime(checked_at)
        if last_checked is None:
            return True
        return now.astimezone(timezone.utc) - last_checked.astimezone(timezone.utc) >= OA_PENDING_SCAN_INTERVAL

    @staticmethod
    def _pending_oa_approval_message(candidate: Any, now: datetime) -> DingTalkMessage:
        process_instance_id = str(candidate.process_instance_id).strip()
        title = str(candidate.title or candidate.process_name or "OA 待审批").strip()
        process_name = str(candidate.process_name or "").strip()
        oa_url = (
            "https://aflow.dingtalk.com/dingtalk/pc/query/pchomepage.htm"
            f"?procInstId={quote(process_instance_id)}&swfrom=oa"
        )
        content_lines = [
            f"[Ding]系统提醒您审批{title}",
            f"processInstanceId: {process_instance_id}",
        ]
        if process_name:
            content_lines.append(f"processName: {process_name}")
        content_lines.append(oa_url)
        return DingTalkMessage(
            open_conversation_id=OA_PENDING_CONVERSATION_ID,
            open_message_id=f"oa-pending:{process_instance_id}",
            conversation_title=OA_PENDING_CONVERSATION_TITLE,
            single_chat=False,
            sender_name="OA审批",
            create_time=now.astimezone(DINGTALK_MESSAGE_TIME_ZONE).strftime(DINGTALK_TIME_FORMAT),
            content="\n".join(content_lines),
            raw_payload={
                "source": "pending_oa_approval_scan",
                "processInstanceId": process_instance_id,
                "processName": process_name,
                "title": title,
            },
        )
```

In `_queued_trigger_is_still_actionable`, add:

```python
        if trigger.open_message_id.startswith(f"{OA_PENDING_CONVERSATION_ID}:"):
            return True
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_pending_oa_approval_scan_enqueues_and_comments tests/test_worker.py::test_pending_oa_approval_scan_respects_interval -q
```

Expected: PASS.

- [ ] **Step 5: Document config**

Add to `.env.example`:

```env
CEO_OA_PENDING_SCAN_INTERVAL=30s
```

Add to `README.md` service loop section:

```markdown
- OA pending scan：按 `CEO_OA_PENDING_SCAN_INTERVAL` 主动检查钉钉 OA 待审批列表，默认 `30s`；新审批会以 `oa-pending:<processInstanceId>` 合成触发消息进入现有 OA 审阅/评论流程。
```

- [ ] **Step 6: Commit**

```bash
git add app/worker.py tests/test_worker.py .env.example README.md
git commit -m "feat: proactively enqueue pending OA approvals"
```

### Task 5: OA Comment-Only Boundary

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing test**

Add this test near OA approval tests:

```python
def test_oa_approval_comment_only_requires_process_target(tmp_path: Path, monkeypatch):
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的录用申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        dry_run=False,
        oa_approval_handler=MissingTargetOaApprovalHandler(),
    )

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.send_status == "commented"
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == ""
    assert dws.oa_approval_comments == [("proc-1", "材料不足，暂不执行审批动作。")]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_oa_approval_comment_only_requires_process_target -q
```

Expected: FAIL because existing code requires both process and task target.

- [ ] **Step 3: Split target requirements in worker**

In `_handle_oa_approval_if_actionable`, replace the `has_approval_target` block with:

```python
            has_process_target = bool(effective_oa_process_instance_id.strip())
            has_task_target = bool(has_process_target and effective_oa_task_id.strip())
            if result.oa_action == "退回":
                if has_process_target:
                    try:
                        action_result = self.dws.comment_oa_approval(
                            effective_oa_process_instance_id,
                            result.oa_remark,
                        )
                        send_status = "commented"
                    except Exception as exc:
                        send_status = "failed"
                        send_error = str(exc)
                else:
                    send_status = "skipped"
                    send_error = target_error or "missing_oa_approval_target"
            else:
                if has_task_target:
                    try:
                        action_result = self.dws.execute_oa_approval_action(
                            effective_oa_process_instance_id,
                            effective_oa_task_id,
                            result.oa_action,
                            result.oa_remark,
                        )
                        send_status = "skipped"
                    except Exception as exc:
                        send_status = "failed"
                        send_error = str(exc)
                else:
                    send_status = "skipped"
                    send_error = target_error or "missing_oa_approval_target"
```

- [ ] **Step 4: Run OA tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py -k "oa_approval or pending_oa" -q
```

Expected: all selected OA tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "fix: allow OA comments with process target only"
```

### Task 6: Restricted Feedback Bugfix Candidate Store

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing test**

Add this test to `tests/test_worker.py`:

```python
def test_store_records_service_bugfix_candidate_from_feedback(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    candidate_id = store.create_service_bugfix_candidate(
        source_kind="feedback",
        source_id="attempt:3144",
        conversation_id="cid-1",
        conversation_title="用户反馈",
        trigger_message_id="msg-feedback-1",
        summary="反馈文案太生硬，需要优化本服务反馈提示。",
    )
    candidates = store.list_service_bugfix_candidates(statuses=("pending",), limit=10)

    assert candidate_id > 0
    assert len(candidates) == 1
    assert candidates[0]["summary"] == "反馈文案太生硬，需要优化本服务反馈提示。"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_store_records_service_bugfix_candidate_from_feedback -q
```

Expected: FAIL because store methods do not exist.

- [ ] **Step 3: Add table and methods**

In `app/store.py`, add table creation in `_ensure_schema`:

```sql
                create table if not exists service_bugfix_candidates (
                    id integer primary key autoincrement,
                    source_kind text not null,
                    source_id text not null,
                    conversation_id text not null default '',
                    conversation_title text not null default '',
                    trigger_message_id text not null default '',
                    summary text not null,
                    status text not null default 'pending',
                    error text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    finished_at text not null default '',
                    unique(source_kind, source_id)
                );
                create index if not exists idx_service_bugfix_candidates_status
                    on service_bugfix_candidates(status, id);
```

Add methods to `AutoReplyStore`:

```python
    def create_service_bugfix_candidate(
        self,
        *,
        source_kind: str,
        source_id: str,
        conversation_id: str,
        conversation_title: str,
        trigger_message_id: str,
        summary: str,
    ) -> int:
        with self._connect() as db:
            db.execute(
                """
                insert into service_bugfix_candidates (
                    source_kind, source_id, conversation_id, conversation_title,
                    trigger_message_id, summary
                )
                values (?, ?, ?, ?, ?, ?)
                on conflict(source_kind, source_id) do update set
                    conversation_id=excluded.conversation_id,
                    conversation_title=excluded.conversation_title,
                    trigger_message_id=excluded.trigger_message_id,
                    summary=excluded.summary,
                    updated_at=current_timestamp
                """,
                (
                    source_kind.strip(),
                    source_id.strip(),
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    summary.strip(),
                ),
            )
            row = db.execute(
                """
                select id from service_bugfix_candidates
                where source_kind=? and source_id=?
                """,
                (source_kind.strip(), source_id.strip()),
            ).fetchone()
            return int(row["id"])

    def list_service_bugfix_candidates(
        self,
        *,
        statuses: tuple[str, ...] = ("pending",),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from service_bugfix_candidates
                where status in ({placeholders})
                order by id desc
                limit ?
                """,
                (*statuses, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_store_records_service_bugfix_candidate_from_feedback -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_worker.py
git commit -m "feat: store service bugfix candidates"
```

### Task 7: Reject Arbitrary Development Requests

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing tests**

Add:

```python
def test_feedback_bugfix_intake_rejects_arbitrary_code_request(tmp_path: Path, monkeypatch):
    trigger = principal_message(
        "Mina Agent，用codex执行这个任务。开发一个新的飞书通道",
        message_id="dev-anything-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()

    assert queued == 0
    assert worker.store.claim_reply_tasks(limit=1) == []
    assert worker.store.list_service_bugfix_candidates(statuses=("pending",), limit=10) == []


def test_feedback_bugfix_intake_accepts_service_bug_feedback(tmp_path: Path, monkeypatch):
    trigger = principal_message(
        "反馈：CEO agent 的反馈评价文案太生硬，这是本服务 bug，请修复。",
        message_id="bug-feedback-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()
    candidates = worker.store.list_service_bugfix_candidates(statuses=("pending",), limit=10)

    assert queued == 1
    assert len(candidates) == 1
    assert "反馈评价文案太生硬" in candidates[0]["summary"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_feedback_bugfix_intake_rejects_arbitrary_code_request tests/test_worker.py::test_feedback_bugfix_intake_accepts_service_bug_feedback -q
```

Expected: FAIL because worker has no service bugfix intake.

- [ ] **Step 3: Add narrowly scoped intake**

In `app/worker.py`, add helpers:

```python
SERVICE_BUG_FEEDBACK_MARKERS = (
    "本服务 bug",
    "CEO agent",
    "CEO 服务",
    "自动回复",
    "反馈文案",
    "history 页面",
    "attempt",
)
ARBITRARY_DEV_MARKERS = (
    "用codex执行这个任务",
    "用 codex 执行这个任务",
    "开发一个",
    "新增一个",
    "写一个",
)


def _looks_like_service_bug_feedback(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return (
        ("反馈" in normalized or "bug" in normalized.lower() or "修复" in normalized)
        and any(marker in normalized for marker in SERVICE_BUG_FEEDBACK_MARKERS)
    )


def _looks_like_arbitrary_dev_request(text: str) -> bool:
    return any(marker in text for marker in ARBITRARY_DEV_MARKERS)
```

In `produce_once`, before normal candidate reply enqueue for principal messages, add a call shaped like:

```python
            queued_tasks += self._produce_service_bugfix_candidates(
                conversation,
                candidate_source_messages,
                max_tasks=None if max_tasks is None else max(0, max_tasks - queued_tasks),
            )
```

Add method:

```python
    def _produce_service_bugfix_candidates(
        self,
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
        *,
        max_tasks: int | None = None,
    ) -> int:
        queued = 0
        for message in messages:
            if max_tasks is not None and queued >= max_tasks:
                break
            if self.store.has_seen(message.open_message_id):
                continue
            if _looks_like_arbitrary_dev_request(message.content) and not _looks_like_service_bug_feedback(message.content):
                continue
            if not _looks_like_service_bug_feedback(message.content):
                continue
            self.store.create_service_bugfix_candidate(
                source_kind="dingtalk_feedback",
                source_id=f"{conversation.open_conversation_id}:{message.open_message_id}",
                conversation_id=conversation.open_conversation_id,
                conversation_title=conversation.title,
                trigger_message_id=message.open_message_id,
                summary=message.content.strip(),
            )
            self._mark_seen([message])
            queued += 1
        return queued
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_feedback_bugfix_intake_rejects_arbitrary_code_request tests/test_worker.py::test_feedback_bugfix_intake_accepts_service_bug_feedback -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: restrict feedback bugfix intake to service bugs"
```

### Task 8: Bugfix Candidate UI

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_history_shows_service_bugfix_candidates(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.create_service_bugfix_candidate(
        source_kind="dingtalk_feedback",
        source_id="cid-1:msg-1",
        conversation_id="cid-1",
        conversation_title="用户反馈群",
        trigger_message_id="msg-1",
        summary="反馈评价文案太生硬，这是本服务 bug，请修复。",
    )

    html = render_attempt_list(store)

    assert "服务 bug 修复候选" in html
    assert "反馈评价文案太生硬" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_history_shows_service_bugfix_candidates -q
```

Expected: FAIL because History does not render candidates.

- [ ] **Step 3: Add renderer**

In `app/audit_web.py`, add:

```python
def _service_bugfix_candidates_card(store: AutoReplyStore) -> str:
    candidates = store.list_service_bugfix_candidates(statuses=("pending",), limit=20)
    if not candidates:
        return ""
    rows = []
    for candidate in candidates:
        rows.append(
            "<tr>"
            f"<td>#{int(candidate['id'])}</td>"
            f"<td>{escape(candidate['conversation_title'])}</td>"
            f"<td>{escape(candidate['summary'])}</td>"
            f"<td>{escape(_format_local_time(candidate['created_at']))}</td>"
            "</tr>"
        )
    return (
        "<section class=\"card\"><h2>服务 bug 修复候选</h2>"
        "<table class=\"attempt-table\"><thead><tr><th>ID</th><th>来源</th>"
        "<th>反馈</th><th>时间</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )
```

In `render_attempt_list`, append this card near backlog cards:

```python
    bugfix_candidates_html = _service_bugfix_candidates_card(store)
```

and include `bugfix_candidates_html` in the returned body before the main attempt table.

- [ ] **Step 4: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_history_shows_service_bugfix_candidates -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: show service bugfix candidates in history"
```

### Task 9: Focused Regression Suite And Service Restart

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py -k "oa_approval or pending_oa or service_bugfix" -q
.venv/bin/python -m pytest tests/test_audit_web.py -k "oa_detail or oa_metadata or service_bugfix" -q
```

Expected: all selected tests PASS.

- [ ] **Step 2: Run broader affected tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py tests/test_audit_web.py tests/test_cli.py -q
```

Expected: PASS, except if the existing internal-personnel baseline failures are still present on main. If those two failures remain, document them in the final PR summary with exact test names and prove they fail on `origin/main`.

- [ ] **Step 3: Document boundary**

Add to `README.md`:

```markdown
### Service bugfix feedback boundary

Feedback-triggered development intake is intentionally narrow. It may create a local candidate only when the feedback describes a bug in this CEO agent service, such as auto-reply wording, History UI behavior, attempt handling, memory write behavior, OA processing, or feedback gating. It must not accept arbitrary DingTalk requests to change unrelated repositories, create business artifacts, or run a generic development agent.
```

- [ ] **Step 4: Commit docs**

```bash
git add README.md
git commit -m "docs: document service bugfix intake boundary"
```

- [ ] **Step 5: Restart service after runtime changes**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: service shows a fresh running `pid`.

- [ ] **Step 6: Verify backlog**

Run:

```bash
sqlite3 data/auto-reply.sqlite3 "select 'reply_tasks_failed_processing', count(*) from reply_tasks where status in ('failed','processing');"
sqlite3 data/auto-reply.sqlite3 "select 'work_summary_inputs_failed_processing', count(*) from work_summary_inputs where status in ('failed','processing');"
```

Expected: both counts are `0`, or final report lists exact remaining rows and why they are unrelated/unrecoverable.

## Self-Review

Spec coverage:
- #2 self-developed: covered by proactive OA pending scan, comment-only boundary, OA detail page, and history table tasks.
- #3 self-developed: covered by restricted service bugfix feedback intake and explicit rejection of arbitrary code-change tasks.
- OA approval skill display: covered by `_oa_skill_decision_text` and OA detail page.
- Historical processing table: covered by `list_oa_attempt_history` and `_oa_attempt_history_table`.

Placeholder scan:
- No `TBD`, `TODO`, `implement later`, or "similar to" placeholders remain.

Type consistency:
- `ReplyAttempt`, `AutoReplyStore`, `DingTalkAutoReplyWorker`, and existing test helpers are used consistently.
- New store methods return either `ReplyAttempt` models or simple dict rows as declared in tests.
