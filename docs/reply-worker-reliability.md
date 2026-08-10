# Reply Worker Reliability

本文档描述 Consumer Agent A / Audit Agent B 回复链路的可靠性约束。目标是让服务在网络失败、
Agent 超时、进程重启和外部写入结果未知时，仍能恢复而不重复发送或伪造完成。

## 核心不变量

1. Producer 只发现触发并把精确 source revision 入队。
2. A 代表 Derek 做只读判断；B 独立审计并独占任务驱动的外部写入。
3. 同一业务对话复用一个 A session；每个候选 revision 新建一个 B session。
4. 同一任务最多两个内容反馈周期，基础设施重试不计入该上限。
5. 完全相同的 revision 不执行两次；内容修订后的新 revision 可以执行。
6. 写入结果未知时先在原 B session 中读回，禁止创建新 B 盲目重放。
7. Codex JSONL 保存详细审计，SQLite 只保存恢复所需状态。
8. 证据不足但可向参与者获得时，自动提出并发送一个具体澄清问题，不请求 Derek 选择。

## 任务、generation 与 revision

`reply_tasks` 是持久工作队列。每个 trigger 使用稳定来源身份；同一来源内容没有变化时不会
产生重复任务。显式重跑会创建新的 execution generation，但仍复用业务对话的 A session。

一个 generation 内：

- A 的首个候选是 proposal revision 0。
- B 接受后执行；要求修改时返回 `revision_required`。
- A 收到 B 的结构化反馈后生成下一 revision。
- 最多允许两个内容反馈周期。达到上限仍不能执行时进入明确终态，不无限循环。

内容反馈周期只统计 A/B 对候选业务内容的往返。Codex 启动失败、通道暂时不可用、租约恢复
和只读结果核对属于基础设施恢复，不消耗该上限。

## 会话生命周期

### Consumer Agent A

每个 `conversation_id` 绑定一个 A Codex session。新消息通过 `codex exec resume` 追加到该
session，使 A 能复用参与者、历史事实、已做决定和此前澄清结果。只有 session 文件缺失或
损坏时才轮换会话。

同一时刻只允许一个 A turn 更新该 session。服务使用短期可续租的 transcript 锁保证 JSONL
顺序；后续消息仍保留在 SQLite 队列，不因锁存在而丢失。

### Audit Agent B

每个候选 revision 使用一个新的 B session，避免前一候选的审计结论污染新候选。B 读取
最新事实和当前 Audit Rules，不能依赖 A 的结论代替独立核验。

如果一次外部写入结果未知，恢复必须复用该次 B session。只有这一场景允许 B session 跨
turn 复用，因为原 session 含有精确操作身份、工具参数和返回上下文。

## 缺失事实与人工判断

以下两类状态必须分开：

- **可通过交流补齐的事实**：A 提出一个具体、可回答的澄清问题；B 审阅并通过正常发送
  通路执行。系统不展示“继续处理 / 先追问”的二选一。
- **不可约管理判断**：即使材料完整且参与者已回答，仍必须由 Derek 决定价值取舍、授权
  边界或管理立场，才返回 `needs_human`。

例如，缺少项目截止日期时应询问项目负责人；是否接受一个已知成本与收益的战略取舍，才是
可能的 `needs_human`。权限缺失、网络失败、CLI 未登录和材料读取错误属于依赖状态，也不能
包装成管理选择。

## A/B 权限隔离

A 的 Codex 命令只注入已审核的读取工具。A 可以读取 DWS、Lark、本地 workspace、Memory、
Exa 和 Xiaoqing 中当前任务允许访问的材料，但不能调用发送、评论、审批或文档修改工具。
下载到临时目录的 Office 压缩包可以用严格只读的 `unzip -p` 管道展开到 stdout；不带 `-p`
的解压、指定输出目录或重定向写文件仍会被拒绝。

B 的命令注入相同读取能力和固定写能力。B 只能执行 A 候选中明确列出的 capability、operation、
target 和 payload；若实时读取发现业务参数需要变化，必须返回 `revision_required`，不能自行
改写后执行。

OA 待办扫描产生的是合成 trigger，不是名为“审批待办”的真实单聊。初次判断和 Audit 前刷新
都只复用持久化的 process/task ID、链接与精确 DWS 命令；service 不解析联系人、不预读审批
正文，也不按申请人或标题猜测目标。A/B 必须通过 DWS 读取实时审批详情和当前任务归属。

