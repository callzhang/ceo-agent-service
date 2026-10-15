# Specialized input JSON formatting

## Scope

Four serializer calls only: meeting inline schema, complete meeting source, email classification context (sort_keys=True preserved), Task WorkItem metadata. Indentation/separator spaces are removed. No field/string/schema/source truncation, instruction relocation, native channel, task classification, session mechanism, business rule, permission or Audit binding ownership change. Existing meeting root-description omission and Task summary/source projection remain exactly as before. Held PR57/native schema/provider changes remain separate.

## Verification

Three new focused regressions fail before formatting changes and pass after, checking actual builder output, complete model field equality, Unicode/string whitespace/escapes and the separate Task semantic context. Initial four-file run358 passed/1 failed: only an existing scheduled Task ID assertion required whitespace. It now parses the actual WorkItem and checks scheduled_consumer.scheduled_task_run_id. Final rerun and independent readonly review are in progress.

Original saved Meeting360330/360165 service inputs are268758/333772 characters. Formatting only the embedded schema and full source blocks removes19714/54268 characters. Email classification360277/359897/359771 removes1609/1682/2567 characters. All parsed block values compare exactly. These are observed source-block character differences, not total tokens or new native business outcomes. The latest Task357887/357723 attempts do not contain the original WorkItem marker; no live Task reduction is inferred and no history is reconstructed. This preparation initially asserted that every historical sample contained the current marker; after locating the mismatch it records those samples unavailable.

Read-only production queries use URI mode=ro with bounded query progress, no Store constructor, writes, provider calls or task replay. Private original inputs stay in their native files; metadata/ranges/hashes only are in evals/trajectory_optimization/specialized_json_formatting.live_metadata.v1.json. No claim that process completion proves delivered meeting messages.

No publication or deployment yet.

Final four-file run359 passed in14.76s; Ruff/diff pass. Independent readonly review finds no runtime blocker, confirms four serializer-only changes and metadata arithmetic, and independently passes the3 new tests. Its minor integer-test precision finding is addressed with exact int type plus value; final4 direct checks pass in0.59s. No model/provider calls were added for formatting-only equivalence. Formal publication and production readback pending.

Ordinary push is initially rejected because current main has the separate Console query repair d7c72a3f and its merge/readback documentation. Normal merge7f98a4b2 preserves that upstream change; no code conflict and no history rewrite. Post-merge eight-file specialized/Task/query-cost/snapshot/cache group passes373 tests in19.52s, targeted Ruff/diff pass. This supersedes the earlier359-test integration count for the published tree; provider/business execution remains outside formatting equivalence.


## Formal publication and production readback

Formatting commit21e2237c plus normal upstream merge7f98a4b2 and validationafd78131 are pushed to main. Formal `python -m app.deploy` reports deployed832da02e ->afd78131. Production HEADafd78131 and all three runtime source files match reviewed bytes. New supervisor40832/service40835/web40836/email40837 were read back. Health200/ok0.033s; Status2001.165s with pending3/processing1/failed23/retryable0/Attention17; Attention2000.498s (15 groups); History page_size2/include_chartfalse2001.27s (2 items); public Consumer/Codex Settings preview200/available/current0.17s. The History improvement is attributable to the separately merged Console query repair, not a performance claim for JSON formatting. Current processing1 is a queue observation, not evidence of stuck work; existing failure counts are not claimed resolved. Metadata/source hashes/process IDs in specialized_json_formatting.production.v1.json.

This proves formal deployment/source/process/API readback of the formatting change. No new specialized native business task was forced or replayed for acceptance; actual subsequent business judgments, total tokens and all-trajectory completion are not inferred. Audit binding ownership remains proposal-only awaiting explicit scoped approval. Held Task Skill/Settings/native schema/provider drafts remain separate.
