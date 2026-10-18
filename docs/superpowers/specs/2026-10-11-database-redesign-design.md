# CEO Agent 数据库重构方案（已确认结构设计）

结构设计已获 Derek 的 OK 确认：business 命名、来源优先内联、归属直接 FK、仅共享来源使用 provenance、实际多对多事实进入 actions、可溯源原文不长期保存。实施状态按 `../plans/2026-10-11-database-redesign-program.md` 的最新记录：前段精简和 tasks/runs 已发布，actions 及其后合并仍在实施，完整十表目标尚未完成。普通成功轮询明细保留 30 天未确认，排除于当前实施范围。

证据基线：生产提交 `d171fb722380f551f1e71e6933a222dfce1a8aa7`；数据库 `~/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`。本轮观测 149 张表（含 FTS 虚拟表及影子表）、284 个索引、68 个触发器；逻辑数据库大小 413.60 MB。业务持续运行，行数会变化。没有写生产数据库、改运行代码、重启服务或运行生产测试。

## 1. 推荐及取舍

采用 10 张业务/运行表加 1 张 schema migration 表的目标结构：`tasks、runs、actions、business、emails、provenance、configs、provider_state、schedules、state`。provenance 仅用于真实共享的来源身份/版本，独有来源信息直接内联。这是完成所有阶段的设计目标；每阶段独立验证，不承诺一次切换，也不保证表数下降等比例降低空间。

三种方案对比：

| 方案 | 收益 | 代价 | 判断 |
|---|---|---|---|
| 仅清理原 149 表 | 风险较低，先处理确定重复 | 写入/读取和领域状态仍分散 | 作为第一阶段，不作为最终结构 |
| 共用生命周期 + typed action + 业务对象 | 计划、尝试、步骤、回执在 actions；队列/运行通用；来源唯一 | 要迁移现有 SQL、约束及 API，typed schema 必须严格 | 推荐 |
| 一个 agent_runs 或少数任意 JSON 大表 | 表名少 | 任务与运行、当前状态与不可变证据混在一起；恢复/唯一性难以查询约束 | 不采用 |

与之前 tasks/runs/actions/sources/changes/configs/projects/todos 方案相比：取消独立 changes；不将邮箱 provider 当前状态混入来源或配置；将 project/todo/attention 等实际业务快照放在 business 的明确类型内；保留独立 schedules 与高频 state。业务对象的逻辑差异仍存在，物理存储与 DAO 统一。不会新增臆想的 project deadline 或替不存在的字段设计迁移。

## 2. 当前业务的真实差异

* 任务是排队、版本、claim 和完成状态；逻辑 Agent run 是 Consumer/Audit 的 revision；runtime attempt 是某 provider/route 的一次执行。它们不能当成同一条记录。
* 定时 occurrence 是一次触发事实，不等于一次 Agent turn；普通轮询不能人为创建 task+agent run 两份历史。
* 方案、审核、选项、外部操作、执行尝试和 provider 回执是不同事实，但可在 actions 内以类型及关联统一存储。
* `sent_replies` 除发送正文还包含撤回/反馈的可变状态；不可变 provider 回执不能替代它。迁移后是 actions 不同类型。
* `work_updates` 是被采用的摘要与异构 changes_json，不是全量 before/after 数据库日志。其实际 action、todo_changes、follow_up_drafts 等字段原样保留，不虚构状态变化。
* 邮件 `normalized_text` 是可读正文；`model_text` 是训练/分类使用的脱敏文本。8,477 对行中只有 2 对正文相同。合表不等于删 model_text。
* 72,003 provider 状态中 66,240 条没有 message/classification 行。provider 状态必须能独立存在。
* 旧业务 975 projects、3,327 todos、5,468 updates 仍被 Tasks/检索/History 使用。当前语义导入器把这 9,770 行全部判为 history_only，关联表为零。迁移只改存储，不能按名字升格正式业务对象。
* 旧 TODO 的 3,325 个 deadline、875 个钉钉 link、2,477 个 follow-up 和 242 个 sync outbox 都是真实数据。新正式 TODO 表中 deadline 当前全空，不能反向当作旧日期无用的证据。旧 outbox 为 202 completed、40 skipped；follow-up 当前均终态，不重新排队。

