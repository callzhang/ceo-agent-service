# CEO Agent Service Architecture

## 数据库结构与迁移合同

当前生产基线为 Store `2026-10-21.1`／Email53，包含十张核心表、Email 迁移账本及三张 Action Entity 表，共 14 张普通表。生产运行时代码为 `87a34d299d5749af1af454bfdd01c48a936799a4`，PID 2903；健康、状态、队列、Attention 和 History 读回均为 HTTP 200。共同 Action、Receipt、Observer 分别有 15,472、18,621、506 条；本期退休的七个已填充来源类型均为零。原唯一 FK finding `runs(190042) -> tasks(1)` 保留。准确发布边界、读回与容量限制见[Phase 1 生产验收](action-entity-production-acceptance.md)。
显式 Store19.11／Email53 → Store19.12／Email53 工具重建 Actions，逐值保留四字段、类型、公开／物理身份、其他业务事实与回执及分配水位；schema inverse 恢复捕获的原声明与版本时钟并保留当前数据。fresh 来源没有四字段的所有权，若迁移后新增非中性值，逆迁明确拒绝而不删除事实。生产实际来源为 actual，具有这四字段。
历史 D 九字段退休工具仍供明确旧版本迁移与逆迁调用，已批准的九字段逆迁仍填中性默认值，原评估值不再保存。Email48→49、49→50 的必要历史 DDL／校验已移至显式 history 模块，不由正常构造器加载。Workbench 附件文件维护属于业务行为，保留。
当前验收见[运行时声明清理验证](runtime-current-schema-validation.md)。历史 GPT-6 原生链路证据见[此前发布验收](database-gpt6-release-validation.md)。
字段范围、逐字段映射与证据边界见[九字段映射](business-model-legacy-fields-mapping.md)及
[验证记录](business-model-legacy-fields-validation.md)。
B2b 观察提交、副本及原生 CLI 的独立证据见[专有动作验收](database-redesign-specialized-actions-validation.md)。
C1 来源证据见[C1 生命周期验收](business-model-task-lifecycle-validation.md)；本候选局部验证与剩余发布门槛见[Prepared Action 归属验收](database-redesign-prepared-actions-validation.md)。
[此前阶段验收](database-redesign-final-acceptance.md)只记录 Store14.1 的历史范围；
当前 C1／C2、A5 及 D 的范围见[业务简化计划](superpowers/plans/2026-10-19-business-model-simplification.md)，
不能据此前阶段结案推断本轮全部完成或容量已通过。

Action Entity Phase 1 已部署，当前生产即使用统一 Action owner：`actions` 的 `_kind=70` 保存共同 Action，`action_receipts` 保存逐次 provider/execution 事实，`action_observers` 保存多个 Run 的观察关系，typed identity map 保留精确旧身份。生产 Store `2026-10-21.1`／Email53 有 14 张普通表；创建、CAS 领取和结果更新由 `app.action_entity_storage` 提供，调用方持有事务。生产读回、准确退休清单、copy 容量结果和剩余路线见[Phase 1 生产验收](action-entity-production-acceptance.md)。这只表示第一外部 Action 单元完成，不表示全部 Actions 或数据库已统一。
字段映射见 `docs/action-entity-field-map.json`；`docs/action-entity-target.sql` 是初始字段合同夹具，当前完整 Store/Email 目标声明见 `docs/action-entity-current-target.sql`。

Tasks 合并二十种已有任务。七个结构列和十二个生命周期物理列可写；
`payload_json` 只保存当前 `TASK20_PAYLOAD_FIELDS`／`TASK20_LAYOUTS` 的业务值数组。
`_kind` 是唯一存储的类型，`record_type` 是只读生成名称；原公开字段、过滤、
唯一性和类型约束由生成列及既有索引提供。当前／claimed 输入指针和真实物理父子
关系保留；公开 ID、源 ROWID、独立分配水位、SQLite REAL/BLOB、负零及业务字段
内的 JSON 文本不改变。历史 `task_storage` 是迁移来源 oracle，不是运行时兼容层。

Store19.8 的 C1 将九种实际可执行 Task 的源 status、due、attempt、error、时间与
既有 owner／PID／generation／expiry 事实移到单一物理列；不存在的源字段保持
SQL NULL，另外十一种记录保持原业务状态且没有通用生命周期。物理列保存一个
原始 JSONB 标量，`lifecycle_*` 生成列按原源列 affinity 解码；唯一保存的 status 是源状态，通用状态
和 `meeting_phase` 只读派生。原始非最小 JSONB 标头同样保留；共同写入器在一次
Task UPDATE 内瞬时重建源数组、调用原生 jsonb_set，再拆回各唯一 owner。
该临时数组不落库，业务 payload 的根标头只保留自身 framing。

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

三个专业来源共同使用 `create_logical_run`／`claim_logical_run`／
`finish_logical_run`／`get_logical_run` 与同一恢复写入器。旧三套 begin／record／
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
保持 NULL，其 owner/expiry fence 不变。native 读取保留原 latest_task_ref、
latest_standalone_ref 和 exact_standalone_ref 选择；来源不可用继续明确返回不可用。

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
来源 sequence 行保持原 ROWID/name/value，并保留在 capture metadata 供 inverse 使用。该候选不包含
B3B2 inverse handoff producer、新 native Run 分配或全新事实 inverse，也未激活运行时。

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

Task3（2026-10-21）将 reviewed、direct-email、prepared-message 与 Sent 执行恢复切换到同一 Action owner。调用方保留原事务、审批、provider 核验和不确定结果恢复边界；prepared 的准备、claim、接受回执仍在各自原有提交边界。prepared Action 在真实发送调用时才记录 provider operation；prepared facts 不借用 Sent 或 provider 时钟，且 `prepared_receipt_id` 精确指向其接受回执，避免与同一 owner 的 Sent/canonical provider body 混淆。审查执行在每个 execution/action_index 保留一个可更新的当前观察，旧执行的观察与 provider receipt 仍独立保留。direct-email retry 复用原 Action identity 并追加各次执行观察。Sent 历史缺失的 canonical operation、target、digest 或 action identity 保持 NULL；已核验的 failed-Audit 恢复可把这些事实绑定到原 provider body receipt，不复制正文、不重发。canonical provider 结果通过 reference-only metadata receipt 直接指向当前 immutable body；普通 provider receipt 不会仅因 ID 较新而改变该绑定。复杂 unsubscribe 保留原专有流程；这次切换不改变任何审批、授权、核验或重试规则。

Task4（2026-10-21，候选）将 direct-email History、Attention、队列快照、重要邮件清单及邮件分类可观测性读取切换到 Action 与 Action Receipt。发送详情以 Action 的物理 ID、正文、准确 Sent 回执和 `observed_at` 展示；provider 正文只从该 Action 的 provider receipt 读取，缺失的 operation/target 显示为未提供，不合成来源事实。多个 Run observer 仍对应一条发送效果。撤回只更新同一 Action 的撤回字段，不新增发送或改写原发送回执；feedback token 从同一 Action 取得，并继续按其原 conversation/trigger 身份关联消息和反馈。邮件退订仍走其专有流程。

Task6B 的 Dashboard Email 队列从 `DIRECT_EMAIL_ACTION_SQL` 和当前分类投影读取 pending、processing 及按 worker 原 retry 谓词可重试的 failed Action；队列 ID 使用 common Action 物理 ID，标题、状态和空详情链接维持原 API 契约。Email Attention 的 common Action ID 以字符串输出，符合 `AttentionRow.id` 的严格响应类型；失败筛选和工作者状态投影保持现有语义。

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
`backfill-todo-owner-ids` 和 `repair-task-projects-apply` 已退休；Project 修复
`repair-task-projects-plan` 仍只生成清单。历史 Work Project／Work Todo／Work Update
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

原 `business_source_documents` 正文表及数字容器 ID 退休。Signal DAO 仅持久化
来源身份、精确版本、hash／字节数及 locator；一个 owner 直接内联，第二个 owner
在原 savepoint 中把同一精确来源提升到共享 provenance，保留 Signal ID 和事实。
推广失败时整个 savepoint 回滚。证据引语、来源关系和已采用业务事实仍保持原义。
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
tracks the exact node as Derek-owned and its current reply task has had no
update for more than one hour. A recent pending node is still visible in its
normal queue and History views; a pending scanner key without a matched task
timestamp is excluded because it cannot establish stalled work. This projection
does not change the OA process, task, or provider receipt.

Current Attention suppresses an older failed OA reply Attempt only after a
newer attempt for the same process is linked to its current Audit-approved
candidate and that candidate's `revert_task` execution is `done` with a verified
DWS `REDIRECT_PROCESS` receipt (including the DingTalk success response and
matching process identity). The failed Attempt remains failed in History; an
uncertain, incomplete, mismatched, or non-return receipt leaves it visible.
This is an Attention projection only and does not change task state, receipts,
or OA execution.

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
Codex Consumer/Audit 的角色工具清单只开放 `ai_services.ocr_document` 这一项 AI Services 工具；其他 AI Services 工具不随角色注入。普通 Consumer 和非 OA Audit 仍把 `mcp__agent_cli`、`mcp__ai_services` 列为 direct-only 命名空间。OA Consumer 与 OA Audit 将两者交给同一个 CodeMode JS 调用；OA Audit 的其他 MCP 保持 direct-only，内建命名空间不进入该调用：附件读取结果中的 base64 在工具内存中直接传给 OCR，模型只接收 OCR 文本与来源信息。只声明 direct-only 工具并不能避免原生运行时截断超长附件输出；工具审批与业务动作边界不因此改变。

本文档默认描述当前 Consumer Agent A / Audit Agent B 运行架构；明确标注为“已批准的
生命周期政策”的段落描述后续实现必须达到的目标，不表示对应代码已经切换、部署或在生产
启用。历史方案保留在 `docs/superpowers/` 中，仅用于追溯，不代表当前目标运行方式。

## 当前任务运行机制

钉钉发送核验只从发送结果的 `result` 读取新消息/发送任务身份，不递归扫描引用消息、
历史消息或 nextActions。存在 `result.openTaskId` 时必须查询该任务，即使同时返回了消息 ID；
只有原生查询的 `result.sendStatus=SUCCESS` 才算该异步任务投递成功。没有可查询任务时，
只有服务通过只读会话核验得到的 `dingtalk.reply-readback.v1` 证据可确认投递：同一会话、
完整分页的原会话窗口中唯一精确正文、引用原触发消息、当前账号发送、消息未撤回，且
核验正文摘要匹配。`im.message-list.v1` 解析会保留 `quotedMessage` 身份，供精确引用校验。
服务在比较正文时只移除自己添加的反馈回调尾缀，外部回执仍保留实际发送文本。单独的消息 ID、
processQueryKey、重复 UUID 接受及泛化的 finished 均不是投递证明。unknown、pending、
不完整或存在冲突的读回保持未知；读回找不到消息也不能单独授权重发。
对于严格完整、无失败的群聊窗口，零个精确匹配必须记为投递核验失败，并停止旧候选；不完整窗口或多个匹配继续保持 uncertain。任何后续消息都须生成新候选并经 Audit。
原生回复在投递未定时保存已接受的 provider 回执，冷恢复只重查同一任务，不再次发消息。
Consumer 在 Audit 前为可执行的钉钉聊天消息准备服务尾缀后的完整正文，并按任务、动作、代次和候选版本持久化。准备目标与执行器一致：回复要求会话和原消息 ID；显式群发要求会话；直接发送要求稳定收件人 ID；通用发送可以使用会话 ID。执行器只发送与审核候选正文完全相同的已准备内容。
所有 Consumer 共用不得直接发送对外消息、邮件或执行审批的指令。Audit 对声称已经由 Agent 直接执行这类动作的候选必须退回或拒绝；候选文本不能证明外部效果，只有服务的原生回执能作为送达证据。Audit 还要遵守来源或任务明确提出的交付范围：只要草稿或由用户自己发送时，退回或拒绝任何拟发送消息、邮件的候选；未限制发送的有效结构化外发提案仍可由 Audit 审核，并仅在审核通过后交由系统执行。这项规则不改变工具权限或执行器。
已显式纠正为未知的历史回执仍保留已接受效果，System 只能核对它，不能复用为 verified 或重发。

如果已审核的钉钉发送在准备正文一致性检查处失败，System 不会直接复用原动作键。新执行先用原动作目标、审核正文和原始派发时间，对钉钉做只读完整分页窗口读回；唯一精确命中按真实发送回执恢复，完整窗口零命中记录 `confirmed_no_effect` 后才可重试。单聊按稳定 userId/openDingTalkId 查询，群聊按会话 ID 查询。读回失败、不完整或有多条精确命中时不发送并保留失败，后续只能在新的 Consumer/Audit 候选中重新评估。
历史修正通过显式小批量维护完成，保留原始 provider 结果、之前的核验结论和状态；不改写 Agent
运行或历史 Attempt。当前任务的失败状态优先于历史 completed 展示。未知投递使用技术失败／
uncertain 执行状态，不生成管理者业务决策问题。

`reconcile-failed-reply-chat-readback --task-id ...` is an explicit, one-task
maintenance path for a historical reply whose send is already present in the
sent-reply ledger but whose provider task can no longer be queried. It reads
only that conversation around the UTC `sent_at` instant (SQLite's naive
`CURRENT_TIMESTAMP` is interpreted as UTC) and requires the exact
query range plus complete, untruncated, failure-free native history, one exact
outgoing body quoting the original trigger, and a sender matching the authenticated principal. `--apply` records that evidence
against the same approved candidate, external action and generation in one
CAS transaction, then settles the task. It never sends or comments. Preview is
read-only; incomplete, conflicting or multi-action evidence remains unresolved.

Consumer 与 Audit 的实际只读工具目录提供 `read_dingtalk_group_members` 和
`read_dingtalk_user_profiles`。群成员读取使用 DWS 原生有界自动分页，原样保留真人、
机器人、完整性、桶、续页和失败信息；企业资料读取只接收已核实的组织 userId，
保留原始组织资料字段，不按姓名自动补全，也不假设每份资料都含 title。群 openDingtalkId 不能当作组织
userId；身份关联必须由同一账号返回的稳定 ID 证明，空资料或 partial 仍是证据缺口。
这些读取不新增审核、发送门禁或业务来源绑定，也不改变既有外部动作权限。

本节描述实际进入 AgentOrchestrator 的运行契约。进入 AgentOrchestrator 的业务任务遵循“执行 Agent → 审核 Agent → 反馈/修正 → 再审核”的生命周期；领域任务替换输入和工具能力。WeChat task 3 保留 DecisionRunner/persistent Sender，Email 退订和确定性技术命令沿用独立系统直接路径。Consumer 可按业务 Skill 读取事实、准备材料并执行普通工具工作；系统注册的受控外部动作由 System Executor 根据持久化且审核通过的精确方案执行和回读。服务保存候选、审核、选择、执行及回执事实。

Codex Consumer 使用按当前 purpose 选择的原生输出 Schema（general 或 OA）；Codex 本地 OAuth Audit 使用从现有 Audit wire contract 生成的原生 Schema。服务 API 提供商没有证实原生 Schema 兼容性时，Audit 保留现有无原生 Schema 路径。两者的动态 JSON 对象通过键值条目表示，并在原有严格 Pydantic 校验前无损还原。Audit Schema 保留 outcome 与 feedback 的现有条件关系；候选 revision/digest 仍由服务从本轮 AuditTurnContext 绑定，候选字段断言仍执行原校验。字段断言路径不存在时，修正反馈会指出准确路径和该父对象可用的字段名或数组索引，不回显候选字段值；候选绑定、断言校验和审核决定不变。该传输层不改变结果字段、业务判断、SystemExecutor 外部动作流程或 Consumer 直发消息/邮件禁令；Claude 与 Friday 输出路径保持原样。

微信单聊 producer 只把有可读正文的入站文本作为回复候选。每次 Reader 扫描按消息时间把相隔不超过五分钟的候选合成一段；只有该段最后一条消息已安静满五分钟，才以它的原始消息 ID 创建一条回复任务。未结束的段不推进读取水位，因此跨定时扫描到达的后续文字仍可合并；相隔超过五分钟的段分别处理。群聊仍只按精确的当前账号 @ 入队，手动重跑和 Sender 投递不经过这一步重新合并。

已发送的微信 Attempt 不能从普通重跑入口再次入队。历史详情仅对当前最新的已发送 Attempt、且原始输入未被精简时提供独立的“再次发送”操作；操作展示原消息内容和 ID，并把该 ID 随请求提交。服务在同一事务中核对来源 Attempt、会话、原消息 ID、最新 Attempt 与之前的再次发送记录，然后创建新执行代际。相同来源在新代际待处理时重复提交复用它，完成后不能再次使用旧 Attempt 发起。精简后缺少可执行原文的历史任务不提供该操作。失败 Attempt 的普通重跑、其他渠道和现有 Sender 投递规则不变。

```text
pending -> processing -> done
                     -> failed
                     -> needs_human
                     -> skipped
```

- `pending`：任务已持久化，等待执行。
- `processing`：正在执行或正处于“审核 Agent 要求修改、执行 Agent 重跑”的反馈闭环中；task 在整个闭环期间都停留在这个状态，不会切换到单独的“待反馈”“待修正”状态——修改请求本身记在 `AuditAgentResult.feedback`（`AuditFeedback.rule/observation/requested_revision`），修正次数记在 `proposal_revision` 计数器上。
- `done`：任务逻辑完成且结果已持久化。
- `skipped`：判定为不可执行或不必执行，主动收口，不再重试。
- `needs_human`：信息完整但这一类任务缺少可复用规则，且质量门槛允许升级；必须有 needs_human_reason、decision_basis 和 2–4 个互斥规则选项，均标记 `applies_to=task_class`。不能让 Derek 决定当前具体事项。当前事实缺失或需要向来源方确认时使用普通、边界明确的 proposal；技术失败不能成为管理决策。执行分支绑定完整方案，停止分支写明 skipped 和原因；未经审核或旧无绑定选项不可执行。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败，并保留失败阶段和原因。

Attempt History 的“需要你处理/选择处理方式”只对应当前可操作的 `needs_human` 规则候选。
当前 `failed` 即使重试耗尽，也继续显示为执行失败，提供重试或技术详情；不得把技术失败、
候选绑定错误或外部执行错误伪装成业务规则决策，也不得提供“人工处理/暂不处理”作为替代决策。

如果 DingTalk trigger 引用了本服务通过 provider 回执确认已发送的 Agent 消息，Consumer context 会携带结构化来源标志；此类 Agent 发起的续接/恢复任务不得进入 `needs_human`。该标志以 conversation、quoted message ID 与成功发送状态回读匹配，不靠正文签名猜测；Consumer 生成校验与 Audit 独立复核都会拒绝该升级，要求 Agent 自主判断、补充有边界的 proposal 或向事实来源方 ask-back。

审核闭环如下（task 状态全程停留在 `processing`，直到最终收口为 `done`/`failed`/`needs_human`/`skipped`）：

```text
执行 Agent 生成 R0
  -> 审核 Agent 审核 R0
      -> 通过：System Executor 执行持久化的 R0，provider 回执与实际回读确认后完成
      -> 需要修改：审核 Agent 写入 F0，R0 保留
          -> 执行 Agent 收到 F0，生成 R1
              -> 审核 Agent 审核 R1
```

### 主页面执行入口
Workbench 的同步执行线程在一个 turn 内复用一条 SQLite 连接，减少大 schema 的重复装载；每次 Store 操作仍独立提交或回滚，turn 结束时关闭连接。心跳和 runtime 回调线程各自打开连接；只读快照仍使用独立的只读连接，不跨线程共享连接。


`/` 主页面是 Service 的 Web 入口，不是独立 Agent Runtime。主页面创建的 turn 作为
`workbench` workload 进入统一 `RoutedCodexExecution`，与后台 Agent 共用模型路由、会话、
runtime attempt、失败切换和 CLI 原生 `auto_review`。页面后端只把统一运行事件投影为时间线并
维护停止请求；不得自行构造 provider 命令、启用 approval bypass，或实现第二套命令审批。

CLI 原生 `auto_review` 是 Codex 对 `codex-auto-review` 模型的一次额外调用，只有 OpenAI 托管路由
（`codex_oauth`）提供该模型。`service_api` 路由（第三方 OpenAI 兼容 provider，如 MiniMax）上不存在该
模型，审批请求会被 provider 以 unknown model 拒绝，进而拒绝所有需审批的动作；因此 turn runner 在
`service_api` 路由上把 `approval_policy` 设为 `never` 并去掉 reviewer：沙箱保留，不再请求审批。

旧 `workbench_confirmations` 表、等待确认状态、确认事件与执行逻辑、确认／取消 API 和前端资源已从当前开发版本移除。原生 CLI 的审批机制保持不变。

审核 Agent 只能反馈规则、观察结果和具体修改要求，不能直接改写执行 Agent 的业务正文。执行 Agent 必须基于反馈生成新 revision；原 run 不覆盖、不删除。一个任务最多允许三个内容反馈周期，基础设施失败不消耗反馈周期。反馈次数耗尽本身是自动闭环失败，不是人工决策依据。Consumer 和 Audit 必须按固定顺序对新候选应用质量门槛：`information_completeness < 0.5` 时先 ask_back；否则仅 `(risk=high 且 confidence < 0.5) 或 rule_coverage < 0.5` 才可进入 `needs_human`；其他情况按适用规则自主处理。Consumer 的新结果类型化校验会拒绝不满足门槛的 `needs_human`；Audit 在审核候选前及校验其绑定结果时独立重算同一门槛，不符合时拒绝该新候选。已持久化候选在恢复、拒绝分支比较、已审核动作执行及历史读回时按历史证据解析，不重新套用新准入门槛；这保留原始 run 与已审核决定，且不会重新执行已确认动作。历史解析不能让新模型输出绕过 Consumer/Audit 校验。Audit 必须核对新结果分类与四项分值一致；不满足门槛的人工升级不得通过。

消息发送回执仍为 `uncertain` 时，后续 Audit 批准的 Consumer `no_action` 不得关闭任务；编排器继续以原不确定执行为依据向 Consumer 提供回执核对反馈。若在三个内容反馈周期后仍未形成可审计的新提案，任务以 `external_action_uncertain` 失败并保留 Attention；来源审批已终止时仍由精确 OA 终态核对路径将任务置为 `skipped`，不误报送达或要求人工处理。完整负向回读只证明原消息未送达，任何新消息仍须形成新候选并经过 Audit。

修订后的 Audit 输入从当前 Consumer 的持久父链读取促成该修订的已采用 return/reject，传入原 proposal_revision、candidate_digest 和完整 feedback。该段在当前事实和候选之前，明确只是修订背景，不是事实来源或业务授权；不复制旧 summary 或分值。Audit 对照上次要求复核当前候选，先前要求本身有误时应说明依据并纠正，遵从反馈不能证明事实成立。初轮和已批准执行后的新阶段不带该段；进程恢复及技术重试从同一采用记录恢复，不依赖原生轨迹可用性。三轮上限、候选绑定、状态和外部动作权限保持原样。

恢复已完成的 Audit runtime 结果时仍执行相同的敏感值校验；校验失败记为结果校验失败，不记为 Codex/provider 进程故障。拒绝规则不放宽，失败结果不会因此被采用或执行。

所有任务都禁止使用 `discard` 动作或写入 `discarded` 状态。无需动作的结果在 trace 记录 `no_action` 后进入 `done`；需要修正时由审核 Agent 写入 `audit_feedback`，执行 Agent 生成新 revision；处理失败使用 `failed`；无法自动解决使用 `needs_human`。

`okr_review` 使用上述闭环生成逐 KR 评审；管理者 OKR 周报评分范围是当前 OKR 周期起始日至报告日，本周窗口只标记新增进展和风险。每位名单成员都必须保留一个区块，但只有可用的实时目标和有效分析才产生评分；个人季度或目标确证缺失、已验证来源中的成员数据故障以及终态结果校验失败使用系统绑定成员 ID 的未评分缺口。不得以零分、上季度目标或省略成员填补缺口。共享认证、未配置来源、未分类来源故障或身份/权限证明无效仍阻断发布，不按失败人数推断共享故障；明确的成员级失败即使覆盖全名单，也只能形成如实的缺口报告。原始回执和诊断保留在本地来源材料及任务记录中，不把历史季度指标、凭据、路径或 provider 正文放入管理报告。

名单覆盖以稳定 user ID 关联评分、来源和缺口；同名不同成员仍保留独立区块及附录，现有唯一姓名附录的文档身份不变。有效评分保留原 KR、四项领导力和三项文化维度校验及计算公式。OKR 分析使用普通 scheduled Agent 生命周期和 task generation 产物，来源变更后重新分析；模型调用沿用统一应急上限与 idle watchdog。评分、文档及群摘要回执是不同完成级别；完整名单缺口报告已发布不等于所有成员已完成评分。调度、重试、租约与恢复由普通任务机制负责。

OKR 评审的实时数据读取由业务 Skill 选择当前可用的 provider 能力完成。服务提供的
`app.cli read-dingteam-okr --user-id <owner-id> --period-label <period>`，该入口调用
`CEO_OKR_LIVE_SOURCE_COMMAND`（应指向服务 checkout 中的 `scripts/dingteam_okr_headless_source.py`；
旧 Skill 浏览器入口不会把完整个人季度缺席转成成员级收据），并返回包含
`processed.objectives`、`processed.okrRows` 的实时载荷，或经完整个人周期列表验证的
`availability.status=goals_not_established` 收据；它是一种可用实现而非应用层命令契约。
Codex 路线将服务进程配置的该命令传给 `agent_cli` 子进程，避免子进程因环境筛选回读旧 `.env` 入口。

即时叮当 OKR 评分与定时周报分析是两种任务。普通 Consumer 可用
`read_okr_member` 和 `read_dingtang_okr_score_detail` 取得实时目标、当前评分批次、
KR 与正式评价人 ID；评分动作只以 `dingtang-okr/score_kr` 的精确目标、0–1 分数和评语
提交候选。Audit 审核后 SystemExecutor 才在服务的无头 Chrome 页面内注入 JS 调用
页面原生 `makeScore`。该接口由登录用户决定评分归属，提交体没有评价人或批次字段；
执行前须核对登录用户就是候选评价人、周期名称与唯一当前批次相符、该周期使用 KR 评分，
且评分详情中的周期、目标、KR 与评价人均对应候选。执行后重新读取
`getScoreDetail`，只有同一评价人的分数、原评语和 `complete` 状态全部相符才记为已完成。
评分页面的 `getScoreDetail` 与 `makeScore` 请求沿用页面认证头，但把该接口所需的
`X-Space-Id` 置空；周期、批次等其他读取仍保持页面原请求。只读评分详情若收到
提供方对象错误，服务只传出数值 code 和消息，经既有脱敏后报告失败，不传 traceId。
页面目标读取成功只证明目标可评分，不代表已提交或完成评分。
写入结果不明时只做读取核对，不对已发出的评分自动重发；Consumer/Audit 不直接写分。

逐 KR 评审的 `StructuredCodexRunner` 通过专用 `app/schemas/okr_review.schema.json`
向原生 CLI 声明输出结构。该快照保留通用 AgentEnvelope 的外围契约，并将
`domain_payload` 固定为现有 `OkrReviewPayload`、`OkrReviewItem`、`OkrEvidence`
模型的完整字段；所有 object 均声明具体 properties、全部 required 字段及
`additionalProperties: false`。通用 envelope 的解析和 OKR 领域结果处理保持原有契约。

Consumer 形成通过/不通过判断时应使用当前 OKR 数据；截图、仓库链接或重试终态不能替代实时读取，
读取失败时必须保留底层认证、浏览器启动或源端错误码。Dingteam OKR 只有在检查缓存 token 未过期后才复用；过期缓存进入既有串行 Chrome 刷新流程，通过 `app.service_browser.launch_service_chrome` 和每日 Chrome cookie 副本启动真 Chrome，并沿用现有本机 DingTalk SSO。仅验证有效的 token 才返回或写入缓存；登录仍失败时保留真实认证失败，不绕过认证。不得另起 bundled Chromium 或专用 CDP 资料目录。
本机 SSO 在登录页存在“扫码登录”标签时优先使用它、已登录账号头像和原生确认流程，再选择目标组织；只有扫码入口不存在时才尝试直接账号入口，因为页面上可见的“立即登录”也可能被遮罩挡住。组织选择器可能继续使用登录 URL；若登录过程已直接跳回 OKR 入口则按页面导航完成处理。选择器缺失或页面未进入组织/OKR 页面时，保留浏览器的实际错误，不吞掉异常并伪报通用会话过期。
多个评审或维护任务同时遇到缓存过期时，headless source 只允许一个调用刷新认证；其他调用在取得刷新锁后重新读取缓存并复用结果，不能因正常刷新耗时产生并发锁错误。
headless source 还会在调用 OKR API 前校验新捕获凭据的有效期；专用浏览器会话过期必须明确报告会话需要重新登录，不能误投影为“没有该 OKR 周期”。

完整状态和恢复说明见 [`docs/runtime-mechanism.md`](runtime-mechanism.md)。
错误码解释统一见 [`docs/error-catalog.md`](error-catalog.md)。

### Agent Cron 与统一 Dispatcher

用户可见的定时任务是 `任务描述 + Cron + Agent 能力（结构化 Skill 引用）+ Runtime`。任务描述是所有
定时任务共有的可读用途说明；Agent 任务另有执行提示词，服务命令不把描述伪装成 Agent prompt。Scheduler 维护
下一个触发点，并把一次触发保存为 `scheduled_task_run`；它不执行领域业务，
也不把 Consumer/Audit 的完成或失败复制回调度记录。若 SQLite 写入因 `BUSY`/`LOCKED` 冲突在
存储层有限重试后仍失败，Scheduler 保留该触发点，等待 1 秒再重试；该数据库错误不会被记录为
运行结果，也不会让 Service worker 退出。Dispatcher 先领取该 trigger，原子创建
唯一的 `channel=scheduled` execution source，并在 trigger 上保存
`execution_kind + execution_id`。之后 Scheduled Agent Consumer 从这条不可变输入进入标准
Consumer → Audit → feedback revision 生命周期：

若 Scheduler 因首选 Runtime 暂时不可达而无法分派已到期触发，它保留触发计划，不创建终态
`skipped` 运行；路由恢复后以原计划时间派发一次。多次错过时仍合并为最新的错过时间点。静态
配置错误仍按原规则写入 `skipped` 和 Attention；已有执行未终态时也保留补跑计划，待前次收口后派发。

```text
scheduled_tasks
  -> scheduled_task_runs (trigger fact)
  -> Dispatcher: scheduled adapter
  -> reply_tasks[channel=scheduled] (exact active input; settled source provenance)
  -> Dispatcher: scheduled_execution adapter
  -> Consumer Agent -> Audit Agent -> feedback/revision
  -> reply_attempt / agent_runs / provider facts
```

Scheduled Agent Audit also receives compact service projections of completed
`consumer_document_write` returns from completed Consumer runs in the exact
scheduled task execution generation, including earlier revisions after Audit
feedback. Each projection contains the returned document identity and a digest
and byte count of the returned readback object; it omits the report body itself.
An empty list means no matching return was captured in that generation. This
establishes what the service tool returned, not whether the document or its
claims are correct. The behavior is shared by scheduled Agent tasks and does
not change Audit's review standard.

定时任务有两种执行形式，由 `scheduled_tasks.command` 区分。`command` 为空的是 Agent 任务，
走上面的完整路径。`command` 非空的是服务命令任务：它只声明服务命令目录中的一个名字（当前是
DingTalk 消息、DingTalk 近期消息恢复、微信消息、会议、OA、工作来源、受限 AI 听记权限申请和 AI 听记同步），不需要
Runtime、Skill 或工作目录。判断标准是这次执行本身有没有判断空间：确定性的发现或同步工作属于
服务命令，需要 Skill 判断的才是 Agent 任务。AI 听记同步的分页读取、归档和内容游标都由
`app/minutes_sync.py` 确定性完成，因此它也是服务命令。
“工作来源”扫描还会只读发现管理周报、项目管理部周报和部门产研周报，按文档 ID、链接、统计周期和内容 digest
建立 Task-first `work_summary` 输入；同一文档版本重复扫描保持幂等，报告内容仍由 Task Agent 提取 Project 和 Task。
这些输入的逻辑 `payload_json` 对较大内容使用带版本标记的 zlib BLOB，小内容仍用 JSON 文本；最终 Tasks 向量按原 SQLite 值类型编码该字段，应用模型和离线导入/修复入口统一还原为原始 JSON 文本。Attention 的 JSON 字段投影在该查询连接上使用同一解码器；副本回放比较准确解码正文后，CAS 绑定当前投影返回的精确 TEXT/BLOB 值，通过当前 Tasks 写入器沿用原编码器。迁移命令 `python -m app.work_summary_payload --db <database>` 按小批事务压缩仍需保留正文的非终态行；`done` 和 `skipped` 行继续使用终态精简，命令可重复运行、不删除记录且不执行 VACUUM，必须在维护窗口及已验证备份后单独执行。
DingTalk 消息增量扫描与近期恢复扫描共享 producer 锁以避免并发读取；若恢复扫描仍占锁，分钟级消息或日历扫描立即以 `deferred=producer_busy` 返回，并由下一次定时触发补扫，不排队等待长恢复任务结束。恢复扫描仍等待已开始的短增量扫描完成后再运行，队列按原消息身份保持幂等。
scheduled adapter 领取 trigger 后，Dispatcher 在本进程内直接运行该命令；成功时把
`service_command + 命令名` 记为 trigger 的 execution link、保存命令返回的单行结果摘要并标记 `dispatched`，失败时 trigger
以 `failed` 结束并进入 Attention。服务命令任务不创建 synthetic scheduled reply task、agent run
或 reply_attempt；命令发现真实对象后，才由既有 reply、meeting 或 work-summary Consumer 处理。
运行记录 API 和定时任务页面展示已保存的结果摘要；旧运行记录的摘要为空。这样服务命令即使没有
创建 Agent Attempt，也能显示本次扫描数量和结果。页面把结果里的 `key=数字` 读成中文计数（入队、发现、失败……），原始摘要、trigger id 和执行链接收进每行的“技术详情”。连续的定时触发如果什么都没带出来——没有 Attempt，且结果计数除上下文项（邮箱数、会话剩余天数）外全为 0，或摘要为空，或因上一轮未结束而跳过——合并成一行“N 次定时检查都没有新内容”；手动运行、失败和有计数的运行始终单独成行。没有新对象时不会在消息历史里留下每分钟一条的记录。`scheduled-task-options` 为每个服务
命令附带一份从服务状态计算的只读“下游”描述（通道、consumer 执行器、角色边界常量、实际加载的
Skill、consumer 要求的 Runtime 能力和路由可用性），页面据此说明命令发现的消息会被谁处理：

```text
scheduled_tasks[command]
  -> scheduled_task_runs (trigger fact)
  -> Dispatcher: scheduled adapter runs the service command in-process
  -> trigger: dispatched + service_command link, or failed + Attention
```

