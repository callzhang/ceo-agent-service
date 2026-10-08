# Current Runtime Mechanism

Reply queue polling first reads the current channel's due pending tasks without
acquiring a write transaction. An empty channel, future-only queue, or another
channel's work returns immediately even while a different writer is active.
When the preview finds work, the original write transaction rechecks and claims
the current rows atomically. The preview is not a reservation or queue cache;
work arriving after an empty preview remains pending for the next poll. This
removes idle Reply polling as a SQLite lock contender; it does not establish
that all other long transactions or API latency causes have been eliminated.

本文档是 CEO Agent Service 当前运行机制与已批准生命周期政策的总览入口。除明确标注为
“已批准的生命周期政策”的段落外，正文描述当前代码事实；政策段落是后续实现约束，不表示
对应代码已经切换、部署或在生产启用。`docs/superpowers/` 下被新设计取代的历史 spec/plan
仅用于追溯，不代表当前目标规则。

## 运行角色

两角色都可通过 `read_dingtalk_group_members` 读取精确群 ID 的 DWS 真人/机器人
成员桶和原生有界自动分页结果；调用方检查 complete、hasMore、buckets 和 failures。
`read_dingtalk_user_profiles` 按已验证的组织 userId 批量读取原始资料，不进行姓名搜索
或身份补全，并保留原始组织字段和空资料；title 必须由真实返回证据确认。群 openDingtalkId
不能直接传为组织 userId；姓名搜索只发现候选，须按稳定 open ID 关联后才能读企业资料。
工具在 Consumer/Audit 的实际目录及原生 CLI enabled_tools 中一致注册为只读，Audit
仍没有写能力。本修复仅补齐读取能力，不新增成员变化门禁或来源绑定策略。

微信 Reader Skill 的 `status`、`read-recent` 和 `produce-once` 都必须显式接收调用方提供的
绝对服务数据库路径，并原样传给受控 IPC CLI。缺少 `--db` 时在启动 IPC 前拒绝执行，
不能从工作树的 `data/` 推断生产账号就绪状态。该路径修正不改变 Sender、目标选择、
外部回检、发送授权或历史投递状态。

运行时发送工具拒绝 `provider_risk_rejected` 时，服务保留该错误代码并停止自动重试，
不将其降级为通用可重试失败或改成 `needs_human`。恢复必须先取得知情授权或形成实质更安全的
候选，再按原业务身份经过正式审核、外部回检及投递流程；不得换工具绕过拒绝。
历史 Attempt 的“重新处理”入口也会识别 AgentRun 中的 `code` 或 `source_code` 拒绝，
页面不提供原候选重放，直接 POST 返回冲突且不入队。详情明确说明限制；普通技术性失败仍保留
原来的重试入口。新的实质不同候选或有明确范围的授权必须作为新的正式处理提交，不能复用这条
历史按钮代替。

失败 Reply task 的 Attention 保留当前执行代最新 run 的原始诊断：优先 `source_code`，
否则 `code`，并展示来源及「Agent 说明」`reported_summary`。因此已保存的
`provider_risk_rejected` 不会只剩通用 `agent_reported_failure`；这些诊断不是外部效果证明。
旧代或无法解析的诊断不替换任务错误；读取不修改状态或恢复边界。

同一诊断生产器用于 Status API 时，后端 `AttentionRow` 与前端 Status 解码器均声明
可选的字符串或 `null` 字段 `error_code`；无原始诊断的行不必提供它。未知字段与错误类型
仍严格拒绝。真实任务/run 生成的 Attention 行必须能经过 `/api/console/status` 与前端
解码，不能只验证生产器自身而让新增诊断导致 HTTP 500 或页面拒绝正常响应。
人工决策行使用同一基础字段合同并要求字符串 `detail_label` / `detail`，不是普通
Attention 行；前端保留其独立说明。提供 `human_decision_rows` 时必须是数组，不能把
错误的对象、字符串或 `null` 当成“无人工决策”。

Status 的每次请求从一份新的只读 SQLite 快照读取队列、Attention 和人工决策，
同一请求各区块及 summary 复用这些结果，不再逐区块重复扫描或混用不同快照。
下一次请求重新读取，不缓存业务队列状态。连接器、微信 IPC 及系统健康探测仍使用
各自已有的短期后台缓存，不阻塞业务队列读取；此优化不修改失败或人工决策的判定。

Attention 的失败 Reply task 先在已有 `(status, id)` 覆盖索引上筛选 ID，再按主键读取
正文和诊断，避免逐行读取大型任务表正文。大小写兼容、当前业务对象、后续成功覆盖和
外部失败排除条件保持不变；不新增业务缓存或数据库迁移。
其他 Attention 来源及队列的最新失败原因也先通过已有状态索引筛选行 ID，再读取
必要字段。失败大小写、非空错误过滤、更新时刻及 ID 排序和下一快照立即反映恢复保持不变。

定时任务的后续恢复检查先匹配当前失败 Reply task 或错误所绑定的旧 run，再查询同一
scheduled_task_id 的更晚 run。使用嵌套 EXISTS 避免查询器从大量无关成功 run 反向连接，
导致每个成功 run 重复扫描历史。覆盖条件仍是更晚 run 已 dispatched 且对应 Reply task
为 done/skipped；不同任务、较早成功、后续失败或 service_command 均不能覆盖。
SQLite VM 操作预算回归限制无关成功历史带来的查询工作量，语义回归同时保留原失败和错误
物理记录；不新增索引、迁移、队列缓存或恢复写入。该修复针对查询计算成本，不将慢连接
上下文计时等同于持写锁时长，也不证明所有 SQLITE_BUSY 原因已消除。

Workbench 领取轮询先只读检查是否存在 queued 或租约已过期的 running turn。两者都没有时
立即返回，不执行 BEGIN IMMEDIATE 与其他服务写入争锁；非过期 running 不需要恢复。
只读连接结束后，有工作时重新开启原写事务并重查、恢复过期 turn、按原条件与 CAS 领取。
检查后新提交的任务保留 queued，下一次轮询领取，不缓存空队列或抢先修改租约；终态、
审批确认、归档、恢复及停止策略不变。

运行记录质检的路径规则在每次扫描开始时解析一次，随后逐行、逐字段检查同一份规则。
下一次扫描重新读取配置，不缓存质检结果或遗漏历史记录。凭据检查与固定临时运行路径
检查保持不变；显式空路径配置不会重新加载默认值，仍检查固定临时路径。该修复减少
逐字段重复解析环境与主目录的计算成本，不代表所有数据库 I/O 或锁等待均已消除。

日报的 task-bound `consumer_document_write` 回执除报告文档和正文读回外，还原样返回
已从目标知识库完整目录解析出的 `folder` 元数据。Consumer 使用其中 provider 实际提供的
`url` / `docUrl` 完成日报文件夹链接要求；服务不由 node ID 拼接 URL，也不为返回该元数据
新增写操作。provider 未提供链接时仍保留缺失事实。周报回执、审核和消息投递边界不变。

所有系统业务审核项必须对应实际系统任务及其当前业务实例；工具或接口存在不构成审核需求。没有对应系统任务就不设置该业务的系统审核项。当前对应关系见 [系统任务与审核范围](audit-task-scope.md)。小青面试与提交由 Derek 在个人对话中处理，不属于本次后台系统审核范围。扫描、下载和登录命令沿用既有技术结果验证，不因这条范围规则新增 Audit 回合。

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。

Consumer 的普通工具工作不因写入而自动进入系统动作审核。任务绑定的本地文件由
`consumer_artifact_write` 写入 `CEO_WORKSPACE/consumer-artifacts/<task_id>/<execution_generation>`，
Consumer 与 Audit 可用 `read_task_artifact`、`list_task_artifacts` 读取该代次材料；Audit 没有写工具。
原 `consumer_document_write` 继续只处理绑定的日报/周报文档。工具返回的实际内容读回及 SHA
证明普通材料写入，不是已审核消息或 OA 动作的 System 回执。原生 V8 可调用具名工具和计算，
Codex Consumer 可在绑定工作区进行原生代码执行；Audit 只读。实际不可用的工具应如实报告，不能伪造执行。
系统注册且需要审核的动作仍只由 SystemExecutor 执行。Consumer 的结果解析只验证严格 wire，
不再对整份文字扫描“已发送”等完成短语；历史事实和本轮工作由完整来源及真实回执支撑，
摘要不会生成 candidate execution、外部动作回执或 sent_replies。此规则不改变已有运行时风险拒绝、
授权缺失或历史拒绝不可重放的处理。

候选分为动作计划和当前实例的人工问题。人工问题包含来源上下文、具体原因、证据、互斥可行选项及后果；可执行选项各自绑定完整动作计划，停止选项写明 skipped 和原因。只有 Derek 能补充的开放事实使用 requested_input，不制造假选择。不能混合立即执行的动作与尚未选择的条件分支。

通用消息受众契约由 Consumer/Audit 共用的固定审核规则注入，已保存或空的自定义规则均不会
省略该契约。候选应依据相关群讨论、完整当前成员、稳定身份及当前 title/职责判断实际内容
的披露范围；职位只是证据，不是自动授权，私聊也不自动安全。同一受众适合全部内容时保持
单条消息；确需不同受众时沿用既有多动作候选，各自保存准确正文、目标与 action identity。
拆分后的私聊只能发给已核实的对应人员，不能发给 principal 自己或其已验证别名、默认抄送
自己或把自己作为未知对应人员的兜底；另行明确要求给 principal 的报告不受此拆分约束影响。
每个动作独立核对既有回执并恢复未完成结果；拆分不得绕过历史运行时风险拒绝。这是准备和
审核规则，不是新增发送器，不以 prompt 注入成功替代原生业务验收或生产回执。

Consumer 的业务结果与 wire JSON Schema 和解析器一致：`proposal`、`no_action`、`failed` 的 `decision_options` 为空，`requested_input`、`needs_human_reason`、`decision_basis` 不得有值；这些字段只属于 `needs_human`。普通方案的事实证据写在 `proposal.sourced_facts`，无需动作的依据写在 `summary`，不借用人工问题字段。

Consumer 的未审核外部效果检测只把写入操作的 provider 回执视作副作用；
群消息列表中的 `conversationMessagesList[*].messages[*].openMessageId` 是历史消息身份，
不能据此判定 Consumer 发送了消息或阻止后续安全恢复。检测只读取 provider 的
`data` / `result` / `provider_result` 结果封装中的回执，MCP `content[*].text` 与
`structuredContent` 先按传输封装解码；正式发送工具的 `provider_result.result.openTaskId`
同样保留为执行证据，不递归
业务正文、历史消息样本或其重新组织后的预览。拒绝结果不作为已接受的效果，真实
发送结果继续保留回执的出现顺序并去重。这是诊断证据边界，不改变审核、发送授权或恢复规则。


Audit 返回 approve、return、reject，必须绑定 candidate_digest 和 proposal_revision；failed 只表示技术失败。Audit 不修改正文、选项，不返回执行回执，也不自行创作另一个人工问题。return 允许保留正文并补足证据；reject 要求实质改变被驳回内容，不能只改描述、元数据。首次提交最多三次内容重提，耗尽为 failed，技术失败不占内容预算。

人工问题也先经过 Audit。Audit 检查：是否确需 Derek、规则/代码/Skill/记忆/会话/读取是否可自行解决、是否应向来源人索取材料、是否把技术失败伪装成决策、上下文和理由是否充分、选项是否可行且确有差异、每个执行分支是否完整并仅限当前实例。只有当前版本 approve 后才进入 needs_human 和通知，审核通过不等于业务 done。

选择接口提交 candidate_id、review_id 和 option_key，服务读取持久化分支。首次选择独立记录并将原 execution_generation 唤醒；相同选择幂等，冲突选择拒绝。有效分支直接由系统执行，不重新启动 Consumer/Audit。补充文字保留为新输入，使旧候选失效并进入新完整候选和审核；历史选择、问题和回执保留。选择事件不等于执行完成。不再提供 applies_to=task_class、可复用规则、Skill 更新复选框或此决策处理器的 Skill 写入副作用。

旧长期规则问题仅通过显式单项命令 `python -m app.rule_question_retirement --attempt-id <id> --authority <confirmed-contract>` 退役，默认只读预览；应用另加 `--apply --verified-backup <path>`。资格限定为当前业务对象、当前执行代、最新 Attempt 所绑定的已完成 Consumer、done 任务、完整无技术错误的 task_class 问题，且该任务没有新候选；不批量排除旧结果。应用只原子写入 resolved_at、resolution 与包含原结果 SHA256/任务/运行/执行代/契约依据的退役回执，保留原 send_status、模型结果、任务状态和外部回执。详情、History 和队列将该已退役问题显示为 skipped 并解释依据，重试 POST 拒绝。它不是人工选择、Audit 通过或业务完成；未退役和当前无效问题继续由原质量检查报错。预览及应用均不初始化或迁移数据库。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，stage_index、predecessor_review_id 绑定前一已完成阶段的回执；阶段上限独立于每阶段三次内容修订预算。

主页面 `/` 只是同一 Service Runtime 的一个输入和展示入口。页面 turn 使用 `workbench`
workload 进入 `RoutedCodexExecution`，共用模型路由、会话、runtime attempt、失败切换和 CLI
原生 `auto_review`；页面不启动自己的 Codex runtime，也不定义独立审批策略。
旧版确认记录只读，不能从页面或 API 恢复执行。

主页面事件订阅回归先用 React `act` 完成时间线及其副作用，再等待真实 EventSource
订阅建立后注入事件；页面文字出现不代表订阅已经建立。合并刷新和切换任务取消刷新
的断言保留，不靠延长超时或跳过断言处理测试竞态。

Workbench 下载的路径替换回归以打开时的文件身份和实际成功关闭事件验证描述符生命周期，
不在请求结束后用数字描述符是否可 `fstat` 判断泄漏；数字描述符可能已被并发线程复用。
测试只替换 API 模块的 OS 访问代理，不修改共享 `os` 模块；遗漏流关闭的负向回归必须失败，
且仍须验证路径被替换为符号链接后只返回原文件，不返回链接目标的内容。

模型路由的名字：只有 `codex_oauth`、`claude_oauth`、`friday_runtime` 三条内置路由名字固定；其余
路由（含名为 `codex_api`、`claude_api` 的）都是添加的线路，由 `CEO_RUNTIME_<名字>_*` 描述、可改名，
运行时规则按路由种类而不按名字判断（详见 `docs/architecture.md` 的「Agent Runtime 路由模型」）。
旧的 `CEO_CODEX_API_*` / `CEO_CLAUDE_API_*` 在服务启动时由 supervisor 先行一次性迁移，名字不变，
迁移前备份 `.env`。添加的线路改名由服务端一次完成：先在一个数据库事务里改定时任务首选线路、可派发的
定时运行快照、会话续接、线路暂停和能力快照，再改 `.env`；运行尝试历史保留旧名。重启后生效。

## 标准生命周期

```text
pending -> processing -> done
                     -> failed
                     -> needs_human
                     -> skipped
```

- `pending`：已持久化，等待执行。
- `processing`：正在执行，或正处于“审核 Agent 要求修改、执行 Agent 重跑”的反馈闭环中；
  闭环期间任务始终停留在这个状态，不切到单独的“待反馈”“待修正”状态——修改请求记在
  `AuditAgentResult.feedback`（`rule`/`observation`/`requested_revision`），修正次数记在
  `proposal_revision` 计数器上。
- `done`：逻辑完成且结果已持久化。
- `skipped`：判定为不可执行或不必执行，主动收口，不再重试。
- `needs_human`：当前完整 Consumer 人工问题已通过 Audit，只等待本次选择或 requested_input。历史问题和未经审核的 Consumer 输出不能作为当前人工待办。完整选项各自包含精确计划；质量分值是证据指标，不是制造人工问题的机械阈值。
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

