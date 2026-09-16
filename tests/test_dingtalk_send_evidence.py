from __future__ import annotations

import json
from types import SimpleNamespace

from app.dingtalk_send_evidence import DingTalkSendEvidenceDriver
from app.store import AgentRole


def _proposal(action: dict) -> str:
    return json.dumps(
        {
            "outcome": "proposal",
            "risk": "low",
            "confidence": 0.85,
            "rule_coverage": 0.9,
            "information_completeness": 0.4,
            "summary": "ask",
            "proposal": {
                "objective": "ask",
                "actions": [action],
                "sourced_facts": [],
                "authored_judgment": "",
            },
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        },
        ensure_ascii=False,
    )


CHAT_SEND = {
    "description": "向 Melody 请求做出判断所需的关键材料",
    "action_identity": "request_candidate_info",
    "capability": "dingtalk-chat",
    "operation": "send",
    "target": {"conversation_id": "cid-1"},
    "payload": {"content": "简历关键信息能否直接发我？"},
}

CALENDAR_RESPOND = {
    "description": "接受会议邀请",
    "action_identity": "accept-invite",
    "capability": "dingtalk-calendar",
    "operation": "event_respond",
    "target": {"event_id": "ev-1"},
    "payload": {"response": "accept"},
}


def _receipt_event(receipt: str):
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "exit_code": 0,
            "output": json.dumps({"result": {"openTaskId": receipt}, "success": True}),
        },
    }


def _driver(*, action: dict, tool_events: list):
    task = SimpleNamespace(
        id=384224, channel="dingtalk", execution_generation="initial"
    )
    audit_run = SimpleNamespace(
        id=19557, role=AgentRole.AUDIT, proposal_revision=0,
        final_result_json="", tool_events=tool_events,
    )
    consumer_run = SimpleNamespace(
        id=19556,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        final_result_json=_proposal(action),
    )
    store = SimpleNamespace(
        get_agent_run=lambda _id: audit_run,
        list_agent_runs_for_task_generation=lambda *_a: [consumer_run, audit_run],
    )
    return DingTalkSendEvidenceDriver(store), task


def test_a_send_claimed_without_any_provider_call_has_no_evidence() -> None:
    """Task 384224: the turn ran no send and reported the trigger's own id.

    It closed as done with an empty delivery ledger, so the question it was
    supposed to put to the sender was never asked by anyone.
    """
    driver, task = _driver(action=CHAT_SEND, tool_events=[])
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_a_send_the_provider_accepted_has_evidence() -> None:
    driver, task = _driver(
        action=CHAT_SEND, tool_events=[_receipt_event("T4iUBCTxjrrq=")]
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_a_send_identified_by_reading_the_conversation_back_still_has_evidence() -> None:
    """Task 307731 sent for real, then named its message from a list read.

    The send receipt is an openTaskId; the message id it reported came from
    `+chat-messages`.  Demanding that the reported id be the receipt would
    block a delivered message, and the retry would send it twice.
    """
    readback = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "exit_code": 0,
            "output": json.dumps({"messages": [{"messageId": "msgefjRVDoO6="}]}),
        },
    }
    driver, task = _driver(
        action=CHAT_SEND, tool_events=[_receipt_event("T4iUBCTxjrrq="), readback]
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_an_action_the_service_prepared_no_message_for_is_not_gated_here() -> None:
    """Calendar, approval and reaction effects carry other provider identities.

    This receipt shape does not describe them, so answering for them would
    block honest work rather than catch anything.
    """
    driver, task = _driver(action=CALENDAR_RESPOND, tool_events=[])
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_a_task_on_another_channel_keeps_its_own_contract() -> None:
    driver, task = _driver(action=CHAT_SEND, tool_events=[])
    task.channel = "email"
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_the_correction_names_the_receipt_rather_than_the_claim() -> None:
    driver, _ = _driver(action=CHAT_SEND, tool_events=[])
    requirement = driver.execution_evidence_requirement()
    assert "receipt" in requirement
    assert "not a receipt" in requirement
