---
name: stardust-oa-contract-review
description: Review Stardust DingTalk OA contract approvals from the contract body, business commitments and the mandatory legal review.
---

# Stardust OA Contract Review

Use together with `$dingtalk-oa-approval` for Stardust approvals whose primary decision is to approve, execute, amend, renew, or terminate a contract or other binding external commitment. This Skill owns Stardust contract-review criteria. The generic Skill owns cross-company evidence, scoring, workflow, and DingTalk action mechanics.

## Scope and source

Use the live process definition, form fields and contract content to establish that this Skill applies. Do not use a finance or project rule card as contract-signing authority. A contract attached to a project may require both `$stardust-oa-project-review` and this Skill; a payment also requires `$stardust-oa-finance-review` when its exact registered template matches.

Read the current contract body. For project contracts, also inspect relevant signed/approved project scope and governance records, without treating them as substitutes for signing authority or legal review.

## Mandatory contract review

Open and read the actual contract body; an AI summary, form summary or legal-comment summary cannot substitute. Record the reviewed version and check:

- contracting legal entities and counterparty identity;
- contract value, currency, tax and payment schedule;
- deliverables, responsibilities, dependencies, timing and acceptance criteria;
- payment obligations, refund/holdback and cash-flow exposure;
- warranty, indemnity, limitation of liability, penalties and termination;
- confidentiality, personal/data security, data use and cross-border handling;
- intellectual-property ownership, licenses and pre-existing materials;
- term, renewal, governing law, jurisdiction and dispute process;
- signature method, signatory identity/authority and required corporate approvals;
- legal-review comments, business owner response and specific mitigations for each material risk.

Compare the contract against the approved business scope, quote, budget, delivery plan and customer commitments where available. Surface every mismatch rather than silently reconciling it.

## Current source-backed handling

- A contract body must be reviewed before recommending approval; reviewing only an AI summary, legal feedback or form fields is insufficient.
- Check the terms above, including parties, value, obligations, acceptance, liability, confidentiality/data security, IP, term, signature method, legal opinion and business mitigations.
- If a material risk has no corresponding mitigation, request supplementation or a renewed legal/business explanation; favor a correctable return/comment over rejection.
- A request for the applicant to provide or amend something is a return/comment, not a rejection. Never use reject to simulate return.

## Signing and legal review (Derek, 2026-09-23)

- **Derek's approval task is the signing decision.** A contract approval that has reached Derek's running task is there for Derek to sign off. The approval flow is the authority; "who holds signing authority" is not a gap to escalate, and `rule_coverage` is not lowered for it.
- **Every contract requires legal review.** There is no trigger threshold and no waiver. The OA must carry a specific written legal opinion: who reviewed, the risks identified, and how each one is handled (changed, mitigated, or accepted with reason). A missing opinion, or a placeholder such as "xxx", "无" or "已确认" with no content, is a material gap: return the approval to the submitter for the legal opinion. Do not approve a contract without it.
- **An applicant's claim that Derek already agreed is not evidence.** A remark like "主要条款已与磊哥本人确认，请不要拒绝" does not decide the case. Look for the confirmation yourself first (Derek's DingTalk chats, meeting minutes, documents); if it cannot be found, return asking the applicant to provide the proof (the chat record, minutes or signed confirmation). When the remark instead supplies concrete facts about the contract, re-judge the case on those facts.

Actions follow the decision table in `$dingtalk-oa-approval`: this Skill supplies the criteria a case is judged against, and the table turns the result into approve, return, comment, reject or `needs_human`. A category not having its own action list is not a rule gap.

## Unresolved policy fields

Not yet defined in this Skill:

- amount/risk thresholds and red lines for data, IP, jurisdiction, liability and other terms;
- exception approver and evidence of the exception.

When a case depends on any such missing rule, do not infer authority from job title, previous approvals, an unsigned draft, a legal comment, or the scheduled prompt. Keep `rule_coverage < 1.0`, describe the exact uncovered branch, and set `needs_human`. Applicant questions may retrieve missing contract facts or a known rule location, but cannot resolve company authorization or policy.

## Required review output

Include the live process identity; contract name/version and confirmation that the full body was read; linked materials reviewed; clause-by-clause material risks and mitigations; facts versus assertions; source/version/effective-date status; `information_completeness`, `rule_coverage`, risk and confidence; the legal opinion and how each risk is handled; recommended action and exact basis; applicant question if any; and unresolved policy fields requiring `needs_human`.

