# architecture.md — 已退役／历史章节归档

从 `docs/architecture.md` 原样移出的章节，仅作溯源；当前合同以 `docs/architecture.md` 为准。

### 历史发布阶段记录（均为当时状态，当前状态见本文顶部最新合同）

Derek于2026-10-24批准分阶段退役历史迁移链：部署路径 `prepare_database_schema_release` 只处理当前 Store／Email 版本、其上一版本（Store `2026-10-19.12` 经 `action_entity` 与 `task_run_entity` 发布）以及仅 Email 变化的发布；面向更旧目标版本的分支和 `prepare_store_schema_release` 里的 Run／Task／Workbench／Action 旧转换已删除，遇到这些版本一律以 `Store schema release needs a verified inverse for this transition` 拒绝。部署进度 DAO（`ExistingSchemaUpgradeStateStore`）只接受 Email 账本版本 52／53／54，Email 51 及更早的账本报 `unsupported committed Email schema role`。生产已于 Store `2026-10-23.1`／Email54，不受影响；回滚到比上一版本更旧的提交不再有自动逆迁。

2026-10-25 第三阶段（Derek 批准）：上一版本发布 `app/task_run_entity_schema_release.py` 已退役，连同 `scripts/inverse_task_run_entity.py`、`tools/validate_task_run_entity_copy.py`、`tests/test_task_run_entity_schema_release.py`、`tests/test_current_publication_sources.py`（该发布期望 Email 53 而当前配对是 Email 54，已无法发布）。`prepare_database_schema_release` 现在只处理当前 Store／Email 版本和仅 Email 变化的发布；标记为 Store `2026-10-21.1` 的数据库一律以 `unsupported current Action source schema; explicit paired release required` 拒绝。生产 `auto-reply.sqlite3.task-run-inverse.json` 逆向交接文件原样保留，但已无命令可以消费它，回滚到 `2026-10-21.1` 不再有自动逆迁。`task_entity_schema`、`run_entity_schema`、`run_adoption_*` 的转换／逆向函数不再被任何应用代码调用，当时仅剩测试和 `scripts/validate_task_2d*` 引用；本待办已在2026-10-28第四批候选中完成闭包退休，当前范围及发布状态见本文顶部与第四批验收。

2026-10-24 第二阶段：27 个历史 `*_schema_release` 模块（`action_email`、`task20`、`runs11`、`confirmation`、`selection`、`result`、`email_metadata`、`execution_owner`、`sent_result`、`shared_action`、`specialized_action`、`prepared_action`、`logical_run`、`business_legacy_field`、`business`、`configs`、`state`、`provider_state`、`task`、`run`、`workbench`、`action`、`action_delivery`、`action_postfix`、`action_receipt`、`action_reply`、`task_lifecycle`）及其测试、`scripts/validate_*_copy.py` 与 `scripts/retire_verified_email_inputs.py` 已删除。现行存储仍用到的辅助函数迁入 `app/business_schema_helpers.py`、`app/configs_schema_current.py`、`app/provider_state_schema_current.py`、`app/result_schema_helpers.py`、`app/runs11_schema_helpers.py`、`app/state_schema_current.py`，代码逐字复制，没有改行为。部署路径只剩 `store_schema_release`、`action_entity_schema_release`、`task_run_entity_schema_release`、`email_schema_release`、`current_action_schema_release`；`app/email_publication_sources.py` 和 `scheduled_config_storage.publication_scheduled_source` 无运行时调用方，一并删除。
同日第二阶段补充：只被已删迁移链引用的 13 个历史结构模块也已删除（`business_legacy_field_schema`、`email_metadata_schema`、`email_training_migration`、`execution_owner_schema`、`final_source_migration`、`logical_run_schema`、`model_result_schema`、`prepared_action_schema`、`sent_result_schema`、`shared_action_schema`、`specialized_action_schema`、`task_lifecycle_schema`、`state_publication_sources`），其测试随之退役。

（2026-10-25 起已恢复：`TEST_GATE_ENABLED = True`，5 分钟真实时间到即自动停止。）
Derek于2026-10-25决定：`python -m app.deploy` 在停服务或改动任何东西之前，先在临时克隆里用干净环境对要安装的提交跑闸门测试（`tests/gate_files.txt`，单进程，2 GB 和 5 分钟），有任何一个失败就拒绝部署，没有绕过选项（`--restart` 不受此限）。运行时机制见 `docs/runtime-mechanism.md`。

以下生产数据是 2026-10-22 Phase 1 验收时的历史读回：Store `2026-10-21.1`／Email53，共 14 张普通表；运行时代码 `87a34d299d5749af1af454bfdd01c48a936799a4`，PID 2903；健康、状态、队列、Attention 和 History 均返回 HTTP 200；Action、Receipt、Observer 分别为 15,472、18,621、506 条，七个已退休来源类型为零。本轮没有重新读取生产状态，Task/Run Phase 2 也没有部署。该次读回保留了 `foreign_key_check` finding `('runs', 190042, 'tasks', 1)`；末项 `1` 是 `foreign_key_list` 的约束序号，不是 Task ID。2026-10-23 对冻结 `source.sqlite3` 的只读核对发现 runs 190042 的 `task_id=404225`、`source_type='meeting_alignment_runs'`、`legacy_key='2298'`、`job_id=4908`，而 Task 404225 不存在；这是该冻结快照中的真实缺失 Task，不等于 finding 末项。此证据不是当前生产读回，也不授权修复、伪造 Task、置空或删除事实。准确 Phase 1 发布边界见[Phase 1 生产验收](action-entity-production-acceptance.md)。
显式 Store19.11／Email53 → Store19.12／Email53 工具重建 Actions，逐值保留四字段、类型、公开／物理身份、其他业务事实与回执及分配水位；schema inverse 恢复捕获的原声明与版本时钟并保留当前数据。fresh 来源没有四字段的所有权，若迁移后新增非中性值，逆迁明确拒绝而不删除事实。生产实际来源为 actual，具有这四字段。
历史 D 九字段退休工具仍供明确旧版本迁移与逆迁调用，已批准的九字段逆迁仍填中性默认值，原评估值不再保存。Email48→49、49→50 的必要历史 DDL／校验已移至显式 history 模块，不由正常构造器加载。Workbench 附件文件维护属于业务行为，保留。
当前验收见[运行时声明清理验证](runtime-current-schema-validation.md)。历史 GPT-6 原生链路证据见[此前发布验收](database-gpt6-release-validation.md)。
字段范围、逐字段映射与证据边界见[九字段映射](business-model-legacy-fields-mapping.md)及
[验证记录](records/business-model-legacy-fields-validation.md)。
B2b 观察提交、副本及原生 CLI 的独立证据见[专有动作验收](database-redesign-specialized-actions-validation.md)。
C1 来源证据见[C1 生命周期验收](business-model-task-lifecycle-validation.md)；本候选局部验证与剩余发布门槛见[Prepared Action 归属验收](database-redesign-prepared-actions-validation.md)。
[此前阶段验收](database-redesign-final-acceptance.md)只记录 Store14.1 的历史范围；
当前 C1／C2、A5 及 D 的范围见[业务简化计划](superpowers/plans/2026-10-19-business-model-simplification.md)，
不能据此前阶段结案推断本轮全部完成或容量已通过。

以下 Phase 1 及旧 Store 版本说明是其验收时的历史记录，不能用于推断当前生产版本。

Action Entity Phase 1 已部署，当前生产即使用统一 Action owner：`actions` 的 `_kind=70` 保存共同 Action，`action_receipts` 保存逐次 provider/execution 事实，`action_observers` 保存多个 Run 的观察关系，typed identity map 保留精确旧身份。生产 Store `2026-10-21.1`／Email53 有 14 张普通表；创建、CAS 领取和结果更新由 `app.action_entity_storage` 提供，调用方持有事务。生产读回、准确退休清单、copy 容量结果和剩余路线见[Phase 1 生产验收](action-entity-production-acceptance.md)。这只表示第一外部 Action 单元完成，不表示全部 Actions 或数据库已统一。
字段映射见 `docs/action-entity-field-map.json`；`docs/action-entity-target.sql` 是初始字段合同夹具，当前完整 Store/Email 目标声明见 `docs/action-entity-current-target.sql`。

当前 native `tasks` 表的 `kind` 合同包含九类：`reply`、`work_summary`、
`meeting_alignment`、`okr_review`、`okr_analysis`、`workbench_turn`、
`email_classification`、`feedback_processing` 和 `wechat_import`。Task 的生命周期
字段与 `parameters_json` 由 `tasks` 持有；Reply 输入由 `task_inputs` 持有，
WorkSummary、Email classification、Feedback processing 和 Workbench turn 的
类型化明细由各自 owner 表持有。原来源类型及迁移结构不是当前运行时 kind 或
兼容层。当前字段、约束和类型定义见 `app/task_run_entity_definitions.py`。

Feedback processing 仍按批次领取，但结案可按 `feedback_ids` 指定批内项目。每个选中项目
必须独立满足原有证据与关联校验；未选项目保持原状态，批次在全部项目结案前仍为
`processing`。再次处理只选择未结案项目，不重放已结案项目的任务或外部动作。
结案校验只使用选中项目自身的当前轮次、关联和完成回执；服务其他队列的积压继续由
健康与队列投影报告，不阻止已满足自身结案条件的反馈项目完成。

截至 2026-10-25，Task/Run 统一已部署并构成当前生产运行时。下方按 Task2D、Task3E
等阶段记录的“未启用”“仍待后续验收”“候选”等状态，是各阶段当时的历史实现边界，
不表示当前 constructor、dispatcher 或生产部署仍待启用。当前部署和证据边界见
[业务模型当前进度](business-model-execution-progress.md)。

### Historical Task2D0/Task2D10A/Task2D1 implementation notes (superseded)

The following task-phase notes preserve their original scoped implementation evidence. Statements that constructor activation, dispatcher integration, readers, or runtime cutover remained pending describe that historical checkpoint and are not current service status. The current native Task contract and deployment status are stated above.

Task2D0 将 Reply 的 queue peek/max/get、单条与批量 claim、TaskInput 列表及 pending
trigger refresh 改为直接读写 `tasks`／`task_inputs` 原生 owner。claim 在同一写事务内
递增 attempts 并绑定当前输入及版本；后续输入刷新只推进 current 指针，不改 held
claimed 指针。范围仅是这些既有 Store API，Reply 其余生命周期、recover、terminal、
dispatcher lease、构造器和 schema 选择仍待后续验收，当前不代表完整 Task runtime 已启用。

Task2D10A 将失败 Consumer/Audit turn retry、pre-Agent retry、native Codex auth failure
查询/恢复及普通/授权 defer 切到原生 Task、TaskInput、Run、Reply Action 与 WeChat
delivery owner。turn retry 要求当前 generation 的最新 Consumer/Audit Run 失败并旋转
generation；无 Run 的 pre-Agent retry 保留 generation。auth recovery 保留原认证分类、
90 秒锁时限及 Sent、运行 Run、WeChat effect fences。普通 defer 最多按原规则退款一次
attempt，授权 defer 不退款；二者只更新原 lifecycle 字段。此单元不启用 constructor，
也不完成 Reply 的其他生命周期、dispatcher 或全局读取。

Task2D1 将 WorkSummary 输入创建、去重更新、读取、领取、终态压缩、失败重试、容量延期及
处理中恢复改为原生 `tasks.id` 与 `work_summary_inputs.task_id`；并发重复来源在取得立即写事务后
读取唯一 child 并返回同一个 canonical Task ID。`WorkSummaryInput.id` 是 Task ID。Task Agent
使用专业 Run 的原生 create/claim/finish/get，并将投影写入
`run_projections`。来源状态、due/order、尝试与错误规则及终态保留的摘要、哈希和字节数不变；
无 effect 的运行尝试按原 parent Run 关闭，已有 effect 留给原 reconciliation。WorkSummary
History、Task Agent 项目修复读取使用原生 Run/Task。此单元未启用 Store constructor 或通用
dispatcher claim，Meetings/OKR History 与 reader、其余 Task API、CLI/完整副本及发布验证仍待后续单元。
`todo_evidence_candidate.work_summary_input_id` 是存储整数的 typed Task 引用；新写入使用
canonical Task ID，历史值的正逆向转换尚待最终协调器，不能将历史读取视为已就绪。
`app.web_api.dashboard._runtime_metadata` 仍以一条包含 Reply、Meeting 与 WorkSummary 的
来源形状 SQL 读取运行尝试；WorkSummary 分支不能单独切换，需后续按全部 family 一并迁移。

Task3E1 将 Consumer／Audit Run 的 Store 读取、领取、续租、session 更新、终态与未启动删除
直接落在原生 `runs` 行；候选与审核从 adopted result／review 读取，CLI transcript 和最终结果
仍按实际 `runtime_attempts.parent_id` 引用读取。终态完成与业务采用沿用同一 SQLite 事务，
Review business ID 与 Audit Run ID 分开。此孤立单元未启用 constructor 或 RuntimeAttempt writer，
不代表完整 runtime cutover。`fail_expired_agent_run` 只更新 Run 终态，不新增 failure 事件；
其他 recovery/requeue 的既有 failure-event producers 仍待各自迁移。

Store19.8 的 C1 将九种实际可执行 Task 的源 status、due、attempt、error、时间与
既有 owner／PID／generation／expiry 事实移到单一物理列；不存在的源字段保持
SQL NULL，另外十一种记录保持原业务状态且没有通用生命周期。物理列保存一个
原始 JSONB 标量，`lifecycle_*` 生成列按原源列 affinity 解码；唯一保存的 status 是源状态，通用状态
和 `meeting_phase` 只读派生。原始非最小 JSONB 标头同样保留；共同写入器在一次
Task UPDATE 内瞬时重建源数组、调用原生 jsonb_set，再拆回各唯一 owner。
该临时数组不落库，业务 payload 的根标头只保留自身 framing。

End of historical Task/Run phase notes. The following section describes the current Task lifecycle contract.

Reply／ScheduledExecution、WorkSummary、Meeting、OKR 的 claim、续租、release、
当前 owner 判定和 metrics 读取 Task；两个 Reply adapter 的 ledger 身份与 channel
过滤仍然独立。`dispatcher_claim_leases` 只保留源限定的被动历史：旧 public ID、
generation、清空的 owner、terminal/error 及各自时间均在同一事务派生维护，
历史错误仍按原时钟和 ROWID 排序，不参与活跃租约仲裁。原有 terminal ledger
裁剪只在同一源身份／代际且没有活跃 owner 时清空对应 Task 的 claim 元数据，
使裁剪后的下一次 claim 保持旧源 generation=1 语义。Meeting 的 ready_to_send
不能重新进入 Consumer claim；送达锁 `locked_at`、实际 send claim／receipt、
quarantined 保护状态与原有取消、恢复、结果事务不变。
Task 单一仲裁的范围是这些符合源 channel／adapter 条件的 dispatcher claim。
在原生 Task/Run 组合目标中，共享 claim helper 以 `(tasks.kind, tasks.id)` 定位当前租约，
并把对应 `dispatcher_claim_leases.source_id` 写成 canonical Task ID；State 只保存按
adapter 区分的被动历史。Reply 的 stale、无 Run 的 pre-Agent orphan、服务重启与终态
orphan Run 收尾也直接更新 Task／Run／RuntimeAttempt owner，服务失败记录追加到
`run_failures`。五类 Task-backed adapter 的 State 引用只在显式 Task 转换边界通过类型化
身份映射双向转换；逆向保留 State 行 ID 和未知历史，含当前 State 编辑的两轮往返回归
已覆盖。Reply closure 的边界按当前候选范围区分：Task2D10C 已把触达的终态 settlement、
error reconciliation、rerun、requeue 和相关读取改为 native Task／TaskInput／Run／
RuntimeAttempt／adopted-result／Action／Receipt owner。候选仍未启用；
`replace_pending_single_chat_reply_task_trigger` 已以原生 Task／TaskInput／TaskBusinessKey
完成合并；WeChat 审核回执按原生 Task、Run 与 delivery Action 校验。共享
History／Attention、WeChat delivery 及其他 Reply 列表／详情读取仍保留来源投影。公共 Email
unsubscribe receipt reconciliation 仍受全局 manifest 可用性门槛约束。方法、验证和其余
发布门槛见 `task-reply-terminal-closure-report.md`。
`EmailStore._unsubscribe_cancellation_decision` 仍读取 channel=email 的
reply／scheduled_execution State 历史；该 channel 对两者都不 eligible，
这条既有取消前置事实不迁成 Task 租约，也不据 owner／expiry 改变取消判定。

C1 的历史 paired coordinator 只接受准确的 Store19.7／Email53 源，发布 Store19.8／Email53。
current-data inverse 使用当前 Task 单一 owner 与未归档的被动 ledger 重建旧声明及
精确 JSONB 字节，保留源 marker 原时钟、Email 账本、FK 变体和分配水位；不恢复
旧数据快照。字段来源、九种状态映射、验证与成本见
[C1 Task 生命周期说明](business-model-task-lifecycle-validation.md)。
局部实测区分 Task 的索引点选择与既有 generated-parent FK 检查：后者在旧／新
payload UPDATE 上都随无关行数增长。C1 没有新增该扫描；本地旧／新对照、单语句
原子性和已知 2 秒期限测试失败不构成生产启动／全量副本容量已通过的声明。

Store `2026-10-19.10` 的 C2 将 Task Agent、Meeting、OKR 的真实历史运行合到
`runs[agent_runs]`。`source_type` 保存原来源 namespace，`id` 是共同物理 Run ID，
`legacy_key` 保留原公开键的 SQL 值与类型；公开唯一性仍按原整数 cast 语义、分
namespace 检查。原 Consumer／Audit 的 `source_type` 为 NULL，所有角色、revision、
adoption 入口只选这一分支；Run logical-parent 和四类 Actions Run 引用的原
类型边界同样限定 `source_type IS NULL`，逆迁恢复原 trigger 文本。
`codex_session_search_index.source_type` 含义不变。
旧深链、API 与 workload key 用 `(source_type, legacy_key)` 读取真实共同 owner，
不能直接把同号公开键当作物理 ID。新 Run 使用已有 runs AUTOINCREMENT、公开键
触发器与 `runs:published` 水位，不增加分配器、编码或持久旧行副本。
Task／OKR 的字典 DTO 保留原公开 `.id`、`source_key` 和 `common_run_id`；
MeetingAlignmentRun 的 `.id` 仍是原公开键，另有 `common_run_id`，没有 `source_key` 字段。

在 Task2D1 之前，三个专业来源共同使用 `create_logical_run`／`claim_logical_run`／
`finish_logical_run`／`get_logical_run` 与同一恢复写入器；Task2D1 已将 WorkSummary
Task Agent 改为原生专业 Run API，Meeting 与 OKR caller 仍待切换。旧三套 begin／record／
finish／get writer、九个已无存储事实的结果参数及对应固定 SQL 输出退休；原生
trajectory 的业务结果仍供详情和 UI 读取。Task 的唯一采用回执 `projection_json`
及 Meeting Job 已采用的决定不删。专业 Run finish 不进入 Consumer／Audit 的
`complete_agent_run`，也不制造 candidate、review 或 adoption。

Task `summary_input_id`、OKR `request_id` 以各自真实 Task 类型的公开键定位
`task_id`；不支持精确值与 SQLite 类型逆迁的来源在写入前拒绝。Meeting 保留原
物理 `task_id`；已存在的 missing-parent 预留身份、`job_id` 与 FK finding 两向
保留，不制造 Task。Task／Meeting 的原 `finished_at` 由 `completed_at` 唯一
保存，所有原 status／session／error／时间和 JSON text 不归一化。OKR 从未存有的
status／error／完成或更新时间，以及三个来源缺少的 role／generation／revision／
turn／operation／lease／start，仍为 SQL NULL；Task 缺少的 transcript range 同样
为 NULL。没有真实模型调用的历史 Run 仍是 Run，不补 attempt。

`agent_runtime_attempts` 继续独立拥有实际调用、route、attempt number、retry 与
lease；workload_kind／完整 workload_key／parent_id 原值不变。plain Task Run key
与 `decision_repair.N` 仍指向该 typed Run；`deadline_backfill[.N]` 和
`memory_backfill` 分别保留旧 Todo／Project 父归属。operation attempt 的 parent_id
保持 NULL，其 owner/expiry fence 不变。该阶段使用的 latest/exact standalone
包装已退休；当前专业运行与会议搜索共用 `native_run_ref`。它按当前
RuntimeAttempt 的 parent_id 和执行 workload_kind 读取；Task 用 task、Meeting
用 meeting，不把 meeting_audit 当成执行结果。来源不可用仍明确返回不可用。

C2 的配对发布只接受准确 Store19.9／Email53，目标 Store19.10／Email53。
逆迁从当前共同 owner 重建旧来源事实，包括正迁后的新 Run；只用 TEMP 暂存，
保留物理 ID、SQL 值／类型、原 marker 时钟、全部真实 sequence 行／ROWID／水位
及原 FK multiset。历史 Runs8／Runs11 Factory 输出冻结；当前 constructor、native、
History、search、UI 和实际业务 handler 选择共同 owner。字段映射、拒绝边界、
实际旧 DAO 对照及未完成发布门槛见 [C2 Run 归属验收](business-model-run-owner-validation.md)。
发布 quiet 与 maintenance 使用该配对 release 的准确当前版本及前序版本集合
（包含19.9）；State 未知版本仍拒绝。Run publication 仅在明确19.10/19.11且当前
Run 声明匹配时使用 `source_type IS NULL` 的 Consumer／Audit 投影；早期没有
`source_type` 的声明保持原投影。已有等待、活跃 claim、租约和锁判断不改变。

Task/Run Phase 2 的 B3B1 forward 接受显式版本 1 JSON Run identity handoff；它只
含稀疏 `(source_kind, source_physical_id, canonical_id)` 边、八个原生 Run owner 的
独立 AUTOINCREMENT high-water、只绑定选定 source DDL 的 capture context 和 SHA-256 digest；合法旧源写入可在
inverse 后发生，forward 每次以新的 source capture 校验当前行、FK 和 sequence 事实。当前选定
`logical_run_current_schema` 仍是唯一 Run 来源；缺少或与 capture 不匹配的 handoff、
未知／重复／悬空身份以及同一 owner 内的 canonical ID 冲突在替换源 `runs` 表前
拒绝。Run ID 与 logical parent、专业 provenance/projection、runtime parent/workload、failure、schedule、
Workbench 与 Meeting search 引用都用同一预计算 typed map；Task ID 仍由 Task2B
metadata 解析，来源中原有的 missing Task FK 保持相同 Run 边。来源公开键仍以原
SQLite `CAST(... AS INTEGER)` 语义解析；identity map 分别保留来源物理 ID、公开键
及其 SQLite 类型和 canonical target。native owner 水位至少取 handoff 捕获值和迁入行最大 canonical ID。`runs` 的
SQLite 序列名同时承载来源 global runs 水位，因此保留来源 global 水位、handoff
原生水位和 canonical 行最大值的最大值及来源 ROWID；published/per-kind 与其他
来源 sequence 行保持原 ROWID/name/value，并保留在 capture metadata 供 inverse 使用。
B3B2 先调用 Task inverse，再将其 content-free `inverse_identity` 与还原后的 selected
source Task projection 传给 Run inverse。Run inverse 从八个当前 native owner 的现值重建
来源字段；新 Run source physical ID 高于原 global water 和已保留 source IDs，Task references
使用来源实际 public identity（包括 Email message hash 与已分配 Workbench TURN UUID）。Run
handoff 是版本 1 JSON，包含稀疏 source-kind/source-physical/canonical 边、八个独立 native
owner high-water、selected source DDL context 与 SHA-256 digest；source sequence 只增不减：
`runs:published` 按旧触发器追到非 RUN11 新种类的物理 ID，三个 RUN11 新种类各自按
来源 public legacy key 维护独立水位；新专业 Run 的 public key 与旧 `create_logical_run`
触发器一致，取新物理 ID 的文本形式，既有专业 public key 原样保留。八个 owner 的缺省值
不相互推导，insert 后 delete 的 AUTOINCREMENT 水位也随 JSON handoff 保存。未映射 native
删除事实不会从初始快照复活；原先有映射但当前 owner 已不存在的 occurrence/cache 行会从
还原源事实中退休，handoff 也只携带仍存在的 owner 身份。保留的子事实若因此产生悬空引用仍拒绝，
不会借退休过滤掩盖。Task-first composed inverse
与 handoff producer仍是 migration-only；没有激活运行时。

