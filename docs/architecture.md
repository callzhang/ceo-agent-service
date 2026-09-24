# CEO Agent Service Architecture

本文档默认描述当前 Consumer Agent A / Audit Agent B 运行架构；明确标注为“已批准的
生命周期政策”的段落描述后续实现必须达到的目标，不表示对应代码已经切换、部署或在生产
启用。历史方案保留在 `docs/superpowers/` 中，仅用于追溯，不代表当前目标运行方式。

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
- `sent`：历史兼容名称；新任务以 `done` 表示完成，provider 发送结果保存在 trace。
- `needs_human`：现有 Skill 没有覆盖的一类规则需要人工确定；不是技术读取失败的兜底状态。每个结果必须附带面向用户的 `needs_human_reason` 和可追溯 `decision_basis`（已核验事实、适用规则、质量分值解释、未发生外部动作的依据和结论）。若理由只是技术、路由、schema、Audit 或重试失败，该投影无效并收口为 `failed`。高风险外部动作还必须提供匹配当前对象的单一 `authorization_plan`，说明动作、影响、明确排除的动作及执行后读回；详情页只展示由当前 Attempt 所指 run 的有效结构化依据。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败，并保留失败阶段和原因。

审核闭环如下：

```text
执行 Agent 生成 R0
  -> 审核 Agent 审核 R0
      -> 通过：审核 Agent 执行/发布 R0，provider 返回成功结果后完成
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

审核 Agent 只能反馈规则、观察结果和具体修改要求，不能直接改写执行 Agent 的业务正文。执行 Agent 必须基于反馈生成新 revision；原 run 不覆盖、不删除。一个任务最多允许三个内容反馈周期，基础设施失败不消耗反馈周期。反馈次数耗尽本身是自动闭环失败，不是人工决策依据；只有 Audit 自身返回信息完整且满足 `(risk=high 且 confidence<0.5)` 或 `rule_coverage<0.5` 的结构化结果时才进入 `needs_human`。

所有任务都禁止使用 `discard` 动作或写入 `discarded` 状态。无需动作的结果在 trace 记录 `no_action` 后进入 `done`；需要修正时由审核 Agent 写入 `audit_feedback`，执行 Agent 生成新 revision；处理失败使用 `failed`；无法自动解决使用 `needs_human`。

`okr_review` 使用上述闭环生成逐 KR 评审；`weekly_okr` 使用上述闭环生成管理者 OKR 进度周报。周报在分析、文档发布和群摘要获得 provider 成功结果后推进成功日期。周报调度的 `last_attempt_at` 只控制失败后的重试间隔，不代表仍在运行的实例；完整周报流程另用 SQLite 原子领取、周期续租的全局 run lease 保证单实例执行，正常结束释放，进程崩溃后由租约超时允许恢复。

OKR 评审的实时数据读取由业务 Skill 选择当前可用的 provider 能力完成。服务提供的
`app.cli read-dingteam-okr --user-id <owner-id> --period-label <period>`，该入口调用
`CEO_OKR_LIVE_SOURCE_COMMAND`（当前为 Dingteam headless source），并返回包含
`processed.objectives` 与 `processed.okrRows` 的实时载荷，是一种可用实现而非应用层命令契约。
Consumer 形成通过/不通过判断时应使用当前 OKR 数据；截图、仓库链接或重试终态不能替代实时读取，
读取失败时必须保留底层认证、浏览器启动或源端错误码。
多个评审或维护任务同时遇到缓存过期时，headless source 只允许一个调用刷新认证；其他调用在取得刷新锁后重新读取缓存并复用结果，不能因正常刷新耗时产生并发锁错误。
headless source 还会在调用 OKR API 前校验新捕获凭据的有效期；专用浏览器会话过期必须明确报告会话需要重新登录，不能误投影为“没有该 OKR 周期”。

完整状态和恢复说明见 [`docs/runtime-mechanism.md`](runtime-mechanism.md)。
错误码解释统一见 [`docs/error-catalog.md`](error-catalog.md)。

### Agent Cron 与统一 Dispatcher

用户可见的定时任务是 `任务描述 + Cron + Agent 能力（结构化 Skill 引用）+ Runtime`。任务描述是所有
定时任务共有的可读用途说明；Agent 任务另有执行提示词，服务命令不把描述伪装成 Agent prompt。Scheduler 只计算
当前时间之后的下一个触发点，并把一次触发保存为 `scheduled_task_run`；它不执行领域业务，
也不把 Consumer/Audit 的完成或失败复制回调度记录。Dispatcher 先领取该 trigger，原子创建
唯一的 `channel=scheduled` execution source，并在 trigger 上保存
`execution_kind + execution_id`。之后 Scheduled Agent Consumer 从这条不可变输入进入标准
Consumer → Audit → feedback revision 生命周期：

```text
scheduled_tasks
  -> scheduled_task_runs (trigger fact)
  -> Dispatcher: scheduled adapter
  -> reply_tasks[channel=scheduled] (immutable execution input)
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
scheduled adapter 领取 trigger 后，Dispatcher 在本进程内直接运行该命令；成功时把
`service_command + 命令名` 记为 trigger 的 execution link、保存命令返回的单行结果摘要并标记 `dispatched`，失败时 trigger
以 `failed` 结束并进入 Attention。服务命令任务不创建 synthetic scheduled reply task、agent run
或 reply_attempt；命令发现真实对象后，才由既有 reply、meeting 或 work-summary Consumer 处理。
运行记录 API 和定时任务页面展示已保存的结果摘要；旧运行记录的摘要为空。这样服务命令即使没有
创建 Agent Attempt，也能显示本次扫描数量和结果。没有新对象时不会在消息历史里留下每分钟一条的记录。`scheduled-task-options` 为每个服务
命令附带一份从服务状态计算的只读“下游”描述（通道、consumer 执行器、角色边界常量、实际加载的
Skill、consumer 要求的 Runtime 能力和路由可用性），页面据此说明命令发现的消息会被谁处理：

```text
scheduled_tasks[command]
  -> scheduled_task_runs (trigger fact)
  -> Dispatcher: scheduled adapter runs the service command in-process
  -> trigger: dispatched + service_command link, or failed + Attention
```

调度层不补跑停机期间错过的时间点；上一轮仍未终态时，本轮 trigger 记为 `skipped`，不会并行
创建第二个执行输入。手动运行会创建独立 trigger，但不移动正常计划。任务固定指定 Runtime route、
model、thinking（仅受支持时）和工作目录，不允许失败后切换其他 Runtime；managed Skill 必须绑定
精确、已加载且启用的 revision。派发前不可用时 trigger 为 `skipped`：Runtime 未配置、缺少能力、
认证暂停或 Skill revision 不可用属于配置性不可用，每次进入 Attention；provider 暂时不可用导致
的路由暂停（过载、传输断连）只体现为 run 记录和路由暂停状态，不按每次触发写 Attention。
派发后的执行可用性失败记录在 execution source，trigger 仍只表示已经派发。

统一 Consumer Dispatcher 不建立第二套业务队列表。各 Queue Adapter 直接领取现有事实来源：
scheduled trigger、scheduled execution、普通 reply、meeting、work summary、OKR review 和
DingTalk Todo outbox。统一层只处理唤醒、公平领取、租约、全局 Agent 容量和分发；领域 Consumer
继续负责自己的生命周期和外部事实。Dispatcher 的有界等待是跨进程恢复机制，不是用户 Cron，
也没有用户可编辑的 polling/settle 设置。空队列只显示零指标，不生成 run。

