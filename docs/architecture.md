# CEO Agent Service Architecture

本文档默认描述当前 Consumer Agent A / Audit Agent B 运行架构；明确标注为“已批准的
生命周期政策”的段落描述后续实现必须达到的目标，不表示对应代码已经切换、部署或在生产
启用。历史方案保留在 `docs/superpowers/` 中，仅用于追溯，不代表当前目标运行方式。

## 当前任务运行机制

Consumer 与 Audit 的实际只读工具目录提供 `read_dingtalk_group_members` 和
`read_dingtalk_user_profiles`。群成员读取使用 DWS 原生有界自动分页，原样保留真人、
机器人、完整性、桶、续页和失败信息；企业资料读取只接收已核实的组织 userId，
保留原始组织资料字段，不按姓名自动补全，也不假设每份资料都含 title。群 openDingtalkId 不能当作组织
userId；身份关联必须由同一账号返回的稳定 ID 证明，空资料或 partial 仍是证据缺口。
这些读取不新增审核、发送门禁或业务来源绑定，也不改变既有外部动作权限。

本节描述实际进入 AgentOrchestrator 的运行契约。进入 AgentOrchestrator 的业务任务遵循“执行 Agent → 审核 Agent → 反馈/修正 → 再审核”的生命周期；领域任务替换输入和工具能力。WeChat task 3 保留 DecisionRunner/persistent Sender，Email 退订和确定性技术命令沿用独立系统直接路径。Consumer 可按业务 Skill 读取事实、准备材料并执行普通工具工作；系统注册的受控外部动作由 System Executor 根据持久化且审核通过的精确方案执行和回读。服务保存候选、审核、选择、执行及回执事实。

```text
pending -> processing -> done
                     -> failed
                     -> needs_human
                     -> skipped
```

- `pending`：任务已持久化，等待执行。
- `processing`：正在执行或正处于“审核 Agent 要求修改、执行 Agent 重跑”的反馈闭环中；task 在整个闭环期间都停留在这个状态，不会切换到单独的“待反馈”“待修正”状态——修改请求本身记在 `AuditAgentResult.feedback`（`AuditFeedback.rule/observation/requested_revision`），修正次数记在 `proposal_revision` 计数器上。
- `done`：任务逻辑完成且结果已持久化。
- `skipped`：判定为不可执行或不必执行，主动收口，不再重试。
- `needs_human`：完整的当前实例问题已经 Audit 审核通过，等待绑定选项选择或 requested_input。必须有 needs_human_reason 和 decision_basis；技术失败不能成为管理决策。执行选项绑定完整精确方案，停止选项写明 skipped 和原因；未经审核或旧无绑定选项不可执行。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败，并保留失败阶段和原因。

审核闭环如下（task 状态全程停留在 `processing`，直到最终收口为 `done`/`failed`/`needs_human`/`skipped`）：

```text
执行 Agent 生成 R0
  -> 审核 Agent 审核 R0
      -> 通过：System Executor 执行持久化的 R0，provider 回执与实际回读确认后完成
      -> 需要修改：审核 Agent 写入 F0，R0 保留
          -> 执行 Agent 收到 F0，生成 R1
              -> 审核 Agent 审核 R1
```

### 主页面执行入口

`/` 主页面是 Service 的 Web 入口，不是独立 Agent Runtime。主页面创建的 turn 作为
`workbench` workload 进入统一 `RoutedCodexExecution`，与后台 Agent 共用模型路由、会话、
runtime attempt、失败切换和 CLI 原生 `auto_review`。页面后端只把统一运行事件投影为时间线并
维护停止请求；不得自行构造 provider 命令、启用 approval bypass，或实现第二套命令审批。

CLI 原生 `auto_review` 是 Codex 对 `codex-auto-review` 模型的一次额外调用，只有 OpenAI 托管路由
（`codex_oauth`）提供该模型。`service_api` 路由（第三方 OpenAI 兼容 provider，如 MiniMax）上不存在该
模型，审批请求会被 provider 以 unknown model 拒绝，进而拒绝所有需审批的动作；因此 turn runner 在
`service_api` 路由上把 `approval_policy` 设为 `never` 并去掉 reviewer：沙箱保留，不再请求审批。

历史 `workbench_confirmations` 仅用于读取既有记录，不属于新 turn 的执行路径；确认与取消
接口固定拒绝执行，主页也不再显示操作按钮。

审核 Agent 只能反馈规则、观察结果和具体修改要求，不能直接改写执行 Agent 的业务正文。执行 Agent 必须基于反馈生成新 revision；原 run 不覆盖、不删除。一个任务最多允许三个内容反馈周期，基础设施失败不消耗反馈周期。反馈次数耗尽本身是自动闭环失败，不是人工决策依据；只有 Consumer 提出的完整当前实例问题经过 Audit approve，才进入 `needs_human`；分值描述证据，不作为固定路由阈值。

所有任务都禁止使用 `discard` 动作或写入 `discarded` 状态。无需动作的结果在 trace 记录 `no_action` 后进入 `done`；需要修正时由审核 Agent 写入 `audit_feedback`，执行 Agent 生成新 revision；处理失败使用 `failed`；无法自动解决使用 `needs_human`。

`okr_review` 使用上述闭环生成逐 KR 评审；管理者 OKR 周报评分范围是当前 OKR 周期起始日至报告日，本周窗口只标记新增进展和风险。每位名单成员都必须保留一个区块，但只有可用的实时目标和有效分析才产生评分；个人季度或目标确证缺失、已验证来源中的成员数据故障以及终态结果校验失败使用系统绑定成员 ID 的未评分缺口。不得以零分、上季度目标或省略成员填补缺口。共享认证、未配置来源、未分类来源故障或身份/权限证明无效仍阻断发布，不按失败人数推断共享故障；明确的成员级失败即使覆盖全名单，也只能形成如实的缺口报告。原始回执和诊断保留在本地来源材料及任务记录中，不把历史季度指标、凭据、路径或 provider 正文放入管理报告。

名单覆盖以稳定 user ID 关联评分、来源和缺口；同名不同成员仍保留独立区块及附录，现有唯一姓名附录的文档身份不变。有效评分保留原 KR、四项领导力和三项文化维度校验及计算公式。每位管理者的模型分析沿用统一应急上限、idle watchdog、任务租约及完成缓存；仍在执行的分析保持 `analysis_in_progress`，不能当作终态缺口提前发布。评分、文档及群摘要回执是不同完成级别；完整名单缺口报告已发布不等于所有成员已完成评分。周报调度的 `last_attempt_at` 只控制失败重试间隔；完整流程沿用原有单实例 run lease、续租及崩溃恢复。

OKR 评审的实时数据读取由业务 Skill 选择当前可用的 provider 能力完成。服务提供的
`app.cli read-dingteam-okr --user-id <owner-id> --period-label <period>`，该入口调用
`CEO_OKR_LIVE_SOURCE_COMMAND`（当前为 Dingteam headless source），并返回包含
`processed.objectives` 与 `processed.okrRows` 的实时载荷，是一种可用实现而非应用层命令契约。
Consumer 形成通过/不通过判断时应使用当前 OKR 数据；截图、仓库链接或重试终态不能替代实时读取，
读取失败时必须保留底层认证、浏览器启动或源端错误码。Dingteam OKR 的认证刷新与其他需要登录的浏览器任务一样，经 `app.service_browser.launch_service_chrome` 使用每日 Chrome cookie 副本启动真 Chrome；不得另起 bundled Chromium 或专用 CDP 资料目录。
多个评审或维护任务同时遇到缓存过期时，headless source 只允许一个调用刷新认证；其他调用在取得刷新锁后重新读取缓存并复用结果，不能因正常刷新耗时产生并发锁错误。
headless source 还会在调用 OKR API 前校验新捕获凭据的有效期；专用浏览器会话过期必须明确报告会话需要重新登录，不能误投影为“没有该 OKR 周期”。

完整状态和恢复说明见 [`docs/runtime-mechanism.md`](runtime-mechanism.md)。
错误码解释统一见 [`docs/error-catalog.md`](error-catalog.md)。

### Agent Cron 与统一 Dispatcher

用户可见的定时任务是 `任务描述 + Cron + Agent 能力（结构化 Skill 引用）+ Runtime`。任务描述是所有
定时任务共有的可读用途说明；Agent 任务另有执行提示词，服务命令不把描述伪装成 Agent prompt。Scheduler 只计算
当前时间之后的下一个触发点，并把一次触发保存为 `scheduled_task_run`；它不执行领域业务，
也不把 Consumer/Audit 的完成或失败复制回调度记录。若 SQLite 写入因 `BUSY`/`LOCKED` 冲突在
存储层有限重试后仍失败，Scheduler 保留该触发点，等待 1 秒再重试；该数据库错误不会被记录为
运行结果，也不会让 Service worker 退出。Dispatcher 先领取该 trigger，原子创建
唯一的 `channel=scheduled` execution source，并在 trigger 上保存
`execution_kind + execution_id`。之后 Scheduled Agent Consumer 从这条不可变输入进入标准
Consumer → Audit → feedback revision 生命周期：

```text
scheduled_tasks
  -> scheduled_task_runs (trigger fact)
  -> Dispatcher: scheduled adapter
  -> reply_tasks[channel=scheduled] (exact active input; settled source provenance)
  -> Dispatcher: scheduled_execution adapter
  -> Consumer Agent -> Audit Agent -> feedback/revision
  -> reply_attempt / agent_runs / provider facts
```

定时任务有两种执行形式，由 `scheduled_tasks.command` 区分。`command` 为空的是 Agent 任务，
走上面的完整路径。`command` 非空的是服务命令任务：它只声明服务命令目录中的一个名字（当前是
DingTalk 消息、DingTalk 近期消息恢复、微信消息、会议、OA、工作来源、受限 AI 听记权限申请和 AI 听记同步），不需要
Runtime、Skill 或工作目录。判断标准是这次执行本身有没有判断空间：确定性的发现或同步工作属于
服务命令，需要 Skill 判断的才是 Agent 任务。AI 听记同步的分页读取、归档和内容游标都由
`app/minutes_sync.py` 确定性完成，因此它也是服务命令。
“工作来源”扫描还会只读发现管理周报、项目管理部周报和部门产研周报，按文档 ID、链接、统计周期和内容 digest
建立 Task-first `work_summary` 输入；同一文档版本重复扫描保持幂等，报告内容仍由 Task Agent 提取 Project 和 Task。
scheduled adapter 领取 trigger 后，Dispatcher 在本进程内直接运行该命令；成功时把
`service_command + 命令名` 记为 trigger 的 execution link、保存命令返回的单行结果摘要并标记 `dispatched`，失败时 trigger
以 `failed` 结束并进入 Attention。服务命令任务不创建 synthetic scheduled reply task、agent run
或 reply_attempt；命令发现真实对象后，才由既有 reply、meeting 或 work-summary Consumer 处理。
运行记录 API 和定时任务页面展示已保存的结果摘要；旧运行记录的摘要为空。这样服务命令即使没有
创建 Agent Attempt，也能显示本次扫描数量和结果。页面把结果里的 `key=数字` 读成中文计数（入队、发现、失败……），原始摘要、trigger id 和执行链接收进每行的“技术详情”。连续的定时触发如果什么都没带出来——没有 Attempt，且结果计数除上下文项（邮箱数、会话剩余天数）外全为 0，或摘要为空，或因上一轮未结束而跳过——合并成一行“N 次定时检查都没有新内容”；手动运行、失败和有计数的运行始终单独成行。没有新对象时不会在消息历史里留下每分钟一条的记录。`scheduled-task-options` 为每个服务
命令附带一份从服务状态计算的只读“下游”描述（通道、consumer 执行器、角色边界常量、实际加载的
Skill、consumer 要求的 Runtime 能力和路由可用性），页面据此说明命令发现的消息会被谁处理：

```text
scheduled_tasks[command]
  -> scheduled_task_runs (trigger fact)
  -> Dispatcher: scheduled adapter runs the service command in-process
  -> trigger: dispatched + service_command link, or failed + Attention
```

OA 的钉钉系统通知和聊天催办是外部观察事件，不是第二条审批执行入口。消息生产器将
`OA审批` 系统通知以及 `[Ding]…提醒您审批` 聊天消息写入 `oa_notification_events`；前者只
留痕，后者保存原会话和原消息作为结果回传目标，二者都不创建 Agent run。OA 定时扫描读取
`processInstanceId` 的实时节点：只有当前用户恰好有一个运行中 `taskId` 时，才把无
`taskId` 事件归并到该 OA task；多个候选或无法解析时不猜测。审批 task 完成并收到 provider
回执后，服务使用独立的 `oa-reminder-result:{event_id}:{attempt_id}` 投递键通过原生引用回复
催办人。结果回复有自己的事件状态和回执，不改变 OA 审批 Attempt 的业务结论，也不会重新
启动一个聊天 Agent。

调度层不补跑停机期间错过的时间点；上一轮仍未终态时，本轮 trigger 记为 `skipped`，不会并行
创建第二个执行输入。手动运行会创建独立 trigger，但不移动正常计划。任务指定的 Runtime route 是
**首选**而不是唯一线路（Derek 2026-09-24）：执行先上首选线路，失败时走与其他 Agent 轮次相同的统一
fallback（`app/runtime_fallback.py`：满载先同线路重试，再暂停该线路、按配置顺序换下一条）；任务的
model、thinking 只作用于首选线路。managed Skill 必须绑定精确、已加载且启用的 revision。派发前按
「首选 + 其余配置线路」检查，只有这些线路都不可用时 trigger 才为 `skipped`；Runtime 未配置、缺少能力、
认证暂停或 Skill revision 不可用属于配置性不可用，每次进入 Attention；provider 暂时不可用导致
的路由暂停（过载、传输断连）只体现为 run 记录和路由暂停状态，不按每次触发写 Attention。
派发后的执行可用性失败记录在 execution source，trigger 仍只表示已经派发。

统一 Consumer Dispatcher 不建立第二套业务队列表。各 Queue Adapter 直接领取现有事实来源：
scheduled trigger、scheduled execution、普通 reply、meeting、work summary、OKR review、
DingTalk Todo outbox 和任务长期记忆写入（`task_memory_write`）。统一层只处理唤醒、公平领取、租约、全局 Agent 容量和分发；领域 Consumer
继续负责自己的生命周期和外部事实。Dispatcher 的有界等待是跨进程恢复机制，不是用户 Cron，
也没有用户可编辑的 polling/settle 设置。空队列只显示零指标，不生成 run。

启动时以稳定 migration key 幂等创建默认任务，清单以 `app/agent_cron/seeds.py::seed_scheduled_tasks`
为准（当前 14 项）：分类新邮件、处理新的钉钉消息、处理新的钉钉日历邀请、补查遗漏的钉钉消息和
日历更新、同步会议结论与管理者视角、处理已授权会话的新微信消息、处理新的钉钉 OA 审批、将会议
行动项整理到 Tasks、生成并发送每周 OKR 管理周报、准备 CEO 管理周报、发送 CEO 每日总结、同步
Chrome 登录态、申请读不到的钉钉 AI 听记，以及下载新增的钉钉 AI 听记（每天 `20:00`，`Asia/Shanghai`）。
全部以服务命令形式 seed；早先以 Agent 形式创建的同一
migration key 任务在启动时原地转换为命令形式，
保留名称、Cron 和时区，已删除的旧任务不动，其命令通过 Console API 不可修改。新安装创建的全部
默认任务都是**暂停**状态，由用户配好连接器后自行启用（Derek 2026-09-23）；seed 从不改变已有任务的
启用状态，包括从未被编辑过的 version 1 任务。默认的名称与 Cron 与负责人本机的现行任务一致。Lark
不创建默认 seed；其余已有任务的用户修改不会被 seed 覆盖。

另有两个 Agent 形式的报告任务，同样以暂停状态 seed：每周六 `12:00`（`America/Los_Angeles`，即北京周日 03:00）的“准备 CEO 管理周报”（`ceo-weekly-report`）和每天 `21:00` 的“发送 CEO 每日总结”（`ceo-daily-report`）。每日总结的必需输入由只读服务命令 `app.cli daily-report-facts --scheduled-run <触发记录 id>` 从服务库导出，窗口是上一份成功发出的日报到本次触发（会议与会后跟进、当天变化的业务 Task、业务需关注项、当天标为重要的邮件、已处理与等 Derek 处理的事项），Agent 再扫描窗口内群消息，发布同日钉钉文档，并经服务的已审核消息通道以 Derek 本人身份单聊发给「磊哥」（不用机器人）；缺来源写进覆盖说明而不追问，细节见 `docs/runtime-mechanism.md`。周报的必需输入同样由只读服务命令 `app.cli weekly-report-materials --scheduled-run <触发记录 id>` 给出（目标周一、窗口、目标与上期周会文档、本周会议清单），周报只写进目标周会文档的三处，见 `docs/runtime-mechanism.md`。

### Runtime-managed Skill 生命周期

业务行为的可配置部分使用运行时托管的不可变 Skill 修订，而不是 Settings 对项目文件或
`~/.agents/skills` 的直接写入。首次迁移只将仓库所有的 service-managed Skills 导入 SQLite；
它不扫描或修改用户、系统、插件和 operation Skills。每个保存产生一个新的 revision（内容、SHA、
父修订、来源均保留），每次启用产生一个 next-start runtime config，绑定精确 revision、启用状态、
加载顺序和用途。

```text
Settings revision/config candidate
  -> pending_restart
  -> process loads exact revision bodies at startup
  -> append-only PID/config/SHA load receipt
  -> active
     or load_failed (previous active config stays active)
```

Consumer 在 invocation 开始时接收这个 immutable snapshot；同一次调用不会重新读取 Settings。
因此 service 不用关键词路由 Skill，不解析业务材料，也不建立并行的 Skill 审计数据库。
`feedback_iteration` 是 runtime config 的系统能力，不是业务 producer feature：关闭它会阻止新的
反馈 claim，并让 UI 显示 disabled 的处理入口，但不影响反馈读取、历史和 reopen。反馈结案证据
按 decision scope 验证：Skill/config 路径需要匹配的 activation/load receipt，code 路径才需要
本地 main 祖先 commit；两类路径都需要场景、健康和零 backlog 的已回读证据。

普通会话里提出“修改分身规则、Skill 或服务行为”并不自动成为 `feedback_iteration` 队列项。
只有输入上下文明确携带 `feedback_key` / `batch_id` 时，才要求反馈处理轮次记录；普通消息直接按
适用 Skill 形成规则修改候选。找不到反馈队列记录不能作为普通消息处理失败的理由。

OA 每个审批节点只校验当前表单真实存在且明确必填的信息。后续业务阶段、其他表单或评审偏好
中的字段不能反向成为当前节点的强制条件；Audit 发现这种候选必须退回 Consumer 重新生成。

OA 定时任务冻结传入通用 `dingtalk-oa-approval` 与 Stardust 财务、立项、合同、人员、
考勤/出差、云资源六个业务大类 Skill。个人身份、工作区和其他部署参数由运行时注入 Prompt，不写入 Skill。审批 Agent 只按通用
审批 Skill 和适用的 Stardust 业务 Skill 判断；背景参考文档不是
运行时规则来源，代码与默认 Prompt 都不引用它们。Consumer 按 live `processCode` 和表单事实选择适用类别；跨类别事项组合适用 Skill。
财务 Skill 的规则卡只适用于登记的财务模板，不匹配其他类别不得单独触发升级；规则卡只提供判断标准，动作与其他类别一样按通用决策表（2026-09-24）。适用业务 Skill
必须覆盖当前事项的规则条件、例外、权限和动作映射，且内容有效，
`rule_coverage` 才能为 1.0；否则低于 1.0，当前实例的规则缺口需要完整候选和 Audit approve 后才能进入 `needs_human`，不得按分值自动批准、拒绝或升级。
申请人可以补足事实或材料；若政策判断仍无法解决，先审核并执行必要的材料请求阶段，再提交带前阶段回执的当前实例人工问题。申请人补足事实不自动覆盖规则或权限边界。个人审批偏好仍来自定时任务 Prompt。

### Business Object、Task、Agent Run 与 Reply Attempt 的关系

这三个对象分属调度、执行和展示三层，不能混为一个状态：

```text
business_object（稳定业务对象，例如一条 OA 审批节点）
  ├── reply_task_inputs（不同入口的不可变输入身份；结算后保留摘要和来源）
  └── reply_task（唯一当前队列投影）
        ├── agent_runs（多次真实 Agent 执行）
        └── reply_attempt（稳定的业务结果当前投影）
```

- `business_object` 表示外部系统中的同一件业务事项。OA 使用
  `process_instance_id + task_id`；普通消息在没有更稳定身份时才回退到
  `channel + conversation_id + message_id`。同一事项从轮询、事件或人工重试进入时，
  只更新一个 `reply_task` 当前投影，每次输入追加到 `reply_task_inputs`；活动输入保留准确正文，已结算正文按存储保留契约改为来源与摘要。
- `reply_task` 负责排队、领取、重试、`execution_generation` 和 worker 所有权。
  新输入到达正在执行的 task 时，本轮结束后重新排队同一个 task，不创建并行任务。
- Provider 若原地更新同一个 DingTalk 日程卡片而继续复用消息 ID，近期消息恢复会把
  `input_revision_key` 不同的新快照追加到 `reply_task_inputs`，同时更新同一个
  `reply_task` 的当前投影和 `input_version`。只有卡片内容变化、日程仍有效且本人仍待响应时
  才开启新的 `execution_generation`；普通已读消息仍按消息 ID 去重。