已有 typed proposal 只代表待审核的动作，不代表真实外部授权。Audit 返回
`authorization_required` 时，解析器保留此错误，不将其误判为无效 JSON 或普通 CLI
确认参数问题。服务认定为不可重试的授权缺失，在 Consumer 和 Audit 两条路径都落为
`failed_terminal`；再次调度以及任务上已保存同一错误码都不能触发自动重试。
这不会授权发送、伪造 `needs_human` 或修改历史失败；明确授权后的恢复仍须走正式入口。

当前代次的最新 Attempt 指向失败 run 时，即使关联任务进入 `pending` 等待重试，History 与 Attention
仍显示该失败，直到后续有效 run/Attempt 给出新的当前状态。没有当前代次失败 run 的 pending
任务本身不进入 Attention；旧代次失败也不污染新代次。同一定时任务的较早 Reply task 若失败，后续 run 已派发且对应 Reply task 进入 done/skipped，则较早失败及绑定该 run 的读取错误仅保留在 History，不再作为当前 Attention；仅派发、pending 或另一个任务成功均不满足恢复条件。
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

### 发布期间单实例调度暂缓

`python -m app.reply_task_deferral --db <absolute-db> --task-id <id>` 默认只读预览，
不初始化或迁移 Store。2026-10-05 的明确操作授权仅覆盖原 386130/386131，不能扩展到历史
风险拒绝或其他业务任务。显式 `--apply` 必须携带预览的业务对象、执行代、输入版本、最新失败
Consumer run ID/指纹、独立验证备份、原因、请求 ID 和 60–86400 秒的有界时长。
模块在 `BEGIN IMMEDIATE` 中再次核对所有身份、原生运行、租约、Dispatcher 活跃所有者、
候选及可归属的真实/未知效果（含本触发的发送记录）。仍有活跃所有者就拒绝，不能强制抢占。
仅在无这些阻断时把原任务留为 pending、设置未来 available_at，并同事务保存独立调度回执；
输入、代次、尝试数、错误、Agent 历史和外部回执保留。预览中的已暂停任务明确显示 already_paused。

正式部署后，`--resume --receipt-id <id> --request-id <id>` 再次锁内核对该回执的原身份、
失败指纹与精确延迟，确认没有新工作或效果后恢复原 available_at；它不创建新任务或代次，
也不是业务成功。相同请求幂等，不同请求或中间变化明确拒绝。CLI 每次只做一个原子操作；
发布方的有界等待只轮询只读资格，每次应用仍独立复验。现有不透明出站键只有通过任务的候选、
动作、运行、工具和发送事实才能归属，不能用不相关的全局出站记录阻断一个实例。

## 功能机制开关与任务生产

邮件分类使用独立的 `email_agent_classification_tasks` 持久化队列。每封邮件是一次经共享
Runtime Router 的 Agent 轮次（`EmailClassifierRoutedBackend`），与其他 Agent 轮次使用同一条线路
顺序和统一 fallback；没有哪条线路名是邮件分类专用的（Derek 2026-09-24）。此前的「直连名为
`codex_api` 的线路的 Responses API、失败再走路由」主路径已删除，代价是每封邮件变成一次完整的
命令行轮次，更慢、更耗额度。离线训练标注命令 `app/email_training_labeler.py` 同样走系统模型路由（Derek 2026-09-25，不再有
`--route` 直连）：路由只运行服务记录为运行中的工作，所以标注命令为每次分类临时登记一条运行中的
分类工作项（`EmailClassificationTaskAdapter.open_offline_task`，不含邮件定位、不可被扫描领取，分类结束即删，
启动时清掉崩溃遗留），再交给同一个 `EmailClassifierRoutedBackend`。
分类请求允许正常的模型响应时间；临时网络、超时和租约中断不进入终态 `failed`，而是在同一
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

每个 Consumer 和 Audit 结果都严格携带 risk、confidence、rule_coverage、information_completeness；后三项在 [0, 1] 内。分值描述本案证据，不按固定阈值决定路由。材料可向申请人获得时先提交完整材料请求计划；技术、认证、路由和解析失败保持 failed。真实的当前实例管理选择由 Consumer 提出，只有精确候选的 Audit approve 才进入 needs_human。停止选项明确 skipped 和原因，开放事实用 requested_input。

新 wire 严格解析新合同；历史结果只在独立只读历史模型中解释，不补齐后重新执行，不改写原始 run。当前人工决策必须绑定原对象、execution_generation、持久化 candidate/review 和未被替代的当前版本；旧的无绑定选项仍可阅读但不能执行。人工决策和错误分别展示；审核批准不是投递完成。

等 Derek 决定的事项出现在他先看的地方（Derek 2026-09-25）：Agent 首页左栏顶部「需要你决定 · N」列出当前 generation 中已经审核通过、未选择且绑定有效的 `needs_human`；旧无绑定问题仅在 History 只读展示，每条以事项名称为标题（OA 扫描取「待处理审批：」后的审批名，钉钉 DING 催办取「提醒您审批…」的审批名并注明催办人，评论提及取审批单名），次行是一句原因，点击打开该 attempt。首页原有的「处理反馈 · N」只计消息反馈，与待决事项无关。点击通知时，服务先在已打开的 Chrome/Safari 控制台标签页里切到该 attempt 并把窗口提到前面（AppleScript，按控制台 origin 匹配），只有没有已打开的控制台标签页时才新开页面（Derek 2026-09-25）。首次由服务进程控制浏览器时 macOS 会请求一次自动化权限。

## 审核反馈闭环

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。

候选分为动作计划和当前实例的人工问题。人工问题包含来源上下文、具体原因、证据、互斥可行选项及后果；可执行选项各自绑定完整动作计划，停止选项写明 skipped 和原因。只有 Derek 能补充的开放事实使用 requested_input，不制造假选择。不能混合立即执行的动作与尚未选择的条件分支。

Audit 返回 approve、return、reject，必须绑定 candidate_digest 和 proposal_revision；failed 只表示技术失败。Audit 不修改正文、选项，不返回执行回执，也不自行创作另一个人工问题。return 允许保留正文并补足证据；reject 要求实质改变被驳回内容，不能只改描述、元数据。首次提交最多三次内容重提，耗尽为 failed，技术失败不占内容预算。

人工问题也先经过 Audit。Audit 检查：是否确需 Derek、规则/代码/Skill/记忆/会话/读取是否可自行解决、是否应向来源人索取材料、是否把技术失败伪装成决策、上下文和理由是否充分、选项是否可行且确有差异、每个执行分支是否完整并仅限当前实例。只有当前版本 approve 后才进入 needs_human 和通知，审核通过不等于业务 done。

选择接口提交 candidate_id、review_id 和 option_key，服务读取持久化分支。首次选择独立记录并将原 execution_generation 唤醒；相同选择幂等，冲突选择拒绝。有效分支直接由系统执行，不重新启动 Consumer/Audit。补充文字保留为新输入，使旧候选失效并进入新完整候选和审核；历史选择、问题和回执保留。选择事件不等于执行完成。不再提供 applies_to=task_class、可复用规则、Skill 更新复选框或此决策处理器的 Skill 写入副作用。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，stage_index、predecessor_review_id 绑定前一已完成阶段的回执；阶段上限独立于每阶段三次内容修订预算。

钉钉引用回复由服务使用 DWS `chat +messages-reply` 投递；该入口核验原消息、发送者与会话一致性，且适用于已确认的单聊会话。投递仍沿原业务对象的幂等键和回执核验，不因引用回复失败自动改发普通消息。

会议总结的业务群候选由正文中的业务词与标题共同检索，排除听记摘要里的时间标签、图片链接等元数据。平台中立的共享群发现服务先按标题/摘要与群名筛选，再用完整日历名册的参会人覆盖率缩小候选；唯一或少量通过这两层筛选的群才读取近期消息并交给决策 Agent 做完整业务承接核验。当前运行时只有 DingTalk/DWS 适配器，Lark 和 Slack 需要各自 Provider 的认证、读取和外部读回验证后才能启用。发现服务不选择最终目标，也不把 Provider 故障降级成“找不到群”。没有标题命中时仍保留候选做实时名册检查，不能按搜索接口的原始返回顺序截断候选。排序优先考虑群名是否对应摘要主题，再参考讨论片段；词项交集只是检索线索，Agent 必须核对实际讨论、行动负责人和受众。参会人覆盖率只证明受众交集，不证明业务归属，不能单独强制选群。历史投递仍需本次实时成员覆盖达到门槛，且须与当前议题一致；此前已发送的总结不会因路由修复自动重发。

候选群消息读取明确返回 DWS `code=1001`、业务原因「该群为保密群，无法获取消息记录」，且 `retryable_external_dependency=false` 时，适配器保留原始拒绝，不包装成瞬时错误。会议任务记录 `meeting_group_discovery` 失败并释放执行锁，不继续自动重试；权限或可读取的可信来源恢复后才走正式恢复。该拒绝既不是空消息结果，也不允许跳过该群而改投其他群或组织者。未知读取错误以及 provider 明确标记可重试的故障仍沿原有重试路径处理。

对于源单聊的澄清动作，Consumer 必须在 action target 中提供已经通过实时读取确认的参与者 `open_dingtalk_id`。若该参与者字段以 `verified_participant_open_dingtalk_id` 表示，System Executor 使用候选中已确认的同一稳定接收人身份执行单聊发送；不会把 `conversation_id` 当作群聊目标。
若单聊候选同时带有原会话的 `conversation_id` 和收件人的 `open_dingtalk_id`，发送入口在核对会话与原任务一致后只保留收件人目标，不把会话 ID 当作第二个互斥目标。DWS 的单聊写入口实际要求 `receiverUid`（userId），因此服务会用原始触发消息中与该 open ID 精确匹配的姓名和 open ID 解析 userId，再只把 userId 传给 DWS；解析不到唯一 userId 时在 provider 写入前失败，不猜姓名、不直接把 open ID 当作 receiverUid。群聊或不匹配会话不作此转换。

当 Audit 因临时授权映射或运行配置失败后被重新调度时，恢复会创建下一次 Audit turn；已失败的 turn 保持历史记录，不能被重复领取。

每个队列任务的 `Original trigger` 是该任务唯一的权威输入，由
`trigger_message_id` 标识。近期会话消息、材料和实时读取结果只能补充事实，不能把
Consumer 的任务改成另一个消息、日程或审批事项。Audit 返回 `return` 或 `reject` 后，服务
必须把规则、观察结果和修改要求传给下一版 Consumer proposal，再创建对应的 Audit run；
Audit 只反馈修改要求，不直接替换 Consumer 的业务正文。
### Task Agent 的项目、任务与判断（已发布；业务验收部分完成）

2026-10-04 获批设计的代码经 PR #16/#17 发布；2026-10-06 22:34 PDT 读回生产为
`f4a889df`，包含项目 Memory 写入规则及当前来源项目引文的 bounded repair 修复。
生产 input `27465` / run `10690`
保存了 4 份 ProjectContext 和 2 张零 Task 成员的业务关注卡片。input `27497` /
run `10691` 的项目引文不连续，被领域校验拒绝并回滚，原失败保留；其后的
run `10692` 虽 completed，但只判断会议主题，具体项目覆盖不通过。后续副本对照
仍有引文纠正耗尽和旧关注卡原证据声明错误，详见验证记录；不把修复发布当成业务通过。
历史项目覆盖及近期多来源结果尚未全部达到设计预期。
当前预期/实际、发布与未完成验收见 `docs/task-project-centered-validation.md`，
不能把健康检查、非零关注或已发布代码单独当成全部业务效果证明。

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

Project 可选关联 Fxiaoke CRM AccountObj，持久化稳定 `_id` 与 CRM 显示名称，不修改 Project 标题；内部 Project 可以不关联客户。Task 不保存重复客户字段，只有关联到已确认 Project 的 Task 才在读取时继承其客户名称，独立 Task 保持无客户。Task Agent prompt/Skill 引导写入当前证据支持的持久项目风险和重要进度/状态更新，保留原始来源引用与来源时间，区分来源事实和有证据的风险推断；不写单条 Task/TODO、日常或临时信息、无依据猜测、秘密、原始转录及重复更新。不增加工具白名单、通用只读模式或 CLI/MCP 写入拦截。

CRM 查询使用 `sharecrm data record query-by-name` 对 AccountObj 做只读名称解析，不写 CRM。Agent 提供的 `crm_customer_evidence` 必须同时、完全一致地出现在 Project `evidence` 中。该解析器不能证明候选精确或穷尽；Task Agent 和 Project 页面返回的每个候选（包括单条候选）都保持未关联，只有用户明确确认后才建立本地关联。Resolver `NO_MATCH` 与 CLI/认证/查询不可用分开记录。查询失败或后续冲突不会清除已确认关联。客户汇总按 CRM `_id` 分组，只包含已确认关联的 Project；未关联项目仍在常规项目视图。

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
project_link_proposal、attention_proposal；历史 decision_json 原样读，不经当前 parser 升级。
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
raw decision_json 的 outcome/reason 不被应用回执改写。领域事务先写 pending 回执，卡片消费者及
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

OA 通知不等于 OA 执行任务。来自 `OA审批` 的系统通知只写入
`oa_notification_events(event_kind=system_notification)` 并标记已读；聊天窗口中形如
`[Ding]…提醒您审批` 的催办写入同一表的 `chat_reminder` 事件，保留原会话、原消息、发送人和
原生回复所需的消息 JSON，但不创建 `reply_task`、`agent_run` 或独立审批 Session。定时
`scan-oa-approvals` 读取实时待办后，如果当前用户恰好只有一个 `RUNNING` 节点，就把没有
`task_id` 的催办事件认领到该节点；没有节点或有多个候选节点时不猜测，继续等待下一次扫描。
实际审批仍只由定时扫描创建的 OA `reply_task` 执行。System Executor 保存并回读 OA
动作成功回执后，服务使用 `oa-reminder-result:{event_id}:{attempt_id}` 的稳定投递键，对原催办消息
做一次原生引用回复；发送失败会释放事件等待重试，已发送事件不会重复回复。这样通知页、
系统通知和聊天催办都能作为同一个 OA 案例的观察输入，而不会重复启动审批。

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
Attempt 详情的每条 runtime 记录分别提供 `status`（运行时调用状态）和 `run_status`
（所属 Agent run 的业务运行状态），页面分别标注两者。模型调用返回 `completed` 不代表
业务审核通过、外部动作成功或业务 run 完成；业务 run 仍可能是 `failed` 或 `running`。
此处只投影已有状态，不改写历史记录、Audit 决策或恢复资格。
Session 可用性测试同样按真实 `AgentRun` 提供业务 `status`；缺失 transcript
只影响 `session_available`，不改变业务运行状态或运行时调用状态。
Workers 的当前 attempt 队列统计会排除 `agent_run_id` 指向旧 execution generation
的记录，即使同一业务对象更新了 trigger message；旧 attempt 仍可在历史详情中查看。

对进入 Consumer/Audit 的 task，当前投影结合当前 execution_generation 的候选、审核、人工选择、System execution 和真实回执。模型 run 完成或 Audit approve 不等于动作成功。System 已开始的精确动作在同一 candidate/review/generation/selection 下恢复；确认成功的持久化回执可完成执行投影，不要求为取得回执重新运行模型。无法确认的外部结果保持 uncertain，不取得再次发送资格。自动技术重试在同一 generation/revision 追加 turn；明确的人工 rerun 或来源补充通过正式入口创建新 generation；旧 run、attempt、session、runtime event 和回执保留为历史事实。真实 pending/processing 仍显示等待/执行中，旧 generation 的失败不覆盖当前进展。

`needs_human` 的有效绑定选项直接选择已审核精确方案，由 System Executor 执行；停止选项按原方案写入 skipped 和原因。requested_input 或新增事实进入 Consumer/Audit 生成并审核新 revision，不能改写已经批准的方案。旧无绑定选项不可执行，未收到选择时不猜测决定。

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
- **通知归并**：OA 系统通知和聊天窗口催办不再各自创建 Agent 输入。系统通知只记录观察事件；
  `[Ding]…提醒您审批` 只记录原消息作为待回复目标。扫描器解析实时当前节点后才创建一个 OA
  执行任务；唯一当前节点才允许归并无 `taskId` 的催办，多节点或无法解析时保持待归并状态。
  OA provider 回执确认后，服务沿原消息的会话和消息 ID回复催办人；该回复与 OA 审批 Agent
  执行分开记账并按事件/Attempt 幂等。
