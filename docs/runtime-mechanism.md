# Current Runtime Mechanism

本文档是 CEO Agent Service 当前运行机制与已批准生命周期政策的总览入口。除明确标注为
“已批准的生命周期政策”的段落外，正文描述当前代码事实；政策段落是后续实现约束，不表示
对应代码已经切换、部署或在生产启用。`docs/superpowers/` 下被新设计取代的历史 spec/plan
仅用于追溯，不代表当前目标规则。

## 运行角色

每个需要 Agent 处理的任务都经过两个职责不同的角色：

1. 执行 Agent 读取上下文和证据，形成候选结果或任务结果。
2. 审核 Agent 独立检查执行结果，决定通过、反馈修改或升级人工处理。

执行 Agent 不确认自己的结果已经完成。审核 Agent 也不替执行 Agent 重写业务内容；如果业务含义、对象、证据或输出需要变化，审核 Agent 必须给出具体反馈，由执行 Agent 生成修正版。

主页面 `/` 只是同一 Service Runtime 的一个输入和展示入口。页面 turn 使用 `workbench`
workload 进入 `RoutedCodexExecution`，共用模型路由、会话、runtime attempt、失败切换和 CLI
原生 `auto_review`；页面不启动自己的 Codex runtime，也不定义独立审批策略。
旧版确认记录只读，不能从页面或 API 恢复执行。

## 标准生命周期

```text
pending -> running -> done
                  -> failed
                  -> needs_human
```

- `pending`：已持久化，等待执行。
- `processing`：历史兼容名称；新任务统一使用 `running`。
- `needs_feedback`：审核 Agent 已完成审阅，反馈已持久化，执行 Agent 需要修改原结果。
- `revision_pending`：修正版已排队；修正版必须有新的 revision 标识，并保留原结果和反馈的关联。
- `done`：逻辑完成且结果已持久化。
- `sent`：历史兼容名称；新任务以 `done` 表示完成，provider 发送结果保存在 trace。
- `needs_human`：只能是有可追溯 Agent run 的完整结构化规则/Skill 缺口：
  `information_completeness>=0.5`，且 `(risk=high 且 confidence<0.5)` 或
  `rule_coverage<0.5`，并附 2--4 个互斥、可执行选项。技术读取、provider、receipt、
  Audit、路由、schema 或重试失败一律使用 `failed`；已完成的外部动作也不改变这条分类。
  重跑前由 `external_action_key` 和 provider 回读机械去重，而不是把“可能已执行”伪造成
  Derek 的规则选择。
- 每个 `needs_human` 结果还必须包含面向用户的 `needs_human_reason` 和可追溯的
  `decision_basis`：已核验事实、适用规则、质量分值解释、未执行证据和结论。高风险外部
  动作需要单一、与当前 OA 实例/任务匹配的 `authorization_plan`，写明动作、影响、不会执行
  的动作以及执行后的读回。Attempt 详情只根据当前 Attempt 指向的有效终态 run 展示这份说明；
  旧 run 或不完整结果不能把 `done` 投影成当前人工待办。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败；必须保留失败原因和阶段。

### 失败与人工决策通知

`failed` 和 `needs_human` 都使用同一条通用本地通知链路，不按业务频道假定目标是钉钉。通知携带当前
`attempt_id`，点击通过 `/open-attempt` 打开对应审计详情；若存在已打开的 CEO 页面，由 Service Worker
导航到 `/attempts/{attempt_id}`，否则由本机通知回退直接打开该详情页。钉钉会话只能作为额外上下文入口，
不能成为邮件、听记、任务或其他来源通知的唯一点击目标。

高风险 OA 同意还须核验当前用户对当前实例、当前任务的明确授权。材料完整、会议结论或
定时任务的概括性授权均不能代替该授权；缺少时不执行外部动作，保留对象身份和
`authorization_required` 错误供人工确认后重试。此门禁与证据质量评分分开，不能通过
调低 `confidence` 或 `rule_coverage` 伪造 `needs_human` 分类。

当前代次的最新 Attempt 指向失败 run 时，即使关联任务进入 `pending` 等待重试，History 与 Attention
仍显示该失败，直到后续有效 run/Attempt 给出新的当前状态。没有当前代次失败 run 的 pending
任务本身不进入 Attention；旧代次失败也不污染新代次。
从失败 Attempt 手动重跑时，Consumer 与 Audit 的上下文必须带入来源代次的结构化 Audit 反馈；
旧候选被否决的点仍须解决，或用新证据明确说明其不再适用，不得把相同候选当作未审核的新建议。
Codex CLI 报告同一 session 有其他 active writer 时，运行时将其视为本地 session 冲突，
在原路由和原 session 上限次指数退避重试，不因此暂停整个 provider。
服务启动时恢复被中断的会议作业，会在同一事务内将作业改为 `retry`、关闭运行记录并释放
该作业的 dispatcher claim；不再等待旧进程的租约自然到期才允许重新领取。
会议群消息发送的 DWS 可重试故障即使超过普通尝试次数，也保留同一任务和投递键进入
`retry`，按共享指数退避且最长等待 15 分钟；错误记录须保留 provider 原因。只有非外部
依赖故障才按普通尝试上限进入 `failed`，重试前必须核对本地回执及可用外部读回。
旧版本已落为 `failed` 的会议发送，只有在原决策、原群目标完整且本地无发送回执，
并独立确认群内未收到该摘要后，才可受限恢复至 `ready_to_send`；恢复保留原正文和投递键。

## 功能机制开关与任务生产

邮件分类使用独立的 `email_agent_classification_tasks` 持久化队列。Responses API
请求允许正常的模型响应时间；临时网络、超时和租约中断不进入终态 `failed`，而是在同一
任务上按共享指数退避重试，重试间隔最多 15 分钟。只有输入、契约或持久化冲突等不可重试
错误进入 `failed`。Status、Attention 和每小时 quality gate 都必须覆盖该队列，不能只用
Email worker 的汇总健康状态代替任务状态。

`Settings -> Skills` 的开关位于任务生产边界。功能与业务 Skill 的多对多关系由
`data/config/skill-features.json` 声明，运行时开关由 `data/config/skill-state.json`
持久化；每次生产检查都会读取最新状态，因此常驻 worker 不需要因开关变更而重启。

关闭一个功能只拒绝该功能对应的**新任务/新作业创建**，并不会取消、删除或改写已经存在的
`pending`、`running` 或重试任务。消费者领取和继续处理已有任务时不再重新判断该开关，保证
切换不会中断正在进行的生命周期。缺少或校验失败的关联 Skill 会让该功能标记为配置不完整，
并阻止它继续创建新任务；其他功能不受影响。

## Runtime-managed Skill 修订与启动配置

Settings 不再直接编辑项目目录或 `~/.agents/skills`。每次保存会在 SQLite 中创建一条不可变的
managed Skill revision，保存原始 `SKILL.md`、精确 SHA-256、父修订和来源。项目内八个
service-owned 业务 Skill 只在首次初始化时导入为基线；导入不扫描、不覆盖用户、插件、系统或
operation Skill，也不会把 Settings 编辑同步回任意运行时目录。

一次启用操作创建一个不可变的 `runtime_skill_config`，其状态只能是
`pending_restart`、`active` 或 `load_failed`。它绑定每个 Skill 的精确 revision、启用位、加载
顺序和用途。运行中的 Agent 不读取可变 Settings：服务进程启动时解析候选配置，加载精确修订，
并写入包含 PID、配置 ID 和每个 Skill SHA 的 append-only load receipt；只有匹配的成功 receipt
才能把候选配置变成 `active`。候选加载失败会记录具体错误并保留前一个 active 配置；回滚同样
创建新的 next-start 配置，不会改写历史记录。

Consumer 在一次 invocation 开始时获得这一不可变 snapshot，之后不会在同一个 invocation 中
重新读取 Settings。`feedback_iteration` 是同一配置中的系统能力，不是第八个业务任务生产开关。
它关闭时，反馈的读取、历史和 reopen 仍可用，但 UI 的“处理反馈”保持可见且 disabled，后端也
拒绝新的 claim；已有处理项按受控配置转换返回 pending/open，并保留其历史。

功能与业务 Skill 的多对多关系仍由 `data/config/skill-features.json` 声明，运行时开关由
`data/config/skill-state.json` 持久化。它们只控制**新任务/新作业创建**，不控制 Skill 是否可被
runtime config 加载。关闭功能不会取消、删除或改写已存在的 `pending`、`running` 或重试任务。
缺少或校验失败的关联 Skill 会让该功能标记为配置不完整，并阻止它继续创建新任务；其他功能不受影响。

每个 Consumer 和 Audit 的结构化结果必须统一携带四个字段：`risk`（`low`/`medium`/`high`）、
`confidence`、`rule_coverage`、`information_completeness`，后三者均为闭区间 `[0, 1]`。
路由严格按以下顺序执行：`information_completeness < 0.5` 时形成普通 proposal 或一个具体
问题的 ask-back，沿现有 Audit/send 链路处理，不新增 ask-back 持久化 outcome；否则，
`(risk=high 且 confidence<0.5)` 或 `rule_coverage<0.5` 才进入 `needs_human`，并提供 2--4 个
互斥、可执行的规则/Skill 选项；每项必须包含唯一稳定的 `key`、显示用 `label`、可执行的
`instruction` 和 `consequence`/影响；其余由适用 Skill 自主完成。ask-back 不计入 needs_human。
读到 Skill 不等于它覆盖本案：Skill 自己写明本案所依赖的分支（门槛、例外边界、谁有权决定）未定义时，
`rule_coverage` 低于 1.0；以缺规则、缺门槛或缺授权为理由的 `needs_human` 配 `rule_coverage=1.0` 属于自相矛盾。
反馈可同时选择 one-time 与 Skill update；二者复用同一业务对象和同一 attempt，在 provider 仍可访问的
同一 session 中生成新 revision，不新建 session。技术、provider、读取、路由、schema、Audit 或 retry failure
永远是 `failed`；领域 `authorization_required` 也不泛化为 `needs_human`。只有精确通用码、
匹配当前动作的授权计划和统一质量门槛全部成立，才可形成结构化人工规则决策。
provider 返回 `confirmation_required` 也属于运行时失败边界：它表示外部动作尚未执行，
不是需要 Derek 决定的业务规则缺口。必须保留具体错误并落为 `failed`，不能生成“确认执行/停止”
这类泛化的人工作业按钮；修复旧投影时同时清空这类选项。
同理，持久化的 `needs_human` 结果若声称 `error_retryable` 为真，或
`error_authorization_required` 为真但缺少精确通用码、授权计划或统一门槛，不能作为规则决策
展示；启动修复将其收口为 `failed` 并保留该结果给 History 排查。

新 wire 结果的四字段均为必填并严格校验。旧 `final_result_json` hydration 仅可受控补齐
`rule_coverage=1.0`、`information_completeness=1.0`，保留旧的 `risk`/`confidence`；原始历史
run 和 audit 不改写。Quality gate 与 Attention 只按 current latest projection 的结构化结果计数：
字段缺失、非法值或 outer outcome mismatch 均 fail-closed 为 invalid violation；reviewed、历史、
pending recovery 排除，ask-back 不计 `needs_human`。

