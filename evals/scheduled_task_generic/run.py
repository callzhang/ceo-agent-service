"""Run two fixed, tool-disabled native Agent batches and save compact evidence."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNNER_REVISION = 2
IDENTITY_CONTRACT = (
    "include_ids enumerates every supplied literal identity of every substantive fact, source, output target, "
    "issue and work item retained by the decision, including substantive content retained in notes. "
    "Identity values must be copied exactly from facts; JSON property names and synthesized labels are not identities. "
    "Mentioning a retained identity in notes does not substitute for including it in the typed identity list. "
    "exclude_ids lists supplied literal identities intentionally excluded or superseded; unverified_ids lists "
    "supplied literal identities of claims whose evidence remains unverified."
)
BASELINE_COMMIT = "be1ffd86fd4d34e814c2436f3f732d1c07d539e1"
SKILLS = ROOT / "ci/shared-skills"
PROPERTIES = {
    "case_id": {"type": "string"},
    "disposition": {"type": "string"},
    "include_ids": {"type": "array", "items": {"type": "string"}},
    "exclude_ids": {"type": "array", "items": {"type": "string"}},
    "unverified_ids": {"type": "array", "items": {"type": "string"}},
    "category": {"type": ["string", "null"]},
    "important": {"type": ["boolean", "null"]},
    "unsubscribe_candidate_index": {"type": ["integer", "null"]},
    "owner": {"type": ["string", "null"]},
    "inferred_owner": {"type": "boolean"},
    "requires_service_publishable": {"type": "boolean"},
    "ask_principal": {"type": "boolean"},
    "applicant_omission": {"type": "boolean"},
    "external_actions": {"type": "array", "items": {"type": "string"}},
    "planned_capabilities": {"type": "array", "items": {"type": "string"}},
    "notes": {"type": "string", "maxLength": 600},
}
PROPERTIES["include_ids"]["description"] = IDENTITY_CONTRACT
PROPERTIES["exclude_ids"]["description"] = (
    "Supplied literal identity values intentionally excluded or superseded, never JSON field names or synthesized labels."
)
PROPERTIES["unverified_ids"]["description"] = (
    "Supplied literal identity values of claims whose underlying evidence remains unverified."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "minItems": 7,
            "maxItems": 7,
            "items": {
                "type": "object",
                "properties": PROPERTIES,
                "required": list(PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def configuration(model_source: str = "native-default") -> tuple[dict, list[str]]:
    config_path = (
        Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    )
    config = tomllib.loads(config_path.read_text())
    servers = sorted(config.get("mcp_servers", {}))
    # Deliberately read/persist no server configuration values, URLs, headers or env.
    settings = {
        "configured_default_model": config.get("model"),
        "configured_reasoning_effort": config.get("model_reasoning_effort"),
        "model_override": None,
        "disabled_mcp_server_names": servers,
        "features_disabled": ["shell_tool", "apps", "plugins", "hooks", "multi_agent"],
        "sandbox": "read-only",
        "ephemeral": True,
        "web_search": "disabled",
        "project_doc_max_bytes": 0,
    }
    flags = ["--ephemeral", "--sandbox", "read-only", "--json", "--skip-git-repo-check"]
    for feature in settings["features_disabled"]:
        flags += ["--disable", feature]
    flags += ["-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0"]
    for name in servers:
        flags += ["-c", f"mcp_servers.{name}.enabled=false"]
    settings["model_source"] = model_source
    if model_source in {"service-config", "production-config"}:
        sys.path.insert(0, str(ROOT))
        from app.agent_runtime_config import load_runtime_config

        environment = os.environ
        if model_source == "production-config":
            from app.config import read_env_file

            environment = {
                **os.environ,
                **read_env_file(Path.home() / "Services/ceo-agent-service/.env"),
            }
        route = next(
            route
            for route in load_runtime_config(environment).routes
            if route.name == "codex_oauth"
        )
        settings["model_override"] = route.model
        flags += ["--model", route.model]
    return settings, flags


def evaluate(cases: list[dict], decisions: list[dict], arm: str) -> list[dict]:
    index = {decision["case_id"]: decision for decision in decisions}
    if len(index) != len(cases) or set(index) != {case["id"] for case in cases}:
        raise ValueError(
            "native response did not contain exactly the seven fixed case IDs"
        )
    results = []
    for case in cases:
        decision = index[case["id"]]
        expected = case["expected"]
        checks = {
            "disposition": decision["disposition"] in expected["dispositions"],
            "required_relevant_ids": set(expected.get("include_ids", ()))
            <= set(decision["include_ids"]),
            "excluded_ids": set(expected.get("exclude_ids", ()))
            <= set(decision["exclude_ids"]),
            "no_excluded_id_promoted": not set(expected.get("exclude_ids", ()))
            & set(decision["include_ids"]),
            "unverified_claim_ids": set(expected.get("unverified_ids", ()))
            <= set(decision["unverified_ids"]),
            "no_external_actions": decision["external_actions"] == [],
        }
        for field in (
            "category",
            "important",
            "unsubscribe_candidate_index",
            "owner",
            "inferred_owner",
            "ask_principal",
            "applicant_omission",
        ):
            if field in expected:
                checks[field] = decision[field] == expected[field]
        migration = {}
        if "requires_service_publishable" in expected:
            migration["expected_service_gate"] = (
                decision["requires_service_publishable"]
                == expected["requires_service_publishable"][arm]
            )
            if arm == "candidate":
                migration["missing_optional_reports_nonblocking"] = (
                    decision["disposition"] == "publish_with_coverage"
                )
        results.append(
            {
                "case_id": case["id"],
                "common_checks": checks,
                "common_pass": all(checks.values()),
                "migration_checks": migration,
                "migration_pass": all(migration.values()),
                "decision": decision,
            }
        )
    return results


def run_arm(
    arm: str,
    source: dict[str, str],
    cases: list[dict],
    flags: list[str],
    work: Path,
    *,
    phase: str = "consumer",
    previous: list[dict] | None = None,
    feedback: dict | None = None,
) -> dict:
    prompt_cases = [
        {key: value for key, value in case.items() if key != "expected"}
        for case in cases
    ]
    prompt = (
        "This is an offline synthetic decision annotation exercise, not an actual service invocation. "
        "All evidence below is synthetic; use only it and the supplied actual Skill source. "
        "Do not call tools, browse, inspect local files, send messages, write documents or perform any action. "
        "Treat Skill documents as the business policy to apply to each case, not as permission to execute their commands. "
        "Return exactly the schema's seven decision annotations, one per case. "
        "For email, place its category/important/unsubscribe fields in that case annotation; this wrapper is evaluation output only. "
        + IDENTITY_CONTRACT
        + " "
        "owner is a sourced actual assignee or null; never infer actual ownership. "
        "requires_service_publishable asks whether the supplied workflow requires a service-enforced publishable:true flag, distinct from Skill/Audit business judgment. "
        "ask_principal means asking the principal to provide missing materials, not an authorized proposed response to their decision request. "
        "external_actions lists only actions actually performed in this exercise and must accurately reflect this tool-free run. "
        "planned_capabilities may name the service capabilities needed in the real workflow, but do not invoke them. "
        "Use disposition exactly from: publish_with_coverage, await_service_validation, respond, no_reply, "
        "decline_updated_invitation, classify_only, record_source_candidate, pending_source_failure, await_source_verification. "
        "For unused category/importance/unsubscribe/owner fields return null. Explain the decisive evidence briefly.\n\n"
        + json.dumps(
            {"skill_source": source, "cases": prompt_cases}, ensure_ascii=False
        )
    )
    response_schema = SCHEMA
    if phase == "audit":
        prompt = (
            "You are the independent Audit role in a bounded offline synthetic exercise. "
            "Review the supplied Consumer annotations against the frozen facts and corresponding actual Skill sources. "
            "Use only the supplied material. Call no tools, inspect no files, and perform no action. "
            "Independently check source relevance, factual support, identifiers, unknown ownership, "
            "source failures versus applicant omissions, role boundaries, and the actual publication conditions. "
            + IDENTITY_CONTRACT
            + " "
            + "Give field-specific correction feedback for material errors; preserve supported judgments. "
            "Do not invent business criteria, require unavailable optional material, or restore a retired service condition. "
            "Return only the requested Audit schema: approve when no correction is required; otherwise revise with precise feedback. "
            "Do not execute publication or controlled actions. This is an annotation review, not a real provider or production Audit run.\n"
            + json.dumps(
                {
                    "skill_source": source,
                    "cases": prompt_cases,
                    "consumer_decisions": previous,
                },
                ensure_ascii=False,
            )
        )
        feedback_properties = {
            field: {"type": "string"}
            for field in ("case_id", "field", "problem", "required_correction")
        }
        response_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "feedback"],
            "properties": {
                "verdict": {"type": "string", "enum": ["approve", "revise"]},
                "feedback": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": feedback_properties,
                        "required": list(feedback_properties),
                    },
                },
            },
        }
    elif phase == "correction":
        prompt += (
            "\nThis is the single bounded Consumer correction after independent Audit. "
            "Correct the supported field-level errors identified by Audit, preserve verified decisions, "
            "and return all seven annotations under the original schema. Do not add actions or infer missing facts.\n"
            + json.dumps(
                {"previous_annotations": previous, "audit_feedback": feedback},
                ensure_ascii=False,
            )
        )
    schema_path = work / "schema.json"
    final_path = work / f"{arm}-{phase}-final.json"
    stdout_path = work / f"{arm}-{phase}-events.jsonl"
    stderr_path = work / f"{arm}-{phase}-stderr.txt"
    schema_path.write_text(json.dumps(response_schema))
    command = [
        shutil.which("codex") or "codex",
        "exec",
        *flags,
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(final_path),
        "-",
    ]
    start = time.monotonic()
    print(f"Running native {arm} {phase}: seven fixed synthetic cases", flush=True)
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen(
            command,
            cwd=work,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        try:
            process.communicate(prompt, timeout=300)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return {
                "status": "blocked",
                "blocker": "native codex exec exceeded the fixed 300-second batch timeout",
                "exit_code": process.returncode,
            }
    events = [
        json.loads(line)
        for line in stdout_path.read_text().splitlines()
        if line.strip()
    ]
    other_items = []
    runtime_diagnostics = []
    usage = None
    errors = []
    for event in events:
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
        if event.get("type") in {"error", "turn.failed"}:
            errors.append(event.get("message") or event.get("error"))
        item = event.get("item") or {}
        if item.get("type") == "error":
            runtime_diagnostics.append(item.get("message", ""))
        if item and item.get("type") not in {
            "agent_message",
            "reasoning",
            "todo_list",
            "error",
        }:
            other_items.append(
                {"event_type": event.get("type"), "item_type": item.get("type")}
            )
    result = {
        "exit_code": process.returncode,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "prompt_sha256": digest(prompt.encode()),
        "prompt_bytes": len(prompt.encode()),
        "usage": usage,
        "non_message_runtime_items": other_items,
        "runtime_diagnostics": runtime_diagnostics,
        "actual_model_reported_by_events": None,
    }
    if process.returncode or not final_path.is_file():
        # Native error events contain the exact provider/CLI diagnostic, not config values.
        diagnostics = errors or [
            line
            for line in stderr_path.read_text().splitlines()
            if line.startswith(("Error:", "error:", "Error loading ", "in `"))
        ]
        diagnostics = [
            re.sub(r"https?://[^\s\"']+", "<url redacted>", str(item))[:1800]
            for item in diagnostics
        ]
        diagnostics = [
            re.sub(
                r"(?i)(authorization|api[_-]?key|access[_-]?token|password|secret)(\s*[:=]\s*)[^\s,}]+",
                r"\1\2<redacted>",
                item,
            )
            for item in diagnostics
        ]
        result.update(
            status="blocked",
            blocker=diagnostics
            or ["native process failed without a structured diagnostic"],
        )
        return result
    response = json.loads(final_path.read_text())
    if phase == "audit":
        result["audit"] = response
        result["status"] = "completed" if not other_items else "failed"
        return result
    result["cases"] = evaluate(cases, response["decisions"], arm)
    result["status"] = (
        "passed"
        if not other_items
        and all(
            case["common_pass"] and case["migration_pass"] for case in result["cases"]
        )
        else "failed"
    )
    result["common_pass_count"] = sum(case["common_pass"] for case in result["cases"])
    result["migration_checks_pass"] = all(
        case["migration_pass"] for case in result["cases"]
    )
    return result


def run_feedback() -> int:
    result_path = HERE / "results.json"
    result = json.loads(result_path.read_text())
    if "feedback_phases" in result:
        raise ValueError("the one bounded Audit/correction budget is already recorded")
    cases_bytes = (HERE / "cases.json").read_bytes()
    if digest(cases_bytes) != result["cases_sha256"]:
        raise ValueError("frozen cases changed before Audit")
    cases = json.loads(cases_bytes)["cases"]
    settings, flags = configuration(result["settings"]["model_source"])
    if settings != result["settings"]:
        raise ValueError("native settings changed before Audit")
    sources = {"baseline": {}, "candidate": {}}
    for arm in sources:
        for path, expected in result["source_sha256"][arm].items():
            raw = (
                subprocess.check_output(
                    [
                        "git",
                        "show",
                        f"{result['baseline_ref']}:ci/shared-skills/{path}",
                    ],
                    cwd=ROOT,
                )
                if arm == "baseline"
                else (SKILLS / path).read_bytes()
            )
            if digest(raw) != expected:
                raise ValueError("Skill source changed before bounded Audit")
            sources[arm][path] = raw.decode()
    result["feedback_phases"] = {}
    temporary_path = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="scheduled-native-feedback-", dir="/tmp"
        ) as directory:
            work = Path(directory)
            temporary_path = work
            for arm in ("baseline", "candidate"):
                original = [case["decision"] for case in result["arms"][arm]["cases"]]
                audit = run_arm(
                    arm,
                    sources[arm],
                    cases,
                    flags,
                    work,
                    phase="audit",
                    previous=original,
                )
                phases = {"audit": audit}
                result["feedback_phases"][arm] = phases
                if (
                    audit["status"] == "completed"
                    and audit["audit"]["verdict"] == "revise"
                ):
                    phases["correction"] = run_arm(
                        arm,
                        sources[arm],
                        cases,
                        flags,
                        work,
                        phase="correction",
                        previous=original,
                        feedback=audit["audit"],
                    )
                final = phases.get("correction", result["arms"][arm])
                phases["post_feedback_common_pass_count"] = final.get(
                    "common_pass_count"
                )
                phases["post_feedback_migration_checks_pass"] = final.get(
                    "migration_checks_pass"
                )
                phases["post_feedback_status"] = (
                    final["status"] if audit["status"] == "completed" else "blocked"
                )
                result_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
    except Exception as error:
        result["feedback_runner_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
    result["feedback_temporary_artifacts_cleaned"] = (
        temporary_path is None or not temporary_path.exists()
    )
    result["post_feedback_status"] = (
        "passed"
        if len(result["feedback_phases"]) == 2
        and all(
            phase.get("post_feedback_status") == "passed"
            for phase in result["feedback_phases"].values()
        )
        and "feedback_runner_error" not in result
        else "failed"
    )
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "original_pair_status": result["status"],
                "post_feedback_status": result["post_feedback_status"],
                "arms": {
                    arm: {
                        key: value
                        for key, value in phase.items()
                        if key.startswith("post_feedback_")
                    }
                    for arm, phase in result["feedback_phases"].items()
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if result["post_feedback_status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-feedback-only", action="store_true")
    parser.add_argument(
        "--model-source",
        choices=("native-default", "service-config", "production-config"),
        default="native-default",
    )
    args = parser.parse_args()
    if args.audit_feedback_only:
        return run_feedback()
    cases_bytes = (HERE / "cases.json").read_bytes()
    cases = json.loads(cases_bytes)["cases"]
    baseline_ref = subprocess.check_output(
        ["git", "rev-parse", BASELINE_COMMIT], cwd=ROOT, text=True
    ).strip()
    paths = sorted({path for case in cases for path in case["skills"]})
    sources = {"baseline": {}, "candidate": {}}
    for path in paths:
        sources["baseline"][path] = subprocess.check_output(
            ["git", "show", f"{baseline_ref}:ci/shared-skills/{path}"], cwd=ROOT
        ).decode()
        sources["candidate"][path] = (SKILLS / path).read_text()
    settings, flags = configuration(args.model_source)
    result = {
        "version": 1,
        "runner_revision": RUNNER_REVISION,
        "annotation_contract_version": 2,
        "created_at": datetime.now(UTC).isoformat(),
        "baseline_ref": baseline_ref,
        "candidate_git_ref": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "cases_sha256": digest(cases_bytes),
        "case_count": len(cases),
        "synthetic_only": True,
        "runtime": "native codex exec",
        "cli_version": subprocess.check_output(
            ["codex", "--version"], text=True
        ).strip(),
        "settings": settings,
        "source_sha256": {
            arm: {path: digest(content.encode()) for path, content in docs.items()}
            for arm, docs in sources.items()
        },
        "arms": {},
    }
    prior_path = HERE / "results.json"
    if prior_path.exists():
        prior = json.loads(prior_path.read_text())
        result["attempt_history"] = [
            *prior.get("attempt_history", []),
            {
                "created_at": prior.get("created_at"),
                "runner_revision": prior.get("runner_revision", 1),
                "post_feedback_status": prior.get("post_feedback_status"),
                "feedback_phases": prior.get("feedback_phases"),
                "feedback_setup_attempts": prior.get("feedback_setup_attempts"),
                "settings": prior.get("settings"),
                "baseline_ref": prior.get("baseline_ref"),
                "candidate_git_ref": prior.get("candidate_git_ref"),
                "cases_sha256": prior.get("cases_sha256"),
                "source_sha256": prior.get("source_sha256"),
                "status": prior.get("status"),
                "arms": {
                    arm: {
                        **{
                            key: value
                            for key, value in data.items()
                            if key
                            in {
                                "status",
                                "exit_code",
                                "blocker",
                                "common_pass_count",
                                "migration_checks_pass",
                            }
                        },
                        "case_outcomes": [
                            {
                                "case_id": case["case_id"],
                                "common_pass": case["common_pass"],
                                "migration_pass": case["migration_pass"],
                                "disposition": case["decision"]["disposition"],
                                "requires_service_publishable": case["decision"][
                                    "requires_service_publishable"
                                ],
                                "failed_checks": [
                                    name
                                    for name, passed in {
                                        **case["common_checks"],
                                        **case["migration_checks"],
                                    }.items()
                                    if not passed
                                ],
                                "failed_decision": case["decision"]
                                if not (case["common_pass"] and case["migration_pass"])
                                else None,
                                "failure_notes": case["decision"]["notes"]
                                if not (case["common_pass"] and case["migration_pass"])
                                else None,
                            }
                            for case in data.get("cases", [])
                        ],
                    }
                    for arm, data in prior.get("arms", {}).items()
                },
            },
        ]
    temporary_path = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="scheduled-native-eval-", dir="/tmp"
        ) as directory:
            work = Path(directory)
            temporary_path = work
            for arm in ("baseline", "candidate"):
                if configuration(args.model_source)[0] != settings:
                    raise ValueError(
                        "native model/MCP configuration changed between comparison arms"
                    )
                result["arms"][arm] = run_arm(arm, sources[arm], cases, flags, work)
                (HERE / "results.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
    except Exception as error:
        result["runner_error"] = {"type": type(error).__name__, "message": str(error)}
    result["temporary_artifacts_cleaned"] = (
        temporary_path is None or not temporary_path.exists()
    )
    result["status"] = (
        "passed"
        if len(result["arms"]) == 2
        and all(arm["status"] == "passed" for arm in result["arms"].values())
        and "runner_error" not in result
        else "blocked"
        if "runner_error" in result
        or any(arm["status"] == "blocked" for arm in result["arms"].values())
        else "failed"
    )
    (HERE / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "arms": {
                    key: {
                        k: v
                        for k, v in arm.items()
                        if k
                        in {
                            "status",
                            "common_pass_count",
                            "migration_checks_pass",
                            "blocker",
                            "exit_code",
                        }
                    }
                    for key, arm in result["arms"].items()
                },
                "results": str(HERE / "results.json"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
