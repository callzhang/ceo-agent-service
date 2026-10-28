# Database Model Retirement Next Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除当前会话/反馈的重复 owner、无调用入口、不可达旧分派和已证明重复的索引；不保留运行时旧结构兼容，不丢来源身份、业务事实、采用结果或回执。

**Architecture:** 现行正面声明是唯一运行时结构。业务读取/写入直接使用现有 owner。历史源造数进入测试夹具；必要的前一发布版本转换与当前数据 inverse 是显式工具，不参与 constructor、任务分派或 fallback。无 DDL 清理、索引迁移、空家族退休、会话归一、反馈归一及历史工具闭包退休分提交验收。

**Tech Stack:** Python、SQLite JSONB/生成列/复合 FK、pytest/Ruff、现有 Store/EmailStore、现有标准 app.deploy 和 console 前端。

**Status:** Derek 已于 2026-10-28 确认实施。第一批无 DDL 已作为 c5f39aa8e 标准部署，相关测试、SPEC/QUALITY、生产读回和明确归属清理通过。第二批44c13bc91已标准部署及读回/清理完成，会话/反馈/剩余历史闭包继续；第二批冻结依赖见 docs/model-retirement-batch2-contract.md。会话 inverse 明确采用权威记录重建完整副本，不额外保存旧镜像空值。

**Audit baseline:** production `d1efdbc28ddbc9ea847036c9f8ba3a597b354e88`，本地 `c866d4fbd051044f1ba86e846ab14b70706ba741`，检查时 main `0ef0a929565248a51ea5a9e8fd43d1abfe575d37`；48 表/476 对象。精确字段、调用方与索引证据见 [审计](../../database-simplification-audit-20261027.md) 和三个 CSV。实施先刷新基线，不硬编码本次 PID 或把本次行数当作未来迁移前置常量。

**Final live recheck (23:22Z):** production/origin main 均为 `479f4da417c1d9938d886b7d1da09df540b255bb`，schema SQL 与冻结快照完全相等，FK 集合为空；全部存储相关源码无增量。新增语音/提示词/OA 修复已对照，不改本计划归属结论。

## 通用约束与执行顺序

- 使用隔离 worktree，先核对 docs/agent-claims.md；每个单元认领准确文件，app/store.py 始终只由一个实施者编辑。不能触碰另一发布任务认领的文件或并行部署。
- 每个单元进行 SPEC/QUALITY 审查。仅运行直接相关文件/节点，每个测试进程 2 GB、10 分钟，不提高预算、不在运行服务旁跑全量套件。
- 所有模型/provider 测试用替身；只读生产验收，不创建生产测试任务、不重发历史发送。
- 不新增业务字段、备用 JSON、旧表探测、自动补建、双写或旧读取 fallback。恢复/授权/审查/重试政策维持原义。
- 代码清理单元先完成；DDL 单元逐个冻结目标 SQL、逐字段映射、序列与 inverse 合同再实施。每个行为修改同提交更新 docs/architecture.md 和 docs/runtime-mechanism.md；进度与证据同步更新。
- 不为固定表数合并。48 表未必减少；本期价值是唯一 owner 和调用链简化。

## Task 1 — 无 DDL 清理，先证明当前调用合同

**Modify:** app/store.py、app/action_storage.py、app/action_entity_definitions.py、app/shared_action_lifecycle.py、app/action_entity_storage.py、app/action_business_storage.py、app/business_storage.py、app/task_entity_storage.py、app/run_entity_storage.py、app/logical_run_storage.py、app/scheduled_config_storage.py、app/current_publication_sources.py、app/email_store.py、app/email_current_schema.py、app/email_canonical_storage.py、app/provider_state_storage.py、app/workbench/store.py；仅实际涉及的测试/夹具、tests/gate_files.txt 和上述文档。

**Delete when callers migrated:** app/runs11_storage.py、tests/test_runs11_storage.py。其他文件按审计 §3 的符号粒度删除，不能整包误删。

