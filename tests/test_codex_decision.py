import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.codex_decision import (
    CodexDecisionRunner,
    _decision_envelope_repair_prompt,
    append_signature,
    extract_codex_audit_events,
    extract_codex_session_id,
    parse_codex_json,
)
from app.codex_runner import memory_connector_config_issue
from app.dingtalk_models import (
    CalendarResponseStatus,
    CodexAction,
    SensitivityKind,
)
from app.leak_check import contains_forbidden_leak


def test_decision_runner_exposes_only_routed_execution():
    assert "executor" not in inspect.signature(CodexDecisionRunner).parameters








def _routed_runner(tmp_path: Path, raw: str) -> CodexDecisionRunner:
    class Routed:
        def execute(self, **kwargs):
            return SimpleNamespace(
                value=kwargs["parser"](raw), session_id="routed-session",
                transcript_start=0, transcript_end=0,
            )

    return CodexDecisionRunner(workspace=tmp_path, codex_home=tmp_path, routed_execution=Routed())


def test_routed_decision_requires_and_uses_persisted_agent_run(tmp_path: Path):
    calls = []

    class Routed:
        def execute(self, **kwargs):
            calls.append(kwargs)
            value = kwargs["parser"](
                _agent_envelope_json(
                    kind="no_action", mode="no_reply", summary="无需回复。"
                )
            )
            return SimpleNamespace(
                value=value,
                session_id="decision-session",
                transcript_start=2,
                transcript_end=4,
            )

    runner = CodexDecisionRunner(
        workspace=tmp_path,
        routed_execution=Routed(),
        codex_home=tmp_path,
    )

    with pytest.raises(ValueError, match="persisted agent run"):
        runner.decide("decide", None)
    decision = runner.decide("decide", None, run_id=41)

    assert decision.action == CodexAction.NO_REPLY
    assert calls[0]["workload_kind"] == "agent_run"
    assert calls[0]["workload_key"] == "41"
    assert runner.last_session_id == "decision-session"


def test_routed_decision_propagates_runtime_failure_instead_of_business_stop(
    tmp_path: Path,
):
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError

    class Routed:
        def execute(self, **_kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                "provider detail with secret material",
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                failure_code="codex_provider_auth_failed",
            )

    runner = CodexDecisionRunner(workspace=tmp_path, routed_execution=Routed())

    with pytest.raises(RoutedCodexExecutionError) as raised:
        runner.decide("decide", None, run_id=41)

    assert raised.value.code == "runtime_execution_failed"


def test_routed_decision_correction_prompt_names_rejected_fields(tmp_path: Path):
    from app.agent_runtime_router import RoutedResultValidationError

    class Routed:
        def execute(self, **kwargs):
            correction_prompt = kwargs["result_validation_retry"].correction_prompt
            assert correction_prompt is not None
            rejected = _live_agent_message_raw(
                _fenced(_agent_envelope_json(summary="消息已时过境迁。", confidence=None))
            )
            with pytest.raises(RoutedResultValidationError) as raised:
                kwargs["parser"](rejected)
            # The router hands the builder failure.raw_output, so the parser
            # must attach the rejected output for the field problems to appear.
            assert raised.value.raw_output == rejected
            correction = correction_prompt(raised.value.raw_output)
            assert "valid AgentEnvelope" in correction
            assert "- audit.confidence: Field required" in correction
            assert "消息已时过境迁" not in correction
            assert '"user_response"' in correction
            assert '"kind"' in correction
            assert "mode/reply/text" in correction
            value = kwargs["parser"](
                _agent_envelope_json(
                    kind="no_action", mode="no_reply", summary="无需回复。"
                )
            )
            return SimpleNamespace(
                value=value,
                session_id="decision-correction-session",
                transcript_start=2,
                transcript_end=4,
            )

    runner = CodexDecisionRunner(
        workspace=tmp_path,
        routed_execution=Routed(),
        codex_home=tmp_path,
    )

    decision = runner.decide("decide", None, run_id=42)

    assert decision.action == CodexAction.NO_REPLY


