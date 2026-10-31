# CEO Agent Service Architecture

> 本文只放现行合同。已退役／历史章节和日期化迁移实施记录原样存于 [archive/architecture-superseded-notes.md](archive/architecture-superseded-notes.md)；新增日期化变更说明写完即并入对应现行小节，不再堆在文件顶部。

Meeting 的业务结果由 `task_results` 中的 `(task_id, result_kind)` 唯一持有。局部结果更新使用一次明确指向该主键的 UPSERT：首次插入使用现有默认值，已有行只更新本次提供的结果字段，不覆盖其他已保存的消息、发送回执或日历结果。专业运行详情与副本验收通过原生 `runs.id` 和 `run_provenance` 读取，不再使用旧 Run 容器的 writer／projection。


## 数据库结构与迁移合同

Attempt HTML／JSON详情只接受当前公开Attempt ID；不再把Actions物理ID转换成公开ID或重定向。History链接沿用公开身份。旧service-bugfix候选的生产／查询入口及DTO已退休，其唯一历史记录保留在原存储中，不形成当前待执行任务。同步outbox与跟进回执的应用读取只保留真实运行调用；单元测试使用测试目录内的当前投影窄查询，不在应用中保留测试专用DAO。 Run 按原生 ID 读取；memory 写入事件保留现行写入与重建，测试按当前投影读取。同步和任务投递的测试造数放在测试支持层，不保留测试专用应用写入口。

当前发布选择器只处理当前 Store／Email 声明、明确的单一上一版本（Store 2026-10-29.2／Email54）迁移和仅 Email 变化的迁移；不匹配的 schema 在任何写入前失败关闭。

### 历史业务读取归一（2026-10-25）

历史详情统一使用 `/tasks/legacy-project/{id}` 及现有 JSON API，HTML 与 JSON 共用
`load_project_task_detail`，History／反馈／跟进链接使用同一明确身份路径。
历史页面只读展示来源、事实、待办、更新、跟进结果和钉钉回执，标明历史状态；
不按旧 active 状态创建任务、不重发待办、不按相同整数 ID 合并正式 Task。
逐字段与退休清单见[历史读取归一](archive/records/business-model-history-read-unification.md)。

Attempt 的最终业务摘要由现有 `audit_summary` 字段持有，它不是 Audit 结论的副本；真实审核仍由 Run 的采用结果／审核事实持有。与摘要完全相同的 `codex_reason` 只保存引用，不重复文本；不同原因（包括空字符串）、直接退订的失败详情继续独立保留。来源三元组与精确关联的已终结 Run 原始编码完全相同时，Attempt 只引用该 Run；无 Run、不同值或不同编码的来源仍由 Attempt 持有。现行存储以四个既有 JSONB 槽位的 NULL 表示上述精确引用，不新增业务字段，也不在运行期读取旧结构。公开写入仍拒绝这些逻辑字段的 NULL；局部更新先还原旧引用值再应用修改，保留未传入的原因／来源。仅被旧测试调用的 `clear_agent_run_session` 已删除，当前会话清理继续作用于会话 owner，不改写历史 Run。

## 当前任务运行机制

钉钉发送核验只从发送结果的 `result` 读取新消息/发送任务身份，不递归扫描引用消息、
历史消息或 nextActions。存在 `result.openTaskId` 时必须查询该任务，即使同时返回了消息 ID；
只有原生查询的 `result.sendStatus=SUCCESS` 才算该异步任务投递成功。没有可查询任务时，
只有服务通过只读会话核验得到的 `dingtalk.reply-readback.v1` 证据可确认投递：同一会话、
完整分页的原会话窗口中唯一精确正文、引用原触发消息、当前账号发送、消息未撤回，且
核验正文摘要匹配。`im.message-list.v1` 解析会保留 `quotedMessage` 身份，供精确引用校验。
服务在比较正文时只移除自己添加的反馈回调尾缀，外部回执仍保留实际发送文本。单独的消息 ID、
processQueryKey、重复 UUID 接受及泛化的 finished 均不是投递证明。unknown、pending、
不完整或存在冲突的读回保持未知；读回找不到消息也不能单独授权重发。
对于严格完整、无失败的群聊窗口，零个精确匹配必须记为投递核验失败，并停止旧候选；不完整窗口或多个匹配继续保持 uncertain。任何后续消息都须生成新候选并经 Audit。
原生回复在投递未定时保存已接受的 provider 回执，冷恢复只重查同一任务，不再次发消息。
Consumer 在 Audit 前为可执行的钉钉聊天消息准备服务尾缀后的完整正文，并按任务、动作、代次和候选版本持久化。准备目标与执行器一致：回复要求会话和原消息 ID；显式群发要求会话；直接发送要求稳定收件人 ID；通用发送可以使用会话 ID。执行器只发送与审核候选正文完全相同的已准备内容。
所有 Consumer 共用不得直接发送对外消息、邮件或执行审批的指令。Audit 的角色规则明确要求逐项核对外发候选的收件人/目标、内容、证据和任务范围；候选文本不能证明 Consumer 已执行外部效果，Audit 不发送或执行任何动作，只有 System Executor 可执行已审核并持久化的精确方案，只有服务原生回执能作为送达证据。Audit 还要遵守来源或任务明确提出的交付范围：只要草稿或由用户自己发送时，退回或拒绝任何拟发送消息、邮件的候选；未限制发送的有效结构化外发提案仍可由 Audit 审核，并仅在审核通过后交由系统执行。这项规则不改变工具权限或执行器。
Audit 审核钉钉消息正文时会另外看到一个便于内容判断的业务正文视图，其中仅移除精确的服务生成末尾署名和经配置校验的反馈回调对。完整未修改候选、收件目标和摘要仍在同一审核上下文中，继续作为字段断言和执行绑定依据；正文其他部分、目标、受众、事实和敏感信息仍全部照常审核。该视图不改候选、不改变审核或发送门禁。
已显式纠正为未知的历史回执仍保留已接受效果，System 只能核对它，不能复用为 verified 或重发。

如果已审核的钉钉发送在准备正文一致性检查处失败，System 不会直接复用原动作键。新执行先用原动作目标、审核正文和原始派发时间，对钉钉做只读完整分页窗口读回；唯一精确命中按真实发送回执恢复，完整窗口零命中记录 `confirmed_no_effect` 后才可重试。单聊按稳定 userId/openDingTalkId 查询，群聊按会话 ID 查询。读回失败、不完整或有多条精确命中时不发送并保留失败，后续只能在新的 Consumer/Audit 候选中重新评估。
历史修正通过显式小批量维护完成，保留原始 provider 结果、之前的核验结论和状态；不改写 Agent
运行或历史 Attempt。当前任务的失败状态优先于历史 completed 展示。未知投递使用技术失败／
uncertain 执行状态，不生成管理者业务决策问题。

`reconcile-failed-reply-chat-readback --task-id ...` is an explicit, one-task
maintenance path for a historical reply whose send is already present in the
sent-reply ledger but whose provider task can no longer be queried. It reads
only that conversation around the UTC `sent_at` instant (SQLite's naive
`CURRENT_TIMESTAMP` is interpreted as UTC) and requires the exact
query range plus complete, untruncated, failure-free native history, one exact
outgoing body quoting the original trigger, and a sender matching the authenticated principal. `--apply` records that evidence
against the same approved candidate, external action and generation in one
CAS transaction, then settles the task. It never sends or comments. Preview is
read-only; incomplete, conflicting or multi-action evidence remains unresolved.

Consumer 与 Audit 的实际只读工具目录提供 `read_dingtalk_group_members` 和
`read_dingtalk_user_profiles`。群成员读取使用 DWS 原生有界自动分页，原样保留真人、
机器人、完整性、桶、续页和失败信息；企业资料读取只接收已核实的组织 userId，
保留原始组织资料字段，不按姓名自动补全，也不假设每份资料都含 title。群 openDingtalkId 不能当作组织
userId；身份关联必须由同一账号返回的稳定 ID 证明，空资料或 partial 仍是证据缺口。
这些读取不新增审核、发送门禁或业务来源绑定，也不改变既有外部动作权限。

本节描述实际进入 AgentOrchestrator 的运行契约。进入 AgentOrchestrator 的业务任务遵循“执行 Agent → 审核 Agent → 反馈/修正 → 再审核”的生命周期；领域任务替换输入和工具能力。WeChat task 3 走同一 Consumer → Audit → SystemExecutor 流程并经现有 Sender 发送，Email 退订和确定性技术命令沿用独立系统直接路径。Consumer 可按业务 Skill 读取事实、准备材料并执行普通工具工作；系统注册的受控外部动作由 System Executor 根据持久化且审核通过的精确方案执行和回读。服务保存候选、审核、选择、执行及回执事实。

Codex Consumer 使用按当前 purpose 选择的原生输出 Schema（general 或 OA）；Codex Audit 使用从现有 Audit wire contract 生成的同一原生 Schema，不再按 OAuth/API 认证方式区分 schema、解码器或恢复指纹。两者的动态 JSON 对象通过键值条目表示，并在原有严格 Pydantic 校验前无损还原。Audit Schema 保留 outcome 与 feedback 的现有条件关系；候选 revision/digest 仍由服务从本轮 AuditTurnContext 绑定，候选字段断言仍执行原校验。字段断言路径不存在时，修正反馈会指出准确路径和该父对象可用的字段名或数组索引，不回显候选字段值；候选绑定、断言校验和审核决定不变。该传输层不改变结果字段、业务判断、SystemExecutor 外部动作流程或 Consumer 直发消息/邮件禁令；Claude 与 Friday 输出路径保持原样。

微信单聊 producer 只把有可读正文的入站文本作为回复候选。每次 Reader 扫描按消息时间把相隔不超过五分钟的候选合成一段；只有该段最后一条消息已安静满五分钟，才以它的原始消息 ID 创建一条回复任务。未结束的段不推进读取水位，因此跨定时扫描到达的后续文字仍可合并；相隔超过五分钟的段分别处理。群聊仍只按精确的当前账号 @ 入队，手动重跑和 Sender 投递不经过这一步重新合并。
独立 Reader helper 仅按 Reader backend 实际读取的 `db_storage/message/message_[0-9]*.db` 分片发现账号，不递归遍历其他数据库和缓存目录。

已发送的微信 Attempt 不能从普通重跑入口再次入队。历史详情仅对当前最新的已发送 Attempt、且原始输入未被精简时提供独立的“再次发送”操作；操作展示原消息内容和 ID，并把该 ID 随请求提交。服务在同一事务中核对来源 Attempt、会话、原消息 ID、最新 Attempt 与之前的再次发送记录，然后创建新执行代际。相同来源在新代际待处理时重复提交复用它，完成后不能再次使用旧 Attempt 发起。精简后缺少可执行原文的历史任务不提供该操作。失败 Attempt 的普通重跑、其他渠道和现有 Sender 投递规则不变。

微信回复任务统一走标准流程（Derek 2026-10-25：不要开关，统一逻辑，不留旧逻辑）：`worker.process_claimed_wechat_task` 用 Reader 读到的近期会话和已存的触发消息构建上下文，Consumer 以 `plan` 提议唯一一个目标固定为触发会话的 `wechat / send_message`，服务在送审前把正文预备成最终正文（含署名与反馈链接），Audit 审核后由 `WechatSendMessageHandler` 经放行判定发送。`plan` 的提案在候选里只保留一份：服务预备最终正文后，Consumer 自带的 `purpose_output.payload` 若仍是它原来的提案，就换成预备后的提案，Audit 不会看到两段不同的正文。离线回放用 `scripts/replay_wechat_standard_pipeline.py`，只读线上、不发送。

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
- `needs_human`：信息完整但这一类任务缺少可复用规则，且质量门槛允许升级；必须有 needs_human_reason、decision_basis 和 2–4 个互斥规则选项，均标记 `applies_to=task_class`。不能让 Derek 决定当前具体事项。当前事实缺失或需要向来源方确认时使用普通、边界明确的 proposal；技术失败不能成为管理决策。执行分支绑定完整方案，停止分支写明 skipped 和原因；未经审核或旧无绑定选项不可执行。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败，并保留失败阶段和原因。

Attempt History 的“需要你处理/选择处理方式”只对应当前可操作的 `needs_human` 规则候选。
当前 `failed` 即使重试耗尽，也继续显示为执行失败，提供重试或技术详情；不得把技术失败、
候选绑定错误或外部执行错误伪装成业务规则决策，也不得提供“人工处理/暂不处理”作为替代决策。
质量扫描只将关联当前 execution generation 且 Task 仍为 `needs_human` 的 Attempt 计入当前 Attention
或质量违规。Task 已为 `done`、`skipped` 或 `failed` 时，旧 Attempt 留在 History，不再作为当前人工决策。

如果 DingTalk trigger 引用了本服务通过 provider 回执确认已发送的 Agent 消息，Consumer context 会携带结构化来源标志；此类 Agent 发起的续接/恢复任务不得进入 `needs_human`。该标志以 conversation、quoted message ID 与成功发送状态回读匹配，不靠正文签名猜测；Consumer 生成校验与 Audit 独立复核都会拒绝该升级，要求 Agent 自主判断、补充有边界的 proposal 或向事实来源方 ask-back。

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

审核 Agent 只能反馈规则、观察结果和具体修改要求，不能直接改写执行 Agent 的业务正文。执行 Agent 必须基于反馈生成新 revision；原 run 不覆盖、不删除。一个任务最多允许十个内容反馈周期（Audit 的 return/reject，2026-10-26 Derek 由三次放宽；被打回是正常业务流程，不是重试），基础设施和技术失败不消耗反馈周期，技术重试另有各自的预算。反馈次数耗尽本身是自动闭环失败，不是人工决策依据。Consumer 和 Audit 必须按固定顺序对新候选应用质量门槛：`information_completeness < 0.5` 时先 ask_back；否则仅 `(risk=high 且 confidence < 0.5) 或 rule_coverage < 0.5` 才可进入 `needs_human`；其他情况按适用规则自主处理。Consumer 的新结果类型化校验会拒绝不满足门槛的 `needs_human`；Audit 在审核候选前及校验其绑定结果时独立重算同一门槛，不符合时拒绝该新候选。已持久化候选在恢复、拒绝分支比较、已审核动作执行及历史读回时按历史证据解析，不重新套用新准入门槛；这保留原始 run 与已审核决定，且不会重新执行已确认动作。历史解析不能让新模型输出绕过 Consumer/Audit 校验。Audit 必须核对新结果分类与四项分值一致；不满足门槛的人工升级不得通过。

消息发送回执仍为 `uncertain` 时，后续 Audit 批准的 Consumer `no_action` 不得关闭任务；编排器继续以原不确定执行为依据向 Consumer 提供回执核对反馈：只给目标和结果映射（先用 dws 弄清消息有没有真的发出去，没弄清不重发；已发出或看不清就 `no_action` 并说明，确认没发出且来源请求仍有效才重新提案；怎么查由 Consumer 自己决定，不规定顺序）。若核对满三轮（`MAX_UNCERTAIN_DELIVERY_REVISIONS`）后仍未形成可审计的新提案，任务以 `external_action_uncertain` 失败并保留 Attention；来源审批已终止时仍由精确 OA 终态核对路径将任务置为 `skipped`，不误报送达或要求人工处理。完整负向回读只证明原消息未送达，任何新消息仍须形成新候选并经过 Audit。

修订后的 Audit 输入从当前 Consumer 的持久父链读取促成该修订的已采用 return/reject，传入原 proposal_revision、candidate_digest 和完整 feedback。该段在当前事实和候选之前，明确只是修订背景，不是事实来源或业务授权；不复制旧 summary 或分值。Audit 对照上次要求复核当前候选，先前要求本身有误时应说明依据并纠正，遵从反馈不能证明事实成立。初轮和已批准执行后的新阶段不带该段；进程恢复及技术重试从同一采用记录恢复，不依赖原生轨迹可用性。反馈周期上限、候选绑定、状态和外部动作权限保持原样。

恢复已完成的 Audit runtime 结果时仍执行相同的敏感值校验；校验失败记为结果校验失败，不记为 Codex/provider 进程故障。拒绝规则不放宽，失败结果不会因此被采用或执行。

所有任务都禁止使用 `discard` 动作或写入 `discarded` 状态。无需动作的结果在 trace 记录 `no_action` 后进入 `done`；需要修正时由审核 Agent 写入 `audit_feedback`，执行 Agent 生成新 revision；处理失败使用 `failed`；无法自动解决使用 `needs_human`。

`okr_review` 使用上述闭环生成逐 KR 评审；管理者 OKR 周报评分范围是当前 OKR 周期起始日至报告日，本周窗口只标记新增进展和风险。每位名单成员都必须保留一个区块，但只有可用的实时目标和有效分析才产生评分；个人季度或目标确证缺失、已验证来源中的成员数据故障以及终态结果校验失败使用系统绑定成员 ID 的未评分缺口。不得以零分、上季度目标或省略成员填补缺口。共享认证、未配置来源、未分类来源故障或身份/权限证明无效仍阻断发布，不按失败人数推断共享故障；明确的成员级失败即使覆盖全名单，也只能形成如实的缺口报告。原始回执和诊断保留在本地来源材料及任务记录中，不把历史季度指标、凭据、路径或 provider 正文放入管理报告。

名单覆盖以稳定 user ID 关联评分、来源和缺口；同名不同成员仍保留独立区块及附录，现有唯一姓名附录的文档身份不变。有效评分保留原 KR、四项领导力和三项文化维度校验及计算公式。OKR 分析使用普通 scheduled Agent 生命周期和 task generation 产物，来源变更后重新分析；模型调用沿用统一应急上限与 idle watchdog。评分、文档及群摘要回执是不同完成级别；完整名单缺口报告已发布不等于所有成员已完成评分。调度、重试、租约与恢复由普通任务机制负责。

OKR 评审的实时数据读取由业务 Skill 选择当前可用的 provider 能力完成。服务提供的
`app.cli read-dingteam-okr --user-id <owner-id> --period-label <period>`，该入口调用
`CEO_OKR_LIVE_SOURCE_COMMAND`（应指向服务 checkout 中的 `scripts/dingteam_okr_headless_source.py`；
旧 Skill 浏览器入口不会把完整个人季度缺席转成成员级收据），并返回包含
`processed.objectives`、`processed.okrRows` 的实时载荷，或经完整个人周期列表验证的
`availability.status=goals_not_established` 收据；它是一种可用实现而非应用层命令契约。
Codex 路线将服务进程配置的该命令传给 `agent_cli` 子进程，避免子进程因环境筛选回读旧 `.env` 入口。

即时叮当 OKR 评分与定时周报分析是两种任务。绑定 `dingtang-okr-review` 的 Consumer 可用
`read_okr_member` 和 `read_dingtang_okr_score_detail` 取得实时目标、当前评分批次、
KR 与正式评价人 ID；评分动作只以 `dingtang-okr/score_kr` 的精确目标、0–1 分数和评语
提交候选，不得直接调用写入脚本或接口。Audit 审核后 SystemExecutor 才在服务的无头 Chrome 页面内注入 JS 调用
页面原生 `makeScore`。该接口由登录用户决定评分归属，提交体没有评价人或批次字段；
执行前须核对登录用户就是候选评价人、周期名称与唯一当前批次相符、该周期使用 KR 评分，
且评分详情中的周期、目标、KR 与评价人均对应候选。执行后重新读取
`getScoreDetail`，只有同一评价人的分数、原评语和 `complete` 状态全部相符才记为已完成。
评分页面的 `getScoreDetail` 与 `makeScore` 请求沿用页面认证头，但把该接口所需的
`X-Space-Id` 置空；周期、批次等其他读取仍保持页面原请求。只读评分详情若收到
提供方对象错误，服务只传出数值 code 和消息，经既有脱敏后报告失败，不传 traceId。
页面目标读取成功只证明目标可评分，不代表已提交或完成评分。
写入结果不明时只做读取核对，不对已发出的评分自动重发；Consumer/Audit 不直接写分。

逐 KR 评审的 `StructuredCodexRunner` 通过专用 `app/schemas/okr_review.schema.json`
向原生 CLI 声明输出结构。该快照保留通用 AgentEnvelope 的外围契约，并将
`domain_payload` 固定为现有 `OkrReviewPayload`、`OkrReviewItem`、`OkrEvidence`
模型的完整字段；所有 object 均声明具体 properties、全部 required 字段及
`additionalProperties: false`。通用 envelope 的解析和 OKR 领域结果处理保持原有契约。

Consumer 形成通过/不通过判断时应使用当前 OKR 数据；截图、仓库链接或重试终态不能替代实时读取，
读取失败时必须保留底层认证、浏览器启动或源端错误码。Dingteam OKR 只有在检查缓存 token 未过期后才复用；过期缓存进入既有串行 Chrome 刷新流程，通过 `app.service_browser.launch_service_chrome` 和每日 Chrome cookie 副本启动真 Chrome，并沿用现有本机 DingTalk SSO。仅验证有效的 token 才返回或写入缓存；登录仍失败时保留真实认证失败，不绕过认证。不得另起 bundled Chromium 或专用 CDP 资料目录。
本机 SSO 在登录页存在“扫码登录”标签时优先使用它、已登录账号头像和原生确认流程，再选择目标组织；只有扫码入口不存在时才尝试直接账号入口，因为页面上可见的“立即登录”也可能被遮罩挡住。组织选择器可能继续使用登录 URL；若登录过程已直接跳回 OKR 入口则按页面导航完成处理。选择器缺失或页面未进入组织/OKR 页面时，保留浏览器的实际错误，不吞掉异常并伪报通用会话过期。
多个评审或维护任务同时遇到缓存过期时，headless source 只允许一个调用刷新认证；其他调用在取得刷新锁后重新读取缓存并复用结果，不能因正常刷新耗时产生并发锁错误。
无头来源的锁最多等待 130 秒；独立 worker 的 220 秒运行上限必须小于 DWS 调用方的 240 秒上限，并为终止和回收 Chromium 进程组留出时间。不能让 worker 上限早于锁等待上限，否则正常并发刷新会被误报为来源超时。
headless source 还会在调用 OKR API 前校验新捕获凭据的有效期；专用浏览器会话过期必须明确报告会话需要重新登录，不能误投影为“没有该 OKR 周期”。

完整状态和恢复说明见 [`docs/runtime-mechanism.md`](runtime-mechanism.md)。
错误码解释统一见 [`docs/error-catalog.md`](error-catalog.md)。

### Agent Cron 与统一 Dispatcher

每个定时任务有独立的「发送完成通知」偏好，默认开启。偏好保存在任务配置中，并在创建手动或 Cron run 时冻结进该 run snapshot；后续编辑只影响之后创建的 run。关闭时只抑制该 run 的终态桌面通知，不改变任务/run 状态、Attention 或 History。没有该字段的历史 snapshot 按开启读取。

用户可见的定时任务是 `任务描述 + Cron + Agent 能力（结构化 Skill 引用）+ Runtime`。任务描述是所有
定时任务共有的可读用途说明；Agent 任务另有执行提示词，服务命令不把描述伪装成 Agent prompt。Scheduler 维护
下一个触发点，并把一次触发保存为 `scheduled_task_run`；它不执行领域业务，
也不把 Consumer/Audit 的完成或失败复制回调度记录。若 SQLite 写入因 `BUSY`/`LOCKED` 冲突在
存储层有限重试后仍失败，Scheduler 保留该触发点，等待 1 秒再重试；该数据库错误不会被记录为
运行结果，也不会让 Service worker 退出。Dispatcher 先领取该 trigger，原子创建
唯一的 `channel=scheduled` execution source，并在 trigger 上保存
`execution_kind + execution_id`。之后 Scheduled Agent Consumer 从这条不可变输入进入标准
Consumer → Audit → feedback revision 生命周期：

若 Scheduler 因首选 Runtime 暂时不可达而无法分派已到期触发，它保留触发计划，不创建终态
`skipped` 运行；路由恢复后以原计划时间派发一次。多次错过时仍合并为最新的错过时间点。静态
配置错误仍按原规则写入 `skipped` 和 Attention；已有执行未终态时也保留补跑计划，待前次收口后派发。

```text
scheduled_tasks
  -> scheduled_task_runs (trigger fact)
  -> Dispatcher: scheduled adapter
  -> reply_tasks[channel=scheduled] (exact active input; settled source provenance)
  -> Dispatcher: scheduled_execution adapter
  -> Consumer Agent -> Audit Agent -> feedback/revision
  -> reply_attempt / agent_runs / provider facts
```

Scheduled Agent Audit also receives compact service projections of completed
`consumer_document_write` returns from completed Consumer runs in the exact
scheduled task execution generation, including earlier revisions after Audit
feedback. Each projection contains the returned document identity and a digest
and byte count of the returned provider write result; it omits the report body itself.
An empty list means no matching return was captured in that generation. This
establishes what the service tool returned, not whether the document or its
claims are correct. The behavior is shared by scheduled Agent tasks and does
not change Audit's review standard.

定时任务有两种执行形式，由 `scheduled_tasks.command` 区分。`command` 为空的是 Agent 任务，
走上面的完整路径。`command` 非空的是服务命令任务：它只声明服务命令目录中的一个名字，不需要
Runtime、Skill 或工作目录。判断标准是这次执行本身有没有判断空间：确定性的发现或同步工作属于
服务命令，需要 Skill 判断的才是 Agent 任务。AI 听记同步的分页读取、归档和内容游标都由
`app/minutes_sync.py` 确定性完成，因此它也是服务命令。
摘要接口对没有有效转写文本的听记会返回终态 `textGenerationInfo=SKIPPED/NO_VALID_TEXT` 和上游跳过的视觉摘要；同步器把它作为无摘要处理，仍归档可用逐字稿。其他未知摘要形状继续按单项失败处理，不把状态信息写进摘要正文。
“工作来源”扫描还会只读发现管理周报、项目管理部周报和部门产研周报，按文档 ID、链接、统计周期和内容 digest
建立 Task-first `work_summary` 输入；同一文档版本重复扫描保持幂等，报告内容仍由 Task Agent 提取 Project 和 Task。
Scheduled Agent 执行按 trigger 冻结托管 Skill 的精确 revision 和正文。重试继续使用该冻结版本；即使该 revision 不在当前进程启动时加载的 Skill snapshot 中，运行时仍按精确 Skill ID、revision ID 和名称从不可变 revision store 读取它。缺失、跨 Skill 或显式禁用的 revision 仍拒绝执行；系统不替换为最新版。
这些输入的逻辑 `payload_json` 对较大内容使用带版本标记的 zlib BLOB，小内容仍用 JSON 文本；最终 Tasks 向量按原 SQLite 值类型编码该字段，应用模型和离线导入/修复入口统一还原为原始 JSON 文本。Attention 的 JSON 字段投影在该查询连接上使用同一解码器；副本回放比较准确解码正文后，CAS 绑定当前投影返回的精确 TEXT/BLOB 值，通过当前 Tasks 写入器沿用原编码器。迁移命令 `python -m app.work_summary_payload --db <database>` 按小批事务压缩仍需保留正文的非终态行；`done` 和 `skipped` 行继续使用终态精简，命令可重复运行、不删除记录且不执行 VACUUM，必须在维护窗口及已验证备份后单独执行。
DingTalk 消息增量扫描与近期恢复扫描共享 producer 锁以避免并发读取；若恢复扫描仍占锁，分钟级消息或日历扫描立即以 `deferred=producer_busy` 返回，并由下一次定时触发补扫，不排队等待长恢复任务结束。恢复扫描仍等待已开始的短增量扫描完成后再运行，队列按原消息身份保持幂等。
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

