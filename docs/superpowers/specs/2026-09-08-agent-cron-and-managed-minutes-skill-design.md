# Agent Cron 与托管听记 Skill 设计

**日期：** 2026-09-08
**状态：** 已实施并上线；2026-09-09 按实际实现修订（见文末《修订记录》）

## 目标

在 CEO Agent Service 中增加一级导航“定时任务”，用统一的用户模型管理所有主动、周期性执行：

```text
定时任务 = Cron + 执行形式
  执行形式 = Agent 任务（Skills + Runtime + 任务描述）
           | 服务命令任务（服务命令目录中的一个确定性命令，进程内执行，不经过 Agent）
```

Agent 任务适合需要判断的检查；服务命令任务适合“只跑一条确定性 producer 命令”的检查，例如
钉钉消息和微信消息的增量读取，这类检查放进 Agent 只会消耗模型调用并在历史里制造噪音。

同时把已经停止更新的“每日 AI 听记同步”迁为 CEO Agent Service 管理的定时任务，并将其业务方法定义为 service-managed Skill `ceo-minutes-sync`。

本设计还统一现有 Consumer 队列的检查和分发机制：数据发现由 Agent Cron 触发，Cron 产生的执行输入由一个内部 Dispatcher 领取，并交给相应 Consumer；Consumer 队列检查本身不是用户可配置的 Cron。

## 已确认的产品决定

1. Cron 与 Skill 是两个模块。Cron 在顶部导航，Skill 继续在 Settings 管理。
2. Connector 与 Cron 完全解耦。Connector 只提供认证、连接、权限和确定性读写能力，不保存扫描频率或业务计划。
3. 用户可见的定时任务只有一个列表，不增加独立的“系统 Cron”类型；同一个列表里的任务有两种
   执行形式（Agent 任务、服务命令任务），都有 Cron、时区、启停、手动运行和运行历史。
4. Settings 不提供 Agent Cron 页面，managed Skill 定义也不增加 `trigger: cron`。
5. Agent 任务配置任务描述、Cron、时区、Skill 引用和 Runtime；Runtime 选项来自当前 Agent Runtime
   配置，不写死 CEO Agent、Codex 或 Claude 枚举。服务命令任务只配置名称、Cron、时区、启停和
   命令；命令来自服务命令目录，页面不得为它显示假的 Runtime、提示词或 Skill 控件。
6. DingTalk 消息、会议、WeChat 消息、OA、听记、Lark 等主动检查均通过 Agent Cron 表达；没有 Cron 的 Connector 不会主动发起业务检查。
7. Consumer 队列检查是常驻内部逻辑。一个 Dispatcher 统一发现可执行输入，再按输入类型交给不同 Consumer；用户不配置 Consumer 轮询频率。
8. 投递、外部效果确认、Consumer → Audit → feedback → revision 和任务恢复继续属于内部任务生命周期。
9. 不增加“微信复盘”“重要消息日报”或其他新的微信业务目标；迁移只承接已有微信检查行为。
10. 不补跑服务停机期间错过的 Cron。服务恢复后从下一个未来触发点继续。
11. 同一个定时任务上一轮仍未结束时，当前触发记为 `skipped`，不并发重复执行。
12. 手动运行不改变下一次 Cron 时间。
13. Runtime 不可用时不静默切换到其他 Runtime；任务保留，本轮记录 `skipped`。配置性不可用
    （Runtime 未配置、缺少所需能力、认证暂停、Skill revision 不可用）每次进入 Attention；
    provider 暂时不可用导致的路由暂停（过载、传输断连）只体现为运行记录和路由暂停状态，不按
    每次触发写 Attention，以免一次外部故障在每个任务上刷出成串错误。
14. 托管 Skill 绑定精确 revision，不自动升级到新 revision。
15. 服务命令任务在 Dispatcher 领取 trigger 后直接在服务进程内执行，不创建 reply task、agent run
    或 reply_attempt；命令必须幂等。仓库 seed 的服务命令任务不能通过 API 改成别的命令。

