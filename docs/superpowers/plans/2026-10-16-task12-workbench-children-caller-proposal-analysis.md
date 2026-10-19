# Unselected Workbench child caller proposal

The adjacent patch completes the bounded runtime SQL proposal for `workbench_artifacts` and `workbench_attachments` in `app/workbench/store.py`. It applies **after the exact existing-eight Workbench proposal**. Application and test files remain unchanged. This is static copied-code evidence; no public method, constructor, database or attachment filesystem behavior was executed, and no runtime release is selected.

| Input or output | SHA256 |
| --- | --- |
| Original Workbench source | `4a3bb9287dfea1f2e13e31be5ff2a9ac536a458ac079f30ccb1519848870e18e` |
| Existing-eight prerequisite patch | `66d1c19cfee4fd636d596400b228b0926f9fca46aee177d180a6701b33386c26` |
| Workbench after prerequisite | `59e56e58f9c071631b47e60827a931681ba14b2657115f1e14aee8cfae0d012a` |
| Incremental child patch | `f9e64c543a9aec532184a99ee9832d691635bdbaf4dfade9c515e25e3f268cc7` |
| Cumulative Workbench candidate | `b9027194eb4f9f277630935bf2d00a36792047f626e6b0242667d22a7de070db` |
| Accepted `app/task12_storage.py` | `8a7a84db53006065697ae44a2e39dc27a2008ca2687b6ddfc54e23ff9cc1729a` |
| Current `app/task20_definitions.py` | `cf58403bcc9dc7bf4c3ef5b9a777ff941c7a6de210e04b29e4cb7abb1e0df4d0` |

Files: [prerequisite](2026-10-16-task8-workbench-caller-proposal.patch), [incremental patch](2026-10-16-task12-workbench-children-caller-proposal.patch), and [machine-readable source inventory and verification](2026-10-16-task12-workbench-children-caller-proposal-analysis.json). The JSON includes the exact original execute SQL expressions and parameter expressions, both projection constants, source anchors, helper hashes and external dependency classifications.

## Complete bounded runtime inventory

The source walk inspects every `execute` first argument, including `JoinedStr` references and indirect module projection consumers. There are **nine runtime owners and seventeen execute sites**, plus the two module projection constants. This includes `list_artifacts`, which contains no literal native table name. All anchors below refer to the original source before either patch.

| Runtime owner | Original method line | Child SQL sites | Proposal effect |
| --- | ---: | --- | --- |
| `save_attachment` | 339 | 367,396,414 | Request replay and readback keep their queries; native metadata insert becomes `insert_task12` with the same eight values. |
| `list_attachments` | 437 | 440 | Existing projected read consumes the new module constant. |
| `append_artifact_event` | 881 | 913,923 | Native artifact insert becomes `insert_task12` with the same five values; readback consumes the new projection. |
| `list_artifacts` | 931 | 934 | Existing artifact/turn join consumes the new artifact projection. |
| `timeline_snapshot` | 945 | 1017,1052 | Both source UUID parent filters and all dynamic cursor/order/limit fragments remain exact. |
| `get_artifact` | 1109 | 1111 | Existing UUID lookup consumes the new projection. |
| `workbench_stats` | 1116 | 1176,1179 | Both existing counts consume the new projections. |
| `attachment_paths_for_executor` | 1226 | 1240 | Existing lease-checked private path read consumes the new attachment projection. |
| `_reconcile_attachments` | 2286 | 2297,2305,2311,2325 | Existing projected scan consumes typed source fields; three native DELETE statements target the attachment kind and typed source UUID field. |

The actual edits comprise **two plain inserts, three native deletes, and two projection constants**. There is no child UPDATE, UPSERT, REPLACE or RETURNING caller in this bounded source. No statement shape is invented for an absent operation. All existing dynamic cursor fragments, placeholders, Python parameter expressions, pagination, DTO construction, parent lookups and event coupling remain in their original methods.

## Identity and native parent semantics

These two native sources are ordinary ROWID tables with a nullable TEXT `id` primary key. The public UUID `id`, hidden source ROWID and global physical Tasks ID are three distinct coordinates. Task12 stores the UUID in its exact typed payload field and the hidden ROWID in `legacy_key`; the accepted helper allocates the source ROWID independently. Neither caller consumes the insert cursor or lastrowid. This proposal adds no ROWID conversion or public result adapter.

The current native child `task_id`/`turn_id` columns already hold INTEGER physical parent IDs. Both inserts retain the exact `task_physical_id(db, 'workbench_tasks'/'workbench_turns', public_uuid)` expressions. The accepted helper stores those source values in `parent_id`. It must not resolve them again as public keys or replace them with the child's physical ID. Existing old-eight lookup helpers remain used for parents only.

