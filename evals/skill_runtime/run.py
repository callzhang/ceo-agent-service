# ruff: noqa: E402

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Literal
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    # Direct script execution exposes only this file's directory on sys.path.
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.agent_wire_contracts import (
    parse_audit_agent_wire_result,
    parse_consumer_agent_wire_result,
)
from app.audit_rules import render_audit_rules
from app.business_skills import BUNDLED_BUSINESS_SKILL_NAMES
from app.codex_runner import CodexRunner
from app.consumer_agent import (
    audit_developer_instructions,
    consumer_developer_instructions,
)
from app.process_runner import run_process_with_idle_timeout
from app.store import AgentRole
from tests.support.native_codex_read_fixture import isolate_read_only_fixture_command

DEFAULT_CASES_PATH = Path(__file__).with_name("cases.jsonl")
Outcome = Literal["proposal", "no_action", "needs_human", "failed"]
LiveRole = Literal["consumer", "audit"]


class EvalValidationError(ValueError):
    """The eval corpus is malformed or contains unsafe source material."""


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    context: str = Field(min_length=1)
    expected_business_skills: tuple[str, ...] = Field(min_length=1)
    forbidden_business_skills: tuple[str, ...]
    expected_outcome: Outcome
    required_assertions: tuple[str, ...] = Field(min_length=1)

    @field_validator(
        "expected_business_skills",
        "forbidden_business_skills",
        "required_assertions",
        mode="before",
    )
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        if not _is_slug(value):
            raise ValueError("case_id must be a lowercase ASCII slug")
        return value

    @field_validator("expected_business_skills", "forbidden_business_skills")
    @classmethod
    def validate_business_skills(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("business Skill names must be unique")
        unknown = set(value).difference(BUNDLED_BUSINESS_SKILL_NAMES)
        if unknown:
            raise ValueError(f"unknown business Skills: {sorted(unknown)}")
        return value

    @field_validator("required_assertions")
    @classmethod
    def validate_assertions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or not all(_is_slug(item, "_") for item in value):
            raise ValueError("required assertions must be unique lowercase ASCII slugs")
        return value


@dataclass(frozen=True)
class ScriptedObservation:
    business_skills: tuple[str, ...]
    outcome: Outcome
    assertions: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    ok: bool
    observed_business_skills: tuple[str, ...]
    observed_outcome: str
    missing_skills: tuple[str, ...]
    forbidden_skills: tuple[str, ...]
    missing_assertions: tuple[str, ...]


@dataclass(frozen=True)
class SuiteReport:
    mode: str
    ok: bool
    results: tuple[CaseResult, ...]

    def to_jsonable(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "ok": self.ok,
            "passed": sum(result.ok for result in self.results),
            "total": len(self.results),
            "results": [asdict(result) for result in self.results],
        }


@dataclass(frozen=True)
class LiveCaseResult:
    case_id: str
    ok: bool
    consumer_skill_reads: tuple[str, ...]
    consumer_skill_read_events: tuple[dict[str, str], ...]
    audit_skill_reads: tuple[str, ...]
    audit_skill_read_events: tuple[dict[str, str], ...]
    consumer_outcome: str
    consumer_result: dict[str, object]
    audit_outcome: str
    audit_result: dict[str, object]
    errors: tuple[str, ...]


def load_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[EvalCase, ...]:
    cases: list[EvalCase] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvalValidationError(f"unable to read eval cases: {path}: {exc}") from exc
    if not lines:
        raise EvalValidationError(f"eval corpus is empty: {path}")
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            raise EvalValidationError(f"blank JSONL row at {path}:{line_number}")
        try:
            payload = json.loads(raw)
            case = EvalCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise EvalValidationError(f"invalid eval row at {path}:{line_number}: {exc}") from exc
        if case.case_id in seen:
            raise EvalValidationError(f"duplicate case_id at {path}:{line_number}: {case.case_id}")
        seen.add(case.case_id)
        _validate_sanitized(case, path=path, line_number=line_number)
        if set(case.expected_business_skills).intersection(case.forbidden_business_skills):
            raise EvalValidationError(
                f"expected and forbidden Skills overlap at {path}:{line_number}"
            )
        cases.append(case)
    return tuple(cases)


def run_scripted(cases: tuple[EvalCase, ...]) -> SuiteReport:
    observations = _scripted_observations()
    results: list[CaseResult] = []
    for case in cases:
        observation = observations.get(case.case_id)
        if observation is None:
            results.append(
                CaseResult(
                    case_id=case.case_id,
                    ok=False,
                    observed_business_skills=(),
                    observed_outcome="missing_fixture",
                    missing_skills=case.expected_business_skills,
                    forbidden_skills=(),
                    missing_assertions=case.required_assertions,
                )
            )
            continue
        missing_skills = tuple(
            skill
            for skill in case.expected_business_skills
            if skill not in observation.business_skills
        )
        forbidden_skills = tuple(
            skill
            for skill in observation.business_skills
            if skill in case.forbidden_business_skills
        )
        missing_assertions = tuple(
            assertion
            for assertion in case.required_assertions
            if assertion not in observation.assertions
        )
        ok = not (
            missing_skills
            or forbidden_skills
            or missing_assertions
            or observation.outcome != case.expected_outcome
        )
        results.append(
            CaseResult(
                case_id=case.case_id,
                ok=ok,
                observed_business_skills=observation.business_skills,
                observed_outcome=observation.outcome,
                missing_skills=missing_skills,
                forbidden_skills=forbidden_skills,
                missing_assertions=missing_assertions,
            )
        )
    return SuiteReport("scripted", all(result.ok for result in results), tuple(results))


def build_live_command(
    case: EvalCase,
    *,
    role: LiveRole,
    workspace: Path,
    config_path: Path,
    log_path: Path,
) -> list[str]:
    if role not in {"consumer", "audit"}:
        raise ValueError(f"unsupported live role: {role}")
    rules_role = AgentRole.CONSUMER if role == "consumer" else AgentRole.AUDIT
    rules = render_audit_rules(rules_role)
    if role == "consumer":
        instructions = consumer_developer_instructions(rules)
    else:
        instructions = audit_developer_instructions(rules) + (
            "\n\n## Eval dry-run\nReview only. Never execute an external write. "
            "Report the Audit outcome under the strict result contract."
        )
    command = CodexRunner(workspace=workspace).build_command(
        prompt="",
        session_id=None,
        use_output_schema=False,
        approval_policy="never",
        developer_instructions=instructions,
        use_approval_bypass=False,
        preserve_native_model_config=True,
    )
    return isolate_read_only_fixture_command(
        command,
        server_command=sys.executable,
        server_args=(
            "-m",
            "tests.support.task5_read_fixture_mcp",
            str(config_path),
            str(log_path),
        ),
        server_cwd=str(ROOT),
    )


def run_live(cases: tuple[EvalCase, ...]) -> tuple[LiveCaseResult, ...]:
    results: list[LiveCaseResult] = []
    for case in cases:
        try:
            results.append(_run_live_case(case))
        except Exception as exc:
            results.append(
                LiveCaseResult(
                    case_id=case.case_id,
                    ok=False,
                    consumer_skill_reads=(),
                    consumer_skill_read_events=(),
                    audit_skill_reads=(),
                    audit_skill_read_events=(),
                    consumer_outcome="failed_to_run",
                    consumer_result={},
                    audit_outcome="failed_to_run",
                    audit_result={},
                    errors=(str(exc),),
                )
            )
    return tuple(results)


def _run_live_case(case: EvalCase) -> LiveCaseResult:
    with tempfile.TemporaryDirectory(prefix=f"skill-runtime-{case.case_id}-") as raw:
        work = Path(raw)
        config_path = work / "fixture.json"
        consumer_log = work / "consumer-events.jsonl"
        audit_log = work / "audit-events.jsonl"
        skill_paths = [
            ROOT / "skills" / name / "SKILL.md"
            for name in BUNDLED_BUSINESS_SKILL_NAMES
        ]
        read_argv = ["fixture-read", case.case_id]
        config_path.write_text(
            json.dumps(
                {
                    "skill_paths": [str(path.resolve()) for path in skill_paths],
                    "operation_responses": [
                        {
                            "argv": read_argv,
                            "stdout": json.dumps(
                                {"trigger": case.trigger, "context": case.context},
                                ensure_ascii=True,
                            ),
                        }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        available = "\n".join(str(path.resolve()) for path in skill_paths)
        consumer_prompt = (
            f"Generalized eval trigger:\n{case.trigger}\n\n"
            f"Generalized eval context:\n{case.context}\n\n"
            f"Available business Skill paths:\n{available}\n\n"
            f"Available exact reviewed read command:\n{json.dumps(read_argv)}\n\n"
            "Read every applicable available business Skill and the fixture evidence. "
            "Perform no external writes. Return only the strict Consumer result; a "
            "proposal describes the one action Audit would review."
        )
        consumer_stdout = _execute_live_command(
            build_live_command(
                case,
                role="consumer",
                workspace=ROOT,
                config_path=config_path,
                log_path=consumer_log,
            ),
            consumer_prompt,
        )
        consumer_raw = _last_agent_message(consumer_stdout)
        consumer = parse_consumer_agent_wire_result(consumer_raw)
        consumer_events = _read_event_log(consumer_log)
        receipts = [
            event["result"]
            for event in consumer_events
            if event.get("tool") == "read_skill"
        ]
        audit_prompt = (
            f"Generalized eval trigger:\n{case.trigger}\n\n"
            f"Generalized eval context:\n{case.context}\n\n"
            f"Consumer strict result:\n{consumer_raw}\n\n"
            f"Verified Consumer Skill receipts:\n{json.dumps(receipts, sort_keys=True)}\n\n"
            f"Available business Skill paths:\n{available}\n\n"
            f"Available exact reviewed read command:\n{json.dumps(read_argv)}\n\n"
            "This is an Audit dry-run. Independently reread applicable Skills and "
            "fixture evidence, review the Consumer result, execute nothing, and return "
            "only the strict Audit result."
        )
        audit_stdout = _execute_live_command(
            build_live_command(
                case,
                role="audit",
                workspace=ROOT,
                config_path=config_path,
                log_path=audit_log,
            ),
            audit_prompt,
        )
        audit_raw = _last_agent_message(audit_stdout)
        audit = parse_audit_agent_wire_result(audit_raw)
        audit_events = _read_event_log(audit_log)
        consumer_skills = _observed_skill_names(consumer_events)
        audit_skills = _observed_skill_names(audit_events)
        consumer_skill_events = _skill_read_events(consumer_events)
        audit_skill_events = _skill_read_events(audit_events)
        expected = set(case.expected_business_skills)
        errors: list[str] = []
        if set(consumer_skills) != expected:
            errors.append("Consumer Skill reads did not match expected business Skills")
        if set(audit_skills) != expected:
            errors.append("Audit Skill reads did not match expected business Skills")
        if consumer.outcome.value != case.expected_outcome:
            errors.append("Consumer outcome did not match expected outcome")
        return LiveCaseResult(
            case_id=case.case_id,
            ok=not errors,
            consumer_skill_reads=consumer_skills,
            consumer_skill_read_events=consumer_skill_events,
            audit_skill_reads=audit_skills,
            audit_skill_read_events=audit_skill_events,
            consumer_outcome=consumer.outcome.value,
            consumer_result=consumer.model_dump(mode="json"),
            audit_outcome=audit.outcome.value,
            audit_result=audit.model_dump(mode="json"),
            errors=tuple(errors),
        )


def _execute_live_command(command: list[str], prompt: str) -> str:
    process = run_process_with_idle_timeout(
        command,
        prompt=prompt,
        env={"PATH": os.environ.get("PATH", "")},
        total_timeout_seconds=300,
        idle_timeout_seconds=120,
        on_stdout_line=lambda _line: None,
    )
    if process.returncode != 0:
        raise RuntimeError(f"Codex exec failed: {process.stderr.strip()}")
    return process.stdout


def _last_agent_message(stdout: str) -> str:
    messages: list[str] = []
    for raw in stdout.splitlines():
        record = json.loads(raw)
        item = record.get("item")
        if (
            record.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            messages.append(item["text"])
    if not messages:
        raise RuntimeError("Codex exec returned no agent result")
    return messages[-1]


def _read_event_log(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _observed_skill_names(events: list[dict[str, object]]) -> tuple[str, ...]:
    return tuple(
        str(event["result"]["name"])
        for event in events
        if event.get("tool") == "read_skill" and isinstance(event.get("result"), dict)
    )


def _skill_read_events(
    events: list[dict[str, object]],
) -> tuple[dict[str, str], ...]:
    evidence: list[dict[str, str]] = []
    for event in events:
        result = event.get("result")
        if event.get("tool") != "read_skill" or not isinstance(result, dict):
            continue
        evidence.append(
            {
                key: str(result[key])
                for key in ("name", "path", "sha256")
                if key in result
            }
        )
    return tuple(evidence)


def _scripted_observations() -> dict[str, ScriptedObservation]:
    return {
        "calendar-vague-invite-clarify-inviter": ScriptedObservation(
            ("ceo-calendar-invite",),
            "proposal",
            ("inviter_receives_one_factual_question", "no_principal_decision_options"),
        ),
        "calendar-clear-context-accept": ScriptedObservation(
            ("ceo-calendar-invite",),
            "proposal",
            ("invitation_context_read", "accept_without_clarification"),
        ),
        "document-readable-reference-read": ScriptedObservation(
            ("ceo-document-review",),
            "proposal",
            ("referenced_document_read", "no_paste_request"),
        ),
        "document-image-inspect-before-judgment": ScriptedObservation(
            ("ceo-document-review",),
            "proposal",
            ("image_content_inspected", "judgment_after_image_read"),
        ),
        "mail-truncated-card-resolve-thread": ScriptedObservation(
            ("ceo-mail-review",),
            "proposal",
            ("full_mail_thread_read", "truncated_preview_not_used_as_content"),
        ),
        "personnel-unrelated-recipient-protect": ScriptedObservation(
            ("ceo-personnel-communication",),
            "no_action",
            (
                "recipient_authorization_checked",
                "sensitive_details_not_disclosed",
                "unsupported_personnel_facts_not_invented",
            ),
        ),
        "tracking-participant-is-not-owner": ScriptedObservation(
            ("ceo-work-tracking",),
            "no_action",
            ("participation_not_owner_evidence", "no_follow_up_created"),
        ),
        "tracking-completed-todo-suppress": ScriptedObservation(
            ("ceo-work-tracking",),
            "no_action",
            ("todo_completion_verified", "no_duplicate_follow_up"),
        ),
        "oa-factual-gap-clarify-applicant": ScriptedObservation(
            ("ceo-message-triage",),
            "proposal",
            ("applicant_receives_one_factual_question", "no_principal_decision_options"),
        ),
        "meeting-mentions-adjacent-to-actions": ScriptedObservation(
            ("ceo-meeting-work",),
            "proposal",
            ("each_mention_adjacent_to_action", "no_mention_wall"),
        ),
    }


def _validate_sanitized(case: EvalCase, *, path: Path, line_number: int) -> None:
    values = (case.trigger, case.context, *case.required_assertions)
    for value in values:
        issue = _sanitization_issue(value)
        if issue:
            raise EvalValidationError(
                f"sanitization failure at {path}:{line_number}: {issue}"
            )


def _sanitization_issue(value: str) -> str:
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return "only printable ASCII generalized text is allowed"
    lowered = value.casefold()
    normalized = lowered.replace("-", "_").replace(" ", "")
    for label in ("user_id=", "userid=", "token=", "session_id=", "sessionid="):
        if label in normalized:
            return "identifier or credential field is not allowed"
    if "@" in value:
        return "mentions and email-like identities are not allowed"
    for chunk in value.split():
        candidate = chunk.strip(".,;:()[]{}<>'\"")
        parsed = urlsplit(candidate)
        if parsed.scheme or parsed.netloc:
            return "links are not allowed in the sanitized corpus"
        compact = "".join(char for char in candidate if char.isalnum())
        if (
            len(compact) >= 24
            and any(char.isalpha() for char in compact)
            and any(char.isdigit() for char in compact)
            and _entropy(compact) >= 3.5
        ):
            return "opaque high-entropy identifier is not allowed"
    return ""


def _entropy(value: str) -> float:
    counts = {char: value.count(char) for char in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _is_slug(value: str, extra: str = "-") -> bool:
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789" + extra)
    return bool(value) and value[0].isalnum() and value[-1].isalnum() and set(value) <= allowed


def _print_scripted(report: SuiteReport) -> None:
    print(f"scripted: {sum(result.ok for result in report.results)}/{len(report.results)} passed")
    for result in report.results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.case_id}: {result.observed_outcome}")
    print(json.dumps(report.to_jsonable(), indent=2, sort_keys=True))


def _print_live(results: tuple[LiveCaseResult, ...]) -> None:
    passed = sum(result.ok for result in results)
    print(f"live: {passed}/{len(results)} passed")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(
            f"[{status}] {result.case_id}: Consumer={result.consumer_outcome} "
            f"Audit={result.audit_outcome}"
        )
    print(
        json.dumps(
            {
                "mode": "live",
                "ok": all(result.ok for result in results),
                "passed": passed,
                "total": len(results),
                "results": [asdict(result) for result in results],
            },
            indent=2,
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Skill-runtime regression cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument(
        "--live",
        action="store_true",
        help="opt in to isolated native Codex Consumer and dry-run Audit evidence",
    )
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        if args.live:
            live_results = run_live(cases)
            _print_live(live_results)
            return 0 if all(result.ok for result in live_results) else 1
        report = run_scripted(cases)
        _print_scripted(report)
        return 0 if report.ok else 1
    except EvalValidationError as exc:
        print(json.dumps({"mode": "validation", "ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
