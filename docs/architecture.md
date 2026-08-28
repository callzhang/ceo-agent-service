# CEO Agent Service Architecture

本文档描述当前 Consumer Agent A / Audit Agent B 运行架构。历史方案保留在
`docs/superpowers/` 中，仅用于追溯，不代表当前运行方式。

## 当前任务运行机制

本节是所有任务类型的统一运行契约。每个任务都遵循“执行 Agent → 审核 Agent → 反馈/修正 → 再审核”的生命周期；领域任务只能替换输入和工具能力，不能改变这条基本链路。外部系统的读取、写入和重试由 Agent 按业务 Skill 完成，服务只保存结果投影和去重所需的事实。

```text
pending -> running -> done
                  -> failed
                  -> needs_human
```

- `pending`：任务已持久化，等待执行。
- `processing`：历史兼容名称；新任务统一使用 `running`。
- `needs_feedback`：审核 Agent 已发现结果需要修改，反馈已持久化并等待执行 Agent 修正。
- `revision_pending`：修正版已排队；必须有新的 revision，并保留原 run、反馈、session 和外部回执的关系。
- `done`：任务逻辑完成且结果已持久化。
- `sent`：历史兼容名称；新任务以 `done` 表示完成，发送与回读保存在 trace。
- `needs_human`：现有 Skill 没有覆盖的一类规则需要人工确定；不是技术读取失败的兜底状态。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败，并保留失败阶段和原因。

审核闭环如下：

```text
执行 Agent 生成 R0
  -> 审核 Agent 审核 R0
      -> 通过：审核 Agent 执行/发布 R0，回读后完成
      -> 需要修改：审核 Agent 写入 F0，R0 保留
          -> 执行 Agent 收到 F0，生成 R1
              -> 审核 Agent 审核 R1
```

审核 Agent 只能反馈规则、观察结果和具体修改要求，不能直接改写执行 Agent 的业务正文。执行 Agent 必须基于反馈生成新 revision；原 run 不覆盖、不删除。一个任务最多允许两个内容反馈周期，基础设施失败不消耗反馈周期。

所有任务都禁止使用 `discard` 动作或写入 `discarded` 状态。无需动作的结果在 trace 记录 `no_action` 后进入 `done`；需要修正时由审核 Agent 写入 `audit_feedback`，执行 Agent 生成新 revision；处理失败使用 `failed`；无法自动解决使用 `needs_human`。

`okr_review` 使用上述闭环生成逐 KR 评审；`weekly_okr` 使用上述闭环生成管理者 OKR 进度周报。周报只有在分析、文档发布、群摘要发送和外部回读全部完成后，才能推进成功日期。

完整状态和恢复说明见 [`docs/runtime-mechanism.md`](runtime-mechanism.md)。

### Repository Upgrade

服务周期性读取配置的 `origin/main`，只识别可安全 fast-forward 的更新；分叉、状态指纹变化或
脏工作树不会被静默覆盖。History 页面只展示状态并启动带 operation ID 的 detached updater；
updater 在共享 Git 锁内重新校验指纹，必要时按用户确认的分支名和提交信息保存本地改动，创建
SQLite 在线备份并只保留一个最新快照（同时清理 SQLite sidecar），执行依赖同步和测试，重启
launchd 后验证新 PID、HTTP 健康与 Store 可读性。
升级后验证失败时只对本次安装的精确 commit 做 compare-and-swap 回滚；无法证明仓库仍归本次
操作所有时进入 `needs_manual`，不执行破坏性 Git 操作。MCP 配置不由该流程探测、禁用或覆盖，
直接沿用用户当前 Codex 配置。

### 多人会议投递目标

多人会议默认仍要求 Meeting Alignment Agent 明确选择首个候选群；没有选择目标时，服务不会替
Agent 猜测目标。服务读取所选群的权威会话信息后，仅在明确证明该会话不可发送（例如
`singleChat=true` 或成员数为零）时，才使用会议创建人的已解析身份发送私信。会话元数据缺失、
不一致或身份无法唯一解析时，保留 `MeetingDeliveryRetry`，不发送也不猜测。该回退不适用于
一对一会议，也不改变已确认可发送群的投递路径。

## History 语义与无效入口边界

History 是执行历史的单一展示入口，不把同一次执行拆成多行，也不把队列请求状态
混入执行结果状态。筛选条件的业务含义固定如下：

- `status` 只筛选执行状态；不再使用含糊的 `type` 名称。
- `task_type` 只筛选任务类型；多选通过重复的 `task_type` 参数表达，不再使用
  `object_type` 名称。
