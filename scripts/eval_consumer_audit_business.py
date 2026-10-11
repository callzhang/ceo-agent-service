#!/usr/bin/env python3
"""Run a frozen synthetic business-judgment corpus through native Codex roles.

The corpus is independent of historical recordings and the 20 contract probes.
Both Git trees use their own production role instructions and wire schemas; the
same native model, CLI flags, inline facts, and scoring code run each case.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import re

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.wechat.codex_safety import ROLE_DISABLED_NATIVE_FEATURES  # noqa: E402 - direct script entry

MANIFEST_PATH = ROOT / "evals/consumer_audit_business/v3.json"
FROZEN_MODELS = {
    "consumer-audit-business.v1": "gpt-6.1-sol",
    "consumer-audit-business.v2": "gpt-5.6-sol",
    "consumer-audit-business.v3": "gpt-5.6-sol",
    "consumer-audit-business.v4": "gpt-5.6-sol",
}
FROZEN_CASES_SHA256 = "966beaf8963f250b383f3e334277f6148f9fbae90a942527dcc214331382b64c"
V4_CASES_SHA256 = "6b06786d5094209f1f6435fa0f09538c60f6b5bb59bf644cce47a179f39e2c80"
V3_HARNESS_CONTRACT = {
    "wire_schema_prompt": "exact-ref-production",
    "lexical_screening": "string-values-only",
    "audit_failed_consumer": "not-applicable",
    "semantic_review": "independent-exact-output-required",
}
V4_HARNESS_CONTRACT = {
    **V3_HARNESS_CONTRACT,
    "audit_injection": "summary-only-canonical-digest",
    "ordinary_tool_receipts": "supplied-inline-synthetic-evidence",
}
PROMPT_SOURCE = r'''
import json
from app.audit_rules import render_audit_rules
from app.consumer_agent import consumer_developer_instructions, audit_developer_instructions
from app.store import AgentRole
from app.agent_wire_contracts import ConsumerAgentWireResult, AuditAgentWireResult
print(json.dumps({
    "consumer": consumer_developer_instructions(render_audit_rules(AgentRole.CONSUMER)),
    "audit": audit_developer_instructions(render_audit_rules(AgentRole.AUDIT)),
    "wire_schemas": {"consumer": ConsumerAgentWireResult.model_json_schema(),
                     "audit": AuditAgentWireResult.model_json_schema()},
}, ensure_ascii=False))
'''
NORMALIZE_SOURCE = r'''
import json, sys
from app.agent_wire_contracts import parse_consumer_agent_wire_result, parse_audit_agent_wire_result
role = sys.argv[1]
raw = json.loads(sys.stdin.read())
event = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(raw)}})
result = (parse_consumer_agent_wire_result(event) if role == "consumer"
          else parse_audit_agent_wire_result(event))
digest = None
if role == "consumer":
    try:
        from app.reviewed_candidates import candidate_digest
    except ImportError:
        pass
    else:
        digest = candidate_digest(result)
print(json.dumps({"result": result.model_dump(mode="json"), "digest": digest}, ensure_ascii=False))
'''
REVIEW_SUBJECT_SOURCE = r'''
import json, sys
from app.agent_contracts import ConsumerAgentResult
from app.reviewed_candidates import candidate_digest
result = ConsumerAgentResult.model_validate(json.loads(sys.stdin.read()))
print(json.dumps({"result": result.model_dump(mode="json"), "digest": candidate_digest(result)}, ensure_ascii=False))
'''
SOURCE_FILES = (
    "app/consumer_agent.py", "app/audit_agent.py", "app/agent_contracts.py",
    "app/agent_wire_contracts.py", "app/audit_rules.py",
    "app/schemas/consumer_agent_result.schema.json",
    "app/schemas/audit_agent_result.schema.json",
    "docs/system-action-contracts.md",
)


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    settings = manifest.get("settings", {})
    version = manifest.get("version")
    expected_count = 15 if version == "consumer-audit-business.v4" else 8
    if version not in FROZEN_MODELS or len(manifest.get("cases", [])) != expected_count:
        raise ValueError(f"frozen business corpus must contain exactly {expected_count} cases")
    if version == "consumer-audit-business.v3" and manifest.get("harness_contract") != V3_HARNESS_CONTRACT:
        raise ValueError("frozen harness contract changed")
    if version == "consumer-audit-business.v4" and manifest.get("harness_contract") != V4_HARNESS_CONTRACT:
        raise ValueError("frozen harness contract changed")
    if settings != {"model": FROZEN_MODELS[version], "reasoning_effort": "high", "concurrency": 1,
                    "timeout_seconds_per_case": 300, "tools": "none", "external_facts": "synthetic-inline-only"}:
        raise ValueError("frozen native model settings changed")
    ids = [case["id"] for case in manifest["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate business case id")
    for case in manifest["cases"]:
        fields = {"id", "trigger", "facts", "skill_excerpt", "expected_consumer",
                  "required_concepts", "forbidden_concepts"}
        extra = {"expected_audit", "audit_override"} if version == "consumer-audit-business.v4" else set()
        if not fields <= set(case) or not set(case) <= fields | extra:
            raise ValueError(f"invalid frozen case fields: {case.get('id')}")
        if "audit_override" in case and (
            case.get("expected_audit") != ["return", "reject"]
            or set(case["audit_override"]) != {"summary"}
            or not isinstance(case["audit_override"]["summary"], str)
        ):
            raise ValueError(f"invalid frozen Audit override: {case.get('id')}")
    canonical_cases = json.dumps(manifest["cases"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode()
    expected_sha = V4_CASES_SHA256 if version == "consumer-audit-business.v4" else FROZEN_CASES_SHA256
    if sha256(canonical_cases).hexdigest() != expected_sha:
        raise ValueError("frozen business cases changed")
    return manifest


def archive_ref(ref: str, destination: Path) -> None:
    result = subprocess.run(["git", "archive", "--format=tar", ref], cwd=ROOT,
                            capture_output=True, check=True, timeout=120)
    with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:") as archive:
        archive.extractall(destination, filter="data")


def source_identity(root: Path, *, archived: bool = False) -> dict:
    digest = sha256()
    for relative in SOURCE_FILES:
        path = root / relative
        digest.update(relative.encode())
        digest.update(path.read_bytes() if path.exists() else b"<absent>")
    if archived:
        return {"head": None, "dirty": None,
                "source_fingerprint_sha256": digest.hexdigest()}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=False).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain", "--", *SOURCE_FILES],
                            cwd=root, capture_output=True, text=True, check=False)
    return {"head": head or None, "dirty": bool(status.stdout.strip()) if head else None,
            "source_fingerprint_sha256": digest.hexdigest()}


def role_instructions(root: Path) -> dict:
    result = subprocess.run([sys.executable, "-c", PROMPT_SOURCE], cwd=root,
                            capture_output=True, text=True, timeout=30, check=True,
                            env={**os.environ, "PYTHONPATH": str(root)})
    instructions = json.loads(result.stdout.strip().splitlines()[-1])
    if not all(isinstance(instructions.get(role), str) and instructions[role]
               for role in ("consumer", "audit")):
        raise ValueError("role instruction extraction failed")
    return instructions


def normalize_role_result(root: Path, role: str, result: dict | None) -> dict:
    if result is None:
        return {"ok": False, "error": "missing JSON result", "result": None, "digest": None}
    completed = subprocess.run([sys.executable, "-c", NORMALIZE_SOURCE, role],
                               cwd=root, input=json.dumps(result, ensure_ascii=False),
                               capture_output=True, text=True, timeout=30, check=False,
                               env={**os.environ, "PYTHONPATH": str(root)})
    if completed.returncode:
        return {"ok": False, "error": "role_wire_contract_invalid", "result": None, "digest": None}
    parsed = json.loads(completed.stdout.strip().splitlines()[-1])
    return {"ok": True, "error": "", **parsed}


def normalize_review_subject(root: Path, result: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", REVIEW_SUBJECT_SOURCE], cwd=root,
        input=json.dumps(result, ensure_ascii=False), capture_output=True, text=True,
        timeout=30, check=False, env={**os.environ, "PYTHONPATH": str(root)},
    )
    if completed.returncode:
        return {"ok": False, "error": "audit_review_subject_invalid", "result": None, "digest": None}
    return {"ok": True, "error": "", **json.loads(completed.stdout.strip().splitlines()[-1])}


def audit_subject(root: Path, case: dict, result: dict, digest: str | None) -> dict:
    if "audit_override" not in case:
        return {"ok": True, "error": "", "result": result, "digest": digest}
    altered = json.loads(json.dumps(result, ensure_ascii=False))
    override = case["audit_override"]
    if "summary" in override:
        altered["summary"] = override["summary"]
    for action_override in override.get("action_overrides", []):
        matches = [
            action for action in (altered.get("proposal") or {}).get("actions", [])
            if action.get("target") == action_override["target_match"]
        ]
        if len(matches) != 1:
            return {"ok": False, "error": "audit_override_target_not_unique", "result": None, "digest": None}
        action = matches[0]
        for field in ("target", "payload"):
            if field in action_override:
                action[field] = action_override[field]
    return normalize_review_subject(root, altered)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def native_mcp_server_names() -> tuple[str, ...]:
    """Inventory the actual CLI home's inherited MCP keys before disabling them."""
    home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    config = home / "config.toml"
    if not config.exists():
        return ()
    payload = tomllib.loads(config.read_text(encoding="utf-8"))
    servers = payload.get("mcp_servers", {})
    if not isinstance(servers, dict) or any(
        re.fullmatch(r"[A-Za-z0-9_-]+", name) is None for name in servers
    ):
        raise ValueError("invalid native MCP server inventory")
    return tuple(sorted(servers))


