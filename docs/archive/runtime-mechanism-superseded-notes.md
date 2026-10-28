# runtime-mechanism.md — 已退役／历史章节归档

从 `docs/runtime-mechanism.md` 原样移出的章节，仅作溯源；当前合同以 `docs/runtime-mechanism.md` 为准。

### Historical Task2D0/Task2D10A/Task2D1 implementation notes (superseded)

The following task-phase notes preserve their original scoped implementation evidence. Statements that constructor activation, dispatcher integration, readers, or runtime cutover remained pending describe that historical checkpoint and are not current service status. The current native Task contract and deployment status are stated above.

Task2D0 将 Reply 的 queue peek/max/get、单条与批量 claim、TaskInput 列表及 pending
trigger refresh 改为直接读写 `tasks`／`task_inputs` 原生 owner。claim 在同一写事务内
递增 attempts 并绑定当前输入及版本；后续输入刷新只推进 current 指针，不改 held
claimed 指针。范围仅是这些既有 Store API，Reply 其余生命周期、recover、terminal、
dispatcher lease、构造器和 schema 选择仍待后续验收，当前不代表完整 Task runtime 已启用。

Task2D10A 将失败 Consumer/Audit turn retry、无 Agent Run/已发送回复的 pre-Agent retry、
native Codex auth failure 查询/恢复，以及普通与授权 defer 写入直接切到 tasks、
task_inputs 当前指针、原生 runs、reply Action 和 WeChat delivery owner。turn retry
只接受当前代最新失败 Consumer/Audit Run，并旋转 generation；pre-Agent retry 不旋转
generation。auth recovery 保留原 channel、当前输入、最新 Reply Attempt、认证分类、
90 秒锁超时及 Sent/运行 Run/WeChat effect fences；普通 defer 按原规则最多退款一次
attempt，授权 defer 不退款。两类 defer 只改原来负责的 lifecycle 字段。此单元不启用
constructor，也不完成 Reply requeue、Run closure、stale/orphan restart、dispatcher、
terminal settlement 或全局生命周期读取。

Task2D1 将 WorkSummary 的输入队列与 Task Agent Run 改为原生实体：输入 ID 是 `tasks.id`，
内容和来源摘要在 `work_summary_inputs.task_id`；重复来源写入先取得立即写事务再读 unique child，
并发调用返回同一 canonical Task ID。Run producer 使用原生专业 Run API，投影仍在
`run_projections`。pending 输入按现有 due 时间与 ID 顺序领取；attempts、失败/重试/容量延期、
done/skipped 压缩与 no-effect attempt 收尾遵循既有状态和事务边界。压缩清空 body 但保留
source-created time、hash、bytes、compact 标记、状态与错误。WorkSummary 专属 History 和修复
读取使用原生 Task/Run。通用 dispatcher claim、constructor activation、Meeting/OKR 与其余 family
reader、CLI/完整副本及发布验证仍未完成。`todo_evidence_candidate.work_summary_input_id` 的
新写入是 canonical Task ID；历史整数转换仍待最终协调器。
`app.web_api.dashboard._runtime_metadata` 的共享运行元数据查询仍依赖来源形状的
RuntimeAttempt/Run SQL 并同时服务 Reply、Meeting 和 WorkSummary；它需要后续全 family 切换，
当前不能将 dashboard WorkSummary 读路径视为原生或启用完整 runtime。

Task3E1 将 Consumer／Audit Run 的 Store 读取、领取、续租、session 更新、终态与未启动删除
直接落在原生 `runs` 行；候选与审核从 adopted result／review 读取，CLI transcript 和最终结果
仍按实际 `runtime_attempts.parent_id` 引用读取。终态完成与业务采用沿用同一 SQLite 事务，
Review business ID 与 Audit Run ID 分开。此孤立单元未启用 constructor 或 RuntimeAttempt writer，
不代表完整 runtime cutover。`fail_expired_agent_run` 只更新 Run 终态，不新增 failure 事件；
其他 recovery/requeue 的既有 failure-event producers 仍待各自迁移。

Task3E4 将实际 `AutoReplyStore` RuntimeAttempt 的 claim、start/session/effect、lease、
complete/adoption、fail、supersede、recovery 和历史读取直接切到 `runtime_attempts`；
`AgentRuntimeAttempt.parent_id` 是原生 Run ID，DTO 不再暴露来源名 `agent_run_id`，也不保存
空的结果 envelope 字段。完成时仍校验调用方传入的 envelope schema/大小，并只在原事务内
采用 Consumer 结果及 `result_ref`。只有 `workload_kind='agent_run'` 才使用 Agent Run lease、
终态与 adoption。Task Agent plain/decision-repair key 使用调用方传入的精确 professional Run ID，
并只检查该 Run 的 running 状态及 `task_agent_runs` provenance；不从 Task ID、latest Run、role
或 execution generation 推断父级。Structured OKR 在 native Task 为 processing 时即可领取，
CLI 前的 RuntimeAttempt 保持 parent NULL；后续 producer 必须将确切返回的 attempt ID 绑定到
验证成功后创建的 professional Run。Task/decision-repair 和 Meeting 操作即使绑定 professional
Run，也保留自己的调用 lease、ordinal、session 与失败状态，不终结父 Run。Meeting 按精确 Run ID
和自身 provenance 解析。Reply、全局 dashboard/History 及其他来源型生命周期读取仍有后续切换。
此本地候选未启用 constructor 或全 family runtime，Email、Workbench 生产者仍需各自完成原子
Task/DTO/workload-reader 转换。Task2D5 已切换 WeChat Memory import 的真实 Task creator、finish
writer 与两个 routed producer：`begin_wechat_memory_import_job` 返回 canonical `tasks.id`，并仅以
TEXT `import_run_id`、`account_id` 保存请求归因；原 `running/completed/failed` 状态及错误和时间
事实写入 `tasks`，完成时间写入 `task_results(result_kind='wechat_import').finished_at`。同一终态
重复完成不改时间，冲突终态拒绝。两个 producer 将该 Task ID 用作
`memory:wechat_memory_import_job:{task_id}` key；RuntimeAttempt 仍是 parent NULL 的独立 operation，
保留自己的 lease、ordinal、session 与终态，不创建 professional Run 或保存原始结果正文。此为
组装目标上的本地切片，不代表 constructor、完整读者、CLI/UI 或生产运行时已启用；其他 Memory
workload 分支保持原 owner，Email、Workbench 与共享 History/Dashboard 仍待后续协调。

