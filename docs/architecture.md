# CEO Agent Service Architecture

本文档说明 CEO Agent Service 的当前运行架构、边界和排障入口。行为细节以代码、测试和专题文档为准。

## 目标

CEO Agent Service 是本地优先的企业消息处理服务。它发现需要 Derek 处理的消息和审批，将原始触发、可用上下文和工具入口交给 Direct Agent，并保存结构化终态和原生 Codex session 审计指针。

核心原则：

- Producer 只发现触发并入队，不做业务判断。
- Direct Agent 自行读取材料、判断任务并直接调用获准的 CLI/MCP 工具。
- Service 只负责依赖 gate、队列生命周期、对话 session ID 复用、结果映射和精确重复投递幂等。
- 已开始但结果不确定的写操作不自动重放，只进入只读 reconciliation。
- 诊断不是完成；该规则由 Direct Agent 执行并通过严格 AgentResult 返回，service 不再复制工具事件后二次判断。

## 运行形态

生产环境通常由 macOS launchd 启动：

- 主服务：`com.ceo-agent-service.main`（唯一 launchd job）
- 审计页面：由主服务的 supervisor 托管
- 命令入口：`ceo-agent`，由 `app.cli:main` 提供
- 默认 launchd 命令：`python -m app.service_supervisor`；worker 子进程命令：`python -m app.cli service`
- 审计页面：默认 `http://127.0.0.1:8765`
- 运行库：默认 `~/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`
- 质量快照：默认写入运行库同目录的 `hourly-quality-gate.json`；仅显式设置
  `CEO_HOURLY_QUALITY_GATE_PATH` 时覆盖该位置。

`com.ceo-agent-service.main` 启动 `app.service_supervisor`。supervisor 运行两个独立子进程：`service` 负责数据库备份、消息 producer/consumer、会议处理、任务维护和可选微信组件，`audit-web` 负责审计页面。两个子进程共享同一 SQLite 事实源，但不争用同一 Python 解释器。任一子进程退出时，supervisor 会终止并回收另一方后以失败退出，交由同一个 launchd job 重启；收到停止信号时它也会回收两个子进程，因此不会留下孤儿进程。默认 History 在 Web 进程启动时预热，刷新期间继续返回最近一次完整页面；若初始预热遇到 SQLite 写锁，先缓存并返回轻量忙碌页，再自动刷新。主服务启动时只恢复当前队列和可安全恢复的运行状态；结果未知的写操作不会作为普通失败自动重试。

## 权威处理流

```text
External trigger
      |
      v
Channel gate
      |
      v
reply_tasks queue
      |
      v
One Direct Agent run
      |
      +--> live CLI/MCP reads
      +--> approved CLI/MCP writes
      |
      v
Append-only tool events and receipts
      |
      v
Terminal result mapping
      |
      +--> delivery / completed / skipped / needs_human / failed
      +--> unknown write -> read-only reconciliation
```

Direct Agent 的输入包括原始 trigger、已有对话事实、材料引用、process/task ID、链接和精确读取命令。已有事实必须复用，不能再次追问。OA 详情、当前任务归属、表单、评论和附件由 agent 通过 live DWS read 获取；service 不按申请人或标题猜目标，不预读正文，也不搜索同名材料作为替代。

Direct Agent 使用本机 Codex 原生配置暴露的 MCP、plugin、App、shell、skill 和 DWS/Lark CLI。Service 不维护第二套 MCP registry、effect 分类器、工具事件副本或写操作回执。认证由 channel gate 管理；Agent 不执行 login/reset/logout。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `app.channel_gate` | 在 agent 启动前检查 DWS/Lark 等通道可用性并协调一次性登录请求 |
| `app.worker.DingTalkAutoReplyWorker` | 发现、入队、领取、结果映射和恢复调度 |
| `app.agent_context` | 向 Direct Agent 提供原始事实、材料引用和明确命令 |
| `app.agent_runner.DirectAgentRunner` | 复用仍存在的对话 Codex session；若持久化指针对应的本地 session 已缺失，则清理旧指针并从新 session 继续，避免 `codex exec resume` 在任务启动前失败 |
| `app.native_cli_metadata` | 为已审阅 CLI/MCP 能力提供结构化 effect metadata |
| `app.store.AutoReplyStore` | 持久化任务、agent run、append-only events、attempt、delivery 和回执 |
| `app.audit_web` | 本地审计、人工核对和受保护的 mutation API |
| `app.wechat.sender` | 通过 generation-aware 原子 claim 发送当前微信 delivery |