启动时以稳定 migration key 幂等创建八个默认任务：钉钉消息、每小时 `:30` 的钉钉近期消息恢复、
会议、微信 reader、OA、每日工作来源、每周 OKR，以及每天 `20:00`（`Asia/Shanghai`）运行的
AI 听记同步。八项全部以服务命令形式 seed；早先以 Agent 形式创建的同一
migration key 任务在启动时原地转换为命令形式，
保留名称、Cron 和时区，已删除的旧任务不动，其命令通过 Console API 不可修改。新安装创建的全部
默认任务都是**暂停**状态，由用户配好连接器后自行启用（Derek 2026-09-23）；seed 从不改变已有任务的
启用状态，包括从未被编辑过的 version 1 任务。默认的名称与 Cron 与负责人本机的现行任务一致。Lark
不创建默认 seed；其余已有任务的用户修改不会被 seed 覆盖。

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
考勤/出差、云资源六个业务大类 Skill，并在 Prompt 中记录 Derek 的个人规则。审批 Agent 只按通用
审批 Skill 和适用的 Stardust 业务 Skill 判断；背景参考文档不是
运行时规则来源，代码与默认 Prompt 都不引用它们。Consumer 按 live `processCode` 和表单事实选择适用类别；跨类别事项组合适用 Skill。
财务 Skill 的规则卡只适用于登记的财务模板，不匹配其他类别不得单独触发升级。适用业务 Skill
必须覆盖当前事项的规则条件、例外、权限和动作映射，且内容有效，
`rule_coverage` 才能为 1.0；否则低于 1.0，规则缺口进入 `needs_human`，不得自动批准或拒绝。
申请人可以补足事实或材料，Consumer 应在原审批评论明确缺口；若同时存在政策缺口，须另行
进入 `needs_human`，申请人回复不能关闭政策升级。两者在同一结果中表达：proposal 携带评论或
退回，并同时带 `needs_human_reason`、`decision_basis` 和 2--4 个选项；Audit 执行动作后任务以
`needs_human` 收口（Derek 2026-09-23）。此前结果只能二选一，总会丢掉一半。个人审批偏好只存在于定时任务 Prompt，不是
公司通用规则。

### Business Object、Task、Agent Run 与 Reply Attempt 的关系

这三个对象分属调度、执行和展示三层，不能混为一个状态：

```text
business_object（稳定业务对象，例如一条 OA 审批节点）
  ├── reply_task_inputs（不同入口收到的不可变输入）
  └── reply_task（唯一当前队列投影）
        ├── agent_runs（多次真实 Agent 执行）
        └── reply_attempt（稳定的业务结果当前投影）
```

- `business_object` 表示外部系统中的同一件业务事项。OA 使用
  `process_instance_id + task_id`；普通消息在没有更稳定身份时才回退到
  `channel + conversation_id + message_id`。同一事项从轮询、事件或人工重试进入时，
  只更新一个 `reply_task` 当前投影，每次输入追加到 `reply_task_inputs`。
- `reply_task` 负责排队、领取、重试、`execution_generation` 和 worker 所有权。
  新输入到达正在执行的 task 时，本轮结束后重新排队同一个 task，不创建并行任务。
- Provider 若原地更新同一个 DingTalk 日程卡片而继续复用消息 ID，近期消息恢复会把
  `input_revision_key` 不同的新快照追加到 `reply_task_inputs`，同时更新同一个
  `reply_task` 的当前投影和 `input_version`。只有卡片内容变化、日程仍有效且本人仍待响应时
  才开启新的 `execution_generation`；普通已读消息仍按消息 ID 去重。
- `agent_run` 表示一次实际 Consumer 或 Audit Agent 执行。重试、服务重启接管或
  新的 generation 都会产生新的 run；run 的状态、session、revision、transcript
  范围、tool event 和原始错误是不可覆盖的执行事实。
- `reply_attempt` 表示同一 trigger/channel 的业务结果当前投影。重跑更新原 attempt
  的当前状态、结果和错误，复用原 ID，不创建第二个业务 attempt。

Attempt 详情页默认展示 `reply_attempt` 的 current projection，并允许在同一 attempt
下切换查看多个底层 `agent_runs`。因此“当前结果可以被修正”与“执行历史不可抹除”
可以同时成立：列表显示最新结论，详情保留每一次 run 的真实轨迹。

#### 为什么这是系统级边界

扫描、事件、工作通知和人工反馈可能分别携带同一业务对象的新输入。如果服务仅按
`trigger_message_id` 创建任务，同一个 OA 节点、会议或消息投递会形成多个可并行执行的 task；
每个 task 又可能独立产生反馈 revision 和外发动作。即使其中一个 run 已经完成，其他历史 task
仍可能继续运行，最终表现为重复请求材料、互相矛盾的状态说明或重复的成功通知。

因此所有业务类型共同遵守以下约束：

1. 先解析稳定的 `business_object_key`，再排队；入口消息只作为 append-only input。
2. 每个业务对象只有一个 current `reply_task` 和一个 current `reply_attempt`。
3. 重试、反馈和服务恢复只追加 `agent_run`，不得创建第二个业务 attempt。
4. 只有稳定动作身份对应的 provider 成功结果可以阻止重放；应用层不根据命令、工具、Skill、
   read-only 分类、receipt 格式或所谓“未知效果”推断业务是否完成。
5. Attention、Workers 和质量门只统计 current projection；History 才展示旧 task、旧 run 和原始失败。

如果同一业务对象的 History 出现多个旧 task，模式迁移可以重建 `business_object_tasks` 的当前映射，
但不得删除旧输入、run、session、tool event、provider 结果或错误事件。当前投影修正不能被解释为
“历史从未失败过”。

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
这项约束在 typed result 解析时校验，正文准备、Audit 执行和动作键生成不再各自解释别名。

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
这组状态与普通 Agent 任务的 `pending -> running -> done` 不是同一状态机。
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
provider 文件夹”绑定并单向创建/校验目标文件夹；分类结果本身不能覆盖文件夹事实。Inbox
表示尚未分类，Spam/Trash 固定为内部 `junk`，Sent/Draft 不参加训练。`important` 不是类别，
而是独立注意信号：兼容 provider 的 Starred/Important/Flagged 信号与成熟模型信号取并集，
但 junk 始终抑制 important。分类确认只保存最终类别、训练反馈和不可变 `ActionPlan`。确定性动作清单是
`label`、`mark_read`、`archive`、`move`、`trash`；它们属于 Email 子系统，由独立
Email worker 领取、执行并通过 provider readback 验证结果。这些确定性动作
不创建 CEO Agent task，也不创建 Consumer/Audit run。`trash` 只允许可恢复的 move-to-Trash；
永久删除、IMAP `EXPUNGE` 和清空 Trash 在所有配置与执行入口都不可达。

标准 IMAP 账号使用 `UID MOVE`。若 provider 未声明 MOVE capability，但其官方协议明确规定
`UID COPY` 本身就是移动语义，账号可显式配置 `imap_move_mode=copy_as_move`；服务不会按主机名
猜测，也不会把普通 IMAP 的 COPY 当作移动。两种模式都必须用稳定 Message-ID 重新定位并确认
目标文件夹，且都不得通过 `STORE \\Deleted` 或 `EXPUNGE` 模拟移动。

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

运行时 `AgentTaskContext` 从 Email 数据源提供当前邮件和 thread 的纯文本正文。附件是
metadata-only，没有 image/content material；只投影文件名、MIME、字节大小、数量和 inline
标记，并固定 `image_paths=()`。任何组件都不得
下载、打开、OCR、解析、总结或推断附件正文。不可变 ActionPlan 是唯一动作授权；Adapter
只排队，不发送、不打开退订页面。

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

