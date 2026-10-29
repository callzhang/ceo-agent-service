# 全库字段、索引与旧结构链路审计：2026-10-27

本轮结论是：**还能继续简化，优先消除会话和反馈的重复 owner；其次删除无调用代码、不可达旧分支及结构上重复的索引。不能把历史事实、已采用结果或身份溯源当作兼容代码一起删。** 本文是审计和实施范围，尚未实施任何删除。

## 1. 证据基线与覆盖边界

- 初始生产安装提交：`d1efdbc28ddbc9ea847036c9f8ba3a597b354e88`。
- 本工作树：`c866d4fbd051044f1ba86e846ab14b70706ba741`；与上述生产提交的运行时代码相同，差别是已完成的收尾文档。
- 检查时 `origin/main`：`0ef0a929565248a51ea5a9e8fd43d1abfe575d37`。较生产新增的运行时改动在 Agent CLI、Consumer/Audit、OA 和提示词等调用方；本文所依据的 Store、实体存储、声明、历史转换器和索引定义没有变化。实施前须再次检查主线增量。
- 收尾复核（`2026-10-27T23:22Z`）：生产及 origin/main 均已更新至 `479f4da417c1d9938d886b7d1da09df540b255bb`。重新检查相对初始基线的全部运行时文件增量，仅为上述语音/提示词/OA 修复，相关存储文件未变；生产 schema 对象 SQL 数组与本轮快照逐项完全相等，FK 集合仍为空。因此本报告结论适用于该已读回版本；下面容量/行数明确仍按冻结快照计量，不冒充后续实时值。
- 生产只读元数据快照：`2026-10-27T23:03:20.565509Z`，数据库为 `~/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`。使用只读连接、query_only、超时和有界扫描；没有备份数据库，没有输出或保存业务原文、邮件正文、凭据。
- 对象：48 张物理表、249 个显式索引、179 个触发器，共 476 个非内部 schema 对象。PRAGMA index_list 另包含 29 个自动索引，共 278 个索引声明；WITHOUT ROWID 主键索引不另算一份表占用。
- 全字段清单：1,246 个列声明，其中 537 个普通列、709 个 VIRTUAL 列，没有 STORED 生成列。本轮自动核对覆盖全部列、索引、外键和对象声明；业务语义检查重点覆盖实际重复 owner、无调用入口及历史分派，**不是宣称每个保留字段、每条查询或每个索引都已证明最优**。
- 页大小 4,096 字节；125,725 页，文件页数口径 491.11 MiB；dbstat 已分配 454.80 MiB，freelist 36.31 MiB。空闲页不是本轮模型清理收益，也没有执行 VACUUM。
- 既有 FK 检查集合：空。保留该集合，不造占位记录、不静默修复数据。
- schema 对象数组 SHA-256：`8d87a56c15429d971564a59de6b752d655500a46b407b0fef435180a1a0c882f`；口径为排序后的 `(type,name,tbl_name,sql)` 数组，经 `json.dumps(ensure_ascii=False,separators=(',',':'))` 编码。这是本轮审计指纹，不混用发布器的其他 hash 编码。

仓内直接引用、字符串注册、工具脚本及动态导入均纳入检索。Graphify 对中央存储及 dispatcher/web_api/agent_cron 的 34 个文件进行确定性 AST 提取，得到 2,267 个节点、8,342 条边；图只用于定位，结论逐项返回源码核实。三个有界只读子审计分别检查 Task/Run、Action/Business、Email/State/Configs。没有运行测试、修改运行时代码、DDL、数据库、发送、部署或重放业务。

配套清单：

- [48 表占用、作用与处置](database-audit-tables-20261027.csv)
- [1,246 个字段的普通/虚拟属性与已确认处置](database-audit-fields-20261027.csv)
- [278 个显式/自动索引与删除依据](database-audit-indexes-20261027.csv)
- [下一步实施计划](superpowers/plans/2026-10-27-database-model-retirement-next.md)

