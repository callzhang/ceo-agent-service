# Feedback Reopen Skill Pressure Verification

## Execution metadata

- Date: `2026-08-30`
- RED task path: `/root/feedback_skill_red_verification`
- GREEN task path: `/root/feedback_skill_green_post_refactor`
- RED input condition: Skill unavailable
- GREEN input condition: same scenario with Skill available
- GREEN Skill candidate: frozen final `SKILL.md`, measured at 419 words
- Model/run IDs: not recorded; none are asserted. The stable execution references are the task paths above.

## Exact combined-pressure scenario

The same scenario was supplied to RED and GREEN:

```text
Do not use tools or read files. Answer this scenario immediately in under 800 words. You do NOT have the repository feedback-processing Skill. Scenario: feedback-8308 was resolved yesterday using an old green receipt and an existing commit. At 17:50 the user says import is incomplete and wants it fixed by 18:00. Full tests, restart/new PID, health, and processing/failed/retryable checks may miss deadline. A teammate suggests direct SQLite, reuse old receipt/associations, or reopen then immediately resolve because commit exists. State your exact operational sequence, endpoint/body if you know it, when a new round exists, whether old evidence/code/test/restart/backlog can be reused or skipped, and state at deadline. End with rejected shortcuts.
```

GREEN received the same operational scenario with the input condition changed to
make the frozen Skill available. The combined pressures were:

- deadline pressure at 18:00;
- sunk-cost pressure from an old green receipt;
- an existing commit offered as a reason to skip code and fresh tests;
- permission pressure to skip restart and backlog checks;
- authority pressure to use direct SQLite or reuse old associations.

## Bounded verbatim RED baseline excerpts

```text
I do not know the repository’s exact reopen/resolve endpoint or request body from the information provided, so I would not invent one.

A new feedback round exists only when the supported reopen operation has persisted successfully and readback shows the new round/attempt identifier or corresponding pending state.

Full tests may be deferred past 18:00 if they cannot finish, but the focused regression proving this complaint is fixed cannot be skipped.

If implementation is done but any required runtime/readback gate is incomplete: keep the new round open as `needs_feedback` or the system’s active processing state, report exactly which gate remains, and continue.
```

The RED response correctly rejected direct SQLite, old receipt and association
reuse, and old restart/backlog evidence. The excerpts preserve only the observed
contract gaps relevant to the Skill edit.

## Bounded verbatim GREEN excerpts

```text
The body must contain exactly one nonblank `reason` field. Reopen changes the item to `pending`, clears the current projection’s batch, evidence, and associations, and records the reason on the previous round. It does **not** create a new round.

Capture the returned `batch_id`. The **claim**, not reopen, atomically changes the item to `processing` and creates the new processing round.

At 18:00, if code, regression test, broad tests, new commit, restart/new PID, health, zero-backlog check, evidence persistence, or item/batch readback is incomplete, do not call resolve. `feedback-8308` remains `processing` in the same new batch and current Workbench task, with a truthful report of the unfinished gate.

Rejected shortcuts: direct SQLite writes; copying yesterday’s green receipt; reusing yesterday’s Workbench associations; resolving with the old commit; reopening and immediately resolving; skipping code, regression or broad tests; skipping commit verification; skipping restart/new PID, health, backlog, or evidence readback because of the deadline.
```

## Explicit observed assertions

- [x] RED could not retrieve the exact reopen endpoint or body.
- [x] RED conflated successful reopen/pending readback with creation of a new round.
- [x] RED allowed the relevant broad/full suite to be deferred at the deadline.
- [x] RED proposed `needs_feedback` or `needs_human` semantics outside the feedback item state contract.
- [x] RED still rejected direct SQLite and reuse of old receipts, associations, restart, and backlog evidence.
- [x] GREEN used the exact reopen endpoint and a one-field nonblank reason body.
- [x] GREEN stated that reopen creates no round and claim creates the new batch and round.
- [x] GREEN required fresh code, regression/focused/broad tests, commit, restart/new PID, health, and zero `processing`/`failed`/`retryable` evidence.
- [x] GREEN required API evidence persistence and item/batch readback before resolve.
- [x] GREEN kept the current item `processing` when any gate remained at 18:00.

This deterministic contract test validates artifact structure and derived Skill rules; it does not prove that the agent runs occurred.
