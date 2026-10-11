import json
from types import SimpleNamespace

import pytest

from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.runtime_prompt_context import render_runtime_context, runtime_prompt_snapshot
from app.wechat.codex_safety import make_role_agent_command


def route(kind=RuntimeKind.CODEX_CLI):
    return RuntimeRoute(name="selected", runtime_kind=kind,
                        credential_mode=CredentialMode.LOCAL_OAUTH, model="example-model")


def command(role, monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    value = ["codex", "exec", "-c", 'mcp_servers.exa.command="exa"', "task"]
    make_role_agent_command(value, role=role, task_workspace="/task/current" if role == "consumer" else None)
    return value


def test_context_uses_final_role_directory_and_declared_tools(monkeypatch):
    consumer = render_runtime_context(role="consumer", route=route(), command=command("consumer", monkeypatch), task=None, current_time="2026-10-05T18:00:00-07:00")
    audit = render_runtime_context(role="audit", route=route(), command=command("audit", monkeypatch), task=None, current_time="2026-10-05T18:00:00-07:00")
    assert "/task/current" in consumer
    assert "agent_cli.list_dingtalk_calendar_events" in consumer
    assert "Read the principal's events over an explicit time window" not in consumer
    assert "agent_cli.consumer_artifact_write" in consumer
    assert "agent_cli.consumer_artifact_write" not in audit
    assert "exa.web_search_exa" in consumer
    assert "命令网络关闭" in consumer
    assert "只读审核" in audit
    assert "未绑定" in consumer


def test_claude_context_does_not_inherit_codex_native_or_third_party_tools():
    text = render_runtime_context(role="consumer", route=route(RuntimeKind.CLAUDE_CLI), command=["claude", "--restricted", "--tools", "Read,Glob,Grep"], task=None, current_time="2026-10-05T18:00:00-07:00")
    assert "Read/Glob/Grep" in text
    assert "shell 执行：本轮未声明" in text
    assert "exa.web_search_exa" not in text
    assert "agent_cli.consumer_artifact_write" in text
    assert "认证状态：本轮未验证" in text


def test_source_task_identity_does_not_follow_channel_or_person_name(monkeypatch):
    task = SimpleNamespace(id=41, channel="dingtalk", execution_generation="generation-a", business_object_key="message:object", trigger_message_id="message-a", trigger_message_json=json.dumps({"raw_payload": {"scheduled_consumer": {"scheduled_task_id": 8, "scheduled_task_run_id": 19}}}))
    text = render_runtime_context(role="consumer", route=route(), command=command("consumer", monkeypatch), task=task, current_time="2026-10-05T18:00:00-07:00")
    assert "41" in text and "generation-a" in text
    assert '"scheduled_task_id": 8' in text
    assert '"scheduled_task_run_id": 19' in text
    assert "message-a" in text
    assert "参与者时区：以原请求和已读取来源为准" not in text


def test_prompt_snapshot_preserves_service_input_without_transport_secrets():
    data = runtime_prompt_snapshot(role="consumer", route=route(), runtime_attempt_id=7, task=None,
        developer_instructions="role rules", task_prompt="original task", runtime_context="context",
        current_time="2026-10-05T18:00:00-07:00")
    assert data["type"] == "runtime.prompt"
    assert data["developer_instructions"] == "role rules"
    assert data["task_prompt"] == "original task"
    assert data["runtime_attempt_id"] == 7
    assert "env" not in data and "command" not in data
    claude = runtime_prompt_snapshot(role="audit", route=route(RuntimeKind.CLAUDE_CLI), runtime_attempt_id=8, task=None,
        developer_instructions="rules", task_prompt="candidate", runtime_context="context", current_time="2026-10-05T18:00:00-07:00")
    assert claude["submitted_input"] == "<developer-instructions>\nrules\n</developer-instructions>\n<task>\ncandidate\n</task>"


def test_snapshot_metadata_uses_existing_credential_redaction():
    data = runtime_prompt_snapshot(role="consumer", route=route(), runtime_attempt_id=7, task=None,
        developer_instructions="rules", task_prompt="original", runtime_context="context",
        current_time="2026-10-05T18:00:00-07:00",
        invocation_facts={"skill_protocol": "token=private-test-secret", "participant_timezones": [
            {"source_ref": "Bearer private-token", "timezone": "Asia/Tokyo"}]})
    assert "private-test-secret" not in json.dumps(data)
    assert "private-token" not in json.dumps(data)
    assert data["redacted"] is True


def test_context_never_copies_transport_credentials(monkeypatch):
    value = command("consumer", monkeypatch)
    value.extend(["-c", 'mcp_servers.exa.env.API_KEY="private-test-secret"', "-c", 'mcp_servers.exa.http_headers.Authorization="Bearer private-token"'])
    text = render_runtime_context(role="consumer", route=route(), command=value, task=None, current_time="2026-10-05T18:00:00-07:00")
    assert "private-test-secret" not in text and "private-token" not in text


def test_context_projects_explicit_stage_and_participant_zone_sources(monkeypatch):
    facts = {"stage_index": 2, "proposal_revision": 3, "participant_timezones": [
        {"participant_id": "person-b", "timezone": "Asia/Tokyo", "source_ref": "explicit-request", "applies_on": "2026-11-04"}]}
    text = render_runtime_context(role="consumer", route=route(), command=command("consumer", monkeypatch),
        current_time="2026-10-05T18:00:00-07:00", invocation_facts=facts)
    assert "阶段：2；proposal revision：3" in text
    assert "Asia/Tokyo" in text and "2026-11-04" in text and "explicit-request" in text


def test_claude_context_uses_inherited_process_directory_not_materials_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CEO_WORKSPACE", str(tmp_path / "materials"))
    text = render_runtime_context(role="audit", route=route(RuntimeKind.CLAUDE_CLI), command=["claude"],
        current_time="2026-10-05T18:00:00-07:00")
    assert f"本轮命令工作目录：{tmp_path}" in text
    assert "consumer_document_write" not in text


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_undeclared_codex_tools_are_not_described_as_available(role):
    text = render_runtime_context(role=role, route=route(), command=["codex", "exec", "task"], task=None, current_time="2026-10-05T18:00:00-07:00")
    assert "agent_cli.list_dingtalk_calendar_events" not in text
    assert "本轮未声明" in text


def test_actual_consumer_submission_matches_submission_prompt_snapshot(tmp_path, monkeypatch):
    from app.agent_context import AgentTaskContext
    from app.consumer_agent import ConsumerAgentRunner
    from app.store import AutoReplyStore
    from tests.test_consumer_agent import CapturingExecutor, _result_jsonl
    from tests.support.native_protocol import install_protocol_native_trajectories

    install_protocol_native_trajectories(tmp_path, monkeypatch, CapturingExecutor)
    observations = []
    original_append = AutoReplyStore.append_agent_run_event

    def observe_submission(store, run_id, event, **kwargs):
        result = original_append(store, run_id, event, **kwargs)
        if event.get("type") in {"runtime.prompt", "runtime.prompt.invoked"}:
            observations.append(event)
        return result

    monkeypatch.setattr(AutoReplyStore, "append_agent_run_event", observe_submission)

    monkeypatch.setenv("CEO_WORKSPACE", str(tmp_path / "materials"))
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    store.enqueue_reply_task(conversation_id="group", conversation_title="Group", single_chat=False,
        trigger_message_id="source-message", trigger_create_time="2026-10-05T12:00:00+00:00",
        trigger_sender="Origin", trigger_text="Original request", execution_generation="one")
    task = store.claim_reply_tasks(limit=1)[0]
    context = AgentTaskContext(task_id=task.id, channel=task.channel, conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, single_chat=False, trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender, trigger_text=task.trigger_text, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(), prior_receipts=())
    executor = CapturingExecutor(_result_jsonl())
    ConsumerAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
        task, context, proposal_revision=0, parent_agent_run_id=None)
    [run] = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    [snapshot] = [event for event in observations if event.get("type") == "runtime.prompt"]
    assert not any(event.get("type") == "runtime.prompt" for event in run.tool_events)
    assert snapshot["task_prompt"] == executor.prompts[0]
    configs = {value.partition("=")[0]: value.partition("=")[2] for value in executor.commands[0] if value.startswith("developer_instructions=")}
    assert json.loads(configs["developer_instructions"]) == snapshot["developer_instructions"]
    assert snapshot["runtime_context"] in snapshot["developer_instructions"]
    assert snapshot["invocation_facts"]["stage_index"] == 0
    assert any(event.get("type") == "runtime.prompt.invoked" for event in observations)
    assert "参与者时区：以原请求" not in snapshot["developer_instructions"]
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) is None