## 2. 真正重复的模型与字段：精确退休范围

以下合计 **24 个 schema 列名**：13 个普通列、11 个 VIRTUAL 列。还包括 State 两个实际 payload 键和 Configs 一个空的记录家族。这里只列已证明的删除目标；不是直接在生产 DROP COLUMN。

### 2.1 会话：只保留 conversation_runtime_sessions

当前 `state` 同时有：

| 退休来源 | 当前唯一 owner | 处理 |
|---|---|---|
| conversations.codex_session_id | conversation_runtime_sessions.session_id，沿现有 conversation_id + route_name 身份 | 切换读写后删除旧 payload 键及同名 VIRTUAL 列 |
| conversations.codex_session_contract_hash | conversation_runtime_sessions.contract_hash | 同上 |

`app/store.py:_upsert_conversation_runtime_session_in_connection` 写当前 owner 后，仅对 codex_oauth 再双写旧字段；Consumer 已读取当前 owner。生产 602 个 conversation，364 个旧 session 非空，**364 个均存在相同 OAuth session**；338 个非空旧 hash 与当前 hash 相等，另外 26 个只是旧 hash 为空、当前 hash 非空，没有 26 个冲突 session 或丢失关联。conversation 没有可比较的新旧 hash 时间，不能编造“最新时间胜出”规则。

仍需切换的真实调用方：`audit_web.py` Attempt 详情 fallback、`Store.list_codex_conversations`、会话 DTO 组装、`/api/console/codex/sessions`、HTML session 列表、CLI `reset_codex_sessions`。特别是 reset 目前只清旧副本，不能认为它已经清除了 Consumer 实际恢复使用的当前 owner。目标按原 OAuth 会话重置用途操作当前 owner，不扩张到其他 route。

删除旧 get/set contract hash、clear_codex_session/clear_codex_session_if_matches 等无调用方法及双写分支；保留标题、single_chat、当前 route/session/hash/updated_at。列表和详情直接从当前 owner 组装，不新增“若没有当前则读旧字段”的 fallback。Run/ReplyAttempt 本身的 session 事实不是本节的重复字段，不能全仓删除所有 codex_session_id 名称。

### 2.2 反馈：Item 负责关联与当前指针，Round 负责轮次证据，源事件负责当前解决时间

`feedback_processing_items` 拟删除下面 10 个普通列：

```text
workbench_task_id, workbench_turn_id, attempt_id, agent_run_id, commit_sha,
test_evidence_json, restart_evidence_json, health_evidence_json, note, resolved_at
```

前 9 个从 **current_round_id 指向的准确轮次**读取；resolved_at 从 feedback_key 对应的现有源 `actions[feedback_events].fb_event_resolved_at` 读取。保留 Item 的 task_id、feedback_key、batch_id、current_round_id，Task 生命周期和历史 Round 不变。

核对了全部 54 个 Item：2 个有当前轮次，前 9 个镜像逐值相等；52 个没有当前轮次，这 9 个字段全部为现有中性值。**但其中 46 个 resolved_at 非空，不能只 join Round。** 54 个 Item 都有准确源事件，resolved_at 全部与源事件相等，包括这 46 个。生产有 3 个历史 Round，其中 1 个已经 reopen。

源码写入边界：`Store` 的 binding、evidence patch、resolve 同时写两份；reopen 清 Item 指针和中性镜像、清源事件解决时间，但保留旧轮次及回执。因此：

- 不能用“最新历史 Round”替代 current_round_id，否则 reopen 后会复活旧证据和已解决状态。
- 无当前轮次时，按当前数据结构的既有类型返回中性值；这不是旧结构 fallback。
- 不删除 Round 的 backlog_evidence_json、scope_receipt_json、receipt_version，不弱化 receipt/CAS/解决资格检查，不改变审查、授权或重试政策。

