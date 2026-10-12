# Database Redesign Phase4 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to execute this approved direction in the independent release units below. Do not treat analysis artifacts as completed migrations.

**Goal:** 将真实tasks/runs字段统一存储，取消已退休原生缓存；保留public/native/idempotency身份、状态、业务采用结果与执行回执。

**Architecture:** typed tasks/runs；record_type使用实际源表名，legacy_key保存准确旧PK；公共查询/claim/FK字段为实际列，专有既有字段显式typed payload。WSI TEXT/BLOB及Workbench最小事件payload保持原字节。4A、4B、4C独立发布及配对schema逆迁，无长期双写。

**Tech Stack:** Python、SQLite、现有Store/dispatcher/routed runtime/Workbench及schema publication机制。

本文件是基于真实字段的迁移执行计划，精确字段与源DDL见phase4 CSV/inventory。4A目标DDL、DAO切换和配对逆迁已实现并处于验证；4B/4C尚未实现，第四期尚未上线。正式实施每个单元前只为该单元选取新schema marker，不预占后续版本；重新核对main/生产/claims，合入他人的新主线行为。

## Task 0: 已完成分析交付

- [x] 同步隔离worktree到f59a41d7，建立codex/database-redesign-phase4-20261011，保留主树及生产。
- [x] 核对实际Store11.1/Email50及scoped PRAGMA/DDL/index/trigger/FK，而非fresh模板。
- [x] 17表258范围字段及61删除/延期字段逐列覆盖319/319。
- [x] 核对17退休物理缓存的实际占位、native/DTO/maintenance/幂等SQL依赖。
- [x] 核对旧ID空间、所有workload类型、Meeting投递key、Workbench UUID/event cursor、现有FK异常。

## Task 1: 4A core logical/provider/state runs

**Modify:** app/store.py（agent_runs/agent_runtime_attempts/agent_run_state_events schema、DAO、所有相关SQL）、app/agent_runtime_router.py、app/agent_turn_runner.py、app/native_trajectory.py、app/native_standalone.py、app/reply_task_deferral.py、app/deploy_maintenance.py、app/storage_maintenance.py；受影响API和10条现存FK关系。新建统一存储/迁移模块的位置仅在DDL与依赖复核后确定，不能随意另起第二存储框架。

**Tests:** tests/test_agent_runtime_migration.py、test_agent_turn_store.py、test_native_only_core.py、test_native_only_maintenance.py、test_routed_codex_execution.py、test_agent_runtime_router.py、test_consumer_result_validation_retry.py、test_reply_task_deferral.py、test_storage_maintenance.py、test_console_attempt_detail_api.py、test_approval_history.py；只选涉及改动的case/file。

- [x] 在一致副本捕获旧实际DDL/index/trigger/序列/schema版本及所有scoped/public/native/业务fingerprint；文件只在task-specific/tmp。
- [x] 实现本单元精确DDL与CSV→physical scalar投影；既有reply_tasks仍在原位置，本单元runs.task_id直接关联它，不提前建立半成品tasks双写。类型专有字段采用真实列，以保留SQL约束与查询索引；不适用类型为NULL。
- [x] RED覆盖不同源同号ID、Consumer/Audit代际/revision/turn/parent、多个provider attempts、standalone无parent、7trigger/correction/resume约束、自然唯一和expired-lease reclaim、原生缺失、result_schema校验。
- [x] 一次事务迁移三表，取消final_result_json/result_envelope_json占位，保留完整校验参数、DTO/native投影、adopted结果JOIN；所有相关SQL与已有外键同版本切换。
- [x] 去除agent_run_events的纯退休SQL后删除该空表；若真实副本存在服务业务行，停止该删除并重新分析，不复制trajectory正文。
- [x] 对10条原agent FK关系逐行映射；原public IDs/workload strings不变；typed parent lineage使用原真实身份比较。
- [x] 证明初始化不会重新创建旧表/列或改变source触发器合同；没有读旧表fallback、长期双写或native正文入库。
- [x] 运行相关focused tests、Ruff及diff-check；更新architecture/runtime当前行为及本单元验收，同代码提交。
- [x] 按Task4完成副本正迁/逆迁/实际旧代码及发布失败各阶段演练；独立复核通过；远端完整CI与正式部署仍待完成。

