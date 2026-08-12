from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from evals.skill_runtime.run import (
    EvalCase,
    EvalValidationError,
    build_live_command,
    load_cases,
    main,
    run_scripted,
)
from tests.support.native_codex_read_fixture import (
    assert_isolated_read_only_fixture_command,
)


CASES_PATH = Path("evals/skill_runtime/cases.jsonl")
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "evals" / "skill_runtime" / "run.py"
EXPECTED_OUTCOMES = {"proposal", "no_action", "needs_human", "failed"}


def test_every_skill_runtime_eval_declares_skill_outcome_and_assertions():
    cases = load_cases(CASES_PATH)

    assert len(cases) >= 10
    assert len({case.case_id for case in cases}) == len(cases)
    for case in cases:
        assert case.expected_business_skills
        assert case.expected_outcome in EXPECTED_OUTCOMES
        assert case.required_assertions


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
        "required_assertions": ["no_external_write"],
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
        ("user identifier", "user_id=839201"),
        ("token", "token=abcdefghijklmnopqrstuvwxyz0123456789"),
        ("session identifier", "session_id=1234567890abcdef"),
        ("signed link", "https://example.test/file?signature=opaque"),
    ):
        unsafe_path = tmp_path / f"unsafe-{label.replace(' ', '-')}.jsonl"
        unsafe_path.write_text(
            json.dumps({**base, "context": unsafe}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(EvalValidationError, match="sanitization"):
            load_cases(unsafe_path)


def test_scripted_runner_passes_corpus_and_detects_expectation_mutation():
    cases = load_cases(CASES_PATH)

    passing = run_scripted(cases)
    assert passing.ok
    assert len(passing.results) == len(cases)
    assert all(result.ok for result in passing.results)

    mutated = list(cases)
    mutated[0] = EvalCase.model_validate(
        {
            **mutated[0].model_dump(mode="json"),
            "required_assertions": ["full_mail_thread_read"],
        }
    )
    failing = run_scripted(tuple(mutated))
    assert not failing.ok
    assert failing.results[0].missing_assertions == ("full_mail_thread_read",)


def test_default_cli_passes_without_invoking_live_runner(monkeypatch, capsys):
    def fail_if_live(*_args, **_kwargs):
        raise AssertionError("default mode must not execute Codex")

    monkeypatch.setattr("evals.skill_runtime.run.run_live", fail_if_live)

    assert main([]) == 0
    output = capsys.readouterr().out
    assert "10/10 passed" in output
    assert '"mode": "scripted"' in output


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
                "expected_outcome": "failed",
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
        assert "scripted: 10/10 passed" in completed.stdout
        assert '"total": 10' in completed.stdout


def test_script_path_cli_returns_nonzero_for_mutated_corpus(tmp_path: Path):
    case = load_cases(CASES_PATH)[0]
    mutated_path = tmp_path / "mutated.jsonl"
    mutated_path.write_text(
        json.dumps(
            {
                **case.model_dump(mode="json"),
                "expected_outcome": "failed",
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
    assert "scripted: 0/1 passed" in completed.stdout
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