Service 不替 agent 阅读业务文档、选择业务材料、恢复 OA target、判断申请人或执行 agent 本应完成的外部动作。Service 只验证结构化 result、当前 generation 和重复投递事实，不从用户文本推断执行意图。

## 凭证规则

- DWS 和 Lark CLI 复用当前 macOS 用户正常登录后保存在各 CLI 标准位置的凭证。
- Service 不导出、导入、复制或恢复认证 archive，也不维护第二套 token。
- Agent 永远不得执行 auth login/reset/logout，不能自行弹出授权页面。
- Channel gate 使用结构化 status 和一次 live authenticated probe 判断 `ready`、`needs_login`、`blocked` 或 `unavailable`。
- 只有 gate 明确返回 `needs_login` 时，Login Coordinator 才可启动一次对应 CLI 的登录流程；同一通道一小时内抑制重复启动，并持久化协调状态。
- 网络故障、命令异常或不可读 status 不得被猜成需要登录。

## 外部依赖重试

- DWS 是按操作使用的依赖，不是整个服务的启动闸门；本机网络不可用时暂停轮询，单个 DWS 接口失败只影响当前任务。
- `DwsClient` 负责命令级超时、只读操作重试、进程并发控制和结构化错误分类。通用错误码和具体服务端错误码并存时，以具体错误码为准。
- 日历、消息、通讯录、AI 听记、文档和钉盘下载等只读命令遇到可识别的临时 `ERROR`、`RATE_LIMIT_ERROR`、`PREPARE_CALL_TOOL_ERROR` 或网络错误时可有限重试；发送、审批等写操作没有幂等键时不得走通用重试。
- 调用层重试耗尽后保留结构化外部依赖错误；队列层按状态把任务退回待处理或 retry，不从错误文案猜测是否可恢复。

## 终态与恢复

`agent_runs` 保存一次 Direct Agent generation 的状态和最终 result；`agent_run_events` 按 sequence 追加 CLI/MCP 开始、完成和回执事件。

| 状态 | 含义 |
| --- | --- |
| `completed` | 终态 result 已通过证据校验 |
| `needs_human` | 权限、材料或业务条件需要人工处理 |
| `failed` | 已确认失败，按任务策略决定是否生成新 generation |
| `unknown` | 写操作可能发生但没有可靠回执，只允许只读核对 |

当 result 声称 `completed + confirmed` 且任务要求外部动作时，必须存在持久化的 completed effectful event 或执行回执。只有诊断而没有动作时不能标 completed。Reconciliation 只能查询已有操作结果；确认未执行后，后续修正必须创建新的明确 generation，不能重放结果未知的调用。

## 投递一致性

- 同一 trigger 的已发送记录用于阻止完全重复投递，不阻止后续明确生成的修正版。
- 微信 delivery 绑定 `reply_task` 当前 generation。
- generation 旋转会原子废弃旧的 `ready_to_send` delivery。
- 自动和人工 sender 在真实发送前都必须通过 Store compare-and-swap claim：`ready_to_send -> sending`，并再次匹配当前 generation。
- 未 claim 成功的 sender 不得调用外部发送接口。

## OA 审批

Direct Agent 按 OA skill 工作：

1. 从 service 提供的原始 `process_instance_id`、`task_id`、链接和精确 DWS 命令开始。
2. Live 读取审批详情、当前用户任务归属、表单字段、评论和附件。
3. 自行判断材料是否充分以及应评论、通过、拒绝、退回或请求人工处理。
4. 直接执行获准动作，并让 completed tool event 或 DWS 回执进入审计流。

