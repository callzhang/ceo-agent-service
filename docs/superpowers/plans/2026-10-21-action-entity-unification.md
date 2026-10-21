# Action Entity Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 reviewed 具体外部动作、邮件直接动作和 prepared/sent 消息统一成一种可执行 Action，退休目标来源的独立类型、身份分派、双写和专用索引。

**Architecture:** 先从真实 schema/caller 固定字段、基数、状态、DDL 和回退合同，再实现单一 Action 存储接口，切换执行/恢复及 API/UI，最后以显式迁移原子发布。没有外部操作的方案、审查、错误和工作进展不作为 Action；它们的退出属于总路线后续单元，不在本单元偷偷迁成另一类 Action。

**Tech Stack:** Python、SQLite JSONB、pytest、React/TypeScript、Vitest、原生 Codex CLI、现有成对 schema release 与 app.deploy。

**Spec:** `docs/superpowers/specs/2026-10-21-action-entity-unification-design.md`。

## Global Constraints

- 保留 provenance、必要状态、已采用业务事实和实际执行回执。
- 原生 trajectory 是交互原文的唯一来源；有独立可验证来源的正文不另存副本。
- 邮件预览保留至多 1,000 Unicode 字符。
- 不新增无真实来源的业务字段；新增技术引用必须标明旧字段来源和读取必要性。
- 不需要运行时兼容旧结构、旧 DTO 或旧类型名称；只保留必要的显式迁移与回退工具。
- 不新增或改变审查、授权、确认、取消、重试和外部结果核对策略。
- 先在隔离 worktree 和数据库副本验证；原生 CLI 验收的对外发送使用替身。
- 正迁后新增的业务事实和回执必须能通过当前数据逆迁恢复；不以恢复旧数据库备份冒充 schema 回退。
- 只有新备份验证成功后才清理旧备份；发布使用现有 app.deploy。

## Review Focus

1. 多旧类型同号公开 ID、空 external key、孤立历史 Sent：不能误合并或凭正文制造引用；Task 1/2/5。
2. 多个 Run/Task 观察同一真实发送、内容相同但实际发送不同：身份保持真实基数，不能重发或合并真实回执；Task 2/3/4。
3. provider 已接受、本地未完成，重启后旧 claim/lease 仍有效：继续既有恢复语义，不能将 unknown 当失败后重发；Task 3/6。
4. 新版本运行后回退：新增/修订/撤回/新回执都保留，不能借旧副本覆盖；Task 5/6。
5. 原文不可读、历史回执不同、provider 返回 SQL NULL 对比 JSON `null`/`{}`：明确来源不可用，保留独有事实；Task 1/2/5/6。

---

## 执行边界与源证据

本轮只写文档；未执行未来任务的测试或任何副本迁移。规划 worktree `/Users/derek/.codex/worktrees/action-entity-plans/ceo-agent-service`，分支 `codex/action-entity-plans-20261021`，基线 `7c37664518f04f8afcefd937b53484bef503a507`。当前 source marker 为 Store19.12／Email53；正式执行重新核对 origin/生产，不将旧快照行数或版本硬编码为目标版本。

沿用 Derek 已选择的 Subagent-Driven。每任务依次实施、规格审查、质量审查；字段/DDL 冻结在 Task 1 的审查点先通过，Task 2-7 随后必须完成。方案/审查政策不在此次范围；如存储改造必须改变判定政策，停止该策略变更并单独提出，不顺手实现。

2026-10-21 旧测量：四表及索引434.59 MB，DB493.26 MB，223个索引；两张调度 pending 局部索引有19.05 MB机会，但属 Task/Run 后续单元。不能以减少这两张索引替代本单元类型退休。

## 文件结构与职责

