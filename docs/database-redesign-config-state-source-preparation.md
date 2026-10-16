# Config, provider, schedule and state source preparation

Readonly preparation for the approved ten-core-table design; no target schema,
version, caller conversion, retention operation or production change is selected.
Production still exposed123 non-SQLite-internal tables during the2026-10-14
08:45–08:47 UTC observations. The code and database observations are separate.

The original approved field CSV still covers every current column of these
34 source tables exactly: no added or removed source column was found. This
is column coverage only, not acceptance of constraints or migration behavior.

| Approved target | Existing source tables | Mapped source fields | Current allocated bytes, including indexes |
|---|---:|---:|---:|
| configs |15|118|5,029,888|
| provider_state |2|12|24,870,912|
| schedules |1|17|24,576|
| state |16|91|4,792,320|

Counts and sizes came from readonly query_only transactions. Sizes include
allocated pages and are not a reclaim estimate. The categories were matched
by CSV target_table, never by source column names such as state.

The provider sources are email_provider_observations (72,091 current message
rows;24,862,720 bytes) and email_provider_folder_observation_generations
(55 rows;8,192 bytes). Account configuration is separate: email_accounts has
two rows and occupies8,192 bytes. EmailStore.get_provider_classification_state
joins a message's observation with its folder's generation; the refresh writer
upserts by account/stable identity and reconciles complete folder membership.
These are current inventory facts, not an append-only observation log. Preserve
that behavior when combining the two provider families into provider_state.
The already approved retirement boundary for unavailable unreferenced rows
requires an authoritative refresh; this preparation performs no retirement.

Configuration size is mainly managed_skill_revisions:156 rows occupy4,407,296
bytes. Existing immutable config versions and their references cannot be reduced
to one latest config merely by applying the separate latest-model policy.
scheduled_task_config_versions has68 rows/294,912 bytes; runs references those
versions and scheduled_tasks. runtime_skill_load_receipts references runtime
config, and managed_skill_export_receipts references managed revisions. Both
receipt families belong to the separately mapped actions work and must have
their direct references converted in the configuration migration closure.
scheduled_task_skill_refs also references scheduled_tasks and managed versions.

Other native dependency facts: category-folder bindings use CASCADE edges to
accounts/categories; model mode transitions use a RESTRICT edge to promotion
config; runtime configs and managed revisions have self-parent edges. Config
immutability and three existing account blocker families are enforced by
existing triggers. Their exact native semantics need actual/fresh capture and
source/target DML proof; absence of data in a family does not retire its contract.
email_training_snapshot_observations retains an FK to snapshot metadata even
though its row population was retired earlier; the approved spec retires that
empty structure rather than migrating a training corpus into configs.

The sixteen state source tables hold91 mapped fields. Larger sources are the
dispatcher lease ledger (10,059 rows/1,892,352 bytes), seen-message identities
(7,113 rows/1,003,520 bytes), daily scan state (9 rows/872,448 bytes), and native
session bindings (5,298 rows/696,320 bytes). These measurements establish neither
expiry eligibility nor a new history policy. Preserve dedupe boundaries, active
lease ownership/generation, source cursors and session bindings during conversion.

Next bounded work: freeze literal actual and genuine fresh DDL, indexes, triggers,
allocator/ROWID and full incoming SQL/FK closure; map native uniqueness and
mutation behavior; then plan the smallest atomic publication units with direct
references. No compatibility tables/views, copied native trajectory, source-body
archive or new schema fields are justified by this inventory.


## Literal actual/published-fresh source freeze, 2026-10-15

A new metadata-only capture at 2026-10-15T13:33:48 UTC is recorded in
`superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json`.
A single query-only read transaction inspected the current production source;
producer `7dadc492c1c62d5b0624f2f97dd0c89f4f358646` and Store2026-10-13.5/Email51
were verified. All handles closed after capture. Genuine fresh metadata came
from an exact `git archive` of that published code, using its real Store/Email
constructors at an explicit private temporary database path. Archive, database
and parent temporary directory were removed and absence verified.

The actual 34 families contain **238 fields, 44 indexes, 25 owned triggers,
13 outgoing FK groups, four AUTOINCREMENT families and three WITHOUT ROWID
families**. Eighteen incoming FK groups include thirteen internal edges and
five external ones: retired-corpus observation metadata → training snapshot,
managed Skill export receipts → revisions, runtime Skill load receipts →
configs, and two `runs` edges → schedule/config version. This is a literal FK
inventory, not proof of the complete dynamic caller/trigger reference closure.
External views/triggers are recorded as an explicitly unfiltered declaration
inventory for later classification. No production row contents or config
values were captured.

