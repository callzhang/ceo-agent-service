# Stardust Finance OA Rule Cards

These cards turn known sources into the review criteria for each registered template (Derek, 2026-09-24). **Actions are not taken from these cards**: they follow the complete decision table in `$dingtalk-oa-approval`. The last column of the matrix below is kept only as history and is superseded; a card's `partial` status means some branches are unsourced, not that actions are forbidden. The authorization column is not a gap either: an approval that reached Derek's running task is there for Derek to decide, and the approval flow is the authority.

## Shared execution contract

- Match by the exact `processCode` in [template-registry.md](template-registry.md), never by title alone.
- Read current OA detail, task, records/comments, every accessible attachment and linked record before reaching a card.
- **Compliant:** every sourced condition held and no unsourced branch touched → `rule_coverage = 1.0`; approve under the decision table.
- **Material gap:** return for the named applicant-resolvable material (decision table row 4).
- **Unsourced branch the case falls into (exception, limit, conflict):** `needs_human` for that branch only; do not ask the applicant to create policy.
- **Non-compliance:** a sourced condition verified as not met → decision table rows 5/8.
- **"Missing policy" items that name approval authority, action authority or action mapping are not gaps.** Authority is the approval flow reaching Derek's task; actions come from the decision table. Only the missing thresholds, exceptions and conditions in those lines can be unsourced branches.
- **Terms the contract already fixes are not exceptions.** When the effective contract or work order states how the amount is settled — for example a 3% special-invoice tax point with payment at 97% of the tax-inclusive amount — and the invoice, settlement and payment amount follow it, that is the amount/contract consistency condition holding, not a tax exception. Only a treatment the contract does not provide for (an overseas payee, an advance, no invoice, an amount the contract does not support) is an unsourced branch.

## Card decision, exception, authority and action matrix

This matrix is part of every named card below. It makes the absence of authority explicit rather than leaving it implicit in prose. The authoritative source-row link for every `template_key` is the identically named row in [source-register.md](source-register.md); no row is `active`.

| template_key | Stardust decision conditions actually sourced | exceptions / expiry | authorization and conflict boundary | (superseded 2026-09-24; history only) old action mapping |
| --- | --- | --- | --- | --- |
| supplier_payment | customer-confirmed delivery, settlement condition, amount/cost confirmation, effective contract, CRM, invoice/settlement/payee/contract consistency | overseas, advance, tax and any other exception unsourced | approval authority and conflict resolver unknown | approve/return/reject not authorized; comment only for listed material; `needs_human` for all policy/action decisions |
| external_expense_payment | invoice/proof/timeliness plus payment or travel evidence for the actual branch | branch limits, budget and tax exceptions unsourced | authority/conflict resolver unknown | approve/return/reject not authorized; comment only for listed material; `needs_human` otherwise |
| advance_fund | only travel or administrative procurement; known year-end / five-working-day lifecycle | amount, eligibility and overdue exceptions unsourced | authority/conflict resolver unknown | approve/return/reject not authorized; comment only for listed material; `needs_human` otherwise |
| executive_travel_reimbursement | trip approval, itinerary, invoices, hotel/payment evidence and stated filing timing | executive-office limits and exceptions unsourced | authority/conflict resolver unknown | approve/return/reject not authorized; comment only for listed material; `needs_human` otherwise |
| retired_operations_payment | no current condition is sourced | retirement/migration and every business exception unsourced | all authority unknown | no final action; comment for migration evidence if applicant can provide it; always `needs_human` |
| asset_intake | no template-bound decision condition is sourced | all exceptions/expiry unsourced | all authority/conflict handling unknown | no final action; comment for procurement/acceptance/custody evidence; always `needs_human` |
| asset_release | no template-bound decision condition is sourced | all exceptions/expiry unsourced | all authority/conflict handling unknown | no final action; comment for identity/use/custody evidence; always `needs_human` |
| asset_disposal | only the limited 36-month bad-debt provision condition is sourced, not fixed-asset disposal | disposal, valuation, related-party and method exceptions unsourced | management is named for limited bad debt; template authority unknown | approve/return/reject not authorized; comment for source material; `needs_human` for branch/action |
| monthly_revenue_recognition | stated customer acceptance basis and signed income-confirmation pack | adjustment, reversal, cross-border/currency exceptions unsourced | authority/conflict resolver unknown | approve/return/reject not authorized; comment for named evidence; `needs_human` otherwise |
| special_matter | only a limited first-curve, below-25%-gross-margin special-approval condition is sourced | binding, other categories, duration and exceptions unsourced | management owner is required in limited rule; template authority unknown | approve/return/reject not authorized; comment for cited exception evidence; `needs_human` otherwise |
| international_legal_preapproval | no template-bound decision condition is sourced | all conflicts, urgent cases, budget/rate exceptions unsourced | legal/finance authority and conflict resolver unknown | no final action; comment for scope/quote/entity evidence; always `needs_human` |

