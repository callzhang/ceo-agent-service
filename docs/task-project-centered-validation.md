# Project-centered work validation

## Scope and completion level

2026-10-05 continuation: Derek confirmed the exact source Project title and the
migration rule to preserve the original foreign-key violation set without repair.
The fixed fixture is now version 4: source titles follow the source's full formal
name; source records carry the runtime's actual AI Minutes action-item or
authorized-assignment metadata; and report cases include an exact registry row.
Task Agent prompt and shared Skill now require a
complete ProjectContext snapshot on new facts, one assessment per relevant
Project (including each separate report row), exact current-source registration
quotes, and no Attention solely because an assignee has not accepted while work
is progressing. A material Project risk can be watched without a Task; a
display-only next-step suggestion is allowed only when saved Project
responsibilities support its concrete action and proposed owner. Focused verification
passed **742 tests**, Ruff and `git diff --check`; a native smoke passed the
role-based suggestion case. The full v2 native replay later scored **10/19**;
its original artifact is retained under
`/private/tmp/project-centered-native-v2-300e107e`. Review found invalid report
registry / meeting action metadata in two fixtures and real prompt gaps around
tasks inferred from roles or routine steps, missing-owner attention, and Chinese
role suffixes included in names. Version 3 corrects the source metadata and these
prompt rules. The v3 focused rerun of the nine previously failing cases completed
3/9; six still need correction or runtime diagnosis. The v4 oracle now distinguishes
missing overall owner alone (`not_needed`) from an explicit disputed transfer
(`needs_attention`), accepts a material-risk display-only next step when a sourced
responsibility fits, and keeps the existing-card membership assertion strict.
The full fixed v4 native comparison remains pending.

### 2026-10-05 native rerun on 89e401e0

The candidate completed the native runtime-route probe and began the fixed v4
suite on fresh temporary databases with `gpt-5.6-luna` via `codex_oauth`, a
loaded CI Skill, 900-second total timeout, 300-second idle timeout, and
concurrency 1. The first case completed but failed the frozen oracle: the Agent
invented a source Task to confirm a payment date despite no saved Project duty
or source action, then attached that Task to a zero-Task Attention case. This
was not a runtime availability or schema failure. The prompt and Skill were
clarified so Project risk may stand alone, and inferred Task suggestions require
a saved, sourced Project responsibility that supports both the action and its
proposed owner. A new clean-commit full 19-case run is required; no results from
the earlier d2450fbf run or this stopped partial replay count for that candidate.

### Initial full v4 native candidate (2026-10-05)

Candidate `75d7885022ac3a6c9f4cb76d751783726202e7dd`, route
`codex_oauth` / `gpt-5.6-luna`, effective timeout 900 seconds / idle 300 seconds,
concurrency 1, completed all 19 isolated native cases: 15 passed and 4 failed.
The behavioral failures were `unknown-overall-owner` (a responsibility-only
Project context generated two candidate Tasks) and
`responsibility-change-conflict` (the competing overall-owner candidates were
also stored as responsibilities, although `overall_owner` was correctly null).
Two other failures were over-specific oracle assertions, not wrong persisted
behavior: the promoted payment Task used “付款安排” rather than the expected
literal “付款时间”, and the normal two-deliverable assessment cited the actual
owner/action evidence rather than the separate sentence saying the deliverables
were independent. The v4 fixture/oracle and Task Agent guidance were refined for
these findings; the candidate replay after those changes is still required.
All case databases are isolated under `/private/tmp/project-centered-v4-full-75d78850`.

### Full v4 rerun at candidate `3a3070e2` (2026-10-05)

After the responsibility/owner rules and evidence alternatives were updated, a
fresh 19-case run completed **16/19**. The role-only task suppression,
overall-owner dispute, one-Project/multiple-source context, distinct deliverables,
same-source idempotency, same-reference versioning, same-ID promotion semantic
case, and exact Attention membership all passed in this run. Two cases ended in
`runtime_result_validation_failed` after a result-correction attempt
(`role-based-unnamed-suggestion` and `suggestion-promoted-same-id`); they are
unresolved runtime-contract failures, not semantic passes. The remaining
`completed-task-risk-persists` fixture lacked an explicit Project decision and
trusted completion linkage, so the Agent correctly retained an unresolved
Project clue and did not complete an ambiguously linked Task. The fixture is
being corrected to include the meeting's continuation decision, stable owner
identity, same conversation, and explicit reply-to reference. A new full replay
is required after this fixture correction. Artifacts:
`/private/tmp/project-centered-v4-candidate-3a3070e2`.

The corrected fixture was committed as `af893c10`; its fresh 19-case replay
completed **18/19**. The explicit Project-decision and trusted completion-link
fixture now passes, as do the two previously correction-sensitive cases. The
only failure is `peer-not-auto-member`: the Agent correctly kept the new
Wang Wu business-reconciliation responsibility in ProjectContext but also
created a source candidate from “王五负责另一项独立的商务对账”. That sentence
states a responsibility but contains no explicit action or action-item record,
so it must not create a second Task. This final finding led to one more prompt /
Skill rule: even when the responsibility names a distinct deliverable area,
`X负责Y` alone remains ProjectContext. The fixed native set must be rerun after
that rule is committed.
Artifact: `/private/tmp/project-centered-v4-final-af893c10`.