## 范围

### 本次范围

- 顶部导航新增“定时任务”。
- Agent Cron 的创建、编辑、启停、手动运行、删除和运行历史。
- Cron 到期计算、触发去重、无补跑、重叠跳过和时区处理。
- 从 Agent Runtime 当前配置动态提供执行方式。
- 引用 service-managed Skills 和已安装 operation Skills。
- `ceo-minutes-sync` 托管 Skill 及每日听记同步任务。
- 将当前服务内的周期性业务发现迁为 Agent Cron，并移除对应的隐藏计时入口。
- 一个内部 Consumer Dispatcher，通过 Queue Adapter 统一检查和分发不同业务队列。
- Settings、架构文档、运行机制文档和操作文档同步更新。
- 暗色模式、窄窗口和错误状态下的 UI 验收。

### 非目标

- 不把 Connector 做成调度器。
- 不在 Skill frontmatter 中增加 Cron、触发器或执行频率。
- 不把所有业务表强制迁入一张“万能队列表”。
- 不合并 DingTalk、WeChat、Meeting 等 Consumer 的业务实现。
- 不新增微信复盘、摘要或主动发送目标。
- 不自动导入其他 Codex 项目的 Automations。
- 不改变已有发送授权、联系人范围、群聊规则、审核规则或外部效果确认策略。
- 不为本功能附带新增审核、安全门或人工确认逻辑。
- 不补跑历史漏掉的触发点。

## 用户模型与信息架构

### 顶部导航

“定时任务”与 Agent、History、Tasks、Email、Settings 平级。它是 Agent Cron 的唯一入口。

页面采用已确认的紧凑布局：

- 左侧或窄屏顶部：任务列表、启用状态、可读计划、下次运行和最近结果。
- 主编辑区：任务名称、执行计划、任务描述、Skill 引用、Runtime、运行状态和操作按钮。
- 暗色模式使用高对比文本、清晰边界和单一主要操作色。
- 窄窗口改为任务卡片在上、单列编辑器在下，不保留拥挤的双栏。

### Settings

Settings 保留以下职责：

- `Skills`：托管 Skill 内容、revision、功能关联和启用状态。
- `Agent Runtime`：可用 Runtime、模型、thinking、凭据与健康状态。
- `Connectors`：DingTalk、Lark、WeChat、Email 的认证、权限、连接测试和提供者策略。
- 现有 Prompts、Audit Rules、Configuration、Status 和 Attention。

Settings 删除或不新增以下内容：

- Agent Cron 页面或侧栏项。
- Connector 扫描频率。
- Consumer 队列轮询频率。
- Skill 上的 `trigger: cron`。

### 定时任务编辑字段

每个任务包含：

- 名称；
- Cron 表达式与可读的计划预览；
- 时区；
- 启用状态；
- 执行类型：Agent 任务，或服务命令目录中的一个命令（显示可读名称和说明）；
- Agent 任务另有：自然语言任务描述、Runtime、Runtime 支持的模型和 thinking 参数、一个或多个
  Skill 引用；工作目录使用服务工作区，不在任务里重复配置；
- 可读的下一次运行时间；
- 最近运行状态和运行历史；服务命令的运行记录显示命令的可读名称。

服务命令任务的页面只显示命令及其说明，并说明它发现的消息由统一 Dispatcher 的 reply consumer
处理、该 consumer 的提示词、Skill 和 Runtime 路由由服务维护；不显示无效的编辑控件。

任务描述使用与 Agent Composer 一致的交互，支持通过 `$` 搜索和引用 Skill。页面同时以独立标签显示已解析的 Skill，避免只依赖正文中的字符串。

## Connector、Skill、Runtime 与 Cron 的边界

### Connector

Connector 是 Agent 可调用的能力，不是触发器。它负责：

- 登录、Token、Reader/IPC 或 API 连接；
- 权限和可用范围；
- 连接健康与手动测试；
- 确定性读取和写入；
- 已有提供者策略，例如 WeChat `auto/confirm`、联系人范围和群聊 `@` 规则。