## 3. 目标表与字段设计

### 通用物理表示

带多种记录类型的表有 `id INTEGER PRIMARY KEY`、`record_type TEXT`，以及确实适用的公用状态/时间/归属外键列。类型专用字段采用**显式、版本管理的 typed payload schema**；字段来自下面的旧列清单，不允许任意添加键。供 claim、排序、唯一约束和外键使用的值必须是实际列或受约束的生成列，不能隐藏在不可查询 JSON 中。

2026-10-18 最终物理表示修订：相同生产输入、两端同样VACUUM后，中间候选从487,759,872字节增至540,725,248字节，容量未通过。Actions/Tasks决定采用按真实record_type固定字段顺序的JSONB值向量，并把它们的类型物理存储由TEXT改为稳定INTEGER类型码 `_kind`，原有 `record_type` 是只读SQL生成列，提供原来的类型名称；逻辑类型名、每次尝试独立一行、业务字段、标量编码、公开身份和关系语义保持不变。这是对上述TEXT物理约定的明确修正，原因是同数据行打包实验减少54,681,600表页字节；不能据此宣称完整容量通过。字段顺序与类型码属于显式schema版本，来自现有字段声明；不得新增任意业务键、长期对象JSON兼容层或运行时新旧格式回退。类型只有 `_kind` 一份可写存储，生成名称不占行正文空间；这不是新增业务字段、第二份可写类型或新旧数据格式回退。保留SQL类型名称使已有查询和外键语义不必改写为魔法数字。其余表不为形式统一而更换表示。完整索引/约束、SQL读写、迁移后新增数据恢复以及同输入容量仍需一起验证。唯一主验收与详细数值见`../../database-redesign-final-acceptance.md`。

明确新增的结构字段：`record_type`、需要层级时的 `parent_id`、承载旧专用字段的 `payload_json`、保存旧主键以供历史定位的 `legacy_key`。各表需要引用的 `provenance_id/email_id/business_id/config_id/schedule_id` 是既有关系的新统一 FK，不是新增业务事实。只有实际发生的关系才写入。

旧 PK 不同空间存在相同数字。目标采用整数 ID；迁移工具生成确定映射并重写引用；保留旧类型与 legacy_key 的唯一定位。邮件合并以原 classification ID 优先作为目标 ID，message ID 做旧身份别名关系；只有 message 缓存而无分类的合法阶段仍能独立分配 ID。历史 URL 通过唯一映射定位，不靠 title 或时间匹配。

旧字段完整清单见 `2026-10-11-database-redesign-field-map.csv`（1,611 个字段）。其中 `payload.<旧字段>` 表示保持该列含义，不表示任意 JSON 框架；原类型、NOT NULL、CHECK、unique、FK 与 68 个触发器需要逐项分类：保留的业务语义译为类型约束；原文退休及表合并涉及的 body 必填/不可变规则、重复字段一致性检查同步删除或重写，不能照抄。真实原 DDL、FK、索引和触发器见 baseline JSON。CSV 是迁移方向清单，最终每阶段 SQL 必须细化 JSON 路径、生成列及关系约束后再执行。

