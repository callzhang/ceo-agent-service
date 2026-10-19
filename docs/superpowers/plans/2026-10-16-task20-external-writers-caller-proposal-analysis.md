# Published-eight external Tasks writers: unselected caller proposal

The adjacent [patch](2026-10-16-task20-external-writers-caller-proposal.patch) is a complete **unselected, docs-only proposal** for the eight application files below, against source HEAD `39d20d6615ac34d776bfa89213cf50261ea58b10`. It applies directly to their current source bytes. It does not apply the existing Store/Workbench proposals itself, change any application file, select Tasks20, migrate a database, or establish public-method acceptance. The [companion JSON](2026-10-16-task20-external-writers-caller-proposal-analysis.json) contains every native SQL anchor, exact original parameters, generated SQL, projection consumer and preservation check.

Patch SHA256: `c52007327727e670dd55f434375b8aba3416112f369732717eba998102423415`.

| Application file | Source SHA256 | Candidate SHA256 |
| --- | --- | --- |
| `app/reviewed_candidate_store.py` | `e1e9df1b6c9c247cbc1f8d19f96c1c25f93a02030b4798b26841a4fc39f2f15f` | `8bc37995d49f1d0dafdc8663655de5145335918037ebd5d1cc1b2d15e7525321` |
| `app/dispatcher/adapters.py` | `7b7ab7d8b5225b66d666b8d744fca14bf77c6724ab4c2b739e5c31c149ae49ad` | `e2962e5f5395a98e93b7d3b9cbb342de7f38f8f36c2eb11b7c240ac8402a2cf1` |
| `app/scheduled_task_recovery.py` | `fc521a22d9d90b2d438c7044479fded6ed15b69f375dca4720fa80c02072451f` | `a4168d4a4a7da9b8a55589a50cfd09e433d460e6a8b0d2f672997b5c38aec7e2` |
| `app/reply_task_deferral.py` | `a7ff5a4a8b9077c98a8d51fdfd2abd221e8caade9886b20e09dd93486e79a64d` | `487a00a890d95850a66838f6ece5820fd2c45ebb5a1247dcb02a65e6bd8a6bcc` |
| `app/dingtalk_delivery_repair.py` | `7adf012e42f87b343ca0955ec016e27a540b7da0f34a61f8c2eaf7dcb1bb4c1f` | `02fed7987f18595e3f1e1e961af1ad7fc45520f8dbe8a69bffb01ad46225e924` |
| `app/error_code_migration.py` | `047548f29f5a11aa64b86efe92658f6559a1062d77b83887072d34ede56029f1` | `ed9fbcc4115b6123cc339c91a70046af0f760997db5e3f4d73acc173cedb246e` |
| `app/storage_maintenance.py` | `d1021d40b06d5e78c680625a200fff3bb92eaa6d95338e72ad966c06acf522f6` | `78a897414c9abc9f030344f32c555a9dd8d2df135c6c5fd2a2241e8546bbc34a` |
| `app/work_summary_payload.py` | `dd4011560f7c7293b756a46a82e171ee98ad7b1e202fdd45f4f19cdd9c66c5c6` | `fae1a40234c1c1d49605fee2c833f7d697cc6720866d76b5dd7efac566412e98` |

## Finite source closure

| Native Tasks operation | Count | Proposed conversion |
| --- | ---: | --- |
| Literal UPDATE | 19 | Original native statement/cursor with accepted pure `task8_set_parts` and generated source-field predicates |
| Dynamic UPDATE | 1 | Existing maintenance loop, finite `workbench_turns.error_detail` source field |
| Literal SELECT | 2 | OKR pending count and physical reply-task delivery read |
| Dynamic SELECT | 1 | Existing maintenance byte count for the same error-detail field |
| Existing source-shaped projection name uses | 54 | Twelve local `task20_projection` aliases; three `REPLY_TASK_CURRENT_SQL` uses retain their Store-owned dependency |
| Explicit raw connection codec hooks | 5 | Existing deferral writable connection, three Tasks maintenance connections, summary codec connection |

There are 23 direct native Tasks SQL sites, including the dynamic SELECT/UPDATE pair; 20 mutations are covered. No native Tasks INSERT, DELETE or RETURNING occurs in this slice. The proposal creates none. All 20 pure SET calls have empty helper-bound parameters: original native parameter tuples/lists remain exact for the 19 literal UPDATEs, including `executemany`. The maintenance loop adds only its explicit kind value to the two original `(empty, empty)` tuples, yielding `(empty, kind, empty)` for the corresponding SELECT and UPDATE. No row loop replaces a native bulk SQL statement.

