"""Read-only Settings projection of service inputs; never executes a turn."""

from datetime import datetime
from pathlib import Path
import sys

from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import RuntimeKind
from app.audit_rules import render_audit_rules
from app.config import workspace_path
from app.consumer_agent import consumer_developer_instructions, audit_developer_instructions, default_consumer_skill_protocol
from app.prompt_composition import append_runtime_context, load_prompt_configuration
from app.runtime_prompt_context import render_runtime_context, runtime_prompt_snapshot
from app.store import AgentRole, AutoReplyStore


PROMPT_SCOPE = "服务提交的 Developer 与 Task 输入；不包含 CLI 自行生成的系统提示、工具 schema 或会话历史。凭据脱敏处标记 [REDACTED]。"


def _saved_configuration_fingerprints(snapshot: dict | None) -> dict[str, str | None]:
    saved = (snapshot or {}).get("invocation_facts", {}).get("prompt_configuration") or {}
    return {name: saved.get(name) for name in (
        "developer_template", "developer_instructions", "user_template", "work_profile_instruction",
    )}


def _empty_item(*, mode: str, role: str) -> dict[str, object]:
    return {"mode": mode, "status": "available", "role": role, "runtime_kind": "",
            "route_name": "", "model": "", "rendered_at": "", "task_id": None, "run_id": None,
            "runtime_attempt_id": None, "execution_generation": None, "proposal_revision": None,
            "stage_index": None, "submission_state": "preview", "attempts": [],
            "developer_instructions": "", "task_prompt": "", "submitted_input": "",
            "runtime_context": "", "reason": "", "scope": PROMPT_SCOPE, "routes": [],
            "configuration_fingerprints": _saved_configuration_fingerprints(None),
            "task_source_configuration_fingerprints": _saved_configuration_fingerprints(None),
            "task_source_run_id": None, "task_source_rendered_at": None}


def historical_prompt_preview(store: AutoReplyStore, *, run_id: int, runtime_attempt_id: int | None = None) -> dict[str, object]:
    run = store.get_agent_run(run_id)
    if run is None:
        raise LookupError("运行不存在")
    result = _empty_item(mode="historical", role=run.role.value)
    result.update(run_id=run.id, task_id=run.reply_task_id)
    snapshots = [event for event in run.tool_events if event.get("type") == "runtime.prompt"]
    if not snapshots:
        result.update(status="unavailable", reason="该运行未保存实际提交输入；不能用当前配置重建或重跑补齐。")
        return result
    invoked_ids = {event.get("runtime_attempt_id") for event in run.tool_events if event.get("type") == "runtime.prompt.invoked"}
    result["attempts"] = [{"runtime_attempt_id": event["runtime_attempt_id"], "route_name": event["route_name"],
                           "rendered_at": event["rendered_at"],
                           "submission_state": "invoked" if event["runtime_attempt_id"] in invoked_ids else "prepared"}
                          for event in snapshots]
    saved = next((event for event in snapshots if event.get("runtime_attempt_id") == runtime_attempt_id), None) if runtime_attempt_id is not None else snapshots[-1]
    if saved is None:
        raise LookupError("所选运行尝试不存在")
    for key in ("role", "runtime_kind", "route_name", "model", "rendered_at",
                "developer_instructions", "task_prompt", "submitted_input", "runtime_context"):
        result[key] = saved[key]
    for key in ("runtime_attempt_id", "execution_generation", "proposal_revision"):
        result[key] = saved.get(key)
    result["stage_index"] = saved.get("invocation_facts", {}).get("stage_index")
    result["configuration_fingerprints"] = _saved_configuration_fingerprints(saved)
    result["task_source_configuration_fingerprints"] = _saved_configuration_fingerprints(saved)
    result["task_source_run_id"] = run.id
    result["task_source_rendered_at"] = saved["rendered_at"]
    invoked = saved.get("runtime_attempt_id") in invoked_ids
    result["submission_state"] = "invoked" if invoked else "prepared"
    result["reason"] = ("运行适配器已调用；此输入记录不证明模型接收、任务完成或外部动作发生。" if invoked else "仅保存准备输入，尚无运行适配器提交记录；不能称为模型已经收到。") + (" 凭据已脱敏，标记 [REDACTED]。" if saved.get("redacted") else "")
    return result


