from pathlib import Path
import json

from ceo_agent_service.oa_approval import (
    OA_APPROVAL_SCHEMA_PATH,
    OaApprovalCodexRunner,
    OaApprovalResult,
    extract_oa_url,
)


def _developer_instructions_arg(command: list[str]) -> str:
    for index, item in enumerate(command):
        if item != "-c":
            continue
        value = command[index + 1]
        if value.startswith("developer_instructions="):
            return value
    raise AssertionError("developer_instructions config missing")


def test_valid_result_accepts_approve_action_and_stores_remark():
    result = OaApprovalResult(
        process_instance_id="proc-1",
        task_id="task-1",
        oa_url="https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=proc-1",
        oa_action="通过",
        oa_remark="同意，预算归属清晰。",
        action_result={"ok": True},
        audit_summary="已核对申请正文和审批记录。",
        audit_documents=[{"source": "detail", "summary": "预算归属清晰"}],
    )

    assert result.oa_action == "通过"
    assert result.oa_remark == "同意，预算归属清晰。"


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


def test_runner_injects_skill_uses_schema_parses_result_and_records_session(
    tmp_path: Path, monkeypatch
):
    skill_path = tmp_path / "skills" / "dingtalk-oa-approval" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# OA Skill\n\n审批前先审阅。", encoding="utf-8")
    monkeypatch.setenv("HOME", "/Users/derek")

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
                    {
                        "process_instance_id": "proc-1",
                        "task_id": "task-1",
                        "oa_url": "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=proc-1",
                        "oa_action": "通过",
                        "oa_remark": "同意。",
                        "action_result": {"success": True},
                        "audit_summary": "已审阅并通过。",
                        "audit_documents": [{"source": "records", "summary": "审批链完整"}],
                    },
                    ensure_ascii=False,
                ),
            ]
        )

    runner = OaApprovalCodexRunner(
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
    assert "--output-schema" in command
    assert command[command.index("--output-schema") + 1] == str(OA_APPROVAL_SCHEMA_PATH)
    assert runner.runner.build_env()["HOME"] == "/Users/derek"
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