- `agent_run` 表示一次实际 Consumer 或 Audit Agent 执行。重试、服务重启接管或
  新的 generation 都会产生新的 run；run 的状态、session、revision、transcript
  范围是不可覆盖的执行事实；tool event 与原始错误按准确范围从原生记录读取，缺失时明确不可用。
- `reply_attempt` 表示同一 trigger/channel 的业务结果当前投影。重跑更新原 attempt
  的当前状态、结果和错误，复用原 ID，不创建第二个业务 attempt。

Attempt 详情页默认展示 `reply_attempt` 的 current projection，并允许在同一 attempt
下切换查看多个底层 `agent_runs`。因此“当前结果可以被修正”与“执行历史不可抹除”
可以同时成立：列表显示最新结论，详情按引用读取每一次 run 的真实原生轨迹；来源缺失时保留运行状态并明确过程不可用。

#### 为什么这是系统级边界

扫描、事件、工作通知和人工反馈可能分别携带同一业务对象的新输入。如果服务仅按
`trigger_message_id` 创建任务，同一个 OA 节点、会议或消息投递会形成多个可并行执行的 task；
每个 task 又可能独立产生反馈 revision 和外发动作。即使其中一个 run 已经完成，其他历史 task
仍可能继续运行，最终表现为重复请求材料、互相矛盾的状态说明或重复的成功通知。

因此所有业务类型共同遵守以下约束：

1. 先解析稳定的 `business_object_key`，再排队；入口输入身份和版本只追加，已结算正文改为来源摘要与引用。
2. 每个业务对象只有一个 current `reply_task` 和一个 current `reply_attempt`。
3. 重试、反馈和服务恢复只追加 `agent_run`，不得创建第二个业务 attempt。
4. 只有稳定动作身份对应的 provider 成功结果可以阻止重放；应用层不根据命令、工具、Skill、
   read-only 分类、receipt 格式或所谓“未知效果”推断业务是否完成。
5. Attention、Workers 和质量门只统计 current projection；History 展示旧 task、旧 run 和失败状态，原始过程按需从可用的原生来源读取。

失败 Reply task 的 Attention 诊断读取该任务当前执行代的最新 run，展示原始 `source_code`
（没有时使用 `code`）、来源与明确标注为「Agent 说明」的 `reported_summary`。旧代或无法
解析的诊断不替换任务自己的错误。展示这些字段不证明外部操作已执行，不改变拒绝、重试、
人工决定或任务状态。

如果同一业务对象的 History 出现多个旧 task，模式迁移可以重建 `business_object_tasks` 的当前映射，
但不得删除旧输入、run、session、tool event、provider 结果或错误事件。当前投影修正不能被解释为
“历史从未失败过”。

服务启动时，`reconcile_done_reply_tasks_with_failed_current_run` 只把当前执行代的最新 run 确为
`failed`、且没有成功外部动作或已收口错误的 `done` task 改回真实失败投影。此恢复按
`external_action_results.business_object_key`、`sent_replies.external_action_key` 和
`errors(conversation_id, message_id, kind)` 索引反查，不在写事务中为每条 task 扫描整张回执和
错误表；原有状态条件及历史保留规则不变。

旧版本曾在同一个 `reply_task` 的不同 generation 各写入一条 `reply_attempt` 投影。此类遗留行仍
保留其 `agent_run` 作为执行事实，但 History（包括 Console API）只展示该 task 的最新 Attempt；它们
不是多个独立业务事项，也不能重复计数或形成多张处理卡片。History 图表仍在每条原始 Attempt 的
发生时段保留事件，但对被隐藏的遗留行以该 task 当前的 `done`、`skipped` 或 `needs_human` 终态
投影生命周期标签，保证图表与列表不把已收口工作重新计为失败。

当一个旧 `needs_human` Attempt 的来源 `agent_run.reply_task_id` 与该业务对象的 current task
不同，且 current task 已终态时，启动收口会把旧 Attempt 标为 `skipped`，并写入“新任务已接管”的
resolution。相同规则也适用于同一 trigger 已有更晚的完成或无动作终态 Attempt。它只修正过期的当前
投影，不删除旧 run 或改变外部结果；同一 current task 上仍待选择的 `needs_human`，或后续结果为
`failed` 的 Attempt，都不满足这个条件，必须继续保留。

### 外部动作身份、顺序与发送投影

Consumer 为每个 ProposedAction 返回 `action_identity`。同一业务对象中，同一预期外部
结果在 feedback revision、服务重试和新 agent run 之间必须复用该身份；预期结果、目标或
用途改变时必须使用新身份。服务根据 `business_object_key + action_identity + operation + target`
生成 `external_action_key`，而不是用 run id 或 revision 做去重。

钉钉消息 target 在 wire 边界只使用一套服务字段：群发使用 `conversation_id`，引用回复
同时使用 `conversation_id` 与 `message_id`，单聊使用 `open_dingtalk_id` 等稳定接收人
身份。Provider 返回的 `openConversationId` 等字段只属于执行结果，不能进入 proposal target。
这项约束在 typed result 解析时校验，正文准备、System 执行和动作键生成不再各自解释别名。

Provider 成功结果按 `external_action_key` 只保存一次。后续 run 再次遇到同一动作时复用既有
结果，不再次调用 provider；新 run 仍追加自己的观察关联，因此执行历史完整。一个 proposal
中的动作严格按数组顺序执行：序号更小的动作尚未成功时，后续动作不得开始。这样审批失败时
不会继续发送“已审批”的通知。

OA 工作通知有时只有 `process_instance_id`，没有 `task_id`。服务会同时从原生字段和事件正文
中的 OA URL 提取身份；当该审批实例只有一个已知节点时，把评论事件映射到这个节点，避免
webhook、待办扫描和工作通知各自产生一个任务。同一实例存在多个节点且事件未给出节点身份时，
服务不猜测节点。

所有成功消息统一投影到 `sent_replies`。同一 `external_action_key` 只有一条消息记录，相关的
agent run 通过 `sent_reply_observers` 关联到它。History 因此既能显示真实已发送消息，也不会
把一次复用展示成第二次发送。这些是执行幂等与展示事实，不是应用层命令审核、业务证据审核
或 read-back 状态机。

消息 provider 返回稳定消息 ID，即构成已完成的发送事实；应用层不再要求额外 read-back
证据才能写入投影。投影按 `action_identity` 精确关联 proposal action，不能从动作数组中猜正文，
也不能要求发送目标会话等于源任务会话。会议跟进优先发送到 Agent 选定的业务群；该群不存在、
已解散或不可发送时，使用会议上下文中已经存在的组织者 `user_id` 或 `open_dingtalk_id` 发送
单聊。服务不得根据组织者姓名搜索或猜测身份；上下文没有稳定组织者标识时保留任务重试，不发送。
群发与组织者单聊使用同一个稳定业务投递键，因此重试或新 run 不会重复投递。服务启动后
会从 append-only Consumer/Audit typed results 修复缺失的 `external_action_results`、`sent_replies`
与 observer；该修复不重放 provider 动作，也不改写历史 run/event。

会议总结按受众拆分内容。业务会议的普通结论、安排和行动发送到 Agent 根据实时业务承接证据
选择的群；人员评价、绩效、薪酬、晋升、去留、候选人结论、健康或请假内容不得进入该群消息，
而是作为可选的独立私聊消息发送给经实时身份与职责确认的参会 HR/人员负责人，无法确认时发给
当前负责人本人。若 DWS 实时群发现确认目标是 HR 专属或已匹配的群，且敏感讨论面向会议中的 HR
共同受众而非针对具体个人，则允许敏感详情随 HR 群消息发送，并不再重复发送私聊；群受众不明确、
含非授权成员或讨论针对具体个人时仍按脱敏群消息加敏感私聊处理。群消息与敏感私聊使用不同的稳定投递键，任一发送重试都会复用已成功的另一条，
日历描述只写群消息版本。

钉钉消息在交给 Audit 前生成带服务后缀的最终候选正文。这个准备记录按
`execution_generation + proposal_revision` 区分：同一 revision 的进程重试复用同一正文，Audit
要求修改后生成的下一 revision 使用修正后的新正文。外部动作的 `action_identity` 和
`external_action_key` 仍跨 revision 保持稳定；如果 provider 成功结果已经存在，后续 revision
复用该成功结果而不再次发送。

`sent_replies` 和 provider 成功结果的优先级高于后来失败的 attempt current projection。即使
结构化结果解析、服务重启或后续 Agent turn 失败，下一轮也会收到真实的已发送记录，并把该
动作视为已经完成；不能因为 attempt 当前显示 failed 就再次发送。

### 用户反馈处理投影与重新打开

用户反馈处理使用稳定的 `pending -> processing -> resolved` 当前投影；
这组状态与普通 Agent 任务的 `pending -> processing -> done` 不是同一状态机。
`pending` 表示未领取，`processing` 表示已被一个批次原子领取，`resolved`
表示当前处理轮次已用完整回执结案。页面上的“未完成”是
`{pending, processing}` 的合集，不增加第四个存储状态。

重新打开是唯一合法的 `resolved -> pending` 转换。调用者通过现有本地
Feedback API 提交一个不为空的确切 `reason`；服务不生成、重写或补默认理由。
重新打开只清除当前处理投影，不创建新轮次，也不直接进入
`processing`。下一次原子 claim 才创建新批次和新的不可变处理轮次。
历史批次、轮次、Workbench 关联和回执仍可读且不得被覆盖；证据写入和
结案只能作用于当前 `processing` 轮次，旧轮次的证据不能满足新一轮。

Feedback API 跟随现有后端的本地访问边界，供 Workbench 和仓库 Agent
共用；它不对公网暴露，也不增加 feedback 专用鉴权。完成一个当前轮次时，
后端必须从当前轮次回执中同时确认：代码已实现，测试成功，提交存在且是
本地 `main` 的祖先，`com.ceo-agent-service.main` 已重启且 PID 实际变更，
本地健康接口返回 HTTP 200 和 `ok=true`，权威实时的 `processing` / `failed` /
`retryable` 数量都是零，并且状态和证据已经通过 API 持久化后回读一致。

该能力不在 Agent 页面增加第二套流程，也不使用模型合成重新打开理由或新回执。

### Email Agent task 映射

> **实现与部署状态：** Email folder classifier 与 audited-v2 lifecycle 已合入 `main`；本机
> launchd 的独立 Email worker 已启用，并通过真实 IMAP 可逆验证。SMTP 与自动回复仍禁用。

邮箱服务器中的当前文件夹是类别的唯一事实来源。服务维护“业务类别 → 每个账号的精确
provider 文件夹”绑定并单向创建/校验目标文件夹；分类结果本身不能覆盖文件夹事实。定期把这份文件夹事实读进训练快照的观察任务，一条邮件解析失败（IMAP 读到 `fetch_uid_batch`
Python 的 email 包无法解析的结构化邮件头）只跳过这一条、游标照常前进，不会重试同一条、也不会拖垮整批（Derek，2026-09-28 实测：Gmail 账号的观察任务因此连续几天每次都失败，导致自动重训三天没有真正判断过一次）。IMAP 只读连接的 socket 超时默认 90 秒（曾是 20 秒，同一次实测里单封邮件头就要 5-6 秒，20 秒经常不够，报的是 `TimeoutError`）；socket 超时和邮件头解析失败不同，不能在同一条连接上跳过继续读——response 会和下一条命令错位——所以超时时整条连接照常作废重连，靠放宽上限而不是"跳过"来解决。Inbox
表示尚未分类，Spam/Trash 固定为内部 `junk`，Sent/Draft 不参加训练。`important` 不是类别，
而是独立注意信号：兼容 provider 的 Starred/Important/Flagged 信号与成熟模型信号取并集，
但 junk 始终抑制 important。控制台详情页的 Star / Flag 图标是主人本人的手动点击，直接让服务连上邮箱增删 `\Flagged` / `$Important` 这一个关键字并读回确认，不经过 ActionPlan；观察到的状态随后更新，下一轮扫描再对账。分类确认只保存最终类别、训练反馈和不可变 `ActionPlan`。确定性动作清单是
`label`、`mark_read`、`archive`、`move`、`trash`、`flag_important`；它们属于 Email 子系统，由独立
Email worker 领取、执行动作，并以服务器的应答为结果（Derek, 2026-09-25：不需要回读，服务器接受就够了）。
每次 provider observation scan 在同一 SQLite 事务中发布消息状态、authoritative folder 的缺失对账和 folder generation 时间；generation 时间单独按账号/文件夹保存，读取仍返回该文件夹最近一次完整扫描时间。相同消息状态不重复写消息行或其索引，扫描成员先写入连接级临时表再用索引对账，避免在共享数据库写锁期间构造超长身份列表。这样保留整代原子可见性，同时限制 Email worker 对共享 SQLite 的写锁占用。
直接邮件动作领取先从状态索引找出含 pending/failed 动作的分类，再通过 `(classification_id, action_plan_id)` 索引读取该分类的动作组；候选探测与 `BEGIN IMMEDIATE` 内的重核都使用这条路径。状态、当前 ActionPlan、同组 processing 阻塞、账号范围、重试时间、依赖、动作优先级和条件更新保持不变，不在写锁内遍历整张 `email_actions`。
执行前仍先读一次当前状态，已满足就不写。改标记的动作（标已读、标星、贴标签）在服务器接受 STORE 后即完成；
移动、归档、删除在服务器回了 `COPYUID`（新位置已知）时同样即完成。只有服务器没说邮件去了哪里时，
才保留一次回读，用同一账号共享的连接去找新位置并确认。进程内每一个会碰 IMAP 的调用点——直接动作
投递（写连接和回读连接）、定期分类扫描、训练观察任务、OTP 读取、历史回溯（含重读候选、补建模型
动作）、Agent 任务上下文加载、退订入口解析——现在都经 `EmailAccountConnector`
（`app/email_account_connector.py`）取用，一个账号一个连接，由互斥锁串行化，按优先级排队（用户直接
等待的：分类回读、扫描、直接动作投递、OTP、任务上下文、退订入口解析，都是 HIGH；后台批量的：训练
观察、历史回溯、模型动作补建，都是 LOW）；排不到就在 600 秒后超时报错；连接闲置超过 45 秒、连续
复用超过 300 秒或探活（NOOP）失败就丢弃重连，失败的连接不复用——这三个数值不变，只是从"每个调用点
自己维护一份连接"改成了"一个账号一份，由 `EmailConnectorRegistry` 持有"（Derek，2026-09-28：
`py-spy dump` 抓到训练观察和直接动作投递两个线程同时各握着一条到同一个 Gmail 账号的活连接，账号随之
被限流，拖垮了所有排在后面的调用者）。训练观察和这些按需调用点各自的实现本身基本不改动（都仍是
"取一次、用完就关"的调用约定），只是它们的 `source_factory` 现在从连接器取用、关闭时把连接签回而
不是真登出。退订审计/直接执行走独立进程（每次任务一个 CLI 调用），各自建一个只服务这一个任务的
registry，不与主 worker 进程共享——同一账号在两个不同进程里各自最多开一条连接的边界仍然存在，本次
改动解决的是同一进程内多线程各自开连接的问题。这些确定性动作
不创建 CEO Agent task，也不创建 Consumer/Audit run。`trash` 只允许可恢复的 move-to-Trash；
永久删除、IMAP `EXPUNGE` 和清空 Trash 在所有配置与执行入口都不可达。

标准 IMAP 账号使用 `UID MOVE`。若 provider 未声明 MOVE capability，但其官方协议明确规定
`UID COPY` 本身就是移动语义，账号可显式配置 `imap_move_mode=copy_as_move`；服务不会按主机名
猜测，也不会把普通 IMAP 的 COPY 当作移动。两种模式都以服务器回的 `COPYUID` 得到新位置；
服务器没回 `COPYUID` 时，才用稳定 Message-ID 重新定位并确认目标文件夹。都不得通过 `STORE \\Deleted` 或
`EXPUNGE` 模拟移动。

只有不可变 `ActionPlan` 明确授权的 `unsubscribe` 会创建 `channel=email` 的
`pending` task；持久化生命周期标识仍为 `email_unsubscribe_audited_v2`，以兼容既有
任务输入，但它不要求 Consumer 或 Audit turn。分类确认、零动作计划
和其他 Email 动作都不会创建 task。`auto_reply`、SMTP 和 `mailto` 发送全部禁用：配置、
分类结果、人工确认和 Agent 都不能生成或发送邮件回复或退订邮件。

Email task 继续使用现有唯一键 `(channel, conversation_id, trigger_message_id)`：

```text
conversation_id
  = digest(account_id + stable_thread_identity)

trigger_message_id
  = digest(account_id + stable_message_identity
           + action_type + action_plan_version)
```

同一 ActionPlan 的重复扫描、进程重启或模型重训只会取回原任务，不改写原任务、
generation 或运行历史。新的计划版本获得新的动作身份，因此可以在保留旧 run 的同时
形成与动作类型匹配的新生命周期。

任务的 `trigger_message_json` 只保存可追溯的动作身份、账户/邮件/thread 身份、
ActionPlan、分类、模型和配置版本，以及 opaque unsubscribe entry。
它不复制邮箱凭证、附件字节、本地附件路径、邮件正文、完整私密 URL 或 query token。

模型拒判后的分类 Agent 走系统统一的 runtime 线路与回退顺序，没有专用线路（Derek，2026-09-24）。
它的提示词里带最多 8 封主人已确认标签、与当前邮件最相似的邮件（发件人、主题、正文前 250 字和主人给的类别），
让它照主人的归类习惯判断（Derek，2026-09-26；`app/email_similar_examples.py`）。相似度用字符 n-gram 的 TF-IDF，
只取主人确认过（`classification_source='user'`）、类别仍然存在、且不是当前邮件本身的行；索引按主人确认标签的“条数+最新更新时间”缓存在进程里，每次分类只读这一个戳；主人有新确认时才重读并重建（约 900 封 1.2 秒，单次查询 7 毫秒，重启后第一封多花一次建索引的时间）。
示例只是证据，不是指令，不改变“只分类、不行动”的约束。离线评测（888 封主人标注）里这样做的准确率：
零样本 65%，带示例 89%（已见过的发件人）、77%（新发件人）。示例里含其他邮件的片段，会随提示词发给所选 runtime 线路。
检索方式同期对比过 embedding，字符 n-gram TF-IDF 效果持平且不用额外起模型服务，没有换的理由（Derek，2026-09-28）。
Chrome 内置 AI（Gemini Nano）评估后放弃，不是待办：它的模型文件需要重新下载约 4GB（此前已删），只能通过页面 JS API
调用、要搭无头 Chrome 才能跑，且无头模式下能否拿到该 API 未经验证；零样本本身的准确率已经不够，Nano 大概率同样受限，
不值得为它搭这套 harness（Derek，2026-09-28）。

运行时 `AgentTaskContext` 从 Email 数据源提供当前邮件和 thread 的纯文本正文。附件是
metadata-only，没有 image/content material；只投影文件名、MIME、字节大小、数量和 inline
标记，并固定 `image_paths=()`。任何组件都不得
下载、打开、OCR、解析、总结或推断附件正文。不可变 ActionPlan 是唯一动作授权；Adapter
只排队，不发送、不打开退订页面。

Email worker 的退订任务修复先扫描分类 ID 和动作身份，只为确实缺少稳定任务的记录读取完整分类行；
修复后按该分类 ID 定点复核，不重扫所有分类。

退订是低风险的直接 Email worker 动作；退订不做结构化审核，也不做 Consumer 或 Audit turn。
worker 以 task id 从
durable 状态读取 ActionPlan 已授权的 entry，一次调用完成整件事——打开该 entry，按页面当场呈现的
精确链接、表单或独立退订按钮操作，直到第一个终态页面，然后保存 outcome 和脱敏后的页面原文。
兼容调用语义仍称为 `unsubscribe_email(task_id)`，但它由 worker 直接执行；没有 proposal 要抄写、没有
acceptance 要绑定、没有 Audit turn，也没有 continuation 要续。

幂等性只靠 receipt：`email_unsubscribe_receipts` 里每个动作身份一条，已有 receipt 时再调一次只会
把它原样返回，不会重复退订。这取代了原先的 claim 租约、effect digest 链、owner fence 和
Consumer→Audit 往返——退订在真实世界本来就是幂等的，那套 exactly-once 支架换不来任何东西，
却让每一次点击都要花一整轮 agent，而过期的 continuation 会让任务失败在机制上而不是页面上。
同理，有浏览器步骤但没有 receipt 不再升级为 needs_human：重跑一次即可。

页面要求登录或 CAPTCHA 时记 `skipped_login_required` / `skipped_captcha`。
页面说的是什么由 Agent 判断，不再靠关键词（Derek，2026-09-25）：服务照旧负责机械步骤（打开链接、等页面文字连续 1 秒不再变化、操作控件、抓取文字），然后把主机名（不含带令牌的链接）、页面文字（最多 6000 字）和可操作控件的种类交给一次 Agent 调用，它返回一个状态（已退订、之前就退订过、要登录、要验证码、要付费、链接过期、有可操作项、看不出来）和决定它的页面原话。原话写在回执结果文字的第一行“判断依据：……”，后面是它读到的整页文字。链接过期和看不出来都不算页面本身的结论，走下面“页面读到了但这个服务不操作它提供的控件”那条路。Agent 线路不可用或返回的不是约定的对象时，这一次读取按浏览器故障失败（记录里的类别是 `page_judge_unavailable`），任务先延后重试一次，仍失败就记 `failed`，可以手动重跑，不猜结果。这是因为站点导航里的“Sign in”和一个要登录的页面无法靠关键词区分，Substack 这类站点还会在静态外壳之后用脚本弹出“You've been unsubscribed”的提示，只读外壳会把已经成功的退订记成需要登录（2026-09-25）。审计式（audited）旧流程没有 Agent，仍按关键词读页面；工作负载种类是 `email_unsubscribe_page`。
页面读到了但这个服务
不操作它提供的控件时记 `skipped_no_reliable_entry`，并保留页面原文。两种跳过（没有可操作项、需要登录）都只是“当时读到的页面”，不是对链接的定论：手动重跑会重新读页面，读法相同时保留原回执，读法不同（比如需要登录变成已退订）时用新回执替换它。

