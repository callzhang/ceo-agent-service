"""Compare one native Meeting input with and without an unrelated project document.

All meeting, group, and workspace document facts in this probe are fictional.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.business_skills import codex_skill_config_override
from app.meeting_alignment_agent import (
    build_meeting_alignment_prompt,
    parse_meeting_alignment_decision,
)
from meeting_skill_scope_native import MODEL, _cases, _checks


def _run(prompt: str, directory: str, schema: Path, *, prune: bool) -> dict:
    command = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "-C", directory, "-s", "read-only", "-m", MODEL,
        "-c", codex_skill_config_override(()),
        *(["-c", "project_doc_max_bytes=0"] if prune else []),
        "--json", "--output-schema", str(schema), "-",
    ]
    completed = subprocess.run(
        command, input=prompt, capture_output=True, text=True, timeout=240,
        check=False,
    )
    events = []
    for line in completed.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    messages = [
        event.get("item", {}).get("text", "") for event in events
        if event.get("item", {}).get("type") == "agent_message"
    ]
    decision = None
    validation_error = ""
    if messages:
        try:
            decision = parse_meeting_alignment_decision(messages[-1])
        except Exception as exc:
            validation_error = str(exc)[:300]
    return {
        "exit_code": completed.returncode,
        "pydantic_valid": decision is not None,
        "validation_error": validation_error,
        "tool_calls": sum(
            event.get("item", {}).get("type") in {"mcp_tool_call", "function_call"}
            for event in events
        ),
        "decision": decision,
        "error_excerpt": completed.stderr[:300] if completed.returncode else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    schema = Path(__file__).resolve().parents[2] / "app/schemas/meeting_alignment_decision.schema.json"
    skill = (Path.home() / ".agents/skills/ceo-meeting-work/SKILL.md").read_text()
    digest = hashlib.sha256(skill.encode()).hexdigest()
    protocol = f"## Managed Skill: ceo-meeting-work\nsha256: {digest}\n\n{skill}"
    results = []
    with tempfile.TemporaryDirectory(prefix="meeting-project-doc-", dir="/tmp") as directory:
        project_doc = Path(directory) / "AGENTS.md"
        project_doc.write_text(
            "# Synthetic workspace filing notes\n"
            + ("Archive reference examples under their subject folder. "
               "These folder rules contain no meeting facts.\n" * 83)
        )
        doc_chars = len(project_doc.read_text())
        for name, source, groups in _cases():
            if name not in {"business_alignment", "mixed_sensitive"}:
                continue
            prompt = build_meeting_alignment_prompt(
                source,
                work_profile="For this fictional case, cite meeting facts and keep personnel matters private.",
                work_profile_source="",
                group_candidates=groups,
                consumer_prompt="只依据本次虚构会议和给定的虚构证据判断。",
                skill_protocol=protocol,
            )
            for arm, prune in (("baseline", False), ("candidate", True)):
                response = _run(prompt, directory, schema, prune=prune)
                decision = response.pop("decision")
                checks = _checks(name, decision) if decision else {}
                accepted = (
                    response["exit_code"] == 0
                    and response["pydantic_valid"]
                    and response["tool_calls"] == 0
                    and all(checks.values())
                )
                item = {"case": name, "arm": arm, "accepted": accepted,
                        "checks": checks, **response}
                results.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
    args.output.write_text(json.dumps({
        "model": MODEL,
        "scope": "fictional Meeting source and directory notes; read-only native Codex OAuth",
        "same_prompt_per_pair": True,
        "workspace_doc_chars": doc_chars,
        "skill_sha256": digest,
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
