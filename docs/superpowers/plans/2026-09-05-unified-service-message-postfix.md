# Unified Service Message Postfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Route every service-owned DingTalk and WeChat human-facing text delivery through one immutable postfix boundary that applies the signature and configured feedback pair exactly once.

**Architecture:** Add an outbound-postfix persistence/policy layer and a ServiceMessageSender facade. Each logical delivery receives a stable delivery key; preparation atomically persists the final body and feedback token before provider dispatch. DingTalk and WeChat adapters only receive PreparedOutboundMessage, while an architecture test forbids new first-party direct calls to raw provider send methods.

**Tech Stack:** Python 3, SQLite migrations in AutoReplyStore, Pydantic contracts, pytest, Ruff, local launchd service.

---

## File map

- Create: app/outbound_postfix.py — immutable channel-neutral contracts and deterministic postfix composition.
- Create: app/service_message_sender.py — only first-party facade that prepares/persists outgoing text and calls DingTalk/WeChat transport adapters.
- Modify: app/store.py — schema migration plus atomic lookup-or-create methods for final postfix bodies.
- Modify: app/consumer_agent.py, app/audit_agent.py, app/agent_turn_runner.py — prepare Agent message deliveries before Audit and retain final sent text.
- Modify: app/follow_up.py, app/meeting_alignment_delivery.py, app/weekly_okr_report.py, app/cli.py, app/feedback_spike.py — replace direct DingTalk sends with facade calls.
- Modify: app/wechat/consumer.py, app/wechat/accessibility.py, app/wechat/service.py, app/wechat/models.py — persist and dispatch the prepared WeChat body.
- Create: tests/test_outbound_postfix.py, tests/test_service_message_sender.py, tests/test_message_send_architecture.py.
- Modify focused existing test modules: tests/test_consumer_agent.py, tests/test_agent_turn_runner.py, tests/test_follow_up.py, tests/test_meeting_alignment_delivery.py, tests/test_weekly_okr_report.py, tests/test_cli.py, tests/test_feedback_spike.py, tests/wechat/test_send_mode.py, tests/wechat/test_accessibility.py, tests/test_store.py.

### Task 1: Define the immutable postfix contract and persistence record

**Files:**

- Create: tests/test_outbound_postfix.py
- Create: app/outbound_postfix.py
- Modify: app/store.py
- Test: tests/test_store.py

- [ ] **Step 1: Write failing policy and store tests**

~~~python
def test_prepare_outbound_message_adds_one_signature_and_feedback_pair(monkeypatch):
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.test")
    prepared = prepare_outbound_message(
        channel="dingtalk", delivery_key="task:1:proposal:0",
        body="已收到", original_text="请确认",
    )
    assert prepared.final_body.startswith("已收到（by明哥分身）")
    assert prepared.final_body.count("/api/dingtalk-feedback-spike") == 2
    assert prepared.feedback_token


def test_store_reuses_final_body_and_token_for_same_delivery_key(store, monkeypatch):
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.test")
    first = store.prepare_outbound_postfix("wechat", "delivery:21", "你好", "触发")
    second = store.prepare_outbound_postfix("wechat", "delivery:21", "被重试的候选", "触发")
    assert second == first
    assert second.final_body.count("/api/dingtalk-feedback-spike") == 2
~~~

- [ ] **Step 2: Run test to verify RED**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_outbound_postfix.py tests/test_store.py -q -k 'outbound_postfix or reuses_final_body'

Expected: FAIL because the outbound-postfix module and store method do not exist.

- [ ] **Step 3: Add minimal implementation**

~~~python
@dataclass(frozen=True)
class PreparedOutboundMessage:
    channel: Literal["dingtalk", "wechat"]
    delivery_key: str
    final_body: str
    feedback_token: str
    postfix_version: str = "v1"


def compose_postfix(*, channel: str, delivery_key: str, body: str,
                    original_text: str, feedback_base_url: str) -> PreparedOutboundMessage:
    token = stable_feedback_token(channel, delivery_key)
    prepared = prepare_outgoing_reply_text(
        reply_text=body, original_text=original_text,
        feedback_base_url=feedback_base_url, feedback_token=token,
    )
    return PreparedOutboundMessage(channel, delivery_key, prepared.text,
                                   prepared.feedback_token)
~~~

Add an outbound_postfixes table with channel, delivery_key, final_body, feedback_token, postfix_version, created_at, and a unique channel/delivery_key. Implement prepare_outbound_postfix inside one BEGIN IMMEDIATE transaction: return the stored record if it exists; otherwise compose and insert it, then return the inserted record. Reject empty body/delivery keys and channels other than DingTalk or WeChat.

