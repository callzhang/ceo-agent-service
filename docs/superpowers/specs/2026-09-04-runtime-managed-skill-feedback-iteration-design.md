# Runtime-Managed Skills and Feedback Iteration Design

## Purpose

Make Skills a first-class, runtime-managed capability of CEO Agent Service.
Users may create, revise, enable, and trial a Skill locally in Settings without
first publishing it to Git.  A service restart loads one explicit, immutable
configuration of Skill revisions.  User feedback can then improve the service
through a bounded, evidence-backed iteration flow that chooses among a Skill
revision, runtime configuration change, code change, or a combination.

This design replaces the current Settings behavior that edits a repository
`SKILL.md` and synchronizes it into `~/.agents/skills`.  Settings becomes the
runtime control plane; it does not directly manipulate an arbitrary Skill
directory.

## Confirmed product semantics

- A user may create a new Skill and enable it locally without a Git commit or
  release.
- Every save creates an immutable Skill revision.  Saving never overwrites the
  currently enabled revision.
- Enabling a revision immediately updates the configuration for the *next*
  service start.  It never changes the Skills of a live Agent invocation.
- A restart and an actual-load receipt make a configuration effective.
- Git is an optional explicit export and long-term engineering archive, not a
  prerequisite for trial, activation, or feedback resolution.
- Feedback storage and viewing are always available.  The separate
  `feedback_iteration` system capability controls only self-optimization.
- With feedback iteration disabled, the `处理反馈` control is visible and
  disabled.  It creates no Agent session and performs no feedback API mutation.
- User-facing unfinished feedback is `open`; the persisted lifecycle remains
  `pending -> processing -> resolved`, where open is the pending/processing
  set.  Reopening a resolved item returns it to pending.
- Completion is `resolved`; there is no second completed status.

## Scope and non-goals

### In scope

- Immutable locally managed Skill revisions and active runtime configurations.
- Startup-time loading, receipts, activation, rollback, and Settings views.
- Registration and management of the feedback-iteration system capability.
- A structured feedback iteration decision: `skill_only`, `runtime_config`,
  `code`, `mixed`, or `needs_human`.
- Path-specific completion evidence that permits a locally managed Skill-only
  change to resolve feedback without a Git commit.

### Out of scope

- A remote Skill marketplace, remote authentication, or cross-machine sync.
- Replacing generic Codex Skills, plugins, or their installation mechanism.
- Editing existing repository Skill folders from Settings.
- Automatically publishing a local Skill revision to Git.
- Adding an audit, authorization, or external-effect policy to unrelated
  business features.

## Architecture

### Managed Skill model

The runtime database owns these append-only or versioned records:

| Record | Responsibility | Mutability |
|---|---|---|
| `managed_skills` | Stable local identity, display name, description, ownership state | Limited metadata changes only |
| `managed_skill_revisions` | Full UTF-8 `SKILL.md` content, SHA-256, parent revision, source, timestamps, optional feedback linkage and validation result | Immutable |
| `runtime_skill_configs` | One versioned set of enabled bindings for the next process start | Immutable after creation |
| `runtime_skill_bindings` | A config's `skill_id`, exact `revision_id`, enabled flag, load order, and purpose | Immutable with its config |
| `runtime_skill_load_receipts` | Process ID, config version, loaded revisions/SHA, failures, startup timestamp | Append-only |

`managed_skill_revisions` validates the existing Skill frontmatter contract:
name, description, valid UTF-8, and `metadata.managed_by`.  A revision stores
the raw body and SHA so a future load is reproducible.  A new revision may be
created from a user edit, feedback iteration, or an explicit repository import.

The initial migration imports the current service-owned repository Skills as
immutable revisions.  It does not scan, edit, or copy user, plugin, or generic
Skill directories.  Existing repository source remains a compatibility/import
source until the prior direct-write Settings endpoints are retired.

### Runtime configuration and process boundary

A Settings edit produces a new config revision, for example:

```text
config #24: active
  ceo-message-triage -> revision #31, enabled
  ceo-feedback-iteration -> revision #6, enabled

user saves revision #32 and enables it
  -> config #25: pending_restart

next service process starts
  -> validates config #25 and loads exact revision bodies
  -> writes load receipt with config #25, revision #32 and its SHA
  -> config #25: active
```

The current active config remains usable if a pending config is malformed or
cannot be loaded.  A failed startup receipt marks the candidate configuration
`load_failed` with the specific rejected Skill; Settings offers rollback by
creating a new configuration based on a prior active one.  It does not silently
fall back while claiming the pending configuration was active.

An Agent invocation receives the load-receipt identity and the exact Skill
revision list at its start.  It never rereads mutable Settings state midway
through an invocation.  Existing read-receipt / allowed-root checks remain for
external and bundled Skills, while managed Skills are read through the runtime
configuration resolver.

### Settings information architecture

The Skills area has two separately labelled groups:

