"""Native MCP fixture: real OKR Skill scripts, synthetic source and document IO."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from functools import wraps
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

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
            "diagnostic": (
                "Current-quarter member endpoint returned 404 for userId "
                "eval-failed (request_id=fixture-okr-member-404); roster and "
                "eval-a/eval-b reads succeeded in this collection run, with no "
                "shared authorization or runtime error"
            ),
        }
        outcome.update(
            kind="source_failed",
            scope=failure["scope"],
            diagnostic=failure["diagnostic"],
            evidence=(
                "Verified isolated current-quarter member endpoint 404; the "
                "same-run roster and other member reads succeeded"
                if failure["scope"] == "member"
                else "Shared source read failed; no score established"
            ),
        )
        provider_receipt = {
            "provider": "dingtang_okr",
            "operation": "read_current_quarter_member",
            "request_id": "fixture-okr-member-404" if failure["scope"] == "member" else "fixture-okr-auth-unavailable",
            "principal_user_id": "fixture-principal",
            "authorization_status": "granted" if failure["scope"] == "member" else "unavailable",
            "http_status": 404 if failure["scope"] == "member" else 503,
            "error_code": "member_not_found" if failure["scope"] == "member" else failure.get("code", "auth_unavailable"),
        }
        return {
            "manager": identity,
            "sourceOutcome": outcome,
            "providerReceipt": provider_receipt,
        }
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
        "providerReceipt": {
            "provider": "dingtang_okr",
            "operation": "read_current_quarter_member",
            "request_id": "fixture-okr-member-200-" + uid,
            "principal_user_id": "fixture-principal",
            "authorization_status": "granted",
            "http_status": 200,
            "error_code": "",
        },
        "liveOkr": live,
        "independentEvidence": [
            facts["evidence"][uid],
            facts["evidence"].get("behavior_by_manager", {}).get(
                uid, facts["evidence"]["behavior"]
            ),
        ],
    }


class DocumentProvider:
    """Synthetic provider IO behind the real application tool callbacks."""

    dws_bin = "synthetic-dws"

    def __init__(self, fixture):
        self.fixture = fixture

    def run_json(self, command):
        if command[1:3] != ["wiki", "+node-list"]:
            raise ValueError("Unconfigured synthetic provider operation")
        parent = command[command.index("--folder") + 1]
        return {"data": {"hasMore": False, "nodes": [
            {"nodeId": node, "name": doc["title"]}
            for node, doc in self.fixture.documents().items() if doc["parent_id"] == parent]}}

    def create_report_document(self, *, name, content, doc_format, folder_id):
        if doc_format != "markdown":
            raise ValueError("This frozen workflow submits Markdown artifacts")
        docs = self.fixture.documents()
        node = "eval-doc-" + str(len(docs) + 1)
        docs[node] = {"title": name, "content": content, "parent_id": folder_id, "revision": 1}
        self.fixture.docs_path.write_text(json.dumps(docs, ensure_ascii=False))
        return {"ok": True, "data": {"nodeId": node, "success": True}}

    def overwrite_report_document(self, *, node_id, content, doc_format, expected_revision=None):
        if doc_format != "markdown":
            raise ValueError("This frozen workflow submits Markdown artifacts")
        docs = self.fixture.documents()
        docs[node_id].update(content=content, revision=docs[node_id]["revision"] + 1)
        self.fixture.docs_path.write_text(json.dumps(docs, ensure_ascii=False))
        return {"ok": True, "data": {"nodeId": node_id, "success": True}}

    def read_doc(self, node_id):
        doc = self.fixture.documents()[node_id]
        return {"complete": True, "status": "success", "content": {
            "markdown": doc["content"], "nodeId": node_id, "title": doc["title"],
            "revision": doc["revision"], "success": True,
            "docUrl": "https://docs.example.invalid/" + node_id}}

    def doc_info(self, node_id):
        doc = self.fixture.documents()[node_id]
        return {"complete": True, "ok": True, "status": "success", "data": {"document": {
            "nodeId": node_id, "name": doc["title"], "folderId": doc["parent_id"],
            "workspaceId": "eval-workspace", "success": True,
            "docUrl": "https://docs.example.invalid/" + node_id}}}


class Fixture:
    def __init__(self, facts, work, scheduled_run_id=1):
        self.facts, self.work = facts, Path(work)
        self.scheduled_run_id = scheduled_run_id
        self.work.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.work.with_name(self.work.name + "-tool-calls.jsonl")
        self.docs_path = self.work.with_name(self.work.name + "-provider.json")

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
            "bytes": p.stat().st_size,
            "path": str(p),
            "sha256": digest(p.read_bytes()),
        }

    def artifact_manifest(self):
        return [
            {"name": path.name, "sha256": digest(path.read_bytes())}
            for path in sorted(self.work.iterdir())
            if path.is_file()
            and not path.is_symlink()
            and not path.name.startswith(".task-artifact-")
        ]

    def documents(self):
        return json.loads(self.docs_path.read_text()) if self.docs_path.exists() else {}

    @contextmanager
    def production_tools(self, role):
        from app import agent_cli

        with ExitStack() as stack:
            stack.enter_context(patch.object(agent_cli, "_current_task_generation", return_value="fixture"))
            stack.enter_context(patch.object(agent_cli, "_task_file_root", return_value=self.work))
            stack.enter_context(patch.object(agent_cli, "_bound_scheduled_run", return_value=(None, None)))
            stack.enter_context(patch("app.dws_client.DwsClient", return_value=DocumentProvider(self)))
            bound = agent_cli.build_role_server(role, task_id=1, db_path=Path("/unused-fixture-db"), execution_generation="fixture")
            names = ["read_task_artifact", "list_task_artifacts", "read_dingtalk_document", "read_dingtalk_document_info"]
            if role == "consumer":
                names += ["consumer_artifact_write", "consumer_document_write"]

            def recorded(name, callback):
                @wraps(callback)
                def call(*args, **kwargs):
                    arguments = dict(inspect.signature(callback).bind(*args, **kwargs).arguments)
                    result = callback(*args, **kwargs)
                    if name == "consumer_document_write":
                        docs = self.documents()
                        docs[result["node_id"]].update(artifact_name=arguments["artifact_name"],
                            readback_name=result["readback"]["artifact_name"])
                        self.docs_path.write_text(json.dumps(docs, ensure_ascii=False))
                    return self.record(name, arguments, result)
                return call

            yield {name: recorded(name, bound._tool_manager.get_tool(name).fn) for name in names}

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

    def build_server(self, role, production):
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
                    "timezone_name": "Asia/Shanghai",
                    "run_id": self.scheduled_run_id, "scheduled_task_id": 1, "task_version": 1,
                    "principal_user_id": "fixture-principal", "history": [],
                    "artifact_directory": str(self.work),
                    "task_artifacts": self.artifact_manifest(),
                },
            )

        for name, callback in production.items():
            server.add_tool(callback, name=name)

        server.add_tool(read_system_action_contract)
        if role == "audit":
            return server

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
                {**self.artifact(filename), "user_id": user_id, "source_outcome": payload["sourceOutcome"]},
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

        return server

    def serve(self, role):
        with self.production_tools(role) as production:
            self.build_server(role, production).run(transport="stdio")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--role", choices=("consumer", "audit"), default="consumer")
    parser.add_argument("--run-id", type=int, default=1)
    args = parser.parse_args()
    Fixture(
        json.loads(Path(args.facts).read_text()), args.work, args.run_id
    ).serve(args.role)