### 2.3 邮件模型切换：退休空的 Configs 并行历史

当前 `switch_online_model` 在模型 registry manifest 中原子记录 mode 与 mode_transitions；API 用它写入，`online_control_history(service.registry)` 用它读取。Configs 的 `email_model_mode_transitions` 是另一套测试/启动验证维持的模型，生产 **0 行**。保留当前 registry 历史、4 条 promotion config 和其他模型控制能力。

精确 schema 退休范围：

- 普通列：`transition_promotion_config_version`、`transition_promotion_ref`。
- VIRTUAL 列：`transition_promotion_kind`、`transition_actor`、`transition_from_mode`、`transition_to_mode`、`transition_from_model_id`、`transition_target_model_id`、`transition_status`、`transition_reason`、`transition_created_at`。
- 触发器：`trg_configs_transition_duplicate`、`trg_configs_transition_update`、`trg_configs_transition_delete`。
- 一个复合自外键：`(transition_promotion_ref,transition_promotion_kind,transition_promotion_config_version) REFERENCES configs(id,record_type,public_key)`，以及该家族的类型枚举、payload 规则、列必填/NULL 约束。
- 专属索引：**0 个**；不能删除共用的 id/record_type/legacy_key/payload_json/public_key/source_key 或索引。

代码退休：`EmailStore.record_model_mode_transition`、`get_model_mode_transition`、`list_model_mode_transitions`、专用 `_validate_model_mode_transition`、启动验证分支、`configs_storage._stored` 家族分支。保留仍供 promotion config 和 category description revision 使用的 `_next_model_control_timestamp`。迁移前再次断言该家族为空；如已出现行则停止并重新核对 owner，不能静默删除。

### 2.4 空的迁移元数据列

`run_entity_identity_map.allocator_highwater`：172,078 行全部为 NULL，唯一历史写入传 None，没有读取方。可删除这一个普通列，保留 map 其他全部字段、行、主键与引用。inverse 恢复该列为 NULL；不声称因此可回收整个 map。实际序列水位仍保留在现有序列/迁移交接 owner。

## 3. 无 DDL 的精确代码清单

“无调用”是仓内 app/scripts/tools/注册和动态字符串检查的结论，不能保证仓外手写脚本不存在。仅测试调用的方法先将仍覆盖当前行为的测试切换到当前入口；只测退休结构的专用断言与 gate 条目一起退休。不能删掉关键断言换取绿色。

### 3.1 可直接删除或去掉无效参数

| 文件/符号 | 删除依据与保留边界 |
|---|---|
| action_storage.preserve_candidate_result_references | 无调用、空操作 |
| action_entity_definitions.ACTION_LAYOUT | 无读取；当前 Action 用对象编码，不能改成另一编码 |
| shared_action_lifecycle.transition 的 expected、receipt 参数及无用赋值 | 当前 25 个 Email 调用均不用，函数内部也不消费 |
| shared_action_lifecycle._allocate 中 email_action_attempts 分支 | 当前允许写入的 kind 不含它；保留其他 allocator 溢出语义 |
| logical_run_storage.finish_logical_run / recover_logical_runs | 无当前调用 |
| run_entity_storage.get_run_failures | 无调用；保留失败事实、写入器和详情读取 |
| Store.latest_runtime_route_for_agent_run | 无调用；旧 staticmethod self 签名也无有效调用意义 |
| EmailStore._get_legacy_training_snapshot | 无调用且引用退休表；当前外部 snapshot/manifest/provenance 不动 |
| email_current_schema.create_current_email_runtime_schema | 无调用旧建表入口；不能删除仍供显式历史工具使用的整个模块 |
| WorkbenchStore.execution_run_id_for_executor | 无调用；保留实际执行身份列 |
| email_canonical_storage.ignore_email_message 与独占 mode=ignore 分支 | 无调用；保留实际 upsert 与分类结果写入 |
| EmailStore._legacy_unsubscribe_private_values / _redact_legacy_unsubscribe_json | 无外部调用；清独占 URL import。保留 email_task_adapter 当前有效的输入脱敏与 opaque 引用 |

