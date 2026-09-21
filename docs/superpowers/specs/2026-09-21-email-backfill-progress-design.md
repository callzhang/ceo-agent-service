# Email 历史回溯进度设计

## 目标

Email Console 顶部直接增加一个紧凑的“历史邮件处理进度”区域，准确显示当前由本地模型或 Agent 执行的历史回溯，而不是把单次扫描批次伪装成整个回溯窗口的进度。

当模型未上线时，当前处理者为 Agent，范围使用账户的 `agent_lookback_days` 和 `scan_read_state`。当模型上线后，模型独占新的分类扫描，范围使用 `model_lookback_days` 并覆盖已读和未读邮件；不会按邮件时间把近期邮件继续交给 Agent。模型不确定的结果保存为 `pending_feedback`，也不会转交 Agent。

进度需要回答四个不同问题：

1. 固定回溯范围内一共发现多少个待处理邮箱对象；
2. 模型或 Agent 已经完成多少个分类判断，还剩多少个；
3. 其中多少封需要人工确认；
4. 已接受分类生成的邮箱动作还有多少未在 provider 端完成。

## 非目标

- 不重新分类已经有稳定分类结果的邮件。
- 不把 `pending_feedback` 交给 Agent。
- 不把邮箱动作完成数混入分类进度分母。
- 不修改类别、阈值、训练、人工反馈或 provider action 的现有业务规则。
- 不在本功能中修改全局“五分钟无输出即终止任务”的 watchdog。进度接口提供 `last_progress_at` 和停滞时长，实际进程终止规则作为独立修复实现和验证。
- 不为历史回溯引入新的队列服务、数据库或前端框架。

## 为什么不能直接使用现有计数

当前 Agent 路径有持久化的 `email_agent_classification_tasks`，可以直接计算 pending、running、done 和 failed。当前模型路径没有同等的任务队列：每次定时扫描执行一次 IMAP SEARCH，最多读取 50 封正文，逐封完成模型推理并保存分类。`persisted_count` 只表示这一批成功保存了多少封，不代表一年窗口的总量。

同时，已经自动移动的邮件会离开 Inbox，新邮件会进入 Inbox，人工确认和 provider action 又发生在分类之后。用“当前 Inbox 数量”作分母会导致百分比倒退；用 `processed + pending_feedback` 作总量则只计算已经分类的记录，永远看不到剩余邮件。

## 采用方案：固定快照的持久化回溯批次

每次需要历史回溯时先创建一个 backfill run。发现阶段只读取符合范围的 UID，不下载正文；所有账户和来源文件夹都完成发现后，冻结分母，才进入可显示确定百分比的运行阶段。

回溯 run 的处理者由创建时的有效 runtime 决定：

- `agent`：无上线模型时使用 Agent 窗口和读取范围；
- `model`：模型上线后使用模型窗口，已读和未读均包含在内。

同一个 run 不会同时使用两种处理者。runtime、模型、窗口或来源配置变化后，旧 run 结束为 `superseded`，未分类的当前可见邮件进入一个新快照；已有稳定分类仍然不会被重复处理。

### 与实时新邮件的边界

固定快照只负责创建时可见的历史 backlog。每个来源记录自己的 UIDVALIDITY、最高 UID 和快照时间；快照之后到达的新 UID 不改变当前分母。历史 run 追平以后，新邮件继续由现有定时扫描按当前 runtime 处理，并在顶部进度区显示“历史邮件已追平”。只有以下变化会创建新的历史 run：

- runtime 在 Agent 与模型之间切换；
- 上线模型身份变化；
- Agent 或模型回溯天数变化；
- Agent 已读范围变化；
- 启用账户或来源文件夹集合变化。

普通新邮件到达不会持续创建 backfill run。

## 数据模型

在 Email SQLite schema 中增加三个关系表。具体命名可以在实现时遵循现有 `email_*` 命名，但字段语义不可改变。

### `email_backfill_runs`

一行表示一个跨账户的历史回溯批次。

