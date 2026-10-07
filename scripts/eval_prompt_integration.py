#!/usr/bin/env python3
"""Frozen tool-free native Consumer/Audit prompt integration comparison.

Preparation archives immutable refs and renders the actual service's semantic
inputs. Native sessions have no business/source tools. Contract screening is
separate from blinded independent semantic review; size never earns a pass.
"""

from __future__ import annotations
import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.eval_consumer_audit_business import (  # noqa: E402 - direct script entry
    archive_ref,
    native_command,
    run_role,
    normalize_role_result,
    resolve_commit_ref,
)

MANIFEST = ROOT / "evals/prompt_integration/cases.v2.json"
ARTIFACT_ROOT = Path(
    "/Users/derek/Documents/memory/ceo-agent-service/prompt-integration-20261006/evaluation"
)
CORPUS_SHA = "5041b0dfb2656fceea617ffedbae2913592878c7158917112937f0b5a89edea2"
BASELINE = "0d6bf42eb2922ed57a6bc6de8b836f5a443106c3"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def load_manifest():
    manifest = json.loads(MANIFEST.read_text())
    if (
        manifest["version"] != "prompt-integration.v2"
        or manifest["baseline_ref"] != BASELINE
    ):
        raise ValueError("frozen version/ref changed")
    if len(manifest["cases"]) != 20 or fingerprint(manifest["cases"]) != CORPUS_SHA:
        raise ValueError("frozen 20-case corpus changed")
    expected = {
        "route_name": "codex_oauth",
        "runtime_kind": "codex_cli",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "concurrency": 1,
        "timeout_seconds": 300,
        "repetitions": 1,
        "fixed_time": "2026-10-06T09:00:00-07:00",
        "tools": "none",
        "profile": "identical-synthetic-short",
    }
    if manifest["settings"] != expected:
        raise ValueError("frozen runtime settings changed")
    for case in manifest["cases"]:
        if (
            fingerprint(case["audit_subject"]["source_bindings"])
            != case["source_binding_sha256"]
        ):
            raise ValueError("complete source binding changed")
    return manifest


