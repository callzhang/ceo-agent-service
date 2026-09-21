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


def _driver(*, action=None, actions=None, tool_events: list, classifier=None, parent=True):
    task = SimpleNamespace(
        id=384224, channel="dingtalk", execution_generation="initial"
    )
    consumer_run = SimpleNamespace(
        id=19556,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        final_result_json=_proposal_with(actions if actions is not None else [action]),
    )
    audit_run = SimpleNamespace(
        id=19557, role=AgentRole.AUDIT, proposal_revision=0,
        parent_agent_run_id=19556 if parent else None,
        final_result_json="", tool_events=tool_events,
    )
    runs = {19556: consumer_run, 19557: audit_run}
    store = SimpleNamespace(get_agent_run=lambda run_id: runs.get(run_id))
    return DingTalkSendEvidenceDriver(store, classifier=classifier), task


def _proposal_with(actions: list) -> str:
    payload = json.loads(_proposal(actions[0]))
    payload["proposal"]["actions"] = actions
    return json.dumps(payload, ensure_ascii=False)


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
        for flag in ("--id", "--message-id", "--instance-id", "--node"):
            if flag in argv:
                ids[flag.strip("-")] = argv[argv.index(flag) + 1]
        effect = {
            "calendar event respond": EffectKind.EFFECTFUL,
            "calendar event get": EffectKind.READ_ONLY,
            "chat +messages-send --group": EffectKind.EFFECTFUL,
            "chat message add-emoji": EffectKind.EFFECTFUL,
            "oa approval approve": EffectKind.EFFECTFUL,
            "oa approval detail": EffectKind.READ_ONLY,
            "doc comment create": EffectKind.EFFECTFUL,
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


def test_the_correction_says_what_to_do_when_no_calendar_write_is_needed() -> None:
    driver, _ = _driver(action=RESPOND, tool_events=[], classifier=_SchemaClassifier())
    requirement = driver.execution_evidence_requirement()
    assert "feedback_provided" in requirement
    assert "write call on the object the proposal named" in requirement


REACTION = {
    "description": "表情回复", "action_identity": "react", "capability": "dingtalk-chat",
    "operation": "add-emoji",
    "target": {"conversation_id": "cidecoVMQj5AbsnpzlPqHbyQw==", "message_id": "msgyXmTAdXUx3cezX0pO1ppLA=="},
    "payload": {"emoji": "收到"},
}

APPROVE = {
    "description": "批准请假", "action_identity": "approve", "capability": "dingtalk-oa-approval",
    "operation": "approve",
    "target": {"process_instance_id": "mgprBD0wT1Sr6WqM3Qkr_A03641789432389"},
    "payload": {"remark": "同意"},
}


def test_a_reaction_the_provider_accepted_counts_even_with_its_output_redirected() -> None:
    """Audit run 19561 added the emoji and got `success: true` back.

    The command ended in `2>&1`, which the native classifier refuses to parse,
    so the real reaction read as no reaction. What ran is the first stage.
    """
    driver, task = _driver(
        action=REACTION,
        tool_events=[_shell('dws chat message add-emoji --conversation-id cidecoVMQj5AbsnpzlPqHbyQw== --message-id msgyXmTAdXUx3cezX0pO1ppLA== --emoji "收到" 2>&1')],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_a_reaction_claimed_without_reacting_has_no_evidence() -> None:
    driver, task = _driver(action=REACTION, tool_events=[], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_an_approval_claimed_without_approving_has_no_evidence() -> None:
    """Reading the approval and notifying the applicant is not approving it."""
    driver, task = _driver(
        actions=[APPROVE, CHAT_SEND],
        tool_events=[
            _shell("dws oa approval detail --instance-id mgprBD0wT1Sr6WqM3Qkr_A03641789432389 --format json"),
            _shell("dws chat +messages-send --group cid-1 --text 已同意", output='{"result":{"openTaskId":"t-1"},"success":true}'),
        ],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_an_approval_on_the_proposed_instance_counts() -> None:
    driver, task = _driver(
        action=APPROVE,
        tool_events=[_shell("dws oa approval approve --instance-id mgprBD0wT1Sr6WqM3Qkr_A03641789432389 --format json")],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_a_write_command_that_printed_a_dws_error_is_not_evidence() -> None:
    """Audit run 20012 reported an approval executed after only a failed comment call.

    `dws oa approval oa-comments ... | head -150` printed `--content is required`
    but the pipeline exited 0 through `head`, so the failed write counted.
    """
    driver, task = _driver(
        action=APPROVE,
        tool_events=[
            _shell(
                "dws oa approval approve --instance-id mgprBD0wT1Sr6WqM3Qkr_A03641789432389 --format json 2>&1 | head -150",
                output='{\n  "error": {\n    "category": "internal",\n    "code": 5,\n    "message": "--content is required"\n  }\n}\n',
            )
        ],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_a_send_under_another_capability_spelling_is_still_gated() -> None:
    """September spelled chat as dingtalk-chat, dingtalk_chat, dingtalk chat and dws chat.

    A gate keyed on one spelling let run 8302's `dingtalk_chat` send through.
    """
    send = dict(CHAT_SEND, capability="dingtalk_chat")
    driver, task = _driver(action=send, tool_events=[], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_a_document_comment_is_satisfied_by_its_write_without_a_message_receipt() -> None:
    comment = {
        "description": "评论文档", "action_identity": "comment", "capability": "dingtalk-doc",
        "operation": "comment_create", "target": {"node": "N7dx2rn0JbRoGDyBfNKmazywJMGjLRb3"},
        "payload": {"content": "第三节的数据口径需要统一。"},
    }
    driver, task = _driver(
        action=comment,
        tool_events=[_shell("dws doc comment create --node N7dx2rn0JbRoGDyBfNKmazywJMGjLRb3 --content x")],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_an_object_reached_only_through_a_third_party_tool_is_not_judged() -> None:
    """The interview system is an MCP server the service cannot classify.

    Run 19653 really uploaded the interview result there. The service cannot
    tell that write from a read, so it does not refuse it.
    """
    upload = {
        "description": "上传面试结果", "action_identity": "upload", "capability": "xiaoqing-interview",
        "operation": "upload_interview_result",
        "target": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"}, "payload": {},
    }
    mcp = {"type": "item.completed", "item": {
        "type": "mcp_tool_call", "server": "xiaoqing_interview", "tool": "upload_interview_result",
        "error": None, "arguments": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"},
        "result": {"content": [{"type": "text", "text": json.dumps(
            {"status": "ok", "data": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"}})}]},
    }}
    driver, task = _driver(action=upload, tool_events=[mcp], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_a_run_whose_reviewed_proposal_cannot_be_found_has_no_evidence() -> None:
    """Finding nothing must not read as "nothing was proposed"."""
    driver, task = _driver(action=CHAT_SEND, tool_events=[], classifier=_SchemaClassifier(), parent=False)
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_command_substitution_is_never_read_as_what_ran() -> None:
    from app.dingtalk_send_evidence import _first_stage_argv

    assert _first_stage_argv("/bin/zsh -lc 'dws calendar event respond --id $(cat id)'") is None
    assert _first_stage_argv('dws chat +dm --to "张毅倜" --content "a|b > c"') == (
        "dws", "chat", "+dm", "--to", "张毅倜", "--content", "a|b > c"
    )


UPLOAD = {
    "description": "提交面评", "action_identity": "upload", "capability": "xiaoqing-interview",
    "operation": "upload_interview_result",
    "target": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"}, "payload": {},
}


def _interview_call(tool: str, payload: dict):
    return {"type": "item.completed", "item": {
        "type": "mcp_tool_call", "server": "xiaoqing_interview", "tool": tool,
        "error": None, "arguments": {},
        "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
    }}


def test_a_reviewed_third_party_write_is_evidence() -> None:
    """Run 19653 really uploaded the evaluation through the interview server."""
    driver, task = _driver(
        action=UPLOAD,
        tool_events=[_interview_call("upload_interview_result", {
            "status": "ok",
            "data": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"}})],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_a_reviewed_third_party_read_is_not_evidence() -> None:
    """Run 9611 claimed the upload and only listed and read the candidate.

    Before the server's catalogue was recorded, any successful call to it left
    the action unjudged, so reading the object passed as writing to it.
    """
    driver, task = _driver(
        action=UPLOAD,
        tool_events=[
            _interview_call("list_candidate_interviews", {
                "status": "ok", "data": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"}}),
            _interview_call("get_interview_context", {
                "status": "ok", "data": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"}}),
        ],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_a_tool_the_catalogue_does_not_list_stays_unjudged() -> None:
    """Adding a tool to a server must not start refusing that server's work."""
    driver, task = _driver(
        action=UPLOAD,
        tool_events=[_interview_call("some_future_tool", {
            "status": "ok", "data": {"interview_id": "int-97c2d24b-79f7-4cb7-bb34-4b049e3ce9ca"}})],
        classifier=_SchemaClassifier(),
    )
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_an_action_declared_to_change_nothing_needs_no_receipt() -> None:
    """Task 384361 proposed a single calendar `no-op` and nothing else.

    Audit reported it executed, the gate refused six times because no write had
    happened, and the task exhausted its retries and failed -- for a proposal
    whose whole content was that nothing needed doing.
    """
    no_op = dict(RESPOND, operation="no-op", effect="none")
    driver, task = _driver(action=no_op, tool_events=[], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_declaring_no_effect_cannot_slip_a_message_past_the_gate() -> None:
    send = dict(CHAT_SEND, effect="none")
    driver, task = _driver(action=send, tool_events=[], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


def test_an_action_that_declares_nothing_is_still_held_to_evidence() -> None:
    """Every proposal written before this field exists omits it."""
    assert "effect" not in RESPOND
    driver, task = _driver(action=RESPOND, tool_events=[], classifier=_SchemaClassifier())
    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False


REVERT = {
    "description": "退回审批给发起人补充材料",
    "action_identity": "revert-approval",
    "capability": "dingtalk-oa-approval",
    "operation": "approval_revert",
    "target": {"instance_id": "UGl6QdiSRau-rm7OWXvxgQ0364"},
    "payload": {},
}


class _NoSchemaClassifier:
    """DWS publishes no runtime schema for revert-task, so it types nothing."""

    def classify(self, command):
        return None


def test_a_revert_counts_although_dws_publishes_no_schema_for_it() -> None:
    """Run 20070 sent the 江淮 POC back to Wayne and the task still failed.

    DingTalk recorded REDIRECT_PROCESS and the approval left the pending list,
    but `oa approval revert-task` has no runtime schema, so the classifier
    typed nothing, the effect left no trace in the evidence, and Audit reported
    provider_receipt_missing. The execution path already falls back to the
    registered-write list; this gate has to use the same fallback or a real
    irreversible action reads as unproven.
    """

    driver, task = _driver(
        action=REVERT,
        tool_events=[
            _shell(
                "dws oa approval revert-task --instance-id UGl6QdiSRau-rm7OWXvxgQ0364"
                " --task-id 103412315620 --target-activity-id sid-startevent"
                " --action REVERT_FOR_RESUBMIT --remark 补齐后重新提交 --yes --format json"
            )
        ],
        classifier=_NoSchemaClassifier(),
    )

    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is True


def test_history_provider_writes_do_not_launch_schema_discovery(monkeypatch) -> None:
    from app.dingtalk_send_evidence import completed_provider_writes

    monkeypatch.setattr(
        "app.native_cli_metadata.run_bounded_process",
        lambda *_args, **_kwargs: pytest.fail("history must not launch DWS schema"),
    )
    events = [
        _shell(
            "dws oa approval revert-task --instance-id instance-1"
            " --task-id task-1 --target-activity-id start"
            " --action REVERT_FOR_RESUBMIT --remark 补齐后重新提交"
            " --yes --format json"
        )
    ]

    assert completed_provider_writes(
        events,
        discover_metadata=False,
    ) == {"instance-1", "task-1", "start"}


def test_an_unregistered_unschemad_command_is_still_not_evidence() -> None:
    """The fallback is the registered-write list, not "anything unclassifiable"."""

    driver, task = _driver(
        action=REVERT,
        tool_events=[_shell("dws oa approval tasks --instance-id UGl6QdiSRau-rm7OWXvxgQ0364")],
        classifier=_NoSchemaClassifier(),
    )

    assert driver.audit_run_has_execution_evidence(task, audit_run_id=19557) is False