`needs_human` 是 Agent run 的业务决策终态；对应的 `reply_task` 可以已经是
`done`，但在 Derek 提交决策前仍属于当前待处理项。这类带结构化依据和可执行选项的
决策在独立的人工决策列表展示，不计入只显示错误的 Attention。无结构化依据或
`information_completeness<0.5` 的结果是技术失败，保留在 History 和 Attention。
人工决策列表以当前 `reply_attempt.agent_run_id` 关联的已完成 run 为准，并同时校验
任务代次和当前 Business Object；不能再用一个按 turn 顺序挑出的“最新 run”覆盖该关联。
这样，较晚的 Audit turn 不会把当前 Attempt 指向的 Consumer 规则决策从 Attention 投影中隐藏。
重跑前写入的 `reviewed_at` 只记录上一轮授权反馈；若本轮最新结果再次是未解决的
`needs_human`，仍须展示为新的人工决策，直到状态变为已选择或显式解决。

## 审核反馈闭环

```text
执行 Agent 生成 run R0
  -> 审核 Agent 审核 R0
      -> 通过：审核 Agent 执行/发布 R0，provider 返回成功结果后进入 done/sent
      -> 需要修改：写入反馈 F0，R0 保留为历史 run
          -> 执行 Agent 收到 F0，生成修正版 R1
              -> 审核 Agent 审核 R1
                  -> 通过：发布 R1
                  -> 再需修改：进入下一反馈周期
                  -> 超过内容反馈上限：failed
```

反馈必须包含规则、观察结果和修改要求。审核 Agent 不能直接改写执行 Agent 的业务正文；服务只保存 run、revision、反馈、session 和 provider 结果标识之间的关系。同一任务最多允许三个内容反馈周期；基础设施失败不消耗内容反馈周期。内容反馈耗尽是自动闭环失败，终态为 `failed`。只有 Consumer 或 Audit 自身返回的完整、可追溯结构化结果满足 `(risk=high 且 confidence<0.5)` 或 `rule_coverage<0.5`，并同时提供 2--4 个规则/Skill 选项时，任务才进入 `needs_human`。

Consumer 与 Audit 之间自然流逝的时间不是候选事实冲突。当前执行时间只用于判断动作是否过期或上下文是否变旧；Audit 不得要求候选复述精确执行时间，也不得仅因自己的执行时间晚于 Consumer 而拒绝其他方面可执行的候选。

钉钉引用回复由服务使用 DWS `chat +messages-reply` 投递；该入口核验原消息、发送者与会话一致性，且适用于已确认的单聊会话。投递仍沿原业务对象的幂等键和回执核验，不因引用回复失败自动改发普通消息。

会议总结的业务群候选由会议正文中的业务词与标题共同检索，排除听记摘要里的时间标签、图片链接等元数据。服务读取候选群近期消息，将与会议议题相关的讨论片段、群成员覆盖、群规模及同名会议历史投递一起交给决策 Agent。排序优先考虑群名是否对应摘要主题，再参考讨论片段；词项交集只是检索线索，Agent 必须核对实际讨论、行动负责人和受众。参会人覆盖率只证明受众交集，不证明业务归属，不能单独强制选群。历史投递仍需本次实时成员覆盖达到门槛，且须与当前议题一致；此前已发送的总结不会因路由修复自动重发。

对于源单聊的澄清动作，Consumer 必须在 action target 中提供已经通过实时读取确认的参与者 `open_dingtalk_id`。若该参与者字段以 `verified_participant_open_dingtalk_id` 表示，Audit 将其作为同一稳定接收人身份执行单聊发送；不会把 `conversation_id` 当作群聊目标。
若单聊候选同时带有原会话的 `conversation_id` 和收件人的 `open_dingtalk_id`，发送入口在核对会话与原任务一致后只向 DWS 传收件人 ID，避免把一个单聊动作误作两个互斥目标；群聊或不匹配会话不作此转换。

当 Audit 因临时授权映射或运行配置失败后被重新调度时，恢复会创建下一次 Audit turn；已失败的 turn 保持历史记录，不能被重复领取。

每个队列任务的 `Original trigger` 是该任务唯一的权威输入，由
`trigger_message_id` 标识。近期会话消息、材料和实时读取结果只能补充事实，不能把
Consumer 的任务改成另一个消息、日程或审批事项。Audit 返回 `feedback_provided` 后，服务
必须把规则、观察结果和修改要求传给下一版 Consumer proposal，再创建对应的 Audit run；
Audit 只反馈修改要求，不直接替换 Consumer 的业务正文。

Task Agent 按 Task-first 合约处理普通 work-summary：一个来源可返回 0..N 个 `task_decisions`。
每个保留决策都必须引用 WorkItem 的准确 `source_ref`，并提供确实出现在来源摘要中的原文
`source_excerpt`；检索到的 Task、Project 候选及 memory 只能提供背景，不能替代来源证据或授权。
`skip` 表示没有应保留的 Task，不再以 Project 是否存在作为判断条件。
正式 Project 注册表和当前 Task 状态优先读取最近一次确认的正式周报，尤其是
项目管理部或管理层周报中明确列出的项目、负责人、目标、DDL、状态和下周任务；
保留周报文档引用及统计周期。会议纪要、逐字稿或已确认会议行动项是尚未进入
周报的新决策或变更的次级权威来源。聊天或消息只能补充上下文、负责人、状态或
链接，不能单独创建正式 Project，也不能自行覆盖周报明确字段。来源冲突时先取
最新明确周报字段，再取最新确认的会议决策，并保留精确来源引用。
更新既有 Task 时，Task Agent 可依据本轮来源证据修改标题或描述；变更、新来源信号的证据链接及 before/after Task 事件在同一事务提交。纯标题/描述变更记录 `details_changed`，与状态、负责人或相关性等字段合并变更时记录 `fields_changed`；只把证据链接到 Task 而没有任何实际字段变化仍是无效更新。

Task Agent 使用统一的 `TaskAgentDecision` 结果协议，既可返回 0..N 个新建/更新 Task 决定，
也可在同一个决定里返回对已绑定 TODO 或 follow-up 的适用状态转换。CLI 仍按 Work Item 的精确
source type 选择上下文与服务端应用路径，但调用的是同一个 Task Agent、相同结果 schema；没有
独立 Completion Agent 或第二套 decision schema。completion 操作在 envelope 顶层分别使用类型化的
`todo_changes` 和 `follow_up_changes` 列表，不嵌套在单个 `task_decisions` 中。`todo_completion_evidence_candidate`、
`todo_completion_check` 来源可关闭 Work Item 中唯一绑定的既有本地 TODO，并记录 completion evidence
与顶层 `search_trace`；证据候选同步更新 candidate 状态。TODO 完成会完成其关联 follow-up；需要检查
或修复单个 follow-up 的来源只可转换 Work Item 中明确链接的既有 follow-up。关闭外部 DingTalk TODO
仍通过既有同步 outbox，且仅在 DingTalk client 已配置时排入。无完成证据时仍持久化检查摘要与
`search_trace`，保持 TODO 开放并将输入标记为 skipped。错误身份、类型或不合法决策使 Task Agent run
与输入失败，领域变化、输入终态和 run 终态在一个本地事务中提交。

服务端应用路径在提交事务内核对 work-summary 队列的 source_type/ref、持久化 TODO/project、
证据候选及所有关联 follow-up draft 的绑定关系。`search_trace` 的 source_kind 必须属于输入
`search_policy.allowed_sources`，来源时间必须在指定时间窗内，检索时间由服务记录且不得早于窗口起点，
来源数量和当前运行中可观察的工具调用数不得超过 policy 上限；来源定位符必须能匹配当前 run 的工具事件，call IDs 由
服务端从该事件生成，不接受模型自报 ID。completion candidate 的来源时间还必须与持久化候选一致。
现有 receipt 不独立证明外部证据正文的真实性或语义；`completed_at` 只验证时间格式和不晚于当前检查时刻，
不证明它来自来源正文。raw source read 次数也没有独立运行时计数。Task Agent prompt 明确要求只读发现，
不得通过 CLI/API/MCP 工具创建、更新、删除、发送或完成外部记录；这是 prompt-only 的 best-effort 指引，
不是运行时权限边界，Codex route 仍没有 per-turn MCP 写工具 allowlist。Task Agent 的结构化输出由服务端
校验并应用支持的操作；外部 TODO 完成只走现有 outbox 同步，避免双写。当前 audit event 也没有可信的
search-vs-raw-read 分类，无法独立计数 `max_raw_reads`。该限制属于当前 prompt/runtime 能力边界，超出已批准范围，不是 Task 6 发布阻断；prompt 中的只读要求仍是 best-effort 指引而非硬性运行时边界。

这描述当前功能分支的代码契约，不证明变更已部署。Task 6 仍不得单独部署，整体切换仍需发布验收。
Task-first 输出不承载 Project 写操作或直接创建新外部 TODO；合格 Task 的钉钉镜像由 Task 7
服务端 outbox 完成。缺少可信 producer 提供的 `external_task_id` 时不得猜测外部对象。

Task Agent 的共享会话不会改变 Task、TODO 或 follow-up 的服务端应用边界；适用操作仍由当前
Work Item 明确绑定，并在对应服务事务中校验和应用。

普通 Task 提取和 TODO/follow-up 完成检查共用稳定的 `task-agent:work-tracking:v1` 会话范围；每个
Work Item 仍有独立的 workload key、Task Agent run 与 runtime attempt。路由器按 runtime route 保存
session，同一 route 上的后续输入续接既有 session。`process-work-items` 在恢复队列和领取输入之前
取得共享 SQLite session lock，运行期间每 60 秒续租；竞争中的进程返回 0 项且不领取、不增加尝试次数。
执行前与领域事务提交前均检查 lease；失锁后本轮不提交领域更改，并按 work-summary 临时错误策略重试。
服务内的 dispatcher 以单 worker 运行 `work_summary` 队列（与会议队列相同，见 `SINGLE_SESSION_ADAPTERS`），同一时刻只有一个 Task Agent turn 续接共享 session；2026-09-24 Task-first 上线后该队列曾用两个 worker，第二个 turn 总是撞上 `already has an active writer`，约 70 秒重试后判失败，25 个 Work Item 因此失败。
旧 `task:<run_id>` 会话记录不会迁移或覆盖。Task Agent prompt 将此前会话内容限定为背景，决定须依据当轮
Work Item、当前存储/检索状态和新来源证据。Codex CLI 自己管理上下文自动压缩；其他 route 使用其自身
会话能力，压缩后的会话仍不能替代 Work Item、数据库或来源证据。
若 CLI 明确报告 compaction 自身因模型 context window 超限而失败，当前 run 会清除此 route 的共享
session 指针并在同一路由的新 session 重试一次；若 fresh session 仍超限，则转入既有 runtime route
fallback，不循环创建 session。普通会话冲突或其他错误不会清除共享 session。

正式指派不等于负责人接受：有授权来源、明确交付物和明确负责人的指派可成为
`assigned_unaccepted` Task；只有负责人本人明确接受并且来源上下文带有可信、精确的
`reply_to_source_ref`，且决策明确引用该 Task 已链接的指派证据，才可转为已接受承诺。模型不能
制造回复链接或仅凭“收到”、相似文本、TODO 存在推断接受。缺少可信链接时保留未接受状态并记录
跳过原因。负责人身份 ID 只能来自可信来源映射或与来源发言人匹配的稳定身份，不由模型单独指定。