## Task 2: 4B task containers/input and domain runs

为控制风险分成四个内部子单元，各自验证，组合后才发布完整4B：

1. reply_tasks/reply_task_inputs + WorkSummary/Task闭环；current/claimed、版本水位、body retirement、WSI1字节、修复suffix及原子业务apply/projection一次切换。
2. meeting job/run + delivery claim/Memory parent引用；保留真实采用决定、来源/日历证据及原delivery key；明确已知孤儿的保留映射。
3. OKR request/run +原items request定位；不推测OKR run状态、终态时间或唯一run规则。
4. weekly analysis job自然键/cache/lease；service_state的report singleton保持原处。

**Modify:** app/store.py、app/dispatcher/adapters.py、app/task_agent.py、app/task_models.py、app/task_project_repair.py、app/meeting_alignment.py、app/okr_review.py、app/weekly_okr_report.py、app/native_standalone.py、scripts/inspect_task_attention.py、scripts/replay_task_attention.py及准确API读者；跨期业务表仅改被迁父FK，不重构动作。

**Tests:** test_reply_input_reference.py、test_task_store.py、test_task_agent_runtime_repair.py、test_task_semantic_import.py、test_task_project_repair.py、test_work_summary_payload.py、test_meeting_alignment_store.py、test_meeting_recovery_safety.py、test_okr_review.py、test_weekly_okr_report.py、test_native_standalone.py、dispatcher/Attention/History/Email真实调用测试。

- [ ] 再核对最新生产，捕获实际源约束和4A runs结构；完成本单元目标tasks DDL及legacy公共ID映射。
- [ ] RED证明current/claimed不同、再次领取捕获current、原input身份/provenance/JSON字符串/WSI BLOB byte-exact、terminal input不可重建、缺准确来源报不可用。
- [ ] RED覆盖Task普通/decision_repair.N/memory_backfill/deadline_backfill不同parent namespace；应用结果/receipt与输入精简同事务；Task projection后续更新仍允许。
- [ ] RED覆盖Meeting ready_to_send≠sent、已有主消息后继续calendar、不重新发送、旧delivery key相等；保留meeting run孤儿而不指向新类型巧合同号row。
- [ ] RED覆盖同request多个OKR精确session/range、旧request prompt/native ID不变、原items逐列相等；weekly缓存及owner-expiry CAS不变。
- [ ] 同时切换queue/dispatcher、恢复、raw SQL脚本、领域DAO/native定位；取消9个领域退休cache列及finish比较占位，保留状态/session/error及业务结果。
- [ ] 根据真实source CHECK/FK/unique迁移，不把fresh模板附带新历史限制。
- [ ] focused tests/Ruff/docs完成后按Task4验证并独立复核，再正常部署4B。

## Task 3: 4C scheduled and Workbench

**Modify:** app/store.py、app/scheduled_config_storage.py（引用读取边界，配置不重迁）、app/agent_cron/scheduler.py/consumer.py/models.py、app/dispatcher/adapters.py；app/workbench/store.py/models.py/native_events.py/executor.py/service_runtime.py/api.py、有关History/Session API及frontend读取测试。

**Tests:** test_agent_cron_scheduler.py、test_agent_cron_dispatcher_health.py、scheduled store/recovery与dispatcher测试、test_workbench_store.py、test_workbench_api.py、test_workbench_native_events.py、test_workbench_service_runtime.py、test_workbench_executor.py、test_store.py native search cases、test_meeting_alignment.py、frontend/src/pages/CodexPages.test.tsx及实际History/Attention测试。