| 表 | 职责 | 现有字段重点及目标处理 |
|---|---|---|
| tasks | 队列/工作台容器与 turn/领域任务 | status、available_at、attempts/attempt_count、generation/execution_generation、input_version、claimed_input_version、business_object_key、error、stop_requested、client_request_id、task_sequence；当前/claimed 输入版本指针只存一处；独有来源内联，共享版本直接 provenance FK |
| runs | 逻辑 run、provider attempt、定时 occurrence、运行状态/性能样本 | role、proposal_revision、turn_attempt、operation_id、workload_kind/key、route_name、runtime_kind、credential_mode、model、session_id、transcript_reference/start/end、failure_class/code、first_effect_started_at、lease、event_id、scheduled_for、dispatch_status、occurrence_count、snapshot_id；各类型各自约束 |
| actions | 已采用方案与业务结果、动作、审核、尝试、步骤、发送/记忆/同步回执 | candidate_digest/json、decision、review_id、selection_id、action_plan_id/version、action_identity、effect_digest、previous_effect_digest、action_type、parameters_json、sequence、operation、state、external_action_key、provider_result_json/result_digest、recall_status；计划→动作→尝试→步骤→回执是可关联记录，审查交叉关系另保留 |
| provenance | 需要多处共享/独立维护的来源身份与版本；不保存原文 | 从现有 source_type/ref/time、identity_key、hash/bytes/provenance 提取准确身份与版本；独有记录内联，无须创建来源行；不迁 body/context 原文副本 |
| emails | 邮件缓存与当前分类，一封邮件一条 canonical row | account_id、stable_message_identity、folder/uidvalidity/uid、sender/subject/preview、recipients_json、thread_identity/in_reply_to/references_json、attachment_metadata_json、normalized_text、model_text、category/predicted/confirmed、probabilities_json、confidence/margin、model_id/config_version、current_action_plan_id、included_in_model_id；允许 classification 尚未生成 |
| business | 业务对象当前状态，明确区分正式/候选/旧项目、TODO、attention、anchor、cluster/context revision | title、description、stage/status、commitment_status、formal_basis、business_relevance、owner_user_id/name/evidence、deadline_at（仅现有 TODO 类型）、goal/background/facts/current_state/blocker/next_step、registry_source、canonical_anchor_id、CRM、context_json/assessment_json；项目类型禁止凭空出现 deadline |
| configs | 账号/分类/Skill/路由与不可变配置版本、模型快照 metadata | config_version、config_json、category_key、threshold、revision_number、sha256、content（Skill 配置正文）、runtime_skill_bindings、snapshot_digest/manifest_json、seed、input_schema_version、model ID 与引用；凭据只沿用 secret_reference，不引入副本；训练全集/历史权重不入 DB |
| provider_state | 邮箱 provider 当前事实、folder observation generation | 原 account_id + stable_message_identity 主键、state、provider_folder_id/name、category_key、important/important_signals_json、observed_at；独立于是否分类，按类型唯一键 |
| schedules | 15 条现有定时定义 | cron_expression、timezone、runtime_id/options、working_directory、enabled、version、deleted_at、command/prompt、required_runtime_capabilities_json；不是普通 task，按版本 CAS 更新 |
| state | 游标、锁、会话绑定、dispatcher lease、组织资料缓存和水位 | scanner_name、cursor_json、last_success_at、account/folder/uidvalidity/last_seen_uid、route/session/contract_hash、owner/pid/generation/lease、key/value、updated_at；类型与精确 key 保证唯一，不追加普通心跳历史 |

### actions 的具体示例（仅引用现有字段）

同一封邮件分类后：

1. `plan` 记录原 email_action_plans 的 action_plan_id、version、category、actions_json 和参数/配置快照。
2. `operation` 记录原 email_actions 的 action_id、action_type、parameters_json、status、next_attempt_at，parent 指向 plan。
3. `attempt` 记录原 email_action_attempts 的 attempt_number、provider_operation/target/result_id、status/error 和时间，parent 指向 operation。
4. `step` 记录现有 unsubscribe 的 sequence/operation/state/reference，关联精确 action_identity + effect_digest。
5. `receipt` 记录精确 effect_digest、outcome、receipt_id/evidence/provider result；保留现有幂等键、应用校验及允许的精确 backfill。部分回执表没有禁止 UPDATE/DELETE 的数据库触发器，不能新增一刀切的不可变规则。