- [x] 重新检索审计 §3 每个符号的直接调用、别名 import、字符串注册及脚本。若主线新增真实调用，切换到已存在的当前入口；不得用兼容包装保持旧名字。
- [x] 分三个小提交执行：纯无调用/无用参数；测试专用 DAO 与旧写入夹具；执行身份参数和发布选择器。每次只 stage 自己的 hunks。
- [x] 删除 preserve_candidate_result_references、ACTION_LAYOUT、transition.expected/receipt、旧 email_action_attempts allocator 分支、finish_logical_run/recover_logical_runs/get_run_failures、latest_runtime_route_for_agent_run、旧训练读取、未用 Email builder、Workbench executor helper、ignore_email_message 及两项未用退订 redactor。清独占 import，不动有效脱敏。
- [x] 将 bind_claimed_input、get_run_projection、旧 schedule/attempt/weekly OKR 包装、Provider 六个单行 helper 及 business create/delete 测试切到实际 native owner/当前调用。保留 claim/current-vs-claimed、原生结果可用/不可用、receipt、去重等有效断言；退休 runs11 专用测试和 gate 行。
- [x] 删除 current_publication_sources 的七个不可达 else/fallthrough，保留当前/受支持 predecessor 的严格版本验证和真实静默等待调用。
- [x] Reviewed/provider receipt helper 固定 execution_run_id：删除 owner_column/execution_owner_column 参数和旧列名选择。先将 action_entity_transfer 等显式旧工具对 append_action_receipt 的依赖移到冻结工具 helper，再清当前入口。保留 CAS、lineage、ACK 后恢复合同，不加入新 policy。
- [x] `CANDIDATE_ACTION_ATTEMPT_SQL` 只供旧来源夹具则移到测试；当前始终保留 NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL。
- [x] 分进程运行受影响测试：tests/test_shared_action_lifecycle.py、test_provider_state_storage.py、test_task_entity_storage.py、test_run_entity_storage.py、test_logical_run_owner.py、test_scheduled_task_store.py、test_repository_updater_publication.py、test_configs_source_contract.py；涉及业务夹具再跑 test_business_storage.py、test_business_schema_release.py、test_replay_task_attention_project.py。不因列在计划中就把所有文件拼成一个大进程。
- [x] 符号复扫无调用/动态注册/兼容别名；Ruff/差异审查通过。constructor 与生产 schema 的完整 SQL/字段/索引/FK 指纹逐项不变；记录“代码简化，0 字节容量收益”。

## Task 2 — 独立退休 10 个结构重复索引

**Modify:** app/current_schema_declarations.py、app/current_schema_manifest.py、app/task_run_entity_definitions.py、app/run_adoption_definitions.py、app/configs_definitions.py 及实际生成候选索引的当前声明；app/store_schema_release.py 和本单元明确认领的显式 release/inverse；tests/test_current_declarations_independent.py、tests/test_store_schema_release.py 与一个新专用 release 测试文件。旧冻结来源 SQL 不随当前目标改写。

- [x] 在最新生产元数据上复核 CSV 中键序、collation、ASC/DESC、predicate、UNIQUE、FK 父键和所有 INDEXED BY。无法重现证明的索引移出本单元，不能靠猜测补齐数量。
- [x] 冻结正向退休 SQL，必须与声明/manifest 一同升版，不能保留旧 marker 后直接 DROP：

```sql
DROP INDEX idx_configs_binding_config_order;
DROP INDEX idx_business_object_tasks_business_key_identity;
DROP INDEX idx_feedback_processing_items_feedback_key_identity;
DROP INDEX idx_feedback_processing_rounds_feedback;
DROP INDEX idx_run_adopted_reviews_consumer;
DROP INDEX idx_workbench_artifacts_id_identity;
DROP INDEX idx_workbench_attachments_id_identity;
DROP INDEX idx_codex_session_id;
DROP INDEX idx_email_samples_id;
DROP INDEX idx_setup_events_id;
```

