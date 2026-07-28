# CEO Agent Service Architecture

本文档说明 CEO Agent Service 的当前系统架构、模块边界、主要数据流和排障入口。它是维护总览，不替代具体专题文档；行为细节仍以代码、测试和专题文档为准。

## 目标

CEO Agent Service 是一个本地优先的企业消息处理服务。它从钉钉、微信、日程、审批、会议和本地工作材料中发现需要 Derek 处理的信息，把需要判断的事项交给 Codex Agent 生成结构化计划，再由服务侧校验、执行、审计和恢复。

核心目标：

- 自动处理可安全处理的消息、审批、会议跟进和任务跟进。
- 所有外部动作先落本地 SQLite，再执行或记录执行结果。
- 对每次回复、审批、追问、跳过和失败保留可审计证据。
- 遇到权限、材料、上下文或工具异常时进入可恢复状态，而不是静默丢失。

## 运行形态

生产运行通常由 macOS launchd 启动：

- 主服务：`com.ceo-agent-service.main`
- 命令入口：`ceo-agent`，由 `app.cli:main` 提供。
- 默认服务命令：`python -m app.cli service`
- 审计页面：默认 `http://127.0.0.1:8765`
- 运行库：默认 `~/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`

`service` 模式在一个进程内启动多个线程：

| 线程 | 入口 | 职责 |
| --- | --- | --- |
| audit-web | `run_audit_web_command` | 本地 FastAPI 审计页面和 API |
| database-backup | `run_database_backup_loop` | SQLite 定期备份和保留策略 |
| producer | `run_producer_loop` | 发现钉钉消息并写入 `reply_tasks` |
| consumer | `run_consumer_loop` | 消费 `reply_tasks`，规划并执行动作 |
| meeting-producer | `run_meeting_producer_loop` | 发现已结束会议并写入会议队列 |
| meeting-consumer | `run_meeting_consumer_loop` | 分析会议并发送会后对齐 |
| task-maintenance | `run_task_maintenance_loop` | 工作事项抽取、任务跟进、Todo 同步 |
| wechat components | `_wechat_service_components` | 可选微信读写、记忆候选处理 |

服务启动时会先恢复部分中间状态，包括 processing 的回复任务、work summary input、universal action、OKR review 和 meeting alignment job。

## 外部依赖

| 依赖 | 主要用途 | 访问方式 |
| --- | --- | --- |
| DWS CLI | 钉钉消息、文档、OA、日程、会议、组织信息、发送消息 | `app.dws_client.DwsClient` |
| Codex CLI | 结构化决策、材料读取、工具使用 | `app.codex_runner` 和各类 runner |
| SQLite | 本地队列、审计、执行状态、服务状态 | `app.store.AutoReplyStore` |
| Memory Connector | 长期记忆读取和写入 | Codex MCP 继承或服务侧 memory action |
| macOS launchd | 常驻服务 | `launchd/*.plist` |
| 本地 workspace | 业务材料、工作画像、语料 | `CEO_WORKSPACE` |

外部动作必须通过服务侧 executor 执行。Codex 负责生成结构化计划，不应直接绕过服务状态机执行可见副作用。

## 故障隔离

DWS 是按操作使用的外部依赖，不是整个服务的启动或运行闸门。服务只在本机网络不可用时暂停轮询；DWS 的授权失效、返回字段异常、命令超时或单个业务接口失败，不得暂停其他线程。

- `DwsClient` 负责命令级超时、只读操作重试、进程并发控制和错误分类；消息只读命令把 DWS 的 `*_INVOKE_FAILED` 错误族视为瞬时外部故障，避免依赖单个服务端错误名。文档或钉盘下载遇到可识别的临时网络错误时也可重试，但发送、审批、升级等动作不使用该重试路径。
- DWS 错误文本无法识别时，worker 读取结构化认证状态；只有认证字段明确不健康才启动备份恢复或重新登录，认证状态本身不可读时不猜测登录原因。
- `CachedDwsClient` 必须完整转发 worker 使用的公开材料读取接口；worker 只调用 `doc_info`、`list_doc_nodes`、`read_doc`、`read_sheet` 和文件下载等公开方法，不访问底层 CLI 字段或通用命令执行方法。
- producer、consumer 和 meeting 循环在单轮边界隔离异常；失败写入 `errors`，下一轮继续。
- task-maintenance 按工作事项、OKR、任务扫描、OA 扫描和 follow-up 分步骤隔离；一个步骤失败不阻断同轮其他步骤。
- 发送等有副作用的命令只有具备幂等键时才允许自动重试，避免重复发送。
- Memory MCP 为 deferred tool 时，service-owned 子 Codex 可先用 `tool_search` 加载 `memory_connector.memory_write`；审计忽略这一步只读发现，但仍要求最终恰好一个参数完全匹配的 memory_write。已发起工具调用但明确返回后端错误时，记录为可恢复 `blocked`；只有缺少工具回执、无法确认是否写入时才使用 `unknown`。
- 失败不能伪装为空结果或成功；任务状态、attempt 和错误原因仍必须落库，供恢复和审计使用。
- 数据库自动保留任务只解析标准日期备份文件；手工备份或未知文件名必须被忽略，不能使主服务退出。