OA 的钉钉系统通知和聊天催办是外部观察事件，不是第二条审批执行入口。消息生产器将
`OA审批` 系统通知以及 `[Ding]…提醒您审批` 聊天消息写入 `oa_notification_events`；前者只
留痕，后者保存原会话和原消息作为结果回传目标，二者都不创建 Agent run。OA 定时扫描读取
`processInstanceId` 的实时节点：只有当前用户恰好有一个运行中 `taskId` 时，才把无
`taskId` 事件归并到该 OA task；多个候选或无法解析时不猜测。审批 task 完成并收到 provider
回执后，服务使用独立的 `oa-reminder-result:{event_id}:{attempt_id}` 投递键通过原生引用回复
催办人。结果回复有自己的事件状态和回执，不改变 OA 审批 Attempt 的业务结论，也不会重新
启动一个聊天 Agent。

服务启动或主机从睡眠恢复后，调度层补最近的一个已错过触发；更早的错过点合并到该次执行，
避免恢复时突发排出多份历史报告。任务创建时间之前的触发不会补造，已有触发继续由唯一键去重。
若补跑时上一轮仍未终态，Scheduler 保留该补跑时间点，待上一轮终态后再提交；正常到期的
触发仍按 `skipped` 收口，不会并行创建第二个执行输入。手动运行会创建独立 trigger，但不移动
正常计划。CEO 日报默认按 `America/Los_Angeles` 每天 06:00 触发，OKR 管理周报默认按
`America/Los_Angeles` 每周日 03:00 触发，避免夏令时切换后因固定北京时间而偏移一小时。
首次迁移只改仍匹配旧仓库默认周期与时区的任务；已自定义周期或时区的任务不变，历史运行继续引用原配置快照。
任务指定的 Runtime route 是
**首选**而不是唯一线路（Derek 2026-09-24）：执行先上首选线路，失败时走与其他 Agent 轮次相同的统一
fallback（`app/runtime_fallback.py`：满载先同线路重试，再暂停该线路、按配置顺序换下一条）；任务的
model、thinking 只作用于首选线路。managed Skill 必须绑定精确、已加载且启用的 revision。派发前按
「首选 + 其余配置线路」检查，只有这些线路都不可用时 trigger 才为 `skipped`；Runtime 未配置、缺少能力、
认证暂停或 Skill revision 不可用属于配置性不可用，每次进入 Attention；provider 暂时不可用导致
的路由暂停（过载、传输断连）只体现为 run 记录和路由暂停状态，不按每次触发写 Attention。
Codex provider 错误事件报告的最终 HTTP 429 按已有满载路线处理：先有界地同路线重试；
失败 attempt 已观察到 session 时，使用原生 `codex exec resume` 续接并发送 `continue`，
包括冷启动首轮已创建 session 后才返回 429 的情况。未观察到 session 时仍提交原任务输入；
工具结果正文出现 429 不触发此分类。
派发后的执行可用性失败记录在 execution source，trigger 仍只表示已经派发。

统一 Consumer Dispatcher 不建立第二套业务队列表。各 Queue Adapter 直接领取现有事实来源：
scheduled trigger、scheduled execution、普通 reply、meeting、work summary、OKR review、
当前 BusinessTask 的 DingTalk Todo outbox 和任务长期记忆写入（`task_memory_write`）。统一层只处理唤醒、公平领取、租约、全局 Agent 容量和分发；领域 Consumer
继续负责自己的生命周期和外部事实。Dispatcher 的有界等待是跨进程恢复机制，不是用户 Cron，
也没有用户可编辑的 polling/settle 设置。空队列只显示零指标，不生成 run。

Dispatcher 完成租约后仍在同一事务保留最新 10,000 条终态租约与所有活动租约；同一完成时间按
rowid 倒序排序。终态时间的必需部分索引定位第 10,000 条边界，删除严格早于该时间／rowid 的
记录，避免每次完成都在共享写锁内排序全表并构造一万行成员列表。存量 schema 初始化补齐该索引，
不重写租约记录。共享 State 的受保护更新按已读取的数值 source rowid 使用现有唯一表达式索引定位，
避免完成租约时扫描同类记录；领取、CAS 与同事务保留规则不变。

启动时以稳定 migration key 幂等创建默认任务，清单以 `app/agent_cron/seeds.py::seed_scheduled_tasks`
为准（当前 14 项）：分类新邮件、处理新的钉钉消息、处理新的钉钉日历邀请、补查遗漏的钉钉消息和
日历更新、同步会议结论与管理者视角、处理已授权会话的新微信消息、处理新的钉钉 OA 审批、将会议
行动项整理到 Tasks、生成并发送每周 OKR 管理周报、准备 CEO 管理周报、发送 CEO 每日总结、同步
Chrome 登录态、申请读不到的钉钉 AI 听记，以及下载新增的钉钉 AI 听记（每天 `20:00`，`Asia/Shanghai`）。
默认定义保留服务命令与普通 Agent 两种执行形式；以稳定 migration key 原地升级默认配置，
保留名称、Cron、时区和用户修改。已删除的旧任务不动，服务命令通过 Console API 不可修改。新安装创建的全部
默认任务都是**暂停**状态，由用户配好连接器后自行启用（Derek 2026-09-23）；seed 从不改变已有任务的
启用状态，包括从未被编辑过的 version 1 任务。默认的名称与 Cron 与负责人本机的现行任务一致。Lark
不创建默认 seed；其余已有任务的用户修改不会被 seed 覆盖。

定时任务共用现有调度、队列、Consumer → Audit → revision 生命周期；14 个默认任务由声明表生成，默认新安装仍暂停。11 项发现/同步任务保留服务命令，CEO 日报、CEO 管理周报和 OKR 管理周报是三项普通 Agent 任务。业务要求由任务 prompt 与 Skill 决定，系统层不按报告名称或 Skill 名称分支，也不以 `publishable` 判断业务质量。通用 `read_scheduled_context` 返回触发时刻、时区、版本及历史完成事实；Consumer 还会收到当前 task generation 中已有产物的文件名和 SHA-256 清单，不包含文件正文，以便重试先复用当前执行已有材料。`read_service_activity` 按调用者明确给出的时间窗口和来源返回服务事实；`read_meeting_archive` 按会议身份读取归档。Codex 与 Claude 均可通过 Consumer 的 `consumer_run_skill_script` 执行所选 Skill 的确定性 Python 脚本。脚本在当前 task generation 的产物目录运行；产物读回分页，完整正文留在本地产物中。

产物 offset 是从0起的字符索引；每页统一返回 offset、total_chars、next_offset，sha256 始终对应整份文件。完整读取从0开始，按 next_offset 续读至 null，不能把非零起点的尾部当作全文。

`consumer_document_write` 接受正文产物、标题和显式目标；更新使用 node_id，创建使用 parent_id 与 workspace_id。JSONML 由原生解析器检查，更新保留 revision 校验与版本保存，创建前核对同名目标。返回 provider 结果及完整读回产物的引用；Agent/Audit 读取全部分页或使用本地脚本比较。消息继续通过既有已审核投递通道发送，普通文档工作与受控消息动作沿用原角色契约。

日报窗口由 `ceo-daily-report/scripts/window.py` 按上次真实完成的历史事实计算，漏发并入下一份，同日重跑沿用原起点。日报位置与通知内容归该 Skill。Consumer 先读取服务事实和群消息；仅当会议管理细节仍缺失时，才读取本任务冻结的 `dingtalk-minutes` Skill 并查询听记摘要。管理周报的周一窗口、目标/上期文档、空白模板策略和三处内容更新归 `ceo-weekly-report` 的材料/渲染脚本；渲染器只检查实际读取字段与 JSONML，固定指标、材料覆盖与章节等业务要求由 Skill 和 Audit 判断。三处写入仍为 CEO 判断、公司指标和新增跟踪行。

OKR 报告的应用层只读取明确群名的稳定名单和明确人员/周期的实时数据及来源回执。每人一次读取、单次最多 240 秒，模型可见真实步骤结果；纯本地 `ceo-weekly-okr-report/scripts/report.py` 按稳定 userId 准备输入、计算原有精确公式并渲染。Agent 保存成员产物以恢复进度，发布管理报告、成员子页并提出群摘要消息。Skill 的 prepare 在计划中标记来源摘要匹配且已完成分析的 `analysis_reusable`；`analysis_failure` 占位不算完成且不会复用。任何恢复的 Consumer turn 先读取最新 Audit 与任务产物，再刷新当前名单和每位成员来源各一次；当前没有独立的来源摘要查询接口，因此只有读取成员来源才能发现来源是否变化。prepare 按新摘要保留未变化成员的完整分析，只重做来源已变或分析未完成的成员；同一 turn 内不得重复成功的成员读取。未完成本轮分析时必须保留有效产物并以可重试结果继续，使用正常有界重试。零进度不是结果证据；每个 KR 都按原规则检索独立证据并评分，未找到佐证时说明证据缺口，不能用 `analysis_failure` 代替未完成分析。领导力和文化的每项分数须由该维度自身的行为证据支持，不能跨维度借用；创新须有具体改进、验证和观察效果，AI能力须有具体AI使用和工作效果。Audit 逐人核对全部七项维度、证据与分数。未评分成员保留已知职级与逐人来源缺口，不生成分数。业务 KR 的完成档位与领导力/文化行为档位分别应用；行为评分使用正式维度，70–79 为符合标准，80 分及以上须有明确超出当前职级标准的具体效果（经理日常辅导、总监在团队内复用方法本身仍可属于本职标准），90 分及以上须具备对应榜样范围证据；显示分数保留一位小数并采用通常四舍五入。每次渲染后必须按当前 manifest 写入或更新并完整读回全部主文档和附录，即使某成员分析未变，日期、标题、链接仍可能变化；旧标题或旧读回不能满足本次 manifest。最终群提案逐字使用 summary.md；这些步骤由 Skill 与现有 Audit 承担，未新增服务发布门槛。原独立周报 Agent 子任务、租约/缓存编排和星期日重复调度逻辑退出当前执行路径；历史记录保留。

发布时，现有 Consumer system contract publication 原子发布 Skill 文件、新脚本与新 managed revision，在服务停止窗口迁移三项报告任务定义，记录完整前后配置和版本；启动 seed 再运行保持幂等。待恢复的旧报告执行必须先自然结束；核查未完成执行的原始快照，不依赖当前任务仍保留旧命令或 Skill 引用。失败回滚按精确版本恢复任务配置、runtime config 和既有文件，并移除本次新建文件；任务 ID、历史触发快照、周期、时区和用户自定义文本保留。

定时任务的固定 Agent 验收使用独立离线合成案例；Consumer、Audit 和修订共享同一评测传输契约，业务规则仍来自选定 Skill。评测字段不能混用生产动作名称，也不能把排除说明的来源追踪当作最终管理内容。案例与评分函数保持冻结，失败历史、首轮与一次审核修订结果分别保留。正文/动作草稿与真实外部回执独立核查，详见 [Agent 验收记录](scheduled-task-generic-agent-validation.md)。此评测契约不注入生产运行。

### Runtime-managed Skill 生命周期

业务行为的可配置部分使用运行时托管的不可变 Skill 修订，而不是 Settings 对项目文件或
`~/.agents/skills` 的直接写入。首次迁移只将仓库所有的 service-managed Skills 导入 SQLite；
它不扫描或修改用户、系统、插件和 operation Skills。每个保存产生一个新的 revision（内容、SHA、
父修订、来源均保留），每次启用产生一个 next-start runtime config，绑定精确 revision、启用状态、
加载顺序和用途。

```text
Settings revision/config candidate
  -> pending_restart
  -> process loads exact revision bodies at startup
  -> append-only PID/config/SHA load receipt
  -> active
     or load_failed (previous active config stays active)
```

Consumer 在 invocation 开始时接收这个 immutable snapshot；同一次调用不会重新读取 Settings。
因此 service 不用关键词路由 Skill，不解析业务材料，也不建立并行的 Skill 审计数据库。
`feedback_iteration` 是 runtime config 的系统能力，不是业务 producer feature：关闭它会阻止新的
反馈 claim，并让 UI 显示 disabled 的处理入口，但不影响反馈读取、历史和 reopen。反馈结案证据
按 decision scope 验证：Skill/config 路径需要匹配的 activation/load receipt，code 路径才需要
本地 main 祖先 commit；两类路径都需要场景、健康和零 backlog 的已回读证据。

普通会话里提出“修改分身规则、Skill 或服务行为”并不自动成为 `feedback_iteration` 队列项。
只有输入上下文明确携带 `feedback_key` / `batch_id` 时，才要求反馈处理轮次记录；普通消息直接按
适用 Skill 形成规则修改候选。找不到反馈队列记录不能作为普通消息处理失败的理由。

OA 每个审批节点只校验当前表单真实存在且明确必填的信息。后续业务阶段、其他表单或评审偏好
中的字段不能反向成为当前节点的强制条件；Audit 发现这种候选必须退回 Consumer 重新生成。

OA 定时任务冻结传入通用 `dingtalk-oa-approval` 与 Stardust 财务、立项、合同、人员、
考勤/出差、云资源六个业务大类 Skill。个人身份、工作区和其他部署参数由运行时注入 Prompt，不写入 Skill。审批 Agent 只按通用
审批 Skill 和适用的 Stardust 业务 Skill 判断；只有未被适用 Skill 明确引用的
背景材料才可跳过。Skill 明确引用的规则卡、登记表等规范材料是该 Skill 的规则来源，
审阅时必须读取。Consumer 按 live `processCode` 和表单事实选择适用类别；跨类别事项组合适用 Skill。
财务 Skill 的规则卡只适用于登记的财务模板，不匹配其他类别不得单独触发升级；规则卡只提供判断标准，动作与其他类别一样按通用决策表（2026-09-24）。适用业务 Skill
必须覆盖当前事项的规则条件、例外、权限和动作映射，且内容有效，
`rule_coverage` 才能为 1.0；部分覆盖本身不等于人工升级，只有低于 0.5 才满足规则覆盖分支，并且必须确认缺失的是适用决策规则，而非事实或执行能力。申请人可以补足事实或材料；若政策判断仍无法解决，先审核并执行必要的材料请求阶段，再提交满足统一门槛的人工问题。个人审批偏好仍来自定时任务 Prompt。

供应商付款证据台账只记录与实质付款条件相关且由当前材料核实的事实；OCR 可读不代表字段与付款判断相关。复核旧退回或拒绝时，只报告有独立证据支持的实质缺口轮数；未计入轮次的历史原因不复述，也不以泛称间接指代。供应商付款不评估或请求签章相关材料，也不在证据叙述或决策理由中复述相关栏位或历史要求；双方对价格、范围和结算条件的确认只根据协议、订单、报价、往来内容等实质证据判断。对已结束流程的历史复核只作分析，结果为 `no_action`，不得提出新的 OA 评论或审批动作；新提交的流程必须按自己的当前任务和证据单独审核。`rule_coverage` 只由本案触及的分支决定；未核实的适用事实计入 `information_completeness`，不降低规则覆盖度。重复退回阈值只能按明确适用的规则计算。每次退回都须以可读、来源核验的证据独立确认同一项实质且可由申请人补正的缺口；审阅端权限、解析、OCR 或客户端读取失败、误述状态以及合同没有要求的形式要求，不计作申请人未解决的问题。

供应商付款 Skill 进一步明确：审阅者不评估签字、印章、签章栏是否存在或可读，也不在内部/对外结果提及这些观察；付款判断只看双方实质合意、交付/验收、结算金额、发票、收款方及记录一致性。此排除仅适用于 `supplier_payment`，其他财务模板仍按各自来源规则审查。

### Business Object、Task、Agent Run 与 Reply Attempt 的关系

这三个对象分属调度、执行和展示三层，不能混为一个状态：

```text
business_object（稳定业务对象，例如一条 OA 审批节点）
  ├── reply_task_inputs（不同入口的不可变输入身份；结算后保留摘要和来源）
  └── reply_task（唯一当前队列投影）
        ├── agent_runs（多次真实 Agent 执行）
        └── reply_attempt（稳定的业务结果当前投影）
```

- `business_object` 表示外部系统中的同一件业务事项。OA 使用
  `process_instance_id + task_id`；普通消息在没有更稳定身份时才回退到
  `channel + conversation_id + message_id`。同一事项从轮询、事件或人工重试进入时，
  只更新一个 `reply_task` 当前投影，每次输入追加到 `reply_task_inputs`；活动输入保留准确正文，已结算正文按存储保留契约改为来源与摘要。
- `reply_task` 负责排队、领取、重试、`execution_generation` 和 worker 所有权。
  新输入到达正在执行的 task 时，本轮结束后重新排队同一个 task，不创建并行任务。
  正文和 provenance 只保存在 `reply_task_inputs`；`current_input_id` 外键指向当前输入，
  每次队列领取保存 `claimed_input_id`，当次执行和冻结 Skill 按代际校验读取领取快照。
  延后或恢复后的再次领取沿用原有版本水位语义，捕获当时的当前输入。
  `input_version`／`claimed_input_version` 继续作为原有并发版本水位，不充当输入 ID。
  已结算输入不另存从 provenance 可生成的精简 JSON；API 按来源摘要投影原有展示形式。
- Provider 若原地更新同一个 DingTalk 日程卡片而继续复用消息 ID，近期消息恢复会把
  `input_revision_key` 不同的新快照追加到 `reply_task_inputs`，同时更新同一个
  `reply_task` 的当前投影和 `input_version`。只有卡片内容变化、日程仍有效且本人仍待响应时
  才开启新的 `execution_generation`；普通已读消息仍按消息 ID 去重。
- `agent_run` 表示一次实际 Consumer 或 Audit Agent 执行。重试、服务重启接管或
  新的 generation 都会产生新的 run；run 的状态、session、revision、transcript
  范围是不可覆盖的执行事实；tool event 与原始错误按准确范围从原生记录读取，缺失时明确不可用。
- `reply_attempt` 表示同一 trigger/channel 的业务结果当前投影。重跑更新原 attempt
  的当前状态、结果和错误，复用原 ID，不创建第二个业务 attempt。

Attempt 详情页默认展示 `reply_attempt` 的 current projection，并允许在同一 attempt
下切换查看多个底层 `agent_runs`。因此“当前结果可以被修正”与“执行历史不可抹除”
可以同时成立：列表显示最新结论，详情按引用读取每一次 run 的真实原生轨迹；来源缺失时保留运行状态并明确过程不可用。

#### 为什么这是系统级边界

扫描、事件、工作通知和人工反馈可能分别携带同一业务对象的新输入。如果服务仅按
`trigger_message_id` 创建任务，同一个 OA 节点、会议或消息投递会形成多个可并行执行的 task；
每个 task 又可能独立产生反馈 revision 和外发动作。即使其中一个 run 已经完成，其他历史 task
仍可能继续运行，最终表现为重复请求材料、互相矛盾的状态说明或重复的成功通知。

因此所有业务类型共同遵守以下约束：

1. 先解析稳定的 `business_object_key`，再排队；入口输入身份和版本只追加，已结算正文改为来源摘要与引用。
2. 每个业务对象只有一个 current `reply_task` 和一个 current `reply_attempt`。
3. 重试、反馈和服务恢复只追加 `agent_run`，不得创建第二个业务 attempt。
4. 只有稳定动作身份对应的 provider 成功结果可以阻止重放；应用层不根据命令、工具、Skill、
   read-only 分类、receipt 格式或所谓“未知效果”推断业务是否完成。
5. Attention、Workers 和质量门只统计 current projection；History 展示旧 task、旧 run 和失败状态，原始过程按需从可用的原生来源读取。

失败 Reply task 的 Attention 诊断读取该任务当前执行代的最新 run，展示原始 `source_code`
（没有时使用 `code`）、来源与明确标注为「Agent 说明」的 `reported_summary`。旧代或无法
解析的诊断不替换任务自己的错误。展示这些字段不证明外部操作已执行，不改变拒绝、重试、
人工决定或任务状态。

如果同一业务对象的 History 出现多个旧 task，模式迁移可以重建 `business_object_tasks` 的当前映射，
但不得删除旧输入、run、session、tool event、provider 结果或错误事件。当前投影修正不能被解释为
“历史从未失败过”。

服务启动时，`reconcile_done_reply_tasks_with_failed_current_run` 只把当前执行代的最新 run 确为
`failed`、且没有成功外部动作或已收口错误的 `done` task 改回真实失败投影。此恢复按
`external_action_results.business_object_key`、`sent_replies.external_action_key` 和
Actions 的 Errors 类型 `(conversation_id, message_id, kind)` 索引反查；成功外部动作检查直接使用 typed `actions`
表，并固定走已有业务对象键与发送回执键索引，避免 SQLite 对嵌套投影连接选择按 record_type
反复扫描回执。候选扫描在只读连接完成，写入按固定大小分批；每批短写事务只按候选 task ID、执行代和
最新 run ID 查询并重核外部成功动作、已收口错误及终态回执，不在写锁内重跑全历史候选查询。
并发变化的候选会跳过，不会被旧扫描结果覆盖；历史保留规则不变。

`reconcile_unresolved_reply_tasks_with_settlement_evidence` 同样先只读扫描精确 trigger 的已收口
settlement evidence，再按固定大小批次短写。每个批次重新核验 task ID、execution generation、未解决状态
及 settlement evidence，并只压缩实际关闭的 task 输入。启动恢复不会为整个历史集合持有一个 SQLite
写事务，也不会覆盖并发 writer 已写入的终态。

只有明确的操作员决定可以停止仍处于 `external_action_uncertain` 的失败 Reply task：先读取
`GET /api/console/reply-tasks/{task_id}` 的当前 generation，再向对应 `/skip` 入口提交 generation
和非空原因。写入原子检查失败状态与 generation，将 task 投影为 `skipped` 并追加
`reply_task_skipped_by_operator` settlement evidence；原 task error、Attempt 和 Agent run 保留，
不声称外部动作成功，也不调用 provider 或重新执行动作。重复提交、generation 过期或 task 状态变化均被拒绝。

旧版本曾在同一个 `reply_task` 的不同 generation 各写入一条 `reply_attempt` 投影。此类遗留行仍
保留其 `agent_run` 作为执行事实，但 History（包括 Console API）只展示该 task 的最新 Attempt；它们
不是多个独立业务事项，也不能重复计数或形成多张处理卡片。History 图表仍在每条原始 Attempt 的
发生时段保留事件，但对被隐藏的遗留行以该 task 当前的 `done`、`skipped` 或 `needs_human` 终态
投影生命周期标签，保证图表与列表不把已收口工作重新计为失败。

当一个旧 `needs_human` Attempt 的来源 `agent_run.reply_task_id` 与该业务对象的 current task
不同，且 current task 已终态时，启动收口会把旧 Attempt 标为 `skipped`，并写入“新任务已接管”的
resolution。相同规则也适用于同一 trigger 已有更晚的完成或无动作终态 Attempt。它只修正过期的当前
投影，不删除旧 run 或改变外部结果；同一 current task 上仍待选择的 `needs_human`，或后续结果为
`failed` 的 Attempt，都不满足这个条件，必须继续保留。

### 外部动作身份、顺序与发送投影

Consumer 为每个 ProposedAction 返回 `action_identity`。同一业务对象中，同一预期外部
结果在 feedback revision、服务重试和新 agent run 之间必须复用该身份；预期结果、目标或
用途改变时必须使用新身份。服务根据 `business_object_key + action_identity + operation + target`
生成 `external_action_key`，而不是用 run id 或 revision 做去重。

钉钉消息 target 在 wire 边界只使用一套服务字段：群发使用 `conversation_id`，引用回复
同时使用 `conversation_id` 与 `message_id`，单聊使用 `open_dingtalk_id` 等稳定接收人
身份。Provider 返回的 `openConversationId` 等字段只属于执行结果，不能进入 proposal target。
这项约束在 typed result 解析时校验，正文准备、System 执行和动作键生成不再各自解释别名。

Provider 成功结果按 `external_action_key` 只保存一次。后续 run 再次遇到同一动作时复用既有
结果，不再次调用 provider；新 run 仍追加自己的观察关联，因此执行历史完整。一个 proposal
中的动作严格按数组顺序执行：序号更小的动作尚未成功时，后续动作不得开始。这样审批失败时
不会继续发送“已审批”的通知。

OA 工作通知有时只有 `process_instance_id`，没有 `task_id`。服务会同时从原生字段和事件正文
中的 OA URL 提取身份；当该审批实例只有一个已知节点时，把评论事件映射到这个节点，避免
webhook、待办扫描和工作通知各自产生一个任务。同一实例存在多个节点且事件未给出节点身份时，
服务不猜测节点。

所有成功消息统一投影到 `sent_replies`。同一 `external_action_key` 只有一条消息记录，相关的
agent run 通过 `sent_reply_observers` 关联到它。History 因此既能显示真实已发送消息，也不会
把一次复用展示成第二次发送。这些是执行幂等与展示事实，不是应用层命令审核、业务证据审核
或 read-back 状态机。

消息 provider 返回稳定消息 ID，即构成已完成的发送事实；应用层不再要求额外 read-back
证据才能写入投影。投影按 `action_identity` 精确关联 proposal action，不能从动作数组中猜正文，
也不能要求发送目标会话等于源任务会话。会议跟进优先发送到 Agent 选定的业务群；该群不存在、
已解散或不可发送时，使用会议上下文中已经存在的组织者 `user_id` 或 `open_dingtalk_id` 发送
单聊。服务不得根据组织者姓名搜索或猜测身份；上下文没有稳定组织者标识时保留任务重试，不发送。
群发与组织者单聊使用同一个稳定业务投递键，因此重试或新 run 不会重复投递。服务启动后
会从 append-only Consumer/Audit typed results 修复缺失的 `external_action_results`、`sent_replies`
与 observer；该修复不重放 provider 动作，也不改写历史 run/event。

会议总结按受众拆分内容。业务会议的普通结论、安排和行动发送到 Agent 根据实时业务承接证据
选择的群；人员评价、绩效、薪酬、晋升、去留、候选人结论、健康或请假内容不得进入该群消息，
而是作为可选的独立私聊消息发送给经实时身份与职责确认的参会 HR/人员负责人，无法确认时发给
当前负责人本人。若 DWS 实时群发现确认目标是 HR 专属或已匹配的群，且敏感讨论面向会议中的 HR
共同受众而非针对具体个人，则允许敏感详情随 HR 群消息发送，并不再重复发送私聊；群受众不明确、
含非授权成员或讨论针对具体个人时仍按脱敏群消息加敏感私聊处理。群消息与敏感私聊使用不同的稳定投递键，任一发送重试都会复用已成功的另一条，
日历描述只写群消息版本。

钉钉消息在交给 Audit 前生成带服务后缀的最终候选正文。这个准备记录按
`execution_generation + proposal_revision` 区分：同一 revision 的进程重试复用同一正文，Audit
要求修改后生成的下一 revision 使用修正后的新正文。外部动作的 `action_identity` 和
`external_action_key` 仍跨 revision 保持稳定；如果 provider 成功结果已经存在，后续 revision
复用该成功结果而不再次发送。

`sent_replies` 和 provider 成功结果的优先级高于后来失败的 attempt current projection。即使
结构化结果解析、服务重启或后续 Agent turn 失败，下一轮也会收到真实的已发送记录，并把该
动作视为已经完成；不能因为 attempt 当前显示 failed 就再次发送。

### 用户反馈处理投影与重新打开

用户反馈处理使用稳定的 `pending -> processing -> resolved` 当前投影；
这组状态与普通 Agent 任务的 `pending -> processing -> done` 不是同一状态机。
`pending` 表示未领取，`processing` 表示已被一个批次原子领取，`resolved`
表示当前处理轮次已用完整回执结案。页面上的“未完成”是
`{pending, processing}` 的合集，不增加第四个存储状态。

重新打开是唯一合法的 `resolved -> pending` 转换。调用者通过现有本地
Feedback API 提交一个不为空的确切 `reason`；服务不生成、重写或补默认理由。
重新打开只清除当前处理投影，不创建新轮次，也不直接进入
`processing`。下一次原子 claim 才创建新批次和新的不可变处理轮次。
历史批次、轮次、Workbench 关联和回执仍可读且不得被覆盖；证据写入和
结案只能作用于当前 `processing` 轮次，旧轮次的证据不能满足新一轮。

Feedback API 跟随现有后端的本地访问边界，供 Workbench 和仓库 Agent
共用；它不对公网暴露，也不增加 feedback 专用鉴权。完成一个当前轮次时，
后端必须从当前轮次回执中同时确认：代码已实现，测试成功，提交存在且是
本地 `main` 的祖先，`com.ceo-agent-service.main` 已重启且 PID 实际变更，
本地健康接口返回 HTTP 200 和 `ok=true`，权威实时的 `processing` / `failed` /
`retryable` 数量都是零，并且状态和证据已经通过 API 持久化后回读一致。

该能力不在 Agent 页面增加第二套流程，也不使用模型合成重新打开理由或新回执。

### Email Agent task 映射

> **实现与部署状态：** Email folder classifier 与 audited-v2 lifecycle 已合入 `main`；本机
> launchd 的独立 Email worker 已启用，并通过真实 IMAP 可逆验证。SMTP 与自动回复仍禁用。

邮箱服务器中的当前文件夹是类别的唯一事实来源。服务维护“业务类别 → 每个账号的精确
provider 文件夹”绑定并单向创建/校验目标文件夹；分类结果本身不能覆盖文件夹事实。定期把这份文件夹事实读进训练快照的观察任务，一条邮件解析失败（IMAP 读到 `fetch_uid_batch`
Python 的 email 包无法解析的结构化邮件头）只跳过这一条、游标照常前进，不会重试同一条、也不会拖垮整批（Derek，2026-09-28 实测：Gmail 账号的观察任务因此连续几天每次都失败，导致自动重训三天没有真正判断过一次）。IMAP 只读连接的 socket 超时默认 90 秒（曾是 20 秒，同一次实测里单封邮件头就要 5-6 秒，20 秒经常不够，报的是 `TimeoutError`）；socket 超时和邮件头解析失败不同，不能在同一条连接上跳过继续读——response 会和下一条命令错位——所以超时时整条连接照常作废重连，靠放宽上限而不是"跳过"来解决。Inbox
表示尚未分类，Spam/Trash 固定为内部 `junk`，Sent/Draft 不参加训练。`important` 不是类别，
而是独立注意信号：兼容 provider 的 Starred/Important/Flagged 信号与成熟模型信号取并集，
但 junk 始终抑制 important。控制台详情页的 Star / Flag 图标是主人本人的手动点击，直接让服务连上邮箱增删 `\Flagged` / `$Important` 这一个关键字并读回确认，不经过 ActionPlan；观察到的状态随后更新，下一轮扫描再对账。分类确认只保存最终类别、训练反馈和不可变 `ActionPlan`。确定性动作清单是
`label`、`mark_read`、`archive`、`move`、`trash`、`flag_important`；它们属于 Email 子系统，由独立
Email worker 领取、执行动作，并以服务器的应答为结果（Derek, 2026-09-25：不需要回读，服务器接受就够了）。
每次 provider observation scan 在同一 SQLite 事务中发布消息状态、authoritative folder 的缺失对账和 folder generation 时间；generation 时间单独按账号/文件夹保存，读取仍返回该文件夹最近一次完整扫描时间。相同消息状态不重复写消息行或其索引，扫描成员先写入连接级临时表再用索引对账，避免在共享数据库写锁期间构造超长身份列表。这样保留整代原子可见性，同时限制 Email worker 对共享 SQLite 的写锁占用。
直接邮件动作领取先从状态索引找出 pending/failed 动作，并按分类主键核对它属于已处理分类的当前 ActionPlan，再通过 `(classification_id, action_plan_id)` 索引读取该分类的全部动作组。仅旧计划残留未完成动作的分类不会加载历史计划正文；同分类旧 processing 动作仍参与阻塞判断。候选探测与 `BEGIN IMMEDIATE` 内的重核都使用这条路径。状态、账号范围、重试时间、依赖、动作优先级和条件更新保持不变，不在写锁内遍历整张 `email_actions`。
稳定 Message-ID 查找若在多个文件夹命中，通常属于确定性身份歧义：在只读状态定位阶段失败，不执行邮件写入，也不自动重试。若移动动作的原 UID/UIDVALIDITY 定位失效，执行器只读检查解析后的目标文件夹；仅当该文件夹恰有一条同一稳定消息身份且满足目标状态，才以 `readback_noop` 记录已发生的移动，不再派发 MOVE。目标文件夹没有唯一匹配或状态不满足时仍保留失败，不以其他文件夹的匹配推断成功；超时、DNS 和连接错误仍可重试。
执行前仍先读一次当前状态，已满足就不写。改标记的动作（标已读、标星、贴标签）在服务器接受 STORE 后即完成；
移动、归档、删除在服务器回了 `COPYUID`（新位置已知）时同样即完成。只有服务器没说邮件去了哪里时，
才保留一次回读，用同一账号共享的连接去找新位置并确认。进程内每一个会碰 IMAP 的调用点——直接动作
投递（写连接和回读连接）、定期分类扫描、训练观察任务、OTP 读取、历史回溯（含重读候选、补建模型
动作）、Agent 任务上下文加载、退订入口解析——现在都经 `EmailAccountConnector`
（`app/email_account_connector.py`）取用，一个账号一个连接，由互斥锁串行化，按优先级排队（用户直接
等待的：分类回读、扫描、直接动作投递、OTP、任务上下文、退订入口解析，都是 HIGH；后台批量的：训练
观察、历史回溯、模型动作补建，都是 LOW）；排不到就在 600 秒后超时报错；连接闲置超过 45 秒、连续
复用超过 300 秒或探活（NOOP）失败就丢弃重连，失败的连接不复用——这三个数值不变，只是从"每个调用点
自己维护一份连接"改成了"一个账号一份，由 `EmailConnectorRegistry` 持有"（Derek，2026-09-28：
`py-spy dump` 抓到训练观察和直接动作投递两个线程同时各握着一条到同一个 Gmail 账号的活连接，账号随之
被限流，拖垮了所有排在后面的调用者）。训练观察和这些按需调用点各自的实现本身基本不改动（都仍是
"取一次、用完就关"的调用约定），只是它们的 `source_factory` 现在从连接器取用、关闭时把连接签回而
不是真登出。退订审计/直接执行走独立进程（每次任务一个 CLI 调用），各自建一个只服务这一个任务的
registry，不与主 worker 进程共享——同一账号在两个不同进程里各自最多开一条连接的边界仍然存在，本次
改动解决的是同一进程内多线程各自开连接的问题。这些确定性动作
不创建 CEO Agent task，也不创建 Consumer/Audit run。`trash` 只允许可恢复的 move-to-Trash；
永久删除、IMAP `EXPUNGE` 和清空 Trash 在所有配置与执行入口都不可达。

标准 IMAP 账号使用 `UID MOVE`。若 provider 未声明 MOVE capability，但其官方协议明确规定
`UID COPY` 本身就是移动语义，账号可显式配置 `imap_move_mode=copy_as_move`；服务不会按主机名
猜测，也不会把普通 IMAP 的 COPY 当作移动。两种模式都以服务器回的 `COPYUID` 得到新位置；
服务器没回 `COPYUID` 时，才用稳定 Message-ID 重新定位并确认目标文件夹。都不得通过 `STORE \\Deleted` 或
`EXPUNGE` 模拟移动。

只有不可变 `ActionPlan` 明确授权的 `unsubscribe` 会创建 `channel=email` 的
`pending` task；持久化生命周期标识仍为 `email_unsubscribe_audited_v2`，以兼容既有
任务输入，但它不要求 Consumer 或 Audit turn。分类确认、零动作计划
和其他 Email 动作都不会创建 task。`auto_reply`、SMTP 和 `mailto` 发送全部禁用：配置、
分类结果、人工确认和 Agent 都不能生成或发送邮件回复或退订邮件。