Audit Rules 对 A/B 同时可见，但只控制业务审阅规则。它不能改变角色权限、加载新的 MCP、
取消精确去重或扩大恢复写入范围。

## 外部写入与精确去重

每个候选动作绑定 task generation、proposal revision、operation ID、目标和参数摘要。B 在写入
前读取实时状态；若同一 revision 已有确定成功结果，则跳过该动作并记录已有结果。

去重保护的是“同一条内容不发送两次”，不是禁止修复。A 根据 B 反馈改变正文、目标或业务参数
后会产生新 revision，新 revision 经过独立审计后可以执行。

发送层仍使用持久化 claim 和 `sent_replies` 保护精确 trigger 的消息投递。claim 成功不等于
外部成功；最终状态必须来自 B 的执行结果和外部读回。

## 未知结果恢复

当 B 已开始写入但进程退出、连接断开或结果无法解析时，run 标记为 `unknown`。恢复顺序固定：

1. 领取原 unknown run，并续租原 run。
2. 使用原 B session 和原 operation ID 做只读查询。
3. 对每个原动作判断 `present`、`absent` 或 `ambiguous`。
4. `present`：收口为已确认，不再写入。
5. `absent`：只有动作仍符合固定能力边界且原参数可精确绑定时，才在同一 B session 执行。
6. `ambiguous`：保持未知或请求 Derek 处理，不重复写入。

恢复不会从自然语言猜测动作，也不会新建一个无上下文的执行 Agent。MCP 直写若缺少可验证的
精确恢复入口，即使读回为 absent 也不自动重放。

受控 CLI 读回以 `operation_digest`、目标标识和 `result_digest` 组成的结构化回执为准。回执
验证通过后，不再递归扫描可能很大的业务正文来二次判断成功；明确带错误的回执仍按失败处理。
这样大量聊天记录等合法结果不会因通用 MCP 内容大小上限被误判，同时恢复判断仍只绑定精确动作。
读写命令的目标结构不必完全相同：标记为 `shared` 的读回关系要求双方至少有一个共同目标标识，
且所有共同标识值一致。例如 OA 写入可绑定流程实例和当前 task，详情读回只绑定同一流程实例；
不同流程实例仍会被拒绝。未标记为 `shared` 的 MCP 继续要求目标标识完全一致。

## 租约与 stale recovery

Agent run 和 reply task 都有租约。每次有效 Codex 流式进度会续租正在运行的 run；stale sweep
只回收当前 generation 中没有有效 run 租约的任务。因此运行超过固定扫描周期但仍持续输出的
Agent 不会被并发重入队。

进程崩溃后租约停止续期。租约到期后，持久队列可以恢复 run：

- A 未产生候选：重试同一 generation，并复用对话 session。
- B 尚未开始写入：可以重新审计同一候选。
- B 写入状态未知：进入原 B session 的只读结果核对。

## 通道与 MCP gate

Agent 启动前，service 使用 gate 检查依赖：

```bash
.venv/bin/ceo-agent channel-doctor
.venv/bin/ceo-agent doctor-mcp --verify-live
```

`channel-doctor` 检查 DWS 和 Lark CLI 的结构化状态与认证探测。`doctor-mcp` 检查服务 MCP
清单中的 Exa、Memory Connector 和 Xiaoqing。明确 `needs_login` 时，Tutorial/Login
Coordinator 只启动一次登录流程；网络错误、命令超时或状态不可读不会触发重复登录页面。

Agent 不执行 `auth login`、`reset` 或 `logout`。依赖未就绪时任务保留为可恢复状态，等 gate
恢复后继续，不使用缺失材料生成猜测回复。

受控 DWS/Lark 通路允许以 `--help` 或 `-h` 结尾的帮助查询作为只读操作，即使查询停在命令组
而不是具体业务子命令。这样 Agent 可以先确认安装版本的真实语法；帮助查询不能获得写权限，
真正的业务命令仍按 CLI schema 中的 read/write 元数据分类。

## 用户 Codex 能力复用

