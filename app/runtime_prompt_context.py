"""Describe the selected role invocation, using its declared native tool catalog."""

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from app.agent_runtime_contracts import RuntimeKind, RuntimeRoute
from app.config import principal_display_name, workspace_path
from app.leak_check import redact_credentials, redact_credentials_in_value


RUNTIME_WORK_PRINCIPLES = """## 原请求与取证
先识别原请求要向谁交付什么，再判断缺哪些事实，使用本轮实际声明的入口按需取证。
能自行查到的资料先读取，不把尚未尝试的系统取证推给 principal。原触发是权威请求，近期上下文与反馈补充事实，不替换原业务目标。
其他参与者待确认时，先交付能够核实的部分，明确剩余协调责任；只追问真正阻断交付且无法自行取得的事实。
入口已声明不等于已认证、读取成功或业务动作完成。保留具体来源、范围、时间与错误；技术、认证或工具失败如实 failed，不伪装成人工业务选择。
Consumer 可做本轮普通文档、文件、研究、报告与计算工作，以真实工具结果和读回证明结果；普通写入不因此自动成为受控动作。
只有当前系统任务已注册且要求 Audit 的动作需要完整 proposal → Audit 只读审核 → SystemExecutor → 真实回执。此说明不新增审核项、授权或审批政策，不通过别的工具/渠道绕过历史风险拒绝。
Audit 按原请求独立复核动态事实；有入口和稳定目标时自行读取，不要求 Consumer 复制工具输出。修订保留业务目标；依赖失败不能通过内容修订伪装解决。
自然语言 summary/trace 或整个 result 的关键词不能证明执行；完整候选、来源与实际回执才是依据。

## 日历任务与参与者时区
日历相关任务必须考虑本人、对方及必要协调者的时区。先从原请求、有效日程及实际可用来源核实，不用机器时区、公司所在地、姓名或消息时间戳代替对方时区。
按每个候选的具体日期分别核实双方命名时区的 UTC 偏移，不能沿用今天的偏移或假定两地同日切换夏令时。使用本轮可用的时区计算能力或明确来源验算：本人当地时间 → UTC → 对方当地时间，并反向换算核对同一时刻；处理跨日与不存在/重复的当地时间。只有完成日期对应的偏移核实和换算才展示确定的双方当地日期与起止。无法核实换算时仍给本人侧已确认时区的候选，明确对方当地换算待核实，不凭记忆输出确定对方时间。
考虑已知工作时段、占用与明确偏好，避免只因本人空闲推荐对方深夜；不编造固定办公时间。已知时区不证明对方空闲。
对方时区仍未知时标明待确认，先给本人侧明确时区的暂定候选；只有确实阻断必要判断才问合适的请求方/协调者。不把暂定窗口称为双方均合适或已安排会议。
读取范围内未见冲突不保证可出席；单纯时间流逝不证明原请求已取消、完成或过期。
缺值如实写未提供、未核实、本轮未声明或具体读取失败。快照只描述当时声明能力，不证明实时外部状态；不对外暴露凭据、私密日历正文或内部配置。
""".strip()


def _command_configs(command: list[str]) -> dict[str, str]:
    return {
        command[index + 1].partition("=")[0]: command[index + 1].partition("=")[2]
        for index, value in enumerate(command[:-1]) if value == "-c"
    }


def explicit_participant_timezones(raw_payload) -> list[dict[str, str]]:
    """Project only scalar timezone evidence, never unrelated source fields."""
    zones = raw_payload.get("participant_timezones", []) if isinstance(raw_payload, Mapping) else []
    if not isinstance(zones, list):
        return []
    fields = ("participant_id", "timezone", "source_ref", "applies_on")
    return [{key: zone[key] for key in fields if isinstance(zone.get(key), str)}
            for zone in zones if isinstance(zone, Mapping)
            and isinstance(zone.get("timezone"), str) and zone["timezone"].strip()]


def _redacted_facts(value):
    facts = dict(value)
    if "participant_timezones" in facts:
        facts["participant_timezones"] = explicit_participant_timezones(facts)
    return redact_credentials_in_value(facts)


def declared_role_tools(role: str, route: RuntimeRoute, command: list[str]) -> dict[str, list[str]]:
    from app.wechat.codex_safety import AGENT_CLI_CONSUMER_TOOLS, AGENT_CLI_READ_TOOLS

    if route.runtime_kind is RuntimeKind.CLAUDE_CLI:
        return {"agent_cli": list(AGENT_CLI_CONSUMER_TOOLS if role == "consumer" else AGENT_CLI_READ_TOOLS)}
    configs = _command_configs(command)
    tools = {}
    for key, value in configs.items():
        if key.startswith("mcp_servers.") and key.endswith(".enabled_tools"):
            server = key.removeprefix("mcp_servers.").removesuffix(".enabled_tools")
            if configs.get(f"mcp_servers.{server}.enabled") != "false":
                tools[server] = json.loads(value)
    return tools


def declared_thinking_effort(command: list[str]) -> str:
    configs = _command_configs(command)
    if "model_reasoning_effort" in configs:
        return json.loads(configs["model_reasoning_effort"])
    if "--effort" in command:
        return command[command.index("--effort") + 1]
    return "未提供"