C2B2C 提供 migration-only `restore_run_adoptions`，组合现有 B1 identity planner、
retained/adopted Action row builders，在 Task inverse → Run inverse 之后恢复旧 Actions、
receipt、observer 与 identity-map catalog。它读取当前 adopted owner、current receipt/observer
和 Task/Run handoff 重建来源事实，不依赖初始 TEMP 行快照；调用方持有 FK-off transaction、
commit/rollback。forward 会按实际 source/target stored-column 差集拒绝非空的 retired-only
保留字段。此入口尚未接入 constructor 或运行时；完整 frozen copy 与旧 cold-writer 验收仍待后续阶段。

Actions 保留五十九种物理类型：五十八种方案、审查、候选、修订、动作、尝试、步骤、业务事件和回执，以及一种只保留旧 execution 身份的技术 alias。
类型只存 `_kind`，业务值使用声明的 JSONB 槽位及 prepared Action 的三个源限定列；公开字段按其唯一 owner 读取。
公开 ID 与物理 Action ID 保持区别，各源 AUTO 水位和普通隐藏 ROWID 语义保留。
Reply、Email、Sync、delivery、Errors、Feedback、Business evidence、Email history
及 Skill receipt 调用方均使用这一结构；原事务、CAS、claim/lease、约束顺序和
幂等回执合同保留。Errors 的 ID/时间读取和 Attention 的未解决项使用其既有局部索引。
Email Attention 先在 Tasks 的现有类型／状态索引上筛选 failed 的公开 ROWID，再读取这些任务的内容；不通过通用完整投影扫描成功任务的输入。

Store `2026-10-19.9` 的 A5a 以现有 `outbound_postfixes` prepared Action 为唯一
owner，退休 `outbound_postfix_receipts` 与 `native_reply_dispatches` 两个物理类型。
父记录的 channel／delivery_key、final_body、feedback_token、postfix_version、
created_at 及整个原始 JSONB payload 不改写。三个 nullable TEXT 物理列分别保存
源 receipt 的原始 provider_result_json、receipt.created_at，以及 dispatch.created_at；
只有 prepared 父类型可持有它们。SQL NULL 表示不存在的子事实，文本 `null`／`{}`
保留为已存在的 provider 字符串；claim 与 receipt 的时钟独立于准备时钟。

Task3（2026-10-21）将 reviewed、direct-email、prepared-message 与 Sent 执行恢复切换到同一 Action owner。调用方保留原事务、审批、provider 核验和不确定结果恢复边界；prepared 的准备、claim、接受回执仍在各自原有提交边界。prepared Action 在真实发送调用时才记录 provider operation；prepared facts 不借用 Sent 或 provider 时钟，且 `prepared_receipt_id` 精确指向其接受回执，避免与同一 owner 的 Sent/canonical provider body 混淆。审查执行在每个 execution/action_index 保留一个可更新的当前观察，旧执行的观察与 provider receipt 仍独立保留。direct-email retry 复用原 Action identity 并追加各次执行观察；retry request 是不可替换的追加事实，同一 Action identity 与 attempt number 的再次插入会被拒绝。Sent 历史缺失的 canonical operation、target、digest 或 action identity 保持 NULL；已核验的 failed-Audit 恢复可把这些事实绑定到原 provider body receipt，不复制正文、不重发。canonical provider 结果通过 reference-only metadata receipt 直接指向当前 immutable body；普通 provider receipt 不会仅因 ID 较新而改变该绑定。复杂 unsubscribe 保留原专有流程；这次切换不改变任何审批、授权、核验或重试规则。

Task4（2026-10-21，候选）将 direct-email History、Attention、队列快照、重要邮件清单及邮件分类可观测性读取切换到 Action 与 Action Receipt。发送详情以 Action 的物理 ID、正文、准确 Sent 回执和 `observed_at` 展示；provider 正文只从该 Action 的 provider receipt 读取，缺失的 operation/target 显示为未提供，不合成来源事实。多个 Run observer 仍对应一条发送效果。撤回只更新同一 Action 的撤回字段，不新增发送或改写原发送回执；feedback token 从同一 Action 取得，并继续按其原 conversation/trigger 身份关联消息和反馈。邮件退订仍走其专有流程。

Task6B 的 Dashboard Email 队列从 `DIRECT_EMAIL_ACTION_SQL` 和当前分类投影读取 pending、processing 及按 worker 原 retry 谓词可重试的 failed Action；队列 ID 使用 common Action 物理 ID，标题、状态和空详情链接维持原 API 契约。Email Attention 的 common Action ID 以字符串输出，符合 `AttentionRow.id` 的严格响应类型；失败筛选和工作者状态投影保持现有语义。未解决的全局服务错误即使没有 conversation/message identity 仍显示在 Attention；SQL NULL 不得把它们排除。

Task5A（2026-10-21）在当前真实 prepared-message caller 与 reviewed SystemExecutor 路径中，以 `agent_message_delivery_key` 对应的 channel/delivery key，将已准备的 Action owner 绑定到已审批选项的 reviewed Action，再由同一 owner 承载 dispatch observation、canonical provider receipt、Sent receipt 和 observer。选项 key、task generation 与 proposal revision 保持在源 delivery identity 中；provider 仍只调用一次。

Task5B（2026-10-21，本地前向迁移已验证）把只读 Store19.12／Email53 源中的七个 covered families 转为共同 Action、Action Receipt、Action Observer 与 typed identity map；确定的 canonical、approved-step、delivery-key 和原 owner 引用才合并，孤立来源保留为独立 Action。退休 covered source kinds 与其 family-only DDL。两个仍在范围外的 direct-retry control rows 只把既有 `email_actions` 父引用经 typed map 改到 `actions.id`，其他 control 字段和时钟保持原值。完整 populated copy 通过 target manifest、原有单条 FK finding 多重集与 `quick_check`；对源中 72,478 条未覆盖 Action 行逐物理字段比较，唯一变化是这两个 retry row 的 parent composite-key 单元。该证明仅覆盖独立拥有的本地副本，不是 current-data inverse、old-code cold-open/reforward、发布或生产行为验收；Task5C 与独立最终审查仍是发布门槛。没有改变审批、provider 核验或 retry/reconciliation policy。
Task5C1（2026-10-21，本地候选）修复了 prior reconciliation 的 source mismatch：已有 canonical body 不同于本次 attempt 结果时，canonical metadata/body 保持不变，差异结果保留在该 exact attempt。ambiguous-to-sent correction 由 reference-only canonical metadata receipt 直接改指向新 immutable body；它保留 source 的 first operation、target、run 与 clock，current digest 从 exact body 投影。其他 candidate observation 和被动 Sent 仍引用原 body；只有本次 corrected observation 前移。已无独立引用的被替换 verification wrapper 才会被定点清理。

Task5C2（2026-10-22，本地候选）实现从 current Action facts 逆迁回 Store19.12／Email53 源声明：按 typed source identities 恢复旧公开 ID、父引用、候选原始列、独立本地结果正文与 ordered History/retry 引用；用准确源声明重建表，并保留 57 列 attempt 的原始 out-of-scope 字段和 sequence 水位。已在隔离的实际数据副本上逆迁新写入 facts，由 pinned 旧 Store/EmailStore 冷读并写入 prepared postfix，再由当前版本迁回，验证原始 source facts 及新增正文、回执、时钟和引用仍可读。该本地证据不代表 native/capacity/UI 门槛、独立最终审查或发布/生产验收；没有改变审批、provider、授权或 retry policy。

A5a 的既有配对发布只接受准确 Store19.8／Email53，发布 Store19.9／Email53；
C1 Task 声明和所有历史 Actions 声明输出冻结。current-data inverse 用当前父值重建
旧子事实，包含正迁后的新准备、claim 和接受回执；只保存声明变体和原 marker 元数据。
所有父／无关记录的物理 ID、SQL 类型、值与 payload 原字节及真实 sqlite_sequence
行／ROWID／水位保持。退休子记录没有公开整数 ID、入站物理子 ID 外键或业务 URL；
其重建 private ID、legacy_key 的等价 JSON 拼写和下一次普通 private ID 分配可以改变。
无调用方的旧内部子记录 CRUD／物理 FK 操作合同随类型退休，不增加替代触发器。
本地验证、字段映射、空间测量和未完成发布门槛见
[Prepared Action 归属验收](database-redesign-prepared-actions-validation.md)。

Store `2026-10-19.1` 的 `candidate_selections` 保留物理 ID、公开 legacy key、
candidate／review FK，以及 `option_key`、status、created_at 三个 payload 槽位；
`branch_json` 槽位和生成列退休。执行、回执核对、Worker OA 历史及 Console 详情
均从选择所绑定的不可变 candidate 和唯一 option_key 读取分支。旧 API 中的
`branch_json` 仅为请求内派生值；后来的候选不替换原选择或历史回执的分支。

Store `2026-10-19.2` 的 `candidate_action_attempts.result_json` 原槽位允许 SQL NULL：
仅 verified 且原始字符串与同一 `external_action_key` 的
`external_action_results.provider_result_json` 完全一致时，正文由 receipt 唯一持有。
failed、uncertain 和独立的结果（包括不同于 receipt 的 `{}`）仍保留在 attempt。
执行、History、详情和修复读取只在 verified／NULL 时从准确 receipt 派生原 API 的
`result_json`；不以空值真伪选择结果，缺失 owner 明确报完整性错误。
正常确认与历史尝试对账在原事务中写 receipt 和 NULL 引用。既有 receipt 修正会
先把此前引用它的历史尝试还原为原正文，再将本次实际修正的尝试指向新正文；
因此未被修正的历史事实不会随 receipt 改写。原有身份、幂等和状态合同不变。

B1 的历史配对发布将准确 Store `2026-10-19.2`／Email52
发布为 `2026-10-19.3`／Email53。B1 只退休五个重复的 classification 元数据列，
以现有 message 列为唯一 owner；发布前逐行检查四对标量的 SQLite 值及类型完全
相等、classified 的旧 message_preview 为空且 class_preview 为至多1000字符的文本。
message-only 的五个旧分类槽位必须为 NULL，有界预览保持原值；任何来源差异
明确拒绝，不清洗、不读取 provider 或原文。源 Email52 的默认
声明工厂、A1／A2 的版本常量与历史发布入口冻结；Email53 必须显式选择。
普通构造器只接受当前声明或正常空库，不自动升级 predecessor 或 mixed pair。
发布事务同时重建 Emails、更新 Store marker 并追加 Email53 marker，然后用既有
冷启动入口验证。B1 失败回滚从当前 message owner 重建五个 class 字段，classified
的旧 message_preview 恢复为空，message-only 行的 class 字段恢复 NULL；包括发布
后新建、改址及改元数据的行。公开／物理 ID、独立分配水位、SQLite 类型、原 marker
时间及 FK finding 多重集合保留。逆迁仅保存声明 variant、marker 和 FK finding，
不保存业务行副本；两种已有 Reply FK 及 Action 到 classification_id 的 FK 保留。
A2 的历史逆迁仍从当前 receipt 恢复 verified／NULL attempt 的正文；A1 仍从绑定
candidate 重建选择分支。B1 的局部证据见[Email 元数据验收](database-redesign-email-metadata-validation.md)。


Store `2026-10-19.4` 将已审核 candidate 作为执行状态的唯一 owner。candidate 原有
created_at、失效时间及原因不变；七个新增 JSONB 槽位保存旧 execution 的公开 ID、
status、lease owner／expiry、result、created_at 和 updated_at，两个可空物理 FK 保存
实际采用的 review 和 selection。未执行 candidate 的九个值全部为 NULL；选择可以为空，
执行时其余值按原声明完整保留。多个 review 仍是独立历史，不由执行状态替代。
旧 `candidate_executions` 类型不再进入当前声明或运行时写入；其类型码5及历史声明冻结。
新增类型码62的 `candidate_execution_alias` 原位保留旧 Actions 物理 ID、legacy key 和
candidate FK，JSONB 是空数组，身份不可改写或删除，不持有状态、租约、结果或时间。
Attempt 的物理 execution_id 继续指向 alias；公共投影连接 plan 后仍返回原 execution
公开 ID，绝不以 candidate ID 代替。claim、续租、finish 及既有历史回执修复只更新 plan。
既有代际、最新候选、失效、审核、租约、终态和回执校验保持原语义。

Alias 与完整执行块由可延迟到 COMMIT 的双向复合 FK 保持一一对应，三个虚拟 binding
列仅由已有物理／公开身份和类型生成。孤立 alias、缺 alias 的执行块或身份不符不能提交。
独立 candidate 唯一索引禁止多个 alias；复合 FK 保证采用的 review／selection 属于同一
物理 candidate，selection 的 review 与采用的 review 一致。首次 claim 在原事务内建立
alias 并填写执行块，使用原普通 INTEGER PRIMARY KEY 的公开 ID 分配语义；不新增水位。

A3 的历史 `prepare_database_schema_release` 将准确 Store `2026-10-19.3`／Email53
发布为 `2026-10-19.4`／Email53。A3 在原发布事务内重建 Actions 并更新 Store marker，
Email53 账本及其他表保持原值；构造器在写入前拒绝 predecessor 或 mixed pair。迁移前
检查每个既有执行的精确候选／review／selection 绑定，两种真实 Reply FK 声明分别匹配。
失败回滚从当前 plan 执行块与 alias 重建旧 execution，包括迁移后新建执行和 attempt；
没有执行块就不生成 execution。保留物理／公开 ID、SQLite 类型、原 marker 时间及所有
已有分配水位，不归档业务行副本。仍使用既有 prepare、publish、verify_loaded、rollback、
finalize 和旧代码冷启动验收。A3 仅完成 reviewed plan 的执行归属，Reply／Sent／Postfix
及 Email 通用 plan／step／attempt 的后续切片未在本次完成；证据范围见
[执行归属验收](database-redesign-execution-owner-validation.md)。



Store `2026-10-19.5` 的 Sent provider 正文只在一个位置持有。原
`sent_replies.send_result_json` 槽位仅在已有非空 `external_action_key` 指向
`external_action_results.provider_result_json`、且两者的 SQLite 值和类型完全相等时
改为 SQL NULL；正文通过同一查询快照中的准确 key 连接投影。空 key、无 owner、
不同的正文、原始非 JSON 文本和历史默认空字符串均保持原语义；不解析或规范化 provider
正文。Sent 的物理／公开 ID、AUTO 水位、trigger、消息正文、发送／撤回时间、撤回字段、
feedback token、run、channel，以及 observer 的物理 FK 和公开 pair 都保留。

两个只由原 external key 派生的虚拟列和可延迟到 COMMIT 的 FK 保证 NULL 引用有
owner；不新增业务字段。一个完整唯一 parent key 索引是 SQLite FK 的要求，另一个
只含 NULL 引用行的局部 child 索引避免 owner 变更时扫描全表。目标声明的精确相等
触发器使正常 Sent 写入、明确正文修改和后来到达的 receipt 共用正文。外部 owner
修改或删除的 AFTER 触发器将未被明确修改的 Sent 恢复为旧正文；AFTER 顺序避免
Sent 的相等触发器又把正文去重到尚未改变的旧 owner。仅修改 Sent key 时也保留其
此前正文。既有 delivery repair 先修改 receipt、再明确修改 Sent 的事务仍采用新结果，
可再次去重；candidate attempt 的 A2 保存机制与 provider 歧义判定不变。

A4 的历史配对发布只将准确 Store `2026-10-19.4`／Email53 通过已有 prepare、publish、
verify_loaded、rollback、finalize 入口变为 `2026-10-19.5`／Email53；构造器在写入前
拒绝旧版、混合及未知声明。逆迁从当前 Sent 与 receipt 重建旧完整正文，包括迁移后
新增、修正及删除 owner 后保留的事实。源声明工厂、字段排列、类型码和默认值冻结；
不保存迁移前业务行副本，不增加部署或兼容入口。

本切片同时退休无应用调用方的 `record_agent_message_delivery`、
`record_reply_attempt_for_trigger` 和 `finalize_orchestrated_reply_task` 的可选 Sent
插入分支及两个参数。仍在使用的 `record_sent_reply`（OKR CLI）、
`record_reply_attempt`（包括 direct unsubscribe）和 finalize 的 Attempt／任务写入
保留；历史 Sent／Attempt 类型、读取及逆迁继续支持。`complete_agent_run` 原本只更新
run，未作为 Sent writer 退役。Postfix 的准备／首个接受结果、OKR、Reply Attempt 类型、
Email 通用机制及 Reply／unsubscribe claim／result 归属分别由后述 B2a／B2b 实现；A5 及 C／D 业务单元仍未完成，A 单元不能据此整体结案。
局部实现、测试和源冻结证据见[Sent 归属验收](database-redesign-sent-result-validation.md)。

Store `2026-10-19.6` 的 B2a 将 reviewed Consumer 与确定性 direct Email 的
plan version、effect step、attempt 交给 `shared_action_lifecycle` 的同一分配、写入与
claim／outcome 机制。五个现存来源原位改用追加的类型码63–67；原 `_kind` 码、
声明、字段顺序和默认值冻结供显式逆迁使用。`action_family`／`action_origin` 只由
原来源身份生成；只读 `record_type` 继续返回原名称，供既有 Emails／Configs 触发器、
索引和公开查询使用。没有第二份业务模型或运行时格式探测，旧 direct Email 状态写入
分支及 `_update_direct_action` 已退休；Reply／unsubscribe 的专有事实继续保留。

Reviewed plan 保留全部原 candidate 字段和 A3 执行块，独立 review、Audit run、
human selection 及采用关系不变；effect step 从准确采用的不可变 candidate／option
分支读取，action index、identity、operation、target、payload 和 effect 不另复制。
Reviewed attempt 仍是 `(execution_id, action_index)` 的一个可变槽位，重派发不追加
历史或计数。Direct Email plan 保留原版本、classification、account、完整原授权快照、
动作及参数；重新分类只采用新版本，不把旧计划或回执改绑到新分类。Direct step 保留
当前 status、next_attempt_at 和进行中 started_at；终态的开始／完成时间、provider
operation／target／result ID、error 只由最新的有序 attempt 持有，attempt_count 从
原 append-only ledger 计数。新 claim 的公开投影清空旧终态字段并显示本次 started_at，
不泄露上一轮 attempt 的结果。Email attempt 保留原整数公开 ID、物理 ID 和独立 AUTO
水位；Email provider 操作不生成 Consumer／Audit／AgentRun 或伪造 provider JSON。
A2 verified／NULL 正文与 A4 Sent receipt owner 继续使用原规则。

B2a 的历史配对发布通过既有 prepare、publish、verify_loaded、rollback、finalize，准确地从
Store `2026-10-19.5`／Email53 转到 `2026-10-19.6`／Email53。转前逐一验证 direct
attempt 的整数连续序号、数量和最新镜像的精确 SQLite 值／类型；pending 必须无历史，
processing 的终态字段必须已清空。任何来源例外在写入前拒绝，不擦除或修补。
正逆迁的临时 Actions staging 在 CTAS 后立即按物理 `id` 建临时索引，逐行改写
direct step 时按 ID 查找；索引随临时表删除，永久源／目标声明不变。
逆迁从当前 plan／step／attempt 重建旧布局，包括发布后的新计划、新 reviewed 执行及
可变槽位、新 Email retry attempt 和当前 claim；原 Actions 物理 ID、公开 identity、
FK finding 多重集合、分配水位及原 marker 时间保留。Email53 账本不增版本，源行不归档。
B2a 的证据只覆盖上述五类来源；Reply／unsubscribe claims 由后述 B2b 接续，Postfix 归属由本页 A5a 接续。
局部 fake-provider、源程序差分、逆迁及冻结证据见
[共享动作机制验收](database-redesign-shared-actions-validation.md)。


Store `2026-10-19.7` 的 B2b 将 Reply 的17字段 dispatch claim 和 unsubscribe 的
19字段 claim 原位改为同一共享 effect step，追加类型码68／69、来源为
`email_reply`／`email_unsubscribe`。`record_type` 的只读原名称保留，公开 DTO、
Actions 物理 ID、legacy key、唯一 outgoing message ID 及所有原字段／FK 不变。
Reply 的 sender、thread、account snapshot／更新时间，以及 unsubscribe 的 phase、
entry reference、operations、audit run、thread／account 时钟仍由各自 typed step 持有。
既有 EmailStore 入口保留来源校验、provider 证据核验与事务边界；claim、CAS 状态、
回收、重试、完成及 receipt 写入统一由 `shared_action_lifecycle.transition` 执行。
原逐行 claim／receipt mutation SQL 循环已退休。CAS 在同一 SQL 中通过原身份索引
取得物理 ID 并更新 Actions，owner generation／lease token 的原条件不变。

Reply 的13字段 receipt 和 unsubscribe 的20字段 receipt 继续各有一个权威 typed
result owner，共享结果入口直接写入它们；claim 的状态不生成 SMTP／browser 成功。
Unsubscribe 多版本 effect journal、独立整数 AUTO 水位的有序 observation steps、
unfinished continuation 的 observation／controls／时钟、explicit cancellation 的
principal decision，以及 feedback 的计划版本采用和不可变授权快照均完整保留。
这些事实不改写为通用 attempt，不制造 provider JSON 或 AgentRun。原 uncertain
恢复、来源核验后的显式 retry、no-effect／skip、取消及 receipt 幂等语义不变。

B2b 配对发布通过已有入口准确地从 Store `2026-10-19.6`／Email53 转到
`2026-10-19.7`／Email53；只转换两个 claim 类型码和对应声明，B2a 五个来源的
声明工厂／映射不变。正逆迁按一遍临时 staging 转换类型码，逐项比较原存储值和
SQLite 类型、物理／公开身份、独立分配水位及原 FK finding 多重集合。逆迁读取
当前 typed claims／results 和独立证据，包括发布后新建的 claim、receipt、effect
版本、continuation、cancellation 及 retry 状态；仅保留声明／marker 元数据，
不保留源业务行副本。Email53 账本、Configs 声明和原 source marker 时钟不变。
父级已完成 B2b 标准部署和 Store19.7／Email53 生产回读。
该切片不包含 A5 的 Postfix／OKR／Attempt 归属及 C／D 业务单元；
C1 和 A5a 的发布状态分别以各自验收记录及生产回读为准。
局部测试、实际 archived Store19.6／Email53 冷启动和 source freeze 范围见
[专有 Email 动作归属验收](database-redesign-specialized-actions-validation.md)。

Store `2026-10-25.1`／Email54 的 A5b 将 Reply Attempt 中能按同一 Agent Run
祖先链、operation、OA process/task identity 和完全相同 provider JSON 唯一对应的
历史结果改为 `reply_attempt_receipt_refs` 外键，provider receipt 仍是正文唯一 owner。
未匹配或有多个候选 receipt 的 Attempt 保留原本地结果；新完成流程只在收到已核验
receipt ID 且正文完全相等时建立引用，并在同一事务清空重复正文。History、详情、重开
读取通过引用投影得到同一结果；receipt 退休会检查该引用。来源 Store `2026-10-23.1`／
Email54 到目标的显式迁移只处理唯一精确匹配，逆迁先把引用正文还原到 Attempt 再删除
关系并恢复原 schema marker。该存储归属不改变 Audit、审批、授权、重试或外部动作策略。
A5 原始输入已于2026-10-25通过真实 GPT-6 Luna Codex CLI Consumer／Audit 和替代 provider 的精确审查选项重验，见[A5 原始用例重验](business-model-a5-original-revalidation.md)。这项后续验收不改写2026-10-19失败，也不表示所有 A5 用例或模型输出均通过；不是由上述存储归属变更直接关闭。

最终紧凑 Tasks／Actions 的既有触发器在生成 schema 时把类型判别和类型不可改写的 UPDATE OF 事件绑定到实际 `_kind`；公开 `record_type` 仍是只读名称。这样避免 SQLite 在仅更新其他列的 BEFORE UPDATE 中遗漏虚拟 NEW 类型值。历史 TEXT 来源工厂不改写。Tasks 声明为数值 legacy_key 的来源身份直接投影其整数表达式（NULL 保持 NULL），来源投影显式包含现有 numeric identity 部分索引的准确谓词。父任务和 canonical 输入均使用现有身份索引定位，避免审查链路扫描无关输入；不新增索引或字段。

Runs 包含原八类执行及搜索会话元数据、分类运行样本、设置事件，共十一种既有类型。
公开源 ID、物理运行 ID、attempt 关联和水位保留。持久标题 FTS／shadow 表退休，
搜索仍读取 CLI 原生 trajectory 并使用既有请求内／内存索引；逆迁重建旧声明和由
恢复元数据派生的标题索引。分类运行样本的全局保留上限仍为 2,048。
旧的 native 副本／持久 FTS 清理函数及其隐式 CLI 动作退休；这些正文和索引在 schema 发布时已退休。`python -m app.storage_maintenance` 必须选择现有的 `--work-summary-inputs`、`--settled-reply-inputs` 或 `--done-email-classification-inputs` 之一；三个输入清理实现不变，物理 VACUUM 由维护调用者在静默窗口单独协调。

