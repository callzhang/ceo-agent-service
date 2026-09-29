---
name: stardust-oa-finance-review
description: Review Stardust finance-led DingTalk OA approvals against the criteria in the rule card matched to the live approval definition; actions follow the generic OA decision table.
---

# Stardust OA Finance Review

Use this Skill together with `$dingtalk-oa-approval` only for the finance-led OA templates registered in [references/template-registry.md](references/template-registry.md).

**Scope gate — check this first, before anything else in this Skill.** Look up the approval's exact `processCode` in the registry. **If it is not there, this Skill does not apply to the approval at all**: stop reading this Skill, apply nothing from it, and decide the approval entirely under `$dingtalk-oa-approval` and its decision table, scoring `rule_coverage` from the written policy that governs that approval type. A missing registry row is **not** a rule gap and is **not** grounds for `needs_human`. Every rule below, including the invariant that the rule card is the only source of action authority, governs the registered finance templates only.

Why this is spelled out: this Skill is bound to the general OA scheduled task, so it is loaded for every approval. On 2026-09-22 a 软件算法项目需求评估 (POC) and an 其他合同审批 — neither a finance template — were both stopped at `needs_human` for "no rule card matching the live processCode". Neither was ever meant to have one. The scheduled-task prompt selects this Skill; it must not duplicate monetary limits, approvers or exceptions.

## Scope and ownership

- Resolve an approval with the exact live `processCode`, never title matching. The display name is only a cross-check.
- The 11 registered templates are `供应商付款申请`, `费用报销对外付款综合审批单`, `备用金申请`, `差旅报销-总裁办专用`, `【停用】运营部付款申请`, `资产入库申请`, `资产出库申请`, `资产处置申请单`, `月度收入确认审批`, `特批事项申请审批`, and `国际律师咨询使用事前审批`.
- `云资源费用&服务器使用审批` is reviewed by `$stardust-oa-cloud-resource-review` as a change against a resource plan. Do not treat it as finance-led merely because it has cost; this Skill may only be consulted for an explicit budget gate.
- The generic Skill defines cross-company mechanics and **all actions**. This Skill defines Stardust-specific template scope and the review criteria for each registered template.

## Criteria from the card, actions from the decision table (Derek, 2026-09-24)

A rule card supplies the **criteria** a registered template is judged against: the decision conditions it actually sources (for example, for `supplier_payment`: customer-confirmed delivery, settlement condition, amount/cost confirmation, effective contract, CRM, invoice/settlement/payee/contract consistency). **Actions follow the complete decision table in `$dingtalk-oa-approval`**, exactly as for every other approval type. A card's `partial` status, or its old action column, does not forbid approve, return, comment or reject.

- Every sourced condition checked and held, and the case does not fall into anything the card leaves unsourced → `rule_coverage = 1.0`; with complete information and confidence the table approves it.
- A sourced condition not met because the applicant did not supply something → return for it (table row 4).
- The case falls into a branch the card leaves unsourced (an overseas or advance payment, a tax exception, an amount above a limit the card does not define, a conflict between approvers) → `rule_coverage < 1.0` for that branch only, and `needs_human` naming it. An unsourced branch the case does not touch is not a gap.
- A template whose card sources no decision condition at all has no criteria: `rule_coverage = 0`, table row 3.

The form schema, an old decision, an applicant assertion, an Agent memory, a search snippet, an informal message, or a scheduled prompt cannot invent a criterion the card does not source.

## Ordered review algorithm

1. Read the latest OA detail, current `RUNNING` task, operation records/comments, every accessible attachment, and linked business records. Reread current state after any applicant update.
2. Resolve the exact `processCode` through [template-registry.md](references/template-registry.md). The scope gate above has already established that a row exists. Do not borrow a card from a similar template: a registered template whose own card is missing or incomplete is a rule gap and escalates; an unregistered template is outside this Skill and never reaches this step.
3. Read the matching source-register row and its rule card in [finance-rule-cards.md](references/finance-rule-cards.md). Preserve source title, locator and version/effective date in the review record.
4. Build five explicit ledgers for this case: **facts**, **evidence**, **rules**, **authority**, and **action**. Keep applicant-provided facts distinct from independently read records.
5. Compute `information_completeness` from material actually read and `rule_coverage` from whether the card's sourced conditions cover this exact case (see above). A `partial` card yields `rule_coverage = 1.0` when the case stays within its sourced conditions.
6. Choose the action from the generic decision table: approve when every sourced condition holds; return for applicant-resolvable missing material; escalate `needs_human` only for an unsourced branch the case actually falls into, together with any return under the generic two-way handling.
7. Reread DingTalk after the action.

## Required result contract

State all of the following in the review result:

- `process_code`, `template_key`, card status, source title/version and the card branch reviewed;
- fact/evidence ledger, including each unread or unavailable substantive item;
- rule/authority/action ledger, including every uncovered field or conflict;
- `information_completeness`, `rule_coverage`, risk and confidence, which must be mutually consistent;
- applicant comment question and provider readback, if a material gap was commented;
- the separate `needs_human` reason when the case falls into an unsourced branch.

Never call a case covered solely because its registry identity or form schema matched. It reaches `rule_coverage = 1.0` when each sourced condition was checked against the case and held, and the case touches no unsourced branch.

## Gap handling

| Case | Applicant-facing step | Internal conclusion |
| --- | --- | --- |
| Every sourced condition holds, no unsourced branch touched | none | approve (decision table) |
| Missing applicant-resolvable material | Return for the named material | return (decision table row 4) |
| Case falls into an unsourced branch (exception, limit, conflict) | Do not ask applicant to invent policy | `needs_human` for that branch |
| Both material gap and unsourced branch | Return for the material and escalate the branch in the same result | `needs_human` after the return executes |
| Card sources no decision condition | Ask only for a known rule location if applicable | decision table row 3 |

## Stopped operating-payment template

For `【停用】运营部付款申请`, first verify whether the form is retired and read a current migration or exception policy. A readable definition or a historic payment rule is not permission to follow historic normal-payment behavior. New or ambiguous instances always remain `needs_human` until the current policy explicitly binds them.

## Rule-card maintenance

To extend a card, update its source-register row and rule card with the actual original source and version/effective date: scope/identity, required facts/evidence, decision conditions and values, exceptions/expiry. Actions are not a card field; they come from the generic decision table. Change a card as a versioned policy change; do not backfill it from prior cases.
