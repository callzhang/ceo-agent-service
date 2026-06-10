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
    parse_agent_envelope,
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


def test_parse_agent_envelope_accepts_legacy_okr_review_result():
    payload = {
        "kind": "okr_review",
        "request_id": 5,
        "status": "completed",
        "result": {
            "person_name": "Claire",
            "period_label": "2026 Q2",
            "summary": "已审核。",
            "items": [
                {
                    "objective_title": "O",
                    "objective_weight": 1.0,
                    "kr_title": "KR",
                    "kr_weight": 0.5,
                    "self_progress": "80%",
                    "kr_progress_update": "完成两个验收。",
                    "claim_text": "完成两个验收。",
                    "claim_completion_time": "",
                    "deadline": "",
                    "claim_base_score": 60,
                    "claim_discount_factor": 1.0,
                    "claim_discount_reason": "未发现折扣。",
                    "claim_score": 60,
                    "verified_completion_time": "",
                    "verified_base_score": 0,
                    "verified_discount_factor": 1.0,
                    "verified_discount_reason": "无可核验证据。",
                    "verified_score": 0,
                    "evidence_used": [],
                    "evidence_gap": "缺少验收记录。",
                    "review_comment": "证据不足。",
                    "suggested_follow_up": "补充验收记录。",
                }
            ],
        },
    }
    raw = json.dumps({"item": {"text": json.dumps(payload, ensure_ascii=False)}})

    envelope = parse_agent_envelope(raw)

    assert envelope.kind == "okr_review"
    assert envelope.system_actions[0].type == "persist_okr_review"
    assert envelope.system_actions[0].request_id == 5
    assert envelope.domain_payload["person_name"] == "Claire"


def test_parse_agent_envelope_normalizes_okr_review_audit_object():
    payload = {
        "kind": "okr_review",
        "user_response": {
            "mode": "send_reply",
            "text": "OKR review completed.",
            "sensitivity_kind": "internal_personnel",
        },
        "system_actions": [{"type": "persist_okr_review", "request_id": 5}],
        "domain_payload": {
            "person_name": "Claire",
            "period_label": "2026 Q2",
            "summary": "已审核。",
            "items": [
                {
                    "objective_title": "O",
                    "objective_weight": 1.0,
                    "kr_title": "KR",
                    "kr_weight": 0.5,
                    "self_progress": "80%",
                    "kr_progress_update": "完成两个验收。",
                    "claim_text": "完成两个验收。",
                    "claim_completion_time": "",
                    "deadline": "",
                    "claim_base_score": 60,
                    "claim_discount_factor": 1.0,
                    "claim_discount_reason": "未发现折扣。",
                    "claim_score": 60,
                    "verified_completion_time": "",
                    "verified_base_score": 0,
                    "verified_discount_factor": 1.0,
                    "verified_discount_reason": "无可核验证据。",
                    "verified_score": 0,
                    "evidence_used": [],
                    "evidence_gap": "缺少验收记录。",
                    "review_comment": "证据不足。",
                    "suggested_follow_up": "补充验收记录。",
                }
            ],
        },
        "audit": {
            "request_id": 5,
            "source_system": "叮当OKR Dingteam Web",
            "method": "逐 KR 审核。",
        },
    }
    raw = json.dumps({"item": {"text": json.dumps(payload, ensure_ascii=False)}})

    envelope = parse_agent_envelope(raw)

    assert envelope.audit.summary == "逐 KR 审核。"
    assert envelope.audit.documents == []
    assert envelope.audit.confidence == 0.7


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
        session_exists=lambda _session_id: True,
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


def test_structured_runner_clears_missing_local_session_before_exec(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", True, "missing-session")
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
        session_exists=lambda _session_id: False,
    )

    runner.run(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        prompt="hello",
        owner="reply:msg-1",
    )

    assert calls[0][0][:2] == ["codex", "exec"]
    assert calls[0][0][2] != "resume"
    assert "missing-session" not in calls[0][0]
    assert store.get_codex_session_id("cid-1") == "session-2"


def test_structured_runner_retries_fresh_after_session_refresh_error(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", True, "expired-session")
    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(
        store=store,
        workspace=tmp_path,
        spec=spec,
        session_exists=lambda _session_id: True,
    )
    calls = []

    def fake_execute(command, prompt):
        calls.append(command)
        if "expired-session" in command:
            raise RuntimeError(
                "Failed to refresh token: 400 Bad Request: Your session has ended."
            )
        return "\n".join(
            [
                json.dumps({"type": "session", "id": "new-session"}),
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

    runner._execute = fake_execute

    result = runner.run("cid-1", "Friday", True, "hello", owner="reply:msg-1")

    assert result.codex_session_id == "new-session"
    assert "expired-session" in calls[0]
    assert "expired-session" not in calls[1]
    assert store.get_codex_session_id("cid-1") == "new-session"


def test_structured_runner_reads_audit_events_from_session_transcript(
    tmp_path, monkeypatch
):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    session_id = "019eb102-dc3e-7620-b0e9-16bcc2cb7038"
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "06"
        / "10"
        / f"rollout-2026-06-10T03-10-15-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    command = 'dws doc search --query "Friday PMF Claire" --format json'
    session_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": session_id}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": "call-dws-search",
                            "arguments": json.dumps({"cmd": command}),
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.structured_agent._codex_home", lambda: tmp_path)

    def executor(command_args, prompt, env):
        return "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": session_id}}),
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
        session_exists=lambda _session_id: True,
    )

    result = runner.run("cid-1", "Friday", True, "hello", owner="reply:msg-1")

    assert result.transcript_end_line == 2
    assert result.audit_tool_events == [
        {
            "event_type": "response_item",
            "tool": "exec_command",
            "call_id": "call-dws-search",
            "input": json.dumps({"cmd": command}, ensure_ascii=False, indent=2),
            "command": command,
        }
    ]


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
    assert command[command.index("--disable") + 1] == "hooks"
    assert "--output-schema" not in command
    assert str(schema) not in command


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
