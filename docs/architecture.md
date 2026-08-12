# CEO Agent Service Architecture

本文档描述当前 Consumer Agent A / Audit Agent B 运行架构。历史方案保留在
`docs/superpowers/` 中，仅用于追溯，不代表当前运行方式。

## 设计目标

CEO Agent Service 是本地优先的企业消息处理服务。它发现需要 Derek 处理的消息、
审批和任务，把业务判断与外部执行拆给两个职责明确的 Agent：

- **Consumer Agent A** 是 Derek 的 read-oriented representative：理解业务、读取证据并提出精确
  候选；按角色和结果协议不得主动执行消息发送、审批等外部写操作。
- **Audit Agent B** 独立审阅候选，是 service 生命周期中唯一被授权发布 accepted action 的
  Agent；它执行消息、评论、审批等动作并读回结果。
- Service 负责触发发现、队列、会话指针、角色编排、严格结果校验、租约恢复和精确重复
  投递保护，不替 Agent 做业务判断。

## 单一服务进程模型

生产环境只安装一个 launchd job：

- job：`com.ceo-agent-service.main`
- 入口：`python -m app.service_supervisor`
- worker 子进程：`python -m app.cli service`
- audit-web 子进程：由同一 supervisor 托管，默认监听
  `http://127.0.0.1:8765`

supervisor 同时管理 worker 和 audit-web。任一子进程异常退出时，supervisor 只在同一个
launchd job 内退避重启该子进程，健康子进程继续运行。不要安装第二个 audit-web plist，也不要
恢复双 launchd 模型。

launchd 和本地启动脚本都设置 `PYTHONDONTWRITEBYTECODE=1`。worker 与审计 Web 会并发
导入同一份代码；禁止写入 `.pyc` 缓存可避免缓存文件锁把一次正常启动误判为服务失败。
### Codex 会话隔离

同一 `conversation_id` 的 Consumer A 先取得持久会话锁，再通过原子任务认领启动 Codex；
不同会话可以在同一个 launchd 服务内并行执行。Audit B 的每个 revision 使用独立 session。
服务不再用全局进程锁串行化所有 Codex 调用，否则一个长会话会让无关会话已认领却无法运行。

跨 worker、审计页面和服务重启的竞争由 SQLite 会话锁、Agent run lease 和结果回读处理；
同一会话顺序不依赖共享的进程级锁。

服务重启时，未开始外部动作的运行会创建新的执行代次；已完成 Agent 回合会从持久化结果继续。
若 Audit 已进入外部动作而在收尾中断，服务立即将其切换为同一代次的只读核对，读取外部状态后
再决定完成、补发或阻塞，绝不等待旧租约到期或直接重放该动作。

### Schema 初始化竞争

worker 与审计页面会各自打开 SQLite。它们先取得同一个初始化文件锁，再检查 schema 版本和
必要表。检查遇到短暂 `locked` 或 `busy` 时会在该锁内等待后复查；只有稳定确认 schema 过期或
缺表才执行迁移。这样高负载写入不会被误判为 schema 缺失，也不会让审计页面在请求期间执行 DDL。

### 受控命令回执

Agent 的 DWS、Lark 和本地读取都通过受控 CLI 执行并回传可校验回执。DWS Runtime Schema 是
本地只读的命令契约查询，供 Agent 按已安装 skill 选择命令；它可在 Consumer 中使用，但任何实际
写入仍只能由 Audit 的已批准调用执行。若 MCP 返回受控错误，运行时保存该错误码，而不把它笼统
改写成“回执缺失”。被拒绝的只读查询没有外部副作用，错误会回传给同一 Agent 回合，使其可用
Runtime Schema 或 Skill 修正命令；写入及未受控命令仍会立即拒绝。
受控本地读取同样保留其稳定目标标识，因此带 `--instance-id`、`--task-id` 等参数的只读核验可
准确对应同一目标的受控写入；无目标或目标冲突的读取不能作为送达或审批结果的证据。

## Skill-first 权威处理流

```text
trigger/context/material references
  -> Consumer A discovers and reads business Skill(s)
  -> A reads operation Skill(s) and proposes an exact action
  -> service derives verified Skill receipts from existing tool events
  -> Audit B rereads the same business Skill(s) and operation Skill(s)
  -> B reviews, executes, and reads back
  -> service persists the existing run/attempt/receipt state
```