日期按语义类型保存，不使用含糊的通用 deadline：`assigned_at`（指派日期）、
`requested_deadline_at`（请求方要求日期）、`external_deadline_at`（外部期限）、
`committed_deadline_at`（负责人接受的承诺期限）、`estimated_deadline_at`（估算日期）及
`next_check_at`（下次检查日期）。Task 6 只记录来源明确给出的 next-check 日期，不自动创建检查 cadence，也不把请求/外部期限改写成检查日期。每条日期都要有明确日期值、对应来源引用、原文摘录和行为人；只有
接受承诺能建立 `committed_deadline_at`。标准化日期必须与原文中精确、可解析的日期短语一致；不能从只写日期的来源扩展出具体时分。周级或其他不可解析表达只保留在已链接的原始来源信号中，不产生 typed date fact，也不猜时间戳。`assigned_at` 只从明确正式指派来源的可信创建时间元数据派生，不是模型提供的日期。估算的行为人沿用作出估算的可信来源发言人；Agent 仅是抽取者。只有 `next_check_at` 由 Task Agent 署名，且必须是来源中明确给出的检查日期；它不自动创建 cadence。其他日期要求可识别的来源行为人，不能伪装或猜测身份。当前 AI Minutes producer 尚无可信的发言人到身份映射，因此不能把纪要中被转述的多方日期归给主持人或模型指定的人；该类日期暂不入 Task，需由 producer 提供映射后另行接通。

一个 work-summary 输入中的全部 Task 变更、来源信号、日期、关系/聚类/锚点/Project 候选提案及
输入/run 结果在单个数据库事务中提交或回滚。CEO Attention 是派生投影，只在提交后先应用有效提案，再对所有受影响 Task（合并时包括来源和目标两侧）重算成员资格；完成、取消或变为不相关的 Task 会退出当前成员。Task Agent 的类型化完成和外部 TODO 完成也在各自领域事务提交后重算对应 Attention 成员，完成 Task 退出而未完成的兄弟 Task 保留；这不自动把 Attention 标为已解决。投影失败
不回滚已完成的 Task 事实，也不把已完成 run/input 改成失败。Task 6 不再把 `work_projects`、
`work_todos` 或 `work_updates` 当作 Task 事实写入；Task 7 的钉钉 TODO 镜像使用独立的
`business_task_dingtalk_links` 和 `business_task_todo_sync_outbox`，并不把旧 Project/TODO 当作 Task 主键。
此处描述的是代码分支，不代表已经部署；Task 6 不单独部署或重启服务。

目前通用 work-item 生产者尚未提供所有授权和 owner identity 映射元数据；在来源元数据缺失时，
不能据此把显式提到负责人的讨论升级成正式授权指派。对应的生产者接线必须作为单独集成范围处理，
不能由 Task Agent 猜测或合成。

Attention 的 `material_trigger` 是 Agent 对来源证据的语义分类，不是独立机器证明。每项提案必须附带当前来源摘录中的精确 trigger quote、分类理由和明确 CEO action；投影还要求业务锚点已确认、Task 相关且来源信号已链接。系统把 trigger 类型和摘录写入 Attention 原因/事件沿革。系统不靠关键词推断重大性；若未来需要独立机器级判定，应另行定义 canonical trigger facts 或人工确认机制。

OA 审批中，申请人的补充只可完善其可核验的事实或材料，不能生成、替代或关闭规则、例外、
授权与动作映射。材料缺口和规则缺口同时存在时，Consumer 在原审批向申请人评论可补材料，
并独立保留 `needs_human` 处理政策缺口；申请人后续回复只能触发重新读取 OA，不能使
`rule_coverage` 变成 100%。

两件事放在同一个结果里：`outcome: proposal` 携带可执行的评论或退回，同时填
`needs_human_reason`、`decision_basis` 和 2--4 个 `decision_options` 描述政策缺口
（`ConsumerAgentResult.escalates`）。三者必须齐全；不能带 `authorization_plan` 或错误码。
Audit 按常规审阅并执行 proposal，执行后返回 `executed`，不把候选的问题改写成自己的 `needs_human`
（384699 的 21117 这样做后分值被判不符，重试把 rule_coverage 从 0.7 压到 0.4 才通过）；Audit
`executed` 后编排结果为 `needs_human`，选项取自 Consumer 的升级，消息投影照常按 Audit
的执行记录写入。与“外部动作已完成→needs_human”同一终态：动作已做，剩下的问题归 Derek。
Derek 2026-09-23 定；此前结果只能二选一，384699 丢了评论，384514 没要材料。

## Business Object、Task、Agent Run 与 Reply Attempt

运行时先用 `business_object_key` 识别同一外部业务事项，再使用三层运行对象：
`reply_task` 是可领取和重试的唯一当前队列投影，`agent_run` 是一次真实的
Consumer/Audit 执行，`reply_attempt` 是稳定的业务结果当前投影。一个 task 可以有
多个 agent run；重跑不新建业务 attempt，而是在原
`reply_attempt` 上更新 current projection，并把新的 agent run 追加到历史。

```text
business_object 1 ── 1 current reply_task
reply_task 1 ──< reply_task_inputs
reply_task 1 ──< agent_runs
business_object 1 ── 1 current reply_attempt
```

OA 的稳定身份是 `process_instance_id + task_id`。同一 OA 从 webhook、pending scan、
用户人工反馈进入时追加 input 并复用同一 task、同一兼容 Consumer session，在当前 run
结束后提高 generation 并重新排队同一 task。服务修复运行时、路由或本地执行环境时，虽仍
复用同一 task 和业务对象，但必须创建新的 execution generation，以隔离不可变的旧 run；只要
provider session 仍可访问，就继续该 session 并在新 turn 注入当前 Skill/契约。只有 provider
明确返回 session 不存在或认证上下文失效时才清除绑定；这两类重跑都保留旧
run/session/provider 事实。

DingTalk 日程卡片改期可能原地覆盖卡片内容而不产生新消息 ID。每小时的近期消息恢复读取
该消息当前可读内容；若它和当前 task 投影不同，且实时日程仍有效、本人仍待响应，就以新的
`input_revision_key` 追加一条 `reply_task_inputs`，提高 `input_version`，并为同一个 task
开启新的 execution generation。相同卡片快照不会重复入队，已经接受、拒绝或取消的日程也
不会因此重新触发。

评论通知只有 `process_instance_id` 时，运行时从事件正文提取该身份；若该实例只有一个已知
节点，则归并到该节点。存在多个节点而事件没有 `task_id` 时不得猜测。

`reply_attempt` 的 `agent_run_id` 指向当前投影对应的最新或终态 run；完整执行历史
通过 run 的 task、generation 和关联事件查询。Attempt 页面可以切换多个 Consumer
或 Audit run，但不能编辑或覆盖旧 run。原始失败、session、runtime attempt、tool
event 和 provider 结果仍然作为 append-only 事实保留。
Workers 的当前 attempt 队列统计会排除 `agent_run_id` 指向旧 execution generation
的记录，即使同一业务对象更新了 trigger message；旧 attempt 仍可在历史详情中查看。

对进入 Consumer/Audit 的 task，当前状态由 task 当前 `execution_generation` 中最后一个
Agent run 决定：最后一个 run 失败则当前投影为 `failed`；只有该 generation 有明确完成的
终态 run，task 才能投影为 `done`、`skipped` 或 `needs_human`。`reply_task.status` 只表示
队列是否仍待领取或已被收口，不能单独把失败 run 改写成成功。外部 provider receipt 仍用于
防止重放，但它不能覆盖 Agent run 的失败；服务必须创建新的 generation 并完成新的 run 才能
修正当前投影。真实处于 `pending` 或 `processing` 的 task 则分别显示为等待或执行中。
History 的当前筛选也遵循此投影：同一 generation 的更新 run 已在执行时，旧 attempt
字段中的失败仅保留在 run 详情，不继续计入当前 `failed` 筛选。
当失败 Audit 已持有同一稳定动作身份的 receipt 时，服务自动先持久化失败 run，再通过正式
重试入口开启新的 generation。新一轮 Audit 只读取 receipt，不重新执行外部动作；它成功后才
把 current projection 收口为 `done` 或 `skipped`。

`needs_human` 收到明确人工指令后，必须创建新的 reviewed revision 并重新进入统一
Consumer/Audit 流程；原 `needs_human` attempt 继续作为历史事实保留。没有明确指令时
不得自动猜测决策，已经送达或完成的 attempt 也不得由该入口重新打开。

如果 Derek 已直接在钉钉完成 OA，定时 OA 扫描会读取该 process instance 的实时状态。
实例已进入明确终态时，服务不重放审批动作，而是把当前 `needs_human` attempt 收口为
`skipped`，保存实时结果作为 resolution，并从当前 Attention 移除；原 Agent run 和决策
内容仍作为历史事实保留。仍为 `RUNNING` 或读取失败时不得自动改写。

### 重复外发故障的统一排查顺序

当人员收到多条相似或互相矛盾的消息时，不得先把问题归因于某一条 prompt 或某个发送命令。
按以下顺序检查同一稳定业务对象的完整链路：

1. 是否有多个 `reply_tasks.business_object_key` 指向同一个 OA 节点、会议或投递对象；
2. `business_object_tasks.reply_task_id` 是否指向当前唯一 task；
3. 每条入口是否只追加到 `reply_task_inputs`，还是错误创建了并行 task；
4. 已成功动作的 `external_action_key` 和 provider 结果是否被后续 run 复用；
5. 外发消息是否只形成一条 `sent_replies` 投影，其他 run 是否只追加 observer；
6. Workers、Attention 和质量门是否只统计 current projection，而不是历史物理行。

典型错误链是“多个入口生成多个 task → 各自反馈和执行 → 一个 task 成功后其他 task 仍运行”。
修复目标是稳定业务身份、唯一当前投影和动作幂等；不得通过删除历史 run、修改旧 tool event、
按人员姓名硬编码跳过发送，或重新引入应用层命令/证据审核来掩盖重复执行。

只有成功的 provider 结果和真实发送记录可以作为 prior execution receipt。失败的 attempt、解析
错误或调度错误不是执行回执，不能被放入“已完成事实”污染下一轮判断。普通聊天中的规则改进请求
也不依赖 Feedback 页面记录；只有上下文明示 `feedback_key` / `batch_id` 时才进入反馈处理轮次。

OA 判断以当前节点的实际表单为边界：不存在于当前表单的字段不能被判为必填，后续阶段字段也
不能提前阻塞当前审批。该规则同时进入 Consumer 候选契约和 Audit 复核契约。

### OA 审批扫描与决策（2026-09-17 重写）

扫描规则：

- **`dws oa approval list-pending` 的行在 `result.values`。** 解析器曾只认 `list` / `items` /
  `processInstances` / `processInstanceList`，因此每一轮都解析出空队列，而钉钉里审批仍在等待，
  最久的挂了两个多月。cursor 里 `seen_process_instance_ids` 为空表示**解析失败**，不是"没有待办"。
- **本人的评论不是决策。** 指纹曾在"最新一条操作记录属于本人"时判定审批已处理；分身的审阅评论
  是以本人身份发出的，于是它自己的评论让审批对扫描器永久隐形。`ADD_REMARK` / `COMMENT`
  不终结指纹计算。
- **每条审批一个会话，且每次扫描都从干净会话开始。** 会话按 conversation id 复用，早期所有审批
  共用 `oa_pending_scan`，一个会话里第二条起零工具调用、直接复述上一条的结论，其中一次谎报
  "已执行通过"；改为一条审批一个 conversation id 后，同一条审批的重跑仍会续用自己的旧 transcript，
  因此入队时清空该 conversation 的全部路线会话（`clear_conversation_runtime_sessions`）。
- **唤醒条件**：已经评论过的审批不再按天唤醒，等指纹变动——指纹本就排除本人记录，所以只有他人
  评论或操作才会叫醒它；**没有评论过的**保留每日重看，因为那种是静默丢掉的，没有别的机制能捞回来。
