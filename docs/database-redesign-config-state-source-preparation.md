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
