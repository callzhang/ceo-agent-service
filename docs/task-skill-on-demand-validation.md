# Task Work Tracking Skill 按需读取验收（2026-10-19）

## 问题和边界

完整原生 Task trajectory 显示，同一逻辑 session 的相邻输入重复提交大量历史上下文，且多数相邻轮次之间发生 native compaction；因此不能直接省略历史证据。另一处独立、可精确界定的重复是 `build_task_agent_prompt` 每轮内联当前 23,627 字节的 `ceo-work-tracking` Skill。该 Skill 文件已经由服务选定，本机 Codex/Claude CLI 可只读访问其绝对路径。本机 CLI 路线每轮收到绝对路径与源文件 SHA-256；冷启动读取全文；续会话仅在相同版本全文仍在当前上下文时复用，否则重读。远端 Friday Runtime 无法读取服务本机文件，继续收到完整 Skill 正文。Skill 文件、业务判断、来源上下文、native schema、session 及路由保持原样。此前同名旧副本的目录项仍由现有 `inlined_skill_names` 抑制。

验收要求是：文件更新能改变提示中的版本摘要；冷启动实际读取所选全文，版本变化后的续会话重新读取；固定匿名 Project 建立和风险续会话保留负责人、Project 事实、候选 Task、Attention 及原文引用；原生结果通过既有 Pydantic/应用流程。仅缩短服务提交字数或进程成功都不算通过。

## 证据

- 新的 prompt 回归先失败于缺少精确 Skill 路径，修改后通过；新增版本变化断言也先失败于缺少摘要，修改后通过。旧测试里要求 Skill 原文出现在 prompt 的断言已改为核对所选文件及其中的业务规则，其余业务断言保留。远端 Friday 完整正文交付的回归先失败于缺少路线覆盖，修改后通过。审查发现对已拼接文本做首匹配替换会吞掉包含同一标记的定时任务专项内容；现在构造时显式生成两个版本，固定案例保留标记前后内容，并核对 Friday 路由使用完整版本。最终五份受影响测试共 287 项通过；Ruff 与 `git diff --check` 通过。
- 固定案例用 `task_skill_on_demand_runner.py` 对照完整内联与按需读取，两个独立匿名 Store、同一 Codex OAuth 路线、模型、输入、时间、schema 和只读工具环境，各做冷启动与同 session 风险续会话。完整内联的服务输入为 39,293 / 37,911 字符；按需读取为 14,764 / 13,367 字符，分别少 24,529 / 24,544 字符。四轮保存的服务输入与原生 `task_prompt` 逐字相符。
- 两个分支均完成 Project 1、Task 0、Attention 0，续会话后 Project 1、Task 1、Attention 1；Task 为未正式承诺的候选、Attention 为 active，Project 总负责人陈明、回款排期责任人赵华，候选 Task 都针对先确认客户验收材料再落实回款日期。Project/Task/Attention 文字有正常措辞差异；两边从 Project context、候选与 Attention 提取的 9 条引用均指向这两份匿名来源中的连续原文，未出现坏引用。两边的原生 Attempt 均 completed，结果经原有 schema/Pydantic 和 Store 应用，SQLite quick_check 为 ok。
- 按需读取分支冷启动原生轨迹分两次读取所选 Skill 全部 362 行；相同版本的续会话复用了已有上下文，没有再次读取。另一个匿名冷启动后，仅向 Skill 末尾添加无业务规则的版本标记；同 session 风险轮看到新摘要，原生轨迹执行 `sha256sum` 并重新读取全文，应用结果仍为 1/1/1。
- 独立 Claude OAuth（当前备用路线）匿名只读探针使用 `sonnet`，仅开放原生 `Read` 工具、关闭 MCP，在临时目录读取同一份 Skill 副本。原生轨迹有一次指向精确绝对路径的 `Read` 调用；工具返回含文件首尾且未截断，终态无错误。这验证了本机路径的可读性，不等于 Claude 路线的完整 Task 业务案例通过。
- 改成显式双版本构造后，仅补跑受影响的原生固定案例：完整内联 arm 冷启动仍为 39,293 字符并得到 1/0/0；按需读取 arm 冷启动/同 session 风险轮为 14,764 / 13,815 字符，得到 1/0/0 → 1/1/1，Task 标题保留“验收材料”和“客户回款日期排期”。两轮 10/15 条来源摘录均为匿名原文连续子串，原生输入与服务提交文本逐字相同，Attempt 均 completed，数据库 quick_check 为 ok。冷启动读全 Skill、相同版本续会话复用读过的正文；原先完整 arm 风险轮及版本变更轮的充分证据继续复用，没有因其代码未变而重复运行。

匿名案例来自 `evals/trajectory_optimization/task_schema_titles.release.cases.v1.json`；精简的成对结果与原生 session 定位见 `evals/trajectory_optimization/task_skill_on_demand.results.v1.json`。

以上字符数是 Codex 路线的服务提交输入长度，不是计费 token 或延迟节省；读取全文的冷启动仍会消耗模型上下文。远端 Friday 保留原完整输入，经单元回归检查；当前配置的四条路线没有 Friday。文件 SHA 是版本提示，不是不可变快照；轨迹已验证跨轮更新，尚未验证构造提示和读取之间恰好发生的文件变更。匿名对照不证明所有自然 Task 决策无回归，也未强制触发一次 native compaction。发布前还需完成代码审查、受影响测试与文档核对；发布后必须读回真实 Task 原生轨迹，确认 compaction 后能重新读取当前版本并保持业务判断。本报告不把匿名成功称为生产行为。