`task12_projection` returns source-shaped columns, including the native physical parent field. The existing `c` child-to-`t` Tasks joins remain in both module constants and recover `t.legacy_key` as the original public UUID parent. The attachment projection still returns its original nine columns in original order; artifact still returns its original six. Thus existing DTO fields, filename/path redaction and physical-parent relations stay source-shaped. Neither constant uses the historical child table after cutover.

The three reconciliation deletes remain one native DELETE each, with the same `row['id']` parameter and existing transaction ownership. They scope by `record_type='workbench_attachments'` and `task20_column('workbench_attachments','id')`; they do not compare the public UUID to structural `tasks.id` or numeric `legacy_key`. The source does not consume their cursor/rowcount. No point-delete helper, extra transaction, commit, fallback, dual writer or read-path mutation is added.

## Filesystem and transaction preservation

`save_attachment` retains all validation, SHA256 calculation, idempotent same-request metadata comparison, canonical UUIDs, task-existence checks, generated directory/path construction and attachment lock. Its existing temp-file body write, flush/fsync, fault hook, BEGIN IMMEDIATE, metadata readback, `os.replace`, file fsync, directory fsync and metadata commit keep their exact order. The existing rollback/close and temp/final-path unlink handlers are unchanged. The new insert occupies the original insert position and supplies values in the original tuple order; omitted `created_at` remains a native statement default through the accepted helper.

`append_artifact_event` retains its executor lease check, next-event-sequence check, original physical turn lookup, coupled `runs.workbench_events` insert and both readbacks in the same BEGIN IMMEDIATE transaction. Artifact `path` remains a stored source metadata value; the proposal does not read or rewrite its file body.

`_reconcile_attachments` remains called by the unchanged `WorkbenchStore.__init__` after the Store constructor. It retains the existing lock, managed-directory/symlink checks, generated UUID/path/file validation, metadata deletion conditions, referenced-path set, temporary-file cleanup and generated-orphan cleanup. This source reconciliation does not gain a content hash or size validation policy. Only its typed metadata projection and DELETE targets change. Both its database commit boundary and later filesystem iteration remain exact.

No authorization, audit, ownership, safety, source-body retention, native-home, retry, lock or reconciliation policy is introduced. Original filesystem functions and control flow are preserved, including their existing checks and failure behavior.

## Verification performed

Two owned temporary code copies were used and removed. The exact prerequisite patch applied first, then the incremental patch passed `git apply --check` and application. Applied bytes matched the independently built cumulative candidate. Reversing the incremental patch restored the exact prerequisite bytes; subsequently reversing the prerequisite restored the exact original source bytes. The real application source hash was rechecked and remains unchanged.

The pinned executable `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B` reported Python3.13.12. In-memory compile, Ruff with the repository config and `--no-cache`, and copied-module import without invoking constructors all passed. Import evaluated both projection strings successfully; it did not open a database.

All **79 qualified function signatures/decorators/return annotations** are exact. Three Workbench method bodies change; the other76 function ASTs are identical to the prerequisite candidate. All16 bodies changed by the existing-eight proposal remain exact. The fourteen listed filesystem/DTO/constructor/connection methods are exact, including `WorkbenchStore.__init__` and `_connect`. All30 explicit transaction/commit/rollback/close call ASTs remain exact. All other module statements are exact apart from the added Task12 import and two scoped projection assignments.

For each changed body, a second AST comparison masks only the two replacement INSERT expression statements and three DELETE SQL first arguments. Every other AST node, including `with`, `try`, `except`, `finally`, filesystem calls, error messages and return statements, remains exact. Each replacement insert's ordered value ASTs match the original execute tuple. No native child FROM/INSERT/DELETE target remains in current Workbench runtime SQL; outer source-alias names remain intentionally. A first validator keyed methods by bare name and counted78 because the two `__init__` methods collided; the corrected class-qualified validator independently proved all79. Candidate bytes were unchanged by that validator correction.

These results establish integrity, bounded source closure and static preservation. They do not establish native SQL/error/allocator equivalence through the real public methods, file durability/recovery, races, source-target data equality, constructor selection, migration or runtime acceptance. Existing storage component tests and the earlier Workbench81-checkpoint public proof are separate evidence; neither exercises this new child conversion.

## Separate final constructor, schema and dependency obligations

The application-wide family/constant search found no additional direct current child SQL DAO outside Workbench. It did find dynamic schema consumers and current constructors; these are explicit root obligations and remain unchanged by this runtime-only proposal:

