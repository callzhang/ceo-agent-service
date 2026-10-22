# Unselected Tasks20 remaining-reader closure

The adjacent patch completes the remaining bounded current projection consumers after the eight existing caller proposals. It changes no application file in this worktree and selects no runtime layout. Exact current publication selection and constructor/migration integration remain root-owned. This is a static/copy/import proposal, with **zero SQLite connections or public-method executions**.

Patch: [2026-10-16-task20-remaining-readers-caller-proposal.patch](2026-10-16-task20-remaining-readers-caller-proposal.patch). Evidence: [analysis JSON](2026-10-16-task20-remaining-readers-caller-proposal-analysis.json). Patch SHA256 `618c2033a0ca47185819dad4a24a9f4f6409d1d368a1655bc8da4b13efa8898c`.

## Exact prerequisites

Apply these exact patches, in this order, before this patch. This ordering was checked on an isolated copied `app/` tree. Existing proposal contents are not duplicated here.

1. `docs/superpowers/plans/2026-10-16-task8-store-caller-proposal.patch` — SHA256 `691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c`.
2. `docs/superpowers/plans/2026-10-16-task8-workbench-caller-proposal.patch` — SHA256 `66d1c19cfee4fd636d596400b228b0926f9fca46aee177d180a6701b33386c26`.
3. `docs/superpowers/plans/2026-10-16-task12-email-native-caller-proposal.patch` — SHA256 `610bd87fb49ee22fca098fdaf887ccf62bd37e5462a9a8d2945440057304b8cc`.
4. `docs/superpowers/plans/2026-10-16-task12-store-misc-caller-proposal.patch` — SHA256 `aeb1ffd38995428d61f9f01bf4d0a58dfdd223b510b358ff6a7c2e01976706c4`.
5. `docs/superpowers/plans/2026-10-16-task12-feedback-caller-proposal.patch` — SHA256 `9bf3323406b39fc06fff84496bd6fb2bcda6c3eb990e96a550b878e433a8105d`.
6. `docs/superpowers/plans/2026-10-16-task12-workbench-children-caller-proposal.patch` — SHA256 `f9e64c543a9aec532184a99ee9832d691635bdbaf4dfade9c515e25e3f268cc7`.
7. `docs/superpowers/plans/2026-10-16-task20-external-writers-caller-proposal.patch` — SHA256 `c52007327727e670dd55f434375b8aba3416112f369732717eba998102423415`.
8. `docs/superpowers/plans/2026-10-16-task20-console-readers-caller-proposal.patch` — SHA256 `a823ff570d1d8b6045f1289853831f7893531f78badc1281134bb80389f717df`.

The canonical Task12 helper is `127311aa2ed303066fc3dd325a5642c0e61725a2d8db3b5bedfc218e804e013f`. This proposal uses its existing `task12_projection` and `task12_set_parts` APIs only; no helper extension is needed. The JSON pins all helper blobs and every prerequisite patch SHA256.

## Current reader roles

Eight modules replace their remaining native `task_storage` projection imports with nine local `task20_projection` constants, placed after existing imports. Every function in these eight modules is AST-identical to its post-prerequisite body. Existing SQL text, parameter binding, aliases, row conversion, predicates, sort order, exception handling and connection ownership remain unchanged; only the selected source-shaped relation changes.

| Module | Current read path and reason for local selection |
| --- | --- |
| `attempt_rerun_presentation.py` | Current Store connection joins run diagnostics with Reply public IDs and business object keys. Its wording/eligibility behavior is untouched. |
| `minutes_owner_backfill.py` | Current Store connection checks an existing Work-summary source type/ref before queuing. The existing deduplication decision and enqueue path remain exact. |
| `task_project_repair.py` | `_archive_candidates` reads Work-summary source provenance; `_database_fingerprint` counts current source inputs. Both receive the current connection. The optional `historical_db_path` branch reads **only `work_projects`**, never this projection, so its historical source reader is untouched. No whole-tool compatibility claim is made for unrelated still-native Project/Task families. |
| `cli.py` | Existing worker-channel discovery and explicit task lookup use the selected Reply source shape. No command parsing, execution or CLI policy changes. |
| `agent_cli.py` | `_current_task_generation` and `_read_bound_task_skill` use raw read-only connections to the selected service database. Both Reply and input projections retain public numeric IDs and existing current/claimed input semantics. No raw connection registration is added: these read projections decode via SQLite built-ins and do not call the scalar write codec. |
| `task_semantic_import.py` | Current source fingerprint, import planning and supporting-source verification read Work-summary facts. Its input path is the database whose manifest is built/checked/applied; “legacy” describes retained business rows within that database, not an immutable predecessor schema reader. All import policy, manifests and writes remain exact. |
| `email_store.py` | Five Reply reads cover current generation, bound-run joins, queue status counts, task existence and delivery/recovery reads. The existing Email Task12 proposal converted classifier/historical families but left this original Reply import. This patch closes only that import; the earlier Email changes are preserved byte-for-byte outside the import/assignment delta. |
| `workbench/native_events.py` | Exact previously identified companion: terminal cache eviction and event hydration use current Workbench turn projections. Its native trajectory readers consume native files separately and remain AST-identical. `task_id_expression` remains the original physical parent lookup helper. |