The Peer case was isolated and replayed once on `3e17adc2`: it passed with one
source-origin candidate for Li Si's explicit payment-schedule action, Wang Wu's
separate business-reconciliation responsibility stored only in ProjectContext,
one active Attention card with exactly the relevant payment Task, and no second
Task. This is targeted evidence; the full fixed 19-case replay at the revised
candidate is still required.

### Focused v4 native follow-up (2026-10-05)

The six-case native subset at candidate `ee02df12` completed **3/6** cases:
unknown overall owner, explicit responsibility-transfer conflict, and promotion
of the same suggested Task ID passed. The same-reference version and peer-member
cases failed before semantic comparison: both normal and result-correction
attempts recorded `runtime_result_validation_failed` / “No TaskAgentDecision
JSON found”; their stored result envelopes are empty. They are native runtime
result failures, not semantic passes. The completed-Task case ran successfully
but exposed both an oracle omission and an over-broad membership: actual state
contained the formal completed deliverable and the payment-risk suggestion
(two Tasks), while the Attention card also contained both. The corrected oracle
expects both Tasks but only the payment-risk member, and the Task Agent prompt
and shared Skill now explicitly exclude completed or unrelated Project Tasks
from an assessment's supporting members. Focused prompt/evaluator tests passed
(244 tests), Ruff and `git diff --check` passed. These changes still require a
fresh native replay; the prior artifact is immutable and remains evidence of the
earlier attempt.

A fresh peer-member replay at `15e2699c` completed after the runtime's normal
schema-correction attempt. It saved one payment follow-up suggestion with Li
Si as suggested owner, retained Wang Wu's distinct business-reconciliation
responsibility in ProjectContext, and the Attention card had exactly the one
payment-risk member. The prior fixture had required a second Task for that
independent responsibility; this contradicted the confirmed Project-first rule
and the instruction not to enumerate every small work item. The revised oracle
checks both distinct Project responsibilities and expects the single relevant
Task. This fresh case replay repeated twice at `3c7dca96`: one run failed when
correction hit `codex_provider_overloaded`; another failed result validation
because a suggestion also carried actual-owner fields. The same-reference case
also repeated “No TaskAgentDecision JSON found” on both normal and correction
attempts. Therefore the earlier successful peer domain readback is useful
semantic evidence, but the corrected fixed peer case has not yet passed its
evaluator. These native result-contract failures block the full comparison, W39
replay, PR merge and deployment.

The isolated completed-Task replay at `3c7dca96` persisted the expected two
separate Tasks: the formal验收材料 Task is `done`, the payment-risk suggestion
remains a candidate, and the active Attention card contains only the latter
Task. This confirms that completing the deliverable does not resolve the
Project risk and that the completed peer is excluded from Attention membership.
It is a focused persisted-row readback, not a substitute for the full fixed
native comparison.

For the current fixture revision, the source-only canonical input digest is
`b832fa601464e2a675b58661a2fd0792fb352973e18cca99393a1572701261f3`; the
full oracle fixture digest is
`b2ec9a6e40ab5f019ebe16bc2a59c6ada872be3518827772d3ba38b2786b3a9d`, and the
loaded candidate Skill digest is
`4249e1919a1f1b6dfcc084294ea47c94348add49b011acf22b1ca7f6fd5d2d69`.

The immutable W39 source copy was read-only backed up to
`/private/tmp/project-centered-w39-final-20261005.sqlite3` and opened using the
current Store code. All 403 Signal bodies and identity fields matched the source
hash exactly; Task and Project counts stayed 259 and 20; input 27465's hash was
unchanged; `quick_check=ok`; and the foreign-key check returned exactly the same
single pre-existing `meeting_alignment_runs` → missing
`meeting_alignment_jobs` row. No repair or original-copy write occurred. The
ordinary Store upgrade also introduced current-schema Project evidence/context
tables, populated Project evidence, and added default `origin=source` /
empty-suggestion fields to historical Tasks; the existing `business_object_tasks`
rebuild rewrote its timestamps. That timestamp behavior predates this change and
is recorded as a migration observation, not attributed to the Project-centered
source-body migration. Exact W39 replay and business-domain readback remain pending.

The approved design is `superpowers/specs/2026-10-04-project-centered-work-design.md`;
the implementation checklist is `superpowers/plans/2026-10-04-project-centered-work.md`.
Tasks 1–7 are one release unit. Core integration is saved in `eeb69de9`; API/UI
integration is saved in `707d41f2`. Neither commit has been deployed by this workflow.
Task 8's deterministic evaluation scaffolding is verified. The first fixed native
baseline/candidate comparison ran on 2026-10-04 and did not pass; a candidate
rerun after prompt clarification, real W39 validation, PR and release remain pending.
Local tests and synthetic browser checks do not establish a live business effect.

## Fixed evidence and comparison procedure

`tests/fixtures/task_project_centered_v4.json` contains 19 version-4 cases. Both
runtimes receive the same original `source_inputs` in the same order. Native
cases have empty `existing_context`: neither side receives manually seeded
Project roles, owner, risk or conclusions. Unit tests may seed persisted facts
to check the evaluator, but these seeds are not native comparison evidence.
Each source version is enqueued immediately before its turn; enqueuing all
versions first would overwrite earlier bodies sharing a source reference.