- `app/store.py:393,394,443,448,449` retains current table/index manifests. Current declaration checks at2489, `_initialize` auxiliary construction at2744 and `_migrate_removed_runtime` current predicate at8527 still depend on historical Workbench schema selection. Root must select final manifests/factories coherently without recreating the retired child sources.
- `app/workbench_storage.py` retains `WORKBENCH_CHILDREN`, the exact historic declarations, `_child_ddl`, `create_workbench_auxiliary_schema`, generic capture/migrate/restore loops, `_current_auxiliary_objects` and `workbench_schema_is_current`. These use table names through dynamic selector tuples and execute loops. Preserve frozen source factories; introduce final current construction/predicates only in the separately scoped root change.
- `app/task_storage.py:_rebuild` and `install_task_child_guards` use generic source-table/FK introspection and dynamic SQL. Their Workbench source profiles and retained confirmation guards remain historical facts, not remaining current child DAO calls. Final release coordination must account for the two children moving inside Tasks while preserving the confirmation family.
- Store/Email target connections must register the accepted exact scalar functions as part of final cutover. The real `_open_connection` and every connection/constructor body remain unchanged here.
- The existing-eight `app/workbench/native_events.py` typed turn-projection companion remains outside this incremental patch. Root already reproduced its nonempty-cache/hydration dependency and negative control in Workbench81 proof. Full acceptance must keep that companion and re-exercise it; empty-cache fixtures can conceal it.

Complete constructors, actual/fresh source copy and current-fact inverse, predecessor writes/reforward, other callers, native source/body contracts, UI/business outcomes, final11-table reconciliation and production remain root-owned. This patch selects none of them.

## Required paired public and filesystem acceptance before transfer

Root's next proof must run the actual source and cumulative patched Workbench methods in isolated copies against exact actual/fresh source metadata and accepted target declarations. Retain the original connection contexts, leases, event append, DTO conversions, locks and all filesystem helpers. A prepared empty-target constructor bypass may support a bounded proof, but must be labeled; final selection still requires normal constructors and startup reconciliation. Compare original source fields plus storage classes, source hidden ROWIDs, independently varying public UUID/global physical IDs, parent relations, errors, transactions and actual file bytes/paths/hash after every operation. Normalize only explicitly controlled clocks/UUID streams and task-owned root prefixes.

| Real paths to exercise | Required distinguishing cases |
| --- | --- |
| `save_attachment`, `list_attachments`, `timeline_snapshot`, `attachment_paths_for_executor` | Nonempty binary/Unicode metadata; zero-byte content; same-request replay with unchanged metadata/file/ROWID; filename/media type/size/hash conflicts; invalid bytes/UUID/empty inputs; wrong/missing task and wrong/expired executor lease. Physical parent must differ from public UUID and child hidden ROWID. Verify bytes/hash/size and private-path/public DTO boundaries. |
| `save_attachment` failures | Inject at temp-created hook, metadata insert/readback, rename, file fsync, directory fsync and metadata commit using the original hooks/filesystem boundary. Compare exact exceptions, rollback, temp/final file cleanup and absence/presence of metadata at the original boundaries. Do not replace the public method or its SQL execution. |
| Attachment concurrency and lock | Two real stores/connections with same and conflicting request IDs, concurrent reconciliation and file save, and lock contention. Compare request uniqueness, generated file count and existing transaction boundaries. |
| `_reconcile_attachments` through normal startup when selected | Valid metadata/file survives; invalid UUID, missing directory, wrong storage_path, missing/nonregular file and generated-orphan/temp entries follow original outcomes; unrelated nongenerated names remain. Exercise symlink components and original error behavior. Verify each typed UUID DELETE removes only the selected child kind and leaves same-looking parent/other-kind payloads intact. |
| `append_artifact_event`, `list_artifacts`, `get_artifact`, timeline/stats | Valid running lease and exact sequence, wrong/expired owner, out-of-order sequence and missing turn; multiple parents/tied clocks and pagination equality boundaries; artifact + runs insert failure rolls back both. Verify public UUID readback and independent physical/global/source identities. |
| Cross-family identity and current facts | Interleave artifact and attachment allocations with all old-eight/new-twelve kinds; delete a maximum hidden source ROWID then insert again; retain source UUID uniqueness and native constraints/defaults. Full copy/inverse must restore current source hidden ROWIDs and values, including post-conversion writes. |

No real database/provider/native CLI/network/main/push/service/deploy action, source-table retirement, application change or public filesystem proof occurred in this proposal task.
