# Current12 Email: two observed runtime regressions

Scoped functional repair after test-only closure review, not a new audit,
authorization, confirmation, schema-repair or safety-policy feature. Preserve
existing assertions. Evidence: focused session62687,47passed/4failed834.21s;
fixture-only future-version failure fixed and bothvariants passed23761.
Remaining three actual failures cover two mechanisms. No production changes.

## 1. Lightweight terminal unsubscribe scan

Expected: before identifying a qualifying terminal task, the scan needs only
classification identity/account/stableidentity/currentplan/status and plan
version/actions. It must not access subject/preview/modeltext/agentresult.
Actual: first scan at app/email_store.py:list_terminal_unsubscribe_tasks_missing_receipts
uses EMAIL_CLASSIFICATION_SQL, whose subquery names every physical class column.
SQLite authorizer sees those reads during statement preparation even when no
row is returned. Existing authorizer regression therefore denies class_subject.
Later full-classification fetch is correctly conditional on a qualifying
terminal task and must remain conditional.

Repair that FIRST scan to directly select only the needed canonical physical
columns with existing public aliases; retain classification_id-is-not-null
filter, processed-status/current-plan join, unsubscribe action predicate, order,
receipt/cancellation exclusion, terminal done/skipped/needs_human vs failed
behavior and later full hydration. No generic projection framework or broad
query rewrite. Existing empty no-hydration test plus populated valid terminal
and failed-task tests are the focused regression/control set.

## 2. Non-text canonical schema identifier normalization

Expected existing contract: malformed schema identifiers raise
EmailPersistenceCorruption containing schema identifier, before writable
constructor/cache/journal changes. Both old table-name and trigger-name BLOB
regressions already assert that domain error.
Actual: require_current_email_pair runs before _validate_schema_shape;
current_canonical_email_parent_is_current hashes metadata captured by
_source_declaration. BLOB name enters its objects list and json.dumps raises raw
TypeError before the existing _schema_identifier can normalize it.

Repair the current-parent metadata boundary BEFORE hashing to apply the existing
identifier validation to canonical owned schema object names/owner names. Reuse
existing Email domain validator/error; do not catch arbitrary TypeError,
convert BLOBs to text, omit malformed objects, whitelist the corrupted fixture,
change expected declaration hashes or add a schema self-repair path. Existing
email_current_schema functions already lazily import EmailStore constants;
choose the smallest consistent dependency, not a new framework. Inspect actual
metadata representation and original validator rather than blindly following
a proposed code snippet. Keep malformed current declaration class behavior for
missing/extra column/unique and the exact source shape checks unchanged.

## Execution and evidence

Claim only app/email_store.py first scan and app/email_current_schema.py parent
metadata validation plus a NEW runtime-closure validation note. No test assertion
edits, no component/requirements changes, no schema version bump or migration
semantics. Use the exact three already observed RED cases from62687 as the unchanged
baseline (do not repeat them merely to recreate the same failure), then exact GREEN and
minimal existing terminal/constructor positive controls; no full suite or repeat
of unaffected47passed cases absent new evidence. Managed temporary SQLite files
only, bytecode/cache disabled, focused -n3 allowed, no source/production DB,
provider/nativeCLI/network/outbound calls. Record structured handles, poll same
handles, cleanup disposable artifacts. Root freezes changed files/runtime after
sole-author completion, then fresh SPEC and fresh QUALITY; a fresh full actual
copy remains required after approval. No commit/push/deploy in this bounded unit.

## Evidence-driven scope extension: one control fixture

Runtime author's controls97046 exited with4passed/5failed132.54s. Four failures
are the terminal-status parameter cases, before calling the repaired method:
unchanged test setup executes `update tasks set status=?`, but Tasks20 has no
physical status column. Root authorizes same author to translate ONLY that
setup in test_terminal_unsubscribe_probe_hydrates_only_valid_terminal_tasks
using existing finite task8_set_parts plus minimumlocalimport; all status values
and business/hydration assertions remain unchanged. Reuse97046 as RED, then run
exact4variants GREEN. This one fixture hunk extends the2app+newvalidation unit;
the prior95ebb88 test freeze remains historical and its assertions are retained.
Fresh SPEC and QUALITY review the additional test hunk and new finalfilehash.

The fifth failure is in old _rewrite_required_identifier_case before startup:
it expects17native tables, but selectedcurrent has0ofthatset. It is an
out-of-scope historical declaration-fixture adaptation, not evidence that the
repaired code rejects a well-formed current schema. Do not weaken or rewrite
that helper/test in this unit. Run the valid current
initialization_preserves_delete_journal_mode[native-trace] positive instead and
retain the oldfixture failure honestly for final test-disposition closure.

The selected native-trace positive exposed one more stale TEST wrapper: it
monkeypatches _open_connection without the current keyword-only foreign_keys
argument. Root authorizes this second exact test-function fixture translation:
mirror and forward the actual keyword to the original connection method,
preserving every trace/journal/schema/no-write assertion. Run both existing
parameter cases after the observed RED; do not search a third positive or change
the runtime API. Final scope is2apphunks+2testfunction fixtures+newvalidation.
Stop broadening after the specified regressions and these finite controls.

## Final same-function metadata coverage translation

Both cold trace cases44197 reached journal/schema/no-write checks, which passed,
then failed the old requirement that EVERY historical read-pragma allowlist
prefix appear. Their actual traces were not retained; no missing-prefix counts
are inferred. Source diagnosis: current _source_declaration unconditionally
reads main.table_xinfo/main.foreign_key_list/main.index_list/main.index_xinfo;
_validate_schema_shape uses table_list; historical unqualified index_info/
index_xinfo reads are conditional on original table/index loops now retired.

Root authorizes this same test-function contract translation: retain complete
read-pragma ALLOWLIST and all no-write/journal/schema assertions; require separate
current mandatory coverage for table_list and the4main introspection prefixes,
with missing-prefix diagnostics. This preserves selected target metadata
coverage rather than demanding execution of retired reader branches. Reuse44197
as RED, run both parameters once after this change. No broader helper rewrite
or app change; fresh independent SPEC/QUALITY must verify the interpretation.