| 文件 | 处理范围 |
|---|---|
| `tools/action_entity_contract.py`（新增） | 只读源字段/约束/引用/caller和唯一归属核对，输出文档级契约与统计；不调用 Store 初始化、不读取正文内容到报告 |
| `docs/action-entity-field-map.json`（新增） | 每个源字段的 owner、目标字段/引用、消费者及 inverse 规则；技术清单，不是新业务表 |
| `docs/action-entity-target.sql`（新增） | 经审查的静态目标 DDL；每列关联 field-map 来源，包含索引/FK和状态值 |
| `app/action_entity_storage.py`（新增） | 无 origin 参数的实体读写、CAS和唯一结果所有权 |
| `app/current_action_schema.py`, `app/action_storage.py` | 接入静态新声明，退休覆盖范围的旧类型/投影/索引；未覆盖类型明确保持原实现 |
| `app/shared_action_lifecycle.py` | 删除已被替换的来源分支，不另包一层旧 dispatcher |
| `app/reviewed_candidate_store.py`, `app/system_executor.py` | 具体动作 identity/结果写入使用新 owner；现有审查/选择及核对判定不变 |
| `app/email_store.py`, `app/email_provider_actions.py`, `app/email_action_reconcile.py` | 直接动作领取/重试/结果读写切换；历史授权只读真实旧 owner，不复制当前配置 |
| `app/store.py`, `app/outbound_postfix.py`, `app/service_message_sender.py` | prepared/sent/recall及重启读取改造，仅改目标方法 |
| `app/history_actions.py`, `app/history.py`, `app/web_api/registration.py`, `frontend/src/`实际调用点 | 统一 ID、详情、回执、撤回、反馈读取，不保留旧 DTO 分派 |
| `app/action_entity_schema_release.py`（新增） | 显式正逆迁移，当前数据关系重建；不保存旧行副本 |
| `app/store_schema_release.py`, `app/email_schema_release.py`, `app/current_database_schema.py` | 只注册完整配对发布，不在 constructor 自动修补 |
| 下列任务的精确测试文件 | 每任务自己的失败/通过周期；无生产测试、无整套串行 pytest |

## Task 1: 冻结实体字段、关系和静态 DDL

**Files:** Create `tools/action_entity_contract.py`, `tests/test_action_entity_contract.py`, `docs/action-entity-field-map.json`, `docs/action-entity-target.sql`。
**Consumes:** 设计第4节来源；`current_action_schema.current_action_payloads()`；实际 `action_storage`、`prepared_action_definitions`、`sent_result_definitions` 和现有 caller。
**Produces:** `inspect_action_entity_source(database: Path) -> dict`、`validate_action_entity_contract(contract: dict) -> None`。函数在 `tools/action_entity_contract.py`；后者遇到无 owner/来源/逆迁规则、未覆盖字段或含来源类型分派的目标实体即抛 `ValueError`。CLI `python tools/action_entity_contract.py --database PATH --output PATH`，成功退出0、失败非0；输出只有统计、字段名、哈希和代码定位。

- [ ] **Step 1: 写失败测试。** `test_all_source_fields_have_one_owner` 断言覆盖真实源字段集合；`test_no_unsourced_business_column` 断言新DDL业务列都能反查源；`test_equal_public_ids_are_not_identity` 断言同号不同源不合并；`test_multiple_observers_do_not_create_multiple_effects` 断言现有外键/业务键相同才合并；`test_missing_parent_or_different_receipt_is_not_erased` 断言保留独有事实；`test_inventory_leaves_schema_and_rows_unchanged` 比较前后哈希。
- [ ] **Step 2: 运行** `python -m pytest -q tests/test_action_entity_contract.py`；预期新工具缺失而失败。使用纯合成小库，不以生产实际数量作为断言。
- [ ] **Step 3: 实现上述两个函数及CLI。** 按设计逐个展开六个主体来源及 observer 引用；逐字段固定 retain/reference/retire 及依据。真实回执集合的成员只能来自现有 identity/结果/时间字段，不保留旧 attempt payload。不能找到独立来源的独有事实保留；正文只能在验证可重读后退休。Action存储类型统一为 `action`，operation沿用已有操作名称；静态字段/状态映射和唯一约束写入DDL，不用旧类型名称选布局。旧整数ID只供显式迁移映射，正常查询只认统一ID/稳定外部身份。
- [ ] **Step 4: 执行同一测试命令并做只读数据库副本检查。** 预期全通过；产出逐字段映射、来源基数、冲突分组数量、每个索引保留/删除理由及目标DDL。清单须区分“记录类型全量退休”与“仅目标来源退出、其他实际caller仍使用”，未全量退休不算类型消失。审查若不能证明一个字段可删，就在契约中保留其业务归属，不把未知当删除许可。
- [ ] **Step 5: 规格/质量审查后提交。** `git add tools/action_entity_contract.py tests/test_action_entity_contract.py docs/action-entity-field-map.json docs/action-entity-target.sql`；`git commit -m "docs(actions): freeze source-backed action entity contract"`。只有此任务产物审查通过后进入Task2；本任务不能作为整个工程完成。

