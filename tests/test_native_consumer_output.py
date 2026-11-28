import json

import pytest
from jsonschema import Draft202012Validator

from app.agent_result import ResultParseError
from app.agent_wire_contracts import consumer_wire_model, parse_consumer_agent_wire_result
from app.consumer_agent import _parse_consumer_result
from app.native_agent_output import (
    CONSUMER_DECISION_FIELDS,
    _decode,
    native_output_schema,
    native_output_schema_path,
    parse_native_output,
    parse_native_result,
)


def _event(value):
    return json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(value, ensure_ascii=False),
    }}, ensure_ascii=False)


def _consumer(action="no_action", *, purpose="general", target=None, payload=None, **outcome_fields):
    wire = consumer_wire_model(purpose).model_validate({
        "action": action, "reason": "Current source checked", "target": target or {},
        "payload": payload or {}, "evidence_refs": ["source:1"],
        "summary": "Current source checked", "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        **outcome_fields,
    }).model_dump(mode="json")
    wire["decision"] = {name: wire.pop(name) for name in CONSUMER_DECISION_FIELDS}
    return wire


def _encode_dynamic(value):
    if isinstance(value, dict):
        return {"entries": [{"key": key, "value": _encode_dynamic(item)}
                            for key, item in value.items()]}
    if isinstance(value, list):
        return [_encode_dynamic(item) for item in value]
    return value


def test_new_calendar_action_checks_provider_response_status():
    native = _consumer("plan", payload={
        "objective": "Respond to the invitation",
        "actions": [{
            "description": "Accept the invitation", "action_identity": "rsvp",
            "capability": "dingtalk-calendar", "operation": "respond_calendar_event",
            "target": {"event_id": "event-1"}, "payload": {"response_status": "accept"},
        }],
        "sourced_facts": [], "authored_judgment": "Attendance is useful",
    })
    native.update(native.pop("decision"))
    with pytest.raises(ResultParseError, match=r"response_status.*accepted"):
        _parse_consumer_result(_event(native))

    historical = parse_consumer_agent_wire_result(_event(native))
    assert historical.proposal.actions[0].payload["response_status"] == "accept"

    native["payload"]["actions"][0]["payload"]["response_status"] = "accepted"
    result = _parse_consumer_result(_event(native))
    assert result.proposal.actions[0].payload["response_status"] == "accepted"


@pytest.mark.parametrize(
    "target, sender_user_id, error_field",
    [
        ({"user_id": "sender-open-id"}, "", "open_dingtalk_id"),
        ({"open_dingtalk_id": "sender-user-id"}, "sender-user-id", "user_id"),
        ({"open_dingtalk_id": "sender-open-id"}, "", None),
        ({"user_id": "sender-user-id"}, "sender-user-id", None),
        ({"user_id": "other-verified-recipient"}, "sender-user-id", None),
    ],
)
def test_consumer_checks_dingtalk_recipient_id_type_before_audit(
    tmp_path, target, sender_user_id, error_field,
):
    from dataclasses import replace

    from app.consumer_agent import _prepare_reviewable_candidate
    from tests.test_reviewed_orchestration import setup

    store, task, context = setup(tmp_path)
    context = replace(
        context, trigger_sender_user_id=sender_user_id,
        trigger_sender_open_dingtalk_id="sender-open-id",
    )
    wire = _consumer("plan", payload={
        "objective": "Respond to the invitation and notify its sender",
        "actions": [
            {
                "description": "Decline", "action_identity": "rsvp",
                "capability": "dingtalk-calendar", "operation": "respond_calendar_event",
                "target": {"event_id": "event-1"}, "payload": {"response_status": "declined"},
            },
            {
                "description": "Notify the sender", "action_identity": "notice",
                "capability": "dingtalk-chat", "operation": "send_direct_message",
                "target": {"conversation_id": context.conversation_id, **target},
                "payload": {"content": "I cannot attend."},
            },
        ],
        "sourced_facts": [], "authored_judgment": "Calendar conflict confirmed",
    })
    wire.update(wire.pop("decision"))
    candidate = _parse_consumer_result(_event(wire))
    prepare = dict(store=store, task=task, context=context, proposal_revision=0, source_client=None)
    if error_field:
        with pytest.raises(ResultParseError, match=f"use target.{error_field}"):
            _prepare_reviewable_candidate(candidate, **prepare)
        return

    prepared = _prepare_reviewable_candidate(candidate, **prepare)
    assert prepared.proposal.actions[0] == candidate.proposal.actions[0]
    assert prepared.proposal.actions[1].target == candidate.proposal.actions[1].target
    assert prepared.proposal.actions[1].payload["content"].startswith("I cannot attend.")


