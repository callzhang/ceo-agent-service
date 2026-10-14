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


### 2026-10-13 Phase5C正式发布

PR77／46bfdbe0正式部署完成，实际Store2026-10-13.3／Email51、PID82712、126物理表。
最终Quality CI38445177259通过，原九类2167条actions及1080／614／67条postfix事实的
字段值／类型／身份保留，两个原生水位560／115不回退；原FK诊断保持。真实CLI替身
及配对逆迁证据范围按phase5c验证文档；生产只读API／页面与最新Store17日备份验证
完成，未触发测试对外发送或VACUUM。来源临时数据清理以release证据为准。
D工作树已合入这一已发布主线，不保留旧测试夹具；下一步刷新实际Store17来源并
实施D目标存储。邮件动作来源另补4表41字段，整体13表144实际字段已盘点；独立
来源测试20项仅覆盖其代表性行为。D／E／邮件目标及第六至八期仍未完成，10+1不变。

### 2026-10-13 Phase5D opt-in storage acceptance

D来源已绑定实际已发布46bfdbe0／Store17，发送记录14字段与观察关系4字段的
有限actions存储基础64a5c110通过独立SPEC和QUALITY复审，两个审查均270项
定向检查通过。原六／九／十二类型声明逐对象不变，观察者整数复合身份、
公开ID索引及sent原生ID耗尽事务行为的审查发现已用回归修正；无新增物理表。
证据见database-redesign-phase5d-validation.md。这仅是opt-in存储基础，未激活、
未保留D目标版本号、未上线；下一步实施成对正迁／当前事实逆迁，再做caller、
真实副本、旧代码重开、release及上线验收。完整10+1目标仍未完成。

### 2026-10-13 Phase5D standalone migration/copy acceptance

成对存储迁移25ed876c通过独立SPEC／QUALITY；两个审查各125项定向检查通过，
额外拒绝／MAX／全十二类型当前事实循环按D验证文档记录。最新生产来源已跟进
到dc6abe2d／Store17（主线增量仅OA状态描述，无本期存储代码变化）。真实在线
副本3928原actions、2341发送及320观察者保留值／类型／身份／关系／水位，
122其他表持久字段一致，原单项FK诊断不变；修改后事实逆迁、实际已发布代码
新进程重开、再正迁及二次逆迁通过。证据phase5d_current_source_storage_copy.v1.json。
副本126→124物理表仅证明存储函数；Store marker有意保持17，不是新构造器／
发布证明。508825373字节任务临时副本／代码已清理。下一步合入最新主线，
接入Store/bootstrap、全部调用方及配对release，再做真实构造器副本／CLI／CI／
生产验收。D未上线，完整10+1仍未完成。

### 2026-10-13 Phase5D integrated copy/native acceptance

D isolated Store18 candidate has converted every sent/observer caller and
exact17→18 publication reader. Independent SPEC/QUALITY acceptance covers
bootstrap, migration/inverse, release, caller behavior and adjacent readers.
Actual dc6abe2d Store17 online copy passed the normal default new-process
publication, exact field/type/identity checks, current-fact inverse, actual
published old-code reopen and republication;126→124 physical tables,122
unrelated table hashes and the existing FK diagnostic retained. Real native
Consumer/Audit approval persisted one business result and substitute receipt;
11 separately scripted copy lifecycle scenarios also passed. Evidence:
phase5d_constructor_copy.v1.json, phase5d_native_copy.v1.json and D validation.
Root-owned temporary directories were removed. CI, normal deployment and live
acceptance remain pending; D is not yet published. Full10+1 remains incomplete;
E and later action/business/email/config/state/final-retirement work remains.

The first full D CI38467335512 exposed ten historical source16 constructor
fixtures;12746 checks passed. Fixture-only9acd12dd explicitly advances those
inputs through the genuine source17 transition, retaining all original
business assertions and the frozen historical source contract. Exact ten plus
81 adjacent checks and independent SPEC/QUALITY pass. Latest production
ba8b5931 is merged; its persistence modules are unchanged, and refreshed real
constructor/current-fact inverse/actual old-code/native approval plus11 scripted
copy cases pass. The v1 D copy artifacts now bind that source; temporary copies
are removed. Replacement full CI and normal deployment remain pending, so D
is still unpublished and the complete10+1 goal remains incomplete.

Subsequent current-main7fabea80 Calendar/OA/deploy-stop increments are merged.
The same seven persistence modules are byte-equal to the last actual copy
source; readonly Store17 declaration/full-integrity/FK readback and two69-case
increment/release suites pass. Binding artifact distinguishes this evidence
from the actual ba8b5931 constructor/native proof. No source snapshot or
transcript is retained. Replacement full CI and live acceptance remain pending.

### 2026-10-13 Phase5D actual publication accepted

PR80/head5221062d final Quality38476785656 passed12764Python checks(112skip,44deselect),
723frontend(2skip), browser2 and build. Normal deployment published1b6f55e5,
Store18/Email51,126→124 physical tables;2341 sent and320 observer facts are in
finite actions. Full current declaration, unchanged original FK diagnostic,
verified latest backup and readonly History/Attention/expanded receipt checks
passed. An existing Status frontend coverage-contract mismatch was separately
reproduced, fixed, focused-tested and normally published as9e0570cb; the actual
Status page now renders running/healthy with queue data. Details and limits are
in phase5d validation and phase5d_production_release.v1.json. No real test send,
recall or feedback action, no native copy or VACUUM. Actual file size remains
471347200 bytes. E can now refresh against actual published18, rather than the
old Store17/empty-candidate observation. The full10+1 redesign remains incomplete.


