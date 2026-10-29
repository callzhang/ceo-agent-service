# Task delivery Actions validation, 2026-10-17

This is a bounded continuation of the approved database redesign. Its target
remains ten core tables plus `email_schema_migrations`; the selected development
candidate currently has36 ordinary tables. The four-family component below is
unselected. Neither this document nor point-write tests constitute current
caller conversion, complete migration acceptance or a production deployment.

## Source and implementation boundary

The exact actual/fresh metadata is in
`superpowers/specs/2026-10-17-task-delivery-source-metadata.json`.
`meeting_alignment_delivery_claims`, `meeting_memory_write_events`,
`task_memory_write_events` and `wechat_deliveries` map to typed Actions.
The actual source contains0/595/611/133 rows respectively. Actual WeChat
uniqueness and meeting Memory default/order differ from fresh declarations;
both profiles require exact reconstruction. No business fields or policies
are added, and no trajectory/body backup is introduced.

Author verification reports30 component/adjacent tests and2 paired integration
cases passing, plus Ruff, compilation and whitespace checks. Sequential independent SPEC and QUALITY approve the component baseline
recorded below. Current caller integration is separate and remains open.
Its point APIs and native SQL parts do not establish complete bulk, CAS or
UPSERT caller behavior.

Initial independent SPEC found three writer differences. A TEMP allocator can
shadow unqualified MAIN counters, and an invalid PK assignment was evaluated
before an unmatched UPDATE predicate; both are corrected and passed independent SPEC re-review.
The third difference concerns automatic claim `job_id` allocation: native
BEFORE INSERT sees the unallocated sentinel, while target preallocation sees
the resolved ID. This is not a used business operation: both existing Store
claim INSERTs explicitly supply a task ID selected from Tasks. The new internal
claim writer is therefore narrowed to require an explicit non-NULL `job_id`,
rather than introducing an omission flag or a trigger bypass. Missing/null
claim IDs are outside this helper contract; no native parity is claimed for
that unused input. Migration and inverse must still preserve every existing
source claim row and reconstruct the exact original declaration. This narrows
the parent's initial overly broad helper requirement, not an existing business
caller contract. The revised component passes16 author tests in49.61s. SPEC re-review approves:16 focused cases plus2720 supported-field
native/target comparisons show no acceptance/value/storage-class differences.
Sixteen zero-row/false-guard invalid-PK comparisons also match; the unrelated
TEMP allocator control returns81 in both source and target. Generated CHECK
versus native NOT NULL extended error codes were excluded from scalar parity.
Independent probes preserve current facts and native automatic-ID claim facts
through inverse/reforward. This remains SPEC acceptance of the bounded helper,
not activation of callers or final QUALITY approval.


## Current runtime baseline

The baseline still uses the existing native delivery tables. Three current
fixtures now encode Tasks20/Task12 facts through their declared storage APIs
instead of obsolete flat columns. This preserves their original assertions,
physical FK identities and deliberate public/physical ID divergence.

- Current meeting storage readers:8 passed; the historical orphan case is
  separate below.
- Meeting and Task Memory files:54 cases passed in the ordered baseline run.
- WeChat Store:52 cases passed before the stale schema-repair fixture; that
  repaired concurrent-schema case passed separately.
- Remaining3 WeChat cases plus `test_meeting_alignment_delivery.py`:79 passed
  in84.40s.
- Ruff, compilation and `git diff --check` pass for the parent-owned fixture
  corrections. No runtime delivery behavior was changed by those corrections.

The historical orphan case fails before meeting logic while capturing an
unsupported historical Config declaration. This is an open fixture/source
integration issue, not a passed rollback case. Its owned temporary directory
`/tmp/task-delivery-readers.Vk2RPC` was removed.

## Current copied-database rehearsal