Actual and genuine fresh declarations differ for six families: email_accounts,
scheduled_task_config_versions, email_provider_observations, scheduled_tasks,
conversations and org_user_profiles. Fresh has **234 fields**: the four actual
legacy conversations columns `in_handoff`, `handoff_message_id`,
`handoff_message_create_time`, and `manual_clear_message_id` are absent from
published fresh creation. A bounded search of current `app/` and `tests/`, excluding the new metadata
oracle that names those source columns, found no consumer of those four names. They are candidates for explicit retirement
in the later state field map; this capture deletes no values or columns.
The old CSV still covers all 238 actual fields. Fresh initialization also
creates default category/config/state records, so fresh is not assumed empty.

`tests/test_config_state_native_source.py` first failed on the missing artifact.
Initial roundtrip checks exposed the four-field actual/fresh distinction and
the three WITHOUT ROWID PK indexes that have PRAGMA index metadata but no
separate sqlite_schema index record. The final native SQL reconstruction and
CSV coverage suite passed **six cases in 0.21 seconds**; Ruff and diff checks
passed. All literal tables, column metadata, native FK lists, index lists/xinfo
and trigger SQL reconstruct exactly for both variants. These are source
metadata/reconstruction checks only; source mutation semantics, CAS/cascade/
RESTRICT/immutable DML, target mapping, migration/inverse and caller selection
remain to be implemented. No target schema, version, retention rule, provider
call, production write or deployment was introduced.


### Independent source-metadata acceptance

Sequential fresh SPEC and QUALITY accepted commit `3e171e85`. Both independently
matched the 34-family grouping, actual/fresh field distinction, original CSV
coverage, index/trigger/FK and allocator/layout counts. SPEC reran six cases
in 0.10 seconds; QUALITY reran them in 0.19 seconds, with no runtime or provider
activity and no edits. This accepts metadata and literal source reconstruction
only. The six tests do not independently prove the production read or temporary
cleanup: those are root's recorded capture-command receipts, including the
closed query-only source and exact published archive constructor invocation.
The parent TemporaryDirectory was asserted absent after its context exited.
No raw temporary database or archive is retained for historical replay.

The next work for these groups remains exact source mutation/caller semantics
and target field/constraint mapping, including explicit treatment of the four
unused actual-only conversation fields and the five external FK groups. This
is preparation for the existing ten-table goal, not another target design or
a claim that these 34 tables have been merged.

## Bounded configs source-contract plan, 2026-10-15

`docs/superpowers/plans/2026-10-15-configs-source-contract-plan.md` specifies
the next independently testable unit for the 15 config families. All 118
existing fields already have accepted literal actual/fresh metadata coverage;
the proposed source oracle samples named DML constraints and caller behavior,
without claiming universal behavioral proof for every field. It uses synthetic
SQLite connections and the genuine archived published constructor for category
CAS, runtime Skill load-receipt matching, scheduled snapshot interning and
references, and the WeChat scope watermark. The six predecessor account
blockers receive declaration comparison in this plan; the separate existing
`tests/test_email_action_children_source.py::test_each_external_in_flight_trigger_blocks_source_mutation`
gate exercises 13 captured external trigger cases over actual/fresh_candidate
Email action declarations with narrow account/message/classification stubs.
This is research and planning only: no source contract test, target declaration,
migration, caller selection, actual-copy proof or release was implemented by
this appendix.

The future atomic closure remains the 15 config families together with
`scheduled_tasks` and the two `runs` schedule/config references, managed Skill
export and runtime Skill load receipts converted to typed `actions` references,
and the approved empty `email_training_snapshot_observations` FK/trigger
retirement. The six account blocker owners must retain the current candidate's
typed `actions` predicates; the old source blocker SQL is predecessor evidence
only. These are separate target, migration and caller/publication plans. This
source plan reserves no schema version and introduces no auditor or retention
policy.

## Bounded configs SOURCE behavior oracle, 2026-10-15

The accepted literal capture covers all 118 fields across the 15 config
families for both `actual` and published `fresh` variants. The new unselected
oracle reconstructs those declarations in owned in-memory SQLite connections
and behaviorally samples native identities, affinity and NULL behavior,
AUTOINCREMENT and WITHOUT ROWID layout, uniqueness, CHECKs, internal FKs,
cascades, immutable history and snapshot exceptions. It also exercises the
genuine archived `7dadc492` Store/Email constructor through public category
CAS, runtime Skill activation/load receipts, scheduled configuration interning
and references, caller-owned transaction rollback, and WeChat scope watermark
methods. The six predecessor account blocker predicates are compared with the
current candidate's typed `actions` predicates as declarations only.

