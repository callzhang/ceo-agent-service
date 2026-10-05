# Project-to-CRM Customer Association Design

**Date:** 2026-10-05

**Status:** Draft for user review

**Scope:** Optional customer identity on official Projects, sourced from the
Fxiaoke CRM `AccountObj` directory. This is a design amendment to the Task-first
Project model; it does not implement runtime or console changes.

## 1. Problem

Project titles sometimes contain a customer name and a project name, for
example “甲客户一期交付项目”. A title is useful for recognizing the specific
Project, but it is not a reliable customer identity: names can vary, and not
every Project is customer-facing. Without a separate optional CRM association,
Projects for the same customer cannot be reliably grouped, while putting a
customer on every Task would duplicate identity and drift over time.

The current semantic Project record has a canonical title and registry source,
but no CRM customer reference. The local CRM CLI exposes the Fxiaoke
`AccountObj` schema. Its stable record key is `_id`; its customer display name
is `name`, with additional fields `field_customer_alias__c` (customer alias)
and `UDSText1__c` (registered full name). The customer object also has
`account_no` and `parent_account_id`. These are CRM fields, not fields currently
implemented in the Project model.

## 2. Goals

1. Preserve the official Project title exactly as established by its source or
   confirmation, including any customer prefix.
2. Allow an official Project to be linked to zero or one CRM customer.
3. Enable grouping Projects by the same stable CRM customer identity.
4. Keep internal Projects valid without a customer association.
5. Derive customer context for Tasks through their confirmed Project link; do
   not copy a customer ID onto each Task.
6. Automatically link only a single unambiguous CRM match; expose uncertain
   matches for human confirmation instead of guessing.
7. Keep CRM as the authority for customer identity and avoid CRM writes.

## 3. Non-goals

- Creating or updating CRM customer records.
- Replacing the existing Project title with a CRM customer name or a generated
  combination of customer and project names.
- Requiring every Project or Task to belong to a customer.
- Building a local customer master, background synchronization service, or
  general-purpose CRM integration.
- Automatically linking a Project to a CRM parent customer through
  `parent_account_id`; customer hierarchy remains a CRM concept in this
  iteration.
- Changing Task extraction, Task lifecycle, project registration authority, or
  CEO attention eligibility.

## 4. Considered approaches

### A. Optional direct Project-to-CRM association — selected

Store the CRM customer record ID on the Project when the match is unambiguous.
Keep the Project title unchanged and derive Task customer display through the
Project.

**Trade-off:** Smallest model that supports customer grouping while preserving
internal Projects. Matching and CRM availability still need explicit handling.

### B. Local customer directory synchronized from CRM

Create local customer entities, periodically sync CRM records, and associate
Projects with local IDs.

**Trade-off:** Better for offline use and large-scale customer views, but adds
sync lifecycle, freshness, deletion, and identity-reconciliation problems not
needed for the current requirement.

### C. Store customer name only on Project

Save a name extracted from source text without a CRM ID.

**Trade-off:** Easy to display but cannot reliably group aliases, name changes,
or duplicate names, so it fails the customer-summary use case.

Approach A is selected. `AccountObj._id` is the canonical association key;
`AccountObj.name` is the display label. The CRM customer number and name fields
may assist matching and display but do not replace `_id` as identity.

## 5. Domain model

- An official Project retains its own canonical `title` and registry source.
- A Project may have a nullable `crm_customer_id`, containing the exact
  `AccountObj._id` value. Many Projects may refer to the same CRM customer.
- The association also retains a customer-name snapshot for readable display
  when CRM cannot be queried. It is explicitly a display snapshot, not an
  identity key; the CRM ID remains authoritative. A later successful CRM read
  may refresh this label without changing Project identity.
- The Project stores the match state and the CRM candidate records needed to
  explain an unresolved match. Candidate records contain CRM `_id`, `name`,
  available alias/registered-name labels, and the match basis. Candidate data
  is not an association until selected or uniquely matched.
- Tasks do not receive a copied customer field. A Task's customer is derived
  only through its current confirmed Project relationship. Standalone Tasks,
  candidates, and Tasks linked only to non-Project anchors have no inherited
  customer.
- Clearing or changing an association does not rename, merge, or delete the
  Project or its Tasks.

## 6. Matching and confirmation

### 6.1 Input evidence

Attempt CRM matching only when Project source evidence or explicit human input
identifies a customer name/alias. A title prefix may count as the source's
customer label when that complete prefix exactly matches a CRM customer name or
alias and the remaining title clearly names the Project work (for example,
“甲客户” + “一期交付项目”). Do not strip arbitrary title words or use fuzzy
similarity to invent the customer label. Keep the original evidence citation
for the extracted customer name.

### 6.2 Automatic association

Use only customer identity fields verified from the live CRM schema: `name`,
`field_customer_alias__c`, and `UDSText1__c`, plus the stable `_id`. A unique
match is one CRM record whose verified name/alias values match the
source-established customer label after safe whitespace and case/Unicode
normalization. Do not use fuzzy similarity, customer number coincidence, or
parent-customer hierarchy to auto-link.

- Exactly one matching CRM record: link the Project to its `_id` and retain the
  matched display name and evidence.
