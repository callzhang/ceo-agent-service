# Business core native source validation, 2026-10-15

This is source preparation only. The complete actual/fresh source contract is
`docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json`.
The oracle reconstructs captured literal source tables, indexes and all nine owned
triggers in native in-memory SQLite with foreign keys enabled. It imports no app
store, uses synthetic values only and cannot activate a target schema.

The actual metadata came from the immutable root-owned Store19/Email51 snapshot
bound in the preparation document. All actual handles were explicitly closed
immediately after capture and root was notified. The fresh source was constructed
under `/tmp/ceo-business-source.ybNekQ` using `git archive` of exact published
`7dadc492c1c62d5b0624f2f97dd0c89f4f358646` for `app/` and the phase4 inventory JSON.
A subprocess with that archive as both cwd and PYTHONPATH called published
`AutoReplyStore(explicit_path)` then `EmailStore(same_path)` using the existing
`/Users/derek/miniforge3/bin/python`. No provider or native Agent was invoked.

The complete source has 10 families/84 fields/3,526 actual rows, 16 indexes,
37 CHECK expressions, 12 outgoing FK groups/13 FK rows and 9 owned triggers.
Eight families use independent INTEGER PRIMARY KEY AUTOINCREMENT allocators;
Task and Project evidence use ordinary-rowid tables with composite primary keys.
The actual sequence waters in family order are 594, 833, 188, absent, absent, 893,
50, 50, absent and 6; fresh sequences are absent. Project's water 50 with 32 rows
is a concrete reason to preserve the allocator independently of row count.

Actual/fresh xinfo differs only in Task origin/suggestion placement and Project
created_at placement. Task index xinfo reflects those changed column positions.
Signal table SQL also differs literally (rebuilt quoted table versus fresh DDL).
Field types/defaults/nullability/PKs agree by name; FKs, owned triggers and all
other table/index declarations remain captured separately. The external Attention
item table also has assessment_json at a different ordinal in actual/fresh. No
normalization discards these differences.

The exact external endpoint families are:

| Existing child | Existing business parent columns |
| --- | --- |
| business_task_dingtalk_links | business_task_id → Tasks |
| business_task_follow_ups | business_task_id → Tasks; source_signal_id → Signals |
| business_task_todo_sync_outbox | business_task_id → Tasks |
| business_task_relations | from_task_id/to_task_id → Tasks; supporting_signal_id → Signals |
| business_work_cluster_tasks | task_id → Tasks |
| business_task_anchor_links | task_id → Tasks; anchor_id → Anchors; evidence_signal_id → Signals |
| business_project_candidates | confirmed_project_id → Projects; confirmation_signal_id → Signals |
| business_attention_items | anchor_id → Anchors; evidence_signal_id/resolution_signal_id → Signals |
| business_attention_tasks | task_id → Tasks |
| business_attention_proposal_tasks | task_id → Tasks |
| business_attention_events | signal_id → Signals |
| business_legacy_links | task_id/signal_id/anchor_id/project_id → their respective parents |

These are exactly 12 physical families/118 fields/23 FK groups; all target id with
NO ACTION updates/deletes. Their literal DDL/xinfo and complete FK lists are
captured without migrating or exercising all child behavior. No external-owner
trigger or view references the ten-family boundary. Project context citation
validation is service logic; the revision table itself accepts non-JSON text.

The source oracle covers exact metadata; all 84 approved CSV source fields;
omitted defaults and coherent timestamps; actual/fresh distinctions; binary
natural-key uniqueness; typed composite Project↔Anchor FK; Task self-merge and
state coupling; shared Signals across Tasks and evidence roles; Project evidence
uniqueness; immutable Signal/document/date facts including REPLACE and IGNORE;
date/event guards; immediate FK update/delete/insert failures; independent native
IDs with absent, negative, zero, generated and deleted-high-water cases; and one
transaction rolling back all ten families and every allocator after a late FK
failure. Immutable families reject deleting their highest IDs as the source does.

The initial artifact-existence test failed with the expected missing-source
assertion. After capture, `python -m pytest -q tests/test_business_core_source.py`
passed 36 cases. Two existing adjacent functions were then selected: creation
replay after merge copies evidence and zero-Task Project history/source persistence.
They passed 3 parameter cases in 2.68 seconds. Those existing cases preserve the
service distinction that replay reads the original event even when evidence is
shared, and Project context is a result with no required Task.

No runtime file, Email acceptance artifact, schema version, target declaration,
migration, body-retention policy, provider, production, CI, push or deploy is part
of this preparation. Future parent replacement still needs exact field mapping,
source-shaped mutations, remapped external endpoints and actual published-source
binding. None of the 188 source bodies is proved retrievable by this oracle.

Final focused source run: 36 passed in 0.18 seconds. Scoped Ruff and
`git diff --check` passed. `lsof +D` found no open handles under the owned
temporary root; that exact root and all archive/fresh DB/intermediate contents
were removed and absence was verified. Root-owned source cleanup remains with root.
Sequential independent SPEC then QUALITY review remains pending.