页面要求登录或 CAPTCHA 时记 `skipped_login_required` / `skipped_captcha`；页面读到了但这个服务
不操作它提供的控件时记 `skipped_no_reliable_entry`，并保留页面原文，这类结果不重试。

receipt 另外保存 `entry_url`，即这次实际打开的完整私密 URL。`entry_reference` 只是该 URL 的 sha256，邮件 HTML 正文也不落库，所以在此之前 `skipped_no_reliable_entry` 这类结论只能指出 host，无法被人工复现。写入前校验 sha256 与 `entry_reference` 一致；该值是可直接触发对外副作用的链接，只在 receipt 表和 Attempt 详情页出现，不进入 `trigger_message_json`、步骤日志或错误码。

分类器按阶段运行。每个邮箱分别保存 Agent 与模型回溯窗口，默认 30 天和 365 天。尚无上线模型时，
定时扫描只把 Agent 窗口内符合该账户“仅未读/全部”设置的 Inbox/未绑定来源邮件放入 Agent 队列。
模型上线后，定时扫描改为只由模型优先接管模型窗口内全部尚无稳定记录的已读和未读邮件，不再按
日期保留近期邮件给 Agent，也不在同一轮运行 Agent 扫描。模型高置信度结果沿现有不可变 ActionPlan 和
provider action 队列整理邮件；低置信度、`others` 或未晋升类别只保存为 `pending_feedback`，进入
Console“待确认”并等待人工标注，不回退给 Agent，也不创建动作计划。模型/Embedding 技术失败则
保留邮件下轮重试，不伪装成待确认。历史批处理允许 120 秒的 Embedding 请求期限；实时调用仍保持
2 秒期限，历史回填不会因为实时低延迟预算而永久卡在同一封邮件。分类过程不擅自改变邮件原有已读状态。训练只在冻结的
provider-folder snapshot 上离线、分阶段执行，shadow 模型不进入实时扫描。只有连续两个兼容的完整模型版本都满足全部类别、important、样本组
和系统性错误门槛，并且来自两个先后冻结、digest 不同且 folder/important 标签水位与独立评估证据
确实前进的 snapshot，才原子晋升整个模型。同一 snapshot 改 model ID 或训练时间不能形成连续证据。
旧的显式实时调用接口仍保留顺序 fallback 契约，但定时收信与历史回填统一使用上述模式互斥路由；
模型上线后，不确定结果进入待确认而不是调用 Agent。

历史和实时移动都先读取 provider 当前状态，写入后再按新 locator 回读；important flag 在移动后的
locator 上执行。用户随后在邮箱中移动邮件时，下一份冻结 snapshot 直接采用新文件夹标签。
`junk` 的退订候选由代码从标准 header/body 链接中发现，Agent 只在已审计的退订任务里决定和执行
后续网页步骤；成功或无需继续后再移动到系统 Trash。连接邮箱的邮件 OTP 只在站点、收件人和有限
时间窗同时匹配时临时读取；普通 CAPTCHA 可在隔离 profile 中尝试，密码/MFA/CAPTCHA 无法完成时
保存有界 continuation 并转人工接管，不持久化 OTP、cookie、完整 URL 或浏览器秘密。

Email Console 的 learning、model-version、folder-binding 和 classification-detail API 只投影版本、
门槛、计数、时延、fallback、动作/readback 与 continuation 状态。它们不返回正文、附件字节、
embedding、OTP、完整退订 URL 或私密浏览器数据。分类确认不会创建通用 CEO task；系统在任何阶段
都不发送、回复或草拟 Email。常驻 classifier runtime 将有界的阶段耗时、cache-hit、结果和受控
fallback code 写入 `EmailStore`；Web 进程只读取这些跨进程聚合，不依赖注入 worker 的内存对象，
也不保存请求文本、向量或原始错误理由。每次 provider 扫描还会更新独立的“最新观察”投影；
classification detail 从该投影读取当前文件夹事实，不从冻结训练 snapshot 推断，也不在 API 请求中
访问邮箱网络。训练相关字段明确使用 snapshot 命名，避免把历史冻结状态误称为当前状态。

Email Console 的“模型训练”页读取后端统一计算的晋升资格。默认门槛为 Macro F1 ≥ 0.95、
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
SQLite 在线备份并只保留一个最新快照（同时清理 SQLite sidecar），执行依赖同步和测试，重启
launchd 后验证新 PID、HTTP 健康与 Store 可读性。
升级后验证失败时只对本次安装的精确 commit 做 compare-and-swap 回滚；无法证明仓库仍归本次
操作所有时进入 `needs_manual`，不执行破坏性 Git 操作。MCP 配置不由该流程探测、禁用或覆盖，
直接沿用用户当前 Codex 配置。

### 会议投递目标

会议跟进先由内容决定范围，日历只用于证明参会名单。客户、项目、产品、需求、交付、排期、
测试、部署、客户沟通或跨团队行动均是业务内容：Meeting Alignment Agent 必须从业务承接证据中
搜索、排序并自行选择最强的可发送群。多个合理群不是人工选择条件；每场会议都必须生成并发送
一条总结，不能返回 `no_action`。业务群发现或投递失败先按群候选重试；当权威会话信息证明
所选群不可发送、且会议上下文已有稳定的日历组织者 `user_id` 或 `open_dingtalk_id` 时，
服务把同一总结私聊给该组织者，不按姓名搜索或猜测身份。
群标题相似、部分参会人重合或近期活跃本身不构成业务承接证据。
找群先核对会议材料里明确提及的讨论群，再从结论、行动和负责人提炼主题，分别以原文中文
业务词、英文术语、会议标题和核心议题查群及群内消息。预置候选或首次搜索零命中不等于穷尽；
候选须核对近期同工作线讨论、行动承接人、受众和可发送性，并在决策中说明来源与业务承接关系。
多个合理群按本次议题和行动归属排序，不能用宽泛群凑数，也不能把敏感内容送给无授权受众。

只有个人、非业务内容，且完整日历名单明确证明 Derek 与另一位参会人两人参会时，才允许 direct
到该另一位参会人。逐字稿中的发言人只能证明发言，不能证明完整名单或两人会议。所选业务群被
权威会话信息证明不可发送时，服务重试群验证、使用 Agent 的下一证据候选群，或使用稳定日历
组织者 direct fallback；不会回退到任意参会人。
如果业务群不可发送且日历组织者没有稳定的 `user_id` 或 `open_dingtalk_id`，服务保留本次已生成的完整
决策，将会议任务收口为 `needs_human` 并写明 `meeting_identity` 原因；不发送、不猜测身份，也不把这条
完整决策投影成空结果。
会议没有实质分歧时，仍发送简短的已确认事项和下一步；只有已确认删除的听记因原始内容
永久不可读取而记录为 `no_action`，其他听记读取、群发现或投递问题仍必须可重试或明确失败。

`audience_scope` 是当前 Meeting Alignment Agent 输出的必填字段。投递器读取早于该字段的
持久化 `send` 决策时，只能从其已保存的 `target.kind` 规范化一次：`group` 对应 `business`，
`direct` 对应 `personal`，并立即写回规范 JSON；缺少目标或其他字段不合规的记录仍按失败处理。
该规范化不重新分析会议、不改变目标，也不重发已有送达记录。

## History 语义与无效入口边界

History 是任务和执行记录的单一展示入口。同一个任务不得被拆成多条当前队列记录；
队列任务与执行记录都必须保留各自真实状态。筛选条件的业务含义固定如下：

- `status` 只筛选执行状态；不再使用含糊的 `type` 名称。
- `task_type` 只筛选任务类型；多选通过重复的 `task_type` 参数表达，不再使用
  `object_type` 名称。