def test_timezone_projection_drops_unrelated_fields_and_redacts_named_credentials(monkeypatch):
    from app.runtime_prompt_context import explicit_participant_timezones

    source = {"participant_timezones": [{"participant_id": "person-b", "timezone": "Asia/Tokyo",
        "source_ref": "request", "applies_on": "2026-11-04", "token": "abcdef123456",
        "private_calendar_body": "unrelated-private-body"}, {"timezone": {"token": "nested-secret"}}]}
    zones = explicit_participant_timezones(source)
    assert zones == [{"participant_id": "person-b", "timezone": "Asia/Tokyo", "source_ref": "request", "applies_on": "2026-11-04"}]
    facts = {"participant_timezones": source["participant_timezones"], "token": "abcdef123456"}
    text = render_runtime_context(role="consumer", route=route(), command=command("consumer", monkeypatch),
        current_time="2026-10-05T18:00:00-07:00", invocation_facts=facts)
    data = runtime_prompt_snapshot(role="consumer", route=route(), runtime_attempt_id=7, task=None,
        developer_instructions=text, task_prompt="original", runtime_context=text,
        current_time="2026-10-05T18:00:00-07:00", invocation_facts=facts)
    encoded = json.dumps(data)
    assert "abcdef123456" not in encoded and "unrelated-private-body" not in encoded and "nested-secret" not in encoded
    assert "Asia/Tokyo" in encoded and data["redacted"] is True