Expected values remain offline and are compared only after actual application.
They cover Project identity, saved context and responsibilities, actual versus
suggested Task state, every relevant Project judgment, actual receipts and
Attention membership. Optional supporting membership may specify a bounded
`allowed_task_counts` in an assessment; it does not relax expected Project,
outcome, evidence or application status. An independent Task with no Project
has an explicit empty assessment set. An unconfirmed Project clue instead
requires an insufficient-evidence judgment without an invented official ID.

Only the final promotion, selective-member and completed-Task/persistent-risk
cases permit `allowed_application_statuses=["applied", "existing"]`: the current
contract permits refreshing a risk proposal or verifying a saved card. This
bounded oracle choice was reviewed before native execution, follows the source
facts, and does not waive actual card/anchor/evidence/member checks. Other cases
keep exact application statuses. A receipt status alone never proves card ID
continuity.

The cases include zero-Task risk, normal progress, ambiguous risk, no-report
meeting/chat context, a two-Project report, three-source synthesis, role-based
suggestions and settled negative evidence, unknown owner, responsibility
change/conflict, human promotion of the same suggested Task, same Task updates,
distinct deliverables, repeated sources, changed versions of one reference,
selective Attention membership and a completed Task whose Project risk persists.

The existing `scripts/replay_task_attention.py` runs the runtime from an explicit
`--code-root`. Pin the old baseline at `da368453` and the candidate at a clean
committed revision; set each side's own CI Skill root. Use the same configured
`codex_oauth` / `gpt-5.6-luna`, effective 900-second total / 300-second idle
budget and concurrency 1. Record actual route, model, code revision and Skill
hash in each fresh artifact. Old experiment artifacts are historical evidence,
not substitutes for this comparison. Native execution has not yet occurred for
this fixed Project-centered fixture.

## Readback and observability

The evaluator reads actual domain rows, not the Agent's summary. The full
`business_*` snapshot includes original bodies, Signals, Project context revisions,
Task dates, links and events. Repeating identical source input must leave that
domain unchanged; Agent runs and attempts are counted separately. Same-ref
version expectations count distinct stored source documents, not Signal rows.
Project titles are compared as a multiset: two same-title official rows are not
one Project. Ambiguous identities cannot choose an arbitrary owner's context.

The evaluator checks the schema actually present. Missing current Project
context/evidence/shared-body tables are reported as
`project_centered_storage_missing`, with explicit missing tables and FAIL. It
does not migrate the baseline, substitute candidate modules, fabricate receipts
or add a production compatibility layer. Its observed-source rule and exact
raw-text/one-JSON-leaf quotation oracle are independent of runtime modules.
Successful old turns still receive subsequent original inputs despite comparison
failures. Real execution, run or projection failures stop the sequence; every
step's original failure remains recorded.

Per-turn `context_deliveries` records the metrics actually delivered to the
runtime: document/Signal counts, full/visible character counts, truncation,
visible ranges and citation-budget status when present. It omits source bodies.
Absent historical metrics stay absent. These are delivered ranges, not proof
that the Agent read or understood them. The existing read-only inspector accepts
an optional `--replay-result` artifact and attaches these metrics only to its
matching persisted input/run. Historical runs are not rewritten or reconstructed.

## Deterministic verification record

Regression counterexamples reject wrong owner, suggestions falsely made formal,
missing second Project, an unsaved zero-Task card, duplicate Task/Project rows,
outbound intent growth, wrong same-count Attention members, insufficient source
versions, duplicate-source domain changes and missing Project judgments.

The initial old-schema counterexample failed with `OperationalError` before the
evaluator-only fix; the initial duplicate-Project counterexample returned no
failures because title dictionary keys collapsed rows. After repair, focused
source chronology / missing-schema tests passed (3 tests). A readback smoke using
the actual pinned `da368453` Store returned FAIL with all three missing tables,
without importing candidate runtime modules. This was not an Agent/native run.
The all-case assessment assertion also failed against the incomplete fixture;
all fixed original-source cases now define their expected final judgments.
Final primary verification: four targeted files, **191 passed / 14.56s**,
Ruff and diff check passed. Independent final scaffold/oracle review: three
tool/evaluator files, **40 passed / 2.87s**, no outstanding must-fix. Positive
bounded-status tests validate the current decision model before recording it;
rejected/error receipts still fail. No native PASS is claimed here.

Frozen SHA-256 values for this scaffold:

| Artifact | SHA-256 |
| --- | --- |
| Version-1 fixture | `cd5ac2387694f3457d24066aa5cb3917a2494a7a893da3c1d6c73f06ad445e34` |
| Source-only canonical input | `93ea623065006242182f281800cf7b82621926529a177e996b3da394a57cea1d` |
| New evaluator tests | `c999e44a4eacdf313a235f5f738cab3fd50d61eda2b1936bbb21c2a8f030b636` |
| Candidate CI Skill | `11c5df5b30aec5acd3df7e31be3bca740609db02147875ad1831d898d1d357c2` |

The source-only digest is computed with
`jq -cS '[.cases[]|{case_id,source_inputs,work_item}]'` followed by SHA-256.
The review-stage fixture changes touched offline expectations only, before the
first native execution. See the comparison addendum below; each native artifact
records its clean committed code revision and actual loaded Skill hash.

