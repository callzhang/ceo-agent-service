# 模型退休第五批：关闭历史转换闭包

状态：分单元实施中，尚未部署，不能据此标记整个计划完成。

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

临时产物：/private/tmp/ceo-runner-retirement.tzwA6t，待最终发布／读回后核实并清理。
Business/Email/Workbench转换半部与旧声明来源依赖、map生成器的后续单元见下文，
不宣称全仓库零兼容或数据库最优。当前生产发布由第四批独占，不能并行部署本单元。

## 显式前序声明依赖

Store28.2/Email54的463项精确SQL冻结为内容无关的版本目录，规范hash
8fe0b0d99c39c9bcab5b6637ba188677038cd620a72c78e7daa720a8d0d36b2a。
当前Feedback发布/inverse/静默读取只引用该直接前代，目标28.3的全部SQL逐字不变；
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
最后两文件47项通过；这是重叠运行，不相加为独立总数。独立SPEC最终PASS，
QUALITY均PASS。三个归属测试临时目录已核实由实施者删除，无生产或备份操作。

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

本批所有实施单元现已通过审查；尚需正常主线整合与精确提交完整发布gate。
第四批仍持标准发布锁等待在途业务，不并行部署第五批、不绕过安静门槛。