Business 保留已有 Project、Task、Anchor、context revision、修复候选、Work Cluster、
TODO evidence、Work Project、Work Todo 及候选业务对象。Work Project 的
`memory_context_json` 退休，无替代缓存。WorkTodo 的正式 Task 关联直接存
`business.import_task_id`；独立 LegacyLinks 表和旧 Action guard 退休。
真实字段、公共 ID、父子 FK、普通子行 ROWID 和分配水位由当前 typed DAO 保持。
Store19.11 按 Derek 2026-10-20 确认永久退休九项：WorkProject 的 priority、risk_level、
needs_derek_attention、next_follow_up_at、follow_up_mode；WorkTodo 的 priority、
next_follow_up_at；WorkUpdate 的 merge_reason、confidence。当前 payload key/slot、
生成列、对应约束、两个旧运营索引、DTO/API/UI/历史检索属性一并退出。
保留 follow_up_question、changes_json、负责人/截止日期/证据/来源/时钟及所有回执；
原 51 字段留下 42 字段，975/3327/5468 个来源身份继续可读。该来源严格清单均为
history_only，当前 source-backed 正式导入条件仍保留，清单版本升为4。
逆迁从当前数据补回九项中性默认值并恢复原声明，不归档或恢复旧评估值；
其它保留 SQL 值/类型、ID/ROWID、分配水位与 FK finding multiset 保持。
`business.import_task_id` 仍是唯一已采用 Todo→Task 关联，不重建 LegacyLinks。
完整51→42映射与默认值见[九字段映射](business-model-legacy-fields-mapping.md)。

手工旧业务模型写入命令 `backfill-routine-process-todos`、`backfill-todo-deadlines`、
`backfill-todo-owner-ids` 和 `repair-task-projects-apply` 已退休；旧 Project 只读修复规划器 `repair-task-projects-plan` 及其专用解码器也已退休。历史 Work Project／Work Todo／Work Update
行与已有回执继续供读取和 History 使用；D1a/D2 仅退休旧入口。当前 Task Agent、
Task 写入及 task-semantic-import 的来源核对与正式采用条件保持，19.11 字段处置按上文执行。
五个无运行时调用者的旧 Store writer（创建/更新 Work Project、创建/更新 Work Todo、
创建 Work Update）已退休；历史测试用当前 typed 存储原语在测试事务中建立来源事实。
旧 Work Todo 的钉钉镜像执行代码也已退休：不再注册 `task_todo_sync_outbox`
dispatcher adapter，Status 中该 adapter 的队列展示也已移除；不再创建、重试、回读恢复、完成外部旧 Todo，也不再通过
旧 Store API 改写 link/outbox。当前镜像继续只按 `business_task_id` 使用
`business_task_todo_sync_outbox`，创建、完成及未知回执语义保持原样。
Derek 2026-10-20 确认：无法核验 current Task 身份的旧 Todo 仅保留原身份的来源、
业务事实和执行回执；不按共享整数 ID 晋升或关联 Task，不取消、完成或重发真实外部 Todo。
旧 `active` 是已保存的历史状态，不代表仍在运营；历史 link/outbox 读取、History 和
legacy-project 深链接保留。D1b 不改变物理声明或旧记录。独立 D1c 已从 Status 聚合移除旧 `DingTalk Todos`
及其专属 retryable 计算，并从小时质量检查的 required/optional 来源和外部队列检查中
移除旧 link/outbox；历史 link/outbox 事实、回执及读取保持原样。D1b/D1c 的源码结果
不代表完整 D、正式发布或 live 验收。
Project CRM 搜索、确认和清除关联仍在原业务写事务中；搜索只存本地候选，
不替代已确认关联，也不写外部 CRM。

前端只保留专用 `LegacyProjectDetail` 历史详情适配器，不再把正式 Task 列表映射为旧 Project 摘要，也不从历史 Todo 推算当前进度。无调用的三个 Work Project/Todo 批量列表 Store API 已退休；精确历史 getter、来源、事实与回执读取保留。

原 `business_source_documents` 正文表及数字容器 ID 退休。Signal DAO 仅持久化
来源身份、精确版本、hash／字节数及 locator。Store25.3 目标把所有来源版本统一交给既有
`provenance` owner；Signal 仅保存必需 `provenance_id` 与标题、context、dedupe_key、created_at
四项自身事实。首个 Signal 即创建或复用 parent，第二个 Signal 不修改第一个 Signal；
父来源与 Signal 插入失败时原 savepoint 整体回滚。运行读取只走 parent join，无 inline
兼容或 promotion。显式25.2→25.3发布与当前数据 inverse 保留来源值/类型、公开与物理
身份、关联和回执；旧17槽仅供迁移/回退工具。该目标尚待副本验收和标准发布，基线生产仍是25.2。
证据引语、来源关系和已采用业务事实保持原义。详见[Signal来源归属](signal-source-owner-unification.md)。
历史正文退休工具 `final_action_schema` 的17槽投影固定在工具内部，不能借用当前5槽 reader；
它仍按原精确 binding/read_body 合同逆迁共享与内联来源。历史 Action ABI 测试使用独立
Task20/Run11 fixture，当前 Signal/来源接口测试使用实际 current schema，不混用两者。
TaskAgent 按实际生成任务的 runtime attempt、session 和记录范围检查完整原生输入；
定位器验证 CLI 原始行 hash、JSON leaf、字符范围、完整 UTF-8 hash／字节数。
单独直接注入的文本仅在明确选择 `plain_utf8` 时按该格式读取；通常来源使用
`json_string_literal_utf8`。不扩大 prompt，不写 trajectory 副本，不把 DB 正文写回 CLI。
只有实际原生查无后才读取准确上游；当前生成链也用于迁移历史清单之后的新来源版本。
不能证明的新增版本拒绝迁移。仅已授权的二十份历史来源允许原文不可用，保留来源、
校验信息、采用事实及回执；不将这一授权扩大到其他原文。

AI Minutes locator 显式选择 sentence_v1 或历史 paragraph_v1 excerpt 格式，保持原有
前六／后四窗口；没有 excerpts 的 locator 不带该字段。格式／hash 不匹配不换解码器。
AI Minutes 来源通过 UUID 读取准确基础信息，不为重新定位该 UUID 扫描周边日期内的
全部听记分页。基础信息必须对应请求的 UUID，开始时间有效；缺失或身份不同则来源不可用。
随后按 locator 读取行动项及其明确选定的 transcript excerpt／摘要组件，并核对原始正文版本。
听记列表分页只用于发现与同步，不参与已知 UUID 的来源回读。列表 `all` 是提供方可访问视图，
不宣称覆盖 `mine + shared` 的全部记录。
Worker、语义服务和 Web 在域写事务之外使用同一准确来源读取机制；Console 的已有
业务来源可从链接事件事实取得，不建立原文缓存。

Emails 把原 classification 与 message 存成一行，公开 ID 仍各自独立；账号、稳定
身份、UIDVALIDITY／UID 共享。folder、sender、subject、received_at、preview 只由
现有 message 列持有，分类公开投影从同一 owner 读取；没有双写或旧列回退。
两个 RFC Message-ID、thread、创建／更新时间、分类 model_text 与原文
message_normalized_text 仍独立。message-only 行合法；没有 message 的分类插入
及分类仍在时删除 message 保持明确拒绝。删除分类只清空分类事实，保留 message
元数据。配对 folder CAS 在一列上检查原两侧条件，只写一个新 folder，保留两个时钟。
单次完整原文引用内联在 `message_source_reference_json`：原生／provider 定位、实际
序列化形式、完整 hash 和字节数。显式退休及正常完成回调只有在 processed、没有
未完成分类／回复／邮箱动作／dispatch 输入依赖且完整来源相等时清空原文，CAS 再核对
原文、状态、时钟和当前计划；未知来源保留原文。模型输入另有生命周期，尚未整体退休。
分类、最后一个确定邮箱动作及完成的审查／退订任务在结果和回执提交后尝试本行退休；
共享 LOW-priority reader 复用唯一账号 connector，动作执行的 lease 已释放才读取。
历史同步和恢复路径使用同一回调，不新增周期历史扫描。
Console 与执行上下文从准确来源读取完整消息并验证引用版本；body-only 原生字段
不能冒充 To／Cc，角色标头取真实邮件来源。模型输入、全文搜索及历史批量退休仍按
主验收清单列明差项，未把局部来源成功当作全量完成。
Derek于2026-10-19批准每封邮件保留最多1000个Unicode字符的纯文本预览。Email53 的 classified 与 message-only 行均只填现有 message_preview，DDL 约束字符上限；class_preview 物理列退休。扫描及两种正文退休入口写入同一 owner；Email52→53 只移动已存的有界预览，原文已退休时不重建。完整正文和模型输入的hash／来源、非终态执行与恢复输入不变，预览不会成为完整模型输入或完整正文。
Console默认列表及搜索只读取本地发件人、主题和预览，使用Unicode casefold连续子串，保留原过滤／total／顺序／分页；列表返回preview，不将片段作为message_text。界面标明范围，普通输入默认预览搜索，显式“全文检索”发送search_scope=full并显示加载状态；不新增后台job、轮询或进度百分比。详情及显式全文查询仍从准确来源读取完整消息，来源失败明确报错。
Console 全文成员判定按固定50条候选分批、批内按账号读取原始来源，独立于页面limit；这与native exact IMAP的50条上限一致，避免小页面退化成逐封来源会话。匹配仍为Unicode casefold连续子串，准确total／顺序／offset／limit及来源变化检查不变；原文只在请求内使用。会话数下降不等于全库来源延迟验收通过。

两类 Email history 已并入 Actions，保留原二十四字段、公开 TEXT key、可空 key 的
隐藏 ROWID 和原六个 API 的去重／processing-only 更新合同。Provider_state 存账号
范围内的 observation 与 folder generation 两种当前事实，保留十二个源字段和复合
身份；generation 观察时间覆盖逻辑不变，不把每封邮件的事实当作账号配置删除。

Configs 存十五种已有配置，Schedules 保留真实十七字段；两种 Skill receipt 的
loaded_json 从已有 binding／revision 重建，不再存第二份正文。配置转换保留六条
AUTO 水位、signed ROWID、循环 FK 和 native SQLITE_FULL 的事务回滚语义。
Runs 中 scheduled_task_id 和 snapshot_id 只改向真实 Schedules／Configs 公共别名，
其他运行字段、物理 ID、索引／trigger 和水位保留。旧 account blockers、非规范／BLOB
load body、late binding 和外部依赖按明确源声明预检，不能由未证明的重建冒充成功。
State 合并十五种保留状态；延期／恢复回执使用既有 service_state 名字空间，
保留 request_id 幂等、原资格、十九字段和当前回执。已取消 Workbench confirmation
不再有表／API／UI，原生 CLI approval 不变；迁移按已批准的空来源合同检查和恢复声明。

配对迁移捕获精确 actual/fresh 源声明及必要水位，在调用方拥有的 FK-off 事务内转换；
保留既有 source-profile、外部 FK/view/trigger 和 TEMP shadow 检查。大型重建使用
事务内 SQLite TEMP staging 和 SQL 值／类型核对，不归档 Python 行快照。
Email canonical 阶段还精确识别已发布原生空库的 `fresh_published` 声明组合：
classification 使用既有 actual 声明，message 使用既有 fresh 声明。该组合由真实
旧构造器及原 Actions 转换读回确认，按完整声明 hash 识别；不做 SQL 格式归一化，
未知组合仍拒绝。逆迁保存并恢复本次实际捕获的完整声明。
Tasks 在 State 之后转换，Config FK 翻译先于 Runs 扩展；inverse 按依赖逆序从当前
事实重建。原来源表只在转换事务中暂存，最终 schema predicate 不允许旧容器残留。

Child 在事务内目标校验之后、COMMIT 之前 flush 全套十个逆迁元数据组件；Parent
仅接受完整集合，子进程异常／超时仍保留完整逆迁信息并传播原始失败，截断传输不作为
部分成功。既有 child deadline 为九百秒，包含转换、校验、正常冷启动和一次明确的
终态会议输入压缩／最新 selected baseline 准备；不增加运行时格式回退或自动重试。
元数据不包含原文。Schema inverse 在写 lease 之前准备 Email 原文，已有 verified
backup 只贡献 hash／字节匹配的原文字节，新增／更新版本从自己的精确来源取得；
进入 inverse 再验证当前引用，保留迁移后新增邮件、业务结果和回执，不还原旧业务行。
明确没有原文和必要恢复来源时，旧 schema 重建失败而非伪造正文。

The local History query candidate projects source identity and sort time from
each selected source before the combined page sort, applies existing filters
within those source branches, and hydrates only the selected page in one read
snapshot. Reply Attempt recovery and supersession checks share the maximum
terminal public Attempt ID per trigger with their original status rules. This is a read-path
change only. Task6 verified whole-copy plain, matching and no-match public
History reads and isolated read-only UI clicks; this candidate is not deployed.
The aggregate evidence is in the Email-actions native-copy acceptance JSON.

The historical Follow-up/Todo-sync Tasks 1A/1B scaffold defines nine source
families and their original 150 fields. Its explicit opt-in declaration remains
an input to the selected final compact Actions conversion; it is not a second
runtime layout. The send-attempt draft edge uses its real physical Action FK
and projects the public draft ID; Business parent edges are translated by the
paired release. Source public-ID streams, caller-owned transactions and
SQLite-lock-held defaults remain. One row's omitted defaults share a statement
clock. Source row constraints precede draft-parent validation: ignored native
NOT NULL/CHECK/UNIQUE rows do not acquire a new FK failure, and unresolved
non-NULL parents still fail their native FK path.


Task Agent's native result schema explains that `task_id` identifies the
existing Task being updated, while `target_task_id` and `identity_proposal`
describe a merge between two distinct persisted Tasks. Scope/content updates
use `update_fields` with no identity merge. A new Task supporting a registered Project
assessment explicitly selects that Project; unresolved Project clues may retain
a null selector. These field descriptions mirror
existing validators; correction limits and lifecycle rules are unchanged.
Diagnosis and regression evidence: `docs/system-health-quality-validation.md`.

Console History counts and orders compact source keys through an inline union,
then hydrates the requested page within one database read snapshot. It has no
page-result cache or background page refresh: page size and newly committed
records are reflected by the current query. Overview Attention computes the
latest public Attempt ID set once per message key; the Attempt queue explicitly
materializes its reused current-row projection once per SQL statement. These
are equivalent read-plan changes with unchanged lifecycle, counts, filtering,
ordering and external actions. Status-dependent History reads compute later
terminal Attempt IDs from one grouped maximum per channel, conversation and
trigger. The status projection looks it up only when needed, so key-only
pagination prunes it. Each chart uses one
read snapshot and reuses its current-task/delivery reads for repeated failed
message keys only within that request. Evidence is in
`docs/console-query-performance.md`.

An OA process enters Attention only while the authenticated scanner still
tracks the exact node as Derek-owned, its current reply task is still `pending`
or `processing`, and that task has had no update for more than one hour. A
completed audited comment or return may leave the OA process pending while the
applicant or another approver acts; that external wait stays in normal queue and
History views but is not a stalled service task. A pending scanner key without
a matched active-task timestamp is excluded because it cannot establish stalled
work. This projection does not change the OA process, task, or provider receipt.

Current Attention suppresses an older failed OA reply Attempt only after a
newer attempt for the same process is linked to its current Audit-approved
candidate and that candidate's `revert_task` execution is `done` with a verified
DWS `REDIRECT_PROCESS` receipt (including the DingTalk success response and
matching process identity). The failed Attempt remains failed in History; an
uncertain, incomplete, mismatched, or non-return receipt leaves it visible.
Suppressing an older Attempt is only an Attention projection and does not
change task state, receipts, or OA execution.

For the same-generation OA return, if read-only reconciliation has already
persisted the exact verified receipt but the Reply Task remains failed as
`external_action_uncertain`, reconciliation closes that stale Task projection
to `done` only while the same candidate and Audit approval remain current and
its sole action remains verified with the matching external receipt. The
original failed Attempt and its provider response stay in History; the return
is not dispatched again.

OA return readback determines event uniqueness from matching `REDIRECT_PROCESS`
records for the exact task, current user, and remark. Unrelated `PROCESS_CC`
copy records do not make a matching redirect ambiguous within the same second.
Multiple matching redirects, mismatched identity or remark, or missing
destination-task evidence still leave the action uncertain. Reconciliation is
read-only and never replays the return.

OA Reply Attempt History status and status filters use the owning task's
current execution generation, matching Attempt detail; OA Attempts without a
resolvable bound run resolve the task by channel, conversation and trigger message
identity. The action field retains the historical operation label. Current
legacy failed Attempt labels are also reconciled to the matching task's current
terminal status, and a failed or blocked Attempt with no owning task retains
its own status. Current unresolved System Execution uncertainty continues to
override task closure; the projection does not modify the Attempt, candidate,
or execution receipt. When a current OA candidate's Audit run and exact current
execution are linked to the Attempt, a verified `done` execution also resolves
the stale `external_action_uncertain` task error for History projection while
the task is still pending, processing or running; History then shows that live
task state without rewriting the stored Attempt or task error. An unresolved
execution remains `uncertain`. Failed-only History selects Reply Attempt keys
with this same status projection and hydrates only the selected Attempt IDs
through it; count, status and displayed rows therefore use the same current
state.

Attempt detail uses the owning Task's current execution generation consistently
for its reviewed result, Audit summary, System Execution, runtime steps and
external-action evidence. When a later generation replaces an older Attempt's
failed processing, the original run, error and source-closure evidence remain
available in a separate historical section. An OA decision is shown from the
reviewed action for the exact process/task, together with its own canonical
action receipt; a terminal Task alone does not prove approval or rejection.
Source-only terminal closure is described in plain language rather than exposing
the stored JSON as the current outcome. This is a read-only display projection.

An operator may reconcile one existing uncertain OA return through the dedicated
History endpoint. It requires the exact current reviewed candidate/review, an
Attempt linked to that Audit run (and matching any OA identity fields already
stored on the Attempt), one existing uncertain action journal, and a current
uncertain execution. The executor reuses the original action identity and runs in
reconciliation-only mode: only the registered OA handler's readback can update
the action ledger and execution projection; the dispatch path is unavailable. The
original provider response is retained in the verified evidence. Inconclusive or
conflicting DWS evidence leaves the execution uncertain. This endpoint is not an
Attempt rerun and does not create a new Consumer or Audit generation.

When live OA evidence proves that the exact source process and reviewed task are
terminal, an unresolved delivery task may be closed as `skipped` with that
source evidence. This closes current actionable Attention without asserting the
message was delivered; its original uncertain Attempt and execution receipt
remain unchanged for History and audit.

任务 Skill 入口只展示名称、用途、读取入口及冻结来源标识。冻结内容指纹取已保存完整正文
的 UTF-8 SHA-256，与 `read_task_skill` 返回的外层内容指纹一致，不是正文内部的 managed
revision hash。Consumer/Audit 使用同一元数据渲染器，完整 Skill 仍按需读取，自定义 override
保持原文。当前预览为旧无分段快照补充明确标记的当前入口；历史视图及已有分段快照保持
实际保存的 Task 输入。任务入口的名称、用途和展示格式不进入稳定会话合同指纹，不因这次精简
重置 session。

Consumer 会话兼容键还绑定本轮实际选中的 Skill 正文：冻结 Skill 使用任务输入保存的完整正文摘要，
非冻结 Skill 使用当前安装源文件的路径及内容摘要。带已选 Skill 的 Consumer 轮次仅复用 Skill 摘要
完全匹配的持久化路由会话；已选 Skill 更新会让后续新轮次开启新会话，防止旧会话中的规则覆盖本轮
输入。冻结轮次的重试仍按原始 Skill 正文计算，不因安装源后来更新而改写旧轮次。没有显式选中 Skill
的 Consumer 会话仍按原连续会话行为处理。旧会话及历史运行保留，Audit 会话规则、审批权限和动作门槛
不变。

Codex Consumer 结果校验失败后的重试，只在同一执行代次和 revision、失败明确可重试且 session 可续用、
并且原生输出 schema 身份与当前 purpose 完全匹配时复用原 session。原生 schema 由 CLI 参数提交，
Developer 只保存其 SHA-256 指纹；旧会话中完整保存的 Pydantic wire schema 仍可精确核验。身份缺失或
不匹配时开启新 session。解析原生指纹时允许后续 Developer 内容以独立的 `## ` 段落出现，拒绝任意尾随文本；
相同无效结果重复失败继续遵守既有新 session 规则和重试上限。

Consumer 收到 Audit 的 return/reject 后，只有原候选、审核、任务代次及 Codex session
精确匹配，原始完整输入指纹和上一候选的原生完成回答仍在同一 transcript 中，且 session 自上一 Consumer turn 后没有
新增内容或 compaction 时，才在该 session 里提交精简修订指令：本轮时间、完整 Audit feedback、
必要的 runtime correction，以及要求返回完整替代候选。原始事实和候选由同一 session 继续承载，
指纹绑定 Developer 指令、稳定 Task 输入、结果契约和图片来源。条件不满足、找不到可信轨迹或
线路不是该 Codex session 时，一律提交完整 Consumer 输入。轨迹尾部行数、marker 与实际输入
解析共用同一个已解析的 Codex home，避免不同目录中的同名 session 交叉验证。本轮反馈、时间和
必要修正每轮提交；实际提交输入及 prompt snapshot 记录所选版本。精简许可仅用于首次提交；
若该次原生调用失败，同一运行的后续重试提交完整输入，因为失败也可能改写或压缩 session。
此优化不改变 Audit 独立审核、
反馈预算、候选校验或任务状态。

人工重跑 OA Attempt 会创建新执行代际，并从当前选中的 OA 定时任务 Skill 重新冻结输入；若无法读取
当前所选 Skill 或对应任务配置，则不排入重跑。原 Attempt、旧代际和旧 Skill 快照保留不变。
人工重跑内部 OA 待办使用持久化任务中的 `oa-pending:` 合成触发 ID；按触发 ID 定位当前任务并恢复其实际 `oa_pending_scan:<process>` 会话。该类 ID 不是钉钉消息 ID，不调用钉钉会话读取或按 ID 读取；若当前任务已不存在，按未找到任务失败。
输入已压缩时，按任务保存的审批 URL、业务身份和定时扫描 run ID 恢复当前 OA 触发及其最新选中 Skill；身份或运行快照无法对应时拒绝排队，不把合成 ID 交给钉钉消息读取。
同一来源 Attempt 和完全相同的审核反馈/修正文案修订，最多允许 3 次人工重跑（即原执行之外 3 次重试）。服务按已追加的不可变 TaskInput 修订记录计数；仍在排队的相同提交按幂等键复用，不增加次数。第 4 次在创建新执行代之前冲突退出，原任务代次和输入不变。来源 Attempt 或审核反馈/修正文案改变即为新的审核修订，独立使用 3 次预算。此计数与任务内自动重试分别管理；权限恢复后的延迟重读仍按每 12 小时一次、最长 72 小时及最多 3 次的既有专用预算执行。

Task 级技术重试使用 `POST /api/console/history/tasks/{task_id}/retry`，无需把内部故障转换成对话回复或 `needs_human`。入口只接受当前 `failed` Reply Task 的最新 Consumer/Audit run 明确标记 `retryable=true` 的失败；`dependency_read_unavailable` 也可显式重试，并在同一事务内清除该会话的旧 runtime sessions，以便新执行代重新读取缺失来源。存在持久化运行效果证据、provider 风险拒绝、活动/旧代或其他未标记可重试的失败都拒绝。重试保留原输入和失败历史，生成新的 `execution_generation`；API 只重新排队，不能写入成功或直接执行外部动作，后续仍由正常 Agent/Audit/效果回执流程决定终态。

Consumer 和 Audit 的只读工具目录提供钉钉邮件邮箱列表、完整会话、单封原文与发送状态读取：
`list_dingtalk_mailboxes`、`read_dingtalk_mail_thread`、`read_dingtalk_mail_message`、
`read_dingtalk_mail_sent_status`。这些接口直接调用现有 DWS 原生只读命令，保留其完整 JSON
正文、附件、分页与状态信息及原始错误；线程使用 conversationId，单封使用 messageId，
发送状态使用 internetMessageId。它们不发送邮件、不增加审核规则，也不开放 Audit 命令执行。

