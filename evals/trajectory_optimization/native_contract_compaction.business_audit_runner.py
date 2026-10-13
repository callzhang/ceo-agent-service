"""Paired native Audit business comparison; no expected answer in model input."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.agent_runtime_config import load_runtime_config
from app.agent_wire_contracts import AuditAgentWireResult
from app.audit_rules import render_audit_rules
from app.business_skills import render_task_skill_discovery
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.config import load_env_file
from app.consumer_agent import audit_developer_sections, default_task_skill_catalog
from app.native_agent_output import (
    native_developer_assembly,
    native_output_schema_path,
    native_task_prompt,
    parse_native_output,
)
from app.prompt_composition import (
    PromptSection,
    compose_plain_task_assembly,
    load_prompt_configuration,
    render_prompt_sections,
)
from app.store import AgentRole
from app.wechat.codex_safety import ControlledCliConfig, make_role_agent_command


def main():
    base = Path(__file__).resolve().parent
    root = Path(sys.argv[1]).resolve()
    manifest_path = Path(sys.argv[2]).resolve()
    manifest = json.loads(manifest_path.read_text())
    case = next(c for c in manifest["cases"] if c["id"] == "audit-document-feedback")
    work = root / case["id"]
    assert (work / "fixture.sqlite3").is_file()
    load_env_file(Path("/Users/derek/Services/ceo-agent-service/.env"))
    config = load_runtime_config(os.environ)
    route = next(r for r in config.routes if r.name == manifest["configured_route"])
    assert route.model == manifest["model"]
    specification = importlib.util.spec_from_file_location(
        "business_inputs", base / "native_contract_compaction.business_inputs.py"
    )
    inputs = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(inputs)
    rules = render_audit_rules(AgentRole.AUDIT, create_missing=False)
    context = inputs.audit_context(case, audit_rules=rules)
    configuration = load_prompt_configuration(create_missing=False, role="audit")
    sections = audit_developer_sections(
        rules, runtime_context="", prompt_configuration=configuration
    )
    full = render_prompt_sections(sections, placement="developer")
    compact = native_developer_assembly(full, sections, "audit").text
    discovery = render_task_skill_discovery(
        context.task.skill_names,
        catalog=default_task_skill_catalog(context.task.skill_names),
        frozen_names=context.task.skill_names,
    )
    task = compose_plain_task_assembly(
        PromptSection("任务 Skill 入口", "任务冻结 Skill 选择", "task", discovery),
        PromptSection(
            "当前审核任务", "当前任务与候选", "task",
            context.render(current_time=manifest["fixed_runtime_time"]),
        ),
    ).text
    prompt = native_task_prompt(task, "audit")
    (work / "candidate.json").write_text(context.candidate.model_dump_json(indent=2))
    (work / "submitted-task.txt").write_text(prompt)
    adapter = CodexRuntimeAdapter(work, config)
    records = []
    for variant, developer in (("full", full), ("compact", compact)):
        stem = work / variant
        developer_path = stem.with_suffix(".developer.txt")
        developer_path.write_text(developer)
        output = stem.with_suffix(".output.json")
        # Substitute only the eval transport; no live service connector in this comparison.
        with patch("app.codex_runtime_adapter.service_mcp_config_options", lambda: []):
            argv = adapter.build_command(
                route=route, prompt=prompt, session_id=None, image_paths=None,
                output_schema_path=native_output_schema_path("audit"),
                use_output_schema=True, approval_policy="never",
                developer_instructions=developer, use_approval_bypass=False,
                sandbox_mode="read-only", skip_git_repo_check=True,
                reasoning_effort=manifest["reasoning_effort"],
            )
        make_role_agent_command(
            argv, role="audit",
            controlled_cli=ControlledCliConfig(
                command=sys.executable,
                args=(str(base / "native_contract_compaction.business_fixture.py"),
                      "--root", str(work), "--manifest", str(manifest_path),
                      "--case", case["id"], "--phase", "cold", "--log-label", variant),
                cwd=str(work),
            ),
        )
        argv[-1:-1] = ["-c", "mcp_servers.agent_cli.enabled=true", "--output-last-message", str(output)]
        with stem.with_suffix(".trajectory.jsonl").open("w") as stdout, stem.with_suffix(".stderr.txt").open("w") as stderr:
            completed = subprocess.run(
                argv, input=prompt, text=True, stdout=stdout, stderr=stderr,
                env=adapter.build_env(route), cwd=work, timeout=600,
            )
        raw = stem.with_suffix(".trajectory.jsonl").read_text()
        wire = json.loads(parse_native_output(raw, "audit")) if raw else None
        canonical = (
            AuditAgentWireResult.model_validate(wire).to_result().model_dump(mode="json")
            if wire else None
        )
        events = [json.loads(line) for line in stem.with_suffix(".trajectory.jsonl").read_text().splitlines() if line.startswith("{")]
        session = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
        record = {
            "case": case["id"], "variant": variant,
            "model": route.model, "route": route.name, "phase": "cold",
            "exit_code": completed.returncode, "session_id": session,
            "turn_completed": any(e.get("type") == "turn.completed" for e in events),
            "candidate_digest": context.candidate_digest,
            "proposal_revision": context.proposal_revision,
            "canonical": canonical,
            "wire": wire,
            "task_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "developer_sha256": hashlib.sha256(developer.encode()).hexdigest(),
            "developer_characters": len(developer),
            "native_schema_sha256": hashlib.sha256(native_output_schema_path("audit").read_bytes()).hexdigest(),
        }
        records.append(record)
        (root / "audit-results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(json.dumps({"variant": variant, "exit_code": completed.returncode, "session_id": session, "outcome": canonical and canonical.get("outcome")}), flush=True)


if __name__ == "__main__":
    main()
