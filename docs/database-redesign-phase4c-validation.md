# Phase4C scheduled / Workbench acceptance

Status: core candidate is in the isolated worktree, with independent SPEC/QUALITY and coordinated outer runtime changes still pending. This is not a production C release.

## Actual source and exact field map

Source Git: `44a1a4e39fc24765c92d0e51c7f03d578b9d75de`; Store `2026-10-11.3`; Email `51`. Source metadata was captured read-only after actual B deployment. Counts are from this one snapshot, not a cross-snapshot deletion oracle.

| Source table | Captured rows | Target |
|---|---:|---|
| `scheduled_task_runs` | 156245 | `runs`, `record_type=scheduled_task_runs` |
| `workbench_tasks` | 20 | `tasks`, `record_type=workbench_tasks` |
| `workbench_turns` | 37 | `tasks`, `record_type=workbench_turns` |
| `workbench_events` | 810 | `runs`, `record_type=workbench_events` |

The following map covers all49 actual source fields (17+7+19+6). Shared physical integer `id` is allocated independently; public source IDs retain exact legacy values. Workbench TEXT keys are not cast to integers.

| Source | Exact target or retirement |
|---|---|
| `scheduled_task_runs.id` | `runs.legacy_key`; `runs.id` is the physical allocator identity |
| `scheduled_task_runs.event_id` | `runs.event_id` |
| `scheduled_task_runs.scheduled_task_id` | `runs.scheduled_task_id` |
| `scheduled_task_runs.trigger_kind` | `runs.trigger_kind` |
| `scheduled_task_runs.scheduled_for` | `runs.scheduled_for` |
| `scheduled_task_runs.dispatch_status` | `runs.dispatch_status` |
| `scheduled_task_runs.skip_or_error_reason` | `runs.skip_or_error_reason` |
| `scheduled_task_runs.execution_kind` | `runs.execution_kind` |
| `scheduled_task_runs.execution_id` | `runs.execution_id` |
| `scheduled_task_runs.lease_owner` | `runs.lease_owner` |
| `scheduled_task_runs.lease_expires_at` | `runs.lease_expires_at` |
| `scheduled_task_runs.created_at` | `runs.created_at` |
| `scheduled_task_runs.dispatched_at` | `runs.dispatched_at` |
| `scheduled_task_runs.first_scheduled_for` | `runs.first_scheduled_for` |
| `scheduled_task_runs.occurrence_count` | `runs.occurrence_count` |
| `scheduled_task_runs.result_summary` | `runs.result_summary` |
| `scheduled_task_runs.snapshot_id` | `runs.snapshot_id` |
| `workbench_tasks.id` | `tasks.legacy_key`; `tasks.id` is the physical allocator identity |
| `workbench_tasks.title` | `tasks.title` |
| `workbench_tasks.runtime_kind` | `tasks.runtime_kind` |
| `workbench_tasks.provider_session_ref` | `tasks.provider_session_ref` |
| `workbench_tasks.archived_at` | `tasks.archived_at` |
| `workbench_tasks.created_at` | `tasks.created_at` |
| `workbench_tasks.updated_at` | `tasks.updated_at` |
| `workbench_turns.id` | `tasks.legacy_key`; `tasks.id` is the physical allocator identity |
| `workbench_turns.task_id` | `tasks.parent_id` → physical Workbench task; outward projection retains original TEXT task ID |
| `workbench_turns.client_request_id` | `tasks.client_request_id` |
| `workbench_turns.task_sequence` | `tasks.task_sequence` |
| `workbench_turns.user_text` | `tasks.user_text` |
| `workbench_turns.status` | `tasks.status` |
| `workbench_turns.stop_requested` | `tasks.stop_requested` |
| `workbench_turns.final_text` | Remove physical empty cache; native/RAM presentation contract must pass outer validation |
| `workbench_turns.error_code` | `tasks.error_code` |
| `workbench_turns.error_detail` | `tasks.error_detail` |
| `workbench_turns.resume_context` | `tasks.resume_context` |
| `workbench_turns.lease_owner` | `tasks.lease_owner` |
| `workbench_turns.lease_expires_at` | `tasks.lease_expires_at` |
| `workbench_turns.execution_run_id` | `tasks.execution_run_id` |
| `workbench_turns.runtime_quiesced_run_id` | `tasks.runtime_quiesced_run_id` |
| `workbench_turns.started_at` | `tasks.started_at` |
| `workbench_turns.completed_at` | `tasks.completed_at` |
| `workbench_turns.created_at` | `tasks.created_at` |
| `workbench_turns.updated_at` | `tasks.updated_at` |
| `workbench_events.id` | `runs.legacy_key`; `runs.id` is the physical allocator identity |
| `workbench_events.turn_id` | `runs.task_id` → physical Workbench turn; outward projection retains original TEXT turn ID |
| `workbench_events.sequence` | `runs.sequence` |
| `workbench_events.event_type` | `runs.event_type` |
| `workbench_events.payload_json` | `runs.payload_json` |
| `workbench_events.created_at` | `runs.created_at` |

## Removal and preservation boundary

Four old physical source tables are replaced by typed rows; there are no compatibility views or dual writes. Only the empty `workbench_turns.final_text` and search caches `summary_text`, `fts_text`, `embedding_json`, `embedding_model`, `embedding_updated_at` are retired. Nonempty retired-cache values must refuse migration before mutation.