def native_command(*, model: str, effort: str, developer_instructions: str, workdir: Path) -> list[str]:
    command = ["codex", "exec", "--json", "--disable", "hooks",
            "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
            "--cd", str(workdir), "--model", model,
            "-c", f"model_reasoning_effort={_toml_string(effort)}",
            "-c", f"developer_instructions={_toml_string(developer_instructions)}",
            "-c", 'approval_policy="never"',
            "-c", 'web_search="disabled"']
    for feature in ROLE_DISABLED_NATIVE_FEATURES:
        command.extend(("-c", f"features.{feature}=false"))
    command.extend(("-c", "features.code_mode_host=false",
                    "-c", "features.code_mode_only=true",
                    "-c", 'features.code_mode.excluded_tool_namespaces=["functions"]'))
    for name in native_mcp_server_names():
        command.extend(("-c", f"mcp_servers.{name}.enabled=false"))
    command.append("-")
    return command


def _last_message(stdout: str) -> str:
    messages = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
    return messages[-1] if messages else ""


def _event_error(stdout: str) -> str:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") in {"error", "turn.failed"}:
            value = event.get("message") or event.get("error")
            if isinstance(value, dict):
                value = value.get("code") or value.get("message")
            if value:
                safe = re.sub(
                    r"(?i)\b(token|password|api[_-]?key|authorization|bearer)\b\s*[:=]\s*[^\s,;]+",
                    r"\1=[REDACTED]", str(value)[:1500],
                )
                return safe
    return ""


