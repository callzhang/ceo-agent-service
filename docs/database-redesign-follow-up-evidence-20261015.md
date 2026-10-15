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

Provider current integrationb13a01d0 has fresh sequential SPEC and QUALITY
acceptance. Root independently passed33 focused source/current/
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


## Native canonical minutes verification follow-up

The metadata-only receipt
`superpowers/specs/2026-10-15-business-minutes-canonical-source-probe.json`
records a real native DWS read of all137 previously unproved minutes sources,
followed by five narrowly scoped duplicate-row/historical-formatter checks.
The frozen input is SHA256
`970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`;
its hash and the four executed application modules were unchanged by the
first pass. The follow-up again confirms the source hash unchanged. The older
native recovery receipt used a different database snapshot; all188 document
IDs, full-body hashes and UTF-8 byte lengths were compared equal between the
snapshots before combining document-content evidence. This is not a claim
that the two whole databases are identical.

The first pass verified126 exact canonical bodies (4600819 UTF-8 bytes).
An exact duplicate list row for one source and genuine historical excerpt
formatters for three sources explain four additional exact reconstructions.
The combined strict result is130 minutes sources and4637606 UTF-8 bytes;
source document IDs12,31,46,49,50,51,52 remain unproved under the receipt's
current checks. IDs49,50,52 already match the whole body and recomputed stored
signal identity, but their legacy reference suffix is unexplained by the
inspected producer formulas. That narrower fact is preserved rather than
calling their content mismatched or inventing a producer formula.

Bodies were reconstructed from native exact-UUID list/TODO/transcription/
summary reads, with only metadata retained in the receipt. Stored components
were not reused in the reconstruction. Stored signal metadata supplies the
identity comparison; human authorship was not independently reverified.
Two list responses contained repeated identical rows; follow-up records the
complete-row equality before selecting that one distinct row. The native
profile inventory contained one current profile/current organization. A
comparison between differently typed account/organization identifiers was
not used as an authorization or source-identity conclusion.

These reads establish exact source reconstruction at the time of the probe,
not perpetual provider availability. Missing or changed sources must remain
explicit. No body has been retired, no production database has been written,
and no external send, source edit, permission change or historical-version
revert was performed. Historical weekly-document verification is a separate
in-progress probe and is not counted as completed here.

## Harness and replacement CI follow-up

Business CI fixture closure is accepted locally through887cd377 and pushed;
replacement run38785796782 is still in progress at this recording. The prior
failed run remains a failed result. Provider source-copy harness commits
6cdad6f5 and4d3f6927 have fresh SPEC acceptance, including later unchanged
observation/generation behavior and exact whitespace-bearing typed values.
Independent QUALITY review and actual copied-database execution are pending.
No production deployment or final eleven-table acceptance follows from these
intermediate checks.


## Native weekly historical-version investigation

The separate metadata-only weekly historical source receipt records15 remaining
source rows (373943 UTF-8 body bytes,177 attached signals) across five known
DingTalk nodes. Native readonly version metadata lists103 versions with all
metadata pagination complete. Of those,97 unique versions had exact requested/
returned version binding. None matched an expected frozen Markdown hash, so
no remaining source row is accepted as an exact provider-original recovery.
This is a bounded negative search, not proof of permanent unavailability:
three listed versions were unattempted and three otherwise successful replies
lacked the required historical-version binding.

Actual historical body reads were101, exceeding the100-call bound byone.
An exploratory fetch was omitted from the running counter; the researcher
corrected the count and stopped all provider reads. Source SHA256 before and
after remained970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320.
The receipt records executed module hashes and coverage rather than raw bodies.
No source edit, version revert, permissions change, send, database write or
body retirement occurred. Current-source matches for four other weekly rows
are separate root observations and are not counted as historical recovery of
these fifteen. They cannot justify deleting the unproved originals.


## Provenance retirement boundary, read-only contract review

The approved design distinguishes a confirmed original locator from proof that
its historical bytes can still be retrieved. The15 remaining weekly rows have
confirmed node identity and old input hashes/lengths;97 exactly bound version
reads failed to match those bytes in the bounded search. That is not an invalid
node, permanent source loss or permission to substitute current content. The
three minutes rows49/50/52 already have exact whole-body and stored identity
matches; only their legacy suffix formula remains unexplained. The strict
receipt counts are retained until that reference interpretation is resolved.

Design lines75–99 permit provenance identity/version and require accurate
source reading. Line132 says completed recoverable bodies retire, unavailable
sources retain explicit gaps and existing adopted results/actual locators,
and failed source checks are separately listed in rehearsal rather than
silently deleting production rows. It does not impose perpetual provider
availability. The unresolved case is whether confirmed locator plus historical
input hash/bytes and an honest version-unavailable read satisfies that rehearsal
check; this review authorizes neither deletion nor blanket indefinite body
retention. The final concrete rehearsal must list these rows and its proposed
handling for final approval. No production change has occurred.

Current body consumers still include Store business signal reads, task source
bundle rendering, Task citation validation and Project citation validation.
Their exact-source/unavailable replacements are required before retiring the
source body table. Native trajectory missing/changed errors demonstrate honest
unavailability but do not prove provider history retrieval. These consumers
and adopted quote/result evidence must not be conflated with expendable input
copies. This contract review made no further provider reads; the prior101
historical fetch accounting correction and stopped-call boundary remain.