- 任务类型包括 `replay`、`wechat`、`approval`、`task`、`meeting` 和
  `okr_review`。OKR 评审优先依据明确的 `action='okr_review'` 识别，其次依据与
  `okr_review_requests` 的会话和触发消息关联识别；同一执行记录只能归入一个类型。
- `okr_review_requests` 的队列状态不能覆盖对应执行记录的 History 状态。若未来需要
  展示队列生命周期，应设计独立视图，不能在 History 中制造第二行或混合两种状态。

History 不承诺旧查询参数或旧 URL 的兼容别名；接口和页面使用当前语义，历史数据只
通过当前代码的分类规则重新解释。

“待处理服务修复”不是运行时能力：它没有生产者、处理动作、修复执行器或闭环，不能
作为服务健康状态或执行队列的一部分。移除该死入口时，范围包括导航、History 卡片、
页面和路由，以及仅服务于该入口的模型、存储 API、初始化表和索引；`feedback_events`
和真正的反馈流程必须保留。删除已有 `service_bugfix_candidates` 表属于独立的数据库
迁移，必须先做并校验 SQLite 在线备份，再小批量迁移、读回表已删除且反馈数据未变化；
迁移必须幂等，不能通过旧路由别名或重新建表恢复该入口。

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

服务重启时，未完成的 Agent turn 按普通失败重试；已完成 Agent 回合会从持久化结果继续。服务不创建独立的 unknown 或状态核对状态机，也不根据工具事件替 Agent 判断外部动作结果。下一次 Agent turn 按当前业务 Skill 读取外部状态，再决定是否继续。

### Schema 初始化竞争

worker 与审计页面会各自打开 SQLite。它们先取得同一个初始化文件锁，再检查 schema 版本和
必要表。检查遇到短暂 `locked` 或 `busy` 时会在该锁内等待后复查；只有稳定确认 schema 过期或
缺表才执行迁移。这样高负载写入不会被误判为 schema 缺失，也不会让审计页面在请求期间执行 DDL。

### Workbench 启动恢复竞争

审计 Web 启动时会执行 Workbench 恢复。若恰好与 worker 的 SQLite 写事务重叠，恢复会对短暂
`locked` 或 `busy` 进行有限次数重试；非锁异常或超过上限的锁仍按服务错误处理。这样一次并发
写入不会使审计页直接启动失败，也不会吞掉持续的数据库异常。

### 外部动作结果与重试

服务不维护 `unknown`、`reconciled` 或 `side_effect_state` 状态机，也不启动专门的只读 recovery 回合。
外部动作中断、回执缺失、读取失败和结果解析失败都按普通 `failed` 记录，并由下一次 Agent turn 依据当前
业务 Skill 重新读取目标状态后继续处理。Agent 自己负责判断动作是否已完成，不得盲目重复执行。

服务仅在 provider 返回时保存三个最小事实：`operation`、`target` 和稳定的 provider result identifier。
这些字段用于把后续业务处理关联到同一外部对象，并在重试前提供去重依据；它们不是业务审核结论，也不触发
任何命令、工具或读取权限检查。原始 stdout、工具调用和详细外部响应仍属于 Agent/runtime 的执行记录。

### 运行结果与外部写入标识

Skill、CLI 和 MCP 的具体调用属于 Agent/runtime 执行环境，不是应用层业务审核条件。应用层不
审核命令名称、命令参数或 Skill receipt，也不因读取命令未登记而否定一个结构化业务结果。
纯读取结果不要求额外 receipt。只有发生外部写入时，才保留 provider 返回的最小
`operation`、`target` 和稳定结果标识，用于识别同一动作是否已经完成并避免重放；这不是对
Agent 如何执行命令的二次审核。

## Skill-first 权威处理流

```text
trigger/context/material references
  -> Consumer A discovers and reads business Skill(s)
  -> A reads operation Skill(s) and proposes an exact action
  -> Consumer/Audit return a typed business result
  -> B reviews, executes, and reads back
  -> service persists the existing run/attempt/provider identifier
```

Producer 只根据消息来源、会话类型、明确的 @、稳定卡片类型和去重标识决定是否创建任务。
系统**没有关键词业务路由器**：service 不通过项目名、人员名、百分比或业务词判断该加载哪个
Skill。Consumer A 根据完整上下文使用 Codex 原生 Skill discovery，按需读取业务 Skill 和操作
Skill。