- 扫描任务绑定通用 `dingtalk-oa-approval` 与适用的 Stardust 业务 Skills。曾把
  `dingtalk-misc/references/oa.md` 当作审批规则来源，导致我们自己的审批规则从未进入模型；
  官方技能还会被 `dws upgrade` 覆盖，规则写在那里留不住。

决策规则在审批 Skill 里，不在代码里：通用的完整决策表、
`information_completeness` / `rule_coverage` 评分口径、退回优先于评论搁置、拒绝前必须先查
`revert-activities`、`--remark` 必填，都在 `dingtalk-oa-approval` 中。OA 定时任务冻结绑定
`dingtalk-oa-approval` 以及 Stardust 财务、立项、合同、人员、考勤/出差、云资源六个业务 Skill，本机任务的
Prompt 另写入负责人的个人规则（只存在该定时任务的数据库记录里，代码默认值不含）。背景参考文档不由审批 Agent 读取，代码与默认 Prompt 都不引用它们。Consumer 按 live `processCode` 与表单事实
分类，交叉事项组合适用类别；财务规则卡仅约束登记的财务模板，且只提供判断标准，动作按通用决策表。适用业务 Skill 必须覆盖
当前事项的规则条件、例外、权限和动作映射，适用 Skill 完整覆盖时才允许 `rule_coverage=1.0`；其他情况
低于 1.0 描述证据缺口；当前实例问题必须有完整 Consumer 候选与 Audit approve 才能进入 `needs_human`，不得按分值自动批准、拒绝或升级。申请人可补材料但不能关闭并存的政策升级。
六份 Stardust Skill 都只存在于运行时目录，没有仓库副本，按 `RUNTIME_ONLY_VERSIONED_SKILL_NAMES`
版本化，**不得带 `metadata.managed_by` 标记**——操作 Skill 目录会拒绝带标记的文件，定时任务
也就不能选择它们。Derek 的个人规则仅写在 OA 定时任务 Prompt，不得推广为公司规则。

### 历史 DWS schema 闸口说明

`oa approval revert-task` 和 `revert-activities` 在 DWS 的 runtime schema 里不存在
（上游 issue #1406），同族的 `reject`、`redirect-task` 正常。命令功能完好，但两道闸口都依赖
schema 判断写操作，因此各自失效过一次：

- **执行闸**：`_execute_reviewed` 判 `agent_cli_command_unreviewed`，退回命令到不了钉钉，
  "退回优先"这条规则写下后一次都没执行过。修法是登记到 `config/mcp-tool-effects.json`
  的受控写名单（`6b6b9081`）。
- **证据闸**：`dingtalk_send_evidence` 的分类器同样认不出它，退回真的执行了、钉钉记录
  `REDIRECT_PROCESS` 且待办减少，任务仍被判 `provider_receipt_missing`。修法是让证据闸
  使用同一份登记表兜底（`ef6f4de4`）。

以上为旧 Agent 命令执行路径的历史问题。当前受控动作注册使用 typed System action handler，绑定精确 operation/target/payload，并由对应 provider 回执和读回来确认；不能依靠旧的命令证据分类器或成功 envelope 推断当前动作完成。

## 外部动作幂等与依赖

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

原生群消息和直接用户消息在 provider 接受、但本地回执尚未保存的中断后，恢复读取原精确目标，并比对持久化审核正文、本人发送者、未撤回、dispatch 时间下界及唯一消息身份，匹配后补存回执而不重派。新 action attempt 在调用 provider 前原子保存 UTC 毫秒 dispatch 时间；旧无时区秒时间不能作为精确归属边界，保持 uncertain。provider 只返回秒时间时，同秒不足以证明在 dispatch 后的消息也保持 uncertain；没有向下取整时间来扩大匹配。空页或多匹配不是无效果证明。文档评论和 OA 评论恢复读取原 node/process instance 并保存只读诊断；尚无已验证的 author/time/body/stable-ID 联合归属契约时，保留 uncertain，不凭相似正文补记成功或再次发送。

candidate_executions 保存租约，candidate_action_attempts 在 provider 调用之前保存 dispatch 边界；已验证回执复用。进程中断或超时没有回执不能证明未发生效果：先读取原外部对象。确认成功继续，确认没有效果才可重试同一计划，无法消除的歧义保存 uncertain/failed，不伪造完成或人工问题。技术恢复不重新审核不变业务方案；业务事实改变则使候选失效，重新形成并审核方案。没有通用 shell 或 Agent 执行兜底；未支持的动作明确失败。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

当前 Attempt 的结构化运行结果 `code` 或 `source_code` 为 `provider_risk_rejected` 时，历史“重新处理”入口不可用，直接提交该入口也返回冲突且不入队；API、React 和原生 HTML 优先展示该原因。确需新的候选或执行范围时，应通过明确的本次任务和完整候选提交处理，不能用旧入口重放历史候选。对于后端允许重新处理的其他失败，`rerun_label` / `rerun_confirmation` 仍由同一业务对象的结构化历史提供；跨执行代的历史拒绝可使措辞显示“重新评估候选”，普通技术失败保留“重新处理”。只认确切的顶层结构化错误，不匹配正文、嵌套文字或其他对象；System 的既有历史拒绝限制和原始记录继续保留。

Codex 角色使用原生 code_mode_only 和 V8 host。Consumer 的内建 functions 命令与补丁接口在当前 task/generation 的 consumer-artifacts 目录运行，使用 CLI 自带 workspace-write 沙箱，命令网络关闭、额外 writable_roots 为空；MCP agent_cli 的 cwd 仍是服务源码目录。Audit 使用 read-only 沙箱并排除 functions namespace，只有具名读取。受控发送与 OA 等注册操作没有暴露给角色 MCP，仍由审核后的 System 执行。Claude 没有普通 shell 执行能力，Friday 仍不具备角色能力。实际工具调用与文件回读证明执行，无工具固定合成业务比较仅证明判断。

补充人工问题验收使用 `evals/consumer_audit_human_review/v1.json` 和 `python -m scripts.eval_consumer_audit_human_review`：冻结两个有效问题与六个语义错误问题，分别调用 baseline/candidate 的真实原生 Audit、完整角色指令和 wire schema，核对候选摘要、修订及审核结果并保存原文。输入是刻意构造且结构合法的候选，不证明 Consumer 实际产生了这些问题；结论仍需独立原文复核，不替代真实发送或生产验收。原十五项业务比较保持不变。 v1 的开放输入正例被独立复核发现含不存在的收件人/双选项说明；原版本与运行结果保留，v2 只修正该正例的质量说明和缺失信息程度，其他七项不变。运行 v2 时显式选择其 manifest，不能将 v1 结果重标为 v2。

需关注中的新审核问题显示为“本次事项选择”，说明需要选择本次事项的处理方案，判断依据保留已审核候选的具体人工原因。旧 `task_class` 问题的历史状态与选项保留，但不出现在当前可执行选择中；隐藏旧选项不表示已重新审核或业务完成。

正式 Consumer/Audit 契约发布使用 `python -m app.deploy --publish-consumer-system-contracts`，在服务空闲且停止、数据库备份已完成后，先校验并将恢复快照交给 updater，再开始替换九个契约文件、六个 managed Skill 修订和受影响任务的完整引用。发布中途失败也由 updater 持有该快照恢复；恢复失败时保留回执及原文件副本，记录 `needs_manual`，不回滚 Git 后启动可能与外部文件或引用不一致的旧服务。恢复成功后才启动旧版本；健康通过后仍核对 active 配置、全部启用绑定与新 worker 的加载回执/PID、九文件 SHA 和完整任务引用，最后标记 verified。代码上线、资产发布和实际业务结果分别验收。

正式关闭的微信投递若在关闭原因中记录 `superseded_by_principal_reply:<source-message-id>`，表示本人后续回复已覆盖旧候选。仅在匹配当前任务、执行代和会话的投递上，详情状态保留 `skipped`，不被任务的 `done` 覆盖；页面说明已由本人回复、旧候选未发送且无需重试，不显示旧投递重试动作。手动重试的同一写事务读取该正式来源引用并拒绝重新入队，原投递、Attempt、执行代与回执不变。该判定读取分号分隔的精确关闭字段及非空来源引用，不匹配回复正文或一般文字。其他普通过期投递的现有手动重试合同保持。

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

Feedback API 的 Git 回归使用独立临时仓库初始化 `main`，并将测试 API 与回执指向同一仓库。
PR 的 detached checkout 或缺少本地 `main` 不应改变测试结果；测试不得修改开发/生产检出的
分支，也不得用 `HEAD` 代替 main 祖先证明。真实的缺失提交、未合入 main 的提交仍必须拒绝结案。

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

**对账已失败的 move/trash 动作**（2026-09-25）：Gmail 被限流后会在 COPYUID 响应后追加自己的文字
（`[THROTTLED]`），旧解析器因此把 Gmail 已经完成的移动记成 `failed`（`provider_apply_failed:
ImapReadbackUnsupported`），而这类失败是终态（`attempt_count=1`、`next_attempt_at` 为空），修好解析器
也不会自动清掉，盲目重试又可能把邮件移动两次。`python -m app.cli reconcile-email-actions --account <id>
[--apply] [--limit N] [--after ACTION_ID] [--pause-seconds S]`（`app/email_action_reconcile.py`）
只**读**邮箱来对账：对当前计划里每条 `failed` 的 move/trash，先看原位置（原 UID 还在则没移动，
留 `failed`），再按稳定 Message-ID 在目标文件夹（move 的 `target_folder`、trash 的 `\Trash` 文件夹）
里找，最后才扫全部文件夹。目标里恰好一份、且满足动作时，`--apply` 才把它经由现有状态机
（`claim_failed_direct_action_for_reconciliation` → `complete_direct_action_attempt`）追加一次 `done`
尝试，`provider_operation=reconciled_readback`、`provider_result_id` 以 `reconciled:` 开头，明示这是
事后对账而不是动作当时的回读，同时把分类与邮件的定位更新到新的 UID；原来的 `failed` 尝试原样保留。
其余一律不动、不写库：原位置还在（`in_source`）、在别的文件夹（`elsewhere`）、哪都找不到
（`not_found`，包括 Message-ID 缺失）、重复 Message-ID（`ambiguous`）。命令**不会把任何行改回
`pending` 或重新排队**：`pending` 必须 `attempt_count=0` 且没有尝试记录，`failed` 行没有受支持的
保持不变式的重试入口，仍在原位置的行要重试须另行决定。默认不写库，只有显式 `--apply` 才记 `done`
（若全局 `--dry-run`/`CEO_NOT_SEND_MESSAGE` 生效则拒绝 `--apply`）。**命令永远不改邮箱**：会话被
`ReadOnlyImapSession` 包住，只放行 LIST、EXAMINE（只读 SELECT）、UID SEARCH、UID FETCH（头部与
标志）、CAPABILITY、LOGOUT，其余（STORE、MOVE、COPY、EXPUNGE、APPEND、CREATE 等）在发出前抛
`ReadOnlyViolation`。可重复运行（记成 `done` 的行不再是 `failed`）、每次最多 `--limit` 行（默认 100，
按 action id 排序，输出的 `next_after` 可接着 `--after` 续跑）、行间停 `--pause-seconds`（默认 0.5 秒）；
单封超时或被限流只记为 `error`、丢弃并重连会话、指数退避后继续，连续 5 次错误才停下（`aborted=true`）；每封（含连接）有 `--message-timeout-seconds`（默认 60 秒）的墙钟上限，超时就从外部关掉该连接并记 `error`，不依赖套接字读超时——曾有一次读取卡住 30 分钟；每 25 封输出一行计数进度，Ctrl-C 会带着已有计数正常结束（`interrupted=true`）。
输出只有计数（按 move/trash 分：verified、in_source、elsewhere、not_found、ambiguous、error），
不含邮件内容。

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

启动时的终态 receipt 投影恢复只重开“receipt 已落库、任务因旧生命周期超时而失败”的退订任务。`skipped_no_reliable_entry` 和 `skipped_login_required` 两种跳过 receipt 且任务已有执行次数的，不再重开：手动重跑因原邮件已不在邮箱而失败（`email unsubscribe source message is unavailable`）时，每次重启重开只会让同一批任务必然再失败一次。

每个邮箱账户有两个独立回溯窗口：Agent 默认 30 天，模型默认 365 天。`email-message-check-once`
按当前 runtime 模式只运行其中一条路径：无上线模型时运行 Agent 窗口并遵守账户的“仅未读/全部”
设置；模型上线后只运行模型窗口，优先覆盖全部尚无稳定记录的已读和未读 Inbox/未绑定来源邮件，不
按日期把近期邮件留给 Agent，也不在同一轮创建 Agent 分类任务。模型路径以每文件夹固定批量
在后续定时轮次继续向历史推进。模型 accepted 结果进入现有不可变 ActionPlan 和 provider action
队列；`model_rejected`、`model_others` 与 `model_category_not_promoted` 对 Agent 能接的邮件（未读，或账户设为
“全部”，与 Agent 消费时的资格判断一致）创建 Agent 分类任务，由 Agent 判定；Agent 结果 `certainty` 不是
`certain` 时才存为 `pending_feedback` 等待人工标注（Derek, 2026-09-25：模型不确定先回退 Agent，Agent 不确定再问人）。
Agent 会以 `provider_message_no_longer_eligible` 跳过的已读邮件不入队，直接存为 `pending_feedback`，
否则任务以 skipped 结束且没有任何分类记录，邮件同时从 Agent 和待确认里消失。Embedding、runtime 或持久化技术失败使本轮
失败并在下轮重试，既不写人工待确认，也不当作模型拒绝交给 Agent。定时历史模型 runtime 的 Embedding 请求期限为 120 秒，实时
调用仍是 2 秒，避免批处理复用实时延迟预算后在同一封历史邮件上永久超时。冻结训练 snapshot 直接采用 provider 文件夹和 important 信号，
训练与 shadow 评估均为离线、阶段性作业，不在收信路径实时训练或并行推理；晋升只看最新候选自己的证据，
按类别逐个判断，达标类别由模型决定、其余仍由 Agent 决定，不再要求连续两个候选或标签水位前进。
已上线模型只对照它自己的证据保持生效：后训练的新候选无论达标与否都不会让它下线，候选目录里有无法读取
的文件仍 fail-closed。实时新邮件主路径严格按 `model -> Agent fallback` 顺序执行；定时路径同样如此：模型拒绝
进入 Agent 分类队列，Agent 不确定才进入待确认，技术失败则重试，不调用 Agent。

Provider 训练观察按有界批次运行。观察缓存与请求队列使用各自独立的进程锁和文件锁；长时间 IMAP
扫描不得阻塞实时扫描、分类结果落库或确定性邮箱动作。Email worker 只有在扫描/动作、Agent
consumer、训练三个组件都至少成功完成一轮后才发布 `ready`。

业务类别移动完成后在变更后的 locator 上执行 flag/read 动作，以服务器应答为结果；用户在 provider 中再次移动
邮件时，下一份 snapshot 立即以该文件夹作为训练标签。旧的 staged/manual 历史评测入口仍用于候选
验证；生产历史整理由上述定时模型窗口自动、小批量、可恢复地推进，不会一次性读取整个邮箱。
`junk` 先由代码发现标准退订候选；unsubscribe 使用独立的系统直接
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