1. **Business Skills** — Skills bound to message, mail, calendar, meeting,
   document, personnel, and work-tracking mechanisms.
2. **System Capabilities** — at minimum `反馈迭代 / feedback_iteration`.

Each Skill card shows its active revision, candidate revisions, SHA, current
load receipt, enabled state for the next start, and whether it is repository
exported.  New Skill and new-revision actions create candidate records.  The
user may select one candidate for next-start activation immediately; no
pre-release test gate is required.

The old business-feature switch stays semantically distinct: it controls
whether that business mechanism creates **new tasks**.  It does not mean a
Skill is or is not loadable.  The UI must label it accordingly and must not
present it as a global Skill-loading switch.

### Feedback iteration capability

`feedback_iteration` is a runtime-managed system capability, not an eighth
business producer feature.  Its primary managed Skill is
`ceo-feedback-iteration`, which supersedes the execution checklist role of the
current `ceo-feedback-processing` Skill.  It uses `brainstorming` as a bounded
discussion method.

When enabled, the User Feedback page permits selecting open feedback and
starting a processing batch.  When disabled:

- feedback ingestion, list, detail, historic receipts, and reopen remain
  available;
- the `处理反馈` button is visible, disabled, and explains that feedback
  iteration is off;
- no new claim, Agent session, automatic Skill/config/code change, or automatic
  resolution may originate from feedback;
- unfinished processing work is returned to pending/open during the controlled
  configuration transition, preserving its history and reason.

The backend enforces the capability state as well as the UI; a disabled button
is not the authorization boundary.

## Feedback discussion and decision protocol

### Deterministic context import

Starting a feedback batch sends only persisted data already associated with
each item:

- stable feedback key;
- existing persisted summary;
- valid `task#`, `attempt#`, `run#`, and `codex#` detail paths;
- current runtime config, loaded Skill revisions, and SHA values.

The import step must not call a model to regenerate or synthesize a new
feedback summary.  The Agent may inspect detail paths when needed.

### Bounded brainstorm profile

Generic `brainstorming` remains a general design method and is not globally
rewritten for this service.  `ceo-feedback-iteration` defines a focused profile
that applies its useful parts:

1. establish the observed behavior and current loaded capability facts;
2. identify the likely root-cause class;
3. if a material product or policy choice is uncertain, ask the user one
   focused question at a time and offer alternatives with a recommendation;
4. persist a structured decision before changing any Skill, configuration, or
   code;
5. execute and verify only the approved decision path.

It does not require a separate design document for every individual feedback
item, and it does not force a user discussion when facts already establish the
correct path.

### `feedback_iteration_decisions`

The batch records one or more immutable decisions, each associated with one or
more feedback keys and the responsible Agent session/turn.  Required fields:

```json
{
  "scope": "skill_only",
  "root_cause": "existing_tool_usage_policy_is_missing",
  "feedback_keys": ["manual:8308"],
  "source_references": ["attempt#8308", "run#401"],
  "target_skill_revisions": [
    {"skill_id": "ceo-message-triage", "from_revision": 12, "to_revision": 13}
  ],
  "why_not_code": "The needed data, tool, and route already exist.",
  "acceptance": {
    "scenario": "attempt#8308",
    "expected_behavior": "...",
    "verification": ["focused regression", "startup load receipt"]
  }
}
```

Allowed scopes and classification rules:

| Scope | Use only when |
|---|---|
| `skill_only` | Data, tool, and runtime route already exist; policy, prioritization, procedure, or prompting is deficient. |
| `runtime_config` | A valid capability exists but is disabled, bound to an incorrect revision, absent from the active configuration, or otherwise misconfigured. |
| `code` | A required data source, API, UI, state transition, tool, or execution capability is missing or defective. |
| `mixed` | Both a Skill/config revision and a code change are necessary. |
| `needs_human` | A reusable policy, required external authorization, or indispensable business fact is missing. This never resolves the item. |

The Agent proposes a classification; persisted evidence and path-specific
verification determine whether it may resolve feedback.  A prose assertion is
not sufficient to close an item.

## Feedback states and completion evidence

The existing storage lifecycle remains `pending -> processing -> resolved`,
with the UI naming the unfinished set `open`.  Claim creates a new processing
round.  A completed round is `resolved`; reopening makes a later pending round
without modifying historic evidence.

Completion evidence is a discriminated receipt rather than a universal commit
requirement:

| Decision scope | Required successful evidence before `resolved` |
|---|---|
| `skill_only` | Exact managed Skill revision/SHA, activated config version, post-restart load receipt, feedback-scenario regression or deterministic verification, service health, and zero processing/failed/retryable backlog. |
| `runtime_config` | Previous and target config versions, post-restart active-config/load receipt, feedback-scenario verification, service health, and zero backlog. |
| `code` | Local-main ancestor commit SHA, successful tests, service restart with distinct before/after process evidence, health readback, and zero backlog. |
| `mixed` | Both the applicable code receipt and the applicable Skill/config receipt. |
| `needs_human` | No resolution receipt; record the missing input and return/retain the item as open. |

