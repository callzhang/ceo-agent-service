# Product Logic

CEO Agent Service 把业务理解交给动态加载 Skill 的 Agent，把发现、持久队列、角色边界、恢复和
精确去重留给 service。钉钉是主要对话入口；已配置的 Lark CLI、MCP、plugins 和本地 Skills 复用
安装用户现有的 Codex 环境。

Consumer A 与 Audit B 使用同一套安装用户 MCP/plugin/Skill 配置和认证状态。系统没有另一份技术
MCP allowlist，也没有为 A/B 建立两阶段 MCP permission profile；继承的第三方 MCP 可能同时公开
读写工具。A/B 分工由角色与结果协议定义：A 是用户的 read-oriented representative，只应读取、
分析并提出候选，不得主动执行外部写操作；B 是审计者，也是 service 生命周期中唯一被授权执行和
发布 accepted action 的 Agent。只有 B 的执行和读回会被状态机接受为任务完成。

## 从触发到终态

1. Producer 根据来源、会话类型、明确 @、稳定卡片结构和 unread cursor 发现 trigger。
2. Service 保存精确 source revision，并向 Consumer A 传递上下文和材料引用，不解释业务正文。
3. A 通过原生 Skill discovery 选择并读取一个或多个 CEO 业务 Skill。
4. A 再读取需要的操作 Skill，按其命令读取证据，提出一个精确候选或返回终态。
5. Service 从已完成的 `agent_cli.read_skill` tool event 提取路径与 SHA-256 receipt。
6. Audit B 根据 receipts 重读同一组业务/操作 Skill，核对实时事实和 Audit Rules。
7. B 接受候选后执行并读回；不接受时把具体反馈发回同一对话的 A session 生成新 revision。
8. Service 在现有 task/run/attempt/receipt 状态中记录结果并推进恢复。

系统没有按关键词选择业务处理器的 router，也没有把文档正文预读后塞入 prompt 的 service-side
业务解释层。Service 可以验证材料来源、文件类型、大小、路径和完整性，但“哪个材料相关、应展开
哪一层、材料说明了什么”由 Agent 决定。系统也不维护单独的 Skill audit DB；详细 Skill 调用以
Codex session JSONL 为准，SQLite 只保存恢复所需 receipt 和状态指针。

## 七个业务 Skill

- `ceo-message-triage`：判断回复、reaction、具体追问或 `no_action`。
- `ceo-calendar-invite`：根据日程目的、参与价值、冲突和所需输入决定接受、拒绝或向邀请人追问。
- `ceo-document-review`：读取钉钉/Lark 文档、文件夹、图片、附件和表格并形成可验证结论。
- `ceo-meeting-work`：处理听记、静默会、会议材料、总结、分歧和行动项。
- `ceo-mail-review`：读取完整线程后回复邮件，避免脱离上下文处理单封邮件。
- `ceo-personnel-communication`：判断人事信息的接收人、可见性、最小披露和沟通方式。
- `ceo-work-tracking`：贯通任务提取、项目/TODO、跟进、完成验证和关闭。

这些 Skill 不复制 DWS/Lark 命令目录。A 根据业务 Skill 的指引继续加载 `dingtalk-chat`、
`dingtalk-calendar`、`dingtalk-doc`、`dingtalk-minutes`、`dingtalk-mail`、`dingtalk-todo` 或对应
Lark Skill。B 必须读取 A 已使用的同一组 Skill 后才能执行。

## 专业流程委派

OA、候选人面试和 OKR 不重新写进 CEO 通用 Skill：

- OA 加载 `dingtalk-oa-approval`，由 Agent 通过 live DWS 读取 process/task ID 对应详情和当前任务；
  service 不按申请人或标题猜测审批对象，也不预读正文替 Agent 决策。
- 面试加载现有 `xiaoqing_interview` 或安装环境中的面试专业 Skill，CEO 人事 Skill 只负责受众和
  隐私边界。
- OKR 加载 `dingtang-okr-review`，沿用其证据、评分和结果格式，不在通用 prompt 中复制规则。

