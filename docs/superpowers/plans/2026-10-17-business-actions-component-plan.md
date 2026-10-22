# Business Actions ten-family component, 2026-10-17

Approved final-model implementation; next bounded opt-in component after accepted current25 copy. Final10core+ledger11 and capacity remain open. One implementer, then fresh SPEC, then fresh QUALITY. Do not select the new schema in Store/Email/paired coordinator or alter current callers in this unit. No commit/push/deploy, production DB writes, provider calls, native role execution or real sends.

## Evidence and exact source

Accepted real copy: `../specs/2026-10-17-business-candidates-current-copy.json` SHA5c5a0e30f2a8dde986e08a7aa5f98a8c1de4d6801740f6bdd4e200f2c15ec09e,116→25→116→25/738.207s. Exact post-current25 ten-family declarations and pre-edit oracles are in `../specs/2026-10-17-business-actions-current25-source.json` SHA7c65b685c8cc75b8cd36ab6569f476912d853546ec276d5d1a4426248e696ae2. This includes62 existing fields, original SQLite types/defaults/CHECKs/unique/indices/FKs. Original approved1611-field CSV maps these ten families to Actions. No invented business fields, generic relation table, new policy/status, source replay or row-body archive.

Source fields, in actual post-target order:

```json
{
  "business_attention_events": [
    "id",
    "attention_item_id",
    "event_type",
    "signal_id",
    "before_json",
    "after_json",
    "reason",
    "created_at"
  ],
  "business_attention_proposal_tasks": [
    "attention_item_id",
    "task_id",
    "created_at"
  ],
  "business_attention_tasks": [
    "attention_item_id",
    "task_id",
    "created_at"
  ],
  "business_project_evidence": [
    "project_id",
    "signal_id",
    "created_at"
  ],
  "business_task_anchor_links": [
    "id",
    "task_id",
    "anchor_id",
    "status",
    "active",
    "evidence_signal_id",
    "reason",
    "created_at"
  ],
  "business_task_date_evidence": [
    "id",
    "task_id",
    "date_type",
    "value_at",
    "raw_phrase",
    "source_signal_id",
    "actor_kind",
    "actor_user_id",
    "actor_name",
    "created_at"
  ],
  "business_task_evidence": [
    "task_id",
    "signal_id",
    "evidence_role",
    "created_at"
  ],
  "business_task_relations": [
    "from_task_id",
    "to_task_id",
    "relation_type",
    "status",
    "supporting_signal_id",
    "reason",
    "created_at"
  ],
  "business_task_signals": [
    "id",
    "source_type",
    "source_ref",
    "source_time",
    "conversation_id",
    "conversation_title",
    "author_user_id",
    "author_name",
    "source_document_id",
    "context_json",
    "dedupe_key",
    "created_at",
    "author_kind"
  ],
  "business_work_cluster_tasks": [
    "cluster_id",
    "task_id",
    "created_at"
  ]
}
```

Four source tables have INTEGER PRIMARY KEY AUTOINCREMENT: attention_events, task_anchor_links, task_date_evidence, task_signals. Other six are ordinary composite-PK tables with independent hidden ROWID; preserve native ROWID allocation/reuse/lastrowid where callers consume it. All4 public AUTO waters and source presence bits survive forward/currentwrites/inverse, including deleted maxima and zero/negative native IDs. Actions.id is an ordinary INTEGER PRIMARY KEY, not AUTOINCREMENT; preserve the preexisting optional sqlite_sequence(actions) presence/value exactly, following the existing Error/Feedback components. Do not introduce a new private Actions AUTO counter or apply Business physical-water promotion here. Remove only this component's owned allocator aliases on inverse, after restoring corresponding native waters. Do not touch earlier Action-kind aliases/private counters; the candidate alias regression demonstrated why this must be tested nonempty, through lower-level strict recognition.

## Source and dependency boundaries

Use the successful receipt's post-target source declarations, not original116 native Business parent names. Fresh current25 may have a distinct exact source profile: capture it independently from the genuine existing fresh constructor before changing factories; keep actual/fresh distinctions explicit and frozen in runtime constants. Runtime never loads evidence JSON. Capture source metadata only, not row backups; conversion and inverse read current facts inside the caller-owned FK-off transaction.

The new signal Actions kind retains its native SourceDocuments FK for this intermediate component. SourceDocuments body/identity/immutable rules and all runtime source-reader behavior stay unchanged here; provenance/body retirement is separate. Signal context_json contains real source/identity/assignment context used by semantic services, so preserve its existing fields and JSON/object constraint. Date raw_phrase is an existing adopted date quotation, not permission to delete source evidence. No30day policy.

