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

The 7.4 MB normalized-text, 8.5 MB model-text, and 7.8 MB preview figures are
logical value lengths inside `emails`, not independently reclaimable SQLite
page estimates. They must not be summed as physical savings or removed without
the per-row provider/source checks.

## Concrete reduction boundary

1. **Use the existing completed-input compactor.** `compact_done_email_classification_inputs`
   in `app/storage_maintenance.py` already selects only `done` classifier Tasks,
   applies `EmailClassificationTaskAdapter.compact_done_input_json`, and updates
   the row under a status predicate. This is the only currently source-backed
   bulk content reduction identified for Email inputs. The compactor explicitly
   requires caller-owned recovery and coordination; this work did not run it.
2. **Keep runtime samples bounded.** The writer in `EmailStore.record_classifier_runtime_sample`
   removes samples beyond `MAX_CLASSIFIER_RUNTIME_SAMPLES`; current readers use
   those samples for Quality and latency. No evidence supports treating these as
   training data or deleting the current window.
3. **Compact historical candidates only after row-level proof.** Candidate
   locator, task/result references, scan state and source availability must be
   checked together. There is no safe table-wide DELETE rule in the current code.
4. **Do not merge Email Tasks or Actions back into `emails`.** A message, a
   classification request, and a provider operation have different identities
   and lifecycles. The accepted B1/B2 design already assigns them to their
   respective owners.

## Execution and release boundary

This turn made no database or runtime change. The development worktree was
read-only with respect to production, and no backup, database copy, or temporary
artifact was created. A production schema or data migration still requires the
project’s current-data forward/inverse acceptance and the standard release
path. The deployment workflow automatically makes a database backup; because
Derek has explicitly prohibited backups and temporary files, that release path
cannot be run under the present instruction.

The separate A5b Reply-local-result work remains open in the broader plan; it is
not an Email table migration. It should not be silently folded into this Email
phase. See `docs/superpowers/plans/2026-10-19-business-model-simplification.md`
and `docs/business-model-run-owner-validation.md` for that boundary.