Store19 (`2026-10-13.5`) is published at5691f6c7, observed on2026-10-14
with Email51 and121 physical noninternal tables. Its complete sixteen-type
`actions` declaration matches the actual-no-Memory-FK source variant; physical
`reply_attempts` and `memory_write_events` are absent. Native public IDs,
allocation waters, adopted results and receipts remain in finite action
projections. Existing delivery blockers, recovery predicates, result labels
and receipt policy remain unchanged.

Phase5D's Store18 delivery publication and E's accepted copy/current-fact
inverse/old-code/native-substitute proofs remain separately recorded. E's
first normal deployment timed out without changes; a later independent
deployment succeeded, and this task's second normal invocation observed that
publication. `evals/database_redesign/phase5e_publication_status.v1.json`
retains both receipts. Readonly quick_check passed and the single original
runs-to-tasks FK violation is unchanged. Live UI/performance acceptance is
still incomplete: Status eventually rendered but exposed degraded recovery
health; Status/Attention/History request timeouts remain under investigation.
This publication does not complete the full10+1 redesign.

Installed Skill discovery (`agent_cli.read_skill()` without a source) returns
body-free TSV names and paths with separate Root and Relative path columns. Joining
the referenced root directory with the relative component reconstructs the
absolute source path; no combined alias expression is presented as a read argument.
The full name and reconstructed absolute read path of every authorized source are retained,
including distinct paths sharing a name. The response includes its path joining
and table quoting instructions. Named/path reads, source authorization and
ambiguity checks are unchanged; no output budget or role policy is changed.
When an Agent needs another Skill, the Task points to
`agent_cli.read_skill(query=...)`. This returns complete name, description and path metadata
fields for entries whose name or description contains every supplied topic
term, in any order and without case sensitivity, without returning bodies. An
empty query is rejected;
`read_skill()` still lists every authorized source when the topic is unknown,
without repeating all descriptions. A topic query supplies those descriptions.
The Agent selects an exact name or path for the subsequent full Skill read.

Email unsubscribe page judgement explicitly selects an empty task Skill catalog
through the existing native per-path Skill configuration. Unspecified standard
turns retain the native default catalog, while an explicit selection (including
empty) filters the catalog through the existing per-path helper. Existing
always-on/plugin entries may remain visible; empty does not mean zero native Skills.
The page-judgement Codex command also sets `project_doc_max_bytes=0`, omitting
the unrelated checkout `AGENTS.md` project document from this no-file task.
The page states, verbatim evidence contract, route, approval, sandbox and
unsubscribe execution rules are unchanged. Native global memory and AGENTS
instructions remain CLI-owned and are not represented as service prompt text.
A conditional consequence such as “By unsubscribing, you will no longer
receive…” does not establish completion and cannot override `action_required`.
Explicit completion and existing unconditioned tracker-host receipt wording
remain terminal. See `docs/email-unsubscribe-conditional-copy-validation.md`.

Console manual rerun loads only the terminal Agent run metadata used by the
existing refusal check. It does not discover sessions or read/parse native
trajectory/result files before queueing. A `skipped` OA review can be
reopened only when the reviewer supplied new explicit feedback, the same
DingTalk OA task remains pending, its process/task identity still matches, and
the prior review has no recorded external effect. The new generation goes
through the ordinary Consumer and Audit review. A missing legacy `resolved_at`
timestamp does not by itself block this path; task identity and no-effect checks
remain mandatory. This does not reopen `needs_human`
or other skipped work. Refusal behavior, original-input verification and the
appended execution generation remain unchanged.

For a scheduled Agent Attempt, explicit History rerun preserves the original
occurrence date, prompt, route, workspace, runtime options and run identity, but
creates a new input with the Skills currently selected on that schedule. The
original run and input remain immutable, and automatic retries stay pinned to
the original Skill snapshot. Rerun attempts are counted against the exact
scheduled input digest: the three-retry cap remains for identical input, while
a changed selected-Skill snapshot is a new input revision. Missing or
unresolvable occurrence/task/Skill data rejects the rerun. Consumer, independent
Audit, send authorization and receipt checks are unchanged.

Empty dispatcher polls are read-only across all queue adapters. A candidate
probe does not reserve an item: the existing writer transaction rechecks the
candidate before changing its source and lease ledger atomically. Todo outbox
probes include expired running rows requiring the existing reconciliation
transition. Candidate order, live-owner protection and claim generations retain
their existing rules.

Agent run metadata writes (claim, lease renewal, session binding and expired-run
failure) capture their row and native attempt references in the transaction,
then commit and close SQLite before native session discovery, transcript reading
or parsing. Return values retain that written metadata snapshot and the existing
native event/result projection. File I/O never extends these writer leases.

Consumer 和只读 Audit 可通过精确节点读取 DingTalk Drive 附件。PDF 下载上限为 64 MiB，其他附件为 20 MiB；扫描 PDF 超过 700 KiB 的内联 MCP 输入上限时，工具返回原始一基页码，之后按页渲染为受限 JPEG，由 `ai_services.ocr_document` 读取。源文件仅在当前 MCP 进程临时目录中保留，技术读取或 OCR 失败继续保持真实技术故障。
Codex Consumer/Audit 的角色工具清单只开放 `ai_services.ocr_document` 与 `ai_services.transcribe_audio`（微信语音转写）两项 AI Services 工具；其他 AI Services 工具不随角色注入。普通 Consumer 和非 OA Audit 仍把 `mcp__agent_cli`、`mcp__ai_services` 列为 direct-only 命名空间。OA Consumer 与 OA Audit 将两者交给同一个 CodeMode JS 调用；OA Audit 的其他 MCP 保持 direct-only，内建命名空间不进入该调用：附件读取结果中的 base64 在工具内存中直接传给 OCR，模型只接收 OCR 文本与来源信息。只声明 direct-only 工具并不能避免原生运行时截断超长附件输出；工具审批与业务动作边界不因此改变。

本文档默认描述当前 Consumer Agent A / Audit Agent B 运行架构；明确标注为“已批准的
生命周期政策”的段落描述后续实现必须达到的目标，不表示对应代码已经切换、部署或在生产
启用。历史方案保留在 `docs/superpowers/` 中，仅用于追溯，不代表当前目标运行方式。

### Historical Task/Run Phase 2 candidate notes (checkpoint through 2026-10-24; superseded)

The following phase-by-phase implementation and validation notes preserve their original evidence. Statements that a constructor was unactivated, production deployment had not occurred, or acceptance gates remained open describe that historical checkpoint only; consult the current Task/Run contract above and the progress table at the top of `business-model-execution-progress.md` for current status.


Task1 is complete at the independently reviewed local schema-contract scope, including later SQL artifact and Run parity repairs (`progress.md:92`); the candidate remains unactivated. Accepted bounded Task owners now include the earlier Reply queue/input slice and later WorkSummary, Meeting, OKR, weekly, WeChat, Email-classifier, and Workbench parent/attachment paths. Accepted Run work includes the professional Run/RuntimeAttempt owner, adoption/receipt and converter handoffs; Workbench Turn/event/artifact handling (Task2D7B/J) and Feedback reference conversion (Task2D8R) are accepted at bounded scope through `2980895e1`. Task2D8A implements native Feedback batch/item creation, batch/item/round reads, and atomic claim. Task2D8B moves association, evidence, typed iteration decision, resolve, reopen and reached processing-lineage validators to actual native Task and Feedback owners. Task2D8C moves event intake, public feedback list/by-key reads and pending counts to native Task, Feedback, Reply Attempt and Run owners. Item status and lifecycle timestamps live on the Task; current association/evidence live on the Feedback item; historical rounds, decisions and transitions remain on their domain owners. Scope receipt conversion maps only decision_id and Workbench task/turn leaves under evidence.associations, preserving independent attempt/run numbers, other JSON and current native edits through inverse/reforward. Receipt policy, legacy receipt versions, capability checks and atomic Task/detail/round/batch/Action/event updates are unchanged. The candidate remains unactivated. Full browser UI acceptance and complete Task2/3 caller cutover remain outside these bounded slices. No Phase 2 production write, integration into `main`, push or deployment occurred. The only fresh production observation recorded for this phase is the read-only weekly-count query at `2026-10-23T13:46:56Z`, which found no active weekly Tasks or attempts; it is not a full current production readback or release snapshot. Adjacent Phase 1 values remain dated observations.

Retained Feedback decision-item Actions reference native decision IDs, feedback keys and round IDs through their typed domain-table foreign keys. Native creation requires no Task/Run migration identity-map rows; the source Task-discriminator guards are excluded from the unactivated target assembly. This changes only the candidate schema contract and does not activate Feedback runtime callers. The target `feedback_processing_items.batch_id` remains required TEXT and keeps its batch lookup index, but has no batch-parent FK because the source has none; empty and source-valid unresolved batch values survive Task2B conversion without NULL normalization or reparenting.

Task2D8R completes these explicit migration references across the current Task/Run/adoption converters. Feedback decision, item and round Workbench Task/Turn references use the same typed Workbench public-owner handoff; item `current_round_id` follows the typed Feedback round public identity, with NULL, zero and unresolved captured values retained. Adopted Feedback transition Actions translate only the two Workbench string members in the stored Feedback payload through that handoff. Sparse inverse markers preserve unresolved numeric-looking strings that could otherwise collide with a canonical native ID. Current-fact inverse applies a captured unresolved Feedback reference marker only while the native field still equals that captured value. Current native edits and claim resets therefore survive inverse; untouched numeric-looking TEXT exceptions still restore byte-for-byte. When an edit is lexically equal to the captured marker, the converter cannot distinguish it from an unchanged fact and preserves the source marker; no stronger inference is made. Existing decision-item Action decision/round scalar mapping remains the single mapping path. `attempt_id` and `agent_run_id` remain independent caller facts. Task2D8A's bounded native batch/item/claim and reached processing-lineage paths use the actual native owners but remain unactivated pending whole-phase review and cutover.

Task2D8C moves feedback event intake, user-feedback list/by-key reads and pending counts to the native owners. An event upsert creates one `feedback_processing` Task and its Feedback detail in the same transaction only when the detail is absent; repeated intake updates the existing source event under its original conflict rules and advances Task.updated_at. The `resolved_at` value is copied with its SQLite value and storage class intact. A new item's detail value follows the source `coalesce(resolved_at, '')`, independently of status, so blank TEXT, whitespace-only TEXT and empty BLOB values remain unchanged in the detail. Source status classification keeps SQLite `trim(resolved_at) <> ''` semantics: a nonblank value resolves the Task; a blank value initializes it as `pending`. On repeat, a nonblank value resolves the Task and updates the detail, while a blank value leaves the existing Task status and detail resolution timestamp unchanged. A newly created Task starts as `pending` or source-projected `resolved`, with status and lifecycle timestamps owned by `tasks`; association and evidence remain owned by `feedback_processing_items`. Association, evidence and round history remain unchanged on repeat. User-feedback reply context still selects the latest sent-token attempt or exact manual-key attempt from `REPLY_ATTEMPT_COMMON_SQL`; role comes from native `runs`, while `codex_session_id` remains the ReplyAttempt's existing source context. Processing association `attempt_id` and `agent_run_id` remain separate values. Registered API list, detail, batch and count paths use these methods. This bounded candidate remains unactivated; browser UI acceptance and complete Task2/3 cutover remain open.

Task2D9 moves OA notification event intake, adoption, reminder listing and attempt-fenced claim/sent/failure writes directly to the existing `oa_notification_events` owner. Its `task_id` remains opaque provider text; reminder attempt/result IDs remain source reply-attempt facts. Email historical-page enqueue, candidate selection and state updates use the existing `email_historical_candidates` owner keyed by account, folder, model and stable message identity. Page refresh updates only normalized-input hash, provider projection, UID metadata and refresh time; it retains retry state and timestamps. Cursor updates stay atomic with the page, and selection remains read-only with the existing deferred quota and ordering. No new send, retry rule or Task/Run fact is introduced. Task2D9 is a bounded local candidate pending independent review; constructor activation and broader Task/Run cutover remain open.

The Email historical-candidate locator refinement is a separate Email 53→54 release. The scalar `account_id`, `folder`, `stable_message_identity`, `uidvalidity`, and `uid` columns own locator values when their JSON counterparts have the exact same type and value; `provider_message_json` keeps `messageId`, `threadId`, and any differing or otherwise additional fields. The list projection reconstructs the provider locator from those scalar owners. The explicit inverse restores removed matching values from the same row. During deployment drain only, the quiet-window selector accepts the exact current Store/Email53 source so the release can safely reach migration, and deploy progress storage recognizes Email54 immediately after conversion; ordinary runtime selectors remain strict Email54. See [Email phase audit](business-model-email-phase3-audit.md) for measured logical JSON savings and the validation boundary.

The three Task parameter identity indexes for Meeting, OKR review and weekly analysis preserve the source TEXT-affinity keys. Their SQL expression decodes a tagged native BLOB parameter to a BLOB key, while an ordinary TEXT value that resembles the codec marker remains TEXT. The source writer has already applied TEXT affinity to numeric and infinity scalar inputs before migration. The expression keeps the original composite components, partial predicates, NULL behavior and duplicate rejection; no Task runtime lookup caller has cut over.

Task2B inverse metadata also records exact compressed per-kind source physical-ID ranges. This migration-only membership fact distinguishes an existing source owner from a new native owner whose reused ROWID falls below the source high-water mark; counts and high-water marks alone cannot establish that identity. The ranges carry no Task payload or lifecycle values and are not read by native runtime callers.

Task2B's source current/claimed input pointer rebind now updates the remapped native Reply Task ID selected by the typed identity handoff. The prior physical source ID could miss the target row after a canonical Task remap, leaving old input public IDs in current/claimed pointers and causing the paired Task inverse CHECK to fail. Current/claimed version values remain untouched. The regression uses the real Task source with Reply Task 201 remapped to native ID 1201 and verifies current/claimed inputs 205/206 through inverse. This is a local migration-helper correction; native runtime activation remains outside this phase.

Task2D10B moves settled Reply input compaction to the assembled native Task, TaskInput, Run, RuntimeAttempt and Task-owned lease fields while preserving the existing done/skipped, version, in-flight delivery, failed-run settlement and unresolved-Attempt fences. It clears body copies transactionally and keeps input identities and version pointers. Generated `reply_task_id` and `matching_input_id` leaves inside ReplyAttempt trigger-text provenance follow the typed Task and TaskInput public/native maps in both adoption directions; unresolved positive integer leaves carry only an Action ID, field name, type and value so an unknown source value cannot be mistaken for a colliding canonical ID during inverse. Other provenance values, unknown JSON members, and typed BLOB provenance retain their source values. The composed migration test includes source Task 201 remapped to 1201, source Input public ID 5 mapped to native Input 205, collision values 1201/205, and a Task/Input created natively after forward conversion, then reads source-public references after inverse and a second forward cycle. This remains an unactivated migration/runtime candidate; no constructor, provider, release or deployment behavior changes here.

Task2D10C moves reached Reply completion/failure/skip, stale-Run requeue, manual rerun and source-revision rerun, run-finalization, memory-write enqueue, and terminal reconciliation paths onto native Task/TaskInput/Run/RuntimeAttempt/adopted-result/Action/Receipt owners. Current business keys come from the current TaskInput; Task status changes use generation/status CAS, and existing input version, claimed-input, latest current Run, retryable failure, receipt, no-action, confirmation-required, needs-human, and superseded-object predicates remain in force. Stale-Run requeue fails only stale Consumer/Audit Runs of the current generation without professional Run provenance, appends native RunFailure facts, and requeues the same Task generation with its existing `force_new_decision` parameter. Task-memory-write events remain idempotent by Task and execution generation. The constructor remains unactivated. Public Email unsubscribe receipt reconciliation remains behind the whole-current-manifest availability gate, and shared History/Attention/WeChat readers and full-copy/cold-process acceptance remain separate gates; see `task-reply-terminal-closure-report.md` for exact method and evidence boundaries.

Task2D10D moves `replace_pending_single_chat_reply_task_trigger` to native Task, TaskInput and TaskBusinessKey owners for the DingTalk worker and WeChat producer. It preserves the newest eligible pending single-chat selection, exact unchanged comparison, generation CAS, running Run supersession, stable duplicate Input/key identities, transaction-timestamped new BusinessKey rows, current-versus-claimed input ownership, the explicit post-merge input-version count, and duplicate Task retirement in one transaction. A retained Run reference that prevents duplicate Task deletion aborts the complete transaction. `clear_agent_run_session` matches native Run owner fields and returns the count of every exact matching Run, including an already-empty session, through Run CAS. WeChat review reads and writes delivery Actions by their public numeric Action identity, binds the exact Audit/Consumer Run parent chain, and uses the existing ready-to-send status predicate; reject propagation obtains conversation/message facts from the current native TaskInput. The candidate remains unactivated. Shared History/Attention, broader WeChat delivery/list reads, full-copy acceptance and the current-manifest publication unit remain open.

The Task/Run paired publication selectors retain the exact frozen Store `2026-10-21.1` / Email53 source digest. The current source target is Store `2026-10-23.1` / Email54, with Email54 applying the separate historical-candidate locator refinement above. Production Email54 was deployed at `a4154c03b` on 2026-10-24; the schema manifest and SQLite quick check pass, and API health, queues, Attention and History returned HTTP 200. The production file remains 514,969,600 bytes because it was not VACUUMed; the measured 182,907-byte reduction is logical JSON payload only. Runtime readback and mutation paths in the shared Store, dashboard, quality gate, explicit delivery-repair and rule-question-retirement commands, deployment quiescence check, and standalone helpers use native Task, Run, RuntimeAttempt, scheduled occurrence, adopted-candidate, Action receipt/delivery and Email Action owners. Deployment quiescence selects the frozen predecessor Task projection before publication and native Task predicates after publication; ordinary runtime selectors do not probe or fall back to source IDs. The adoption converter maps only known `errors` references with WorkSummary/task-agent ownership through the typed WorkSummary Task identity and restores those references through the typed source owner on inverse. The broader Task/Run candidate remains distinct from this Email54 deployment.

When RepositoryUpdater supplies its verified predeployment backup for this paired release, the release records the actual content-free portable inverse handoff beside the active database before the schema commit. Routine SQLite backup rotation therefore retains the handoff needed to invert current facts even when it retires the original backup. The explicit `scripts/inverse_task_run_entity.py` command applies the inverse from a separate process and removes the handoff only after a successful commit. These recovery semantics remain unactivated until final acceptance and release.

Task3C1B refines the local, unactivated Store slice over assembled Task/Run DDL. Candidate IDs are Consumer Run IDs. Each adopted Review has its own source-compatible `id` business/protocol key and a unique `audit_run_id` Run owner; candidate `predecessor_review_id` references the Review key. Progress stores the actual Selection business ID. Immediate Review/Selection existence FKs remain alongside deferred composite Consumer/approved-Audit and Selection/Consumer/approved-Audit ownership FKs, preserving source commit timing and rejecting a cross-Review pair at commit. Candidate and review JSON/digests remain immutable. This does not activate the native Store constructor, execution/receipt writers, source-copy converter, or publication cutover; it performs no migration or deployment.

Task3C1C keeps the native adopted-candidate getters as historical reads: they return the stored adoption and its original Consumer Run generation even after the Task advances or the candidate is invalidated. The shared native lookup is neutral; mutation context still checks current Task generation and candidate validity, while `current_reviewed_candidate` continues to project only the current actionable approval. Missing candidates still return `None`. This restores the source getter contract without changing action eligibility or activating the native Store.

Task3C2A adds a caller-transaction forward converter for current Action adoption rows, receipt-level execution references, and observer/retained-Action Run and Task references. It validates the exact current Action and child catalogs, captures source rows in TEMP tables before Task2B/Run3B conversion, then maps references through those converters' typed identity metadata, including candidate, observer and typed Feedback references plus JSONB `action_data.agent_run_id`. Sparse Task identity rows remain valid when exact Task source ranges and native Task kinds prove identity; native integer existence alone never supplies a typed Feedback owner. The assembled Actions target retires generic `task_id`; a populated retained source value fails before rebuild rather than being dropped. Null and integer-zero weak Run sentinels are preserved; unresolved positive references fail. Any captured Actions sqlite_sequence row is restored exactly after retired rows are removed. Actual-source tests cover Task and Run canonical remaps, nonempty option binding, two Consumer/Audit owner pairs, receipt-level ownership, sparse Feedback identity and physical/public/native collision rejection, catalog rejection, and caller rollback. The converter is unactivated; paired current-fact inverse work remains before constructor or runtime cutover.

Task3C2B1 adds an unactivated, content-free identity planner for the adoption current-fact inverse. After Task2B then Run3B inverse, it resolves current Candidate, Review, Selection, Supplement and execution-alias owners from native rows and the actual Run handoff; retired mappings remain retired, and a Review's identity includes its Audit Run owner. It preserves raw source public-key values and SQLite types, while Candidate/alias next keys follow their per-kind `CAST(... AS INTEGER)` namespace. The global Action physical key follows ordinary `INTEGER PRIMARY KEY` ROWID allocation because the selected Actions catalog has no AUTOINCREMENT; retained current Actions win collisions and reconstructed adoption rows receive fresh physical IDs. Sequence facts are reported unchanged, including absence versus a stray captured row. This planner does not write adoption bodies, perform the inverse materialization, change runtime constructors, or establish old-code cold-open/reforward behavior; those remain separate C2B work.

Task3C2B2A adds an unactivated, read-only row translator for retained current Actions after Task2B and Run3B inverse. It validates the captured source Actions catalog and exact assembled native Actions catalog, returns source-shaped stored-column dictionaries, and fills the eight retired adoption-only columns with NULL. It reverses only the retained typed Run, Task, Feedback decision/round and JSON `action_data.agent_run_id` references through the actual inverse owner maps; weak Run NULL/zero values and all other stored values, payloads and Action IDs are preserved. The helper does not write storage or rebuild DDL.

Task3C2B2B adds an unactivated in-memory builder for the five actual source adopted Action kinds after the Task and Run inverse handoffs. It reuses the adoption identity plan, builds rows from current native adopted facts and execution progress, and resolves source physical Action and Run references through the plan. Candidate/review/selection/supplement business JSON and timestamps remain current native values; execution progress is paired with its source alias, and retired current children are not recreated. It does not write Actions or sequences, restore receipts/observers, rebuild DDL, activate a constructor, or establish full foreign-key closure; the source-catalog oracle covers only Actions constraints. Task3C2B3 validates a bounded two-cycle old-DAO cold write and reforward; full database-copy and runtime activation gates remain pending.

Task3C progress-selection parity stores the selected source Selection's actual business ID in `run_execution_progress.selection_id`. Immediate existence FKs still require the approved Audit Review and Selection to exist; deferred composite FKs require the Selection's Consumer and Audit owner to match the progress row's Consumer and approved Audit at commit. Current inverse resolves the Selection by that exact native ID, and the candidate execution reader joins on it. This is unactivated schema/converter/reader parity; it adds no persisted field and changes no runtime writer or review policy.

Task3C2B3's bounded offline validator exercises the actual published predecessor's candidate DAO reads and supplement writer after current-fact inverse, database close/reopen, and two JSON-only metadata/handoff forward cycles. It also carries a new native Audit Review and Run, a supplement retirement/addition, and a current Action collision with independent provider receipt lineage through both cycles. The validator is unactivated conversion evidence; runtime constructors and provider behavior are unchanged.

Task3E2 adds a native professional Run CRUD surface using canonical Task and Run IDs. Task family is inferred from `tasks.kind`; family provenance lives in `run_provenance`, Task Agent projection facts in `run_projections`, and invocation references follow `runtime_attempts.parent_id` where D1 proved the link. The Codex-session Meeting history reader gets its job identity from native `runs.task_id`; optional provenance `job_id` is not the Task identity source. The native tests preserve Task Agent NULL transcript fields, Meeting source status/session/range/error, and OKR's absent lifecycle fields; historical OKR references do not gain a fabricated Run parent. Task2D1 subsequently moves the WorkSummary producer, its History/recovery readers, and WorkSummary-specific project repair reads onto native IDs. Meeting/OKR producers and History readers, Meeting Task detail hydration, generic dispatcher/runtime writer activation, CLI behavior, and release verification remain open. No constructor or production runtime activation occurred. See `task-3e2-professional-run-runtime-report.md` and `task-2d1-work-summary-runtime-report.md` for exact coverage and evidence.

