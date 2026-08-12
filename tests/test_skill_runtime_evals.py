from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.agent_contracts import ConsumerAgentResult
from evals.skill_runtime.run import (
    EvalCase,
    EvalValidationError,
    ProtocolEvent,
    _render_live_audit_prompt,
    _run_live_case,
    _verified_live_skill_receipts,
    build_live_command,
    load_fixtures,
    load_cases,
    main,
    replay_fixture,
    run_scripted,
)
from tests.support.native_codex_read_fixture import (
    assert_isolated_read_only_fixture_command,
)


CASES_PATH = Path("evals/skill_runtime/cases.jsonl")
FIXTURES_PATH = Path("evals/skill_runtime/fixtures.jsonl")
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "evals" / "skill_runtime" / "run.py"
EXPECTED_OUTCOMES = {"proposal", "no_action", "needs_human", "failed"}


def _rejected_audit_result(outcome: str = "failed") -> dict[str, object]:
    result: dict[str, object] = {
        "outcome": outcome,
        "summary": "Dry-run review did not accept the candidate.",
        "proposal_revision": 0,
        "side_effect_state": "none",
        "feedback": None,
        "external_result": None,
        "reconciliation": [],
        "error": {
            "code": "review_failed",
            "retryable": False,
            "authorization_required": False,
        },
    }
    if outcome == "revision_required":
        result["feedback"] = {
            "rule": "fixture_contract",
            "observation": "The candidate needs revision.",
            "requested_revision": "Return a compliant candidate.",
        }
    return result


def _consumer_result_for_outcome(
    base: dict[str, object], outcome: str
) -> dict[str, object]:
    if outcome == "proposal":
        return base
    result = {
        **base,
        "outcome": outcome,
        "proposal": None,
        "decision_options": [],
    }
    if outcome == "failed":
        result["error"] = {
            "code": "fixture_failure",
            "retryable": False,
            "authorization_required": False,
        }
    if outcome == "needs_human":
        result["decision_options"] = [
            {
                "key": "A",
                "label": "Proceed",
                "instruction": "Proceed with the generalized candidate.",
                "consequence": "Audit can review the candidate.",
            },
            {
                "key": "B",
                "label": "Revise",
                "instruction": "Request a generalized revision.",
                "consequence": "No candidate executes.",
            },
        ]
    return result


def _case_for_terminal_outcome(case: EvalCase, outcome: str) -> EvalCase:
    assertions = tuple(
        assertion.model_copy(update={"expected": outcome})
        if assertion.source == "consumer_result" and assertion.path == ("outcome",)
        else assertion
        for assertion in case.required_assertions
    )
    return case.model_copy(
        update={
            "expected_outcome": outcome,
            "acceptable_audit_outcomes": "not_applicable",
            "required_assertions": assertions,
        }
    )


def test_every_skill_runtime_eval_declares_skill_outcome_and_assertions():
    cases = load_cases(CASES_PATH)

    assert len(cases) >= 10
    assert len({case.case_id for case in cases}) == len(cases)
    for case in cases:
        assert case.expected_business_skills
        assert case.expected_outcome in EXPECTED_OUTCOMES
        assert case.required_assertions


@pytest.mark.parametrize("terminal_outcome", ["no_action", "needs_human", "failed"])
def test_non_proposal_case_schema_requires_audit_not_applicable(
    terminal_outcome: str,
):
    base = load_cases(CASES_PATH)[5]
    terminal = _case_for_terminal_outcome(base, terminal_outcome)

    assert EvalCase.model_validate(terminal.model_dump(mode="json"))
    with pytest.raises(ValueError, match="not_applicable"):
        EvalCase.model_validate(
            {
                **terminal.model_dump(mode="json"),
                "acceptable_audit_outcomes": ["needs_human"],
            }
        )