- [x] 冻结精确 inverse SQL（原始显式定义），不写 IF NOT EXISTS 掩盖声明不匹配：

```sql
CREATE INDEX idx_configs_binding_config_order on configs(skill_binding_config_id,skill_binding_load_order) where record_type='runtime_skill_bindings';
CREATE UNIQUE INDEX idx_business_object_tasks_business_key_identity ON task_business_keys(business_key);
CREATE UNIQUE INDEX idx_feedback_processing_items_feedback_key_identity ON feedback_processing_items(feedback_key);
CREATE INDEX idx_feedback_processing_rounds_feedback ON feedback_processing_rounds(feedback_key,round_number);
CREATE INDEX idx_run_adopted_reviews_consumer ON run_adopted_reviews(consumer_run_id,audit_run_id);
CREATE UNIQUE INDEX idx_workbench_artifacts_id_identity ON workbench_artifacts(id);
CREATE UNIQUE INDEX idx_workbench_attachments_id_identity ON workbench_attachments(id);
CREATE INDEX idx_codex_session_id ON codex_session_search_index(id DESC);
CREATE INDEX idx_email_samples_id ON email_classifier_runtime_samples(id DESC);
CREATE INDEX idx_setup_events_id ON setup_wizard_events(id DESC);
```

- [x] 保留对应 UNIQUE 自动索引和主键；保留精确复合 FK 父键、schedule 全状态 claim 和 pending dispatch 的不同合同。
- [x] 当前生产派生副本正向、写入新的相关父子事实、inverse、真实旧代码冷启动读写、reforward。比较值/SQLite 类型/身份/序列/FK 异常集合；EXPLAIN 验证相关 join/唯一查找/倒序分页无明显退化。不能只比对象数量。
- [x] 同 page size/同数据测量；本次 301 页/1,232,896 字节仅作为索引 B-tree 机会，记录迁移后实际结果和文件空闲页，不能宣称文件立刻减少 1.18 MiB。

## Task 3 — 退休空的 Configs 并行历史与 NULL 元数据列

**Modify:** app/current_schema_declarations.py、app/current_schema_metadata.py、app/current_schema_manifest.py、app/configs_definitions.py、app/configs_storage.py、app/email_store.py、app/run_entity_definitions.py；明确的 paired release/inverse 与专用测试。保留 registry 当前控制实现 app/email_classifier_runtime.py 的现有业务语义。

- [x] 复核 `email_model_mode_transitions` 当前生产行数为 0；`allocator_highwater IS NOT NULL` 行数为 0。未来如非零，阻止这一预定迁移并补证据，不自动清空或放进 JSON。
- [x] 删除 Configs 该家族的 2 普通列、9 VIRTUAL 列、3 个触发器、一个三列自 FK 和所有独占 family/check/存储规则，清理精确 old DAO/startup validator。保留共享键/index及 `_next_model_control_timestamp`。
- [x] 新目标用正面固定声明；对需要 rebuild 的 Configs 使用显式列映射，逐列保留其他所有家族、JSON 编码/SQLite 类型、旧主键和水位；触发器、FK、引用目标随精确新声明创建，不运行自动修补。
- [x] 删除 run_entity_identity_map.allocator_highwater 这一空列；保留其他 6 个列及全部 map 行。inverse 恢复 NULL；不能推断原 allocator 水位或删除其他 identity provenance。
- [x] 专用当前数据副本演练包含：新 promotion config、现有 registry 切换替身/历史读取、其他 Configs 家族新数据、inverse 旧代码读写、reforward。0 行旧 transition 家族 inverse 恢复为空，不把新 registry 历史复制回旧 DAO。
- [x] 相关测试：test_email_model_controls.py、test_configs_storage.py、test_configs_source_contract.py、test_current_declarations_independent.py、test_store_schema_release.py 及新 release 测试；验证共享约束未被删。保留当前 registry activation/history 与必要回执断言。

## Task 4 — Conversation session 归属到唯一现有 owner