- `run_id`：稳定主键；
- `processor`：`model` 或 `agent`；
- `runtime_mode`、`model_id`、`config_version`：创建 run 时冻结的分类环境；Agent run 的 `model_id` 为空；
- `scope_fingerprint`：账户、文件夹、窗口和读取范围的规范化摘要，用于幂等创建和变更检测；
- `status`：`discovering`、`running`、`completed`、`completed_with_errors`、`failed` 或 `superseded`；
- `started_at`、`snapshot_completed_at`、`last_progress_at`、`completed_at`；
- `error`：run 级发现或一致性错误，不保存邮件正文。

run 级总数和进度不作为可漂移的累计字段保存；接口按 item 状态实时聚合。这样崩溃或重试不会让计数和事实表分叉。

### `email_backfill_sources`

每行表示 run 中的一个账户和来源文件夹快照。

- `run_id`、`account_id`、`folder`：唯一来源身份；
- `lookback_days`、`read_scope`；
- `uidvalidity`、`snapshot_max_uid`、`snapshot_at`；
- `status`：`pending`、`ready` 或 `failed`；
- `discovered_count`、`error`。

只有全部来源均为 `ready` 时，run 才从 `discovering` 进入 `running`。因此确定进度条出现后，分母不再增加。

### `email_backfill_items`

每行表示一个来源快照中的 provider UID，并作为模型和 Agent 路径共享的持久化工作清单。

- `run_id`、`account_id`、`folder`、`uidvalidity`、`uid`：唯一快照成员；
- `status`：`pending`、`queued_agent`、`processing_model`、`processed`、`pending_feedback`、`satisfied_existing`、`skipped_missing` 或 `failed`；
- `classification_id`、`agent_task_id`：已有结果或 Agent 任务的可选关联；
- `attempt_count`、`last_error`、`available_at`、`owner`、`lease_expires_at`；
- `created_at`、`updated_at`、`completed_at`。

item 不保存邮件正文、模型输入或退订 URL。现有分类表和 Agent task 表继续拥有这些数据。

### 约束与索引

- 唯一约束：`(run_id, account_id, folder, uidvalidity, uid)`；
- 一个 run 只能有一组固定 processor 和 scope fingerprint；
- `running` run 的所有 source 必须为 `ready`；
- 为 `(run_id, status)`、`(account_id, folder, uidvalidity, uid)` 和 `agent_task_id` 建索引；
- active run 的创建在 `BEGIN IMMEDIATE` 事务中完成，避免两个定时触发创建重复 run；
- 外部 IMAP 读取不放在数据库事务内。

## 发现与冻结分母

IMAP adapter 增加只读 UID snapshot 方法。它使用与当前扫描相同的 SINCE、UNSEEN/全部和文件夹规则，但只执行 SEARCH，不获取 BODYSTRUCTURE、header 或正文。返回：

- 当前 UIDVALIDITY；
- 搜索结果中的 UID 列表；
- 本次列表中的最高 UID；
- provider 读取时间。

发现阶段按 source 写入 UID item。已有稳定分类或稳定 Agent task 的 UID 不进入本轮剩余 backlog；这些历史事实通过单独的累计指标显示，而不伪装成本轮已处理。这样首次部署时，本轮进度从“当前真实剩余”开始，不试图不可靠地重建部署前已离开 Inbox 的原始分母。

如果任一 source 遇到可重试的 provider 错误，该 source 回到 `pending` 并保留错误，run 保持 `discovering`；不可重试的身份或一致性错误才把 source 和 run 置为 `failed`。UI 使用不确定进度状态并展示失败来源，不能先显示一个之后还会增长的百分比。重试必须幂等覆盖同一个尚未冻结的 source，不得重复插入 item。

## 处理流程

### 模型 run