定时任务只有在服务命令或 Agent execution 已持久化为成功（`dispatched`/`done`）或失败
（`failed`）时，才发送桌面通知，标题含任务名称，正文含结果摘要或错误。排队、等待重试、跳过和仍在执行时不通知；通知不改变运行状态或 Attention 规则。终态保存后，通知以尽力投递处理；本机没有可用通知程序时记录错误并保留任务的真实终态。

OA 的钉钉系统通知和聊天催办是外部观察事件，不是第二条审批执行入口。消息生产器将
`OA审批` 系统通知以及 `[Ding]…提醒您审批` 聊天消息写入 `oa_notification_events`；前者只
留痕，后者保存原会话和原消息作为结果回传目标，二者都不创建 Agent run。OA 定时扫描读取
`processInstanceId` 的实时节点：只有当前用户恰好有一个运行中 `taskId` 时，才把无
`taskId` 事件归并到该 OA task；多个候选或无法解析时不猜测。审批 task 完成并收到 provider
回执后，服务使用独立的 `oa-reminder-result:{event_id}:{attempt_id}` 投递键通过原生引用回复
催办人。结果回复有自己的事件状态和回执，不改变 OA 审批 Attempt 的业务结论，也不会重新
启动一个聊天 Agent。

服务启动或主机从睡眠恢复后，调度层补最近的一个已错过触发；更早的错过点合并到该次执行，
避免恢复时突发排出多份历史报告。任务创建时间之前的触发不会补造，已有触发继续由唯一键去重。
若补跑时上一轮仍未终态，Scheduler 保留该补跑时间点，待上一轮终态后再提交；正常到期的
触发仍按 `skipped` 收口，不会并行创建第二个执行输入。手动运行会创建独立 trigger，但不移动
正常计划。CEO 日报默认按 `America/Los_Angeles` 每天 06:00 触发，OKR 管理周报默认按
`America/Los_Angeles` 每周日 03:00 触发，避免夏令时切换后因固定北京时间而偏移一小时。
首次迁移只改仍匹配旧仓库默认周期与时区的任务；已自定义周期或时区的任务不变，历史运行继续引用原配置快照。
任务指定的 Runtime route 是
**首选**而不是唯一线路（Derek 2026-09-24）：执行先上首选线路，失败时走与其他 Agent 轮次相同的统一
fallback（`app/runtime_fallback.py`：满载先同线路重试，再暂停该线路、按配置顺序换下一条）；任务的
model、thinking 只作用于首选线路。managed Skill 必须绑定精确、已加载且启用的 revision。派发前按
「首选 + 其余配置线路」检查，只有这些线路都不可用时 trigger 才为 `skipped`；Runtime 未配置、缺少能力、
认证暂停或 Skill revision 不可用属于配置性不可用，每次进入 Attention；provider 暂时不可用导致
的路由暂停（过载、传输断连）只体现为 run 记录和路由暂停状态，不按每次触发写 Attention。
Codex provider 错误事件报告的最终 HTTP 429 按已有满载路线处理：先有界地同路线重试；
失败 attempt 已观察到 session 时，使用原生 `codex exec resume` 续接并发送 `continue`，
包括冷启动首轮已创建 session 后才返回 429 的情况。未观察到 session 时仍提交原任务输入；
工具结果正文出现 429 不触发此分类。
派发后的执行可用性失败记录在 execution source，trigger 仍只表示已经派发。

统一 Consumer Dispatcher 不建立第二套业务队列表。各 Queue Adapter 直接领取现有事实来源：
scheduled trigger、scheduled execution、普通 reply、meeting、work summary、OKR review、
当前 BusinessTask 的 DingTalk Todo outbox。统一层只处理唤醒、公平领取、租约、全局 Agent 容量和分发；领域 Consumer
继续负责自己的生命周期和外部事实。Dispatcher 的有界等待是跨进程恢复机制，不是用户 Cron，
也没有用户可编辑的 polling/settle 设置。空队列只显示零指标，不生成 run。

启动时以稳定 migration key 幂等创建默认任务，清单以 `app/agent_cron/seeds.py::seed_scheduled_tasks`
为准（当前 14 项）。
听记权限命令的登录页加载失败、组织选择或控制台跳转超时都会前置打开用户可见的 Chrome；组织选择和控制台跳转各限 10 秒，原运行保持失败，直至后续运行读回实际状态。
`scan-meetings-once` 服务命令只发现符合条件的已结束会议，并报告新建的会议任务数。
默认定义保留服务命令与普通 Agent 两种执行形式；以稳定 migration key 原地升级默认配置，
保留名称、Cron、时区和用户修改。已删除的旧任务不动，服务命令通过 Console API 不可修改。新安装创建的全部
默认任务都是**暂停**状态，由用户配好连接器后自行启用（Derek 2026-09-23）；seed 从不改变已有任务的
启用状态，包括从未被编辑过的 version 1 任务。

11 项发现/同步任务保留服务命令，CEO 日报、CEO 管理周报和 OKR 管理周报是三项普通 Agent 任务。业务要求由任务 prompt 与 Skill 决定，系统层不按报告名称或 Skill 名称分支，也不以 `publishable` 判断业务质量。通用 `read_scheduled_context` 返回触发时刻、时区、版本及历史完成事实；Consumer 还会收到当前 task generation 中已有产物的文件名和 SHA-256 清单，不包含文件正文，以便重试先复用当前执行已有材料。 OKR Consumer 同一轮必须继续完成所有可分析成员，不得主动以部分分析结束；只有实际 turn 中断才保留有效产物并返回可重试的不完整结果。`read_service_activity` 按调用者明确给出的时间窗口和来源返回服务事实；定时任务的历史活动与群消息读取不接受晚于本次触发 `scheduled_for` 的结束时间，避免把触发后发生的事实计入本次运行。非定时任务及面向未来的日历读取不受此边界影响。`read_meeting_archive` 按会议身份读取归档。Codex 与 Claude 均可通过 Consumer 的 `consumer_run_skill_script` 执行所选 Skill 的确定性 Python 脚本。脚本在当前 task generation 的产物目录运行；产物读回分页，完整正文留在本地产物中。日报按北京时间报告日期取数：主窗口从该日 00:00 到本次实际 `scheduled_for`，不得越过触发时刻；漏掉的报告日期作为独立 catch-up 覆盖呈现，不并入当天主窗口。

产出报告的定时任务（CEO 管理周报、CEO 每日总结、每周 OKR 管理周报；声明表 `ScheduledTaskDefault.produces_report`）有一条独立的 Attention 规则（Derek 2026-10-25，纯展示，不改任何任务、运行记录或日程）：最近一次计划运行（不含「上一次仍在执行」的跳过）没有产出报告就显示一行「Scheduled task」，直到之后有一次运行（计划或手动）以 `done` 结束。「没有产出」指三种情况：运行因运行线路、Skill 不可用等原因被跳过因此没有任务；派发本身失败；任务以 `skipped` 结束。任务 `failed` 已有自己的 Reply task 行，不重复显示；进行中的不显示；暂停的日程不显示。实现在 `app/report_schedule_attention.py`，由 Attention 列表汇总调用。

产物 offset 是从0起的字符索引；每页统一返回 offset、total_chars、next_offset，sha256 始终对应整份文件。完整读取从0开始，按 next_offset 续读至 null，不能把非零起点的尾部当作全文。

`consumer_document_write` 接受正文产物、标题和显式目标；更新使用 node_id，创建使用 parent_id 与 workspace_id。JSONML 由原生解析器检查，更新保留 revision 校验与版本保存。创建前后都会读取父目录完整清单（DWS `doc.list.v1` 的 `nodes`）；清单不完整、有失败或存在分页时停止，创建前拒绝同名目标，创建后要求目标名称与文档身份唯一。通过 DWS 原生 `doc create/update` 提交，返回 `ok=true/status=success`、provider 写入回执、文档身份和提交正文 SHA；不自动读回正文、不生成 readback 产物，也不进行内容或格式比对。消息继续通过既有已审核投递通道发送，普通文档工作与受控消息动作沿用原角色契约。
文档写入成功由原生 provider 写入结果确定，未执行或提交状态未知仍抛出原错误，不自动重写。创建后的目标解析失败保留已成功写入的回执、已知 node 和解析错误；不将目标解析失败改写为写入失败。输入 JSONML 解析、revision 冲突检查与版本保存保留，自动全文读回和内容核验已按 Derek 指示移除。

日报窗口由 `ceo-daily-report/scripts/window.py` 按上次真实完成的历史事实计算，漏发并入下一份，同日重跑沿用原起点。日报位置与通知内容归该 Skill。Consumer 先读取服务事实和群消息；仅当会议管理细节仍缺失时，才读取本任务冻结的 `dingtalk-minutes` Skill 并查询听记摘要。管理周报的周一窗口、目标/上期文档、空白模板策略和三处内容更新归 `ceo-weekly-report` 的材料/渲染脚本；渲染器只检查实际读取字段与 JSONML，固定指标、材料覆盖与章节等业务要求由 Skill 和 Audit 判断。三处写入仍为 CEO 判断、公司指标和新增跟踪行。目标周会文档若是只有议程的新模板、没有「重点问题及待办跟踪」「CEO本周判断」「公司级重点指标」三个编号区块，渲染器在文档顶部按这个顺序补上缺的区块（跟踪表用标准表头），原有内容全部保留在其后；已有的区块不动，重复的仍然拒绝渲染。管理周报缺 CRM、财务、遥测或业务线材料时写「无数据」并披露覆盖缺口，照常发布；只有已声明的读取或文档写入本身失败才报 `failed`，不能因证据不全报 `operation_unavailable`。CRM 实际用 Consumer shell 里的 `sharecrm` 只读命令读取，其钥匙串登录需按 Consumer 权限模型请求提权；财务暂无可读来源。

周报无数据不是失败（Derek 2026-10-26）：缺数据的指标显示为「无数据」，缺负责人显示为「待确认负责人」，任务照常完成，不失败、不转 `needs_human`、不进 Attention。渲染脚本 `validate_run.py` 只检查渲染器放不下的结构（类型、行的身份字段 `id`/`question`/`name`/`text`），不再因缺值、空值、缺负责人、缺业务线或「待补」负责人而拒绝渲染；`report_contract.py` 的辅助检查也不再把缺业务线周报或决策关键的无数据指标当错误。服务里没有任何代码因「无数据」让任务失败。决策质量门槛的 `information_completeness` 对周报是「可读来源中已读的比例」，Skill 明写无数据指标不拉低它；向负责人要数是正常步骤，不是门槛。通用规则放在所有 Consumer/Audit 回合都会注入的默认开发者提示 `app/defaults/developer_prompt.md`（Derek 2026-10-26）：数据源、指标或工具读不到、返回为空时，输出里写「无数据」或「未核实」和试过什么，任务照常完成；缺数据或缺工具本身不是失败、`needs_human` 或阻塞，只有真实违反契约、动作不安全、或缺的东西就是任务要求的效果本身才算失败。周报 Skill 只指向这条通用规则并保留周报特有的部分。提示模板用 `python -m app.deploy --publish-prompt-templates` 单独发布（与 Skill 契约发布分开部署）。

每日总结的群聊覆盖不全同样是披露的缺口，不是不交付的理由（Derek 2026-10-26）：`ceo-daily-report` Skill 要求尝试每个可访问群聊，没读、读失败、超时或读到不完整的会话，在「覆盖说明」里写明会话、窗口和原样错误或 `not attempted`，报告和通知提案照常写出并送审，Audit 核对披露是否准确而不是因覆盖不全退回。

OKR 报告的应用层只读取明确群名的稳定名单和明确人员/周期的实时数据及来源回执。每人一次读取、单次最多 240 秒，模型可见真实步骤结果；纯本地 `ceo-weekly-okr-report/scripts/report.py` 按稳定 userId 准备输入、计算原有精确公式并渲染。Agent 保存成员产物以恢复进度，发布管理报告、成员子页并提出群摘要消息。Skill 的 prepare 在计划中标记来源摘要匹配且已完成分析的 `analysis_reusable`；`analysis_failure` 占位不算完成且不会复用。恢复的 Consumer turn 先读取最新 Audit 与当前代际任务产物，再复用同一 scheduled run 中仍存在的名单和成员来源回执；当前没有独立的来源摘要查询接口，因此需要刷新时读取成员来源才能发现来源是否变化。只有文件中仍有有效回执的成员才视为已成功读取；回执丢失或此前明确失败的成员按 missing/failed receipt 补读一次，保存后再 prepare。prepare 按新摘要保留未变化成员的完整分析，只重做来源已变或分析未完成的成员；不会重复读取已有成功回执，也不会因已丢失回执曾经读取成功就永久阻止恢复。Consumer 必须在同一轮继续处理所有可分析成员，不得主动以部分分析结束；只有实际的 turn 中断才保留有效产物并以可重试结果继续，使用正常有界重试。零进度不是结果证据；每个 KR 都按原规则检索独立证据并评分，未找到佐证时说明证据缺口，不能用 `analysis_failure` 代替未完成分析。领导力和文化的每项分数须由该维度自身的行为证据支持，不能跨维度借用；创新须有具体改进、验证和观察效果，AI能力须有具体AI使用和工作效果。Audit 逐人核对全部七项维度、证据与分数。未评分成员保留已知职级与逐人来源缺口，不生成分数。业务 KR 的完成档位与领导力/文化行为档位分别应用；行为评分使用正式维度，70–79 为符合标准，80 分及以上须有明确超出当前职级标准的具体效果（经理日常辅导、总监在团队内复用方法本身仍可属于本职标准），90 分及以上须具备对应榜样范围证据；显示分数保留一位小数并采用通常四舍五入。每次渲染后必须按当前 manifest 写入或更新并完整读回全部主文档和附录，即使某成员分析未变，日期、标题、链接仍可能变化；旧标题或旧读回不能满足本次 manifest。最终群提案逐字使用 summary.md。

OKR 报告的进展标题使用“已核实进展”并以报告日为截止，不能将没有来源日期的期间结果标作“本周”新增进展；管理者表格列同样只陈述进展与证据。

发布清单的 `old_sha256` 必须对应当前已安装文件；若文件已经与候选版本一致，可将 old/new 摘要都记为该版本，以便校验与精确回滚继续成立，不提供宽松的旧版本匹配。失败回滚按精确版本恢复任务配置、runtime config 和既有文件，并移除本次新建文件；任务 ID、历史触发快照、周期、时区和用户自定义文本保留。

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
本地 main 祖先 commit；适用路径还需要各自要求的场景和健康证据。服务级队列积压由单独的队列
投影报告，不作为单个反馈项目的结案条件。

普通会话里提出“修改分身规则、Skill 或服务行为”并不自动成为 `feedback_iteration` 队列项。
只有输入上下文明确携带 `feedback_key` / `batch_id` 时，才要求反馈处理轮次记录；普通消息直接按
适用 Skill 形成规则修改候选。找不到反馈队列记录不能作为普通消息处理失败的理由。

OA 每个审批节点只校验当前表单真实存在且明确必填的信息。后续业务阶段、其他表单或评审偏好
中的字段不能反向成为当前节点的强制条件；Audit 发现这种候选必须退回 Consumer 重新生成。

OA 定时任务冻结传入通用 `dingtalk-oa-approval` 与 Stardust 财务、立项、合同、人员、
考勤/出差、云资源六个业务大类 Skill。个人身份、工作区和其他部署参数由运行时注入 Prompt，不写入 Skill。审批 Agent 只按通用
审批 Skill 和适用的 Stardust 业务 Skill 判断；只有未被适用 Skill 明确引用的
背景材料才可跳过。Skill 明确引用的规则卡、登记表等规范材料是该 Skill 的规则来源，
审阅时必须读取。Consumer 按 live `processCode` 和表单事实选择适用类别；跨类别事项组合适用 Skill。
财务 Skill 的规则卡只适用于登记的财务模板，不匹配其他类别不得单独触发升级；规则卡只提供判断标准，动作与其他类别一样按通用决策表（2026-09-24）。适用业务 Skill
必须覆盖当前事项的规则条件、例外、权限和动作映射，且内容有效，
`rule_coverage` 才能为 1.0；部分覆盖本身不等于人工升级，只有低于 0.5 才满足规则覆盖分支，并且必须确认缺失的是适用决策规则，而非事实或执行能力。申请人可以补足事实或材料；若政策判断仍无法解决，先审核并执行必要的材料请求阶段，再提交满足统一门槛的人工问题。个人审批偏好仍来自定时任务 Prompt。

供应商付款证据台账只记录与实质付款条件相关且由当前材料核实的事实；OCR 可读不代表字段与付款判断相关。复核旧退回或拒绝时，只报告有独立证据支持的实质缺口轮数；未计入轮次的历史原因不复述，也不以泛称间接指代。供应商付款不评估或请求签章相关材料，也不在证据叙述或决策理由中复述相关栏位或历史要求；双方对价格、范围和结算条件的确认只根据协议、订单、报价、往来内容等实质证据判断。对已结束流程的历史复核只作分析，结果为 `no_action`，不得提出新的 OA 评论或审批动作；新提交的流程必须按自己的当前任务和证据单独审核。`rule_coverage` 只由本案触及的分支决定；未核实的适用事实计入 `information_completeness`，不降低规则覆盖度。供应商付款也适用通用 OA Skill 第13条的三次有效退回规则，但必须逐轮证明确是同一项实质、可补正且仍未解决的缺口。每次退回都须以可读、来源核验的证据独立确认同一项实质且可由申请人补正的缺口；审阅端权限、解析、OCR 或客户端读取失败、误述状态以及合同没有要求的形式要求，不计作申请人未解决的问题。

供应商付款 Skill 进一步明确：审阅者不评估签字、印章、签章栏是否存在或可读，也不在内部/对外结果提及这些观察；付款判断只看双方实质合意、交付/验收、结算金额、发票、收款方及记录一致性。此排除仅适用于 `supplier_payment`，其他财务模板仍按各自来源规则审查。

历史退回次数按实际操作和当时证据核实：先读完整 DWS 审批详情的 `operationRecords[].remark`；简化流水若只有动作和时间，必须补读完整详情。`REDIRECT_PROCESS` 只证明发生了退回动作，不能单独证明退回理由或该轮有效。被跳过、未执行或未获钉钉成功回执的 Consumer 候选是草稿；若找不到当时真实评论/备注或其他可核验来源，标记该轮有效性未知，不计入三次有效退回。

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
  正文和 provenance 只保存在 `reply_task_inputs`；`current_input_id` 外键指向当前输入，
  每次队列领取保存 `claimed_input_id`，当次执行和冻结 Skill 按代际校验读取领取快照。
  延后或恢复后的再次领取沿用原有版本水位语义，捕获当时的当前输入。
  `input_version`／`claimed_input_version` 继续作为原有并发版本水位，不充当输入 ID。
  WeChat 的 `read_wechat_messages` 不接收会话选择参数，只读取已领取 TaskInput 中绑定的会话，
  因此执行期间新输入成为 current 时仍使用本轮 claimed 会话及其 generation。
  已结算输入不另存从 provenance 可生成的精简 JSON；API 按来源摘要投影原有展示形式。
  微信语音（Derek 2026-10-26）：语音消息的 `kind` 是 `voice`，`text` 是占位 `[语音N秒·未转写，用 save_message_audio 取音频再转写]`，因此语音不是空消息，直接聊天里每条入站语音和入站文字一样成为回复触发（群聊仍只认 @）。
  音频不在消息行里：Reader 的 `read_voice` 按 `message_id` 在该会话自己的表里确认它是语音，再按 `server_id` 到 `media_N.db` 的 `VoiceInfo`（最新的分片先查）取 SILK 字节，最大 2 MiB；找不到、过大、不是语音都返回 `voice_unavailable`。
  通用工具 `save_message_audio(message_id, resource_id)`（Consumer 与 Audit，与 `read_wechat_messages` 同一份读取工具清单、不接收会话参数，按任务绑定的渠道取音频）把语音存成本任务当前执行代的任务文件并返回文件名：微信在服务环境用 `pilk` 把 SILK 转成 WAV；钉钉按消息里的 `mediaId`（作为 `resource_id`）用 `dws chat +messages-resource-download` 下载并核对消息身份；其他渠道没有语音，直接报错。读不到或解不开时抛出「could not read the voice message」，Reader 不可用则按原样抛出，两者都不会表现为空或已回复。转写走 `ai_services` 的大文件路径，音频不经过模型：Agent 调 `ai_services.create_audio_upload` 取一次性上传地址，再调通用工具 `upload_audio_file(name, upload_url)`（任何来源的音频任务文件都可用，钉钉语音同理）由服务把该文件 POST 到该地址并返回 `job_id`，最后调 `ai_services.get_audio_transcription(job_id)` 直到 `done`。服务里不保存任何转写凭据——令牌就在一次性地址里。转写结果不缓存（Derek 2026-10-28 撤销了短暂上线的转写缓存）：不重复转写靠 Audit 的角色说明，不靠存储。
  自己发的语音是指令（Derek 2026-10-28）：语音无法带 `@磊哥`，所以本人发的语音在两处自成触发，且只限语音——服务自己发出的都是文字，不会自己回自己。钉钉：本人在以 Agent 名字命名的单聊（`chat_bot_names()`）里发的、含 `[语音消息](mediaId=…)` 的消息进入候选，仍排除服务自己的投递；本人发给同事的语音、群里的语音、该单聊里的文字仍是历史。微信：本人在 `filehelper`（文件传输助手）里发的语音（方向为 outbound）成为候选，前提是该会话有启用的回复范围；其他会话里的 outbound 语音、该会话里的文字仍被忽略。
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

所有业务类型共同遵守以下约束：

1. 先解析稳定的 `business_object_key`，再排队；入口输入身份和版本只追加，已结算正文改为来源摘要与引用。
2. 每个业务对象只有一个 current `reply_task` 和一个 current `reply_attempt`。
3. 重试、反馈和服务恢复只追加 `agent_run`，不得创建第二个业务 attempt。
4. 只有稳定动作身份对应的 provider 成功结果可以阻止重放；应用层不根据命令、工具、Skill、
   read-only 分类、receipt 格式或所谓“未知效果”推断业务是否完成。
5. Attention、Workers 和质量门只统计 current projection；History 展示旧 task、旧 run 和失败状态，原始过程按需从可用的原生来源读取。

质量门的「反馈没有结案」（`feedback_events / unresolved`，Derek 2026-10-24 批准）：没结案的反馈算错误，
但同时满足下列全部条件的手动反馈改为提示 `acted_unresolved`，不再让 Status 变 degraded——
key 是 `manual:<attempt 编号>`；该 attempt 在反馈到达之后又有一轮候选执行（执行创建时间不早于反馈）；
该执行状态为 `done`，至少一个动作已 `verified` 且没有未验证的动作；反馈从未被重新打开。
提示只说明反馈没有被晾着，
不说明重跑做对了；反馈记录本身仍是待处理，结案流程、Audit 和系统执行都不变。其他反馈（含用户 👎）照旧算错误。
同一规则的另一半（Derek 2026-10-28）：手动反馈指向的 attempt 状态是 `failed`/`blocked`，且同一触发之后已有任务 `done`
（任务完成时间不早于反馈到达），也不算错误，改为提示 `recovered_unresolved`——一个触发以最新结果为准，与回复 attempt 检查口径一致。
指向已发送 attempt 的反馈、或反馈晚于任务完成的，照旧算错误。

失败 Reply task 的 Attention 诊断读取该任务当前执行代的最新 run，展示原始 `source_code`
（没有时使用 `code`）、来源与明确标注为「Agent 说明」的 `reported_summary`。旧代或无法
解析的诊断不替换任务自己的错误。展示这些字段不证明外部操作已执行，不改变拒绝、重试、
人工决定或任务状态。

定时 Agent task 的 Attention 以同一 scheduled task 最新的已派发 run 为当前状态。较早 run 的失败 Reply task 和绑定它的失败 Reply Attempt 继续保留在 History，但不增加 Attention；最新 run 失败时仍显示其失败记录。与 occurrence 关联的 `scheduled_task_execution_unavailable` 事件，如果同一 occurrence 已派发的 Reply task 当前为 `done` 或 `skipped`，即视为已被该 task 终态覆盖；比较同一事件 ID，避免 task 终态写入与错误事件写入相差数秒时留下陈旧 Attention。其他旧事件仍要求后续已派发 run 的 Reply task 终态覆盖。`scheduled-task-run:<id>` 读取错误若已无法关联到现存 run，也只保留在 History，不增加 Attention。尚未派发的触发不覆盖当前 run，pending/processing/needs_human 本身仍不构成 Attention。

如果同一业务对象的 History 出现多个旧 task，模式迁移可以重建 `business_object_tasks` 的当前映射，
但不得删除旧输入、run、session、tool event、provider 结果或错误事件。当前投影修正不能被解释为
“历史从未失败过”。

服务启动时，`reconcile_done_reply_tasks_with_failed_current_run` 只把当前执行代的最新 run 确为
`failed`、且没有成功外部动作或已收口错误的 `done` task 改回真实失败投影。

Service supervisor 启动主 service 后，等待该进程完成启动期任务恢复并通过一次性管道发出 ready 信号，才启动 Email worker。主 service 在运行中重启时，supervisor 先停止 Email worker，再等待新 service 的恢复信号后重新启动它；Audit web 保持独立。这样恢复流程不会把另一个刚启动消费者领取的 Reply task 误判为上次进程遗留。

只有明确的操作员决定可以停止仍处于 `external_action_uncertain` 的失败 Reply task：先读取
`GET /api/console/reply-tasks/{task_id}` 的当前 generation，再向对应 `/skip` 入口提交 generation
和非空原因。写入原子检查失败状态与 generation，将 task 投影为 `skipped` 并追加
`reply_task_skipped_by_operator` settlement evidence；原 task error、Attempt 和 Agent run 保留，
不声称外部动作成功，也不调用 provider 或重新执行动作。重复提交、generation 过期或 task 状态变化均被拒绝。

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

Consumer 为每个 ProposedAction 返回 `action_identity`，只是同一提案内唯一的短标签。后续 revision 或
新 agent run 可以写不同的标识，Audit 不得仅因标识与前一版不同而退回或驳回（Derek 2026-10-26：
一封消息正常只发一次，发送与回执都有轨迹记录）。
服务根据 `business_object_key + action_identity + operation + target` 生成 `external_action_key`，而不是用
run id 或 revision 做去重；已批准并持久化的计划按其自身标识执行和重试，不受后续候选影响。

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
也不能要求发送目标会话等于源任务会话。业务会议跟进优先发送到 Agent 选定并经实时核验的
业务群；完整搜索后没有合适群时，普通业务总结可单聊给来源明确的参会人（若只有一位非当前用户参会人则发给该人；多人时优先选与讨论最相关者）。不要求会议原文明确指派此人负责行动。目标不可改发当前用户。该群不存在、已解散或不可发送时，本次投递失败并保留
原目标，不得改投其他群或个人。个人事项只允许完整日历 1:1 发给另一位参会人；人员敏感内容仍可
按独立授权拆分为单独私聊。群消息与敏感私聊各自保留稳定投递键，因此重试或新 run 不会重复投递。服务启动后
会从 append-only Consumer/Audit typed results 修复缺失的 `external_action_results`、`sent_replies`
与 observer；该修复不重放 provider 动作，也不改写历史 run/event。

会议日历事件可在标题不同的情况下关联到听记：听记创建者姓名必须与唯一、时间重叠且边界偏差在既有容差内的已确认日历事件中的一位参会人精确匹配。候选事件仍须唯一；创建者或事件关联有歧义时不推断参会人身份。无法唯一关联日历事件而改用听记参会名单时，名单包含听记创建者，即使创建者没有发言；只有现有目录查找将创建者姓名唯一解析为稳定 user_id 后，才把该身份作为可选参会人，歧义仍不产生可投递身份。复核该名单时按稳定身份将创建者作为参会人核验；只有名字确实出现在逐字稿中的人还需作为发言人匹配，不能把未发言的创建者误判成缺失发言人。

