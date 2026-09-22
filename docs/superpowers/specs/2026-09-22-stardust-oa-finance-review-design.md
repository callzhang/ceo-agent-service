# Stardust OA Finance Review Design

## Status

Approved design. This document defines the intended approval-policy architecture only. It does not itself authorize a DingTalk action, change a scheduled task, or claim that any financial approval is currently eligible for automatic execution.

## Goal

Create one company-specific Skill, `stardust-oa-finance-review`, that contains Stardust's complete financial approval rules for every current finance-led DingTalk OA template. It will run together with the cross-company `dingtalk-oa-approval` Skill.

The system must never confuse:

```text
an OA template is registered in the Stardust finance Skill
with
the actual approval case has complete, applicable rules and rule_coverage = 1.0
```

## Confirmed decisions

1. `dingtalk-oa-approval` remains a cross-company generic Skill. It defines evidence-reading, material and rule coverage, escalation, action verification, and the distinction between a missing fact and a missing rule. It contains no Stardust monetary amounts, organization-specific authorization roles, policy exceptions, or action thresholds.
2. `stardust-oa-finance-review` is the single company-specific source for Stardust financial approval rules. It contains template-level conditions, thresholds, exceptions, authorization boundaries, and action mappings.
3. A source document, policy table, DingTalk document, or business system cited by `stardust-oa-finance-review` is evidence for the Skill's rules and version. It is not a competing, ad-hoc fourth policy layer that an Agent must reconstruct during a case.
4. The scheduled-task prompt activates the two Skills and identifies the operating scope. It must not duplicate template-level decision rules or action mappings.
5. For a case with both applicant-resolvable gaps and a rule, exception, authorization, or action-mapping gap, the Agent acts in parallel: it comments to the applicant about facts, materials, or a known rule source they can provide, and it records `needs_human` for the rule gap. An applicant response cannot itself create or validate a missing Stardust rule.
6. The first company Skill covers every current finance-led OA template, including a template that is marked stopped. A stopped template has an explicit inactive handling rule; it is not silently excluded from the inventory.
7. The Skill is the single source for whether a matched finance case can be approved, returned, rejected, commented on, or escalated. The generic Skill supplies the mechanics, not a second action policy.

## Scope

### Included finance-led OA templates

The current inventory contains these eleven templates. The implementation must bind each card to the stable DingTalk approval definition identifier or `processCode`; the Chinese name is a display label and must not be the sole identity key.

| Module | Current template | Required inventory state |
| --- | --- | --- |
| Outgoing funds | 供应商付款申请 | registered |
| Outgoing funds | 费用报销对外付款综合审批单 | registered |
| Outgoing funds | 备用金申请 | registered |
| Outgoing funds | 差旅报销-总裁办专用 | registered |
| Outgoing funds | 【停用】运营部付款申请 | retired or active only after current status is verified |
| Asset control | 资产入库申请 | registered |
| Asset control | 资产出库申请 | registered |
| Asset control | 资产处置申请单 | registered |
| Revenue and exceptions | 月度收入确认审批 | registered |
| Revenue and exceptions | 特批事项申请审批 | registered |
| Professional services | 国际律师咨询使用事前审批 | registered |

### Explicit cross-domain boundary

`云资源费用&服务器使用审批` has a cost component but is currently a technical-resource and access-control template. It is not one of the eleven finance-led templates above. A future Stardust access/change Skill owns its primary decision, but must request or enforce its financial-budget gate through an explicit cross-domain contract. It must not be silently treated as a finance template merely because it includes spend.

## Skill layout

The company Skill lives beside the other installed user Skills:

```text
/Users/derek/.agents/skills/stardust-oa-finance-review/
├── SKILL.md
└── references/
    ├── template-registry.md
    ├── source-register.md
    └── finance-rule-cards.md
```

`SKILL.md` is the entry point and carries the binding operational rules. The reference files remain part of the same company-specific Skill; they exist only to keep the entry point readable while preserving a stable source-to-rule trail.

The related generic Skill remains:

```text
/Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md
```

## Responsibilities by layer

| Layer | Owns | Must not own |
| --- | --- | --- |
| `dingtalk-oa-approval` | Latest-record reading, attachment/link handling, `information_completeness`, `rule_coverage`, applicant questions, `needs_human`, action readback, evidence reporting | Stardust payment amounts, tax exceptions, finance roles, approval authorities, or template-specific decisions |
| `stardust-oa-finance-review` | Template identity, complete Stardust rule cards, source/version bindings, thresholds, exceptions, authorizations, action mappings, finance-specific evidence requirements | A generic replacement for DingTalk OA mechanics or company-agnostic policy |
| Scheduled-task prompt | Load both Skills, select the financial OA operating scope, prohibit unlisted policy sources from being silently treated as rules | A duplicate copy of Stardust finance decision thresholds or action mappings |
| Original source material | Evidence and version authority for a Stardust rule card | An implicit action policy if it has not been incorporated and verified in the Stardust Skill |

