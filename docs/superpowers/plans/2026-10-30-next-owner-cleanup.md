# 下一批事实归属与无调用链路精简实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 删除已查实无生产用途的入口和旧 ID 适配，再将直接邮件 Action 的三个重复字段交还给唯一的不可变 Plan owner。

**Architecture:** 当前 Task／Run／Action 身份、来源、业务结果及回执保持原有含义。无 DDL 清理先独立上线；随后单独进行 Email Action 字段迁移。运行时只使用当前结构，不增加旧字段读回、备用 JSON、双写或自动修补；显式上一版本迁移及当前数据逆迁工具保留。

**Tech Stack:** Python、SQLite JSONB、pytest、Ruff、现有 Console、标准 app.deploy。

## 已核对基线与边界

2026-10-30 只读核对：开发工作树 `fc938001024533f35917d52f6dafa8fb94fdb9b6`；生产代码 `1e8d793ab6ca7c1cd78147e648afa6f320de01b6`。生产 Store2026-10-29.2／Email54，48张非SQLite内部表、239个显式索引、176个触发器，共463对象；逐对象匹配当前声明，SHA256 `f691c5e632c662948c94b8cc6112632d34331e57bbb58956d0422ea156382d02`。审计末尾再次核对，生产已推进到同一 `fc938001024533f35917d52f6dafa8fb94fdb9b6`，schema仍精确匹配且FK为空；额外Quality投影变更不改变本清单。提交和数据持续变化，实施与发布前重新核对。

已完成项不重复做：Meeting结果单次UPSERT、logical_run_storage退休、Attempt／Run provenance精确引用及相同原因／摘要去重、Signal来源归属、A5b回执引用、已完成的Email54／旧配置声明清理。另一任务的Quality修复不归本计划，认领及发布窗口单独协调。

本轮审计没有修改应用、数据库、声明或部署。静态核对覆盖app/scripts/tools/tests/frontend，Graphify只对7个相关存储模块做AST导航，再逐项核实源码。不能把本次候选列表说成全仓零兼容证明。

## 单元1：无DDL删除清单

|删除符号|准确位置|依据与处理|
|---|---|---|
|FollowUpMode|app/task_models.py|仅定义，无应用／测试调用；删除枚举。|
|owner_identity_is_supported|app/task_models.py|应用无调用，仅test_work_tracking_skill的直接测试；删除旧helper及专用测试。owner_identity_record／owner_identity_evidence_records仍被e2e使用，保留。|
|TaskAgentRun|app/task_models.py|仅test_store用作字典包装；改验当前Run／run_projections实际值，保留projection_json回执前后断言。|
|list_business_task_follow_up_send_attempts|app/store.py|仅test_follow_up读取；测试改用现有SYNC_ACTION_PROJECTIONS精确draft_id查询，保留sent／unknown／失败及顺序断言。|
|list_business_task_todo_sync_outbox|app/store.py|仅测试及测试harness字符串配置调用；改用测试目录的窄读取夹具，覆盖statuses筛选；保留实际dispatcher、claim／finish／retry／unknown恢复链。|
|find_single_chat_conversation_by_title|app/store.py|仅定义；删除按标题猜会话的闲置入口，保留按稳定conversation_id读取。|
|find_conversation_by_title|app/store.py|仅定义；同上。|
|foreign_key_violations|app/store.py|仅定义；诊断工具继续直接执行pragma foreign_key_check，不重建Store包装。|
|create_service_bugfix_candidate|app/store.py|生产仅被下面的无调用包装引用；测试为已移除页面造数。退休writer。|
|create_service_bugfix_candidate_for_feedback_event|app/store.py|仅定义；退休该旧反馈候选生产入口。|
|list_service_bugfix_candidates|app/store.py|仅已移除页面的测试调用；删除旧查询。|
|count_service_bugfix_candidates|app/store.py|同上；删除旧计数。|
|ServiceBugfixCandidate|app/store.py|仅以上旧方法使用的DTO；一起删除。|
|resolve_attempt_detail_reference|app/web_api/attempts.py|仍有真实调用，不能当死代码：先把build_attempt_detail及audit_web的详情入口切换为当前公开Attempt ID直接查询，再删除物理actions.id→legacy_key的旧URL适配与307重定向。|

旧service_bugfix_candidate有3条唯一历史事实（2pending／1resolved，payload约1784字节）。**本单元只退休入口，不删这3条记录、类型声明或索引，不把pending解释成当前应执行事项。**没有可证明对应的反馈Task，不迁移或合并到正式任务。

### 实施文件