- 扫描任务绑定通用 `dingtalk-oa-approval` 与适用的 Stardust 业务 Skills。曾把
  `dingtalk-misc/references/oa.md` 当作审批规则来源，导致我们自己的审批规则从未进入模型；
  官方技能还会被 `dws upgrade` 覆盖，规则写在那里留不住。

决策规则在审批 Skill 里，不在代码里：通用的完整决策表、
`information_completeness` / `rule_coverage` 评分口径、退回优先于评论搁置、拒绝前必须先查
`revert-activities`、`--remark` 必填，都在 `dingtalk-oa-approval` 中。OA 定时任务冻结绑定
`dingtalk-oa-approval` 以及 Stardust 财务、立项、合同、人员、考勤/出差、云资源六个业务 Skill，本机任务的
Prompt 另写入负责人的个人规则（只存在该定时任务的数据库记录里，代码默认值不含）。背景参考文档不由审批 Agent 读取，代码与默认 Prompt 都不引用它们。Consumer 按 live `processCode` 与表单事实
分类，交叉事项组合适用类别；财务规则卡仅约束登记的财务模板。适用业务 Skill 必须覆盖
当前事项的规则条件、例外、权限和动作映射，适用 Skill 完整覆盖时才允许 `rule_coverage=1.0`；其他情况
低于 1.0，规则缺口 `needs_human`，不得自动批准/拒绝。申请人可补材料但不能关闭并存的政策升级。
六份 Stardust Skill 都只存在于运行时目录，没有仓库副本，按 `RUNTIME_ONLY_VERSIONED_SKILL_NAMES`
版本化，**不得带 `metadata.managed_by` 标记**——操作 Skill 目录会拒绝带标记的文件，定时任务
也就不能选择它们。Derek 的个人规则仅写在 OA 定时任务 Prompt，不得推广为公司规则。

### 没有 runtime schema 的 DWS 写操作

`oa approval revert-task` 和 `revert-activities` 在 DWS 的 runtime schema 里不存在
（上游 issue #1406），同族的 `reject`、`redirect-task` 正常。命令功能完好，但两道闸口都依赖
schema 判断写操作，因此各自失效过一次：

- **执行闸**：`_execute_reviewed` 判 `agent_cli_command_unreviewed`，退回命令到不了钉钉，
  "退回优先"这条规则写下后一次都没执行过。修法是登记到 `config/mcp-tool-effects.json`
  的受控写名单（`6b6b9081`）。
- **证据闸**：`dingtalk_send_evidence` 的分类器同样认不出它，退回真的执行了、钉钉记录
  `REDIRECT_PROCESS` 且待办减少，任务仍被判 `provider_receipt_missing`。修法是让证据闸
  使用同一份登记表兜底（`ef6f4de4`）。

**新登记一个没有 schema 的写操作时，两处都要能认出它**，否则服务会允许一个动作、然后拒绝相信它发生过。

## 外部动作幂等与依赖

每个 ProposedAction 必须包含稳定的 `action_identity`。服务使用
`business_object_key + action_identity + operation + target` 生成
`external_action_key`：

- provider 成功结果按该键原子保存一次；
- feedback revision 或新 agent run 再遇到该键时直接复用，不调用 provider；
- 一个 proposal 内只有所有较小 action index 都成功后，下一个动作才可 dispatch；
- 审批动作失败时，后续“审批成功”通知因此不会发送；
- 消息成功统一写入一条 `sent_replies`，其他 run 只追加 observer 关联。

钉钉消息 proposal 的 target 只接受服务 wire 字段：群聊 `conversation_id`，引用回复
`conversation_id + message_id`，单聊使用稳定接收人 ID。Provider 的字段名只保留在 provider
结果中。成功结果必须带 `action_identity` 和稳定 provider 消息 ID；运行时以该身份关联唯一 action，
原子写入 provider 结果、消息投影和 observer。

投递方式由 `dingtalk_chat_delivery(operation)` 一处判定：operation 名里含 `reply` 即引用回复，
含 `group` 即群发，其余按单聊。契约校验、正文准备和 Audit 执行都只问这一个函数。Consumer 对同一
动作有二十多种写法（`reply`、`messages-reply`、`reply_to_message`……），任务 384694 用
`reply_to_message` 提了一条群内回复，执行端只认三种写法，Audit 连续六次被拒为
`dingtalk_message_action_unsupported`。引用回复仍然只能回到本任务自己的会话和触发消息。

typed result 里的这些字段命名一次外发，但不构成它发生过的证据：它们由做出该声称的同一轮写出，
其中 `delivery_key` 和 `external_action_key` 本就是服务在 prompt 里交给它的，回显不证明任何事。
证据是 provider 接受副作用时返回的回执（`openTaskId` / `openMessageId`），它只会出现在运行时
记录的调用流 `agent_run_events` 里，由服务而非模型写入。

因此，当被接受的 proposal 里存在一个携带外发正文的 `dingtalk-chat` 动作时，Audit 的 `executed`
要求本轮调用流里至少有一个 provider 回执；没有则该 result 无效，模型在下一轮收到纠正，而不是
让任务带着没有依据的成功收口。消息投影同样以该回执为前提。

回执只回答“副作用是否发生”。至于 typed result 用哪个 ID 标记它，允许来自读回会话——真实发送后
用 `+chat-messages` 认出自己那条消息是正常做法，要求上报 ID 必须等于回执会拦下已送达的消息，
而重试等于再发一次。

日程响应、审批、表情等动作的 provider 身份不是这个回执形状，它们维持既有约定，直到各自有可核验
的回执为止。

Agent 生成的钉钉候选正文在进入 Audit 前按 `execution_generation + proposal_revision` 持久化：
同一 revision 的重试复用同一准备正文，反馈产生的下一 revision 则持久化修正后的正文，不能被
第一版正文覆盖。这个准备键不替代跨 revision 稳定的 `external_action_key`；已有 provider 成功
结果时仍直接复用，不得重复发送。

### 送达只在发送当场记录一次（`721b7f58`）

**执行发送的那一轮，在当场写下这条送达，内容是 provider 的真实返回。写不成就是没发成——
不补、不猜、不重建。** 服务里不再有任何重建路径：`_repair_completed_message_delivery_projections`、
它的候选查询、结果再水合，以及 `provider_effect_reconcile` 模块都已删除。

删除的理由是这些重建本身制造了故障：它挂在生产从不调用的 `consume_once` 上，从未在服务里运行过；
候选 SQL 漏了 `dws chat +dm` 只返回的 `open_task_id`，真实送达永远选不中；它把模型自述的
`delivery_status: "sent"` 当成送达，写出过七行「History 显示发过、实际没发」的幻影。
而它存在的唯一理由，是实时写入长期不工作——`_audit_terminal` 构造的结果不带 `consumer_result`，
投影第一道门就返回 None，直到 `de176316` 才修好。

由此产生一个**有意保留的缺口**：实时写入失败就永久没有记录，没有兜底。唯一会告诉我们实时路径坏了的，
是 quality gate 的 `executed_without_record`——它报告「报了 executed、有 provider 回执、却没有送达记录」，
**只报告，不自动补写**。看到它报警要去查实时写入路径，不要把记录补回去：用重建填补一次本该失败的发送，
正是那七行幻影的成因。

非 Agent 发送（如 OKR 周报）由发送它的那条命令自己记录，同一原则；微信有自己的 `wechat_deliveries`。
`sent_replies` 里 2026-09 之前的记录早于运行模型，只读保留，不迁移也不改写。

这里只管理稳定身份、动作顺序和 provider 成功事实。应用层不检查 Agent 使用了哪些工具，也不建立
read-only、unknown 或 reconciliation 状态机：回执核验是对已记录调用流的一次读取，不是新的状态机，
它只回答“这一轮是否拿到过 provider 回执”，不评判 Agent 选择了什么路径去拿。

## 两道效果检查的共同盲区：自带回执的领域

服务有两道检查判断"外部动作到底发生了没有"，它们从两个方向问同一件事：

- `claims_external_action_without_tools`（`app/agent_effect_claim.py`）问"这一轮到底
  调过 provider 没有"：结果自称已执行、整代次却零工具调用，就打回纠正。
- `DingTalkSendEvidenceDriver`（`app/dingtalk_send_evidence.py`）问"效果有没有落到
  提案指定的对象上"：`executed` 必须有 provider 回执或被原生 CLI 元数据判定为写入的调用。

**两道检查在同一种情况下都是瞎的**：某个领域自己驱动客户端、自己记回执，服务看不到
工具事件。已知两例：

- **邮件退订**由受审浏览器驱动执行并写自己的回执，所以审核轮如实报告"已执行"时，
  整代次没有任何工具事件。第一道检查因此误报，把整条端到端流程打挂（`aa252e9d` →
  `fddac656` 按渠道收窄，排除 email）。
- **第三方 MCP 服务**（如面试系统）不在原生 CLI 的 schema 里，第二道检查无法区分读写，
  因此对它们不做判定；能人工整理的目录记在 `data/config/mcp-tool-effects.json`。

**新增渠道或领域时**：只要它自带客户端和回执，两道检查都要同时加豁免，而且**任何一道
都不会自己发现遗漏**——表现是正当动作被判为"没发生"，然后走纠正轮、耗尽轮次或整条流程失败。

### 第三种盲区：解析器读不懂那条命令

判定「是否写入」要先把 shell 命令解析成 argv，而 `native_command_argv` 对**任何含 shell 操作符的命令
一律拒绝解析**（这在决定「能不能执行」时是对的，不要放松它）。模型很爱在命令后面加 `2>&1` 或
`| head`，于是真实执行过的写入被读成「什么都没有」：一次 OA 批准（run 19444，钉钉已记录
`REDIRECT_PROCESS`）和一次日程接受（task 383208）都因此被判成从未发生，其中一条还据此对同事说了
不实的话。

证据侧因此只取管道第一段并丢掉文件描述符重定向（`dws … 2>&1 | head` 里真正执行的是 `dws …`），
命令替换 `$(...)` 仍然拒绝——那时文本已经不能说明执行了什么。

**下判断前先确认解析器读得懂那条命令**：结论「没执行」和结论「解析不了」是两回事，把后者当成前者，
就会把真实发生过的不可逆动作记成没发生。

## 用户反馈处理轮次

用户反馈处理有自己的当前投影：

```text
pending -> processing -> resolved -> pending
```

它不使用普通 Agent 任务的 `running` / `done` 状态。其中 `pending` 是待领取，
`processing` 是已被唯一批次领取，`resolved` 是当前处理轮次完整结案。
从 `resolved` 重新打开时，本地 API 必须收到不为空的原始 `reason`。该转换只把
当前投影返回 `pending`，记录转换和理由，不创建新轮次。

之后的 claim 才会创建新批次和下一个处理轮次。每个轮次的 Workbench 任务/
turn、attempt/run、commit 和测试/重启/健康证据都归属该轮次。新轮次以空关联和
空证据开始；旧批次、旧轮次和旧回执始终不可变，也不参与新轮次的结案。

只有当前 `processing` 轮次可以接收关联或证据 PATCH。resolution receipt 按已持久化的
feedback-iteration decision scope 校验，而不是对所有修复一律要求 commit：`skill_only` 要求
精确 revision/SHA、active config、匹配的启动 load receipt、场景验证、health 和零 backlog；
`runtime_config` 要求前后 config、目标 receipt、场景验证、health 和零 backlog；`code` 要求
本地 `main` 祖先 commit、测试、不同 PID 的重启、health 和零 backlog；`mixed` 同时满足相应
code 与 Skill/config 条件。`needs_human` 不接受 resolution receipt，项目保持 open。任一适用
条件不满足时，整个批次保持 `processing`，不部分结案。关联、证据和结果必须通过 Feedback API
持久化并回读一致。

