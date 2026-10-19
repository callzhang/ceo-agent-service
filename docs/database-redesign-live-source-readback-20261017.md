# Live database source readback, 2026-10-17

This is a read-only source binding and metadata check for the ongoing
`2026-10-11-database-redesign-design.md` implementation. It records the actual
LaunchAgent database path and current signal/event source shape. It is not a
copy-migration test or production readiness approval.

## Database path and current shape

`/Users/derek/Library/LaunchAgents/com.ceo-agent-service.main.plist` sets
`CEO_SERVICE_ROOT=/Users/derek/Services/ceo-agent-service` and defaults
`CEO_WORKER_DB` to
`/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`.
The running job's database is therefore the Library/Application Support file,
not `data/auto-reply.sqlite3` under the service checkout.

The configured database was opened with SQLite `mode=ro` and
`PRAGMA query_only=on`. Readback was taken at `2026-10-17T03:54Z`.
The current `app.service_supervisor`, `app.cli service`, `app.cli audit-web`,
and `app.cli email-worker` process arguments independently show the same
`--db` path.

| Measurement | Result |
| --- | ---: |
| File size | 496,115,712 bytes |
| `PRAGMA quick_check` | `ok` |
| Non-internal table objects, including virtual/shadow FTS objects | 121 |
| `email_schema_migrations` latest row | 51 |
| `business_task_signals` | 918 rows |
| `business_task_events` | 971 rows |
| `business_source_documents` | 213 rows / 7,017,149 body bytes |

A later metadata-only `pragma_table_list` read at `2026-10-17T10:24Z`
separates this aggregate into **116 ordinary tables, one virtual FTS table and
four shadow tables**, as captured in
`superpowers/specs/2026-10-17-task-delivery-source-metadata.json`. The earlier
121 count must not be described as ordinary tables excluding FTS. The row and
body-byte observations above retain their original observation time.

The service-root `data/auto-reply.sqlite3` is a distinct 1,880,064-byte file;
the inspected Business/Email source tables there were empty. It must not be
used as the current production migration source.

## Signal dependency facts

Every current signal has a nonempty `source_ref`; 613 also have nonempty
`source_time`. The source-type counts are 611 `ai_minutes`, 242
`management_weekly_report`, 50 `project_weekly_report`, 13
`department_weekly_report`, one `console`, and one `service_data_repair`.
There are 800 signals with a nonempty `context_json.source_link`.

`business_task_events` directly references both `business_tasks` and
`business_task_signals`. The signal table has incoming FKs from eleven source
families: `business_task_follow_ups`, `business_task_evidence`,
`business_task_date_evidence`, `business_task_events`,
`business_task_relations`, `business_task_anchor_links`,
`business_project_candidates`, `business_attention_items`,
`business_attention_events`, `business_legacy_links`, and
`business_project_evidence`. Current row counts for those families were also
read without reading their text fields; several are nonempty, so the signal
parent cannot be retired by migrating only task events.

| Signal-referencing source family | Rows |
| --- | ---: |
| `business_task_follow_ups` | 0 |
| `business_task_evidence` | 975 |
| `business_task_date_evidence` | 0 |
| `business_task_events` | 971 |
| `business_task_relations` | 0 |
| `business_task_anchor_links` | 99 |
| `business_project_candidates` | 10 |
| `business_attention_items` | 11 |
| `business_attention_events` | 20 |
| `business_legacy_links` | 0 |
| `business_project_evidence` | 44 |

All 213 source-document rows are referenced and have distinct `identity_key`
values. Of those source versions, 80 are referenced by exactly one signal and
133 by multiple signals; the highest fan-out is 24. This is the actual current
cardinality that the provenance migration must use: unique-owner source
identity stays inline, while only genuinely shared versions need a
`provenance` row. Source-body recovery remains unverified, so this count does
not authorize dropping `business_source_documents.body`.

