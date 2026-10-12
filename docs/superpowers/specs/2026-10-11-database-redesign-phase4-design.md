# Database Redesign Phase4: exact tasks/runs mapping

本文件是第四期分析交付与实施范围，尚未改运行代码、迁移或部署。总体 tasks/runs 方向已由 Derek 批准；本次把它落实到真实字段、身份和读取方，不新增业务事实。当前基线为 `f59a41d7a4f840ead657722985f949a602e41496`，生产 Store `2026-10-11.1`、Email `50`。元数据/行数是只读观察快照，会随业务变化；正式副本及发布前必须重新核对。第三期验收不是第四期验证。

## 1. 交付与覆盖

- `2026-10-11-database-redesign-phase4-field-map.csv`：319个实际字段逐列映射。258个来自拟处理的17张表（16张合表，搜索元数据表本期只删缓存列），10个属于拟退休agent_run_events，51个属于本期不合并的5张业务/附件表。
- `2026-10-11-database-redesign-phase4-inventory.json`：这些表真实PRAGMA列顺序/类型/NULL/default/PK、完整DDL、索引、触发器、已有FK及引用方、占用与退休列统计；没有原文、trajectory或业务正文。
- 本文：类型、身份、删除边界和准确约束。
- 分期计划：实施、数据库副本、schema逆迁与上线验证。

覆盖检查：CSV每个source_table/source_column与实际PRAGMA集合一一相等，319/319，无重复、无猜测源字段。旧字段SQL默认值不转换为新业务值。

## 2. 三种实施方式与选择

1. 推荐分成4A/4B/4C，最终仍为typed tasks/runs。每个发布单元一次切换自己的读写，分别验证副本与配对逆迁；不长期双写或提供正文fallback。好处是身份及回退可独立验证，代价是第四期需要多次发布。
2. 全部范围一次切换。统一最快，但会同时触及核心运行、多个领域、调度和工作台，任何遗漏都影响恢复和旧代码逆迁；不采用。
3. 只删空缓存、不合并表。实现较小，但不解决已经确认的生命周期存储碎片；删除缓存作为各单元中的一项，不能替代第四期。

## 3. 真实表归属

record_type直接使用原表名，便于明确原ID空间。它是已批准的结构字段，不是新增业务状态。

| 旧表 | 目标 | 快照行数 | 原字段数 | 特别保留 |
|---|---|---:|---:|---|
| reply_tasks | tasks[reply_tasks] |7263|26|代际、版本水位、current/claimed引用、队列自然键|
| reply_task_inputs | tasks[reply_task_inputs] |7791|17|准确版本、来源、终态精简、所属任务|
| work_summary_inputs | tasks[work_summary_inputs] |9084|14|来源自然键、原TEXT/BLOB、原hash/bytes/标记|
| meeting_alignment_jobs | tasks[meeting_alignment_jobs] |681|23|采用决定、来源、投递目标、正文与发送/日历回执|
| okr_review_requests | tasks[okr_review_requests] |20|16|消息身份、季度、准确来源及完成session|
| weekly_okr_analysis_jobs | tasks[weekly_okr_analysis_jobs] |169|11|成员/周末/digest自然键、缓存及租约|
| workbench_tasks | tasks[workbench_tasks] |20|7|UUID、可修改标题、archive与当前provider session|
| workbench_turns | tasks[workbench_turns] |37|19|UUID、request去重、顺序、输入、stop、claim UUID|
| agent_runs | runs[agent_runs] |21628|20|Consumer/Audit、revision/turn、parent及lease|
| agent_runtime_attempts | runs[agent_runtime_attempts] |22754|31|provider尝试、路由、会话、失败、lineage及原生范围|
| agent_run_state_events | runs[agent_run_state_events] |1151|5|服务状态/失败/恢复事件，非原生过程副本|
| scheduled_task_runs | runs[scheduled_task_runs] |153867|17|触发、dispatch、overlap计数、原execution身份与snapshot FK|
| meeting_alignment_runs | runs[meeting_alignment_runs] |2527|13|分析状态、job关系、准确原生范围|
| task_agent_runs | runs[task_agent_runs] |10718|12|input关系、状态、错误及projection_json业务应用回执|
| okr_review_runs | runs[okr_review_runs] |22|9|request/session/准确范围/创建时间；没有历史status|
| workbench_events | runs[workbench_events] |810|6|服务回放ID、turn顺序、控制事件和native ordinal|
| codex_session_search_index | 本期留原表；最终runs（第8期） |663|12|session/source/title；保留原生内存检索|

tasks候选原表页合计48,447,488B，runs候选原表页合计53,882,880B，不含索引；这些是会被迁移的存储，不是预计可删除空间。每行保留独立record_type，不能用latest provider状态覆盖逻辑run状态。scheduled trigger不是Agent turn；service command仍不制造synthetic任务/Agent运行。

## 4. 目标字段规则（逐列以CSV为准）