Task3D1 extends the unactivated `runtime_attempts.parent_id` contract to permit an optional Run FK for any non-`agent_run` workload. The actual forward conversion assigns this owner only when the source workload key resolves through the typed Task Agent or Meeting Run map: plain/`decision_repair.N` Task keys and Meeting keys. Task backfill business keys and other operation keys remain unowned. The inverse verifies that each native Task/Meeting key still has its exact derivable Run owner and refuses mismatched, missing, or otherwise unrepresentable non-Agent parent facts before changing the schema; it then restores source non-Agent `parent_id` as NULL and returns the exact source public identity in `workload_key`. Two portable inverse/reforward cycles preserve the relationship after Run ID remapping. Operation-attempt leases and the active-lease index follow `workload_kind<>'agent_run'`, independently of parent ownership; tuple/ordinal and active-route keys remain unchanged. The target DDL intentionally does not impose extra workload/Run-provenance policy on non-Agent parent links. The JSON Run handoff also carries `runtime_attempt_parent_edges`, keyed by source physical attempt and Run IDs. Only a structured attempt whose canonical request Task matches an `okr_review_runs` parent may use this edge; inverse derives it from current native provenance and Task ownership, while forward validates both source endpoints and applies the typed owner map. Other unsupported parent shapes still refuse before DDL. The structured OKR producer remains outside this slice: it creates its professional Run after the validated CLI result, so the subsequent producer cutover must carry that exact returned `attempt_id`. No constructor, runtime caller, producer order, or migration is activated here.

Task2D2A adds a native-only Meeting delivery identity association using the existing `task_business_keys` table. Each source Meeting derives exactly `meeting-alignment:{source public id}:{meeting_id}` and associates that exact string with its canonical Meeting Task, even before an outbound Action exists. The source public ID remains provenance: runtime lookup by canonical Task ID reads the stored association and does not reverse-map or parse old IDs. Existing Action delivery keys, sensitive sibling keys, prepared-target bindings, receipts, and provider UUID inputs remain unchanged. The inverse restores only Reply-owned keys to legacy `business_object_tasks`; Meeting key rows are omitted from the old source schema and carried as `{source_meeting_physical_id, task_business_key_row_id}` in portable `meeting_business_keys` metadata. Reforward rederives key text from source facts and reuses the row ID. Those IDs reserve the old shared Tasks allocator water, and a deleted source Meeting drops its stale association without resurrection. The Meeting creator and delivery loop are unchanged until Task2D2B, and this candidate remains unactivated.

Task2D2B's local Meeting candidate uses canonical Task IDs for creation, request/result reads and writes, analysis/delivery claims, retry, recovery, replay, baseline, ASR attachment, recurring-target queries and Meeting parent checks. Professional Meeting Runs and RuntimeAttempts are read through native Task/Run parents. The producer retains the exact TaskBusinessKey delivery key; prepared Actions, primary/sensitive receipts, target binding and provider UUID inputs stay unchanged. Similar-Meeting lookup reads native Meeting Runs and no longer writes a source-index row; the generic cross-family search reader remains available to its other callers. Meeting Run detail remains inspectable by native Run ID when its Task parent is NULL or points to no Meeting Task: HTML and API show Task unavailable, retain the Run error/result and original `run_provenance.job_id`, and do not look up or synthesize a source Task ID. An absent Run still returns 404. Generic dispatcher, all-family dashboard/History and other cross-family readers still require coordinated cutover. The candidate remains unactivated; see `task-2d2b-meeting-runtime-report.md` for exact implemented methods and remaining dependencies.

That Meeting checkpoint also removed SQL interpolation from two unrelated `AutoReplyStore` queries: managed scheduled-Skill revision validation and stale Reply Run recovery. The f-string prefixes are restored, with SQLite regressions covering invalid cross-Skill revisions and stale-Run terminal failure/event behavior. This repairs those existing query paths without advancing the Reply lifecycle cutover or activating the assembled schema.

Task2D5 cuts the actual WeChat Memory import owner and both routed producers to native Task IDs on the assembled target. `begin_wechat_memory_import_job` creates a new `wechat_import` Task for every invocation, preserving exact TEXT `import_run_id` and `account_id` values as its only parameters and returning `tasks.id`; there is no source job integer alias or dedupe by business attribution. The source `running/completed/failed` lifecycle, error, and timestamps map to Task status/error/created/updated, with terminal `finished_at` in `task_results(result_kind='wechat_import')`. Repeating the same terminal write is idempotent and preserves timestamps; a conflicting terminal rewrite is rejected. Extraction and recall matching route attempts with `memory:wechat_memory_import_job:{canonical_task_id}`. Their RuntimeAttempts retain independent operation leases and ordinals with `parent_id=NULL`; neither a professional Run nor raw result content is synthesized. This local owner slice does not activate constructors, full-copy/inverse behavior, the CLI/UI or production runtime; other Memory workload branches and pending shared family readers retain their current status. See `task-2d5-wechat-import-report.md` for the scoped evidence and remaining dependencies.

Task3E4 moves the actual `AutoReplyStore` RuntimeAttempt lifecycle and its typed Agent readers to `runtime_attempts`. `AgentRuntimeAttempt.parent_id` is the native Run owner; `workload_kind='agent_run'`, rather than parent presence, selects Agent Run lease, terminalization, and result adoption. Task Agent plain/decision-repair workload keys identify the exact running professional Run ID and its `task_agent_runs` provenance; parent lookup does not infer from Task ID, latest Run, role, or execution generation. Structured OKR work is runnable from its processing native Task before CLI execution and begins with a parentless RuntimeAttempt; the later producer must bind the exact returned attempt ID when its validated result creates the professional Run. Task, decision-repair, and Meeting operations retain their own leases, retry ordinals, sessions, and failures when they reference a professional Run; completing or failing an operation does not terminalize that Run. Agent result adoption remains transactional and validates the transient result envelope while persisting only its exact `result_ref`. These local Store/router changes do not activate constructors or the mixed Task families: Email and Workbench producers still require their dedicated native Task cutovers; WeChat import producers have a local native Task owner slice in Task2D5. Reply recovery, shared dashboard/history, and other cross-family readers remain pending. See `task-3e4-runtime-attempt-report.md` for focused implementation and evidence.

Task2D7A moves the Workbench parent and its attachments to their assembled native owners. WorkbenchTask IDs and attachment IDs are canonical decimal strings at the existing API/DTO boundary and native integer primary keys in storage; provider session references remain on the parent. Parent listing uses native integer ID tie ordering, and latest state plus the archive fence read raw queued/running Turn rows through their native parent FK. Attachment `client_request_id` remains its UUID idempotency key. The persisted `storage_path` is the file locator: reconciliation follows that locator under the managed attachment root without deriving ownership from legacy path components, so migrated bytes remain at their existing paths. New attachment writes retain the existing file/metadata transaction ordering. Attachment IDs use AUTOINCREMENT, and explicit inverse identity handoff preserves the attachment allocation highwater through portable inverse/reforward so a reconciled highest ID cannot be reused against a sparse source identity. Workbench Turn lifecycle writers, constructor activation and production selection remain separate pending work; see `task-2d7a-workbench-parent-report.md`.

Task2D7I adds an explicit, migration-only inverse file coordinator for Workbench attachments. It snapshots canonical attachment and parent IDs, locators, hashes and sizes before inverse; stages source-compatible UUID locators from restored source owner rows in the caller's FK-off transaction; and leaves the prior committed file intact until database commit and replacement readback. Its JSON-portable handoff carries only typed source/canonical identity and locator/hash/size facts. Reforward stages the native locator from that handoff and removes the prior source locator only after committed native-row and byte readback. A failed stage cleans only links created by that stage, and callers can remove staged links after rolling back their transaction. This coordinator is explicit and unactivated: it does not change runtime reconciliation, infer owners from paths, or alter the frozen predecessor constructor. The separate operator acceptance invokes that unchanged constructor from an explicitly selected predecessor root.

The composed Task-then-Run inverse consumes the complete current Task inverse identity object through Task's shared validator and digest contract, including Workbench parent/attachment/file identities and allocation highwater. Run reconstruction still uses only the validated typed Task owner map and current restored source rows; malformed or unknown identity fields, digest mismatch, and unresolved/wrong-kind references fail before Run schema replacement. This is converter integration only; it does not select the candidate constructor or activate runtime behavior.

Task3E5 extends the migration-only Run handoff to version 3 with sorted,
digest-bound `runtime_attempt_orphan_workload_edges`. After Task inverse, an
Email RuntimeAttempt whose canonical Task owner is absent and whose parent is
NULL receives a deterministic predecessor-valid `email-classification:` plus
64 lowercase hex operation identity derived from its typed native Task key.
The edge names the exact allocated source physical Attempt ID, operation key,
and native workload key. Forward conversion validates the exact source Attempt,
key grammar, absence of a live Task owner, and edge ordering before replacing
the source declaration, then restores the native unowned key explicitly.
Existing Email Task owners still use typed Task resolution; wrong-kind live
owners and unsupported workload kinds remain errors. Attempt lifecycle and
execution facts are preserved; no Task, business field, runtime alias, or
constructor activation is added. This is an unactivated converter change; see
`task-3e5-email-orphan-handoff-report.md` for implementation and verification
status.

Task2D3 moves the existing OKR review request queue, claim, recovery, terminal updates, item writes and typed Run history read to the canonical OKR Task, Run and RuntimeAttempt owners. A validated CLI result is still checked before creating its professional Run; the producer then binds the exact `attempt_id` returned by the routed structured execution, writes domain items, and marks the Task done. Invalid results create no Run, while their actual completed attempt remains parentless. Binding requires the matching OKR Task, `structured` workload and request Task key, completed attempt, and an OKR Run whose `task_id` is that same Task; it only CASes a NULL parent and never resolves by session, latest attempt, or historical key. Source-absent Task lifecycle fields and OKR Run status/error/role/generation remain NULL. Native OKR Run detail reads resolve the attempt only through that exact parent; orphan Runs remain readable by native Run ID and provenance without inventing a Task or old request ID. This is an unactivated local producer/read path: weekly OKR analysis is a separate Task family, and constructors, shared all-family History/dashboard, provider behavior and production remain outside this change. The Task2D3 writer was based on the original `360dfa1` review baseline with the independently reviewed Task3E4 Fix2 parent-helper correction `30426e7` interleaved. See `task-2d3-okr-review-report.md` for scoped evidence and remaining dependencies.

Task2D4 retires the unused weekly-analysis begin, finish and cache-miss writers and their claim DTO after source caller checks found definitions/tests only. Historical weekly facts remain `okr_analysis` Tasks and `task_results` with the existing `weekly_okr` operation workload identity; the native parent-runnable lookup and the two startup supersession cleanups use Task parameters/lifecycle and `task_results.finished_at`. Cleanup retains the source predicates for expired leases, same-manager later completed weeks, technical-only failures, and the created-at rerun comparison. The configured production read found 183 completed weekly Tasks and 353 terminal weekly attempts, with no active weekly chain. The shared all-family Dashboard queue and dashboard detail resolver still depend on the Task20 source projection and need a later all-family conversion; no partial weekly projection or new detail route is introduced here.



The final shared-reader and publication candidate targets Store `2026-10-23.1` with Email53. The paired explicit release freezes the exact `2026-10-21.1` Store / Email53 source catalog (505 SQLite schema objects; catalog digest `920b7eedb6138439ab94d4fbf109e60258ce8842c212d3a2ee3eec56fb4ca290`), captures Task, Run and adoption sources before mutation, converts in one FK-off transaction, verifies the exact current manifest, and supports current-fact inverse plus re-forward. Task 5's local final-copy acceptance on 2026-10-24 passed the production-derived disposable-copy roundtrip, pinned predecessor cold read/write, native Luna/high Consumer/Audit/revision path, fake-effect process recovery, worker-disabled UI checks and same-source packed net-space gate. The inverse now retains the sparse Task identities/context needed by the next source forward. This is local acceptance, not production activation; whole-branch SPEC/QUALITY, normal main integration and deployment/readback remain required. See `.superpowers/sdd/2026-10-22-task-run-entity-unification/task-5-report.md` for exact evidence and limits. The production database was never modified.

Task-then-Run forward conversion preserves every captured source `sqlite_sequence` rowid, name, and water value. If a newly created native Run owner occupies a captured source rowid, the converter relocates only that owner's technical sequence rowid before restoring source facts; the owner name and AUTOINCREMENT high-water remain intact. The same narrow handling applies during Run inverse restoration, and unrelated sequence rows are never renumbered. The paired Task/Run inverse also writes the sparse Task identity/context returned by the inverse as an adjacent source continuation. The next forward consumes and validates it, preserving Meeting delivery-key IDs across a cold source interval; it contains no source bodies and is replaced by the new current-target inverse handoff on success.


End of historical Task/Run Phase 2 candidate notes. The following sections describe current service contracts unless they identify another dated historical checkpoint.

## 第二批：迁移实施记录与日期化清退日志（原样移出）

## Run 正面声明与显式发布窗口收口（2026-10-28）

Run／采用结果只保留当前正面声明和真实身份／来源维护，不再生成旧字段全集映射。
11项当前receipt SQL直接固定，逐字等于原生成结果，不改写旧execution_id声明。
过期的9个release/source模块、旧copy工具和专用转换测试退出；本批4022验收时保留的显式Feedback
28.2→28.3发布及新增数据inverse有独立证据；后发28.4由另一任务负责。所需历史Configs源SQL只在测试夹具中。
四张identity_map的身份事实、当前coalescer和历史查询不变。当前触发器重解析回归
仍执行两次ALTER及EXPLAIN UPDATE；不因旧测试容器退休而取消当前覆盖。
所有463项SQL与前一当前声明逐字一致，不新增schema版本、运行时兼容或policy。
本段是代码合同；部署／生产功能／清理的实际状态以第五批验收为准。


## Email／Workbench 历史转换退休（2026-10-28）

Email现行投影、元数据、正文来源和业务写入继续使用原有owner；已关闭的旧表转换、
来源变体识别和inverse写入退出。Workbench旧source13转换链路及专用探测退休，
当前文件生命周期、Task/Run父子、事件和回执不变。仅保留实际显式来源工具依赖的
原有声明，普通constructor不调用；测试当前值模板独立放在tests/support。
没有DDL、字段、版本或发送／恢复政策变更，部署状态见第五批验收记录。


## Business 当前写入与导入链路清退（2026-10-28）

当前候选insert/update/delete及WorkTodo导入read/assign继续使用已有Business owner；
关闭的候选／导入owner转换器、旧来源校验及专用final Action转换包退出代码。
候选projection只按当前字段顺序读取，不再接收actual/fresh来源开关；现行生成DDL、
外键、业务事实、引用与水位完全不变。当前测试使用原生Store，SQLite来源DDL只作
独立存储语义oracle，不参与应用构造或兼容读取。实际部署状态见第五批验收记录。


## Decision Runner 当前单一执行链路（2026-10-28）

`CodexDecisionRunner` 只使用已有 `RoutedCodexExecution` 和持久化 Run，构造时传入
Store 或已配置的 routed executor。旧 `executor` 注入、直接 subprocess 和专用命令／
超时恢复分支已经退休；当前原生调用、结果解析、同会话纠正、session/transcript 范围、
审计事件和 `_finalize_decision` 的现有合同不变。测试用 routed/native 替身，不真实外发。
这项无 DDL 清理不改变路线能力、Audit、授权、重试或发送政策；实际部署状态见
[第五批验收](model-retirement-batch5-validation.md)。


## Feedback 单一事实归属 Store28.3/Email54（2026-10-28）

显式Feedback发布及inverse直接读取冻结的28.2/54目录；不再通过更早的会话／索引
迁移包拼出前序SQL。普通constructor仍只使用当前正面声明，不载入历史目录。
本单元不改变任何schema SQL、版本或业务事实。

Item只保留task_id/feedback_key/batch_id/current_round_id四列。九项当前证据从准确Round
（id+feedback_key+batch_id）读取，resolved_at从同一来源反馈Action读取；无Round解决事实
继续可查。创建/领取/关联/patch/解决/重开不双写镜像。移除同源别名间的重复比较，保留
原有Task/来源/指针/批次/状态/类型及外部回执资格；Round写后检查当前指针条件，竞争仍回滚。
没有新审查、授权、重试政策。当前正面声明48表/463对象/1222列，不靠旧字段集相减。

显式28.2→28.3 release先验证源镜像逐值/类型匹配真实owner，再移除；inverse由当前owner
恢复，保留新增事实、身份、当前水位及FK集合。constructor不处理旧结构或自动补建。
已关闭Task/Run/adoption转换包、其专用工具和测试退出；当前微信/审批/Workbench/反馈测试
使用原生夹具，实际identity provenance与必要现行paired inverse保留。候选/安装/读回边界见
[第四批验收](model-retirement-batch4-validation.md)。上述转换半部与Runner已在第五批分单元退休，
发布／读回状态见[第五批验收](model-retirement-batch5-validation.md)。

## 会话唯一归属 Store28.2/Email54（2026-10-28）

正常运行只在`conversation_runtime_sessions`保存按会话和路线的session/hash，
`conversations`只保留元数据。Consumer观测session时统一写入已有任务的普通会话元数据；
session列表/Attempt关联直接读取canonical owner。CLI OAuth reset清除同一owner的OAuth槽，
其他路线保留；原有全路线clear、路线上compare-and-clear与Run/Attempt原生证据不变。
退休两个镜像payload键/生成列及旧getter/setter/clear、双写和无用开关，不保留运行时兼容。
历史af24发布中的显式28.1→28.2先验证镜像非空事实与canonical一致；当时inverse从canonical恢复完整
旧镜像（包括原来为空的hash），不保存镜像备份。463项schema、48表、1232列。历史声明固定，
constructor不自动升级。af24bc485标准部署/读回/清理已完成，证据见[第三批验收](model-retirement-batch3-validation.md)。


## 模型退休第二批：Store28.1 / Email54（2026-10-28）

历史44c13bc91代码声明为Store `2026-10-28.1` / Email54，完整配对463对象（bare462）。
正常constructor只接受空库或精确当前声明，不探测/修补旧结构。显式发布仅支持
准确Store26.1/Email54前序，发布静默读取使用同一冻结来源；25.2/25.3退出自动
发布窗口。必要历史工具使用自己的固定声明，不能随current重新生成旧来源。

模型切换的唯一历史owner是既有registry manifest；生产为空的Configs
email_model_mode_transitions家族、旧DAO及启动扫描退休。promotion config、
category revision和registry实际切换/历史保留。Configs退休2普通/9虚拟列及
3trigger/1FK，Run身份map只退休恒NULL allocator_highwater；全部真实身份行保留。
10个重复索引退出当前声明，保留主键、精确复合FK父键及调度完整领取索引。

显式paired release只重建Configs/Run map并退休指定索引，按原生SQL复制现在的
普通列和hidden rowid，保留所有现场sqlite_sequence rowid/名称/值/类型。inverse
从迁移后当前事实重建旧结构，退休中性列恢复NULL，恢复原版本clock；事务中断
回滚全部DDL与事实。没有业务正文归档或新的运行时兼容分支。已关闭的Configs
和selection转换专用入口/测试作为必要依赖退休，当前功能与固定来源断言保留。
本段描述候选代码合同；实际部署状态见[第二批验收](model-retirement-batch2-validation.md)。

## 模型退休：无 DDL 入口清理（2026-10-28）

当前 Action receipt 写入和 Reviewed 执行只接受 `execution_run_id`，不再通过参数
选择旧 `execution_id` 列；允许字段按当前结构正面声明。显式历史转换器使用自己
冻结的来源 receipt 写入，不让来源结构进入当前业务 helper。
发布静默读取仍严格验证当前及已支持的前一声明，只读取原生 Task/Run/调度 owner，
不存在到旧表的不可达 fallback。发布等待、回滚、CAS、回执及审查政策不变。

无调用入口和仅测试维持的旧 Store/Run/Provider 包装已退出应用；当前功能测试走
现有 dispatcher、native owner 和测试目录中的窄造数夹具。旧 runs11 写入器及其
专用测试退休，实际 session 搜索、性能样本和 setup 事件仍由各自当前 owner 持有。
本批没有 DDL 或业务行变更；会话、反馈和 Configs 字段归一属于后续独立批次。
版本、测试与部署读回见[第一批验收记录](model-retirement-batch1-validation.md)。

## History 慢请求与 SQL 工件验收收口（2026-10-27）

History 可见页面仍按10秒间隔检查刷新；已有列表请求未完成时不发起下一次自动
刷新，避免刷新依赖变更反复取消慢请求。请求成功或失败后恢复自动刷新。用户
修改筛选、翻页或离开页面时仍取消过时请求，旧请求的迟到响应不能覆盖新结果。
这不改变后端查询、结果排序、业务状态、审查或发送合同。

历史验收的Store21.1来源工件采用通知字段加入前的冻结schedules声明，保留原有
505项／920b7eed 摘要；当前 Task/Run SQL 文档则与当前声明同步，包含已有的
notify_on_completion 字段。历史来源与当前目标不互相冒充，不增加运行时兼容。
测试与实际发布读回见[收口验收](history-artifact-closeout-20261027.md)。


## 当前业务读取入口清退（2026-10-26）

历史 Project 详情继续由 `load_project_task_detail` 服务 HTML 和 JSON，保留原有
URL、来源、事实、Todo、Update、跟进和回执；旧的独立文本 renderer 与固定的
检索评分字段已经退出当前模型。无调用的单条历史查询、旧跟进查询和重复的已发送
回复别名已删除，历史数据仍由原有 owner 持有。

Run 落库事实由当前 Run／provenance／projection owner 提供，原生结果由已有
native resolver 按精确身份和范围读取；测试不再依赖另一套专业 Run 聚合读取器。
History 展示测试使用正式 History 入口，纯持久化测试的窄查询仅位于测试目录。
Attention 的读取不改变其业务状态；没有单独的 viewed 写入入口。

Email 计划只使用 `persist_email_plan`；Reviewed 的旧计划写入及旧候选造数支持
不进入当前业务写入器。显式历史迁移测试使用冻结来源夹具。
本轮不改变 DDL、业务事实、审查、授权、重试或回执语义；部署状态与读回证据见
[清退验收](unused-business-path-retirement.md)。

### 第一批追加清退：当前业务入口与 Attempt 读取（2026-10-26）

旧跟进草稿、Todo完成证据的生产写入／状态推进入口和测试专用Store包装器已退出当前
应用；20个方法及RecentFollowUpCandidate重复DTO删除。历史Project／Todo／Update、
跟进尝试、终态同步outbox、完成证据及其来源／决定／回执仍由原有历史owner持有，
现行详情与History继续读取。重放脚本使用现有Signal事务入口；旧源码造数仅在
冻结前版本测试中执行，不构成运行时旧结构兼容。

Attempt会话列表直接读取当前Run，不再构造旧Attempt形状或空工具事件字段；
RuntimeAttempt读取只使用现有批量接口。仍保留历史Attempt自身的真实session和
缺少run关联时沿准确Task读取Run的逻辑；原生范围、可用性、角色和状态合同不变。
本批不改DDL、历史行、审查／授权／重试／发送政策；不退休Project或领取字段。
发布状态、测试范围和清理见[本批验收](business-entrypoint-batch1-cleanup.md)。

### 第二批：固定当前结构声明（2026-10-26）

当前 Actions／Business SQL、Business／Sync 字段和 Action payload 槽位由明确的
当前声明持有。空库构造、当前结构校验、Action 默认写入／投影及 Email 计划、claim、
receipt 写入不再组合历史开关，也不从旧字段全集减去退休项。其余当前 owner 继续
使用原有声明；全部 SQL、字段顺序、索引、触发器和外键保持原样。
模块导入不再预生成历史 DDL；历史来源工厂及逆迁布局只服务显式转换工具，
不参与运行时旧结构兼容。固定声明只按当前账本角色缓存不可变 SQL，调用方取得
独立字典；真实数据库的声明及版本检查保持逐库执行。
Store26.1／Email54 版本及业务、审查、重试和回执合同不变；发布状态与准确证据见
[第二批验收](current-declaration-batch2-validation.md)。

## Unified core run storage (Store 2026-10-11.2)

第四期4A将逻辑 Agent run、provider attempt、服务状态事件存入同一物理 `runs` 表，
`record_type` 区分三个原有记录类型。原有公共ID、workload key、代际、revision、turn、
父子关系和原生 session／transcript 范围保持准确；DAO仍返回原合同。逻辑 run 的物理ID
等于公共ID，迁移后的 provider／状态事件以 `legacy_key` 返回原公共ID；新增记录从捕获
的最大ID／序列以上分配。类型专有列只对所属类型应用原默认值和约束，其他类型为NULL。
现有 Consumer／Audit 唯一性、provider session／lineage／lease 约束及外键的逻辑run
类型范围保留。4A的 `task_id` 仍直接引用原 `reply_tasks`，后续4B才统一任务容器。