A fresh SQLite native online backup was taken using a read-only source
connection under `/tmp/task-delivery-copy.L2sYnk`. The baseline has116 ordinary
tables,1 virtual FTS table,4 shadows,449 noninternal schema objects,48 allocator
rows and1 original FK finding; `quick_check` is `ok`. No raw source content is
stored in this receipt. The existing accepted paired migration completed on this disposable copy
in129.05s:116 ordinary tables became36, `quick_check` remains `ok`, and the
original runs190042→tasks FK finding is unchanged. The same frozen four-family component subsequently passed a108.54s rehearsal:
36→32→36→32→36 ordinary tables, with explicit target writes, component inverse,
cold reforward and a second component inverse. All current four-family facts
survived; exact component source schema was restored; `quick_check` is `ok`;
the original FK finding is unchanged. Test writes created one claim, one Task
Memory event and one WeChat delivery, and updated the claim token. Meeting
Memory's595 existing rows were compared through the roundtrip without a new
meeting event. This is not whole-release current-predecessor acceptance.

| Source family | Before component | After copy-only writes |
| --- | ---: | ---: |
| meeting_alignment_delivery_claims |0|1|
| meeting_memory_write_events |595|595|
| task_memory_write_events |611|612|
| wechat_deliveries |133|134|

Both copy steps used Python3.12.11 with SQLite3.53.4.
The component proof is bound to these unchanged code hashes:

- `app/action_storage.py`: `58bab29def1aa1f0e0fa71116314fde8975185013052a2389922665da4b644bc`
- `app/action_task_delivery_definitions.py`: `7dc308eb145465b51e5c367bb081387a79617a47bdea43bc4d08d0b091fb8917`
- `app/action_task_delivery_storage.py`: `0672792201991d642a04754cac7fe941932145b4ec978f3390be8333648f298f`
- `tests/test_action_task_delivery_storage.py`: `440f3fef262c9422e2a1d5218c59e4661449c38144949e0ed931b035db41bb34`


The owned copy directory `/tmp/task-delivery-copy.L2sYnk`, including both
scripts, baseline schema metadata and copied database, was removed after
writing this concise receipt. Independent QUALITY found that fresh `memory_attempt_ref` was not forced
NULL on the four new kinds, allowing an accepted unrelated reference to be
silently dropped on inverse. A second independent transaction probe found that native task-delivery
AUTOINCREMENT exhaustion preserves the caller transaction because of its
original BEFORE parent triggers, while the reused helper manually rolled it
back. Of258 comparisons,12 exposed this mismatch and246 matched; native
trigger/RETURNING controls pin the cause. The task-delivery allocator must
preserve the caller transaction without changing existing other-kind allocator
semantics. The corrections now pass final bounded SPEC re-review, including144 native
error-precedence comparisons and24 MAIN/TEMP parent-namespace comparisons.
The exhausted allocator reuses the existing wrong-parent trigger before
reporting FULL, preserves caller state and reads `main.tasks`. Author focused
36 AUTO cases pass; independent final QUALITY also approves the frozen
component baseline recorded below. The actual-copy
result above remains bound to its original four hashes; it is not
relabeled as a replay of revised code. No caller or publication is selected.

## Final unselected component acceptance

Sequential independent SPEC and QUALITY approve the component baseline below.
The later caller/SQL-expression integration is a separate, still-open change;
these hashes do not approve those later edits.

- `app/action_task_delivery_definitions.py`: `600b3d1c58915b2b8d935eadef7fb4cdb08cb351d11ba00938cd5aa8eac73307`
- `app/action_task_delivery_storage.py`: `0672792201991d642a04754cac7fe941932145b4ec978f3390be8333648f298f`
- `app/action_storage.py`: `f714b358491037ccc3a9ca4c8fa2f026697b876943f6423069dfb037743cc42b`
- `tests/test_action_task_delivery_storage.py`: `1acc480c599f98abd748e6abd7157dd7fb3cace658a72c4a60233a17cf6876a1`

Final QUALITY used Python3.12.11/SQLite3.53.4:97 tests passed in61.12s
(53 component cases and44 generic Reply/Sent/Email/Sync allocator/transaction
regressions). Its348 independent native transaction comparisons had zero
mismatches, with the stated CHECK/NOT-NULL extended-code exclusion. Actual and
fresh supplemental migration proofs injected forward/inverse DDL failures and
restored full schema/rows/counters through caller savepoints; modified all four
families; used JSON-serialized metadata for inverse; preserved generic Action
physical identities and unrelated trigger logs; and passed disk-close/reopen,
reforward/inverse and integrity checks. Generic TEMP allocator controls also
passed. All reviewer-owned temporary roots were removed.