Task2D3 将 OKR review 的原有 request creator/dedupe、claim、stale recovery、done/failed、item 写入与 Run history/detail 读改为 canonical Task、Run、RuntimeAttempt owner。顺序仍是 routed CLI result → normalization/validation → professional Run create → 精确绑定该次 result 返回的 `attempt_id` → domain items → Task done/render。无效结果不创建 Run；已完成的 CLI attempt 留在 parent NULL。绑定校验同一 OKR Task、`structured` workload key、completed attempt 和该 Task 所属的 `okr_review_runs` Run，只 CAS 写入 NULL parent，不按 session、最新 attempt 或旧 source key 猜测。Task 不存在的 attempts/due/locks/lease/generation 和 OKR Run 不存在的 status/error/role/generation 均保持 SQL NULL。Run detail 从 native Run ID/provenance 检索，尝试只从实际 `parent_id` 关系读取；孤立 Run 不推断旧 request ID 或补 Task。weekly OKR analysis 是独立 Task family。Task2D4 退休没有生产调用方的 begin/finish/cache-miss writer 和 DTO，保留 historical `okr_analysis` Task、TaskResult、`weekly_okr` workload facts；native parent-runnable 查询与 startup 两项 supersession cleanup 使用 Task 参数、lifecycle 和 TaskResult.finished_at，保持原过期租约、同经理替代周、技术错误类别及 created_at 条件。只读生产读数为183个 completed weekly Task、353个 terminal attempt、无 active weekly chain。全 family Dashboard queue/detail 仍由共享 Task20 source projection 驱动，属于未完成的共用 reader 依赖；本次不添加 partial projection 或 detail route。constructor、provider 与生产仍未启用。Task2D3 基于原 review BASE `360dfa1`，与已独立复核的 Task3E4 Fix2 `30426e7` 交错；具体命令、测试和依赖见 `task-2d3-okr-review-report.md`。

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
原生 Task/Run 组合目标中的 claim helper 直接以 `(tasks.kind, tasks.id)` CAS 当前租约，
并使用 canonical Task ID 更新对应 adapter 的被动 State 历史。Reply stale、无 Run 的
pre-Agent orphan、服务重启和终态 orphan Run recovery 读取原生 Task、Run 与
RuntimeAttempt；关闭服务中断的 Run 时把服务失败事实写入 `run_failures`。五类
Task-backed adapter 的 State 引用在显式 Task 转换边界通过类型化身份映射双向转换，
逆向保留 State 行 ID 和未知历史，含当前 State 编辑的两轮往返回归已覆盖。Task2D10C
已将触达的 Reply terminal settlement、error reconciliation、rerun、requeue 和相关读取
改为 native Task／TaskInput／Run／RuntimeAttempt／adopted-result／Action／Receipt owner；
这是仍未启用的候选路径。`replace_pending_single_chat_reply_task_trigger` 已以原生
Task／TaskInput／TaskBusinessKey 完成合并，WeChat 审核校验使用原生 Task、Run 与
delivery Action。共享 History／Attention、WeChat delivery 及其他 Reply 列表／详情读取仍使用来源投影。公共 Email
unsubscribe receipt reconciliation 仍受全局 manifest 可用性门槛约束；精确方法与验证边界
见 `task-reply-terminal-closure-report.md`。
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
删除事实不会从初始快照复活。Task-first composed inverse
与 handoff producer仍是 migration-only；没有激活运行时。

C2B2C 的 `restore_run_adoptions` 是未激活的 migration helper：在 Task inverse 与 Run
inverse 后，使用当前 native adopted rows、receipts、observers 和 identity map 重建旧
Actions 及其完整来源 catalog，不读取初始 TEMP 行快照。调用方负责 FK-off transaction
及 commit/rollback。正向 materializer 按来源与目标的实际 stored-column 差集检查保留行，
遇到 populated retired-only field 会在丢失前拒绝。完整 frozen copy 和旧 cold-writer 双周期仍属后续验收。

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

Task3（2026-10-21）将 reviewed、direct-email、prepared-message 与 Sent 执行恢复切换到同一 Action owner。调用方保留原事务、审批、provider 核验和不确定结果恢复边界；prepared 的准备、claim、接受回执仍在各自原有提交边界。prepared Action 在真实发送调用时才记录 provider operation；prepared facts 不借用 Sent 或 provider 时钟。审查执行在每个 execution/action_index 保留一个可更新的当前观察，旧执行的观察与 provider receipt 仍独立保留。direct-email retry 复用原 Action identity 并追加各次执行观察；retry request 是不可替换的追加事实，同一 Action identity 与 attempt number 的再次插入会被拒绝。Sent 历史缺失的 canonical operation、target、digest 或 action identity 保持 NULL；已核验的 failed-Audit 恢复可把这些事实绑定到原 provider body receipt，不复制正文、不重发。canonical provider 结果通过 reference-only metadata receipt 直接指向当前 immutable body；普通 provider receipt 不会仅因 ID 较新而改变该绑定。复杂 unsubscribe 保留原专有流程；这次切换不改变任何审批、授权、核验或重试规则。

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
同一不可变 plan 再次写入时，按 plan 与 operation 复用已有 Action owner；初始
`created_at` 是来源事实，不参与身份冲突判断，也不会因重放而改写。
Direct Email 的有序状态与回执投影继续按创建时间和原公开 source action ID 排列；
Action owner 改用物理整数 ID 后，查询需按同一稳定 source identity 维持原顺序。
Direct step 仍保留当前 status、next_attempt_at 和进行中 started_at；终态的开始／完成时间、provider
operation／target／result ID、error 只由最新的有序 attempt 持有，attempt_count 从
原 append-only ledger 计数。新 claim 的公开投影清空旧终态字段并显示本次 started_at，
不泄露上一轮 attempt 的结果。Email attempt 保留原整数公开 ID、物理 ID 和独立 AUTO
水位；Email provider 操作不生成 Consumer／Audit／AgentRun 或伪造 provider JSON。
attempt 写入先用 owner 原生 `started_at` 做 CAS，再追加 receipt 并复核 owner 的状态、
开始时间、重试时间与更新时间；NULL 不等同于投影空字符串。若追加期间观察到 owner 改动，
整个事务回滚，避免成功 receipt 与已被并发改动的 Action owner 一起提交。
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

Store `2026-10-25.1`／Email54 的 Reply OA 结果只在 Attempt 与同一 Run 祖先链、
operation、OA process/task identity 及 provider JSON 完全一致，且匹配唯一时，改为
通过 `reply_attempt_receipt_refs` 读取不可变 provider receipt；无匹配或多匹配仍读取
Attempt 本地结果。完成写入在同一事务核验 receipt ID 与正文、插入 FK 并清空重复正文。
History 和详情使用 receipt-aware Attempt 投影，receipt 退休先确认没有 Reply 引用。
显式 Store `2026-10-23.1`／Email54 发布迁移及 inverse 不保存业务行快照；inverse 从
当前引用恢复正文后移除关系、恢复原 marker。此处不改变执行、审核或发送政策。

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
旧 Project 只读修复规划器及专用解码器已退休；旧 Project／Todo／Update 历史行和
既有回执继续读取。D1a/D2 仅退休旧入口；当前 Task Agent、Task 写入及
task-semantic-import 的来源核对与正式采用条件保持。Store19.11 的 DDL 与字段迁移
按上文执行；这些子单元不代表整个 D 已完成。
Project CRM 搜索、确认和清除关联仍在原业务写事务中；搜索只存本地候选，
不替代已确认关联，也不写外部 CRM。

原 `business_source_documents` 正文表及数字容器 ID 退休。Signal DAO 仅持久化
来源身份、精确版本、hash／字节数及 locator。Store25.3 目标的每个 Signal 都引用既有
`provenance`，只保存引用与标题、context、dedupe_key、created_at 四项自身事实。首个
owner 即创建/复用来源，后续写入不推广或修改旧 Signal；parent/Signal 在原 savepoint
内原子写入，查询只走 parent join。旧17槽只属于显式25.2/54→25.3/54迁移和
current-data inverse，不进入运行时兼容分支。来源值/类型、Signal身份、业务关联、回执
及来源不可用语义不变。该目标尚待验收发布；基线生产25.2的两种表示仅是迁移源。
详见[字段归属和验证](signal-source-owner-unification.md)。
保留的历史正文退休工具只使用内部固定17槽 LEFT JOIN/COALESCE reader；当前运行时仍为
必需 parent 的5槽 INNER JOIN，不按旧结构回退。旧显式工具的来源正文核验、alias/clock、
新数据 inverse 和回执保留规则不变；历史测试 fixture 与当前 Signal fixture 分开。
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
事实重建。Run inverse 只重建仍有当前 owner 的 source-mapped 事实；已删除的 occurrence/cache
不会从 capture 中复活，仍被保留事实引用的缺失身份继续作为错误拒绝。原来源表只在转换事务中
暂存，最终 schema predicate 不允许旧容器残留。

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
The manual-feedback rerun check reads reopened-round evidence from the native
`feedback_processing_rounds` owner; reopened feedback
continues to count as unattended under the existing quality rule.

