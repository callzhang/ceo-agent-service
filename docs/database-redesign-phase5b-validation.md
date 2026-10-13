# 第五期B验证记录

当前为隔离 worktree 开发和生产数据库副本验证，未上线。

源 Store2026-10-13.1，目标 Store2026-10-13.2；Email51不变。生产副本132张物理表迁至129，三类来源分别336条全局回执、214条effect intent、115条execution receipt。实际字段值、SQLite类型、原六类型actions公开/物理ID与正文、126张非受影响表数据一致。全局回执按文本主键核对，隐式rowid不属于业务身份。

正迁、修改后的当前事实逆迁、重复逆迁、用实际生产978f825d旧代码新进程初始化回退数据库，以及重新正迁通过。源FK已有1项，迁移前后完全一致，未宣称全库零FK问题。测试仅数据库副本，无真实发送。

120项foundation/source/caller/paired-release定向测试通过。发布依赖、代码验证、部分restart、health、loaded五个失败点均验证先逆迁再启动旧代码并保留新回执。原allocator缺少写锁的并发回归实际RED；修复后12case覆盖两类型、普通/ignore、deferred/legacy autocommit/Python explicit autocommit。独立QUALITY复核通过，额外20个事务探针未发现调用方事务被提交。

运行调用方另138项定向通过；与120选集存在重叠，不相加。已移除source3运行时直接SQL；既有测试的旧表造数/读回换成新接口，原业务断言不变。另108项历史存储/fresh-target定向检查通过。集成SPEC独立22项以及10次源/目标publication读取通过；集成QUALITY已通过：独立72项与另23项bootstrap/factory检查通过（选集与前述存在重叠，不相加）。完整CI、正式部署和生产读回尚未完成。

简明证据位于 evals/database_redesign/phase5b_copy_proof.v1.json。完整10+1表目标仍未完成，不能将第五期B或Store版本号作为整体完成证明。

可重跑命令：`python -m scripts.validate_action_receipt_copy --source <数据库路径> --source-ref <实际前版commit>`。本轮基于978f825dd21329e4db8faa62968ddf025a671435；详细proof包含126张表的行数/值类型哈希、三类回执摘要、源六类完整存储行摘要和5个实际代码文件SHA256。每次正迁使用新进程，避免路径初始化缓存掩盖逆迁后的启动。

强化副本演练覆盖：迁移后新增global和两类native事实、更新global原始JSON、删除global。逆迁后和实际旧constructor后逐项比对当前三类值/类型、水位、六类完整物理行、准确源DDL、原marker；重新正迁后再比对当前值/类型、水位和六类完整物理行。详细proof与脚本SHA256绑定。

独立集成QUALITY还验证generic发布回调、新进程构造、旧布局新增ID500、重复逆迁、重新正迁后下一ID501。详细proof逐字节与临时文件一致，5个代码SHA256与当前文件全部匹配；结论仅代码和离线证据，不包括完整CI或上线。

真实Codex CLI副本批准链路通过：Consumer/Audit2轮，1次替身投递及回执，123张原非内部/非virtual表行保持。真实修订链路通过：4轮，首次return/reject后最终approve、1次替身投递和回执、done与输入正文精简、终态重建无需新调用。原生trajectory仅保留CLI-owned home，repository证据只含运行元数据。见phase5b_native_copy.v1.json和phase5b_native_revision.v1.json。

新增current-target fixture对齐：历史run receipt保留原始source SQL，通过typed writer装入fresh Store16；3项原旧表self-insert错误RED后修复。相关109项通过，另11项默认跳过需要显式副本；指定owned Store16副本后11个脚本化完整工作流均通过（模型输出为脚本角色，与上述真实CLI证据分开）。这次修正只改tests和验收文档，不改运行代码。

## 当前主线重新绑定

main/实际生产已推进到f1377ab8（OA field facts与return恢复）；已merge成9dc0ead9。151项receipt/caller/agent-turn/OA检查通过。实际f137旧constructor的完整副本正逆正重跑通过，仍132→129、126非affected表、原FK1。合并后真实CLI批准2轮、修订4轮均重跑通过，各1次替身发送和回执、123原非内部表保持。当前copy/native/revision evidence已替换成这轮证明，并附同一app Python/JSON文件哈希；不把较早978证明当当前发布证明。独立窄审确认14个main运行/测试blob原样保留，任务14条runtime/验证路径相对已审存储代码无diff。正式CI需对应这次合并的新head，尚未部署。
