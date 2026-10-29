# 模型退休第五批：关闭历史转换闭包

状态：全部实施单元、独立SPEC／QUALITY／最终集成、精确4022bbe59标准发布和生产读回通过；本任务明确归属的临时产物及备份代次已清理／退休。

## Decision Runner 无 DDL 单元

删除 executor/_legacy_adapter 直接 CLI 执行及独占命令、超时恢复、错误封装 helper。
生产构造方本来就提供 Store，现在统一到已有 RoutedCodexExecution，decide要求真实
持久化 Run。保留当前结果解析、同会话修订、image 参数、审计事件、native范围与
ReviewedCandidate所需_finalize_decision。没有新兼容包装或审查／授权／重试政策。

旧分支专用断言退休，当前功能改用 routed替身；修订用例实际运行RoutedCodexExecution
并替换原生进程结果，检查两次命令、resume、image和纠正prompt。完整Decision文件41项、
Runner文件39项、两个确实不调用Codex的Worker节点、非live smoke构造节点1项通过。
未启用真实CLI/provider/live发送。Ruff和diff检查通过，独立SPEC与QUALITY均PASS；
审查为只读检查，未将它们误记为额外测试运行。

临时产物/private/tmp/ceo-runner-retirement.tzwA6t已在最终验收后确认无打开引用并删除。
Business/Email/Workbench转换半部与旧声明来源依赖、map生成器的后续单元见下文，
不宣称全仓库零兼容或数据库最优。实施期间保持发布串行，第四批后第五批均经标准流程完成。

## 显式前序声明依赖

Store28.2/Email54的463项精确SQL冻结为内容无关的版本目录，规范hash
8fe0b0d99c39c9bcab5b6637ba188677038cd620a72c78e7daa720a8d0d36b2a。
4022目标的Feedback发布/inverse/静默读取只引用该直接前代，目标28.3的全部SQL逐字不变；
普通constructor不得载入历史目录。生产仍保留必要显式当前数据inverse，不保留
运行时旧结构兼容。复制验证器只提取原生类型／rowid／水位比较与有界复制工具，
不再为了这些通用操作导入已关闭的旧迁移主程序。

相关source/target/新写入/inverse/reforward和cold guard15项通过。静默窗口中
一个旧用例仍拿25.2当受支持前代，改为准确28.2；保留运行Run计数及损坏声明
只读拒绝断言，整份drain测试33项通过。没有恢复旧选择器分支。

## Business 转换闭包

删除Business候选和旧导入owner的已关闭转换／inverse、独占校验和子表安装入口，
保留8个现行函数逐AST不变。final_action_schema仅有专用测试调用，连同该工具和
专用测试一起退休。移除候选projection的来源变体参数和已无调用的旧来源常量，
当前所有调用方原本均使用同一默认投影；正面DDL与463项SQL完整摘要不变。

当前原生候选／导入和语义导入70项验证通过（含exact schema）；SPEC发现两处
混合旧测试仍含有效当前断言，已补回FK-on目标删除/ID变更保护、WorkTodo删除
仍保留Task，以及任意status、非JSON文本、显式timestamp和SQLite类型比对。
最后两文件47项通过；这是重叠运行，不相加为独立总数。独立SPEC与QUALITY最终均PASS。三个归属测试临时目录已核实由实施者删除，无生产或备份操作。

## Email／Workbench 已关闭转换

删除Email capture/migrate/restore和独占来源校验、搬运、inverse；现行19个函数
及实际显式声明消费者逐AST不变。无测试定义的旧Email夹具模块退休，两个当前
值模板原样移入tests/support/email_native_fixtures.py；删除无调用current13夹具
与仅导入重导出的引用，不通过应用旧builder造数。

Workbench只供旧source13的转换／inverse／探测及6个转换专用测试退休；未调用
的整链旧caller夹具同步删除。当前文件生命周期、原生父子、API、运行与回执
保留。两个仍被明确source工具使用的原有辅助声明函数与FTS声明保留，未为
测试新增兼容入口。