For a processing Reply, `processing_stale` requires a stale Task timestamp and
no recent progress in an active Consumer/Audit Run of that Task's current
execution generation or one of its active RuntimeAttempts. Appending a native
transcript event updates the Run timestamp, so an advancing transcript prevents
a false stale alert even if the Task row itself has not changed. A missing or
stale active execution record does not suppress the alert; progress from an
older generation or a terminal Run also does not suppress it. The quality
check remains read-only: it does not refresh a lease or rewrite a Task/Run
status.

RuntimeAttempt parent-existence checks resolve against the native `runs` owner, including provenance-backed professional Runs, and still require a matching Run ID. The completed-attempt final-Run check applies to `workload_kind=agent_run`, matching the source invariant that applied to non-NULL `agent_run_id`; it retains the `completed`, `failed`, or `unknown` parent-status set. Other workloads can point to Runs whose status describes a professional phase such as `ready_to_send`, rather than Agent execution completion. The Reply/Audit-only projection is not the owner for these cross-family parent relationships.

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
精简指纹；旧会话仍可通过完整 Pydantic wire schema 精确核验。Consumer 会话键同时绑定本轮实际提交的
Developer 指令摘要；新 Developer 与持久化会话合同不匹配时，不复用旧会话，本轮携完整事实和当前 Developer
开新 session。原生指纹后可以继续出现以 `## ` 开始的独立 Developer
段落；任意其他尾随文本仍视为格式无效，不能用来判定 session schema 兼容。
Work Profile 变化仅在已进入会话合同的相应字段变化时影响复用。Claude 每轮重新提交当前
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
Attempt History 仅对当前 `needs_human` 规则候选显示“需要你处理/选择处理方式”。普通技术重试耗尽、结果校验、候选绑定、Audit 或外部执行失败继续标为 `failed`，可提供重试和技术详情，但不要求用户判断业务规则，也不以“人工处理/暂不处理”冒充规则选择。OA 附件权限属于外部阻塞：按每 12 小时一次、最多 3 次且总计不超过 72 小时重读；预算耗尽后，若能绑定原 Consumer 结果，则启动一次禁止继续读文件的 Consumer 轮次，提出普通 OA `return` 候选并经 Audit、System Execution 和节点读回。无法绑定有效 OA 退回候选时保留为 `permission_blocked` / `needs_human` 等待处理，不把权限问题记为申请人或 OA 的 `failed`。
人工重跑内部 OA 待办使用持久化任务中的 `oa-pending:` 合成触发 ID；按触发 ID 定位当前任务并恢复其实际 `oa_pending_scan:<process>` 会话。该类 ID 不是钉钉消息 ID，不调用钉钉会话读取或按 ID 读取；若当前任务已不存在，按未找到任务失败。
同一来源 Attempt 和完全相同的审核反馈/修正文案修订，最多允许 3 次人工重跑（即原执行之外 3 次重试）。服务按已追加的不可变 TaskInput 修订记录计数；仍在排队的相同提交按幂等键复用，不增加次数。第 4 次在创建新执行代之前冲突退出，原任务代次和输入不变。来源 Attempt 或审核反馈/修正文案改变即为新的审核修订，独立使用 3 次预算。此计数与任务内自动重试分别管理；权限恢复后的延迟重读仍按每 12 小时一次、最长 72 小时及最多 3 次的既有专用预算执行。

`POST /api/console/history/tasks/{task_id}/retry` 是 Task 级技术恢复入口。它只读取当前失败代的最新 Consumer/Audit run，要求结构化错误明确为 `retryable=true`；唯一额外可重试的技术错误是 `dependency_read_unavailable`，此时在同一事务内清除 Conversation runtime sessions，避免在旧会话上下文中重复相同的来源失败。`retry_failed_reply_task` 原子校验 Task 代次、失败 run 和无效果来源记录；provider 风险拒绝、已留有 run provenance、活动/旧代或其他不可重试失败不入队。成功时沿用稳定 Task 与原输入，创建新执行代，保留旧 run/Attempt 历史；它不写终态、不绕过 Audit，也不直接调用 provider。
History 只允许对尚未解决、没有 OA 外部效果且身份匹配的跳过审批，根据新的明确反馈开启复审。Attempt 已同时保存 `resolved_at` 与 `resolution` 时，代表该历史审阅已被解决；即使本地任务状态仍是 `done` 或 `skipped`，详情页也不提供重跑，POST 重跑路由同样拒绝。钉钉终态读回和后续任务取代的闭合证据因此不会被旧本地任务状态重新打开；仍待处理的审批可由新证据或 Skill 版本变化触发新的扫描轮次。
压缩后的输入从任务所绑定的 OA URL/业务键核对 process 与 task 身份，并通过保存的定时扫描 run 恢复 OA Consumer 上下文后重新冻结当前所选 Skill。无法精确核对身份或找回 run/Skill 时拒绝排队；不会尝试把内部触发 ID 当作钉钉消息读取。
待办扫描的复审指纹包括当前选择的 Skill 正文摘要。Skill 规则更新时，即使外部审批流水未改变，也为仍在本人待办中的流程排入一轮新 Consumer/Audit；相同 Skill 正文继续去重。新一轮仍冻结实际选中的完整 Skill 正文，并使用原有审批身份、Audit、System Executor 和外部回执门禁。
组织架构变动的业务理由或受影响工作等可补充事实，由 people-review Skill 指示向已核实的发起人/责任人留言询问并保留 OA 待办；审批人拥有普通最终审批节点本身不构成保留给个人的 `needs_human` 特殊裁量。
组织架构同一实质、可补正事实缺口的证据、DWS 同意和退回记录按来源时间戳排序。仅将有当时可读且来源核验的证据支持、指出会改变结论的具体缺口、提供可执行补正要求并给予公平机会的退回计为有效轮次；随后必须核实同一缺口仍未解决。三次有效退回后，第四次到达 Derek 授权节点时，若当前可读且来源核验的证据仍证明同一缺口未解决，应拒绝并说明缺口及三轮有效补正机会，不得只按次数拒绝。读取/OCR/解析失败、无依据或不可执行的要求、不同缺口，以及被退回后材料已补正的缺口均不计数。审批评论中的具体可核实陈述是证据，必须审阅；不要求另行确认文件。若经核验身份与职责的接收主管在退回后直接以 OA 评论明确确认汇报或交接事实，应把该陈述作为其明示事实的证据；不得仅因没有独立附件/确认单就把该事实继续视为未解决或重复退回。若仍有具体实质事实缺少支持或存在矛盾，只追问该事实。只退回未解决或新出现的实质缺口，不重复已解决请求。具体审查标准见 people-review Skill。
历史 OA 复核必须按原审批或退回时点判断：只使用能证明当时已可获得的来源版本。之后才更新的表格或文件不能反推更早内容；除非版本历史或其他带时间的来源能还原，否则更早状态记为未知，不据此认定申请人当时缺材料，也不计作有效退回。
拒绝前还须把完整 DWS `REDIRECT_PROCESS` 时间线与本地执行回执及有效轮数逐条对账。已核实的 DWS 退回不能仅因本地缺少执行回执而遗漏；每条不计入的退回都要说明记录和证据理由。总退回数与有效轮数不同时必须解释差异；无法核实完整时间线或差异时，不得声称达到拒绝阈值。