同一张表仍为多行。把所有历史 attempts 塞进一个不断 UPDATE 的数组，会丢失并发 claim、不可变回执与唯一约束，禁止采用。review/selection 不是单纯父子：保留 review→candidate、selection→candidate 与 approved review、execution→candidate/approved review/selection 的独立 typed 自引用 FK；parent_id 不能替代这些关系。selection 与 execution 对 candidate 的既有唯一约束也保留。278 个全局 external_action_results 中 219 个没有 candidate attempt，允许独立 receipt，不制造父节点。

actions 的唯一约束按类型保留：plan 的 (classification_id, action_plan_version)、email operation 的 (action_plan_id, action_type)、attempt 的 (action_id, attempt_number)、effect 的 (action_identity, effect_digest)、step 的现有 sequence 约束、全局 external_action_key。outbound_postfixes 的 (channel, delivery_key) 最终正文/feedback_token、native_reply_dispatches 同键一次 claim、outbound_postfix_receipts 同键回执是三个不同类型，不能用一个 sent 或 receipt 状态代替。保留原有 body 和 provider-first 写入顺序及幂等边界。sent delivery 的非空 external_action_key 保留原部分唯一索引；sent_reply_observers 的一条送达关联多个 run/task 迁到 actions 的 delivery_observer 类型，不能仅按 conversation/trigger 去重。


### 修订：来源内联、直接外键与必要的多条证据

本次按 Derek 的纠正取消通用 sources 和 relations/business_links。默认直接在拥有该信息的业务/动作/任务记录保存现有 source_type、source_ref、来源时间与校验元信息；只有多个记录需共享同一确切来源版本时，才通过 provenance_id（新增结构 FK）引用 provenance。provenance 不存文档、邮件、逐字稿、原始输入 JSON 或 Codex trajectory 正文。版本 identity/hash/byte length 是引用核对信息，不能拿它们替代真实可定位出处。

共享来源不靠题目、姓名、相同链接或 body hash 自动合并。相同 source_ref 下可能有不同版本、信号和引用范围；现有 733 signals 只涉及 96 个 type/ref 组合，但有 161 个 document 身份，不能直接合成 96 行。signal_id 的既有身份及其证据角色独立保留，context 中可重取的原文复制退休，采用的事实归 actions。

本轮只读关系复核：108 个任务有多条 signal；任务 evidence 中没有 signal 同时支持多个 task；7 个项目有多条 signal，3 个 signal 被多个项目使用；sent_reply_observers 当前没有多个 run 观察同一送达，但现有 schema 支持该关系。不能将真实多条 evidence 压成唯一外键，也不能仅凭当前零/单条样本取消代码已使用的关系。

| 现有结构 | 修订去向 | 精确约束/事实 |
|---|---|---|
| work_updates.source_type/source_ref | actions.update 上直接内联 | 来源不共享时不创建 provenance 行 |
| reply_task_inputs | tasks.input_version，当前任务直接引用版本 | 活动正文一份，完成且可溯源后退休；保留版本唯一键 |
| business_object_tasks | tasks 的当前 business_object_key 定位 | 不以跨 generation 的全表唯一键阻止修订；保留当前映射更新语义，历史 task key 可重复 |
| business_source_documents | 有真实共享需要的来源 identity/版本归 provenance，否则内联 | 不长期迁移 body；引用无法核实就标为迁移阻塞，不声称有可用出处 |
| business_task_signals | actions.signal_fact，直接来源字段或 provenance FK | 保留原 signal 身份、作者和已采用事实；去掉可重取 context 原文副本 |
| business_task_evidence | actions.task_evidence 行，直接 business_id + signal_action_id FK | 保留 (task, signal, evidence_role) 唯一性，角色不丢 |
| business_project_evidence | actions.project_evidence 行，直接 project/business + signal FK | 保留 (project, signal) 唯一性 |
| business_task_date_evidence | actions.date_fact，直接 task + signal FK | 日期值、日期类型、提供者是业务事实；可溯源原话不复制 |
| business_task_anchor_links / relations | actions.anchor_association / task_relation 行，端点直接 FK | 保留状态、证据、reason、不同 task/self-link 检查及复合唯一性 |
| attention→task / proposal-task / cluster-task | actions 对应明确关联类型行 | 保留各自角色与端点，不用任意关系字符串推断 |
| sent_reply_observers | actions.delivery_observer 行，直接 delivery + run + task FK | 保留 (sent_reply_id, agent_run_id) 唯一性与现有查询语义 |
| feedback_iteration_decision_items | actions.feedback_decision_item 行，直接 decision + item + round FK | 精确关联三者；不可只放一个 parent |
| business_legacy_links | 已核实归属直接引用及 legacy_key | 当前零行；不按名称或时间猜测旧对象身份，不另建泛化链接体系 |

