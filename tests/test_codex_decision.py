import json
from pathlib import Path

import app.codex_decision as codex_decision
from app.codex_decision import (
    CodexDecisionRunner,
    append_signature,
    extract_codex_audit_events,
    extract_codex_session_id,
    parse_codex_json,
)
from app.codex_runner import memory_connector_config_issue
from app.dingtalk_models import (
    CalendarResponseStatus,
    CodexAction,
    CodexDecision,
    SensitivityKind,
)
from app.leak_check import contains_forbidden_leak
from app.process_runner import ProcessRunResult


class FakeExecutor:
    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []

    def __call__(self, command: list[str], prompt: str) -> str:
        self.commands.append(command)
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def make_runner(
    tmp_path: Path,
    executor=None,
    timeout_seconds: int = 120,
    idle_timeout_seconds: int = 180,
) -> CodexDecisionRunner:
    return CodexDecisionRunner(
        workspace=tmp_path,
        executor=executor,
        timeout_seconds=timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        codex_home=tmp_path,
    )


def _agent_envelope_json(
    *,
    mode: str = "no_reply",
    text: str = "",
    summary: str = "只需上下文判断。",
    kind: str = "reply",
    documents: list[dict[str, str]] | None = None,
    domain_payload: dict | None = None,
) -> str:
    system_actions = (
        [{"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}]
        if mode == "send_reply"
        else []
    )
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
            "audit": {
                "summary": summary,
                "documents": documents or [],
                "confidence": 0.8,
            },
        },
        ensure_ascii=False,
    )


def test_parse_codex_json_accepts_decision_object():
    raw = json.dumps(
        {
            "action": "send_reply",
            "reply_text": "收到",
            "reason": "direct ask",
            "ding_self": False,
            "macos_notify": True,
        }
    )

    decision = parse_codex_json(raw)

    assert decision == CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="收到",
        reason="direct ask",
        ding_self=False,
        macos_notify=True,
    )


def test_parse_codex_json_accepts_permission_fields():
    raw = json.dumps(
        {
            "action": "send_reply",
            "reply_text": "先观察",
            "sensitivity_kind": "internal_personnel",
            "personnel_subject_user_id": "user-1",
        }
    )

    decision = parse_codex_json(raw)

    assert decision.sensitivity_kind == "internal_personnel"
    assert decision.personnel_subject_user_id == "user-1"


def test_parse_codex_json_accepts_calendar_response_status():
    raw = json.dumps(
        {
            "action": "no_reply",
            "calendar_response_status": "tentative",
            "audit_summary": "已读取日程，标题足以判断先暂定。",
        },
        ensure_ascii=False,
    )

    decision = parse_codex_json(raw)

    assert decision.calendar_response_status == "tentative"


def test_parse_codex_json_strict_rejects_legacy_decision_object():
    raw = json.dumps(
        {
            "action": "send_reply",
            "reply_text": "收到",
            "audit_summary": "旧结构。",
        },
        ensure_ascii=False,
    )

    try:
        parse_codex_json(raw, allow_legacy=False)
    except json.JSONDecodeError as exc:
        assert "AgentEnvelope" in exc.msg
    else:
        raise AssertionError("legacy CodexDecision JSON should be rejected")


def test_parse_codex_json_accepts_audit_fields():
    raw = json.dumps(
        {
            "action": "send_reply",
            "reply_text": "先看岗位画像",
            "audit_documents": [
                {
                    "path": "面试/项目经理/岗位画像.md",
                    "title": "项目经理岗位画像",
                    "relevance": "用于判断候选人匹配度",
                }
            ],
            "audit_summary": "根据岗位画像要求先判断项目闭环经验，再给推进建议。",
        },
        ensure_ascii=False,
    )

    decision = parse_codex_json(raw)

    assert decision.audit_documents == [
        {
            "path": "面试/项目经理/岗位画像.md",
            "title": "项目经理岗位画像",
            "relevance": "用于判断候选人匹配度",
        }
    ]
    assert "项目闭环" in decision.audit_summary


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

    decision = parse_codex_json(raw, allow_legacy=False)

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


def test_memory_connector_config_issue_reports_expired_token(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.memory_connector]
url = "https://memory.example/mcp/"

[mcp_servers.memory_connector.http_headers]
Authorization = "Bearer eyJhbGciOiJub25lIn0.eyJleHAiOjF9."
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert memory_connector_config_issue() == "memory connector token is expired"


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
            json.dumps({"action": "no_reply", "reason": "cc only"}),
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
                    "message": json.dumps({"action": "no_reply", "reason": "cc only"}),
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
                    "content": json.dumps(
                        {"action": "send_reply", "reply_text": "收到", "reason": "direct ask"}
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
                        {"type": "text", "text": json.dumps({"action": "no_reply", "reason": "done"})}
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
                        "text": json.dumps({"action": "no_reply", "reason": "live final"}),
                    },
                }
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.reason == "live final"


