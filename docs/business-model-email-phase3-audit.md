# Email business-model phase: current owners and safe reduction boundary

## Conclusion

The planned Email B1 and B2 restructurings are already in the current code and
their accepted validation records. B1 made `emails` the message-metadata owner
and bounded the preview; B2 moved Email action claims and outcomes into the
shared Action lifecycle. Repeating those migrations would not simplify the
current model further.

The remaining Email records do not all represent the same fact. The code shows
four distinct uses: a canonical message, a classification Task input, a
historical-scan candidate, and bounded runtime timing observations. Their
owners and readers differ, so merging or deleting a whole type based on its
name or row count is not supported by the current evidence.

## Source-backed ownership

| Current owner | Fields/facts it owns | Current use | Disposition |
| --- | --- | --- | --- |
| `emails` | Stable message identity, provider locator, canonical message metadata, bounded preview and current classification link | Email lists, details, source retrieval and classification display | Retain. This is the canonical current message record after B1. The original message is read from its provider when needed; the local preview is bounded. |
| `email_classification_inputs` | Stable message identity, provider locator and the immutable input needed by its `email_classification` Task | Task dedupe, pending/running recovery, provider UID protection and dashboard/task detail; see `app/email_task_adapter.py` | Retain identity and active input. For completed Tasks, use the existing explicit compactor only where its source and exact input identity checks pass. Do not delete the table or pending inputs. |
| `email_historical_candidates` | Historical-scan source locator, candidate content, retry/terminal state and timestamps | History scan/resume and candidate presentation; see `app/email_store.py` | Retain pending, retryable and otherwise referenced rows. A terminal row’s source payload is only a per-row compaction candidate after the source can be re-read and the adopted classification/result is proven independent of that copy. No whole-table deletion is justified. |
| `email_classifier_runtime_samples` (typed records in `runs`) | Model ID, outcome/fallback code, cache/warm flags, stage timings and timestamp | Quality-gate counters and classifier latency/observability; see `app/email_store.py` and `app/quality_gate.py` | Retain within the existing 2,048-row bound. These are content-free runtime measurements, not model training examples. Deleting them would remove recent failure/latency evidence. |

The model input and historical-candidate JSON can contain source-derived text;
they are not equivalent to the native Codex trajectory. They must not be
treated as trajectory duplicates. The current code has separate source
provenance and provider retrieval semantics for Email.

## Measured production footprint

The read-only aggregate inspection in this work session reported:

| Record | Rows | SQLite allocation (table plus indexes) | Meaning |
| --- | ---: | ---: | --- |
| `emails` | 10,657 | 57,835,520 bytes | Canonical message facts and indexes; the total includes structural index cost. |
| `email_classification_inputs` | 1,420 | 1,654,784 bytes | Current Task inputs and their provider identity. |
| `email_classifier_runtime_samples` | 2,048 | 299,008 bytes | Bounded content-free timing observations. |
| `email_historical_candidates` | 924 | 692,224 bytes | Historical scan work and retained candidate source facts. |

### Candidate locator dedup preflight (2026-10-24)

A separate read-only preflight of the configured production database found 924
candidate rows with valid JSON. Each row had all five locator properties
(`accountId`, `folder`, `stableMessageIdentity`, `uidValidity`, `uid`) present
and exactly type/value matched the same row's scalar columns: 4,620 of 4,620
property instances matched. There were no missing properties, mismatches, or
unknown extra JSON keys in this observed population. The original JSON occupied
323,162 UTF-8 bytes; serializing the same JSON objects after removing only those
five exact duplicates with the existing sorted compact serializer occupied
140,255 bytes, a 182,907-byte logical payload reduction. This is a JSON value
length comparison only. It does not establish SQLite page reclamation or a
physical database file reduction.

The Email 53→54 transform removes a locator property only when its
JSON type and value exactly match the corresponding scalar column. For
reversible source semantics it fails closed if any of the five source
properties is absent; a differing value remains in the JSON, including a
`uidValidity` that reflects the historical traversal fallback. `messageId`,
`threadId`, unknown keys, and all candidate state, hashes, timestamps, and
provenance remain untouched. New writes use the same compact representation;
the list reader reconstructs the public provider-message projection from
current scalar owners while retaining any persisted mismatch. Email54 was
deployed on 2026-10-24 at commit `a4154c03b`. Production readback confirmed
Store `2026-10-23.1` / Email54, current schema manifest, `quick_check=ok`, 924
candidate rows and 140,255 JSON bytes with no exact duplicate locator keys.
The database file remains 514,969,600 bytes: the 182,907-byte logical JSON
reduction did not reclaim SQLite pages because production was not VACUUMed.
The existing foreign-key finding `runs(190042) -> tasks(1)` is unchanged.

The 7.4 MB normalized-text, 8.5 MB model-text, and 7.8 MB preview figures are
logical value lengths inside `emails`, not independently reclaimable SQLite
page estimates. They must not be summed as physical savings or removed without
the per-row provider/source checks.

## Concrete reduction boundary

1. **Completed Task input.** `compact_done_email_classification_inputs`
   in `app/storage_maintenance.py` already selects only `done` classifier Tasks,
   applies `EmailClassificationTaskAdapter.compact_done_input_json`, and updates
   the row under a status predicate. This is the only currently source-backed
   bulk content reduction for classifier inputs. The read-only preflight found
   no currently eligible un-compacted completed rows, so no compaction was run.
2. **Keep runtime samples bounded.** The writer in `EmailStore.record_classifier_runtime_sample`
   removes samples beyond `MAX_CLASSIFIER_RUNTIME_SAMPLES`; current readers use
   those samples for Quality and latency. No evidence supports treating these as
   training data or deleting the current window.
3. **Remove only exact historical-candidate locator duplication.** The local
   Email 53→54 candidate removes the five locator keys only where exact typed
   values match same-row scalar columns, and the read-only current-data check
   measured 182,907 logical JSON bytes. It retains message/thread IDs, all
   candidate content and status facts, and any mismatches. This does not prove a
   safe table-wide DELETE or candidate-body retirement rule.
4. **Do not merge Email Tasks or Actions back into `emails`.** A message, a
   classification request, and a provider operation have different identities
   and lifecycles. The accepted B1/B2 design already assigns them to their
   respective owners.

## Execution and release boundary

The 53→54 production data migration and standard deployment completed after the
current-data forward/inverse rehearsal. Post-deploy `/healthz`, queue, Attention
and History endpoints returned HTTP 200. The verified deployment database
backup and deployment staging files were removed after readback, per Derek's
instruction; the production database was retained. The backup directory was
cleared of its remaining backup artifacts.

The separate A5b Reply-local-result work remains open in the broader plan; it is
not an Email table migration. It should not be silently folded into this Email
phase. See `docs/superpowers/plans/2026-10-19-business-model-simplification.md`
and `docs/business-model-run-owner-validation.md` for that boundary.