Connector 不知道任务何时运行，也不拥有 Cron、Prompt、Skill 或 Runtime。

### Skill

Skill 定义 Agent 如何完成一类任务，包括业务边界、读取方式、工具使用和验证要求。Skill 不拥有调度计划。

Service-managed Skill 使用不可变 revision。定时任务明确绑定一个 revision；保存新 revision 不改变已有任务。外部 operation Skill 以来源和名称引用，每次运行记录实际加载的来源与内容摘要。

### Runtime

Runtime 决定由哪个 Agent 执行任务。可选项完全来自 Settings → Agent Runtime 的当前配置：

- 已配置且可用：可以选择；
- 已配置但暂不可用：保留显示但禁用，并说明原因；
- 未配置：不显示。

任务保存后，如果所选 Runtime 变为不可用，系统不自动 fallback。到期时不创建新的业务执行任务，而是记录跳过原因并在 Attention 中提示。

### 服务命令

服务命令是服务自身的一个确定性操作，与对应的 `app.cli` / `app.wechat.cli` 子命令做同一件事。
命令目录是唯一的名字来源（当前：`produce-once` 钉钉消息增量读取、`wechat-produce-once`
微信消息增量读取），服务启动时必须把目录里每个名字绑定到进程内实现。命令不拥有 Cron，也不
经过 Runtime、Skill、Consumer 或 Audit。Reader 或账号不可用属于该通道的健康事实，由命令自己
按原内部循环的语义报告（只记一次、成功后恢复），不是 trigger 失败。

### Cron

Cron 只决定何时为一个已保存任务创建新的执行输入。首版使用带秒的六段表达式：

```text
秒 分 时 日 月 周
```

这样既能表达每日、每周计划，也能迁移当前 15 秒、60 秒级检查。UI 优先显示“每分钟”“每天 20:00”等可读计划，同时保留高级 Cron 编辑和即时校验。

## 数据模型

### `scheduled_tasks`

保存用户配置和迁移后的种子任务：

- `id`
- `migration_key`：可空；系统迁移任务使用稳定键防止重复创建
- `name`
- `prompt`：Agent 任务的任务描述；服务命令任务为空
- `command`：服务命令任务的命令名；Agent 任务为空。一个任务只能是其中一种
- `cron_expression`
- `timezone`
- `runtime_id`、`runtime_options_json`、`required_runtime_capabilities_json`、`working_directory`：
  仅 Agent 任务；服务命令任务为空
- `enabled`
- `version`：用于并发编辑检查
- `created_at`
- `updated_at`

### `scheduled_task_skill_refs`

保存结构化 Skill 引用：

- `scheduled_task_id`
- `skill_source`
- `skill_name`
- `managed_skill_id`：仅托管 Skill
- `managed_revision_id`：仅托管 Skill，必须是精确 revision
- `position`

不得仅从 Prompt 中正则提取 `$skill` 作为执行依据。Prompt 中的显示引用与结构化引用由编辑器一起维护，后端以结构化引用为准。

### `scheduled_task_runs`

保存一次触发事件和配置快照：

- `id`
- `scheduled_task_id`
- `trigger_kind`：`scheduled` 或 `manual`
- `scheduled_for`
- `dispatch_status`：`pending`、`dispatched`、`skipped` 或 `failed`
- `skip_or_error_reason`
- 任务描述、Cron、时区、Runtime 和 Skill 引用快照
- `execution_kind`：`reply_task`（Agent 任务，被分发到 Scheduled Agent Consumer）或
  `service_command`（服务命令已在本进程内跑完）
- `execution_id`：`reply_task` 时为该 Consumer 事实来源中的任务 ID；`service_command` 时为命令名
- `created_at`
- `dispatched_at`