The proposal reuses the accepted Task8 parts directly. It adds no module, SQL parser, SQL rewrite layer, transaction wrapper, audit/authorization/confirmation/effect policy, task field or lifecycle rule. Source-generated Python in the patch contains only finite field references and the existing runtime-built maintenance loop; the proposal preparation script is not runtime code.

## Required preservation details

**Dispatcher claim and release.** Reply claim retains the original-row `attempts+1`, OLD `input_version` to `claimed_input_version`, and physical OLD `current_input_id` to physical `claimed_input_id` inside the same native UPDATE. The latter remains an explicit structural assignment so the native pointer UPDATE contract stays present. Release retains `max(attempts-1,0)`, locks and timestamps. Meeting and work-summary claims/releases and OKR status CAS retain their predicates, identity expressions, rowcount checks and existing lease acquisition/release order. Handler-error retry calculations and terminal decisions remain unchanged.

The two existing `_channel_clause(self, alias="task")` methods keep signatures and dynamic override dispatch. The actual source call aliases are only default `task`, explicit `source`, and native `tasks`; JSON records every call. Only the native `tasks` alias chooses the generated `reply_tasks__channel` field. Projection aliases still use their source-shaped `.channel`, and unknown alias strings retain their original qualified `.channel` behavior. Exact source/candidate strings were evaluated for both methods at all three actual aliases plus one unobserved alias. Channel membership remains `dingtalk/wechat` for Reply and `scheduled` for ScheduledExecution. No added join or policy branch selects channels.

**Physical delivery identity.** The raw `apply_delivery_reverification` query continues to select `tasks.*` with the exact `record_type='reply_tasks' and legacy_key=?` predicate and original parameter. It adds generated `execution_generation`, `status`, and `error` under the source aliases consumed downstream. The original physical `task["id"]` and `legacy_key` remain present. All downstream task fields are inventoried: `id`, `execution_generation`, `status`, `error`. The following UPDATE keeps its physical `where id=?`; it does not acquire a new kind/CAS/effect check. Both existing uncertain-delivery CAS UPDATEs preserve error, generation and status predicates and every receipt/attempt write and exception path.

**Typed work-summary payload.** The source business `payload_json` is the typed leaf `work_summary_inputs__payload_json`, distinct from the outer structural `tasks.payload_json` container. Compression SET uses `task8_set_parts(..., update_sql={"payload_json": "?"})`; its original equality CAS compares the generated business leaf to `current`. Terminal compaction similarly sets the business leaf to the literal string `{}` while updating the original provenance fields. It never overwrites the whole outer container with the compressed body or `{}`. The source codec, BLOB/text decisions, batch bounds, iteration, counters, quick-check, commit/rollback and error propagation remain unchanged. Exact pure SQL rendering records this distinction.

**Dynamic maintenance scope and counters.** In the published-eight source declaration, `error_detail` belongs only to `workbench_turns`; native Workbench NULL checks force the field to NULL for every other source kind (`app/task_storage.py:519-559`). Consequently the source `error_detail<>empty` predicate already selects only that kind. The candidate loop records the kind instead of the physical table name and retains its single SELECT, byte-reduction expression, native SET and `.rowcount` accumulation. An explicit `record_type=?` and the finite generated field express the same eligible rows. Existing event cleanup, FTS rebuild, quick-check, commit, VACUUM and checkpoint behavior remain exactly in place; this proposal changes no retirement or cleanup policy.

**Independent raw connections.** `register_task8_functions(db)` is added immediately after the existing writable deferral `sqlite3.connect`, and inside each existing Tasks maintenance/summary connection context. Deferral read-only connections keep their source behavior. The email-only compactor has no Tasks8 call and receives no hook. All original connection options, PRAGMAs, context expressions, begin/commit/rollback/close statements and exception paths are AST-identical. The hook does not begin a transaction. Shared Store/Email/Workbench `_connect` registration and complete constructor selection remain root-owned.

**Other writers.** Selected-candidate wakeup preserves generation/status CAS; scheduled supersession preserves exact source eligibility and attempt resolution; deferral apply/resume preserve their receipts and transaction/backup predicates; meeting error migration preserves dry-run, `executemany`, source identity lookup and all original migration values. Pure legacy-key `task_id_expression` calls remain valid and unchanged.

## Verification actually performed

Pinned Python: `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python`, Python 3.13.12. The patch passed exact `git apply --check` and actual application in a task-owned source copy, produced each pinned candidate byte string, then passed reverse check/application and recovered every source byte. All eight candidate modules parse/compile, pass Ruff and import from a separate copied `app/` tree without any constructor. A fresh import run with `sqlite3.connect` replaced by a rejecting function also passed, proving that this import check opened no SQLite connection. The task-owned temporary tree was removed and its absence verified. No public DAO, provider or native CLI method was called.