## Post-main-merge candidate smoke (2026-10-04)

The feature branch was normally merged with the then-current `origin/main`
(`60125400`) in merge commit `63c5bf0e`; the merged tree is clean. The pinned
old baseline remains `da368453`. Candidate `63c5bf0e` still contains the frozen
Task-centered fixture/oracle and CI Skill SHA-256 remains
`11c5df5b30aec5acd3df7e31be3bca740609db02147875ad1831d898d1d357c2`.

After the merge, the 15 directly affected Task/Project/evaluator/API test files
passed (**1202 passed / 196.01s**); the two-file Task 8 evaluator/inspector
check passed (**25 passed / 1.94s**). These verify deterministic behavior only.

A one-case native candidate smoke initially used the planned `codex_oauth` /
`gpt-5.6-luna` route, 900-second total / 300-second idle limits, concurrency 1,
and the candidate's CI Skill root. The Codex CLI was present (`0.154.0`) and
reported logged in, but no Task Agent turn reached the model: the production
runtime probe could not configure the service MCP manifest. Direct command
construction identified the missing required environment variable
`MEMORY_CONNECTOR_URL`; `CONNECTOR_API_KEY` is also absent from the local shell,
the production `.env`, and the launchd plist. The replay recorded a failed run
with no runtime attempts and no Project, Task, or Attention rows. This is a
runtime-configuration failure, not a native semantic result. At that point, the
fixed native baseline/candidate comparison remained unrun; do not treat this
configuration-blocked smoke as a candidate pass or failure. The subsequent full
comparison and MCP configuration resolution are recorded below.

The frozen W39 database had the unrelated `meeting_alignment_runs` row 2298 →
missing `meeting_alignment_jobs` row 4908 foreign-key reference, which blocked
opening that exact frozen copy for a complete migration and replay. A later
W39-derived migration-function round trip is recorded below; it does not replace
the missing pristine pre-migration comparison. The real W39 semantic
replay/readback remains blocked. No orphan was repaired or bypassed, and no
production semantic import/apply, push, PR, or deploy was performed at this
stage. Task 9 remains unstarted.

Earlier implementation checks: 14 core files / 636 passed; multisource plus
Project readback / 161 passed; API four files / 25 passed; frontend eight files /
86 passed and production build passed. Browser checks used synthetic fixtures,
light/dark and 1600/433/320 CSS-pixel widths, not production business data.

## Native MCP configuration and first fixed comparison (2026-10-04)

The earlier smoke's MCP-manifest blocker was resolved without copying credentials:
the isolated replay process explicitly used the service's existing
`data/config/service-mcp.json` through `CEO_SERVICE_MCP_CONFIG_PATH`. The manifest
uses native CLI OAuth configuration for its connected MCP servers. A read-only
preflight verified the manifest and the `codex_oauth` / `gpt-5.6-luna` route;
no production database was opened or changed.

The first full 19-case fixture was then replayed sequentially on fresh isolated SQLite
databases, using the same original inputs, model, route, timeout and concurrency
for the pinned baseline (`da368453`) and candidate (`f45a7a7a`), with each side's
own CI Skill root. The native database artifacts are retained under
`/tmp/project-centered-eval.NFdB25` for readback. Baseline: **0/19** cases passed
the new Project-centered contract; old-schema/missing-Project-storage failures
were common and are expected limitations of that baseline, not isolated evidence
of a semantic regression. Candidate before the prompt-contract clarification:
**2/19** passed all current fixture assertions (`single-report-two-projects`,
`unconfirmed-project`). This is not a release pass.

The failed candidate cases exposed two separate issues. First, the fixture expects
the short title `甲客户一期交付` even in source sentences that say
`甲客户一期交付项目`, while the current prompt explicitly asks the Agent to
preserve the authoritative source title; title-dependent context and assessment
assertions then cascade-fail. The fixture/source naming contract needs review;
the oracle was not changed after seeing the output. Second, native output
validation repeatedly rejected assessment selectors containing both
`anchor_id` and `project_decision_index`, Task updates that set status/relevance
without `transition=update_fields`, and assessment support lists containing
`skip` decisions. The Pydantic validators are intentional; the prompt previously
did not state these exact constraints clearly enough.

The Task Agent prompt and validation-repair prompt now state those existing
contracts explicitly. Regression assertions were added and the full
`tests/test_task_agent.py` file passed (**225 passed / 14.64s**), with Ruff and
`git diff --check` clean. The candidate rerun against this first clarification is
recorded in the following section. Further prompt clarifications made after that
rerun still need native verification. The W39 foreign-key orphan blocker below is
unchanged.

## Prompt-contract candidate rerun (2026-10-04)

Commit `4b29348e` clarified assessment selector choice, disallowed `skip` indexes,
and the existing `update_fields` transition. A second sequential 19-case candidate
run used the same fixture, `codex_oauth` / `gpt-5.6-luna`, timeout, concurrency,
and candidate Skill root on fresh isolated databases under
`/tmp/project-centered-eval.NFdB25/candidate-guidance-*.sqlite3`. Only
`unconfirmed-project` passed all current assertions (**1/19**). The run still
showed title-oracle mismatches, Task/task-count mismatches and intermittent invalid
TaskAgentDecision output. Examples include status/relevance without the required
transition, a promotion that still carried a suggestion field, and
`project_link_evidence` without a Project selector. This single run does not prove
the prompt change made aggregate semantic quality worse; the native Agent is
non-deterministic, and the comparison is not repeated-sample statistical evidence.