快照同样包含 `command`。`scheduled_task_runs` 只记录触发与分发，不复制 Consumer/Audit 的业务状态。分发成功后的执行结果通过 `execution_kind + execution_id` 解析到对应 Consumer 的事实来源、Agent runs 和 History，避免两套状态漂移。

同一任务的 `scheduled_for` 必须唯一；手动运行使用独立事件 ID。Scheduler 在同一事务内创建 run 并取得分发所有权，避免服务重启或多个 Scheduler 重复派发。

## Scheduler 语义

### 正常触发

1. Scheduler 读取启用任务及其下一未来触发点。
2. 到期时原子创建 `scheduled_task_run`。
3. 校验同一任务没有未结束的关联执行。
4. Agent 任务校验绑定的 Runtime 和 Skill revision 仍可用；服务命令任务校验命令仍在目录中。
5. `ScheduledTaskQueueAdapter` 将这条 `pending` run 暴露给内部 Dispatcher。
6. Dispatcher 原子领取 run。Agent 任务：创建唯一的 `channel=scheduled` 执行输入并写入
   `execution_kind=reply_task + execution_id`，交给 Scheduled Agent Consumer。服务命令任务：在同一
   claim 内直接运行命令，成功后写入 `execution_kind=service_command + 命令名` 并标记 `dispatched`；
   命令抛错时 trigger 记 `failed`，原因 `scheduled_task_service_command_failed: <原因>` 进入 Attention。
7. 下一次触发点只按 Cron 和时区计算。已完成的服务命令执行由构造保证终态；命令仍在运行时
   trigger 保持 `pending`，同一任务不会并行跑第二次。

### 不补跑

服务启动或恢复时，不枚举停机期间错过的触发点。每个任务直接计算“当前时间之后的下一次运行”。手动运行是唯一的显式补执行方式，并且不移动正常 Cron。

### 重叠运行

如果上一轮关联任务仍处于非终态：

- 当前触发记录为 `skipped`；
- 原因为“上一轮仍在运行”；
- 不创建第二个业务任务；
- 不改变下一次 Cron；
- 不等待上一轮结束后补发本轮。

### 配置或能力不可用

- Cron 或时区无效：拒绝保存。
- Runtime 不可用：任务保留；到期记录 `skipped`，不 fallback。配置性不可用进入 Attention；
  provider 暂时不可用导致的路由暂停不逐次写 Attention（见决定 13）。
- 托管 Skill revision 不存在或不可用：任务保留；到期记录 `skipped`，进入 Attention。
- 服务命令不在目录中：任务保留；到期记录 `skipped`，原因
  `scheduled_task_service_command_unavailable`，进入 Attention。
- Connector 在 Agent 执行期间不可用：由 Skill 和现有 Consumer/Audit 生命周期形成可见的失败或需要处理结果，不在 Scheduler 中伪造成功。

## 内部 Consumer Dispatcher

### 原则

Consumer 队列检查是服务内部机制，不是 Cron，也没有用户可编辑频率。统一的是检查、领取、容量和分发；不同 Consumer 的业务处理保持独立。

### Queue Adapter

不新建一张复制所有业务状态的通用队列表。Dispatcher 通过小型 Queue Adapter 使用现有事实来源：

- `ReplyTaskQueueAdapter`：现有 `reply_tasks`，覆盖 DingTalk、WeChat 和其他消息任务；
- `MeetingQueueAdapter`：现有 `meeting_alignment_jobs`；
- `WorkSummaryQueueAdapter`：现有 `work_summary_inputs`；
- `ScheduledTaskQueueAdapter`：以 `pending` 的 `scheduled_task_runs` 作为唯一待分发输入，不再复制一张调度队列表。

每个 Adapter 只负责：

- 报告是否存在已到期且可领取的输入；
- 原子 claim；
- 返回统一分发信封，包括输入类型、来源 ID、可用时间、优先级、尝试次数和 execution generation；
- 将完成、失败或重新可用状态写回原事实来源。

### 分发

