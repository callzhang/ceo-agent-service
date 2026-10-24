"""Pin what the WeChat pipeline does today, end to end.

Phase 0 of the plan to move WeChat onto the standard Consumer -> Audit ->
revision path. These tests run the real Consumer, the real Audit gate and the
real service loop with fake model, reader and sender, and assert the observable
terminal states. They describe current behaviour, including the behaviour the
migration will deliberately change; those tests say so in their name so the
change is explicit when it happens.

Real terminal states seen in production (138 deliveries) and where they are
pinned:

  sent ...................................... test_pipeline_approved_reply_is_sent
  failed  wechat_audit_rejected ............. test_pipeline_audit_rejection_closes_the_delivery_without_revision
  wechat_audit_candidate_changed / _unavailable  (both raise today) test_audit_candidate_change_currently_raises_*, test_audit_unavailable_currently_raises_*
  skipped no_reply / handoff_to_human ....... test_consumer_decision_matrix
  superseded_by_newer_wechat_trigger/delivery  tests/wechat/test_store.py
  skipped stale_*/user_rejected/principal ... tests/wechat/test_store.py, test_send_mode.py
  failed  accessibility_not_trusted etc. .... tests/wechat/test_send_mode.py (pre-action retries)
  send_unknown reconciliation ............... tests/wechat/test_accessibility.py
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dingtalk_models import CodexAction, CodexDecision
from app.store import AgentRole, AgentRunLeaseLostError, AutoReplyStore
from app.wechat import service
from app.wechat.accessibility import SendOutcome
from app.wechat.audit import (
    WechatAuditDecision, WechatDeliveryAuditor, wechat_audit_candidate,
    wechat_audit_digest,
)
from app.wechat.consumer import WechatReplyConsumer
from app.wechat.models import WechatAccount, WechatReplyScope


class FakeCodexRunner:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def decide(self, prompt, session_id, image_paths=None, *, run_id=None):
        self.calls += 1
        return self.decision


class FakeReader:
    def read_messages(self, account, **kwargs):
        return []


class ScriptedAuditRuntime:
    """Answers the Audit turn with a fixed outcome for whatever digest it is shown."""

    last_json = ""

    def __init__(self, outcome="approve", summary="reviewed"):
        self.outcome, self.summary, self.calls = outcome, summary, 0

    def execute(self, **kwargs):
        self.calls += 1
        digest = json.loads(kwargs["prompt"])["candidate_digest"]
        decision = WechatAuditDecision(
            outcome=self.outcome, candidate_digest=digest, summary=self.summary,
        )
        ScriptedAuditRuntime.last_json = decision.model_dump_json()
        return SimpleNamespace(value=ScriptedAuditRuntime.last_json, transcript_end=0)


@pytest.fixture(autouse=True)
def native_audit_result(monkeypatch):
    """The store re-reads the Audit's native result; serve the scripted one."""
    import app.native_trajectory as native_trajectory

    ScriptedAuditRuntime.last_json = ""
    monkeypatch.setattr(
        native_trajectory, "read_native_result_json_from_attempt",
        lambda *_args, **_kwargs: ScriptedAuditRuntime.last_json,
    )


class StrictSender:
    """Claims through the real gate, then records the send like the real sender."""

    def __init__(self, store):
        self.store, self.sent = store, []

    def send(self, delivery, scope):
        claimed = self.store.claim_wechat_delivery(
            delivery.id, expected_execution_generation=delivery.execution_generation,
        )
        if claimed is None:
            return SendOutcome("not_claimed")
        self.sent.append(delivery.id)
        self.store.set_wechat_delivery_status(delivery.id, "sent")
        return SendOutcome("sent")


@pytest.fixture
def account():
    return WechatAccount(
        account_id="acct-1", display_name="derek", self_user_id="self-1",
        account_dir="/a", db_dir="/a/db_storage", app_version="4.1.10",
    )


@pytest.fixture
def store(tmp_path):
    store = AutoReplyStore(tmp_path / "pipeline.sqlite3")
    store.replace_wechat_reply_scopes("acct-1", [WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u9",
        conversation_id="u9", display_name="Alex",
        trigger_mode="every_inbound_text", binding_status="verified",
    )])
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00", trigger_sender="Alex",
        trigger_text="下午能给结论吗",
    )
    return store


def _consume(store, account, decision):
    runner = FakeCodexRunner(decision)
    consumer = WechatReplyConsumer(store, runner, reader=None, account=account)
    assert consumer.run_once(limit=1) == 1
    return runner


def _send_reply():
    return CodexDecision(
        action=CodexAction.SEND_REPLY, reply_text="收到，我下午给你结论。",
        reason="明确承诺", audit_summary="明确承诺",
    )


def _run_service(store, account, runtime, sender):
    auditor = WechatDeliveryAuditor(store, runtime, FakeReader(), account)
    return service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True,
        reader=None, account=None, auditor=auditor,
    )