- 任务类型包括 `replay`、`wechat`、`approval`、`task`、`meeting` 和
  `okr_review`。OKR 评审优先依据明确的 `action='okr_review'` 识别，其次依据与
  `okr_review_requests` 的会话和触发消息关联识别；同一执行记录只能归入一个类型。
- `reply_tasks` 的 `pending` 和 `processing` 是当前队列任务，必须在 History 中按真实
  状态展示、筛选和计数；它们不属于 Attention。
- History 在页面可见时每十秒读取当前快照；已经进入终态的队列任务不得因页面保持打开而继续
  显示为 `processing`。
- 24 小时图表统计的是该小时内发生的历史事件，不是当前队列的状态计数。重试和执行开始须以
  独立事件标签呈现，不能使用 `processing` 改写旧失败记录；实时 `processing` 只由当前队列
  列表和状态页统计。
- `okr_review_requests` 的队列状态不能覆盖对应执行记录的 History 状态。若同一对象已有
  当前队列记录，列表以这条队列记录承载 `pending` 或 `processing`，不再制造重复的当前状态行。

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

- **Consumer Agent A** 理解业务、读取当前事实并提出精确候选；它与用户 Agent 继承相同的
  runtime 能力，但按角色和结果协议不发布候选中的消息、审批等外部动作。
- **Audit Agent B** 独立审阅候选，并负责在 service 生命周期中正式发布 accepted action；
  provider 命令、工具和结果判断属于 Agent/runtime，不由应用层再次审核。
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

### Schema 初始化竞争

worker 与审计页面会各自打开 SQLite。它们先取得同一个初始化文件锁，再检查 schema 版本、必要表、
必要列和定时任务运行快照字段（包括 `scheduled_tasks.command`、`command` 快照字段）。检查遇到短暂
`locked` 或 `busy` 时会在该锁内等待后复查；只有稳定确认 schema 过期、缺表、缺列或快照需要回填才
执行迁移。这样高负载写入不会被误判为 schema 缺失，也不会让审计页面在请求期间执行 DDL；即使旧库
的版本号已经提前写成当前版本，也会先完成定时任务 schema 的补齐再启动调度。

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
  -> B reviews and executes through its normal runtime capabilities
  -> service persists the existing run/attempt/provider identifier
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
材料是否相关、是否需要继续展开以及它们支持什么结论，都由 A 判断；B 在执行前独立复核。

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

### Task-first 工作跟踪（Task 6 与 Task 7；代码未部署）

Task-first 的正式 Project 注册表和当前 Task 状态以最近一次确认的正式周报为
首要来源，尤其是项目管理部或管理层周报中明确列出的项目、负责人、目标、
DDL、状态和下周任务。周报即使汇总会议纪要与项目沟通记录，也必须保留周报
文档引用和统计周期。会议纪要、逐字稿或已确认会议行动项是尚未进入周报的
新决策或变更的次级权威来源。聊天或消息只能补充负责人、状态、链接等上下文，
不能单独创建正式 Project，也不能自行覆盖周报中明确的字段；来源冲突时先取
最新明确周报字段，再取最新确认的会议决策，并保留精确来源引用。

Task Agent 的结构化结果是 `task_decisions` 列表，同一来源可以得到 0 到多个决定。每个非 skip 项必须引用原始来源中的精确摘录与来源引用；Task、负责人、日期和承诺不得由 Agent 自行补造。正式 Task 必须有来源支持的明确负责人；正式指派先记为 `assigned_unaccepted`。只有负责人本人对唯一现存 Task 的明确接受证据才能进入 `accepted`。外部 TODO 的存在只证明有一条外部记录，不证明负责人接受。
更新既有 Task 时，Agent 可用本轮来源证据修订标题或描述；内容变更、新信号证据链接及 before/after 事件原子提交。纯内容更新记为 `details_changed`，内容与其他 Task 字段同时更新记为 `fields_changed`。重复回放不重复追加事件；只有新证据、没有字段实际变化的更新会被拒绝。

Task Agent 使用一个统一的 `TaskAgentDecision` 生命周期契约：同一来源可同时产生 0..N 个新建/更新 Task 决定，以及适用的既有 TODO 完成或 follow-up 状态转换。completion 操作分别使用 envelope 顶层类型化的 `todo_changes` 和 `follow_up_changes` 字段，不嵌套在单个 `task_decisions` 内。共享 work-summary consumer 仍按精确 source type 选择上下文准备和服务端应用操作，但不启动另一个 Agent，也不使用第二套结果协议。`todo_completion_check` 只可关闭输入明确链接的既有 TODO；证据候选只更新其自身状态；follow-up completion/repair 只可转换输入明确链接的既有 follow-up。没有完成证据时仍记录 `search_trace` 与检查摘要并保持 TODO 开放。无效身份/操作使输入和 run 失败且不提交领域变化；Task 转换、TODO 本地完成、关联 follow-up 完成、候选状态、work-summary input/run 终态在同一事务中提交。事务提交后，外部 TODO 完成与类型化 Task 完成都按受影响 Task 重算当前 Attention 成员；完成的 Task 从成员列表退出，但不会仅凭读取或完成动作把 Attention 标成已解决。

completion apply 会在事务中校验队列 source_type/ref 与持久化 TODO/project/follow-up/candidate 绑定；trace source_kind、来源时间、服务记录的检索时间、来源数和可观察工具调用数按 Work Item 的 search_policy 校验，并要求 source locator 能在本次运行的工具结果中匹配。candidate 来源时间须与持久化候选一致。receipt 不构成外部内容真实性的独立证明；`completed_at` 只验证可解析且不晚于检查时间，并不证明该时间来自来源正文。当前 audit event 不含可信的 search-vs-raw-read 分类，因此 `max_raw_reads` 没有独立运行时计数；这是当前 prompt/runtime 能力限制，超出已批准范围，不是 Task 6 发布阻断。Task Agent prompt 明确要求只读发现，不得通过 CLI/API/MCP 工具创建、更新、删除、发送或完成外部记录；这是 prompt-only 的 best-effort 指引，不是运行时权限边界，Codex route 仍没有 per-turn MCP 写工具 allowlist。外部完成只由现有 outbox 同步。

普通 Task 提取和 TODO/follow-up 完成检查共用稳定的 `task-agent:work-tracking:v1` 会话范围；每个 Work Item 仍有独立 `workload_key`、run 和运行记录。会话按 runtime route 分开保存，同一路由的后续 Task Agent 输入会续接该路由的 session。`process-work-items` 在领取输入前持有共享 SQLite session lock，并在处理期间续租；锁被其他进程占用时不领取、不增加输入尝试次数。锁续租失败会在领域事务提交前终止本轮并安排输入重试。此前 run 使用的 `task:<run_id>` 会话记录保留不迁移。Agent 每轮以当前 Work Item、当前检索状态和新来源证据作判断，会话历史只作背景；Codex CLI 的 context compaction 由 Codex 原生机制管理，不是事实存储，也不替代当前来源证据。
若 Codex 明确报告 context compaction 自身超过模型窗口，当前 run 会在同一路由清除该 route 的共享 session 指针并用 fresh session 重试一次；若新 session 仍超限，则进入既有 runtime route fallback，不循环新建 session。其他 session 错误不触发该恢复路径。

定时完成检查还会从语义存储选择开放的正式 Business Task，不要求 Task 已有 Project；Work Item 携带链接的来源信号、类型化日期、Task follow-up 和外部 TODO 上下文，并按有界批次逐日入队。关闭来源上的 Task 后，Attention 成员在领域提交后更新；同一关注项中仍开放的兄弟 Task 继续保留。

