# Remaining task input source preparation

Bounded source research only. The approved global CSV assigns meeting source
JSON and OKR source JSON to active input followed by verified source references.
Phase4 deliberately preserved their stored values during structural migration;
that stage did not implement their eventual terminal-input retirement.
No new field, retention period, source deletion, migration or release is
selected by this note.

A readonly mode=ro/query_only single-transaction production observation on
2026-10-14, with separately observed code d9d34093, found:

| Source input in current tasks | Rows | UTF-8 bytes | Active | Terminal |
|---|---:|---:|---:|---:|
| meeting_alignment_jobs.source_json |717|10,644,427|3 /115,409bytes|714 /10,529,018bytes|
| okr_review_requests.okr_source_json |20|2,671,871|0|20 /2,671,871bytes|

Meeting terminal statuses were561 sent,85 no_action and68 skipped. OKR had19
done and1 skipped. Aggregate counts are observations, not future source-count
assertions. Checkout HEAD and database snapshot are not atomic. No private
body or source reference values are included.

## Existing dependent behavior

Meeting creation stores provider discovery, minutes metadata and derived
calendar evidence in app/meeting_alignment.py. Analysis and delivery read
calendar/recording evidence from that JSON. Store segment attachment can mutate
the same input and reopen even a sent job; unsent no_action/failed jobs also
have a rerun path. Weekly report materials read the stored start time and
minutes URL. Thus terminal status alone does not prove that the entire JSON
is disposable: small adopted metadata and exact source reconstruction for a
new segment must be handled before retirement. Source locations:
app/meeting_alignment.py:2497,1234,1670; app/store.py:16612,17783;
app/weekly_report_materials.py:133 in the research candidate.

OKR processing reads the captured processed.objectives/processed.okrRows and
source person identity for the review prompt. Failed requests can be reopened
with replacement source input. Existing live reads fetch by user and period;
a period/page URL plus capturedAt does not establish retrieval of the same
historical version. Source locations: app/okr_review.py:711,62 and
app/store.py:18230 in the research candidate.

## Exact-source questions before retirement

All717 meeting rows have a meeting ID and minutes URL;671 include calendar
evidence/discovery start time and2 include extra segment IDs. All20 OKR rows
have conversation/trigger IDs and source user/period/capture metadata;16 have
a page URL and9 have trigger_sender_user_id. The generic tasks.source_ref is
blank for these737 rows, and their existing typed representations do not
establish a source-body hash/version locator. Native run metadata may supply
process evidence but has not demonstrated the exact original provider input.

The next bounded unit must verify exact original-source feasibility per row,
map the small existing metadata consumed by readers, and prove active input,
segment reopening, reruns and old-code/current-fact inverse behavior before
retiring recoverable input portions. Preserve adopted decision_json, final
message, send_result_json/receipts and OKR review output independently. Do not
turn a current provider fetch or a session ID into a false historical-source
claim. Unknown original availability remains unverified, not permission for
blanket terminal-row deletion or a provenance body archive.

No provider reread, native trajectory copy, schema/data write or deletion was
performed by this research. The already implemented reply/WSI compaction and
these remaining inputs are separate acceptance scopes.

## Refreshed264owned-source observation, 2026-10-15

The verified online backup observed2026-10-15T09:15:41.822422+00:00 refreshes
this historical preparation:730meeting jobs hold11001089UTF-8 source_json bytes
(571sent/10524626bytes,85no_action/177922,72skipped/221755,2failed/76786).
Twenty OKR requests retain2671871bytes (19done/2468674,1skipped/203197).
The exact source SHA/ref and groups are in
`docs/superpowers/specs/2026-10-15-database-redesign-remaining-input-bytes.json`.
No private source body or source-ref value is exported.

This is an owned immutable backup observation, not a later live-production count.
It updates sizes without resolving the original-version/reopening questions
above. No source_json or okr_source_json was retired or schema-selected.