@pytest.mark.parametrize(
    ("action", "delivery_status", "attempt_status", "attempt_error"),
    [
        (CodexAction.SEND_REPLY, "ready_to_send", "pending", ""),
        (CodexAction.ASK_CLARIFYING_QUESTION, "ready_to_send", "pending", ""),
        (CodexAction.NO_REPLY, None, "skipped", "no_reply"),
        (CodexAction.HANDOFF_TO_HUMAN, None, "skipped", "handoff_to_human"),
    ],
)
def test_consumer_decision_matrix(
    store, account, action, delivery_status, attempt_status, attempt_error,
):
    decision = CodexDecision(
        action=action, reply_text="可以。" if delivery_status else "",
        reason="r", audit_summary="s",
    )
    _consume(store, account, decision)

    delivery = store.get_wechat_delivery_for_task(1)
    assert (delivery.status if delivery else None) == delivery_status
    attempt = store.get_latest_reply_attempt_for_trigger("u9", "m1")
    assert attempt.send_status == attempt_status
    assert (attempt.send_error or "") == attempt_error


def test_pipeline_approved_reply_is_sent(store, account):
    _consume(store, account, _send_reply())
    runtime, sender = ScriptedAuditRuntime("approve"), StrictSender(store)

    assert _run_service(store, account, runtime, sender) == 1

    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery.status == "sent"
    assert sender.sent == [delivery.id]
    assert runtime.calls == 1
    assert store.wechat_delivery_review_approved(delivery.id)
    runs = store.list_agent_runs_for_task_generation(
        1, delivery.execution_generation, load_events=False,
    )
    assert [(run.role, run.status) for run in runs] == [
        (AgentRole.CONSUMER, "completed"), (AgentRole.AUDIT, "completed"),
    ]
    assert runs[1].operation_id == f"wechat-delivery:{delivery.id}"
    assert store.get_latest_reply_attempt_for_trigger("u9", "m1").send_status == "sent"


def test_pipeline_audit_rejection_closes_the_delivery_without_revision(store, account):
    """CURRENT behaviour, changed by the unification: a rejection is terminal.

    The Consumer is not asked to revise, nothing is sent, and the delivery ends
    ``failed`` with ``wechat_audit_rejected`` (so it appears in Attention).
    """
    runner = _consume(store, account, _send_reply())
    runtime, sender = ScriptedAuditRuntime("reject", "repeats a question already answered"), StrictSender(store)

    assert _run_service(store, account, runtime, sender) == 0

    delivery = store.get_wechat_delivery_for_task(1)
    assert (delivery.status, delivery.error, delivery.pre_action_failure) == (
        "failed", "wechat_audit_rejected", True,
    )
    assert sender.sent == []
    assert runner.calls == 1
    assert store.requeue_unperformed_wechat_deliveries(retry_delay_seconds=0) == 0
    assert store.get_latest_reply_attempt_for_trigger("u9", "m1").send_status == "failed"
    consumers = [
        run for run in store.list_agent_runs_for_task_generation(
            1, delivery.execution_generation, load_events=False,
        ) if run.role is AgentRole.CONSUMER
    ]
    assert len(consumers) == 1


def test_audit_candidate_change_currently_raises_instead_of_closing_the_delivery(
    store, account, monkeypatch,
):
    """CURRENT behaviour (a latent bug, retired by the unification).

    The Auditor means to close a delivery whose reviewed digest no longer matches
    with ``wechat_audit_candidate_changed``, but ``set_wechat_delivery_status``
    only accepts ``failed`` from ``sending``/``send_unknown`` for that error, so
    the call raises and the delivery stays ``ready_to_send``.
    """
    _consume(store, account, _send_reply())
    delivery = store.get_wechat_delivery_for_task(1)
    task = store.get_reply_task(1)
    stale = WechatAuditDecision(
        outcome="approve",
        candidate_digest=wechat_audit_digest({**wechat_audit_candidate(task, delivery), "reply_text": "old"}),
        summary="reviewed an earlier body",
    )
    parent = max(
        (run for run in store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation, load_events=False)
         if run.role is AgentRole.CONSUMER),
        key=lambda run: run.id,
    )
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=parent.id,
        operation_id=f"wechat-delivery:{delivery.id}", owner="audit",
    ).run
    store.complete_agent_run(audit.id, stale.model_dump(mode="json"), owner="audit")
    original = store.get_agent_run
    monkeypatch.setattr(
        store, "get_agent_run",
        lambda run_id, **kw: original(run_id, **kw).model_copy(
            update={"final_result_json": stale.model_dump_json()}
        ) if run_id == audit.id else original(run_id, **kw),
    )
    runtime = ScriptedAuditRuntime()

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        WechatDeliveryAuditor(store, runtime, FakeReader(), account).review(delivery)

    assert store.get_wechat_delivery_by_id(delivery.id).status == "ready_to_send"
    assert runtime.calls == 0