def test_native_consumer_schema_tracks_both_current_purposes():
    general = native_output_schema(purpose="general")
    oa = native_output_schema(purpose="oa")
    Draft202012Validator.check_schema(general)
    Draft202012Validator.check_schema(oa)

    def actions(schema):
        return {branch["properties"]["action"]["enum"][0]
                for branch in schema["properties"]["decision"]["anyOf"]}

    assert actions(oa) - actions(general) == {"approve", "reject", "return", "add_approver", "comment"}


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_committed_native_schema_matches_current_wire_contract(purpose):
    assert json.loads(native_output_schema_path(purpose=purpose).read_text()) == (
        native_output_schema(purpose=purpose)
    )


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_non_human_schema_rejects_decision_basis(purpose):
    from tests.test_agent_contracts import _decision_basis

    native = _consumer(purpose=purpose)
    native["decision"]["decision_basis"] = _decision_basis()
    assert not Draft202012Validator(native_output_schema(purpose=purpose)).is_valid(native)


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_needs_human_accepts_full_decision_basis(purpose):
    from tests.test_agent_contracts import _decision_basis, _decision_options

    native = _consumer(
        "needs_human", purpose=purpose, risk="high", confidence=0.49,
        decision_options=_decision_options(), decision_basis=_decision_basis(),
        needs_human_reason="A policy decision is needed",
    )
    for option in native["decision"]["decision_options"]:
        if option["plan"] is None:
            continue
        for action in option["plan"]["actions"]:
            for name in ("target", "payload"):
                action[name] = _encode_dynamic(action[name])
    schema = native_output_schema(purpose=purpose)
    assert "anyOf" not in schema
    validator = Draft202012Validator(schema)
    validator.validate(native)
    restored = json.loads(parse_native_output(_event(native), purpose=purpose))
    assert restored["decision_basis"] == _decision_basis()
    assert len(restored["decision_options"]) == 2
    native["decision"]["decision_basis"].pop("conclusion")
    assert not validator.is_valid(native)


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_retry_schema_and_parser_agree(purpose):
    native = _consumer("retry", purpose=purpose)
    assert set(native["decision"]) == set(CONSUMER_DECISION_FIELDS)
    Draft202012Validator(native_output_schema(purpose=purpose)).validate(native)

    restored = json.loads(parse_native_output(_event(native), purpose=purpose))
    assert restored["action"] == "retry"
    assert restored["evidence_refs"] == ["source:1"]


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_retry_requires_root_evidence(purpose):
    native = _consumer("retry", purpose=purpose)
    native["evidence_refs"] = []
    with pytest.raises(ResultParseError):
        parse_native_output(_event(native), purpose=purpose)


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_retry_rejects_nested_evidence(purpose):
    native = _consumer("retry", purpose=purpose)
    native["decision"]["evidence_refs"] = ["source:1"]
    with pytest.raises(ResultParseError):
        parse_native_output(_event(native), purpose=purpose)


def test_native_consumer_schema_rejects_flat_result_that_can_lose_root_fields():
    malformed = _consumer()
    malformed.pop("decision")
    malformed.update({"action": "no_action", "target": {}, "payload": {}})

    assert not Draft202012Validator(native_output_schema()).is_valid(malformed)


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_consumer_schema_preserves_exact_current_wire_values(purpose):
    target = ({"process_instance_id": "p", "task_id": "001", "target_activity_id": "a"}
              if purpose == "oa" else {"conversation_id": "c", "message_id": "m"})
    payload = ({"remark": "请补充材料", "revert_action": "REVERT_FOR_RESUBMIT"}
               if purpose == "oa" else {"content": "草稿"})
    native = _consumer("return" if purpose == "oa" else "reply", purpose=purpose,
                       target=target, payload=payload)
    assert Draft202012Validator(native_output_schema(purpose=purpose)).is_valid(native)

    result = parse_native_result(
        _event(native), "consumer", purpose=purpose,
        parser=lambda raw: parse_consumer_agent_wire_result(raw, purpose=purpose),
    )
    assert result.purpose_output["target"] == target
    assert result.purpose_output["payload"] == payload


def test_native_consumer_restores_nested_dynamic_json_without_losing_special_keys():
    target = {"conversation_id": "c", "message_id": "m"}
    payload = {"content": "草稿", "a/~b": [{"nested": [None, True, 1.5, 2]}]}
    native = _consumer("plan", payload={
        "objective": "交付草稿", "actions": [{
            "description": "交付草稿", "action_identity": "draft-1",
            "capability": "dingtalk-chat", "operation": "reply_to_message",
            "target": target, "payload": payload, "effect": "external",
        }],
        "sourced_facts": [], "authored_judgment": "已核实来源",
    })
    action = native["decision"]["payload"]["actions"][0]
    for name in ("target", "payload"):
        action[name] = _encode_dynamic(action[name])

    assert Draft202012Validator(native_output_schema()).is_valid(native)
    restored = json.loads(parse_native_output(_event(native)))
    restored_action = restored["payload"]["actions"][0]
    assert restored_action["target"] == target
    assert restored_action["payload"] == payload