Producer 只根据消息来源、会话类型、明确的 @、稳定卡片类型和去重标识决定是否创建任务。
系统**没有关键词业务路由器**：service 不通过项目名、人员名、百分比或业务词判断该加载哪个
Skill。Consumer A 根据完整上下文使用 Codex 原生 Skill discovery，按需读取业务 Skill 和操作
Skill。

Service 也不读取正文后替 Agent 解释业务材料。它只传递 trigger、上下文、原始 process/task ID、
链接、本地受控材料引用和可执行的精确读取命令。文档、文件夹、图片、表格、日历、听记和 OA
材料是否相关、是否需要继续展开以及它们支持什么结论，都由 A 判断；B 在执行前独立复核。

Skill 使用证明来自现有 Codex tool events：只有已完成的 `agent_cli.read_skill` 调用才会生成包含
Skill 路径和 SHA-256 的 verified receipt。B 必须按 receipt 重读同一份 Skill。SQLite 继续保存
既有 task/run/attempt/effect receipt 状态；系统**不建立平行的 Skill 审计数据库**，详细工具轨迹
仍以 Codex session JSONL 为准。

### 动态 Skill 分层

七个 CEO 业务 Skill 安装到 `~/.agents/skills`，按任务动态加载：

| 业务 Skill | 负责的业务判断 | 常见操作 Skill |
| --- | --- | --- |
| `ceo-message-triage` | 回复、反应、追问、无需动作 | `dingtalk-chat` |
| `ceo-calendar-invite` | 日程邀请是否接受、拒绝或追问 | `dingtalk-calendar` |
| `ceo-document-review` | 文档、文件、图片、表格的审阅路径 | `dingtalk-doc`、`dingtalk-drive`、Lark 文档 Skill |
| `ceo-meeting-work` | 听记、静默会、会议总结与行动项 | `dingtalk-minutes`、`dingtalk-chat` |
| `ceo-mail-review` | 完整邮件线程审阅和回复 | `dingtalk-mail` |
| `ceo-personnel-communication` | 人事信息的受众、可见性和最小披露 | 候选人/通讯录操作 Skill |
| `ceo-work-tracking` | 任务提取、项目/TODO、跟进和关闭 | `dingtalk-todo`、`dingtalk-chat` |

业务 Skill 说明“如何判断”，操作 Skill 说明“如何读取或执行”。OA、面试和 OKR 已有成熟的专业
Skill，CEO Skill 只负责识别需要委派的场景，不复制专业规则：分别加载
`dingtalk-oa-approval`、`xiaoqing_interview`/现有面试 Skill、`dingtang-okr-review`。

### Consumer Agent A

A 的身份是 Derek 本人，而不是旁观审核员。A 会：

1. 复用同一业务对话的 Codex session，理解此前已经确认的事实。
2. 动态发现并读取适用的业务 Skill，再读取完成任务所需的操作 Skill。
3. 以读取和判断为目的使用安装用户已有的 CLI/MCP，按需读取原始消息和材料引用，不依赖
   service 预读或解释正文。
4. 返回结构化候选，其中包含目标、动作、收件人/对象、正文或参数、事实引用和预期验证。
5. 对不需要动作的触发返回 `no_action`。

A 的角色协议禁止主动发送消息、评论、审批、修改文档或执行其他外部写操作；它只能提出候选。
Service 不为 A/B 建立两套 MCP 权限配置，也不能保证安装用户继承的每个第三方 MCP 都从技术上
隐藏写工具。因此这里的边界不是“所有写工具在 A 进程中必然不可见”，而是：A 不得调用写工具，
A 的结果协议不接受其自行执行的外部动作，只有 B 对 accepted candidate 的执行进入正式生命周期。
Audit Rules 不能把 A 改成执行者。

当缺失事实可以向当前对话参与者获得时，A 必须提出**一个具体澄清问题**作为普通候选，
由 B 审阅并发送；不得把这种情况转成 `needs_human`，也不得要求 Derek 在“继续处理”和
“先追问”之间选择。`needs_human` 只用于无法通过读取材料或向参与者提问解决、必须由
Derek 作出的管理判断。

### Audit Agent B

B 不是 Derek 的第二个写作分身，而是独立审计与执行者。B 会：

1. 根据 verified Skill receipts 重读 A 使用过的业务 Skill 和操作 Skill。
2. 重新读取执行前的实时事实和 Audit Rules。
3. 检查 A 的候选是否有事实依据、目标准确、内容最小、权限合适且符合当前规则。
4. 候选合格时按原样执行，并从外部系统读回结果。
5. 业务含义需要变化时返回具体反馈，由 A 生成新 revision；B 不自行改写候选。
6. 外部结果未知时不直接重放，而是在原 B session 内先做只读读回。