### 2026-10-14 current continuation and physical completion boundary

The approved target is still exactly ten core tables plus one migration ledger,
not ten groups of permanently retained old tables. A fresh readonly production
observation at07:36 UTC found code `d9d34093`, Store18/Email51,123 non-SQLite-
internal tables (including FTS virtual/shadow tables), a471,347,200-byte main
file and4,185,952-byte WAL. Checkout, DB snapshot and file-size observations are
separate, not an atomic production capture. The subsequent page observation
reported115,075 pages of4096 bytes,6,425 free pages. No space was reclaimed.

- Phase5D delivery consolidation is published. Phase5E Attempt/Memory candidate
  `de01dcfd` is pushed in PR82, not published: current main delivery-repair and
  presentation increments are preserved. FullCI38529409719 is running. Earlier
  CI38521548780 at2bf passed13,083 backend,729 frontend and2 browser checks,
  but does not accept the newer merged head. Actual-copy/old-code/native proof
  executed at74ac; supplemental unchanged-path binding remains separately
  identified. On the measured source, E removes two old physical tables,123→121.
- Email13 source/writers and disabled pure forward/current-fact inverse passed
  sequential independent SPEC then QUALITY; root acceptance2b888442 records
  Task3A only. No constructor, paired release marker or target version selects
  this schema. Actual E publication binding and Task3B latest-copy/genuine-old-
  code cold read/write/re-forward remain mandatory before activation.
- Nine follow-up/Todo-sync families have accepted source inventory/native
  oracle and a150-field implementation plan/map atad8852d0. Planning and its
  independent review are not storage implementation or publication. One atomic
  nine-family migration must include the retained legacy-link FK conversion.

Even E, Email13 and those nine families together would remove only24 of the
measured123 old/noninternal tables (if no intervening additions). They do not
complete the goal. Remaining business/evidence, emails, config/provider/current
state, leftover actions/tasks, FTS retirement and ledger consolidation still
require their approved mappings, caller conversion and paired inverse proof.
Final acceptance must enumerate physical remaining tables and retained source
fields, not infer completion from a stage name or green test count.

Provider configuration is small: two email_accounts rows occupied8192 bytes
including indexes. The distinct email_provider_observations table held72,090
current per-message facts,13,316,096 data bytes plus11,542,528 index bytes.
The approved destinations are configs and provider_state respectively; these
counts do not justify keeping either old table permanently or deleting current
provider inventory by age. No new retention policy is introduced here.

### 2026-10-14 final E CI and unsuccessful normal publication

Final complete CI38529409719 passed atde01dcfd:13,092 Python checks
(118 skips/44 deselections),729 frontend checks(2 skips),build and2 browser
checks. PR82 merged as4a962ff7 at08:26:51 UTC. Normal development-checkout
deployment waited30 minutes and exited at08:57:26 with no changes because
the service did not become idle. Quiet wait was not bypassed. Production
remainedd9d34093/Store18/Email51 and123 physical noninternal tables; E is merged,
not published. The new phase5e_publication_status.v1.json records this boundary.

Before any E conversion, the same production PID73677 exposed healthz=ok but
system_health=degraded with five latest-reply recovery violations; failure
summary rose26→28 and Attention21→23 from the08:25:59 baseline. These are
separately observed pre-migration facts, not evidence of an E regression or
permission to replay/modify old tasks. Deployment/live acceptance remains open.

Followup9 disabled source-shaped writers and readonly capture are now accepted
through sequential SPEC/QUALITY; Task2B pure migration is beginning. Email13
still awaits actual E publication binding and Task3B real-copy/old-code proof.
Readonly configs/provider/schedules/state preparation covers34 existing tables
and238 fields exactly against the approved CSV; it selects no runtime migration.
The full ten-core-table plus ledger objective remains incomplete.


### 2026-10-14 E publication readback and remaining acceptance

The first timeout above remains a historical no-change receipt. A separate
repository upgrade succeeded at09:38:23 UTC fromd9d34093 to5691f6c7; this task's
second normal deploy exited0 because another deploy had already reached that
commit. Readonly09:40–09:46 metadata confirmed Store19/Email51,121 tables,
exact complete16-type actual action declaration and retirement of the two
physical E source tables. Quick_check returnedok; the original single
runs190042→tasks FK violation is unchanged. Main file474468352 bytes is not
smaller; no VACUUM or file-size improvement is claimed.

Status eventually rendered PID34642 with degraded recovery health and30
failed queue items at09:55:14 UTC; earlier degraded health predates E.
Several Status/Attention/History requests timed out. Readonly profiling
isolated a History correlated anti-join bottleneck; a query-shape diagnostic
reduced full page-key selection from>5s to1.440s, but set equivalence, HTTP
acceptance and the other timeouts remain unverified. Publication is established;
complete live acceptance is not.

Followup9 pure migration/current-fact inversec57de7bc now passed sequential
SPEC then QUALITY and is accepted locally at e60adb84. It remains disabled.
Email13 now starts Task1 binding against actual published5691f6c7; Task3B
latest-copy/genuine-old-code read/write/re-forward remains required before
constructors, paired markers or any activation. The ten-core-table plus one
ledger objective remains active and incomplete.


Email13 Task1 is now accepted at467b4dc2 after sequential independent SPEC and
QUALITY, against actual publishedeb797e6b Store19/Email51. Exact13-source-table
metadata/144 fields and all declared closure match. The source-only70 checks
include the repaired ordinary-rowid ignored-MAX behavior. This does not accept
Task3B, constructors, callers, activation or publication. Next: preserve that
published main increment, then actual-copy/current-fact inverse and genuine
archived-old-code cold read/write/re-forward proof.
