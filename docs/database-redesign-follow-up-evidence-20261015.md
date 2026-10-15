# Redesign follow-up evidence, 2026-10-15

These are bounded read-only investigations within the accepted ten-core-table
plus one-ledger goal. They do not select new DDL, delete history or bodies,
verify external sources, or change the existing retention and runtime rules.

## Scheduled-run indexes

`app/run_storage.py` currently defines two nonunique scheduled indexes over
every `scheduled_task_runs` record: dispatch keys are dispatch status, lease
expiry, scheduled time and public integer ID; claim keys are dispatch status,
scheduled time and public integer ID. `app/dispatcher/adapters.py` metrics and
claim SQL explicitly filter `dispatch_status='pending'`. Store fallback claim
does likewise and orders by scheduled time then public ID.

Root opened the existing immutable Business/Email source image read-only with
query_only, queried grouped counts and closed it. This is the frozen input
captured at15:35UTC, not current production: dispatched152789, failed1016,
pending2, skipped15427; total169234. The prior same-input capacity measurement
allocated9420800 and9072640 bytes to these two indexes. These are occupied
bytes, not proven savings.

A candidate refinement is a pending-only predicate on both indexes, possibly
omitting their now-constant leading status key. No historical row is retired.
The claim key must preserve scheduled-time/public-ID order. This remains an
unimplemented candidate: compare result identity, EXPLAIN and latency on a
realistic isolated copy for tied times, active/expired leases, dispatcher
metrics, terminal History and failure streaks before accepting it. Wide failed
History could lose a useful status access path. Keep the separate full per-task
latest index: terminal history, newest-run and overlap queries depend on it.
The original Phase4C deliberately retained all five scheduled indexes, so this
is a new index refinement, not an already accepted or measured space recovery.

## Historical minutes source reconstruction

The existing native-source recovery receipt records188 Business source
documents:30 verified full-body native matches and158 still unproved. Of the
unproved set,137 are `ai_minutes`; this investigation did not change those
counts or establish that any original is permanently unavailable.

Read-only source/metadata inspection found that all154 `ai_minutes` bodies in
the frozen input are canonical JSON containing a meeting list row and raw TODO
payload, optionally transcript excerpts and meeting summary. The producer is
`app/task_scanners.py` (`_canonical_minutes_todos_payload`, scan path), with
owner backfill revisions in `app/minutes_owner_backfill.py` and git history.
The source-reference suffix usually hashes only the action list, not the whole
body; `source_document_key` in `app/task_source_documents.py` additionally
hashes source metadata and body. Neither digest is itself an external locator.

Among137 unproved minutes documents,108 match the scanner action-list digest,
five match owner-backfill revision4, and historical producer formulas explain
another20 (four revision1, three revision2, thirteen revision3). Four suffixes
remain unexplained by the inspected formulas. These are producer relationships
computed against stored inputs, not successful original-source retrieval.

The bounded external verification recipe is to read the original meeting UUID's
exact list row, raw TODO response, complete transcript pages and raw summary;
use the applicable producer/excerpt/summary formatter; then compare full UTF-8
body hash and bytes and recomputed identity metadata against the frozen source
receipt. Current `DwsClient` wrappers expose these reads but no historical
version selector. A matching current read would prove reconstruction at that
read; a mismatch or missing source must stay explicit. Future missing or
changed source content must remain unavailable, never silently substitute a
different version. The approved contract requires exact validated provenance;
this investigation does not add a new perpetual availability guarantee.

The local Markdown minutes archive is a rendered summary/transcript, omits the
raw TODO/list inputs, and can be rewritten by later sync. It is not proof of
the exact historical input. The frozen Business database is the stored copy
being evaluated, not independent original-source evidence. No provider read,
body retirement, raw-body export, native file copy or source rewrite occurred.

## Provider and CI boundaries

Provider current integrationb13a01d0 has fresh SPEC acceptance; QUALITY is a
separate pending review. Root independently passed33 focused source/current/
pure-migration checks in2.37s. The synthetic pair target is still unpublished;
actual121-to104 source conversion and genuine-old-code/native acceptance are
not established by these checks.

Separate Business/Email CI38769762524 at ancestorac60db6d failed28 tests and
nine setup cases, with14361 passing. Its exact-head result is recorded in the
Business worktree validation appendix atbf0f7ea7. Read-only triage identifies
fixture index-creation order, native JSON-BLOB acceptance parity, missing test
transaction, retired-table assertions, negative replay fixture shape and Linux
temporary-directory paths. Fixes and replacement CI remain pending. No current
Provider code is covered by that ancestor CI result.