The current `business_task_signals.context_json` has keys `action`,
`assignment_authorized`, `reason`, `source_description`, `source_link`, and
`work_item_title`; it has no `native_provenance` key. The earlier 30/188 native
body-recovery result used a different frozen database snapshot and does not
prove current source-body recoverability. The current `source_ref` values are
retained inline provenance facts, but locator presence alone does not prove an
exact historical source version can be re-read.

The approved CSV routes signals to `actions.signal_fact` and task events to
`actions.task_event`, with direct task and signal relationships. The current
Actions implementation does not yet provide these record types. The next
release must account for the eleven incoming signal FK families and validate
the existing source-body/provenance retirement boundary against the current
source; no production migration was performed here.

## Bounded current native-version recheck, 2026-10-17

A later read-only snapshot still contains213 source documents and7,017,149
UTF-8 body bytes. Root independently rechecked all188 document IDs from
`superpowers/specs/2026-10-15-database-redesign-business-native-source-recovery.json`: their byte lengths and SHA-256 values all match the current rows.
The25 additional current documents contribute1,030,823 bytes.

Root then imported the existing `read_native_source_body` normally under the
project Python runtime and bounded its two path finders to the exact two local
paths already recorded in that artifact. All30 proven coordinates passed the
existing raw-line version, JSON leaf/range and decoded body hash checks, and
the returned body hashes match the current database: **30 current documents /
672,297 bytes**. No body was printed, copied into a receipt or persisted.
The native files were read as existing CLI-owned sources.

**183 current documents / 6,344,852 bytes remain unproved by this native-file
method**, rather than known unavailable. The earlier158/188 gap must not be
reported as the current denominator. The reader consumes already proven
coordinates; it does not discover missing locators. A next finite proof must
bind current Signal source metadata through typed work-summary Tasks and
Task Agent/runtime Runs to explicit candidate sessions, then validate exact
version and body coordinates. The earlier provider-minutes probe is separate
from this native-file check and does not establish current provider availability.

The database used URI `mode=ro`, `query_only=on` and a read transaction. Body
hashes were computed in process memory only. No source database/file was
modified; no provider/network request, production migration or deployment
was performed. This readback does not authorize retiring the remaining bodies.

## Additional25 current versions independently recovered

The metadata-only artifact
`superpowers/specs/2026-10-17-business-native-source-additional-recovery.json`
records exact native coordinates for current document IDs189–213. Root
independently loaded it and called the unchanged native reader under the
normal project runtime with the finder limited to its one explicit session
path. All25 whole bodies/1,030,823 UTF-8 bytes match the read-only current
DB rows and artifact hashes; all85 associated Signal source-identity checks
also match. The artifact is34,905 bytes with SHA-256
`803f78d34564275edc1a296a076122478b2aa45069a610e6fefcc6ecb66e1d1a`.

This supersedes the preceding183-unproved lower bound: **55/213 current
documents,1,703,120/7,017,149 bytes now have exact native-file recovery
proof;158 documents/5,314,029 bytes remain unproved by that method**.
No current body was persisted in the artifact. Multiple exact matches in a
session do not imply source-ref uniqueness:397 matching decoded leaves
were observed; the artifact records a deterministic one per document.
The lineage's runtime-attempt rows share a session but have parent_id NULL;
they are not a fabricated FK to Task Agent runs.

This remains a bounded current readback, not retirement or production migration
approval. The native line/version checks must still succeed when the eventual
provenance migration is verified.

A separate bounded follow-up scanned the same one explicit Codex file once
(134,811,851 bytes/882 valid JSON lines, unchanged stat; SHA-256
`02dd8f68158e264ad5aa8066c129aead38eca59f435e86cb73351743b16dd4ec`)
against exactly the158 remaining bodies. It found zero additional whole-body
matches in the existing supported JSON-string-literal format. All723 linked
Signal identities for those158 bodies recomputed against current source
metadata; this does not supply missing native coordinates. The55-document
lower bound is unchanged. No wider native-home scan or provider request was
performed, and this negative single-file result does not prove unavailability
elsewhere.