The11 Email Reply DAO failures initially excluded from QUALITY were confirmed
against committed HEAD as well. They are a separate old partial-schema fixture
problem: its explicit target migration omits Configs while current account
DAOs read Configs. Root is correcting that fixture without changing runtime
account or delivery behavior; its results are recorded separately below.

## Email Reply DAO fixture prerequisite repair

The separate11 failing Reply DAO cases used explicit converted-copy fixtures:
Email and Provider had been migrated, but account Configs had not. Current
`get_account` reads the Configs projection. Root independently reproduced the
representative `no such table: configs` failure in4.64s, then added the exact
Config source capture/migration after Email/Provider in the same fixture
transaction. The archived source construction and effective native SQL oracle remain
unchanged; its normal test operations still write its disposable source copy.
No current constructor is bypassed additionally or claimed as tested.

The representative case then passed6.28s. The full file produced10 passes and
one remaining failure in45.21s: a constraint test directly updated retired
`email_accounts`. That test now explicitly supplies the equivalent current
Configs payload/public-key UPDATE for the target, while its archived native
source invocation retains the original SQL. The unchanged assertion compares
native trigger errors, outgoing uniqueness, authorization, receipt facts,
values and storage classes. The remaining case passed5.94s; Ruff and whitespace
checks passed. Thus all11 existing cases have passing observations through the
10+1 runs initially. Root then reran the complete unchanged file: **11 passed
in50.26s**, using a task-owned disposable pytest root. This confirms the
fixture prerequisite repair as a complete file run; it is not a cold runtime
or full task-delivery integration proof. These fixture edits await the
coherent caller integration's independent review, rather than changing runtime
account or delivery code.

The owned `/tmp/task-delivery-email-fixture.z55fEq` root, containing only
these test databases/archived code and pytest artifacts, was removed. The
later `/tmp/task-delivery-root-recheck.wjxjnq` rerun root was also removed
after its complete file result was recorded.

## Historical source13 fixture diagnosis

The historical orphan case was reproduced again in5.57s. Metadata-only
comparison identifies the exact unsupported companion mixture: its account
schema matches the independently captured fresh profile; its scheduling
schema matches the actual upgrade profile with appended columns. The
`scheduled_task_config_versions` DDL formatting and scheduled index creation
order also belong to the actual/source13 fixture rather than the fresh
profile. The paired Config preflight accepts the exact full actual or fresh
profiles, so this mixed fixture stops before meeting logic.

The two scheduling tables and accounts are empty; the fixture additionally
contains9 category configs,9 description revisions and1 promotion config.
Root did not drop/rewrite these schemas, add a permissive source profile,
change runtime preflight, or label this case passed. Resolving the historical
fixture's coherent source role is separate from the verified current actual
production-source migration. This diagnosis does not replace genuine current
predecessor rollback acceptance. The owned diagnostic root
`/tmp/task-delivery-historical.NttgnL` was removed.

## Remaining integration

After component SPEC and QUALITY acceptance, validate real copied rows and
current writes through inverse/cold reforward. Then convert the complete Store
and direct caller set, task-child guards, constructor/current predicates and
paired coordinator together before selecting the new shape. The remaining
Business/Email/Provenance/Actions families, final11 names and absence checks,
whole-release schema rollback with genuine predecessor code, fake-provider
native CLI, UI checks, reconciliation with current main and formal publication
remain open. No source database write, real send, push or deploy occurred.

## Current caller integration frozen for independent review

The implementer has frozen the current four-family Store/Email/dispatcher/
Audit/Quality/config/run-manifest/paired coordinator change. Fresh SPEC is
now independent and pending; fresh QUALITY must follow SPEC approval. Author
reports populated actual/fresh paired inverse/current-new-write4 cases,
fresh conversion-failure1 case, both cold constructor orders2 cases and
nonempty Audit/Quality6 cases passing, plus bounded Memory/WeChat tests.
These new paired/fresh tests are permanent cases in
`tests/test_action_task_delivery_storage.py`; no actual production-copy rerun
or whole current-predecessor proof is claimed here. The author interrupted
a later full component-file run after one case; independent full file testing
remains required rather than counting that interrupted run as green.