Console 的”模型训练”提供版本化门槛配置及显式主模型开关。四项默认值为 Micro F1 0.95、
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
标题、列表和反馈链接由钉钉按 Markdown 渲染。回复触发消息用钉钉原生引用回复（`+messages-reply --content`），正文同样按 Markdown 渲染：2026-09-25 在Derek 自己的会话实测，标题、加粗、行内代码、无序与有序列表都正常显示，相邻行的列表项也分行——DWS 帮助里「普通引用按纯文本解释」与实际不符，读回接口把列表拼成一行只是读回的呈现。推送横幅标题去掉 Markdown 标记。Consumer 起草的钉钉消息正文一律写成结构化 Markdown（加粗结论行、`- ` 列表、关键事实加粗，分段时用短`###` 小标题）；微信、OA 审批评论和邮件正文保持纯文本。`needs_human_reason` 用中文大白话先写 Derek 要决定的那一件事，再用一句话说原因，不出现 rule_coverage、规则卡、partial、动作映射等内部词（Derek 2026-09-24/25）。群聊、单聊仍使用稳定的 conversation/user/openDingTalk ID；
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
- Audit approve 后只允许系统执行；全部动作有验证回执后任务才进入 done。

如果任务确定无需执行，应进入 `done`，并在 trace 写入 `agent_output/no_action`；如果结果需要修改，写入 `audit_feedback` 并保持 `running`；如果处理失败，应进入 `failed`。

任务 Agent 的 `memory_recall_used` 是 Agent 提供的上下文记录，不是服务工具调用验收条件。可用时应以聚焦
查询读取稳定背景；当前身份和业务事实仍需依赖相应来源/实时读取，memory 不能作为任务存在、指派授权、
负责人 ID 或接受承诺的证明。

邮件退订由独立系统服务执行，不进入 Consumer/Audit。其原有 ActionPlan、浏览器步骤、回执和退避保留，历史 Audit 退订 run 只作历史事实，不是新的执行路径。
退订浏览器仅把固定的内部失败类别投影到错误码；已识别的导航超时和页面状态缺失必须与兜底 `email_unsubscribe_browser_failed` 区分，错误码、步骤日志和页面原文里不得写入 URL 或凭证。结果的 `error_detail` 记下失败是什么：已识别的 `UnsubscribeBrowserError` 写固定枚举 `category=<名称>`（没有专属错误码的类别共用兜底码 `email_unsubscribe_browser_failed`，这里是唯一说明具体类别的地方，2026-09-25 有 6 次失败只剩这个兜底码）；未预期异常（兜底）会留下异常类名和截断到 240 字符的消息：URL、cookie/token/session 一类字段和 32 位以上的长串都先替换掉，再写入回执 evidence、这次尝试的 `audit_summary` 和任务的错误文本（`<码>: <类名: 消息>`，重试判断只看第一个冒号前的码）。此前该兜底只留下码，任务 384835（2026-09-25）两次失败因此查不出原因。外部原因造成的退订失败（第三方页面拒绝表单或一键请求、页面操作失败或超时、导航目标无效、控件不可用，清单在 `app/external_failures.py`，靠任务错误文本里的 `category=` 判断，不加字段）保持 `failed`，History 照常列出，但不进 Attention（Derek 2026-09-25）：这些是我们改不了的，同一页面重跑结果不变。

`email_unsubscribe_browser_timeout`、`email_unsubscribe_browser_session_unavailable`、兜底码 `email_unsubscribe_browser_failed` 这三个瞬时失败会先退避重试；用完固定次数后写终态 `failed` 错误文本时，只有兜底码把 `task_error` 里已经带的 `: category=<名称>`（或未预期异常的类名与消息）一并写入，其余两个专属码保持裸码——`app/external_failures.py` 只登记了这个精确格式。此前用完次数一律只写裸码，2026-09-25 那 6 次失败因此从未真正匹配过外部原因清单：它们的错误文本是裸的 `email_unsubscribe_browser_failed`，本该在 Attention 之外却一直在里面（Derek 2026-09-28 指出后修复）。控制台看到的失败原因同样有这道口子：一个还没读到任何页面、因而没有回执的退订任务（浏览器在读页面前就报错），观测时间线过去只给状态不给原因，2026-09-28 起把任务自己的错误文本一并投影进 `kind=unsubscribe` 的记录（`list_email_classification_observability` 和 `list_unsubscribe_states` 的 `error` 字段），控制台把已知的码译成人话，未知的码原样显示而不是不显示。钉钉 OKR 登录失效（`okr_headless_session_expired…`、`okr_authorization_required…`）同样是外部原因：只有人能重新登录，也保持 `failed`、不进 Attention；只有标准错误码 `authorization_required` 才能承载「需要人工」的授权决定，OKR 自己的错误码不行，所以它记为失败而不是人工决定。唯一例外是 `email_unsubscribe_receipts.entry_url`：该列按 Derek 的明确要求保存这次实际打开的完整私密 URL（含 query 与 token），用于人工复现同一个退订入口。写入前校验它的 sha256 等于 `entry_reference` 的摘要，因此不能与生命周期认定的身份漂移；它不经过 `assert_no_credentials`，因为被保存的正是那类 token。打开该 URL 会真实执行退订，任何能读这张表或这个页面的人都能替当事人退订。该列只在本次变更之后产生的 receipt 上有值，历史行为空且无法补全。退订浏览器不再对页面发出的网络请求做 origin 白名单、跳转或资源家族限制。
同一 generation、同一 proposal revision 内，Consumer 或 Audit 每个 worker pass 最多跑 2 次 turn（`MAX_ROLE_ATTEMPTS_PER_PROCESS`）；仍是可重试的通用失败时该 pass 以 `failed_retryable` 结束，并给出 `retry_after_seconds`（共享指数退避：60 秒、120 秒、240 秒……封顶 15 分钟，`external_retry.retry_delay_seconds`）。编排层按持久化的 run 计数该角色在这个 revision 上**连续**失败的 turn 数，满 `MAX_CONSECUTIVE_FAILED_TURNS`（6 = 3 个 pass × 2）后，在下一次 provider 调用之前结果就是 `failed_terminal`：错误码不变（例如 `agent_reported_failure`），Agent 原文 `source_code` 保留在错误诊断和结果摘要里，任务结束为 `failed`，不再重新入队。计数只在该角色同修订出现 `completed` run 时中断；实际失败的容量、连接和运行时 turn 都计入六次上限。尚未实际执行的活动租约等待不产生失败 turn；授权缺失沿用不可重试终态。这条上限对所有调用方生效：DingTalk worker 的 3 次 attempts 恰好对应同一数字；定时执行与 Email 任务延期时会归还 attempts，此前没有任何计数，2026-09-26 定时周报任务 385880 因 Audit 反复报告 `proposal_already_executed`（`agent_reported_failure`）在同一 generation 里被重跑 310 次，每 12 秒一次。内容反馈预算耗尽时进入 failed 并保留具体修改意见，不创建人工问题或停止选项。

## 周期性工作的归属

Derek，2026-09-18：**后台周期性工作必须是定时任务**，在控制台里可见、可开关。不允许把
干活的循环藏在服务进程里，也不允许靠手工 CLI 命令批量灌入。

- 被删除：工作区文件全量扫描。它把工作区里每个 `.md`/`.txt` 当作工作材料、每个文件跑一次
  Agent；一个 1475 文件的头脑风暴目录排了 564 条，一小时里 109 次运行调用有 105 次在读这些
  过程稿。`scan-task-sources` 现在只读 AI 听记。
- 取消隐藏的 `task-maintenance` 常驻循环：它不再扫描来源、驱动业务任务，也不再以
  `task_maintenance.*` 健康组件伪装成业务服务。会议 Memory 写入由自身的 Dispatcher 队列完成；
  错误收口和孤儿任务恢复由服务启动恢复、Consumer/Dispatcher 的租约机制负责。
- 并入定时任务「补查遗漏的钉钉消息和日历更新」（`recover-recent-messages`）：钉钉待办完成扫描
  （`scan_completed_dingtalk_todos`，Derek 2026-09-25：完成由新证据驱动，不单独检查）。它按页列出
  Derek 创建的已完成钉钉待办（`dws todo task list --status true --role-types creator`，每页 20 条、
  最多 30 页，找齐所有活动链接即停），列表里出现的已链接待办直接关闭本地 TODO 或业务 Task，
  完成证据记为 `dingtalk_todo:<taskId>`，不经 Agent。此前的做法是逐条读取每个活动链接（660 条），
  且只在手动运行的每日维护里执行，最近完成的待办在服务里全部仍是开放状态。同一步先按已保存回执
  收口结果未知的待办创建（`reconcile_unknown_business_task_todo_creates`），这是投递记账，不是完成检查。
- 已退役：定时任务「投递到期的跟进事项」（`process-follow-ups`，原先是每 60 秒的隐藏循环，
  2026-09-18 改为每 5 分钟的定时任务）。Derek 2026-09-25 决定催办只由他在 Task 详情页点按钮发送，
  该定时任务当天先停用，随后删除：服务启动时 seeding 用控制台同一个软删除
  （`delete_scheduled_task`）删掉 migration key 为 `follow-up-delivery-v1` 的任务，运行记录保留为历史；
  `process-follow-ups` 命令、每日维护里的催办步骤一并删除。
- 任务长期记忆写入**不是**定时任务（Derek 2026-09-24：「写入记忆不应该是个定时任务，而是系统层
  自动的」）：任务结束时在 `finalize_orchestrated_reply_task` 同一事务里入队，由统一 Dispatcher 的
  `task_memory_write` adapter 立即领取写入；退避重试的行到点再被领取，重试上限后进 Attention。
  见 `docs/architecture.md`「任务长期记忆」。
- 仍为常驻循环的只有 `meeting-delivery`：它只投递已审核通过的会议结论，10 秒一轮就是它的意义；
  以及备份、cron 调度/派发、探针等不产生业务判断的基础设施。

任务卡死的回收由服务启动恢复、Consumer 的 stale-age 检查和 Dispatcher 的过期租约处理共同负责；
不再依赖一个隐藏的业务级维护循环。`recover_stale_runtime_attempts`/`recover_expired_terminal_task_runtime_attempts`
此前只在服务启动时跑一次：一个在进程运行期间才过期的租约（owner 进程已死、route 卡住）会
永远停在 `starting`/`running`，`_claim_runtime_attempt` 复用同一 workload key 的现有行时不
检查过期，新的领取请求只会拿回同一条死记录；这条非终态记录还会一直卡住部署前的
`in_flight_work()` 静默检查（Derek 2026-09-28：查了两条孤儿 `weekly_okr` attempt，租约在
约 1 小时 40 分钟前就过期，背后没有存活的 Codex session，`python -m app.deploy` 因此连续
几个小时都在「service did not become idle」上空跑）。修复是把这两个恢复函数也挂到一个新的
常驻组件 `runtime-attempt-reclaim` 上，每 5 分钟跑一次，和 `database-backup`、探针一样是
命名、受心跳监控的基础设施循环，不是隐藏循环。

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
- OKR 无头来源启动使用进程锁；并发调用者取得锁后必须再次读取共享缓存，复用前一个调用刚刷新的认证信息，不能重复启动浏览器或把正常刷新误报为锁超时。锁等待上限覆盖一次完整刷新周期；认证刷新通过 `app.service_browser.launch_service_chrome` 使用每日 Chrome cookie 副本启动真 Chrome，来源命令只负责在上层超时时终止自己的 worker 进程，不再启动或清理独立的 bundled Chromium 子进程。
- OKR 无头来源在请求业务 API 前校验新捕获认证的有效期。专用浏览器会话过期时返回明确的 `okr_headless_session_expired` 服务错误，不得继续请求并把认证失败误报为周期不存在。
- 个人季度不存在由共享 source 的 `MissingOkrPeriod` 类型表示：只有 provider 整数 code 0、完整且结构有效的个人季度列表及有效季度身份才能产生该结果。headless wrapper 输出绑定请求 userId/periodLabel 的 `availability.status=goals_not_established`、UTC 采集时间、providerCode 0、periodsComplete true 和真实季度列表，不伪造 `processed` 空数组。认证准备、HTTP 401 和未分类共同来源失败保持 shared 技术失败；可明确归属单次成员读取的 HTTP 或格式失败使用 member 范围。错误按结构化 scope/code/detail 回到 source adapter 并抛出 `OkrLiveSourceError`，不靠错误文字猜缺目标或只用失败人数判断共享认证。依赖 wrapper 部署前必须核验本机共享 SDK 类型与打包 source 合同一致；保留共享目录无关改动。
- 完整的周 OKR 流程（实时读取、全部管理者分析、文档发布、群消息发送）使用独立的全局 run lease。领取在 SQLite `BEGIN IMMEDIATE` 事务内完成，运行期间周期续租，结束后按 owner 释放；并发调度或人工恢复只能有一个进入流程，其他调用返回 `analysis_in_progress`。`last_attempt_at` 仅用于失败重试退避，不能作为长任务仍在运行的判断。进程异常退出后不再续租，租约到期即可由下一次调度恢复。
- 过期的单人周 OKR 分析如果已经被同一管理者更晚周期的成功分析覆盖，启动恢复将旧作业置为 `completed`，记录 `superseded_by_later_completed_week` 并清除租约。它不再显示为当前 `running`；若相同自然键缺少缓存，正式分析流程仍会重新领取。
- 周 OKR 分析任务每次获得新的租约时使用新的 runtime 执行代次。单次执行中的结果格式修正保持有界；已终态的旧代次不得阻断同一分析任务在后续租约中的重新执行。
- 周 OKR runtime 租约过期并已记录为技术失败时，启动恢复必须同步关闭仍为 `running` 的父分析任务；父任务不得在没有有效 runtime 所有者时继续显示执行中。
- 所有需要 `BEGIN IMMEDIATE` 的 Store 写路径统一经过同一个有界重试事务。短暂的 SQLite 写锁在 Store 内等待并重试；只有超过上限的持续锁才上升为服务错误。队列 claim、反馈批处理和恢复路径不得绕过这一规则。

### Cron trigger 与 Consumer Dispatcher

Agent Cron 保存任务定义及其结构化 Skill refs、首选 Runtime route/model/options、工作目录、Cron
和时区。Scheduler 每次只计算当前时间之后的最近触发点，不枚举停机窗口，所以没有 catch-up。
手动运行只追加一次 manual trigger，不改变 `next_run_at`。若上一轮关联 execution 尚未终态，
本轮以 `skipped` 和稳定原因结束，不等待后补。

`scheduled_task_runs` 必须保存非空 `snapshot_json`；测试夹具也使用
`ScheduledTaskSnapshot.from_task(task).to_json()` 生成冻结定义，不省略快照。
同一任务的不同运行使用不同 `scheduled_for`，遵守任务 ID 与调度时间的唯一约束；
验证 execution 映射时也不得通过放宽这些生产约束来构造夹具。

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
Reader 会把 WeChat 共享文章的标题、摘要和 URL 解码为文本，因此链接消息进入与普通文本相同的回复队列；图片目前只保留消息类型元数据，未提供媒体文件或视觉输入时不会被当作可读文本触发。
通过 History 发起的 WeChat 手动 rerun 会在决策提示中明确标记为 Derek 的主动重跑；消息时效和后续上下文仍会提供给 Agent，但不能单独把该 rerun 强制收口为 `no_reply`。

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
其中“会议行动项”命令还会在同一轮只读发现管理周报、项目管理部周报和部门产研周报；报告按文档内容摘要建立带
文档 ID、链接、统计周期和内容 digest 的 `work_summary` 输入，重复扫描同一 digest 不重复入队。
新安装创建的全部默认任务都是暂停状态，用户配好连接器后自行启用；seed 从不改变已有任务的启用状态
（Derek 2026-09-23）。AI 听记同步不再需要 Skill 判断：分页读取
摘要与逐字稿、写入本地归档、维护内容游标都由 `app/minutes_sync.py` 确定性完成，时长不足五分钟的
会议直接跳过。成功运行的归档命令单行结果摘要会持久化到 scheduled run，并显示在定时任务运行记录中，包含
`discovered`、`synced`、`skipped`、`permission_requested`、`permission_pending`、`failed` 计数及可操作的跳过明细。
History 也显示定时触发（Derek 2026-09-25，类型「定时命令」/「定时任务」，规则见 `docs/architecture.md` 的
「History 语义」）：任何定时任务的失败触发（之后同一任务成功过一次即为 `recovered`）、Agent 形式任务因不可用
被跳过的触发，以及结果不交给 Agent 的服务命令（听记同步、听记权限申请、OKR 周报）的每次成功运行；把结果交给
Agent 的生产命令成功时不进 History，它们排入的每一项各自是一条 History。邮件 provider 动作同样进 History
（类型「邮件动作」），失败口径与 Attention 相同。