The prompt now also states that new/skip decisions leave status and relevance
unset, promotion omits the suggestion field while preserving its saved history,
and project-link evidence requires a selected Project. These clarify existing
model-validator rules and do not weaken or bypass them. Regression coverage and
the full Task Agent file again pass (**225 passed / 9.63s**); Ruff and
`git diff --check` are clean. The rerun is documented in the next section; the
project-title source/oracle contract remains unresolved. No complete
fixed native comparison, W39 verification, PR, or deployment is claimed.

## Second prompt clarification native rerun (2026-10-04)

Commit `4cf23b9f` additionally clarified that create/candidate/skip decisions
leave status and relevance unset, suggestion promotion omits the suggestion
field, and Project link evidence requires a selected Project. The same 19 fixed
candidate cases were run sequentially on fresh databases under
`/tmp/project-centered-eval.NFdB25/candidate-contract2-*.sqlite3`, with the same
source inputs, model/route, timeout, concurrency and candidate Skill hash.
**2/19** passed (`single-report-two-projects`, `unconfirmed-project`); this is
still a clear fixed-eval failure, not a release candidate. Title-oracle mismatch
continues across cases whose source uses `甲客户一期交付项目`; the suite expects
`甲客户一期交付`. Task lifecycle/assignment outputs also remain inaccurate or
intermittently schema-invalid: this rerun again logged a status/relevance
transition error, and the responsibility-conflict, independent-Task,
same-source/version and completed-Task/risk cases did not pass. More prompt
wording alone is not established as sufficient. The fixture/title contract needs
an explicit resolution, and the remaining business-judgment failures need their
own expected-vs-actual review before any further algorithm change.

Readback of `candidate-contract2-project-risk-without-task.sqlite3` confirms that
at least some apparent title-related “missing context” failures are evaluator-key
cascades, not absent stored context: the Project row is titled
`甲客户一期交付项目`, its latest revision contains the payment-date fact, Project
evidence contains both meeting and chat Signals, no Task was created, and an active
Attention item is linked to that Project. The oracle looks up context/card identity
under `甲客户一期交付`, so it reports context, evidence and Attention mismatches
for this exact-title difference. This evidence does not resolve whether the desired
business title should retain or drop the generic suffix “项目”.

The third run used commit `4cf23b9f` and the same loaded Skill SHA-256
`11c5df5b30aec5acd3df7e31be3bca740609db02147875ad1831d898d1d357c2`. The full
`tests/test_task_agent.py` file passed (**225 passed / 9.63s**), Ruff and
`git diff --check` were clean. The semantic/native gate remains failed; W39,
independent review, PR, and release gates are still pending.

## Real W39 and release blockers

The preserved frozen W39 database has 259 Tasks, 16 Projects, 0 Attention,
398 Signals and 434 Task events; `quick_check=ok`. Its input 27465 includes
中汽创智、岚图、项目管理、Einride POC and 抽检包生命周期 clues. The department
heading 项目管理 must not be invented as an official Project. All relevant
clues require first-pass actual judgment or a stated identity/evidence limitation;
a second pass finding an earlier omission is not first-pass success.

The full frozen copy's pre-existing foreign-key failure is
`meeting_alignment_runs` row 2298 referencing missing `meeting_alignment_jobs`
row 4908. Source migration correctly rejected it and rolled back: original
Signal bodies, Tasks, run/event history and old schema marker remained intact.
The live database has the same independently observed unrelated orphan. No
repair, orphan deletion, ignored integrity check or focused replacement snapshot
has been authorized or performed by this workflow. Choices already requested
from Derek—an equivalent Task-domain-only comparison snapshot, and a separately
scoped production integrity repair—remain unanswered.

The empty temporary database used only for the pinned-Store smoke check was
moved to Trash after verification; it remains recoverable. The original frozen
W39 database and its migration-failure evidence were not removed or changed.

Do not deploy around this limitation. Once native comparison and real-data
verification are complete, require the approved PR, same-version loaded Skill,
standard `python -m app.deploy`, and actual PID/health/queue/Attention/History and
page readback. No production checkout edits, direct SQL card creation, automatic
assignment or whole-database business promotion belong to this release.

## 2026-10-05 resumed completion pass

The confirmed native output-contract defect was fixed: `TaskAgentCodexRunner`
previously requested no Codex output schema despite advertising
`structured_output`. The Task Agent now passes the checked strict schema through
`--output-schema`; `task_agent_output_schema()` derives it from the Pydantic
contract, requires every object property, removes unsupported `$ref` siblings,
and narrows the legacy arbitrary evidence dictionaries to their known or empty
wire shapes. The local parser and one same-session correction remain in place.
Regression tests were added before the implementation and showed both missing
schema selection and missing schema artifact as failures.

