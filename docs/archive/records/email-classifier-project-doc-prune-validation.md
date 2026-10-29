# 邮件分类器项目文档输入精简验证

## 问题与边界

生产邮件分类器的两条完整原生 trajectory（attempt `382562`、`380723`）均包含一个
6,670 字符的 `AGENTS.md` User 消息，此外才是本轮分类输入。两次判断都没有调用工具。
分类器只需冻结的分类 Skill、当前邮件事实、允许类别和候选退订入口；仓库项目说明并不提供
邮件归类证据。这里仅对分类器 Codex 调用设置原生 `project_doc_max_bytes=0`，
不改其他任务、Skill 内容、路由、结果契约或退订执行。

验收条件：分类器本轮服务输入与 Developer 指令保持不变，原生项目文档段减少；
工作邮件和无关推广仍归入原类别；带退订候选的垃圾邮件仍只返回索引，并由服务绑定
本轮精确 URL；所有结果满足严格结构，工具调用为零。正式发布后还需核对服务版本、
健康、队列、Attention 和 History。没有自然出现的新邮件分类时，运行状态回读不能
代替真实邮件结果。

## 改动前后的证据

同一目录、同一合成任务的 `codex debug prompt-input`：默认项目相关 User 段
11,254 字符，设置 `project_doc_max_bytes=0` 后为 4,380 字符，减少 6,874 字符；
Developer 段和任务段哈希分别保持不变。剩余 User 内容属于 CLI 的其他自身上下文，
不能将它算作服务分类 prompt 的删减。

回归测试先在旧调用上失败（`project_doc_max_bytes=None`），改为 `0` 后分类器
测试 29 项通过。匿名、只读的固定对照以相同 `gpt-5.6-luna`、相同冻结分类 Skill
和相同服务 prompt，比较默认输入与精简输入；工作邮件、无关推广、带一个虚构退订
候选的推广各重复两次，共 12/12 个结果通过结构解析、预期类别和候选索引绑定；
总工具调用 0。测试没有连接邮箱、业务 MCP 或执行退订。

固定案例及可复跑脚本：
`evals/trajectory_optimization/email_classifier_skill_once.native.cases.v1.json`、
`evals/trajectory_optimization/email_classifier_project_doc_native.py`；
匿名逐次结果保存在 `evals/trajectory_optimization/email_classifier_project_doc.native.v1.json`。

## 发布回读

PR #155 合并为 `26e50f8d8b1c091aba46171399bbc85925168dc7`。正式部署使用
`python -m app.deploy` 等待在途工作归零后，把生产 checkout 从 `51781f1c`
前进到该提交；生产源码回读确认分类器调用点设置 `project_doc_max_bytes=0`。
新 PID `90276` 存活，`/healthz` 返回 `ok`；状态接口有 13 个队列、处理项 0、
在途计数 0、系统健康违规 0。Attention 为 4 条，更新时间都早于本次部署；
History 返回 HTTP 200。回读时最近一条真实邮件分类仍是部署前的 attempt
`382562`，因此还没有生产自然邮件的行为结果可核对。

PR 的 GitHub Quality 在 `npm run lint:local` 阶段失败，原因是
`tests/test_agent_role_tool_boundary.py` 中未使用的 `AgentReadOnlyViolationError`
import；相同行已存在于本 PR 的基线 `origin/main`，不在本次差异内。
这项检查结果记录为仓库基线缺口，不替代上述定向测试和匿名行为对照。