def test_routed_decision_repairs_once_with_images_and_native_commands(tmp_path, monkeypatch):
    from app.agent_runtime_router import RoutedCodexExecution
    from app.codex_history import count_codex_session_lines
    from app.codex_runtime_adapter import CodexRuntimeAdapter
    from app.process_runner import ProcessRunResult
    from app.store import AutoReplyStore
    from tests.support.native_protocol import install_protocol_native_trajectories
    from tests.test_routed_codex_execution import config, make_router, seed_agent_run_parent

    store = AutoReplyStore(tmp_path / "decision.sqlite3")
    run_id = seed_agent_run_parent(store)
    runtime_config = config.__wrapped__()
    commands, prompts = [], []

    class Executor:
        def __call__(self, command, **kwargs):
            commands.append(command)
            prompts.append(kwargs["prompt"])
            message = "not json" if len(commands) == 1 else _agent_envelope_json(
                kind="no_action", summary="无需回复。"
            )
            return ProcessRunResult(0, "\n".join(json.dumps(event) for event in [
                {"type": "thread.started", "thread_id": "decision-session"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                {"type": "turn.completed"},
            ]), "")

    install_protocol_native_trajectories(tmp_path, monkeypatch, Executor)
    routed = RoutedCodexExecution(
        store=store, config=runtime_config, router=make_router(store, runtime_config),
        adapter=CodexRuntimeAdapter(tmp_path, runtime_config), executor=Executor(),
        session_line_counter=lambda session: count_codex_session_lines(
            session, codex_home=tmp_path / "codex-home"
        ),
    )
    runner = CodexDecisionRunner(workspace=tmp_path, routed_execution=routed)
    image = tmp_path / "diagram.png"

    decision = runner.decide("decide", None, image_paths=[image], run_id=run_id)

    assert decision.action == CodexAction.NO_REPLY
    assert len(commands) == 2
    assert all(command[command.index("--image") + 1] == str(image) for command in commands)
    assert "resume" in commands[1] and "decision-session" in commands[1]
    assert "did not contain an AgentEnvelope JSON object" in prompts[1]


def _agent_envelope_json(
    *,
    mode: str = "no_reply",
    text: str = "",
    summary: str = "只需上下文判断。",
    kind: str = "reply",
    documents: list[dict[str, str]] | None = None,
    domain_payload: dict | None = None,
    confidence: float | None = 0.8,
) -> str:
    system_actions = (
        [{"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}]
        if mode == "send_reply"
        else []
    )
    audit: dict = {"summary": summary, "documents": documents or []}
    if confidence is not None:
        audit["confidence"] = confidence
    return json.dumps(
        {
            "kind": kind,
            "user_response": {
                "mode": mode,
                "text": text,
                "sensitivity_kind": "general",
            },
            "system_actions": system_actions,
            "domain_payload": domain_payload or {},
            "audit": audit,
        },
        ensure_ascii=False,
    )


def _fenced(text: str) -> str:
    return f"```json\n{text}\n```"


def _live_agent_message_raw(text: str) -> str:
    """The live `codex exec --json` shape: thread.started, then item.completed."""
    return "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": text},
                },
                ensure_ascii=False,
            ),
        ]
    )


def test_parse_codex_json_maps_permission_fields_from_agent_envelope():
    raw = json.dumps(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "先观察",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {"personnel_subject_user_id": "user-1"},
            "audit": {"summary": "涉及内部人员，先观察。", "documents": [], "confidence": 0.8},
        },
        ensure_ascii=False,
    )

    decision = parse_codex_json(raw)

    assert decision.sensitivity_kind == "internal_personnel"
    assert decision.personnel_subject_user_id == "user-1"


def test_parse_codex_json_rejects_legacy_decision_object():
    raw = json.dumps(
        {
            "action": "send_reply",
            "reply_text": "收到",
            "audit_summary": "旧结构。",
        },
        ensure_ascii=False,
    )

    with pytest.raises(json.JSONDecodeError) as raised:
        parse_codex_json(raw)

    assert "No AgentEnvelope JSON found" in raised.value.msg


