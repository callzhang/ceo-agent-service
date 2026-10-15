# Remaining Email cache retirement: actual caller findings

Read-only source review at6056cddc6fe0c11ab631e2d80b7e8df71a2498f9, using
only source code and the committed264 per-table aggregate. That initial review
made no provider availability check, field-byte measurement, mutation or policy
change. Subsequent owned-source byte measurements are recorded below; provider
availability remains unverified.

The two actual text caches are email_messages.normalized_text (readable body
or legacy synthesized header/body) and email_classifications.model_text
(redacted/tokenized legacy feedback features, body truncated2048characters).
They are not interchangeable canonical embedding input. Both tables have8919rows
and occupy35.5MB/39.8MB including indexes; those are whole-table costs and cannot
be promised as recoverable field savings.

Current inference creates canonical_model_input from a freshly fetched provider
message (email_classifier_scan.py:503). The primary-model writer nevertheless
recomputes and stores legacy model_text and separately persists readable body
(email_worker.py:294-308; email_store.py:8446,8754).

Necessary current caller conversions before retirement:

| Cache | Actual readers | Required direction under approved provenance rule |
| --- | --- | --- |
| model_text | list_training_examples14342; owner_labelled_examples13748 and worker850; inclusion digests15866 | Retain labels/model attribution/inclusion facts, load ref-resolved selected input transiently and compute needed features; do not blank current cache silently. |
| normalized_text | Console list13306, search13447, detail13699; selected training14386 and learning119; thread13573/context_source50; recovery worker2249 | Fetch recoverable bodies through retained provider locators for display, selected-training and thread/recovery; keep necessary active input until its actual consumer completes. |

Current selected training reconstructs canonical input from message body,
recipients, headers and attachment metadata. Legacy model_text is explicitly
not that canonical input. Updating model_text currently invalidates model
inclusion through an existing trigger; retirement must move that contract rather
than falsely preserving inclusion. A processed classification can still have
pending mailbox actions, so processed alone is not settled-input evidence.

Both tables retain account/message identity, locator and display metadata.
Consolidation should keep the single existing Email business/provenance identity,
classification decisions/confidence/human correction/model and config attribution,
Agent result, current action-plan link and necessary timestamps. Provider-derived
locator/display fields are not independent business results, but byte equality
and historical recoverability are not established by code inspection. Current
locator equality validation and move writers touch both tables (store7482,17665),
so those calls must migrate together. No new generic source/relation ledger.

Historical prototype backfill populated normalized_text from model_text and empty
recipient/attachment lists (store6853). A provider fetch may be a better original
but cannot be assumed to reproduce that exact old snapshot. Missing originals
and content used by active work require explicit handling from real evidence,
not an invented fallback or a blanket cache deletion.

Existing completed-classification-input compaction keeps provider_locator, stable
identity, config and checksums (email_task_adapter.py:614-655) but does not retire
either cache above. That gap is specific remaining phase6 work, not evidence
that the previously agreed rule has fully reached all storage paths.

Concrete implementation order: convert legacy model-text consumers; convert
provider-backed body consumers and selected training; then combine the two Email
records with direct action/source references and remove duplicate physical fields.
Retain business labels/results/state/receipts throughout. No size savings, source
recoverability or complete field map is claimed by this bounded review.

## Subsequent owned-source measurements

At the same immutable264source, normalized_text totals24416053UTF-8 bytes
(8795nonempty of8919rows); model_text totals13771112bytes. The combined text
value total is38187165bytes. Exact groups, Agent result bytes and table page
metadata are in2026-10-15-database-redesign-email-cache-bytes.json. These do
not establish provider recoverability, per-row retirement eligibility or physical
savings. Both cache fields still need their actual caller conversions before
retirement under the approved provenance rule.