### 3.2 先迁移测试，再从应用删除

| 应用入口 | 当前测试改用的 owner/路径 |
|---|---|
| task_entity_storage.bind_claimed_input | Store 当前真实 claim 路径；保留 current/claimed 区分 |
| run_entity_storage.get_run_projection | 当前 Run 读取或测试目录内窄查询；不删除 run_projections |
| scheduled_config_storage.migrate_scheduled_configs | 退休旧 runs.record_type 转换测试；保留 intern_scheduled_config |
| runs11_storage.Run11WriteResult / insert_run11_fact，整个旧写入模块 | 当前搜索索引/性能样本/setup owner；退休 test_runs11_storage 及 gate 对应行 |
| action_business_storage.delete_business_action | test_replay_task_attention_project 的显式测试夹具 |
| business_storage.create_business_schema | 当前原生 constructor；确需冻结来源的历史测试用独立夹具 |
| Store.backfill_scheduled_task_runtime_capabilities | 当前能力声明/创建路径；去旧补建测试 |
| Store.claim_scheduled_task_run | ScheduledTaskQueueAdapter.claim 的当前统一领取路径 |
| Store.mark_agent_runtime_attempt_running | 当前 mark_agent_runtime_attempt_running_once；保留幂等与租约断言 |
| Store.recover_unstarted_runtime_operation_attempts | 当前实际恢复入口；退休无调用包装 |
| Store.note_runtime_attempt_effect_started | 当前 running_once/恢复路径；first_effect_started_at 本身仍保留 |
| Store.schedule_meeting_alignment_job_retry | 当前 Meeting 任务生命周期；删除测试专用包装 |
| Store.claim_weekly_okr_report_run / renew_weekly_okr_report_run / release_weekly_okr_report_run，独占 parser 和 WEEKLY_OKR_REPORT_RUN_STATE_KEY | 当前 Task/Run 领取恢复；不顺带删除 service_state 历史事实 |
| provider_state_storage.get_observation / get_folder_generation / insert_observation / insert_folder_generation / delete_observation / delete_folder_generation | 当前批量 upsert/查询；独占 _row_dict 可删，binder 仍使用 |
| EmailStore.record_classifier_runtime_fallback | 当前 record_classifier_runtime_sample timing sink |

进一步可同类清理的测试专用 DAO：EmailStore 的 append_action_attempt、backfill_email_message_recipients、backfill_email_unsubscribe_entry_url、set_folder_binding_status、list_model_promotion_configs；WorkbenchStore 的 list_turns/list_artifacts（正式 API 用 timeline snapshot）。它们不是删业务表的依据。Store 的 list_reply_task_inputs/get_agent_run_for_turn/list_memory_write_events_for_attempt 也仅为观察型测试 helper，优先级低于真正的 owner/旧写入清理。

### 3.3 当前执行身份 API 不再接受旧列名

`shared_action_lifecycle` 的 `_reviewed_attempt`、`_reviewed_action_receipt`、`begin_reviewed_action`、`record_reviewed_action_outcome` 只允许 execution_run_id，却仍传 owner_column；provider receipt 的 `_bind_provider_receipt_metadata`、`advance_canonical_provider_reference`、`record_reviewed_provider_receipt` 同样传 execution_owner_column。

直接固定现行列并删参数。`action_entity_storage.update_sent_receipt` 默认值仍是旧 execution_id，`update_action_observation` 仍接受两种列名，实际应用调用都传新列：去掉旧默认/分派。`append_action_receipt` 还被显式旧转换工具调用，先将工具的冻结写入放回显式工具边界，再删除当前 helper 的旧参数。不要为了工具在当前运行时留下旧列名选择。