Attention 对最新成功待办扫描仍报告为活动的 OA 流程继续显示 pending；本地 Consumer 已完成评论或退回时，默认详情仅说明扫描仍见流程未终结，不推断“没有退回回执”。具体回执以 History/System Execution 与 DWS 流水读回为准。
History 可对一条已有 uncertain OA 退回执行专用只读核验：调用方必须提供当前绑定的候选和 Audit review；Attempt 必须关联该 Audit run，Attempt 已保存的 OA 身份字段（若有）也必须与候选匹配；并校验唯一既存 uncertain action journal 及 uncertain execution 后，才进入 `reconciliation_only` 执行模式。此模式只调用已注册 OA handler 的严格 DWS 回读，不调用 dispatch；通过后将匹配回执写入外部动作账本并更新同一执行代际，原始 provider response 继续保留在核验证据中。证据不完整或冲突时维持 uncertain。若精确回执已持久化但同一代 Reply Task 仍因 `external_action_uncertain` 失败，且当前候选仍是该 Audit 批准的唯一动作、执行为 done、回执仍匹配，则同一只读入口将 Task 结清为 done；原 Attempt 的失败记录保留。它不创建 Consumer/Audit 新代际，不改变审批结论，也不重放动作。
退回读回只用与原任务、当前用户和准确意见匹配的 `REDIRECT_PROCESS` 记录判定唯一性。同秒的 `PROCESS_CC` 抄送不计入该退回的歧义判断；多个匹配退回或缺少目标任务证据仍保持 uncertain。该流程仍只读，不重放退回。
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

Attempt detail shows the current generation's reviewed decision, Audit summary,
execution receipts and Agent steps together. A subsequent OA approval therefore
shows its actual decision, reason, remark and verified receipt even when opened
through an older failed Attempt. That older run/error/resolution remains in the
historical section. An OA decision belongs to the exact reviewed process/task
action; without its canonical receipt, the page states that execution remains
unverified. Terminal-source closure JSON is rendered as a readable closure
explanation, never as evidence that an approval or a message send succeeded.
A Task that has already stopped as `failed` on `external_action_uncertain` or
`external_action_failed` still reads as failed there; only an execution that is
still open and uncertain reads as uncertain.
The projection does not write state or execute external actions.

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

After a verified append-task, DingTalk may keep the overall process RUNNING,
mark Derek's original task PAUSED, and create a new RUNNING task for the added
approver. A PAUSED task is not actionable by its prior owner: exclude it when
building current-user pending task IDs and remove its exact old key from
`pending_business_object_keys` after a complete, successful source read. If the
scan is partial or the source cannot be read, keep the prior key as an
unverified reminder until a later successful read confirms the task status.

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
then resumes the existing calendar-only tail. If the persisted decision carries
`_service_audit`, recovery verifies the approved result's candidate digest
binding before parsing the original business decision; an invalid or mismatched
envelope blocks restoration. The legacy unwrapped path remains available for
receipt-only historical recovery. It requires an unchanged failed,
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
An `unknown` judgement may return empty evidence only when the page has no
reliable sentence supporting a state; every explicit state still requires
nonblank copied page evidence. Unknown maps to the existing page-state-unknown
browser failure, records no success receipt, performs no click, and follows
the existing external retry/backoff policy. Approval, sandbox and unsubscribe
execution rules are unchanged. Native global memory and AGENTS instructions
remain CLI-owned and are not represented as service prompt text.
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
PPTX attachments have a separate 128 MiB compressed and 160 MiB expanded bound;
they do not inherit the 20 MiB spreadsheet limit. The initial read returns visible
slide text and indexes embedded images in reading order. A `page_number` read
returns one embedded image as a bounded JPEG with its original source digest and
slide number, so Consumer/Audit can send it directly to the configured OCR MCP.
This enlarged presentation path is limited to process-scoped OA form attachments.
Some OA image fields are represented directly as DingTalk static media URLs rather
than file IDs. Consumer and Audit can use `read_dingtalk_oa_form_image` for these
fields; the reader first verifies the exact URL is present in the live form values
for the supplied process instance, then applies the same origin, redirect, size,
OCR and temporary-file bounds as OA comment images. A URL from another process is
rejected before any network fetch.

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

### Historical Task/Run Phase 2 candidate notes (checkpoint through 2026-10-24; superseded)

The following phase-by-phase implementation and validation notes preserve their original evidence. Statements that a constructor was unactivated, production deployment had not occurred, or acceptance gates remained open describe that historical checkpoint only; consult the current Task/Run contract above and the progress table at the top of `business-model-execution-progress.md` for current status.


Task1 is complete at the independently reviewed local schema-contract scope, including later SQL artifact and Run parity repairs (`progress.md:92`); the candidate remains unactivated. Accepted bounded Task owners span Reply queue/input refresh and later WorkSummary, Meeting, OKR, weekly, WeChat, Email-classifier, and Workbench parent/attachment paths. Accepted Run work covers the professional Run/RuntimeAttempt owner, adoption/receipt and converter handoffs; Workbench Turn/event/artifact handling (Task2D7B/J) and Feedback reference conversion (Task2D8R) are accepted at bounded scope through `2980895e1`. Task2D8A implements native Feedback batch/item creation and reads, atomic claim, and the reached processing-lineage readers. Task2D8B moves association, evidence, typed iteration decision, resolve, reopen and reached validators to native Task status and Feedback item/batch/round/decision owners. Item status/timestamps are Task facts; current association/evidence are Feedback item facts; rounds, decisions and transitions remain domain facts. Scope receipt conversion maps only decision_id and Workbench task/turn leaves in evidence.associations, preserving independent attempt/run numbers, other JSON and current native edits across inverse/reforward. Existing receipt policy, legacy versions, capability checks and atomic Task/detail/round/batch/Action/event updates remain in force. The constructor is unactivated; backlog/UI list readers and complete Task2/3 caller cutover remain open. A configured-production read-only query at `2026-10-23T13:46:56Z` checked weekly counts and found no active weekly Tasks or attempts; it is not a full current production readback or release snapshot. No Phase 2 production write or deployment occurred. The frozen `source.sqlite3` read and orphan finding remain historical snapshot-scoped evidence, not a current production assertion.

In the unactivated Task1 target, retained Feedback decision-item Actions use direct native foreign keys to Feedback decisions, items and rounds. Six Task-identity-map discriminator triggers from the source-shaped Action declaration are omitted because new native Feedback owners have no migration identity rows. Runtime Feedback readers and writers remain outside this schema candidate. `feedback_processing_items.batch_id` remains required TEXT with its source lookup index but carries no batch-parent FK: the source has no such constraint, and empty or historical unresolved batch values remain valid source facts through forward/inverse conversion. This leaves the real Feedback batch, round and Action parent constraints intact.

Task2D8R completes the explicit Task/Run/adoption migration references for current Feedback facts. Decision, item and round Workbench Task/Turn references use typed Workbench public-owner maps; the item current-round pointer uses the typed round public identity and preserves NULL, zero and unresolved captured values. Adopted transition payloads translate only their two Workbench string references, with sparse inverse markers retaining unresolved numeric-looking strings. Current-fact inverse applies a captured marker only while the native field still equals that captured value, so current edits and claim resets survive inverse while untouched numeric-looking TEXT exceptions remain exact. If a current edit is lexically equal to its captured marker, the converter cannot distinguish it from an unchanged value and preserves the source marker. Decision-item decision/round columns continue through their existing adoption mapping once. Processing `attempt_id` and `agent_run_id` remain independent source caller values because no typed parent relationship is established. Task2D8A's bounded batch/item/claim and reached processing-lineage Store paths now use native owners, but remain unactivated with whole-phase cutover and review still pending.

The unactivated Task target's Meeting, OKR review and weekly analysis parameter identity indexes retain source TEXT-affinity equality. A tagged BLOB parameter is indexed as its decoded BLOB bytes, so an ordinary TEXT value containing the same marker JSON remains distinct. Numeric and infinity values admitted through the source writer are already TEXT by the time the task is captured. Existing composite key components, partial predicates, NULL uniqueness and duplicate rejection are unchanged; this is schema parity only and does not switch runtime lookup callers.

Task2B inverse metadata carries exact compressed per-kind source physical-ID ranges. The migration uses these ranges to distinguish an existing source owner from a new native owner whose reused ROWID lies below the original high-water mark. Counts and high-water marks cannot answer that membership question. The ranges contain identity facts only; native runtime callers do not consult them.