微信 History 的任务完成与消息投递分别投影：同一任务、会话和当前执行代的最新投递为
`failed` 或 `send_unknown` 时，失败 Attempt 继续在列表筛选、详情、状态计数和图表中显示
`failed`，不能被候选任务的 `done` 掩盖。旧代、其他会话和已有后续成功投递的失败不覆盖
当前状态；旧 pending Attempt 对应已完成任务仍显示 `done`。这些读取不改写历史记录或投递
状态，也不派发重试。
该投递优先规则只覆盖物理 `send_status=failed` 的 Attempt；非失败历史行继续按原任务投影，
列表、详情、队列计数和图表不得各用不同的覆盖条件。
最新投递按 `(reply_task_id, execution_generation, id)` 非唯一覆盖索引关联，避免对每条失败
Attempt 重扫全部微信投递历史。结构哨兵要求该索引存在，初始化在旧投递表重建后补齐；
只补索引，不改写回执、失败或发送状态。生产升级先完成并验证 SQLite 备份。
听记权限扫描在打开后台后显式查询最近 30 天至次日的历史记录；后台默认只筛当天，
空表不能直接归因为账号缺少管理权限。申请按持久化的 `requested_ids` 去重，
只有听记页面读回已申请状态才计为成功。
退订页返回的固定外部拒绝类别即使附带 `;operation=...` 阶段信息，也保留失败历史、
归为外部依赖故障而不计入待工程修复的 Attention；浏览器/会话自身错误仍计入 Attention。
## Chrome 登录态副本（系统服务）

OKR 服务包装器与共享来源复用同一有效请求头收集器：首次请求头缺少令牌或令牌已过期时继续等待后续有效请求，不能用第一条不完整请求锁定整个刷新轮次。只有满足有效期与提前刷新窗口的令牌才进入缓存，实际 API 读取前仍校验有效期；不输出令牌或认证头。

OKR 本机 SSO 在点击当前账号后先检查指定组织是否已可见；组织选择页仍使用 `login.dingtalk.com`，不能仅凭该域名就认定需要原生确认弹窗。指定组织已可见时直接选择原组织；尚未可见才进入既有原生叮当 OKR 确认路径。组织选择失败时，只有 URL 的协议、域名和路径与配置的 OKR 入口一致，才允许单组织流程省略选择；空白页、其他域名或同域其他页面不能被记为已跳转成功。此区分不改变账号、组织、权限或无头实时来源要求。

需要登录的无头浏览器任务（退订链接、听记权限申请、Dingteam OKR）不各自重新登录：服务每天 `06:00`（`Asia/Shanghai`）运行“同步 Chrome 登录态”（`sync-chrome-cookies`，定时任务，`chrome-cookie-copy-daily-v1`），用 SQLite 备份接口把 `~/Library/Application Support/Google/Chrome/Default/Cookies` 复制到服务数据库旁的 `chrome-cookies/Default/Cookies`，再按明文 `host_key` 删掉 `CEO_CHROME_COOKIE_DENY_DOMAINS`（逗号分隔的域名，含子域名）里的银行、券商和支付类域名，其余全部保留供各任务复用；这份名单为空时命令拒绝执行。整个过程不解密任何 cookie，也不碰钥匙串。

所有任务只通过 `app/service_browser.py` 的 `launch_service_chrome` 启动浏览器：真 Chrome、始终无头、指向调用方自己的资料目录；启动前把最新的副本放进该目录的 `Default/Cookies`，并去掉 Playwright 默认的 `--use-mock-keychain`（否则 Chrome 解不开复制来的 cookie）。这样由真 Chrome 自己读“Chrome Safe Storage”钥匙串项，不会弹授权。副本是快照，会话过期后等下一次同步；不连接你正在用的 Chrome（Chrome 136 起默认资料目录不允许远程调试）。

另有一个每天 `19:30`（`Asia/Shanghai`）运行的“申请读不到的钉钉 AI 听记”服务命令：读取听记管理后台，逐条在听记页面提交访问申请，并以页面读回状态计数；对方批准后，`20:00` 的归档任务会读取内容。该后台由服务的共享无头 Chrome 打开（`app/service_browser.launch_service_chrome`），带的是每天复制的 Derek 本人 Chrome 登录态；这份副本能把他认到「选择你管理的组织」那一步，再由代码选中「北京星尘纪元智能科技有限公司」（`MINUTES_CONSOLE_ORG`）进入控制台。每次启动都会用当天的副本覆盖 profile，控制台自己的 `access_token` 不会留到下一次，所以这里没有自己保存的会话，也没有过期和续期一说；副本里没有钉钉登录态时命令直接失败，补救办法是在 Chrome 里重新登录钉钉并等当天的 `sync-chrome-cookies` 跑过，而不是在这里扫码。申请命令结果摘要同样持久化在 scheduled run，并显示在定时任务运行记录中，包括本次发现、已申请、已可读、申请人未解析和失败数量。OKR 周报同样是服务命令：它唯一的动作就是执行一条确定性命令，而那条命令的实时 OKR 读取会跑到五十分钟以上，任何 Agent 超时都装不下，被杀之后命令还会脱离运行记录继续跑。因此发现与同步类种子任务都不是 Agent 形式，Cron 在所有模型路由都不可用时仍然照常工作。只有两个报告是 Agent 种子任务，走标准 Consumer → Audit 生命周期：每周六 `12:00`（`America/Los_Angeles`）的“准备 CEO 管理周报”（`ceo-weekly-report`），和每天 `21:00` 的“发送 CEO 每日总结”（`ceo-daily-report`）。每日总结的必需输入全部来自服务自己的记录：Agent 先运行只读命令 `python -m app.cli daily-report-facts --scheduled-run <触发记录 id>`（`app/daily_report_facts.py`）。报告窗口由服务算：终点是本次触发的 `scheduled_for`，起点是同一任务报告日期更早、且真正发出了报告（执行任务 `done` 且最后一次处理结果为 `completed`；无需处理或线路不可用被跳过的也是 `done`，不算）的最近一次运行的触发时刻（断掉的日子并入下一份，同日重跑沿用原起点），从未成功过则回看 24 小时；报告日期是终点的北京日期。命令取得窗口内结束的会议及其已发会后跟进、当天有活动的 Task-first 业务 Task 及其当天事件、全部未解决的业务需关注项（`business_attention_items`）、当天在邮箱里被标为重要的邮件（已执行的 `flag_important` 动作；Agent 只摘有管理含义的，其余计数）、当天处理过的事项（不含 skipped，只计数），以及 Attention 口径下等 Derek 处理的事项；截止日期不从 `deadline_at` 自行推算（见 `docs/task-semantic-storage.md`）；再扫描窗口内群消息、按需补读听记，写成七段报告，发布到知识库“🎯  目标与执行”/“CEO 每日总结”下的同日文档（同日重跑覆盖同一篇）并读回，最后以 Derek 本人身份单聊发给「磊哥」（不用机器人）：要点、需介入条数、当天文档标题和「CEO 每日总结」文件夹链接（正文在执行轮就由服务定稿，那时文档还不存在，所以放文件夹链接）。这条消息走服务的已审核消息通道：执行轮提 `dingtalk-chat` 单聊动作（`target.user_id` 为 Derek 的 userId），服务加签名并记投递台账，Audit 只读审核，审核通过后由 System Executor 使用原生 sender 执行精确持久化动作。该通道接受的任务频道为 `DINGTALK_MESSAGE_CHANNELS`（钉钉消息与定时任务，Derek 2026-09-24）；此前只接受钉钉消息任务，定时任务没有合规的发送方式（运行 84467 因此改用命令行发送并被判不合规）。某个来源读不到只写进报告的覆盖说明，不向 Derek 追问材料。该命令登记在 `app/native_cli_metadata.py` 的服务只读命令中。周报的必需输入同样来自服务：Agent 先运行只读命令 `python -m app.cli weekly-report-materials --scheduled-run <触发记录 id>`（`app/weekly_report_materials.py`，同样登记为服务只读命令）。目标周一是运行时刻（按北京日期）之后的第一个周一（周六跑就是后天的周会，绝不是已开完的那次），窗口为其前一个周一 00:00 到目标周一 00:00（北京），截止取运行时刻与窗口终点的较早者。命令在知识库“🎯  目标与执行”/“1. 管理层周会”下找“{年} 年：管理层周会”（是一篇文档，每周的周会文档是它的子页面；名字比较忽略空白），按标题 `{年}年{月}日管理层周会`（月日不补零）找目标与上期文档，找不到只报 `exists: false` 不猜，同名多篇、列表不完整都直接报错；会议清单来自会议队列（`meeting_alignment_jobs`）里窗口内结束的会议，附参与人、会后跟进和听记链接，归档转写文件在才给路径。周报**只写进目标周会文档的三处**：“二、CEO本周判断”、“三、公司级重点指标”表，以及往“一、重点问题及待办跟踪”表追加本周新问题的新行（已有行、各业务线小节、主题讨论、周报链接都归会议参与人，不动）；目标文档不存在时先照上期文档建；完整七段版留作运行目录里的草稿。业务线还没提交的报告写进覆盖说明，不阻塞发布。周报任务经保存版本 → revision 守卫的一次覆盖写入 → 全文读回核对发布，授权来自 Derek 2026-09-24 对该定时任务的批准。Lark 没有
访问申请页需等待申请按钮实际可用；按钮仍禁用时保留为申请人未解析，下一轮继续尝试。页面仅显示加载壳或其他非权限内容时不能当作已可读，只有听记读取 API 能确认可读。
默认 seed。
内部投递、发送状态确认、错误恢复仍是内部机制，不外化为 Cron；催办不自动发送，只由 Derek 点按钮发送。

Task Agent 的正常 liveness 由结构化 provider stdout 事件驱动：执行器在完整事件行被消费后续租
Agent run，并把它作为有效进展证据；stderr 和未完成的 stdout 片段不刷新进展时间。生产环境连续
300 秒没有这样的事件时，idle watchdog 才会中断当前 turn；`CEO_TASK_CODEX_TIMEOUT_SECONDS=7200`
只是防止泄漏进程长期占用资源的紧急上限，不是长任务的业务完成时限。任务可以在同一 session
上持续或恢复多个 turn，原有同 revision 的重复失败上限仍然有效。

### 应用层边界

应用层不审核 Agent 使用的命令、MCP 工具、Skill、读写模式或工具名称，也不维护
`side_effect_state`、`unknown`、`reconciled` 等业务状态。应用层只校验最终 typed result 的形状，
推进 `done`、`failed`、`skipped` 和 `needs_human`（反馈闭环期间任务停留在 `processing`），并保存去重所需的最小外部事实：
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
是否等人；其他任何失败码记为 `agent_reported_failure`，走有上限的普通重试（同一 revision 连续失败 6 次 turn 即
终止为 `failed`，见上文），Agent 原文保存在 `source_code` 供排查。非失败结果（`needs_human`、`no_action` 等）上的码只作为原因标签，不驱动重试
或授权。编排层依赖的服务码（`runtime_*`、`codex_provider_*`、租约与恢复类）只能由服务写入，
Agent 写入时一律按 `agent_reported_failure` 处理。

服务反馈回调的结果格式校验失败属于结果解析失败：保留原始 `feedback_callback_pair_invalid` 在 `detail`，通过 `ResultParseError` 进入现有 `codex_result_invalid` 纠错流程；每次处理最多两个回合，同一修订连续六次失败后终止为 `failed`。校验不能抛出普通 `ValueError` 绕过编排后由调度器无限重新领取。该异常转换不改变反馈链接配对规则、历史来源校验、审核职责或调度器所有权。

**原则：任何更底层观察到的错误码或原文，被归并成更通用的服务码时都不能丢失，必须原样带到 Agent 报错记录里，供事后排查。** 通用化是给重试/授权决策用的分类，不是删除诊断信息的许可。已落实的例子：`agent_reported_failure` 保留 Agent 原文在 `source_code`；Agent 报告的 typed 失败结果保留其必填的 `summary` 在 `reported_summary`（2026-09-28，见下段）；Codex 进程失败保留 stderr 与 JSONL 里的 `error`/`message`/`detail`/`reason` 字段在 `detail`（`_process_failure_detail`，裁剪到 1000 字符、脱敏凭证）；路由执行错误沿 `__cause__` 链找具体解析/校验异常的 `reason`（`_runtime_failure_detail`）；退订浏览器错误保留异常类名和截断消息（见上文退订段落）。新增一处错误归并时必须同样保留来源，不能只留下分类码。

任务 Agent 的 `memory_recall_used` 是 Agent 给出的上下文记录，不是服务的工具调用验收条件。
Task Agent 的 Memory 写入边界由提示词约束：可调用 `memory_connector.memory_write` 保存
当前证据支持的持久、重要项目风险与进度/状态更新，注明项目与原始来源，并以来源时间作为
`created_at`，使用 `type="text"`；没有持久项目变化则不写。此行为由 prompt 引导，不是工具层权限隔离。

Task Agent 不直接调用外部 TODO 写入；Task 7 的创建/完成 intent 在 Task 语义事务中排入
`business_task_todo_sync_outbox`，由 dispatcher 按 `business_task_id` 执行。创建仅限正式、开放、
有来源支持的明确负责人、已接受承诺且有来源支持的可解析 `committed_deadline_at` 的 Task；
estimate、requester/external deadline 和 next check 均不能充当镜像期限。外部创建保存 provider ID
再读回；若首次读回失败但 ID 已保存，后续 TODO 状态拉取按该 ID 重试读回并收口链接/outbox；若创建
结果未知且没有 ID，不自动重复调用或排入另一创建键。同一 Task 存在未收口创建 intent 时不会再排新的
创建。`failed` outbox 在有界 `next_attempt_at` 退避后才可再次领取。外部 TODO 状态轮询只按其 Task
链接关闭 Task，写入完成来源证据、事件，并关闭该 Task 的 follow-up；关联 Project 或同聚类 Task 不随之完成。

**催办由 Derek 点按钮发送**（Derek 2026-09-25：「催办应该变成 UI 上的一个按钮，让用户决定是否要发送催办（点击一键发送），如果后面有新的信息更新了 task，应该取消可催办状态」）：Task 详情页「催办」区列出该 Task 的全部 follow-up；待发送（`draft`/`approved`）和发送失败（`failed`）的每条带一个发送按钮，点击调用 `POST /api/console/tasks/items/{task_id}/follow-ups/{follow_up_id}/send`（带当前 `revision`），由 `send_business_task_follow_up` 当场发送：不看工作时间、不经 Agent 审核，点击就是决定。失败或结果未知的发送保持 `failed` 并把结果留在该行上，不排 Agent 修复；再点一次作为新 revision 发送，原 attempt 不改。Task Agent 把新信息应用到已有 Task 时，在同一事务里用 `cancel_pending_business_task_follow_ups` 撤回该 Task 其余未发出的 follow-up（`cancelled`，原因写明被哪条来源更新），同一来源信号新建的 follow-up 保留，正在发送的不动。

Task 7 的新 follow-up 只从链接来源信号的精确群/单聊目标与明确、可解析 `next_check_at` 创建，
不从 deadline 推导时间或猜收件人；`next_check_at` 只是建议的催办时间，不会到点自动发送。群聊回到精确
来源会话并提及来源证据支持的负责人；单聊发给来源明确指定的负责人账号，原会话 ID 保留用于核对。发送
（仅由上述按钮触发）有独立的 claim、lease、revision、幂等 UUID 和回执记录；发送中的租约过期或网络中断
标为 `unknown`，该 follow-up 显示为 `failed`，不自动重发，也不排 Agent 核查，是否再发由 Derek 再点一次决定。
旧 `follow_up_drafts` 只是历史记录：不会再被发送，失败的旧记录也不再提供「让 Agent 重新核验负责人 /
取消本次跟进」表单（`/follow-ups/{id}/resolution-form` 已删除）；小时质检和安装向导的 dry-run 检查
不再把未发送或失败的旧记录当作积压。Task 8 的旧记录导入与生产切换仍是独立边界。

