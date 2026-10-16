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
### Source-bound row transfer and current-fact inverse

`app/configs_schema_transfer.py` implements the row conversion needed by the
future atomic config/schedule release. It covers exactly eighteen source
families and 146 existing fields (118 configs,17 schedules,11 receipts).
Metadata contains only frozen actual/fresh declarations; it contains no row
snapshot, document body or copied trajectory. Forward maps typed physical
references before insertion, including forward/cyclic source parents, while
retaining public identities and hidden signed ROWIDs. Inverse reads current
target facts into installed empty native declarations. Both directions retain
all six AUTOINCREMENT waters and leave transaction completion to the caller.

The initial missing-module RED was followed by eleven focused synthetic cases;
the inverse-FK regression also failed when its comparison was removed in a
transient in-process control. Two additional actual/fresh same-connection tests
first failed because the runtime validator correctly requires old source tables
absent. Transfer now compares the exact target action objects directly while
the pre-retirement transaction still has source tables; runtime validation is
unchanged. All thirteen transfer cases and thirty-five integrated storage cases
passed together: **48 passed in 5.47 seconds**. Ruff, compileall and diff checks
passed, and owned pytest temporary roots were removed with absence checked.

Independent read-only review passed the initial ten-case engine, independently
running those ten tests, and subsequently accepted the bounded inverse FK and
same-connection corrections. It did not claim full release acceptance.

The final executed module SHA-256 is
`9fcf96b0e4bd226a2b60ec98602ab118e25dedcf0410c422b8d89877bb35bbc3`.
On immutable frozen970a source, all **6,823 rows** across all eighteen families
copied into in-memory targets and restored into in-memory original declarations.
The 4,105 load receipts reconstruct byte-exactly and the 1,939 export receipts
preserve their native values. Both quick checks returned `ok`; exact row/value/
storage-class/ROWID checks and six sequence comparisons passed. Source was
opened mode=ro/query_only and its whole-file hash remained unchanged.
`superpowers/specs/2026-10-15-configs-row-transfer-actual.json` records only
counts, waters, executed hash and scope. The final run took5.802seconds; timings
from earlier runs varied widely, so this is not a speedup claim.

This unit does not rewrite `runs` FKs, retire empty training observations,
rebuild existing action rows, drop the eighteen native families, install schema
markers or convert current callers. Its target requires the current Email
action scaffold; follow-up/sync kinds are independently optional. Their own
caller/release paths remain unselected and are still required for the final
ten-core-plus-ledger release.

Fresh sequential review closure for `3fbbbd07`: after the independent
source-fidelity/delta review above, a fresh QUALITY reviewer inspected the
committed engine, projections and evidence, independently passed all thirteen
transfer cases and the combined48-case set, and verified the exact executed
module hash and clean worktree. No concrete blocker was found in this bounded
row engine. No additional production, caller or table-retirement claim follows
from those review passes.

### Skill receipts compose with the existing Email scaffold

The initial Skill opt-in guard unnecessarily required the unrelated follow-up/
sync opt-in. Source inspection and an independent four-variant SQLite DDL
probe found no dependency in Skill columns, FKs, checks, indexes or triggers.
The guard now requires Email (which already requires reply storage), while the
transfer engine accepts either exact Email+Skill declaration with or without
follow-up/sync. No constructor selects either declaration yet.

Two new behavioral controls failed on the old guard and pass on both actual
and fresh reply variants after the fix. They copy a real native config and
load receipt into the no-follow-up declaration and assert no follow-up kinds
are present. An incomplete first fixture lacked unrelated core parent tables;
it was corrected to install them rather than weakening storage checks.
All fifteen transfer, thirty-five integrated and seventeen follow-up storage
cases pass:67 in4.08seconds. Ruff/diff pass and the owned pytest parent is
removed. Comparing all56 valid pre-existing flag/variant combinations with
Skill opt-in false found byte-identical objects. The frozen proof recorded
above remains explicitly tied to the3fbbbd07 module; it is not a new no-sync
release proof. No source table, marker or production path changed here.

Independent source-dependency audit and subsequent fresh QUALITY review both
accepted this bounded decoupling. QUALITY verified the exact diff and the
independent fragment composition; it did not repeat the67-case run. The test
checks one representative follow-up kind absent in DDL; all nine absent kinds
also follow from the reviewed conditional declaration assembly.

### Config row transfer after the existing Email migration

The paired Email13 conversion replaces six account-owned blocker triggers
with their existing typed-action definitions. The eighteen retained config,
schedule and receipt families therefore have a different exact declaration
fingerprint after that conversion. The row engine now recognizes the actual
and fresh variants of this real source state as well as the original sources;
a partial mixture of old and translated blockers is rejected. Fresh projection
order remains source-specific. This adds no runtime schema selection.

The two added source-profile round trips failed on the previous engine. The
updated eighteen transfer and thirty-five integrated cases pass (53 in 2.94s).
On a new owned copy of the unchanged frozen 970a source, the existing Email,
Business and Provider conversions ran first. Capture then selected
`actual_email52`; all 6,823 rows across eighteen families and 146 source fields
copied to installed in-memory targets and inversely restored with exact
values/types/rowids and six allocator waters. Both quick checks returned `ok`.
The owned copy was removed and its absence verified; the source SHA remained
unchanged. The metadata-only receipt is
`superpowers/specs/2026-10-15-configs-email52-row-transfer-actual.json`.

This proves sequential row-engine composition after the existing migrations.
The copied candidate still has 104 tables. It does not prove retained-runs FK
translation, source-table retirement, current constructors/callers, paired
markers or a complete current-fact release rollback. Those remain required
before the final ten-core-plus-ledger schema can be selected.