Feedback API 是现有本地后端边界内的操作接口，供 Workbench 和仓库 Agent 共用；它不对
公网暴露，也不增加 feedback 专用鉴权或第二套 Agent 流程。

### Email task 的运行边界

> **实现与部署状态：** Email folder classifier 与 audited-v2 lifecycle 已合入 `main`；本机
> launchd 的独立 Email worker 已启用，并通过真实 IMAP 可逆验证。SMTP 与自动回复仍禁用。

Email 的分类确认不是 Agent 运行，也不会创建通用 `reply_task`。邮箱服务器中的当前文件夹是
类别的唯一事实来源：业务文件夹映射为相应类别，Inbox 表示未分类，Spam/Trash 映射为内部
`junk`，Sent/Draft 排除。`important` 是与类别正交的标记，由 provider Starred/Important/Flagged
与成熟模型信号取并集；junk 不允许 important。确定性 Email 动作清单是
`label`、`mark_read`、`archive`、`move`、`trash`；独立 Email worker 领取并执行这些动作，
随后读取 provider 状态确认结果。这些确定性动作
不创建 CEO Agent task，也不创建 Consumer/Audit run。`trash` 只能执行可恢复的 move-to-Trash；
永久删除、IMAP `EXPUNGE`
和清空 Trash 不存在可调用路径。

IMAP 移动模式是账号级显式配置：默认 `imap_move_mode=move` 并要求服务端支持 `UID MOVE`；
只有 provider 官方协议把 `UID COPY` 定义为移动时才配置 `copy_as_move`。运行时不按 hostname
自动推断，不对普通 COPY 执行 `\\Deleted`/`EXPUNGE` 补偿；动作完成后统一用稳定 Message-ID
重新定位，确认邮件只存在于目标文件夹并读取新的 UID/UIDVALIDITY。

只有确认后不可变 `ActionPlan` 中授权的 `unsubscribe` 创建 `channel=email` 的
`reply_task`，生命周期固定为 `email_unsubscribe_audited_v2`；其他动作和零动作计划不创建
task。`auto_reply`、SMTP 和 `mailto` 发送全部禁用，不能由配置、分类结果、人工确认或 Agent
生成，也不能作为退订 fallback。

Email action task 的去重身份由以下四项确定：

```text
account_id + stable_message_identity + action_type + action_plan_version
```

账户与稳定 thread 身份确定 `conversation_id`，上述动作身份确定
`trigger_message_id`，继续依赖现有 `(channel, conversation_id, trigger_message_id)`
唯一约束。幂等重放返回已有任务，不重置其状态或 execution generation。

Adapter 只创建 `pending` task 和受限上下文，不直接发送邮件、打开网页或写入新的任务
状态。邮件正文和 thread 纯文本在运行时上下文中可用；附件是 metadata-only，没有
image/content material，只包含文件名、MIME、字节大小、数量和 inline 标记，没有读取命令
或 image path。任何组件都不得下载、打开、OCR、解析、总结或推断附件正文。持久 trigger payload 不包含凭证、附件内容、本地路径、
完整私密 URL 或 query token。

退订不做结构化审核。Email worker 直接执行兼容调用语义 `unsubscribe_email(task_id)`：一次调用完成整件事——
打开 ActionPlan 已授权的 entry，按页面当场呈现的控件操作，直到第一个终态页面，然后返回
outcome 和脱敏后的页面原文。它只接受 task id 这一个调用方无法伪造的参数，其余全部从 durable 状态
读出，所以没有 proposal 要抄写、没有 acceptance 要绑定、没有 Audit turn，也没有 continuation 要续。

幂等性只靠 receipt：`email_unsubscribe_receipts` 里每个动作身份一条，已有 receipt 时再调一次
只会把它原样返回，不会重复退订。这取代了原先的 claim 租约、effect digest 链、owner fence 和
Consumer→Audit 往返——退订在真实世界本来就是幂等的。同理，有浏览器步骤但没有 receipt
不再升级为 needs_human，重跑一次即可。历史 run、session、step、receipt 和失败事实保持不可变。

已经创建的退订任务重建上下文时，必须使用任务中保存的不可变、脱敏 ActionPlan 载荷；浏览器入口
只在创建任务时从邮件正文中选择一次。Provider 后续重排正文、移除 header 或无法读取当前邮件时，
不得重新选择入口并改变已有授权。已有终态 receipt 的新 Consumer/Audit 执行只读取该 receipt，
形成新的明确成功 run，不重新打开浏览器或发送新的外部请求。

每个邮箱账户有两个独立回溯窗口：Agent 默认 30 天，模型默认 365 天。`email-message-check-once`
按当前 runtime 模式只运行其中一条路径：无上线模型时运行 Agent 窗口并遵守账户的“仅未读/全部”
设置；模型上线后只运行模型窗口，优先覆盖全部尚无稳定记录的已读和未读 Inbox/未绑定来源邮件，不
按日期把近期邮件留给 Agent，也不在同一轮创建 Agent 分类任务。模型路径以每文件夹固定批量
在后续定时轮次继续向历史推进。模型 accepted 结果进入现有不可变 ActionPlan 和 provider action
队列；`model_rejected`、`model_others` 与 `model_category_not_promoted` 保存完整预测证据为
`pending_feedback`，不创建 Agent 分类任务和动作计划。Embedding、runtime 或持久化技术失败使本轮
失败并在下轮重试，不写人工待确认。定时历史模型 runtime 的 Embedding 请求期限为 120 秒，实时
调用仍是 2 秒，避免批处理复用实时延迟预算后在同一封历史邮件上永久超时。冻结训练 snapshot 直接采用 provider 文件夹和 important 信号，
训练与 shadow 评估均为离线、阶段性作业，不在收信路径实时训练或并行推理；全量线上模型仍需连续两个兼容版本对
全部类别和 important 都达标且没有未解决的系统性错误。整个模型晋升后，实时新邮件主路径严格按
`model -> Agent fallback` 顺序执行；该旧接口不用于定时收信和历史回填。定时路径中的模型拒绝直接
进入待确认，技术失败则重试，不调用 Agent。两个连续候选必须分别绑定不同且时间递增的冻结 snapshot；snapshot digest 必须不同，
folder/important 累计标签水位以及至少一项独立评估样本或组证据必须前进。同一 snapshot 的重复训练
不能满足晋升。

Provider 训练观察按有界批次运行。观察缓存与请求队列使用各自独立的进程锁和文件锁；长时间 IMAP
扫描不得阻塞实时扫描、分类结果落库或确定性邮箱动作。Email worker 只有在扫描/动作、Agent
consumer、训练三个组件都至少成功完成一轮后才发布 `ready`。

业务类别移动完成后在变更后的 locator 上执行 flag/read 动作并回读；用户在 provider 中再次移动
邮件时，下一份 snapshot 立即以该文件夹作为训练标签。旧的 staged/manual 历史评测入口仍用于候选
验证；生产历史整理由上述定时模型窗口自动、小批量、可恢复地推进，不会一次性读取整个邮箱。
`junk` 先由代码发现标准退订候选；只有 unsubscribe 进入 Consumer/Audit
网页流程，最终再移动到系统 Trash。连接邮箱 OTP 仅允许站点、收件人、挑战上下文和时间窗全部
匹配的临时读取；普通 CAPTCHA 在隔离 profile 中有限尝试，不能完成的密码/MFA/CAPTCHA 保存不含
秘密的有界 continuation 并交给用户，恢复时不重放已经审计的 operation prefix。

只读 Console API 提供当前 provider-derived category 或 unavailable、文件夹绑定、描述/训练 snapshot
版本、样本与组数量、逐类别历史资格、连续晋升证据、active mode/model、时延与 fallback、动作回读
和退订 continuation。分类列表不暴露完整正文；用户打开单封邮件时，详情接口返回已持久化的文本
正文及收件人信息。API 不暴露完整退订 URL、OTP、secret、raw embedding、附件字节或浏览器 session
私密数据。外部配置的 embedding model/revision 不原样投影，也不返回可被离线枚举的
摘要；API 仅返回固定受控占位符。canonical model/snapshot ID 则按生产构造格式校验。当前 category
来自扫描进程持续写入的 latest-provider-observation 投影，
不读取冻结 snapshot，API 也不会额外连接邮箱；冻结数据只以 training snapshot 字段展示。SMTP、
自动回复和所有 reply/send 路径继续不可达。

### Email folder classifier live verification

Console 的“模型训练”提供版本化门槛配置及显式主模型开关。四项默认值为 Macro F1 0.95、
逐类别 Precision 0.95、逐类别独立测试样本 20、常驻端到端 P95 500ms；缺失测量不能达标。
达标显示红点，用户操作开关才调用 runtime-mode API。运行状态和切换身份/历史在同一个
online-active.json 中原子提交，expected mode/model 冲突返回 409；关闭模型保留证据和历史。
Registry 锁串行化模式切换，提交期间配置写锁保持当前描述和门槛不变。模式改变由 worker tick
读取生效。门槛和类别描述使用 schema 34 的追加版本记录；线上数据库升级前执行并验证 SQLite
在线备份。代码回滚至 schema 33 不能直接打开升级后的库，须使用已验证的升级前备份。

训练证据区分 head latency 和端到端 latency。趋势图只连接同一评测协议、独立测试集摘要和
类别集合的版本；旧版本缺少这些字段时显示不可比较。单个损坏的 staged 文件仍显示异常条目，
其他健康版本继续展示，晋升判定保留损坏事实。

Embedding 训练观察与线上预测共用 provider 字段映射和 canonical JSON 序列化；正文（含引用）、
允许的邮件头及附件元数据必须产生相同输入和缓存键。旧 TF-IDF 的分词文本不能冒充该输入
schema。候选计时从已读取邮件的输入构建开始，覆盖预热后的缓存查询、远端 Embedding 和输出头，
不包含邮箱网络读取或分类后的外部动作；远端未命中路径和缓存命中路径分别记录。无法验证输入
一致性、缺少 Embedding 配置或测量失败时记录未测量原因，不生成可晋升的延迟数值。

Live checks are opt-in and excluded from normal test runs. The mailbox check requires a
designated reversible message plus `CEO_LIVE_EMAIL_FOLDER_CLASSIFIER_E2E=1`,
`CEO_LIVE_EMAIL_IMAP_HOST`, `CEO_LIVE_EMAIL_IMAP_PORT` (default 993),
`CEO_LIVE_EMAIL_IMAP_USERNAME`, `CEO_LIVE_EMAIL_IMAP_PASSWORD`,
`CEO_LIVE_EMAIL_IMAP_MOVE_MODE` (default `move`; use `copy_as_move` only for a verified provider),
`CEO_LIVE_EMAIL_ACCOUNT_ID`, `CEO_LIVE_EMAIL_MESSAGE_UID`,
`CEO_LIVE_EMAIL_MESSAGE_UIDVALIDITY`, `CEO_LIVE_EMAIL_MESSAGE_ID`,
`CEO_LIVE_EMAIL_LOCATOR_FOLDER`, and `CEO_LIVE_EMAIL_TEST_FOLDER`. UID/folder variables are only
locator hints: before any write, the test freezes the provider-derived folder, UIDVALIDITY, UID,
Message-ID, and the complete persistent IMAP FLAGS set, including keywords/provider labels (the
non-persistent `\\Recent` session flag is excluded). The test simulates a provider/user-side move with
a raw IMAP operation outside the service executor, then freezes an observation snapshot and proves the
new folder-derived category. In `finally` it re-locates the designated message by stable Message-ID
across allowed folders, restores the folder and exact persistent flag set, and verifies every property
again through a fresh connection. A MOVE whose returned locator/readback fails is treated as possibly
applied: compensation re-locates before any further write. If exact restoration cannot be proved, it
fails with the stable identity and current locator for manual recovery. It never configures SMTP or
calls send/reply.

