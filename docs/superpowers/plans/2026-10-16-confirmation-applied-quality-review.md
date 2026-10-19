# Confirmation retirement: fresh applied QUALITY review

Status: **APPLIED QUALITY APPROVE** for selected application commit `0ca8454b9e22449e89245999486dd58021c73a24`. No actionable runtime quality defect found in this bounded review.

This review follows the applied SPEC approval and accepts the exact 41-file confirmation-retirement selection, against approved design108, the retirement plan and prior canonical SPEC/QUALITY reviews. It does not establish production readiness, approve the final migration, or complete the broader database redesign.

## Independent candidate binding

Fresh SHA256 checks verified all **41 before hashes** against the selected commit's parent and all **41 after hashes** against both the selected commit and current working tree, including the deleted ConfirmationCard path. All **13 proposal patch hashes** and the canonical-to-installed regression copy match the selection manifest. The selected commit changes exactly those41 paths plus its own claim row.

All **five preserved source/native files** match their pins in the selected parent, selected commit and working tree: `app/task_storage.py`, `app/workbench_storage.py`, `app/agent_cli.py`, `app/workbench/service_runtime.py` and `app/native_cli_metadata.py`. Thus the historical factories, native approval/configuration adapter and shared native runtime are byte-preserved. This is source verification, not real CLI/provider execution.

At the binding check, HEAD was `b9aae602c678735c86ddb90eab97259a5ab051cf`; its19 changed paths relative to the selection were documentation only. Subsequent root-owned proof-document/timing clarification does not change the manifest-bound candidate. This reviewer made no application, test or frontend changes. Review execution made no index or commit changes; root subsequently authorized an evidence-only commit of this review and this reviewer's own claim row under an exclusive index handoff.

## Applied quality findings

The current runtime consistently removes the canceled feature: models, event validation, executor branches, Store CRUD/recovery, API resources and frontend types/rendering agree. The only retained frontend retired-event literal is its explicit negative test. The current Store timeline and API consumer agree on the six-element snapshot. Ordinary execution retains its existing worker reservation, lease, recovery, stop/completion race, artifacts, native observation and bounded shutdown paths. Native Agent CLI approval remains separately preserved by the five exact source pins.

The applied migration binds the exact original empty declaration, including its indexes, triggers and FKs, through source hash `e215afb029d2993691a8585b07a614667aca8cddedd8dd21b9b0a3f3335d982d`. Source capture checks rows, waiting turns and legacy events before current Tasks DDL; forward rechecks the feature closure immediately before retirement. The helper requires the caller's FK-off transaction and never commits. Inverse carries declarations only and recreates the original empty table before Tasks20 restoration. Current unrelated business rows and counters remain inputs to the existing Tasks20 inverse; no historical row snapshot is introduced.

Independent AST call-order checks confirmed capture-before-Tasks in source recheck, retirement-after-Tasks in forward, and confirmation restoration-before-Tasks in inverse. Inspection also confirmed the same capture order during initial preparation and target verification that requires the retired relation absent. The installed behavior-document introductions accurately describe this contract and the intermediate54-table state.

## Fresh focused execution

Pinned Python: `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python`, using `-B`, `PYTHONDONTWRITEBYTECODE=1`, pytest without its cache provider, and an owned temporary basetemp.

**54 passed in22.64s** across the complete selected modules:

- `tests/test_confirmation_schema_release.py`: exact empty declaration inverse, source row/waiting/event refusal, late-row recheck, transaction ownership, rollback, ordinary event preservation, TEMP collision and metadata/declaration rejection.
- `tests/test_workbench_confirmation_retirement.py`: removed resource/state/event contracts and retained recovery clock, transient-lock behavior, non-lock error propagation, expired/live/stopped distinctions.
- `tests/test_workbench_executor.py`: ordinary streaming/session/terminal persistence, artifacts, lease renewal/loss, stop races, failure behavior, attachment validation, two-worker capacity and bounded shutdown.
- `tests/test_workbench_native_events.py`: native replay/ranges, missing-source reporting, tool inputs/completions, stable pages, ordinary business terminal state, file/reasoning recovery and terminal-cache behavior through rollback/commit.

Separate in-memory probes passed: the current Tasks declaration fingerprint is `1afb0380b4280bc061eea303bfbfc43262c0768aad47ead8f5e2d60dbc2da9ff`; a legacy confirmation event written after source capture and Tasks conversion is refused with all schema rows unchanged and the caller transaction still active; caller rollback restores the exact source declaration; FK-on caller transactions are refused. These probes create no persistent database.

Pinned Ruff with `--no-cache` passed on all13 selected runtime Python files. The sole filesystem test root, `/tmp/confirmation-applied-quality-f74r9gft`, was removed by its context manager and absence verified. No temporary review artifact remains.

## Accepted receipts and limits

The earlier canonical QUALITY review independently ran201 backend cases and an overlapping301-case migration/current-schema set, plus135 frontend cases and a production build. The applied SPEC reviewer independently ran184 cases and three cold constructor orders yielding54 tables, ordinary writes, six-element timelines and clean FK checks. Mechanical selection reports397 focused passes and Ruff on21 changed Python files. These are accepted prior receipts bound to the selected hashes; they were not rerun as part of this narrow review, and overlapping counts must not be added as unique cases.

Full API52 passes and retained Store47 passes/3 unchanged unsupported source13 hybrid-fixture failures remain their documented owner/root evidence. The API fixture's final source hash is bound by this review's manifest checks and its final delta received the recorded SPEC inspection. The Store module is not wholly green; its three preserved fixture failures were not skipped, normalized or reclassified by this reviewer. No new failure was found in the independently executed54 cases.

The inspected cumulative preflight JSON records source116→target54→restored116, genuine predecessor `44ee956d10a1360c1ba9b44bf08f4c61e630beb4`, current/predecessor writes through inverse/reforward, exact restored declarations and source markers/ledger, preserved allocators,315 focused migration passes and owned-temp cleanup. Its original `candidate_commit` identifies preparation base `7111ea47546ac7cb242ff7267993868eacec6561`; the installed binding is the selection manifest, not that base label. The checker and runtime proposal hashes match the installed selection. The internal SQLite backup SHA `2584df8d2c60f102f1238e9932b1941fcb5a2f8efcc8a2e051a75a0bab2ecaa7` is distinct from root's unchanged frozen-input SHA `970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`. Root clarified399.437s is whole-preflight elapsed time, including315 tests;382.274s is copy-checker time. This reviewer inspected the receipt/harness and exact candidate binding, but did not rerun the source-copy proof or independently rehash its root-owned input.

The three canonical extra EOF blank lines are cosmetic and unchanged. No full suite, full API/frontend replay, actual-source copy, production database operation, real native CLI/provider, network/outbound action, merge, push, restart or deploy was performed. Latest-source eligibility and final migration risk remain root-owned. Final ten core tables plus the Email ledger, native CLI/fake-provider/UI/performance/CI acceptance and human-approved publication remain open.