- [ ] **Step 4: Run test to verify GREEN**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_outbound_postfix.py tests/test_store.py -q -k 'outbound_postfix or reuses_final_body'

Expected: PASS. Confirm a disabled feedback URL still yields one signature, an empty token, and no callback pair.

- [ ] **Step 5: Commit**

~~~bash
git add app/outbound_postfix.py app/store.py tests/test_outbound_postfix.py tests/test_store.py
git commit -m "feat: persist unified outbound message postfixes"
~~~

### Task 2: Introduce the only first-party text sender facade

**Files:**

- Create: tests/test_service_message_sender.py
- Create: app/service_message_sender.py

- [ ] **Step 1: Write failing facade tests**

~~~python
def test_dingtalk_facade_only_dispatches_prepared_body(store, fake_dws):
    sender = ServiceMessageSender(store=store, dingtalk=fake_dws)
    receipt = sender.send_dingtalk(
        delivery_key="follow-up:revision-1", body="请确认", original_text="原消息",
        conversation_id="cid-1",
    )
    assert fake_dws.sent_text == receipt.message.final_body
    assert fake_dws.sent_text.count("/api/dingtalk-feedback-spike") == 2


def test_wechat_facade_reuses_prepared_body(store, fake_wechat):
    sender = ServiceMessageSender(store=store, wechat=fake_wechat)
    first = sender.send_wechat(delivery_key="delivery:7", body="你好", original_text="原文", scope=scope)
    second = sender.send_wechat(delivery_key="delivery:7", body="变化的正文", original_text="原文", scope=scope)
    assert second.message.final_body == first.message.final_body
~~~

- [ ] **Step 2: Run test to verify RED**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_service_message_sender.py -q

Expected: FAIL because ServiceMessageSender does not exist.

- [ ] **Step 3: Write minimal implementation**

~~~python
class ServiceMessageSender:
    def prepare(self, *, channel: Channel, delivery_key: str, body: str,
                original_text: str = "") -> PreparedOutboundMessage:
        return self.store.prepare_outbound_postfix(channel, delivery_key, body, original_text)

    def send_dingtalk(self, *, delivery_key: str, body: str, original_text: str,
                      conversation_id: str | None, **target) -> SendReceipt:
        message = self.prepare(channel="dingtalk", delivery_key=delivery_key,
                               body=body, original_text=original_text)
        result = self._dingtalk_transport.send(message.final_body, conversation_id, **target)
        return SendReceipt(message=message, provider_result=result)
~~~

Implement a parallel send_wechat_prepared path whose public input is a PreparedOutboundMessage; it passes final_body to the existing IPC runner. Do not put provider command construction or business classification in the facade.

- [ ] **Step 4: Run test to verify GREEN**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_outbound_postfix.py tests/test_service_message_sender.py tests/test_store.py -q -k 'outbound_postfix or service_message_sender or reuses_final_body'

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add app/service_message_sender.py tests/test_service_message_sender.py
git commit -m "feat: add unified service message sender"
~~~

### Task 3: Move Agent DingTalk actions onto final prepared text before Audit

**Files:**

- Modify: tests/test_consumer_agent.py, tests/test_agent_turn_runner.py
- Modify: app/consumer_agent.py, app/audit_agent.py, app/agent_turn_runner.py

- [ ] **Step 1: Write failing typed, legacy, and receipt tests**

~~~python
@pytest.mark.parametrize("operation,payload", [
    ("send_direct_message", {"content": "确认"}),
    ("messages-reply", {"reply_text": "确认"}),
    ("chat message send", {"argv": ["dws", "chat", "message", "send", "--text", "确认"]}),
])
def test_consumer_audit_candidate_contains_final_postfixed_body(
    store, task, context, operation, payload
):
    result = run_consumer_then_capture_audit_candidate(
        store=store, task=task, context=context, operation=operation, payload=payload
    )
    assert final_text(result).count("/api/dingtalk-feedback-spike") == 2


def test_agent_delivery_receipt_records_the_prepared_final_body(store, task, context):
    receipt = execute_completed_reviewed_dingtalk_write(
        store=store, task=task, context=context, body="确认"
    )
    assert receipt.reply_text == stored_postfix.final_body
~~~

- [ ] **Step 2: Run test to verify RED**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_consumer_agent.py tests/test_agent_turn_runner.py -q -k 'final_postfixed_body or prepared_final_body'

Expected: FAIL because Agent paths do not use the stable sender delivery key or prepared record.

- [ ] **Step 3: Write minimal implementation**

Derive Agent delivery keys from immutable task id, execution generation, proposal revision, and action index. Normalize content for typed and legacy DingTalk actions, call ServiceMessageSender.prepare, and write final_body back into the proposal before Audit starts. AuditAgentRunner constructs provider argv only from that final proposal body. AgentTurnRunner records the same body in sent_replies and never appends a second postfix.