The GPU4 check requires `CEO_LIVE_EMAIL_EMBEDDING_E2E=1` plus the normal
`EmailEmbeddingClient.from_environment` endpoint and authentication variables. The cached check
runs the same normalized message through the warmed production `PromotedEmailClassifierRuntime`,
including normalization, cache lookup, description-aware MLP head, and result validation, and proves
cache hits before requiring P95 below 100 ms. Distinct uncached GPU requests require P95 below 500 ms.
Without explicit opt-in, both live checks skip without external mutation.

Run both opt-in checks only with a designated reversible message and GPU4 configured:

```bash
CEO_LIVE_EMAIL_FOLDER_CLASSIFIER_E2E=1 CEO_LIVE_EMAIL_EMBEDDING_E2E=1 \
pytest --run-live -q -m live tests/test_email_folder_classifier_e2e.py tests/test_email_embedding_client.py
```

## DingTalk / WeChat 统一外发后缀
### 只能发服务准备好的正文

Derek，2026-09-18：外发消息的正文必须是服务为该动作准备好的那一份。Agent 自己编正文发出去
会绕过签名、反馈链接、幂等键和投递台账——实测它把提示词里的反馈链接手抄进正文并抄坏了编码，
同一条修复又抄坏一次。执行轮发送时若正文不是服务准备的，按结果契约违规处理，走普通纠正轮
（`app/outbound_text_authority.py`）。提案正文里也不得自带反馈链接或签名，服务会自己追加。


所有服务所有的 DingTalk、WeChat 人员可见文本，在 provider 调用前都必须由
`ServiceMessageSender` 生成并持久化 `PreparedOutboundMessage`。持久化键是
`(channel, delivery_key)`；首次准备确定最终正文、feedback token 和后缀版本，之后的重试、恢复、
发送回读与 WeChat 撤回只能复用该记录，不得根据当前配置重新生成。provider adapter 只接收已持久化
的最终正文，业务模块直接调用原始发送方法会被架构测试拒绝。

DingTalk provider 将该最终正文通过 `dws chat +messages-send --as user --markdown` 发送，使 Markdown
标题、列表和反馈链接由钉钉按 Markdown 渲染。回复触发消息用钉钉原生引用回复（`+messages-reply --content`），正文同样按 Markdown 渲染：2026-09-25 在Derek 自己的会话实测，标题、加粗、行内代码、无序与有序列表都正常显示，相邻行的列表项也分行——DWS 帮助里「普通引用按纯文本解释」与实际不符，读回接口把列表拼成一行只是读回的呈现。推送横幅标题去掉 Markdown 标记。群聊、单聊仍使用稳定的 conversation/user/openDingTalk ID；
delivery UUID、结构化群 @ 及 `ServiceMessageSender` 准备的签名与反馈链接保持原样随正文发送。

Consumer 修订版可以原样复用上一 revision 中已持久化的服务反馈链接。敏感值校验只会对配置域名、
固定回调路径、有效签名和成对反馈 token 完全匹配的服务链接做占位化处理；真实凭证、陌生域名、
畸形回调或其他敏感值仍然必须使该 run 失败。
新生成的反馈回调 URL 只携带不透明 feedback token、评分和 attempt ID，不得把触发消息或回复正文写入
URL 查询参数。外部反馈页可以收集评分，但不能通过链接、浏览器历史或访问日志获得内部消息文本。
未持久化的旧格式正文若已包含有效的服务反馈链接，外发准备阶段保留原 token 和消息正文，重新生成
不含正文参数的链接；已持久化的历史投递回执不改写。

这是一条机械传输边界，不是新的业务审核或授权规则。Email 仍是独立的非发送通道：SMTP、自动回复
和 `mailto` 均保持禁用，不受此 DingTalk / WeChat 后缀机制影响。

## 统一禁止事项

- 所有任务都不得使用 `discard` 动作。
- 所有任务都不得写入 `discarded` 状态。
- 不得用“丢弃”代替审核反馈、修正原 run、重新排队、人工升级或失败记录。
- 业务 `reply_attempt` 的 current projection 可以由重跑更新；同一 business object 复用原 attempt ID，
  不创建新的业务 attempt。原始失败作为 append-only state event 保留。其下的 `agent_runs`、proposal
  版本、revision lineage、session、runtime attempt 和 tool event 不得覆盖；attempt 页面可切换查看
  这些底层 run。`business_object_key`、`action_identity`、`operation`、`target` 和 provider
  成功结果是避免重放所需的最小持久化事实。
- 已成功的消息发送或 provider 回执不会因为 attempt 后来失败而失效。下一轮上下文直接读取这些
  append-only 执行事实，不能把解析失败、服务重启或 current projection 失败理解成“尚未发送”。
- Audit 返回 `executed` 后任务即可进入 `done`；外部结果的读取与判断由 Agent 按业务 Skill 完成。

如果任务确定无需执行，应进入 `done`，并在 trace 写入 `agent_output/no_action`；如果结果需要修改，写入 `audit_feedback` 并保持 `running`；如果处理失败，应进入 `failed`。

任务 Agent 的 `memory_recall_used` 是 Agent 提供的上下文记录，不是服务工具调用验收条件。可用时应以聚焦
查询读取稳定背景；当前身份和业务事实仍需依赖相应来源/实时读取，memory 不能作为任务存在、指派授权、
负责人 ID 或接受承诺的证明。

邮件退订任务把不可变 ActionPlan 派生出的首步 `ProposedAction` 作为任务绑定契约直接提供给 Consumer。该动作固定使用 `email_browser/unsubscribe`、精确目标字段和 `operations` payload；内部 `classification_id` 不进入外部工具参数，避免 64 位标识经 JSON 数值链路发生精度变化。
审计 `proposal_revision` 只表示反馈修订轮次，退订 `operations` 的长度只表示浏览器步骤；两者独立计数。首步动作经过审计反馈后仍可在更高 revision 执行，不能被误判为缺少后续浏览器步骤。
旧版本若在浏览器预检查阶段失败并错误留下 `uncertain/effect_uncertain` claim，可通过显式恢复命令释放，但必须精确绑定失败 Audit，且确认没有浏览器步骤、完成记录、续跑记录或多个 effect；释放后仍需单独发起正式任务重试。
当前退订执行只投影明确成功或失败。浏览器动作返回失败且没有持久化步骤、完成记录或续跑记录时，释放本轮 claim 并交给统一重试；不得先写入不可重试的中间状态再让下一次 Audit 撞上 claim 冲突。
退订浏览器仅把固定的内部失败类别投影到错误码；已识别的导航超时和页面状态缺失必须与兜底 `email_unsubscribe_browser_failed` 区分，错误码、步骤日志和页面原文里不得写入 URL 或凭证。唯一例外是 `email_unsubscribe_receipts.entry_url`：该列按 Derek 的明确要求保存这次实际打开的完整私密 URL（含 query 与 token），用于人工复现同一个退订入口。写入前校验它的 sha256 等于 `entry_reference` 的摘要，因此不能与生命周期认定的身份漂移；它不经过 `assert_no_credentials`，因为被保存的正是那类 token。打开该 URL 会真实执行退订，任何能读这张表或这个页面的人都能替当事人退订。该列只在本次变更之后产生的 receipt 上有值，历史行为空且无法补全。退订浏览器不再对页面发出的网络请求做 origin 白名单、跳转或资源家族限制。
Google Workspace 邮件使用的 `c.gle` 短入口只允许桥接到 `google.com` provider family；该精确映射不能作为通用短链放行规则。
Google 退订页面只允许从 `google.com` 和 `gstatic.com` provider dependency family 加载 HTTPS 公网资源；页面中的普通跨站链接不进入许可集合，仍在请求发出前拒绝。
Consumer 或 Audit 在同一 proposal revision 内耗尽统一重试 ceiling 后，编排结果不得返回 `failed_retryable` 让外层重新进入同一 generation 并无限增加 `turn_attempt`。普通执行/依赖失败进入 `failed_terminal` 并保留最后一个 run 的真实根因；如果 Audit 的内容反馈轮次耗尽且最后一次 Audit 保留了具体修改意见，编排结果进入 `needs_human`，提供“按审计意见修订”或“停止不执行”两个明确选择，避免把可继续处理的审计修订误投影为 opaque failed。

## 周期性工作的归属

Derek，2026-09-18：**后台周期性工作必须是定时任务**，在控制台里可见、可开关。不允许把
干活的循环藏在服务进程里，也不允许靠手工 CLI 命令批量灌入。

- 被删除：工作区文件全量扫描。它把工作区里每个 `.md`/`.txt` 当作工作材料、每个文件跑一次
  Agent；一个 1475 文件的头脑风暴目录排了 564 条，一小时里 109 次运行调用有 105 次在读这些
  过程稿。`scan-task-sources` 现在只读 AI 听记。
- 并入定时任务「同步会议结论与管理者视角」：会议 Memory 写入的**执行**（入队本来就在这里），
  以及内部维护（错误自动收口、僵死任务锁回收、待办完成核查）。
- 独立定时任务「投递到期的跟进事项」（每 5 分钟）：原先是每 60 秒的隐藏循环。
- 任务长期记忆写入**不是**定时任务（Derek 2026-09-24：「写入记忆不应该是个定时任务，而是系统层
  自动的」）：任务结束时在 `finalize_orchestrated_reply_task` 同一事务里入队，由统一 Dispatcher 的
  `task_memory_write` adapter 立即领取写入；退避重试的行到点再被领取，重试上限后进 Attention。
  见 `docs/architecture.md`「任务长期记忆」。
- 仍为常驻循环的只有 `meeting-delivery`：它只投递已审核通过的会议结论，10 秒一轮就是它的意义；
  以及备份、cron 调度/派发、探针等不产生业务判断的基础设施。

任务卡死的回收有两条路径：服务启动时回收所有仍标 `processing` 的任务（启动时本进程什么都没在跑，
这类任务必然是孤儿），以及维护步骤每分钟回收锁超过 15 分钟且没有活运行的任务。

## 进程、租约和恢复

- 生产入口是 launchd 管理的 `com.ceo-agent-service.main`，由 supervisor 管理 worker 和 audit-web。
- 同一 `conversation_id` 同时只能有一个执行 Agent 持有 Codex session lock。
- 每个执行/审核 run 都有独立 lease、revision 和 transcript 范围。
- Dispatcher 对“租约已过期但 owner 进程仍存活”的来源不重新领取，以免重复执行。处理函数已经返回、只是以错误结束（记下 lease error）时，dispatcher 会把该租约标成“owner 已不在”（`owner_pid=0`、清空到期时间，按 owner 与 generation 围栏），来源随即按常规规则可再领取。此前这种租约要等服务重启才释放：任务 384735 重排后空等了三十分钟。
- History 解析本地 Codex session 路径时，按 `session_path_index.jsonl` 的文件签名缓存最新索引
  记录；多个 retry/run 复用同一 session 不会重复解析完整索引。索引被 Codex 或维护程序更新后，
  文件签名变化会使缓存自动失效，因此页面不会因缓存遗漏新 session。
- History 从既有工具事件归纳已完成的 provider 写入时禁用原生 CLI schema 发现；详情读取只使用
  已记录的回执和注册写入定义，不启动 `dws schema` 或其他外部子进程。