Email task 继续使用现有唯一键 `(channel, conversation_id, trigger_message_id)`：

```text
conversation_id
  = digest(account_id + stable_thread_identity)

trigger_message_id
  = digest(account_id + stable_message_identity
           + action_type + action_plan_version)
```

同一 ActionPlan 的重复扫描、进程重启或模型重训只会取回原任务，不改写原任务、
generation 或运行历史。新的计划版本获得新的动作身份，因此可以在保留旧 run 的同时
形成与动作类型匹配的新生命周期。

任务的 `trigger_message_json` 只保存可追溯的动作身份、账户/邮件/thread 身份、
ActionPlan、分类、模型和配置版本，以及 opaque unsubscribe entry。
它不复制邮箱凭证、附件字节、本地附件路径、邮件正文、完整私密 URL 或 query token。

模型拒判后的分类 Agent 走系统统一的 runtime 线路与回退顺序，没有专用线路（Derek，2026-09-24）。
分类器的 Codex 轮次将原生 `project_doc_max_bytes` 设为 `0`，不重复注入仓库的
`AGENTS.md` 项目说明；分类任务输入中仍包含本轮冻结的完整分类 Skill、邮件事实、
允许类别和退订候选，且使用原有结果校验及候选索引绑定。
该 Codex 轮次还通过现有原生 `skills.config` 只保留默认常驻插件入口，排除与分类无关的
原生 Skill 目录及已由服务完整提交的分类 Skill 副本；这不改变 Claude/Friday 路线或
其他任务的 Skill 目录。
它的提示词里带最多 8 封主人已确认标签、与当前邮件最相似的邮件（发件人、主题、正文前 250 字和主人给的类别），
让它照主人的归类习惯判断（Derek，2026-09-26；`app/email_similar_examples.py`）。相似度用字符 n-gram 的 TF-IDF，
只取主人确认过（`classification_source='user'`）、类别仍然存在、且不是当前邮件本身的行；索引按主人确认标签的“条数+最新更新时间”缓存在进程里，每次分类只读这一个戳；主人有新确认时才重读并重建（约 900 封 1.2 秒，单次查询 7 毫秒，重启后第一封多花一次建索引的时间）。
示例只是证据，不是指令，不改变“只分类、不行动”的约束。模型输入只投影当前邮件、允许类别及说明、未读状态和退订候选，不传服务路由 locator、稳定邮件身份、文件夹目标映射或配置版本。离线评测（888 封主人标注）里这样做的准确率：
零样本 65%，带示例 89%（已见过的发件人）、77%（新发件人）。示例里含其他邮件的片段，会随提示词发给所选 runtime 线路。
检索方式同期对比过 embedding，字符 n-gram TF-IDF 效果持平且不用额外起模型服务，没有换的理由（Derek，2026-09-28）。
Chrome 内置 AI（Gemini Nano）评估后放弃，不是待办：它的模型文件需要重新下载约 4GB（此前已删），只能通过页面 JS API
调用、要搭无头 Chrome 才能跑，且无头模式下能否拿到该 API 未经验证；零样本本身的准确率已经不够，Nano 大概率同样受限，
不值得为它搭这套 harness（Derek，2026-09-28）。

运行时 `AgentTaskContext` 从 Email 数据源提供当前邮件和 thread 的纯文本正文。附件是
metadata-only，没有 image/content material；只投影文件名、MIME、字节大小、数量和 inline
标记，并固定 `image_paths=()`。任何组件都不得
下载、打开、OCR、解析、总结或推断附件正文。不可变 ActionPlan 是唯一动作授权；Adapter
只排队，不发送、不打开退订页面。

Email worker 的退订任务修复先扫描分类 ID 和动作身份，只为确实缺少稳定任务的记录读取完整分类行；
修复后按该分类 ID 定点复核，不重扫所有分类。

退订是低风险的直接 Email worker 动作；退订不做结构化审核，也不做 Consumer 或 Audit turn。
worker 以 task id 从
durable 状态读取 ActionPlan 已授权的 entry，一次调用完成整件事——打开该 entry，按页面当场呈现的
精确链接、表单或独立退订按钮操作，直到第一个终态页面，然后保存 outcome 和脱敏后的页面原文。
兼容调用语义仍称为 `unsubscribe_email(task_id)`，但它由 worker 直接执行；没有 proposal 要抄写、没有
acceptance 要绑定、没有 Audit turn，也没有 continuation 要续。

幂等性只靠 receipt：`email_unsubscribe_receipts` 里每个动作身份一条，已有 receipt 时再调一次只会
把它原样返回，不会重复退订。这取代了原先的 claim 租约、effect digest 链、owner fence 和
Consumer→Audit 往返——退订在真实世界本来就是幂等的，那套 exactly-once 支架换不来任何东西，
却让每一次点击都要花一整轮 agent，而过期的 continuation 会让任务失败在机制上而不是页面上。
首次外部调用前会持久化执行标记。只有执行返回明确的瞬时浏览器错误码时，标记才转成带错误码和
重试次数的 retryable 状态；任务按现有指数退避及四次预算恢复同一 ActionPlan、entry、generation
和输入摘要。终态 receipt 仍是唯一成功／跳过凭证。进程中断、结果未知、错误未分类或重试预算耗尽时，
标记保持不可重试并进入核对／Attention；不能清标记或重放来推定成功。已有不可分类的执行标记会以
`email_unsubscribe_outcome_unresolved` 记录为外部结果未确认，而非内部 `ValueError`；它仍保持失败并等待
只读回执核对，不会自动重放。浏览器步骤本身不升级为
`needs_human`，技术失败保留为失败；有浏览器步骤但没有 receipt 不再升级为 needs_human，重试资格由上述明确标记和错误码决定。
启动时若终态退订回执已落库而对应任务仍失败，符合恢复条件的任务按同一动作身份投影为 `done` 或
`skipped`，不再次调用邮箱提供商；带回执的更新只读取该任务自己的持久化字段。

页面要求登录或 CAPTCHA 时记 `skipped_login_required` / `skipped_captcha`。
页面说的是什么由 Agent 判断，不再靠关键词（Derek，2026-09-25）：服务照旧负责机械步骤（打开链接、等页面文字连续 1 秒不再变化、操作控件、抓取文字），然后把主机名（不含带令牌的链接）、页面文字（最多 6000 字）和可操作控件的种类交给一次 Agent 调用，它返回一个状态（已退订、之前就退订过、要登录、要验证码、要付费、链接过期、有可操作项、看不出来）和决定它的页面原话。原话写在回执结果文字的第一行“判断依据：……”，后面是它读到的整页文字。链接过期和看不出来都不算页面本身的结论，走下面“页面读到了但这个服务不操作它提供的控件”那条路。Agent 线路不可用或返回的不是约定的对象时，这一次读取按浏览器故障失败（记录里的类别是 `page_judge_unavailable`），任务先延后重试一次，仍失败就记 `failed`，可以手动重跑，不猜结果。这是因为站点导航里的“Sign in”和一个要登录的页面无法靠关键词区分，Substack 这类站点还会在静态外壳之后用脚本弹出“You've been unsubscribed”的提示，只读外壳会把已经成功的退订记成需要登录（2026-09-25）。审计式（audited）旧流程没有 Agent，仍按关键词读页面；工作负载种类是 `email_unsubscribe_page`。
页面读到了但这个服务
不操作它提供的控件时记 `skipped_no_reliable_entry`，并保留页面原文。两种跳过（没有可操作项、需要登录）都只是“当时读到的页面”，不是对链接的定论：手动重跑会重新读页面，读法相同时保留原回执，读法不同（比如需要登录变成已退订）时用新回执替换它。

任务已关闭（done / skipped / needs_human）却没有 receipt 时，启动与训练维护里的对账会补一条 `skipped_no_reliable_entry`（证据 `durable_context_entry_unavailable`），让后续动作不被卡住。`failed` 的任务不在此列：它结束于技术故障，不是对链接的结论。2026-09-25 一个退订任务因数据库校验错误失败，对账把它写成“找不到退订入口”，而同一条链接其实一直在邮件里；失败的任务保持 `failed`，由人重跑。

receipt 另外保存 `entry_url`，即这次实际打开的完整私密 URL。`entry_reference` 只是该 URL 的 sha256，邮件 HTML 正文也不落库，所以在此之前 `skipped_no_reliable_entry` 这类结论只能指出 host，无法被人工复现。写入前校验 sha256 与 `entry_reference` 一致；该值是可直接触发对外副作用的链接，只在 receipt 表和 Attempt 详情页出现，不进入 `trigger_message_json`、步骤日志或错误码。

分类器按阶段运行。每个邮箱分别保存 Agent 与模型回溯窗口，默认 30 天和 365 天。尚无上线模型时，
定时扫描只把 Agent 窗口内符合该账户“仅未读/全部”设置的 Inbox/未绑定来源邮件放入 Agent 队列。
模型上线后，定时扫描改为只由模型优先接管模型窗口内全部尚无稳定记录的已读和未读邮件，不再按
日期保留近期邮件给 Agent，也不在同一轮运行 Agent 扫描。模型高置信度结果沿现有不可变 ActionPlan 和
provider action 队列整理邮件；低置信度、`others` 或未晋升类别不由模型决定，进入 Agent 分类队列，
Agent 明确判定的照常处理，Agent 自己也不确定的才存为 `pending_feedback`，进入 Console“待确认”
并等待人工标注（Derek, 2026-09-25：先回退 Agent，Agent 不确定再问人）。Agent 只接账户允许它接的邮件
（未读，或账户设为“全部”）；模型拒绝的已读邮件 Agent 会跳过且不留分类记录，所以它们直接存为
`pending_feedback`，不入 Agent 队列。模型/Embedding 技术失败则
保留邮件下轮重试，不伪装成待确认，也不当作模型拒绝交给 Agent。历史批处理允许 120 秒的 Embedding 请求期限；实时调用仍保持
2 秒期限，历史回填不会因为实时低延迟预算而永久卡在同一封邮件。分类过程不擅自改变邮件原有已读状态。训练只在冻结的
provider-folder snapshot 上离线、分阶段执行，shadow 模型不进入实时扫描。晋升只看最新一个候选自己携带的证据：
按类别逐个判断（接受准确率、验证样本数达到界面配置的门槛），达标的类别由模型自己决定，其余仍由 Agent 决定；
不再要求连续两个候选，也不再要求标签水位前进。已上线的模型只对照它自己的证据（仍达标、兼容性一致、artifact
哈希一致）保持生效，之后训练的任何新候选，无论达标与否，都不会让它下线；候选目录里有无法读取的文件仍 fail-closed。
旧的显式实时调用接口仍保留顺序 fallback 契约，定时收信与历史回填统一使用上述模式互斥路由；
模型上线后，模型不确定的结果同样先交给 Agent。

历史和实时移动都先读取 provider 当前状态，写入后以服务器应答给出的新 locator 为准（没给才回读）；
important flag 在移动后的 locator 上执行。用户随后在邮箱中移动邮件时，下一份冻结 snapshot 直接采用新文件夹标签。
`junk` 的退订候选由代码从标准 header/body 链接中发现；分类 Agent 的 wire result 只返回本次候选序号，服务按该次调用的候选列表绑定精确 URL，并继续执行既有类别、索引和持久化校验；模型不回传或改写 URL。选定候选后，独立的系统直接退订流程执行后续网页步骤；成功或无需继续后再移动到系统 Trash。连接邮箱的邮件 OTP 只在站点、收件人和有限
时间窗同时匹配时临时读取；普通 CAPTCHA 可在隔离 profile 中尝试，密码/MFA/CAPTCHA 无法完成时
保存有界 continuation 并转人工接管，不持久化 OTP、cookie、完整 URL 或浏览器秘密。

邮件 Worker、Web 邮件路由、定时邮件发现和每日报告读取在初始化时校验 `EmailStore` 的 schema 结构，不扫描全部历史持久行，
避免多 GB 邮件库让启动或首个详情请求长时间阻塞；显式/离线 `EmailStore` 调用默认仍校验完整持久行。
初始化失败仍由既有邮件可用性边界隔离并如实报告，不阻止其他 Console 路由启动；显式/离线完整校验只读取
用于持久状态核对的列，不把 `email_classifications.model_text` 正文或分类任务完整输入载入内存。邮件 Attempt 详情每次通过新的数据库连接
读取当前分类和退订回执，不缓存详情或记录内容，也不重复扫描整库持久化状态。

Email Console 的 learning、model-version、folder-binding 和 classification-detail API 只投影版本、
门槛、计数、时延、fallback、动作/readback 与 continuation 状态。它们不返回正文、附件字节、
embedding、OTP、完整退订 URL 或私密浏览器数据。分类确认不会创建通用 CEO task；系统在任何阶段
都不发送、回复或草拟 Email。常驻 classifier runtime 将有界的阶段耗时、cache-hit、结果和受控
fallback code 写入 `EmailStore`；Web 进程只读取这些跨进程聚合，不依赖注入 worker 的内存对象，
也不保存请求文本、向量或原始错误理由。每次 provider 扫描还会更新独立的“最新观察”投影；
classification detail 从该投影读取当前文件夹事实，不从冻结训练 snapshot 推断，也不在 API 请求中
访问邮箱网络。训练相关字段明确使用 snapshot 命名，避免把历史冻结状态误称为当前状态。

Email Console 的“模型训练”页读取后端统一计算的晋升资格。默认门槛为 Micro F1 ≥ 0.95、
每个启用类别 Precision ≥ 0.95、逐类独立测试 support ≥ 20、常驻端到端 P95 ≤ 500ms。
门槛通过带 expected-current version 的接口追加新版本；修改门槛不激活模型。
现有双候选、important 和完整性条件继续适用，并比较候选与当前描述集和类别集合。
缺失指标显示未测量，历史 classifier-head latency 不充当端到端测量。

用户打开主模型开关后，runtime 在 Registry 锁与配置写锁内重读当前事实、重验候选，并原子
替换 online-active.json。该文件同时持久化运行模式、模型身份和模式切换历史，是这次切换的
提交事实源；进程中断不会留下没有切换身份的已激活模型。关闭开关保存 Agent 主分类及历史，
不删除任何模型。worker 在 tick 时读取新事实，不需要为开关本身重启进程。
描述编辑把当前配置和不可变版本快照在同一 SQLite 事务中保存，过期编辑返回冲突。

### Repository Upgrade

日备份目录按既有单副本保留策略，只保留一个最新的已验证日备份；未完成的副本不作为恢复依据。

服务周期性读取配置的 `origin/main`，只识别可安全 fast-forward 的更新；分叉、状态指纹变化或
脏工作树不会被静默覆盖。History 页面只展示状态并启动带 operation ID 的 detached updater；
日备份循环只保留一个最新的已验证日备份；部署与日备份共用备份目录，仍只保留一个完整副本。

updater 在共享 Git 锁内重新校验指纹，必要时按用户确认的分支名和提交信息保存本地改动。服务静默并停止后，每次部署从当前数据库创建独立 operation ID 的一致性备份，不复用同日或此前部署的旧副本。日备份仍每天一次；当天完整副本不存在时创建新的当前快照。

备份共用目录锁；持锁时先清理仅由本流程生成的 `.ceo-backup-stage-*.tmp` 半成品及其 sidecar，保留其它临时文件，再在同目录生成一个候选文件：完成 SQLite backup、DELETE journal、integrity_check、完成标记提交并关闭连接，再读回完成标记，才原子替换目标文件并清理旧备份及该目录中残留的候选文件。复制或验证失败只清理本次候选，原已验证备份保持不变；替换后清理失败仍保留新完整文件并明确报错。验证期间允许新旧两份共存，成功后仅保留最新一份。原子文件名替换不等于已验证突然断电后的持久性；没有新增 fsync 保证。

部署在新备份创建后、服务仍停止时取得同一目录锁，持续持有至健康／加载验证、发布收尾或失败回退结束，防止刚启动的日备份线程提前删除回退所需的来源。日备份是独立组件线程，等待该锁不阻塞 API 启动。部署备份失败时不切换代码，沿用既有失败恢复流程。每小时日备份循环确认当天副本完整后，继续清理数据库目录中超过24小时的 `auto-reply.sqlite3.pre-*` 及其 sidecar。下一次日备份可以用更新的当前快照替换部署前快照；本策略不长期保留旧 schema 数据。随后沿用既有依赖、构建、启动与健康验证流程。收尾验证见[迁移收尾](database-migration-closeout-validation.md)。
等待静默期间，updater 不向共享 SQLite 写部署进度，避免与仍在运行的业务写事务争锁；服务静默并停止后，才用不触发 schema 初始化的窄状态存储记录后续阶段。该短写仍对意外的并发 SQLite 锁做有限次数重试。
升级后验证失败时只对本次安装的精确 commit 做 compare-and-swap 回滚；无法证明仓库仍归本次
操作所有时进入 `needs_manual`，不执行破坏性 Git 操作。MCP 配置不由该流程探测、禁用或覆盖，
直接沿用用户当前 Codex 配置。

**生产检出与部署**（Derek 2026-09-25）：服务不从开发树运行。launchd 运行独立检出
`~/Services/ceo-agent-service`（安装脚本把 `CEO_SERVICE_ROOT` 写成它所在的检出；代码里是
`app.config.service_root()`，模板和代码都不写机器路径）。这个检出没人编辑，只会前进到已推送的
`origin/main` 提交，所以上线的永远是完整提交，任何会话都可以部署：`python -m app.deploy`
复用上面的 updater，先等没有进行中的 Agent 回合和已领取的条目（30 分钟内不空闲就什么都不改），
再停止服务、复用最近 24 小时的已验证数据库备份（没有则新建并验证）、fast-forward、控制台不是从检出当前的 `frontend/` 构建的时重建（比较构建戳 `app/static/workbench/.built-from` 与 `HEAD:frontend` 的树，而不是看本次部署的差异——中途停下的部署或别的会话插进来的部署会让下一次差异里没有前端改动，控制台就停在旧版本）、检查 import、重启，并轮询健康最多
15 分钟（重启要重读数 GB 的数据库，负载高时曾用 8 分钟，只探一次会把正常升级误判回滚）。
需要只发布已批准的精确版本时，可加 `--target-commit <40 位 SHA>`；该 SHA 必须指向当前 `origin/main` 历史中的 commit 对象，且不能使生产倒退。即使 main 后续快进，部署也只合并该 SHA；等待空闲后重新获取 main，若已不再包含它，会在常规停止/备份前退出。网络获取期间若又有任务进入，则在同一空闲等待期限内继续等待，不会直接停止正在运行的任务。不指定时沿用当前 main 的普通部署行为。
`launchctl bootout` 成功后还会等待 `launchctl print` 明确确认服务 label 已卸载，避免异步移除期间过早 `bootstrap`。
静默检查将会议和回复任务处于 `processing` 视为在途工作，仅当它们有有效的对应 dispatcher 租约，
或分别存在运行中的 meeting alignment / Agent 运行记录时阻止部署；没有这些 owner 证据的孤儿行
由服务启动时现有的恢复逻辑重新排队。Email processing 动作通过明确支持的 publication 源投影计数，
覆盖旧 Email 表和当前 common Action 的状态字段；没有 Email migration ledger 的 Store-only 数据库
不贡献 Email 动作计数。仍锁定的会议投递继续阻止部署。静默轮询每次只读计数后关闭数据库连接，
避免长等待累积 SQLite 句柄。
紧急部署可显式使用 `python -m app.deploy --skip-quiet-wait` 跳过在途工作等待；部署仍通过
updater 正常停止服务后才备份和切换检出，重启时由既有任务恢复逻辑接管中断工作。若切换在新版本
安装前失败，updater 会重新启动旧版本并检查健康；设置重启也会在 launchd 任务已卸载时重新 bootstrap。
两个会话同时部署由仓库锁串行，后到的发现检出已前进就停止。只改了设置、没有提交要部署时（有些设置，比如邮箱账号，是 worker 启动时才读），用 `python -m app.deploy --restart`：同样先等没有进行中的工作，再经 launchd 重启并等健康；确需立即重启时可加 `--skip-quiet-wait`，不手动 `launchctl kickstart`。生产检出里不能提交也不能跑测试（Derek 2026-09-25，此前有会话在那里跑测试并就地提交，检出与 main 分叉，之后所有部署都停下）：部署时装上 `pre-commit` / `pre-merge-commit` / `pre-rebase` 钩子，一律拒绝并提示去开发树改；`tests/conftest.py` 发现自己在生产检出里就退出。部署只做 fast-forward，不触发这些钩子。检出若已分叉，部署停下并列出只在生产里的提交，不会自动丢弃。`app/`、`frontend/src/`、`tests/` 在两次部署之间还是 chmod 只读（Derek 2026-09-28：上面三层防的是"改动悄悄上线"，这层防的是"改动被写下来"本身）；部署把这几棵源码树的解锁窗口精确框在 checkout+构建+校验期间，`finally` 里无论成功、回滚还是异常都重新上锁。`data/`、`.env` 和这三棵树以外的构建产物（`app/static/workbench`、`frontend/dist`、`frontend/node_modules`）保持可写——服务运行时和构建步骤本来就要写它们；`app/static/workbench` 虽然物理上在 `app/` 里，但只有构建步骤会碰它，而构建步骤总是在解锁窗口内跑。

仓库 mutex 覆盖源码解锁、停机、备份、快进、构建、校验、启动和重新锁定；settings-only restart 也使用同一把 mutex。若中断只留下未暂存文档，且每个文件都与刚 fetch 的目标提交逐字节相同，正式 deploy 会先停服务并备份，再把这些文件恢复到当前 HEAD，由正常 fast-forward 安装目标版本；任何暂存改动、其他路径或内容不完全一致的工作树仍按本地修改保护规则拒绝部署。

### 会议投递目标

日历仅用于确认参会名单，不决定业务内容的披露受众。会议跟进使用同一 `MESSAGE_AUDIENCE_CONTRACT`：根据实际内容、近期讨论、完整当前成员、
稳定身份、当前职位职责和具体披露范围选择群或明确私聊受众，不因业务内容或参会人数强制发群。
相同适当受众合并一条；不同受众才拆分。明确业务私聊的稳定 `user_id` 必须唯一匹配来源参会人
或日历证明的组织者，身份和受众理由保留在决策中；不可从逐字稿发言人推断组织者。
群不可发送时保留失败，不能静默换成组织者或本人。私聊入口拒绝本人及已核验别名；
职务、HR 身份和私聊本身均不构成披露授权，身份检查也不替模型证明业务理由真实。
群标题相似、部分参会人重合或近期活跃本身不构成业务承接证据。
裁员、减员和人员优化的存在、进展、时间及执行安排本身就是人员敏感信息；没有姓名、名单或人数不构成脱敏。共享消息受众合同和会议/人员 Skill 要求逐一核对完整群成员（含未参会者）对实际正文的披露权限；业务相关、历史讨论、职务、参会人重合和人员准确率均不授予权限。任何成员未授权或权限未知时，业务群正文、标题、问题及任务提及均排除该敏感内容及其间接暗示；只有独立核验的授权受众才能接收，找不到时保留人员事项未解决，不转发本人兜底。敏感分类和内容授权由 Agent 根据证据判断，不增加运行时关键词过滤或比例授权阈值。
找群先核对会议材料里明确提及的讨论群，再从结论、行动和负责人提炼主题，分别以原文中文
业务词、英文术语、会议标题和核心议题查群及群内消息。预置候选或首次搜索零命中不等于穷尽；
候选须核对近期同工作线讨论、行动承接人、受众和可发送性，并在决策中说明来源与业务承接关系。
搜索结果按会议标题/摘要与群名筛选并读取完整群成员。成员匹配统一使用准确率 `member_precision = 匹配参会人数 / 完整群成员数`（3/N），不再使用参会人召回率或原有“至少两人且覆盖60%参会人”的过滤条件。业务讨论和主题仍先于成员准确率排序，准确率用于同等业务证据的候选比较，不能覆盖实际业务归属或替代披露权限。候选保留完整成员 IDs、群人数、匹配人数、准确率及参考参会名单是否完整；历史投递只记录次数，不标记为已验证受众。平台中立的发现服务只返回候选及证据，不决定最终目标或发送；近期消息截取和历史送达只是发现线索，Agent 仍需核验完整相关讨论与当前受众，不得按搜索顺序截断候选或仅凭词项匹配判断披露范围。

DWS 明确的保密群历史拒绝由适配器转换为候选级 `GroupHistoryReadDenied`，保留该群 ID、成员证据、代码及原因，讨论内容/分数保持未知，并继续核验其他候选。全部拒绝仍为明确 `history_read_denied`，不变成无群、空历史、可发送或自动私聊依据。拒绝证据到达 Agent 不解除权限；无法核实受众或内容时仍保留真实失败。临时及未知 provider 故障保持原失败/重试语义。
多个合理群不是人工选择条件，由 Agent 按本次议题和行动归属排序，不能用宽泛群凑数，也不能把敏感内容送给无授权受众。

个人、非业务内容仍要求完整日历两人名册才能私聊另一位参会人；逐字稿不能证明两人会议。
业务私聊不是个人分类或组织者 fallback，必须有明确内容目的、实际职责与非本人稳定受众。
投递前重复核验相同身份，先检查两个接收对象再发任何一条；同一私聊受众的正文合并一次发送，
不凭 HR 职务吸收本应给别人的内容。恢复已有准备或主消息回执时，先精确核对持久化正文：旧拆分正文继续使用原敏感动作身份，不因收件人相同声称已合并完成，也不改写主消息或重复发送。正文不匹配或合并正文与已准备敏感动作冲突时失败。技术或目标校验失败不得伪装成人工决策问题。
会议没有实质分歧时，仍发送简短的已确认事项和下一步；只有已确认删除的听记因原始内容
永久不可读取而记录为 `no_action`，其他听记读取、群发现或投递问题仍必须可重试或明确失败。

正式分析恢复沿用 `rerun_meeting_alignment_jobs` 和原会议业务身份；事务内拒绝部分效果、
已准备/已有回执的主消息或敏感消息、活跃 dispatcher/投递归属及仍活跃的 meeting/runtime run。
旧运行保持历史，只在确认未发送且无活跃归属后清空原决策重做实际来源与受众核验。
当 DWS 逐字稿接口故障而完整 MCP ASR 结果可用时，`import-meeting-asr-transcript` 通过原
meeting job ID 绑定模型、音频 SHA-256、分段时间和完整分块数，并以单事务重新排入同一个未发送
任务。入口只接受有完整日历参会人证据的失败任务；ASR 不提供说话人身份或受众判断。已有部分
效果、投递回执、活动领取或运行时回合都会拒绝导入。分析和投递阶段读取同一持久化转写，保留
原失败运行；外部消息仍只能由原 Meeting 审查与投递流程产生。
独立 Meeting Alignment planner/delivery 仍是独立路径；共享受众规则不代表迁入通用 Consumer/Audit 引擎。Meeting Alignment 没有任务内 Skill 读取能力。Codex 路线在本轮有冻结的托管会议 Skill 时，提交本轮定时任务提示和该托管 Skill 的完整规则，省去已由服务完成的操作 Skill 正文；原生 Skill 目录仅保留 `dingtalk-chat` 入口供 Agent 按需使用 DWS 核验群，其余重复 Skill 隐藏。其他路线仍提交全部冻结 Skill 正文。没有托管会议 Skill 或冻结材料时，沿用完整原输入及旧自定义 inline 约定。结果模型、受众核验与投递流程不变。

Meeting Codex 命令设置原生 `project_doc_max_bytes=0`，不再额外注入工作区 `AGENTS.md`。会议来源、工作人格和本轮冻结的托管 Skill 仍由服务提交；全局原生指令、Schema 和独立会话保持原样。

Meeting 群候选的受众核验要求在非空候选列表前完整提交一次，不再在每个候选项里重复；
候选的群身份、完整成员名单、近期讨论与读取状态仍逐项保留。空候选列表保持原输入。

`audience_scope` 是当前 Meeting Alignment Agent 输出的必填字段。投递器读取早于该字段的
持久化 `send` 决策时，只能从其已保存的 `target.kind` 规范化一次：`group` 对应 `business`，
`direct` 对应 `personal`，并立即写回规范 JSON；缺少目标或其他字段不合规的记录仍按失败处理。
该规范化不重新分析会议、不改变目标，也不重发已有送达记录。

## History 语义与无效入口边界

History 页面顶部固定展示与独立 Attention 页面共用的 Attention 面板，直接读取同一 Attention API；它不改变记录筛选、计数或终态语义。

Status 的 Attempt 队列统计先按触发对象选出最新 Attempt 标识，再读取当前状态和失败详情，排序不携带历史错误正文。
Status 的 Reply task 当前业务对象投影和 Email 当前计划动作投影各在单次 SQL 中只读取一次，
随后计算计数、重试、最新时间和错误。每次请求仍使用新的只读快照；当前对象范围、大小写与空值
处理、失败／重试划分以及更新时间和 ID 的最新行排序均沿用原口径。

History 分页先在同一数据库快照中筛选、计数和排序记录标识，再只读取本页记录的展示正文；总数与内容保持同一时点，正文读取量随页大小变化。Reply Attempt 的操作日志按每个任务全部关联 Attempt 的最大公开 ID 排除较早记录，另按运行与任务的当前 execution generation 排除过时代次；最新 ID 汇总不限制代次。

History 是任务和执行记录的单一展示入口。同一个任务不得被拆成多条当前队列记录；
队列任务与执行记录都必须保留各自真实状态。筛选条件的业务含义固定如下：

- `status` 只筛选执行状态；不再使用含糊的 `type` 名称。`status` 和 `object_type` 都可用逗号
  传多个值（Derek 2026-09-25：两个筛选都是可多选的复选框菜单，状态显示中文名，页面标签为
  「状态」「任务类型」，原来的快捷状态按钮已去掉）；`done` 同时包括 `sent`。
- `object_type` 只筛选任务类型。类型只有一份定义：服务端注册表
  `app/history_types.py`（值、中文名、顺序）。History 联合查询写出的 `history_type`
  取自它，页面从 `GET /api/console/history/types` 读取筛选项，行上的类型徽标用同一个
  中文名；读不到类型表时页面只提供「全部」。不在注册表里的值（包括已退役的
  `replay`）不做筛选，按「全部」返回，页面筛选框也显示「全部」（Derek 2026-09-25）。
- 类型按顺序为：队列 `queue`、钉钉消息 `dingtalk`、日历邀请 `calendar`、OA 审批
  `approval`、邮件 `email`、邮件退订 `email_unsubscribe`、邮件动作 `email_action`、
  微信 `wechat`、会议跟进 `meeting`、Task `task`、定时任务 `scheduled_agent`、
  定时命令 `scheduled_command`。同一执行记录只能归入一个类型。`reply_attempts`
  依次判定：OA（`action='oa_approval'` 或带审批实例）→ 微信渠道 → 邮件退订 → 其余邮件
  渠道 → `channel='scheduled'` 的 Agent 形式定时任务（每日总结、周报等）→ 日历邀请 →
  钉钉消息。日历邀请用的是邀请生产者自己的判定
  （`DingTalkAutoReplyWorker._is_calendar_message`：`[日程]` 开头，或内容含
  `newCalendar=1` / `calendarDetail` / `uniqueId=`，含 URL 编码形式），另加 attempt 上的
  `calendar_response` / `calendar_reconciliation` 动作或 `calendar_event_id`；测试把两处
  判定绑在一起。OKR 评审 attempt（`action='okr_review'`）归入钉钉消息。
- History 的逻辑来源是 `HISTORY_SOURCE_TABLES`：`reply_attempts` 投影、会议、Task 各来源，
  以及两类新来源。Audit web 启动不预热大范围 History 查询；打开尝试详情时批量读取该任务代次的
  Agent run 及工具事件，图表只投影所选时间范围内 attempt 的当前 History 状态。
  页面缓存以来源表元组为键。
  `errors` 不是 History 来源，其分支的 `service_error` 类型只出现在旧 `/logs` 页。
- 定时命令（`scheduled_task_runs`）：失败的触发一律显示；同一定时任务之后有一次成功
  运行的，失败显示为 `recovered`（服务在那次成功时解决它的 Attention 条目），否则为
  `failed`。成功的运行只显示结果不会出现在别处的命令（`consumer_prompt_enabled=False`：
  同步 Chrome 登录态、听记同步、听记权限申请、OKR 周报），显示为 `done`，结果行是命令的一行摘要；把结果
  交给 Agent 的生产命令（读取新钉钉消息、日历邀请、邮件等，每天数千次且几乎都是
  「没找到」）成功时不显示，它们排入的每一项自己就是一条 History。Agent 形式任务因
  运行时或 Skill 不可用而跳过的触发显示为 `skipped`；上一次还在运行而跳过
  （`scheduled_task_previous_execution_active`）是调度的防重叠记录，不显示。Agent
  形式任务的失败触发归入「定时任务」，其余归入「定时命令」。详情链接到
  `/scheduled-tasks?id=<任务>`（没有单次运行的页面）。
- 邮件动作（EmailStore 在同一 SQLite 文件里的 `email_actions`）：每个动作一行，标题是
  邮件主题，来源是发件人，结果是动作（移动到「文件夹」、归档、移到垃圾箱、标为重要、
  标为已读、打标签）及错误。状态沿用 Attention 和状态页的口径：只有当前计划、已处理
  邮件上用尽重试的失败才是 `failed`；还在重试窗口里的失败是 `pending`；被新计划取代
  或邮件不再处理时，未完成的动作是 `skipped`。所以 History 的失败数不会多出 Attention
  没有的邮件动作；定时命令的失败与 Attention 的 `scheduled-task:<id>` 条目同样一一对应，
  唯一的差别是依赖短暂不可达时，服务要等故障持续 15 分钟才写 Attention，这段时间里
  最近一次失败只在 History 里。详情链接到 `/email?tab=all&selected=<邮件>`。
- 这两类来源与 `reply_attempts` 一样按 `occurred_at` 排序，时间统一成
  `YYYY-MM-DD HH:MM:SS`（UTC）。History 图表仍只统计回复 attempt、会议和 Task，不含
  定时命令和邮件动作。
- `reply_tasks` 的 `pending` 和 `processing` 是当前队列任务，必须在 History 中按真实
  状态展示、筛选和计数；它们不属于 Attention。还没有 Attempt，所以不能像已完成记录
  那样链到执行详情：`channel='scheduled'` 的队列行链到它所属定时任务的运行记录
  `/scheduled-tasks?id=<任务>`（映射来自 `scheduled_task_runs` 里 `execution_kind='reply_task'`
  且 `execution_id` 等于该 reply task id 的那一行，`AutoReplyStore.scheduled_task_ids_for_reply_tasks`），
  其余渠道链到 `/workers`（Derek 2026-09-27：点开原来一律落到 Status 页，看起来像详情坏了）。
- History 在页面可见时每十秒读取当前快照；已经进入终态的队列任务不得因页面保持打开而继续
  显示为 `processing`。
- 微信候选任务的 `done` 不证明消息已投递。同一任务、会话和当前执行代的最新投递为
  `failed` 或 `send_unknown` 时，失败 Attempt 在 History 筛选、详情、状态计数和图表中仍为
  `failed`；旧代、其他会话或已被后续成功投递取代的失败不覆盖当前任务状态。读取投影不改写
  Attempt、任务或投递台账，也不触发重新发送。