Independent SPEC review reproduced all four source hashes from the original
declarations and existing six-trigger translation; all 62 partial combinations
per base profile were rejected. Subsequent fresh QUALITY review found no
blocker and verified all eleven executed module hashes. Its combined check had
51 passes and two import failures in a minimal interpreter; precisely those
two cases passed in the existing provisioned test environment. All 53 cases
therefore passed across those bounded runs. All reviewer-owned temporary
parents were removed and their absence checked. Ruff, syntax parsing and
`git diff --check` also pass. This is acceptance of source binding only.

#### Unselected retained-runs FK translation

`app/configs_run_schema.py` now validates the exact full current Workbench
`runs` table, index and trigger declarations. Under the caller's FK-off
transaction it rebuilds `runs` with only two changed FK targets:
`scheduled_task_id` to `schedules(id)` and `snapshot_id` to the full-unique,
kind-restricted `configs(schedule_snapshot_public_id)` alias. The alias is
derived from the existing numeric public key for scheduled config versions;
no new config business content is stored. Forward and inverse preserve the
physical runs rows, public values/storage classes, all other declarations and
`runs` plus `runs:<kind>` sequence waters. An unrelated synthetic
`workbench_events` to `tasks` orphan, including its binary payload, remains
unchanged and is not treated as a translated-edge rejection. Inverse reads
current facts after conversion, including newer writes. Focused synthetic
actual/fresh tests pass;
native parent retirement, caller selection and copied actual-data proof remain
separate work.

### Retained-runs actual-copy correctness and measured cost

Independent SPEC and subsequent fresh QUALITY reviews accepted the bounded
`5fec73f3` translator; each independently passed its twelve tests. A new owned
copy of the unchanged frozen 970a source then underwent the existing Email,
Business and Provider conversions. Its 231,340 runs and all 41 run declaration
objects matched the current source. Fifteen schedules and sixty-nine schedule
snapshot facts were installed in unselected target parents; native parents
remained available for the inverse. This was a retained-child proof, not a
complete config release or source-table retirement.

Forward preserved the exact row values/types digest and sequence watermark.
One raw local `insert_run` appended a scheduled run with FK enforcement on;
inverse retained all 231,341 current rows, their values/types digest, physical
IDs and updated sequence. The original unrelated runs/tasks orphan remained
the only runs FK finding in all three states. Full quick check returned `ok`.
The owned copy was removed and absence checked; original source SHA unchanged.
See `superpowers/specs/2026-10-15-configs-retained-runs-actual.json`.

Correctness passed, but elapsed time was 736.176 seconds. A process-memory
observation showed 2.3 GiB peak physical footprint and about 1.2 GiB swapped;
a one-second sample showed Python dictionary/tuple comparisons dominating its
samples. This is not a controlled benchmark, but the full Python row/type
snapshots are a concrete cost that must be corrected before adoption. A
SQLite TEMP-staging prototype is under review; no new runtime path is selected.
The attempt to interrupt only the owned test process was rejected by automatic
approval (approval required, AskForApproval Never); it was not retried through
another tool, and the test subsequently completed naturally and cleaned up.

### SQLite-staged retained-runs implementation

The reviewed staging prototype replaces the two full Python row/type matrices
with an ephemeral SQLite TEMP table over all 95 stored columns. Before source
drop and after explicit-column INSERT SELECT, row counts and indexed ID joins
check every value with IS and every SQLite typeof. This retains the existing
exact declaration, allocator, two-edge FK, unrelated-orphan and current-fact
inverse contracts. Successful transfer drops staging; the caller must roll back
its transaction on any exception. There is no durable row archive.

SPEC found a real mixed-case TEMP-name regression in the prototype's IN/COLLATE
placement; four failing cases became passing after applying NOCASE to the name
operand. Direct BLOB/NUL readback assertions strengthen the special-type tests;
the original escape literals already represented binary/NUL input. Re-SPEC and
subsequent fresh QUALITY accepted module SHA256
`3d911e9ffc223ba4ad1579ce87d7271f6f806cd8c9191657774f1bc94c79881d`.
The repository adopted that exact module and the same regression functions on
the existing captured-source fixture. All 148 selected cases passed in 45.18s;
Ruff and diff checks pass, and the owned pytest parent is absent. A new actual
copy proof is still needed; the old 736-second result belongs to the earlier
Python-materialized module and is not evidence for this implementation.

### SQLite-staged retained-runs frozen-source acceptance

The first combined rerun was interrupted without a final receipt. On resume,
its process handle was missing and no corresponding Python process or open
copy handles remained. That stopped, task-owned candidate was inspected and
removed; no success is inferred from its intermediate database files.

A new owned copy then tested the exact canonical runs source directly. This
bounded run does not repeat the independent Email/Business/Provider conversions;
the full coordinator still needs combined release acceptance. All 231,340 runs
and their 95 stored columns preserved their independent streaming typed-row
digest and physical sequence. A raw local scheduled-run insert with FK on was
retained by inverse, yielding 231,341 current rows and the updated allocator.
All 41 native run objects were restored. The same pre-existing runs/tasks FK
finding remained in source, forward and inverse. Quick check returned `ok`,
TEMP staging was absent after each direction, the owned database directory was
removed, and frozen source SHA remained unchanged.

Forward took 2.930s and inverse 2.955s in this observation; the entire bounded
proof took 15.562s. Peak resident-set usage reported by Darwin getrusage was
60,047,360 bytes. This is not a controlled comparison with the earlier
736-second combined proof or its vmmap physical-footprint metric: both scope
and measurement differ. Exact executed hashes and stage times are recorded in
`superpowers/specs/2026-10-15-configs-retained-runs-streaming-actual.json`.
The reviewed prototype directory was removed by its author and absence checked.
At that proof stage no runtime constructor or marker was selected and native
source parents remained. The unselected coordinator below subsequently added
synthetic source retirement; caller selection is still open.

### Unselected full config schema coordinator