@pytest.mark.parametrize(
    "raw",
    [
        _live_agent_message_raw(
            json.dumps(
                {"mode": "no_reply", "audit_summary": "The delayed message is stale."}
            )
        ),
        json.dumps(
            {
                "user_mode": "no_reply",
                "reply": None,
                "audit_summary": "The delayed message is stale.",
            }
        ),
        json.dumps(
            {
                "user_mode": "no_reply",
                "reply": "This must not be treated as action-free.",
                "audit_summary": "Invalid no-reply shape.",
            }
        ),
    ],
    ids=["mode_shorthand", "user_mode_shorthand", "user_mode_with_reply"],
)
def test_parse_codex_json_rejects_action_free_no_reply_shorthand(raw: str):
    # No prompt documents the shorthand; it costs the correction turn like any
    # other non-envelope output.
    with pytest.raises(json.JSONDecodeError) as raised:
        parse_codex_json(raw)

    assert "No AgentEnvelope JSON found" in raised.value.msg
    prompt = _decision_envelope_repair_prompt(raw)
    assert "did not contain an AgentEnvelope JSON object" in prompt
    assert "mode/reply/text" in prompt


def test_parse_codex_json_accepts_agent_envelope_object():
    raw = json.dumps(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "收到，我来处理。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {
                "summary": "已根据当前消息判断需要回复。",
                "documents": [
                    {
                        "title": "当前钉钉消息",
                        "url": "",
                        "relevance": "触发用户请求",
                    }
                ],
                "confidence": 0.9,
            },
        },
        ensure_ascii=False,
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "收到，我来处理。"
    assert decision.reason == "已根据当前消息判断需要回复。"
    assert decision.audit_summary == "已根据当前消息判断需要回复。"
    assert decision.audit_documents == [
        {"title": "当前钉钉消息", "url": "", "relevance": "触发用户请求"}
    ]


def test_parse_codex_json_maps_calendar_response_from_agent_envelope_domain_payload():
    raw = json.dumps(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "这个会议需要参加。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {"calendar_response_status": "accepted"},
            "audit": {
                "summary": "根据会议标题和上下文判断需要参加。",
                "documents": [],
                "confidence": 0.8,
            },
        },
        ensure_ascii=False,
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.calendar_response_status == "accepted"


def test_parse_codex_json_maps_handoff_agent_envelope():
    raw = json.dumps(
        {
            "kind": "reply",
            "user_response": {
                "mode": "handoff_to_human",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {
                "summary": "对方要求本人立即进入会议，必须转交本人。",
                "documents": [],
                "confidence": 0.9,
            },
        },
        ensure_ascii=False,
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.HANDOFF_TO_HUMAN
    assert decision.reason == "对方要求本人立即进入会议，必须转交本人。"


def test_extract_codex_audit_events_from_jsonl_tool_events():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "tool_call",
                        "tool_name": "exec_command",
                        "arguments": {
                            "cmd": "sed -n '1,120p' /Users/principal/Documents/memory/面试/岗位画像.md"
                        },
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    events = extract_codex_audit_events(raw)

    assert events == [
        {
            "event_type": "item.completed",
            "tool": "exec_command",
            "input": json.dumps(
                {
                    "cmd": "sed -n '1,120p' /Users/principal/Documents/memory/面试/岗位画像.md"
                },
                ensure_ascii=False,
                indent=2,
            ),
            "command": "sed -n '1,120p' /Users/principal/Documents/memory/面试/岗位画像.md",
            "path": "/Users/principal/Documents/memory/面试/岗位画像.md",
        }
    ]


def test_extract_codex_audit_events_preserves_mcp_tool_name():
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "friday memory_memory_recall",
                "arguments": {"query": "候选人筛选项目", "limit": 10},
            },
        },
        ensure_ascii=False,
    )

    events = extract_codex_audit_events(raw)

    assert events[0]["tool"] == "friday memory_memory_recall"
    assert "候选人筛选项目" in events[0]["input"]


def test_extract_codex_audit_events_preserves_tool_name_without_payload():
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "response_item",
                    "item": {
                        "type": "function_call",
                        "name": "list_mcp_resources",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "item": {
                        "type": "tool_search_call",
                    },
                }
            ),
        ]
    )

    events = extract_codex_audit_events(raw)

    assert events == [
        {"event_type": "response_item", "tool": "list_mcp_resources"},
        {"event_type": "response_item", "tool": "tool_search_call"},
    ]