actions 的关联类型是已有业务事实的一行，字段和唯一约束各自定义；不会新增一张任意 from_table/from_id/to_table/to_id 关系表。重要端点用真实 FK；专用类型可有 nullable 列，但 CHECK 要限制允许的列组合和被引用 record_type。单一归属的 task→run、operation→plan、attempt→operation、review→candidate 等仍放记录自身直接 FK，不另增关联行。

迁移时逐个移除旧 source_document_id 等被原文表绑死的读取接口与触发器，改为准确原文按源读取及现有身份/证据语义；不保留两套正文缓存，不声称只删 body 就能通过原来 body NOT NULL/不可变触发器。活动任务可以保留处理和恢复必需的一份输入，终态且无依赖后退休；这与 provenance 的长期记录是两类生命周期。

## 4. 取消、合并与保留

### 确定取消重复存储/废弃结构

* `email_classifications.action_plan_json`：每个 current plan 已有 canonical plan；读取从指针重建。必须同步修改启动验证和 API，不保留双写副本。
* reply_tasks 与 reply_task_inputs 的当前输入副本：只保留一个 tasks 输入版本记录，任务引用当前/claimed 版本。input revision 唯一约束和 compacted provenance 保留。
* DB 的 Agent 事件/审查工具正文、final_result/envelope 复制：使用原生 trajectory 的 session/path/行范围；被采用业务结果在 actions 保留。原生文件缺失显示不可用，不制造输入或自动 replay。
* `agent_run_events` 空表；`email_training_snapshot_observations` 空表和 SQL 全集写入；已废弃 `workbench_confirmations` 表/旧确认 API。入口行为仍用原生 CLI；不新增审批机制。
* `codex_session_search_fts` 及影子表，独立 `codex_session_search_index`：当前代码已仅存 session/source/title，正文和 embedding 均清空，搜索使用 native 文件临时构造 corpus。元信息归入 runs/tasks，搜索功能沿用该原生路径；取消独立表，不宣称现在仍存完整 FTS。
* `work_projects.memory_context_json` 的 query/memories 缓存：不再长期复制；采用的 goal/facts/current_state 等业务字段保留。唯一证据不能以“缓存”为由删除。
* 不再维护旧、新两套业务写入器；统一 business/actions API，记录直接来源/归属外键。旧数据为显式 legacy 类型，停止写入旧物理表；不凭名称把 legacy 变成 formal。

### 已提出的七项压缩全部纳入