### 外部依赖重试契约

外部依赖失败使用两层恢复，不消耗业务任务的终态尝试次数：

1. 调用层先执行有限次数的短退避重试。`app.external_retry` 在重试耗尽后保留 `ExternalDependencyError` 类型；DWS 在 `DwsError.retryable_external_dependency` 上保留同等信息。
2. 队列层根据错误类型把 `reply_tasks` 恢复为 `pending`、把 `work_summary_inputs` 保持为 `pending`、把会议分析任务保持为 `retry`，并安排下一次退避。队列不再通过外部服务错误文案判断是否可恢复。

Codex planner、task agent、meeting agent、structured agent，以及允许自动重试的 DWS 只读/幂等命令都必须遵守该契约。权限缺失继续进入授权流程；业务输入、目标绑定、脱敏和 schema 校验失败继续使用终态失败或明确 blocked。

已经发起外部写操作但无法确认结果时，不适用自动重放。该状态必须保持 `unknown` 或隔离失败，先用回执、任务 ID 或查询接口核对；只有确认上次操作失败后才能重新执行，避免重复回复、重复审批或重复写入。

## 顶层数据流

```text
外部消息/会议/任务
        |
        v
Producer / Scanner
        |
        v
SQLite 队列
        |
        v
Context Builder
        |
        v
Codex Planner
        |
        v
Validator
        |
        v
Executor
        |
        v
SQLite 审计 + 外部系统结果
```

关键原则：

- Producer 只发现和入队，不做复杂语义决策。
- Consumer 领取任务后构造上下文，调用 planner，然后按计划执行。
- Validator 在执行前检查依赖、权限、重复发送、dry-run、可信目标和敏感边界。
- Executor 负责真实副作用，并把结果写回 `reply_attempts` 和 `universal_action_executions`。

## 钉钉回复链路

主要模块：

| 模块 | 职责 |
| --- | --- |
| `app.worker.DingTalkAutoReplyWorker` | 钉钉生产、消费和大部分业务编排 |
| `app.dws_client.DwsClient` | 封装 DWS 命令和结果解析 |
| `app.store.AutoReplyStore` | 持久化队列、attempt、sent reply、error |
| `app.universal_context` | 构造 Universal Agent 可见任务上下文 |
| `app.universal_planner` | 调用 Codex 生成 `UniversalPlan` |
| `app.universal_validator` | 执行前校验 |
| `app.universal_consumer` | 计划复用、依赖检查、执行编排 |
| `app.universal_executor` | 执行动作并记录执行状态 |

处理步骤：

1. Producer 读取未读会话、@消息、机器人私聊、广播 alias 和慢路径补扫。
2. 路由规则过滤系统消息、过期消息、无效群消息和不可处理卡片。
3. 合格触发写入 `reply_tasks`，唯一键是 `channel + conversation_id + trigger_message_id`。
4. Consumer 领取 pending task，读取上下文、文档、图片、OA、日程、任务信息和本地材料。
5. Universal planner 输出结构化 action。
6. Validator 检查重复、权限、依赖、dry-run、可信目标和计划合法性。
7. Executor 发送回复、追问、handoff、OA 操作、日程动作、文档动作、reaction 或 memory write。
8. 结果写入 `reply_attempts`、`sent_replies`、`universal_plan_executions`、`universal_action_executions`。

## Universal Consumer

Universal Consumer 把“决策”和“副作用”分离：

- Codex 输出 `UniversalPlan`。
- 服务保存 plan 和 context hash。
- 每个 action 都有独立 execution row。
- 已成功的 action 重放时跳过。
- `unknown` 表示外部动作可能成功但无法确认，自动重放必须停止。

主要状态：

