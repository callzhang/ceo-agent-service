"""Native MCP fixture: real OKR Skill scripts, synthetic source and document IO."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "ci/shared-skills/ceo-weekly-okr-report"


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        "okr_fixture_" + name, SKILL / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(value).hexdigest()


def key(user_id):
    return digest(user_id.encode())[:16]


def member_payload(member, facts):
    uid = member["user_id"]
    identity = {
        "name": member["name"],
        "title": member["title"],
        "userId": uid,
        "openDingtalkId": member["open_dingtalk_id"],
    }
    outcome = {
        "user_id": uid,
        "stage": "collection",
        "kind": "collected",
        "evidence": "Verified synthetic current source",
    }
    if facts.get("shared_failure") or uid == "eval-failed":
        failure = facts.get("shared_failure") or {
            "scope": "member",
            "diagnostic": "Synthetic member-bound source unavailable",
        }
        outcome.update(
            kind="source_failed",
            scope=failure["scope"],
            diagnostic=failure["diagnostic"],
            evidence="Current source read failed; no score established",
        )
        return {"manager": identity, "sourceOutcome": outcome}
    if uid == "eval-missing":
        outcome.update(
            kind="goals_missing",
            evidence="Verified current period exists and complete live objective list is empty",
        )
        return {
            "manager": identity,
            "sourceOutcome": outcome,
            "liveOkr": {
                "userId": uid,
                "periodLabel": facts["period_label"],
                "processed": {"objectives": [], "okrRows": []},
            },
        }
    live = {
        "userId": uid,
        "periodLabel": facts["period_label"],
        "processed": {
            "objectives": [{"id": "o-" + uid, "title": "交付目标 " + uid}],
            "okrRows": [
                {
                    "level": "KR",
                    "objectiveId": "o-" + uid,
                    "objectiveTitle": "交付目标 " + uid,
                    "objectiveWeight": 100,
                    "krId": "kr-" + uid,
                    "krTitle": "交付并验收 " + uid,
                    "krWeight": 100,
                    "krProgress": 100,
                    "krDetailsUpdatesAggregated": "成员自报完成100%；需独立核实实际效果和验收。",
                }
            ],
        },
    }
    return {
        "manager": identity,
        "sourceOutcome": outcome,
        "liveOkr": live,
        "independentEvidence": [facts["evidence"][uid], facts["evidence"]["behavior"]],
    }


class Fixture:
    def __init__(self, facts, work):
        self.facts, self.work = facts, Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.work / "tool-calls.jsonl"
        self.docs_path = self.work / "documents.json"

    def write_json(self, name, payload):
        (self.work / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def record(self, name, arguments, result):
        with self.trace_path.open("a") as stream:
            stream.write(
                json.dumps(
                    {"tool": name, "arguments": arguments, "result": result},
                    ensure_ascii=False,
                )
                + "\n"
            )
        return result

    def artifact(self, name):
        p = self.work / name
        return {
            "artifact_name": name,
            "size_bytes": p.stat().st_size,
            "sha256": digest(p.read_bytes()),
        }

    def documents(self):
        return json.loads(self.docs_path.read_text()) if self.docs_path.exists() else {}

    def destination(self, args):
        module = load_script("destination")
        documents = self.documents()

        def source(command):
            if command[:2] == ["wiki", "+space-list"]:
                return {
                    "data": {
                        "spaces": [
                            {"name": "🎯  目标与执行", "workspaceId": "eval-workspace"}
                        ],
                        "hasMore": False,
                    }
                }
            if command[:2] == ["wiki", "+node-list"]:
                if "--folder" not in command:
                    nodes = [{"name": "管理者 OKR 进度周报", "nodeId": "eval-folder"}]
                else:
                    parent = command[command.index("--folder") + 1]
                    nodes = [
                        {"name": d["title"], "nodeId": node}
                        for node, d in documents.items()
                        if d["parent_id"] == parent
                    ]
                return {"data": {"nodes": nodes, "hasMore": False}}
            raise ValueError("Unconfigured synthetic destination operation")

        module.run = source
        parser = argparse.ArgumentParser()
        parser.add_argument("--title", required=True)
        parser.add_argument("--parent", default="")
        parsed = parser.parse_args(args)
        return {
            "exit_code": 0,
            "stdout": json.dumps(
                module.resolve(parsed.title, parsed.parent), ensure_ascii=False
            ),
            "stderr": "",
        }

    def serve(self, role):
        from mcp.server.fastmcp import FastMCP
        from app.action_contract_catalog import read_system_action_contract

        server = FastMCP("okr_acceptance")

        @server.tool()
        def read_task_skill(name: str) -> dict:
            """Read a selected Skill's exact source."""
            if name == "ceo-weekly-okr-report":
                path = SKILL / "SKILL.md"
            elif name == "dingtang-okr-review":
                path = Path.home() / ".agents/skills/dingtang-okr-review/SKILL.md"
            else:
                raise ValueError("Skill is not selected for this synthetic task")
            return self.record(
                "read_task_skill",
                {"name": name},
                {"name": name, "content": path.read_text()},
            )

        @server.tool()
        def read_scheduled_context() -> dict:
            """Read the synthetic trigger and persisted artifacts from this run."""
            return self.record(
                "read_scheduled_context",
                {},
                {
                    "scheduled_for": self.facts["report_date"] + "T10:00:00Z",
                    "timezone": "Asia/Shanghai",
                    "artifact_directory": str(self.work),
                    "artifacts": [p.name for p in self.work.iterdir() if p.is_file()],
                },
            )

        @server.tool()
        def read_task_artifact(
            name: str, offset: int = 0, max_chars: int = 20000
        ) -> dict:
            """Read the exact current run artifact with pagination."""
            if Path(name).name != name:
                raise ValueError("Use a flat artifact file name")
            text = (self.work / name).read_text()
            return self.record(
                "read_task_artifact",
                {"name": name, "offset": offset, "max_chars": max_chars},
                {
                    "name": name,
                    "content": text[offset : offset + max_chars],
                    "offset": offset,
                    "total_chars": len(text),
                    "has_more": offset + max_chars < len(text),
                },
            )

        @server.tool()
        def read_dingtalk_document_info(node_id: str) -> dict:
            """Read the canonical synthetic provider URL and document revision."""
            doc = self.documents()[node_id]
            return self.record(
                "read_dingtalk_document_info",
                {"node_id": node_id},
                {
                    "node_id": node_id,
                    "title": doc["title"],
                    "revision": doc["revision"],
                    "url": "https://docs.example.invalid/" + node_id,
                },
            )

        server.add_tool(read_system_action_contract)
        if role == "audit":
            server.run(transport="stdio")
            return

        @server.tool()
        def read_okr_roster(group_name: str) -> dict:
            """Read the current synthetic roster and retain stable user IDs."""
            if group_name != self.facts["roster"]["name"]:
                raise ValueError("Unknown fixture group")
            self.write_json("okr-roster.json", self.facts["roster"])
            return self.record(
                "read_okr_roster",
                {"group_name": group_name},
                {
                    **self.artifact("okr-roster.json"),
                    "member_count": len(self.facts["roster"]["managers"]),
                },
            )

        @server.tool()
        def read_okr_member(
            user_id: str,
            name: str,
            period_label: str,
            title: str = "",
            open_dingtalk_id: str = "",
        ) -> dict:
            """Read one exact member's live source; one bounded source request."""
            member = next(
                m for m in self.facts["roster"]["managers"] if m["user_id"] == user_id
            )
            if (name, title, open_dingtalk_id, period_label) != (
                member["name"],
                member["title"],
                member["open_dingtalk_id"],
                self.facts["period_label"],
            ):
                raise ValueError("Current source identity mismatch")
            payload = member_payload(member, self.facts)
            filename = "okr-member-" + digest(user_id.encode()) + ".json"
            self.write_json(filename, payload)
            return self.record(
                "read_okr_member",
                {"user_id": user_id, "period_label": period_label},
                {**self.artifact(filename), "outcome": payload["sourceOutcome"]},
            )

        @server.tool()
        def consumer_artifact_write(name: str, content: str) -> dict:
            """Save exact text in the current run's flat artifact directory."""
            if Path(name).name != name:
                raise ValueError("Use a flat artifact file name")
            (self.work / name).write_text(content)
            return self.record(
                "consumer_artifact_write", {"name": name}, self.artifact(name)
            )

        @server.tool()
        def consumer_run_skill_script(
            name: str, script: str, arguments: list[str] | None = None
        ) -> dict:
            """Run the selected real Skill's pure report script or mocked destination adapter."""
            if name != "ceo-weekly-okr-report" or script not in (
                "scripts/report.py",
                "scripts/destination.py",
            ):
                raise ValueError("Unknown selected Skill script")
            if script == "scripts/destination.py":
                result = self.destination(arguments or [])
            else:
                p = subprocess.run(
                    [sys.executable, str(SKILL / script), *(arguments or [])],
                    cwd=self.work,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={**os.environ, "PYTHONPATH": str(ROOT)},
                )
                result = {
                    "exit_code": p.returncode,
                    "stdout": p.stdout,
                    "stderr": p.stderr,
                }
            return self.record(
                "consumer_run_skill_script",
                {"name": name, "script": script, "arguments": arguments or []},
                result,
            )

        @server.tool()
        def consumer_document_write(
            artifact_name: str,
            title: str,
            doc_format: str = "markdown",
            node_id: str = "",
            parent_id: str = "",
            workspace_id: str = "",
            expected_revision: int | None = None,
        ) -> dict:
            """Mock provider: write exact artifact, persist revision and provide full readback."""
            docs = self.documents()
            if node_id:
                doc = docs[node_id]
                if expected_revision != doc["revision"]:
                    raise ValueError("Synthetic document revision conflict")
                revision = doc["revision"] + 1
                parent_id = doc["parent_id"]
            else:
                if not parent_id or workspace_id != "eval-workspace":
                    raise ValueError("Explicit provider parent/workspace is required")
                node_id, revision = "eval-doc-" + str(len(docs) + 1), 1
            content = (self.work / artifact_name).read_text()
            docs[node_id] = {
                "title": title,
                "parent_id": parent_id,
                "revision": revision,
                "content": content,
                "artifact_name": artifact_name,
            }
            self.write_json("documents.json", docs)
            readback = "readback-" + node_id + ".md"
            (self.work / readback).write_text(content)
            return self.record(
                "consumer_document_write",
                {
                    "artifact_name": artifact_name,
                    "title": title,
                    "node_id": node_id,
                    "expected_revision": expected_revision,
                },
                {
                    "success": True,
                    "node_id": node_id,
                    "revision": revision,
                    "readback_artifact": self.artifact(readback),
                    "synthetic_provider": True,
                },
            )

        server.run(transport="stdio")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--role", choices=("consumer", "audit"), default="consumer")
    args = parser.parse_args()
    Fixture(json.loads(Path(args.facts).read_text()), args.work).serve(args.role)
