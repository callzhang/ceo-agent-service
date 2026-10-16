# Tasks20 精确字段与关系映射

本文件把已经发布的8种 Tasks 和剩余12个来源表汇入同一张 `tasks` 的方向细化为字段清单。目标仍是10张核心表加1张 Email migration ledger；这里的20是记录类型数，不是新表数。当前是待落实为可执行DDL的映射，未选择运行时或迁移任何真实数据库。

逐字段机器清单：[2026-10-16-tasks20-field-map.json](superpowers/specs/2026-10-16-tasks20-field-map.json)。267项当前字段中，265项来自原已确认CSV；另外2项是已发布的 `current_input_id`、`claimed_input_id` 物理指针，原设计已要求只保存一份输入版本指针。它们不是本次新增业务字段。

## 存储结构

保留7个可写结构列：`id、record_type、legacy_key、payload_json、parent_id、current_input_id、claimed_input_id`。原有专用状态、时间、claim等标量只保存于各类型明确限定的payload字段；约束、claim、排序、唯一性和FK使用有声明亲和类型的生成列。原来没有的状态或时间不补默认业务值。现有8种的全局物理ID与public legacy_key不改，已有Actions/Runs/子记录的引用不重编号。

原 `work_summary_inputs.payload_json` 是结构JSON内的同名源字段叶子 `$.payload_json`，读取时恢复原字符串/BLOB；不能把外层结构JSON当成输入正文。已退休的Workbench final_text不重新入库。

新增12种中，5种自增来源的原整数id通过legacy_key保留，分配流独立；另外7种以原隐藏ROWID作为legacy_key，6个可空TEXT主键及历史队列的4列复合主键独立保留于payload并保持原唯一性。不得把NULL/BLOB主键强制转成字符串或把相同NULL键合并。现有8种legacy_key的可空规则不做全局收紧。

## 精确范围

| record_type | 当前字段数 | 来源 |
|---|---:|---|
| reply_tasks | 26 | 已发布Tasks8 |
| reply_task_inputs | 17 | 已发布Tasks8 |
| work_summary_inputs | 14 | 已发布Tasks8 |
| meeting_alignment_jobs | 23 | 已发布Tasks8 |
| okr_review_requests | 16 | 已发布Tasks8 |
| weekly_okr_analysis_jobs | 11 | 已发布Tasks8 |
| workbench_tasks | 7 | 已发布Tasks8 |
| workbench_turns | 19 | 已发布Tasks8 |
| business_object_tasks | 4 | 剩余独立来源表 |
| email_agent_classification_tasks | 14 | 剩余独立来源表 |
| email_historical_candidates | 13 | 剩余独立来源表 |
| feedback_iteration_decisions | 6 | 剩余独立来源表 |
| feedback_processing_batches | 6 | 剩余独立来源表 |
| feedback_processing_items | 16 | 剩余独立来源表 |
| feedback_processing_rounds | 23 | 剩余独立来源表 |
| oa_notification_events | 21 | 剩余独立来源表 |
| okr_review_items | 8 | 剩余独立来源表 |
| wechat_memory_import_jobs | 8 | 剩余独立来源表 |
| workbench_artifacts | 6 | 剩余独立来源表 |
| workbench_attachments | 9 | 剩余独立来源表 |

## 关系和当前映射

当前业务键映射以 `record_type=business_object_tasks` 的记录存放于Tasks，一条原映射对应一条记录，保留原键、独立创建/更新时间及parent_id→reply_tasks。实际只读汇总见 [映射事实](superpowers/specs/2026-10-16-tasks-business-map-facts.json)：7376条映射、3个多键父任务、13个键不同于父任务canonical key、68组历史重复reply key。合并待处理回复时，现有代码会把全部别名指向留下的任务；不能只在该任务保存一个键，或对所有历史reply key加唯一性。

附件→Workbench task、artifact→Workbench turn、映射→reply task这3个来源外键已经是物理整数；迁入parent_id继续保留现有类型守卫。原reply input→reply task和Workbench turn→Workbench task也保留既有parent_id语义。

决策→feedback batch的源外键是public batch_id。目标同时保留源batch_id叶子和parent_id物理端点，使用与Business一致的typed composite FK，继续禁止被引用的public batch key更新/删除；单独parent_id外键会弱化源约束。feedback item/round中的0和空串哨兵、OKR request_id和反馈中的Workbench/attempt/run坐标没有源外键的，仍沿用现有应用校验，不顺带新增FK。

仍留在Actions待合并清单中的feedback_iteration_decision_items有decision、item、round三条独立外键。过渡验证需要Tasks的3个类型专属public alias和完整唯一索引；SQLite外键不能引用partial unique index。最终它进入Actions后，各端点必须改为准确的物理Tasks外键；不能把这3个端点压成一个parent。

## 实施顺序和验收

1. 把267项映射编译成20种有限payload声明，生成确切getter、NOT NULL/CHECK、索引、FK和触发器；保留实际与fresh源布局差异供逆迁移使用。原生源oracle已434用例通过并经独立审查，不等于目标DDL已通过。
2. 逐组转换真实SQL读写：当前业务映射；邮件分类和历史队列；OA/OKR/WeChat；反馈batch/item/round/decision及关联子表；Workbench文件元数据和恢复。既有状态、事务、CAS、结果和回执不改变，不增加审批或安全策略。
3. 先在副本迁移，执行当前API写入，按当前事实逆迁移，使用真实旧代码读写，再向前迁移；核对原声明、分配水位、所有原行和精确新增行、FK基线及冷启动。
4. 副本上使用真实Codex CLI和对外发送替身覆盖创建、执行、审查退回/修订、失败恢复、业务结果和回执；再做受影响UI及最终普通表数验收。最后才进入已授权边界内的发布流程。

当前这一步没有执行上述目标迁移或生产发布，67表的已选候选也尚未达到最终11张普通表。


Unselected Tasks20 executable declaration: app/task20_definitions.py compiles20source kinds/267source fields into one Tasks table with7writable structural columns and constrained generated source getters. tests/test_task20_definitions.py passes325synthetic-memory cases including source-derived NOT NULL success controls, existing CHECKs/uniques, physical/public parent relationships, current mapping aliases/times, nullable/BLOB keys,3retained feedback FK endpoints and native decision REPLACE. No historical factory/default/constructor/currentcaller/allocator/migration/source retirement is selected by this addition; final10core+ledger remains open.
