# Database Redesign Program

**已确认目标：** 采用 business；单一来源内联，单一归属直接 FK；仅需共享来源版本时使用 provenance；实际多条证据/关联事实归 actions；可准确溯源原文不长期保存。保留必要状态、已采用业务结果和执行回执。

**基线：** 2026-10-11 生产/计划工作树 36e42c17，Email schema 47。149 表/1611 字段/413.60 MB 附录来自此前 d171fb72 审计，不能用作当前精确执行前计数。

**排除：** 未确认的普通成功轮询明细 30 天政策；任何新增审查、授权或恢复策略；业务对象自动升格；原文/trajectory 副本；对新字段或共享来源身份的猜测。设计阶段只提交结构文档和计划；已执行阶段以末节验收为准。

## 分期及依赖

| 期 | 范围 | 代码入口 | 完成条件 |
|---|---|---|---|
| 1 | provider 冗余索引 | app/email_store.py schema 47→48 | schema/业务行/查询计划/幂等/失败回滚测试及副本演练；独立计划已附 |
| 2 | current plan JSON 改为指针读取 | app/email_store.py 的 _classification_row、持久化分类、计划切换、_validate_durable_rows；email API | reader 返回相同 EmailActionPlan、指针合法、无重复 JSON、旧 schema 迁移/重启可读；精确代码计划单独写 |
| 3 | 输入唯一版本和内联 provenance | app/store.py reply_tasks/reply_task_inputs/work_summary_inputs、email_task_adapter、dispatcher/adapters、task_source_documents | 当前/claimed 输入不复制；活动依赖可恢复；来源不共享无需 provenance 行；终态正文不留长期副本 |
| 4 | tasks/runs 共用记录 | app/store.py、native_standalone、agent_runtime_router、agent_cron、workbench | 每类运行身份/代次/lease/revision 与原生引用准确；不把 UUID/request ID 误判为 agent_run ID |
| 5 | actions 和直接 FK | reviewed_candidate_store、system_executor、email_store、store、follow_up、todo_sync、outbound_postfix | plan/review/execution/attempt/step/receipt 映射完整，发送幂等键/交叉审核 FK/允许的回填/历史结果不变 |
| 6 | business 与实际证据事实 | store、task_semantic_service/models、task_retrieval、project_context_service、task_source_documents | legacy/formal 区分、截止日/link 及所有证据角色保留；108 多信号 task、7 多信号 project、3 共享 signal 不被单 FK 截断 |
| 7 | emails、configs、provider_state、schedules、state | email_store、scheduled_config_storage、managed_skills、runtime_*、org_cache、dispatcher | 当前邮件身份/分类和 provider 完整库存可读；body 按来源读取；当前模型真实输入依赖单独核实；水位/CAS 原子性不变 |
| 8 | 移除旧结构、正式切换与回收 | deploy、database_backup、storage_maintenance、文档 | 精确 DDL 及脚本已副本演练；生产 quiet 切换；回读 PID/healthz/queue/Attention/History；报告真实 DB/WAL 及页占用 |

每一期是独立发布单元。完成上一期后重新核对生产提交与 schema，再编写下一期逐任务代码计划，不预先占用后续版本号。不得把方向 CSV 自动变成迁移 SQL；每项旧 CHECK/FK/trigger 必须有显式处理。不能长期双写新旧结构或增加正文 fallback。

## 页面与场景验证矩阵

| 范围 | 页面/读取方 | 必查场景 |
|---|---|---|
| Email | 分类列表、详情、反馈、action 历史、训练状态 | message-only 中间态、pending/processed/failed、人工分类、plan 切换、完整 provider 库存、原文不可用、模型 inclusion 失效 |
| Task | Tasks、Project、Attention、检索、History | legacy/formal/candidate、多个来源、多个 evidence_role、旧 TODO 日期、合并/取消、源无法读取、钉钉同步与跟进结果 |
| Agent | AttemptDetail、AgentSessions、工作台、History | Consumer/Audit revision、route failover、native 缺失、停止、重试、无 Agent 的系统触发 |
| Actions | OA/邮件/WeChat/发送/撤回/跟进 | 已 verified receipt、uncertain OA comment、无 candidate 的全局 receipt、交叉审核绑定、外部效果已开始、精确 backfill、重复 claim |
| Scheduling | ScheduledTasks、Status、队列 | 版本 CAS、overlap/skipped、occurrence_count、cursor/lock 恢复、无效触发；无新增退休期限 |