Dispatcher 使用一个内部唤醒入口：生产者或 Scheduler 写入后主动唤醒，并保留有界等待作为跨进程写入与异常恢复的兜底。它不使用用户 Cron，也不在 UI 产生空轮询记录。

Dispatcher 领取任务后按输入类型送到对应 Consumer Worker Pool：

- Scheduled trigger consumer：Agent 任务生成执行输入；服务命令任务直接在进程内执行命令；
- 通用 Scheduled Agent Consumer；
- DingTalk Consumer；
- WeChat Consumer；
- Meeting Consumer；
- Work Summary Consumer。

耗时 Agent 执行不得阻塞 Dispatcher。每类 Consumer 保留与业务一致的并发约束：例如 WeChat 同一会话不并发，Meeting 不占满所有 Agent 容量。全局 Agent 容量来自 Runtime，不给每个 Consumer 增加用户可见的轮询设置。

### 任务生命周期

所有新 Agent Cron 执行继续使用现有统一生命周期：

```text
Consumer Agent
  -> Audit Agent
  -> feedback_provided 时创建新 revision
  -> 再执行、再审核
  -> completed / failed / needs_human
```

不增加 `discard` 或 `discarded`。故意不可执行使用 `skipped`；运行失败使用 `failed`；审核要求修正使用 `needs_feedback`/现有反馈语义；反馈周期无法解决时使用 `needs_human`。原始运行和每次 revision 都保留。

投递、发送状态、未知外部效果和后续确认继续由现有内部机制管理，不作为独立 Cron。

## 会议等待窗口

当前 `settle_seconds=600` 的实际含义是：会议结束后等待 10 分钟，才允许读取和处理会议资料：

```text
eligible_at = ended_at + 10 minutes
```

它用于避免在 AI 听记、摘要、参会人或日历关联证据尚未生成完成时过早处理，不是 Connector 的轮询频率。

迁移后不在 Settings 增加 `settle` 配置。种子会议 Agent Cron 的任务描述明确“只处理已经结束至少 10 分钟且资料可读取的会议”，并引用 `ceo-meeting-work`。Cron 决定检查时机，任务描述与 Skill 决定会议是否已具备处理资格；用户以后可以通过明确编辑该任务调整等待窗口。

## `ceo-minutes-sync` 托管 Skill

新增 repository-owned、service-managed Skill `ceo-minutes-sync`。它定义：

- 搜索新增或新获得访问权限的 DingTalk AI 听记；
- 正确分页并读取听记基本信息、摘要和完整逐字稿；
- 使用 `dingtalk-minutes` 完成正常读取；
- 仅在访问被拒且需要申请权限时使用 `dingtalk-minutes-access-request`；
- 将成功读取的内容归档到约定的本地工作数据目录；
- 持久化内容游标，下一次运行可以发现上次内容边界以前仍缺失的材料；
- 不把“进程成功”或“列表请求成功”当作内容已经同步；
- 输出本次发现、成功、跳过、无权限和失败的可核验结果。

Skill 不包含 Cron。种子任务单独配置：

```text
名称：每天同步 AI 听记
Cron：北京时间每天 20:00
Runtime：当前默认且可用的 CEO Agent Runtime
Skills：ceo-minutes-sync 的精确 revision
```

首轮可通过手动运行主动补齐当前内容缺口，但 Scheduler 本身不提供历史触发点补跑。

现有依赖固定浏览器客户端版本的旧脚本不再作为成功前提；Runtime 通过 Skill 使用当前已安装且可用的 Connector 能力，并在运行快照中记录实际能力来源。

## 业务任务迁移

迁移使用稳定 `migration_key`，重复启动或重复升级不会创建副本。每项先创建新任务并验证可执行，再移除对应旧计时入口；同一业务行为不得长期双跑。

首批迁移任务：