@pytest.mark.parametrize(
    ("encoded", "message"),
    [
        ({"entries": [{"key": "x", "value": "first"},
                       {"key": "x", "value": "second"}]}, "duplicate"),
        ({"entries": [{"key": 1, "value": "value"}]}, "string key and value"),
        ({"entries": [{"key": "x"}]}, "string key and value"),
    ],
)
def test_native_consumer_rejects_ambiguous_or_malformed_dynamic_json_entries(
    encoded, message,
):
    dynamic_object = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }
    with pytest.raises(ValueError, match=message):
        _decode(encoded, dynamic_object, {})


@pytest.mark.parametrize("body_chars, has_effect, fits", [
    pytest.param(8000, True, True, id="identical-plan-counted-once"),
    pytest.param(8000, False, False, id="different-plan-shape-preserved"),
    pytest.param(13500, True, False, id="genuinely-over-limit"),
])
def test_plan_candidate_uses_existing_utf8_bound_with_full_source(
    tmp_path, body_chars, has_effect, fits,
):
    from dataclasses import replace

    from app.agent_turn_runner import _validate_runtime_reference_domain_result
    from app.consumer_agent import _prepare_reviewable_candidate
    from tests.test_reviewed_orchestration import setup

    store, task, context = setup(tmp_path)
    source = {"content": "源" * 9000}
    context = replace(context, trigger_raw_payload=source)
    body = "答" * body_chars
    action = {
        "description": "Answer the questions", "action_identity": "answer",
        "capability": "dingtalk-chat", "operation": "reply_to_message",
        "target": {"conversation_id": context.conversation_id,
                   "message_id": context.trigger_message_id},
        "payload": {"content": body},
    }
    if has_effect:
        action["effect"] = "external"
    plan = {"objective": "Answer", "actions": [action],
            "sourced_facts": [], "authored_judgment": "Use the verified source"}
    native = _consumer("plan", payload=plan)
    native_action = native["decision"]["payload"]["actions"][0]
    for name in ("target", "payload"):
        native_action[name] = _encode_dynamic(native_action[name])
    parsed = parse_native_result(_event(native), "consumer", parser=_parse_consumer_result)
    prepared = _prepare_reviewable_candidate(
        parsed, store=store, task=task, context=context, proposal_revision=0, source_client=None,
    )
    assert prepared.proposal.actions[0].payload["content"].startswith(body)
    assert prepared.source_bindings[0].value["trigger_raw_payload"] == source
    assert prepared.purpose_output["evidence_refs"] == ["source:1"]
    if fits:
        _validate_runtime_reference_domain_result(prepared, allow_configured_feedback_links=True)
        assert "payload" not in prepared.purpose_output
    else:
        with pytest.raises(ValueError, match="runtime_result_reference_too_large"):
            _validate_runtime_reference_domain_result(prepared, allow_configured_feedback_links=True)
        if has_effect:
            assert "payload" not in prepared.purpose_output
        else:
            assert prepared.purpose_output["payload"] == plan


def test_native_consumer_rejects_malformed_suffix_instead_of_salvaging_it():
    native = _consumer()
    with pytest.raises(ResultParseError):
        parse_native_output(_event(native)[:-2] + "}")


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_native_developer_uses_injected_schema_and_keeps_session_fingerprint(purpose, tmp_path, monkeypatch):
    from hashlib import sha256
    from app.consumer_agent import consumer_developer_sections_by_runtime
    from app import native_prompt_input

    sections = consumer_developer_sections_by_runtime(
        purpose_name=purpose, runtime_context="Anonymous", work_profile="",
    )["codex_cli"]
    native = next(s.text for s in sections if s.name == "原生输出契约指纹")
    assert "## Native Output Format\n" in native
    assert len(native) < 1000
    assert "read_consumer_output_schema" not in native
    supplied = json.loads(native_output_schema_path(purpose=purpose).read_text())
    assert json.dumps(supplied, separators=(",", ":")) not in native
    assert Draft202012Validator(supplied).is_valid(_consumer(purpose=purpose))
    flat = _consumer(purpose=purpose)
    flat.update(flat.pop("decision"))
    assert not Draft202012Validator(supplied).is_valid(flat)
    saved = tmp_path / "session.jsonl"
    saved.write_text(json.dumps({"type":"response_item", "payload":{
        "type":"message", "role":"developer", "content":[{"type":"input_text","text":native}],
        "internal_chat_message_metadata_passthrough":{"content_item_kinds":["generic.developer_instructions"]},
    }}) + "\n")
    monkeypatch.setattr(native_prompt_input.native, "find_codex_session_path", lambda *args, **kwargs: saved)
    assert native_prompt_input.codex_session_wire_schema("saved") == {
        "$native_output_schema_sha256":sha256(native_output_schema_path(purpose=purpose).read_bytes()).hexdigest()}