In Task2B's caller-owned migration, current/claimed input pointers are rebound on the remapped native Reply Task ID after typed input identities exist. The earlier update targeted the physical source ID and therefore missed a Reply Task moved to a different canonical ID, leaving stale public input IDs and failing inverse validation. The correction preserves current/claimed version values; the actual Task 201→1201 source-fixture regression passes through forward and inverse. No native runtime caller or constructor is activated by this helper change.

Task2D10B's candidate settled-input compactor reads and retires native Reply TaskInput bodies only for done/skipped Tasks after preserving the source version, running Run/RuntimeAttempt, current-generation delivery, Task-owned lease, failed-run settlement and unresolved-ReplyAttempt fences. The transaction retains every TaskInput identity and current/claimed pointer. When it compacts an Attempt body, its generated Task and matching-input references are native IDs; the Action adoption forward and inverse map only those two integer leaves through typed Task/Input identities. Unknown positive integer references carry content-free exception metadata so a numeric collision with a canonical owner does not retarget them. Hash, byte count, zero sentinel, unknown fields, malformed provenance, typed BLOB provenance and unrelated JSON values remain untouched. Focused assembled-store checks and a composed two-cycle migration exercise an actually compacted native Task created after forward conversion. The constructor remains unactivated, so this does not change the service's current runtime path.

Task3C1B locally converts the reviewed-candidate Store methods to unactivated native adopted tables. Candidate identity is the Consumer Run ID. A Review keeps its distinct source business/protocol `id` and its unique Audit Run owner; predecessor metadata and the immutable candidate wire field use the Review ID. Execution progress stores the actual Selection business ID and resolves the selected Audit owner through that Selection. Immediate existence FKs remain, with deferred composite Consumer/approved-Audit and Selection/Consumer/approved-Audit ownership checks at commit. The native result reader follows `runtime_attempts.parent_id` and the existing native transcript parser, including native Run transcript fields when no RuntimeAttempt exists. Candidate/review JSON remains unchanged. The native Store constructor, execution and receipt writers, source-copy conversion, and complete runtime cutover remain pending, so the published service continues to use its current committed runtime.

Task3C1C preserves historical adoption reads in the unactivated native Store slice. `adopted_candidate_for_consumer_run` and `get_review_candidate` use one neutral native row lookup and return the stored candidate after Task generation advancement or invalidation, including its original Run generation and immutable JSON/digest. The existing mutation context applies current-generation validation, and `current_reviewed_candidate` remains the current approved/actionable projection. Missing candidate IDs return `None`; no audit or action-eligibility policy changes.

Task3C2A adds an unactivated caller-transaction converter for current adoption rows, action receipts, and observer/retained-Action references. Capture validates the exact current Action and child catalogs before Task2B/Run3B conversion and retains source rows transaction-locally; forward conversion runs after both owner maps exist. Candidate Task ownership and typed observer references follow Task2B's physical identity by source kind and preserve valid sparse identities. Feedback public references use Task2B's complete typed public-owner index, so a same-number native row or a different owner's physical ID cannot stand in for a missing public owner. The assembled Actions target retires generic `task_id`; any non-NULL retained source value fails before rebuild rather than disappearing. Receipt `execution_id` resolves from embedded candidate progress to the Consumer Run at receipt granularity, while observer/action Run references retain their independent owner, including JSONB `action_data.agent_run_id`. Null and integer-zero weak Run sentinels survive unchanged; unresolved positive references fail. Selection branches are derived from the exact bound candidate option because the selected source has no stored `branch_json`. Any captured source Actions sequence watermark is restored exactly after obsolete rows leave Actions. Actual-source tests cover Task/Run remaps, populated selection and supplements, two Consumer/Audit pairs, typed Feedback mapping and collision rejection, rejection, and caller rollback. Current-fact inverse, constructor activation, and runtime cutover remain outside this step.

Task3C2B1 adds an unactivated, content-free identity planner for the adoption current-fact inverse. After Task2B then Run3B inverse, it resolves current Candidate, Review, Selection, Supplement and execution-alias owners from native rows and the actual Run handoff; retired mappings remain retired, and a Review's identity includes its Audit Run owner. It preserves raw source public-key values and SQLite types, while Candidate/alias next keys follow their per-kind `CAST(... AS INTEGER)` namespace. The global Action physical key follows ordinary `INTEGER PRIMARY KEY` ROWID allocation because the selected Actions catalog has no AUTOINCREMENT; retained current Actions win collisions and reconstructed adoption rows receive fresh physical IDs. Sequence facts are reported unchanged, including absence versus a stray captured row. This planner does not write adoption bodies, perform the inverse materialization, change runtime constructors, or establish old-code cold-open/reforward behavior; those remain separate C2B work.

Task3C2B2A adds an unactivated, read-only translator from current retained Actions rows to old-source stored-column rows after Task2B and Run3B inverse. It validates exact source and native Actions catalogs, preserves current Action IDs, SQLite values and payloads, and reconstructs the eight retired adoption-only stored columns as NULL. It remaps typed Run and Task references through the actual inverse owner maps, resolves Feedback decision/round references to the source Task public IDs, and translates only the JSON `action_data.agent_run_id` scalar while preserving other JSON content. Weak Run NULL and integer zero remain unchanged. The helper does not mutate Actions, sequences, receipts, observers or schema.

Task3C2B2B adds an unactivated in-memory builder for the five actual source adopted Action kinds after the Task and Run inverse handoffs. It reuses the adoption identity plan and reads current adopted rows and progress to return source-shaped stored-column dictionaries with physical Run/Action references and the exact native business JSON and timestamps. Current execution progress is represented by the paired Candidate fields and execution alias; retired children are absent. The builder does not write Actions or sequences, restore receipts/observers, rebuild DDL, or alter constructor/runtime selection. Its independent source-catalog oracle checks the old Actions CHECK and foreign-key contract only. Task3C2B3 later validates a bounded two-cycle inverse, genuine old DAO cold write, and fresh forward; full database-copy and runtime activation remain pending.

Task3C progress-selection parity stores `run_execution_progress.selection_id` as the actual Selection business ID. Immediate FKs retain source existence behavior, while deferred composite FKs reject Consumer/approved-Audit/Selection owner mismatch at commit and permit a temporary mismatch among existing rows inside the transaction. Forward conversion, current-fact inverse, and the current execution reader use that exact Selection identity. The schema/read path remains unactivated; no runtime claim writer or review policy changes.

Task3C2B3 adds a bounded offline validator for genuine published-old DAO cold writes. The runner pins a predecessor source root, executes old public candidate/selection/execution reads and a supplement write in a child process after inverse commit and close, then JSON-roundtrips only converter metadata/handoffs and performs a fresh forward. Two cycles preserve the newly added native Audit Review/Run, new supplement and retirement, and a colliding current Action with its own provider receipt lineage. This is offline schema-conversion evidence only; constructors remain unactivated and no provider or production action runs.

Task3E2 adds native professional Run create/claim/finish/get operations using native Run and Task IDs, deriving family from the native Task kind and keeping provenance and Task Agent projection in their typed owners. Task Agent transcript fields remain NULL; Meeting lifecycle/session/transcript/error fields remain source-present; OKR lifecycle/error/completion/update fields remain absent. Native child RuntimeAttempts are selected through actual Task/Meeting Run parents. Historical standalone OKR reads use exact session/range and create no parent relationship. The Codex-session Meeting history reader links its Meeting Run through native `runs.task_id`; optional provenance `job_id` is not the Task identity source. Task2D1 subsequently moves the WorkSummary producer, its History/recovery readers, and WorkSummary-specific project repair reads to native Task/Run IDs. Meeting/OKR producer DTOs and History readers, Meeting detail hydration, generic dispatcher/runtime writer activation, CLI behavior, and release verification remain open; this candidate is not live.

