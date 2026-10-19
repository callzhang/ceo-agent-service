"""Run two fixed, tool-disabled native Agent batches and save compact evidence."""

from __future__ import annotations

import argparse
from copy import deepcopy
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
RUNNER_REVISION = 3
DISPOSITIONS = (
    "publish_with_coverage", "await_service_validation", "respond", "no_reply",
    "decline_updated_invitation", "classify_only", "record_source_candidate",
    "pending_source_failure", "await_source_verification",
)
IDENTITY_CONTRACT = (
    "The identity lists project the substantive OUTPUT or proposed action, not all input provenance. "
    "include_ids contains literal fact identities retained as substantive report items, action targets, "
    "issues, or decisive grounding evidence. exclude_ids contains literal identities reviewed but "
    "intentionally omitted from that output, including irrelevant and superseded input. "
    "Explaining an exclusion in notes or counting excluded records in coverage does not retain the "
    "excluded record as substantive output. The include and exclude lists must be disjoint. "
    "unverified_ids identifies claims whose truth is still unverified; it may overlap include_ids "
    "when an unverified claim is explicitly retained as such. Only explicit id/*_id values in the "
    "supplied facts are identities; dates, names, category keys, Skill paths and JSON property names "
    "are not identities. Copy the literal values exactly. Notes cannot replace these typed lists."
)
ANNOTATION_CONTRACT = (
    "This is an evaluation transport contract, shared unchanged by Consumer, Audit and correction. "
    "Skill sources define business policy, not this transport or its labels. "
    + IDENTITY_CONTRACT + " "
    "Use disposition only from these existing labels: " + ", ".join(DISPOSITIONS) + ". "
    "These are workflow meanings, not production wire action/outcome names: respond means prepare "
    "a response proposal, not send it; pending_source_failure means a required source failed and "
    "the business item remains pending; await_source_verification means defer a business decision "
    "until its source is verified. Do not replace these labels with proposal, failed, reply or plan. "
    "publish_with_coverage means prepare the report while disclosing unavailable optional material; "
    "await_service_validation means the supplied workflow still requires service validation. "
    "decline_updated_invitation means propose declining the current updated invitation; classify_only "
    "means classify without performing mail actions; record_source_candidate means preserve an action "
    "candidate without inventing an owner; no_reply means no response is warranted. "
    "For email, set category, important and unsubscribe_candidate_index; unused fields are null. "
    "owner is an actually sourced assignee or null; inferred_owner records unsupported attribution. "
    "requires_service_publishable asks about a service-enforced flag, separate from Skill/Audit review. "
    "ask_principal means asking for missing materials, not proposing a response to a decision request. "
    "applicant_omission means verified missing applicant material, not a connector failure. "
    "external_actions lists only effects actually performed; this offline tool-free exercise performs none. "
    "planned_capabilities may describe future real operations; never invoke them. "
    "Every case returns its annotation under the supplied schema, not the production service schema."
)
BASELINE_COMMIT = "be1ffd86fd4d34e814c2436f3f732d1c07d539e1"
SKILLS = ROOT / "ci/shared-skills"
PROPERTIES = {
    "case_id": {"type": "string"},
    "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
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


def fact_identities(facts: object) -> list[str]:
    """Mechanical identity vocabulary from inputs, without consulting the oracle."""
    result = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key == "id" or key.endswith("_id")) and isinstance(item, str) and item:
                    result.add(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(facts)
    return sorted(result)


def annotation_schema(cases: list[dict]) -> dict:
    schema = deepcopy(SCHEMA)
    variants = []
    for case in cases:
        item = deepcopy(schema["properties"]["decisions"]["items"])
        item["properties"]["case_id"]["enum"] = [case["id"]]
        identities = fact_identities(case["facts"])
        for field in ("include_ids", "exclude_ids", "unverified_ids"):
            if identities:
                item["properties"][field]["items"]["enum"] = identities
            else:
                item["properties"][field]["maxItems"] = 0
        variants.append(item)
    schema["properties"]["decisions"]["items"] = {"anyOf": variants}
    return schema


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
    material = {"skill_source": source, "cases": prompt_cases}
    prompt = (
        "This is an offline synthetic decision exercise. All evidence is supplied; use no tools, "
        "browse no files, send nothing and mutate nothing. Apply the supplied Skill business policy. "
        + ANNOTATION_CONTRACT + "\n\n" + json.dumps(material, ensure_ascii=False)
    )
    response_schema = annotation_schema(cases)
    if phase == "audit":
        prompt = (
            "Independently review the supplied Consumer output against the supplied facts and Skills. "
            "Check relevance, grounding, current identities, unknown ownership, source-read failures "
            "versus applicant omissions, role boundaries and actual publication conditions. "
            "Review the evaluation transport with the SAME contract and schema used by Consumer: "
            + ANNOTATION_CONTRACT + " "
            "Give field-specific feedback for errors and preserve supported decisions. No expected "
            "answers are supplied. Do not introduce business criteria, infer missing evidence or "
            "replace evaluation labels with production wire names. Call no tools and perform no action. "
            "Return approve only when no correction is needed, otherwise revise with precise feedback.\n"
            + json.dumps({**material, "annotation_schema": response_schema,
                          "consumer_decisions": previous}, ensure_ascii=False)
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
    from jsonschema import Draft202012Validator

    format_errors = list(Draft202012Validator(response_schema).iter_errors(response))
    result["output_schema_sha256"] = digest(json.dumps(response_schema, sort_keys=True).encode())
    result["annotation_contract_sha256"] = digest(ANNOTATION_CONTRACT.encode())
    if format_errors:
        result.update(status="failed", raw_response=response,
                      format_errors=[error.message for error in format_errors],
                      common_pass_count=0, migration_checks_pass=False)
        return result
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
        "annotation_contract_version": 3,
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