| 状态 | 含义 |
| --- | --- |
| `succeeded` | 动作确认完成 |
| `failed` | 动作确认失败，可按策略重试 |
| `unknown` | 外部动作结果不确定，禁止自动重放 |
| `started` / `recovering` | 执行中或恢复中 |
| `not_started` | 尚未有 execution row |

详情见 `docs/universal-consumer-agent.md`。

## OA 审批链路

OA 审批是高风险动作，必须满足更严格条件：

- 必须有可信 `process_instance_id` 和 `task_id`。
- 必须读取审批详情、当前节点、附件或相关业务材料。
- 必须确认任务属于 Derek 当前可处理节点。
- 无法确认时应评论追问、私信追问、handoff 或进入 blocked，而不是猜测通过/拒绝。

当前代码中 OA 相关逻辑分布在：

| 模块 | 职责 |
| --- | --- |
| `app.oa_approval` | OA 结构化处理和 schema |
| `app.worker` | OA 上下文读取、可信目标解析、执行保护 |
| `app.dws_client` | DWS OA 命令封装 |
| `app.universal_context` | 将可信 OA target 写入 planner 上下文 |
| `app.universal_executor` | 按计划执行 OA action |

排查 OA 问题时先看 attempt 的 `oa_process_instance_id`、`oa_task_id`、`oa_action_result_json`、audit docs 和 universal action execution，不要只看回复文本。

## 会议对齐链路

会议对齐是独立队列，不复用 `reply_tasks`：

| 表 | 用途 |
| --- | --- |
| `meeting_alignment_jobs` | 待分析或待发送会议任务 |
| `meeting_alignment_runs` | 每次 Codex 分析记录 |

处理步骤：

1. meeting producer 读取会议和日程信息。
2. 只保留 Derek 参会、已结束并满足静默等待时间的会议。
3. meeting consumer 读取会议材料，判断是否需要输出 Derek 观点解读或冲突对齐。
4. 可发送时进入 `ready_to_send`，再由 delivery 发送并记录结果。

会议链路的目标不是总结所有会议，而是只处理需要 Derek 观点或对齐动作的会议。
AI 听记分页在首屏失败时整轮失败并重试；已读取至少一页后若后续 DWS 游标失败，则降级处理已验证页面，避免单个不稳定游标阻断整个 producer。

## 工作事项和跟进链路

任务系统以项目为中心，不以消息为中心。

| 模块 | 职责 |
| --- | --- |
| `app.task_scanners` | 从消息、会议、文档等来源发现 work summary input |
| `app.task_agent` | 用 Codex 抽取项目、事实、TODO 和跟进草稿 |
| `app.task_retrieval` | 检索已有项目和 TODO 候选 |
| `app.todo_sync` | 同步钉钉 Todo 状态 |
| `app.follow_up` | 生成和发送跟进 |

主要表：

| 表 | 用途 |
| --- | --- |
| `work_summary_inputs` | 待抽取的工作输入 |
| `work_projects` | 项目主记录 |
| `work_todos` | 项目下 TODO |
| `work_updates` | 项目事实和进展 |
| `follow_up_drafts` | 跟进草稿和发送结果 |
| `work_todo_dingtalk_links` | 本地 TODO 与钉钉 Todo 的关联 |

## 微信链路

微信是可选通道，和钉钉共用本地存储与审计原则，但有独立 reader/sender 进程：

| 模块 | 职责 |
| --- | --- |
| `app.wechat.reader*` | 读取微信消息 |
| `app.wechat.producer` | 写入微信 reply task |
| `app.wechat.consumer` | 消费微信任务 |
| `app.wechat.memory*` | 微信长期记忆候选和写入 |
| `app.wechat.sender*` | 发送微信回复 |

微信 launchd plist 位于 `launchd/com.stardust.ceo-agent.wechat-*.plist`。

直接联系人发送采用双重证据绑定：

1. consumer 将当前入站消息正文随 delivery 保留，供本次发送校验。
2. sender 必须在微信最近会话侧边栏中找到显示名相同且消息预览与该入站正文匹配的唯一会话；完整正文要求相等，较长正文允许界面前缀或截断，短文本不做模糊匹配；`wxid` 不作为界面搜索词。
3. 绑定记录只保存入站正文哈希和界面标题指纹，不保存正文。
4. 实际发送前再次按同一入站正文选择会话并校验 composer 标题。找不到唯一匹配时失败关闭，不向同名联系人猜测发送。