def test_memory_connector_config_issue_does_not_inspect_copied_service_token(tmp_path, monkeypatch):
    config = tmp_path / "service-mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "memory_connector": {
                        "url_env": "MEMORY_CONNECTOR_URL",
                        "bearer_token_env_var": "CONNECTOR_API_KEY",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(config))
    monkeypatch.setenv("MEMORY_CONNECTOR_URL", "https://memory.example/mcp/")
    monkeypatch.setenv(
        "CONNECTOR_API_KEY",
        "eyJhbGciOiJub25lIn0.eyJleHAiOjF9.",
    )

    assert memory_connector_config_issue() == ""


def test_extract_codex_session_id_accepts_session_meta():
    raw = json.dumps(
        {
            "type": "session_meta",
            "payload": {"id": "019e29ed-e90f-7002-9507-1e8b7d9efcdc"},
        }
    )

    assert extract_codex_session_id(raw) == "019e29ed-e90f-7002-9507-1e8b7d9efcdc"


def test_parse_codex_json_accepts_jsonl_direct_decision_line():
    raw = "\n".join(
        [
            json.dumps({"type": "session", "id": "session-1"}),
            _agent_envelope_json(kind="no_action", summary="cc only"),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.reason == "cc only"


def test_parse_codex_json_accepts_jsonl_agent_message_decision():
    raw = "\n".join(
        [
            json.dumps({"session_id": "session-1"}),
            json.dumps(
                {
                    "type": "agent_message",
                    "message": _agent_envelope_json(kind="no_action", summary="cc only"),
                }
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.reason == "cc only"


def test_parse_codex_json_accepts_jsonl_message_content_decision():
    raw = "\n".join(
        [
            json.dumps({"sessionId": "session-1"}),
            json.dumps(
                {
                    "type": "message",
                    "content": _agent_envelope_json(
                        mode="send_reply", text="收到", summary="direct ask"
                    ),
                }
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "收到"


def test_parse_codex_json_accepts_jsonl_message_content_text_decision():
    raw = "\n".join(
        [
            json.dumps({"type": "session", "id": "session-1"}),
            json.dumps(
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "text",
                            "text": _agent_envelope_json(kind="no_action", summary="done"),
                        }
                    ],
                }
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.reason == "done"


def test_parse_codex_json_accepts_live_item_completed_agent_message_text():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": _agent_envelope_json(
                            kind="no_action", summary="live final"
                        ),
                    },
                }
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.reason == "live final"


def test_parse_codex_json_accepts_fenced_agent_envelope_in_live_agent_message():
    raw = _live_agent_message_raw(
        _fenced(_agent_envelope_json(summary="消息已时过境迁，无需回复。"))
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.audit_summary == "消息已时过境迁，无需回复。"


def test_parse_codex_json_picks_last_valid_envelope_after_prose_and_draft():
    raw = _live_agent_message_raw(
        "Draft:\n"
        + _fenced(_agent_envelope_json(kind="dingtalk_reply", summary="草稿。"))
        + "\nFinal:\n"
        + _fenced(_agent_envelope_json(summary="最终判断。"))
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.audit_summary == "最终判断。"


def test_parse_codex_json_raises_field_error_for_schema_invalid_envelope():
    raw = _live_agent_message_raw(
        _fenced(_agent_envelope_json(summary="空白图片消息。", confidence=None))
    )

    with pytest.raises(ValidationError) as raised:
        parse_codex_json(raw)

    assert "audit.confidence" in str(raised.value)


def test_parse_codex_json_rejects_fenced_legacy_decision():
    raw = _live_agent_message_raw(
        _fenced(json.dumps({"action": "no_reply", "reason": "消息已过时。"}))
    )

    with pytest.raises(json.JSONDecodeError) as raised:
        parse_codex_json(raw)

    assert "No AgentEnvelope JSON found" in str(raised.value)


def test_decision_envelope_repair_prompt_lists_field_problems_from_schema():
    raw = _live_agent_message_raw(
        _fenced(
            _agent_envelope_json(
                kind="wechat_chat", summary="空白图片消息。", confidence=None
            )
        )
    )

    prompt = _decision_envelope_repair_prompt(raw)

    assert "- audit.confidence: Field required" in prompt
    assert "- kind: Input should be" in prompt
    assert "空白图片消息" not in prompt
    assert "wechat_chat" not in prompt
    assert '"oa_approval"' in prompt
    assert '"handoff_to_human"' in prompt
    assert '"dws_oa_approval_action"' in prompt
    assert "AgentEnvelope Pydantic JSON schema:" in prompt
    assert (
        "Do not call tools, do not send messages, do not take any external action."
        in prompt
    )


def test_decision_envelope_repair_prompt_reports_decision_rule_violations():
    # Schema-valid envelope (no reply system action) that the runner's own
    # decision rules still reject: a clarifying question needs text.
    raw = _live_agent_message_raw(
        _agent_envelope_json(mode="ask_clarifying_question", text="")
    )

    prompt = _decision_envelope_repair_prompt(raw)

    assert "- reply_text is required for reply actions" in prompt


def test_decision_envelope_repair_prompt_without_envelope_uses_fixed_line():
    for raw in ("", "not valid AgentEnvelope", _fenced('{"action": "no_reply"}')):
        prompt = _decision_envelope_repair_prompt(raw)

        assert "- the reply did not contain an AgentEnvelope JSON object" in prompt
        assert "mode/reply/text" in prompt


def test_parse_codex_json_rejects_nonstandard_envelope_with_user_response():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": json.dumps(
                            {
                                "kind": "dingtalk_reply",
                                "user_response": {
                                    "mode": "send_reply",
                                    "text": "可以，我先按这个日报每天看当天新增。",
                                    "sensitivity_kind": "internal_personnel",
                                },
                                "system_actions": [
                                    {
                                        "type": "persist_daily_doc_review_watch",
                                        "doc_url": "https://alidocs.dingtalk.com/i/nodes/doc",
                                    }
                                ],
                                "domain_payload": {"doc_title": "项目日报"},
                                "audit": {
                                    "material_read": True,
                                    "evidence_summary": "已读取日报并判断风险。",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    with pytest.raises(ValidationError) as raised:
        parse_codex_json(raw)

    assert "kind" in str(raised.value)
    prompt = _decision_envelope_repair_prompt(raw)
    assert "- kind: Input should be" in prompt
    assert "- audit.summary: Field required" in prompt
    assert "已读取日报并判断风险" not in prompt


def _candidate_meeting_envelope(audit: dict) -> str:
    return json.dumps(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "这个会可以接。",
                "sensitivity_kind": "external_candidate",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {
                "candidate_context_known": True,
                "candidate_department_ids": ["dept-ai"],
                "calendar_response_status": "accepted",
            },
            "audit": audit,
        },
        ensure_ascii=False,
    )


def test_parse_codex_json_rejects_envelope_without_audit_confidence():
    raw = _candidate_meeting_envelope({"summary": "已结合岗位信息判断。", "documents": []})

    with pytest.raises(ValidationError) as raised:
        parse_codex_json(raw)

    assert "audit.confidence" in str(raised.value)


def test_parse_codex_json_maps_domain_payload_from_agent_envelope():
    raw = _candidate_meeting_envelope(
        {"summary": "已结合岗位信息判断。", "documents": [], "confidence": 0.8}
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "这个会可以接。"
    assert decision.sensitivity_kind == SensitivityKind.EXTERNAL_CANDIDATE
    assert decision.candidate_context_known is True
    assert decision.candidate_department_ids == ["dept-ai"]
    assert decision.calendar_response_status == CalendarResponseStatus.ACCEPTED


def test_parse_codex_json_accepts_event_msg_agent_message_payload():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": _agent_envelope_json(mode="send_reply", text="收到"),
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "收到"






def test_runner_tracks_audit_tool_events(tmp_path: Path):
    raw = "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "tool_call",
                                "tool_name": "exec_command",
                                "arguments": {
                                    "cmd": "rg -n 岗位 /Users/principal/Documents/memory/面试"
                                },
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        json.loads(
                            _agent_envelope_json(
                                kind="no_action",
                                mode="no_reply",
                                summary="已检查上下文，问题已处理。",
                            )
                        ),
                        ensure_ascii=False,
                    ),
                ]
            )
    runner = _routed_runner(tmp_path, raw)

    runner.decide(prompt="decide", session_id=None, run_id=41)

    assert runner.last_audit_tool_events[0]["tool"] == "exec_command"
    assert "rg -n" in runner.last_audit_tool_events[0]["command"]


def test_runner_tracks_dws_material_read_audit_events(tmp_path: Path):
    command = (
        "dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 "
        "--format json"
    )
    raw = "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "tool_call",
                                "tool_name": "exec_command",
                                "call_id": "call-dws-read",
                                "arguments": {"cmd": command},
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "tool_result",
                                "call_id": "call-dws-read",
                                "output": "OpenAI 合作建议补充版\n建议先补齐材料。",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        json.loads(
                            _agent_envelope_json(
                                kind="no_action",
                                mode="no_reply",
                                summary="已读取 DWS 材料。",
                            )
                        ),
                        ensure_ascii=False,
                    ),
                ]
            )
    runner = _routed_runner(tmp_path, raw)

    runner.decide(prompt="decide", session_id=None, run_id=41)

    assert runner.last_audit_tool_events == [
        {
            "event_type": "item.completed",
            "tool": "exec_command",
            "call_id": "call-dws-read",
            "input": json.dumps({"cmd": command}, ensure_ascii=False, indent=2),
            "command": command,
        },
        {
            "event_type": "item.completed",
            "tool": "tool_output",
            "call_id": "call-dws-read",
            "output": "OpenAI 合作建议补充版\n建议先补齐材料。",
        },
    ]










def test_parse_codex_json_accepts_item_completed_message_output_text():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": _agent_envelope_json(
                                    mode="send_reply",
                                    text="按这个口径推进。",
                                    summary="只需上下文判断，当前消息足够确认回复。",
                                ),
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "按这个口径推进。"
    assert decision.audit_summary.startswith("只需上下文判断")


def test_parse_codex_json_accepts_task_complete_last_agent_message():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": _agent_envelope_json(
                            kind="no_action", summary="对方只是确认收到，无需回复。"
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.audit_summary == "对方只是确认收到，无需回复。"








def test_reply_with_empty_audit_documents_accepts_nonempty_audit_summary(
    tmp_path: Path,
):
    raw = _agent_envelope_json(
                mode="send_reply",
                text="先按A方案走",
                summary="未使用可作为业务依据的文档材料；本次判断主要基于当前群聊上下文和直接@明哥的管理同步规则。",
            )
    runner = _routed_runner(tmp_path, raw)

    decision = runner.decide(prompt="decide", session_id="session-1", run_id=41)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.audit_summary.startswith("未使用可作为业务依据的文档材料")


def test_append_signature_once():
    assert append_signature("收到") == "收到（by明哥分身）"
    assert append_signature("收到（by明哥分身）") == "收到（by明哥分身）"


def test_detects_forbidden_leaks():
    assert contains_forbidden_leak("/Users/principal/Documents/memory/secret.md") is True
    assert contains_forbidden_leak("graphify evidence: node 1") is True
    assert contains_forbidden_leak("Sources: internal notes") is True
    assert contains_forbidden_leak("sources: internal notes") is True
    assert contains_forbidden_leak("source=exec") is True
    assert contains_forbidden_leak("source = exec") is True
    assert contains_forbidden_leak("source=memory") is True
    assert contains_forbidden_leak("source = memory") is True
    assert contains_forbidden_leak("来源：内部材料") is True
    assert contains_forbidden_leak("session_id abc") is True
    assert contains_forbidden_leak("sessionId abc") is True
    assert contains_forbidden_leak("session id abc") is True
    assert contains_forbidden_leak("thread_id abc") is True
    assert contains_forbidden_leak("thread id abc") is True
    assert contains_forbidden_leak("参考 [1]") is True
    assert contains_forbidden_leak("参考【1】") is True
    assert contains_forbidden_leak("/tmp/secret.md") is True
    assert contains_forbidden_leak("/home/principal/secret.md") is True
    assert contains_forbidden_leak("正常回复（by明哥分身）") is False
