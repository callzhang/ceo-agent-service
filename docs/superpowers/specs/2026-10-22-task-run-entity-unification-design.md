# Task / Run Entity Phase 2 — approved design

Derek approved implementation of the in-chat Phase 2 plan on 2026-10-22. This is its repository execution contract. The original analysis was based on `62f92daaeb10c6842612ee5291034d0d63ce163b`; execution starts from fresh origin/main `b32279d03`. These are dated inputs, not claims about future production HEAD.

## Outcome and authority

Tasks contain executable work only; Runs contain logical runs only. Inputs, business-key associations, CLI invocations, scheduled occurrences and domain records have named owners. Delete duplicate metadata, execution identity aliases and runtime source-schema dispatch. Table count may increase: Derek chose real entity boundaries over an approximately ten-table cap. Derek also chose aggregation of consecutive successful empty scans, preserving active work, failures, actual business outcomes and receipts.

No audit, authorization, fact-checking, cancellation, retry, effect reconciliation or native-session policy change is authorized. Keep provenance, necessary state, adopted facts and immutable receipts. Do not duplicate native trajectories. Test sends/recalls/provider verification use strict substitutes. Isolated worktree, copy validation, current-fact inverse, genuine old-code cold read/write, native CLI and UI acceptance precede standard deployment. Publication requires a net reduction under comparable packing, counting replacement indexes and technical inverse metadata.

## Task owner

Nine work kinds: reply, work_summary, meeting_alignment, okr_review, okr_analysis, workbench_turn, email_classification, feedback_processing, wechat_import. Existing workers/eligibility/state semantics remain; absent facts are NULL. In particular Meeting ready_to_send must not become claimable Consumer work.