1. 定时命令从 active run 领取一批 `pending` item，并写入 owner、lease 和 `processing_model`；
2. provider 按明确 UID 获取邮件，且要求 UIDVALIDITY 与快照一致；
3. 使用 run 冻结的上线模型和 canonical model input 推理；
4. 高置信度结果沿用现有分类和动作计划持久化，item 变为 `processed`；
5. 模型拒绝、`others` 或类别未晋升沿用现有 `pending_feedback` 持久化，item 变为 `pending_feedback`，不创建 Agent task 或动作计划；
6. 分类结果与 item 终态在同一 SQLite 事务中提交，防止“邮件已分类但进度仍 pending”；
7. 每完成一个 item 更新 run 的 `last_progress_at`。

技术失败不伪装成人工待确认。可重试失败保留为 `pending` 并记录 `attempt_count`、`last_error` 和下一次可用时间；现有 scheduled command 在后续轮次重试。不可重试的单邮件错误进入 item `failed`，run 在所有 item 终态后成为 `completed_with_errors`；source 身份、UIDVALIDITY 或 run 级一致性错误使整个 run 成为 `failed` 或 `superseded`。

### Agent run

1. 定时命令从 active run 读取 `pending` item并按明确 UID获取邮件；
2. 使用现有 `EmailClassificationTaskProducer` 创建稳定 Agent task；
3. item 保存 `agent_task_id` 并变为 `queued_agent`；
4. Agent task 的实际 pending、running、done 和 failed 仍由现有 task adapter 管理；
5. 进度投影根据 task 与最终分类记录把 item 变为 `processed`、`pending_feedback` 或 `failed`。

模型上线后不创建新的 Agent run 或 Agent task。切换前已经存在的 Agent task 不与模型 run 重复领取：其 UID 从模型快照中排除，并在 UI 单独显示为“遗留 Agent 队列”。

### 复制、移动和 UID 变化

- 同一稳定邮件身份已经由另一个 UID 完成分类时，item 进入 `satisfied_existing`，并沿用现有分类重放逻辑；
- 邮件在处理前被用户移动或删除，精确 UID 已不存在时，item 进入 `skipped_missing`，不伪装成已分类；
- provider action 在分类提交后移动邮件，不影响已经终态的 item；
- UIDVALIDITY 在运行期间变化时，旧 UID 快照不可继续使用。run 进入 `superseded`，新 run 重新发现；已经持久化的稳定分类继续去重。

## 指标定义

进度接口必须返回原始计数，前端不自行推断业务口径。

- `total_count`：快照冻结后的 item 总数；
- `automatic_count`：状态为 `processed` 的数量；
- `pending_feedback_count`：模型或 Agent 已完成判断，但需要用户确认的数量；
- `classified_count`：`automatic_count + pending_feedback_count`；
- `satisfied_existing_count`：由稳定既有结果满足的副本数量；
- `skipped_count`：`skipped_missing` 数量；
- `failed_count`：不可继续处理的 item 数量；
- `settled_count`：上述所有终态数量之和；
- `remaining_count`：`total_count - settled_count`；
- `queue_length`：`pending + queued_agent + processing_model`；
- `progress_percent`：`settled_count / total_count`，只在 run 冻结后返回；
- `historical_classified_count`：当前账户、来源文件夹和回溯窗口内本地已有的全部稳定分类累计量，不按 model、Agent 或后续 user confirmation 拆分，只作上下文且不进入本轮百分比；
- `provider_action_counts`：现有 email action 的 pending、processing、done、skipped 和 failed，独立于分类分母；
- `legacy_agent_queue_counts`：模型 active 时仍存在的旧 Agent task 状态分布。

`pending_feedback` 算作模型或 Agent“已处理”，因为分类判断已经结束；它不算 provider 动作已完成。`progress_percent` 使用所有终态，是“本轮清单已检查”的百分比；UI 同时明确显示 `classified_count`、`skipped_count` 和 `failed_count`，不能把跳过或失败写成已分类。

## API

新增只读接口：

`GET /api/console/email/backfill-progress`

响应包含：