- More than one matching CRM record: leave the Project unlinked and persist
  the candidate records for user confirmation.
- No exact matching record: leave the Project unlinked and show that no exact
  match was found. The user may search/select from CRM candidates in the
  confirmation UI; a searched candidate is not automatically accepted.
- CRM lookup unavailable, unauthenticated, or failed: leave the Project's
  existing association unchanged; record the lookup as unavailable rather
  than treating the failure as “no customer”.

Manual confirmation links only the selected CRM `_id`. Rejected candidates do
not become an association. Re-evaluation of newer evidence must not silently
replace a previously confirmed association; a conflicting unique result is
shown as a conflict for explicit resolution.

## 7. User experience

The Project detail and list views show:

- the unchanged Project title;
- the linked CRM customer name when linked;
- “内部 / 未关联客户” when no customer is associated;
- “待确认” plus the candidate CRM customer names when matching is ambiguous;
- “未找到 CRM 客户” for a completed lookup with no exact match; and
- “CRM 暂不可用” for a failed lookup, distinct from a successful empty search.

The customer view groups Projects by CRM `_id`, not by title text or customer
label. It excludes unassociated internal Projects from customer groups while
leaving them visible in the general Project list. A user-confirmed association
is a Project-level operation. Task screens may display the inherited customer
as contextual information, but do not offer an independent Task customer edit.

## 8. Data flow and boundaries

1. The evidence scan / Project update identifies a source-backed customer label
   when one is actually present.
2. The Task Agent or project-resolution path performs a read-only CRM lookup
   using the installed `sharecrm` CLI and live-described `AccountObj` fields.
3. A unique exact match is included in the Project update; ambiguous and
   missing matches remain unassociated with their lookup result represented
   distinctly.
4. The Project persistence layer stores the optional CRM reference and the
   latest match display/proposal information with the Project update.
5. Project and Task APIs expose the linked CRM ID/name and match state; the
   console groups by CRM ID and renders unresolved choices.

CRM access is read-only for this feature. The feature must not create, update,
or delete CRM data. CRM credentials and session material are not copied into
the application database. A CRM error is visible and retriable on a later
evidence-driven update; it is not interpreted as a negative match.

## 9. Migration and compatibility

- Existing Projects migrate with `crm_customer_id = null` and no inferred
  customer. Existing titles and Task/Project relations remain unchanged.
- CRM matching is not run as an unbounded migration over old Projects.
- An existing Project is associated only when new source evidence uniquely
  resolves its customer or a human confirms a CRM candidate.
- APIs remain backward-compatible by making all customer fields optional.
- Existing Task behavior remains unchanged when the Project has no customer.

## 10. Failure handling

- CRM expired authentication, CLI absence, permission denial, schema drift, or
  service failure is represented as lookup unavailable and must not erase a
  previously linked customer.
- An unknown `AccountObj` field or unsupported CLI response shape stops the
  lookup; the implementation must re-describe the live schema rather than
  guessing.
- Deleted, merged, or inaccessible CRM records are not silently reassigned by
  name. They are shown as unresolved/stale and require a new successful lookup
  or explicit confirmation.
- Duplicate names are ordinary ambiguity, not an exception to the
  confirmation rule.

## 11. Acceptance criteria

1. A Project titled “甲客户一期交付项目” retains that exact title when linked
   to CRM customer “甲客户”.
2. Two Projects linked to the same CRM `_id` appear in the same customer group,
   even if their titles and stored customer-name snapshots differ.
3. An internal Project remains valid with a null CRM customer ID and is not
   forced into any CRM customer group.
4. A Task linked through a confirmed Project displays that Project's CRM
   customer; a standalone Task does not inherit one.
5. One exact CRM match auto-links; two exact matches remain unlinked and expose
   both candidates; no exact match remains unlinked; lookup failure is visibly
   distinct from no match.
6. A user may confirm one candidate, after which the Project uses that
   candidate's stable `_id`; CRM itself receives no write.
7. A failed or conflicting later lookup never clears or silently changes an
   existing confirmed association.
8. Existing Projects remain unchanged after migration apart from nullable
   default association state.

## 12. Test plan

- Unit tests for nullable CRM ID, one-to-many Project/customer grouping, and
  customer inheritance through Project only.
- Matching tests for exact canonical-name/alias/full-name match, duplicate
  candidates, no match, normalization, and no fuzzy auto-link.
- State tests showing lookup failure differs from no match and never clears a
  confirmed association; a conflicting match cannot replace it implicitly.
- API/console tests for linked, internal/unlinked, ambiguous, no-match,
  unavailable, and stale association states.
- Migration test proving existing Projects keep title and registry identity
  and receive no inferred customer.
- CRM integration tests use a fake `sharecrm` process/schema; no tests write to
  the live CRM.

## 13. Scope boundary for implementation

Implementation requires a separate plan covering the schema and typed model,
read-only CRM lookup adapter through the native CLI, evidence-driven match
resolution, the minimal Project customer confirmation/display API and UI, and
focused migration, unit, and UI tests. It must not include a local CRM mirror,
background full-directory sync, CRM writes, or changes to unrelated Task
lifecycle/attention behavior.