`app/configs_schema_release.py` now composes the accepted row transfer and
retained-runs translator in a caller-owned FK-off transaction. It binds the
finite native eighteen-family declaration, independently binds the exact
Email29 action variant, and checks the actual/fresh declaration of the empty
training-observation table. The paired path would invoke it after Email13, so
its supported complete source profiles are `actual_email52` and
`fresh_email52`, each with an independently matched action reply variant.
The standalone row engine still supports four native config fingerprints.
An original config profile combined with Email29 is an invalid synthetic full
source: its old account blocker triggers still refer to the retired Email
action tables, and the release preflight refuses that mixture.

Forward installs configs/schedules, stages old actions in SQLite TEMP, rebuilds
only the Skill receipt extension, copies eighteen families and six source
allocator waters, maps the two real Workbench `runs` FKs, and retires all
eighteen old tables plus the exact empty observation table. Its final predicate
requires those nineteen names absent, exact configs/schedules, the 31-kind
Email+Skill action declaration, and mapped real runs. Inverse recreates source
declarations from metadata, reconstructs current target facts and allocator
waters, restores native run FKs and 29-kind actions, and removes only synthetic
config/Skill allocator aliases. It leaves rollback to the caller and neither
direction changes Store/Email markers or current constructors.

The focused synthetic suite covers both post-Email13 config fingerprints
crossed with the two independent action variants, post-forward
schedule/config/run and Skill export/success/failure plus non-Skill action
writes, exact native DDL on inverse, second forward, unrelated run orphan
preservation, nonempty observation and noncanonical receipt refusal,
changed/mixed declarations,
inverse broken-target FK refusal, and savepoint rollback after a mid-copy
failure. This unit has no real database, provider, native CLI or production
effect. Caller integration, actual combined-copy proof, and publication are
separate work.

Additional regression-first preflight coverage rejects an empty external child
FK to the retired observation table, including mixed-case SQLite parent names;
external views and triggers that refer to any retired source table or either
target table being dropped on inverse; and MAIN/TEMP name shadows before any
schema change. The SQL dependency check reuses the existing SQLite schema-only
rename parser. Both directions still leave transaction rollback to the caller.


### Full unselected config coordinator: independent review and frozen-copy proof

The preflight fix `a9b66a45` passed independent SPEC and fresh QUALITY reviews,
each with 35 focused tests and additional in-memory probes. The combined
frozen-copy receipt is
`superpowers/specs/2026-10-15-configs-full-schema-actual.json`.
It applies the existing Email, Business and Provider conversions, then the
config coordinator against the genuine `actual_email52` declaration.
All 18 native families (6,823 rows) and the empty observation table retire;
configs/schedules and the two Skill receipt kinds replace them. The intermediate
candidate has 82 ordinary tables under `PRAGMA table_list` excluding virtual,
shadow and SQLite internal tables; this is not the final ten-table target or a
production count.

The first config inverse exactly restores native rows/storage classes, source
DDL, independent allocator waters and all 61,310 retained action / 231,340 run
rows. A second forward followed by copy-only writes verifies seven additions
on config inverse: schedule, configuration version, run, ordinary action and
Skill export/success/failure receipts. Retained counts become 61,311 and
231,341. The subsequent whole Email/Business/Provider inverse restores the
initial main schema and unchanged markers; quick_check is ok and the sole
pre-existing runs-to-tasks FK finding remains unchanged. New-fact checks occur
after config inverse; this proof does not claim prior-code constructor or
current-caller verification after the whole inverse.

The run completed in 137.278 seconds. The immutable source file hash is
unchanged, and both the disposable copied database and proof workspace were
removed with absence verified. No provider, native CLI, runtime selection,
version activation, real send, push, merge or deployment occurred. The next
work is current callers, fresh initialization and paired release integration;
this helper acceptance does not finish the complete redesign.


### Current callers and cold copied-source acceptance, 2026-10-16

Independent SPEC and fresh QUALITY accept exact `6515d77f`. Their follow-ups
correct wrong-owner object-name repair collisions, preserve native duplicate
WeChat selection last-write-wins, and generate canonical CHECK declarations in
stable field order across processes. No weaker schema comparison or fixed
hash seed was introduced.

Root actual-source acceptance passed in244.045 seconds, exit0. It used a new
SQLite-backup copy of frozen970a and genuine archived7dad code, with two real
cold publication subprocesses. Current public writes cover Skill versions,
export and runtime load success/failure receipts, disabled schedules/pending
runs, disabled fake Email accounts, duplicate WeChat scopes and nested receipt
reads. Whole inverse preserves those current facts, restores the full original
schema SQL, and allows genuine old code to cold-read and write further facts;
reforward retains both generations. Config18 values/types/ROWIDs/six allocator
waters and original records in77 unrelated ordinary tables (including original
actions/runs) compare exactly using native joins. quick_check remains ok; the
one original runs190042-to-tasks FK finding is unchanged. The metadata-only
receipt is `superpowers/specs/2026-10-16-configs-current-cold-copy.json`.

The copied database/code/archive were removed with absence checked and source
hash unchanged. The outer proof-script directory was also removed after
recording evidence, with absence verified. No native Agent CLI, provider or real send ran. Target82
ordinary tables is an intermediate result; this does not complete ten-core-table
plus ledger consolidation, full CI, latest native lifecycle or publication.
Only aggregate elapsed time was captured, not per-phase timings. Two initial
proof-preparation interface mistakes (missing caller read transaction and the
Business parents metadata key) were corrected before publication; application
code was unchanged during this acceptance.

### Bounded current fixture closure, 2026-10-16

The ten selected current/source test files initially had 50 failures and 103
passes. Current fixtures still queried or mutated retired `email_accounts`,
`email_category_configs`, `email_training_snapshots`, `scheduled_tasks`, and
`scheduled_task_config_versions` tables, or expected the empty retired training
observation table. Their current setup now uses the accepted typed Configs and
Schedules projections/writers. Account-change readback, queued snapshot, seed
rollback, private training-body, Attention cost, and current corruption
assertions remain executable. The archived Email51 seed and 2773 validator
source oracles remain separate and unchanged.

