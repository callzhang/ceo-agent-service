# Project CRM Customer Association Implementation Plan

**Design:** `../specs/2026-10-06-project-crm-customer-association-design.md` (Derek-approved optional CRM identity; candidate search/read-only boundary)

## Objective

Add a nullable Fxiaoke CRM customer relation to an official Project, keep Tasks customer-free in storage, derive customer context only through a confirmed Project link, and let a user resolve ambiguous candidates without writing to CRM.

## Implementation sequence

1. **Read-only CRM adapter** — use the installed `sharecrm data record query-by-name` CLI against verified `AccountObj`; retain only candidate ID/name and resolver state, distinguish single candidate / multiple candidates / no match / unavailable, bound the candidate list, and never persist credentials or full CRM responses. The resolver is not treated as exhaustive or exact; every result needs human confirmation. Add fake-process tests first.
2. **Project storage and Task Agent contract** — nullable `crm_customer_id`, display snapshot, source-backed customer label/evidence, latest lookup state and bounded candidates. Existing rows migrate to unassociated without title changes. Apply lookup results in the existing Project transaction; preserve a confirmed link on unavailable, empty, ambiguous, or conflicting later results. Prompt permits CRM reads only and prohibits CRM writes plus Memory `memory_write`.
3. **Explicit local confirmation** — authenticated console action confirms only a candidate already saved on that Project; it changes CEO Agent Service data only. Add direct store/API tests for stale IDs, arbitrary IDs, repeated confirmation, and clearing/relinking behavior.
4. **Project and Task read models** — show the linked CRM label and lookup state on Project summary/detail; derive Task customer context through its single confirmed Project; add customer-grouped Project view keyed by `_id`, leaving unassociated/internal Projects in the general list.
5. **Verification and release** — run focused backend/frontend tests, migration tests, CRM adapter tests against fake CLI, fixed native Project eval, build, and read-only production schema/query smoke; record separate evidence for exact W39 migration, pull request/push, deploy, and live readback. CRM writes are never part of tests or release.

## Acceptance gates

- Project title and CRM customer identity remain separate; internal Projects migrate with no inferred customer.
- Every CRM resolver result remains unlinked until a user confirms it, including one returned candidate; no match and CLI/auth failure remain distinct.
- A previously confirmed customer link is never cleared or silently replaced by an evidence scan.
- Tasks inherit customer information only via their confirmed Project relation; standalone Tasks do not receive a customer.
- Project customer grouping uses stable CRM `_id`, not displayed names.
- No CRM create/update/delete command is reachable from this feature.
