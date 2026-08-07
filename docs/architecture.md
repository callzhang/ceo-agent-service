# CEO Agent Service Architecture

本文档描述当前 Consumer Agent A / Audit Agent B 运行架构。历史方案保留在
`docs/superpowers/` 中，仅用于追溯，不代表当前运行方式。

## 设计目标

CEO Agent Service 是本地优先的企业消息处理服务。它发现需要 Derek 处理的消息、
审批和任务，把业务判断与外部执行拆给两个职责明确的 Agent：

- **Consumer Agent A** 代表 Derek 理解业务、读取证据并提出精确候选，但没有外部写权限。
- **Audit Agent B** 独立审阅候选，是任务处理链路中唯一可以发送消息、评论、审批或执行
  其他外部写操作的 Agent。
- Service 负责触发发现、队列、会话指针、角色编排、严格结果校验、租约恢复和精确重复
  投递保护，不替 Agent 做业务判断。

## 单一服务进程模型

生产环境只安装一个 launchd job：

- job：`com.ceo-agent-service.main`
- 入口：`python -m app.service_supervisor`
- worker 子进程：`python -m app.cli service`
- audit-web 子进程：由同一 supervisor 托管，默认监听
  `http://127.0.0.1:8765`

supervisor 同时管理 worker 和 audit-web。任一子进程异常退出时，supervisor 会回收另一个
子进程并退出，由同一个 launchd job 拉起完整服务。不要安装第二个 audit-web plist，也不要
恢复双 launchd 模型。

## 权威处理流

```text
External trigger
      |
      v
Producer + channel gate
      |
      v
reply_tasks (exact source revision)
      |
      v
Consumer Agent A (read-only, conversation session)
      |
      +--> proposal ------+
      +--> no_action      |
      +--> needs_human    |
      +--> failed         |
                         v
                 Audit Agent B
                 (fresh review session)
                         |
                         +--> execute + verify
                         +--> revision_required -> A
                         +--> needs_human
                         +--> failed
                         +--> unknown -> same B session read-back
```

### Consumer Agent A

A 的身份是 Derek 本人，而不是旁观审核员。A 会：

1. 复用同一业务对话的 Codex session，理解此前已经确认的事实。
2. 使用服务提供的只读 CLI/MCP 能力读取原始消息、文档、审批、日历、记忆和检索结果。
3. 返回结构化候选，其中包含目标、动作、收件人/对象、正文或参数、事实引用和预期验证。
4. 对不需要动作的触发返回 `no_action`。

A 不能发送消息、评论、审批、修改文档或执行其他外部写操作。该边界固定在运行配置中，
不能通过 Audit Rules 放宽。

当缺失事实可以向当前对话参与者获得时，A 必须提出**一个具体澄清问题**作为普通候选，
由 B 审阅并发送；不得把这种情况转成 `needs_human`，也不得要求 Derek 在“继续处理”和
“先追问”之间选择。`needs_human` 只用于无法通过读取材料或向参与者提问解决、必须由
Derek 作出的管理判断。

### Audit Agent B

B 不是 Derek 的第二个写作分身，而是独立审计与执行者。B 会：

1. 重新读取执行前的实时事实和 Audit Rules。
2. 检查 A 的候选是否有事实依据、目标准确、内容最小、权限合适且符合当前规则。
3. 候选合格时按原样执行，并从外部系统读回结果。
4. 业务含义需要变化时返回具体反馈，由 A 生成新 revision；B 不自行改写候选。
5. 外部结果未知时不直接重放，而是在原 B session 内先做只读读回。

每个候选 revision 使用一个新的 B session。只有同一个候选的未知结果恢复会复用原 B
session，以保留该次执行的工具上下文和操作身份。

## 会话与反馈周期

- 每个 `conversation_id` 对应一个长期 A session；同一业务对话的新消息通过
  `codex exec resume` 进入该 session。
- 每个候选 revision 对应一个新的 B session。
- B 的 `revision_required` 会通过持久化反馈消息送回 A。
- 一个任务最多允许两个内容反馈周期。基础设施重试不消耗内容反馈周期。
- A session 缺失或损坏时才创建新的会话；服务不会为每条消息无条件创建新 A session。

同一对话在任一时刻只允许一个 A turn 写入会话 JSONL。会话锁只保护本地 transcript 的
一致性，不代表业务消息被丢弃；新任务保留在持久队列中等待该 turn 完成。

## Audit Rules

Audit Rules 是 A 和 B 共享的可见业务规则：

- 默认文件：`data/prompts/audit_rules.md`
- 配置页面：`Config -> Audit Rules`
- A 使用规则自检候选。
- B 使用同一规则独立审计并决定是否执行。

可配置内容包括表达、信息最小化、审批材料要求、特定业务风险和需要升级给 Derek 的判断。
以下边界不可配置：A 只读、B 独占任务写权限、精确 revision 去重、最多两个内容反馈周期、
未知结果先读回以及敏感凭证不进入提示词和审计页面。

## 能力与配置

所有 Agent 直接继承安装用户的 `~/.codex/config.toml`、已安装 MCP、plugin、hook 和 skills。
服务不复制 OAuth header、token 或 MCP transport，也不维护第二套 MCP 清单。这样同一套已登录
的 Memory、Xiaoqing、Exa、Lark 等能力既可在 Codex 桌面端使用，也可在 CEO Agent 任务中使用。