def test_audit_unavailable_currently_raises_instead_of_closing_the_delivery(store, account):
    """CURRENT behaviour (same latent bug): the third failed Audit run raises."""
    _consume(store, account, _send_reply())
    delivery = store.get_wechat_delivery_for_task(1)
    task = store.get_reply_task(1)
    parent = max(
        (run for run in store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation, load_events=False)
         if run.role is AgentRole.CONSUMER),
        key=lambda run: run.id,
    )
    for turn in range(3):
        audit = store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.AUDIT,
            proposal_revision=0, turn_attempt=turn, parent_agent_run_id=parent.id,
            operation_id=f"wechat-delivery:{delivery.id}", owner=f"audit-{turn}",
        ).run
        store.fail_agent_run(
            audit.id, {"code": "wechat_audit_failed", "detail": "test"}, owner=f"audit-{turn}",
        )
    runtime = ScriptedAuditRuntime()

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        WechatDeliveryAuditor(store, runtime, FakeReader(), account).review(delivery)

    assert store.get_wechat_delivery_by_id(delivery.id).status == "ready_to_send"
    assert runtime.calls == 0


def test_one_unclosable_audit_blocks_the_deliveries_behind_it_today(store, account):
    """CURRENT behaviour: head-of-line blocking in ``process_ready_wechat_deliveries``.

    Reviews run in a plain loop, so the delivery whose third Audit run failed
    raises (see the test above) before the next delivery is reviewed; the sender
    loop swallows the error and repeats the same pass.
    """
    # A different conversation: a newer trigger in the same one would supersede
    # the first delivery instead.
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u8", conversation_title="Blake",
        single_chat=True, trigger_message_id="m2",
        trigger_create_time="2026-07-17T10:05:00", trigger_sender="Blake",
        trigger_text="还有一件事",
    )
    runner = FakeCodexRunner(_send_reply())
    consumer = WechatReplyConsumer(store, runner, reader=None, account=account)
    assert consumer.run_once(limit=2) == 2
    first, second = sorted(
        service.pending_wechat_deliveries(store), key=lambda delivery: delivery.id,
    )
    task = store.get_reply_task(first.task_id)
    parent = max(
        (run for run in store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation, load_events=False)
         if run.role is AgentRole.CONSUMER),
        key=lambda run: run.id,
    )
    for turn in range(3):
        audit = store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.AUDIT,
            proposal_revision=0, turn_attempt=turn, parent_agent_run_id=parent.id,
            operation_id=f"wechat-delivery:{first.id}", owner=f"audit-{turn}",
        ).run
        store.fail_agent_run(
            audit.id, {"code": "wechat_audit_failed", "detail": "test"}, owner=f"audit-{turn}",
        )
    runtime, sender = ScriptedAuditRuntime("approve"), StrictSender(store)

    with pytest.raises(AgentRunLeaseLostError):
        _run_service(store, account, runtime, sender)

    assert runtime.calls == 0
    assert sender.sent == []
    assert store.get_wechat_delivery_by_id(second.id).status == "ready_to_send"
    assert not store.wechat_delivery_review_approved(second.id)


# --- golden outcomes ---------------------------------------------------------
# One row per scenario: the observable end state of the pipeline. Phase 3 of the
# unification runs the standard path in shadow mode and diffs against this file;
# a deliberate behaviour change edits the file in the same commit.

GOLDEN_PATH = Path(__file__).with_name("fixtures") / "pipeline_outcomes.json"


def _outcome(store):
    delivery = store.get_wechat_delivery_for_task(1)
    attempt = store.get_latest_reply_attempt_for_trigger("u9", "m1")
    runs = []
    if delivery is not None:
        runs = [
            [run.role.value, run.status]
            for run in store.list_agent_runs_for_task_generation(
                1, delivery.execution_generation, load_events=False,
            )
        ]
    return {
        "delivery": None if delivery is None else {
            "status": delivery.status,
            "error": delivery.error,
            "pre_action_failure": delivery.pre_action_failure,
        },
        "attempt": {"send_status": attempt.send_status, "send_error": attempt.send_error or ""},
        "runs": runs,
    }


def _scenarios():
    return {
        "send_reply_audit_approve": (_send_reply(), "approve"),
        "send_reply_audit_reject": (_send_reply(), "reject"),
        "ask_clarifying_audit_approve": (
            CodexDecision(action=CodexAction.ASK_CLARIFYING_QUESTION,
                          reply_text="具体指哪一份？", reason="r", audit_summary="s"),
            "approve",
        ),
        "no_reply": (
            CodexDecision(action=CodexAction.NO_REPLY, reply_text="", reason="r", audit_summary="s"),
            None,
        ),
        "handoff_to_human": (
            CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN, reply_text="", reason="r", audit_summary="s"),
            None,
        ),
    }


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_pipeline_outcome_matches_the_golden_file(store, account, name):
    decision, audit_outcome = _scenarios()[name]
    _consume(store, account, decision)
    if audit_outcome is not None:
        _run_service(store, account, ScriptedAuditRuntime(audit_outcome), StrictSender(store))

    actual = _outcome(store)

    if os.environ.get("UPDATE_WECHAT_GOLDEN"):
        golden = json.loads(GOLDEN_PATH.read_text()) if GOLDEN_PATH.exists() else {}
        golden[name] = actual
        GOLDEN_PATH.parent.mkdir(exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    assert actual == json.loads(GOLDEN_PATH.read_text())[name]