## Task 2: 实现真正的 Action owner

**Files:** Create `app/action_entity_storage.py`, `tests/test_action_entity_storage.py`; Modify `app/current_action_schema.py`, `app/action_storage.py`, `app/current_database_schema.py`。
**Consumes:** Task1的静态DDL及逐字段映射；具体列/约束直接从已审查DDL实现，不在运行时加载文档JSON或按旧origin分派。已有类型字段用于范围外过渡记录；本单元所有新动作使用同一实体字段集，不按操作选择不同payload布局。
**Produces:** 下列接口均在 `app/action_entity_storage.py`，`db: sqlite3.Connection` 由调用方拥有事务；`Mapping` 来自 `collections.abc`，所有函数第一个参数标注为 `db: sqlite3.Connection`；返回dict的字段集合严格等于已冻结实体字段集合：

```python
create_action(db, *, values: Mapping[str, object]) -> dict
get_action(db, action_id: int) -> dict | None
claim_action(db, action_id: int, *, expected: Mapping[str, object], changes: Mapping[str, object]) -> dict | None
record_action_result(db, action_id: int, *, expected: Mapping[str, object], changes: Mapping[str, object]) -> dict | None
```

- [ ] **Step 1: 写** `test_create_claim_result_uses_one_action`, `test_stale_claim_does_not_overwrite`, `test_one_effect_multiple_observers`, `test_equal_body_different_effects_stay_distinct`, `test_distinct_historical_receipts_survive`, `test_null_text_null_and_empty_json_remain_distinct`；断言同一Action物理ID贯穿领取/结果、旧CAS竞争结果一致、provider调用计数和真实身份基数一致。
- [ ] **Step 2: 运行** `python -m pytest -q tests/test_action_entity_storage.py`，预期缺失模块失败。
- [ ] **Step 3: 实现四接口和新DDL。** 无 origin/source_variant 参数，不生成每来源一套payload；不通过旧DAO转发。仅采用现有状态转换和CAS条件，schema变更本身不新增调度/重试策略。范围外旧类型不改，但不得被这四接口调用。结果重复引用只保留一个owner；不同真实回执按Task1合同保留。
- [ ] **Step 4: 运行** `python -m pytest -q tests/test_action_entity_storage.py tests/test_prepared_action_owner.py tests/test_execution_owner.py tests/test_sent_result_references.py`。记录旧用例因DTO退休的必要迁移，不弱化原幂等/回执断言。
- [ ] **Step 5: 提交** 新模块、schema及测试，并更新 `docs/architecture.md`、`docs/runtime-mechanism.md`，注明候选未上线；提交 `refactor(actions): introduce single action entity owner`。

## Task 3: 切换真实执行与恢复，删除旧分派

**Files:** Inspect the actual caller paths in `app/shared_action_lifecycle.py`, `app/reviewed_candidate_store.py`, `app/system_executor.py`, `app/email_store.py`, `app/email_provider_actions.py`, `app/email_action_reconcile.py`, `app/store.py`, `app/outbound_postfix.py`, and `app/service_message_sender.py`; modify only files that own the storage call being converted. Create `tests/test_action_entity_callers.py`. The implementation record must explain any inspected file left unchanged with its concrete call path and storage owner.
**Consumes:** Task2四接口，现有 provider adapter 和既有事务/批准/恢复判定。
**Produces:** 覆盖来源全部只调用统一owner；旧来源专用写入和已无调用的DTO/投影删除。