## supplier_payment

Identity: `PROC-053E56DA-97A6-4888-82FF-2890B75A752D`; status `partial`; source `supplier_payment` in [source register](source-register.md). Scope: supplier settlement.

Verified facts/evidence: customer-confirmed delivery; settlement condition; cost confirmation versus payment amount; effective contract; CRM number; invoice, settlement statement, payee and contract consistency; account details. Missing policy: current-template binding, exceptions including advance/overseas/tax, authorization and action map. Applicant question: “请补充本次结算对应的客户验收、结算单、有效合同及 CRM 编号链接。” Human escalation: source owner must confirm exceptions, authority and final action.

Actions: generic decision table (see the shared execution contract above).

## external_expense_payment

Identity: `PROC-BD20D34C-9D4C-4768-B699-2DDB9900B7D4`; status `partial`; source `external_expense_payment`. Scope: reimbursement/direct-payment branches.

Verified facts/evidence: correct invoice type/time, original proof, applicable bill/contract, payee, amount, payment entity and travel materials where applicable. Missing policy: branch-to-rule binding, budget/limit and tax exceptions, authority and action map. Applicant question: “请补充本次支出对应的发票、付款凭证、账单及适用合同/出差审批链接。” Human escalation: determine applicable branch and final action.

Actions: generic decision table (see the shared execution contract above).

## advance_fund

Identity: `PROC-8ACB3743-C5A4-4983-A18F-350ED2F4F979`; status `partial`; source `advance_fund`. Scope: travel or administrative-procurement advances only.

Verified facts/evidence: type is travel/administrative procurement; linked trip approval for travel; amount, recipient account, requested/repayment date, purpose and supporting material. Known lifecycle: travel write-off within five working days after return; administrative advance returned by 31 December. Missing policy: amount/eligibility limits, overdue handling, authority and action map. Applicant question: “请补充关联出差审批或行政采购依据，并说明本次借款用途和预计核销/归还日期。” Human escalation: validate limits, exceptions and action.

Actions: generic decision table (see the shared execution contract above).

## executive_travel_reimbursement

Identity: `PROC-8E6C95A9-9C71-4870-ABAF-5A1C45705DFC`; status `partial`; source `executive_travel_reimbursement`. Scope: executive-office travel reimbursement.

Verified facts/evidence: one linked trip approval, itinerary, invoices, hotel water bill where applicable, payment proof, travel purpose and timely filing. Missing policy: executive-office standard/limits, exceptions, authorization and action map. Applicant question: “请补充关联出差审批、行程单、发票/酒店水单及付款凭证。” Human escalation: confirm executive-office limits and decision authority.

Actions: generic decision table (see the shared execution contract above).

## retired_operations_payment

Identity: `PROC-99E764BF-3BB8-44B5-AE5B-D6C2E7C66CD7`; status `source_pending`. Scope: a template labelled stopped although its definition endpoint still returns `PUBLISHED`.

Required facts/evidence: current process status, an explicit migration/exception policy, linked current template and its rule source. Missing policy: all current approval rules and action authority. Applicant question: “请提供本单被允许继续使用的当前迁移/例外依据及对应现行流程链接。” Human escalation: confirm whether any action is permitted. Six cases: compliant/material gap/rule gap/exception/non-compliance/conflict → `needs_human` (material gap may additionally receive the stated comment).