def render_runtime_context(
    *, role: str, route: RuntimeRoute, command: list[str], task=None,
    current_time: str, working_directory: Path | None = None,
    invocation_facts: dict[str, object] | None = None,
    preview: bool = False,
) -> str:
    from app.agent_cli import build_role_server
    from app.business_skills import bundled_business_skills_root

    descriptors = {tool.name: tool for tool in build_role_server(role)._tool_manager.list_tools()}
    tools = declared_role_tools(role, route, command)
    facts = _redacted_facts(invocation_facts or {})
    cwd = str(working_directory or Path.cwd())
    explicit_cwd = False
    for index, arg in enumerate(command[:-1]):
        if arg in ("--cd", "-C"):
            cwd = command[index + 1]
            explicit_cwd = True
    if route.runtime_kind is RuntimeKind.CODEX_CLI and not explicit_cwd and "resume" in command[:5]:
        cwd = "原生续会话目录未核实（启动目录：" + str(Path.cwd()) + "）"
    binding = {}
    if task is not None:
        raw = json.loads(task.trigger_message_json).get("raw_payload", {})
        if isinstance(raw, Mapping):
            source = raw.get("scheduled_consumer", raw)
            if isinstance(source, Mapping):
                binding = {key: source[key] for key in ("scheduled_task_id", "scheduled_task_run_id") if key in source}
    instant = datetime.fromisoformat(current_time)
    lines = [
        "## 运行环境与能力说明（Runtime Context）",
        f"- 后台角色：{role}；principal 展示名：{principal_display_name()}",
        f"- {'所选 runtime 的配置预览' if preview else '实际 runtime'}：{route.runtime_kind.value}；route：{route.name}；model：{route.model}",
        f"- 本轮 thinking：{declared_thinking_effort(command)}",
        f"- 快照时间：{current_time}；UTC 偏移：{instant.strftime('%z')}；命名时区：未独立核实",
        f"- 资料根目录：{workspace_path()}；本轮命令工作目录：{cwd}",
        f"- Skill 根目录：{bundled_business_skills_root()}",
        f"- 业务任务：{task.id if task else '未绑定'}；执行代：{task.execution_generation if task else '未绑定'}",
        f"- 来源频道：{task.channel if task else '未绑定'}；阶段：{facts.get('stage_index', '未提供')}；proposal revision：{facts.get('proposal_revision', '未提供')}",
        f"- 原业务对象：{task.business_object_key if task else '未绑定'}；原触发：{task.trigger_message_id if task else '未绑定'}",
        f"- 后台来源绑定：{json.dumps(binding, ensure_ascii=False) if binding else '未提供'}",
        "- 参与者时区：以原请求和已读取来源为准；本人、对方时区没有证据则待确认，按会议日期核对夏令时。源时间戳解释不证明参与者所在地。",
        "- 认证状态：本轮未验证；以下为本轮声明入口，不是读取结果或动作回执。",
    ]
    zones = facts.get("participant_timezones", [])
    if zones:
        lines.append("- 来源显式提供的参与者时区/适用日期（由 Agent 按原来源核实）：" + json.dumps(zones, ensure_ascii=False))
    if role == "audit":
        lines.append("- 普通工作：只读审核；不执行命令、不写工件、不调度受控动作。")
    elif route.runtime_kind is RuntimeKind.CLAUDE_CLI:
        lines.append("- 普通工作：Read/Glob/Grep 与角色 MCP 工件/绑定报告工具；shell 执行：本轮未声明。")
    else:
        configs = _command_configs(command)
        shell = configs.get("features.shell_tool") == "true"
        network = "命令网络关闭" if configs.get("sandbox_workspace_write.network_access") == "false" else "命令网络设置：未提供"
        lines.append("- 普通工作：任务工件读写、绑定报告；" + (f"本轮原生 shell/补丁与计算，{network}。" if shell else "原生 shell 执行：本轮未声明。"))
    lines.append("- 来源与普通工作入口（参数以本轮工具 schema 为准）：")
    for server, names in sorted(tools.items()):
        for name in names:
            description = ""
            if server == "agent_cli" and name in descriptors:
                description = descriptors[name].description.strip().splitlines()[0]
            lines.append(f"  - {server}.{name}" + (f"：{description}" if description else ""))
    if not any(tools.values()):
        lines.append("  - 本轮未声明来源读取入口；不能由个人安装或历史会话推断。")
    if role == "consumer":
        lines.append("- consumer_document_write 仅适用于已有绑定的 scheduled 日报/周报，声明工具不代表本任务有绑定。")
    lines.append("- 受控业务动作的精确范围以已注入 System Action Contracts 与当前系统任务为准；Consumer 提案、Audit 只读、System 执行。")
    return "\n".join(lines)


def runtime_prompt_snapshot(
    *, role: str, route: RuntimeRoute, runtime_attempt_id: int, task,
    developer_instructions: str, task_prompt: str, runtime_context: str, current_time: str,
    invocation_facts: dict[str, object] | None = None,
) -> dict[str, object]:
    from app.claude_runtime_adapter import claude_input_contract

    submitted = (claude_input_contract(prompt=task_prompt, developer_instructions=developer_instructions)
                 if route.runtime_kind is RuntimeKind.CLAUDE_CLI else task_prompt)
    texts = {"developer_instructions": developer_instructions, "task_prompt": task_prompt,
             "submitted_input": submitted, "runtime_context": runtime_context}
    redacted = {key: redact_credentials(value) for key, value in texts.items()}
    facts = invocation_facts or {}
    saved_facts = _redacted_facts(facts)
    return {
        "type": "runtime.prompt", "runtime_attempt_id": runtime_attempt_id,
        "role": role, "runtime_kind": route.runtime_kind.value, "route_name": route.name,
        "model": route.model, "rendered_at": current_time,
        "task_id": task.id if task else None,
        "execution_generation": task.execution_generation if task else None,
        "submission_state": "prepared", "invocation_facts": saved_facts,
        "redacted": texts != redacted or facts != saved_facts, **redacted,
    }