## Rule-card contract

Every finance template has one card. A card is not active until every required field has an authoritative Stardust value and source.

```text
identity
  - DingTalk definition ID / processCode
  - current display name and aliases
  - Stardust legal/entity scope
  - active, retired, or pending-source status

business boundary
  - what this OA controls
  - included and excluded business situations
  - cross-domain handoffs

evidence requirements
  - required form fields
  - mandatory attachments and linked documents
  - required approval-history/comments checks
  - named external system records, when applicable

Stardust decision rules
  - business, budget, contractual, tax, accounting, asset, or legal conditions
  - amount/period/unit thresholds
  - prohibited situations and fact-validation rules

exceptions
  - permitted exception types
  - supporting evidence
  - exception expiry and scope
  - required exception authorizer

authority
  - business reviewer, financial reviewer, legal reviewer, asset owner, and final authorizer
  - delegation conditions and conflicts of interest

action mapping
  - conditions for approve
  - conditions for return
  - conditions for reject
  - conditions for comment and await
  - conditions for needs_human

source and verification
  - original Stardust source location
  - version/effective date
  - rule owner
  - latest verification date

test scenarios
  - compliant case
  - missing material
  - missing rule
  - permitted exception
  - non-compliant case
  - conflicting facts or authorization
```

No prior case, informal oral statement, Agent memory, or applicant assertion can substitute for a missing field in a rule card.

## Rule-card readiness and case-level coverage

The implementation distinguishes the following inventory states:

| State | Meaning | Case result |
| --- | --- | --- |
| `registered` | The template identity and target rule-card structure are known. | No claim that its rule set is complete. |
| `source_pending` | A needed source or authority is unavailable or unread. | `rule_coverage = 0`; request applicant-resolvable evidence if applicable and set `needs_human`. |
| `partial` | One or more thresholds, exceptions, authorities, or action mappings are incomplete. | `rule_coverage < 1.0`; no automatic action based on the missing branch. |
| `active` | The card has complete Stardust rules, verifiable sources, and test scenarios. | A specific case may reach `rule_coverage = 1.0` only if its facts match the card. |
| `retired` | The form or rule is stopped or superseded. | Do not execute the historic action path; verify the current status and escalate or follow the explicit migration rule. |

For a case, `rule_coverage = 1.0` requires all of the following to match the current facts:

1. a verified active template identity;
2. the applicable Stardust rules and source version;
3. all required facts and evidence conditions;
4. thresholds, units, and time boundaries;
5. exception or special-approval paths;
6. authorization and conflict-of-interest boundaries; and
7. a rule-mandated action mapping.

If any of these is absent, ambiguous, conflicting, or outside the case branch, the Agent must not claim full coverage or derive a policy from experience.

## Finance modules and required decision dimensions

### A. Outgoing funds

| Template | Rule card must determine |
| --- | --- |
| 供应商付款申请 | Payee identity and receiving account; contract or order; payment milestone; delivery/acceptance condition; amount/currency; budget or project attribution; invoice or permitted substitute; prepayment, emergency payment, and related-party treatment; required authorizers; action for each condition. |
| 费用报销对外付款综合审批单 | Expense truth and business necessity; cost attribution; claimant/payee relationship; receipts and tax treatment; time limit; reimbursement or external-payment conditions; no-invoice, over-limit, emergency, and advance-settlement exceptions; reviewers and action mapping. |
| 备用金申请 | Purpose; budget/project; amount and currency; custody owner; drawdown period; settlement/return conditions; renewal or overdue treatment; authorization and action mapping. |
| 差旅报销-总裁办专用 | Business justification; itinerary; cost categories; limits; receipts; special counterpart, customer, or accompanying-person circumstances; total-office-specific authorization; late or exceptional reimbursement handling. |
| 【停用】运营部付款申请 | Whether the definition is truly stopped; handling of a pre-stop in-flight case; whether new submissions are returned or migrated; who may authorize an exception. It must never be silently treated as a normal payment card. |

### B. Asset control

| Template | Rule card must determine |
| --- | --- |
| 资产入库申请 | Procurement source; contract/order; acceptance; asset identity and classification; valuation; custodian; ledger recognition; discrepancy and partial-receipt handling. |
| 资产出库申请 | Asset identity; receiving person/entity; use; custody/return responsibilities; transfer or external handoff; asset-ledger update; authorizer. |
| 资产处置申请单 | Asset condition; disposal rationale; valuation or price basis; method; data erasure or handover; proceeds; related-party risk; asset and finance authority. |

### C. Revenue and exceptions