### Task 的来源与证据

Derek 2026-09-25 定的规则。Task Agent 用同一个长期 session 是为了不丢上下文，所以**不禁止它用之前的证据**，也不禁止它通过 `memory_recall` 顺着 provenance 找到原始来源；但每条来源都要能回溯到出处。

Console 的来源读接口分两步保持正文唯一：
`GET /api/console/tasks/projects/{project_id}/evidence?page=N&page_size=M` 只分页返回该 Project
保存的 `BusinessProjectEvidence` 引用，沿用 Store 的“最新窗口优先、窗口内既有顺序”，并返回
total/has_more/next_cursor；`GET /api/console/tasks/signals/{signal_id}` 再按精确 ID 返回完整保存的
`BusinessTaskSignal` 和不可变 source document 原文。长 JSON/原始正文不使用详情页的 2048 字符
投影，也不经过 display normalization。未知 Project/Signal 返回 404，已存在但无证据的 Project
返回空列表 200；读取在同一个 `read_snapshot` 中完成，不更新 Task、Project、Signal 或证据关系。
Project 详情仍只展示原有 20 条有界来源并保留 context 引文 pinned 语义。

**每个决定引用什么**

| 字段 | 含义 |
| --- | --- |
| `evidence_origin` | `current`（当前 Work Item，默认）、`session`（此前 session 里读到的）、`memory`（Memory provenance 指向的原始来源） |
| `source_ref` | 来源引用；`session` / `memory` 时是**原始来源**的引用 |
| `source_excerpt` | prompt、Skill 和字段说明要求原文中连续的逐字摘录，保留标点、空格和换行；普通 Task 的原有校验仍不检查来源子串，日期证据及 Project assessment / Attention 引文仍按各自既有原文规则校验 |
| `source_link` | 来源有链接就必须给 |
| `source_description` | 没有链接时用文字描述在哪里，例如钉钉消息写“群 + 发送人”；`source_group` 加 `source_person` 也算 |

- 对 `session` / `memory`，链接和描述至少要有一个，否则模型校验拒绝。对 `current`，缺的定位由服务补：URL 引用、听记摘要里的页面链接、或会话加发送人；都没有时记录里仍有 `source_ref`。
- 普通 Task 引文的原有校验仍不检查是不是原文子串，逐字摘录是 Agent 指令，不是新加入的验证器。仍然要求：引文非空；负责人的名字必须出现在负责人引文里；日期证据仍要求是来源子串。Project assessment / Attention 的原文核验按前述独立契约执行。
- `session` / `memory` 只能用于完善已有 Task（`update_fields`）或记录候选。创建正式 Task、晋升、确认接受、身份合并仍要当前 Work Item 的授权与身份元数据；日期证据仍要当前来源和明确说话人。
- 引用的旧证据单独存成一条来源信号：`source_type` 为 `session_provenance` 或 `memory_provenance`，引文作证据文本，链接和描述放 `context_json`（`source_link`、`source_description`），群和人放会话标题与作者名，`cited_while_processing` 记录当时在处理哪个 Work Item。服务无法重读原文，所以这条记录标明的是“引用而非当下观察”。

### 展示型任务建议的领域存储

`RecordTaskSuggestion` 在既有业务 Task 表保存来源支持的建议，本分支 Task Agent 已接入 `TaskDecision.suggestion`，页面按当前 candidate 建议与真实晋升 Task 区分。新建议固定为 open candidate、commitment=none、无 formal_basis，实际 owner 和 deadline 为空；建议人选、理由、职责与事实的真实 Signal 引用仅在 `suggestion_json`。职责证明可以来自先前项目/人员分工，不要求最新风险原文点名，但不能把 Agent 理由当原始来源。建议命令只使用同一领域事务的 Task/Signal/evidence/event/Project link，既有建议 ID 的相同资料不追加事件，实际变化追加 details_changed；无 ID 的原来源重放保留最初任务身份，包括之后已由人类晋升的情况。建议更新省略标题或描述时保留原值，不把默认空字符串当成清空。

无 ID 的重放身份来自原创建或其他已有事件的命令结果，不来自所有 evidence link。后来来源只补证据且资料未变时，不制造变更事件，应用及重放都保留明确 task_id，由 Agent 对已检索事项判断关联。一条来源可支持多个 Task，合并也会复制证据关系，不能据此自动选定唯一任务或合并。

人类明确指派用既有 promotion 在同一 ID 晋升，保留 origin=agent_suggestion 和建议依据，实际 owner 才来自人类指派证明；真实接受、承诺日期与 TODO 资格仍按原生命周期。展示建议不产生 acceptance、follow-up、通知或 TODO outbox；不会因为历史 origin 永久拦住已由人类接受的正式任务。旧任务只增 source/空建议默认列，阶段、承诺、负责人及历史 JSON 不变。
这里复用已有“原始观察／未观察的引用”区分：项目证据、上下文引文及建议的发现/职责/事实来源不接受 `memory_provenance` 或 `session_provenance` 作为原文。并非限制工具权限，也不按正文关键词分类；原有一般候选 Task 使用 memory/session 完善信息的语义不变。

**批处理里的容错**：同一场会议的多个条目在一次提交里应用，一条不成立不应拖垮其余。

- 负责人证据不成立（负责人的名字不在引文里）的更新，只跳过那一条并写明原因。
- 一条只重复 Task 现状的 `update_fields`（标题、状态等都没变，且“unknown”的相关性不算判断）记为跳过，不报错。
- 检索上下文一定包含同一来源此前产生的 Task，不论词面排名，否则 Agent 看不到它们，会把同一件事再记一遍。

### 听记行动项的负责人

**问题**：钉钉从听记抽出的行动项，`executorList` 恒为空，负责人不在行动项里。最初只从对话里找：每条行动项带 `createdTime`（在录音里的毫秒位置），逐字稿每段带说话人昵称和起止毫秒，截一段窗口出来找负责人；但窗口只锚定在行动项被抽取的那一刻，指派往往发生在窗口之外——9-28 复查发现至少 4/7 条“无负责人”候选任务，负责人其实写在钉钉自己生成的会议摘要里，只是不在这个窗口内（Derek：肯定是信息没有挖掘全）。所以负责人来源现在有两处：转录窗口，和整份会议摘要。

Task Agent 会把负责人证据再结构化为 `owner_kind`（`individual` / `team` / `unknown`）和 `owner_relation`（明确分派、自承诺、会议行动项、仅发言或未知）。只有明确指向单个个人且关系为前三种之一的行动项，才记录为正式 `meeting_action_item` Task，并保持 `assigned_unaccepted`；团队负责人、仅发言或关系不明仍是候选。缺少稳定 ID、日期或完成标准属于后续补充，不会单独把已经明确指派的行动项降级为候选。AI Minutes 行动项不以 `external_todo` 作为正式依据。

**做法**（`app/minutes_todo_context.py`，由 `scan_meeting_todos` 调用）：

1. 入队前读完整份逐字稿，**再读一次这场会议自己的 DingTalk 摘要**（`get_minutes_summary`，`minutes_full_summary` 取其 `result.fullSummary`）。
2. 转录窗口：对每条有有效 `createdTime` 的行动项，截取包含该时刻的那一段及前 6 段、后 4 段，每句写成一行“说话人：内容”放进 `transcript_excerpts`（钉钉的 `sentenceList` 把整段当作一句，所以在句末标点处自己拆句）。
3. 会议摘要：原样整段放进 `meeting_summary`，不做关键词或分节切片——钉钉的摘要经常直接写“行动项：**磊哥**与**周俊杰**负责代码 Review”，但可能出现在整场会议的任何位置，切片会漏掉切片规则没覆盖到的写法。
4. 没有摘录/摘要的情况：`createdTime` 缺失或为 0、超出逐字稿的行动项在转录窗口里没有摘录；逐字稿为空、或摘要为空字符串，对应字段就不放进去。逐字稿或摘要任一读不到时，这场会议本轮不入队、也不记为已见，下一轮重读。
5. 去重摘要仍只覆盖行动项本身，所以之后补读逐字稿或摘要不会让处理过的会议重复入队。

**提示词规则**：负责人是对话把事情交给的人或接下的人，不一定是说话人（甲说“你写下来”是指派给被点名的乙）；负责人引文可以来自 `transcript_excerpts` 或 `meeting_summary`，是一句带说话人标签、包含负责人名字的话（可摘取），常常不是决定本身的 `source_excerpt`；钉钉对认不出的说话人给的“发言人 N”是占位标签、团队或部门名（“研发”“算法团队”“Product Marketing”）也不是人，两者都绝不作负责人；转录窗口和摘要都说不清就留空——这是来源本身的边界，不是要去凑一个负责人出来。日期仍不从听记取，因为没有可信的说话人到身份映射。

**回填已入库的候选**（已处理过的会议不会被再读到，所以必须回填）：`python -m app.cli backfill-minutes-owners`（`app/minutes_owner_backfill.py`）。

- 找出“有无负责人的开放候选 Task，且发现来源是 AI 听记”的会议，重读行动项、逐字稿和会议摘要，用同样方式组装 Work Item，以新的来源引用再入队一次，由 Task Agent 用普通更新路径补负责人。
- 默认只预演；`--apply` 才入队；`--limit N` 取最近的 N 场。
- 转录窗口和摘要至少要有一个能读到才入队；两个都读不到才跳过该会议，`decisions` 里写明原因。
- 来源引用里的摘要覆盖行动项、转录摘录、会议摘要和 `BACKFILL_REVISION`，所以重复运行不会重复入队；改了提示词、引文格式或校验规则后递增这个版本号，才会让上一轮没补上的会议再读一次（2026-09-28 起是 4）。

### 控制台对候选 Task 的操作

Derek 2026-09-25。候选只是没确认的猜测，所以可以在控制台“忽略”，也可以“恢复”。Tasks 页面将“正式任务”和“待确认线索”分成独立视图；“全部任务”保留为诊断入口，可同时查看两种 stage，但不作为正式任务的语义替代。默认关注页仍只展示 CEO 需关注投影，正式项目单独展示。

- Tasks 页“待确认线索”（以及诊断入口“全部任务”）的候选行和候选 Task 详情页有忽略按钮，调用 `POST /api/console/tasks/items/{task_id}/candidate-decision`（`action` 为 `ignore` 或 `restore`，实现在 `app/task_console_actions.py`）。
- 忽略把 Task 状态改为 `cancelled`（阶段仍是 `candidate`），恢复改回 `open`。走普通的 `TaskSemanticService.update_task`，留下与其他变更相同的痕迹：一条 Derek 署名的 `console` 来源信号（`author_kind=human`）、`correction` 证据链接和 `status_changed` 事件。原发现证据不动，什么都不删除。
- 来源引用带上一次的 `updated_at`，同一状态下重复点击返回 409 `not_applicable`，不重复记录。提交后照 Task Agent 的做法重算受影响的关注成员。
- 只有候选可以忽略：正式 Task 有负责人和来源引文，状态只能由证据推动。控制台**不提供**“确认为正式任务”——晋升要求已识别的负责人和来自来源的负责人原话，点击给不出这两样，见 `docs/task-semantic-storage.md`。
- 忽略后的 Task 仍在“全部任务”里（灰显、划线），Task Agent 见到已取消的候选不会自行恢复它。
- `GET /api/console/tasks/all` 另接受 `owner=assigned|unassigned` 和 `sort=updated|created`，非法取值返回 400。
- 服务写入的固定事件原因现在是中文（如“根据来源证据记录为候选任务”）；此前写入的英文原因是历史记录，不改写。候选没有负责人是正常状态，界面不把它当缺口。


## 任务类型

- `okr_review`：指定人员和周期的逐 KR 评审。执行 Agent 按当前业务 Skill 读取实时
  `processed.objectives`/`processed.okrRows`，再生成评审；审核 Agent 审阅并反馈修改，
  修正版通过后才发送。底层读取错误（认证失效、浏览器/profile 锁、周期解析失败等）
  必须原样保留，不能被 `consumer_retry_exhausted` 覆盖。服务入口先复用有效 token；
  缓存过期时只启动 headless 浏览器刷新，不打开可见窗口，也不把“禁止可见浏览器”
  误解为“禁止刷新”。
- `weekly_okr`：定时生成管理者 OKR 进度周报。评分范围是当前 OKR 周期起始日至报告日（例如 Q3 至今），本周窗口只用于标记新增进展和风险。全流程先领取并持续续租全局 run lease，避免超过重试间隔的长任务被重复启动；每位管理者的模型分析调用直接使用服务统一配置的应急总上限（生产为 2 小时）和 idle watchdog（生产为 5 分钟）；只要持续产生有效结构化进展事件，模型可以在应急上限内继续运行。超时只失败该管理者的分析任务并释放其租约，主流程不会无限等待；管理者分析的父租约覆盖完整模型调用、一次结构化校验重试及运行时恢复余量，避免维护扫尾在分析仍运行时提前回收并造成终态冲突；结构化输出同时固定每个管理者恰好 3 个文化价值观维度，模型复述 KR 标题且省略可用 ID 时，在行数准确且标题非空的前提下按实时源顺序绑定，仍校验重复、缺行和全名单覆盖；分析、报告发布和群摘要获得 provider 成功结果后，推进周报成功日期。每位 OKR 名单成员都必须保留一条评分区块；系统没有本周进展时按无新增进展和证据缺口评分，不得因为分析输出缺行而发布部分人员版本。
- 实际使用 AgentOrchestrator 的业务候选遵循新的 Consumer → 只读 Audit → System 执行契约。微信 task3 仍由 dispatcher 调用 WechatReplyConsumer / WechatDecisionRunner，持久化 delivery 后由独立 Sender 循环处理，没有独立 Audit 回合；本次未将它迁移到 candidate/review/SystemExecutor。其 audit_summary 是既有决策字段，不是独立审核回执。扫描、下载、登录等服务命令沿用技术结果验证，系统任务对应表不为这些路径新增 Audit 回合。

## 文档索引

- 总体 A/B 架构：`docs/architecture.md`
- 路由失败和恢复：`docs/runtime-route-recovery.md`
- Runtime 路由、fallback 决策与 429 重试：`docs/architecture.md` 的「Agent 失败重试」与各 Runtime 路由小节，
  实现在 `app/runtime_fallback.py`
- Consumer/Audit 反馈设计：`docs/superpowers/specs/2026-08-06-consumer-audit-agent-design.md`
- OKR 领域输入和输出：`docs/superpowers/specs/2026-06-08-okr-review-runner-design.md`
- 当前实现：`app/agent_orchestrator.py`、`app/consumer_agent.py`、`app/audit_agent.py`、`app/okr_review.py`、`app/weekly_okr_report.py`、`app/store.py`
- 系统错误码目录：[`docs/error-catalog.md`](error-catalog.md)
### Production deployment lifecycle

SQLite immediate-write retries apply only before the transaction body starts.
Once the body starts, a lock failure rolls back and propagates the original
exception; a context manager cannot replay its caller's body. SQLite failure
diagnostics include a bounded caller chain and thread identity, without frame
locals, SQL parameters, or message bodies. These diagnostics identify the
waiting operation, not the owner of a cross-process write lock.

A service-command trigger with a persisted execution link has already returned
from that command. If source terminalization was interrupted, its next consumer
resumes terminalization under the current claim guard instead of executing the
command or overwriting the immutable link again. The persisted execution kind
and command must match the trigger snapshot; mismatches remain errors. This
closes the result-persisted/source-pending crash window without replaying effects.

