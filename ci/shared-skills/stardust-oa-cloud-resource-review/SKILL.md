---
name: stardust-oa-cloud-resource-review
description: Review Stardust DingTalk OA approvals for cloud-resource fees and server use (云资源费用&服务器使用审批) as changes against an approved resource plan.
---

# Stardust OA Cloud Resource Review

Use together with `$dingtalk-oa-approval` for `云资源费用&服务器使用审批` and any other approval whose primary decision is spending on cloud resources, servers or other compute hardware. This Skill supplies the criteria; the generic Skill supplies evidence handling, scoring and the decision table. The rules are here — do not read `钉钉审批审阅原则.md` or any other reference document for them.

This is not a finance-template review: the finance Skill explicitly leaves this approval to its owning review. A project whose cost it is may also need `$stardust-oa-project-review`.

## The rule (Derek, 2026-09-23)

**Cloud and hardware spending runs against a plan. Without a plan, ad-hoc cost approvals are not allowed.** Each hardware or cloud cost approval is therefore a **change approval against that plan**: it must say why the plan is being changed, and which project bears the cost.

## Checks, in order

1. **There is a resource plan this request belongs to.** Find it: linked in the form, attached, named in the remarks, or findable by the project/team it names. Record its title, owner and the period and budget it covers.
2. **The request states why the plan is changing.** New workload, a capacity shortfall, a vendor price change, a scope change — something specific. "需要用" or a restatement of the spend is not a reason.
3. **The request names the project that bears the cost**, and that project exists and is the one actually consuming the resource.
4. **The amount, resource type and period are consistent with the stated reason** and with the plan's remaining budget, or the overrun is explained.

## Outcomes (map to the generic decision table)

| Situation | Action |
|---|---|
| No resource plan exists for this spending at all | **Reject** — ad-hoc cost approval without a plan is not allowed. Cite this rule. Generic table row 8 (未达标准). |
| A plan exists but is not linked or attached | **Return** for the plan link. The applicant can supply it; this is a material gap (row 4). |
| The plan is linked but could not be read after every reading path | Do not return; handle as unread material (generic principle 5, row 4a). |
| Change reason or cost-bearing project is missing | **Return**, naming which is missing (row 4). |
| Change reason is vague, or the named project does not match the resource's real use | **Comment** asking the specific question (row 9). |
| Plan found, change reason specific, cost project named and matching, amount consistent | **Approve** under the generic band (row 11–13). |

Before rejecting, the generic reject preconditions still apply: check `revert-activities`, and if the reason asks the applicant to supply anything, it is a return, not a rejection. "No plan exists" is a fact about the request, not a request for the applicant to supply one — that is why it rejects.

## Required review output

Include live process identity; the plan found (title, owner, period, budget) or the search showing none exists; the stated change reason; the cost-bearing project and whether it matches actual use; amount, resource type and period against the plan; `information_completeness`, `rule_coverage`, risk and confidence; the table row applied; and the return reason or question, if any.
