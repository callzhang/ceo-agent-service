# Business TODO evidence candidate validation

This unpublished database-redesign slice moves the existing
`todo_evidence_candidates` source kind into the typed `business` table. It does
not change the Task API model or retire any other source family.

## Exact source mapping

The mapping follows the 15 rows for `todo_evidence_candidates` in
`docs/superpowers/specs/2026-10-11-database-redesign-field-map.csv` and the
captured source declaration fingerprint
`82469a32d895faa593a5b0a3d9473a1ade2a6c524e8c4db6164d9af7fd0e3db1`.

| Source field | Business field | Preserved contract |
| --- | --- | --- |
| `id` | `public_id` / `legacy_key` | Source-visible signed ID and per-kind allocator |
| `project_id` | `todo_evidence_candidate_project_id` | Integer scalar, matching source affinity |
| `todo_id` | `todo_evidence_candidate_todo_id` | Integer scalar, matching source affinity |
| `source_type` | `source_type` | Inline provenance value |
| `source_ref` | `source_ref` | Inline provenance value |
| `source_created_at` | `source_created_at` | Inline provenance value |
| `evidence_text` | `todo_evidence_candidate_evidence_text` | Text value |
| `reason` | `todo_evidence_candidate_reason` | Text value |
| `confidence` | `todo_evidence_candidate_confidence` | REAL value and SQLite storage class |
| `status` | `status` | Exact values: `candidate`, `enqueued`, `accepted`, `rejected`, `error` |
| `work_summary_input_id` | `todo_evidence_candidate_work_summary_input_id` | Integer scalar; no FK exists in the source declaration |
| `decision_json` | `todo_evidence_candidate_decision_json` | Text value |
| `dedupe_key` | `todo_evidence_candidate_dedupe_key` | Unique per candidate, matching source uniqueness |
| `created_at` | `created_at` | Text value |
| `updated_at` | `updated_at` | Text value |

The source table has no foreign keys. The migration does not infer new foreign
keys for `project_id`, `todo_id`, or `work_summary_input_id`. Existing Store
writers, task listing, discovery and History reads use the typed projection;
logical History `source_table` labels remain `todo_evidence_candidates` for
existing links and clients.

## Verification

- Focused actual/fresh Business schema release, Business storage, and candidate
  Store DAO tests: **455 passed**.
- Frozen actual SQLite copy: three candidate rows before migration; forward
  projection preserved every mapped source value/type, source IDs, allocator
  water, and retained-child facts. A synthetic candidate written after forward
  migration survived inverse reconstruction and reforward.
- `PRAGMA quick_check` passed. The one pre-existing
  `runs` → `tasks` foreign-key finding remained identical before and after.
- Frozen source SHA-256 remained
  `970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`.
  All writes were on a temporary copy, removed after verification.

This proves this Business-only slice on actual/fresh declarations and a frozen
database copy. It does not prove the full paired 116-to-11 cutover or production
readiness. No commit, push, production write or deployment is included.
