from pathlib import Path
import json

import pytest
from pydantic import ValidationError

import app.oa_approval as oa_approval
from app.agent_envelope import AgentEnvelope
from app.codex_runner import CODEX_BYPASS_APPROVALS_AND_SANDBOX
from app.oa_approval import (
    OA_APPROVAL_SCHEMA_PATH,
    OaApprovalReviewClient,
    OaApprovalResult,
    extract_oa_url,
)
from app.process_runner import ProcessRunResult
from app.structured_agent import StructuredCodexRunner


def _developer_instructions_arg(command: list[str]) -> str:
    for index, item in enumerate(command):
        if item != "-c":
            continue
        value = command[index + 1]
        if value.startswith("developer_instructions="):
            return value
    raise AssertionError("developer_instructions config missing")


def _oa_envelope_json(
    *,
    action: str = "退回",
    remark: str = "请补充材料。",
    action_result: dict | None = None,
    summary: str = "已审阅，材料不足。",
) -> str:
    return AgentEnvelope.model_validate(
        {
            "kind": "oa_approval",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [],
            "domain_payload": {
                "process_instance_id": "proc-1",
                "task_id": "task-1",
                "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
                "oa_action": action,
                "oa_remark": remark,
                "action_result": action_result or {},
                "audit_summary": summary,
                "audit_documents": [],
            },
            "audit": {"summary": summary, "documents": [], "confidence": 0.8},
        }
    ).model_dump_json()


def test_valid_result_accepts_approve_action_and_stores_remark():
    result = OaApprovalResult(
        process_instance_id="proc-1",
        task_id="task-1",
        oa_url="https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=proc-1",
        oa_action="通过",
        oa_remark="同意，预算归属清晰。",
        action_result={"ok": True},
        audit_summary="已核对申请正文和审批记录。",
        audit_documents=[
            {
                "title": "审批详情",
                "url": "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=proc-1",
                "relevance": "预算归属清晰",
            }
        ],
    )

    assert result.oa_action == "通过"
    assert result.oa_remark == "同意，预算归属清晰。"


def test_result_rejects_non_aflow_url_and_mismatched_process_id():
    with pytest.raises(ValidationError):
        OaApprovalResult(
            process_instance_id="proc-1",
            task_id="task-1",
            oa_url="https://example.com/detail?procInstId=proc-1",
            oa_action="通过",
            oa_remark="同意。",
            action_result={},
            audit_summary="已审阅。",
            audit_documents=[],
        )

    with pytest.raises(ValidationError):
        OaApprovalResult(
            process_instance_id="proc-1",
            task_id="task-1",
            oa_url="https://aflow.dingtalk.com/detail?procInstId=other",
            oa_action="退回",
            oa_remark="请补材料。",
            action_result={},
            audit_summary="已审阅。",
            audit_documents=[],
        )


def test_result_accepts_missing_identifiers_for_material_insufficient_review():
    result = OaApprovalResult(
        process_instance_id="",
        task_id="",
        oa_url="",
        oa_action="退回",
        oa_remark="材料不足，暂不处理。",
        action_result={},
        audit_summary="未取得审批详情，不能执行审批动作。",
        audit_documents=[],
    )

    assert result.process_instance_id == ""
    assert result.task_id == ""
    assert result.oa_url == ""


def test_extract_oa_url_decodes_encoded_aflow_url_inside_dingtalk_card():
    encoded_url = (
        "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fmobile%2Fhomepage.htm"
        "%3FprocInstId%3Dproc-1%26taskId%3Dtask-1"
    )
    text = f'{{"pcLink":"dingtalk://dingtalkclient/page/link?url={encoded_url}"}}'

    assert extract_oa_url(text) == (
        "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm"
        "?procInstId=proc-1&taskId=task-1"
    )


def test_extract_oa_url_ignores_outer_wrapper_params_and_trailing_punctuation():
    encoded_url = (
        "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fmobile%2Fhomepage.htm"
        "%3FprocInstId%3Dproc-1%26taskId%3Dtask-1"
    )
    text = (
        f"(dingtalk://dingtalkclient/page/link?url={encoded_url}"
        "&pc_slide=false)"
    )

    assert extract_oa_url(text) == (
        "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm"
        "?procInstId=proc-1&taskId=task-1"
    )