def test_recorded_protocol_fixtures_are_separate_complete_and_read_only():
    cases = load_cases(CASES_PATH)
    fixtures = load_fixtures(FIXTURES_PATH)

    assert {fixture.case_id for fixture in fixtures} == {
        case.case_id for case in cases
    }
    for fixture in fixtures:
        assert fixture.scenario_sha256
        assert fixture.skill_receipts
        assert fixture.consumer_events
        assert fixture.consumer_result
        if fixture.consumer_result["outcome"] != "proposal":
            assert fixture.audit_events == ()
            assert fixture.audit_result is None
        else:
            assert fixture.audit_events
            assert fixture.audit_result
        assert all(
            event.tool in {"read_skill", "execute_reviewed_read"}
            for event in (*fixture.consumer_events, *fixture.audit_events)
        )


def test_fixture_loader_applies_sanitization_to_recorded_protocol(tmp_path: Path):
    fixture = load_fixtures(FIXTURES_PATH)[0]
    unsafe = fixture.model_copy(
        update={
            "consumer_result": {
                **fixture.consumer_result,
                "summary": "session-id = 1234567890abcdef",
            }
        }
    )
    path = tmp_path / "unsafe-fixture.jsonl"
    path.write_text(
        json.dumps(unsafe.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EvalValidationError, match="sanitization"):
        load_fixtures(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_id", 7),
        ("userId", "8"),
        ("open-id", "member"),
        ("session_id", 9),
        ("messageId", "10"),
        ("conversation id", "11"),
        ("task-id", 12),
        ("attemptId", "13"),
        ("recipient_user_id", 14),
        ("senderUserId", "15"),
        ("dingtalk_open_id", "member"),
        ("sourceSessionId", "16"),
        ("process_instance_id", 17),
        ("approvalProcessInstanceId", "18"),
        ("apiToken", "short"),
        ("responseSignatureValue", "abc"),
        ("requestAuthKey", "key"),
        ("clientSecret", "secret"),
        ("ｒｅｃｉｐｉｅｎｔ＿ｕｓｅｒ＿ｉｄ", 19),
        ("token", "short"),
        ("signature", "abc"),
    ],
)
def test_fixture_sanitizer_rejects_nested_identifier_aliases(
    tmp_path: Path, field: str, value: object
):
    fixture = load_fixtures(FIXTURES_PATH)[0]
    unsafe = fixture.model_copy(
        update={
            "consumer_events": (
                fixture.consumer_events[0].model_copy(
                    update={
                        "result": {
                            **fixture.consumer_events[0].result,
                            "nested": [{"metadata": {field: value}}],
                        }
                    }
                ),
                *fixture.consumer_events[1:],
            )
        }
    )
    path = tmp_path / "unsafe-fixture.jsonl"
    path.write_text(
        json.dumps(unsafe.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EvalValidationError, match="sanitization"):
        load_fixtures(path)


def test_fixture_sanitizer_rejects_nested_signed_reference(tmp_path: Path):
    fixture = load_fixtures(FIXTURES_PATH)[0]
    unsafe = fixture.model_copy(
        update={
            "audit_result": {
                **fixture.audit_result,
                "feedback": {
                    "references": ["files.example.test/report?sign=short"]
                },
            }
        }
    )
    path = tmp_path / "unsafe-signed-fixture.jsonl"
    path.write_text(
        json.dumps(unsafe.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EvalValidationError, match="sanitization"):
        load_fixtures(path)


def test_fixture_sanitizer_allows_benign_structured_roles_counts_and_dates(
    tmp_path: Path,
):
    fixture = load_fixtures(FIXTURES_PATH)[0]
    benign = fixture.model_copy(
        update={
            "consumer_events": (
                fixture.consumer_events[0].model_copy(
                    update={
                        "result": {
                            **fixture.consumer_events[0].result,
                            "nested": [
                                {
                                    "owner_role": "reviewer",
                                    "task_count": 12,
                                    "review_date": "2026-08-12",
                                    "identity_verified": True,
                                    "open_question": "Confirm the agenda.",
                                    "user_id": "",
                                    "token": None,
                                }
                            ],
                        }
                    }
                ),
                *fixture.consumer_events[1:],
            )
        }
    )
    path = tmp_path / "benign-fixture.jsonl"
    path.write_text(
        json.dumps(benign.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    assert load_fixtures(path)[0].case_id == fixture.case_id


def test_corpus_is_sanitized_and_covers_all_required_regressions():
    cases = load_cases(CASES_PATH)

    assert {case.case_id for case in cases} == {
        "calendar-vague-invite-clarify-inviter",
        "calendar-clear-context-accept",
        "document-readable-reference-read",
        "document-image-inspect-before-judgment",
        "mail-truncated-card-resolve-thread",
        "personnel-unrelated-recipient-protect",
        "tracking-participant-is-not-owner",
        "tracking-completed-todo-suppress",
        "oa-factual-gap-clarify-applicant",
        "meeting-mentions-adjacent-to-actions",
    }


def test_loader_rejects_extra_fields_duplicate_ids_and_unsanitized_content(
    tmp_path: Path,
):
    base = {
        "case_id": "valid-case",
        "trigger": "A generalized trigger.",
        "context": "A generalized context.",
        "expected_business_skills": ["ceo-message-triage"],
        "forbidden_business_skills": [],
        "expected_outcome": "no_action",
        "acceptable_audit_outcomes": "not_applicable",
        "required_assertions": [
            {
                "assertion_id": "no_external_write",
                "source": "consumer_result",
                "path": ["proposal"],
                "operator": "equals",
                "expected": None,
            },
            {
                "assertion_id": "consumer_evidence_read",
                "source": "consumer_evidence",
                "path": [0, "result"],
                "operator": "contains",
                "expected": "stdout",
            },
        ],
    }

    extra = tmp_path / "extra.jsonl"
    extra.write_text(json.dumps({**base, "unexpected": True}) + "\n", encoding="utf-8")
    with pytest.raises(EvalValidationError, match="unexpected"):
        load_cases(extra)

    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        json.dumps(base) + "\n" + json.dumps(base) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvalValidationError, match="duplicate case_id"):
        load_cases(duplicate)

    for label, unsafe in (
        ("user identifier", "user id: 839201"),
        ("user hex identifier", "user-id = deadbeef"),
        ("open identifier", "open_id: member"),
        ("session identifier", "session_id: 1234567890abcdef"),
        ("message identifier", "message-id=90210"),
        ("conversation identifier", "conversation id: abc123"),
        ("task identifier", "task_id = 42"),
        ("attempt identifier", "attempt-id: 7f8e9d"),
        ("user id whitespace", "user id 839201"),
        ("session id whitespace", "session id 1234567890abcdef"),
        ("message id whitespace", "message id msg90210"),
        ("conversation id whitespace", "conversation id 442200"),
        ("short user id", "user id x"),
        ("short open id", "open id: z"),
        ("short task id", "task id=a"),
        ("process instance id", "process instance id p"),
        ("status followed by value", "user id unknown x"),
        ("token", "token=abcdefghijklmnopqrstuvwxyz0123456789"),
        ("signed link", "https://example.test/file?signature=opaque"),
        ("schemeless signed link", "files.example.test/file?expires=1900000000"),
        ("relative signed reference", "/file/report?auth=opaque"),
        ("query token", "files.example.test/file?token=7"),
        ("query sign", "files.example.test/file?sign=8"),
        ("query key", "files.example.test/file?key=9"),
        ("opaque identifier", "01J9ZQ7M4N8K2T6V3X5C7B9D1F"),
    ):
        unsafe_path = tmp_path / f"unsafe-{label.replace(' ', '-')}.jsonl"
        unsafe_path.write_text(
            json.dumps({**base, "context": unsafe}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(EvalValidationError, match="sanitization"):
            load_cases(unsafe_path)


@pytest.mark.parametrize(
    "benign",
    [
        "The review date is 2026-08-12.",
        "The corpus contains 10 cases and 24 checks.",
        "Version 123 is the current generalized fixture.",
        "The participant completed the documented reconciliation workflow.",
        "The owner_role is reviewer and the task count is 12.",
        "The user id is unavailable in this generalized fixture.",
        "The message id was not provided by the source.",
        "The session id remains unknown.",
        "The conversation id is missing.",
        "The task id: none.",
        "A user identity is important for authorization checks.",
    ],
)
def test_sanitizer_accepts_benign_dates_counts_and_prose(tmp_path: Path, benign: str):
    case = load_cases(CASES_PATH)[0]
    path = tmp_path / "benign.jsonl"
    path.write_text(
        json.dumps({**case.model_dump(mode="json"), "context": benign}) + "\n",
        encoding="utf-8",
    )

    assert load_cases(path)[0].context == benign


def test_scripted_runner_passes_corpus_and_detects_expectation_mutation():
    cases = load_cases(CASES_PATH)
    fixtures = load_fixtures(FIXTURES_PATH)

    passing = run_scripted(cases, fixtures)
    assert passing.ok
    assert len(passing.results) == len(cases)
    assert all(result.ok for result in passing.results)

    mutated = list(cases)
    mutated[0] = EvalCase.model_validate(
        {**mutated[0].model_dump(mode="json"), "trigger": "A different trigger."}
    )
    failing = run_scripted(tuple(mutated), fixtures)
    assert not failing.ok
    assert "scenario digest" in " ".join(failing.results[0].errors)


def test_recorded_replay_rejects_context_and_skill_digest_mutations():
    case = load_cases(CASES_PATH)[0]
    fixture = load_fixtures(FIXTURES_PATH)[0]
    changed_context = EvalCase.model_validate(
        {**case.model_dump(mode="json"), "context": "A different context."}
    )
    assert not replay_fixture(changed_context, fixture).ok

    receipt = fixture.skill_receipts[0]
    changed_fixture = fixture.model_copy(
        update={
            "skill_receipts": (
                receipt.model_copy(update={"sha256": "0" * 64}),
                *fixture.skill_receipts[1:],
            )
        }
    )
    result = replay_fixture(case, changed_fixture)
    assert not result.ok
    assert "Skill digest" in " ".join(result.errors)


def test_recorded_replay_parses_nested_results_and_effect_metadata():
    case = load_cases(CASES_PATH)[0]
    fixture = load_fixtures(FIXTURES_PATH)[0]

    assert replay_fixture(case, fixture).ok

    malformed = fixture.model_copy(
        update={"consumer_result": {**fixture.consumer_result, "proposal": None}}
    )
    result = replay_fixture(case, malformed)
    assert not result.ok
    assert "Consumer result" in " ".join(result.errors)


def test_protocol_evaluation_requires_reads_assertions_and_acceptable_audit():
    case = load_cases(CASES_PATH)[0]
    fixture = load_fixtures(FIXTURES_PATH)[0]

    without_evidence = fixture.model_copy(
        update={
            "consumer_events": tuple(
                event
                for event in fixture.consumer_events
                if event.tool != "execute_reviewed_read"
            )
        }
    )
    missing = replay_fixture(case, without_evidence)
    assert not missing.ok
    assert missing.missing_assertions
    assert "evidence read" in " ".join(missing.errors)

    forbidden_skill = case.forbidden_business_skills[0]
    forbidden_path = REPO_ROOT / "skills" / forbidden_skill / "SKILL.md"
    forbidden_event = fixture.consumer_events[0].model_copy(
        update={
            "result": {
                "name": forbidden_skill,
                "path": str(forbidden_path),
                "sha256": __import__("hashlib").sha256(
                    forbidden_path.read_bytes()
                ).hexdigest(),
            }
        }
    )
    with_forbidden = fixture.model_copy(
        update={"consumer_events": (*fixture.consumer_events, forbidden_event)}
    )
    forbidden = replay_fixture(case, with_forbidden)
    assert not forbidden.ok
    assert forbidden.forbidden_skills == (forbidden_skill,)

    rejected_audit = fixture.model_copy(
        update={"audit_result": _rejected_audit_result()}
    )
    rejected = replay_fixture(case, rejected_audit)
    assert not rejected.ok
    assert "Audit outcome" in " ".join(rejected.errors)


def test_recorded_proposal_requires_exact_consumer_audit_skill_receipt_parity():
    case = load_cases(CASES_PATH)[0]
    fixture = load_fixtures(FIXTURES_PATH)[0]
    without_audit_skill = fixture.model_copy(
        update={
            "audit_events": tuple(
                event for event in fixture.audit_events if event.tool != "read_skill"
            )
        }
    )

    result = replay_fixture(case, without_audit_skill)

    assert not result.ok
    assert "exact verified Consumer receipts" in " ".join(result.errors)


def test_live_skill_receipts_use_production_validation_and_context_formatter():
    case = load_cases(CASES_PATH)[0]
    fixture = load_fixtures(FIXTURES_PATH)[0]
    skill_path = (REPO_ROOT / fixture.skill_receipts[0].path).resolve()
    content = skill_path.read_text(encoding="utf-8")
    receipt = fixture.skill_receipts[0]
    event = ProtocolEvent.model_validate(
        {
            "tool": "read_skill",
            "arguments": {"path": str(skill_path)},
            "result": {
                "name": receipt.name,
                "path": str(skill_path),
                "sha256": receipt.sha256,
                "content": content,
            },
        }
    )

    receipts = _verified_live_skill_receipts((event,))
    consumer = ConsumerAgentResult.model_validate(fixture.consumer_result)
    prompt = _render_live_audit_prompt(
        case,
        consumer,
        receipts,
        list(fixture.consumer_events[1].arguments["argv"]),
    )

    assert receipts[0].path == str(skill_path)
    assert "Verified Skills read by Consumer A" in prompt
    assert str(skill_path) in prompt
    assert receipt.sha256 in prompt

    tampered = event.model_copy(
        update={"result": {**event.result, "sha256": "0" * 64}}
    )
    with pytest.raises(EvalValidationError, match="tampered"):
        _verified_live_skill_receipts((tampered,))
    with pytest.raises(EvalValidationError, match="missing"):
        _verified_live_skill_receipts(())


def test_calendar_live_probe_reads_business_and_explicit_operation_skill(
    monkeypatch, tmp_path: Path
):
    case = load_cases(CASES_PATH)[0]
    fixture = load_fixtures(FIXTURES_PATH)[0]
    operation_skill = (
        tmp_path / ".agents" / "skills" / "dingtalk-calendar" / "SKILL.md"
    )
    operation_skill.parent.mkdir(parents=True)
    operation_skill.write_text(
        "# DingTalk calendar\n\nRead calendar state without writing.\n",
        encoding="utf-8",
    )
    business_skill = REPO_ROOT / "skills" / "ceo-calendar-invite" / "SKILL.md"

    def skill_event(path: Path) -> ProtocolEvent:
        content = path.read_text(encoding="utf-8")
        return ProtocolEvent.model_validate(
            {
                "tool": "read_skill",
                "arguments": {"path": str(path)},
                "result": {
                    "name": path.parent.name,
                    "path": str(path),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "content": content,
                },
            }
        )

    skill_events = (skill_event(business_skill), skill_event(operation_skill))
    evidence = next(
        event
        for event in fixture.consumer_events
        if event.tool == "execute_reviewed_read"
    )
    prompts: list[str] = []
    configured_skill_paths: list[set[str]] = []

    def wire_result(result: dict[str, object]) -> str:
        error = result["error"]
        assert isinstance(error, dict)
        flattened = {
            **{key: value for key, value in result.items() if key != "error"},
            "error_code": error["code"],
            "error_retryable": error["retryable"],
            "error_authorization_required": error["authorization_required"],
        }
        return json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(flattened),
                },
            }
        )

    outputs = iter(
        (
            wire_result(fixture.consumer_result),
            wire_result(fixture.audit_result),
        )
    )

    def execute(command: list[str], prompt: str) -> str:
        prompts.append(prompt)
        args_option = next(
            item for item in command if item.startswith("mcp_servers.agent_cli.args=")
        )
        server_args = json.loads(args_option.split("=", 1)[1])
        config = json.loads(Path(server_args[-2]).read_text(encoding="utf-8"))
        configured_skill_paths.append(set(config["skill_paths"]))
        return next(outputs)

    monkeypatch.setattr("evals.skill_runtime.run._execute_live_command", execute)
    monkeypatch.setattr(
        "evals.skill_runtime.run._read_event_log",
        lambda _path: (*skill_events, evidence),
    )

    result = _run_live_case(
        case,
        fixture,
        operation_skill_paths=(operation_skill,),
    )

    expected_paths = {str(business_skill.resolve()), str(operation_skill.resolve())}
    assert result.ok
    assert result.audit_result is not None
    assert result.audit_result["side_effect_state"] == "none"
    assert len(prompts) == 2
    assert all(expected_paths.issubset(paths) for paths in configured_skill_paths)
    assert all(all(path in prompt for path in expected_paths) for prompt in prompts)
    assert [event["result"]["name"] for event in result.consumer_events[:2]] == [
        "ceo-calendar-invite",
        "dingtalk-calendar",
    ]
    assert [event["result"]["name"] for event in result.audit_events[:2]] == [
        "ceo-calendar-invite",
        "dingtalk-calendar",
    ]


def test_live_operation_skill_receipt_requires_exact_explicit_path(tmp_path: Path):
    allowed = tmp_path / "allowed" / "dingtalk-calendar" / "SKILL.md"
    other = tmp_path / "other" / "dingtalk-calendar" / "SKILL.md"
    for path in (allowed, other):
        path.parent.mkdir(parents=True)
        path.write_text("# Calendar\n", encoding="utf-8")
    content = other.read_text(encoding="utf-8")
    event = ProtocolEvent.model_validate(
        {
            "tool": "read_skill",
            "arguments": {"path": str(other)},
            "result": {
                "name": "dingtalk-calendar",
                "path": str(other),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content": content,
            },
        }
    )

    with pytest.raises(EvalValidationError, match="invalid or tampered"):
        _verified_live_skill_receipts(
            (event,),
            authorized_skill_paths=(allowed,),
        )


@pytest.mark.parametrize("terminal_outcome", ["no_action", "needs_human", "failed"])
def test_live_terminal_outcome_does_not_invoke_audit(
    monkeypatch, terminal_outcome: str
):
    cases = {case.case_id: case for case in load_cases(CASES_PATH)}
    fixture = next(
        item
        for item in load_fixtures(FIXTURES_PATH)
        if item.consumer_result["outcome"] == "no_action"
    )
    case = cases[fixture.case_id]
    nested = _consumer_result_for_outcome(fixture.consumer_result, terminal_outcome)
    error = nested["error"]
    wire = {
        **{key: value for key, value in nested.items() if key != "error"},
        "error_code": error["code"],
        "error_retryable": error["retryable"],
        "error_authorization_required": error["authorization_required"],
    }
    stdout = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(wire),
            },
        }
    )
    invocations: list[list[str]] = []

    def execute(command: list[str], _prompt: str) -> str:
        invocations.append(command)
        return stdout

    monkeypatch.setattr("evals.skill_runtime.run._execute_live_command", execute)
    monkeypatch.setattr(
        "evals.skill_runtime.run._read_event_log",
        lambda _path: fixture.consumer_events,
    )
    monkeypatch.setattr(
        "evals.skill_runtime.run._verified_live_skill_receipts",
        lambda _events, **_kwargs: (),
    )

    terminal_case = _case_for_terminal_outcome(case, terminal_outcome)
    result = _run_live_case(terminal_case, fixture)

    assert result.ok
    assert result.audit_outcome == "not_applicable"
    assert result.audit_events == ()
    assert len(invocations) == 1