- [ ] **Step 4: Run test to verify GREEN**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_consumer_agent.py tests/test_agent_turn_runner.py tests/test_worker.py -q -k 'feedback or postfix or sent_reply_projection'

Expected: PASS, including redaction and malformed-feedback-pair tests.

- [ ] **Step 5: Commit**

~~~bash
git add app/consumer_agent.py app/audit_agent.py app/agent_turn_runner.py tests/test_consumer_agent.py tests/test_agent_turn_runner.py tests/test_worker.py
git commit -m "refactor: prepare agent messages through unified postfix"
~~~

### Task 4: Route every non-Agent DingTalk sender through the facade

**Files:**

- Modify: tests/test_follow_up.py, tests/test_meeting_alignment_delivery.py, tests/test_weekly_okr_report.py, tests/test_cli.py, tests/test_feedback_spike.py
- Modify: app/follow_up.py, app/meeting_alignment_delivery.py, app/weekly_okr_report.py, app/cli.py, app/feedback_spike.py
- Modify: app/dws_client.py only to make raw provider functions adapter-private where feasible; do not alter its wire command semantics.

- [ ] **Step 1: Write failing behavior tests for each known sender**

~~~python
def test_follow_up_retry_reuses_one_postfixed_body(store, fake_dws):
    run_due_follow_ups(store, fake_dws)
    run_due_follow_ups(store, fake_dws)
    assert fake_dws.calls[0].text == fake_dws.calls[1].text


def test_meeting_and_weekly_group_messages_have_feedback_pair(fake_dws, store):
    meeting = deliver_meeting_alignment(store=store, dws=fake_dws, delivery=meeting_delivery())
    weekly = WeeklyOkrReporter(fake_dws, store=store).send_group_summary(
        conversation_id="cid-1", title="周报", text="进展"
    )
    assert meeting.sent_text.count("/api/dingtalk-feedback-spike") == 2
    assert weekly.sent_text.count("/api/dingtalk-feedback-spike") == 2
~~~

- [ ] **Step 2: Run test to verify RED**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_follow_up.py tests/test_meeting_alignment_delivery.py tests/test_weekly_okr_report.py tests/test_cli.py tests/test_feedback_spike.py -q -k 'postfix or feedback_pair or retry_reuses'

Expected: FAIL because meeting and weekly paths only append a signature and do not use a stable prepared message.

- [ ] **Step 3: Write minimal implementation**

Use each sender's existing durable identity as delivery_key: follow-up revision UUID, meeting delivery identity, weekly report period/report identity, and explicit CLI idempotency key. Preserve provider targets, mentions, titles, and idempotency parameters. Persist and record the facade final_body, not the pre-postfix local variable.

- [ ] **Step 4: Run test to verify GREEN**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_follow_up.py tests/test_meeting_alignment_delivery.py tests/test_weekly_okr_report.py tests/test_cli.py tests/test_feedback_spike.py -q

Expected: PASS with no real external send.

- [ ] **Step 5: Commit**

~~~bash
git add app/follow_up.py app/meeting_alignment_delivery.py app/weekly_okr_report.py app/cli.py app/feedback_spike.py app/dws_client.py tests/test_follow_up.py tests/test_meeting_alignment_delivery.py tests/test_weekly_okr_report.py tests/test_cli.py tests/test_feedback_spike.py
git commit -m "refactor: route DingTalk sends through postfix facade"
~~~

### Task 5: Persist final WeChat delivery bodies before dispatch

**Files:**

- Modify: tests/wechat/test_send_mode.py, tests/wechat/test_accessibility.py, tests/test_store.py
- Modify: app/wechat/consumer.py, app/wechat/models.py, app/wechat/service.py, app/wechat/accessibility.py, app/store.py

- [ ] **Step 1: Write failing WeChat lifecycle tests**

~~~python
def test_wechat_delivery_persists_postfixed_body_before_auto_send(store, sender):
    delivery = produce_and_finalize_wechat_reply(store, reply_text="回复")
    assert delivery.reply_text.count("/api/dingtalk-feedback-spike") == 2
    assert sender.runner.received_text == delivery.reply_text


def test_wechat_retry_and_recall_use_same_prepared_body(store, sender, scope, runner):
    delivery = ready_wechat_delivery(store)
    sender.send(delivery, scope)
    requeued = store.get_wechat_delivery_by_id(delivery.id)
    assert requeued.reply_text == delivery.reply_text
    assert recall_wechat_delivery(store, runner, delivery.id, requeued.reply_text)
~~~