既有 `needs_human` Meeting 可按精确 meeting ID 刷新来源并重新分析。原 Runs 保持不可变；任务锁、活动运行、投递 claim、已准备消息或外部回执会阻止重开。重新排队后仍经过原 Consumer、独立 Audit 和投递门禁。
同一 Meeting Task 的内容修订若丢失了前一轮已经读取的群候选，后续 Consumer 会收到该候选及原证据，避免只改正文时丢掉目标上下文；这只恢复候选输入，不代表 Audit 批准或触发发送。

会议总结按受众拆分内容。业务会议的普通结论、安排和行动发送到 Agent 根据实时业务承接证据
选择的群；人员评价、绩效、薪酬、晋升、去留、候选人结论、健康或请假内容不得进入该群消息，
而是仅在收件人身份、职责和披露授权均有实时证据时，作为独立私聊消息发送给经核验的
参会人员。会议默认发送总结；面试（`skip_interview`，由 Derek 单独处理）和没有可共享信息量的会议（`skip_no_content`）是不发送的决定，经同一独立 Audit 后分别终止为 `skipped` 和 `no_action`（Derek，2026-10-28）。人员敏感详情始终从业务群正文和标题中拆出；即使目标群是 HR 群或参会人匹配度高，也不得放入群消息。无法核验授权时不发送敏感私聊，并记录未解决缺口。普通业务群按群内讨论和参会人与成员的匹配选择，不逐一核验普通群成员对普通业务内容的披露授权。参会名单不完整、无法与群成员匹配时，只要群讨论覆盖同一项目或客户且完整成员名单可读，就可选该群，并把这一缺口记入 audit_summary，不因此拒绝（Derek，2026-10-27）。群消息与敏感私聊使用不同的稳定投递键，任一发送重试都会复用已成功的另一条，
日历描述只写群消息版本。

会议消息已有不可变发送回执时，日历摘要恢复先只读原事件。若描述中已完整包含同一冻结摘要正文，
即记录 `already_present` 并结束日历阶段，不重复写日历或发送消息；不完整正文仍走现有标记块写入及严格读回。

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
共用；它不对公网暴露，也不增加 feedback 专用鉴权。结案入口接收现有
`ResolutionEvidence` 的配置、Skill revision 与 load receipt 字段，并交由 Store 按当前已持久化
的 decision scope 校验：`code`/`mixed` 仍要求本地 `main` 祖先提交；纯配置或 Skill 修复
使用原有配置关系及精确加载回执，不额外要求代码提交。调用者仍不能提交服务队列快照，
未知字段继续拒绝。当前轮次的关联、测试、不同 PID 的重启、health 及适用回执必须完整且一致；
权威实时的 `processing` / `failed` / `retryable` 数量由独立队列投影报告，不作为单个反馈项目的
结案门槛。状态和证据必须通过 API 持久化后回读一致。

该能力不在 Agent 页面增加第二套流程，也不使用模型合成重新打开理由或新回执。

### Email Agent task 映射

退订入口原文读取复用 `EmailSourceReader`：按当前保存的来源坐标和文件夹事实精确读取原信，位置变化时使用该读取器既有的定位流程，继续核对 UIDVALIDITY、稳定 Message-ID 与原文版本。已知位置有效时不枚举所有文件夹或扫描全邮箱邮件头；读取失败继续进入原任务现有错误处理，不能替换为其他邮件或其他退订入口。

退订浏览器的遮挡弹窗处理先核对 DOM 归属：若原已绑定目标位于确认弹窗内部，保留该弹窗并继续点击原目标；仅对弹窗外被遮挡的目标沿用既有关闭处理。

Email task Consumer 串行执行，每轮只领取即将处理的一条任务；后续任务保持 pending，直到轮到它们才创建 processing 锁并开始计时，避免批次内等待被 stale watchdog 误算为执行停滞。领取后的执行、重试和回收阈值沿用现有机制。

> **实现与部署状态：** Email folder classifier 与 audited-v2 lifecycle 已合入 `main`；本机
> launchd 的独立 Email worker 已启用，并通过真实 IMAP 可逆验证。SMTP 与自动回复仍禁用。

邮箱服务器中的当前文件夹是类别的唯一事实来源。服务维护“业务类别 → 每个账号的精确
provider 文件夹”绑定并单向创建/校验目标文件夹；分类结果本身不能覆盖文件夹事实。定期把这份文件夹事实读进训练快照的观察任务，一条邮件解析失败（IMAP 读到 `fetch_uid_batch`
Python 的 email 包无法解析的结构化邮件头）只跳过这一条、游标照常前进，不会重试同一条、也不会拖垮整批。Email worker 的 IMAP 会话（只读读取和确定性 provider 动作）使用共享 transport；socket 空闲超时、初始连接和服务器 greeting 总时限、每条命令总时限均为 90 秒。跨文件夹精确来源定位另有 8 分钟总时限，覆盖文件夹清单、各文件夹匹配和身份核验。TLS 行与 IMAP literal 都通过非阻塞 socket 和剩余截止时长分块接收，不依赖 TLS 缓冲文件层或跨线程关闭来打断读取；服务器持续零星返回字节也不能延长连接初始化、单条命令或整次来源定位。任一超时都会让整条连接作废并重连，不能在同一连接上跳过继续读——response 会和下一条命令错位；超时沿用现有外部依赖退避。Inbox
表示尚未分类，Spam/Trash 固定为内部 `junk`，Sent/Draft 不参加训练。`important` 不是类别，
而是独立注意信号：兼容 provider 的 Starred/Important/Flagged 信号与成熟模型信号取并集，
但 junk 始终抑制 important。控制台详情页的 Star / Flag 图标是主人本人的手动点击，直接让服务连上邮箱增删 `\Flagged` / `$Important` 这一个关键字并读回确认，不经过 ActionPlan；观察到的状态随后更新，下一轮扫描再对账。分类确认只保存最终类别、训练反馈和不可变 `ActionPlan`。确定性动作清单是
`label`、`mark_read`、`archive`、`move`、`trash`、`flag_important`；它们属于 Email 子系统，由独立
Email worker 领取、执行动作，并以服务器的应答为结果（Derek, 2026-09-25：不需要回读，服务器接受就够了）。邮件 Reply Task 的 stale 恢复由独立 watchdog 周期执行，不依赖正在处理任务的 Agent consumer 回到循环顶部。watchdog 复用 Task generation/CAS 恢复，只回收超过 stale 阈值、当前 generation 没有运行中 Run 或 RuntimeAttempt 的 Email Task；正在运行的任务保持 processing。恢复只释放原任务供 Email consumer 按既有流程领取，不执行外部邮件动作、不刷新租约，也不改写旧 History。
若一次移动已通过精确 DWS 读回留下 `reconciled_dws_readback` 凭证，而原计划中的加星/已读动作因 IMAP 读不到已移动邮件而跳过，控制台恢复入口可基于该历史移动回执追加不可变计划版本，只含原计划中尚未完成的加星/已读动作。旧计划的移动与跳过记录保留；恢复版本不再包含移动或退订。Worker 仅在当前动作仍属于该分类的当前计划、历史 DWS 移动回执唯一且目标身份一致时，使用回执中的 DWS 邮件 ID；写入前以邮箱、发件人、完整主题、会话和收信时间差不超过 10 秒做唯一匹配；来源与 DWS 时间复用原 MOVE 核对的日期解析合同，支持合法 RFC／ISO 表示、要求明确时区并统一为 UTC，不改写原始来源值；并重算回执的 SHA-256 绑定校验，不把 IMAP thread ID 当作 DWS conversation ID。匹配不唯一、哈希不符或字段不符时停止。DWS 搜索空结果若返回 `total=0` 和固定的空占位对象，执行器只将这一精确形状归一为空集合。已读/重要状态通过 DWS 搜索读回；只对原计划授权的加星/已读动作写入，并将回执保存在原动作生命周期中。非交互 DWS 写命令带全局 `--yes` 以完成 CLI 确认；前置精确身份/状态读回和写后状态读回保持不变。一个 DWS 动作若在写入前因身份校验 ValueError 失败，恢复入口可将该精确失败复制到新不可变计划；Worker 会先读状态，已满足则不写，缺失时才按原授权动作写入。普通 IMAP 动作路径不变。

DWS `FLAG_IMPORTANT` 沿用原邮箱的标记合同：系统标签 `1` 对应 IMAP `\Flagged`；系统“重要”标签 `11` 和同名自定义标签不是该信号的替代。写入用 `addTags --tags 1`，逐封读回用同一精确身份查询加 `tag:1`，不按显示名称选择标签，也不使用示例字符串 `tag:important`。执行器通过当前账号连接只读 EXAMINE 原 MOVE 参数中的目标文件夹，复用 IMAP 原有 PERMANENTFLAGS 要求；只有要求恰为 `\Flagged` 时可由这条 DWS 映射证明完成。若邮箱还声明 `$Important` 等尚未核实映射的要求，明确失败且不写、不忽略信号。元数据探测发生在 DWS 执行器的原有非自动重试错误边界内；不改变自动重试政策。

`retry-dws-source-verified-email-action --account <account> --action-id <id>` 是独立的显式恢复工具，默认只读 dry-run，`--apply` 才登记一次请求。它只接受当前计划上 `dws_mail_action` 的写后读回不匹配失败，重新核验唯一历史 MOVE 凭证、目标邮件及账号标记要求；factory 配置快照必须与当前账号一致。写锁内再次比较计划、动作、尝试数、参数、失败、来源/MOVE 与配置，确认无并发动作且前序依赖完成后，使用现有 retry request owner 释放同一个 Action。原计划、原 MOVE、失败回执和已有标签保留，不把写后失败解释成无效果，不追加计划、不放开自动重试。普通 Worker 领取后再次核验：状态满足就追加 `dws_readback_noop` 回执；缺少时只执行原授权动作并读回。缺失/变化/不完整的来源证明不释放。

每次 provider observation scan 在同一 SQLite 事务中发布消息状态、authoritative folder 的缺失对账和 folder generation 时间；generation 时间单独按账号/文件夹保存，读取仍返回该文件夹最近一次完整扫描时间。相同消息状态不重复写消息行或其索引，扫描成员先写入连接级临时表再用索引对账，避免在共享数据库写锁期间构造超长身份列表。这样保留整代原子可见性，同时限制 Email worker 对共享 SQLite 的写锁占用。
候选分类的同组动作按每批 500 个分类一次读出，再在内存中分组，选择结果与逐分类读取一致。
稳定 Message-ID 查找若在多个文件夹命中，通常属于确定性身份歧义：在只读状态定位阶段失败，不执行邮件写入，也不自动重试。若移动动作的原 UID/UIDVALIDITY 定位失效，执行器只读检查解析后的目标文件夹；仅当该文件夹恰有一条同一稳定消息身份且满足目标状态，才以 `readback_noop` 记录已发生的移动，不再派发 MOVE。目标文件夹没有唯一匹配或状态不满足时仍保留失败，不以其他文件夹的匹配推断成功；超时、DNS 和连接错误仍可重试。

若历史 `move` 因 `readback_missing` 结束为 `skipped`，可用一次性 `reconcile-skipped-email-move-from-dws` 只读核对补录回执。它要求当前计划仍是同一条 skipped move、DWS 企业账号唯一、搜索结果与目标文件夹读回中的内部邮件 ID 和会话一致，并同时匹配原记录的发件人、完整主题及收件时间（误差不超过 10 秒）。任一身份不唯一、目标文件夹读回不完整或状态不匹配时不写入。核对通过后只追加正式 move done 回执，不调用 MOVE；现有 Email worker 随后处理依赖它的标星、已读动作。该入口不连接 IMAP，也不处理退订。
执行前仍先读一次当前状态，已满足就不写。改标记的动作（标已读、标星、贴标签）在服务器接受 STORE 后即完成；
移动、归档、删除在服务器回了 `COPYUID`（新位置已知）时同样即完成。只有服务器没说邮件去了哪里时，
才保留一次回读，用同一账号共享的连接去找新位置并确认。进程内每一个会碰 IMAP 的调用点——直接动作
投递（写连接和回读连接）、定期分类扫描、训练观察任务、OTP 读取、历史回溯（含重读候选、补建模型
动作）、Agent 任务上下文加载、退订入口解析——现在都经 `EmailAccountConnector`
（`app/email_account_connector.py`）取用，一个账号一个连接，由互斥锁串行化，按优先级排队（用户直接
等待的：分类回读、扫描、直接动作投递、OTP、任务上下文、退订入口解析，都是 HIGH；后台批量的：训练
观察、历史回溯、模型动作补建，都是 LOW）；排不到就在 600 秒后超时报错；连接闲置超过 45 秒、连续
复用超过 300 秒或探活（NOOP）失败就丢弃重连，失败的连接不复用；每个账号的连接由 `EmailConnectorRegistry` 持有，各调用点的
`source_factory` 从连接器取用、关闭时把连接签回而不是真登出。退订审计/直接执行走独立进程（每次任务一个 CLI 调用），各自建一个只服务这一个任务的
registry，不与主 worker 进程共享——同一账号在两个不同进程里各自最多开一条连接。这些确定性动作
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
分类器的 Codex 轮次将原生 `project_doc_max_bytes` 设为 `0`，不重复注入仓库的
`AGENTS.md` 项目说明；分类任务输入中仍包含本轮冻结的完整分类 Skill、邮件事实、
允许类别和退订候选，且使用原有结果校验及候选索引绑定。
该 Codex 轮次还通过现有原生 `skills.config` 只保留默认常驻插件入口，排除与分类无关的
原生 Skill 目录及已由服务完整提交的分类 Skill 副本；这不改变 Claude/Friday 路线或
其他任务的 Skill 目录。
它的提示词里带最多 8 封主人已确认标签、与当前邮件最相似的邮件（发件人、主题、正文前 250 字和主人给的类别），
让它照主人的归类习惯判断（Derek，2026-09-26；`app/email_similar_examples.py`）。相似度用字符 n-gram 的 TF-IDF，
只取主人确认过（`classification_source='user'`）、类别仍然存在、且不是当前邮件本身的行；索引按主人确认标签的“条数+最新更新时间”缓存在进程里，每次分类只读这一个戳；主人有新确认时才重读并重建。
示例只是证据，不是指令，不改变“只分类、不行动”的约束。模型输入只投影当前邮件、允许类别及说明、未读状态和退订候选，不传服务路由 locator、稳定邮件身份、文件夹目标映射或配置版本。示例里含其他邮件的片段，会随提示词发给所选 runtime 线路。
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

邮件处理进度将当前 ActionPlan 的 `pending`／`processing` 邮件动作计为实际队列，不设年龄窗口；
近 24 小时动作计数仍用于完成比例，近 30 分钟仍用于吞吐。旧的待办动作不能因为超过 24 小时
就让页面显示“队列为空”。该读数只展示队列，不触发动作、对账、重试或状态变更。

退订是低风险的直接 Email worker 动作；退订不做结构化审核，也不做 Consumer 或 Audit turn。
worker 以 task id 从
durable 状态读取 ActionPlan 已授权的 entry，一次调用完成整件事——打开该 entry，按页面当场呈现的
精确链接、表单或独立退订按钮操作，直到第一个终态页面，然后保存 outcome 和脱敏后的页面原文。
兼容调用语义仍称为 `unsubscribe_email(task_id)`，但它由 worker 直接执行；没有 proposal 要抄写、没有
acceptance 要绑定、没有 Audit turn，也没有 continuation 要续。

幂等性只靠 receipt：`email_unsubscribe_receipts` 里每个动作身份一条，已有 receipt 时再调一次只会
把它原样返回，不会重复退订。首次外部调用前会持久化执行标记。只有执行返回明确的瞬时浏览器错误码时，标记才转成带错误码和
重试次数的 retryable 状态；任务按现有指数退避及四次预算恢复同一 ActionPlan、entry 和输入摘要。
正式重试可产生新的 Task generation/input version；只有任务 ID、稳定 Action identity、完整输入摘要
相同且标记含已识别的瞬时外部错误时，才把这个 retryable 标记接续到新 generation，并保留原重试计数。
终态 receipt 仍是唯一成功／跳过凭证。进程中断、结果未知、错误未分类或重试预算耗尽时，
标记保持不可重试并进入核对／Attention；不能清标记或重放来推定成功。已有不可分类的执行标记会以
`email_unsubscribe_outcome_unresolved` 记录为外部结果未确认，而非内部 `ValueError`；它仍保持失败并等待
只读回执核对，不会自动重放。浏览器步骤本身不升级为
`needs_human`，技术失败保留为失败；有浏览器步骤但没有 receipt 不再升级为 needs_human，重试资格由上述明确标记和错误码决定。
旧格式执行标记只有在与当前 Task、稳定 Action identity 和完整输入摘要一致，且 Task 错误匹配共享外部瞬时失败分类、历史 Attempt 记录了有效退订操作时，才可升级为同一全局 `retryable` 退避状态；判定覆盖所有支持的入口来源和操作阶段，不按站点或单个案例分支。错误/阶段身份不匹配、未知结果或预算耗尽时，其他标记继续作为对账围栏。
启动时若终态退订回执已落库而对应任务仍失败，符合恢复条件的任务按同一动作身份投影为 `done` 或
`skipped`，不再次调用邮箱提供商；带回执的更新只读取该任务自己的持久化字段。

页面要求登录或 CAPTCHA 时记 `skipped_login_required` / `skipped_captcha`。
页面说的是什么由 Agent 判断，不再靠关键词（Derek，2026-09-25）：服务照旧负责机械步骤（打开链接、等页面文字连续 1 秒不再变化、操作控件、抓取文字），然后把主机名（不含带令牌的链接）、页面文字（最多 6000 字）和可操作控件的种类交给一次 Agent 调用，它返回一个状态（已退订、之前就退订过、要登录、要验证码、要付费、链接过期、有可操作项、看不出来）和决定它的页面原话。除 `unknown` 在页面没有可靠判读原句时可返回空证据外，其余状态必须给出页面原文证据；`unknown` 仍走页面状态未知的浏览器错误和现有退避重试，不产生成功回执或点击任何控件。原话写在回执结果文字的第一行“判断依据：……”，后面是它读到的整页文字。链接过期和看不出来都不算页面本身的结论，走下面“页面读到了但这个服务不操作它提供的控件”那条路。Agent 线路不可用或返回的不是约定的对象时，这一次读取按浏览器故障失败（记录里的类别是 `page_judge_unavailable`），任务先延后重试一次，仍失败就记 `failed`，可以手动重跑，不猜结果。审计式（audited）旧流程没有 Agent，仍按关键词读页面；工作负载种类是 `email_unsubscribe_page`。
页面读到了但这个服务
不操作它提供的控件时记 `skipped_no_reliable_entry`，并保留页面原文。两种跳过（没有可操作项、需要登录）都只是“当时读到的页面”，不是对链接的定论：手动重跑会重新读页面，读法相同时保留原回执，读法不同（比如需要登录变成已退订）时用新回执替换它。

任务已关闭（done / skipped / needs_human）却没有 receipt 时，启动与训练维护里的对账会补一条 `skipped_no_reliable_entry`（证据 `durable_context_entry_unavailable`），让后续动作不被卡住。`failed` 的任务不在此列：它结束于技术故障，不是对链接的结论。失败的任务保持 `failed`，由人重跑。

receipt 另外保存 `entry_url`，即这次实际打开的完整私密 URL。`entry_reference` 只是该 URL 的 sha256，邮件 HTML 正文也不落库。写入前校验 sha256 与 `entry_reference` 一致；该值是可直接触发对外副作用的链接，只在 receipt 表和 Attempt 详情页出现，不进入 `trigger_message_json`、步骤日志或错误码。

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
`junk` 的退订候选由代码从标准 header/body 链接中发现；分类 Agent 的 wire result 只返回本次候选序号，服务按该次调用的候选列表绑定精确 URL，并继续执行既有类别、索引和持久化校验；模型不回传或改写 URL。选定候选后，独立的系统直接退订流程执行后续网页步骤；成功或无需继续后再移动到系统 Trash。连接邮箱的邮件 OTP 只在站点、收件人和有限
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

Email Console 的“模型训练”页读取后端统一计算的晋升资格。默认门槛为 Micro F1 ≥ 0.95、
每个启用类别 Precision ≥ 0.95、逐类独立测试 support ≥ 20、常驻端到端 P95 ≤ 500ms。
门槛通过带 expected-current version 的接口追加新版本；修改门槛不激活模型。
现有 important 和完整性条件继续适用，并比较候选与当前描述集和类别集合。
缺失指标显示未测量，历史 classifier-head latency 不充当端到端测量。

用户打开主模型开关后，runtime 在 Registry 锁与配置写锁内重读当前事实、重验候选，并原子
替换 online-active.json。该文件同时持久化运行模式、模型身份和模式切换历史，是这次切换的
提交事实源；进程中断不会留下没有切换身份的已激活模型。关闭开关保存 Agent 主分类及历史，
不删除任何模型。worker 在 tick 时读取新事实，不需要为开关本身重启进程。
描述编辑把当前配置和不可变版本快照在同一 SQLite 事务中保存，过期编辑返回冲突。

### Repository Upgrade

服务周期性读取配置的 `origin/main`，只识别可安全 fast-forward 的更新；分叉、状态指纹变化或
脏工作树不会被静默覆盖。History 页面只展示状态并启动带 operation ID 的 detached updater；
日备份循环只保留一个最新的已验证日备份；部署与日备份共用备份目录，仍只保留一个完整副本。

updater 在共享 Git 锁内重新校验指纹，必要时按用户确认的分支名和提交信息保存本地改动。服务静默并停止后，每次部署从当前数据库创建独立 operation ID 的一致性备份，不复用同日或此前部署的旧副本。日备份仍每天一次；当天完整副本不存在时创建新的当前快照。

备份共用目录锁；持锁时先清理仅由本流程生成的 `.ceo-backup-stage-*.tmp` 半成品及其 sidecar，保留其它临时文件，再在同目录生成一个候选文件：完成 SQLite backup、DELETE journal、integrity_check、完成标记提交并关闭连接，再读回完成标记，才原子替换目标文件并清理旧备份及该目录中残留的候选文件。复制或验证失败只清理本次候选，原已验证备份保持不变；替换后清理失败仍保留新完整文件并明确报错。验证期间允许新旧两份共存，成功后仅保留最新一份。原子文件名替换不等于已验证突然断电后的持久性；没有新增 fsync 保证。

部署在新备份创建后、服务仍停止时取得同一目录锁，持续持有至健康／加载验证、发布收尾或失败回退结束，防止刚启动的日备份线程提前删除回退所需的来源。日备份是独立组件线程，等待该锁不阻塞 API 启动。部署备份失败时不切换代码，沿用既有失败恢复流程。每小时日备份循环确认当天副本完整后，继续清理数据库目录中超过24小时的 `auto-reply.sqlite3.pre-*` 及其 sidecar。下一次日备份可以用更新的当前快照替换部署前快照；本策略不长期保留旧 schema 数据。随后沿用既有依赖、构建、启动与健康验证流程。收尾验证见[迁移收尾](archive/records/database-migration-closeout-validation.md)。
等待静默期间，updater 不向共享 SQLite 写部署进度，避免与仍在运行的业务写事务争锁；服务静默并停止后，才用不触发 schema 初始化的窄状态存储记录后续阶段。该短写仍对意外的并发 SQLite 锁做有限次数重试。
升级后验证失败时只对本次安装的精确 commit 做 compare-and-swap 回滚；无法证明仓库仍归本次
操作所有时进入 `needs_manual`，不执行破坏性 Git 操作。MCP 配置不由该流程探测、禁用或覆盖，
直接沿用用户当前 Codex 配置。

**生产检出与部署**（Derek 2026-09-25）：生产检出规则见 `AGENTS.md`；任何会话都可以部署：`python -m app.deploy`
复用上面的 updater，先等没有进行中的 Agent 回合和已领取的条目（30 分钟内不空闲就什么都不改），
再停止服务、创建本次部署独立 operation ID 的新备份并验证、fast-forward、控制台不是从检出当前的 `frontend/` 构建的时重建（比较构建戳 `app/static/workbench/.built-from` 与 `HEAD:frontend` 的树，而不是看本次部署的差异——中途停下的部署或别的会话插进来的部署会让下一次差异里没有前端改动，控制台就停在旧版本）、检查 import、重启，并轮询健康最多
15 分钟（重启要重读数 GB 的数据库，负载高时曾用 8 分钟，只探一次会把正常升级误判回滚）。
需要只发布已批准的精确版本时，可加 `--target-commit <40 位 SHA>`；该 SHA 必须指向当前 `origin/main` 历史中的 commit 对象，且不能使生产倒退。即使 main 后续快进，部署也只合并该 SHA；等待空闲后重新获取 main，若已不再包含它，会在常规停止/备份前退出。网络获取期间若又有任务进入，则在同一空闲等待期限内继续等待，不会直接停止正在运行的任务。不指定时沿用当前 main 的普通部署行为。
`launchctl bootout` 成功后还会等待 `launchctl print` 明确确认服务 label 已卸载，避免异步移除期间过早 `bootstrap`。
静默检查将会议和回复任务处于 `processing` 视为在途工作，仅当它们有有效的对应 dispatcher 租约，
或分别存在运行中的 meeting alignment / Agent 运行记录时阻止部署；没有这些 owner 证据的孤儿行
由服务启动时现有的恢复逻辑重新排队。Email processing 动作通过明确支持的 publication 源投影计数，
覆盖旧 Email 表和当前 common Action 的状态字段；没有 Email migration ledger 的 Store-only 数据库
不贡献 Email 动作计数。仍锁定的会议投递继续阻止部署。静默轮询每次只读计数后关闭数据库连接，
避免长等待累积 SQLite 句柄。
服务常驻的 `runtime-attempt-reclaim` 每五分钟还会用现有 generation/CAS 恢复回收超阈值、
没有同代活动 Run 或 RuntimeAttempt 的 processing Reply Task；因此单个同步业务 worker 卡住时，
其他停滞 Reply Task 仍可恢复。带活动执行证据的任务保持 processing。该恢复只重新排队，
邮件外部动作仍由原有执行围栏、重试条件和回执核对控制。
紧急部署可显式使用 `python -m app.deploy --skip-quiet-wait` 跳过在途工作等待；部署仍通过
updater 正常停止服务后才备份和切换检出，重启时由既有任务恢复逻辑接管中断工作。若切换在新版本
安装前失败，updater 会重新启动旧版本并检查健康；设置重启也会在 launchd 任务已卸载时重新 bootstrap。
两个会话同时部署由仓库锁串行，后到的发现检出已前进就停止。只改了设置、没有提交要部署时（有些设置，比如邮箱账号，是 worker 启动时才读），用 `python -m app.deploy --restart`：同样先等没有进行中的工作，再经 launchd 重启并等健康；确需立即重启时可加 `--skip-quiet-wait`，不手动 `launchctl kickstart`。

仓库 mutex 覆盖源码解锁、停机、备份、快进、构建、校验、启动和重新锁定；settings-only restart 也使用同一把 mutex。若中断只留下未暂存文档，且每个文件都与刚 fetch 的目标提交逐字节相同，正式 deploy 会先停服务并备份，再把这些文件恢复到当前 HEAD，由正常 fast-forward 安装目标版本；任何暂存改动、其他路径或内容不完全一致的工作树仍按本地修改保护规则拒绝部署。

### 会议投递目标