每个 Consumer 回合的 developer instructions 都携带七个已安装业务 Skill 的精确名称和路径，并把
至少一次业务 Skill 读取定义为返回任何业务结论之前的协议前置条件。目录只声明可用能力，不替 Agent
选择领域；选择仍由 A 根据完整上下文完成。目录或 wire contract 变化时会轮换旧的对话 session。

Service 也不读取正文后替 Agent 解释业务材料。它只传递 trigger、上下文、原始 process/task ID、
链接、本地受控材料引用和可执行的精确读取命令。文档、文件夹、图片、表格、日历、听记和 OA
材料是否相关、是否需要继续展开以及它们支持什么结论，都由 A 判断；B 在执行前独立复核。

Skill 加载属于 Agent 执行环境，service 不要求或校验普通 Consumer/Audit 结果中的 Skill receipt。
仅当发生外部写入时，SQLite 保留 provider 返回的操作标识，用于重试时识别已完成动作并避免重复写入。
SQLite 继续保存既有 task/run/attempt/provider result identifier 状态；系统**不建立平行的 Skill 审计数据库**，详细工具轨迹
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

1. 根据候选内容和当前上下文独立判断适用的业务规则。
2. 重新读取执行前的实时事实和 Audit Rules。
3. 检查 A 的候选是否有事实依据、目标准确、内容最小、权限合适且符合当前规则。
4. 候选合格时按原样执行，并从外部系统读回结果。
5. 业务含义需要变化时返回具体反馈，由 A 生成新 revision；B 不自行改写候选。
6. 外部动作中断时由下一次 Agent turn 按当前业务 Skill 读取目标状态并决定是否继续；服务不创建专门的恢复回合。

每个候选 revision 使用一个新的 B session。后续失败重试沿用任务、generation 和 revision 关系。

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
外部动作标识去重以及敏感凭证不进入提示词和审计页面。

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
两者都可以使用用户安装的工具和 skills。服务只负责 DWS/Lark channel gate、任务去重和结果持久化；
Agent 不执行 `auth login`、`reset` 或 `logout`。某个 MCP 实际返回未授权时，
任务如实记录该依赖不可用，不把认证失败伪装成材料缺失。

### Friday Runtime 路由

`friday_runtime` 是与 `codex_oauth`、`codex_api` 和 `claude_api` 并列的 Agent
Runtime 路由。它通过 Friday Runtime 的 HTTP 接口创建一个 Thread、提交一个 turn、等待
operation 完成，再读取该 Thread 的最终 Artifact；CEO Agent 不直接调用 MiniMax 或其他
provider 的 API，也不把 Friday CLI 当作 Codex CLI 执行。Friday 项目负责 provider、模型、
凭证和 provider 协议（包括 MiniMax 的 Chat Completions 兼容），CEO Agent 只接收 Friday
返回的最终文本或结构化 Artifact。启用该路由时，CEO Agent 使用独立的
`CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL` / `CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY` /
`CEO_FRIDAY_RUNTIME_PROVIDER_MODEL` 生成 Friday launcher 可用的
`FRIDAY_LLM_BASE_URL` / `FRIDAY_LLM_API_KEY` / `FRIDAY_LLM_MODEL`；这些 provider 配置不与
Codex API 配置共享。
Friday Runtime HTTP 的 RuntimeTicket/session token 仍是独立的服务认证，不与 provider key 混用。

路由顺序由 `CEO_AGENT_RUNTIME_ROUTES` 按配置顺序决定，例如：

```text
codex_oauth,codex_api,friday_runtime,claude_api
```

启用 `friday_runtime` 必须同时提供 `CEO_FRIDAY_RUNTIME_PROJECT_ID`，并选择一种认证方式：
`CEO_FRIDAY_RUNTIME_TICKET` 或 `CEO_FRIDAY_SESSION_TOKEN`；也可以显式设置
`CEO_FRIDAY_RUNTIME_AUTH_DISABLED=1` 用于本地无认证测试。服务只把认证信息放在 HTTP
请求头，不写入提示词、结果、History 或日志。Friday 的 provider/model 选择不由
`CEO_CODEX_MODEL` 或 `CEO_CLAUDE_MODEL` 覆盖；`CEO_FRIDAY_RUNTIME_MODEL` 仅保留为
路由元数据，实际模型选择归 Friday 项目配置。

一次 fallback 始终属于同一个 Agent run：当前路由失败后，Router 选择下一条已配置且健康的
路由，保留原任务、generation、proposal/revision 和 A/B 生命周期，不创建第二个 Consumer
或 Audit run。Friday 的 Thread、turn、operation 和 Artifact 标识只作为该次 runtime 调用
的结果事实保存，供失败重试和 History 关联。

