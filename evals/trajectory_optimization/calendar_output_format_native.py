#!/usr/bin/env python3
"""Run the approved anonymous Calendar Consumer/Audit format comparison.

Uses native Codex OAuth, a local fake read-only MCP server, no Work Profile, and
only the synthetic fixture below. The four arms each receive a cold turn and a
same-session continuation: Consumer/Audit x wire-schema-only/native-schema.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MODEL = "gpt-5.6-luna"
EFFORT = "medium"
CONVERSATION = "synthetic-calendar-cold-resume"
SKILL_PATH = ROOT / "ci/shared-skills/ceo-calendar-invite/SKILL.md"
CALENDAR_SKILL_PATH = Path.home() / ".agents/skills/dingtalk-calendar/SKILL.md"
DINGTALK_CHAT_SKILL_PATH = Path.home() / ".agents/skills/dingtalk-chat/SKILL.md"
DINGTALK_SHARED_SKILL_PATH = Path.home() / ".agents/skills/dingtalk-shared/SKILL.md"
CURRENT_CONVERSION_GUIDANCE = (
    "使用本轮可用的时区计算能力：一次结果已给出源区当地时间、UTC instant、目标区当地时间及偏移；"
    "重复时间会列出所有 fold。复用这些结果，不要为同一 instant 再反向换算。"
    "同一原生 session 续会话时，先查看此前的转换结果；若 `local_time`、`source_timezone`、`target_timezone` 三项完全相同，复用完整结果。若本轮新来源明确给出的 UTC instant 精确匹配此前结果中的某个 choice，且目标时区相同，复用该 choice 已给出的目标当地时间并更新 fold 选择，无需重新换算。只有既不符合这两种匹配且时间或时区输入变化时才重新计算。本轮消息和日历状态仍须重新读取。"
)
PRE_SESSION_REUSE_GUIDANCE = (
    "使用本轮可用的时区计算能力：一次结果已给出源区当地时间、UTC instant、目标区当地时间及偏移；"
    "重复时间会列出所有 fold。复用这些结果，不要为同一 instant 再反向换算；"
    "只有候选时间或目标时区不同才另行计算。"
    "只有日期对应的两地时间都已核实才展示确定的双方当地日期与起止。"
)
EXACT_INPUT_REUSE_GUIDANCE = (
    "使用本轮可用的时区计算能力：一次结果已给出源区当地时间、UTC instant、目标区当地时间及偏移；"
    "重复时间会列出所有 fold。复用这些结果，不要为同一 instant 再反向换算。"
    "同一原生 session 续会话时，先查看此前的转换结果；若 `local_time`、`source_timezone`、`target_timezone` 三项完全相同，则复用完整结果，即使新消息刚确认了某个 fold，也只更新 fold 选择，不重算；三项任一变化才重新计算。本轮消息和日历状态仍须重新读取。"
)
LEGACY_CONVERSION_GUIDANCE = (
    "使用本轮可用的时区计算能力或明确来源验算：本人当地时间 → UTC → 对方当地时间，"
    "并反向换算核对同一时刻；处理跨日与不存在/重复的当地时间。"
    "只有完成日期对应的偏移核实和换算才展示确定的双方当地日期与起止。"
)
MESSAGES_INITIAL = [
    {"message_id": "fixture-001", "sender": "Mina",
     "content": "旧提议的 2026-11-01 01:30 PDT 已撤销，不要沿用。"},
    {"message_id": "fixture-002", "sender": "Mina",
     "content": "新提议是 2026-11-01 01:30（America/Los_Angeles）；组织者仍在核对，尚未确认。"},
    {"message_id": "fixture-003", "sender": "Bot",
     "content": "卡片部分结果：时间待确认；详情未完成。此状态不能证明已确认或已创建日程。"},
    {"message_id": "fixture-005", "sender": "Mina",
     "content": "我的时区是 Europe/London；请同时告诉我这个时刻对应的伦敦当地时间。"},
]
MESSAGES_UPDATED = MESSAGES_INITIAL + [
    {"message_id": "fixture-004", "sender": "Mina",
     "content": "更新：组织者已确认新提议对应 2026-11-01T09:30:00Z。尚未创建日程，也没有对方空闲信息。"},
]
EVENTS = [{"event_id": "fixture-event-1", "title": "Synthetic focus block",
           "start_time": "2026-11-01T17:00:00Z", "end_time": "2026-11-01T18:00:00Z",
           "timezone": "America/Los_Angeles"}]
TOOLS = [
    {"name": "read_skill", "description": "Read the requested installed business Skill.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"], "additionalProperties": False}},
    {"name": "read_dingtalk_messages_in_window", "description": "Read synthetic messages for the specified conversation and time window.",
     "inputSchema": {"type": "object", "properties": {"conversation_id": {"type": "string"},
        "start": {"type": "string"}, "end": {"type": "string"}},
        "required": ["conversation_id", "start", "end"], "additionalProperties": False}},
    {"name": "list_dingtalk_calendar_events", "description": "Read synthetic principal calendar events in a time window.",
     "inputSchema": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        "required": ["start", "end"], "additionalProperties": False}},
    {"name": "convert_dingtalk_calendar_time", "description": "Convert local calendar wall time using explicit IANA timezones; report ambiguous folds.",
     "inputSchema": {"type": "object", "properties": {"local_time": {"type": "string"},
        "source_timezone": {"type": "string"}, "target_timezone": {"type": "string"}},
        "required": ["local_time", "source_timezone", "target_timezone"], "additionalProperties": False}},
]


def response(name: str, args: dict, stage: str, skill_variant: str = "optimized") -> tuple[dict, bool]:
    if name == "read_skill":
        requested = args.get("path")
        requested_path = Path(requested) if requested else None
        if requested == str(Path.home() / ".agents/AGENTS.md"):
            shared = Path.home() / ".agents/AGENTS.md"
            if shared.is_file():
                content = shared.read_text(encoding="utf-8")
                return {"path": str(shared), "sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "content": content}, False
        if (requested == str(CALENDAR_SKILL_PATH)
                or (requested_path and requested_path.parts[-2:] == ("dingtalk-calendar", "SKILL.md"))) \
                and CALENDAR_SKILL_PATH.is_file():
            content = CALENDAR_SKILL_PATH.read_text(encoding="utf-8")
            return {"path": str(CALENDAR_SKILL_PATH),
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "content": content}, False
        if (requested == str(DINGTALK_CHAT_SKILL_PATH)
                or (requested_path and requested_path.parts[-2:] == ("dingtalk-chat", "SKILL.md"))) \
                and DINGTALK_CHAT_SKILL_PATH.is_file():
            content = DINGTALK_CHAT_SKILL_PATH.read_text(encoding="utf-8")
            return {"path": str(DINGTALK_CHAT_SKILL_PATH),
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "content": content}, False
        if (requested == str(DINGTALK_SHARED_SKILL_PATH)
                or (requested_path and requested_path.parts[-2:] == ("dingtalk-shared", "SKILL.md"))) \
                and DINGTALK_SHARED_SKILL_PATH.is_file():
            content = DINGTALK_SHARED_SKILL_PATH.read_text(encoding="utf-8")
            return {"path": str(DINGTALK_SHARED_SKILL_PATH),
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "content": content}, False
        if requested != str(SKILL_PATH) or not SKILL_PATH.is_file():
            return {"error": "fixture_skill_not_found"}, True
        content = SKILL_PATH.read_text(encoding="utf-8")
        if skill_variant == "baseline":
            content = content.replace(CURRENT_CONVERSION_GUIDANCE, LEGACY_CONVERSION_GUIDANCE, 1)
        elif skill_variant == "pre_session_reuse":
            content = content.replace(CURRENT_CONVERSION_GUIDANCE, PRE_SESSION_REUSE_GUIDANCE, 1)
        elif skill_variant == "exact_input_reuse":
            content = content.replace(CURRENT_CONVERSION_GUIDANCE, EXACT_INPUT_REUSE_GUIDANCE, 1)
        return {"path": str(SKILL_PATH), "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content}, False
    if name == "read_dingtalk_messages_in_window":
        if args.get("conversation_id") != CONVERSATION or not args.get("start") or not args.get("end"):
            return {"error": "fixture_window_or_conversation_mismatch"}, True
        return {"messages": MESSAGES_INITIAL if stage == "cold" else MESSAGES_UPDATED,
                "complete": True, "fixture_stage": stage}, False
    if name == "list_dingtalk_calendar_events":
        if not args.get("start") or not args.get("end"):
            return {"error": "fixture_window_required"}, True
        return {"events": EVENTS, "complete": True, "fixture_stage": stage}, False
    if name == "convert_dingtalk_calendar_time":
        try:
            from app.calendar_timezone import convert_calendar_timezone
            return convert_calendar_timezone(
                args["local_time"], args["source_timezone"], args["target_timezone"]
            ), False
        except Exception:
            return {"status": "invalid_input", "choices": []}, True
    return {"error": "fixture_tool_not_found"}, True


def serve(stage: str, trace: Path, skill_variant: str) -> None:
    for line in sys.stdin:
        req = json.loads(line)
        method, request_id = req.get("method"), req.get("id")
        if method == "notifications/initialized":
            continue
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "calendar-synthetic-readonly", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = req.get("params", {})
            payload, failed = response(
                params.get("name", ""), params.get("arguments") or {}, stage, skill_variant
            )
            with trace.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"tool": params.get("name"), "arguments": params.get("arguments") or {},
                                    "result": payload, "isError": failed}, ensure_ascii=False) + "\n")
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                      "structuredContent": payload, "isError": failed}
        else:
            continue
        if request_id is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False), flush=True)


def _child_server():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--stage", choices=("cold", "resume"))
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--skill-variant", choices=("baseline", "pre_session_reuse", "exact_input_reuse", "optimized"), default="optimized")
    args = parser.parse_args()
    serve(args.stage, args.trace, args.skill_variant)
    return 0


def task(role: str, stage: str, candidate: str = "") -> str:
    if role == "consumer":
        if stage == "cold":
            return ("合成只读评估。请读取指定会话与日历信息，并使用时区转换工具核对重复小时；向 Europe/London 时区的人展示当地时间。只返回 Consumer JSON 候选；禁止发送消息、创建或修改日历。"
                    f"\nconversation_id={CONVERSATION}; window=2026-11-01T00:00:00Z..2026-11-02T00:00:00Z; skill={SKILL_PATH}")
        return ("同一 session 续跑。新来源已到，请重新读取，不要沿用旧时间；按 Europe/London 显示当地时间并更新候选。禁止任何外部操作。"
                f"\nconversation_id={CONVERSATION}; window=2026-11-01T00:00:00Z..2026-11-02T00:00:00Z; skill={SKILL_PATH}")
    return ("合成只读审核。按需读取对应 Skill 和指定会话/日历来源，检查以下候选与最新来源是否一致。只返回 Audit JSON；不得执行外部操作。"
            f"\nconversation_id={CONVERSATION}; window=2026-11-01T00:00:00Z..2026-11-02T00:00:00Z; skill={SKILL_PATH}\n候选：{candidate}")


def run_matrix(
    *,
    roles: tuple[str, ...] = ("consumer", "audit"),
    modes: tuple[str, ...] = ("wire_only", "native_schema"),
    skill_variant: str = "optimized",
) -> dict:
    from app.audit_rules import render_audit_rules
    from app.consumer_agent import consumer_developer_instructions, audit_developer_instructions
    from app.codex_runner import CodexRunner
    from app.store import AgentRole
    from app.wechat.codex_safety import ControlledCliConfig, make_role_agent_command
    from app import wechat
    from app.agent_contracts import ConsumerAgentResult, ConsumerProposal, ProposedAction, ProposalFact
    from app.agent_wire_contracts import parse_consumer_agent_wire_result, parse_audit_agent_wire_result
    from app.agent_wire_contracts import _ConsumerWire, _AuditWire
    from app.prompt_composition import PromptConfiguration
    from app.developer_prompt import render_developer_prompt_template

    dev_template = (ROOT / "app/defaults/developer_prompt.md").read_text(encoding="utf-8")
    user_template = (ROOT / "app/defaults/user_prompt.md").read_text(encoding="utf-8")
    config = PromptConfiguration(dev_template, render_developer_prompt_template(dev_template), user_template, "", "")
    dev = {"consumer": consumer_developer_instructions(
                runtime_context="仅提供本次匿名评估的四个只读 fixture 工具；无生产记录、Work Profile 或业务 MCP。",
                work_profile="", prompt_configuration=config),
           "audit": audit_developer_instructions(
                render_audit_rules(AgentRole.AUDIT),
                runtime_context="仅提供本次匿名评估的四个只读 fixture 工具；无生产记录、Work Profile 或业务 MCP。",
                work_profile="", prompt_configuration=config)}
    # Fixed canonical candidate keeps both Audit formats on identical input.
    candidate = ConsumerAgentResult(
        outcome="proposal", summary="旧时刻已撤销；新提议尚未确认，暂不创建日历。",
        proposal=ConsumerProposal(
            objective="请确认新的日历时刻后再安排。",
            actions=[ProposedAction(description="向原会话发送待确认问题。", action_identity="fixture-ask-time-v1",
                capability="dingtalk-chat", operation="reply_to_message",
                target={"conversation_id": CONVERSATION, "message_id": "fixture-002"},
                payload={"content": "我看到旧时刻已撤销。新提议的 01:30 仍待确认；请确认精确 UTC 时刻和参与者是否可行。"}, effect="external")],
            sourced_facts=[ProposalFact(assertion="旧 01:30 PDT 提议已撤销", references=["fixture-001"]),
                           ProposalFact(assertion="新提议尚未确认", references=["fixture-002", "fixture-003"])],
            authored_judgment="目前不能把部分卡片状态视为确认，也不能推断对方空闲。"),
        error={"code": "", "retryable": False, "authorization_required": False},
        risk="medium", confidence=0.9, rule_coverage=0.8, information_completeness=0.7,
    ).model_dump(mode="json")
    candidate_json = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(candidate_json.encode()).hexdigest()
    schema_paths = {"consumer": ROOT / "app/schemas/consumer_agent_result.schema.json",
                    "audit": ROOT / "app/schemas/audit_agent_result.schema.json"}
    # The CLI ignores user config in this evaluation. Mirror that boundary in
    # the role builder so it does not append transport-less disabled MCP stubs
    # for unrelated servers from the developer's normal Codex config.
    wechat.codex_safety.inherited_native_server_names = lambda: ()
    results = []
    with tempfile.TemporaryDirectory(prefix="ceo-calendar-format-matrix-") as tmp:
        tmp_path = Path(tmp)
        for role in roles:
            for mode in modes:
                session_id = None
                arm = {"role": role, "mode": mode, "candidate_sha256": digest if role == "audit" else None, "turns": []}
                for stage in ("cold", "resume"):
                    trace = tmp_path / f"{role}-{mode}-{stage}.jsonl"
                    workspace = tmp_path / f"workspace-{role}-{mode}"
                    workspace.mkdir(exist_ok=True)
                    command = CodexRunner(workspace=workspace).build_command(
                        prompt="", session_id=session_id,
                        output_schema_path=schema_paths[role],
                        use_output_schema=(mode == "native_schema"), approval_policy="never",
                        developer_instructions=dev[role], use_approval_bypass=False,
                        ignore_user_config=True, model=MODEL, reasoning_effort=EFFORT,
                        sandbox_mode="read-only", skip_git_repo_check=(role == "audit"),
                    )
                    if mode == "native_schema":
                        model = _ConsumerWire if role == "consumer" else _AuditWire
                        schema = model.model_json_schema()
                        # The endpoint rejects top-level oneOf. Branch semantics
                        # remain enforced by the unchanged Pydantic parser.
                        schema.pop("oneOf", None)
                        schema["required"] = list(schema.get("properties", {}))
                        schema_path = tmp_path / f"{role}-provider-compatible-schema.json"
                        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
                        command[command.index("--output-schema") + 1] = str(schema_path)
                    controlled = ControlledCliConfig(sys.executable,
                        (str(Path(__file__).resolve()), "--server", "--stage", "cold" if stage == "cold" else "resume",
                         "--trace", str(trace), "--skill-variant", skill_variant), str(ROOT))
                    if role == "consumer":
                        make_role_agent_command(command, role="consumer", task_workspace=str(workspace), controlled_cli=controlled)
                    else:
                        make_role_agent_command(command, role="audit", controlled_cli=controlled)
                    # Limit Consumer's local tools as this is a read-only comparison.
                    for feature in ("shell_tool", "unified_exec"):
                        command[-1:-1] = ["-c", f"features.{feature}=false"]
                    prompt = task(role, stage, candidate_json if role == "audit" else "")
                    completed = subprocess.run(command, input=prompt, capture_output=True, text=True,
                                               timeout=240, check=False, cwd=ROOT)
                    parsed = []
                    errors = []
                    for line in completed.stdout.splitlines():
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "thread.started":
                            session_id = event.get("thread_id")
                        if event.get("type") == "item.completed":
                            item = event.get("item") or {}
                            if item.get("type") == "agent_message":
                                parsed.append(item.get("text", ""))
                            elif item.get("type") not in ("reasoning", "error"):
                                pass
                        if event.get("type") in ("error", "turn.failed"):
                            errors.append(str(event.get("message") or event.get("error") or "")[:300])
                    final = parsed[-1] if parsed else ""
                    stderr_excerpt = completed.stderr[:700].replace("\n", " ")
                    try:
                        final_event = json.dumps({"type": "item.completed", "item": {
                            "type": "agent_message", "text": final}}, ensure_ascii=False)
                        if role == "consumer":
                            normalized = parse_consumer_agent_wire_result(final_event).model_dump(mode="json")
                        else:
                            normalized = parse_audit_agent_wire_result(final_event, proposal_revision=1, candidate_digest=digest).model_dump(mode="json")
                        valid = True
                    except Exception as exc:
                        normalized, valid = None, False
                        errors.append(type(exc).__name__ + ":" + str(exc)[:300])
                    calls = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
                    for call in calls:
                        if call.get("tool") == "read_skill" and isinstance(call.get("result"), dict):
                            content = call["result"].pop("content", "")
                            call["result"]["content_chars"] = len(content)
                    arm["turns"].append({"stage": stage, "exit_code": completed.returncode,
                        "session_id": session_id, "valid_wire": valid, "result": normalized,
                        "tool_calls": calls, "errors": errors,
                        "stderr_excerpt": stderr_excerpt,
                        "submitted_prompt_chars": len(prompt)})
                    print(f"{role}/{mode}/{stage}: exit={completed.returncode} valid={valid} "
                          f"calls={len(calls)} session={'yes' if session_id else 'no'}", flush=True)
                    if completed.returncode != 0 or not valid:
                        break
                results.append(arm)
    return {"model": MODEL, "reasoning_effort": EFFORT, "fixture": "synthetic-only",
            "work_profile": "omitted", "live_business_mcp": False,
            "calendar_skill_variant": skill_variant, "arms": results}


if __name__ == "__main__" and "--server" in sys.argv:
    raise SystemExit(_child_server())
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("consumer", "audit"), action="append")
    parser.add_argument("--mode", choices=("wire_only", "native_schema"), action="append")
    parser.add_argument("--skill-variant", choices=("baseline", "pre_session_reuse", "exact_input_reuse", "optimized"), default="optimized")
    args = parser.parse_args()
    data = run_matrix(roles=tuple(args.role or ("consumer", "audit")),
                      modes=tuple(args.mode or ("wire_only", "native_schema")),
                      skill_variant=args.skill_variant)
    print(json.dumps(data, ensure_ascii=False, indent=2))