def _native_tool_item_types(stdout: str) -> list[str]:
    """Identify actual native tool activity without retaining arguments or output."""
    types: set[str] = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") not in {"item.started", "item.updated", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type not in {"reasoning", "agent_message", "error"}:
            types.add(item_type if isinstance(item_type, str) else "unknown")
    return sorted(types)


def run_role(*, command: list[str], prompt: str, timeout: int) -> dict:
    try:
        completed = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                   timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"native model timeout after {timeout}s",
                "tool_item_types": []}
    raw = _last_message(completed.stdout)
    tool_item_types = _native_tool_item_types(completed.stdout)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    valid = completed.returncode == 0 and isinstance(parsed, dict)
    return {"ok": valid and not tool_item_types,
            "exit_code": completed.returncode, "result": parsed,
            "raw": raw[:20000], "tool_item_types": tool_item_types,
            "error": "native_tool_use_violation" if tool_item_types else
            ("" if valid else (_event_error(completed.stdout) or
             f"native_cli_exit_{completed.returncode}_without_json_error_event"))}


def _consumer_prompt(case: dict, schema: dict) -> str:
    return (
        "This is a synthetic, read-only business judgment evaluation. No real provider, "
        "external write, or tool is available. Treat the following inline facts and "
        "business Skill excerpt as the complete verified evidence; do not ask for a tool. "
        "Return one strict Consumer wire JSON object matching your role contract. "
        "Use synthetic IDs exactly. If an external action is appropriate, propose its "
        "typed plan but do not execute it.\n\n"
        f"Business Skill excerpt:\n{case['skill_excerpt']}\n\n"
        f"Trigger:\n{case['trigger']}\n\nVerified facts:\n{case['facts']}\n\n"
        f"Exact production wire schema for this ref:\n{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
    )


