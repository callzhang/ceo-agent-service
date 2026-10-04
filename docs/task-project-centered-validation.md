# Project-centered work validation

## Scope and completion level

The approved design is `superpowers/specs/2026-10-04-project-centered-work-design.md`;
the implementation checklist is `superpowers/plans/2026-10-04-project-centered-work.md`.
Tasks 1–7 are one release unit. Core integration is saved in `eeb69de9`; API/UI
integration is saved in `707d41f2`. Neither commit has been deployed by this workflow.
Task 8's deterministic evaluation scaffolding is being verified. Fixed native
baseline/candidate comparison, real W39 validation, PR and release remain pending.
Local tests and synthetic browser checks do not establish a live business effect.

## Fixed evidence and comparison procedure

`tests/fixtures/task_project_centered_v1.json` contains 19 version-1 cases. Both
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
The review-stage fixture changes touched offline expectations only, before
native execution. Each future native artifact must record its clean committed
code revision and actual loaded Skill hash rather than assume these values.

Earlier implementation checks: 14 core files / 636 passed; multisource plus
Project readback / 161 passed; API four files / 25 passed; frontend eight files /
86 passed and production build passed. Browser checks used synthetic fixtures,
light/dark and 1600/433/320 CSS-pixel widths, not production business data.

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