No global `task_storage` constant is rewritten. Local projections therefore cannot alter historical factories/inverse code through a shared imported constant.

## Classification parent and explicit compaction

`AutoReplyStore._runtime_operation_parent_exists` changes only the email-classification relation to `task12_projection('email_agent_classification_tasks')`. The existing TEXT `workload_key` parameter, `task_id=?`, `status='running'`, boolean result and every other workload branch remain exact. There is no cast of task identity to structural Tasks ID or hidden source ROWID.

`compact_done_email_classification_inputs` keeps its explicit caller-owned connection, `begin immediate`, complete fetch before iteration, existing JSON compactor, per-row update, status recheck, cursor rowcount accounting, UTF-8 logical-byte accounting, quick-check, commit and close. The new code:

- Registers the already accepted scalar codec on this existing raw connection before its original transaction. No wrapper, transaction owner, retry or source/target detection is added.
- Reads the same source fields and original `status='done'`, `json_extract(..., '$.input_compacted') is not 1`, `order by task_id` predicates through `task12_projection`.
- Runs one native UPDATE per original row using `task12_set_parts` for `input_json`, filtering the exact kind plus typed generated `task_id` and `status`. The original two bindings `(compact, row['task_id'])` remain exact.

Native classification `task_id` is nullable TEXT and is **not** its hidden source ROWID. A NULL task ID still does not match `task_id=?`; the patch does not invent a NULL-equality branch. The compactor never substitutes global physical `tasks.id` for that key. Hidden source ROWID remains numeric `legacy_key`; this operation neither selects nor updates it. No new field, source row ordering, guard, fallback, classifier policy or compaction policy is added.

## Preserved historical/source roles and root integration

| Native import/factory remaining | Traced role / remaining root obligation |
| --- | --- |
| `task_storage.py` | Frozen original wide-Tasks DDL, source projections, capture/migrate/restore and historical declaration validation remain the source oracle. Its projections must stay native. `task_id_expression` and `task_physical_id` use structural kind/key/id coordinates that remain valid under Tasks20. |
| `workbench_storage.py` | `WORKBENCH_PROJECTIONS`, captured columns and native insert/schema helpers serve the historical source13 conversion/inverse and exact source declaration profile. `restore_workbench_schema` is historical inverse code. Keep its native imports; target constructor/auxiliary recognition must be handled explicitly in root integration. |
| `task_schema_release.py` | `publication_task_sources` and `publication_meeting_sources` explicitly serve historical marker branches from 11.2 through current 2026-10-14.1. Those currently published branches still describe the native wide layout and must retain their source projections. The new selected Tasks20 marker branch must be added alongside them during complete release selection, using local target projections/status fields and the exact accepted target recognizer. This proposal invents neither a marker nor a recognizer. `TaskSchemaRelease` itself remains the historical 11.2→11.3 protocol. |
| `workbench_schema_release.py` | Historical source13→Workbench release and rollback import validation helpers only, with no remaining runtime projection import. Preserve exact historical behavior. |
| Store's remaining `SOURCE_COLUMNS`, `create_task_schema`, `task_schema_is_current`, guards | Constructor and supported-source/profile recognition are explicit root scope. Existing source oracle imports remain here until root makes the coupled target selection. They are not hidden runtime readers missed by this proposal. |
| `email_classification_schema.py`, `email_current_schema.py` | Original native classification factory/current counterpart schema maps are source oracles needed by historical source construction. Root must stop native recreation for the selected target and supply dependent schema metadata explicitly, without globally repointing historical factories. |
| `state_publication_sources.py`, `email_publication_sources.py` | Existing marker-pair and complete declaration contracts stay untouched. Their new selected-target role requires the root's coupled publication change. |

The JSON records byte identity and source hashes for these eight preserved source-oracle modules. The remaining `app.task_storage` imports after all nine patches are exhaustively recorded there: only the historical modules above, Store's source constructor/profile helpers, and existing structural identity helpers in dispatcher, Workbench and maintenance remain. No current service module retains a native source-shaped Task projection import outside these explicit historical/selection roles.

## Exact source pins

“Working source” is this worktree before proposals. “Prerequisite result” is the exact cumulative base of this incremental patch. The first six import-only modules plus native-events have identical working/base blobs; Store, Email and maintenance include earlier proposal changes.