任务已关闭（done / skipped / needs_human）却没有 receipt 时，启动与训练维护里的对账会补一条 `skipped_no_reliable_entry`（证据 `durable_context_entry_unavailable`），让后续动作不被卡住。`failed` 的任务不在此列：它结束于技术故障，不是对链接的结论。2026-09-25 一个退订任务因数据库校验错误失败，对账把它写成“找不到退订入口”，而同一条链接其实一直在邮件里；失败的任务保持 `failed`，由人重跑。

receipt 另外保存 `entry_url`，即这次实际打开的完整私密 URL。`entry_reference` 只是该 URL 的 sha256，邮件 HTML 正文也不落库，所以在此之前 `skipped_no_reliable_entry` 这类结论只能指出 host，无法被人工复现。写入前校验 sha256 与 `entry_reference` 一致；该值是可直接触发对外副作用的链接，只在 receipt 表和 Attempt 详情页出现，不进入 `trigger_message_json`、步骤日志或错误码。

分类器按阶段运行。每个邮箱分别保存 Agent 与模型回溯窗口，默认 30 天和 365 天。尚无上线模型时，
定时扫描只把 Agent 窗口内符合该账户“仅未读/全部”设置的 Inbox/未绑定来源邮件放入 Agent 队列。
模型上线后，定时扫描改为只由模型优先接管模型窗口内全部尚无稳定记录的已读和未读邮件，不再按
日期保留近期邮件给 Agent，也不在同一轮运行 Agent 扫描。模型高置信度结果沿现有不可变 ActionPlan 和
provider action 队列整理邮件；低置信度、`others` 或未晋升类别不由模型决定，进入 Agent 分类队列，
Agent 明确判定的照常处理，Agent 自己也不确定的才存为 `pending_feedback`，进入 Console“待确认”
并等待人工标注（Derek, 2026-09-25：先回退 Agent，Agent 不确定再问人）。Agent 只接账户允许它接的邮件
（未读，或账户设为“全部”）；模型拒绝的已读邮件 Agent 会跳过且不留分类记录，所以它们直接存为
`pending_feedback`，不入 Agent 队列。模型/Embedding 技术失败则
保留邮件下轮重试，不伪装成待确认，也不当作模型拒绝交给 Agent。历史批处理允许 120 秒的 Embedding 请求期限；实时调用仍保持
2 秒期限，历史回填不会因为实时低延迟预算而永久卡在同一封邮件。分类过程不擅自改变邮件原有已读状态。训练只在冻结的
provider-folder snapshot 上离线、分阶段执行，shadow 模型不进入实时扫描。晋升只看最新一个候选自己携带的证据：
按类别逐个判断（接受准确率、验证样本数达到界面配置的门槛），达标的类别由模型自己决定，其余仍由 Agent 决定；
不再要求连续两个候选，也不再要求标签水位前进。已上线的模型只对照它自己的证据（仍达标、兼容性一致、artifact
哈希一致）保持生效，之后训练的任何新候选，无论达标与否，都不会让它下线；候选目录里有无法读取的文件仍 fail-closed。
旧的显式实时调用接口仍保留顺序 fallback 契约，定时收信与历史回填统一使用上述模式互斥路由；
模型上线后，模型不确定的结果同样先交给 Agent。

历史和实时移动都先读取 provider 当前状态，写入后以服务器应答给出的新 locator 为准（没给才回读）；
important flag 在移动后的 locator 上执行。用户随后在邮箱中移动邮件时，下一份冻结 snapshot 直接采用新文件夹标签。
`junk` 的退订候选由代码从标准 header/body 链接中发现，独立的系统直接退订流程执行
后续网页步骤；成功或无需继续后再移动到系统 Trash。连接邮箱的邮件 OTP 只在站点、收件人和有限
时间窗同时匹配时临时读取；普通 CAPTCHA 可在隔离 profile 中尝试，密码/MFA/CAPTCHA 无法完成时
保存有界 continuation 并转人工接管，不持久化 OTP、cookie、完整 URL 或浏览器秘密。

邮件 Worker、Web 邮件路由、定时邮件发现和每日报告读取在初始化时校验 `EmailStore` 的 schema 结构，不扫描全部历史持久行，
避免多 GB 邮件库让启动或首个详情请求长时间阻塞；显式/离线 `EmailStore` 调用默认仍校验完整持久行。
初始化失败仍由既有邮件可用性边界隔离并如实报告，不阻止其他 Console 路由启动；显式/离线完整校验只读取
用于持久状态核对的列，不把 `email_classifications.model_text` 正文或分类任务完整输入载入内存。邮件 Attempt 详情每次通过新的数据库连接
读取当前分类和退订回执，不缓存详情或记录内容，也不重复扫描整库持久化状态。

Email Console 的 learning、model-version、folder-binding 和 classification-detail API 只投影版本、
门槛、计数、时延、fallback、动作/readback 与 continuation 状态。它们不返回正文、附件字节、
embedding、OTP、完整退订 URL 或私密浏览器数据。分类确认不会创建通用 CEO task；系统在任何阶段
都不发送、回复或草拟 Email。常驻 classifier runtime 将有界的阶段耗时、cache-hit、结果和受控
fallback code 写入 `EmailStore`；Web 进程只读取这些跨进程聚合，不依赖注入 worker 的内存对象，
也不保存请求文本、向量或原始错误理由。每次 provider 扫描还会更新独立的“最新观察”投影；
classification detail 从该投影读取当前文件夹事实，不从冻结训练 snapshot 推断，也不在 API 请求中
访问邮箱网络。训练相关字段明确使用 snapshot 命名，避免把历史冻结状态误称为当前状态。

Email Console 的“模型训练”页读取后端统一计算的晋升资格。默认门槛为 Micro F1 ≥ 0.95、
每个启用类别 Precision ≥ 0.95、逐类独立测试 support ≥ 20、常驻端到端 P95 ≤ 500ms。
门槛通过带 expected-current version 的接口追加新版本；修改门槛不激活模型。
现有双候选、important 和完整性条件继续适用，并比较候选与当前描述集和类别集合。
缺失指标显示未测量，历史 classifier-head latency 不充当端到端测量。

用户打开主模型开关后，runtime 在 Registry 锁与配置写锁内重读当前事实、重验候选，并原子
替换 online-active.json。该文件同时持久化运行模式、模型身份和模式切换历史，是这次切换的
提交事实源；进程中断不会留下没有切换身份的已激活模型。关闭开关保存 Agent 主分类及历史，
不删除任何模型。worker 在 tick 时读取新事实，不需要为开关本身重启进程。
描述编辑把当前配置和不可变版本快照在同一 SQLite 事务中保存，过期编辑返回冲突。

### Repository Upgrade

服务周期性读取配置的 `origin/main`，只识别可安全 fast-forward 的更新；分叉、状态指纹变化或
脏工作树不会被静默覆盖。History 页面只展示状态并启动带 operation ID 的 detached updater；
updater 在共享 Git 锁内重新校验指纹，必要时按用户确认的分支名和提交信息保存本地改动，创建
SQLite 在线备份并只保留一个最新的已验证日备份；每小时备份循环在确认当天副本完整后，也清理数据库目录中超过 24 小时的 `auto-reply.sqlite3.pre-*` 快照及 SQLite sidecar。备份不用临时文件，每次正式备份前先删掉备份目录里的旧副本，直接写成最终文件名，通过完整性检查后才盖上完成标记（`application_id`），失败或被打断的半成品当场或下次备份时删除，不算「今天已备份」；文件夹级别的锁让两个备份互不删除对方；此前每次备份被重启打断都会留下 2.9 GB 的隐藏临时文件，一天堆到 30 GB 写满磁盘。随后执行依赖同步和测试，重启
launchd 后验证新 PID、HTTP 健康与 Store 可读性。
等待静默期间，updater 不向共享 SQLite 写部署进度，避免与仍在运行的业务写事务争锁；服务静默并停止后，才用不触发 schema 初始化的窄状态存储记录后续阶段。该短写仍对意外的并发 SQLite 锁做有限次数重试。
升级后验证失败时只对本次安装的精确 commit 做 compare-and-swap 回滚；无法证明仓库仍归本次
操作所有时进入 `needs_manual`，不执行破坏性 Git 操作。MCP 配置不由该流程探测、禁用或覆盖，
直接沿用用户当前 Codex 配置。

**生产检出与部署**（Derek 2026-09-25）：服务不从开发树运行。launchd 运行独立检出
`~/Services/ceo-agent-service`（安装脚本把 `CEO_SERVICE_ROOT` 写成它所在的检出；代码里是
`app.config.service_root()`，模板和代码都不写机器路径）。这个检出没人编辑，只会前进到已推送的
`origin/main` 提交，所以上线的永远是完整提交，任何会话都可以部署：`python -m app.deploy`
复用上面的 updater，先等没有进行中的 Agent 回合和已领取的条目（30 分钟内不空闲就什么都不改），
再备份数据库、fast-forward、控制台不是从检出当前的 `frontend/` 构建的时重建（比较构建戳 `app/static/workbench/.built-from` 与 `HEAD:frontend` 的树，而不是看本次部署的差异——中途停下的部署或别的会话插进来的部署会让下一次差异里没有前端改动，控制台就停在旧版本）、检查 import、重启，并轮询健康最多
15 分钟（重启要重读数 GB 的数据库，负载高时曾用 8 分钟，只探一次会把正常升级误判回滚）。
静默检查将会议和回复任务处于 `processing` 视为在途工作，仅当它们有有效的对应 dispatcher 租约，
或分别存在运行中的 meeting alignment / Agent 运行记录时阻止部署；没有这些 owner 证据的孤儿行
由服务启动时现有的恢复逻辑重新排队。仍锁定的会议投递继续阻止部署。静默轮询每次只读计数后
关闭数据库连接，避免长等待累积 SQLite 句柄。
紧急部署可显式使用 `python -m app.deploy --skip-quiet-wait` 跳过在途工作等待；部署仍通过
updater 正常停止服务后才备份和切换检出，重启时由既有任务恢复逻辑接管中断工作。若切换在新版本
安装前失败，updater 会重新启动旧版本并检查健康；设置重启也会在 launchd 任务已卸载时重新 bootstrap。
两个会话同时部署由仓库锁串行，后到的发现检出已前进就停止。只改了设置、没有提交要部署时（有些设置，比如邮箱账号，是 worker 启动时才读），用 `python -m app.deploy --restart`：同样先等没有进行中的工作，再经 launchd 重启并等健康；确需立即重启时可加 `--skip-quiet-wait`，不手动 `launchctl kickstart`。生产检出里不能提交也不能跑测试（Derek 2026-09-25，此前有会话在那里跑测试并就地提交，检出与 main 分叉，之后所有部署都停下）：部署时装上 `pre-commit` / `pre-merge-commit` / `pre-rebase` 钩子，一律拒绝并提示去开发树改；`tests/conftest.py` 发现自己在生产检出里就退出。部署只做 fast-forward，不触发这些钩子。检出若已分叉，部署停下并列出只在生产里的提交，不会自动丢弃。`app/`、`frontend/src/`、`tests/` 在两次部署之间还是 chmod 只读（Derek 2026-09-28：上面三层防的是"改动悄悄上线"，这层防的是"改动被写下来"本身）；部署把这几棵源码树的解锁窗口精确框在 checkout+构建+校验期间，`finally` 里无论成功、回滚还是异常都重新上锁。`data/`、`.env` 和这三棵树以外的构建产物（`app/static/workbench`、`frontend/dist`、`frontend/node_modules`）保持可写——服务运行时和构建步骤本来就要写它们；`app/static/workbench` 虽然物理上在 `app/` 里，但只有构建步骤会碰它，而构建步骤总是在解锁窗口内跑。

仓库 mutex 覆盖源码解锁、停机、备份、快进、构建、校验、启动和重新锁定；settings-only restart 也使用同一把 mutex。若中断只留下未暂存文档，且每个文件都与刚 fetch 的目标提交逐字节相同，正式 deploy 会先停服务并备份，再把这些文件恢复到当前 HEAD，由正常 fast-forward 安装目标版本；任何暂存改动、其他路径或内容不完全一致的工作树仍按本地修改保护规则拒绝部署。

### 会议投递目标

日历仅用于确认参会名单，不决定业务内容的披露受众。会议跟进使用同一 `MESSAGE_AUDIENCE_CONTRACT`：根据实际内容、近期讨论、完整当前成员、
稳定身份、当前职位职责和具体披露范围选择群或明确私聊受众，不因业务内容或参会人数强制发群。
相同适当受众合并一条；不同受众才拆分。明确业务私聊的稳定 `user_id` 必须唯一匹配来源参会人
或日历证明的组织者，身份和受众理由保留在决策中；不可从逐字稿发言人推断组织者。
群不可发送时保留失败，不能静默换成组织者或本人。私聊入口拒绝本人及已核验别名；
职务、HR 身份和私聊本身均不构成披露授权，身份检查也不替模型证明业务理由真实。
群标题相似、部分参会人重合或近期活跃本身不构成业务承接证据。
找群先核对会议材料里明确提及的讨论群，再从结论、行动和负责人提炼主题，分别以原文中文
业务词、英文术语、会议标题和核心议题查群及群内消息。预置候选或首次搜索零命中不等于穷尽；
候选须核对近期同工作线讨论、行动承接人、受众和可发送性，并在决策中说明来源与业务承接关系。
搜索结果先按会议标题/摘要与群名筛选，再用完整日历名册的参会人覆盖率缩小候选；群成员读取不再依赖会议是否有完整日历名册。候选保留真实群成员 IDs 和人数，会议参会人覆盖率单独记录，不把会议人数当群人数。平台中立的发现服务只返回候选及证据，不决定最终目标或发送；近期消息截取和历史送达只是发现线索，Agent 仍需核验完整相关讨论与当前受众，不得按搜索顺序截断候选或仅凭词项匹配判断披露范围。

DWS 明确的保密群历史拒绝由适配器转换为候选级 `GroupHistoryReadDenied`，保留该群 ID、成员证据、代码及原因，讨论内容/分数保持未知，并继续核验其他候选。全部拒绝仍为明确 `history_read_denied`，不变成无群、空历史、可发送或自动私聊依据。拒绝证据到达 Agent 不解除权限；无法核实受众或内容时仍保留真实失败。临时及未知 provider 故障保持原失败/重试语义。
多个合理群不是人工选择条件，由 Agent 按本次议题和行动归属排序，不能用宽泛群凑数，也不能把敏感内容送给无授权受众。

个人、非业务内容仍要求完整日历两人名册才能私聊另一位参会人；逐字稿不能证明两人会议。
业务私聊不是个人分类或组织者 fallback，必须有明确内容目的、实际职责与非本人稳定受众。
投递前重复核验相同身份，先检查两个接收对象再发任何一条；同一私聊受众的正文合并一次发送，
不凭 HR 职务吸收本应给别人的内容。恢复已有准备或主消息回执时，先精确核对持久化正文：旧拆分正文继续使用原敏感动作身份，不因收件人相同声称已合并完成，也不改写主消息或重复发送。正文不匹配或合并正文与已准备敏感动作冲突时失败。技术或目标校验失败不得伪装成人工决策问题。
会议没有实质分歧时，仍发送简短的已确认事项和下一步；只有已确认删除的听记因原始内容
永久不可读取而记录为 `no_action`，其他听记读取、群发现或投递问题仍必须可重试或明确失败。

正式分析恢复沿用 `rerun_meeting_alignment_jobs` 和原会议业务身份；事务内拒绝部分效果、
已准备/已有回执的主消息或敏感消息、活跃 dispatcher/投递归属及仍活跃的 meeting/runtime run。
旧运行保持历史，只在确认未发送且无活跃归属后清空原决策重做实际来源与受众核验。
独立 Meeting Alignment planner/delivery 仍是独立路径；共享受众规则不代表迁入通用 Consumer/Audit 引擎。

`audience_scope` 是当前 Meeting Alignment Agent 输出的必填字段。投递器读取早于该字段的
持久化 `send` 决策时，只能从其已保存的 `target.kind` 规范化一次：`group` 对应 `business`，
`direct` 对应 `personal`，并立即写回规范 JSON；缺少目标或其他字段不合规的记录仍按失败处理。
该规范化不重新分析会议、不改变目标，也不重发已有送达记录。

## History 语义与无效入口边界

Status 的 Attempt 队列统计先按触发对象选出最新 Attempt 标识，再读取当前状态和失败详情，排序不携带历史错误正文。

History 分页先在同一数据库快照中筛选、计数和排序记录标识，再只读取本页记录的展示正文；总数与内容保持同一时点，正文读取量随页大小变化。

History 是任务和执行记录的单一展示入口。同一个任务不得被拆成多条当前队列记录；
队列任务与执行记录都必须保留各自真实状态。筛选条件的业务含义固定如下：

- `status` 只筛选执行状态；不再使用含糊的 `type` 名称。`status` 和 `object_type` 都可用逗号
  传多个值（Derek 2026-09-25：两个筛选都是可多选的复选框菜单，状态显示中文名，页面标签为
  「状态」「任务类型」，原来的快捷状态按钮已去掉）；`done` 同时包括 `sent`。
- `object_type` 只筛选任务类型。类型只有一份定义：服务端注册表
  `app/history_types.py`（值、中文名、顺序）。History 联合查询写出的 `history_type`
  取自它，页面从 `GET /api/console/history/types` 读取筛选项，行上的类型徽标用同一个
  中文名；读不到类型表时页面只提供「全部」。不在注册表里的值（包括已退役的
  `replay`）不做筛选，按「全部」返回，页面筛选框也显示「全部」（Derek 2026-09-25）。
- 类型按顺序为：队列 `queue`、钉钉消息 `dingtalk`、日历邀请 `calendar`、OA 审批
  `approval`、邮件 `email`、邮件退订 `email_unsubscribe`、邮件动作 `email_action`、
  微信 `wechat`、会议跟进 `meeting`、Task `task`、定时任务 `scheduled_agent`、
  定时命令 `scheduled_command`。同一执行记录只能归入一个类型。`reply_attempts`
  依次判定：OA（`action='oa_approval'` 或带审批实例）→ 微信渠道 → 邮件退订 → 其余邮件
  渠道 → `channel='scheduled'` 的 Agent 形式定时任务（每日总结、周报等）→ 日历邀请 →
  钉钉消息。日历邀请用的是邀请生产者自己的判定
  （`DingTalkAutoReplyWorker._is_calendar_message`：`[日程]` 开头，或内容含
  `newCalendar=1` / `calendarDetail` / `uniqueId=`，含 URL 编码形式），另加 attempt 上的
  `calendar_response` / `calendar_reconciliation` 动作或 `calendar_event_id`；测试把两处
  判定绑在一起。OKR 评审 attempt（`action='okr_review'`）归入钉钉消息。
- History 的来源表是 `HISTORY_SOURCE_TABLES`：`reply_attempts`、会议、Task 各表，
  以及两类新来源。Audit web 启动不预热大范围 History 查询；打开尝试详情时批量读取该任务代次的
  Agent run 及工具事件，图表只投影所选时间范围内 attempt 的当前 History 状态。
  页面缓存以来源表元组为键。
  `errors` 不是 History 来源，其分支的 `service_error` 类型只出现在旧 `/logs` 页。
- 定时命令（`scheduled_task_runs`）：失败的触发一律显示；同一定时任务之后有一次成功
  运行的，失败显示为 `recovered`（服务在那次成功时解决它的 Attention 条目），否则为
  `failed`。成功的运行只显示结果不会出现在别处的命令（`consumer_prompt_enabled=False`：
  同步 Chrome 登录态、听记同步、听记权限申请、OKR 周报），显示为 `done`，结果行是命令的一行摘要；把结果
  交给 Agent 的生产命令（读取新钉钉消息、日历邀请、邮件等，每天数千次且几乎都是
  「没找到」）成功时不显示，它们排入的每一项自己就是一条 History。Agent 形式任务因
  运行时或 Skill 不可用而跳过的触发显示为 `skipped`；上一次还在运行而跳过
  （`scheduled_task_previous_execution_active`）是调度的防重叠记录，不显示。Agent
  形式任务的失败触发归入「定时任务」，其余归入「定时命令」。详情链接到
  `/scheduled-tasks?id=<任务>`（没有单次运行的页面）。
- 邮件动作（EmailStore 在同一 SQLite 文件里的 `email_actions`）：每个动作一行，标题是
  邮件主题，来源是发件人，结果是动作（移动到「文件夹」、归档、移到垃圾箱、标为重要、
  标为已读、打标签）及错误。状态沿用 Attention 和状态页的口径：只有当前计划、已处理
  邮件上用尽重试的失败才是 `failed`；还在重试窗口里的失败是 `pending`；被新计划取代
  或邮件不再处理时，未完成的动作是 `skipped`。所以 History 的失败数不会多出 Attention
  没有的邮件动作；定时命令的失败与 Attention 的 `scheduled-task:<id>` 条目同样一一对应，
  唯一的差别是依赖短暂不可达时，服务要等故障持续 15 分钟才写 Attention，这段时间里
  最近一次失败只在 History 里。详情链接到 `/email?tab=all&selected=<邮件>`。
