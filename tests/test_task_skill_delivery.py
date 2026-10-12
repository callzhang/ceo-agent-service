import json
from types import SimpleNamespace

from app.agent_runtime_router import _claude_turn_text, _friday_turn_text
from app.task_agent import TaskAgentCodexRunner, build_task_agent_prompt
from app.task_models import WorkItem


class Record:
    def execute(self, **kwargs):
        self.factory = kwargs["command_factory"]
        return SimpleNamespace(
            value=json.dumps(
                {
                    "decision": {
                        "project_decisions": [],
                        "task_decisions": [],
                        "project_assessments": [],
                        "update_summary": "No Project in fixture.",
                    },
                    "audit_tool_events": [],
                }
            ),
            session_id="",
            transcript_start=0,
            transcript_end=1,
        )


def test_exact_current_skill_is_read_on_demand_locally_and_inlined_once_remotely(
    tmp_path, monkeypatch
):
    path = tmp_path / "custom-skill-root" / "ceo-work-tracking" / "SKILL.md"
    path.parent.mkdir(parents=True)
    body = "AUTHORITATIVE_CURRENT_SKILL_BODY"
    path.write_text(
        "---\nname: ceo-work-tracking\ndescription: Project and Task assessment.\n---\n\n"
        + body
    )
    monkeypatch.setattr("app.task_agent.WORK_TRACKING_SKILL_PATH", path)
    work = WorkItem.model_validate(
        {
            "summary": "Current source",
            "source": {"type": "local_file", "ref": "synthetic-source"},
            "context": {"source_conversation_kind": "file"},
        }
    )
    prompt = build_task_agent_prompt(
        work, "EXACT_CURRENT_FACT", current_time="2026-10-12T10:00:00Z"
    )
    record = Record()
    TaskAgentCodexRunner(routed_execution=record).decide(
        prompt=prompt, workload_key="synthetic"
    )
    assert body not in prompt
    assert str(path) in prompt
    assert "EXACT_CURRENT_FACT" in prompt
    assert record.factory.skill_paths == (path,)
    local = _claude_turn_text(record.factory, prompt)
    remote = _friday_turn_text(record.factory, prompt)
    assert body not in local
    assert str(path) in local
    assert remote.count(body) == 1
    assert "EXACT_CURRENT_FACT" in remote
    assert path.read_text().strip() in remote