Verification in this worktree: `python -m pytest -q
tests/test_configs_source_contract.py tests/test_config_state_native_source.py
tests/test_provider_state_source.py tests/test_storage_scheduled_configs.py`
passed **38 cases in 19.54 seconds**, including **19 new behavior cases** and
the six previously accepted config metadata cases. `python -m pytest -q
tests/test_email_action_children_source.py::test_each_external_in_flight_trigger_blocks_source_mutation`
passed **26 cases in 0.52 seconds**. That predecessor DML gate reconstructs
captured Email action parents/children with narrow account, message and
classification stubs; it is not a genuine full constructor or new configs
target test. Ruff, compileall and `git diff --check` passed on the new files.
On SQLite 3.53.4, the explicit high-water probe returned sequences 700 for
`managed_skills` and 901 for `managed_skill_revisions` and
`runtime_skill_configs` in both variants; ignored invalid CHECK rows advanced
the latter two native sequences. These results pin bounded source behavior,
not universal DML proof for all 118 fields.

The next atomic closure remains separate work: map all 15 config families;
`scheduled_tasks`, `runs.snapshot_id` and `runs.scheduled_task_id`;
`scheduled_task_skill_refs`; `managed_skill_export_receipts` and
`runtime_skill_load_receipts` to typed `actions` references; the approved
empty `email_training_snapshot_observations` FK/trigger retirement; and the
six account blocker predicates against current typed `actions`. Target
declaration/storage, pure migration/inverse, current caller/paired publication,
and actual-copy gates each need their own evidence. This source-only oracle
does not establish target parity, migration readiness, production behavior,
or a release. It introduces no schema version, audit rule or retention policy.

### Allocator assertion SPEC follow-up

The initial allocator check used a lower bound and would accept a higher
`sqlite_sequence` value. The test now requires exactly 700, 901 and 901 for
`managed_skills`, `managed_skill_revisions` and `runtime_skill_configs` in
each captured variant; the shared expectations also require actual and fresh
to agree. A temporary `seq + 1` mutant of the same test passed both variants
with the old assertion (2 passed), then failed both variants with the exact
assertion (`701 != 700`; 2 expected failures). The unmutated allocator plus
accepted config metadata checks passed 8 cases in 0.59 seconds, and the full
new source-contract file passed 19 cases in 14.30 seconds. Ruff, compileall
and diff checks passed. The temporary mutant was removed by its owned
`TemporaryDirectory`; no captured declaration or runtime code changed.


### Independent configs SOURCE acceptance

Fresh SPEC accepted the bounded source implementation after4c8a287d closes the
allocator lower-bound gap; its final independent allocator/metadata run passed
eight cases in0.34s. Fresh QUALITY then independently passed25 new-source/
accepted-metadata cases in12.48s and26 predecessor trigger cases in0.20s,
with Ruff and diff checks passing. Both reviewed the genuine archived7dad
constructor/public callers and the separate declaration-only account blocker
comparison. This accepts the source oracle at4c8a287d, not target storage or
an actual migration. The original38-case adjacent run and exact19-case new
file run remain separate receipts. The118-field figure is metadata coverage;
behavior is sampled by the specified cases. No production, provider, native
Agent CLI, policy or schema version change is part of this acceptance.

### Unselected seven-kind Email configs storage plan, 2026-10-15

`docs/superpowers/plans/2026-10-15-configs-email-storage-plan.md` is the next
bounded, docs-only implementation plan. It covers exactly the seven existing
Email config kinds and 73 captured source fields: four ordinary rowid tables
and three immutable `WITHOUT ROWID` TEXT-primary-key tables. It gives finite
target declaration, source-shaped write API and synthetic actual/fresh parity
tests using the accepted 15-family/118-field metadata capture and 19-case
source oracle. The target keeps raw source JSON TEXT values, native nullable
TEXT keys, signed rowids, typed binding/promotion FKs, partial active-folder
uniqueness, snapshot freeze semantics and six account blockers against current
typed `actions`.

The plan's code fences were extracted into an owned temporary package and
checked without writing `app/`, `tests/` or source artifacts. The isolated
synthetic run passed 73 plan cases across actual/fresh variants, including
the seven-kind constraint matrix, with no production database or native
Agent CLI use. This is plan-code self-check only; no target storage module or
caller has been implemented in this worktree. Eight Skill/schedule/WeChat
config kinds remain outside this first declaration, and final publication
still requires complete 15-kind closure plus schedules/runs, the two receipt
families, empty training-observation disposition, actual-copy and native
acceptance. No schema version, production write, provider call, push or
deployment is selected by this plan.

#### Plan evidence-scope correction