- [ ] 定义trigger独立record_type，保留event_id、scheduled/manual partial unique、snapshot_id现存FK、execution_kind/key；不能给service command制造任务。
- [ ] RED覆盖overlap first/last/count聚合、schedule版本与candidate双CAS、contention重试不丢trigger、dispatch原子task+lease关闭、业务终态不反写触发。
- [ ] 迁Workbench task/turn/event，保留public UUID、全局client_request唯一、准确输入比较、task_sequence、single-active及claim UUID。
- [ ] RED覆盖stop/completion竞态、late write、service event cursor/sequence/native ordinal、工具start/completion配对、跨页、missing native、Claude/Friday以及终态RAM释放/回滚。
- [ ] 移除物理final_text但保留native/RAM DTO；不删user_text/resume_context/error_detail或既有confirmation结果。附件/工件/confirmation跨期留表并映射parent FK，保持public UUID和原删除语义。
- [ ] 本期明确只取消search metadata五空缓存；保留7个元字段的原表及FTS external-content关联。metadata迁runs及FTS/shadow删除留第8期组合验证，不新增复制正文或永久双写。
- [ ] RED比较旧/新native检索排序、分数、title-only/unavailable和cache失效；0.55 cosine+0.30 BM25算法不变。
- [ ] focused tests/前端相关测试/文档通过，Task4与独立复核后正常部署4C。

## Task 4: 每个发布单元的正迁、逆迁与上线

- [ ] 使用SQLite backup API创建一致副本到mktemp任务目录，捕获源commit、实际DDL/index/trigger/sequence/version、所有业务/回执和原生定位fingerprint。原生文件只按准确引用读，不备份trajectory，不回放历史任务、不调用provider。
- [ ] 在副本正迁后按原legacy identity逐行比较所有保留值、API、source选择及typed约束；比较自然键/CAS/claim/lease恢复与EXPLAIN，不能用行数代替内容。
- [ ] 正迁事务失败注入证明无半迁移，重复初始化不复活旧结构；所有已有FKfinding按语义映射不新增，逆迁raw指纹恢复准确。
- [ ] 副本正迁后新增synthetic input/run/provider attempt/receipt及本单元涉及事件；逆迁恢复捕获的源DDL/default/index/trigger/序列/version，保留新业务事实；禁止用旧DB文件覆盖。
- [ ] 对应本次源commit的真实旧代码在逆迁副本启动/读取/领取/恢复，再次正迁和重开通过。下一单元的旧代码是上一单元实际已部署版本，不固定使用最初f59a41d7。
- [ ] 扩展既有schema release factory支持本次明确旧→新版本，不泛化猜测未知版本。保持停止replacement→inverse→旧代码启动的现有机制；覆盖dependencies/verification/restart/health/loaded失败阶段。
- [ ] 分开核对Store和Email版本；本期Email50不改，任何其他会话升级需先合入/重新验证，不把双schema变更偷偷打包。
- [ ] 专项独立复核；focused tests与远端Quality CI按实际版本完成。不给本机活服务跑全套串行pytest。
- [ ] 推送完整commit，使用python -m app.deploy正常quiet/mutex/backup，不skip quiet、不手动kill/kickstart、不编辑生产checkout。
- [ ] 上线读回实际SHA/PID/schema/health/queues/Attention/History、原input/结果/回执保留、新失败/卡住、native缺失呈现；新增自然业务行单独比较，不能要求所有表永久行数不变。
- [ ] 保存简洁验收，保留最新已验证运行备份；删除准确task-owned副本/复制代码/日志/AST索引/临时ledger，报告逻辑内容和DB/WAL/页占用；不在本期混入生产VACUUM、保留期限或新审查/授权策略。

## 本次状态

Task0完成；4A实现、副本正逆迁及独立规格/质量复审通过，远端Quality CI和正式上线尚待完成。最新真实源commit3195cd3bd24d2841f9f36d0f3063d10fb9614fea，Store11.1/Email50；45,649条原核心记录全部投影及145张其他原表全值指纹一致。新增结果/回执及分配后删除的ID在逆迁后保留，真实源302个Python文件逐一匹配Git blob，旧代码读取/领取/再次正迁通过。发布五阶段失败回退演练通过。

索引修复保留原公共ID和排序；实际副本parent/workload查找已恢复范围索引，没有开放P1/P2。专项core130通过；调用方2493通过/6跳过；最新main扩大组2933通过/6跳过，其12项新fixture旧表SQL失败已修正，native/prompt26通过。独立最终复审114通过；完整远端CI仍是上线门槛。副本已用空间增加3,543,040 B（约0.886%），仅说明结构整合，尚未实现第四期容量减少。4B/4C未实施，第四期未上线。没有新的功能字段、历史状态补造、额外安全政策或正文备份。详细验收见docs/database-redesign-phase4a-validation.md。
