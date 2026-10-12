# Database Redesign Phase4 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to execute this approved direction in the independent release units below. Do not treat analysis artifacts as completed migrations.

**Goal:** 将真实tasks/runs字段统一存储，取消已退休原生缓存；保留public/native/idempotency身份、状态、业务采用结果与执行回执。

**Architecture:** typed tasks/runs；record_type使用实际源表名，legacy_key保存准确旧PK；公共查询/claim/FK字段为实际列。4B首单元专有字段采用准确source scalar，避免JSON包装与类型转换；其他类型按其逐列映射核对。WSI TEXT/BLOB及Workbench最小事件payload保持原字节。4A、4B、4C独立发布及配对schema逆迁，无长期双写。

**Tech Stack:** Python、SQLite、现有Store/dispatcher/routed runtime/Workbench及schema publication机制。

本文件是基于真实字段的迁移执行计划，精确字段与源DDL见phase4 CSV/inventory。4A目标DDL、DAO切换和配对逆迁已实现并处于验证；4B首单元及会议核心/DAO/外围调用已在隔离分支实现，4B完整候选与4C尚未上线；4A发布状态见本次状态。正式实施每个单元前只为该单元选取新schema marker，不预占后续版本；重新核对main/生产/claims，合入他人的新主线行为。

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

- [x] 再核对最新生产，捕获实际源约束和4A runs结构；完成本单元目标tasks DDL及legacy公共ID映射。
- [x] RED证明current/claimed不同、再次领取捕获current、原input身份/provenance/JSON字符串/WSI BLOB byte-exact、terminal input不可重建、缺准确来源报不可用。
- [x] RED覆盖Task普通/decision_repair.N/memory_backfill/deadline_backfill不同parent namespace；应用结果/receipt与输入精简同事务；Task projection后续更新仍允许。
- [x] RED覆盖Meeting ready_to_send≠sent、已有主消息后继续calendar、不重新发送、旧delivery key相等；保留meeting run孤儿而不指向新类型巧合同号row。
- [x] RED覆盖同request多个OKR精确session/range、旧request prompt/native ID不变、原items逐列相等；weekly缓存及owner-expiry CAS不变。
- [x] 同时切换queue/dispatcher、恢复、raw SQL脚本、领域DAO/native定位；取消9个领域退休cache列及finish比较占位，保留状态/session/error及业务结果。
- [x] 根据真实source CHECK/FK/unique迁移，不把fresh模板附带新历史限制。
- [x] focused tests/Ruff/docs完成后按Task4验证并独立复核，再正常部署4B。

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
- [ ] 分开核对Store和Email版本；本期不改Email schema；当前主线已升级Email51并合入/重新验证，任何其他会话升级需先合入/重新验证，不把双schema变更偷偷打包。
- [ ] 专项独立复核；focused tests与远端Quality CI按实际版本完成。不给本机活服务跑全套串行pytest。
- [ ] 推送完整commit，使用python -m app.deploy正常quiet/mutex/backup，不skip quiet、不手动kill/kickstart、不编辑生产checkout。
- [ ] 上线读回实际SHA/PID/schema/health/queues/Attention/History、原input/结果/回执保留、新失败/卡住、native缺失呈现；新增自然业务行单独比较，不能要求所有表永久行数不变。
- [ ] 保存简洁验收，保留最新已验证运行备份；删除准确task-owned副本/复制代码/日志/AST索引/临时ledger，报告逻辑内容和DB/WAL/页占用；不在本期混入生产VACUUM、保留期限或新审查/授权策略。

## 本次状态

Task0和4A已完成。PR50的完整Quality通过并正常部署为7baa1f0e，Store11.2/Email50；发布PID/health/queues/Attention/History、45,715个原公共ID及115条原回执读回通过，详见4A验收文档。后续其他会话已部署54e6c209的role-Skill构建性能改动，不改变Store12。4A验收文档PR51的Quality38205420510通过并合入ad7a6506。

4B第一内部单元在隔离分支43c1585b实现：reply/input/WSI存入tasks，Task run扩展runs；106个Store读写和18个外部调用模块同步切换，诊断/replay脚本使用公共投影和物理映射。准确旧状态/CAS、业务结果、回执、provenance、WSI1及输入身份保持。无值旧10.2→11.1迁移路径退休，保留当前源拒绝/claim/FK/receipt/OA/session约束。

7baa实际源副本正常构造器12→13、四源及原4A runs逐值比较、141其他表逐值、全表DDL/序列/marker baseline inverse、新writes/receipt逆迁、实际旧代码启动/claim/ID水位、完整claimed行再次正迁均通过。五阶段离线publication控制流程回退通过，stop/start/health是callbacks，真实构造器和旧代码另由实际副本验证。独立规格/质量复核关闭证据缺口。当前数据对应其自己的冻结副本，不跨快照按计数猜增删。

599个相关Store/发布/辅助修复测试、71个新storage/reader/manifest用例、五个故障阶段、36个脚本用例、Attention专项及合并后的84个相关测试通过；这些有重叠，不作为完整CI总数。已逐项合入主线54e6c209及验收文档ad7a6506，保留A的19个源声明校验、单次元数据读取检查及typed Task布局；额外32个源声明与6个状态投影用例通过。