def test_extract_oa_url_strips_sentence_period_from_direct_url():
    text = "请处理 https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1."

    assert (
        extract_oa_url(text)
        == "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )


def test_runner_injects_skill_uses_schema_parses_result_and_records_session(
    tmp_path: Path, monkeypatch
):
    skill_path = tmp_path / "skills" / "dingtalk-oa-approval" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# OA Skill\n\n审批前先审阅。", encoding="utf-8")
    monkeypatch.setenv("HOME", "/Users/principal")

    calls: list[tuple[list[str], str]] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        calls.append((command, prompt))
        return "\n".join(
            [
                json.dumps({"type": "session", "id": "session-1"}),
                json.dumps(
                    {
                        "item": {
                            "type": "tool_call",
                            "tool_name": "functions.exec_command",
                            "cmd": "dws oa approval detail proc-1",
                        }
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    AgentEnvelope.model_validate(
                        {
                            "kind": "oa_approval",
                            "user_response": {
                                "mode": "no_reply",
                                "text": "",
                                "sensitivity_kind": "internal_personnel",
                            },
                            "system_actions": [
                                {
                                    "type": "dws_oa_approval_action",
                                    "process_instance_id": "proc-1",
                                    "task_id": "task-1",
                                    "action": "通过",
                                    "remark": "同意。",
                                }
                            ],
                            "domain_payload": {
                                "process_instance_id": "proc-1",
                                "task_id": "task-1",
                                "oa_url": "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=proc-1",
                                "oa_action": "通过",
                                "oa_remark": "同意。",
                                "action_result": {"success": True},
                                "audit_summary": "已审阅并通过。",
                                "audit_documents": [
                                    {
                                        "title": "审批流水",
                                        "url": "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=proc-1",
                                        "relevance": "审批链完整",
                                    }
                                ],
                            },
                            "audit": {
                                "summary": "已审阅并通过。",
                                "documents": [],
                                "confidence": 0.9,
                            },
                        }
                    ).model_dump(mode="json"),
                    ensure_ascii=False,
                ),
            ]
        )

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        codex_bin="codex",
        executor=fake_executor,
        skill_path=skill_path,
    )

    result = runner.run("请审批", session_id=None)

    command, prompt = calls[0]
    developer_arg = _developer_instructions_arg(command)
    assert "# OA Skill" in developer_arg
    assert "审批前先审阅。" in developer_arg
    assert CODEX_BYPASS_APPROVALS_AND_SANDBOX in command
    assert command[command.index("--disable") + 1] == "hooks"
    assert "--output-schema" not in command
    assert isinstance(runner.runner, StructuredCodexRunner)
    assert prompt == "请审批"
    assert result.process_instance_id == "proc-1"
    assert runner.last_session_id == "session-1"
    assert runner.last_transcript_start_line == 0
    assert runner.last_transcript_end_line == 0
    assert runner.last_audit_tool_events == [
        {
            "tool": "functions.exec_command",
            "command": "dws oa approval detail proc-1",
        }
    ]


def test_resume_command_omits_agent_envelope_output_schema(tmp_path: Path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        calls.append(command)
        return _oa_envelope_json(
            action="拒绝",
            remark="依据不足，拒绝。",
            summary="已审阅。",
        )

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=fake_executor,
        skill_path=skill_path,
    )
    runner.runner.session_exists = lambda _session_id: True

    runner.run("继续处理", session_id="session-1")

    command = calls[0]
    assert command[:3] == ["codex", "exec", "resume"]
    assert command[command.index("--disable") + 1] == "hooks"
    assert "--output-schema" not in command


def test_parse_oa_approval_json_accepts_item_completed_message_output_text():
    result_json = {
        "process_instance_id": "proc-1",
        "task_id": "task-1",
        "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
        "oa_action": "退回",
        "oa_remark": "请补充付款依据、预算归属和验收材料。",
        "action_result": {},
        "audit_summary": "已审阅审批详情、流水和付款材料，材料不足。",
        "audit_documents": [],
    }
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
                                "text": json.dumps(result_json, ensure_ascii=False),
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    result = oa_approval.parse_oa_approval_json(raw)

    assert result.process_instance_id == "proc-1"
    assert result.oa_action == "退回"
    assert result.oa_remark.startswith("请补充付款依据")


def test_parse_oa_approval_json_ignores_empty_task_id_query_value():
    result_json = {
        "process_instance_id": "proc-1",
        "task_id": "task-1",
        "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=",
        "oa_action": "退回",
        "oa_remark": "请补充面试记录和试用期考核标准。",
        "action_result": {},
        "audit_summary": "已审阅审批详情，材料不足。",
        "audit_documents": [],
    }
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(
                    {
                        "kind": "oa_approval",
                        "user_response": {
                            "mode": "no_reply",
                            "text": "",
                            "sensitivity_kind": "internal_personnel",
                        },
                        "system_actions": [],
                        "domain_payload": result_json,
                        "audit": {
                            "summary": "已审阅审批详情，材料不足。",
                            "documents": [],
                            "confidence": 0.8,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        },
        ensure_ascii=False,
    )

    result = oa_approval.parse_oa_approval_json(raw, allow_legacy=False)

    assert result.task_id == "task-1"
    assert result.oa_action == "退回"


def test_parse_oa_approval_json_accepts_task_complete_last_agent_message():
    result_json = {
        "process_instance_id": "proc-1",
        "task_id": "task-1",
        "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
        "oa_action": "通过",
        "oa_remark": "同意。",
        "action_result": {},
        "audit_summary": "已审阅审批详情和流水。",
        "audit_documents": [],
    }
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": json.dumps(
                            result_json,
                            ensure_ascii=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    result = oa_approval.parse_oa_approval_json(raw)

    assert result.oa_action == "通过"
    assert result.audit_summary == "已审阅审批详情和流水。"


def test_parse_oa_approval_json_accepts_agent_envelope_domain_payload():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "oa_approval",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [
                {
                    "type": "dws_oa_approval_action",
                    "process_instance_id": "proc-1",
                    "task_id": "task-1",
                    "action": "通过",
                    "remark": "同意。",
                }
            ],
            "domain_payload": {
                "process_instance_id": "proc-1",
                "task_id": "task-1",
                "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
                "oa_action": "通过",
                "oa_remark": "同意。",
                "action_result": {},
                "audit_summary": "已审阅。",
                "audit_documents": [],
            },
            "audit": {"summary": "已审阅。", "documents": [], "confidence": 0.9},
        }
    )

    result = oa_approval.parse_oa_approval_json(envelope.model_dump_json())

    assert result.process_instance_id == "proc-1"
    assert result.task_id == "task-1"
    assert result.oa_action == "通过"
    assert result.oa_remark == "同意。"