Verification: `tests/test_task_agent.py` and
`tests/test_work_tracking_skill.py` passed (**232 passed**); fixed evaluation
oracle/inspector plus semantic-store coverage passed (**517 passed**); Ruff,
`git diff --check`, schema parity and strict-object checks passed. A direct
synthetic `codex exec --model gpt-5.5 --output-schema ...` request generated a
valid `TaskAgentDecision`; its `null` default-list values also passed local
Pydantic parsing. This proves the strict schema can be used by the CLI, not that
the complete Task Agent workflow or business judgments pass.

The normal isolated replay entrypoint was also retried on fresh synthetic DBs.
It made no Project/Task/Attention writes and failed before any runtime attempt:
`no_eligible_route:codex_oauth=paused:runtime_probe_failed` (empty
`runtime_attempts`). Therefore fixed 19-case native candidate results remain
unverified; the native gate is not passed. The failure is at runtime capability
probe/route availability, not evidence that the corrected output schema was
rejected. A successful direct CLI smoke request does not substitute for the
production router probe.

Read-only inspection of the preserved local W39 working copy
`/private/tmp/project-centered-w39-final-20261005.sqlite3` found it is already on
Store schema `2026-10-04.4`, with 259 Tasks, 20 Projects, 403 Signals, 83 shared
source documents and 3 Attention items; `quick_check=ok`. Input 27465 is already
`skipped` and has prior runs 10634/10662; its latest stored projection includes
five Project registrations and three applied Attention proposals. This is a
readback of an already processed/migrated copy, not a fresh W39 replay, a repeat
idempotency test, or a frozen before/after comparison. The earlier note that this
copy still had the source-body migration blocked is stale for its current state;
the true pre-migration frozen baseline was not located or changed in this pass.

No PR, push, production deploy, or live Task page verification was performed:
the native route gate remains unavailable and the current W39 artifact cannot
serve as the untouched baseline. Do not treat prior persisted Attention or this
schema smoke request as release approval.

### Fixed v4 full replay on 05fa0c69

The complete serial native replay used 19 fresh databases, the configured
`codex_oauth` route with `gpt-5.6-luna`, the CI Skill SHA-256 recorded in each
result, 900-second total timeout, 300-second idle timeout and concurrency 1.
Fourteen cases passed. Five failed: two oracle mismatches
(`meeting-chat-no-report`, whose frozen expectation incorrectly required a Task
for routine planned progress; `peer-not-auto-member`, whose required fact phrase
did not match the source wording), two evidence-boundary failures (split but
individually valid citations in `same-project-three-sources`, and an invented
historical quote in `suggestion-promoted-same-id`), and one existing-card
membership rejection in the completed-Task/persistent-risk case. That final case
must keep the existing card's empty member list; no new payment Task is justified
without saved responsibility evidence.

The frozen oracle now keeps routine planned progress at zero Tasks, allows
multiple exact supporting excerpts for one assessment, matches the original
wording of the peer update, and does not expect a payment-follow-up Task absent
a saved responsibility. Prompt/Skill guidance now explicitly forbids adding a
Task updated in the current turn to an existing Attention card whose delivered
membership is empty, and forbids reconstructing historical quotations from
later summaries. Focused tests passed after these changes; a complete clean-
commit 19-case replay is still required. The 05fa0c69 result is diagnostic, not
the final gate.

### Full v4 replay on 8afb72c1 (2026-10-05)

The next 19-case native replay passed 15/19. Four cases remained red:
`existing-task-update` returned an invalid update/acceptance combination;
`repeated-same-source` ended in a Codex idle timeout (transport failure, not a
semantic result); `peer-not-auto-member` omitted the concrete verify-and-report
action stated in the source; and `completed-task-risk-persists` failed to keep
the project-level risk assessment and its suggested next step coherent.

The follow-up prompt and Skill now distinguish a specific verify-and-report
action from a bare duty-area responsibility, state that progress is not owner
acceptance, and permit the saved single Project overall owner as a display-only
coordinator suggestion for a project-wide risk when appropriate. The peer-case
oracle now checks the source's stable “payment uncertainty” phrase. The
completed-task case again expects the concrete payment-date follow-up suggestion
to the overall owner; this is a proposed next step, not a formal assignment.
These changes are covered by prompt/Skill regression assertions and the fixture.

The four-case failures have not yet been demonstrated resolved by a fresh full
native replay. Focused tests passed after the prompt/fixture changes, but the
fixed semantic gate remains open. The earlier 05fa0c69 interpretation that
forbade a project-wide suggestion without a saved specific responsibility was
too narrow and is superseded by the clarified single-overall-owner rule above.

### Full v4 replay on 3ae6e33a (2026-10-06)

The 19-case serial native replay used fresh temporary SQLite databases and the
same `codex_oauth` / `gpt-5.6-luna` route, 900-second total timeout, 300-second
idle timeout, concurrency 1 and Skill hash recorded in each result. It passed
15/19. The previously observed `existing-task-update`, `peer-not-auto-member`
and `repeated-same-source` cases passed after the prompt/fixture changes.

Four cases remain unresolved:

- `role-based-unnamed-suggestion`: material payment-date/cash impact was assessed
  and the saved Project role `王五负责商务回款` was preserved, but no display-only
  candidate next step was emitted.
- `single-report-two-projects`: the model emitted two Project decisions and
  Tasks for both report rows, but after schema feedback emitted an assessment
  only for the first Project; the correction turn failed the same strict contract.
