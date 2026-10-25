# Historical business read unification — field and caller contract

Baseline: `2fb17e859`, Store `2026-10-25.2` / Email54. This change retires duplicate operational readers, not historical facts. No new schema, business field, row deletion or migration is proposed.

## Owner and retirement rules

- `business` owns retained Project/Todo history; `actions` owns retained Update and historical delivery/follow-up facts. Their existing typed identity and source references remain unchanged.
- Current business Tasks, formal project context, follow-ups and DingTalk operations keep their existing semantic owners; executable Task/Run scheduling remains separate from business task meaning.
- Matching titles or integer IDs do not prove identity. Historical status, ownership and dates are recorded observations, not current provider or operational truth.
- `business.import_task_id` is the existing explicit Todo-to-adopted-Task reference. This change does not bulk import, infer mappings, or change import eligibility.
- Previously approved nine retired controls remain retired; no value backup is reintroduced. See [nine-field mapping](business-model-legacy-fields-mapping.md).

## Every retained field

All 42 current fields below remain. Every row is read by the existing typed history projection/model or exact semantic import. Similar names on current Tasks denote different records and are not evidence of duplicate values.

| Historical kind | Field | Existing use / retained owner | Decision |
| --- | --- | --- | --- |
| `work_project` | `id` | Stable typed history identity and deep link | retain |
| `work_project` | `title` | Historical display / import source fact | retain |
| `work_project` | `category` | Historical Project classification | retain |
| `work_project` | `tags_json` | Historical Project tags | retain |
| `work_project` | `status` | Historical lifecycle observation only | retain |
| `work_project` | `owner_user_id` | Historical owner identity / import evidence | retain |
| `work_project` | `owner_name` | Historical owner display / import evidence | retain |
| `work_project` | `related_people_json` | Historical Project participants | retain |
| `work_project` | `goal` | Historical Project goal | retain |
| `work_project` | `background` | Historical Project context | retain |
| `work_project` | `facts_json` | Retained adopted Project facts | retain |
| `work_project` | `current_state` | Historical Project summary | retain |
| `work_project` | `blocker` | Historical unresolved constraint | retain |
| `work_project` | `next_step` | Historical proposed next step; not an enqueue instruction | retain |
| `work_project` | `source_conversations_json` | Source conversation provenance | retain |
| `work_project` | `created_at` | Original fact creation time | retain |
| `work_project` | `updated_at` | Original fact update time | retain |
| `work_project` | `last_activity_at` | Historical activity time | retain |
| `work_project` | `owner_evidence_json` | Source-supported owner evidence; no source reconstruction by guess | retain |
| `work_todo` | `id` | Stable typed history identity and deep link | retain |
| `work_todo` | `project_id` | Original historical Project association; never inferred as a formal project ID | retain |
| `work_todo` | `title` | Historical display / import source fact | retain |
| `work_todo` | `owner_user_id` | Historical owner identity / import evidence | retain |
| `work_todo` | `owner_name` | Historical owner display / import evidence | retain |
| `work_todo` | `status` | Historical lifecycle observation only | retain |
| `work_todo` | `deadline_at` | Recorded deadline; not a follow-up scheduler | retain |
| `work_todo` | `follow_up_question` | Historical question; not an active follow-up job | retain |
| `work_todo` | `blocker` | Historical unresolved constraint | retain |
| `work_todo` | `completion_evidence_json` | Retained completion evidence | retain |
| `work_todo` | `created_from_update_id` | Exact original Update identity | retain |
| `work_todo` | `created_at` | Original fact creation time | retain |
| `work_todo` | `updated_at` | Original fact update time | retain |
| `work_todo` | `completed_at` | Recorded completion time | retain |
| `work_todo` | `description` | Retained Todo fact / import source content | retain |
| `work_todo` | `owner_evidence_json` | Source-supported owner evidence; no source reconstruction by guess | retain |
| `work_updates` | `id` | Stable typed history identity and deep link | retain |
| `work_updates` | `project_id` | Original historical Project association; never inferred as a formal project ID | retain |
| `work_updates` | `source_type` | Source provenance kind | retain |
| `work_updates` | `source_ref` | Source provenance locator | retain |
| `work_updates` | `summary` | Retained adopted Update summary | retain |
| `work_updates` | `changes_json` | Retained adopted changes; no replay or new operation | retain |
| `work_updates` | `created_at` | Original fact creation time | retain |

## Caller cutover