Friday 路由使用以下明确错误码：

| 错误码 | 含义 | 是否可重试 |
| --- | --- | --- |
| `friday_runtime_unreachable` | Friday Runtime 网络不可达或 operation 超时 | 是 |
| `friday_runtime_auth_failed` | Runtime ticket/session token 无效或被拒绝 | 否，需修复 Friday 认证配置 |
| `friday_runtime_result_invalid` | Friday 返回不是约定 JSON、缺少 operation/Artifact，或结果为空 | 否，需修复契约/实现 |
| `friday_runtime_failed` | Friday operation 或其 provider 最终失败 | 是，按任务重试策略处理 |
| `friday_runtime_unavailable` | 已选择 Friday 路由但 adapter 未注入/未配置 | 否，需修复服务配置 |

健康探测使用同一 Friday HTTP 契约和配置，但只提交合成 prompt，不访问业务数据、不调用业务
工具、不执行外部写入。`tests/e2e/test_runtime_failover_live.py` 默认运行合成路由契约测试；
真实 Codex/Friday provider 探测和 fallback E2E 必须显式设置
`CEO_LIVE_RUNTIME_FAILOVER_E2E=1`，并提供真实运行时配置。这样默认测试不会消耗 provider
配额或依赖本地 Friday 服务，真实 E2E 则验证网络、认证、operation 完成和 Artifact 读取。

## 重复执行与恢复

### 精确 revision 去重

重复保护绑定源 trigger、任务 generation 和候选 revision。完全相同的 revision 已有外部
成功结果时不会再次执行；A 根据反馈产生的新 revision 不会被旧 revision 的结果阻止。

OA pending 扫描会先读取当前审批记录。只有最新有效记录来自其他参与者时才生成 review
任务；如果 Derek 已在该外部更新之后完成评论、审批或其他处理，扫描不再把同一审批重新入队。
History 中的每条 attempt 始终显示自己的真实状态和错误，不以另一条 attempt 改写为“已恢复”
或“已由后续处理”。历史数据中的旧 unknown/reconciled 标签只按历史事实展示，不参与当前状态迁移。

### Agent 失败重试

Consumer 或 Audit 的运行、依赖、解析和外部系统错误统一进入 `failed`，由 Agent 在下一次 turn 中按当前
业务 Skill 读取必要事实并决定是否重试。服务不区分“有副作用失败”和“无副作用失败”，也不维护专门的
独立核对队列。重试仍绑定原任务、generation 和 revision；若 provider 已返回稳定结果标识，Agent
必须先使用该标识或读取目标状态避免重复动作。

服务重启后，仍有有效租约的 run 不会被 stale recovery 抢占；租约过期且没有活动进程的
run 才能被持久队列恢复。

## 持久化与审计

Codex 原生 session JSONL 是详细审计来源，保存每个 Agent turn 的提示、工具调用、输出和
结果。SQLite 只保存恢复所需的最小状态：

- task/generation、角色、proposal revision 和父子 run 关系；
- A/B session ID 与 transcript 行范围；
- operation、target、provider result identifier（仅在 provider 返回时保存）；
- run 状态、租约和下一次可用时间；
- 结构化最终结果和精确去重键。
- 当前业务 turn 的受审工具生命周期元数据，包括 capability、operation 和参数/结果摘要；
  原始参数与工具结果仍只保留在 Codex session JSONL。若原生 MCP 事件只出现在 session
  JSONL，运行器会在进程结束后按 turn ID 回放这些元数据，并排除随后执行的 hook turn。

服务不在 SQLite 复制完整 Codex transcript，也不维护另一套业务审计日志。History 页面按
session 指针读取 JSONL，并只向普通用户展示业务结果；内部角色、规划标签和原始敏感工具
输出保持折叠或脱敏。

## 终态语义

| 终态 | 含义 |
| --- | --- |
| `executed` | B 已执行并从外部系统确认结果。 |
| `no_action` | A 确认当前触发无需外部动作。 |
| `revision_required` | B 给出结构化反馈，等待 A 生成下一 revision。 |
| `needs_human` | 只能由 Derek 作出的不可约管理判断；不是普通材料不足。结果必须提供通用的 `risk` 和 `confidence`，且仅当风险为 `high`、置信度低于 `0.5` 时允许；同时提供 2 至 4 个互斥、可执行的选项，每项包含唯一稳定的 key、显示标签、执行指令和后果。 |
| `failed` | 当前 run 失败；错误说明是否可重试。 |
| `quarantined` | 历史数据中的旧投影标签，仅用于历史展示；新执行不得写入。 |