def _audit_prompt(case: dict, consumer: dict, candidate_digest: str | None, schema: dict) -> str:
    return (
        "This is a synthetic, read-only business judgment evaluation. Review the whole "
        "Consumer result against the exact inline facts and Skill excerpt. No tool or "
        "external write is available. Return one strict Audit wire JSON object matching "
        "your role contract. A correct candidate may be approved without executing it.\n\n"
        f"Business Skill excerpt:\n{case['skill_excerpt']}\n\n"
        f"Verified facts:\n{case['facts']}\n\n"
        "Proposal revision: 0. Stage index: 0.\n"
        f"Candidate digest: {candidate_digest or '(not used by this version)'}\n"
        f"Consumer result:\n{json.dumps(consumer, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Exact production wire schema for this ref:\n{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
    )


def _string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)


def score_case(case: dict, consumer: dict | None, audit: dict | None) -> dict:
    if consumer is None:
        return {"consumer_ok": False, "consumer_outcome_correct": False,
                "audit_ok": False, "audit_applicable": False, "errors": ["Consumer JSON missing"]}
    # JSON field names describe the transport, not a business decision. In
    # particular needs_human_reason:null cannot count as an escalation.
    text = " ".join(_string_values(consumer)).lower().replace("_", " ")
    errors = []
    outcome_correct = consumer.get("outcome") == case["expected_consumer"]
    if not outcome_correct:
        errors.append(f"Consumer outcome {consumer.get('outcome')} != {case['expected_consumer']}")
    for concept in case["required_concepts"]:
        if concept.lower().replace("_", " ") not in text:
            errors.append(f"missing concept: {concept}")
    for concept in case["forbidden_concepts"]:
        if concept.lower().replace("_", " ") in text:
            errors.append(f"forbidden concept: {concept}")
    consumer_ok = not errors
    if consumer.get("outcome") == "failed" and outcome_correct:
        return {"consumer_ok": consumer_ok, "consumer_outcome_correct": True,
                "audit_ok": audit is None, "audit_applicable": False, "errors": errors}
    audit_outcome = audit.get("outcome") if audit else None
    # Lexical coverage is a screening diagnostic, not authority for an Audit
    # decision. A valid candidate can be correctly approved in another language.
    allowed = (set(case["expected_audit"]) if "expected_audit" in case else
               ({"approve", "executed"} if outcome_correct else {"return", "reject", "feedback_provided"}))
    if outcome_correct and consumer.get("outcome") == "no_action":
        allowed.add("no_action")
    if outcome_correct and consumer.get("outcome") == "needs_human":
        allowed.add("needs_human")
    audit_ok = audit_outcome in allowed
    if not audit_ok:
        errors.append(f"Audit outcome {audit_outcome} not in {sorted(allowed)}")
    return {"consumer_ok": consumer_ok, "consumer_outcome_correct": outcome_correct,
            "audit_ok": audit_ok, "audit_applicable": True, "errors": errors}


def run_suite(root: Path, manifest: dict, *, archived: bool = False) -> dict:
    settings = manifest["settings"]
    instructions = role_instructions(root)
    identity = source_identity(root, archived=archived)
    identity["prompt_sha256"] = {role: sha256(instructions[role].encode()).hexdigest()
                                 for role in ("consumer", "audit")}
    identity["wire_schema_sha256"] = {
        role: sha256(json.dumps(instructions["wire_schemas"][role], sort_keys=True).encode()).hexdigest()
        for role in ("consumer", "audit")
    }
    identity["schema_sha256"] = {role: sha256((root / f"app/schemas/{role}_agent_result.schema.json").read_bytes()).hexdigest()
                                  for role in ("consumer", "audit")}
    cases = []
    with tempfile.TemporaryDirectory(prefix="native-business-eval-") as raw:
        workdir = Path(raw)
        for case in manifest["cases"]:  # deliberately serial: concurrency = 1
            consumer = run_role(
                command=native_command(model=settings["model"], effort=settings["reasoning_effort"],
                                       developer_instructions=instructions["consumer"], workdir=workdir),
                prompt=_consumer_prompt(case, instructions["wire_schemas"]["consumer"]), timeout=settings["timeout_seconds_per_case"],
            )
            normalized_consumer = (
                normalize_role_result(root, "consumer", consumer["result"])
                if consumer["ok"] else
                {"ok": False, "error": consumer["error"], "result": None, "digest": None}
            )
            audit_applicable = normalized_consumer["ok"] and normalized_consumer["result"]["outcome"] != "failed"
            subject = (audit_subject(root, case, normalized_consumer["result"], normalized_consumer["digest"])
                       if audit_applicable else normalized_consumer)
            audit_applicable = audit_applicable and subject["ok"]
            audit = run_role(
                command=native_command(model=settings["model"], effort=settings["reasoning_effort"],
                                       developer_instructions=instructions["audit"], workdir=workdir),
                prompt=_audit_prompt(case, subject["result"], subject["digest"], instructions["wire_schemas"]["audit"]),
                timeout=settings["timeout_seconds_per_case"],
            ) if audit_applicable else {"ok": False, "error": "Audit not applicable to failed or invalid Consumer", "result": None}
            normalized_audit = (
                normalize_role_result(root, "audit", audit["result"])
                if audit["ok"] else
                {"ok": False, "error": audit["error"], "result": None, "digest": None}
            )
            cases.append({"id": case["id"],
                          "score": score_case(case, normalized_consumer["result"], normalized_audit["result"]),
                          "consumer": consumer, "consumer_contract": normalized_consumer,
                          "audit_subject": subject if "audit_override" in case else None,
                          "audit": audit, "audit_contract": normalized_audit})
    return {"identity": identity, "cases": cases,
            "consumer_passed": sum(row["score"]["consumer_ok"] for row in cases),
            "consumer_outcome_contract_passed": sum(row["score"]["consumer_outcome_correct"] for row in cases),
            "audit_applicable": sum(row["score"]["audit_applicable"] for row in cases),
            "audit_passed": sum(row["score"]["audit_ok"] and row["score"]["audit_applicable"] for row in cases)}


