# 第五期B：独立回执与原生执行事实合入 actions

本期在第五期A发布并回读完成后实施；不会把B的未验证修改并入A发布。完整10+1目标保持不变。

## 已查实的源边界

三张表：external_action_results、agent_effect_intents、agent_execution_receipts。准入源版本2026-10-13.1；准确的post-A actions声明、三表PRAGMA/DDL/index/trigger/FK及序列见phase5b-inventory.json（来自已验证的生产派生副本，只含结构）。实际生产旧版独立只读调查为330/214/115条，随运行增长，不用跨快照计数作删除标准。

全局回执的主键是TEXT external_action_key，没有integer id；219条没有candidate attempt。intent与execution receipt没有彼此FK，52条receipt没有匹配intent；4条intent的非空external key没有global receipt。这些不是丢失关联，不制造candidate、intent或业务对象父记录。

native_reply_dispatches不进入此发布单元：它的(channel,delivery_key)复合FK实际指向outbound_postfixes，必须与postfix及postfix receipt一组迁移。sent_replies也另行迁移；其2021个agent_run_id=0是无声明FK的旧事实，不能套用本组三表的有效run FK。

## 准确字段映射

external_action_results：external_action_key作为精确TEXT legacy身份，保留原同名公开字段/索引查询；business_object_key、action_identity、operation、target_identifiers_json、provider_result_json、result_digest、created_at保留原含义，first_agent_run_id直接runs FK及原run类型约束。原JSON正文是TEXT，没有内层JSON-valid CHECK，不额外添加。原PK的PRAGMA notnull=0是真实SQLite TEXT-PK空值特性，不能虚构为NOTNULL；当前NULL/空key为0。无法表达的源身份在修改前明确拒绝，不伪造编号。

agent_effect_intents：id是INTEGER PRIMARY KEY AUTOINCREMENT；agent_run_id直接runs FK。authorization_id、action_index、receipt_operation_id、capability、operation、operation_digest、arguments_digest、target_identifiers_json、state、result_digest、exit_code、prepared_at、dispatched_at、acknowledged_at、updated_at、external_action_key均保留原类型、NULL/default/枚举/唯一与索引。exit_code nullable，action_index>=0；state prepared/dispatched/acknowledged。external_action_key仅标量，不新增到global receipt的FK。

agent_execution_receipts：id是INTEGER PRIMARY KEY AUTOINCREMENT；agent_run_id直接runs FK。receipt_id、operation_id、cli、command_path、command_digest、exit_code、completed、persisted、safe_to_confirm、created_at、effect_counted全部保留。三个integer flags没有原boolean CHECK，不能臆造。保持(run,operation_id)唯一。两类旧AUTOINCREMENT水位560/115需要跟随后续成功/忽略插入和删除保留，不能用max(现存ID)+1假冒。

## 执行顺序与删除清单

1. 独立源oracle RED：文本key（含前导零/大小写差异）、run类型/FK、独立无candidate回执、NULL/default/原唯一与CHECK、AUTOINCREMENT已删除高水位/ignore/上界，Row/tuple，以及外部依赖NOCASE拒绝。
2. 扩展当前actions有限type定义为九种：保留A六种当前值/物理ID，TEXT identity使用精确匹配与约束；增加两项真实run FK列，查询列由原字段生成。不得任意payload、统一parent替代交叉关系、SQL拦截器、compatibility view或双写。
3. 本组三表API和SQL调用方原子切换：reviewed_candidate_store、store、reply_task_deferral、deploy_maintenance、scheduled_task_recovery、native-copy harness。历史intent/exec receipt没有当前writer但有恢复读取，不能当空功能删除。global receipt writer保持原幂等与冲突语义。
4. 删除三张旧物理表及其索引/trigger；保留所有必要事实。原AUTOINCREMENT来自现有sqlite_sequence元数据，可沿用已用的sequence工具，不能新增一张业务表或把完整数据放入migration state。源B动作结构只能在准确源A结构上扩展，未知字段/约束/依赖先拒绝。
5. 新marker与完整manifest/bootstrap、显式发布路由及配对逆迁同一单元。逆迁重建A动作声明与三表当前数据，保留新的独立receipt、更新、删除和分配水位；不恢复陈旧DB。
6. SPEC→QUALITY、targeted checks、actual source副本全表/类型/身份/FK/回执比对、新事实/删除/逆迁/实际旧代码/fresh再正迁、五发布失败阶段、完整CI；通过后正常deploy与PID/health/queue/Attention/History回读。对外发送只在业务运行，测试保持替身。

该计划不替代其余actions和第6–8期；只保留已有字段和已批准结果/回执，不新增政策或不存在的实体关系。

## Independent source-oracle preparation while Phase5A CI runs

Captured source declarations are exercised directly in tests/test_action_receipt_source.py;14 tests pass without target helpers. Measured contracts include BINARY text keys (leading zero/case preserved), multiple legal source NULL TEXT primary keys, blank rejection, raw non-JSON strings, logical-run insert/update guards, missing-run FK rejection, both intent unique keys, no invented cross-receipt FK, integer flags outside0/1, defaults, deletion/ignored-insert AUTOINCREMENT watermarks and SQLITE_FULL at the upper bound. These are source semantics, not evidence that the target receipt migration exists. Phase5A runtime and CI head remain untouched.