服务通过原生 `codex exec` 继承安装用户的 `~/.codex` 配置、MCP、plugin、hook 和 skills。
MCP OAuth/token 继续由 Codex 原生凭证存储管理，服务不复制到 `.env`、数据库或仓库配置。
因此 Memory、Xiaoqing、Exa 和其他用户安装能力可直接为任务服务。实际调用返回未授权时，
任务记录准确的依赖失败并等待原生授权恢复；Agent 不自行触发登录。

## DWS 读取可靠性

DWS 只读调用可以对明确的临时网络、限流和服务准备错误做有界重试；写操作不使用通用自动
重试。未读消息读取使用重叠窗口防止同一时间锚点丢消息，但只把原始最新未读前缀交给解析，
不会把窗口中的旧消息提升为新 trigger。

所有经 `agent_cli.execute_reviewed_write` 执行的 DWS 写命令必须包含全局 `--yes` 参数。它仅
消除 CLI 的交互确认等待，不改变已经由 Consumer 候选和 Audit 审阅限定的目标、内容或权限。
缺少该参数的调用会在进程启动前被拒绝，避免服务任务因无终端交互而长时间占用租约。

结构化 JSON 命令允许标准进度输出，但最终结果必须是完整合法 JSON。截断或损坏的写操作
结果不会被修补为成功。

## `no_action`、`needs_human` 与失败

- `no_action`：当前 trigger 不需要外部动作。
- `needs_human`：必须由 Derek 作出的不可约管理判断。
- 可重试失败：依赖、网络或进程问题，保留在持久队列等待恢复。
- 不可重试失败：明确缺少权限、目标不存在或当前规则禁止执行，并记录具体原因。

每个 Consumer/Audit turn 都会收到明确的当前本地执行时间，同时保留 trigger 和上下文消息的
原始时间。Agent 必须结合经过时长与最新会话判断动作是否仍服务于原始意图。即时协调、澄清、
确认和提醒已经失去时效时，Consumer 返回 `no_action`；Audit 发现候选已过期时返回
`revision_required`，不能因为“尚未重复发送”就执行迟到消息。这个判断由 Agent 按通用 Audit
Rules 完成，service 不按午餐、会议等业务关键词代替 Agent 决策。

Codex 明确返回额度、配额或 usage limit 时，服务将其归类为 `codex_provider_unavailable`：不在
同一 turn 内连续重试，不消耗任务重试预算，并保留为延后执行的 pending 任务。审计图以“等待
Provider 恢复”显示这类任务。退避到期后，下一次持久队列执行会重新领取同一个无副作用 run
并真实调用 Codex；如果 Provider 仍不可用，则只执行这一次并再次延期，不能只刷新
`available_at` 而不启动 Agent。重领旧 run 时必须 resume 该 run 自己已记录的 Codex session；
同一对话后来产生的新 session 不能覆盖旧 run 的审计身份。只有当前仍处于终态的失败才使用红色。

旧版本已经落为 `failed` 的 Consumer 运行时故障，只能通过 Store 的受限恢复迁移回原 generation：
task 和指定 Consumer run 必须是同代最新 run 且仍为 `failed`，并明确 `retryable=true`、
`side_effect_state=none`；同代
不能存在 running/unknown run、已记录副作用或完成回执。恢复不会创建新 generation，也不会
丢失该 run 的 Codex session；任何条件不满足时拒绝恢复并保留原终态。

只有诊断或建议、没有完成用户要求的动作时，不能标记为执行成功。B 只有在外部系统确认结果
后才能返回 `executed`。

## Codex JSONL 与 SQLite

每个 A/B turn 的完整提示、工具调用和输出保存在 Codex session JSONL。History 通过 session
ID 和 transcript 行范围读取这些记录。SQLite 保存 task/run 关系、proposal revision、operation
ID、租约、终态、外部结果状态和精确去重键，不复制完整 transcript。

Consumer A 的 wire result 只负责传输；其中 `proposal_json` 必须解码为当前运行时
`ConsumerProposal` 模型的完整对象。模型要求目标、动作说明、能力、操作、目标参数、载荷、
预期验证、带引用的事实和 Agent 判断。旧的简化对象（例如只有 `actions` 和 `verification`）
会被严格拒绝，不做兼容补齐；运行时把该模型生成的 JSON Schema 直接交给 Agent，避免提示
文案与实际校验漂移。同一对话 session 可能含有旧版本输出，因此每个新 turn 的任务 prompt
也会携带当前 schema；Agent 复用对话事实，但不能照搬历史 wire 形状。

