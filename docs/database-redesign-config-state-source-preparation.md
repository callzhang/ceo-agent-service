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
