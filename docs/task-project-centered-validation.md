# Project-centered work validation

## Scope and completion level

2026-10-05 continuation: Derek confirmed the exact source Project title and the
migration rule to preserve the original foreign-key violation set without repair.
The fixed fixture is now version 4: source titles follow the source's full formal
name; source records carry the runtime's actual AI Minutes action-item or
authorized-assignment metadata; and report cases include an exact registry row.
Task Agent prompt and shared Skill now require a
complete ProjectContext snapshot on new facts, an actionable display-only next
step for material unresolved Project risks based on saved responsibilities, and
no such suggestion for routine/settled/ambiguous evidence. Focused verification
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

The pre-existing W39 migration blocker is unchanged: the frozen database has
the unrelated `meeting_alignment_runs` row 2298 → missing
`meeting_alignment_jobs` row 4908 foreign-key reference, so Task 1's complete
real-copy migration and Task 8's real W39 replay/readback remain blocked. No
orphan was repaired or bypassed, and no production semantic import/apply,
push, PR, or deploy was performed. Task 9 remains unstarted.

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