def extract(root, manifest, arm, *, profile_path=None):
    fixture = ROOT / "evals/prompt_integration"
    profile = Path(profile_path) if profile_path is not None else fixture / "profile.md"
    # Source ref selects default template semantics, never mutable live files.
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "USER_ALIAS": "负责人",
        "CEO_WORK_PROFILE_PATH": str(profile),
        "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH": str(
            root / "app/defaults/developer_prompt.md"
        ),
        "CEO_USER_PROMPT_TEMPLATE_PATH": str(root / "app/defaults/user_prompt.md"),
        "CEO_AUDIT_RULES_TEMPLATE_PATH": str(root / "app/defaults/audit_rules.md"),
        "CEO_WORKSPACE": "/synthetic/evaluation/workspace",
    }
    completed = subprocess.run(
        [sys.executable, "-c", (fixture / "assemble.py").read_text()],
        cwd=root,
        env=env,
        input=json.dumps(
            {
                "manifest": manifest,
                "arm": arm,
                "fixture_profile_sha256": sha256(profile.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def sizes(rows):
    result = {}
    for role in ("consumer", "audit"):
        selected = [r for r in rows if r["role"] == role]
        result[role] = {}
        for name, values in (
            ("static_developer_chars", [r["static_developer_chars"] for r in selected]),
            ("developer_chars", [len(r["developer"]) for r in selected]),
            ("task_chars", [len(r["task"]) for r in selected]),
            ("total_chars", [len(r["developer"]) + len(r["task"]) for r in selected]),
        ):
            result[role][name] = {
                "min": min(values),
                "mean": sum(values) / len(values),
                "max": max(values),
            }
    return result


def screen(case, row, native, normalized):
    result = normalized.get("result")
    errors = []
    if not normalized["ok"]:
        errors.append(normalized["error"])
    elif row["role"] == "consumer":
        if result["outcome"] not in case["expected_consumer_outcomes"]:
            errors.append("unexpected_consumer_outcome")
        for field in ("stage_index", "predecessor_review_id"):
            if result.get(field) != row[field]:
                errors.append("consumer_" + field + "_mismatch")
    else:
        if result["outcome"] not in case["expected_audit_outcomes"]:
            errors.append("unexpected_audit_outcome")
        for field in ("candidate_digest", "proposal_revision"):
            if result[field] != row[field]:
                errors.append("audit_" + field + "_mismatch")
    if native.get("tool_item_types"):
        errors.append("native_tool_use_violation")
    return {
        "contract_and_binding_screen_passed": not errors,
        "errors": errors,
        "semantic_review": "pending-independent-review",
        "quality_pass": None,
    }


def usage_from_stdout(stdout):
    values = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed" and "usage" in event:
            values.append(event["usage"])
    return values


def run_native(row, settings, workdir):
    from time import monotonic
    from scripts.eval_consumer_audit_business import (  # noqa: E402 - direct script entry
        _last_message,
        _native_tool_item_types,
        _event_error,
    )

    command = native_command(
        model=settings["model"],
        effort=settings["reasoning_effort"],
        developer_instructions=row["developer"],
        workdir=workdir,
    )
    started = monotonic()
    try:
        completed = subprocess.run(
            command,
            input=row["task"],
            text=True,
            capture_output=True,
            timeout=settings["timeout_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "ok": False,
            "error": "native_timeout",
            "result": None,
            "raw": _last_message(stdout),
            "tool_item_types": _native_tool_item_types(stdout),
            "elapsed_seconds": monotonic() - started,
            "usage": usage_from_stdout(stdout),
            "exit_code": None,
            "native_events": stdout,
            "stderr": stderr,
        }
    raw = _last_message(completed.stdout)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    tool_types = _native_tool_item_types(completed.stdout)
    valid = completed.returncode == 0 and isinstance(parsed, dict) and not tool_types
    return {
        "ok": valid,
        "error": ""
        if valid
        else (
            _event_error(completed.stdout)
            or ("native_tool_use_violation" if tool_types else "missing_valid_json")
        ),
        "result": parsed,
        "raw": raw,
        "tool_item_types": tool_types,
        "elapsed_seconds": monotonic() - started,
        "usage": usage_from_stdout(completed.stdout),
        "exit_code": completed.returncode,
        "native_events": completed.stdout,
        "stderr": completed.stderr,
    }


def save(output, report):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-ref")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--cases", help="Comma-separated frozen IDs; default all 20")
    parser.add_argument(
        "--arms", choices=("baseline", "candidate", "both"), default="both"
    )
    parser.add_argument("--artifact-root", type=Path, default=ARTIFACT_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output is None:
        mode = "probe" if args.probe else ("native" if args.run else "size")
        args.output = args.artifact_root / f"{args.arms}-{mode}.v2.json"
    elif not args.output.is_absolute():
        args.output = args.artifact_root / args.output
    manifest = load_manifest()
    settings = manifest["settings"]
    if args.probe:
        with TemporaryDirectory(prefix="prompt-integration-probe-") as raw:
            result = run_role(
                command=native_command(
                    model=settings["model"],
                    effort=settings["reasoning_effort"],
                    developer_instructions="Return one JSON object only.",
                    workdir=Path(raw),
                ),
                prompt='Return exactly {"probe":"prompt-integration"}.',
                timeout=60,
            )
        report = {"mode": "probe", "settings": settings, "result": result}
        save(args.output, report)
        print(canonical({"output": str(args.output), "ok": result["ok"]}))
        return 0 if result["ok"] else 1
    arms = ["baseline", "candidate"] if args.arms == "both" else [args.arms]
    if "candidate" in arms and not args.candidate_ref:
        parser.error("--candidate-ref is required for immutable candidate comparison")
    selected = (
        set(args.cases.split(","))
        if args.cases
        else {c["id"] for c in manifest["cases"]}
    )
    if selected - {c["id"] for c in manifest["cases"]}:
        parser.error("unknown frozen case ID")
    refs = {"baseline": BASELINE}
    if "candidate" in arms:
        refs["candidate"] = resolve_commit_ref(args.candidate_ref)
    report = {
        "mode": "native_synthetic_tool_free" if args.run else "prompt_size_preparation",
        "manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
        "cases_sha256": CORPUS_SHA,
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "assembler_sha256": sha256(
            (ROOT / "evals/prompt_integration/assemble.py").read_bytes()
        ).hexdigest(),
        "refs": refs,
        "settings": settings,
        "category_counts": dict(Counter(c["category"] for c in manifest["cases"])),
        "scope": "Synthetic inline facts, not production business results; first-turn only, no native history.",
        "tokenizer": "not measured; characters are not tokens",
        "arms": {},
        "completed": False,
    }
    for arm in arms:
        with TemporaryDirectory(prefix="prompt-integration-" + arm + "-") as raw:
            root = Path(raw) / "source"
            root.mkdir()
            archive_ref(refs[arm], root)
            rows = extract(root, manifest, arm)
            report["arms"][arm] = {
                "size_summary": sizes(rows),
                "inputs": [],
                "results": [],
            }
            for row in rows:
                row.update(
                    developer_sha256=sha256(row["developer"].encode()).hexdigest(),
                    task_sha256=sha256(row["task"].encode()).hexdigest(),
                )
                report["arms"][arm]["inputs"].append(row)
            save(args.output, report)
            if args.run:
                workdir = Path(raw) / "native"
                workdir.mkdir()
                for row in rows:
                    if row["case_id"] not in selected:
                        continue
                    case = next(
                        c for c in manifest["cases"] if c["id"] == row["case_id"]
                    )
                    native = run_native(row, settings, workdir)
                    normalized = (
                        normalize_role_result(root, row["role"], native["result"])
                        if native["ok"]
                        else {
                            "ok": False,
                            "error": native["error"],
                            "result": None,
                            "digest": None,
                        }
                    )
                    screen_result = screen(case, row, native, normalized)
                    result = {
                        "case_id": row["case_id"],
                        "role": row["role"],
                        "native": native,
                        "normalized": normalized,
                        "screen": screen_result,
                    }
                    report["arms"][arm]["results"].append(result)
                    save(args.output, report)
                    print(
                        canonical(
                            {
                                "arm": arm,
                                "case_id": row["case_id"],
                                "role": row["role"],
                                "screen_passed": screen_result[
                                    "contract_and_binding_screen_passed"
                                ],
                                "native_error": native["error"],
                                "seconds": round(native["elapsed_seconds"], 2),
                            }
                        ),
                        flush=True,
                    )
                    # Capacity/auth/transport failure is missing model evidence: stop,
                    # preserve partial artifact, never replay the whole completed prefix.
                    if not native["ok"] and native["result"] is None:
                        report["stop_reason"] = native["error"]
                        save(args.output, report)
                        return 1
    report["completed"] = True
    save(args.output, report)
    print(
        canonical(
            {
                "output": str(args.output),
                "completed": True,
                "arms": arms,
                "cases": len(selected),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