这是代码分支的运行时契约，不等于服务已部署或发布。Task 6 不得单独部署；Task 7 在 Task 键控的执行表中实现 TODO 镜像、follow-up、回执和完成转换。只有正式、未关闭、来源支持明确负责人且已接受承诺，并有来源支持的可解析 `committed_deadline_at` 的 Task 才排入 TODO 创建 outbox；不得由请求期限、估算或下次检查日期推算镜像期限。创建后保存外部 ID 并读回；若首次读回失败但 provider ID 已知，后续状态拉取会按该 ID 重新读回并收口链接/outbox；若创建结果未知且没有 ID，不会以新的操作键再次创建。同一 Task 有未结清创建 intent 时不再排第二个创建；已知的 `failed` 操作使用有界退避，未到 `next_attempt_at` 时不被再次领取。缺少可信 producer 提供的 `external_task_id` 时也不猜测该 ID。

新 follow-up 只有在已链接来源信号提供精确会话目标和可解析 `next_check_at` 时才创建；问题可概括 Task 状态，但目标与检查时间不能从截止日或历史会话推断。群聊在精确来源群中发送并提及 Task 的证据化负责人；单聊只发送给来源明确指定的负责人账号，来源会话 ID 保留作核对，不把消息误发给可能不是任务负责人的原发件人。发送前先读回已链接的外部 TODO；若其已完成，则关闭精确绑定的 Task/follow-up、保存 provider 状态并释放未发送 claim，不发消息。发送尝试以 Task follow-up 的 revision、租约和幂等 UUID 留存，成功记录回执；发送中断或租约过期的结果标为未知并排入 Task Agent 核查，不自动重发。外部 TODO 完成仅关闭其明确链接的 Task 和 follow-up，不触及同一聚类的兄弟 Task。历史 `work_todos`、`follow_up_drafts` 和旧 outbox 仍可读取；旧 follow-up 定时发送暂保留以处理未迁移记录，Task 8 导入与生产切换之前不能将其误称为新 Task 写入路径。

系统首先在一个语义事务中写入来源信号、候选或正式 Task、证据、类型化日期、显式 Task 转换和关系/锚点/Project 候选提议；随后从已提交的语义事实重算关注投影。候选提升、接受、字段更正和同一事项合并使用不同转换；模型相似度仅用于提示，不授权身份转换。`created_at` 是系统记录时间；`assigned_at` 仅从明确正式指派的可信源时间戳派生，日期值必须与精确摘录中的可解析日期一致。周级或不可解析日期短语只留在关联的来源信号中，不生成 typed date fact 或猜测时间戳。估算由可信来源发言人署名，抽取 Agent 不冒充估算者；Task 6 的 `next_check_at` 由 Agent 署名但只记录来源明确给出的检查日期，不安排 cadence、不将 due date 转成检查时间；其他日期要求可识别来源行为人。当前 AI Minutes producer 没有可信 speaker→identity 映射，故不把转述者或模型填写的人作为日期行为人，相关日期暂不记录。指派日、请求/外部/承诺 DDL、估算和下次检查分别保存为独立类型。Project 只能引用注册表中的正式对象；Project 候选和锚点匹配不会自动创建正式 Project。

需关注必须同时有已确认的业务锚点、相关 Task、已链接来源信号、明确 CEO action 和当前来源的精确 trigger 摘录。trigger 类型和原因是 Agent 对来源的语义分类并写入 Attention provenance；它不等于机器独立证明其重大性，系统不做关键词重大性推断。提交后还要按所有受影响 Task 重算当前关注成员，完成、取消、不相关及合并都会更新/移除成员；投影失败不回滚已提交 Task。仅相关、已接受、正常进度或临近日期不足以进入关注。Task Agent 不再把 `work_projects` / `work_todos` / `work_updates` 当新语义事实的双写目标；外部 TODO 镜像走 Task 7 的独立 Task 键控 outbox，Task 6 单独不得部署。

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

A 的身份是 Derek 本人，而不是旁观审核员。A 会：

1. 复用同一业务对话的 Codex session，理解此前已经确认的事实。
2. 动态发现并读取适用的业务 Skill，再读取完成任务所需的操作 Skill。
3. 以读取和判断为目的使用安装用户已有的 CLI/MCP，按需读取原始消息和材料引用，不依赖
   service 预读或解释正文。
4. 返回结构化候选，其中包含目标、动作、收件人/对象、正文或参数和必要的事实引用。
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

A 和 B 的结果统一携带 `risk`、`confidence`、`rule_coverage`、`information_completeness`；
后三者严格为 0--1。先看信息完整度：低于 `0.5` 只产生普通 proposal 或一个具体问题的
ask-back，继续现有 Audit/send 链路；ask-back 不落新的 outcome，也不计 `needs_human`。否则，
仅当 `(risk=high 且 confidence<0.5)` 或 `rule_coverage<0.5` 时进入 `needs_human`，并给出 2--4
个互斥可执行的规则/Skill 选项；每个选项必须包含唯一稳定的 `key`、显示用 `label`、可执行的
`instruction` 和 `consequence`/影响；其余由 Skill 自主完成。one-time 与 Skill update 可同时作为
反馈选择，复用同一业务对象、attempt 和兼容 session，产生新 revision，不新建 session。
技术、provider、receipt、读取、路由、schema、Audit、retry failure 永远为 `failed`；领域
`authorization_required` 不泛化为人工升级；只有精确的通用错误码、匹配当前动作的授权计划和统一质量门槛同时成立，才可形成结构化规则决策。
provider 返回 `confirmation_required` 同样属于运行时失败边界：外部动作尚未执行，服务必须保留具体
错误并落为 `failed`，不得创建泛化的“确认执行外部操作/停止当前事项”选项。

新 wire 四字段必填且严格校验。当前投影不兼容旧的“状态字符串 + 服务生成按钮”逻辑：
`needs_human` 必须能追溯到完整 `final_result_json`，字段缺失、非法值、outer outcome mismatch、
无 Agent run、选项不完整或 `error_retryable` 为真都 fail-closed 为 `failed`；`error_authorization_required`
只有精确通用码 `authorization_required`、匹配的单一授权计划和同一质量门槛全部成立才有效。
保留原始历史 run/audit，不改写其内容。
服务启动时幂等修复这种 current projection，同时清空服务生成的选项。Quality gate/Attention
只统计修复后的 current latest projection；reviewed、historical、pending recovery 排除。

### Audit Agent B

B 不是 Derek 的第二个写作分身，而是独立审计与执行者。B 会：

1. 根据候选内容和当前上下文独立判断适用的业务规则。
2. 重新读取执行前的实时事实和 Audit Rules。
3. 检查 A 的候选是否有事实依据、目标准确、内容最小、权限合适且符合当前规则。
4. 候选合格时按原样执行，并返回 provider 的执行结果。
5. 业务含义需要变化时返回具体反馈，由 A 生成新 revision；B 不自行改写候选。
6. 外部动作中断时由下一次 Agent turn 按当前业务 Skill 读取目标状态并决定是否继续；服务不创建专门的恢复回合。

同一任务的 B session 在 provider session 存在且可访问时继续复用；Skill/契约版本更新和 revision 前进不会单独强制创建新 session。只有 session 不存在或认证上下文失效时才创建新 session。

## 会话与反馈周期

- 每个 `conversation_id` 对应一个长期 A session；同一业务对话的新消息通过
  `codex exec resume` 进入该 session。
