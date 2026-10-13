# 第五期A：采用候选、审核与执行合入 actions

最终目标是2026-10-11-database-redesign-design.md的完整十表结构。此发布单元是第五期的第一段，不替代其余actions迁移或第6–8期。复用隔离工作树，基线源Store2026-10-12.1/Email51，原生轨迹只引用。

## 源单元与约束

六张源表：review_candidates、candidate_reviews、candidate_selections、candidate_supplements、candidate_executions、candidate_action_attempts。精确DDL/columns/default/PK/FK/index/trigger及元数据计数已冻结在../specs/2026-10-13-database-redesign-phase5a-inventory.json；没有外部入向声明FK，candidate predecessor→review形成内部环。

保留独立candidate→predecessor review，review→candidate，selection→candidate+approved review，execution→candidate+review+selection，attempt→execution的直接关系。共享parent不能吞掉交叉关系。各源PK空间保持public legacy定位，物理actions.id统一分配；原candidate/execution/selection唯一及attempt(execution,index)唯一、实际CHECK/default/type和六个task/run类型trigger语义不变。普通INTEGER PK不是AUTOINCREMENT；逆迁不编造原不存在的sequence。

字段完全来自实际源列。公共关系/状态/时间用可查询列，专用数据有有限的typed payload定义，需要查询/索引的值为受约束的生成列。禁止任意payload键、双写、永久旧表/view、SQL拦截器及正文fallback。候选JSON原始字符串及digest不变，不能重序列化内层业务JSON导致hash改变。独立external_action_results暂在本段保留，后续actions receipt迁移必须允许无candidate的回执。

## 实施步骤及验收

1. 独立存储模块：先RED，精确来源声明预检；一张actions目标DDL、类型投影、CRUD、公私ID映射、正迁、从当前数据逆迁。支持SQLite tuple/Row。不可表示或未知来源在任何持久修改前拒绝。测试空和非空selection、stage/predecessor环、类型碰撞、唯一/交叉FK、保留新写入/更新/删除的逆迁。
2. Mixin切换：读者使用明确SQL投影，写者使用actions helpers；保留审核绑定、末轮/代次、选择、租约、late receipt、verified独立ledger、顺序执行和无candidate全局receipt。原公开DTO/POST ID不变，不增加policy。
3. 所有直接SQL调用：store、audit_web、web_api/attempts、quality_gate、reply_task_deferral、rule_question_retirement、deploy_maintenance及验收脚本。没有source物理表依赖残留。源DDL留在精确逆迁证据，不能初始化重新建立六张源表。
4. 构造器/manifest与配对schema release同一版本：准确source14到新目标；混合/未知/额外对象拒绝；当前必要对象修复边界保留。发布异常逆迁保留发布后的业务事实，不覆盖旧备份。
5. 独立SPEC→QUALITY审核，各阶段有未解决问题不得发布。相关测试先跑，必要全量只在CI或隔离开发树并行；不得生产checkout运行。
6. 新线上一致副本：完整数据值/类型/ID/FK/回执比较，正迁→新事实→逆迁→实际旧代码启动→再正迁；发布失败阶段演练，正常constructor幂等。全部provider用替身，不回放真实任务/发送。
7. 完整CI、push/PR、正常app.deploy，读取PID/healthz/queue/Attention/History与受影响功能。只保留本流程最新已验证备份并清理拥有的临时副本。

## 本段尚未覆盖

全局回执、reply_attempts/sent_replies/消息effect、邮件plan/actions/attempts/退订steps/receipts、follow-up/sync/Memory与业务关联仍需第五期后续精确映射与迁移。第五期不得以本段六表合并宣称完成。第6–8期保持原目标和验收。


## 2026-10-13 实施检查点（未发布）

- actions 基础层、六类型有限字段与直接 FK、Mixin 和读写调用方、fresh bootstrap、准确前版迁移、配对 release/inverse 已接入隔离分支。旧六表 DDL 不再进入正常 bootstrap；没有永久兼容 view 或双写。
- 自动检查曾取得 97 项联合通过；随后单独三项 release 检查通过。扩大范围的 213 项发现 11 个失败：十个源于 scheduled 发布读取器未接入目标版本，一个来自旧表 SQL fixture。修正后相关 deferral/deploy 50 项重跑通过。尚未将这些结果概括为完整发布验收。
- 一致生产副本含 candidate541/review532/selection0/supplement3/execution141/attempt136，共1353行。初步 fresh constructor 正迁→配对逆迁→fresh constructor 再正迁通过，六类型按原列顺序和值计算的指纹保持相同；只保留计数和指纹作为证据，没有输出业务正文。源库没有写入，没有 provider 调用。
- 同一进程中的 initialized-path cache 不能证明回退后的启动；演练改用真实 release.publish 的新进程，未附带添加缓存恢复逻辑。
- 独立 SPEC 已确认准确源列/声明、类型字段、引用、公开 ID 和独立回执方向；发现 native-copy 脚本 import 顺序 P2，已修正并用无 PYTHONPATH 的 --help 验证。外部 view/trigger SQL 依赖旧表的修改前拒绝尚需补齐并重新审核。
- 下一步：完成依赖预检的回归及 SPEC 复核，再 QUALITY；冻结整体代码后进行全库等价、新事实/删除、实际旧代码启动、发布失败阶段与再正迁演练；CI及正式部署回读。其余 actions 家族、第6–8期和最终10+1生产结构仍未完成。


## 最终合并代码检查点（待CI/发布）

- 实现提交7a2070a2，合入当前main2d1e94的合并提交0169d055；没有重写其他会话提交。补充NoCase/完整bootstrap对象命名空间修复的QUALITY最终89项通过；foundation51项通过。
- 复用同一不可变生产源副本，最终137表值/类型/rowid、新增/更新/删除及独立回执、精确逆迁、实际2d旧代码新进程启动、再正迁均通过。311应用文件与证据完全匹配，最终receipt见evals/database_redesign/phase5a_copy_proof.v1.json。合并后154项相关测试通过。
- PR72已建立并附加本任务，CI正在执行。生产尚未变更，整个十表目标仍未完成。