## Next Errors component source aggregate (readonly)

A bounded production URI mode=ro/query_only aggregate after the TaskDelivery
copy acceptance sees15,447 errors, maximum public ID15,490 and sqlite_sequence
water15,490. conversation_id is NULL in5,073 rows; message_id is NULL in7,813.
Only2 rows have resolved_at=''. Total detail UTF8 bytes are4,509,755. Source
scalar storage types are nullable text for those two IDs and text for all
other non-ID columns. No detail text was printed or persisted. This supports
nullable-identity and deleted-ID/high-water tests only. It does not establish
native provenance for resolved detail or authorize its deletion. Errors is an
independent next opt-in component; current32 remains selected until a complete
caller/constructor/paired-release cutover is accepted.


## Remaining native-lineage bounded refresh after current-copy PASS

Read-only research and independent root SQL readback agree that the remaining
158 documents /5,314,029 bytes link723 distinct signals,92 source-type/ref
pairs,90 typed work-summary inputs,198 task-agent runs and23 nonempty Codex
session UUIDs. Correct run matching uses the input's public
`cast(tasks.legacy_key as integer)=runs.summary_input_id`, not physical
`tasks.id`. Documents8 and30 stop at signals; the other156 reach nonempty
sessions. Same-session runtime sharing is candidate lineage, not a direct
parent FK. All23 UUIDs are in the earlier24-missing-file cohort.

Root independently re-read ONLY those23 UUID entries from the native session
path index:23 entries exist,0 indexed original paths exist, and0 deterministic
archived_sessions filename counterparts exist. No new finite readable native
candidate was found; no transcript bodies were re-scanned. The exact native
lower bound remains55/213 documents. This is not proof of permanent source
unavailability and authorizes no source-body retirement. No provider access,
source DB mutation or persisted source content occurred.


## Production allocation refresh during Errors caller cutover

Readonly mode=ro/query_only at 2026-10-17T12:43:45.023136+00:00 reports
500,031,488 allocated SQLite page bytes /499,998,720 active page bytes /
32,768 freelist bytes, main-file size500,031,488 and WAL4,144,752 bytes.
These are observations of live production, not a post-migration candidate.
A separate bounded dbstat grouping yields table+index bytes:

| Native family | Allocated object bytes |
|---|---:|
| runs | 130314240 |
| actions | 94392320 |
| tasks | 50843648 |
| email_classifications | 40615936 |
| email_messages | 36261888 |
| email_provider_observations | 25026560 |
| email_action_plans | 15466496 |
| email_actions | 14192640 |
| work_updates | 12365824 |
| follow_up_drafts | 11206656 |

Freelist alone does not measure unused space within B-tree pages; this is not
a prediction of VACUUM savings or proof that all active payload is necessary.
Next31 copy rehearsal will record source/target allocation alongside schema
and fact proofs. No VACUUM, checkpoint or source write was issued.


## Runs source growth shape (readonly aggregate, not retention change)

At the bounded readback, native `runs` contains177,419 scheduled_task_runs,
24,289 runtime attempts,22,970 agent runs,10,752 task-agent runs,2,588
meeting runs,1,157 state events,810 workbench events and22 OKR review runs.
Scheduled rows have zero payload_json/projection_json body bytes; task-agent
projection_json totals128,688 bytes, workbench payload_json13,756 bytes.
A later status aggregate (live source may advance between reads) sees160,547
service-command dispatches,15,742 skips,1,060 failures,71 reply-task dispatches
and2 pending occurrences. Service-command result_summary bytes3,390,920.
Do not call all130,314,240 allocated Runs object bytes trajectory bodies or
attribute bytes by row count alone; nullable fields, identities and indexes
also occupy pages. No historical scheduler rows were deleted and no retention
rule was changed. This observation does not itself prove which receipts are
safe to retire; source/execution references need separate verification.