- 这两类来源与 `reply_attempts` 一样按 `occurred_at` 排序，时间统一成
  `YYYY-MM-DD HH:MM:SS`（UTC）。History 图表仍只统计回复 attempt、会议和 Task，不含
  定时命令和邮件动作。
- `reply_tasks` 的 `pending` 和 `processing` 是当前队列任务，必须在 History 中按真实
  状态展示、筛选和计数；它们不属于 Attention。还没有 Attempt，所以不能像已完成记录
  那样链到执行详情：`channel='scheduled'` 的队列行链到它所属定时任务的运行记录
  `/scheduled-tasks?id=<任务>`（映射来自 `scheduled_task_runs` 里 `execution_kind='reply_task'`
  且 `execution_id` 等于该 reply task id 的那一行，`AutoReplyStore.scheduled_task_ids_for_reply_tasks`），
  其余渠道链到 `/workers`（Derek 2026-09-27：点开原来一律落到 Status 页，看起来像详情坏了）。
- History 在页面可见时每十秒读取当前快照；已经进入终态的队列任务不得因页面保持打开而继续
  显示为 `processing`。
- 微信候选任务的 `done` 不证明消息已投递。同一任务、会话和当前执行代的最新投递为
  `failed` 或 `send_unknown` 时，失败 Attempt 在 History 筛选、详情、状态计数和图表中仍为
  `failed`；旧代、其他会话或已被后续成功投递取代的失败不覆盖当前任务状态。读取投影不改写
  Attempt、任务或投递台账，也不触发重新发送。
- 24 小时图表统计的是该小时内发生的历史事件，不是当前队列的状态计数。重试和执行开始须以
  独立事件标签呈现，不能使用 `processing` 改写旧失败记录；实时 `processing` 只由当前队列
  列表和状态页统计。
- `okr_review_requests` 的队列状态不能覆盖对应执行记录的 History 状态。若同一对象已有
  当前队列记录，列表以这条队列记录承载 `pending` 或 `processing`，不再制造重复的当前状态行。

History 不承诺旧查询参数或旧 URL 的兼容别名；接口和页面使用当前语义，历史数据只
通过当前代码的分类规则重新解释。旧值不会报错，只是不再筛选。

“待处理服务修复”不是运行时能力：它没有生产者、处理动作、修复执行器或闭环，不能
作为服务健康状态或执行队列的一部分。移除该死入口时，范围包括导航、History 卡片、
页面和路由，以及仅服务于该入口的模型、存储 API、初始化表和索引；`feedback_events`
和真正的反馈流程必须保留。删除已有 `service_bugfix_candidates` 表属于独立的数据库
迁移，必须先做并校验 SQLite 在线备份，再小批量迁移、读回表已删除且反馈数据未变化；
迁移必须幂等，不能通过旧路由别名或重新建表恢复该入口。

## 设计目标

CEO Agent Service 是本地优先的企业消息处理服务。它发现需要 Derek 处理的消息、
审批和任务，由两个 Agent 准备及审核候选，再由系统执行：

- **Consumer Agent A** 理解业务、读取当前事实并提出精确候选；它使用同一原生
  runtime，按角色限定可用能力，不发布候选中的消息、审批等受控外部动作。
- **Audit Agent B** 只读独立审阅整份候选，包括拟提交给 Derek 的问题；不发布外部动作。
- **System Executor** 执行审核通过的持久化精确方案，并保存 provider 回执与回读；人工选项执行绑定的原方案，不由模型重写。
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
不同会话可以在同一个 launchd 服务内并行执行。Consumer 与 Audit 的重试都优先继续各自已有的
原 Agent session；feedback、Skill 更新或 revision 只推进业务决策/Skill 版本，不会自动清空
session。每次 turn 仍记录当前契约哈希作为回执，但哈希变化不是会话身份边界。只有 runtime 明确
确认原 session 不存在、不可访问或认证上下文失效时，才建立新的 session，并保留旧 session lineage。
服务不再用全局进程锁串行化所有 Codex 调用，否则一个长会话会让无关会话已认领却无法运行。

跨 worker、审计页面和服务重启的竞争由 SQLite 会话锁、Agent run lease 和结果回读处理；
同一会话顺序不依赖共享的进程级锁。

服务重启时，未完成的 Agent turn 按普通失败重试；已完成 Agent 回合会从持久化结果继续。普通重启保留同一任务的 execution generation、session 和外部回执；明确完成运行时、路由或本地环境修复后，服务修复重试可以创建新的 execution generation，但仍优先续用可访问的 session。两者都不创建独立的 unknown 或状态核对状态机，也不根据工具事件替 Agent 判断外部动作结果。下一次 Agent turn 按当前业务 Skill 读取外部状态，再决定是否继续。

Task Agent 的正常 liveness 由结构化 provider 事件驱动：每个有效 JSONL 事件都会续租 Agent run 并作为进展证据；连续 5 分钟没有事件时，idle watchdog 才会中断该 turn。`CEO_TASK_CODEX_TIMEOUT_SECONDS`（生产为 7200 秒）只是防止泄漏进程长期占用资源的紧急上限，不是长任务的业务完成时限；只要有进展，任务可以在同一 session 上持续或恢复多个 turn。

### Schema 初始化竞争

所有使用同一 SQLite 文件的 `AutoReplyStore` 和 `EmailStore` schema 初始化入口，都先取得按数据库路径共享的
跨进程文件锁，再检查 schema 版本、必要表、必要列和定时任务运行快照字段（包括
`scheduled_tasks.command`、`command` 快照字段）。检查遇到短暂
`locked` 或 `busy` 时会在该锁内等待后复查；只有稳定确认 schema 过期、缺表、缺列或快照需要回填才
执行迁移。这样多个启动进程不会并发执行不同 Store 的 schema 工作，也不会把高负载写入误判为 schema 缺失，或让审计页面在请求期间执行 DDL；即使旧库
的版本号已经提前写成当前版本，也会先完成定时任务 schema 的补齐再启动调度。默认离线
`EmailStore` 的完整持久行校验在释放 schema 锁后执行，不会占用初始化锁扫描历史邮件行。

### Workbench 启动恢复竞争

审计 Web 启动时会执行 Workbench 恢复。若恰好与 worker 的 SQLite 写事务重叠，恢复会对短暂
`locked` 或 `busy` 进行有限次数重试；非锁异常或超过上限的锁仍按服务错误处理。这样一次并发
写入不会使审计页直接启动失败，也不会吞掉持续的数据库异常。

### 外部动作结果与重试

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

candidate_executions 保存租约，candidate_action_attempts 在 provider 调用之前保存 dispatch 边界；已验证回执复用。进程中断或超时没有回执不能证明未发生效果：先读取原外部对象。确认成功继续，确认没有效果才可重试同一计划，无法消除的歧义保存 uncertain/failed，不伪造完成或人工问题。技术恢复不重新审核不变业务方案；业务事实改变则使候选失效，重新形成并审核方案。没有通用 shell 或 Agent 执行兜底；未支持的动作明确失败。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

当前 Attempt 的结构化运行结果 `code` 或 `source_code` 为 `provider_risk_rejected` 时，历史“重新处理”入口不可用，直接提交该入口也返回冲突且不入队；API、React 和原生 HTML 优先展示该原因。确需新的候选或执行范围时，应通过明确的本次任务和完整候选提交处理，不能用旧入口重放历史候选。对于后端允许重新处理的其他失败，`rerun_label` / `rerun_confirmation` 仍由同一业务对象的结构化历史提供；跨执行代的历史拒绝可使措辞显示“重新评估候选”，普通技术失败保留“重新处理”。只认确切的顶层结构化错误，不匹配正文、嵌套文字或其他对象；System 的既有历史拒绝限制和原始记录继续保留。

需关注中的新审核问题显示为“本次事项选择”，说明需要选择本次事项的处理方案，判断依据保留已审核候选的具体人工原因。旧 `task_class` 问题的历史状态与选项保留，但不出现在当前可执行选择中；隐藏旧选项不表示已重新审核或业务完成。

正式 Consumer/Audit 契约发布使用 `python -m app.deploy --publish-consumer-system-contracts`，在服务空闲且停止、数据库备份已完成后，先校验并将恢复快照交给 updater，再开始替换九个契约文件、六个 managed Skill 修订和受影响任务的完整引用。发布中途失败也由 updater 持有该快照恢复；恢复失败时保留回执及原文件副本，记录 `needs_manual`，不回滚 Git 后启动可能与外部文件或引用不一致的旧服务。恢复成功后才启动旧版本；健康通过后仍核对 active 配置、全部启用绑定与新 worker 的加载回执/PID、九文件 SHA 和完整任务引用，最后标记 verified。代码上线、资产发布和实际业务结果分别验收。

正式关闭的微信投递若在关闭原因中记录 `superseded_by_principal_reply:<source-message-id>`，表示本人后续回复已覆盖旧候选。仅在匹配当前任务、执行代和会话的投递上，详情状态保留 `skipped`，不被任务的 `done` 覆盖；页面说明已由本人回复、旧候选未发送且无需重试，不显示旧投递重试动作。手动重试的同一写事务读取该正式来源引用并拒绝重新入队，原投递、Attempt、执行代与回执不变。该判定读取分号分隔的精确关闭字段及非空来源引用，不匹配回复正文或一般文字。其他普通过期投递的现有手动重试合同保持。

## Skill-first 权威处理流

```text
trigger/context/materials
  -> Consumer business work and complete persisted candidate
  -> Audit read-only whole-candidate review
     -> return/reject: Consumer complete revision
     -> approve action plan: system execution and verified receipt
     -> approve question: visible needs_human
        -> exact stored option selection: system execution and verified receipt
```

Producer 只根据消息来源、会话类型、明确的 @、稳定卡片类型和去重标识决定是否创建任务。
系统**没有关键词业务路由器**：service 不通过项目名、人员名、百分比或业务词判断该加载哪个
Skill。Consumer A 根据完整上下文使用 Codex 原生 Skill discovery，按需读取业务 Skill 和操作
Skill。

每个 Consumer 回合的 developer instructions 都携带八个已安装业务 Skill 的精确名称和路径，并把
至少一次业务 Skill 读取定义为返回任何业务结论之前的协议前置条件。目录只声明可用能力，不替 Agent
选择领域；选择仍由 A 根据完整上下文完成。目录或 wire contract 变化时会轮换旧的对话 session。

Service 也不读取正文后替 Agent 解释业务材料。它只传递 trigger、上下文、原始 process/task ID、
链接、本地受控材料引用和可执行的精确读取命令。文档、文件夹、图片、表格、日历、听记和 OA
材料是否相关、是否需要继续展开以及它们支持什么结论，都由 A 判断；B 在审核时独立复核。

Skill 加载属于 Agent 执行环境，service 不要求或校验普通 Consumer/Audit 结果中的 Skill receipt。
仅当发生外部写入时，SQLite 保留 provider 返回的操作标识，用于重试时识别已完成动作并避免重复写入。
SQLite 继续保存既有 task/run/attempt/provider result identifier 状态；系统**不建立平行的 Skill 审计数据库**，详细工具轨迹
仍以 Codex session JSONL 为准。

### 动态 Skill 分层

八个 CEO 业务 Skill 安装到 `~/.agents/skills`，按任务动态加载：

| 业务 Skill | 负责的业务判断 | 常见操作 Skill |
| --- | --- | --- |
| `ceo-message-triage` | 回复、反应、追问、无需动作 | `dingtalk-chat` |
| `ceo-calendar-invite` | 日程邀请是否接受、拒绝或追问 | `dingtalk-calendar` |
| `ceo-document-review` | 文档、文件、图片、表格的审阅路径 | `dingtalk-doc`、`dingtalk-drive`、Lark 文档 Skill |
| `ceo-meeting-work` | 听记、静默会、会议总结与行动项 | `dingtalk-minutes`、`dingtalk-chat` |
| `ceo-mail-review` | 完整邮件线程审阅和回复 | `dingtalk-mail` |
| `ceo-personnel-communication` | 人事信息的受众、可见性和最小披露 | 候选人/通讯录操作 Skill |
| `ceo-work-tracking` | 从来源提取 Task、证据化归属/承诺、关联正式 Project 与关注事项 | Task Agent 不直接写外部 TODO；合格 Task 经 Task 7 outbox 镜像 |
| `ceo-sales-weekly-report` | 按需核对销售目标、CRM 实际、公司及业务线进度评分并生成 workspace 周报 | `ceo-weekly-report`、`fxiaoke-crm-cli` |

`ceo-sales-weekly-report` 没有独立 producer 或功能开关。它由 Consumer 根据明确的销售周报请求动态选择，直接使用安装用户已有的 `sharecrm` 登录态；CRM 只读限制由 Skill 和 Codex automatic review 约束，不表示 service 建立了 `sharecrm` 命令白名单。

### 项目为中心的工作跟踪（已发布；业务验收部分完成）

2026-10-04 获批设计的代码经 PR #16/#17 发布；当前生产运行 `a385b86d`，
包含 2026-10-06 确认的项目 Memory 写入规则。真实生产样本已保存 4 份独立 ProjectContext，
并在没有 Task 成员时生成 2 张关注卡片；这不代表全部项目或多来源业务验收完成。
较新会议因不连续原文引文在领域应用阶段失败，项目身份覆盖、历史上下文补充、
真实建议/Memory/客户关联正向读回仍有未完成项。发布记录、预期与实际及证据边界见
`docs/task-project-centered-validation.md` 当前状态；本节描述当前契约，不宣称所有样本通过。

#### 来源、项目与任务

所有会议、管理/部门/项目周报、聊天、邮件均可作为事实来源；周报不是唯一来源或风险判断前提。
正式 Project 须有真实目标和范围，不把部门、客户标签、主题、孤立小任务或相似性 cluster 当成项目。
确认的报告项目登记表行、明确会议立项可以登记正式 Project；聊天/邮件补充既有项目事实，
不单独建立正式项目身份。保留不同时间、来源的冲突与引用，不静默以某类来源覆盖较新事实。

独立 `ProjectDecision.registration` 引用当前权威定义，含 exact title、authority 与非空 source_excerpt；
不是 Task 的嵌套字段，也不需要 Task 或 cluster 作为登记载体。报告核验实际类型、完整原文登记行
中的项目列与提案标题；登记摘录只定位原文，不移动列。Task/行动章节不是项目登记表。
registration.source_excerpt 必须是当前不可变来源中定义该项目的连续原文；一份报告列出多个项目时，分别记录各项目的判断与证据，不能用一个项目的判断覆盖其他行。每个 project_decisions[i] 必须有且只有一个对应 project_assessment。项目标题与 Task 行动分处原文不同位置时，project_link_evidence 引用一段从项目标题到行动的连续原文，保留中间内容，不拼接不连续片段。
会议核验实际 AI Minutes 或可信 minutes 会话和当前原文引文；立项含义由 Agent 判断，不加关键词规则。项目标题只包含实体名称，不把同句中的状态或动作并入标题；例“甲客户一期交付项目正式启动”的标题是“甲客户一期交付项目”，启动状态可作为事实。
先采用当前权威来源的精确定义再选对象：只有唯一活动、已登记且精确同标题的 Project 才复用
规范 anchor 和原 registry provenance；多活动同名对象为身份冲突，无对象才用现有来源登记方法。
旧相似名称、简称或标题前缀不能替代新的正式定义；已退休的来源项目不由本轮重新激活。

Project 可选关联 Fxiaoke CRM 客户；CRM 身份单独保存为稳定 AccountObj `_id` 和显示名称，不拼进项目标题，内部项目可保持未关联。Task 不复制客户字段：只有 Task 通过已确认的正式 Project 关系读取其 CRM 客户；独立 Task 不推断客户。Task Agent 通过 prompt/Skill 引导，将当前证据支持的持久、重要项目风险和进度/状态更新写入 Memory `memory_write`：简洁注明项目、原始来源与来源时间，区分来源事实与有证据的风险推断。不写单条 Task/TODO、日常活动、临时讨论、无依据猜测、秘密、原始转录或重复信息。这项约束不增加工具白名单、通用只读模式或 CLI/MCP 代码拦截。

服务通过已认证的 `sharecrm data record query-by-name` 对 AccountObj 做只读名称解析。`crm_customer_evidence` 还必须作为同一条引用出现在 Project 的 `evidence` 中。该解析器不保证候选完整或精确，单候选也只是候选；来源扫描和 Project 页面查询得到的所有 CRM ID 都须经用户明确确认后才能建立本地关联。多候选展示供选择，`NO_MATCH` 与查询不可用分开保留。查询失败或后续冲突不会清除已确认关联；冲突需显式处理。客户视图按 CRM `_id` 聚合已确认关联的项目，未关联项目（包括内部项目）留在常规正式项目视图，不进入客户分组。

`ProjectContext` 独立于 Task 保存完整快照：goal、scope、最多一名 overall_owner 或 null、
其他人的不同职责 responsibilities，以及各自的事实 facts。每条职责/事实有实际原始 Signal/ref/
连续逐字引文；总负责与负责结果不能从 Task owner 猜出。未改变的职责沿用历史原文证据。
context=null 只补 Project↔Signal 关系；明确提供空分工快照才清空当前分工，旧版本仍可读。
当前 Project 读取最新 context revision，不双存可失配的 current JSON；相同结构快照不追加版本。
Agent 得到新事实时仍须返回完整的当前 ProjectContext 快照，带回先前有效职责、事实和出处；
不能因本轮只讨论一项新风险，就把未变化资料从快照中丢掉。
仅描述“某人负责某业务领域”的职责陈述只进入 ProjectContext，不单独生成 Task/candidate；
Task 需要来源明确的、可独立完成的交付物或行动，或由已证实重大风险引出的独立建议下一步。
正常项目序列中的例行里程碑和下一步即使写成行动句，也只记为 Project fact，不生成 Task/candidate。
总体负责人归属冲突时 overall_owner 留空，竞争主张与未确认移交记为有出处的事实，不能把候选总负责人改写成不同职责；ProjectContext 的事实必须明确写“总体负责人存在冲突，仍待确认”，不能只写“未形成一致确认/仍需核实”；独立负责人的真实交付职责仍保留。
会议明确某人总负责 Project 时，该人进入唯一 overall_owner 而不是一般职责；已有真实 Task 已直接处理同一风险时，用该 Task 支撑项目判断，不另建重复的监测/评估建议。
“X负责Y”仅描述职责，即使 Y 是独立业务领域，也不自动生成 Task；需要来源明确的独立行动/交付要求或真实行动项记录。
保留 existing_attention_id 时，assessment 必须引用当前 `current_project_attention.assessment_json.evidence` 中至少一条完全相同的原始证据三元组（signal_id、source_ref、source_excerpt）；新证据只能补充，不能替代卡片原始证明。
区别职责与行动时，“李四负责核实客户付款排期并反馈”是具体可执行 Task；“王五负责商务对账”若仅描述职能范围则是 ProjectContext。项目级风险的显示型建议可基于已保存职责，也可由唯一 overall_owner 承接跨职能协调，但不构成正式指派。

`business_source_documents` 保存不可变原文，Signal 通过真实 source_document_id 外键引用。
只有来源类型/ref/时间、会话、作者 ID/姓名/类型和正文八项完全相同才共享正文；Signal ID、
dedupe_key、上下文、Task 证据角色与历史仍独立。同 ref 的 memory/session 引文不冒充 observed 原文。
旧库在既有重建事务逐行读回身份、正文、计数和外键后删除旧正文物理列，失败回滚。
公开 BusinessTaskSignal.evidence_text 仍由 JOIN 返回原文；Task/event/run JSON 不重写。
当前业务 schema 是 `2026-10-04.4`，不等于生产已迁移。

Console 提供两条只读的原始证据接口。`GET /api/console/tasks/projects/{project_id}/evidence`
按既有 Store 顺序分页返回 `BusinessProjectEvidence` 引用（project_id、signal_id、created_at），
不复制来源正文；第一页取最新窗口，meta 返回 total、has_more 和下一页 cursor。项目详情原有的
20 条有界来源及 context 引文 pinned 行为保持不变。`GET /api/console/tasks/signals/{signal_id}`
按精确 Signal ID 返回完整 `BusinessTaskSignal`，evidence_text 来自不可变 source document 的原始
正文，不走展示截断或值规范化；相同 source_ref 的不同来源版本仍由各自 Signal 和
source_document_id 区分。两条接口都在一个只读 SQLite snapshot 中执行，不修改业务领域表。

Task 保留真实独立交付物和行动的身份。来源明确行动记 origin=source；缺真实负责人/授权等时
仍可为候选，不因为项目有总负责就伪造 Task 指派。新增任务必须非空标题；已有 ID 的更新可不填标题，
仅 update_fields 修改提供的标题/描述，promotion、acceptance、merge 保留持久化标题。
来源有明确的 authorized assignment、self commitment、external TODO 或具体有个人负责的
meeting action 才按既有 FormalityEvidence 正式化。AI Minutes 区分 owner_kind/owner_relation，
团队、仅发言或通用发言人占位不是个人负责人。owner ID 来自可信来源身份，不由 Agent 补造。