The 2773 validator helper's source has `fresh_email52` Config declarations but
an `actual` training-observation declaration (fingerprint
`002c18708d22a42212460b9d7c35b5dd5943a396386c5346a50aa06b4c87b587`).
It correctly fails paired release preflight, which requires the fresh
observation declaration. Validation tests therefore keep that genuine old
database only as their source oracle and rebuild a separate owned target copy
through the accepted Email, Business, Provider, and Config transfer primitives.
The Config copier verifies all eighteen families' row values, SQLite types,
source identities, and foreign keys; the fixture checks the observation family
is empty before retiring it. This is synthetic test setup, not paired publisher
acceptance. The supported frozen-source cold publication and inverse have their
own receipt at `superpowers/specs/2026-10-16-configs-current-cold-copy.json`
(commit `18657800`).

The cancellation-preview test now copies that typed current fixture, constructs
a real `EmailStore` on the copy, calls the public read-only preview, and compares
both SQLite dump and file bytes before/after. Its original
`prepare_database_schema_release`/`publish` call cannot accept the mixed 2773
source and is no longer exercised by this test; the supported-source cold
publication receipt above covers that separate release path. The current
fixture's Email52 and Store marker are confined to its owned synthetic target.

Final exact ten-file pytest: **153 passed, 1 Starlette deprecation warning in
59.36 seconds**. Scoped Ruff, compileall, and `git diff --check` passed. No
application/schema/validator code, real database, provider, native CLI, send,
push, main merge, or deploy changed here.

### Source-phase installer test correction, 2026-10-16

Fresh SPEC review of `98297c3d` found that a neutral metadata test had
replaced `create_email_action_external_triggers` with a test-local copy of the
current runtime loop. The test could pass if the production installer never
ran. The corrected test separates two contracts: a typed current target checks
both action variants against `EmailStore._validate_schema_shape`, including
missing and malformed retained plan-switch blockers; a genuine native
source-phase parent layout calls the production
`create_email_action_external_triggers` helper for transaction rollback,
idempotence, and nonrepair of an existing malformed blocker. The current
runtime's inline installer loop is separate and is not claimed to be tested by
the source-phase helper case.

The four split cases pass. A negative control that replaced the real helper
with a function that raises failed both source-phase installer cases (2 failed,
15 deselected); the unmodified validation file passed 17/17. The exact ten-file
rerun passed 155/155 in 58.75 seconds with the same Starlette deprecation
warning. Scoped Ruff, compileall, and diff checks passed. No application logic
or accepted source fingerprint changed.


### Independent current-fixture acceptance, 2026-10-16

Sequential SPEC re-review and fresh QUALITY accept exact `00a4fd4f`, covering
the test-only `98297c3d` and production-installer coverage correction. The
independent QUALITY exact ten-file matrix passes155 cases in61.09 seconds,
with only the existing Starlette BlockingPortal deprecation. Ruff, compilation,
diff checks, final clean worktree and owned-temp absence all pass.

SPEC independently observes eight real installer calls across four split cases.
A helper that commits prematurely and one that replaces existing triggers each
make both source-phase tests fail. Current target manifest validation remains
separate from those production-helper assertions. Genuine2773 source oracles,
original business/corruption/readback checks and the real-constructor public
readonly preview remain; unsupported-source publisher coverage is not claimed.
The separate supported-source cold publication receipt remains unchanged.

Metadata is `superpowers/specs/2026-10-16-configs-current-fixture-acceptance.json`.
No app, schema, validator, actual database, provider, native CLI, send, main,
push or deployment changed in this unit. Full CI, remaining harnesses/native
fixtures and complete ten-core-plus-ledger consolidation remain outstanding.


### State source behavior and next physical merge, 2026-10-16

The metadata-only preflight receipt is `superpowers/specs/2026-10-16-state-native-behavior-preflight.json`. It preserves previously executed native actual/fresh identity/CHECK probes, three genuine7dad public lease baseline cases and the frozen970a default/null conversation-field probe. These remain source-only evidence; no state target or retirement is selected.

Current code closure has now been independently read across all16 state families: Store/Email compound mutations, dispatcher adapters, quality/Attention, processing reactions, quiet/deploy progress and active publication-marker readers. Native service_state marker migration and fresh CREATE/ALTER/backfill must move in the same atomic cutover. Existing ownership, CAS, rowid tie order, NULL/affinity domains and transaction boundaries must remain; no incidental policy tightening is authorized.

The source-contract implementation plan is `superpowers/plans/2026-10-16-state-source-contract-plan.md`. Its complete disposable prototype passes172 native cases; NULL-key, affinity and CHECK mutants each fail two intended cases. It reuses unchanged literal source metadata/fixtures, creates no app target and carries no production/native-CLI acceptance. Proposed temporary files were removed. The next action is committing that executable source oracle and independent review, followed by one state16-to1 storage/caller/release plan.

Reusable copy/native harness updates remain a final integration requirement; they will be wired to the final selected table set rather than repeatedly adapting intermediate table-count literals. Existing config frozen-copy and fixture acceptance are retained at their recorded scope. The full ten-core-plus-ledger goal remains open.


## State native oracle implementation handoff, 2026-10-16

The mechanical worker handed off the canonical test and its claim with no process remaining. Root completed the remaining syntax, Ruff, exact plan/file equality and diff checks, and commits the handed-off work explicitly. The test SHA256 is `433f02ecc261457de3836c9b1282654be0e75aadc35579d150dade51f43c7f8c`; the accepted source metadata and reconstruction fixture are unchanged.