- 24 小时图表统计的是该小时内发生的历史事件，不是当前队列的状态计数。重试和执行开始须以
  独立事件标签呈现，不能使用 `processing` 改写旧失败记录；实时 `processing` 只由当前队列
  列表和状态页统计。
- `okr_review_requests` 的队列状态不能覆盖对应执行记录的 History 状态。若同一对象已有
  当前队列记录，列表以这条队列记录承载 `pending` 或 `processing`，不再制造重复的当前状态行。

History 不承诺旧查询参数或旧 URL 的兼容别名；接口和页面使用当前语义，历史数据只
通过当前代码的分类规则重新解释。旧值不会报错，只是不再筛选。

“待处理服务修复”不是运行时能力：它没有生产者、处理动作、修复执行器或闭环，不能
作为服务健康状态或执行队列的一部分。导航、History 卡片、页面和路由不提供此入口；
`feedback_events` 及其反馈流程仍保留。服务修复候选是业务记录，按数据库重设计迁入
`business` 的 `service_bugfix_candidate` 类型，保留原字段、公共 ID、水位和唯一
`feedback_event_key`；当前初始化不再新建旧 `service_bugfix_candidates` 表。迁移从已有
旧表搬运记录并提供可验证回退，不能把该存储合并误解为重新开放已移除的页面或路由。

当前未发布的开发候选已选择 Feedback→Actions：`feedback_events`、
`feedback_processing_transitions`、`feedback_iteration_decision_items` 分别使用 Actions
投影和写入；Store、QualityGate 与 Feedback API 仍保留原字段、顺序、计数、事务边界和
处理关联。事件可空文本键与隐藏 ROWID 分开保存，transition 保留独立 AUTOINCREMENT
水位，decision item 保留三条指向 Tasks 生成列的外键及追加后不可更新/删除约束。
新建与重开数据库都不再创建这三个原表；配对发布在 Errors 转换之后迁入 Feedback，
逆转换先恢复 Feedback 当前事实和原声明，再恢复 Errors。当前候选为 28 张普通表；
生产发布、最终十张核心表加 Email 迁移 ledger 仍待各自验收。

## 设计目标

CEO Agent Service 是本地优先的企业消息处理服务。它发现需要 Derek 处理的消息、
审批和任务，由两个 Agent 准备及审核候选，再由系统执行：

- **Consumer Agent A** 理解业务、读取当前事实并提出精确候选；它使用同一原生
  runtime，按角色限定可用能力，不发布候选中的消息、审批等受控外部动作。
- **Audit Agent B** 只读独立审阅整份候选，包括拟提交给 Derek 的问题；不发布外部动作。
- **System Executor** 执行审核通过的持久化精确方案，并保存 provider 回执与回读；人工选项执行绑定的原方案，不由模型重写。
- Service 负责触发发现、队列、会话指针、角色编排、严格结果校验、租约恢复和精确重复
  投递保护，不替 Agent 做业务判断。

## 单一服务进程模型

生产环境只安装一个 launchd job：

- job：`com.ceo-agent-service.main`
- 入口：`python -m app.service_supervisor`
- worker 子进程：`python -m app.cli service`
- audit-web 子进程：由同一 supervisor 托管，默认监听
  `http://127.0.0.1:8765`

supervisor 同时管理 worker 和 audit-web。任一子进程异常退出时，supervisor 只在同一个
launchd job 内退避重启该子进程，健康子进程继续运行。不要安装第二个 audit-web plist，也不要
恢复双 launchd 模型。

launchd 和本地启动脚本都设置 `PYTHONDONTWRITEBYTECODE=1`。worker 与审计 Web 会并发
导入同一份代码；禁止写入 `.pyc` 缓存可避免缓存文件锁把一次正常启动误判为服务失败。
### Codex 会话隔离

同一 `conversation_id` 的 Consumer A 先取得持久会话锁，再通过原子任务认领启动 Codex；
不同会话可以在同一个 launchd 服务内并行执行。Consumer 与 Audit 的重试都优先继续各自已有的
原 Agent session；feedback、Skill 更新或 revision 只推进业务决策/Skill 版本，不会自动清空
session。每次 turn 仍记录当前契约哈希作为回执，但哈希变化不是会话身份边界。只有 runtime 明确
确认原 session 不存在、不可访问或认证上下文失效时，才建立新的 session，并保留旧 session lineage。
服务不再用全局进程锁串行化所有 Codex 调用，否则一个长会话会让无关会话已认领却无法运行。

跨 worker、审计页面和服务重启的竞争由 SQLite 会话锁、Agent run lease 和结果回读处理；
同一会话顺序不依赖共享的进程级锁。

服务重启时，未完成的 Agent turn 按普通失败重试；已完成 Agent 回合会从持久化结果继续。普通重启保留同一任务的 execution generation、session 和外部回执；明确完成运行时、路由或本地环境修复后，服务修复重试可以创建新的 execution generation，但仍优先续用可访问的 session。两者都不创建独立的 unknown 或状态核对状态机，也不根据工具事件替 Agent 判断外部动作结果。下一次 Agent turn 按当前业务 Skill 读取外部状态，再决定是否继续。

Task Agent 的正常 liveness 由结构化 provider 事件驱动：每个有效 JSONL 事件都会续租 Agent run 并作为进展证据；连续 5 分钟没有事件时，idle watchdog 才会中断该 turn。`CEO_TASK_CODEX_TIMEOUT_SECONDS`（生产为 7200 秒）只是防止泄漏进程长期占用资源的紧急上限，不是长任务的业务完成时限；只要有进展，任务可以在同一 session 上持续或恢复多个 turn。

### Schema 初始化竞争

`AutoReplyStore` 和 `EmailStore` 在构造器缓存或 journal 修改前只读核对准确当前声明。真正空库在共享路径文件锁内直接建立当前结构；准确当前库冷启动不执行 DDL。旧版本及缺失／损坏对象必须由显式发布工具处理，运行时不迁移、修补或回填。短暂 `locked`／`busy` 仍沿用有限等待复查，不把锁错误当成结构缺失。Email 完整持久行校验在释放 schema 锁后执行。

### Workbench 启动恢复竞争

审计 Web 启动时会执行 Workbench 恢复。若恰好与 worker 的 SQLite 写事务重叠，恢复会对短暂
`locked` 或 `busy` 进行有限次数重试；非锁异常或超过上限的锁仍按服务错误处理。这样一次并发
写入不会使审计页直接启动失败，也不会吞掉持续的数据库异常。

### 外部动作结果与重试

自动重试共享上限为 3 次 retry（含首次调用最多 4 次尝试），由
`app.external_retry.MAX_AUTOMATIC_RETRIES` 和
`MAX_AUTOMATIC_ATTEMPTS` 定义。DWS 瞬时调用、同路由容量重试、浏览器瞬时失败、
普通任务和系统执行都引用该共享上限，不再各自维护相同数字。持久化 Consumer 与 Audit 失败预算分角色记录，
每个角色最多四次失败尝试；权限申请后的材料重读采用独立节奏预算，最多三次、
每 12 小时一次、原申请起 72 小时截止。只有可安全重试的技术失败进入自动预算；
非重试错误直接终止，任何外部效果不确定时先对账原对象，不因次数预算自动重放。
这些是彼此独立的预算，不合并相加；等待时间、Agent 内部模型轮数和只读轮询不是自动重试次数。

OA 权限预算耗尽后停止文件重读，由 Consumer 再运行一次提出普通 OA `return` 候选；
该轮不得再次申请权限或安排重试，但退回前必须对被阻塞文件实读一次：能读则按正常审批处理，仍读不了才提退回。候选仍经 Audit、System Execution 和节点读回，
服务不会合成或直接执行退回。明确的权限申请失败也走同一流程；无法形成有效候选时才进入
人工处理，不能把外部权限阻塞记为 `failed`。非 OA purpose 耗尽后仍可投影为
`permission_blocked` / `blocked`，在 `needs_human` 队列外等待；History 的一次人工访问检查
入口保留原预算且只适用于权限已开通后的确认读取。

权限预算属于业务对象，不属于某一次触发。同一审批在预算耗尽后收到新触发（新的 execution generation，尚无本代 Consumer run）时，若还没有已认领的 OA 退回轮次，就启动一个禁止再读取材料的 Consumer 轮次，提出普通 OA `return` 候选并继续 Audit、执行器和节点读回；该业务对象只认领一次，后续重复触发挂靠最近一次已完成的 Consumer run，记为 `permission_blocked` / `blocked`，避免重复退回。没有可用的 Consumer run 时进入人工处理，不记为权限 `failed`。

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

OA 退回回执按钉钉实际响应字段 `dingOpenErrcode=0`、`result=true`、`success=true` 识别成功；流程操作记录的数字时间按 Unix 毫秒解析，字符串时间按带时区 ISO 或钉钉本地时间解析。若 OpenAPI 操作记录只有秒级时间，读回同一实例的 DWS 结构化审批流水，并按操作类型、操作人及同一秒唯一关联其毫秒 `operationTime`；有多条匹配或来源不完整时仍保留 uncertain，不能放宽派发时间边界。成功响应只允许进入只读对账；旧回执若没有 `accepted_at`，只有在上述成功字段通过校验时才以持久化的 `response_received_at` 作为接受边界。完成仍要求原任务已取消、同一审批人唯一写入精确备注的 `REDIRECT_PROCESS`、目标节点出现对应新任务，且全部时间落在派发和接受边界内。若目标任务创建时间被钉钉截断到秒级，只有当时间桶从退回事件之后开始且完整早于下一操作，或该桶与唯一退回流水落在同一秒、OpenAPI 与 DWS 流水在该秒内均无其他操作且该桶与派发/接受区间相交时，才可将其作为目标节点读回；仍须匹配精确目标 activity 与实例。精确到毫秒的目标创建时间继续按精确事件区间判断，桶与操作边界相交时保持 uncertain。证据不完整时保留 uncertain，恢复不重发退回。

`review_candidates` 的执行块保存租约；`candidate_execution_alias` 仅保留原执行物理 ID、公开 ID 和候选 FK，旧形状执行投影连接别名与计划读取当前状态。`candidate_action_attempts` 的 `execution_id` 继续指向别名，在 provider 调用之前保存 dispatch 边界；已验证回执复用。进程中断或超时没有回执不能证明未发生效果：先读取原外部对象。确认成功继续，确认没有效果才可重试同一计划，无法消除的歧义保存 uncertain/failed，不伪造完成或人工问题。技术恢复不重新审核不变业务方案；业务事实改变则使候选失效，重新形成并审核方案。没有通用 shell 或 Agent 执行兜底；未支持的动作明确失败。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

当前 Attempt 的结构化运行结果 `code` 或 `source_code` 为 `provider_risk_rejected` 时，历史“重新处理”入口不可用，直接提交该入口也返回冲突且不入队；API、React 和原生 HTML 优先展示该原因。确需新的候选或执行范围时，应通过明确的本次任务和完整候选提交处理，不能用旧入口重放历史候选。对于后端允许重新处理的其他失败，`rerun_label` / `rerun_confirmation` 仍由同一业务对象的结构化历史提供；跨执行代的历史拒绝可使措辞显示“重新评估候选”，普通技术失败保留“重新处理”。只认确切的顶层结构化错误，不匹配正文、嵌套文字或其他对象；System 的既有历史拒绝限制和原始记录继续保留。

需关注中的新审核问题说明缺少的可复用规则、适用任务类别和判断依据，并提供 2–4 个 `task_class` 选项；不得要求 Derek 决定当前具体业务动作。历史问题、原状态与已存选项保留，但不因此重新激活或执行；隐藏旧选项不表示已重新审核或业务完成。

组织架构审批中，空白或“业务调整”等笼统依据属于可向申请人或已核实责任人补齐的事实缺口：先核实接收方，再询问具体业务需要、受影响工作与人员、接收安排和汇报关系；保持待办并独立核验证据。不得仅因 Agent 未查到证据就升级 Derek；`needs_human` 留给真实政策/权限缺口或明确需要 Derek 作出的业务取舍。不得凭未核实陈述直接批准或拒绝。

正式 Consumer/Audit 契约发布使用 `python -m app.deploy --publish-consumer-system-contracts`，在服务空闲且停止、数据库备份已完成后，先校验并将恢复快照交给 updater，再开始替换十四个契约文件（包括通用 OA Skill、财务审批 Skill 与规则卡、people-review 和 attendance-review Skill）、六个 managed Skill 修订和受影响任务的完整引用。发布清单的 `old_sha256` 必须对应部署前实际安装文件，`new_sha256` 必须对应仓库源文件；人工维护的 Skill 引用必须先同步到已确认的安装内容，并对两个摘要分别读回。任一摘要过时或源文件与安装文件不一致时，发布前置校验应停止，修复清单或来源后再发布，不能覆盖用户的运行时 Skill。people-review Skill 包含招聘需求审批的前置时序与离职安全切换要求；attendance-review Skill 载明合理请假时长和 on-call 共存标准；新 OA 审阅轮次按当前运行时 Skill 绑定。发布中途失败也由 updater 持有该快照恢复；恢复失败时保留回执及原文件副本，记录 `needs_manual`，不回滚 Git 后启动可能与外部文件或引用不一致的旧服务。恢复成功后才启动旧版本；健康通过后仍核对 active 配置、全部启用绑定与新 worker 的加载回执/PID、十四文件 SHA 和完整任务引用，最后标记 verified。若启动时的任务版本对账基于本次发布配置创建了后代配置，只接受其仍沿父链追溯到本次发布，且启用 Skill 摘要、存活 worker 的完整加载回执、十四文件 SHA 和任务引用逐项匹配时通过核验。代码上线、资产发布和实际业务结果分别验收。

正式关闭的微信投递若在关闭原因中记录 `superseded_by_principal_reply:<source-message-id>`，表示本人后续回复已覆盖旧候选。仅在匹配当前任务、执行代和会话的投递上，详情状态保留 `skipped`，不被任务的 `done` 覆盖；页面说明已由本人回复、旧候选未发送且无需重试，不显示旧投递重试动作。手动重试的同一写事务读取该正式来源引用并拒绝重新入队，原投递、Attempt、执行代与回执不变。该判定读取分号分隔的精确关闭字段及非空来源引用，不匹配回复正文或一般文字。其他普通过期投递的现有手动重试合同保持。

## Skill-first 权威处理流

```text
trigger/context/materials
  -> Consumer business work and complete persisted candidate
  -> Audit read-only whole-candidate review
     -> return/reject: Consumer complete revision
     -> approve action plan: system execution and verified receipt
     -> approve question: visible needs_human
        -> exact stored option selection: system execution and verified receipt
```

Producer 只根据消息来源、会话类型、明确的 @、稳定卡片类型和去重标识决定是否创建任务。
系统**没有关键词业务路由器**：service 不通过项目名、人员名、百分比或业务词判断该加载哪个
Skill。Consumer A 根据完整上下文使用 Codex 原生 Skill discovery，按需读取业务 Skill 和操作
Skill。

Consumer 新会话的 Developer 只给出通用 Skill 使用指令；当前 Task 中的目录携带已安装业务
Skill 的名称、读取入口和至少一次业务 Skill 读取的协议前置条件。目录只声明可用能力，不替 Agent
选择领域；选择仍由 A 根据完整上下文完成。Codex 续会话沿用原生会话开场保存的 Developer，而本轮 Task 指令重新提交。Consumer 仅在已选 Skill 来源兼容、且原生会话保存的 Pydantic wire schema 与本轮 purpose schema 相同时续用该路线 session；旧 schema 或无法核实来源时，在该路线开启带完整当前输入的新 session。普通 prompt 文案和目录展示变化不因此重置兼容会话；Audit 会话不受此 Consumer 判断影响。

Service 也不读取正文后替 Agent 解释业务材料。它只传递 trigger、上下文、原始 process/task ID、
链接、本地受控材料引用和可执行的精确读取命令。文档、文件夹、图片、表格、日历、听记和 OA
材料是否相关、是否需要继续展开以及它们支持什么结论，都由 A 判断；B 在审核时独立复核。

Skill 加载属于 Agent 执行环境，service 不要求或校验普通 Consumer/Audit 结果中的 Skill receipt。
仅当发生外部写入时，SQLite 保留 provider 返回的操作标识，用于重试时识别已完成动作并避免重复写入。
SQLite 继续保存既有 task/run/attempt/provider result identifier 状态；系统**不建立平行的 Skill 审计数据库**，详细工具轨迹
仍以 Codex session JSONL 为准。

### 动态 Skill 分层

八个 CEO 业务 Skill 安装到 `~/.agents/skills`，按任务动态加载：

| 业务 Skill | 负责的业务判断 | 常见操作 Skill |
| --- | --- | --- |
| `ceo-message-triage` | 回复、反应、追问、无需动作 | `dingtalk-chat` |
| `ceo-calendar-invite` | 日程邀请是否接受、拒绝或追问 | `dingtalk-calendar` |
| `ceo-document-review` | 文档、文件、图片、表格的审阅路径 | `dingtalk-doc`、`dingtalk-drive`、Lark 文档 Skill |
| `ceo-meeting-work` | 听记、静默会、会议总结与行动项 | `dingtalk-minutes`、`dingtalk-chat` |
| `ceo-mail-review` | 完整邮件线程审阅和回复 | `dingtalk-mail` |
| `ceo-personnel-communication` | 人事信息的受众、可见性和最小披露 | 候选人/通讯录操作 Skill |
| `ceo-work-tracking` | Task Agent 从来源提取 Task、证据化归属/承诺、关联正式 Project 与关注事项；返回结构化判断，不回复来源 | Task Agent 不直接写外部 TODO；合格 Task 经 Task 7 outbox 镜像 |
| `ceo-sales-weekly-report` | 按需核对销售目标、CRM 实际、公司及业务线进度评分并生成 workspace 周报 | `ceo-weekly-report`、`fxiaoke-crm-cli` |

`ceo-sales-weekly-report` 没有独立 producer 或功能开关。它由 Consumer 根据明确的销售周报请求动态选择，直接使用安装用户已有的 `sharecrm` 登录态；CRM 只读限制由 Skill 和 Codex automatic review 约束，不表示 service 建立了 `sharecrm` 命令白名单。

`ceo-work-tracking` 的发现描述与正文均限定 Task Agent 的结构化读取职责。Consumer
审阅项目文档并回复来源时使用 `ceo-document-review`，一般消息判断使用
`ceo-message-triage`；文档包含项目事实不改变调用角色。这是现有 Skill 职责的说明，
不增加正文关键词路由或改变工具权限。CI 资产的描述修订不等于已安装运行资产已发布。

### 项目为中心的工作跟踪（已发布；业务验收部分完成）

2026-10-04 获批设计的代码经 PR #16/#17 发布；当前生产运行 `a385b86d`，
包含 2026-10-06 确认的项目 Memory 写入规则。真实生产样本已保存 4 份独立 ProjectContext，
并在没有 Task 成员时生成 2 张关注卡片；这不代表全部项目或多来源业务验收完成。
较新会议因不连续原文引文在领域应用阶段失败，项目身份覆盖、历史上下文补充、
真实建议/Memory/客户关联正向读回仍有未完成项。发布记录、预期与实际及证据边界见
`docs/task-project-centered-validation.md` 当前状态；本节描述当前契约，不宣称所有样本通过。

#### 来源、项目与任务

所有会议、管理/部门/项目周报、聊天、邮件均可作为事实来源；周报不是唯一来源或风险判断前提。
正式 Project 须有真实目标和范围，不把部门、客户标签、主题、孤立小任务或相似性 cluster 当成项目。
确认的报告项目登记表行、明确会议立项可以登记正式 Project；聊天/邮件补充既有项目事实，
不单独建立正式项目身份。保留不同时间、来源的冲突与引用，不静默以某类来源覆盖较新事实。

独立 `ProjectDecision.registration` 引用当前权威定义，含 exact title、authority 与非空 source_excerpt；
不是 Task 的嵌套字段，也不需要 Task 或 cluster 作为登记载体。报告核验实际类型、完整原文登记行
中的项目列与提案标题；登记摘录只定位原文，不移动列。Task/行动章节不是项目登记表。
registration.source_excerpt 必须是当前不可变来源中定义该项目的连续原文；一份报告列出多个项目时，分别记录各项目的判断与证据，不能用一个项目的判断覆盖其他行。每个 project_decisions[i] 必须有且只有一个对应 project_assessment。项目标题与 Task 行动分处原文不同位置时，project_link_evidence 引用一段从项目标题到行动的连续原文，保留中间内容，不拼接不连续片段。
会议核验实际 AI Minutes 或可信 minutes 会话和当前原文引文；立项含义由 Agent 判断，不加关键词规则。项目标题只包含实体名称，不把同句中的状态或动作并入标题；例“甲客户一期交付项目正式启动”的标题是“甲客户一期交付项目”，启动状态可作为事实。
先采用当前权威来源的精确定义再选对象：只有唯一活动、已登记且精确同标题的 Project 才复用
规范 anchor 和原 registry provenance；多活动同名对象为身份冲突，无对象才用现有来源登记方法。
旧相似名称、简称或标题前缀不能替代新的正式定义；已退休的来源项目不由本轮重新激活。

Project 可选关联 Fxiaoke CRM 客户；CRM 身份单独保存为稳定 AccountObj `_id` 和显示名称，不拼进项目标题，内部项目可保持未关联。Task 不复制客户字段：只有 Task 通过已确认的正式 Project 关系读取其 CRM 客户；独立 Task 不推断客户。Task Agent 通过 prompt/Skill 引导，将当前证据支持的持久、重要项目风险和进度/状态更新写入 Memory `memory_write`：简洁注明项目、原始来源与来源时间，区分来源事实与有证据的风险推断。不写单条 Task/TODO、日常活动、临时讨论、无依据猜测、秘密、原始转录或重复信息。这项约束不增加工具白名单、通用只读模式或 CLI/MCP 代码拦截。

服务通过已认证的 `sharecrm data record query-by-name` 对 AccountObj 做只读名称解析。`crm_customer_evidence` 还必须作为同一条引用出现在 Project 的 `evidence` 中。该解析器不保证候选完整或精确，单候选也只是候选；来源扫描和 Project 页面查询得到的所有 CRM ID 都须经用户明确确认后才能建立本地关联。多候选展示供选择，`NO_MATCH` 与查询不可用分开保留。查询失败或后续冲突不会清除已确认关联；冲突需显式处理。客户视图按 CRM `_id` 聚合已确认关联的项目，未关联项目（包括内部项目）留在常规正式项目视图，不进入客户分组。

`ProjectContext` 独立于 Task 保存完整快照：goal、scope、最多一名 overall_owner 或 null、
其他人的不同职责 responsibilities，以及各自的事实 facts。每条职责/事实有实际原始 Signal/ref/
连续逐字引文；总负责与负责结果不能从 Task owner 猜出。未改变的职责沿用历史原文证据。
context=null 只补 Project↔Signal 关系；明确提供空分工快照才清空当前分工，旧版本仍可读。
当前 Project 读取最新 context revision，不双存可失配的 current JSON；相同结构快照不追加版本。
Agent 得到新事实时仍须返回完整的当前 ProjectContext 快照，带回先前有效职责、事实和出处；
不能因本轮只讨论一项新风险，就把未变化资料从快照中丢掉。
仅描述“某人负责某业务领域”的职责陈述只进入 ProjectContext，不单独生成 Task/candidate；
Task 需要来源明确的、可独立完成的交付物或行动，或由已证实重大风险引出的独立建议下一步。
正常项目序列中的例行里程碑和下一步即使写成行动句，也只记为 Project fact，不生成 Task/candidate。
总体负责人归属冲突时 overall_owner 留空，竞争主张与未确认移交记为有出处的事实，不能把候选总负责人改写成不同职责；ProjectContext 的事实必须明确写“总体负责人存在冲突，仍待确认”，不能只写“未形成一致确认/仍需核实”；独立负责人的真实交付职责仍保留。
会议明确某人总负责 Project 时，该人进入唯一 overall_owner 而不是一般职责；已有真实 Task 已直接处理同一风险时，用该 Task 支撑项目判断，不另建重复的监测/评估建议。
“X负责Y”仅描述职责，即使 Y 是独立业务领域，也不自动生成 Task；需要来源明确的独立行动/交付要求或真实行动项记录。
保留 existing_attention_id 时，assessment 必须引用当前 `current_project_attention.assessment_json.evidence` 中至少一条完全相同的原始证据三元组（signal_id、source_ref、source_excerpt）；新证据只能补充，不能替代卡片原始证明。
区别职责与行动时，“李四负责核实客户付款排期并反馈”是具体可执行 Task；“王五负责商务对账”若仅描述职能范围则是 ProjectContext。项目级风险的显示型建议可基于已保存职责，也可由唯一 overall_owner 承接跨职能协调，但不构成正式指派。

`business_source_documents` 保存不可变原文，Signal 通过真实 source_document_id 外键引用。
只有来源类型/ref/时间、会话、作者 ID/姓名/类型和正文八项完全相同才共享正文；Signal ID、
dedupe_key、上下文、Task 证据角色与历史仍独立。同 ref 的 memory/session 引文不冒充 observed 原文。
目标物理迁移仍需在配对事务中核对来源身份、正文、计数和引用，按已授权的例外版本处置原文；本运行时单元不执行该迁移。
这个段落描述原先的共享正文存储设计；当前未发布的 B/C/D 运行时已改用下面的精确来源读取契约。
Store 的 Signal 查询返回不含正文的事实和不可变来源绑定：原有七项来源身份、完整正文身份键、
SHA-256、UTF-8 字节数及可选的原生定位坐标。旧 `business_source_documents.body` 仍是此阶段
唯一选定的权威正文，Store 从该行暂时计算校验事实，随后在写事务外按身份键解析原文；
不存在 provider 失败后回读旧表的逐条回退。Signal ID、上下文、标题和去重键仍是各自独立事实。
独立请求中的相同精确版本只读取一次，写事务接收已准备的按 Signal ID 区分的事实及共享正文，
重新核对绑定和已有证据关系后才用原文验证引文。当前输入自带正文可直接加入这张请求内映射。
只依赖历史来源元数据的接受与身份引用也重新核对预检事实，不要求旧原文可用；
Attention 在主业务提交后的独立投影事务内重新核对既有卡片成员和 Project 原证明，
并在同一投影写事务中更新卡片，不把本次新建的 Task 证据关系误判为并发改动。
API 将有原文和原文不可用分别表示为非空正文或 null 加原因；已保存的引文、业务事实和来源链接
仍可查看。来源包以稳定身份键标识文档；原文不可用时不伪造字符长度。
2026-10-18 已授权对 20 个例外版本永久移除原文、保留事实/来源/回执；这个物理删除尚未执行，
不属于 B/C/D 运行时单元。
当前业务 schema 是 `2026-10-04.4`，不等于生产已迁移。

Console 提供两条只读的原始证据接口。`GET /api/console/tasks/projects/{project_id}/evidence`
按既有 Store 顺序分页返回 `BusinessProjectEvidence` 引用（project_id、signal_id、created_at），
不复制来源正文；第一页取最新窗口，meta 返回 total、has_more 和下一页 cursor。项目详情原有的
20 条有界来源及 context 引文 pinned 行为保持不变。`GET /api/console/tasks/signals/{signal_id}`
按精确 Signal ID 返回带明确原文可用状态的来源视图；可用时 `evidence_text` 是未经展示截断
的原文，不可用时为 null 并带原因。相同 source_ref 的不同版本按完整来源绑定区分，公开响应
不依赖旧 source_document_id。两条接口只读，不修改业务领域表；正文解析在 Store 写事务外完成。

Task 保留真实独立交付物和行动的身份。来源明确行动记 origin=source；缺真实负责人/授权等时
仍可为候选，不因为项目有总负责就伪造 Task 指派。新增任务必须非空标题；已有 ID 的更新可不填标题，
仅 update_fields 修改提供的标题/描述，promotion、acceptance、merge 保留持久化标题。
来源有明确的 authorized assignment、self commitment、external TODO 或具体有个人负责的
meeting action 才按既有 FormalityEvidence 正式化。AI Minutes 区分 owner_kind/owner_relation，
团队、仅发言或通用发言人占位不是个人负责人。owner ID 来自可信来源身份，不由 Agent 补造。

按项目事实和有出处的职责推导下一步，用同一 Task 表的 origin=agent_suggestion 与 suggestion_json。
suggested_owner 不写实际 owner；建议记录为 open candidate、commitment=none、无 formal_basis/
真实 owner/deadline，不产生 TODO、通知或 follow-up。最新消息不必再次点名建议人，历史职责和
事实均须有原文证明；Agent 理由或上轮建议不是人类安排。之后真实指派沿用同一 ID promotion，
保留建议发现历史，真实接受后不因 origin 阻止原有 TODO 资格。真实 source Task 不被重新标为建议。
后续更新必须携带已检索 Task ID，不从可共享/被合并复制的证据关系猜唯一任务。
仅改建议理由时省略 title/description 保留原值；确实变更才记事件。
发现尚未解决且需要推进的重大项目风险时，即使原文未直接指派某人，也可依据该项目已保存且有出处的
职责提出带 Project 关联、建议负责人和下一步的显示型 Task 建议；当重大影响与已保存职责直接相关时，
即使当前消息没有写出下一步，也应推导一条具体建议。每个重大项目风险优先形成一个建议；若来源说明
一个行动是另一个行动的前置条件或上游 gate，则视为连续依赖步骤，只为最近的阻塞结果建立一个建议，
在描述中带出下游影响，不按步骤或不同职责拆成多个候选。只有来源支持可独立完成且结果不同的行动时，
才产生多个建议。仅当现有 Task 处理同一未解决风险时才抑制重复建议；已完成或无关的项目 Task 不算
该风险已被处理。这不构成正式派活。普通进展、已解决事项、单独的模糊线索不因“可能有帮助”而生成建议。
顺序依赖的例子：同一交付/回款链中的“客户确认验收材料”和“确认付款日期”不因分别对应
不同职责就拆成两个候选；只建议最近的阻塞结果，并在描述中说明下游付款影响。建议标题须写出
待确认的项目关键结果以及必要的上游 Gate；若风险涉及付款安排，即使先处理验收 Gate，标题仍须
点明付款日期/排期，不能用“更新计划”等宽泛标题替代。
项目风险也可在没有 Task 时独立生成需关注；若没有已保存职责能支持具体下一步与建议负责人，不补造监测任务、负责人或期限。保留已有 Attention 卡时只沿用实际已保存的 Task 成员；若原成员为空，不因新建的同风险候选 Task 扩大成员集合。候选 Task 仍可单独关联 Project。

正式指派是 assigned_unaccepted，不等于本人已接受。接受仍需唯一既有 Task、本人身份、
精确已链接指派 Signal、同会话与可信 reply_to_source_ref；收到、TODO 存在或服务消息不证明承诺。
但未接受本身不自动构成项目风险：若当前证据显示工作正常推进、没有实质影响或负责人归属争议，应保留 assigned_unaccepted，不单独生成 Attention。
任务继续推进、收到材料或更新项目背景均不等于本人接受；commitment_status 只有来源明确记录接受、争议、完成或取消时才更新。
日期明确区分系统创建、assigned_at、requested/external/committed/estimated_deadline_at、
next_check_at。来源日期需完整可解析当前短语及可信 actor，不能给原文补时分或用报告名作 actor；
AI Minutes 暂无可信 speaker→identity 映射，不把多方日期归给主持人。assigned_at 来自可信指派时间；
committed_deadline_at 需实际接受/承诺；next_check_at 只记录明确检查日期，不创造 cadence。
项目登记 DDL 不转成 Task DDL。一般 Task 的历史 memory/session 引文可完善候选，但不能代替
正式化、接受、合并或类型化日期所需的当前授权/身份；项目/职责/建议/关注证明只用已观察原文。

#### 一个 Agent 的当前输出与应用

TaskAgentDecision 必须明确返回三项无默认列表：
`project_decisions`、`task_decisions`、`project_assessments`，各为 0..N。
项目资料/判断可以有零 Task，空 Task 不等于无业务结果；没有相关项目/线索的空 assessment
仍需非空 update_summary。当前 parser 不接受 TaskDecision 中的旧 project_proposal、
project_link_proposal、attention_proposal。历史运行只保存原生执行引用，原始决定按该次准确范围读取；
原生来源缺失或旧格式无法解析时，过程详情明确不可用，不从数据库副本恢复或重新执行。
已应用的业务投影回执仍由服务保存。 `scripts/inspect_task_attention.py` 以只读 URI 打开数据库，通过当前 Store 的 Task／Run 公共投影读取输入状态、公开身份和投影回执；原生决定读取在查询连接关闭后进行，不依赖已退休的平铺字段或保存正文。
Codex CLI 路径通过 `--output-schema` 使用 `app/schemas/task_agent_decision.schema.json`，
该文件由 `TaskAgentDecision.model_json_schema()` 生成并由测试校验一致；strict schema
要求每个对象显式返回全部属性，省略值用其契约允许的 `null` 表示。服务仍运行本地 Pydantic
语义校验，失败时最多在同一 session 修正一次；结构化输出不取代业务校验。 acceptance_polarity 与 acceptance_target_signal_id 的 transition 不变量由字段级校验报告到 `task_decisions[i].<field>`，使该修正轮能定位应调整的字段；接受动作及引用证据要求保持不变。Task 任务正文不重复附加完整 schema；Codex 使用上述原生通道，Claude／Friday 由现有运行适配器将同一份完整 schema 拼入单条消息。Skill、事实、业务规则和会话绑定保持不变。
新建、候选及 skip 决定携带 `status` 或 `business_relevance` 时，校验明确要求省略这两个初始字段；只有既有 Task 的 `update_task` 配合 `update_fields` 才能修改它们。该诊断不放宽来源、身份或业务校验。
Task 业务指导以当前 `ceo-work-tracking` Skill 为主；本机 CLI 路线的每轮包装只给出该文件的绝对路径和 SHA-256，不复制完整正文。Agent 冷启动先读全文；续会话仅在相同版本全文仍处于当前上下文时复用，否则重新读取。远端 Friday Runtime 继续接收完整 Skill 正文。包装保留 Skill 未完整描述的输出约束：assessment 选择器、顺序依赖建议、状态更新 transition、晋升 suggestion、owner_evidence 结构、Attention 决定成员与相同事实提案去重。动态来源、时间、visible_ranges 和当前 Project/Task 上下文保持完整。不新增读取工具、权限或路线；原先完整注入阶段的固定对照见 docs/task-guidance-dedup-evidence.md，按需读取对照见 docs/task-skill-on-demand-validation.md。


ProjectSelector 使用既有 anchor_id 或零基 project_decision_index 二选一；后者指向顶层
Project 决定，不是 Task 列表。Task 用 project 与 project_link_evidence 明确归属，复用真正已有
confirmed link；新关联依据当前或历史实际原文证明，不按词面相似性确认。
所有 link 引文都保存为 Task 和 Project 的证据关系，canonical link 保留首条证明。
关联本身没有 Task 字段变化时也可应用，使用其实际证明 Signal/anchor 写回执；不虚构 details_changed、
重排 follow-up 或发 TODO。普通仅重述 Task 且没有新关联证明的 update 仍 skipped。
来源明确的报告登记与 Task 显式 project_decision_index/cluster_id 可确认已有项目候选及原分组；
已确认关联的首条证明不覆写，新来源追加为补充证据。新确认或重新有效的成员传给成员-only
recompute，不把同项目但不支持该卡片的 Task 自动加入关注，也不重判风险。
Task 关系用已有 related_task_id 和相对当前实际 Task 的 direction，应用后派生两端；
相似交付只 proposed relation/cluster，不直接身份 merge，原始身份合并证明语义不变。
跨版本报告事项只在被引用的 Task Signal 与持久 owner-evidence source_ref 均能定位到该 Task 已保存的完整版本化 DingTalk 报告正文、引用的所有文档节点相同、每个 SHA-256 均与其 `markdown` 正文相符，且相同负责人事项行在每份引用正文中恰好出现一次时才允许身份合并；owner-evidence 可以引用同一文档的较早版本，但必须有对应 Signal 和完整正文。JSON 包装的报告正文先解包 `markdown` 字段，不能在元数据或序列化转义文本中碰巧命中；缺版本/正文/唯一行/负责人证明或彼此冲突就拒绝。Project 关联和 Task 标题相似度不是身份依据。