确认既有主消息已送达的状态恢复会保留原准备正文和 provider 回执；当持久化决策含 `_service_audit` 时，先核对 Audit `approve` 与候选 SHA-256 绑定，再由 Meeting 决策读取器解析原候选。无效封套不恢复；未带封套的历史回执继续沿既有核验路径处理。

日历仅用于确认参会名单，不决定业务内容的披露受众。会议跟进使用同一 `MESSAGE_AUDIENCE_CONTRACT`，并覆盖其通用的敏感群消息例外：业务总结优先发到
根据相关群讨论和参会人成员匹配选出的群；完整群发现后没有合适群时，发给一位来源明确的参会人单聊，优先选与讨论最相关者，若仅有一位非当前用户参会人则发给该人，不要求会议明确指派其负责决定或行动。目标必须绑定来源中的稳定 user_id 且不能是当前用户。普通业务内容不逐一核验群成员披露授权。所有人员敏感内容
均从群正文和标题拆出，作为独立私聊发送给身份、当前职责和披露授权均有实时证据的个人；HR 群及
参会人高匹配度不改变此规则。明确业务私聊的稳定 `user_id` 必须唯一匹配来源参会人
或日历证明的组织者，身份和受众理由保留在决策中；不可从逐字稿发言人推断组织者。
群不可发送时保留失败，不能静默换成组织者或本人。私聊入口拒绝本人及已核验别名；
职务、HR 身份和私聊本身均不构成披露授权，身份检查也不替模型证明业务理由真实。
群标题相似、部分参会人重合或近期活跃本身不构成业务承接证据。
裁员、减员和人员优化的存在、进展、时间及执行安排本身就是人员敏感信息；没有姓名、名单或人数不构成脱敏。会议总结中的人员敏感内容始终不得进入业务群，不逐一审核群成员是否可接收敏感详情；敏感部分只可按具体内容发给经核验且有披露授权的个人，找不到时保留未解决事项。普通业务群只检查群内业务讨论和参会人成员匹配，不逐成员审查普通业务内容的披露授权；没有合适群时普通业务总结可发给相关参会人单聊，目标使用来源里的稳定身份，群是否可发送由服务投递时核验，不可发送则失败且不改投，不得为发送而选宽泛群。敏感分类和个人授权判断由 Agent 根据证据完成，不增加运行时关键词过滤或比例授权阈值。
找群先核对会议材料里明确提及的讨论群，再从结论、行动和负责人提炼主题，分别以原文中文
业务词、英文术语、会议标题和核心议题查群及群内消息。预置候选或首次搜索零命中不等于穷尽；
候选须核对近期同工作线讨论、行动承接人、受众和可发送性，并在决策中说明来源与业务承接关系。
搜索结果按会议标题/摘要与群名筛选并读取完整群成员。成员匹配统一使用准确率 `member_precision = 匹配参会人数 / 完整群成员数`（3/N），不再使用参会人召回率或原有“至少两人且覆盖60%参会人”的过滤条件。业务讨论和主题仍先于成员准确率排序，准确率用于同等业务证据的候选比较，不能覆盖实际业务归属或替代披露权限。候选保留完整成员 IDs、群人数、匹配人数、准确率及参考参会名单是否完整；历史投递只记录次数，不标记为已验证受众。平台中立的发现服务只返回候选及证据，不决定最终目标或发送；近期消息截取和历史送达只是发现线索，Agent 仍需核验完整相关讨论与当前受众，不得按搜索顺序截断候选或仅凭词项匹配判断披露范围。

群发现对保留候选的完整成员读取和近期消息读取分别使用固定最多 4 个并行只读调用，每次最多提交 4 项；按原候选顺序消费结果后再执行原筛选与稳定排序。候选范围、阈值、成员完整性、每项证据、读取计数及错误分类保持原合同。并发完成顺序不改变同分候选顺序或首个失败的归属；失败时不提交后续批次，等待已启动的只读调用结束，不增加重试、缓存或外部写入。 DWS 执行边界仅允许原生 `chat group members` 与 `chat message list` 共享固定 4 个读取槽；其余命令及隔离进程命令仍全局排他，须等待已启动读取结束，所有命令继续使用同一启动间隔和原超时、错误处理。

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
旧运行保持历史，只在确认未发送且无活跃归属后清空原决策重做实际来源与受众核验。该入口接受 `failed`、`no_action` 和 `needs_human`（Derek 2026-10-27）：`needs_human` 任务由控制台重跑重新开启，原 Run 与 Audit 反馈全部保留，重跑从 `retry` 重新经 Consumer→独立 Audit。
当 DWS 逐字稿接口故障而完整 MCP ASR 结果可用时，`import-meeting-asr-transcript` 通过原
meeting job ID 绑定模型、音频 SHA-256、分段时间和完整分块数，并以单事务重新排入同一个未发送
任务。入口只接受有完整日历参会人证据的失败任务；ASR 不提供说话人身份或受众判断。已有部分
效果、投递回执、活动领取或运行时回合都会拒绝导入。分析和投递阶段读取同一持久化转写，保留
原失败运行；外部消息仍只能由原 Meeting 审查与投递流程产生。
独立 Meeting Alignment planner/delivery 仍是独立路径；共享受众规则不代表迁入通用 Consumer/Audit 引擎。Meeting Alignment 没有任务内 Skill 读取能力。Codex 路线在本轮有冻结的托管会议 Skill 时，提交本轮定时任务提示和该托管 Skill 的完整规则，省去已由服务完成的操作 Skill 正文；原生 Skill 目录仅保留 `dingtalk-chat` 入口供 Agent 按需使用 DWS 核验群，其余重复 Skill 隐藏。其他路线仍提交全部冻结 Skill 正文。没有托管会议 Skill 或冻结材料时，沿用完整原输入及旧自定义 inline 约定。结果模型、受众核验及通过审核后的投递机制保持不变；新增独立审核门禁。

Meeting planner 生成的 `audit_summary` 只是候选字段，不是 Audit。每个候选在进入 `ready_to_send` 前必须由独立、model-only（无原生工具或 MCP）的 Audit 调用复核会议来源、群候选证据、准确收件目标、正文事实和披露范围。会议原文明确把后续工作分配给精确收件人时，该分工本身可证明向其分享完成任务所需的简要结论和必要背景；不要求另有逐字发送指示，也不能据此扩大到无关或敏感内容，职位本身不构成授权。运行器传来的 Codex JSONL 先提取最终 assistant 消息；路由器已规范化的纯 JSON 则直接解析，两种输入均执行相同结果模型校验。Audit 结果不含 digest，由服务把被审核候选的 SHA-256 写入 `_service_audit` 元数据与原候选共同保存（旧结果中模型抄写的 digest 须与之相等）；新投递与人工重排入口都要求重新验证该绑定且结果为 `approve`。没有发送回执、没有活动领取，且因候选与既有收件目标绑定冲突失败的旧任务，可由最新 Attempt 通过明确的 audit-only 恢复入口重新读取会议来源并审核原冻结候选；若上一次 Audit 为 `return`，同一入口可在用户明确请求后再次复核。入口要求精确候选 SHA-256，不重新生成或改写目标/正文，并先剥离旧审核封套再解析候选。只有同一候选的新独立 Audit `approve` 才以 CAS 重新进入原投递 worker；`return`、`reject`、Audit 错误或候选变化均保留失败，不触发发送。`return` 携带 rule/observation/requested_revision 并进入反馈修订（上限同 `MAX_CONTENT_FEEDBACK_CYCLES`），新 Consumer revision 使用独立 Meeting run 与 Audit runtime workload；达到上限仍未通过才转 `needs_human`。`reject` 转人工，缺失、无效或摘要不匹配按失败关闭，均不得产生新投递。独立 Audit 的外部依赖暂时不可用时，沿用 Meeting 原有外部依赖退避重试；无效审核结果按失败处理。旧的未审核 `ready_to_send` 记录也会在投递前失败关闭。已存在 provider 回执的只读核实与状态恢复继续沿用原有回执、正文及目标校验，不要求为既成外部效果补造审核。收件人准备绑定仍不可变；新会议投递键按规范化收件目标区分，旧键只有在已绑定目标与本次目标完全一致时才复用，避免跨目标误用回执。重跑与来源恢复仍会拦截已有准备动作；provider 回执与幂等投递保护不变。

定时 Meeting 重放仅重新开启没有结果、也没有已准备钉钉外发动作的失败或 `no_action` 任务。普通、敏感和目标绑定版本的 prepared outbound 即使还没有 provider 回执，也会阻止重放；这些动作由原有投递恢复路径处理，重放不能清除后重新分析其既定动作，也不能改变已绑定收件人。Console 可从最新失败 Meeting Attempt 显式请求重跑；入口确认 Attempt 仍是该 Task 最新运行后，按精确 Minutes UUID 只读刷新该会议的来源与参会名单，再调用同一受保护重放方法。来源不可用或任务不再符合重放条件时保持原状态。后续重新进入普通 Meeting 分析和独立 Audit，历史 Run 保持不变，不直接发送或跳过原有外发回执门禁。

Meeting planner 与 Audit 的 RuntimeAttempt 分别使用 `meeting` 和 `meeting_audit` workload kind，并以准确 Meeting Run 十进制 ID 为 key；native `parent_id` 都指向该 Run。显式迁移转换器保留新 source 的数字 `meeting_audit` key，也只在转换中处理历史 `meeting` 加 `:audit` key。重放检查读取 TaskBusinessKey 中已保存的公开投递键，以及对应的敏感和目标派生版本，确保 Task ID 重映射后仍识别已有 prepared outbound。

Meeting Codex 命令设置原生 `project_doc_max_bytes=0`，不再额外注入工作区 `AGENTS.md`。会议来源、工作人格和本轮冻结的托管 Skill 仍由服务提交；全局原生指令、Schema 和独立会话保持原样。

Meeting 群候选的受众核验要求在非空候选列表前完整提交一次，不再在每个候选项里重复；
候选的群身份、完整成员名单、近期讨论与读取状态仍逐项保留。空候选列表保持原输入。

`audience_scope` 是当前 Meeting Alignment Agent 输出的必填字段。投递器读取早于该字段的
持久化 `send` 决策时，只能从其已保存的 `target.kind` 规范化一次：`group` 对应 `business`，
`direct` 对应 `personal`，并立即写回规范 JSON；缺少目标或其他字段不合规的记录仍按失败处理。
该规范化不重新分析会议、不改变目标，也不重发已有送达记录。

## History 语义与无效入口边界

History 页面顶部固定展示与独立 Attention 页面共用的 Attention 面板，直接读取同一 Attention API；它不改变记录筛选、计数或终态语义。

Tasks 与 History 列表的结果、总数、分页、快照与错误按完整当前查询归属，搜索、筛选或翻页在当次渲染中隐藏旧查询快照；输入控件不重挂载，搜索焦点保留。同一查询的后台刷新继续展示已有快照，慢请求不被轮询打断。History 图表独立按时间范围归属，列表条件不重取图表。独立 Attention 及其嵌入面板的定时刷新不重叠，刷新键变更和卸载会取消旧读取，迟到成功/失败与计数回调不得覆盖新快照；已消失问题的展开状态清除。上述均为前端读取归属，不改变业务执行、授权、审查、恢复或发送策略。

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
- History 的逻辑来源是 `HISTORY_SOURCE_TABLES`：`reply_attempts` 投影、会议、Task 各来源，
  以及两类新来源。Audit web 启动不预热大范围 History 查询；打开尝试详情时批量读取该任务代次的
  Agent run 及工具事件，图表只投影所选时间范围内 attempt 的当前 History 状态。
  顶层查询按来源显式组织分支，计数键查询和页面明细使用同一来源投影；不按 SQL 文本拆分嵌套的 Attempt 来源查询，也不缓存页面结果。
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
  最近一次失败只在 History 里；同一短暂故障不会逐分钟弹 macOS 失败通知，达到 15 分钟
  门槛时只通知一次，恢复时仍发送成功通知。详情链接到 `/email?tab=all&selected=<邮件>`。
- 这两类来源与 `reply_attempts` 一样按 `occurred_at` 排序，时间统一成
  `YYYY-MM-DD HH:MM:SS`（UTC）。History 图表仍只统计回复 attempt、会议和 Task，不含
  定时命令和邮件动作。
- `reply_tasks` 的 `pending` 和 `processing` 是当前队列任务，必须在 History 中按真实
  状态展示、筛选和计数；它们不属于 Attention。还没有 Attempt，所以不能像已完成记录
  那样链到执行详情：`channel='scheduled'` 的队列行链到它所属定时任务的运行记录
  `/scheduled-tasks?id=<任务>`（映射来自 `scheduled_task_runs` 里 `execution_kind='reply_task'`
  且 `execution_id` 等于该 reply task id 的那一行，`AutoReplyStore.scheduled_task_ids_for_reply_tasks`），
启动时的 Reply pre-Agent orphan recovery 只处理由 Agent 执行通道拥有的任务；由专用 worker
消费的其他通道继续由各自 worker 的租约恢复负责，不会因不存在 AgentRun 被重置。
  其余渠道链到 `/workers`（Derek 2026-09-27：点开原来一律落到 Status 页，看起来像详情坏了）。
- History 以 task 为核心（Derek 2026-10-23）：回复类记录的每一行带它所属的 `task_id` 和该 task 的
  `run_count`（Agent run 总数），行内链接指向 `/history/tasks/<task_id>`，而不是直接指向某个
  Attempt。task 页（`GET /api/console/history/tasks/<task_id>`，`app/web_api/history_tasks.py`）
  展示 task 的当前状态，并列出它的全部 Attempt（各自链到 `/attempts/<id>`）和全部 Agent run
  （按执行代标明「当前代」或「历史代」）。Attempt 详情页不变；这是对已存行的只读投影，不改状态、
  不改任务生命周期。队列行、会议、定时和邮件动作行没有 task 页，仍按各自规则链接。
- `POST /api/console/history/tasks/{task_id}/retry` 是 Task 级技术恢复入口。Reply Task 沿用当前代最新 Consumer/Audit run 的可重试错误校验。直接 Email 退订 Task 仅在当前输入精确绑定退订身份，且不存在 AgentRun、认领、回执、效果或浏览器步骤时才可入队；无标记可安全开始，已有标记仅允许当前 task/action/input 精确匹配、状态为 `retryable`、错误码为类型化瞬时浏览器错误且预算未耗尽。`pending`、unknown、身份不符或超预算标记原样保留并拒绝重试。显式重试按稳定 action identity 最多 3 次，代次轮换不会清零预算。两种路径都保留原 Attempt/History，只创建新执行代并排队，由原 worker 和现有外部动作门禁处理，不写成功终态。
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
作为服务健康状态或执行队列的一部分。导航、History 卡片、页面和路由不提供此入口；
`feedback_events` 及其反馈流程仍保留。服务修复候选是业务记录，按数据库重设计迁入
`business` 的 `service_bugfix_candidate` 类型，保留原字段、公共 ID、水位和唯一
`feedback_event_key`；当前初始化不再新建旧 `service_bugfix_candidates` 表。迁移从已有
旧表搬运记录并提供可验证回退，不能把该存储合并误解为重新开放已移除的页面或路由。

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

`AutoReplyStore` 和 `EmailStore` 在构造器缓存或 journal 修改前只读核对准确当前声明。真正空库在共享路径文件锁内直接建立当前结构；准确当前库冷启动不执行 DDL。旧版本及缺失／损坏对象必须由显式发布工具处理，运行时不迁移、修补或回填。短暂 `locked`／`busy` 仍沿用有限等待复查，不把锁错误当成结构缺失。Email 完整持久行校验在释放 schema 锁后执行。 Email 读取使用这个已验证的当前 Store 合同，直接查询 Task、Run 和 Action，并调用当前 EmailStore 方法；真实缺失的模型、快照或扫描进度仍按其返回值展示。

### Workbench Turn native execution ownership

Workbench public parent and Turn IDs are decimal strings backed by canonical `tasks.id`; Turn-specific control facts remain on `workbench_turns(task_id)`. Claims, leases, stop/recovery/completion, native events and artifact reads/writes use that Task owner directly. Workbench RuntimeAttempts use the exact `workload_kind='workbench'` and canonical Turn Task ID key; no logical Run or UUID execution alias is created. The retired confirmation workflow and `waiting_confirmation` status are absent from the current Turn contract; no production Turn uses that status.

### Workbench 启动恢复竞争

审计 Web 启动时会执行 Workbench 恢复。若恰好与 worker 的 SQLite 写事务重叠，恢复会对短暂
`locked` 或 `busy` 进行有限次数重试；非锁异常或超过上限的锁仍按服务错误处理。这样一次并发
写入不会使审计页直接启动失败，也不会吞掉持续的数据库异常。

### 外部动作结果与重试

自动重试共享上限为 3 次 retry（含首次调用最多 4 次尝试），由
`app.external_retry.MAX_AUTOMATIC_RETRIES` 和
`MAX_AUTOMATIC_ATTEMPTS` 定义。DWS 瞬时调用、同路由容量重试、浏览器瞬时失败、
普通任务和系统执行都引用该共享上限，不再各自维护相同数字。持久化 Consumer 与 Audit 失败预算分角色记录，
每个角色最多四次失败尝试；权限申请后的材料重读采用独立节奏预算，最多三次、
每 12 小时一次、原申请起 72 小时截止。只有可安全重试的技术失败进入自动预算；
非重试错误直接终止，任何外部效果不确定时先对账原对象，不因次数预算自动重放。
这些是彼此独立的预算，不合并相加；等待时间、Agent 内部模型轮数和只读轮询不是自动重试次数。

邮件退订在执行标记建立前读取并确认原邮件身份；该阶段经共享外部依赖判定器识别的 provider/source 读取故障统一记录为 `email_unsubscribe_external_dependency_unavailable`，并使用同一有界指数退避预算，不建立执行标记或触发退订。读取/身份/策略错误不重试。执行标记建立后的动作继续使用原回执与效果围栏规则；退订失败仍留在 History，不进入管理者 Attention。

OA 权限预算耗尽后停止文件重读，由 Consumer 再运行一次提出普通 OA `return` 候选；
该轮不得再次申请权限或安排重试，但退回前必须对被阻塞文件实读一次：能读则按正常审批处理，仍读不了才提退回。候选仍经 Audit、System Execution 和节点读回，
服务不会合成或直接执行退回。明确的权限申请失败也走同一流程；无法形成有效候选时才进入
人工处理，不能把外部权限阻塞记为 `failed`。非 OA purpose 耗尽后仍可投影为
`permission_blocked` / `blocked`，在 `needs_human` 队列外等待；History 的一次人工访问检查
入口保留原预算且只适用于权限已开通后的确认读取。

权限预算属于业务对象，不属于某一次触发。同一审批在预算耗尽后收到新触发（新的 execution generation，尚无本代 Consumer run）时，若还没有已认领的 OA 退回轮次，就启动一个禁止再读取材料的 Consumer 轮次，提出普通 OA `return` 候选并继续 Audit、执行器和节点读回；该业务对象只认领一次，后续重复触发挂靠最近一次已完成的 Consumer run，记为 `permission_blocked` / `blocked`，避免重复退回。没有可用的 Consumer run 时进入人工处理，不记为权限 `failed`。

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

证据不完整时保留 uncertain，恢复不重发退回。

`run_execution_progress` 以 Consumer Run ID 保存租约、状态、结果及 approved Audit Run 和 Selection 所有权；对外既有 execution DTO 的 `id` 仍返回该 Consumer Run ID，Review business ID 与 Audit Run ID 保持分开。每次 action attempt 在 `action_receipts.execution_run_id` 关联到同一 Consumer Run，并以 `action_index` 及 provider receipt 记录派发与验证。`candidate_execution_alias` 和旧 `execution_id` receipt 形状只供明确的迁移与 source-inverse oracle 使用，运行时 claim、renew、reclaim、finish、attempt 和 receipt 路径直接读写 native 表。进程中断或超时没有回执不能证明未发生效果：先读取原外部对象。确认成功继续，确认没有效果才可重试同一计划，无法消除的歧义保存 uncertain/failed，不伪造完成或人工问题。技术恢复不重新审核不变业务方案；业务事实改变则使候选失效，重新形成并审核方案。历史 provider refusal 按每个 Task 当前 `current_input_id` 的 business key 匹配。没有通用 shell 或 Agent 执行兜底；未支持的动作明确失败。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

需关注中的新审核问题说明缺少的可复用规则、适用任务类别和判断依据，并提供 2–4 个 `task_class` 选项；不得要求 Derek 决定当前具体业务动作。历史问题、原状态与已存选项保留，但不因此重新激活或执行；隐藏旧选项不表示已重新审核或业务完成。

组织架构审批中，空白或“业务调整”等笼统依据属于可向申请人或已核实责任人补齐的事实缺口：先核实接收方，再询问具体业务需要、受影响工作与人员、接收安排和汇报关系；保持待办并独立核验证据。不得仅因 Agent 未查到证据就升级 Derek；`needs_human` 留给真实政策/权限缺口或明确需要 Derek 作出的业务取舍。不得凭未核实陈述直接批准或拒绝。

正式 Consumer/Audit 契约发布使用 `python -m app.deploy --publish-consumer-system-contracts`，在服务空闲且停止、数据库备份已完成后，先校验并将恢复快照交给 updater，再开始替换 `EXPECTED_SOURCES` 清单所列契约文件、`MANAGED_NAMES` 所列 managed Skill 修订和受影响任务的完整引用。发布清单的 `old_sha256` 必须对应部署前实际安装文件，`new_sha256` 必须对应仓库源文件；人工维护的 Skill 引用必须先同步到已确认的安装内容，并对两个摘要分别读回。`old_sha256` 与 `new_sha256` 可以相同：例如安装文件已被提前同步到候选内容，但运行时 managed revision 尚未发布；此时 `old_sha256` 仍记录磁盘上的实际内容，不能为了制造差异而填写已过时的哈希。任一摘要过时或源文件与安装文件不一致时，发布前置校验应停止，修复清单或来源后再发布，不能覆盖用户的运行时 Skill。发布中途失败也由 updater 持有该快照恢复；恢复失败时保留回执及原文件副本，记录 `needs_manual`，不回滚 Git 后启动可能与外部文件或引用不一致的旧服务。恢复成功后才启动旧版本；健康通过后仍核对 active 配置、全部启用绑定与新 worker 的加载回执/PID、清单所列文件 SHA 和完整任务引用，最后标记 verified。若启动时的任务版本对账基于本次发布配置创建了后代配置，只接受其仍沿父链追溯到本次发布，且启用 Skill 摘要、存活 worker 的完整加载回执、清单所列文件 SHA 和任务引用逐项匹配时通过核验。代码上线、资产发布和实际业务结果分别验收。

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

Consumer 新会话的 Developer 只给出通用 Skill 使用指令；当前 Task 中的目录携带已安装业务
Skill 的名称、读取入口和至少一次业务 Skill 读取的协议前置条件。目录只声明可用能力，不替 Agent
选择领域；选择仍由 A 根据完整上下文完成。Codex 续会话沿用原生会话开场保存的 Developer，而本轮 Task 指令重新提交。Consumer 仅在已选 Skill 来源、当前 Developer 指令合同及原生会话保存的 Pydantic wire schema 均兼容时续用该路线 session；任一不匹配或无法核实时，在该路线开启带完整当前输入的新 session，避免旧 Developer 指令覆盖本轮规则。目录展示变化不重置兼容会话；Audit 会话不受此 Consumer 判断影响。

Service 也不读取正文后替 Agent 解释业务材料。它只传递 trigger、上下文、原始 process/task ID、
链接、本地受控材料引用和可执行的精确读取命令。文档、文件夹、图片、表格、日历、听记和 OA
材料是否相关、是否需要继续展开以及它们支持什么结论，都由 A 判断；B 在审核时独立复核。

Skill 加载属于 Agent 执行环境，service 不要求或校验普通 Consumer/Audit 结果中的 Skill receipt。
仅当发生外部写入时，SQLite 保留 provider 返回的操作标识，用于重试时识别已完成动作并避免重复写入。
SQLite 继续保存既有 task/run/attempt/provider result identifier 状态；系统**不建立平行的 Skill 审计数据库**，详细工具轨迹
仍以 Codex session JSONL 为准。

### 动态 Skill 分层

CEO 业务 Skill（下表为 Consumer 按需发现的主要几个，完整清单见 `data/config/skill-features.json`）按任务动态加载：

| 业务 Skill | 负责的业务判断 | 常见操作 Skill |
| --- | --- | --- |
| `ceo-message-triage` | 回复、反应、追问、无需动作 | `dingtalk-chat` |
| `ceo-calendar-invite` | 日程邀请是否接受、拒绝或追问 | `dingtalk-calendar` |
| `ceo-document-review` | 文档、文件、图片、表格的审阅路径 | `dingtalk-doc`、`dingtalk-drive`、Lark 文档 Skill |
| `ceo-meeting-work` | 听记、静默会、会议总结与行动项 | `dingtalk-minutes`、`dingtalk-chat` |
| `ceo-mail-review` | 完整邮件线程审阅和回复 | `dingtalk-mail` |
| `ceo-personnel-communication` | 人事信息的受众、可见性和最小披露 | 候选人/通讯录操作 Skill |
| `ceo-work-tracking` | Task Agent 从来源提取 Task、证据化归属/承诺、关联正式 Project 与关注事项；返回结构化判断，不回复来源 | Task Agent 不直接写外部 TODO；合格 Task 经 Task 7 outbox 镜像 |
| `ceo-sales-weekly-report` | 按需核对销售目标、CRM 实际、公司及业务线进度评分并生成 workspace 周报 | `ceo-weekly-report`、`fxiaoke-crm-cli` |

`ceo-sales-weekly-report` 没有独立 producer 或功能开关。它由 Consumer 根据明确的销售周报请求动态选择，直接使用安装用户已有的 `sharecrm` 登录态；CRM 只读限制由 Skill 和 Codex automatic review 约束，不表示 service 建立了 `sharecrm` 命令白名单。

`ceo-work-tracking` 的发现描述与正文均限定 Task Agent 的结构化读取职责。Consumer
审阅项目文档并回复来源时使用 `ceo-document-review`，一般消息判断使用
`ceo-message-triage`；文档包含项目事实不改变调用角色。这是现有 Skill 职责的说明，
不增加正文关键词路由或改变工具权限。CI 资产的描述修订不等于已安装运行资产已发布。

### 项目为中心的工作跟踪（已发布；业务验收部分完成）