| 任务 | 迁移默认计划 | 执行形式 | 主要 Skill |
| --- | --- | --- | --- |
| 检查 DingTalk 消息 | 每分钟 | 服务命令 `produce-once` | 无；发现的消息由 reply consumer 处理 |
| 检查新增会议 | 每分钟 | Agent | `ceo-meeting-work`、`dingtalk-minutes`、`dingtalk-calendar` |
| 检查 WeChat 消息 | 每 15 秒；上一轮未结束时跳过本轮 | 服务命令 `wechat-produce-once` | 无；发现的消息由 wechat reply consumer 处理 |
| 同步 AI 听记 | 北京时间每天 20:00 | Agent | `ceo-minutes-sync` |
| 检查 DingTalk OA | 每小时 | Agent | 对应 DingTalk/OA Skills |
| 扫描工作来源 | 每天 | Agent | `ceo-work-tracking` 及来源 Skills |
| 每周 OKR 汇总 | 北京时间周日 18:00 | Agent | `ceo-weekly-okr-report`、`dingtang-okr-review` |

已经以 Agent 形式创建过的钉钉消息、微信消息 seed 在启动时原地转换为服务命令形式：保留名称、
Cron、时区；从未编辑过的旧 seed 转换后启用（它原来的停用只反映 Agent 形式缺少 Skill 或
Runtime），用户编辑过的保留用户的启用状态；已删除的不动。

Lark 不自动创建没有明确目标的种子任务。用户可以在顶部“定时任务”新建 Lark 检查，选择所需 Lark Skills 和 Runtime。

已存在于 Codex Automations、其他项目或 macOS LaunchAgents 的任务不自动导入。导入功能不属于首版。

## API 边界

提供独立于 Settings 的窄接口：

- `GET /api/console/scheduled-tasks`
- `POST /api/console/scheduled-tasks`
- `GET /api/console/scheduled-tasks/{id}`
- `PUT /api/console/scheduled-tasks/{id}`，携带 `version` 做并发编辑检查
- `DELETE /api/console/scheduled-tasks/{id}`
- `POST /api/console/scheduled-tasks/{id}/run`
- `POST /api/console/scheduled-tasks/{id}/enable`
- `POST /api/console/scheduled-tasks/{id}/disable`
- `GET /api/console/scheduled-tasks/{id}/runs`
- `GET /api/console/scheduled-task-options`，返回可用 Runtime 和可引用 Skills

删除任务只删除未来计划，不删除历史 Agent runs、外部效果或 History 证据。API 返回任务已删除后的历史保留引用。

## 错误处理与可观察性

- Scheduler 健康与业务运行结果分开显示。
- `last_tick` 或进程存活不能替代最近成功内容时间。
- 任务列表显示最近实际结果、下一触发点和 Attention 原因。
- 每次 run 显示实际 Runtime、模型、Skill revision、计划时间、派发时间和关联任务；服务命令的
  run 显示命令可读名称，技术标识折叠显示。
- 服务命令不产生 reply_attempt，因此消息历史里不出现定时检查记录；命令失败通过 trigger 的
  `failed` 和 Attention 可见，通道级健康（例如微信 Reader）通过健康组件可见。
- Dispatcher 暴露各 Queue Adapter 的待处理数量、最老等待时间、运行中数量和最近错误。
- 空队列不创建用户可见运行记录。
- 外部读取失败保留提供者返回的可诊断分类，但不泄露凭据。
- 所有删除旧计时入口的迁移都必须验证没有重复触发和没有未认领输入。

## 测试与验收

### Scheduler 与存储

- 六段 Cron、时区、夏令时、非法表达式、下一未来触发点和可读计划描述。
- 服务命令任务：不能同时携带 Agent 字段；快照包含命令；命令不在目录中时跳过并进入 Attention；
  已完成的命令执行终态；seed 原地转换与启用规则；schema 版本升级补齐旧库的 `command` 列和快照。
- 服务停机后不补跑。
- 同一 `scheduled_for` 的原子去重。
- 上一轮未结束时记录 `skipped`，且不创建第二个业务任务。
- 手动运行不改变下一次 Cron。
- migration key 幂等。
- Runtime/Skill revision 不可用时不 fallback，并进入 Attention。
- 更新时的 `version` 冲突和删除后的历史保留。

