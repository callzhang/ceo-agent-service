# Reply Worker Reliability

本文档描述 Consumer Agent A / Audit Agent B 回复链路的当前可靠性契约。服务负责任务队列、角色编排、租约、
失败重试和结果持久化；业务读取、外部动作以及动作是否已完成由 Agent 按适用 Skill 负责。

## 核心不变量

1. Producer 只发现触发并把精确 source revision 入队。
2. A 代表 Derek 读取上下文、理解业务并提出候选；B 独立审阅并执行 accepted action。
3. 同一业务对话复用一个 A session；每个候选 revision 使用独立 B session。
4. 同一任务最多两个内容反馈周期，基础设施重试不计入该上限。
5. 完全相同的 source/generation/revision 不重复执行；反馈改变业务内容后形成新 revision。
6. 运行、依赖、解析和 provider 错误统一记录为 `failed`，按统一退避重试或进入明确终态。
7. Codex session JSONL 保留详细执行记录；SQLite 只保存任务投影和去重所需的 provider 事实。
8. 证据不足但参与者可以回答时，A 提出一个具体澄清问题；只有规则缺口才返回 `needs_human`。

## 任务、generation 与 revision

`reply_tasks` 是持久工作队列。每个 trigger 使用稳定来源身份；同一来源内容没有变化时不会产生重复任务。
显式重跑创建新的 execution generation，但仍复用业务对话的 A session。

一个 generation 内，A 首先产生 proposal revision，B 独立审阅并执行或反馈；A 根据反馈生成下一 revision。
旧 run、反馈和 session 记录不可覆盖，当前任务 projection 可以由后续 revision 更新。达到反馈上限仍不能
解决时返回 `needs_human`，不无限循环。

## 会话生命周期

每个 `conversation_id` 绑定一个 A Codex session，新消息通过 `codex exec resume` 追加。A session 缺失或损坏时
才创建新会话；服务不为每条消息无条件创建新 session。每个候选 revision 使用独立 B session。

服务保存 run、revision、session、租约和最终 typed result 的关系。工具调用、命令文本、Skill 读取和工具输出
属于 Agent/runtime 执行记录，不构成应用层审核条件。

同一时刻只允许一个 A turn 更新该 session。服务使用短期 transcript 锁保证 JSONL 顺序；后续消息保留在队列，
不因锁存在而丢失。

## 缺失事实与人工判断

- 可通过交流补齐的事实：A 生成一个具体澄清候选，由 B 按正常流程发送。
- 不可约管理判断：现有 Skill 无法给出规则，且仍需要 Derek 决定价值取舍或管理立场时，返回 `needs_human`。
- 权限、网络、CLI、材料读取和 provider 失败：记录真实依赖错误并进入 `failed`，不得包装成管理选择。

## Agent 与服务边界

Service 不审核 Agent 使用的命令名称、MCP 工具、Skill、读写模式或“未知工具”，也不要求纯读取 receipt。
A/B 角色和结果协议决定职责：A 只应读取、分析和提出候选，B 审阅并执行 accepted action。

Service 只校验最终 typed result 的结构，并在 provider 返回时保存最小的：

```text
operation
 target
provider_result_identifier
```

这三个值用于把后续处理绑定到同一个外部对象并防止重复执行；它们不是业务审核结论，也不触发命令门禁。
Provider 没有返回稳定标识时记录失败，下一次 Agent turn 通过业务 Skill 读取当前目标状态后自行判断。

## 外部动作与重试

每个候选动作绑定 task generation、proposal revision、operation 和 target。完全相同 revision 已有 provider
result identifier 时，Agent 不再重复动作；正文、目标或参数改变形成新 revision 后可以重新审阅和执行。

外部动作中断、回执缺失、读取失败和结果解析失败均按 `failed` 处理。服务不创建 `unknown`、`reconciled`、
`pending_reconciliation` 或 `side_effect_state` 状态，也不启动单独的 reconciliation 回合。下一次 Agent turn
按当前业务 Skill 正常读取外部状态，再决定继续、修正或终止；服务不根据工具事件替 Agent 作业务判断。

会议投递还有一条领域边界：多人会议必须由 Agent 明确选择首个候选群，服务不会替 Agent 猜测目标。
只在所选群已被权威会话信息证明不可发送时（例如 `singleChat=true` 或成员数为零），服务才允许
改用已唯一解析的会议创建人私信；群发现失败、网络失败、元数据缺失或不一致时不猜测收件人，
保留可恢复重试。Agent 在完整群发现后也可以显式选择创建人 direct target；这与服务层对已选群
不可发送的回退是两条不同路径，均不能把不完整证据当成发送依据。

## 持久化

Codex session JSONL 是详细审计来源，保存提示、工具调用、输出和结果。SQLite 保存：

- task/generation、角色、proposal revision 和父子 run 关系；
- A/B session ID、transcript 行范围、run 状态、租约和下一次可用时间；
- provider 返回的 operation、target、稳定 result identifier（若有）；
- 结构化最终结果和精确去重键。

原始参数、完整工具输出和 Skill 内容不复制到 SQLite。旧数据库中的 `unknown`、`reconciled`、
`side_effect_state` 值仅作为不可变历史事实显示，新代码不得写入，也不参与当前状态迁移。

## 进程与租约

生产只运行 `com.ceo-agent-service.main`，由 supervisor 管理 worker 和 audit-web。每个 Agent run 和 reply task
都有租约；有效流式进度会续租，stale sweep 只回收没有有效租约的任务。进程崩溃或租约到期后，持久队列按
统一失败重试继续，不创建第二条业务任务。

服务重启不会改变 source revision、generation 或 provider 去重事实。已完成的 Agent turn 从持久化 typed result
继续；未完成的 turn 重新执行当前 revision。所有原始失败、attempt、session、tool event 和 provider 结果
事实保持 append-only。

## History 与通知

History 默认展示任务的当前 projection，并在详情页展示原始 attempts、sessions、tool events 和 provider result
identifiers。旧失败记录不会被删除；后续成功只更新当前 projection，并标识原始失败为历史事件。

`failed`、`needs_feedback` 和真正的 `needs_human` 进入通知；已完成任务不因旧失败事件继续占用收件箱。通知
只显示脱敏的原因、当前结果和下一步，不显示 token、绝对路径或原始敏感工具输出。

## 终态

- `executed`：B 返回合法结果，且其业务流程确认动作完成。
- `no_action`：A 判断当前 trigger 无需外部动作。
- `revision_required`：B 提供规则、观察和修改要求，等待 A 生成新 revision。
- `needs_human`：现有 Skill 没有覆盖且确需 Derek 决定的规则缺口。
- `failed`：当前 run 未完成，保留根因、阶段和重试条件。

新代码不得写入 `unknown`、`reconciled`、`pending_reconciliation` 或 `side_effect_state`。这些旧值只在历史详情
中保留，不能驱动路由、重试、通知或业务判断。

## 运行检查

运行代码或配置更新后，应重启并验证 supervisor：

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -fsS http://127.0.0.1:8765/ >/dev/null
```

完成报告前检查 reply tasks、agent runs、work summary、meeting 和外部投递队列中没有新增失败或长期 processing。