按项目事实和有出处的职责推导下一步，用同一 Task 表的 origin=agent_suggestion 与 suggestion_json。
suggested_owner 不写实际 owner；建议记录为 open candidate、commitment=none、无 formal_basis/
真实 owner/deadline，不产生 TODO、通知或 follow-up。最新消息不必再次点名建议人，历史职责和
事实均须有原文证明；Agent 理由或上轮建议不是人类安排。之后真实指派沿用同一 ID promotion，
保留建议发现历史，真实接受后不因 origin 阻止原有 TODO 资格。真实 source Task 不被重新标为建议。
后续更新必须携带已检索 Task ID，不从可共享/被合并复制的证据关系猜唯一任务。
仅改建议理由时省略 title/description 保留原值；确实变更才记事件。
发现尚未解决且需要推进的重大项目风险时，即使原文未直接指派某人，也可依据该项目已保存且有出处的
职责提出带 Project 关联、建议负责人和下一步的显示型 Task 建议；当重大影响与已保存职责直接相关时，
即使当前消息没有写出下一步，也应推导一条具体建议。仅当现有 Task 处理同一未解决风险时才抑制重复建议；
已完成或无关的项目 Task 不算该风险已被处理。这不构成正式派活。普通进展、已解决事项、单独的模糊线索
不因“可能有帮助”而生成建议。
项目风险也可在没有 Task 时独立生成需关注；若没有已保存职责能支持具体下一步与建议负责人，不补造监测任务、负责人或期限。保留已有 Attention 卡时只沿用实际已保存的 Task 成员；若原成员为空，不因新建的同风险候选 Task 扩大成员集合。候选 Task 仍可单独关联 Project。

正式指派是 assigned_unaccepted，不等于本人已接受。接受仍需唯一既有 Task、本人身份、
精确已链接指派 Signal、同会话与可信 reply_to_source_ref；收到、TODO 存在或服务消息不证明承诺。
但未接受本身不自动构成项目风险：若当前证据显示工作正常推进、没有实质影响或负责人归属争议，应保留 assigned_unaccepted，不单独生成 Attention。
任务继续推进、收到材料或更新项目背景均不等于本人接受；commitment_status 只有来源明确记录接受、争议、完成或取消时才更新。
日期明确区分系统创建、assigned_at、requested/external/committed/estimated_deadline_at、
next_check_at。来源日期需完整可解析当前短语及可信 actor，不能给原文补时分或用报告名作 actor；
AI Minutes 暂无可信 speaker→identity 映射，不把多方日期归给主持人。assigned_at 来自可信指派时间；
committed_deadline_at 需实际接受/承诺；next_check_at 只记录明确检查日期，不创造 cadence。
项目登记 DDL 不转成 Task DDL。一般 Task 的历史 memory/session 引文可完善候选，但不能代替
正式化、接受、合并或类型化日期所需的当前授权/身份；项目/职责/建议/关注证明只用已观察原文。

#### 一个 Agent 的当前输出与应用

TaskAgentDecision 必须明确返回三项无默认列表：
`project_decisions`、`task_decisions`、`project_assessments`，各为 0..N。
项目资料/判断可以有零 Task，空 Task 不等于无业务结果；没有相关项目/线索的空 assessment
仍需非空 update_summary。当前 parser 不接受 TaskDecision 中的旧 project_proposal、
project_link_proposal、attention_proposal。历史运行只保存原生执行引用，原始决定按该次准确范围读取；
原生来源缺失或旧格式无法解析时，过程详情明确不可用，不从数据库副本恢复或重新执行。
已应用的业务投影回执仍由服务保存。
Codex CLI 路径通过 `--output-schema` 使用 `app/schemas/task_agent_decision.schema.json`，
该文件由 `TaskAgentDecision.model_json_schema()` 生成并由测试校验一致；strict schema
要求每个对象显式返回全部属性，省略值用其契约允许的 `null` 表示。服务仍运行本地 Pydantic
语义校验，失败时最多在同一 session 修正一次；结构化输出不取代业务校验。

ProjectSelector 使用既有 anchor_id 或零基 project_decision_index 二选一；后者指向顶层
Project 决定，不是 Task 列表。Task 用 project 与 project_link_evidence 明确归属，复用真正已有
confirmed link；新关联依据当前或历史实际原文证明，不按词面相似性确认。
所有 link 引文都保存为 Task 和 Project 的证据关系，canonical link 保留首条证明。
关联本身没有 Task 字段变化时也可应用，使用其实际证明 Signal/anchor 写回执；不虚构 details_changed、
重排 follow-up 或发 TODO。普通仅重述 Task 且没有新关联证明的 update 仍 skipped。
来源明确的报告登记与 Task 显式 project_decision_index/cluster_id 可确认已有项目候选及原分组；
已确认关联的首条证明不覆写，新来源追加为补充证据。新确认或重新有效的成员传给成员-only
recompute，不把同项目但不支持该卡片的 Task 自动加入关注，也不重判风险。
Task 关系用已有 related_task_id 和相对当前实际 Task 的 direction，应用后派生两端；
相似交付只 proposed relation/cluster，不直接身份 merge，原始身份合并证明语义不变。

每个 assessment 针对一个实际 Project 或身份待明确的线索。one Project/精确标题 一轮一项；
覆盖所有已输出项目决定、Task Project selector 及当前引用 Task 的 confirmed 正式项目。
支持 Task 决定用 decision_indexes，已有 Task 用 task_ids；两者可空，不伪造支持成员。若 Task 决定更新的正是本次评估中的同一具体工作（包括正常进展的 not_needed 评估），应将该决定列为支撑；已有 Task 也只有直接支撑评估时才列入。仅关联到同一 Project 不够，完成或无关 Task 不是成员。
未登记线索保留原名与原文，outcome=insufficient_evidence，不借标题猜 ID。
真实正常进展可 not_needed，没有风险或没有 Task 本身不等于证据不足。
needs_attention 需实际已核验卡片或 assessment 自己的 attention_proposal；负面结果不生成或关闭卡片。
提案没有重复 anchor/task selector，由所属判断解析 actual Project/支持成员；不在每条 Task 复制。

当前 null-ID 引文精确匹配 immutable Work Item ref/原文；历史严格正 Signal ID 精确匹配保存的
ref/逐字引文且为 observed 原始来源，JSON 引文可在一个解码叶子内，不跨字段拼接。
historical_comparison 同时用当前与历史原始证明；当前对旧报告的转述不是旧报告，首次当前事实判断
仍允许。缺原文只说明不确定，不用 session/Memory 充当原始事实。
在同一领域事务写入前验证这些 selector、实际对象和引用，再按 Project 登记/资料→真实 Task
生命周期或 record_suggestion→项目判断顺序应用。无效来源或身份冲突回滚本轮，不猜未来 ID。
支持 Task 必须实际属于所评 Project，或本轮同一支持决定用真实证据确认；不能只因 ID 存在算成员。

同一个既有 stored Project/assessment 只读校验器也在领域应用前、原有两轮 decision
repair 中通过短连接执行；其 ValueError 携带原始错误和被拒决定反馈给同一 Task session。
包括已有关注卡缺少其保存原证据的声明错误，不放宽原文、身份、成员标准。领域事务内
仍再次执行未改动的同一校验器；SQLite 操作错误和原子应用阶段错误不进入模型纠正。
没有新增校验政策、循环或纠正预算。
当前 null-ID 项目引文的反馈一次列出所有不同的拒绝原因、来源 ref 和被拒引文原样文本；
重复项只报告一次，不自动改写、模糊匹配或归一化引文，整体仍只有既有两轮纠正。

existing_attention_id 是该同 Project 活动旧卡原始证明的声明，不是 upsert 目标。
它须核验真实原始 Signal/ref/quote，并在 assessment 原样引用该卡至少一条保存的证明；
支持 Task 只取卡片实际成员，不把项目同伴当成员。真实旧证明可与当前 attention_proposal 并存。
有当前提案时回执优先报告其实际 applied/rejected/error，已验证旧卡 ID 不把失败伪装 existing；
只复用旧卡时才 existing。新风险按 Project stable key 更新，需关注不等于需介入，watch 可无需 CEO 动作。

#### 回执、检索及尚未完成的关注集成

Project 决定回执保存实际 project_decision_index→project_id/anchor/revision_id/signal_ids；
Task 决定保存实际 decision_index→Task/Signal/anchor。skip、失败接受和无操作决定不伪造映射。
项目-only、建议-only 输入仍可 completed/done；当前线索判断也保存真实来源，不依赖 Task 载体。
逐 assessment 回执区分 recorded/applied/existing/rejected/error，引用只附实际来源 ID/时间/link；
原生决定中的 outcome/reason 不被应用回执改写；服务只保存实际应用的 projection 回执，
不保存第二份原始决定。领域事务先写 pending 回执，卡片消费者及
最终回执在提交后；错误可观察但不把已完成 run 改失败，不增恢复 loop 或读路径自愈。
proposal_count 按 assessment 自己的提案计，applied_count 按成功卡 ID 去重，
project_link_count 为实际确认/复用的不同 Task↔Project 关系数；registry_row_count 只统计原始报告登记行。
最终回执保存失败保留 pending 与日志；未记录 run 的直接 apply 不创建假 run。

Attention domain 要求正式活动 Project 与 business_project_evidence 中已存在的原始 Signal 关系，
不要求至少一个 Task，也不借 Task evidence 作为 Project 证明。可选成员必须是真实、相关、未合并的
Task 且已确认关联同 Project；当前显示成员仍只取 open/waiting。零 Task positive assessment 只有
实际 upsert 并读回卡片后才为 applied，重复来源不产生重复卡或事件。
显式 resolve 要求同 Project 的原始解决证据，不依赖 Task 当前/历史成员；not_needed、Task 完成或
成员清空不自动 resolve。recompute_for_tasks 保留且仅更新既有卡片成员、时间和成员事件，不重新
判断风险、不创建卡片、不修改 category/current_state 或 active/resolved。人类 TODO 完成后直接
调用这个成员更新，不再调用旧 Agent 卡片适配或要求虚假 receipt。
Schema .4 的一次性迁移仅把旧 active 卡片所属 Project 已确认有效 Task 关联中的原始 Signal
建成 Project evidence，排除 memory/session provenance，不改历史对象或引用，不重新激活 retired
Project；原有关系即使 anchor 后来退休也仅作历史证明保留。独立 marker 与证据插入同事务提交；
已有证据保留，多角色 Signal 去重，失败整体回滚，后续初始化/读取不继续补写。
只读 inspector 输出当前 Project 决定、真实回执映射及由这些实际身份查得的最新 context/revision；
历史缺字段仍缺失，不补为 not_needed，不推测 ID，不改源库或历史 run。
多来源回归已经通过；v2 native 对照、W39 副本迁移/回放和生产迁移尚未全部完成。评测读回独立判断的提案，
要求每项提案有对应真实卡片与实际 applied 回执；缺回执或仅写 completed 不判成功。
当前 negative assessment 也要有对应保存的回执，历史缺字段不自动升级。既有会议表外键问题另行处理，
不通过忽略外键或修改 frozen baseline 隐藏。这里没有新审批、工具白名单或风险关键词逻辑。

检索先读取正式 Project 当前资料/证据，再相关 Tasks；同来源旧身份强制保留，预算先给它们。
零 Task 项目仍可读 context、当前职责/事实证明与有界近期来源，context revision limit=1，
证据有界并固定保留引用 ID。current_project_attention 保留真实卡成员和原 assessment 原文。
source_documents 提供精确版本的共享正文一次，source_signals 保留所有 ID/元数据并指向正文；
当前 WorkItem 正文也只出现一次，immutable 输入不改。历史长文在 2048 字符预算内保留首尾及关键
引文，visible_ranges/full_length/truncated 和引用超预算明确显示，JSON 解码引用有实际 leaf 路径/偏移。
source_metrics 是正文字符观测，不是总 prompt token 保证；检索排名不证明身份、分工或业务重大性。

Task Agent 共享 task-agent:work-tracking:v1 逻辑 session，各 route 保留各自原生 session，
native CLI 自行 compact；每个 WorkItem 仍独立 workload/run。process-work-items 领取前持有 SQLite
共享会话锁，运行时续租并在提交前核验；失锁本轮不提交，普通既有重试策略不变。
旧 task:<run_id> session 历史保留。prompt 只用新加载的 CI Skill revision 4，旧 scheduled
skill_protocol 不注入；scheduled prompt 仅保留专项业务范围，旧 payload/history 不改。
全局 Skill 尚未发布。只读工具行为由 prompt 引导，不是技术上禁用 CLI/MCP 写入的权限边界。

完成只由新信息驱动。独立 completion Agent、周期性任务完成检查已删除，旧三类 completion-check
输入到 Agent 前 skipped，枚举仅保历史；todo_changes/follow_up_changes/search_trace 没有当前应用路径。
人类 DingTalk TODO 完成扫描只关闭对应 Task；外部 TODO 镜像/完成仍走现有 outbox，
不由本 Task Agent 直接操作外部记录。隐藏 maintenance/recovery 业务 loop 不恢复。

Attention 只读详情读取实际保存的 assessment_json 与准确引用 Signals，不重检索或补造历史 {}；
页面现有“来源事实/Agent 判断”、watch“关注点”与 decision/push“你的动作”保留。
项目列表直接读保存的 ProjectContext，展示唯一总负责人、整体负责事项、事实近况和实际活动卡片
的关注原因。删除从关联 Task 周报重新推导项目的读路径；未知仍待明确，不借 Task owner。
详情按整体情况、总负责及分工、关注原因、来源任务、Agent 建议、来源与修订展示；来源任务
包含 Project 已确认关联的来源候选，实际安排以任务阶段和承诺为准，执行计数不是项目经营结论。
当前建议仅指 origin=agent_suggestion 且 stage=candidate；同 ID 人类晋升后进入真实任务，
显示实际 owner/date，原 origin 和 suggestion 仅作为历史依据，不再计入建议。
Task 日期标签只采用与实际日期匹配的 typed date evidence；未知类型不猜请求或承诺截止。
Project 资料更新时间取实际保存的 Project/context revision/evidence 时间，不借 Task 时间或报告周期。
Project 来源首批最近 20 条加当前引用原 Signal，修订最近 20 条，并返回真实 total/has_more；
页面明确未展示全部历史，没有假的加载更多。零 Task 关注保留来源与判断，显示暂无关联任务。
API、TypeScript、mock、页面同步；合成浅/暗/窄屏验证不等于 native 或生产业务验收。

业务 Skill 说明“如何判断”，操作 Skill 说明“如何读取或执行”。OA、面试和 OKR 已有成熟的专业
Skill，CEO Skill 只负责识别需要委派的场景，不复制专业规则：分别加载
`dingtalk-oa-approval`、`xiaoqing_interview`/现有面试 Skill、`dingtang-okr-review`。

日程邀请在进入 Consumer 和 Audit 前，service 会读取邀请的时间窗并把所有已确认占用的
重叠日程作为结构化事实传入。个人 `Blocked`/睡眠占位是硬边界，不能被会议覆盖。两个会议
重叠时，Agent 按目的、紧迫性、必要参与人、负责人和 principal 的必要贡献判断：原会议更重要时
保留原会议、拒绝新邀请并向新邀请人说明理由；新会议更重要时接受新会议、拒绝原会议并向原邀请人
说明理由；无法判断时先通知新邀请人补充重要性理由或联系原邀请人协调，再进行第二次判断。新邀请
的开始时间换算到 principal 本地时区后若晚于 23:00 且存在任一占用冲突，则不比较重要性，直接拒绝
新邀请并向其邀请人说明本地夜间冲突。

### Settings 中的功能机制开关

`Settings -> Skills` 管理的是功能机制，而不是单个文件的可读性。功能清单保存在
`data/config/skill-features.json`，一个功能可以关联多个顶层业务 Skill，一个业务 Skill
也可以被多个功能复用；不存在 Skill 下的子 Skill。启用状态单独保存在
`data/config/skill-state.json`，默认值来自功能清单，状态写入采用原子更新。

开关只作用于对应机制为**新输入创建任务**的入口。关闭后，新的会议、邮件、跟进、任务扫描或
其他有明确生产边界的输入不会入队；已经排队、运行中或可重试的任务仍按原有生命周期继续处理。
共享 Skill 不会从 catalog 删除，其他启用功能仍可使用它。普通消息无法在任务创建前可靠地按
业务领域分类，因此不会用关键词把文档或人员 Skill 拆成独立路由；这些 Skill 仍由通用消息
Consumer 在完整上下文中按需发现。

Skill 的唯一权威来源是 `~/.agents/skills/<name>/SKILL.md`（可用 `CEO_SKILLS_ROOT`
覆盖，仅用于测试）。仓库不再保存第二份副本：两份并存时，改动可能落在没有被加载的那一份，
而且不会报错。服务启动时从该目录导入受管基线并做版本管理；发布到共享 Skill 库是单独的
对外动作，方向是从该目录出去，不是回来。Settings 编辑同样以该目录为准，保存会校验
frontmatter 并以 SHA 防止并发覆盖。用户、系统或插件目录中的外部 operation Skill 不在
该页面的编辑范围内。

因此本仓库的 `scripts/bootstrap-local-components.sh` 不再安装业务 Skill，只校验它们是否
存在；新机器从共享 Skill 库安装。

### Consumer Agent A

所有系统业务审核项必须对应实际系统任务及其当前业务实例；工具或接口存在不构成审核需求。没有对应系统任务就不设置该业务的系统审核项。当前对应关系见 [系统任务与审核范围](audit-task-scope.md)。小青面试与提交由 Derek 在个人对话中处理，不属于本次后台系统审核范围。扫描、下载和登录命令沿用既有技术结果验证，不因这条范围规则新增 Audit 回合。

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。

Consumer 可以直接使用普通工作工具，写入并读回本任务本代次的材料，或通过既有绑定的
日报/周报接口操作文档。普通写入不因为有副作用而被一律改成待审核系统动作；其事实证据是
真实工具结果与读回。`consumer_artifact_write` 写入任务工作目录，`read_task_artifact` 与
`list_task_artifacts` 为两角色提供只读材料；Audit 没有对应写入工具。系统已注册且需要审核的
动作仍必须完整提案 → Audit → SystemExecutor → 真实回执。Consumer 解析器不扫描整个候选的
自然语言完成短语，也不把摘要转换成执行事实。是否引用历史完成、是否还需要业务动作，由
完整候选、来源及实际回执判断；风险拒绝、授权缺失与历史拒绝不可重放的机制保留。

上述新审核执行契约适用于实际使用 AgentOrchestrator 的业务候选。已授权微信会话的 task3 保留原有 WechatReplyConsumer / WechatDecisionRunner 决策与持久化 delivery，随后由独立 Sender 循环发送；它没有独立 Audit 回合，本次未迁移到 candidate/review/SystemExecutor。微信结果中的 audit_summary 不是独立审核回执。系统任务对应表不代表新增该任务的审核流程。

发布可达性使用独立的显式单任务调度暂缓入口 `app.reply_task_deferral`，默认只读；本次操作
仅授权原 386130/386131。应用需完整备份及精确业务对象/代次/输入版本/失败 run 指纹，锁内
确认没有活跃处理者、候选或可归属的已有/未知效果后，才设置原任务的有界 available_at 并保存
调度回执。活跃 ownership 不能因批准或某时点没有 AgentRun 而忽略。恢复只撤销该回执的精确
延迟，身份、历史、错误与外部回执不改；相同请求幂等，中间变化拒绝。它不产生 Audit 通过或
业务完成，也不重放历史风险拒绝。具体 CLI 与资格检查见 runtime-mechanism。

候选分为动作计划和当前实例的人工问题。人工问题包含来源上下文、具体原因、证据、互斥可行选项及后果；可执行选项各自绑定完整动作计划，停止选项写明 skipped 和原因。只有 Derek 能补充的开放事实使用 requested_input，不制造假选择。不能混合立即执行的动作与尚未选择的条件分支。

Consumer 的业务结果与 wire JSON Schema 和解析器一致：`proposal`、`no_action`、`failed` 的 `decision_options` 为空，`requested_input`、`needs_human_reason`、`decision_basis` 不得有值；这些字段只属于 `needs_human`。普通方案的事实证据写在 `proposal.sourced_facts`，无需动作的依据写在 `summary`，不借用人工问题字段。

Audit 返回 approve、return、reject，必须绑定 candidate_digest 和 proposal_revision；failed 只表示技术失败。Audit 不修改正文、选项，不返回执行回执，也不自行创作另一个人工问题。return 允许保留正文并补足证据；reject 要求实质改变被驳回内容，不能只改描述、元数据。首次提交最多三次内容重提，耗尽为 failed，技术失败不占内容预算。

人工问题也先经过 Audit。Audit 检查：是否确需 Derek、规则/代码/Skill/记忆/会话/读取是否可自行解决、是否应向来源人索取材料、是否把技术失败伪装成决策、上下文和理由是否充分、选项是否可行且确有差异、每个执行分支是否完整并仅限当前实例。只有当前版本 approve 后才进入 needs_human 和通知，审核通过不等于业务 done。

选择接口提交 candidate_id、review_id 和 option_key，服务读取持久化分支。首次选择独立记录并将原 execution_generation 唤醒；相同选择幂等，冲突选择拒绝。有效分支直接由系统执行，不重新启动 Consumer/Audit。补充文字保留为新输入，使旧候选失效并进入新完整候选和审核；历史选择、问题和回执保留。选择事件不等于执行完成。不再提供 applies_to=task_class、可复用规则、Skill 更新复选框或此决策处理器的 Skill 写入副作用。

