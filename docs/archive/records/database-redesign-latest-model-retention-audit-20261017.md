# Latest-model and training retention: reconciled source audit

Read-only source evidence plus the bounded live filesystem inventory below.
No caller/component files or data were changed.

## Reconciled approved policy

The first-pass literal active-plus-latest reading below was incomplete. The
existing approved policy in docs/storage-slimming-plan.md:3-6 explicitly keeps
**current and previous runnable model plus latest candidate under evaluation**,
with one latest baseline per distinct training dataset type. The completed
storage-slimming claim at docs/agent-claims.md:1228 records the same approval.
The later redesign specification prohibits historical weights/training corpora
in DB; it does not explicitly cancel that concrete previous-model exception.
Do not introduce a new model-fallback removal unit based solely on the shorter
phrase current/latest. Three observed embedding artifacts are exactly current,
previous-online and latest readable candidate, matching the established rule.
They are outside SQLite; their118342536 bytes do not explain DB table size.
No retention policy change or artifact deletion is selected by this audit.
Live readback and actual source policy reconcile; broader training/lifecycle
acceptance still requires the final integrated-release checks.



The selected EmailStore.prune_training_snapshot_data keeps the latest frozen
publication per actual snapshot_version after training becomes idle. It queries
Configs projections, then calls email_training_data.prune_snapshots to remove
older external datasets/pins (app/email_store.py:14566-14584;
app/email_training_data.py:140-151). This preserves different current training
formats rather than calling all formats one latest publication. External dataset
content is not a database copy. Verify actual format membership and current
training dependencies before final latest-only acceptance.

The live cleanup entry is EmailClassifierLearningService._cleanup_storage:
called by poll_retrain after _poll_retrain, it supplies training_active to both
training-data and registry pruning (app/email_classifier_learning.py:537-548).
This is actual reachable cleanup, not an unused helper.

EmailModelRegistry.prune_artifacts skips deletion during active training and
protects active.json, previous.json, the online-control model, one previous
online transition target, and the newest readable candidate. Therefore the
source does not establish a literal active-plus-latest-only artifact rule
(app/email_model_registry.py:1072-1145). Some IDs may coincide; a live inventory
is needed to measure actual retained versions/bytes. No current violation count
or deletion authorization is inferred from the number of references.

Previous models are used by real runtime_fallback and consistency restoration,
not only history display (app/email_model_registry.py:911-948,979-1013).
Tests explicitly exercise previous artifact preservation and fallback
(tests/test_email_model_registry.py:731-770,806-899). The approved concrete current/previous/latest policy reconciles those actual
dependencies; this audit does not authorize a separate retention-policy change. Do not silently delete a referenced
artifact while leaving a runnable manifest or cancellation/rollback control
pointing to it, or add a new fallback policy as an incidental storage change.
Keep metadata/business outcomes distinct from historical weight files and
training datasets. Existing model history is not proof that old weights belong
inside Configs or the database.

The initial hypothesis of an unimplemented latest-only rule is not supported
by the prior approved policy and actual readback. The current retention mapping
is current/previous/latest-readable-candidate; this audit adds no product field,
cleanup behavior or new implementation gate and blocks no Email caller tests.

## Bounded actual registry readback

The configured worker root is DB-parent/email-models (email_worker.py:3346).
A direct read/stat inventory of that root, without constructing the registry
or touching its locks, found no sparse artifacts/metadata/active/previous
manifest, three embedding weight files totaling118342536 bytes,93 staged
evidence files totaling10329401 bytes, and110 run files totaling20962502
bytes. These are external files, not SQLite table occupancy. The current online
model weighs54726153 bytes, the latest other retained candidate file54710702
bytes, and the previous-online target8905681 bytes. No file was changed or
deleted. The timestamp/status readback below establishes observed bindings
only; no retention rule is silently changed based on this inventory.

```json
{
  "observed_at_utc": "2026-10-18T01:22:46.320254+00:00",
  "registry_constructor_used": false,
  "staged_status_counts": {
    "candidate": 93
  },
  "retained_weight_bindings": [
    {
      "model_id": "email-embedding-mlp-20260919T113005Z-79cd24bf",
      "status": "candidate",
      "trained_at": "2026-09-19T11:30:05.262573+00:00",
      "model_family": "embedding-mlp"
    },
    {
      "model_id": "email-embedding-mlp-20261001T223541Z-a0cea8b5",
      "status": "candidate",
      "trained_at": "2026-10-01T22:35:41.415441+00:00",
      "model_family": "embedding-mlp"
    },
    {
      "model_id": "email-embedding-mlp-20260930T064710Z-fab1a0e5",
      "status": "candidate",
      "trained_at": "2026-09-30T06:47:10.671409+00:00",
      "model_family": "embedding-mlp"
    }
  ],
  "active_training_run_present": false
}
```

## Actual latest training datasets

Read-only native Email51 metadata and external file inspection; no registry or
EmailStore constructor, no cleanup call and no raw training text output. This
checks each retained filename digest against the latest frozen publication per
actual snapshot_version. It does not substitute for the schema-specific
cryptographic content validation in the existing read_snapshot lifecycle tests.

```json
{
  "observed_at_utc": "2026-10-18T01:41:00.158234+00:00",
  "external_dataset_files": 2,
  "external_bytes": 26507412,
  "active_pin_files": 0,
  "dataset_version_counts": {
    "email-selected-training-snapshot-v1": 1,
    "email-folder-training-snapshot-v1": 1
  },
  "all_files_are_latest_published": true,
  "latest_publication_versions": 2,
  "training_observation_table_exists": true,
  "files": [
    {
      "bytes": 16858401,
      "snapshot_version": "email-selected-training-snapshot-v1",
      "latest_published_digest": true,
      "publication_version_matches": true
    },
    {
      "bytes": 9649011,
      "snapshot_version": "email-folder-training-snapshot-v1",
      "latest_published_digest": true,
      "publication_version_matches": true
    }
  ],
  "read_only": true
}
```

The native legacy observation table exists but contains **0 rows** at this
readback. Its empty declaration is a remaining source-schema object, not a
retained training corpus; final Configs conversion removes that source table.

The existing read_snapshot reader then successfully validated both retained
files in memory (typed rows, manifest and complete snapshot digest). No new
constructor, file, pin or data write was used:

```json
[
  {
    "snapshot_version": "email-selected-training-snapshot-v1",
    "observations": 4423,
    "digest_and_schema_verified": true
  },
  {
    "snapshot_version": "email-folder-training-snapshot-v1",
    "observations": 2398,
    "digest_and_schema_verified": true
  }
]
```


Read-only refresh at 2026-10-18T13:25:43Z reconfirmed the same three exact
model bindings and byte sizes. Every artifact digest matches its staged evidence.
Both external datasets remain latest for their actual snapshot type and pass
existing read_snapshot complete-content/schema/digest validation; no active
training or pins. No cleanup was necessary or performed. Current metadata lives
in the single capacity evidence JSON, latest_external_retention_readback; this
is external retention evidence, not completion of DB model-input retirement.