### Dispatcher

- 服务命令在 trigger claim 内执行：成功链接 `service_command`，失败记 `failed` 并进入 Attention，
  不创建 reply task、agent run 或 reply_attempt；微信命令的 Reader 健康语义（只记一次、成功后恢复）。
- 多个 Queue Adapter 的公平领取和原子 claim。
- 长任务不阻塞队列检查。
- 同一任务不会被两个 Consumer 领取。
- 服务终止后 lease 恢复、execution generation 和幂等边界保持。
- DingTalk、WeChat、Meeting、Work Summary 和 Scheduled Agent 的正确分发。
- 空队列保持安静，不产生假运行。

### Managed Skill 与迁移

- `ceo-minutes-sync` 能作为 service-managed Skill 导入、修订和绑定精确 revision。
- 听记同步验证真实内容新鲜度，而不只验证请求成功。
- 新任务验证成功后旧听记、消息、会议、OA、每日扫描和每周 OKR 计时入口被移除。
- 重启不会重复创建种子任务。
- WeChat 迁移不扩大联系人、群聊或发送授权，不新增复盘任务。
- Codex Automations 和其他系统任务没有被误导入。

### API 与 UI

- 顶部导航、任务列表、创建/编辑、启停、手动运行、删除和历史。
- 执行类型选择：服务命令任务只显示命令和说明；seed 任务的命令不可改；`scheduled-task-options`
  返回服务命令目录。
- `$` Skill 选择与后端结构化引用一致。
- Runtime 选项随 Settings 配置变化；不可用项显示原因。
- 暗色模式正文、辅助文字、输入框、禁用状态和错误提示达到清晰可读的对比度。
- 窄窗口为单列布局，没有横向挤压和重叠操作。
- Settings 不出现 Agent Cron 或 Connector 扫描频率。

### 生产发布验收

运行完整测试后，按项目运行契约重启 `com.ceo-agent-service.main`，验证：

1. 新进程已经运行；
2. 所有种子任务只创建一次，下一次运行时间正确；
3. 手动运行 AI 听记同步能读取当前真实数据并更新内容游标；
4. DingTalk、Meeting 和 WeChat 只由新的定时任务触发，没有旧循环重复执行；钉钉和微信消息检查
   以服务命令执行，运行记录链接 `service_command`，不产生 reply task、agent run 或 reply_attempt；
5. Dispatcher 能将新输入交给正确 Consumer；
6. Consumer → Audit → feedback → revision 和投递状态保持完整；
7. 没有新增 unresolved `failed`、长期 `processing` 或未认领积压；
8. 暗色模式和窄窗口中的定时任务 UI 已在真实浏览器验证。

## 实施阶段

1. 建立 Scheduled Task 数据模型、Cron 计算和后端 API。
2. 建立内部 Dispatcher 与 Queue Adapter，先保持现有 Consumer 业务实现不变。
3. 增加顶部“定时任务”页面，并移除 Settings 中任何 Agent Cron 入口。
4. 新增 `ceo-minutes-sync` 托管 Skill 和听记种子任务，验证真实同步。
5. 逐项迁移 DingTalk、Meeting、WeChat、OA、工作来源和每周 OKR；每项验证后删除旧计时入口。
6. 完成回归测试、文档、服务重启、真实数据 readback 和浏览器验收。

## 修订记录

- 2026-09-09：增加“服务命令任务”执行形式（决定 3、5、15；服务命令小节；数据模型 `command`；
  Scheduler 与 Dispatcher 语义；迁移表；测试与验收）。钉钉消息、微信消息检查改为服务命令，
  旧的 `_run_wechat_loop` producer/consumer 角色删除，服务内只保留 `wechat-sender` 循环。
- 2026-09-09：决定 13 细化：provider 暂时不可用导致的路由暂停只体现为运行记录和路由状态，
  不按每次触发写 Attention；配置性不可用仍逐次进入 Attention。
