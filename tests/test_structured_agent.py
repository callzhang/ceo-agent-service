import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_envelope import AgentEnvelope
from app.store import AutoReplyStore
from app.structured_agent import (
    AgentSpec,
    SkillLoadError,
    StructuredCodexRunner,
    load_skill_text,
    parse_agent_envelope,
)


class FakeRoutedExecution:
    def __init__(
        self,
        raw,
        *,
        session_id="structured-session",
        transcript_start=2,
        transcript_end=7,
    ):
        self.raw = raw
        self.session_id = session_id
        self.transcript_start = transcript_start
        self.transcript_end = transcript_end
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        raw = self.raw() if callable(self.raw) else self.raw
        value = kwargs["parser"](raw)
        value = kwargs["result_codec"].decode(kwargs["result_codec"].encode(value))
        return SimpleNamespace(
            value=value,
            route_name="codex_oauth",
            attempt_id=1,
            session_id=self.session_id,
            transcript_start=self.transcript_start,
            transcript_end=self.transcript_end,
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


def test_structured_runner_routes_processing_request_with_read_only_policy(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    raw = json.dumps(
        {
            "kind": "reply",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {"summary": "ok", "documents": [], "confidence": 1},
        }
    )
    routed = FakeRoutedExecution(raw)
    runner = StructuredCodexRunner(routed_execution=routed, spec=AgentSpec("okr", schema))

    result = runner.run(
        41,
        "cid-1",
        "Friday",
        True,
        "inspect",
        owner="okr_review:41",
    )

    call = routed.calls[0]
    assert call["workload_kind"] == "structured"
    assert call["workload_key"] == "41"
    assert call["conversation_id"] == "cid-1"
    assert call["command_factory"]._approved_policy.effect_mode == "read_only"
    assert call["required_capabilities"] == frozenset(
        {
            "structured_output",
            "local_schema_validation",
            "reviewed_read_tools",
        }
    )
    assert result.codex_session_id == "structured-session"


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


def test_parse_agent_envelope_accepts_no_reply_shorthand_without_actions():
    envelope = parse_agent_envelope(
        json.dumps(
            {
                "item": {
                    "content": [
                        {
                            "type": "Text",
                            "text": json.dumps(
                                {
                                    "mode": "no_reply",
                                    "audit_summary": "The delayed response would be stale.",
                                }
                            ),
                        }
                    ]
                }
            }
        )
    )

    assert envelope.kind == "no_action"
    assert envelope.user_response.mode == "no_reply"
    assert envelope.user_response.text == ""
    assert envelope.system_actions == []
    assert envelope.domain_payload == {}
    assert envelope.audit.summary == "The delayed response would be stale."


def test_parse_agent_envelope_rejects_no_reply_shorthand_with_extra_fields():
    with pytest.raises(ValueError, match="no valid AgentEnvelope"):
        parse_agent_envelope(
            json.dumps(
                {
                    "mode": "no_reply",
                    "system_actions": [],
                }
            )
        )


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
    routed = FakeRoutedExecution(
        executor([], "hello", {}), session_id="session-2"
    )
    runner = StructuredCodexRunner(
        routed_execution=routed,
        spec=spec,
    )

    result = runner.run(
        1,
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        prompt="hello",
        owner="reply:msg-1",
    )

    assert isinstance(result.envelope, AgentEnvelope)
    assert store.get_codex_session_id("cid-1") == "session-1"
    assert routed.calls[0]["conversation_id"] == "cid-1"


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
    routed = FakeRoutedExecution(
        executor([], "hello", {}), session_id="session-2"
    )
    runner = StructuredCodexRunner(
        routed_execution=routed,
        spec=spec,
    )

    runner.run(
        1,
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        prompt="hello",
        owner="reply:msg-1",
    )

    assert routed.calls[0]["conversation_id"] == "cid-1"
    assert store.get_codex_session_id("cid-1") == "missing-session"


def test_structured_runner_resumes_session_to_repair_invalid_json(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    invalid = "not json"
    valid = json.dumps(
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
            "audit": {"summary": "valid", "documents": [], "confidence": 0.8},
        }
    )

    class RepairingRoutedExecution:
        def __init__(self):
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            retry = kwargs["result_validation_retry"]
            assert retry.resume_same_session is True
            with pytest.raises(Exception) as first:
                kwargs["parser"](invalid)
            repair_prompt = retry.corrected_prompt("hello", first.value)
            assert repair_prompt.startswith(
                "上一次输出不是合法 AgentEnvelope JSON。请基于同一个上下文"
            )
            assert invalid in repair_prompt
            value = kwargs["parser"](valid)
            return SimpleNamespace(
                value=value,
                route_name="codex_oauth",
                attempt_id=2,
                session_id="session-1",
                transcript_start=2,
                transcript_end=7,
            )

    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    routed = RepairingRoutedExecution()
    runner = StructuredCodexRunner(routed_execution=routed, spec=spec)

    result = runner.run(
        1, "cid-1", "Friday", True, "hello", owner="reply:msg-1"
    )

    assert result.envelope.user_response.text == "ok"
    assert result.codex_session_id == "session-1"
    assert len(routed.calls) == 1


def test_structured_runner_can_skip_persisting_shared_conversation_session(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", True, "chat-session")

    def executor(command, prompt, env):
        return "\n".join(
            [
                json.dumps({"type": "session", "id": "structured-session"}),
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

    spec = AgentSpec("okr_review", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(
        routed_execution=FakeRoutedExecution(
            executor([], "hello", {}), session_id="structured-session"
        ),
        spec=spec,
    )

    result = runner.run(
        1,
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        prompt="hello",
        owner="okr_review:1",
    )

    assert result.codex_session_id == "structured-session"
    assert store.get_codex_session_id("cid-1") == "chat-session"


def test_structured_runner_retries_fresh_after_session_refresh_error(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", True, "expired-session")
    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    from app.agent_runtime_router import RoutedCodexExecutionError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError("runtime_execution_failed")

    runner = StructuredCodexRunner(
        routed_execution=FailingRoutedExecution(), spec=spec
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed"):
        runner.run(1, "cid-1", "Friday", True, "hello", owner="reply:msg-1")


def test_structured_runner_reads_audit_events_from_session_transcript(
    tmp_path, monkeypatch
):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
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
        routed_execution=FakeRoutedExecution(
            executor([], "hello", {}),
            session_id=session_id,
            transcript_start=0,
            transcript_end=2,
        ),
        spec=spec,
    )

    result = runner.run(1, "cid-1", "Friday", True, "hello", owner="reply:msg-1")

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


def test_structured_runner_requires_injected_execution(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    with pytest.raises(TypeError):
        StructuredCodexRunner(spec=spec)


def test_structured_runner_uses_explicit_output_schema_when_configured(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    output_schema = tmp_path / "output.schema.json"
    output_schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    def executor(command, prompt, env):
        del command, prompt, env
        return "\n".join(
            [
                json.dumps({"type": "session", "id": "session-output-schema"}),
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

    spec = AgentSpec(
        "reply",
        schema,
        [skill],
        [],
        "Return JSON.",
        output_schema_path=output_schema,
    )
    routed = FakeRoutedExecution(executor([], "hello", {}))
    runner = StructuredCodexRunner(
        routed_execution=routed,
        spec=spec,
    )

    runner.run(1, "cid-1", "Friday", True, "hello", owner="reply:msg-1")

    assert routed.calls[0]["command_factory"]._output_schema_path == output_schema


def test_structured_runner_rejects_effectful_mode(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(
        routed_execution=FakeRoutedExecution("{}"), spec=spec
    )

    with pytest.raises(ValueError, match="read-only"):
        runner.run(
            1,
            "cid-1",
            "Friday",
            True,
            "hello",
            owner="reply:msg-1",
            allow_side_effects=True,
        )