每个 assessment 针对一个实际 Project 或身份待明确的线索。one Project/精确标题 一轮一项；
覆盖所有已输出项目决定、Task Project selector 及当前引用 Task 的 confirmed 正式项目。
支持 Task 决定用 decision_indexes，已有 Task 用 task_ids；两者可空，不伪造支持成员。若 Task 决定更新的正是本次评估中的同一具体工作（包括正常进展的 not_needed 评估），应将该决定列为支撑；已有 Task 也只有直接支撑评估时才列入。仅关联到同一 Project 不够，完成或无关 Task 不是成员。
未登记线索保留原名与原文，outcome=insufficient_evidence，不借标题猜 ID。
真实正常进展可 not_needed，没有风险或没有 Task 本身不等于证据不足。
needs_attention 需实际已核验卡片或 assessment 自己的 attention_proposal；负面结果不生成或关闭卡片。
提案没有重复 anchor/task selector，由所属判断解析 actual Project/支持成员；不在每条 Task 复制。

当前 null-ID 引文精确匹配 immutable Work Item ref/原文；历史严格正 Signal ID 精确匹配保存的
ref/逐字引文且为 observed 原始来源，JSON 引文可在一个解码叶子内，不跨字段拼接。
historical_comparison 同时用当前与历史原始证明；当前对旧报告的转述不是旧报告，首次当前事实判断
仍允许。缺原文只说明不确定，不用 session/Memory 充当原始事实。
在同一领域事务写入前验证这些 selector、实际对象和引用，再按 Project 登记/资料→真实 Task
生命周期或 record_suggestion→项目判断顺序应用。无效来源或身份冲突回滚本轮，不猜未来 ID。
支持 Task 必须实际属于所评 Project，或本轮同一支持决定用真实证据确认；不能只因 ID 存在算成员。

同一个既有 stored Project/assessment 只读校验器也在领域应用前、原有两轮 decision
repair 中通过短连接执行；完整来源、原始错误和被拒决定始终作为修订的完整输入。只有实际运行线路续接刚生成候选的同一个 native session 时，才提交仅含错误与被拒决定的短输入；冷启动或续接其他 session 使用完整输入。Task 正文不再重复附加运行适配器已提交的完整 schema，不删除 schema 字段或断言。
包括已有关注卡缺少其保存原证据的声明错误，不放宽原文、身份、成员标准。领域事务内
仍再次执行未改动的同一校验器；SQLite 操作错误和原子应用阶段错误不进入模型纠正。
没有新增校验政策、循环或纠正预算。
当前 null-ID 项目引文的反馈一次列出所有不同的拒绝原因、来源 ref 和被拒引文原样文本；
重复项只报告一次，不自动改写、模糊匹配或归一化引文，整体仍只有既有两轮纠正。

existing_attention_id 是该同 Project 活动旧卡原始证明的声明，不是 upsert 目标。
它须核验真实原始 Signal/ref/quote，并在 assessment 原样引用该卡至少一条保存的证明；
支持 Task 只取卡片实际成员，不把项目同伴当成员。真实旧证明可与当前 attention_proposal 并存。
有当前提案时回执优先报告其实际 applied/rejected/error，已验证旧卡 ID 不把失败伪装 existing；
只复用旧卡时才 existing。新风险按 Project stable key 更新，需关注不等于需介入，watch 可无需 CEO 动作。

#### 回执、检索及尚未完成的关注集成

Project 决定回执保存实际 project_decision_index→project_id/anchor/revision_id/signal_ids；
Task 决定保存实际 decision_index→Task/Signal/anchor。skip、失败接受和无操作决定不伪造映射。
项目-only、建议-only 输入仍可 completed/done；当前线索判断也保存真实来源，不依赖 Task 载体。
逐 assessment 回执区分 recorded/applied/existing/rejected/error，引用只附实际来源 ID/时间/link；
原生决定中的 outcome/reason 不被应用回执改写；服务只保存实际应用的 projection 回执，
不保存第二份原始决定。领域事务先写 pending 回执，卡片消费者及
最终回执在提交后；错误可观察但不把已完成 run 改失败，不增恢复 loop 或读路径自愈。
proposal_count 按 assessment 自己的提案计，applied_count 按成功卡 ID 去重，
project_link_count 为实际确认/复用的不同 Task↔Project 关系数；registry_row_count 只统计原始报告登记行。
最终回执保存失败保留 pending 与日志；未记录 run 的直接 apply 不创建假 run。

Attention domain 要求正式活动 Project 与 business_project_evidence 中已存在的原始 Signal 关系，
不要求至少一个 Task，也不借 Task evidence 作为 Project 证明。可选成员必须是真实、相关、未合并的
Task 且已确认关联同 Project；当前显示成员仍只取 open/waiting。零 Task positive assessment 只有
实际 upsert 并读回卡片后才为 applied，重复来源不产生重复卡或事件。
显式 resolve 要求同 Project 的原始解决证据，不依赖 Task 当前/历史成员；not_needed、Task 完成或
成员清空不自动 resolve。recompute_for_tasks 保留且仅更新既有卡片成员、时间和成员事件，不重新
判断风险、不创建卡片、不修改 category/current_state 或 active/resolved。人类 TODO 完成后直接
调用这个成员更新，不再调用旧 Agent 卡片适配或要求虚假 receipt。
当前配对迁移逐值保留源库已有 Project evidence，包括原身份、SQLite 类型和关系；不再运行历史 Schema .4 从 Attention／Task 关系推导 Project evidence 的一次性函数。该函数已无运行时调用者并随旧物理表退休。新证据仍由显式 Signal 与已核验原文建立，不在初始化或读取时推导、补写或重新激活历史项目。
只读 inspector 输出当前 Project 决定、真实回执映射及由这些实际身份查得的最新 context/revision；
历史缺字段仍缺失，不补为 not_needed，不推测 ID，不改源库或历史 run。
多来源回归已经通过；v2 native 对照、W39 副本迁移/回放和生产迁移尚未全部完成。评测读回独立判断的提案，
要求每项提案有对应真实卡片与实际 applied 回执；缺回执或仅写 completed 不判成功。
当前 negative assessment 也要有对应保存的回执，历史缺字段不自动升级。既有会议表外键问题另行处理，
不通过忽略外键或修改 frozen baseline 隐藏。这里没有新审批、工具白名单或风险关键词逻辑。

检索先读取正式 Project 当前资料/证据，再相关 Tasks；同来源旧身份强制保留，预算先给它们。
正式 Task 的负责人原文核验先处理全部同来源记录，再按排名填满已核验与待核验两个展示桶；两桶都满后不再读取不会展示的后续 Task 来源。同来源超额记录、已选 Task 的证据及来源失败语义不变。
零 Task 项目仍可读 context、当前职责/事实证明与有界近期来源，context revision limit=1，
证据有界并固定保留引用 ID。current_project_attention 保留真实卡成员和原 assessment 原文。
Task 语义上下文用无损紧凑 JSON 序列化，省去排版缩进；字段、顺序、原文字符串、引用偏移和检索选择保持不变。
source_documents 按完整绑定身份键提供精确版本的共享正文一次，source_signals 保留引用 ID、来源和业务元数据并指向正文；数据库内部 dedupe_key 不进入 Agent 输入，持久去重键及其查询行为不变。
同一轮 Task semantic context 中完全相同的 `visible_ranges` 与 `decoded_excerpts` 只序列化一次，其余文档用 `duplicate_of_document_id` 指向首份投影；文档身份、元数据、信号和引用保持独立，展开后与原始 source bundle 完全一致。若压缩后的完整 bundle 不更短，则保留原始表示。
当前 WorkItem 正文也只出现一次，immutable 输入不改。历史长文在 2048 字符预算内保留首尾及关键
引文；原文可用时，visible_ranges/full_length/truncated 和引用超预算明确显示，JSON 解码引用有实际 leaf 路径/偏移。原文不可用时明确展示状态、定位元数据及已保存引文，未知字符数为 null。
source_metrics 是正文字符观测，不是总 prompt token 保证；检索排名不证明身份、分工或业务重大性。

Task Agent 共享 task-agent:work-tracking:v1 逻辑 session，各 route 保留各自原生 session，
native CLI 自行 compact；每个 WorkItem 仍独立 workload/run。process-work-items 领取前持有 SQLite
共享会话锁，运行时续租并在提交前核验；失锁本轮不提交，普通既有重试策略不变。
旧 task:<run_id> session 历史保留。本机 CLI 路线的 prompt 指向当前选定 Skill 根目录中的 ceo-work-tracking 文件及其 SHA-256；远端 Friday Runtime 路线继续收到完整正文，不固定历史 revision；旧 scheduled
skill_protocol 不注入，scheduled prompt 仅保留专项业务范围，旧 payload/history 不改。只读工具行为由 prompt 引导，不是技术上禁用 CLI/MCP 写入的权限边界。

完成只由新信息驱动。独立 completion Agent、周期性任务完成检查已删除，旧三类 completion-check
输入到 Agent 前 skipped，枚举仅保历史；todo_changes/follow_up_changes/search_trace 没有当前应用路径。
人类 DingTalk TODO 完成扫描只关闭对应 Task；外部 TODO 镜像/完成仍走现有 outbox，
不由本 Task Agent 直接操作外部记录。隐藏 maintenance/recovery 业务 loop 不恢复。

Attention 只读详情读取实际保存的 assessment_json 与准确引用 Signals，不重检索或补造历史 {}；
页面现有“来源事实/Agent 判断”、watch“关注点”与 decision/push“你的动作”保留。
项目列表直接读保存的 ProjectContext，展示唯一总负责人、整体负责事项、事实近况和实际活动卡片
的关注原因。删除从关联 Task 周报重新推导项目的读路径；未知仍待明确，不借 Task owner。
详情按整体情况、总负责及分工、关注原因、来源任务、Agent 建议、来源与修订展示；来源任务
包含 Project 已确认关联的来源候选，实际安排以任务阶段和承诺为准，执行计数不是项目经营结论。
当前建议仅指 origin=agent_suggestion 且 stage=candidate；同 ID 人类晋升后进入真实任务，
显示实际 owner/date，原 origin 和 suggestion 仅作为历史依据，不再计入建议。
Task 日期标签只采用与实际日期匹配的 typed date evidence；未知类型不猜请求或承诺截止。
Project 资料更新时间取实际保存的 Project/context revision/evidence 时间，不借 Task 时间或报告周期。
Project 来源首批最近 20 条加当前引用原 Signal，修订最近 20 条，并返回真实 total/has_more；
页面明确未展示全部历史，没有假的加载更多。零 Task 关注保留来源与判断，显示暂无关联任务。
API、TypeScript、mock、页面同步；合成浅/暗/窄屏验证不等于 native 或生产业务验收。

业务 Skill 说明“如何判断”，操作 Skill 说明“如何读取或执行”。OA、面试和 OKR 已有成熟的专业
Skill，CEO Skill 只负责识别需要委派的场景，不复制专业规则：分别加载
`dingtalk-oa-approval`、`xiaoqing_interview`/现有面试 Skill、`dingtang-okr-review`。

日程邀请在进入 Consumer 和 Audit 前，service 会读取邀请的时间窗并把所有已确认占用的
重叠日程作为结构化事实传入。个人 `Blocked`/睡眠占位是硬边界，不能被会议覆盖。两个会议
重叠时，Agent 按目的、紧迫性、必要参与人、负责人和 principal 的必要贡献判断：原会议更重要时
保留原会议、拒绝新邀请并向新邀请人说明理由；新会议更重要时接受新会议、拒绝原会议并向原邀请人
说明理由；无法判断时先通知新邀请人补充重要性理由或联系原邀请人协调，再进行第二次判断。新邀请
的开始时间换算到 principal 本地时区后若晚于 23:00 且存在任一占用冲突，则不比较重要性，直接拒绝
新邀请并向其邀请人说明本地夜间冲突。

### Settings 中的功能机制开关

`Settings -> Skills` 管理的是功能机制，而不是单个文件的可读性。功能清单保存在
`data/config/skill-features.json`，一个功能可以关联多个顶层业务 Skill，一个业务 Skill
也可以被多个功能复用；不存在 Skill 下的子 Skill。启用状态单独保存在
`data/config/skill-state.json`，默认值来自功能清单，状态写入采用原子更新。

开关只作用于对应机制为**新输入创建任务**的入口。关闭后，新的会议、邮件、跟进、任务扫描或
其他有明确生产边界的输入不会入队；已经排队、运行中或可重试的任务仍按原有生命周期继续处理。
共享 Skill 不会从 catalog 删除，其他启用功能仍可使用它。普通消息无法在任务创建前可靠地按
业务领域分类，因此不会用关键词把文档或人员 Skill 拆成独立路由；这些 Skill 仍由通用消息
Consumer 在完整上下文中按需发现。

Skill 的唯一权威来源是 `~/.agents/skills/<name>/SKILL.md`（可用 `CEO_SKILLS_ROOT`
覆盖，仅用于测试）。仓库不再保存第二份副本：两份并存时，改动可能落在没有被加载的那一份，
而且不会报错。服务启动时从该目录导入受管基线并做版本管理；发布到共享 Skill 库是单独的
对外动作，方向是从该目录出去，不是回来。Settings 编辑同样以该目录为准，保存会校验
frontmatter 并以 SHA 防止并发覆盖。用户、系统或插件目录中的外部 operation Skill 不在
该页面的编辑范围内。

因此本仓库的 `scripts/bootstrap-local-components.sh` 不再安装业务 Skill，只校验它们是否
存在；新机器从共享 Skill 库安装。

### Consumer Agent A

所有系统业务审核项必须对应实际系统任务及其当前业务实例；工具或接口存在不构成审核需求。没有对应系统任务就不设置该业务的系统审核项。当前对应关系见 [系统任务与审核范围](audit-task-scope.md)。小青面试与提交由 Derek 在个人对话中处理，不属于本次后台系统审核范围。扫描、下载和登录命令沿用既有技术结果验证，不因这条范围规则新增 Audit 回合。

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。所有 Consumer 入口共用同一条行为指令：不直接向外发送消息或邮件，也不直接执行审批；返回结构化决定或候选，由服务处理外部效果。来源或任务明确要求只要草稿或由用户发送时，Audit 退回或拒绝拟发送消息、邮件的候选。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。该指令不构成工具或凭据的强制权限隔离。

Consumer 可以直接使用普通工作工具，写入并读回本任务本代次的材料，或通过既有绑定的
通用 scheduled 文档接口操作明确目标的文档。普通写入不因为有副作用而被一律改成待审核系统动作；其事实证据是
真实工具结果与读回。`consumer_artifact_write` 将本任务材料写入
`/tmp/ceo-agent-service-task-artifacts/<task_id>/<generation>`，`read_task_artifact` 与
`list_task_artifacts` 为两角色提供当前代的只读材料；Audit 没有对应写入工具。任务仍为
`pending` 或 `processing` 时保留文件供恢复和审核；进入 `done`、`failed`、`needs_human` 或
`skipped` 后，服务清理该任务的全部代次目录。部署前遗留的 workspace 工件会在任务下次读写时迁入
`/tmp`，终态清理也会清除尚未访问的旧位置。Consumer 与 Audit 的运行指令同时明确 memory 是耐久
参考区、临时工件只能使用 task-bound `/tmp` 工作区。系统已注册且需要审核的
动作仍必须完整提案 → Audit → SystemExecutor → 真实回执。Consumer 解析器不扫描整个候选的
自然语言完成短语，也不把摘要转换成执行事实。是否引用历史完成、是否还需要业务动作，由
完整候选、来源及实际回执判断；风险拒绝、授权缺失与历史拒绝不可重放的机制保留。

上述 AgentOrchestrator 候选仍走完整的 Consumer → Audit → SystemExecutor。已授权微信会话的 task3 保留独立的 WechatReplyConsumer / WechatDecisionRunner、持久化 delivery 与 Sender；微信 Consumer 的 `audit_summary` 只是自述。微信待发送记录由服务先补全最终正文，再由独立 model-only Audit Agent 读取原始触发消息、同一会话最近消息及精确目标/正文，输出批准或拒绝。审核结果与当前任务代次、Consumer 父运行、delivery ID 和候选摘要绑定；自动发送、人工确认和重试都在同一个数据库领取入口校验已完成的批准运行及未变的目标/正文。拒绝关闭待发送记录，审核失败或来源不可读时保持不可发送。微信没有迁入通用 candidate/review/SystemExecutor 表，发送回执仍由现有 Sender 持久化。
微信决策和独立审核在 Codex 与 Claude 路线都运行于 model-only 环境：Codex 禁用原生命令、浏览器、插件及全部 MCP server，使用只读 sandbox；Claude 关闭全部工具；不具备角色工具边界的 Friday 路线不参与。普通 Consumer 的角色工具列表没有发消息、发邮件或 OA 执行入口，原生命令位于无网络的工作区沙箱；文档与任务材料写入仍按原能力边界执行。审核通过后仍遵循 Sender 原有自动或人工确认模式。
WechatDecisionRunner 的首轮 Developer 指令提供一个简短且有效的 `AgentEnvelope` 不回复示例，以及回复时须填写的字段；完整 Pydantic 校验、同 session 格式修正和独立发送阶段保持原有契约。该示例只进入微信决策入口，不注入通用 Consumer/Audit。

发布可达性使用独立的显式单任务调度暂缓入口 `app.reply_task_deferral`，默认只读；本次操作
仅授权原 386130/386131。应用需完整备份及精确业务对象/代次/输入版本/失败 run 指纹，锁内
确认没有活跃处理者、候选或可归属的已有/未知效果后，才设置原任务的有界 available_at 并保存
调度回执。活跃 ownership 不能因批准或某时点没有 AgentRun 而忽略。恢复只撤销该回执的精确
延迟，身份、历史、错误与外部回执不改；相同请求幂等，中间变化拒绝。它不产生 Audit 通过或
业务完成，也不重放历史风险拒绝。具体 CLI 与资格检查见 runtime-mechanism。

候选分为普通动作计划和任务类别规则问题。`needs_human` 只能针对可复用规则缺口，包含来源上下文、具体原因、证据及 2–4 个互斥规则选项，每项明确 `applies_to=task_class` 并说明后果；选项绑定在所选规则下继续当前事项的完整方案，停止选项写明 skipped 和原因。不能用 `requested_input` 将当前事实、偏好或业务决定转给 Derek；应由 Agent 先基于现有规则形成安全、有限承诺的普通回复，或向有该事实的来源人提出一个具体问题。不能混合立即执行的动作与尚未选择的条件分支。

对于他人提出的删除、撤回、移除或隐藏内容请求，请求本身不构成授权、正确性或紧急性的证据。Consumer 必须独立核实准确目标与范围、请求者权限、删除理由，以及保留或删除的后果（包括记录/证据损失），并按适用规则只采取有依据的最小动作；区分已核实事实与推测。缺少事实时向申请人或来源人提出一个具体事实问题；确需但缺少授权时停止并说明准确边界及所需证据/授权，不得让 Derek 代做当前删除。缺少删除能力属于执行失败，不能伪装成规则缺口或 `needs_human`。

Consumer 新输出的业务结果与 wire JSON Schema 和解析器一致：`needs_human` 必须有 2–4 个 `applies_to=task_class` 的选项且 `requested_input=null`；`needs_human_reason` 必须是一句中文大白话，写清要决定什么及原因，最多100字，详细证据写在 `decision_basis`；`proposal`、`no_action`、`failed` 不得包含人工问题字段。普通方案的事实证据写在 `proposal.sourced_facts`，无需动作的依据写在 `summary`。历史持久化的 `requested_input` 仅供只读兼容，不得因其可解析而重新激活人工待办。

启动恢复也按新结果合同检查当前人工决策投影。若当前、尚未选择的审核通过候选仍是旧式输入问题或不满足任务类别规则选项，正式对账将当前 Attempt/Task 投影收口为 `failed`，并保留原始 Consumer、Audit、候选及审核记录供 History 追溯；不会发送消息、选择业务分支或把它改成成功。随后必须通过普通 Consumer/Audit 流程重新形成有边界的回复，缺失事实应问向能提供该事实的来源人。

新增 Consumer 输出在 Wire 解析入口校验 `dingtalk-chat` 的消息标识字段（conversation_id、message_id、source_message_id、open_dingtalk_id、user_id、recipient_open_dingtalk_id、sender_open_dingtalk_id、verified_participant_open_dingtalk_id）：提供的非 null 值必须是含非空白字符的字符串；不把数字转换成字符串。普通 proposal 和人工选项中的可执行 plan 均适用，输出 JSON Schema 同步声明该约束。历史保存结果的 Pydantic 域模型读取保持原样；这不表示旧数字标识仍符合新的输出 Schema，也不改变历史执行或恢复行为。其他 capability 的 target 继续支持通用 JSON 值。

Audit wire 只返回审阅结论和反馈，不要求模型复述 `candidate_digest` 或 `proposal_revision`。解析器从本轮已核验的 `AuditTurnContext` 注入这两个绑定值，再用同一个上下文核对候选字段断言；严格 wire schema 拒绝模型额外输出绑定字段。恢复已完成的 Audit 原生结果也使用同一候选绑定解析器。持久化的 Audit 域结果仍带摘要和版本号，并保留原有完整内容与敏感值校验。

Audit 返回 approve、return、reject，必须绑定 candidate_digest 和 proposal_revision；failed 只表示技术失败。Audit 不修改正文、选项，不返回执行回执，也不自行创作另一个人工问题。return 允许保留正文并补足证据；reject 要求实质改变被驳回内容，不能只改描述、元数据。首次提交最多三次内容重提，耗尽为 failed，技术失败不占内容预算。

Audit 当前任务除完整候选外，还收到服务从同一候选 JSON 确定性生成的标量字段证据：JSON Pointer 路径、精确字面值和 JSON 类型。字段类型反馈必须和该证据及候选相符；此索引不增加来源事实、改写候选或改变审核决策规则。

规则问题也先经过 Audit。Audit 检查：适用规则是否确有缺口、规则/代码/Skill/记忆/会话/读取是否可自行解决、当前事实是否应由来源人补充、是否把技术失败或当前实例决定伪装成规则选择、上下文和理由是否充分、2–4 个规则选项是否互斥可行且确有差异、每个执行分支是否完整并有依据。仅当前版本 approve 后才进入 `needs_human` 并通知；审核通过不等于业务 done。

选择接口提交 candidate_id、review_id 和 option_key，服务从该不可变候选中读取唯一匹配的选项；选择记录只保留引用和选项键，不复制分支正文。首次选择独立记录并将原 execution_generation 唤醒；既有 generation、review、失效及最新候选校验通过后，相同选择幂等，冲突选择拒绝。较新候选不会改变历史选择绑定，但旧候选的再次选择仍按原校验拒绝。有效分支直接由系统执行，不重新启动 Consumer/Audit。补充文字保留为新输入，使旧候选失效并进入新完整候选和审核；历史选择、问题和回执保留。选择事件不等于执行完成。反馈可以同时标记一次性应用与 Skill 更新；只有明确获选的 Skill 更新才进入受控 Skill 发布流程。

旧长期规则问题仅通过显式单项命令 `python -m app.rule_question_retirement --attempt-id <id> --authority <confirmed-contract>` 退役，默认只读预览；应用另加 `--apply --verified-backup <path>`。资格限定为当前业务对象、当前执行代、最新 Attempt 所绑定的已完成 Consumer、done 任务、完整无技术错误的 task_class 问题，且该任务没有新候选；不批量排除旧结果。应用只原子写入 resolved_at、resolution 与包含原结果 SHA256/任务/运行/执行代/契约依据的退役回执，保留原 send_status、模型结果、任务状态和外部回执。详情、History 和队列将该已退役问题显示为 skipped 并解释依据，重试 POST 拒绝。它不是人工选择、Audit 通过或业务完成；未退役和当前无效问题继续由原质量检查报错。预览及应用均不初始化或迁移数据库。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，`stage_index`、`predecessor_review_id` 绑定前一已完成阶段的回执；阶段上限独立于每阶段三次内容修订预算。Consumer wire 仍包含这两个字段，但服务在解析时以编排器提供的阶段上下文覆盖模型回传值，再持久化和绑定候选。Audit 返回后的修订因此保持当前阶段及原前置审核；仅前一阶段 Audit 批准且该阶段动作已核验完成后，编排器才提供下一阶段绑定。候选存储仍校验服务绑定的完整代际、阶段、审批和执行回执关系。

Audit 返回或拒绝后，Consumer 必须提交当前阶段的完整修订候选。服务以当前编排上下文绑定 `stage_index` 与 `predecessor_review_id`，不把模型复述这两个 lineage 字段的准确性作为候选业务结果的前置条件。返回反馈不会推进阶段；仅当前阶段审核通过且其动作有已验证的完成回执，后续阶段才会开放。候选的阶段、前置审核、代际及执行验证仍由服务严格校验。

### 任务长期记忆

Derek 2026-09-24：长期记忆由执行 Agent A 在结果里给出、系统写入 Memory、不经审核。

- A 的结果协议（`ConsumerAgentWireResult`，四种 outcome 都一样）必填 `durable_memories`，可为空列表。
  每条是 `title`、`content`、`source_time`（信息在来源里产生的时间，ISO-8601）、`source_refs`
  （消息 id、文档链接、审批单号）和可选 `subject`（Person/Project/Customer/Organization + 名称）。
  写什么、不写什么只由字段说明约束（`app/agent_contracts.py` 的 `DurableMemory`）。
  旧结果读回时视为空列表。
- 任务在 `finalize_orchestrated_reply_task` 里进入 `done` 的同一事务中，服务从库里存的、该执行代最后一次
  完成的 Consumer 结果读出这个字段（不用内存里的编排结果对象，它多数情况下不带 Consumer 结果），写进
  Actions 中的 `task_memory_write_events` 类型：每个任务执行代一行（键为 `reply_task_id +
  execution_generation`），`pending` 带待写条目；没有条目记 `skipped / no_durable_memories`，
  没有 Consumer 结果记 `skipped / no_consumer_result`。所以每个经编排结束的任务都有一个记忆结论。
- 写入是系统自动行为，不是定时任务（Derek 2026-09-24）：统一 Dispatcher 的 `task_memory_write`
  adapter（`TaskMemoryWriteQueueAdapter`）像 DingTalk Todo outbox 一样，一入队就领取，
  `app/task_memory_write.py` 用服务自己的 memory-connector 客户端逐条调用 `memory_write`（与会议结论同一客户端）。
  dry-run 不注册该 adapter。
  正文与时间来自 Agent，写入前去掉服务附加的反馈回调链接和署名（`app/memory_text.py`，会议结论同样处理，并去掉跟进消息抬头；标识符只放 metadata，不进正文：固定内容会让 memory-connector 聚类把无关 episode 判成近似重复，Derek 2026-10-02）；`thread_id`（会话标识）、`source_metadata`（渠道、会话、触发消息、任务 id，
  外加 Agent 指认的 `source_refs`）和 `provenance_metadata`（执行者、Consumer run、执行代、线路、
  模型、用途 `task_durable_memory`）由服务从记录里填。每写成一条就记下它的 id，重试只写剩下的；
  连接器失败按退避重试（上限 20 次），之后记 `failed` 并进 Attention（`task_memory_write_failed`）。
- 这取代了让 Agent 自己调用 memory 工具：那条路在 2026-07-26 之后衰减到一条不写；也取代了
  memory-connector 插件的收尾钩子（服务的 `codex exec` 已固定 `--disable hooks`）。

### Audit Agent B

Codex Consumer/Audit 显式启用原生 `features.code_mode.enabled`。普通 Consumer 与非 OA Audit 的任务工具仍为 direct-only；OA Consumer 与 OA Audit 的只读附件工具和 OCR 工具在 CodeMode 中组合，避免模型接收再复制可能被截断的 base64。OA 的一次性会话合同变化会建立新 session，此后继续复用同一对话对象的兼容 session；其他任务的会话合同不变。OA Audit 的 CodeMode 排除 functions、image_gen、multi_agent_v1、web 内建命名空间；其他 Audit 维持原 functions 排除。角色工具列表、只读沙箱、路线和外部动作规则不变。工具配置不能替代实际调用验证。

Audit 使用实际只读工具接口审核候选，没有受控发送、审批或命令执行能力。Consumer 保留任务绑定的文档和报告操作；Codex Consumer 使用原生 shell、unified execution、补丁和 V8 host 在当前 task/generation 的 `/tmp/ceo-agent-service-task-artifacts/<task_id>/<generation>` 目录执行普通本地代码。该 task-bound 目录与 durable workspace 分离，原生 workspace-write 沙箱只为当前任务打开它及原生临时目录，命令网络关闭；MCP 进程仍从服务源码目录启动。任务终态清理所有代次的本地工件，pending/processing 期间保留以支持恢复和 Audit 读回。受控发送、OA 等注册操作仍只由 System 执行。所有任务类型的 Consumer 都不得通过 shell、CLI、MCP、browser 或其他工具直接联系他人或执行影响他人的外部动作（包括消息、邮件和审批）；文档写入保持允许。Consumer 只能提交 typed candidate，由 Audit 审核，System 只执行审核通过的持久化方案。Audit 排除内建 functions namespace 并保留 read-only 沙箱。两种角色均不开放 browser、image generation、委派、自动 Skill 安装、记忆写入或未登记 MCP 操作。Claude 仍只有受限内建读取与角色 MCP 工件工具，没有 shell 代码执行；Friday 不能承担这两个角色。原生执行验证必须保留实际命令结果和文件回读，写出代码文件不等于执行代码。

## 会话与反馈周期

Consumer 继续兼容业务会话，Audit 按现有角色会话继续读取和审核。每阶段初始提交加最多三次内容重提；return/reject 消耗预算，运行时和 provider 重试不消耗。原始 run、修订和错误 append-only，任务 processing 在反馈期间保持不变。

### 任务提取与 follow-up 是一个生命周期

`ceo-work-tracking` 把同一事项从识别到关闭作为一个流程：从对话、会议或材料中提取有证据的
Task，按需链接正式 Project，记录负责人及类型化日期。只有符合承诺与期限条件的 Task 才通过
outbox 镜像为钉钉 TODO；只有来源明确给出下次检查时间与目标会话才创建 follow-up。follow-up
只是建议：由 Derek 在 Task 详情页点按钮发送（Derek 2026-09-25），新信息更新该 Task 时撤回未发出的
follow-up，见 `docs/runtime-mechanism.md`。没有自动发送：到期的 follow-up 不会被发出，结果未知的
发送显示为失败、由 Derek 决定是否再点。发送记录 revision、租约、幂等 ID 与 provider 回执。读取后续
回复或新完成的外部 TODO 后，仅凭明确完成证据关闭对应 Task 与其 follow-up。旧 Project/TODO/follow-up 记录保留在历史边界；没有可核验 current Task 绑定的旧外部 Todo 不会因镜像链路退休而被晋升为 Task。

## Audit Rules

Audit Rules 是 A 和 B 共享的可见业务规则：

- 默认文件：`data/prompts/audit_rules.md`
- 配置页面：`Config -> Audit Rules`
- A 使用规则自检候选。
- B 使用同一规则独立审计并决定是否执行。

Message Triage 的 Audit 按触发消息判断 Derek 角色是否有待办。对方已明确接手的后续事项即使尚未完成，也不单独构成要求 Derek 追问的理由；但 Audit 仍须退回直接要求 Derek 决策或行动的候选，并且不得把对方承诺写成已完成或已核实。

可配置内容包括表达、信息最小化、审批材料要求、特定业务风险和需要升级给 Derek 的判断。
以下边界不可配置：A 负责读取/判断并提出候选、B 负责 accepted action 的正式执行、精确 revision 去重、最多三个内容反馈周期、
外部动作标识去重以及敏感凭证不进入提示词和审计页面。

## 能力与配置

### MCP 服务器可见范围

OA Consumer/Audit 收到扫描页时，在当前 Native Codex session 的同一 CodeMode 调用里传递读取工具返回的字节、MIME、页码和摘要给 `ai_services.ocr_document`，仅展示 OCR 文本与来源信息；非 OA Audit 和其他角色保持原直接工具路径。嵌套目录、shell 清单或其他 Agent 的工具列表都不能单独证明本轮 OCR 可用；以本轮实际调用结果判断依赖故障。

Codex 会把 `$CODEX_HOME/config.toml` 中的全部 MCP 服务器合并进每次运行，后台 Agent turn 因此
默认能看到安装用户的个人工具。服务清单（`CEO_SERVICE_MCP_CONFIG_PATH`）除了声明服务自己的
transport，还用 `disabled_servers` 列出后台 Agent 不得使用的个人服务器；命令组装时对这些名字发出
整表 `enabled = false` 覆盖（Codex 不接受单字段覆盖，占位 transport 不会被启动）。Settings → MCP
读取 `codex mcp list --json` 展示全局服务器与清单服务器，保存即写回清单，从下一个 Agent turn 生效。

后台 Agent 使用安装用户的原生 CLI home 和已安装 skills，MCP 直接连接；不复制 OAuth header、token，也不添加本地 credential proxy。Codex 每轮按角色发出显式工具 allowlist：Consumer 的 agent_cli 普通材料读写与原生工作区代码执行可用；Audit 仅保留只读审核能力。两角色可用具名只读工具 `read_dingtalk_drive_file` 下载单个已授权钉盘节点并提取支持的图片、PDF、文本或表格内容；`read_dingtalk_oa_process_attachment` 按当前审批实例和表单附件 ID 经 DWS 的 OA 附件下载地址读取正文，不要求通用钉盘节点权限；`read_dingtalk_oa_comment_attachment` 按流程实例 ID、评论附件 ID 和文件名，通过 OA Premium 评论附件接口读取精确的评论附件；`read_dingtalk_oa_comment_image` 则读取 OA 记录 `images[]` 返回的钉钉图片 URL，供没有 file ID 的评论截图进入相同 OCR 流程。图片 URL 读取仅允许 HTTPS `static.dingtalk.com/media/`，不跟随重定向、限制响应体大小和读取时长；所有下载文件仅保留在该 Agent CLI 进程的 `oa-review-*` 临时目录中。扫描图片和无文本层的 PDF 返回精确来源的 base64、MIME、页码及 SHA-256，Consumer/Audit 使用原生 MCP `ai_services.ocr_document` 识别并保留页码、置信度和警告；不使用本地 OCR 脚本。超过 700 KiB OCR 输入上限的支持型栅格图片先转为有界 JPEG 输入，读取结果仍保留原始图片 SHA-256 和源尺寸；未超限图片保持原始字节。后台 Consumer/Audit 命令只对 `ai_services` 的 `health`、`ocr_document`、`transcribe_audio`、`synthesize_speech` 设置逐工具自动授权，以便角色在既有工具范围内直接调用已配置服务；全局审批策略不变，其他 MCP 工具不增加自动授权。`ai_services` 通过后台服务 MCP 清单按名称和 URL 连接，OAuth 沿用 Codex 原生配置，不增加 token 代理。下载权限/权益失败、输入超过工具响应上限、MCP 失败或 OCR 为空时返回技术阻塞，不能解释为申请人未提供材料。进程退出时清理临时文件。Claude 的本轮 MCP 配置只连接 agent_cli，沿用其原生 home 和 inline 配置。

