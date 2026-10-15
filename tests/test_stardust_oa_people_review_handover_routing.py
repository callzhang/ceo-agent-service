from __future__ import annotations

from pathlib import Path


SKILL_PATHS = [Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")]
RUNTIME_SKILL = Path("/Users/derek/.agents/skills/stardust-oa-people-review/SKILL.md")
if RUNTIME_SKILL.is_file():
    SKILL_PATHS.append(RUNTIME_SKILL)


def test_explicit_no_return_instruction_overrides_generic_resignation_return() -> None:
    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "explicit instruction from the current authorized approver says not to return the approval to a named person" in text
        assert "overrides the generic return-to-submitter route" in text
        assert "do not return the approval to that person" in text


def test_resignation_gap_routes_only_to_verified_responsible_target_or_stays_pending() -> None:
    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "Route a correctable gap only to a valid OA target" in text
        assert "whose responsibility for that gap is supported by evidence" in text
        assert "If no such target is established, keep the current task pending with `no_action`" in text
        assert "use `needs_human` for the unresolved routing decision" in text


def test_explicit_no_return_instruction_does_not_waive_handover_evidence() -> None:
    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "Do not treat the instruction as evidence that the handover gap is resolved" in text
        assert "do not approve solely because returning to the named person is prohibited" in text
        assert "Do not ask that person to repeat already verified material" in text
