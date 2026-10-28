"""Read-only Settings projection of service inputs; never executes a turn."""

from datetime import datetime
from pathlib import Path
import sys

from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import RuntimeKind
from app.audit_rules import render_audit_rules
from app.config import workspace_path
from app.consumer_purpose import code_mode_handoff_for_task
from app.consumer_agent import (consumer_developer_sections_by_runtime, audit_developer_sections,
    default_task_skill_catalog)
from app.business_skills import frozen_task_skill_binding, frozen_task_skill_names, render_task_skill_discovery
from app.prompt_composition import (PromptSection, append_runtime_context, load_prompt_configuration,
    prompt_section_facts, render_prompt_sections)
from app.runtime_prompt_context import render_runtime_context, runtime_prompt_snapshot
from app.store import AgentRole, AutoReplyStore
from app.task_artifacts import task_artifact_directory


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
            "task_source_run_id": None, "task_source_rendered_at": None, "sections": []}


def _saved_input_snapshots(store, run, *, attempts=None):
    """Live prepared inputs and native invocation records have distinct lifetimes."""
    from app.native_prompt_input import read_attempt_input
    from app.leak_check import redact_credentials

    from app.native_trajectory import live_events
    events = live_events(str(store.path.resolve()), run.id) or [] if run.status == "running" else []
    snapshots = [event for event in events if event.get("type") == "runtime.prompt"]
    known = {event.get("runtime_attempt_id") for event in snapshots}
    for attempt in attempts if attempts is not None else store.list_agent_runtime_attempts(run.id):
        if attempt.id in known:
            continue
        saved = read_attempt_input(attempt)
        if saved is None:
            continue
        texts = {key: redact_credentials(saved[key]) for key in ("developer_instructions", "task_prompt", "submitted_input")}
        sections = [{"name": "Developer 指令", "source": saved["developer_source"], "placement": "developer", "characters": len(texts["developer_instructions"])},
                    {"name": "本轮 Task", "source": "选定 attempt 的原生输入", "placement": "task", "characters": len(texts["task_prompt"])}]
        snapshots.append({"type": "runtime.prompt", "runtime_attempt_id": attempt.id,
            "role": run.role.value, "task_id": run.reply_task_id, "runtime_kind": attempt.runtime_kind,
            "route_name": attempt.route_name, "model": attempt.model, "rendered_at": "",
            "execution_generation": run.execution_generation, "proposal_revision": run.proposal_revision,
            "runtime_context": "", "invocation_facts": {}, "sections": sections,
            "redacted": any(texts[key] != saved[key] for key in texts), "native_recorded": True,
            "source_note": saved["developer_source"] + "；仅显示原生保存的服务输入，未重建细分配置来源或渲染时间。", **texts})
    return sorted(snapshots, key=lambda item: item.get("runtime_attempt_id") or 0)


