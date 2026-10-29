# Current-model test marker cleanup — 2026-10-26

The scope is every Python test xfail/skip declaration and explicit skip call,
including fixtures and collection configuration. The initial41 source call
sites include one xfail and ten unconditional skip decorators (two decorators
were duplicated on one Worker test). After cleanup29 sites remain:17 skipif,
4 dependency importorskip,8 conditional skip calls. There are no remaining
xfail calls or unconditional skip decorators. These are source-site counts,
not counts of collected or executed cases.

| Original contract | Disposition and current coverage |
| --- | --- |
| WeChat concurrent old-schema automatic repair | Delete the xfail test, process helper and exclusive old Task8/Task12/Task20 imports. Runtime constructors intentionally reject non-current declarations without mutation; current cold-open/schema and WeChat identity/retry tests remain. |
| Fresh Memory FK writer variant | Delete the permanently skipped node. Its sole fixture has only actual/actual_no_memory_fk since5e3b77c1d explicitly retired the fresh writer variant. Source-only frozen actual/fresh oracle tests are distinct and remain. |
| Worker generation mismatch retry | Remove both duplicated skips; existing current test body passes unchanged. |
| Old ScriptAgent receipt retry | Delete the old skipped test; it never performs a retry. Current SystemExecutor verified-action replay and uncertain reconciliation tests plus the durable Worker open-task replay test cover actual no-repeat effects. |
| Consumer read-only command/resume composite | Retire obsolete prompt/tool-registry expectations and restore session behavior in the gated real-Store/current-native-schema three-case test: matching route hash resumes, changed/missing route hash starts fresh. Two unmarked old bare-hash neighbors also expected unconditional reuse; the current cases replace those invalid assumptions. |
| Two Consumer provider-event traces | Remove skips; actual native effectful/later provider evidence remains visible. An old negative assertion is replaced by the current evidence-presence assertion, without interpreting the event as permission to act. |
| Unprobed API fallback | Retire the skipped old fallback expectations; existing typed-ineligible/unprobed cases assert no process starts. |
| Probe effect/event policy | Delete two skipped tests of retired application-level provider policies. The current standard native command/complete-result probe remains covered. |
| Friday fallback declared closed | Restore it: Friday is currently supported. Use native enqueue/claim rather than a retired reply_tasks table, and an explicitly injected localhost fake HTTP transport. OAuth/API/Friday attempts and operation evidence remain checked. |

## Real defect exposed during restoration

The new two-turn Consumer case initially failed even with the correct current
native output-schema SHA. Session creation saved the selected-skills/Developer
contract, then RuntimeAttempt completion overwrote it with the bare wire hash.
The second identical turn therefore ran fresh. Separate runtime fix754019af1
passes the already-existing route-specific contract to completion. It adds no
new matching gate, compatibility lookup, source backfill, Audit/auth/retry or
route policy. Both actual Store turns use substituted CLI execution.

The existing Developer-change-requires-fresh unit remains. The three integration
cases also check final stored hash and exact session identity, including fresh
session-b when the stored contract is changed or missing.

## Verification and retained skips

-138 current WeChat/Store/schema/Action cases pass in45.00s.
-73 restored provider/probe/runner cases pass in4.28s; final gated session/runner
  matrix48 passes in2.43s, including the two added mismatch/missing cases.
-4 restored Worker/current SystemExecutor successor cases pass in1.44s.
- Friday localhost fake-service case passes in1.43s.
- Source-profile oracle controls:2 pass,2 deliberately skip the nonmatching
  source profile. Ruff and diff checks pass; independent SPEC/QUALITY review
  approved, including the session persistence fix and final parameter matrix.

Remaining skip sites are explicit opt-in live/provider/mailbox/GPU tests,
browser/dependency or OS requirements, an explicitly supplied owned DB snapshot,
actual-vs-fresh frozen source contracts, and SQLite NOT NULL default semantics.
They do not catch missing fields or convert schema errors into passes. The
collection hook only excludes live tests by default and implements CI sharding;
its conflict-copy ignore is unrelated to old schema behavior. No full local
suite was run, and no test sent to a real provider.

The exact remaining sites and focused evidence are in the
[marker record](validation/2026-10-26-current-model-test-markers.json).

## Publication and closeout

Standard exact-target deployment completed `b9244988e → a8d3aba6d` after the
mandatory clean-clone gate passed under unchanged2GB/5minute limits. It waited
for real production work to finish. At20:58:22 UTC, PID76100 loaded the exact
target. Health, queues/status, Attention and History return200; actual Chrome
Dashboard/History shows running service, queue tabs, Attention and history rows.
Installed completion AST calls `session_contract_hash_for_route(route)`. All476
schema objects have the exact prior hash and FK findings remain empty. No schema
or historical-data migration was performed.

The before/after aggregate failed count is25→27 and Attention3→4. The waiting
production task417543 failed with audit_revision_exhausted at20:56:50 UTC before
the new process started at20:57:04 UTC; this verification does not claim no
existing business failures. No Task was replayed or rewritten by these tests.
Current session behavior is verified by real-Store/substitute CLI regressions;
no extra production model turn or real test send was triggered.

The exact deployment workflow backup passed its completion marker and full
SQLite integrity check. With no active deploy or open files, the owned temporary
root and verified backup were removed and their absence verified. Production DB
and native trajectory originals remain. The concise marker/publication record
is retained; test DBs, raw logs and intermediate code are not archived.