@pytest.mark.parametrize("audit_outcome", ["failed", "revision_required"])
def test_every_recorded_case_rejects_unacceptable_audit_outcome(
    audit_outcome: str,
):
    fixture_by_id = {
        fixture.case_id: fixture for fixture in load_fixtures(FIXTURES_PATH)
    }
    for case in load_cases(CASES_PATH):
        if case.expected_outcome == "no_action":
            continue
        fixture = fixture_by_id[case.case_id]
        rejected_fixture = fixture.model_copy(
            update={"audit_result": _rejected_audit_result(audit_outcome)}
        )

        result = replay_fixture(case, rejected_fixture)

        assert not result.ok
        assert "Audit outcome" in " ".join(result.errors)


def test_recorded_terminal_outcomes_reject_any_audit_protocol():
    cases = {case.case_id: case for case in load_cases(CASES_PATH)}
    for fixture in load_fixtures(FIXTURES_PATH):
        if fixture.consumer_result["outcome"] != "no_action":
            continue
        for terminal_outcome in ("no_action", "needs_human", "failed"):
            case = _case_for_terminal_outcome(
                cases[fixture.case_id], terminal_outcome
            )
            terminal = fixture.model_copy(
                update={
                    "consumer_result": _consumer_result_for_outcome(
                        fixture.consumer_result, terminal_outcome
                    ),
                }
            )
            assert replay_fixture(case, terminal).ok

            audited = terminal.model_copy(
                update={
                    "audit_events": (fixture.consumer_events[0],),
                    "audit_result": _rejected_audit_result(),
                }
            )
            result = replay_fixture(case, audited)

            assert not result.ok
            assert "terminal Consumer outcome must not contain Audit events" in result.errors
            assert "terminal Consumer outcome must not contain an Audit result" in result.errors


