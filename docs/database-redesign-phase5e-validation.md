# Phase5E source refresh and execution boundary

This is source preparation for the approved Attempt/Memory action consolidation,
not target storage, a migration, a new Store version or a deployment. The full
ten business/runtime tables plus one migration table goal remains incomplete.
The managed Phase5C checkout was clean and has been reused on the independent
`codex/database-redesign-phase5e-20261013` branch at D candidate5221062d. D's
pending release branch is unchanged by this source-only work.

## Current declarations and dependencies

`2026-10-13-database-redesign-phase5e-source-refresh.json` records a readonly
snapshot of actual published7fabea80 Store17, and an owned empty Store18
candidate constructor. The original frozen Store16/candidate17 inventory is
unchanged and is not relabelled. Both new declarations, column/default/PK
metadata, exact indexes, triggers and inbound/outgoing FKs equal their
corresponding frozen source variants.

Actual Attempt has58 fields and14821 rows; actual Memory has10 fields and476
rows. The empty candidate has54/10 fields. Actual Memory still has no parent
FK; fresh Memory retains its immediate Attempt FK. The four populated
historical Attempt fields remain source facts, not new DTO fields or inferred
relations. Counts are observations, not migration constants or retention rules.
Only schema metadata and aggregate counts/byte sizes are exported; no raw
business row, provider body, trajectory or credential is in the artifact.

All14821 current Attempt event arrays equal`[]`, occupying29642 bytes, while
Memory payload copies occupy601922 bytes. This snapshot does not make queued
review markers impossible: `record_reviewed_reply_rerun` still appends the exact
existing `audit_review`/`queued` marker. The approved migration must retain its
order and multiplicity while dropping copied native tool fragments.

The normal metadata writer still returns`[]` and transient original events feed
Memory receipt/error extraction before retirement. Static app call/import
inspection still finds no caller of`run_codex_memory_write`; its implementation
takes explicit data/type/time/source arguments and does not read a stored
Memory payload as retry input. WeChat imports typed Memory helpers, and meeting
code imports the exception; neither is a caller of that wrapper. The wrapper's
eligible completion semantics and the three existing native-range behaviors
remain requirements of the approved E plan; no replay is added here.

## Verified scope

Independent frozen source oracle:54 passed, two opposite-variant skips.
Existing Memory wrapper tests plus the main detail-page and bounded-zero-range
regressions:12 passed. The source declaration equality and current aggregate
checks passed against the actual readonly snapshot and empty constructor.
All source-only temporary databases, synthetic native fixtures and scripts were
removed after retaining the metadata artifact.

Independent readonly review of6e39cf31 reproduced every declaration/hash and
aggregate equality check, confirmed the static wrapper/import and marker
claims, and ran the combined source/dependency suite:66 passed, two opposite
variant skips. It found no blocking gap, removed its owned test directory and
made no production/provider/model call. This is source-preparation acceptance.

D must actually publish before E implementation/migration activation. Re-read
the exact immediately preceding published schema and its dependencies then;
this Store17 observation or empty Store18 candidate does not prove a published
Store18 source. No E version is reserved, no source row was altered, and no
model, Memory provider, send, recall or deployment was invoked by this work.
