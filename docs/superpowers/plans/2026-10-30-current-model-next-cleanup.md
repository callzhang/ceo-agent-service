# 下一批当前模型清理实施建议

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans after Derek approves this proposal. Steps use checkbox syntax. Derek已批准本轮执行任务1–3；Task4 anchor_type迁移明确不做。

**Goal:** 删除没有当前调用方的应用入口和已经不可能成立的旧结构分支，让 Project 职责只通过当前 context 读取。

**Architecture:** 当前生产只接受精确当前 schema；Email API 只使用现有 EmailStore；测试改验当前事实 owner。保留真正的来源、历史事实、回执及明确上一版本的迁移／逆迁工具，不添加运行时兼容、别名、双写或备用 JSON。

**Tech Stack:** Python、SQLite、pytest、React／TypeScript、Vitest、标准 app.deploy。

**Spec:** Derek 本次要求“结合当前代码和生产 schema，排除已完成项，给出下一批具体删除清单和实施计划，不加旧结构兼容逻辑”；本文件的审计边界和删除合同是本计划的依据。

## 当前证据与范围

- 开发基线 `60adfec675f3c0037ac390e14e6e5320d3e81104`；生产读回 `42300c317f89528a2b7b7fc337da30c311815f9c`。实施与发布时重新核对，而不是把本次时点当作永久状态。
- Store `2026-10-30.1`／Email54；48表、239显式索引、176触发器，463对象精确匹配当前声明，hash `6e3244bb571073026a3256b7cfbac6cd547477331e2d4f0fc37d37273275951e`，FK为空。
- `business_legacy_links` 物理表不存在；其 DTO 仍只由测试登记使用。旧映射的实际 owner 是 WorkTodo.import_task_id，不能删除。
- 33条 Project 的 anchor_type 都是 SQLite TEXT `project`，JSON类型也全部为 text；常量值本身合计231字节。这不构成可观的磁盘回收承诺。
- 同列、排序、collation、唯一性、partial 条件及精确索引正文核对未找到可直接 DROP 的完整重复项。表达式索引仅忽略名称比较原始定义，不宣称证明所有 SQL 的语义等价；前缀重叠没有列入删除清单。
- 已完成的14入口、物理Attempt ID适配、Email Action三字段归还Plan、Meeting单次结果写入、logical Run入口、Attempt／Run provenance与相同摘要去重，不重新实施。
- 审计仅阅读代码、当前生产元数据及少量聚合；没有创建数据库副本、调用模型／provider或运行测试。Graphify只对8个存储模块做AST导航（51节点、74边，无外部模型调用），结论回到源码核实。

## Global Constraints

- 不删除唯一历史事实、不同失败详情、业务结果、provenance或执行回执。
- 不修改 Audit、授权、发送、重试或外部效果核对政策；删除无调用旧写入口不代表变更当前决策流程。
- 不增加当前不存在的业务字段、通用历史容器、应用侧替代约束或能力开关。
- 每个测试进程固定2GiB／10分钟；只跑直接相关文件／节点。集成后通过标准完整gate、自然quiet、备份和失败回滚部署。
- 测试模型和provider全部使用替身；生产读回只读，不创建测试任务、不重放历史发送。
- 任务1–3不改DDL、Store／Email版本或数据；发布前后catalog/hash/FK必须相同。任务4是低优先级独立迁移，不能夹带。
- 测试专用查询与造数放在tests/support；不以应用DAO／DTO／别名的形式重新保留旧入口。

## 方案取舍

1. 只删12个死入口：风险最低，但邮件运行时仍保留已无意义的兼容判断，职责也仍有两种API入口。
2. **推荐任务1–3一起推进：** 死入口、Email无效分支和Project职责重复投影分别聚焦验证，集成发布一次。没有schema迁移，直接减少当前维护合同。
3. 再加入任务4：多取消一个可写常量字段，但需要完整迁移／逆迁维护；仅33条常量，优先级低，建议独立决定与发布。

## Review Focus

1. 当前存在直接退订回执但没有逻辑Run的记录：仍展示回执，不因清理缺表分支强行要求Run。
2. Store或模型真实不可用、模型未激活、当前方法返回None：保留真实业务分支，只删除缺少旧方法的兼容分支。
3. 测试中通过字符串getattr／monkeypatch登记的旧入口：一起迁移，不能只搜索普通import。
4. Project context为空或历史revision职责不同：不丢引文，不借其他Project／Task补职责，不留下顶层旧字段双读。
5. 非Project Anchor以及被引用Anchor改类型：保留四元FK拒绝语义；不是删掉类型列就算模型简化。