The bulk writer's bounded capacity pre-count adds one trim of the newly
eligible body (three observations versus the source's two); the tests retain
the requirement not to read already-enqueued bodies. It remains one atomic
INSERT SELECT under the original IMMEDIATE transaction, without a new
transaction owner or per-row insertion loop.

The author-owned21 `/tmp/task-delivery-cutover.*`/`task-delivery-cutover-test`
test directories total276,601,488 logical file bytes. Root verified the exact
paths are ordinary task-owned directories and no test process references them.
Their cleanup was rejected by automatic approval review (`approval required
by policy, but AskForApproval is set to Never`). A concrete21-path human
authorization request is pending; deletion has not been retried or bypassed.

Exact cleanup targets in the pending human request (no glob deletion):

```text
/tmp/task-delivery-cutover.s3Xwoy
/tmp/task-delivery-cutover.hjO5FU
/tmp/task-delivery-cutover.7vLcah
/tmp/task-delivery-cutover.KWRn1O
/tmp/task-delivery-cutover.hZV1cQ
/tmp/task-delivery-cutover.S3jWP2
/tmp/task-delivery-cutover.BG5ugD
/tmp/task-delivery-cutover.KTMARi
/tmp/task-delivery-cutover.FVzPOj
/tmp/task-delivery-cutover.K7GVFp
/tmp/task-delivery-cutover.vcbPzg
/tmp/task-delivery-cutover.zkMBRY
/tmp/task-delivery-cutover.UOgvLk
/tmp/task-delivery-cutover.7HoWbo
/tmp/task-delivery-cutover.0t9MbD
/tmp/task-delivery-cutover.fI0EJI
/tmp/task-delivery-cutover.UEJBnD
/tmp/task-delivery-cutover.GCcfts
/tmp/task-delivery-cutover.ykfVua
/tmp/task-delivery-cutover.3Tjv4t
/tmp/task-delivery-cutover-test
```

## Current cutover SPEC acceptance

Fresh independent SPEC approves the frozen four-family current caller and
paired migration slice. Its clean complete component run passed60/60 in
105.35s; focused current callers passed31 cases in225.80s with exactly the
known historical Config-hybrid failure before meeting conversion. The review
checked all nine runtime file hashes and the component/Email DAO fixture
hashes unchanged, fresh-only transient declarations under the original TX,
current32 reopen, apply-after-followup/inverse-before-followup order and
bulk one-statement atomicity. No source database, provider or production
runtime was accessed by this reviewer. This is SPEC approval only; QUALITY,
actual-copy/current-predecessor and final11 acceptance remain open.

The reviewer's `/tmp/task-delivery-spec.IdLhZZ` temporary root also remains:
its exact-path cleanup was rejected by automatic approval review with the
same approval-required/Never reason. It is outside the already-requested21
author paths; no cleanup retry or alternate deletion mechanism was used.

## Current cutover QUALITY acceptance

Fresh independent QUALITY approves the frozen bounded cutover. Its10
independent focused tests passed (6 caller/CAS/atomicity in25.65s;4 nonempty
Audit/Quality cases in26.46s). A direct source-vs-Actions Task Memory UPSERT
probe advances both family counters1→2 on duplicate conflict. Runtime
closure, same transaction/coordinator order, root fixture scope and nine
runtime/two test hashes were checked; Ruff and diff check pass. No code edits,
source DB access, providers or production changes occurred.

Its sole `/tmp/task-delivery-cutover-quality.XixY3Q` root (about25MB) also
remains after the same approval-required/Never automatic rejection. No
alternate deletion or retry was attempted. The known Config-hybrid fixture
and final11/actual-copy/current-predecessor/native-role acceptance remain
separate; QUALITY does not convert them to passes.

## Real current-source cold-copy regression discovered

Root took an online `mode=ro`/`query_only` production snapshot bound to
actual production commit `8a3262b921d370d8d9cd90fab33f026ddafc3345`.
It contains116 ordinary tables and0/595/611/133 four-family rows.
The genuine paired `publish()` subprocess and both current cold constructors
completed to32 ordinary tables in209.46s. All original four-family field
values and SQLite types matched; current helper writes added one fact per
family and Task Memory/WeChat DAO readback succeeded.

Whole rollback then failed BEFORE task-delivery DDL at
`action_task_delivery_storage.py:286` with
`ValueError: unsupported inverse Config trigger declarations`. The release
rolled back its transaction. This is a FAILED current cold inverse proof,
not full-copy acceptance; predecessor cold writes and reforward were not
reached. The task-managed temporary copy/archive scope was removed on exit;
the production DB was never opened for writing.

The observed boundary missing from direct `apply()` tests is the real
`publish()` subprocess's JSON inverse-metadata transport. The trigger-pair
comparison converts only the outer sequence to tuple, leaving JSON-decoded
inner lists unequal to SQLite tuple rows. Follow-up inverse contains the
same comparison pattern; the implementer is confirming both with nonempty
Configs trigger metadata before a narrow representation correction and
permanent cold/JSON regressions. Exact trigger names/order/SQL validation
must remain intact. Earlier SPEC/QUALITY approvals bind the preceding
code; the fix requires its own re-review and a repeated actual-copy proof.


## Narrow JSON inverse correction and independent re-review

The two inverse comparison sites now normalize both sequence levels of the
known trigger name/SQL pairs. Names, declaration SQL and ordering still compare
exactly; no schema, business behavior, fallback or policy was added. The new
actual/fresh regression at `test_json_transport_inverse_round_trips_config_triggers_and_new_facts`
JSON-roundtrips both nonempty metadata subtrees, adds one current fact in each
of the four families, and checks whole original schema, the four newly written
facts and three AUTO waters after inverse. This focused test reproduces IPC
representation; it does not itself exercise `publish()` or compare all old rows.

Author regression:2 passed38.31s. Independent SPEC re-review:APPROVE,
2 passed37.49s. Independent QUALITY re-review:APPROVE,2 passed36.89s,
plus six negative controls changing trigger name, SQL or order at each inverse
site; every control rejects before DDL and leaves schema unchanged. Ruff,
compile and scoped diff checks pass. Frozen corrected hashes:

- `app/action_task_delivery_storage.py`:e5891f9485ae209c2e58f1cdbea043f18d4da16377f72b18868add3743ad3f62
- `app/action_followup_sync_storage.py`:a576e218dd9e058b99083c362ad238b346d47656e651ce6a1b67f77c24c73c95
- `tests/test_action_task_delivery_storage.py`:bdce303877adf3939dfe0e862b72bda49ff9956595d7d35fd8f987b7e895a17a

A fresh root-managed actual production online copy is now repeating the full
cold forward/inverse/predecessor-write/reforward proof. Its source116 contains
0/595/612/133 four-family rows and binds actual production predecessor
8a3262b921d370d8d9cd90fab33f026ddafc3345. No PASS is inferred until all
stages complete. Production remains readonly; no provider sends are involved.


## Corrected real-copy full cold rehearsal: PASS

The corrected frozen candidate passed the real paired lifecycle in566.413s:
116 source ordinary tables →32 current →116 inverse →32 cold reforward.
Metadata-only machine receipt:
`superpowers/specs/2026-10-17-task-delivery-current-copy.json`.
Source predecessor is actual production8a3262b921d370d8d9cd90fab33f026ddafc3345,
Store2026-10-13.5/Email51. All source four-family rows (0/595/612/133),
field values and SQLite types survive forward. One genuine current storage
write per family survives the whole inverse, and the complete original source
schema/table-name set is exact. The real archived predecessor opens both
stores, reads new Task Memory/WeChat records, then writes task Memory614 as
`skipped` through its original internal enqueue path. All current and old
writes survive cold reforward (final family counts1/596/614/134).

The three native allocator waters progress295561/612/137 →295562/613/138
→295562/614/138 and survive inverse/reforward exactly. quick_check passes;
the original single FK diagnostic `(runs,190042,tasks,1)` is unchanged.
Target has no virtual/shadow FTS objects. All thirteen frozen runtime hashes
remain unchanged across the rehearsal. Managed temporary copy and archived
code `/tmp/task-delivery-current-copy.8devjw47` are removed. Production was
mode=ro/query_only only, and provider/native-role invocations equal zero.

This proves the whole schema lifecycle and all four TaskDelivery fact families;
it does not compare every row of all116 original families. Other components
retain their separately bounded receipts. It proves storage/current-fact and
cold predecessor behavior, not a native execution/audit/provider workflow.
Final11, whole final-schema acceptance, native CLI with fake sends, UI,
publication and actual space reclamation remain open. The earlier failed
attempt remains recorded above and is not relabelled as passing.