Meeting23/13真实字段及已知run2298→缺失job4908已复核并在隔离分支实现。核心21项、DAO9项（含修复显式updated_at遗漏）、外围定向24项通过，并完成分阶段独立规格/质量复核；组合67项也通过，数量存在重叠。六张实际生产源DDL只读校验通过；这些不代替最终一致副本或上线。六个相关fixture文件284项通过，另四项读取ps的维护恢复/竞态用例在具备权限的隔离测试环境中通过；损坏源/current布局拒绝前后全逻辑dump及user_version一致。fixture最终独立规格/质量复核通过，没有剩余P1/P2；全B最终副本及发布验收仍待完成。Meeting与OKR/weekly的核心及运行调用已完成并分阶段独立复核；当前相关fixture和完整候选验收仍进行中，组合发布4B后再实施4C。完整B最终候选仍须最新源副本/回退、相关调用方测试、独立复核、远端Quality及正常deploy/live读回。当前B已推送并创建草稿PR58，最终运行代码582fccfe；完整Quality38237197124失败（142 failed/11,866 passed），相关fixture与WeChat身份假设正在逐项修复，尚未生产迁移，没有第四期容量减少声明。最新生产来源cac3b08d的四轮完整副本/逆迁/旧代码/再次正迁通过，670项主线整合及75项后续工具描述检查通过；待远端完整CI及正常deploy/live读回。当前详细证据见docs/database-redesign-phase4b-validation.md。


## 4C post-B-base preparation (read-only)

Read-only582fccfe/copy-Store13 check confirms the current shared insert/projection/index/NULL-key paths normalize legacy_key to integer. Workbench task/turn UUIDs collide under CAST (one task group, five turn groups); add exact text-key paths and uniqueness for those two source types, retain numeric ordering/allocation for other types, and scope NULL auto-fill to numeric types. Source TEXT PRIMARY KEY permits multipleNULL keys; no invented NOT NULL constraint. Parent ownership must distinguish reply inputs from Workbench turns.

Actual scalar additions: tasks add Workbench runtime_kind/provider_session_ref/archived_at, and turn client_request_id/task_sequence/user_text/stop_requested/error_code/error_detail/resume_context/execution_run_id/runtime_quiesced_run_id/started_at/completed_at. Runs add scheduled event_id/scheduled_task_id/trigger_kind/scheduled_for/dispatch_status/skip_or_error_reason/execution_kind/execution_id/dispatched_at/first_scheduled_for/occurrence_count/result_summary/snapshot_id, plus Workbench event sequence/event_type/payload_json. Reuse existing title/times/parent/status/lease/task columns only where source meaning matches.

The old creator text is not source DDL: scheduled source has snapshot_id FK and no occurrence_count positivity CHECK. Preserve source event uniqueness/partial schedule uniqueness/defaults/FKs/indexes and public trigger IDs. Search cache removal must also adjust the existing external-content FTS column definition, which currently names summary_text and fts_text; retain a readable content association and inverse without moving metadata or retiring all shadow tables (Phase8 scope).

Preparation snapshot counts155723 scheduled runs/sequence155726; Workbench20 tasks/37 turns/810 events; search metadata671. All final_text and five search caches are empty in that copy. These counts and DDL must be refreshed after actual B deployment; they do not authorize C migration now. Source guards, populated confirmation/attachment/artifact inverse, nullable UUID cases, event sequence/native ordinal, stop races and native search ranking remain required RED cases.

Read-only C caller preflight at582fccfe identifies coordinated runtime changes beyond the main DAOs: dispatcher/adapters.py scheduled metrics/claim/lease joins; repository_updater.py and deploy_maintenance.py quiet reads; scheduled_task_recovery.py reply lineage; audit_web.py superseded-run filtering; scheduled_config_storage.py initialization/reference rewrite; store.py scheduled Attempt/History/failure/recovery and runtime snapshot readers. Outward History source label scheduled_task_runs remains unchanged. Workbench native_events.py terminal/native event reads, store.py confirmation recovery/workload-active checks, workbench/store.py confirmation joins and storage_maintenance.py turn/event compaction also need typed projections. Required old-table/index/column manifests must change with current schema; no compatibility views. Existing child NO ACTION semantics stay in place. Source pointers were independently enumerated and representative dispatcher/quiet/native/FTS paths were checked by root. No C runtime edits or provider calls occurred.


Latest B release gate: c73039cb safely integrates production2bb2809c. All142 original CI failures now have separately SPEC/QUALITY-approved fixture repairs; combined31-file971 checks, whole Ruff/diff and11 offline publication/factory/updater checks pass. A fresh actual2bb source completed baseline inverse, new-fact inverse, actual321-file old-code startup/claim/watermarks and full re-forward; final independent critical review passed. Exact evidence is in the B validation document. New complete Quality and normal production deploy/readback are still required. C implementation must wait for actual B release and refresh its source13 snapshot.


B live release completed: PR58/44a1a4e3, complete Quality38248135583 passed, PID68292/Store13/Email51/public IDs/115 receipts/manifests/health/queues/Attention/History read back. Details and physical-page limits in B validation. C now starts on isolated codex/database-redesign-phase4c-20261012 from actual deployed44a1a4e3; refresh exact source13 DDL and current counts before core implementation. Pending old daily backup deletion requires explicit answer to the asynchronous question; no automatic assumption.

4C core unit: source13 exact49-field map, typed primitive/paired inverse/bootstrap/search cache coupling implemented. Core writer207 focused passes; two SPEC P2 source-boundary gaps fixed with8-failure RED/12-case GREEN; independent re-SPEC201 and QUALITY141 focused checks pass. Source constraints/UUID/raw values/children/rowids/FTS state verified within this core scope. Outer scheduled and Workbench DAO/readers/current14 source-quiet, real consistent production-copy/actual old-code, publication failures, complete CI and deployment remain pending. See C validation document; no C production application claimed.