对应测试文件：test_email_store/startup_validation/web_api/training_storage、test_task_semantic_store/import/service、test_task_project_repair、test_reviewed_candidate_store、test_system_executor、test_agent_runtime_router、test_task_agent_session、test_workbench_store/native_events、test_scheduled_task_store、test_history/approval_history/history_types、test_console_web_api，以及各页面实际测试。实施时按阶段精确选择测试，不在生产 checkout 测试、不运行开发树的全套串行 pytest。

## 迁移与发布原则

1. 每阶段使用 SQLite backup API 创建一致副本，不复制 native trajectory。副本需 quick_check/integrity/FK 和业务结果核对；保留该流程最新已验证备份。
2. 演练不调用外部 provider、不 enqueue Agent、不回放既有任务。准确 source_ref/版本或 native range 缺失，明确报告不可用，不伪造来源。
3. DDL、schema validator、读写 DAO、直接 SQL 读取者和测试同一版本切换。不可将旧代码启动在新 schema 上；恢复需代码与 DB 配对。设计基线时 repository_updater 的异常回退尚未配对 DB schema。第1/2期已分别验证47→48索引逆迁和48→49缓存列逆迁；后续版本变化仍需各自明确的配对回退计划与演练，不能仅凭 backup_path 或前一期逆迁存在宣称可回退。
4. 生产产生新动作回执后，禁止用旧 DB 快照回滚覆盖新回执。需保持幂等事实的兼容逆迁或前向修复。
5. 部署沿用 python -m app.deploy 和现有 mutex/quiet 等流程。只有需要新文件/schema 切换时才修改部署实现，并单独测试；不假设现部署已经支持完整重构。
6. VACUUM 是独立回收步骤，在验收后的安静窗口进行。DROP INDEX 后通常只增 freelist，不宣称实际文件已经缩小。

## 文档

已确认结构与原基线保存在 docs/superpowers/specs/2026-10-11-database-redesign-design.md 及同前缀 inventory/field-map/schema-baseline。每一期行为代码提交同时更新 docs/architecture.md 和 docs/runtime-mechanism.md，区分当前行为与未来已批准方向。文档计划提交不宣称迁移、测试、部署或空间回收完成。

## Current acceptance

第1期索引精简和第2期 current-plan 指针读取已上线，第2期650项定向测试、149表副本/逆迁/实际旧48启动及生产读回完成，见 docs/storage-phase2-validation.md。第3期 reply_tasks／reply_task_inputs canonical 输入及未来 Task WSI 包精简已上线于5d25497e，Store2026-10-11.1、Email49；150表副本、精确逆迁、旧b998启动／恢复、生产读回及全量Quality CI38140069414完成，见 docs/storage-phase3-validation.md。本期保持现有WSI结构和原有回收条件，其他业务来源表不在此次范围。后续第4期起仍需先核对最新生产与读者依赖，分别制定逐任务计划；未实施 tasks/runs 或 actions 的表合并，也未新增保留期限。

第四期已开始分析：以f59a41d7/Store2026-10-11.1/Email50为新基线，完成17表258字段及退休/延期结构总319字段的准确映射。第四期计划分4A core runs、4B任务/领域、4C定时/工作台；16表拟合并，搜索metadata原表只取消5空缓存，合表与FTS退休留第8期组合验证。详见phase4设计/field-map/inventory及计划。尚未改runtime、数据库副本、schema逆迁或上线；禁止把第三期验收套用到第四期。


## 2026-10-13 全目标执行状态（覆盖上文阶段性时间说明）

原目标保持10张业务/运行表加1张迁移表；第1–4期已上线不等于整个设计完成。生产当前Store2026-10-13.2、Email51，第四期最终发布与副本/逆迁证据见database-redesign-phase4a/4b/4c-validation.md；后续真实CLI验收见database-native-copy-lifecycle/branches-20261012.md。第5A已于31d95f4e上线，第5B已于ebaf0d96上线；其余第5期单元及第6–8期尚未上线。