相关Email／Workbench230项、6个直接生命周期节点、精确463项catalog摘要通过；
有重叠不累加为全仓覆盖。独立SPEC和QUALITY均PASS。Ruff和diff／引用
闭包检查通过，实施者清理的临时根已由根代理确认不存在。未改DDL或生产业务。

## Run 映射生成器与过期发布闭包

退休Run／Adoption旧字段全集映射生成器及其专用再生成测试；当前正面DDL、
采用结果、receipt约束、四张唯一身份map和现行coalescer／来源事实保留。
将11项当前receipt SQL固定为正面声明，逐字符串等于基线生成结果，不再从
旧execution_id声明作字符串替换；没有改变任何owner、lineage或不可变约束。

删除9个已关闭release/source模块、3个旧copy工具及其专用迁移测试和3份无调用
旧schema JSON。只保留现行Feedback28.2→28.3显式发布／当前数据inverse；
历史Action测试实际需要的45项Configs源声明冻结在测试目录，不进入应用。
更早505工件／旧映射再生成案例退休，当前Signal与receipt测试继续验证实际owner。
未知版本拒绝用准确28.2fixture加明确的synthetic非法marker，检查报错后文件字节不变，
不再为了构造一个拒绝用例保留整个旧运行结构。

最新Feedback前后迁移、当前新增事实和ledger读写/inverse均保留；Run／assembly36项、
Feedback10项、声明／Signal／receipt16项、Configs历史codec3项、前序替换20项、
drain完整33项，以及最后receipt／组装／cold／Feedback42项通过，存在重叠。
独立SPEC和QUALITY最终均PASS。18个被删Python文件在app/tests/scripts/tools
的import闭包无残留，gate全部路径存在。当前463项target完整hash及前代source
hash保持冻结值。未对其他有实际显式消费者的历史Action／Email工具作全仓退休宣称。

后一个42项运行使用明确归属临时目录并已确认删除。该单元先前pytest采用默认
临时根；目前剩余根无法归属于本次工作，保留其他任务目录，不用测试名或时间猜测删除。
根代理另建/private/tmp/ceo-retirement-final.xv4Wg9用于最终集成，验收后按归属清理。

QUALITY发现已退休专用文件里有一个真实当前回归：schema重解析与Action public ID
编译测试。已将原两次ALTER和EXPLAIN UPDATE原样迁入当前声明测试，完整7项
通过；两位reviewer复查PASS。不能因文件名旧就删除仍覆盖当前触发器的断言。
根代理当前Task／Run／ReviewedCandidate／发布接口集成61项通过。

本批所有实施单元通过审查；已正常整合主线，精确提交完整标准发布通过，未绕过安静门槛。

## 精确最终代码的当前数据副本复验

7c654734a代码于2026-10-28T11:21:57Z完成最新生产派生副本循环：49张原生
表（含sequence）typed事实、身份及现有水位不变；公开API创建/claim/bind/patch/
resolve/reopen/再claim，inverse保留新增轮次结果回执，真实af24旧代码冷读/冷写，
再forward和当前代码再次读写通过。真实旧代码归档465个文件再次逐字核对一致。
源代码各层已退休依赖不会影响显式当前inverse或旧代码冷启动；fixture拒绝网络和
CLI，没有生产测试任务或实际发送。详见第五批JSON内完整逐表摘要；临时副本已在读回后按明确归属清理。

最终独立集成审查PASS：23个已删Python模块无直接／动态引用，405个gate路径均存在。
门禁/安静等待/备份验证/部署/生产读回仍走标准流程，不因本地通过跳过发布条件。

## 标准发布与生产验收结案

准确4022bbe59c5d258ab1a1a48d8a96fa6f7669e8d2经标准完整gate、自然quiet、
备份与rollback流程于2026-10-28T11:54:07Z成功安装，operation
deploy-ca69085b-4289-4acb-a5f3-668c8311dba9。运行代码来自独立冻结publish工作树，
不让另一任务已推进到28.4的开发声明混入本次28.3目标；没有skip、force或生产测试任务。
实际PID89202/health200/frontendstamp匹配；463项完整SQL仍ec77…129a0a、FK[]。