def test_audit_outcome_can_be_explicitly_allowed_by_corpus():
    case = load_cases(CASES_PATH)[0].model_copy(
        update={"acceptable_audit_outcomes": ("failed",)}
    )
    fixture = load_fixtures(FIXTURES_PATH)[0].model_copy(
        update={"audit_result": _rejected_audit_result()}
    )

    result = replay_fixture(case, fixture)

    assert "Audit outcome 'failed' is not acceptable" not in " ".join(result.errors)


def test_recorded_replay_evaluates_every_required_assertion():
    fixture_by_id = {
        fixture.case_id: fixture for fixture in load_fixtures(FIXTURES_PATH)
    }
    for case in load_cases(CASES_PATH):
        fixture = fixture_by_id[case.case_id]
        for index, assertion in enumerate(case.required_assertions):
            changed_assertions = list(case.required_assertions)
            changed_assertions[index] = assertion.model_copy(
                update={"expected": "__forced_mismatch__"}
            )
            changed_case = case.model_copy(
                update={"required_assertions": tuple(changed_assertions)}
            )

            result = replay_fixture(changed_case, fixture)

            assert not result.ok
            assert assertion.assertion_id in result.missing_assertions


def test_default_cli_passes_without_invoking_live_runner(monkeypatch, capsys):
    def fail_if_live(*_args, **_kwargs):
        raise AssertionError("default mode must not execute Codex")

    monkeypatch.setattr("evals.skill_runtime.run.run_live", fail_if_live)

    assert main([]) == 0
    output = capsys.readouterr().out
    assert "10/10 passed" in output
    assert '"mode": "recorded_replay"' in output


