# OA Event Coalescing and Reminder Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge DingTalk OA notifications and chat reminders into the same OA node work item, keep the scheduled OA scan as the sole review owner, and send confirmed review results back to real chat reminders.

**Architecture:** Add a small OA notification routing module for extracting and classifying case/node references. Persist `oa_notification_events` separately from reply tasks so a message without `taskId` can wait at case level. The scheduled scanner resolves the current node and adopts events; the worker sends an idempotent result reply after a provider-confirmed OA outcome.

**Tech Stack:** Python, SQLite, Pydantic message models, existing `AutoReplyStore`, DingTalk DWS client, pytest.

---

## File map

- Create `app/oa_notification_routing.py`: classify system OA notifications versus real chat reminders, normalize case/node keys, render result replies.
- Modify `app/store.py`: persist system/reminder events, adopt unresolved events into a node, and claim/send each reminder once.
- Modify `app/worker.py`: route OA system notifications to persistence only and invoke reminder-result delivery after a confirmed OA result.
- Modify `app/task_scanners.py`: pass the resolved node identity to the existing OA reply task and adopt pending reminder targets transactionally.
- Create `tests/test_oa_notification_routing.py`: pure classification, key, and result-text tests.
- Modify `tests/test_store.py`, `tests/test_worker.py`, and `tests/test_task_scanners.py`: regression coverage for coalescing, no standalone run, and one reply per reminder.
- Modify `docs/architecture.md` and `docs/runtime-mechanism.md`: document event ownership, node resolution, and reminder result delivery.
- Create `docs/superpowers/specs/2026-09-25-oa-event-coalescing-design.md`: approved design record.

### Task 1: Add pure OA notification routing rules

**Files:**
- Create `app/oa_notification_routing.py`
- Create `tests/test_oa_notification_routing.py`

- [ ] Write tests for these exact cases: system sender `OA审批` is context-only; `[Ding]申请人提醒您审批...` is a chat reminder; a message with an OA URL and no reminder wording is context-only; keys are `oa:<process>` and `oa:<process>:<task>`; result text contains the confirmed Chinese action and OA URL.
- [ ] Run `pytest -q tests/test_oa_notification_routing.py` and confirm the imports or behavior fail before implementation.
- [ ] Implement typed helpers `classify_oa_message`, `oa_case_key`, `oa_node_key`, and `render_oa_reminder_result` without reading provider state or sending anything.
- [ ] Run the focused test file and confirm it passes.

### Task 2: Persist reminder targets and node adoption

**Files:**
- Modify `app/store.py` schema initialization and migration sections
- Modify `tests/test_store.py`

- [x] Add `oa_notification_events` with unique `(channel, conversation_id, trigger_message_id)`, case/node identity, original message JSON, state, claimed result attempt, and timestamps.
- [x] Add store methods to record an event idempotently, list unresolved reminders by process, adopt them to a resolved task, claim one target for a result attempt, and mark sent/failed.
- [x] Write store tests proving two readers of the same message create one target, a missing task remains case-level, and adoption preserves the original conversation/message identity.
- [ ] Run the affected store tests and confirm the new schema works in a temporary SQLite database.

### Task 3: Route incoming OA messages without an independent review

**Files:**
- Modify `app/worker.py`
- Modify `tests/test_worker.py`

- [x] Add a producer branch before `_enqueue_reply_task`: identify `OA审批` system notifications and real `[Ding]...提醒您审批` reminders, extract `processInstanceId` and optional `taskId`, and record the appropriate event.
- [x] Ensure system notifications do not enqueue a normal reply task or Agent run.
- [x] Ensure a chat reminder records its original DingTalk conversation and message as a reminder event and is marked seen exactly once.
- [ ] Preserve existing non-OA message triage and existing OA URLs that already have a task ID.
- [x] Add regression tests proving reminder messages do not call the Agent runner and create one persisted event.
- [ ] Run the focused worker tests.

### Task 4: Resolve and adopt targets in the OA scheduled scan

**Files:**
- Modify `app/task_scanners.py`
- Modify `tests/test_task_scanners.py`

- [x] After the current-task resolver returns exactly one current task, call the store adoption method for the process instance and task ID.
- [ ] Make the scheduled OA reply task use `oa:<process>:<task>` even when an earlier notification had no task ID.
- [x] Leave zero-task and multi-task cases at case level and record them in the scanner cursor without creating an executable OA task.
- [ ] Add tests for one current task adoption, unresolved no-task retention, and multi-task retention.
- [ ] Run `pytest -q tests/test_task_scanners.py`.

### Task 5: Reply to chat reminders after confirmed OA results

**Files:**
- Modify `app/worker.py`
- Modify `app/store.py` if claim/send projection needs a small helper
- Modify `tests/test_worker.py`

- [x] After `_apply_orchestration_result` records a provider-confirmed OA result, enumerate reminder events for the same process/node.
- [x] Claim each target using the final OA attempt ID and an idempotency key before sending.
- [ ] Send a Markdown reply through the existing `ServiceMessageSender.send_dingtalk_reply_to_trigger_prepared` path, using the original conversation and trigger message from the stored JSON.
- [ ] Render only confirmed outcomes: approved, returned, rejected, commented/pending, or needs human decision. Technical failures keep the target pending and never claim an outcome.
- [x] Mark successful sends and failed sends separately; a retry must not send a successful target twice.
- [x] Add tests for one successful reply, duplicate retry suppression, and no reply when no provider-confirmed OA result exists.
- [ ] Run the focused worker and sender tests.

### Task 6: Document and verify the runtime contract

**Files:**
- Modify `docs/architecture.md`
- Modify `docs/runtime-mechanism.md`
- Modify `docs/agent-claims.md`

- [ ] Document that OA Case identity is `processInstanceId`, executable Node identity is `processInstanceId + taskId`, and Agent Session belongs to a run.
- [ ] Document that system OA notifications are context-only and real chat reminders receive confirmed result replies.
- [ ] Run all focused tests together: `pytest -q tests/test_oa_notification_routing.py tests/test_task_scanners.py tests/test_worker.py -k 'oa or approval or reminder'`.
- [ ] Run the relevant store and sender tests, inspect `git diff --check`, and commit the implementation as one focused change.

## Self-review checklist

- Every requirement in the approved design is covered by Tasks 1–6.
- No message without a resolved task can execute an OA action.
- Chat reminder delivery is idempotent and uses the original conversation/message target.
- System notifications do not create standalone Agent runs.
- Existing external OA action and applicant notification paths remain unchanged.
