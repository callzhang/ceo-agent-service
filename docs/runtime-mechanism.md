# Current Runtime Mechanism

## 数据库结构与迁移合同

当前生产基线为 Store `2026-10-21.1`／Email53，包含十张核心表、Email 迁移账本及三张 Action Entity 表，共 14 张普通表。生产运行时代码为 `87a34d299d5749af1af454bfdd01c48a936799a4`，PID 2903；健康、状态、队列、Attention 和 History 读回均为 HTTP 200。共同 Action、Receipt、Observer 分别有 15,472、18,621、506 条；本期退休的七个已填充来源类型均为零。原唯一 FK finding `runs(190042) -> tasks(1)` 保留。正常空库直接建立当前声明；运行时只接受真正空库或精确当前声明冷启动，缺表/列/索引/触发器及版本混合均在修改前拒绝，不自动补表或回填。Store-first 可暂缺 Email 账本；Email 构造器只在准确状态下建立账本与首次默认配置。验收和边界见[Phase 1 生产验收](action-entity-production-acceptance.md)。
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

Action Entity Phase 1 已部署，当前生产即使用统一 Action owner：`actions` 的 `_kind=70` 保存共同 Action，`action_receipts` 保存逐次 provider/execution 事实，`action_observers` 保存多个 Run 的观察关系，typed identity map 保留精确旧身份。创建、CAS 领取和结果更新由 `app.action_entity_storage` 提供，调用方持有事务。生产读回、准确退休清单、copy 容量结果和剩余路线见[Phase 1 生产验收](action-entity-production-acceptance.md)。这只表示第一外部 Action 单元完成，不表示全部 Actions 或数据库已统一。
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

Task3（2026-10-21）将 reviewed、direct-email、prepared-message 与 Sent 执行恢复切换到同一 Action owner。调用方保留原事务、审批、provider 核验和不确定结果恢复边界；prepared 的准备、claim、接受回执仍在各自原有提交边界。prepared Action 在真实发送调用时才记录 provider operation；prepared facts 不借用 Sent 或 provider 时钟。审查执行在每个 execution/action_index 保留一个可更新的当前观察，旧执行的观察与 provider receipt 仍独立保留。direct-email retry 复用原 Action identity 并追加各次执行观察。Sent 历史缺失的 canonical operation、target、digest 或 action identity 保持 NULL；已核验的 failed-Audit 恢复可把这些事实绑定到原 provider body receipt，不复制正文、不重发。canonical provider 结果通过 reference-only metadata receipt 直接指向当前 immutable body；普通 provider receipt 不会仅因 ID 较新而改变该绑定。复杂 unsubscribe 保留原专有流程；这次切换不改变任何审批、授权、核验或重试规则。

Task4（2026-10-21，候选）将 direct-email History、Attention、队列快照、重要邮件清单及邮件分类可观测性读取切换到 Action 与 Action Receipt。发送详情以 Action 的物理 ID、正文、准确 Sent 回执和 `observed_at` 展示；provider 正文只从该 Action 的 provider receipt 读取，缺失的 operation/target 显示为未提供，不合成来源事实。多个 Run observer 仍对应一条发送效果。撤回只更新同一 Action 的撤回字段，不新增发送或改写原发送回执；feedback token 从同一 Action 取得，并继续按其原 conversation/trigger 身份关联消息和反馈。邮件退订仍走其专有流程。

Task6B 的 Dashboard Email 队列从 `DIRECT_EMAIL_ACTION_SQL` 和当前分类投影读取 pending、processing 及按 worker 原 retry 谓词可重试的 failed Action；队列 ID 使用 common Action 物理 ID，标题、状态和空详情链接维持原 API 契约。Email Attention 的 common Action ID 以字符串输出，符合 `AttentionRow.id` 的严格响应类型；失败筛选和工作者状态投影保持现有语义。

Task5A（2026-10-21）在当前真实 prepared-message caller 与 reviewed SystemExecutor 路径中，以 `agent_message_delivery_key` 对应的 channel/delivery key，将已准备的 Action owner 绑定到已审批选项的 reviewed Action，再由同一 owner 承载 dispatch observation、canonical provider receipt、Sent receipt 和 observer。选项 key、task generation 与 proposal revision 保持在源 delivery identity 中；provider 仍只调用一次。

Task5B（2026-10-21，本地前向迁移已验证）把只读 Store19.12／Email53 源中的七个 covered families 转为共同 Action、Action Receipt、Action Observer 与 typed identity map；确定的 canonical、approved-step、delivery-key 和原 owner 引用才合并，孤立来源保留为独立 Action。退休 covered source kinds 与其 family-only DDL。两个仍在范围外的 direct-retry control rows 只把既有 `email_actions` 父引用经 typed map 改到 `actions.id`，其他 control 字段和时钟保持原值。完整 populated copy 通过 target manifest、原有单条 FK finding 多重集与 `quick_check`；对源中 72,478 条未覆盖 Action 行逐物理字段比较，唯一变化是这两个 retry row 的 parent composite-key 单元。该证明仅覆盖独立拥有的本地副本，不是 current-data inverse、old-code cold-open/reforward、发布或生产行为验收；Task5C 与独立最终审查仍是发布门槛。没有改变审批、provider 核验或 retry/reconciliation policy。
Task5C1（2026-10-21，本地候选）修复了 prior reconciliation 的 source mismatch：已有 canonical body 不同于本次 attempt 结果时，canonical metadata/body 保持不变，差异结果保留在该 exact attempt。ambiguous-to-sent correction 由 reference-only canonical metadata receipt 直接改指向新 immutable body；它保留 source 的 first operation、target、run 与 clock，current digest 从 exact body 投影。其他 candidate observation 和被动 Sent 仍引用原 body；只有本次 corrected observation 前移。已无独立引用的被替换 verification wrapper 才会被定点清理。该本地候选尚未完成 Task5C2 current-data inverse 与 old-code cold-open/reforward 验收，不是发布或生产行为验收。

Task5C2（2026-10-22，本地候选）实现了实际 current-fact inverse：按 typed identity 重建旧公开 ID 与父引用，保留旧 declaration、out-of-scope 行与 sequence 水位，复原 retry composite parent 与 ordered History 引用。独立复制的 15,267 Action 事实逆迁后通过旧版 Store19.12／Email53 Store 和 EmailStore 冷读，并用旧 Store writer 创建 harmless prepared-postfix；当前版本再迁回后，canonical、Sent body/reference、prepared receipt/dispatch clock、Email attempts、retry 与 History 仍可读，FK finding multiset 与来源一致。source 的 Actions PRAGMA 有 468 个投影列（30 stored、438 virtual）；source 与 target 有 438 个同名投影列，其中 24 个是双方共有的 stored 列。该本地副本证明尚不授权候选发布；Task5C 独立最终审查、Task6 原生读取/容量/UI 门槛与 Task7 发布门槛仍未完成。没有改变审批、provider、授权或 retry policy。

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

四个手工旧业务模型写入 CLI 已退休：`backfill-routine-process-todos`、
`backfill-todo-deadlines`、`backfill-todo-owner-ids`、`repair-task-projects-apply`。
`repair-task-projects-plan` 仍提供只读清单；旧 Project／Todo／Update 历史行和
既有回执继续读取。D1a/D2 仅退休旧入口；当前 Task Agent、Task 写入及
task-semantic-import 的来源核对与正式采用条件保持。Store19.11 的 DDL 与字段迁移
按上文执行；这些子单元不代表整个 D 已完成。
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

History 页面在事件图表和执行记录之前展示 Attention 面板；它复用独立 Attention 页面及 `/api/console/attention` 的读取、刷新、总数与详情行为，不改变 History 的记录查询或任务状态。

The main launchd job declares `ProcessType=Interactive` because the local HTTP
Console is a user-facing application dependency and does not use XPC activity
for Adaptive classification. Omitting ProcessType applies Standard CPU/I/O
limits. Existing installed plist settings must match this declaration before
the formal deploy/restart re-bootstraps the job; deployment does not copy the
entire plist over machine-specific environment settings.

Hourly quality checks reuse the unchanged credential/path predicate result for
identical runtime metadata strings within one scan. Every historical runtime
row and all seven fields remain in scope, violations are still counted per
row, and each new scan resolves the current path-prefix configuration afresh.

Reply Attempt fields are VIRTUAL generated columns over `actions.payload_json`.
The four trigger-latest quality checks first read candidate statuses through
the existing status index, then group only candidate trigger keys and resolve
each key's latest Attempt through the existing identity index. The candidate
keys drive the lookup so SQLite cannot reverse it into another history scan.
All statuses participate in the latest lookup; NULL key partitions and the
original `datetime(updated_at), id` ordering remain unchanged. The separate
OA process-identity window and all downstream quality predicates are unchanged.

Task Agent's checked-in native strict schema documents existing Task identity
fields: ordinary `update_task`/`update_fields` uses `task_id`; merge-only target
and identity fields identify two distinct persisted Tasks. New supporting Tasks
carry a registered assessment's Project selector explicitly; unresolved Project
clues may retain a null selector. Model field descriptions and
the generated schema stay synchronized. No validator or correction-budget
change accompanies this clarification. A failed uncompacted Work input may be
requeued through the existing reviewed-recovery CLI after the diagnosed cause
is repaired; old failed runs remain intact, and acceptance requires a new
terminal source/projection readback. See `docs/system-health-quality-validation.md`.

DingTalk delivery verification reads outgoing identifiers only from the native
send `result`, never referencedMessage or nested action metadata. An openTaskId
always requires a task query; only result.sendStatus=SUCCESS confirms asynchronous
delivery. When no queryable task exists, the native reply recovery path can
confirm delivery from its `dingtalk.reply-readback.v1` proof only after a
complete paginated read of the original conversation window yields one exact
outgoing message. The message must quote the original trigger, match the
authenticated sender and un-recalled state, and have the same normalized body
digest as the prepared authored text. `im.message-list.v1` parsing preserves
quoted-message identity for this comparison. The service removes only its own
feedback callback suffix when comparing the authored body; it retains the
actual provider text in the receipt. A message ID by itself, accepted UUID
duplicates, processQueryKey and generic finished statuses do not prove delivery.
Unknown, pending, incomplete or conflicting readbacks retain ambiguity. Native
reply preparation persists the accepted receipt before verification, including
unknown outcomes, and rechecks it on recovery without another send.
Before Audit, Consumer preparation persists the exact service-finalized body for each executable DingTalk chat action. Its target rules match the executor: replies require a conversation and source message, group sends require a conversation, direct sends require a stable recipient, and generic sends may target a conversation. The executor sends only when the reviewed body exactly matches that prepared body.
For a strictly complete, failure-free group-window readback, zero exact matches
is a delivery verification failure, not an unknown success and not permission to
dispatch the old reviewed candidate again. Incomplete windows or multiple exact
matches remain uncertain. Any follow-up message requires a new candidate and Audit.

Explicit historical delivery re-verification preserves original provider output,
the earlier verifier and prior execution/task state. Corrected unknown receipts
remain accepted effects requiring read-only reconciliation; they are neither
completed actions nor permission to resend. The maintenance tool never dispatches
Agents or providers, leaves original run/Attempt rows unchanged, and withdraws
only the same generation's closed task completion. Failed current tasks override
historical completion in the Console. Unknown historical delivery is a technical
verification failure, not a needs_human business choice. No maintenance runs in
read paths or startup.

For a historical native reply with an existing sent-reply row but an unavailable
provider task, `reconcile-failed-reply-chat-readback --task-id ...` performs a
bounded, paginated read of the original conversation around `sent_at`; naive
SQLite `CURRENT_TIMESTAMP` values are UTC. A
complete result must contain exactly one message matching the conversation,
the recorded query range, quoted trigger, exact persisted body (or the native
quoted-sender rendering using the persisted trigger sender and the unchanged
full body), and authenticated sender identity. The native renderer's
whitespace-only normalization is shared with the ordinary native reply
reconciler; all non-whitespace characters, mention name and body content must
match. UTC range timestamps are compared as timezone-aware instants because
the provider may canonicalize `+00:00` to `Z`; a different instant is rejected.
Arbitrary prefixes or rewritten content do not match.
The native page ledger must be complete, non-partial,
failure-free, and untruncated. The
explicit `--apply` then rechecks the failed task, generation, approved
single-action candidate, uncertain external receipt and inactive execution
inside one immediate transaction. It records the readback digest and message
identity, settles the same execution/task and compacts only that settled input.
The command has no send/comment path; previews are read-only and every mismatch
or incomplete response leaves the task unresolved.

Historical tasks from before persisted Audit candidates were introduced use a
separate evidence contract, not a reconstructed approval: the task must have no
candidate in any generation, exactly one completed legacy reply Attempt with no
permission or Audit evidence, and one completed Agent run bound to the exact
task generation. The existing sent-reply row and external receipt must match
the same business key and exact conversation/trigger target. After the same
complete native readback proves the outgoing message, the transaction records
delivery verification and closes only the task; it does not create candidate,
review, execution, or approval records and does not rewrite the historical
Attempt. Any approval trace, ambiguous identity, or mismatched target keeps the
task unresolved.

Startup reply-task projection recovery performs candidate discovery on read-only
connections and applies bounded batches in short SQLite write transactions. The
failed-current-run repair rechecks only the candidate task IDs, generations and
latest run IDs in each write batch, along with current receipt and settlement
evidence; it never reruns the full historical candidate scan under the writer
lock. Settled-trigger reconciliation also rechecks exact task generations and
current evidence inside each batch, preserving concurrent terminal updates.

OA audit metadata backfill follows the same lock boundary: it discovers and
parses missing process identities outside a write transaction, then updates at
most 100 still-unidentified attempts per short `BEGIN IMMEDIATE` batch. It must
not issue a no-op write before scanning the historical Attempt/Task join, since
the implicit transaction would hold SQLite's single writer while that scan
runs. Each update remains conditional on the Attempt's process identity still
being empty, so concurrent recovery is not overwritten.

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
的 Consumer 会话仍按原连续会话行为处理。Codex Consumer 在续用前读取原生会话开场实际保存的
Developer 中的原生输出 schema SHA-256 指纹，并与本轮 purpose 的服务 schema 比较；一致才续接。
Codex 的原生结构化 schema 通过 CLI 参数传入，不会完整保存在 Developer 文本里，因此 Developer 只记录
精简指纹；旧会话仍可通过完整 Pydantic wire schema 精确核验。指纹不匹配或缺失时，只清除该路线的会话
指针，本轮携完整事实和当前 Developer 开新 session。原生指纹后可以继续出现以 `## ` 开始的独立 Developer
段落；任意其他尾随文本仍视为格式无效，不能用来判定 session schema 兼容。
仅普通 Developer 文案或 Work Profile 变化不会触发此检查失败。Claude 每轮重新提交当前
Developer，Audit 会话规则、审批权限和动作门槛不变；旧原生会话与历史运行记录仍保留。

Consumer 遇到明确 `retryable=true`、`session_continuable=true` 的结果校验失败时，同一执行代次和
revision 的下一轮继续使用已绑定且 schema 匹配的 Codex session，并提交结果修正信息；相同无效结果
重复失败达到既有新 session 条件时仍重新开始。其他失败仍按原会话兼容性和 retry policy 处理。

Consumer 收到 Audit return/reject 后，仅当任务代次、候选与审核父链、原始完整输入指纹和精确
Codex session 均匹配，上一候选回答有原生完成记录，且 transcript 自上一 Consumer turn 后无新增内容、无 compaction 时，才
复用该 session 的源上下文，并提交本轮时间、完整 Audit feedback、必要的 runtime correction 与
完整替代候选要求。指纹绑定 Developer 指令、稳定 Task 输入、输出契约及图片来源。轨迹缺失、检查
失败、session 被其他 turn 追加或路线不匹配时，回退到完整输入。轨迹尾部行数、marker 和实际输入
解析共用同一个已解析的 Codex home，不交叉使用不同目录中的同名 session。首次精简提交若失败，
同一运行的后续重试使用完整输入；运行快照记录每次实际选用的 prompt。
Audit 独立性、结果校验、反馈次数及任务生命周期均不变。

人工重跑 OA Attempt 会创建新执行代际，并从当前选中的 OA 定时任务 Skill 重新冻结输入；若无法读取
当前所选 Skill 或对应任务配置，则不排入重跑。原 Attempt、旧代际和旧 Skill 快照保留不变。
若重刷后的完整触发输入与当前代际相同，重跑按原幂等键返回当前代际；若所选 Skill 导致输入变化，
则在单一事务中轮换到新代际。轮换前会校验 Attempt 仍绑定原预期代际；若旧代际仍有运行中的
Agent，则返回冲突，不报告已入队。旧输入、Attempt 和运行历史不改写。
对已审核 `needs_human` 候选提交补充信息时，也按新审核代际处理：入队前从当前 OA 定时任务重新冻结已选 Skill，而不是沿用该候选生成时的旧快照。当前 Skill 或任务配置不可读时拒绝入队；成功后仍经 Consumer、Audit 和原有 System 动作门禁。旧候选、审核及证据不改写。
Attempt History 仅对当前 `needs_human` 规则候选显示“需要你处理/选择处理方式”。重试耗尽、结果校验、候选绑定、Audit 或外部执行等失败继续标为 `failed`，可提供重试和技术详情，但不要求用户判断业务规则，也不以“人工处理/暂不处理”冒充规则选择。
人工重跑内部 OA 待办使用持久化任务中的 `oa-pending:` 合成触发 ID；按触发 ID 定位当前任务并恢复其实际 `oa_pending_scan:<process>` 会话。该类 ID 不是钉钉消息 ID，不调用钉钉会话读取或按 ID 读取；若当前任务已不存在，按未找到任务失败。
压缩后的输入从任务所绑定的 OA URL/业务键核对 process 与 task 身份，并通过保存的定时扫描 run 恢复 OA Consumer 上下文后重新冻结当前所选 Skill。无法精确核对身份或找回 run/Skill 时拒绝排队；不会尝试把内部触发 ID 当作钉钉消息读取。
待办扫描的复审指纹包括当前选择的 Skill 正文摘要。Skill 规则更新时，即使外部审批流水未改变，也为仍在本人待办中的流程排入一轮新 Consumer/Audit；相同 Skill 正文继续去重。新一轮仍冻结实际选中的完整 Skill 正文，并使用原有审批身份、Audit、System Executor 和外部回执门禁。
组织架构变动的业务理由或受影响工作等可补充事实，由 people-review Skill 指示向已核实的发起人/责任人留言询问并保留 OA 待办；审批人拥有普通最终审批节点本身不构成保留给个人的 `needs_human` 特殊裁量。
组织架构同一实质、可补正事实缺口的证据、DWS 同意和退回记录按来源时间戳排序。仅将有当时可读且来源核验的证据支持、指出会改变结论的具体缺口、提供可执行补正要求并给予公平机会的退回计为有效轮次；随后必须核实同一缺口仍未解决。三次有效退回后，第四次到达 Derek 授权节点时，若当前可读且来源核验的证据仍证明同一缺口未解决，应拒绝并说明缺口及三轮有效补正机会，不得只按次数拒绝。读取/OCR/解析失败、无依据或不可执行的要求、不同缺口，以及被退回后材料已补正的缺口均不计数。审批评论中的具体可核实陈述是证据，必须审阅；不要求另行确认文件。若经核验身份与职责的接收主管在退回后直接以 OA 评论明确确认汇报或交接事实，应把该陈述作为其明示事实的证据；不得仅因没有独立附件/确认单就把该事实继续视为未解决或重复退回。若仍有具体实质事实缺少支持或存在矛盾，只追问该事实。只退回未解决或新出现的实质缺口，不重复已解决请求。具体审查标准见 people-review Skill。
历史 OA 复核必须按原审批或退回时点判断：只使用能证明当时已可获得的来源版本。之后才更新的表格或文件不能反推更早内容；除非版本历史或其他带时间的来源能还原，否则更早状态记为未知，不据此认定申请人当时缺材料，也不计作有效退回。
拒绝前还须把完整 DWS `REDIRECT_PROCESS` 时间线与本地执行回执及有效轮数逐条对账。已核实的 DWS 退回不能仅因本地缺少执行回执而遗漏；每条不计入的退回都要说明记录和证据理由。总退回数与有效轮数不同时必须解释差异；无法核实完整时间线或差异时，不得声称达到拒绝阈值。

Attention 对最新成功待办扫描仍报告为活动的 OA 流程继续显示 pending；本地 Consumer 已完成评论或退回时，默认详情仅说明扫描仍见流程未终结，不推断“没有退回回执”。具体回执以 History/System Execution 与 DWS 流水读回为准。
History 可对一条已有 uncertain OA 退回执行专用只读核验：调用方必须提供当前绑定的候选和 Audit review；Attempt 必须关联该 Audit run，Attempt 已保存的 OA 身份字段（若有）也必须与候选匹配；并校验唯一既存 uncertain action journal 及 uncertain execution 后，才进入 `reconciliation_only` 执行模式。此模式只调用已注册 OA handler 的严格 DWS 回读，不调用 dispatch；通过后将匹配回执写入外部动作账本并更新同一执行代际，原始 provider response 继续保留在核验证据中。证据不完整或冲突时维持 uncertain。它不创建 Consumer/Audit 新代际，不改变审批结论，也不重放动作。
默认 OA Consumer prompt 只负责选择通用审批与业务 Skill、按实时流程分类及提示执行后读回；审批阈值、可补充事实的问询和人工升级条件由所选 Skill 定义。启动迁移只替换与已知旧版内容完全相同的系统 OA prompt，并保留后续自定义改动。

Consumer 和 Audit 的只读工具目录提供钉钉邮件邮箱列表、完整会话、单封原文与发送状态读取：
`list_dingtalk_mailboxes`、`read_dingtalk_mail_thread`、`read_dingtalk_mail_message`、
`read_dingtalk_mail_sent_status`。这些接口直接调用现有 DWS 原生只读命令，保留其完整 JSON
正文、附件、分页与状态信息及原始错误；线程使用 conversationId，单封使用 messageId，
发送状态使用 internetMessageId。它们不发送邮件、不增加审核规则，也不开放 Audit 命令执行。

Audit revision context follows the current Consumer run's persisted parent Audit
review. Only adopted return/reject feedback is carried forward, with its exact
proposal revision and candidate digest, before current facts and candidate
fields. Historical review summaries and scores are omitted. The feedback is
revision background, not source evidence or authorization; complying with it
does not establish a factual claim. Audit must correct an unsupported earlier
instruction and distinguish requested advice from an enacted rule. Initial
reviews and stages following approval have no revision-feedback section. Cold
recovery and technical retries reconstruct it from the adopted review without
native trajectory reads. Feedback budgets, candidate binding, terminal states
and external execution remain unchanged.

Published Store19 (`2026-10-13.5`) at5691f6c7 uses the sixteen-type
action layout. Readonly observation on2026-10-14 found Email51,121 physical
noninternal tables and no physical reply_attempts or memory_write_events.
Their existing public IDs, allocation waters, results and receipts use finite
projections. Store18 delivery consolidation and its inverse proof remain
historical evidence; the current E actual layout retains no Memory FK.
Existing blockers, exclusions, labels, delivery verification and recovery
behavior are unchanged.

E passed complete CI and merged inPR82. This task's first normal deploy made
no changes after the idle timeout; a separate deployment later succeeded.
The second normal invocation observed production already at5691f6c7. Exact
receipts and readonly schema checks are in
`evals/database_redesign/phase5e_publication_status.v1.json`. Quick_check is ok,
and the one original runs/task FK violation remains unchanged. Live
UI/performance acceptance is still incomplete: Status eventually rendered
PID34642 with degraded recovery health, while Status/Attention/History request
timeouts require further diagnosis. The isolated Store14.1/Email52 candidate
and its follow-up/Todo-sync conversion do not complete the final10+1 goal.

OA return uses its own effect verification, not the approval/rejection node
completion predicate. The exact pending-node ownership check still precedes a
new return. System first saves the actual response, principal, action digest and
UTC call bounds. Only an explicit successful boolean operation result is
acceptance; negative or incomplete responses cannot prove it. Read-only recovery
joins the native process identity/business ID with the existing official OA
OpenAPI process: original canceled node, unique own return event, exact approved
remark in the call interval, and destination tasks in the approved activity
before the next provider transition. Tied or untimed transitions, imprecise
dispatch-second evidence, missing destination or partial records remain
uncertain. No delay threshold or guessed recipient is used.

Persisted dispatched/uncertain OA return actions reconcile before the pre-write source
guard, because the original effect can itself change those facts. Reconciliation
cannot dispatch. New writes retain source revalidation; prior acceptance evidence
survives provider read failures. This does not retrospectively manufacture lost
acceptance responses or rewrite historical failed executions. Comment and other
action recovery retain their existing source/context fences.

An uncertain DingTalk reply tied to a reviewed OA process is skipped only after
live readback confirms both the exact process is terminal and the exact reviewed
task is terminal. The skip records process/task identity and the observed
terminal state, preserves the uncertain delivery error and original provider
receipt, and removes the task from actionable Attention without claiming the
message was sent. Nonterminal, mismatched, incomplete, or failed OA reads leave
the task unresolved. A failed OA Attempt with no owning Reply Task is closed by the same read-only scan
when the exact reviewed task is terminal and either the process is terminal or the principal's own
return canceled that task and left nothing pending for the principal (the process may still be
RUNNING at the applicant); the evidence records `OA_RETURNED`, the Attempt leaves Failed and
Attention, and nothing is sent or rewritten. When this exact closure was already recorded for a historical
Attempt, History projects the follow-up as `skipped` in both list and detail while
the raw provider/System Execution receipt remains `uncertain`; the status means
no further follow-up is actionable, not that delivery failed or succeeded.

When an approved DingTalk message send has no verified provider receipt, the
orchestrator routes a bounded follow-up revision to Consumer with explicit
receipt-acquisition feedback and the prior uncertain execution marked
`completed=false`. Consumer queries the existing `openTaskId`, or performs one
complete native read of the original conversation when no queryable send task
exists. A unique exact message readback confirms delivery; a complete,
failure-free negative readback proves non-delivery and permits a newly audited
proposal only while the source is still current. Incomplete, truncated, failed,
or ambiguous readback preserves uncertainty. The follow-up may not dispatch the
same candidate or claim success. Restart recovery recreates the same feedback
from the persisted uncertain execution before retrying a failed Consumer turn.
An Audit-approved `no_action` cannot settle the generation while that exact
message execution remains uncertain. The orchestrator carries the original
uncertain receipt through later Consumer/Audit parent revisions and asks the
Consumer to reconcile it again; after the existing three content-feedback
cycles, it stops as `failed_terminal` with `external_action_uncertain`. Exact
live terminal OA reconciliation may still settle the follow-up as `skipped`,
without changing or treating the original send as delivered.

The OA scan also handles legacy failed OA Attempts that have no active local
Reply Task bound to their trigger. It checks at most 20 distinct process/task
groups per scan, prioritizing those whose local business-object task is already
`done` or `skipped`, and reads exact persisted OA IDs through the native
read-only detail API. Only an explicitly terminal process
and that exact terminal task, followed by an in-transaction recheck that no
active local Task owns the trigger, it can close unresolved failures for that
exact process/task as `skipped` (the source detail is read once per process per
scan). A local Task already closed as `done` or `skipped` is not active.
Each resolution records the source state and preserves the original failure
reason; delivery remains unverified. Missing, partial, mismatched, nonterminal
or failed reads do not change History or enable a replay.

Audit handoff includes System-produced JSON type facts for each current action's
target and payload fields, including human-option plans. The facts come from the
exact validated candidate; they do not authorize an action or prove live
ownership. New native feedback must explicitly classify its observation and
include its field-assertion array; omission is a result error, not an implicit
business judgment. Field-based feedback supplies candidate-relative path,
literal and JSON type assertions. Before adopting the Audit result, the runner
checks every assertion against the digest-bound candidate. A missing path,
unsupported index, changed literal or wrong type corrects Audit via the existing
technical result repair, not Consumer, business routing or automatic approval.
Policy, source, authority and full-effect review remain Audit's work. Business
prose is not parsed with keywords or regex, and historical feedback is not
reconstructed as new evidence. A business misclassification or unsupported prose
is still a model-review limitation, not proven factual truth merely because an
assertion matches. Scalar field evidence is rendered before the complete final
candidate JSON; source bindings remain in that JSON without repeating their
values in the field index.

OA Attention is a business-pending projection for work Derek still owns or must
review. The scanner tracks exact Derek-owned RUNNING nodes from the authenticated
pending list and source details. The projection includes a tracked node only
when its matching current reply task has had no update for more than one hour;
recently changed pending nodes remain in their normal queue and History views.
Unmatched keys have no reliable task-progress timestamp and are not promoted to
Attention. A locally completed reply task does not remove a stale OA item while
that exact external node is still RUNNING for Derek. Conversely,
an overall RUNNING process is not Derek's Attention when the exact tracked node
is completed or assigned to another approver; the scanner removes only that
node's key and preserves the historical task/attempt. Partial scans, missing
identity, malformed ownership/status, and source-read failures retain prior
keys because they cannot prove that Derek's responsibility ended. Non-OA
pending/processing/needs_human status alone remains excluded.

Current Attention also hides an older failed OA Reply Attempt only when a
newer attempt for the same process is bound to its current Audit-approved
candidate and the current `revert_task` execution is `done` with a verified DWS
`REDIRECT_PROCESS` receipt, including the successful DingTalk response and
matching process identity. The historical failed Attempt remains unchanged in
History. Uncertain, incomplete, mismatched, or non-return evidence keeps the
old failure visible. This projection does not change task state, receipts, or
OA execution.

Reconciliation removes an item when live detail proves the process terminal,
the exact reviewed task is no longer assigned to Derek or is terminal, or when
the exact reviewed task is canceled and the principal's latest
workflow-changing operation is `REDIRECT_PROCESS`. Absence from a pending-list
page alone is not proof. Comments and unrelated transitions are not return
receipts. Historical local closures made solely for `OA_TASK_NOT_PENDING` are
rechecked without rewriting their attempts: they remain projected only while
the exact task is still Derek's live action or the source cannot be verified.
The service's DingTalk OpenAPI reader returns snake-case `process_instance`,
`operation_records`, and operation fields; reconciliation reads those native
records as well as the CLI detail projection before deciding a verified return.
When a latest local attempt was previously closed solely as
`OA_TASK_NOT_PENDING`, the scanner corrects that resolution only under an
identity-and-original-resolution CAS after live terminal/return evidence; it
retains the prior text and does not rewrite the attempt's outcome.

Source feedback callback identities are opaque historical evidence, not newly
generated outbound tokens. The validation copy removes only configured-origin
pairs with matching identities and valid query fields; authored/outgoing token,
credential, domain and signature checks are unchanged. Canonical source values
and source digests are not rewritten.

`reconcile-confirmed-meeting-delivery --job-id ...` previews an existing primary
delivery receipt using provider readback without sending. `--apply` restores
only the exact prepared body and original receipt under a transaction CAS,
then resumes the existing calendar-only tail. It requires an unchanged failed,
unlocked job, immutable same-target binding, no active dispatch claim and no
sensitive split preparation. Revised unsent prose is not presented as sent;
unconfirmed receipts, changed targets or split actions are not reconciled.

Installed Skill discovery (`agent_cli.read_skill()` without a source) returns
body-free TSV names and paths with separate Root and Relative path columns. Joining
the referenced root directory with the relative component reconstructs the
absolute source path; no combined alias expression is presented as a read argument.
The full name and reconstructed absolute read path of every authorized source are retained,
including distinct paths sharing a name. The response includes its path joining
and table quoting instructions. Named/path reads, source authorization and
ambiguity checks are unchanged; no output budget or role policy is changed.
Task discovery now offers `agent_cli.read_skill(query=...)` for an additional
Skill. It filters the installed metadata by case-insensitive topic terms that
may appear in any order across name and description, and retains complete
metadata and exact source paths for
matches; it does not return Skill bodies. Blank queries fail. The no-argument
directory lists every name and path without repeating descriptions; exact
name/path reads keep their existing behavior.

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

The existing `read_dingtalk_drive_file` attachment reader limits downloaded bytes
to 20 MiB for non-PDF files and 64 MiB for PDFs before parsing; OOXML aggregate
uncompressed bytes remain limited to 20 MiB. Scanned PDF input above the 700 KiB
inline MCP payload limit returns the original one-based OCR page list, and a
page-specific read renders only that page to a bounded JPEG for
`ai_services.ocr_document`. The exact downloaded PDF stays in the MCP process's
temporary directory and is reused for page reads; it is removed when the process
exits. Codex Consumer and Audit enable only `ai_services.ocr_document` in their
role tool list; approval configuration alone does not expose a disabled tool.
Member parsing
also uses bounded reads against one aggregate budget, with actual compressed
stream termination checked instead of trusting the ZIP directory's size alone;
only stored and deflated OOXML members are accepted. This termination check is
covered with the configured Python 3.12 runtime and CI. Downloads open
the resulting regular file once with `O_NOFOLLOW` and read from that validated
descriptor; link replacement cannot redirect the read. Every attachment format
returns a SHA-256 digest of the downloaded bytes. PDF and presentation previews
report actual text/page truncation, and the complete serialized tool response
must fit below the 1 MiB MCP text-block boundary using the same indented
`pydantic_core.to_json` serialization as the pinned FastMCP SDK, including JSON
escaping, structural whitespace and duplicated text. An oversized response fails rather than presenting partial
business evidence as complete. Textless PDF pages and supported image attachments
are read through the installed Stardust OCR client; PDF page order, source hash,
and OCR confidence remain visible. Supported raster images that exceed the
700 KiB OCR MCP input bound are re-encoded as a bounded JPEG for OCR while the
original source SHA-256 and dimensions remain in the reader result; images
already under the bound are passed through unchanged. Image OCR results include
extracted text, confidence, warnings, and source hash. OCR errors are technical read failures
rather than evidence of missing applicant material. Process-owned
temporary directory cleanup remains unchanged; failed extraction/serialization removes the
new material file. These limits do not grant shell access to DWS credentials.
OA operation-record attachments have a separate read-only entry point because their
`file_id` is not a Drive node: `read_dingtalk_oa_comment_attachment` uses the official
premium process-attachment download URL with the exact process instance and file ID
from the record, then applies the same byte bounds, extraction, OCR-page handoff,
temporary lifetime, and response-size checks. Premium entitlement or download
failure remains a technical read failure; it never proves the attachment was absent.
OA form attachments use `read_dingtalk_oa_process_attachment` with the exact current
process instance and form file ID. It obtains a signed URL through the configured
DWS CLI's read-only `attachment download-url` command, limits downloaded bytes,
and shares the same extraction, OCR-page handoff, temporary lifetime and response
size checks. Generic Drive access denial does not prove that this OA-scoped read
is unavailable. This path does not call `authorize-preview` or modify access.

When the premium comment download is unavailable (for example the tenant's OA
Premium benefit has expired) or the form download is refused, the Consumer runs
`dws oa approval attachment authorize-preview --instance-id ... --file-ids ...
--with-comment-attachment` in its own shell for that one instance and file (the same
preview access opening the attachment in DingTalk grants), then reads the body with
`read_dingtalk_drive_file(node_id=file_id, space_id, file_name)` or
`dws drive +download`. The Skill limits that shell to reads and this preview
authorization. The Consumer has the same shell on every route, confined the same
way. Codex: `sandbox_workspace_write.network_access=true`,
`writable_roots=[~/.dws]`, working directory = the task directory. Claude: the
`Bash` tool plus an inline `sandbox` setting (`failIfUnavailable`,
`allowUnsandboxedCommands=false`, `filesystem.allowWrite=[~/.dws]`, network limited to
`*.dingtalk.com` and `*.aliyuncs.com`, `enableWeakerNetworkIsolation` so the Go `dws`
binary can verify TLS inside the macOS sandbox), run with the task directory as the
process working directory (the sandbox allows writes there). Audit has no shell on
any route. There is no service tool for preview authorization any more.
(Derek, 2026-10-22/23.)

`retry-source-verified-email-action --account ... --action-id ...` examines one
current failed move/trash action through the existing read-only IMAP wrapper.
Preview opens SQLite read-only without schema initialization. Only an exact
message still in its source can be released with `--apply`; missing, ambiguous,
already moved or changed identities are not retried. The transaction checks the
current plan, parameters, config version, failure snapshot, dependencies and
active sibling claims. It schedules one attempt using `next_attempt_at`, keeps
the failed status and immutable attempt history, and does not reset the counter.
The normal worker consumes that schedule before calling the provider; another
failure does not renew this explicit allowance. Dry-run refuses `--apply`.
Schema 51 records the source-readback digest and exact next attempt number in
the immutable `email_direct_retry_requests` relation. Provider-factory failures
and interrupted-worker recovery also consume this one-attempt allowance rather
than renewing it. Deployment requires a verified backup and the matched 50-to-51
publication inverse; rollback removes only an empty request relation and refuses
to discard any recorded recovery authorization. Readback exceptions close the
session opened by the examination worker before propagating the error.
Duplicate-key inserts, including SQLite `INSERT OR REPLACE`, are rejected by
the authorization relation alongside updates and deletes.
Authorization includes a canonical non-secret snapshot of the IMAP account identity,
transport, credential reference and move mode captured by the read-only factory.
The release transaction and both normal-worker claim paths compare it against
the current account configuration; a changed configuration cannot consume that
old authorization.
Missing captured configuration evidence is rejected rather than inferred from
the current database. A changed configuration is also shown and counted as
blocked, not as automatically retriable; authorization and failure history remain.

Audit field-error feedback must cite the exact JSON path, literal value and type
from the current digest-bound candidate. Prior revision feedback is not evidence
of the current field value. Quoted numeric identifiers remain strings; an
already-applied correction must not be requested again. This evidence rule does
not waive actual action-contract violations or authorize external execution.

Manual rerun reads its terminal run with `load_events=False`: the existing
refusal check consumes only structured error metadata. Native trajectory and
result hydration is excluded from this queueing request. The existing refusal,
original-trigger verification and new-generation enqueue flow are unchanged.

Dispatcher queue claims first probe using a read-only connection. The existing
candidate scanners still apply source order, due time and live-owner protection;
positive probes are repeated after acquiring the immediate write transaction
before changing the source or lease ledger. Empty probes acquire no writer lock.
Todo outboxes also probe for expired running rows that need their existing
reconciliation transition, so that cleanup still runs even with no queued work.

Agent run claim, lease renewal, session binding and expired-run failure commit
their metadata transaction before native trajectory hydration. They capture the
written row and its native attempt references together, so the returned metadata
remains the exact operation snapshot even if another writer runs during file
reading. Native session-directory discovery, transcript parsing and result
projection run after SQLite has released the writer lock and closed the
connection; lease checks, claim ownership and returned events/results remain
unchanged.

Meeting Memory enqueue first excludes existing event IDs using the meeting
status and unique event indexes in a read-only connection. Only missing jobs
load `final_message` to check the existing nonempty delivered-conclusion rule.
An empty probe returns without acquiring the shared SQLite writer lock. A
positive probe closes its read connection, then the immediate write transaction
rechecks status, conclusion and event absence for those exact IDs before insert.
Competing enqueues create one event without advancing the sequence on a no-op;
jobs delivered after the read snapshot are picked up by the next scan. This
removes the historical wide-row scan from the write transaction: production
telemetry observed a 13.454-second hold there with a simultaneous 13.045-second
dispatcher lock wait.

Dispatcher lease completion retains the same newest 10,000 terminal rows,
ordered by `terminal_at desc, rowid desc`, and every active row. The required
partial terminal-time index supports that order. Completion finds the 10,000th
row's `(terminal_at, rowid)` boundary and deletes only older terminal rows in the
same transaction. It no longer sorts the whole table or materializes a 10,000-ID
membership list while holding the writer lock. Existing-schema initialization
adds the index without rewriting lease facts. Guarded State updates seek the
existing numeric source-rowid unique index using the identity already selected
under the writer lock; canonical identity checks, CAS and retention remain unchanged.

The meeting delivery batch poll reads the exact ready, unlocked and due
eligibility before requesting SQLite's shared writer lock. An ineligible poll
returns without joining the writer queue. This read is not a reservation: the
existing immediate transaction rechecks eligibility, locks the selected rows
and writes delivery claim tokens atomically. Competing claims, token-bound
cleanup, retry timing and external sending are unchanged. This removes idle
meeting delivery lock waits, not every possible source of SQLite contention.

Manual rerun of a compacted DingTalk message reads the exact original message ID
through the native `+messages-mget` contract, without reactions, threads or
resource downloads. Complete message results and matching conversation/message
identities are required before a new generation is queued. Display fields never
substitute for message content. Historical provider risk refusal is checked
before this read and remains non-replayable. The enqueue transaction also checks
the captured execution generation, current attempt retirement, and structured
refusals of the current generation; a stale source read cannot rotate a newly
refused generation. Raw source rows are validated before message normalization,
so malformed entries cannot be silently dropped to manufacture a unique match.
Other channel payload requirements
and the Consumer/Audit/System execution lifecycle are unchanged.

For WeChat, a sent Attempt cannot enter the ordinary History rerun endpoint,
including a direct API call. History exposes a separate resend operation only
for the latest sent Attempt of a task whose exact original input is still
available. Its request names the original trigger message ID, and the store
checks that ID against the sent Attempt and conversation in the same enqueue
transaction. Repeated submissions while the new generation is pending or
processing reuse it; after that generation ends, the same source Attempt
cannot enqueue another resend. A later explicit resend must start from its
latest sent Attempt. Compacted WeChat inputs have no executable original
payload, so they do not expose this action. Existing failed-Attempt reruns
and WeChat Sender delivery/recovery behavior are unchanged.

An instance supplement that rotates execution generation requires the original
schema-valid execution payload. Missing source material rejects the request
without persisting a supplement, invalidating the candidate, or changing its
generation; the supplement and rerun remain one transaction.

The isolated Task replay tool accepts `--source-payload` for a retired work
input. It verifies the exact original UTF-8 hash, byte length and source identity
before taking a lease or changing input status, then uses that same verified
source for execution and evidence readback. Missing or changed material is
rejected without changing the input. Frozen multi-version cases advance only
after the preceding turn completes and compare-and-swap its prior source hash;
this is a database-copy evaluation operation, not a production semantic import.
The CLI selects its existing `--code-root` before importing application modules;
run each comparison revision's matching script in a separate process so the
reported revision describes the imported code. Readback uses the current typed
Task/Run, Business, Action and provenance projections. Domain comparisons include Business relations held in shared
Actions and their provenance, excluding run bookkeeping; source versions use
the existing source identity key. Each case retains its supplied originals only
in one transient PreparedSources instance through seeding, each turn and final
readback. The verified WorkItem summary can supply only its exact matching
source binding, hash and byte count. External originals use the request reader;
missing material remains unverifiable. No source body or trajectory copy is
persisted. Input updates retain the prior exact source, status, body fingerprint
and payload CAS predicates through current typed Tasks SET parts.

Reply consumption reads the current generation's persisted orchestration terminal
result before parsing its trigger input. A completed generation can therefore
finish idempotently after its historical input has been compacted. This read uses
the existing candidate, Audit, and System receipt state machine and dispatches
no action. Nonterminal generations still require complete validated context and
the ordinary source reread before any new Agent turn or controlled execution.
Settled-input assertions use exact original UTF-8 hashes and byte counts plus
stable task/input lineage; a revised pending input must still retain its body.

Attention selects failed email classification task IDs from the existing compact
status index before reading task payloads. Physical row IDs preserve nullable
legacy task identities. Case-insensitive failure matching and
the displayed source fields remain unchanged. This avoids scanning completed
classification input payloads; it does not introduce a cache or schema change.

Native DingTalk message resources are reviewed as stable conversation/message/
resource identities plus verified byte length and SHA-256. The provider reader
downloads by those identities in a task-owned temporary directory, requires
the native message-verification receipt and exact returned identities, and
checks file containment and length before hashing. Downloads are removed when
the read ends. Source capture and pre-execution reread use the same reader;
changed resource bytes or message/resource facts invalidate the candidate.
Only the native resource schema's URL, expiry and download instruction are
transport capabilities, not candidate facts. Unknown resource fields and missing
identity/verification fail explicitly. Original raw messages remain unchanged;
provider-rendered or serialized text is not a native resource envelope and
remains unchanged under the existing source security validation.
signed URLs elsewhere, including authored results, retain the existing rejection.
This changes source representation, not action permissions or historic replay.

Worker image downloads bind media IDs in `content` to the current message and
media IDs in `quoted_content` to `quoted_message_id`. Download requests and
task-local cache keys use that source message identity. Failure details name the
resource owner while the error row remains bound to the trigger message for task
recovery. A quoted image without a source message ID remains a technical read
failure instead of being fetched against the reply message.

Runtime result security validation budgets authored fields, captured-source
metadata and each captured-source value separately. Candidate/list/binding
wrappers are not charged again against a source's JSON-decoding depth budget.
Every field is still inspected by the same credential, signed-URL and local
runtime predicates; each source retains its depth bound, callback validation
and original contents. The total serialized result byte limit still applies
to the complete envelope. This does not exempt business secrets or transport
capabilities, change source identity/digest checks, or permit an external action.
Recovery of a completed Audit runtime result repeats the authored-result
sensitive-value check. A violation remains rejected and is recorded as a
result-validation failure, not a Codex/provider process outage; it cannot be
adopted or executed.

The Task Attention inspector reads decision fields only from the completed
runtime attempt's exact native session and range. It closes the read-only
SQLite connection after loading references, before reading transcript files,
then uses a separate read-only connection to select Project context from saved
projection identities. Missing, invalid or out-of-range native decisions are
reported as unavailable, not reconstructed from retired `decision_json` bodies
or interpreted as a successful empty decision. Projection receipts remain
visible independently; the inspector never initializes or migrates storage.

Terminal unsubscribe reconciliation probes classification identity and plan
version before loading payloads. Only a missing receipt plus an existing
done/skipped/needs_human task triggers full hydration; failed tasks stay excluded.
Hydration rechecks the current plan version and processed status, so concurrent
plan replacement cannot attach new payloads to the old action identity. This
read-only optimization does not create or infer provider receipts.

Reply quality checks rank attempts using only identity, ordering, status and
review fields, not complete input or reply payloads. OA completion receipts and
reviewed human-decision candidates are still checked through their original
queries. Deduplication and recovery semantics are unchanged; narrower projection
does not establish that all SQLite lock contention or endpoint latency is fixed.

Runtime EmailStore initialization validates account configuration even when
historical durable-row validation is disabled. Invalid account JSON makes email
storage unavailable through the existing initialization boundary; unrelated
audit routes remain available. This scans account configuration, not historical
messages, actions or training observations.
Both lightweight and full validation require at least one nonblank string scan
folder per account, using the same account configuration check.

Email training snapshot reads validate the external payload against the frozen
SQLite parent's identity, versions, seed, observation time and digest. A valid
external file does not excuse a tampered parent record. These comparisons run
only for the requested snapshot and do not restore historical startup scans.
Publishing a subsequent snapshot propagates failures while reading the previous
snapshot; a corrupt parent cannot be treated as missing label history or advance
watermarks. The failed publication leaves the previous snapshot unchanged.
Folder and selected snapshots have separate cumulative label baselines. Reads,
training pins and idle cleanup therefore use the latest complete snapshot of
each type, not a single global latest file. Older snapshots of the same type
remain unavailable unless pinned by an active training run. Already removed
payloads are not reconstructed or silently treated as empty history.

Cron scheduler and dispatcher incidents close only after the same component
completes a successful tick. Recovery retains incidents from the tick's entire
start second or later, because stored incident timestamps have second precision,
and incidents from other components. Startup health alone is
not recovery evidence. With no eligible incident, resolution does not acquire
the SQLite writer lock; the write transaction rechecks the same conditions.

A resumed runtime attempt initializes an empty transcript range at its observed
start offset. Setting the session advances an unset end to that offset without
discarding any later captured end. Thus a result-validation failure can retain
its original error before a transcript end is captured, instead of failing its
own persistence with an invalid range. An empty range is not transcript content
or execution evidence. Explicit invalid completion ranges are still rejected.

Email classification polling checks for due pending work or an expired running
lease before acquiring the SQLite writer lock. Ineligible polls are read-only.
The eligibility read grants no ownership: expired-lease recovery and the full
claim query still run under BEGIN IMMEDIATE, preserving generation and owner
checks if another worker claims the candidate between the read and the lock.

An orchestration failure before the first Agent run has `final_run_id=0`.
The Reply worker handles both retryable and terminal failures through its
generation-checked no-run transition. Terminal failures retain their original
error and summary in the failed Attempt without retrying or creating an Agent
run. A missing nonzero final run ID remains an integrity error, not a pre-run
failure.

When the Reply worker records an exception after a newly failed AgentRun,
an explicit JSON boolean `retryable=false` in that run's error denies task
requeue, including clean-session retry. Caller denial also remains binding.
Only the current task/generation and a run changed since the pre-processing
snapshot participate; historical unchanged failures cannot supply this flag.
Missing flags retain the caller policy and existing attempt budget. The
original failed run and failed Attempt remain evidence, not `needs_human` or
a successful business result. Existing authorization/provider wait paths are
unchanged.

Retry session selection honors `session_continuable=false` on the latest failed
run for the current generation, role and revision, except that a retryable
Agent-sourced typed Consumer failure does not force a new session. Its wire result's
`AgentError.session_continuable` default is not evidence that the native
session is unusable, so Consumer continues the same route session. Audit keeps
its existing fresh-session behavior for this case. Service-sourced
noncontinuable failures still start a fresh runtime. Repeated identical
invalid/missing result failures retain their existing fresh-session behavior.
Missing flags and string values are not treated as false. A later completed
session covers an older failure. Existing candidate digest, revision, source,
approval and external-receipt checks remain unchanged; fresh transport is not
permission to replay an external action. Old run/session history is preserved.
This decision uses the latest terminal run even when its legacy
`codex_session_id` is empty: API/Claude sessions may exist only in route/runtime
records. The runner computes fresh selection before claiming, and the same
decision initializes the new run without an inherited session. This also keeps
Audit's repeated-result fresh retry consistent with immutable run binding.
Fresh selection remains effective across capacity retries and route changes.
Within that run, a route may resume only a session actually observed in its
current native events; before a new session event it stays fresh. Another
explicit fresh decision invalidates that route's earlier in-run session.
Normal continuation outside a fresh retry keeps its existing selection rules.

`CEO_DATABASE_BACKUP_PAUSED=1` pauses automatic daily database backups before
opening databases, creating directories, or pruning snapshots. Unset the flag
to resume the existing daily workflow. Set it in the service process environment
and use the formal restart/deployment path; it does not interrupt an in-flight
copy. Explicit deployment backups remain governed by the deployment safety gate.
Migration regression fixtures record their actual legacy schema version before
reopening and assert the complete path to the current version. Quiet-deployment
regressions distinguish a running meeting turn from an unclaimed historical
processing row; neither a stale timestamp nor a status alone proves live work.

The database-backup worker checks once per hour. It keeps one integrity-checked
daily backup in `backups/`; after confirming that copy is complete, it prunes
root-level `auto-reply.sqlite3.pre-*` recovery snapshots and their SQLite
sidecars once they are older than 24 hours. A failed or interrupted daily copy
does not authorize that cleanup.

The repository updater does not write deployment progress to the shared
SQLite database while waiting for the live service to become quiet. It records
the next phase only after the service is quiet and stopped, using a narrow
connection that does not initialize the application schema. That write
acquires `BEGIN IMMEDIATE` and retries transient `SQLITE_BUSY`/`SQLITE_LOCKED`
up to three times with the connection's existing 30-second busy timeout.

Reply queue polling first reads the current channel's due pending tasks without
acquiring a write transaction. An empty channel, future-only queue, or another
channel's work returns immediately even while a different writer is active.
When the preview finds work, the original write transaction rechecks and claims
the current rows atomically. The preview is not a reservation or queue cache;
work arriving after an empty preview remains pending for the next poll. This
removes idle Reply polling as a SQLite lock contender; it does not establish
that all other long transactions or API latency causes have been eliminated.

Slow SQLite context diagnostics separately report body execution, transaction
finish (commit or rollback), and connection close time. Body time can include
waiting to acquire a transaction, so it is not automatically write-lock hold
time. Logs keep call sites and elapsed durations, not SQL parameters or business
payloads. Transaction behavior and existing error propagation are unchanged.
Reused `read_snapshot` connections also report contexts lasting at least one
second with the same caller chain and timing fields; their finish and close
times are zero because the enclosing snapshot retains transaction ownership.
Specialized Status queue readers also report individual calls lasting at least
one second, using only the reader name and duration; no parameters or business
payloads are logged. Counts, freshness and error propagation remain unchanged.

Worker status summarizes the current `reply_attempts` projection using only
identity, status, timestamp, and error fields. These fields are VIRTUAL generated
columns over `actions.payload_json`: selecting metadata alone does not prove
index-only I/O, and an uncovered field can still decode the full JSON payload.
It groups trigger identities and seeks each trigger's latest attempt through
`idx_actions_reply_current_trigger`; it does not rank every retry row in a
window function for each status request.

Runtime-attempt detail reads filter by `agent_run_id` and order by
`attempt_number`; the partial `idx_agent_runtime_attempts_run` index serves that
lookup for run-bound attempts. Attempt details batch this indexed lookup across
all runs on the page, avoiding one SQLite connection and query per run.
Attempt detail loads the selected run's native event and result references in
SQLite, closes the connection, then reads and parses the native transcript files.
Slow or large local transcripts therefore do not keep a SQLite connection open.
The complete event-evidence projection uses the same protocol normalization as
the detail reader: native Codex `McpToolCall` items become `mcp_tool_call` while
retaining their call ID and result. This lets the existing scheduled Consumer
document-receipt projection recognize native evidence; it does not approve an
Audit result, replay a write, or alter task state.
Generalized attempts without an `agent_run_id` remain outside this index and
keep their existing workload-key indexes.

Audit web startup does not prewarm the History list or its 24-hour, 7-day and
30-day charts. Those views scan large operation-history projections and run
queries on demand when requested, so every service restart does not launch
multi-minute reads against the same SQLite database used by workers.

The current `idx_actions_errors_unresolved` partial index contains only typed
Errors whose `coalesce(error_resolved_at, '')` is empty. Attention retains the same recovery,
supersession, ordering and current-state predicates, but does not scan resolved
incident payloads. Resolving an incident removes its index entry without deleting
history; a new unresolved incident is visible immediately. Installation follows
the existing schema-version/manifest startup migration after the quiet-deploy
backup, not request-time index creation or business-state repair.

Scheduled incident recovery locates the original run through its indexed event
ID or integer primary key, retaining the exact canonical conversation-ID check
after parsing. Malformed/noncanonical IDs cannot claim a recovery, and a later
success must still belong to the same scheduled task with a done/skipped Reply
execution. Unrelated run history is not scanned to discover the original run.
Email-action Attention first selects failed rows by internal rowid from the
compact status index before loading classification payloads; projected business
action IDs, including legacy NULL values, are unchanged. An explicit join order keeps
SQLite from starting with all processed classifications. Current ActionPlan,
case-insensitive status, retry-window, ordering and limit rules are unchanged.

Direct Email action polling uses the same current-plan, sibling, retry-time,
account and dependency selection for a read-only probe and the subsequent
write transaction. A negative probe does not acquire the writer lock. A
positive probe is not a reservation: BEGIN IMMEDIATE must recompute eligibility
before the unchanged conditional update. New work arriving after a negative
probe waits for the next poll; no queue result is cached. Each pass starts from
the indexed pending/failed statuses and primary-key checks that each action
belongs to the processed classification's exact current plan. Classifications
with only retired-plan pending/failed rows do not hydrate historical plan
bodies. Remaining candidates still read all sibling actions, preserving the
block from any historical processing action, through the
`(classification_id, action_plan_id)` index; it does not scan every action while
holding the SQLite writer lock. Candidate order and the existing action,
dependency and retry rules are unchanged.

Reply-task scheduled recovery uses the required partial expression index
`idx_scheduled_task_runs_reply_execution`, keyed by the existing integer-cast
execution link for `execution_kind='reply_task'`. It avoids scanning unrelated
scheduled history while preserving the original CAST behavior, including legacy
noncanonical link text. No channel or canonical-text restriction is added to
that pre-existing recovery rule. Installation uses schema version/manifest
migration after a verified quiet-deploy backup.

本文档是 CEO Agent Service 当前运行机制与已批准生命周期政策的总览入口。除明确标注为
“已批准的生命周期政策”的段落外，正文描述当前代码事实；政策段落是后续实现约束，不表示
对应代码已经切换、部署或在生产启用。`docs/superpowers/` 下被新设计取代的历史 spec/plan
仅用于追溯，不代表当前目标规则。

## 运行角色

两角色都可通过 `read_dingtalk_group_members` 读取精确群 ID 的 DWS 真人/机器人
成员桶和原生有界自动分页结果；调用方检查 complete、hasMore、buckets 和 failures。
`read_dingtalk_user_profiles` 按已验证的组织 userId 批量读取原始资料，不进行姓名搜索
或身份补全，并保留原始组织字段和空资料；title 必须由真实返回证据确认。群 openDingtalkId
不能直接传为组织 userId；姓名搜索只发现候选，须按稳定 open ID 关联后才能读企业资料。
工具在 Consumer/Audit 的实际目录及原生 CLI enabled_tools 中一致注册为只读，Audit
仍没有写能力。本修复仅补齐读取能力，不新增成员变化门禁或来源绑定策略。

微信 Reader Skill 的 `status`、`read-recent` 和 `produce-once` 都必须显式接收调用方提供的
绝对服务数据库路径，并原样传给受控 IPC CLI。缺少 `--db` 时在启动 IPC 前拒绝执行，
不能从工作树的 `data/` 推断生产账号就绪状态。该路径修正不改变 Sender、目标选择、
外部回检、发送授权或历史投递状态。

运行时发送工具拒绝 `provider_risk_rejected` 时，服务保留该错误代码并停止自动重试，
不将其降级为通用可重试失败或改成 `needs_human`。恢复必须先取得知情授权或形成实质更安全的
候选，再按原业务身份经过正式审核、外部回检及投递流程；不得换工具绕过拒绝。
历史 Attempt 的“重新处理”入口也会识别 AgentRun 中的 `code` 或 `source_code` 拒绝，
页面不提供原候选重放，直接 POST 返回冲突且不入队。详情明确说明限制；普通技术性失败仍保留
原来的重试入口。新的实质不同候选或有明确范围的授权必须作为新的正式处理提交，不能复用这条
历史按钮代替。

失败 Reply task 的 Attention 保留当前执行代最新 run 的原始诊断：优先 `source_code`，
否则 `code`，并展示来源及「Agent 说明」`reported_summary`。因此已保存的
`provider_risk_rejected` 不会只剩通用 `agent_reported_failure`；这些诊断不是外部效果证明。
旧代或无法解析的诊断不替换任务错误；读取不修改状态或恢复边界。

同一诊断生产器用于 Status API 时，后端 `AttentionRow` 与前端 Status 解码器均声明
可选的字符串或 `null` 字段 `error_code`；无原始诊断的行不必提供它。未知字段与错误类型
仍严格拒绝。真实任务/run 生成的 Attention 行必须能经过 `/api/console/status` 与前端
解码，不能只验证生产器自身而让新增诊断导致 HTTP 500 或页面拒绝正常响应。
人工决策行使用同一基础字段合同并要求字符串 `detail_label` / `detail`，不是普通
Attention 行；前端保留其独立说明。提供 `human_decision_rows` 时必须是数组，不能把
错误的对象、字符串或 `null` 当成“无人工决策”。

Status 的每次请求从一份新的只读 SQLite 快照读取队列、Attention 和人工决策，
同一请求各区块及 summary 复用这些结果，不再逐区块重复扫描或混用不同快照。
回复任务的当前业务对象投影与邮件动作的当前 ActionPlan 投影各在单条查询内物化一次，
计数、可重试数量、最新更新时间和最新错误从同一紧凑投影计算；不为这些字段反复读取历史宽行。
回复错误仍按失败任务的更新时间／ID 取最新，邮件仍按当前动作的更新时间／动作 ID 取最新，
原有大小写、空值、终态失败与可重试划分保持。
下一次请求重新读取，不缓存业务队列状态。连接器、微信 IPC 及系统健康探测仍使用
各自已有的短期后台缓存，不阻塞业务队列读取；此优化不修改失败或人工决策的判定。

Attention 的失败 Reply task 先在已有 `(status, id)` 覆盖索引上筛选 ID，再按主键读取
正文和诊断，避免逐行读取大型任务表正文。大小写兼容、当前业务对象、后续成功覆盖和
外部失败排除条件保持不变；不新增业务缓存或数据库迁移。
其他 Attention 来源及队列的最新失败原因也先通过已有状态索引筛选行 ID，再读取
必要字段。失败大小写、非空错误过滤、更新时刻及 ID 排序和下一快照立即反映恢复保持不变。

定时任务的后续恢复检查先匹配当前失败 Reply task 或错误所绑定的旧 run，再查询同一
scheduled_task_id 的更晚 run。使用嵌套 EXISTS 避免查询器从大量无关成功 run 反向连接，
导致每个成功 run 重复扫描历史。覆盖条件仍是更晚 run 已 dispatched 且对应 Reply task
为 done/skipped；不同任务、较早成功、后续失败或 service_command 均不能覆盖。
SQLite VM 操作预算回归限制无关成功历史带来的查询工作量，语义回归同时保留原失败和错误
物理记录；不新增索引、迁移、队列缓存或恢复写入。该修复针对查询计算成本，不将慢连接
上下文计时等同于持写锁时长，也不证明所有 SQLITE_BUSY 原因已消除。

Workbench 领取轮询先只读检查是否存在 queued 或租约已过期的 running turn。两者都没有时
立即返回，不执行 BEGIN IMMEDIATE 与其他服务写入争锁；非过期 running 不需要恢复。
只读连接结束后，有工作时重新开启原写事务并重查、恢复过期 turn、按原条件与 CAS 领取。
检查后新提交的任务保留 queued，下一次轮询领取，不缓存空队列或抢先修改租约；终态、
审批确认、归档、恢复及停止策略不变。

运行记录质检的路径规则在每次扫描开始时解析一次，随后逐行、逐字段检查同一份规则。
下一次扫描重新读取配置，不缓存质检结果或遗漏历史记录。凭据检查与固定临时运行路径
检查保持不变；显式空路径配置不会重新加载默认值，仍检查固定临时路径。该修复减少
逐字段重复解析环境与主目录的计算成本，不代表所有数据库 I/O 或锁等待均已消除。

日报的 task-bound `consumer_document_write` 回执除报告文档和正文读回外，还原样返回
已从目标知识库完整目录解析出的 `folder` 元数据。Consumer 使用其中 provider 实际提供的
`url` / `docUrl` 完成日报文件夹链接要求；服务不由 node ID 拼接 URL，也不为返回该元数据
新增写操作。provider 未提供链接时仍保留缺失事实。周报回执、审核和消息投递边界不变。

通用文档写入在 DWS 明确返回 `doc.operation.v1` 的对应写操作 `partial_success` 时保留其结构化错误及已执行步骤；写入返回后服务读回失败也保留已有 provider 结果。两者返回 `ok=false/status=partial_success/verified=false/readback=null`，不制造成功读回回执、不自动重写；未执行或提交状态未知仍是原错误。Agent 继续使用现有读取入口核对目标，现有 Audit 与任务续跑规则不变。

所有系统业务审核项必须对应实际系统任务及其当前业务实例；工具或接口存在不构成审核需求。没有对应系统任务就不设置该业务的系统审核项。当前对应关系见 [系统任务与审核范围](audit-task-scope.md)。小青面试与提交由 Derek 在个人对话中处理，不属于本次后台系统审核范围。扫描、下载和登录命令沿用既有技术结果验证，不因这条范围规则新增 Audit 回合。

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。所有 Consumer 入口共用同一条行为指令：不直接向外发送消息或邮件，也不直接执行审批；返回结构化决定或候选，由服务处理外部效果。Audit 的角色规则逐项核对外发候选的收件人/目标、内容、证据和任务范围；候选文本不作实际执行或送达证明，Audit 不发送或执行任何动作。若来源或任务明确要求只要草稿或由用户发送，Audit 退回或拒绝拟发送消息、邮件的候选；其他有效结构化外发提案也只能由 System Executor 在 Audit 批准后执行，只有服务回执证明外部效果。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。该指令不构成工具或凭据的强制权限隔离。

Consumer 的普通工具工作不因写入而自动进入系统动作审核。任务绑定的本地文件由
`consumer_artifact_write` 写入 `/tmp/ceo-agent-service-task-artifacts/<task_id>/<execution_generation>`，
Consumer 与 Audit 可用 `read_task_artifact`、`list_task_artifacts` 读取当前代材料；Audit 没有写工具。
任务处于 `pending` 或 `processing` 时保留文件供恢复和审核；进入 `done`、`failed`、`needs_human`
或 `skipped` 后，服务清理该任务全部代次的工件目录。Consumer 与 Audit 运行指令均要求把 memory
作为耐久参考区、禁止把临时代码、测试、数据库、日志、下载、截图、缓存、草稿、中间结果或单次运行
工件存入 memory，临时文件只使用 task-bound `/tmp` 工作区；有价值的最终材料仅在已有授权目的地按主题
归档。部署前遗留的 workspace 工件会在任务下次读写时迁入 `/tmp`，终态清理也会清除尚未访问的旧位置。
`consumer_document_write` 处理当前 scheduled 任务产物和显式文档目标。创建前通过
`dws doc +list --folder <parent_id> --page-all` 完整核对同名目标；创建后使用成功且已验证的
provider 回执 nodeId 进行正文读回，不把文档 ID 交给 Wiki 节点列表接口。工具返回的实际内容读回及 SHA
证明普通材料写入，不是已审核消息或 OA 动作的 System 回执。原生 V8 可调用具名工具和计算，
Codex Consumer 可在 task-bound `/tmp` 工作区进行原生代码执行；Audit 只读。实际不可用的工具应如实报告，不能伪造执行。
系统注册且需要审核的动作仍只由 SystemExecutor 执行。Consumer 的结果解析只验证严格 wire，
不再对整份文字扫描“已发送”等完成短语；历史事实和本轮工作由完整来源及真实回执支撑，
摘要不会生成 candidate execution、外部动作回执或 sent_replies。此规则不改变已有运行时风险拒绝、
授权缺失或历史拒绝不可重放的处理。

Codex Consumer 的工作工具面不是白名单（Derek 2026-10-24）：`make_role_agent_command` 对 Consumer 不再强制关闭
`features.apps`、`features.plugins`、`features.remote_plugin`，也不再关闭 `~/.codex/config.toml` 里的其他 MCP server
或限制它们的工具，而是沿用本机 CLI 的配置，并对这些 server 设置 `default_tools_approval_mode="approve"`
（`approval_policy="never"` 下否则工具会被拒绝）。仍然关闭的是：服务 MCP 清单（Settings → MCP）的 `disabled_servers`、桌面控制类 server（`computer-use`、`computer-history`、`cua_repl`，代码常量 `CONSUMER_ALWAYS_DISABLED_SERVERS`）、
`multi_agent`、`computer_use`、`browser_use*` 等原生功能。读取 Figma 这类外部信息属于处理过程中的工作工具，不走提议；
只有发消息和发邮件等需要 Audit 的终态动作走 typed action，该约束仍是提示词约束（Consumer 不得经 shell 的 `dws` 发消息、发邮件）。
Audit 的命令不变：仍关闭上述功能，只启用 `ROLE_MCP_READ_TOOLS` 列出的只读工具，并强制关闭其余 server。
Runtime Context 对没有 `enabled_tools` 限制的 server 声明 `mcp__<server>__*`（全部工具，参数以本轮工具 schema 为准），不再逐个枚举。

要求修改 CEO Agent 服务本身的请求（例如「请磊哥修一下分身」，任务 15810）不是回复或 Agent 能完成的事。`ceo-message-triage` Skill 规定 Consumer 只回复一次，引导发信人用回复末尾的「👎 需改进」反馈链接提交，使其进入用户反馈流程；会话里已有这条回复时用 `no_action`。同一 Skill 的 Audit 解释把这条回复（或之后的 `no_action`）视为已满足请求，不得因服务尚未修复而退回，重复发送同样的指引属于重复。此前 Consumer 在「再回复」与 `no_action` 之间被 Audit 反复退回，三轮用完以 `audit_revision_exhausted` 失败。

微信统一路径阶段 1（Derek 2026-10-24，微信 Consumer 与其他频道使用相同工具面）：Consumer 与 Audit 的 `agent_cli` 读取工具新增 `read_wechat_messages(limit)`，读取当前认领的微信任务自己的会话（最新消息在后，链接带文章正文）。工具不接受会话参数，会话 ID 和单聊/群聊类型取自认领任务，任务不是微信频道、执行代变化或没有就绪账号都会拒绝。微信目前仍走自己的流水线，所以只有微信任务才会用到它；其他频道的任务调用它会得到「不是微信会话」的拒绝。

Codex Consumer 命令按当前 `consumer_purpose` 选择 general/OA 原生输出 Schema，并在模型输出后
把动态 JSON 键值条目无损还原，再交给现有严格 wire/Pydantic 解析器；OA 动作仍按原有
action-specific target/payload 合同校验。已完成运行结果的 schema identity 绑定实际提交的 Schema
字节，避免用旧自由格式结果恢复新原生格式运行。Consumer 的 Codex 与 Claude 原生 schema 路线不再把同一份 Pydantic wire schema 重复放进服务 Developer 文本；Friday 无原生 schema 的路线仍保留完整文本，Audit 输入不变。预览及运行快照使用同一路线装配。所有 Consumer 继续共用“不得直接发送对外消息、邮件或执行审批”的约束；解析、业务判断、审核与 SystemExecutor 外部动作流程不变。

绑定 `dingtang-okr-review` 的 Consumer 读取当前评分详情并提交 `dingtang-okr/score_kr` 候选，不直接运行评分脚本或写入接口。该动作走现有候选、Audit 和 SystemExecutor 执行记录；服务通过
已同步登录态的无头 Chrome 页面调用页面原生 API。只读入口返回周期、当前批次、目标、
评价人及登录用户的评分详情；写入前核对周期列表与批次名称、详情中的周期和目标、
KR 评分配置，以及登录用户与候选评价人一致。页面的 `makeScore` 只接收目标和 KR 记录，
按登录用户写入当前批次；写入后逐项回读同一周期、批次、KR、评价人、分数、评语和完成状态。
页面的评分详情与评分提交请求使用原页面认证，只对这两个端点将 `X-Space-Id`
置空；其余页面请求不改。只读详情的提供方对象错误保留数值 code 与消息，经既有
凭据脱敏后返回，不泄露 traceId；读取成功不产生评分写入回执。
不确定回执只允许只读 reconcile；不能因进程恢复重新提交 `makeScore`。

候选分为普通动作计划和任务类别规则问题。`needs_human` 只能针对可复用规则缺口，包含来源上下文、具体原因、证据及 2–4 个互斥规则选项，每项明确 `applies_to=task_class` 并说明后果；选项绑定在所选规则下继续当前事项的完整方案，停止选项写明 skipped 和原因。不能用 `requested_input` 将当前事实、偏好或业务决定转给 Derek；应由 Agent 先基于现有规则形成安全、有限承诺的普通回复，或向有该事实的来源人提出一个具体问题。不能混合立即执行的动作与尚未选择的条件分支。

启动恢复只会保留满足当前 Consumer 结果合同的待选决策。旧式 `requested_input` 或缺少可复用规则选项的候选仍可在 History 只读查看；若它仍占据当前、经 Audit 批准且未选择的投影，`reconcile_invalid_needs_human_projections` 会把当前 Attempt/Task 投影转为 `failed`、清空可执行选项，并保留原候选、Audit 与 run 原文。之后须由正常 Consumer/Audit 闭环生成普通回复或向来源人追问；该对账不发送消息、不执行当前分支，也不把失败伪装成完成。有效的 task-class 规则问题保持 `needs_human`。

若 DingTalk trigger 引用了一条本服务通过 provider 成功发送状态回读确认的 Agent 消息，系统将该关系作为结构化输入来源传给 Consumer；模型不得将其续接/恢复任务升级为 `needs_human`。此判定使用 conversation、quoted message ID 与发送回执的对应关系，不根据正文里是否有 AI 签名做字符串匹配。Consumer 结果校验和 Audit 对同一当前任务上下文独立执行该限制。

通用消息受众契约由 Consumer/Audit 共用的固定审核规则注入，已保存或空的自定义规则均不会
省略该契约。候选应依据相关群讨论、完整当前成员、稳定身份及当前 title/职责判断实际内容
的披露范围；职位只是证据，不是自动授权，私聊也不自动安全。同一受众适合全部内容时保持
单条消息；确需不同受众时沿用既有多动作候选，各自保存准确正文、目标与 action identity。
拆分后的私聊只能发给已核实的对应人员，不能发给 principal 自己或其已验证别名、默认抄送
自己或把自己作为未知对应人员的兜底；另行明确要求给 principal 的报告不受此拆分约束影响。
每个动作独立核对既有回执并恢复未完成结果；拆分不得绕过历史运行时风险拒绝。这是准备和
审核规则，不是新增发送器，不以 prompt 注入成功替代原生业务验收或生产回执。

Consumer 新输出的业务结果与 wire JSON Schema 和解析器一致：`needs_human` 必须有 2–4 个 `applies_to=task_class` 的选项且 `requested_input=null`；`needs_human_reason` 必须是一句中文大白话，写清要决定什么及原因，最多100字，详细证据写在 `decision_basis`；`proposal`、`no_action`、`failed` 不得包含人工问题字段。普通方案的事实证据写在 `proposal.sourced_facts`，无需动作的依据写在 `summary`。历史持久化的 `requested_input` 仅供只读兼容，不得因其可解析而重新激活人工待办。

新增 Consumer 输出在 Wire 解析入口校验 `dingtalk-chat` 的消息标识字段（conversation_id、message_id、source_message_id、open_dingtalk_id、user_id、recipient_open_dingtalk_id、sender_open_dingtalk_id、verified_participant_open_dingtalk_id）：提供的非 null 值必须是含非空白字符的字符串；不把数字转换成字符串。普通 proposal 和人工选项中的可执行 plan 均适用，输出 JSON Schema 同步声明该约束。历史保存结果的 Pydantic 域模型读取保持原样；这不表示旧数字标识仍符合新的输出 Schema，也不改变历史执行或恢复行为。其他 capability 的 target 继续支持通用 JSON 值。

Consumer 的未审核外部效果检测只把写入操作的 provider 回执视作副作用；
群消息列表中的 `conversationMessagesList[*].messages[*].openMessageId` 是历史消息身份，
不能据此判定 Consumer 发送了消息或阻止后续安全恢复。检测只读取 provider 的
`data` / `result` / `provider_result` 结果封装中的回执，MCP `content[*].text` 与
`structuredContent` 先按传输封装解码；正式发送工具的 `provider_result.result.openTaskId`
同样保留为执行证据，不递归
业务正文、历史消息样本或其重新组织后的预览。拒绝结果不作为已接受的效果，真实
发送结果继续保留回执的出现顺序并去重。这是诊断证据边界，不改变审核、发送授权或恢复规则。


Audit 返回 approve、return、reject，必须绑定 candidate_digest 和 proposal_revision；failed 只表示技术失败。Audit 不修改正文、选项，不返回执行回执，也不自行创作另一个人工问题。return 允许保留正文并补足证据；reject 要求实质改变被驳回内容，不能只改描述、元数据。首次提交最多三次内容重提，耗尽为 failed，技术失败不占内容预算。

规则问题也先经过 Audit。Audit 检查：适用规则是否确有缺口、规则/代码/Skill/记忆/会话/读取是否可自行解决、当前事实是否应由来源人补充、是否把技术失败或当前实例决定伪装成规则选择、上下文和理由是否充分、2–4 个规则选项是否互斥可行且确有差异、每个执行分支是否完整并有依据。仅当前版本 approve 后才进入 `needs_human` 并通知；审核通过不等于业务 done。

选择接口提交 candidate_id、review_id 和 option_key，服务从该不可变候选中读取唯一匹配的选项；选择记录只保留引用和选项键，不复制分支正文。首次选择独立记录并将原 execution_generation 唤醒；既有 generation、review、失效及最新候选校验通过后，相同选择幂等，冲突选择拒绝。较新候选不会改变历史选择绑定，但旧候选的再次选择仍按原校验拒绝。有效分支直接由系统执行，不重新启动 Consumer/Audit。补充文字保留为新输入，使旧候选失效并进入新完整候选和审核；历史选择、问题和回执保留。选择事件不等于执行完成。反馈可以同时标记一次性应用与 Skill 更新；只有明确获选的 Skill 更新才进入受控 Skill 发布流程。

Attempt 详情仅在当前状态要求人工决定，或该候选已有历史选择时显示人工决策依据；未选择且已失效的旧候选保留在历史数据中，但不再向用户展示为当前待决定事项。

旧长期规则问题仅通过显式单项命令 `python -m app.rule_question_retirement --attempt-id <id> --authority <confirmed-contract>` 退役，默认只读预览；应用另加 `--apply --verified-backup <path>`。资格限定为当前业务对象、当前执行代、最新 Attempt 所绑定的已完成 Consumer、done 任务、完整无技术错误的 task_class 问题，且该任务没有新候选；不批量排除旧结果。应用只原子写入 resolved_at、resolution 与包含原结果 SHA256/任务/运行/执行代/契约依据的退役回执，保留原 send_status、模型结果、任务状态和外部回执。详情、History 和队列将该已退役问题显示为 skipped 并解释依据，重试 POST 拒绝。它不是人工选择、Audit 通过或业务完成；未退役和当前无效问题继续由原质量检查报错。预览及应用均不初始化或迁移数据库。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，`stage_index`、`predecessor_review_id` 绑定前一已完成阶段的回执；阶段上限独立于每阶段三次内容修订预算。Consumer wire 仍携带这两个字段，但解析器用编排器提供的当前阶段绑定覆盖模型回传值。Audit 返回后的同阶段修订保留原阶段和原前置审核；只有前一阶段审核通过且动作回执已验证，编排器才允许下一阶段。候选存储继续验证服务绑定的代际、阶段、审批结论和执行回执。

Audit 返回或拒绝后，Consumer 必须提交当前阶段的完整修订候选。服务从当前编排上下文绑定 `stage_index` 与 `predecessor_review_id`，模型复述错误不会将候选误判为跨阶段或错误前置。返回反馈不会推进阶段；仅当前阶段审核通过且其动作有已验证的完成回执，后续阶段才会开放。候选的阶段、前置审核、代际及执行验证仍由服务严格校验。

主页面 `/` 只是同一 Service Runtime 的一个输入和展示入口。页面 turn 使用 `workbench`
workload 进入 `RoutedCodexExecution`，共用模型路由、会话、runtime attempt、失败切换和 CLI
原生 `auto_review`；页面不启动自己的 Codex runtime，也不定义独立审批策略。
旧版确认记录只读，不能从页面或 API 恢复执行。
Workbench 的同步执行线程在一个 turn 内复用一条 SQLite 连接，减少大 schema 的重复装载；每次 Store 操作仍独立提交或回滚，turn 结束时关闭连接。心跳和 runtime 回调线程各自打开连接；只读快照仍使用独立的只读连接，不跨线程共享连接。


主页面事件订阅回归先用 React `act` 完成时间线及其副作用，再等待真实 EventSource
订阅建立后注入事件；页面文字出现不代表订阅已经建立。合并刷新和切换任务取消刷新
的断言保留，不靠延长超时或跳过断言处理测试竞态。

Workbench 下载的路径替换回归以打开时的文件身份和实际成功关闭事件验证描述符生命周期，
不在请求结束后用数字描述符是否可 `fstat` 判断泄漏；数字描述符可能已被并发线程复用。
测试只替换 API 模块的 OS 访问代理，不修改共享 `os` 模块；遗漏流关闭的负向回归必须失败，
且仍须验证路径被替换为符号链接后只返回原文件，不返回链接目标的内容。

模型路由的名字：只有 `codex_oauth`、`claude_oauth`、`friday_runtime` 三条内置路由名字固定；其余
路由（含名为 `codex_api`、`claude_api` 的）都是添加的线路，由 `CEO_RUNTIME_<名字>_*` 描述、可改名，
运行时规则按路由种类而不按名字判断（详见 `docs/architecture.md` 的「Agent Runtime 路由模型」）。
添加的 Codex 路由可设置独立的 `REASONING_EFFORT`（`low`/`medium`/`high`/`xhigh`）；
留空沿用全局 Codex 默认值，任务本轮显式指定的值仍优先。路线改名和删除会同步处理此设置。
旧的 `CEO_CODEX_API_*` / `CEO_CLAUDE_API_*` 在服务启动时由 supervisor 先行一次性迁移，名字不变，
迁移前备份 `.env`。添加的线路改名由服务端一次完成：先在一个数据库事务里改定时任务首选线路、可派发的
定时运行快照、会话续接、线路暂停和能力快照，再改 `.env`；运行尝试历史保留旧名。重启后生效。

## 标准生命周期

```text
pending -> processing -> done
                     -> failed
                     -> needs_human
                     -> skipped
```

- `pending`：已持久化，等待执行。
- `processing`：正在执行，或正处于“审核 Agent 要求修改、执行 Agent 重跑”的反馈闭环中；
  闭环期间任务始终停留在这个状态，不切到单独的“待反馈”“待修正”状态——修改请求记在
  `AuditAgentResult.feedback`（`rule`/`observation`/`requested_revision`），修正次数记在
  `proposal_revision` 计数器上。
- `done`：逻辑完成且结果已持久化。
- `skipped`：判定为不可执行或不必执行，主动收口，不再重试。
- `needs_human`：当前完整 Consumer 规则问题已通过 Audit，只等待对适用于这一类任务的 2–4 个规则选项作出反馈。选项必须标记 `applies_to=task_class`，不能要求 Derek 决定当前具体事项；当前事实问题应由 Agent 通过普通、安全的 proposal 处理。历史问题和未经审核的 Consumer 输出不能作为当前人工待办。分数是结果分类的硬门槛：信息完整度低于 0.5 时先 ask-back；否则只有高风险且置信度低于 0.5，或规则覆盖低于 0.5，才可能进入 `needs_human`。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败；必须保留失败原因和阶段。

### 失败与人工决策通知

`failed` 和 `needs_human` 都使用同一条通用本地通知链路，不按业务频道假定目标是钉钉。通知携带当前
`attempt_id`，点击通过 `/open-attempt` 打开对应审计详情；若存在已打开的 CEO 页面，由 Service Worker
导航到 `/attempts/{attempt_id}`，否则由本机通知回退直接打开该详情页。钉钉会话只能作为额外上下文入口，
不能成为邮件、听记、任务或其他来源通知的唯一点击目标。

### 失败回复的自动恢复与 Attention 终态

`audit_revision_exhausted` 的钉钉回复任务，在同一会话读到发送者身份已验证、非当前用户且严格晚于失败时间的新消息时，
自动以该批消息身份生成一次新的执行代次。当前用户自己的消息和缺少发送者身份的记录不触发重放。相同来源修订幂等；
只有旧任务仍为该错误、代次未变、没有活跃 run、发送回执、
受控动作执行回执或 provider 风险拒绝时才入队。失败后没有新来源不重复空跑；已发送或风险拒绝任务不自动重放。

OA 扫描按已验证的当前用户 ID 归属待办，任务节点凡未处于终态（包括 `PENDING`、`IN_PROGRESS` 和
无状态记录）都作为未完成节点，不只识别 `RUNNING`。来源不完整或所有者无法验证时，不把本地状态当作审批完成。
如果失败回复的当前代次审核候选指向精确 OA 流程和任务，扫描器只有在实时详情同时证明流程及该任务节点均为
终态后，才记录外部读回证据并以代次 CAS 将该回复任务收口；OA 仍待办、ID 不匹配或读取失败都保留原状态。
Attention 只投影当前未解决状态；已完成／已跳过任务不继续显示，旧 OA 游标也须经完整实时读回才可清除。

高风险 OA 同意还须核验当前用户对当前实例、当前任务的明确授权。材料完整、会议结论或
定时任务的概括性授权均不能代替该授权；缺少时不执行外部动作，保留对象身份和
`authorization_required` 错误供人工确认后重试。此门禁与证据质量评分分开，不能通过
调低 `confidence` 或 `rule_coverage` 伪造 `needs_human` 分类。

已有 typed proposal 只代表待审核的动作，不代表真实外部授权。Audit 返回
`authorization_required` 时，解析器保留此错误，不将其误判为无效 JSON 或普通 CLI
确认参数问题。服务认定为不可重试的授权缺失，在 Consumer 和 Audit 两条路径都落为
`failed_terminal`；再次调度以及任务上已保存同一错误码都不能触发自动重试。
这不会授权发送、伪造 `needs_human` 或修改历史失败；明确授权后的恢复仍须走正式入口。

当前代次的最新 Attempt 指向失败 run 时，即使关联任务进入 `pending` 等待重试，History 与 Attention
仍显示该失败，直到后续有效 run/Attempt 给出新的当前状态。没有当前代次失败 run 的 pending
任务本身不进入 Attention；旧代次失败也不污染新代次。对于同一定时 Agent task，Attention 只统计最新的已派发 run：较早 run 的失败 Reply task、失败 Attempt 及绑定该 run 的读取错误仅保留在 History，不再作为当前 Attention；最新 run 失败时仍显示其失败。未派发触发和其他 scheduled task 的成功均不覆盖当前状态。
从失败 Attempt 手动重跑时，Consumer 与 Audit 的上下文必须带入来源代次的结构化 Audit 反馈；
旧候选被否决的点仍须解决，或用新证据明确说明其不再适用，不得把相同候选当作未审核的新建议。
已结案的 `skipped` OA review 仅在审核者提交明确反馈、同一钉钉 OA task 仍为 pending、流程/task
身份仍匹配且原审核没有外部动作回执时，允许手动创建新一代 Consumer→Audit 审核。该入口不重开
`needs_human`、其他 skipped 项或已有外部动作的 OA 审核；新的业务动作仍须经过原有审核与效果回执。
旧 Attempt 即使缺少 `resolved_at` 也可进入同一判定；跳过状态、任务身份、反馈与无外部效果条件仍须全部成立。
Codex CLI 报告同一 session 有其他 active writer 时，运行时将其视为本地 session 冲突，
在原路由和原 session 上限次指数退避重试，不因此暂停整个 provider。
服务启动时恢复被中断的会议作业，会在同一事务内将作业改为 `retry`、关闭运行记录并释放
该作业的 dispatcher claim；不再等待旧进程的租约自然到期才允许重新领取。
会议群消息发送的 DWS 可重试故障即使超过普通尝试次数，也保留同一任务和投递键进入
`retry`，按共享指数退避且最长等待 15 分钟；错误记录须保留 provider 原因。只有非外部
依赖故障才按普通尝试上限进入 `failed`，重试前必须核对本地回执及可用外部读回。
旧版本已落为 `failed` 的会议发送，只有在原决策、原群目标完整且本地无发送回执，
并独立确认群内未收到该摘要后，才可受限恢复至 `ready_to_send`；恢复保留原正文和投递键。

### 发布期间单实例调度暂缓

`python -m app.reply_task_deferral --db <absolute-db> --task-id <id>` 默认只读预览，
不初始化或迁移 Store。2026-10-05 的明确操作授权仅覆盖原 386130/386131，不能扩展到历史
风险拒绝或其他业务任务。显式 `--apply` 必须携带预览的业务对象、执行代、输入版本、最新失败
Consumer run ID/指纹、独立验证备份、原因、请求 ID 和 60–86400 秒的有界时长。
模块在 `BEGIN IMMEDIATE` 中再次核对所有身份、原生运行、租约、Dispatcher 活跃所有者、
候选及可归属的真实/未知效果（含本触发的发送记录）。仍有活跃所有者就拒绝，不能强制抢占。
仅在无这些阻断时把原任务留为 pending、设置未来 available_at，并同事务保存独立调度回执；
输入、代次、尝试数、错误、Agent 历史和外部回执保留。预览中的已暂停任务明确显示 already_paused。

正式部署后，`--resume --receipt-id <id> --request-id <id>` 再次锁内核对该回执的原身份、
失败指纹与精确延迟，确认没有新工作或效果后恢复原 available_at；它不创建新任务或代次，
也不是业务成功。相同请求幂等，不同请求或中间变化明确拒绝。CLI 每次只做一个原子操作；
发布方的有界等待只轮询只读资格，每次应用仍独立复验。现有不透明出站键只有通过任务的候选、
动作、运行、工具和发送事实才能归属，不能用不相关的全局出站记录阻断一个实例。

## 功能机制开关与任务生产

邮件分类使用独立的 `email_agent_classification_tasks` 持久化队列。每封邮件是一次经共享
Runtime Router 的 Agent 轮次（`EmailClassifierRoutedBackend`），与其他 Agent 轮次使用同一条线路
顺序和统一 fallback；没有哪条线路名是邮件分类专用的（Derek 2026-09-24）。此前的「直连名为
`codex_api` 的线路的 Responses API、失败再走路由」主路径已删除，代价是每封邮件变成一次完整的
命令行轮次，更慢、更耗额度。离线训练标注命令 `app/email_training_labeler.py` 同样走系统模型路由（Derek 2026-09-25，不再有
`--route` 直连）：路由只运行服务记录为运行中的工作，所以标注命令为每次分类临时登记一条运行中的
分类工作项（`EmailClassificationTaskAdapter.open_offline_task`，不含邮件定位、不可被扫描领取，分类结束即删，
启动时清掉崩溃遗留），再交给同一个 `EmailClassifierRoutedBackend`。
分类器 Codex 调用设置原生 `project_doc_max_bytes=0`，因此不会把仓库 `AGENTS.md`
项目说明重复加入这一封邮件的判断输入；服务仍提交冻结分类 Skill、当前邮件、允许类别及退订候选，
结果仍按严格结构校验，并只用候选索引绑定本轮原 URL。其他任务的原生项目文档设置不变。
同一 Codex 命令使用既有 `skills.config` 目录选择器排除与分类无关的原生 Skills，
并继续排除已内联的 `ceo-email-classifier` 原生副本；默认常驻插件入口可保留。
目录选择在命令构建时读取原生 Skill 根，服务不复制或维护另一套静态清单。
Claude/Friday 路线、完整冻结 Skill 正文和分类结果合同不变。
分类请求允许正常的模型响应时间；临时网络、超时和租约中断在同一任务的四次尝试预算内（首次调用加三次 retry）按共享
指数退避重试，重试间隔最多 15 分钟；第四次失败进入 `failed` 并保留实际错误。输入、契约或
持久化冲突等不可重试错误直接进入 `failed`。修复根因后的显式重试重置当前预算并保留任务身份。Status、Attention 和每小时 quality gate 都必须覆盖该队列，不能只用
Email worker 的汇总健康状态代替任务状态。

`Settings -> Skills` 的开关位于任务生产边界。功能与业务 Skill 的多对多关系由
`data/config/skill-features.json` 声明，运行时开关由 `data/config/skill-state.json`
持久化；每次生产检查都会读取最新状态，因此常驻 worker 不需要因开关变更而重启。

关闭一个功能只拒绝该功能对应的**新任务/新作业创建**，并不会取消、删除或改写已经存在的
`pending`、`running` 或重试任务。消费者领取和继续处理已有任务时不再重新判断该开关，保证
切换不会中断正在进行的生命周期。缺少或校验失败的关联 Skill 会让该功能标记为配置不完整，
并阻止它继续创建新任务；其他功能不受影响。

Skill 发现描述必须对应其正文的调用角色：`ceo-work-tracking` 是 Task Agent 的
Project/Task 结构化读取器，不回复来源；Consumer 的项目文档审阅及来源回复使用
`ceo-document-review`，一般消息判断使用 `ceo-message-triage`。这不改变现有
任务分类、工具权限或 Skill 正文。仓库 CI 资产与实际加载的安装资产分别验证，
不能从 CI 文件更新推断生产已经加载新描述。

## Runtime-managed Skill 修订与启动配置

Settings 不再直接编辑项目目录或 `~/.agents/skills`。每次保存会在 SQLite 中创建一条不可变的
managed Skill revision，保存原始 `SKILL.md`、精确 SHA-256、父修订和来源。项目内八个
service-owned 业务 Skill 只在首次初始化时导入为基线；导入不扫描、不覆盖用户、插件、系统或
operation Skill，也不会把 Settings 编辑同步回任意运行时目录。

一次启用操作创建一个不可变的 `runtime_skill_config`，其状态只能是
`pending_restart`、`active` 或 `load_failed`。它绑定每个 Skill 的精确 revision、启用位、加载
顺序和用途。运行中的 Agent 不读取可变 Settings：服务进程启动时解析候选配置，加载精确修订，
并写入包含 PID、配置 ID 和每个 Skill SHA 的 append-only load receipt；只有匹配的成功 receipt
才能把候选配置变成 `active`。候选加载失败会记录具体错误并保留前一个 active 配置；回滚同样
创建新的 next-start 配置，不会改写历史记录。

Consumer 在一次 invocation 开始时获得这一不可变 snapshot，之后不会在同一个 invocation 中
重新读取 Settings。`feedback_iteration` 是同一配置中的系统能力，不是第八个业务任务生产开关。
它关闭时，反馈的读取、历史和 reopen 仍可用，但 UI 的“处理反馈”保持可见且 disabled，后端也
拒绝新的 claim；已有处理项按受控配置转换返回 pending/open，并保留其历史。

功能与业务 Skill 的多对多关系仍由 `data/config/skill-features.json` 声明，运行时开关由
`data/config/skill-state.json` 持久化。它们只控制**新任务/新作业创建**，不控制 Skill 是否可被
runtime config 加载。关闭功能不会取消、删除或改写已存在的 `pending`、`running` 或重试任务。
缺少或校验失败的关联 Skill 会让该功能标记为配置不完整，并阻止它继续创建新任务；其他功能不受影响。

每个 Consumer 和 Audit 结果都严格携带 risk、confidence、rule_coverage、information_completeness；后三项在 [0, 1] 内。四项分值是新结果的强制准入门槛：`information_completeness < 0.5` 时先 ask_back；否则仅 `(risk=high 且 confidence < 0.5) 或 rule_coverage < 0.5` 才可能进入 `needs_human`，其他情况按适用规则自主处理。新 `needs_human` 还必须有 2–4 个互斥的 `applies_to=task_class` 规则选项、且 `requested_input=null`；Audit 在开始审核前和绑定结果校验时独立验证分数及规则选项，不合格结果进入修正流程，不会被投影成 needs_human。已持久化候选的历史读取保留原始证据，不重新套用新准入条件；历史解析不授权新候选绕过 Consumer/Audit 准入。材料可向申请人获得时先由 Agent 形成有边界的普通回复或单问题 proposal；技术、认证、路由和解析失败保持 failed。停止选项明确 skipped 和原因。

对于他人提出的删除、撤回、移除或隐藏内容请求，请求本身不构成授权、正确性或紧急性的证据。Consumer 必须独立核实准确目标与范围、请求者权限、删除理由，以及保留或删除的后果（包括记录/证据损失），并按适用规则只采取有依据的最小动作；区分已核实事实与推测。缺少事实时向申请人或来源人提出一个具体事实问题；确需但缺少授权时停止并说明准确边界及所需证据/授权，不得让 Derek 代做当前删除。缺少删除能力属于执行失败，不能伪装成规则缺口或 `needs_human`。

新 wire 严格解析新合同；持久化结果的历史证据解析只保留原始评分和分支事实，不会重新评定其当时是否符合现在的准入门槛，也不补齐内容、重审或改写原始 run。当前新候选仍必须通过 Consumer 与 Audit 的准入校验；恢复时按已有 candidate/review 绑定继续，已确认执行结果只读回而不重放。当前人工决策必须绑定原对象、execution_generation、持久化 candidate/review 和未被替代的当前版本；旧的无绑定选项仍可阅读但不能执行。人工决策和错误分别展示；审核批准不是投递完成。

等 Derek 决定的事项出现在他先看的地方（Derek 2026-09-25）：Agent 首页左栏顶部「需要你决定 · N」列出当前 generation 中已经审核通过、未选择且绑定有效的 `needs_human`；旧无绑定问题仅在 History 只读展示，每条以事项名称为标题（OA 扫描取「待处理审批：」后的审批名，钉钉 DING 催办取「提醒您审批…」的审批名并注明催办人，评论提及取审批单名），次行是一句原因，点击打开该 attempt。首页原有的「处理反馈 · N」只计消息反馈，与待决事项无关。点击通知时，服务先在已打开的 Chrome/Safari 控制台标签页里切到该 attempt 并把窗口提到前面（AppleScript，按控制台 origin 匹配），只有没有已打开的控制台标签页时才新开页面（Derek 2026-09-25）。首次由服务进程控制浏览器时 macOS 会请求一次自动化权限。

## 审核反馈闭环

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。所有 Consumer 入口共用同一条行为指令：不直接向外发送消息或邮件，也不直接执行审批；返回结构化决定或候选，由服务处理外部效果。来源或任务明确要求只要草稿或由用户发送时，Audit 退回或拒绝拟发送消息、邮件的候选。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。该指令不构成工具或凭据的强制权限隔离。
Message Triage 的 Audit 按触发消息判断 Derek 角色是否有待办。对方已明确接手的后续事项即使尚未完成，也不单独构成要求 Derek 追问的理由；但 Audit 仍须退回直接要求 Derek 决策或行动的候选，并且不得把对方承诺写成已完成或已核实。

候选分为普通动作计划和任务类别规则问题。`needs_human` 只能针对可复用规则缺口，包含来源上下文、具体原因、证据及 2–4 个互斥规则选项，每项明确 `applies_to=task_class` 并说明后果；选项绑定在所选规则下继续当前事项的完整方案，停止选项写明 skipped 和原因。不能用 `requested_input` 将当前事实、偏好或业务决定转给 Derek；应由 Agent 先基于现有规则形成安全、有限承诺的普通回复，或向有该事实的来源人提出一个具体问题。不能混合立即执行的动作与尚未选择的条件分支。

Audit wire 只返回审阅结论和反馈，不要求模型复述 `candidate_digest` 或 `proposal_revision`。解析器从本轮已核验的 `AuditTurnContext` 注入这两个绑定值，再用同一个上下文核对候选字段断言；严格 wire schema 拒绝模型额外输出绑定字段。恢复已完成的 Audit 原生结果也使用同一候选绑定解析器。持久化的 Audit 域结果仍带摘要和版本号，并保留原有完整内容与敏感值校验。

Audit 返回 approve、return、reject，必须绑定 candidate_digest 和 proposal_revision；failed 只表示技术失败。Audit 不修改正文、选项，不返回执行回执，也不自行创作另一个人工问题。return 允许保留正文并补足证据；reject 要求实质改变被驳回内容，不能只改描述、元数据。首次提交最多三次内容重提，耗尽为 failed，技术失败不占内容预算。

Audit Task 在完整候选旁附上服务从同一候选 JSON 确定性生成的标量字段证据：JSON Pointer 路径、精确字面值和 JSON 类型。字段类型反馈必须和该证据及候选相符；该索引不是额外来源事实，也不改变审核决策规则。

规则问题也先经过 Audit。Audit 检查：适用规则是否确有缺口、规则/代码/Skill/记忆/会话/读取是否可自行解决、当前事实是否应由来源人补充、是否把技术失败或当前实例决定伪装成规则选择、上下文和理由是否充分、2–4 个规则选项是否互斥可行且确有差异、每个执行分支是否完整并有依据。仅当前版本 approve 后才进入 `needs_human` 并通知；审核通过不等于业务 done。

选择接口提交 candidate_id、review_id 和 option_key，服务从该不可变候选中读取唯一匹配的选项；选择记录只保留引用和选项键，不复制分支正文。首次选择独立记录并将原 execution_generation 唤醒；既有 generation、review、失效及最新候选校验通过后，相同选择幂等，冲突选择拒绝。较新候选不会改变历史选择绑定，但旧候选的再次选择仍按原校验拒绝。有效分支直接由系统执行，不重新启动 Consumer/Audit。补充文字保留为新输入，使旧候选失效并进入新完整候选和审核；历史选择、问题和回执保留。选择事件不等于执行完成。反馈可以同时标记一次性应用与 Skill 更新；只有明确获选的 Skill 更新才进入受控 Skill 发布流程。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，stage_index、predecessor_review_id 绑定前一已完成阶段的回执；阶段上限独立于每阶段三次内容修订预算。

Audit 返回或拒绝后的修订仍绑定原阶段及原有前置审核 ID；只有审核通过并且当前阶段动作的执行回执已验证完成，编排器才推进下一阶段。返回反馈不授权跨阶段，也不绕过严格的候选血缘校验。

钉钉引用回复由服务使用 DWS `chat +messages-reply` 投递；该入口核验原消息、发送者与会话一致性，且适用于已确认的单聊会话。投递仍沿原业务对象的幂等键和回执核验，不因引用回复失败自动改发普通消息。

会议总结的业务群候选由正文中的业务词与标题共同检索，排除听记摘要里的时间标签、图片链接等元数据。平台中立的共享群发现服务按标题/摘要与群名筛选，读取完整成员，并以 `member_precision=匹配参会人数/完整群成员数`（3/N）代替参会人召回率。移除至少两人且覆盖60%参会人的过滤条件；业务讨论、主题优先排序，准确率只用于同等业务证据的候选比较。候选保留完整成员 IDs、群人数、匹配人数、准确率及参考参会名单完整性，再交给 Agent 核验业务承接与实际披露授权。当前运行时只有 DingTalk/DWS 适配器，Lark 和 Slack 需要各自 Provider 的认证、读取和外部读回验证后才能启用。发现服务不选择最终目标，也不把 Provider 故障降级成“找不到群”。没有标题命中时仍保留候选做实时名册检查，不能按搜索接口的原始返回顺序截断候选。候选排序依次参考讨论片段分数、标题/摘要与群名的主题分数、人员准确率；词项交集只是检索线索，Agent 必须核对实际讨论、行动负责人和受众。成员准确率仅描述参会人与完整群成员的交集，不证明业务归属或披露权限。历史投递只报告次数，不再赋予 verified_recurring_group 标记，仍须核验当前实际内容和完整受众；此前已发送的总结不会因路由修复自动重发。

候选群消息读取明确返回 DWS `code=1001`、业务原因「该群为保密群，无法获取消息记录」，且 `retryable_external_dependency=false` 时，适配器保留原始拒绝，不包装成瞬时错误。会议任务记录 `meeting_group_discovery` 失败并释放执行锁，不继续自动重试；权限或可读取的可信来源恢复后才走正式恢复。该拒绝既不是空消息结果，也不允许跳过该群而改投其他群或组织者。未知读取错误以及 provider 明确标记可重试的故障仍沿原有重试路径处理。

对于源单聊的澄清动作，Consumer 必须在 action target 中提供已经通过实时读取确认的参与者 `open_dingtalk_id`。若该参与者字段以 `verified_participant_open_dingtalk_id` 表示，System Executor 使用候选中已确认的同一稳定接收人身份执行单聊发送；不会把 `conversation_id` 当作群聊目标。
若单聊候选同时带有原会话的 `conversation_id` 和收件人的 `open_dingtalk_id`，发送入口在核对会话与原任务一致后只保留收件人目标，不把会话 ID 当作第二个互斥目标。DWS 的单聊写入口实际要求 `receiverUid`（userId），因此服务会用原始触发消息中与该 open ID 精确匹配的姓名和 open ID 解析 userId，再只把 userId 传给 DWS；解析不到唯一 userId 时在 provider 写入前失败，不猜姓名、不直接把 open ID 当作 receiverUid。群聊或不匹配会话不作此转换。

当 Audit 因临时授权映射或运行配置失败后被重新调度时，恢复会创建下一次 Audit turn；已失败的 turn 保持历史记录，不能被重复领取。

每个队列任务的 `Original trigger` 是该任务唯一的权威输入，由
`trigger_message_id` 标识。近期会话消息、材料和实时读取结果只能补充事实，不能把
Consumer 的任务改成另一个消息、日程或审批事项。Audit 返回 `return` 或 `reject` 后，服务
必须把规则、观察结果和修改要求传给下一版 Consumer proposal，再创建对应的 Audit run；
Audit 只反馈修改要求，不直接替换 Consumer 的业务正文。
### Task Agent 的项目、任务与判断（已发布；业务验收部分完成）

2026-10-04 获批设计的代码经 PR #16/#17 发布；2026-10-06 22:34 PDT 读回生产为
`f4a889df`，包含项目 Memory 写入规则及当前来源项目引文的 bounded repair 修复。
生产 input `27465` / run `10690`
保存了 4 份 ProjectContext 和 2 张零 Task 成员的业务关注卡片。input `27497` /
run `10691` 的项目引文不连续，被领域校验拒绝并回滚，原失败保留；其后的
run `10692` 虽 completed，但只判断会议主题，具体项目覆盖不通过。后续副本对照
仍有引文纠正耗尽和旧关注卡原证据声明错误，详见验证记录；不把修复发布当成业务通过。
历史项目覆盖及近期多来源结果尚未全部达到设计预期。
当前预期/实际、发布与未完成验收见 `docs/task-project-centered-validation.md`，
不能把健康检查、非零关注或已发布代码单独当成全部业务效果证明。

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

Project 可选关联 Fxiaoke CRM AccountObj，持久化稳定 `_id` 与 CRM 显示名称，不修改 Project 标题；内部 Project 可以不关联客户。Task 不保存重复客户字段，只有关联到已确认 Project 的 Task 才在读取时继承其客户名称，独立 Task 保持无客户。Task Agent prompt/Skill 引导写入当前证据支持的持久项目风险和重要进度/状态更新，保留原始来源引用与来源时间，区分来源事实和有证据的风险推断；不写单条 Task/TODO、日常或临时信息、无依据猜测、秘密、原始转录及重复更新。不增加工具白名单、通用只读模式或 CLI/MCP 写入拦截。

CRM 查询使用 `sharecrm data record query-by-name` 对 AccountObj 做只读名称解析，不写 CRM。Agent 提供的 `crm_customer_evidence` 必须同时、完全一致地出现在 Project `evidence` 中。该解析器不能证明候选精确或穷尽；Task Agent 和 Project 页面返回的每个候选（包括单条候选）都保持未关联，只有用户明确确认后才建立本地关联。Resolver `NO_MATCH` 与 CLI/认证/查询不可用分开记录。查询失败或后续冲突不会清除已确认关联。客户汇总按 CRM `_id` 分组，只包含已确认关联的 Project；未关联项目仍在常规项目视图。

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
这个段落是原共享正文存储方案。当前未发布 B/C/D 运行时的四个 Store Signal 读取叶节点返回
不含正文的事实与完整来源绑定；旧 `business_source_documents.body` 在此阶段仍是唯一选定的
权威正文，读取叶节点只短暂用它计算身份键、SHA-256 和 UTF-8 字节数。独立的请求读取器
在写事务外按精确身份键解析正文，同版扇出一次；没有 provider 失败后旧表兜底。
Task Agent、ProjectContext、建议、检索与来源包共用请求内的按 Signal ID 区分的事实和
按身份键共享的正文。决策预检在写事务外准备历史引用；写事务内重新核对不可变绑定和
相关既有证据关系，只用已准备的正文验证引文。当前 WorkItem/Signal 的已提供正文可直接
注册，无历史来源 IO。上下文检索和其后决策预检是两次独立读取请求，各自只读取同版一次。
仅需历史元数据的接受和身份比较也在写事务内核对预检事实与完整绑定；旧原文不可用不阻止
这类判断。主业务提交后，Attention 投影在其自己的写事务内再次核对已准备的既有卡片、
成员及 Project 原证明，然后在同一事务中写卡片；本次主业务新建的 Task 链接不作旧关系重放。
API 使用有类型的来源视图：可用时正文非空且无错误，不可用时正文 null 且有原因；
已保存引文、业务事实与来源链接保留。2026-10-18 已授权永久移除 20 个例外版本的原文，
保留事实/来源/回执；物理删除尚未执行，也不属于这个运行时单元。
原生来源定位器只在调用方提供的真实 runtime/session 和完成的本轮行号范围内，
查找现有提示词中的完整来源 JSON 字符串；输出行/内容路径、字符区间和校验值，交给精确读取器。
部分引文或本轮范围外的匹配不能替代原文，不写 trajectory 或正文副本。当前定位原语尚未接入
Signal 持久化，新写入路径的完整集成仍属最终迁移验收差项。
开发中的无正文 Signal DAO 已使用最终 `_kind` 与固定 JSONB 槽位。首次使用在 Signal 内联来源；第二个使用者才将同一来源提升到 provenance，通过置空来源槽位并设置外键完成，不移动数组槽位，也不改变业务事实和公开 ID。第二次插入失败时，现有调用方事务内的 savepoint 回滚整个提升，其他证据引用保持。该组件不保存原文；Store 尚未选择最终来源结构，历史正文未退休、生产未发布。

AI Minutes 的来源定位包含 transcript_excerpts 时，必须同时记录真实生成格式
`transcript_excerpt_format`：当前生产者为 sentence_v1，已核实历史整段格式为 paragraph_v1。
两者沿用相同的前六段、后四段窗口，仅行序列化不同；读取严格选择指定格式，缺失或未知格式
在访问 provider 前返回无效定位，hash 不匹配也不尝试另一种格式。没有转录片段的定位不保存
格式字段。这是本地精确读取能力，不表示已选择最终物理表或删除历史正文。
当前业务 schema 是 `2026-10-04.4`，不等于生产已迁移。

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
语义校验，失败时最多在同一 session 修正一次；结构化输出不取代业务校验。Task 任务正文不重复附加完整 schema；Codex 使用上述原生通道，Claude／Friday 由现有运行适配器将同一份完整 schema 拼入单条消息。Skill、事实、业务规则和会话绑定保持不变。
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
source_documents 以完整来源绑定身份键提供精确版本的共享正文一次，source_signals 保留引用 ID、来源和业务元数据并指向正文；数据库内部 dedupe_key 不进入 Agent 输入，持久去重键及其查询行为不变。
同一轮 Task semantic context 中完全相同的 `visible_ranges` 与 `decoded_excerpts` 只序列化一次，其余文档用 `duplicate_of_document_id` 指向首份投影；文档身份、元数据、信号和引用保持独立，展开后与原始 source bundle 完全一致。若压缩后的完整 bundle 不更短，则保留原始表示。
Task 输入里，当前 WorkItem 的正文就是 Work Item JSON 里的 `summary`，只出现一次；语义上下文不再含
`current_work_item`，也没有 `source_metrics`。来源绑定带外部定位信息（provider 或 native 定位，`source_ref`
本身就写明听记 ID 或钉钉文档节点）的历史来源不再复制进输入：其文档只有 `document_id`、
`source_in_input: false`、`body_utf8_bytes` 和已保存的引文 `saved_quotations`，Agent 自己用 dws 读取原文，
Task 运行可见 `dingtalk-minutes`、`dingtalk-doc` 这两个 Skill，并可用 ai_services 的 `transcribe_audio`（听记转写
不可用时的提示里说明）。服务只在决策引用某条来源时才读取其精确绑定版本来核对引文（`PreparedSources` 惰性读取），
不再为拼提示而读取每条历史 Signal。没有定位信息的来源（Console 动作记录）无处可读，保持原有投影：
2048 字符预算内保留首尾及关键引文，`visible_ranges`/`full_length`/`truncated` 和引用超预算明确显示，JSON 解码引用有实际 leaf 路径/偏移。
Task 要求路线具备 `local_process_execution` 能力（Codex CLI 与 Claude CLI 具备，Friday Runtime 不具备，因此不再被选去执行 Task），
调用方不按路线分支。回复任务（reply task）的上下文拼接不变。检索排名不证明身份、分工或业务重大性。

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



## Business Object、Task、Agent Run 与 Reply Attempt

运行时先用 `business_object_key` 识别同一外部业务事项，再使用三层运行对象：
`reply_task` 是可领取和重试的唯一当前队列投影，`agent_run` 是一次真实的
Consumer/Audit 执行，`reply_attempt` 是稳定的业务结果当前投影。一个 task 可以有
多个 agent run；重跑不新建业务 attempt，而是在原
`reply_attempt` 上更新 current projection，并把新的 agent run 追加到历史。

```text
business_object 1 ── 1 current reply_task
reply_task 1 ──< reply_task_inputs
reply_task 1 ──< agent_runs
business_object 1 ── 1 current reply_attempt
```

`reply_task_inputs` 的输入身份、版本和来源摘要保留；准确正文只供未结算的执行和恢复使用。符合既有终态结算条件后，当前任务、其输入版本及 Attempt 的输入正文改为来源引用与准确摘要，业务结果与执行回执不变。原生过程和准确原文缺失时明确不可用，不由其他结果拼造历史输入。

Store schema `2026-10-11.1` 中，`reply_tasks.current_input_id` 与
`claimed_input_id` 直接外键引用所属输入行。父表不保存正文、provenance 或精简标记副本；
当前展示读取 current，当次队列领取后的执行和冻结 Skill 按代际校验读取 claimed 快照。
再次领取仍捕获当时 current 输入，沿用原有恢复及版本水位行为。
输入变化与指针、版本水位在原事务内更新；`input_version`／`claimed_input_version`
仍用于原有并发判断。终态输入正文及重复精简 JSON 为空，旧 API 表示按 provenance 生成。
已结算的真实历史身份不删除，无法证明的旧领取输入不推测。迁移必须在正式 quiet 停止窗口执行。
配对逆迁恢复实际原表声明、索引、序列和旧正文投影，保留迁移后新增业务结果与回执；
逆迁失败时沿用既有发布机制保持 replacement 停止。

新建 Task 工作汇总输入只移除 Task Agent 不注入的 `scheduled_consumer.skill_materials`。
共享 Consumer 上下文、prompt、Skill 名称、协议、来源及既有输入保持原样；
WSI 原有无损压缩和终态清理继续执行，应用边界仍返回准确 JSON 字符串。

OA 的稳定身份是 `process_instance_id + task_id`。同一 OA 从 webhook、pending scan、
用户人工反馈进入时追加 input 并复用同一 task、同一兼容 Consumer session，在当前 run
结束后提高 generation 并重新排队同一 task。服务修复运行时、路由或本地执行环境时，虽仍
复用同一 task 和业务对象，但必须创建新的 execution generation，以隔离不可变的旧 run；只要
provider session 仍可访问，就继续该 session 并在新 turn 注入当前 Skill/契约。只有 provider
明确返回 session 不存在或认证上下文失效时才清除绑定；这两类重跑都保留旧
run/session/provider 事实。

OA 通知不等于 OA 执行任务。来自 `OA审批` 的系统通知只写入
`oa_notification_events(event_kind=system_notification)` 并标记已读；聊天窗口中形如
`[Ding]…提醒您审批` 的催办写入同一表的 `chat_reminder` 事件，保留原会话、原消息、发送人和
原生回复所需的消息 JSON，但不创建 `reply_task`、`agent_run` 或独立审批 Session。定时
`scan-oa-approvals` 读取实时待办后，如果当前用户恰好只有一个 `RUNNING` 节点，就把没有
`task_id` 的催办事件认领到该节点；没有节点或有多个候选节点时不猜测，继续等待下一次扫描。
实际审批仍只由定时扫描创建的 OA `reply_task` 执行。System Executor 保存并回读 OA
动作成功回执后，服务使用 `oa-reminder-result:{event_id}:{attempt_id}` 的稳定投递键，对原催办消息
做一次原生引用回复；发送失败会释放事件等待重试，已发送事件不会重复回复。这样通知页、
系统通知和聊天催办都能作为同一个 OA 案例的观察输入，而不会重复启动审批。

DingTalk 日程卡片改期可能原地覆盖卡片内容而不产生新消息 ID。每小时的近期消息恢复读取
该消息当前可读内容；若它和当前 task 投影不同，且实时日程仍有效、本人仍待响应，就以新的
`input_revision_key` 追加一条 `reply_task_inputs`，提高 `input_version`，并为同一个 task
开启新的 execution generation。相同卡片快照不会重复入队，已经接受、拒绝或取消的日程也
不会因此重新触发。

评论通知只有 `process_instance_id` 时，运行时从事件正文提取该身份；若该实例只有一个已知
节点，则归并到该节点。存在多个节点而事件没有 `task_id` 时不得猜测。

`reply_attempt` 的 `agent_run_id` 指向当前投影对应的最新或终态 run；执行历史状态
通过 run 的 task、generation 和关联事件查询。Attempt 页面可以切换多个 Consumer
或 Audit run，但不能编辑或覆盖旧 run。失败状态、session、runtime attempt 和采用的 provider 回执继续保留；原始失败与 tool event 从原生记录按需读取，来源缺失时明确不可用。
Attempt 详情的每条 runtime 记录分别提供 `status`（运行时调用状态）和 `run_status`
（所属 Agent run 的业务运行状态），页面分别标注两者。模型调用返回 `completed` 不代表
业务审核通过、外部动作成功或业务 run 完成；业务 run 仍可能是 `failed` 或 `running`。
此处只投影已有状态，不改写历史记录、Audit 决策或恢复资格。
Session 可用性测试同样按真实 `AgentRun` 提供业务 `status`；缺失 transcript
只影响 `session_available`，不改变业务运行状态或运行时调用状态。
Workers 的当前 attempt 队列统计会排除 `agent_run_id` 指向旧 execution generation
的记录，即使同一业务对象更新了 trigger message；旧 attempt 仍可在历史详情中查看。

对进入 Consumer/Audit 的 task，当前投影结合当前 execution_generation 的候选、审核、人工选择、System execution 和真实回执。模型 run 完成或 Audit approve 不等于动作成功。System 已开始的精确动作在同一 candidate/review/generation/selection 下恢复；确认成功的持久化回执可完成执行投影，不要求为取得回执重新运行模型。无法确认的外部结果保持 uncertain，不取得再次发送资格。自动技术重试在同一 generation/revision 追加 turn；明确的人工 rerun 或来源补充通过正式入口创建新 generation；旧 run、attempt、session、runtime event 和回执保留为历史事实。真实 pending/processing 仍显示等待/执行中，旧 generation 的失败不覆盖当前进展。

`needs_human` 的有效绑定选项对应已审核的 task-class 规则分支，由 System Executor 执行；停止选项按原方案写入 skipped 和原因。历史 `requested_input` 或新增事实作为新输入进入 Consumer/Audit 生成并审核新 revision，不能改写已经批准的方案，也不能将历史输入问题重新激活成当前 `needs_human`。旧无绑定选项不可执行，未收到规则反馈时不猜测决定。

Console Attention 同时投影当前队列故障和当前有效、已通过 Audit、尚未选择且仍未解决的 `needs_human` 决策。普通 `pending`、`processing` 或仅因状态为 `needs_human` 的记录不因此进入 Attention；人工决策行必须仍绑定当前 task/generation 与最新有效候选。Status 的 Attention 总数与 `/api/console/attention` 使用相同口径，避免把需要用户选择的已审核事项留在单独列表而漏出 Attention。

如果 Derek 已直接在钉钉完成 OA，定时 OA 扫描会读取该 process instance 的实时状态。
实例已进入明确终态时，服务不重放审批动作，而是把当前 `needs_human` attempt 收口为
`skipped`，保存实时结果作为 resolution，并从当前 Attention 移除；原 Agent run 和决策
内容仍作为历史事实保留。仍为 `RUNNING` 或读取失败时不得自动改写。

### 重复外发故障的统一排查顺序

当人员收到多条相似或互相矛盾的消息时，不得先把问题归因于某一条 prompt 或某个发送命令。
按以下顺序检查同一稳定业务对象的完整链路：

1. 是否有多个 `reply_tasks.business_object_key` 指向同一个 OA 节点、会议或投递对象；
2. `business_object_tasks.reply_task_id` 是否指向当前唯一 task；
3. 每条入口是否只追加到 `reply_task_inputs`，还是错误创建了并行 task；
4. 已成功动作的 `external_action_key` 和 provider 结果是否被后续 run 复用；
5. 外发消息是否只形成一条 `sent_replies` 投影，其他 run 是否只追加 observer；
6. Workers、Attention 和质量门是否只统计 current projection，而不是历史物理行。

典型错误链是“多个入口生成多个 task → 各自反馈和执行 → 一个 task 成功后其他 task 仍运行”。
修复目标是稳定业务身份、唯一当前投影和动作幂等；不得通过删除历史 run、修改旧 tool event、
按人员姓名硬编码跳过发送，或重新引入应用层命令/证据审核来掩盖重复执行。

只有成功的 provider 结果和真实发送记录可以作为 prior execution receipt。失败的 attempt、解析
错误或调度错误不是执行回执，不能被放入“已完成事实”污染下一轮判断。普通聊天中的规则改进请求
也不依赖 Feedback 页面记录；只有上下文明示 `feedback_key` / `batch_id` 时才进入反馈处理轮次。

OA 判断以当前节点的实际表单为边界：不存在于当前表单的字段不能被判为必填，后续阶段字段也
不能提前阻塞当前审批。该规则同时进入 Consumer 候选契约和 Audit 复核契约。

### OA 审批扫描与决策（2026-09-17 重写）

扫描规则：

- **`dws oa approval list-pending` 的行在 `result.values`。** 解析器曾只认 `list` / `items` /
  `processInstances` / `processInstanceList`，因此每一轮都解析出空队列，而钉钉里审批仍在等待，
  最久的挂了两个多月。cursor 里 `seen_process_instance_ids` 为空表示**解析失败**，不是"没有待办"。
- **待办查询窗口按钉钉时区解释。** 扫描器所在主机的本地日期可能与钉钉日期不同；DWS 对
  `--start/--end` 的筛选按日历日期执行。服务先把窗口边界转换到 `Asia/Shanghai` 再调用，避免
  本地深夜创建、钉钉已进入次日的审批被 `end` 日期排除。窗口长度、分页和后续当前用户 task 校验不变。
- **本人的评论不是决策。** 指纹曾在"最新一条操作记录属于本人"时判定审批已处理；分身的审阅评论
  是以本人身份发出的，于是它自己的评论让审批对扫描器永久隐形。`ADD_REMARK` / `COMMENT`
  不终结指纹计算。
- **每条审批一个会话，且每次扫描都从干净会话开始。** 会话按 conversation id 复用，早期所有审批
  共用 `oa_pending_scan`，一个会话里第二条起零工具调用、直接复述上一条的结论，其中一次谎报
  "已执行通过"；改为一条审批一个 conversation id 后，同一条审批的重跑仍会续用自己的旧 transcript，
  因此入队时清空该 conversation 的全部路线会话（`clear_conversation_runtime_sessions`）。
- **唤醒条件**：已经评论过的审批不再按天唤醒，等指纹变动——指纹本就排除本人记录，所以只有他人
  评论或操作才会叫醒它；**没有评论过的**保留每日重看，因为那种是静默丢掉的，没有别的机制能捞回来。
- **通知归并**：OA 系统通知和聊天窗口催办不再各自创建 Agent 输入。系统通知只记录观察事件；
  `[Ding]…提醒您审批` 只记录原消息作为待回复目标。扫描器解析实时当前节点后才创建一个 OA
  执行任务；唯一当前节点才允许归并无 `taskId` 的催办，多节点或无法解析时保持待归并状态。
  OA provider 回执确认后，服务沿原消息的会话和消息 ID回复催办人；该回复与 OA 审批 Agent
  执行分开记账并按事件/Attempt 幂等。
- 扫描任务绑定通用 `dingtalk-oa-approval` 与适用的 Stardust 业务 Skills。曾把
  `dingtalk-misc/references/oa.md` 当作审批规则来源，导致我们自己的审批规则从未进入模型；
  官方技能还会被 `dws upgrade` 覆盖，规则写在那里留不住。

决策规则在审批 Skill 里，不在代码里：通用的完整决策表、
`information_completeness` / `rule_coverage` 评分口径、退回优先于评论搁置、拒绝前必须先查
`revert-activities`、`--remark` 必填，都在 `dingtalk-oa-approval` 中。OA 定时任务冻结绑定
`dingtalk-oa-approval` 以及 Stardust 财务、立项、合同、人员、考勤/出差、云资源六个业务 Skill，本机任务的
Prompt 另写入负责人的个人规则（只存在该定时任务的数据库记录里，代码默认值不含）。未被适用 Skill 明确引用的背景材料可跳过；Skill 明确引用的规则卡、登记表等规范材料必须读取，代码默认 Prompt 不得一概禁止读取参考资料。Consumer 按 live `processCode` 与表单事实
分类，交叉事项组合适用类别；财务规则卡仅约束登记的财务模板，且只提供判断标准，动作按通用决策表。适用业务 Skill 必须覆盖
当前事项的规则条件、例外、权限和动作映射，适用 Skill 完整覆盖时才允许 `rule_coverage=1.0`；其他情况
低于 1.0 描述证据缺口；只有低于 0.5 且缺失的是适用决策规则时，才满足通用质量门槛中的规则覆盖分支。符合门槛仍不等于自动批准、拒绝或升级：Consumer 必须提供完整候选，Audit 复核全部事实和规则，技术或材料缺口不得伪装成政策问题。申请人可补材料但不能替公司制定或关闭政策；材料尚未形成、但申请人仍可协调责任来源方补齐时，先正式退回等待补正和重新提交，不能仅因“当前暂时拿不到”就把本案是否继续交给 Derek。材料阶段完成后，若仍有独立规则、例外或授权边界缺口，再按 `needs_human` 处理。

历史 OA 复核按原动作时点还原证据：只使用能证明当时已可获得的文件/表格版本和评论。动作之后才更新的内容不能倒算成当时已有；只有版本历史或其他可核验时间来源能证明内容及时间，才可用于判断。时间或版本无法核实就标记为“未知”，不能据此认定申请人当时漏交材料，也不能把该轮计为有效退回。
审批服务无法读取已提交附件时，该附件仍视为已提交；权限、下载、解析、OCR 或客户端错误记录为审阅端技术阻塞，审批待办保持未处理。不能要求申请人重复提交同一文件，亦不能把此类错误退回计入“三次后拒绝”；只有独立证据确认源文件自身缺失、空白、损坏或缺页，才走申请人补正分支。财务付款审核必须按附件逐项留存文件名或节点 ID、授权读取结果、可读文本/页数或 OCR 结果及已核实事实；某一附件的读取失败只适用于该附件，不得据此声称其他附件也不可读。证据台账和结果摘要只保留与实质付款条件相关且由当前材料核实的事实；OCR 可读不代表字段与付款判断相关。复核历史退回或拒绝时，只报告有独立证据支持的实质缺口轮数；未计入轮次的历史原因不复述，也不以泛称间接指代。财务 Skill 同时约束付款证据判断：无法核验的验收不等于验收失败；供应商付款审核不评估、请求或在证据叙述/决策理由中复述签章相关栏位或历史要求，不将其作为缺口或拒绝依据；只核对交付、客户验收、结算、金额、收款方、发票及双方对实质条款的确认。已终结流程的历史复核仅产生 `no_action` 分析结果，不提出新的 OA 评论、退回或其他审批动作；重新提交时按新流程当前任务及独立证据审核。多个历史退回本身也不授权拒绝。

对供应商付款，财务 Skill 明确要求完全跳过签字、印章、签章栏的存在/缺失/可读性判断，并在任何内部或对外结果中省略相关观察。它们不是付款条件或补正缺口；此规则只覆盖 `supplier_payment`，不改变其他财务模板各自的证据标准。

供应商付款的第三方客户回复正文未取得，表示客户验收状态尚未核实；它本身不能证明申请人没有提交客户确认材料。只有独立证据确认申请材料确实缺少该项且申请人能够补正时，才向申请人提出具体补充请求。否则保持待处理，不因审阅方未取得第三方回复而留言索要，也不把未知转成拒绝。历史终态流程仅作 `no_action` 分析。

供应商付款复核适用通用 OA Skill 的三次有效退回规则。逐轮列出原退回问题、当时可读且来源核验的证据、申请人的回应及公平补正机会；每一轮都须证明同一项实质、可补正的付款缺口仍未解决。第四次到达 Derek 节点时，只有当前可读、来源核验的证据仍证明该缺口未满足，才可拒绝；不得仅按次数拒绝。读取失败、把“已回复邮件”误述为未回复、要求合同未规定的形式要求、已补正的缺口或不同问题，均不能作为有效退回依据。

财务压力案例：表格显示“已回复邮件”但邮件正文无法读取，已提交的结算图片又发生审阅端 OCR/读取失败，历史退回混合客户确认材料和印章要求时，不能把验收状态从“未核实”推成“未通过”，也不能将读取失败或仅提及印章/签字有无的轮次计入三次拒绝阈值。逐轮核实每次要求、当时的来源证据和申请人回应；若没有其他独立可读证据证明付款条件未满足，审批保持待处理且不执行终局动作。
六份 Stardust Skill 都只存在于运行时目录，没有仓库副本，按 `RUNTIME_ONLY_VERSIONED_SKILL_NAMES`
版本化，**不得带 `metadata.managed_by` 标记**——操作 Skill 目录会拒绝带标记的文件，定时任务
也就不能选择它们。Derek 的个人规则仅写在 OA 定时任务 Prompt，不得推广为公司规则。

### 历史 DWS schema 闸口说明

`oa approval revert-task` 和 `revert-activities` 在 DWS 的 runtime schema 里不存在
（上游 issue #1406），同族的 `reject`、`redirect-task` 正常。命令功能完好，但两道闸口都依赖
schema 判断写操作，因此各自失效过一次：

- **执行闸**：`_execute_reviewed` 判 `agent_cli_command_unreviewed`，退回命令到不了钉钉，
  "退回优先"这条规则写下后一次都没执行过。修法是登记到 `config/mcp-tool-effects.json`
  的受控写名单（`6b6b9081`）。
- **证据闸**：`dingtalk_send_evidence` 的分类器同样认不出它，退回真的执行了、钉钉记录
  `REDIRECT_PROCESS` 且待办减少，任务仍被判 `provider_receipt_missing`。修法是让证据闸
  使用同一份登记表兜底（`ef6f4de4`）。

以上为旧 Agent 命令执行路径的历史问题。当前受控动作注册使用 typed System action handler，绑定精确 operation/target/payload，并由对应 provider 回执和读回来确认；不能依靠旧的命令证据分类器或成功 envelope 推断当前动作完成。

## 外部动作幂等与依赖

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

OA 退回回执按钉钉实际响应字段 `dingOpenErrcode=0`、`result=true`、`success=true` 识别成功；流程操作记录的数字时间按 Unix 毫秒解析，字符串时间按带时区 ISO 或钉钉本地时间解析。若 OpenAPI 操作记录只有秒级时间，读回同一实例的 DWS 结构化审批流水，并按操作类型、操作人及同一秒唯一关联其毫秒 `operationTime`；有多条匹配或来源不完整时仍保留 uncertain，不能放宽派发时间边界。成功响应只允许进入只读对账；旧回执若没有 `accepted_at`，只有在上述成功字段通过校验时才以持久化的 `response_received_at` 作为接受边界。完成仍要求原任务已取消、同一审批人唯一写入精确备注的 `REDIRECT_PROCESS`、目标节点出现对应新任务，且全部时间落在派发和接受边界内。若目标任务创建时间被钉钉截断到秒级，只有当时间桶从退回事件之后开始且完整早于下一操作，或该桶与唯一退回流水落在同一秒、OpenAPI 与 DWS 流水在该秒内均无其他操作且该桶与派发/接受区间相交时，才可将其作为目标节点读回；仍须匹配精确目标 activity 与实例。精确到毫秒的目标创建时间继续按精确事件区间判断，桶与操作边界相交时保持 uncertain。证据不完整时保留 uncertain，恢复不重发退回。

原生群消息和直接用户消息在 provider 接受、但本地回执尚未保存的中断后，恢复读取原精确目标，并比对持久化审核正文、本人发送者、未撤回、dispatch 时间下界及唯一消息身份，匹配后补存回执而不重派。窗口读回使用 DingTalk `im.message-list.v1` 完整分页 contract，并验证时间范围、页数、失败数及结果条数；群聊按会话 ID 查询，单聊按稳定 userId/openDingTalkId 查询。若回复任务明确标记 `input_compacted`，触发消息 JSON 是溯源包装而非 `DingTalkMessage`；具名 reconcile 先按保留的原触发消息 ID 只读读取该消息，并核对会话、消息 ID、发送人和时间均与任务元数据一致，再进行原生完整会话消息核对。压缩输入不得导致普通异常重试或进入 dispatch。新 action attempt 在调用 provider 前原子保存 UTC 毫秒 dispatch 时间；旧无时区秒时间不能作为精确归属边界，保持失败并要求新的精确复检；provider 只返回秒时间时，同秒不足以证明消息在 dispatch 后，也保持失败并停止重发。完整窗口唯一精确命中恢复真实发送回执；完整窗口零个匹配的发送人/正文/引用组合记录为未送达，停止旧候选，仅允许确认无效果后由新候选重试；不完整窗口或多匹配保持失败且不派发。若原始发送在准备正文一致性检查处已失败，新执行先对原目标、审核正文和原始派发边界执行只读完整窗口复检。只有完整零命中才把原 action attempt 记录为 `confirmed_no_effect` 并解除动作键门禁；唯一命中记录 verified 回执。若源任务终止而当前待办归档为 skipped，历史中仍保留未送达事实，不投影为已送达。文档评论和 OA 评论恢复读取原 node/process instance 并保存只读诊断；尚无已验证的 author/time/body/stable-ID 联合归属契约时，不凭相似正文补记成功或再次发送。

`review_candidates` 的执行块保存租约；`candidate_execution_alias` 仅保留原执行物理 ID、公开 ID 和候选 FK，旧形状执行投影连接别名与计划读取当前状态。`candidate_action_attempts` 的 `execution_id` 继续指向别名，在 provider 调用之前保存 dispatch 边界；已验证回执复用。进程中断或超时没有回执不能证明未发生效果：先读取原外部对象。确认成功继续，确认没有效果才可重试同一计划，无法消除的歧义保存 uncertain/failed，不伪造完成或人工问题。技术恢复不重新审核不变业务方案；业务事实改变则使候选失效，重新形成并审核方案。没有通用 shell 或 Agent 执行兜底；未支持的动作明确失败。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

当前 Attempt 的结构化运行结果 `code` 或 `source_code` 为 `provider_risk_rejected` 时，历史“重新处理”入口不可用，直接提交该入口也返回冲突且不入队；API、React 和原生 HTML 优先展示该原因。确需新的候选或执行范围时，应通过明确的本次任务和完整候选提交处理，不能用旧入口重放历史候选。对于后端允许重新处理的其他失败，`rerun_label` / `rerun_confirmation` 仍由同一业务对象的结构化历史提供；跨执行代的历史拒绝可使措辞显示“重新评估候选”，普通技术失败保留“重新处理”。只认确切的顶层结构化错误，不匹配正文、嵌套文字或其他对象；System 的既有历史拒绝限制和原始记录继续保留。
原生运行时的完整调用流保存在 Codex/Claude session 中。服务从当前进程的调用事件或原生 session
的精确调用范围读取它；`agent_run_events` 不再持久化调用事件或精简元数据，
参数、命令、工具输出和从原生记录提取的结果均按需读取。中断运行按原生 `task_started` / `task_complete`
边界恢复证据，既有投递核对和恢复契约保持不变。

Codex 角色使用原生 code_mode_only 和 V8 host。Consumer 的内建 functions 命令与补丁接口在当前 task/generation 的 `/tmp/ceo-agent-service-task-artifacts/<task_id>/<generation>` 目录运行，使用 CLI 自带 workspace-write 沙箱，命令网络关闭、额外 writable_roots 为空；MCP agent_cli 的 cwd 仍是服务源码目录。任务终态清理该任务全部代次目录；未到终态时保留工件供恢复及 Audit 读取。Audit 使用 read-only 沙箱并排除 functions namespace，只有具名读取。两角色的 `read_dingtalk_drive_file` 只下载调用方明确指定的已授权钉盘节点；`read_dingtalk_oa_process_attachment` 按审批实例和表单文件 ID 走 DWS 只读下载地址，不要求通用钉盘节点权限，也不执行授权写入；预览授权不再有服务工具：Consumer 在自己的 shell 里运行 `dws oa approval attachment authorize-preview` 为指定附件开通预览，再用 `read_dingtalk_drive_file` 或 `dws drive +download` 读取（Audit 没有 shell）（评论附件的高级版下载不可用时使用）；`read_dingtalk_oa_comment_attachment` 只按当前审批记录提供的流程实例、评论附件 ID 和文件名走官方 Premium 只读下载；`read_dingtalk_oa_comment_image` 读取审批记录 `images[]` 中没有 file ID 的钉钉图片 URL。该 URL 读取只接受 HTTPS `static.dingtalk.com/media/`，拒绝重定向，并限制下载时长和响应大小。所有材料工具均抽取可读文本；扫描 PDF 的无文本页和受支持的图片材料返回有界的精确源 base64、MIME 类型、PDF 页码与 SHA-256，由 Consumer/Audit 通过服务清单中的原生 `ai_services.ocr_document` MCP 读取。普通 Consumer/非 OA Audit 的 direct-only MCP 命名空间同时包含 `mcp__agent_cli` 和 `mcp__ai_services`。OA Consumer/Audit 把这两个已授权工具放进同一个 CodeMode 调用；OA Audit 的其他已配置 MCP 保持 direct-only，内建命名空间被排除，在 JS 内存中将附件读取结果直接交给 `ai_services.ocr_document`；不把 base64 打印给模型或要求它在下一轮重写。OA 专属会话合同变化只使 OA Consumer 一次性换到兼容的新 session；OA Audit 在 CodeMode 内排除 functions、image_gen、multi_agent_v1、web；其他 Audit 保持 functions 排除，所有任务的既有 session 复用路径不变。服务角色清单仍仅暴露 `ai_services.ocr_document`，其他 AI Services 工具不可见。工具审批配置只对经批准的 AI Services 工具逐工具应用，不改全局审批策略，也不放宽其他 MCP 工具；工具可用性以本轮真实调用为准。保留 OCR 页码、置信度与警告；读取失败返回技术错误，不能解释为申请人未提供材料。MCP 进程退出时清理目录。受控发送与 OA 等注册操作没有暴露给角色 MCP，仍由审核后的 System 执行。所有任务类型的 Consumer 都不得通过 shell、CLI、MCP、browser 或其他工具直接联系他人或执行影响他人的外部动作（包括消息、邮件和审批）；文档写入保持允许。Consumer 只能提交 typed candidate，由 Audit 审核，System 只执行审核通过的持久化方案。Claude 没有普通 shell 执行能力，Friday 仍不具备角色能力。实际工具调用与文件回读证明执行，无工具固定合成业务比较仅证明判断。

`read_dingtalk_oa_process_attachment` 下载后的临时文件路径由材料层显式返回，不依赖 PDF/图片/表格抽取器是否自行附带 `path`。因此表格正文已成功抽取时，两角色都能缓存该准确来源；实例 ID、文件 ID、原字节摘要、大小上限、OCR 分页和 provider 失败语义保持原样。

Codex Consumer/Audit 显式启用原生 `features.code_mode.enabled`。普通 Consumer 和非 OA Audit 将任务 MCP 声明为 direct-only；OA Consumer/Audit 的任务读取与 OCR 由同一个 CodeMode JS 调用衔接。角色工具选择、Audit functions 排除、沙箱和路线不变。超长直接调用结果可能被原生运行时截断，因而不能要求模型复制其中的 base64。

OA Consumer/Audit 处理扫描页时，在当前 Native Codex session 的同一 CodeMode 调用里把读取结果的确切字节、MIME、页码和摘要交给 `ai_services.ocr_document`；只显示 OCR 文本及来源信息。非 OA Audit 和其他角色保持原有直接工具路径。嵌套工具目录、shell 清单和其他 Agent 的目录都不能单独证明本轮工具是否可调用；以实际调用结果判断依赖故障，继续区分审阅端读取失败和申请人缺材料。

补充人工问题验收使用 `evals/consumer_audit_human_review/v1.json` 和 `python -m scripts.eval_consumer_audit_human_review`：冻结两个有效问题与六个语义错误问题，分别调用 baseline/candidate 的真实原生 Audit、完整角色指令和 wire schema，核对候选摘要、修订及审核结果并保存原文。输入是刻意构造且结构合法的候选，不证明 Consumer 实际产生了这些问题；结论仍需独立原文复核，不替代真实发送或生产验收。原十五项业务比较保持不变。 v1 的开放输入正例被独立复核发现含不存在的收件人/双选项说明；原版本与运行结果保留，v2 只修正该正例的质量说明和缺失信息程度，其他七项不变。运行 v2 时显式选择其 manifest，不能将 v1 结果重标为 v2。

需关注中的新审核问题说明适用的任务类别、尚缺的可复用规则和判断依据，并列出已审核的 task-class 规则选项。旧问题的历史状态与选项保留，但不因此重新激活；隐藏旧选项不表示已重新审核或业务完成。

组织架构审批中，空白或“业务调整”等笼统依据属于可向申请人或已核实责任人补齐的事实缺口：先核实接收方，再询问具体业务需要、受影响工作与人员、接收安排和汇报关系；保持待办并独立核验证据。不得仅因 Agent 未查到证据就升级 Derek；`needs_human` 留给真实政策/权限缺口或明确需要 Derek 作出的业务取舍。不得凭未核实陈述直接批准或拒绝。

正式 Consumer/Audit 契约发布使用 `python -m app.deploy --publish-consumer-system-contracts`，在服务空闲且停止、数据库备份已完成后，先校验并将恢复快照交给 updater，再开始替换十四个契约文件（包括通用 OA Skill、财务审批 Skill 与规则卡、people-review 和 attendance-review Skill）、六个 managed Skill 修订和受影响任务的完整引用。发布清单的 `old_sha256` 必须对应部署前实际安装文件，`new_sha256` 必须对应仓库源文件；人工维护的 Skill 引用必须先同步到已确认的安装内容，并对两个摘要分别读回。`old_sha256` 与 `new_sha256` 可以相同：例如安装文件已被提前同步到候选内容，但运行时 managed revision 尚未发布；此时 `old_sha256` 仍记录磁盘上的实际内容，不能为了制造差异而填写已过时的哈希。任一摘要过时或源文件与安装文件不一致时，发布前置校验应停止，修复清单或来源后再发布，不能覆盖用户的运行时 Skill。people-review Skill 包含招聘需求审批的前置时序与离职安全切换要求；attendance-review Skill 载明合理请假时长、on-call 与休假可并存，且“工作/会议线上进行”本身不代表正常工作或要求重分类请假的标准；新 OA 审阅轮次按当前运行时 Skill 绑定。发布中途失败也由 updater 持有该快照恢复；恢复失败时保留回执及原文件副本，记录 `needs_manual`，不回滚 Git 后启动可能与外部文件或引用不一致的旧服务。恢复成功后才启动旧版本；健康通过后仍核对 active 配置、全部启用绑定与新 worker 的加载回执/PID、十四文件 SHA 和完整任务引用，最后标记 verified。若启动时的任务版本对账基于本次发布配置创建了后代配置，只接受其仍沿父链追溯到本次发布，且启用 Skill 摘要、存活 worker 的完整加载回执、十四文件 SHA 和任务引用逐项匹配时通过核验。代码上线、资产发布和实际业务结果分别验收。

正式关闭的微信投递若在关闭原因中记录 `superseded_by_principal_reply:<source-message-id>`，表示本人后续回复已覆盖旧候选。仅在匹配当前任务、执行代和会话的投递上，详情状态保留 `skipped`，不被任务的 `done` 覆盖；页面说明已由本人回复、旧候选未发送且无需重试，不显示旧投递重试动作。手动重试的同一写事务读取该正式来源引用并拒绝重新入队，原投递、Attempt、执行代与回执不变。该判定读取分号分隔的精确关闭字段及非空来源引用，不匹配回复正文或一般文字。其他普通过期投递的现有手动重试合同保持。

微信发送前失败（`pre_action_failure=1`，已记录非空 `action_started_at`、从未真正发出）的投递，自动重试用尽后停在 `failed`，History 与重试接口都把它当作可手动重试的投递：详情显示「重试发送」，`requeue_expired_wechat_delivery_for_user` 同时接受 `skipped` 与此类 `failed`。此前只有手动 CLI `skip-stale-wechat-delivery` 能把它改成 `skipped`，而 `skipped` 是唯一出现重试入口的状态，所以最需要重发的失败投递反而没有入口（投递 #140，`wechat_ui_not_ready`，2026-10-23）。独立 Audit 拒绝（`wechat_audit_rejected`）从未领取，没有 `action_started_at`，不在此列；重新入队后发送仍需通过原有的已批准 Audit 证据才能领取。

## 用户反馈处理轮次

Consumer/Audit 的通用应用结果合同只在 Developer 注入一次，不在每轮 Task context 复制。Audit 的重复 Role Boundary 合并到“应用结果合同”段；Task 中显式自定义 Audit Rules、完整候选与 digest、当前事实、阶段和反馈继续保留。会话绑定、角色政策和 wire schema 保持不变，Settings 的当前预览沿用同一装配函数，历史视图继续读取原始已保存输入。

用户反馈处理有自己的当前投影：

```text
pending -> processing -> resolved -> pending
```

它不使用普通 Agent 任务的 `running` / `done` 状态。其中 `pending` 是待领取，
`processing` 是已被唯一批次领取，`resolved` 是当前处理轮次完整结案。
从 `resolved` 重新打开时，本地 API 必须收到不为空的原始 `reason`。该转换只把
当前投影返回 `pending`，记录转换和理由，不创建新轮次。

之后的 claim 才会创建新批次和下一个处理轮次。每个轮次的 Workbench 任务/
turn、attempt/run、commit 和测试/重启/健康证据都归属该轮次。新轮次以空关联和
空证据开始；旧批次、旧轮次和旧回执始终不可变，也不参与新轮次的结案。

只有当前 `processing` 轮次可以接收关联或证据 PATCH。resolution receipt 按已持久化的
feedback-iteration decision scope 校验，而不是对所有修复一律要求 commit：`skill_only` 要求
精确 revision/SHA、active config、匹配的启动 load receipt、场景验证、health 和零 backlog；
`runtime_config` 要求前后 config、目标 receipt、场景验证、health 和零 backlog；`code` 要求
本地 `main` 祖先 commit、测试、不同 PID 的重启、health 和零 backlog；`mixed` 同时满足相应
code 与 Skill/config 条件。`needs_human` 不接受 resolution receipt，项目保持 open。任一适用
条件不满足时，整个批次保持 `processing`，不部分结案。关联、证据和结果必须通过 Feedback API
持久化并回读一致。

Feedback API 的 Git 回归使用独立临时仓库初始化 `main`，并将测试 API 与回执指向同一仓库。
PR 的 detached checkout 或缺少本地 `main` 不应改变测试结果；测试不得修改开发/生产检出的
分支，也不得用 `HEAD` 代替 main 祖先证明。真实的缺失提交、未合入 main 的提交仍必须拒绝结案。

Feedback API 是现有本地后端边界内的操作接口，供 Workbench 和仓库 Agent 共用；它不对
公网暴露，也不增加 feedback 专用鉴权或第二套 Agent 流程。

当前未发布开发候选已启用三个 Feedback→Actions 存储类型。Store 的反馈事件、
处理转换和迭代决策子项读写使用类型化 Actions 投影/辅助函数；QualityGate 也读取同一
投影。新建 Store 在现有发布事务内短暂建立精确的空原声明并立即迁入 Actions，重开与修复
不创建原表。配对迁移在 Errors 之后执行 Feedback，逆向先从当前 Actions 事实恢复三个
原表，再逆向恢复 Errors；正逆都沿用调用方 FK-off 事务。转换保留原字段、SQLite 值类型、
事件/decision item 隐藏 ROWID、transition 独立自增水位、三条 Tasks 外键和不可变子项
约束。当前开发候选是 28 张普通表；生产发布和最终十张核心表加 Email ledger 尚未完成。

### Email task 的运行边界

> **实现与部署状态：** Email folder classifier 与 audited-v2 lifecycle 已合入 `main`；本机
> launchd 的独立 Email worker 已启用，并通过真实 IMAP 可逆验证。SMTP 与自动回复仍禁用。

Email 的分类确认不是 Agent 运行，也不会创建通用 `reply_task`。邮箱服务器中的当前文件夹是
类别的唯一事实来源：业务文件夹映射为相应类别，Inbox 表示未分类，Spam/Trash 映射为内部
`junk`，Sent/Draft 排除。`important` 是与类别正交的标记，由 provider Starred/Important/Flagged
与成熟模型信号取并集；junk 不允许 important。确定性 Email 动作清单是
`label`、`mark_read`、`archive`、`move`、`trash`；独立 Email worker 领取并执行这些动作，
随后读取 provider 状态确认结果。这些确定性动作
不创建 CEO Agent task，也不创建 Consumer/Audit run。`trash` 只能执行可恢复的 move-to-Trash；
永久删除、IMAP `EXPUNGE`
和清空 Trash 不存在可调用路径。

稳定 Message-ID 查找若在多个文件夹命中，属于确定性身份歧义：在只读状态定位阶段失败，不执行邮件写入，也不自动重试；超时、DNS 和连接错误仍可重试。身份问题解决后，才可重新处理该邮件动作。

Provider observation scan 在一个 SQLite 写事务内发布消息状态、authoritative folder 缺失项对账和
folder generation 时间。generation 按账号和文件夹单独记录，分类状态读取仍投影该文件夹最近一次
扫描时间；消息内容未变时不更新消息行及其索引。扫描成员先放入连接级临时表，再按
`(account_id, provider_folder_id)` 索引对账，避免大批动态 `NOT IN` 参数并缩短共享数据库写锁持有时间。

IMAP 移动模式是账号级显式配置：默认 `imap_move_mode=move` 并要求服务端支持 `UID MOVE`；
只有 provider 官方协议把 `UID COPY` 定义为移动时才配置 `copy_as_move`。运行时不按 hostname
自动推断，不对普通 COPY 执行 `\\Deleted`/`EXPUNGE` 补偿；动作完成后统一用稳定 Message-ID
重新定位，确认邮件只存在于目标文件夹并读取新的 UID/UIDVALIDITY。

**对账已失败的 move/trash 动作**（2026-09-25）：Gmail 被限流后会在 COPYUID 响应后追加自己的文字
（`[THROTTLED]`），旧解析器因此把 Gmail 已经完成的移动记成 `failed`（`provider_apply_failed:
ImapReadbackUnsupported`），而这类失败是终态（`attempt_count=1`、`next_attempt_at` 为空），修好解析器
也不会自动清掉，盲目重试又可能把邮件移动两次。`python -m app.cli reconcile-email-actions --account <id>
[--apply] [--limit N] [--after ACTION_ID] [--pause-seconds S]`（`app/email_action_reconcile.py`）
只**读**邮箱来对账：对当前计划里每条 `failed` 的 move/trash，先看原位置（原 UID 还在则没移动，
留 `failed`），再按稳定 Message-ID 在目标文件夹（move 的 `target_folder`、trash 的 `\Trash` 文件夹）
里找，最后才扫全部文件夹。目标里恰好一份、且满足动作时，`--apply` 才把它经由现有状态机
（`claim_failed_direct_action_for_reconciliation` → `complete_direct_action_attempt`）追加一次 `done`
尝试，`provider_operation=reconciled_readback`、`provider_result_id` 以 `reconciled:` 开头，明示这是
事后对账而不是动作当时的回读，同时把分类与邮件的定位更新到新的 UID；原来的 `failed` 尝试原样保留。
其余一律不动、不写库：原位置还在（`in_source`）、在别的文件夹（`elsewhere`）、哪都找不到
（`not_found`，包括 Message-ID 缺失）、重复 Message-ID（`ambiguous`）。命令**不会把任何行改回
`pending` 或重新排队**：`pending` 必须 `attempt_count=0` 且没有尝试记录，`failed` 行没有受支持的
保持不变式的重试入口，仍在原位置的行要重试须另行决定。默认不写库，只有显式 `--apply` 才记 `done`
（若全局 `--dry-run`/`CEO_NOT_SEND_MESSAGE` 生效则拒绝 `--apply`）。**命令永远不改邮箱**：会话被
`ReadOnlyImapSession` 包住，只放行 LIST、EXAMINE（只读 SELECT）、UID SEARCH、UID FETCH（头部与
标志）、CAPABILITY、LOGOUT，其余（STORE、MOVE、COPY、EXPUNGE、APPEND、CREATE 等）在发出前抛
`ReadOnlyViolation`。可重复运行（记成 `done` 的行不再是 `failed`）、每次最多 `--limit` 行（默认 100，
按 action id 排序，输出的 `next_after` 可接着 `--after` 续跑）、行间停 `--pause-seconds`（默认 0.5 秒）；
单封超时或被限流只记为 `error`、丢弃并重连会话、指数退避后继续，连续 5 次错误才停下（`aborted=true`）；每封（含连接）有 `--message-timeout-seconds`（默认 60 秒）的墙钟上限，超时就从外部关掉该连接并记 `error`，不依赖套接字读超时——曾有一次读取卡住 30 分钟；每 25 封输出一行计数进度，Ctrl-C 会带着已有计数正常结束（`interrupted=true`）。
空 `--after` 表示从最小 action ID 开始，适用于规范化后保存为整数的 Action ID；
分页游标与内部领取 API 在边界统一转成文本 ID，不改变记录的原始 ID。对账领取按同一
事务内读取的 Action 当前 `started_at` 精确做 CAS，兼容仍保存空字符串的失败记录。输出只有计数（按
move/trash 分：verified、in_source、elsewhere、not_found、ambiguous、error），不含邮件内容。

只有确认后不可变 `ActionPlan` 中授权的 `unsubscribe` 创建 `channel=email` 的
`reply_task`，生命周期固定为 `email_unsubscribe_audited_v2`；其他动作和零动作计划不创建
task。`auto_reply`、SMTP 和 `mailto` 发送全部禁用，不能由配置、分类结果、人工确认或 Agent
生成，也不能作为退订 fallback。

Email action task 的去重身份由以下四项确定：

```text
account_id + stable_message_identity + action_type + action_plan_version
```

账户与稳定 thread 身份确定 `conversation_id`，上述动作身份确定
`trigger_message_id`，继续依赖现有 `(channel, conversation_id, trigger_message_id)`
唯一约束。幂等重放返回已有任务，不重置其状态或 execution generation。

Adapter 只创建 `pending` task 和受限上下文，不直接发送邮件、打开网页或写入新的任务
状态。邮件正文和 thread 纯文本在运行时上下文中可用；附件是 metadata-only，没有
image/content material，只包含文件名、MIME、字节大小、数量和 inline 标记，没有读取命令
或 image path。任何组件都不得下载、打开、OCR、解析、总结或推断附件正文。持久 trigger payload 不包含凭证、附件内容、本地路径、
完整私密 URL 或 query token。

模型 ActionPlan 的退订任务修复扫描只读取候选分类身份，确认缺任务后才加载完整分类行；
每次修复后的复核按分类 ID 定点查询，避免每修复一个任务就再次扫描所有历史分类。

退订不做结构化审核。Email worker 直接执行兼容调用语义 `unsubscribe_email(task_id)`：一次调用完成整件事——
打开 ActionPlan 已授权的 entry，按页面当场呈现的控件操作，直到第一个终态页面，然后返回
outcome 和脱敏后的页面原文。它只接受 task id 这一个调用方无法伪造的参数，其余全部从 durable 状态
读出，所以没有 proposal 要抄写、没有 acceptance 要绑定、没有 Audit turn，也没有 continuation 要续。

Direct 浏览器提交退订表单前，只为唯一的空 `type=email` 字段填写当前连接邮箱账户地址。
已有字段值保持不变，不从普通文本字段名称推测身份；缺失账户身份或多个邮箱字段时停止提交。
填写后重新读取真实控件，并核对提交目标、方法、编码、提交按钮和全部成功字段（包括隐藏字段）
与填写前一致，仅允许该邮箱值变化，再使用新控件引用提交。旧结构化审核执行路径不隐式填写字段。

幂等性只靠 receipt：`email_unsubscribe_receipts` 里每个动作身份一条，已有 receipt 时再调一次
只会把它原样返回，不会重复退订。这取代了原先的 claim 租约、effect digest 链、owner fence 和
Consumer→Audit 往返——退订在真实世界本来就是幂等的。同理，有浏览器步骤但没有 receipt
不再升级为 needs_human，重跑一次即可。历史 run、session、step、receipt 和失败事实保持不可变。

已经创建的退订任务重建上下文时，必须使用任务中保存的不可变、脱敏 ActionPlan 载荷；浏览器入口
只在创建任务时从邮件正文中选择一次。Provider 后续重排正文、移除 header 或无法读取当前邮件时，
不得重新选择入口并改变已有授权。已有终态 receipt 的新 Consumer/Audit 执行只读取该 receipt，
形成新的明确成功 run，不重新打开浏览器或发送新的外部请求。

启动时的终态 receipt 投影恢复只重开“receipt 已落库、任务因旧生命周期超时而失败”的退订任务。`skipped_no_reliable_entry` 和 `skipped_login_required` 两种跳过 receipt 且任务已有执行次数的，不再重开：手动重跑因原邮件已不在邮箱而失败（`email unsubscribe source message is unavailable`）时，每次重启重开只会让同一批任务必然再失败一次。
恢复更新按不可变动作身份连接终态回执，读取任务持久化字段时限定任务表；回执投影只能收口匹配的失败任务，不发起新的退订操作。

每个邮箱账户有两个独立回溯窗口：Agent 默认 30 天，模型默认 365 天。`email-message-check-once`
对单账户的 DNS、连接、超时及 IMAP 会话中断先按共享指数退避间隔重试，最多 3 次；同轮其他账户仍独立扫描。仅当失败账户全是这类外部传输故障时，定时任务按短时外部依赖故障处理：保留该轮失败事实，依既有持续故障门槛决定是否写入 Attention，后续定时轮次继续恢复。登录凭据、TLS 配置、扫描结果错误和本地逻辑失败不按传输故障重试或降级；成功轮次仍用既有正式入口消解已恢复的故障记录。
按当前 runtime 模式只运行其中一条路径：无上线模型时运行 Agent 窗口并遵守账户的“仅未读/全部”
设置；模型上线后只运行模型窗口，优先覆盖全部尚无稳定记录的已读和未读 Inbox/未绑定来源邮件，不
按日期把近期邮件留给 Agent，也不在同一轮创建 Agent 分类任务。模型路径以每文件夹固定批量
在后续定时轮次继续向历史推进。模型 accepted 结果进入现有不可变 ActionPlan 和 provider action
队列；`model_rejected`、`model_others` 与 `model_category_not_promoted` 对 Agent 能接的邮件（未读，或账户设为
“全部”，与 Agent 消费时的资格判断一致）创建 Agent 分类任务，由 Agent 判定；Agent 结果 `certainty` 不是
`certain` 时才存为 `pending_feedback` 等待人工标注（Derek, 2026-09-25：模型不确定先回退 Agent，Agent 不确定再问人）。
Agent 会以 `provider_message_no_longer_eligible` 跳过的已读邮件不入队，直接存为 `pending_feedback`，
否则任务以 skipped 结束且没有任何分类记录，邮件同时从 Agent 和待确认里消失。Embedding、runtime 或持久化技术失败使本轮
失败并在下轮重试，既不写人工待确认，也不当作模型拒绝交给 Agent。定时历史模型 runtime 的 Embedding 请求期限为 120 秒，实时
调用仍是 2 秒，避免批处理复用实时延迟预算后在同一封历史邮件上永久超时。冻结训练 snapshot 直接采用 provider 文件夹和 important 信号，
训练与 shadow 评估均为离线、阶段性作业，不在收信路径实时训练或并行推理；晋升只看最新候选自己的证据，
按类别逐个判断，达标类别由模型决定、其余仍由 Agent 决定，不再要求连续两个候选或标签水位前进。
已上线模型只对照它自己的证据保持生效：后训练的新候选无论达标与否都不会让它下线，候选目录里有无法读取
的文件仍 fail-closed。实时新邮件主路径严格按 `model -> Agent fallback` 顺序执行；定时路径同样如此：模型拒绝
进入 Agent 分类队列，Agent 不确定才进入待确认，技术失败则重试，不调用 Agent。

Provider 训练观察按有界批次运行。观察缓存与请求队列使用各自独立的进程锁和文件锁；长时间 IMAP
扫描不得阻塞实时扫描、分类结果落库或确定性邮箱动作。Email worker 只有在扫描/动作、Agent
consumer、训练三个组件都至少成功完成一轮后才发布 `ready`。

业务类别移动完成后在变更后的 locator 上执行 flag/read 动作，以服务器应答为结果；用户在 provider 中再次移动
邮件时，下一份 snapshot 立即以该文件夹作为训练标签。旧的 staged/manual 历史评测入口仍用于候选
验证；生产历史整理由上述定时模型窗口自动、小批量、可恢复地推进，不会一次性读取整个邮箱。
`junk` 先由代码发现标准退订候选；分类 Agent 只返回本次候选序号，服务按该次调用的候选列表绑定精确 URL，并继续执行既有类别、索引和持久化校验。之后 unsubscribe 使用独立的系统直接网页流程，最终再移动到系统 Trash。连接邮箱 OTP 仅允许站点、收件人、挑战上下文和时间窗全部
匹配的临时读取；普通 CAPTCHA 在隔离 profile 中有限尝试，不能完成的密码/MFA/CAPTCHA 保存不含
秘密的有界 continuation 并交给用户，恢复时不重放已经审计的 operation prefix。

只读 Console API 提供当前 provider-derived category 或 unavailable、文件夹绑定、描述/训练 snapshot
版本、样本与组数量、逐类别历史资格、连续晋升证据、active mode/model、时延与 fallback、动作回读
和退订 continuation。分类列表不暴露完整正文；用户打开单封邮件时，详情接口返回已持久化的文本
正文及收件人信息。API 不暴露完整退订 URL、OTP、secret、raw embedding、附件字节或浏览器 session
私密数据。外部配置的 embedding model/revision 不原样投影，也不返回可被离线枚举的
摘要；API 仅返回固定受控占位符。canonical model/snapshot ID 则按生产构造格式校验。当前 category
来自扫描进程持续写入的 latest-provider-observation 投影，
不读取冻结 snapshot，API 也不会额外连接邮箱；冻结数据只以 training snapshot 字段展示。SMTP、
自动回复和所有 reply/send 路径继续不可达。

### Email folder classifier live verification

Console 的”模型训练”提供版本化门槛配置及显式主模型开关。四项默认值为 Micro F1 0.95、
逐类别 Precision 0.95、逐类别独立测试样本 20、常驻端到端 P95 500ms；缺失测量不能达标。
达标显示红点，用户操作开关才调用 runtime-mode API。运行状态和切换身份/历史在同一个
online-active.json 中原子提交，expected mode/model 冲突返回 409；关闭模型保留证据和历史。
Registry 锁串行化模式切换，提交期间配置写锁保持当前描述和门槛不变。模式改变由 worker tick
读取生效。门槛和类别描述使用 schema 34 的追加版本记录；线上数据库升级前执行并验证 SQLite
在线备份。代码回滚至 schema 33 不能直接打开升级后的库，须使用已验证的升级前备份。

训练证据区分 head latency 和端到端 latency。趋势图只连接同一评测协议、独立测试集摘要和
类别集合的版本；旧版本缺少这些字段时显示不可比较。单个损坏的 staged 文件仍显示异常条目，
其他健康版本继续展示，晋升判定保留损坏事实。

Embedding 训练观察与线上预测共用 provider 字段映射和 canonical JSON 序列化；正文（含引用）、
允许的邮件头及附件元数据必须产生相同输入和缓存键。旧 TF-IDF 的分词文本不能冒充该输入
schema。候选计时从已读取邮件的输入构建开始，覆盖预热后的缓存查询、远端 Embedding 和输出头，
不包含邮箱网络读取或分类后的外部动作；远端未命中路径和缓存命中路径分别记录。无法验证输入
一致性、缺少 Embedding 配置或测量失败时记录未测量原因，不生成可晋升的延迟数值。

Live checks are opt-in and excluded from normal test runs. The mailbox check requires a
designated reversible message plus `CEO_LIVE_EMAIL_FOLDER_CLASSIFIER_E2E=1`,
`CEO_LIVE_EMAIL_IMAP_HOST`, `CEO_LIVE_EMAIL_IMAP_PORT` (default 993),
`CEO_LIVE_EMAIL_IMAP_USERNAME`, `CEO_LIVE_EMAIL_IMAP_PASSWORD`,
`CEO_LIVE_EMAIL_IMAP_MOVE_MODE` (default `move`; use `copy_as_move` only for a verified provider),
`CEO_LIVE_EMAIL_ACCOUNT_ID`, `CEO_LIVE_EMAIL_MESSAGE_UID`,
`CEO_LIVE_EMAIL_MESSAGE_UIDVALIDITY`, `CEO_LIVE_EMAIL_MESSAGE_ID`,
`CEO_LIVE_EMAIL_LOCATOR_FOLDER`, and `CEO_LIVE_EMAIL_TEST_FOLDER`. UID/folder variables are only
locator hints: before any write, the test freezes the provider-derived folder, UIDVALIDITY, UID,
Message-ID, and the complete persistent IMAP FLAGS set, including keywords/provider labels (the
non-persistent `\\Recent` session flag is excluded). The test simulates a provider/user-side move with
a raw IMAP operation outside the service executor, then freezes an observation snapshot and proves the
new folder-derived category. In `finally` it re-locates the designated message by stable Message-ID
across allowed folders, restores the folder and exact persistent flag set, and verifies every property
again through a fresh connection. A MOVE whose returned locator/readback fails is treated as possibly
applied: compensation re-locates before any further write. If exact restoration cannot be proved, it
fails with the stable identity and current locator for manual recovery. It never configures SMTP or
calls send/reply.

The GPU4 check requires `CEO_LIVE_EMAIL_EMBEDDING_E2E=1` plus the normal
`EmailEmbeddingClient.from_environment` endpoint and authentication variables. The cached check
runs the same normalized message through the warmed production `PromotedEmailClassifierRuntime`,
including normalization, cache lookup, description-aware MLP head, and result validation, and proves
cache hits before requiring P95 below 100 ms. Distinct uncached GPU requests require P95 below 500 ms.
Without explicit opt-in, both live checks skip without external mutation.

Run both opt-in checks only with a designated reversible message and GPU4 configured:

```bash
CEO_LIVE_EMAIL_FOLDER_CLASSIFIER_E2E=1 CEO_LIVE_EMAIL_EMBEDDING_E2E=1 \
pytest --run-live -q -m live tests/test_email_folder_classifier_e2e.py tests/test_email_embedding_client.py
```

## DingTalk / WeChat 统一外发后缀
### 只能发服务准备好的正文

Derek，2026-09-18：外发消息的正文必须是服务为该动作准备好的那一份。Agent 自己编正文发出去
会绕过签名、反馈链接、幂等键和投递台账——实测它把提示词里的反馈链接手抄进正文并抄坏了编码，
同一条修复又抄坏一次。执行轮发送时若正文不是服务准备的，按结果契约违规处理，走普通纠正轮
（`app/outbound_text_authority.py`）。提案正文里也不得自带反馈链接或签名，服务会自己追加。


所有服务所有的 DingTalk、WeChat 人员可见文本，在 provider 调用前都必须由
`ServiceMessageSender` 生成并持久化 `PreparedOutboundMessage`。持久化键是
`(channel, delivery_key)`；首次准备确定最终正文、feedback token 和后缀版本，之后的重试、恢复、
发送回读与 WeChat 撤回只能复用该记录，不得根据当前配置重新生成。provider adapter 只接收已持久化
的最终正文，业务模块直接调用原始发送方法会被架构测试拒绝。

DingTalk provider 将该最终正文通过 `dws chat +messages-send --as user --markdown` 发送，使 Markdown
标题、列表和反馈链接由钉钉按 Markdown 渲染。回复触发消息用钉钉原生引用回复（`+messages-reply --content`），正文同样按 Markdown 渲染：2026-09-25 在Derek 自己的会话实测，标题、加粗、行内代码、无序与有序列表都正常显示，相邻行的列表项也分行——DWS 帮助里「普通引用按纯文本解释」与实际不符，读回接口把列表拼成一行只是读回的呈现。推送横幅标题去掉 Markdown 标记。Consumer 起草的钉钉消息正文一律写成结构化 Markdown（加粗结论行、`- ` 列表、关键事实加粗，分段时用短`###` 小标题）；微信、OA 审批评论和邮件正文保持纯文本。`needs_human_reason` 用中文大白话一句话写清 Derek 要决定的那一件事和原因，最多100字；详细判断依据留在 `decision_basis`，不出现 rule_coverage、规则卡、partial、动作映射等内部词（Derek 2026-09-24/25/2026-10-19）。群聊、单聊仍使用稳定的 conversation/user/openDingTalk ID；
delivery UUID、结构化群 @ 及 `ServiceMessageSender` 准备的签名与反馈链接保持原样随正文发送。

Consumer 修订版可以原样复用上一 revision 中已持久化的服务反馈链接。敏感值校验只会对配置域名、
固定回调路径、有效签名和成对反馈 token 完全匹配的服务链接做占位化处理；真实凭证、陌生域名、
畸形回调或其他敏感值仍然必须使该 run 失败。
新生成的反馈回调 URL 只携带不透明 feedback token、评分和 attempt ID，不得把触发消息或回复正文写入
URL 查询参数。外部反馈页可以收集评分，但不能通过链接、浏览器历史或访问日志获得内部消息文本。
未持久化的旧格式正文若已包含有效的服务反馈链接，外发准备阶段保留原 token 和消息正文，重新生成
不含正文参数的链接；已持久化的历史投递回执不改写。

这是一条机械传输边界，不是新的业务审核或授权规则。Email 仍是独立的非发送通道：SMTP、自动回复
和 `mailto` 均保持禁用，不受此 DingTalk / WeChat 后缀机制影响。

## 统一禁止事项

- 所有任务都不得使用 `discard` 动作。
- 所有任务都不得写入 `discarded` 状态。
- 不得用“丢弃”代替审核反馈、修正原 run、重新排队、人工升级或失败记录。
- 业务 `reply_attempt` 的 current projection 可以由重跑更新；同一 business object 复用原 attempt ID，
  不创建新的业务 attempt。原始失败作为 append-only state event 保留。其下的 `agent_runs`、proposal
  版本、revision lineage、session、runtime attempt 和 tool event 不得覆盖；attempt 页面可切换查看
  这些底层 run。`business_object_key`、`action_identity`、`operation`、`target` 和 provider
  成功结果是避免重放所需的最小持久化事实。
- 已成功的消息发送或 provider 回执不会因为 attempt 后来失败而失效。下一轮上下文直接读取这些
  append-only 执行事实，不能把解析失败、服务重启或 current projection 失败理解成“尚未发送”。
- Audit approve 后只允许系统执行；全部动作有验证回执后任务才进入 done。

如果任务确定无需执行，应进入 `done`，并在 trace 写入 `agent_output/no_action`；如果结果需要修改，写入 `audit_feedback` 并保持 `running`；如果处理失败，应进入 `failed`。

任务 Agent 的 `memory_recall_used` 是 Agent 提供的上下文记录，不是服务工具调用验收条件。可用时应以聚焦
查询读取稳定背景；当前身份和业务事实仍需依赖相应来源/实时读取，memory 不能作为任务存在、指派授权、
负责人 ID 或接受承诺的证明。

邮件退订由独立系统服务执行，不进入 Consumer/Audit。其原有 ActionPlan、浏览器步骤、回执和退避保留，历史 Audit 退订 run 只作历史事实，不是新的执行路径。
退订入口导航遇到超时时，若同一次导航已离开空白页，执行器直接读取当前页面并检查真实页面回执，不再请求退订 URL；若仍停留在空白页，则保留外部超时失败并沿用有界退避。该读回只恢复已经到达的页面证据，不清除未知执行围栏，也不把页面未读成成功。
退订浏览器仅把固定的内部失败类别投影到错误码；已识别的导航超时和页面状态缺失必须与兜底 `email_unsubscribe_browser_failed` 区分，错误码、步骤日志和页面原文里不得写入 URL 或凭证。结果的 `error_detail` 记下失败是什么：已识别的 `UnsubscribeBrowserError` 写固定枚举 `category=<名称>`（没有专属错误码的类别共用兜底码 `email_unsubscribe_browser_failed`，这里是唯一说明具体类别的地方，2026-09-25 有 6 次失败只剩这个兜底码）；未预期异常（兜底）会留下异常类名和截断到 240 字符的消息：URL、cookie/token/session 一类字段和 32 位以上的长串都先替换掉，再写入回执 evidence、这次尝试的 `audit_summary` 和任务的错误文本（`<码>: <类名: 消息>`，重试判断只看第一个冒号前的码）。此前该兜底只留下码，任务 384835（2026-09-25）两次失败因此查不出原因。所有结构化 `action_type=unsubscribe` 的失败都保持真实 `failed` 状态并留在 History，不进入管理者 Attention；外部原因的失败（第三方页面拒绝表单或一键请求、页面操作失败或超时、导航目标无效、控件不可用，清单在 `app/external_failures.py`）继续按既有有界指数退避重试。Attention 分类通过任务错误中的固定 `category=` 校验分类，允许其后出现已脱敏 `detail` 和 `operation` 阶段后缀；浏览器/会话自身类别仍作为内部错误显示。兼容旧 Attempt 的 `failed_browser: category=<名称>` 格式。若已有执行标记不满足明确 retryable 状态及身份校验，或 retryable 次数已耗尽，服务以 `email_unsubscribe_outcome_unresolved` 记录未决结果；任务仍失败且不进入管理者 Attention，不把围栏误报为内部 `ValueError`，不自动重放，也不清除标记。只有终态 receipt 可收口该动作。
旧失败任务的 effect-free 自动重入队还要求没有 `email_unsubscribe_execution_pending` 标记；即使没有 claim、receipt、effect、浏览器 step 或 Agent run，这个标记仍表示执行需要对账，恢复筛选必须保留失败状态并禁止自动重放。
执行标记在退订请求前落库。浏览器返回属于瞬时外部失败清单的明确错误时，服务把当前标记原子更新为 `retryable`，只保留错误码、次数和原任务身份；下一次领取仍按本段的固定次数与指数退避执行同一授权动作。`retry_count` 记录已授予的重试次数；共享上限为首次操作之后最多 3 次重试，所以 count 为 3 时仍可执行第 4 次浏览器操作；之后的下一次本地领取在浏览器运行前以 `email_unsubscribe_outcome_unresolved` 拒绝。没有明确失败结果（例如进程中断）、非瞬时错误或无回执异常仍保留原 pending 标记，阻止自动重放；这与已归类的外部浏览器错误分开处理，不能把未知结果当成可重试失败。

`email_unsubscribe_browser_timeout`、`email_unsubscribe_browser_session_unavailable`、兜底码 `email_unsubscribe_browser_failed` 这三个瞬时失败会先退避重试；用完固定次数后写终态 `failed` 错误文本时，只有兜底码把 `task_error` 里已经带的 `: category=<名称>`（或未预期异常的类名与消息）一并写入，其余两个专属码保持裸码——`app/external_failures.py` 只登记了这个精确格式。此前用完次数一律只写裸码，2026-09-25 那 6 次失败因此从未真正匹配过外部原因清单：它们的错误文本是裸的 `email_unsubscribe_browser_failed`，本该在 Attention 之外却一直在里面（Derek 2026-09-28 指出后修复）。控制台看到的失败原因同样有这道口子：一个还没读到任何页面、因而没有回执的退订任务（浏览器在读页面前就报错），观测时间线过去只给状态不给原因，2026-09-28 起把任务自己的错误文本一并投影进 `kind=unsubscribe` 的记录（`list_email_classification_observability` 和 `list_unsubscribe_states` 的 `error` 字段），控制台把已知的码译成人话，未知的码原样显示而不是不显示。钉钉 OKR 登录失效（`okr_headless_session_expired…`、`okr_authorization_required…`）同样是外部原因：只有人能重新登录，也保持 `failed`、不进 Attention；只有标准错误码 `authorization_required` 才能承载「需要人工」的授权决定，OKR 自己的错误码不行，所以它记为失败而不是人工决定。唯一例外是 `email_unsubscribe_receipts.entry_url`：该列按 Derek 的明确要求保存这次实际打开的完整私密 URL（含 query 与 token），用于人工复现同一个退订入口。写入前校验它的 sha256 等于 `entry_reference` 的摘要，因此不能与生命周期认定的身份漂移；它不经过 `assert_no_credentials`，因为被保存的正是那类 token。打开该 URL 会真实执行退订，任何能读这张表或这个页面的人都能替当事人退订。该列只在本次变更之后产生的 receipt 上有值，历史行为空且无法补全。退订浏览器不再对页面发出的网络请求做 origin 白名单、跳转或资源家族限制。
同一 generation、同一 proposal revision 内，Consumer 或 Audit 每个 worker pass 最多跑 2 次 turn（`MAX_ROLE_ATTEMPTS_PER_PROCESS`）；仍是可重试的通用失败时该 pass 以 `failed_retryable` 结束，并给出 `retry_after_seconds`（共享指数退避，封顶 15 分钟，`external_retry.retry_delay_seconds`）。Consumer 与 Audit 的失败次数分别按持久化同角色 run 计算，首次失败后最多重试 3 次；同一角色 4 次连续失败后，在下一次 provider 调用之前结果为 `failed_terminal`。容量、连接、来源刷新和其他可重试技术失败都计数，另一角色的失败不占用本角色预算，同角色成功 turn 会重置连续失败计数。错误码与 Agent 原文 `source_code` 保留在诊断和结果摘要里，任务结束为 `failed`，不再重新入队。尚未实际执行的活动租约等待不产生失败 turn；授权缺失沿用不可重试终态。该角色预算独立于 worker/task attempt；各自最多 3 次 retry（初始调用之外），不会退款或跨角色共用。定时执行与 Email 延期不再把已失败的 Consumer/Audit turn 退款后无限重跑。内容反馈预算是业务修订循环，不属于技术 retry 预算。

`dependency_read_unavailable` 和 `agent_context_refresh_failed` 都计入该角色最多 4 次失败 turn 的预算，不形成无限外部等待例外。`agent_context_refresh_failed` 仍保留服务侧外部刷新等待；同码持久化 run 按共享退避逐步延长，最长 15 分钟。成功读取但无匹配数据、证据缺失/歧义或规则未定义走正常证据/业务判断，不能伪报依赖故障。权限申请后等待使用经审核的 Consumer purpose `retry`，规则如下。

当路由执行失败时，已分类的 `RoutedCodexExecutionError.retryable_external_dependency` 同时决定 AgentRun 与 Runtime Attempt 的 `retryable` 记录。能力失败保持终止，容量和传输失败继续可重试；没有失败类别的旧式路由错误沿用既有默认值。

## 周期性工作的归属

Derek，2026-09-18：**后台周期性工作必须是定时任务**，在控制台里可见、可开关。不允许把
干活的循环藏在服务进程里，也不允许靠手工 CLI 命令批量灌入。

- 被删除：工作区文件全量扫描。它把工作区里每个 `.md`/`.txt` 当作工作材料、每个文件跑一次
  Agent；一个 1475 文件的头脑风暴目录排了 564 条，一小时里 109 次运行调用有 105 次在读这些
  过程稿。`scan-task-sources` 现在只读 AI 听记。
- 取消隐藏的 `task-maintenance` 常驻循环：它不再扫描来源、驱动业务任务，也不再以
  `task_maintenance.*` 健康组件伪装成业务服务。会议 Memory 写入由自身的 Dispatcher 队列完成；
  错误收口和孤儿任务恢复由服务启动恢复、Consumer/Dispatcher 的租约机制负责。
- 并入定时任务「补查遗漏的钉钉消息和日历更新」（`recover-recent-messages`）：钉钉待办完成扫描
  （`scan_completed_dingtalk_todos`，Derek 2026-09-25：完成由新证据驱动，不单独检查）。它按页列出
  Derek 创建的已完成钉钉待办（`dws todo task list --status true --role-types creator`，每页 20 条、
  最多 30 页，找齐所有活动链接即停），列表里出现的已链接待办直接关闭本地 TODO 或业务 Task，
  完成证据记为 `dingtalk_todo:<taskId>`，不经 Agent。此前的做法是逐条读取每个活动链接（660 条），
  且只在手动运行的每日维护里执行，最近完成的待办在服务里全部仍是开放状态。同一步先按已保存回执
  收口结果未知的待办创建（`reconcile_unknown_business_task_todo_creates`），这是投递记账，不是完成检查。
- 已退役：定时任务「投递到期的跟进事项」（`process-follow-ups`，原先是每 60 秒的隐藏循环，
  2026-09-18 改为每 5 分钟的定时任务）。Derek 2026-09-25 决定催办只由他在 Task 详情页点按钮发送，
  该定时任务当天先停用，随后删除：服务启动时 seeding 用控制台同一个软删除
  （`delete_scheduled_task`）删掉 migration key 为 `follow-up-delivery-v1` 的任务，运行记录保留为历史；
  `process-follow-ups` 命令、每日维护里的催办步骤一并删除。
- 任务长期记忆写入**不是**定时任务（Derek 2026-09-24：「写入记忆不应该是个定时任务，而是系统层
  自动的」）：任务结束时在 `finalize_orchestrated_reply_task` 同一事务里入队，由统一 Dispatcher 的
  `task_memory_write` adapter 立即领取写入；退避重试的行到点再被领取，重试上限后进 Attention。
  见 `docs/architecture.md`「任务长期记忆」。
- 仍为常驻循环的只有 `meeting-delivery`：它只投递已审核通过的会议结论，10 秒一轮就是它的意义；
  以及备份、cron 调度/派发、探针等不产生业务判断的基础设施。

Agent Cron Scheduler 在保存触发记录时遇到 SQLite `BUSY`/`LOCKED`，且存储层有限重试仍未取得写锁，
会保留尚未保存的触发点，等待 1 秒后重试。该冲突不会生成失败运行记录或使 Service worker 退出；
触发记录仍由原有原子校验与去重事务创建。

任务卡死的回收由服务启动恢复、Consumer 的 stale-age 检查和 Dispatcher 的过期租约处理共同负责；
不再依赖一个隐藏的业务级维护循环。`recover_stale_runtime_attempts`/`recover_expired_terminal_task_runtime_attempts`
此前只在服务启动时跑一次：一个在进程运行期间才过期的租约（owner 进程已死、route 卡住）会
永远停在 `starting`/`running`，`_claim_runtime_attempt` 复用同一 workload key 的现有行时不
检查过期，新的领取请求只会拿回同一条死记录；这条非终态记录还会一直卡住部署前的
`in_flight_work()` 静默检查（Derek 2026-09-28：查了两条孤儿 `weekly_okr` attempt，租约在
约 1 小时 40 分钟前就过期，背后没有存活的 Codex session，`python -m app.deploy` 因此连续
几个小时都在「service did not become idle」上空跑）。修复是把这两个恢复函数也挂到一个新的
常驻组件 `runtime-attempt-reclaim` 上，每 5 分钟跑一次，和 `database-backup`、探针一样是
命名、受心跳监控的基础设施循环，不是隐藏循环。

部署静默检查对 `meeting_alignment_jobs.status='processing'` 和 `reply_tasks.status='processing'`
核验 owner：会议任务须有有效的 meeting dispatcher 租约或共同 `agent_runs` 中来源为 `meeting_alignment_runs` 的 `running`
记录；回复任务须有有效的 reply dispatcher 租约或 `agent_runs` 的 `starting`/`running` 记录。
失去 owner 证据的 processing 行由服务启动时既有恢复逻辑重新排队，不会无限卡住部署；
`ready_to_send` 且仍锁定的会议投递仍计为在途工作。每次静默计数结束都会关闭只读 SQLite
连接，避免长时间轮询累积文件描述符。

启动恢复 `reconcile_done_reply_tasks_with_failed_current_run` 会校正当前执行代最新 run 为
`failed`、但 task 投影误为 `done` 的回复任务，并保留成功外部动作和已收口错误的排除条件。
候选扫描在只读连接完成；写入按固定大小分批，每批在短写事务内重新核对 task ID、执行代、
最新失败 run、成功外部动作、已收口错误及适用的邮件终态回执。外部读取或扫描不会持有全局写锁，
并发改变或已收口的候选在写入前被排除；候选状态条件和历史保留规则不变。成功外部动作的
只读核验使用 typed `actions` 表，并显式选择既有 `idx_actions_global_business` 与
`idx_actions_sent_external_key` 索引，避免 SQLite 对嵌套投影连接退化为按类型反复扫描。

## 进程、租约和恢复

- 生产入口是 launchd 管理的 `com.ceo-agent-service.main`，由 supervisor 管理 worker 和 audit-web。
- 同一 `conversation_id` 同时只能有一个执行 Agent 持有 Codex session lock。
- 每个执行/审核 run 都有独立 lease、revision 和 transcript 范围。
- Dispatcher 对“租约已过期但 owner 进程仍存活”的来源不重新领取，以免重复执行。处理函数已经返回、只是以错误结束（记下 lease error）时，dispatcher 会把该租约标成“owner 已不在”（`owner_pid=0`、清空到期时间，按 owner 与 generation 围栏），来源随即按常规规则可再领取。此前这种租约要等服务重启才释放：任务 384735 重排后空等了三十分钟。
- History 解析本地 Codex session 路径时，按 `session_path_index.jsonl` 的文件签名缓存最新索引
  记录；多个 retry/run 复用同一 session 不会重复解析完整索引。索引被 Codex 或维护程序更新后，
  文件签名变化会使缓存自动失效，因此页面不会因缓存遗漏新 session。
- History 从既有工具事件归纳已完成的 provider 写入时禁用原生 CLI schema 发现；详情读取只使用
  已记录的回执和注册写入定义，不启动 `dws schema` 或其他外部子进程。
- 已被恢复器终态化或被新 generation 替代的 run 视为 lease 丢失；旧执行线程不会把该状态记录成新的任务失败。
- 回复队列的 `processing` 有双重恢复边界：十分钟没有当前 generation 的运行心跳会被恢复；即使运行持续续租，单次队列处理超过一小时也会被释放，进入既有重试或终态路径，避免反馈循环无限占用队列。
- Work summary 输入按 `source_type + source_ref` 幂等入队；重复扫描只能更新同一输入的载荷，不能把已经 `failed` 或 `skipped` 的输入重新打开。需要恢复失败输入时必须调用显式重试入口，避免周期扫描把终态输入反复改回 `pending`。
- 重启时，未完成的 Agent turn 统一按 `failed` 重试；服务不创建 unknown 或独立状态核对队列，也不依据工具事件决定是否重放。下一次 Agent turn 按业务 Skill 读取当前外部状态，再自行判断后续动作。
- 会议发现只相信自己能用的东西。结束时间取详情接口的值：列表与详情描述的是同一段录制，两者从不是一致性信号，钉钉 2026-09-18 移除列表的 `endTime` 后更会相差一秒到四十一分钟，而下游只用它做五分钟时长门槛、投递沉淀延迟、四小时日历匹配窗口和标题里的时间段。录制状态同样不做判断：钉钉改报数字码（已结束的录制上 2 和 4 都出现过），而仍在录制的听记根本不进列表接口。发现窗口为一周——迟到一周的会议跟进对参会者已是噪音，窗口越宽，provider 返回内容的任何变化就越容易变成一次批量重发。

- 一场会议可能被分成几段录制，每段各有自己的 taskUuid。同名且相邻两段间隔不超过两小时算同一场会议（由间隔决定，不按自然日切分，跨零点的会议因此保持完整）。后到的录制并入已有任务：转写与摘要接到第一段之后，会议结束时间顺延到最后一段，任务重开并**对整场会议重新生成总结**，而不是补一段增量。此时标题标注「第二次总结」。只有发送前已保存了另一条旧消息的凭证，才会在新总结发送成功后撤回旧消息；首次发送以及同一条消息的日历备注重试都不撤回自身。provider 拒绝撤回不算会议失败。

- 会议总结在 Agent 决策前，以日历组织者的完整名称通过当前 DingTalk 组织目录解析稳定身份；只有唯一精确姓名或昵称命中才回填组织者及同名唯一参会人，供业务 fallback 与敏感私信共同使用。模糊、多候选或冲突身份不回填。日历中缺少稳定 ID 的其他参会人也按同一规则做唯一姓名/昵称补全，避免 HR 只有显示名而无法被识别。服务用当前 DWS 群搜索格式检索会议标题和摘要正文里的业务词，再读取候选群近期讨论，把去重后的实时候选及讨论片段交给 Agent；候选仍需核对受众与业务承接，不能仅凭群名、词项重合或参会人覆盖发送。群搜索失败或结果不完整时任务重试，不将依赖故障说成“找不到群”并私聊组织者。敏感内容按实际披露权限投递：裁员、减员及人员优化方案本身属于人员敏感信息，不因没有具体姓名而成为业务内容。必须对完整当前群成员逐一核验实际内容的授权；HR群名、职务、业务相关及参会人重合都不能代替授权。权限未知或缺失时从业务群消息中排除相应敏感内容，只能向独立核验的授权受众拆分；无法确认时保留人员跟进未解决，不私聊本人兜底。进入投递后复用同一个持久化投递键：钉钉发送使用由该键确定的 UUID，provider 成功返回会立刻写入同一键的回执。服务重启后，恢复的 worker 先复用回执；若进程恰在 provider 接收后中断，使用相同 UUID 继续投递，provider 的重复 UUID 回应视为原投递已送达，不能产生第二条群消息。
Meeting 群发现提交给 Agent 的候选列表保留每个群的 ID、完整成员、讨论证据与读取状态；
原来每个候选都重复的同一段受众核验要求，现于非空列表前逐字提交一次。
无候选时不增加这段文字，受众授权与发送规则本身不变。

- 会议跟进的钉钉消息标题取 Agent 按实际受众核验的 `message_title`，原会议标题只有在该受众有权获知时才能沿用，不取群名或私聊收件人名；该标题只到达推送横幅和会话列表，聊天窗口里看不到，所以正文首行以 Markdown 一级标题（`# message_title`）写已核验标题，第二行以斜体呈现时间副标题（`*时间：…*`），二次总结另起斜体说明行。时间按 Asia/Shanghai 呈现：Minutes 交过来的时间戳是 UTC，直接格式化会把上午八点的会写成零点。Agent 的 `final_message` 只承载结论、行动和待确认事项，不重复会议标题和时间。正文用 Markdown 结构化：小节用加粗标签（如 **结论**、**后续行动**、**待确认**），多条并列事项用 `- ` 列表并在列表前空一行；钉钉这些消息以 `--markdown` 发送，加粗、标题和列表都按 Markdown 渲染（Derek 2026-09-24 确认渲染）。发送目标仍由已核实的受众决定，标题不参与路由或投递幂等键。
- 这一恢复规则适用于所有任务：普通服务重启只释放已经停止的 worker 租约，保留同一任务的执行代次、已准备消息和外部回执；恢复 worker 从这些事实继续。若运维明确完成了运行时/路由修复，则服务修复重试创建新的 execution generation 和新的 session 绑定，但仍复用同一业务对象及外部动作幂等事实；用户业务反馈重跑则按反馈闭环复用兼容 session。
- 外部动作的 operation、target 和 provider result identifier（若 provider 返回）会保留用于去重；缺少标识属于 provider/Agent 失败，不转换为额外状态。
- WeChat reader 由独立 launchd job 自动保持运行；worker 连续三次 IPC 超时后主动 kickstart 该 job，处理“进程仍在但 IPC 已卡住”的情况。worker 只恢复 reader 进程，不启动 WeChat 主应用，也不重放消息。
- WeChat sender loop 每轮成功完成恢复检查与待发送队列检查后，才将此前同组件错误标记为已恢复；抛出异常的轮次不会清除错误。该收口仅更新错误的恢复状态，不代表任何消息已发送；发送事实仍以 delivery 和 provider 回执为准。
- OKR 无头来源启动使用进程锁；并发调用者取得锁后必须再次读取共享缓存，复用前一个调用刚刷新的认证信息，不能重复启动浏览器或把正常刷新误报为锁超时。锁等待上限覆盖一次完整刷新周期；认证刷新通过 `app.service_browser.launch_service_chrome` 使用每日 Chrome cookie 副本启动真 Chrome，来源命令只负责在上层超时时终止自己的 worker 进程，不再启动或清理独立的 bundled Chromium 子进程。
- OKR 无头来源在请求业务 API 前校验新捕获认证的有效期。专用浏览器会话过期时返回明确的 `okr_headless_session_expired` 服务错误，不得继续请求并把认证失败误报为周期不存在。
- 个人季度不存在由共享 source 的 `MissingOkrPeriod` 类型表示：只有 provider 整数 code 0、完整且结构有效的个人季度列表及有效季度身份才能产生该结果。服务命令须指向 `scripts/dingteam_okr_headless_source.py`；旧 Skill 浏览器读取入口虽能读取已有目标，却未输出个人季度缺席收据。Codex 子进程保留服务进程的 `CEO_OKR_LIVE_SOURCE_COMMAND`，使 `agent_cli` 不会回读旧 `.env` 命令。headless wrapper 输出绑定请求 userId/periodLabel 的 `availability.status=goals_not_established`、UTC 采集时间、providerCode 0、periodsComplete true 和真实季度列表，不伪造 `processed` 空数组。认证准备、HTTP 401 和未分类共同来源失败保持 shared 技术失败；可明确归属单次成员读取的 HTTP 或格式失败使用 member 范围。错误按结构化 scope/code/detail 回到 source adapter 并抛出 `OkrLiveSourceError`，不靠错误文字猜缺目标或只用失败人数判断共享认证。依赖 wrapper 部署前必须核验本机共享 SDK 类型与打包 source 合同一致；保留共享目录无关改动。
- 完整的周 OKR 流程（实时读取、全部管理者分析、文档发布、群消息发送）使用独立的全局 run lease。领取在 SQLite `BEGIN IMMEDIATE` 事务内完成，运行期间周期续租，结束后按 owner 释放；并发调度或人工恢复只能有一个进入流程，其他调用返回 `analysis_in_progress`。`last_attempt_at` 仅用于失败重试退避，不能作为长任务仍在运行的判断。进程异常退出后不再续租，租约到期即可由下一次调度恢复。
- 过期的单人周 OKR 分析如果已经被同一管理者更晚周期的成功分析覆盖，启动恢复将旧作业置为 `completed`，记录 `superseded_by_later_completed_week` 并清除租约。它不再显示为当前 `running`；若相同自然键缺少缓存，正式分析流程仍会重新领取。
- 周 OKR 分析任务每次获得新的租约时使用新的 runtime 执行代次。单次执行中的结果格式修正保持有界；已终态的旧代次不得阻断同一分析任务在后续租约中的重新执行。
- 周 OKR runtime 租约过期并已记录为技术失败时，启动恢复必须同步关闭仍为 `running` 的父分析任务；父任务不得在没有有效 runtime 所有者时继续显示执行中。
- 所有需要 `BEGIN IMMEDIATE` 的 Store 写路径统一经过同一个有界重试事务。短暂的 SQLite 写锁在 Store 内等待并重试；只有超过上限的持续锁才上升为服务错误。队列 claim、反馈批处理和恢复路径不得绕过这一规则。

### Cron trigger 与 Consumer Dispatcher

Agent Cron 保存任务定义及其结构化 Skill refs、首选 Runtime route/model/options、工作目录、Cron
和时区。启动或主机恢复后，Scheduler 为每个任务最多补一个最近的已错过触发；更早的错过点合并，
任务创建时间之前的触发不补造，已有触发仍由唯一约束去重。之后恢复到下一个未来触发点。
手动运行只追加一次 manual trigger，不改变 `next_run_at`。正常到期时若上一轮关联 execution 尚未
终态，本轮以 `skipped` 和稳定原因结束；补跑则保留最近错过时间点，等上一轮终态后再提交。
若 Scheduler 派发时首选 Runtime 因 provider 暂时不可达而暂停，它不创建 `skipped` 运行，也不推进
该计划；后续 tick 在路由恢复后按原计划时间派发一次。多个错过点仍合并为最新一点。静态路由配置
错误继续进入 `skipped` 和 Attention。
CEO 日报默认使用 `America/Los_Angeles` 每天 06:00，OKR 管理周报默认使用该时区每周日 03:00，
使夏令时切换前后保持相同的洛杉矶本地时间。seed 只迁移精确匹配旧仓库默认周期和时区的现有任务；
用户改过周期或时区时保留原值。配置变更生成新版本，不改写历史运行快照。

`scheduled_task_runs.snapshot_id` 必须引用 `scheduled_task_config_versions` 中的非空 `snapshot_json`。
完全相同的冻结配置只存一份；修改任务会产生新的配置版本，已排队的运行仍引用原版本。测试夹具使用
`ScheduledTaskSnapshot.from_task(task).to_json()` 生成冻结定义，不省略快照。
同一任务的不同运行使用不同 `scheduled_for`，遵守任务 ID 与调度时间的唯一约束；
验证 execution 映射时也不得通过放宽这些生产约束来构造夹具。

一次正常到期分为两个可恢复阶段：`scheduled` adapter 领取 `scheduled_task_runs.pending`，在一个
事务中创建或复用唯一 `reply_tasks.channel=scheduled` 输入、保存 execution link，并把 trigger
标记 `dispatched`；`scheduled_execution` adapter 再领取该未结算 execution source，按派发时冻结的
prompt、Skill protocol、首选 route、model、thinking 和 workdir 启动 Agent；首选线路失败时按统一
fallback 换到其余配置线路（Derek 2026-09-24，此前定时任务固定单线路、从不 fallback）。managed Skill
使用精确 revision；执行前若首选及其余线路都不可用、该 revision 或工作目录已经不可用，execution 以
`skipped` 收口并产生 Attention，不把业务结果写回 trigger。Scheduler 在派发前发现全部线路不可用时
同样记 `skipped`：配置性原因（未配置、缺能力、认证暂停）每次写 Attention，
provider 暂时不可用造成的路由暂停只留下 run 记录和路由暂停状态，不逐次写 Attention。

创建 Audit 输入时，编排器从当前代次、当前 reply task 且已完成的 Consumer 运行中（包括此前反馈修订的运行），读取
`agent_cli.consumer_document_write` 已完成调用的结构化返回，或 Claude 轨迹中单个文本块／字符串承载的同一 JSON 对象，并投影文档身份、读回对象摘要和
字节数；不把报告正文重复注入。无匹配返回时 Audit 明确看到空列表。该证据只证明服务工具
返回了什么，不证明文档已被独立核实或报告事实正确。该交接适用于 `channel=scheduled` 的
Agent 任务，不增加日报或周报专属规则，也不改变 Audit 的独立审查标准。
本轮交接与真实写入回执形状的验证见 `docs/scheduled-audit-receipt-validation.md`。

服务命令任务（`scheduled_tasks.command` 非空）只有第一阶段：`scheduled` adapter 领取 trigger
后，在同一个 claim 内直接运行服务命令目录中的实现（例如 `produce-once` 是 DingTalk 消息
producer 的一次增量读取，与 `app.cli produce-once` 相同），成功后把
`execution_kind=service_command`、`execution_id=<命令名>` 链接到 trigger 并标记 `dispatched`。
服务命令本身不经过 Runtime、Skill 或 synthetic scheduled Agent；只有命令发现真实对象后，
对应的 reply、meeting 或 work-summary Consumer 才会处理业务队列。正常触发遇到上一轮未终态时，
记为 `skipped`（`scheduled_task_previous_execution_active`），不并行执行；补跑时间点则等待上一轮终态。
命令抛错时 trigger 以 `failed` 和
`scheduled_task_service_command_failed: <原因>` 收口；除依赖短暂不可达（DNS、网关繁忙、超时）
以外的原因每次写入 Attention。依赖不可达按持续时间判断：同一任务连续失败不足 15 分钟时只留在
trigger 记录里，一次外部抖动不会在每分钟的命令上刷出成串同样的条目；连续失败超过 15 分钟则写
一条 Attention（该条未解决期间不再重复写），下一次成功自动标记为已恢复。因此一次两小时的 DNS
中断是一条记录，而不是零条或一百二十条。命令幂等，claim 丢失后的
重领会直接重跑。命令名不在目录中时 Scheduler 在派发前以
`scheduled_task_service_command_unavailable` 跳过。服务命令任务不经过 Runtime、Skill、
Consumer 或 Audit，也不产生 reply task、agent run 或 reply_attempt。

DingTalk 的分钟级 `produce-once` / `calendar-invites-once` 与 `recover-recent-messages`
共用锁，避免同一 producer 并发读取。若恢复扫描正在进行，分钟级命令不等待锁，而是返回
`deferred=producer_busy` 并结束本次 trigger；下一个分钟级定时触发会补做增量扫描。恢复扫描仍会
等待已经开始的短增量扫描结束。任务入队继续按原会话和触发消息身份去重，不创建重复任务。

`wechat-produce-once` 默认每五分钟对唯一就绪的微信账号跑一次 producer。Reader 的可用性是
`wechat.reader` 健康事实而不是 trigger 失败：没有就绪账号时命令返回跳过摘要；Reader IPC 连续
失败 3 次时请求一次 Reader 重启、把健康标为 degraded 并只记录一条
`wechat_reader_unavailable`；App Data 权限缺失只记录一条 `wechat_data_permission_required`；
下一次成功读取恢复 healthy 并清除上述报告。其他异常才是命令失败，trigger 记 `failed`。
Reader 会把 WeChat 共享文章的标题、摘要和 URL 解码为文本，因此链接消息进入与普通文本相同的回复队列；图片目前只保留消息类型元数据，未提供媒体文件或视觉输入时不会被当作可读文本触发。
单聊只有非空的可读入站文本可触发回复。producer 在 Reader 扫描时将相邻消息间隔不超过五分钟的候选合成一段，等该段最后一条消息已安静满五分钟后，才以最后一条创建回复任务；未结束的段保持在读取水位之后，下次扫描继续合并。已完成的较早一段可先入队，群聊 @ 规则不变。这样处理时间按实际看到完整安静窗口计算，不依赖任务在下一轮扫描前仍处于 pending。
通过 History 发起的 WeChat 手动 rerun 会在决策提示中明确标记为 Derek 的主动重跑；消息时效和后续上下文仍会提供给 Agent，但不能单独把该 rerun 强制收口为 `no_reply`。

独立微信 Consumer 的 `CodexDecision` 不是统一 Audit 候选。完成时仅对持久任务
通道为 `wechat`、包含有效 `action` 且不含 `outcome` 的旧契约免于候选采纳；
代次、角色和运行终态仍需匹配。原决策结果保留在原生轨迹。新版 proposal 及其他通道
继续执行完整候选校验，独立微信既有交付检查和外部动作拒绝不变。
微信 Consumer 和新增的独立 Audit 均在 model-only 环境运行：Codex 没有原生命令和 MCP 工具，Claude 没有工具，Friday 不可选。Consumer 写出 delivery 后，服务先持久化带尾缀的最终正文；Sender 循环读取最新同会话消息，Audit 以原始触发消息和精确 delivery 目标、正文为候选进行审核。Audit 得知最终正文含服务按既有策略追加的签名，仍审查实际完整正文，但不把这段签名误认作 Consumer 文本缺陷。Audit 运行与 Consumer 父运行同属当前代次，模型输出为批准或拒绝、候选 SHA-256 及摘要。数据库把已完成 Audit 的原生结果核对后，将运行 ID、摘要和决定记入 delivery；自动和人工发送入口及数据库 `claim_wechat_delivery` 均只领取批准且正文、目标、任务输入未变的记录。拒绝关闭记录，技术失败不授权发送。可重试的提供商容量失败保留同一 Audit run 和路由尝试，短暂停租后继续/恢复，不另建候选；重启恢复也不会把仍绑定当前待发送 delivery 的运行误判为孤儿。不可重试失败有界终止。原有自动/人工确认模式、Sender 目标绑定和回执核对保持有效。

AgentRun 的终态重复完成先在写事务外读取准确原生引用。独立微信原生
`AgentEnvelope` 按 Runner 同一解析及依赖失败规则转换为 `CodexDecision`，
不能直接比较两个不同契约。`BEGIN IMMEDIATE` 内再次核对运行、任务代次、
角色、状态、session、转写边界和被选中的 completed runtime attempt；引用变化
或来源缺失不能被视为成功重放。候选及审核记录仍按既有事务校验。返回对象的
原生轨迹加载发生在写事务释放后，不用 SQL 正文副本代替原生证据。这只是
终态收尾路径的锁暴露修复，不代表全部生产延迟或 SQLite 竞争已经根治。

微信专用 Runner 在首轮 Developer 指令中给出有效的最小 `AgentEnvelope` 字段示例；首次格式错误仍由原有同 session 校验修正处理，业务结果仍由完整模型校验。原生 output-schema 开关、路线、工具、会话和交付阶段均未因此改变。

同一 Dispatcher 还通过独立 adapter 领取普通 reply、meeting、work summary、OKR review、
当前 BusinessTask 的 DingTalk Todo outbox 和任务长期记忆写入（`task_memory_write`）。adapter 只读写各自既有事实来源，并统一 claim generation、lease、唤醒、
公平性和容量；Consumer 保持领域边界。主动唤醒之外的有界等待只用于跨进程写入和异常恢复，不是
用户配置。Status 为每个实际 adapter 展示 pending、oldest、running 和 latest error；scheduler
进程/扫描健康与 scheduled run 的业务结果分别展示，空队列不会制造 Agent run。

默认业务生产任务通过稳定 migration key 幂等 seed，共十个：邮件、钉钉消息、钉钉日历邀请、每小时
`:30` 的“恢复近期 DingTalk 消息”、会议、微信 reader、OA、会议行动项、每周 OKR，以及每天
`20:00`（`Asia/Shanghai`）的 AI 听记同步。十个任务全部是服务命令，旧的 producer timing loops
已移除。以 Agent 形式创建的旧 `dingtalk-message-check-v1`、`wechat-message-check-v1`、会议、OA、工作来源和
`ceo-minutes-sync-daily-v1` 在启动时原地转换为命令形式（保留名称、Cron、时区和启用状态，已删除的不动）。
其中“会议行动项”命令还会在同一轮只读发现管理周报、项目管理部周报和部门产研周报；报告按文档内容摘要建立带
文档 ID、链接、统计周期和内容 digest 的 `work_summary` 输入，重复扫描同一 digest 不重复入队。较大输入的逻辑 JSON 字段使用带版本标记的无损压缩 BLOB，最终 Tasks 向量保留其 SQLite 值类型；Task Agent、语义导入、项目修复和 Attention 投影在读取边界解码。副本回放以当前投影返回的精确 TEXT/BLOB 值做 CAS、通过当前 Tasks 写入器沿用既有 codec，准确来源／状态／正文指纹及 lease 校验不变；小 payload 保持普通 JSON。`python -m app.work_summary_payload --db <database>` 只对仍需保留正文的非终态行按小批事务压缩，可续跑；done/skipped 行继续使用终态精简，不删数据或自动 VACUUM。
新安装创建的全部默认任务都是暂停状态，用户配好连接器后自行启用；seed 从不改变已有任务的启用状态
（Derek 2026-09-23）。AI 听记同步不再需要 Skill 判断：分页读取
摘要与逐字稿、写入本地归档、维护内容游标都由 `app/minutes_sync.py` 确定性完成，时长不足五分钟的
会议直接跳过。成功运行的归档命令单行结果摘要会持久化到 scheduled run，并显示在定时任务运行记录中，包含
`discovered`、`synced`、`skipped`、`permission_requested`、`permission_pending`、`failed` 计数及可操作的跳过明细。
History 也显示定时触发（Derek 2026-09-25，类型「定时命令」/「定时任务」，规则见 `docs/architecture.md` 的
「History 语义」）：任何定时任务的失败触发（之后同一任务成功过一次即为 `recovered`）、Agent 形式任务因不可用
被跳过的触发，以及结果不交给 Agent 的服务命令（听记同步、听记权限申请、OKR 周报）的每次成功运行；把结果交给
Agent 的生产命令成功时不进 History，它们排入的每一项各自是一条 History。邮件 provider 动作同样进 History
（类型「邮件动作」），失败口径与 Attention 相同。

History 的回复类记录以 task 为核心：列表行带 `task_id` 与 `run_count`，行内链接指向
`/history/tasks/<task_id>`；该页只读取 `tasks`、`runs` 和 Attempt 投影，列出 task 的全部 run
（含历史执行代）与 Attempt，Attempt 详情页保持原样（规则见 `docs/architecture.md` 的「History 语义」）。

微信 History 的任务完成与消息投递分别投影：同一任务、会话和当前执行代的最新投递为
`failed` 或 `send_unknown` 时，失败 Attempt 继续在列表筛选、详情、状态计数和图表中显示
`failed`，不能被候选任务的 `done` 掩盖。旧代、其他会话和已有后续成功投递的失败不覆盖
当前状态；旧 pending Attempt 对应已完成任务仍显示 `done`。这些读取不改写历史记录或投递
状态，也不派发重试。
该投递优先规则只覆盖物理 `send_status=failed` 的 Attempt；非失败历史行继续按原任务投影，
列表、详情、队列计数和图表不得各用不同的覆盖条件。
最新投递按 `(reply_task_id, execution_generation, id)` 非唯一覆盖索引关联，避免对每条失败
Attempt 重扫全部微信投递历史。结构哨兵要求该索引存在，初始化在旧投递表重建后补齐；
只补索引，不改写回执、失败或发送状态。生产升级先完成并验证 SQLite 备份。
听记权限扫描在打开后台后显式查询最近 30 天至次日的历史记录；后台默认只筛当天，
空表不能直接归因为账号缺少管理权限。申请按持久化的 `requested_ids` 去重，
只有听记页面读回已申请状态才计为成功。
退订页返回的固定外部拒绝类别即使附带 `;operation=...` 阶段信息，也保留失败历史、
归为外部依赖故障而不计入待工程修复的 Attention；浏览器/会话自身错误仍计入 Attention。
## Chrome 登录态副本（系统服务）

OKR 服务包装器与共享来源复用同一有效请求头收集器：首次请求头缺少令牌或令牌已过期时继续等待后续有效请求，不能用第一条不完整请求锁定整个刷新轮次。只有满足有效期与提前刷新窗口的令牌才进入缓存，实际 API 读取前仍校验有效期；不输出令牌或认证头。

OKR 本机 SSO 在点击当前账号后先检查指定组织是否已可见；组织选择页仍使用 `login.dingtalk.com`，不能仅凭该域名就认定需要原生确认弹窗。指定组织已可见时直接选择原组织；尚未可见才进入既有原生叮当 OKR 确认路径。组织选择失败时，只有 URL 的协议、域名和路径与配置的 OKR 入口一致，才允许单组织流程省略选择；空白页、其他域名或同域其他页面不能被记为已跳转成功。此区分不改变账号、组织、权限或无头实时来源要求。

需要登录的无头浏览器任务（退订链接、听记权限申请、Dingteam OKR）不各自重新登录：服务每天 `06:00`（`Asia/Shanghai`）运行“同步 Chrome 登录态”（`sync-chrome-cookies`，定时任务，`chrome-cookie-copy-daily-v1`），用 SQLite 备份接口把 `~/Library/Application Support/Google/Chrome/Default/Cookies` 复制到服务数据库旁的 `chrome-cookies/Default/Cookies`，再按明文 `host_key` 删掉 `CEO_CHROME_COOKIE_DENY_DOMAINS`（逗号分隔的域名，含子域名）里的银行、券商和支付类域名，其余全部保留供各任务复用；这份名单为空时命令拒绝执行。整个过程不解密任何 cookie，也不碰钥匙串。 Dingteam OKR token 缓存读取时先验证 JWT 有效期；过期或将过期的缓存不会返回，调用者在锁内重新读取缓存后再尝试既有 Chrome/本机 SSO 刷新。新捕获 token 也先验证再写入；认证仍不可用时保留认证失败。

所有任务只通过 `app/service_browser.py` 的 `launch_service_chrome` 启动浏览器：真 Chrome、始终无头、指向调用方自己的资料目录；启动前把最新的副本放进该目录的 `Default/Cookies`，并去掉 Playwright 默认的 `--use-mock-keychain`（否则 Chrome 解不开复制来的 cookie）。这样由真 Chrome 自己读“Chrome Safe Storage”钥匙串项，不会弹授权。副本是快照，会话过期后等下一次同步；不连接你正在用的 Chrome（Chrome 136 起默认资料目录不允许远程调试）。

另有一个每天 `19:30`（`Asia/Shanghai`）运行的“申请读不到的钉钉 AI 听记”服务命令：读取听记管理后台，逐条在听记页面提交访问申请，并以页面读回状态计数；对方批准后，`20:00` 的归档任务会读取内容。该后台由服务的共享无头 Chrome 打开（`app/service_browser.launch_service_chrome`），带的是每天复制的 Derek 本人 Chrome 登录态；这份副本能把他认到「选择你管理的组织」那一步，再由代码选中组织列表里的第一个组织（Derek 2026-10-23：不写死公司名）进入控制台。每次启动都会用当天的副本覆盖 profile，控制台自己的 `access_token` 不会留到下一次，因此不会跨运行保存该令牌。登录页加载超时以及组织选择或其后的控制台跳转超时统一按登录失效处理，任务保持失败；组织选择和控制台跳转各有 10 秒上限。服务会直接启动用户的 Google Chrome 并用新窗口前置打开听记后台，供用户完成正常登录或检查组织选择，而不是只在后台留一个标签。登录后仍需让每天的 `sync-chrome-cookies` 将最新登录态复制到服务 profile，后续任务才能继续；没有通过扫码或程序保存凭据来绕过登录。申请命令结果摘要同样持久化在 scheduled run，并显示在定时任务运行记录中，包括本次发现、已申请、已可读、申请人未解析和失败数量。

定时任务共用现有调度、队列、Consumer → Audit → revision 生命周期；14 个默认任务由声明表生成，默认新安装仍暂停。11 项发现/同步任务保留服务命令，CEO 日报、CEO 管理周报和 OKR 管理周报是三项普通 Agent 任务。业务要求由任务 prompt 与 Skill 决定，系统层不按报告名称或 Skill 名称分支，也不以 `publishable` 判断业务质量。通用 `read_scheduled_context` 返回触发时刻、时区、版本及历史完成事实；Consumer 还会收到当前 task generation 中已有产物的文件名和 SHA-256 清单，不包含文件正文，以便重试先复用当前执行已有材料。`read_service_activity` 按调用者明确给出的时间窗口和来源返回服务事实；定时任务的历史活动与群消息读取拒绝晚于本次触发 `scheduled_for` 的结束时间，以免报告触发后发生的数据被误认为当时已覆盖。非定时任务与面向未来的日历读取不受此限制。`read_meeting_archive` 按会议身份读取归档。Codex 与 Claude 均可通过 Consumer 的 `consumer_run_skill_script` 执行所选 Skill 的确定性 Python 脚本。脚本在当前 task generation 的产物目录运行；产物读回分页，完整正文留在本地产物中。

产物 offset 是从0起的字符索引；每页统一返回 offset、total_chars、next_offset，sha256 始终对应整份文件。完整读取从0开始，按 next_offset 续读至 null，不能把非零起点的尾部当作全文。

定时任务固定 Agent 案例在独立离线原生 CLI 中运行。评测 Consumer、Audit 和修订共享同一传输字段、标签与身份投影定义；业务规则仍来自各自精确 Skill 快照。冻结案例和评分函数，不改标历史失败；首轮、一次审核修订、实际正文/动作草稿与外部发布分别记录。参见 [Agent 验收记录](scheduled-task-generic-agent-validation.md)。评测协议不改变生产 Consumer/Audit 契约。

`consumer_document_write` 接受正文产物、标题和显式目标；更新使用 node_id，创建使用 parent_id 与 workspace_id。JSONML 由原生解析器检查，更新保留 revision 校验与版本保存，创建前核对同名目标。返回 provider 结果及完整读回产物的引用；Agent/Audit 读取全部分页或使用本地脚本比较。DWS 命令失败诊断会隐藏 `--content` 和 `--content-file` 的值，不把文档正文或本地内容路径写入 trajectory。消息继续通过既有已审核投递通道发送，普通文档工作与受控消息动作沿用原角色契约。

日报窗口由 `ceo-daily-report/scripts/window.py` 按上次真实完成的历史事实计算，漏发并入下一份，同日重跑沿用原起点。日报位置与通知内容归该 Skill。Consumer 先读取服务事实和群消息；仅当会议管理细节仍缺失时，才读取本任务冻结的 `dingtalk-minutes` Skill 并查询听记摘要。管理周报的周一窗口、目标/上期文档、空白模板策略和三处内容更新归 `ceo-weekly-report` 的材料/渲染脚本；渲染器只检查实际读取字段与 JSONML，固定指标、材料覆盖与章节等业务要求由 Skill 和 Audit 判断。三处写入仍为 CEO 判断、公司指标和新增跟踪行。

OKR 报告的应用层只读取明确群名的稳定名单和明确人员/周期的实时数据及来源回执。每人一次读取、单次最多 240 秒，模型可见真实步骤结果；纯本地 `ceo-weekly-okr-report/scripts/report.py` 按稳定 userId 准备输入、计算原有精确公式并渲染。Agent 保存成员产物以恢复进度，发布管理报告、成员子页并提出群摘要消息。恢复的 Consumer turn 先读 Audit 与产物，再刷新名单和每位成员当前来源各一次；由于没有独立摘要查询接口，刷新读取用于发现来源变化。prepare 对照新摘要复用未变化成员的完整分析，只重做来源变化或分析未完成成员；同一 turn 内成功的来源读取不重复。已经保存的成员读取回执也只在同一 task generation 内、且 userId、姓名、职级、钉钉身份、周期均完全匹配时复用；新 generation 会重新读取，`source_failed` 回执不会缓存复用。来源摘要相同但带 `analysis_failure` 占位的文件不算完成、不复用。逐 KR 检索独立证据，零进度或未找到佐证仍按既有评分规则完成并说明证据缺口；领导力和文化分数须由各自行为证据支持，禁止跨维度借证，Audit逐人核对七项维度证据和分数。未评分成员保留已知职级与逐人来源缺口，不生成分数。业务 KR 的完成档位与领导力/文化行为档位分别应用；行为评分使用正式维度，70–79 为符合标准，80 分及以上须有明确超出当前职级标准的具体效果（经理日常辅导、总监在团队内复用方法本身仍可属于本职标准），90 分及以上须具备对应榜样范围证据；显示分数保留一位小数并采用通常四舍五入。每次渲染后都按当前 manifest 写入或更新并完整读回全部报告和附录，即使成员分析没变；旧标题及旧读回不能代替本次结果。群提案逐字使用当前 summary.md；这些步骤由 Skill 与现有 Audit 承担，未新增服务发布门槛。原独立周报 Agent 子任务、租约/缓存编排和星期日重复调度逻辑退出当前执行路径；历史记录保留。

OKR 报告的进展标题使用“已核实进展”并以报告日为截止，不能将没有来源日期的期间结果标作“本周”新增进展；管理者表格列同样只陈述进展与证据。

发布时，现有 Consumer system contract publication 原子发布 Skill 文件、新脚本与新 managed revision，在服务停止窗口迁移三项报告任务定义，记录完整前后配置和版本；启动 seed 再运行保持幂等。待恢复的旧报告执行必须先自然结束；核查未完成执行的原始快照，不依赖当前任务仍保留旧命令或 Skill 引用。发布清单的 `old_sha256` 必须对应当前已安装文件；若文件已经与候选版本一致，可将 old/new 摘要都记为该版本，以便校验与精确回滚继续成立，不提供宽松的旧版本匹配。失败回滚按精确版本恢复任务配置、runtime config 和既有文件，并移除本次新建文件；任务 ID、历史触发快照、周期、时区和用户自定义文本保留。

访问申请页等待申请按钮实际可用；非权限加载壳不能当作已可读，只有听记读取 API 确认可读。Lark 没有默认定时任务 seed。

内部投递、发送状态确认、错误恢复仍是内部机制，不外化为 Cron；催办不自动发送，只由 Derek 点按钮发送。

Task Agent 的正常 liveness 由结构化 provider stdout 事件驱动：执行器在完整事件行被消费后续租
Agent run，并把它作为有效进展证据；stderr 和未完成的 stdout 片段不刷新进展时间。生产环境连续
300 秒没有这样的事件时，idle watchdog 才会中断当前 turn；`CEO_TASK_CODEX_TIMEOUT_SECONDS=7200`
只是防止泄漏进程长期占用资源的紧急上限，不是长任务的业务完成时限。任务可以在同一 session
上持续或恢复多个 turn，原有同 revision 的重复失败上限仍然有效。

### 应用层边界

应用层不审核 Agent 使用的命令、MCP 工具、Skill、读写模式或工具名称，也不维护
`side_effect_state`、`unknown`、`reconciled` 等业务状态。应用层只校验最终 typed result 的形状，
推进 `done`、`failed`、`skipped` 和 `needs_human`（反馈闭环期间任务停留在 `processing`），并保存去重所需的最小外部事实：
`operation`、`target`、provider 稳定结果标识。纯读取不需要 receipt；写入中断时由下一次 Agent turn
按业务 Skill 读取目标状态，服务不启动专门的只读核对回合，也不因“未知工具”阻断执行。

Agent 也不得用嵌套的 `codex mcp list` 或 `codex exec` 子进程结果判断当前父 Agent session 的
MCP 是否注入；这类子进程只说明子 CLI 环境，不是当前 turn 的能力事实。需要 MCP 证据时必须在
当前 Agent session 中直接调用对应 MCP 工具；如果直接调用或 provider 操作失败，才可以把该具体
工具错误作为依赖失败返回。仅自报 `<server>_mcp_not_injected` 属于结果契约错误，会触发修正轮，
不能终结业务任务。

历史数据库升级时会移除 `agent_runs` 中的 `unknown`、`side_effect_state`、effect counter 和
reconciliation 投影列；旧 `unknown` run 的当前状态迁移为 `failed`，并追加一条
`legacy_unknown_migrated` state event 保存当时的原始错误。已有 session、runtime attempt、tool event、
provider 结果和旧错误事件不改写、不删除。旧 spec/plan 中描述这些状态机的内容属于历史设计，
不应作为实现依据。

反馈、人工重跑、进程失败和 typed-result 失败都会创建新的 append-only `agent_run`，但不会创建
新的业务 `reply_attempt`。只要原 runtime session 仍可访问，新 run 就向原 session 发送
continuation；新 revision 或 Skill/契约版本表示业务规则/结果版本前进，不表示必须创建新 session。
每个 turn 都记录当前契约哈希作为回执，但哈希变化不能切断上下文。只有 provider 明确返回 session
不存在或认证上下文失效时，才创建新 session。

Agent 返回的错误只是它观察到的现象，含义由服务决定（`app/agent_reported_error.py`）。wire 结果里的
`error_retryable` 与 `error_authorization_required` 不被读取；`error_code` 先统一为小写，服务认识的码
（需要授权或确认、dry run、可自行恢复的依赖、浏览器工具回报的退订码，以及缺少已声明操作的 `operation_unavailable`、`capability_unavailable`、`write_capability_unavailable`）按服务政策决定是否重试、
是否等人；其他任何失败码记为 `agent_reported_failure`，走有上限的普通重试（同一 role、generation、revision 连续失败 4 次 turn，即初始调用加三次 retry，
终止为 `failed`，见上文），Agent 原文保存在 `source_code` 供排查。非失败结果（`needs_human`、`no_action` 等）上的码只作为原因标签，不驱动重试
或授权。编排层依赖的服务码（`runtime_*`、`codex_provider_*`、租约与恢复类）只能由服务写入，
Agent 写入时一律按 `agent_reported_failure` 处理。

服务反馈回调的结果格式校验失败属于结果解析失败：保留原始 `feedback_callback_pair_invalid` 在 `detail`，通过 `ResultParseError` 进入现有 `codex_result_invalid` 纠错流程；每次处理最多两个回合，同一角色/代次/修订连续四次失败后终止为 `failed`（首次调用加三次 retry）。校验不能抛出普通 `ValueError` 绕过编排后由调度器无限重新领取。该异常转换不改变反馈链接配对规则、历史来源校验、审核职责或调度器所有权。

**原则：任何更底层观察到的错误码或原文，被归并成更通用的服务码时都不能丢失，必须原样带到 Agent 报错记录里，供事后排查。** 通用化是给重试/授权决策用的分类，不是删除诊断信息的许可。已落实的例子：`agent_reported_failure` 保留 Agent 原文在 `source_code`；Agent 报告的 typed 失败结果保留其必填的 `summary` 在 `reported_summary`（2026-09-28，见下段）；Codex 进程失败保留 stderr 与 JSONL 里的 `error`/`message`/`detail`/`reason` 字段在 `detail`（`_process_failure_detail`，裁剪到 1000 字符、脱敏凭证）；路由执行错误沿 `__cause__` 链找具体解析/校验异常的 `reason`（`_runtime_failure_detail`）；退订浏览器错误保留异常类名和截断消息（见上文退订段落）。新增一处错误归并时必须同样保留来源，不能只留下分类码。

任务 Agent 的 `memory_recall_used` 是 Agent 给出的上下文记录，不是服务的工具调用验收条件。
Task Agent 的 Memory 写入边界由提示词约束：可调用 `memory_connector.memory_write` 保存
当前证据支持的持久、重要项目风险与进度/状态更新，注明项目与原始来源，并以来源时间作为
`created_at`，使用 `type="text"`；没有持久项目变化则不写。此行为由 prompt 引导，不是工具层权限隔离。

Task Agent 不直接调用外部 TODO 写入；Task 7 的创建/完成 intent 在 Task 语义事务中排入
`business_task_todo_sync_outbox`，由 dispatcher 按 `business_task_id` 执行。创建仅限正式、开放、
有来源支持的明确负责人、已接受承诺且有来源支持的可解析 `committed_deadline_at` 的 Task；
estimate、requester/external deadline 和 next check 均不能充当镜像期限。外部创建保存 provider ID
再读回；若首次读回失败但 ID 已保存，后续 TODO 状态拉取按该 ID 重试读回并收口链接/outbox；若创建
结果未知且没有 ID，不自动重复调用或排入另一创建键。同一 Task 存在未收口创建 intent 时不会再排新的
创建。`failed` outbox 在有界 `next_attempt_at` 退避后才可再次领取。外部 TODO 状态轮询只按其 Task
链接关闭 Task，写入完成来源证据、事件，并关闭该 Task 的 follow-up；关联 Project 或同聚类 Task 不随之完成。

旧 `work_todo_id` 镜像入口、`TaskTodoSyncOutboxQueueAdapter`、旧 completion/retry/
reconcile 写入及 `reclassify_unmirrored_todo_outbox.py` 维护命令已退休。
`maybe_create_dingtalk_todo` 必须提供当前 `business_task_id`；Todo dispatcher adapter
及 Status 中该 adapter 的队列展示只保留当前 BusinessTask Todo outbox。旧 link/outbox 的 schema、已保存状态、来源和
provider 回执保持原样，读取仍供 History、已发送 Todo 和 legacy-project 详情使用。
旧 Work Project、Work Todo、Work Update 的五个无调用者 Store 创建/更新方法已移除；
保留各自 typed 历史行、reader 和由明确来源清单驱动的 task-semantic-import apply。
Derek 2026-10-20 选择：旧记录无法核验 current Task 身份时仅保留历史，不按公共整数
ID 关联或晋升、不取消或完成外部 Todo、不重发。完成扫描和未知创建回执核对仍只读取
当前 BusinessTask 的链接；旧 `active` 不再触发任何 provider 操作。独立 D1c 已从 Status 聚合移除旧 `DingTalk Todos` 及其专属 retryable 分支，
从小时质量检查的 required/optional 来源与外部队列检查中移除旧 link/outbox。
语义导入、物理 schema、历史数据和读取未改变；这仍非完整 D 或 live 验收。

**催办由 Derek 点按钮发送**（Derek 2026-09-25：「催办应该变成 UI 上的一个按钮，让用户决定是否要发送催办（点击一键发送），如果后面有新的信息更新了 task，应该取消可催办状态」）：Task 详情页「催办」区列出该 Task 的全部 follow-up；待发送（`draft`/`approved`）和发送失败（`failed`）的每条带一个发送按钮，点击调用 `POST /api/console/tasks/items/{task_id}/follow-ups/{follow_up_id}/send`（带当前 `revision`），由 `send_business_task_follow_up` 当场发送：不看工作时间、不经 Agent 审核，点击就是决定。失败或结果未知的发送保持 `failed` 并把结果留在该行上，不排 Agent 修复；再点一次作为新 revision 发送，原 attempt 不改。Task Agent 把新信息应用到已有 Task 时，在同一事务里用 `cancel_pending_business_task_follow_ups` 撤回该 Task 其余未发出的 follow-up（`cancelled`，原因写明被哪条来源更新），同一来源信号新建的 follow-up 保留，正在发送的不动。

Task 7 的新 follow-up 只从链接来源信号的精确群/单聊目标与明确、可解析 `next_check_at` 创建，
不从 deadline 推导时间或猜收件人；`next_check_at` 只是建议的催办时间，不会到点自动发送。群聊回到精确
来源会话并提及来源证据支持的负责人；单聊发给来源明确指定的负责人账号，原会话 ID 保留用于核对。发送
（仅由上述按钮触发）有独立的 claim、lease、revision、幂等 UUID 和回执记录；发送中的租约过期或网络中断
标为 `unknown`，该 follow-up 显示为 `failed`，不自动重发，也不排 Agent 核查，是否再发由 Derek 再点一次决定。
旧 `follow_up_drafts` 只是历史记录：不会再被发送，失败的旧记录也不再提供「让 Agent 重新核验负责人 /
取消本次跟进」表单（`/follow-ups/{id}/resolution-form` 已删除）；小时质检和安装向导的 dry-run 检查
不再把未发送或失败的旧记录当作积压。Task 8 的旧记录导入与生产切换仍是独立边界。

### Task 的来源与证据

Derek 2026-09-25 定的规则。Task Agent 用同一个长期 session 是为了不丢上下文，所以**不禁止它用之前的证据**，也不禁止它通过 `memory_recall` 顺着 provenance 找到原始来源；但每条来源都要能回溯到出处。

Console 的来源读接口分两步保持正文唯一：
`GET /api/console/tasks/projects/{project_id}/evidence?page=N&page_size=M` 只分页返回该 Project
保存的 `BusinessProjectEvidence` 引用，沿用 Store 的“最新窗口优先、窗口内既有顺序”，并返回
total/has_more/next_cursor；`GET /api/console/tasks/signals/{signal_id}` 再按精确 ID 返回完整保存的
`BusinessTaskSignal` 和不可变 source document 原文。长 JSON/原始正文不使用详情页的 2048 字符
投影，也不经过 display normalization。未知 Project/Signal 返回 404，已存在但无证据的 Project
返回空列表 200；读取在同一个 `read_snapshot` 中完成，不更新 Task、Project、Signal 或证据关系。
Project 详情仍只展示原有 20 条有界来源并保留 context 引文 pinned 语义。

**每个决定引用什么**

| 字段 | 含义 |
| --- | --- |
| `evidence_origin` | `current`（当前 Work Item，默认）、`session`（此前 session 里读到的）、`memory`（Memory provenance 指向的原始来源） |
| `source_ref` | 来源引用；`session` / `memory` 时是**原始来源**的引用 |
| `source_excerpt` | prompt、Skill 和字段说明要求原文中连续的逐字摘录，保留标点、空格和换行；普通 Task 的原有校验仍不检查来源子串，日期证据及 Project assessment / Attention 引文仍按各自既有原文规则校验 |
| `source_link` | 来源有链接就必须给 |
| `source_description` | 没有链接时用文字描述在哪里，例如钉钉消息写“群 + 发送人”；`source_group` 加 `source_person` 也算 |

- 对 `session` / `memory`，链接和描述至少要有一个，否则模型校验拒绝。对 `current`，缺的定位由服务补：URL 引用、听记摘要里的页面链接、或会话加发送人；都没有时记录里仍有 `source_ref`。
- 普通 Task 引文的原有校验仍不检查是不是原文子串，逐字摘录是 Agent 指令，不是新加入的验证器。仍然要求：引文非空；负责人的名字必须出现在负责人引文里；日期证据仍要求是来源子串。Project assessment / Attention 的原文核验按前述独立契约执行。
- `session` / `memory` 只能用于完善已有 Task（`update_fields`）或记录候选。创建正式 Task、晋升、确认接受、身份合并仍要当前 Work Item 的授权与身份元数据；日期证据仍要当前来源和明确说话人。
- 引用的旧证据单独存成一条来源信号：`source_type` 为 `session_provenance` 或 `memory_provenance`，引文作证据文本，链接和描述放 `context_json`（`source_link`、`source_description`），群和人放会话标题与作者名，`cited_while_processing` 记录当时在处理哪个 Work Item。服务无法重读原文，所以这条记录标明的是“引用而非当下观察”。

### 展示型任务建议的领域存储

`RecordTaskSuggestion` 在既有业务 Task 表保存来源支持的建议，本分支 Task Agent 已接入 `TaskDecision.suggestion`，页面按当前 candidate 建议与真实晋升 Task 区分。新建议固定为 open candidate、commitment=none、无 formal_basis，实际 owner 和 deadline 为空；建议人选、理由、职责与事实的真实 Signal 引用仅在 `suggestion_json`。职责证明可以来自先前项目/人员分工，不要求最新风险原文点名，但不能把 Agent 理由当原始来源。建议命令只使用同一领域事务的 Task/Signal/evidence/event/Project link，既有建议 ID 的相同资料不追加事件，实际变化追加 details_changed；无 ID 的原来源重放保留最初任务身份，包括之后已由人类晋升的情况。建议更新省略标题或描述时保留原值，不把默认空字符串当成清空。

无 ID 的重放身份来自原创建或其他已有事件的命令结果，不来自所有 evidence link。后来来源只补证据且资料未变时，不制造变更事件，应用及重放都保留明确 task_id，由 Agent 对已检索事项判断关联。一条来源可支持多个 Task，合并也会复制证据关系，不能据此自动选定唯一任务或合并。

人类明确指派用既有 promotion 在同一 ID 晋升，保留 origin=agent_suggestion 和建议依据，实际 owner 才来自人类指派证明；真实接受、承诺日期与 TODO 资格仍按原生命周期。展示建议不产生 acceptance、follow-up、通知或 TODO outbox；不会因为历史 origin 永久拦住已由人类接受的正式任务。旧任务只增 source/空建议默认列，阶段、承诺、负责人及历史 JSON 不变。
这里复用已有“原始观察／未观察的引用”区分：项目证据、上下文引文及建议的发现/职责/事实来源不接受 `memory_provenance` 或 `session_provenance` 作为原文。并非限制工具权限，也不按正文关键词分类；原有一般候选 Task 使用 memory/session 完善信息的语义不变。

**批处理里的容错**：同一场会议的多个条目在一次提交里应用，一条不成立不应拖垮其余。

- 负责人证据不成立（负责人的名字不在引文里）的更新，只跳过那一条并写明原因。
- 一条只重复 Task 现状的 `update_fields`（标题、状态等都没变，且“unknown”的相关性不算判断）记为跳过，不报错。
- 检索上下文一定包含同一来源此前产生的 Task，不论词面排名，否则 Agent 看不到它们，会把同一件事再记一遍。

### 听记行动项的负责人

**问题**：钉钉从听记抽出的行动项，`executorList` 恒为空，负责人不在行动项里。最初只从对话里找：每条行动项带 `createdTime`（在录音里的毫秒位置），逐字稿每段带说话人昵称和起止毫秒，截一段窗口出来找负责人；但窗口只锚定在行动项被抽取的那一刻，指派往往发生在窗口之外——9-28 复查发现至少 4/7 条“无负责人”候选任务，负责人其实写在钉钉自己生成的会议摘要里，只是不在这个窗口内（Derek：肯定是信息没有挖掘全）。所以负责人来源现在有两处：转录窗口，和整份会议摘要。

Task Agent 会把负责人证据再结构化为 `owner_kind`（`individual` / `team` / `unknown`）和 `owner_relation`（明确分派、自承诺、会议行动项、仅发言或未知）。只有明确指向单个个人且关系为前三种之一的行动项，才记录为正式 `meeting_action_item` Task，并保持 `assigned_unaccepted`；团队负责人、仅发言或关系不明仍是候选。缺少稳定 ID、日期或完成标准属于后续补充，不会单独把已经明确指派的行动项降级为候选。AI Minutes 行动项不以 `external_todo` 作为正式依据。

**做法**（`app/minutes_todo_context.py`，由 `scan_meeting_todos` 调用）：

1. 入队前读完整份逐字稿，**再读一次这场会议自己的 DingTalk 摘要**（`get_minutes_summary`，`minutes_full_summary` 取其 `result.fullSummary`）。
2. 转录窗口：对每条有有效 `createdTime` 的行动项，截取包含该时刻的那一段及前 6 段、后 4 段，每句写成一行“说话人：内容”放进 `transcript_excerpts`（钉钉的 `sentenceList` 把整段当作一句，所以在句末标点处自己拆句）。
3. 会议摘要：原样整段放进 `meeting_summary`，不做关键词或分节切片——钉钉的摘要经常直接写“行动项：**磊哥**与**周俊杰**负责代码 Review”，但可能出现在整场会议的任何位置，切片会漏掉切片规则没覆盖到的写法。
4. 没有摘录/摘要的情况：`createdTime` 缺失或为 0、超出逐字稿的行动项在转录窗口里没有摘录；逐字稿为空、或摘要为空字符串，对应字段就不放进去。逐字稿读取失败时仍继续读会议摘要，并把 `source_transcript_unavailable=true` 标记交给 Task Agent；Agent 只能基于实际传入的行动项和摘要作判断，不得从缺失转写中推断负责人、承诺或指派。若会议摘要读取失败，才将该会议保留为未入队、未记为已见并由下一轮重读。
5. 去重摘要仍只覆盖行动项本身，所以之后补读逐字稿或摘要不会让处理过的会议重复入队。

**提示词规则**：负责人是对话把事情交给的人或接下的人，不一定是说话人（甲说“你写下来”是指派给被点名的乙）；负责人引文可以来自 `transcript_excerpts` 或 `meeting_summary`，是一句带说话人标签、包含负责人名字的话（可摘取），常常不是决定本身的 `source_excerpt`；钉钉对认不出的说话人给的“发言人 N”是占位标签、团队或部门名（“研发”“算法团队”“Product Marketing”）也不是人，两者都绝不作负责人；转录窗口和摘要都说不清就留空——这是来源本身的边界，不是要去凑一个负责人出来。转写不可用标记存在时，Agent 只能使用实际可读的行动项和会议摘要，不得补造转写证据；没有明确个人负责人时仍不得将行动项升级为正式分派任务。日期仍不从听记取，因为没有可信的说话人到身份映射。

**回填已入库的候选**（已处理过的会议不会被再读到，所以必须回填）：`python -m app.cli backfill-minutes-owners`（`app/minutes_owner_backfill.py`）。

- 先从 Signal 事实只选符合条件的会议 UUID 与候选 Task ID，再按该 UUID 读取当前会议记录，核对响应记录的 UUID；缺失或不符时跳过该会议。只用该真实当前记录重读行动项、逐字稿和会议摘要，组装新的 Work Item，以新的来源引用再入队一次，由 Task Agent 用普通更新路径补负责人；旧库已存正文不能代替当前会议记录。
- 默认只预演；`--apply` 才入队；`--limit N` 取最近的 N 场。
- 转录窗口和摘要至少要有一个能读到才入队；两个都读不到才跳过该会议，`decisions` 里写明原因。
- 来源引用里的摘要覆盖行动项、转录摘录、会议摘要和 `BACKFILL_REVISION`，所以重复运行不会重复入队；改了提示词、引文格式或校验规则后递增这个版本号，才会让上一轮没补上的会议再读一次（2026-09-28 起是 4）。

### 控制台对候选 Task 的操作

Derek 2026-09-25。候选只是没确认的猜测，所以可以在控制台“忽略”，也可以“恢复”。Tasks 页面将“正式任务”和“待确认线索”分成独立视图；“全部任务”保留为诊断入口，可同时查看两种 stage，但不作为正式任务的语义替代。默认关注页仍只展示 CEO 需关注投影，正式项目单独展示。

- Tasks 页“待确认线索”（以及诊断入口“全部任务”）的候选行和候选 Task 详情页有忽略按钮，调用 `POST /api/console/tasks/items/{task_id}/candidate-decision`（`action` 为 `ignore` 或 `restore`，实现在 `app/task_console_actions.py`）。
- 忽略把 Task 状态改为 `cancelled`（阶段仍是 `candidate`），恢复改回 `open`。走普通的 `TaskSemanticService.update_task`，留下与其他变更相同的痕迹：一条 Derek 署名的 `console` 来源信号（`author_kind=human`）、`correction` 证据链接和 `status_changed` 事件。原发现证据不动，什么都不删除。
- 来源引用带上一次的 `updated_at`，同一状态下重复点击返回 409 `not_applicable`，不重复记录。提交后照 Task Agent 的做法重算受影响的关注成员。
- 只有候选可以忽略：正式 Task 有负责人和来源引文，状态只能由证据推动。控制台**不提供**“确认为正式任务”——晋升要求已识别的负责人和来自来源的负责人原话，点击给不出这两样，见 `docs/task-semantic-storage.md`。
- 忽略后的 Task 仍在“全部任务”里（灰显、划线），Task Agent 见到已取消的候选不会自行恢复它。
- `GET /api/console/tasks/all` 另接受 `owner=assigned|unassigned` 和 `sort=updated|created`，非法取值返回 400。
- 服务写入的固定事件原因现在是中文（如“根据来源证据记录为候选任务”）；此前写入的英文原因是历史记录，不改写。候选没有负责人是正常状态，界面不把它当缺口。


## 任务类型

- `okr_review`：指定人员和周期的逐 KR 评审。执行 Agent 按当前业务 Skill 读取实时
  `processed.objectives`/`processed.okrRows`，再生成评审；审核 Agent 审阅并反馈修改，
  修正版通过后才发送。底层读取错误（认证失效、浏览器/profile 锁、周期解析失败等）
  必须原样保留，不能被 `consumer_retry_exhausted` 覆盖。服务入口先复用有效 token；
  缓存过期时使用现有 Chrome cookie 副本启动 headless 浏览器刷新，不启动额外可见窗口。
  登录页存在“扫码登录”标签时优先走本机 DingTalk SSO 的该标签、已登录账号头像、
  原生确认和目标组织选择；若导航直接抵达 OKR 入口则接受该导航。当前中文登录页不能用
  英文 `QR Code` 标签匹配；可见的“立即登录”按钮可能被遮罩挡住，因此只有扫码入口缺失时
  才尝试直接账号入口。SSO 选择器/组织缺失和导航错误必须保留实际异常，不能静默吞掉后
  统一报“会话过期”；有限等待超时仍按真实认证/浏览器故障失败，不绕过验证或伪造取数成功。
  该任务的 `StructuredCodexRunner` 使用 `app/schemas/okr_review.schema.json` 声明
  原生输出：外围仍为通用 AgentEnvelope，`domain_payload` 为现有 OKR 模型的完整
  字段，所有 object 具备具体 properties、全部 required 字段和 `additionalProperties: false`。
- 管理者 OKR 周报使用普通 scheduled Agent 与 `ceo-weekly-okr-report` Skill。实时取数按成员执行；评分公式、身份绑定和渲染在 Skill 脚本，来源和发布事实使用既有记录。恢复的 Consumer turn 先读 Audit 与产物，再刷新名单和每位成员当前来源各一次；由于没有独立摘要查询接口，刷新读取用于发现来源变化。prepare 对照新摘要复用未变化成员的完整分析，只重做来源变化或分析未完成成员；同一 turn 内成功的来源读取不重复。来源摘要相同但带 `analysis_failure` 占位的文件不算完成、不复用。逐 KR 检索独立证据，零进度或未找到佐证仍按既有评分规则完成并说明证据缺口；每个领导力和文化分数须由相应维度的行为证据支持，Audit 逐人核对七项；本轮未完成时保留完成项并使用正常有界重试，不能用 `analysis_failure` 代替未完成分析。历史 weekly_okr runtime/分析记录保留，当前调度不再创建该独立工作流。

## 文档索引

- 总体 A/B 架构：`docs/architecture.md`
- 路由失败和恢复：`docs/runtime-route-recovery.md`
- Runtime 路由、fallback 决策与 429 重试：`docs/architecture.md` 的「Agent 失败重试」与各 Runtime 路由小节，
  实现在 `app/runtime_fallback.py`
- 429 同路线重试在失败 attempt 已观察 session 时续用该 session，并提交 `continue`；冷启动首次返回
  429 也保留从失败 attempt 取得的 session。无 session 时重放原任务输入。细节见
  `docs/runtime-route-recovery.md`。
- Consumer/Audit 反馈设计：`docs/superpowers/specs/2026-08-06-consumer-audit-agent-design.md`
- OKR 领域输入和输出：`docs/superpowers/specs/2026-06-08-okr-review-runner-design.md`
- 当前实现：`app/agent_orchestrator.py`、`app/consumer_agent.py`、`app/audit_agent.py`、`app/okr_review.py`、`app/weekly_okr_report.py`、`app/store.py`
- 系统错误码目录：[`docs/error-catalog.md`](error-catalog.md)
### Production deployment lifecycle

SQLite immediate-write retries apply only before the transaction body starts.
Once the body starts, a lock failure rolls back and propagates the original
exception; a context manager cannot replay its caller's body. SQLite failure
diagnostics include a bounded caller chain and thread identity, without frame
locals, SQL parameters, or message bodies. These diagnostics identify the
waiting operation, not the owner of a cross-process write lock.

A service-command trigger with a persisted execution link has already returned
from that command. If source terminalization was interrupted, its next consumer
resumes terminalization under the current claim guard instead of executing the
command or overwriting the immutable link again. The persisted execution kind
and command must match the trigger snapshot; mismatches remain errors. This
closes the result-persisted/source-pending crash window without replaying effects.

Production deployment is serialized at both the repository and service
boundaries. The deployer first waits for the persisted work leases to drain.
This includes valid dispatcher leases before an Agent runtime exists, meeting
preparation, and already-claimed meeting deliveries. Expired or terminal claims
and unclaimed ready-to-send work do not count as active execution.
It stops the launchd service before creating a database backup or changing the
production checkout. Every deployment takes a fresh SQLite snapshot of that
stopped current database under its operation ID; a complete daily or earlier
deployment file is not reused merely because it is less than 24 hours old.
Under the existing backup-directory flock, abandoned `.ceo-backup-stage-*.tmp`
files and sidecars are removed before a new candidate is allocated; unrelated
hidden temporary files and the verified backup are preserved. One candidate is copied,
converted to DELETE journal, integrity-checked, stamped complete, committed and
closed. Its completion marker is read back before atomic destination replacement.
Only afterward are older backups and abandoned candidates removed. Copy or
verification failure preserves the previous verified file byte-for-byte; failure
before checkout changes follows the existing service recovery path. A cleanup
failure after publication leaves the new verified file in place and reports the
error. Old and new copies coexist during validation; success retains only the
newest. No explicit fsync or sudden-power-loss guarantee is added.
After creating the stopped-service snapshot, the updater holds the same directory
lock through restart, health/loaded verification, finalization or failure rollback.
The daily backup runs in a separate component thread and waits for this lock, so
it cannot retire a recovery source before deployment has settled; API startup is
independent of that wait. The lock releases on both success and failure.
The hourly daily workflow still backs up once per day. If a deployment replaced
the daily file, its next tick can create a newer daily snapshot and retire the
pre-deployment one only after verification. Existing 24-hour pruning of database-
adjacent pre-deployment snapshots is unchanged. Backup retention is current-state
recovery, not a permanent archive of old schemas. It starts the service again
with launchd bootstrap after verification and performs normal health and queue
readback. See `docs/database-migration-closeout-validation.md` for copy proof.
After a successful launchd `bootout`, the updater waits until `launchctl print`
confirms that the service label is absent before checkout or a later bootstrap;
an inactive-but-still-removing label is not treated as stopped.

An operator may explicitly run `python -m app.deploy --skip-quiet-wait` when
immediate deployment is required. This bypasses only the in-flight-work wait;
the repository lock, normal service stop before database backup and checkout
changes, verification, restart, health check, and rollback behavior still
apply. Work interrupted by that stop is handled by the existing startup
recovery and claim logic.
An optional `--target-commit <full 40-character SHA>` pins a deploy to a commit
object in the current `origin/main` ancestry. The target must fast-forward the
production checkout. A normal advance of main while the deploy waits does not
change the pinned target; a main rewrite that drops it fails before the normal
service stop and backup. If new work starts during the post-wait fetch, the
deployer waits for quiet again within the original 30-minute deadline before
stopping the service. The updater merges the literal commit, so a later ref
change cannot expand an exact-SHA approval. Omitting the flag retains the
ordinary current-main deploy path: after the quiet wait it fetches once more and installs the newest
fast-forward `origin/main`, so commits pushed while claims were closed ride this deploy and the sessions
queued behind it find production already current and exit without draining again (a main rewrite that
drops the planned target fails before the stop). `--restart` does not accept a target SHA.
`--skip-quiet-wait` on `python -m app.deploy --restart` skips only the quiet wait
before a settings-only service restart.
If fast-forward fails before a replacement revision is installed, the updater
starts the original service again and checks its health. A settings-only
restart also bootstraps the configured launchd job if it was already unloaded.

The repository mutex covers the protected-source unlock window as well as
checkout, build, verification, restart and relock; settings-only restart uses
the same mutex. If an interrupted fast-forward leaves only unstaged docs whose
bytes exactly match the fetched target commit, formal deployment stops the
service and takes its backup before restoring those docs to the current HEAD;
the ordinary fast-forward then installs the target. Staged edits, other paths,
or any content mismatch remain a hard local-change refusal.

The Email worker, Audit web, scheduled Email discovery, and scheduled report
readers lazily create one shared EmailStore and check schema shape during
runtime initialization. Every EmailStore schema
initialization and the AutoReplyStore schema initializer acquire the same
per-database cross-process file lock before inspecting or changing schema.
They do not scan every historical email row while the service is starting or
serving scheduled Email and report work; production measurements showed those
scans taking 11–21 seconds per construction. Explicit and offline EmailStore
callers retain full durable-row validation by default, after releasing the
schema lock. Email detail reads still use fresh connections and do not cache
record contents.

### Public information and native reply recovery

The fixed Consumer/Audit rules now distinguish established public disclosure
scope from factual support. All-staff public OKRs and ordinary coordination
within their established audience do not require an additional personnel
privacy grant. A privacy refusal must identify concrete nonpublic information
and an audience mismatch. Public visibility still does not support invented
conclusions, dates, commitments, or nonpublic individual assessments. This
contract is included even when a persisted custom Audit Rules template exists.

Native DingTalk replies commit the existing dispatch claim before the provider
call. In Store19.9 the claim timestamp lives in its prepared Action’s
`postfix_dispatch_created_at` slot; the source-shaped claim API is unchanged. After an interrupted call, another invocation of the same prepared
delivery automatically performs read-only reconciliation instead of sending
again. A confirmed match requires exactly one unrecalled message with the
original conversation, quoted trigger, prepared body with the provider's
native mention of the verified trigger sender (rendered whitespace ignored),
and authenticated current sender. Its message ID and readback are
persisted as the normal outbound receipt. Empty, duplicate, mismatched, or
unavailable reads remain inconclusive; a bounded recent-message read cannot
prove absence and never releases the dispatch guard. This is recovery on
invocation, not a new background retry scheduler. A pre-send Audit rejection
does not create a dispatch or bypass authorization. Existing failed generations
without this new boundary still require the formal evidence-based recovery
path; their historical dispatch state is not fabricated.

In the unified Action declaration, the prepared provider result is read
through that Action's `prepared_receipt_id`. The pointer selects the exact
accepted prepared-delivery receipt, so another provider body attached to the
same Action for Sent history or canonical correction cannot shadow it by row
order.

### Runtime result failure evidence

Reply-attempt queue status computes counts, the latest timestamp and failure
detail from one narrow latest-trigger projection. It does not sort full
message/proposal bodies or repeat the window query for the timestamp. Recovery,
execution-generation and business-object filters remain part of that same
projection; physical historical failures are not current queue failures.

Unsubscribe forms execute through the exact native submitter selected by the
audited DOM model. Its selector is included in the control identity alongside
the form association, method, action and successful controls. The browser runs
the provider's submit handlers; the service does not reconstruct a GET or POST
from the form attributes. A Salesloft page returned HTTP 405 to a reconstructed
POST but confirmed successful opt-out after native submission. Completion still
requires the page's terminal readback, not merely a click or HTTP 2xx response.
An HTTP 204 from the exact bound form target and method is an explicit no-content
submission receipt. Native forms wait for browser navigation completion; async
submit handlers remain responsible for their visible terminal readback.
After a native form submission, the browser waits within its existing bounded
readback budget for the submitted control identity or visible text to change.
An unchanged form is an operation timeout, not evidence of "no reliable entry"
or a reason to manufacture a skipped receipt before an async handler completes.

Rejected provider responses retain their numeric HTTP status in the
sanitized failure detail when supplied by the executor. Private response URLs and
subscription tokens remain excluded. A rejected response is not a success
receipt and cannot terminalize the unsubscribe as done.

An empty unsubscribe email field uses the original message recipient, not the
connected mailbox login. The direct operation obtains recipients from the
classification's exact account and stable message identity; only one parsed
recipient can be prepared. Missing or ambiguous recipients leave the field
unavailable rather than substituting the primary mailbox. Authentication and
mailbox OTP continue to use the connected account. The prepared recipient is
part of the complete form control digest, so hidden controls, submitter, target,
intent and form association remain checked before native submission. Prefilled
email fields must also match the source recipient; a missing source or different
prefilled address is rejected without submission. User
instructions to retain a subscription must not be recorded as provider opt-out
success or executed through an unsubscribe request.

`cancel-failed-email-unsubscribe` records an explicit principal decision to retain
one subscription. It requires the original task id, action identity, execution
generation and input version. The default previews without writing a decision;
`--apply` uses the configured browser profile's nonblocking lock and an immediate
database transaction. A changed identity or current plan, active dispatcher,
Agent history, unsubscribe claim, effect, step or provider receipt prevents
cancellation and requires reconciliation first. An accepted decision is stored
separately in EmailStore's `email_unsubscribe_cancellations` (schema 50), preserving the original failure and all history,
and the original task becomes `skipped`; no provider receipt is created. Repeat
apply is idempotent. The direct operation rejects the cancelled action, checks
again inside the profile lock before opening a page, and the durable write-claim
entry also rejects it. Missing-provider-receipt repair does not synthesize a
receipt for these principal cancellations. This is not a blanket address-level
unsubscribe policy or authorization to alter other mail actions.

Cancellation preview uses a read-only SQLite connection without constructing
execution stores or migrating schema; a pre-50 database must be deployed first.
Direct browser execution and result persistence retain the same profile lease,
including reentrant adapter calls that cross the session TTL. Before the first
browser operation, a generation/input-digest-bound execution-pending record is
written in the reply-task store's runtime metadata. Only an actual terminal
receipt clears that exact record. A crash, unconfirmed result or failed receipt
write leaves the record requiring external reconciliation and prevents a false
subscription-retention closure. An explicit retry of a worker-finalized login
or no-entry skip still requires its exact predecessor receipt digest; provider
completion receipts do not permit replay.

Real Chrome unsubscribe fixtures verify the persisted effect, continuation,
step, claim and receipt fields separately: only the receipt's `entry_url` may
contain the private entry. Returned redacted evidence must not contain it.
Entry reads can load redirects and resources under the existing no-network-
whitelist policy; these GETs are not proof of zero external effects. A discovered
form still returns a continuation without submission or terminal completion
until its control is accepted. Incremental Audit fixtures retain the exact
GET/POST/POST progression and bind every proposal to its action identity.

Successful multi-family email training runs use the successful `embedding-mlp`
entry in `family_results` for description optimization. The compatibility
`model_id` field may point at a traditional classifier and does not imply
embedding staged evidence. Classic-only runs complete without description
optimization; missing or corrupt evidence for a successful embedding candidate
still fails closed rather than silently clearing the training run.
Each scheduler cycle performs independent model maintenance before provider
observation, so the initial completion poll does not wait for IMAP inventory
or message reads. Observation failures retain their separate health record.

When a Codex turn produces no typed result or an invalid typed result, the
runtime attempt is recorded with the result-stage failure code
(`codex_result_missing` or `codex_result_invalid`) rather than the generic
`runtime_unclassified` code. This keeps Task Agent retries and Attention
classification tied to the actual failure stage; genuinely unknown execution
exceptions remain fail-closed as `runtime_unclassified`.

Unsubscribe page judgment accepts a terminal parent only when that exact email
action has one of the direct executor's existing retryable skipped receipts.
This lets the authorized direct reread obtain new provider evidence without
rewriting a completed reply task to pending. A successful receipt, missing
receipt, or failed terminal parent does not authorize this runtime operation;
the action plan and browser effect authorization remain unchanged.

Status queue statistics rank compact Attempt IDs by trigger before reading
the current Attempt state and failure detail, keeping historical error bodies
out of the latest-row window. The reused current-Attempt CTE is materialized
once per statement. Attention computes its latest public Attempt IDs once per
message key, rather than rescanning all historical Attempts for each problem
candidate; cross-channel grouping and existing result ordering are unchanged.
Email provider-action aggregation scans actions before seeking their exact
classification primary keys, retaining the same processed/current-plan filter.

Audit web startup does not scan and cache History lists or charts. History
pages are computed when requested. The list counts and sorts compact source
keys, then hydrates only the requested page in the same read snapshot; it does
not materialize every historical display body to compute the total. Its union
is an inline subquery so SQLite does not repeatedly expand a named CTE. No
page-result cache or background page refresh can reuse another page size or
hide a just-committed record. The reply-attempt History branch computes each task's latest associated public Attempt ID once per query and applies the separate current-generation exclusion; source-specific list, count, chart, and page hydration use that same branch. Attempt detail loads tool events in one
batched read for the historical generation shown on the page. If the task has
since moved to a newer execution generation, its runs are read without event
payloads because only their status is needed for the current projection. The
historical run rows and native transcript references are captured in SQLite
first; native files are read and parsed only after that connection closes. The
session path index is cached per file signature. If an indexed file was cleaned
up, lookup checks its flat `archived_sessions` counterpart; if that is missing,
the Attempt remains readable and reports native evidence unavailable without
recursively walking the historical session trees. This does not alter persisted
business results or audit evidence. The
History chart projects lifecycle status only for reply
attempts in the requested time window, avoiding a full-history status scan.
For failed Attempts, the History status projection also follows the owning
AgentRun's stable reply-task ID when the OA scanner has changed the task's
trigger key. Status filters and counts use that projected state; stored Attempt
and execution records are unchanged. A current candidate execution with an
unresolved uncertain receipt remains `uncertain`, even if the task closed;
independently resolved Attempts retain their resolution projection.
The later-terminal Attempt predicate uses a grouped maximum public ID per
channel, conversation and trigger through the status projection; key-only
pagination does not evaluate that lookup. Each chart uses one read snapshot and reads a failed
message's current task and WeChat delivery once within that request. Chart
counts, lifecycle labels, recovered meeting handling and time buckets remain
unchanged. Performance evidence is in `docs/console-query-performance.md`.
Health readiness is not evidence that History requests or business actions
have completed; their APIs and provider receipts must still be read back.

The failed History filter first selects source rows that can still project to
failed under the existing status projection. Reply Attempt candidate keys use
the same current-generation task, OA, delivery and uncertainty status as the
unfiltered History list, while other source branches retain their selection.
The total and page order come from compact source keys; the selected Reply
Attempt IDs are hydrated through that same projection, with display fields
loaded only for the requested page. History reads do not replay work or change
stored status.

DingTalk meeting group searches explicitly request 100 candidates per page,
the provider's supported maximum, while retaining the complete-source check.
The CLI's default 20-item pages exhausted its 50-page budget for large
participant queries such as Melody; retrying the same bounded query could
never reach the remaining candidates. A partial response still fails closed;
larger pages do not change recipient ranking or authorize a send.

Worker status reads its local SQLite queue, Email health and component facts
on every request. These facts are not served from the last background payload:
after a worker writes its state, the next status request must reflect it.
External connector authentication probes retain their independent cache.
The Email unsubscribe task view uses a partial `reply_tasks` index over only
audited unsubscribe tasks, so repeated Worker status reads do not rescan each
task's full trigger JSON to compute its counts and latest update.

Typed result parsing preserves malformed or unclosed JSON as a result-stage
invalid-result failure, including its syntax cause. It is not classified as a
missing result. An earlier valid result in the same primary turn remains usable;
no JSON repair or successful external-effect inference is performed.

Manual reruns of scheduled Agent work preserve the saved scheduled execution
context, including its route and pinned Skill content. A missing or invalid
scheduled payload is rejected instead of being rebuilt as a DingTalk message;
the original scheduled run is the authoritative source for explicit recovery.

The approved DingTalk send tool persists its exact prepared body and verified
provider receipt into `sent_replies` as soon as provider verification reports
`sent`, before returning to the Audit turn. A later invalid Audit result cannot
erase this delivery evidence. Pending or ambiguous verification produces no
successful History projection. This records one message's actual effect, not
completion of the whole proposal: the task and external-action completion
ledger still require the existing Audit lifecycle and evidence checks.
# External Command Failure Evidence

External command failure previews retain both the beginning and the end of
long output within the existing 400-character content budget. Python traceback
frames must not displace the final exception code and cause. Structured provider
errors still expose only the existing approved error fields; command argument
redaction, retry policy and action authorization are unchanged. This improves
future failure evidence and does not rewrite already-truncated historical runs.

### Sensitive Result Classification

The post-process sensitive-result guard raises the same result-parse exception
as other invalid typed output. Rejection is recorded as `codex_result_invalid`
at the result stage, allowing the existing Audit result-correction prompt and
bounded attempt handling to operate. It is not a CLI execution outage. The
credential rejection itself remains unchanged; no unsafe result is accepted and
no content-feedback cycle is consumed by a parse failure.

Generic credential key/value text requires the value to end at the string boundary
or an explicit comma/semicolon delimiter. This avoids treating ordinary diagnostic
prose such as `access token: load access token: acquiring file lock` as a secret,
while retaining detection for delimited values such as `api_key=...;`. Known
credential formats, sensitive-field context, entropy checks and outbound
credential validation are unchanged, except that Basic-auth text is recognized
only when the following base64 value decodes to the required `username:password`
form. This detects credentials in `Authorization: Basic <base64>` headers and
bare Basic-auth fragments while ordinary prose such as `Basic information`
does not trigger result rejection or redaction.

Process diagnostic redaction preserves each original line boundary before joining
separate error fields, while retaining the same field-level whitespace-spanning
credential patterns. Unsubscribe result normalization retains the existing full-
text redaction and applies the same detector to original normalized lines before
rejoining them. It preserves final newlines, URL/path redaction, the complete
redacted-text digest and UTF-8 byte bound. Credential patterns and policies do
not change; concatenation must not erase a credential's original end boundary.

### Daily Report Source Identity

Daily report `handled_today` facts retain their original conversation ID,
trigger message ID, creation timestamp and Agent run ID alongside the display
title and summary. Reports from previous scheduled runs can share those display
fields; their completed status is evidence for that historical source only,
not the current report's publication or notification. The collector remains
read-only and does not infer or modify delivery state.

### DingTalk Message Text

The current DingTalk `im.message-list.v1` ledger permits a null `text` value.
The message adapter projects that absence as an empty string, preserving the
original message ID, conversation, sender and complete raw payload. It neither
discards the message nor invents text or a delivery receipt. Nonstring values
other than null still fail model validation. Meeting group discovery can read
the remaining discussion evidence without treating absent text as evidence.

# Email SQLite Contention Diagnostics

EmailStore and AutoReplyStore report connection contexts lasting at least one second, including
elapsed time and the caller's file, line, and function. This duration includes
lock acquisition, the body, commit, and close; it is not by itself proof of the
write-lock holder or transaction duration. Logs exclude SQL parameters and mail
content. Correlate the caller with the operating system's WAL-lock owner and
the affected run before changing transaction boundaries.
Shared Store diagnostics retain up to eight caller frames so a context-manager
wrapper cannot hide the business method that opened the connection.
Slow AutoReplyStore write transactions additionally report time waiting in
`BEGIN IMMEDIATE` separately from time holding the acquired transaction.
Slow direct email-action claims report their read preflight, writer-lock wait,
locked recheck and claim mutation separately. These measurements identify the
slow phase; they do not change retry, claim or transaction behavior.

Explicit and offline EmailStore callers perform full durable-row validation
for email-message metadata, provider locators, recipients, references,
attachment metadata, classifier-task identity and redaction, classification
lineage and ActionPlan snapshots. The validator projects only the fields
required for those checks; it does not load full cached message bodies or
classifier inputs into memory. Runtime Email worker, Audit web, scheduled Email
discovery and daily-report readers validate schema and account configuration
without rescanning all durable rows.

The Web process shares one successfully schema-initialized EmailStore between
registered email routes, Console detail routes and the email learning service.
Email route registration retains its existing initialization error boundary:
an invalid schema or account configuration does not prevent unrelated Console
routes from starting and is not treated as healthy email state. The Attempt DTO
builder queries this initialized store only for
an existing email-channel Attempt's classification and unsubscribe context.
Every request reads current records through fresh database connections; detail
results are not cached and requests do not repeat the full durable-state scan.

Direct provider-action claims hold BEGIN IMMEDIATE only over classifications
with pending or failed actions, rather than materializing every processed
classification's settled history on each poll. For each selected classification
all sibling plan versions remain visible: an older processing action still
blocks the current plan, dependencies and priority remain unchanged, and the
claim update remains atomic. Fully settled groups cannot yield a claim and are
excluded before rows are materialized. This requires no migration or replay.

A direct-action claim compares the Action owner's own stored `started_at`, read
inside the claim transaction, not the view's projected value. The view reports
a pending Action's `started_at` as NULL, but Actions written before
2026-10-22 11:33 UTC store an empty string. Comparing against the projection
made each claim of such a row raise `EmailActionAttemptConflict`; because the
oldest eligible Action is always claimed first, one such row at the head blocked
every later Email action from 2026-10-22 23:59 UTC (615 queued by 10-23). The
status and attempt-count checks are unchanged, and no stored value is rewritten.

Legacy unsubscribe terminalization uses the same lifecycle selector as its
inventory: only `email_unsubscribe_consumer_direct_v1`. An inventoried object
replaced by an `email_unsubscribe_audited_v2` task must remain untouched, even
when its task ID, generation, and pending status otherwise match. The mutation
checks the lifecycle again under its write transaction.
# Task Evidence Repair And Retired Anchors

Meeting claims order attempts by the later of `eligible_at` and `available_at`,
with the job ID as the tie-breaker. A due retry is not ordered by the meeting's
original date: otherwise an old external dependency failure can repeatedly
occupy the single meeting consumer and starve pending meetings. The dispatcher
keyset scan and legacy claim path use the same ordering; eligibility, lease
ownership, and external delivery guards are unchanged.

Meeting delivery batches release still-ready claims on both normal completion
and exceptional exit. Each acquisition persists a fresh token in the separate
`meeting_alignment_delivery_claims` Action kind, in the same transaction as the job
lock. The token is returned only with the claimed job, not stored in the job
row. Release compares job ID, original lock timestamp and nonempty token, so
even a same-second successor cannot be unlocked by a stale batch. The job row
shape stays unchanged for strict parsing during code rollback. Startup recovery
releases interrupted locks and clears abandoned ownership records before fresh
acquisition. Cleanup never changes receipts,
retry deadlines, attempt counts or terminal status. If delivery and release both
fail, a grouped exception records both causes instead of hiding the first.
Persisted `sent` receipts remain authoritative: recovery resumes only calendar
terminalization, not analysis or chat sending. Transcript-only meetings retain
the existing explicit calendar-skip receipt because no original event exists.

Meeting discovery may refresh a waiting recording, but once a job is queued,
claimed, retrying, ready for delivery or terminal, discovery cannot replace its
persisted source snapshot or participant evidence. Recovery refreshes failed
unsent jobs through the explicit replay lifecycle only when no standard or
sensitive DingTalk outbound is already prepared. A prepared outbound remains
owned by its existing delivery recovery path and cannot be cleared by scheduled
replay. A failed roster lookup during rediscovery must not remove
`calendar_evidence` from an already queued job.
Recent-meeting replay uses the same ended-recording metadata contract as
discovery, without requiring the provider status to equal the literal `ended`.

Scheduled service incident reconciliation uses the numeric scheduled-task ID to
seek the indexed run history, then verifies the exact conversation identity,
successful command kind and later dispatch time. It must not scan all dispatched
runs once per unresolved error while holding a SQLite writer transaction.

Task Agent validates formal creation and candidate promotion owner citations
before entering its atomic domain transaction. A repairable evidence error is
returned with the rejected candidate and original context, for at most two
correction rounds. Task also supplies a compact correction bound to the native
session that produced the previous candidate. The runtime selects it only when
the actual route resumes that exact session; cold starts and other sessions
receive the complete source, semantic context and correction. Existing route
selection and format-correction policies are unchanged. Friday creates a new
thread on every invocation, so it always receives full repair context even when
a prior thread reference is stored. No domain changes are
committed before validation succeeds. Exhaustion retains a real failed run and
uses the existing work-summary retry policy; it never guesses an owner or
changes a technical failure into `needs_human`. Memory-backed ownership still
requires linked source provenance and an `episode_id`.

Current Work Item Project citations, including nested context and assessment
evidence, use the same exact source identity and contiguous-quote validator
before domain writes and again during atomic apply. Invalid current citations
enter the existing bounded correction rounds with the rejected candidate.
One rejection reports all distinct invalid current citation entries, retaining
each validator reason, source_ref and exact rejected excerpt; repeated entries
are reported once. This does not rewrite or normalize quotes, and the same
two-round budget applies to the entire decision, not separately per citation;
exhaustion remains failed without partial Project or signal writes. Historical
signal citations retain their existing provenance checks. The same read-only
stored Project/assessment validator also runs through a short connection before
domain apply, within these existing correction rounds. Its ValueError reports
the original evidence/identity/membership error to the model; this includes an
existing Attention declaration that omits the card's stored original proof.
Atomic apply runs the unchanged validator again. Database operational errors
and errors raised during atomic apply are not converted to model correction.
No new validation rule, correction budget or recovery loop is introduced.

Date source, actor and exact-precision validation uses the same pure validator
before the domain transaction and during apply. Evidence errors enter the same
bounded correction loop; unidentified source actors or invented dates remain
invalid. Each semantic correction has a deterministic runtime operation key
`<task_agent_run_id>:decision_repair.<round>`; the initial turn keeps its run ID.
The router reuses a completed receipt only within that operation, so a changed
correction prompt actually executes a new turn in the shared Task session.
Corrections retain the original active Task run as their parent, not a Project;
input reset, orphan recovery and expired-terminal recovery close their no-effect
runtime attempts just as they close the initial turn. Existing memory and
deadline backfill parent identities are unchanged.

Both Cron-dispatched Work Summary consumption and the manual
`process-work-items` command use the same renewable Task session lease. A
claimed item whose session is busy is returned to the retry queue before any
Agent turn starts. Completion, exceptions and repair rounds remain inside that
lease; serial workers inside one dispatcher do not substitute for cross-process
session ownership.

An inactive `task-agent-project` anchor is a retained retirement decision.
Weekly-report registry rows and Agent Project proposals must not reactivate it
implicitly. The source-backed Task is saved independently, without a Project
link to that retired anchor, and the run records why the Project was not applied.
The general anchor registration conflict checks remain unchanged.

### 已审核来源、执行进度与失败展示

服务捕获的 source_bindings.value 是原始来源数据，不是 Agent 自己写的短引用字段。
日历卡片等来源可能把完整 JSON 放在 reference 中，不套用候选 reference 的
512 字符上限；来源仍接受严格反馈链接、凭据、嵌套深度与整个结果编码大小检查。
绑定元数据与候选字段仍执行原短字段限制。检查仅使用投影副本，不截断或修改
原来源、candidate digest 或派发前同对象回读比较。

Consumer 完成候选准备时，把原 trigger、消息/材料和计划涉及的 OA 原表单或文档源事实保存为 source_bindings，参与 candidate digest。System Executor 在每个尚未核验动作派发之前重新读取相同原对象，包括首次执行、同次后续动作和恢复部分执行。OA 已完成动作造成的 task 状态/操作记录变化由具名处理器核验，不作为原表单变化；真实表单、文档或上下文变化使该 review 失效，保留已核验动作回执和原人工回答，在同阶段形成新的完整候选并重审。来源读取失败属于可重试技术失败，不执行旧分支。

来源快照中的反馈链接在检查副本中按 Markdown 链接和真实 JSON 容器读取；不要求其他参与者使用本人的外发签名、标签或段落格式。仍严格核对当前配置 host/path/query、生成 token、上下链接同一身份与唯一完整配对，额外未配对 URL 或凭据仍拒绝。只移除已核验 URL 的检查副本，不把来源链接当授权或外部动作回执。当前待发送正文仍要求原始严格格式；原 source_bindings、候选 digest、持久化结果与发送前来源回读比较不变，其他来源字段继续接受原有敏感值、深度和大小检查。确定性的来源结果验证失败记录 runtime_result_source_invalid、stage=result、source=service、retryable=false、session_continuable=false；不归为 CLI/provider 失败并反复恢复同一会话。

Attempt 详情的 system_execution 按 candidate、review、selection 绑定显示当前及历史阶段、声明顺序、未开始动作、失败/不确定状态和 provider 回执。只有 canonical action ledger 中核验成功的动作计为 verified；Audit approve 和用户选择都不能显示为已经执行。

History 的“外部动作”摘要按 receipt 的 `first_agent_run_id` 归属：属于当前 Attempt 所含运行的 verified receipt 才算本轮新产生的效果；本轮继续处理时复用的较早 receipt 单独显示为已复用，不冒充本轮操作或本轮 acting run。时间从 provider 结构化操作记录读取，以 UTC 毫秒精度呈现；Attempt 更新时刻不能替代原始操作时间。此规则只修正只读展示，保留不可变 receipt、原 done 状态和现有幂等复用语义。

失败 Reply task 的 Attention 诊断只读取本对象本 generation 的当前 run，显示原 source_code（无则 code）与明确标为“Agent 说明”的 reported_summary；缺少或坏 JSON 时保留 task error，不将 Agent 自述当作已证实的 provider 拒绝。History、Attempt 详情和 Reply attempts queue 对微信分别读取本对象当前 generation 最新 delivery：failed/send_unknown 不能被只表示候选完成的 task done 遮蔽。旧 generation 或其他对象的 delivery 不影响当前结果；这些展示不更新任务、投递或授权状态。

独立 Email 退订仍使用原有 direct executor、浏览器步骤和回执。无运行时调用者的旧 Agent continuation driver 已删除；Agent finalizer 不再从历史 Audit 声明触发退订、切换渠道或伪造执行成功。默认 developer prompt 和 OA 规则种子统一为新角色合同；既有自定义规则、managed Skill 配置与 runtime-only Skill 必须在安静的正式发布窗口中逐项发布并记录版本，部署代码本身不会覆盖它们。

原生 OA detail 明确 success=false 时，来源读取边界保留 errcode/errorCode 及 errmsg/errorMessage，按提供方错误记录原 server/code/认证属性；不把 native 失败 envelope 当成空表单或通用 Codex 错误。Consumer 失败 run 保留原始脱敏说明，System 执行技术失败保留来源和原码，不派发或制造业务人工问题。

Consumer/Audit stage clarification (2026-10-04): `continue_after_execution` means the next stage can run immediately from the verified result. A material-request comment or return ends its request stage with the flag false; only a later source update establishes material arrival and permits a fresh complete candidate. The request receipt cannot replace the material. Audit runner and the frozen model harness share `audit_developer_instructions`, including registered action contracts and exact provider-identity evidence; a project/request ID is not an OA process identifier, and a decision notification does not perform a payment.

角色 MCP 启动参数携带原领取 run 的 execution_generation，启动时与当前任务校验；旧回合不能在 generation 更换后绑定到新一代工件，运行中的既有读写仍逐次检查当前 generation。

真实、未解除的 provider-risk refusal 必须保留 failed 与原始错误码，不能因不重试改为 no_action 完成；Audit 对错误完成候选退回修正。历史同一动作拒绝不允许换键或渠道重放。保留既有历史拒绝与恢复限制，不用关键词推断效果。

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

### 调度异常的重试上限（2026-10-05）

Derek 要求自动重试统一最多三次 retry（首次调用之外），即最多四次调用。ReplyQueueAdapter 的 handler 异常计入既有任务 attempts，
不再退款或立即重新领取；前三次按共享指数退避延后 60/120/240 秒，第四次原任务记为 failed，
保留原错误、业务对象、执行代和运行历史。ScheduledExecutionQueueAdapter 继承同一规则。
共享上限由 `app.external_retry.MAX_AUTOMATIC_RETRIES` 定义；DWS 瞬时调用、同路由容量重试、
浏览器瞬时失败及其他自动重试默认读取这一上限，配置值不得放大它。
普通主动 release 仍是未执行业务的调度释放，不消费异常预算。Consumer 与 Audit 的已持久化
技术失败分别使用独立的每角色、同执行代、同修订四次尝试预算（每个角色最多三次 retry），调用前检查；
底层 code/source_code 保留，预算耗尽不伪造 needs_human 或业务完成。

DWS 网关自己的 5xx 失败（stderr 里 `error.code=1`、`error.reason=http_<5xx>`）以 `http_<5xx>` 作为错误码，
对消息读取命令按上述共享上限自动重试，用尽后仍标记为可重试的外部依赖故障、不进入 Attention；
消息发送命令没有幂等键时不重试。

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

## Runtime Context 与 Settings 渲染输入

后台 Consumer/Audit 在实际 route 和角色工具配置确定后追加动态 Runtime Context；fallback
使用新 route 的模型、thinking、native cwd 和声明目录重新生成。目录来自最终 CLI allowlist 与
role server 描述，仅证明本轮声明的入口，不证明连接/认证/读取成功。资料根目录与任务工件目录
分开显示；Claude 的命令继承进程 cwd，Codex 优先显示明确 `--cd`，未知续会话目录如实标注。
任务、执行代、stage/revision 和后台 scheduled_consumer 绑定来自原任务与真实 invocation，
不按消息 channel、人员或关键词推断。稳定取证原则加入角色指令及契约 hash，动态时间不参与
会话兼容 hash。普通 Consumer 工作、Audit 只读及 SystemExecutor 受控动作边界保持不变。

日历任务先核实相关参与者时区与来源，按会议日期处理夏令时、跨日和不存在/重复的当地时间；
已知工作时段、占用和偏好用于选候选。对方时区未知时保留待确认，先交付本人侧带明确时区的
暂定窗口，不称双方均合适；已知时区也不证明对方空闲。结构化来源字段可投影到环境说明，
文本事实由 Agent 按原材料理解，不把机器时区或钉钉时间戳解析规则当成参与者时区。

每次 invocation 在现有事件流保存 `runtime.prompt`（准备输入）及适配器正常返回后的
`runtime.prompt.invoked`（适配器调用记录）。前者可能出现在启动失败前，后者也不代表模型已
接收或任务成功；两者不是工具进展、外部动作或 provider receipt。记录完整服务 developer/task
输入、实际 route、run attempt、执行代、修订与来源上下文，并复用已有凭据脱敏。

Settings 的 Prompts/运行输入 使用 `/api/console/settings/prompt-preview` 查看上述输入。
当前预览标注「所选路线的配置预览」，使用当前角色规则和已保存的完整任务输入；无完整上下文
的绑定任务显示不可用，不通过只保留原触发的重建冒充完整。历史预览只读既有事件，标明准备/
调用状态、run/attempt/generation/revision/stage/时间；旧 run 缺输入时不重跑、不写回、不补造。
页面范围限定为服务提交输入，CLI 自建系统/schema/history 不伪造为可见内容。预览不改变配置、
队列或动作事实，不读取外部日历和业务资料；历史指定的 role/task/route 不匹配时明确返回 422。

历史输入可按 `runtime_attempt_id` 选择同一 run 的每次路线尝试，逐次显示 prepared/invoked，
后来的准备失败不会隐藏早先已调用的输入。当前预览重新渲染当前默认 Skill 目录，仅复用明确
标记的 task override；任务正文仍标记保存来源。时区事实只投影 participant_id/timezone/
source_ref/applies_on 的字符串字段，其他来源字段不进入环境段；快照复用结构化凭据脱敏。

Prompts 的 Developer/User/Profile 各自保留 Template 与 Rendered preview 的一一对应；预览只渲染
同一份已保存模板，未保存草稿不进入预览。保存成功后重新读取服务器模板及渲染结果，避免继续
显示旧预览。后台 Consumer/Audit 的多段完整输入放在独立只读「运行输入」页签（prompt=runtime），
不再占用 Developer/User 模板的 Rendered preview；该页没有 Template 或保存操作。

Prompts 内容标题随当前查看方式显示「模板」或「渲染结果」，与所选 Developer/User/Profile 页签对应；完整运行输入仍位于独立只读「运行输入」页签。

Developer Prompt 只承载适用于所有任务的角色、能力、证据、系统动作、对应路线所需的输出契约及共同工作原则；工作人格仍注入 Consumer 与 Audit，Audit Rules 只进入 Audit。Consumer 使用 Codex 或 Claude 原生输出 Schema 时，服务输入省去重复的完整 Pydantic wire schema 文本；没有原生 schema 的 Friday 路线仍保留该文本，Audit 输入不变。所有 Consumer 路线继续共用不得直接发送对外消息、邮件或执行审批的角色约束；本精简不改变该约束、严格 Pydantic 业务校验、原生 schema、Audit 或 System 执行流程。OA、日历、消息受众、OKR、招聘和文档等专项流程由当前任务选择的 Skill 承载，不再作为全任务 Developer 规则重复注入。User Prompt 是 Consumer 的完整任务模板，必须包含恰好一个 `{{task_context}}`，仅支持普通文本和这个插槽。任务来源、定时要求、stage、反馈、既有回执及 continuation 先由服务组装成完整上下文，再填入插槽；不将旧消息块模板叠加到后台任务。Workbench、独立 WeChat、纯服务命令及 Email 退订保留各自入口和显式指令。

Consumer 和 Audit 每轮 Task 输入均传入当前任务已保存的专项指令，来源为「已保存任务配置」，现有运行输入快照记录该段来源和长度。Audit 不只从候选转述推断任务规则。`task_specific_instructions` 复用 Consumer 已有的精确 Skill 附录分离逻辑；只分离已知的完整末尾附录，不截断正文或按业务关键词改写。生成发现提示不重复预载，自定义 Skill 约定仍在 Skill 段保留。没有专项指令的任务不添加空段；审核权限、规则优先级、反馈和 session 行为不变。

服务组装的 Consumer Developer Prompt 不读取本机 `AGENTS.md`。Audit 启动时由服务仅从固定路径读取共享 `~/.agents/AGENTS.md` 和本项目根目录 `AGENTS.md`，作为 Developer 指令段注入；缺任一文件或读取失败时停止该次 Audit，避免依赖 Agent 通过受限的 `read_text_file` 访问项目指令。该读取由服务执行，不扩大 Agent 文件工具的授权根，也不接受任务提供的文件路径。Consumer 的角色、能力、输出契约和后台 Memory bootstrap 仍由原有代码提供；本机 CLI 的全局指令及 Agent 文件读取能力不变。

Consumer 的代码生成「Capability Instructions」只说明当前角色的工具使用细节：任务工件／文档写入入口、原生工作区、钉钉直连只读工具、依赖读取失败与风险拒绝的结果码。通用的按需取证、普通工作与受控动作边界、回执及失败原则已在同一 Developer 输入的已保存模板和角色合同中提供，不在能力段复述。能力目录仍提供精确 `read_system_action_contract` 入口；完整结果校验、Audit 规则、Task 事实和现有 session 绑定不变。Settings 当前预览与执行使用相同装配段，历史预览读取保存的原输入。

后台 Consumer/Audit 使用原生 skills.config 禁用专项 Skill 的自动正文注入，避免原生加载、Developer inline 与工具读取三份重复。原生 `project_doc_max_bytes=0` 关闭 CLI 自动加载项目目录 `AGENTS.md`；共享及项目指令只由 Audit 的服务 Developer 段按固定路径预载，Consumer 不接收这两段。全局 `~/.codex/AGENTS.md` 仍由 CLI 加载，服务不修改原生 home 或全局规则文件。此设置仅适用于后台角色命令。

角色命令只在最终角色构建器生成一次原生 Skill 配置。AgentTurnProcess 不再让适配器先按任务 Skill 生成随后被角色构建器丢弃的配置，避免重复扫描 Skill 目录和序列化。任务选择仍用于每轮 Task 的 Skill 入口及运行来源回执；最终原生配置、工具权限、会话和路由不变。独立直接使用 CodexRuntimeAdapter 的调用仍可传入自己的 task Skill 选择。

Codex Consumer/Audit 对 `read_task_skill` 与 `read_skill` 均使用原生单工具 `output_token_limit=30000`，让冻结与已安装 Skill 的正文在 MCP 完整返回后完整进入模型历史。`read_task_skill` 的 MCP 回执只提交一份含 name、完整 content 和 sha256 的 JSON 文本，不再同时附上内容相同的 structuredContent；冻结来源、角色权限和业务结果不变。其他工具输出预算、角色、sandbox、审批方式和 session 绑定保持现有行为。该设置保障来源交付，不替代 Skill 精简或证明业务判断正确；验证及发布记录见 `docs/task-skill-native-output-validation.md`。

System Action Contracts 在 Developer 中提供由 docs/system-action-contracts.md 自动生成的 capability/operation 目录；目录以 provider-neutral 的 `read_system_action_contract` 工具名指向 Runtime Context 中声明的原生入口（Codex 为 `mcp__agent_cli__read_system_action_contract`，Claude 为 `agent_cli.read_system_action_contract`）。该工具保留动作表外的全部原规范正文，只返回选定能力／操作的动作表行；只给 capability 时返回该能力的全部操作。原有角色、identity、payload、target、回执和完成条件保持完整，不返回其他操作的表行。Consumer 提出及 Audit 审查相应操作前必须读取规范；契约指纹仍包含全文。结果 schema 在 prompt 中只去除 title/default 注释，保留字段名、描述、枚举、约束及原有 Pydantic 校验。Codex Consumer 使用当前 Consumer wire model 生成的 general/OA 原生 Schema；Codex 本地 OAuth Audit 使用当前 Audit wire model 生成的原生 Schema。未验证 schema 兼容性的服务 API Audit 路线保留无原生 Schema 的既有解析路径。动态 JSON 对象以键值条目传输，并在严格 Pydantic 校验前无损还原；Audit 的 outcome/feedback 条件与 Consumer wire 分支约束保持不变，revision、digest 和候选字段断言仍由当前 Audit 上下文及既有校验负责。候选字段断言路径不存在时，修正反馈会指出准确路径和该父对象可用的字段名或数组索引，不回显候选字段值；校验和审核标准不变。Claude、Friday 及其他运行时路径不变。配置路线验证状态见 `docs/native-consumer-output-validation.md`。

Audit 传输结果的 `feedback` 在 `approve` 或 `failed` 时可省略，解析为 `None`；在 `return` 或 `reject` 时仍必须提供完整反馈。批准结果若携带非空反馈仍拒绝。服务绑定的候选、审查业务模型、结果校验、审核决策和外部动作权限不变。这个字段语义对齐针对两次完整轨迹中唯一的格式错误，验证见 `docs/audit-null-feedback-validation.md`。

Worker 上下文回归分别验证当前业务事实、执行阶段和会话恢复，不要求把 Developer 的角色合同复制进用户上下文。Consumer 的真实命令捕获回归另行检查 Developer 中仍包含提案／审核／系统执行边界，且用户输入不重复该角色合同；精简上下文不代表移除安全合同。

Task Agent 的当前 Skill 合同回归核对所选根目录的准确文件与完整内容，不固定旧版本号。定时输入仍验证未采用历史冻结正文、保留运行身份且未修改原输入，当前 Project／Task 输出边界断言继续保留。

OA 评论出现未知效果后，System 只读原实例的正式 detail；摘要 records 只含操作结果，不能证明评论正文。恢复要求持久动作键、uncertain 状态及准确带时区的首次动作时间，并匹配 detail 中唯一的 ADD_REMARK、当前本人 ID、完整获批正文和动作开始后的毫秒时间。旧评论、其他作者、歧义或缺失证据继续保留 uncertain，绝不重新评论。回执保存原实例、原生操作类型／作者／时间与正文摘要，不编造 commentId、不复制整份审批详情；该读回不授权新的审批决定。


运行输入的分段来源由同一装配函数提供：每段记录 name、source、placement（Developer/Task）及脱敏正文 characters，长度不含段间分隔符。runtime.prompt 是运行期 RAM 中的准备/调用回执；终态不另存正文副本。Settings 公共预览使用当前角色装配，任务绑定预览区分当前 Developer 与该角色实际保存的 Task。历史预览读取选定 runtime attempt 的原生输入范围：Codex 只取带 generic.developer_instructions 与 user.text 标记的服务消息，排除 CLI 基础指令、AGENTS、环境、工具和历史；续会话明确标记 session 先前保存的 Developer，不冒充本轮配置提交回执。Claude 读取本轮原生服务合并消息的既有 Developer/Task 封装。没有细分来源、配置指纹或渲染时间时保持未记录，不用当前配置补齐。尝试列表保留不可读来源的准确 ID/路线；最新或选定来源缺失时明确不可用，不改显示较早输入。读取不重跑任务、修改数据库或保存原文副本。日历时区解释流程位于 ceo-calendar-invite Skill，Runtime Context 仅保留显式参与者时区事实。

未精简任务的当前配置预览从原任务结构化输入识别冻结 Skill，使用任务内读取入口并标明冻结/当前目录来源；冻结材料不需要在当前安装目录存在。已结算回复输入的冻结正文精简后明确不可用。历史模式只展示选定原生 invocation 保存的服务 Task 正文，不追加任何新入口，也不由当前 Skill 或精简来源记录重建正文。

一次角色 invocation 只读取一次 Developer/User/Profile 正文，供组装、静态指纹与重试共用；Audit 不读取无关 User 模板。实际路线和工具命令确定后，再追加 Runtime Context。静态配置 SHA 写入运行期 runtime.prompt invocation facts，仅作来源回执；原生历史未保存这些元数据时不重建 SHA；Consumer 继续按 `conversation_id + route` 复用原 session，配置或 contract hash 变化不创建新会话，Audit 的独立 session 不与 Consumer 合并。

默认 User 的 Rendered preview 使用明确标记的合成完整任务，不读取业务材料；Developer/Profile 渲染同一份已保存正文。完整运行输入仍在独立只读页签，当前配置 Developer 与所选已保存历史 Task 的来源分别标注，历史模式不重新渲染。读取设置/预览不创建或覆盖配置文件。已存在模板不在读取时自动升级；部署使用 `python -m app.deploy --publish-prompt-templates`，仅在既有停止/备份窗口根据 `ci/prompt-template-release.json` 的精确旧/新 SHA 发布默认 Developer/User。本次默认 Developer 发布只接受上一版精确默认 SHA（3ccd6261291b6610aa95a4764d4fd81b7c232011f8ca7dbc52b18639703fb457），移除全局日历专项段；原规则逐字保留在实际 authored calendar Skill，User 默认和工作人格不变。自定义模板须明确迁移，不能覆盖；文件备份、发布与回退沿用 RepositoryUpdater 的 publication 协议。该发布不修改工作人格。

仓库受跟踪的 `data/prompts/developer_prompt.md` 是通用 Developer 模板，继续保留 Git 版本管理，
与已批准的 `app/defaults/developer_prompt.md` 保持逐字节一致。发布后的生产文件与回执一致
不等于工作树已干净；源码同步不放宽部署器的本地修改检查，也不允许手工重置生产模板。

Prompts 设置读取先返回 Developer/User 的已保存原文；某份模板验证或渲染失败时，仅该份渲染预览为空并返回明确的 `preview_errors`，编辑器仍显示原文供修正。保存仍需通过现有验证，后台角色调用仍严格验证，不自动迁移或覆盖旧模板。

精简只移除重复或已由任务 Skill 承载的流程说明，不改变角色边界、身份与证据规则、完整效果判断、业务结果模型或 Pydantic 校验。Audit 的候选、source_bindings、revision 和 digest 保持完整；只有 provider、object_ref 与来源 value 按排序 JSON 完整相符（保留布尔/数值等 JSON 类型区别）时，Task 中重复的触发正文、raw payload、历史消息正文和材料 reference 指向候选的来源绑定，同一输入内仍能读到完整值。来源不同时两份全文保留。未结算的定时服务命令在 `scheduled_consumer.skill_materials` 冻结完整 managed revision 或 operation snapshot；未结算的通用 Scheduled Agent 在 `scheduled_agent_execution.v1` 顶层保存有序 `skill_names`、`skill_materials` 及其来源，原始 trigger 投影只保留结构化 Skill 身份事实，不再次复制正文。冷启动和续接都从同一未结算持久输入恢复；已结算输入改为来源摘要，冻结正文读取明确不可用。每一轮 Consumer 与 Audit Task 只列当前选中 Skill 的名称、极简用途和按需读取入口。保存输入声明该名称为冻结 Skill 时使用 `agent_cli.read_task_skill(name)`；所选冻结 Skill 的 revision_id/sha256 未变且完整正文仍在同一 session 上下文时，发现提示允许复用已读的确切内容；新会话、来源变化或全文不在上下文时仍准确读取，不新增缓存或本轮读取回执。材料重复、损坏或缺失由精确读取报告技术错误，不得改读当前磁盘版本。没有冻结声明的已选 Skill 使用目录中的实际授权绝对路径调用 `agent_cli.read_skill(path=...)`；Skill 正文引用的其他已安装 Skill 通过 `agent_cli.read_skill(name=...)` 精确读取，不把依赖目录预装进 Task。旧任务的显式 inline Task 约定仍在每轮保留。正文不复制进新 prompt，也不根据任务文字猜测来源。未选择 Skill 时 Task 只给 `agent_cli.read_skill()` 发现入口；无参数调用返回授权目录内的全部 name 和路径，不重复所有用途描述；按主题 `query` 查询返回匹配项的完整 name、用途和路径元数据，均不返回正文，Agent 再按精确 name 或 path 读取。目录查询和 name 解析沿用相同授权根，越界符号链接不会进入元数据，重名明确报错。目录 frontmatter 只提取顶层 name/description，嵌套产品 metadata 不会隐藏可用 Skill。无论有无选中 Skill，显式自定义 Task 约定都继续在每轮 Task 保留；服务生成的发现文字由结构化来源标记识别，不重复注入。新运行事实记录实际入口来源与本轮 `skill_names`；未注入的旧全量目录不再作为 `skill_protocol` 保存。Email 分类、Meeting Alignment 与独立 WeChat 没有任务内读取工具；Email 和 WeChat 保留完整冻结正文。Meeting Alignment 的 Codex 路线在有冻结托管会议 Skill 时保留其完整正文，省去已执行的操作 Skill 正文；其他路线及没有托管会议 Skill 的运行保留完整冻结正文。Email 分类的 Codex 命令按显式内联 Skill 名称，通过原生 skills.config 仅禁用同名安装副本的自动正文注入；本轮服务选定的托管或冻结正文仍完整提交。此配置不重新启用或关闭其他 Skill，不影响 Claude/Friday 的原有单条消息提交，也不改变分类规则、工具权限、路由、审批或会话绑定。会议来源预装配不能代替冻结托管 Skill 中的业务要求，因此 Codex 的精简输入仍包含该 Skill 的完整正文，不能只提交名称提示。Runtime Context 缩短工具说明；Codex CodeMode 的可用工具清单显示原生 `mcp__server__tool` 可调用名称，Claude 保持 `server.tool` 表示法，其他环境事实不变。Task 中的 Skill 调用提示仍使用服务命名空间的 `agent_cli.read_*` 表示法；该提示与清单的区别在 `docs/runtime-mcp-tool-name-validation.md` 记录。无业务调用的旧 build_turn_prompt、ceo_agent_thread_prompt 及 CodexRunner 的隐含业务 Developer 默认入口已退休；底层 native 指令保留模式及真实调用者显式指令不变。

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

Meeting planner 的 `audit_summary` 不是审核回执。分析成功后，服务以独立 model-only Audit（无原生工具或 MCP）调用复核会议来源、群候选、准确收件人、正文与披露范围；运行器传来的 Codex JSONL 先提取最终 assistant 消息，路由器已规范化的纯 JSON 直接解析，然后对两种输入执行相同结果模型及候选 SHA-256 校验。审核输出绑定候选 SHA-256，并和候选及 Audit session 引用一并保存。只有通过该摘要绑定校验且 `outcome=approve` 的候选可转为 `ready_to_send`。`return` 必须提供 rule/observation/requested_revision，服务持久化反馈并创建新 Meeting run 做最多三轮修订；用尽仍未通过才转入 `needs_human`。`reject` 转人工；格式或结果验证失败按原 Meeting 失败规则处理，外部依赖不可用沿用原退避重试。新投递和人工重排都会检查审核绑定，旧的未审核候选、缺失审核、修改后的候选或错误摘要均不得产生新发送。已经有 provider 回执时的只读效果核实与状态恢复沿用原有回执、正文、收件目标验证，不要求为既成外部效果补造审核。不可变收件人准备绑定、同一投递键、provider 回执和原目标冲突保护保持原样。

## Meeting Audience Recovery

独立会议路径复用共享消息受众合同。候选级保密历史拒绝保留为明确未知证据，继续读取其他
候选；不当作空讨论、无群或发送许可。群成员完整性和真实群人数独立于会议参会人数。
Agent 依据实际讨论、完整当前名册、稳定身份、当前职责和内容披露范围决定群或明确私聊。
仅有转写参会人而无完整日历名册时，群发现先按原发送可用性和标题规则预选，再读取保留群的完整成员；无标题匹配时仍保留原候选范围。完整日历名册仍先检查参会覆盖再筛标题，不能让不符合覆盖的同名群遮蔽合格群。该读取顺序优化不降低所选群的身份、完整名册和讨论证据要求，不缓存受众，也不赋予发送许可。
业务私聊只接受来源中唯一的稳定参会人身份或日历证明的组织者；拒绝本人和已核验别名。
个人私聊仍要求完整日历两人名册。正文同一私聊受众合并发送，不凭 HR 职务扩大披露范围。
计划群不可发送时保留失败，不能自动改投组织者或本人。
恢复前精确核对主消息及敏感消息已准备正文与本次披露范围，不仅检查相同受众分支。
旧 HR 合并正文若含仅适合另一受众的敏感部分，在任何发送之前明确失败；不覆盖正文或伪造送达。
会议准备在现有 `service_state` 的 `meeting_delivery_target:v1` 命名空间原子绑定规范化目标与
原动作正文，发送及复用回执前再次校验目标。新投递键包含规范化目标的 SHA-256 指纹；旧键仅在
其已有目标绑定与本次目标完全一致时复用，因此不同目标不借用原回执。旧 prepared 缺少目标绑定
时不从新决策推断或回填，明确保留未核实失败。重跑和来源恢复门禁同时检查旧键及目标专属键，
防止绕过已准备动作。无需新增表、列或历史数据 apply。
Provider 明确返回 `success=false` 的主消息或敏感消息记录真实失败，不继续发送后续部分，
不把缺失的送达回执升级为 `sent`。

`rerun_meeting_alignment_jobs` 是原身份重新分析入口，不是送达重试。事务内要求未保存任何
部分效果、未准备主消息或敏感消息、无已有回执、无锁/活跃 dispatcher 或投递归属，且无活跃
会议/运行时回合。已准备或已部分发送必须沿原回执恢复，不能清空重做候选。历史运行不改写。
这些身份和归属检查不证明模型所写业务理由真实，也不代表会议已迁入通用 Consumer/Audit 引擎。

`import-meeting-asr-transcript --job-id ... --input-json ...` 先只读预览目标任务和 ASR 元数据；
带 `--apply` 时，事务再次校验任务仍为未锁定的 `failed`、会议身份和日历参会证据不变、没有
投递回执/部分效果/活跃 dispatcher 或运行时 owner。它把完整 MCP ASR 分段、音频 SHA-256、模型
和完成分块数附加到原 `source_json`，保留已有来源事实与运行历史，并把同一 job 重新排入 `retry`。
分析及投递均从这份持久化 ASR 来源读取；无说话人身份时不把文本归给参会人。ASR 只替代失败的
逐字稿读取，不跳过 Meeting Agent 的内容、目标及受众核验，也不直接发送消息。并发改动、回执或
活动 owner 会使 apply 失败；不得通过再次导入或直接重置任务绕过该门禁。

### 历史反馈链接的来源读取（2026-10-07）

Consumer 捕获的历史来源文本可能合并多条消息；校验器按反馈 token 与 attempt ID 分组，并要求每组各自包含通过配置 host/path、query 字段和评分验证的上下反馈链接。旧链接的触发/回复预览各自仍接受敏感值检查，但文字差异不使当前任务失败；Markdown 来源解析保留 URL 原文，以便只在结果检查副本中移除经过检查的链接。当前待发送正文保持完整 query 的严格配对，来源原文、候选 digest 和执行前来源比较不受影响。

## Storage retention (2026-10-09)

训练数据正文和观察明细放在数据库旁的 `*-training-data` 目录，SQLite 只保留快照摘要与训练/评估元数据。运行时按快照类型保留最新完整基线，与各类型标签水位累计一致；已启动训练的 run pin 暂时保留其选定数据，结束后清理同类型旧快照。历史摘要不能恢复已经清理的数据，读取明确报告不可用。迁移先写出并校验每种独立数据类型的最新完整快照，再移除旧正文表；运行时不从摘要重建已删除的基线。既有迁移遗漏的当前基线仅从身份、水位及摘要完全匹配的已验证原始备份显式恢复，并读回校验，不恢复历史版本集合。

完整 Agent trajectory 的唯一数据源是 Codex/Claude 原生 session 或 Friday operation。服务仅持久化服务任务状态及定位原生运行所需的引用和准确范围；调用正文、精简事件、原始最终结果、runtime result envelope、审查调用正文及派生搜索正文不再保存第二份。当前调用流和解析结果仅在内存中使用，历史详情按需读取原生记录。迁移清除历史数据库副本，原生文件缺失的过程详情明确不可用，不回退到数据库副本。服务自己提交的业务状态、待执行输入和外部动作账本继续作为服务数据保存。

工作汇总输入进入 `done` 或 `skipped` 时，在同一业务投影事务中删除排队正文，只保留来源身份、来源时间、原始正文摘要与字节数、明确的精简标记及既有状态和运行引用。同一来源再次入队不会修改执行中或终态记录，迟到的失败或重试不能重新打开已精简终态；`pending`、`processing`、`failed` 输入保留准确正文供续跑。显式维护命令负责历史终态正文清理，初始化不执行该批处理。History 只显示来源元数据，历史语义导入需要原始证据时明确报告正文不可用。已采纳的业务证据、结果与执行回执不受正文清理影响。

回复任务在既有终态结算条件满足后，将当前输入、输入版本和对应 Attempt 的输入文字改为来源摘要及引用；每份被移除的原文都保留准确 SHA-256 与字节数，Attempt 只绑定内容确实匹配的输入版本。实际完成入口 `finalize_orchestrated_reply_task` 在业务 Attempt、记忆事件和发送回执写入后检查精简；仍有调度租约时延后，在 `ReplyQueueAdapter.complete` 释放租约的同一事务再次检查该任务。其他完成／跳过、回执和人工结算路径也只检查它们刚结算的任务。失败、待人工、更新的输入版本、活动 Agent／调度租约和未结束的微信投递保持完整输入；`done` 最新运行失败且没有既有结算依据时不精简。邮件动作、计划版本、邮箱／消息／线程身份、脱敏退订入口与认证引用继续用于既有回执校验；定时运行与配置引用、冻结 Skill 的版本或路径及摘要留在小型来源记录里。原有不可变输入校验的批量入口以摘要比较，普通消息仍按身份去重；准确的新输入清除精简标记并继续既有代际流程。精简标记不能作为可执行输入；历史重跑和冻结 Skill 缺少准确原文时报告不可用，不从其他副本或当前材料拼造原文。历史批量清理由显式维护执行，状态、业务结果、审查决定与执行回执保持不变。

独立 Meeting、Task、OKR 运行的原始决定及审查正文也只从原生执行范围按需读取。Meeting 已采用的 job 决定、Task 已应用的 projection 回执和 OKR 已采用的业务事项继续保存。会议搜索索引只保存 session/source/title 引用，完整会议来源与决定从该次原生输入/输出读取，正文和向量在有界内存缓存中计算，按原生文件及来源变化失效；既有 0.55 cosine + 0.30 BM25 评分保持不变。原生来源缺失时只有服务标题可检索，正文不可用。

服务采用的冻结业务计划保存在既有 review_candidates 中，包含服务准备的投递标识和捕获的来源事实；该计划与 Consumer 完成状态在同一事务内提交，恢复、审查和执行都使用同一已采用计划。既有 candidate_reviews 保存已采用的审查决定和修订反馈，并与 Audit 完成状态一并提交。服务准备阶段产生的失败在同一事务内记录 runtime attempt 与 Agent run 的失败状态，不把原生输出中的成功提案当成已采用计划。任务恢复不重新运行已完成的 Agent 或重新捕获历史来源。上述业务状态不作为 Agent 原始输出或过程详情的替代来源。最终任务记忆从已采用的 Consumer 计划读取，并按任务和执行代际去重；原生过程不可用不影响该项服务输入。运行流只在 RAM 中保留，事件发布使用既有 SQLite 写事务排序，终态成功提交后释放正文缓存，事务回滚保留实际观察到的运行流。运维延后与维护检查按原有条件读取准确范围内的原生证据；记录不可用不能证明没有工具活动，已证实尚未创建原生会话的失败仍按原条件处理。

业务 History、Attempt 指标和审批结果从服务已采用的 Consumer 计划、Audit 决定及既有 verified 外部动作回执投影，原生原始输出仅用于过程详情。审批回执按实际结构化操作、任务/执行代际及审批实例/已知任务标识匹配，冲突仍显示未知；不改变执行或审查规则。状态列表和恢复状态判断只读取服务状态及采用结果，不加载原生正文。工具开始记录保留实际输入，完成记录提供结果；同一原生 item 配对时，完成记录的空参数不会覆盖开始时的输入。

过程详情独立说明原生最终结果和工具过程的可用性；Friday 最终 Artifact 可读不表示工具过程也可读。可读的 Claude 记录没有受支持查看器时不提供错误的 Codex 链接。历史列表保留已采用的会议最终消息和目标，业务历史列表只读取服务采用的消息、标题与状态；原生搜索和单次运行过程详情在数据库快照关闭后按需读取。质量报告的信息性原生证据覆盖数量仅在当前内存报告/页面中展示，不写入小时状态文件，也不改变质量状态与违规规则。

Workbench 的实时文字和工具正文仅在运行时 RAM 中使用；SQLite 事件行只保存服务回放 ID、顺序和最小原生定位。工具开始与完成通过原生完成顺序定位，并按原生 item ID 配对输入与结果；只有完成记录时直接读取该条原生记录。历史文字以原生完成消息聚合回放。任务结束后，在事务提交后释放正文缓存；失败与停止运行也按准确范围读取已有原生信息。最终文字不保存副本，详情按需读取 Codex/Claude 原生记录或 Friday Artifact；远端 Artifact 的读取在 SQLite 事务外进行。原生记录缺失时明确显示过程不可用，保留实际任务状态和服务附件记录。运行提供者的原始错误正文也不另存；服务保留失败状态、错误码和服务生成的公共原因，历史错误正文清除后由错误码生成展示原因。

定时配置正文存于 `scheduled_task_config_versions`，触发记录以 `snapshot_id` 引用不可变版本。数据库迁移保留运行 ID、状态和已排队输入。

模型目录在无训练进程活动时清理，保留当前 online 模型、实际 active／previous manifest 引用的可运行制品和跨模型系列最新一份待评估候选；历史 mode transition 仅保留切换事实及回执，不因曾被选用而保留旧权重。没有 active／previous manifest 时只保留当前模型和最新候选；其他旧制品及遗留临时文件删除。业务状态、最终结果、执行回执与小型评估记录保留。数据库删除旧正文后需要执行存储维护并压缩页才能释放文件空间。

描述优化提案保持产生它时的快照身份与引用证据不变。若提案尚未评估而源快照已被最新数据替代，提案一次性转为 `unavailable`，原因 `description_proposal_source_unavailable`；不反复启动失败的评估，也不把旧证据套到新数据上。下一次基于最新数据的训练可产生新的提案。

Status API 与前端 SystemHealth 解码器均声明 native_delivery_coverage（checked、unavailable）：允许省略或 null；存在时只接受这两个非负整数，仍拒绝未知字段。该字段仅实时计算，不写入质量快照，也不改变质量违规判断。

Codex 原生工具只有开始、没有对应完成记录时，历史详情保留准确范围内的
`item.started` 和实际输入，并按原生顺序展示；不生成结果、退出码或成功回执。
匹配到完成记录时仍只采用对应完成投影，空参数不覆盖开始输入，旧 MCP end
格式的完成结果也不会被开始投影覆盖。这是过程可见性，不证明外部动作发生，
不改变运行终态、审批或重试资格。

服务捕获的 source_bindings 含凭据或未通过签名链接安全边界时，按
`runtime_result_source_invalid` 记录为 service/result 的不可重试失败，
不归为 Codex 执行失败，也不切换提供者来重复处理同一不安全来源。
模型自行生成的敏感值仍由原有结果边界拒绝；不删除来源字段、放宽凭据
检查或把该失败改成 needs_human。此分类修复不表示原业务已经恢复。

Task 验收读回在 SQLite 快照内取得准确的原生引用，关闭快照后读取该范围
内运行时实际选择的最后一个 schema 合法候选。验收保留所选候选的原始
字段，不用模型默认值补齐缺失证据，不回退到已移除的 SQL 决定正文。
原生决定缺失或全部候选无效时明确验收失败；业务投影仍以既有 SQLite
应用回执核对。本修复不授权生产语义导入，也不代替真实业务样本验收。

已完成的邮件分类任务不长期保存分类输入正文：`email_agent_classification_tasks.status=done` 在既有租约／代际校验的完成事务内，将 `input_json` 改为明确标记的来源记录，保留原始 JSON 摘要和字节数、去除 `scheduled_consumer` 后的不可变输入摘要、稳定邮件身份、provider locator、配置版本及脱敏退订候选引用。定时配置与 Skill 只保留运行／版本／摘要引用；模型输入、邮件正文和冻结 Skill 正文不再复制。重复发现以既有不可变输入语义核对摘要，不补回正文；内容变化仍按既有错误处理。pending／running／failed 保留完整输入以便恢复，分类结果、任务状态和执行回执保持原样。历史精简由显式维护命令执行，不在初始化时扫描。该历史分期保留了与邮件原文不同的模型特征文本。最终结构中的 `emails.class_model_text` 仅在活动或原文尚未证明时保留；终态且完整字节已由原生／provider 证明后，改用已有 `message_source_reference_json.model_input` 引用。人工相似案例仍读取准确特征，具体生命周期以下文最终合同为准。

Email schema 47 为分类表增加非唯一定位索引，前缀为 account_id／folder／uidvalidity，后续保留 status／updated_at desc／uid。稳定 UID 集合查询从覆盖索引读取，不再为文件夹和 UIDVALIDITY 过滤回读整张宽表；provider 元数据查询仍读取准确分类行，不使用缓存或重建内容。已有分类、计划、动作、游标和授权数据不改写，重复 UID 的既有状态／更新时间次序继续覆盖回归。v46→v47 在原升级事务中安装索引并记录版本，验证失败时索引与版本一并回滚；当前版本缺少索引视为结构损坏，不在读取中自修复。生产迁移须先验证备份、隔离对照与正式 quiet 门禁；旧运行时仍拒绝未来版本，回退须使用迁移前验证备份。此项修复针对已证实的定位读取放大，不代表全局锁等待、连接关闭延迟或主机内存压力已消除。

存储 schema 升级迁移业务对象映射时，只插入新键、删除已不存在的键或更新变化的任务绑定；绑定未变的 `business_object_tasks` 原行及创建／更新时间保持不变，最新任务与 OA 别名选择规则不变。

精简后的邮件来源记录同时保留业务分类 `category` 和候选来源 `action_parameters.candidate_source`，确保已完成 Attempt 详情的业务显示不变；其他参数正文不复制。日历卡片修订仍只比较呈现正文（精简后使用原文摘要），定时扫描运行 ID 的变化不会重新打开已完成任务。

## Provider identity index migration

Email schema 47→48 在现有初始化事务内删除
`idx_email_provider_observations_lookup` 并推进 schema 版本。新库不创建
或要求该索引，重复启动不重建它；folder 索引仍为 required schema。
点查使用原主键，文件夹查询使用原 folder 索引。删除索引释放数据库内部页，
不代表 SQLite 文件已经缩小；实际文件回收是独立的维护步骤。
本段是已发布 47→48 的历史行为。未发布的 Store20/Email52 候选将
两张 provider 表合并为 `provider_state`，以两个身份 partial unique 索引
和一个 observation-folder partial 索引服务当前查询。
未发布的 31 表候选中，四个 observation 来源字段 `provider_folder_name`、
`category_key`、`important` 和 `important_signals_json` 为原生可写列，
folder generation 对应列均为 NULL；不再生成 `payload_json`。现有三条 partial
索引、观察扫描的 set-based 更新、调用方事务和按当前事实逆迁仍适用。

## Schema publication and failed-start rollback

app.deploy、独立 repository_updater 模块和 CLI repository-updater 命令使用
ExistingSchemaUpgradeStateStore 读取部署状态，避免在 quiet/停止/备份前
构造 AutoReplyStore。数据库 publication 在 checkout 后、dependency/import
验证前捕获旧 schema；在停止窗口迁移后再验证和启动。

47→48 的回退只恢复捕获的 provider lookup 索引和准确的原版本历史，
新增业务数据及 provider 回执原样保留。回退接受已捕获的原 47 状态或
准确的 47+48 追加状态；遇到不兼容 schema 不删数据、不尝试旧代码启动。
restart callback 调用前即标记 attempted，使部分 bootstrap 失败也会停止
replacement。bootout 报错只有在原生 launchctl print 明确证明 label 不存在
时接受；权限或其他停止失败仍是错误。

每次新 schema 逆迁的首次上线必须由已更新的 deploy 调用方执行：已运行的旧 Python
updater 不会因为 fast-forward 而获得新 callback。后续更新后的独立 updater
入口也使用相同的停止、数据库 publication 和启动机制。

## Email current plan deduplication

Email schema48→49 在初始化事务内先执行现有业务状态校验和旧 action_plan_json
精确序列化校验，再删除 email_classifications.action_plan_json。计划由
current_action_plan_id 指向 email_action_plans 唯一读取；当前写入不保存重复 JSON。
分页列表一次批量查询相关计划，避免每条分类单独连接/查询。

模型异常归因的存在性查询只查分类主记录，不加载计划，也不表示计划有效。扫描使用的
原已有分类判断和完整读回继续
校验不可变计划；已有分类的计划损坏或版本冲突必须报告持久化冲突，不能误判为尚未落库
而回退到 Agent。迁移测试声明旧版本前，须重建该版本实际存在的缓存列；对分类元数据
漂移的修复不能覆盖或修复被篡改的不可变计划。

正式发布49→48的逆迁在一个事务内添加旧缓存列，以当前计划重建内容，恢复原版本历史；
只捕获旧记录的序列化区别 ID 集合，不备份正文。新分类和修订按回退时的当前计划重建，
新增业务记录和执行回执保留。校验或逆迁失败不会部分提交，也不以部署前数据库覆盖新写入。
该逆迁仅适用于已验证的48→49发布，与47→48索引逆迁分别匹配。

Attention 的失败回复任务查询先从 `reply_tasks` 状态覆盖索引筛选 ID，再按主键读取父任务及其 current 输入；筛选阶段不读取正文或连接输入表。现有失败条件、排序、限制和业务展示保持不变。


## SQLite connection lifecycle

`AutoReplyStore._connect` keeps one idle SQLite connection per database file
and thread, because every new connection re-parses the whole schema (about 520
objects with JSON generated columns; about 45 ms against the live database
versus 0.02 ms for a warm query), which on 2026-10-20 held the service at
180-300% CPU. Nested or concurrent use in the same thread still opens its own
connection, and a connection is discarded rather than kept when the body
raised, a transaction is still open, `foreign_keys` is no longer on, the
database file was replaced, or the store's `_open_connection` differs. The
"close" time in the slow-context diagnostics is therefore the time to return
the connection to the idle slot. Code that registers functions, handlers or
pragmas on a `_connect` connection must undo them, because the next call in
that thread sees the same connection. A connection going idle releases its
page cache (`pragma shrink_memory`) and keeps only the parsed schema: with the
64 MB read cache per connection, four warm idle connections held about 230 MB
more than four released ones, and the service keeps one per thread per database.
No query, schema, retry, audit or safety-policy change.

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

Phase5E 隔离分支的 Store19 候选以完整十六类型 action manifest 验证
Attempt/Memory；实际 Store18 升级和 fresh 创建分别保留无 Memory 外键与有
Memory 父外键的唯一规范模式。缺失辅助对象的重启修复先匹配整套模式，不能
凭一列推断或把 actual 修成 fresh。Store 内部的 Attempt/Memory CRUD 已路由
到有限 action reader/writer；Memory 写入完成只更新相同事件 ID 的
pending/failed/written 状态、episode、错误和更新时间，不增加重试次数或重建
删除的事件。外部 History/Attention/质量扫描/恢复/邮件/维护调用者使用
有限 Attempt/Memory 投影与按公共 ID 的 action 更新；原有触发身份、
状态排序、租约条件及业务回执保留。Attempt 详情的 Audit 工具输入输出只
读取原生会话中明确指定的行范围；原生缺失时显示不可用，数据库中已退休的
工具正文不作为回退。发布前 quiet-wait 在完整 Store19 actual/fresh manifest
上继续读取候选、回执、postfix、delivery、任务、会议及定时来源。
Store19 候选的 Attempt 创建时间及状态索引包含首列 `record_type`；Reply 公共
ID 排序索引保留原键和谓词并声明唯一。前者让时间范围读取使用索引，后者
让 fresh Memory 的公共父 ID 精确读取通过父索引和 Memory 父索引完成；
原有公共 ID 唯一约束及规范整数文本约束已保证该唯一性。这些调整只属于
Store19 索引声明，Memory 投影的 LEFT JOIN 和旧版本声明不变。
上述 Phase5e 的离线验收见 `docs/database-redesign-phase5e-validation.md`；
该历史候选状态不代表当前发布状态。当前结构与生产读回以本文开头及专有动作
验收记录为准，不据此声称完整 CI 已通过。


### Full document native output delivery

Consumer/Audit Codex commands use the existing per-tool native output budget of30,000 for `agent_cli.read_dingtalk_document_full`, on cold and continued turns. The server's complete result can otherwise be clipped in the native model-visible output. This preserves the selected JSONML/revision result without substituting Markdown, changing all-tool budgets or adding prompt rules. The source may still exceed that finite budget; server complete=true alone is not proof of complete model visibility. Regression/native evidence is recorded in `docs/document-native-output-validation.md`.

Consumer 通用 Skill 约定只规定独立选择并应用适用 Skill；每轮 Task 发现入口统一决定何时读取，避免通用层的读取措辞与冻结来源复用条件冲突。安装来源和未选择 Skill 的既有读取入口不变，不新增缓存或读取回执。


Consumer/Audit 的原生 `read_dingtalk_messages` 单工具输出限额为30000，用于完整交付已请求的消息字段；该设置冷启动和续会话均保留。不提高全局输出限额，不改变窗口工具、正文或分流/审核规则；长目录与文件读取保持各自现有单工具设置。

`read_dingtalk_messages` 可用 `direct_open_dingtalk_id` 传入触发消息的对方身份；该参数即选择单聊路径，无需同时设置 `single_chat=true`。DWS 通过 `list-direct --open-dingtalk-id` 读取，不把单聊 CID 当作用户身份。显式设置 `single_chat=true` 却未给身份且标题为空时，在服务工具边界明确失败，避免空字符串联系人搜索；标题解析和群聊读取沿用原路径。此变更不增加 Audit 规则或改变消息正文投影。

`agent_cli.read_dingtalk_messages` 只在每条消息顶层字符串与同条 `raw_payload` 直接字符串字段完全相同、且按 FastMCP 实际缩进 JSON 格式放入 `messages` 数组后的模型可见文本更短时，才将重复的原始字段值改为 `null`，增加 `raw_payload_field_refs` 指向仍完整保留的顶层字段。展开引用可恢复原始字段值和键顺序；未重复字段、引用消息、反应、资源、身份、消息数量和顺序、失败结果保持原样。此投影只发生在该工具返回边界，不改变 DWS 查询、其他 MCP 工具、Prompt 或审核策略。

`agent_cli.read_dingtalk_sheet_range` 只在 DWS 成功返回且按 FastMCP 实际缩进 JSON 计算更短时，将各单元格重复的完整 `cellStyles` 对象放进顶层 `sharedCellStyles`，以原字段位置的 `cellStyleRef` 索引引用。原始 JSON 的行列、单元格值、样式内容和键顺序均可还原。失败或较小结果保持原样；不改变 DWS 查询参数、角色工具集、Prompt、session、路由或审批策略。单块输出仍受 Codex 有限大小约束，服务不会把 `success=true` 解释为模型已读完整表格。

OA pending scans carry the complete scheduled Consumer wrapper, including exact frozen Skill materials, into the claimed reply input. Consumer and Audit read those original bodies through read_task_skill; the scanner must not use the Task extraction projection that omits skill_materials. Task extraction sources retain their existing body-free scheduled context. Review deduplication includes a digest of selected OA Skill bodies, so a changed rule schedules one fresh review for a still-pending approver even if DingTalk records are unchanged; it waits for any same-approval review already pending or processing to settle before enqueueing that new revision. An unchanged Skill snapshot stays deduplicated. For org-structure approvals, applicant-correctable missing facts with a valid OA return path use a formal return; comments are reserved for missing return paths or genuine external-party waits, with no added document-format requirement. This changes input delivery and the selected OA policy, not routes or sessions; old inputs are not rebuilt.

An unchanged daily OA scan may revisit an approval whose prior review completed without a decision, so it remains visible to the reviewer. It must not reopen a terminally failed technical review for the same evidence and selected-Skill revision: doing so would rotate the reply-task execution generation and refund the Consumer/Audit retry budget. A new DWS evidence revision or selected-Skill revision starts a new review round with a fresh budget; a successful no-action review retains its normal daily revisit behavior.

OA review round binding (2026-10-15, completed 2026-10-17): when the scheduled OA scanner enqueues a new review input, it reads the current scheduled-task Skill selection again. For each selected managed Skill it resolves the newest registered revision directly from the managed Skill store. A revision missing from the worker's immutable startup snapshot (`managed_revision_not_loaded`) can be materialized for this new trigger; a disabled or otherwise unavailable latest revision stops the scan instead of falling back to older guidance. Selected operation Skills are read from their current authorized files. These exact bodies are frozen in the new OA trigger wrapper. Retries and corrections reuse their existing trigger snapshot; historical inputs and runs are immutable. A binding failure leaves the OA scan unadvanced and records the reason. Approval permissions, business thresholds and action gates do not change.

Service Consumer turns prefer their declared `mcp__agent_cli` tools for DingTalk reads and may use local `dws` commands through shell for source reads when appropriate. The OA Consumer may use the selected OA Skill's `dws oa approval attachment authorize-preview` and same-file read/download steps when native preview permission is missing. Consumer must not use `dws` through shell to send DingTalk messages or email, or to perform OA approve, reject, return, or comment actions; those effects use the reviewed typed-action path. Audit uses declared read tools only and must not use shell or local `dws` commands. OA instance/task identifiers passed to read tools come verbatim from the current pending/detail fields; parameter or response-shape errors trigger source-value verification before a safe same-target read retry, and are not treated as missing applicant evidence. Missing native read capabilities remain explicit dependency failures. `search_dingtalk_documents` publishes `page_size` 1–20 in its native parameter schema; invalid sizes fail argument validation before DWS, while omitted and valid sizes keep the existing query behavior.

The task-bound `read_dingtalk_messages` and `read_dingtalk_messages_in_window` resolve the direct-chat counterparty from the claimed trigger's stable openDingTalkId when given that task's conversation ID. The resolver compares task generation, original message ID and conversation ID before use. The window path calls `read_complete_message_window`, so it returns `complete=true` only after the existing pagination and query-range checks; its messages must still belong to the bound conversation. The declared MCP tool set exposes reads only; Consumer shell use of `dws` is separately limited to reads by the role instructions and must not send messages or email or perform OA actions.


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

OA approval Attention reflects an unresolved process-level state from the latest
verified provider scan. A running OA process may have completed the current
principal's node and moved to another approver; unless the provider detail
identifies the current node owner, the projection must not claim that the
principal's node is still pending. Keep the process in Attention until a final
approval or return receipt is read back.

### 日历组织者来源字段

日历解析继续生成原有 organizer 显示字符串，同时在 organizer_details 中保留
provider 返回的完整组织者对象。日历事件读取、窗口读取及既有 model_dump 任务输入
因此可看到原始 userId、openDingTalkId、邮箱及其他来源字段，字符串标识和嵌套值
不被改写。来源只有字符串时 details 为空，不根据显示名猜测稳定身份。新增对象是
来源证据，不能冒充接收人核验或授权结果；既有动作、Audit、通知和运行路线不变。

### DingTalk message-list response contract

DingTalk message-list readers require a structured object response. JSON `null`,
a non-object legacy result envelope, or a missing `messages` array in the
canonical `im.message-list.v1` contract is a typed provider-contract failure,
never an empty-message success. Unread reads do not
retry an invalid `null` response through the recent-message fallback; valid but
unrecognized object envelopes retain the existing fallback behavior. Read-only
message commands make only the configured bounded retry for JSON `null`; if it
persists, the typed failure remains visible to normal recovery and Attention.

### Email actions: isolated storage declaration

`app/action_storage.py` can explicitly build a twenty-nine-type `actions`
declaration when `include_email=True` is paired with the current reply variant.
The default factories still build their prior declarations. The Email addition
defines the 13 original families, their source-field projections and their
own native constraints/indexes/triggers. It does not route live Email reads or
writes through `actions`; no Store/Email initializer selects this option.
Finite source-shaped Email mutation helpers now preserve nine signed source
ROWID locators, two independent AUTOINCREMENT sequences and two composite keys.
The writer lock precedes source default evaluation and locator/payload reads;
successful calls return the source identity. Separate pure forward and
current-fact inverse functions reconstruct all thirteen source families inside
a caller-owned foreign-key-off transaction, translating the thirteen external
blockers and preserving the original locators and allocator waters. They are
explicit source-shaped migration functions selected by the paired release.
Their historical scaffold declarations remain migration inputs; current runtime
uses the final compact Actions layout. Production deployment is still pending. Preflight
replays MAIN and TEMP schema bindings in a separate SQLite connection and
probes rename and DROP effects before touching the caller's tables. A TEMP
trigger or view that would change or disappear is refused; unrelated TEMP
objects remain untouched.
Generated Email columns are scoped to their exact source family membership;
same-named payload members on an older action never appear as Email fields.
An Email-only CHECK keeps the older task/run/action physical reference slots
NULL, including the fresh variant's memory attempt reference; native Email
source-key foreign keys remain the only Email parent relationships.

The selected paired follow-up/Todo-sync migration requires the caller's
existing foreign-key-off transaction. It recaptures the reviewed source before
writing, copies ten action families and retargets the retained legacy-link FK.
Its inverse reads current typed rows and current sequence waters before
restoring the literal actual or fresh source tables, indexes and original
legacy-link FK. The paired release updates Store and Email markers in the same
transaction; neither conversion performs provider calls.

Recorded DingTalk delivery recovery (2026-10-14): startup reconciliation, explicit failed-task completion and protection against failed-run projection all honor the receipt verification state. A recorded receipt with verification.state=ambiguous or failed does not prove delivery and cannot restore done. Positive sent receipts and previously recorded legacy readback receipts keep their existing completion behavior.

Historical terminal Attempt or settlement projections cannot override a current external_action_uncertain/external_action_failed task error. A later verified provider result uses the normal execution completion writer. Original Attempt and settlement history remains unchanged.

An operator may explicitly stop a current failed `external_action_uncertain` Reply task through
`GET /api/console/reply-tasks/{task_id}` followed by `POST /api/console/reply-tasks/{task_id}/skip` with
the observed `execution_generation` and a non-empty reason. The generation and uncertain failure are checked
again inside the write transaction. The task becomes `skipped`; its external-action error and original Attempt
and Agent runs remain unchanged, and an immutable settlement error records the operator's reason. This records
that no more work should run; it does not report the external action as successful or verified.

Attempt detail and History attention prioritize the current failed task delivery error over historical Agent success summaries or a stored sent-reply row. Uncertain delivery is displayed as unconfirmed, while provider failure is displayed as failed. Historical Agent explanations and original raw receipts remain available as historical evidence.

Attempt detail 的 System Execution 只对当前审核候选，或已有执行记录的历史候选生成投影。过期候选若没有执行记录，仍可作为历史审核证据查看，但不把其中未开始的动作显示为“等待执行”。已有历史执行和 canonical provider receipt 继续只读显示；此投影不改变队列、重试或执行状态。


### 原始触发、当前来源和立项财务口径（2026-10-14）

钉钉 Task 保存原始触发快照和固定回复目标，近期会话是另一次来源读取；同一 message_id 的审批卡片可更新，Task 标明两者来源及正文是否一致。近期已核实的处理结果可证明原请求已解决，后续新请求仍是独立任务。时间事实提供 UTC 及相对原触发的秒数；来源时间不可解析时不推断顺序。Audit 的 source_bindings 由服务在 Consumer 输出后捕获，反馈应修改 Consumer 自己的候选，不能要求 Consumer 重写原始来源快照。

立项财务 Skill 按本次项目范围、期间和综合口径判断毛利；历史试做行不自动代表当前 POC 或第一曲线项目。先核对成本是否已包含，避免重复计入。实际适用的第一曲线项目综合毛利低于 25% 时，原有特批路径继续适用。修订通过新的托管 Skill revision 发布；历史任务冻结输入不被覆盖。


会议工作人格来源引用（2026-10-14）：Meeting Alignment 每轮只取得一次实际注入的工作人格正文，来源引用为该正文的 `work_profile:sha256:<digest>`；历史来源按该引用回写，完整原生输入可重算摘要。会议结果不再被提示词要求回写本地文件路径。现有 codec 的本地路径和凭据限制不变。

Meeting 的 Codex CLI 每轮显式设置 `project_doc_max_bytes=0`，省去 CLI 自动加载的工作区 `AGENTS.md`；服务装配的会议来源、工作人格、冻结托管 Skill、结果 Schema 与受众校验不变。非 Codex 路线不使用这个 CLI 配置。

Meeting Codex Developer 指令只保留任务特有的 DWS 群与身份只读取证提示；禁止直接对外发消息、邮件和执行审批的行为指令来自所有 Consumer 共用的同一文本。Meeting 只返回决策，由既有服务流程投递。这些指令不是 DWS 或 shell 的强制权限隔离；命令、凭据及投递实现均未改变。

会议 Skill 范围（2026-10-18；2026-10-21 修正）：服务仍保存并组装本轮全部冻结材料。仅 Codex CLI 路线在存在冻结托管会议 Skill 时，使用该完整托管规则构造实际提交输入，并在原生 Skill 目录保留 `dingtalk-chat` 入口供按需调用 DWS 群读取；其他重复 Skill 仍隐藏。服务已完成听记、日历等操作读取后，不再将对应操作 Skill 全文交给会议判断轮。Claude/Friday 及缺少托管会议 Skill 的旧任务保持原输入。原生输出纠错沿本路线的实际输入继续，切换路线时改用目标路线的输入；会议结果、受众和投递契约不变。匿名固定对照及早期校验漏判见 `docs/meeting-managed-skill-scope-validation.md`；修正校验后的三轮结果见 `evals/trajectory_optimization/meeting_skill_scope_native.v3.json`。


已有外部动作的核验（2026-10-14）：System Executor 对已有 dispatched/uncertain journal 先调用具名处理器只读 reconcile；旧动作造成的来源变化不能阻止读取实际回执。该路径不再次 dispatch。所有尚未派发的新动作仍先检查原审批候选绑定的当前来源，已完成动作的身份和正文冲突检查继续执行。原生发送回执只从明确的发送返回结构读取 send-task 身份，引用的入站消息 ID 不能作为投递凭证。


正式部署的停止完成条件（2026-10-14）：在 bootout 前读取本次 launchd 服务的 supervisor 及其直接服务子进程 PID；停止等待同时要求标签已卸载和这些已知进程已退出，再进入备份、代码切换和数据库发布。沿用现有停止超时，不扫描或清理无关 CLI，也不增加审批步骤。


## Claude native attempt coordinates

Claude attempt start/end coordinates use the native Claude JSONL line counter, including resumed, completed, cancelled and interrupted turns. Streamed Claude references use claude_session; stdout event counts are not native transcript coordinates. Codex/Friday bookkeeping and task/session/routing policies are unchanged. Historical incorrect ranges are not rewritten or reconstructed. Original anonymous native evidence and current-main regression results are documented in docs/native-input-evidence-release-validation.md.


## Complete inline schema formatting

Claude/Friday single-message bridges serialize the complete output schema as compact JSON. Parsed content, descriptions and whitespace within string values are preserved; only JSON formatting is removed. Codex retains its native file channel. Schema occurrence count, validation, facts, routing and sessions are unchanged. Verification is documented in docs/native-input-evidence-release-validation.md.

Claude Consumer additionally passes a compatible `ConsumerAgentWireResult` schema
inline through native CLI `--json-schema`. This projection retains the field
shapes and required fields but removes the wire schema's unsupported `oneOf` and
`allOf` conditions; the unchanged Pydantic wire/business models remain the
authoritative validator for those relationships and every result. The adapter
uses the native result's JSON text through the existing event normalizer and
parser. Audit and other Claude workloads keep their existing command; no route,
tool, Skill, prompt rule, retry or session policy changes.


## Task citation mismatch diagnostics

Existing current-Project citation validation reports both the expected immutable WorkItem source_ref and the received reference when they differ. The existing repair path receives those known values. Validation conditions, result contract, repair budget, routing and permissions remain unchanged; no additional prompt rule is added. Verification is documented in docs/native-input-evidence-release-validation.md.


## Paired Store/Email schema release

The selected Business candidate component adds a default-false
`include_candidates` declaration option for attention items, project
candidates, and WeChat Memory candidates. Its writer preserves the three
independent native public-ID allocators and caller-owned transactions. Paired
forward conversion moves their current facts into typed Business records and
rewrites five existing child FK endpoints across four retained children;
inverse restores actual or fresh source declarations from current facts,
including new writes, child rows, and public allocator waters. The existing
coordinator and the prior current Store entrypoints used the 25-table selection. Historical
source factories retain their nine-kind Business default; current constructors
explicitly select all twelve kinds. Attention and project candidate projections
expose source public IDs and source-shaped fields, and WeChat review/write
transitions preserve their conditional updates in the original transactions.
Actual-copy and release acceptance remain separate from development selection.
On inverse, the candidate component transfers its three `business:` public
allocator waters to the corresponding native tables and removes only those
three aliases before the base Business inverse verifies the source. Old-kind
aliases and the consumed private Business water follow their existing rules.
The inverse restores the original private Business allocator when no later
Business insertion advanced it; after later inserts it retains the consumed
private high water, including an insert later deleted. New-candidate-only
writes can therefore leave harmless private Business ID padding, while their
native public-ID waters remain exact.
Forward and inverse preflights use SQLite identifier case rules for reserved
MAIN/TEMP names and the existing SQLite schema dependency probe for external
views and triggers. They reject unsupported dependencies before DDL and keep
the exact Actions legacy-link counterpart trigger through both directions.

The then-selected Store/Email constructor used 15 ordinary tables:
the ten remaining Business Action families are exact source-shaped Actions
projections and finite writers. Fresh creation converted their transient source
tables in the same publication transaction; reopen recognized the typed graph
and did not retain those ten source tables. The paired release captured only
declarations, dependency facts, and allocator waters, converts these families
after Business candidates and before the two markers, and reverses this layer
before candidate and Feedback inverses. Signals still join the retained
BusinessSourceDocuments body; native LegacyLinks stays a native table with a
typed Signal FK. Signal, date, anchor-link and Attention-event public IDs use
four independent source allocator aliases, while six composite families retain
hidden source ROWIDs; the physical Actions ID is private. Date copying remains
one INSERT SELECT with the original eight-field duplicate test, created_at,
and all-row statement atomicity. Attention membership replacement preserves
unchanged membership rows and timestamps. Historical native source factories
and exact lower-stage current predicates keep their previous roles; the new
current role explicitly checks the Actions and Business Action declarations.
This is a local selected-code path pending independent review and actual-copy
proof, not production behavior.

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

## Email13 group4B DAO WIP boundary

The unpublished candidate's current plan/direct/feedback/retry/attempt DAO reads
now use finite actions projections. All direct claim, completion, explicit retry
release and stale-recovery transitions keep their original writer transactions
and guard outcomes. A finite direct-action helper rechecks the original public
action ID and supplied status/start/attempt-count predicates, then mutates the
resolved source ROWID through the accepted writer. A failed CAS still raises or
returns the original caller outcome, and the caller's full transaction rolls
back. Attempt IDs come from the original independent AUTO namespace.

Operation creation preserves the exact plan/type `DO NOTHING` conflict target,
without suppressing other native constraints. The two shared C/D dependency
reads are converted solely to allow the B call paths; C/D lifecycle writes,
startup metadata/durable validation and constructor activation remain deferred.
The default schema is still Store19/Email51, so this partial branch does not
claim usable default business paths, paired publication readiness, or native/
provider acceptance.


## Email13 group4C unsubscribe DAO WIP boundary

The partial unpublished DAO now covers all six unsubscribe families, including
state/terminal snapshots, live lineage, observation/context reads, continuation
and journal writes, claim release/recovery, journal repair, terminal evidence,
exact URL backfill and cancellation. Each existing explicit writer transaction
keeps the original predicates while resolving public source identities to the
accepted writer locator. Multi-row mutations remain within that transaction;
a failure rolls back earlier changes. Journal steps use their original AUTO
allocator and effects use their original composite identity, independently of
physical actions IDs. Source-valid SQL NULL public IDs remain a storage concern
separate from strict application validation.

Continuation UPSERT uses its original public-key conflict target and changes
only effect digest, observation reference, controls and updated time. Creation
time and structural source ROWID survive the update, including encoded scalar
values. Existing broad OR IGNORE effect writes remain broad, and receipt
promotion/backfill retain their original allowed updates. Backfill takes the
writer lock only after URL validation, then applies its original empty-address
condition; an intervening fill still returns false without overwrite.

The C deferrals stated at group4B acceptance are now closed for current DAO SQL.
Seven reply lifecycle methods remain group4D work; only their two required mixed
reply-receipt reads and the previously accepted shared reply-claim guard are
converted. Historical schema dispatch, startup validation, fresh constructors,
real target markers and the paired coordinator remain E/F work. Store19/Email51
is still selected by default. This branch therefore remains partial preparation,
with no default-runtime, full Task4, native/provider or production acceptance.


## Email13 group4D reply DAO WIP boundary

Current EmailStore reply claims and receipts now use finite actions projections
and source-shaped writers, with all seventeen claim and thirteen receipt fields
preserved. Every original D writer already held BEGIN IMMEDIATE; the conversion
adds no transaction or earlier authorization boundary. Locator selection keeps
the original public identity, status, owner/generation/lease and receipt JSON-text
predicates within that same writer transaction. Native uniqueness and typed
parent FKs remain in force.

Dispatch replay and immutable conflicts, retryable snapshot refresh, terminated
owner recovery and uncertain no-replay behavior retain their original outcomes.
Receipt replay can enrich a previously empty SMTP acceptance ID but cannot erase
or replace a non-empty one. A failed claim completion rolls back receipt inserts
and enrichment. SQL NULL public keys remain separate from the strict public
method input rules; no source storage constraint has been tightened.

Current thirteen-family DAO SQL is now converted inside EmailStore, superseding
the historical B/C D deferrals. Default constructors, metadata/row validators,
historical schema dispatch and real paired release activation are still pending
E/F work. Cancellation preview depends on the deferred shape validator. External
consumers and native/full-copy/production acceptance remain separate Task5/6
work. This is partial unpublished preparation; dormant SMTP remains disabled and
Store19/Email51 is still selected by default.

History latest-task lookup publication666f4078 passed exact-tree full Quality
and normal deployment on2026-10-14. Production PID31985 and healthz were read
back; live History list/chart, type/status controls, pagination and an existing
Attempt detail were opened. Initial20s combined-request timeout and variable
Status latency remain recorded; system health still reported degraded and
unavailable historical native delivery evidence. This is scoped read acceptance,
not proof of all task execution or provider outcomes. See
`docs/database-redesign-history-query-validation.md` for exact evidence.
No schema or retention change is part of this History release.

The subsequent local History query candidate narrows each selected source branch
to `source_table`, `source_id`, and `occurred_at` before the combined page sort.
The existing type, status, and text filters still run inside each source branch;
the selected page is hydrated through the original full projection in the same
read snapshot. Two later/superseding reply-attempt status checks share one
per-trigger maximum terminal public Attempt ID, preserving the same eight
terminal statuses and strict newer-ID comparison. At that candidate stage, the
failed-only path was unchanged; the 2026-10-16 correction above now shares the
canonical status projection. This candidate has
focused local tests and Task6 whole-copy acceptance. Plain, matching and
no-match public reads completed in1.7845s,0.9163s and1.3001s respectively;
isolated read-only UI clicks also passed. Search still evaluates its original
text predicates; this evidence does not promise a general latency bound or
production acceptance. See the Email-actions native-copy acceptance JSON.


### Unselected current Email fresh seeds (Task4E1)

A private fresh-seed entry point now reuses the original category insertion,
model-promotion defaults and initial description-revision logic without running
the historical migration chain. The original v19→20 and v33→34 migrations call
the factored bodies at the same points, retaining their clock, UUID, legacy
conversion, DDL and ledger behavior. The fresh entry point requires retained
tables and leaves transactions, savepoints and rollback to its caller.

No constructor or reopen path calls it. F must select it only for a newly created
database, never as repair on reopen, Email51 migration or reads. Repeated calls
on seeded facts allocate no new IDs or clocks; existing configured sets and
promotion/revision facts are preserved. Tests use an explicit neutral factory
and genuine archived2773 default fresh0→51 facts, not a current default-constructor
proof. Metadata/durable validators and coupled default activation remain E2/F;
Store19/Email51 selection, production and dormant SMTP behavior are unchanged.


### Current Email29 validation, Task4E2 preparation

The candidate's one current shape/durable validator now targets the finite29
schema. Its active plan, retry, direct, feedback, claim, effect, continuation,
journal and receipt reads use the accepted projections. Exact action manifests
cover both existing reply variants. Original16 retained-business requirements
plus ledger, required metadata and retained-row policy remain; optional three
historical tables are not newly required by startup. The49-object neutral
factory proof is separate from this original startup coverage.

Shape validation selects19 retained native triggers and13 exact existing
translated blockers. Their finite installer is caller-transactional and creates
only missing triggers, without repair/overwrite. Blocker predicates, status
checks and errors stay unchanged. Durable validation adds no filtering, writes,
commits or receipt/authorization/recovery policy. The genuine-source comparison
retains specific NULL behavior: native NULL receipt identity remains accepted
by the row validator but refused by the public writer; NULL feedback ID remains
a durable refusal. The original retry timestamp ValueError is also preserved.
The existing public cancellation preview now works read-only on an explicit
converted target and returns the original decision.

F is still pending. The default Store19/Email51 bootstrap currently creates the
old layout, while the current validator targets29; this unpublished intermediate
state is not a runnable default release. Historical <=48 cached-plan/source
migration code is preserved, not routed through an alternate validator. F must
refuse unsupported older sources before mutation and switch the supported pair,
constructors/locks, coordinator and real markers together. E1 fresh seeds remain
unselected; external Task5 and copy/native/publication Task6 remain open.


### Coupled Email29 bootstrap and release candidate, Task4F

The earlier candidate selected Store `2026-10-14.1`/Email52 together with
34-kind actions and typed configs, schedules and Skill receipts. Exact Store19
(`2026-10-13.5`)/Email51 must pass through one paired
coordinator release before target constructors run. Direct old-pair construction
refuses read-only before WAL/cache/schema mutation and repeats selection under
the existing lock; unsupported/mixed/old Email-only sources are also refused.
The real publication child receives the prepared full metadata binding on stdin,
atomically applies both markers/schema, and cold opens both real default classes.
BLOB timestamps retain native values/types. Failed deployment verification uses
the existing current-fact inverse before the old restart; unsupported inverse
leaves the replacement stopped under existing updater behavior.

Fresh Store keeps its legacy early commits but publishes canonical classification,
34 actions, typed configs/schedules, mapped runs and its marker in one final
transaction. A failed prefix/suffix can
leave unversioned core and existing retry refusal; no new repair is introduced.
A successful Store-first state is valid. Email-first invokes actual AutoReplyStore
once before taking Email's non-reentrant path lock. Its fresh transaction creates
current retained nonactions, ledger, original seeds, seven retained Email blockers
and52; the six account-owned blockers are installed by configs;
rollback returns to Store-first. No historical0→51/action_plan_json/old13 creation
runs. Current reopen and source51 migration leave existing empty config/revision
sets empty. Existing coherent-current Store repair behavior is retained for
missing canonical Store-owned Skill/runtime guards, selected schedule/Skill
indexes, run auxiliaries and action auxiliaries; altered declarations and
Email-owned auxiliaries remain refusals. Target index definitions and constraints
remain exact after repair; SQLite's `PRAGMA index_list.seq` enumeration order
may differ after recreation. Email current reopen validates the exact current
`provider_state` declaration before durable
reads. The old Email51 provider-observation signal-column repair is retired;
malformed marker-correct current targets are refused without repair.

Local tests cover real constructors, both orders/concurrency, child fault/crash,
current-fact inverse and the actual RepositoryUpdater lifecycle using the newly
loaded selector. They do not establish selector reload in an already-running old
UI/updater process. Task5A now reads current Email action History (including the
failed candidate path), Attention, unsubscribe receipts and deployment quiet
state through finite source-shaped projections. History identity uses the
signed source ROWID even when the public action ID is NULL. Attention retains
current-plan, status and retry-window predicates; the current Email runtime
requires both Store20 and Email52, while generic publication readers accept
Store20-first before the Email ledger is initialized. Deployment quiet checks
read the exact Store19/Email51 native source or Store20/Email52 target.
Historical/default fixture adaptation and the remaining Task5 selected paths
are still open. The branch is not deployable; source-copy/native/provider/CI/
publication gates remain open.


### Original Email metadata refusals, Task5B0

Store's missing-object repair permission excludes the32 Email-only auxiliaries
already required by the target manifest. Their same-variant29-minus16 name
difference is checked against canonical object type, owner and SQL, not a pair
of special index names. Email does this read-only before its genuine Store call
and retains EmailPersistenceCorruption; Store also requires the same metadata
before entering its original current-known-repair path. Missing Email auxiliaries
remain missing without schema/fact/marker/DB-byte or DELETE-journal changes.
The shared action table/public-ID index and all prior Store repair paths retain
their existing treatment. Normal Email→Store call count/lock order, current
provider-state exact current validation, fresh orders and coordinator/inverse
remain as described in the current candidate. The old provider signal-column
repair is retired.
No general factory, writer, source contract or Task5A consumer was changed.
This is an unpublished regression correction, not final Task5 acceptance.

Current Email29 action-family metadata belongs to the shared Store20 `actions`
table. If its generated Email column, CHECK, collation or attempt parent FK is
malformed, the genuine Store schema preflight raises `sqlite3.IntegrityError`
before EmailStore's own current-schema validation, even with
`validate_rows=False`. The Email Web route's existing exception mapping marks
this `sqlite_integrity_error` and keeps Email unavailable. The older Email51
separate-table metadata path used `EmailPersistenceCorruption` and
`email_persistence_corruption`; this diagnostic difference follows the physical
owner of the schema. Neither path repairs the malformed structure or adds a
retry, recovery or authorization rule.

The Store20/Email52 pair selector runs before the Email retained-schema
validator. Partial malformed/future Email ledger values and a complete current
pair with a future Email ledger are rejected as
`sqlite3.IntegrityError("unsupported or mixed Store/Email source schema")`;
a ledger lacking `version` raises `sqlite3.OperationalError("no such column:
version")`. These source-selection reads do not change schema, ledger or
Store marker facts, schema cookie or database bytes and do not open a repair
or automatic retry path. Current retained Email business-table identifiers
remain case-insensitive under the existing validator; their quoted forms use
SQLite's normal double-quote, backtick and bracket syntax. Shared `actions`
metadata and the pair markers are outside that retained Email owner scope.

The Web API's direct `register_email_routes` path catches this paired
IntegrityError and keeps Email endpoints unavailable while routes registered
beforehand can answer. Full `create_audit_app` constructs AutoReplyStore first;
its paired preflight rejects an unknown future Email ledger before route
registration. The old Email51 whole-app isolation test runs in its bound
published-source suite, while the current whole-app test asserts the refusal
and unchanged schema, markers, cookie and bytes. No fallback, catch or retry
was added to the runtime.


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

### 会议人员方案披露与成员准确率（2026-10-15）

裁员、减员、人员优化的存在、进展、时间、名单复核、交接及替代培养安排本身均属人员敏感信息，没有个人姓名或人数也不得当作普通业务内容。共享消息受众合同及会议/人员 Skill 要求对完整当前群成员（含非参会者）逐一核验实际正文的披露权限；业务相关、历史讨论、HR/管理职务及参会人重合都不能代替授权。权限未知或缺失时，业务群标题、正文、待确认问题和任务提及均排除敏感内容及其间接暗示；不同内容仅向独立核验的授权受众拆分，无合适受众时保留人员跟进未解决，不转发本人兜底。人员匹配采用3/N（匹配参会人数/完整群人数），不再使用参会人召回率；任何比例都不自动授权。Agent 负责基于证据做敏感分类与授权判断，代码不增加关键词过滤或任意比例授权门槛。原投递键和已确认回执恢复机制保持不变，已发送历史消息不会因本修复自动重发或撤回。

会议消息新增 `message_title`，由 Agent 按实际接收受众核验：安全时可沿用会议原题，否则使用中性业务标题。正文首行与推送标题均只取此字段，不再自动拼接可能含人员方案信息的 `source.title`；没有已核验标题的持久化决策使用中性的“会议跟进”。原会议标题仍留在来源记录中用于追溯。

标题合同升级不会改写旧的已准备正文：旧决策未核验标题时采用中性标题；如已有未确认的准备正文与之不符，沿现有正文一致性检查停止，需通过原有重新决策流程生成新版本。已确认的精确回执仍按原动作核对，不重发以获取新标题。发布前需核对旧版已准备但未确认的会议动作。

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

The OA approval detail endpoint uses the same current-task-generation status projection as History detail for the newest Attempt. It returns the immutable persisted Attempt state separately as `recorded_status`, while older Attempts remain historical rows. This projection does not change the database or external receipts; uncertain current execution evidence is never upgraded to success.
Consumer and Audit both persist a new Codex session selected by the existing runtime retry router within the active run. After an idle timeout, a fresh Audit attempt may replace that run's current session just as Consumer does; each runtime attempt retains its original session, transcript slice and failure. A later Audit proposal revision continues the latest Audit-owned session from the same ReplyTask and execution generation, unless that latest run explicitly marks it noncontinuable. Audit never reads or writes the Consumer conversation session slot. Every revised Audit still receives the current Task, full candidate and revision binding and independently checks current evidence. This changes Audit session continuity only; it does not change route selection, retry budgets, candidate binding, review policy or external execution.

Attention list, summary and count readers follow a historical Attempt's original AgentRun task ID when the OA scanner has since changed the trigger message key. A current bound task in done/skipped no longer appears as an unresolved historical Attempt. Same-generation failed tasks remain visible, valid reviewed human decisions keep their independent projection, and the historical Attempt/run/receipt records are unchanged.


### Native-source scalar representation and State runtime

Native-source Email/Business/Configs/Skill/Sync and State storage encode finite REAL values as an exact binary significand and exponent and decode them with SQLite's built-in pow, preserving native TEXT/INTEGER affinity results. Scalar binding reads float and string subclasses through their base implementations and bytes through the buffer protocol, matching sqlite3's underlying bound value. Non-C-contiguous buffers retain native BufferError behavior. The shared encoder validates the underlying integer against SQLite's signed 64-bit range before serialization. Provider's four observation fields bind native scalar values directly with SQLite affinity, without a JSON codec. Published Reply retains its existing scalar format; frozen source fingerprints remain unchanged.

Store20/Email52 uses one State table for fifteen retained native state kinds. Fresh Store-first and Email-first construction creates this declaration; current callers use the existing State projections and transaction-owned write primitives. The paired release retires sixteen native State tables, including the eligible empty corpus source table. Original unused conversation fields are retired only when their existing source eligibility check permits it. Archived native factories and historical release source contracts remain unchanged.

The unpublished intermediate Store20/Email52 current path now uses
`business.import_task_id` only on `record_type='work_todo'` to refer to the
adopted formal Task public ID. Current constructors and strict predicates
require this owner column and reject `business_legacy_links` and
`trg_sync_legacy_update_public_id`; known-object repair does not recreate
either. The source WorkTodo projection omits the owner column, preserving
manifest and source digests across import. History-only Project/Update items
never query the mapping. The paired FK-off transaction captures and converts
the old link last, publishes both markers atomically, and restores the old
link first on inverse before earlier schema families. Supported populated
WorkTodo/Task links keep owner and target identities; link-local ID, time and
allocator are retired. Unsupported alternatives and dependencies refuse before
DDL. This remains an eleven-table intermediate candidate with the source
document body still present; no production publication is implied.
Apply checks each current source row and its supporting WorkUpdate by indexed
public-ID lookup inside the item transaction after the one corpus fingerprint.
Known-object repair never creates the retired Legacy table or index; their
native declarations exist only during fresh transient source construction.
Import manifests use version 3 and reject older versions before item decoding;
the old Project match registry key and zero count are absent from current
serialized plan and apply output.

State INSERT, point UPDATE, UPSERT and bulk UPDATE keep caller-owned transactions and do not commit or roll back earlier caller work. Omitted defaults and OR REPLACE substitutions for supplied NULL/NaN on defaulted NOT NULL fields execute inside the final write statement. Nullable NULL and defaultless NOT NULL failures retain native behavior. Bulk updates preserve one-statement affected-row counts, ABORT behavior, original-row expressions and untouched encoded leaves. SQLite CURRENT_TIMESTAMP remains statement-stable.

State UPSERT uses the existing source-key partial unique index in one INSERT. Original INSERT-candidate constraints run before a conflict update, including explicit NULL on a required wizard confirmer, even when an update guard is false. Matched rows keep their source identity and change only selected payload leaves. No failed SQL operation retries a different storage layout.

Organization name and open DingTalk ID indexes use the original source ROWID as the final key, preserving equal-key encounter order independently of State physical IDs. Current declaration checking sorts index records by name and omits only SQLite's index enumeration seq; SQL, ordered columns, uniqueness, origin, partial predicates, foreign keys and triggers remain exact. Source declarations and physical State DDL are unchanged. Current constructors retain only the existing two dispatcher-index repairs and reject unsupported namespace collisions before changing journal mode or DDL.

Current session, conversation, lock, route, wizard, scan/history cursor, reaction, health and deferral callers use State. Dispatcher ownership, generation, terminal/expiry predicates, cancellation fences and task eligibility retain their existing business rules. Empty dispatcher probes remain read-only; positive claims recheck inside the existing writer transaction.

Publication readers choose the native historical source or exact current State marker explicitly. A malformed current declaration never retries native. The seven generic current publication selectors require the complete current Actions/Skill receipt declaration; Email availability and action publication retain their existing Store/Email marker-pair contract. Store-only current databases do not advertise an Email runtime.

Paired forward migration captures State declarations only, converts State after Configs and writes the Store marker and Email ledger with the same existing timestamp in the original atomic transaction. Target verification reads the current State marker. Inverse restores State first from current facts, then preserves the existing Configs/Business/Email/Provider inverse sequence and original native marker/ledger restoration. It does not restore State rows from an earlier snapshot.

Upgrade progress selects the committed schema at DAO construction and immediately before native DAO progress writes. One read-only snapshot observes the retained Email ledger first: positive native versions through source51 select native service_state and target52 selects State; malformed or unsupported future ledger versions remain visible errors. Without a ledger version, Store-only State requires its exact current Store marker; missing paths and empty databases remain source without creating files or tables. Explicit getters/setters keep their original transactions and bounded lock/busy retry and do not reselect after SQL failure. Generic protocol stores are unchanged. Progress after a child commit or inverse follows the actual committed role, including a commit that becomes durable before an exception is reported.

OA Reply Attempt History rows and their status filters use the owning task's
current-generation projection, matching Attempt detail. If no run resolves, the
current task is resolved by channel, conversation and trigger message identity.
Legacy failed and blocked Attempt labels are reconciled to the matching task's
current terminal status; without an owning task, the Attempt keeps its own
failure or block status. Historical action stays in the action field. An
unresolved current System Execution remains `uncertain` even when the task was
later closed; History reads never rewrite the Attempt or execution receipt.
When the exact current candidate's Audit run and System Execution are linked
to the Attempt, a verified `done` execution resolves a stale
`external_action_uncertain` task error for History projection while the task
remains pending, processing or running. History then shows the live task state
without rewriting the stored Attempt or task error. An unresolved execution
remains `uncertain`. Other History types retain their own status projections.
The failed-only Reply Attempt key selection and page hydration both use this
same canonical projection, so OA legacy labels, pending tasks and unresolved
uncertainty are counted and displayed consistently.

听记管理页认证失效（2026-10-15）：组织选择或登录校验抛出 MinutesBrowserSessionExpired 时，服务启动本人普通 Google Chrome 并前置打开听记 history 登录页；登录页导航失败会转为该认证失效结果，组织选择及其后的控制台跳转等待各限 10 秒，超时同样触发可见接力。原命令仍保留失败，不把打开页面当成认证成功；后台浏览器照常关闭。本人完成登录后的既有 cookie 同步与正式重试流程不变。听记管理浏览器使用 Chrome 原生 `--restore-last-session` 恢复每日副本中的 session cookies，避免 Chrome 默认启动删除仍有效的后台 access cookie；首次使用新 profile 时先启动并关闭一次空白 Chrome，随后重新注入副本再正式启动，因为 Chrome 对新 profile 忽略该参数。启动后关闭恢复的旧标签，退出前把仍打开的页面导航到 about:blank，避免后续启动累积标签或重载旧听记 URL。该设置仅用于听记管理 profile，不改变 cookie 到期时间、权限申请规则或退订浏览器。

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

The Task Agent codec's existing sensitive-result rejection now gives a schema-valid
credential-like result a field-path-only correction hint in the same Agent session.
The value is never copied into the hint. Runtime-path guidance, rejection rules,
and the one-correction budget remain unchanged.

Reply declaration selectors accept an explicit optional Skill configuration receipt scaffold and forward it to the existing complete-shape recognizer. The default remains false for historical callers; current State Memory and generic publication readers select the complete current declaration.


Current database construction (Store2026-10-21.1/Email53): blank Store/Email construction installs the ten core tables, the Email ledger and the three Action Entity tables directly. Cold opens require the exact current declaration. Missing auxiliaries are errors; startup never repairs schema or backfills OA metadata. Historical source factories serve explicit migration/inverse tools only. Publication requires production-copy typed-fact comparison, current-data inverse, old-code cold read, and reforward proof; evidence is recorded in runtime-current-schema-validation.md.

Current Signal creation and reads retain exact source identity, locator, hash
and byte count without an original-body table. Semantic replay/import,
Project context, Attention and minutes owner backfill use the current provenance
projection rather than the historical source-document projection. Worker and
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


Email list search preserves newer locally typed text when its own debounced
query update reaches the URL. External query navigation and browser back/forward
continue to synchronize the input from the URL.

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
path rule only for that error. Acceptance polarity and target-signal transition
violations are reported at `task_decisions[i].acceptance_polarity` or
`task_decisions[i].acceptance_target_signal_id`, so correction feedback identifies
the exact field; the transition and evidence requirements are unchanged. The
initial Work Tracking Skill, strict result validation, native schema, routes and
correction budget are unchanged. Fixed-case evidence and limits are in
`docs/task-result-repair-prompt-validation.md`.

小时 Quality 的既有 Actions 声明校验使用最终 compact Tasks／Actions 发布对应的 FINAL_ACTION_OPTIONS；Memory 队列仍从现有 typed projection 读取，额外未知 Actions index 等不匹配声明继续报错。ASR 恢复、操作员跳过、终态 OA 和 needs-human 当前投影使用既有 typed DAO 写入，并保留上游原事务、状态条件和回执语义。

History 按明确 source_tables 构建查询时，仅在包含 actions 或未限制来源时检查 Email publication；非邮件分页与失败列表不读取被排除的 Email／State manifest。direct-email History 的 source ID 来自 common Action，输出 source table 为 actions；`email_action` 仍是用户可选的语义类型，状态、分页和详情路由保持原行为。邮件分支仍执行原有准确 schema 检查。

## Consumer purpose output 和有界权限等待

新 Consumer wire 用 `action`、`reason`、`target`、`payload`、`evidence_refs` 表达业务核心；`evidence_refs` 对每个结果都是必填字段，无可用引用时返回空数组，权限重试必须列出验证访问请求的引用；保留 summary、评分、阶段及长期记忆元数据。purpose 来自当前任务已选择的 OA Skill 或明确 OA 动作上下文，否则使用普通 purpose。schema 和解析器由同一 purpose 动作定义生成，拒绝未知动作、不完整参数及非法 ID 类型。`plan` 的 payload 是现有完整 ConsumerProposal；直接动作规范化为同样的提案。OA `return` 规范化为 dingtalk-oa/revert_task；它与 Audit 的 return 反馈分属不同协议。OA 请求参数 revert_action 是 REVERT_FOR_APPROVAL 或 REVERT_FOR_RESUBMIT，REDIRECT_PROCESS 是外部操作记录类型。候选追加服务绑定的 purpose_output；旧候选不重写。

OA `request_access` 是普通 Consumer purpose 动作，不进入 Audit。target 必须包含准确流程和文件 ID；同一任务同一文件的权限请求去重，不同文件可分别申请。服务经现有 OA 评论执行器提交只读权限请求，并在同一 `service_state` purpose 记录里持久化 action key、分发时间、状态及回执。恢复时依据该记录精确对账；原回执未确认前不重发。Consumer 请求阶段必须结束当前材料审核；评论送达不证明权限已恢复。只有回执验证成功后才开始权限等待。权限未恢复时 Consumer 输出 `retry`，引用本任务已核验的权限申请回执；retry 同样不进入 Audit、不产生外部写操作，只安排预算内的延后重读。解析准备阶段和编排层均从当前任务已核验的原权限请求核对它确为 request_access，不解析 reason 关键词。兼容迁移前缺少 purpose_output 的旧 OA 候选时，只接受本任务唯一且完全匹配 `material-read-access-block` identity 的已核验评论；其他评论、未知 key、uncertain 回执和其他任务的请求不能充当成功申请。

预算持久化在 service_state，key 绑定 business_object_key（无业务键时绑定 reply task）和 purpose。原已验证请求的动作 dispatch 时间固定 72 小时硬截止；模型不能指定时间、次数或重试间隔，新的 receipt 不能重置已存在预算。Consumer 的回执引用在匹配前只剥离一个已知 `receipt:` 或 `prior_receipt_id=` 标记，随后与已验证动作存储的原始 key 精确比较。Consumer request_access 执行并核验后先等待，再进入后续 Consumer；Consumer retry 按同一 purpose 的预算延后重读。最多启动 3 次后续 Consumer 重读，每次固定间隔 12 小时；旧版本已排队的等待首次加载时同步更新为 12 小时间隔，不清零原次数、reservation 或 deadline。worker/scheduled/email 延后时保留普通 attempt 预算，遵守该延时。

每次实际准备启动后续 Consumer 前用既有 SQLite 短事务预留一次，最多 3 次。所有被请求文件共享一个业务对象和 purpose 预算。key 中不含 native session、proposal revision 或 execution generation；更换它们、重启和任务 attempt 退款都不能清零。预约使用 task/generation/revision/turn 唯一键，重复检查同一预约不重复扣数；进程在预约后退出仍消耗该次预算。到达 3 次或 72 小时后不再刷新来源或启动新的文件重读。OA purpose 在预算耗尽或权限申请明确失败时，让 Consumer 再运行一轮提出普通 `return` 候选；即使恢复记录里没有可关联的已完成 Consumer run，也启动受限的 Consumer 退回轮次，再走 Audit。该业务对象只认领一次 OA 退回轮次；后续重复触发挂靠该轮对应的已完成 Consumer run，进入 `permission_blocked` / `blocked`，防止重复退回。若没有可关联的 Consumer run，保留非失败状态并转人工处理。退回轮次不得再次申请权限或安排重试，但提退回前必须对被阻塞文件实读一次：能读则按正常审批处理，仍读不了才提退回。该候选经 Audit、执行器和节点读回，服务不合成或直接执行退回；候选无效或未产生时才转人工处理，外部权限问题不标记为 `failed`。非 OA purpose 仍可返回 `permission_blocked`，Attempt 为 `blocked`，ReplyTask 为 `needs_human` 队列外等待。History 的一次人工重读入口保留原预算，只用于确认权限已开通；仍不可读时不继续重试，也不执行 OA 动作。

Purpose 等待在业务动作成功完成或经审核 no_action 后关闭活动调度；原次数和期限保留，显式重新等待同一回执也不清零。恢复旧候选时省略不存在的 purpose_output 元数据，保持原候选字节及摘要。字段、动作表、JSON 示例和计数边界见 [Consumer purpose 输出与权限重试](consumer-purpose-output.md)；发布证据见 [验证记录](consumer-purpose-actions-validation.md)。


## Dashboard execution projection (2026-10-20)

Pending scheduled triggers display the immutable run snapshot's task name. A claimed
trigger is identified as the scheduler dispatching a built-in command or handing
off a task, without presenting its configured runtime as an executing Agent/model.
Elapsed time starts at the persisted first dispatcher claim, and last update uses
the matching claim's renewal clock; enqueue time is not substituted for a start.
The execution column says 本次触发: dispatcher lease generations are not Agent retry
counts. Dispatched child tasks keep their own existing execution metadata.


/history now renders Dashboard: one Service status row, Failed / Active tasks /
Queued tabs, then the unchanged execution chart and History filters. Failed is
exactly the current Attention projection, including pending OA and persisted
human choices, with original states retained. Counts of problems and execution
tasks are independent. The initial successful read chooses Failed if nonempty,
otherwise Active; background refresh never overrides manual selection.

/api/console/dashboard/queues is a read-only metadata projection with active and
queued totals plus pagination. It reads current source counters, generations,
business-object ownership, leases and runtime model/role metadata without native
trajectory hydration. Queued includes initial work and source-scheduled retries;
Active includes current valid Task/runtime leases and current provider-action
processing. Expired/superseded execution records do not become live Agents.
If a deployed `runs` schema lacks the optional summary-input link, runtime
metadata omits that association while queue rows, counts and other available
lease metadata remain readable.
Retry ordinals exclude first execution and remain unknown where the source has
no execution counter; Audit content revisions are not retries. Source state,
retry/recovery policy, authorization and execution mechanisms do not change.


## Deployment admission and drain (2026-10-20)

`python -m app.deploy` and `--restart` close the database-adjacent admission
flock first, then acquire the drain flock exclusively after already admitted
claim transactions commit. New dispatcher, direct email reply/classifier/provider,
Workbench and meeting delivery/Memory claims take admission nonblocking, acquire
the shared drain flock, and release admission before starting SQLite work; the
shared drain flock spans commit. The short admission turnstile prevents later
claims from starving an exclusive waiter. A closed turnstile yields an empty
claim without changing the source. Existing tasks keep renewing and finishing.
Incoming work is still saved and pending/retry rows stay queued, so deployment
waits for executing work rather than all future work.

The exclusive scope lasts through the existing stopped-service backup, checkout,
build, verification, restart, health and rollback sequence. Success or every
exception/timeout/cancel path closes the descriptor and permits claims again;
process exit also releases flock automatically. No pause bit, new task status or
retry-policy change is introduced. Independent direct email reply/classifier, Workbench and weekly-OKR claims,
processing provider operations and meeting Memory leases count as in-flight even
before a native Agent starts. Processing Email actions are read through the
explicit publication source, including the current common Action status field;
a Store-only database without the Email migration ledger contributes zero Email
actions. Existing quiet timeout, backup and recovery rules remain in force.
Initial installation uses the prior normal quiet path because already running
old code has not loaded the new claim hooks.

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