Consumer A 可以在 proposal 中描述受控写操作，但不得调用、试验或验证该写操作；它只能执行
已审核的读取命令。执行与外部回读均由 Audit B 在接受 proposal 后完成。

这使诊断可以回答：A 看到了什么、B 为什么要求修改、哪个 B session 执行了什么、外部结果
是否已确认，同时避免维护另一套会漂移的详细审计格式。

## 通知与审计页面

`failed`、`unknown` 和真正的 `needs_human` 会进入 History。审计页 `/notifications` 从 SQLite
按同一原始触发的最新状态列出当前 `failed`、`blocked` 和未审阅 `needs_human`。关联任务已经
完成时，旧失败和阻塞不会继续占用收件箱；未审阅的人工选择仍会保留。

若关联任务当前正在执行，或已因外部 Codex 容量问题进入定时恢复，旧 `failed` attempt 也不进入
收件箱；前者会在任务页显示处理中，后者显示等待服务商恢复。

浏览器通知按原始 trigger 使用稳定 ID，同一事项的更新不会制造并列弹窗。通知正文直接显示事项、
状态、原因和下一步，点击使用本次真实 attempt ID 打开审计详情；它不能退化为只有“X 项问题待处理”
的计数提示。任务转为终态时，关闭动作使用独立的命名浏览器事件，旧页面会忽略它而不会显示空白
通知。普通用户页面展示业务类型、对话、问题、候选/结果和恢复条件；A/B 内部标签、session ID、
token、绝对路径和原始敏感工具输出不作为默认正文展示。

处于只读核对状态的详情页从已验证的 Consumer proposal 展示具体事项和待核对动作，不能只显示
`pending` 或内部错误码。旧 attempt 只有在后续 attempt 已到业务终态时才能写“后续处理完成”；
后续 attempt 仍在核对时必须链接到最新记录并明确用户当前无需决策。

自动化测试默认拦截 macOS 和浏览器通知。只有通知专项测试可以显式替换发送函数并检查 payload；
临时测试数据库中的 attempt ID 不得进入运行中的 `127.0.0.1:8765` 通知流。

Audit Rules 在 `Config -> Audit Rules` 可查看和修改。修改后新 A/B turn 使用同一份规则；已经
完成的外部动作不会因规则变化自动重放。

## 单一 supervisor 恢复

生产只运行 `com.ceo-agent-service.main`。它的 supervisor 管理 worker 与 audit-web：

- 任一子进程退出，另一子进程会被回收；
- launchd 拉起新的 supervisor；
- worker 从 SQLite 恢复任务和租约；
- audit-web 从最近完整缓存快速提供 History，再后台预热；
- 不存在独立 audit-web launchd job。

