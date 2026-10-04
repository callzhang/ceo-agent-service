import json

from app.agent_contracts import ConsumerAgentResult
from app.system_executor import ActionOutcome, SystemExecutor


def _candidate(*, outcome="proposal", actions=None, options=None):
    plan = {"objective": "Do work", "actions": actions or [{
        "description": "Act", "action_identity": "first", "capability": "test",
        "operation": "act", "target": {"id": "1"}, "payload": {"value": "x"},
    }], "sourced_facts": [], "authored_judgment": "Supported"}
    return ConsumerAgentResult.model_validate({
        "outcome": outcome, "summary": "Do work", "proposal": plan if outcome == "proposal" else None,
        "decision_options": options or [], "needs_human_reason": "Choose" if outcome == "needs_human" else None,
        "decision_basis": {"verified_facts": [{"assertion": "Fact", "references": ["source:1"]}],
            "rule_evidence": [{"assertion": "Rule", "references": ["rule:1"]}],
            "quality_explanation": "Need choice", "no_external_action_evidence": [{"assertion": "None", "references": ["attempt:1"]}],
            "conclusion": "Choose"} if outcome == "needs_human" else None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0, "information_completeness": 1.0,
    })


class FakeStore:
    def __init__(self, candidate, *, selected=None):
        self.candidate = candidate
        self.selected = selected
        self.attempts = {}
        self.events = []
        self.status = None
        self.previous_receipt = None
        self.previous_candidate = None
        self.ledger = None
        self.verified_source = None

    def current_reviewed_candidate(self, task_id, generation):
        self.events.append("load")
        return {"id": 1, "task_id": task_id, "execution_generation": generation,
            "review_id": 2, "candidate_json": json.dumps(self.candidate.model_dump(mode="json")),
            "candidate_digest": __import__("app.reviewed_candidates", fromlist=["candidate_digest"]).candidate_digest(self.candidate),
            "proposal_revision": 0, "stage_index": 0, "predecessor_review_id": None,
            "audit_run_id": 8,
            "selection_id": 3 if self.selected else None,
            "branch_json": json.dumps(self.selected) if self.selected else None}

    def get_reply_task(self, task_id):
        return type("Task", (), {"id": task_id, "business_object_key": "object:1", "execution_generation": "gen-1", "conversation_id": "cid", "trigger_message_id": "trigger"})()

    def claim_candidate_execution(self, candidate_id, review_id, owner, lease_seconds):
        self.events.append("claim")
        if self.candidate.outcome.value == "needs_human" and not self.selected:
            return None
        return {"id": 4, "candidate_id": candidate_id, "review_id": review_id}

    def renew_candidate_execution_claim(self, execution_id, owner, lease_seconds):
        self.events.append("renew")
        return {"id": execution_id}

    def get_candidate_execution(self, candidate_id):
        return None

    def get_candidate_external_action(self, action_key):
        return self.previous_receipt or self.ledger

    def get_verified_action_source(self, action_key):
        return self.verified_source

    def get_agent_run(self, run_id):
        return type("Run", (), {"final_result_json": json.dumps(self.previous_candidate.model_dump(mode="json"))})()

    def begin_candidate_action(self, execution_id, owner, index, action_key):
        self.events.append(f"begin:{index}")
        if index not in self.attempts:
            self.attempts[index] = {"status": "dispatched", "external_action_key": action_key}
        elif self.attempts[index]["status"] == "dispatched":
            self.attempts[index]["status"] = "uncertain"
        return self.attempts[index]

    def record_candidate_external_action(self, execution_id, owner, index, action_key, operation, target_identifiers, provider_result):
        self.events.append(f"receipt:{index}")
        self.attempts[index]["status"] = "verified"
        self.previous_candidate = self.candidate
        self.ledger = {"provider_result_json": json.dumps(provider_result), "external_action_key": action_key, "first_agent_run_id": 7}
        return self.ledger

    def record_completed_agent_message_delivery(self, **kwargs):
        self.events.append("sent_reply")
        assert kwargs["agent_run_id"] == 8
        return kwargs

    def record_candidate_action_outcome(self, execution_id, owner, index, status, result):
        self.attempts[index]["status"] = status
        self.attempts[index]["result_json"] = json.dumps(result)
        return self.attempts[index]

    def list_candidate_action_attempts(self, execution_id):
        return [{"action_index": index, **attempt}
                for index, attempt in sorted(self.attempts.items())]

    def finish_candidate_execution(self, execution_id, owner, status, result, *, invalidate_reason=""):
        self.events.append(f"finish:{status}")
        if invalidate_reason:
            self.events.append(f"invalidate:1:{invalidate_reason}")
        self.status = status
        return {"status": status}


class Handler:
    def __init__(self):
        self.calls = []
        self.reconcile_result = None

    def dispatch(self, action, *, action_key, candidate):
        self.calls.append(action.action_identity)
        return ActionOutcome("verified", {"receipt_id": action.action_identity})

    def reconcile(self, action, *, action_key, candidate):
        return self.reconcile_result


def test_exact_reviewed_plan_dispatches_only_after_persisted_claim():
    store = FakeStore(_candidate())
    handler = Handler()
    result = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(store.get_reply_task(1), 1, 2)
    assert result.outcome == "executed"
    assert handler.calls == ["first"]
    assert store.events[:4] == ["load", "claim", "renew", "begin:0"]
    assert store.events[-2:] == ["receipt:0", "finish:done"]