旧 `agent_runs`、`agent_runtime_attempts`、`agent_run_state_events` 及已为空的
`agent_run_events` 物理表删除，不创建旧表view或长期双写。`final_result_json`／
`result_envelope_json` 不再作为物理列；准确过程及原生输出继续只从CLI trajectory／
运行中RAM读取，缺少准确原生引用时仍返回不可用。必要服务错误状态、采用的业务结果、
执行回执和外部动作结果继续按原合同保存。

配对发布只支持已验证的Store `2026-10-11.1→2026-10-11.2` 和当前fresh初始化；
发布前quiet读取显式支持本次旧／新源布局，运行中查询统一读取 `runs`。停止窗口捕获
原DDL、索引、触发器、序列、schema marker和既有FK发现；逆迁恢复这些声明并保留
replacement新增的记录、业务结果和回执，不用旧数据库文件覆盖。发布失败沿用
停止replacement、逆迁、恢复旧代码、启动及health验证的既有顺序。Email schema50
本单元不变，其他未验证的同时schema变化不能合并发布。
正迁在第一次结构或行修改前核对实际11.1四张退休源表的19项显式table/index/trigger
声明，保留SQL字面量与内部空白。未知或缺失声明、同列但约束不同的源均拒绝；捕获
原DDL用于逆迁，不意味着接受任意历史变体。其他表既有回执外键及其索引／触发器按
原声明重建，仍保留其数据和行为。

当前Store版本且 `runs` 核心声明准确时，缺少已声明的索引／触发器或其他既有表
仍走原初始化补齐路径，保留原修复行为和业务行。核心声明、索引或触发器定义不匹配，
或旧／新核心表混存时，初始化拒绝该未验证布局；不把它当作fresh库部分建表。
核心schema manifest核对本期实际表声明、索引与触发器，缺失对象不能被标记为current。
声明比较保留SQL字符串字面量大小写，仅去掉两端空白；当前布局的预期声明由同一
DDL helper在一次性内存库中生成。缺失对象可补齐，已有对象定义改变不能被补齐路径掩盖。

4A三个整数源ID由唯一 `(CAST(legacy_key AS INTEGER), record_type)` 索引保留
各自身份范围，显式新key写为原整数的规范字符串。索引先按数字ID排序，按parent或
workload查provider引用时沿用范围索引并在范围内按原public ID排序；不扫描整个
provider历史，不改成物理ID排序。取消重复TEXT唯一索引，没有增加覆盖索引。
此规则限定本期三个实际整数ID类型，后续UUID类型需采用实际TEXT身份约束。

## Unified reply and work-input storage (Store 2026-10-11.3)

第四期4B首单元将回复任务、输入版本与工作汇总输入存入物理 `tasks`，Task Agent 运行存入 `runs`。
逻辑类型使用准确原表名；公共 ID 保留原整数命名空间，`legacy_key` 记录旧 ID。reply task 的公共 ID
等于物理 ID；输入和工作输入通过类型与公共 ID 查找物理记录。`current_input_id`、`claimed_input_id`
与输入的父引用使用物理外键，DTO 继续返回准确原公共输入 ID。Task run 的 `summary_input_id`
仍是原公共工作输入 ID，原来源没有该外键，不新增外键。

保留的输入 TEXT、BLOB 与 WSI1 编码保持原字节。终态正文精简规则、状态与尝试次数、执行代、领取 CAS、
业务结果、原生运行定位和执行回执保持原语义。Task run 的三项已退休空过程缓存不再物理保存；历史正文
依然从原生 trajectory 读取，缺失明确报告不可用。没有新增业务字段、旧表视图或长期双写。

此单元配对发布使用已验证的 `2026-10-11.2→2026-10-11.3`。副本正迁和逆迁比较原记录、源 DDL、
索引、触发器、序列及版本；逆迁保留 replacement 期间的新输入、结果与回执，以及已分配后删除的 ID
水位。部署静默检查明确读取旧12或新13布局，并支持普通元组与 sqlite3.Row 连接。
Meeting、OKR与weekly结构由下面的同版本内部子单元处理；scheduled/Workbench仍待4C。

这是当前开发分支的行为说明，生产上线状态和验收边界见 `docs/database-redesign-phase4b-validation.md`。

旧 `2026-10-10.2→2026-10-11.1` 输入 publication 与其无运行调用的迁移／inverse helper 已退休；当前二次启动不运行旧全局补造逻辑。损坏或混合的当前 typed schema 明确拒绝，既有修复缺失辅助索引的入口保持原边界。回退使用本次捕获的准确源布局和对应旧代码，保留 replacement 新事实。


## Unified Meeting storage (Store 2026-10-11.3)

历史4B会议子单元将原meeting_alignment_jobs的23项源字段保存为tasks[meeting_alignment_jobs]的准确scalar；当时会议运行使用runs[meeting_alignment_runs]（当前C2使用共同agent_runs owner，见本页顶部），只退休三项已为空的过程缓存。已采用的决定、最终消息、发送结果与日历结果仍是业务状态；原生trajectory继续提供过程正文，缺失时明确不可用。源run.updated_at默认空字符串，正常begin/record入口仍显式写SQLite当前时间。

DAO、Dispatcher、runtime父定位与恢复、History、搜索、Console及静默检查保留原公共job/run ID和排序。投递key、Dispatcher source_id、generation、owner/expiry和claim token的原谓词不变；Memory event、delivery claim和run的父FK指向物理tasks ID，DTO回投原公共ID。ready_to_send仍不代表sent；已有发送结果与后续calendar状态保持原业务语义。

已知历史run2298的job4908不存在，迁移保留这一真实缺失关系并消耗保留物理ID水位，不创建业务父记录、不指向其他类型巧合同号行。正常run只保存物理父FK，公共父ID由typed parent还原；只有历史孤儿保留实际source job_id。配对逆迁还原原公共身份与同一FK发现，保留replacement新事实。当前开发代码已完成会议调用切换与独立复核；全B的最终生产源副本、旧代码回退验证、CI和发布仍以B验收文档为准。


## Unified OKR and weekly analysis storage (Store 2026-10-11.3)

4B的完整存储核心将九张源表合入tasks/runs。OKR request保留16个原scalar字段、conversation/trigger自然唯一键和原无枚举CHECK的status。失败请求的重复输入只刷新原允许的source JSON、pending状态、错误、session和更新时间，不改写其原标题或trigger正文。领取、恢复、Dispatcher租约与Console使用原公共request ID；对应任务的物理ID可不同。

OKR run保留原公共request_id、session、transcript range与created_at，只退休三个空过程缓存。源没有request外键或run状态、错误、完成/更新时间；共享列保持NULL，task_id不补成FK。items的request_id同样没有声明FK，保留原public scalar；多个run及原允许的缺失request关系均不合并或补造父行。精确native读取继续使用structured workload的公共request ID和原session/range。

weekly analysis 的历史事实仍以 `okr_analysis` Task、参数自然键、Task lifecycle、`task_results.finished_at` 与 `weekly_okr` RuntimeAttempt 保存。专用 begin/finish/cache-miss writer 与 claim DTO 已退休：源码和受管周报 Skill 没有调用方，配置生产只读快照中的183个周分析任务全部 completed，353条周分析 attempt 全部 terminal。启动时原有两项 supersession cleanup 保留，现按相同的过期租约、同经理更晚成功周、技术失败类别及 created_at 比较规则更新 native Task，并把完成时间写入 TaskResult。 weekly Task.status 在 native owner 中保留来源 running/completed/failed 值；恢复和清理谓词使用这些原值。没有新增周分析 producer；周报全局 lease 与 last_success/last_attempt 仍属于 service_state。该变化不修改 frozen source/inverse oracles。


Email task visibility in Store13: unsubscribe classification lists, classification detail/in-flight progress, verified entry URL lineage and summary queue counts detect the physical tasks/runs tables. They read public task/run IDs through the typed projections and resolve canonical inputs through the physical current_input_id reference. A standalone EmailStore without the shared task tables retains its existing behavior. Existing receipt/lineage/URL validation and redaction rules remain; this change fixes the physical-table presence check and adds no policy.


## Phase4C core storage (Store 2026-10-12.1)

隔离候选的核心 schema 将 scheduled_task_runs 和 workbench_events 存入 runs，
workbench_tasks 和 workbench_turns 存入 tasks；record_type 保留来源身份。Workbench
公共 TEXT ID 按原值保留，内部父引用使用物理整数 ID；原任务/run ID 不重写。
scheduled/Workbench CRUD 与 reader 切换已完成并通过独立规格及质量复核。
生产是否启用本期结构，以本期验收文档中的实际部署读回为准。

构造器支持精确已验证 source13 到本期候选的事务迁移；无标记/未知来源包含任一退休源表
时，在初始化修改之前拒绝。C 源 tasks/runs 的完整对象清单也必须符合已捕获的声明，
不能静默删除额外源触发器或索引。当前 schema 已有的缺失对象修复规则保持不变。

本期只取消全空的 Workbench final_text 与搜索 summary_text、fts_text、embedding_json、
embedding_model、embedding_updated_at；非空或 NULL 值在迁移修改之前拒绝。用户输入、
恢复上下文、错误详情、原始事件 payload、附件/工件/confirmation 结果保留；三个子表仍在
原表，父外键映射保留原 NO ACTION 语义。七列搜索元数据与 title-only external-content
FTS 保留，native 检索排序算法不变。Workbench DTO/native/RAM 的呈现已通过相关调用测试。

配对回退通过当前数据逆迁，保留迁移后业务事实，而非用旧数据库文件覆盖。原 DDL、
marker/timestamp、分配水位、TEXT 源表与保留子表的 rowid，以及原 FTS shadow 状态由
本次发布的临时内存证据准确恢复；新/变更/删除搜索元数据只调整相应差异。
不持久保存另一份 trajectory 或索引快照。实际副本迁移、逆迁、新事实保留、旧代码
启动及五种离线发布失败阶段已通过6b43a93b来源验证。最终验收还需绑定最新生产来源，
通过完整CI、正常部署和线上读回；详见 database-redesign-phase4c-validation.md。


## Phase4C scheduled callers

候选的定时触发 DAO、dispatcher、恢复、Attempt/History、运行路由引用读取已切换到
runs 中 record_type=scheduled_task_runs 的记录。查询返回原公共 scheduled ID；写入按
record_type 和公共 legacy key 精确定位。execution_id 继续保留原 TEXT 值与 execution_kind
含义，服务命令不会被制造为回复任务。History 对外的 source 标签仍是 scheduled_task_runs。

原事件唯一、定时自然键、overlap 的 first/last/count、领取 CAS、租约、派发事务和
回复关联规则不变。配置版本和冻结 snapshot_json 留在 scheduled_task_config_versions，
snapshot_id 保留原 FK；当前初始化不再建立退休 scheduled 表，现有配置默认值修正使用
typed 父引用。历史 Attempt 和 skipped 快照不因运行路由改名而重写。

正常 deploy 等待读者明确支持已验证的2026-10-11.2 / 2026-10-11.3 来源
和2026-10-12.1候选。来源判断读取实际 schema；旧标记同时含 C runs 声明与旧表时拒绝，
包括没有 scheduled 行的混合库。真正历史来源和当前候选沿用原忙碌/租约谓词，
只读检查不修改行、schema 或数据库 bytes。Workbench CRUD/native/API 仍是后续单元，
本段不表示完整 C 已发布。

Task/Run Phase2 Task4 的隔离候选将 scheduled trigger runtime CRUD、claim/lease、
dispatcher、Dashboard、恢复和 reached History 查询改为直接使用 `schedule_occurrences`；
冻结配置仍由 `configs` 持有，trigger 的 `snapshot_id` 继续引用对应的公开快照 ID。
full-state claim 索引服务完整状态仲裁，pending-only dispatch 索引只覆盖待派发记录。
Scheduled Tasks 页面按 API 返回的 `first_scheduled_for`、`scheduled_for` 和
`occurrence_count` 展示已保存区间，不再按通用摘要计数合并历史行。重启补派去重将聚合
区间端点和候选时间按 SQLite 实际时间比较，因此不同 UTC offset 的原始时间字符串仍代表
同一覆盖区间；原始时间文本保持不变，专用 partial expression index 支持该范围查询。
此候选尚未激活。迁移前安静窗口由 `RepositoryUpdater.current_publication_sources` 的旧
schema 来源检查负责，不能提前查询 native owner。`consumer_system_release` 资产发布发生在
database release 之后，其显式冻结 `SCHEDULED_RUN_SQL` 来自 `app.run_storage`，不是安静
窗口来源，也不是普通 runtime fallback。每个 service-command producer 使用单一 typed
`ServiceCommandResult`；瞬态 counters 不改变持久化摘要。Store 只在当前完整 typed counts
全零、紧邻前一同 Task occurrence 的完整冻结旧摘要也证明零、冻结 command/config 相同、Cron
下一时间点精确连续且当前和前一都没有 Task/Meeting 引用时，在一个写事务保留较新身份并更新
区间/count，再删除较早 occurrence 与 identity mapping。只有 `occurrence_count>1` 不构成空跑
证明；旧 Email grammar 完整校验 `accounts/discovered/failures`，仅以 discovered/failures 判空；
旧 Meeting 扫描、Recovery 多计数和 cookie 维护均不作为历史空跑证据。Run forward migration
在引用转成 native 后，同一事务一次预建保护集并按 UTC 时间/ID tie 顺序单遍退休可证明的历史
相邻空跑，返回迁移统计且不新增持久字段。versioned scheduler create 仍阻止重启补派覆盖区间内
的同配置时间点。Task/Meeting occurrence 引用在 forward/current-inverse 中只转换声明整数叶，
保留其余原始 JSON 字节与 hash 证据。候选仍未激活，尚待完整 Task4 独立 SPEC/QUALITY
审查；quiet-window source 判定仍在旧 schema 阶段，Consumer/Audit 资产发布在 database release
之后，后续发布批须将其读取切换至目标 owner。


Phase4C 的初始化修复边界：旧分散 Workbench0.7/0.8/0.9表缺列后自动补
resume_context、task_sequence、turn控制和附件client_request_id/content_sha256的路径
已退休。当前变更只支持准确source13正迁和完整current14声明；缺失必要列的库拒绝，
不通过补默认值猜测原数据。已有缺失索引/触发器修复继续支持。源13迁移须保留
原user_text、私有恢复上下文、task_sequence、控制状态、附件标识/hash/path/文件bytes；
原本同时间戳但不同UUID的turn按原sequence排序。测试覆盖真实来源保留、当前不完整
声明的非修改拒绝与并发索引修复，不以半旧半新的schema冒充来源。


## Phase4C Workbench callers

Workbench task/turn 的CRUD、全局client_request幂等、task_sequence、单active turn、
claim/lease/stop/control/recovery保留原合同。读者从typed tasks投影公共TEXT ID，
写者按record_type+legacy_key定位；附件/工件/确认表存储物理父整数，DTO仍返回公共
task/turn ID。事件在runs中按公共numeric legacy cursor分页和配对，物理run ID、
turn sequence及native ordinal各自保持原含义；先按物理父筛选再投影cursor以使用索引。

不再写入物理final_text；已有native/RAM与Codex/Claude/Friday读取继续提供正文。
本单元不改API/UI契约、工具权限、确认政策或新的保留期限。准确source13迁移保留
pending confirmation，原有业务recovery是独立调用，不作为迁移副作用。
相关停止/完成竞态、late write、quiescence和恢复行为仍由原机制管理。

旧列补齐测试已换成真实source13值保留和当前不完整schema非修改拒绝；索引修复
继续以两次并发构造、原外键及附件bytes验证。缺失辅助对象的既有初始化路径仍会
执行conversation-session回填并更新schema marker时间；它不承诺所有业务行逐字不变。
最终发布仍须最新来源绑定、完整CI、正常部署与线上读回；本段不表示已部署。


Phase4C Attention 定时错误读取以原event_id或规范scheduled-task-run:<公共整数ID>
分别定位候选，再应用原同日程较新dispatched且回复done/skipped的恢复谓词。
两路以UNION ALL组合，事件身份和conversation身份互相矛盾时仍沿用原“任一路匹配”
语义；不新增身份收紧政策。定位使用既有event/public-ID索引，不新增schema/index/helper。
原2,000VM步预算和只读合同保留；采样进度回调数不表示精确总指令数。


Phase4C 查询性能边界：数字ID投影显式包含已有部分索引的类型条件，SQLite可使用
idx_tasks_public_id完成按公共ID查找；TEXT投影不加入此条件。Work Attention先从
work_summary_inputs类型的scalar状态记录筛出公共ID，再读取所选输入正文；无关类型
增长不扩大其候选扫描。状态、排序、limit、去重与竞争复核规则不变。

Phase4C已于2026-10-12通过PR61/05ca33b1正常上线，生产Store2026-10-12.1、Email51、PID43265及数据/队列读回已验证。具体来源、字段映射、回退、CI、容量和清理边界见docs/database-redesign-phase4c-validation.md；上文候选阶段的时间性验收说明以本条及验收文档最终回执为准。

Phase5E 候选 Store19（`2026-10-13.5`）初始化将 Reply Attempt 和
Memory Write Event 放入有限 `actions` 类型，保留各自公共整数 ID；物理
`reply_attempts` 和 `memory_write_events` 表退役。准确 Store18 来源通过同一
事务内的配对迁移与完整 manifest 核验，空库创建采用 fresh Memory 父外键模式；
已迁移的 actual 模式在重启和辅助索引修复时继续保持无 Memory 外键模式。
Store 与外部 History、Attention、质量扫描、定时恢复、邮件取证和维护读取
都使用有限 Attempt/Memory 投影；外部写入按各自公共 ID 经有限 action writer
更新，维持原有状态、条件、时间及事务语义。Memory 正文不复制到目标，
`payload_json` 读取为 `{}`，仅保留准确的排队业务标记。主 Attempt 详情中的
Audit 工具记录只从原生会话的指定行范围读取；缺失原生记录明确显示不可用，
不会把已退休的数据库工具正文当作替代。已有业务结果、原始回执和排队审查
标记继续保留。发布 quiet-wait 的候选、回执、postfix、delivery、任务、会议和
定时来源只接受完整的 Store19 actual 或 fresh manifest，仍保留旧版本的准确选择。
Store19 的 Attempt 创建时间及状态索引以 `record_type` 开头，使时间范围与
原有状态排序读取在同类型记录增长时仍有准确索引；Reply 公共 ID 排序索引声明为唯一，帮助 fresh
Memory 的公共父 ID 精确读取先定位父记录、再走 Memory 父索引。该唯一性已由
既有全局公共 ID 唯一索引和整数规范文本约束保证；Memory 的 LEFT JOIN、
字段及旧六／九／十二／十四类型声明不变。
本段保留 Phase5e 当时的结构与离线验收说明，见
`docs/database-redesign-phase5e-validation.md`；当时的候选状态不代表当前发布状态。
当前结构与生产读回以本文开头及专有动作验收记录为准，不据此声称完整 CI 已通过。


### 第五期A：候选与执行存储合并

Store `2026-10-13.1` 将候选、审查、选择、补充、执行和动作尝试六种已有事实存入一张 `actions` 表。
原公开 ID 保留各自类型的整数空间；真实外键使用统一物理 ID，页面和业务 API 仍返回原公开 ID。
候选的 predecessor review、selection 的 candidate/review、execution 的 candidate/review/selection
及 attempt 的 execution 关系分别保留。读取使用明确类型投影；写入使用有限字段定义；没有旧物理表、
兼容 view 或双写。`actions` 自己声明任务、运行及动作类型引用约束，不重复安装通用 child trigger。
独立 `external_action_results` 在这段仍保留，后续迁入独立 receipt 类型；无候选回执不会被制造归属。
这是第五期A当时的完整十表目标记录；后续候选目标表数见本文顶部的当前 Action Entity 集成状态。

这个发布单元只接受准确的前版 Store `2026-10-12.1`。迁移前核对六表声明、字段及引用；迁移和 schema
版本更新在同一事务内完成。配对逆迁从当前 `actions` 重建源表并恢复原版本记录，保留发布后的新增、更新、
删除事实，不还原陈旧数据库备份。正常发布和回退后的重新启动在新进程验证，不能以同进程已初始化路径
缓存代替实际启动验收。此段描述本发布代码契约；是否上线以正式部署回执为准。


### 第五期B：独立回执合并

Store `2026-10-13.2` 将 external_action_results、agent_effect_intents 和
agent_execution_receipts 的实际字段与约束合入同一 actions 表。全局回执保留准确文本主键，
通过 first_agent_run_id 引用逻辑 Agent run，不制造 candidate 归属。两类原生执行事实保留各自
AUTOINCREMENT 公开 ID 和 native sqlite_sequence 水位；自动分配与插入持有同一 SQLite 写锁，
显式自动提交连接使用自有 savepoint，普通连接的事务仍由调用方控制。内层 JSON 原始字符串保持原值。
正常运行只读写九类型 actions，不创建旧三表、兼容 view 或双写。

本发布单元只接受准确 Store15 声明，包括实际历史 ALTER 与实际 fresh constructor 的列顺序差异。
配对逆迁按捕获的源声明恢复当前事实与当前水位，保留迁移后的新增、修改和删除，不恢复旧备份内容。
发布 quiet-wait 读取器仅在明确声明的源／目标结构读取原生执行事实；恢复、审查、发送及回执策略不变。
在第五期B记录时，其余 Actions 家族和第六至八期仍未完成。随后第一外部 Action 单元已部署；当前生产状态见本文开头，其他 Action 家族和后续阶段仍未完成。

### Store17 postfix 整合（已发布）

以下记录历史 Store17 边界；当前 Store19.9 的 A5a 将两个子类型事实移到 prepared 父记录，详见本页顶部的当前合同。

Store `2026-10-13.3` 将 outbound_postfixes、outbound_postfix_receipts、
native_reply_dispatches 的十三个实际字段合入十二类型 actions。正常调用与
WeChat bootstrap 只使用 actions 投影及类型写入，不创建旧表、兼容 view 或双写。
复合身份和立即 NO ACTION 外键保留；首次准备、provider 前 claim、首次回执、
echo 判断及会议恢复顺序保持现有行为。只接受准确前版 Store16 来源。
配对逆迁从当前事实重建旧三表及九类型 actions，保留发布后写入并恢复原版本记录。
第五期C曾通过PR77／46bfdbe0正常发布，阶段验收时的Store17／Email51及126物理表读回通过；这是历史发布回执，后续已发布Store18。实际副本、当前事实逆迁、CLI替身与完整CI／上线证据见docs/database-redesign-phase5c-validation.md。完整十张核心表加迁移表目标仍未完成，其余actions家族及第六至八期仍需实施。

### Email action storage preparation (opt-in only)

The isolated Email action storage factory accepts `include_email=True` only with
the sixteen-type reply schema. It adds thirteen finite Email record types and
generated projections for the 144 frozen source fields. Nine ordinary source
ROWID families retain their signed locator in `legacy_key` and nullable public
TEXT key in the payload; two AUTOINCREMENT families retain numeric public IDs;
effects and retry requests retain two-component keys. Native generated columns,
CHECKs, unique indexes, parent-key foreign keys, count triggers and immutable
retry triggers enforce the source declarations. The Email option is disabled in
all Store/Email constructors and runtime callers. The finite Email writer
helpers now accept source-shaped inserts, updates and deletes: nine families
locate by signed source ROWID, two use independent AUTOINCREMENT aliases, and
two use projected composite keys. They evaluate original source defaults under
the writer lock and return source identities. Isolated pure migration functions
now move the exact frozen thirteen-table source into this opt-in declaration
and reconstruct those tables from current typed facts. They preserve the nine
source ROWID namespaces, two AUTOINCREMENT waters, existing sixteen action
types and all thirteen external blockers. The caller owns the foreign-key-off
transaction; no Store/Email constructor selects these functions. Actual
published-predecessor binding, paired release and production acceptance remain
pending. Preflight uses a schema-only SQLite rename/drop probe across MAIN and
TEMP, refusing dependencies that would be rewritten or silently removed while
leaving unrelated TEMP objects intact. The focused validation boundary is recorded in
`docs/database-redesign-email-actions-validation.md`.
Each generated Email source column returns NULL for all older action types and
for Email kinds that lack that original field; its finite CASE reads only the
record types owning the field. The operation-parent discriminator uses the
approved `email_operation_parent_type` name.
Email records also require the shared physical task/run/action reference columns
to remain NULL. The fresh reply variant includes `memory_attempt_ref` in that
check. Original Email relationships use their separately declared source-key
foreign keys.

