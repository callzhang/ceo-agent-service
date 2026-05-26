import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from ceo_agent_service.codex_decision import (
    CodexDecisionRunner,
    append_signature,
    contains_forbidden_leak,
    extract_codex_audit_events,
    extract_codex_session_id,
    parse_codex_json,
)
from ceo_agent_service.dingtalk_models import CodexAction, CodexDecision


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
) -> CodexDecisionRunner:
    return CodexDecisionRunner(
        workspace=tmp_path,
        executor=executor,
        timeout_seconds=timeout_seconds,
        codex_home=tmp_path,
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
                            "cmd": "sed -n '1,120p' /Users/derek/Documents/memory/面试/岗位画像.md"
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
            "command": "sed -n '1,120p' /Users/derek/Documents/memory/面试/岗位画像.md",
            "path": "/Users/derek/Documents/memory/面试/岗位画像.md",
        }
    ]


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
            json.dumps(
                {
                    "action": "no_reply",
                    "reason": "cc only",
                    "audit_summary": "无需回复，消息只是抄送。",
                }
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.NO_REPLY
    assert len(executor.commands) == 2
    assert executor.commands[0][:4] == ["codex", "exec", "resume", "--json"]
    assert executor.commands[0][-2:] == ["session-1", "-"]
    assert 'approvals_reviewer="auto_review"' in executor.commands[0]
    assert executor.commands[1][:4] == ["codex", "exec", "resume", "--json"]
    assert executor.commands[1][-2] == "session-1"
    assert "只输出合法 JSON" in executor.prompts[1]
    assert "audit_documents" in executor.prompts[1]


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
                                    "cmd": "rg -n 岗位 /Users/derek/Documents/memory/面试"
                                },
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "action": "no_reply",
                            "reason": "handled",
                            "audit_summary": "已检查上下文，问题已处理。",
                        },
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


