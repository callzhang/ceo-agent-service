"""The Task decision schema reaches each runtime exactly once."""

import json
from types import SimpleNamespace

import pytest

from app.agent_runtime_router import _claude_turn_text, _friday_turn_text
from app.agent_runtime_contracts import RuntimeKind
from app.task_agent import TaskAgentCodexRunner, build_task_agent_prompt
from app.task_models import WorkItem, task_agent_output_schema


class RecordingExecution:
    def execute(self, **kwargs):
        self.call = kwargs
        return SimpleNamespace(
            value=json.dumps(
                {
                    "decision": {
                        "project_decisions": [],
                        "task_decisions": [],
                        "project_assessments": [],
                        "update_summary": "No relevant Project in the synthetic source.",
                    },
                    "audit_tool_events": [],
                }
            ),
            session_id="",
            attempt_id=1,
            transcript_start=0,
            transcript_end=1,
        )


def prompt_and_factory(monkeypatch):
    monkeypatch.setattr(
        "app.task_agent.load_skill_text", lambda paths: "FULL_SKILL_RULE"
    )
    work = WorkItem.model_validate(
        {
            "summary": "Source fact",
            "source": {"type": "local_file", "ref": "synthetic-source"},
            "context": {"source_conversation_kind": "file"},
        }
    )
    prompt = build_task_agent_prompt(
        work, "CURRENT_SEMANTIC_FACT", current_time="2026-10-12T10:00:00Z"
    )
    friday_prompt = build_task_agent_prompt(
        work, "CURRENT_SEMANTIC_FACT", current_time="2026-10-12T10:00:00Z",
        inline_skill=True,
    )
    recorder = RecordingExecution()
    TaskAgentCodexRunner(routed_execution=recorder).decide(
        prompt=prompt, friday_prompt=friday_prompt, workload_key="synthetic"
    )
    return prompt, recorder.call


def schema_occurrences(text, schema):
    count = 0
    decoder = json.JSONDecoder()
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        count += parsed == schema
    return count


@pytest.mark.parametrize("bridge,runtime_kind", [
    (_claude_turn_text, RuntimeKind.CLAUDE_CLI),
    (_friday_turn_text, RuntimeKind.FRIDAY_RUNTIME),
])
def test_single_message_task_routes_receive_one_complete_schema(monkeypatch, bridge, runtime_kind):
    prompt, call = prompt_and_factory(monkeypatch)
    factory = call["command_factory"]
    schema = task_agent_output_schema()
    assert json.loads(factory.output_schema_path.read_text()) == schema
    assert factory.use_output_schema is True
    submitted = bridge(
        factory, call.get("route_prompt_overrides", {}).get(runtime_kind, prompt)
    )
    assert ("FULL_SKILL_RULE" in submitted) is (runtime_kind is RuntimeKind.FRIDAY_RUNTIME)
    assert "CURRENT_SEMANTIC_FACT" in submitted
    assert schema_occurrences(submitted, schema) == 1


def test_codex_task_schema_is_out_of_band_and_business_input_remains(monkeypatch):
    prompt, call = prompt_and_factory(monkeypatch)
    factory = call["command_factory"]
    assert factory.use_output_schema is True
    assert (
        json.loads(factory.output_schema_path.read_text()) == task_agent_output_schema()
    )
    assert "FULL_SKILL_RULE" not in prompt
    assert "ceo-work-tracking/SKILL.md" in prompt
    assert "CURRENT_SEMANTIC_FACT" in prompt
    assert "TaskAgentDecision Pydantic JSON schema:" not in prompt


def test_task_factory_forwards_its_native_skill_selection(monkeypatch):
    prompt, call = prompt_and_factory(monkeypatch)
    factory = call["command_factory"]
    calls = []
    adapter = SimpleNamespace(
        build_command=lambda **kwargs: calls.append(kwargs) or ["codex"],
        build_env=lambda route: {},
    )
    factory.build(
        adapter=adapter,
        route=SimpleNamespace(name="codex_oauth"),
        prompt=prompt,
        session_id=None,
    )
    assert calls[0]["skill_names"] == ("ceo-work-tracking",)
