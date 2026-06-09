import json
from pathlib import Path

import pytest

from app.agent_envelope import AgentEnvelope
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore
from app.structured_agent import (
    AgentSpec,
    SkillLoadError,
    StructuredCodexRunner,
    load_skill_text,
)


def test_load_skill_text_reads_exact_paths(tmp_path: Path):
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("# Test Skill\n\nUse exact rules.", encoding="utf-8")

    assert load_skill_text([skill]) == "# Test Skill\n\nUse exact rules."


def test_load_skill_text_fails_fast_when_missing(tmp_path: Path):
    with pytest.raises(SkillLoadError, match="missing skill file"):
        load_skill_text([tmp_path / "missing" / "SKILL.md"])


def test_agent_spec_developer_instructions_include_skills(tmp_path: Path):
    skill = tmp_path / "skill.md"
    skill.write_text("# OKR Skill", encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    spec = AgentSpec(
        name="okr_review",
        schema_path=schema,
        primary_skill_paths=[skill],
        reply_visible_skill_paths=[],
        developer_preamble="Return only JSON.",
    )

    assert "# OKR Skill" in spec.developer_instructions()
    assert "Return only JSON." in spec.developer_instructions()


def test_structured_runner_uses_conversation_session_lock_and_persists_session(
    tmp_path,
):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", True, "session-1")
    calls = []

    def executor(command, prompt, env):
        calls.append((command, prompt, env))
        return "\n".join(
            [
                json.dumps({"type": "session", "id": "session-2"}),
                json.dumps(
                    {
                        "kind": "reply",
                        "user_response": {
                            "mode": "send_reply",
                            "text": "ok",
                            "sensitivity_kind": "general",
                        },
                        "system_actions": [
                            {
                                "type": "send_dingtalk_reply",
                                "reply_text_ref": "user_response.text",
                            }
                        ],
                        "domain_payload": {},
                        "audit": {
                            "summary": "valid",
                            "documents": [],
                            "confidence": 0.8,
                        },
                    }
                ),
            ]
        )

    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(
        store=store,
        workspace=tmp_path,
        spec=spec,
        executor=executor,
    )

    result = runner.run(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        prompt="hello",
        owner="reply:msg-1",
    )

    assert isinstance(result.envelope, AgentEnvelope)
    assert store.get_codex_session_id("cid-1") == "session-2"
    assert calls[0][0][:3] == ["codex", "exec", "resume"]
    assert "session-1" in calls[0][0]


def test_structured_runner_default_executor_uses_process_runner_signature(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return ProcessRunResult(
            returncode=0,
            stdout="\n".join(
                [
                    json.dumps({"type": "session", "id": "session-structured"}),
                    json.dumps(
                        {
                            "kind": "reply",
                            "user_response": {
                                "mode": "send_reply",
                                "text": "ok",
                                "sensitivity_kind": "general",
                            },
                            "system_actions": [
                                {
                                    "type": "send_dingtalk_reply",
                                    "reply_text_ref": "user_response.text",
                                }
                            ],
                            "domain_payload": {},
                            "audit": {
                                "summary": "valid",
                                "documents": [],
                                "confidence": 0.8,
                            },
                        }
                    ),
                ]
            ),
            stderr="",
        )

    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(
        store=store,
        workspace=tmp_path,
        spec=spec,
        timeout_seconds=7,
        idle_timeout_seconds=3,
    )
    runner._run_process_with_idle_timeout = fake_run

    result = runner.run("cid-1", "Friday", True, "hello", owner="reply:msg-1")

    assert result.codex_session_id == "session-structured"
    command, kwargs = calls[0]
    assert kwargs["prompt"] == "hello"
    assert kwargs["env"] == runner.runner.build_env()
    assert kwargs["total_timeout_seconds"] == 7
    assert kwargs["idle_timeout_seconds"] == 3
    assert "--output-schema" in command
    assert str(schema) in command


def test_structured_runner_fails_fast_when_lock_is_held(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.acquire_codex_session_lock("cid-1", "other") is True
    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(store=store, workspace=tmp_path, spec=spec)

    with pytest.raises(RuntimeError, match="codex session locked"):
        runner.run("cid-1", "Friday", True, "hello", owner="reply:msg-1")