The planned relation test deletes a category and observes its binding cascade
in both literal source variants and the unselected target. It does not delete
an account, so the 73-case plan-code self-check does not prove positive
account→binding cascade. The config-only source fixture omits predecessor
action children referenced by the account's native blockers; source account
DELETE under that fixture raises `OperationalError` for missing
`email_unsubscribe_claims`. Positive source account UPDATE/DELETE and cascade
parity therefore need genuine predecessor action declarations or the full
published constructor and complete reference closure before selection. The
target account FK still declares `ON DELETE CASCADE`. The planned blocker test
uses exact current typed `actions` DDL with narrow classification/task/run
stubs and proves only the six in-flight predicates; it is a focused predicate
fixture, not a full EmailStore constructor. No missing-table exception is
counted as parity success.

#### Plan correction: negative-only ordinary rowids

The seven-kind storage plan's initial `_next_source_rowid` expression clamped a
negative maximum to zero. With only rowid `-7` in an ordinary source table,
native SQLite allocates `-6`, while that initial plan code allocated `1`.
The new actual/fresh differential test reproduced the mismatch in both
variants (`2 failed`, `73 deselected`). The plan now returns `1` only for an
empty kind and otherwise adds one to the signed maximum; its existing
maximum-64-bit-integer scratch-table overflow branch remains unchanged. The
test also checks reuse of `-6` after deletion and allocation of `1` in an
empty table, before inserting any zero rowid. Extracting the corrected plan
code to an owned temporary package yielded `75 passed` in `3.73s`; Ruff and
compileall passed. These are synthetic plan-code checks, not an implemented
target module, source migration or account-cascade acceptance.

#### Unselected seven-kind Email configs storage implementation

Commits `07a5b841` and `573ab35b` add the finite seven-kind `configs`
declaration and caller-transactional source-shaped storage API. The new
`tests/test_configs_storage.py` passed 75 synthetic actual/fresh cases; the
focused command including `tests/test_configs_source_contract.py`,
`tests/test_config_state_native_source.py`, and
`tests/test_email_action_children_source.py::test_each_external_in_flight_trigger_blocks_source_mutation`
passed 126 cases total. Ruff, compileall and `git diff --check` passed. A
direct finite-kind probe rejected `managed_skills`, an empty Email account
payload, and an extra account payload key with `IntegrityError`.

The seven-kind Email `configs` declaration and storage API passed synthetic
actual/fresh differential checks. This is unselected local storage; the final
15-kind target, caller conversion, actual copy, native execution, production
publication and source retirement remain separate gates. The positive source
account UPDATE/DELETE and account-to-binding cascade still need genuine
predecessor action declarations or the full published constructor before
selection; the focused typed-action fixture proves the six current target
blocker predicates only. No Store/Email default constructor selects this
module, and no schema version or migration changed.


#### State caller and transaction source research, 2026-10-15

Bounded read-only review at `4c14dcd7` confirms the captured sixteen state
families and 91 actual fields. All are ordinary ROWID tables, without
AUTOINCREMENT, WITHOUT ROWID, owned triggers or captured incoming/outgoing
FKs. Single-column TEXT PRIMARY KEY fields lack explicit NOT NULL; composite
key columns explicitly require values. This describes source declarations,
not a target constraint decision or dynamic SQL closure.

| Source family | Actual fields | Source behavior to preserve in the one-table conversion |
| --- | ---: | --- |
| channel_login_reservations |3| Channel ownership paired transactionally with service_state login requests |
| codex_session_locks |3| Owner/stale-time acquire, renew, release and restart cleanup |
| conversation_runtime_sessions |5| Conversation/route/session/contract binding, conditional clear and route rename |
| conversations |9| Conversation title/single-chat/native session binding; fresh has five fields |
| corpus_sources |2| Creation found; no current bounded app reader/writer found, not deletion authority |
| daily_scan_state |5| Raw cursor JSON, success/error and update time consumed by scanners/quality |
| dispatcher_claim_leases |10| Owner/generation/expiry CAS, generation increment, terminal ordering by terminal_at and ROWID |
| email_historical_traversals |6| Account/folder/model cursor; UIDVALIDITY generation and durable enqueue transaction |
| email_scan_cursors |6| Account/folder cursor; expected generation, monotonic UID and durable enqueue |
| org_cache_metadata |3| Raw keyed JSON, including principal and HR department IDs |
| org_user_profiles |11| Profile fields/JSON arrays; nonunique name and DingTalk-ID lookup indexes |
| runtime_route_pauses |5| Retry-time expiry and route rename coupled with sessions/schedules/capability |
| seen_messages |3| Global message-ID INSERT OR IGNORE dedupe and recent-conversation ordering |
| service_state |3| Raw keyed values, schema marker, capacity/lease/channel/deploy records |
| setup_wizard_steps |6| Manual confirmation identity/time retained when an update omits confirmer |
| wechat_read_state |11| Empty-value coalescing preserves self identity, watermark and last scan |