The API validates receipt shape against `scope`.  It rejects a request that
omits a required load receipt, scenario verification, health check, or backlog
evidence.  It must no longer require a commit SHA for a valid `skill_only` or
`runtime_config` receipt, while still requiring one for code paths.

## APIs

The exact route names follow existing console conventions, but their resources
are intentionally separate from repository file endpoints.

- list/create managed Skills and immutable revisions;
- read a revision by stable Skill and revision ID;
- create a next-start runtime configuration and activate/disable bindings;
- read the pending/active configuration and latest load receipts;
- explicitly import from or export a revision to the repository;
- list feedback-iteration capability state and reject claim/start while it is
  disabled;
- record/read `IterationDecision` objects associated with feedback rounds;
- resolve a feedback batch through the path-specific receipt validator.

All writes are transactional.  Optimistic concurrency applies when creating a
new runtime configuration from an expected predecessor, not by overwriting an
already activated configuration.  IDs, names, config bindings, and revision
references are backend-resolved; callers never provide arbitrary filesystem
paths.

## Migration and compatibility

1. Add managed Skill, revision, runtime config/binding/load-receipt, and
   feedback decision schema additively.
2. Import the seven existing service-owned business Skill sources as initial
   revisions and create an initial active runtime config matching current
   behavior.
3. Register feedback iteration as a system capability, initially mapped to the
   imported feedback processing protocol revision.
4. Read legacy repository Skills only for explicit import/export compatibility;
   stop direct Settings writes and runtime-directory synchronization.
5. Convert the Settings page to managed revisions/configuration.  Clarify that
   existing business feature toggles control only new task creation.
6. Replace universal commit-based feedback resolution validation with the
   discriminated receipt contract while retaining historic code-path receipts.

All migrations are additive and idempotent.  Before production migration, make
and verify an online database backup; after restart, read back schema version,
managed-Skill count, active config, load receipt, feedback state distribution,
and backlog counts.

## Failure handling and rollback

- Invalid Skill frontmatter/content creates no revision and leaves the active
  configuration unchanged.
- Failed config creation or compare-and-swap leaves the predecessor active.
- A restart that cannot load a pending configuration emits `load_failed`; it
  does not claim that configuration is active.
- Selecting an old revision creates a new config version; revision history is
  never deleted as part of rollback.
- Disabling feedback iteration cannot silently resolve or discard feedback.
- A missing decision, mismatched receipt scope, failed test/verification,
  failed load receipt, failed health readback, or nonzero required backlog
  prevents `resolved` and leaves the batch recoverable.

## Test and acceptance plan

### Data and runtime

- revision immutability, SHA calculation, parent lineage, UTF-8/frontmatter
  validation, and name uniqueness;
- candidate save followed by activation creates distinct config versions;
- launch loads only the exact configured revisions and writes a receipt;
- failed candidate load preserves last active config and is observable;
- rollback selects prior revision through a new config, then proves a new
  startup receipt;
- a running Agent invocation remains bound to the receipt/config it began with.

### Feedback protocol and API

- disabled feedback iteration rejects claim/start server-side and the UI button
  is disabled without making a request;
- deterministic context import contains persisted summaries and valid routes
  only, with no model-generated import summary;
- each legal `IterationDecision` scope validates and an invalid classification
  or unsupported receipt is rejected;
- `skill_only` can resolve only with revision, config, startup, scenario,
  health, and backlog evidence, without a commit;
- code and mixed paths still reject missing/non-ancestor commits or failed
  tests;
- `needs_human` cannot resolve; reopen/round history remains intact.

### React and end-to-end

- create a local Skill, save a new revision, select it for next start, restart,
  and read back its SHA in Settings;
- activate a candidate without Git publication, then roll back to an older
  revision;
- distinguish business task-routing toggles from Skill/config activation;
- display feedback iteration in System Capabilities; when off, show a disabled
  `处理反馈` action and explanatory text;
- select open feedback, inspect the deterministic context, persist a decision,
  complete each receipt path, and verify state rendering/route links;
- after every runtime-changing acceptance flow, verify a new
  `com.ceo-agent-service.main` process, health, and zero failed/processing
  backlog before reporting success.

## Implementation slices

1. Data model, migration/import, revision/config/load-receipt service, and
   unit tests.
2. Runtime startup resolver and immutable invocation binding, with restart and
   receipt integration tests.
3. Console APIs and Settings managed-Skills UI; retire direct file edit/sync.
4. Feedback-iteration system capability, disabled-state UI/API behavior, and
   deterministic batch context.
5. Feedback decision persistence, scope-specific evidence validator, protocol
   Skill, React history, and end-to-end tests.

Each slice remains independently testable.  Runtime code changes require the
normal launchd restart, process readback, health check, and zero-backlog
verification before a slice is declared live.