## Cross-method coverage reconciliation (not fresh provider availability)

Root re-read the older exact canonical Provider receipt2026-10-15-business-minutes-canonical-source-probe.json including its four successful follow-up rows. All130 verified document IDs still have exactly the recorded full-body UTF8 SHA256/byte lengths in the current readonly source. They are disjoint from55 native-file-proven rows. Thus185/213 current document contents match some recorded exact recovery proof, leaving28/676423B without either recorded proof:8,12,30,31,46,49,50,51,52,66,67,68,69,90,91,93,94,95,96,97,99,100,101,114,115,118,119,120. Their signal types are console/ai_minutes/service_data_repair/management_weekly_report/project_weekly_report.

This does NOT refresh Provider availability, implement a canonical Provider reader or authorize retiring130 bodies based on an old receipt. It corrects interpretation:158 is the current native-file-only gap, not the number lacking every historical method. Next source work should reuse130 exact known Provider locators/formatters and refresh only needed current availability/read-path contracts, while28 need genuinely new exact reconstruction or an explicit new decision. No providers called, source writes, raw body output/artifacts or broad scans in this reconciliation.

## Current220 versions: Console reconstruction and new7 native proof

A new readonly snapshot has220 documents/7,164,322 UTF8 body bytes. Root rechecks the185 older verified metadata records against all current body hashes/byte lengths:55 native-recorded and130 Provider-recorded versions still match. Provider rows enter this union only with status=verified; whole_body_equal alone is insufficient (three nonverified rows49/50/52 retain their gaps). This is not fresh Provider availability.

Metadata receipt `superpowers/specs/2026-10-17-business-console-source-recovery.json` SHA9d2875aa1686b42e5d038cae2f2ffd2c8abbfb48b9a1043896095df58f8f0ed0 proves document8/111B can be reconstructed exactly by the existing Console formatter from signal44 author/action and task-event44 before_json title/updated_at. The full source_ref(task43/action/version), bodySHA and complete source_document_key match; altered-body control fails. No author name, source text or event snapshot is stored in receipt. This is a demonstrated reconstruction, not a selected runtime reader. Repair document30 remains unproved; context.reason/event.reason are not exact originals.

New214–220 have3 source_type/ref pairs, each bound through its exact completed summary-input and TaskAgent run to the one already-known Codex session01a13b26-22ac-7891-9388-64b6a5a44786. Root reads only that explicit native file, selects deterministic literal coordinates, and calls unchanged `read_native_source_body`: all7/147,173B and11 complete source identity checks pass. The observed170,230,039B prefix remains unchanged after readback; append is allowed, selected raw-line hashes remain exact. No native file is copied. Root further rereads every prior55 proven coordinate through the unchanged reader, with finders limited to the2 explicit known Codex/Claude files: all55 pass against current DB content. New7 proof1.632s/session95102 EXIT0; old55 refresh1.478s/session71241 EXIT0. Both sessions closed. The probe initially assumed3 distinct paths before reading files; metadata shows2, corrected before the successful55 read. No source or migration mutation occurred.

Final metadata receipt `superpowers/specs/2026-10-17-business-native-source-new7-recovery.json` SHA59217b6280dfa1561cf10317b7dd4b0cfeebbf2a62f7e6cf27642fd385737c96 includes the new7 coordinates and fresh55 reader recheck. Current native-reader exact coverage is62/220 documents and1,850,293B; plus Console1/111B gives63 current exact reconstructions. The130 older Provider records/4,637,606B retain current content identity but still require a working current reader/availability verification. No runtime body-reader activation, provider request or body retirement occurs here.

27 older versions/676,312B remain without either recorded method or Console proof:12,30,31,46,49,50,51,52,66,67,68,69,90,91,93,94,95,96,97,99,100,101,114,115,118,119,120. They are unproved, not permanently unavailable. The native-only gap is158/220, not the total unproved count. Current body-dependent readers/validators still need later provenance integration with no provider I/O inserted into a writer transaction. These exact-version proofs do not authorize deleting the unproved originals or storing a duplicate archive.