The four actual-only conversation handoff fields remain retirement candidates,
not approved value deletion. Zero observed corpus/reservation/lock rows do not
retire the corresponding behavior. No source trigger enforces dedupe, fences or
cursor monotonicity: those rules live in the current writers and SQL predicates.

The schema marker is a bootstrap dependency: Store reads service_state during
construction; store_schema_release reads it before choosing a paired inverse;
ExistingSchemaUpgradeStateStore accesses it directly before constructing the
newer Store. Its one-table conversion must update that pre-construction path,
paired forward/inverse and current callers together, without read-path repair
or a permanent compatibility table/view. Dispatcher direct joins and deploy
quiet checks likewise need conversion with its ledger. The source terminal
prune ROWID tie-break must remain stable through migration and new writes.

The next bounded source oracle should exercise actual/fresh service-state
marker/direct updater access and dispatcher owner/generation CAS plus ROWID
terminal ties, followed by native session, cursor/enqueue, dedupe and watermark
transactions. This review executed no new DML, target schema, migration,
production reads/writes, native CLI or provider calls, and adds no expiry or
30-day history policy. It is not evidence that sixteen families have already
been removed.

#### Positive Email account mutation against published fresh source

The earlier config-only fresh fixture cannot establish positive account
deletion: inserting a valid account and deleting it raised
`OperationalError: no such table: main.email_unsubscribe_claims`. The new
`tests/test_configs_account_source.py` instead uses a temporary SQLite
database constructed by the archived published `7dadc492` AutoReplyStore and
EmailStore. It asserts that the three predecessor action tables and six
account blocker triggers exist before testing the synthetic account.

The test passed a nonidentity account update, rejected a linked account-key
update by native `NO ACTION` on both source and typed target with both rows
unchanged, unlinked the binding, updated the account key, recreated the
binding under the new key, and deleted the account with the binding cascading
on both sides. At each step it compared source/target rowcounts, the complete
fresh account projection and SQLite storage classes, and account/binding
counts. The focused run of the new case plus the 75 storage cases, 19 source
oracle cases and adjacent native/action cases passed `127` tests; Ruff,
compileall and diff checks passed. This is a synthetic published-fresh
constructor proof, not an actual production database copy or a runtime
selection. Actual/fresh config declarations were reviewed separately. No
app/schema/migration/default-caller or source fixture changed.

#### Root acceptance and current production boundary

Fresh independent SPEC followed by QUALITY accepts the unselected seven-kind
implementation `dc15505d`: both independently ran 94 storage/source cases and
Ruff without a finding. They also accept the genuine published-fresh account
closure `9f695659`; SPEC ran 76 cases, QUALITY ran the one new constructor
case. Root separately ran 100 storage/source/native-metadata cases in 32.45s
and the new constructor case in 6.96s. This completes the seven-kind local
storage unit, not all fifteen kinds, caller selection or migration.

Two current production query-only read transactions at 20:07 and 20:09 UTC
on 2026-10-15 expose **121 tables**, Store `2026-10-13.5`, Email51 and logical
size **486,330,368 bytes**. Production checkout HEAD before/after is
`35d1636fd0b7ce83b77642120b62127fe0a3cb5e`; other work has advanced it from
the frozen 7dad predecessor. No deployment was performed by this redesign
task. The latest observations and all 121 per-table data/index totals are in
`superpowers/specs/2026-10-15-production-table-size-readback.json`. The first
observation had 4,096 free bytes and a 4,148,872-byte WAL; the second had zero
free bytes. These are distinct live observations, not an immutable combined
image. At the second observation email_accounts held **two rows/8,192 bytes
including its index**; provider observations held **72,147 rows/24,956,928
bytes including indexes**. Provider account configuration is small; inventory
facts are a separate storage responsibility. The 104-table paired candidate
and its capacity increase remain frozen-copy evidence, not production facts.

#### Load receipt duplicate-body probe and explicit exception

The live per-table receipt also compares all 121 same-named tables with the
frozen source allocation: file and summed table/index net growth both equal
1,118,208 bytes. The largest deltas are runs +552,960,
business_source_documents +217,088 and managed_skill_revisions +106,496.
This attributes allocated page changes to tables only; it does not determine
which input/output content or writer caused each change.