Map existing source edges to typed generated endpoints: six membership/evidence PKs keep their exact uniqueness; Business Tasks/Projects/Anchors/WorkClusters/Attention reference their existing public aliases; source signal references target the new exact signal Action-kind endpoint. Existing typed Business attention evidence/resolution and candidate confirmation FKs, current followup/task-event Actions signal FKs, and native LegacyLinks.signal_id must also be rebuilt to the new signal alias, preserving all unrelated declarations/rows/storage types/IDs/counters and original NO ACTION behavior. The ten-family set has21 outgoing FK rows in the original metadata; derive and verify actual post-target graph rather than hardcoding this count as a replacement for declarations. Preserve finite source external triggers/views by translating only established endpoints; reject unsupported dependencies before DDL as existing migration components do. No additional audit/authorization logic.

## Implementation and oracles

Create `app/action_business_definitions.py`, `app/action_business_storage.py`, `tests/test_action_business_storage.py`. Narrowly extend structured `app/action_storage.py` with default-false include_business_actions; if needed thread default-false endpoint selection through `app/business_definitions.py`, `app/business_candidate_definitions.py`, `app/business_storage.py`, `app/action_followup_sync_definitions.py` and narrowly existing schema-predicate/dependency primitives. Claim exact paths first. Preserve historical/default declarations byte-for-byte. Do not edit Store/Email/caller routes/paired current selector. Do not rewrite unrelated accepted dirty hunks.

Before app edits, add and run fixed pre-edit oracles from source metadata: both current Actions variants2c97f88e18fe052ce247e61216303783c47662ca42fdb2f895ddd628cc49dcc2 and3e4873f10d7af890bc208082875ac969f94a526be922c6129745f19eac7d3d62, selected12-kind Businessf594908497b0469ad772423155432132bfe16ac414c7c2f06690c4cb4697bc89, plus native-nine oracle91fedd3da844ec56624767b78fac27210d342414de7b79c01f02de5f038d9d91. Use exact options in metadata; never derive an expected oracle from edited factories. New API import regression must be RED before implementation.

Provide source-shaped projection and narrow native write primitives sufficient for existing ten-family callers, following accepted Action Feedback/TaskDelivery patterns; no generic SQL parser or arbitrary JSON framework. Suggested interface `business_action_projection(kind,source_variant=...,include_rowid=False)`, `capture_business_action_source(db)`, `migrate_business_action_schema(db,metadata)`, `restore_business_action_schema(db,metadata)` and narrow insert/update/delete helpers as actual callers require. Source kind names remain the native table names, matching existing Actions conventions; all payload members correspond to the62 source fields. Use existing structural fields, finite typed constraints, query-qualified partial indexes and real FK aliases. Do not invent a fake source id for composite records in their public source projection. Keep internal physical IDs separate from native IDs/ROWIDs and composite identities.

Native one-statement defaults/timestamps, sqlite scalar affinities/storage classes including legal BLOB/TEXT/INTEGER/NULL cases, required/enum/JSON/check/unique errors, IGNORE/update rowcounts, failed FK behavior, and caller commit/rollback semantics remain. Plain-INSERT SQLITE_FULL must reproduce native rollback behavior where relevant. No hidden helper commit/rollback of a successful transaction, no read-time repair or legacy fallback.

## Focused verification and handoff

TDD against genuine source fixtures, actual and independently captured fresh profile. Seed all ten families and every supported incoming endpoint, including composite rows, nonempty current Actions and Business, native LegacyLinks, and the four native AUTO streams plus original Action public aliases, optional sequence presence/value and ordinary physical ROWID MAX behavior. Compare exact fields/values/storage types/hiddenROWIDs/keys/waters before and after, not only API status. For wrong-kind and absent parents, native and target outcomes agree. Exercise current target writes and deleted maxima, JSON-roundtrip metadata inverse, original source declaration/schema and strict lower-level recognition, native writes after inverse and reforward. Preserve unrelated existing FK findings; reject mutated declarations and reserved TEMP/MAIN collisions before DDL. Include late-failure caller rollback and INTEGER FULL cases at the narrowest useful level. All prior default current25 factories and current constructors remain unchanged; opt-in helper graph should retire ten names25→15, but no current runtime15 claim.

Use a task-owned /tmp TemporaryDirectory with TMPDIR and --basetemp/cache outside repository. Run focused tests only; -n2 where useful. No full service suite. Clean owned temporary DB/code artifacts at completion; don't touch shared/previous denied roots. Update architecture/runtime descriptions of this opt-in component after tests pass, preserving selected current25 truth and capacity-open boundary. Return DONE/concerns, exact changed paths/hashes, RED/GREEN and focused results, and no outstanding app edits. Root freezes files, obtains fresh SPEC then QUALITY, and only then plans current caller selection and a new actual copy. Component success is not current15/final11/CLI/UI/space/deployment acceptance.

Root clarification after author code evidence: Business private AUTOINCREMENT-water rules do not apply to ordinary Actions.id. Existing Actions optional sequence aliases must remain exact; new four native AUTO streams retain their own exact waters. At shared physical ROWID MAX, compare ordinary SQLite allocation behavior while exposing only the new family's native public ID; no invented global-full rejection or counter promotion. This changes no business field, source policy or current selection.
