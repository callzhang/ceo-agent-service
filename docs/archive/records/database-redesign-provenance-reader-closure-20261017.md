# Provenance reader closure before the final schema unit

## Current exact-body criterion adjudication

Independent source review approved the narrow body-availability criterion for
49/50/52: exact original UUID, independently rebuilt complete matching bytes/hash,
and full retained Signal identity establish body availability at the recorded
read time. Their unmatched todos suffix is queue/action-set identity, not an
immutable Provider version or full-body digest; full suffixes and unknown
producer lineage remain. No historical authorship or future availability claim.
New receipt `superpowers/specs/2026-10-17-provenance-exact-body-criterion-review.json`
records the source-only review and binds the original current-read receipt,
which remains unchanged with proof_promotion=false/body_retirement=false.
This adjudication itself calls no provider and opens no database.

At the reconciled220 snapshot only, the former23 strict gaps become20/489023B
for body availability after reclassifying these3/93763B. This is not a fresh
whole-database count, not200 currently reread originals:130 historical Provider
proofs still need reader/availability refresh. Runtime reader/model/transaction
closure and body-retirement lifecycle remain unimplemented. Older statements
below requiring suffix producer reconstruction before ANY body availability
promotion are superseded only for these three exact matching bodies. No waiver
applies to the other20 unknown bodies, document51, incomplete responses, changed
body bytes, missing exact locator or mismatched Signal identity.


This is bounded read-only analysis of existing Business source consumers while
the current12 Email caller unit is still under implementation. It selects no
new DDL, field, runtime reader, cancellation or data-retirement policy. Production
was not accessed for this analysis. Paths and line numbers refer to the isolated
worktree at this analysis boundary and are navigation, not immutable source pins.

## Actual body storage and read contract

`AutoReplyStore._business_source_document_id` computes the existing
`source_document_key` from source type/ref/time, conversation, actor and full
`evidence_text`, then writes `business_source_documents(identity_key,body)`.
The key is a hash of the complete identity array, not a standalone body digest.
A source URL or this key alone does not establish current historical availability.

Six current Store access methods expose `document.body as evidence_text`:
lookup by signal dedupe key (4034), transactional task/source-ref lookup (4211)
and its public wrapper, list signals (4355), public get signal (4364), and
transactional get signal (4368). Two public wrappers delegate; four leaf
methods contain the actual current SQL body joins. The older source-document migration
around6990 is a historical source migration, not a current reader to convert by
global substitution. `BusinessTaskSignal` at task_semantic_models.py164 currently
requires nonblank evidence_text and an integer source_document_id. Simply
removing body or returning empty text would break this existing contract.

## Consumer meaning, not just SQL names

| Existing consumer | Actual requirement | Consequence for the provenance unit |
|---|---|---|
| TaskSemanticService._require_same_signal (232), _signal_id_or_create and _replay_result | Compare an incoming source with the existing dedupe record | The caller already has the incoming transient body. Compare exact version/identity from it; this path need not fetch an old body merely to return an existing ID. Preserve the existing mismatch behavior. |
| todo_completion.complete_business_task_from_external_todo (30) | Existing dedupe lookup is used for signal.id; new completion already carries its source evidence | Do not add provider/native I/O just to read an ID inside its writer transaction. |
| TaskSemanticService suggestion citations (527), referenced acceptance (952), TaskAgent historical project citation (1415) | Validate a quotation against a particular observed original source | Accurate source readback is still required. Preserve the existing quote/source-ref checks; a URL alone cannot substitute for the checked text. |
| ProjectContextService._require_signal/apply (46–76) | Load source kind/ref/body and check exact citations inside the caller's transaction | Move external/native readback outside the writer transaction, then use the exact immutable source binding during existing transactional mutation. Do not perform provider calls while the writer lock is held. |
| task_retrieval._source_backed_owner_signal_id (115) and semantic context assembly (352) | Verify owner excerpts and assemble source-backed Agent context | Unavailable historical text must not be represented as a successful quote match or invented text. Retain the adopted owner/business results and accurate source identity separately. |
| task_source_documents.source_bundle (144 onward) | Group source versions, show bounded visible ranges and pinned excerpts; current source_document_id groups multiple signals | Maintain real sharing/grouping identity, exact supplied content/ranges when readable, and honest unavailability when not readable. Avoid repeating one provider fetch for every signal sharing a version. |
| web_api/tasks.py attention/task/project detail; registration.py signal detail (568) | Return source-bearing BusinessTaskSignal models and existing results | Change the model/response path together with storage/readback; do not silently keep a Nonblank field by filling it with a placeholder described as original text. |

SourceSignal used by new semantic commands is already a transient input distinct
from the persisted BusinessTaskSignal model. New commands' incoming evidence and
adopted citation excerpts are not permission to persist another complete original.
Existing small business evidence/citations and execution results remain retained.
The existing signal comparison also includes conversation_title, context_json
and dedupe_key, which are not all part of source_document_key. Replacing its body
comparison with an exact source identity check must preserve those other field
comparisons; a matching document hash alone is not the existing dedupe contract.

## Source availability boundary already proved elsewhere

The latest reconciled220-document observation is historical to this analysis;
it was not rehashed here. The live-source-readback report records62 exact native
versions plus one Console reconstruction and four refreshed current weekly
versions. Another130 older Provider proofs still match the recorded content
identity but need a working current reader and availability refresh. The23
remaining versions/582786 bytes are unproved, not permanently unavailable.
No failed historical-version scan is repeated by this analysis.

The approved design already requires only shared versions in provenance and
unique-owner source identity inline. The next unit must account for a formerly
unique version becoming shared when another actual signal references it; current
fan-out counts alone are not a permanent classification. Source IDs are also
used to group runtime source bundles, so their disposition must be explicit.

Removing the old SourceDocuments table is therefore coupled to the concrete
reader/model/transaction closure above. It is not accomplished by renaming the
table, moving full bodies into an Actions payload, a local archive, or duplicating
originals behind a new cache. Accurate source-unavailable output is a runtime
readback result; it must not fabricate availability or erase retained business
results. The existing design's unproved-body retirement boundary still applies.


### Finite producer-formula follow-up

The recorded body crosscheck found documents49,50,52 identical to previously
verified Provider bodies. This alone does not prove their stored source version.
A read-only follow-up used the actual `_minutes_todo_actions` and
`_actions_digest` function ASTs, inspecting historical backfill producers at
f3faae9fe,7f69aca12,3262eaa30,9d799c61b. Those commits establish the initial
`[actions,excerpts]`, revision2/3 `[revision,actions,excerpts]`, and revision4
`[4,actions,excerpts,summary]` formulas. None matched the stored source refs
for these three documents with their complete current canonical payloads.
Bodies and source refs were read only inside a production mode=ro/query_only
transaction; no body was printed, persisted, or fetched externally. No source
availability was promoted and no body was retired. The next investigation must
supply new producer/input-version evidence; do not repeat these same four
formulas or waive the source-ref identity check based on body equality.