服务仍保留职责边界：A 生成候选并按共享 Audit Rules 自检；B 独立审阅并执行被接受的外部动作。
两者都可以使用用户安装的工具和 skills。服务只负责 DWS/Lark channel gate、任务去重、发送回读、
未知结果核对与持久化；Agent 不执行 `auth login`、`reset` 或 `logout`。某个 MCP 实际返回未授权时，
任务如实记录该依赖不可用，不把认证失败伪装成材料缺失。

## 重复执行与恢复

### 精确 revision 去重

重复保护绑定源 trigger、任务 generation 和候选 revision。完全相同的 revision 已有外部
成功结果时不会再次执行；A 根据反馈产生的新 revision 不会被旧 revision 的结果阻止。

### 未知外部结果

如果 B 已开始写操作但没有得到确定结果，run 进入 `unknown`：

1. 保留原 operation ID、候选 revision 和 B session。
2. 在同一 B session 中只读查询目标系统。
3. 已存在：记录确认结果，不再执行。
4. 明确不存在：仅对通过固定能力边界且可精确绑定的原动作继续恢复执行。
5. 无法判断：保持 `unknown` 或转 `needs_human`，禁止猜测和盲目重放。

服务重启后，仍有有效租约的 run 不会被 stale recovery 抢占；租约过期且没有活动进程的
run 才能被持久队列恢复。

## 持久化与审计

Codex 原生 session JSONL 是详细审计来源，保存每个 Agent turn 的提示、工具调用、输出和
结果。SQLite 只保存恢复所需的最小状态：

- task/generation、角色、proposal revision 和父子 run 关系；
- A/B session ID 与 transcript 行范围；
- operation ID、run 状态、租约和下一次可用时间；
- 结构化最终结果、外部结果状态和精确去重键。

服务不在 SQLite 复制完整 Codex transcript，也不维护另一套业务审计日志。History 页面按
session 指针读取 JSONL，并只向普通用户展示业务结果；内部角色、规划标签和原始敏感工具
输出保持折叠或脱敏。

## 终态语义

| 终态 | 含义 |
| --- | --- |
| `executed` | B 已执行并从外部系统确认结果。 |
| `no_action` | A 确认当前触发无需外部动作。 |
| `revision_required` | B 给出结构化反馈，等待 A 生成下一 revision。 |
| `needs_human` | 只能由 Derek 作出的不可约管理判断；不是普通材料不足。 |
| `failed` | 当前 run 失败；错误说明是否可重试。 |
| `unknown` | 写操作可能发生但尚未确认，必须在原 B session 中先读回。 |
| `quarantined` | 没有可验证回执的旧投递；保留证据、停止重发，并单独展示为提醒。 |
| `discarded` | 已确认不属于 Derek 当前职责的请求；保留原因，不再进入执行队列。 |

只有诊断、没有完成用户要求的动作时，不能标记为 `executed`。如果缺的是参与者可以回答的
事实，正确动作是发送一个具体澄清问题，而不是 `needs_human`。

OA 列表读取成功后，个别审批任务或详情读取失败记录在扫描游标中，作为待跟进提醒；只有
列表读取本身失败才写入扫描器错误状态。

## 关键模块

| 模块 | 职责 |
| --- | --- |
| `app.worker.DingTalkAutoReplyWorker` | 领取任务、构造上下文、调用编排器并映射终态。 |
| `app.agent_orchestrator.AgentOrchestrator` | 在 A、B、反馈和未知结果恢复之间推进状态机。 |
| `app.consumer_agent.ConsumerAgentRunner` | 复用对话 A session，执行只读判断。 |
| `app.audit_agent.AuditAgentRunner` | 新建 B 审计 session，执行合格候选并处理未知结果。 |
| `app.agent_contracts` | 严格定义 A proposal 与 B audit result。 |
| `app.audit_rules` | 保存、校验并分别渲染共享 Audit Rules。 |
| `app.codex_runner.CodexRunner` | 以原生 `codex exec` 启动并继承安装用户的 Codex 配置。 |
| `app.channel_gate` / `app.mcp_doctor` | 在运行前检查 CLI 与 MCP 依赖。 |
| `app.store.AutoReplyStore` | 保存队列、run 关系、租约、revision 和最小恢复状态。 |
| `app.audit_web` | History、Agent session、Audit Rules、配置和恢复入口。 |

## 运维入口

```bash
# DWS + Lark 通道状态
.venv/bin/ceo-agent channel-doctor

# MCP 注册与可用性诊断；加 --verify-live 做实时探测
.venv/bin/ceo-agent doctor-mcp --verify-live

# 单次 dry-run
CEO_NOT_SEND_MESSAGE=1 .venv/bin/ceo-agent run-once --not-send-message

# 质量巡检并验证外部通道
.venv/bin/ceo-agent quality-check --verify-channels

# 当前唯一 launchd job
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

安装和配置细节见 [agent-installation-runbook.md](agent-installation-runbook.md)，任务恢复细节见
[reply-worker-reliability.md](reply-worker-reliability.md)。
