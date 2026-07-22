from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.quality.models import (
    EvaluationResult,
    QualityCase,
    QualityFailure,
    QualitySuite,
)

TERMINAL_STATES = {"succeeded", "failed", "unknown"}


def load_cases(path: Path) -> list[QualityCase]:
    cases: list[QualityCase] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cases.append(QualityCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"invalid quality case at line {line_number}: {exc}") from exc
    if not cases:
        raise ValueError("quality case file is empty")
    return cases


def _all_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _all_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _all_text(item)


def _case_failures(case: QualityCase) -> list[QualityFailure]:
    failures: list[QualityFailure] = []
    actions_raw = case.recorded_output.get("actions", [])
    if not isinstance(actions_raw, list) or any(not isinstance(item, dict) for item in actions_raw):
        return [QualityFailure(case_id=case.id, rule="protocol_schema", detail="actions must be a list of objects")]
    actions: list[dict[str, Any]] = actions_raw

    for action in actions:
        kind = str(action.get("kind") or "")
        if not kind or (case.allowed_actions and kind not in case.allowed_actions):
            failures.append(QualityFailure(case_id=case.id, rule="allowed_actions", detail=f"action is not allowed: {kind or '<missing>'}"))
        if kind in case.forbidden_actions:
            failures.append(QualityFailure(case_id=case.id, rule="forbidden_actions", detail=f"forbidden action requested: {kind}"))
        for field, expected in case.target_binding.items():
            if action.get(field) != expected:
                failures.append(QualityFailure(case_id=case.id, rule="target_binding", detail=f"{field} does not match the trusted target"))
        evidence = action.get("evidence")
        for field in case.required_evidence:
            if not isinstance(evidence, Mapping) or not evidence.get(field):
                failures.append(QualityFailure(case_id=case.id, rule="required_evidence", detail=f"missing evidence: {field}"))

    combined_text = "\n".join(_all_text(case.recorded_output)).casefold()
    for forbidden in case.forbidden_text:
        if forbidden.casefold() in combined_text:
            failures.append(QualityFailure(case_id=case.id, rule="forbidden_text", detail="output contains prohibited text"))

    if "unique_idempotency_key" in case.replay_invariants:
        keys = [str(action.get("idempotency_key") or "") for action in actions]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            failures.append(QualityFailure(case_id=case.id, rule="unique_idempotency_key", detail="external actions need unique, non-empty idempotency keys"))
    if "single_terminal_state" in case.replay_invariants:
        by_key: dict[str, set[str]] = {}
        for action in actions:
            key = str(action.get("idempotency_key") or "")
            status = str(action.get("status") or "")
            if key and status in TERMINAL_STATES:
                by_key.setdefault(key, set()).add(status)
        if any(len(statuses) > 1 for statuses in by_key.values()):
            failures.append(QualityFailure(case_id=case.id, rule="single_terminal_state", detail="one action has conflicting terminal states"))

    for key, expected in case.expected_semantics.items():
        if case.recorded_output.get(key) != expected:
            failures.append(QualityFailure(case_id=case.id, rule="semantic_expectation", detail=f"semantic field mismatch: {key}"))
    return failures


def evaluate_cases(cases: Iterable[QualityCase], *, suite: QualitySuite) -> EvaluationResult:
    selected = [case for case in cases if case.suite == suite]
    if not selected:
        raise ValueError(f"no cases found for suite: {suite}")
    failures: list[QualityFailure] = []
    failed_ids: set[str] = set()
    critical_failed = False
    for case in selected:
        case_failures = _case_failures(case)
        if case_failures:
            failed_ids.add(case.id)
            critical_failed = critical_failed or case.critical
            failures.extend(case_failures)
    passed = len(selected) - len(failed_ids)
    score = passed / len(selected)
    threshold = 0.95 if suite == "semantic" else 1.0
    status = "passed" if score >= threshold and not critical_failed else "failed"
    return EvaluationResult(
        suite=suite,
        status=status,
        total=len(selected),
        passed=passed,
        failed=len(failed_ids),
        score=score,
        failures=tuple(failures),
    )