**Modify:** app/state_definitions.py、app/store.py、app/audit_web.py、app/web_api/registration.py、app/cli.py、ConversationRecord 的真实声明/全部构造方、固定当前声明/元数据/manifest、显式 release/inverse 与 tests/test_codex_session_api.py、test_state_storage.py、test_consumer_session_prompt_fingerprint.py、test_consumer_skill_session_contract.py、test_audit_session_handoff.py。

- [ ] 冻结全部 conversations/session DTO 读写方，包括已有 session 列表 HTML/API、Attempt 详情、CLI reset。按 conversation_id+既有 route_name 精确关联，不能按标题或 session 字符串跨会话合并。
- [ ] 在最新副本分类核对：每个非空旧 session 都有同一 OAuth 关联；非空旧 hash 必须与 canonical 相等。任何非空冲突/缺失关联阻止迁移，补事实后重新冻结，不双向猜测覆盖。
- [ ] 当前集合为 364 个 session、338 个非空相同 hash、26 个仅旧 hash 为空。Derek 已确认会话 inverse 由 canonical 重建完整副本：这 26 个空镜像恢复为现有非空 canonical hash，不额外保存旧空值。当前 session/hash 事实始终逐值不变；验收明确区分 canonical 事实与退休副本。
- [ ] 写入只操作 conversation_runtime_sessions，清旧 codex_oauth mirror 分支。当前列表/DTO/详情直接从该 owner 读取；不要给旧 getter 留转发别名。
- [ ] CLI OAuth reset 改为操作同一现有 canonical OAuth owner，保留其他 route。测试确认 reset 后 Consumer 不会继续恢复被 reset 的旧 OAuth 会话。无当前会话时明确不可用，不从旧列 fallback。
- [ ] 删除旧 get/set hash、clear session helper 和两项 conversations payload 字段/同名 VIRTUAL 列；保留 title/single_chat/route timestamps 和 Run/Attempt 的独立 session 事实。
- [ ] 副本正向后测试新会话、hash 更新、reset、其他 route 不变、历史 Run 原生结果可用/不可用、API/HTML session 列表；inverse 旧代码冷启动读取及写入新的会话后 reforward。比较 canonical 事实、身份、类型、序列和 FK。

## Task 5 — Feedback Item 去掉 10 份镜像

**Modify:** app/store.py、app/feedback_processing.py、反馈当前 schema/固定声明/manifest、精确 release/inverse、tests/test_feedback_processing.py、tests/test_feedback_processing_e2e.py、tests/feedback_processing_fixtures.py、tests/test_source_feedback_identity.py。

- [ ] 冻结四层 owner：Task 生命周期；Item 的 task_id/feedback_key/batch_id/current_round_id；Round 的轮次关联与证据；源 feedback event 的当前 resolved_at。不造新 DTO/JSON owner。
- [ ] 逐行确认 9 个 Round 镜像与准确 current_round_id 的一致性；无指针时只能是既有中性值。单独确认 Item.resolved_at 与同一 feedback_key 的源事件逐值相等；缺失/冲突阻止迁移。不要用 latest Round。
- [ ] 重构 bind/patch/resolve/reopen 为单一写入 owner；保留已有事务、CAS、receipt 资格和历史不变性。读取从准确指针及源事件组装现有功能所需字段。
- [ ] 冻结 Item 目标列仅 task_id、feedback_key、batch_id、current_round_id，并保留已有 PK/UNIQUE/FK/合法指针约束；逐项核对被退休 10 列关联的触发器/检查/索引，不能只做文本 DROP。若主线新增实际业务列，先核对后更新合同。
- [ ] 重点回归：46 个无 Round 但已解决的来源事实不丢；无当前 Round 的 52 个镜像中性值不变；reopen 后旧 Round/receipt 留存但不会再次成为当前证据；新 claim/bind/patch/resolve 正常；source feedback 身份精准匹配。
- [ ] 副本 forward 后新增一轮、resolve、reopen、再 claim/新轮；inverse 从当前指针重建 9 项镜像、从源事件重建 resolved_at，再由旧代码冷启动完成一次替身轮次并 reforward。逐事实/类型/引用/水位比较，不能只检查“没有异常”。