Worker verification:172 new native-source cases plus6 original metadata cases pass (178/1.06s); three process-local SQL mutants each fail exactly two intended actual/fresh cases with170 deselected. Root independently reran the same178 cases successfully (1.04s) and checked canonical byte equality before handoff; final Ruff, syntax and diff checks pass. Disposable pytest roots were removed. This evidence binds Python3.13.12/SQLite3.50.4 at the recorded project interpreter; earlier3.53 briefing text is not this run's runtime.

This is a source-only oracle for all sixteen families and actual91/fresh87 named columns. Fresh SPEC then QUALITY review remain open. No target `state` table, caller selection, retirement, schema release, actual database writes, provider/native CLI/send, main merge or deployment is selected by this commit. The full ten-core-plus-ledger goal is incomplete. Root's separate finite-REAL preflight receipt records a current Configs synthetic losslessness gap that must be fixed before reusing its codec for State.


## State native oracle independent acceptance, 2026-10-16

Fresh SPEC then fresh QUALITY both accept exact`50fbd70a3eaa89e67e36b9f646ce4bebc1dc7ae6`. SPEC independently passes178 cases/1.27s and each of three mutants produces its exact two intended failures. QUALITY independently passes178/1.02s plus Ruff/diff/canonical equality. Both verify unchanged test, source fixture and captured metadata hashes, remove owned pytest roots, and leave a clean worktree. The bounded receipt is `superpowers/specs/2026-10-16-state-source-oracle-acceptance.json`.

This closes only the literal state source oracle. Target schema/caller cutover/current-fact inverse remain unselected. The newly confirmed finite-REAL gap is scoped for correction on unpublished native-source kinds while preserving the already-published Reply declaration and payload contract. Full10+ledger and final copy/native acceptance remain open.


## Exact native-source finite REAL implementation, 2026-10-16

The unpublished Email/Business/Provider/Configs/Skill/Sync native-source writers now encode a finite REAL as a signed binary significand and exponent; their generated readers use SQLite built-in `pow`. This preserves source REAL bits and SQLite TEXT/INTEGER affinity, while keeping existing BLOB/nonfinite/scalar cases. SQLite JSON, `unhex`, and math functions are explicit requirements. Published Reply encoder, payload, generated decoder/check, insert/update operations, declaration, and frozen source fingerprints remain unchanged; only the Provider target fingerprint follows its changed target declaration. Native-source inverse still consumes current decoded facts. State remains unselected.

On the unmodified application, the exact Configs reproducer failed four actual/fresh cases through value mismatches (two REAL, two TEXT-affinity), with13 deselected in0.35s. The paired test initially hit the existing Email attempt-count drift trigger and Provider important 0/1 CHECK. Its valid replacement uses Email plan confidence and Provider JSON-valid important_signals text. The final 17 new cases pass in11.99s, including 5,010 finite binary values, malformed tags, and actual/fresh paired forward/current-write/inverse with schema and quick checks.

Focused matrix A passed425, skipped6 in217.56s before that Provider-only paired-test refinement; no app code changed afterward. The final 17-case module was rerun after refinement. Focused matrix B passed715 in57.66s. The process-local old-encoder negative control restored the same four Configs value mismatches with13 deselected in0.42s; it changed no file. Ruff passed on all11 changed Python files; their source text compiled, `git diff --check` passed, five published Reply function ASTs and Reply release module matched the baseline, and the Provider source hashes stayed frozen. These are isolated synthetic tests only: no actual database, provider, native CLI, network, production execution, main merge, push, or deployment. Independent SPEC/QUALITY and root copied-source verification remain open.


## Exact native scalar independent and actual-copy acceptance, 2026-10-16

Root accepts exact`ba01727f32edace3233102998c33719a2c892f18` after sequential fresh SPEC and QUALITY. SPEC independently passes31 cases/13.48s and exact baseline installed Reply schemas for both variants. QUALITY passes17/14.22s plus12,584 finite-value/binary-boundary probes and7 additional malformed-tag refusals; its declared69/72 object counts exclude the implicit sqlite_sequence included in SPEC70/73. Published Reply function bodies and frozen source hashes remain unchanged; Provider target fingerprint matches its regenerated declaration.

The root-owned final frozen970a copy rehearsal passes in210.875s: two real cold publication subprocesses, exact current Configs REAL and TEXT values, Provider direct-update TEXT rendering, newly written Reply payload bytes, exact whole source schema inverse, genuine7dad cold Store/Email public reads and service-state writes, then reforward/current cold reads. The target actual name set equals the82-name intermediate inventory. Native BINARY IS plus typeof joins verify original retained78 tables on target and all116 original tables on inverse with exact predicted row-count deltas. Provider two native compound-key tables and Email effects/direct-retry use their approved composite identities; their unretained hidden ROWIDs are not falsely treated as public identity. Only the target Store marker changes under the exact paired marker protocol; inverse restores original marker values/timestamps.

Earlier rehearsal failures corrected only the proof's ledger/marker expectations, a synthetic scope enum, and already-approved natural-key versus hidden-ROWID identity; the application commit did not change. A separate comparer smoke probe proves the target marker exception does not hide an unrelated service-state mutation. Final quick_check is ok; the original runs190042/tasks FK finding is unchanged. Frozen source SHA remains970a909a...566320; all owned copied DB/old-code/probe roots were removed and absence verified. No provider, native Agent CLI, real send, production write, main merge or deploy occurred.

Metadata receipt: `superpowers/specs/2026-10-16-native-scalar-codec-actual-copy.json`. This closes the exact native scalar prerequisite only. State target/storage/caller/release integration, remaining physical table consolidation, final reusable copy/native/CI gates and approved publication remain open.


## Unselected State foundation implementation, 2026-10-16

