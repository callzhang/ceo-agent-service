---
name: stardust-oa-people-review
description: Review Stardust DingTalk OA approvals for hiring, resignation, promotion, hiring requisitions (招聘需求), probation confirmation (转正), compensation changes (薪酬变更) and org-structure changes (组织架构变动) using current people, performance and handover evidence.
---

# Stardust OA People Review

Use together with `$dingtalk-oa-approval` for Stardust hiring, resignation, promotion, hiring-requisition, probation-confirmation, compensation-change and org-structure-change approvals. This Skill groups related people evidence and review mechanics; each approval type retains its own criteria and gaps. Do not transfer a rule from one type to another. The generic Skill owns cross-company evidence, scoring, workflow, and DingTalk action mechanics.

Read the current, applicable HR/performance source when one exists. Confirm source title, version/effective date and scope; historical feedback and recruiting methodology may add context but cannot create a company-wide approval threshold or action authority.

## Hiring

Read every substantive item required for this candidate and role, including the trial/probation objectives, resume, complete interview record/evaluation, hiring-team supplement, salary basis and OA remarks. When a candidate link is absent, search the candidate/interview system using available identity details and inspect the candidate's complete interview context; a missing link alone is not proof the source material does not exist.

Check that:

- probation objectives are measurable and tied to real current problems for the role/team;
- interview evidence addresses the problems and demonstrates the claimed skills, potential and role fit;
- role level is checked against the current job-leveling source;
- proposed compensation fits the salary standard below;
- current department/business context supports the stated role need and probation plan.

**Salary standard** (Derek, 2026-09-23). HR keeps a unified salary table; when one exists and covers the role, the proposed salary and level must match it. When there is no HR table for the role, **do the market research yourself** (Derek, 2026-09-23): search current postings and salary data for the same role, city and level on BOSS直聘, 猎聘, 职友集 and similar sources, record each source, the band it shows and the date, and judge which tier of that band the candidate's actual demonstrated performance (interview evidence, track record) supports. Do not return the approval to HR merely because they did not attach a salary basis; the market check is our work. Return to the submitter only when the proposed salary or level does not fit the HR table or the market band and tier you found, naming the gap and the sources; this is a material gap, not a rule gap, and is not escalated to `needs_human`. With this standard, salary and level are covered: an HR table or local-market band plus a tier judgment is the complete rule, so a case that meets it can reach `rule_coverage = 1.0`.

**Material the submitter supplies goes back to the submitter** (Derek, 2026-09-23). Salary basis, market comparison, leveling rationale, probation objectives and interview records are submitted material: when one is missing, return the approval asking for it. When the submitted material contradicts itself or the process state — for example an interviewer who explicitly did not recommend the candidate while the candidate system already shows an offer — the submitter must explain it: return with both facts named and ask for the explanation and the decision record behind it (generic decision table row 6). When the submitter's explanation points to a record — a hiring round-table, a recruiting stand-up's AI minutes, an interview-system note — find and read that record yourself (`dws minutes` by title and date for minutes) before deciding; judge the contradiction on what it says. Return for a link only after searching by name, date and attendees fails, and say what was searched. Do not escalate such a contradiction to `needs_human`; only a gap in the company's own rules or authority is escalated, independently, under the generic Skill's two-way handling.

**When required hiring material has not been fully read, take no workflow action** (Derek, 2026-09-23). Do not approve, return or reject. Unread trial objectives or interview records are almost always a reading failure on our side, not something the applicant failed to supply: the records usually exist in the candidate/interview system. Returning the approval would send a candidate's hiring back through the process for our own gap. So first search the candidate/interview system with the candidate's identity and read the complete interview context. If a required item still cannot be read after that, keep the approval pending, comment asking for an accessible copy or link naming the exact item, and escalate `needs_human` stating what was searched and what is still unread. This is the generic rule for unread material (generic Skill principle 5) applied to hiring, where the records most often live in the interview system.

## Resignation

Read the handover document, OA remarks/comments, and records for the receiving person, accounts and company assets. Verify handover items are specific and traceable, the recipient has acknowledged acceptance, and there is a concrete account/access and asset transfer or closure arrangement. A statement such as “已给” without a link or itemized receipt is not auditable evidence; ask for a traceable handover list or record.

This Skill does not yet define the completion standard, time-critical exceptions, or the authority of HR/manager/asset and account owners. An incomplete handover is a material gap: return it for a traceable handover record under the decision table. Do not infer approval authority from role title or past cases; escalate the exact unresolved branch with `needs_human`.

## Promotion