def test_unselected_branch_and_dry_run_never_dispatch():
    store = FakeStore(_candidate(outcome="needs_human", options=[
        {"key": "go", "label": "Go", "instruction": "Act", "consequence": "Effect", "plan": _candidate().proposal.model_dump(mode="json")},
        {"key": "stop", "label": "Stop", "instruction": "Stop", "consequence": "No effect", "terminal_outcome": "skipped", "reason": "Requested stop"},
    ]))
    handler = Handler()
    executor = SystemExecutor(store, {("test", "act"): handler}, owner="worker")
    assert executor.execute(store.get_reply_task(1), 1, 2).outcome == "failed"
    assert handler.calls == []
    store.selected = store.candidate.decision_options[1].model_dump(mode="json")
    assert executor.execute(store.get_reply_task(1), 1, 2, dry_run=True).outcome == "dry_run"
    assert executor.execute(store.get_reply_task(1), 1, 2).outcome == "skipped"
    assert handler.calls == []


def test_reviewed_no_action_finishes_skipped_without_provider_call():
    store = FakeStore(_candidate(outcome="no_action"))
    handler = Handler()
    result = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(store.get_reply_task(1), 1, 2)
    assert result.outcome == "skipped"
    assert store.status == "skipped"
    assert handler.calls == []


def test_partial_verified_action_is_not_replayed_and_unknown_stops():
    actions = [
        {"description": "OA", "action_identity": "oa", "capability": "test", "operation": "act", "target": {"id": "1"}, "payload": {}},
        {"description": "Notice", "action_identity": "notice", "capability": "test", "operation": "act", "target": {"id": "2"}, "payload": {}},
    ]
    store = FakeStore(_candidate(actions=actions))
    handler = Handler()
    executor = SystemExecutor(store, {("test", "act"): handler}, owner="worker")
    store.attempts[0] = {"status": "verified", "external_action_key": "existing"}
    assert executor.execute(store.get_reply_task(1), 1, 2).outcome == "executed"
    assert handler.calls == ["notice"]
    store.attempts[1] = {"status": "dispatched", "external_action_key": store.attempts[1]["external_action_key"]}
    handler.calls.clear()
    assert executor.execute(store.get_reply_task(1), 1, 2).outcome == "failed"
    assert handler.calls == []


def test_uncertain_action_resumes_from_positive_reconciliation_without_redispatch():
    store = FakeStore(_candidate())
    store.attempts[0] = {"status": "uncertain", "external_action_key": "prior-key"}
    handler = Handler()
    handler.reconcile_result = ActionOutcome("verified", {"receipt_id": "first"})
    result = SystemExecutor(store, {("test", "act"): handler}, owner="reconciler").execute(store.get_reply_task(1), 1, 2)
    assert result.outcome == "executed"
    assert handler.calls == []
    assert store.events.index("begin:0") < store.events.index("receipt:0")


def test_unsupported_action_fails_without_generic_execution():
    store = FakeStore(_candidate())
    result = SystemExecutor(store, {}, owner="worker").execute(store.get_reply_task(1), 1, 2)
    assert result.outcome == "failed"
    assert store.status == "failed"


def test_verified_message_updates_existing_sent_reply_projection():
    candidate = _candidate(actions=[{
        "description": "Send", "action_identity": "notice", "capability": "dingtalk-chat",
        "operation": "send_message", "target": {"conversation_id": "cid"},
        "payload": {"content": "Prepared text"},
    }])
    store = FakeStore(candidate)
    handler = Handler()
    result = SystemExecutor(store, {("dingtalk-chat", "send_message"): handler}, owner="worker").execute(store.get_reply_task(1), 1, 2)
    assert result.outcome == "executed"
    assert "sent_reply" in store.events
    assert result.external_result.live_result_reference["actions"][0]["action_identity"] == "notice"


def test_successful_old_action_with_changed_body_cannot_be_reused_or_resent():
    before = _candidate()
    after = before.model_copy(deep=True)
    after.proposal.actions[0].payload["value"] = "different"
    store = FakeStore(after)
    store.previous_candidate = before
    store.previous_receipt = {"first_agent_run_id": 7}
    handler = Handler()
    result = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(store.get_reply_task(1), 1, 2)
    assert result.outcome == "failed"
    assert result.error.code == "completed_action_content_conflict"
    assert handler.calls == []
    assert "begin:0" not in store.events