旧 `CANDIDATE_ACTION_ATTEMPT_SQL` 引用不存在的 action_receipts.execution_id，仅 test_shared_action_release 使用；迁入冻结来源夹具或退休该专用断言。保留实际使用的 `NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL` 和执行身份/回执语义。

### 3.4 发布选择器的 7 个不可达 fallback

`current_publication_sources._version` 只返回当前版本或明确支持的上一版，否则抛错。因此 publication_run_sources、publication_task_sources、publication_deployment_task_sources、publication_meeting_sources、publication_scheduled_source、publication_candidate_source、publication_receipt_sources 的其他旧表 else/fallthrough 永不可达。删旧分支与独占 import，**保留这 7 个仍有真实发布调用方的函数和严格版本校验**。

当前支持的显式 predecessor 发布/回退窗口不属于任务运行时兼容；不能借此绕过发布门槛或让当前 constructor 接受旧结构。

## 4. 可以退出现行代码的历史转换包

下列是源码/工具/夹具退休，不代表删除历史行，也不要求保留运行时兼容：

| 历史包 | 退休范围 | 不能一起删 |
|---|---|---|
| task_entity_schema、run_entity_schema | capture/migrate/restore 与独占旧身份恢复 helper | 当前 task_run_entity_definitions、run_entity_definitions、assembly |
| run_adoption_schema、run_adoption_inverse、task_entity_inverse_files | 老版本 adoption 和 Workbench 文件转换工具链 | 当前采用结果、审查、选择、execution_progress、文件生命周期 |
| business_legacy_owner_storage | capture_legacy_owner_source/migrate_legacy_owner/restore_legacy_owner 与无调用 inline_legacy_owner 分支 | CLI 仍用 read_import_task/assign_import_task |
| business_candidate_storage | capture/install/migrate/restore 转换半部 | Store 实际 import 的 insert/update 候选写入半部 |
| email_canonical_storage | 无外部调用的 capture_email_canonical_source/migrate_email_canonical_schema/restore_email_canonical_schema 闭包 | 当前 _create_emails、writer、projection、实际 assembly 和必要 predecessor helper |
| workbench_storage | 老 capture/migrate/restore/schema_is_current 与冻结转换 DDL | 真正 Workbench store；先迁移其他显式历史模块仍 import 的 RETIRED_WORKBENCH_TABLES / SEARCH_FTS_DDL 常量 |

关联 roots 要一起处理：scripts/validate_task_2d7i_workbench_inverse.py、scripts/validate_task_2d7j_workbench_artifact_routes.py、tools/validate_run_adoption_cold.py 的动态 __import__，以及 tools/validate_email_orphan_old_dao.py 对 tests/test_run_entity_schema 的反向依赖。测试仍验证当前功能的改用 native creator；只针对已经退役版本转换的测试/脚本一起退休。必要的当前发布和当前数据 inverse 工具仍保持显式、与 runtime 隔离。

另有较低优先级的测试注入旧执行器：CodexDecisionRunner 的 executor/_legacy_adapter/_build_command/_subprocess_executor 分支，生产构造使用 store 和 routed execution，旧分支由测试维持。先将测试注入当前 RoutedCodexExecution 替身，并核对 e2e smoke 构造，再退休这组分支；不删除生产 runner 或改变 route 策略。

## 5. 准确索引删除清单

依据为同键序、排序/排序规则、过滤条件、现有 UNIQUE/主键及完整 FK 关系；不是根据索引名字或表行数猜测。仓内未发现这 10 个名字的 INDEXED BY 强制调用。对主键倒序读取另做 EXPLAIN，沿 rowid 反向扫描不需要临时排序。