def test_cli_exits_nonzero_when_a_scripted_expectation_mismatches(
    tmp_path: Path,
    capsys,
):
    case = load_cases(CASES_PATH)[0]
    mutated_path = tmp_path / "mutated.jsonl"
    mutated_path.write_text(
        json.dumps(
                {
                    **case.model_dump(mode="json"),
                    "expected_business_skills": ["ceo-document-review"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["--cases", str(mutated_path)]) == 1
    output = capsys.readouterr().out
    assert "0/1 passed" in output
    assert '"ok": false' in output


def test_script_path_cli_runs_from_repo_root_and_unrelated_cwd(tmp_path: Path):
    invocations = (
        ([sys.executable, "evals/skill_runtime/run.py"], REPO_ROOT),
        ([sys.executable, str(SCRIPT_PATH)], tmp_path),
    )

    for command, cwd in invocations:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert "recorded replay: 10/10 passed" in completed.stdout
        assert '"total": 10' in completed.stdout


def test_script_path_cli_returns_nonzero_for_mutated_corpus(tmp_path: Path):
    case = load_cases(CASES_PATH)[0]
    mutated_path = tmp_path / "mutated.jsonl"
    mutated_path.write_text(
        json.dumps(
                {
                    **case.model_dump(mode="json"),
                    "expected_business_skills": ["ceo-document-review"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--cases", str(mutated_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1, completed.stderr
    assert "recorded replay: 0/1 passed" in completed.stdout
    assert '"ok": false' in completed.stdout


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_live_command_is_isolated_and_read_only(role: str, tmp_path: Path):
    case = load_cases(CASES_PATH)[0]
    command = build_live_command(
        case,
        role=role,
        workspace=Path.cwd(),
        config_path=tmp_path / "fixture.json",
        log_path=tmp_path / f"{role}.jsonl",
    )

    assert command[:2] == ["codex", "exec"]
    assert_isolated_read_only_fixture_command(command)
    assert 'approval_policy="never"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    if role == "audit":
        assert any("dry-run" in item for item in command)
