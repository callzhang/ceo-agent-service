---
name: stardust-oa-project-review
description: Review Stardust DingTalk OA approvals for project initiation, first-curve projects, POC and special-approval exceptions using source-backed company rules.
---

# Stardust OA Project Review

Use together with `$dingtalk-oa-approval` for Stardust approvals whose primary business decision is project initiation, a first-curve project, POC/new-opportunity investment, or a project special approval. This Skill supplies the project criteria; the generic Skill supplies evidence handling, scoring and the decision table that turns a result into an action. The rules are in this Skill — do not read `钉钉审批审阅原则.md` or any other reference document for them.

## Scope

Classify from the live process definition and form content, not title alone. A project can also trigger a separate finance, contract, or access/change review; load each relevant business Skill independently. This Skill does not supply contract-signing authority.

Project-governance documents (`跨部门协同服务标准` v0.2 / v0.3, strategy and operating-model notes) may be read as evidence of what was agreed for a specific project — scope, gates, owners, commitments. They are not a source of numeric approval thresholds, and their examples are not limits.

## Review checklist

Read the complete application, comments, attachments and linked project material. Record evidence for:

- customer/problem and strategic fit;
- project classification, including whether it is explicitly marked first-curve or special approval;
- gross margin calculation and its inputs, scope and period;
- BRD or equivalent scope, effort estimate, staffing/resources and opportunity cost;
- customer confirmation, contract/budget status, delivery boundary, acceptance criteria and measurable outcome;
- cost ceiling, owner, milestones, dependencies, risks and mitigations;
- for a below-floor project: assess the strategic, customer, algorithm-efficiency, product-upgrade, delivery-capability and reusable value from the application and linked evidence, whether or not the applicant explicitly requested special approval; if the case merits an exception, identify the responsible manager's position and the applicable approval authority.

Keep applicant assertions separate from independently verified records. An item that was submitted but could not be read is handled under the generic Skill's principle 5 (unread is not missing).

## Project rules

1. **Strategic fit.** A project must fit the company strategy or bring a product, algorithm or delivery-efficiency upgrade. Check this first; it is the reason a project exists.
2. **First-curve margin floor: 25%.** A project explicitly classified as first-curve needs a comprehensive gross margin of at least 25%. Reaching 25% does not by itself make the case approvable; the rest of the checklist still applies.
3. **Below 25% requires the form's special-approval path and proactive business-value review** (Derek, 2026-09-23):
   - The applicant must check the form's **"特批"** option when a first-curve project's comprehensive gross margin is below 25%. If the box is not checked, **return the application** for the applicant to select it; do not reject it for the margin shortfall and do not select the box on the applicant's behalf.
   - A checked request must include either a business-value assessment or a plan for how the project will earn back the margin shortfall. If neither is supplied, return it for that applicant-provided material. Do not treat the checkbox or applicant's conclusion as proof that the exception is justified.
   - Independently assess the case using the evidence actually available: strategic fit and market/customer importance; confirmed customer commitment, revenue and budget; product or algorithm improvement and measurable efficiency gain; reusable data, capability or delivery assets; total effort/opportunity cost; and whether risks and delivery boundaries are controllable. Separate verified facts from applicant claims. Do not invent a numeric business-value cutoff that this Skill does not define.
   - If the evidence supports a compelling, strategically relevant case and the material facts are complete, state **"recommend special approval"**, explain the value, trade-offs and/or credible earn-back path, and identify the required special approver/manager decision. Execute the exception only when the current authorization explicitly permits it and the authorized special approver's decision is recorded; otherwise route that approval decision to `needs_human`. Proactive assessment is not itself authority to grant an exception.
   - If the value case or earn-back path cannot be assessed because applicant-supplied facts are missing (for example, revenue/customer commitment, cost assumptions, scope, measurable outcome or risk mitigation), **ask back / return for those specific facts**. The required form checkbox is a procedural gate, not a substitute for the reviewer assessing business value. Reassess the exception on resubmission.
   - If the needed company policy, exception boundary, special-approver identity/authority is missing or conflicting, or complete facts still leave a material strategic trade-off that the reviewer is not authorized to decide, set `rule_coverage < 1.0` where the rule is missing and route to `needs_human`. Do not ask the applicant to create company policy or decide the company's strategic trade-off.
   - If the evidence is complete and affirmatively shows no material strategic/customer/product/algorithm/reusable value, or the benefits do not justify the 25% shortfall and risks, conclude that the exception is not justified and reject only when the generic Skill's rejection preconditions are met. If a curable factual gap remains, ask back instead.
4. **A low margin with no strategic, product, algorithm or reusable value** weighs against the exception; state the evidence and rationale. Mere absence of an applicant-authored exception request is not evidence that the business case lacks value.

Before rejecting, the generic Skill's reject preconditions still apply: check `revert-activities`, and if the reason asks the applicant to supply anything, it is a return, not a rejection.

## Not yet defined here

These block only a case that actually turns on them; when one does, keep `rule_coverage < 1.0`, name the exact branch, and escalate `needs_human`:

- who the "responsible manager" is for a given project and who holds final exception authority; the reviewer can still assess and state an exception recommendation without resolving this, but may not execute a grant until authority is clear;
- financial thresholds and boundaries for projects that are not first-curve;
- which version of the project-governance standard governs when v0.2 and v0.3 conflict on the point in question.

Actions follow the generic Skill's decision table. A project type not having its own action list is not a rule gap.

## Required review output

Include live process identity; classification; each material read; margin calculation; for a below-25% first-curve project, whether the form's special-approval box is checked and whether the applicant supplied a business-value assessment or earn-back plan; the rule branch applied (rules 1–4 above); facts versus assertions; the reviewer's independent business-value assessment; `information_completeness`, `rule_coverage`, risk and confidence; recommendation; applicant question or return reason if any; and each undefined item above that the case depends on.