def test_parse_oa_approval_json_normalizes_string_audit_documents():
    raw = json.dumps(
        {
            "kind": "oa_approval",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [],
            "domain_payload": {
                "process_instance_id": "proc-1",
                "task_id": "task-1",
                "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
                "oa_action": "退回",
                "oa_remark": "请补充材料。",
                "action_result": {},
                "audit_summary": "已审阅，材料不足。",
                "audit_documents": ["OA审批表单详情"],
            },
            "audit": {
                "summary": "已审阅，材料不足。",
                "documents": ["OA审批表单详情"],
                "confidence": 0.8,
            },
        },
        ensure_ascii=False,
    )

    result = oa_approval.parse_oa_approval_json(raw, allow_legacy=False)

    assert result.audit_documents == [
        {"name": "OA审批表单详情", "status": "mentioned"}
    ]


def test_parse_oa_approval_json_strict_rejects_legacy_result():
    raw = json.dumps(
        {
            "process_instance_id": "proc-1",
            "task_id": "task-1",
            "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
            "oa_action": "通过",
            "oa_remark": "同意。",
            "action_result": {},
            "audit_summary": "已审阅。",
            "audit_documents": [],
        },
        ensure_ascii=False,
    )

    with pytest.raises(json.JSONDecodeError, match="AgentEnvelope"):
        oa_approval.parse_oa_approval_json(raw, allow_legacy=False)