def test_parse_codex_json_accepts_nonstandard_envelope_with_user_response():
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

    decision = parse_codex_json(raw, allow_legacy=False)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "可以，我先按这个日报每天看当天新增。"
    assert decision.sensitivity_kind == "internal_personnel"
    assert decision.audit_summary == "已读取日报并判断风险。"
    assert decision.system_actions[0]["type"] == "persist_daily_doc_review_watch"


def test_parse_nonstandard_envelope_preserves_domain_payload():
    raw = json.dumps(
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
            "audit": {
                "summary": "已结合岗位信息判断。",
                "documents": [],
            },
        },
        ensure_ascii=False,
    )

    decision = parse_codex_json(raw, allow_legacy=False)

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
                        "message": json.dumps(
                            {
                                "action": "send_reply",
                                "reply_text": "收到",
                                "audit_summary": "只需上下文判断。",
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    decision = parse_codex_json(raw)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "收到"


def test_invalid_json_retries_once(tmp_path: Path):
    executor = FakeExecutor(
        [
            "not json",
            _agent_envelope_json(
                kind="no_action",
                mode="no_reply",
                summary="无需回复，消息只是抄送。",
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.NO_REPLY
    assert len(executor.commands) == 2
    assert executor.commands[0][:4] == [
        "codex",
        "exec",
        "resume",
        "--json",
    ]
    assert executor.commands[0][-2:] == ["session-1", "-"]
    assert 'approvals_reviewer="auto_review"' in executor.commands[0]
    assert executor.commands[1][:4] == [
        "codex",
        "exec",
        "resume",
        "--json",
    ]
    assert executor.commands[1][-2] == "session-1"
    assert "只输出合法 JSON" in executor.prompts[1]
    assert '"kind":"reply|no_action|error"' in executor.prompts[1]
    assert '"mode":"send_reply|ask_clarifying_question|handoff_to_human|no_reply"' in executor.prompts[1]


def test_runner_reads_current_session_when_stdout_has_no_decision(tmp_path: Path):
    session_id = "thread-1"
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "05"
        / "27"
        / f"rollout-2026-05-27T06-51-23-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": session_id}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": _agent_envelope_json(
                                        mode="send_reply",
                                        text="今晚只放一个主目标。",
                                        summary="只需上下文判断。",
                                    ),
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    executor = FakeExecutor(
        [json.dumps({"type": "thread.started", "thread_id": session_id})]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "今晚只放一个主目标。"
    assert runner.last_session_id == session_id
    assert len(executor.commands) == 1


def test_runner_tracks_audit_tool_events(tmp_path: Path):
    executor = FakeExecutor(
        [
            "\n".join(
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
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    runner.decide(prompt="decide", session_id=None)

    assert runner.last_audit_tool_events[0]["tool"] == "exec_command"
    assert "rg -n" in runner.last_audit_tool_events[0]["command"]


def test_runner_tracks_dws_material_read_audit_events(tmp_path: Path):
    command = (
        "dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 "
        "--format json"
    )
    executor = FakeExecutor(
        [
            "\n".join(
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
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    runner.decide(prompt="decide", session_id=None)

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


def test_empty_reply_for_reply_action_retries_once(tmp_path: Path):
    executor = FakeExecutor(
        [
            json.dumps({"action": "send_reply", "reply_text": ""}),
            _agent_envelope_json(
                mode="send_reply",
                text="收到，我看一下",
                summary="只需当前消息判断，基于当前消息可直接确认。",
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "收到，我看一下"
    assert len(executor.commands) == 2
    assert "user_response.text 必须非空" in executor.prompts[1]


def test_decide_forwards_images_to_initial_and_repair_turns(tmp_path: Path):
    image = tmp_path / "diagram.png"
    executor = FakeExecutor(
        [
            json.dumps({"action": "send_reply", "reply_text": ""}),
            _agent_envelope_json(
                mode="send_reply",
                text="这张图可以放官网。",
                summary="只需当前消息判断，并结合图片内容；未找到文档证据。",
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(
        prompt="decide",
        session_id="session-1",
        image_paths=[image],
    )

    assert decision.reply_text == "这张图可以放官网。"
    assert executor.commands[0][-4:] == ["--image", str(image), "session-1", "-"]
    assert executor.commands[1][-4:] == ["--image", str(image), "session-1", "-"]


def test_first_turn_invalid_json_retries_with_extracted_session_id(tmp_path: Path):
    executor = FakeExecutor(
        [
            "\n".join(
                [
                    json.dumps({"type": "session", "id": "new-session"}),
                    json.dumps({"type": "agent_message", "message": "not json"}),
                ]
            ),
            _agent_envelope_json(
                kind="no_action",
                mode="no_reply",
                summary="修复后判断无需回复。",
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert runner.last_session_id == "new-session"
    assert executor.commands[0][:3] == [
        "codex",
        "exec",
        "--json",
    ]
    assert executor.commands[0][-3:] == ["--cd", str(tmp_path), "-"]
    assert 'approvals_reviewer="auto_review"' in executor.commands[0]
    assert executor.commands[1][:4] == [
        "codex",
        "exec",
        "resume",
        "--json",
    ]
    assert executor.commands[1][-2] == "new-session"


def test_first_turn_invalid_json_retries_with_thread_started_id(tmp_path: Path):
    executor = FakeExecutor(
        [
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": "not json"},
                        }
                    ),
                ]
            ),
            _agent_envelope_json(
                kind="no_action",
                mode="no_reply",
                summary="修复后判断无需回复。",
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert runner.last_session_id == "thread-1"
    assert executor.commands[0][:3] == [
        "codex",
        "exec",
        "--json",
    ]
    assert executor.commands[0][-3:] == ["--cd", str(tmp_path), "-"]
    assert 'approvals_reviewer="auto_review"' in executor.commands[0]
    assert executor.commands[1][:4] == [
        "codex",
        "exec",
        "resume",
        "--json",
    ]
    assert executor.commands[1][-2] == "thread-1"


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
                                "text": json.dumps(
                                    {
                                        "action": "send_reply",
                                        "reply_text": "按这个口径推进。",
                                        "reason": "direct business follow-up",
                                        "audit_documents": [],
                                        "audit_summary": "只需上下文判断，当前消息足够确认回复。",
                                    },
                                    ensure_ascii=False,
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
                        "last_agent_message": json.dumps(
                            {
                                "action": "no_reply",
                                "reason": "ack only",
                                "audit_summary": "对方只是确认收到，无需回复。",
                            },
                            ensure_ascii=False,
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


def test_invalid_json_waits_for_session_decision_before_repair(tmp_path: Path):
    executor = FakeExecutor(
        [
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                    json.dumps({"type": "turn.started"}),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {"type": "reasoning", "text": "thinking"},
                        }
                    ),
                ]
            )
        ]
    )
    runner = make_runner(tmp_path, executor=executor)
    waits: list[int] = []
    session_decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="按这个口径推进。",
        audit_documents=[],
        audit_summary="只需上下文判断，当前消息足够确认回复。",
    )

    def fake_current_session_decision(wait_seconds: int = 0):
        waits.append(wait_seconds)
        return session_decision if wait_seconds > 0 else None

    runner._current_session_decision = fake_current_session_decision  # type: ignore[method-assign]

    decision = runner.decide(prompt="decide", session_id="thread-1")

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "按这个口径推进。"
    assert waits == [15]
    assert len(executor.commands) == 1


def test_invalid_json_twice_returns_stop_with_error(tmp_path: Path):
    executor = FakeExecutor(["not json", "still not json"])
    runner = make_runner(tmp_path, executor=executor)
    runner._current_session_decision = lambda wait_seconds=0: None  # type: ignore[method-assign]

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert "invalid JSON" in decision.reason


def test_missing_audit_summary_retries_once(tmp_path: Path):
    executor = FakeExecutor(
        [
            json.dumps({"action": "no_reply", "reason": "cc only"}),
            _agent_envelope_json(
                kind="no_action",
                mode="no_reply",
                summary="消息只是抄送，无需回复。",
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.NO_REPLY
    assert decision.audit_summary == "消息只是抄送，无需回复。"
    assert len(executor.commands) == 2
    assert "audit.summary 必须非空" in executor.prompts[1]


def test_reply_with_empty_audit_documents_accepts_nonempty_audit_summary(
    tmp_path: Path,
):
    executor = FakeExecutor(
        [
            _agent_envelope_json(
                mode="send_reply",
                text="先按A方案走",
                summary="未使用可作为业务依据的文档材料；本次判断主要基于当前群聊上下文和直接@明哥的管理同步规则。",
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.audit_summary.startswith("未使用可作为业务依据的文档材料")
    assert len(executor.commands) == 1


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


def test_subprocess_executor_passes_timeout(tmp_path: Path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return ProcessRunResult(
            returncode=0,
            stdout=_agent_envelope_json(
                kind="no_action",
                mode="no_reply",
                summary="无需回复。",
            ),
            stderr="",
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path, timeout_seconds=7, idle_timeout_seconds=3)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert calls[0][1]["total_timeout_seconds"] == 7
    assert calls[0][1]["idle_timeout_seconds"] == 3
    assert calls[0][1]["prompt"] == "decide"


def test_subprocess_timeout_returns_stop_with_error(tmp_path: Path, monkeypatch):
    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=-15,
            stdout="",
            stderr="",
            timed_out=True,
            timeout_kind="total",
            timeout_reason="process timed out after 7 seconds",
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path, timeout_seconds=7)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert "timed out" in decision.reason


def test_subprocess_idle_timeout_returns_stop_with_error(tmp_path: Path, monkeypatch):
    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=-15,
            stdout="",
            stderr="",
            timed_out=True,
            timeout_kind="idle",
            timeout_reason="process produced no output for 3 seconds",
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path, timeout_seconds=7, idle_timeout_seconds=3)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert decision.reason == "process produced no output for 3 seconds"


def test_subprocess_timeout_uses_finished_session_decision(
    tmp_path: Path, monkeypatch
):
    session_id = "thread-timeout-1"
    session_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "05"
        / "27"
        / f"rollout-2026-05-27T07-21-00-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": session_id}}),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "message": json.dumps(
                                json.loads(
                                    _agent_envelope_json(
                                        mode="send_reply",
                                        text="这版可以先发，先改四个硬伤。",
                                        summary="已查看材料并给出反馈。",
                                        documents=[
                                            {
                                                "title": "BP",
                                                "url": "tmp/bp.pdf",
                                                "relevance": "用于审核反馈。",
                                            }
                                        ],
                                    )
                                ),
                                ensure_ascii=False,
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=-15,
            stdout=json.dumps({"type": "thread.started", "thread_id": session_id}),
            stderr="",
            timed_out=True,
            timeout_kind="total",
            timeout_reason="process timed out after 7 seconds",
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path, timeout_seconds=7)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "这版可以先发，先改四个硬伤。"
    assert runner.last_session_id == session_id


def test_subprocess_nonzero_keeps_stdout_decision(tmp_path: Path, monkeypatch):
    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=1,
            stdout=_agent_envelope_json(
                kind="no_action",
                mode="no_reply",
                summary="stdout 已经有合法决策。",
            ),
            stderr="warning only",
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.reason == "stdout 已经有合法决策。"


def test_subprocess_nonzero_preserves_thread_id_for_error(tmp_path: Path, monkeypatch):
    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=1,
            stdout=json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            stderr="fatal schema error",
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert runner.last_session_id == "thread-1"
    assert "fatal schema error" in decision.reason


def test_subprocess_nonzero_reports_error_line_before_startup_warning(
    tmp_path: Path, monkeypatch
):
    stderr = "\n".join(
        [
            "2026-05-26T20:24:01Z WARN codex_core_plugins::startup_remote_sync: startup remote plugin sync failed; will retry",
            "2026-05-26T20:24:02Z ERROR codex_api::endpoint::responses_websocket: failed to connect to websocket: HTTP error: 401 Unauthorized",
            "2026-05-26T20:24:02Z WARN codex_core::session::turn: stream disconnected",
        ]
    )

    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=1,
            stdout=json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            stderr=stderr,
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert "ERROR codex_api" in decision.reason
    assert "401 Unauthorized" in decision.reason
    assert "startup_remote_sync" not in decision.reason


def test_subprocess_nonzero_reports_stdout_error_before_warning_only_stderr(
    tmp_path: Path, monkeypatch
):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "error",
                    "message": "Your workspace is out of credits. Ask your workspace owner to refill in order to continue.",
                }
            ),
            json.dumps({"type": "turn.failed"}),
        ]
    )
    stderr = (
        "2026-06-10T00:01:15.198160Z WARN codex_file_watcher: "
        "failed to unwatch /Users/derek/Documents/memory/.agents/skills: No watch was found."
    )

    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=1,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert "out of credits" in decision.reason
    assert "failed to unwatch" not in decision.reason


def test_subprocess_nonzero_warning_only_stderr_uses_generic_failure_reason(
    tmp_path: Path, monkeypatch
):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "task_complete", "last_agent_message": None}),
        ]
    )
    stderr = (
        "2026-06-10T00:01:15.198160Z WARN codex_file_watcher: "
        "failed to unwatch /Users/derek/Documents/memory/.agents/skills: No watch was found."
    )

    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=1,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr(codex_decision, "run_process_with_idle_timeout", fake_run)
    runner = make_runner(tmp_path)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert decision.reason == "codex exec failed without a valid AgentEnvelope"
    assert "failed to unwatch" not in decision.reason
