"""Focused guards for the current Action Entity native acceptance harness."""

import hashlib
import pytest
from types import SimpleNamespace

from scripts.validate_native_action_entity_lifecycle import (
    _acceptance_instruction,
    _approved_audit_run_id,
    _audit_run_id,
    _assert_old_cold_write_fact,
    _assert_old_native_sent_read,
    _case_fixture,
    _print_event,
    _assert_native_current_facts_equal,
    _assert_provider_receipt_matches,
    _assert_settlement_recovery,
    _stable_inverse_facts,
)


def test_native_action_case_requests_system_effect_without_contradicting_sink():
    instruction = _acceptance_instruction()

    assert 'send_group_message' in instruction
    assert '合成群的完整成员、角色和接收适配信息' in instruction
    assert '系统动作注册表仅声明 send_group_message' in instruction
    assert 'SystemExecutor' in instruction
    assert '本地内存测试替身' in instruction
    assert '没有真实 provider 客户端或网络路径' in instruction
    assert '不要调用任何直接发送工具' in instruction
    assert '不要发送' not in instruction
    assert '禁止发送' not in instruction


def test_inverse_fact_comparison_preserves_business_facts_while_ignoring_only_local_owner_ids():
    before = {"sent_replies": [{"id": 7, "conversation_id": "synthetic", "reply_text": "approved body",
                               "send_result_json": '{"message_id":"native-receipt"}', "_action_id": 101}],
              "outbound_postfixes": []}
    after = {"sent_replies": [{"id": 7, "conversation_id": "synthetic", "reply_text": "approved body",
                              "send_result_json": '{"message_id":"native-receipt"}', "_action_id": 900}],
             "outbound_postfixes": [{"channel": "dingtalk", "delivery_key": "test-cold-write",
                                     "final_body": "old runtime preparation"}]}

    assert _stable_inverse_facts(before) == _stable_inverse_facts(after, exclude_delivery_key="test-cold-write")
    after["sent_replies"][0]["send_result_json"] = '{"message_id":"different-receipt"}'
    assert _stable_inverse_facts(before) != _stable_inverse_facts(after, exclude_delivery_key="test-cold-write")


def test_old_cold_prepare_compares_its_own_body_not_the_native_sent_body():
    facts = {"outbound_postfixes": [{"delivery_key": "test-cold-write", "final_body": "prepared body"}]}

    _assert_old_cold_write_fact(facts, "test-cold-write", "prepared body")
    with pytest.raises(AssertionError, match="cold-write fact"):
        _assert_old_cold_write_fact(facts, "test-cold-write", "native sent body")


def test_old_public_sent_read_requires_exact_body_receipt_and_run_identity():
    body = "native sent body"
    receipt = '{"receipt":"provider"}'
    sent = {"reply_text": body, "send_result_json": receipt, "agent_run_id": 19}
    receipt_digest = hashlib.sha256(receipt.encode()).hexdigest()

    _assert_old_native_sent_read(sent, body, receipt_digest, 19)
    with pytest.raises(AssertionError, match="body or run"):
        _assert_old_native_sent_read(sent, "different body", receipt_digest, 19)
    with pytest.raises(AssertionError, match="provider receipt"):
        _assert_old_native_sent_read(sent, body, hashlib.sha256(b"different").hexdigest(), 19)
    with pytest.raises(AssertionError, match="body or run"):
        _assert_old_native_sent_read(sent, body, receipt_digest, 20)