OA 表单附件的临时文件路径由下载材料层直接返回；表格抽取结果本身可以没有 `path`，角色读取器仍以该真实临时文件缓存来源并返回表格内容、原审批实例/文件 ID 和原字节 SHA-256。此路径修复不改变 OA 下载权限、钉盘拒绝结果或审核动作。

Codex 后台角色固定 `features.plugins=false`；`codex exec` 固定带 `--disable hooks`（Derek 2026-09-24）。插件 Stop hook 曾使结构化结果重复；长期记忆由 Consumer 在 `durable_memories` 提出、系统写入，见「任务长期记忆」。Consumer 生成候选并按共享 Audit Rules 自检；Audit 只读独立审核；System Executor 执行已持久化审核通过的受控动作。普通工具工作不因产生材料而自动注册为系统受控动作。个人对话中的小青面试或上传没有对应后台系统任务，不设置服务审核项。

Agent 不执行 `auth login`、`reset` 或 `logout`。MCP 实际返回未授权时，任务保留认证失败，不把它解释为材料缺失。

Agent 不得把嵌套 shell 里的 `codex mcp list` 或 `codex exec` 结果当成当前父 Agent session 的
MCP 注入证明。需要 Xiaoqing、Memory、Exa、Lark 等 MCP 时，Consumer/Audit 必须直接调用当前
session 中的对应 MCP 工具；只有直接工具调用或 provider 操作失败，才形成可持久化依赖失败。
`<server>_mcp_not_injected` 这种自报注入状态是 wire result 契约错误，进入修正轮而不是任务终态。

### Agent Runtime 路由模型

`CEO_AGENT_RUNTIME_ROUTES` 是一个有序的路由名列表，**顺序就是故障切换顺序**：前一条不可用时
Router 取下一条已配置且健康的路由。列表里出现的名字分两类：

- **内置路由**：只有 `codex_oauth`、`claude_oauth`、`friday_runtime` 三条（常量
  `SUPPORTED_RUNTIME_ROUTES`，Derek 2026-09-24），名字固定、不能改名，使用各自固定的环境变量
  （`CEO_CODEX_MODEL*`、`CEO_CLAUDE_MODEL*`、`CEO_FRIDAY_RUNTIME_*`）。
- **添加的路由**：列表里任何其它名字（小写字母、数字、下划线，字母开头）都是运维自己加的路由，
  由它自己名下的 `CEO_RUNTIME_<大写名>_KIND` / `_BASE_URL` / `_MODEL` / `_API_KEY` 描述，
  模型自由填写。`KIND` 取 `codex_oauth`、`codex_api`、`claude_oauth`、`claude_api` 之一
  （常量 `ADDED_ROUTE_KINDS`）；OAuth 两种复用本机 CLI 登录，只需要模型、不需要 Token
  （`ADDED_ROUTE_KINDS_WITH_KEY` 之外）。因此同一种 provider 可以配置多条，各自指向不同地址
  和模型——`RuntimeRoute.base_url` 就是为此存在，Codex adapter 用的是**该路由自己的**地址，
  而不是某个全局配置。`codex_api`、`claude_api` 以前是内置路由（`CEO_CODEX_API_*`、
  `CEO_CLAUDE_API_*`），现在只是两条添加线路的名字，没有任何代码按名字认它们。
  添加的 Codex 路由还可选 `CEO_RUNTIME_<大写名>_REASONING_EFFORT`，支持 `low`、`medium`、
  `high`、`xhigh`；空值继续使用全局 Codex thinking 强度。Settings 同一字段保存、展示、改名和删除。
  具体提供商可能只接受其中部分取值，配置时应以该路由的实测能力为准。
- **按种类、不按名字**：依赖认证方式的规则看 `RuntimeRoute.is_cli_api_route`（凭自己 API Key
  登录的 Codex/Claude CLI 路由）。「临时失败后在同一路由换新会话重试一次」适用于所有这类路由；
  Consumer 连续两次结果不可用时强制换新会话，对所有路由一视同仁，没有例外（Derek 2026-09-25；
  此前凭 Key 的 Codex CLI 路由沿用 `codex_api` 的例外，已去掉）。安装向导按配置顺序列出全部路由，`probe-agent-runtimes` 在配置无效时对
  任何缺 Key 的 API 种类路由报 `missing_secret`。

**一次性迁移**：服务 supervisor 在启动 worker、web、email 三个子进程之前，先用独立进程运行
`python -m app.agent_runtime_migration`。`.env` 里仍有 `CEO_CODEX_API_*` / `CEO_CLAUDE_API_*`
时，把在 `CEO_AGENT_RUNTIME_ROUTES` 里的 `codex_api` / `claude_api` 原样写成
`CEO_RUNTIME_CODEX_API_*`（KIND=codex_api，BASE_URL 缺省为 `https://api.openai.com/v1`，
MODEL 缺省取 `CEO_CODEX_MODEL`）和 `CEO_RUNTIME_CLAUDE_API_*`（KIND=claude_api，MODEL 缺省取
`CEO_CLAUDE_MODEL`），**名字不变**，定时任务、会话续接、暂停这些引用都不用动；不在列表里的
那条不迁移（添加的线路只在列表里时才存在），`CEO_AGENT_RUNTIME_HIDDEN_ROUTES` 里的这两个名字
去掉（隐藏列表只记内置卡片）。写之前把 `.env` 备份为 `.env.runtime-routes-migration.bak`
（固定文件名，只留最新一份，写前核对字节一致），然后删掉旧键。旧键不在时什么也不做，所以重复
运行无副作用。迁移放在独立进程是因为 supervisor 进程一旦 import `app.config` 就会把 `.env`
抄进自己的环境，之后重启的子进程会把这份旧值当成权威。

控制台的 Settings / Agent Runtime 直接编辑这份列表：拖动卡片改顺序，开关决定该路由是否在列表
里。内置卡片删除时把名字记进 `CEO_AGENT_RUNTIME_HIDDEN_ROUTES` 并只清除该路由**独有**的凭据
（共用的设置保留），可以从「新增 runtime」恢复；添加的线路关掉或删除都会把它移出列表，保存时
清掉它名下的 `CEO_RUNTIME_<名字>_*`。

**改名**只对添加的线路开放，由服务端完成（`POST /api/console/settings/agent-runtime/routes/{名字}/rename`，
`{"new_name": ...}`；页面上改卡片名字即调用它）。新名字须合规、不能是三个内置名、不能与已配置的线路重名；
内置线路不能改名。一次改名先在**一个数据库事务**里把按名字引用这条线路的地方全部改过去
（`AutoReplyStore.rename_runtime_route`）：定时任务首选线路 `scheduled_tasks.runtime_id`（版本号 +1）、
还可能被派发或重建的定时运行快照（`scheduled_task_runs` 中 `pending`/`dispatched` 通过 `snapshot_id` 引用的配置版本的 `snapshot_json.runtime_id`；
`skipped`/`failed` 已是终态不改）、会话续接 `conversation_runtime_sessions`、线路暂停 `runtime_route_pauses`、
能力快照 `service_state` 的 `agent-runtime-capability:<名字>`（连同快照里的 `route_name`）；新名字下已有的
残留行属于一条已不存在的线路，被覆盖。历史 `agent_runtime_attempts.route_name` 不改，它记的是当时用的名字。
数据库提交之后再改 `.env`：线路顺序里换名、`CEO_RUNTIME_<旧名>_*` 搬到 `CEO_RUNTIME_<新名>_*` 并删除旧键
（隐藏列表只记内置名，不涉及）。先数据库后 `.env` 的理由：数据库这一步提交后再跑一次什么也不动，
`.env` 写失败时原样重试同一次改名即可补完；反过来先写 `.env`，数据库失败时 `.env` 已经没有旧名，
重试会被「旧名不在已配置线路里」拒绝，引用就永久分裂了。运行中的服务不重读配置，新名字在下次
重启后生效（与其他设置保存一样）；改名到重启之间，到点的定时任务会因为「首选线路未配置」被跳过并进
Attention，所以改名后应尽快部署（`python -m app.deploy`）。
保存只有一个入口：React 设置页提交到 `POST /api/console/settings/agent-runtime`，字段用 `.env`
键名，由 `app/web_api/agent_runtime_settings.py` 校验并写入。读取同一接口时，凭据类字段（`*_API_KEY`、Friday 的 ticket / session token）只返回部分遮蔽值（12 位及以上保留前 3 后 4，其余 `****`；更短的整体 `****`），服务从不把完整凭据发给控制台（Derek 2026-09-25）；页面把遮蔽值原样存回，服务认出它等于已存凭据的遮蔽值，就保持已存凭据不变，输入新值才替换。旧的服务端渲染页
（`/config?tab=agent-runtime` 与表单 `POST /config/agent-runtime`）已删除——React 设置页
上线（2026-08-29）后浏览器访问页面时拿到的一直是 SPA，那套页面只剩测试在用。

健康探测（`app/agent_runtime_probe.py`）与真实 turn 走同一条 provider 路径，因此超时按一条真实
turn 的长度给：`PROBE_TOTAL_TIMEOUT_SECONDS` 和 `PROBE_IDLE_TIMEOUT_SECONDS` 都是 300 秒，
服务读取这两个常量作为 `CEO_RUNTIME_PROBE_TIMEOUT_SECONDS` / `CEO_RUNTIME_PROBE_IDLE_TIMEOUT_SECONDS`
的默认值——两处不同的默认值曾把一条健康的路由判成不可达。`probe-agent-runtimes --route` 接受
任何已配置的路由名（含新增路由），并且**不采纳其它进程的快照**：显式探测必须给出本进程自己的
结果，否则运维看到的可能是服务几十秒前的结论。

路由执行错误会将已分类的失败类别、来源错误码和 `retryable_external_dependency` 同时写入 Runtime Attempt 与 AgentRun。能力错误按路由判定终止，容量和传输错误按路由判定重试；未分类的旧式错误保留 AgentRun 原有默认重试判断。

### Claude Runtime 路由

Claude CLI 有两种路由：内置的 `claude_oauth` 用 `CEO_CLAUDE_MODEL`（默认 `sonnet`），
Claude API 种类的添加线路（例如名为 `claude_api` 的那条）用它自己的 `CEO_RUNTIME_<名字>_MODEL`。
两种共用 `CEO_CLAUDE_MODEL_REASONING_EFFORT`（默认 `medium`，取值 `low`/`medium`/`high`/`xhigh`，
作为 `--effort` 传给 CLI）。调度任务上的 thinking 选项会按次覆盖该默认值，与 Codex 的
`reasoning_effort` 使用同一套取值。下文的 `claude_api` 指任何 Claude API 种类的线路。

| 路由 | 凭据 | 命令差异 |
| --- | --- | --- |
| `claude_oauth` | 本机 `claude` CLI 的登录态（订阅） | 不使用 `--bare`，不设置 `ANTHROPIC_API_KEY`，`CLAUDE_CONFIG_DIR` 不覆盖 |
| `claude_api` | `CEO_RUNTIME_<名字>_API_KEY` | 使用 `--bare`，凭据只进子进程环境，`CLAUDE_CONFIG_DIR` 同样不覆盖 |

两条路由都不覆盖 `CLAUDE_CONFIG_DIR`，直接复用调用方真实的 `~/.claude`：`--bare` 已经
保证 `claude_api` 的认证只能来自 `ANTHROPIC_API_KEY`（不会读到 `~/.claude` 里缓存的 OAuth
凭据），所以没有必要为凭据隔离单独换一个配置目录。每次调用的 settings 和 MCP 配置由
`ClaudeRuntimeAdapter._invocation_boundary` 构造成两段 JSON 字符串，作为 `--settings` /
`--mcp-config` 的参数值直接传给 CLI；不写任何文件到磁盘，调用结束后也就没有文件要清理
（`app/claude_runtime_adapter.py`，AGENTS.md「Keep runtime plumbing simple」：inline 传参，
不做临时文件这类防御层）。

`--bare` 规定 Anthropic 认证只能来自 `ANTHROPIC_API_KEY`，因此订阅路由必须去掉它，改由
CLI 自己解析本机登录态；`CLAUDE_CODE_SIMPLE=1` 与 `--bare` 等价，同样不可用于该路由。
`--safe-mode` 虽然能屏蔽个人配置，但会连同 `--mcp-config` 显式传入的服务 MCP 一起停用，
所以两条路由都不使用它。两条路由的隔离都由 `--setting-sources ""`、`--settings` 和
`--strict-mcp-config --mcp-config` 保证：调用方的 CLAUDE.md、skills、plugins 和 hooks 都
不会进入服务运行。两条路由都会把会话文件写入调用方的 `~/.claude/projects/<cwd>`，与本人
交互式会话共享订阅额度（`claude_api` 用的是独立的 API Key 配额，只是会话记录文件位置共用）。

Claude 事件语法只把 turn item 映射成 runtime 事件。传输层遥测不携带 turn item，
统一映射为空事件：订阅额度窗口 `rate_limit_event`（可能出现在 session init 之前）和
`--effort` 触发的 extended thinking 预算通知 `system/thinking_tokens`。终局 `result`
仍然决定这一次调用的成败；除这两类以外的事件形状仍然是语法违规，不做静默丢弃。

未登录时 `claude_oauth` 的健康探测失败，Router 直接跳过该路由，不影响其余路由。

`claude_oauth` 已登录却仍失败时，先看 403 原文：`OAuth token does not meet scope requirement`
表示 keychain 里那份 token 没有 `user:inference`。在真实终端跑 `claude auth login` 会重新签发带推理
scope 的 token，但 keychain 里这份会被本机其他进程改写，线路随之再次失败（2026-09-17 观察到
03:49:39Z 改写、04:00Z 再次 403。改写者是本机 quota guard：它那一轮在 03:49:36Z 向 Hub 上报，与
keychain 修改时间只差 3 秒。guard 可能把一份不含 `user:inference` 的凭据整份写回 keychain，该机制
由 quota-report-hub 跟进，尚未用实际 scopes 证实；Hub 用 profile/usage 接口检查凭据，不需要推理
权限，所以 Hub 侧会一直显示正常）。因此本机登录态只适合临时使用；需要稳定可用的 Claude 线路时
使用 `claude_api`。
复现服务所见的情况时必须用服务的最小环境运行 CLI（`env -i HOME=… PATH=… USER=… claude -p …`）：
在 Claude Code 会话里直接运行 `claude -p` 会继承桌面应用自己的凭据而成功，不能证明服务可用。

所有 workload（Agent turn、任务 Agent、会议、邮件分类、workbench、TODO 截止日期回填）都能路由到
Claude。Claude 的 developer instructions 与完整输出 schema 仍由
`app.claude_runtime_adapter.claude_input_contract` 与任务拼成同一条消息；
Claude Consumer 另通过 CLI `--json-schema` 接收内联的兼容 wire schema，原有
Pydantic 模型继续校验完整条件与业务规则。Audit 和其余 Claude workload 仍按既有
单消息 schema 路径运行，不改变工具、路由或会话；
事件流由该 adapter 的 normalizer 校验，最终消息再整理成 Codex 同形的 runtime 事件交给 workload
的解析器，解析器不需要知道是哪条 runtime 执行的。

### Friday Runtime 路由

`friday_runtime` 是与 `codex_oauth`、`claude_oauth` 并列的内置 Agent Runtime 路由。它通过 Friday Runtime 的 HTTP 接口创建一个 Thread、提交一个 turn、等待
operation 完成，再读取该 Thread 的最终 Artifact；CEO Agent 不直接调用 provider 的 API。

**Friday 由它自己的 CLI 运行，本服务不安装也不启动 Friday。** CLI 随 Friday 桌面版一起安装，
路径取自 Friday 的安装记录（`~/.friday/install.json` 的 `cli_executable_path`，通常是
`/Applications/Friday.app/Contents/MacOS/friday-cli`）。没有检测到该 CLI 时，控制台把这条路由
置灰，保存也会被拒绝——没有 Friday 桌面版就没有可调用的 Friday。

一次调用按以下顺序解析，全部不需要人工配置：

1. **地址**：`friday runtime start` 启动或复用共享的无界面 runtime（不需要打开桌面版应用），
   Friday 把地址写进 `~/.friday/runtime/default.json`，服务每次读取。该端口是动态的，每次启动
   都不同，因此地址不能写死或缓存。
2. **凭据**：服务用 `~/.friday/runtime/runtime-auth.json` 里的共享密钥签一个短期
   RuntimeTicket（HS256，claims 为 `iss`/`aud`/`sub`/`iat`/`exp`），与 Friday 自己的 CLI 信任
   方式相同。控制台不再提供 ticket 或 session token 输入，服务也不存储任何 Friday 凭据。
3. **项目**：Friday 只接受它自己签发的 project id（未知 id 直接返回 `project not found`）。
   `CEO_FRIDAY_RUNTIME_PROJECT_ID` 为空时，保存会调用 Friday 的 `POST /v1/projects` 申请一个
   并存下来。
4. **模型**：由 Friday 自己的配置（`~/.friday/config/runtime.yaml`）决定。CLI 启动的 runtime
   不读本服务注入的 `FRIDAY_LLM_*` 环境变量，所以控制台不提供 Friday 的 provider 配置入口。

保存这条路由时会做真实验证：CLI 必须能提供一个接受本机签发 ticket 的 runtime，否则保存被拒绝
并给出原因，不会存下一份看起来完整却不可用的配置。

**同一台机器只能有一个 Friday runtime 在跑。** `~/.friday/runtime/friday_runtime.db` 被所有
Friday runtime 共用，谁先 claim 到 operation 就由谁执行，并使用它自己的 provider 配置。历史上
本服务用 launchd 任务 `com.friday-runtime.main` 另起过一个 Friday，结果与桌面版 sidecar 互相
抢任务，错误里出现的是另一个进程的 provider 地址，排查方向完全被带偏。该 launchd 任务已停用，
不要在 CLI 管理的 runtime 之外再起第二个。

路由顺序由 `CEO_AGENT_RUNTIME_ROUTES` 的书写顺序决定，例如：

```text
codex_oauth,codex_api,claude_oauth,friday_runtime
```

一次 fallback 始终属于同一个 Agent run：当前路由失败后，Router 选择下一条已配置且健康的
路由，保留原任务、generation、proposal/revision 和 A/B 生命周期，不创建第二个 Consumer
或 Audit run。

Friday 的 turn API 只接收一条用户消息且没有 schema 字段，因此与 Claude 一样由服务把 Codex
带外获得的 developer instructions 与输出 schema 拼进这条消息；Friday 只返回最终消息，服务同样
把它整理成 runtime 事件后再交给 workload 解析器，否则所有 typed result 都会被判为缺失。Friday 的 Thread、turn、operation 和 Artifact 标识只作为该次 runtime 调用
的结果事实保存，供失败重试和 History 关联。

Friday 路由使用以下明确错误码：

| 错误码 | 含义 | 是否可重试 |
| --- | --- | --- |
| `friday_runtime_unreachable` | Friday Runtime 网络不可达、CLI 起不来或 operation 超时 | 是 |
| `friday_runtime_auth_failed` | 本机 Friday 认证记录缺失/不完整，或签出的 ticket 被拒绝 | 否，需修复本机 Friday 安装 |
| `friday_runtime_result_invalid` | Friday 返回不是约定 JSON、缺少 operation/Artifact，或结果为空 | 否，需修复契约/实现 |
| `friday_runtime_failed` | Friday operation 或其 provider 最终失败 | 是，按任务重试策略处理 |
| `friday_runtime_unavailable` | 未安装 Friday 桌面版，或已选择 Friday 路由但 adapter 未注入 | 否，需修复本机安装或服务配置 |
| `friday_runtime_project_create_failed` | 自动申请 project 失败 | 是 |

已知契约边界：Friday 只接受纯文字的最终回复。模型返回的内容如果是（或包含）JSON 对象，
Friday 会把它当成自己的结构化信封解析，找不到回复正文时报
`invalid_conversation_reply:empty_reply`。本服务的所有 workload 都要求带类型的 JSON 结果，
因此这条路由能否承接真实任务取决于 Friday 侧是否接受 JSON 回复。

健康探测使用同一 Friday HTTP 契约和配置，但只提交合成 prompt，不访问业务数据、不调用业务
工具、不执行外部写入。`tests/e2e/test_runtime_failover_live.py` 默认运行合成路由契约测试；
真实 Codex/Friday provider 探测和 fallback E2E 必须显式设置
`CEO_LIVE_RUNTIME_FAILOVER_E2E=1`，并提供真实运行时配置。这样默认测试不会消耗 provider
配额或依赖本地 Friday 服务，真实 E2E 则验证网络、认证、operation 完成和 Artifact 读取。

## 重复执行与恢复

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

`review_candidates` 的执行块保存租约；`candidate_execution_alias` 仅保留原执行物理 ID、公开 ID 和候选 FK，旧形状执行投影连接别名与计划读取当前状态。`candidate_action_attempts` 的 `execution_id` 继续指向别名，在 provider 调用之前保存 dispatch 边界；已验证回执复用。进程中断或超时没有回执不能证明未发生效果：先读取原外部对象。确认成功继续，确认没有效果才可重试同一计划，无法消除的歧义保存 uncertain/failed，不伪造完成或人工问题。技术恢复不重新审核不变业务方案；业务事实改变则使候选失效，重新形成并审核方案。没有通用 shell 或 Agent 执行兜底；未支持的动作明确失败。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

角色同一 revision 每个 pass 最多两个技术 turn；Consumer、Audit 各自按持久化的同角色失败 run 计数，首次失败后最多再重试 3 次（4 次失败 turn 后终止），互不占用对方预算。容量、连接、来源刷新和其他可重试技术失败均计入该角色预算，并保留真实 source_code；同角色成功 turn 会重置连续失败计数。`dependency_read_unavailable` 按同一上限处理；本轮缺少所需的已声明工具或可执行的选定 Skill 脚本时，Consumer/Audit 报告 `operation_unavailable`，服务直接终止该次失败，不重复运行相同能力集合，也不把技术缺口变成人工授权请求。服务也将 Agent 报告的 `capability_unavailable` 和 `write_capability_unavailable` 归到同一不可重试类别，同时保留原始 source_code；这两个名称在 416428 的四次 Consumer 轨迹中交替出现，重复运行没有补出缺失能力。权限申请后的等待由经审核的 purpose `retry` 动作表示。`agent_context_refresh_failed` 使用持久化 run 计数与共享退避，最长 15 分钟，也受该角色总预算限制。成功读取但无匹配数据或业务证据仍缺失不属于依赖错误。未开始执行的活动租约等待不产生失败 turn，技术预算与内容预算独立。

## 统一外发消息后缀

服务向人员发送的 DingTalk 或 WeChat 文本，必须先通过
`app.service_message_sender.ServiceMessageSender` 准备为持久化的
`PreparedOutboundMessage`，再交给 provider adapter。准备阶段恰好附加一次服务签名；启用反馈服务时，
还会恰好附加一组点赞/点踩链接。每个逻辑 delivery 使用稳定的 `delivery_key`，其最终正文和
feedback token 一经写入即不可变；重试、恢复和撤回复用同一份最终正文，不能再次拼接后缀。

Consumer 提案中的消息动作在 Audit 前完成这一机械准备，因此 Audit 看到的就是实际待发送文本；
这不改变 Consumer/Audit 的业务职责，也不引入新的审核回合。源码架构测试禁止业务模块直接调用
DingTalk 原始发送方法或 WeChat IPC runner。Email 不属于该文本发送策略，仍禁止 SMTP、自动回复
和任何邮件发送路径。

## 持久化与审计

Consumer/Audit 的完整 Application Result Contract 各在 Developer 的“应用结果合同”段注入一次。Task context 只渲染当前事实、阶段、候选与反馈，继续保留显式自定义 Audit Rules；不再次复制通用结果合同。Audit 的旧 Role Boundary 与应用结果合同完全相同，合并为同一段。输出 wire schema、业务合同内容、会话绑定与路由不变；Settings 使用同一装配函数显示该来源与顺序，历史输入按原保存内容展示。

报告工具已统一为上述显式事实、文档与产物接口；字段结构、格式转换和精确计算由各 Skill 的脚本承担。

Codex 原生 session JSONL 是详细审计来源，保存每个 Agent turn 的提示、工具调用、输出和
结果。SQLite 只保存恢复所需的最小状态：

- task/generation、角色、proposal revision 和父子 run 关系；
- A/B session ID 与 transcript 行范围；
- operation、target、provider result identifier（仅在 provider 返回时保存）；
- run 状态、租约和下一次可用时间；
- 服务采用的业务结果和精确去重键，原始最终输出只从原生来源读取。
- provider 原始工具事件按执行顺序在原生 session 保存；SQLite 只保留定位引用，应用层不分类命令、读写模式或工具权限。
  原始参数与工具结果以 Codex session JSONL 为详细来源。

服务不在 SQLite 复制完整 Codex transcript，也不维护另一套业务审计日志。History 页面按
session 指针读取 JSONL，并只向普通用户展示业务结果；内部角色、规划标签和原始敏感工具
输出保持折叠或脱敏。

Attempt 详情只为页面展示的历史执行代批量读取 Agent 工具事件。若任务已有更新的执行代，
当前状态投影只读取新执行代的运行状态，不再重复加载其工具事件。
详情页将这些运行的 runtime 尝试记录合并为一次按 run ID 查询，保持 run 内尝试顺序。
Attempt 详情先在 SQLite 中读取所选 run 的原生轨迹引用并关闭连接，再读取和解析 transcript
文件；本地轨迹较大或读取较慢时，不会占用 SQLite 连接。
完整原生事件证据读取与详情投影使用相同的协议类型归一化：Codex `McpToolCall` 映射为
`mcp_tool_call`，保留同一 call ID 和原始结果，使 scheduled Consumer 的既有文档写入回执投影
可以识别原生证据。此归一化不表示 Audit 批准，也不触发重放或修改任务终态。

为避免一个详情页因同一 session 的 Consumer、重试和 runtime 记录而重复扫描全量本地
索引，`session_path_index.jsonl` 的最新记录按文件的 mtime、大小和 inode 做进程内只读缓存。
索引文件变更后下一次解析自动失效并重建该缓存；缓存只加速 session 路径发现，不改变
SQLite 投影、JSONL 原文或审计证据。

若索引记录仍在、其原路径已清理，路径解析会直接检查 Codex 平铺的 `archived_sessions/`
对应文件名；若也不存在，就按 transcript unavailable 返回，不再递归枚举全部历史会话目录。
因此清理掉的旧 transcript 不会拖慢 Attempt 详情，也不会改变 SQLite 中的业务结果与审计记录。

详情页判断既有外部动作时只重放已持久化的工具事件和 provider 回执；它不得为了渲染
History 再启动原生 CLI 去发现命令元数据。运行时发现失败属于执行事实，不能成为一次
只读 History 请求的延迟或副作用。

当原始 session JSONL 已被清理、不可读取或不再可用时，Codex 详情页必须明确显示
“Agent 记录不可用”，不能把它误解成业务结果丢失。页面仍可展示关联的 Attempt 索引；
关联仅限于该 session 所属 `agent_run` 的同一 task、同一 execution generation 的当前投影，
不得把同一 task 的早期 generation 全部误标为这个 session 的记录。同一 session 可能被多个
处理轮次复用，因此关联 Attempt 的数量不等于发送次数或外部动作次数。用户可从最新关联事项
查看当前状态，较早记录应保持折叠，技术状态枚举必须转换为可读标签。

## 终态语义

| 状态 | 含义 |
| --- | --- |
| processing | Consumer、审核、内容修订或已选择计划正在执行。 |
| needs_human | 当前完整问题和分支已审核通过，等待本次输入。 |
| executed / done | 系统完成全部计划且有验证回执。 |
| no_action / skipped | 已审核无需动作或选定停止，记录具体原因。 |
| failed | 技术、外部效果或内容预算最终失败，保留原因。 |

approve 是审核结论，decision_selected 是选择事实；两者均不能投影为执行成功。旧 executed/feedback_provided Audit 结果仅历史可读，不能作为新计划授权。

## 关键模块

| 模块 | 职责 |
| --- | --- |
| `app.worker.DingTalkAutoReplyWorker` | 领取任务、构造上下文、调用编排器并映射终态。 |
| `app.agent_orchestrator.AgentOrchestrator` | 在 A、B、反馈和失败重试之间推进状态机。 |
| `app.business_skills` / `app.managed_skills` | 提供八个仓库基线 Skill，并管理 SQLite 中不可变 revision、next-start config 和启动 load receipt；不参与业务路由。 |
| `app.agent_skill_usage` | 提供 Agent 执行环境所需的 Skill 读取辅助；不参与普通业务结果审核。 |
| `app.consumer_agent.ConsumerAgentRunner` | 复用兼容的 A session，业务准备和完整候选；保留任务绑定文档/报告能力，受控动作由系统执行。 |
| `app.audit_agent.AuditAgentRunner` | 复用兼容的 B session，只读审核完整候选并返回绑定 digest/revision 的结论；不执行动作或返回回执。 |
| `app.agent_contracts` | 严格定义 A proposal 与 B audit result。 |
| `app.audit_rules` | 保存、校验并分别渲染共享 Audit Rules。 |
| `app.codex_runner.CodexRunner` | 以原生 `codex exec` 启动并继承安装用户的 Codex 配置。 |
| `app.friday_runtime_adapter.FridayRuntimeAdapter` | 通过 Friday Thread/turn/operation/Artifact HTTP 契约执行一个 Agent turn；不实现 provider 选择。 |
| `app.friday_runtime_contract.FridayRuntimeContract` | 定义 Friday Runtime 请求、认证头、operation 状态和最终 Artifact 的稳定契约。 |
| `app.channel_gate` / `app.mcp_doctor` | 在运行前检查 CLI 与 MCP 依赖。 |
| `app.store.AutoReplyStore` | 保存队列、run 关系、租约、revision 和最小恢复状态。 |
| `app.audit_web` | History、Agent session、Audit Rules、配置和恢复入口。 |

Worker 下载消息图片时，`content` 中的媒体资源归当前消息所有，`quoted_content` 中的媒体资源归
`quoted_message_id` 指向的原消息所有。资源下载和任务内缓存使用资源来源消息 ID；失败诊断明确记录
该来源 ID，而错误行仍绑定触发消息 ID 以便与原任务恢复。引用内容缺少来源 ID 时保留技术错误，不把
回复消息 ID 冒充资源所属消息。

## 运维入口

