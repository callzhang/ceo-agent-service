# Legacy business writer retirement: parent acceptance, 2026-10-20

D2 deletes exactly five uncalled public Store methods for creating/updating old WorkProject, WorkTodo and WorkUpdate records. Source SPEC independently passes both D2 and the separately scoped D1c worker-status fixture follow-up. Final independent QUALITY passed for D2 and the separate D1c follow-up. The reviewer independently verified the frozen sources and artifacts, all 757 retained Store methods, actual old-code program and the final read-only 12-table / 511,607-row typed-fact oracle; no P1/P2 issue was found. This report supplements the frozen implementer source and test reports; it is local evidence, not a production release.

## Source and business boundary

The frozen base is `765488f678b8673848a364912ec9ed5052ddab90`; the 25-path manifest SHA-256 is `d6c82737347b20f530a406cdd79e260b5bebf9f30261780f578b058c0ac8953c`, and the combined patch SHA-256 is `ca19e97e4e7c31c4e4771b26057c239bb25d46fedf1c295abf2584a2857aa43e`. Root independently confirmed that all 757 retained Store methods have identical ASTs and the importer, current semantic service, business declarations, maintenance implementation and schema selector are unchanged. The importer remains a real, conditional source-backed promotion capability with actual import_task_id binding. Numeric ID collision or titles are not promotion evidence.

No schema field, stored history or business fact is removed by D2. Old identities, provenance, business conclusions, provider associations and receipts remain queryable. Test-only typed fixtures replace181 historical setup calls; they do not restore a runtime writer or new policy. The real old-code program embedded in the paired-copy validation script is byte-identical; only current-copy fixture setup changes. No undocumented current-facing behavior is cancelled.

## Actual predecessor and current copy reads

Root archived all444 application files from actual base765488 and verified every file byte against Git. That actual code cold-opened the already exercised current-data copy with Store, strict EmailStore and Workbench. It read new synthetic Task736(done), Task737(open), all three explicitly substitute outbox receipts, archived project/Todo/link facts, 540 recorded-active old links and242 old outbox rows. The five old writer APIs were present in this actual predecessor.

The frozen new code then cold-opened the same copy and read those same facts and receipts, while asserting all five removed APIs absent. An independent typed-cell oracle compared all12 physical tables to the exact final post-recovery snapshot: values, SQL types, rowids, sequences, schema, publication markers, Email53 and the original FK exception all match. No record is rewritten, promoted or deleted. Root executes no native model, Audit Agent or provider in this readback.

D2 contains no DDL, so these are code cold-read checks, not a new schema inverse. The same copy already passed the official19.10→19.9→19.8, actual productionb6 cold read, then19.8→19.9→19.10 recovery recorded in the D1b parent report. No second schema roundtrip is claimed or needed to test deletion of uncalled Python methods. This copy still has the documented provenance of the prior production19.8→prepared19.9→Run19.10 validation chain plus explicit mechanical fixtures; it is not a fresh production19.10 snapshot.

## Focused failures and repaired test fixtures

The five-API regression initially failed on the old create_work_project method; its original tool output was observed but not captured as a raw file. Root separately proved all five APIs exist in actual predecessor code. Recorded core31 tests passed. Wider adjacent runs recorded63pass/3fail and audit28pass/1fail; their selected node lists and logs are retained and hashed. Source/test batches overlap and are not summed.

Root independently reproduced three legacy-SQL fixture failures on clean D1b (3failed,6.96s), plus the source-race fixture's missing ceo_task_restore function (1failed,3.21s). Those fixtures now use existing typed current APIs/configured connections with the same values, source-change timing, commit boundary and expected outcomes. Three fixed fixtures passed; the race fixture passed and the core group then passed31. All assertion ASTs are preserved except three historical read-query operands using current projections and three additional D1c assertions. No test function is deleted; the mixed historical-read case was renamed and passed separately.

The worker-status case is different: it passed before D1c (root1pass,4.24s), then failed because its only retryable example used the retired old Todo source. Its separate D1c follow-up adds a current pending Reply task plus a matching failed Reply attempt, retains every original assertion, and verifies current Reply retryable=1, no old queue, and the old failed fact still readable (1pass,7.84s). No runtime monitoring rule or threshold changes. That fixture/documentation follow-up is committed separately from D2.

The older unrelated Email fixture AttributeError remains a previously recorded baseline failure outside this scope. The two A5 native content failures remain FAIL and release_ready=false. Full unit D and the four-unit migration still require their remaining field-level implementation, acceptance and final fresh production-copy release gates. No push, production change or real external sending is performed here.

[Machine evidence](superpowers/specs/2026-10-20-legacy-write-parent-evidence.json) binds frozen sources, scripts, original failure logs, focused results and actual-copy proof. Active temporary data remains only while the multi-unit task and reviews require it; no native transcript bodies are copied into this report.
