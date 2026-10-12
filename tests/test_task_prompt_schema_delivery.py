"""The Task decision schema reaches each runtime exactly once."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agent_runtime_router import _claude_turn_text, _friday_turn_text
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
            transcript_start=0,
            transcript_end=1,
        )


def prompt_and_factory(monkeypatch, tmp_path):
    path = tmp_path / "ceo-work-tracking" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("FULL_SKILL_RULE")
    monkeypatch.setattr("app.task_agent.WORK_TRACKING_SKILL_PATH", path)
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
    recorder = RecordingExecution()
    TaskAgentCodexRunner(routed_execution=recorder).decide(
        prompt=prompt, workload_key="synthetic"
    )
    return prompt, recorder.call["command_factory"]


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


@pytest.mark.parametrize("bridge", [_claude_turn_text, _friday_turn_text])
def test_single_message_task_routes_receive_one_complete_schema(
    monkeypatch, tmp_path, bridge
):
    prompt, factory = prompt_and_factory(monkeypatch, tmp_path)
    schema = task_agent_output_schema()
    assert json.loads(factory.output_schema_path.read_text()) == schema
    assert factory.use_output_schema is True
    submitted = bridge(factory, prompt)
    assert ("FULL_SKILL_RULE" in submitted) is (bridge is _friday_turn_text)
    assert str(factory.skill_paths[0]) in submitted
    assert "CURRENT_SEMANTIC_FACT" in submitted
    assert schema_occurrences(submitted, schema) == 1


def test_codex_task_schema_is_out_of_band_and_business_input_remains(
    monkeypatch, tmp_path
):
    prompt, factory = prompt_and_factory(monkeypatch, tmp_path)
    assert factory.use_output_schema is True
    assert (
        json.loads(factory.output_schema_path.read_text()) == task_agent_output_schema()
    )
    assert "FULL_SKILL_RULE" not in prompt
    assert str(factory.skill_paths[0]) in prompt
    assert "CURRENT_SEMANTIC_FACT" in prompt
    assert "TaskAgentDecision Pydantic JSON schema:" not in prompt


@pytest.mark.parametrize("extra_skill_names", [(), ("ceo-calendar-invite",)])
def test_task_factory_forwards_its_native_skill_selection(
    monkeypatch, tmp_path, extra_skill_names
):
    prompt, factory = prompt_and_factory(monkeypatch, tmp_path)
    factory = replace(factory, skill_names=extra_skill_names)
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
    assert calls[0]["skill_names"] == extra_skill_names + ("ceo-work-tracking",)