- `distinct-deliverables`: both independent source Tasks and Project evidence
  were extracted, but the second Task's `project_link_evidence` spliced the
  Project-definition sentence and later action sentence, omitting intervening
  source text. The exact-source validator correctly rejected that output.
- `completed-task-risk-persists`: the completed acceptance-material Task was
  correctly excluded from the existing risk card's empty membership, but the
  model emitted no new payment-date suggestion for the still-active Project risk.
  The oracle's former expectation of one Attention member contradicted the
  preserved empty-membership rule; it is corrected to zero while retaining the
  separate candidate Task expectation.

The candidate run is at `/private/tmp/project-centered-v11-full-3ae6e33a/results.jsonl`.
Follow-up prompt/Skill rules and regression assertions now explicitly require a
candidate when a material risk maps directly to a saved Project responsibility,
one assessment for each Project decision index, and contiguous unsynthesized
Project-link quotations. Focused prompt-contract tests pass. Those changes have
not yet been validated by another native replay; do not treat 15/19 as passing.
The clean W39 before-migration snapshot remains unavailable, so the data
comparison/idempotency and release gates remain blocked independently of this
semantic replay.

### Targeted v4 rerun on `f6a3714d` (2026-10-06)

The three semantic/contract follow-up cases passed on fresh native runs:
`role-based-unnamed-suggestion`, `single-report-two-projects`, and
`distinct-deliverables` all passed with code revision `f6a3714d` and loaded Skill
SHA-256 `72a5d26dbdb0a26afefff352f928728a651482c5dccff1ef75ed9b9787938055`.

`completed-task-risk-persists` first ended in `codex_idle_timeout`; a fresh retry
produced the expected Project-linked candidate Task for the unresolved payment
risk. The projector attached that candidate to the new Attention card during the
first source step, so the second step correctly received a card whose stored
membership contains that Task. The later `task_ids: [2]` therefore preserves, and
does not expand, the delivered membership. The `2d4e5c83` result was marked failed
only because the temporary fixture edit incorrectly expected zero members and
zero assessment members. The fixture has now been restored to expect one member
and the payment-date candidate. The “do not expand an empty card” prompt rule
remains valid for cards that are in fact delivered with no members. A fresh replay
with the corrected oracle and the full 19-case replay have not yet run on this
revision. Targeted run artifacts are retained in
`/private/tmp/project-centered-v12-targeted-f6a3714d/results.jsonl` and
`/private/tmp/project-centered-v12-risk-retry-f6a3714d/results.jsonl`.

### Full v4 replay on `ad65d0bb` (2026-10-06)

The final candidate full run used 19 fresh databases, the unchanged
`codex_oauth` / `gpt-5.6-luna` route, 900-second total timeout, 300-second idle
timeout and concurrency 1. It passed 18/19. The completed-risk case, including
the corrected one-member oracle, passed. The sole failure was
`responsibility-change-conflict`: overall_owner correctly remained null and both
competing claims were preserved, but the new Project fact said the transfer had
not reached consistent confirmation / still needed verification, without
explicitly saying “总体负责人存在冲突”. The confirmed contract and frozen oracle
require that conflict to be stated plainly in a Project fact. The prompt and Skill
now include the exact semantic requirement and prohibit using only the softer
phrases. The full run is recorded at
`/private/tmp/project-centered-v15-full-ad65d0bb/results.jsonl`; a fresh native
rerun on the corrected final candidate is still required.

### Final candidate local and native follow-up (`388effc7`, 2026-10-06)

The final owner-conflict wording is committed as
`388effc734534ed79f8d7a608cedb4e2115fde2e`; the CI Skill SHA-256 is
`4749583bbd0f82695b34dfaaa2066863ce3e87a920398136c3c5e89b8708bbe5`.
Focused local verification passed: `tests/test_task_agent.py`,
`tests/test_task_project_centered_eval.py`, and `tests/test_work_tracking_skill.py`
reported 253 passed; `tests/test_task_semantic_store.py`,
`tests/test_task_project_centered_eval.py`, and
`tests/test_inspect_task_attention.py` reported 519 passed. Ruff, fixture JSON
parsing, and `git diff --check` also passed.

A fresh isolated native run of `responsibility-change-conflict` used the pinned
`codex_oauth` / `gpt-5.6-luna` route and a new temporary database at
`/private/tmp/project-centered-v18-owner-388effc7/owner.sqlite3`. It did not
produce a Task Agent decision: the only runtime attempt ended after the configured
15-minute limit with `codex_total_timeout`. This is an unavailable semantic
result, not a pass and not a semantic failure. Earlier in-sandbox attempts also
failed before model execution because Codex could not write its own `~/.codex`
state; allowing the native CLI to access its normal home resolved that specific
sandbox failure but not the timeout. The owner-conflict rule therefore remains
without a fresh native semantic result. The complete fixed v4 replay, frozen
pre-migration W39 comparison, idempotency replay, PR, deployment, and production
readback remain open.

### Full v4 replay on `5ced17fd` and assessment-support follow-up (2026-10-06)