```bash
# DWS + Lark 通道状态
"$HOME/miniforge3/bin/ceo-agent" channel-doctor

# MCP 注册与可用性诊断；加 --verify-live 做实时探测
"$HOME/miniforge3/bin/ceo-agent" doctor-mcp --verify-live

# 单次 dry-run
CEO_NOT_SEND_MESSAGE=1 "$HOME/miniforge3/bin/ceo-agent" run-once --not-send-message

# 质量巡检并验证外部通道
"$HOME/miniforge3/bin/ceo-agent" quality-check --verify-channels

# 当前唯一 launchd job
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

安装和配置细节见 [agent-installation-runbook.md](agent-installation-runbook.md)，任务恢复细节见
[reply-worker-reliability.md](reply-worker-reliability.md)。

## OA 审批处理原则

审批 Agent 必须先读取最新 OA `detail`、`tasks`、`records`，按
`oa_process_instance_id` 去重；History 按审批事项归并展示，不能让旧 attempt 的
Failed 覆盖后续真实状态。无时区时间按 `Asia/Shanghai` 解释，比较时转换为 UTC，
同时保留 OA 原始时间字符串用于审计；缺少时区不得产生冲突错误。

审批申请人是其申请陈述的权威来源。实际申请人明确说明已补充所需材料或已修正关联状态后，
Consumer 和 Audit 必须接受该陈述并从该事实继续审批；不得要求另一个业务系统再次证明，也不得
用延迟、缓存或与申请人陈述冲突的系统视图推翻申请人。只有申请人尚未说明的必填信息仍然缺失时，
才继续请求补充。申请人确认本轮要求已经完成后，该要求即结案；同一回审轮次不得新增此前未提出的
格式偏好、评分方法细节或潜在歧义要求，除非 OA 表单明确标记的必填字段确实为空。外部系统读取可
帮助理解申请，但不构成推翻申请人陈述或延迟审批的应用层证据门禁。

审批实例和当前 task 仍为 `RUNNING` 且申请人尚未说明必填信息时，Agent 必须在原审批中
评论具体缺失材料并通知实际申请人，保持审批待处理，不得让
Derek 选择。已有相同目的且已确认的评论或通知不得重复写入；新材料出现后基于最新
OA 内容重新运行 Skill。已有后续终态时只读对账。

申请人说明某项必需事实“尚未形成”或“正在等待技术讨论”，但申请人仍可协调责任来源方补齐时，
这仍是材料补正阶段：正式退回当前申请，要求准备好后重新提交；不要把“现在暂时拿不到”转成
Derek 选择是否带着缺口继续。不要重复发送同一份宽泛清单；通过退回明确本轮先暂停补正，之后
根据新材料重新审阅。只有补正阶段完成后，仍存在独立的公司规则、例外或授权缺口，才进入
`needs_human`；已证实不存在申请人/责任来源方可行补齐路径也不能单独构成规则问题。

已提交材料因审批服务的权限、下载、解析、OCR 或客户端能力而读不到，不等同于申请人缺少材料：
按通用 OA Skill 记录审阅端技术阻塞并保留待办，不因该失败要求申请人重复提交同一文件，也不将
由此产生的错误退回计入重复退回拒绝条件。只有独立证据确认源文件本身缺失、空白、损坏或缺页时，
才按申请人可补正的材料缺口处理。财务审批按附件逐项记录文件名或节点 ID、授权读取结果、可读文本/页数或 OCR 结果及已核实事实；一个附件的读取失败不能推及其他附件，所有文件均不可读的结论须有逐文件失败证据。已读取的其他证据仍须单独评估。

供应商付款所需的客户验收/结算确认属于第三方事实。审阅方未取得客户邮件正文时，不能据此推定申请人未提交该材料；只有独立证据证明申请材料确实缺少该项且申请人能够补正，才可向申请人提出具体追问。否则保持待处理；已终态流程只作 `no_action` 历史分析。

重试复用同一个正式任务和审批实例，不创建替代审批事项。瞬态故障进入 exponential
backoff；终态失败必须说明根因、已尝试动作、provider 标识（若有）、下一步和重试条件。
DWS/OA 技术错误不得直接暴露给申请人，所有“已评论”“已通知”“已完成”都必须由 Agent
根据 provider 返回结果确认；应用层不额外要求发送 read-back 证据。

Codex Agent 可以通过 `agent_cli.read_skill` 读取 Skill；Skill 读取属于 Agent 执行环境，
不是应用层业务结果的前置 receipt。launchd 业务服务不是 Skill 可读性的前置条件。

### 已审核来源、执行进度与失败展示

Consumer 完成候选准备时，把原 trigger、消息/材料和计划涉及的 OA 原表单或文档源事实保存为 source_bindings，参与 candidate digest。System Executor 在每个尚未核验动作派发之前重新读取相同原对象，包括首次执行、同次后续动作和恢复部分执行。OA 已完成动作造成的 task 状态/操作记录变化由具名处理器核验，不作为原表单变化；真实表单、文档或上下文变化使该 review 失效，保留已核验动作回执和原人工回答，在同阶段形成新的完整候选并重审。来源读取失败属于可重试技术失败，不执行旧分支。

来源快照中的历史反馈正文只在结果检查副本中解析 Markdown 链接，并对真正的序列化来源容器递归检查。一个来源文本可合并多条历史消息，因此按 feedback_token 与 attempt_id 分组，并逐组验证各自完整的一条 up/down 链接；每个链接仍须通过当前配置 host/path/query、评分和敏感值检查。旧链接中的 original_text/reply_text 是预览文案，不作为配对身份；提供方渲染后两份预览可能不同。解析保留链接原文拼写，避免 Unicode URL 规范化后无法从检查副本中移除。当前待发送正文仍要求原始严格格式及完整 query 配对；原 source_bindings、候选 digest、持久化结果与发送前来源回读比较不变，其他来源字段继续接受原有敏感值、深度和大小检查。确定性的来源结果验证失败记录 runtime_result_source_invalid、stage=result、source=service、retryable=false、session_continuable=false；不归为 CLI/provider 失败并反复恢复同一会话。

Attempt 详情的 system_execution 按 candidate、review、selection 绑定显示当前及历史阶段、声明顺序、未开始动作、失败/不确定状态和 provider 回执。只有 canonical action ledger 中核验成功的动作计为 verified；Audit approve 和用户选择都不能显示为已经执行。

Attempt History 的外部效果摘要进一步区分本执行代首次产生的 verified effect 与本代复用的旧回执。当前代的 effect 必须由回执记录的原始 Agent run 归属；复用回执单列为“已复用”，不把旧操作归到本轮或本轮 acting run。动作时间取 provider 结构化操作记录中的原始时间；不以本次复用或 Attempt 更新时间替代。该区分只影响只读 History 投影，不改变 verified receipt、幂等复用或任何 provider 操作。

失败 Reply task 的 Attention 诊断只读取本对象本 generation 的当前 run，显示原 source_code（无则 code）与明确标为“Agent 说明”的 reported_summary；缺少或坏 JSON 时保留 task error，不将 Agent 自述当作已证实的 provider 拒绝。History、Attempt 详情和 Reply attempts queue 对微信分别读取本对象当前 generation 最新 delivery：failed/send_unknown 不能被只表示候选完成的 task done 遮蔽。旧 generation 或其他对象的 delivery 不影响当前结果；这些展示不更新任务、投递或授权状态。

独立 Email 退订仍使用原有 direct executor、浏览器步骤和回执；所有结构化 `action_type=unsubscribe` 失败都保留真实 `failed` 状态和 History 记录，但不进入管理者 Attention。已识别的可重试外部页面/网络失败按有界指数退避重试；共享上限是初次操作加 3 次重试，因此执行标记记录的 `retry_count=3` 仍允许第 4 次浏览器操作，之后的调用在浏览器运行前以未决结果拒绝。当前 `email_unsubscribe_browser_failed: category=<名称>` 和旧 `failed_browser: category=<名称>` 在附带脱敏 `detail`／`operation` 后缀时仍依据固定外部类别匹配，不会误归入内部错误。服务自身的执行围栏错误（如 `unsubscribe_operation_rejected:ValueError`）仍归类为内部技术错误，但结果不确定时只保留失败并等回读，不重放操作或清除标记；不确定性不改变 Attention 投影规则。效果零副作用的旧失败任务也只有在 ActionPlan 完整且没有执行中标记、claim、receipt、effect、浏览器 step 或 Agent run 时才会自动重新入队。无运行时调用者的旧 Agent continuation driver 已删除；Agent finalizer 不再从历史 Audit 声明触发退订、切换渠道或伪造执行成功。默认 developer prompt 和 OA 规则种子统一为新角色合同；既有自定义规则、managed Skill 配置与 runtime-only Skill 必须在安静的正式发布窗口中逐项发布并记录版本，部署代码本身不会覆盖它们。

退订入口导航遇到超时时，若同一次导航已离开空白页，执行器直接读取当前页面并检查真实页面回执，不再请求退订 URL；若仍停留在空白页，则保留外部超时失败并沿用有界退避。该读回只恢复已经到达的页面证据，不清除未知执行围栏，也不把页面未读成成功。

原生 OA detail 明确 success=false 时，来源读取边界保留 errcode/errorCode 及 errmsg/errorMessage，按提供方错误记录原 server/code/认证属性；不把 native 失败 envelope 当成空表单或通用 Codex 错误。Consumer 失败 run 保留原始脱敏说明，System 执行技术失败保留来源和原码，不派发或制造业务人工问题。

Consumer/Audit stages (2026-10-04): immediate continuation is reserved for work whose inputs are established by the preceding verified action, such as approval followed by applicant notification. Material requests complete their own stage and wait for a later source update; their delivery receipt is not evidence that materials arrived. Audit uses the same complete production review instructions as the frozen model evaluation, and checks actual provider identities and complete effects rather than outcome labels alone.

角色 MCP 启动参数携带原领取 run 的 execution_generation，启动时与当前任务校验；旧回合不能在 generation 更换后绑定到新一代工件，运行中的既有读写仍逐次检查当前 generation。

未解除的实际 provider-risk refusal 保留 failed 与原始错误码，不重放；Consumer 不能把“拒绝后不重试”报告为 no_action 完成，Audit 应退回该终态错误。这个规则依据真实拒绝证据，不扫描正文完成短语。

### 显式维护部署（2026-10-05）

Derek 明确授权解除生产循环对部署的阻塞。`python -m app.deploy --maintenance-task <id>`
可重复声明本次允许中断的任务；普通部署仍等待空闲。维护入口在同一发布锁内核实所有在途
工作，只有指定且无候选、动作意图、回执或工具/未知事件的 Consumer 准备运行允许中断；
摘要、会议、邮件动作和其他调度领取仍会阻止维护。先冻结已核实的 Dispatcher owner 及其后代至进程树稳定，阻止新领取和子进程产生；通过现有 launchd 停止入口停止整个服务并终止存活的已冻结后代，
确认已记录的运行进程及其子进程退出，再核对业务对象、执行代、输入版本和效果。
这是受控中断，不是优雅排空。先创建完整且 integrity_check 验证的维护备份，随后调用既有
服务中断恢复方法，原任务回到 pending、保留业务身份和历史，保存 maintenance 回执；
正常空闲检查、部署备份、主线快进、构建、验证、启动和健康检查继续执行。确认进程树全灭、尚未应用代码或契约时的准备失败才启动
原服务；冻结、终止失败或 needs_manual 保持停止。生产源码仍在 finally 重新锁定。不修改业务成功状态、不手工抢租约、不重放风险拒绝。

调度器处理 Reply handler 异常时最多执行四次任务 attempts（首次调用加三次 retry）：
前三次共享退避 60/120/240 秒，第四次 failed，不再退回 attempts 并立即重新领取。此规则也适用于定时执行的 Reply 子类，
保留业务对象、代次和底层错误。正常调度 release 与实际 handler 失败分开。

### 原生消息 ledger 读回（2026-10-05）

`chat +chat-messages` 的当前 `im.message-list.v1` 返回顶层 messages，记录字段为
conversationId/messageId/text/createTime/sender/senderId。DwsClient 按该原生契约解码，
保留原消息及会话身份和 raw_payload；原生 atomic 查询的 result.messages 继续按其自身
provider 字段解码。通用 senderId 只与真实通讯录 profile 的 user_id/open_dingtalk_id
匹配，不猜身份类型、不因展示名相同就认定为本人。恢复读取使用相同解码，仍保留既有
准确目标/正文/时间/作者及歧义处理；解析修复不重新发送、修改候选或产生新的执行回执。


### System 与未创建角色运行的实际失败上限（2026-10-05）

定时任务在 Consumer/Audit 已完成后的 System 实际失败使用四次持久化任务 attempts
（首次调用加三次 retry），前三次至少延后 60/120/240 秒，第四次 failed；重新入队不退回计数。候选、审核、动作键和
已核验回执保持不变，效果 uncertain 仍保留 uncertain 并只回读核验，不重发。
execution_claim_unavailable 是未取得执行领取的等待，至少延后 60 秒，不消费失败预算。
定时任务的来源刷新失败，以及 Worker 尚未创建角色运行的普通技术失败也保留 attempts，第四次写入 failed 历史，
保留原错误和 summary。既有授权、活跃运行及 provider 恢复等待沿用原等待机制；
真正开始后产生的 Consumer 与 Audit 技术失败分别计入独立、持久化、同执行代/同修订的四次尝试预算（每个角色最多三次 retry）。

## 运行环境与能力说明、Settings 完整输入预览

Consumer/Audit 的稳定指令包含原请求交付、按需取证、部分事实先交付及参与者时区原则。
稳定规则参与 Consumer 契约 hash；本轮时间与任务事实不因每次变化强制创建新 session。
每次实际 route 与角色 CLI 配置完成后，运行时追加「运行环境与能力说明（Runtime Context）」：
principal、task/generation/stage/revision、原业务对象与后台扫描来源、所选模型和 thinking、
资料根目录、实际命令目录及声明工具目录。工具说明复用 role server 注册描述和最终 allowlist，
不声明认证已经成功，不由个人安装能力推定后台权限。Claude 继承服务进程 cwd；Codex 使用
显式 `--cd`，缺少明确目录的续会话标为未核实。Consumer 普通材料工作与系统注册且要求审核的
动作保持既有边界；这项说明不新增审核、授权、路由或执行政策。

日历任务按原请求和实际来源核实本人、对方及必要协调者时区，按会议日期处理夏令时、跨日和
当地时间歧义，并考虑已知工作时段/偏好。结构化来源已经提供的时区、来源和适用日期可进入
环境事实；文字材料仍由 Agent 理解，不通过姓名或关键词分支提取。机器时区、公司所在地和
钉钉源时间戳解析规则不证明对方所在地；未知时区与未知忙闲分别标注。

`Settings → Prompts → 运行输入` 显示完整服务输入，支持 Consumer/Audit、所选配置路线、
当前配置预览与历史 run。当前预览使用同一 developer/Skill/环境组装逻辑；无任务显示未绑定，
绑定任务仅复用已保存的完整该角色任务正文，并标记来源 run/时间。不用省略材料、反馈和回执的
原触发重建结果冒充完整输入；旧任务缺记录时显示不可用。历史模式读取当时保存内容，保留
run/runtime attempt/generation/revision/stage/route/model/time，不使用当前 Settings 改写历史。
用户指定的历史角色、任务或路线不匹配时返回 422。页面预览只读，不读取外部业务源或运行任务。

输入使用现有 `agent_run_events` 保存：`runtime.prompt` 是准备提交的服务输入；
`runtime.prompt.invoked` 只记录运行适配器返回，不能证明模型接收、业务完成或外部效果。
凭据使用已有脱敏函数处理并显示标记。服务输入范围不包括 CLI 自行生成的系统提示、原生工具
schema 或完整会话历史；未保存的旧 run 不重跑补齐。没有新增数据库、迁移或独立审核台账。

历史输入可按 `runtime_attempt_id` 选择同一 run 的每次路线尝试，逐次显示 prepared/invoked，
后来的准备失败不会隐藏早先已调用的输入。当前预览重新渲染当前默认 Skill 目录，仅复用明确
标记的 task override；任务正文仍标记保存来源。时区事实只投影 participant_id/timezone/
source_ref/applies_on 的字符串字段，其他来源字段不进入环境段；快照复用结构化凭据脱敏。

Prompts 的 Developer/User/Profile 各自保留 Template 与 Rendered preview 的一一对应；预览只渲染
同一份已保存模板，未保存草稿不进入预览。保存成功后重新读取服务器模板及渲染结果，避免继续
显示旧预览。后台 Consumer/Audit 的多段完整输入放在独立只读「运行输入」页签（prompt=runtime），
不再占用 Developer/User 模板的 Rendered preview；该页没有 Template 或保存操作。

Developer Prompt 只承载适用于所有任务的角色、能力、证据、系统动作、对应路线所需的输出契约及共同工作原则；工作人格仍注入 Consumer 与 Audit，Audit Rules 只进入 Audit。Audit 启动时，服务从固定路径预载共享 `~/.agents/AGENTS.md` 和项目根目录 `AGENTS.md` 到 Audit Developer 输入；文件缺失或不可读会阻止该次审核。Consumer 不注入这两份指令，Agent 的 `read_text_file` 可访问范围也不改变。Consumer 使用 Codex 或 Claude 原生输出 Schema 时，服务输入省去重复的完整 Pydantic wire schema 文本；没有原生 schema 的 Friday 路线仍保留该文本，Audit 输入不变。所有 Consumer 路线继续共用不得直接发送对外消息、邮件或执行审批的角色约束；本精简不改变该约束、严格 Pydantic 业务校验、原生 schema、Audit 或 System 执行流程。OA、日历、消息受众、OKR、招聘和文档等专项流程由当前任务选择的 Skill 承载，不再作为全任务 Developer 规则重复注入。User Prompt 是 Consumer 的完整任务模板，必须包含恰好一个 `{{task_context}}`，仅支持普通文本和这个插槽。任务来源、定时要求、stage、反馈、既有回执及 continuation 先由服务组装成完整上下文，再填入插槽；不将旧消息块模板叠加到后台任务。Workbench、独立 WeChat、纯服务命令及 Email 退订保留各自入口和显式指令。

Consumer 和 Audit 的 Task 输入都携带当前任务已保存的专项指令一次。Audit 将它作为候选审核的任务要求，来源记为「已保存任务配置」；不将其放进通用 Developer，也不依赖候选转述任务规则。两者复用已有的精确 Skill 附录分离逻辑，生成的发现提示不重复预载，自定义 Skill 约定仍在独立的 Skill 段保留。此输入修复不改变业务规则、规则优先级、审核或执行权限。

Consumer 的代码生成「Capability Instructions」只说明当前角色的工具使用细节：任务工件／文档写入入口、原生工作区、钉钉直连只读工具、依赖读取失败与风险拒绝的结果码。通用的按需取证、普通工作与受控动作边界、回执及失败原则已在同一 Developer 输入的已保存模板和角色合同中提供，不在能力段复述。能力目录仍提供精确 `read_system_action_contract` 入口；完整结果校验、Audit 规则、Task 事实和现有 session 绑定不变。Settings 当前预览与执行使用相同装配段，历史预览读取保存的原输入。

服务组装的 Consumer Developer Prompt 不读取本机 `AGENTS.md`。Audit 启动时由服务仅从固定路径读取共享 `~/.agents/AGENTS.md` 和本项目根目录 `AGENTS.md`，作为 Developer 指令段注入；缺任一文件或读取失败时停止该次 Audit，避免依赖 Agent 通过受限的 `read_text_file` 访问项目指令。该读取由服务执行，不扩大 Agent 文件工具的授权根，也不接受任务提供的文件路径。Consumer 的角色、能力、输出契约和后台 Memory bootstrap 仍由原有代码提供；本机 CLI 的全局指令及 Agent 文件读取能力不变。

后台 Consumer/Audit 使用原生 skills.config 禁用专项 Skill 的自动正文注入，避免原生加载、Developer inline 与工具读取三份重复。原生 `project_doc_max_bytes=0` 关闭 CLI 自动加载项目目录 `AGENTS.md`；共享及项目指令只由 Audit 的服务 Developer 段按固定路径预载，Consumer 不接收这两段。全局 `~/.codex/AGENTS.md` 仍由 CLI 加载，服务不修改原生 home 或全局规则文件。此设置仅适用于后台角色命令。

角色命令只在最终角色构建器生成一次原生 Skill 配置。AgentTurnProcess 不再让适配器先按任务 Skill 生成随后被角色构建器丢弃的配置，避免重复扫描 Skill 目录和序列化。任务选择仍用于每轮 Task 的 Skill 入口及运行来源回执；最终原生配置、工具权限、会话和路由不变。独立直接使用 CodexRuntimeAdapter 的调用仍可传入自己的 task Skill 选择。

Codex Consumer/Audit 对 `read_task_skill` 与 `read_skill` 均使用原生单工具 `output_token_limit=30000`，让冻结与已安装 Skill 的正文在 MCP 完整返回后完整进入模型历史。`read_task_skill` 的 MCP 回执只提交一份含 name、完整 content 和 sha256 的 JSON 文本，不再同时附上内容相同的 structuredContent；冻结来源、角色权限和业务结果不变。其他工具输出预算、角色、sandbox、审批方式和 session 绑定保持现有行为。该设置保障来源交付，不替代 Skill 精简或证明业务判断正确；验证及发布记录见 `docs/task-skill-native-output-validation.md`。

System Action Contracts 在 Developer 中提供由 docs/system-action-contracts.md 自动生成的 capability/operation 目录；agent_cli.read_system_action_contract(capability, operation) 保留动作表外的全部原规范正文，只返回选定能力／操作的动作表行；只给 capability 时返回该能力的全部操作。原有角色、identity、payload、target、回执和完成条件保持完整，不返回其他操作的表行。Consumer 提出及 Audit 审查相应操作前必须读取规范；契约指纹仍包含全文。结果 schema 在 prompt 中只去除 title/default 注释，保留字段名、描述、枚举、约束及原有 Pydantic 校验。原生 output-schema 保持关闭：实际探测显示现有 RootModel 顶层 $ref 被拒绝，等价展开后 Consumer 的 oneOf 与 Audit 可选字段仍不满足原生 strict schema 限制；未通过弱化业务契约来适配。

Audit 传输结果的 `feedback` 在 `approve` 或 `failed` 时可省略，解析为 `None`；在 `return` 或 `reject` 时仍必须提供完整反馈。批准结果若携带非空反馈仍拒绝。服务绑定的候选、审查业务模型、结果校验、审核决策和外部动作权限不变。这个字段语义对齐针对两次完整轨迹中唯一的格式错误，验证见 `docs/audit-null-feedback-validation.md`。


运行输入的分段来源由同一装配函数提供：每段记录 name、source、placement（Developer/Task）及脱敏正文 characters，长度不含段间分隔符。runtime.prompt 是运行期 RAM 中的准备/调用回执；终态不另存正文副本。Settings 公共预览使用当前角色装配，任务绑定预览区分当前 Developer 与该角色实际保存的 Task。历史预览读取选定 runtime attempt 的原生输入范围：Codex 只取带 generic.developer_instructions 与 user.text 标记的服务消息，排除 CLI 基础指令、AGENTS、环境、工具和历史；续会话明确标记 session 先前保存的 Developer，不冒充本轮配置提交回执。Claude 读取本轮原生服务合并消息的既有 Developer/Task 封装。没有细分来源、配置指纹或渲染时间时保持未记录，不用当前配置补齐。尝试列表保留不可读来源的准确 ID/路线；最新或选定来源缺失时明确不可用，不改显示较早输入。读取不重跑任务、修改数据库或保存原文副本。日历时区解释流程位于 ceo-calendar-invite Skill，Runtime Context 仅保留显式参与者时区事实。

一次角色 invocation 只读取一次 Developer/User/Profile 正文，供组装、静态指纹与重试共用；Audit 不读取无关 User 模板。实际路线和工具命令确定后，再追加 Runtime Context。静态配置 SHA 写入运行期 runtime.prompt invocation facts，仅作来源回执；原生历史未保存这些元数据时不重建 SHA；Consumer 继续按 `conversation_id + route` 复用原 session，配置或 contract hash 变化不创建新会话，Audit 的独立 session 不与 Consumer 合并。

默认 User 的 Rendered preview 使用明确标记的合成完整任务，不读取业务材料；Developer/Profile 渲染同一份已保存正文。完整运行输入仍在独立只读页签，当前配置 Developer 与所选已保存历史 Task 的来源分别标注，历史模式不重新渲染。读取设置/预览不创建或覆盖配置文件。已存在模板不在读取时自动升级；部署使用 `python -m app.deploy --publish-prompt-templates`，仅在既有停止/备份窗口根据 `ci/prompt-template-release.json` 的精确旧/新 SHA 发布默认 Developer/User。本次默认 Developer 发布只接受上一版精确默认 SHA（3ccd6261291b6610aa95a4764d4fd81b7c232011f8ca7dbc52b18639703fb457），移除全局日历专项段；原规则逐字保留在实际 authored calendar Skill，User 默认和工作人格不变。自定义模板须明确迁移，不能覆盖；文件备份、发布与回退沿用 RepositoryUpdater 的 publication 协议。该发布不修改工作人格。

Prompts 设置读取先返回 Developer/User 的已保存原文；某份模板验证或渲染失败时，仅该份渲染预览为空并返回明确的 `preview_errors`，编辑器仍显示原文供修正。保存仍需通过现有验证，后台角色调用仍严格验证，不自动迁移或覆盖旧模板。

精简只移除重复或已由任务 Skill 承载的流程说明，不改变角色边界、身份与证据规则、完整效果判断、业务结果模型或 Pydantic 校验。Audit 的候选、source_bindings、revision 和 digest 保持完整；只有 provider、object_ref 与来源 value 按排序 JSON 完整相符（保留布尔/数值等 JSON 类型区别）时，Task 中重复的触发正文、raw payload、历史消息正文和材料 reference 指向候选的来源绑定，同一输入内仍能读到完整值。来源不同时两份全文保留。未结算的定时服务命令在 `scheduled_consumer.skill_materials` 冻结完整 managed revision 或 operation snapshot；未结算的通用 Scheduled Agent 在 `scheduled_agent_execution.v1` 顶层保存有序 `skill_names`、`skill_materials` 及其来源，原始 trigger 投影只保留结构化 Skill 身份事实，不再次复制正文。冷启动和续接都从同一未结算持久输入恢复；已结算输入改为来源摘要，冻结正文读取明确不可用。每一轮 Consumer 与 Audit Task 只列当前选中 Skill 的名称、极简用途和按需读取入口。保存输入声明该名称为冻结 Skill 时使用 `agent_cli.read_task_skill(name)`；所选冻结 Skill 的 revision_id/sha256 未变且完整正文仍在同一 session 上下文时，发现提示允许复用已读的确切内容；新会话、来源变化或全文不在上下文时仍准确读取，不新增缓存或本轮读取回执。材料重复、损坏或缺失由精确读取报告技术错误，不得改读当前磁盘版本。没有冻结声明的已选 Skill 使用目录中的实际授权绝对路径调用 `agent_cli.read_skill(path=...)`；Skill 正文引用的其他已安装 Skill 通过 `agent_cli.read_skill(name=...)` 精确读取，不把依赖目录预装进 Task。旧任务的显式 inline Task 约定仍在每轮保留。正文不复制进新 prompt，也不根据任务文字猜测来源。未选择 Skill 时 Task 只给 `agent_cli.read_skill()` 发现入口；无参数调用返回授权目录内的全部 name 和路径，不重复所有用途描述；按主题 `query` 查询返回匹配项的完整 name、用途和路径元数据，均不返回正文，Agent 再按精确 name 或 path 读取。目录查询和 name 解析沿用相同授权根，越界符号链接不会进入元数据，重名明确报错。目录 frontmatter 只提取顶层 name/description，嵌套产品 metadata 不会隐藏可用 Skill。无论有无选中 Skill，显式自定义 Task 约定都继续在每轮 Task 保留；服务生成的发现文字由结构化来源标记识别，不重复注入。新运行事实记录实际入口来源与本轮 `skill_names`；未注入的旧全量目录不再作为 `skill_protocol` 保存。Email 分类、Meeting Alignment 与独立 WeChat 没有任务内读取工具，仍直接使用完整冻结正文。Email 分类的 Codex 命令按显式内联 Skill 名称，通过原生 skills.config 仅禁用同名安装副本的自动正文注入；本轮服务选定的托管或冻结正文仍完整提交。此配置不重新启用或关闭其他 Skill，不影响 Claude/Friday 的原有单条消息提交，也不改变分类规则、工具权限、路由、审批或会话绑定。Runtime Context 缩短工具说明；Codex CodeMode 的可用工具清单显示原生 `mcp__server__tool` 可调用名称，Claude 保持 `server.tool` 表示法，其他环境事实不变。Task 中的 Skill 调用提示仍使用服务命名空间的 `agent_cli.read_*` 表示法；该提示与清单的区别在 `docs/runtime-mcp-tool-name-validation.md` 记录。无业务调用的旧 build_turn_prompt、ceo_agent_thread_prompt 及 CodexRunner 的隐含业务 Developer 默认入口已退休；底层 native 指令保留模式及真实调用者显式指令不变。

Developer 保存先以现有渲染器验证，未知变量或不可渲染内容返回具体错误且不覆盖已保存正文；该检查属于配置格式合同。

默认共同工作原则要求先识别原请求要向谁交付什么、按本轮声明入口取证、交付可核实部分并说明剩余协调责任。日历时区与候选日期偏移规则位于 `ceo-calendar-invite` Skill，只在适用任务按需读取；共同 Developer 不再全量携带该专项流程。两者都不改变已有角色、审核或执行合同。

配置回执同时记录 Developer 模板原文和渲染后共同原则正文的 SHA；相同模板引用的变量/文件/代码展开改变时，静态契约指纹也改变。静态指纹仍不是 session 身份；历史记录缺正文 SHA 时只显示未记录，不以当前值补造。

默认共同原则保留原有的部分交付与必要追问要求。审核角色、决定类型与生命周期保持原契约。
### 业务审核按完整上下文判断（2026-10-06）

Consumer/Audit 根据完整任务和对话上下文、本人职责、实际事实、接收对象、工作目的和动作后果判断候选是否合理。业务上下文不划分为可信或不可信，不另设“可信授权”或逐条消息许可声明。财务主题或群名本身不构成拒绝理由；不合适的候选必须指出具体事实缺口、受众不匹配、无依据承诺或与明确只出草稿／不发送要求的冲突。上下文不改变已配置能力，也不证明外部动作完成。邮件与微信 Skill 使用同一原则；Email 子系统现有 unsubscribe ActionPlan 和禁用 auto_reply 的范围保持原样。

Consumer 完整候选、Audit 精确审核、System 执行持久化计划及真实回执的绑定保持原样。实际 CLI／provider 认证、权限和拒绝错误仍按原诊断保留；本次规则修改不重放历史拒绝动作。固定规则在已保存自定义审核模板前注入，并通过正式部署发布邮件、微信 Skill 和默认审核规则。

### Attempt 无动作终态展示（2026-10-07）

任务的 done 表示处理周期已经结束，不能覆盖最新有效运行所绑定 Attempt 的 skipped：经审核无需动作的 Attempt 继续显示 skipped 和无需操作。判定使用现有执行代与最新有效运行身份，并按存储契约先比较 revision，再比较 Consumer/Audit 角色、该角色的重试次数和运行 ID；旧执行代或旧 revision 的 skipped 不覆盖后续任务结果。其他 done 详情也属于终态，保留既有微信待发送／重试操作对操作按钮的影响。该展示修正不修改任务、Attempt、Consumer/Audit 结果或外部执行回执，页面读取不产生业务动作。

## DingTalk 临时处理表情

普通钉钉聊天消息被服务接入处理队列后，原消息上添加服务账号的文字表情「处理中」。
日历邀请、合成 service_task 和 dry-run 不添加。成功回复的 provider 回执写入 sent_replies 后，
立即移除这个临时表情；无需回复、失败、转人工以及待处理任务换成新消息时也移除。
待处理和执行中的重试保留表情。表情的添加、移除失败不改变业务任务或回复结果。

ProcessingReaction 在现有 service_state 保存文字表情模板与每条源消息的添加/移除意图；
正常生产和消费 pass 根据现有 reply_tasks 与 sent_replies 清理已结束的源消息，
重启后继续未确认的表情操作。只操作本功能记录的源消息与服务账号的「处理中」表情。
该进度展示不改变 Consumer/Audit 生命周期、发送授权或现有业务效果检查。
## 会议 Memory 入队的 SQLite 边界

会议结论入队先通过会议状态索引与事件的唯一会议 ID 索引排除已入队历史，随后只读取缺失会议的
`final_message`，沿用 `sent` 且结论非空的资格。没有候选时不申请共享写锁；存在候选时关闭只读
连接，在写事务内仅对这些 ID 重新核验状态、结论与事件缺失，再生成原有事件和 execution generation。
并发入队仍每个会议只有一条事件，空扫描不修改事件或自增序列；只读快照之后新送达的会议由下一轮发现。
历史正文检查与全量会议扫描不再放在写锁内。

## Storage retention (2026-10-09)

已完成或明确跳过的工作汇总输入不长期保留正文。`work_summary_inputs` 在业务投影提交的同一事务中将正文替换为空对象，并保留来源类型和引用、来源时间、原始 UTF-8 正文的 SHA-256 与字节数、明确的精简标记、状态、尝试次数和错误，以及既有原生运行引用。扫描器再次发现同一终态来源不会写回正文，执行中的输入也不会被重复入队覆盖；迟到的失败或重试不能重新打开已精简终态。待处理、执行中和失败输入保留准确正文以供恢复；已采纳的业务证据、业务结果和执行回执继续保存。历史终态正文通过显式存储维护清理，不在初始化中批量清理。History 展示来源元数据；依赖原始正文的历史语义导入明确报告来源不可用，不将空对象当作原文或由业务结果重建输入。

回复输入在 `done`／`skipped` 且按既有条件已结算后精简：当前任务、其输入版本与对应 Attempt 不再保留输入文字副本，原始文字和 JSON 的准确摘要、字节数、消息身份、输入版本及运行引用继续保存。Attempt 的摘要按它自己的原文计算，只有准确匹配时才绑定输入版本；业务回复、采用计划、审查决定和执行回执保持原样。执行中或待恢复的任务、待人工处理、较新输入版本、活动执行／调度租约和未结束的微信投递保留准确输入；最新运行失败且既有回执／结算条件未满足的 `done` 也保留。正常完成与回执结算使用针对该任务的精简方法，调度租约释放后在同一事务重新检查该任务，不增加历史扫描循环。精简 JSON 保留已有查询及回执校验所需的邮件动作、计划版本、邮箱／消息／线程身份、分类字段、脱敏退订入口与认证引用，以及定时运行／配置与冻结 Skill 的版本、路径和摘要引用，并明确标记不可执行。原有不可变输入校验的批量入口以原文摘要核对，普通消息仍按身份去重，均不写回副本；准确的新输入仍按既有修订／代际逻辑入队。历史重跑或冻结 Skill 读取缺少准确原文时明确不可用，不用 Attempt 文字、业务结果或当前 Skill 重建历史输入。

训练数据正文和观察明细放在数据库旁的 `*-training-data` 目录，SQLite 只保留快照摘要与训练/评估元数据。运行时按快照类型保留最新完整基线，与各类型标签水位累计一致；已启动训练的 run pin 暂时保留其选定数据，结束后清理同类型旧快照。历史摘要不能恢复已经清理的数据，读取明确报告不可用。迁移先写出并校验每种独立数据类型的最新完整快照，再移除旧正文表；运行时不从摘要重建已删除的基线。既有迁移遗漏的当前基线仅从身份、水位及摘要完全匹配的已验证原始备份显式恢复，并读回校验，不恢复历史版本集合。

完整 Agent trajectory 的唯一数据源是 Codex/Claude 原生 session 或 Friday operation。服务仅持久化服务任务状态及定位原生运行所需的引用和准确范围；调用正文、精简事件、原始最终结果、runtime result envelope、审查调用正文及派生搜索正文不再保存第二份。当前调用流和解析结果仅在内存中使用，历史详情按需读取原生记录。迁移清除历史数据库副本，原生文件缺失的过程详情明确不可用，不回退到数据库副本。服务自己提交的业务状态、待执行输入和外部动作账本继续作为服务数据保存。

独立 Meeting、Task、OKR 运行的原始决定及审查正文也只从原生执行范围按需读取。Meeting 已采用的 job 决定、Task 已应用的 projection 回执和 OKR 已采用的业务事项继续保存。会议搜索索引只保存 session/source/title 引用，完整会议来源与决定从该次原生输入/输出读取，正文和向量在有界内存缓存中计算，按原生文件及来源变化失效；既有 0.55 cosine + 0.30 BM25 评分保持不变。原生来源缺失时只有服务标题可检索，正文不可用。

服务采用的冻结业务计划保存在既有 review_candidates 中，包含服务准备的投递标识和捕获的来源事实；该计划与 Consumer 完成状态在同一事务内提交，恢复、审查和执行都使用同一已采用计划。既有 candidate_reviews 保存已采用的审查决定和修订反馈，并与 Audit 完成状态一并提交。服务准备阶段产生的失败在同一事务内记录 runtime attempt 与 Agent run 的失败状态，不把原生输出中的成功提案当成已采用计划。任务恢复不重新运行已完成的 Agent 或重新捕获历史来源。上述业务状态不作为 Agent 原始输出或过程详情的替代来源。最终任务记忆从已采用的 Consumer 计划读取，并按任务和执行代际去重；原生过程不可用不影响该项服务输入。运行流只在 RAM 中保留，事件发布使用既有 SQLite 写事务排序，终态成功提交后释放正文缓存，事务回滚保留实际观察到的运行流。运维延后与维护检查按原有条件读取准确范围内的原生证据；记录不可用不能证明没有工具活动，已证实尚未创建原生会话的失败仍按原条件处理。

业务 History、Attempt 指标和审批结果从服务已采用的 Consumer 计划、Audit 决定及既有 verified 外部动作回执投影，原生原始输出仅用于过程详情。审批回执按实际结构化操作、任务/执行代际及审批实例/已知任务标识匹配，冲突仍显示未知；不改变执行或审查规则。状态列表和恢复状态判断只读取服务状态及采用结果，不加载原生正文。工具开始记录保留实际输入，完成记录提供结果；同一原生 item 配对时，完成记录的空参数不会覆盖开始时的输入。

过程详情独立说明原生最终结果和工具过程的可用性；Friday 最终 Artifact 可读不表示工具过程也可读。可读的 Claude 记录没有受支持查看器时不提供错误的 Codex 链接。历史列表保留已采用的会议最终消息和目标，业务历史列表只读取服务采用的消息、标题与状态；原生搜索和单次运行过程详情在数据库快照关闭后按需读取。质量报告的信息性原生证据覆盖数量仅在当前内存报告/页面中展示，不写入小时状态文件，也不改变质量状态与违规规则。

Workbench 的实时文字和工具正文仅在运行时 RAM 中使用；SQLite 事件行只保存服务回放 ID、顺序和最小原生定位。工具开始与完成通过原生完成顺序定位，并按原生 item ID 配对输入与结果；只有完成记录时直接读取该条原生记录。历史文字以原生完成消息聚合回放。任务结束后，在事务提交后释放正文缓存；失败与停止运行也按准确范围读取已有原生信息。最终文字不保存副本，详情按需读取 Codex/Claude 原生记录或 Friday Artifact；远端 Artifact 的读取在 SQLite 事务外进行。原生记录缺失时明确显示过程不可用，保留实际任务状态和服务附件记录。运行提供者的原始错误正文也不另存；服务保留失败状态、错误码和服务生成的公共原因，历史错误正文清除后由错误码生成展示原因。

定时配置正文存于 `scheduled_task_config_versions`，触发记录以 `snapshot_id` 引用不可变版本。数据库迁移保留运行 ID、状态和已排队输入。

模型目录在无训练进程活动时清理，保留当前 online 模型、实际 active／previous manifest 引用的可运行制品和跨模型系列最新一份待评估候选；历史 mode transition 仅保留切换事实及回执，不因曾被选用而保留旧权重。没有 active／previous manifest 时只保留当前模型和最新候选；其他旧制品及遗留临时文件删除。业务状态、最终结果、执行回执与小型评估记录保留。数据库删除旧正文后需要执行存储维护并压缩页才能释放文件空间。

描述优化提案保持产生它时的快照身份与引用证据不变。若提案尚未评估而源快照已被最新数据替代，提案一次性转为 `unavailable`，原因 `description_proposal_source_unavailable`；不反复启动失败的评估，也不把旧证据套到新数据上。下一次基于最新数据的训练可产生新的提案。

Status API 与前端 SystemHealth 解码器均声明 native_delivery_coverage（checked、unavailable）：允许省略或 null；存在时只接受这两个非负整数，仍拒绝未知字段。该字段仅实时计算，不写入质量快照，也不改变质量违规判断。

已完成的邮件分类任务不长期保存分类输入正文：`email_agent_classification_tasks.status=done` 在既有租约／代际校验的完成事务内，将 `input_json` 改为明确标记的来源记录，保留原始 JSON 摘要和字节数、去除 `scheduled_consumer` 后的不可变输入摘要、稳定邮件身份、provider locator、配置版本及脱敏退订候选引用。定时配置与 Skill 只保留运行／版本／摘要引用；模型输入、邮件正文和冻结 Skill 正文不再复制。重复发现以既有不可变输入语义核对摘要，不补回正文；内容变化仍按既有错误处理。pending／running／failed 保留完整输入以便恢复，分类结果、任务状态和执行回执保持原样。历史精简由显式维护命令执行，不在初始化时扫描。该历史分期保留了与邮件原文不同的模型特征文本。最终结构中的 `emails.class_model_text` 仅在活动或原文尚未证明时保留；终态且完整字节已由原生／provider 证明后，改用已有 `message_source_reference_json.model_input` 引用。人工相似案例仍读取准确特征，具体生命周期以下文最终合同为准。

存储 schema 升级迁移业务对象映射时，只插入新键、删除已不存在的键或更新变化的任务绑定；绑定未变的 `business_object_tasks` 原行及创建／更新时间保持不变，最新任务与 OA 别名选择规则不变。

精简后的邮件来源记录同时保留业务分类 `category` 和候选来源 `action_parameters.candidate_source`，确保已完成 Attempt 详情的业务显示不变；其他参数正文不复制。日历卡片修订仍只比较呈现正文（精简后使用原文摘要），定时扫描运行 ID 的变化不会重新打开已完成任务。

## Email provider identity lookup storage

Email provider 当前库存以 `(account_id, stable_message_identity)` 主键定位，
按账户/文件夹使用独立 `idx_email_provider_observations_folder` 索引。
Email schema 48 移除覆盖同一身份键的 `observed_at` lookup 索引，
provider 状态、分类、刷新和外部动作语义不变。主键和文件夹索引仍保留。
这是已发布 47→48 的历史结构；未发布的 Store20/Email52 候选改用单表
`provider_state` 的 observation 身份、folder generation 身份及 observation
folder 三个 partial 索引，不再使用上述两张旧表。
当前未发布的 31 表候选把 observation 的 `provider_folder_name`、`category_key`、
`important`、`important_signals_json` 存为四个原生可写列，保留原有亲和性和三条
partial 索引；folder generation 的这四列为 NULL。不再为这四个值写入重复的
`payload_json`。正向迁移和逆迁仍比较全部来源值及 SQLite 存储类型。

## Reversible database schema release

正式 deploy 在 quiet/停止服务及一致备份之后，先捕获本次 Store 或 Email schema 的回退
元信息，再在停止窗口执行数据库迁移，随后校验 imports、启动及 health。
已验证的 47→48 provider 索引逆迁恢复原索引 SQL，移除新增版本48行；
48→49 移除分类的重复 action_plan_json，回退从当前不可变 plan 重建旧列并移除
版本49行。回退捕获的原版本历史及旧序列化形式保持准确；不覆盖数据库文件或业务记录。
启动失败或 health/加载验证失败时，先停止可能已启动的 replacement、回退可选发布资产与 schema，
再恢复旧代码并启动。schema 逆迁失败时保持 replacement 停止并记录
needs_manual，不将旧代码启动在不兼容数据库上。其他 schema 变化需要
各自验证的逆迁，不能自动套用本次索引回退。

Store `2026-10-10.2→2026-10-11.1` 移除 reply_tasks 的四个正文／provenance 缓存列，
增加指向准确输入的 current／claimed 外键。迁移只选择完全匹配的现有输入，无法匹配时
从实际旧投影追加准确快照，保留旧输入 ID、来源、版本水位、业务结果和执行回执。
历史终态不推测领取输入；旧 processing 领取必须先达到正式 quiet 条件。
逆迁从迁移后的当前 canonical 输入重建捕获的原表声明、索引、序列及四列内容，
包括旧格式所需的精简 JSON，保留 replacement 新增记录和回执；不覆盖旧备份。
未验证的 Store／Email 同时版本变化不能合并发布。Email 本期保持 schema49。

## Canonical email action-plan reference

Email schema49 删除 email_classifications.action_plan_json。分类只保留现有
current_action_plan_id，当前计划及其授权、动作参数从 email_action_plans 读取；
列表在同一连接内批量读取计划，详情、反馈重放、修订及退订修复也使用此唯一来源。
48→49 在现有初始化事务内先验证旧缓存与计划完全一致，再删除列；当前读写不再维护副本。
分类归属、连续计划版本、最高版本指针、分类字段与授权约束继续验证。
49→48 回退只重建缓存列，保留迁移后新增的分类、计划修订、动作和执行回执。

Attention 的失败回复任务查询先从 `reply_tasks` 状态覆盖索引筛选 ID，再按主键读取父任务及其 current 输入；筛选阶段不读取正文或连接输入表。现有失败条件、排序、限制和业务展示保持不变。


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

### Task 原生 Skill 目录

Task 的 command factory 将当前 `ceo-work-tracking` 选择传到既有 Codex adapter，使用 CLI 原生 `skills.config` 限制目录。目录只启用明确选择的 Skill 和既有默认 Memory 插件，不再从正文的名称提及或 `dws` 命令推断递归依赖；相关依赖继续由 Agent 按需读取。未选中 Skill 的文件和既有读取权限不变。Consumer/Audit 使用空选择时，生成的配置与变更前逐字符相同。Task 的动态事实、输出 schema、业务校验、session 与路线选择保持原有行为；本机 Codex/Claude CLI 以绝对路径和版本摘要按需读取当前 Work Tracking Skill，远端 Friday Runtime 继续收到完整正文。Claude/Friday 的单消息装配不受该 Codex 配置影响。Codex 自带系统 Skill 仍由 CLI 决定，不将目录配置数量冒充实际显示数量。目录验证见 `docs/task-native-skill-catalog-validation.md`。

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

weekly analysis job的11个原字段、running/completed/failed CHECK和week_end/manager/source_digest自然键保持。cache hit与cache miss复用同一公共job ID，owner/expiry CAS及superseded错误和时间保持；runtime workload包含原公共job ID。周报全局lease及last_success/last_attempt仍在service_state。逆迁保留原序列水位和replacement新写入/删除后的分配水位。当前代码处于隔离分支；完整B验收与生产状态以验收文档为准。


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
增长不扩大其候选扫描。会议Memory入队的missing候选、主键连接和事务复核使用
现有物理任务ID；Actions 中的 meeting_memory_write_events 类型仍保存物理父FK，对外还原公共ID，
已入队记录正文不参与候选检查。状态、排序、limit、去重与竞争复核规则不变。

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


### Full document native output delivery

Consumer/Audit Codex commands use the existing per-tool native output budget of30,000 for `agent_cli.read_dingtalk_document_full`, on cold and continued turns. The server's complete result can otherwise be clipped in the native model-visible output. This preserves the selected JSONML/revision result without substituting Markdown, changing all-tool budgets or adding prompt rules. The source may still exceed that finite budget; server complete=true alone is not proof of complete model visibility. Regression/native evidence is recorded in `docs/document-native-output-validation.md`.

Consumer 通用 Skill 约定只规定独立选择并应用适用 Skill；每轮 Task 发现入口统一决定何时读取，避免通用层的读取措辞与冻结来源复用条件冲突。安装来源和未选择 Skill 的既有读取入口不变，不新增缓存或读取回执。


Consumer/Audit 的原生 `read_dingtalk_messages` 单工具输出限额为30000，用于完整交付已请求的消息字段；该设置冷启动和续会话均保留。不提高全局输出限额，不改变窗口工具、正文或分流/审核规则；长目录与文件读取保持各自现有单工具设置。

读取单聊时，`read_dingtalk_messages` 可接收触发消息已有的对方 `openDingTalkId`，并把它作为 DWS 单聊目标；提供此身份本身即选择单聊路径，不依赖另传 `single_chat=true`。会话 ID 仍用于标识原会话。若显式选择单聊却没有对方身份或可唯一搜索的标题，工具直接报缺少单聊目标，不以空标题查询通讯录。群聊读取方式及审核判断不变。

`read_dingtalk_messages` 返回的单条消息如果在顶层字段与 `raw_payload` 的直接字符串字段中含有完全相同的长值，服务在工具结果中保留顶层值、原始字段的位置及键名，以 `null` 占位并附 `raw_payload_field_refs`（原始字段名 → 同条消息的顶层字段名）。仅当按该工具实际的 FastMCP 缩进 JSON 输出格式、放在 `messages` 数组中比较仍更短时才这样表示；按引用展开可逐字段、按原顺序还原原始 `raw_payload`。未匹配的原始字段、消息顺序和数量、查询及工具错误不变；其他钉钉读取工具不使用此投影。

`read_dingtalk_sheet_range` 对同一次成功返回中的重复 `cellStyles` 做无损共享：单元格原位置的样式字段改为从零开始的 `cellStyleRef`，顶层 `sharedCellStyles` 按首次出现顺序保存完整样式对象。仅当实际 FastMCP 缩进 JSON 结果更短时才返回这种表示；按引用展开可还原全部行列、值、样式和键顺序。短结果、失败回执、DWS 查询范围及其他工具不变。该投影降低单个 MCP 文本块超过 1 MiB 后被截断的风险，但不保证任意大小的表格都能完整进入模型上下文；验证记录见 `docs/trajectory-optimization-validation.md`。

OA pending scans carry the complete scheduled Consumer wrapper, including exact frozen Skill materials, into the claimed reply input. Consumer and Audit read those original bodies through read_task_skill; the scanner must not use the Task extraction projection that omits skill_materials. Task extraction sources retain their existing body-free scheduled context. This changes input delivery only, not Skill contents, task classification, audit policy, routes or sessions; old inputs are not rebuilt.

OA pending scan query bounds are normalized to `Asia/Shanghai` before DWS applies its start/end calendar-date filters. This keeps host-local midnight from excluding approvals created on the next DingTalk calendar day; lookback duration, pagination and current-user task verification stay unchanged. Review deduplication includes a digest of the selected Skill bodies, so changing an OA business rule creates one fresh review round for still-pending nodes even when provider records have not changed. An unchanged Skill snapshot remains deduplicated. If a review for that approval is still pending or processing, a changed Skill revision waits until that round settles before enqueueing the replacement review, preventing concurrent OA decisions. For org-structure approvals, applicant-correctable missing facts with a valid OA return path use a formal return; comments are reserved for missing return paths or genuine external-party waits. No document format is added as a requirement.

Each newly enqueued OA review round re-materializes the currently selected Skill references from the current scheduled-task configuration. For a selected managed Skill, it resolves the newest registered revision directly from the managed Skill store, including a revision absent from the worker's immutable startup snapshot (`managed_revision_not_loaded`); it does not fall back to an older loaded body. Disabled or otherwise unavailable latest revisions stop the scan instead of silently choosing older guidance. Operation Skills are read from their current authorized source. The resulting complete bodies are frozen in that new trigger's scheduled Consumer wrapper. Retries and corrections of an existing round continue to use that round's saved input, and previously saved trigger/run history is never rewritten. This changes which Skill revision a new OA review round receives; it does not change approval permissions, business thresholds, or action gates.

Manual OA rerun compares the refreshed trigger payload with the current task input. Identical input keeps the existing pending generation idempotently. When the selected Skill payload changed, rerun rotates to a new generation; an active Agent run makes that rotation unavailable and the request returns a conflict instead of claiming it was queued. The generation check and rotation are committed together, and prior inputs and runs remain immutable.

Supplementing a reviewed OA `needs_human` candidate also starts a new reviewed generation. Before queueing it, the service refreshes the currently selected OA Skills from the scheduled task, exactly as for a manual rerun; it must not silently reuse the old candidate's frozen business-rule snapshot. If the selected current Skill or task configuration cannot be resolved, the supplement is rejected without queueing. The prior candidate, review, and evidence remain immutable; the new generation still goes through Consumer, Audit, and the existing System action gates.

For organization-change approvals, the people Skill treats missing business rationale and affected-work details as facts to clarify with a verified submitter or responsible party while the OA remains pending. Derek's ordinary final approval node alone does not turn that clarification into a reserved `needs_human` decision; only an explicit discretionary choice outside the normal approval decision or a genuine policy/authority gap uses that outcome.
For the same org-structure fact gap, order DWS approval/return operations and source-verified evidence by their source timestamps. Only readable material submitted after the latest verified return counts as newly submitted post-return evidence; an earlier item does not become new because an approver refers to it later. A responsible approver’s renewed `AGREE` is that approver’s decision and must be assessed under the ordinary criteria, with the approval chain and statements in the OA treated as evidence; do not require a separate confirmation record, repeat the same return, or reject solely because no newer material was submitted. If a concrete contradiction that could change the decision remains after the third review, keep the task pending and use the CEO-2 separate-reporting path instead of another return/comment or an automatic rejection by count. Do not repeat the same request to the submitter unless new post-return evidence reveals a distinct, applicant-controllable gap. Reviewer-side read/OCR failures are not applicant-resolvable gaps. See the people-review Skill for the case review criteria.

OA Attention keeps process instances present in the latest successful pending scan visible even after the local Consumer task has completed a comment or return action. Its fallback detail states only that the latest scan still sees the DingTalk process pending; it does not infer that no return receipt exists. History/System Execution and live DWS readback remain the sources for action receipt and active-node detail.

The scheduled OA Consumer prompt names the available operation and business Skills and directs the Agent to use their rules and the generic OA action table. Business thresholds and case-routing policy live in the selected Skills. Startup migrates only the exact known stale OA prompt; later administrator edits remain untouched.

Service Consumer/Audit turns perform DingTalk reads through their declared `mcp__agent_cli` MCP tools. Material command strings are reference hints for interactive sessions; these service roles do not invoke local `dws` commands through shell. OA instance/task identifiers passed to read tools come verbatim from the current pending/detail fields; parameter or response-shape errors trigger source-value verification before a safe same-target read retry, and are not treated as missing applicant evidence. Missing native read capabilities remain explicit dependency failures; the runtime does not broaden shell sandbox access to DWS credential files. Document search exposes its existing page-size limit of 1–20 in the native tool schema and retains the same service-side guard.

For the bound DingTalk direct-chat task, the message readers take the counterparty's stable openDingTalkId from the exact claimed trigger when the Agent supplies only its conversation ID. They verify the trigger message and conversation identities before reading; a mismatched or compacted source fails rather than guessing from the contact name. `read_dingtalk_messages_in_window` uses the existing complete, bounded direct-chat window reader for that task, while unrelated group reads retain their command and response. This repairs Audit source access without changing candidate review or outbound execution.


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

### Calendar organizer source projection

DwsCalendarEvent exposes the existing organizer display string and an additional
organizer_details object containing the exact provider organizer object. Event
and calendar-window readers, and existing serialized invitation/conflict inputs,
retain userId, openDingTalkId and other source fields without turning a display
name into an identifier. A source that supplies only a string has empty details.
This is source delivery, not an identity-verification result; existing recipient,
Audit, routing and execution contracts continue to apply.

### DingTalk message-list response contract

DingTalk message-list readers require a structured object response. JSON `null`,
a non-object legacy result envelope, or a missing `messages` array in the
canonical `im.message-list.v1` contract is a typed provider-contract failure,
never an empty-message success. Unread reads do not
retry an invalid `null` response through the recent-message fallback; valid but
unrecognized object envelopes retain the existing fallback behavior. Read-only
message commands make only the configured bounded retry for JSON `null`; if it
persists, the typed failure remains visible to normal recovery and Attention.

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


### 原始触发、当前来源和立项财务口径（2026-10-14）

钉钉 Task 保存原始触发快照和固定回复目标，近期会话是另一次来源读取；同一 message_id 的审批卡片可更新，Task 标明两者来源及正文是否一致。近期已核实的处理结果可证明原请求已解决，后续新请求仍是独立任务。时间事实提供 UTC 及相对原触发的秒数；来源时间不可解析时不推断顺序。Audit 的 source_bindings 由服务在 Consumer 输出后捕获，反馈应修改 Consumer 自己的候选，不能要求 Consumer 重写原始来源快照。

立项财务 Skill 按本次项目范围、期间和综合口径判断毛利；历史试做行不自动代表当前 POC 或第一曲线项目。先核对成本是否已包含，避免重复计入。实际适用的第一曲线项目综合毛利低于 25% 时，原有特批路径继续适用。修订通过新的托管 Skill revision 发布；历史任务冻结输入不被覆盖。


会议工作人格来源引用（2026-10-14）：Meeting Alignment 每轮只取得一次实际注入的工作人格正文，来源引用为该正文的 `work_profile:sha256:<digest>`；历史来源按该引用回写，完整原生输入可重算摘要。会议结果不再被提示词要求回写本地文件路径。现有 codec 的本地路径和凭据限制不变。


已有外部动作的核验（2026-10-14）：System Executor 对已有 dispatched/uncertain journal 先调用具名处理器只读 reconcile；旧动作造成的来源变化不能阻止读取实际回执。该路径不再次 dispatch。所有尚未派发的新动作仍先检查原审批候选绑定的当前来源，已完成动作的身份和正文冲突检查继续执行。原生发送回执只从明确的发送返回结构读取 send-task 身份，引用的入站消息 ID 不能作为投递凭证。回复任务若已明确压缩触发消息输入，只能通过保留的原消息 ID 从 DWS 精确回读，并逐项核对会话、消息 ID、发送人和时间后再继续投递核验；不能把溯源包装解析错误误记为执行失败或转入重新派发。


正式部署的停止完成条件（2026-10-14）：在 bootout 前读取本次 launchd 服务的 supervisor 及其直接服务子进程 PID；停止等待同时要求标签已卸载和这些已知进程已退出，再进入备份、代码切换和数据库发布。沿用现有停止超时，不扫描或清理无关 CLI，也不增加审批步骤。


## Claude native attempt coordinates

Claude attempt start/end coordinates use the native Claude JSONL line counter, including resumed, completed, cancelled and interrupted turns. Streamed Claude references use claude_session; stdout event counts are not native transcript coordinates. Codex/Friday bookkeeping and task/session/routing policies are unchanged. Historical incorrect ranges are not rewritten or reconstructed. Original anonymous native evidence and current-main regression results are documented in docs/native-input-evidence-release-validation.md.


## Complete inline schema formatting

Claude/Friday single-message bridges serialize the complete output schema as compact JSON. Parsed content, descriptions and whitespace within string values are preserved; only JSON formatting is removed. Codex retains its native file channel. Schema occurrence count, validation, facts, routing and sessions are unchanged. Verification is documented in docs/native-input-evidence-release-validation.md.


## Task citation mismatch diagnostics

Existing current-Project citation validation reports both the expected immutable WorkItem source_ref and the received reference when they differ. The existing repair path receives those known values. Validation conditions, result contract, repair budget, routing and permissions remain unchanged; no additional prompt rule is added. Verification is documented in docs/native-input-evidence-release-validation.md.


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
Consumer and Audit both persist a new Codex session selected by the existing runtime retry router within the active run. After an idle timeout, a fresh Audit attempt may replace that run's current session just as Consumer does; each runtime attempt retains its original session, transcript slice and failure. Audit never writes the Consumer conversation session slot. This repairs session persistence only and does not change route selection, retry budgets, candidate binding or external execution.

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


Current database construction (Store2026-10-21.1/Email53): blank Store/Email construction installs the ten core tables, the Email ledger and the three Action Entity tables directly. Cold opens require the exact current declaration. Missing auxiliaries are errors; startup never repairs schema or backfills OA metadata. Historical source factories serve explicit migration/inverse tools only. Publication requires production-copy typed-fact comparison, current-data inverse, old-code cold read, and reforward proof; evidence is recorded in runtime-current-schema-validation.md.

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
the existing ranking and snippets; missing or changed sources remain explicit.
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

## Consumer purpose 动作输出和权限重试

Consumer 的业务核心输出统一为 `action/reason/target/payload/evidence_refs`；`evidence_refs` 对每个结果都是必填字段，无可用引用时返回空数组，权限重试必须列出验证访问请求的引用，评分、阶段绑定和长期记忆保留为元数据。服务在推理前选择 purpose，生成动作集合及严格的 target/payload 分支；普通 purpose 支持 typed plan、reply/react 等动作，OA purpose 支持 approve/reject/return/comment。`return` 明确映射到真实 OA `revert_task`。自然语言 reason 不用于分流。多动作 plan 保留既有完整提案结构。新输出先规范化为现有 ConsumerAgentResult，再绑定来源、计算摘要并交 Audit；历史候选保持原样。Consumer 的 purpose 输出另存于候选中，以保留动作来源和回执关联。

OA `request_access` 是 Consumer purpose 直接发起的只读权限请求，不是审批结果 proposal，不进入 Audit。每个权限动作必须带准确 `process_instance_id` 和 `file_id`，经现有 OA 评论执行器向责任人留言；同一任务同一文件去重，不同文件可各自申请。执行状态在既有 purpose 等待状态中持久化，重启时仅核对原请求回执，不能盲目重复留言。请求阶段必须停止当前材料审核；成功送达只表示权限申请已发出，不表示权限已恢复。Consumer 的普通 `retry` 也不进入 Audit、不产生外部写操作；它必须引用本任务已核验的权限申请回执，服务在共享预算内安排延后重读。匹配前只剥离一个已知 `receipt:` 或 `prior_receipt_id=` 标记，再与存储的原始 key 精确比较。迁移前没有 `purpose_output` 的旧 OA 候选，仅当它本身唯一的评论提案和已核验回执具有相同的 `material-read-access-block` action identity 时兼容为权限请求；普通评论、未经核验的请求、其他任务的回执仍不满足条件。OA approve、reject、return、comment 以及普通业务 proposal 仍由 Audit 审核。所有文件共享按业务对象和 purpose 绑定的同一预算，在既有 service_state 中用短事务持久化，不因 session、revision、attempt 退款或进程重启重置，也不能换回执清零。

原权限请求不计入重读次数；之后最多启动 3 次 Consumer 重读，固定每 12 小时一次，并保留从已验证动作 dispatch 时刻起算的 72 小时硬截止。先达到次数或截止即停止。每次重读前在既有 service_state 短事务预留次数；重复检查同一预约不递增，进程崩溃后已预留的次数保留。旧版本已排队的时间在首次读取时更新为 12 小时间隔，原 deadline、计数和 reservation 不重置。OA 权限预算耗尽或权限申请明确失败时，Consumer 仅再运行一轮提出普通 OA return；该轮不得再读、重试或申请被阻塞文件，候选须经 Audit、执行器和节点读回。不能形成有效 return 候选时才转人工处理。非 OA purpose 的权限等待仍记 `consumer_purpose_retry_exhausted` / `permission_blocked`。解析、OCR 等审阅端失败仍不能归责申请人。

Purpose 等待在业务动作成功完成或经审核 no_action 后关闭活动调度；原次数和期限保留，显式重新等待同一回执也不清零。恢复旧候选时省略不存在的 purpose_output 元数据，保持原候选字节及摘要。字段、动作表、JSON 示例和计数边界见 [Consumer purpose 输出与权限重试](consumer-purpose-output.md)；发布证据见 [验证记录](consumer-purpose-actions-validation.md)。


## Dashboard console (2026-10-20)

Pending scheduled triggers display the immutable run snapshot's task name. A claimed
trigger is identified as the scheduler dispatching a built-in command or handing
off a task, without presenting its configured runtime as an executing Agent/model.
Elapsed time starts at the persisted first dispatcher claim, and last update uses
the matching claim's renewal clock; enqueue time is not substituted for a start.
The execution column says 本次触发: dispatcher lease generations are not Agent retry
counts. Dispatched child tasks keep their own existing execution metadata.


The /history workspace and navigation label are Dashboard. A single Service row
precedes Failed / Active tasks / Queued tabs; there is no visible page/date title.
Failed reuses the existing Attention API unchanged, including current unresolved
OA processes and persisted business choices; original states remain visible. It
counts problems, while Active/Queued count execution tasks, so counts are not
additive. First successful Attention read selects Failed when nonempty, otherwise
Active; manual selection survives refresh. Attention read failure is not zero.

The read-only /api/console/dashboard/queues endpoint provides current Task,
WeChat Memory import, Memory/Todo outbox, email-action and scheduled-dispatch metadata, with separate
counts and paginated rows. Task/Run generation, business-object identity and
existing lease facts determine the current execution view; expired Task leases
are not presented as running. Native runtime metadata adds Agent role/model and
start/update times without loading trajectories. When a deployed `runs` schema
does not expose the optional summary-input link, Dashboard omits only that link
and still returns queue rows and other available runtime metadata. Email provider operations use
their existing current-plan processing state and native retry predicate. Unknown
attempt counts remain unknown; first execution is zero retries, a queued item
with two prior executions is about to retry twice, and a running third execution
is in its second retry. Audit proposal revisions are separate. Existing queue
eligibility/recovery/Attention rules and all provider actions are unchanged.

Execution trends and searchable, paginated History remain below the queue.
Dashboard polls while visible; per-request cancellation prevents stale tab/page
responses from replacing newer reads. Service/queue errors remain explicit.


## Deployment execution drain (2026-10-20)

Ordinary deployment and restart first stop admitting new execution claims, then
wait for already admitted work to finish before the existing stop/backup/update/
build/verification/restart/health sequence. Queued work, delayed retries and new
messages remain persisted; the entire pending queue need not become empty.

Two stable database-adjacent POSIX flock files provide admission and drain.
Claims take `.deployment-admission.lock` nonblocking, acquire a shared
`.deployment-drain.lock`, then release admission before their existing SQLite
transaction. The shared drain lock spans commit. Deployment closes admission
first, waits for already admitted transactions with the exclusive drain lock,
and keeps both through health and rollback. Later claims return an empty poll,
so continuous new claim traffic cannot prolong that wait. This covers dispatcher,
direct email reply/classification/provider actions, Workbench and meeting
delivery/Memory initial claims. Renewal, results, source intake and ordinary
recovery continue. Descriptor closure releases admission on success, timeout,
failure, cancellation or deploy-process exit; there is no persistent pause flag
or task-state rewrite. The existing timeout still aborts a deployment that cannot
drain. This does not force hung work to finish.

In-flight checks include claimed email classifiers, Workbench turns and weekly
OKR jobs before native runtime creation, direct email reply claims and provider operations in processing,
and claimed meeting Memory writes, in addition to the existing runtime and
dispatcher ownership facts. Retry policy, external-action rules and task
lifecycle are unchanged. The first release installs the claim hooks through
the existing quiet deployment path; older processes cannot honor the new lock.

Deployment's runtime count excludes an unleased historical runtime when its
parent AgentRun generation has been explicitly superseded by the current Task
generation. A runtime with a valid independent native lease still counts, as do
current-generation and unbound runtimes. This is a passive read: historical Run
records and recovery state are unchanged.

Admission/drain flock acquisition is also bounded by30minutes. A stalled
already-admitted claim transaction therefore cannot hold admission indefinitely
before the ordinary in-flight quiet timer starts. Timeout closes both descriptors
and leaves the Service and pending work untouched; the existing in-flight wait
retains its own30-minute timeout after the claim boundary is acquired.

### 导航栏服务更新（2026-10-20）

默认每5分钟检查 Git 上游（可用现有环境配置覆盖）；导航栏在发现未部署提交时显示“更新服务 · N”。打开后重新检查仓库，以当前版本和上游版本的完整 SHA 固定待更新范围。单个提交显示原始说明；多个提交将范围内所有提交的标题、正文交给服务配置的原生 Codex Agent，总结为不超过500字的中文说明，并保留可展开的完整提交列表。成功摘要按两个 SHA 缓存；生成失败展示真实错误和原提交，允许重试。

确认后沿用现有仓库预约，启动独立更新进程，调用正式 `app.deploy` 安装弹窗确认的精确提交，网页与部署使用同一个 operation ID。正常部署暂停领取新执行任务，等待在途任务排空；新消息、Queued 和延迟 retry 保留，恢复后继续处理。原有备份、生产目录保护、构建、重启、健康检查和回滚仍由正式部署负责。重复提交相同预约不会再次启动进程。

更新状态读取原子仓库状态及现有部署记录，不等待发布期间持有的仓库互斥锁。页面空闲每60秒读取状态、更新中每3秒读取；重启断连显示重连，只有成功回执、目标 SHA、已安装 SHA 和实际 HEAD 一致时自动刷新。失败、回滚及需人工处理展示实际原因，结束后释放本次预约。

首次安装排空锁的边界（2026-10-20）：只更新部署CLI不能暂停尚未实现领取锁的旧Worker。旧版本没有暂停入口时，首次受控切换需使用已显式授权的原有`--skip-quiet-wait`入口，经正式停止、备份和发布，再由新Worker执行既有重启恢复；这会中断旧turn，不等同于旧turn自然排空。安装完成后，后续普通部署使用admission/drain锁。生产源码差异必须先保全提交并按正式流程协调，不能绕过dirty检查。验证记录见`docs/first-deployment-bootstrap-validation.md`。

Failed / Attention cards expose persisted Attempt links before the error disclosure.
Reply failures use their original Attempt ID; failed Reply tasks and pending OA
resolve the latest Attempt through the task's current execution generation and
AgentRun identity. Meetings use their latest Meeting Attempt ID. A Task ID is
never substituted for an Attempt ID; a record without a persisted Attempt says
so in its expanded details. Membership, terminal states and retry policy do not change.


### Work Summary retry budget

Work Summary processing allows three retries after the initial call (four
attempts total). A
shared Task Agent session already owned by another worker is a scheduling wait:
the input returns to pending with a delayed available_at and refunds only the
current claim, just like the existing provider-capacity deferral. Actual failures
continue to consume attempts. An exception escaping the dispatch handler before
an Agent run is created now uses the same fenced, delayed failure transition as
Reply dispatch; the fourth failure ends failed and retains its error.

The Work Summary dispatcher closes an already exhausted pending or expired
processing input as failed with work_summary_retry_exhausted before admitting
another handler, even if its old available_at is in the future. The historical accumulated count is not cleared by this closure
(the closing claim itself is still counted); original runs and source content
remain available. Explicit retry-after-root-cause-fix resets the input's current
attempt budget, while preserving its existing Task Agent run history. Normal
provider-outage deferral, Audit behavior and external-action identity/recovery
are unchanged. Regression and release evidence is in
`docs/work-summary-retry-budget-validation.md`.


### Email classification retry budget

Dedicated Email classification tasks allow at most three retries after the
initial call (four processing attempts total) per explicit retry. A retryable
failure returns to pending with the existing capped backoff only when its
exception identifies a transient transport failure (such as DNS resolution,
connection reset/refusal, timeout, or a runtime error explicitly marked
retryable by the router). Authentication, TLS, configuration, malformed-input
and other logic failures end failed without retry. Retryable failures are
requeued only before the fourth attempt; the fourth failure ends failed and
retains the actual error. Expired running claims consume the
same budget and become failed once it is exhausted. Startup recovery and queue
claiming also close previously exhausted pending records, including ones with a
future available_at, without incrementing their old count or calling the mail
provider or Agent. Their source input, stable identity and original error remain
available. Active unexpired claims and done tasks are not closed by this sweep.
The existing explicit selected-task repair resets attempts and keeps task
identity; discovery does not automatically reopen failed records. See
`docs/email-classification-retry-budget-validation.md` for evidence.


### Classification source-read failure repair

Owner-labelled context excludes only provider-only model inputs whose current
provider observation explicitly says unavailable. Inline and native originals
remain eligible regardless of mailbox observation; unknown provider observations
still use the existing exact read. Labels and source references are retained.
The example stamp follows this eligible set, so an observation becoming available
restores that example. Similarity scoring and exact-original hash checks are
unchanged. One known-unavailable historical example no longer aborts every new
classification before the Agent runs.

The shared account connector passes the same 90-second read timeout as the
readonly adapter, rather than inheriting the deterministic provider constructor's
20-second default. Classification owner-example reads use the existing shared account connector
through the supplied source reader, avoiding a separate connection registry.
Account credential selection is unchanged. FETCH framing reconstructs IMAP literals by their declared byte lengths;
BODYSTRUCTURE literal filename values are decoded as data, and single-message UID
validation shares the existing complete-record parser. Literal body bytes cannot
supply a UID attribute. This supports real multipart filename literals and UID
attributes after a literal without changing account, generation or message
identity checks. See `docs/email-classification-source-repair-validation.md`.

### Task/Run entity Phase 2 candidate (2026-10-22)

Task1 has a locally validated, unactivated schema contract assembled from target Task/Run/adoption/receipt owners and the individual current native creators for unchanged owners. `run_projections` remains the professional Run projection owner, distinct from `run_adopted_results`; receipt execution references `run_execution_progress`. The assembly preserves Task-kind immutability, translates retained owner guards, and maps OKR item parents to the real Task owner. This Task1 schema completion does not publish Phase 2 runtime readers or writers, perform a migration, or deploy. Production figures and release identifiers in the adjacent Phase 1 record are dated observations; refresh current runtime state before relying on them.

Retained Feedback decision-item Actions reference native decision IDs, feedback keys and round IDs through their typed domain-table foreign keys. Native creation requires no Task/Run migration identity-map rows; the source Task-discriminator guards are excluded from the unactivated target assembly. This changes only the candidate schema contract and does not activate Feedback runtime callers. The target `feedback_processing_items.batch_id` remains required TEXT and keeps its batch lookup index, but has no batch-parent FK because the source has none; empty and source-valid unresolved batch values survive Task2B conversion without NULL normalization or reparenting.

The three Task parameter identity indexes for Meeting, OKR review and weekly analysis preserve the source TEXT-affinity keys. Their SQL expression decodes a tagged native BLOB parameter to a BLOB key, while an ordinary TEXT value that resembles the codec marker remains TEXT. The source writer has already applied TEXT affinity to numeric and infinity scalar inputs before migration. The expression keeps the original composite components, partial predicates, NULL behavior and duplicate rejection; no Task runtime lookup caller has cut over.

Task2B inverse metadata also records exact compressed per-kind source physical-ID ranges. This migration-only membership fact distinguishes an existing source owner from a new native owner whose reused ROWID falls below the source high-water mark; counts and high-water marks alone cannot establish that identity. The ranges carry no Task payload or lifecycle values and are not read by native runtime callers.