## Task 1：删除12个无生产调用的入口／DTO／helper

|准确符号|当前位置|实际依赖与处置|
|---|---|---|
|AutoReplyStore.resolve_needs_human_attempt|app/store.py:20571|应用、测试、脚本与工具均无调用；删除。当前决策继续record_actionable_attempt_decision→record_reviewed_reply_rerun，不改close_needs_human_task_with_decision。|
|AutoReplyStore.get_agent_run_for_turn|app/store.py:7309|仅测试；测试在当前runs中定位准确ID，再用现有get_agent_run读取。|
|AutoReplyStore.list_memory_write_events_for_attempt|app/store.py:20330|仅测试；测试窄查current_reply_action_projection("memory_write_events")。保留事件记录、当前writer及重建语义。|
|delete_sync_action|app/action_storage.py:1839|仅测试及测试monkeypatch；造数／删除移至测试夹具，保留生产insert/update sync与逆迁事实断言。|
|update_task_delivery_action|app/action_storage.py:1984|仅测试及测试monkeypatch；测试使用现有task_delivery_set_parts及原事务／CAS，不重建应用包装。|
|delete_task_delivery_action|app/action_storage.py:2011|仅测试；按当前身份窄删测试数据，保留FK／allocator测试。|
|validated_reply_variant|app/action_storage.py:1003|仅测试；当前声明校验改走现有require_current_pair／schema_objects与current_objects。只服务退出变体的测试退休。|
|reply_schema_is_current|app/action_storage.py:1027|仅测试；同上。不连带删除明确source/inverse仍使用的工厂。|
|email_action_auxiliary_objects_are_current|app/action_storage.py:1197|连测试也无调用；删除。当前初始化不做辅助对象自动修补。|
|EmailStore._training_snapshot_from_dict|app/email_store.py:10180|只有定义；真实读取已走_get_training_snapshot→email_training_data.read_snapshot，删除旧dict重建helper。|
|_insert_raw|app/email_canonical_storage.py:91|只有定义；保留insert/upsert_email_message→_write_email_message及insert_email_classification。|
|BusinessLegacyLink|app/task_semantic_models.py:489|仅test_task_semantic_store的字符串模型登记；删除DTO、ROWS／INVALID_ROWS登记和专用测试，不删除实际import owner。|

**Files:** 修改上表app文件；测试涉及test_store、test_audit_agent、test_consumer_agent、test_worker、test_agent_runtime_worker、test_reply_attempt_action_store、test_reply_current_declaration、test_followup_sync_action_writes／inverse、test_action_task_delivery_storage、test_console_web_api、tests/wechat及test_task_semantic_store。Run fixture复用tests/support/native_run_rows.py；窄读取可加在该测试支持层，不另造应用聚合DTO。

**Interfaces:** 生产Run继续get_agent_run／claim_agent_run；当前同步和delivery生产writer／parts不变；memory事件保留原writer；历史→正式任务映射继续business_legacy_owner_storage。

- [x] 冻结上述符号的应用、tests、tools、scripts、ci及前端精确名称调用；若主线新增真调用，切到已有当前入口后再删。
- [x] 将仍覆盖当前事实的测试移到当前模型：保留Run身份／顺序／状态、memory事件成对提取／重建、原生类型、FK、CAS与allocator断言。纯旧变体专用测试删除。
- [x] 删除12项及确无其他调用的独占导入；不删除仍使用的parts helper、事件存储和显式迁移／逆迁工具。
- [x] 分组执行实际调整的窄节点，例如`python -m pytest -q tests/test_reply_attempt_action_store.py tests/test_business_legacy_field_retirement.py tests/test_action_task_delivery_storage.py`；大型Agent／Store文件只选受影响节点。
- [x] 对删除符号重新检索，确认无调用、导入、字符串登记或兼容别名；更新architecture/runtime相应当前描述，独立提交。

## Task 2：删除Email运行时旧结构分支

**Files:** app/email_store.py、app/web_api/email.py；test_email_web_api、test_email_console_training_api、test_email_model_progress、test_email_canonical_current12及对应Store节点。

**Interfaces:** EmailStore构造器已经先require_current_pair、再AutoReplyStore、再验证当前声明；生产factory固定构造EmailStore(validate_rows=False)，require_store返回当前EmailStore。不会存在一个合法当前Store缺少actions／tasks／runs或这些当前方法。

