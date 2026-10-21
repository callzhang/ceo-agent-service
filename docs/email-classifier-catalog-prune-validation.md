# Email Classifier native Skill catalog scope

## Observed problem and acceptance gate

The latest four complete production Codex Email Classifier trajectories
(`389177`, `389011`, `388962`, `388724`) each received a roughly 39 KB native
Developer message and made no tool calls. In `389177`, 21,396 characters of
that message are the global native Skill list. The service input already
contains the exact frozen `ceo-email-classifier` body, allowed category
descriptions, current email facts, examples and the typed result contract.
The native catalog is offered independently by the CLI; it does not establish
that the classifier needs any of its unrelated entries.

The candidate may change only the native Skill selection on the Codex Email
Classifier command. It must retain the full service-submitted Skill and prompt,
the current route, model, tools, approval, session, category and candidate-index
contracts. Other workloads and non-Codex classifier routes must be unchanged.
The selected native catalog should contain no unrelated user Skill entries and
should no longer trigger the CLI description-budget warning. This is an input
optimization; no speed, token or business-quality improvement is assumed.

Before release, require a regression that fails before the command change and
passes after it, focused classifier tests, and a native prompt-input comparison
that checks the identical service input and the actual catalog difference.
The fixed anonymous classification comparison must keep the same cases, model
and facts across baseline and candidate and preserve category, importance,
certainty and unsubscribe candidate index for every case. Any decision change
is a no-go. After independent review and merge, use the standard quiet-wait
deployment and read back version, PID, health, queues, Attention, History and
a post-deploy native classifier input. A process exit or valid JSON alone does
not establish classification quality.

The 2026-10-18 two-case catalog comparison in the trajectory review is
supporting evidence for the CLI control only. Its earlier classifier category-
table ablation is a separate no-go because `external_billing.important`
changed; this change does not edit that table or its managed Skill body.

## Local and anonymous native validation

The focused factory assertion failed before the one-line selection change
(`skill_names=None`, expected `()`) and passes after it. The prior single-copy
test intentionally asserted that command building never scanned a Skill root;
full catalog exclusion requires the existing native selector to enumerate
those roots. That test now uses an isolated synthetic root and verifies both
the unrelated path exclusion and the inlined classifier-name exclusion across
three Codex routes and cold/resume command shapes. The two affected files pass
35 tests; Ruff, compile and diff checks pass. Three local selector builds took
162, 55 and 57 ms and produced a 39,700-character native command override.
These are local construction costs, not measured end-to-end latency.

Using the current CLI's `debug prompt-input` with one fictional email and
identical service prompt, baseline and candidate User messages had the same
SHA-256. The native Skill section fell from 169 entries / 19,644 characters
to five entries / 2,176 characters; the enclosing first Developer message
fell from 28,893 to 11,425 characters. The other two Developer messages and
global AGENTS User message had equal hashes. This confirms the input-scope
effect for the current native catalog, not a token or speed measurement.

The versioned 11-category anonymous fixture was then run as 22 paired Codex
OAuth turns with the current configured `gpt-6-luna` model at high effort,
read-only sandbox, disabled business tools/MCP, and the same complete managed
Skill and prompt in each pair. All 22 responses parsed into the current wire
model and bound successfully. Each case kept the expected category and the
baseline/candidate decisions matched exactly on category, importance,
certainty and unsubscribe candidate index; the junk candidate bound to its
fictional URL in both arms. There were zero tool calls. The reproducible script
and concise result are in `evals/trajectory_optimization/` as
`email_classifier_catalog_prune_native.py` and
`email_classifier_catalog_prune.native.v1.json`. The isolated model run ignores
personal Codex configuration and disables plugins; the separate prompt-input
check above covers the actual user Skill catalog. No mailbox, provider action
or real business MCP was used.

A separate one-case control recorded zero Skill-description-budget warnings in
both arms (`email_classifier_catalog_prune.warning.v1.json`). The current
isolated baseline did not emit that warning, so this run does not attribute a
warning reduction to the candidate. The catalog reduction above remains
directly visible in the native prompt input.

Independent review raised a possible on-demand dependency of the inlined
classifier Skill. The active production binding is revision 388, SHA-256
`5ba8cb23d0f888f9d5cfad949d584331217091e6c82e52d0416848d42f554ea6`,
byte-identical to the bundled Skill used in this comparison. That complete
Skill directs classification only, forbids tool use and has no other Skill
reference. The scheduled classifier path already requires exactly the
`ceo-email-classifier` selected Skill and matching frozen material. Therefore
the proposed native catalog exclusion does not remove a dependency of the
current contract. A later Skill revision that adds a native dependency would
be a new contract and needs its own catalog decision and fixed-case check;
no speculative dependency parser is added here.

The scoped review's one dependency concern is addressed by the current
production revision check above; the independent rereview reported no
actionable defect.

## Release and production readback

PR #196 merged at `231f60c796f9bb2df530c8d1766d1312ee033dae` on
2026-10-21. The standard pinned deployment ran
`python -m app.deploy --target-commit 231f60c796f9bb2df530c8d1766d1312ee033dae`
without `--skip-quiet-wait` and returned `deployed 18d7d5f3 -> 231f60c7`. It waited
for in-flight work to drain before stopping the service. The production
checkout was clean at that exact commit. New supervisor PID `94807`, service
worker `94817`, console `94818` and email worker `94819` started at
2026-10-21 10:31:22 UTC. `/healthz` returned HTTP 200 with `ok: true`;
Dashboard queues returned HTTP 200 with active `0` and queued `24` in that
snapshot; Attention returned HTTP 200 with seven items; History returned HTTP
200 with total `36,726` and readable recent statuses. A later in-flight count
was six as normal scheduling resumed, so the queue snapshot is not a claim of
permanent idleness.

A post-deploy anonymous command construction used the deployed commit's
`EmailClassifierRoutedBackend`, `CodexCommandFactory` and native CLI
`debug prompt-input`, with an empty temporary MCP manifest and the same
fictional work-email prompt. The factory selected `skill_names=()`; its native
input contained five Skill paths (1,105 Skill-section characters), the full
managed classifier Skill and current wire contract. The last native User
message matched the service prompt SHA-256 exactly. This was a prompt-input
readback with no model call, mailbox access or provider action.

The latest natural production classifier attempt was still `389177` from
10:02:19 UTC, before this deployment. Thus the release is verified at the
loaded-code and native-input level; a real post-release classification
judgment has not yet occurred and no such business outcome is claimed.
