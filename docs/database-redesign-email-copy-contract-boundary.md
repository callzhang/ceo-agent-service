# Email copy proof: native SQL and strict old application contracts

This is a verification-plan clarification only. No source or target field,
constraint, validation rule, retained data, task lifecycle, provider action or
production behavior changes. Execution of the split proof remains pending
sequential independent SPEC then QUALITY review.

## Expected versus observed

The original Task3B combined two requirements in one database: preserve two
SQL-legal plans whose action_plan_id is NULL, then cold-open the resulting
inverse with genuine old default EmailStore and execute public writes.

The unchanged published9cd41bcf5ea43a275eeb1376817d37f7cef6bd21 already rejects
that same state before any migration. The implementer independently created a
fresh genuine-old-code AutoReplyStore/EmailStore source, used public
persist_scan_result and apply_human_classification to create a valid immutable
plan, inserted two native SQL-legal NULL action_plan_id rows at distinct
versions2/3, and reopened in a fresh archived-old-code process. Without any
forward or inverse it exited1 with EmailPersistenceCorruption:
`invalid immutable ActionPlan None`. Pydantic rejected a non-string plan ID.
The separate inverse-derived case produced the same rejection. Owned temporary
code/databases were cleaned; no provider or production write occurred.

Root verified the exact current source chain: EmailStore.__init__ defaults
validate_rows=True; _validate_durable_state calls _validate_durable_rows;
the latter selects every email_action_plans row and calls _stored_action_plan,
which constructs EmailActionPlan and translates validation errors into
EmailPersistenceCorruption. Production's separately readonly aggregate at the
investigation point was9857 plans with0 NULL action_plan_id and0 reply receipts.
These are bounded observations, not a guarantee that every other source row
satisfies every application rule; strict default validation must still run.

The earlier source oracle correctly describes native SQLite semantics. It did
not claim every SQLite-legal state is accepted by the old application. Removing
NULL rows or opening old EmailStore with validate_rows=False would conceal this
contract difference and would not satisfy the intended application proof.

## Required separate cases

1. **SQL-boundary preservation:** retain both NULL plans, independent signed
   rowids, versions, all144 source values and storage classes, existing keys,
   receipts and allocator waters through forward/inverse/re-forward. Compare
   all actual original facts and exact synthetic deltas. Record the unmigrated
   old constructor's rejection separately as a baseline fact, never as old
   application acceptance. No NULL row may be removed, filtered or rewritten.
2. **Strict application compatibility:** use a separate full latest actual
   source and genuine-old-code fresh source. Preserve every original row and
   seed fixtures only through valid public application factories/contracts.
   Perform current-fact changes allowed by the application, inverse, genuine
   archived old default constructors, meaningful public read/write with fake
   provider results, and re-forward. Include feedback replay, receipt enrichment,
   attempt/step sequence continuation, explicit retry, History/source identity,
   failed visibility and exact unrelated-fact comparisons. Existing source
   corruption, if encountered, stops this gate; it is not sanitized or excluded.

Both must pass their own explicit assertions. A source-only legal edge case
cannot satisfy the old application gate, and a public-API case cannot replace
lossless preservation of SQL boundary facts. No constructor/caller integration,
release marker, target version or production activation is authorized by this
clarification or by either case alone. It does not establish native CLI, UI,
provider outcome or performance acceptance.
