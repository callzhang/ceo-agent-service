from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import ValidationError

from app.quality.database import check_database, rehearse_database
from app.quality.evaluator import evaluate_cases, load_cases
from app.quality.migrations import CURRENT_SCHEMA_VERSION
from app.quality.models import QualityCase, QualitySnapshot
from app.quality.release import LocalReleaseManager, SandboxProfile, run_sandbox_canaries
from app.quality.snapshot import build_quality_snapshot, current_commit
from app.store import AutoReplyStore

PASS = 0
QUALITY_FAILURE = 1
CONFIG_ERROR = 2
INFRASTRUCTURE_UNAVAILABLE = 3


class InfrastructureUnavailable(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ceo-quality")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--tier", choices=("pr", "full", "release"), required=True)
    check.add_argument("--format", choices=("text", "json", "junit"), default="text")

    evaluate = subparsers.add_parser("eval")
    evaluate.add_argument("--suite", choices=("protocol", "safety", "semantic"), required=True)
    evaluate.add_argument("--cases", type=Path, default=Path("quality/cases/golden.jsonl"))
    evaluate.add_argument("--live", action="store_true")
    evaluate.add_argument("--commit", default="")
    evaluate.add_argument("--baseline-results", type=Path)
    evaluate.add_argument("--db", type=Path)
    evaluate.add_argument("--format", choices=("text", "json", "junit"), default="text")

    database = subparsers.add_parser("db-check")
    database.add_argument("--mode", choices=("check", "rehearse"), required=True)
    database.add_argument("--db", type=Path, required=True)
    database.add_argument("--backup-dir", type=Path, default=Path("data/backups"))
    database.add_argument("--format", choices=("text", "json", "junit"), default="text")

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--db", type=Path, required=True)
    snapshot.add_argument("--format", choices=("text", "json", "junit"), default="text")

    release = subparsers.add_parser("release-check")
    release.add_argument("--db", type=Path, required=True)
    release.add_argument("--restart", action="store_true")
    release.add_argument("--sandbox-profile", required=True)
    release.add_argument("--commit", default="")
    release.add_argument(
        "--release-root",
        type=Path,
        default=Path(
            os.getenv(
                "CEO_RELEASE_ROOT",
                "~/.local/share/ceo-agent-service",
            )
        ).expanduser(),
    )
    release.add_argument("--format", choices=("text", "json", "junit"), default="text")
    return parser


def _junit(payload: dict[str, Any]) -> str:
    failed = payload.get("status") not in {"passed", "ok"}
    suite = ET.Element("testsuite", name="ceo-quality", tests="1", failures="1" if failed else "0")
    case = ET.SubElement(suite, "testcase", name=str(payload.get("suite") or payload.get("command") or "quality"))
    if failed:
        failure = ET.SubElement(case, "failure", message=str(payload.get("status")))
        failure.text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return ET.tostring(suite, encoding="unicode")


def _emit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif output_format == "junit":
        print(_junit(payload))
    else:
        print(f"{payload.get('status', 'unknown')}: {payload.get('summary', payload.get('command', 'quality'))}")


def _run_gate(tier: str) -> tuple[int, dict[str, Any]]:
    commands = [
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "pyright", "app"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not live",
            "--cov=app",
            "--cov-report=xml:coverage.xml",
            "--cov-fail-under=84",
        ],
        [
            sys.executable,
            "scripts/check_critical_coverage.py",
            "coverage.xml",
            "--threshold",
            "95",
        ],
        [
            sys.executable,
            "-m",
            "app.quality.cli",
            "eval",
            "--suite",
            "protocol",
        ],
        [
            sys.executable,
            "-m",
            "app.quality.cli",
            "eval",
            "--suite",
            "safety",
        ],
    ]
    if tier in {"full", "release"}:
        commands.extend(
            [
                [
                    sys.executable,
                    "-m",
                    "app.quality.cli",
                    "eval",
                    "--suite",
                    "semantic",
                ],
                [sys.executable, "-m", "pip_audit"],
                [sys.executable, "scripts/quality_secret_scan.py"],
            ]
        )
    checks: list[dict[str, Any]] = []
    for command in commands:
        try:
            completed = subprocess.run(command, text=True, capture_output=True)
        except OSError as exc:
            return INFRASTRUCTURE_UNAVAILABLE, {"status": "infrastructure_error", "summary": type(exc).__name__, "checks": checks}
        checks.append({"name": " ".join(command[2:4]), "passed": completed.returncode == 0})
        if completed.returncode != 0:
            return QUALITY_FAILURE, {"status": "failed", "summary": "quality gate failed", "checks": checks}
    return PASS, {"status": "passed", "summary": f"{tier} gate passed", "checks": checks}