Modify: app/task_models.py、app/store.py、app/web_api/attempts.py、app/audit_web.py；实际受影响tests/test_store.py、test_work_tracking_skill.py、test_follow_up.py、test_todo_sync.py、test_task_agent.py、test_task_attention_multisource.py、test_consumer_dispatcher.py、test_sync_outbox_dispatcher_actions.py、test_legacy_todo_retirement.py、test_audit_web.py、test_console_attempt_detail_api.py；新建测试窄查询夹具放tests/support，不能移回应用通用DAO。更新architecture/runtime相关当前入口段落。

- [ ] 刷新main／生产与claims，确认14项没有新增真实调用；一个实施者认领store.py各子范围，其他任务的发布和Quality文件不碰。
- [x] 保存相关节点基线。新增当前ID回归：公开ID成功；只存在物理ID而无公开ID的请求返回404、不重定向；数值碰撞时只按公开ID读取。保留当前History/Task链接正确的测试。
- [x] 删除只服务退休功能的专用测试；现行回执、outbox、Run projection的测试切换到当前owner，不能删断言或添加应用别名。
- [x] 退休service-bugfix页面的404回归继续保留；不再为一个已退休页面测试旧writer的CRUD。若需历史造数，由测试夹具直接写当前物理结构。
- [x] 执行精确符号搜索，确认没有剩余导入、getattr注册、脚本字符串、专用当前文档声明；删独占导入，不连带删除仍有用途的身份helper。
- [ ] 每个测试进程2GB／10分钟；先跑单文件／节点。对大型test_store、test_audit_web、test_task_agent只选实际相关节点；不在生产运行全套。
- [ ] 比较前后schema清单／hash与FK集合完全不变；SPEC／QUALITY通过后，行为文档与代码同一提交，单独标准部署并只读验收。

验收：当前公开ID详情、History链接和回执正常；旧物理ID适配不存在；所有原有效果断言继续保留；历史3条bugfix记录不变。本单元没有数据库文件缩小承诺。

## 单元2：Email Action三字段退出重复owner

### 已证明的字段归属

在同一只读事务核对的13078条直接邮件Action中，以下三项与精确action_plan_id对应的Plan按SQLite值和类型逐条相同，不一致均0：

|退休Action字段／JSON键|保留的唯一owner|重复值UTF-8字节|
|---|---|---:|
|classification_id／action_classification_id|Plan.classification_id|246966|
|account_id／action_account_id|Plan.account_id|262462|
|parameters_json／action_parameters_json|Plan.action_parameters_json的当前operation子项；沿用现有缺省空对象合同|265774|

三项合计775202字节（约0.74MiB），这是逻辑重复值，不是SQLite净文件回收预测。非Plan Action的这三个字段在所检生产数据均NULL；当前运行时除direct Email writer外没有赋独有值的create_action调用。模板默认NULL键也随字段声明移除。实施前必须重验新数据及各历史输入，不能依据本快照静默丢弃差异。

### 目标字段与DDL合同

- 从当前ACTION_FIELDS／ACTION_ALLOWED_FIELDS／ACTION_INTEGER_FIELDS移除这3个字段；共同action_data对象移除对应3个键。不是保留NULL列或写NULL占位。
- 目标Actions声明退休3个现有generated虚拟列：action_parameters_json、action_classification_id、action_account_id。其余字段／身份／回执不动。
- 保留action_plan_id、operation和唯一索引idx_actions_action_direct_email_plan_operation。Plan自身的完整action_parameters_json、依赖信息、版本和授权快照不删。
- DIRECT_EMAIL_ACTION_SQL直接从准确Plan派生现有Email业务字段；无双读分支，无COALESCE回旧Action字段。generic Action API不再接收／输出3个已退休字段；Email执行DTO保持其实际业务输入。
- 只保留一个当前正向转换和对应当前数据逆迁；上一版结构仅出现在显式工具／冻结夹具。准确目标SQL由本单元冻结，不能混入其他待发布schema变更。

### 必须一起迁移的读取与既有约束

1. shared_action_lifecycle.create_direct_email_action不再接收或双写3项；现有冲突检测沿准确Plan+operation检查同一个不可变事实。
2. EmailStore._persist_action_plan／_ensure_direct_action_rows／_claimed_direct_action、重试CAS、依赖图及completed查询统一沿Plan读取。初始化一致性检查不再检查已不存在的两份值；迁移预检负责拒绝不一致源。
3. 当前邮件plan-switch阻挡触发器以及账号update/delete阻挡触发器改为Action→Plan关联，保留原processing条件、原操作范围和原拒绝含义。这是原约束换owner，不新增Audit、授权、恢复或重试政策。
4. generic create/get/CAS、prepare_outbound_action默认模板及ACTION_FIELDS动态使用方同步切换，非Email发送与回执不能受影响。
5. 旧action_entity_transfer中的显式forward/inverse适配新当前目标：原历史3值必须先与其Plan完全相同才能省略；有差异拒绝并报告，不猜测。inverse从当前Plan还原准确旧值。