运行代码或配置更新后：

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -fsS http://127.0.0.1:8765/ >/dev/null
```

完成报告前还要检查 reply tasks、agent runs、work summary、meeting 和外部投递队列中没有新增
`failed` 或长期 `processing`。
### Unavailable single-chat context

When a single-chat title cannot be resolved to one direct user, the worker may
continue with the triggering message and no historical context. This is not a
delivery failure and must not create an error event or user-facing failure
notification. Group-chat resolution, authorization failures, transport errors,
and every attempted external action remain error-reporting paths.

### Recent error reconciliation

The hourly quality check treats an error as open only until a later durable
attempt for the same conversation and trigger records a completed delivery or
operation. This keeps the audit evidence while preventing a recovered retry
from remaining a red service failure for the rest of the repair window.

### Consumer/Audit structured output

Consumer A and Audit B use Codex native `--output-schema`. Their wire schemas keep
dynamic nested proposal and receipt data in JSON strings, then decode and validate
those values against the full Pydantic business contracts before any action can be
accepted. The service does not fall back to the former result shape when the wire
schema is violated.

For an executed Audit result, `external_result_json` has one strict shape:
`operation_id` must match the reviewed proposal, `verification_summary` describes
the live readback, and `live_result_reference` contains the external identifiers
needed to locate that readback. Richer free-form execution summaries do not replace
these fields; this keeps successful writes machine-verifiable and recoverable.

For WeChat, a rotated generation may replace a delivery only while it is unsent.
`ready_to_send`, `superseded`, and a sender-confirmed `action_not_performed`
outcome are replaceable; `sending` and `send_unknown` remain immutable until
read-only reconciliation resolves whether the message was sent.

Audit recovery encodes `reconciliation_json` as a JSON array string. Each entry
contains only `action_index`, `disposition`, and `read_result_digest`; operation
identity remains in the persisted run and must not be repeated as an outer JSON
envelope. This keeps the transport shape unambiguous while the full contract binds
each disposition to a completed, target-matching read event from the current recovery
turn. Repeating an exact scoped read does not invalidate another scoped read from the
same turn; unrelated or historical digests remain invalid.

`reconciled` is limited to an explicit unknown-outcome recovery turn. During a
normal Audit review, live evidence that the proposed action already happened is
revision feedback, not recovery: B returns `revision_required`, A revises the
proposal to `no_action`, and neither Agent repeats the external action.

The audit UI keeps `pending_reconciliation` as the persisted lifecycle value but
renders it as a read-only verification state. The detail page states why the
result is unknown, that no duplicate approval or message will be attempted, and
that the user has no decision to make until reconciliation returns evidence.

Failed read-only reconciliation attempts keep the external result unknown and
use persisted exponential retry delays from one minute up to fifteen minutes.
They are never immediately reclaimed in a hot loop, and the delay does not
authorize replaying an approval, message, or other external action.

Audit validates the mechanical command contract before starting an execution.
A DWS write without `--yes` is returned to Consumer A as a revision instead of
being attempted. If an older persisted candidate reaches unknown-outcome recovery
with that invalid command, the service rotates to a new Consumer generation; it
does not ask the user to choose and does not replay the old command.

Only the first Codex turn started for a Consumer or Audit invocation is part of
that business run. Plugin stop hooks may open later turns for tasks such as
durable-memory maintenance; their messages and tool events are not parsed as the
business result and cannot change its side-effect state.

Controlled CLI readback compares stable target identity rather than spelling of
equivalent DingTalk flags. `group`, `conversation`, `conversation-id`, and
`open-conversation-id` identify the same conversation namespace; unrelated
person, task, approval, and document identifiers remain distinct and cannot
confirm one another merely because their values happen to match.
Direct-message commands retain `user` and `uuid` as stable target identifiers.
Unknown-result recovery must use a target-scoped read with the same conversation,
recipient, approval, or idempotency identity; a global keyword search cannot
confirm an action performed for one recipient.
Recovery starts with the smallest recent target-scoped window that can decide the
exact outcome. It does not start with an unbounded or `--page-all` history read;
older pages are fetched only when the recent window cannot decide. A partial
window may prove presence when it contains the exact action, but it cannot prove
absence.
The reconciliation result must cite the digest of a completed matching read in
that turn. Repeating the same scoped read is allowed; the service does not rewrite
the Agent's cited digest and rejects an unrelated or historical digest.

Unknown DingTalk message delivery uses the reviewed command descriptor and the
service delivery ledger as its identity. The Agent's free-form operation label
does not decide whether an action is a message send. When the exact trigger has
no delivery record, the unknown run is closed as absent and the task rotates to
a fresh generation; the old write is never replayed.

Browser notification clicks call the local DingTalk bridge with `POST`, matching
the bridge's external-action boundary. The click may then focus an existing audit
window and navigate it to the exact attempt detail; it does not issue a `GET`
that fails with 405 or opens a duplicate browser window.

Consumer and Audit Agents use the same `agent_cli.execute_reviewed_read` path
for classified local read-only commands such as `sed`, `find`, and `rg`. The
classifier continues to reject writes, redirection, and unknown shell commands;
Audit can repeat a local evidence read before publishing exact file-derived
content instead of trusting a value copied only into Consumer's proposal.
Reviewed DWS and Lark reads use the principal's existing local CLI credential
store. Agents never start a separate login flow or copy credentials into prompts,
receipts, or service configuration.

An `agent_cli` command that returns a structured error receipt is recorded as a
failed effect with its retryability and channel state. It is not treated as an
unreviewed tool call or an unknown successful write. Native DWS/Lark commands may
run for up to 15 minutes, matching the documented CLI timeout and remaining below
the enclosing Agent turn limit.
