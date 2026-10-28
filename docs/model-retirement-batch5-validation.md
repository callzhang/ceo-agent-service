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
