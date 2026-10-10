# Approved storage simplification

1. Move training snapshot bodies/observations out of SQLite. Keep only latest complete data file; keep small metadata/evaluation records in DB. Pin the selected external file with small run-bound metadata for active training. Migrate newest old snapshot before removing old body tables.
2. Use Codex/Claude native session and Friday operation history as the sole trajectory source. Do not persist full or compact events, agent result bodies, runtime result envelopes, audit tool bodies or derived search bodies in SQLite or archive files. Persist service-origin task state and minimal native run references. Remove historical copies even where native records have disappeared; those details become explicitly unavailable.
3. Store each exact scheduled configuration once, referenced by trigger rows. Preserve queued input and historical configuration.
4. Keep current and previous runnable model, plus latest candidate under evaluation. Remove other artifacts and unreferenced temp files only when no training is in flight.

Validation: test-first focused regressions, native readers and runtime receipt tests, real DB-copy migration and integrity/foreign-key checks, compare business object counts and pending references; deploy through app.deploy; verify PID, health, queues, Attention/History and disk sizes. No unrelated safety or audit policy changes.

## Earlier conservative migration copy (superseded, 2026-10-09)

A SQLite backup of production was migrated from email schema 45 to 46 and vacuumed. Size changed from 3,482,066,944 to 1,848,934,400 bytes (46.9% reclaimed). quick_check returned ok. Counts were unchanged: reply_tasks 7,175; reply_attempts 14,578; agent_runs 21,219; external_action_results 250; sent_replies 2,268; business_tasks 466; scheduled_task_runs 145,890; email_training_snapshots 122. Scheduled runs reference 66 immutable configurations with zero missing references. Latest external training data is 16,858,401 bytes.

That earlier copy retained unavailable or unmatched native records as sole historical evidence and removed zero historical runs. Derek subsequently approved removal of those copies as well; this retention behavior is superseded by the native-only policy above. Both baseline and migrated copy have the same pre-existing foreign-key finding in meeting_alignment_runs row 2298; migration introduced no new finding. Focused cron, training, native trajectory, receipt, audit storage, attention and Workbench regressions and reviews applied to that earlier implementation, not to the subsequent native-only change.

## Native-only migration copy (2026-10-09)

A fresh verified production backup was migrated and all database trajectory copies removed, including historical copies whose native files are absent. Preliminary size is 670,842,880 bytes (640 MiB), down from 3,482,775,552 bytes (80.7%). Removed payload bodies total 1,109,625,474 bytes across 11,756 runs plus result and audit/search copies. This is a disposable validation copy, not the live database.

Counts remain unchanged: reply_tasks 7,175; reply_attempts 14,578; agent_runs 21,224; external_action_results 250; sent_replies 2,268; business_tasks 466; scheduled_task_runs 145,911; email_training_snapshots 122. quick_check is ok; the same pre-existing meeting_alignment_runs row 2298 foreign-key finding remains. Shared configurations: 66, missing references: zero. Store reopen succeeded after cleanup. An explicit WAL checkpoint returned `(0, 0, 0)`; after closing the connection, WAL and SHM occupied zero bytes, so the 670,842,880-byte size includes the complete SQLite footprint of this copy.

Of 12,873 Codex runs with session references, 125 find a native session file and 12,748 do not in the configured native home. File existence is not complete-content verification. Missing native history becomes unavailable by the approved policy. No business state or external action record was removed. Workbench and standalone run-history/search migration are validated separately before release.

## Complete trajectory scrub validation copy (2026-10-10)

The complete scrub additionally removed standalone Task/Meeting/OKR run bodies and Workbench Agent event/final/provider-error copies. A new disposable copy of the earlier migrated database is 585,777,152 bytes (about 559 MiB), with zero WAL bytes. This is 83.2% smaller than the original 3,482,775,552-byte baseline. The additional logical payload reduction is 79,210,382 bytes across 24,453 changed fields; logical payload bytes are not the physical page savings.

All 17 compared record counts are unchanged, including 10,703 Task runs, 2,503 Meeting runs, 413 adopted OKR items, 810 Workbench events, 146 frozen review candidates and 140 adopted reviews. Meeting job decisions/final messages, Task projection receipts and Workbench event identities/order/timestamps compare identically. quick_check is ok and foreign-key findings are unchanged. This validates a copy only; new production backup, deployment and live cleanup/readback are still required. Historical Workbench tools retain only native ordinals paired by call ID in completion order; unmatched starts and old text/reasoning/file bodies retain no content. Standalone raw decisions/summaries and Workbench final/provider error bodies are cleared while service input/status/error code and adopted outcomes remain.

## Fresh production-copy verification (2026-10-10)

A newly captured production backup passed quick_check and measured 3,483,848,704 bytes. The complete migration copy is 593,289,216 bytes (about 566 MiB), with zero WAL bytes, a 83.0% physical reduction. Scrubbing took 65.38 seconds and process maximum RSS was 99,418,112 bytes (about 95 MiB). Logical removed payload: 1,191,883,297 bytes; 49,487 cleared fields; 11,926 Agent runs with event copies.

All 17 table counts are identical before/after: reply_tasks 7,218; reply_attempts 14,626; agent_runs 21,395; runtime attempts 22,486; Meeting jobs 669/runs 2,518; Task runs 10,709; adopted OKR items 413/runs 22; external_action_results 266; sent_replies 2,281; business_tasks 484; scheduled_task_runs 146,823; Workbench events 810/turns 37; frozen candidates 211; adopted reviews 204. Exact content comparisons passed for Meeting adopted decisions/messages/targets/status, Task projections, candidate bodies/digests, adopted review bodies, Workbench event identity/order/time, external action receipts, sent replies and queued task inputs/state. quick_check is ok, pre-existing foreign-key findings are unchanged, and shared scheduled configuration references have zero missing targets. This remains a disposable copy; deployment and live cleanup/readback have not yet occurred.