## Task 6 — 移除已关闭历史转换包的闭包

**Modify/Delete:** 审计 §4 明确列出的 task_entity_schema/run_entity_schema/run_adoption_schema/run_adoption_inverse/task_entity_inverse_files、business_legacy_owner_storage 转换半部、business_candidate_storage 转换半部、email_canonical_storage 转换闭包、workbench_storage 老转换；调用它们的显式过期工具、测试夹具和 gate 清单。

- [ ] 在 Tasks 1–5 稳定后重新建立 import/动态字符串依赖闭包；先将当前业务测试改为 native creator，再退休只验证旧版本转换的测试。不能只删除核心模块，让 tests 被 tools 反向 import 后断裂。
- [ ] 明确处理 validate_run_adoption_cold 的动态 import、validate_email_orphan_old_dao 对测试的依赖、两个 Workbench inverse 验证脚本。旧工具如仍决定保留，隔离为冻结显式工具；不得为了它们保留当前业务 helper 的旧字段分支。
- [ ] 保留当前声明/assembly、Business insert/update 与语义导入 read_import_task/assign_import_task、实际 Workbench 文件生命周期和当前需要的 predecessor release/inverse。
- [ ] 保留四张 identity_map 的唯一身份事实和 coalescer 当前必要维护。删除旧转换器不代表允许删除 provenance。不得新建通用历史容器或将它们塞进备用 JSON。
- [ ] CodexDecisionRunner executor/_legacy_adapter 分支单独小提交：当前测试改为现有 RoutedCodexExecution 替身，检验 live smoke 构造合同，再删旧 direct executor；保留生产运行与 route 能力规则，不改 policy。
- [ ] Ruff、仓内符号/动态注册复扫和保留下来的直接相关测试通过；文档明确哪些历史工具已退休、哪些显式 inverse 仍必要。

## 不混入本计划的行为变更

Email worker 的旧退订 startup 扫描/失败标记/authority inventory gate 是真实可达状态逻辑。若要退休，单独提案并确认范围，再独立代码、测试、文档和提交；本计划的“无调用清理”不能改变这部分恢复/状态/授权规则。邮件分类输入 payload 的小冗余也须先补完全部消费合同，不预先删除。

## 各批发布与清理合同

- [ ] 每批实施完成后确认实际 origin/main 增量、认领范围、精确提交及审查；正常合并、受影响复验并推送。运行时代码按标准 `python -m app.deploy --target-commit <完整已验证SHA>` 发布，不并行抢发布锁，不自行加 skip-quiet-wait。
- [ ] 完整 gate 保持 10 分钟/2 GB；备份验证、安静窗口与失败回滚不绕过。涉及 DDL 的 forward/inverse/current-data/旧代码冷启动证据必须在部署前完成。
- [ ] 生产读回实际安装 SHA/PID/health/frontend stamp、队列/Attention/History 翻页筛选详情、Task/Run/原生结果/回执；会话批次额外读 session 页面，反馈批次额外读反馈列表/详情。只读，不触发真实外发。
- [ ] 对照该批确切 schema SQL/字段/索引/FK 集合和序列。无 DDL 批次必须逐字不变；DDL 批次必须只含预定差异。历史失败记录不强行归零。
- [ ] 验收后按 Derek 已确认规则，核实本批备份/临时文件代次、完整性、归属和打开引用，持锁并以精确 inode/mtime 护栏清除；保留生产数据库、原生 trajectory 和提交的简洁证据。平台拒绝则报告实际阻塞，不重复索要已给授权。
- [ ] 更新进度/验收记录，区分代码/部署/生产读回/清理；不为纯文档再次部署，不把临时文件清理或 freelist 计为业务模型容量收益。
