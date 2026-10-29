# Remaining action families: source preparation

This is source preparation for the approved ten-core-table plus one-ledger
redesign. It selects no target schema, version, constructor, migration or
retention rule. The future immediate publication predecessor remains unbound.

Readonly metadata around2026-10-14T10:23Z found121 non-SQLite-internal physical
tables. Of45 remaining old tables mapped to actions,13 belong to Email13,
9 to follow-up/sync,11 to the prepared business/evidence unit, and12 are below.
Existing target actions/runs/tasks are counted separately. These groups
explain remaining work; they do not imply any disabled migration is published.

Root independently verified all116 live columns against the original approved
field-map CSV, with no missing, extra or duplicate column names in these twelve
families. The addendum introduces none of them. In a separate mode=ro,
query_only single transaction root confirmed23 indexes,9 FK constraints and14
owned triggers, including exact sequence entries shown below. Production
HEAD before and after that read was9cd41bcf5ea43a275eeb1376817d37f7cef6bd21.
Git observations and the DB snapshot are not atomic. Caller references use
worktree0e1c404a; root checked that the intervening Store diff only batches two
other reconciliation methods, with no source DDL or these helper changes.

| Source family | Fields | Rows at research snapshot | Indexes / outgoing FK / triggers | Native key and sequence |
|---|---:|---:|---|---|
| email_historical_classification_history |9|183|1 /0 /0|TEXT event_id PK; no allocator|
| email_historical_operations |15|0|2 /0 /0|TEXT operation_id PK; unique model_id + stable_message_identity|
| errors |8|15352|2 /0 /0|INTEGER PK AUTOINCREMENT;15395|
| feedback_events |13|48|2 /0 /0|TEXT key PK; feedback_token + received_at index|
| feedback_iteration_decision_items |3|0|2 /3 /2|composite decision_id + feedback_key PK|
| feedback_processing_transitions |10|5|3 /0 /0|INTEGER PK AUTOINCREMENT;5|
| managed_skill_export_receipts |5|1932|1 /1 /2|INTEGER PK AUTOINCREMENT;1932|
| meeting_alignment_delivery_claims |2|0|0 /1 /2|INTEGER job_id PK; no AUTOINCREMENT|
| meeting_memory_write_events |14|566|3 /1 /2|INTEGER PK AUTOINCREMENT;295532|
| runtime_skill_load_receipts |6|4027|1 /1 /2|INTEGER PK AUTOINCREMENT;4027|
| task_memory_write_events |16|505|3 /1 /2|INTEGER PK AUTOINCREMENT;505|
| wechat_deliveries |15|133|3 /1 /2|INTEGER PK AUTOINCREMENT;137|

## Current writer and reader closure

All twelve have current writer paths. Zero rows in historical operations,
feedback decision items and meeting delivery claims do not prove dead code.
Neither row age nor an empty snapshot authorizes deletion.

- Historical Email outcomes: email_worker.py:694/2557 calls
  EmailStore.record_historical_classification_outcome; its writer/reader are
  email_store.py:9075/9276. Processing operations begin/touch/complete from
  email_worker.py:609/639/695 and resume at2477; EmailStore methods at9316–9370.
  Their operation identity, model/message uniqueness and processing/terminal
  state must be frozen with the Email caller boundary.
- Errors: Store.record_error at store.py:29521 has active worker/CLI callers.
  Resolution, settlement and History readers remain at29537–30056,
  quality_gate.py:979 and audit_web.py:3748. Resolved detail retirement still
  requires its approved exact-source/read-path analysis; active code alone
  does not establish that every historical text value is necessary.
- Feedback events enter through feedback_events.py:92 and Store20653;
  UI synchronization at audit_web.py:9445 and processing readers at
  Store21060/22920–23304 remain active. Decision items are written through
  registration.py:1226 to Store20899/20990 and read/validated at21020/22470.
  Their three declared parent edges and two immutable triggers need a paired
  tasks/actions boundary. Transitions are appended by Store21308/21458/22882
  and read by21547/21798/21940; source/target enums and lineage are existing
  facts, not a new transition policy.
- Managed Skill exports: managed_skills.py:562/572/662 and
  consumer_system_release.py:298 reach Store.record_managed_skill_export at
  8421; latest receipt reads8455. It checks the exact revision and digest and
  retains its revision FK plus immutable update/delete triggers. Runtime Skill
  load success/failure at managed_skills.py:699/708/713 reaches Store8677/8725;
  release, Console and validation readers include consumer_system_release.py:221,
  registration.py:1732 and Store22497. These receipts depend on the prepared
  configs parents, rather than permitting a duplicated configuration body.
- Meeting delivery claims: meeting_alignment.py:678 reaches Store17609/17635;
  release/reset/eligibility at17656/17712/17816 remain active. Its job FK is
  CASCADE and its two typed task-child triggers remain part of the source.
- Meeting Memory: Store16834/16990/17021 enqueue and meeting_memory_write.py
  187/287/366–498 processes; reconciliation/history at Store17040–17418 and
  audit_web.py:3679. Its unique meeting_job_id, due/lease indexes, task FK and
  two typed child triggers are current. Sequence295532 must not be replaced
  by the much smaller row count566.
- Task Memory: Store16850/16898/23836 enqueues the adopted task outcome;
  task_memory_write.py:88–130 settles it, and Store16971/audit_web.py:3714 read
  failures/history. Preserve the existing task + execution_generation uniqueness,
  task FK, due/lease indexes and child triggers.
- WeChat: wechat/consumer.py:266–367 finalizes through Store15264; service.py
  105–231 and Store15497–16249 send/recover; audit_web.py:3254–3298 reads History.
  Its task + execution_generation identity, task FK, status index and child
  triggers describe existing delivery state; no send or replay was tested here.

## Next source gate

Freeze exact literal actual/fresh declarations and full external SQL/FK closure
before target definitions. Coordinate the four task-child families with existing
tasks; feedback items with their decision/item/round parents; Skill receipts
with configs; and historical Email operations with Email/actions. Source native
DML, independent allocator behavior, cascade/immutable/typed-child constraints,
lease/CAS and representative query costs still need their own source oracle.
No source body, row hash, credential or trajectory was recorded. No test suite,
provider, native CLI, production write, constructor or migration was run.