验收状态见 `docs/archive/records/task-project-centered-validation.md`。

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
Project、Task 和 Attention 详情先返回已保存的业务事实和来源引用，不同步重读每条来源原文；Project／Attention 的 `evidence_signals` 和 Task 的 `evidence[].signal` 仅含现有来源坐标和元数据，不宣称原文可用或不可用。Task 保留证据角色、日期与事件，Attention 保留原始引文、assessment、事件和关联任务；三类页面共用单条原文读取组件。用户选择来源后，经既有 Signal 详情接口读取该条原文，保留精确版本核验和不可用原因；不新增正文缓存、持久副本或后台预取。
Task、Attention、Project、历史 Project、History Task／Attempt、Meeting、OA、服务错误和原生会话详情的页面状态均按当前实体身份隔离；Attempt 执行子页还区分 role，通用资源详情包含 endpoint／kind。切换身份会在该次渲染中卸载旧事实、快照、输入、忙碌状态、操作消息和按需原文状态；旧页的初始详情请求及按需原文读取会被取消，其读取回调检查取消状态。已发出的业务操作仍归原实体，其迟到回调不能写入新详情；同一 Task 的操作后刷新继续保留已展示事实。
Project 详情 API 的当前分工只在 `context.responsibilities` 中返回；历史分工从各个 `context_revisions[].context.responsibilities` 读取，context 为 null 时页面显示待明确。
Agent 得到新事实时仍须返回完整的当前 ProjectContext 快照，带回先前有效职责、事实和出处；
不能因本轮只讨论一项新风险，就把未变化资料从快照中丢掉。
仅描述“某人负责某业务领域”的职责陈述只进入 ProjectContext，不单独生成 Task/candidate；
Task 需要来源明确的、可独立完成的交付物或行动，或由已证实重大风险引出的独立建议下一步。
正常项目序列中的例行里程碑和下一步即使写成行动句，也只记为 Project fact，不生成 Task/candidate。
总体负责人归属冲突时 overall_owner 留空，竞争主张与未确认移交记为有出处的事实，不能把候选总负责人改写成不同职责；ProjectContext 的事实必须明确写“总体负责人存在冲突，仍待确认”，不能只写“未形成一致确认/仍需核实”；独立负责人的真实交付职责仍保留。
会议明确某人总负责 Project 时，该人进入唯一 overall_owner 而不是一般职责；已有真实 Task 已直接处理同一风险时，用该 Task 支撑项目判断，不另建重复的监测/评估建议。
保留 existing_attention_id 时，assessment 必须引用当前 `current_project_attention.assessment_json.evidence` 中至少一条完全相同的原始证据三元组（signal_id、source_ref、source_excerpt）；新证据只能补充，不能替代卡片原始证明。
区别职责与行动时，“李四负责核实客户付款排期并反馈”是具体可执行 Task；“王五负责商务对账”若仅描述职能范围则是 ProjectContext。项目级风险的显示型建议可基于已保存职责，也可由唯一 overall_owner 承接跨职能协调，但不构成正式指派。

Console 提供两条只读的原始证据接口。`GET /api/console/tasks/projects/{project_id}/evidence`
按既有 Store 顺序分页返回 `BusinessProjectEvidence` 引用（project_id、signal_id、created_at），
不复制来源正文；第一页取最新窗口，meta 返回 total、has_more 和下一页 cursor。项目详情原有的
20 条有界来源及 context 引文 pinned 行为保持不变。`GET /api/console/tasks/signals/{signal_id}`
按精确 Signal ID 返回带明确原文可用状态的来源视图；可用时 `evidence_text` 是未经展示截断
的原文，不可用时为 null 并带原因。相同 source_ref 的不同版本按完整来源绑定区分，公开响应
不依赖旧 source_document_id。两条接口只读，不修改业务领域表；正文解析在 Store 写事务外完成。

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
即使当前消息没有写出下一步，也应推导一条具体建议。每个重大项目风险优先形成一个建议；若来源说明
一个行动是另一个行动的前置条件或上游 gate，则视为连续依赖步骤，只为最近的阻塞结果建立一个建议，
在描述中带出下游影响，不按步骤或不同职责拆成多个候选。只有来源支持可独立完成且结果不同的行动时，
才产生多个建议。仅当现有 Task 处理同一未解决风险时才抑制重复建议；已完成或无关的项目 Task 不算
该风险已被处理。这不构成正式派活。普通进展、已解决事项、单独的模糊线索不因“可能有帮助”而生成建议。
顺序依赖的例子：同一交付/回款链中的“客户确认验收材料”和“确认付款日期”不因分别对应
不同职责就拆成两个候选；只建议最近的阻塞结果，并在描述中说明下游付款影响。建议标题须写出
待确认的项目关键结果以及必要的上游 Gate；若风险涉及付款安排，即使先处理验收 Gate，标题仍须
点明付款日期/排期，不能用“更新计划”等宽泛标题替代。
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
已应用的业务投影回执仍由服务保存。 `scripts/inspect_task_attention.py` 以只读 URI 打开数据库，通过当前 Store 的 Task／Run 公共投影读取输入状态、公开身份和投影回执；原生决定读取在查询连接关闭后进行，不依赖已退休的平铺字段或保存正文。
Codex CLI 路径通过 `--output-schema` 使用 `app/schemas/task_agent_decision.schema.json`，
该文件由 `TaskAgentDecision.model_json_schema()` 生成并由测试校验一致；strict schema
要求每个对象显式返回全部属性，省略值用其契约允许的 `null` 表示。服务仍运行本地 Pydantic
语义校验，失败时最多在同一 session 修正一次；结构化输出不取代业务校验。 acceptance_polarity 与 acceptance_target_signal_id 的 transition 不变量由字段级校验报告到 `task_decisions[i].<field>`，使该修正轮能定位应调整的字段；接受动作及引用证据要求保持不变。Task 任务正文不重复附加完整 schema；Codex 使用上述原生通道，Claude 由现有运行适配器将同一份完整 schema 拼入单条消息（Friday 不具备 Task 所需能力，不执行 Task）。Skill、事实、业务规则和会话绑定保持不变。
新建、候选及 skip 决定携带 `status` 或 `business_relevance` 时，校验明确要求省略这两个初始字段；只有既有 Task 的 `update_task` 配合 `update_fields` 才能修改它们。该诊断不放宽来源、身份或业务校验。
Task 业务指导以当前 `ceo-work-tracking` Skill 为主；每轮包装对所有可执行 Task 的路线只给出该文件的绝对路径和 SHA-256，不复制完整正文。Agent 冷启动先读全文；续会话仅在相同版本全文仍处于当前上下文时复用，否则重新读取。包装保留 Skill 未完整描述的输出约束：assessment 选择器、顺序依赖建议、状态更新 transition、晋升 suggestion、owner_evidence 结构、Attention 决定成员与相同事实提案去重。动态来源、时间、visible_ranges 和当前 Project/Task 上下文保持完整。不新增读取工具、权限或路线；原先完整注入阶段的固定对照见 docs/archive/records/task-guidance-dedup-evidence.md，按需读取对照见 docs/archive/records/task-skill-on-demand-validation.md。

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
跨版本报告事项只在被引用的 Task Signal 与持久 owner-evidence source_ref 均能定位到该 Task 已保存的完整版本化 DingTalk 报告正文、引用的所有文档节点相同、每个 SHA-256 均与其 `markdown` 正文相符，且相同负责人事项行在每份引用正文中恰好出现一次时才允许身份合并；owner-evidence 可以引用同一文档的较早版本，但必须有对应 Signal 和完整正文。JSON 包装的报告正文先解包 `markdown` 字段，不能在元数据或序列化转义文本中碰巧命中；缺版本/正文/唯一行/负责人证明或彼此冲突就拒绝。Project 关联和 Task 标题相似度不是身份依据。

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
repair 中通过短连接执行；完整来源、原始错误和被拒决定始终作为修订的完整输入。只有实际运行线路续接刚生成候选的同一个 native session 时，才提交仅含错误与被拒决定的短输入；冷启动或续接其他 session 使用完整输入。Task 正文不再重复附加运行适配器已提交的完整 schema，不删除 schema 字段或断言。
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
新证据仍由显式 Signal 与已核验原文建立，不在初始化或读取时推导、补写或重新激活历史项目。
只读 inspector 输出当前 Project 决定、真实回执映射及由这些实际身份查得的最新 context/revision；
历史缺字段仍缺失，不补为 not_needed，不推测 ID，不改源库或历史 run。
评测读回独立判断的提案，
要求每项提案有对应真实卡片与实际 applied 回执；缺回执或仅写 completed 不判成功。
当前 negative assessment 也要有对应保存的回执，历史缺字段不自动升级。既有会议表外键问题另行处理，
不通过忽略外键或修改 frozen baseline 隐藏。这里没有新审批、工具白名单或风险关键词逻辑。

检索先读取正式 Project 当前资料/证据，再相关 Tasks；同来源旧身份强制保留，预算先给它们。
正式 Task 的负责人原文核验先处理全部同来源记录，再按排名填满已核验与待核验两个展示桶；两桶都满后不再读取不会展示的后续 Task 来源。同来源超额记录、已选 Task 的证据及来源失败语义不变。
零 Task 项目仍可读 context、当前职责/事实证明与有界近期来源，context revision limit=1，
证据有界并固定保留引用 ID。current_project_attention 保留真实卡成员和原 assessment 原文。
Task 语义上下文用无损紧凑 JSON 序列化，省去排版缩进；字段、顺序、原文字符串、引用偏移和检索选择保持不变。
source_documents 按完整绑定身份键提供精确版本的共享正文一次，每份 document 带 `doc` 序号和其 Signal 共有的来源字段（source_type/source_ref/source_time、会话、作者）及 `document_id` 一次；source_signals 只保留 Signal id、`doc`、`context`（指向 `signal_contexts` 里的 context_json 文本，相同文本只写一次）、`created_at`，某个共有字段取值与其 document 不同时才在该 Signal 上重复写出。Agent 引用仍是 Signal id 加该 document 的 source_ref，服务校验引文不变；数据库内部 dedupe_key 不进入 Agent 输入，持久去重键及其查询行为不变。
同一轮 Task semantic context 中完全相同的 `visible_ranges` 与 `decoded_excerpts` 只序列化一次，其余文档用 `duplicate_of_document_id` 指向首份投影；文档身份、元数据、信号和引用保持独立，展开后与原始 source bundle 完全一致。若压缩后的完整 bundle 不更短，则保留原始表示。
Task 输入里，当前 WorkItem 的正文就是 Work Item JSON 里的 `summary`，只出现一次；语义上下文不再含
`current_work_item`，也没有 `source_metrics`。来源绑定带外部定位信息（provider 或 native 定位，`source_ref`
本身就写明听记 ID 或钉钉文档节点）的历史来源不再复制进输入：其文档只有 `document_id`、
`source_in_input: false`、`body_utf8_bytes` 和已保存的引文 `saved_quotations`，Agent 自己用 dws 读取原文，
Task 运行可见 `dingtalk-minutes`、`dingtalk-doc` 这两个 Skill，并可用 ai_services 的 `transcribe_audio`（听记转写
不可用时的提示里说明）。服务只在决策引用某条来源时才读取其精确绑定版本来核对引文（`PreparedSources` 惰性读取），
不再为拼提示而读取每条历史 Signal。没有定位信息的来源（Console 动作记录）无处可读，保持原有投影：
2048 字符预算内保留首尾及关键引文，`visible_ranges`/`full_length`/`truncated` 和引用超预算明确显示，JSON 解码引用有实际 leaf 路径/偏移。
Task 要求路线具备 `local_process_execution` 能力（Codex CLI 与 Claude CLI 具备，Friday Runtime 不具备，因此不再被选去执行 Task），
调用方不按路线分支。回复任务（reply task）的上下文拼接不变。检索排名不证明身份、分工或业务重大性。

Task Agent 共享 task-agent:work-tracking:v1 逻辑 session，各 route 保留各自原生 session，
native CLI 自行 compact；每个 WorkItem 仍独立 workload/run。process-work-items 领取前持有 SQLite
共享会话锁，运行时续租并在提交前核验；失锁本轮不提交，普通既有重试策略不变。
旧 task:<run_id> session 历史保留。prompt 指向当前选定 Skill 根目录中的 ceo-work-tracking 文件及其 SHA-256，不固定历史 revision；旧 scheduled
skill_protocol 不注入，scheduled prompt 仅保留专项业务范围，旧 payload/history 不改。只读工具行为由 prompt 引导，不是技术上禁用 CLI/MCP 写入的权限边界。

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

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。所有 Consumer 入口共用同一条行为指令：不直接向外发送消息或邮件，也不直接执行审批；返回结构化决定或候选，由服务处理外部效果。来源或任务明确要求只要草稿或由用户发送时，Audit 退回或拒绝拟发送消息、邮件的候选。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。该指令不构成工具或凭据的强制权限隔离。

Consumer 可以直接使用普通工作工具，写入并读回本任务本代次的材料，或通过既有绑定的
通用 scheduled 文档接口操作明确目标的文档。普通写入不因为有副作用而被一律改成待审核系统动作；其事实证据是
真实工具结果与读回。文档写入与核验分别表达：provider 已确认执行的写入返回 `ok=true/status=success`，即使后续目标解析、读回或核验失败；此时保留 `verified=false`、原始错误与已有目标，不生成成功读回。未执行或提交状态未知仍返回原错误。`consumer_artifact_write` 将本任务材料写入
`/tmp/ceo-agent-service-task-artifacts/<task_id>/<generation>`，`read_task_artifact` 与
`list_task_artifacts` 为两角色提供当前代的只读材料；Audit 没有对应写入工具。
清单只列文件名并流式计算 SHA-256；单个文件超过正文读取上限不影响清单，正文读取仍受原有上限约束。
任务在 `pending` 或 `processing` 时保留文件供恢复和审核。定时任务进入可重试的 `failed` 后保留
全部代次工件；同一 Task 通过正式入口重跑时，Consumer 将前代文件复制到新执行代，当前代写入围栏不变，
同名新材料优先且旧代原件保留。任务进入 `done`、`needs_human` 或 `skipped` 后，服务清理该任务的全部代次目录。
其他渠道的 `failed` 仍按原规则清理。部署前遗留的 workspace 工件会在任务下次读写时迁入
`/tmp`，终态清理也会清除尚未访问的旧位置。Consumer 与 Audit 的运行指令同时明确 memory 是耐久
参考区、临时工件只能使用 task-bound `/tmp` 工作区。系统已注册且需要审核的
动作仍必须完整提案 → Audit → SystemExecutor → 真实回执。Consumer 解析器不扫描整个候选的
自然语言完成短语，也不把摘要转换成执行事实。是否引用历史完成、是否还需要业务动作，由
完整候选、来源及实际回执判断；风险拒绝、授权缺失与历史拒绝不可重放的机制保留。

上述 AgentOrchestrator 候选仍走完整的 Consumer → Audit → SystemExecutor。已授权微信会话的回复任务走同一 Consumer → Audit → SystemExecutor（Derek 2026-10-25：统一逻辑，不再有独立的微信决策和审核路径）。Consumer 以 `plan` 提议唯一一个目标固定为触发会话的 `wechat / send_message`，服务在送审前把正文预备成最终正文，Audit 读取同一候选的精确目标和正文；通过后 `WechatSendMessageHandler` 创建绑定该审核的 delivery，由现有 Sender 发送并持久化回执。自动发送、人工确认和重试都在同一个数据库领取入口校验标准审核证据及未变的目标/正文；审核未通过时不创建 delivery。
微信 Consumer 与 Audit 的工具边界与其他渠道相同（普通 Consumer 的角色工具列表没有发消息、发邮件或 OA 执行入口）。审核通过后仍遵循 Sender 原有自动或人工确认模式。

候选分为普通动作计划和任务类别规则问题。`needs_human` 只能针对可复用规则缺口，包含来源上下文、具体原因、证据及 2–4 个互斥规则选项，每项明确 `applies_to=task_class` 并说明后果；选项绑定在所选规则下继续当前事项的完整方案，停止选项写明 skipped 和原因。不能用 `requested_input` 将当前事实、偏好或业务决定转给 Derek；应由 Agent 先基于现有规则形成安全、有限承诺的普通回复，或向有该事实的来源人提出一个具体问题。不能混合立即执行的动作与尚未选择的条件分支。

对于他人提出的删除、撤回、移除或隐藏内容请求，请求本身不构成授权、正确性或紧急性的证据。Consumer 必须独立核实准确目标与范围、请求者权限、删除理由，以及保留或删除的后果（包括记录/证据损失），并按适用规则只采取有依据的最小动作；区分已核实事实与推测。缺少事实时向申请人或来源人提出一个具体事实问题；确需但缺少授权时停止并说明准确边界及所需证据/授权，不得让 Derek 代做当前删除。缺少删除能力属于执行失败，不能伪装成规则缺口或 `needs_human`。

Consumer 新输出的业务结果与 wire JSON Schema 和解析器一致：`needs_human` 必须有 2–4 个 `applies_to=task_class` 的选项且 `requested_input=null`；`needs_human_reason` 必须是一句中文大白话，写清要决定什么及原因，最多100字，详细证据写在 `decision_basis`；`proposal`、`no_action`、`failed` 不得包含人工问题字段。普通方案的事实证据写在 `proposal.sourced_facts`，无需动作的依据写在 `summary`。历史持久化的 `requested_input` 仅供只读兼容，不得因其可解析而重新激活人工待办。

启动恢复也按新结果合同检查当前人工决策投影。若当前、尚未选择的审核通过候选仍是旧式输入问题或不满足任务类别规则选项，正式对账将当前 Attempt/Task 投影收口为 `failed`，并保留原始 Consumer、Audit、候选及审核记录供 History 追溯；不会发送消息、选择业务分支或把它改成成功。随后必须通过普通 Consumer/Audit 流程重新形成有边界的回复，缺失事实应问向能提供该事实的来源人。

新增 Consumer 输出在 Wire 解析入口校验 `dingtalk-chat` 的消息标识字段（conversation_id、message_id、source_message_id、open_dingtalk_id、user_id、recipient_open_dingtalk_id、sender_open_dingtalk_id、verified_participant_open_dingtalk_id）：提供的非 null 值必须是含非空白字符的字符串；不把数字转换成字符串。普通 proposal 和人工选项中的可执行 plan 均适用，输出 JSON Schema 同步声明该约束。历史保存结果的 Pydantic 域模型读取保持原样；这不表示旧数字标识仍符合新的输出 Schema，也不改变历史执行或恢复行为。其他 capability 的 target 继续支持通用 JSON 值。

Audit wire 只返回审阅结论和反馈，不要求模型复述 `candidate_digest` 或 `proposal_revision`。解析器从本轮已核验的 `AuditTurnContext` 注入这两个绑定值，再用同一个上下文核对候选字段断言；严格 wire schema 拒绝模型额外输出绑定字段。恢复已完成的 Audit 原生结果也使用同一候选绑定解析器。持久化的 Audit 域结果仍带摘要和版本号，并保留原有完整内容与敏感值校验。

Audit 返回 approve、return、reject，必须绑定 candidate_digest 和 proposal_revision；failed 只表示技术失败。Audit 不修改正文、选项，不返回执行回执，也不自行创作另一个人工问题。return 允许保留正文并补足证据；reject 要求实质改变被驳回内容，不能只改描述、元数据。首次提交最多十次内容重提（被打回是正常业务流程），耗尽为 failed，技术失败不占内容预算。

Audit 当前任务除完整候选外，还收到服务从同一候选 JSON 确定性生成的标量字段证据：JSON Pointer 路径、精确字面值和 JSON 类型。字段类型反馈必须和该证据及候选相符；此索引不增加来源事实、改写候选或改变审核决策规则。

规则问题也先经过 Audit。Audit 检查：适用规则是否确有缺口、规则/代码/Skill/记忆/会话/读取是否可自行解决、当前事实是否应由来源人补充、是否把技术失败或当前实例决定伪装成规则选择、上下文和理由是否充分、2–4 个规则选项是否互斥可行且确有差异、每个执行分支是否完整并有依据。仅当前版本 approve 后才进入 `needs_human` 并通知；审核通过不等于业务 done。

选择接口提交 candidate_id、review_id 和 option_key，服务从该不可变候选中读取唯一匹配的选项；选择记录只保留引用和选项键，不复制分支正文。首次选择独立记录并将原 execution_generation 唤醒；既有 generation、review、失效及最新候选校验通过后，相同选择幂等，冲突选择拒绝。较新候选不会改变历史选择绑定，但旧候选的再次选择仍按原校验拒绝。有效分支直接由系统执行，不重新启动 Consumer/Audit。补充文字保留为新输入，使旧候选失效并进入新完整候选和审核；历史选择、问题和回执保留。选择事件不等于执行完成。反馈可以同时标记一次性应用与 Skill 更新；只有明确获选的 Skill 更新才进入受控 Skill 发布流程。

旧长期规则问题仅通过显式单项命令 `python -m app.rule_question_retirement --attempt-id <id> --authority <confirmed-contract>` 退役，默认只读预览；应用另加 `--apply --verified-backup <path>`。资格限定为当前业务对象、当前执行代、最新 Attempt 所绑定的已完成 Consumer、done 任务、完整无技术错误的 task_class 问题，且该任务没有新候选；不批量排除旧结果。应用只原子写入 resolved_at、resolution 与包含原结果 SHA256/任务/运行/执行代/契约依据的退役回执，保留原 send_status、模型结果、任务状态和外部回执。详情、History 和队列将该已退役问题显示为 skipped 并解释依据，重试 POST 拒绝。它不是人工选择、Audit 通过或业务完成；未退役和当前无效问题继续由原质量检查报错。预览及应用均不初始化或迁移数据库。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，`stage_index`、`predecessor_review_id` 绑定前一已完成阶段的回执；阶段上限独立于每阶段最多十次内容反馈周期（`MAX_CONTENT_FEEDBACK_CYCLES`）。Consumer wire 仍包含这两个字段，但服务在解析时以编排器提供的阶段上下文覆盖模型回传值，再持久化和绑定候选。Audit 返回后的修订因此保持当前阶段及原前置审核；该同阶段修订由编排器从上一份候选的 adopted metadata 恢复阶段和前置审核，immediate parent Audit 则审核上一份同阶段候选。仅前一阶段 Audit 批准且该阶段动作已核验完成后，编排器才提供下一阶段绑定。候选存储按现有契约验证非空的前置 Audit Run ID 是否对应同一任务/代际、前一阶段、批准结论及已完成动作回执；迁移保留的历史 JSON 和摘要保持原样，关系以 adopted result 的 Audit Run 外键为准。

Audit 返回或拒绝后，Consumer 必须提交当前阶段的完整修订候选。服务以当前编排上下文绑定 `stage_index` 与 `predecessor_review_id`，不把模型复述这两个 lineage 字段的准确性作为候选业务结果的前置条件。返回反馈不会推进阶段；仅当前阶段审核通过且其动作有已验证的完成回执，后续阶段才会开放。候选的阶段、前置审核、代际及执行验证仍由服务严格校验。

### Consumer 权限模型：auto 模式，不是只读／工作区写沙箱（Derek 2026-10-24）

Derek 的设计决定：Consumer 运行在各 CLI 的原生 auto 模式里（Codex 的 `auto_review` 审批、Claude 的 auto permission mode），能力由 CLI 自带的自动审批逐动作把关，**不用只读或只写工作区的沙箱来限制它能做什么**。理由：沙箱是静态的，会挡住合法的本机工具。2026-10-24 的实例：`sharecrm` 的登录凭据放在 macOS 钥匙串，沙箱里 `SecItemCopyMatching failed -50`，CEO 周报于是读不到 CRM；这与「本机的所有环境都可以给 Agent 使用」相矛盾。

不变的边界与沙箱无关：终态外发动作（发消息、发邮件）仍只走 proposal → Audit → SystemExecutor；Audit 仍是只读；Consumer 的禁用工具清单（Settings → MCP 的 `disabled_servers`、桌面控制类 server）仍然有效。

**当前实现（Derek 2026-10-24 批准切换，已按两个 CLI 实测）：**
- Codex Consumer 保留 `workspace-write` 沙箱，但审批改为 CLI 原生 auto：`approval_policy="on-failure"`（Codex 实际按 on-request 运行）加 `approvals_reviewer="auto_review"`，由 turn 构造方设置，`make_role_agent_command` 对 Consumer 不再覆盖成 `never`。命令在沙箱里失败后，Agent 以 `sandbox_permissions="require_escalated"` 加理由重新请求，自动审查（`codex-auto-review` 模型）放行后在沙箱外执行。Audit 仍是 `read-only` 加 `never`。
- Claude Consumer 的 shell 沙箱把 `allowUnsandboxedCommands` 设为 `true`，权限模式本来就是 `auto`；沙箱内失败的命令可由 Agent 以 `dangerouslyDisableSandbox` 重试，由 auto 模式的分类器逐次决定。
- Agent 不会自己发现要提权：两个 CLI 都只在提示里明说「失败就提权重试」才这样做，所以需要钥匙串的工具必须由对应 Skill 写明运行方式。
- 路由设置 `automatic_review` 声明该路由的 provider 是否提供 `codex-auto-review` 模型；未设置时按凭据模式取默认（本机登录路由为是，`service_api` 路由为否），自己加的 `codex_api` 路由可用 `CEO_RUNTIME_<NAME>_AUTOMATIC_REVIEW=true|false` 改。为否的路由由 `disable_automatic_review` 把 `approval_policy` 设为 `never`：该路由上的 Consumer 仍全自动运行，不申请提权，沙箱限制照旧。需要沙箱外权限的任务不应路由到该路由，这是该路由已声明的能力差距，不在本次变更里绕过。
- 不变：终态外发动作仍走 proposal → Audit → SystemExecutor，Audit 只读，Consumer 禁用工具清单有效；`dws` 仍用沙箱内的 `~/.dws` 和网络设置。

### 微信发送放行：只认标准审核（Derek 2026-10-24 方案 G2；原微信审核于 2026-10-25 退休）

微信投递只有在独立审核批准了「完全相同的目标和文本」之后才能被领取发送。放行判定只认一种证据：投递证据里的 `standard_candidate_id`、`standard_review_id`、`standard_action_identity`，指向被采纳的候选、它的 Audit 批准和候选里的 `wechat / send_message` 动作。

投递状态回写 Attempt 只认任务当前执行代（Derek 2026-10-28）：旧代的失败投递被新代已发送投递标为 `superseded` 时不再回写同一个 Attempt，避免把刚发送成功的状态覆盖成 `skipped`。Reader 对本机微信显示不了的 appmsg 输出 `[无法显示的内容…]` 占位并归为 `unknown`，不再伪装成 `[链接]` 分享、不生成回复任务（细节见 runtime-mechanism）。

Reader 与服务同版本（Derek 2026-10-28）：Reader 源文件摘要是双方共享的版本号，打包时写进 app，每条 IPC 回复带上，服务每次调用核对，不一致即拒绝并要求重建 Reader（细节见 runtime-mechanism）。

控制台命令记录来源（Derek 2026-10-28）：被接受的 Attempt 重新处理/重试/再次发送各写一条带 actor、客户端和 User-Agent 的记录，详情页按行显示；读回收口的任务在详情页说明「已通过读回确认发送」而不是「执行失败」（细节见 runtime-mechanism）。

失败微信 Attempt 的 History「重试」API 先只读对账，不再次发送：当前已审核候选、审核、执行代、动作正文和 Delivery 证据必须完全绑定。`sent` 只有在微信 Reader 从精确账号、会话和发送时间窗口读回唯一匹配的本人消息后，才能把失败 Task 结算为 `done`；`superseded` 只有在其明确 successor 链最终到达已发送且精确读回的 Delivery、旧正文在完整有限读回窗口内不存在时才能结算为 `skipped`。读回缺失、重复或窗口不完整均保留失败并返回冲突。原失败 Attempt、Run 和动作记录不改写；该 API 不创建 Sender，也不重放消息。

内部处理失败的正式 API 是 `POST /api/console/history/{attempt_id}/rerun`：它把同一业务输入送回原任务流程，由原审核和投递门禁重新处理，不需要通过聊天确认卡回复。`/retry` 仅用于上段所述的外部动作读回对账，不能替代或触发重跑。

标准证据按失败即关闭的方式逐项核对，任何一项对不上都视为未放行：审核是 `approve` 且摘要等于候选摘要、候选未失效、候选属于已完成的 Consumer 运行、Audit 运行已完成且其父运行就是该候选、两者的任务与代次与投递一致、候选是普通 `proposal`、按 `action_identity` 唯一找到该动作、动作的账号／会话类型／对象／会话 ID 与投递完全一致、动作的 `payload.text` 与投递的 `reply_text` 逐字相等。`AutoReplyStore.create_standard_wechat_delivery` 从被批准的动作本身取目标和文本（不取调用方参数），审批文本已是最终正文，因此不再追加发送后缀。

### 微信发送动作 `wechat / send_message`（阶段 2，Derek 2026-10-24）