只增加此前批准的结构：`id INTEGER PRIMARY KEY`、`record_type`、`legacy_key`、必要`parent_id`、承载既有专用字段的`payload_json`；关系列来自真实旧字段。`UNIQUE(record_type,legacy_key)`保留不同旧表的独立身份。

- claim、状态、排序、自然唯一键、FK、恢复和运行定位字段保留实际列。CSV目标为tasks.xxx/runs.xxx的字段必须进入DDL或有等价受约束索引表达式，不能让队列扫描JSON正文。channel/conversation/trigger/input_revision、source_type/ref、meeting_id、week_end/manager/digest等自然键按类型保留。
- 只用于该类型展示/采用结果的旧专用列进入显式typed payload；键名就是旧列名，不允许任意新增键。旧JSON TEXT作为原值字符串保留，不能parse再format而改变原有原文摘要、序列化形式或回执值。
- WSI的`tasks.payload_json`直接保持原TEXT/BLOB及WSI1字节，不包裹到外层JSON，不base64、不重压缩；其13个其他实际列保存来源和生命周期。existing codec边界不变。
- workbench_events的`runs.payload_json`保持原最小服务/定位JSON；sequence/event_type/task关联为列，不以native重新生成服务事件。
- 其他record_type的payload_json才是该类型专用字段的显式对象；不能使用同一无约束schema解释所有类型。
- codex_session_id等旧名字映到session_id；旧range名字映到transcript_start/end。DTO保留原属性与选择规则。finished_at与completed_at分别保留源事实，不从一者补造另一者。
- OKR run没有status/error/finished_at、Workbench没有proposal_revision，源记录缺失字段在共享列为空。不能根据request done填completed，也不能从创建时间猜完成时间。
- 不新增adopted_result_json/tool_events数据库列：它们是现有DTO从review_candidates/candidate_reviews或native动态投影的属性。
- 共用状态列没有统一状态枚举。每个类型保留自身原枚举、NULL/default、唯一性与CAS，不把meeting ready_to_send/sent或workbench stopped改为通用completed。

## 5. ID、FK和外部身份

所有public DAO继续返回原数字ID/UUID；内部统一PK经确定映射分配。既有URL、source_ref、runtime workload_key、dispatcher source_id、delivery key、反馈绑定、native原始输出中的旧ID保持原值。只重映射SQL内部真实FK，不重写原生JSON或外部回执。

关键对应：

| 旧关系 | 新结构关系 |
|---|---|
| reply input.reply_task_id | tasks.parent_id -> tasks[reply_tasks] |
| reply current/claimed_input_id | tasks.current/claimed_input_id -> tasks[reply_task_inputs]，保持同任务归属|
| workbench turn.task_id | tasks.parent_id -> tasks[workbench_tasks]，public UUID不变|
| agent run.reply_task_id | runs.task_id -> tasks[reply_tasks] |
| agent run.parent_agent_run_id | runs.parent_id -> runs[agent_runs]，role/generation/revision关系不变|
| runtime attempt.agent_run_id / state event.agent_run_id | runs.parent_id -> runs[agent_runs] |
| meeting run.job_id | runs.task_id -> tasks[meeting_alignment_jobs] |
| task run.summary_input_id | runs.task_id -> tasks[work_summary_inputs]，既有代码关系明确|
| OKR run.request_id | runs.task_id -> tasks[okr_review_requests]，原多run关系保留|
| workbench event.turn_id | runs.task_id -> tasks[workbench_turns] |
| scheduled trigger.scheduled_task_id/snapshot_id | 原scheduled_tasks/config_versions FK不变|
| scheduled execution_kind/execution_id | reply_task时经旧任务映射定位；service_command时仍为命令文本|

源码：app/store.py:13042-13283、app/native_standalone.py:274-335、app/workbench/service_runtime.py:177。

`workload_key`不能统一CAST：agent_run数字是逻辑run，meeting数字是meeting run，structured数字是OKR request；task普通/decision_repair.N是Task run，memory_backfill是work_projects，deadline_backfill是work_todos；weekly_okr为日期/成员/digest及可选job/owner复合；email和memory各有真实字符串身份；workbench是UUID。留在其他期的对象继续用其准确现存定位，不伪造统一task parent。

Meeting发送键`meeting-alignment:{OLD job.id}:{meeting_id}[:sensitive]`原样保留（app/meeting_alignment.py:1684、app/store.py:19888-19913）。Workbench turn UUID、claim execution_run_id UUID、provider session、整数attempt ID不同；event.id是服务SSE游标，sequence是turn内顺序，native_ordinal是原生完成顺序，不能合并。

统一自增种子必须高于全部相关源sqlite_sequence及旧整数public IDs；旧记录legacy_key保留原值，新整数public身份可取超过所有旧namespace高水位的全局分配值。UUID仍原生生成。逆迁恢复源序列，包含replacement期间新增public IDs，不能倒退或重复。旧类型内ORDER BY id、分页和event cursor按legacy数值顺序保持，不能改用跨类型物理顺序。