def test_parse_oa_approval_json_rejects_wrong_agent_envelope_kind():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "收到。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {"summary": "普通回复。", "documents": [], "confidence": 0.9},
        }
    )

    with pytest.raises(ValidationError, match="oa_approval"):
        oa_approval.parse_oa_approval_json(envelope.model_dump_json())


def test_invalid_oa_json_retries_once_with_repair_prompt(tmp_path: Path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")
    prompts: list[str] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        del command
        prompts.append(prompt)
        if len(prompts) == 2:
            return _oa_envelope_json()
        return "\n".join(
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

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=fake_executor,
        skill_path=skill_path,
    )

    result = runner.run("处理审批", allow_side_effects=False)

    assert result.oa_action == "退回"
    assert len(prompts) == 2
    assert "上一次输出不是合法 OA 审批 AgentEnvelope JSON" in prompts[1]


def test_invalid_oa_json_repair_prompt_requests_agent_envelope(tmp_path: Path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")
    prompts: list[str] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return "not json"
        return AgentEnvelope.model_validate(
            {
                "kind": "oa_approval",
                "user_response": {
                    "mode": "no_reply",
                    "text": "",
                    "sensitivity_kind": "internal_personnel",
                },
                "system_actions": [],
                "domain_payload": {
                    "process_instance_id": "proc-1",
                    "task_id": "task-1",
                    "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
                    "oa_action": "退回",
                    "oa_remark": "请补充材料。",
                    "action_result": {},
                    "audit_summary": "已审阅，材料不足。",
                    "audit_documents": [],
                },
                "audit": {
                    "summary": "已审阅，材料不足。",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        ).model_dump_json()

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=fake_executor,
        skill_path=skill_path,
    )

    result = runner.run("处理审批", allow_side_effects=False)

    assert result.oa_action == "退回"
    assert '"kind":"oa_approval"' in prompts[1]
    assert '"domain_payload"' in prompts[1]
    assert "旧 OA approval JSON" not in prompts[1]


def test_output_schema_uses_strict_object_shapes_required_by_codex():
    schema = json.loads(OA_APPROVAL_SCHEMA_PATH.read_text(encoding="utf-8"))

    def assert_strict_objects(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
        for value in node.values():
            if isinstance(value, dict):
                assert_strict_objects(value)
            elif isinstance(value, list):
                for item in value:
                    assert_strict_objects(item)

    assert_strict_objects(schema)


def test_read_only_handle_allows_dws_reads_and_requires_empty_action_result(
    tmp_path: Path,
):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        calls.append(command)
        return _oa_envelope_json(summary="只读审阅，未执行审批动作。")

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=fake_executor,
        skill_path=skill_path,
    )

    result = runner.handle("触发消息", "", "", execute=False)

    command = calls[0]
    assert result.action_result == {}
    assert CODEX_BYPASS_APPROVALS_AND_SANDBOX in command
    assert 'approval_policy="never"' in command
    assert 'sandbox_mode="read-only"' not in command


def test_execute_handle_warns_return_becomes_service_comment(tmp_path: Path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")
    prompts: list[str] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        prompts.append(prompt)
        return _oa_envelope_json(
            action="拒绝",
            remark="材料不符合规则，拒绝。",
            summary="已审阅。",
        )

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=fake_executor,
        skill_path=skill_path,
    )

    runner.handle("触发消息", "", "", execute=True)

    assert "退回会由服务作为审批单评论提交" in prompts[0]
    assert "不会用拒绝冒充退回" in prompts[0]


def test_handle_warns_dws_login_failure_is_tool_issue(tmp_path: Path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")
    prompts: list[str] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        del command
        prompts.append(prompt)
        return _oa_envelope_json(
            action="退回",
            remark="DWS 未登录，当前无法读取审批材料。",
            summary="DWS 未登录导致工具不可用。",
        )

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=fake_executor,
        skill_path=skill_path,
    )

    runner.handle(
        "触发消息",
        "",
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        approval_detail_text='{"tool_status":"dws_login_required"}',
        execute=False,
    )

    assert "DWS 未登录" in prompts[0]
    assert "工具问题" in prompts[0]
    assert "不要表述为申请人没有提供材料" in prompts[0]


def test_read_only_runner_can_use_local_dws_auth(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DWS_CLIENT_ID", "wrong-client-id")
    monkeypatch.setenv("DWS_CLIENT_SECRET", "wrong-client-secret")
    monkeypatch.setenv("DINGTALK_APP_KEY", "wrong-app-key")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "wrong-app-secret")
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        skill_path=skill_path,
    )

    env = runner.runner.runner.build_env()
    command = runner.runner._build_command(
        "review only",
        session_id=None,
        allow_side_effects=False,
    )

    assert "DWS_CLIENT_ID" not in env
    assert "DWS_CLIENT_SECRET" not in env
    assert "DINGTALK_APP_KEY" not in env
    assert "DINGTALK_APP_SECRET" not in env
    assert CODEX_BYPASS_APPROVALS_AND_SANDBOX in command
    assert 'sandbox_mode="read-only"' not in command
    assert 'approval_policy="never"' in command


def test_read_only_handle_rejects_mutating_result_or_tool_event(tmp_path: Path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")

    def nonempty_action_result(command: list[str], prompt: str) -> str:
        return _oa_envelope_json(
            action="通过",
            remark="同意。",
            action_result={"errcode": 0},
            summary="不应被接受。",
        )

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=nonempty_action_result,
        skill_path=skill_path,
    )
    with pytest.raises(RuntimeError, match="action_result"):
        runner.handle("触发消息", "", "", execute=False)

    def mutating_tool_event(command: list[str], prompt: str) -> str:
        return "\n".join(
            [
                json.dumps(
                    {
                        "item": {
                            "type": "tool_call",
                            "tool_name": "functions.exec_command",
                            "cmd": "dws oa approval approve --instance-id proc-1 --task-id task-1 --yes",
                        }
                    }
                ),
                _oa_envelope_json(
                    action="通过",
                    remark="同意。",
                    summary="不应被接受。",
                ),
            ]
        )

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=mutating_tool_event,
        skill_path=skill_path,
    )
    with pytest.raises(RuntimeError, match="mutating"):
        runner.handle("触发消息", "", "", execute=False)


def test_read_only_runner_repairs_empty_stdout_once(tmp_path: Path):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        calls.append(command)
        if len(calls) == 1:
            return ""
        return _oa_envelope_json()

    runner = OaApprovalReviewClient(
        workspace=tmp_path,
        executor=fake_executor,
        skill_path=skill_path,
    )

    result = runner.run("处理审批", allow_side_effects=False)

    assert result.oa_action == "退回"
    assert len(calls) == 2
    assert 'approval_policy="never"' in calls[1]


def test_subprocess_failure_redacts_sensitive_stderr(tmp_path: Path, monkeypatch):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")

    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=1,
            stdout="",
            stderr=(
                "failed access_token=secret-token "
                "appsecret=secret-value cookie:session-id"
            ),
        )

    monkeypatch.setattr("app.structured_agent.run_process_with_idle_timeout", fake_run)
    runner = OaApprovalReviewClient(workspace=tmp_path, skill_path=skill_path)

    with pytest.raises(RuntimeError) as excinfo:
        runner.run("处理审批")

    message = str(excinfo.value)
    assert "secret-token" not in message
    assert "secret-value" not in message
    assert "session-id" not in message
    assert "[REDACTED]" in message


def test_subprocess_failure_reports_codex_json_stdout_error(
    tmp_path: Path, monkeypatch
):
    skill_path = tmp_path / "skill.md"
    skill_path.write_text("# OA Skill", encoding="utf-8")

    def fake_run(command, **kwargs):
        return ProcessRunResult(
            returncode=1,
            stdout="\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                    json.dumps(
                        {
                            "type": "error",
                            "message": json.dumps(
                                {
                                    "error": {
                                        "code": "invalid_json_schema",
                                        "message": "Invalid schema",
                                    }
                                }
                            ),
                        }
                    ),
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr("app.structured_agent.run_process_with_idle_timeout", fake_run)
    runner = OaApprovalReviewClient(workspace=tmp_path, skill_path=skill_path)

    with pytest.raises(RuntimeError, match="invalid_json_schema: Invalid schema"):
        runner.run("处理审批")