表单字段齐全、卡片只有实例 ID、多候选不唯一、任务已完成或不属于当前用户，都由 agent 根据 live read 得出结论。Service 不通过姓名、标题或历史缓存猜测可执行 target。

## SQLite 事实源

主要当前表：

| 分组 | 表 |
| --- | --- |
| 消息队列 | `conversations`、`seen_messages`、`reply_tasks` |
| Agent 运行 | `agent_runs`、`agent_run_events` |
| 回复审计 | `reply_attempts`、`sent_replies`、`errors` |
| 微信投递 | `wechat_deliveries` |
| 反馈 | `feedback_events`、`service_bugfix_candidates` |
| 会议 | `meeting_alignment_jobs`、`meeting_alignment_runs` |
| 工作事项 | `work_summary_inputs`、`work_projects`、`work_todos`、`work_updates`、`follow_up_drafts` |
| 服务状态 | `service_state`、`codex_session_locks` |

外部可见动作必须留下本地事件或回执。Recoverable failed/blocked 不能伪装成完成；不可恢复原因必须明确落库，避免每轮重复处理。

## 决策通知

Chrome 通知以 attempt ID 作为稳定标签。同一 attempt 的重试会更新既有通知，
不会产生并列弹窗，并要求用户显式关闭。`/notifications` 从运行库重建固定的
“待处理决策”列表：只有同一触发消息的最新、未审阅 `needs_human` attempt
才会显示。被后续发送、跳过或人工审阅覆盖的旧记录不会再作为待决事项提示。

## 其他链路

- 会议对齐使用 `meeting_alignment_jobs` 和 `meeting_alignment_runs` 独立排队。
- 工作事项由 scanners、task agent、project/TODO store 和 follow-up 流程处理。
- 微信 reader/producer/consumer/sender 使用独立组件，但复用 generation、审计和投递幂等原则。
- Memory Connector、DWS、Lark 和其他 MCP/CLI 调用必须出现在 agent event 审计中。
- Task maintenance loop 每轮做本地周报到期检查；管理者 OKR 周报默认周日 18:00 后执行一次，失败按 `CEO_WEEKLY_OKR_RETRY_SECONDS` 重试。只有实时 OKR 获取、文档创建回读和群消息发送都确认后，才记录当周完成。

## 安全边界

- 群聊触发必须满足当前路由规则；Producer 不做业务语义裁决。
- 回复不得泄露本地路径、token、session id、签名 URL 或原始敏感工具输出。
- Live send 需要当前运行配置允许。
- 未审阅 effect 保守处理，不使用命令关键词、正则或 HTTP method 猜测副作用。
- Audit mutation 至少要求 loopback、严格 `application/json`，并拒绝外部 Origin/Referer；无浏览器来源头的本机 CLI 请求可以使用同一 API。

## 排障入口

| 问题 | 首选入口 |
| --- | --- |
| 某条消息为什么没处理 | attempt detail、`reply_tasks`、`agent_runs` |
| 工具是否执行 | `agent_run_events`、result receipt、attempt audit events |
| 是否真的发送 | `sent_replies`、delivery 状态、外部回执 |
| 写操作结果是否未知 | `agent_runs.side_effect_state` 和 reconciliation 记录 |
| OA 为什么不能执行 | live DWS detail/任务归属事件、result、回执 |
| 微信旧文案为何未发 | task generation、delivery generation、claim 状态 |
| 工具或权限 | channel gate、`service_state`、`errors` |

常用只读命令：

```sh
ceo-agent produce-once
ceo-agent consume-once
ceo-agent run-once
ceo-agent audit-web --host 127.0.0.1 --port 8765
```

## 相关文档

- `README.md`：产品目标、快速开始和运行配置。
- `docs/product-logic.md`：产品规则和业务处理逻辑。
- `docs/message-routing-rules.md`：消息路由规则。
- `docs/reply-worker-reliability.md`：Direct Agent 回复链路可靠性。
- `docs/dws-capabilities.md`：DWS 能力说明。
- `docs/agent-installation-runbook.md`：安装和初始化。
- `docs/superpowers/plans/`：历史计划，仅用于设计追溯。
