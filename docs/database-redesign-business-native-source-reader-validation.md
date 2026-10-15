# Exact native business source reader acceptance, 2026-10-15

Accepted implementation: `6d675762e9f37d7dc4007ac7938b527806588d05`, parent
`37a8cf51435ac0a6b4d2705566d20af02362f8e8`, in the isolated
`codex/database-redesign-business-source-20261015` worktree. This implements only
the prerequisite in `superpowers/plans/2026-10-15-business-native-source-reader-plan.md`.
It does not select a production caller, change storage, retire bodies, or complete
the approved ten-core-table-plus-ledger design.

## Verification

The implementer first observed 72 new tests failing because the helper did not
exist, then 72 passing after adding it. The focused four-file suite passed
137 cases in 4.99 seconds:

- `tests/test_native_source_documents.py`
- `tests/test_task_source_documents.py`
- `tests/test_storage_native_trajectory.py`
- `tests/test_business_core_source.py`

Ruff, compilation and diff checks passed. Test roots were temporary and removed.
Fresh SPEC review confirmed that the function and tests are AST-identical to the
approved plan, prior module code is unchanged, both documentation insertions are
exact, and there is no production caller or additional schema/policy change.
Subsequent fresh QUALITY review found no actionable issue and independently ran
the 72 new cases: 72 passed in 0.28 seconds. Its isolated temporary home/test root
was removed. No full local suite ran.

## Readback against existing native originals

At `2026-10-15T11:43:28.959575+00:00`, root used the committed helper against all
30 previously proved `native_provenance` mappings in
`superpowers/specs/2026-10-15-database-redesign-business-native-source-recovery.json`.
The input metadata artifact SHA-256 was
`ed8ef6f509b4c2d08df7a4c1ce10801ed56e345a258941d30139b3492bbd70ea`.
The helper resolved original CLI-owned files through existing finders, verified
the selected raw line, followed the exact JSON leaf and character range, decoded
the supported JSON string literal, and verified the complete body length/hash.
Root additionally compared each returned body's length and SHA-256 with the
artifact's independently recorded source-document length and hash.

| Runtime | Exact bodies | UTF-8 bytes |
| --- | ---: | ---: |
| Codex CLI | 7 | 173,779 |
| Claude CLI | 23 | 498,518 |
| Total | 30 | 672,297 |

All 30 passed. Returned bodies existed only in process memory and were not
printed or written. No source database was opened, model or provider invoked,
native file modified, or new database/test backup created. The command exited
zero and closed its file handles. Compact metadata is in
`superpowers/specs/2026-10-15-business-native-source-reader-readback.json`.

This verifies the implementation on the already known recoverable subset; it
does not increase historical coverage. The other 158 of 188 bodies remain
unproved. No source body was retired. Provenance persistence, existing caller
conversion, exact target fields/constraints and forward/inverse migration
verification remain subsequent work. Nothing here was pushed, merged or deployed.


## Bounded original orchestrator-source probe, 2026-10-15

Root independently checked the two unproved console/service-data-repair source
documents (IDs8 and30) against the existing original orchestrator session
01a122e3-0bec-73b3-b7aa-c0be8e731a61. Exact full-body plain, UTF-8 JSON string
literal and ASCII JSON string literal searches found no matches in the recorded
unchanged native prefixes. The per-file bound was256MiB; this was one identified
original session, not a broad scan. Source body hash/bytes matched the earlier
frozen artifact in process memory; no body, native copy or database was saved.
The metadata-only receipt is
`superpowers/specs/2026-10-15-business-console-source-probe.json`.

This adds no recovered provenance and proves no general source unavailability.
The previous30 verified/158 unproved result and body-retirement blocker remain
unchanged; no source reader, caller, storage or provider behavior was changed.


## Remaining candidates in the same original session, 2026-10-15

Root then tested all156 other unproved source documents against the same
219379588-byte prefix of the original orchestrator trajectory. Its SHA-256
remained exactly the prior probe value. Exact complete plain/UTF-8 JSON/ASCII
JSON string searches found no matches. Candidate bodies were checked against
the prior frozen hash/byte metadata in process memory and were never output
or saved. The selection and proof bounds are recorded in
`superpowers/specs/2026-10-15-business-original-session-source-probe.json`.

Together with the separate two-document probe, this checks all158 unproved
documents against this one identified original session only. It neither proves
general source unavailability nor creates an identity from body equality.
The30 verified/158 unproved source-recovery boundary is unchanged.