每个候选 revision 使用一个新的 B session。只有同一个候选的未知结果恢复会复用原 B
session，以保留该次执行的工具上下文和操作身份。

## 会话与反馈周期

- 每个 `conversation_id` 对应一个长期 A session；同一业务对话的新消息通过
  `codex exec resume` 进入该 session。
- 每个候选 revision 对应一个新的 B session。
- B 的 `revision_required` 会通过持久化反馈消息送回 A。
- 一个任务最多允许两个内容反馈周期。基础设施重试不消耗内容反馈周期。
- A session 缺失或损坏时才创建新的会话；服务不会为每条消息无条件创建新 A session。

当 A 在外部写入之前因进程、解析或会话错误失败时，重试会在同一 revision 创建新的
Consumer turn。失败 turn、其 session 标识、事件和错误保持不可变，供 History 审计；新
turn 可以复用仍有效的对话 session，或在该 session 已失效时安全创建新会话。Audit B 始终
绑定该 revision 最新成功的 A turn，避免覆盖失败记录或把旧 session 标识写入新会话。

同一对话在任一时刻只允许一个 A turn 写入会话 JSONL。会话锁只保护本地 transcript 的
一致性，不代表业务消息被丢弃；新任务保留在持久队列中等待该 turn 完成。

### 任务提取与 follow-up 是一个生命周期

`ceo-work-tracking` 把同一事项从识别到关闭作为一个流程：从对话、会议或材料中提取可执行事项，
关联或创建项目和 TODO，确定 owner、截止时间和验证条件，到期时生成针对缺口的 follow-up，读取
后续回复或外部 TODO 状态，最后以明确完成证据关闭。Follow-up 不是第二套路由或回复引擎；它产生的
消息仍进入同一个 A/B 审阅、执行、读回和去重流程。修订后的 follow-up 是新 revision，不能被旧
正文的去重结果拦截。

## Audit Rules

Audit Rules 是 A 和 B 共享的可见业务规则：

- 默认文件：`data/prompts/audit_rules.md`
- 配置页面：`Config -> Audit Rules`
- A 使用规则自检候选。
- B 使用同一规则独立审计并决定是否执行。

可配置内容包括表达、信息最小化、审批材料要求、特定业务风险和需要升级给 Derek 的判断。
以下边界不可配置：A 只负责读取/判断并提出候选、B 独占 accepted action 的正式执行职责、精确 revision 去重、最多两个内容反馈周期、
未知结果先读回以及敏感凭证不进入提示词和审计页面。

## 能力与配置

所有 Agent 直接继承安装用户的 `~/.codex/config.toml`、已安装 MCP、plugin、hook 和 skills。
服务不复制 OAuth header、token 或 MCP transport，也不维护第二套 MCP 清单。这样同一套已登录
的 Memory、Xiaoqing、Exa、Lark 等能力既可在 Codex 桌面端使用，也可在 CEO Agent 任务中使用。

Consumer A 和 Audit B 没有两阶段 MCP permission profile，也没有 service-owned technical MCP
allowlist。安装用户配置中的 MCP 可能同时公开读写工具；service 不声称能够技术性屏蔽其中每一个
写能力。A/B 的区别由角色指令、候选/审计 result contract 和 service 状态机定义：A 只应读取、
分析和提案，B 才被授权执行并发布 accepted action。任何绕过该顺序的 A-side 外部写入都违反协议，
不能作为 service 的已完成结果。

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

本地微信投递使用同一原则：发送动作已开始但没有界面确认时，恢复仅从该动作的持久化开始时间
对同一会话做只读回查。只有精确命中出站内容才确认 `sent`；无命中仍是 `unknown`，不会重发。

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
| `needs_human` | 只能由 Derek 作出的不可约管理判断；不是普通材料不足。结果必须提供 2 至 4 个互斥、可执行的选项；每项包含唯一稳定的 key、显示标签、执行指令和后果。 |
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
| `app.business_skills` | 清点并安装七个 service-managed 业务 Skill；不参与业务路由。 |
| `app.agent_skill_usage` | 从已完成的 Codex tool events 生成 verified Skill receipts。 |
| `app.consumer_agent.ConsumerAgentRunner` | 复用对话 A session，按 read-oriented 角色协议读取、判断并提出候选。 |
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