删除5组缺表兼容探测，保留查询本体：

|读取入口|当前行|删除范围|
|---|---|---|
|_email_unsubscribe_attempt_ids|1230|actions不存在→空列表。|
|_unsubscribe_classification_membership|7947|tasks/runs不存在→省略task_clause。|
|get_email_unsubscribe_entry_url|8368|tasks/runs不存在→None。|
|list_email_classification_observability|8630附近|按缺表决定是否读取task／lineage；重复结果校验可合一次，但direct无Run仍须正常展示。|
|email_processing_progress|9613附近|tasks不存在→空退订队列。|

删除Email API的8处缺方法回退：list_mailbox_action_states两处（1502、1554），get_provider_classification_state（1537），list_selected_training_labels（2089），classifier_runtime_observability（2454），latest_training_snapshot_state（2483），classifier_runtime_rate（2503），model_scan_progress（2508）。直接调用同名当前方法，不新增协议、能力标志或第二实现。

- [x] 保存现行返回合同及正常／无Run／无当前模型样例；把缺方法的LearningStore等测试替身补成当前合同，或改用真实当前Store。
- [x] 删除仅为缺表／缺方法存在的分支，保留方法实际返回None、初始化失败、无模型、receipt内容和lineage绑定校验。
- [x] 验证`test_email_unsubscribe_filter_returns_dedicated_task_rows`、`test_processing_progress_reports_exact_queue_counts_and_scan_cursors`、`test_a_direct_receipt_without_agent_runs_still_shows_its_unsubscribe_address`及两项audited lineage／conflict测试。
- [x] 验证邮箱动作状态、observability、learning／training snapshot和model progress读取；缺表／混合schema仍在初始化处拒绝，不新增读时修补。
- [x] 复验`test_email_learning_and_model_version_expose_safe_modern_evidence`；为无激活模型和model_scan_progress／latest_training_snapshot_state真实返回None的当前状态保留断言，不通过缺方法替身制造这种状态。
- [x] 更新行为文档，检查无DDL差异，独立提交。

## Task 3：Project职责只通过context返回与读取

**Files:** app/web_api/tasks.py的ConsoleBusinessProjectDetail及business_project_detail；frontend/src/api/console.ts的BusinessProjectDetail及校验；frontend/src/pages/TaskProjectDetailPage.tsx及测试；tests/test_web_api_task_project_summary.py、frontend/src/api/console.test.ts。

**Interfaces:** 唯一事实owner仍为现有project_context_revision中的ProjectContext；当前详情使用context，历史详情使用revision.context。删除顶层responsibilities，不增加别名或双读。

- [x] 加断言：JSON详情不再有顶层responsibilities；context.responsibilities仍保留完整人员、职责和原引文。context为null时显示空态；两个Project不得混用职责。
- [x] 同一提交移除后端字段／赋值、前端类型／校验／夹具，并让页面直接从已存在context取职责；保持历史revision职责显示。
- [x] 执行`python -m pytest -q tests/test_web_api_task_project_summary.py`，以及frontend下`npm test -- --run src/pages/TaskProjectDetailPage.test.tsx src/api/console.test.ts`和`npm run build`。
- [x] 更新当前接口说明，独立提交；不动DB、provenance、context修订或证据页。

## Task 4：Project anchor_type退出payload／DTO，不删除类型约束

**本轮不执行。**此项属于独立低优先级迁移，后续须另行确认。33个常量不是值得立即承担迁移维护成本的磁盘目标；若继续做，价值是取消一个可写的冗余常量合同。

**现有约束必须保留：** Project四元FK的最后一列是project_anchor_type→anchor_anchor_type，且idx_business_typed_anchor是父键。直接删列／索引会削弱“Project只能引用project Anchor”和“被引用Anchor不能改类型”的现有合同。

唯一建议的目标：沿用当前generated discriminator做法；project_anchor_type仍是TEXT generated列、仍NOT NULL，表达式改为：

```sql
CASE WHEN record_type='project' THEN 'project' ELSE '' END
```

保留ELSE空字符串，不能把当前非Project行的值擅自改成NULL。只从Project payload字段集合、writer默认／必填／check路径和BusinessProject DTO删除anchor_type；Anchor.anchor_type、现有四元FK和父唯一索引保留。不加trigger、新表、应用侧替代约束或旧字段双读。