旧长期规则问题仅通过显式单项命令 `python -m app.rule_question_retirement --attempt-id <id> --authority <confirmed-contract>` 退役，默认只读预览；应用另加 `--apply --verified-backup <path>`。资格限定为当前业务对象、当前执行代、最新 Attempt 所绑定的已完成 Consumer、done 任务、完整无技术错误的 task_class 问题，且该任务没有新候选；不批量排除旧结果。应用只原子写入 resolved_at、resolution 与包含原结果 SHA256/任务/运行/执行代/契约依据的退役回执，保留原 send_status、模型结果、任务状态和外部回执。详情、History 和队列将该已退役问题显示为 skipped 并解释依据，重试 POST 拒绝。它不是人工选择、Audit 通过或业务完成；未退役和当前无效问题继续由原质量检查报错。预览及应用均不初始化或迁移数据库。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，stage_index、predecessor_review_id 绑定前一已完成阶段的回执；阶段上限独立于每阶段三次内容修订预算。

### 任务长期记忆

Derek 2026-09-24：长期记忆由执行 Agent A 在结果里给出、系统写入 Memory、不经审核。

- A 的结果协议（`ConsumerAgentWireResult`，四种 outcome 都一样）必填 `durable_memories`，可为空列表。
  每条是 `title`、`content`、`source_time`（信息在来源里产生的时间，ISO-8601）、`source_refs`
  （消息 id、文档链接、审批单号）和可选 `subject`（Person/Project/Customer/Organization + 名称）。
  写什么、不写什么只由字段说明约束（`app/agent_contracts.py` 的 `DurableMemory`）。
  旧结果读回时视为空列表。
- 任务在 `finalize_orchestrated_reply_task` 里进入 `done` 的同一事务中，服务从库里存的、该执行代最后一次
  完成的 Consumer 结果读出这个字段（不用内存里的编排结果对象，它多数情况下不带 Consumer 结果），写进
  `task_memory_write_events`：每个任务执行代一行（键为 `reply_task_id +
  execution_generation`），`pending` 带待写条目；没有条目记 `skipped / no_durable_memories`，
  没有 Consumer 结果记 `skipped / no_consumer_result`。所以每个经编排结束的任务都有一个记忆结论。
- 写入是系统自动行为，不是定时任务（Derek 2026-09-24）：统一 Dispatcher 的 `task_memory_write`
  adapter（`TaskMemoryWriteQueueAdapter`）像 DingTalk Todo outbox 一样，一入队就领取，
  `app/task_memory_write.py` 用服务自己的 memory-connector 客户端逐条调用 `memory_write`（与会议结论同一客户端）。
  dry-run 不注册该 adapter。
  正文与时间来自 Agent，写入前去掉服务附加的反馈回调链接和署名（`app/memory_text.py`，会议结论同样处理，并去掉跟进消息抬头；标识符只放 metadata，不进正文：固定内容会让 memory-connector 聚类把无关 episode 判成近似重复，Derek 2026-10-02）；`thread_id`（会话标识）、`source_metadata`（渠道、会话、触发消息、任务 id，
  外加 Agent 指认的 `source_refs`）和 `provenance_metadata`（执行者、Consumer run、执行代、线路、
  模型、用途 `task_durable_memory`）由服务从记录里填。每写成一条就记下它的 id，重试只写剩下的；
  连接器失败按退避重试（上限 20 次），之后记 `failed` 并进 Attention（`task_memory_write_failed`）。
- 这取代了让 Agent 自己调用 memory 工具：那条路在 2026-07-26 之后衰减到一条不写；也取代了
  memory-connector 插件的收尾钩子（服务的 `codex exec` 已固定 `--disable hooks`）。

### Audit Agent B

Audit 使用实际只读工具接口审核候选，没有受控发送、审批或命令执行能力。Consumer 保留任务绑定的文档和报告操作；Codex Consumer 使用原生 shell、unified execution、补丁和 V8 host 在当前 task/generation 的 consumer-artifacts 目录执行普通本地代码。原生 workspace-write 沙箱允许任务目录及原生临时目录写入，命令网络关闭；MCP 进程仍从服务源码目录启动，受控发送、OA 等注册操作仍只由 System 执行。Audit 排除内建 functions namespace 并保留 read-only 沙箱。两种角色均不开放 browser、image generation、委派、自动 Skill 安装、记忆写入或未登记 MCP 操作。Claude 仍只有受限内建读取与角色 MCP 工件工具，没有 shell 代码执行；Friday 不能承担这两个角色。原生执行验证必须保留实际命令结果和文件回读，写出代码文件不等于执行代码。

## 会话与反馈周期

Consumer 继续兼容业务会话，Audit 按现有角色会话继续读取和审核。每阶段初始提交加最多三次内容重提；return/reject 消耗预算，运行时和 provider 重试不消耗。原始 run、修订和错误 append-only，任务 processing 在反馈期间保持不变。

### 任务提取与 follow-up 是一个生命周期

`ceo-work-tracking` 把同一事项从识别到关闭作为一个流程：从对话、会议或材料中提取有证据的
Task，按需链接正式 Project，记录负责人及类型化日期。只有符合承诺与期限条件的 Task 才通过
outbox 镜像为钉钉 TODO；只有来源明确给出下次检查时间与目标会话才创建 follow-up。follow-up
只是建议：由 Derek 在 Task 详情页点按钮发送（Derek 2026-09-25），新信息更新该 Task 时撤回未发出的
follow-up，见 `docs/runtime-mechanism.md`。没有自动发送：到期的 follow-up 不会被发出，结果未知的
发送显示为失败、由 Derek 决定是否再点。发送记录 revision、租约、幂等 ID 与 provider 回执。读取后续
回复或新完成的外部 TODO 后，仅凭明确完成证据关闭对应 Task 与其 follow-up。旧 Project/TODO/follow-up 记录保留在历史边界，等待 Task 8 导入。

## Audit Rules

Audit Rules 是 A 和 B 共享的可见业务规则：

- 默认文件：`data/prompts/audit_rules.md`
- 配置页面：`Config -> Audit Rules`
- A 使用规则自检候选。
- B 使用同一规则独立审计并决定是否执行。

可配置内容包括表达、信息最小化、审批材料要求、特定业务风险和需要升级给 Derek 的判断。
以下边界不可配置：A 负责读取/判断并提出候选、B 负责 accepted action 的正式执行、精确 revision 去重、最多三个内容反馈周期、
外部动作标识去重以及敏感凭证不进入提示词和审计页面。

## 能力与配置

### MCP 服务器可见范围

Codex 会把 `$CODEX_HOME/config.toml` 中的全部 MCP 服务器合并进每次运行，后台 Agent turn 因此
默认能看到安装用户的个人工具。服务清单（`CEO_SERVICE_MCP_CONFIG_PATH`）除了声明服务自己的
transport，还用 `disabled_servers` 列出后台 Agent 不得使用的个人服务器；命令组装时对这些名字发出
整表 `enabled = false` 覆盖（Codex 不接受单字段覆盖，占位 transport 不会被启动）。Settings → MCP
读取 `codex mcp list --json` 展示全局服务器与清单服务器，保存即写回清单，从下一个 Agent turn 生效。

后台 Agent 使用安装用户的原生 CLI home 和已安装 skills，MCP 直接连接；不复制 OAuth header、token，也不添加本地 credential proxy。Codex 每轮按角色发出显式工具 allowlist：Consumer 的 agent_cli 普通材料读写与原生工作区代码执行可用；Audit 仅保留只读审核能力。Codex 清单中的第三方 Memory、Xiaoqing 等工具只开放已列出的读取能力，不能把个人安装的全部 MCP 写能力视为后台授权。Claude 的本轮 MCP 配置只连接 agent_cli，沿用其原生 home 和 inline 配置。

Codex 后台角色固定 `features.plugins=false`；`codex exec` 固定带 `--disable hooks`（Derek 2026-09-24）。插件 Stop hook 曾使结构化结果重复；长期记忆由 Consumer 在 `durable_memories` 提出、系统写入，见「任务长期记忆」。Consumer 生成候选并按共享 Audit Rules 自检；Audit 只读独立审核；System Executor 执行已持久化审核通过的受控动作。普通工具工作不因产生材料而自动注册为系统受控动作。个人对话中的小青面试或上传没有对应后台系统任务，不设置服务审核项。

Agent 不执行 `auth login`、`reset` 或 `logout`。MCP 实际返回未授权时，任务保留认证失败，不把它解释为材料缺失。

Agent 不得把嵌套 shell 里的 `codex mcp list` 或 `codex exec` 结果当成当前父 Agent session 的
MCP 注入证明。需要 Xiaoqing、Memory、Exa、Lark 等 MCP 时，Consumer/Audit 必须直接调用当前
session 中的对应 MCP 工具；只有直接工具调用或 provider 操作失败，才形成可持久化依赖失败。
`<server>_mcp_not_injected` 这种自报注入状态是 wire result 契约错误，进入修正轮而不是任务终态。

### Agent Runtime 路由模型

`CEO_AGENT_RUNTIME_ROUTES` 是一个有序的路由名列表，**顺序就是故障切换顺序**：前一条不可用时
Router 取下一条已配置且健康的路由。列表里出现的名字分两类：

- **内置路由**：只有 `codex_oauth`、`claude_oauth`、`friday_runtime` 三条（常量
  `SUPPORTED_RUNTIME_ROUTES`，Derek 2026-09-24），名字固定、不能改名，使用各自固定的环境变量
  （`CEO_CODEX_MODEL*`、`CEO_CLAUDE_MODEL*`、`CEO_FRIDAY_RUNTIME_*`）。
- **添加的路由**：列表里任何其它名字（小写字母、数字、下划线，字母开头）都是运维自己加的路由，
  由它自己名下的 `CEO_RUNTIME_<大写名>_KIND` / `_BASE_URL` / `_MODEL` / `_API_KEY` 描述，
  模型自由填写。`KIND` 取 `codex_oauth`、`codex_api`、`claude_oauth`、`claude_api` 之一
  （常量 `ADDED_ROUTE_KINDS`）；OAuth 两种复用本机 CLI 登录，只需要模型、不需要 Token
  （`ADDED_ROUTE_KINDS_WITH_KEY` 之外）。因此同一种 provider 可以配置多条，各自指向不同地址
  和模型——`RuntimeRoute.base_url` 就是为此存在，Codex adapter 用的是**该路由自己的**地址，
  而不是某个全局配置。`codex_api`、`claude_api` 以前是内置路由（`CEO_CODEX_API_*`、
  `CEO_CLAUDE_API_*`），现在只是两条添加线路的名字，没有任何代码按名字认它们。
- **按种类、不按名字**：依赖认证方式的规则看 `RuntimeRoute.is_cli_api_route`（凭自己 API Key
  登录的 Codex/Claude CLI 路由）。「临时失败后在同一路由换新会话重试一次」适用于所有这类路由；
  Consumer 连续两次结果不可用时强制换新会话，对所有路由一视同仁，没有例外（Derek 2026-09-25；
  此前凭 Key 的 Codex CLI 路由沿用 `codex_api` 的例外，已去掉）。安装向导按配置顺序列出全部路由，`probe-agent-runtimes` 在配置无效时对
  任何缺 Key 的 API 种类路由报 `missing_secret`。

**一次性迁移**：服务 supervisor 在启动 worker、web、email 三个子进程之前，先用独立进程运行
`python -m app.agent_runtime_migration`。`.env` 里仍有 `CEO_CODEX_API_*` / `CEO_CLAUDE_API_*`
时，把在 `CEO_AGENT_RUNTIME_ROUTES` 里的 `codex_api` / `claude_api` 原样写成
`CEO_RUNTIME_CODEX_API_*`（KIND=codex_api，BASE_URL 缺省为 `https://api.openai.com/v1`，
MODEL 缺省取 `CEO_CODEX_MODEL`）和 `CEO_RUNTIME_CLAUDE_API_*`（KIND=claude_api，MODEL 缺省取
`CEO_CLAUDE_MODEL`），**名字不变**，定时任务、会话续接、暂停这些引用都不用动；不在列表里的
那条不迁移（添加的线路只在列表里时才存在），`CEO_AGENT_RUNTIME_HIDDEN_ROUTES` 里的这两个名字
去掉（隐藏列表只记内置卡片）。写之前把 `.env` 备份为 `.env.runtime-routes-migration.bak`
（固定文件名，只留最新一份，写前核对字节一致），然后删掉旧键。旧键不在时什么也不做，所以重复
运行无副作用。迁移放在独立进程是因为 supervisor 进程一旦 import `app.config` 就会把 `.env`
抄进自己的环境，之后重启的子进程会把这份旧值当成权威。

控制台的 Settings / Agent Runtime 直接编辑这份列表：拖动卡片改顺序，开关决定该路由是否在列表
里。内置卡片删除时把名字记进 `CEO_AGENT_RUNTIME_HIDDEN_ROUTES` 并只清除该路由**独有**的凭据
（共用的设置保留），可以从「新增 runtime」恢复；添加的线路关掉或删除都会把它移出列表，保存时
清掉它名下的 `CEO_RUNTIME_<名字>_*`。

**改名**只对添加的线路开放，由服务端完成（`POST /api/console/settings/agent-runtime/routes/{名字}/rename`，
`{"new_name": ...}`；页面上改卡片名字即调用它）。新名字须合规、不能是三个内置名、不能与已配置的线路重名；
内置线路不能改名。一次改名先在**一个数据库事务**里把按名字引用这条线路的地方全部改过去
（`AutoReplyStore.rename_runtime_route`）：定时任务首选线路 `scheduled_tasks.runtime_id`（版本号 +1）、
还可能被派发或重建的定时运行快照（`scheduled_task_runs` 中 `pending`/`dispatched` 通过 `snapshot_id` 引用的配置版本的 `snapshot_json.runtime_id`；
`skipped`/`failed` 已是终态不改）、会话续接 `conversation_runtime_sessions`、线路暂停 `runtime_route_pauses`、
能力快照 `service_state` 的 `agent-runtime-capability:<名字>`（连同快照里的 `route_name`）；新名字下已有的
残留行属于一条已不存在的线路，被覆盖。历史 `agent_runtime_attempts.route_name` 不改，它记的是当时用的名字。
数据库提交之后再改 `.env`：线路顺序里换名、`CEO_RUNTIME_<旧名>_*` 搬到 `CEO_RUNTIME_<新名>_*` 并删除旧键
（隐藏列表只记内置名，不涉及）。先数据库后 `.env` 的理由：数据库这一步提交后再跑一次什么也不动，
`.env` 写失败时原样重试同一次改名即可补完；反过来先写 `.env`，数据库失败时 `.env` 已经没有旧名，
重试会被「旧名不在已配置线路里」拒绝，引用就永久分裂了。运行中的服务不重读配置，新名字在下次
重启后生效（与其他设置保存一样）；改名到重启之间，到点的定时任务会因为「首选线路未配置」被跳过并进
Attention，所以改名后应尽快部署（`python -m app.deploy`）。
保存只有一个入口：React 设置页提交到 `POST /api/console/settings/agent-runtime`，字段用 `.env`
键名，由 `app/web_api/agent_runtime_settings.py` 校验并写入。读取同一接口时，凭据类字段（`*_API_KEY`、Friday 的 ticket / session token）只返回部分遮蔽值（12 位及以上保留前 3 后 4，其余 `****`；更短的整体 `****`），服务从不把完整凭据发给控制台（Derek 2026-09-25）；页面把遮蔽值原样存回，服务认出它等于已存凭据的遮蔽值，就保持已存凭据不变，输入新值才替换。旧的服务端渲染页
（`/config?tab=agent-runtime` 与表单 `POST /config/agent-runtime`）已删除——React 设置页
上线（2026-08-29）后浏览器访问页面时拿到的一直是 SPA，那套页面只剩测试在用。

健康探测（`app/agent_runtime_probe.py`）与真实 turn 走同一条 provider 路径，因此超时按一条真实
turn 的长度给：`PROBE_TOTAL_TIMEOUT_SECONDS` 和 `PROBE_IDLE_TIMEOUT_SECONDS` 都是 300 秒，
服务读取这两个常量作为 `CEO_RUNTIME_PROBE_TIMEOUT_SECONDS` / `CEO_RUNTIME_PROBE_IDLE_TIMEOUT_SECONDS`
的默认值——两处不同的默认值曾把一条健康的路由判成不可达。`probe-agent-runtimes --route` 接受
任何已配置的路由名（含新增路由），并且**不采纳其它进程的快照**：显式探测必须给出本进程自己的
结果，否则运维看到的可能是服务几十秒前的结论。

### Claude Runtime 路由

Claude CLI 有两种路由：内置的 `claude_oauth` 用 `CEO_CLAUDE_MODEL`（默认 `sonnet`），
Claude API 种类的添加线路（例如名为 `claude_api` 的那条）用它自己的 `CEO_RUNTIME_<名字>_MODEL`。
两种共用 `CEO_CLAUDE_MODEL_REASONING_EFFORT`（默认 `medium`，取值 `low`/`medium`/`high`/`xhigh`，
作为 `--effort` 传给 CLI）。调度任务上的 thinking 选项会按次覆盖该默认值，与 Codex 的
`reasoning_effort` 使用同一套取值。下文的 `claude_api` 指任何 Claude API 种类的线路。

| 路由 | 凭据 | 命令差异 |
| --- | --- | --- |
| `claude_oauth` | 本机 `claude` CLI 的登录态（订阅） | 不使用 `--bare`，不设置 `ANTHROPIC_API_KEY`，`CLAUDE_CONFIG_DIR` 不覆盖 |
| `claude_api` | `CEO_RUNTIME_<名字>_API_KEY` | 使用 `--bare`，凭据只进子进程环境，`CLAUDE_CONFIG_DIR` 同样不覆盖 |

两条路由都不覆盖 `CLAUDE_CONFIG_DIR`，直接复用调用方真实的 `~/.claude`：`--bare` 已经
保证 `claude_api` 的认证只能来自 `ANTHROPIC_API_KEY`（不会读到 `~/.claude` 里缓存的 OAuth
凭据），所以没有必要为凭据隔离单独换一个配置目录。每次调用的 settings 和 MCP 配置由
`ClaudeRuntimeAdapter._invocation_boundary` 构造成两段 JSON 字符串，作为 `--settings` /
`--mcp-config` 的参数值直接传给 CLI；不写任何文件到磁盘，调用结束后也就没有文件要清理
（`app/claude_runtime_adapter.py`，AGENTS.md「Keep runtime plumbing simple」：inline 传参，
不做临时文件这类防御层）。

`--bare` 规定 Anthropic 认证只能来自 `ANTHROPIC_API_KEY`，因此订阅路由必须去掉它，改由
CLI 自己解析本机登录态；`CLAUDE_CODE_SIMPLE=1` 与 `--bare` 等价，同样不可用于该路由。
`--safe-mode` 虽然能屏蔽个人配置，但会连同 `--mcp-config` 显式传入的服务 MCP 一起停用，
所以两条路由都不使用它。两条路由的隔离都由 `--setting-sources ""`、`--settings` 和
`--strict-mcp-config --mcp-config` 保证：调用方的 CLAUDE.md、skills、plugins 和 hooks 都
不会进入服务运行。两条路由都会把会话文件写入调用方的 `~/.claude/projects/<cwd>`，与本人
交互式会话共享订阅额度（`claude_api` 用的是独立的 API Key 配额，只是会话记录文件位置共用）。

Claude 事件语法只把 turn item 映射成 runtime 事件。传输层遥测不携带 turn item，
统一映射为空事件：订阅额度窗口 `rate_limit_event`（可能出现在 session init 之前）和
`--effort` 触发的 extended thinking 预算通知 `system/thinking_tokens`。终局 `result`
仍然决定这一次调用的成败；除这两类以外的事件形状仍然是语法违规，不做静默丢弃。

未登录时 `claude_oauth` 的健康探测失败，Router 直接跳过该路由，不影响其余路由。

`claude_oauth` 已登录却仍失败时，先看 403 原文：`OAuth token does not meet scope requirement`
表示 keychain 里那份 token 没有 `user:inference`。在真实终端跑 `claude auth login` 会重新签发带推理
scope 的 token，但 keychain 里这份会被本机其他进程改写，线路随之再次失败（2026-09-17 观察到
03:49:39Z 改写、04:00Z 再次 403。改写者是本机 quota guard：它那一轮在 03:49:36Z 向 Hub 上报，与
keychain 修改时间只差 3 秒。guard 可能把一份不含 `user:inference` 的凭据整份写回 keychain，该机制
由 quota-report-hub 跟进，尚未用实际 scopes 证实；Hub 用 profile/usage 接口检查凭据，不需要推理
权限，所以 Hub 侧会一直显示正常）。因此本机登录态只适合临时使用；需要稳定可用的 Claude 线路时
使用 `claude_api`。
复现服务所见的情况时必须用服务的最小环境运行 CLI（`env -i HOME=… PATH=… USER=… claude -p …`）：
在 Claude Code 会话里直接运行 `claude -p` 会继承桌面应用自己的凭据而成功，不能证明服务可用。

所有 workload（Agent turn、任务 Agent、会议、邮件分类、workbench、TODO 截止日期回填）都能路由到
Claude。Claude 没有独立的 developer instructions 和 output schema 通道，统一由
`app.claude_runtime_adapter.claude_input_contract` 把指令、输出 schema 与任务拼成同一条消息；
事件流由该 adapter 的 normalizer 校验，最终消息再整理成 Codex 同形的 runtime 事件交给 workload
的解析器，解析器不需要知道是哪条 runtime 执行的。

### Friday Runtime 路由

`friday_runtime` 是与 `codex_oauth`、`claude_oauth` 并列的内置 Agent Runtime 路由。它通过 Friday Runtime 的 HTTP 接口创建一个 Thread、提交一个 turn、等待
operation 完成，再读取该 Thread 的最终 Artifact；CEO Agent 不直接调用 provider 的 API。