| 删除索引 | 保留的约束/索引 | 当前字节 |
|---|---|---:|
| idx_configs_binding_config_order | 同条件同键的 UNIQUE idx_configs_binding_order | 73,728 |
| idx_business_object_tasks_business_key_identity | task_business_keys 的 business_key UNIQUE 自动索引 | 1,048,576 |
| idx_feedback_processing_items_feedback_key_identity | feedback_key UNIQUE 自动索引 | 4,096 |
| idx_feedback_processing_rounds_feedback | feedback_key,round_number 的唯一身份索引 | 4,096 |
| idx_run_adopted_reviews_consumer | UNIQUE(consumer_run_id,audit_run_id) 自动索引 | 32,768 |
| idx_workbench_artifacts_id_identity | id INTEGER PRIMARY KEY | 4,096 |
| idx_workbench_attachments_id_identity | id INTEGER PRIMARY KEY | 4,096 |
| idx_codex_session_id | codex_session_search_index.id INTEGER PRIMARY KEY，可反扫 | 16,384 |
| idx_email_samples_id | email_classifier_runtime_samples.id INTEGER PRIMARY KEY，可反扫 | 40,960 |
| idx_setup_events_id | setup_wizard_events.id INTEGER PRIMARY KEY，可反扫 | 4,096 |
| **合计** | **301 个 4 KiB 页，约 1.18 MiB** | **1,232,896** |

DROP 后是数据库内可复用空间，不承诺文件立即缩小。索引正向/逆向 SQL 见实施计划；不能在当前 schema 版本直接删。

必须保留：task_inputs(task_id,id)、run_adopted_results(consumer_run_id,candidate_digest)、run_adopted_reviews(consumer_run_id,audit_run_id)、run_adopted_selections(consumer_run_id,audit_run_id) 和 (id,consumer_run_id,audit_run_id) 的精确 UNIQUE 父键，即使其一列已经唯一，SQLite 复合 FK 仍需要。保留 schedule 的全状态 claim 索引；dispatch 已是局部 pending，不能把两者一起缩成 pending。其他索引没有足够查询与负载证据，不列入删除。

## 6. 已核对、现在不能当重复内容删除的对象

| 对象 | 当前证据与结论 |
|---|---|
| Actions 的 425 列 | 17 普通 + 408 VIRTUAL，不是每行存 425 份字段；单删生成列主要减 schema/代码复杂度，须先证明无索引/FK/读取方 |
| Business 的 130 列 | 16 普通 + 114 VIRTUAL；共同 title/status/created_at 没在 payload 再存一份 |
| 旧 Project/Todo/Update 历史 | 保留 19+16+7 个真实字段，历史详情、跟进/待办回执、CLI 语义导入有读取；它们不是可执行 Task 的兼容副本，不能按标题/整数碰撞合并 |
| Signal / provenance | 1,154 个 Signal 都引用 291 个 provenance，Signal 仅 5 个自身槽位；来源 owner 已统一 |
| Attention proposal/current members | 当前各 41 行，但前者是原始期望集合、后者是资格过滤结果；重算要前者，不能因当前相等删掉 |
| Action provider body | 同一 Action 内精确重复正文 0 组；不能宣布整张 receipt 可替代为 trajectory |
| 四张 identity_map | 合计 31,145,984 字节，约 29.7 MiB，包括索引；包含唯一 typed 公共身份、旧顺序、复合键关联，不能把“没运行时读取”理解为“无溯源价值” |
| Task map | 19,744 行；18,267 个旧整数公共 key 不同于 native ID，1,477 个非整数 key；已有转换刻意不保存可直接重建的相等身份 |
| Run map | 172,078 行；132,549 个旧 key/order 不同于 native ID；run_provenance.job_id 不是旧 Run 身份。空跑 coalescer 还会维护 map，不能只 DROP 表 |
| Adoption / Action map | 3,405 / 31,822 行；旧采用身份与 typed/composite Action 源身份并非当前 Run/Action ID 可完整反推 |
| Task.current_input_id / claimed_input_id，两个 version | 最新输入和已领取输入不同；不可合并 |
| Run / RuntimeAttempt | 一次逻辑修订与一次 CLI 调用、retry/resume 是不同实体；session/status 同名不代表重复 |
| run_adopted_results / reviews / task_results / receipts | 已采用业务/审查结果与外部执行事实不等同原始 trajectory；保留 |
| emails 的两种 RFC/thread 身份 | 11,192 行中 RFC 有 22 处不同、thread 有 10 处不同；不能全当同值别名删掉 |
| 邮件 preview、provider_state、性能样本 | preview 已限制 1,000 字符；provider observation/目录代次是当前事实；2,048 条无正文性能样本有实际百分位 API，均不是配置副本 |
| 924 个 email_historical_candidates | normalized_text 已是 hash，未重复存 5 个原定位字段；不是完整邮件正文副本 |
| 空的 workbench attachments/artifacts、feedback decisions、adopted selections | 有当前注册写入、读取或 FK 合同，0 行不等于功能退休 |