| 期 | 当前证据/剩余完成条件 | 状态 |
| --- | --- | --- |
| 1–3 | 指针、输入引用、已实现终态正文精简；原验收文档 | 已发布 |
| 4 | tasks/runs、定时/工作台核心合并，source/inverse/new-fact/旧代码和CI/发布；原验收文档 | 已发布 |
| 5 | actions统一：采用候选/审核/选项/补充/执行/尝试，随后独立回执、消息/OA/邮件/WeChat/跟进/同步；所有读写调用、约束及配对逆迁 | 5A六类型、5B三类独立回执已发布；其余开发中 |
| 6 | business及signal/evidence/date/实际关联；legacy不升格、准确来源版本、原文不复制 | 未实施 |
| 7 | emails/configs/provider_state/schedules/state；当前库存、模型真实输入依赖、水位/锁/CAS | 未实施 |
| 8 | 全部旧物理结构及旧写入器退休，FTS取消、字段与来源规则逐项审计；副本/回退/CI/部署/UI与实际容量 | 未实施 |

本目标完成门槛以最终物理表、读写路径、字段删除清单、来源引用、现有业务合同与执行回执为准。不得以一组测试或阶段发布宣称全目标完成。未经确认的成功轮询30天删除政策仍排除；无需再次设计或确认已接受的十表结构。

### 2026-10-13 Phase5A release

PR72/31d95f4e formal deployment passed, Store2026-10-13.1/Email51,132 physical/126 ordinary application tables. actions six-type manifest, paired inverse with actual current production code, full CI, substitute native approval/revision, operational/API/UI reads and latest verified backup complete. Evidence:database-redesign-phase5a-validation.md and evals/database_redesign/phase5a_release.v1.json. Current targets present:tasks/runs/actions; full10+1 still incomplete. Next unit:phase5b independent receipts, exact plan/source oracle already prepared. Original1611-field map has7 actual current email fields in a separate2026-10-13 addendum; cancellation and explicit retry facts join the future email actions unit, no policy additions.

### 2026-10-13 Phase5B release

PR76／ebaf0d96正式部署通过，Store2026-10-13.2／Email51，129物理表／123普通应用表。完整CI38422492340、当前事实配对逆迁、实际旧代码及真实CLI替身批准／修订证据完成；上线源备份字段值／类型、原六类型、FK诊断、队列、History／Attention读回通过，未测试真实对外发送。证据：database-redesign-phase5b-validation.md及phase5b_release.v1.json。完整10+1仍未完成。C的opt-in十二类型基础与独立当前事实迁移已完成预验证，尚未运行发布；D的18字段源声明及76项独立oracle准备完成，目标存储未实施。下一步以实际已部署Store16来源完成C的caller/bootstrap/release接入与正式副本验收，不重新设计已批准十表目标。

### 2026-10-13 Phase5C integration candidate

PR77开发候选已完成postfix三表十三字段合入十二类型actions、Store17 fresh/bootstrap、
全部prepare/claim/receipt/echo/meeting/WeChat调用和配对release。合并最新OA改动后
212项定向检查、100项恢复／发送检查通过（11项明确环境跳过）；3处current marker
断言RED后校准，128项邻接检查通过。独立SPEC／QUALITY已通过，范围仅5C。
当前生产仍Store16；目标未发布。真实CLI替身批准验收已完成，见
phase5c_native_copy.v1.json：Consumer／Audit及业务结果／替身回执链通过，
group-send案例未覆盖native dispatch claim，后者由caller／copy检查覆盖。
完整CI与正常发布／生产读回仍是门槛，不将来源副本或模型验收当作上线证明。
实际最新生产副本配对constructor／current-fact inverse／实际旧代码启动／再正迁
见phase5c_constructor_copy.v1.json；独立所有原九类型值／类型／水位／改绑／删除
证明见phase5c_current_source_storage_copy.v1.json。临时副本已清理。
完整10+1仍未完成；5D等其余actions家族与第6–8期仍需实施。

### 2026-10-13 Phase5D／5E及邮件动作来源准备

D的发送记录18字段和76项独立来源行为检查已准备；E实际生产58列Attempt／
10列Memory与fresh54／10变体、54项来源检查及2项变体跳过已准备。
E实施计划6ee59d29通过独立SPEC和QUALITY核对，包含当前queued审查标记的
配对逆迁和页面／helper／runtime三种既有native范围行为；两项对应回归通过。
这些结果不证明D／E目标存储、迁移或发布。实施前必须刷新实际已发布来源。

邮件动作来源551cacf0捕获实际Store16／Email51的9表103字段及4张外部子表
依赖，3,265项元数据断言通过，未保留私有原始行。准确DDL／字段／调用路径见
2026-10-13-database-redesign-email-actions-source.json及同名前缀source-map.md。
这是来源盘点；独立来源行为、目标映射／调用转换及配对迁移尚需完成，不占用版本。
所有来源测试均无真实发送、provider回放或生产写入；完整10+1目标仍未完成。
