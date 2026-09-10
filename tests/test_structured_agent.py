import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_envelope import AgentEnvelope
from app.agent_runtime_router import CodexCommandFactory
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


def test_structured_runner_routes_processing_request_with_standard_runtime(tmp_path):
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
    assert isinstance(call["command_factory"], CodexCommandFactory)
    assert call["required_capabilities"] == frozenset(
        {
            "structured_output",
            "local_schema_validation",
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
            assert "不要调用工具，不要发送消息" in repair_prompt
            assert "- no valid AgentEnvelope found" in repair_prompt
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

    assert routed.calls[0]["command_factory"].output_schema_path == output_schema


def test_parse_agent_envelope_accepts_fenced_and_prose_wrapped_agent_message():
    envelope = {
        "kind": "reply",
        "user_response": {
            "mode": "send_reply",
            "text": "好的，马上回",
            "sensitivity_kind": "general",
        },
        "system_actions": [],
        "domain_payload": {},
        "audit": {"summary": "Simple acknowledgement.", "documents": [], "confidence": 0.9},
    }
    draft = {"mode": "send_reply", "reply_text": "draft"}
    text = (
        "Here is my decision:\n\n```json\n"
        + json.dumps(draft, ensure_ascii=False)
        + "\n```\n\nFinal:\n\n```json\n"
        + json.dumps(envelope, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    raw = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
    )

    assert parse_agent_envelope(raw) == AgentEnvelope.model_validate(envelope)


def test_parse_agent_envelope_reports_schema_error_of_last_candidate():
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "```json\n{\"mode\": \"send_reply\", \"reply_text\": \"x\"}\n```",
            },
        }
    )
    with pytest.raises(ValueError):
        parse_agent_envelope(raw)


def test_parse_agent_envelope_ignores_non_json_lines_around_the_stream():
    envelope = {
        "kind": "no_action",
        "user_response": {"mode": "no_reply", "text": "", "sensitivity_kind": "general"},
        "system_actions": [],
        "domain_payload": {},
        "audit": {"summary": "Nothing to do.", "documents": [], "confidence": 0.5},
    }
    raw = "\n".join(
        [
            "Reading prompt from stdin...",
            json.dumps({"type": "thread.started", "thread_id": "s1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(envelope)},
                }
            ),
            "warning: mcp server 'x' exited",
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )

    assert parse_agent_envelope(raw) == AgentEnvelope.model_validate(envelope)


def test_agent_envelope_repair_prompt_names_schema_problems_of_last_candidate():
    from typing import get_args

    from app.agent_envelope import (
        AgentKind,
        AgentSensitivityKind,
        SystemAction,
        UserResponseMode,
    )
    from app.structured_agent import (
        AGENT_ENVELOPE_SCHEMA_PROBLEM_LIMIT,
        _agent_envelope_repair_prompt,
    )

    # The MiniMax shape from agent_run 9581: a fenced envelope whose enum values
    # and system action are made up and whose audit lacks confidence.
    envelope = {
        "kind": "reply",
        "user_response": {
            "mode": "reply_now",
            "text": "",
            "sensitivity_kind": "general",
        },
        "system_actions": [{"type": "send_wechat_reply"}],
        "domain_payload": {},
        "audit": {"summary": "对话在19:03后已自然结束", "documents": []},
    }
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "s1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "Draft: "
                        + json.dumps({"mode": "send_reply", "reply_text": "draft"})
                        + "\n\nFinal:\n```json\n"
                        + json.dumps(envelope, ensure_ascii=False, indent=2)
                        + "\n```",
                    },
                }
            ),
        ]
    )

    prompt = _agent_envelope_repair_prompt(raw)

    assert "不要调用工具，不要发送消息，不要执行任何外部动作" in prompt
    assert "- user_response.mode: Input should be" in prompt
    assert "- system_actions[].SendDingTalkReplyAction.type: Input should be" in prompt
    # One bad system action fails against every union member; the cap must
    # still leave room for the problems of the other top-level fields.
    assert "- audit.confidence: Field required" in prompt
    assert prompt.count("\n- ") <= AGENT_ENVELOPE_SCHEMA_PROBLEM_LIMIT
    # The problems describe the final envelope, not the draft before it.
    assert "- kind: Field required" not in prompt
    assert "对话在19:03后已自然结束" not in prompt
    for enum in (AgentKind, UserResponseMode, AgentSensitivityKind):
        for value in enum:
            assert f'"{value.value}"' in prompt
    for action in get_args(SystemAction):
        (action_type,) = get_args(action.model_fields["type"].annotation)
        assert f'"{action_type}"' in prompt


def test_agent_envelope_repair_prompt_degrades_for_a_valid_envelope():
    from app.structured_agent import _agent_envelope_repair_prompt

    raw = json.dumps(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "ok",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {"summary": "valid", "documents": [], "confidence": 0.8},
        }
    )

    # The router calls this after claiming the correction attempt; it must
    # never raise, even for a raw the parser accepts.
    prompt = _agent_envelope_repair_prompt(raw)

    assert "- the reply did not contain a valid AgentEnvelope JSON object" in prompt
    assert "AgentEnvelope Pydantic JSON schema:" in prompt


def test_agent_envelope_repair_prompt_reports_missing_json_object():
    from app.structured_agent import _agent_envelope_repair_prompt

    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "I could not decide."},
        }
    )

    prompt = _agent_envelope_repair_prompt(raw)

    assert "- agent message does not contain a JSON object" in prompt
    assert "I could not decide." not in prompt