The isolated Store14.1/Email52 candidate selects the paired conversion for all
ten follow-up/Todo-sync/Task Event source families after the Email and Business
migrations. Forward copies the source-shaped facts to typed Actions, maps the
business send-attempt draft to its physical parent, and retargets
`business_legacy_links.work_update_id` to the physical work-update action. The
current Store constructor no longer recreates these ten retired tables; runtime
callers use typed Actions projections and writes. The current Business
`actual` and `fresh` child declarations have separate finite target checks.
An actual LaunchAgent database copy has passed forward and inverse checks;
this candidate remains unpublished and the production database was not
modified. This paired release does not complete the final ten-core-table-plus-
ledger redesign.

The then-selected Store14.1/Email52 graph also folded the ten Business
Action families (Signals, task and project evidence, dates, relations, cluster
membership, anchor links, Attention membership and events) into Actions. Its
ordinary-table count is 15. Signal bodies remain in BusinessSourceDocuments;
LegacyLinks remained a native child at that stage, with its Signal FK and the Business and
Actions Signal endpoints pointing to `actions.business_signal_public_id`.
Public source IDs, hidden composite ROWIDs, and the four AUTOINCREMENT waters
remain distinct from physical `actions.id`. The paired publisher converts
these ten families after Business candidates and restores them before the
candidate inverse, all inside the existing caller-owned transaction. Fresh
Store/Email and reopened constructors selected that graph at the time; historical
source declarations and inverse factories still recognize their exact prior
layouts. This is local implementation and focused test evidence, not an
actual-database copy or deployment result.

Recorded DingTalk delivery recovery (2026-10-14): startup reconciliation, explicit failed-task completion and protection against failed-run projection all honor the receipt verification state. A recorded receipt with verification.state=ambiguous or failed does not prove delivery and cannot restore done. Positive sent receipts and previously recorded legacy readback receipts keep their existing completion behavior.

Historical terminal Attempt or settlement projections cannot override a current external_action_uncertain/external_action_failed task error. A later verified provider result uses the normal execution completion writer. Original Attempt and settlement history remains unchanged.

Attempt detail and History attention prioritize the current failed task delivery error over historical Agent success summaries or a stored sent-reply row. Uncertain delivery is displayed as unconfirmed, while provider failure is displayed as failed. Historical Agent explanations and original raw receipts remain available as historical evidence.

Attempt detail projects System Execution only when a current reviewed candidate
exists or the historical candidate has an execution record. A stale candidate
without an execution record remains visible as historical review evidence, but
its unstarted actions are not presented as pending execution. Existing
historical execution records and canonical provider receipts remain readable;
this projection does not alter queues, retries, or execution state.

History list status for a failed Attempt follows its owning AgentRun's stable
reply-task identity when that task's DingTalk trigger key has since changed.
Current terminal task state is projected without rewriting the Attempt or its
execution audit. A current candidate execution with an unresolved `uncertain`
receipt remains `uncertain` even if its task closed; an independently resolved
Attempt retains its recorded resolution state. A verified terminal-source closure
for an OA follow-up is the narrow exception: History projects that follow-up as
`skipped` only when its persisted readback names the exact original message and OA
process/task, confirms native/OpenAPI identity and terminal states, records zero
external writes and no current principal tasks, and explicitly leaves chat delivery
unverified. The original provider receipt and execution remain `uncertain`; `skipped`
means the obsolete follow-up no longer needs action.

Legacy failed OA Attempts with no active local Reply Task bound to their
trigger are reconciled by the scheduled OA scan using their exact persisted
process and task IDs. It examines at most 20 distinct process/task groups per
scan, prioritizing groups whose local business-object task is already done or
skipped. The scan closes unresolved failed Attempts for that exact process/task when live
native readback confirms both the process and task are terminal and a
transactional recheck confirms no active local Task owns each trigger. A local
Task already closed as `done` or `skipped` does not block the reconciliation.
It records the
readback as the resolution and marks each follow-up `skipped`, preserving the
original failure reason; it does not infer message delivery or create a new
Consumer/Audit run. Missing, partial, mismatched, nonterminal or failed reads
leave the Attempt unchanged.


### Paired Store/Email schema release

The selected candidate coordinator in app/action_email_schema_release.py
prepares the exact Store19/Email51 source and publishes Store20/Email52.
Preparation records the original Store marker and complete Email ledger,
native declarations, required allocator-presence bits and existing FK findings
in one read snapshot.
It carries Email, Business, Provider, Configs/Skill/mapped-Runs8, State, Tasks20,
retired empty Workbench confirmation declarations and Runs11 source-profile
metadata without a business-row snapshot.

Forward converts Email, Business, Provider, Configs, State and Tasks20 in the
existing FK-off transaction, retires the empty confirmation table, then folds
Runs11 before writing the shared Store/Email marker timestamp. Readback validates
the complete current declarations, translated dependencies, retired names,
markers and unchanged FK findings. Inverse recreates the empty confirmation
schema, restores Tasks20 and State, restores mapped Runs8 from current Runs11
facts, then restores Configs/Business/Email/Provider, the original Store timestamp
and complete Email ledger from current facts and allocator waters. A failed
transaction rolls back its DDL, markers and data together. The genuine cold
initializer and constructors are selected; actual-copy, latest predecessor,
native CLI and deployment acceptance remain separate from code selection.

The following group4 development notes are historical milestones. Their WIP
and former Store19/Email51 default statements describe those recorded steps;
the current branch contract is the selected Store20/Email52 coordinator above.

### Email13 group4B partial DAO integration

The unpublished Email candidate now reads the five plan/direct/attempt/retry/
feedback families through their finite typed projections and writes through the
accepted source-shaped helpers. The existing method APIs, original writer
transactions, current-plan CAS, direct dependency ordering, explicit retry
records and successful receipt selection remain unchanged. Direct scalar writes
resolve the original source ROWID with the original status/start/count guards
inside the caller's writer transaction; the physical `actions.id` is never used
as a source identity. Attempt insertion returns its independent public
AUTOINCREMENT ID, including continuity after a deleted high ID.

The confined `ignore_plan_action_conflict` Email-operation helper option uses
SQLite's exact `(email_action_plan_id,email_action_type)` partial unique conflict
target. Only that duplicate is ignored; unrelated public-key, CHECK, NOT NULL
and FK failures remain native errors. This does not broaden existing `ignore`
behavior or change any declaration. The shared feedback reply-in-flight guard
and direct-action unsubscribe-receipt dependency read use their existing finite
projections; all remaining C/D writers are deferred.

This is partial DAO WIP, not a separately runnable default runtime. The converted
methods require an explicitly prepared Email29 database, while default Store19/
Email51 constructors, validators and release selectors still select the old
schema. Remaining unsubscribe/reply methods and mixed observability reads still
require their planned conversion. Final constructor/validator/DAO/marker
activation remains coupled, with Task5/6 acceptance separately open.


### Email13 group4C partial unsubscribe DAO integration

The unpublished candidate now also reads and writes the six unsubscribe
families through their accepted finite actions projections and source-shaped
writers. Claims retain the existing generation/lease and account snapshot
checks; effects retain their original two-component public key; journal steps
retain their independent source AUTOINCREMENT namespace. Continuation acceptance,
failed-operation release, terminated-owner recovery, orphan release, explicit
retry, journal repair, direct retirement, terminal receipt promotion and
principal cancellation keep their original predicates, mutation order and
transaction outcomes. The live lineage helpers and the two mixed observation/
context reply-receipt reads use finite projections as required by these paths.
Reply lifecycle writes remain group4D work.

The confined `update_continuation_conflict` insert option expresses only the
existing continuation public-key UPSERT. It changes effect digest, observation
reference, controls and update time, preserving original creation time and
structural source ROWID. Encoded SQLite scalar values are updated inside JSON
without decode/re-serialization. Other native constraints still apply. Backfill
validates the supplied URL before acquiring its writer transaction at the
original conditional mutation point; a competing fill returns false and is
preserved. No lifecycle, recovery, authorization or retention policy is added.

This supersedes the C-specific deferrals in the historical group4B section.
These methods still require an explicitly converted Email29 database. Default
Store19/Email51 constructors, startup validators, migration selectors and action
flags remain unchanged; their E/F conversion and final paired activation remain
coupled. Group4C does not establish usable default runtime, Task5 external
consumer integration, or Task6 native/provider/production acceptance.


### Email13 group4D partial reply DAO integration

The unpublished candidate now covers the remaining reply dispatch-claim and
receipt DAO methods through the accepted finite projections and writers. All
thirty source fields remain. The eight original mutations retain their existing
writer transactions, owner/generation/lease predicates, current authorization,
immutable dispatch binding, account snapshot behavior and native outgoing
Message-ID uniqueness. A retryable claim may refresh its account snapshot and
owner exactly as before; uncertain dispatches remain reconciliation-only.

Receipt replay retains the original provider evidence comparison, original
JSON-text CAS and one-way SMTP acceptance-ID enrichment. Receipt insertion or
enrichment and claim completion remain one transaction, including rollback on
owner mismatch or failure. Structural source ROWIDs remain distinct from public
action identities and physical actions IDs. No helper interface, SMTP enablement,
recovery, authorization or retry policy changes are included.

This closes the D-specific deferrals in the preceding historical B/C sections
for current EmailStore DAO SQL. It does not activate a default runtime: source
metadata, historical/bootstrap dispatch, startup validators, fresh constructors,
real target markers and coordinator/subprocess remain E/F work. The unsubscribe
cancellation preview still invokes that deferred startup shape validation.
External direct consumers remain Task5, and full-copy/native/provider/production
acceptance remains Task6. Default Store19/Email51 and action flags are unchanged.

History latest-task lookup publication666f4078 passed exact-tree full Quality
and normal deployment on2026-10-14. Production PID31985 and healthz were read
back; live History list/chart, type/status controls, pagination and an existing
Attempt detail were opened. Initial20s combined-request timeout and variable
Status latency remain recorded; system health still reported degraded and
unavailable historical native delivery evidence. This is scoped read acceptance,
not proof of all task execution or provider outcomes. See
`docs/database-redesign-history-query-validation.md` for exact evidence.
No schema or retention change is part of this History release.


### Email current fresh-seed preparation, Task4E1 (unselected)

The unpublished Email candidate factors the existing category conversion and
model-promotion/initial-description-revision seed loops into private helpers.
Both historical migration bodies preserve their prior behavior. A separate
`EmailStore._seed_current_fresh_state` prepares seed facts after the neutral
retained-table factory, under its caller's transaction. It reuses the existing
category definitions, thresholds/config versions, promotion values
0.95/0.95/20/500.0, UUID allocation and revision clock/snapshot helper.

This entry point is currently unselected. F may invoke it only on the new-database
creation branch; current reopen, verified Email51 migration and read paths must
not invoke it to populate an existing empty configuration. It creates no schema,
ledger or action objects, and adds no commit/savepoint/repair policy. Existing
configured sets, promotion rows and existing category revision history remain
intact at this helper boundary. E1 tests compare its new-database seed facts
with genuine archived2773 default initialization through Email51. E2 validators
and F constructors/locking/markers/coordinator remain pending; defaults are
still Store19/Email51 and this partial candidate is not runtime-ready.


### Email29 current validation preparation, Task4E2 (unpublished)

The Email candidate's single current shape and durable validators now inspect
29-kind actions and read all thirteen Email families through the accepted finite
projections. Shape validation accepts either existing reply declaration variant
by exact action-manifest comparison. Retained validation keeps the original16
business-table requirements plus the ledger; the three optional historical
traversal/operation/candidate tables have not become new startup requirements.
The neutral factory's49 objects remain a separate structural contract/proof.
Historical `_REQUIRED_*` maps and retained trigger keys remain intact for their
existing factory/source consumers.

Current shape checks retain19 native nonaction triggers and the exact13 translated
account/message/classification blockers, preserving changed-value predicates,
processing/dispatching status, owner names and errors. A finite missing-only
installer shares the existing declarations with pure conversion and leaves the
caller in charge of transactions. It never repairs or replaces an existing
malformed object; the current shape validator refuses it. No blanket receipt
immutability or account/message foreign key has been added.

Durable policy outside the finite SELECT arguments is unchanged. Native SQL
NULL allowances and application rules are checked per method: the original
receipt row validator accepts a nullable receipt identity while the public
receipt writer rejects NULL input; nullable feedback IDs are rejected by the
original durable validator. Read validation does not write or commit. The public
read-only unsubscribe cancellation preview now validates the explicit target
shape and preserves its existing decision/no-replay checks.

This remains a partial unpublished branch. Current validators target Email29;
historical cached-plan/source DDL remain native, so the <=48 historical chain is
not made compatible by an alternate validator. Default Store19/Email51 bootstrap
and ordinary source constructors are not runnable target proofs. F must select
the exact supported Store19/Email51 pair before ordinary initialization and
explicitly refuse unsupported older sources before any mutation, then couple
constructors/locking/markers/coordinator/default activation. E1 fresh seeds
still have zero runtime callers. External Task5 and full-copy/native/publication
acceptance remain separate gates.


### Coupled Email29 and Configs candidate defaults (unpublished)

The earlier Store14.1/Email52 candidate superseded the E1/E2 staging boundaries
above: AutoReplyStore selected Store `2026-10-14.1` (program Store20), and EmailStore
selected Email52 with the single 34-kind actions schema, including two typed Skill
receipt kinds. This is local candidate
selection, not publication or a claim that the whole service can be deployed.

The supported predecessor is exactly Store `2026-10-13.5`/Email51. The real
database release coordinator selects one paired release before either standalone
preparer. Ordinary target constructors refuse that pair read-only, before cache,
WAL or schema changes, and repeat source selection under the path lock. Other
older/newer/missing/mixed markers and genuine old Email-only databases are refused;
there is no alternate legacy validator or constructor upgrade route.

Fresh Store preserves its existing early bootstrap commits. Its final transaction
creates the canonical30-column classification parent, current configs and schedules,
mapped runs, 34-kind actions and Store20
marker together, validates the existing Store manifest and commits. Earlier or
suffix failure may leave the same unversioned core that existing bootstrap rules
refuse on retry. A completed Store-first state is supported. Fresh Email invokes
the genuine public AutoReplyStore once outside Email's non-reentrant path lock,
then creates the retained current nonaction schema, ledger, existing fresh seeds,
seven retained Email blockers and Email52 marker in one transaction. Six account
blockers live on `configs`. Email failure rolls
back to Store-first and can retry. Store never constructs EmailStore. Current
reopen and51 migration do not seed existing empty configs/revision histories;
known coherent-current Store repairs retain their existing route, including
missing canonical Store-owned Skill/runtime guards, selected schedule/Skill indexes,
run auxiliaries and action auxiliaries after read-only declaration checks. Altered
objects and Email-owned auxiliaries remain refusals. Email current
reopen validates the exact `provider_state` declaration, including partial
indexes and source-name absence, before using it. A malformed target is refused
without recreating the old provider tables or repairing a column.

Paired publication uses a real child process carrying the prepared marker/full
ledger/declarations/FK binding through stdin, applies the same atomic protocol,
then cold opens both genuine current default classes. Legal native BLOB marker
and ledger timestamps use the existing scalar encoding and survive inverse with
their original values/types. Read-only preparation projects only the bounded
native config declarations through the six Email-owned account blocker changes;
apply checks the actual post-Email capture before Configs conversion. The inverse
restores native configs and schedules before Business, Email and Provider, then
reconstructs current action facts,
source identities/ROWIDs/allocator waters and exact source markers/ledger; it does
not restore an old database snapshot. The source SQL NULL/application policy
remains method-specific and unchanged.

Task5A converts the active Store unsubscribe receipt/recovery reads, History
(including the failed-only candidate path), queue/Attention, and deployment
quiet read to finite source-shaped Email projections. History keeps the signed
native source ROWID as `email-action:<rowid>`/`source_id`, independently of the
nullable public action ID; failed Attention keeps current-plan and retry-window
filters. Publication readers explicitly accept the exact Store20 action shape,
including valid Store-first databases without an Email ledger. The Email quiet
reader accepts only the exact Store19/Email51 source or Store20/Email52 target;
runtime Email availability requires the initialized target pair. Existing audit,
recovery, outbound and quality policies are unchanged.

Task5A is a bounded local consumer conversion, not final Task5 acceptance.
Remaining historical/current fixture adaptation, all selected console/worker
tests and source-copy/native/CI/publication/live readback gates remain open.
The whole branch is not deployable.


### Email metadata refusal boundary, Task5B0 (unpublished)

Current constructors preserve the original Email missing-metadata refusal instead
of treating it as Store repair. A narrow read-only comparison derives Email's
32 auxiliary objects from the same-variant29 manifest minus the16 manifest's
object names, then compares complete canonical type/name/owner/SQL. Both reply
variants have identical25 index and7 trigger metadata in this difference.
Shared actions/public-ID metadata stays under the existing Store contract.

Email performs only this comparison after pair selection and before invoking
the genuine Store, reporting EmailPersistenceCorruption for missing/malformed
Email auxiliaries. Normal Email construction still invokes Store once outside
the Email flock. Store's original coherent-current repair permission additionally
requires these Email objects to be intact, so its generic factory cannot silently
repair them. The generic factory and existing cache are unchanged. Old Store
index/route/feedback repairs remain supported; the old Email51 provider signal
column helper is retired from current reopen. Fresh/Store-first and paired
migration behavior stay intact.
This changes no data, authorization, recovery or business policy. Task5A finite
readers remain unchanged; broader Task5/6/7 acceptance and deployment remain open.

For the current Email29 candidate, the action-plan, action, attempt and receipt
columns and the attempt parent key are generated or constrained by the shared
`actions` table. A malformed one is therefore refused by the Store20 schema
check that EmailStore invokes before its own validation, including when
`validate_rows=False`. That path raises SQLite `IntegrityError` and the existing
Email Web availability mapping reports `sqlite_integrity_error`; the old Email51
constructor checked its separate native tables and reported
`email_persistence_corruption`. Both remain unavailable, with no schema repair,
automatic retry or change to audit, recovery or authorization policy.

The current Store20/Email52 constructor selects the paired schema before
Email's retained-table validation. A partial database with a malformed or
future Email ledger therefore raises SQLite `IntegrityError` for an unsupported
or mixed Store/Email source; a ledger missing its `version` column raises
SQLite `OperationalError` for that column. The same paired refusal applies to
an otherwise complete current pair whose Email ledger is advanced beyond52.
These read-only refusals preserve schema, marker and business facts, schema
cookie and database bytes. This diagnostic ordering follows the existing
paired selector; it adds no repair or recovery path.

For a complete Store20 database whose Email ledger is advanced beyond52, a
local Email route registration can still construct its non-Email FastAPI routes
and mark Email unavailable with `sqlite_integrity_error`. Constructing the
whole Audit app first opens AutoReplyStore, so the paired selector refuses
that unsupported pair before route registration. The older Email51 Audit-app
isolation assertion is exercised against its genuine published source. Current
Web API tests use finite Email action projections and typed writers for synthetic
fixtures; this changes no runtime route, recovery or authorization behavior.

Service context JSON blocks use compact serialization rather than indentation.
Every original field, nested value, identifier and string whitespace/newline is
preserved; only JSON separator/indentation bytes change. Consumer and Audit share
this renderer. Candidate digest calculation, source binding, business validation,
role/tool boundaries, session reuse and historical saved inputs remain unchanged.
See docs/context-json-formatting-validation.md for focused verification and
metadata-only measurements; publication status is recorded there.


Specialized Meeting, email-classification and Task inputs serialize their existing
JSON blocks compactly. Meeting inline schema (still minus its existing root
description) and complete MeetingSource, sorted classification context, and
current WorkItem metadata retain the same parsed values and field selection.
Full source strings, schema constraints, source projection, business rules,
roles, native schema delivery and session behavior are unchanged. See
docs/specialized-json-formatting-validation.md for acceptance/publication status.


Task native schemas omit autogenerated display-title annotations, using the same
annotation formatter as Consumer/Audit. Field names (including title), full field
descriptions, literal JSON values, strict required fields and every validation
constraint remain unchanged. Existing Task identity validators and provider,
Skill/source, session, correction and Audit contracts are preserved. Consumer/Audit
schema text is byte-identical. See docs/task-schema-title-formatting-validation.md.

会议消息新增 `message_title`，由 Agent 按实际接收受众核验：安全时可沿用会议原题，否则使用中性业务标题。正文首行与推送标题均只取此字段，不再自动拼接可能含人员方案信息的 `source.title`；没有已核验标题的持久化决策使用中性的“会议跟进”。原会议标题仍留在来源记录中用于追溯。

Task Agent 的本机 CLI 路线当前每轮提交 Work Tracking Skill 的绝对路径和 SHA-256；冷启动读取全文，
续会话只在同版本全文仍在上下文时复用，否则重读。Codex 命令复用现有
`inlined_skill_names` 元数据，由适配器把已有路径筛选和同名隐藏规则合成一份
原生 `skills.config` 数组，避免重复配置键覆盖。仅隐藏同名安装副本的目录项。
远端 Friday Runtime 路线继续收到完整 Skill 正文。其余目录选择、工具权限、输出 schema、路由与 session 绑定保持原样；不修改全局 Skill
或共享 AGENTS 配置。旧版同名入口的修复见 `docs/task-inline-skill-once-validation.md`；
当前按需读取的验收与限制见 `docs/task-skill-on-demand-validation.md`。

Codex continuation commands retain the caller-selected workspace through the native
global `--cd <workspace>` option before `exec resume`; cold commands are unchanged.
Continuation no longer inherits the service process directory. Workbench inserts
its existing model/MCP timeout options after the located exec/resume command so
global options remain intact. Native session IDs, source, schema, credentials,
permissions and route policy are unchanged. See `docs/codex-resume-workspace-validation.md`.
Attempt current failure descriptions use only the latest Agent turn or the persisted final task/execution stop. A later completed turn supersedes earlier technical failures in the current-stop projection; historical runs remain available in the runtime trajectory and are never rewritten. A final Audit return/reject keeps its actual summary, and accepted but unverified System actions retain their uncertain execution diagnosis.

Audit field assertions are first bound to the exact current candidate. Only their sensitivity-check copy reuses the existing strict configured service callback-pair validation; original assertion values, candidate digests and persisted results are unchanged. Other Audit prose, malformed pairs and nonconfigured URLs retain credential detection. Fresh and recovered Audit results use the same check. Audit context identifies the service-prepared DingTalk signature and feedback postfix so Consumer is not asked to remove content the service reattaches.

Attempt detail resolves a persisted run through its stable reply_task_id; message-key lookup is used only for records without a bound run. Current OA input keys can change while the original run keeps its task identity. Verified execution recovery can therefore project the settled task without rewriting historical Attempts or runs. Attempt15465 execution213/action208 was reconciled from its existing accepted OA return using exact native readback, then task404881 was closed through the existing settlement operation; no dispatch was made and original run rows matched the verified backup.

Attempt current stop evidence is scoped to the current task execution generation, including an empty current-run list. Current failure summaries and codes come from that generation's final run, task error or exact current reviewed execution; an approved Audit summary cannot replace a failed or uncertain execution result. Historical run trajectories and execution details retain their identities, while their provider-risk rerun constraints continue to use the original Attempt. A settled done/skipped task has no current stop even if an old Audit returned its candidate.

OA approval detail and History detail use the same current-task-generation status projection for the newest Attempt. The OA detail also returns the persisted Attempt value as `recorded_status`; older rows remain historical. This is a read projection only: stored Attempt rows and execution receipts are unchanged, and an uncertain current execution remains uncertain.
Consumer and Audit both persist a new Codex session selected by the existing runtime retry router within the active run. After an idle timeout, a fresh Audit attempt may replace that run's current session just as Consumer does; each runtime attempt retains its original session, transcript slice and failure. Audit session selection reads the latest Audit-owned native RuntimeAttempt within the same Task and execution generation, at or before the current revision/Run, and requires the same route, runtime kind, credential mode and model. This applies to every route, including existing non-OAuth records whose Run session projection is empty. CLI-native Audit session events update the existing Run session projection so the established repeated-result-failure fresh-session rule still applies. Forced fresh sessions and explicit noncontinuable previous revisions remain fresh. Audit never reads or writes the Consumer conversation session slot. This repairs session persistence only and does not change route selection, retry budgets, candidate binding or external execution.

Attention list, summary and count readers follow a historical Attempt's original AgentRun task ID when the OA scanner has since changed the trigger message key. A current bound task in done/skipped no longer appears as an unresolved historical Attempt. Same-generation failed tasks remain visible, valid reviewed human decisions keep their independent projection, and the historical Attempt/run/receipt records are unchanged.