标准流水线通过 `wechat / send_message` 动作发微信：目标是 `account_id`、`target_type`（`direct` 或 `group`）、`target_id`、`conversation_id`，参数只有 `text`，且 `text` 必须逐字等于已持久化的预备正文（与钉钉一致，渠道为 `wechat`，投递键由 `agent_message_delivery_key` 生成）。`WechatSendMessageHandler` 的 `dispatch`：确认任务是微信任务 → 核对预备正文 → 发送开关（`CEO_WECHAT_SENDER_ENABLED` 且 `CEO_WECHAT_SEND_MODE=auto`，否则直接 `failed` 且不创建投递）→ 用 `create_standard_wechat_delivery` 从被批准的动作创建投递 → 放行判定复核标准审核 → 沿用现有的当前会话取证、绑定核对和 `WechatSender.send`（传入预备消息）。后台 sender loop 在 `auto` 模式重试已审核的 `ready_to_send` 投递时，以及批准、未执行失败重试和已确认无外发回执后的重试 API，都必须复用同一代次、候选、审核、动作身份和最终正文精确匹配的已持久化预备消息。旧投递可使用 `wechat:{delivery_id}` 键，但不能新建或重构正文；证据不全或正文不匹配时，API 在重新入队前报错，且不调用发送器。结果完全取自投递状态：`sent`→verified，`sending`／`send_unknown`→uncertain（交给现有的只读对账，不从未知状态重发），其他发送前失败→failed（重试可再次发送），被取代→business_state_changed。后台 WeChat 发送等待电脑连续空闲达到 `CEO_WECHAT_SEND_IDLE_SECONDS`（默认30秒），不设发送窗口截止时间；发送 IPC 保持请求，执行器续租已审核 action claim，用户活动中断草稿后重新等待。只读目标确认保留原有限等待。sender loop 只在 `auto` 且发送开关启用时消费投递；`confirm` 模式保留显式确认流程。

Sender 的被动 readiness 检查只有在 Accessibility 树中找到发送导航实际使用的搜索框或会话列表控件时才返回 `ready`；仅有 WeChat 进程或窗口不代表已登录且可发送。窗口存在但这些控件缺失时返回 `wechat_window_unavailable`，不会尝试登录或发送。

### 任务长期记忆

Derek 2026-10-24：任务的长期记忆由执行 Agent A 自己在回合内调用 `memory_connector.memory_write`
写入，服务不再有任务记忆的队列、重试或写入器。

- 提示（`app/consumer_agent.py` 的 `_CAPABILITY_COMMON_HEAD`）只要求 A 在结束前检查本轮是否确认了
  应长期保留的信息，有则逐条 `memory_write`；不写临时任务、日志、代码、一次性错误、未确认的猜测、
  敏感原文、密钥，以及 Memory 里已有的内容（含同一任务较早修订已写的）。
- 结果协议不再包含 `durable_memories`（`ConsumerAgentWireResult` 与三份 schema 已删）；旧结果里的该字段
  读回时被丢弃，不再触发任何写入。
- Codex 路线的 Consumer 拥有 `memory_connector` 的全部工具（只有 Audit 受 `ROLE_MCP_READ_TOOLS` 限制）。
  Claude 路线的 Consumer 只连接服务的 `agent_cli`，没有 Memory 工具，所以走该路线的任务不会写长期记忆。
- `codex exec` 仍固定 `--disable hooks`：插件 Stop hook 会在结果之后再加一轮，曾使结构化结果重复。
- Actions 里的 `task_memory_write_events` 记录类型及其索引、触发器保留（历史行可读，需要迁移才能删），
  但没有任何代码再写入它；控制台 Attention 和队列不再列它。
- 会议结论的 Memory 写入同日一并删除（Derek 2026-10-24：不应有两条不同路径）：服务不再有 `meeting_memory_write` 写入器、Worker、`meeting-memory-write` 健康组件、Status 的 `meeting_memory_health` 和自己注册的 OAuth 客户端（`app/memory_connector_client.py`、`authorize-memory-connector`）。已发送的会议结论需要进 Memory 时由人工或 Agent 通过 Memory Connector 补写；历史 `meeting_memory_write_events` 行保留不动。

### Audit Agent B

Codex Consumer/Audit 显式启用原生 `features.code_mode.enabled`。普通 Consumer 与非 OA Audit 的任务工具仍为 direct-only；OA Consumer 与 OA Audit 的只读附件工具和 OCR 工具在 CodeMode 中组合，避免模型接收再复制可能被截断的 base64。OA 的一次性会话合同变化会建立新 session，此后继续复用同一对话对象的兼容 session；其他任务的会话合同不变。OA Audit 的 CodeMode 排除 functions、image_gen、multi_agent_v1、web 内建命名空间；其他 Audit 维持原 functions 排除。角色工具列表、只读沙箱、路线和外部动作规则不变。工具配置不能替代实际调用验证。

Audit 使用实际只读工具接口审核候选，没有受控发送、审批或命令执行能力。Consumer 保留任务绑定的文档和报告操作；Codex Consumer 使用原生 shell、unified execution、补丁和 V8 host 在当前 task/generation 的 `/tmp/ceo-agent-service-task-artifacts/<task_id>/<generation>` 目录执行普通本地代码。该 task-bound 目录与 durable workspace 分离，原生 workspace-write 沙箱只为当前任务打开它及原生临时目录，命令网络关闭；MCP 进程仍从服务源码目录启动。任务终态清理所有代次的本地工件，pending/processing 期间保留以支持恢复和 Audit 读回。受控发送、OA 等注册操作仍只由 System 执行。所有任务类型的 Consumer 都不得通过 shell、CLI、MCP、browser 或其他工具直接联系他人或执行影响他人的外部动作（包括消息、邮件和审批）；文档写入保持允许。Consumer 只能提交 typed candidate，由 Audit 审核，System 只执行审核通过的持久化方案。Audit 排除内建 functions namespace 并保留 read-only 沙箱。两种角色均不开放 browser、image generation、委派、自动 Skill 安装、记忆写入或未登记 MCP 操作。Claude 仍只有受限内建读取与角色 MCP 工件工具，没有 shell 代码执行；Friday 不能承担这两个角色。原生执行验证必须保留实际命令结果和文件回读，写出代码文件不等于执行代码。

## 会话与反馈周期

Consumer 继续兼容业务会话，Audit 按现有角色会话继续读取和审核。每阶段初始提交加最多十次内容重提（`MAX_CONTENT_FEEDBACK_CYCLES`）；return/reject 消耗预算，运行时和 provider 重试不消耗；消息送达不确定时再让 Consumer 确认的次数另有 `MAX_UNCERTAIN_DELIVERY_REVISIONS`，仍是三次。原始 run、修订和错误 append-only，任务 processing 在反馈期间保持不变。

### 任务提取与 follow-up 是一个生命周期

`ceo-work-tracking` 把同一事项从识别到关闭作为一个流程：从对话、会议或材料中提取有证据的
Task，按需链接正式 Project，记录负责人及类型化日期。只有符合承诺与期限条件的 Task 才通过
outbox 镜像为钉钉 TODO；只有来源明确给出下次检查时间与目标会话才创建 follow-up。follow-up
只是建议：由 Derek 在 Task 详情页点按钮发送（Derek 2026-09-25），新信息更新该 Task 时撤回未发出的
follow-up，见 `docs/runtime-mechanism.md`。没有自动发送：到期的 follow-up 不会被发出，结果未知的
发送显示为失败、由 Derek 决定是否再点。发送记录 revision、租约、幂等 ID 与 provider 回执。读取后续
回复或新完成的外部 TODO 后，仅凭明确完成证据关闭对应 Task 与其 follow-up。旧 Project/TODO/follow-up 记录保留在历史边界；没有可核验 current Task 绑定的旧外部 Todo 不会因镜像链路退休而被晋升为 Task。

## Audit Rules

Audit Rules 是 A 和 B 共享的可见业务规则：

- 默认文件：`data/prompts/audit_rules.md`
- 配置页面：`Config -> Audit Rules`
- A 使用规则自检候选。
- B 使用同一规则独立审计并决定是否执行。
- OA 的可配置 Audit 第14条只引用当前任务绑定的业务 Skill 与通用 OA Skill：业务 Skill 提供判断条件，通用 Skill 提供证据、评分、阶段和动作映射。卡片的 partial 标记或缺少单独动作列表本身不构成本单规则缺口；真实材料、制度和授权缺口仍按适用 Skill 处理。已解决的问题不因旧规则重新打开；只读及终态任务不得由该条额外产生评论、通知或审批动作。默认种子更新不覆盖已有自定义文件；本次已获明确授权的线上第14条通过现有 Audit Rules 设置保存，其余自定义条款保留。
- B 的固定角色说明（`app/audit_rules.py::AUDIT_RULE_WRAPPER`，不属于可编辑模板）要求 Audit 不重复获取 Consumer 已取得的信息，按已收集的信息判断；只有 Consumer 对该来源的结果是错误或失败，或需要更新的上下文来检查是否过时时，才再读该来源（Derek 2026-10-28，所有任务类型与渠道一致，不改变工具目录）。

Message Triage 的 Audit 按触发消息判断 Derek 角色是否有待办。对方已明确接手的后续事项即使尚未完成，也不单独构成要求 Derek 追问的理由；但 Audit 仍须退回直接要求 Derek 决策或行动的候选，并且不得把对方承诺写成已完成或已核实。

可配置内容包括表达、信息最小化、审批材料要求、特定业务风险和需要升级给 Derek 的判断。
以下边界不可配置：A 负责读取/判断并提出候选、B 负责 accepted action 的正式执行、精确 revision 去重、最多十个内容反馈周期、
外部动作标识去重以及敏感凭证不进入提示词和审计页面。

## 能力与配置

### MCP 服务器可见范围

OA 表单／评论附件分页读取在本地原件未缓存时，使用原有精确流程与文件 ID 的下载路径取得原件，随后执行同一分页渲染和来源摘要检查；不依赖该 MCP 进程事先读取过元数据。文件仍只保存在本进程临时目录，实际下载失败按技术错误返回。

OA Consumer/Audit 收到扫描页时，在当前 Native Codex session 的同一 CodeMode 调用里传递读取工具返回的字节、MIME、页码和摘要给 `ai_services.ocr_document`，仅展示 OCR 文本与来源信息；非 OA Audit 和其他角色保持原直接工具路径。嵌套目录、shell 清单或其他 Agent 的工具列表都不能单独证明本轮 OCR 可用；以本轮实际调用结果判断依赖故障。

Codex 会把 `$CODEX_HOME/config.toml` 中的全部 MCP 服务器合并进每次运行，后台 Agent turn 因此
默认能看到安装用户的个人工具。服务清单（`CEO_SERVICE_MCP_CONFIG_PATH`）除了声明服务自己的
transport，还用 `disabled_servers` 列出后台 Agent 不得使用的个人服务器；命令组装时对这些名字发出
整表 `enabled = false` 覆盖（Codex 不接受单字段覆盖，占位 transport 不会被启动）。Settings → MCP
读取 `codex mcp list --json` 展示全局服务器与清单服务器，保存即写回清单，从下一个 Agent turn 生效。

后台 Agent 使用安装用户的原生 CLI home 和已安装 skills，MCP 直接连接；不复制 OAuth header、token，也不添加本地 credential proxy。Codex 每轮按角色发出显式工具 allowlist：Consumer 的 agent_cli 普通材料读写与原生工作区代码执行可用；Audit 仅保留只读审核能力。两角色可用具名只读工具 `read_dingtalk_drive_file` 下载单个已授权钉盘节点并提取支持的图片、PDF、文本、表格或演示文稿内容；`read_dingtalk_oa_process_attachment` 按当前审批实例和表单附件 ID 经 DWS 的 OA 附件下载地址读取正文，不要求通用钉盘节点权限；`read_dingtalk_oa_comment_attachment` 按流程实例 ID、评论附件 ID 和文件名，通过 OA Premium 评论附件接口读取精确的评论附件；`read_dingtalk_oa_comment_image` 则读取 OA 记录 `images[]` 返回的钉钉图片 URL，供没有 file ID 的评论截图进入相同 OCR 流程。图片 URL 读取仅允许 HTTPS `static.dingtalk.com/media/`，不跟随重定向、限制响应体大小和读取时长；所有下载文件仅保留在该 Agent CLI 进程的 `oa-review-*` 临时目录中。扫描图片、无文本层 PDF 和 OA 表单中含图片的 PPTX 返回精确来源的 base64、MIME、页码及 SHA-256；PPTX 图片按阅读顺序逐页读取并保留源幻灯片号。Consumer/Audit 使用原生 MCP `ai_services.ocr_document` 识别并保留页码、置信度和警告；不使用本地 OCR 脚本。超过 700 KiB OCR 输入上限的支持型栅格图片先转为有界 JPEG 输入，读取结果仍保留原始图片 SHA-256 和源尺寸；未超限图片保持原始字节。后台 Consumer/Audit 命令只对 `ai_services` 的 `health`、`ocr_document`、`transcribe_audio`、`synthesize_speech` 设置逐工具自动授权，以便角色在既有工具范围内直接调用已配置服务；全局审批策略不变，其他 MCP 工具不增加自动授权。`ai_services` 通过后台服务 MCP 清单按名称和 URL 连接，OAuth 沿用 Codex 原生配置，不增加 token 代理。下载权限/权益失败、输入超过工具响应上限、MCP 失败或 OCR 为空时返回技术阻塞，不能解释为申请人未提供材料。进程退出时清理临时文件。Claude 的本轮 MCP 配置只连接 agent_cli，沿用其原生 home 和 inline 配置。

Audit 对已配置 AI Services 的 OCR/转写调用，不因来源字节交给该配置服务处理这一事实额外要求人工批准；仍按现有规则审查材料敏感性、任务范围和明确的禁止处理要求，并保留真实工具错误。该规则只针对服务内部材料读取，不改变消息、邮件及其他对外动作的收件目标、内容和执行审核。

OA 表单附件的临时文件路径由下载材料层直接返回；表格抽取结果本身可以没有 `path`，角色读取器仍以该真实临时文件缓存来源并返回表格内容、原审批实例/文件 ID 和原字节 SHA-256。此路径修复不改变 OA 下载权限、钉盘拒绝结果或审核动作。

部分 OA 表单图片字段直接保存 DingTalk static media URL，而不是附件 file ID。两角色可用 `read_dingtalk_oa_form_image` 读取；工具先从该流程的实时表单值确认 URL 完全匹配，再使用评论图片相同的域名、重定向、大小、OCR 和临时文件限制。跨流程 URL 在请求网络前被拒绝。

Codex 后台角色固定 `features.plugins=false`；`codex exec` 固定带 `--disable hooks`（Derek 2026-09-24）。插件 Stop hook 曾使结构化结果重复；长期记忆由执行 Consumer 在回合内通过 Memory Connector 写入，历史 `durable_memories` 字段读取后丢弃，见「任务长期记忆」。Consumer 生成候选并按共享 Audit Rules 自检；Audit 只读独立审核；System Executor 执行已持久化审核通过的受控动作。普通工具工作不因产生材料而自动注册为系统受控动作。个人对话中的小青面试或上传没有对应后台系统任务，不设置服务审核项。

Agent 不执行 `auth login`、`reset` 或 `logout`。MCP 实际返回未授权时，任务保留认证失败，不把它解释为材料缺失。

Consumer 与 Audit 的只读 `convert_dingtalk_calendar_time` 工具使用 IANA 时区数据库按日期换算本地墙钟时间，返回源区偏移、UTC instant 和目标区本地时间。重复或不存在的本地时刻分别返回 `ambiguous` 或 `nonexistent`，不能静默选 fold 或构造时刻；一次结果已列出可复用的全部时间表示，不要为同一 instant 再反向调用。该工具只换算时间，不证明参与者空闲、会议已创建或动作已发送；只有时区名和时间得到来源支持时才使用它。

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
  添加的 Codex 路由还可选 `CEO_RUNTIME_<大写名>_REASONING_EFFORT`，支持 `low`、`medium`、
  `high`、`xhigh`；空值继续使用全局 Codex thinking 强度。Settings 同一字段保存、展示、改名和删除。
  具体提供商可能只接受其中部分取值，配置时应以该路由的实测能力为准。
- **按种类、不按名字**：依赖认证方式的规则看 `RuntimeRoute.is_cli_api_route`（凭自己 API Key
  登录的 Codex/Claude CLI 路由）。「临时失败后在同一路由换新会话重试一次」适用于所有这类路由；
  Consumer 连续两次结果不可用时强制换新会话，对所有路由一视同仁，没有例外。Consumer 的可重试 Agent 来源失败继续使用当前 conversation 的 route session；`AgentError` 的默认 `session_continuable=false`
  不代表运行时已确认会话不可用。服务来源明确标记不可续的失败仍新建会话（Derek 2026-09-25；
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
键名，由 `app/web_api/agent_runtime_settings.py` 校验并写入。读取同一接口时，凭据类字段（`*_API_KEY`、Friday 的 ticket / session token）只返回部分遮蔽值（12 位及以上保留前 3 后 4，其余 `****`；更短的整体 `****`），服务从不把完整凭据发给控制台（Derek 2026-09-25）；页面把遮蔽值原样存回，服务认出它等于已存凭据的遮蔽值，就保持已存凭据不变，输入新值才替换。

健康探测（`app/agent_runtime_probe.py`）与真实 turn 走同一条 provider 路径，因此超时按一条真实
turn 的长度给：`PROBE_TOTAL_TIMEOUT_SECONDS` 和 `PROBE_IDLE_TIMEOUT_SECONDS` 都是 300 秒，
服务读取这两个常量作为 `CEO_RUNTIME_PROBE_TIMEOUT_SECONDS` / `CEO_RUNTIME_PROBE_IDLE_TIMEOUT_SECONDS`
的默认值——两处不同的默认值曾把一条健康的路由判成不可达。`probe-agent-runtimes --route` 接受
任何已配置的路由名（含新增路由），并且**不采纳其它进程的快照**：显式探测必须给出本进程自己的
结果，否则运维看到的可能是服务几十秒前的结论。

路由执行错误会将已分类的失败类别、来源错误码和 `retryable_external_dependency` 同时写入 Runtime Attempt 与 AgentRun。能力错误按路由判定终止，容量和传输错误按路由判定重试；未分类的旧式错误保留 AgentRun 原有默认重试判断。

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
scope 的 token，但 keychain 里这份会被本机其他进程改写，线路随之再次失败。因此本机登录态只适合临时使用；需要稳定可用的 Claude 线路时
使用 `claude_api`。
复现服务所见的情况时必须用服务的最小环境运行 CLI（`env -i HOME=… PATH=… USER=… claude -p …`）：
在 Claude Code 会话里直接运行 `claude -p` 会继承桌面应用自己的凭据而成功，不能证明服务可用。

所有 workload（Agent turn、任务 Agent、会议、邮件分类、workbench、TODO 截止日期回填）都能路由到
Claude。Claude 的 developer instructions 与完整输出 schema 仍由
`app.claude_runtime_adapter.claude_input_contract` 与任务拼成同一条消息；
Claude Consumer 另通过 CLI `--json-schema` 接收内联的兼容 wire schema，原有
Pydantic 模型继续校验完整条件与业务规则。Audit 和其余 Claude workload 仍按既有
单消息 schema 路径运行，不改变工具、路由或会话；
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
Friday runtime 共用，谁先 claim 到 operation 就由谁执行，并使用它自己的 provider 配置。不要在 CLI 管理的 runtime 之外再起第二个。

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

角色同一 revision 每个 pass 最多两个技术 turn；Consumer、Audit 各自按持久化的同角色失败 run 计数，首次失败后最多再重试 3 次（4 次失败 turn 后终止），互不占用对方预算。容量、连接、来源刷新和其他可重试技术失败均计入该角色预算，并保留真实 source_code；同角色成功 turn 会重置连续失败计数。`dependency_read_unavailable` 按同一上限处理。请求需要的操作不可用时，Consumer 先判断现有材料能否支撑真实、有用的回复；能则生成给请求人的消息候选并沿用独立 Audit 与投递门禁，不能才报告 `operation_unavailable` 并按终止失败处理。服务不重复运行相同能力集合，也不把技术缺口变成人工授权请求。服务也将 Agent 报告的 `capability_unavailable` 和 `write_capability_unavailable` 归到同一不可重试类别，同时保留原始 source_code。权限申请后的等待由经审核的 purpose `retry` 动作表示。`agent_context_refresh_failed` 使用持久化 run 计数与共享退避，最长 15 分钟，也受该角色总预算限制。成功读取但无匹配数据或业务证据仍缺失不属于依赖错误。未开始执行的活动租约等待不产生失败 turn，技术预算与内容预算独立。

Derek 2026-10-25/26：缺少工具本身不是失败理由。若原操作不可执行，但现有信息足以形成真实、有用的回复，Consumer 应生成给请求人的消息候选，说明未完成操作、已有材料和请求人的下一步，并走原有独立 Audit 与投递流程；不能伪称操作成功。只有操作和有用回复都无法真实完成时才报 `operation_unavailable`（提示在 `_CAPABILITY_COMMON_TAIL`）。

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

Consumer/Audit 的完整 Application Result Contract 各在 Developer 的“应用结果合同”段注入一次。Task context 只渲染当前事实、阶段、候选与反馈，继续保留显式自定义 Audit Rules；不再次复制通用结果合同。Audit 的旧 Role Boundary 与应用结果合同完全相同，合并为同一段。输出 wire schema、业务合同内容、会话绑定与路由不变；Settings 使用同一装配函数显示该来源与顺序，历史输入按原保存内容展示。

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
| `app.audit_agent.AuditAgentRunner` | 在同一任务代次的修订轮次间续用 Audit 自己的兼容 B session；与 Consumer session 隔离，只读审核每轮完整候选并返回绑定 digest/revision 的结论；不执行动作或返回回执。 |
| `app.agent_contracts` | 严格定义 A proposal 与 B audit result。 |
| `app.audit_rules` | 保存、校验并分别渲染共享 Audit Rules。 |
| `app.codex_runner.CodexRunner` | 以原生 `codex exec` 启动并继承安装用户的 Codex 配置。 |
| `app.friday_runtime_adapter.FridayRuntimeAdapter` | 通过 Friday Thread/turn/operation/Artifact HTTP 契约执行一个 Agent turn；不实现 provider 选择。 |
| `app.friday_runtime_contract.FridayRuntimeContract` | 定义 Friday Runtime 请求、认证头、operation 状态和最终 Artifact 的稳定契约。 |
| `app.channel_gate` / `app.mcp_doctor` | 在运行前检查 CLI 与 MCP 依赖。 |
| `app.store.AutoReplyStore` | 保存队列、run 关系、租约、revision 和最小恢复状态。 |
| `app.audit_web` | History、Agent session、Audit Rules、配置和恢复入口。 |

Worker 下载消息图片时，`content` 中的媒体资源归当前消息所有，`quoted_content` 中的媒体资源归
`quoted_message_id` 指向的原消息所有。资源下载和任务内缓存使用资源来源消息 ID；失败诊断明确记录
该来源 ID，而错误行仍绑定触发消息 ID 以便与原任务恢复。引用内容缺少来源 ID 时保留技术错误，不把
回复消息 ID 冒充资源所属消息。

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

Hourly Quality 对 `processing` Reply 的停滞判断同时读取 Task 与当前执行代的 Agent Run/RuntimeAttempt：Task 时间戳超出恢复阈值，但同代 Consumer/Audit Run 或其活动 RuntimeAttempt 仍有近期更新时，任务仍在推进，不报 `processing_stale`。没有近期更新的活动同代 Run/RuntimeAttempt、缺少活动执行记录，或只有旧执行代/终态 Run 时，仍报告停滞。此检查只读，不刷新租约或改写任务状态。

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

申请人说明某项必需事实“尚未形成”或“正在等待技术讨论”，但申请人仍可协调责任来源方补齐时，
这仍是材料补正阶段：正式退回当前申请，要求准备好后重新提交；不要把“现在暂时拿不到”转成
Derek 选择是否带着缺口继续。不要重复发送同一份宽泛清单；通过退回明确本轮先暂停补正，之后
根据新材料重新审阅。只有补正阶段完成后，仍存在独立的公司规则、例外或授权缺口，才进入
`needs_human`；已证实不存在申请人/责任来源方可行补齐路径也不能单独构成规则问题。

已提交材料因审批服务的权限、下载、解析、OCR 或客户端能力而读不到，不等同于申请人缺少材料：
按通用 OA Skill 记录审阅端技术阻塞并保留待办，不因该失败要求申请人重复提交同一文件，也不将
由此产生的错误退回计入重复退回拒绝条件。只有独立证据确认源文件本身缺失、空白、损坏或缺页时，
才按申请人可补正的材料缺口处理。财务审批按附件逐项记录文件名或节点 ID、授权读取结果、可读文本/页数或 OCR 结果及已核实事实；一个附件的读取失败不能推及其他附件，所有文件均不可读的结论须有逐文件失败证据。已读取的其他证据仍须单独评估。

供应商付款所需的客户验收/结算确认属于第三方事实。审阅方未取得客户邮件正文时，不能据此推定申请人未提交该材料；只有独立证据证明申请材料确实缺少该项且申请人能够补正，才可向申请人提出具体追问。否则保持待处理；已终态流程只作 `no_action` 历史分析。

重试复用同一个正式任务和审批实例，不创建替代审批事项。瞬态故障进入 exponential
backoff；终态失败必须说明根因、已尝试动作、provider 标识（若有）、下一步和重试条件。
DWS/OA 技术错误不得直接暴露给申请人，所有“已评论”“已通知”“已完成”都必须由 Agent
根据 provider 返回结果确认；应用层不额外要求发送 read-back 证据。

Codex Agent 可以通过 `agent_cli.read_skill` 读取 Skill；Skill 读取属于 Agent 执行环境，
不是应用层业务结果的前置 receipt。launchd 业务服务不是 Skill 可读性的前置条件。

### 已审核来源、执行进度与失败展示

Consumer 完成候选准备时，把原 trigger、消息/材料和计划涉及的 OA 原表单或文档源事实保存为 source_bindings，参与 candidate digest。System Executor 在每个尚未核验动作派发之前重新读取相同原对象，包括首次执行、同次后续动作和恢复部分执行。OA 已完成动作造成的 task 状态/操作记录变化由具名处理器核验，不作为原表单变化；真实表单、文档或上下文变化使该 review 失效，保留已核验动作回执和原人工回答，在同阶段形成新的完整候选并重审。来源读取失败属于可重试技术失败，不执行旧分支。

来源快照中的历史反馈正文只在结果检查副本中解析 Markdown 链接，并对真正的序列化来源容器递归检查。一个来源文本可合并多条历史消息，因此按 feedback_token 与 attempt_id 分组，并逐组验证各自完整的一条 up/down 链接；每个链接仍须通过当前配置 host/path/query、评分和敏感值检查。旧链接中的 original_text/reply_text 是预览文案，不作为配对身份；提供方渲染后两份预览可能不同。解析保留链接原文拼写，避免 Unicode URL 规范化后无法从检查副本中移除。当前待发送正文仍要求原始严格格式及完整 query 配对；原 source_bindings、候选 digest、持久化结果与发送前来源回读比较不变，其他来源字段继续接受原有敏感值、深度和大小检查。确定性的来源结果验证失败记录 runtime_result_source_invalid、stage=result、source=service、retryable=false、session_continuable=false；不归为 CLI/provider 失败并反复恢复同一会话。

Attempt 详情的 system_execution 按 candidate、review、selection 绑定显示当前及历史阶段、声明顺序、未开始动作、失败/不确定状态和 provider 回执。只有 canonical action ledger 中核验成功的动作计为 verified；Audit approve 和用户选择都不能显示为已经执行。