只有诊断、没有完成用户要求的动作时，不能标记为 `executed`。如果缺的是参与者可以回答的
事实，正确动作是发送一个具体澄清问题，而不是 `needs_human`。

OA 列表读取成功后，个别审批任务或详情读取失败记录在扫描游标中，作为待跟进提醒；只有
列表读取本身失败才写入扫描器错误状态。

## 关键模块

| 模块 | 职责 |
| --- | --- |
| `app.worker.DingTalkAutoReplyWorker` | 领取任务、构造上下文、调用编排器并映射终态。 |
| `app.agent_orchestrator.AgentOrchestrator` | 在 A、B、反馈和失败重试之间推进状态机。 |
| `app.business_skills` | 清点并安装七个 service-managed 业务 Skill；不参与业务路由。 |
| `app.agent_skill_usage` | 提供 Agent 执行环境所需的 Skill 读取辅助；不参与普通业务结果审核。 |
| `app.consumer_agent.ConsumerAgentRunner` | 复用对话 A session，按 read-oriented 角色协议读取、判断并提出候选。 |
| `app.audit_agent.AuditAgentRunner` | 新建 B 审计 session，执行合格候选并处理失败重试。 |
| `app.agent_contracts` | 严格定义 A proposal 与 B audit result。 |
| `app.audit_rules` | 保存、校验并分别渲染共享 Audit Rules。 |
| `app.codex_runner.CodexRunner` | 以原生 `codex exec` 启动并继承安装用户的 Codex 配置。 |
| `app.friday_runtime_adapter.FridayRuntimeAdapter` | 通过 Friday Thread/turn/operation/Artifact HTTP 契约执行一个 Agent turn；不实现 provider 选择。 |
| `app.friday_runtime_contract.FridayRuntimeContract` | 定义 Friday Runtime 请求、认证头、operation 状态和最终 Artifact 的稳定契约。 |
| `app.channel_gate` / `app.mcp_doctor` | 在运行前检查 CLI 与 MCP 依赖。 |
| `app.store.AutoReplyStore` | 保存队列、run 关系、租约、revision 和最小恢复状态。 |
| `app.audit_web` | History、Agent session、Audit Rules、配置和恢复入口。 |

## 运维入口

```bash
# DWS + Lark 通道状态
"$HOME/miniforge3/bin/ceo-agent" channel-doctor

# MCP 注册与可用性诊断；加 --verify-live 做实时探测
"$HOME/miniforge3/bin/ceo-agent" doctor-mcp --verify-live

# 单次 dry-run
CEO_NOT_SEND_MESSAGE=1 "$HOME/miniforge3/bin/ceo-agent" run-once --not-send-message

# 质量巡检并验证外部通道
"$HOME/miniforge3/bin/ceo-agent" quality-check --verify-channels

# 当前唯一 launchd job
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

安装和配置细节见 [agent-installation-runbook.md](agent-installation-runbook.md)，任务恢复细节见
[reply-worker-reliability.md](reply-worker-reliability.md)。

## OA 审批处理原则

审批 Agent 必须先读取最新 OA `detail`、`tasks`、`records`，按
`oa_process_instance_id` 去重；History 按审批事项归并展示，不能让旧 attempt 的
Failed 覆盖后续真实状态。无时区时间按 `Asia/Shanghai` 解释，比较时转换为 UTC，
同时保留 OA 原始时间字符串用于审计；缺少时区不得产生冲突错误。

审批实例和当前 task 仍为 `RUNNING` 且材料可由申请人补充时，Agent 必须在原审批中
评论具体缺失材料、读回评论、通知实际申请人并读回通知，保持审批待处理，不得让
Derek 选择。已有相同目的且已确认的评论或通知不得重复写入；新材料出现后基于最新
OA 内容重新运行 Skill。已有后续终态时只读对账。

重试复用同一个正式任务和审批实例，不创建替代审批事项。瞬态故障进入 exponential
backoff；终态失败必须说明根因、已尝试动作、provider 标识（若有）、下一步和重试条件。
DWS/OA 技术错误不得直接暴露给申请人，所有“已评论”“已通知”“已完成”都必须由 Agent
根据外部系统结果确认。

Codex Agent 可以通过 `agent_cli.read_skill` 读取 Skill；Skill 读取属于 Agent 执行环境，
不是应用层业务结果的前置 receipt。launchd 业务服务不是 Skill 可读性的前置条件。