def current_prompt_preview(
    store: AutoReplyStore, *, role: str, config: AgentRuntimeConfig,
    route_name: str = "", task_id: int | None = None, current_time: str | None = None,
) -> dict[str, object]:
    from app.claude_runtime_adapter import ClaudeCommandPolicy, ClaudeRuntimeAdapter
    from app.codex_runtime_adapter import CodexRuntimeAdapter
    from app.wechat.codex_safety import ControlledCliConfig, make_role_agent_command

    if role not in ("consumer", "audit"):
        raise ValueError("角色必须为 Consumer 或 Audit")
    routes = [route for route in config.routes if route.runtime_kind is not RuntimeKind.FRIDAY_RUNTIME]
    route = next((route for route in routes if route.name == route_name), None) if route_name else (routes[0] if routes else None)
    if route is None:
        raise ValueError("所选路线未提供后台角色能力")
    result = _empty_item(mode="current", role=role)
    result["routes"] = [{"name": item.name, "runtime_kind": item.runtime_kind.value, "model": item.model} for item in routes]
    task = store.get_reply_task(task_id) if task_id is not None else None
    if task_id is not None and task is None:
        raise LookupError("任务不存在")
    rendered_at = current_time or datetime.now().astimezone().isoformat()
    source_snapshot = None
    source_run = None
    if task:
        for run in store.list_agent_runs_for_task_generation(task.id, task.execution_generation):
            if run.role.value == role:
                for event in run.tool_events:
                    if event.get("type") == "runtime.prompt":
                        source_snapshot, source_run = event, run
    facts = source_snapshot.get("invocation_facts", {}) if source_snapshot else {}
    configuration = load_prompt_configuration(create_missing=False, role=role)
    fingerprints = configuration.fingerprints()
    profile = configuration.work_profile
    if role == "consumer":
        developer = consumer_developer_instructions(runtime_context="", work_profile=profile, prompt_configuration=configuration,
            skill_protocol=facts.get("skill_protocol", "") if facts.get("skill_protocol_source") == "task_override" else default_consumer_skill_protocol())
    else:
        rules = render_audit_rules(AgentRole.AUDIT, create_missing=False)
        developer = audit_developer_instructions(rules, runtime_context="", work_profile=profile, prompt_configuration=configuration)
        if facts.get("skill_protocol_source") == "task_override" and facts.get("skill_protocol"):
            developer += "\n\n" + facts["skill_protocol"]
    prompt = source_snapshot["task_prompt"] if source_snapshot else "未绑定任务和候选：仅预览当前角色公共指令与环境。"
    if task and source_snapshot is None:
        result.update(status="unavailable", reason="该任务未保存所选角色的完整上下文输入；不以省略材料、反馈或回执的原触发拼装结果冒充完整 prompt，也不重跑补齐。")
        prompt = ""
    workspace = workspace_path()
    cwd = workspace / "consumer-artifacts" / str(task.id) / task.execution_generation if task and role == "consumer" else workspace
    if route.runtime_kind is RuntimeKind.CODEX_CLI:
        command = CodexRuntimeAdapter(workspace=workspace, config=config).build_command(
            route=route, prompt=prompt, session_id=None, image_paths=None, output_schema_path=None,
            use_output_schema=False, approval_policy="on-failure", developer_instructions=developer, use_approval_bypass=False)
        make_role_agent_command(command, role=role, task_workspace=str(cwd) if role == "consumer" else None,
            controlled_cli=ControlledCliConfig(command=sys.executable, args=("-m", "app.agent_cli", "--role", role), cwd=str(Path(__file__).resolve().parent.parent)))
    else:
        command = ClaudeRuntimeAdapter(config=config, workspace=workspace).build_command(
            route=route, session_id=None, max_turns=40,
            policy=ClaudeCommandPolicy.consumer(task_id=task.id if task else 0, db_path=str(store.path), execution_generation=task.execution_generation if task else "")
            if role == "consumer" else ClaudeCommandPolicy.audit(task_id=task.id if task else 0, db_path=str(store.path), execution_generation=task.execution_generation if task else ""))
    runtime_context = render_runtime_context(role=role, route=route, command=command, task=task,
        current_time=rendered_at, invocation_facts={**facts, "proposal_revision": source_run.proposal_revision if source_run else "未绑定"}, preview=True)
    developer = append_runtime_context(developer, runtime_context)
    result.update(runtime_prompt_snapshot(role=role, route=route, runtime_attempt_id=0, task=task,
        developer_instructions=developer, task_prompt=prompt, runtime_context=runtime_context, current_time=rendered_at,
        invocation_facts={"prompt_configuration": fingerprints}))
    result.pop("type")
    result["configuration_fingerprints"] = fingerprints
    result["task_source_configuration_fingerprints"] = _saved_configuration_fingerprints(source_snapshot)
    result["task_source_run_id"] = source_run.id if source_run else None
    result["task_source_rendered_at"] = source_snapshot["rendered_at"] if source_snapshot else None
    result["submission_state"] = "preview"
    result["proposal_revision"] = source_run.proposal_revision if source_run else None
    result["stage_index"] = facts.get("stage_index")
    if not result["reason"]:
        result["reason"] = "所选路线的当前配置预览，尚未运行或验证认证。" + (f" 完整任务正文与明确标记的任务 Skill override 来自 run {source_run.id} 于 {source_snapshot['rendered_at']} 保存的输入，不代表外部资料当前状态。" if source_run else " 未绑定任务。")
    return result
