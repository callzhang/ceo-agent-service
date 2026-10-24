# ruff: noqa: E402
"""Anonymous, isolated native Task runner sequence; never use a service database."""

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_router import AgentRuntimeRouter, RoutedCodexExecution
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.config import load_env_file
from app import native_trajectory
from app.store import NATIVE_TASK_RUN_SQL
from app.store import AutoReplyStore
from app.task_agent import (
    TASK_RUNTIME_CAPABILITIES,
    TaskAgentCodexRunner,
    TaskAgentRunner,
    process_work_item,
)
from app.task_models import WorkItem
from app.wechat.codex_safety import inherited_native_server_names


def copy_anonymous_skill(original: Path, target: Path):
    target.parent.mkdir(parents=True)
    target.write_text(original.read_text() + "\n<!-- anonymous runner revision 1 -->\n")
    references = original.parent / "references"
    if references.is_dir():
        shutil.copytree(references, target.parent / "references")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--phase", choices=("cold", "risk"), required=True)
    parser.add_argument("--skill-source", type=Path, default=REPO / "ci/shared-skills/ceo-work-tracking/SKILL.md")
    parser.add_argument("--current-time", default="")
    parser.add_argument("--schema-source", type=Path)
    args = parser.parse_args()
    if args.schema_source is not None:
        from app import task_agent
        task_agent.TASK_AGENT_DECISION_SCHEMA_PATH = args.schema_source.resolve()
    workspace = args.workspace.resolve()
    assert workspace.is_dir() and workspace.is_relative_to(Path("/private/tmp"))
    phase = args.phase
    source_root = workspace / ".agents" / "skills"
    source = source_root / "ceo-work-tracking" / "SKILL.md"
    if phase == "cold":
        assert not list(workspace.iterdir())
        copy_anonymous_skill(args.skill_source, source)
    else:
        assert source.is_file()
    store = AutoReplyStore(workspace / "runtime.sqlite3")
    manifest = json.loads((REPO / "evals/trajectory_optimization/task_schema_titles.release.cases.v1.json").read_text())
    seed = manifest["cases"][0 if phase == "cold" else 1]["work_item"]
    if phase == "cold":
        padding = "附件索引：本行没有项目决议、分工、风险或独立行动项。\n" * 110
        seed["summary"] = padding + "\n项目会议决议：\n" + seed["summary"] + "\n" + padding
    now = datetime.fromisoformat(args.current_time) if args.current_time else datetime.now(UTC)
    seed["source"]["ref"] = "meeting:anonymous-full-runner-" + phase
    seed["source"]["created_at"] = now.isoformat()
    item = WorkItem.model_validate(seed)
    load_env_file(Path("/Users/derek/Services/ceo-agent-service/.env"))
    config = load_runtime_config(os.environ)
    route = next(r for r in config.routes if r.name == "codex_oauth")
    config = config.model_copy(update={"routes": (route,)})
    # This diagnostic snapshot selects one route; it is not production capability evidence.
    probe_time = datetime.now(UTC)
    snapshot = RuntimeCapabilitySnapshot(
        route_name=route.name, capabilities=TASK_RUNTIME_CAPABILITIES, healthy=True,
        checked_at=probe_time.isoformat(), expires_at=(probe_time + timedelta(hours=1)).isoformat(),
    )
    submissions = []

    class IsolatedAdapter(CodexRuntimeAdapter):
        def build_command(self, **kwargs):
            submissions.append({
                "session_id": kwargs["session_id"],
                "input_sha256": hashlib.sha256(kwargs["prompt"].encode()).hexdigest(),
                "input_chars": len(kwargs["prompt"]),
                "skill_names": kwargs["skill_names"],
                "native_selection": kwargs["prompt"].startswith(f"[${source.parent.name}]({source})"),
            })
            (workspace / f"{phase}-input-{len(submissions)}.txt").write_text(kwargs["prompt"])
            kwargs.update(approval_policy="never", sandbox_mode="read-only",
                          skip_git_repo_check=True, reasoning_effort="medium")
            command = super().build_command(**kwargs)
            if "--output-schema" in command:
                schema_path = Path(command[command.index("--output-schema") + 1])
                submissions[-1]["native_schema_path"] = str(schema_path)
                submissions[-1]["native_schema_sha256"] = hashlib.sha256(schema_path.read_bytes()).hexdigest()
            insertion = len(command) - (2 if kwargs["session_id"] else 1)
            command[insertion:insertion] = [
                "-c", "features.hooks=false", "-c", 'web_search="disabled"',
                *(option for name in inherited_native_server_names()
                  for option in ("-c", f"mcp_servers.{name}.enabled=false")),
            ]
            return command

    adapter = IsolatedAdapter(workspace, config)
    from app.agent_runtime_router import run_process_with_idle_timeout

    execution = RoutedCodexExecution(
        store=store, config=config,
        router=AgentRuntimeRouter(routes=config.routes, store=store, snapshots={route.name: snapshot}),
        adapter=adapter, executor=run_process_with_idle_timeout,
        total_timeout_seconds=600, idle_timeout_seconds=300,
    )
    runner = TaskAgentRunner(TaskAgentCodexRunner(routed_execution=execution))
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    claimed = store.claim_work_summary_inputs(limit=1)
    assert len(claimed) == 1 and claimed[0].id == input_id
    from app import task_agent
    original_builder = task_agent.build_task_agent_prompt
    def fixed_builder(*items, **options):
        options["current_time"] = now.isoformat()
        return original_builder(*items, **options)
    with (
        patch("app.task_agent.build_task_agent_prompt", fixed_builder),
        patch("app.task_agent.WORK_TRACKING_SKILL_PATH", source),
        patch("app.task_agent.memory_connector_config_issue", lambda: "Anonymous diagnostic: no Memory transport configured."),
        patch("app.codex_runtime_adapter.service_mcp_config_options", lambda **_options: []),
    ):
        process_work_item(store, runner, claimed[0], now=now.isoformat())
    sid = runner.codex.last_session_id
    assert sid
    native = native_trajectory.find_codex_session_path(sid)
    assert native is not None
    with store._connect() as db:
        runs = [dict(row) for row in db.execute(f"select * from ({NATIVE_TASK_RUN_SQL}) order by id")]
        attempts = [dict(row) for row in db.execute("select * from runtime_attempts order by id")]
        check = db.execute("pragma quick_check").fetchone()[0]
    receipt = {
        "phase": phase, "route_name": route.name, "model": route.model, "service_delivery": True, "input_id": input_id,
        "input_status": store.get_work_summary_input(input_id).status.value,
        "session_id": sid, "native_path": str(native),
        "native_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
        "native_lines": len(native.read_bytes().splitlines()),
        "source_sha256": hashlib.sha256(source.read_text().strip().encode()).hexdigest(),
        "submissions": submissions, "runs": runs, "attempts": attempts,
        "projects": len(store.list_business_projects()),
        "tasks": len(store.list_business_tasks()),
        "attention": len(store.list_business_attention_items()), "quick_check": check,
        "boundary": "Owned Store and anonymous facts; isolated readonly/MCP-free adapter. Full Task processing/router/native/application, not scheduler/production/configured-route acceptance.",
    }
    (workspace / f"{phase}-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({key: receipt[key] for key in ("phase", "input_status", "session_id", "projects", "tasks", "attention", "quick_check")}))


if __name__ == "__main__":
    main()
