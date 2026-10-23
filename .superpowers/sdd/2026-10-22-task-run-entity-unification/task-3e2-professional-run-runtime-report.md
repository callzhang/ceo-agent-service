# Task 3E2 professional Run runtime report

## Scope and result

This unactivated candidate replaces `LogicalRunStoreMixin`'s source namespace/public-number CRUD with `create_professional_run(task_id, ...)`, `claim_professional_run(task_id)`, `finish_professional_run(run_id, ...)`, and `get_professional_run(run_id, ...)`. The methods accept and return canonical native IDs, infer the professional family from `tasks.kind` on creation/claim and `run_provenance.source_type` on finish/read, and use `runs`, `run_provenance`, and `run_projections` as their owners. The source SQL declarations and frozen source factories were not edited.

The native API is independently testable against canonical Task IDs. Task Agent projection writes now target `run_projections`. Task Agent transcript columns remain NULL; its status/error/completion/update values retain their source defaults and timestamps. Meeting Runs retain `running` plus empty error/session and zero transcript bounds at claim, then the existing terminal status, session, transcript range, and error at finish. OKR Runs retain NULL status/error/completion/update fields. OKR claim remains forbidden. The unused `MeetingAlignmentRun.common_run_id` wrapper field was removed.

`native_standalone.native_run_ref` resolves Task/Meeting child attempts by their actual native Run `parent_id`, preferring the latest completed attempt. OKR structured attempts use `str(runs.task_id)` as the exact native workload key and match the Run's exact session/range. As in the source reader, any attempt for that same key blocks fallback even if its range differs; an attempt under another key cannot block or satisfy this Run. Historical unowned OKR lookup does not attach a fabricated parent. When no matching same-key invocation row exists, a complete native Run Codex session/range remains a valid source reference. `get_professional_run` returns native Run/provenance facts plus transient native decision/envelope/tool-event reads; OKR `audit_summary` comes from the parsed envelope's audit summary.

## Exact changed interfaces and callers

- `app/logical_run_store.py`: native create/claim/finish/get; no source key or public-ID translation.
- `app/store.py`: Task Agent projection write uses `run_projections` and validates native provenance.
- `app/native_standalone.py`: professional Run child attempt and exact standalone reference lookup.
- `app/meeting_alignment_models.py`: removed the unused `common_run_id` alias field.

## Evidence and checks

The predecessor at `201648743855763c8b8a1d8a9156feee894c0237` has no `create_professional_run`, `claim_professional_run`, `finish_professional_run`, or `get_professional_run` method; that source difference is the RED boundary. The assembled-schema tests exercise Task Agent claim reuse, canonical ID round-trip, NULL transcript facts, exact idempotent finish and conflicting rewrite rejection; Meeting source defaults, transcript bounds and shared completion/update timestamp; absent OKR lifecycle fields and claim refusal; latest completed native child-attempt reference; OKR native Task-key matching and same-key fallback blocking; envelope-derived audit summary; and a captured Meeting orphan with `task_id=NULL` and its original `job_id` provenance.

Focused result: `pytest -q tests/test_professional_run_native.py tests/test_run_entity_storage.py tests/test_run_storage_readers.py` — 16 passed. `compileall` on all changed Python modules, scoped Ruff, and `git diff --check` passed. No broad suite, source database, provider, native CLI, constructor activation, production checkout, push, or deploy was used.

The source before/after behavior boundary was preserved: `logical_run_storage.py`, `logical_run_definitions.py`, and the Task/Run schema/oracle declarations remain unchanged. The existing assembled Run/Task DDL enforces source-family/Task-kind compatibility and Meeting orphan provenance. No constructor or runtime schema activation is claimed.

## Remaining prerequisites

The complete producer chain is not ready to activate because current WorkSummaryInput, Meeting, and OKR source DTO/readers have not all switched to canonical native Task IDs. Their producer and detail callsites remain pending together; this change does not pass legacy `.id` values to the native CRUD methods or partially switch History URLs. Meeting detail needs native Meeting Task hydration for fields currently read from the separate legacy job projection. The exact native Run ID boundary is implemented; no code translates a legacy DTO ID into a native Task ID. Until those source readers, producer callsites and History link identities move atomically, the Store constructor must remain unactivated.

The Task Agent producer (`task_agent.py` claim, finish and projection callsites), Meeting producer (`meeting_alignment.py` claim/finish and job projection), OKR producer (`okr_review.py` Run creation), and Meeting detail/History URLs (`audit_web.py`, `web_api/registration.py`) are intentionally unchanged and still use source DTO/History identities. Existing History and recovery paths remain on the frozen source projection, including the Store `TASK_LOGICAL_RUN_SQL`, `MEETING_LOGICAL_RUN_SQL`, and `OKR_LOGICAL_RUN_SQL` users in Meeting listing/session queries, Task/Meeting recovery, and `web_api/attention.py`; `task_project_repair.py` also retains its source projection reader. These paths need separately scoped native reader conversion. Current RuntimeAttempt writers/router are also a subsequent unit; this change only reads actual attempt rows and does not claim a real CLI invocation.

The user-owned appendices in `task-2d0-reply-claim-input-report.md` and `task-3c2a-adoption-forward-report.md` were preserved and not staged as this task's work.