- [ ] **Step 1: 写参数化测试** `test_reviewed_email_prepared_share_owner`，三来源分别走实际caller后均读取同一实体字段集；`test_restart_after_provider_acceptance_does_not_resend` 在既有provider返回和本地完成之间中断，恢复后替身发送总计1；`test_two_claimants_only_one_provider_call` 并发claim只允许一个调用；`test_old_revision_keeps_its_approved_binding` 保留原审查版本；`test_retry_keeps_original_external_identity` 保留重试实际身份与历史回执。
- [ ] **Step 2: 运行** `python -m pytest -q tests/test_action_entity_callers.py`，预期旧caller不走新owner的断言失败。
- [ ] **Step 3: 替换真实caller。** 邮件配置从不可变父计划读取；不把配置版本复制到子Action。prepared的准备/领取/接受回执分别提交于其既有边界。已接受但未知最终效果仍用既有恢复，不自动重发。删除范围内 `origin` 分支和旧allocator；仅迁移模块可理解旧namespace。退订专有流程保持范围外原实现，不改授权或核对规则。
- [ ] **Step 4: 运行并记录** `python -m pytest -q tests/test_action_entity_callers.py tests/test_native_reply_reconciliation.py tests/test_shared_action_lifecycle.py tests/test_outbound_postfix.py`，并对 Task1消费者清单做调用引用检查；预期覆盖范围的旧writer调用点为0，新增审查/授权策略为0。保留并运行整个既有混合写入/History/Attention/queue/important测试，不删除、不跳过、不弱化任何断言。若当前owner写断言通过而既有History读取断言因Task4 reader尚未转换而失败，Task3报告精确失败层和未到达的断言；这不是Task3运行时扩围的理由，也不能报告完整组合验收通过。Task4必须在真实读取转换后使该原测试节点及本Step完整命令全绿，作为组合验收门槛。
- [ ] **Step 5: 提交** 本任务文件和两份机制文档；提交 `refactor(actions): route effect execution through action owner`。此时仅在分支，不将不完整schema发布main。

## Task 4: 切换详情、回执、撤回和反馈读取

**Files:** Modify `app/history_actions.py`, `app/history.py`, `app/web_api/registration.py`, `app/store.py` 中 Sent/recall读取、`frontend/src/` 实际调用文件；Create `tests/test_action_entity_views.py`。
**Consumes:** Task2 `get_action`；Task1 observer/Task/Run关系；Task3真实执行数据。
**Produces:** 所有目标页面/API以Action身份读取，保留外部消息身份、时间、撤回与反馈语义；没有旧类型DTO路由。

- [ ] **Step 1: 写** `test_history_detail_reads_action_owner`, `test_multiple_observers_show_one_send`, `test_recall_updates_existing_action`, `test_feedback_token_resolves_action`, `test_source_missing_is_visible`。分别断言详情正文/目标/回执一致；两observer不生成两条effect；撤回不会改发送回执或制造新发送；token仍定位原消息；来源不可用明确展示。
- [ ] **Step 2: 运行** `python -m pytest -q tests/test_action_entity_views.py`；预期旧读取接口/类型分派失败。
- [ ] **Step 3: 更新真实API/UI调用方并删除旧DTO。** 前端路径以Task1实际caller清单为准；在本任务Files清单补上精确文件名再编辑。不要保留fallback取“最新旧记录”的代码。跨Run共享同一外部回执只增加关系，不覆盖原结果。
- [ ] **Step 4: 运行** `python -m pytest -q tests/test_action_entity_views.py tests/test_history_actions.py`；按 `frontend/package.json` 已有脚本执行受影响组件测试/构建；在隔离副本控制台实际打开History/详情/回执/撤回替身/反馈入口并读回结果。不能用HTTP200替代页面业务字段核对。
- [ ] **Step 5: 提交** API/UI/测试及两份机制文档；提交 `refactor(console): read sends and receipts from action entity`。

