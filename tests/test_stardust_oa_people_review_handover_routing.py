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


def test_substantive_recipient_comments_satisfy_the_corresponding_handover_evidence() -> None:
    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "OA comments and linked records are eligible evidence for handover receipt" in text
        assert "Match each confirmation to its recipient and the specific handover item" in text
        assert "a later substantive recipient confirmation supersedes an older ‘待确认’ entry" in text
        assert "Do not ask the applicant to repeat or reformat a confirmation already verified" in text
        assert "request only the unresolved item or missing detail" in text
        assert "A generic `AGREE` node alone does not replace item-level evidence" in text


def test_no_return_instruction_is_bound_to_the_identity_it_actually_names() -> None:
    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "Resolve the commenter and the person referred to by an explicit no-return instruction against OA identities" in text
        assert "do not infer that ‘me’ means the applicant or initiator" in text
        assert "The restriction excludes only the identified person" in text
        assert "If the prohibited person is ambiguous, keep the task pending with `no_action`" in text


def test_pending_offboarding_operations_need_a_safe_plan_not_premature_completion() -> None:
    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "Account and credential offboarding may be scheduled for the last working day or a controlled post-approval cutover" in text
        assert "Before approval, verify the responsible owner, timing, and dependencies; do not require a future operation to be complete" in text
        assert "For active shared or service credentials, use an owner-coordinated rotation and validation plan" in text
        assert "do not demand immediate revocation that would interrupt active service" in text
        assert "Do not put credential values in OA records" in text
        assert "If no safe owner-and-timing plan is documented, identify only that gap" in text


def test_preapproval_hiring_requisition_uses_business_line_decision_and_allows_new_role_absence() -> None:
    for path in SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "Recruitment approval is the **prerequisite** for the role to be created or appear in 小青" in text
        assert "the absence of a Xiaoqing record before approval is not contrary evidence" in text
        assert "The business-line owner's approval of the stated number is sufficient business approval" in text
        assert "project/budget evidence is consistent" in text


def test_org_structure_material_gap_is_asked_of_responsible_party_before_human_escalation() -> None:
    for path in [Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")]:
        text = path.read_text(encoding="utf-8")
        assert "No basis: request the missing business rationale from the submitter" in text
        assert "Do not escalate solely because the agent cannot independently verify" in text
        assert "business need, affected work and people, receiving arrangement, and reporting line" in text
        assert "Keep the approval pending and independently verify the response" in text
        assert "Use `needs_human` only for a genuine policy or authority gap" in text
        assert "If the given basis cannot be proven by evidence, `needs_human`" not in text