Attempt History 的外部效果摘要进一步区分本执行代首次产生的 verified effect 与本代复用的旧回执。当前代的 effect 必须由回执记录的原始 Agent run 归属；复用回执单列为“已复用”，不把旧操作归到本轮或本轮 acting run。动作时间取 provider 结构化操作记录中的原始时间；不以本次复用或 Attempt 更新时间替代。该区分只影响只读 History 投影，不改变 verified receipt、幂等复用或任何 provider 操作。

失败 Reply task 的 Attention 诊断只读取本对象本 generation 的当前 run，显示原 source_code（无则 code）与明确标为“Agent 说明”的 reported_summary；缺少或坏 JSON 时保留 task error，不将 Agent 自述当作已证实的 provider 拒绝。Attention 按 Reply task 的当前状态计数，不为同一 WeChat task 的 failed delivery 再增加一条投递级记录；delivery 失败仍保留在 Workers 队列、History、Attempt 详情和 Reply attempts queue。History、Attempt 详情和 Reply attempts queue 对微信分别读取本对象当前 generation 最新 delivery：failed/send_unknown 不能被只表示候选完成的 task done 遮蔽。旧 generation 或其他对象的 delivery 不影响当前结果；这些展示不更新任务、投递或授权状态。

`needs_human` 的质量门禁只检查拥有该 attempt 的当前 Reply Task 仍处于 `needs_human` 时的投影。Task 到达终态后，旧 attempt 继续保留在 History，但不再作为当前 Attention 或健康违规计数。

独立 Email 退订仍使用原有 direct executor、浏览器步骤和回执；所有结构化 `action_type=unsubscribe` 失败都保留真实 `failed` 状态和 History 记录，但不进入管理者 Attention；Attempt 详情同样保留失败状态和原因，但不提供 Attention 处理动作。已识别的可重试外部页面/网络失败按有界指数退避重试；共享上限是初次操作加 3 次重试，因此执行标记记录的 `retry_count=3` 仍允许第 4 次浏览器操作，之后的调用在浏览器运行前以未决结果拒绝。当前 `email_unsubscribe_browser_failed: category=<名称>` 和旧 `failed_browser: category=<名称>` 在附带脱敏 `detail`／`operation` 后缀时仍依据固定外部类别匹配，不会误归入内部错误。服务自身的执行围栏错误（如 `unsubscribe_operation_rejected:ValueError`）仍归类为内部技术错误，但结果不确定时只保留失败并等回读，不重放操作或清除标记；不确定性不改变 Attention 投影规则。效果零副作用的旧失败任务也只有在 ActionPlan 完整且没有执行中标记、claim、receipt、effect、浏览器 step 或 Agent run 时才会自动重新入队。退订动作读取邮件来源时，按精确 RFC Message-ID 在当前可选文件夹中只读定位；只有全邮箱恰有一个匹配项时才读取并校验稳定身份。零匹配是外部来源读缺失，浏览器尚未启动，按同一有界指数退避重试；多个匹配、身份不符、验证逻辑错误及未知结果仍停止且不可重试。不用相邻 UID、主题或时间猜测；外部读取重试不会执行退订动作。默认 developer prompt 和 OA 规则种子统一为新角色合同；既有自定义规则、managed Skill 配置与 runtime-only Skill 必须在安静的正式发布窗口中逐项发布并记录版本，部署代码本身不会覆盖它们。

退订入口导航遇到 Playwright 超时或 Chromium `net::ERR_TIMED_OUT` 时，若同一次导航已离开空白页，执行器直接读取当前页面并检查真实页面回执，不再请求退订 URL；若仍停留在空白页，则记录为外部超时并沿用有界退避。其他未识别的导航错误继续失败，不获得重试资格。该读回只恢复已经到达的页面证据，不清除未知执行围栏，也不把页面未读成成功。

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

### System 与未创建角色运行的实际失败上限（2026-10-05）

定时任务在 Consumer/Audit 已完成后的 System 实际失败使用四次持久化任务 attempts
（首次调用加三次 retry），前三次至少延后 60/120/240 秒，第四次 failed；重新入队不退回计数。候选、审核、动作键和
已核验回执保持不变，效果 uncertain 仍保留 uncertain 并只回读核验，不重发。
execution_claim_unavailable 是未取得执行领取的等待，至少延后 60 秒，不消费失败预算。
定时任务的来源刷新失败，以及 Worker 尚未创建角色运行的普通技术失败也保留 attempts，第四次写入 failed 历史，
保留原错误和 summary。既有授权、活跃运行及 provider 恢复等待沿用原等待机制；
真正开始后产生的 Consumer 与 Audit 技术失败分别计入独立、持久化、同执行代/同修订的四次尝试预算（每个角色最多三次 retry）。

## 运行环境与能力说明、Settings 完整输入预览

Consumer/Audit 的稳定指令包含原请求交付、按需取证、部分事实先交付及参与者时区原则。
稳定规则参与 Consumer 契约 hash；本轮时间与任务事实不因每次变化强制创建新 session。
每次实际 route 与角色 CLI 配置完成后，运行时追加「运行环境与能力说明（Runtime Context）」：
principal、task/generation/stage/revision、原业务对象与后台扫描来源、所选模型和 thinking、
资料根目录、实际命令目录及声明工具目录。工具说明复用 role server 注册描述和最终 allowlist，
不声明认证已经成功，不由个人安装能力推定后台权限。Claude 继承服务进程 cwd；Codex 使用
显式 `--cd`，缺少明确目录的续会话标为未核实。Consumer 普通材料工作与系统注册且要求审核的
动作保持既有边界；这项说明不新增审核、授权、路由或执行政策。

结构化来源已经提供的时区、来源和适用日期可进入环境事实；文字材料仍由 Agent 理解，
不通过姓名或关键词分支提取。

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

Developer Prompt 只承载适用于所有任务的角色、能力、证据、系统动作、对应路线所需的输出契约及共同工作原则；工作人格仍注入 Consumer 与 Audit，Audit Rules 只进入 Audit。Audit 启动时，服务从固定路径预载共享 `~/.agents/AGENTS.md` 和项目根目录 `AGENTS.md` 到 Audit Developer 输入；文件缺失或不可读会阻止该次审核。Consumer 不注入这两份指令，Agent 的 `read_text_file` 可访问范围也不改变。Consumer 使用 Codex 或 Claude 原生输出 Schema 时，服务输入省去重复的完整 Pydantic wire schema 文本；Codex 的原生格式契约本身仍须提供，不能只给指纹；没有原生 schema 的 Friday 路线仍保留该文本，Audit 输入不变。所有 Consumer 路线继续共用不得直接发送对外消息、邮件或执行审批的角色约束；本精简不改变该约束、严格 Pydantic 业务校验、原生 schema、Audit 或 System 执行流程。OA、日历、消息受众、OKR、招聘和文档等专项流程由当前任务选择的 Skill 承载，不再作为全任务 Developer 规则重复注入。User Prompt 是 Consumer 的完整任务模板，必须包含恰好一个 `{{task_context}}`，仅支持普通文本和这个插槽。任务来源、定时要求、stage、反馈、既有回执及 continuation 先由服务组装成完整上下文，再填入插槽；不将旧消息块模板叠加到后台任务。Workbench、独立 WeChat、纯服务命令及 Email 退订保留各自入口和显式指令。

Consumer 和 Audit 的 Task 输入都携带当前任务已保存的专项指令一次。Audit 将它作为候选审核的任务要求，来源记为「已保存任务配置」；不将其放进通用 Developer，也不依赖候选转述任务规则。两者复用已有的精确 Skill 附录分离逻辑，生成的发现提示不重复预载，自定义 Skill 约定仍在独立的 Skill 段保留。此输入修复不改变业务规则、规则优先级、审核或执行权限。

Consumer 的代码生成「Capability Instructions」只说明当前角色的工具使用细节：任务工件／文档写入入口、原生工作区、钉钉直连只读工具、依赖读取失败与风险拒绝的结果码。通用的按需取证、普通工作与受控动作边界、回执及失败原则已在同一 Developer 输入的已保存模板和角色合同中提供，不在能力段复述。能力目录仍提供精确 `read_system_action_contract` 入口；完整结果校验、Audit 规则、Task 事实和现有 session 绑定不变。Settings 当前预览与执行使用相同装配段，历史预览读取保存的原输入。

后台 Consumer/Audit 使用原生 skills.config 禁用专项 Skill 的自动正文注入，避免原生加载、Developer inline 与工具读取三份重复。原生 `project_doc_max_bytes=0` 关闭 CLI 自动加载项目目录 `AGENTS.md`；共享及项目指令只由 Audit 的服务 Developer 段按固定路径预载，Consumer 不接收这两段。全局 `~/.codex/AGENTS.md` 仍由 CLI 加载，服务不修改原生 home 或全局规则文件。此设置仅适用于后台角色命令。

Codex Consumer/Audit 对 `read_task_skill` 与 `read_skill` 均使用原生单工具 `output_token_limit=30000`，让冻结与已安装 Skill 的正文在 MCP 完整返回后完整进入模型历史。`read_task_skill` 的 MCP 回执只提交一份含 name、完整 content 和 sha256 的 JSON 文本，不再同时附上内容相同的 structuredContent；冻结来源、角色权限和业务结果不变。其他工具输出预算、角色、sandbox、审批方式和 session 绑定保持现有行为。该设置保障来源交付，不替代 Skill 精简或证明业务判断正确；验证及发布记录见 `docs/archive/records/task-skill-native-output-validation.md`。

System Action Contracts 在 Developer 中提供由 docs/system-action-contracts.md 自动生成的 capability/operation 目录；目录以 provider-neutral 的 `read_system_action_contract` 工具名指向 Runtime Context 中声明的原生入口（Codex 为 `mcp__agent_cli__read_system_action_contract`，Claude 为 `agent_cli.read_system_action_contract`）。该工具保留动作表外的全部原规范正文，只返回选定能力／操作的动作表行；只给 capability 时返回该能力的全部操作。原有角色、identity、payload、target、回执和完成条件保持完整，不返回其他操作的表行。Consumer 提出及 Audit 审查相应操作前必须读取规范；契约指纹仍包含全文。结果 schema 在 prompt 中只去除 title/default 注释，保留字段名、描述、枚举、约束及原有 Pydantic 校验。

Audit 传输结果的 `feedback` 在 `approve` 或 `failed` 时可省略，解析为 `None`；在 `return` 或 `reject` 时仍必须提供完整反馈。批准结果若携带非空反馈仍拒绝。服务绑定的候选、审查业务模型、结果校验、审核决策和外部动作权限不变。这个字段语义对齐针对两次完整轨迹中唯一的格式错误，验证见 `docs/archive/records/audit-null-feedback-validation.md`。

运行输入的分段来源由同一装配函数提供：每段记录 name、source、placement（Developer/Task）及脱敏正文 characters，长度不含段间分隔符。runtime.prompt 是运行期 RAM 中的准备/调用回执；终态不另存正文副本。Settings 公共预览使用当前角色装配，任务绑定预览区分当前 Developer 与该角色实际保存的 Task。历史预览读取选定 runtime attempt 的原生输入范围：Codex 只取带 generic.developer_instructions 与 user.text 标记的服务消息，排除 CLI 基础指令、AGENTS、环境、工具和历史；续会话明确标记 session 先前保存的 Developer，不冒充本轮配置提交回执。Claude 读取本轮原生服务合并消息的既有 Developer/Task 封装。没有细分来源、配置指纹或渲染时间时保持未记录，不用当前配置补齐。尝试列表保留不可读来源的准确 ID/路线；最新或选定来源缺失时明确不可用，不改显示较早输入。读取不重跑任务、修改数据库或保存原文副本。日历时区解释流程位于 ceo-calendar-invite Skill，Runtime Context 仅保留显式参与者时区事实。

一次角色 invocation 只读取一次 Developer/User/Profile 正文，供组装、静态指纹与重试共用；Audit 不读取无关 User 模板。实际路线和工具命令确定后，再追加 Runtime Context。静态配置 SHA 写入运行期 runtime.prompt invocation facts，仅作来源回执；原生历史未保存这些元数据时不重建 SHA；Consumer 继续按 `conversation_id + route` 复用原 session；Developer 指令或 contract hash 变化时新开 session。Audit 在同一 ReplyTask 和 execution generation 的 proposal revision 间续用自己最近的可续 session，以保留 Audit 自己此前读取的来源上下文；Audit 不读取或覆盖 Consumer 的 conversation session。若最近一轮明确标记 session 不可续用，则新开 session。每个 Audit invocation 仍提交当前 Task、完整候选与对应 revision，并独立检查最新事实、工具结果和候选绑定；路线、反馈预算、审核规则与动作执行不变。

默认 User 的 Rendered preview 使用明确标记的合成完整任务，不读取业务材料；Developer/Profile 渲染同一份已保存正文。完整运行输入仍在独立只读页签，当前配置 Developer 与所选已保存历史 Task 的来源分别标注，历史模式不重新渲染。读取设置/预览不创建或覆盖配置文件。已存在模板不在读取时自动升级；部署使用 `python -m app.deploy --publish-prompt-templates`，仅在既有停止/备份窗口根据 `ci/prompt-template-release.json` 的精确旧/新 SHA 发布默认 Developer/User。自定义模板须明确迁移，不能覆盖；文件备份、发布与回退沿用 RepositoryUpdater 的 publication 协议。

Prompts 设置读取先返回 Developer/User 的已保存原文；某份模板验证或渲染失败时，仅该份渲染预览为空并返回明确的 `preview_errors`，编辑器仍显示原文供修正。保存仍需通过现有验证，后台角色调用仍严格验证，不自动迁移或覆盖旧模板。

精简只移除重复或已由任务 Skill 承载的流程说明，不改变角色边界、身份与证据规则、完整效果判断、业务结果模型或 Pydantic 校验。Audit 的候选、source_bindings、revision 和 digest 保持完整；只有 provider、object_ref 与来源 value 按排序 JSON 完整相符（保留布尔/数值等 JSON 类型区别）时，Task 中重复的触发正文、raw payload、历史消息正文和材料 reference 指向候选的来源绑定，同一输入内仍能读到完整值。来源不同时两份全文保留。未结算的定时服务命令在 `scheduled_consumer.skill_materials` 冻结完整 managed revision 或 operation snapshot；未结算的通用 Scheduled Agent 在 `scheduled_agent_execution.v1` 顶层保存有序 `skill_names`、`skill_materials` 及其来源，原始 trigger 投影只保留结构化 Skill 身份事实，不再次复制正文。冷启动和续接都从同一未结算持久输入恢复；已结算输入改为来源摘要，冻结正文读取明确不可用。每一轮 Consumer 与 Audit Task 只列当前选中 Skill 的名称、极简用途和按需读取入口。保存输入声明该名称为冻结 Skill 时使用 `agent_cli.read_task_skill(name)`；所选冻结 Skill 的 revision_id/sha256 未变且完整正文仍在同一 session 上下文时，发现提示允许复用已读的确切内容；新会话、来源变化或全文不在上下文时仍准确读取，不新增缓存或本轮读取回执。材料重复、损坏或缺失由精确读取报告技术错误，不得改读当前磁盘版本。没有冻结声明的已选 Skill 使用目录中的实际授权绝对路径调用 `agent_cli.read_skill(path=...)`；Skill 正文引用的其他已安装 Skill 通过 `agent_cli.read_skill(name=...)` 精确读取，不把依赖目录预装进 Task。旧任务的显式 inline Task 约定仍在每轮保留。正文不复制进新 prompt，也不根据任务文字猜测来源。未选择 Skill 时 Task 只给 `agent_cli.read_skill()` 发现入口；无参数调用返回授权目录内的全部 name 和路径，不重复所有用途描述；按主题 `query` 查询返回匹配项的完整 name、用途和路径元数据，均不返回正文，Agent 再按精确 name 或 path 读取。目录查询和 name 解析沿用相同授权根，越界符号链接不会进入元数据，重名明确报错。目录 frontmatter 只提取顶层 name/description，嵌套产品 metadata 不会隐藏可用 Skill。无论有无选中 Skill，显式自定义 Task 约定都继续在每轮 Task 保留；服务生成的发现文字由结构化来源标记识别，不重复注入。新运行事实记录实际入口来源与本轮 `skill_names`；未注入的旧全量目录不再作为 `skill_protocol` 保存。Email 分类、Meeting Alignment 与独立 WeChat 没有任务内读取工具，仍直接使用完整冻结正文。Email 分类的 Codex 命令按显式内联 Skill 名称，通过原生 skills.config 仅禁用同名安装副本的自动正文注入；本轮服务选定的托管或冻结正文仍完整提交。此配置不重新启用或关闭其他 Skill，不影响 Claude/Friday 的原有单条消息提交，也不改变分类规则、工具权限、路由、审批或会话绑定。Runtime Context 缩短工具说明；Codex CodeMode 的可用工具清单显示原生 `mcp__server__tool` 可调用名称，Claude 保持 `server.tool` 表示法，其他环境事实不变。Task 中的 Skill 调用提示仍使用服务命名空间的 `agent_cli.read_*` 表示法；该提示与清单的区别在 `docs/archive/records/runtime-mcp-tool-name-validation.md` 记录。

Developer 保存先以现有渲染器验证，未知变量或不可渲染内容返回具体错误且不覆盖已保存正文；该检查属于配置格式合同。

默认共同工作原则要求先识别原请求要向谁交付什么、按本轮声明入口取证、交付可核实部分并说明剩余协调责任。消息处理以原始触发为目标；后续排查建议用于补充上下文，不替换原请求。若建议的操作不在当前能力范围内，但已核实信息仍能回答原询问，应回复观察结果、具体限制及继续所需信息，不因该操作不可用而直接失败。日历时区与候选日期偏移规则位于 `ceo-calendar-invite` Skill，只在适用任务按需读取；共同 Developer 不再全量携带该专项流程。两者都不改变已有角色、审核或执行合同。

低信息简短收口与轻量互动优先用原消息 reaction，避免发送重复的“收到”“好的”等文字；明确需要业务判断、承诺、解释、下一步或正式文字确认时仍发送简短文字。已有 principal reaction 通常视为真人已轻量处理。规则由 Consumer/Audit 共用的默认 Developer 提示提供，不改变消息 reaction 的既有 Action、Audit、执行器或回执合同。

配置回执同时记录 Developer 模板原文和渲染后共同原则正文的 SHA；相同模板引用的变量/文件/代码展开改变时，静态契约指纹也改变。静态指纹仍不是 session 身份；历史记录缺正文 SHA 时只显示未记录，不以当前值补造。

### 业务审核按完整上下文判断（2026-10-06）

Consumer/Audit 根据完整任务和对话上下文、本人职责、实际事实、接收对象、工作目的和动作后果判断候选是否合理。业务上下文不划分为可信或不可信，不另设“可信授权”或逐条消息许可声明。财务主题或群名本身不构成拒绝理由；不合适的候选必须指出具体事实缺口、受众不匹配、无依据承诺或与明确只出草稿／不发送要求的冲突。上下文不改变已配置能力，也不证明外部动作完成。邮件与微信 Skill 使用同一原则；Email 子系统现有 unsubscribe ActionPlan 和禁用 auto_reply 的范围保持原样。

Consumer 完整候选、Audit 精确审核、System 执行持久化计划及真实回执的绑定保持原样。

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

Workbench 的实时文字和工具正文仅在运行时 RAM 中使用；SQLite 事件行只保存服务回放 ID、顺序和最小原生定位。工具开始与完成通过原生完成顺序定位，并按原生 item ID 配对输入与结果；只有完成记录时直接读取该条原生记录。历史文字以原生完成消息聚合回放。任务结束后，在事务提交后释放正文缓存；失败与停止运行也按准确范围读取已有原生信息。最终文字不保存副本，详情按需读取 Codex/Claude 原生记录或 Friday Artifact；远端 Artifact 的读取在 SQLite 事务外进行。原生记录缺失时明确显示过程不可用，保留实际任务状态和服务附件记录。运行提供者的原始错误正文也不另存；服务保留失败状态、错误码和服务生成的公共原因，历史错误正文清除后由错误码生成展示原因。

定时配置正文存于 `scheduled_task_config_versions`，触发记录以 `snapshot_id` 引用不可变版本。数据库迁移保留运行 ID、状态和已排队输入。

模型目录在无训练进程活动时清理，保留当前 online 模型、实际 active／previous manifest 引用的可运行制品和跨模型系列最新一份待评估候选；历史 mode transition 仅保留切换事实及回执，不因曾被选用而保留旧权重。没有 active／previous manifest 时只保留当前模型和最新候选；其他旧制品及遗留临时文件删除。业务状态、最终结果、执行回执与小型评估记录保留。数据库删除旧正文后需要执行存储维护并压缩页才能释放文件空间。

描述优化提案保持产生它时的快照身份与引用证据不变。若提案尚未评估而源快照已被最新数据替代，提案一次性转为 `unavailable`，原因 `description_proposal_source_unavailable`；不反复启动失败的评估，也不把旧证据套到新数据上。下一次基于最新数据的训练可产生新的提案。

Task2D6 的 Email 分类运行时拥有原生 `tasks(kind='email_classification')` 行，状态使用 `pending/running/done/failed`；不可变输入只在 `email_classification_inputs`，分类运行结果只在 `task_results(result_kind='email_classification')`。Task `parameters_json` 不复制输入、结果或生命周期字段；路由运行键为规范 Task ID。完成仍在 owner/generation CAS 事务内精简输入，保存精确来源摘要、provider locator、配置版本和脱敏退订候选引用。稳定邮件身份只由输入表独立列拥有；完成态 JSON 不重复该列，原输入 hash、字节数及 identity hash 不变。固定 Email channel 由该领域合同提供，不再存储成输入列。稳定身份去重、输入不可变性、重试预算、claim 次序和退避保持来源语义；Email 业务分类、ActionPlan 与动作回执仍由 Email 业务表拥有。已完成输入不长期保留正文，历史维护仍显式执行。

精简后的邮件来源记录同时保留业务分类 `category` 和候选来源 `action_parameters.candidate_source`，确保已完成 Attempt 详情的业务显示不变；其他参数正文不复制。日历卡片修订仍只比较呈现正文（精简后使用原文摘要），定时扫描运行 ID 的变化不会重新打开已完成任务。

## Email provider identity lookup storage

邮件原文定位只使用当前账号、已观察到的文件夹与既有 RFC Message-ID 身份。
Message-ID 的域名按现行合同转为小写；IMAP 服务端 HEADER 索引不能作为该规范身份
是否存在的依据。需要重新定位时，使用只读 UID SEARCH ALL，每批至多 50 个 UID
读取 Message-ID 头字段，按同一规范化合同逐值全等比较；唯一匹配且 UIDVALIDITY
未变化才读取完整正文。不同域名、不同本地部分、重复身份和缺失原文仍明确失败。
不按主题/发件人猜测，不添加 DWS 或旧结构回退，也不缓存正文或在读取中修补坐标。
该定位成本随目标文件夹邮件数增长；已知 UID 的正常读取仍使用原来的直接读取。

Email provider 库存存于单表 `provider_state`（`record_type` 为 observation 或 folder_generation），
按 observation 身份、folder generation 身份、observation folder 三个 partial 索引定位；
observation 的 `provider_folder_name`、`category_key`、`important`、`important_signals_json`
为原生可写列，folder generation 的这四列为 NULL。

## Reversible database schema release

正式 deploy 在 quiet/停止服务及一致备份之后，先捕获本次 Store 或 Email schema 的回退
元信息，再在停止窗口执行数据库迁移，随后校验 imports、启动及 health。
回退捕获的原版本历史及旧序列化形式保持准确；不覆盖数据库文件或业务记录。
启动失败或 health/加载验证失败时，先停止可能已启动的 replacement、回退可选发布资产与 schema，
再恢复旧代码并启动。schema 逆迁失败时保持 replacement 停止并记录
needs_manual，不将旧代码启动在不兼容数据库上。每次 schema 变化都需要各自验证的逆迁；未验证的
Store／Email 同时版本变化不能合并发布。

## Canonical email action-plan reference

分类只保留 `current_action_plan_id`，当前计划及其授权、动作参数从 `email_action_plans` 读取，列表批量读取，详情、反馈重放、修订及退订修复使用同一来源；分类归属、连续计划版本、最高版本指针与授权约束继续验证。

> 已移出的历史章节与迁移实施记录（日期化清退日志、Store/Email 各期迁移、Phase4C、Email13/29、Native-source scalar 等）见 [archive/architecture-superseded-notes.md](archive/architecture-superseded-notes.md)。

### Task 原生 Skill 目录

Task 的 command factory 将当前 `ceo-work-tracking` 选择传到既有 Codex adapter，使用 CLI 原生 `skills.config` 限制目录。目录只启用明确选择的 Skill 和既有默认 Memory 插件，不再从正文的名称提及或 `dws` 命令推断递归依赖；相关依赖继续由 Agent 按需读取。未选中 Skill 的文件和既有读取权限不变。Consumer/Audit 使用空选择时，生成的配置与变更前逐字符相同。Task 的动态事实、输出 schema、业务校验、session 与路线选择保持原有行为；本机 Codex/Claude CLI 以绝对路径和版本摘要按需读取当前 Work Tracking Skill。Claude 的单消息装配不受该 Codex 配置影响。Codex 自带系统 Skill 仍由 CLI 决定，不将目录配置数量冒充实际显示数量。目录验证见 `docs/archive/records/task-native-skill-catalog-validation.md`。

### Full document native output delivery

Consumer/Audit Codex commands use the existing per-tool native output budget of30,000 for `agent_cli.read_dingtalk_document_full`, on cold and continued turns. The server's complete result can otherwise be clipped in the native model-visible output. This preserves the selected JSONML/revision result without substituting Markdown, changing all-tool budgets or adding prompt rules. The source may still exceed that finite budget; server complete=true alone is not proof of complete model visibility. Regression/native evidence is recorded in `docs/archive/records/document-native-output-validation.md`.

Consumer 通用 Skill 约定只规定独立选择并应用适用 Skill；每轮 Task 发现入口统一决定何时读取，避免通用层的读取措辞与冻结来源复用条件冲突。安装来源和未选择 Skill 的既有读取入口不变，不新增缓存或读取回执。

Consumer/Audit 的原生 `read_dingtalk_messages` 单工具输出限额为30000，用于完整交付已请求的消息字段；该设置冷启动和续会话均保留。不提高全局输出限额，不改变窗口工具、正文或分流/审核规则；长目录与文件读取保持各自现有单工具设置。

读取单聊时，`read_dingtalk_messages` 可接收触发消息已有的对方 `openDingTalkId`，并把它作为 DWS 单聊目标；提供此身份本身即选择单聊路径，不依赖另传 `single_chat=true`。会话 ID 仍用于标识原会话。若显式选择单聊却没有对方身份或可唯一搜索的标题，工具直接报缺少单聊目标，不以空标题查询通讯录。群聊读取方式及审核判断不变。

Consumer/Audit 的 DingTalk AITable 只读入口为 `read_dingtalk_aitable_base`、`read_dingtalk_aitable_tables` 和 `read_dingtalk_aitable_records`，对应已有 DWS Base/Table/Record 查询并以紧凑 JSON 文本返回；Record 读取保留单工具30000 token 输出预算。CEO Agent Service 的 `ceo-document-review` Skill 在 AITable 材料上引导这条 MCP 路径，避免误用文档或电子表格读取器，也不必为只读检查加载通用 AITable 操作 Skill。该入口不增加写能力或自动扫描。