- 同一任务的候选 revision 优先继续兼容的 B session；revision 前进不等于创建新 session。
- B 的 `feedback_provided` 会通过持久化反馈消息送回 A；`revision_required` 只作为历史输入术语映射。
- 一个任务最多允许三个内容反馈周期。基础设施重试不消耗内容反馈周期。
- A session 缺失或损坏时才创建新的会话；服务不会为每条消息无条件创建新 A session。

当 A 在外部写入之前因进程、解析或会话错误失败时，重试会在同一 revision 创建新的
Consumer turn。失败 turn、其 session 标识、事件和错误保持不可变，供 History 审计；新
turn 可以复用仍有效的对话 session，或在该 session 已失效时安全创建新会话。Audit B 始终
绑定该 revision 最新成功的 A turn，避免覆盖失败记录或把旧 session 标识写入新会话。

同一对话在任一时刻只允许一个 A turn 写入会话 JSONL。会话锁只保护本地 transcript 的
一致性，不代表业务消息被丢弃；新任务保留在持久队列中等待该 turn 完成。

### 任务提取与 follow-up 是一个生命周期

`ceo-work-tracking` 把同一事项从识别到关闭作为一个流程：从对话、会议或材料中提取有证据的
Task，按需链接正式 Project，记录负责人及类型化日期。只有符合承诺与期限条件的 Task 才通过
outbox 镜像为钉钉 TODO；只有来源明确给出下次检查时间与目标会话才创建 follow-up。发送由
Task 键控的 follow-up worker 负责，并记录 revision、租约、幂等 ID 与 provider 回执；
结果未知先进入核查而不是自动重发。读取后续回复或外部 TODO 状态后，仅凭明确完成证据关闭
对应 Task 与其 follow-up。旧 Project/TODO/follow-up 记录保留在历史边界，等待 Task 8 导入。

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

Agent 不得把嵌套 shell 里的 `codex mcp list` 或 `codex exec` 结果当成当前父 Agent session 的
MCP 注入证明。需要 Xiaoqing、Memory、Exa、Lark 等 MCP 时，Consumer/Audit 必须直接调用当前
session 中的对应 MCP 工具；只有直接工具调用或 provider 操作失败，才形成可持久化依赖失败。
`<server>_mcp_not_injected` 这种自报注入状态是 wire result 契约错误，进入修正轮而不是任务终态。

### Agent Runtime 路由模型

`CEO_AGENT_RUNTIME_ROUTES` 是一个有序的路由名列表，**顺序就是故障切换顺序**：前一条不可用时
Router 取下一条已配置且健康的路由。列表里出现的名字分两类：

- **内置路由**：`codex_oauth`、`codex_api`、`claude_oauth`、`claude_api`、`friday_runtime`
  （常量 `SUPPORTED_RUNTIME_ROUTES`）。它们使用各自固定的环境变量，其中有些键被别的功能共用，
  例如 `CEO_CODEX_API_BASE_URL` 也被邮件分类器读取。
- **新增路由**：列表里任何其它名字（小写字母、数字、下划线，字母开头）都是运维自己加的路由，
  由它自己名下的 `CEO_RUNTIME_<大写名>_KIND` / `_BASE_URL` / `_MODEL` / `_API_KEY` 描述。
  `KIND` 取 `codex_oauth`、`codex_api`、`claude_oauth`、`claude_api` 之一
  （常量 `ADDED_ROUTE_KINDS`）；OAuth 两种复用本机 CLI 登录，只需要模型、不需要 Token
  （`ADDED_ROUTE_KINDS_WITH_KEY` 之外）。因此同一种 provider 可以配置多条，各自指向不同地址
  和模型——`RuntimeRoute.base_url` 就是为此存在，Codex adapter 用的是**该路由自己的**地址，
  而不是某个全局配置。

控制台的 Settings / Agent Runtime 直接编辑这份列表：拖动卡片改顺序，开关决定该路由是否在列表
里，删除则把名字记进 `CEO_AGENT_RUNTIME_HIDDEN_ROUTES` 并只清除该路由**独有**的凭据（共用的
设置保留）。被删除的内置路由可以从「新增 runtime」恢复，恢复后仍用原来的固定键名。

健康探测（`app/agent_runtime_probe.py`）与真实 turn 走同一条 provider 路径，因此超时按一条真实
turn 的长度给：`PROBE_TOTAL_TIMEOUT_SECONDS` 和 `PROBE_IDLE_TIMEOUT_SECONDS` 都是 300 秒，
服务读取这两个常量作为 `CEO_RUNTIME_PROBE_TIMEOUT_SECONDS` / `CEO_RUNTIME_PROBE_IDLE_TIMEOUT_SECONDS`
的默认值——两处不同的默认值曾把一条健康的路由判成不可达。`probe-agent-runtimes --route` 接受
任何已配置的路由名（含新增路由），并且**不采纳其它进程的快照**：显式探测必须给出本进程自己的
结果，否则运维看到的可能是服务几十秒前的结论。

### Claude Runtime 路由

Claude CLI 有两条并列路由，共用 `CEO_CLAUDE_MODEL`（默认 `sonnet`）和
`CEO_CLAUDE_MODEL_REASONING_EFFORT`（默认 `medium`，取值 `low`/`medium`/`high`/`xhigh`，
作为 `--effort` 传给 CLI）。调度任务上的 thinking 选项会按次覆盖该默认值，与 Codex 的
`reasoning_effort` 使用同一套取值。

| 路由 | 凭据 | 命令差异 |
| --- | --- | --- |
| `claude_oauth` | 本机 `claude` CLI 的登录态（订阅） | 不使用 `--bare`，不设置 `ANTHROPIC_API_KEY`，`CLAUDE_CONFIG_DIR` 不覆盖 |
| `claude_api` | `CEO_CLAUDE_API_KEY` | 使用 `--bare`，凭据只进子进程环境，`CLAUDE_CONFIG_DIR` 同样不覆盖 |

两条路由都不覆盖 `CLAUDE_CONFIG_DIR`，直接复用调用方真实的 `~/.claude`：`--bare` 已经
保证 `claude_api` 的认证只能来自 `ANTHROPIC_API_KEY`（不会读到 `~/.claude` 里缓存的 OAuth
凭据），所以没有必要为凭据隔离单独换一个配置目录。每次调用要落地的
`ceo-agent-service-settings-<uuid>.json` / `ceo-agent-service-mcp-<uuid>.json` 文件（文件名
按 uuid 区分、带 `ceo-agent-service-` 前缀跟真实配置区分开，不需要目录级隔离）直接写在
`~/.claude/` 根目录下，跟 Codex 直接写在 `~/.codex` 根目录（`auth.json`、`sessions/` 等）
是同一个做法——没有专门建子目录，也就没有目录生命周期要管理。调用结束后单个文件由
`finish_invocation` 清理，不需要任何清理任务。

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

`friday_runtime` 是与 `codex_oauth`、`codex_api`、`claude_oauth` 和 `claude_api` 并列的内置
Agent Runtime 路由。它通过 Friday Runtime 的 HTTP 接口创建一个 Thread、提交一个 turn、等待
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

### 稳定业务动作去重

重复保护绑定业务对象、动作身份、operation 和目标，不绑定 run、generation 或 revision。
反馈 revision 若仍表达同一预期外部结果，必须复用 `action_identity` 并直接复用已经成功的
provider 结果；只有预期结果、目标或用途真的变化时才产生新的动作身份。