**Friday 由它自己的 CLI 运行，本服务不安装也不启动 Friday。** CLI 随 Friday 桌面版一起安装，
路径取自 Friday 的安装记录（`~/.friday/install.json` 的 `cli_executable_path`，通常是
`/Applications/Friday.app/Contents/MacOS/friday-cli`）。没有检测到该 CLI 时，控制台把这条路由
置灰，保存也会被拒绝——没有 Friday 桌面版就没有可调用的 Friday。

一次调用按以下顺序解析，全部不需要人工配置：

1. **地址**：`friday runtime start` 启动或复用共享的无界面 runtime（不需要打开桌面版应用），
   Friday 把地址写进 `~/.friday/runtime/default.json`，服务每次读取。该端口是动态的，每次启动
   都不同，因此地址不能写死或缓存。
2. **凭据**：服务用 `~/.friday/runtime/runtime-auth.json` 里的共享密钥签一个短期
   RuntimeTicket（HS256，claims 为 `iss`/`aud`/`sub`/`iat`/`exp`），与 Friday 自己的 CLI 信任
   方式相同。控制台不再提供 ticket 或 session token 输入，服务也不存储任何 Friday 凭据。
3. **项目**：Friday 只接受它自己签发的 project id（未知 id 直接返回 `project not found`）。
   `CEO_FRIDAY_RUNTIME_PROJECT_ID` 为空时，保存会调用 Friday 的 `POST /v1/projects` 申请一个
   并存下来。
4. **模型**：由 Friday 自己的配置（`~/.friday/config/runtime.yaml`）决定。CLI 启动的 runtime
   不读本服务注入的 `FRIDAY_LLM_*` 环境变量，所以控制台不提供 Friday 的 provider 配置入口。

保存这条路由时会做真实验证：CLI 必须能提供一个接受本机签发 ticket 的 runtime，否则保存被拒绝
并给出原因，不会存下一份看起来完整却不可用的配置。

**同一台机器只能有一个 Friday runtime 在跑。** `~/.friday/runtime/friday_runtime.db` 被所有
Friday runtime 共用，谁先 claim 到 operation 就由谁执行，并使用它自己的 provider 配置。历史上
本服务用 launchd 任务 `com.friday-runtime.main` 另起过一个 Friday，结果与桌面版 sidecar 互相
抢任务，错误里出现的是另一个进程的 provider 地址，排查方向完全被带偏。该 launchd 任务已停用，
不要在 CLI 管理的 runtime 之外再起第二个。

路由顺序由 `CEO_AGENT_RUNTIME_ROUTES` 的书写顺序决定，例如：

```text
codex_oauth,codex_api,claude_oauth,friday_runtime
```

一次 fallback 始终属于同一个 Agent run：当前路由失败后，Router 选择下一条已配置且健康的
路由，保留原任务、generation、proposal/revision 和 A/B 生命周期，不创建第二个 Consumer
或 Audit run。

Friday 的 turn API 只接收一条用户消息且没有 schema 字段，因此与 Claude 一样由服务把 Codex
带外获得的 developer instructions 与输出 schema 拼进这条消息；Friday 只返回最终消息，服务同样
把它整理成 runtime 事件后再交给 workload 解析器，否则所有 typed result 都会被判为缺失。Friday 的 Thread、turn、operation 和 Artifact 标识只作为该次 runtime 调用
的结果事实保存，供失败重试和 History 关联。

Friday 路由使用以下明确错误码：

| 错误码 | 含义 | 是否可重试 |
| --- | --- | --- |
| `friday_runtime_unreachable` | Friday Runtime 网络不可达、CLI 起不来或 operation 超时 | 是 |
| `friday_runtime_auth_failed` | 本机 Friday 认证记录缺失/不完整，或签出的 ticket 被拒绝 | 否，需修复本机 Friday 安装 |
| `friday_runtime_result_invalid` | Friday 返回不是约定 JSON、缺少 operation/Artifact，或结果为空 | 否，需修复契约/实现 |
| `friday_runtime_failed` | Friday operation 或其 provider 最终失败 | 是，按任务重试策略处理 |
| `friday_runtime_unavailable` | 未安装 Friday 桌面版，或已选择 Friday 路由但 adapter 未注入 | 否，需修复本机安装或服务配置 |
| `friday_runtime_project_create_failed` | 自动申请 project 失败 | 是 |

已知契约边界：Friday 只接受纯文字的最终回复。模型返回的内容如果是（或包含）JSON 对象，
Friday 会把它当成自己的结构化信封解析，找不到回复正文时报
`invalid_conversation_reply:empty_reply`。本服务的所有 workload 都要求带类型的 JSON 结果，
因此这条路由能否承接真实任务取决于 Friday 侧是否接受 JSON 回复。

健康探测使用同一 Friday HTTP 契约和配置，但只提交合成 prompt，不访问业务数据、不调用业务
工具、不执行外部写入。`tests/e2e/test_runtime_failover_live.py` 默认运行合成路由契约测试；
真实 Codex/Friday provider 探测和 fallback E2E 必须显式设置
`CEO_LIVE_RUNTIME_FAILOVER_E2E=1`，并提供真实运行时配置。这样默认测试不会消耗 provider
配额或依赖本地 Friday 服务，真实 E2E 则验证网络、认证、operation 完成和 Artifact 读取。

## 重复执行与恢复

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

candidate_executions 保存租约，candidate_action_attempts 在 provider 调用之前保存 dispatch 边界；已验证回执复用。进程中断或超时没有回执不能证明未发生效果：先读取原外部对象。确认成功继续，确认没有效果才可重试同一计划，无法消除的歧义保存 uncertain/failed，不伪造完成或人工问题。技术恢复不重新审核不变业务方案；业务事实改变则使候选失效，重新形成并审核方案。没有通用 shell 或 Agent 执行兜底；未支持的动作明确失败。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

角色同一 revision 每个 pass 最多两个技术 turn；连续六次实际技术失败后，在下一次 provider 调用前终止，容量/连接失败也计数并保留真实 source_code。唯一不计入该上限的已识别外部读取等待是 `dependency_read_unavailable` 和 `agent_context_refresh_failed`：它们让任务退避后重新读取并归还 DingTalk attempt，不形成业务结论；成功读取但无匹配数据或业务证据仍缺失不属于此错误。未开始执行的活动租约等待不产生失败 turn，技术预算与内容预算独立。

## 统一外发消息后缀

服务向人员发送的 DingTalk 或 WeChat 文本，必须先通过
`app.service_message_sender.ServiceMessageSender` 准备为持久化的
`PreparedOutboundMessage`，再交给 provider adapter。准备阶段恰好附加一次服务签名；启用反馈服务时，
还会恰好附加一组点赞/点踩链接。每个逻辑 delivery 使用稳定的 `delivery_key`，其最终正文和
feedback token 一经写入即不可变；重试、恢复和撤回复用同一份最终正文，不能再次拼接后缀。

Consumer 提案中的消息动作在 Audit 前完成这一机械准备，因此 Audit 看到的就是实际待发送文本；
这不改变 Consumer/Audit 的业务职责，也不引入新的审核回合。源码架构测试禁止业务模块直接调用
DingTalk 原始发送方法或 WeChat IPC runner。Email 不属于该文本发送策略，仍禁止 SMTP、自动回复
和任何邮件发送路径。

## 持久化与审计

Codex 原生 session JSONL 是详细审计来源，保存每个 Agent turn 的提示、工具调用、输出和
结果。SQLite 只保存恢复所需的最小状态：

- task/generation、角色、proposal revision 和父子 run 关系；
- A/B session ID 与 transcript 行范围；
- operation、target、provider result identifier（仅在 provider 返回时保存）；
- run 状态、租约和下一次可用时间；
- 服务采用的业务结果和精确去重键，原始最终输出只从原生来源读取。
- provider 原始工具事件按执行顺序在原生 session 保存；SQLite 只保留定位引用，应用层不分类命令、读写模式或工具权限。
  原始参数与工具结果以 Codex session JSONL 为详细来源。

服务不在 SQLite 复制完整 Codex transcript，也不维护另一套业务审计日志。History 页面按
session 指针读取 JSONL，并只向普通用户展示业务结果；内部角色、规划标签和原始敏感工具
输出保持折叠或脱敏。

Attempt 详情只为页面展示的历史执行代批量读取 Agent 工具事件。若任务已有更新的执行代，
当前状态投影只读取新执行代的运行状态，不再重复加载其工具事件。
详情页将这些运行的 runtime 尝试记录合并为一次按 run ID 查询，保持 run 内尝试顺序。

为避免一个详情页因同一 session 的 Consumer、重试和 runtime 记录而重复扫描全量本地
索引，`session_path_index.jsonl` 的最新记录按文件的 mtime、大小和 inode 做进程内只读缓存。
索引文件变更后下一次解析自动失效并重建该缓存；缓存只加速 session 路径发现，不改变
SQLite 投影、JSONL 原文或审计证据。

详情页判断既有外部动作时只重放已持久化的工具事件和 provider 回执；它不得为了渲染
History 再启动原生 CLI 去发现命令元数据。运行时发现失败属于执行事实，不能成为一次
只读 History 请求的延迟或副作用。

当原始 session JSONL 已被清理、不可读取或不再可用时，Codex 详情页必须明确显示
“Agent 记录不可用”，不能把它误解成业务结果丢失。页面仍可展示关联的 Attempt 索引；
关联仅限于该 session 所属 `agent_run` 的同一 task、同一 execution generation 的当前投影，
不得把同一 task 的早期 generation 全部误标为这个 session 的记录。同一 session 可能被多个
处理轮次复用，因此关联 Attempt 的数量不等于发送次数或外部动作次数。用户可从最新关联事项
查看当前状态，较早记录应保持折叠，技术状态枚举必须转换为可读标签。

## 终态语义

| 状态 | 含义 |
| --- | --- |
| processing | Consumer、审核、内容修订或已选择计划正在执行。 |
| needs_human | 当前完整问题和分支已审核通过，等待本次输入。 |
| executed / done | 系统完成全部计划且有验证回执。 |
| no_action / skipped | 已审核无需动作或选定停止，记录具体原因。 |
| failed | 技术、外部效果或内容预算最终失败，保留原因。 |

approve 是审核结论，decision_selected 是选择事实；两者均不能投影为执行成功。旧 executed/feedback_provided Audit 结果仅历史可读，不能作为新计划授权。

## 关键模块

| 模块 | 职责 |
| --- | --- |
| `app.worker.DingTalkAutoReplyWorker` | 领取任务、构造上下文、调用编排器并映射终态。 |
| `app.agent_orchestrator.AgentOrchestrator` | 在 A、B、反馈和失败重试之间推进状态机。 |
| `app.business_skills` / `app.managed_skills` | 提供八个仓库基线 Skill，并管理 SQLite 中不可变 revision、next-start config 和启动 load receipt；不参与业务路由。 |
| `app.agent_skill_usage` | 提供 Agent 执行环境所需的 Skill 读取辅助；不参与普通业务结果审核。 |
| `app.consumer_agent.ConsumerAgentRunner` | 复用兼容的 A session，业务准备和完整候选；保留任务绑定文档/报告能力，受控动作由系统执行。 |
| `app.audit_agent.AuditAgentRunner` | 复用兼容的 B session，只读审核完整候选并返回绑定 digest/revision 的结论；不执行动作或返回回执。 |
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

审批申请人是其申请陈述的权威来源。实际申请人明确说明已补充所需材料或已修正关联状态后，
Consumer 和 Audit 必须接受该陈述并从该事实继续审批；不得要求另一个业务系统再次证明，也不得
用延迟、缓存或与申请人陈述冲突的系统视图推翻申请人。只有申请人尚未说明的必填信息仍然缺失时，
才继续请求补充。申请人确认本轮要求已经完成后，该要求即结案；同一回审轮次不得新增此前未提出的
格式偏好、评分方法细节或潜在歧义要求，除非 OA 表单明确标记的必填字段确实为空。外部系统读取可
帮助理解申请，但不构成推翻申请人陈述或延迟审批的应用层证据门禁。

审批实例和当前 task 仍为 `RUNNING` 且申请人尚未说明必填信息时，Agent 必须在原审批中
评论具体缺失材料并通知实际申请人，保持审批待处理，不得让
Derek 选择。已有相同目的且已确认的评论或通知不得重复写入；新材料出现后基于最新
OA 内容重新运行 Skill。已有后续终态时只读对账。

重试复用同一个正式任务和审批实例，不创建替代审批事项。瞬态故障进入 exponential
backoff；终态失败必须说明根因、已尝试动作、provider 标识（若有）、下一步和重试条件。
DWS/OA 技术错误不得直接暴露给申请人，所有“已评论”“已通知”“已完成”都必须由 Agent
根据 provider 返回结果确认；应用层不额外要求发送 read-back 证据。

Codex Agent 可以通过 `agent_cli.read_skill` 读取 Skill；Skill 读取属于 Agent 执行环境，
不是应用层业务结果的前置 receipt。launchd 业务服务不是 Skill 可读性的前置条件。

### 已审核来源、执行进度与失败展示

Consumer 完成候选准备时，把原 trigger、消息/材料和计划涉及的 OA 原表单或文档源事实保存为 source_bindings，参与 candidate digest。System Executor 在每个尚未核验动作派发之前重新读取相同原对象，包括首次执行、同次后续动作和恢复部分执行。OA 已完成动作造成的 task 状态/操作记录变化由具名处理器核验，不作为原表单变化；真实表单、文档或上下文变化使该 review 失效，保留已核验动作回执和原人工回答，在同阶段形成新的完整候选并重审。来源读取失败属于可重试技术失败，不执行旧分支。

来源快照中的历史反馈正文只在结果检查副本中解析 Markdown 链接，并对真正的序列化来源容器递归检查。历史来源不要求当前发送者的签名、链接标签或段落格式；两个链接各自必须通过当前配置 host/path/query、评分、生成 token 和敏感值检查，并具有相同 feedback_token 与 attempt_id。旧链接中的 original_text/reply_text 是预览文案，不作为配对身份；提供方渲染后两份预览可能不同。解析保留链接原文拼写，避免 Unicode URL 规范化后无法从检查副本中移除。当前待发送正文仍要求原始严格格式及完整 query 配对；原 source_bindings、候选 digest、持久化结果与发送前来源回读比较不变，其他来源字段继续接受原有敏感值、深度和大小检查。确定性的来源结果验证失败记录 runtime_result_source_invalid、stage=result、source=service、retryable=false、session_continuable=false；不归为 CLI/provider 失败并反复恢复同一会话。

Attempt 详情的 system_execution 按 candidate、review、selection 绑定显示当前及历史阶段、声明顺序、未开始动作、失败/不确定状态和 provider 回执。只有 canonical action ledger 中核验成功的动作计为 verified；Audit approve 和用户选择都不能显示为已经执行。

失败 Reply task 的 Attention 诊断只读取本对象本 generation 的当前 run，显示原 source_code（无则 code）与明确标为“Agent 说明”的 reported_summary；缺少或坏 JSON 时保留 task error，不将 Agent 自述当作已证实的 provider 拒绝。History、Attempt 详情和 Reply attempts queue 对微信分别读取本对象当前 generation 最新 delivery：failed/send_unknown 不能被只表示候选完成的 task done 遮蔽。旧 generation 或其他对象的 delivery 不影响当前结果；这些展示不更新任务、投递或授权状态。

独立 Email 退订仍使用原有 direct executor、浏览器步骤和回执。无运行时调用者的旧 Agent continuation driver 已删除；Agent finalizer 不再从历史 Audit 声明触发退订、切换渠道或伪造执行成功。默认 developer prompt 和 OA 规则种子统一为新角色合同；既有自定义规则、managed Skill 配置与 runtime-only Skill 必须在安静的正式发布窗口中逐项发布并记录版本，部署代码本身不会覆盖它们。

原生 OA detail 明确 success=false 时，来源读取边界保留 errcode/errorCode 及 errmsg/errorMessage，按提供方错误记录原 server/code/认证属性；不把 native 失败 envelope 当成空表单或通用 Codex 错误。Consumer 失败 run 保留原始脱敏说明，System 执行技术失败保留来源和原码，不派发或制造业务人工问题。

Consumer/Audit stages (2026-10-04): immediate continuation is reserved for work whose inputs are established by the preceding verified action, such as approval followed by applicant notification. Material requests complete their own stage and wait for a later source update; their delivery receipt is not evidence that materials arrived. Audit uses the same complete production review instructions as the frozen model evaluation, and checks actual provider identities and complete effects rather than outcome labels alone.

角色 MCP 启动参数携带原领取 run 的 execution_generation，启动时与当前任务校验；旧回合不能在 generation 更换后绑定到新一代工件，运行中的既有读写仍逐次检查当前 generation。

未解除的实际 provider-risk refusal 保留 failed 与原始错误码，不重放；Consumer 不能把“拒绝后不重试”报告为 no_action 完成，Audit 应退回该终态错误。这个规则依据真实拒绝证据，不扫描正文完成短语。

### 显式维护部署（2026-10-05）

Derek 明确授权解除生产循环对部署的阻塞。`python -m app.deploy --maintenance-task <id>`
可重复声明本次允许中断的任务；普通部署仍等待空闲。维护入口在同一发布锁内核实所有在途
工作，只有指定且无候选、动作意图、回执或工具/未知事件的 Consumer 准备运行允许中断；
摘要、会议、邮件动作和其他调度领取仍会阻止维护。先冻结已核实的 Dispatcher owner 及其后代至进程树稳定，阻止新领取和子进程产生；通过现有 launchd 停止入口停止整个服务并终止存活的已冻结后代，
确认已记录的运行进程及其子进程退出，再核对业务对象、执行代、输入版本和效果。
这是受控中断，不是优雅排空。先创建完整且 integrity_check 验证的维护备份，随后调用既有
服务中断恢复方法，原任务回到 pending、保留业务身份和历史，保存 maintenance 回执；
正常空闲检查、部署备份、主线快进、构建、验证、启动和健康检查继续执行。确认进程树全灭、尚未应用代码或契约时的准备失败才启动
原服务；冻结、终止失败或 needs_manual 保持停止。生产源码仍在 finally 重新锁定。不修改业务成功状态、不手工抢租约、不重放风险拒绝。

调度器处理 Reply handler 异常时计入既有三次任务 attempts：前两次共享退避 60/120 秒，
第三次 failed，不再退回 attempts 并立即重新领取。此规则也适用于定时执行的 Reply 子类，
保留业务对象、代次和底层错误。正常调度 release 与实际 handler 失败分开。

### 原生消息 ledger 读回（2026-10-05）

`chat +chat-messages` 的当前 `im.message-list.v1` 返回顶层 messages，记录字段为
conversationId/messageId/text/createTime/sender/senderId。DwsClient 按该原生契约解码，
保留原消息及会话身份和 raw_payload；原生 atomic 查询的 result.messages 继续按其自身
provider 字段解码。通用 senderId 只与真实通讯录 profile 的 user_id/open_dingtalk_id
匹配，不猜身份类型、不因展示名相同就认定为本人。恢复读取使用相同解码，仍保留既有
准确目标/正文/时间/作者及歧义处理；解析修复不重新发送、修改候选或产生新的执行回执。


### System 与未创建角色运行的实际失败上限（2026-10-05）

定时任务在 Consumer/Audit 已完成后的 System 实际失败使用三次持久化任务 attempts，
前两次至少延后 60/120 秒，第三次 failed；重新入队不退回计数。候选、审核、动作键和
已核验回执保持不变，效果 uncertain 仍保留 uncertain 并只回读核验，不重发。
execution_claim_unavailable 是未取得执行领取的等待，至少延后 60 秒，不消费失败预算。
定时任务的来源刷新失败，以及 Worker 尚未创建角色运行的普通技术失败也保留 attempts，第三次写入 failed 历史，
保留原错误和 summary。既有授权、活跃运行及 provider 恢复等待沿用原等待机制；
真正开始后产生的角色技术失败仍计入上述六次持久化角色预算。

## 运行环境与能力说明、Settings 完整输入预览

Consumer/Audit 的稳定指令包含原请求交付、按需取证、部分事实先交付及参与者时区原则。
稳定规则参与 Consumer 契约 hash；本轮时间与任务事实不因每次变化强制创建新 session。
每次实际 route 与角色 CLI 配置完成后，运行时追加「运行环境与能力说明（Runtime Context）」：
principal、task/generation/stage/revision、原业务对象与后台扫描来源、所选模型和 thinking、
资料根目录、实际命令目录及声明工具目录。工具说明复用 role server 注册描述和最终 allowlist，
不声明认证已经成功，不由个人安装能力推定后台权限。Claude 继承服务进程 cwd；Codex 使用
显式 `--cd`，缺少明确目录的续会话标为未核实。Consumer 普通材料工作与系统注册且要求审核的
动作保持既有边界；这项说明不新增审核、授权、路由或执行政策。

日历任务按原请求和实际来源核实本人、对方及必要协调者时区，按会议日期处理夏令时、跨日和
当地时间歧义，并考虑已知工作时段/偏好。结构化来源已经提供的时区、来源和适用日期可进入
环境事实；文字材料仍由 Agent 理解，不通过姓名或关键词分支提取。机器时区、公司所在地和
钉钉源时间戳解析规则不证明对方所在地；未知时区与未知忙闲分别标注。

