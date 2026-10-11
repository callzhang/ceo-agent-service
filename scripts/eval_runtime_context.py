#!/usr/bin/env python3
"""Compare frozen baseline and candidate role prompts with synthetic read MCP.

No production server, database, provider, outbound message, or installed Skill
is called. This script deliberately does not assign a semantic pass score: the
blinded packet requires independent review of exact final results and calls.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals/runtime_context/cases.v1.json"
SERVER = ROOT / "evals/runtime_context/fixture_mcp.py"
PROFILE = ROOT / "evals/runtime_context/profile.md"
ARTIFACTS = ROOT / "evals/runtime_context/artifacts"
TOOLS = ("read_skill", "list_dingtalk_calendar_events", "read_dingtalk_messages")
PROMPT_SOURCE = r'''
import json, sys
from pathlib import Path
import app.consumer_agent as consumer_agent
from app.agent_wire_contracts import ConsumerAgentWireResult
version = sys.argv[1]
synthetic_home = Path(sys.argv[2])
consumer_agent.SHARED_RULES_PATH = synthetic_home / ".agents" / "AGENT.md"
consumer_agent.default_skill_catalog = lambda: ()
runtime_context = sys.stdin.read()
instructions = (consumer_agent.consumer_developer_instructions(runtime_context=runtime_context)
                if version == "candidate" else consumer_agent.consumer_developer_instructions())
print(json.dumps({"instructions": instructions,
                  "wire_schema": ConsumerAgentWireResult.model_json_schema()}, ensure_ascii=False))
'''


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def resolve_case(manifest: dict, case_id: str) -> dict:
    cases = {case["id"]: case for case in manifest["cases"]}
    case = dict(cases[case_id])
    if "inherits" in case:
        case = {**cases[case.pop("inherits")], **case}
    return case


def check_manifest(manifest: dict) -> None:
    if manifest["version"] != "runtime-context.v1":
        raise ValueError("unexpected corpus version")
    if manifest["baseline_ref"] != "0636969cafe866017af91ea4a3d7781d26f8d0c8":
        raise ValueError("baseline ref drift")
    settings = manifest["settings"]
    if (settings["model"], settings["reasoning_effort"], settings["max_concurrency"]) != (
        "gpt-5.6-luna", "medium", 2,
    ):
        raise ValueError("model settings drift")
    ids = [case["id"] for case in manifest["cases"]]
    if len(ids) != len(set(ids)) or any(case.get("repeats", 0) < 1 for case in manifest["cases"]):
        raise ValueError("case list is not frozen and unique")
    if any(name not in TOOLS for case in manifest["cases"] for name in case.get("absent_tools", [])):
        raise ValueError("unknown absent tool")
    for case_id in ids:
        resolved = resolve_case(manifest, case_id)
        if not {"trigger", "conversation_id", "messages", "events"} <= resolved.keys():
            raise ValueError(f"case is incomplete: {case_id}")


def runtime_context(manifest: dict, case: dict) -> str:
    tools = [name for name in TOOLS if name not in case.get("absent_tools", [])]
    descriptions = {
        "read_skill": "Read an installed Agent skill or its referenced Markdown safely.",
        "list_dingtalk_calendar_events": "Read the principal's events over an explicit time window.",
        "read_dingtalk_messages": "Read recent messages of an identified DingTalk conversation.",
    }
    lines = [
        "## 运行环境与能力说明（Runtime Context）",
        "- 后台角色：consumer；principal 展示名：负责人",
        f"- 实际 runtime：codex_cli；route：synthetic_readonly；model：{manifest['settings']['model']}",
        f"- 快照时间：{manifest['settings']['fixed_time']}；命名时区：未独立核实",
        "- 当前任务：原群消息 synthetic；原触发和本次请求由 task prompt 提供。",
        "- 来源与普通工作入口（参数以本轮工具 schema 为准）：",
    ]
    lines += [f"  - agent_cli.{name}：{descriptions[name]}" for name in tools]
    lines += [
        "- 以上为本轮声明的只读入口；认证状态未验证，调用结果才是事实。",
        "- 本人日历入口仅覆盖 principal；没有对方日历读取入口。参与者时区需从原请求或实际已读取来源核实。",
        "- 本评测不提供外部写入工具；受控回复只能提交完整候选，不能宣称已发送。",
    ]
    return "\n".join(lines)


def extract_instructions(root: Path, *, version: str, context: str, home: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(root),
           "USER_ALIAS": "负责人", "CEO_WORK_PROFILE_PATH": str(PROFILE),
           "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH": str(root / "app/defaults/developer_prompt.md"),
           "CEO_AUDIT_RULES_TEMPLATE_PATH": str(root / "app/defaults/audit_rules.md"),
           "CEO_WORKSPACE": str(home / "synthetic-workspace"),
           "CEO_SKILLS_ROOT": str(home / ".agents" / "skills")}
    result = subprocess.run([sys.executable, "-c", PROMPT_SOURCE, version, str(home)], cwd=root,
                            env=env, input=context, text=True, capture_output=True,
                            timeout=40, check=True)
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if not payload["instructions"] or not payload["wire_schema"]:
        raise ValueError("empty role instructions")
    return payload


def archive_baseline(ref: str, destination: Path) -> None:
    result = subprocess.run(["git", "archive", "--format=tar", ref], cwd=ROOT,
                            capture_output=True, timeout=120, check=True)
    with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:") as archive:
        archive.extractall(destination, filter="data")


def native_command(manifest: dict, case: dict, instructions: str, trace: Path, cwd: Path) -> list[str]:
    tool_names = [name for name in TOOLS if name not in case.get("absent_tools", [])]
    settings = manifest["settings"]
    command = ["codex", "exec", "--json", "--ignore-user-config", "--ignore-rules",
               "--disable", "hooks", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", "read-only", "--cd", str(cwd), "--model", settings["model"]]
    configs = {
        "model_reasoning_effort": settings["reasoning_effort"],
        "developer_instructions": instructions,
        "approval_policy": "never",
        "web_search": "disabled",
        "features.shell_tool": False,
        "features.unified_exec": False,
        "features.apps": False,
        "features.plugins": False,
        "features.hooks": False,
        "features.multi_agent": False,
        "features.memories": False,
        "features.code_mode_host": True,
        "features.code_mode_only": True,
        "features.code_mode.excluded_tool_namespaces": ["functions"],
        "features.code_mode.direct_only_tool_namespaces": [],
        "mcp_servers.agent_cli.command": sys.executable,
        "mcp_servers.agent_cli.args": [str(SERVER), str(MANIFEST), case["id"], str(trace)],
        "mcp_servers.agent_cli.enabled_tools": tool_names,
        "mcp_servers.agent_cli.default_tools_approval_mode": "approve",
    }
    for key, value in configs.items():
        command.extend(("-c", f"{key}={json.dumps(value, ensure_ascii=False)}"))
    return [*command, "-"]


def task_prompt(manifest: dict, case: dict) -> str:
    source = {
        "now": manifest["settings"]["fixed_time"],
        "conversation_id": case["conversation_id"],
        "message_id": case["trigger_message_id"],
        "conversation_title": "模拟项目群",
        "single_chat": False,
        "original_request": case["trigger"],
        "prior_error": case.get("prior_error"),
    }
    return (
        "This is a synthetic background Consumer turn. Work on the original request "
        "with the tools actually declared in this session. No external write tool is "
        "available. Return exactly one Consumer wire JSON object; a proposed external "
        "reply is only a plan, never a sent message. Do not ask the evaluator for data.\n\n"
        + json.dumps(source, ensure_ascii=False, sort_keys=True)
    )


def parse_native(stdout: str) -> tuple[dict | None, str, list[str]]:
    final = ""
    errors = []
    tool_types = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                final = item["text"]
            elif item.get("type") not in {"reasoning", "error"}:
                tool_types.append(str(item.get("type")))
        elif event.get("type") in {"turn.failed", "error"}:
            errors.append(str(event.get("error") or event.get("message"))[:500])
    try:
        return json.loads(final), "", tool_types
    except (json.JSONDecodeError, TypeError):
        return None, ("; ".join(errors) or "missing_valid_final_json"), tool_types


def run_one(manifest: dict, case: dict, repetition: int, arm: str, instructions: str) -> dict:
    with TemporaryDirectory(prefix="runtime-context-native-") as raw:
        temporary = Path(raw)
        trace = temporary / "calls.jsonl"
        command = native_command(manifest, case, instructions, trace, temporary)
        try:
            completed = subprocess.run(command, input=task_prompt(manifest, case),
                                       capture_output=True, text=True,
                                       timeout=manifest["settings"]["timeout_seconds"], check=False)
            final, error, tool_types = parse_native(completed.stdout)
            error = error or (f"native_exit_{completed.returncode}" if completed.returncode else "")
        except subprocess.TimeoutExpired:
            final, error, tool_types = None, "native_timeout", []
            completed = None
        calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    return {"case_id": case["id"], "repetition": repetition, "arm": arm,
            "instructions_sha256": digest(instructions.encode()),
            "final": final, "error": error, "exit_code": completed.returncode if completed else None,
            "tool_item_types": tool_types, "calls": calls}


def run(manifest: dict, *, selected: list[str], repetitions: int | None) -> dict:
    with TemporaryDirectory(prefix="runtime-context-baseline-") as raw:
        baseline = Path(raw) / "baseline"
        baseline.mkdir()
        archive_baseline(manifest["baseline_ref"], baseline)
        candidate_hash = digest(b"".join((ROOT / name).read_bytes() for name in (
            "app/consumer_agent.py", "app/runtime_prompt_context.py", "app/prompt.py")))
        jobs = []
        identities = {}
        for case_id in selected:
            case = resolve_case(manifest, case_id)
            case["id"] = case_id
            context = runtime_context(manifest, case)
            per_arm = {}
            for arm, root, version in (("baseline", baseline, "baseline"),
                                       ("candidate", ROOT, "candidate")):
                extracted = extract_instructions(root, version=version, context=context,
                                                 home=Path(raw))
                identities[f"{case_id}:{arm}"] = digest(extracted["instructions"].encode())
                per_arm[arm] = extracted["instructions"]
            count = repetitions if repetitions is not None else case["repeats"]
            jobs += [(case, number, arm, per_arm[arm])
                     for number in range(1, count + 1)
                     for arm in ("baseline", "candidate")]
        # A pair is adjacent in the work queue; limit simultaneous native CLI
        # invocations because the live service shares this host.
        results = []
        with ThreadPoolExecutor(max_workers=manifest["settings"]["max_concurrency"]) as pool:
            pending = [pool.submit(run_one, manifest, *job) for job in jobs]
            for future in as_completed(pending):
                result = future.result()
                results.append(result)
                print(f"{result['case_id']} #{result['repetition']} {result['arm']}: "
                      f"exit={result['exit_code']} error={result['error'] or '-'} "
                      f"calls={len(result['calls'])}", flush=True)
    results.sort(key=lambda row: (row["case_id"], row["repetition"], row["arm"]))
    return {"version": manifest["version"], "manifest_sha256": digest(MANIFEST.read_bytes()),
            "harness_sha256": digest(Path(__file__).read_bytes()),
            "fixture_server_sha256": digest(SERVER.read_bytes()),
            "baseline_ref": manifest["baseline_ref"], "candidate_source_sha256": candidate_hash,
            "settings": manifest["settings"], "instruction_hashes": identities,
            "results": results}


def blind(report: dict) -> dict:
    rng = random.Random(20261005)
    grouped: dict[tuple[str, int], list[dict]] = {}
    for row in report["results"]:
        grouped.setdefault((row["case_id"], row["repetition"]), []).append(row)
    items = []
    for key, rows in sorted(grouped.items()):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        items.append({"case_id": key[0], "repetition": key[1],
                      "outputs": [{"label": label, "final": row["final"],
                                   "error": row["error"], "calls": row["calls"]}
                                  for label, row in zip(("A", "B"), shuffled)]})
    return {"version": report["version"], "manifest_sha256": report["manifest_sha256"],
            "items": items}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Case id; omit for complete frozen corpus")
    parser.add_argument("--repetitions", type=int, help="Override repetitions per selected case")
    parser.add_argument("--artifact-version", default="v1", help="Version suffix for new result files")
    parser.add_argument("--probe", action="store_true", help="One call on first positive case")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check_manifest(manifest)
    available = {case["id"] for case in manifest["cases"]}
    selected = args.case or [case["id"] for case in manifest["cases"]]
    if not set(selected) <= available:
        parser.error("unknown case id")
    if args.repetitions is not None and args.repetitions < 1:
        parser.error("repetitions must be positive")
    if not (args.artifact_version.startswith("v") and args.artifact_version[1:].isdigit()):
        parser.error("artifact-version must be v plus digits")
    if args.probe:
        selected = ["candidate-windows-unknown-zone"]
    report = run(manifest, selected=selected,
                 repetitions=1 if args.probe else args.repetitions)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    suffix = "probe" if args.probe else "results"
    (ARTIFACTS / f"{suffix}.{args.artifact_version}.json").write_text(json.dumps(report, ensure_ascii=False,
        indent=2) + "\n", encoding="utf-8")
    (ARTIFACTS / f"{suffix}.blinded.{args.artifact_version}.json").write_text(json.dumps(blind(report),
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(not row["error"] for row in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