def test_native_fact_roundtrip_matches_typed_keys_and_only_allocates_missing_source_ids():
    before = {
        "external_action_results": [],
        "candidate_action_attempts": [{"execution_id": 10, "action_index": 0,
                                        "id": None, "result_json": None, "status": "sent"},
                                       {"execution_id": 10, "action_index": 1,
                                        "id": 30, "result_json": None, "status": "sent"}],
        "outbound_postfixes": [],
        "outbound_postfix_receipts": [],
        "native_reply_dispatches": [],
        "sent_replies": [{"conversation_id": "synthetic", "trigger_message_id": "trigger",
                           "sent_at": "time", "agent_run_id": 19, "reply_text": "native body",
                           "send_result_json": '{"receipt":"copy"}'}],
        "sent_reply_observers": [{"agent_run_id": 19, "reply_task_id": 7,
                                  "created_at": "time", "_legacy_identity": None}],
        "email_actions": [],
        "email_action_attempts": [],
    }
    after = {family: [dict(row) for row in rows] for family, rows in before.items()}
    after["candidate_action_attempts"][0]["id"] = 120
    after["sent_replies"][0]["id"] = 121
    after["sent_replies"][0]["_action_id"] = 900
    after["sent_reply_observers"][0]["_action_id"] = 900
    after["sent_reply_observers"][0]["_legacy_identity"] = (121, 19)

    _assert_native_current_facts_equal(before, after, exclude_delivery_key="cold-write")

    after["candidate_action_attempts"][1]["id"] = 31
    with pytest.raises(AssertionError, match="existing native candidate_action_attempts source identity changed"):
        _assert_native_current_facts_equal(before, after, exclude_delivery_key="cold-write")
    after["candidate_action_attempts"][1]["id"] = 30
    after["sent_reply_observers"][0]["_legacy_identity"] = (999, 19)
    with pytest.raises(AssertionError, match="observer Sent reference changed typed identity"):
        _assert_native_current_facts_equal(before, after, exclude_delivery_key="cold-write")
    after["sent_reply_observers"][0]["_legacy_identity"] = (121, 19)
    after["sent_replies"][0]["send_result_json"] = '{"receipt":"different"}'
    with pytest.raises(AssertionError, match="business fact values changed"):
        _assert_native_current_facts_equal(before, after, exclude_delivery_key="cold-write")


def test_synthetic_roster_has_complete_stable_identity_and_current_responsibility_facts():
    group = _case_fixture("synthetic-conversation")["synthetic_group"]

    assert group["conversation_id"] == "synthetic-conversation"
    assert "complete synthetic roster" in group["membership_basis"]
    assert "no prior messages" in group["conversation_state"]
    assert [member["stable_id"] for member in group["members"]] == [
        "synthetic-member-001", "synthetic-member-002",
    ]
    assert all(member["title"] and member["responsibility"] and member["fit"] for member in group["members"])


def test_native_event_output_serializes_before_flushing(capsys):
    _print_event({"event": "native_case_result_recorded", "sends": 0})

    assert capsys.readouterr().out.strip() == '{"event": "native_case_result_recorded", "sends": 0}'


def test_sent_action_links_to_the_single_audit_run():
    runs = [SimpleNamespace(id=17, role=SimpleNamespace(value="consumer")),
            SimpleNamespace(id=19, role=SimpleNamespace(value="audit"))]

    assert _audit_run_id(runs) == 19
    with pytest.raises(AssertionError, match="expected one audit run"):
        _audit_run_id(runs[:1])


def test_revision_sent_action_links_to_the_audit_run_that_approved_its_candidate():
    reviews = [{"decision": "return", "audit_run_id": 19},
               {"decision": "approve", "audit_run_id": 23}]

    assert _approved_audit_run_id(reviews) == 23
    with pytest.raises(AssertionError, match="expected one approved candidate review"):
        _approved_audit_run_id(reviews + [{"decision": "approve", "audit_run_id": 29}])


def test_provider_receipt_matches_the_persisted_provider_response_and_verification():
    provider_result = {"success": True, "result": {"openMessageId": "native-copy-provider-receipt"}}
    verification = {"state": "sent", "message_id": "native-copy-provider-receipt"}
    receipt = {"provider_result": provider_result, "verification": verification}

    _assert_provider_receipt_matches(receipt, provider_result, verification)
    with pytest.raises(AssertionError, match="provider result or verification"):
        _assert_provider_receipt_matches(receipt, provider_result, {"state": "uncertain"})


def test_settlement_recovery_requires_one_native_approval_and_reuses_one_receipt():
    report = {"status": "done", "completed": True, "interrupted": True,
        "native_runs": [{"role": "consumer"}, {"role": "audit"}],
        "provider_substitute_sends": 1, "provider_substitute_verifications": 1,
        "provider_receipt_rows_for_task": 1, "sent_rows_for_task": 1}

    _assert_settlement_recovery(report)
    report["provider_substitute_sends"] = 2
    with pytest.raises(AssertionError, match="reuse the acknowledged effect"):
        _assert_settlement_recovery(report)