- 已被恢复器终态化或被新 generation 替代的 run 视为 lease 丢失；旧执行线程不会把该状态记录成新的任务失败。
- 回复队列的 `processing` 有双重恢复边界：十分钟没有当前 generation 的运行心跳会被恢复；即使运行持续续租，单次队列处理超过一小时也会被释放，进入既有重试或终态路径，避免反馈循环无限占用队列。
- Work summary 输入按 `source_type + source_ref` 幂等入队；重复扫描只能更新同一输入的载荷，不能把已经 `failed` 或 `skipped` 的输入重新打开。需要恢复失败输入时必须调用显式重试入口，避免周期扫描把终态输入反复改回 `pending`。
- 重启时，未完成的 Agent turn 统一按 `failed` 重试；服务不创建 unknown 或独立状态核对队列，也不依据工具事件决定是否重放。下一次 Agent turn 按业务 Skill 读取当前外部状态，再自行判断后续动作。
- 会议发现只相信自己能用的东西。结束时间取详情接口的值：列表与详情描述的是同一段录制，两者从不是一致性信号，钉钉 2026-09-18 移除列表的 `endTime` 后更会相差一秒到四十一分钟，而下游只用它做五分钟时长门槛、投递沉淀延迟、四小时日历匹配窗口和标题里的时间段。录制状态同样不做判断：钉钉改报数字码（已结束的录制上 2 和 4 都出现过），而仍在录制的听记根本不进列表接口。发现窗口为一周——迟到一周的会议跟进对参会者已是噪音，窗口越宽，provider 返回内容的任何变化就越容易变成一次批量重发。

- 一场会议可能被分成几段录制，每段各有自己的 taskUuid。同名且相邻两段间隔不超过两小时算同一场会议（由间隔决定，不按自然日切分，跨零点的会议因此保持完整）。后到的录制并入已有任务：转写与摘要接到第一段之后，会议结束时间顺延到最后一段，任务重开并**对整场会议重新生成总结**，而不是补一段增量。此时标题标注「第二次总结」。只有发送前已保存了另一条旧消息的凭证，才会在新总结发送成功后撤回旧消息；首次发送以及同一条消息的日历备注重试都不撤回自身。provider 拒绝撤回不算会议失败。

- 会议总结在 Agent 决策前，以日历组织者的完整名称通过当前 DingTalk 组织目录解析稳定身份；只有唯一精确姓名或昵称命中才回填组织者及同名唯一参会人，供业务 fallback 与敏感私信共同使用。模糊、多候选或冲突身份不回填。日历中缺少稳定 ID 的其他参会人也按同一规则做唯一姓名/昵称补全，避免 HR 只有显示名而无法被识别。服务用当前 DWS 群搜索格式检索会议标题和摘要正文里的业务词，再读取候选群近期讨论，把去重后的实时候选及讨论片段交给 Agent；候选仍需核对受众与业务承接，不能仅凭群名、词项重合或参会人覆盖发送。群搜索失败或结果不完整时任务重试，不将依赖故障说成“找不到群”并私聊组织者。敏感内容按受众边界投递：如果 DWS 实时群发现证明目标是 HR 专属或已匹配的群，且讨论是会议中 HR 参会人的共同事项而非针对具体个人，Agent 可以把敏感详情与公开结论合并为一条 HR 群消息，并将敏感私信置空；针对具体个人、群受众不明确或含非授权成员时，仍发送去敏感的业务群消息与独立敏感私信，无法确认敏感收件人时发给当前负责人本人。进入投递后复用同一个持久化投递键：钉钉发送使用由该键确定的 UUID，provider 成功返回会立刻写入同一键的回执。服务重启后，恢复的 worker 先复用回执；若进程恰在 provider 接收后中断，使用相同 UUID 继续投递，provider 的重复 UUID 回应视为原投递已送达，不能产生第二条群消息。
- 会议跟进的钉钉消息标题取会议标题，不取群名或私聊收件人名；该标题只到达推送横幅和会话列表，聊天窗口里看不到，所以正文首行以 Markdown 一级标题（`# 会议标题`）写会议名，第二行以斜体呈现时间副标题（`*时间：…*`），二次总结另起斜体说明行。时间按 Asia/Shanghai 呈现：Minutes 交过来的时间戳是 UTC，直接格式化会把上午八点的会写成零点。Agent 的 `final_message` 只承载结论、行动和待确认事项，不重复会议标题和时间。正文用 Markdown 结构化：小节用加粗标签（如 **结论**、**后续行动**、**待确认**），多条并列事项用 `- ` 列表并在列表前空一行；钉钉这些消息以 `--markdown` 发送，加粗、标题和列表都按 Markdown 渲染（Derek 2026-09-24 确认渲染）。发送目标仍由已核实的受众决定，标题不参与路由或投递幂等键。
- 这一恢复规则适用于所有任务：普通服务重启只释放已经停止的 worker 租约，保留同一任务的执行代次、已准备消息和外部回执；恢复 worker 从这些事实继续。若运维明确完成了运行时/路由修复，则服务修复重试创建新的 execution generation 和新的 session 绑定，但仍复用同一业务对象及外部动作幂等事实；用户业务反馈重跑则按反馈闭环复用兼容 session。
- 外部动作的 operation、target 和 provider result identifier（若 provider 返回）会保留用于去重；缺少标识属于 provider/Agent 失败，不转换为额外状态。
- WeChat reader 由独立 launchd job 自动保持运行；worker 连续三次 IPC 超时后主动 kickstart 该 job，处理“进程仍在但 IPC 已卡住”的情况。worker 只恢复 reader 进程，不启动 WeChat 主应用，也不重放消息。
- OKR 无头来源启动使用进程锁；并发调用者取得锁后必须再次读取共享缓存，复用前一个调用刚刷新的认证信息，不能重复启动浏览器或把正常刷新误报为锁超时。锁等待上限覆盖一次完整刷新周期；来源命令仍由上层超时终止脚本及其临时 headless Chrome 子进程，不能遗留后台浏览器。
- OKR 无头来源在请求业务 API 前校验新捕获认证的有效期。专用浏览器会话过期时返回明确的 `okr_headless_session_expired` 服务错误，不得继续请求并把认证失败误报为周期不存在。
- 完整的周 OKR 流程（实时读取、全部管理者分析、文档发布、群消息发送）使用独立的全局 run lease。领取在 SQLite `BEGIN IMMEDIATE` 事务内完成，运行期间周期续租，结束后按 owner 释放；并发调度或人工恢复只能有一个进入流程，其他调用返回 `analysis_in_progress`。`last_attempt_at` 仅用于失败重试退避，不能作为长任务仍在运行的判断。进程异常退出后不再续租，租约到期即可由下一次调度恢复。
- 过期的单人周 OKR 分析如果已经被同一管理者更晚周期的成功分析覆盖，启动恢复将旧作业置为 `completed`，记录 `superseded_by_later_completed_week` 并清除租约。它不再显示为当前 `running`；若相同自然键缺少缓存，正式分析流程仍会重新领取。
- 每个维护步骤的失败投影到独立的 `task_maintenance.<step>` 服务健康组件，不改变任何业务任务状态。后续同一步骤成功时，该组件立即恢复为 healthy，并以该次成功运行收口同 kind 的历史服务错误。
- 周 OKR 分析任务每次获得新的租约时使用新的 runtime 执行代次。单次执行中的结果格式修正保持有界；已终态的旧代次不得阻断同一分析任务在后续租约中的重新执行。
- 周 OKR runtime 租约过期并已记录为技术失败时，启动恢复必须同步关闭仍为 `running` 的父分析任务；父任务不得在没有有效 runtime 所有者时继续显示执行中。
- 所有需要 `BEGIN IMMEDIATE` 的 Store 写路径统一经过同一个有界重试事务。短暂的 SQLite 写锁在 Store 内等待并重试；只有超过上限的持续锁才上升为服务错误。队列 claim、反馈批处理和恢复路径不得绕过这一规则。

### Cron trigger 与 Consumer Dispatcher

Agent Cron 保存任务定义及其结构化 Skill refs、首选 Runtime route/model/options、工作目录、Cron
和时区。Scheduler 每次只计算当前时间之后的最近触发点，不枚举停机窗口，所以没有 catch-up。
手动运行只追加一次 manual trigger，不改变 `next_run_at`。若上一轮关联 execution 尚未终态，
本轮以 `skipped` 和稳定原因结束，不等待后补。

一次正常到期分为两个可恢复阶段：`scheduled` adapter 领取 `scheduled_task_runs.pending`，在一个
事务中创建或复用唯一 `reply_tasks.channel=scheduled` 输入、保存 execution link，并把 trigger
标记 `dispatched`；`scheduled_execution` adapter 再领取该 execution source，按派发时冻结的
prompt、Skill protocol、首选 route、model、thinking 和 workdir 启动 Agent；首选线路失败时按统一
fallback 换到其余配置线路（Derek 2026-09-24，此前定时任务固定单线路、从不 fallback）。managed Skill
使用精确 revision；执行前若首选及其余线路都不可用、该 revision 或工作目录已经不可用，execution 以
`skipped` 收口并产生 Attention，不把业务结果写回 trigger。Scheduler 在派发前发现全部线路不可用时
同样记 `skipped`：配置性原因（未配置、缺能力、认证暂停）每次写 Attention，
provider 暂时不可用造成的路由暂停只留下 run 记录和路由暂停状态，不逐次写 Attention。

服务命令任务（`scheduled_tasks.command` 非空）只有第一阶段：`scheduled` adapter 领取 trigger
后，在同一个 claim 内直接运行服务命令目录中的实现（例如 `produce-once` 是 DingTalk 消息
producer 的一次增量读取，与 `app.cli produce-once` 相同），成功后把
`execution_kind=service_command`、`execution_id=<命令名>` 链接到 trigger 并标记 `dispatched`。
服务命令本身不经过 Runtime、Skill 或 synthetic scheduled Agent；只有命令发现真实对象后，
对应的 reply、meeting 或 work-summary Consumer 才会处理业务队列。上一轮仍未终态时，下一次
触发记为 `skipped`（`scheduled_task_previous_execution_active`），不并行执行，也不补跑。命令抛错时 trigger 以 `failed` 和
`scheduled_task_service_command_failed: <原因>` 收口；除依赖短暂不可达（DNS、网关繁忙、超时）
以外的原因每次写入 Attention。依赖不可达按持续时间判断：同一任务连续失败不足 15 分钟时只留在
trigger 记录里，一次外部抖动不会在每分钟的命令上刷出成串同样的条目；连续失败超过 15 分钟则写
一条 Attention（该条未解决期间不再重复写），下一次成功自动标记为已恢复。因此一次两小时的 DNS
中断是一条记录，而不是零条或一百二十条。命令幂等，claim 丢失后的
重领会直接重跑。命令名不在目录中时 Scheduler 在派发前以
`scheduled_task_service_command_unavailable` 跳过。服务命令任务不经过 Runtime、Skill、
Consumer 或 Audit，也不产生 reply task、agent run 或 reply_attempt。

`wechat-produce-once` 每 15 秒对唯一就绪的微信账号跑一次 producer。Reader 的可用性是
`wechat.reader` 健康事实而不是 trigger 失败：没有就绪账号时命令返回跳过摘要；Reader IPC 连续
失败 3 次时请求一次 Reader 重启、把健康标为 degraded 并只记录一条
`wechat_reader_unavailable`；App Data 权限缺失只记录一条 `wechat_data_permission_required`；
下一次成功读取恢复 healthy 并清除上述报告。其他异常才是命令失败，trigger 记 `failed`。