def test_empty_reply_for_reply_action_retries_once(tmp_path: Path):
    executor = FakeExecutor(
        [
            json.dumps({"action": "send_reply", "reply_text": ""}),
            json.dumps(
                {
                    "action": "send_reply",
                    "reply_text": "收到，我看一下",
                    "audit_summary": "只需当前消息判断，基于当前消息可直接确认。",
                },
                ensure_ascii=False,
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.reply_text == "收到，我看一下"
    assert len(executor.commands) == 2
    assert "reply_text 必须非空" in executor.prompts[1]


def test_first_turn_invalid_json_retries_with_extracted_session_id(tmp_path: Path):
    executor = FakeExecutor(
        [
            "\n".join(
                [
                    json.dumps({"type": "session", "id": "new-session"}),
                    json.dumps({"type": "agent_message", "message": "not json"}),
                ]
            ),
            json.dumps(
                {
                    "action": "no_reply",
                    "reason": "repaired",
                    "audit_summary": "修复后判断无需回复。",
                },
                ensure_ascii=False,
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert runner.last_session_id == "new-session"
    assert executor.commands[0][:3] == ["codex", "exec", "--json"]
    assert executor.commands[0][-3:] == ["--cd", str(tmp_path), "-"]
    assert 'approvals_reviewer="auto_review"' in executor.commands[0]
    assert executor.commands[1][:4] == ["codex", "exec", "resume", "--json"]
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
            json.dumps(
                {
                    "action": "no_reply",
                    "reason": "repaired",
                    "audit_summary": "修复后判断无需回复。",
                },
                ensure_ascii=False,
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert runner.last_session_id == "thread-1"
    assert executor.commands[0][:3] == ["codex", "exec", "--json"]
    assert executor.commands[0][-3:] == ["--cd", str(tmp_path), "-"]
    assert 'approvals_reviewer="auto_review"' in executor.commands[0]
    assert executor.commands[1][:4] == ["codex", "exec", "resume", "--json"]
    assert executor.commands[1][-2] == "thread-1"


def test_invalid_json_twice_returns_stop_with_error(tmp_path: Path):
    executor = FakeExecutor(["not json", "still not json"])
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert "invalid JSON" in decision.reason


def test_missing_audit_summary_retries_once(tmp_path: Path):
    executor = FakeExecutor(
        [
            json.dumps({"action": "no_reply", "reason": "cc only"}),
            json.dumps(
                {
                    "action": "no_reply",
                    "reason": "cc only",
                    "audit_summary": "消息只是抄送，无需回复。",
                },
                ensure_ascii=False,
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.NO_REPLY
    assert decision.audit_summary == "消息只是抄送，无需回复。"
    assert len(executor.commands) == 2
    assert "audit_summary 必须非空" in executor.prompts[1]


def test_missing_audit_documents_for_reply_retries_once(tmp_path: Path):
    executor = FakeExecutor(
        [
            json.dumps(
                {
                    "action": "send_reply",
                    "reply_text": "先按A方案走",
                    "audit_summary": "基于当前消息可直接判断。",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "action": "send_reply",
                    "reply_text": "先按A方案走",
                    "audit_documents": [],
                    "audit_summary": "只需上下文判断，当前消息已足够确认先按A方案走。",
                },
                ensure_ascii=False,
            ),
        ]
    )
    runner = make_runner(tmp_path, executor=executor)

    decision = runner.decide(prompt="decide", session_id="session-1")

    assert decision.action == CodexAction.SEND_REPLY
    assert decision.audit_summary.startswith("只需上下文判断")
    assert len(executor.commands) == 2
    assert "audit_documents 为空" in executor.prompts[1]


def test_append_signature_once():
    assert append_signature("收到") == "收到（by磊哥分身）"
    assert append_signature("收到（by磊哥分身）") == "收到（by磊哥分身）"


def test_detects_forbidden_leaks():
    assert contains_forbidden_leak("/Users/derek/Documents/memory/secret.md") is True
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
    assert contains_forbidden_leak("/home/derek/secret.md") is True
    assert contains_forbidden_leak("正常回复（by磊哥分身）") is False


def test_subprocess_executor_passes_timeout(tmp_path: Path, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"action": "no_reply", "audit_summary": "无需回复。"},
                ensure_ascii=False,
            ),
            stderr="",
        )

    monkeypatch.setattr("ceo_agent_service.codex_decision.subprocess.run", fake_run)
    runner = make_runner(tmp_path, timeout_seconds=7)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert calls[0][1]["timeout"] == 7
    assert calls[0][1]["input"] == "decide"


def test_subprocess_timeout_returns_stop_with_error(tmp_path: Path, monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("ceo_agent_service.codex_decision.subprocess.run", fake_run)
    runner = make_runner(tmp_path, timeout_seconds=7)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert "timed out" in decision.reason


def test_subprocess_nonzero_keeps_stdout_decision(tmp_path: Path, monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "action": "no_reply",
                    "reason": "stdout decision",
                    "audit_summary": "stdout 已经有合法决策。",
                },
                ensure_ascii=False,
            ),
            stderr="warning only",
        )

    monkeypatch.setattr("ceo_agent_service.codex_decision.subprocess.run", fake_run)
    runner = make_runner(tmp_path)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.NO_REPLY
    assert decision.reason == "stdout decision"


def test_subprocess_nonzero_preserves_thread_id_for_error(tmp_path: Path, monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            stderr="fatal schema error",
        )

    monkeypatch.setattr("ceo_agent_service.codex_decision.subprocess.run", fake_run)
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

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            stderr=stderr,
        )

    monkeypatch.setattr("ceo_agent_service.codex_decision.subprocess.run", fake_run)
    runner = make_runner(tmp_path)

    decision = runner.decide(prompt="decide", session_id=None)

    assert decision.action == CodexAction.STOP_WITH_ERROR
    assert "ERROR codex_api" in decision.reason
    assert "401 Unauthorized" in decision.reason
    assert "startup_remote_sync" not in decision.reason