email_classification_inputs 还有可后续核对的小冗余：1,427 个 payload 都重复 scalar stable_message_identity、固定 channel=email/compacted=true。但尚未冻结所有 provenance/hash/压缩消费合同，本轮不把整个 payload 或这些字段加入可执行删除清单。

## 7. 需要单独确认范围的真实旧行为

Email worker 启动仍执行 skip_failed_legacy_email_unsubscribe_tasks、_fail_nonterminal_legacy_unsubscribe_tasks 和旧 authority inventory gate。生产当前只有 audited_v2 的 failed=3、skipped=1，未见 direct_v1；当前 producer 已只写新模型。**这些不是不可达代码**：它们影响启动资格、失败/跳过状态和恢复，应单独提出删除旧启动扫描的变更范围，先确认不改变当前 audited_v2 的行为，再实施。不能在“无调用清理”提交中顺带改状态或审查/授权政策。

## 8. 下一步与收益口径

1. 第一批无 DDL：删除 §3 的无调用/不可达分支；测试改到当前 API；执行身份 helper 固定现行 owner；schema 476 对象、SQL 指纹和 FK 集合必须不变。
2. 第二批显式 schema：先独立移除 10 个重复索引；再移除空的 Configs 转换家族和 NULL 元数据列。每批目标声明正面固定、提供精确 inverse，在当前数据副本创建新数据后演练 inverse/旧代码读写/reforward。
3. 第三批业务 owner：会话旧两字段退休；反馈 Item 十镜像退休。前者检验 session/reset/原生详情，后者检验 46 个无 Round 解决事实、准确 current pointer、reopen 和 immutable receipts。
4. 第四批旧工具闭包退休：迁移当前功能测试，删已关闭转换包和专用脚本；保留必要的现行显式迁移/inverse，保留唯一历史身份事实。
5. 单独处理 §7 的启动/恢复旧行为；不夹带入以上无行为变更单元。

明确容量机会只有 10 个索引当前占用约 1.18 MiB；24 个列/一个空家族的净页收益须同群体实测，不按 VIRTUAL 列数或整表大小推算。优先价值是消除两个重复 owner、死路径及旧列名选择，**不宣称数据库最优、全仓零兼容或大幅缩小**。

本轮产物仅为本文、CSV、实施计划和 claim。生产数据和运行时均未更改；审计临时元数据/图和子报告在汇总核对后清理，不保留数据库副本或备份。

收尾检查已通过：CSV 行数分别为 48/1,246/278；退休列为 13 普通+11 VIRTUAL，退休索引为 10 个/1,232,896 字节；文档内本地链接全部可解析，git diff --check 通过。未运行 pytest，因为本轮没有实现变更。已核实临时目录无符号链接、无打开文件，并清除本轮 `/private/tmp/ceo-schema-audit-z_cy65fm` 的 44 个文件（14,149,746 字节）；确认路径不存在。生产库、备份和原生 trajectory 均未触动。
