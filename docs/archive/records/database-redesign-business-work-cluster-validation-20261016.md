# Business Work Cluster typed-storage slice — 2026-10-16

This isolated candidate moves the existing `business_work_clusters` source
family into the shared `business` table. It adds no fields and changes no
cluster, candidate, membership, or Project behavior.

## Exact mapping

| Source field | Business kind | Target field |
| --- | --- | --- |
| `business_work_clusters.id` | `work_cluster` | `business.public_id` |
| `business_work_clusters.title` | `work_cluster` | `business.title` |
| `business_work_clusters.created_at` | `work_cluster` | `business.created_at` |

The source declaration is `id INTEGER PRIMARY KEY AUTOINCREMENT`, nonblank
`title`, and `created_at TEXT NOT NULL DEFAULT current_timestamp`. The kind has
its own public ID stream. Business physical row IDs remain separate from that
public ID. The type-specific alias is `work_cluster_public_id`.

These are the only three child references to the source parent; child columns
and row data stay in their existing tables:

| Child table | Child column | New FK target |
| --- | --- | --- |
| `business_work_cluster_tasks` | `cluster_id` | `business(work_cluster_public_id)` |
| `business_project_candidates` | `cluster_id` | `business(work_cluster_public_id)` |
| `business_legacy_links` | `cluster_id` | `business(work_cluster_public_id)` |

Other FKs and columns in those children stay as declared by the source schema.
The DAO still creates a cluster inside its caller's transaction, returns the
same signed public ID, and lists the same `BusinessWorkCluster` record shape.
Project proposal title fallback reads the typed projection. Task Agent and
retrieval continue to use cluster IDs through the unchanged child APIs.

## Migration and rollback boundary

The finite Business release now recognizes six source parents and retains the
same fifteen child tables. It captures all six source declarations and
allocator waters before DDL, copies cluster rows as typed `work_cluster`
records, and rewrites only the three incoming cluster FKs. The inverse reads
the current Business projection and current child rows, then recreates the
captured source schema in the caller-owned FK-off transaction. The separate
`business_work_clusters` table is omitted from the current Store constructor.

The exact actual/fresh source-shaped migration tests pass with all six parent
families populated, including cluster membership, project candidate and legacy
link references. They verify source-shaped values and SQLite storage classes,
child hidden ROWIDs, all 22 translated incoming Business FK endpoints, the 18
unchanged child FK endpoints, `foreign_key_check`, `quick_check`, allocator
waters, inverse after current writes, and reforward.

The Business-only migration was also exercised inside a rollback transaction on
the frozen 463 MiB actual database copy
(`970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`). It
contained 16 Work Clusters. Forward copy preserved all captured Business
parent/child facts, types and sequence waters; a new target Work Cluster
received public ID 17; inverse preserved that current write in the old source
table; the enclosing transaction was rolled back and the full database
SHA-256 remained identical. This is whole-file source-copy evidence for the
Business slice. It does not establish the complete paired 116-to-11 release
or final table count.

## Verification and remaining limits

- `tests/test_business_schema_release.py`: 37 passed.
- Cluster/Project candidate resolution, console and retrieval selection:
  25 passed.
- Paired Email/Business publication, inverse, and constructor selection:
  5 passed.
- Frozen actual-database Business forward/current-write/inverse/rollback check:
  passed; 16 existing clusters preserved, probe ID 17, source SHA unchanged.
- `tests/test_task_semantic_store.py`: 474 passed and 9 failed in unrelated
  existing state/source-document fixture paths (`service_state` is absent from
  the current schema; one historical fixture still inserts removed `tasks`
  columns). The Work Cluster model, manifest, writes, constraints, and FK cases
  in that file passed.
- The full paired 116-to-11 schema release has not been rerun on this frozen
  actual database copy. The Business-only check does not prove that integrated
  release or final 10+1 table count.
- Production, main, push, commit, and deployment are unchanged.