def _live_outputs(cases: list[QualityCase], *, expected_commit: str) -> list[QualityCase]:
    if sys.platform != "darwin":
        raise InfrastructureUnavailable("live evaluation requires a trusted Mac")
    if os.getenv("CEO_QUALITY_TRUSTED") != "1":
        raise ValueError("CEO_QUALITY_TRUSTED=1 is required for live evaluation")
    if os.getenv("GITHUB_EVENT_NAME", "") in {"pull_request", "pull_request_target"}:
        raise ValueError("pull request events may not run local authenticated evaluation")
    actual_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not expected_commit or actual_commit != expected_commit:
        raise ValueError("live evaluation commit does not match the checked-out commit")
    command_text = os.getenv("CEO_QUALITY_LIVE_EVAL_COMMAND", "").strip()
    if not command_text:
        raise ValueError("CEO_QUALITY_LIVE_EVAL_COMMAND is required")
    request_lines = [
        case.model_dump_json(exclude={"recorded_output"}) for case in cases
    ]
    try:
        completed = subprocess.run(
            shlex.split(command_text),
            input="\n".join(request_lines) + "\n",
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InfrastructureUnavailable(type(exc).__name__) from exc
    if completed.returncode != 0:
        raise InfrastructureUnavailable("live evaluator returned a non-zero status")
    outputs: dict[str, dict[str, Any]] = {}
    for line_number, raw_line in enumerate(completed.stdout.splitlines(), 1):
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid live evaluator output at line {line_number}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not isinstance(payload.get("recorded_output"), dict):
            raise ValueError(f"invalid live evaluator output at line {line_number}")
        outputs[payload["id"]] = payload["recorded_output"]
    if set(outputs) != {case.id for case in cases}:
        raise ValueError("live evaluator did not return exactly one output per case")
    return [case.model_copy(update={"recorded_output": outputs[case.id]}) for case in cases]


def _baseline_failed_ids(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    failures = payload.get("failures") if isinstance(payload, dict) else None
    if not isinstance(failures, list):
        raise ValueError("baseline result has no failures list")
    return {
        str(failure["case_id"])
        for failure in failures
        if isinstance(failure, dict) and failure.get("case_id")
    }


def _release_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    profile = Path(args.sandbox_profile)
    if not profile.exists():
        profile = Path("quality/sandboxes") / f"{args.sandbox_profile}.json"
    if not profile.exists():
        return CONFIG_ERROR, {"status": "configuration_error", "summary": "sandbox profile is missing"}
    sandbox = SandboxProfile.load(profile)
    repo = Path.cwd()
    head_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    exact_commit = subprocess.run(
        ["git", "rev-parse", "--verify", f"{args.commit or 'HEAD'}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if exact_commit != head_commit:
        return CONFIG_ERROR, {
            "status": "configuration_error",
            "summary": "release commit must equal the checked-out HEAD",
        }
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        return CONFIG_ERROR, {
            "status": "configuration_error",
            "summary": "release requires a clean working tree",
        }
    gate_code, gate_payload = _run_gate("full")
    if gate_code != PASS:
        return gate_code, gate_payload
    rehearsal = rehearse_database(args.db, backup_dir=args.db.parent / "backups")
    if not rehearsal.ok or rehearsal.schema_version != CURRENT_SCHEMA_VERSION:
        return QUALITY_FAILURE, {"status": "failed", "summary": "database rehearsal failed"}
    manager = LocalReleaseManager(repo=repo, release_root=args.release_root)
    release = manager.build(exact_commit)
    previous: Path | None = None
    service_store = AutoReplyStore(args.db)
    before_pid = _launchd_pid()
    if args.restart:
        previous = manager.activate(release)
        restart_code = _restart_launchd(before_pid)
        if restart_code is not None:
            manager.rollback(previous)
            _restart_launchd(0)
            service_store.record_quality_run(
                suite="release", mode="release", commit_sha=exact_commit,
                status="failed", total=1, passed=0, failed=1, score=0.0,
            )
            return restart_code
    after_pid = _launchd_pid() if args.restart else 0
    snapshot = (
        _wait_for_service_quality(commit=exact_commit, pid=after_pid)
        if args.restart
        else build_quality_snapshot(service_store, commit=exact_commit, pid=after_pid)
    )
    canaries = run_sandbox_canaries(sandbox)
    canaries_passed = all(canary.status == "passed" for canary in canaries)
    backlog_clear = bool(
        snapshot is not None
        and snapshot.backlog.get("failed", 0) == 0
        and snapshot.backlog.get("processing", 0) == 0
        and snapshot.backlog.get("unknown", 0) == 0
    )
    passed = (
        backlog_clear
        and (
            not args.restart
            or (
            snapshot is not None
            and after_pid
            and after_pid != before_pid
            and snapshot.ready
            )
        )
    ) and canaries_passed
    if not passed and args.restart:
        manager.rollback(previous)
        _restart_launchd(after_pid)
    service_store.record_quality_run(
        suite="release",
        mode="release",
        commit_sha=exact_commit,
        status="passed" if passed else "failed",
        total=1 + len(canaries),
        passed=(1 + len(canaries)) if passed else 0,
        failed=0 if passed else 1,
        score=1.0 if passed else 0.0,
    )
    payload = {
        "status": "passed" if passed else "failed",
        "summary": "release checks passed" if passed else "release verification failed and rollback was attempted",
        "commit": exact_commit,
        "schema_version": snapshot.schema_version if snapshot is not None else 0,
        "pid_changed": bool(after_pid and after_pid != before_pid) if args.restart else False,
        "snapshot": snapshot.model_dump(mode="json") if snapshot is not None else {},
        "canaries": [canary.model_dump(mode="json") for canary in canaries],
    }
    return (PASS if passed else QUALITY_FAILURE), payload


def _launchd_label() -> str:
    user_id = subprocess.run(
        ["id", "-u"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return f"gui/{user_id}/com.ceo-agent-service.main"


def _launchd_pid() -> int:
    completed = subprocess.run(
        ["launchctl", "print", _launchd_label()], capture_output=True, text=True
    )
    if completed.returncode != 0:
        return 0
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("pid ="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def _restart_launchd(previous_pid: int) -> tuple[int, dict[str, Any]] | None:
    restarted = subprocess.run(
        ["launchctl", "kickstart", "-k", _launchd_label()],
        capture_output=True,
        text=True,
    )
    if restarted.returncode != 0:
        return INFRASTRUCTURE_UNAVAILABLE, {
            "status": "infrastructure_error",
            "summary": "launchd restart failed",
        }
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current_pid = _launchd_pid()
        if current_pid and current_pid != previous_pid:
            return None
        time.sleep(1)
    return QUALITY_FAILURE, {
        "status": "failed",
        "summary": "service PID did not change",
    }


def _wait_for_service_quality(
    *, commit: str, pid: int, timeout_seconds: int = 180
) -> QualitySnapshot | None:
    url = os.getenv("CEO_QUALITY_URL", "http://127.0.0.1:8765/api/quality")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                payload = json.loads(response.read())
            snapshot = QualitySnapshot.model_validate(payload)
        except (OSError, URLError, json.JSONDecodeError, ValidationError):
            time.sleep(2)
            continue
        if (
            snapshot.commit == commit
            and snapshot.pid == pid
            and snapshot.schema_version == CURRENT_SCHEMA_VERSION
            and snapshot.ready
        ):
            return snapshot
        time.sleep(2)
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            code, payload = _run_gate(args.tier)
        elif args.command == "eval":
            if not args.cases.exists():
                raise FileNotFoundError(args.cases)
            cases = load_cases(args.cases)
            if args.live:
                cases = _live_outputs(cases, expected_commit=args.commit)
            result = evaluate_cases(cases, suite=args.suite)
            payload = result.model_dump(mode="json")
            current_failed = {failure.case_id for failure in result.failures}
            regressions = sorted(current_failed - _baseline_failed_ids(args.baseline_results))
            if regressions:
                payload["regressions"] = regressions
            code = PASS if result.status == "passed" and not regressions else QUALITY_FAILURE
            if args.db is not None:
                AutoReplyStore(args.db).record_quality_run(
                    suite=result.suite,
                    mode="live" if args.live else "recorded",
                    commit_sha=current_commit(),
                    status="passed" if code == PASS else "failed",
                    total=result.total,
                    passed=result.passed,
                    failed=result.failed,
                    score=result.score,
                )
        elif args.command == "db-check":
            result = check_database(args.db) if args.mode == "check" else rehearse_database(args.db, backup_dir=args.backup_dir)
            payload = {"command": "db-check", "status": "passed" if result.ok else "failed", "schema_version": result.schema_version, "quick_check": result.quick_check, "foreign_key_violations": result.foreign_key_violations, "reason": result.reason}
            code = PASS if result.ok else QUALITY_FAILURE
        elif args.command == "snapshot":
            store = AutoReplyStore(args.db)
            payload = build_quality_snapshot(store).model_dump(mode="json")
            payload["status"] = "ok"
            code = PASS
        else:
            code, payload = _release_check(args)
    except (FileNotFoundError, ValueError, ValidationError) as exc:
        code = CONFIG_ERROR
        payload = {"status": "configuration_error", "summary": str(exc)}
    except (OSError, sqlite3.DatabaseError, subprocess.SubprocessError) as exc:
        code = INFRASTRUCTURE_UNAVAILABLE
        payload = {"status": "infrastructure_error", "summary": type(exc).__name__}
    except InfrastructureUnavailable as exc:
        code = INFRASTRUCTURE_UNAVAILABLE
        payload = {"status": "infrastructure_error", "summary": str(exc)}
    _emit(payload, args.format)
    return code


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