def resolve_commit_ref(ref: str) -> str:
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", resolved):
        raise ValueError("candidate_ref did not resolve to a full commit SHA")
    return resolved


def compare(*, candidate_root: Path | None = None, candidate_ref: str | None = None,
            manifest_path: Path = MANIFEST_PATH) -> dict:
    if candidate_root is not None and candidate_ref is not None:
        raise ValueError("candidate_root and candidate_ref are mutually exclusive")
    manifest = load_manifest(manifest_path)
    resolved_candidate_ref = resolve_commit_ref(candidate_ref) if candidate_ref is not None else None
    with ExitStack() as stack:
        baseline_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="native-business-baseline-")))
        archive_ref(manifest["baseline_ref"], baseline_root)
        baseline = run_suite(baseline_root, manifest, archived=True)
        if resolved_candidate_ref is not None:
            evaluated_candidate_root = Path(stack.enter_context(
                tempfile.TemporaryDirectory(prefix="native-business-candidate-")))
            archive_ref(resolved_candidate_ref, evaluated_candidate_root)
            candidate = run_suite(evaluated_candidate_root, manifest, archived=True)
        else:
            candidate = run_suite((candidate_root or ROOT).resolve(), manifest)
    return {"mode": "native_synthetic_business_judgment", "business_model_evaluation": True,
            "manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
            "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_flags": {
                "native_disabled_features": [*ROLE_DISABLED_NATIVE_FEATURES, "code_mode_host"],
                "code_mode_only": True, "excluded_tool_namespaces": ["functions"],
                "web_search": "disabled", "inherited_mcp_servers": "disabled",
                "tool_events": "fail_any_native_tool_item",
            },
            "baseline_ref": manifest["baseline_ref"], "candidate_ref": resolved_candidate_ref,
            "settings": manifest["settings"],
            "baseline": baseline, "candidate": candidate}


def probe(manifest: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="native-business-probe-") as raw:
        result = run_role(command=native_command(model=manifest["settings"]["model"],
                                                 effort=manifest["settings"]["reasoning_effort"],
                                                 developer_instructions="Return one JSON object only.",
                                                 workdir=Path(raw)),
                          prompt='Return exactly {"probe":"native-business-eval"}.', timeout=60)
    return {"mode": "auth_probe", "ok": result["ok"] and result["result"] == {"probe": "native-business-eval"},
            "exit_code": result.get("exit_code"), "error": result.get("error", "")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    candidate_source = parser.add_mutually_exclusive_group()
    candidate_source.add_argument("--candidate-root", type=Path)
    candidate_source.add_argument("--candidate-ref")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--probe", action="store_true", help="One model/auth call; no comparison")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    report = probe(manifest) if args.probe else compare(candidate_root=args.candidate_root,
                                                        candidate_ref=args.candidate_ref,
                                                        manifest_path=args.manifest)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