Task3D1 extends the unactivated `runtime_attempts.parent_id` contract to permit an optional Run FK for any non-`agent_run` workload. The actual forward conversion assigns this owner only when the source workload key resolves through the typed Task Agent or Meeting Run map: plain/`decision_repair.N` Task keys and Meeting keys. Task backfill business keys and other operation keys remain unowned. The inverse verifies that each native Task/Meeting key still has its exact derivable Run owner and refuses mismatched, missing, or otherwise unrepresentable non-Agent parent facts before changing the schema; it then restores source non-Agent `parent_id` as NULL and returns the exact source public identity in `workload_key`. Two portable inverse/reforward cycles preserve the relationship after Run ID remapping. Operation-attempt leases and the active-lease index follow `workload_kind<>'agent_run'`, independently of parent ownership; tuple/ordinal and active-route keys remain unchanged. The target DDL intentionally does not impose extra workload/Run-provenance policy on non-Agent parent links. The JSON Run handoff also carries `runtime_attempt_parent_edges`, keyed by source physical attempt and Run IDs. Only a structured attempt whose canonical request Task matches an `okr_review_runs` parent may use this edge; inverse derives it from current native provenance and Task ownership, while forward validates both source endpoints and applies the typed owner map. Other unsupported parent shapes still refuse before DDL. The structured OKR producer remains outside this slice: it creates its professional Run after the validated CLI result, so the subsequent producer cutover must carry that exact returned `attempt_id`. No constructor, runtime caller, producer order, or migration is activated here.

Task3E5 adds version-3 `runtime_attempt_orphan_workload_edges` to the portable
Run inverse/forward handoff. Inverse uses the actual Task inverse owner maps to
distinguish a missing Email Task from a live wrong-kind owner. A missing owner
is representable only for a parentless `email_classification` attempt; its
source operation key is deterministically allocated from the typed canonical
Task identity and follows the predecessor `email-classification:<64 lowercase
hex>` grammar. Forward checks each sorted edge against the exact source Attempt
ID, workload kind, operation key, and NULL parent, and rejects a live Task key
collision before rebuilding the source table. A validated edge restores the
unowned native key directly, without generic Task fallback. Attempt fields and
other workload mapping rules remain unchanged. This converter is local and
unactivated; the exact verification boundary is in
`task-3e5-email-orphan-handoff-report.md`.

Task2D2A adds a native-only Meeting delivery identity association through existing `task_business_keys`: the exact `meeting-alignment:{source public id}:{meeting_id}` value is stored against the canonical Meeting Task. The Task reader validates Meeting kind and exactly one associated key; it does not inspect the source identity map or reconstruct a key from the Task ID. Outbound Actions and their prepared payloads, target binding, receipt IDs and UUID5 input are untouched. Current inverse restores keys whose parent Task kind is `reply` to `business_object_tasks` and omits Meeting-owned rows, carrying their source physical Meeting ID and native key-row ID in portable `meeting_business_keys` metadata. Reforward rebuilds key text from current source facts. Inverse reserves Meeting key IDs in the restored global Tasks sequence; deleted source Meetings do not regain stale associations.

Task2D2B's local Meeting candidate stores request parameters and results in their native Task and TaskResult owners, with `tasks.id` as DTO identity. Analysis claims only due `waiting`/`pending`/`retry` Tasks; `ready_to_send` is a separate lane. A Meeting Task that is `waiting` for its settle time or parked in `retry` by a release or a retryable failure is claimed as soon as `eligible_at` and `available_at` have passed; before 2026-10-26 these two statuses were never claimed. The queue adapter's due and pending predicates compare the stored `status` (`pending`, plus `waiting`/`retry` for Meeting, plus `processing` for expired leases) so the planner seeks `idx_tasks_claim_due (kind, status)` instead of walking the terminal history. Delivery reservation/release/reset/retry use Task CAS and native Task IDs; delivery identity comes from the saved TaskBusinessKey. A substitute idempotent DWS interruption test verifies the same UUID on retry and no second effect. The Meeting replay gate (`reopen_meeting_alignment_job_for_replay`) refuses to reopen a Task whose delivery key, its `:sensitive` key or any `:target:` key already has a prepared outbound message (`OUTBOUND_POSTFIX_SQL`), whether or not it was sent; it is not limited to delivered receipts. Startup recovery closes native running Meeting Runs and their RuntimeAttempts, while retry/replay, baseline, ASR recovery, recurring targets, effect fences and terminal-input compaction use native Task/Run/Attempt parents and retain calendar-result ownership. Similar-Meeting search reads native Meeting Runs; the old source search-index writer is no longer called by the Meeting producer. HTML and API Run detail retain an orphan Run and its provenance when the native Task is unavailable, without source-ID rebinding; only an absent Run returns 404. The candidate remains unactivated. Shared generic dispatcher, all-family dashboard/History and broader cross-family search callers are separate pending dependencies; details and exact evidence are in `task-2d2b-meeting-runtime-report.md`.

That Meeting checkpoint also removed SQL interpolation from two unrelated `AutoReplyStore` queries: managed scheduled-Skill revision validation and stale Reply Run recovery. The f-string prefixes are restored, with SQLite regressions covering invalid cross-Skill revisions and stale-Run terminal failure/event behavior. This repairs those existing query paths without advancing the Reply lifecycle cutover or activating the assembled schema.

The composed Task-then-Run inverse passes the entire current Task inverse identity object through Task's shared validator and digest contract, including Workbench parent/attachment/file identities and allocation highwater. Run reconstruction consumes only the validated kind-qualified owner map and actual restored Task source rows. Unknown or malformed identity data, a mismatched digest, or unresolved/wrong-kind native Task references fail before Run schema replacement. This is local converter integration; the assembled constructor, runtime cutover and production behavior remain unselected.



The final shared-reader and publication candidate targets Store `2026-10-23.1` with Email53. The paired explicit release freezes the exact `2026-10-21.1` Store / Email53 source catalog (505 SQLite schema objects; catalog digest `920b7eedb6138439ab94d4fbf109e60258ce8842c212d3a2ee3eec56fb4ca290`), captures Task, Run and adoption sources before mutation, converts in one FK-off transaction, verifies the exact current manifest, and supports current-fact inverse plus re-forward. Task 5's local final-copy acceptance on 2026-10-24 passed the production-derived disposable-copy roundtrip, pinned predecessor cold read/write, native Luna/high Consumer/Audit/revision path, fake-effect process recovery, worker-disabled UI checks and same-source packed net-space gate. The inverse now retains the sparse Task identities/context needed by the next source forward. This is local acceptance, not production activation; whole-branch SPEC/QUALITY, normal main integration and deployment/readback remain required. See `.superpowers/sdd/2026-10-22-task-run-entity-unification/task-5-report.md` for exact evidence and limits. The production database was never modified.

Task-then-Run migration preserves captured source `sqlite_sequence` rowids, names, and water values. A native Run owner's colliding sequence rowid is relocated within the Run converter while its name and AUTOINCREMENT high-water stay intact; unrelated allocator rows are not renumbered. The converter applies the same rule during inverse restoration.

Task2D10C ports the reached Reply terminal writers and reconcilers to native Task and TaskInput facts, native Consumer/Audit Runs and their RuntimeAttempts, and existing adopted-result and Action/Receipt owners. Completion, failure, skip, stale-Run requeue, manual/source-revision rerun, and settlement retain their generation/status CAS, current-versus-claimed input version, attempt-floor/refund, latest-current-Run, exact business-key, delivery receipt, no-action, confirmation-required, needs-human and superseding-terminal-task checks. Stale-Run requeue filters native Runs to Consumer/Audit roles without professional provenance, closes only running rows of the expected current generation whose lease expired or update time is at least ten minutes old, appends native RunFailure facts, and leaves the generation unchanged. Finalization and memory-write event insertion use native Run/adopted-result identity and remain idempotent by Task/generation. The native Task's current TaskInput owns the current business-object key even when the Task has other historical key associations. This remains an unactivated candidate. The public Email unsubscribe receipt reconciler still requires global current-manifest availability; shared History/Attention/WeChat reads and full-copy/cold-process acceptance remain pending. Exact converted methods, checks and remaining source consumers are listed in `task-reply-terminal-closure-report.md`.