def test_provider_business_state_change_invalidates_review_and_returns_evidence():
    store = FakeStore(_candidate())

    class ChangedHandler:
        def dispatch(self, action, *, action_key, candidate):
            return ActionOutcome("business_state_changed", {
                "reason": "oa_task_owner_changed", "task_id": "task-1", "owner_id": "other",
            })

        def reconcile(self, action, *, action_key, candidate):
            return None

    result = SystemExecutor(store, {("test", "act"): ChangedHandler()}, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.outcome == "failed"
    assert result.error.code == "business_state_changed"
    assert result.external_result.live_result_reference["evidence"]["owner_id"] == "other"
    assert result.completed_action_keys == ()
    assert "finish:failed" in store.events
    assert "invalidate:1:business_state_changed" in store.events


def test_changed_reviewed_source_invalidates_before_first_dispatch():
    from types import SimpleNamespace
    from app.agent_contracts import ReviewedSourceBinding
    from app.reviewed_sources import context_source

    context = SimpleNamespace(trigger_message_id="trigger", trigger_text="old",
                              trigger_raw_payload={}, messages=(), materials=())
    binding = ReviewedSourceBinding(provider="task_context", object_ref="trigger",
                                    value=context_source(context))
    candidate = _candidate().model_copy(update={"source_bindings": (binding,)})
    store = FakeStore(candidate)
    handler = Handler()
    changed = SimpleNamespace(trigger_message_id="trigger", trigger_text="new",
                              trigger_raw_payload={}, messages=(), materials=())
    result = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(
        store.get_reply_task(1), 1, 2, context=changed,
    )
    assert result.error.code == "business_state_changed"
    assert result.external_result.live_result_reference["evidence"][0]["provider"] == "task_context"
    assert "invalidate:1:business_state_changed" in store.events
    assert "begin:0" not in store.events
    assert handler.calls == []


def test_unavailable_reviewed_source_retries_without_dispatch():
    from app.agent_contracts import ReviewedSourceBinding

    binding = ReviewedSourceBinding(provider="task_context", object_ref="trigger",
                                    value={"trigger_text": "old"})
    store = FakeStore(_candidate().model_copy(update={"source_bindings": (binding,)}))
    result = SystemExecutor(store, {("test", "act"): Handler()}, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.error.code == "reviewed_source_unavailable"
    assert result.error.retryable is True
    assert "begin:0" not in store.events


def test_reviewed_source_auth_error_keeps_native_code_and_auth_flag():
    from app.agent_contracts import ReviewedSourceBinding
    from app.dws_client import DwsError

    class Dws:
        def read_oa_approval_detail(self, process_instance_id):
            raise DwsError("provider detail is private", code="PAT_HIGH_RISK_NO_PERMISSION",
                           server_key="dingtalk-oa", retryable_external_dependency=False)

    binding = ReviewedSourceBinding(provider="dingtalk-oa", object_ref="process-auth",
                                    value={"processInstanceId": "process-auth", "formValueVOS": []})
    store = FakeStore(_candidate().model_copy(update={"source_bindings": (binding,)}))
    result = SystemExecutor(store, {("test", "act"): Handler()}, dws=Dws(), owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.error.code == "authorization_required"
    assert result.error.authorization_required is True
    assert result.error.retryable is False
    assert result.error.source_code == "PAT_HIGH_RISK_NO_PERMISSION"
    assert result.error.source == "dingtalk-oa"
    assert store.status == "failed"
    assert "begin:0" not in store.events
    assert "provider detail is private" not in result.model_dump_json()


def test_reviewed_source_private_group_refusal_is_terminal_with_native_code():
    from app.agent_contracts import ReviewedSourceBinding
    from app.dws_client import DwsError

    class Dws:
        def read_oa_approval_detail(self, process_instance_id):
            raise DwsError("private group not accessible", code="1",
                           server_key="private-group", retryable_external_dependency=False)

    binding = ReviewedSourceBinding(provider="dingtalk-oa", object_ref="process-private",
                                    value={"processInstanceId": "process-private", "formValueVOS": []})
    store = FakeStore(_candidate().model_copy(update={"source_bindings": (binding,)}))
    result = SystemExecutor(store, {("test", "act"): Handler()}, dws=Dws(), owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.error.code == "reviewed_source_unavailable"
    assert result.error.authorization_required is False
    assert result.error.retryable is False
    assert result.error.source_code == "1"
    assert result.error.source == "private-group"
    assert store.status == "failed"
    assert "begin:0" not in store.events


def test_partial_resume_rechecks_native_oa_form_before_next_action():
    from app.agent_contracts import ReviewedSourceBinding
    from app.reviewed_sources import read_provider_source

    class Dws:
        def __init__(self):
            self.form = "old"

        def read_oa_approval_detail(self, process_instance_id):
            return {"success": True, "result": {
                "processInstanceId": process_instance_id,
                "processCode": "leave",
                "originatorUserid": "applicant",
                "formValueVOS": [{"name": "reason", "value": self.form}],
                "tasks": [{"taskId": 10, "taskResult": "AGREE", "taskStatus": "COMPLETED", "userId": "reviewer"}],
                "operationRecords": [{"type": "AGREE"}],
            }}

    dws = Dws()
    old_form = read_provider_source(dws, "dingtalk-oa", "process-1")
    actions = [
        {"description": "Approved OA", "action_identity": "oa", "capability": "test",
         "operation": "act", "target": {"id": "1"}, "payload": {"value": "x"}},
        {"description": "Notify", "action_identity": "notify", "capability": "test",
         "operation": "act", "target": {"id": "1"}, "payload": {"value": "x"}},
    ]
    candidate = _candidate(actions=actions).model_copy(update={"source_bindings": (
        ReviewedSourceBinding(provider="dingtalk-oa", object_ref="process-1", value=old_form),
    )})
    store = FakeStore(candidate)
    store.attempts[0] = {"status": "verified", "external_action_key": "prior-receipt"}
    dws.form = "new"
    handler = Handler()

    result = SystemExecutor(store, {("test", "act"): handler}, dws=dws, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )

    assert result.error.code == "business_state_changed"
    assert result.completed_action_keys == ("oa",)
    assert result.external_result.live_result_reference["evidence"][0]["current_value"]["formValueVOS"][0]["value"] == "new"
    assert "invalidate:1:business_state_changed" in store.events
    assert "begin:1" not in store.events
    assert handler.calls == []


def test_same_call_oa_completion_then_form_change_blocks_notification():
    from app.agent_contracts import ReviewedSourceBinding
    from app.reviewed_sources import read_provider_source

    class Dws:
        form = "old"

        def read_oa_approval_detail(self, process_instance_id):
            return {"success": True, "result": {
                "processInstanceId": process_instance_id,
                "formValueVOS": [{"name": "reason", "value": self.form}],
                "tasks": [{"taskId": 811, "taskStatus": "COMPLETED", "taskResult": "AGREE", "userId": "reviewer"}],
            }}

    dws = Dws()
    binding = ReviewedSourceBinding(
        provider="dingtalk-oa", object_ref="process-811",
        value=read_provider_source(dws, "dingtalk-oa", "process-811"),
    )
    actions = [
        {"description": "Approve", "action_identity": "oa", "capability": "test",
         "operation": "act", "target": {"id": "811"}, "payload": {"value": "approve"}},
        {"description": "Notify", "action_identity": "notice", "capability": "test",
         "operation": "act", "target": {"id": "applicant"}, "payload": {"value": "approved"}},
    ]
    store = FakeStore(_candidate(actions=actions).model_copy(update={"source_bindings": (binding,)}))

    class MutatingHandler(Handler):
        def dispatch(self, action, *, action_key, candidate):
            result = super().dispatch(action, action_key=action_key, candidate=candidate)
            if action.action_identity == "oa":
                dws.form = "new"
            return result

    handler = MutatingHandler()
    result = SystemExecutor(store, {("test", "act"): handler}, dws=dws, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )

    assert result.error.code == "business_state_changed"
    assert result.completed_action_keys == ("oa",)
    assert result.external_result.live_result_reference["completed_actions"][0]["external_action_key"] == store.attempts[0]["external_action_key"]
    assert handler.calls == ["oa"]
    assert "receipt:0" in store.events
    assert "begin:1" not in store.events
    assert "invalidate:1:business_state_changed" in store.events


def test_verified_reaction_does_not_create_sent_message_projection():
    reaction = _candidate(actions=[{
        "description": "React", "action_identity": "react", "capability": "dingtalk-chat",
        "operation": "add_message_emoji", "target": {"conversation_id": "cid", "message_id": "mid"},
        "payload": {"emoji": "👍"},
    }])
    store = FakeStore(reaction)
    result = SystemExecutor(store, {("dingtalk-chat", "add_message_emoji"): Handler()}, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.outcome == "executed"
    assert "sent_reply" not in store.events


def test_competing_execution_claim_is_retryable():
    store = FakeStore(_candidate())
    store.claim_candidate_execution = lambda *args: None
    store.get_candidate_execution = lambda candidate_id: {"status": "running"}
    result = SystemExecutor(store, {("test", "act"): Handler()}, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.error.code == "execution_claim_unavailable"
    assert result.error.retryable is True


def test_provider_exception_preserves_machine_code_without_replay():
    class ProviderException(Exception):
        code = "NATIVE_RATE_LIMIT"
        server_key = "provider-node"

    class RaisingHandler:
        def dispatch(self, action, *, action_key, candidate):
            raise ProviderException("private provider detail")

        def reconcile(self, action, *, action_key, candidate):
            return None

    store = FakeStore(_candidate())
    result = SystemExecutor(store, {("test", "act"): RaisingHandler()}, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.error.code == "external_action_uncertain"
    assert result.error.source_code == "NATIVE_RATE_LIMIT"
    assert result.error.source == "provider-node"
    assert "private provider detail" not in result.model_dump_json()


def test_prior_alternative_with_same_identity_different_body_cannot_be_reused():
    current = _candidate()
    old_plan = current.proposal.model_dump(mode="json")
    alternate = current.proposal.model_dump(mode="json")
    alternate["actions"][0]["payload"] = {"value": "other"}
    prior = _candidate(outcome="needs_human", options=[
        {"key": "a", "label": "A", "instruction": "A", "consequence": "A", "plan": old_plan},
        {"key": "b", "label": "B", "instruction": "B", "consequence": "B", "plan": alternate},
    ])
    store = FakeStore(current)
    store.previous_candidate = prior
    store.previous_receipt = {"first_agent_run_id": 7}
    handler = Handler()
    result = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(store.get_reply_task(1), 1, 2)
    assert result.error.code == "completed_action_content_conflict"
    assert handler.calls == []


def test_prior_success_uses_persisted_selected_branch_not_other_option():
    current = _candidate()
    first = current.proposal.model_dump(mode="json")
    second = current.proposal.model_dump(mode="json")
    second["actions"][0]["payload"] = {"value": "other"}
    prior = _candidate(outcome="needs_human", options=[
        {"key": "a", "label": "A", "instruction": "A", "consequence": "A", "plan": first},
        {"key": "b", "label": "B", "instruction": "B", "consequence": "B", "plan": second},
    ])
    store = FakeStore(current)
    store.previous_candidate = prior
    store.previous_receipt = {"first_agent_run_id": 7, "external_action_key": "stable-key"}
    store.verified_source = {
        "candidate_json": prior.model_dump_json(),
        "branch_json": json.dumps(prior.decision_options[1].model_dump(mode="json")),
        "action_index": 0,
    }
    handler = Handler()
    result = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.error.code == "completed_action_content_conflict"
    assert handler.calls == []
    store.verified_source["branch_json"] = json.dumps(prior.decision_options[0].model_dump(mode="json"))
    result = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(
        store.get_reply_task(1), 1, 2,
    )
    assert result.outcome == "executed"


def test_native_dingtalk_handler_requires_exact_prepared_body_and_receipt():
    from app.outbound_postfix import PreparedOutboundMessage
    from app.system_action_handlers import DingTalkMessageHandler

    class NativeStore:
        def __init__(self):
            self.delivery_keys = []

        def get_reply_task(self, task_id):
            return type("Task", (), {"id": task_id, "business_object_key": "object:1", "execution_generation": "gen-1"})()

        def get_outbound_postfix(self, channel, delivery_key):
            self.delivery_keys.append(delivery_key)
            return PreparedOutboundMessage(channel, delivery_key, "Prepared text", "", "v1")

        def get_outbound_postfix_receipt(self, channel, delivery_key):
            return None

    class Sender:
        def __init__(self):
            self.calls = []

        def send_dingtalk_prepared(self, message, *, conversation_id, **target):
            self.calls.append((message.final_body, conversation_id, target))
            return type("Receipt", (), {"provider_result": {"success": True, "result": {"openMessageId": "m1"}}})()

    class Dws:
        def verify_message_send_result(self, result):
            return {"state": "sent"}

    store, sender = NativeStore(), Sender()
    handler = DingTalkMessageHandler(store, Dws(), sender=sender)
    action = _candidate(actions=[{
        "description": "Send", "action_identity": "first", "capability": "dingtalk-chat",
        "operation": "send_message", "target": {"conversation_id": "cid"},
        "payload": {"content": "Prepared text"},
    }]).proposal.actions[0]
    context = {"task_id": 1, "proposal_revision": 0}
    assert handler.dispatch(action, action_key="key", candidate=context).status == "verified"
    assert sender.calls[0][0:2] == ("Prepared text", "cid")
    bad = action.model_copy(update={"payload": {"content": "Unreviewed text"}})
    assert handler.dispatch(bad, action_key="key", candidate=context).status == "failed"
    assert len(sender.calls) == 1
    from app.service_message_sender import agent_message_delivery_key

    handler.dispatch(action, action_key="key", candidate={**context, "selection_id": 3, "option_key": "go"})
    assert store.delivery_keys[-1] == agent_message_delivery_key(
        business_object_key="object:1", action_identity="first:go",
        execution_generation="gen-1", proposal_revision=0,
    )
    direct = action.model_copy(update={"operation": "send_direct_message", "target": {
        "conversation_id": "source-cid", "user_id": "applicant-user",
        "open_dingtalk_id": "applicant-open-id",
    }})
    handler.dispatch(direct, action_key="direct-key", candidate=context)
    assert sender.calls[-1][1] is None
    assert sender.calls[-1][2]["user_id"] == "applicant-user"
    assert sender.calls[-1][2]["open_dingtalk_id"] is None


def test_calendar_response_requires_matching_provider_readback():
    from app.system_action_handlers import CalendarResponseHandler

    class Dws:
        def __init__(self):
            self.calls = []
            self.status = "needs_action"

        def respond_calendar_event(self, event_id, response_status):
            self.calls.append((event_id, response_status))
            return {"success": True}

        def get_calendar_event(self, event_id):
            return type("Event", (), {"event_id": event_id, "self_response_status": self.status})()

    dws = Dws()
    handler = CalendarResponseHandler(dws)
    action = _candidate(actions=[{
        "description": "Accept", "action_identity": "calendar", "capability": "dingtalk-calendar",
        "operation": "respond_calendar_event", "target": {"event_id": "e1"},
        "payload": {"response_status": "accepted"},
    }]).proposal.actions[0]
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"
    assert dws.calls == [("e1", "accepted")]
    dws.status = "accepted"
    assert handler.reconcile(action, action_key="key", candidate={}).status == "verified"
    assert dws.calls == [("e1", "accepted")]


def test_oa_action_requires_exact_completed_task_and_result_readback():
    from app.system_action_handlers import OaDecisionHandler

    class Dws:
        def __init__(self):
            self.calls = []
            self.result = ""
            self.status = "RUNNING"

        def execute_oa_approval_action(self, process_id, task_id, action, remark):
            self.calls.append((process_id, task_id, action, remark))
            return {"success": True}

        def read_oa_approval_detail(self, process_id):
            return {"result": {"processInstanceId": process_id, "tasks": [
                {"taskId": "task-1", "userId": "derek", "taskStatus": self.status,
                 "taskResult": self.result}]}}

        def get_current_user_id(self):
            return "derek"

    dws = Dws()
    action = _candidate(actions=[{
        "description": "Approve", "action_identity": "oa", "capability": "dingtalk-oa",
        "operation": "approve", "target": {"process_instance_id": "p1", "task_id": "task-1"},
        "payload": {"remark": "Approved"},
    }]).proposal.actions[0]
    handler = OaDecisionHandler(dws)
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"
    assert dws.calls == [("p1", "task-1", "通过", "Approved")]
    dws.status = "COMPLETED"
    dws.result = "AGREE"
    assert handler.reconcile(action, action_key="key", candidate={}).status == "verified"
    assert dws.calls == [("p1", "task-1", "通过", "Approved")]

    dws.status = "RUNNING"
    dws.result = ""
    dws.get_current_user_id = lambda: "someone-else"
    assert handler.dispatch(action, action_key="key", candidate={}).status == "business_state_changed"
    assert dws.calls == [("p1", "task-1", "通过", "Approved")]
    dws.get_current_user_id = lambda: ""
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"


def test_oa_native_detail_nested_tasks_and_integer_task_id_are_authoritative():
    from app.system_action_handlers import OaDecisionHandler

    class Dws:
        def __init__(self):
            self.status = "RUNNING"
            self.result = ""
            self.calls = []

        def read_oa_approval_detail(self, process_id):
            return {"success": True, "result": {"processInstanceId": process_id,
                "tasks": [{"taskId": 123, "userId": "derek",
                           "taskStatus": self.status, "taskResult": self.result}]}}

        def read_oa_approval_tasks(self, process_id):
            raise AssertionError("taskIdList has no owner or completion fields")

        def get_current_user_id(self):
            return "derek"

        def execute_oa_approval_action(self, process_id, task_id, decision, remark):
            self.calls.append((process_id, task_id, decision, remark))
            return {"success": True}

    dws = Dws()
    action = _candidate(actions=[{
        "description": "Approve", "action_identity": "oa", "capability": "dingtalk-oa",
        "operation": "approve", "target": {"process_instance_id": "p1", "task_id": "123"},
        "payload": {"remark": "Approved"},
    }]).proposal.actions[0]
    handler = OaDecisionHandler(dws)
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"
    assert dws.calls == [("p1", "123", "通过", "Approved")]
    dws.status, dws.result = "COMPLETED", "AGREE"
    assert handler.reconcile(action, action_key="key", candidate={}).status == "verified"


def test_oa_revert_uses_exact_activity_and_redirect_readback():
    from app.system_action_handlers import OaRevertHandler

    class Dws:
        def __init__(self):
            self.calls = []
            self.status = "RUNNING"

        def revert_oa_approval_task(self, **kwargs):
            self.calls.append(kwargs)
            return {"success": True}

        def read_oa_approval_detail(self, process_id):
            return {"result": {"processInstanceId": process_id, "tasks": [
                {"taskId": "task-1", "userId": "derek", "taskStatus": self.status,
                 "taskResult": "REDIRECT_PROCESS" if self.status == "COMPLETED" else ""}]}}

        def get_current_user_id(self):
            return "derek"

    dws = Dws()
    action = _candidate(actions=[{
        "description": "Return", "action_identity": "oa-return", "capability": "dingtalk-oa",
        "operation": "revert_task", "target": {"process_instance_id": "p1", "task_id": "task-1", "target_activity_id": "activity-2"},
        "payload": {"revert_action": "return", "remark": "Need material"},
    }]).proposal.actions[0]
    handler = OaRevertHandler(dws)
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"
    assert dws.calls[0]["target_activity_id"] == "activity-2"
    dws.status = "COMPLETED"
    assert handler.reconcile(action, action_key="key", candidate={}).status == "verified"


def test_oa_redirect_requires_exact_new_owner_task_readback():
    from app.system_action_handlers import OaRedirectHandler

    class Dws:
        def __init__(self):
            self.transferred = False
            self.calls = []

        def get_current_user_id(self):
            return "derek"

        def read_oa_approval_detail(self, process_id):
            tasks = [{"taskId": "task-1", "userid": "derek",
                      "taskStatus": "COMPLETED" if self.transferred else "RUNNING",
                      "taskResult": "REDIRECT" if self.transferred else ""}]
            if self.transferred:
                tasks.append({"taskId": "task-2", "userid": "reviewer", "taskStatus": "RUNNING"})
            return {"result": {"processInstanceId": process_id, "tasks": tasks}}

        def redirect_oa_approval_task(self, task_id, to_actioner_id, remark):
            self.calls.append((task_id, to_actioner_id, remark))
            return {"success": True}

    dws = Dws()
    action = _candidate(actions=[{
        "description": "Transfer", "action_identity": "oa-redirect", "capability": "dingtalk-oa",
        "operation": "redirect_task", "target": {"process_instance_id": "p1", "task_id": "task-1", "to_actioner_id": "reviewer"},
        "payload": {"remark": "Please handle"},
    }]).proposal.actions[0]
    handler = OaRedirectHandler(dws)
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"
    assert dws.calls == [("task-1", "reviewer", "Please handle")]
    dws.transferred = True
    assert handler.reconcile(action, action_key="key", candidate={}).status == "verified"


def test_doc_create_requires_provider_node_id_and_exact_action_content():
    from app.system_action_handlers import DocumentCreateHandler

    class Dws:
        def __init__(self):
            self.result = {"result": {"nodeId": "node-1"}}
            self.calls = []
            self.read_content = "# Report"

        def create_markdown_doc(self, name, content):
            self.calls.append((name, content))
            return self.result

        def doc_info(self, node_id):
            return {"result": {"nodeId": node_id, "name": "Report"}}

        def read_doc(self, node_id):
            return {"markdown": self.read_content}

    dws = Dws()
    action = _candidate(actions=[{
        "description": "Create", "action_identity": "doc", "capability": "dingtalk-doc",
        "operation": "create_document", "target": {"name": "Report"},
        "payload": {"content": "# Report"},
    }]).proposal.actions[0]
    handler = DocumentCreateHandler(dws)
    assert handler.dispatch(action, action_key="key", candidate={}).status == "verified"
    assert dws.calls == [("Report", "# Report")]
    dws.read_content = "different"
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"
    dws.result = {"success": True, "nodeId": ""}
    assert handler.dispatch(action, action_key="key", candidate={}).status == "uncertain"
    assert handler.reconcile(action, action_key="key", candidate={}) is None


def test_document_create_reconciles_known_node_without_duplicate_create():
    from app.system_action_handlers import DocumentCreateHandler

    class Dws:
        def __init__(self):
            self.content = "not-yet-visible"
            self.creates = 0

        def create_markdown_doc(self, name, content):
            self.creates += 1
            return {"result": {"nodeId": "node-1"}}

        def doc_info(self, node_id):
            return {"result": {"nodeId": node_id, "name": "Report"}}

        def read_doc(self, node_id):
            return {"markdown": self.content}

    dws = Dws()
    selected = _candidate(actions=[{
        "description": "Create", "action_identity": "report", "capability": "dingtalk-doc",
        "operation": "create_document", "target": {"name": "Report"},
        "payload": {"content": "# Report"},
    }])
    store = FakeStore(selected)
    executor = SystemExecutor(store, {("dingtalk-doc", "create_document"): DocumentCreateHandler(dws)}, owner="worker")
    assert executor.execute(store.get_reply_task(1), 1, 2).error.code == "external_action_uncertain"
    dws.content = "# Report"
    assert executor.execute(store.get_reply_task(1), 1, 2).outcome == "executed"
    assert dws.creates == 1


def test_doc_comment_and_oa_comment_require_stable_provider_comment_identity():
    from app.system_action_handlers import DocumentCommentHandler, OaCommentHandler

    class Dws:
        def __init__(self):
            self.result = {"success": True, "commentKey": "comment-1"}
            self.calls = []

        def create_doc_comment(self, node_id, content):
            self.calls.append(("doc", node_id, content))
            return self.result

        def comment_oa_approval(self, process_id, content):
            self.calls.append(("oa", process_id, content))
            return self.result

    dws = Dws()
    doc_action = _candidate(actions=[{
        "description": "Comment", "action_identity": "doc-comment", "capability": "dingtalk-doc",
        "operation": "create_doc_comment", "target": {"node_id": "node-1"},
        "payload": {"content": "Please revise"},
    }]).proposal.actions[0]
    oa_action = _candidate(actions=[{
        "description": "Comment", "action_identity": "oa-comment", "capability": "dingtalk-oa",
        "operation": "comment", "target": {"process_instance_id": "p1"},
        "payload": {"content": "Please revise"},
    }]).proposal.actions[0]
    assert DocumentCommentHandler(dws).dispatch(doc_action, action_key="key", candidate={}).status == "verified"
    assert OaCommentHandler(dws).dispatch(oa_action, action_key="key", candidate={}).status == "verified"
    dws.result = {"success": True}
    assert DocumentCommentHandler(dws).dispatch(doc_action, action_key="key", candidate={}).status == "uncertain"
    assert OaCommentHandler(dws).dispatch(oa_action, action_key="key", candidate={}).status == "uncertain"
    assert len(dws.calls) == 4


def test_emoji_and_text_emotion_only_complete_with_provider_reaction_identity():
    from app.system_action_handlers import MessageEmotionHandler

    class Dws:
        def __init__(self):
            self.result = {"success": True, "reactionId": "reaction-1"}
            self.calls = []
            self.reply_users = ["derek"]

        def add_message_emoji(self, conversation_id, message_id, emoji):
            self.calls.append(("emoji", conversation_id, message_id, emoji))
            return self.result

        def add_message_text_emotion(self, conversation_id, message_id, **kwargs):
            self.calls.append(("text", conversation_id, message_id, kwargs))
            return self.result

        def list_message_emotion_replies(self, message_id):
            return {"result": {"messages": [{"openMessageId": message_id,
                "emotionReplyList": [{"emoji": "👍", "replyUsers": self.reply_users}]}]}}

        def get_current_user_id(self):
            return "derek"

    dws = Dws()
    emoji = _candidate(actions=[{
        "description": "React", "action_identity": "emoji", "capability": "dingtalk-chat",
        "operation": "add_message_emoji", "target": {"conversation_id": "cid", "message_id": "mid"},
        "payload": {"emoji": "👍"},
    }]).proposal.actions[0]
    text_emotion = _candidate(actions=[{
        "description": "React", "action_identity": "text-emotion", "capability": "dingtalk-chat",
        "operation": "add_message_text_emotion", "target": {"conversation_id": "cid", "message_id": "mid"},
        "payload": {"text": "hello", "emotion_id": "e1", "emotion_name": "Smile", "background_id": "b1"},
    }]).proposal.actions[0]
    handler = MessageEmotionHandler(dws)
    assert handler.dispatch(emoji, action_key="key", candidate={}).status == "verified"
    assert handler.dispatch(text_emotion, action_key="key", candidate={}).status == "verified"
    dws.result = {"success": True}
    assert handler.dispatch(emoji, action_key="key", candidate={}).status == "verified"
    assert handler.reconcile(emoji, action_key="key", candidate={}).status == "verified"
    dws.reply_users = ["someone-else"]
    assert handler.reconcile(emoji, action_key="key", candidate={}).status == "uncertain"


def test_text_emotion_creation_requires_provider_emotion_id():
    from app.system_action_handlers import TextEmotionCreateHandler

    class Dws:
        def create_message_text_emotion(self, *, text, emotion_name, background_id):
            assert (text, emotion_name, background_id) == ("Thanks", "Appreciation", "bg-1")
            return {"result": {"emotionId": "emotion-1"}}

    action = _candidate(actions=[{
        "description": "Create", "action_identity": "emotion", "capability": "dingtalk-chat",
        "operation": "create_message_text_emotion", "target": {"resource": "text_emotion_template"},
        "payload": {"text": "Thanks", "emotion_name": "Appreciation", "background_id": "bg-1"},
    }]).proposal.actions[0]
    assert TextEmotionCreateHandler(Dws()).dispatch(action, action_key="key", candidate={}).status == "verified"


def test_sqlite_reviewed_execution_preserves_receipt_across_restart(tmp_path):
    from app.store import AgentRole, AutoReplyStore
    from app.reviewed_candidates import candidate_digest

    path = tmp_path / "executor.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="executor-test", conversation_title="Test",
        single_chat=True, trigger_message_id="trigger", trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00", trigger_text="test",
    )
    task = store.claim_reply_tasks(1)[0]
    consumer = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.CONSUMER, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=None, operation_id="", owner="consumer").run
    result = _candidate()
    store.complete_agent_run(consumer.id, result.model_dump(mode="json"), owner="consumer")
    candidate = store.persist_review_candidate(task, consumer, result)
    audit = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=consumer.id, operation_id="audit-test", owner="audit").run
    review_body = {"outcome": "approve", "candidate_digest": candidate_digest(result)}
    store.complete_agent_run(audit.id, review_body, owner="audit")
    review = store.record_candidate_review(candidate["id"], audit.id, review_body)
    handler = Handler()
    executed = SystemExecutor(store, {("test", "act"): handler}, owner="worker").execute(task, candidate["id"], review["id"])
    assert executed.outcome == "executed"
    assert handler.calls == ["first"]
    reopened = AutoReplyStore(path)
    receipt = reopened.get_candidate_external_action(executed.external_result.operation_id)
    assert receipt is not None
    source = reopened.get_verified_action_source(executed.external_result.operation_id)
    assert source["candidate_id"] == candidate["id"]
    assert source["action_index"] == 0
    assert source["branch_json"] is None
    assert SystemExecutor(reopened, {("test", "act"): handler}, owner="worker").execute(task, candidate["id"], review["id"]).outcome == "executed"
    assert handler.calls == ["first"]


def test_selected_stop_remains_skippable_after_historical_provider_risk_refusal(tmp_path):
    from app.store import AgentRole, AutoReplyStore
    from app.reviewed_candidates import candidate_digest

    store = AutoReplyStore(tmp_path / "stopped.sqlite3")
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="stop-test", conversation_title="Test",
        single_chat=True, trigger_message_id="trigger", trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00", trigger_text="test",
    )
    task = store.claim_reply_tasks(1)[0]
    consumer = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.CONSUMER, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=None, operation_id="", owner="consumer").run
    result = _candidate(outcome="needs_human", options=[
        {"key": "go", "label": "Go", "instruction": "Act", "consequence": "Effect",
         "plan": _candidate().proposal.model_dump(mode="json")},
        {"key": "stop", "label": "Stop", "instruction": "Stop", "consequence": "No effect",
         "terminal_outcome": "skipped", "reason": "Requested stop"},
    ])
    store.complete_agent_run(consumer.id, result.model_dump(mode="json"), owner="consumer")
    reviewed = store.persist_review_candidate(task, consumer, result)
    audit = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=consumer.id, operation_id="audit-stop", owner="audit").run
    review_body = {"outcome": "approve", "candidate_digest": candidate_digest(result)}
    store.complete_agent_run(audit.id, review_body, owner="audit")
    review = store.record_candidate_review(reviewed["id"], audit.id, review_body)
    refusal = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=1,
        parent_agent_run_id=consumer.id, operation_id="audit-refusal", owner="refusal").run
    store.fail_agent_run(refusal.id, {"code": "provider_risk_rejected", "retryable": False}, owner="refusal")
    store.select_candidate_option(reviewed["id"], review["id"], "stop")
    execution = SystemExecutor(store, {("test", "act"): Handler()}, owner="worker").execute(
        task, reviewed["id"], review["id"],
    )
    assert execution.outcome == "skipped"
    assert store.get_candidate_execution(reviewed["id"])["status"] == "skipped"
