"""The Task decision schema reaches each runtime exactly once."""

import json
from types import SimpleNamespace


from app.agent_runtime_router import _claude_turn_text
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
    recorder = RecordingExecution()
    TaskAgentCodexRunner(routed_execution=recorder).decide(
        prompt=prompt, workload_key="synthetic"
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


def test_single_message_task_route_receives_one_complete_schema(monkeypatch):
    prompt, call = prompt_and_factory(monkeypatch)
    factory = call["command_factory"]
    schema = task_agent_output_schema()
    assert json.loads(factory.output_schema_path.read_text()) == schema
    assert factory.use_output_schema is True
    submitted = _claude_turn_text(factory, prompt)
    assert "FULL_SKILL_RULE" not in submitted
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
    assert calls[0]["skill_names"] == (
        "ceo-work-tracking", "dingtalk-minutes", "dingtalk-doc",
    )