| 候选 | 既往测量 | 目标处理 | 证据边界 |
|---|---:|---|---|
| provider 重复 lookup 索引 | 11.56 MB | 删除重复索引；保留 PK 及 folder 索引 | 本轮 EXPLAIN 点查使用 PK |
| 分类 action_plan_json | 12.52 MB | canonical actions plan + pointer | 本轮 8,455 非空指针无缺失 plan |
| reply 当前输入双份 | 6.14 MB | tasks 当前/claimed 指针引用唯一输入版本 | 原测量，需迁移前复测字节及读取方 |
| Skill loaded_json 重复 | 3.42 MB | configs 一份，加载 actions 回执引用 | 原测量 3,866 回执仅 16 内容；API 需重建兼容返回 |
| 终态 meeting source_json | 9.46 MB | verified source reference，保留业务结论/送达 | 原测量，不能假设所有来源当前可重新取得 |
| 终态 OKR source_json | 2.67 MB | 准确 source reference；原文可用性缺口明确记录 | 原测量，需具体版本/内容校验 |
| resolved errors.detail | 4.45 MB | 保留 kind/time/resolution，移除可从 native 找回的大正文 | 原测量，未解决详情保留 |

合计候选约 50.22 MB，并非已证明全部可回收，不同阶段也不能重复计算同一字段。VACUUM 前后实际文件、页/索引大小才是回收验收结果。

### 推荐的历史/缓存政策（本草案的产品取舍）

* 普通成功轮询、无效触发、性能样本：详细行保留最近 30 天，历史按 schedule/日期/状态计数及耗时统计保存；失败未解决、Agent 审查、业务结果、外部回执以及仍被引用行不纳入此删除。History 对退休详情明确显示“已汇总”，不能显示假的原明细。scheduled trigger 的去重边界/最新水位仍保留，旧事件不重新派发。
* 已完成邮件可读正文：撤回 30 天正文缓存建议；完成且无恢复/人工/未完成动作依赖后不再长期保存可准确溯源的原文，通过原 account/稳定身份读取。界面明确展示来源不可用。model_text 不属于 provenance；当前运行/训练的真实依赖单独核实，能准确重取则直接用来源，不为历史输入长期留副本。只保留当前/最新模型产物，不把历史训练全集重新塞进 DB。
* provider_state：保留 provider 当前完整库存；不可按“未分类/老日期”批量删除。已经明确不可用且无引用的记录可以退休，水位必须来自有效完整刷新。
* 已完成任务输入：立即退休可验证找回的正文，保留来源/hash/bytes/版本；不建原文归档兜底；来源不能准确重取时明确记录缺口，保留已有业务结果和实际定位信息，不能伪造可用出处。来源核对不通过的行在演练中单独列出，不直接删生产数据。
* Derek 于 2026-10-18 的最新明确答复批准一个限定例外：`2026-10-18-provenance-original-retirement-decision.json` 引用的 20 个历史来源版本（489,023 UTF-8 字节）允许在最终迁移中永久移除原文，保留来源定位、identity/hash/字节数、已采用业务事实及回执，并明确显示“原文不可用”。按准确 identity_key 与清单字节数核对范围，不以旧数字 ID 扩展到其他版本；这不是原文已找回的证明。仍须完成副本迁移和恢复演练，且不将原文转存到其他运行表或归档。
* 未发现当前写入点的旧 receipt/effect 不自动删除：214 effect intents 和 115 execution receipts 仍被 deferral 读取；迁入 actions。空但仍被现有路径使用的 selection/supplement/reply receipt 表迁移为类型，不把功能当作死代码删除。
* CRM 关联和正式任务来源约束保留。当前空字段不能证明没有未来/当前读取价值。本设计不取消它们。

仅普通成功轮询明细的 30 天政策仍为待审阅建议；已完成邮件正文不采用这项缓存期限。两项均未执行。

## 5. 迁移路线及验收

### 阶段 0：冻结映射与建立基线

在独立工作树按本草案生成每类型字段 schema、实际 CHECK/unique/FK/触发器映射和 SQL 调用清单。既有列到 typed payload/生成列/关系的路径必须确定；不同 record_type 的必填字段禁止用默认空 JSON 掩盖缺失。重新核对生产提交、DB 行数/大小、活跃 lease、来源可用性及历史 URL。最终 DDL 审阅通过后才进入迁移。