On the unchanged frozen source SHA256 `970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`,
all **4,105** load receipts are successful; their raw TEXT bodies contain
**3,678,270 UTF-8 bytes** and 17 distinct body hashes. Every body exactly
matches the existing public writer's `json.dumps` of the integer Skill-ID to
revision-SHA map for the config's enabled bindings, with `sort_keys=True`
and the original serializer defaults. No body or personal values are
exported. Literal schema/trigger/FK metadata for the two Skill receipt
families and the count/byte receipt are in
`superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json`.
The source hash was checked unchanged after the probe. This is not a current
production receipt scan, target compression or a file-space reclaim result.

The approved duplicate-removal rule can use the referenced config's existing
binding/revision data as the single source, reconstructing successful receipt
JSON in Python and public failures as literal `'{}'`; no additional config
body field is needed. Keep integer keys until the original serialization:
integer IDs 2 and 10 sort differently from their string representations.
DTO/API and inverse bytes must be compared, not just parsed JSON equality.
The current CSV's per-receipt loaded_json disposition needs an explicit
correction in the next bounded compression plan before implementation.

This intentionally cannot claim arbitrary native-SQL history parity. Source
SQL accepts noncanonical or BLOB bodies and permits a late INSERT of another
binding after a receipt, despite prohibiting binding UPDATE/DELETE. Original
receipt bytes stay unchanged in that history, while naive current-map
derivation would change them. Current app writers create the complete config
and bindings together, and no late app binding writer was found. The next
source oracle must preserve that counterexample explicitly. A release-copy
gate must verify exact reconstructability of every receipt and stop on any
gap; do not silently rewrite unmatched bodies or add a new late-insert policy.
Target/inverse, public failure, activation and consumer/console tests remain
open. Neither a fallback body archive nor receipt deletion is implemented.

#### Published receipt source oracle, 2026-10-15

`tests/test_configs_load_receipt_source.py` executes the genuine archived
`7dadc492` AutoReplyStore/EmailStore constructor in owned temporary SQLite
files. The two receipt tables' complete table/index/trigger SQL, column and
outgoing FK metadata equal the frozen **actual** metadata in the probe JSON;
their AUTOINCREMENT sequences advance independently under explicit high-ID
native inserts. This is an actual-to-published-fresh declaration comparison,
not a read of current production rows. The probe's actual sequences and row
counts remain frozen observations, not expected values for the empty fresh DB.

The archived public writer accepts an empty enabled map as literal `'{}'`.
For a synthetic complete config with enabled Skill IDs 20 and 100, its
integer-key `sort_keys=True` serialization yields `"20"` before `"100"`;
string-key sorting would reverse them. A Unicode digest seeded by explicit
native SQL demonstrates the original `json.dumps` escaping and exact UTF-8
body. That setup SQL does not claim genuine public revision validation. The
public config writer creates all bindings together; a disabled binding is
excluded. Repeated successful loads append distinct receipts; list and latest
PID readers return those identities and unchanged TEXT bodies. Missing,
extra and wrong-digest public loads reject without a receipt or status
mutation. A public failed load writes TEXT `'{}'` with the exact error and
PID, changes the config to `load_failed`, and is returned by list/latest.
An invalid public config binding leaves config count unchanged.

Native SQL has a deliberately wider source contract. It accepts a valid but
noncanonical `' { } '` body and can activate its empty config; it also stores
malformed TEXT and BLOB receipt bodies. An activation attempt over malformed
body raises SQLite `malformed JSON` and leaves the config pending. After an
empty public success, native SQL can INSERT a new enabled binding into the
active config. The original receipt body remains `'{}'` while the current
binding-derived map becomes nonempty. The source's UPDATE/DELETE binding
guards do not close that INSERT path. This is an explicit future native-SQL
parity exception: the 4,105 frozen successful production rows were separately
read back as byte-exact derivable, but the schema alone does not establish
that property for every possible or future row. This test adds no target
constraint, fallback body, late-insert restriction or runtime policy.

Development REDs exposed a bad fresh-seed assumption and a malformed embedded
Skill body; the latter was corrected to use the archived public revision
validator. Another RED exposed native activation's SQLite `malformed JSON`
exception, rather than an integrity-trigger error, for the stored malformed
body. The test asserts integer-key output differs from string-key sorting,
preventing that byte-incompatible reconstruction. The final focused run
passed **4 cases in 26.86 seconds**; new plus adjacent config source/storage
and account tests passed **99 cases in 49.50 seconds** before the final
failure-identity assertion was added. The final assertion's focused rerun
passed; Ruff, Python compilation and diff check passed. Target declaration,
migration, inverse and release-copy gating remain separate decisions.

#### Receipt reader readback SPEC correction

