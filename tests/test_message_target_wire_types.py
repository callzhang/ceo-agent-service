import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from app.agent_contracts import parse_persisted_consumer_result
from app.consumer_agent import ConsumerAgentRunner
from tests import test_consumer_agent as consumer_tests
from tests.support.native_consumer_output_fixture import native_consumer_stream_for_command
from app.process_runner import ProcessRunResult
from app.agent_result import ResultParseError
from app.native_agent_output import native_output_schema
from app.agent_wire_contracts import ConsumerAgentWireResult, parse_consumer_agent_wire_result
from tests.test_agent_contracts import _consumer_wire_payload, _proposal


IDENTIFIERS = (
    "conversation_id", "message_id", "source_message_id", "open_dingtalk_id",
    "user_id", "recipient_open_dingtalk_id", "sender_open_dingtalk_id",
    "verified_participant_open_dingtalk_id",
)


def payload_for(target):
    proposal = _proposal()
    proposal["actions"][0].update(
        capability="dingtalk-chat", operation="send_direct_message",
        target=target, payload={"content": "Your materials have been received."}, effect="external",
    )
    return _consumer_wire_payload(outcome="proposal", proposal=proposal)


@pytest.mark.parametrize("field", IDENTIFIERS)
@pytest.mark.parametrize("wrong", [104265682397, 1.5, True, False, {"id": "recipient"}, ["recipient"], ""])
def test_new_wire_rejects_invalid_message_identifier_types(field, wrong):
    payload = payload_for({"user_id": "verified-user", "open_dingtalk_id": "00104", field: wrong})
    with pytest.raises(ValidationError, match=field):
        ConsumerAgentWireResult.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)


def test_wire_rejects_blank_identifier_even_with_another_valid_recipient():
    payload = payload_for({"user_id": "verified-user", "open_dingtalk_id": "  "})
    with pytest.raises(ValidationError):
        ConsumerAgentWireResult.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)


def test_digit_strings_are_preserved_exactly_and_optional_null_is_unset():
    target = {"open_dingtalk_id": "000104265682397", "conversation_id": None,
              "source_metadata": {"a/b~c": [7, False, None]}}
    payload = payload_for(target)
    Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)
    result = ConsumerAgentWireResult.model_validate(payload).to_result()
    assert result.proposal.actions[0].target == target


def test_native_jsonl_parser_rejects_numeric_recipient_before_audit():
    payload = payload_for({"open_dingtalk_id": 104265682397})
    stream = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}})
    with pytest.raises(ResultParseError):
        parse_consumer_agent_wire_result(stream)


def test_historical_domain_result_keeps_original_numeric_target_readable():
    payload = payload_for({"open_dingtalk_id": 104265682397})
    domain = {key: payload[key] for key in (
        "summary", "decision_options", "risk", "confidence", "rule_coverage",
        "information_completeness",
    )}
    domain.update(outcome="proposal", proposal=payload["payload"],
                  error={"code": "", "retryable": False, "authorization_required": False})
    result = parse_persisted_consumer_result(domain)
    assert result.proposal.actions[0].target["open_dingtalk_id"] == 104265682397


def test_other_capabilities_keep_json_target_values():
    payload = payload_for({"open_dingtalk_id": {"vendor": [7, None, False]}})
    payload["payload"]["actions"][0]["capability"] = "extension-tool"
    ConsumerAgentWireResult.model_validate(payload)
    Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)


def test_new_wire_validates_executable_human_branches():
    proposal = payload_for({"open_dingtalk_id": 104265682397})["payload"]
    options = [
        {"key": "send", "label": "Notify", "instruction": "Use the selected rule",
         "consequence": "Recipient receives the notice", "applies_to": "task_class", "plan": proposal},
        {"key": "stop", "label": "Stop", "instruction": "Do not send",
         "consequence": "No message", "applies_to": "task_class", "terminal_outcome": "skipped", "reason": "Not required"},
    ]
    payload = _consumer_wire_payload(outcome="needs_human", decision_options=options)
    with pytest.raises(ValidationError, match="open_dingtalk_id"):
        ConsumerAgentWireResult.model_validate(payload)
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(ConsumerAgentWireResult.model_json_schema()).validate(payload)

store = consumer_tests.store
task = consumer_tests.task
context = consumer_tests.context


class NumericTargetExecutor(consumer_tests.CapturingExecutor):
    """Emit the native form of a valid result, with the recipient id turned into a number."""

    def __call__(self, command, *, on_stdout_line, **kwargs):
        self.prompts.append(str(kwargs["prompt"]))
        self.commands.append(command)
        stdout = native_consumer_stream_for_command(self.stdout, command)
        stdout = stdout.replace('\\"104265682397\\"', "104265682397")
        for line in stdout.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(0, stdout, "")


def test_numeric_identifier_enters_existing_result_correction_before_audit(
    store, task, context, tmp_path
):
    payload = ConsumerAgentWireResult.model_validate(
        payload_for({"open_dingtalk_id": "104265682397"})
    ).model_dump(mode="json")
    stream = "\n".join((
        json.dumps({"type": "thread.started", "thread_id": "numeric-target-fixture"}),
        json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps(payload),
        }}),
    ))
    class NumericNativeExecutor(consumer_tests.CapturingExecutor):
        def __call__(self, command, *, on_stdout_line, **kwargs):
            native = native_consumer_stream_for_command(self.stdout, command)
            validator = Draft202012Validator(native_output_schema("consumer", purpose="general"))
            events = [json.loads(line) for line in native.splitlines()]
            for event in events:
                if event.get("type") == "item.completed":
                    output = json.loads(event["item"]["text"])
                    assert validator.is_valid(output)
                    [target] = output["decision"]["payload"]["actions"][0]["target"]["entries"]
                    assert target["key"] == "open_dingtalk_id"
                    target["value"] = 104265682397
                    assert validator.is_valid(output)
                    event["item"]["text"] = json.dumps(output)
            self.stdout = "\n".join(json.dumps(event) for event in events)
            return super().__call__(command, on_stdout_line=on_stdout_line, **kwargs)

    runner = ConsumerAgentRunner(
        store=store, workspace=Path(tmp_path),
        executor=NumericNativeExecutor(stream),
    )
    with pytest.raises(ResultParseError):
        runner.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    [run] = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    error = json.loads(run.structured_error_json)
    assert run.status == "failed"
    assert error["code"] == "codex_result_invalid"
    assert "open_dingtalk_id" in error["detail"]
    assert run.final_result_json == ""

    executor = consumer_tests.CapturingExecutor(consumer_tests._result_jsonl())
    ConsumerAgentRunner(store=store, workspace=Path(tmp_path), executor=executor).run(
        task, context, proposal_revision=0, parent_agent_run_id=None
    )
    assert "## Result Correction" in executor.prompts[0]
    assert "open_dingtalk_id" in executor.prompts[0]