## Task 5: 实现显式正迁与当前数据逆迁

**Files:** Create `app/action_entity_schema_release.py`, `tests/test_action_entity_schema_release.py`; Modify `app/store_schema_release.py`, `app/email_schema_release.py` 和当前版本注册。
**Consumes:** Task1字段/身份/DDL合同，Task2新写入事实，Task3/4所有caller。
**Produces:** 以下接口位于新release模块；以现有配对release框架调用，不另写部署器：

```python
prepare_action_entity_release(database: Path) -> dict
publish_action_entity_release(database: Path, contract: Mapping[str, object]) -> None
invert_action_entity_release(database: Path, contract: Mapping[str, object]) -> None
```

- [ ] **Step 1: 写** `test_forward_preserves_business_facts_and_receipts`, `test_conflicting_identity_aborts_without_mutation`, `test_new_action_and_recall_survive_inverse`, `test_inverse_does_not_restore_source_snapshot`, `test_reforward_has_no_duplicate_effect`, `test_unsupported_source_rejected_before_write`。新写入包括原生任务产出Action、替身provider结果、共享观察者和撤回状态。
- [ ] **Step 2: 运行** `python -m pytest -q tests/test_action_entity_schema_release.py`，预期release模块不存在而失败。
- [ ] **Step 3: 实现三个接口及完整发布注册。** 捕获源schema/marker/关系，不保存旧行正文；单事务迁移，全部引用统一物理Action身份。只按Task1可证关系合并；冲突不修数据凑一致。以当前统一事实恢复旧结构，包含正迁后新增数据；逆迁需要的辅助元数据只包含schema和身份映射，不在正常读写路径解释旧类型。原独有事实不可逆删除需另列具体字段/数量供确认，不能泛化已批准九字段中性默认值的例外。
- [ ] **Step 4: 运行** 上述测试及 `tests/test_runtime_current_schema.py`；预期全部通过。对真实副本跑forward→新写入→inverse→实际旧代码冷启动→reforward；比较业务事实/回执hash、身份基数、FK finding multiset、所有受影响索引。旧JSON framing/旧内部整数无需成为永久runtime合同，但业务意义及真实外部身份必须保留。
- [ ] **Step 5: 提交** release、版本注册、测试和两份机制文档；提交 `feat(storage): migrate unified action entities with current-data inverse`。

## Task 6: 完整副本、原生CLI与容量验收

**Files:** Create `tools/validate_action_entity_copy.py`, `tests/test_validate_action_entity_copy.py`, `docs/action-entity-unification-validation.md`。
**Consumes:** Task1字段合同、Task2-5冻结commit、真实线上SQLite backup、新旧代码根目录、原生模型配置、严格替身provider。
**Produces:** `validate_copy(source: Path, workspace: Path, old_root: Path, new_root: Path) -> dict`；CLI只在 `/tmp` 副本运行，输出脱敏计数/hash和可复核断言，不复制trajectory/原文到报告。