- [ ] **Step 2: Run test to verify RED**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_send_mode.py tests/wechat/test_accessibility.py tests/test_store.py -q -k 'postfixed_body or retry_and_recall'

Expected: FAIL because the current delivery row stores raw decision text.

- [ ] **Step 3: Write minimal implementation**

When finalize_wechat_reply_task creates a delivery, use its inserted id to derive delivery_key="wechat:<id>", prepare the body atomically, and persist the returned final body/token before the row becomes ready_to_send. Add feedback_token and postfix_version to WechatDelivery only if History or synchronization needs explicit projections; the canonical body and outbound_postfixes record remain authoritative.

Make WechatSender.send receive a prepared delivery only. Its IPC request, accessibility readback comparisons, and recall calls use the stored final body without composing again.

- [ ] **Step 4: Run test to verify GREEN**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_send_mode.py tests/wechat/test_accessibility.py tests/wechat/test_sender_ipc.py tests/wechat/test_audit_web.py tests/test_store.py -q

Expected: PASS without launching or sending to real WeChat.

- [ ] **Step 5: Commit**

~~~bash
git add app/wechat/consumer.py app/wechat/models.py app/wechat/service.py app/wechat/accessibility.py app/store.py tests/wechat/test_send_mode.py tests/wechat/test_accessibility.py tests/test_store.py
git commit -m "feat: persist postfixed WeChat deliveries"
~~~

### Task 6: Enforce the no-bypass boundary and release safely

**Files:**

- Create: tests/test_message_send_architecture.py
- Modify: docs/architecture.md, docs/runtime-mechanism.md
- Modify any focused test fixtures made incompatible by the facade type boundary.

- [ ] **Step 1: Write a failing architecture scan**

~~~python
def test_first_party_message_senders_do_not_bypass_service_message_sender():
    violations = find_raw_send_calls(
        root=Path("app"),
        forbidden=("send_message", "reply_message", "send_reply_to_trigger", "WechatSender.send"),
        allowlisted=("app/service_message_sender.py", "app/dws_client.py", "app/wechat/accessibility.py"),
    )
    assert violations == []
~~~

The scanner ignores tests, provider implementation methods, method definitions, and read-only strings; it reports every violating source path and line number. It has no permanent exception list for business senders.

- [ ] **Step 2: Run test to verify RED**

Run: /Users/derek/miniforge3/bin/python -m pytest tests/test_message_send_architecture.py -q

Expected: FAIL, listing remaining direct first-party sender calls.

- [ ] **Step 3: Finish migration and document the invariant**

Replace remaining first-party direct calls with facade methods; do not add a catch-all exception or wildcard allowlist. Add a concise Unified outbound postfix section to both architecture documents, naming the only facade and stable retry rule. State that Email remains non-sending and outside this transport policy.

- [ ] **Step 4: Run release verification**

~~~bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_outbound_postfix.py tests/test_service_message_sender.py tests/test_message_send_architecture.py tests/test_consumer_agent.py tests/test_agent_turn_runner.py tests/test_follow_up.py tests/test_meeting_alignment_delivery.py tests/test_weekly_okr_report.py tests/test_feedback_spike.py tests/wechat/test_send_mode.py tests/wechat/test_accessibility.py -q
/Users/derek/miniforge3/bin/python -m ruff check app tests/test_outbound_postfix.py tests/test_service_message_sender.py tests/test_message_send_architecture.py
git diff --check
~~~

Expected: all selected tests and Ruff pass; no whitespace errors.

- [ ] **Step 5: Commit enforcement and documentation**

~~~bash
git add docs/architecture.md docs/runtime-mechanism.md tests/test_message_send_architecture.py
git commit -m "test: prevent service message postfix bypasses"
~~~

- [ ] **Step 6: Restart and verify the local service after all runtime commits**

~~~bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -sS --fail http://127.0.0.1:8765/healthz
~~~

Then read the authoritative local database and confirm zero processing and failed reply_tasks. Verify a non-sending prepared-message composition using the real feedback base URL produces exactly two feedback callback URLs. Do not create an unsolicited external message merely for production validation.

## Plan self-review

- **Spec coverage:** Tasks 1–2 establish stable immutable preparation; Task 3 handles both Agent action contracts before Audit; Task 4 covers current non-Agent DingTalk senders; Task 5 covers persisted WeChat delivery/retry/recall; Task 6 prevents future bypasses, documents the contract, and performs service validation. Email is explicitly excluded in every applicable task.
- **No placeholders:** This plan has no known placeholder markers or deferred undefined work. Each code task names files, tests, command, expected red/green outcome, and commit boundary.
- **Contract consistency:** PreparedOutboundMessage, channel/delivery_key, final_body, and feedback_token are used consistently through all tasks.