Task2D10D ports `replace_pending_single_chat_reply_task_trigger` to native Task, TaskInput and TaskBusinessKey owners for the DingTalk worker and WeChat producer. It preserves newest eligible pending single-chat selection, exact unchanged comparison, generation CAS, running Run supersession, stable duplicate Input/key identities, transaction-timestamped new BusinessKey rows, current-versus-claimed input ownership, the explicit post-merge input-version count, and duplicate Task retirement in one transaction. A retained Run reference that prevents duplicate Task deletion aborts the transaction. `clear_agent_run_session` matches the native Run Task/generation/role/revision/attempt identity and returns every matching row, including one whose session is already empty, through Run CAS. WeChat review approval and recording read and update delivery Actions using their public numeric Action identity, retain the ready-to-send status predicate, and bind the exact Audit/Consumer Run parent chain; reject propagation reads the conversation/message from the current native TaskInput. The candidate remains unactivated; broader WeChat delivery/list reads, shared History/Attention, full-copy acceptance, and current-manifest publication remain open.

The paired publication read selectors distinguish the exact frozen Store `2026-10-21.1` / Email53 source from Store `2026-10-23.1` / Email53. The predecessor uses its frozen Task/Run/scheduled declarations; the target uses native Task, Run, RuntimeAttempt, occurrence, adopted-candidate, external-result receipt, sent-reply and Email Action owners. Public sent-reply IDs remain the Action subtype identity. Deployment quiescence reads predecessor Workbench, OKR and Email-classification Tasks only before publication and native Task owners afterward; the direct Email action predicate remains on canonical `actions` on both releases. Current delivery-repair and rule-question-retirement commands use native owners and retain their established status, receipt and authority predicates. During adoption conversion, known `errors` rows for `work_summary_input` / `task_agent` translate only the canonical message-ID reference through the typed WorkSummary Task identity handoff, and inverse resolves it through the typed source owner. The candidate remains local and unactivated pending aggregate review and the separately recorded copy, cold-reader, recovery, UI and packed-size acceptance gates.

For this paired Task/Run candidate, the release writes its actual forward capture as strict portable JSON beside the active database before committing the schema marker. The file contains declarations, typed identity mappings, allocator waters and captured FK findings, not Task or Run bodies. Because it is adjacent to the active database, normal backup rotation can retire an older SQLite backup without removing the current inverse handoff. The explicit `scripts/inverse_task_run_entity.py` command loads that handoff in a cold process and applies the current-fact inverse. On success it atomically replaces the target handoff with a content-free source-identity continuation containing the native identity/context returned by the inverse. The next paired forward reads that continuation even when no backup path is supplied, validates its digest through the existing Task converter and writes the new current-target handoff; failed inverse leaves the current-target handoff available. This remains part of the unactivated candidate and is not production behavior until the complete acceptance and release gates pass.


End of historical Task/Run Phase 2 candidate notes. The following sections describe current service contracts unless they identify another dated historical checkpoint.

### Historical console adapter ownership — 2026-10-25

The console uses the dedicated `LegacyProjectDetail` adapter for retained legacy project details. It does not map the current Business Task list to old Project summaries or compute operational progress from historical Todos. Callerless Work Project/Todo batch-list Store methods are retired; exact historical project/todo getters and the source/fact/receipt detail path remain. This cleanup has no schema or data migration.


Native Reply input owner correction: the existing exact Consumer-direct Email
unsubscribe terminal operation reads channel and trigger JSON from the Task's
current TaskInput. Task lifecycle/generation remain on the Task. Its existing
status, generation, schema and lifecycle predicates are unchanged; the audited
lifecycle and unrelated channels remain ineligible for that operation.

Email observability reads completed Reply input identity facts from the retained
TaskInput provenance when its body has been compacted. In-flight inputs continue
to use their existing body. Classification/action filtering and exact account,
plan, Task, generation and Audit bindings use that same owner; malformed or
mismatched provenance does not expose a lineage. Input bodies are not restored.

The terminal unsubscribe snapshot uses the same retained input provenance after
compaction, including the original entry-reference bindings; Audit/owner/effect
validation is unchanged. Direct Email plan status maps use native integer
Action IDs, matching plan and historical-operation readers, so existing done
receipts are recognized by the original recovery flow.

Explicit Run-adoption conversion/inverse tools compare their historical
predecessor Actions against the existing frozen Signal-source declaration,
rather than the evolving runtime Actions declaration. The exact source catalog
and all row/type/identity/receipt/inverse checks remain required. These tools
are not constructor or runtime compatibility paths.

Scheduled Reply Attention uses the exact declared `CAST(execution_id AS INTEGER)`
expression to seek the existing schedule-occurrence expression index. SQLite
compares the CAST type token for expression-index matching; its casing is kept
identical to the declaration. Existing integer conversion, latest-dispatched
selection and Attention eligibility are unchanged.

Native Task due/metrics reads expose the physical status bound implied by the
existing pending-phase and processing branches. This lets the existing
kind/status/available-at index seek without scanning terminal history hidden
behind the normalized lifecycle CASE. The same pending-phase owner is reused;
all original source eligibility, time, lease and live-owner checks remain.

After reconciling parallel dispatcher fixes, Task-backed dispatcher projections
expose the original physical status directly. Pending eligibility uses the same
existing Meeting waiting/pending/retry phases (pending elsewhere), and expired
processing leases retain the same separate branch. The temporary normalized
status CASE and its redundant eligibility predicates are retired together;
no source phase or eligibility is reinterpreted. Email plan/operation readers
and their status-map lookups use the same native integer Action identity.

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


## 模型退休第二批：明确26.1→28.1发布（2026-10-28）

历史44c13bc91的Store28.1/Email54直接构造463对象（bare462）；运行时不读取旧schema catalog。
部署静默读取和paired release只接收精确26.1/54来源，旧25.2/25.3只保留必要
显式工具，不再自动分派。原安静等待、备份、账本、停止/重启、健康和回滚顺序不变。

模型切换仍由registry manifest记录，Configs空的并行切换家族和旧启动验证循环
已移除；promotion/category有效配置与immutable约束保留。Run map的唯一退休列
为NULL allocator_highwater；身份/provenance不退休。移除10个有准确替代约束的索引。
迁移检查空家族/NULL列后，在一个FK-off事务中原生复制Configs/Run map，重建
各自固定目标约束，恢复所有现场序列及rowid。inverse复制当前新增事实，恢复
原结构/markerclock；失败回滚而不补占位或删不一致记录。
当前UI/API含义、审查、重试、取消和发送政策不变。副本49表typed-fact验证、
真实旧代码冷启动及生产发布证据见[验收记录](model-retirement-batch2-validation.md)。

## 模型退休：无 DDL 入口清理（2026-10-28）

Reviewed/provider receipt 当前 helper 直接使用 `execution_run_id` 与正面字段合同，
没有旧列名参数、默认值或双结构写入。历史转换所需的前一结构 receipt writer
只在显式来源工具中，普通任务不调用它。
发布静默检查先做严格版本声明验证，再读取当前 Task/Run/RuntimeAttempt/调度
owner；已删除不可达旧表分支，静默等待与失败回滚含义不变。

调度测试通过实际 `ScheduledTaskQueueAdapter` 领取；source lease 和 dispatcher
claim ledger 的 CAS/代次/进程存活规则保留。测试显式控制进程存活事实，不用租约
是否到期推断进程已死。RuntimeAttempt 当前 running-once/恢复路径保留，测试
造数不再要求应用暴露旧 setter、周报单例租约或旧通用写入器。
本批不修改 schema、业务状态、重试、Audit 或发送规则。后续 schema 迁移另行
验证、回退演练和发布；本批部署状态见[验收记录](model-retirement-batch1-validation.md)。

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