def test_context_keeps_opaque_source_payload_in_task_without_treating_it_as_metadata(monkeypatch):
    task = SimpleNamespace(id=41, channel="email", execution_generation="g", business_object_key="object",
        trigger_message_id="source", trigger_message_json=json.dumps({"raw_payload": "original opaque material"}))
    text = render_runtime_context(role="consumer", route=route(), command=command("consumer", monkeypatch),
        task=task, current_time="2026-10-05T18:00:00-07:00")
    assert "后台来源绑定：未提供" in text


def test_explicit_timezone_evidence_requires_a_nonempty_source_zone():
    from app.runtime_prompt_context import explicit_participant_timezones

    assert explicit_participant_timezones({"participant_timezones": [
        {"participant_id": "unknown-person"}, {"timezone": "  "},
        {"participant_id": "known-person", "timezone": "Europe/London"}]}) == [
            {"participant_id": "known-person", "timezone": "Europe/London"}]


@pytest.mark.parametrize('role', ['consumer', 'audit'])
@pytest.mark.parametrize('kind', [RuntimeKind.CODEX_CLI, RuntimeKind.CLAUDE_CLI])
def test_context_preserves_every_declared_tool_without_repeating_schema_descriptions(monkeypatch, role, kind):
    from app.agent_cli import build_role_server
    from app.runtime_prompt_context import declared_role_tools

    value = command(role, monkeypatch) if kind is RuntimeKind.CODEX_CLI else ['claude', '--tools', 'Read,Glob,Grep']
    selected = route(kind)
    text = render_runtime_context(role=role, route=selected, command=value, task=None, current_time='2026-10-05T18:00:00-07:00')
    tools = declared_role_tools(role, selected, value)
    expected = [f'{server}.{name}' for server, names in sorted(tools.items()) for name in names]
    actual = [line.removeprefix('  - ') for line in text.splitlines() if line.startswith('  - ')]
    assert actual == expected
    assert '参数以本轮工具 schema 为准' in text

    descriptors = {tool.name: tool for tool in build_role_server(role)._tool_manager.list_tools()}
    old_lines = []
    for server, names in sorted(tools.items()):
        for name in names:
            description = descriptors[name].description.strip().splitlines()[0] if server == 'agent_cli' and name in descriptors else ''
            old_lines.append(f'  - {server}.{name}' + (f'：{description}' if description else ''))
    current_lines = '\n'.join(f'  - {name}' for name in expected)
    baseline = text.replace(current_lines, '\n'.join(old_lines))
    assert len(text) < len(baseline) * .8


def test_snapshot_records_section_sources_without_duplicate_text():
    data = runtime_prompt_snapshot(role="consumer", route=route(), runtime_attempt_id=7, task=None,
        developer_instructions="Common\n\nRole\n\nRuntime", task_prompt="Task", runtime_context="Runtime",
        current_time="2026-10-08T10:00:00+00:00", invocation_facts={"stage_index": 0,
            "prompt_sections": [
                {"name": "通用原则", "source": "共同 Developer 工作原则", "placement": "developer", "text": "Common"},
                {"name": "角色契约", "source": "角色与输出契约", "placement": "developer", "text": "Role"},
            ]})
    assert data["sections"] == [
        {"name": "通用原则", "source": "共同 Developer 工作原则", "placement": "developer", "characters": 6},
        {"name": "角色契约", "source": "角色与输出契约", "placement": "developer", "characters": 4},
        {"name": "运行上下文", "source": "实际运行配置", "placement": "developer", "characters": 7},
        {"name": "任务输入", "source": "当前任务正文", "placement": "task", "characters": 4},
    ]
    assert "prompt_sections" not in data["invocation_facts"]
    assert data["invocation_facts"]["stage_index"] == 0
    assert data["redacted"] is False


def test_snapshot_section_lengths_count_redacted_text():
    secret_text = 'OPENAI_API_KEY="sk-private-example"'
    data = runtime_prompt_snapshot(role="consumer", route=route(), runtime_attempt_id=7, task=None,
        developer_instructions=secret_text, task_prompt="Task", runtime_context="",
        current_time="2026-10-08T10:00:00+00:00", invocation_facts={"prompt_sections": [
            {"name": "通用原则", "source": "共同 Developer 工作原则", "placement": "developer", "text": secret_text}]})
    assert data["sections"][0]["characters"] == len(data["developer_instructions"])
    assert "sk-private-example" not in json.dumps(data)
    assert data["redacted"] is True