def historical_prompt_preview(store: AutoReplyStore, *, run_id: int, runtime_attempt_id: int | None = None) -> dict[str, object]:
    run = store.get_agent_run(run_id, load_events=False)
    if run is None:
        raise LookupError("运行不存在")
    result = _empty_item(mode="historical", role=run.role.value)
    result.update(run_id=run.id, task_id=run.reply_task_id)
    attempts = store.list_agent_runtime_attempts(run.id)
    snapshots = _saved_input_snapshots(store, run, attempts=attempts)
    by_id = {item.get("runtime_attempt_id"): item for item in snapshots}
    references = {item.id: item for item in attempts}
    ids = sorted(set(references) | set(by_id), key=lambda value: value or 0)
    if runtime_attempt_id is not None and runtime_attempt_id not in ids:
        raise LookupError("所选运行尝试不存在")
    from app.native_trajectory import live_events
    events = live_events(str(store.path.resolve()), run.id) or []
    invoked_ids = {event.get("runtime_attempt_id") for event in events if event.get("type") == "runtime.prompt.invoked"}
    result["attempts"] = [{"runtime_attempt_id": identity,
        "route_name": by_id[identity]["route_name"] if identity in by_id else references[identity].route_name,
        "rendered_at": by_id[identity]["rendered_at"] if identity in by_id else "",
        "input_status": "available" if identity in by_id else "unavailable",
        "submission_state": ("invoked" if by_id[identity].get("native_recorded") or identity in invoked_ids else "prepared") if identity in by_id else None}
        for identity in ids]
    selected_id = runtime_attempt_id if runtime_attempt_id is not None else (ids[-1] if ids else None)
    saved = by_id.get(selected_id)
    if saved is None:
        reference = references.get(selected_id)
        if reference:
            result.update(runtime_attempt_id=reference.id, route_name=reference.route_name,
                runtime_kind=reference.runtime_kind, model=reference.model,
                execution_generation=run.execution_generation, proposal_revision=run.proposal_revision)
        result.update(status="unavailable", submission_state=None, reason="所选运行尝试的原生输入记录不可读取；不能用当前配置重建或重跑补齐，也不代表从未提交。")
        return result
    for key in ("role", "runtime_kind", "route_name", "model", "rendered_at",
                "developer_instructions", "task_prompt", "submitted_input", "runtime_context"):
        result[key] = saved[key]
    for key in ("runtime_attempt_id", "execution_generation", "proposal_revision"):
        result[key] = saved.get(key)
    result["sections"] = saved.get("sections", [])
    result["stage_index"] = saved.get("invocation_facts", {}).get("stage_index")
    result["configuration_fingerprints"] = _saved_configuration_fingerprints(saved)
    result["task_source_configuration_fingerprints"] = _saved_configuration_fingerprints(saved)
    result["task_source_run_id"] = run.id
    result["task_source_rendered_at"] = saved["rendered_at"]
    invoked = saved.get("native_recorded", False) or saved.get("runtime_attempt_id") in invoked_ids
    result["submission_state"] = "invoked" if invoked else "prepared"
    result["reason"] = ("运行适配器已调用；此输入记录不证明模型接收、任务完成或外部动作发生。" if invoked else "仅保存准备输入，尚无运行适配器提交记录；不能称为模型已经收到。") + (" 凭据已脱敏，标记 [REDACTED]。" if saved.get("redacted") else "")
    if saved.get("native_recorded"):
        result["scope"] = "选定原生 attempt 保存的 Task 与服务 Developer；续会话的 session Developer 来源单独标明，不冒充本轮配置提交回执。未包含 CLI 基础系统指令、AGENTS、环境消息、工具 schema 或会话历史。"
        result["reason"] = saved["source_note"] + (" 凭据已脱敏，标记 [REDACTED]。" if saved.get("redacted") else "")
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
        role_runs = [run for run in store.list_agent_runs_for_task_generation(task.id, task.execution_generation, load_events=False) if run.role.value == role]
        if role_runs:
            source_run = role_runs[-1]
            attempts = store.list_agent_runtime_attempts(source_run.id)
            snapshots = _saved_input_snapshots(store, source_run, attempts=attempts[-1:])
            latest_id = attempts[-1].id if attempts else (snapshots[-1].get("runtime_attempt_id") if snapshots else None)
            source_snapshot = next((snapshot for snapshot in snapshots if snapshot.get("runtime_attempt_id") == latest_id), None)
    facts = source_snapshot.get("invocation_facts", {}) if source_snapshot else {}
    audit_purpose_name = (
        facts.get("audit_purpose")
        if facts.get("audit_purpose") in {"general", "oa"}
        else "oa" if task and (
            task.oa_url or "dingtalk-oa-approval" in (facts.get("skill_names") or ())
        ) else "general"
    )
    code_mode_handoff = code_mode_handoff_for_task(task)
    configuration = load_prompt_configuration(create_missing=False, role=role)
    fingerprints = configuration.fingerprints()
    profile = configuration.work_profile
    if role == "consumer":
        developer_sections = consumer_developer_sections_by_runtime(
            runtime_context="", work_profile=profile, prompt_configuration=configuration,
            code_mode_handoff=code_mode_handoff,
        )[route.runtime_kind.value]
    else:
        rules = render_audit_rules(AgentRole.AUDIT, create_missing=False)
        developer_sections = audit_developer_sections(rules, runtime_context="", work_profile=profile, prompt_configuration=configuration, purpose_name=audit_purpose_name, code_mode_handoff=code_mode_handoff)
    developer = render_prompt_sections(developer_sections, placement="developer", omit_empty=role == "consumer")
    prompt = source_snapshot["task_prompt"] if source_snapshot else "未绑定任务和候选：仅预览当前角色公共指令与环境。"
    if task and source_snapshot is None:
        result.update(status="unavailable", reason="该任务最新角色运行/尝试的完整输入不可读取；不使用较早输入或原触发拼装结果冒充完整 prompt，也不重跑补齐。")
        prompt = ""
    task_sections = ()
    if source_snapshot and not source_snapshot.get("sections"):
        # A saved legacy Task has no section receipts. Preserve its whole body;
        # current task guidance is a separately identified prefix, not a rewrite
        # or a reconstruction of what the historic Agent received.
        selected_names = facts.get("skill_names", ())
        frozen_names = frozen_task_skill_names(task.trigger_message_json, selected_names)
        guidance = (facts.get("skill_protocol", "") if facts.get("skill_protocol_source") == "task_override"
                    else render_task_skill_discovery(selected_names, catalog=default_task_skill_catalog(tuple(selected_names)), frozen_names=frozen_names,
                        frozen_materials=frozen_task_skill_binding(task.trigger_message_json).materials))
        guidance_source = ("已保存任务 Skill override" if facts.get("skill_protocol_source") == "task_override"
                           else "任务冻结与当前 Skill 元数据" if frozen_names and set(frozen_names) != set(selected_names)
                           else "任务冻结 Skill 元数据" if frozen_names else "当前 Skill 目录")
        task_sections = (PromptSection("任务 Skill 入口", guidance_source, "task", guidance),
                         PromptSection("已保存任务正文", "历史输入快照", "task", prompt))
        prompt = render_prompt_sections(task_sections, placement="task")
    workspace = workspace_path()
    cwd = (
        task_artifact_directory(task.id, task.execution_generation, create=False)
        if task and role == "consumer" else workspace
    )
    if route.runtime_kind is RuntimeKind.CODEX_CLI:
        command = CodexRuntimeAdapter(workspace=workspace, config=config).build_command(
            route=route, prompt=prompt, session_id=None, image_paths=None, output_schema_path=None,
            use_output_schema=False, approval_policy="on-failure", developer_instructions=developer, use_approval_bypass=False)
        make_role_agent_command(command, role=role, purpose_name=audit_purpose_name if role == "audit" else "general", code_mode_handoff=code_mode_handoff, task_workspace=str(cwd) if role == "consumer" else None,
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
        invocation_facts={"prompt_configuration": fingerprints,
                          "prompt_sections": prompt_section_facts(developer_sections, task_sections)}))
    result.pop("type")
    if source_snapshot and source_snapshot.get("sections"):
        result["sections"] = [section for section in result["sections"] if section["placement"] == "developer"] + [
            section for section in source_snapshot["sections"] if section["placement"] == "task"]
    result["configuration_fingerprints"] = fingerprints
    result["task_source_configuration_fingerprints"] = _saved_configuration_fingerprints(source_snapshot)
    result["task_source_run_id"] = source_run.id if source_run else None
    result["task_source_rendered_at"] = source_snapshot["rendered_at"] if source_snapshot else None
    result["submission_state"] = "preview"
    result["proposal_revision"] = source_run.proposal_revision if source_run else None
    result["stage_index"] = facts.get("stage_index")
    if not result["reason"]:
        result["reason"] = "所选路线的当前配置预览，尚未运行或验证认证。" + (f" 完整任务正文与明确标记的任务 Skill override 来自 run {source_run.id} 于 {source_snapshot['rendered_at'] or '渲染时间未记录'} 保存的输入，不代表外部资料当前状态。" if source_run else " 未绑定任务。")
    return result