## asset_intake

Identity: `PROC-4EC90A36-CFDA-4FAC-A2F1-C79D52BA1899`; status `source_pending`. Scope: asset/data-set intake.

Required facts/evidence: source procurement/contract, acceptance, asset identity/classification, quantity/cost, storage/custodian, ledger record and required product instructions. Missing policy: all template-bound criteria, exceptions, authority and action map. Applicant question: “请补充采购/验收依据、资产分类与保管/台账记录链接。” Human escalation: establish the asset-intake rule. Six cases: compliant/material gap/rule gap/exception/non-compliance/conflict → `needs_human` (material gap may additionally receive the stated comment).

## asset_release

Identity: `PROC-676ED488-169D-43B3-A144-CF253198374A`; status `source_pending`. Scope: asset/data-set release, use, transfer or sale.

Required facts/evidence: asset identity, recipient/use, customer/contract if sold, custodian/return or external-handoff record, ledger update. Missing policy: all criteria, data/security conditions, exceptions, authority and action map. Applicant question: “请补充资产编号、领用/交付用途、接收责任人及合同或归还/台账依据。” Human escalation: establish the asset-release rule. Six cases: compliant/material gap/rule gap/exception/non-compliance/conflict → `needs_human` (material gap may additionally receive the stated comment).

## asset_disposal

Identity: `PROC-67371734-3761-4D25-83B0-A7499B257668`; status `partial`. Scope: fixed-asset disposal and accounts-receivable bad-debt branch.

Verified facts/evidence: asset identity, original/net value, quantity, rationale; for bad debt, collection record plus evidence of non-recovery. Limited bad-debt condition: 36-month term plus evidence and management approval for full provision. Missing policy: fixed-asset disposal, valuation, proceeds, data erasure, related-party, authority and action map. Applicant question: “请补充处置/坏账的评估依据、追款或处置记录及管理层审批依据。” Human escalation: select the branch and decide authority/action.

Actions: generic decision table (see the shared execution contract above).

## monthly_revenue_recognition

Identity: `PROC-20C94D39-E955-4080-83B8-026FD268F088`; status `partial`. Scope: monthly revenue recognition.

Verified facts/evidence: customer, amount/currency, period, income summary, customer acceptance email/form; relevant data-service or software-service recognition basis; named business, management, finance and preparer confirmations. Missing policy: template field binding, adjustments/reversals/cross-border exceptions, authority and action map. Applicant question: “请补充客户验收依据、收入确认汇总表及对应签字/确认记录。” Human escalation: confirm accounting exception and final action.

Actions: generic decision table (see the shared execution contract above).

## special_matter

Identity: `PROC-B6332CBA-E104-4C4B-A431-48884FF52E82`; status `partial`. Scope: exceptions.

Limited evidence: for first-curve labelling projects, gross margin below 25% requires an explicit special approval and management owner approval with documented strategic/customer/algorithm/product/delivery/reuse value. It is not proven to bind this template or every exception. Missing policy: scope, thresholds, term, reviewer/authorizer and action map. Applicant question: “请补充本次特批所适用制度条款、已明确的管理层批准依据及特批期限。” Human escalation: confirm that this template and branch are governed by a complete exception policy.

Actions: generic decision table (see the shared execution contract above).

## international_legal_preapproval

Identity: `PROC-3A35E9D2-0D06-4541-9402-F5ED23AA1541`; status `source_pending`. Scope: pre-engagement international legal consultation.

Required facts/evidence: matter scope/necessity, firm/counsel, quote and estimated hours, project/entity, confidentiality/data handling and attached review files. Missing policy: conflict check, budget/rate boundary, emergency exception, legal/finance authorization and action map. Applicant question: “请补充咨询必要性、律师/律所报价与预计时长、涉及主体及保密/数据处理说明。” Human escalation: establish legal-spend policy and action. Six cases: compliant/material gap/rule gap/exception/non-compliance/conflict → `needs_human` (material gap may additionally receive the stated comment).
