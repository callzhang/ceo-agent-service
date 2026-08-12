# ruff: noqa: E402

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Literal
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    # Direct script execution exposes only this file's directory on sys.path.
    sys.path.insert(0, str(ROOT))

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from app.agent_effects import McpToolEffectRegistry
from app.agent_contracts import ProposedAction
from app.agent_result import EffectKind
from app.agent_wire_contracts import (
    parse_audit_agent_wire_result,
    parse_consumer_agent_wire_result,
)
from app.audit_rules import render_audit_rules
from app.audit_agent import _expected_effect_action
from app.business_skills import BUNDLED_BUSINESS_SKILL_NAMES
from app.codex_runner import CodexRunner
from app.consumer_agent import (
    audit_developer_instructions,
    consumer_developer_instructions,
)
from app.process_runner import run_process_with_idle_timeout
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.store import AgentRole
from tests.support.native_codex_read_fixture import isolate_read_only_fixture_command


DEFAULT_CASES_PATH = Path(__file__).with_name("cases.jsonl")
DEFAULT_FIXTURES_PATH = Path(__file__).with_name("fixtures.jsonl")
Outcome = Literal["proposal", "no_action", "needs_human", "failed"]
AuditOutcome = Literal[
    "executed",
    "revision_required",
    "needs_human",
    "failed",
    "unknown",
    "reconciled",
]
LiveRole = Literal["consumer", "audit"]
AssertionSource = Literal[
    "consumer_events",
    "audit_events",
    "consumer_evidence",
    "audit_evidence",
    "consumer_result",
    "audit_result",
]
AssertionOperator = Literal["equals", "contains", "absent", "count_equals"]
_ID_LABEL = re.compile(
    r"\b(?:user|open|union|session|message|conversation|task|attempt)[\s_-]*id\b"
    r"\s*[:=]\s*[a-z0-9][a-z0-9_-]*",
    re.IGNORECASE,
)
_CREDENTIAL_LABEL = re.compile(
    r"\b(?:access[\s_-]*)?(?:token|signature|auth[\s_-]*key|api[\s_-]*key)\b"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_LONG_HEX = re.compile(r"\b[0-9a-f]{20,}\b", re.IGNORECASE)
_SIGNED_QUERY_KEYS = re.compile(
    r"^(?:token|signature|sign|expires|auth|key|api[_-]?key|access[_-]?token)$",
    re.IGNORECASE,
)
_SENSITIVE_STRUCTURED_KEYS = frozenset(
    {
        "userid",
        "openid",
        "unionid",
        "sessionid",
        "messageid",
        "conversationid",
        "taskid",
        "attemptid",
        "token",
        "accesstoken",
        "refreshtoken",
        "authtoken",
        "idtoken",
        "signature",
        "sign",
        "auth",
        "authkey",
        "apikey",
        "clientsecret",
    }
)
_OPAQUE_VALUE_KEYS = frozenset(
    {"caseid", "assertionid", "scenariosha256", "sha256", "resultdigest"}
)


class EvalValidationError(ValueError):
    """The eval corpus or recorded protocol is invalid."""


class AssertionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    assertion_id: str = Field(min_length=1)
    source: AssertionSource
    path: tuple[str | int, ...]
    operator: AssertionOperator
    expected: JsonValue = None

    @field_validator("path", mode="before")
    @classmethod
    def accept_json_path(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("assertion_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _is_slug(value, "_"):
            raise ValueError("assertion_id must be a lowercase ASCII slug")
        return value


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    context: str = Field(min_length=1)
    expected_business_skills: tuple[str, ...] = Field(min_length=1)
    forbidden_business_skills: tuple[str, ...]
    expected_outcome: Outcome
    acceptable_audit_outcomes: tuple[AuditOutcome, ...] = Field(min_length=1)
    required_assertions: tuple[AssertionSpec, ...] = Field(min_length=1)

    @field_validator(
        "expected_business_skills",
        "forbidden_business_skills",
        "acceptable_audit_outcomes",
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
    def validate_assertions(
        cls, value: tuple[AssertionSpec, ...]
    ) -> tuple[AssertionSpec, ...]:
        ids = [item.assertion_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("required assertion IDs must be unique")
        sources = {item.source for item in value}
        required_evidence = {"consumer_evidence", "audit_evidence"}
        if not required_evidence.issubset(sources):
            raise ValueError(
                "required assertions must inspect Consumer and Audit evidence"
            )
        return value


class ProtocolEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    tool: Literal["read_skill", "execute_reviewed_read"]
    arguments: dict[str, JsonValue]
    result: dict[str, JsonValue]


class SkillReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProtocolFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str = Field(min_length=1)
    scenario_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_receipts: tuple[SkillReceipt, ...] = Field(min_length=1)
    consumer_events: tuple[ProtocolEvent, ...] = Field(min_length=1)
    consumer_result: dict[str, JsonValue]
    audit_events: tuple[ProtocolEvent, ...] = Field(min_length=1)
    audit_result: dict[str, JsonValue]

    @field_validator("skill_receipts", "consumer_events", "audit_events", mode="before")
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        if not _is_slug(value):
            raise ValueError("case_id must be a lowercase ASCII slug")
        return value

    @field_validator("skill_receipts")
    @classmethod
    def validate_receipts(
        cls, value: tuple[SkillReceipt, ...]
    ) -> tuple[SkillReceipt, ...]:
        names = [receipt.name for receipt in value]
        if len(names) != len(set(names)):
            raise ValueError("Skill receipt names must be unique")
        return value


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    ok: bool
    observed_business_skills: tuple[str, ...]
    observed_outcome: str
    audit_outcome: str
    missing_skills: tuple[str, ...]
    forbidden_skills: tuple[str, ...]
    missing_assertions: tuple[str, ...]
    consumer_result: dict[str, object]
    audit_result: dict[str, object]
    consumer_events: tuple[dict[str, object], ...]
    audit_events: tuple[dict[str, object], ...]
    errors: tuple[str, ...]


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


def load_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[EvalCase, ...]:
    cases = _load_jsonl(path, EvalCase, "eval case")
    for line_number, case in enumerate(cases, start=1):
        _validate_sanitized(case, path=path, line_number=line_number)
        if set(case.expected_business_skills).intersection(case.forbidden_business_skills):
            raise EvalValidationError(
                f"expected and forbidden Skills overlap at {path}:{line_number}"
            )
    _validate_unique_ids(path, cases)
    return cases


def load_fixtures(path: Path = DEFAULT_FIXTURES_PATH) -> tuple[ProtocolFixture, ...]:
    fixtures = _load_jsonl(path, ProtocolFixture, "protocol fixture")
    for line_number, fixture in enumerate(fixtures, start=1):
        _validate_fixture_sanitized(fixture, path=path, line_number=line_number)
    _validate_unique_ids(path, fixtures)
    return fixtures


def _load_jsonl(path: Path, model: type[BaseModel], label: str):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvalValidationError(f"unable to read {label}s: {path}: {exc}") from exc
    if not lines:
        raise EvalValidationError(f"{label} corpus is empty: {path}")
    items = []
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            raise EvalValidationError(f"blank JSONL row at {path}:{line_number}")
        try:
            items.append(model.model_validate(json.loads(raw)))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise EvalValidationError(
                f"invalid {label} at {path}:{line_number}: {exc}"
            ) from exc
    return tuple(items)


def _validate_unique_ids(path: Path, items: tuple[BaseModel, ...]) -> None:
    seen: set[str] = set()
    for line_number, item in enumerate(items, start=1):
        case_id = str(getattr(item, "case_id"))
        if case_id in seen:
            raise EvalValidationError(
                f"duplicate case_id at {path}:{line_number}: {case_id}"
            )
        seen.add(case_id)


def run_scripted(
    cases: tuple[EvalCase, ...],
    fixtures: tuple[ProtocolFixture, ...] | None = None,
) -> SuiteReport:
    fixture_by_id = {
        fixture.case_id: fixture
        for fixture in (fixtures if fixtures is not None else load_fixtures())
    }
    results = tuple(
        replay_fixture(case, fixture_by_id.get(case.case_id)) for case in cases
    )
    return SuiteReport("recorded_replay", all(result.ok for result in results), results)


def replay_fixture(
    case: EvalCase,
    fixture: ProtocolFixture | None,
) -> CaseResult:
    if fixture is None:
        return _failed_result(case, "recorded protocol fixture is missing")
    errors: list[str] = []
    if fixture.scenario_sha256 != _scenario_digest(case):
        errors.append("recorded scenario digest does not match trigger and context")
    errors.extend(_validate_skill_bindings(fixture))
    errors.extend(_validate_evidence_digests(fixture.consumer_events, "Consumer"))
    errors.extend(_validate_evidence_digests(fixture.audit_events, "Audit"))
    try:
        consumer = parse_consumer_agent_wire_result(
            _wire_jsonl(_consumer_wire(fixture.consumer_result))
        )
        consumer_dump = consumer.model_dump(mode="json")
        if consumer_dump != fixture.consumer_result:
            errors.append("Consumer nested result does not match parsed wire result")
    except Exception as exc:
        return _failed_result(case, f"Consumer result contract failed: {exc}", errors)
    try:
        audit = parse_audit_agent_wire_result(
            _wire_jsonl(_audit_wire(fixture.audit_result))
        )
        audit_dump = audit.model_dump(mode="json")
        if audit_dump != fixture.audit_result:
            errors.append("Audit nested result does not match parsed wire result")
    except Exception as exc:
        return _failed_result(case, f"Audit result contract failed: {exc}", errors)
    return _evaluate_protocol(
        case,
        consumer_dump,
        audit_dump,
        fixture.consumer_events,
        fixture.audit_events,
        errors,
    )


def _consumer_wire(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    error = result.get("error")
    if not isinstance(error, dict):
        raise ValueError("Consumer nested error is missing")
    return {
        key: value for key, value in result.items() if key != "error"
    } | {
        "error_code": error.get("code"),
        "error_retryable": error.get("retryable"),
        "error_authorization_required": error.get("authorization_required"),
    }


def _wire_jsonl(result: dict[str, JsonValue]) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(result, separators=(",", ":")),
            },
        },
        separators=(",", ":"),
    )


def _audit_wire(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    error = result.get("error")
    if not isinstance(error, dict):
        raise ValueError("Audit nested error is missing")
    return {
        key: value for key, value in result.items() if key != "error"
    } | {
        "error_code": error.get("code"),
        "error_retryable": error.get("retryable"),
        "error_authorization_required": error.get("authorization_required"),
    }


def _evaluate_protocol(
    case: EvalCase,
    consumer_result: dict[str, object],
    audit_result: dict[str, object],
    consumer_events: tuple[ProtocolEvent, ...],
    audit_events: tuple[ProtocolEvent, ...],
    initial_errors: list[str] | None = None,
) -> CaseResult:
    errors = list(initial_errors or ())
    consumer_skills = _observed_skill_names(consumer_events)
    audit_skills = _observed_skill_names(audit_events)
    expected = set(case.expected_business_skills)
    forbidden = set(case.forbidden_business_skills)
    missing: set[str] = set()
    observed_forbidden: set[str] = set()
    for role, observed in (("Consumer", consumer_skills), ("Audit", audit_skills)):
        role_set = set(observed)
        role_missing = expected.difference(role_set)
        role_forbidden = forbidden.intersection(role_set)
        missing.update(role_missing)
        observed_forbidden.update(role_forbidden)
        if role_missing:
            errors.append(f"{role} missing Skill reads: {sorted(role_missing)}")
        if role_forbidden:
            errors.append(f"{role} read forbidden Skills: {sorted(role_forbidden)}")
    for role, events in (("Consumer", consumer_events), ("Audit", audit_events)):
        errors.extend(_validate_observed_skill_events(events, role))
        if not any(event.tool == "execute_reviewed_read" for event in events):
            errors.append(f"{role} has no evidence read event")
    observed_outcome = str(consumer_result.get("outcome", ""))
    audit_outcome = str(audit_result.get("outcome", ""))
    if observed_outcome != case.expected_outcome:
        errors.append(
            f"Consumer outcome {observed_outcome!r} != {case.expected_outcome!r}"
        )
    errors.extend(_validate_effect_metadata(consumer_result))
    if audit_outcome not in case.acceptable_audit_outcomes:
        errors.append(
            f"Audit outcome {audit_outcome!r} is not acceptable for this case"
        )
    sources = {
        "consumer_events": _event_views(consumer_events),
        "audit_events": _event_views(audit_events),
        "consumer_evidence": _event_views(_evidence_events(consumer_events)),
        "audit_evidence": _event_views(_evidence_events(audit_events)),
        "consumer_result": consumer_result,
        "audit_result": audit_result,
    }
    missing_assertions = tuple(
        assertion.assertion_id
        for assertion in case.required_assertions
        if not _evaluate_assertion(assertion, sources)
    )
    if missing_assertions:
        errors.append(f"required assertions failed: {list(missing_assertions)}")
    observed_skills = tuple(dict.fromkeys((*consumer_skills, *audit_skills)))
    return CaseResult(
        case_id=case.case_id,
        ok=not errors,
        observed_business_skills=observed_skills,
        observed_outcome=observed_outcome,
        audit_outcome=audit_outcome,
        missing_skills=tuple(sorted(missing)),
        forbidden_skills=tuple(sorted(observed_forbidden)),
        missing_assertions=missing_assertions,
        consumer_result=consumer_result,
        audit_result=audit_result,
        consumer_events=tuple(_event_views(consumer_events)),
        audit_events=tuple(_event_views(audit_events)),
        errors=tuple(errors),
    )


def _evaluate_assertion(
    assertion: AssertionSpec,
    sources: dict[str, object],
) -> bool:
    missing = object()
    value: object = sources[assertion.source]
    for part in assertion.path:
        if isinstance(part, int) and isinstance(value, (list, tuple)):
            value = value[part] if 0 <= part < len(value) else missing
        elif isinstance(part, str) and isinstance(value, dict):
            value = value.get(part, missing)
        else:
            value = missing
        if value is missing:
            break
    if assertion.operator == "absent":
        return value is missing or value is None
    if value is missing:
        return False
    if assertion.operator == "equals":
        return value == assertion.expected
    if assertion.operator == "contains":
        return isinstance(value, (str, list, tuple, dict)) and assertion.expected in value
    if assertion.operator == "count_equals":
        return hasattr(value, "__len__") and len(value) == assertion.expected
    return False


def _validate_effect_metadata(consumer_result: dict[str, object]) -> list[str]:
    proposal = consumer_result.get("proposal")
    if proposal is None:
        return []
    if not isinstance(proposal, dict) or not isinstance(proposal.get("actions"), list):
        return ["Consumer proposal actions are unavailable for effect validation"]
    registry = McpToolEffectRegistry.default()
    native_classifier = NativeCliMetadataClassifier()
    errors: list[str] = []
    for index, action in enumerate(proposal["actions"]):
        if not isinstance(action, dict):
            errors.append(f"proposal action {index} is not structured")
            continue
        try:
            parsed_action = ProposedAction.model_validate(action)
        except ValidationError as exc:
            errors.append(f"proposal action {index} contract failed: {exc}")
            continue
        expected = _expected_effect_action(
            parsed_action,
            registry,
            action_index=index,
        )
        if expected.get("operation_contract_valid") is False:
            errors.append(f"proposal action {index} operation contract is invalid")
            continue
        native_call = native_classifier.classify(
            {"type": "command_execution", **parsed_action.payload}
        )
        if native_call is not None:
            if native_call.effect is not EffectKind.EFFECTFUL:
                errors.append(f"proposal action {index} is not an executable effect")
            continue
        call = registry.classify(
            {
                "type": "mcp_tool_call",
                "server": parsed_action.capability,
                "tool": parsed_action.operation,
                "arguments": parsed_action.payload,
            }
        )
        if call is None or expected.get("reviewed_tool") is None:
            errors.append(f"proposal action {index} has unknown effect metadata")
        elif call.effect is not EffectKind.EFFECTFUL:
            errors.append(f"proposal action {index} is not an executable effect")
    return errors


def _validate_skill_bindings(fixture: ProtocolFixture) -> list[str]:
    errors: list[str] = []
    receipt_by_name = {receipt.name: receipt for receipt in fixture.skill_receipts}
    for receipt in fixture.skill_receipts:
        path = _skill_path(receipt.path)
        if path is None or not path.is_file():
            errors.append(f"Skill path is invalid for {receipt.name}: {receipt.path}")
            continue
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if current != receipt.sha256:
            errors.append(f"Skill digest changed for {receipt.name}")
    for role, events in (
        ("Consumer", fixture.consumer_events),
        ("Audit", fixture.audit_events),
    ):
        for event in events:
            if event.tool != "read_skill":
                continue
            name = event.result.get("name")
            receipt = receipt_by_name.get(str(name))
            event_path = event.result.get("path")
            argument_path = event.arguments.get("path")
            if (
                receipt is None
                or _skill_path(str(event_path)) != _skill_path(receipt.path)
                or _skill_path(str(argument_path)) != _skill_path(receipt.path)
                or event.result.get("sha256") != receipt.sha256
            ):
                errors.append(f"{role} Skill read does not match recorded receipt: {name}")
    return errors


def _validate_evidence_digests(
    events: tuple[ProtocolEvent, ...], role: str
) -> list[str]:
    errors: list[str] = []
    for event in events:
        if event.tool != "execute_reviewed_read":
            continue
        stdout = event.result.get("stdout")
        digest = event.result.get("result_digest")
        arguments_argv = event.arguments.get("argv")
        result_argv = event.result.get("argv")
        if arguments_argv != result_argv:
            errors.append(f"{role} evidence read arguments do not match its result")
        if not isinstance(stdout, str) or digest != hashlib.sha256(stdout.encode()).hexdigest():
            errors.append(f"{role} evidence read digest is invalid")
    return errors


def _validate_observed_skill_events(
    events: tuple[ProtocolEvent, ...], role: str
) -> list[str]:
    errors: list[str] = []
    for event in events:
        if event.tool != "read_skill":
            continue
        name = event.result.get("name")
        path_value = event.result.get("path")
        digest = event.result.get("sha256")
        path = _skill_path(str(path_value))
        expected_path = _skill_path(f"skills/{name}/SKILL.md")
        if path is None or not path.is_file():
            errors.append(f"{role} Skill read has invalid path for {name}: {path_value}")
            continue
        if path != expected_path or _skill_path(str(event.arguments.get("path"))) != path:
            errors.append(f"{role} Skill read path does not match Skill name: {name}")
            continue
        if digest != hashlib.sha256(path.read_bytes()).hexdigest():
            errors.append(f"{role} Skill read digest changed for {name}")
    return errors


def _evidence_events(
    events: tuple[ProtocolEvent, ...],
) -> tuple[ProtocolEvent, ...]:
    return tuple(event for event in events if event.tool == "execute_reviewed_read")


def _event_views(events: tuple[ProtocolEvent, ...]) -> list[dict[str, object]]:
    views: list[dict[str, object]] = []
    for event in events:
        view = event.model_dump(mode="json")
        stdout = view["result"].get("stdout")
        if event.tool == "execute_reviewed_read" and isinstance(stdout, str):
            try:
                view["result"]["decoded_stdout"] = json.loads(stdout)
            except json.JSONDecodeError:
                view["result"]["decoded_stdout"] = None
        views.append(view)
    return views


def _observed_skill_names(events: tuple[ProtocolEvent, ...]) -> tuple[str, ...]:
    return tuple(
        str(event.result["name"])
        for event in events
        if event.tool == "read_skill" and "name" in event.result
    )


def _scenario_digest(case: EvalCase) -> str:
    canonical = json.dumps(
        {"context": case.context, "trigger": case.trigger},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _skill_path(value: str) -> Path | None:
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    skills_root = (ROOT / "skills").resolve()
    return resolved if resolved == skills_root or skills_root in resolved.parents else None


def _failed_result(
    case: EvalCase,
    error: str,
    prior_errors: list[str] | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case.case_id,
        ok=False,
        observed_business_skills=(),
        observed_outcome="failed_to_replay",
        audit_outcome="failed_to_replay",
        missing_skills=case.expected_business_skills,
        forbidden_skills=(),
        missing_assertions=tuple(
            assertion.assertion_id for assertion in case.required_assertions
        ),
        consumer_result={},
        audit_result={},
        consumer_events=(),
        audit_events=(),
        errors=tuple([*(prior_errors or ()), error]),
    )


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
    instructions = (
        consumer_developer_instructions(rules)
        if role == "consumer"
        else audit_developer_instructions(rules)
        + "\n\n## Eval dry-run\nReview only. Never execute an external write."
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


def run_live(
    cases: tuple[EvalCase, ...],
    fixtures: tuple[ProtocolFixture, ...] | None = None,
) -> tuple[CaseResult, ...]:
    fixture_by_id = {
        fixture.case_id: fixture
        for fixture in (fixtures if fixtures is not None else load_fixtures())
    }
    results: list[CaseResult] = []
    for case in cases:
        fixture = fixture_by_id.get(case.case_id)
        if fixture is None:
            results.append(_failed_result(case, "live evidence fixture is missing"))
            continue
        try:
            results.append(_run_live_case(case, fixture))
        except Exception as exc:
            results.append(_failed_result(case, f"live execution failed: {exc}"))
    return tuple(results)


def _run_live_case(case: EvalCase, fixture: ProtocolFixture) -> CaseResult:
    binding_errors: list[str] = []
    if fixture.scenario_sha256 != _scenario_digest(case):
        binding_errors.append("recorded scenario digest does not match trigger and context")
    binding_errors.extend(_validate_skill_bindings(fixture))
    if binding_errors:
        return _failed_result(
            case,
            "live evidence fixture binding is stale",
            binding_errors,
        )
    evidence = next(
        event for event in fixture.consumer_events if event.tool == "execute_reviewed_read"
    )
    read_argv = evidence.arguments.get("argv")
    stdout = evidence.result.get("stdout")
    if not isinstance(read_argv, list) or not isinstance(stdout, str):
        raise EvalValidationError("recorded evidence operation is malformed")
    with tempfile.TemporaryDirectory(prefix=f"skill-runtime-{case.case_id}-") as raw:
        work = Path(raw)
        config_path = work / "fixture.json"
        consumer_log = work / "consumer-events.jsonl"
        audit_log = work / "audit-events.jsonl"
        skill_paths = [
            ROOT / "skills" / name / "SKILL.md" for name in BUNDLED_BUSINESS_SKILL_NAMES
        ]
        config_path.write_text(
            json.dumps(
                {
                    "skill_paths": [str(path.resolve()) for path in skill_paths],
                    "operation_responses": [{"argv": read_argv, "stdout": stdout}],
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
            "Read applicable Skills and evidence. Perform no external writes. Return "
            "only the strict Consumer result with machine-readable action payload."
        )
        consumer_raw = _last_agent_message(
            _execute_live_command(
                build_live_command(
                    case,
                    role="consumer",
                    workspace=ROOT,
                    config_path=config_path,
                    log_path=consumer_log,
                ),
                consumer_prompt,
            )
        )
        consumer = parse_consumer_agent_wire_result(consumer_raw)
        consumer_events = _read_event_log(consumer_log)
        audit_prompt = (
            f"Generalized eval trigger:\n{case.trigger}\n\n"
            f"Generalized eval context:\n{case.context}\n\n"
            f"Consumer strict result:\n{consumer_raw}\n\n"
            f"Available business Skill paths:\n{available}\n\n"
            f"Available exact reviewed read command:\n{json.dumps(read_argv)}\n\n"
            "This is an Audit dry-run. Reread applicable Skills and evidence, review "
            "the candidate, execute nothing, and return only the strict Audit result."
        )
        audit_raw = _last_agent_message(
            _execute_live_command(
                build_live_command(
                    case,
                    role="audit",
                    workspace=ROOT,
                    config_path=config_path,
                    log_path=audit_log,
                ),
                audit_prompt,
            )
        )
        audit = parse_audit_agent_wire_result(audit_raw)
        audit_events = _read_event_log(audit_log)
        return _evaluate_protocol(
            case,
            consumer.model_dump(mode="json"),
            audit.model_dump(mode="json"),
            consumer_events,
            audit_events,
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


def _read_event_log(path: Path) -> tuple[ProtocolEvent, ...]:
    if not path.exists():
        return ()
    try:
        return tuple(
            ProtocolEvent.model_validate(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    except (ValidationError, json.JSONDecodeError) as exc:
        raise EvalValidationError(f"live protocol event is invalid: {exc}") from exc


def _validate_sanitized(case: EvalCase, *, path: Path, line_number: int) -> None:
    _validate_structured_sanitized(
        case.model_dump(mode="json"),
        path=path,
        line_number=line_number,
    )


def _validate_fixture_sanitized(
    fixture: ProtocolFixture, *, path: Path, line_number: int
) -> None:
    _validate_structured_sanitized(
        fixture.model_dump(mode="json"),
        path=path,
        line_number=line_number,
    )


def _validate_structured_sanitized(
    value: object, *, path: Path, line_number: int
) -> None:
    def fail(issue: str, location: tuple[str, ...]) -> None:
        field = ".".join(location) or "root"
        raise EvalValidationError(
            f"sanitization failure at {path}:{line_number} ({field}): {issue}"
        )

    def visit(item: object, location: tuple[str, ...], key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child in item.items():
                normalized = _normalize_field_name(str(child_key))
                child_location = (*location, str(child_key))
                if (
                    normalized in _SENSITIVE_STRUCTURED_KEYS
                    and _has_nonempty_identifier(child)
                ):
                    fail(f"prohibited identifier field {child_key!r}", child_location)
                visit(child, child_location, normalized)
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, (*location, str(index)), key)
            return
        if not isinstance(item, str) or key in _OPAQUE_VALUE_KEYS:
            return
        issue = _sanitization_issue(item)
        if issue:
            fail(issue, location)
        decoded = _decode_json_container(item)
        if decoded is not None:
            visit(decoded, (*location, "decoded"))

    visit(value, ())


def _normalize_field_name(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _has_nonempty_identifier(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _decode_json_container(value: str) -> dict[str, object] | list[object] | None:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, (dict, list)) else None


def _sanitization_issue(value: str) -> str:
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return "only printable ASCII generalized text is allowed"
    if _ID_LABEL.search(value):
        return "identifier field is not allowed"
    if _CREDENTIAL_LABEL.search(value):
        return "credential field is not allowed"
    if "@" in value:
        return "mentions and email-like identities are not allowed"
    if _UUID.search(value) or _ULID.search(value) or _LONG_HEX.search(value):
        return "known opaque identifier shape is not allowed"
    for chunk in value.split():
        candidate = chunk.strip(".,;:()[]{}<>'\"")
        parsed = urlsplit(candidate)
        query = parsed.query
        if query and any(
            _SIGNED_QUERY_KEYS.match(key.replace("-", "_").casefold())
            for key, _value in parse_qsl(query, keep_blank_values=True)
        ):
            return "signed URL or reference is not allowed"
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


def _print_report(report: SuiteReport) -> None:
    label = "recorded replay" if report.mode == "recorded_replay" else report.mode
    print(f"{label}: {sum(result.ok for result in report.results)}/{len(report.results)} passed")
    for result in report.results:
        status = "PASS" if result.ok else "FAIL"
        detail = "; ".join(result.errors) if result.errors else result.observed_outcome
        print(f"[{status}] {result.case_id}: {detail}")
    print(json.dumps(report.to_jsonable(), indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Skill-runtime regression cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_PATH)
    parser.add_argument(
        "--live",
        action="store_true",
        help="opt in to isolated native Codex Consumer and dry-run Audit evidence",
    )
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        fixtures = load_fixtures(args.fixtures)
        if args.live:
            results = run_live(cases, fixtures)
            report = SuiteReport("live", all(item.ok for item in results), results)
        else:
            report = run_scripted(cases, fixtures)
        _print_report(report)
        return 0 if report.ok else 1
    except EvalValidationError as exc:
        print(json.dumps({"mode": "validation", "ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