使用 SQLite backup API 生成一致数据库副本做演练，确认 quick_check/integrity/FK 及关键回执可读；只保留该流程最新已验证备份。native trajectory 只验证引用，绝不打包复制到新 DB。迁移 SQL 只迁存储，不调用外部 provider 或重新运行 Agent。

### 阶段 1：先移除确定重复

处理 action_plan_json、provider 索引、输入当前指针、Skill load 内容引用；native/FTS 已清空副本字段移除。每项先改读者、验证原返回值，再切单一写入路径；不建立长期双写或丢数据后的兼容 fallback。meeting/OKR/error 根据准确来源和引用状态逐条处理，不按表龄清空。

验收：plan 等价；输入当前/claimed 版本哈希与原 API 一致；load API 返回原内容但 DB 一份；队列查询 EXPLAIN 和列表耗时不劣于基线。

### 阶段 2：统一 tasks/runs 与直接来源

分别迁队列、逻辑 run、runtime attempt、scheduled occurrence 与工作台 turn。保留 operation_id、event_id、execution_generation、各类自然唯一键。task Agent repair suffix、OKR request ID、workbench UUID 原样解析，只经确定映射建立关系，禁止把 workload_key 一律 cast 成 integer。定时成功明细退休政策作为单独小变更验证，不能混入身份迁移。

验收：现有 pending/processing/needs_human 状态、代次、lease 及 revision 数一致；Consumer/Audit 与 runtime route 的关联完整；原生行范围定位一致；缺失 trajectory 的 UI 仍诚实显示不可用；调度 overlap/CAS/skipped occurrence_count 一致。存储迁移不覆盖旧 run，也不消费反馈周期。

### 阶段 3：统一 actions

先迁持久化 plan/candidate/review/receipt，并分别保留其实际插入/更新语义，再迁 operation/execution/claim/send/recall/sync 状态，最后切 recovery、History 和直接 SQL 读取方。精确 provider/idempotency 身份、effect hash chain、step 顺序、selection 审查绑定和旧 receipt/effect 全部保留。

验收：每个已 verified 操作仍可定位原回执；没有 candidate 的全局回执可读；uncertain OA comment 不变成 verified 或重发；WeChat 已开始效果/未完成 delivery 不重复发送；sent recall/feedback 状态相同；follow-up revision/idempotency_uuid 不变。迁移不改变当前授权/审核政策。

### 阶段 4：合并 emails 与业务 business 与实际证据关系

合并邮件共享 header/locator 后保留准确分类；原正文只在活动依赖期间临时保留，完成后按源读取。脱敏模型输入依当前真实运行依赖单独评估。message-only 中间状态必须可恢复。provider 状态独立迁移，66,240 条未分类库存不能丢。

旧项目/TODO 移为 legacy 类型保持可见与检索；业务 tasks/projects 按现有证据类型保留。links/follow-up/outbox 全迁后再删除旧物理表与旧写入路径。不执行自动 semantic import 升格：当前全部 history_only 没有映射依据。业务关联/external link 不能靠 title/姓名猜测。

验收：旧项目/TODO及截止日计数、钉钉 link、历史分页/详情与检索一致；正式阶段/commitment/来源约束一致；来源重取原文的 hash/身份与退休前一致，当前模型必需的准确输入有明确生命周期；分类更改仍正确失效 model inclusion；快照 metadata 被当前模型准确引用。

### 阶段 5：配置/运行水位与最终切换

迁 configs/schedules/state，再移除 9 个取消的物理表和退役原结构。调度定义并发更新 CAS、缓存/游标更新、组织资料查询保持原语义。持久化结构变化及缓存/历史政策同步更新 architecture.md/runtime-mechanism.md 和存储文档。