The three complete Python blocks in `docs/superpowers/plans/2026-10-16-state-foundation-plan.md` were extracted byte for byte as `tests/test_state_storage.py`, `app/state_definitions.py`, and `app/state_storage.py`. The test-first collection run failed as expected with `ModuleNotFoundError: No module named 'app.state_definitions'`. After the two application files were added, the exact focused command using `PYTHONDONTWRITEBYTECODE=1`, Python 3.13.12 and SQLite 3.50.4 at `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python` passed **324 cases in 14.55 seconds**: 135 State foundation, 172 literal source contract, and 17 native scalar codec cases. Pytest used the task-owned `/tmp/state-foundation-impl.Dv6Xk4/green` basetemp with `-p no:cacheprovider`.

Ruff `check --no-cache` on the three new files, in-memory compilation, byte-exact plan comparison and `git diff --check` passed. The foundation remains unselected: it covers fifteen retained native state kinds and caller-transactional point writes, without source transfer, caller conversion, schema release, actual database access, native CLI, provider, network, push, main merge or deployment. The owned temporary test root was removed after validation and its absence checked. Fresh independent SPEC and QUALITY acceptance remain separate.


### State foundation SPEC `OR REPLACE` correction, 2026-10-16

Fresh SPEC review found two native counterexamples in the actual and fresh source schemas: `INSERT OR REPLACE` with `service_state.updated_at=NULL` stores the original `CURRENT_TIMESTAMP`, and `UPDATE OR REPLACE` with `dispatcher_claim_leases.owner=NULL` stores the original empty-string default. The initial target writer encoded NULL and hit its NOT NULL CHECK. New actual/fresh differential tests cover every one of the 41 retained NOT NULL/default fields for insert and update, with bound `None` and float NaN, using a deterministic connection-local SQLite `current_timestamp` function. They also cover nullable NULL, defaultless NOT NULL abort, retained outer transactions and a failed CAS that must not evaluate a default. The first targeted run failed at `codex_session_locks.locked_at` as expected; the corrected targeted run passed 12 cases.

The writer now asks SQLite to resolve a supplied NULL with the original default expression only for an original NOT NULL/default field under `replace=True`, before encoding. Matched updates use their existing guarded read and one SQLite UPDATE; no helper commit or extra write was added. The earlier **324/324** pass remains the pre-correction historical result. After the final outer-transaction assertion, the corrected exact focused matrix passed **336/336 in 16.15 seconds**: 147 State, 172 source oracle, and 17 native codec cases, using `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, Python 3.13.12/SQLite 3.50.4 and owned `/tmp/state-replace-correction.PV7erO/final` basetemp. Ruff on the three State Python files, in-memory compilation and canonical three-block equality passed. The owned temporary root was removed and its absence verified. The State foundation remains unselected; independent SPEC re-review precedes QUALITY.


## Independent State foundation acceptance, 2026-10-16

Exact b14198d passes fresh SPEC re-review and then fresh QUALITY, each336 focused cases in15.65s. The original OR REPLACE NULL/NaN source-default counterexample is resolved without changing the declaration constraints. QUALITY also independently checks30 source-family metadata comparisons,48 insert/update identity edges,42 scalar edges and two-connection writer-lock/CAS behavior. Canonical blocks, compilation, Ruff and diff checks pass; review-owned temporary roots are removed and the candidate stays clean. Root independently fingerprints the synthetic one-table declaration as ae25aecef5d8e9a0615ca3c180bb018eb6412a36bb25e850692d7f050eb8e5ab; the source capture remains SHA604496766a335d63fe334a263bde8028cd53c6bc5311acb97a5ef48876ae2aff.

Root query-only frozen970a preflight verifies the exact16-family actual fingerprint b7e149e432369018ba105f9e1ac12b052f38bfe245ee7e6d7b7b7b8b3ab9caa6 against literal source fixtures; fresh fingerprint is48e1608e66d2a35fdb467708e24fd6bef1f2f4ecd58e8a6e45c4af9fb6449a70. No inbound FK targets these families; corpus_sources has0 rows; all523 conversations hold integer0/NULL/NULL/NULL in the four unused fields. These are frozen-source eligibility facts, not production measurements or implemented retirement. Six existing field-map CSV rows still need an explicit guarded-retirement disposition before release. The new receipt superpowers/specs/2026-10-16-state-foundation-acceptance.json records the bounded acceptance. State remains unselected; source-bound transfer/current-fact inverse, atomic caller/publication closure and final10+ledger acceptance remain open. No actual copy selection, provider/native CLI/send, push/main merge or production action occurred.

## Unselected State pure release implementation, 2026-10-16

The complete test and application Python blocks in
`superpowers/plans/2026-10-16-state-release-plan.md` were installed byte for
byte as `tests/test_state_schema_release.py` and `app/state_schema_release.py`.
Test-first collection exited 2 because `app.state_schema_release` did not yet
exist. After the module was added, the exact focused four-file matrix passed
365 cases in 17.12 seconds: 29 release, 147 accepted State foundation, 172
literal source-contract and 17 native scalar codec cases. This used
`PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, the installed Python
3.13.12 interpreter at
`/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python`,
and task-owned `/tmp/state-release-impl.anp6h2` pytest storage. Ruff on both
new Python files, in-memory compilation, canonical byte equality and diff
checks passed. The owned pytest directory was removed and its absence verified.

The pure forward capture binds exact original actual/fresh declarations and
keeps no source row bodies in metadata. It verifies retained signed identities,
values and SQLite storage classes while moving fifteen kinds from sixteen
native families into one State table. It rechecks the four unused conversation
fields and empty corpus immediately before migration, then retires only their
approved six CSV dispositions. Inverse recreates original DDL and consumes
current State facts, including later inserts, updates and deletes. Synthetic
tests cover original declaration recovery, preflight refusal of unexpected
facts and external dependencies, plus injected partial forward and inverse
failures followed by caller rollback to the exact prior image.

This module remains unselected and needs caller-owned FK-off transaction,
rollback and publication integration. No actual source copy or production
database was changed, and no current constructor, caller, schema marker,
ledger, coordinator or repository updater selects it. The last actual copied
candidate still has 82 ordinary tables; 67 is a future integrated cutover
expectation. Final ten-plus-ledger acceptance remains open. Independent SPEC
then QUALITY review follows this local implementation commit.