| File | Working source SHA256 | Prerequisite result SHA256 | Candidate SHA256 |
| --- | --- | --- | --- |
| `app/attempt_rerun_presentation.py` | `e273f8fe73dceafa505dc8761c41772cb782a49b501ff0dcf398c5b97228020d` | `e273f8fe73dceafa505dc8761c41772cb782a49b501ff0dcf398c5b97228020d` | `c457fa0379cb079d50ea50dac574ef1152b4f115a019f30aeaa908c288a9439f` |
| `app/minutes_owner_backfill.py` | `692d7410feb72dfc054c586802f45aeddbd77332d6e5695e24dc85adb807307e` | `692d7410feb72dfc054c586802f45aeddbd77332d6e5695e24dc85adb807307e` | `f7cea67d2d13c56f553262d2f189bdafc6dbed20d52ae9b5b32a5047e5566060` |
| `app/task_project_repair.py` | `387185c2da3e682f6311199bc4ec07866744f1de95b121e5734a60158c9db2b6` | `387185c2da3e682f6311199bc4ec07866744f1de95b121e5734a60158c9db2b6` | `614dd67df3da2a02dc177b09ec216a64604002d96dafc5b7c5c8873e6c773364` |
| `app/cli.py` | `b573fc0d095916f6d0b96cf5eba7ffb0d5876c47962f29f99dc809fbfc3ac1b5` | `b573fc0d095916f6d0b96cf5eba7ffb0d5876c47962f29f99dc809fbfc3ac1b5` | `eb3d201b9600aa76d371753501ec88dc6387d1b22f01ef254342d1151168f79d` |
| `app/agent_cli.py` | `cd0476dc18e408ebcc069c8bffb617c71f0c53a47880bfaa7833694963018274` | `cd0476dc18e408ebcc069c8bffb617c71f0c53a47880bfaa7833694963018274` | `fcd40c67fcfbec339a1c9ece44a0e77f5ba7b2a93837053ee18712216df105ac` |
| `app/task_semantic_import.py` | `e947e42c797aa05edfd8b1916f2b004bfcb9d1d55deea38ae02abc83edd5e035` | `e947e42c797aa05edfd8b1916f2b004bfcb9d1d55deea38ae02abc83edd5e035` | `ae848ac19c8124772a2ff4c4480d95681074f4550991c37183eb5049170c47ff` |
| `app/email_store.py` | `c7f80cd9607cc0b1d500a698f341065e3b22f41b73a536f0ab3c8af88db5ecc9` | `f4554c4233c2a2ee29fd74dc30532164f27d06836d5481d836233f88b3a3a89e` | `27042bcf1233ddf31c0843e337d1aeb8863091aa3ab85d9e6bc9b7970c2f90e6` |
| `app/workbench/native_events.py` | `627dea69c179821fc18e1e97e37c6c96615134719c0536986bc0fa6f23f6a761` | `627dea69c179821fc18e1e97e37c6c96615134719c0536986bc0fa6f23f6a761` | `1cf7a6de80676539fa5f5945cccda4327ac61eb4f1e059ffb40cefc9876b85e1` |
| `app/store.py` | `ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f` | `13dbf3724919fbeac89b7d2aca895ca2651339b3fcb35a322e56ef10d108d55b` | `2d82e4140b34a38025b9b7129d4c90d6262e806d49a9239401f990b633a8c6e5` |
| `app/storage_maintenance.py` | `d1021d40b06d5e78c680625a200fff3bb92eaa6d95338e72ad966c06acf522f6` | `78a897414c9abc9f030344f32c555a9dd8d2df135c6c5fd2a2241e8546bbc34a` | `07f965f76e5583f0edd09be82ec53be838604594ab469bcafe93f301ed5f503c` |

## Verification and limits

Pinned Python `/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B` compiled all ten copied candidates in memory. Ruff `--no-cache` with the repository configuration passed. All ten copied modules imported with `sqlite3.connect` replaced by an assertion-raising function; no invocation occurred. Pure builder assertions distinguish original native Reply from target Reply, require the classification source ROWID alias, preserve the typed task key and confirm scalar SQL uses `ceo_native_json(?)`.

All **1,460 function signatures/decorators/return annotations** remain exact. Only **two** existing function bodies change; **1,458 other function ASTs** remain exact. All **1,976 execute/executemany parameter and keyword expressions**, existing connect/close/commit/rollback and begin/PRAGMA ASTs remain exact. Other module-level statements are unchanged after removing only the scoped imports, nine projection assignments and two permitted function deltas. Exact patch reverse restored all ten cumulative-base blobs; reapplication reproduced every candidate SHA256. Working source hashes were checked again before the documentation commit.

These checks prove patch applicability and bounded source preservation. They do not prove SQLite query results, public DAO semantics, target cold construction, migration/inverse, publication recognition, production runtime or final table count. Root still needs corresponding actual/fresh public fixtures, including classification nullable task IDs, source ROWID/physical-ID separation, running/terminal parent cases, empty/idempotent compaction and scalar/rowcount/rollback behavior. Current-reader fixtures must exercise the real methods with unrelated business dependencies prepared as required; the unchanged-signature/AST evidence is not a substitute for those semantic tests.

No app/test edits, database files/connections, provider calls, native CLI calls, constructor calls, migration selection, main operation, push or deploy occurred. Task-owned copied files were removed after verification. This patch remains **unselected**.