最终生产切换必须使用生产兼容的 deploy 路径，在 quiet/无活动 claim 窗口执行已演练事务；出现失败回滚**代码与数据库一起**，不能将旧代码启动在新 schema 上。只读比对失败则停止，不自动 replay。涉及新数据库文件切换时，生产部署脚本需先显式支持这一阶段流程；不能默认现 deploy 已支持整个新 schema。迁移后的外部发送窗口一旦打开，禁止用陈旧 DB 回滚遗漏新 receipt；需采用兼容逆迁或前向修复，避免重复副作用。

空间回收在迁移成功、回执/队列/页面验收后独立进行，在演练过的安静窗口 VACUUM 并测量文件与 WAL。重启后回读 PID、healthz、queues、Attention、History，再报告上线与空间回收。

## 6. 验证矩阵与交付标准

只在开发/演练副本测试，不在生产 checkout 全量 pytest。针对每阶段使用现有 test_agent_runtime_router、test_task_agent_session、test_meeting_alignment_store、test_okr_review、test_workbench_store/native_events、test_scheduled_task_store/recovery、test_agent_cron_scheduler/dispatcher_health、test_email_store/startup_validation、test_email_training_storage/web_api、test_reviewed_candidate_store、test_system_executor、unsubscribe、outbound_postfix、task_semantic_store/import、task_project_repair、History/Tasks/Attention API 测试及对应页面测试。

新增的迁移回归用例覆盖：跨表整数 ID 冲突；不同类型相同 status 的约束；cache-only email；不同 provider source key；有 parent 与无 parent 的 receipt；来源身份/版本及采用事实的既有不可变语义保留、回执既有精确 backfill 仍可执行；claim 崩溃重启；native 文件缺失；已发送 uncertain/recall 数据；全终态 outbox 不复活；正式来源约束与 legacy 类型；源字段 byte/hash 及无正文复制。

执行时先验证测试文件实际存在，并按变更选定精确路径；此文未声称测试已通过。

全部验收：没有无法解释的源字段丢失；旧→新 PK 与关联无缺失；业务结果/回执等价；关键列表/claim 查询性能达到基线；运行、API、页面与源读取均符合原行为及已批准保留政策；实际 DB/索引/WAL 体积报告明确。未跑演练前不承诺“410 MB 将降到某个值”。

## 7. 交付文件与下一步

* 本文：目标设计、真实字段示例、取舍和分期迁移路线。
* 2026-10-11-database-redesign-inventory.md：全部 149 表的行数、含索引占用与目标去向。
* 2026-10-11-database-redesign-field-map.csv：全部 1,611 个现有列的去向，区分来源字段、关系、native 引用及取消项。
* 2026-10-11-database-redesign-schema-baseline.json：当前实际 DDL/PK/FK/索引/触发器，不包含业务行正文。

下一步按分期计划落实精确 DDL、生成列/索引/触发器与迁移测试；CSV 是方向清单，不可直接执行。30 天轮询保留政策继续排除。执行基线更新为 36e42c17（Email schema 47），本文件中的测量值仍来自 d171fb72 的历史只读审计，执行前重新测量。


### Email 单次原文版本引用的实际字段补齐

当前 canonical emails 已有 provider 身份／定位与 message_normalized_text，但缺少原文字节版本的校验元信息。按本 spec 单次来源在拥有记录内联的规则，emails 增加一个可空 message_source_reference_json：仅含完整 SHA-256、UTF8 字节数、原生精确坐标或已核实的 provider 定位及既有正文序列化形式，不含正文。完整准确来源证明后才能将原 message_normalized_text 置空；未证明、pending feedback 或未完成任务／动作依赖仍保留。class_model_text、现有搜索和相似案例语义单独处理，不以此字段替代它们。当前数据逆迁在写事务外取得准确原文字节，按当前引用重新校验；旧 backup 只作为原文字节来源，不能覆盖新增业务行、当前状态或回执。不新建共享 provenance 行或长期兼容容器来承载一个所有者的来源。