Fresh SPEC found that the initial oracle checked `list_runtime_skill_load_receipts`
and `latest_runtime_skill_load_receipt_for_pid` by ID only, so its full-reader
claim was stronger than its assertions. The archived success and failure
scripts now capture each reader's complete DTO and the persisted row's
`id`, `config_id`, `pid`, `loaded_json`, `error` and `created_at`; assertions
compare every field, exact UTF-8 body bytes and SQLite TEXT type. A temporary
captured-reader-body mutation passed the former ID-only success test (1 pass)
and failed the new full assertion on `loaded_json` (1 expected failure). The
mutant was removed, and the unmodified four-case source oracle passed in
31.63 seconds. Each pytest invocation used a new task-owned `/tmp` parent
and `--basetemp`; the parent was absent after the run. This changes tests and
source preparation only, without any target or runtime policy change.

#### Accepted source baseline and bounded storage plan

Fresh sequential SPEC and QUALITY accepted the receipt source oracle at
`ee350913` plus reader correction `8021df4e`; independent QUALITY reran four
cases in 33.25 seconds and removed its owned temporary parent. Comment-only
`842b61e3` did not change executable behavior. This accepts source declaration,
public writer/reader bytes and the explicit native-SQL counterexamples; it does
not select target compression or prove a production copy.

`docs/superpowers/plans/2026-10-15-configs-schedules-receipts-storage-plan.md`
is the next bounded, unselected target-storage plan. It covers all 15 config
families/118 source fields, the single schedule family/17 fields, and both
Skill receipt families/11 fields. The existing field map's one
`runtime_skill_load_receipts.loaded_json` disposition now specifies exact
derivation from existing config bindings and revision SHA in one database
snapshot, with literal `'{}'` for public failures. No body is added to a
config, action payload or shadow table. The future copy must reject any row
whose original body cannot be reconstructed byte-exactly; source native SQL
still permits counterexample histories. This plan changes no selected runtime,
schema version, migration or real database.

The plan's complete schedule module code was extracted into task-owned
`TemporaryDirectory` prototypes. Before those modules were written, the two
focused schedule tests failed on the missing import; after extraction they
passed (2 cases, 0.02 seconds). A second disposable differential probe
reconstructed both literal actual and genuine fresh `scheduled_tasks` DDL,
inserted all 17 fields, and matched every value and SQLite storage class to
the plan's target DDL. Three constraint cases per variant (duplicate partial
`migration_key`, invalid `enabled`, invalid `version`) matched. Target
`runtime_options_json` accepted BLOB and capabilities accepted non-JSON TEXT,
as the native source does. The extracted modules passed Ruff; selected
standalone plan code blocks compiled, and the CSV still has 1,611 rows with
only the one approved disposition correction. Both owned temporary parents
were absent after closing. This checks the schedule prototype and plan syntax,
not the future complete config/action integration, migration or actual data.

The receipt schema fragment was also compiled in a private SQLite syntax probe:
11 generated fields, fifteen CHECK/FK clauses, three indexes and six triggers
installed without a parser error. Its minimal syntax carrier is not a complete
Email/action/config fixture or a receipt DML parity result. Its temporary parent
was absent after closing.

The plan's complete eight-kind synthetic row set covers all 45 remaining
config source fields. Each row and its real schedule parent inserted without
error into both literal actual and genuine fresh in-memory source schemas.
This is a source-fixture readiness check; target eight-kind DML and typed
action integration remain unimplemented and unverified.

### Complete unselected target prototype and plan preflight (2026-10-15)

The later integrated prototype supersedes the isolated schedule/schema syntax
probes above. In task-owned `/tmp/configs-target-proto.gyuWUJ`, a copied app
package and focused test files contain the complete unselected 15-kind
`configs`, real one-kind `schedules`, and flagged two-kind Skill receipt
`actions` declarations/storage. The applyable 12-file patch is embedded in
`docs/superpowers/plans/2026-10-15-configs-schedules-receipts-storage-plan.md`;
its SHA-256 is
`9edd3b5df518138f07a1079d94f33800d3c1393ad480216f18cc743abc1a4967`.
The twelve prototype Python files, hashed as ordered `path\0content\0` pairs, have SHA-256 `346142d15949fbaef53c44fc22139ff0aee461187ee6330acbd1347cb61eefdf`. `git apply --check` passed against this worktree. The prototype and patch are
retained as implementation input through independent SPEC/QUALITY review and
the subsequent implementation handoff, on the parent task's request; after
that implementation is fixed, the task-owned temporary parent must be removed
and its absence asserted.