Files: app/action_entity_definitions.py、app/action_entity_storage.py、app/shared_action_lifecycle.py、app/email_store.py、app/email_canonical_definitions.py、app/configs_definitions.py、app/action_entity_transfer.py、app/action_storage.py涉及ACTION_FIELDS的当前投影；app/current_schema_declarations.py、current_schema_manifest.py、store_schema_release.py、current_publication_sources.py及对应显式迁移模块／冻结上一版本catalog；更新当前SQL／字段映射文档。认领后单独协调发布文件，不能与其他schema任务并行编辑。

- [ ] 先冻结字段owner、所有读写方、3个generated列及触发器引用、现有索引和完整目标SQL；任何未映射字段报错。
- [ ] 加回归证明当前Action JSON确有3份重复键；之后改为断言键不存在，generic API拒绝旧字段。Email投影仍返回相同参数值、账号与classification；非Email Action及回执不变。
- [ ] 覆盖多Action Plan、空参数、依赖参数、旧Plan版本、agent／unsubscribe非direct项；Plan不存在、参数／身份不一致的源在修改前拒绝。
- [ ] 保留并复验claim／retry／CAS、processing期间plan-switch及账号update/delete阻挡；没有processing时原操作仍可进行。不会删除阻挡触发器来绕过依赖。
- [ ] 当前数据副本正向迁移，写入新Plan／Action／替身回执，再逆迁；真实旧代码冷启动读写，最后再次正向。逐值／类型比较事实、全部身份、引用、序列、其他payload及FK异常集合。
- [ ] 逆迁重建3字段只基于不可变Plan精确事实；含差异历史库不准通过猜测补齐。原始不存在键与NULL等非业务差异若影响源合同，也须明确冻结并验证，不能悄悄宣称逐字无损。
- [ ] 对既有claim、账号保护和completed查询比较EXPLAIN QUERY PLAN及有界同数据计时；必要索引按真实查询决定，不先删除索引凑空间。
- [ ] 定向跑tests/test_email_action_writes.py、test_email_action_plan_boundary.py、test_email_action_status_identity.py、test_action_storage.py、test_action_entity_schema_release.py及新当前逆迁测试，使用实际仓内文件名；全部模型/provider用替身。
- [ ] SPEC／QUALITY通过后标准完整gate、自然quiet、已验证备份、精确提交发布。读回Email队列／History／Action详情／receipt、Store/Email版本、schema/FK，生产仅只读。
- [ ] 读回后重新验证备份精确代次并清除本workflow备份和临时文件；不删他人新生成的文件，不为纯文档再部署。

## 本次不列为直接删除的项

- 旧Project／Todo／Update：历史详情和跟进／钉钉回执仍有真实读取方，保留必要事实和稳定历史身份；不恢复运营写入。
- BusinessProject.anchor_type：33条当前记录均为project，但它仍参与复合FK约束和idx_business_typed_anchor父键，不能只删列／索引；如以后处理，须先单独设计等价约束。本批不改。
- run_adopted_*、runtime_attempts、run_provenance：具有不同采用、调用和来源事实，不能因都属于Run就再次塞回一个JSON。
- 现行明确上一版本转换工具、原始provider receipt、缺失授权证据标记及历史独有失败详情：它们不等于运行时兼容分支，不按名字含legacy直接删除。
- 239个显式索引中未发现键、排序、collation、唯一性与partial条件均相同的完整重复项。前缀重叠不证明冗余；本批不列未经查询／FK证据支持的DROP INDEX。

## 执行与交付边界

先做单元1并独立部署，再做单元2迁移；每个单元分别记录代码、测试、部署、生产功能、业务回执及清理证据。没有授权执行前，本文件仅是计划，不修改生产或开始部署。全过程不新增业务字段或运行时旧结构兼容；不声称全库最优。

## 单元1已上线

2026-10-30标准部署57c75b0c0，门禁8164PASS／2skip／1按规则未运行，486.24秒；自然quiet，无中断。schema463对象/hash和FK[]不变，3条历史bugfix事实指纹不变。公开Attempt16429数据API200、旧物理127430数据API404；SPA页面壳HTML200不等于详情存在，未做旧ID转换。History分页/筛选及回执22354读回通过；临时测试产物在全计划结束统一清理。准确备份代次清理见[Unit1验收JSON](../../validation/2026-10-30-next-owner-unit1.json)。