All **156 qualified function definitions**, including nested functions, retain exact signatures, defaults, decorators and return annotations. **131 unrelated bodies remain AST-identical; 25 bodies change.** Within changed bodies, the complete remaining AST is identical after only the named SQL-expression changes, five codec calls, two removed local projection imports, two channel-expression translations and the finite maintenance dictionary/loop/parameter adjustment. All 228 original native cursor calls remain in place. Context expressions, explicit connection/commit/rollback/close calls and transaction/PRAGMA SQL ASTs are exact. The two maintenance tuples are the only changed existing SQL parameters.

All 20 proposed helper expressions were evaluated as pure functions and returned zero bound parameters. Nineteen literal UPDATE SQL expressions rendered with exactly the source placeholder counts; structural pointer, OLD-value and typed work-summary-leaf checks passed. These are source/static and pure-builder checks. No SQLite connection or SQL, public constructor/method, real database, migration, network, provider, native CLI, service, main, push or deploy action was executed for this proposal. No regression/public behavioral claim is made.

## Remaining root-owned integration and acceptance

- The unchanged Store-owned `REPLY_TASK_CURRENT_SQL` imports and Store compaction calls require the exact existing-eight Store proposal (SHA256 `691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c`). This patch applies directly to these eight files but is usable against a Tasks20 runtime only with that companion.
- Deferral `_snapshot` still reads `business_object_tasks`; the residual twelve-kind external-reader closure is separate from this published-eight mutation scope. It must be converted before source-table retirement.
- Maintenance `_clear_workbench_event_bodies` still uses existing `runs` source fields. The complete Runs conversion must supply its release dependency; this proposal leaves it untouched.
- Shared connection registration, historical/source construction, final target manifests and complete actual/fresh constructors require coherent root selection; copied import does not prove any of them.
- Genuine public-method source/target differential tests must establish native error priority, transaction/CAS/rollback behavior, counters, physical/public ID distinction and stored scalar types. No such public proof ran here.
- Copied forward/current-fact inverse/predecessor writes/reforward, real native/UI observation and the final ten-core-plus-ledger acceptance remain open. This proposal is unselected.

## Exact changed function bodies

- `app/reviewed_candidate_store.py`: `ReviewedCandidateStoreMixin.wake_selected_candidate_execution`.
- `app/dispatcher/adapters.py`: `ReplyQueueAdapter._channel_clause`.
- `app/dispatcher/adapters.py`: `ReplyQueueAdapter.claim`.
- `app/dispatcher/adapters.py`: `ReplyQueueAdapter.release`.
- `app/dispatcher/adapters.py`: `ReplyQueueAdapter.handle_handler_error`.
- `app/dispatcher/adapters.py`: `ScheduledExecutionQueueAdapter._channel_clause`.
- `app/dispatcher/adapters.py`: `MeetingQueueAdapter.claim`.
- `app/dispatcher/adapters.py`: `MeetingQueueAdapter.release`.
- `app/dispatcher/adapters.py`: `OkrReviewQueueAdapter.metrics`.
- `app/dispatcher/adapters.py`: `OkrReviewQueueAdapter.claim`.
- `app/dispatcher/adapters.py`: `OkrReviewQueueAdapter.release`.
- `app/dispatcher/adapters.py`: `WorkSummaryQueueAdapter.claim`.
- `app/dispatcher/adapters.py`: `WorkSummaryQueueAdapter.release`.
- `app/scheduled_task_recovery.py`: `close_superseded_scheduled_reply_tasks`.
- `app/reply_task_deferral.py`: `_connect`.
- `app/reply_task_deferral.py`: `apply_deferral`.
- `app/reply_task_deferral.py`: `resume_deferral`.
- `app/dingtalk_delivery_repair.py`: `collect_native_chat_readback`.
- `app/dingtalk_delivery_repair.py`: `apply_delivery_reverification`.
- `app/dingtalk_delivery_repair.py`: `apply_native_chat_readback`.
- `app/error_code_migration.py`: `migrate_legacy_meeting_projections`.
- `app/storage_maintenance.py`: `compact_settled_reply_inputs`.
- `app/storage_maintenance.py`: `compact_terminal_work_summary_inputs`.
- `app/storage_maintenance.py`: `compact_native_duplicates`.
- `app/work_summary_payload.py`: `migrate_work_summary_payloads`.

Canonical source pins:

- `app/task8_storage.py`: `bdc1809e75725d7364058b0e4e1c81a53cf9aa11621bda44ed2bce68823ff03f`.
- `app/task20_definitions.py`: `cf58403bcc9dc7bf4c3ef5b9a777ff941c7a6de210e04b29e4cb7abb1e0df4d0`.
- `app/task_storage.py`: `e53c32df9a8ff18d0c2804071267c7e630a19c8451f1590e49c977b4e4ad9f86`.