The prototype passed 31 focused integrated cases (numeric SQLite FK affinity
including `'01'`/`'1.0'`/`1.0` and BLOB rejection, mutable integer-alias
rekey, schedule 17-field CAS delete, exact receipt body bytes and independent
sequences) plus three eight-kind source/target cases covering all 45 remaining
source fields in actual and fresh variants. Existing seven-kind tests passed
75/75; the positive published account case passed 1/1; the accepted genuine
receipt source oracle passed 4/4. Ruff passed on the twelve patched Python
files; focused `compileall` passed. The original default actions table DDL,
receipt variant DDL, and `_current_objects()` SHA-256 values matched the
unmodified app exactly. The positive account fixture's historical archive was
resolved in the prototype by a **temporary-only** path adaptation; the patch
does not change that repository path. This is target prototype verification,
not production copy, migration, public caller selection, real DB readback or
native-SQL parity for the four recorded receipt counterexamples.

Fresh SPEC on the first integrated plan found two source-fidelity gaps. In the
retained complete prototype, twelve new controls failed before repair and
passed after repair: two actual/fresh receipt public-reader cases compare
source Store `int()`/`str()` hydration of legal BLOB-in-TEXT fields; ten
actual/fresh cases cover `MAXROWID` in the three config and two receipt
AUTOINCREMENT streams, comparing native `SQLITE_FULL` code 13/name, whole
caller-transaction and savepoint rollback, and pending-marker disappearance.
The complete focused target/config suite passed 109 cases (31 integrated,
3 new-kind differential/metadata, 75 original seven-kind); the published
account positive and accepted receipt source oracle passed another 5 cases.
Normal 700/901/901 stream boundaries stayed green. Only native `SQLITE_FULL`
rolls back caller-owned transactions here, mirroring source plain INSERT;
ordinary source-shaped writes retain the caller transaction. This adds no new
review, safety or authorization behavior.

### Native self-reference correction in unselected prototype (2026-10-15)

Fresh QUALITY review found that the native actual and published-fresh source
schemas accept a plain `INSERT` whose `managed_skill_revisions.parent_revision_id`
or `runtime_skill_configs.parent_id` equals the row's own source ID. The first
target prototype looked up that parent before inserting the row and rejected
all four family/variant cases with a foreign-key error. Four new differential
tests failed on that prototype and passed after the repair. Each test also
checks a missing parent remains rejected, numeric-looking BLOB child keys are
rejected, a NULL parent remains accepted, the full source projection matches,
and `pragma foreign_key_check` is empty. A deleted physical row leaves
`sqlite_sequence` ahead of `max(configs.id)` before each self-insert, so the
physical pointer follows the AUTOINCREMENT high-water mark.

For this self-reference only, the helper now applies SQLite INTEGER affinity
to the child key and supplies the next physical `configs.id` as a scalar
expression in the same `INSERT`. SQLite still assigns the implicit
`AUTOINCREMENT` row ID and enforces the typed immediate FK. The full focused
five-file target/config run passed 118 cases, including the prior 109 target
cases, four new self-reference cases, and five accepted adjacent source cases.
The refreshed twelve-file patch passed `git apply --check` and Ruff on the
changed helper and test. Its SHA-256 is
`516bd969601a9b6b0134e96a59648e660ce420b7ce030ee7429d80916c05e99f`;
the twelve prototype Python files, hashed as ordered `path\0content\0`
pairs, have SHA-256
`a0b9c22055399ff832159da4e0ac710811f5e6ee84697b01c216175343753d7c`.
The plan's authoritative embedded patch has been refreshed to those bytes.
This remains a retained, unselected prototype and documentation input; no
repository application or test files, caller, migration, real database,
provider, native CLI, main, push, or deploy were changed.
### Repository implementation of accepted complete storage patch

Root applied the canonical twelve-file patch with SHA-256
`516bd969601a9b6b0134e96a59648e660ce420b7ce030ee7429d80916c05e99f`
after SPEC acceptance and independent QUALITY acceptance of its self-reference
repair. The delegated implementer had stopped at a claim because of a usage
limit; inspection confirmed no application/test changes before root applied it.

The actual worktree passed 132 focused cases across target integration,
eight-kind source differential, original seven-kind storage, genuine archived
account closure, receipt source oracle and Email action writes in 31.74 seconds.
Ruff on all twelve patched files, focused compileall and diff checks passed.
The newly owned pytest parent was removed and absence verified. An independent
old-module versus current-module comparison found identical default-false
schema objects in 112 flag/variant evaluations (including repeated flag sets).
This verifies the existing action declaration paths remain unchanged by the
new opt-in receipt extension. No current constructor selects the new storage.

All fifteen config kinds, real schedules and two Skill receipt kinds now exist
as repository storage code. Caller conversion, release/inverse, actual-copy
reconstruction checks and physical retirement of the eighteen old source
families remain incomplete. No production database, provider or native CLI
was touched by this unit. The retained author prototype remains an active
implementation/review input until final implementation acceptance.
