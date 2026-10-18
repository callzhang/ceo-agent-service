# AI Minutes source-ref suffix: bounded provenance finding (2026-10-17)

This is source-code and existing-receipt analysis only. No database or native
file was read, and no provider was queried. Runtime and test files were read
only; none was changed. No source document is promoted to verified and no
body-retirement decision is made.

## What the suffix identifies

`_actions_digest` hashes canonical JSON of its *argument*; the scanner calls it
with the extracted action list, compares it with `todo_digests`, and queues only
when that action digest changes. It separately builds the complete canonical
body from meeting list row, raw Todos response, optional transcript excerpts,
and optional meeting summary. `minutes_work_item` puts the digest into
`<minutes UUID>#todos-sha256=<digest>` and the canonical body into `summary`.
Thus the suffix is an action-set/queue identity, not the hash of the complete
body. The scanner test explicitly shows a changed meeting title and Todos
response request ID leave the queue identity unchanged while a changed action
list yields a new reference. Evidence: `app/task_scanners.py:207-215,218-258,
312-315,343-349,376-395`; `tests/test_task_scanners.py:617-636`.

Owner backfill uses the same Work Item builder but deliberately gives its
requeued item a separate digest. The current formula hashes
`[BACKFILL_REVISION, actions, excerpts, summary]`, then compares the resulting
reference to existing input references. The source-era history shows initial
`[actions, excerpts]`, revision 2/3 `[revision, actions, excerpts]`, and revision
4 `[4, actions, excerpts, summary]`; the already recorded finite test of those
four forms found no match for documents 49, 50, or 52. Evidence:
`app/minutes_owner_backfill.py:39-46,74-118`;
`docs/database-redesign-provenance-reader-closure-20261017.md:72-86` (historical
commits identified there). Their actual producer/input-version cause remains
unknown; a formula mismatch alone does not establish tampering or source loss.

The stored `source_document_key` is a different identity: SHA-256 over the
tuple of source type, **full source_ref**, time, conversation, actor fields, and
complete evidence body. The Store writes the immutable body under that key;
Task Agent's current-source Signal copies Work Item `source_ref` and `summary`
into those fields. Separate Signals can therefore have the same body bytes but
different document keys when their source references or other metadata differ.
Evidence: `app/task_source_documents.py:43-65`;
`app/store.py:4047-4064,4083-4116`;
`app/task_agent.py:1029-1067`.

## What the three receipts establish, and what they do not

The 2026-10-15 receipt reports exact listed provider UUID, complete canonical
body byte count/SHA-256 equality, and recomputed stored Signal identity for
documents 49, 50, and 52, while `matching_ref_formulas` is empty for all three.
Its method explicitly required a known digest formula; it labels them
`body_or_identity_mismatch`. The 2026-10-17 read-only crosscheck further finds
the complete body of 49 equal to already verified 48; 50 equal to verified
43/44/45; and 52 equal to verified 47. These are recorded observations, not a
fresh provider read or proof that one document ID inherits another's distinct
source-reference identity. Evidence:
`docs/superpowers/specs/2026-10-15-business-minutes-canonical-source-probe.json:21-27,1089-1177`;
`docs/superpowers/specs/2026-10-17-provenance-recorded-body-crosscheck.json:41-71,164-169`.

The approved design requires an accurate, locatable original, preserves exact
source versions and Signal identities, and forbids treating identity/hash/byte
length as a substitute for the source. It blocks retirement where source
references cannot be verified. Existing citation readers also require the
Signal's exact `source_ref` and a quote from its body; they do not recompute a
Todos suffix. Evidence:
`docs/superpowers/specs/2026-10-11-database-redesign-design.md:73-77,83-99,130-132`;
`app/task_agent.py:1391-1429`;
`app/task_retrieval.py:100-119`.

**Bounded conclusion.** Recomputing this queue suffix from the *current full
body* is not, by itself, a necessary condition for showing that a complete
original body can be read from an exact provider UUID: it is not a body digest
or a provider version selector. A fresh provider read that independently binds
the exact UUID and reconstructs matching complete bytes could legitimately
refresh *body availability* for these three, with the retained Signal metadata
and `source_document_key` checked exactly. It would not by itself establish
which historical producer attached each unmatched suffix to that body.

The strongest remaining evidence for the latter is an independently retained
source-era Work Item/input or native run with the **same full source_ref and
canonical summary**, linked to the specific Signal/document, or genuine
producer-version evidence explaining that reference. The existing compacted
work-summary input retains a hash/byte count of its serialized Work Item but
replaces the input payload with `{}` on completion, so that metadata alone
cannot recover the binding (`app/store.py:24112-24164`). Whether suffix
reproduction is a product invariant beyond exact source readback, or an
overstrict condition of the 2026-10-15 diagnostic, is a separate decision for
the root. Until that is resolved, retain the current unproved status and the
original bodies; do not reuse the verified documents' coordinates as if their
distinct source references were identical.