```sql
CREATE TABLE tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 kind TEXT NOT NULL, status TEXT NOT NULL,
 available_at TEXT, attempts INTEGER, error TEXT, locked_at TEXT,
 lease_owner TEXT, lease_expires_at TEXT, owner_pid INTEGER,
 generation INTEGER, adapter_name TEXT, execution_generation TEXT,
 current_input_id INTEGER REFERENCES task_inputs(id),
 claimed_input_id INTEGER REFERENCES task_inputs(id),
 input_version INTEGER, claimed_input_version INTEGER,
 parameters_json BLOB NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

The final DDL adds source-backed constraints, keys and indexes. `parameters_json` holds only existing request parameters; lifecycle, reply input metadata and results/receipts do not return to it. Input numeric versions remain because deferral/cancellation and receipts consume them. Current and claimed input pointers are distinct.

Reply Task's channel, conversation_id, conversation_title, single_chat, trigger_message_id, trigger_create_time, trigger_sender, oa_url, business_object_key are owned by the bound input only. Latest context uses current input; execution context uses claimed input. All 7,873 original observed pairs agreed; preflight rechecks current source. Do not silently erase a mismatch. `task_inputs` retains the actual reply_task_inputs fields, replacing parent reply_task_id with Task FK, removing namespace/allocator wrappers. Preserve full (channel, conversation_id, trigger_message_id, input_revision_key) identity: 63 observed (Task, revision-key) collisions prohibit shortening it.

`task_business_keys` preserves key→Task uniqueness and Task→multiple keys. Do not replace it with one Task string (four observed multi-key Tasks). Meeting decisions/targets/message/send/calendar results and Email classification results have a Task-result owner, not parameters. Workbench sessions, feedback batches/rounds/decisions, OKR items, OA notifications, attachments/artifacts and historical Email candidates exit Tasks into explicit domain tables. Preserve actual source fields/keys/parent cardinalities mechanically; this is not authority to redesign Phase 3 domain behavior. No generic miscellaneous container.

## Run and adopted-result owners

```sql
CREATE TABLE runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER REFERENCES tasks(id), parent_id INTEGER REFERENCES runs(id),
 execution_generation TEXT, role TEXT, proposal_revision INTEGER,
 turn_attempt INTEGER, operation_id TEXT, status TEXT,
 session_id TEXT, transcript_start INTEGER, transcript_end INTEGER,
 structured_error_json TEXT, error TEXT, lease_owner TEXT,
 lease_expires_at TEXT, started_at TEXT, completed_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT
);
```

Consumer/Audit/Task Agent/Meeting/OKR share logical owner. No synthetic roles, revisions, invocations or lifecycle values where source has none. Runtime Attempts have 0..N cardinality per Run; also retain ordinary operation invocations with NULL Run. Retain actual workload/ordinal/route/runtime/auth/model/fresh-resume/session/transcript/status/failure/lease/effect-time/schema/clocks. Source result_envelope_json/final_result_json/tool_events_json are already absent physically: do not claim savings for deleting them, add persisted copies or remove in-flight validation.

Candidates/reviews become Run-owned adopted results. Remove redundant candidate Task/generation/Consumer/revision references in favor of the owner Run; preserve exact candidate/digest/review binding. Selection binds the exact candidate/review/option. Execution progress uses Consumer Run identity; retire candidate_execution_alias and reciprocal binding wrappers. Keep independent side-effect execution state and receipts: model completion is not external execution completion. ReplyAttempt remains a business-attempt identity, not a Run. Native trajectory is not a substitute for adopted results or receipts. Service-produced failure events retain independent phase/error/time facts; do not label them trajectory duplicates.

Scheduled occurrences, Workbench events, session index, classifier performance samples and setup events exit Runs into narrow domain tables. No runtime legacy source aliases; historical IDs required for explicit current-data inverse live in content-free technical metadata, not copied rows. Do not remap opaque business strings by regex.

## Schedule aggregation and indexes

`schedule_occurrences` owns actual trigger/dispatch/configuration/lease/execution-link/result facts. It does not pretend dispatched means Agent completed, nor fabricate Tasks/CLI calls for in-process service commands. Reuse first_scheduled_for/scheduled_for/occurrence_count; no persisted guessed empty-scan flag.

Aggregate only completed scheduled commands with proven zero work/effects, identical schedule, immutable snapshot and command, consecutive cron instants, no retained Task/Action/receipt/other reference. Manual, active, failed, unknown/empty summary and real outcomes remain individual. New commands use actual structured counters, not log parsing. Historical eligibility accepts only verified complete source return contracts; multi-counter recovery summaries cannot be guessed. Retain the newest occurrence identity, extend first/time/count transactionally; dedupe/restart catch-up must recognize covered cron instants. Inverse preserves aggregates, never invents retired per-tick random event IDs. History displays range/count.

Dispatch index is pending-only; keep all-state claim index (prior probe proved two pending-only indexes regressed metrics). Every replacement preserves actual identity constraints and proves planner behavior with public queries. Old source declarations are immutable migration oracles, not runtime factories.

## Verification and rollout

Freeze current DDL/markers/allocators/FK exceptions and source-field/reader/writer inventory before implementation. Every field has exactly one target or explicit retirement; fail unknown/unmapped fields. Preserve the original inherited FK finding without creating fake Tasks or silently repairing it. Compare logical SQL types/values and indispensable references; distinguish approved lossy tick aggregation from current-fact inverse.

Cover create/claim/refresh/revision/adoption/selection/effect/receipt, new input while claimed, multi-key merged Tasks, multi-invocation Runs, no-invocation work, ACK-before-settlement recovery with exactly one fake effect, missing source/trajectory, current-data inverse/new writes/genuine old-code cold read-write/reforward, UI queues/History/Attention/details/feedback/receipts. Test sends must never reach providers. Capacity baseline and candidate use the same source population and packing, with aggregation accounted separately, inverse-map cost included; fail publication if total does not shrink.

Production baseline measured in planning: main file 501,170,176 bytes; Task table/indexes 45,899,776/12,451,840 bytes; Run table/indexes approximately 90,775,552/52,867,072 bytes, dynamic counts. Dispatch index 9.31 MiB; candidate binding indexes about3.29 MiB; duplicate reply metadata about1.68 MiB are gross opportunities, not net claims. 69,662 empty rows/65,427 adjacent candidates still require continuity/reference proof.

Update architecture/runtime, total progress, acceptance matrix and changelog with actual release state. Historical production versions/PIDs/backups must be dated observations, not live assertions. Verify newest backup before predecessor retirement. The seven previously authorized temporary roots remain separately policy-blocked; their cleanup is not migration capacity gain.


### Review child identity clarification (Task3C1B, 2026-10-22)

The Run-owned Review row retains the existing source Review business/protocol key as `id`, alongside a unique `audit_run_id` foreign key that identifies its Audit Run owner. The approved ownership model does not require these IDs to be equal. Candidate predecessor wire fields and relational FKs refer to Review `id`; selection and progress owner fields continue to refer to Audit Run IDs. This preserves immutable source bodies and source inverse keys. The frozen source read found 23/23 linked predecessor bodies equal their source Review public IDs and differ from Audit Run IDs, with zero unresolved or mismatched IDs. The local native schema/store slice remains unactivated.

## Task2D2A Meeting delivery identity clarification (2026-10-23)

The existing `task_business_keys` owner table also carries the exact Meeting delivery identity as a native-only association: `meeting-alignment:{source public Meeting id}:{meeting_id}`. It does not change the existing Reply `business_object_tasks` source declaration or generic Reply multi-key behavior. The field-map remains a field map; `task_identity_associations` in the generated assembly artifact separately documents the Reply source relation and Meeting native-only relation. A runtime reader accepts canonical Meeting Task ID, checks native kind, and returns the sole stored key.

Current inverse branches by parent Task kind. It restores only Reply-owned key rows as authentic `business_object_tasks`; Meeting-owned rows are omitted from old source tables. The portable `meeting_business_keys` handoff stores only `{source_meeting_physical_id, task_business_key_row_id}`. The digest covers it, the key text is rederived from current source facts, and the Meeting key-row ID contributes to restored global `sqlite_sequence('tasks')`. Missing source Meeting owners are retired and their edges are not replayed. Existing delivery Action keys, prepared state, receipts, UUID5 inputs and provider payloads stay byte-identical. This is the inverse prerequisite only; Task2D2B owns the Meeting creator/producer cutover.