## Independent pure State release and actual-copy acceptance, 2026-10-16

Exact869a783a passes fresh SPEC365 in16.15s and then fresh QUALITY365 in16.34s. Independent extra probes12/8 cover signed identity extrema, nullable PK duplicates/BLOB, TEMP dependencies, partial-DDL rollback, missing/extra/changed target facts before source deletion and read-only predicate behavior. Canonical two blocks and exactly six CSV rows match; source capture and accepted foundation remain unchanged. Both review-owned temporary roots are removed.

Root then verifies a new owned frozen970a State-only copy in59.132s. Original116 ordinary tables become101 after sixteen State families are consolidated into one; inverse restores116. Native symmetric BINARY/typeof/ROWID comparisons check all15 retained State families, all100 unrelated native families and original allocator rows; all116 original declarations and facts match after exact inverse. A single proof-owned inserted/updated service_state fact survives cold reopening, inverse and reforward while all original records stay exact. quick_check is ok; only the original runs190042-to-tasks FK finding remains. Frozen source SHA970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320 is verified before and after; the owned database directory is removed and absence checked. Receipt is superpowers/specs/2026-10-16-state-release-actual-copy.json; proof script SHA29c27dd7e69f0f3c6466b775c856e596992764abf708158d9d5b0434d3725dcb.

This is an isolated State component proof using the original116-table source. It is not a new101-table combined candidate: the last accepted combined candidate remains82, and67 is only the future expectation after actual State caller/publication integration. No current constructor/caller/version/updater/coordinator was activated, no genuine predecessor cold constructor or native Agent CLI was run in this proof, and no provider/send/main/push/production action occurred. Atomic runtime/publication closure, combined copied-data/genuine-old-code proof and final10+ledger acceptance remain required.

## Unselected native State UPSERT prerequisite, 2026-10-16

The canonical candidate adds `state_json_field` and a caller-transactional `upsert_state` using the existing State source-key partial unique index. RED collection failed on the missing `state_json_field` import. After the complete canonical storage block was installed, the new107-case native actual/fresh differential test and365 adjacent cases passed together (472 in17.97s); Ruff, in-memory compile, canonical byte comparison, all nine preexisting storage-function AST comparisons and diff whitespace check passed. Differential cases cover original INSERT-candidate NOT NULL failure despite a conflict UPDATE or false guard, retained signed source identity and repeated NULL keys, omitted defaults versus explicit NULL, one-statement SQLite clocks, and exact encoded JSON leaf selection for existing native scalar values.

This is an unselected storage prerequisite only. No current State caller, constructor, schema marker/version, repository updater or release coordinator selects it. The State declaration, source capture, pure migration and current-fact inverse were not changed. No actual database, native Agent CLI, provider/network/send, main, push, deployment or production action occurred. Atomic caller/release conversion and combined acceptance remain open; independent SPEC then QUALITY review follows this implementation commit.


## Independent native State UPSERT acceptance and caller/publication research, 2026-10-16

Exact0d24f0fa passes sequential SPEC107/1.33s plus116 source/default/zero-ROWID/NULL-before-CASE checks, then QUALITY107/1.91s plus raw encoded finite-REAL leaf, parameter ordering, native fact and guard probes. The implementer's472/17.97s matrix remains separate evidence; the reviewers did not both rerun365 adjacent cases. Both canonical blocks are exact; all nine existing function bodies, declaration, source capture and pure release remain unchanged. Review artifacts are absent (SPEC all in memory; QUALITY owned TemporaryDirectory removed).

The implementation cleanup is not complete: automatic approval review rejected rm-rf of /tmp/state-upsert-impl.694bXO because deletion required approval while AskForApproval was Never. Root verified resolved /private/tmp/state-upsert-impl.694bXO contains only pytest,474 files/3728744 bytes, and asked the user for explicit deletion authorization. No retry or alternate deletion was attempted. Acceptance receipt superpowers/specs/2026-10-16-state-upsert-acceptance.json retains this exception explicitly.

Bounded code navigation used graphify10 code files/166711 words,1882 AST nodes/5798 edges; the owned graph directory was removed. Source verification, not graph ranking, confirmed current UPSERT expression patterns and the exact wizard NULL-confirmer counterexample. A separate AST SQL navigation scan finds27 candidate modules/224 literal SQL calls including historical declarations/helpers; it is not an exhaustive current-caller closure claim. Native Email cursor and route-pause non-UPSERT branches must remain; dispatcher generation stays its original Python int+1 calculation.

Publication research identifies the existing progress-store instance across source/current schema. The paired child commits apply() before Store/Email readback, so a child failure can occur after commit. The release owner must bind the real progress store to the ledger-confirmed committed role before rollback validation and restore the original role only after inverse commit; a failed inverse must retain current binding for needs_manual writes. Generic updater protocol stays unchanged. The initial cutover can use updated development python-m-app.deploy; the old production UI child demonstrably imports its predecessor parent progress class before fast-forward, so old-UI first-cutover support is not established by the new target subprocess. These are concrete integration obligations, not implemented behavior. Current callers/constructors/version/updater/coordinator and final10+ledger remain open; no actual DB, native/provider/send, push/main or production action occurred.

## State caller prerequisites and proposal review, 2026-10-16

The explicit upgrade-progress DAO prerequisite is committed at e06e1e9d plus whitespace-only9cfb7226. Fresh sequential SPEC and QUALITY accept28 cases each (19new plus9existing); QUALITY also proves12 native BEGIN/COMMIT/post-DML failure, rollback and retry boundaries across actual/fresh and source/current roles. The generic updater flow and protocols are unchanged; no entrypoint or paired publication calls bind_schema_current yet. Stable-ledger role binding, postcommit child-constructor failures and inverse-failure progress persistence remain integration work.