Read the full promotion plan, performance/history or feedback sources, meeting records, OA comments and attachments. Assess whether the evidence covers changed role responsibilities, demonstrated business outcomes, capability gaps, organizational collaboration, management methods, risk anticipation and measurable follow-up goals. Search current materials about the person, role and department to ground the proposed plan in actual needs.

The 2026 performance policy may inform performance evidence, but where it delegates grade/level change to other policies (including the leveling and promotion rules), retrieve those primary sources before applying them. Do not substitute role proposals or historical feedback for promotion policy.

The current source set does not establish eligibility thresholds, time-in-role, cross-level exceptions, leveling/salary adjustment authority, exception approvers or approve/return/reject mappings. Where these matter, `rule_coverage < 1.0`; use `needs_human` and name the missing primary policy/authority.

## Hiring requisition (招聘需求申请) (Derek, 2026-09-24)

Decide from the **hiring list** and the **interview situation**, both read from the 小青 interview system (`xiaoqing_interview` MCP), not from the applicant's own account. The list is the hiring plan (Derek: "招聘清单就是计划").

Where it is: call `get_dashboard_stats` with `period=all` (it also takes `jobStatus`, `priority`, `recruiter`, `jobId` filters). `data.job_process` is one row per job: `job_id`, `job_title`, `priority` (P0/P1), `recruiter`, and stage counts `total_count`, `resume_qualified_count`, `interviewed_count`, `first_interview_count`, `second_interview_count`, `third_interview_count`, `written_test_count`, `offer_count`, `awaiting_entry_count`, `abandoned_entry_count`, `onboarded_count`, plus `progress_summary` (for example "等待二面 1人"). Read the interview situation for a role with `search_candidates` (`job_title`, `stage`, `round`) and `list_candidate_interviews`.

What the list does **not** contain: requested headcount, approved HC, remaining gap, target hires, or job open/closed status. `total_count` is the number of candidates, never a headcount. Do not treat it as one. The list is also scoped to what the logged-in user can see.

How to judge:
- Match the requisition to a job by `job_title`. The role is in the list and its pipeline supports the request (candidates in the stages the request implies, no unexplained gap) → the requisition is supported.
- The role is **not** in the list → the plan does not contain it. Return it to the submitter to say which plan entry it belongs to; do not infer one.
- The request states a headcount, or a level the list cannot show → that part cannot be verified from 小青: `needs_human`, naming the headcount claim.

The hiring list and interview situation are read by us, not material the applicant supplies, so a missing one is never returned to the applicant. If the 小青 MCP cannot be reached (401 / needs login) or the tool returns nothing, that is a reading failure: report it and `needs_human`, naming exactly what could not be read. Never approve or reject on the applicant's description of the plan.

## Probation confirmation (转正申请) (Derek, 2026-09-24)

Read the probation assessment standard set when the person was hired and the record of what was actually completed. **Every standard item must correspond one-to-one to a completion record, and there must be a recorded review approval.** An item with no completion record, a completion record that answers no standard item, or no review-approval record is a material gap: return it to the submitter naming the item. Do not confirm on a general statement that the probation went well.

## Compensation change (薪酬变更申请) (Derek, 2026-09-24)

Require both a **salary-adjustment basis** and a **trial-run assessment standard**, and judge whether the change is reasonable against them and the salary evidence in the hiring section (HR table, or the market band you research yourself). Either missing is a submitted-material gap: return it. Both present but the change is not supported by them (out of band, or not tied to the assessed result) is an unmet standard: table row 8.

## Org-structure change (组织架构变动申请) (Derek, 2026-09-24)

The applicant must give the basis for the change. **No basis: return it.** A basis given: verify it against actual evidence (headcount and reporting records, business need, the affected people and work). **If the given basis cannot be proven by evidence, `needs_human`**, naming the claim that could not be proven. Do not accept the basis on the applicant's assertion.

## Shared evidence and result contract

Keep the applicant's claims separate from independently verified records. For each case report the live process identity and people-review type; full materials read/unread; applicable primary sources and their version/effective-date status; facts, evidence and relevant historical/context material; `information_completeness`, `rule_coverage`, risk and confidence; the applicable type-specific branch; action authority and mapping; applicant-resolvable question; and each policy gap requiring `needs_human`.

Do not call the category 100% covered because its evidence checklist is detailed. A case can only have `rule_coverage = 1.0` when this Skill's criteria for its exact type were each checked against the material and held. Actions follow the decision table in `$dingtalk-oa-approval`: this Skill supplies the criteria a case is judged against, and the table turns the result into approve, return, comment, reject or `needs_human`. A category not having its own action list is not a rule gap. Keep correctable material gaps separate from rule gaps and follow the generic Skill's return-first and independent-escalation rules.

