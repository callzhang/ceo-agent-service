# 第五期B验证记录

PR76已于2026-10-13正式部署到ebaf0d96，生产Store2026-10-13.2／Email51；以下离线证据与末节发布读回分别标明范围。

源 Store2026-10-13.1，目标 Store2026-10-13.2；Email51不变。生产副本132张物理表迁至129，三类来源分别336条全局回执、214条effect intent、115条execution receipt。实际字段值、SQLite类型、原六类型actions公开/物理ID与正文、126张非受影响表数据一致。全局回执按文本主键核对，隐式rowid不属于业务身份。

正迁、修改后的当前事实逆迁、重复逆迁、用当前实际生产f1377ab8旧代码新进程初始化回退数据库，以及重新正迁通过。源FK已有1项，迁移前后完全一致，未宣称全库零FK问题。测试仅数据库副本，无真实发送。

120项foundation/source/caller/paired-release定向测试通过。发布依赖、代码验证、部分restart、health、loaded五个失败点均验证先逆迁再启动旧代码并保留新回执。原allocator缺少写锁的并发回归实际RED；修复后12case覆盖两类型、普通/ignore、deferred/legacy autocommit/Python explicit autocommit。独立QUALITY复核通过，额外20个事务探针未发现调用方事务被提交。

运行调用方另138项定向通过；与120选集存在重叠，不相加。已移除source3运行时直接SQL；既有测试的旧表造数/读回换成新接口，原业务断言不变。另108项历史存储/fresh-target定向检查通过。集成SPEC独立22项以及10次源/目标publication读取通过；集成QUALITY已通过：独立72项与另23项bootstrap/factory检查通过（选集与前述存在重叠，不相加）。完整CI、正式部署和生产读回尚未完成。

简明证据位于 evals/database_redesign/phase5b_copy_proof.v1.json。完整10+1表目标仍未完成，不能将第五期B或Store版本号作为整体完成证明。

可重跑命令：`python -m scripts.validate_action_receipt_copy --source <数据库路径> --source-ref <实际前版commit>`。当前证据基于f1377ab85396394a9a4f7a3eca98431b9180f33f；详细proof包含126张表的行数/值类型哈希、三类回执摘要、源六类完整存储行摘要和5个实际代码文件SHA256。每次正迁使用新进程，避免路径初始化缓存掩盖逆迁后的启动。

强化副本演练覆盖：迁移后新增global和两类native事实、更新global原始JSON、删除global。逆迁后和实际旧constructor后逐项比对当前三类值/类型、水位、六类完整物理行、准确源DDL、原marker；重新正迁后再比对当前值/类型、水位和六类完整物理行。详细proof与脚本SHA256绑定。

独立集成QUALITY还验证generic发布回调、新进程构造、旧布局新增ID500、重复逆迁、重新正迁后下一ID501。repository proof保留原副本证明并附当前应用哈希；5个核心代码SHA256与当前文件全部匹配；结论仅代码和离线证据，不包括完整CI或上线。

真实Codex CLI副本批准链路通过：Consumer/Audit2轮，1次替身投递及回执，123张原非内部/非virtual表行保持。真实修订链路通过：4轮，首次return/reject后最终approve、1次替身投递和回执、done与输入正文精简、终态重建无需新调用。原生trajectory仅保留CLI-owned home，repository证据只含运行元数据。见phase5b_native_copy.v1.json和phase5b_native_revision.v1.json。

新增current-target fixture对齐：历史run receipt保留原始source SQL，通过typed writer装入fresh Store16；3项原旧表self-insert错误RED后修复。相关109项通过，另11项默认跳过需要显式副本；指定owned Store16副本后11个脚本化完整工作流均通过（模型输出为脚本角色，与上述真实CLI证据分开）。这次修正只改tests和验收文档，不改运行代码。

## 当前主线重新绑定

main/实际生产已推进到f1377ab8（OA field facts与return恢复）；已merge成9dc0ead9。151项receipt/caller/agent-turn/OA检查通过。实际f137旧constructor的完整副本正逆正重跑通过，仍132→129、126非affected表、原FK1。合并后真实CLI批准2轮、修订4轮均重跑通过，各1次替身发送和回执、123原非内部表保持。当前copy/native/revision evidence已替换成这轮证明，并附同一app Python/JSON文件哈希；不把较早978证明当当前发布证明。独立窄审确认14个main运行/测试blob原样保留，任务14条runtime/验证路径相对已审存储代码无diff。正式CI需对应这次合并的新head，尚未部署。

## 完整 CI 的历史构造修正

正式 CI38415291120 对3f7c0249运行，12,363项通过；7项失败和3项初始化错误均来自source13历史构造。共同helper只完成Workbench source13→14，却将数据库直接交给只接受准确source15→16的当前Store。修正只在测试构造链补充已独立验证的candidate actions source14→15迁移，并保留完整字段／ID及分配器比较；不扩大生产兼容来源，不削弱原业务断言。后续正式CI必须对应修正后的head；本次失败不作为通过证据。

修正已提交34c37699，非空回归包含原candidate原始TEXT、三类独立回执和删除后的高水位50，并分别检查准确Store15中间态及Store16当前态。root新鲜运行task/meeting/Workbench/Store来源案例与该回归，81项通过；此数不与前述选集合计。root逐项重新核对copy/native批准/native修订三份证明所绑定的326个app Python/JSON文件SHA256，均与当前代码匹配；此次fixture修正不改变已接受的副本及真实CLI运行代码范围。

该fixture修正独立SPEC和QUALITY均通过。QUALITY另填满六类actions及三类receipt，对全部字段值和存储类型、NUL原文、非JSON字符串、公开／物理身份和删除后分配器水位做13→14→15→16核对；新分配ID61／51符合保留的水位。故障注入确认两段迁移处于同一FK-off事务，异常时另一连接仍见已提交source13声明／marker，不会留下伪中间版本。此为测试构造质量证据，不扩大生产迁移来源。

## 正式发布读回

Quality CI38422492340在f799ae3f完整通过：12,374项Python检查、46个前端测试文件、构建及浏览器检查通过。PR76合并为ebaf0d96，正常app.deploy由f1377ab8推进到该提交，PID98276，production checkout干净。生产Store16／Email51、九类型actions manifest正确，旧三表为零，129张物理表／123张普通应用表。

最新已验证的Store15备份先完整性／FK验证，再原子替换旧日备份。发布后只读逐字段值和类型比对确认三类回执及旧六类型与该来源一致；原runs→tasks的一条FK诊断保留，未新增失败reply任务。健康、队列、Attention和History读取正常，核对时九条dispatcher队列pending/running均0、latest_error为空。History列表、代表执行15020、展开详情及Attention页面已实际打开；历史原生过程缺失显示不可用，未制造过程记录或测试真实发送。

详细回执见evals/database_redesign/phase5b_release.v1.json。读取时主文件471,347,200字节、WAL17,967,352字节、freelist8138页；没有执行VACUUM，不宣称物理文件已缩小。完整10+1尚未完成，后续C运行接入、剩余actions家族和第六至八期继续实施。

稳定发布读回完成后，日备份再次由当前Store16在线生成，完整性／原FK验证通过后才替换前版Store15副本；仅保留最新已验证日备份。前版备份到生产的字段比对证据已记录在release回执中。一次性B证明数据库／旧代码目录已完成验收并清理，保留仓库脚本及简明证明。
