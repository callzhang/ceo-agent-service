# Consumer 结果契约与续会话兼容性（2026-10-20）

## 观察与完成条件

生产任务 405687 的完整 Codex OAuth 轨迹显示：session `01a153fe-4a15-70d1-8c4a-10238f8679a1` 于 2026-10-19 11:48 UTC 建立；21:44:36 UTC 发布新的 purpose 输出后，22:03 UTC 的 Consumer 仍续用该 session。开场原生 Developer 的 wire 根字段是 `outcome/proposal`，新服务校验要求 `action/reason/target/payload/evidence_refs`。首次返回沿用旧字段，报必填字段缺失及多余字段。此后五次同 session 修正逐步产生不同格式或动作字段错误，直到第六次才通过；首次业务读取的两个 Skill、消息来源及动作合同均返回，修正轮没有新工具调用。证据能确认**首次失败的契约错位**，不能证明所有后续模型选择只有这一个原因。

该问题不是普通 Developer 文案改动：已保存旧 schema 与当前 parser 的字段集合相斥。当前会话筛选仅用已选 Skill 来源摘要约束复用，未核对原生 session 实际保存的 wire schema。PR #31 的原生输出方案仍是独立草稿，不能作为本轮部署前提。

本轮有限完成条件：

1. Codex Consumer 仅在原生会话开场保存的服务 wire schema 与本轮 purpose 的实际 schema 等价时复用；旧 schema、其他 purpose 或无法核实的 schema 改为该路线冷启动。普通 prompt 文案变化且 schema 相同仍复用；既有已选 Skill 来源匹配继续生效。
2. 新 session 仍收到完整当前任务事实和当前 Developer；结果仍走原有完整 Pydantic 与业务校验。Claude、Audit、路由、Skill 正文、外部动作及重试预算不变；不清除其他路线的会话或历史原生文件。
3. 回归先在旧代码失败，再通过。固定合成原生会话覆盖旧 schema、相同 schema、缺失 schema 和无关 Developer 文字；受影响的 Consumer、会话、native-input、turn-runner 测试及静态检查通过。复用已通过的匿名当前-schema 冷启动／同 session 案例，不因未变的模型输入重复调用；没有真实任务重放。
4. 独立审查后合并并按正式 quiet-wait 部署。回读新 PID、health、队列、Attention、History；Settings 当前预览仍是当前 schema，历史 405687 仍显示原输入。发布回读不等同于真实新任务的业务成功。

比较过的边界：每轮 Task 重送约 9.6 千字符完整 schema 会增加重复输入；按整个 Developer 指纹重置会让普通文案改动丢失会话。读取原生开场 schema 并只在不兼容时换会话，保留了已证实可复用的上下文。

## 本地验证

回归 RED：新增的四个定向测试先因缺少原生 schema 读取与 Runner 参数失败。最小实现后它们通过；Work Profile 中引用同名标题及原生前置 Developer 的边界用例也先失败再通过。生产原生文件只读核对返回旧根字段，与当前根字段不等价。

修改中的宽组检查在最后两个边界测试加入前完成：Consumer、Skill 会话、native-input 和 turn-runner 共 210 通过、4 跳过（481.93 秒）。独立审查发现原生 JSONL 中非对象记录会抛异常；新增回归先失败，修复后将无法核实的 schema 视为新会话。最终代码对旧／缺失／相同 schema、默认原生读取路径、非对象记录及现有 Skill 会话条件定向重跑，11 项通过。Ruff、`py_compile` 与 diff check 通过；独立审查确认没有剩余 Critical 或 Important 问题，并独立核对生产 run 385883 的错误与续会话范围。此前 Consumer 能力去重的十组匿名原生配对已覆盖当前 schema 的冷启动与同 session 后续轮次，且本变更没有改动这两轮的 Developer、Task、模型、工具或结果解析，故未重复调用模型。

## 生产回读（2026-10-20 UTC）

PR #167 已合并为 `c4f332417158384d8cb2bfbeb42efffd17f00a1b`。正式 `python -m app.deploy` 报告 `deployed 5a0cb051 -> c4f33241`；持久化操作 `deploy-60a85588-d171-4d07-a60a-dbfa5e593d2c` 为 `succeeded`，安装提交与目标一致，错误为空，所用数据库备份通过完成标记检查。生产 checkout 回读为该提交且干净；launchd 新 PID 为 `64733`，没有跳过空闲等待。

`/healthz`、Status、Tasks、Attention、History 均返回 HTTP 200。Status 显示服务运行、系统健康且当前 quality-gate violations 为 0；队列快照为 pending 8、processing 3、failed 9、retryable 0，Attention 4，History 总数 36,375，Tasks 总数 735。这些是回读时状态，不把既有失败归因于本次发布。

Settings 的 `codex_oauth` Consumer 当前公共预览提交状态为 `preview`，服务 Developer 长 40,376 字符，wire 根字段含 `action/reason/target/payload/evidence_refs`；历史 run `385883`／attempt `385884` 状态为 `invoked`，保留当时 37,442 字符的 Developer、23,883 字符的 Task，wire 根字段仍是 `outcome/proposal`。这验证当前与历史输入按各自来源展示；没有新的真实业务回合用于声称修复后业务判断已改善。GitHub Quality job 在回读时仍排队，行为验收依据为本地回归和独立审查。
