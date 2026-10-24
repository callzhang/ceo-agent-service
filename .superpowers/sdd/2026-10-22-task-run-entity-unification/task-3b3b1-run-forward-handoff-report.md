# Task 3B3B1: portable Run identity handoff in forward migration

Status: bounded forward implementation complete. This does not complete B3; the
B3B2 inverse handoff producer, native new-Run allocation, all-new-fact inverse,
full composed acceptance, runtime cutover, and release remain outside this slice.

## API frozen for B3B2

The forward signature is now:

```python
migrate_run_entities(db, source_metadata, *, task_metadata, run_handoff) -> dict
```

`task_metadata` must be the actual Task2B forward result in this same database
and transaction. `run_handoff` is JSON-serializable with exactly these keys:

```json
{
  "version": 1,
  "source_context": "<64 lowercase hex characters>",
  "identity_edges": [
    {"source_kind": "agent_runs", "source_physical_id": 1001, "canonical_id": 9101}
  ],
  "owner_highwaters": {
    "runs": 8000,
    "runtime_attempts": null,
    "run_failures": null,
    "schedule_occurrences": null,
    "workbench_events": null,
    "codex_session_search_index": null,
    "email_classifier_runtime_samples": null,
    "setup_wizard_events": null
  },
  "digest": "<64 lowercase hex characters>"
}
```

Only changed identities appear in `identity_edges`; an edge whose canonical ID
equals its source physical ID is rejected. An omitted source row defaults to its
source physical ID. Edges must be unique by `(source_kind,
source_physical_id)`, point to a current selected-source row, use a known source
kind, and produce unique canonical IDs within each native owner. Canonical IDs
may coincide across different owner tables. B3B2 should emit changed edges only,
sorted by `(source_kind, source_physical_id)`. `owner_highwaters` always has the
eight exact owner keys. A JSON `null` means that the native owner had no
`sqlite_sequence` row at inverse capture; it is distinct from numeric zero.

`run_handoff_context(source_metadata)` is SHA-256 over UTF-8 JSON for exactly
the selected `source_objects` DDL map, using sorted object keys, compact
separators, and `ensure_ascii=False`. It intentionally excludes source rows,
sequence values, and FK findings because legitimate old-source writes after
inverse change those facts; each forward instead validates them from its fresh
`source_metadata` capture. JSON array order is retained.
`run_handoff_digest(source_context, identity_edges, owner_highwaters)` is SHA-256
over the same canonical JSON settings applied to an object containing `version`,
`source_context`, `identity_edges`, and `owner_highwaters`. The producer must use
the captured Run metadata and exact edge/high-water payload it supplies to
forward. The forward rechecks source-context and digest before replacing `runs`.

The real owner names are the `owner_highwaters` keys above. `run_provenance` and
`run_projections` are plain-ROWID children, not AUTOINCREMENT owners and get no
synthetic counter. The existing source sequence rows, including global,
published, and per-kind `runs:*` rows with original rowids and values, are
replayed exactly after target rows and native owner highwaters are established.

## Identity transforms

Before replacing the source table, forward builds complete typed owner maps for
all source rows. Sparse canonical edges apply to the physical ID in each source
namespace. The map then transforms logical Run IDs and parent revisions, professional provenance and
projection `run_id`, runtime attempt ID and physical Run `parent_id`, failure
`run_id`, and the Run-bearing public reference prefixes. In particular,
`agent_run` workload keys use canonical physical Run IDs; professional `task`
workload prefixes retain the original SQLite-cast public key namespace and map
that key to its canonical Run ID. Reply schedule IDs and Workbench event Task
IDs continue through the actual Task2B typed identity mapping. Meeting session
search `source_id` maps through its source public key to the canonical Run ID.
Opaque keys and unrelated Task/business/provider IDs are unchanged. A captured
missing Task FK remains the same missing Task integer on the corresponding
canonical Run; no Task is invented.

The existing `run_entity_identity_map` stores original source kind, source
physical ID, raw public key and type/order separately from canonical `target_id`.
The selected `logical_run_current_schema` declaration and the source SQLite
`CAST(legacy_key AS INTEGER)` lookup namespace remain authoritative.

Each native owner sequence is restored to at least the greater of its captured
native high-water and its maximum canonical row ID. This preserves an allocated
but subsequently deleted ID when an owner is empty. `null` means no native
sequence row existed; numeric zero preserves an actual row at zero. The source
`runs` sequence is a separate captured fact.
Because SQLite uses the same `runs` sequence name for the old global allocator
and the native `runs` owner, that row is retained at the maximum of source global
water, native captured water, and canonical row maximum, with its source ROWID.
Other source sequence rows, including published/per-kind `runs:*` and unrelated
rows, retain their exact source ROWID/name/value. The original source values remain
in `source_metadata` for inverse reconstruction.

## Verification

- All-eight-source forward fixture: source physical, public, and canonical IDs
  differ; verifies shared integers across two different native owners and checks
  a remapped Consumer/Audit parent, professional provenance/projection, runtime
  parent and both
  workload identity domains, failure, schedule reply Task, Workbench Task, and
  Meeting search references.
- Rejection fixture covers missing handoff, mismatched source DDL context, wrong digest, unknown source kind,
  duplicate source edge, per-owner canonical collision, and dangling source edge;
  each asserts the original source `runs` catalog and rows remain intact. The captured missing-Task
FK fixture also remaps its Run from 1400 to 9140 and verifies the resulting FK finding.
- A genuine old-source insert after handoff construction, followed by a fresh
  capture, keeps the old canonical edge and gives the new source row its physical
  ID as canonical identity.
- Empty target setup-event owner with captured native high-water 12000 remains
  empty at captured high-waters 0 and 12000; next native inserts allocate 1 and 12001.
- Existing Task2B/Run tests plus Run tests: **53 passed** in 53.84 seconds.

This slice simulates the handoff fixture directly. B3B2 must produce it from an
actual current-native inverse and prove JSON round-trip followed by a fresh
context forward conversion; that producer proof is not claimed here.