A further finite attempt asked the native Codex app history reader for completed source run session01a0f1c7-dbea-7ef3-bd0a-b83a886657dd, bound from document66 through summaryinput27454/TaskAgent run10647. The read-only request returns `missing source rollout`; it provides no alternate original for that one session. This is not a conclusion about all27 remaining versions. No cross-thread message or replay occurred.


## Finite historical weekly version gaps rechecked

Native `dws doc +fetch --node ... --version ... --format json`, using the same explicit sole current profile, now returns successful complete responses with exact requested historyVersion and node binding for the six previously unattempted or unbound versions. This is a bounded read-only check, not another complete history traversal.

- Versions16/9/0 of the document behind source66/68/93: three measured requests plus one earlier16 response-shape discovery request. None of their stripped Markdown or reconstructed complete canonical JSON bodies equals any of the three originals. Receipt `superpowers/specs/2026-10-17-business-weekly-unattempted-source-probe.json`, SHA256 db2dd2dcedc406d56607921a233bc1f125fc721f401e47e7ad2278b54b7444aa; session23790 EXIT0/CLOSED.
- Earlier missing-binding versions117/114 of that same node and1065 of source94's node: three requests, all now bound exactly. No Markdown, complete body or full source identity matches. The1065 comparison also includes already native-proven56 as another version of that node; it adds no new recovery claim. Receipt `superpowers/specs/2026-10-17-business-weekly-missing-binding-source-probe.json`, SHA256 249939e615ba7f12638946abcc42a5723c3364d0af64a37c5bee5e5d29de0c95; session22399 EXIT0/CLOSED.

Only hashes, sizes, locator/version metadata and comparisons are retained. No raw Provider response, original source body, persistent profile change, source/provider write or external send occurred. Exact reconstruction coverage remains63 current native/Console versions plus130 historical Provider proofs with current-content identity but unrefreshed availability; the27 strict gaps remain. These finite negative results do not prove permanent source unavailability, implement a runtime reader or authorize body retirement.


## Four current weekly originals now have reproducible exact proof

Root separately refreshes the four previously noted current weekly rows67/69/91/93 via exactly3 known nodes, with the same explicit sole current DWS profile. All3 current +fetch responses are complete/successful and bind exact node identity. Provider node/title/Markdown feed genuine ReportDocument, classify_report_title, report_period and _work_item; only the original URL locator is retained from source metadata, not inferred from a different current URL alias. No stored Markdown supplies reconstructed content. All five report metadata fields, full canonical JSON body, native source_ref digest and all52 attached source_document_key checks match; altered-body controls fail. Source authorship/time/conversation metadata is preserved for identity comparison, not independently provider-verified authorship.

Receipt superpowers/specs/2026-10-17-business-weekly-current4-source-probe.json SHA25628c489d3bd62ff4f40b4d2daf1fe37a6eb48d93fe0fa55b6bf2953388e9146fd records4 whole originals/93,526 UTF8 bytes,3 provider body requests,5.086s/session23436 EXIT0/CLOSED, both producer module hashes unchanged. This adds complete reconstructible-wrapper proof to earlier informal current-Markdown observations. It does not repeat or overturn the negative historical-version probes for other content versions.

Relative to the reconciled220-document snapshot, recorded strict coverage becomes197/220 (67 freshly reconstructed native/Console/current-Provider versions,130 older Provider proofs with current-content identity but availability still unrefreshed). The remaining23 versions/582,786B are:12,30,31,46,49,50,51,52,66,68,90,94,95,96,97,99,100,101,114,115,118,119,120. The220 denominator is the earlier reconciled snapshot, not a newly rehashed full database in this four-row probe. No permanent availability, production migration, runtime Provider-reader activation or body retirement is claimed. Only locator/hash/size/comparison metadata is persisted; no raw body/response, source/provider write or external send.