Accessibility 树遍历按元素去重；微信返回自引用或父子循环节点时不会耗尽深度预算，真实会话列表仍可被扫描。
锁屏或 Sender preflight 临时不可用时不消费 `ready_to_send` delivery；消息保持待发送，图形会话恢复后由后续循环继续处理。
同一账号和会话生成新 delivery 时，旧的 `ready_to_send`、`failed` 或 `send_unknown` delivery 会原子转为 `superseded`，对应 attempt 显示为 `skipped`；已发送、正在发送或用户明确拒绝的记录不受影响。
因此，会话不在最近列表或同名项无法唯一确认时，当前 delivery 会保持未发送，等待重新生成或人工确认；新 trigger 到达后不会继续发送已过时的旧回复。
History 的“重新生成”会保留微信 channel 和原始 `WechatMessage`；不会把微信任务转换成钉钉任务。

## 本地状态库

SQLite 是系统事实源。主要表按职责分组：

| 分组 | 表 |
| --- | --- |
| 消息队列 | `conversations`、`seen_messages`、`reply_tasks` |
| 回复审计 | `reply_attempts`、`sent_replies`、`errors` |
| Universal 执行 | `universal_plan_executions`、`universal_action_executions` |
| 反馈 | `feedback_events`、`service_bugfix_candidates` |
| 记忆 | `memory_write_events`、`wechat_memory_candidates` |
| 会议 | `meeting_alignment_jobs`、`meeting_alignment_runs` |
| 任务 | `work_summary_inputs`、`work_projects`、`work_todos`、`work_updates`、`follow_up_drafts` |
| 组织缓存 | `org_user_profiles`、`org_cache_metadata` |
| 服务状态 | `service_state`、`codex_session_locks` |

原则：

- 外部可见动作必须先有本地记录。
- 重复消息靠唯一键和 sent reply 记录抑制。
- `memory_write` 使用持久化 lease；恢复扫描只接管已过期的 `started`、明确后端失败、旧版本遗留的 `blocked + memory_backend_unavailable` 和结果未知动作，不抢占仍有效的执行，也不放开其他业务 blocked；未知结果沿同一冻结 payload 幂等重试。
- recoverable blocked/failed 不能被当作完成。
- 确定不可恢复的 blocked 必须写清楚原因，避免每轮重复修复。

## 安全边界

服务侧安全边界包括：

- 群聊触发必须满足 @ 本人、机器人名、广播 alias 或明确路由规则。
- 内部人事、候选人、薪酬、审批等敏感事项必须读材料并遵守权限规则。
- 回复正文不得泄露本地路径、token、session id、签名 URL 或原始工具输出。
- live send 需要 `CEO_NOT_SEND_MESSAGE=0` 和 `CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1`。
- Codex 计划不能绕过 validator 和 executor。
- OA、日程、文档、reaction、memory write 等能力有各自的执行前校验。

## 可观测性和排障入口

| 问题 | 首选入口 |
| --- | --- |
| 某条消息为什么没回 | 审计页 attempt detail、`reply_tasks`、`reply_attempts` |
| 是否真的发送 | `sent_replies`、attempt `send_status`、DWS send result |
| Universal action 是否卡住 | `universal_action_executions` |
| OA 为什么不能执行 | attempt OA 字段、audit docs、action result、DWS OA detail |
| 会议为什么没发 | `meeting_alignment_jobs`、`meeting_alignment_runs` |
| 任务跟进为什么 processing/failed | `work_summary_inputs`、`task_agent_runs`、`follow_up_drafts` |
| 用户反馈 | `feedback_events`、`service_bugfix_candidates` |
| 工具或权限 | `errors`、`service_state`、`mcp_doctor` 状态 |

常用只读命令：

```sh
ceo-agent produce-once
ceo-agent consume-once
ceo-agent run-once
ceo-agent audit-web --host 127.0.0.1 --port 8765
```

修改 runtime 代码后，必须提交、重启 launchd 主服务，并确认没有未处理的 failed/processing backlog。

## 相关文档

- `README.md`：产品目标、快速开始和运行配置。
- `docs/product-logic.md`：产品规则和业务处理逻辑。
- `docs/message-routing-rules.md`：消息路由规则。
- `docs/universal-consumer-agent.md`：Universal Consumer 状态机。
- `docs/reply-worker-reliability.md`：回复链路可靠性。
- `docs/dws-capabilities.md`：DWS 能力说明。
- `docs/agent-installation-runbook.md`：安装和初始化。
- `docs/superpowers/plans/`：重要功能和修复计划。