| Template | Rule card must determine |
| --- | --- |
| 月度收入确认审批 | Contract; performance obligation; delivery and acceptance; recognition period; amount/currency; deferral or adjustment; customer evidence; accounting review and authorization. |
| 特批事项申请审批 | The base rule not met; business rationale; alternatives; amount and risk; one-time scope; expiry; specific exception authorizer; action if an exception is refused or incomplete. |

### D. Professional services

| Template | Rule card must determine |
| --- | --- |
| 国际律师咨询使用事前审批 | Legal matter scope; provider identity; conflict check; budget and pricing; confidentiality; needed deliverable; engagement authority; emergency path; action mapping. |

These rows define questions that a Stardust rule card must answer. They are not invented approval criteria or permission to take an action before source rules are captured.

## Decision and dual-action model

The generic Skill supplies a two-channel response model that `stardust-oa-finance-review` applies to each card:

| Finding | Applicant-facing action | Authorization-facing action | Result |
| --- | --- | --- | --- |
| Missing or unclear case fact/material | Ask one concise, answerable question or use the card's return action. | None unless the card requires escalation. | Await information or return as mapped. |
| Missing/ambiguous company rule, exception, authority, or action mapping | Ask only for a source the applicant can reasonably provide, if one exists. Do not ask the applicant to invent company policy. | Describe the missing policy branch and set `needs_human`. | `needs_human`. |
| Both kinds of gap | In one comment, request the applicant-resolvable facts/materials/source. | Simultaneously create the precise `needs_human` explanation. | Comment issued and `needs_human` remains active. |
| Complete rules and facts satisfy the card | No unnecessary question. | Execute only the card-mapped action. | Proposed action and mandatory DingTalk readback. |

The service representation must preserve both effects: a comment receipt cannot erase the unresolved `needs_human`, and an applicant update triggers a new review of the latest OA state rather than reusing a prior conclusion.

## Scheduled-task prompt contract

The scheduled prompt is intentionally short and contains no duplicate Stardust policy. Its required semantics are:

```text
Use $dingtalk-oa-approval and $stardust-oa-finance-review for Stardust
finance-led DingTalk OA tasks.

Match the actual approval definition to a finance rule card. Use only the
current, verified rule card and its cited Stardust sources. Do not infer policy
from historical approvals, an applicant's assertion, Agent memory, or a source
not made part of the company Skill.

For applicant-resolvable gaps, comment with the applicable concise question.
For a rule, exception, authorization, or action-mapping gap, simultaneously
record needs_human. Execute no action except the one mapped by the matched
stardust-oa-finance-review card, then reread DingTalk to verify the effect.
```

The prompt's job is skill selection and scope binding. The active finance Skill is the single source of template-specific action authority.

## Runtime integration requirements

Writing a Skill and updating a seed prompt is insufficient. The actual OA child task must receive the active scheduled consumer/prompt context. The implementation must prove this path end to end:

```text
Scheduled OA task
  -> OA scanner discovers an approval
  -> generated OA child task carries scheduled-consumer context
  -> worker renders the selected generic and Stardust finance Skills
  -> Consumer/Audit receives the same approval-policy context
  -> DingTalk action/comment is executed only when the matched card authorizes it
  -> DingTalk instance is reread and receipt recorded
```

Existing scanner text that treats an applicant's latest statement as authoritative, or says it is unnecessary to reread current materials, conflicts with this design and must be removed rather than preserved alongside the new policy.

## Verification and release criteria

The work is ready only when all of the following are separately evidenced:

1. The eleven templates are resolved to stable DingTalk definition IDs and their current active/retired state is read back.
2. Each template has a rule card and source register entry.
3. Every rule card marked `active` has complete conditions, exceptions, authority, action mapping, source version, and test scenarios.
4. A template with `source_pending` or `partial` produces `needs_human`, not an invented conclusion or `rule_coverage = 1.0`.
5. Material gaps, rule gaps, and combined gaps produce the designated comment/escalation behavior and retain both records.
6. The stopped operating-payment template never takes a historic normal-payment path without an explicit current policy.
7. The actual OA child task receives the selected Skill context; a seed-only unit test is not sufficient.
8. Each DingTalk comment, return, approval, or rejection is reread from the real OA instance and reconciled to a provider receipt.
9. No Skill, prompt, test fixture, or runtime fallback embeds an unstated Stardust rule derived from an old case or Agent memory.

## Out of scope for this design

- Creating the Skill files or populating them with unverified financial thresholds.
- Changing DingTalk OA state, sending comments, or approving/rejecting current live cases.
- Defining the primary policy for cloud-resource/server-use approvals.
- Treating an incomplete historical policy document as a complete finance rule source.
- Creating a separate Skill for each of the eleven templates.
