---
name: stardust-oa-attendance-travel-review
description: Review Stardust DingTalk OA approvals for leave, business travel and local outings using current policy, schedule and purpose evidence.
---

# Stardust OA Attendance and Travel Review

Use together with `$dingtalk-oa-approval` for Stardust leave, business-travel and outing approvals. The shared category reflects evidence overlap only; do not transfer approval criteria across leave, travel and outing. The generic Skill owns cross-company evidence, scoring, workflow and DingTalk action mechanics.

Treat the applicable criteria in this Skill as the runtime rules for leave, travel and outing reviews. Do not search for or read the background OA-principles document to supply policy. Read an external policy only when this Skill explicitly requires it for a case; if such a required source is unavailable, or this Skill lacks a decision-relevant threshold, exception or authority, mark rule coverage below 1.0 and route the policy gap to `needs_human`.

## Leave

Read the leave type, dates/duration, reason and relevant schedule/handover context. Approve a leave request shorter than 3 days whose reason is reasonable, whose dates are stated, and whose work handover is arranged; when commenting, be considerate and remind the applicant to arrange handover.

**Leave of 3 days or more needs a very good reason** (Derek, 2026-09-23). Count the requested days from the form, including half days. Approve only when the reason is specific and strong: it says why this much time is needed and why it cannot be shorter or moved. A generic reason ("个人事务", "休息", "家里有事" with nothing more) is not enough — comment asking the applicant for the specific reason and do not approve; this is not grounds to reject. If the reason is still not specific after the applicant answers, escalate `needs_human` with the reason quoted. Leave-type eligibility, available balance and evidence requirements are not yet defined here: they block only a case that actually turns on one of them.

Do not use performance-management clauses about long illness, maternity or special performance treatment as leave-approval authority; those clauses govern performance treatment unless a primary leave policy explicitly says otherwise. If policy criteria affect the decision and are unavailable/incomplete, set `needs_human` and keep `rule_coverage < 1.0`.

### Handover person — preconditions (Derek, 2026-09-23)

For leave, and for business travel that names someone to cover the work, two things must hold **before** the request can be approved. Both are preconditions, not factors to weigh:

1. **The handover person is capable of the work being handed over.** Look the person up (`dws contact user get --ids <userId>`) and compare their role, department and level with the work the applicant is leaving. Someone in an unrelated function, or clearly junior to work that needs judgement, does not qualify. When they do not, comment naming the mismatch and ask for a suitable handover person; do not approve.
2. **The handover person has already agreed in this approval flow.** Find their own `AGREE` in the operation records. A handover person who has not yet agreed — because the flow has not reached them, or they have not acted — means the request is waiting on someone else: do not approve; comment stating whose agreement is outstanding, and wait. Their name appearing in the form is not agreement.

If no handover person is named at all, comment asking for one; do not approve.

## Business travel

Read the current travel standard and verify purpose/customer or opportunity evidence, visit plan, itinerary/dates, budget and expected result. Compare expenses with the current applicable limits by city, level and cost type, and inspect any exception authorization. Active travel limits, budget ownership and exception approvers are not yet defined here; a trip whose expenses need a limit comparison is blocked on that limit, not on an action list.

If a correctable applicant fact or attachment is missing, ask for that item and follow generic return-first handling. If the standard, limit, exception or authority is missing, that is a separate policy gap requiring `needs_human`; do not treat a quote or applicant assurance as company policy.

## Outing

Read the purpose, location, date/time, business relevance and effect on critical work. **An outing is not business travel and is held to a much lighter standard** (Derek, 2026-09-23). Approve an outing when its business value is clear from the purpose stated. Do not apply the travel checks — standards, budget, itinerary, expense limits — to an outing. When the business value cannot be told from what is written, ask in a comment; that is not grounds to reject. Business travel below stays strict. If an uncovered boundary is material to the decision, `rule_coverage < 1.0` and `needs_human`.

## Required result and open gaps

For each case report live process identity and subtype; material and schedule/expense evidence read; source, version/effective date, scope and exact clauses; facts versus applicant assertions; `information_completeness`, `rule_coverage`, risk and confidence; applicable limits/exceptions/authority; applicant question if a factual item is missing; and each unresolved policy field requiring `needs_human`.

Known missing source fields include: current effective leave policy; active travel limits and exception matrix; outing-vs-travel classification and reasonableness boundaries; authorized decision makers; and approver authority. Actions follow the decision table in `$dingtalk-oa-approval`: this Skill supplies the criteria a case is judged against, and the table turns the result into approve, return, comment, reject or `needs_human`. A category not having its own action list is not a rule gap. Keep applicant-resolvable material gaps separate from company-policy gaps.