**Files:** current_schema_metadata、current_schema_declarations、business_definitions／business_storage所用当前字段、task_semantic_models及相关投影／Task Agent registry断言；单独的新paired release和精确上一版本catalog、store_schema_release及声明产物。实施时重新冻结准确生产源和目标SQL，不借此恢复已退休历史开关组合。

- [ ] 预检所有Project源值及原生类型均为TEXT project，且JSON为text；有差异在修改前拒绝。校验所有非Project现有值及外键、引用和分配水位。
- [ ] 正向仅去掉Project payload常量；逆迁按同一确定常量重建，其他字段与不相关payload保留。JSON外层键顺序不作为业务事实，参数等内层TEXT仍须精确。
- [ ] 保留wrong-parent-type、parent-retag拒绝、parent删除阻挡、无关记录更新及四元FK测试；新增当前writer拒绝已退休业务字段的测试。
- [ ] 当前生产派生副本正向→新增Project／Anchor／context→逆迁→准确旧代码冷启动读写→再正向；比较事实、类型、身份、序列及既有FK异常集合。
- [ ] 独立审查、标准gate／quiet／备份／部署，生产只读验收后按本workflow归属清理。不把231字节值或行数变化宣称为文件缩小。

## 集成、发布与清理

- 推荐先执行任务1–3：分别focused验证／SPEC与QUALITY审查，集成后只做一次标准完整gate和发布，避免每删一个helper重跑门禁；任务4单独发布。
- 实施前核对claims及其他发布负责人；不碰其已认领的重试、授权、审查或发布文件。正常合并主线，不重写他人提交。
- 任务1–3比较全部schema对象／hash和FK前后相同；生产读回安装版本、PID、health、前端stamp、Attention、History分页／详情／回执、邮件队列／详情／学习状态和Project当前／历史职责。
- 浏览器不可用时明确记录未点击项，使用实际API读取证据；不能把HTTP200或页面壳冒充内容验收。
- 测试对外发送始终替身。验收后重新核验本轮备份完整性、完成标记和精确代次，检查无打开引用并清理；保留其他任务新备份、生产库及原生trajectory。

## 本轮不删除的真实业务模型

- WorkProject／WorkTodo／WorkUpdate／TodoEvidenceCandidate仍有当前历史HTML／JSON和前端读取；不删唯一事实，不转成可执行Task，不按标题或整数碰撞合并。
- WorkTodo.import_task_id仍用于显式语义导入去重；删除BusinessLegacyLink DTO不等于删除这个owner。
- Run与RuntimeAttempt的状态／session／transcript是不同层次事实；没有本轮证据支持合并或删除。
- run_provenance.source_type／job_id仍承担实际专业运行和历史无Task运行关系。
- shared_action_lifecycle.transition的email_reply／email_unsubscribe分支有当前调用，不能按名字当成旧共同Action分派删除。
- 原始provider回执、历史独有失败、无Run来源以及明确上一版本的迁移／逆迁工具继续保留。

本轮没有发现可直接列入DROP清单的索引，也没有证明一批新的大体积正文重复。以上是经核对的下一批建议，不是全仓零兼容或全库最优证明。

## 前次审计与本轮执行范围

提交d2ffd0a49仅完成审计和计划；其只读统计及确切符号列表见[审计JSON](../../validation/2026-10-30-current-model-next-audit.json)。审计AST临时目录已清除，未创建数据库副本或备份。此后Derek明确批准本轮实施任务1–3、集成发布一次、生产读回和本轮产物清理；Task4 anchor_type迁移明确不执行。实施／审查／发布结果由本轮验收记录单独报告，不能把前次审计当作已实现证据。

## 任务1–3本轮实现／审查

5a2089750完成12个入口／DTO／helper退休及当前事实测试迁移；ae488a93b完成Email五组缺表及八处缺方法回退删除；91a5fcc7e与256f2a34b统一Project API／页面／decoder／当前dev mock职责读取。三项SPEC／QUALITY均通过；mock遗漏经实际middleware JSON RED／GREEN及限定复审关闭。当前完整声明463对象/hash与生产基线完全相同，没有DDL、版本、业务字段或anchor_type迁移。测试具体分组及首次失败／修正边界见[本轮验收记录](../../validation/2026-10-30-current-model-cleanup-release.json)。整分支审查已通过：唯一门禁清单遗漏已由c67a0d3ce加入现成Project HTTP回归文件并经限定复审关闭。标准门禁／单次发布、生产读回与产物清理尚未完成；不把局部测试称为已上线。
