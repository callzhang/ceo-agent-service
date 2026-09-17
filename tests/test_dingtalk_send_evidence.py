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


def _driver(*, action: dict, tool_events: list, classifier=None):
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
    return DingTalkSendEvidenceDriver(store, classifier=classifier), task


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


def test_a_task_on_another_channel_keeps_its_own_contract() -> None:
    driver, task = _driver(action=CHAT_SEND, tool_events=[])
    task.channel = "email"
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_the_correction_names_the_receipt_rather_than_the_claim() -> None:
    driver, _ = _driver(action=CHAT_SEND, tool_events=[])
    requirement = driver.execution_evidence_requirement()
    assert "receipt" in requirement
    assert "not a receipt" in requirement


class _SchemaClassifier:
    """Stand-in for DWS's own schema: respond writes, get and list only read."""

    def classify(self, command):
        from app.agent_result import EffectKind
        from app.native_cli_metadata import native_command_argv

        argv = native_command_argv(command)
        if argv is None or "--help" in argv:
            return None
        path = " ".join(argv[1:4])
        ids = {}
        if "--id" in argv:
            ids["id"] = argv[argv.index("--id") + 1]
        effect = {
            "calendar event respond": EffectKind.EFFECTFUL,
            "calendar event get": EffectKind.READ_ONLY,
            "chat +messages-send --group": EffectKind.EFFECTFUL,
        }.get(path)
        if effect is None:
            return None
        return SimpleNamespace(command_path=path, effect=effect, target_identifiers=ids)


RESPOND = {
    "description": "接受会议邀请",
    "action_identity": "accept-invite",
    "capability": "dingtalk-calendar",
    "operation": "event_response",
    "target": {"event_id": "VG9xMTg5THNpWldxREcwSzZvNms3QT09"},
    "payload": {},
}


def _shell(command: str, output: str = '{"result":{},"success":true}'):
    return {
        "type": "item.completed",
        "item": {"type": "command_execution", "exit_code": 0,
                 "command": f"/bin/zsh -lc '{command}'", "aggregated_output": output},
    }


def test_a_calendar_response_claimed_without_responding_has_no_evidence() -> None:
    """Audit run 19644 read the invite, listed the calendar, and stopped there.

    It then reported the invitation accepted. 19 of 56 September calendar runs
    that claimed executed never made an effectful call at all.
    """
    driver, task = _driver(
        action=RESPOND,
        tool_events=[_shell("dws calendar event get --id VG9xMTg5THNpWldxREcwSzZvNms3QT09 --format json")],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_a_response_on_the_proposed_event_is_evidence() -> None:
    driver, task = _driver(
        action=RESPOND,
        tool_events=[_shell("dws calendar event respond --id VG9xMTg5THNpWldxREcwSzZvNms3QT09 --status accepted --format json")],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_an_effect_on_something_other_than_the_proposed_event_is_not_evidence() -> None:
    """Three September runs claimed a calendar response and only sent a message.

    Any effectful call would have passed them; the effect has to land on the
    event the proposal named.
    """
    driver, task = _driver(
        action=RESPOND,
        tool_events=[_shell("dws chat +messages-send --group cid-1 --text 好的")],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_asking_for_help_on_respond_is_not_a_response() -> None:
    driver, task = _driver(
        action=RESPOND,
        tool_events=[_shell("dws calendar event respond --help")],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_a_response_through_the_reviewed_cli_tool_counts() -> None:
    mcp = {"type": "item.completed", "item": {
        "type": "mcp_tool_call", "server": "agent_cli", "tool": "execute_reviewed_write",
        "error": None, "result": {"content": [{"type": "text", "text": "{}"}]},
        "arguments": {"argv": ["dws", "calendar", "event", "respond", "--id",
                               "VG9xMTg5THNpWldxREcwSzZvNms3QT09", "--status", "accepted"]},
    }}
    driver, task = _driver(action=RESPOND, tool_events=[mcp], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_a_reaction_is_not_gated_here() -> None:
    """A reaction returns `result: {}` with no identifier to require."""
    reaction = {
        "description": "点赞", "action_identity": "react", "capability": "dingtalk-chat",
        "operation": "messages-add-emoji", "target": {"message_id": "msg-1"},
        "payload": {"emoji": "👍"},
    }
    driver, task = _driver(action=reaction, tool_events=[], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_the_correction_says_what_to_do_when_no_calendar_write_is_needed() -> None:
    driver, _ = _driver(action=RESPOND, tool_events=[], classifier=_SchemaClassifier())
    requirement = driver.execution_evidence_requirement()
    assert "feedback_provided" in requirement
    assert "respond call on the proposed event" in requirement