历史 Project 的 HTML 与 JSON 继续共用 `load_project_task_detail`，原有详情链接、
来源、业务事实和回执保持可查。无调用的单条历史查询和旧跟进查询已退休；旧的
文本详情 renderer 与固定 score／match_reasons 不再构成并行读取模型。
Run、来源与投影事实使用现有 owner，原生结果使用精确 Run 身份的 native resolver；
仅测试使用的专业 Run 聚合读取器及 ReplyAttempt 列表／计数入口已删除。

当前 Email 计划由 `persist_email_plan` 写入，保持现有不可变计划、版本和幂等合同。
Reviewed 旧计划写入分支退出运行时代码；历史转换所需的旧候选造数能力只在测试
夹具中。Attention 读取不产生解决或成员变更。没有新增旧结构兼容、自修补、
审查／授权／重试政策或数据库迁移。发布与生产读回见
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

Task/Run Phase2 Task4 的隔离候选将 scheduled trigger CRUD、claim/lease、Dispatcher、
Dashboard、恢复和 reached History 查询直接放到 `schedule_occurrences`；冻结配置仍由
`configs` 持有，`snapshot_id` 继续引用 schedule snapshot 的公开 ID。完整 claim 索引
保留所有状态，dispatch 索引只包含 pending。Scheduled Tasks 页面只按服务返回的
`first_scheduled_for`、`scheduled_for`、`occurrence_count` 展示区间，不用通用摘要
计数推导可退休行。重启补派去重用 SQLite 时间函数比较聚合区间和新候选的真实 instant，
保留原始时间字符串，并由专用 partial expression index 支持 covered-range 查询。迁移前
安静窗口的旧 schema source 判定由 `RepositoryUpdater.current_publication_sources` 承担；
之后的 `consumer_system_release` 资产发布才读取显式冻结 `SCHEDULED_RUN_SQL`，该查询仍需
在后续发布批切换到目标 owner。
full/compacted TaskInput 和 Meeting source 的已声明 occurrence 引用在迁移时按
source-public/native owner 映射双向转换；只替换整数 token，保留其余 JSON 字符、原 sha256
与 bytes 证据。每个 service-command registry producer 使用单一 `ServiceCommandResult`；
完整 counters 仅在当前进程传递，未知、维护、暂停或不可用返回
`producer_counts=None, complete=False`，持久化 `result_summary` 不变。命令成功完成且
trigger dispatched 后，Store 只在当前完整 typed counters 全零、冻结 command/config 相同、
Cron 下一个时间点精确相邻、前一行摘要独立符合完整冻结历史零语法、且当前与前一 occurrence
均无已声明 Task/Meeting 引用时，在同一写事务保留较新 occurrence 身份并更新 first/count，
再删除较早行及其 identity mapping。前一 `occurrence_count>1` 本身不证明空跑。旧 Email
grammar 完整要求 `accounts/discovered/failures` 且均非负，只以 `discovered/failures` 判空；
旧 scan-meetings、Recovery 多计数和 cookie 维护不提供完整历史零证据。

Run forward migration 在引用 owner 映射成 native 后，同一迁移事务一次预建 protected
reference ID 集，再按真实 UTC 时间和 occurrence ID tie 顺序单遍扫描历史记录；只有完整冻结
历史零语法、同 Task/snapshot/command 且 Cron 连续的相邻行会退休。迁移返回 scanned、candidate、
excluded、retired 计数用于报告，不新增持久字段；时间无效时该 Task 不退休历史行。versioned
scheduler create 入口仍检查同冻结配置已覆盖的时间点，避免重启补派已聚合 tick。covered-range
查询按真实 instant 判断 mixed-offset 时间，并由专用 partial expression index 支持该范围。
安静窗口和 Consumer/Audit 资产发布按 database-release 先后使用各自 schema 读取；后者后续需
显式切到目标 owner。候选仍未激活。


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

Task2D7A 的 Workbench parent 候选以 `workbench_tasks.id` 原生 INTEGER 为唯一运行期父 ID，
现有 API/DTO 仍传递规范十进制字符串；不再以 Task UUID 作为运行时别名。列表游标的
更新时间相同时按原生父整数 ID 降序。最新 Turn 状态和 archive active fence 读取
`workbench_turns` 与 `tasks.status` 的实际 queued/running 值，归档仍保留父及 Turn。
附件继续属于 Workbench parent，使用原子文件写入与 SQLite 元数据提交顺序；UUID
`client_request_id` 仍是请求幂等键，附件整数 ID 与文件 `storage_path` 分离。重启协调器
按已保存的 locator 读取 managed attachment root 中的文件，不从 UUID 或整数路径组件反推
数据库 owner，也不移动迁移后的现有文件。真实旧 Workbench constructor 的逆迁新附件保留
由单独的文件协调验收负责；Turn 生命周期、constructor 启用、部署及线上读回仍未完成。
附件 native 整数 ID 使用 AUTOINCREMENT，且显式逆迁 handoff 携带 allocator highwater，
正向重迁恢复它；因此 reconciliation 删除最高附件后，新上传不会复用仍可能映射到来源
UUID 的旧 canonical ID。`test_native_reconciliation_preserves_target_injected_source_style_locator`
只在 native target 中手工插入来源样式 locator；真正的来源迁入覆盖由 Task2D7A API/迁移
回归与独立文件协调 operator 提供。

Task2D7I 的显式逆迁文件协调器在 caller-owned FK-off transaction 中，把新建或
locator 已变化的 native attachment 通过硬链接暂存到恢复后的来源 parent UUID / attachment
UUID 路径，并同步更新来源 payload。它在数据库提交并验证来源行 locator、文件 hash 和
size 后才删除旧 native locator；事务失败时来源字节保留，caller 可用 JSON handoff 清理
已暂存链接。正向重迁同样先暂存 native locator，提交后验证 native 行和文件，再删除来源
locator。该工具只使用准确 owner edge、来源 ID、managed root 和文件事实，不从路径推断
owner；它是显式迁移工具，不接入普通 Workbench 读写或启动 reconciliation，也未启用
旧 constructor。完整边界和 predecessor acceptance 记录在 `task-2d7i-workbench-file-inverse-report.md`。


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
principal's node is still pending. Keep the process in Attention only while its
current reply task remains `pending` or `processing` beyond the stall window.
After an audited comment or return is completed and verified, a still-pending
external OA process is waiting for the applicant or another approver; preserve
that real state in the normal queue and History without treating it as stalled
service work.

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
input dependencies can retire their original bytes (a direct Action is a
`record_type='action'` row with an email operation whose `action_status` is not
`done`/`skipped`; the original stays until the last one finishes); creating a plan's direct Action again (a repeated persist of the same plan) returns the existing owner and compares account, classification, operation and parameters, not the creation clock; the historical-operation execute and reconcile paths recognise a plan's finished direct Actions by Action id (native integer ids against `direct_action_statuses_for_plan`'s integer keys), so an Action the global worker already finished is not claimed again and the operation can terminalize; at worker startup a pending or processing Consumer-direct unsubscribe Task of the retired lifecycle is failed (`legacy_email_unsubscribe_lifecycle`) by reading its channel and trigger from the current TaskInput, so none is left as unresolved; the unsubscribe lineage readers (email detail, `get_email_unsubscribe_terminal_snapshot`, effect-Audit lineage validation) read a settled Task's trigger from its source facts (`input_provenance_json.source`) when the input is compacted, so a completed audited unsubscribe keeps its Consumer/Audit run ids, Task id and attempt ids; missing/changed sources keep
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

(2026-10-24: `scripts/retire_verified_email_inputs.py` was deleted with the historical schema release chain; it only accepted the Store 2026-10-19.3 / Email53 pair and could not run on the current pair.)
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