OA pending scans carry the complete scheduled Consumer wrapper, including exact frozen Skill materials, into the claimed reply input. Consumer and Audit read those original bodies through read_task_skill; the scanner must not use the Task extraction projection that omits skill_materials. Task extraction sources retain their existing body-free scheduled context. This changes input delivery only, not Skill contents, task classification, audit policy, routes or sessions; old inputs are not rebuilt.

OA pending scan query bounds are normalized to `Asia/Shanghai` before DWS applies its start/end calendar-date filters. This keeps host-local midnight from excluding approvals created on the next DingTalk calendar day; lookback duration, pagination and current-user task verification stay unchanged. Review deduplication includes a digest of the selected Skill bodies, so changing an OA business rule creates one fresh review round for still-pending nodes even when provider records have not changed. An unchanged Skill snapshot remains deduplicated. If a review for that approval is still pending or processing, a changed Skill revision waits until that round settles before enqueueing the replacement review, preventing concurrent OA decisions. For org-structure approvals, applicant-correctable missing facts with a valid OA return path use a formal return; comments are reserved for missing return paths or genuine external-party waits. No document format is added as a requirement.

An unchanged daily OA scan may revisit an approval whose prior review completed without a decision, so it remains visible to the reviewer. It must not reopen a terminally failed technical review for the same evidence and selected-Skill revision: doing so would rotate the reply-task execution generation and refund the Consumer/Audit retry budget. A new DWS evidence revision or selected-Skill revision starts a new review round with a fresh budget; a successful no-action review retains its normal daily revisit behavior.

OA History reads adopted historical outcomes through the exact Attempt reference to its original body receipt and same-Action fact metadata. It does not decode retired provider response variants or historical executed-Audit output during a page read. The completed historical Store28.5-to-29.1 release projected those existing conclusions once; that converter is no longer a current publication tool; original bodies remain byte-exact and referenced, and missing Task/Run/Audit/provider identities remain NULL. A fact projection is not a new verified execution receipt. Current verified execution, workflow ordering, conflicts and comment/terminal precedence retain their existing meaning. Current unknown facts remain unknown when schema inverse is followed by forward migration. A nonempty local body that differs from its referenced body rejects normalization before any write.

History may reopen a skipped OA review only when fresh reviewer feedback exists and the prior Attempt remains unresolved, has no OA effect, and matches the same OA task. Once the Attempt has a resolution timestamp and resolution evidence, the History rerun entry and POST route stay closed even if the local task row is still `done` or `skipped`; this preserves verified terminal or superseding-source readback. A still-actionable OA review can be reconsidered through a new pending scan when its source evidence or selected Skill changes.

Each newly enqueued OA review round re-materializes the currently selected Skill references from the current scheduled-task configuration. For a selected managed Skill, it resolves the newest registered revision directly from the managed Skill store, including a revision absent from the worker's immutable startup snapshot (`managed_revision_not_loaded`); it does not fall back to an older loaded body. Disabled or otherwise unavailable latest revisions stop the scan instead of silently choosing older guidance. Operation Skills are read from their current authorized source. The resulting complete bodies are frozen in that new trigger's scheduled Consumer wrapper. Retries and corrections of an existing round continue to use that round's saved input, and previously saved trigger/run history is never rewritten. This changes which Skill revision a new OA review round receives; it does not change approval permissions, business thresholds, or action gates.

Manual OA rerun compares the refreshed trigger payload with the current task input. Identical input keeps the existing pending generation idempotently. When the selected Skill payload changed, rerun rotates to a new generation; an active Agent run makes that rotation unavailable and the request returns a conflict instead of claiming it was queued. The generation check and rotation are committed together, and prior inputs and runs remain immutable.

Supplementing a reviewed OA `needs_human` candidate also starts a new reviewed generation. Before queueing it, the service refreshes the currently selected OA Skills from the scheduled task, exactly as for a manual rerun; it must not silently reuse the old candidate's frozen business-rule snapshot. If the selected current Skill or task configuration cannot be resolved, the supplement is rejected without queueing. The prior candidate, review, and evidence remain immutable; the new generation still goes through Consumer, Audit, and the existing System action gates.

Historical OA review is an as-of review: use source versions that show what was demonstrably available at the original decision or return time. A tracker or document updated later cannot establish its earlier contents unless version history or another timestamped source reconstructs them; otherwise the earlier state is unknown and cannot prove an applicant gap or count an effective return.

Before applying the repeated-return threshold, reconcile the complete source-timestamped DWS `REDIRECT_PROCESS` timeline against local execution receipts and the effective-return count. For the first two returns, state all known, decision-relevant correctable gaps together with actionable requests; a genuinely new gap revealed by later evidence is explained when it appears, not backdated to an earlier return. A local Attempt marked `skipped` or `uncertain` does not erase a return verified in the complete DWS timeline; match its timestamp, process/task evidence and actual DWS remark to the local receipt where possible. Provider success without DWS action readback is not sufficient. Then assess whether the actual request addressed the same correctable gap using evidence available at that time, and whether a later response left that gap unresolved. Explain every excluded return and any difference between total redirects and valid rounds. If the full timeline or a difference cannot be verified, do not claim the threshold is met.

OA Attention keeps process instances present in the latest successful pending scan visible even after the local Consumer task has completed a comment or return action. Its fallback detail states only that the latest scan still sees the DingTalk process pending; it does not infer that no return receipt exists. History/System Execution and live DWS readback remain the sources for action receipt and active-node detail.

The scheduled OA Consumer prompt names the available operation and business Skills and directs the Agent to use their rules and the generic OA action table. Business thresholds and case-routing policy live in the selected Skills. Startup migrates only the exact known stale OA prompt; later administrator edits remain untouched.

Service Consumer turns prefer their declared `mcp__agent_cli` tools for DingTalk reads and may use local `dws` commands through shell for source reads when appropriate. The OA Consumer may use the selected OA Skill's `dws oa approval attachment authorize-preview` and same-file read/download steps when native preview permission is missing. Consumer must not use `dws` through shell to send DingTalk messages or email, or to perform OA approve, reject, return, or comment actions; those effects use the reviewed typed-action path. Audit uses declared read tools only and must not use shell or local `dws` commands. OA instance/task identifiers passed to read tools come verbatim from the current pending/detail fields; parameter or response-shape errors trigger source-value verification before a safe same-target read retry, and are not treated as missing applicant evidence. Missing native read capabilities remain explicit dependency failures. Document search exposes its existing page-size limit of 1–20 in the native tool schema and retains the same service-side guard.

For the bound DingTalk direct-chat task, the message readers take the counterparty's stable openDingTalkId from the exact claimed trigger when the Agent supplies only its conversation ID. They verify the trigger message and conversation identities before reading; a mismatched or compacted source fails rather than guessing from the contact name. `read_dingtalk_messages_in_window` uses the existing complete, bounded direct-chat window reader for that task, while unrelated group reads retain their command and response. This repairs Audit source access without changing candidate review or outbound execution.

### Calendar organizer source projection

DwsCalendarEvent exposes the existing organizer display string and an additional
organizer_details object containing the exact provider organizer object. Event
and calendar-window readers, and existing serialized invitation/conflict inputs,
retain userId, openDingTalkId and other source fields without turning a display
name into an identifier. A source that supplies only a string has empty details.
This is source delivery, not an identity-verification result; existing recipient,
Audit, routing and execution contracts continue to apply.

### DingTalk message-list response contract

DingTalk message-list readers require a structured object response. JSON `null`,
a non-object legacy result envelope, or a missing `messages` array in the
canonical `im.message-list.v1` contract is a typed provider-contract failure,
never an empty-message success. Unread reads do not
retry an invalid `null` response through the recent-message fallback; valid but
unrecognized object envelopes retain the existing fallback behavior. Read-only
message commands make only the configured bounded retry for JSON `null`; if it
persists, the typed failure remains visible to normal recovery and Attention.

### 原始触发、当前来源和立项财务口径（2026-10-14）

钉钉 Task 保存原始触发快照和固定回复目标，近期会话是另一次来源读取；同一 message_id 的审批卡片可更新，Task 标明两者来源及正文是否一致。近期已核实的处理结果可证明原请求已解决，后续新请求仍是独立任务。时间事实提供 UTC 及相对原触发的秒数；来源时间不可解析时不推断顺序。Audit 的 source_bindings 由服务在 Consumer 输出后捕获，反馈应修改 Consumer 自己的候选，不能要求 Consumer 重写原始来源快照。

已有外部动作的核验（2026-10-14）：System Executor 对已有 dispatched/uncertain journal 先调用具名处理器只读 reconcile；旧动作造成的来源变化不能阻止读取实际回执。该路径不再次 dispatch。所有尚未派发的新动作仍先检查原审批候选绑定的当前来源，已完成动作的身份和正文冲突检查继续执行。原生发送回执只从明确的发送返回结构读取 send-task 身份，引用的入站消息 ID 不能作为投递凭证。回复任务若已明确压缩触发消息输入，只能通过保留的原消息 ID 从 DWS 精确回读，并逐项核对会话、消息 ID、发送人和时间后再继续投递核验；不能把溯源包装解析错误误记为执行失败或转入重新派发。

正式部署的停止完成条件（2026-10-14）：在 bootout 前读取本次 launchd 服务的 supervisor 及其直接服务子进程 PID；停止等待同时要求标签已卸载和这些已知进程已退出，再进入备份、代码切换和数据库发布。沿用现有停止超时，不扫描或清理无关 CLI，也不增加审批步骤。

## Claude native attempt coordinates

Claude attempt start/end coordinates use the native Claude JSONL line counter, including resumed, completed, cancelled and interrupted turns. Streamed Claude references use claude_session; stdout event counts are not native transcript coordinates. Codex/Friday bookkeeping and task/session/routing policies are unchanged. Historical incorrect ranges are not rewritten or reconstructed. Original anonymous native evidence and current-main regression results are documented in docs/archive/records/native-input-evidence-release-validation.md.

## Consumer purpose 动作输出和权限重试

Consumer 的业务核心输出统一为 `action/reason/target/payload/evidence_refs`；`evidence_refs` 对每个结果都是必填字段，无可用引用时返回空数组，权限重试必须列出验证访问请求的引用，评分、阶段绑定和长期记忆保留为元数据。服务在推理前选择 purpose，生成动作集合及严格的 target/payload 分支；普通 purpose 支持 typed plan、reply/react 等动作，OA purpose 支持 approve/reject/return/comment。`return` 明确映射到真实 OA `revert_task`。自然语言 reason 不用于分流。多动作 plan 保留既有完整提案结构。新输出先规范化为现有 ConsumerAgentResult，再绑定来源、计算摘要并交 Audit；历史候选保持原样。Consumer 的 purpose 输出另存于候选中，以保留动作来源和回执关联。

Purpose 等待在业务动作成功完成或经审核 no_action 后关闭活动调度；原次数和期限保留，显式重新等待同一回执也不清零。恢复旧候选时省略不存在的 purpose_output 元数据，保持原候选字节及摘要。字段、动作表、JSON 示例和计数边界见 [Consumer purpose 输出与权限重试](consumer-purpose-output.md)；发布证据见 [验证记录](archive/records/consumer-purpose-actions-validation.md)。

## Dashboard console (2026-10-20)

Pending scheduled triggers display the immutable run snapshot's task name. A claimed
trigger is identified as the scheduler dispatching a built-in command or handing
off a task, without presenting its configured runtime as an executing Agent/model.
Elapsed time starts at the persisted first dispatcher claim, and last update uses
the matching claim's renewal clock; enqueue time is not substituted for a start.
The execution column says 本次触发: dispatcher lease generations are not Agent retry
counts. Dispatched child tasks keep their own existing execution metadata.

The /history workspace and navigation label are Dashboard. A single Service row
precedes Failed / Active tasks / Queued tabs; there is no visible page/date title.
Failed reuses the existing Attention API unchanged, including current unresolved
OA processes and persisted business choices; original states remain visible. It
counts problems, while Active/Queued count execution tasks, so counts are not
additive. First successful Attention read selects Failed when nonempty, otherwise
Active; manual selection survives refresh. Attention read failure is not zero.

The read-only /api/console/dashboard/queues endpoint provides current Task,
WeChat Memory import, Memory/Todo outbox, email-action and scheduled-dispatch metadata, with separate
counts and paginated rows. Task/Run generation, business-object identity and
existing lease facts determine the current execution view; expired Task leases
are not presented as running. Native runtime metadata adds Agent role/model and
start/update times without loading trajectories. When a deployed `runs` schema
does not expose the optional summary-input link, Dashboard omits only that link
and still returns queue rows and other available runtime metadata. Email provider operations use
their existing current-plan processing state and native retry predicate. Unknown
attempt counts remain unknown; first execution is zero retries, a queued item
with two prior executions is about to retry twice, and a running third execution
is in its second retry. Audit proposal revisions are separate. Existing queue
eligibility/recovery/Attention rules and all provider actions are unchanged.

Execution trends and searchable, paginated History remain below the queue.
Dashboard polls while visible; per-request cancellation prevents stale tab/page
responses from replacing newer reads. Service/queue errors remain explicit.

## Deployment execution drain (2026-10-20)

Ordinary deployment and restart first stop admitting new execution claims, then
wait for already admitted work to finish before the existing stop/backup/update/
build/verification/restart/health sequence. Queued work, delayed retries and new
messages remain persisted; the entire pending queue need not become empty.

Two stable database-adjacent POSIX flock files provide admission and drain.
Claims take `.deployment-admission.lock` nonblocking, acquire a shared
`.deployment-drain.lock`, then release admission before their existing SQLite
transaction. The shared drain lock spans commit. Deployment closes admission
first, waits for already admitted transactions with the exclusive drain lock,
and keeps both through health and rollback. Later claims return an empty poll,
so continuous new claim traffic cannot prolong that wait. This covers dispatcher,
direct email reply/classification/provider actions, Workbench and meeting
delivery/Memory initial claims. Renewal, results, source intake and ordinary
recovery continue. Descriptor closure releases admission on success, timeout,
failure, cancellation or deploy-process exit; there is no persistent pause flag
or task-state rewrite. The existing timeout still aborts a deployment that cannot
drain. This does not force hung work to finish.

In-flight checks include claimed email classifiers, Workbench turns and weekly
OKR jobs before native runtime creation, direct email reply claims and provider operations in processing,
in addition to the existing runtime and
dispatcher ownership facts. Retry policy, external-action rules and task

Deployment's runtime count excludes an unleased historical runtime when its
parent AgentRun generation has been explicitly superseded by the current Task
generation. A runtime with a valid independent native lease still counts, as do
current-generation and unbound runtimes. This is a passive read: historical Run
records and recovery state are unchanged.

Admission/drain flock acquisition is also bounded by30minutes. A stalled
already-admitted claim transaction therefore cannot hold admission indefinitely
before the ordinary in-flight quiet timer starts. Timeout closes both descriptors
and leaves the Service and pending work untouched; the existing in-flight wait
retains its own30-minute timeout after the claim boundary is acquired.

### 导航栏服务更新（2026-10-20）

默认每5分钟检查 Git 上游（可用现有环境配置覆盖）；导航栏在发现未部署提交时显示“更新服务 · N”。打开后重新检查仓库，以当前版本和上游版本的完整 SHA 固定待更新范围。单个提交显示原始说明；多个提交将范围内所有提交的标题、正文交给服务配置的原生 Codex Agent，总结为不超过500字的中文说明，并保留可展开的完整提交列表。成功摘要按两个 SHA 缓存；生成失败展示真实错误和原提交，允许重试。

确认后沿用现有仓库预约，启动独立更新进程，调用正式 `app.deploy` 安装弹窗确认的精确提交，网页与部署使用同一个 operation ID。正常部署暂停领取新执行任务，等待在途任务排空；新消息、Queued 和延迟 retry 保留，恢复后继续处理。原有备份、生产目录保护、构建、重启、健康检查和回滚仍由正式部署负责。重复提交相同预约不会再次启动进程。

更新状态读取原子仓库状态及现有部署记录，不等待发布期间持有的仓库互斥锁。页面空闲每60秒读取状态、更新中每3秒读取；重启断连显示重连，只有成功回执、目标 SHA、已安装 SHA 和实际 HEAD 一致时自动刷新。失败、回滚及需人工处理展示实际原因，结束后释放本次预约。

Failed / Attention cards expose persisted Attempt links before the error disclosure.
Reply failures use their original Attempt ID; failed Reply tasks and pending OA
resolve the latest Attempt through the task's current execution generation and
AgentRun identity. Meetings use their latest Meeting Attempt ID. A Task ID is
never substituted for an Attempt ID; a record without a persisted Attempt says
so in its expanded details. Membership, terminal states and retry policy do not change.
Attempt detail HTML and the React detail API accept stable public Attempt IDs. For compatibility,
if a requested number is not a public ID but is an existing `actions.id` for a
reply Attempt, it redirects to that row's public `legacy_key`; a public-ID match
always takes precedence. This read-only alias does not change the Attempt or its
Failed/Attention classification.

### Work Summary retry budget

Work Summary processing allows three retries after the initial call (four
attempts total). A
shared Task Agent session already owned by another worker is a scheduling wait:
the input returns to pending with a delayed available_at and refunds only the
current claim, just like the existing provider-capacity deferral. Actual failures
continue to consume attempts. An exception escaping the dispatch handler before
an Agent run is created now uses the same fenced, delayed failure transition as
Reply dispatch; the fourth failure ends failed and retains its error.

The Work Summary dispatcher closes an already exhausted pending or expired
processing input as failed with work_summary_retry_exhausted before admitting
another handler, even if its old available_at is in the future. The historical accumulated count is not cleared by this closure
(the closing claim itself is still counted); original runs and source content
remain available. Explicit retry-after-root-cause-fix resets the input's current
attempt budget, while preserving its existing Task Agent run history. Normal
provider-outage deferral, Audit behavior and external-action identity/recovery
are unchanged. Regression and release evidence is in
`docs/archive/records/work-summary-retry-budget-validation.md`.

### Email classification retry budget

Dedicated Email classification tasks allow at most three retries after the
initial call (four processing attempts total) per explicit retry. A retryable
failure returns to pending with the existing capped backoff only when its
exception identifies a transient transport failure (such as DNS resolution,
connection reset/refusal, timeout, or a runtime error explicitly marked
retryable by the router). Authentication, TLS, configuration, malformed-input
and other logic failures end failed without retry. Retryable failures are
requeued only before the fourth attempt; the fourth failure ends failed and
retains the actual error. Expired running claims consume the
same budget and become failed once it is exhausted. Startup recovery and queue
claiming also close previously exhausted pending records, including ones with a
future available_at, without incrementing their old count or calling the mail
provider or Agent. Their source input, stable identity and original error remain
available. Active unexpired claims and done tasks are not closed by this sweep.
The Console retry endpoint accepts only a failed task with no saved result,
requeues that selected task through its stable message identity, and confirms
the original input was retained. It cannot reopen a pending, running or done
task; discovery does not automatically reopen failed records. See
`docs/archive/records/email-classification-retry-budget-validation.md` for automatic budget
evidence and `docs/archive/records/email-classification-retry-api-validation.md` for the Console
retry contract.

When the original IMAP UID no longer returns a message, classification readback
may relocate it only with the task's exact account-scoped RFC Message-ID in the
same folder, then fetch and verify that exact message against the stable task
identity. It does not use sender/subject matching or alter classifier actions.

### Classification source-read failure repair

Owner-labelled context excludes only provider-only model inputs whose current
provider observation explicitly says unavailable. Inline and native originals
remain eligible regardless of mailbox observation; unknown provider observations
still use the existing exact read. Labels and source references are retained.
The example stamp follows this eligible set, so an observation becoming available
restores that example. Similarity scoring and exact-original hash checks are
unchanged. One known-unavailable historical example no longer aborts every new
classification before the Agent runs.

The shared account connector passes the same 90-second read timeout as the
readonly adapter, rather than inheriting the deterministic provider constructor's
20-second default. Classification owner-example reads use the existing shared account connector
through the supplied source reader, avoiding a separate connection registry.
See `docs/archive/records/email-classification-source-repair-validation.md`.

### 原生输出格式与解码契约一致

Consumer 的原生输出段仅保留 schema 指纹及 JSON/decision 包装说明，不全量注入 native 或 Pydantic 业务 schema。Codex Consumer 在冷启动和同 session 续跑时都通过 `--output-schema` 提交当前原生 schema。原生解码、完整业务校验、schema 指纹、路线、重试和 session 选择不变。公共/任务预览与实际提交继续复用同一装配，历史已保存输入保留原样；不以 session 历史作为 schema 交付依据，也不预先宣称删除查询后 token 或耗时必然减少。

### DWS 文档格式核验与字面内容

Consumer共用文档工具继续提交原始Markdown/JSONML工件；已执行写入算写入成功，核验状态独立保留，不因未核验自动重写。当前本机DWS核验按钉钉Markdown的双等号高亮语义处理作者输入，要求完整JSONML证明相同文字、规范化词间边界、高亮范围与颜色，再执行整体内容及表格对齐检查；不全局删除标记或忽略内容差异。需要保留双等号的标识符使用代码格式或转义，说明位于按需读取的dingtalk-doc Skill，不增加通用prompt。DWS源码修复仅提交外部PR，本机版本化发布与自然行为验收分别记录于document-write-recovery-validation.md；无新增写入重试、Audit规则或session机制。

### Current Meeting Run reference contract

批量与单任务的会议投递领取共用当前 claim 写入器，按现有
`idx_actions_public_id` 的完整身份和 partial predicate 更新 claim token。
不使用无目标 UPSERT 为 Actions 中其他领域的唯一索引展开更新分支；领取条件、
CAS、租约、token、任务引用及外部执行规则保持原样，不改变 schema。

Historical Meeting Runs whose original Task is unavailable retain the source
Job in `run_provenance.job_id` and use NULL for the optional native `runs.task_id`.
They remain inspectable through the current schema and do not become executable
Tasks. A non-NULL Task reference must resolve to a real current Task. HTML/API
detail and session-related history do not catch a broken lookup and reinterpret

### Native reference owner unification — 2026-10-26

Meeting detail and both search surfaces use the same current `native_run_ref`.
Task/Meeting execution references require their Run parent and existing execution
workload kind; completed-first/id order remains unchanged. Audit attempts remain
independent observed calls and are not decoded as execution output. No Audit,
authorization, retry or provider policy changes. Current Run session/range facts
remain available when there is no recorded Meeting execution attempt; failed or
running execution attempts remain unavailable. OKR exact-parent/session semantics
are unchanged. History metadata search and Meeting-native candidate enumeration
remain distinct; metadata membership/order/session binding and FTS/embedding are
unchanged. See `archive/records/native-reference-reader-unification.md`.

### DWS document permission pagination (2026-10-26)

The shared Consumer/Audit document-collaborator reader uses `dws drive permission list`.
It returns one original provider page and accepts `next_token` for the next page;
callers retain `hasMore`/`nextToken` rather than treating the first 50 members as complete.
No permissions are changed by this reader.

### Complete recent-conversation discovery (2026-10-26)

The shared `recent_dingtalk_conversations` reader retains the scheduled-trigger cutoff,
then calls `DwsClient.read_complete_recent_conversations`. A result is usable as a full
discovery only when the provider reports success, an exhausted cursor and complete
pagination, a matching half-open time window, and consistent collection/page counts.
Incomplete or inconsistent discovery raises `DwsError` rather than looking like an
empty or exhaustive conversation list. The original successful provider ledger is
returned unchanged; this reader adds no retry, continuation loop or business action.
Existing direct/group message-reading paths are unchanged.

### Consumer session contract completion (2026-10-26)

Consumer native session creation and RuntimeAttempt completion persist the same
existing `selected-skills:consumer-developer` contract, including the exact
Developer instructions. Completion must not overwrite it with the bare wire
contract. With an unchanged saved native schema and Developer contract the next
turn resumes the same route session; a changed Developer contract still selects
a fresh session under the existing rule. Audit hashes and route/retry/approval
policy are unchanged. No backfill or old-hash compatibility lookup is added.
The gated two-turn regression uses the real Store and substituted native CLI.

### Repository Skill installation (2026-10-28)

Company policy packages are authored in a separate private repository. The public
release retains generic baseline templates and synthetic/contract tests; company
OA rule cards, registries and policy regressions are not public release assets.
The old explicit contract release list no longer publishes the company OA entry
or review files. Existing installed company packages and managed revisions are
preserved. Optional external identifiers may remain in feature configuration;
they do not make private access a service-startup prerequisite.

Use the existing Settings URL search and Install for new company packages.
Installation preserves same-name content and is not an update/adoption command.
An organization-specific reference package can compose with a generic baseline
without replacing it. Policy authoring belongs in the private source; runtime
edits still use immutable managed revisions. No automatic export to public Git
or history rewrite is performed. Earlier public history is not erased by this
current-tree separation.


First deployment installs missing public managed Skill packages from the release into the installed Skill directory before startup. Existing installed content is preserved. Enterprise packages are optional and are installed separately; the initial baseline does not copy adjacent company Skill folders. Bootstrap verifies the complete generic baseline.

Repository URL/name discovery resolves exact commits. Installing a selected Skill copies its complete tracked package into the installed directory and creates its immutable revision plus pending configuration through the current Configs owner in one transaction. File publication occurs inside that transaction and failed operations restore the staged package. Existing bindings and feedback capability are inherited; no external workflow is enabled. Source identity remains in the revision source field; imported optional names participate in existing edit capture without rewriting their ownership metadata. Runtime activation still requires normal startup and load evidence.

Source-shaped unsupported inputs are rejected locally, while supported HTTPS/SSH sources use native Git only. After a failed load, installation inherits the effective active bindings/capability without deleting the failed revision.

Reviewed Consumer Skill script implementation may live in a private installed package; the public `ci/reviewed-skill-scripts/` catalog pins its entrypoints and complete script-tree digest. Installation alone grants no script entrypoint. See [private script verification](private-skill-script-verification.md).

Reporting policy, scoring, tenant destinations and their executable implementations belong to independently installable organization Skills. The public reporting baselines carry generic discovery/evidence/delivery workflow only; scheduled tasks explicitly select the organization package for normal frozen script binding. Generic report publication preconditions name the generic digest, never the existing company digest.


### 当前投影与退休的 Workbench 确认状态

当前 Task delivery 与 Feedback 投影仅使用现行字段，不接受历史来源变体；历史转换的
字段顺序固定在显式来源投影模块。执行进度只由原生 Consumer Run owner 写入，旧计划
执行造数仅在测试夹具内。Workbench 当前状态为 queued、running、completed、stopped、
failed；已退休确认功能的 waiting_confirmation 不再进入 API 类型、计数或界面状态。
本次不改变原生 CLI 确认、外部授权、审查、回执或恢复策略，也不重解释历史任务状态。


### 邮件历史中性标记退休

Store29.1/Email54 的分类不保存 legacy_processed_without_plan，当前 processed 分类始终
引用已持久化 ActionPlan；计划的 legacy_serialization_pre_v16 零值槽位及虚拟列也已移除。
NULL authorization snapshot 和历史 category 仍是原有事实，计划 ID、正文来源及回执不改变。
已完成的 Store28.5→29.1 发布曾核实零值/NULL、固定 channel 及重复输入身份，
其转换器已退休；当前发布入口只支持 Store29.2→30.1。历史转换不另存旧正文。

### 直接邮件 Action 的字段归属

直接邮件 Action 以 Plan ID 和 operation 定位不可变 Plan；classification、account 和对应 operation 参数从该 Plan 投影，Action 本身只保存执行状态等自身事实。账号变更和分类计划切换的 processing 阻挡经相同 Plan 关联检查。Store29.2 到 Store30.1 发布对全部源值及类型作精确核对，逆迁按同一 Plan 还原；其他 Action、回执和 Attempt payload 保留。