```json
{
  "active_processor": "model",
  "run": {
    "run_id": "...",
    "status": "running",
    "lookback_days": 365,
    "model_id": "...",
    "started_at": "...",
    "snapshot_completed_at": "...",
    "last_progress_at": "...",
    "total_count": 1642,
    "settled_count": 378,
    "classified_count": 378,
    "automatic_count": 254,
    "pending_feedback_count": 124,
    "satisfied_existing_count": 0,
    "skipped_count": 0,
    "failed_count": 0,
    "remaining_count": 1264,
    "queue_length": 1264,
    "progress_percent": 23.02
  },
  "historical_classified_count": 1154,
  "provider_action_counts": {
    "pending": 23,
    "processing": 0,
    "done": 231,
    "skipped": 0,
    "failed": 0
  },
  "legacy_agent_queue_counts": {
    "pending": 0,
    "running": 0,
    "done": 1154,
    "failed": 0
  },
  "sources": [],
  "meta": {"snapshot_at": "..."}
}
```

当 run 处于 `discovering` 时，`total_count` 和 `progress_percent` 为 `null`，sources 返回发现状态和错误。当不存在 active run 时，接口返回最近完成 run 和 `caught_up: true`，而不是制造一个空的 0/0 百分比。

接口只读本地 SQLite，不在页面请求中实时访问邮箱 provider。

## Email Console

进度区直接位于 Email 页面内容顶部、tabs 上方，在“待确认”“全部”“退订记录”“分类配置”“模型训练”所有 tab 上保持可见。不再包成一张独立卡片，也不使用大号 hero 或装饰性图表；使用一条紧凑进度条和同一行或紧邻下一行的关键计数，避免挤占邮件列表高度。

### `discovering`

- 显示“正在统计历史邮件…”和不确定进度条；
- 显示已完成来源数/总来源数；
- 不显示百分比或预计完成时间。

### `running`

- 标题显示“模型回溯处理中 · 最近 365 天”或“Agent 回溯处理中 · 最近 30 天”；
- 确定进度条显示 `settled_count / total_count` 和百分比；
- 主要计数显示“已分类”“待确认”“剩余队列”；
- 次要计数显示跳过、失败和 provider action backlog；
- 显示模型名称或 Agent、开始时间和最后进展时间；
- 如果存在遗留 Agent 队列，单独提示，不并入模型进度。

### terminal

- 无失败时显示“历史邮件已追平”；
- 有跳过时显示“已追平，N 封在处理前已移动或删除”；
- 有失败时显示“本轮完成，但有 N 封处理失败”，并提供可定位的来源或错误摘要；
- 不提供手工把失败改成成功的按钮。

前端在 active 状态每 2 秒刷新，terminal 状态每 30 秒刷新；请求使用 AbortController，卸载或切换页面时取消。加载、空状态、错误和窄屏布局都要有测试。已有邮件列表的 URL 状态、分页和阅读面板不改变。

## 恢复、并发与一致性

- run 创建和 item 领取必须幂等；重复 scheduled command 不能重复快照或重复分类；
- 当前 scheduled command 的互斥行为继续阻止同一命令重叠，item lease 再提供崩溃恢复所需的持久化所有权；
- 进程退出后，过期的 `processing_model` item 回到 `pending`；已经在同一事务中提交分类结果的 item 不会重跑；
- Agent task 的既有稳定身份继续防止重复 Agent 调用；
- API 的所有计数从同一个 SQLite snapshot 读取，确保总数、剩余和分类分项可相互校验；
- `last_progress_at` 只在新增终态或来源发现推进时变化。UI 可以准确展示停滞，但本功能不自行控制或终止系统进程。

## 首次上线与当前正在进行的回溯

数据库迁移不修改现有 `email_classifications`、`email_agent_classification_tasks` 或 `email_actions`。部署后的下一次定时扫描为当前 runtime 创建第一个 run：

1. 当前仍可见且尚无稳定分类/Agent task 的 UID 进入本轮快照；
2. 部署前已经分类并移动的邮件不进入本轮分母；
3. UI 通过 `historical_classified_count` 单独展示部署前累计分类量；
4. 因此首次 run 从 `0 / 当前真实剩余` 开始，不声称能够重建部署前一年窗口的原始总量。