OA pending 扫描会先读取当前审批记录。只有最新有效记录来自其他参与者时才生成 review
任务；如果 Derek 已在该外部更新之后完成评论、审批或其他处理，扫描不再把同一审批重新入队。
同一个业务 `reply_attempt` 是稳定的当前投影：重跑会在原 attempt 上更新当前状态、结果和错误，
不会再创建第二个业务 attempt。页面可以在该 attempt 下切换查看多个底层 `agent_runs`；这些
run、session、runtime attempt、tool event 和原始失败事件仍是 append-only 执行事实，不能被覆盖。
历史中的原始失败仍可展开查看，但列表默认展示 current projection。
模式升级可以规范化业务对象键并重建 `business_object_tasks` 当前映射，但不能删除或改写旧
task、agent run、session、tool event、发送记录或 provider 回执。

### Agent 失败重试

Consumer 或 Audit 的运行、依赖、解析和外部系统错误统一进入 `failed`，由 Agent 在下一次 turn 中按当前
业务 Skill 读取必要事实并决定是否重试。服务不区分“有副作用失败”和“无副作用失败”，也不维护专门的
独立核对队列。重试仍绑定原任务；已持久化的 `external_action_key` 成功结果由服务机械复用，
无需 Agent 再次执行同一动作。没有成功结果的动作由下一轮按当前业务状态继续处理。

服务重启后，仍有有效租约的 run 不会被 stale recovery 抢占；租约过期且没有活动进程的
run 才能被持久队列恢复。

两类运行时失败有明确的结构化处理，而不是落入通用重试：

- **唯一的 fallback 决策**：Agent turn（`app/agent_turn_runner.py`）与其他所有 workload
  （`app/agent_runtime_router.py` 的 `RoutedCodexExecution`）只有一条 fallback 路径：某次 runtime attempt
  失败后，两个执行循环都调用 `app.runtime_fallback.plan_runtime_fallback` 决定是否暂停路由、是否等待、
  下一次在哪条路由执行；执行循环只负责按决策记录各自的 session、transcript 与回执证据。
  两个循环也使用同一组 runtime adapter（Codex、Claude、Friday），任何 workload 都能到达任何已配置路由。
- **模型过载（429）**：provider 报告已满（`server_overloaded`、"Selected model is at capacity"、上游 429/5xx
  包装成的 "high demand"）时，adapter 归为 `capacity` 类且可在同路由重试的 `codex_provider_overloaded`。
  同一路由按共享指数退避（10 秒、20 秒、40 秒）重试最多 `CAPACITY_RETRIES_ON_SAME_ROUTE`（3）次；
  重试期间不暂停该路由，其他 workload 照常使用。3 次重试仍失败才暂停该路由并切到下一条已配置路由。
  计数只看该路由最近一段连续的容量失败，中间出现其他失败或成功即重新计数。
  `codex_provider_capacity_exhausted`（额度用完）不会在等待中恢复，不在同路由重试，直接暂停并切换。
  所有路由都过载时，任务按 provider 恢复等待延期重试，不进入终态失败。
- **路由暂不可用**：所有路由都不可用时，失败码由路由层给出的结构化原因决定，而不是从
  显示字符串里找子串：探针快照缺失/过期或路由因非认证故障被暂停 → `runtime_provider_unreachable`
  （延期重试）；全部路由缺能力 → `runtime_capability_missing`；仅认证类暂停 →
  `runtime_provider_auth_failed`。Email 任务对 `failed_retryable` 的编排结果与 DingTalk worker
  一致：按退避时间延期重入队，不再当作终态失败。
  路由层的其他工作负载（任务 Agent、会议、邮件分类、workbench）在选路阶段就发现所有路由暂停/未探测时，
  同样按可重试的外部依赖延期，而不是判为执行失败。这种“没有进入任何路由”的状态由路由层以
  `runtime_unavailable` 标记；工作项 worker 据此只延期、不消耗自身的有限重试次数，也不记录逐条
  Service error（路由暂停本身就是信号）。
- **Email worker 的文件描述符**：`EmailStore._connect` 是会关闭连接的 context manager（与 `AutoReplyStore`
  一致）；裸连接只提交/回滚不关闭，靠循环 GC 回收，会把 launchd agent 的 256 个 fd 软上限耗尽并让 worker
  线程退出、子进程反复重启。plist 模板把 `NumberOfFiles` 提到 4096，改动需 bootout/bootstrap 重装才生效。
- **会议对齐的供应商中断**：会议消费者与 worker 共用中断判定；全部路由暂停或最后一条路由 capacity/transport
  失败时任务延期（退避封顶、归还 attempt、不进 Attention），不再首轮判终态失败。
- **Audit 的 proposal_revision**：由 Audit run 回填，模型回显不同数字不再让轮次硬失败；规则文本说明该值只标识
  所审阅的候选，不是它请求的修订。
- **任务 Agent 的修订轮次**：可修订规则最多修订 `TASK_DECISION_REPAIR_ROUNDS`（2）轮，修订后触发另一条可修订规则
  会继续修订而不是漏成工作项终态失败；耗尽时抛出类型化的 `TaskDecisionRepairExhausted`。修订提示说明
  `project.memory_context` 的契约（query、召回为空时的说明句、runtime 不可用出口、无变化返回 skip）。
- **跨进程共享探针结果**：每个服务子进程启动时能力注册表为空；缺少某路由快照时先采用同机其他存活进程在
  store 里刚写入的新鲜健康快照，而不是自己再探一次（`_shared_healthy_snapshot`）。此前 email-worker 自己的探针
  在 provider 繁忙时反复失败，Consumer 选路一直是 `snapshot_missing/unhealthy`，整条邮件队列停滞。
- **中断轮询不留失败 run**：选路时发现所有路由暂停/未探测，turn runner 丢弃刚认领、尚未进入 runtime 的 run
  （`discard_unstarted_agent_run`，仅认领者可丢弃、且无 runtime attempt/工具事件/effect/回执），编排层直接延期。
  此前每次轮询都持久化一个失败 Consumer run（单任务累计 458 个 turn_attempt）。
- **可选字段的 null**：任务 Agent 决策模型（`StrictTaskModel`）把可选字段上的 JSON `null` 视为“未提供”（等同省略，
  下游仍看到声明的默认值），派生 schema 把这些字段标为可空；必填字段与动作所需的证据对象仍不可为 null。
- **模型输出不兼容旧结构**：微信/钉钉决策与 OKR 评审的 `AgentEnvelope` 解析不再接受任何旧结构（旧 `CodexDecision`
  对象、宽松 envelope 回退、`okr_review`+`request_id/result`、无动作的 `no_reply` 简写）；结构错误只做一次同会话修正，
  仍失败则任务失败并重跑。
- **修正轮遇到供应商故障**：唯一一次同会话修正轮以 capacity/transport 失败时，最多等待
  `CAPACITY_WAITS_BEFORE_FAILOVER`（3）次再离开该路由：每次失败以可重试的 `runtime_execution_failed`
  （`correction_capacity_wait:<n>`）交给调用方退避延期；等待期内该修正会话只在自己的路由上恢复；第 4 次失败换到
  后继路由，用新会话、原始 prompt 和独立的修正预算。修正轮的非故障类失败仍为终态。
- **同一 revision 的角色重试上限**：Consumer 或 Audit 在一次 worker pass 内对同一 proposal revision 最多 2 次
  turn（`MAX_ROLE_ATTEMPTS_PER_PROCESS`）；仍是可重试的结果/进程/依赖/原生写入失败时编排结果进入
  `failed_terminal`，任务在本 pass 结束为 `failed` 并保留最后一个 run 的真实错误码，不再回到 `pending` 让下一个
  pass 重进同一 generation（否则定时任务消费者和 active-recovery 路径会无限增加 `turn_attempt`）。
  内容反馈轮次耗尽但最后一次 Audit 仍有具体修改意见时，编排结果进入 `needs_human` 并提供“按审计意见修订”或
  “停止不执行”，不能丢掉这条意见后投影为 opaque failed。中断/授权/延期类错误码不计入该上限，按退避延期。