The 19-case native replay used fresh SQLite databases, the `codex_oauth` /
`gpt-5.6-luna` route, 900-second total timeout, 300-second idle timeout, and
concurrency 1. It completed **18/19**. Only `existing-task-update` failed with
`project_assessment_receipt_mismatch`: the turn updated the formal Project-linked
Task for the exact acceptance-material work described by its `not_needed`
progress assessment, but returned empty `decision_indexes` and `task_ids`, so the
persisted assessment receipt did not link the updated work. The other 18 cases
passed. Results are at
`/private/tmp/project-centered-v20-full-5ced17fd/results.jsonl`.

The follow-up prompt/Skill and architecture/runtime contract now explicitly say
to include a Task decision when it updates the same concrete Project work as the
assessment (including `not_needed` progress), while excluding unrelated Project
peers. Regression prompt test was confirmed RED before the prompt update. After
the update, `tests/test_task_agent.py` plus
`tests/test_work_tracking_skill.py` passed 232 tests, and
`tests/test_task_project_centered_eval.py` passed 21 tests; JSON fixture parsing
and `git diff --check` passed.

A fresh native rerun of the repaired case is not yet verified. Launching the
standalone replay from this task's current shell failed before model execution:
the production service MCP manifest could not resolve `MEMORY_CONNECTOR_URL`,
and the Codex OAuth capability probe therefore reported `runtime_probe_failed`
with no Task Agent runtime attempt. The direct CLI smoke itself works, but it is
not a substitute for the routed replay. Do not mark the prompt repair as native
verified or use the 18/19 baseline as a passing fixed evaluation. The next
verification needs the same initialized service-MCP environment as the full
replay, followed by the complete fixed v4 replay. The migration function's
idempotency passed on a W39-derived reconstruction, but equivalence against the
pristine pre-migration W39 database remains unverified. PR, deployment and
production readback also remain open.

### Read-only production-schema baseline check (2026-10-06)

A read-only SQLite online backup of the production database at
`/Users/derek/Services/ceo-agent-service/data/auto-reply.sqlite3` verified as
schema `2026-09-25.1`, `quick_check=ok`, zero foreign-key violations, and no
`source_document_id` column. The Business Task migration tables had no records
(`business_task_signals`, `business_tasks`, `business_task_evidence`,
`business_task_events`, `business_task_anchor_links`, and `business_projects`
all contained zero rows). This confirms the production file is an older-schema
database but cannot establish real-record migration equivalence or substitute
for the missing W39 snapshot. The temporary backup was deleted after the
readback; production was not modified.

### W39-derived source-body migration round trip (2026-10-06)

The retained post-migration W39 artifact at
`/private/tmp/project-centered-w39-final-20261005.sqlite3` had 403 Signals, 83
shared source documents, 259 Tasks, 443 Task evidence rows, 443 Task events, 40
Project links and 20 Projects. Because its original pre-migration file was not
available, a new temporary copy was reverse-reconstructed into the immediately
pre-source-document Signal table shape, restoring each Signal body from its
saved shared document, then the current
`AutoReplyStore._migrate_business_task_source_documents` function was executed.

The migration preserved all 403 public Signal rows and the listed Task/Project
evidence, event and link rows; it recreated 83 distinct source documents. The
single pre-existing foreign-key violation (meeting alignment run 2298 referring
to a missing job) was unchanged. A second migration invocation was idempotent.
This is useful W39-volume, W39-data migration-function evidence, but it is not a
comparison against the missing pristine pre-migration snapshot: the source
state was reconstructed from the retained migrated artifact. Keep that boundary
explicit. The temporary 3.2 GB reconstructed copy was removed after recording
the result; the retained W39 artifact and production database were not modified.

### Current focused verification after assessment-receipt prompt fix (2026-10-06)

At clean candidate `689869a6bb65bf510e83213925a506deb4374365`, the focused
offline evaluator/diagnostic tests completed **27 passed**:
`tests/test_task_project_centered_eval.py` and
`tests/test_inspect_task_attention.py`. A separate rerun of the Task Agent,
shared Skill, and evaluator contract tests completed **253 passed**; Ruff and
`git diff --check` passed. These runs verify the prompt contract and evaluator
locally, not the native model behavior.

Frozen candidate artifacts:

- `app/task_agent.py`: `4e1b1a34fa7b8002be7b036b706a2014421964ff695ffe38dbbcf4260f46f74d`
- `ci/shared-skills/ceo-work-tracking/SKILL.md`:
  `9b171396738f800dae398d356e9c8f4bf9695ef8e9cbf590981a66426aa2c9b8`
- `tests/fixtures/task_project_centered_v4.json`:
  `8ef185f96117fa71523a152d92d7fe9846aba4662d58a5a05d7887f11eccffef`

The formal fixed native replay remains unverified. This shell cannot resolve
the service MCP manifest because these non-production environment variables are
not configured: `MEMORY_CONNECTOR_URL`, `CONNECTOR_API_KEY`,
`MEMORY_CONNECTOR_AUTH_TYPE`, and `MEMORY_CONNECTOR_CONTENT_TYPE`. The routed
probe failed before a Task Agent attempt; a direct CLI smoke is not equivalent.
Do not inject production credentials or remove MCP from the candidate to claim a
formal pass. Next steps remain: provide a safely initialized non-production
service-MCP environment; run the full fixed 19-case replay; and only then finish
the W39/business, PR, deployment and production-readback gates. CRM customer
implementation also awaits Derek's review of the written addendum in the
design specification.