这保留所有现有分类和动作事实，也避免用推测值生成看似精确的百分比。

## Schema 迁移与回滚

- Email schema 从当前版本增加一个顺序 migration，创建三个新表、约束和索引；
- migration 前按现有数据库备份流程创建并验证最新备份；
- migration 不删除或重写旧表；
- 新代码在没有 run 的数据库上自动进入首次发现；
- 如需回滚到不认识新 schema 的旧二进制，先停用新 runtime，再恢复迁移前已验证备份。不能让旧二进制直接打开更高版本 schema；
- 实现提交后由 heartbeat session 按项目规则负责 service restart、迁移、PID/health/queue/Email Console readback；当前开发会话不自行重启服务。

## 测试与验收

### 后端与存储

1. UID snapshot 只执行 SEARCH，不下载正文，并正确冻结 UIDVALIDITY、最高 UID、日期和已读范围；
2. migration 从所有受支持旧版本升级到新版本，约束和索引通过完整性检查；
3. 重复发现、重复 scheduled trigger 和进程崩溃恢复不重复 item；
4. source 未全部 ready 时不返回确定分母；冻结后新邮件不改变总数；
5. 模型 accepted、pending feedback、已有稳定分类副本、provider 邮件消失和技术失败分别进入正确 item 状态；
6. pending feedback 不创建 Agent task 或 provider action；
7. Agent run 的 item 与 Agent task 状态关联，模型 active 时不创建新的 Agent task；
8. UIDVALIDITY 变化会 supersede 旧 run，并由新 run 重建剩余快照；
9. 分类和 item 终态在故障注入下保持原子性；
10. API 计数满足 `settled + remaining = total`，`automatic + pending_feedback = classified`。

### 前端

1. discovering 显示不确定状态且没有虚假百分比；
2. running 显示 processor、固定总数、已分类、待确认、剩余和最后进展时间；
3. provider action backlog 和遗留 Agent 队列与主进度分开；
4. completed、completed_with_errors、failed、caught_up、加载和接口错误状态有明确文案；
5. active/terminal 轮询周期正确，请求在卸载时取消；
6. 窄屏不遮挡计数、进度条或 tabs；
7. 原有 Email 列表分页、搜索、阅读和确认流程继续通过测试。

### 发布验收

1. 部署前备份可读且 SQLite integrity check 通过；
2. 新 PID 和 healthz 正常，Email schema 为新版本；
3. 首次 run 从 discovering 进入 running，冻结后总数在新邮件到达时不变；
4. 至少观察两个模型或 Agent 批次，`classified_count` 增长且 `remaining_count` 同步下降；
5. pending feedback 增长不会创建 Agent task；
6. accepted 分类的 provider action 独立推进并可读回；
7. 重启恢复一个未完成 run 后不会重复分类；
8. Console 没有新增 unresolved failed/processing backlog，再报告上线完成。

## 风险与控制

- **首次 UID SEARCH 返回较大列表**：只传 UID，不读正文；按来源串行发现并记录耗时。不能用固定数量截断，否则分母失真。
- **provider 在快照后移动邮件**：精确 UID 不存在时记录 `skipped_missing`，不伪装为分类成功。
- **多账户发现部分失败**：冻结前保持不确定状态；不显示会变化的百分比。
- **旧 Agent 任务与模型重叠**：从模型快照排除已有稳定 Agent task，并单独显示遗留队列。
- **计数漂移**：不维护独立累计计数；在同一 SQLite snapshot 中从 item 状态聚合。
- **旧版回滚不认识新 schema**：部署前验证备份，回滚时恢复备份，不直接运行旧二进制。

## 完成定义

功能完成必须同时满足：固定快照与持久化 item 已上线；模型和 Agent 两种 route 的进度语义通过测试；Email Console 显示真实计数和状态；运行文档同步更新；由 heartbeat session 完成迁移、服务重启、队列恢复以及 provider 结果 readback。只有前端出现进度条、接口返回 HTTP 200 或单次扫描成功，都不构成完成。