| Surface | Current source / action | Historical access retained |
| --- | --- | --- |
| Current console Task inventory | Existing `business_task_list_response` and semantic Task APIs | No legacy status-based fallback |
| Legacy Project detail | Existing `load_project_task_detail` and one detail projection | Original Project/Todo/Update identities, evidence, follow-ups and receipts |
| Semantic import | Existing source-backed eligibility and explicit import relation | Unmatched records remain history only |
| Duplicate legacy list/search APIs | Retire only after checking all runtime/Skill/frontend references | Explicit historical detail remains |

## DDL and rollback contract

Target DDL is identical to the current Store25.2/Email54 manifest. Retirement SQL is empty: no retained field has a proven interchangeable owner permitting deletion in this slice. Schema forward/inverse is therefore unnecessary. Runtime rollback uses the previous committed code against the unchanged schema; copy validation must prove source rows/types and the existing FK finding remain unchanged. This is a reduction in parallel business readers, not a database file-size reduction.

## Implemented reader retirement and tests

The dead legacy list DTOs, list projection/sort code and ranked legacy search
chain are removed. The current formal Task APIs are retained. The two obsolete
`/api/task-management` routes are removed; the non-SPA legacy operational
`/tasks` list is removed. Current React `/tasks` remains the operational UI.
All current historical links use `/tasks/legacy-project/{id}`, with the same
explicit HTML fallback path. Ambiguous `/tasks/{id}` is retired. Both detail
projections load the historical parent/children through `load_project_task_detail`;
no inferred formal Task identity or provider-state update is introduced.

The new integration regressions failed before the retirement because the old
search API returned200 and because History emitted the ambiguous old path.
After implementation they cover same-integer/different-domain identities,
formal inventory isolation, retained fact/source/Update/Todo receipt details,
canonical History links and identical SPA/non-SPA historical paths.

Only tests exclusively exercising deleted operational APIs/rankers and the
retired automatic Store19.8–19.10 release dispatch were removed. Mixed tests
retain their history/detail assertions. The exact26 removed function names
(four old-release parameterizations belong to two functions) and reasons are in
[retired-test inventory](validation/2026-10-25-history-retired-test-nodes.json).
This does not relabel the historical broad non-green run as passing.

A separate scoped callback Audit test failed on the retired `run_storage`
projection before Audit executed (`runs.record_type` absent). Its shared test
executor now reads the existing current Store native Run projection; all
callback assertions and runtime behavior are unchanged.

Final combined backend selection: **96 passed in60.41seconds**. It covers the
new read-boundary/canonical-link tests, legacy no-dispatch/id-collision boundary,
source-backed import, semantic retrieval, current API contract, all remaining
retained-field tests and outbound/Postfix callback contracts. Two additional
native-harness argument tests pass. Targeted retained HTML/SPA/feedback tests
were also run by the implementer; no whole-suite pass is claimed.

The production-derived baseline and candidate have identical schema SHA256
`dd4a93fd2094a96b18717ed8cdf99fd7c5cdb0ecbc06ba7ded3ad498b7aec609`,
current Store25.2/Email54 declarations, and the same sole existing FK finding.
The original A5 input passed actual CLI selection and substitute execution;
see [native revalidation](business-model-a5-original-revalidation.md).
This release changes no schema, so there is no schema forward/inverse operation
to rehearse; code rollback uses the same unchanged declaration. An archived actual `2fb17e859` app cold-started on the tested copy, read the newly completed native Task and retained Project140/Todo2496, wrote one new formal Business Task852, and current code reopened that new Task successfully; no provider was instantiated.

## Frontend and browser acceptance

The existing legacy detail mapper now retains the existing `source_conversations`
value, rather than dropping it. The read-only page displays Todos, Updates,
evidence candidates, linked/unlinked follow-ups and DingTalk sync receipts from
its existing API. There are no send, retry or decision controls. Historical
status labels do not assert current external state. TODO/follow-up/evidence
anchors target open sections; receipt bodies remain folded until requested.

Four focused frontend files passed **58 tests** (legacy detail, unchanged formal
Task detail, API mapping and app routing); TypeScript and production build pass.
Independent backend and frontend SPEC/QUALITY reviews passed after repairing
canonical links and closed-section anchors.

Actual Chrome against the worker-free isolated copy opened Tasks, clicked the
formal inventory and Task851, and read its source link. It then opened legacy
Project140, located original Todo2496 and expanded its retained DingTalk sync
receipt1. The original provider task identity and stored successful readback
were visible. No provider operation was executed. The in-app browser control
was unavailable; Chrome was used for these observations.

The native CLI case has its own original input/source/trajectory evidence;
UI acceptance is separate. Publication/readback is pending until recorded below.