专业 Skill 仍遵循相同 A/B 边界、Audit Rules、revision 去重和外部读回。

## 会话与澄清

每个业务 `conversation_id` 复用一个 Consumer A session，新消息使用 `codex exec resume` 追加。
已确认的事实必须复用，不能重复追问。材料缺失但对话参与者可以回答时，A 生成一个具体澄清候选，
由 B 审阅后发回来源会话；不得让 Derek 在“继续处理”和“先追问”之间作无意义选择。
`needs_human` 只用于无法通过读取或交流消除、确实需要 Derek 作管理判断的情况。

## Task Extraction 与 Follow-up

任务提取和 follow-up 是一个完整闭环，而不是两套功能：

```text
source evidence
  -> extract actionable work
  -> associate/create project and TODO
  -> establish owner, due time, and completion evidence
  -> follow up on the unresolved gap
  -> read owner reply or external TODO state
  -> close only with explicit evidence
```

`ceo-work-tracking` 负责整个业务判断。Service 只保存项目、TODO、revision、lease、发送状态和外部 ID。
Follow-up 候选继续走 A/B 执行链；完全相同且已送达的 revision 不再发送，经过反馈改正的 revision
仍可执行。外部结果未知时只读 reconciliation，不能盲目重发。

发送失败也按同一 follow-up 生命周期处理。若回执明确消息未发送，History 和 Task 详情直接展示
可读原因、外部副作用和两个互斥动作：让 Agent 重新核验活跃负责人并修复原 follow-up，或取消
原 follow-up。两者都使用原 draft ID 和 revision 乐观锁，重复或过期提交不会生效；修复动作先
进入 Agent 审阅，不会立即向旧目标重发。若回执无法确认消息是否送达，则不提供修复重试，只允许
人工核验，以避免重复发送。

直聊目标已被渠道明确拒绝后，后续 revision 只有在 Agent 完成负责人重定向后才能再次投递。
单纯改回 `draft`、清空错误或延后时间不能解除该门禁；到期时系统会重新进入负责人修复审阅，
不会向同一个已拒绝目标重复尝试。若新目标再次被拒绝，重定向标记会清除并重新执行同一门禁。

## 会议、邮件与人员沟通

- 会议输出只在有待办、分歧、需解释的管理观点或明确后续动作时发送。每个真实 @ 必须放在对应
  任务或信息旁，不在开头罗列一排参与人。
- 邮件必须先读取完整 thread。已授权回复通过邮件操作 Skill 发送，并核对返回的 message identity
  与成功状态。
- 人事信息先判断原材料可见性、Derek 与接收人的关系、内容是否新增评价以及披露是否最小。
  公开且明确的事实通知可以自动执行；需要个性化管理判断时形成候选；非公开人事结论按 Audit
  Rules 处理。

## 终态与恢复

- `executed`：B 已执行且外部系统读回确认。
- `no_action`：当前 trigger 不需要外部动作。
- `revision_required`：B 的反馈已返回 A，等待新 revision。
- `needs_human`：需要 Derek 的不可约判断。
- `failed`：当前 run 失败并记录是否可重试。
- `unknown`：外部动作可能发生，必须在原 B session 中先只读核对。

诊断不等于完成。要求执行的任务只有在外部结果可核对后才能标记 `executed`。同一 source
revision 的同一动作不会发送两次；不同正文或参数的新 revision 不受旧动作限制。

## 安装者与其他使用者

每位安装者部署自己的 service、SQLite、workspace、Codex/DWS/Lark 登录和可选反馈服务。安装流程
把七个受管业务 Skill 写入该用户的 `~/.agents/skills`；不会写入 `~/.codex/skills`，也不会覆盖
同名的用户自有 Skill。普通同事、HR、审批申请人和项目 owner 不安装服务，只在原工作会话中与
Agent 交互。详细步骤见 [README](../README.md) 和
[agent installation runbook](agent-installation-runbook.md)。