Production deployment is serialized at both the repository and service
boundaries. The deployer first waits for the persisted work leases to drain.
This includes valid dispatcher leases before an Agent runtime exists, meeting
preparation, and already-claimed meeting deliveries. Expired or terminal claims
and unclaimed ready-to-send work do not count as active execution.
It then bootstraps no new claims by stopping the launchd service before taking a
database backup or changing the production checkout. It starts the service
again with launchd `bootstrap` after verification, then performs the normal
health and queue readback. This prevents the deployment process and worker
process from writing the same SQLite database concurrently.

### Public information and native reply recovery

The fixed Consumer/Audit rules now distinguish established public disclosure
scope from factual support. All-staff public OKRs and ordinary coordination
within their established audience do not require an additional personnel
privacy grant. A privacy refusal must identify concrete nonpublic information
and an audience mismatch. Public visibility still does not support invented
conclusions, dates, commitments, or nonpublic individual assessments. This
contract is included even when a persisted custom Audit Rules template exists.

Native DingTalk replies persist `native_reply_dispatches` before the provider
call. After an interrupted call, another invocation of the same prepared
delivery automatically performs read-only reconciliation instead of sending
again. A confirmed match requires exactly one unrecalled message with the
original conversation, quoted trigger, prepared body with the provider's
native mention of the verified trigger sender (rendered whitespace ignored),
and authenticated current sender. Its message ID and readback are
persisted as the normal outbound receipt. Empty, duplicate, mismatched, or
unavailable reads remain inconclusive; a bounded recent-message read cannot
prove absence and never releases the dispatch guard. This is recovery on
invocation, not a new background retry scheduler. A pre-send Audit rejection
does not create a dispatch or bypass authorization. Existing failed generations
without this new boundary still require the formal evidence-based recovery
path; their historical dispatch state is not fabricated.

### Runtime result failure evidence

Reply-attempt queue status computes counts, the latest timestamp and failure
detail from one narrow latest-trigger projection. It does not sort full
message/proposal bodies or repeat the window query for the timestamp. Recovery,
execution-generation and business-object filters remain part of that same
projection; physical historical failures are not current queue failures.

Unsubscribe forms execute through the exact native submitter selected by the
audited DOM model. Its selector is included in the control identity alongside
the form association, method, action and successful controls. The browser runs
the provider's submit handlers; the service does not reconstruct a GET or POST
from the form attributes. A Salesloft page returned HTTP 405 to a reconstructed
POST but confirmed successful opt-out after native submission. Completion still
requires the page's terminal readback, not merely a click or HTTP 2xx response.
An HTTP 204 from the exact bound form target and method is an explicit no-content
submission receipt. Native forms wait for browser navigation completion; async
submit handlers remain responsible for their visible terminal readback.
After a native form submission, the browser waits within its existing bounded
readback budget for the submitted control identity or visible text to change.
An unchanged form is an operation timeout, not evidence of "no reliable entry"
or a reason to manufacture a skipped receipt before an async handler completes.

Rejected provider responses retain their numeric HTTP status in the
sanitized failure detail when supplied by the executor. Private response URLs and
subscription tokens remain excluded. A rejected response is not a success
receipt and cannot terminalize the unsubscribe as done.

Successful multi-family email training runs use the successful `embedding-mlp`
entry in `family_results` for description optimization. The compatibility
`model_id` field may point at a traditional classifier and does not imply
embedding staged evidence. Classic-only runs complete without description
optimization; missing or corrupt evidence for a successful embedding candidate
still fails closed rather than silently clearing the training run.
Each scheduler cycle performs independent model maintenance before provider
observation, so the initial completion poll does not wait for IMAP inventory
or message reads. Observation failures retain their separate health record.

When a Codex turn produces no typed result or an invalid typed result, the
runtime attempt is recorded with the result-stage failure code
(`codex_result_missing` or `codex_result_invalid`) rather than the generic
`runtime_unclassified` code. This keeps Task Agent retries and Attention
classification tied to the actual failure stage; genuinely unknown execution
exceptions remain fail-closed as `runtime_unclassified`.

Unsubscribe page judgment accepts a terminal parent only when that exact email
action has one of the direct executor's existing retryable skipped receipts.
This lets the authorized direct reread obtain new provider evidence without
rewriting a completed reply task to pending. A successful receipt, missing
receipt, or failed terminal parent does not authorize this runtime operation;
the action plan and browser effect authorization remain unchanged.

Audit History list and chart prewarming runs in a daemon background thread
after lifecycle recovery starts. These read-only scans populate the existing
caches but do not gate the HTTP listener or launchd startup health checks.
Health readiness is not evidence that History prewarming or business actions
have completed; their APIs and provider receipts must still be read back.

DingTalk meeting group searches explicitly request 100 candidates per page,
the provider's supported maximum, while retaining the complete-source check.
The CLI's default 20-item pages exhausted its 50-page budget for large
participant queries such as Melody; retrying the same bounded query could
never reach the remaining candidates. A partial response still fails closed;
larger pages do not change recipient ranking or authorize a send.

Worker status reads its local SQLite queue, Email health and component facts
on every request. These facts are not served from the last background payload:
after a worker writes its state, the next status request must reflect it.
External connector authentication probes retain their independent cache.

Typed result parsing preserves malformed or unclosed JSON as a result-stage
invalid-result failure, including its syntax cause. It is not classified as a
missing result. An earlier valid result in the same primary turn remains usable;
no JSON repair or successful external-effect inference is performed.

Manual reruns of scheduled Agent work preserve the saved scheduled execution
context, including its route and pinned Skill content. A missing or invalid
scheduled payload is rejected instead of being rebuilt as a DingTalk message;
the original scheduled run is the authoritative source for explicit recovery.

The approved DingTalk send tool persists its exact prepared body and verified
provider receipt into `sent_replies` as soon as provider verification reports
`sent`, before returning to the Audit turn. A later invalid Audit result cannot
erase this delivery evidence. Pending or ambiguous verification produces no
successful History projection. This records one message's actual effect, not
completion of the whole proposal: the task and external-action completion
ledger still require the existing Audit lifecycle and evidence checks.
# External Command Failure Evidence

External command failure previews retain both the beginning and the end of
long output within the existing 400-character content budget. Python traceback
frames must not displace the final exception code and cause. Structured provider
errors still expose only the existing approved error fields; command argument
redaction, retry policy and action authorization are unchanged. This improves
future failure evidence and does not rewrite already-truncated historical runs.

### Sensitive Result Classification

The post-process sensitive-result guard raises the same result-parse exception
as other invalid typed output. Rejection is recorded as `codex_result_invalid`
at the result stage, allowing the existing Audit result-correction prompt and
bounded attempt handling to operate. It is not a CLI execution outage. The
credential predicate and rejection itself remain unchanged; no unsafe result
is accepted and no content-feedback cycle is consumed by a parse failure.

### Daily Report Source Identity

Daily report `handled_today` facts retain their original conversation ID,
trigger message ID, creation timestamp and Agent run ID alongside the display
title and summary. Reports from previous scheduled runs can share those display
fields; their completed status is evidence for that historical source only,
not the current report's publication or notification. The collector remains
read-only and does not infer or modify delivery state.

### DingTalk Message Text

The current DingTalk `im.message-list.v1` ledger permits a null `text` value.
The message adapter projects that absence as an empty string, preserving the
original message ID, conversation, sender and complete raw payload. It neither
discards the message nor invents text or a delivery receipt. Nonstring values
other than null still fail model validation. Meeting group discovery can read
the remaining discussion evidence without treating absent text as evidence.

# Email SQLite Contention Diagnostics

EmailStore and AutoReplyStore report connection contexts lasting at least one second, including
elapsed time and the caller's file, line, and function. This duration includes
lock acquisition, the body, commit, and close; it is not by itself proof of the
write-lock holder or transaction duration. Logs exclude SQL parameters and mail
content. Correlate the caller with the operating system's WAL-lock owner and
the affected run before changing transaction boundaries.
Shared Store diagnostics retain up to eight caller frames so a context-manager
wrapper cannot hide the business method that opened the connection.

The Console history-detail handler passes an EmailStore factory, not an already
initialized EmailStore, to the Attempt DTO builder. Only an existing
email-channel Attempt opens that store for its classification and unsubscribe
context. DingTalk, WeChat and missing Attempts do not scan email durable state.
Each email detail request still initializes and validates the store and reads
current receipts; initialization failures are not suppressed or cached.

Direct provider-action claims hold BEGIN IMMEDIATE only over classifications
with pending or failed actions, rather than materializing every processed
classification's settled history on each poll. For each selected classification
all sibling plan versions remain visible: an older processing action still
blocks the current plan, dependencies and priority remain unchanged, and the
claim update remains atomic. Fully settled groups cannot yield a claim and are
excluded before rows are materialized. This requires no migration or replay.

Legacy unsubscribe terminalization uses the same lifecycle selector as its
inventory: only `email_unsubscribe_consumer_direct_v1`. An inventoried object
replaced by an `email_unsubscribe_audited_v2` task must remain untouched, even
when its task ID, generation, and pending status otherwise match. The mutation
checks the lifecycle again under its write transaction.
# Task Evidence Repair And Retired Anchors

Meeting claims order attempts by the later of `eligible_at` and `available_at`,
with the job ID as the tie-breaker. A due retry is not ordered by the meeting's
original date: otherwise an old external dependency failure can repeatedly
occupy the single meeting consumer and starve pending meetings. The dispatcher
keyset scan and legacy claim path use the same ordering; eligibility, lease
ownership, and external delivery guards are unchanged.

Meeting delivery batches release still-ready claims on both normal completion
and exceptional exit. Each acquisition persists a fresh token in the separate
`meeting_alignment_delivery_claims` table, in the same transaction as the job
lock. The token is returned only with the claimed job, not stored in the job
row. Release compares job ID, original lock timestamp and nonempty token, so
even a same-second successor cannot be unlocked by a stale batch. The job row
shape stays unchanged for strict parsing during code rollback. Startup recovery
releases interrupted locks and clears abandoned ownership records before fresh
acquisition. Cleanup never changes receipts,
retry deadlines, attempt counts or terminal status. If delivery and release both
fail, a grouped exception records both causes instead of hiding the first.
Persisted `sent` receipts remain authoritative: recovery resumes only calendar
terminalization, not analysis or chat sending. Transcript-only meetings retain
the existing explicit calendar-skip receipt because no original event exists.

Meeting discovery may refresh a waiting recording, but once a job is queued,
claimed, retrying, ready for delivery or terminal, discovery cannot replace its
persisted source snapshot or participant evidence. Recovery refreshes failed
unsent jobs through the explicit replay lifecycle. A failed roster lookup during
rediscovery must not remove `calendar_evidence` from an already queued job.
Recent-meeting replay uses the same ended-recording metadata contract as
discovery, without requiring the provider status to equal the literal `ended`.

Scheduled service incident reconciliation uses the numeric scheduled-task ID to
seek the indexed run history, then verifies the exact conversation identity,
successful command kind and later dispatch time. It must not scan all dispatched
runs once per unresolved error while holding a SQLite writer transaction.

Task Agent validates formal creation and candidate promotion owner citations
before entering its atomic domain transaction. A repairable evidence error is
returned to the same Task session and run with the rejected candidate and
original context, for at most two correction rounds. No domain changes are
committed before validation succeeds. Exhaustion retains a real failed run and
uses the existing work-summary retry policy; it never guesses an owner or
changes a technical failure into `needs_human`. Memory-backed ownership still
requires linked source provenance and an `episode_id`.

Current Work Item Project citations, including nested context and assessment
evidence, use the same exact source identity and contiguous-quote validator
before domain writes and again during atomic apply. Invalid current citations
enter the existing bounded correction rounds with the rejected candidate.
One rejection reports all distinct invalid current citation entries, retaining
each validator reason, source_ref and exact rejected excerpt; repeated entries
are reported once. This does not rewrite or normalize quotes, and the same
two-round budget applies to the entire decision, not separately per citation;
exhaustion remains failed without partial Project or signal writes. Historical
signal citations retain their existing provenance checks. The same read-only
stored Project/assessment validator also runs through a short connection before
domain apply, within these existing correction rounds. Its ValueError reports
the original evidence/identity/membership error to the model; this includes an
existing Attention declaration that omits the card's stored original proof.
Atomic apply runs the unchanged validator again. Database operational errors
and errors raised during atomic apply are not converted to model correction.
No new validation rule, correction budget or recovery loop is introduced.

Date source, actor and exact-precision validation uses the same pure validator
before the domain transaction and during apply. Evidence errors enter the same
bounded correction loop; unidentified source actors or invented dates remain
invalid. Each semantic correction has a deterministic runtime operation key
`<task_agent_run_id>:decision_repair.<round>`; the initial turn keeps its run ID.
The router reuses a completed receipt only within that operation, so a changed
correction prompt actually executes a new turn in the shared Task session.
Corrections retain the original active Task run as their parent, not a Project;
input reset, orphan recovery and expired-terminal recovery close their no-effect
runtime attempts just as they close the initial turn. Existing memory and
deadline backfill parent identities are unchanged.

Both Cron-dispatched Work Summary consumption and the manual
`process-work-items` command use the same renewable Task session lease. A
claimed item whose session is busy is returned to the retry queue before any
Agent turn starts. Completion, exceptions and repair rounds remain inside that
lease; serial workers inside one dispatcher do not substitute for cross-process
session ownership.

An inactive `task-agent-project` anchor is a retained retirement decision.
Weekly-report registry rows and Agent Project proposals must not reactivate it
implicitly. The source-backed Task is saved independently, without a Project
link to that retired anchor, and the run records why the Project was not applied.
The general anchor registration conflict checks remain unchanged.

### 已审核来源、执行进度与失败展示

服务捕获的 source_bindings.value 是原始来源数据，不是 Agent 自己写的短引用字段。
日历卡片等来源可能把完整 JSON 放在 reference 中，不套用候选 reference 的
512 字符上限；来源仍接受严格反馈链接、凭据、嵌套深度与整个结果编码大小检查。
绑定元数据与候选字段仍执行原短字段限制。检查仅使用投影副本，不截断或修改
原来源、candidate digest 或派发前同对象回读比较。

Consumer 完成候选准备时，把原 trigger、消息/材料和计划涉及的 OA 原表单或文档源事实保存为 source_bindings，参与 candidate digest。System Executor 在每个尚未核验动作派发之前重新读取相同原对象，包括首次执行、同次后续动作和恢复部分执行。OA 已完成动作造成的 task 状态/操作记录变化由具名处理器核验，不作为原表单变化；真实表单、文档或上下文变化使该 review 失效，保留已核验动作回执和原人工回答，在同阶段形成新的完整候选并重审。来源读取失败属于可重试技术失败，不执行旧分支。

来源快照中的反馈链接在检查副本中按 Markdown 链接和真实 JSON 容器读取；不要求其他参与者使用本人的外发签名、标签或段落格式。仍严格核对当前配置 host/path/query、生成 token、上下链接同一身份与唯一完整配对，额外未配对 URL 或凭据仍拒绝。只移除已核验 URL 的检查副本，不把来源链接当授权或外部动作回执。当前待发送正文仍要求原始严格格式；原 source_bindings、候选 digest、持久化结果与发送前来源回读比较不变，其他来源字段继续接受原有敏感值、深度和大小检查。确定性的来源结果验证失败记录 runtime_result_source_invalid、stage=result、source=service、retryable=false、session_continuable=false；不归为 CLI/provider 失败并反复恢复同一会话。

Attempt 详情的 system_execution 按 candidate、review、selection 绑定显示当前及历史阶段、声明顺序、未开始动作、失败/不确定状态和 provider 回执。只有 canonical action ledger 中核验成功的动作计为 verified；Audit approve 和用户选择都不能显示为已经执行。