- [ ] **Step 1: 写** `test_validation_never_opens_production_for_write`, `test_live_provider_is_rejected_by_harness`, `test_new_facts_checked_after_inverse`, `test_capacity_uses_same_population_and_packing`；断言读取生产只用只读/SQLite backup，provider网络接口只接替身；容量前后同人口/相同VACUUM条件。
- [ ] **Step 2: 运行** `python -m pytest -q tests/test_validate_action_entity_copy.py`，预期验证工具缺失失败。
- [ ] **Step 3: 实现验证器。** 测试Harness控制provider替身，不新增生产安全工具。副本实际创建新任务，reviewed路径经真实CLI Consumer→Audit→既有选择→System Executor→替身操作→业务结果/回执读取；邮件分类与prepared路径按现有真实入口运行，不为测试制造它们原本没有的Consumer/Audit或模型调用。三种来源都验证新动作及回执读取。用确定性测试覆盖并发/未知结果/拒绝后修订/失败恢复，原生CLI另实跑修订和恢复路径，证据分别报告。模型取当时可用、经用户认可的生产模型；不因不可用擅自换昂贵Astra。可用性失败是未验收，不是业务失败。
- [ ] **Step 4: 执行** `python tools/validate_action_entity_copy.py --source PATH --workspace TMP --old-root OLD --new-root NEW`（参数须由工具argparse提供）；预期全部断言通过，替身发送次数符合各场景预期，真实provider调用为0。新事实inverse/旧代码冷启动/reforward同样通过；报告退休类型/字段/writer/index数量、四表页和索引、新增依赖成本、所需native来源实际命中率。收益为负要明确解释，不称为“数据库变小”。
- [ ] **Step 5: 自审并提交** 验证器、测试、精简验收文档；提交 `test(actions): verify copy lifecycle rollback and storage costs`。只保留测试代码/摘要；临时DB清理遵守授权，不绕过平台拒绝。

## Task 7: 集成、发布与生产只读回验

**Files:** Modify `docs/action-entity-unification-validation.md`, `docs/business-model-execution-progress.md`, `docs/architecture.md`, `docs/runtime-mechanism.md`, `CHANGELOG.md`；只在实际缺陷被复现后修改 `app/deploy.py` 或备份实现。
**Consumes:** 完整分支独立规格/质量审查、Task6副本/回退/原生验收、最新origin/main和生产读回。
**Produces:** 已推送完整commit，通过标准deploy上线，当前任务/回执不回放；实际退出清单和生产证据。

- [ ] **Step 1: 写发布验收用例。** 覆盖新备份失败保留旧备份、新备份完整后才清理旧备份、升级失败按显式schema inverse恢复并保留新事实；复用现有deploy/backup测试，不更改既有失败恢复策略。
- [ ] **Step 2: 运行对应测试。** 先定位 `rg --files tests | rg 'deploy|backup'`，只执行覆盖上述行为的现有文件；出现回归先复现并修复，不以泛化try/except或兼容分支掩盖。
- [ ] **Step 3: 集成最新origin并重新冻结。** 核对生产schema/HEAD，源变化则重取副本并重跑受影响正逆迁与native验收；不复制主目录未提交修改、不rebase/reset他人工作。更新当前实现文档的确切退出范围；范围外方案/审查/退订等明确仍未完成。
- [ ] **Step 4: 推送完整commit后执行** `python -m app.deploy`。预期既有静默/备份/FF/启动/health流程成功；备份验证先于旧备份清理。不得手工kill/kickstart生产，不恢复旧数据副本冒充schema回退。
- [ ] **Step 5: 只读读回并提交验收记录。** 核对生产commit/PID/healthz、queues、Attention、History及目标详情/真实历史回执；比较部署前后失败/卡住项和既有FK差分。新写入字段/旧目标类型实际退休、系统无测试fixture、无真实测试发送；未观察到的业务分支不标通过。提交 `docs(actions): record unified entity production acceptance`。

## 计划自审与执行交接

- 规格覆盖：身份/唯一归属→Task1/2；执行恢复→Task3；UI→Task4；迁移回退→Task5；副本/native/容量→Task6；备份/发布→Task7。
- 技术名称：Task2四接口由Task3/4/5使用；Task5三个release接口由Task6/7通过既有发布框架使用。新增函数是计划接口，不宣称现有代码已提供。
- 细节冻结：Task1是必须审查的DDL/字段决定点；本计划不假装现在已经取得所有字段/基数证据。未取得证据的字段不删。Task1通过后须继续实施Task2-7。
- 范围：只统一已有外部动作。Task/Run全面统一、方案审查的最终归属、退订专有步骤及业务/错误类退出属于总路线后续独立计划；本单元通过不代表59种Action已经全部消失。
- 本轮只产生计划，未运行本文未来测试、未实现接口、未变更schema或生产。计划审阅通过后沿用Subagent-Driven；不再次要求选择执行方式。