- **进入最后一条可用路由后才发现的故障**：该路由以 capacity/transport 失败且其余路由均已暂停时，与
  `runtime_unavailable` 同等处理：工作项和钉钉回复任务以 `runtime_provider_unreachable` 退避延期、attempts 归还、
  不写 per-item Service error（判定由 `app.worker._is_runtime_outage_error` 统一提供）；认证、结果、进程类失败仍有界。
- **定时任务的路由暂不可用**：调度器发现任务保存的路由仅是暂停/未探测（用路由层同一个分类器判为
  `runtime_provider_unreachable`）时只写 `skipped` 运行行并记 warning 日志，不再逐条写 Attention；缺能力、认证类
  暂停或运行时未配置仍进入 Attention。
- **结果不合契约时修正提示要说清楚**：任务 Agent 区分“完全没有决策 JSON”与“有对象但不合 schema”，失败原因与
  修正提示列出最后一个候选的字段错误（只含路径与描述，不回显模型的值）和最常违反的规则；会议对齐的解析失败
  以 `RoutedResultValidationError` 上报，因此同会话修正轮真正触发，修正提示折叠列表下标并附派生 schema（主提示同样
  内嵌，第三方 provider 不执行 `--output-schema`）；OKR 评审的 AgentEnvelope 修正提示同理由模型 schema 渲染。
- **微信通道同样的两条规则**：微信/Codex 决策解析（`app/codex_decision.py`）用共享提取器解析围栏/散文包裹的
  AgentEnvelope，形似 envelope 但不合 schema 的候选进入唯一一次同会话修正轮（修正提示列字段问题并渲染
  `AgentEnvelope.model_json_schema()`），不再被宽松回退接受；微信消费者与钉钉 worker 共用供应商中断判定，
  中断时以 `runtime_provider_unreachable` 退避延期、归还 attempt、不写 `reply_attempts`、不进 Attention；
  决策轮次按本代已持久化的 Consumer run 推导（终态 run 推进轮次，running 保留），避免同一轮被无限重入。
- **Provider 的通用过载包装**：Codex 会把上游 429/5xx（限流、MiniMax token plan 用尽、上游过载）
  统一包装成 "We're currently experiencing high demand"，流里不带 provider 原文；服务把它归为
  `codex_provider_overloaded`（容量类：同路由先重试 3 次，仍失败才暂停路由并 failover），而不是传输断开。
- **模型把 JSON 包在说明文字或代码围栏里**：任务 Agent、微信 `AgentEnvelope`、会议对齐三个解析器共用
  `agent_message_json_objects`，在整段消息里逐个定位顶层 JSON 对象（穿过 ``` 围栏和说明文字），
  取最后一个满足各自 schema 的对象；只有完全找不到 JSON 时才报“未找到”。JSONL 流两侧的非 JSON 行
  （Codex 警告、MCP 启动噪音）一律跳过。会议对齐与任务 Agent、微信一样，结果不合 schema 时在同一会话里
  做一次修正轮，修正提示列出上一次输出的具体字段错误；路由层把每次结果校验失败的原因写入服务日志。
- **Email 任务的瞬时故障**：加载任务上下文时遇到邮箱/网络瞬时故障（IMAP TLS 握手超时、连接重置、
  imaplib 传输错误）按退避延期重试（最多 5 次，错误码 `email_provider_transient:<类型>`），不是终态失败；
  轮次中被 rerun 或孤儿回收轮换掉的任务交给新的 generation 处理。
- **Email 任务的孤儿回收**：Email Agent 消费循环每轮先回收本频道的过期认领（`processing` 超过 10 分钟
  且没有活着的 Agent run，或累计超过 60 分钟）并以 `stale_email_task_recovery` 重新入队，再一次只认领
  5 条；任务在该循环里串行执行，认领过多只会拉长尾部锁定时间，进程重启时会把未开始的任务永久留在
  `processing`（Attention 看不到它们）。
- **Audit 无需抄写任何东西**：`unsubscribe_email` 只接受 `task_id`，其余全部从 durable 状态读出；
  模型漏字段、截断摘要或抄错 operation 都不可能发生，因为没有东西给它抄。退订页面若在
  `domcontentloaded` 后仍是空白（脚本渲染），浏览器会最多等待 5 秒再判定 `page_state_missing`。
- **无证据的 executed**：退订任务里，Audit 只有在存在该动作身份的 receipt 时才能返回 `executed`；
  否则解析阶段就判为 `codex_result_invalid`，下一轮带修正块重做，编排层的 continuation 守卫只作最后兜底。
  receipt 由服务写入，模型无法伪造，也不按 turn 计数——一次退订一条 receipt，不是一轮一条。
  有证据的 `executed` 其 `external_result.operation_id` 由服务从 Audit run 回填（不透明操作号是服务
  自己的，模型抄错不应让任务终态失败）；缺少 `external_result` 则同样进入修正轮。
- **结果不合契约**：Agent 返回了 JSON 但不满足 wire schema 时，解析器报 `codex_result_invalid`
  并保留失败字段位置和契约自己的校验说明（不保留模型原文）；同一角色、同一 revision 的下一次 turn 会收到
  `## Result Correction`，把这些位置和说明反馈给模型，要求只返回修正后的结果。只给类型不给说明
  （如 `result: value_error`）时模型无从下手：任务 384711 两轮重试原样重交了同一个结果。只有完全没有 JSON
  对象时才是 `codex_result_missing`，此时下一次 turn 同样收到修正块，说明上一轮只有说明文字。
  wire 契约中的 `error_code` 接受 `null` 作为“无错误”（等价于空字符串）；模型无需为无错误结果编造字符串哨兵。

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
- 结构化最终结果和精确去重键。
- provider 原始工具事件按执行顺序 append-only 保存；应用层不分类命令、读写模式或工具权限。
  原始参数与工具结果仍以 Codex session JSONL 为详细来源。

服务不在 SQLite 复制完整 Codex transcript，也不维护另一套业务审计日志。History 页面按
session 指针读取 JSONL，并只向普通用户展示业务结果；内部角色、规划标签和原始敏感工具
输出保持折叠或脱敏。

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

| 终态 | 含义 |
| --- | --- |
| `executed` | B 已执行，并返回 provider 结果或稳定外部动作标识。 |
| `no_action` | A 确认当前触发无需外部动作。 |
| `feedback_provided` | B 给出结构化反馈，等待 A 在原兼容 session 中生成下一 revision。 |
| `needs_human` | proposal 也可附带一个独立升级（同样 2 至 4 个选项）：Audit 执行动作后任务以 `needs_human` 收口。除此之外，仅在信息完整且 `(risk=high 且 confidence<0.5)` 或 `rule_coverage<0.5` 时使用；必须提供 2 至 4 个互斥、可执行的规则/Skill 选项，每项包含唯一稳定 `key`、显示 `label`、可执行 `instruction` 和 `consequence`/影响。普通材料不足走 ask-back，不计入此状态。 |
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
| `app.business_skills` / `app.managed_skills` | 提供八个仓库基线 Skill，并管理 SQLite 中不可变 revision、next-start config 和启动 load receipt；不参与业务路由。 |
| `app.agent_skill_usage` | 提供 Agent 执行环境所需的 Skill 读取辅助；不参与普通业务结果审核。 |
| `app.consumer_agent.ConsumerAgentRunner` | 复用兼容的 A session，读取、判断并提出候选；应用层不限制其具体工具。 |
| `app.audit_agent.AuditAgentRunner` | 复用兼容的 B session，审核并执行候选；应用层不审核命令、工具或读取方式。 |
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