失败 Reply task 的 Attention 诊断只读取本对象本 generation 的当前 run，显示原 source_code（无则 code）与明确标为“Agent 说明”的 reported_summary；缺少或坏 JSON 时保留 task error，不将 Agent 自述当作已证实的 provider 拒绝。History、Attempt 详情和 Reply attempts queue 对微信分别读取本对象当前 generation 最新 delivery：failed/send_unknown 不能被只表示候选完成的 task done 遮蔽。旧 generation 或其他对象的 delivery 不影响当前结果；这些展示不更新任务、投递或授权状态。

独立 Email 退订仍使用原有 direct executor、浏览器步骤和回执。无运行时调用者的旧 Agent continuation driver 已删除；Agent finalizer 不再从历史 Audit 声明触发退订、切换渠道或伪造执行成功。默认 developer prompt 和 OA 规则种子统一为新角色合同；既有自定义规则、managed Skill 配置与 runtime-only Skill 必须在安静的正式发布窗口中逐项发布并记录版本，部署代码本身不会覆盖它们。

原生 OA detail 明确 success=false 时，来源读取边界保留 errcode/errorCode 及 errmsg/errorMessage，按提供方错误记录原 server/code/认证属性；不把 native 失败 envelope 当成空表单或通用 Codex 错误。Consumer 失败 run 保留原始脱敏说明，System 执行技术失败保留来源和原码，不派发或制造业务人工问题。

Consumer/Audit stage clarification (2026-10-04): `continue_after_execution` means the next stage can run immediately from the verified result. A material-request comment or return ends its request stage with the flag false; only a later source update establishes material arrival and permits a fresh complete candidate. The request receipt cannot replace the material. Audit runner and the frozen model harness share `audit_developer_instructions`, including registered action contracts and exact provider-identity evidence; a project/request ID is not an OA process identifier, and a decision notification does not perform a payment.

角色 MCP 启动参数携带原领取 run 的 execution_generation，启动时与当前任务校验；旧回合不能在 generation 更换后绑定到新一代工件，运行中的既有读写仍逐次检查当前 generation。

真实、未解除的 provider-risk refusal 必须保留 failed 与原始错误码，不能因不重试改为 no_action 完成；Audit 对错误完成候选退回修正。历史同一动作拒绝不允许换键或渠道重放。保留既有历史拒绝与恢复限制，不用关键词推断效果。

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

### 调度异常的重试上限（2026-10-05）

Derek 要求 retry 有上限。ReplyQueueAdapter 的 handler 异常计入既有任务 attempts，
不再退款或立即重新领取；前两次按共享指数退避延后 60/120 秒，第三次原任务记为 failed，
保留原错误、业务对象、执行代和运行历史。ScheduledExecutionQueueAdapter 继承同一规则。
普通主动 release 仍是未执行业务的调度释放，不消费异常预算。Consumer/Audit 的已持久化
技术失败独立受每角色、同执行代、同修订连续六次上限约束，调用前检查，拒绝第七次调用；
底层 code/source_code 保留，预算耗尽不伪造 needs_human 或业务完成。

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

## Runtime Context 与 Settings 渲染输入

后台 Consumer/Audit 在实际 route 和角色工具配置确定后追加动态 Runtime Context；fallback
使用新 route 的模型、thinking、native cwd 和声明目录重新生成。目录来自最终 CLI allowlist 与
role server 描述，仅证明本轮声明的入口，不证明连接/认证/读取成功。资料根目录与任务工件目录
分开显示；Claude 的命令继承进程 cwd，Codex 优先显示明确 `--cd`，未知续会话目录如实标注。
任务、执行代、stage/revision 和后台 scheduled_consumer 绑定来自原任务与真实 invocation，
不按消息 channel、人员或关键词推断。稳定取证原则加入角色指令及契约 hash，动态时间不参与
会话兼容 hash。普通 Consumer 工作、Audit 只读及 SystemExecutor 受控动作边界保持不变。

日历任务先核实相关参与者时区与来源，按会议日期处理夏令时、跨日和不存在/重复的当地时间；
已知工作时段、占用和偏好用于选候选。对方时区未知时保留待确认，先交付本人侧带明确时区的
暂定窗口，不称双方均合适；已知时区也不证明对方空闲。结构化来源字段可投影到环境说明，
文本事实由 Agent 按原材料理解，不把机器时区或钉钉时间戳解析规则当成参与者时区。

每次 invocation 在现有事件流保存 `runtime.prompt`（准备输入）及适配器正常返回后的
`runtime.prompt.invoked`（适配器调用记录）。前者可能出现在启动失败前，后者也不代表模型已
接收或任务成功；两者不是工具进展、外部动作或 provider receipt。记录完整服务 developer/task
输入、实际 route、run attempt、执行代、修订与来源上下文，并复用已有凭据脱敏。

Settings 的 Prompts/运行输入 使用 `/api/console/settings/prompt-preview` 查看上述输入。
当前预览标注「所选路线的配置预览」，使用当前角色规则和已保存的完整任务输入；无完整上下文
的绑定任务显示不可用，不通过只保留原触发的重建冒充完整。历史预览只读既有事件，标明准备/
调用状态、run/attempt/generation/revision/stage/时间；旧 run 缺输入时不重跑、不写回、不补造。
页面范围限定为服务提交输入，CLI 自建系统/schema/history 不伪造为可见内容。预览不改变配置、
队列或动作事实，不读取外部日历和业务资料；历史指定的 role/task/route 不匹配时明确返回 422。

历史输入可按 `runtime_attempt_id` 选择同一 run 的每次路线尝试，逐次显示 prepared/invoked，
后来的准备失败不会隐藏早先已调用的输入。当前预览重新渲染当前默认 Skill 目录，仅复用明确
标记的 task override；任务正文仍标记保存来源。时区事实只投影 participant_id/timezone/
source_ref/applies_on 的字符串字段，其他来源字段不进入环境段；快照复用结构化凭据脱敏。

Prompts 的 Developer/User/Profile 各自保留 Template 与 Rendered preview 的一一对应；预览只渲染
同一份已保存模板，未保存草稿不进入预览。保存成功后重新读取服务器模板及渲染结果，避免继续
显示旧预览。后台 Consumer/Audit 的多段完整输入放在独立只读「运行输入」页签（prompt=runtime），
不再占用 Developer/User 模板的 Rendered preview；该页没有 Template 或保存操作。

Prompts 内容标题随当前查看方式显示「模板」或「渲染结果」，与所选 Developer/User/Profile 页签对应；完整运行输入仍位于独立只读「运行输入」页签。

Developer Prompt 是 Consumer 与 Audit 共用的工作原则，渲染后的冻结正文在两角色核心 Developer 的 Dynamic Skill 之后、System Action Contracts 与 Wire Schema 之前各插入一次；默认正文即原有的中文取证与日历时区原则，不再从代码常量或后续 Shared 段重复注入。User Prompt 是 Consumer 的完整任务模板，必须包含恰好一个 `{{task_context}}`，仅支持普通文本和这个插槽。任务来源、定时要求、stage、反馈、既有回执及 continuation 先由服务组装成完整上下文，再填入插槽；不将旧消息块模板叠加到后台任务。工作人格仍注入两角色，Audit Rules 只进入 Audit。角色、输出 schema、System action contract 与能力职责继续由代码提供。Workbench、独立 WeChat、纯服务命令及 Email 退订保留各自入口和显式指令。

服务组装的 Consumer/Audit Developer Prompt 不再读取或复制本机 `~/.agents/AGENT.md` 开发规则，也不注入缺失文件的占位段或旧的重读提示。业务角色、能力、输出契约和后台 Memory bootstrap 说明仍由原有代码提供；本机原生 CLI 的指令加载与开发 Agent 的 AGENTS.md 文件不在此变更范围。

后台 Consumer/Audit 使用原生 skills.config 禁用专项 Skill 的自动正文注入，避免原生加载、Developer inline 与工具读取三份重复。原生 project_doc_max_bytes=0 只关闭项目目录 AGENTS.md；全局 ~/.codex/AGENTS.md 仍由 CLI 加载，服务不修改原生 home 或全局规则文件。此设置仅适用于后台角色命令。

System Action Contracts 在 Developer 中提供由 docs/system-action-contracts.md 自动生成的 capability/operation 目录；agent_cli.read_system_action_contract(capability, operation) 返回完整规范，包含原有角色、identity、payload、target、回执和完成条件。Consumer 提出及 Audit 审查相应操作前必须读取规范；契约指纹仍包含全文。结果 schema 在 prompt 中只去除 title/default 注释，保留字段名、描述、枚举、约束及原有 Pydantic 校验。原生 output-schema 保持关闭：实际探测显示现有 RootModel 顶层 $ref 被拒绝，等价展开后 Consumer 的 oneOf 与 Audit 可选字段仍不满足原生 strict schema 限制；未通过弱化业务契约来适配。

一次角色 invocation 只读取一次 Developer/User/Profile 正文，供组装、静态指纹与重试共用；Audit 不读取无关 User 模板。实际路线和工具命令确定后，再追加 Runtime Context。静态配置 SHA 写入现有 runtime.prompt invocation facts，仅作来源回执；Consumer 继续按 `conversation_id + route` 复用原 session，配置或 contract hash 变化不创建新会话，Audit 的独立 session 不与 Consumer 合并。

默认 User 的 Rendered preview 使用明确标记的合成完整任务，不读取业务材料；Developer/Profile 渲染同一份已保存正文。完整运行输入仍在独立只读页签，当前配置 Developer 与所选已保存历史 Task 的来源分别标注，历史模式不重新渲染。读取设置/预览不创建或覆盖配置文件。已存在模板不在读取时自动升级；部署使用 `python -m app.deploy --publish-prompt-templates`，仅在既有停止/备份窗口根据 `ci/prompt-template-release.json` 的精确旧/新 SHA 发布默认 Developer/User。自定义模板须明确迁移，不能覆盖；文件备份、发布与回退沿用 RepositoryUpdater 的 publication 协议。该发布不修改工作人格。

仓库受跟踪的 `data/prompts/developer_prompt.md` 是通用 Developer 模板，继续保留 Git 版本管理，
与已批准的 `app/defaults/developer_prompt.md` 保持逐字节一致。发布后的生产文件与回执一致
不等于工作树已干净；源码同步不放宽部署器的本地修改检查，也不允许手工重置生产模板。

Prompts 设置读取先返回 Developer/User 的已保存原文；某份模板验证或渲染失败时，仅该份渲染预览为空并返回明确的 `preview_errors`，编辑器仍显示原文供修正。保存仍需通过现有验证，后台角色调用仍严格验证，不自动迁移或覆盖旧模板。

精简仅去除经固定模型比较确认不会损失质量的重复文本：Consumer 的 Application Result Contract 仍完整保留在 Developer 与 Task；Audit Developer 保留原有两处 Application Result Contract（核心角色边界内一处，Decision Evidence 后一处），Audit Task 开头保留同一合同并原样呈现 `AuditTurnContext.audit_rules`。配置化 Audit Rules 仍由 Audit Developer 携带；生产编排沿用原有空 Task 字段，不把 Developer 规则复制进 Task。先前同时删除多个指令副本的候选出现过错误的日历 rollover；本次只删除 Consumer Task 副本的试验虽然业务判断一致，但续会话首次 JSON 格式失败，因此撤回这项精简。Consumer/Audit 结果合同副本均保持原样，合同条款不删改。Audit 的候选、source_bindings、revision 和 digest 保持完整；只有 provider、object_ref 与来源 value 按排序 JSON 完整相符（保留布尔/数值等 JSON 类型区别）时，Task 中重复的触发正文、raw payload、历史消息正文和材料 reference 指向候选的来源绑定，同一输入内仍能读到完整值。来源不同时两份全文保留。标准 Skill 目录使用 TSV 与路径根别名，保留所有名称、完整用途说明、读取路径和顺序；定时服务命令将专项 Skill 的完整 managed revision 或 operation snapshot 冻结在原任务的 scheduled_consumer.skill_materials 中；Developer 只列选中名称和 agent_cli.read_task_skill(name) 入口，Consumer/Audit 必须先读取适用 Skill。Worker 每轮 Task 的定时要求也携带这份短目录；原生 exec resume 不替换初始 Developer 正文，所以本轮 Skill 选择不能只留在 Developer。这里只重复目录，不重复正文，不重置已有 session。读取返回原冻结正文与 SHA，不随本机 Skill 文件后续修改漂移。旧任务及自定义 inline protocol 保持原样。Email 分类、Meeting Alignment 与独立 WeChat 没有任务内读取工具，仍直接使用完整冻结正文；通用 Scheduled Agent 输入不变，Task Agent 的 prompt 与来源投影继续排除历史 skill_protocol 及 skill_materials，只保留原有定时业务事实。Runtime Context 缩短工具说明，保留原有准确 server.tool 名称和其他环境事实。无业务调用的旧 build_turn_prompt、ceo_agent_thread_prompt 及 CodexRunner 的隐含业务 Developer 默认入口已退休；底层 native 指令保留模式及真实调用者显式指令不变。

Developer 保存先以现有渲染器验证，未知变量或不可渲染内容返回具体错误且不覆盖已保存正文；该检查属于配置格式合同。

默认共同工作原则要求先识别原请求要向谁交付什么、按本轮声明入口取证、交付可核实部分并说明剩余协调责任；日历原则要求核实参与者时区和候选日期的偏移。它们随可编辑 Developer 模板进入两角色，不改变已有角色、审核或执行合同。

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

## Meeting Audience Recovery

独立会议路径复用共享消息受众合同。候选级保密历史拒绝保留为明确未知证据，继续读取其他
候选；不当作空讨论、无群或发送许可。群成员完整性和真实群人数独立于会议参会人数。
Agent 依据实际讨论、完整当前名册、稳定身份、当前职责和内容披露范围决定群或明确私聊。
仅有转写参会人而无完整日历名册时，群发现先按原发送可用性和标题规则预选，再读取保留群的完整成员；无标题匹配时仍保留原候选范围。完整日历名册仍先检查参会覆盖再筛标题，不能让不符合覆盖的同名群遮蔽合格群。该读取顺序优化不降低所选群的身份、完整名册和讨论证据要求，不缓存受众，也不赋予发送许可。
业务私聊只接受来源中唯一的稳定参会人身份或日历证明的组织者；拒绝本人和已核验别名。
个人私聊仍要求完整日历两人名册。正文同一私聊受众合并发送，不凭 HR 职务扩大披露范围。
计划群不可发送时保留失败，不能自动改投组织者或本人。
恢复前精确核对主消息及敏感消息已准备正文与本次披露范围，不仅检查相同受众分支。
旧 HR 合并正文若含仅适合另一受众的敏感部分，在任何发送之前明确失败；不覆盖正文或伪造送达。
会议准备在现有 `service_state` 的 `meeting_delivery_target:v1` 命名空间原子绑定规范化目标与
原动作正文，发送及复用回执前再次校验目标。不同目标不借用原回执；旧 prepared 缺少目标绑定
时不从新决策推断或回填，明确保留未核实失败。无需新增表、列或历史数据 apply。
Provider 明确返回 `success=false` 的主消息或敏感消息记录真实失败，不继续发送后续部分，
不把缺失的送达回执升级为 `sent`。

`rerun_meeting_alignment_jobs` 是原身份重新分析入口，不是送达重试。事务内要求未保存任何
部分效果、未准备主消息或敏感消息、无已有回执、无锁/活跃 dispatcher 或投递归属，且无活跃
会议/运行时回合。已准备或已部分发送必须沿原回执恢复，不能清空重做候选。历史运行不改写。
这些身份和归属检查不证明模型所写业务理由真实，也不代表会议已迁入通用 Consumer/Audit 引擎。

### 历史反馈链接的来源读取（2026-10-07）

Consumer 捕获的历史消息以反馈 token 和 attempt ID 识别上下评分链接是否属于同一对。旧链接中的触发/回复预览各自仍接受敏感值检查，但文字差异不使当前任务失败；Markdown 来源解析保留 URL 原文，以便只在结果检查副本中移除经过检查的链接。两个链接仍分别验证配置的 host/path、query 字段和评分。当前待发送正文保持完整 query 的严格配对，来源原文、候选 digest 和执行前来源比较不受影响。