已有动作/回执表不提前合并，但其实际FK需要同步重建：agent_effect_intents、agent_execution_receipts、candidate_reviews、external_action_results、review_candidates、sent_reply_observers及reply/business/memory/WeChat关联。workbench附件/工件/旧确认亦只映射原parent FK，ON DELETE行为保留。无声明FK的旧关系在CSV标明，按真实代码和数据验证，不将bootstrap模板中不存在于生产的约束冒充原约束。

## 6. 删除清单

没有可以对当前运行库直接DROP且无需改读写的列。以下17列是在读取方、写入、终态比较、schema/bootstrap、maintenance、脚本和测试同时切换后取消的物理缓存；同名DTO/native投影仍保留。

| 表 | 删除列 | 快照非占位行 |
|---|---|---:|
| agent_runs | final_result_json |0|
| agent_runtime_attempts | result_envelope_json |0|
| meeting_alignment_runs | decision_json,audit_tool_events_json,audit_summary |0|
| task_agent_runs | decision_json,audit_summary,memory_recall_used |0；memory flag现写0/从native派生|
| okr_review_runs | envelope_json,audit_tool_events_json,audit_summary |0|
| workbench_turns | final_text |0|
| codex_session_search_index | summary_text,fts_text,embedding_json,embedding_model,embedding_updated_at |0|

文本占位合计只有31,632B，不能承诺删这些缓存就节省几十MB。应取消的是冗余结构与无效写入。正式副本再次验证精确占位；历史audit_summary有错误写入路径，不能在发现唯一诊断时直接丢弃。

`agent_run_events`快照0行，原生/RAM已是唯一过程来源；去掉其migration/inspection/maintenance SQL后退休整表。`agent_run_state_events`有1,151条真实服务状态事件，迁runs保留。

持久FTS约40KiB，运行检索已使用内存FTS，但旧upsert/maintenance仍触及它。external-content计数663不等于实际docsize654；不能用title重建宣称精确逆迁。因此本期明确保留metadata表的7个实际元字段及旧FTS/content关联，只取消5个空缓存列；metadata合并到runs和FTS/shadow物理退休放第8期一起做。CSV的release_unit明确区分4C缓存取消和第8期元数据合表，不存在长期双写或指向已删除content table的悬空schema。

明确不删：result_schema_id、first_effect_started_at、session/range/reference、structured_error_json、state事件、Task projection_json、Meeting job采用decison/来源/发送/日历回执、OKR items、workbench user_text/resume_context/error_detail/stop/控制事件/附件/工件/旧确认结果。公共error_detail有真实服务解释，即使本快照为空也不能根据空值删。WSI终态/活动输入规则不变。

## 7. 实际约束及基线异常

逻辑run自然唯一键task+generation+role+revision+turn；Consumer/Audit parent规则和lease接管不变。provider UNIQUE workload_kind/key/attempt_number、同route活动partial unique、fresh/resume、correction lineage及7个真实trigger不变。新表只是改存储引用，不扩展授权/审查/重试/停止策略。

领域与工作台的active partial unique、自然键、claim排序、dispatcher owner/generation/expiry、weekly缓存及lease、trigger scheduled/manual区别、overlap聚合的first/last/count及版本CAS全部保留。scheduled的snapshot_json已在此前迁为snapshot_id，不再设计不存在旧列的迁移。provider状态与邮箱schema50本期不改。

真实生产task_agent_runs无status CHECK/summary FK，scheduled occurrence_count也无>0 CHECK；与fresh CREATE模板不同。精确捕获源DDL，不能因新表而无声加强历史约束。

已知FK finding为meeting_alignment_runs旧rowid2298→不存在的meeting job。保留原行、不发明父对象、不暗中修复。内部ID映射为被引用但不存在的旧键预留不创建row的物理ID，不能意外指向另一类型已有row；目标语义finding对照旧table/key/关系，逆迁恢复原始raw PRAGMA指纹。需要独立回归证明该保留策略，否则该领域单元不得发布。

## 8. 验收与未完成事项

4A已实现准确DDL、类型投影、DAO和调用方切换及配对逆迁，正在专项测试和独立复核。4A的类型专有字段使用原字段的直接SQL列，保留自然唯一、索引和约束；不适用的字段保持NULL，CSV已更新为实际映射。真实副本的原45,533条核心记录逐列指纹一致，145张其他原表指纹一致，已演练保留新增回执的逆迁、实际旧代码启动／领取和再次正迁。独立复核发现分配后删除记录的逆迁水位问题，必须修正并重验后才发布。4B/4C未实现，尚未改生产或部署第四期。

副本验证需保留所有原业务行、原API、public身份、native选择、结果/回执及状态。加入新input/run/provider attempt/receipt/Workbench事件后逆迁，验证新事实仍在，旧代码真实启动；不能覆盖旧DB备份。quiet正式部署前单元逆迁和相关定向测试、独立复核必须通过。每单元发布后读回PID、health、queues、Attention、History、原生缺失显示和新增失败/卡住记录，最后报告实际DB/WAL/页占用。