Search metadata retains the existing seven columns `id`, `session_id`, `source_type`, `source_id`, `title`, `created_at`, `updated_at`. Its external-content FTS remains readable. Native ranking is outside this structural change; metadata-to-runs migration and complete FTS retirement remain Phase8.

Workbench attachments, artifacts and confirmations remain physical child tables. Their existing NO ACTION semantics and original results/bytes must survive physical parent remapping and inverse. Scheduled configuration tables remain in place; `snapshot_id` and `scheduled_task_id` retain actual existing FKs. `execution_id` remains the actual public text reference, including service commands.

Source allows nullable Workbench TEXT PKs, raw BLOB values, and nonpositive event sequence/occurrence values where no CHECK exists. The real `task_sequence>0`, status, stop flag, natural/partial uniqueness and required-value constraints must survive; no speculative checks or fields are added.

## Verification gates

Core RED confirmed old schema publication and numeric normalization failures. Core focused11-file GREEN reported195 passed in5.00s; independent review and any final inverse fixes remain pending. No full local serial suite is run against the live service.

Final C acceptance requires coordinated outer readers/writers, exact source SQLite backup with full retained values/children/receipt comparison, baseline inverse with actual DDL/rowid/FTS state, replacement facts plus deleted allocation watermarks, actual old-code constructor/claim/recovery, re-forward, publication failure stages, remote Quality, normal deploy and live readback. All323 app Git files from deployed B44a1a4e were extracted into the C task temporary directory with per-file SHA256 for that old-code exercise; no environment or native trajectory copies were made.

Independent SPEC reviewed49 fields and remaining core constraints/inverse/FTS behavior, rerunning78 initial and189 expanded focused checks. It found two P2 source-boundary gaps: markerless C source tables were omitted from bootstrap refusal inventory, and additional source13 tasks/runs objects could be silently retired. Both require refusal-before-mutation regressions and re-review; current-schema repair policy is not broadened. Core SPEC remains open.

Both P2 repairs completed with actual8 failed/4 passed RED and12-case GREEN; writer full11-file207 passed. Independent re-SPEC reproduced both original refusals with unchanged full fingerprints, checked supported current missing-index repair and allocation retention, and independently ran201 focused checks in6.41s. Scoped core SPEC passed; independent QUALITY is pending. No C production release or complete outer acceptance is claimed.

Independent QUALITY passed the frozen core with no P1/P2. It directly read seven runtime files and the source/core/release/manifest fixtures, matched all nine source declarations to independent metadata, ran141 focused checks in5.14s, and passed scoped Ruff/diff. A separate in-memory stale multilingual/repeated-token FTS exercise verified old title/summary/body terms are removed after title update/inverse, new title is searchable and FTS integrity-check passes. No providers, production constructors or new backups were used. This completes core reviews only; outer acceptance and actual copy/publication remain pending.

Concurrent production source advanced to6b43a93b7d5e749f338c13452be5c9e5ceee615b (Skill/Task guidance/schema deduplication PR54/55/60), Store13/Email51 unchanged. Read-only exact C source preflight passed on actual production; one snapshot counts scheduled156422/WBtasks20/turns37/events810. No source constructor or production mutation. DB core/storage/release files are absent from the25-file main delta. Root fetched without merging over active outer edits; final integration and affected tests remain required. Actual old-code evidence is now323 verified6b Git app files; obsolete44 temporary code was removed after verification/no handles (10,298,859 B). Final copy/old-code exercise must use actual current production, not the earlier44 metadata snapshot by assumption.

Scheduled/outer unit is frozen pending review closure: writer242 primary/78 maintenance+quiet/56 History+publication/71 strengthened targeted passes (overlapping). Independent SPEC184 primary/21 quiet passed and confirmed17source fields,2000VM budget/index/cast,public417 vsphysical identity. It found oneP2: false source13 marker plus retired empty scheduled table could hide a busy typed run; must refuse mixed actual layouts. It also found three old fixture cases in History/runtime rename; in-memory typed substitutions preserved their original business oracles, but real repository fixtures still require updates. Four supplemental maintenance permission-only ps cases independently passed in the permitted test environment. Re-SPEC and QUALITY remain pending; no C publication.

Scheduled P2 repair:8-case RED, actualsource12/13 tuple/Row fullstate+bytes/read-only lease regressions and mixed empty/nonempty schema rejection pass; writer254 primary and51 completequiet/History/rename. Independent re-SPEC replayed original hidden-lease case and verified rejection, matchedsourcecolumns,23 focused passes5.13s. All three original business test functions AST-identical; only sourcefixture SQL translated. Scheduled SPEC now passes; fresh QUALITY pending.

Scheduled independent QUALITY passed:227 scoped checks14.02s plus14 repositoryquiet/busy checks1.95s, Ruff/diff clean. Only expected runtime AST changes and all comments retained; History/rename business functions remain identical. Additional in-memory config deduplication preserves public417/418/419 and all other facts, corrupt JSON, empty FK findings and repeated idempotence. No P1/P2, providers, production constructors or new backups. This closes scheduled SPEC/QUALITY only; next WB callers and completeC acceptance remain pending.