同一 Dispatcher 还通过独立 adapter 领取普通 reply、meeting、work summary、OKR review、
DingTalk Todo outbox 和任务长期记忆写入（`task_memory_write`）。adapter 只读写各自既有事实来源，并统一 claim generation、lease、唤醒、
公平性和容量；Consumer 保持领域边界。主动唤醒之外的有界等待只用于跨进程写入和异常恢复，不是
用户配置。Status 为每个实际 adapter 展示 pending、oldest、running 和 latest error；scheduler
进程/扫描健康与 scheduled run 的业务结果分别展示，空队列不会制造 Agent run。

默认业务生产任务通过稳定 migration key 幂等 seed，共十个：邮件、钉钉消息、钉钉日历邀请、每小时
`:30` 的“恢复近期 DingTalk 消息”、会议、微信 reader、OA、会议行动项、每周 OKR，以及每天
`20:00`（`Asia/Shanghai`）的 AI 听记同步。十个任务全部是服务命令，旧的 producer timing loops
已移除。以 Agent 形式创建的旧 `dingtalk-message-check-v1`、`wechat-message-check-v1`、会议、OA、工作来源和
`ceo-minutes-sync-daily-v1` 在启动时原地转换为命令形式（保留名称、Cron、时区和启用状态，已删除的不动）。
新安装创建的全部默认任务都是暂停状态，用户配好连接器后自行启用；seed 从不改变已有任务的启用状态
（Derek 2026-09-23）。AI 听记同步不再需要 Skill 判断：分页读取
摘要与逐字稿、写入本地归档、维护内容游标都由 `app/minutes_sync.py` 确定性完成，时长不足五分钟的
会议直接跳过。成功运行的归档命令单行结果摘要会持久化到 scheduled run，并显示在定时任务运行记录中，包含
`discovered`、`synced`、`skipped`、`permission_requested`、`permission_pending`、`failed` 计数及可操作的跳过明细。
另有一个每天 `19:30`（`Asia/Shanghai`）运行的“申请读不到的钉钉 AI 听记”服务命令：读取听记管理后台，逐条在听记页面提交访问申请，并以页面读回状态计数；对方批准后，`20:00` 的归档任务会读取内容。申请命令结果摘要同样持久化在 scheduled run，并显示在定时任务运行记录中，包括本次发现、已申请、已可读、申请人未解析、失败数量及会话剩余天数。OKR 周报同样是服务命令：它唯一的动作就是执行一条确定性命令，而那条命令的实时 OKR 读取会跑到五十分钟以上，任何 Agent 超时都装不下，被杀之后命令还会脱离运行记录继续跑。因此发现与同步类种子任务都不是 Agent 形式，Cron 在所有模型路由都不可用时仍然照常工作。只有两个报告是 Agent 种子任务，走标准 Consumer → Audit 生命周期：每周六 `12:00` 的“准备 CEO 管理周报”（`ceo-weekly-report`），和每天 `21:00` 的“发送 CEO 每日总结”（`ceo-daily-report`）。每日总结的必需输入全部来自服务自己的记录：Agent 先运行只读命令 `python -m app.cli daily-report-facts --scheduled-run <触发记录 id>`（`app/daily_report_facts.py`）。报告窗口由服务算：终点是本次触发的 `scheduled_for`，起点是同一任务报告日期更早、且真正发出了报告（执行任务 `done` 且最后一次处理结果为 `completed`；无需处理或线路不可用被跳过的也是 `done`，不算）的最近一次运行的触发时刻（断掉的日子并入下一份，同日重跑沿用原起点），从未成功过则回看 24 小时；报告日期是终点的北京日期。命令取得窗口内结束的会议及其已发会后跟进、当天有活动的 Task-first 业务 Task 及其当天事件、全部未解决的业务需关注项（`business_attention_items`）、当天在邮箱里被标为重要的邮件（已执行的 `flag_important` 动作；Agent 只摘有管理含义的，其余计数）、当天处理过的事项（不含 skipped，只计数），以及 Attention 口径下等 Derek 处理的事项；截止日期不从 `deadline_at` 自行推算（见 `docs/task-semantic-storage.md`）；再扫描窗口内群消息、按需补读听记，写成七段报告，发布到知识库“🎯  目标与执行”/“CEO 每日总结”下的同日文档（同日重跑覆盖同一篇）并读回，最后由机器人单聊把要点和链接发给 Derek。某个来源读不到只写进报告的覆盖说明，不向 Derek 追问材料。该命令登记在 `app/native_cli_metadata.py` 的服务只读命令中。Lark 没有
访问申请页需等待申请按钮实际可用；按钮仍禁用时保留为申请人未解析，下一轮继续尝试。页面仅显示加载壳或其他非权限内容时不能当作已可读，只有听记读取 API 能确认可读。
默认 seed。
内部投递、发送状态确认、错误恢复及 Todo completion follow-up 仍是内部机制，不外化为 Cron。

### 应用层边界

应用层不审核 Agent 使用的命令、MCP 工具、Skill、读写模式或工具名称，也不维护
`side_effect_state`、`unknown`、`reconciled` 等业务状态。应用层只校验最终 typed result 的形状，
推进 `done`、`failed`、`needs_feedback` 和 `needs_human`，并保存去重所需的最小外部事实：
`operation`、`target`、provider 稳定结果标识。纯读取不需要 receipt；写入中断时由下一次 Agent turn
按业务 Skill 读取目标状态，服务不启动专门的只读核对回合，也不因“未知工具”阻断执行。

Agent 也不得用嵌套的 `codex mcp list` 或 `codex exec` 子进程结果判断当前父 Agent session 的
MCP 是否注入；这类子进程只说明子 CLI 环境，不是当前 turn 的能力事实。需要 MCP 证据时必须在
当前 Agent session 中直接调用对应 MCP 工具；如果直接调用或 provider 操作失败，才可以把该具体
工具错误作为依赖失败返回。仅自报 `<server>_mcp_not_injected` 属于结果契约错误，会触发修正轮，
不能终结业务任务。

历史数据库升级时会移除 `agent_runs` 中的 `unknown`、`side_effect_state`、effect counter 和
reconciliation 投影列；旧 `unknown` run 的当前状态迁移为 `failed`，并追加一条
`legacy_unknown_migrated` state event 保存当时的原始错误。已有 session、runtime attempt、tool event、
provider 结果和旧错误事件不改写、不删除。旧 spec/plan 中描述这些状态机的内容属于历史设计，
不应作为实现依据。

反馈、人工重跑、进程失败和 typed-result 失败都会创建新的 append-only `agent_run`，但不会创建
新的业务 `reply_attempt`。只要原 runtime session 仍可访问，新 run 就向原 session 发送
continuation；新 revision 或 Skill/契约版本表示业务规则/结果版本前进，不表示必须创建新 session。
每个 turn 都记录当前契约哈希作为回执，但哈希变化不能切断上下文。只有 provider 明确返回 session
不存在或认证上下文失效时，才创建新 session。

Agent 返回的错误只是它观察到的现象，含义由服务决定（`app/agent_reported_error.py`）。wire 结果里的
`error_retryable` 与 `error_authorization_required` 不被读取；`error_code` 先统一为小写，服务认识的码
（需要授权或确认、dry run、可自行恢复的依赖、浏览器工具回报的退订码）按服务政策决定是否重试、
是否等人；其他任何失败码记为 `agent_reported_failure`，走有上限的普通重试，Agent 原文保存在
`source_code` 供排查。非失败结果（`needs_human`、`no_action` 等）上的码只作为原因标签，不驱动重试
或授权。编排层依赖的服务码（`runtime_*`、`codex_provider_*`、租约与恢复类）只能由服务写入，
Agent 写入时一律按 `agent_reported_failure` 处理。

任务 Agent 的 `memory_recall_used` 是 Agent 给出的上下文记录，不是服务的工具调用验收条件。

Task Agent 不直接调用外部 TODO 写入；Task 7 的创建/完成 intent 在 Task 语义事务中排入
`business_task_todo_sync_outbox`，由 dispatcher 按 `business_task_id` 执行。创建仅限正式、开放、
有来源支持的明确负责人、已接受承诺且有来源支持的可解析 `committed_deadline_at` 的 Task；
estimate、requester/external deadline 和 next check 均不能充当镜像期限。外部创建保存 provider ID
再读回；若首次读回失败但 ID 已保存，后续 TODO 状态拉取按该 ID 重试读回并收口链接/outbox；若创建
结果未知且没有 ID，不自动重复调用或排入另一创建键。同一 Task 存在未收口创建 intent 时不会再排新的
创建。`failed` outbox 在有界 `next_attempt_at` 退避后才可再次领取。外部 TODO 状态轮询只按其 Task
链接关闭 Task，写入完成来源证据、事件，并关闭该 Task 的 follow-up；关联 Project 或同聚类 Task 不随之完成。

Task 7 的新 follow-up 只从链接来源信号的精确群/单聊目标与明确、可解析 `next_check_at` 创建，
不从 deadline 推导时间或猜收件人。群聊回到精确来源会话并提及来源证据支持的负责人；单聊发给来源
明确指定的负责人账号，原会话 ID 保留用于核对。发送前会读回已关联的 provider TODO；若已完成，则关闭
精确绑定的 Task/follow-up，记录 provider 状态并释放尚未发送的 claim。发送有独立的 claim、lease、
revision、幂等 UUID 和回执记录；发送中的租约过期或网络中断标为未知，排入 Task Agent 核查，不自动重发。
核查后仅可修订输入所绑定的 Task follow-up；旧 `follow_up_drafts` 的读取和发送暂保留给尚未导入的历史记录，
不能作为新 Task 创建目标。Task 8 的旧记录导入与生产切换仍是独立边界。

## 任务类型

- `okr_review`：指定人员和周期的逐 KR 评审。执行 Agent 按当前业务 Skill 读取实时
  `processed.objectives`/`processed.okrRows`，再生成评审；审核 Agent 审阅并反馈修改，
  修正版通过后才发送。底层读取错误（认证失效、浏览器/profile 锁、周期解析失败等）
  必须原样保留，不能被 `consumer_retry_exhausted` 覆盖。服务入口先复用有效 token；
  缓存过期时只启动 headless 浏览器刷新，不打开可见窗口，也不把“禁止可见浏览器”
  误解为“禁止刷新”。
- `weekly_okr`：定时生成管理者 OKR 进度周报。全流程先领取并持续续租全局 run lease，避免超过重试间隔的长任务被重复启动；分析、报告发布和群摘要获得 provider 成功结果后，推进周报成功日期。
- 普通消息、审批、会议、邮件、任务跟踪和 WeChat 任务都遵循同一生命周期与反馈规则，只在领域输入、provider 能力和稳定结果字段上不同。

## 文档索引

- 总体 A/B 架构：`docs/architecture.md`
- 路由失败和恢复：`docs/runtime-route-recovery.md`
- Runtime 路由、fallback 决策与 429 重试：`docs/architecture.md` 的「Agent 失败重试」与各 Runtime 路由小节，
  实现在 `app/runtime_fallback.py`
- Consumer/Audit 反馈设计：`docs/superpowers/specs/2026-08-06-consumer-audit-agent-design.md`
- OKR 领域输入和输出：`docs/superpowers/specs/2026-06-08-okr-review-runner-design.md`
- 当前实现：`app/agent_orchestrator.py`、`app/consumer_agent.py`、`app/audit_agent.py`、`app/okr_review.py`、`app/weekly_okr_report.py`、`app/store.py`
- 系统错误码目录：[`docs/error-catalog.md`](error-catalog.md)