The Store27-method proposal review found an organization-index source-identity tie defect. Amendment59b27ac6 changes only two nonunique index suffixes to source ROWID plus the target fingerprint, with new8 regressions. Root proposal314 cases and implementation314 pass; fresh SPEC314 passes and rechecks all8 exact proposed org DAO counterexamples. Source capture remains SHA604496766a335d63fe334a263bde8028cd53c6bc5311acb97a5ef48876ae2aff; table fields and the other18 indexes stay unchanged. The new unselected target hash is327c94f68a9354f8766dfebb9ef7c85c9d03c2a4b27f95387eb88cc32fabc1f2. Fresh QUALITY also passes314 cases in29.11s plus4 source-order/ROWID-update probes, recomputes the exact target fingerprint and verifies immutable source metadata/table maps/18other indexes. Later complete copied-candidate acceptance remains required; older ae25ae target receipts are historical.

Both complete executable caller patches remain unselected artifacts: Store27 methods and dispatcher19 existing callables/39native SQL references/28joins. Dispatcher SPEC remains BLOCKED because a native SQLite out-of-range int-subclass parameter is rejected on source but accepted as target REAL by the shared encoder. Fix that encoder with source-bound regression, remove the redundant per-generation SELECT, then re-review; no per-caller workaround is accepted. Matched30 source/target helper checkpoints are narrower evidence than full dispatcher acceptance.

The latest accepted combined copy remains82 ordinary tables;67 is only the expected future integrated State intermediate. Final10 core plus ledger, every remaining native-family retirement, final name/FTS inventory and reusable copied-data/native CLI harness remain incomplete. No current State caller/constructor/version reader/coordinator was selected; no source database, provider/send, main/push/deploy or production action occurred. Metadata receipt: superpowers/specs/2026-10-16-state-caller-prerequisites-acceptance.json. Pending cleanup authorization for /private/tmp/state-upsert-impl.694bXO remains unresolved and no deletion was retried.

The two caller proposal artifacts use zero-context unified diffs (git apply --unidiff-zero). Root normalizes only patch context formatting after unsuppressed diff checking flagged blank context-line spaces; applying the reviewed and normalized forms in owned temporary trees yields byte-identical candidate source, with both original reviewed and normalized hashes in the receipt. Application code is unchanged and both owned trees were automatically removed.

## Native integer, dispatcher proposal and bulk-State readiness, 2026-10-16

Shared integer correction756a6c7e is independently SPEC342/18.61s and QUALITY342/18.22s accepted. Additional14 SPEC native State probes and109 QUALITY native-binding probes plus16 legal parent JSON-byte controls/12 existing-lease overflow probes confirm original binding/encoding/transaction behavior. It changes only the shared encoder; original native source metadata and published legal scalar formats remain unchanged.

Dispatcher proposal b4ff0720 removes exactly the redundant generation SELECT from the candidate. SHA1b0f82c42eac437858662be46db0eca7175dfe3485e9d2d85c13ad240f101131 yields candidate7b7ab7d8b5225b66d666b8d744fca14bf77c6724ab4c2b739e5c31c149ae49ad. Fresh SPEC accepts358 native/candidate scenarios+252 explicit rollback checks and10 pre-write overflow traces; fresh QUALITY independently22 checkpoints, query-index plans and original transaction call positions. This resolves the prior shared-encoder proposal blocker. All19 existing callables/39references/28joins remain unselected; this is not a whole dispatcher workflow or cutover proof. A mechanical malformed uncommitted artifact was detected by apply check and corrected from HEAD through a temporary candidate; the committed final proposal validates cleanly without application edits.

Root compiles the exact27-method Store proposal into a test-only subclass over normal Store connection/hydration code in owned synthetic paired Store/Email databases, rebuilds only actual/fresh native State declarations, and migrates the target. Each variant matches61 method calls, all27 methods exercised. Final15-family facts/types/source ROWIDs agree (8 populated families/7 empty); only4 declared clock values are normalized across databases, not storage types. Blank confirmer preservation is separately checked per database. This6.058s result is not exact cross-database clock equality, actual-source copy validation, current cold constructor reopening or full boundary coverage. Own directories are removed; source constructors never touch production.

Set-based State UPDATE b778f49f is independently SPEC417 (403/18.45s+14/0.21s) accepted with6460 additional field-parity checkpoints. Fresh QUALITY accepts a DIFFERENT547-case matrix/23.78s plus48 behavioral checkpoints: source-contract172 replaces42 author-matrix integer/org/native-source cases, net130 more tests, not the same417 plus130 new unique cases. All12 original storage functions and State/source declarations remain unchanged. The new API preserves one UPDATE statement's counts/ABORT/REPLACE defaults and untouched encoded leaves; no current caller selects it. Actual route rename keys are distinct; no arbitrary competing-key collapse survivor ordering is promised.

Read-only constructor closure now identifies exact work: Store14 native DDL removals (13 retained kinds+empty corpus),4 ALTER blocks and legacy runtime-session synthesis removal; validated fresh State installation before _migrate_removed_runtime's state deletion; exact State predicate/marker before cached or known-repair paths. Runtime Email factory must omit its2 native State families while archived source factories remain intact. Periodic/UI/progress readers and stable-ledger publication version readers must be converted together. Current fresh paired Store+Email matches the frozen fresh16-family/87-field source declarations; this is source-preparation evidence only.

Receipt superpowers/specs/2026-10-16-state-caller-readiness.json records exact hashes, scopes and verification boundaries. Combined accepted copy remains82 ordinary tables; expected integrated State67 and final10+ledger are not yet proven. Complete current caller/constructor/version/progress/publication closure, reusable copy/native harnesses and genuine old-code/current-fact inverse/final-name+FTS evidence remain required. No actual source database/copy, provider/native/send, main/push/deploy or production mutation occurred. Denied cleanup authorization remains pending and was not retried.