### Native-source scalar representation and State runtime

Native-source Email/Business/Configs/Skill/Sync and State storage encode finite REAL values as an exact binary significand and exponent and decode them with SQLite's built-in pow, preserving native TEXT/INTEGER affinity results. Scalar binding reads float and string subclasses through their base implementations and bytes through the buffer protocol, matching sqlite3's underlying bound value. Non-C-contiguous buffers retain native BufferError behavior. The shared encoder validates the underlying integer against SQLite's signed 64-bit range before serialization. Provider's four observation fields bind native scalar values directly with SQLite affinity, without a JSON codec. Published Reply retains its existing scalar format; frozen source fingerprints remain unchanged.

Store20/Email52 uses one State table for fifteen retained native state kinds. Fresh Store-first and Email-first construction creates this declaration; current callers use the existing State projections and transaction-owned write primitives. The paired release retires sixteen native State tables, including the eligible empty corpus source table. Original unused conversation fields are retired only when their existing source eligibility check permits it. Archived native factories and historical release source contracts remain unchanged.

The next unpublished intermediate Store20/Email52 candidate stores a legacy
WorkTodo-to-formal-Task import mapping in the WorkTodo Business row's nullable
`import_task_id`, a typed FK to `business(task_public_id)`. The independent
`business_legacy_links` table and its WorkUpdate Action guard are absent from
the selected current schema. WorkProject and WorkUpdate remain history-only;
the importer checks the source manifest and supporting evidence before any
write. Each apply item rechecks its source row and supporting WorkUpdate through
indexed public-ID lookups inside the writer transaction; the corpus fingerprint
is computed once before writes. It then reads or assigns the WorkTodo owner FK for idempotent formal Task
imports. The paired release converts the legacy owner after canonical Email
inside its existing transaction and restores it first on inverse. Historical
source factories retain the old declaration. This is an intermediate eleven
ordinary-table candidate: `business_source_documents` still retains its body,
and final body-free provenance and production publication remain open.
The import manifest format is version 3: the dead Project registry key and
always-zero Project match count are removed from the plan and CLI output.
Earlier manifests are rejected at read time and must be regenerated.

State INSERT, point UPDATE, UPSERT and bulk UPDATE keep caller-owned transactions and do not commit or roll back earlier caller work. Omitted defaults and OR REPLACE substitutions for supplied NULL/NaN on defaulted NOT NULL fields execute inside the final write statement. Nullable NULL and defaultless NOT NULL failures retain native behavior. Bulk updates preserve one-statement affected-row counts, ABORT behavior, original-row expressions and untouched encoded leaves. SQLite CURRENT_TIMESTAMP remains statement-stable.

State UPSERT uses the existing source-key partial unique index in one INSERT. Original INSERT-candidate constraints run before a conflict update, including explicit NULL on a required wizard confirmer, even when an update guard is false. Matched rows keep their source identity and change only selected payload leaves. No failed SQL operation retries a different storage layout.

Organization name and open DingTalk ID indexes use the original source ROWID as the final key, preserving equal-key encounter order independently of State physical IDs. Current declaration checking sorts index records by name and omits only SQLite's index enumeration seq; SQL, ordered columns, uniqueness, origin, partial predicates, foreign keys and triggers remain exact. Source declarations and physical State DDL are unchanged. Current constructors retain only the existing two dispatcher-index repairs and reject unsupported namespace collisions before changing journal mode or DDL.

Current session, conversation, lock, route, wizard, scan/history cursor, reaction, health and deferral callers use State. Dispatcher ownership, generation, terminal/expiry predicates, cancellation fences and task eligibility retain their existing business rules. Empty dispatcher probes remain read-only; positive claims recheck inside the existing writer transaction.

Publication readers choose the native historical source or exact current State marker explicitly. A malformed current declaration never retries native. The seven generic current publication selectors require the complete current Actions/Skill receipt declaration; Email availability and action publication retain their existing Store/Email marker-pair contract. Store-only current databases do not advertise an Email runtime.

Paired forward migration captures State declarations only, converts State after Configs and writes the Store marker and Email ledger with the same existing timestamp in the original atomic transaction. Target verification reads the current State marker. Inverse restores State first from current facts, then preserves the existing Configs/Business/Email/Provider inverse sequence and original native marker/ledger restoration. It does not restore State rows from an earlier snapshot.

Upgrade progress selects the committed schema at DAO construction and immediately before native DAO progress writes. One read-only snapshot observes the retained Email ledger first: positive native versions through source51 select native service_state and target52 selects State; malformed or unsupported future ledger versions remain visible errors. Without a ledger version, Store-only State requires its exact current Store marker; missing paths and empty databases remain source without creating files or tables. Explicit getters/setters keep their original transactions and bounded lock/busy retry and do not reselect after SQL failure. Generic protocol stores are unchanged. Progress after a child commit or inverse follows the actual committed role, including a commit that becomes durable before an exception is reported.

听记管理页认证失效（2026-10-15）：组织选择或登录校验抛出 MinutesBrowserSessionExpired 时，服务使用 macOS open 在本人普通 Google Chrome 中打开听记 history 登录页。原命令仍保留失败，不把打开页面当成认证成功；后台浏览器照常关闭。本人完成登录后的既有 cookie 同步与正式重试流程不变。听记管理浏览器使用 Chrome 原生 `--restore-last-session` 恢复每日副本中的 session cookies，避免 Chrome 默认启动删除仍有效的后台 access cookie；首次使用新 profile 时先启动并关闭一次空白 Chrome，随后重新注入副本再正式启动，因为 Chrome 对新 profile 忽略该参数。启动后关闭恢复的旧标签，退出前把仍打开的页面导航到 about:blank，避免后续启动累积标签或重载旧听记 URL。该设置仅用于听记管理 profile，不改变 cookie 到期时间、权限申请规则或退订浏览器。

原生进程退出与输出采集（2026-10-15）：子进程退出不代表 stdout/stderr 管道已经读完。Process runner 在既有进程组终止之后以非阻塞方式读取已到达的剩余字节，继续使用相同 UTF-8 解码及逐行回调，再刷新无换行尾行。如此 thread.started 会话定位和最终回答不会因先读到 stderr 而丢失；无效结果仍按原合同失败并保留已观察的原生证据。超时、路由、结果校验、外部动作及回执策略不变，不重放历史退订或清除执行标记。回归和验收见 docs/405078-process-output-validation.md。

Task Agent's existing cross-Project supporting-Task validation reports the exact
assessment index, Task decision index, and the canonical Project selectors to
the existing correction turn, including a missing selector and its required Project.
The native project_link_evidence description states its existing dependency: the
list must be empty when project is null. A Task may not support an assessment for a different
selected Project. Validation predicates, output schema, and correction budget are
unchanged. Regression coverage exercises the correction prompt and valid removal
of only the mismatched membership.

Rejected Task Agent outputs with child-field validation errors also receive
relationship diagnostics from a typed projection of the existing Project/Task
relationship fields. The projection reuses native null/default semantics and the
same envelope validator; it returns diagnostic strings only and cannot be adopted
as a candidate. Malformed relationship syntax supplies no inferred relationship
errors. Supporting membership violations are reported together. Full candidate
validation, the native output schema, and the one-correction budget are unchanged.

When a schema-valid Task Agent result fails the existing sensitive-result codec,
the same-session correction identifies a credential-like field by its field path
without echoing its value; runtime-path failures retain their existing guidance.
The credential and runtime-path rejection rules and one-correction budget do not change.

Reply declaration selectors accept an explicit optional Skill configuration receipt scaffold and forward it to the existing complete-shape recognizer. The default remains false for historical callers; current State Memory and generic publication readers select the complete current declaration.


Current database construction (Store2026-10-23.1/Email54): blank Store/Email construction installs the ten core tables, the Email ledger and the three Action Entity tables directly. Cold opens require the exact current declaration. Missing auxiliaries are errors; startup never repairs schema or backfills OA metadata. Historical source factories serve explicit migration/inverse tools only. Publication requires production-derived typed-fact comparison, current-data inverse, old-code cold read, and reforward proof; evidence is recorded in runtime-current-schema-validation.md.

Current Signal creation and reads retain exact source identity, locator, hash
and byte count without an original-body table. Semantic replay/import,
Project context, Attention and minutes owner backfill use the current provenance
projection rather than the historical source-document projection. The isolated
Task replay tool also uses current Task/Run, Business candidate, Business Action
and sync projections. Its CLI applies the selected code root before application
imports; each comparison revision runs its matching script in a separate process. Its domain comparison includes shared Business relations
and provenance, excludes run bookkeeping, and counts source versions by their
existing identity keys. One case carries exact supplied originals in a transient
PreparedSources object through seeding, execution and final readback. Binding,
hash and byte checks apply before reuse; unavailable originals remain
unverifiable and no body is persisted or substituted from a saved quote. Worker and
web detail readers receive the existing configured DWS reader per request;
originals are read on demand and are not cached in the database. Retained
Console business facts can provide their exact original event text. Missing
originals remain unavailable. The authorized twenty historical originals may
be retired only within the final migration scope; this local change does not
remove production data. Current acceptance and remaining release gaps are in
`docs/database-redesign-final-acceptance.md`.


The final candidate's paired release now prepares exact source-version locators
from the verified metadata inventory and the bounded twenty-version retirement
authorization, then converts final Actions/provenance after inline legacy ownership
and before the atomic marker commit. The complete inverse metadata, including the
final Action conversion, is flushed before COMMIT and received on failure/timeout.
The inverse uses current facts; exact originals come from the explicitly supplied
existing deployment backup or the native/provider/retained Console reader. It never
restores old business rows from that backup. RepositoryUpdater passes its already
verified backup path into the database publication callback; backup policy and
service lifecycle are unchanged. Config row transfer writes its explicit historical
migration-stage receipt representation before the final conversion; current runtime
writers use only the final representation. Current publication readers select the
exact final Actions/provenance declaration; historical marker branches are unchanged.
Full original-source paired copy/current-data inverse/reforward and the five real
CLI cases have passed on final-structure copies with fake outbound. Production
release and remaining Email lifecycle remain open; see the single final acceptance
checklist. Operator deferral receipts use the existing service_state namespace
in State, retaining the original 19 fields and integer receipt IDs. Apply/resume
no longer creates a physical receipt table. Explicit schema inverse restores
the old tool table from current receipts, including post-migration writes. Existing
operator eligibility, backup and transaction behavior is unchanged.


The final candidate's Email readonly adapter exposes fetch_message for exact
account/folder/UIDVALIDITY/UID and stable identity. It reads complete selected
MIME text parts with BODY.PEEK, skips attachment payloads, validates each FETCH
record's UID and rechecks generation. Existing bounded scanning is unchanged.
Task context uses EmailSourceReader and current canonical message metadata,
including message-only records, instead of classification coordinates or a
neighboring UID batch. Provider-state has current folder facts but no UID.
When an available observation names a different folder, locate_message searches
that explicit folder and verifies one unique exact Message-ID header before
fetching. Missing/ambiguous identities are unavailable; metadata is rechecked
after reading and no body or new locator is persisted. Without a current observed
moved folder, changed source generations remain unavailable.
This does not retire completed Email bodies: list/detail/search/training and
historical thread integration still need final acceptance. Component tests used
fake IMAP. A later bounded readonly provider probe verified one original; two
old coordinates remain unavailable in their recorded folder. This is not proof
of general live original availability.


The candidate training-source catalog reads selected label/identity metadata,
using the same selection predicate as the real training-record reader. It no
longer constructs normalized inputs just to count labels. The canonical normalization function and promotion contract remain unchanged;
current selected input sources are described below. The offline labeler's
classified-identity set uses the existing complete identity query directly.
The registered Console JSON classification list, detail and unsubscribe-list
routes use the source-backed reader. Existing sender/subject/visible-body literal
search semantics, totals, ordering and pagination remain intact. Search reads
page-sized batches grouped by account; only the active batch and result page
retain original views. The no-body-copy search product choice is pending; do
not interpret HTTP/fake-provider checks as live provider latency, historical
original availability, production deployment or physical body retirement.


EmailSourceReader may resolve unavailable old coordinates by an exact unique
Message-ID search in an already-observed available same folder; a changed folder
uses the same exact lookup. It does not guess an unobserved placement or persist
new coordinates/body. Folder-only training selection no longer loads unselected
Agent/user input. The test-double-only legacy model_text conversion is removed;
actual selected training sources use the canonical current Store contract.


EmailSourceReader.read_many reads a finite batch for one account through one
existing readonly source lease. It reads locator/folder metadata before provider
I/O and rechecks the whole batch after closing the lease, returning no partial
batch on failure or location change. The single-message reader uses the same
path. There is no persistent body cache, new schema field, connection pool, or
source-locator write. The Console JSON reader uses this batch API through
read_views and the existing readonly connector registry, constructed once per
route registration. Source credentials use the Web effective environment
(including its explicit env path); worker defaults continue to use os.environ.
A missing original, provider failure or changed source snapshot returns an
explicit email_original_unavailable response, never a stored-body fallback.
Provider latency and full mailbox source availability remain unverified.

The reply Memory queue status uses the same current Action variant selector as
Store's reply-Memory reader. It retains memory_write_events/last_error and does
not substitute task_memory_write_events or an old full intermediate declaration.
Malformed shared Email/State declarations are rejected before Store/Email
startup under the current paired schema contract; isolated Email initialization
failures still leave its routes unavailable while other valid routes operate.


Current selected Agent/user training inputs are resolved by
EmailSelectedTrainingReader from the latest exact external selected baseline
when account, stable identity, source, category, important, thread, current input
schema and existing message/classification update clocks match the frozen row.
The file identity/digest/schema is verified by the existing snapshot reader.
Records without such a binding use bounded account source reads and the same
provider_model_input_fields/canonical_model_input as online inference. No DB
body/model_text reader is a fallback, and malformed or unavailable files are not
hidden by trying other sources. Labels and latest-reference metadata are checked
after I/O. Preview and manual selected training use this reader; their blocking
work runs through FastAPI's native thread pool, not its event loop. Audit shares
one source reader between Console and learning; the worker reuses its existing
registry source factory. Training source unavailability is explicit (HTTP 502).
Frozen inputs retain their exact bytes. Newly read source inputs can include Cc
and unsubscribe features that the former DB reconstruction omitted; this is the
existing online input contract, not proof that every new input matches the old
DB reconstruction. No activation or promotion policy changed.
A final-copy rehearsal refreshed the current selected external dataset and
proved all 3,766 labels and normalized inputs equal, with zero source calls and
no Email row/schema change. The real paired initializer now integrates that refresh; the latest full-copy
roundtrip proof is described below. Owner-labelled examples and the remaining original/model-input lifecycle
still prevent wholesale retirement. Historical thread context uses accurate
sources; unused classic/inclusion entrypoints have been removed. Source
availability and completed-input retirement remain separate acceptance items.


The real paired initializer now prepares the current selected external baseline
after the schema transaction and both normal cold constructors. A complete,
validated current baseline is reused without reading DB input text. Otherwise
migration captures the existing exact selected-input serialization once and
publishes it through the existing snapshot API, with no model/trigger/provider
call. It never hides a corrupt baseline or prunes files. Existing idle training
cleanup continues to own latest-per-type retention and active pins. The inverse
is emitted before schema COMMIT, so a later preparation failure still gives the
parent the complete current-data inverse. Full-copy forward, post-forward dataset
publication, inverse and real-child reforward preserved all snapshot metadata,
the new external payload and all 3,766 current inputs; reforward reused the new
baseline. This is copied-data evidence, not production deployment.


The final candidate execution EmailContextSource reads the complete related
thread through one EmailSourceReader account batch, retaining durable relation
order and prior receipts. Every trigger/history text and attachment list comes
from the exact external original; it no longer returns the database body when
source lookup or connectivity fails. SourceReader still checks current locator
metadata after IO and releases the source lease on failure. This is local
execution-context integration; Store relation discovery and scan production are
metadata/transient-source based, and missing-task recovery uses the exact source
reader as described below. Full Email input retirement remains incomplete and
production remains unpublished.


Final candidate Email enqueue routes retain task/action only; their unused
prebuilt execution context is removed. Scan production uses the current
transient original without reading historical stored bodies or leasing the
same account connector recursively. The consumer loads the complete accurate
thread at execution. Store thread discovery selects only actual metadata
columns and keeps the original relation closure/order. Runtime get_classification
also returns metadata/plan/recipients only; Console original text comes from
its explicit source reader. Missing model-action repair reads via the exact LOW
source reader, never a neighboring-UID scan or body-cache reconstruction.
Unavailable originals report the existing conflict and retain the missing-task
candidate; a later available source can repair it idempotently. Completed-task
receipt recovery remains unchanged. The existing continuation validation runs
when execution context is loaded. This candidate remains unpublished; final
Email cache retirement and source coverage are still open.


The candidate explicit unsubscribe cancellation writer now uses the existing
Task8 payload SET and public reply-task identity. It had still referenced
retired physical status/error columns and a physical-id filter. The change
preserves the same locked immediate transaction, cancellation decision,
idempotency and absence of a provider unsubscribe receipt; it adds no new
policy. Focused cancellation and tampered-input regressions pass on the final
schema. No production deployment has occurred.


The final candidate retires the unused pre-frozen training entrypoints
(train_and_promote, its registry/path wrappers, assess_feedback_readiness and
retrain_if_due), plus their Store input-list/inclusion-promotion APIs. No app
caller remains after the training source integration. Actual frozen classic and
embedding candidate training, registry primitives, staged publication/activation
and owner-labelled similar-case retrieval remain. The learning status endpoint
uses count_unincluded_training_examples with the same eligibility predicate,
returning a number without materializing training text or digest records. This
changes no schema or promotion algorithm and deletes no stored inputs. The
latest exact selected-training serializer remains migration-only. Original
Email listing implementations are now test-only; input/cache lifecycle still
requires completion.


Email list search keeps the locally typed text when its own debounced query is
committed to the URL; external query navigation and browser back/forward still
resynchronize the input from the URL. This prevents an in-flight URL update
from replacing newer text entered during the debounce interval.

The final candidate no longer exposes Store.list_classifications,
Store.list_unsubscribe_classifications or the stored-body SQL search helper.
HTTP list/detail/search already use EmailClassificationReader and exact external
sources. The unchanged old SQL is retained only in the tests/support listing
Oracle to compare source-backed visible text/search totals/order/paging and
business filters; application code never imports it. Runtime metadata queries,
recipient facts and action membership remain. No original/input row or schema
is removed by this code retirement.

Stable-identity classification lookup now selects the returned classification metadata directly from canonical emails, excluding model input and message body. Current ActionPlan hydration and corruption checks are unchanged; this read-path change does not retire stored originals.

Native source coordinates support directly injected prompt text only when both locator and reader explicitly select `plain_utf8`; ordinary business source reads continue to require `json_string_literal_utf8`. Both modes verify the selected CLI-owned line hash, string leaf, exact character slice, original UTF-8 byte length and digest. A direct-text field reference proves that field, independently of whether a historical full-turn end watermark remains available. No native text is copied to SQLite or another transcript.

Meeting jobs in sent/skipped compact scheduled consumer prompt and Skill originals after the terminal write when exact sources are readable. Native coordinates from an actual meeting attempt are checked first; only then may the existing scheduled-run snapshot and exact managed revisions/Operation Skill files prove a complete byte match using the existing materialization renderer. Short discovery configuration and source identity remain; an unproved or changed version retains its original input. File access and text decoding failures also retain the original input; database failures remain explicit errors. The original source JSON and terminal status are compared during the update. Replayable and active jobs retain their inputs. The paired initializer runs one explicit historical terminal batch after Store cold open; no periodic history sweep or delivery/receipt decision is added. Candidate and production acceptance is recorded in database-redesign-final-acceptance.md.


The final candidate stores a single-use complete Email original reference inline
in emails.message_source_reference_json (full UTF-8 hash/length, exact native or
provider location and serialization form). The explicit Store compactor first
checks actual native task attempts, then verifies the accurate provider original.
Only processed rows without unfinished classification/reply/action/dispatch
input dependencies can retire their original bytes; missing/changed sources keep
the bytes. The update compares the original, state, source clocks and current
plan, preserving model features, business results and receipts. Native original references remain authoritative byte proofs; Console views and
structured execution context obtain real provider headers for recipient roles
and validate the complete referenced version. A body-only native field never
stands in for To/Cc metadata.
No original cache or transcript copy is created. Completion callbacks invoke the
shared exact input compactor. Body and model input proofs are independent optional
components of the existing message_source_reference_json: the body proof uses the
root fields, while model_input holds the exact native coordinates or the actual
classifier feature serializer and provider locator, full SHA-256 and byte length.
A body match alone cannot prove model features, which include headers. Unproved
inputs retain their bytes. Retiring identical model bytes preserves current model
inclusion in the same transaction; an actual later model input update removes
only its old model proof, and an actual body update preserves an independent model
proof. No-op input updates keep their proof. Owner-labelled similar examples read
exact retired features from their native/provider source outside SQLite, retaining
the existing ranking and snippets. If a bounded historical example batch cannot
be read from its exact provider source, those supplemental examples are omitted;
they do not fail classification of the separately verified current message.
Owner example stamps digest the existing identity, sender, subject, confirmed
category, update clock and complete model-input hash from one row snapshot.
A same-second relabel invalidates the existing in-memory index and is detected
across source reads; retiring the same input bytes preserves the stamp. This
uses no new database fields, stored index, input cache or provider reads for
stamp comparison.
Finite multi-message reads use native UID-set FETCH grouped by mailbox and
UIDVALIDITY, with at most 50 identities per group. Metadata records bind each
complete literal to its own UID, including UID suffixes after the literal;
literal bytes are never parsed as metadata. The existing complete selected
text/HTML decoder and attachment exclusion remain shared with scalar reads.
Each group checks UIDVALIDITY before and after; each message checks its stable
identity and optional Message-ID. SourceReader retains the existing exact-folder
relocation and post-read location checks. Missing, ambiguous or changed originals
remain explicit errors; there is no partial Console response or persisted cache.
Native-only proof lookup is also available separately so an explicit historical
audit can check actual native records before grouping necessary provider reads.
The schema inverse prepares both exact input components before opening its write
lease and restores original bytes with current business facts and inclusion.
Historical input retirement and final release acceptance remain incomplete.
This candidate is not deployed.

Historical retirement can explicitly publish already verified input references
with `python -m scripts.retire_verified_email_inputs --source <audited-copy>
--target <current-db>`; `--apply` performs the writes and the default is a
read-only preview. Both databases use the final pair. Each target identity and
its existing completion predicate are read again inside a short write transaction;
only complete matching input bytes are retired. Existing references and current
labels, inclusion, results, receipts and allocator waters remain. The tool reads
reference metadata from the copy and does not fetch original sources or copy
business facts. Original-source verification precedes this explicit operation;
it is not a runtime sweep or a replacement for source verification.

Email schema inverse prepares exact original bytes before its schema write lease.
An explicitly supplied existing backup contributes only matching original bytes;
newer/new messages require their own exact native/provider source. The current
reference is verified again before restoring the predecessor declaration. Current
business rows, generated results, public IDs and receipts are not rewound.
For source versions created after the historical migration proof snapshot, the
paired migration can derive exact native coordinates from source work-summary
input, its Task Agent run and its task runtime attempt. Complete byte validation
is mandatory; an unproved new version rejects migration. The explicit twenty
historical original retirements remain the only authorized unavailable versions.
The same-session result correction now submits the bounded validation errors and
brief preservation instructions, rather than replaying a generic catalogue of
Project, Task, Attention and source rules. A runtime-path rejection receives its
path rule only for that error. The initial Work Tracking Skill, strict result
validation, native schema, routes and correction budget are unchanged. Fixed-case
evidence and limits are in `docs/task-result-repair-prompt-validation.md`.


Hourly Quality 的 Memory 队列读取继续严格校验 Actions 完整声明；当前期望为最终 compact payload、canonical Email、Business／Email history 及 physical provenance 的现有声明。它不再使用已退休的中间 Actions 声明，未知对象仍拒绝，不增加运行时兼容分支。

History 按明确 source_tables 构建查询时，仅在包含 actions 或未限制来源时检查 Email publication；非邮件分页与失败列表不读取被排除的 Email／State manifest。direct-email History 的 source ID 来自 common Action，输出 source table 为 actions；`email_action` 仍是用户可选的语义类型，状态、分页和详情路由保持原行为。邮件分支仍执行原有准确 schema 检查。
