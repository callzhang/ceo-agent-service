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
剩余Business/Email/Workbench转换半部、旧声明来源依赖与map生成器仍在继续核对，
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