`Settings → Prompts → 运行输入` 显示完整服务输入，支持 Consumer/Audit、所选配置路线、
当前配置预览与历史 run。当前预览使用同一 developer/Skill/环境组装逻辑；无任务显示未绑定，
绑定任务仅复用已保存的完整该角色任务正文，并标记来源 run/时间。不用省略材料、反馈和回执的
原触发重建结果冒充完整输入；旧任务缺记录时显示不可用。历史模式读取当时保存内容，保留
run/runtime attempt/generation/revision/stage/route/model/time，不使用当前 Settings 改写历史。
用户指定的历史角色、任务或路线不匹配时返回 422。页面预览只读，不读取外部业务源或运行任务。

输入使用现有 `agent_run_events` 保存：`runtime.prompt` 是准备提交的服务输入；
`runtime.prompt.invoked` 只记录运行适配器返回，不能证明模型接收、业务完成或外部效果。
凭据使用已有脱敏函数处理并显示标记。服务输入范围不包括 CLI 自行生成的系统提示、原生工具
schema 或完整会话历史；未保存的旧 run 不重跑补齐。没有新增数据库、迁移或独立审核台账。

历史输入可按 `runtime_attempt_id` 选择同一 run 的每次路线尝试，逐次显示 prepared/invoked，
后来的准备失败不会隐藏早先已调用的输入。当前预览重新渲染当前默认 Skill 目录，仅复用明确
标记的 task override；任务正文仍标记保存来源。时区事实只投影 participant_id/timezone/
source_ref/applies_on 的字符串字段，其他来源字段不进入环境段；快照复用结构化凭据脱敏。

Prompts 的 Developer/User/Profile 各自保留 Template 与 Rendered preview 的一一对应；预览只渲染
同一份已保存模板，未保存草稿不进入预览。保存成功后重新读取服务器模板及渲染结果，避免继续
显示旧预览。后台 Consumer/Audit 的多段完整输入放在独立只读「运行输入」页签（prompt=runtime），
不再占用 Developer/User 模板的 Rendered preview；该页没有 Template 或保存操作。

Developer Prompt 只承载适用于所有任务的角色、能力、证据、系统动作、完整 Pydantic 输出契约及共同工作原则；工作人格仍注入 Consumer 与 Audit，Audit Rules 只进入 Audit。OA、日历、消息受众、OKR、招聘和文档等专项流程由当前任务选择的 Skill 承载，不再作为全任务 Developer 规则重复注入。User Prompt 是 Consumer 的完整任务模板，必须包含恰好一个 `{{task_context}}`，仅支持普通文本和这个插槽。任务来源、定时要求、stage、反馈、既有回执及 continuation 先由服务组装成完整上下文，再填入插槽；不将旧消息块模板叠加到后台任务。Workbench、独立 WeChat、纯服务命令及 Email 退订保留各自入口和显式指令。

服务组装的 Consumer/Audit Developer Prompt 不再读取或复制本机 `~/.agents/AGENT.md` 开发规则，也不注入缺失文件的占位段或旧的重读提示。业务角色、能力、输出契约和后台 Memory bootstrap 说明仍由原有代码提供；本机原生 CLI 的指令加载与开发 Agent 的 AGENTS.md 文件不在此变更范围。

后台 Consumer/Audit 使用原生 skills.config 禁用专项 Skill 的自动正文注入，避免原生加载、Developer inline 与工具读取三份重复。原生 project_doc_max_bytes=0 只关闭项目目录 AGENTS.md；全局 ~/.codex/AGENTS.md 仍由 CLI 加载，服务不修改原生 home 或全局规则文件。此设置仅适用于后台角色命令。

System Action Contracts 在 Developer 中提供由 docs/system-action-contracts.md 自动生成的 capability/operation 目录；agent_cli.read_system_action_contract(capability, operation) 返回完整规范，包含原有角色、identity、payload、target、回执和完成条件。Consumer 提出及 Audit 审查相应操作前必须读取规范；契约指纹仍包含全文。结果 schema 在 prompt 中只去除 title/default 注释，保留字段名、描述、枚举、约束及原有 Pydantic 校验。原生 output-schema 保持关闭：实际探测显示现有 RootModel 顶层 $ref 被拒绝，等价展开后 Consumer 的 oneOf 与 Audit 可选字段仍不满足原生 strict schema 限制；未通过弱化业务契约来适配。


运行输入的分段来源由同一装配函数提供：每段记录 name、source、placement（Developer/Task）及脱敏正文 characters，不重复保存分段全文，长度不含段间分隔符。runtime.prompt 保存本轮分段回执；Settings 当前公共预览使用当前角色装配，任务绑定预览明确区分当前 Developer 与保存任务来源，旧任务正文完整保留，必要的当前 Skill 提示作为独立来源段展示。历史预览只读已保存回执，缺少分段来源时标记未记录，不从当前配置猜测。日历时区解释流程位于 ceo-calendar-invite Skill，Runtime Context 仅保留显式参与者时区事实。

一次角色 invocation 只读取一次 Developer/User/Profile 正文，供组装、静态指纹与重试共用；Audit 不读取无关 User 模板。实际路线和工具命令确定后，再追加 Runtime Context。静态配置 SHA 写入现有 runtime.prompt invocation facts，仅作来源回执；Consumer 继续按 `conversation_id + route` 复用原 session，配置或 contract hash 变化不创建新会话，Audit 的独立 session 不与 Consumer 合并。

默认 User 的 Rendered preview 使用明确标记的合成完整任务，不读取业务材料；Developer/Profile 渲染同一份已保存正文。完整运行输入仍在独立只读页签，当前配置 Developer 与所选已保存历史 Task 的来源分别标注，历史模式不重新渲染。读取设置/预览不创建或覆盖配置文件。已存在模板不在读取时自动升级；部署使用 `python -m app.deploy --publish-prompt-templates`，仅在既有停止/备份窗口根据 `ci/prompt-template-release.json` 的精确旧/新 SHA 发布默认 Developer/User。本次默认 Developer 发布只接受上一版精确默认 SHA（3ccd6261291b6610aa95a4764d4fd81b7c232011f8ca7dbc52b18639703fb457），移除全局日历专项段；原规则逐字保留在实际 authored calendar Skill，User 默认和工作人格不变。自定义模板须明确迁移，不能覆盖；文件备份、发布与回退沿用 RepositoryUpdater 的 publication 协议。该发布不修改工作人格。

Prompts 设置读取先返回 Developer/User 的已保存原文；某份模板验证或渲染失败时，仅该份渲染预览为空并返回明确的 `preview_errors`，编辑器仍显示原文供修正。保存仍需通过现有验证，后台角色调用仍严格验证，不自动迁移或覆盖旧模板。

精简只移除重复或已由任务 Skill 承载的流程说明，不改变角色边界、身份与证据规则、完整效果判断、业务结果模型或 Pydantic 校验。Audit 的候选、source_bindings、revision 和 digest 保持完整；只有 provider、object_ref 与来源 value 按排序 JSON 完整相符（保留布尔/数值等 JSON 类型区别）时，Task 中重复的触发正文、raw payload、历史消息正文和材料 reference 指向候选的来源绑定，同一输入内仍能读到完整值。来源不同时两份全文保留。未结算的定时服务命令在 `scheduled_consumer.skill_materials` 冻结完整 managed revision 或 operation snapshot；未结算的通用 Scheduled Agent 在 `scheduled_agent_execution.v1` 顶层保存有序 `skill_names`、`skill_materials` 及其来源，原始 trigger 投影只保留结构化 Skill 身份事实，不再次复制正文。冷启动和续接都从同一未结算持久输入恢复；已结算输入改为来源摘要，冻结正文读取明确不可用。每一轮 Consumer 与 Audit Task 只列当前选中 Skill 的名称、极简用途和按需读取入口。保存输入声明该名称为冻结 Skill 时使用 `agent_cli.read_task_skill(name)`；材料重复、损坏或缺失由精确读取报告技术错误，不得改读当前磁盘版本。没有冻结声明的已选 Skill 使用目录中的实际授权绝对路径调用 `agent_cli.read_skill(path=...)`；Skill 正文引用的其他已安装 Skill 通过 `agent_cli.read_skill(name=...)` 精确读取，不把依赖目录预装进 Task。旧任务的显式 inline Task 约定仍在每轮保留。正文不复制进新 prompt，也不根据任务文字猜测来源。未选择 Skill 时 Task 只给 `agent_cli.read_skill()` 发现入口；该调用按需返回授权目录内的 name、用途和路径元数据，不返回正文，Agent 再按精确 name 或 path 读取。目录查询和 name 解析沿用相同授权根，越界符号链接不会进入元数据，重名明确报错。目录 frontmatter 只提取顶层 name/description，嵌套产品 metadata 不会隐藏可用 Skill。无论有无选中 Skill，显式自定义 Task 约定都继续在每轮 Task 保留；服务生成的发现文字由结构化来源标记识别，不重复注入。新运行事实记录实际入口来源与本轮 `skill_names`；未注入的旧全量目录不再作为 `skill_protocol` 保存。Email 分类、Meeting Alignment 与独立 WeChat 没有任务内读取工具，仍直接使用完整冻结正文。Runtime Context 缩短工具说明，保留原有准确 server.tool 名称和其他环境事实。无业务调用的旧 build_turn_prompt、ceo_agent_thread_prompt 及 CodexRunner 的隐含业务 Developer 默认入口已退休；底层 native 指令保留模式及真实调用者显式指令不变。

Developer 保存先以现有渲染器验证，未知变量或不可渲染内容返回具体错误且不覆盖已保存正文；该检查属于配置格式合同。

默认共同工作原则要求先识别原请求要向谁交付什么、按本轮声明入口取证、交付可核实部分并说明剩余协调责任。日历时区与候选日期偏移规则位于 `ceo-calendar-invite` Skill，只在适用任务按需读取；共同 Developer 不再全量携带该专项流程。两者都不改变已有角色、审核或执行合同。

配置回执同时记录 Developer 模板原文和渲染后共同原则正文的 SHA；相同模板引用的变量/文件/代码展开改变时，静态契约指纹也改变。静态指纹仍不是 session 身份；历史记录缺正文 SHA 时只显示未记录，不以当前值补造。

默认共同原则保留原有的部分交付与必要追问要求。审核角色、决定类型与生命周期保持原契约。
### 业务审核按完整上下文判断（2026-10-06）

Consumer/Audit 根据完整任务和对话上下文、本人职责、实际事实、接收对象、工作目的和动作后果判断候选是否合理。业务上下文不划分为可信或不可信，不另设“可信授权”或逐条消息许可声明。财务主题或群名本身不构成拒绝理由；不合适的候选必须指出具体事实缺口、受众不匹配、无依据承诺或与明确只出草稿／不发送要求的冲突。上下文不改变已配置能力，也不证明外部动作完成。邮件与微信 Skill 使用同一原则；Email 子系统现有 unsubscribe ActionPlan 和禁用 auto_reply 的范围保持原样。

Consumer 完整候选、Audit 精确审核、System 执行持久化计划及真实回执的绑定保持原样。实际 CLI／provider 认证、权限和拒绝错误仍按原诊断保留；本次规则修改不重放历史拒绝动作。固定规则在已保存自定义审核模板前注入，并通过正式部署发布邮件、微信 Skill 和默认审核规则。

### Attempt 无动作终态展示（2026-10-07）

任务的 done 表示处理周期已经结束，不能覆盖最新有效运行所绑定 Attempt 的 skipped：经审核无需动作的 Attempt 继续显示 skipped 和无需操作。判定使用现有执行代与最新有效运行身份，并按存储契约先比较 revision，再比较 Consumer/Audit 角色、该角色的重试次数和运行 ID；旧执行代或旧 revision 的 skipped 不覆盖后续任务结果。其他 done 详情也属于终态，保留既有微信待发送／重试操作对操作按钮的影响。该展示修正不修改任务、Attempt、Consumer/Audit 结果或外部执行回执，页面读取不产生业务动作。

## DingTalk 临时处理表情

普通钉钉聊天消息被服务接入处理队列后，原消息上添加服务账号的文字表情「处理中」。
日历邀请、合成 service_task 和 dry-run 不添加。成功回复的 provider 回执写入 sent_replies 后，
立即移除这个临时表情；无需回复、失败、转人工以及待处理任务换成新消息时也移除。
待处理和执行中的重试保留表情。表情的添加、移除失败不改变业务任务或回复结果。

ProcessingReaction 在现有 service_state 保存文字表情模板与每条源消息的添加/移除意图；
正常生产和消费 pass 根据现有 reply_tasks 与 sent_replies 清理已结束的源消息，
重启后继续未确认的表情操作。只操作本功能记录的源消息与服务账号的「处理中」表情。
该进度展示不改变 Consumer/Audit 生命周期、发送授权或现有业务效果检查。
## Storage retention (2026-10-09)

已完成或明确跳过的工作汇总输入不长期保留正文。`work_summary_inputs` 在业务投影提交的同一事务中将正文替换为空对象，并保留来源类型和引用、来源时间、原始 UTF-8 正文的 SHA-256 与字节数、明确的精简标记、状态、尝试次数和错误，以及既有原生运行引用。扫描器再次发现同一终态来源不会写回正文，执行中的输入也不会被重复入队覆盖；迟到的失败或重试不能重新打开已精简终态。待处理、执行中和失败输入保留准确正文以供恢复；已采纳的业务证据、业务结果和执行回执继续保存。历史终态正文通过显式存储维护清理，不在初始化中批量清理。History 展示来源元数据；依赖原始正文的历史语义导入明确报告来源不可用，不将空对象当作原文或由业务结果重建输入。

回复输入在 `done`／`skipped` 且按既有条件已结算后精简：当前任务、其输入版本与对应 Attempt 不再保留输入文字副本，原始文字和 JSON 的准确摘要、字节数、消息身份、输入版本及运行引用继续保存。Attempt 的摘要按它自己的原文计算，只有准确匹配时才绑定输入版本；业务回复、采用计划、审查决定和执行回执保持原样。执行中或待恢复的任务、待人工处理、较新输入版本、活动执行／调度租约和未结束的微信投递保留准确输入；最新运行失败且既有回执／结算条件未满足的 `done` 也保留。正常完成与回执结算使用针对该任务的精简方法，调度租约释放后在同一事务重新检查该任务，不增加历史扫描循环。精简 JSON 保留已有查询及回执校验所需的邮件动作、计划版本、邮箱／消息／线程身份、分类字段、脱敏退订入口与认证引用，以及定时运行／配置与冻结 Skill 的版本、路径和摘要引用，并明确标记不可执行。原有不可变输入校验的批量入口以原文摘要核对，普通消息仍按身份去重，均不写回副本；准确的新输入仍按既有修订／代际逻辑入队。历史重跑或冻结 Skill 读取缺少准确原文时明确不可用，不用 Attempt 文字、业务结果或当前 Skill 重建历史输入。

训练数据正文和观察明细放在数据库旁的 `*-training-data` 目录，SQLite 只保留快照摘要与训练/评估元数据。运行时按快照类型保留最新完整基线，与各类型标签水位累计一致；已启动训练的 run pin 暂时保留其选定数据，结束后清理同类型旧快照。历史摘要不能恢复已经清理的数据，读取明确报告不可用。迁移先写出并校验每种独立数据类型的最新完整快照，再移除旧正文表；运行时不从摘要重建已删除的基线。既有迁移遗漏的当前基线仅从身份、水位及摘要完全匹配的已验证原始备份显式恢复，并读回校验，不恢复历史版本集合。

完整 Agent trajectory 的唯一数据源是 Codex/Claude 原生 session 或 Friday operation。服务仅持久化服务任务状态及定位原生运行所需的引用和准确范围；调用正文、精简事件、原始最终结果、runtime result envelope、审查调用正文及派生搜索正文不再保存第二份。当前调用流和解析结果仅在内存中使用，历史详情按需读取原生记录。迁移清除历史数据库副本，原生文件缺失的过程详情明确不可用，不回退到数据库副本。服务自己提交的业务状态、待执行输入和外部动作账本继续作为服务数据保存。

独立 Meeting、Task、OKR 运行的原始决定及审查正文也只从原生执行范围按需读取。Meeting 已采用的 job 决定、Task 已应用的 projection 回执和 OKR 已采用的业务事项继续保存。会议搜索索引只保存 session/source/title 引用，完整会议来源与决定从该次原生输入/输出读取，正文和向量在有界内存缓存中计算，按原生文件及来源变化失效；既有 0.55 cosine + 0.30 BM25 评分保持不变。原生来源缺失时只有服务标题可检索，正文不可用。

服务采用的冻结业务计划保存在既有 review_candidates 中，包含服务准备的投递标识和捕获的来源事实；该计划与 Consumer 完成状态在同一事务内提交，恢复、审查和执行都使用同一已采用计划。既有 candidate_reviews 保存已采用的审查决定和修订反馈，并与 Audit 完成状态一并提交。服务准备阶段产生的失败在同一事务内记录 runtime attempt 与 Agent run 的失败状态，不把原生输出中的成功提案当成已采用计划。任务恢复不重新运行已完成的 Agent 或重新捕获历史来源。上述业务状态不作为 Agent 原始输出或过程详情的替代来源。最终任务记忆从已采用的 Consumer 计划读取，并按任务和执行代际去重；原生过程不可用不影响该项服务输入。运行流只在 RAM 中保留，事件发布使用既有 SQLite 写事务排序，终态成功提交后释放正文缓存，事务回滚保留实际观察到的运行流。运维延后与维护检查按原有条件读取准确范围内的原生证据；记录不可用不能证明没有工具活动，已证实尚未创建原生会话的失败仍按原条件处理。

业务 History、Attempt 指标和审批结果从服务已采用的 Consumer 计划、Audit 决定及既有 verified 外部动作回执投影，原生原始输出仅用于过程详情。审批回执按实际结构化操作、任务/执行代际及审批实例/已知任务标识匹配，冲突仍显示未知；不改变执行或审查规则。状态列表和恢复状态判断只读取服务状态及采用结果，不加载原生正文。工具开始记录保留实际输入，完成记录提供结果；同一原生 item 配对时，完成记录的空参数不会覆盖开始时的输入。

过程详情独立说明原生最终结果和工具过程的可用性；Friday 最终 Artifact 可读不表示工具过程也可读。可读的 Claude 记录没有受支持查看器时不提供错误的 Codex 链接。历史列表保留已采用的会议最终消息和目标，业务历史列表只读取服务采用的消息、标题与状态；原生搜索和单次运行过程详情在数据库快照关闭后按需读取。质量报告的信息性原生证据覆盖数量仅在当前内存报告/页面中展示，不写入小时状态文件，也不改变质量状态与违规规则。

Workbench 的实时文字和工具正文仅在运行时 RAM 中使用；SQLite 事件行只保存服务回放 ID、顺序和最小原生定位。工具开始与完成通过原生完成顺序定位，并按原生 item ID 配对输入与结果；只有完成记录时直接读取该条原生记录。历史文字以原生完成消息聚合回放。任务结束或进入等待确认后，在事务提交后释放正文缓存；失败与停止运行也按准确范围读取已有原生信息。最终文字不保存副本，详情按需读取 Codex/Claude 原生记录或 Friday Artifact；远端 Artifact 的读取在 SQLite 事务外进行。原生记录缺失时明确显示过程不可用，保留实际任务状态和服务附件记录。运行提供者的原始错误正文也不另存；服务保留失败状态、错误码和服务生成的公共原因，历史错误正文清除后由错误码生成展示原因。

定时配置正文存于 `scheduled_task_config_versions`，触发记录以 `snapshot_id` 引用不可变版本。数据库迁移保留运行 ID、状态和已排队输入。

模型目录在无训练进程活动时清理，保留当前模型、上一个可运行版本和最新待评估候选；其他旧制品及遗留临时文件删除。业务状态、最终结果、执行回执与小型评估记录保留。数据库删除旧正文后需要执行存储维护并压缩页才能释放文件空间。

描述优化提案保持产生它时的快照身份与引用证据不变。若提案尚未评估而源快照已被最新数据替代，提案一次性转为 `unavailable`，原因 `description_proposal_source_unavailable`；不反复启动失败的评估，也不把旧证据套到新数据上。下一次基于最新数据的训练可产生新的提案。

Status API 的 SystemHealth 使用严格类型的 native_delivery_coverage（checked、unavailable）展示原生轨迹可用性；该字段仅实时计算，不写入质量快照，也不改变质量违规判断。

已完成的邮件分类任务不长期保存分类输入正文：`email_agent_classification_tasks.status=done` 在既有租约／代际校验的完成事务内，将 `input_json` 改为明确标记的来源记录，保留原始 JSON 摘要和字节数、去除 `scheduled_consumer` 后的不可变输入摘要、稳定邮件身份、provider locator、配置版本及脱敏退订候选引用。定时配置与 Skill 只保留运行／版本／摘要引用；模型输入、邮件正文和冻结 Skill 正文不再复制。重复发现以既有不可变输入语义核对摘要，不补回正文；内容变化仍按既有错误处理。pending／running／failed 保留完整输入以便恢复，分类结果、任务状态和执行回执保持原样。历史精简由显式维护命令执行，不在初始化时扫描。`email_classifications.model_text` 仍是训练／人工标注使用的准确特征文本，与邮件原文不同，本阶段保留。

存储 schema 升级迁移业务对象映射时，只插入新键、删除已不存在的键或更新变化的任务绑定；绑定未变的 `business_object_tasks` 原行及创建／更新时间保持不变，最新任务与 OA 别名选择规则不变。

精简后的邮件来源记录同时保留业务分类 `category` 和候选来源 `action_parameters.candidate_source`，确保已完成 Attempt 详情的业务显示不变；其他参数正文不复制。日历卡片修订仍只比较呈现正文（精简后使用原文摘要），定时扫描运行 ID 的变化不会重新打开已完成任务。