当前正式Task列表20行及Task862来源证据可读；History实际下一页／已完成筛选生效，
Attempt16220仍显示receipt20893及All approved actions verified。Feedback API保留
46条无当前Round但来源已解决事实，时间逐值相等；前一发布已实际展开Feedback处理
历史和已处理筛选。本次所有生产操作只读，没有处理／重开／同步／重发或发送测试。

summary failed26保持不变；发布前19个History失败身份全部保留，新增400305是运行
Task417755的会议Audit正常退回，Run记录retry，执行及Audit RuntimeAttempt均completed，
无SQLite/FK或native transport失败。该业务修订不能被静默删除或伪装成全绿。
Attention1来自此前已进入needs_human的OA417753；原反馈批次未解决导致degraded1
单独保留，native可读381／不可用0，未改变业务判断、Audit或重试政策。

## 后发并发版本与清理边界

另一任务随后于12:03:28Z标准发布3f126d1ad0cf8830eb68bd0168723d51f8792580，
新增独立WeChat转写缓存的Store28.4，已包含4022；本任务没有重复部署或降级。
再次独立读回当前PID10517/health200，完整464项schema逐字等于声明、hash
f8fa683f9b1ab7e77ebd076686eb2969c77cfbc36e0be3cb62fed4738038b78b、FK[]。
已退休入口／直接executor／source_variant分支仍不存在。这份后发DDL不属于本批
“无DDL”的收益或迁移验收，不冒称由本任务实施；不修改该任务认领的迁移发布文件。

本任务514969600字节备份代次inode832838274已被后发workflow经验证的原子replacement
退休，backup目录已无该inode。清理护栏发现生产SHA和inode代次变化即退出，没有误删
后发文件。后发832916088代次已只读重验CEOB、integrity、完整schema/FK和成功operation，
仍交其owner完成自身验收／清理；不把它算作本任务残留副本，也不宣称全项目备份目录为空。

本轮publish raw工作树正常unregister后，最终测试根3458文件/693950960字节、Runner
测试根5文件/6447字节均在无打开引用核对后删除，路径不存在。第四批已另清理7234文件
/972661524字节、准确旧备份及编辑脚本。保留生产DB、原生trajectory、提交的简洁证据；
不删除无法归属的其他任务默认pytest目录，不把临时清理当数据库容量缩减。

五批批准的实施与验收工作完成。仍存在的真实业务needs_human／Audit修订及其他任务
发布验收分别按其owner推进；不宣称全库最优或全仓库零历史工具。
结构化证据：[第五批JSON](../../validation/2026-10-28-model-retirement-batch5.json)。

## 2026-10-29 最终清理复核

2026-10-29T07:54:35Z（America/Los_Angeles 00:54）持 backup 目录锁重新核对：
10/28 daily、第四批与第五批的 before-upgrade 精确路径及其 SQLite 附属文件共12个
均不存在；本任务及后发3f备份旧代次 inode832838274/832916088 也已不在备份目录。
上述“后发备份交 owner 清理”是先前时点记录，该文件现在已不存在，清理待确认项关闭。
本次未执行删除，也不推测原文件由哪个后续操作退休。

本轮10个明确临时路径均不存在，两个 raw worktree 注册也已不存在；生产数据库
继续保留。核对结果已同步发布负责人线程，未要求重启、部署或重新执行业务；其
最新回复尚未单独确认备份清理，因此本结论采用目录锁内的独立路径与代次证据，
不把发送协调消息冒充对方确认。

目录当前仅有后续10/29 workflow 的 daily，inode836951240，mtime_ns
1793258639864404224，514969600字节；本次保留，不将其算作10/28遗留，
不宣称整个项目备份目录为空。结构化完整路径见第五批 JSON 的
cleanup.final_absence_verification_20261029。

本次仅更新归档验收记录和结构化证据，没有新增开发、DDL、测试数据库、部署或发送。
批准的本轮模型退休与清理收尾完成；后续独立 workflow 的备份生命周期另行归属。
