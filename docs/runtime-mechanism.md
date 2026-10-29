# Current Runtime Mechanism

> 本文只放现行合同。已退役／历史章节和日期化迁移实施记录原样存于 [archive/runtime-mechanism-superseded-notes.md](archive/runtime-mechanism-superseded-notes.md)；新增日期化变更说明写完即并入对应现行小节，不再堆在文件顶部。

## 数据库结构与迁移合同

Derek于2026-10-24批准分阶段退役历史迁移链：部署路径 `prepare_database_schema_release` 只处理当前 Store／Email 版本、Store `2026-10-28.4`→`2026-10-28.5` 的微信语音转写退役，以及仅 Email 变化的发布；其他版本一律以 `Store schema release needs a verified inverse for this transition` 或 `unsupported current Action source schema; explicit paired release required` 拒绝，较旧提交不再有自动逆迁。部署进度 DAO（`ExistingSchemaUpgradeStateStore`）只接受 Email 账本版本 52／53／54，Email 51 及更早的账本报 `unsupported committed Email schema role`。

闸门进锁并按提交复用（Derek 2026-10-28）：闸门测试在仓库互斥锁（`GitRepository.mutex`，阻塞的 flock）里面跑，和安装同一把锁；拿到锁后先看生产是否已被别人推进（是则直接返回「another deploy got there first」，有发布请求的按 `_ProductionMoved` 重来），再跑测试。通过的结果记在服务数据库同目录 `deploy-gate-passed/<sha>.json`（提交、时间、文件数、摘要行），同一提交再部署或安装失败后重试不再跑；失败、超时、检出失败都不记。10-28 当天 Codex 和两个 Claude 会话同时部署，闸门并行，9 次只过 1 次，7 次超 10 分钟、2 次撞 2 GB，这就是起因。
清单缺失或为空、运行超过 12 分钟（`TEST_GATE_TIMEOUT_SECONDS`；测试进程自身另有 10 分钟和 2 GB 上限）、或无法检出该提交，同样拒绝。失败时保留日志并在报错里给出路径。`--restart` 不运行新代码，不受此限。实现见 `app/deploy.py` 的 `require_green_suite`。

正常空库直接建立当前声明；运行时只接受真正空库或精确当前声明冷启动，缺表/列/索引/触发器及版本混合均在修改前拒绝，不自动补表或回填。Store-first 可暂缺 Email 账本；Email 构造器只在准确状态下建立账本与首次默认配置。验收和边界见[Phase 1 生产验收](action-entity-production-acceptance.md)。
当前验收见[运行时声明清理验证](archive/records/runtime-current-schema-validation.md)。历史 GPT-6 原生链路证据见[此前发布验收](archive/records/database-gpt6-release-validation.md)。

Action Entity Phase 1 已部署，当前生产即使用统一 Action owner：`actions` 的 `_kind=70` 保存共同 Action，`action_receipts` 保存逐次 provider/execution 事实，`action_observers` 保存多个 Run 的观察关系，typed identity map 保留精确旧身份。创建、CAS 领取和结果更新由 `app.action_entity_storage` 提供，调用方持有事务。生产读回、准确退休清单、copy 容量结果和剩余路线见[Phase 1 生产验收](action-entity-production-acceptance.md)。
字段映射见 `docs/action-entity-field-map.json`；`docs/action-entity-target.sql` 是初始字段合同夹具，当前完整 Store/Email 目标声明见 `docs/action-entity-current-target.sql`。

当前 native `tasks` 表的 `kind` 合同包含九类：`reply`、`work_summary`、
`meeting_alignment`、`okr_review`、`okr_analysis`、`workbench_turn`、
`email_classification`、`feedback_processing` 和 `wechat_import`。Task 的生命周期
字段与 `parameters_json` 由 `tasks` 持有；Reply 输入由 `task_inputs` 持有，
WorkSummary、Email classification、Feedback processing 和 Workbench turn 的
类型化明细由各自 owner 表持有。原来源类型及迁移结构不是当前运行时 kind 或
兼容层。当前字段、约束和类型定义见 `app/task_run_entity_definitions.py`.

截至 2026-10-25，Task/Run 统一已部署并构成当前生产运行时。当前部署和证据边界见
[业务模型当前进度](archive/records/business-model-execution-progress.md)。

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

失败 Reply task 的 Attention 保留当前执行代最新 run 的原始诊断：优先 `source_code`，
否则 `code`，并展示来源及「Agent 说明」`reported_summary`。因此已保存的
`provider_risk_rejected` 不会只剩通用 `agent_reported_failure`；这些诊断不是外部效果证明。
旧代或无法解析的诊断不替换任务错误；读取不修改状态或恢复边界。

日报的 task-bound `consumer_document_write` 回执除报告文档和 provider 写入结果外，还原样返回
已从目标知识库完整目录解析出的 `folder` 元数据。Consumer 使用其中 provider 实际提供的
`url` / `docUrl` 完成日报文件夹链接要求；服务不由 node ID 拼接 URL，也不为返回该元数据
新增写操作。provider 未提供链接时仍保留缺失事实。周报回执、审核和消息投递边界不变。

通用文档写入采用 DWS 原生 `doc create/update`，成功返回 `ok=true/status=success`、provider 写入回执、文档身份及正文 SHA；`readback=null`，不输出内容核验结论。自动正文读回、格式比较和读回产物生成已移除。JSONML 输入解析、revision 检查及版本保存保留，未执行或提交状态未知仍抛出错误，不自动重写。创建后目标解析失败保留已执行的写入回执及解析错误。Agent/Audit 仍判断业务目标是否达到，执行状态不明或具体内容有疑问时可使用既有读取入口调查。

所有系统业务审核项必须对应实际系统任务及其当前业务实例；工具或接口存在不构成审核需求。没有对应系统任务就不设置该业务的系统审核项。当前对应关系见 [系统任务与审核范围](audit-task-scope.md)。小青面试与提交由 Derek 在个人对话中处理，不属于本次后台系统审核范围。扫描、下载和登录命令沿用既有技术结果验证，不因这条范围规则新增 Audit 回合。

Consumer 负责业务准备和完整候选，Audit 只读审核整份候选，系统执行已持久化且审核通过的完整结构化动作。所有 Consumer 入口共用同一条行为指令：不直接向外发送消息或邮件，也不直接执行审批；返回结构化决定或候选，由服务处理外部效果。Audit 的角色规则逐项核对外发候选的收件人/目标、内容、证据和任务范围；候选文本不作实际执行或送达证明，Audit 不发送或执行任何动作。若来源或任务明确要求只要草稿或由用户发送，Audit 退回或拒绝拟发送消息、邮件的候选；其他有效结构化外发提案也只能由 System Executor 在 Audit 批准后执行，只有服务回执证明外部效果。Consumer 保留报告、文档准备能力，不自行执行提交审核的受控动作；没有新增 update_daily_report 动作。Email 退订保持独立的系统直接流程。该指令不构成工具或凭据的强制权限隔离。

Consumer 的普通工具工作不因写入而自动进入系统动作审核。任务绑定的本地文件由
`consumer_artifact_write` 写入 `/tmp/ceo-agent-service-task-artifacts/<task_id>/<execution_generation>`，
Consumer 与 Audit 可用 `read_task_artifact`、`list_task_artifacts` 读取当前代材料；Audit 没有写工具。
清单只列文件名并流式计算 SHA-256，单个文件超过正文读取上限仍可列出；正文读取继续受原有大小上限约束。
任务处于 `pending` 或 `processing` 时保留文件供恢复和审核。定时任务失败后保留其 task-bound 工件；
同一 Task 经正式重跑并切换执行代次时，Consumer 将旧代文件复制到当前代，当前代仍有独立写入围栏，
已有同名当前代文件优先，源代文件不移动。任务进入 `done`、`needs_human` 或 `skipped` 后清理所有代次；
非定时任务的失败清理保持不变。Consumer 与 Audit 运行指令均要求把 memory
作为耐久参考区、禁止把临时代码、测试、数据库、日志、下载、截图、缓存、草稿、中间结果或单次运行
工件存入 memory，临时文件只使用 task-bound `/tmp` 工作区；有价值的最终材料仅在已有授权目的地按主题
归档。部署前遗留的 workspace 工件会在任务下次读写时迁入 `/tmp`，终态清理也会清除尚未访问的旧位置。
`consumer_document_write` 处理当前 scheduled 任务产物和显式文档目标。工具返回的 provider 回执与提交正文 SHA
记录普通材料写入，不是已审核消息或 OA 动作的 System 回执。原生 V8 可调用具名工具和计算，
Codex Consumer 可在 task-bound `/tmp` 工作区进行原生代码执行；Audit 只读。实际不可用的工具应如实报告，不能伪造执行。
系统注册且需要审核的动作仍只由 SystemExecutor 执行。Consumer 的结果解析只验证严格 wire，
不再对整份文字扫描“已发送”等完成短语；历史事实和本轮工作由完整来源及真实回执支撑，
摘要不会生成 candidate execution、外部动作回执或 sent_replies。此规则不改变已有运行时风险拒绝、
授权缺失或历史拒绝不可重放的处理。

Codex Consumer 的工作工具面不是白名单（Derek 2026-10-24）：`make_role_agent_command` 对 Consumer 不再强制关闭
`features.apps`、`features.plugins`、`features.remote_plugin`，也不再关闭 `~/.codex/config.toml` 里的其他 MCP server
或限制它们的工具，而是沿用本机 CLI 的配置，并对这些 server 设置 `default_tools_approval_mode="approve"`
（Audit 的 `approval_policy="never"` 下否则工具会被拒绝）。仍然关闭的是：服务 MCP 清单（Settings → MCP）的 `disabled_servers`、桌面控制类 server（`computer-use`、`computer-history`、`cua_repl`，代码常量 `CONSUMER_ALWAYS_DISABLED_SERVERS`）、
`multi_agent`、`computer_use`、`browser_use*` 等原生功能。读取 Figma 这类外部信息属于处理过程中的工作工具，不走提议；
只有发消息和发邮件等需要 Audit 的终态动作走 typed action，该约束仍是提示词约束（Consumer 不得经 shell 的 `dws` 发消息、发邮件）。
Audit 的命令不变：仍关闭上述功能，只启用 `ROLE_MCP_READ_TOOLS` 列出的只读工具，并强制关闭其余 server。
Runtime Context 对没有 `enabled_tools` 限制的 server 声明 `mcp__<server>__*`（全部工具，参数以本轮工具 schema 为准），不再逐个枚举。

要求修改 CEO Agent 服务本身的请求（例如「请磊哥修一下分身」，任务 15810）不是回复或 Agent 能完成的事。`ceo-message-triage` Skill 规定 Consumer 只回复一次，引导发信人用回复末尾的「👎 需改进」反馈链接提交，使其进入用户反馈流程；会话里已有这条回复时用 `no_action`。同一 Skill 的 Audit 解释把这条回复（或之后的 `no_action`）视为已满足请求，不得因服务尚未修复而退回，重复发送同样的指引属于重复。

微信统一路径阶段 1（Derek 2026-10-24，微信 Consumer 与其他频道使用相同工具面）：Consumer 与 Audit 的 `agent_cli` 读取工具新增 `read_wechat_messages(limit)`，读取当前认领的微信任务自己的会话（最新消息在后，链接带文章正文）。工具不接受会话参数，会话 ID 和单聊/群聊类型取自认领任务，任务不是微信频道、执行代变化或没有就绪账号都会拒绝。微信目前仍走自己的流水线，所以只有微信任务才会用到它；其他频道的任务调用它会得到「不是微信会话」的拒绝。

Codex Consumer 命令按当前 `consumer_purpose` 选择 general/OA 原生输出 Schema，并在模型输出后
把动态 JSON 键值条目无损还原，再交给现有严格 wire/Pydantic 解析器；OA 动作仍按原有
action-specific target/payload 合同校验。已完成运行结果的 schema identity 绑定实际提交的 Schema
字节，避免用旧自由格式结果恢复新原生格式运行。Consumer 的 Codex 与 Claude 原生 schema 路线不再把同一份 Pydantic wire schema 重复放进服务 Developer 文本；Friday 无原生 schema 的路线仍保留完整文本，Audit 输入不变。预览及运行快照使用同一路线装配。所有 Consumer 继续共用“不得直接发送对外消息、邮件或执行审批”的约束；解析、业务判断、审核与 SystemExecutor 外部动作流程不变。

绑定 `dingtang-okr-review` 的 Consumer 读取当前评分详情并提交 `dingtang-okr/score_kr` 候选，不直接运行评分脚本或写入接口。该动作走现有候选、Audit 和 SystemExecutor 执行记录；服务通过
已同步登录态的无头 Chrome 页面调用页面原生 API。只读入口返回周期、当前批次、目标、
评价人及登录用户的评分详情；写入前核对周期列表与批次名称、详情中的周期和目标、
KR 评分配置，以及登录用户与候选评价人一致。页面的 `makeScore` 只接收目标和 KR 记录，
按登录用户写入当前批次；写入后逐项回读同一周期、批次、KR、评价人、分数、评语和完成状态。
页面的评分详情与评分提交请求使用原页面认证，只对这两个端点将 `X-Space-Id`
置空；其余页面请求不改。只读详情的提供方对象错误保留数值 code 与消息，经既有
凭据脱敏后返回，不泄露 traceId；读取成功不产生评分写入回执。
不确定回执只允许只读 reconcile；不能因进程恢复重新提交 `makeScore`。

启动恢复只会保留满足当前 Consumer 结果合同的待选决策。旧式 `requested_input` 或缺少可复用规则选项的候选仍可在 History 只读查看；若它仍占据当前、经 Audit 批准且未选择的投影，`reconcile_invalid_needs_human_projections` 会把当前 Attempt/Task 投影转为 `failed`、清空可执行选项，并保留原候选、Audit 与 run 原文。之后须由正常 Consumer/Audit 闭环生成普通回复或向来源人追问；该对账不发送消息、不执行当前分支，也不把失败伪装成完成。有效的 task-class 规则问题保持 `needs_human`。

若 DingTalk trigger 引用了一条本服务通过 provider 成功发送状态回读确认的 Agent 消息，系统将该关系作为结构化输入来源传给 Consumer；模型不得将其续接/恢复任务升级为 `needs_human`。此判定使用 conversation、quoted message ID 与发送回执的对应关系，不根据正文里是否有 AI 签名做字符串匹配。Consumer 结果校验和 Audit 对同一当前任务上下文独立执行该限制。

通用消息受众契约由 Consumer/Audit 共用的固定审核规则注入，已保存或空的自定义规则均不会
省略该契约。候选应依据相关群讨论、完整当前成员、稳定身份及当前 title/职责判断实际内容
的披露范围；职位只是证据，不是自动授权，私聊也不自动安全。同一受众适合全部内容时保持
单条消息；确需不同受众时沿用既有多动作候选，各自保存准确正文、目标与 action identity。
拆分后的私聊只能发给已核实的对应人员，不能发给 principal 自己或其已验证别名、默认抄送
自己或把自己作为未知对应人员的兜底；另行明确要求给 principal 的报告不受此拆分约束影响。
每个动作独立核对既有回执并恢复未完成结果；拆分不得绕过历史运行时风险拒绝。这是准备和
审核规则，不是新增发送器，不以 prompt 注入成功替代原生业务验收或生产回执。

Consumer 的未审核外部效果检测只把写入操作的 provider 回执视作副作用；
群消息列表中的 `conversationMessagesList[*].messages[*].openMessageId` 是历史消息身份，
不能据此判定 Consumer 发送了消息或阻止后续安全恢复。检测只读取 provider 的
`data` / `result` / `provider_result` 结果封装中的回执，MCP `content[*].text` 与
`structuredContent` 先按传输封装解码；正式发送工具的 `provider_result.result.openTaskId`
同样保留为执行证据，不递归
业务正文、历史消息样本或其重新组织后的预览。拒绝结果不作为已接受的效果，真实
发送结果继续保留回执的出现顺序并去重。这是诊断证据边界，不改变审核、发送授权或恢复规则。

规则问题也先经过 Audit。Audit 检查：适用规则是否确有缺口、规则/代码/Skill/记忆/会话/读取是否可自行解决、当前事实是否应由来源人补充、是否把技术失败或当前实例决定伪装成规则选择、上下文和理由是否充分、2–4 个规则选项是否互斥可行且确有差异、每个执行分支是否完整并有依据。仅当前版本 approve 后才进入 `needs_human` 并通知；审核通过不等于业务 done。

Attempt 详情仅在当前状态要求人工决定，或该候选已有历史选择时显示人工决策依据；未选择且已失效的旧候选保留在历史数据中，但不再向用户展示为当前待决定事项。

多动作按声明顺序执行，前置结果验证后才执行依赖动作。每个阶段是一份新完整候选和审核，`stage_index`、`predecessor_review_id` 绑定前一已完成阶段的回执；阶段上限独立于每阶段最多十次内容反馈周期（`MAX_CONTENT_FEEDBACK_CYCLES`）。Consumer wire 仍携带这两个字段，但解析器用编排器提供的当前阶段绑定覆盖模型回传值。Audit 返回后的同阶段修订保留原阶段和原前置审核；其 immediate parent Audit 审核上一份同阶段候选，候选 lineage 仍关联前一阶段 Audit。只有前一阶段审核通过且动作回执已验证，编排器才允许下一阶段。候选存储按现有契约验证非空的前置 Audit Run ID 是否对应同一任务/代际、前一阶段、批准结论和已完成动作回执；迁移保留的历史候选 JSON 与摘要不改写，关系读取 native adopted result 外键。

Audit 返回或拒绝后，Consumer 必须提交当前阶段的完整修订候选。服务从当前编排上下文绑定 `stage_index` 与 `predecessor_review_id`，模型复述错误不会将候选误判为跨阶段或错误前置。返回反馈不会推进阶段；仅当前阶段审核通过且其动作有已验证的完成回执，后续阶段才会开放。候选的阶段、前置审核、代际及执行验证仍由服务严格校验。

主页面 `/` 只是同一 Service Runtime 的一个输入和展示入口。页面 turn 使用 `workbench`
workload 进入 `RoutedCodexExecution`，共用模型路由、会话、runtime attempt、失败切换和 CLI
原生 `auto_review`；页面不启动自己的 Codex runtime，也不定义独立审批策略。
旧版确认记录只读，不能从页面或 API 恢复执行。

模型路由的名字：只有 `codex_oauth`、`claude_oauth`、`friday_runtime` 三条内置路由名字固定；其余
路由（含名为 `codex_api`、`claude_api` 的）都是添加的线路，由 `CEO_RUNTIME_<名字>_*` 描述、可改名，
运行时规则按路由种类而不按名字判断（详见 `docs/architecture.md` 的「Agent Runtime 路由模型」）。
添加的 Codex 路由可设置独立的 `REASONING_EFFORT`（`low`/`medium`/`high`/`xhigh`）；
留空沿用全局 Codex 默认值，任务本轮显式指定的值仍优先。路线改名和删除会同步处理此设置。
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
- `needs_human`：当前完整 Consumer 规则问题已通过 Audit，只等待对适用于这一类任务的 2–4 个规则选项作出反馈。选项必须标记 `applies_to=task_class`，不能要求 Derek 决定当前具体事项；当前事实问题应由 Agent 通过普通、安全的 proposal 处理。历史问题和未经审核的 Consumer 输出不能作为当前人工待办。分数是结果分类的硬门槛：信息完整度低于 0.5 时先 ask-back；否则只有高风险且置信度低于 0.5，或规则覆盖低于 0.5，才可能进入 `needs_human`。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败；必须保留失败原因和阶段。

### 失败与人工决策通知

`failed` 和 `needs_human` 都使用同一条通用本地通知链路，不按业务频道假定目标是钉钉。通知携带当前
`attempt_id`，点击通过 `/open-attempt` 打开对应审计详情；若存在已打开的 CEO 页面，由 Service Worker
导航到 `/attempts/{attempt_id}`，否则由本机通知回退直接打开该详情页。钉钉会话只能作为额外上下文入口，
不能成为邮件、听记、任务或其他来源通知的唯一点击目标。

`audit_revision_exhausted` 的钉钉回复任务，在同一会话读到发送者身份已验证、非当前用户且严格晚于失败时间的新消息时，
自动以该批消息身份生成一次新的执行代次。当前用户自己的消息和缺少发送者身份的记录不触发重放。相同来源修订幂等；
只有旧任务仍为该错误、代次未变、没有活跃 run、发送回执、
受控动作执行回执或 provider 风险拒绝时才入队。失败后没有新来源不重复空跑；已发送或风险拒绝任务不自动重放。

OA 扫描按已验证的当前用户 ID 归属待办，任务节点凡未处于终态（包括 `PENDING`、`IN_PROGRESS` 和
无状态记录）都作为未完成节点，不只识别 `RUNNING`。来源不完整或所有者无法验证时，不把本地状态当作审批完成。
如果失败回复的当前代次审核候选指向精确 OA 流程和任务，扫描器只有在实时详情同时证明流程及该任务节点均为
终态后，才记录外部读回证据并以代次 CAS 将该回复任务收口；OA 仍待办、ID 不匹配或读取失败都保留原状态。
Attention 只投影当前未解决状态；已完成／已跳过任务不继续显示，旧 OA 游标也须经完整实时读回才可清除。

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
任务本身不进入 Attention；旧代次失败也不污染新代次。对于同一定时 Agent task，Attention 只统计最新的已派发 run：较早 run 的失败 Reply task、失败 Attempt 及绑定该 run 的读取错误仅保留在 History，不再作为当前 Attention；当 `scheduled-task-run:<id>` 已无法关联到现存 run 时，其读取错误也仅保留在 History。与 occurrence 关联的 `scheduled_task_execution_unavailable` 事件，如果同一 occurrence 已派发的 Reply task 当前为 `done` 或 `skipped`，即由该 task 终态覆盖；按相同 event ID 绑定，不依赖可能因写入顺序差异而倒置的时间戳。其他旧事件需后续已派发 run 的 Reply task 终态覆盖。最新 run 失败时仍显示其失败。未派发触发和其他 scheduled task 的成功均不覆盖当前状态。
从失败 Attempt 手动重跑时，Consumer 与 Audit 的上下文必须带入来源代次的结构化 Audit 反馈；
旧候选被否决的点仍须解决，或用新证据明确说明其不再适用，不得把相同候选当作未审核的新建议。
已结案的 `skipped` OA review 仅在审核者提交明确反馈、同一钉钉 OA task 仍为 pending、流程/task
身份仍匹配且原审核没有外部动作回执时，允许手动创建新一代 Consumer→Audit 审核。该入口不重开
`needs_human`、其他 skipped 项或已有外部动作的 OA 审核；新的业务动作仍须经过原有审核与效果回执。
旧 Attempt 即使缺少 `resolved_at` 也可进入同一判定；跳过状态、任务身份、反馈与无外部效果条件仍须全部成立。
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

Task2D6 的 Email 分类实现使用原生 `tasks(kind='email_classification')` 持久化队列；输入与结果分别由 `email_classification_inputs` 和 `task_results(result_kind='email_classification')` 拥有。运行期状态仍为 `pending/running/done/failed`，路由工作键只使用规范 Task ID。每封邮件是一次经共享
Runtime Router 的 Agent 轮次（`EmailClassifierRoutedBackend`），与其他 Agent 轮次使用同一条线路
顺序和统一 fallback；没有哪条线路名是邮件分类专用的（Derek 2026-09-24）。
离线训练标注命令 `app/email_training_labeler.py` 同样走系统模型路由（Derek 2026-09-25，不再有
`--route` 直连）：路由只运行服务记录为运行中的工作，所以标注命令为每次分类登记原生运行中 Task 和准确输入子项（`EmailClassificationTaskAdapter.open_offline_task`，不含邮件定位、不可被扫描领取），再交给同一个 `EmailClassifierRoutedBackend`。包装器把规范 Task ID 传入委派分类调用，finally-close 按该 ID 删除准确的空输入占位 Task，启动时只清理同 owner 且符合原空输入条件的崩溃遗留。来源 `INSERT OR REPLACE` 重开同一 incoming token 的行为保持不变。
分类器 Codex 调用设置原生 `project_doc_max_bytes=0`，因此不会把仓库 `AGENTS.md`
项目说明重复加入这一封邮件的判断输入；服务仍提交冻结分类 Skill、当前邮件、允许类别及退订候选，
结果仍按严格结构校验，并只用候选索引绑定本轮原 URL。其他任务的原生项目文档设置不变。
同一 Codex 命令使用既有 `skills.config` 目录选择器排除与分类无关的原生 Skills，
并继续排除已内联的 `ceo-email-classifier` 原生副本；默认常驻插件入口可保留。
目录选择在命令构建时读取原生 Skill 根，服务不复制或维护另一套静态清单。
Claude/Friday 路线、完整冻结 Skill 正文和分类结果合同不变。
分类请求允许正常的模型响应时间；临时网络、超时和租约中断在同一任务的四次尝试预算内（首次调用加三次 retry）按共享
指数退避重试，重试间隔最多 15 分钟；第四次失败进入 `failed` 并保留实际错误。输入、契约或
持久化冲突等不可重试错误直接进入 `failed`。修复根因后的显式重试重置当前预算并保留任务身份。Status、Attention 和每小时 quality gate 都必须覆盖该队列，不能只用
Email worker 的汇总健康状态代替任务状态。

Email 处理进度 API 分开提供当前队列与近期活动：`provider_action_backlog` 统计当前 ActionPlan
的全部 `pending`／`processing` 邮件动作，不因创建时间较早而遗漏；`provider_actions` 的待执行数仍是
近 24 小时活动窗口的一部分，仅供近期完成比例。页面的队列为空判断、待执行数和刷新频率使用当前
backlog；吞吐仍用近 30 分钟窗口。该只读投影不会领取、重试、对账或改写动作。

`Settings -> Skills` 的开关位于任务生产边界。功能与业务 Skill 的多对多关系由
`data/config/skill-features.json` 声明，运行时开关由 `data/config/skill-state.json`
持久化；每次生产检查都会读取最新状态，因此常驻 worker 不需要因开关变更而重启。

关闭一个功能只拒绝该功能对应的**新任务/新作业创建**，并不会取消、删除或改写已经存在的
`pending`、`running` 或重试任务。消费者领取和继续处理已有任务时不再重新判断该开关，保证
切换不会中断正在进行的生命周期。缺少或校验失败的关联 Skill 会让该功能标记为配置不完整，
并阻止它继续创建新任务；其他功能不受影响。

Skill 发现描述必须对应其正文的调用角色：`ceo-work-tracking` 是 Task Agent 的
Project/Task 结构化读取器，不回复来源；Consumer 的项目文档审阅及来源回复使用
`ceo-document-review`，一般消息判断使用 `ceo-message-triage`。这不改变现有
任务分类、工具权限或 Skill 正文。仓库 CI 资产与实际加载的安装资产分别验证，
不能从 CI 文件更新推断生产已经加载新描述。

## Runtime-managed Skill 修订与启动配置


公司业务规则的作者源为独立私有仓库；公共发行保留通用基线模板和合成／运行契约测试。
公司 OA 规则卡、登记表和政策回归不属于公共发布资产，旧显式发布清单也不再发布这些文件。
现有安装目录、不可变修订和加载回执保留；新增公司专用包通过 Settings 的仓库 URL 搜索与
Install 安装。同名内容不覆盖，安装与下次启动加载仍是两个状态。详见
[迁移验证边界](private-business-skill-migration-validation.md)。当前文件分离不改写旧 Git 历史。

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

上述功能开关只控制**新任务/新作业创建**，不控制 Skill 是否可被
runtime config 加载。关闭功能不会取消、删除或改写已存在的 `pending`、`running` 或重试任务。
缺少或校验失败的关联 Skill 会让该功能标记为配置不完整，并阻止它继续创建新任务；其他功能不受影响。

每个 Consumer 和 Audit 结果都严格携带 risk、confidence、rule_coverage、information_completeness；后三项在 [0, 1] 内。四项分值是新结果的强制准入门槛：`information_completeness < 0.5` 时先 ask_back；否则仅 `(risk=high 且 confidence < 0.5) 或 rule_coverage < 0.5` 才可能进入 `needs_human`，其他情况按适用规则自主处理。新 `needs_human` 还必须有 2–4 个互斥的 `applies_to=task_class` 规则选项、且 `requested_input=null`；Audit 在开始审核前和绑定结果校验时独立验证分数及规则选项，不合格结果进入修正流程，不会被投影成 needs_human。已持久化候选的历史读取保留原始证据，不重新套用新准入条件；历史解析不授权新候选绕过 Consumer/Audit 准入。材料可向申请人获得时先由 Agent 形成有边界的普通回复或单问题 proposal；技术、认证、路由和解析失败保持 failed。停止选项明确 skipped 和原因。

新 wire 严格解析新合同；持久化结果的历史证据解析只保留原始评分和分支事实，不会重新评定其当时是否符合现在的准入门槛，也不补齐内容、重审或改写原始 run。当前新候选仍必须通过 Consumer 与 Audit 的准入校验；恢复时按已有 candidate/review 绑定继续，已确认执行结果只读回而不重放。当前人工决策必须绑定原对象、execution_generation、持久化 candidate/review 和未被替代的当前版本；旧的无绑定选项仍可阅读但不能执行。人工决策和错误分别展示；审核批准不是投递完成。

等 Derek 决定的事项出现在他先看的地方（Derek 2026-09-25）：Agent 首页左栏顶部「需要你决定 · N」列出当前 generation 中已经审核通过、未选择且绑定有效的 `needs_human`；旧无绑定问题仅在 History 只读展示，每条以事项名称为标题（OA 扫描取「待处理审批：」后的审批名，钉钉 DING 催办取「提醒您审批…」的审批名并注明催办人，评论提及取审批单名），次行是一句原因，点击打开该 attempt。首页原有的「处理反馈 · N」只计消息反馈，与待决事项无关。点击通知时，服务先在已打开的 Chrome/Safari 控制台标签页里切到该 attempt 并把窗口提到前面（AppleScript，按控制台 origin 匹配），只有没有已打开的控制台标签页时才新开页面（Derek 2026-09-25）。首次由服务进程控制浏览器时 macOS 会请求一次自动化权限。

## 审核反馈闭环

Message Triage 的 Audit 按触发消息判断 Derek 角色是否有待办。对方已明确接手的后续事项即使尚未完成，也不单独构成要求 Derek 追问的理由；但 Audit 仍须退回直接要求 Derek 决策或行动的候选，并且不得把对方承诺写成已完成或已核实。

Audit Task 在完整候选旁附上服务从同一候选 JSON 确定性生成的标量字段证据：JSON Pointer 路径、精确字面值和 JSON 类型。字段类型反馈必须和该证据及候选相符；该索引不是额外来源事实，也不改变审核决策规则。

钉钉引用回复由服务使用 DWS `chat +messages-reply` 投递；该入口核验原消息、发送者与会话一致性，且适用于已确认的单聊会话。投递仍沿原业务对象的幂等键和回执核验，不因引用回复失败自动改发普通消息。

会议总结的业务群候选由正文中的业务词与标题共同检索，排除听记摘要里的时间标签、图片链接等元数据。平台中立的共享群发现服务按标题/摘要与群名筛选，读取完整成员，并以 `member_precision=匹配参会人数/完整群成员数`（3/N）代替参会人召回率。移除至少两人且覆盖60%参会人的过滤条件；业务讨论、主题优先排序，准确率只用于同等业务证据的候选比较。候选保留完整成员 IDs、群人数、匹配人数、准确率及参考参会名单完整性，再交给 Agent 核验业务承接与实际披露授权。当前运行时只有 DingTalk/DWS 适配器，Lark 和 Slack 需要各自 Provider 的认证、读取和外部读回验证后才能启用。发现服务不选择最终目标，也不把 Provider 故障降级成“找不到群”。没有标题命中时仍保留候选做实时名册检查，不能按搜索接口的原始返回顺序截断候选。候选排序依次参考讨论片段分数、标题/摘要与群名的主题分数、人员准确率；词项交集只是检索线索，Agent 必须核对实际讨论、行动负责人和受众。成员准确率仅描述参会人与完整群成员的交集，不证明业务归属或披露权限。业务会议总结优先发到经核验的业务群；完整搜索后没有合适群时按「会议无合适群时的收件人」选择单聊对象。个人事项仍只允许符合条件的个人 1:1，人员敏感内容仍使用独立核验的私聊。历史投递只报告次数，不再赋予 verified_recurring_group 标记，仍须核验当前实际内容和完整受众；此前已发送的总结不会因路由修复自动重发。

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

#### 来源、项目与任务

所有会议、管理/部门/项目周报、聊天、邮件均可作为事实来源；周报不是唯一来源或风险判断前提。
正式 Project 须有真实目标和范围，不把部门、客户标签、主题、孤立小任务或相似性 cluster 当成项目。
确认的报告项目登记表行、明确会议立项可以登记正式 Project；聊天/邮件补充既有项目事实，
不单独建立正式项目身份。保留不同时间、来源的冲突与引用，不静默以某类来源覆盖较新事实。

Project 可选关联 Fxiaoke CRM AccountObj，持久化稳定 `_id` 与 CRM 显示名称，不修改 Project 标题；内部 Project 可以不关联客户。Task 不保存重复客户字段，只有关联到已确认 Project 的 Task 才在读取时继承其客户名称，独立 Task 保持无客户。Task Agent prompt/Skill 引导写入当前证据支持的持久项目风险和重要进度/状态更新，保留原始来源引用与来源时间，区分来源事实和有证据的风险推断；不写单条 Task/TODO、日常或临时信息、无依据猜测、秘密、原始转录及重复更新。不增加工具白名单、通用只读模式或 CLI/MCP 写入拦截。

CRM 查询使用 `sharecrm data record query-by-name` 对 AccountObj 做只读名称解析，不写 CRM。Agent 提供的 `crm_customer_evidence` 必须同时、完全一致地出现在 Project `evidence` 中。该解析器不能证明候选精确或穷尽；Task Agent 和 Project 页面返回的每个候选（包括单条候选）都保持未关联，只有用户明确确认后才建立本地关联。Resolver `NO_MATCH` 与 CLI/认证/查询不可用分开记录。查询失败或后续冲突不会清除已确认关联。客户汇总按 CRM `_id` 分组，只包含已确认关联的 Project；未关联项目仍在常规项目视图。

AI Minutes 的来源定位包含 transcript_excerpts 时，必须同时记录真实生成格式
`transcript_excerpt_format`：当前生产者为 sentence_v1，已核实历史整段格式为 paragraph_v1。
两者沿用相同的前六段、后四段窗口，仅行序列化不同；读取严格选择指定格式，缺失或未知格式
在访问 provider 前返回无效定位，hash 不匹配也不尝试另一种格式。没有转录片段的定位不保存
格式字段。这是本地精确读取能力，不表示已选择最终物理表或删除历史正文。

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
语义校验，失败时最多在同一 session 修正一次；结构化输出不取代业务校验。Task 任务正文不重复附加完整 schema；Codex 使用上述原生通道，Claude 由现有运行适配器将同一份完整 schema 拼入单条消息（Friday 不具备 Task 所需能力，不执行 Task）。Skill、事实、业务规则和会话绑定保持不变。
新建、候选及 skip 决定携带 `status` 或 `business_relevance` 时，校验明确要求省略这两个初始字段；只有既有 Task 的 `update_task` 配合 `update_fields` 才能修改它们。该诊断不放宽来源、身份或业务校验。
Task 业务指导以当前 `ceo-work-tracking` Skill 为主；每轮包装对所有可执行 Task 的路线只给出该文件的绝对路径和 SHA-256，不复制完整正文。Agent 冷启动先读全文；续会话仅在相同版本全文仍处于当前上下文时复用，否则重新读取。包装保留 Skill 未完整描述的输出约束：assessment 选择器、顺序依赖建议、状态更新 transition、晋升 suggestion、owner_evidence 结构、Attention 决定成员与相同事实提案去重。动态来源、时间、visible_ranges 和当前 Project/Task 上下文保持完整。不新增读取工具、权限或路线；原先完整注入阶段的固定对照见 docs/archive/records/task-guidance-dedup-evidence.md，按需读取对照见 docs/archive/records/task-skill-on-demand-validation.md。

#### 回执、检索及尚未完成的关注集成

检索先读取正式 Project 当前资料/证据，再相关 Tasks；同来源旧身份强制保留，预算先给它们。
正式 Task 的负责人原文核验先处理全部同来源记录，再按排名填满已核验与待核验两个展示桶；两桶都满后不再读取不会展示的后续 Task 来源。同来源超额记录、已选 Task 的证据及来源失败语义不变。
零 Task 项目仍可读 context、当前职责/事实证明与有界近期来源，context revision limit=1，
证据有界并固定保留引用 ID。current_project_attention 保留真实卡成员和原 assessment 原文。
Task 语义上下文用无损紧凑 JSON 序列化，省去排版缩进；字段、顺序、原文字符串、引用偏移和检索选择保持不变。
source_documents 以完整来源绑定身份键提供精确版本的共享正文一次，每份 document 带 `doc` 序号和其 Signal 共有的来源字段（source_type/source_ref/source_time、会话、作者）及 `document_id` 一次；source_signals 只保留 Signal id、`doc`、`context`（指向 `signal_contexts` 里的 context_json 文本，相同文本只写一次）、`created_at`，某个共有字段取值与其 document 不同时才在该 Signal 上重复写出。Agent 引用仍是 Signal id 加该 document 的 source_ref，服务校验引文不变；数据库内部 dedupe_key 不进入 Agent 输入，持久去重键及其查询行为不变。
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

`reply_task_inputs` 的输入身份、版本和来源摘要保留；准确正文只供未结算的执行和恢复使用。符合既有终态结算条件后，当前任务、其输入版本及 Attempt 的输入正文改为来源引用与准确摘要，业务结果与执行回执不变。原生过程和准确原文缺失时明确不可用，不由其他结果拼造历史输入。

Store schema `2026-10-11.1` 中，`reply_tasks.current_input_id` 与
`claimed_input_id` 直接外键引用所属输入行。父表不保存正文、provenance 或精简标记副本；
当前展示读取 current，当次队列领取后的执行和冻结 Skill 按代际校验读取 claimed 快照。
再次领取仍捕获当时 current 输入，沿用原有恢复及版本水位行为。
WeChat Agent 的 `read_wechat_messages` 同样读取本轮 claimed TaskInput 的会话，不接受调用者指定会话；
执行期间 current 指针更新不会改写已领取的会话范围。
微信语音（Derek 2026-10-26）：`kind=voice` 的消息，`text` 为非空占位，直接聊天的入站语音与入站文字同样触发回复。Reader 新增 IPC `read_voice(conversation_id, message_id)`，只在该会话的消息表确认语音后，到 `media_N.db.VoiceInfo` 按 `server_id` 取 SILK（最新分片先查，上限 2 MiB，失败码 `voice_unavailable`）。通用 Agent 工具 `save_message_audio(message_id, resource_id)` 按任务渠道把语音存为任务文件（微信：`pilk==0.2.4` 转成 24 kHz 单声道 WAV；钉钉：`dws` 下载消息里 `mediaId` 对应的音频并核对消息身份）；Agent 调 `ai_services.create_audio_upload` 取一次性上传地址，用通用工具 `upload_audio_file(name, upload_url)` 把该任务文件 POST 过去（音频不经过模型，服务不存凭据），再调 `ai_services.get_audio_transcription(job_id)` 取文字；读不到或解不开是明确错误，不当作空消息或已回复。`pilk` 需装进服务 Python（`~/miniforge3`），部署不会替你装；Reader 需重建才有 `read_voice`。解密改为逐页写出，避免 700 MB 的 `media_0.db` 占满 Reader 内存。 自己发的语音是指令（Derek 2026-10-28）：钉钉里本人在以 Agent 名字命名的单聊发的语音消息（含 `[语音消息](mediaId=…)`）和微信里本人在 `filehelper` 发的语音（outbound）都成为候选，不需要 `@磊哥`；仅限语音，其余本人消息仍是历史（`worker._is_principal_voice_to_agent_chat`、`wechat.producer.is_self_voice_command`）。
微信显示不了的消息（Derek 2026-10-28）：本机微信版本渲染不了的 appmsg，微信会用自己的标题「Your current Weixin version does not support this content…」和升级链接 `support.weixin.qq.com/update` 顶替原内容。Reader 的 `decode_message` 识别这个占位（升级链接前缀或中英文「不支持该内容」标题），输出 `[无法显示的内容·本机微信版本不支持这类消息，appmsg 类型 N，看不到发送方发了什么]` 而不是 `[链接]《…》`；`kind_for` 因此把它归为 `unknown`，与图片一样不生成回复任务，只作为会话上下文出现。微信投递状态回写 Attempt 只认当前执行代：`_sync_wechat_delivery_reply_attempt` 在投递的 `execution_generation` 与任务当前代不同时不写回。
Reader 版本（Derek 2026-10-28）：Reader 是 PyInstaller 单独打包的 app，`python -m app.deploy` 不会重建它，所以服务和 Reader 的代码版本由 `app/wechat/reader_version.py` 统一定义：对 Reader 打包进去的源文件（`READER_SOURCE_MODULES`，不含 `app/config.py`）求摘要，取前 16 位。服务从自己的检出算 `expected_reader_version()`；`scripts/build-wechat-reader-app.sh` 把同一摘要写进包内 `reader_version.txt`，Reader 进程用 `running_reader_version()` 读出来（没有戳记的旧包报 `unstamped`）。Reader 的每一条 IPC 回复（成功或失败）都带 `reader_version`，`WechatReaderClient` 每次调用先比对再读结果，不一致就抛 `reader_version_mismatch`，错误文本给出两边版本和重建命令；`health()` 结果里也带 `reader_version`。改了清单里任何一个文件，部署后必须重建并重装 Reader，否则微信读取全部失败并明确说原因，而不是用旧代码继续读。
控制台对 Attempt 的操作有记录（Derek 2026-10-28）：`/api/console/history/{id}/rerun`、`/retry`、`/resend-wechat` 被接受后，服务用 `record_console_attempt_command` 写一条已解决的 error 行（kind `console_<command>_requested`，detail 含 actor、客户端地址、User-Agent、attempt_id），键是该 Attempt 的会话加触发消息；控制台界面的命令请求自带 `X-Console-Actor: console-ui`，脚本或其他 Agent 直接调 API 时 actor 为空、按 User-Agent 显示为「未标识来源」。Attempt 详情的元数据里每条命令一行「操作记录：重新处理 · console-ui · 127.0.0.1 · 时间」。被拒绝的命令不记。
读回收口后的文案：人工「重试」用 Reader 读回把失败的微信任务结算为 `done`/`skipped` 时，执行回执按设计保留 `failed`；Attempt 页现在读 `get_wechat_reply_task_reconciliation`（即 `wechat_reply_task_*_reconciled` 那条已解决 error 行），把状态文案换成「已通过微信读回确认这条回复已发送；原执行回执保留，无需操作」或「…后续回复已发出，这条旧候选已跳过…」，不再在 done 旁边显示「已审核方案执行失败」。
微信 Skill 不重复追问（Derek 2026-10-28）：`ceo-wechat` 规定对话里已经由本账号向对方问过同一个问题或要过同一份材料、且对方之后没有任何回答时，返回 `no_action` 并在摘要点名那条早先的提问；对方再发一条读不出的、空的或占位消息不算回答，也不重新打开这个追问，要不要再问由本人决定。
输入变化与指针、版本水位在原事务内更新；`input_version`／`claimed_input_version`
仍用于原有并发判断。终态输入正文及重复精简 JSON 为空，旧 API 表示按 provenance 生成。
已结算的真实历史身份不删除，无法证明的旧领取输入不推测。迁移必须在正式 quiet 停止窗口执行。
配对逆迁恢复实际原表声明、索引、序列和旧正文投影，保留迁移后新增业务结果与回执；
逆迁失败时沿用既有发布机制保持 replacement 停止。

新建 Task 工作汇总输入只移除 Task Agent 不注入的 `scheduled_consumer.skill_materials`。
共享 Consumer 上下文、prompt、Skill 名称、协议、来源及既有输入保持原样；
WSI 原有无损压缩和终态清理继续执行，应用边界仍返回准确 JSON 字符串。

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

近期单聊恢复只从 `input_compacted=0` 的当前 Reply 输入提取发送者稳定身份。已结算输入的原始
消息 JSON 按保留契约压缩为空串；这些历史输入不参与身份提取，避免把合法的空载荷当成损坏 JSON。

评论通知只有 `process_instance_id` 时，运行时从事件正文提取该身份；若该实例只有一个已知
节点，则归并到该节点。存在多个节点而事件没有 `task_id` 时不得猜测。

`reply_attempt` 的 `agent_run_id` 指向当前投影对应的最新或终态 run；执行历史状态
通过 run 的 task、generation 和关联事件查询。Attempt 页面可以切换多个 Consumer
或 Audit run，但不能编辑或覆盖旧 run。失败状态、session、runtime attempt 和采用的 provider 回执继续保留；原始失败与 tool event 从原生记录按需读取，来源缺失时明确不可用。
Attempt 详情的每条 runtime 记录分别提供 `status`（运行时调用状态）和 `run_status`
（所属 Agent run 的业务运行状态），页面分别标注两者。模型调用返回 `completed` 不代表
业务审核通过、外部动作成功或业务 run 完成；业务 run 仍可能是 `failed` 或 `running`。
此处只投影已有状态，不改写历史记录、Audit 决策或恢复资格。
Session 可用性测试同样按真实 `AgentRun` 提供业务 `status`；缺失 transcript
只影响 `session_available`，不改变业务运行状态或运行时调用状态。
Workers 的当前 attempt 队列统计会排除 `agent_run_id` 指向旧 execution generation
的记录，即使同一业务对象更新了 trigger message；旧 attempt 仍可在历史详情中查看。

对进入 Consumer/Audit 的 task，当前投影结合当前 execution_generation 的候选、审核、人工选择、System execution 和真实回执。模型 run 完成或 Audit approve 不等于动作成功。System 已开始的精确动作在同一 candidate/review/generation/selection 下恢复；确认成功的持久化回执可完成执行投影，不要求为取得回执重新运行模型。已经开始的精确动作，其结果（verified／confirmed_no_effect／uncertain／failed）在 Task 的 execution_generation 已轮换之后仍然记录到该动作的原 Attempt，规则与供应商回执一致；轮换不会让已对外执行的动作停在未确认状态。无法确认的外部结果保持 uncertain，不取得再次发送资格。自动技术重试在同一 generation/revision 追加 turn；明确的人工 rerun 或来源补充通过正式入口创建新 generation；旧 run、attempt、session、runtime event 和回执保留为历史事实。真实 pending/processing 仍显示等待/执行中，旧 generation 的失败不覆盖当前进展。

`needs_human` 的有效绑定选项对应已审核的 task-class 规则分支，由 System Executor 执行；停止选项按原方案写入 skipped 和原因。历史 `requested_input` 或新增事实作为新输入进入 Consumer/Audit 生成并审核新 revision，不能改写已经批准的方案，也不能将历史输入问题重新激活成当前 `needs_human`。旧无绑定选项不可执行，未收到规则反馈时不猜测决定。

Console Attention 同时投影当前队列故障和当前有效、已通过 Audit、尚未选择且仍未解决的 `needs_human` 决策。普通 `pending`、`processing` 或仅因状态为 `needs_human` 的记录不因此进入 Attention；人工决策行必须仍绑定当前 task/generation 与最新有效候选。Status 的 Attention 总数与 `/api/console/attention` 使用相同口径，避免把需要用户选择的已审核事项留在单独列表而漏出 Attention。

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

质量门对「反馈没有结案」的处理见 `docs/architecture.md`：已重跑且全部动作已验证的手动反馈是提示，不是错误。

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

- **`dws oa approval list-pending` 的行在 `result.values`。** cursor 里 `seen_process_instance_ids` 为空表示**解析失败**，不是"没有待办"。
- **待办查询窗口按钉钉时区解释。** 扫描器所在主机的本地日期可能与钉钉日期不同；DWS 对
  `--start/--end` 的筛选按日历日期执行。服务先把窗口边界转换到 `Asia/Shanghai` 再调用，避免
  本地深夜创建、钉钉已进入次日的审批被 `end` 日期排除。窗口长度、分页和后续当前用户 task 校验不变。
- **本人的评论不是决策。** `ADD_REMARK` / `COMMENT`
  不终结指纹计算。
- **每条审批一个会话，且每次扫描都从干净会话开始。** 会话按 conversation id 复用，入队时清空该 conversation 的全部路线会话（`clear_conversation_runtime_sessions`）。
- **唤醒条件**：已经评论过的审批不再按天唤醒，等指纹变动——指纹本就排除本人记录，所以只有他人
  评论或操作才会叫醒它；**没有评论过的**保留每日重看，因为那种是静默丢掉的，没有别的机制能捞回来。
- **通知归并**：OA 系统通知和聊天窗口催办不再各自创建 Agent 输入。系统通知只记录观察事件；
  `[Ding]…提醒您审批` 只记录原消息作为待回复目标。扫描器解析实时当前节点后才创建一个 OA
  执行任务；唯一当前节点才允许归并无 `taskId` 的催办，多节点或无法解析时保持待归并状态。
  OA provider 回执确认后，服务沿原消息的会话和消息 ID回复催办人；该回复与 OA 审批 Agent
  执行分开记账并按事件/Attempt 幂等。
- 扫描任务绑定通用 `dingtalk-oa-approval` 与适用的 Stardust 业务 Skills。

决策规则在审批 Skill 里，不在代码里：通用的完整决策表、
`information_completeness` / `rule_coverage` 评分口径、退回优先于评论搁置、拒绝前必须先查
`revert-activities`、`--remark` 必填，都在 `dingtalk-oa-approval` 中。OA 定时任务冻结绑定
`dingtalk-oa-approval` 以及 Stardust 财务、立项、合同、人员、考勤/出差、云资源六个业务 Skill，本机任务的
Prompt 另写入负责人的个人规则（只存在该定时任务的数据库记录里，代码默认值不含）。未被适用 Skill 明确引用的背景材料可跳过；Skill 明确引用的规则卡、登记表等规范材料必须读取，代码默认 Prompt 不得一概禁止读取参考资料。Consumer 按 live `processCode` 与表单事实
分类，交叉事项组合适用类别；财务规则卡仅约束登记的财务模板，且只提供判断标准，动作按通用决策表。适用业务 Skill 必须覆盖
当前事项的规则条件、例外、权限和动作映射，适用 Skill 完整覆盖时才允许 `rule_coverage=1.0`；其他情况
低于 1.0 描述证据缺口；只有低于 0.5 且缺失的是适用决策规则时，才满足通用质量门槛中的规则覆盖分支。符合门槛仍不等于自动批准、拒绝或升级：Consumer 必须提供完整候选，Audit 复核全部事实和规则，技术或材料缺口不得伪装成政策问题。申请人可补材料但不能替公司制定或关闭政策；材料尚未形成、但申请人仍可协调责任来源方补齐时，先正式退回等待补正和重新提交，不能仅因“当前暂时拿不到”就把本案是否继续交给 Derek。材料阶段完成后，若仍有独立规则、例外或授权边界缺口，再按 `needs_human` 处理。

历史 OA 复核按原动作时点还原证据：只使用能证明当时已可获得的文件/表格版本和评论。动作之后才更新的内容不能倒算成当时已有；只有版本历史或其他可核验时间来源能证明内容及时间，才可用于判断。时间或版本无法核实就标记为“未知”，不能据此认定申请人当时漏交材料，也不能把该轮计为有效退回。
核对历史退回次数时，第一次和第二次退回应一次列清当时已知、影响判断且可补正的缺口，并逐项提出可执行要求；之后新证据揭示的新问题在出现时说明，不能倒算成先前漏提。再读 DWS 完整审批详情的 `operationRecords[].remark`；简化 `approval records` 若只返回时间和动作类型，必须回到完整详情读取理由。DWS `REDIRECT_PROCESS` 记录只证明退回动作发生；若完整详情也没有理由，不能从动作类型推断实际退回内容。本地 Attempt 标记为 `skipped` 或 `uncertain` 不会单独抹去完整 DWS 流水已核实的退回；必须按来源时间及流程/任务信息，把该动作与 DWS 实际 `remark` 和本地回执逐条对账。反过来，只有本地 Consumer 候选或 provider 成功响应、没有 DWS 动作读回时，也不足以证明退回实际发生。Consumer 生成但被跳过或未执行的候选，在没有 DWS 实际备注或其他已发送证据时只是草稿，不证明申请人实际收到过该理由。确认实际理由后，还必须结合退回当时可见的证据、请求所指缺口及后续回应判断该轮有效性；本地 Attempt 状态本身不能决定该轮是否有效。无法确认时标记该轮有效性为“未知”，不计入三次有效退回，并在总退回数与有效轮数对账中说明；无法解释两者差异时，不得宣称拒绝门槛已满足。
审批服务无法读取已提交附件时，该附件仍视为已提交；权限、下载、解析、OCR 或客户端错误记录为审阅端技术阻塞，审批待办保持未处理。不能要求申请人重复提交同一文件，亦不能把此类错误退回计入“三次后拒绝”；只有独立证据确认源文件自身缺失、空白、损坏或缺页，才走申请人补正分支。财务付款审核必须按附件逐项留存文件名或节点 ID、授权读取结果、可读文本/页数或 OCR 结果及已核实事实；某一附件的读取失败只适用于该附件，不得据此声称其他附件也不可读。证据台账和结果摘要只保留与实质付款条件相关且由当前材料核实的事实；OCR 可读不代表字段与付款判断相关。复核历史退回或拒绝时，只报告有独立证据支持的实质缺口轮数；未计入轮次的历史原因不复述，也不以泛称间接指代。财务 Skill 同时约束付款证据判断：无法核验的验收不等于验收失败；供应商付款审核不评估、请求或在证据叙述/决策理由中复述签章相关栏位或历史要求，不将其作为缺口或拒绝依据；只核对交付、客户验收、结算、金额、收款方、发票及双方对实质条款的确认。已终结流程的历史复核仅产生 `no_action` 分析结果，不提出新的 OA 评论、退回或其他审批动作；重新提交时按新流程当前任务及独立证据审核。多个历史退回本身也不授权拒绝。

对供应商付款，财务 Skill 明确要求完全跳过签字、印章、签章栏的存在/缺失/可读性判断，并在任何内部或对外结果中省略相关观察。它们不是付款条件或补正缺口；此规则只覆盖 `supplier_payment`，不改变其他财务模板各自的证据标准。

供应商付款的第三方客户回复正文未取得，表示客户验收状态尚未核实；它本身不能证明申请人没有提交客户确认材料。只有独立证据确认申请材料确实缺少该项且申请人能够补正时，才向申请人提出具体补充请求。否则保持待处理，不因审阅方未取得第三方回复而留言索要，也不把未知转成拒绝。历史终态流程仅作 `no_action` 分析。

供应商付款复核适用通用 OA Skill 的三次有效退回规则。逐轮列出原退回问题、当时可读且来源核验的证据、申请人的回应及公平补正机会；每一轮都须证明同一项实质、可补正的付款缺口仍未解决。第四次到达 Derek 节点时，只有当前可读、来源核验的证据仍证明该缺口未满足，才可拒绝；不得仅按次数拒绝。读取失败、把“已回复邮件”误述为未回复、要求合同未规定的形式要求、已补正的缺口或不同问题，均不能作为有效退回依据。

六份 Stardust Skill 都只存在于运行时目录，没有仓库副本，按 `RUNTIME_ONLY_VERSIONED_SKILL_NAMES`
版本化，**不得带 `metadata.managed_by` 标记**——操作 Skill 目录会拒绝带标记的文件，定时任务
也就不能选择它们。Derek 的个人规则仅写在 OA 定时任务 Prompt，不得推广为公司规则。

## 外部动作幂等与依赖

系统执行器使用现有 provider client、ServiceMessageSender、external_action_key 和成功回执库。消息加载审核后的精确正文、目标及服务预先准备的后缀；不要求运行中的 Audit，也不向 Agent 暴露发送工具。OA 使用精确实例、节点、动作和参数，保存并回读节点结果。已完成动作不因后续通知失败而重跑。

原生群消息和直接用户消息在 provider 接受、但本地回执尚未保存的中断后，恢复读取原精确目标，并比对持久化审核正文、本人发送者、未撤回、dispatch 时间下界及唯一消息身份，匹配后补存回执而不重派。窗口读回使用 DingTalk `im.message-list.v1` 完整分页 contract，并验证时间范围、页数、失败数及结果条数；群聊按会话 ID 查询，单聊按稳定 userId/openDingTalkId 查询。若回复任务明确标记 `input_compacted`，触发消息 JSON 是溯源包装而非 `DingTalkMessage`；具名 reconcile 先按保留的原触发消息 ID 只读读取该消息，并核对会话、消息 ID、发送人和时间均与任务元数据一致，再进行原生完整会话消息核对。压缩输入不得导致普通异常重试或进入 dispatch。新 action attempt 在调用 provider 前原子保存 UTC 毫秒 dispatch 时间；旧无时区秒时间不能作为精确归属边界，保持失败并要求新的精确复检；provider 只返回秒时间时，同秒不足以证明消息在 dispatch 后，也保持失败并停止重发。完整窗口唯一精确命中恢复真实发送回执；完整窗口零个匹配的发送人/正文/引用组合记录为未送达，停止旧候选，仅允许确认无效果后由新候选重试；不完整窗口或多匹配保持失败且不派发。若原始发送在准备正文一致性检查处已失败，新执行先对原目标、审核正文和原始派发边界执行只读完整窗口复检。只有完整零命中才把原 action attempt 记录为 `confirmed_no_effect` 并解除动作键门禁；唯一命中记录 verified 回执。若源任务终止而当前待办归档为 skipped，历史中仍保留未送达事实，不投影为已送达。文档评论和 OA 评论恢复读取原 node/process instance 并保存只读诊断；尚无已验证的 author/time/body/stable-ID 联合归属契约时，不凭相似正文补记成功或再次发送。

历史 code 或 source_code 为 provider_risk_rejected 的同一业务对象不能通过换工具、渠道或执行代自动重放。保留拒绝来源和原始历史记录。native 引用回复仍使用原目标消息和准备正文的正向回读；空的有限消息列表不证明未发送。

当前 Attempt 的结构化运行结果 `code` 或 `source_code` 为 `provider_risk_rejected` 时，历史“重新处理”入口不可用，直接提交该入口也返回冲突且不入队；API、React 和原生 HTML 优先展示该原因。确需新的候选或执行范围时，应通过明确的本次任务和完整候选提交处理，不能用旧入口重放历史候选。对于后端允许重新处理的其他失败，`rerun_label` / `rerun_confirmation` 仍由同一业务对象的结构化历史提供；跨执行代的历史拒绝可使措辞显示“重新评估候选”，普通技术失败保留“重新处理”。只认确切的顶层结构化错误，不匹配正文、嵌套文字或其他对象；System 的既有历史拒绝限制和原始记录继续保留。
原生运行时的完整调用流保存在 Codex/Claude session 中。服务从当前进程的调用事件或原生 session
的精确调用范围读取它；`agent_run_events` 不再持久化调用事件或精简元数据，
参数、命令、工具输出和从原生记录提取的结果均按需读取。中断运行按原生 `task_started` / `task_complete`
边界恢复证据，既有投递核对和恢复契约保持不变。

Codex 角色使用原生 code_mode_only 和 V8 host。Consumer 的内建 functions 命令与补丁接口在当前 task/generation 的 `/tmp/ceo-agent-service-task-artifacts/<task_id>/<generation>` 目录运行，使用 CLI 自带 workspace-write 沙箱，命令网络关闭、额外 writable_roots 为空；MCP agent_cli 的 cwd 仍是服务源码目录。任务终态清理该任务全部代次目录；未到终态时保留工件供恢复及 Audit 读取。Audit 使用 read-only 沙箱并排除 functions namespace，只有具名读取。两角色的 `read_dingtalk_drive_file` 只下载调用方明确指定的已授权钉盘节点；`read_dingtalk_oa_process_attachment` 按审批实例和表单文件 ID 走 DWS 只读下载地址，不要求通用钉盘节点权限，也不执行授权写入；预览授权不再有服务工具：Consumer 在自己的 shell 里运行 `dws oa approval attachment authorize-preview` 为指定附件开通预览，再用 `read_dingtalk_drive_file` 或 `dws drive +download` 读取（Audit 没有 shell）（评论附件的高级版下载不可用时使用）；`read_dingtalk_oa_comment_attachment` 只按当前审批记录提供的流程实例、评论附件 ID 和文件名走官方 Premium 只读下载；`read_dingtalk_oa_comment_image` 读取审批记录 `images[]` 中没有 file ID 的钉钉图片 URL。该 URL 读取只接受 HTTPS `static.dingtalk.com/media/`，拒绝重定向，并限制下载时长和响应大小。所有材料工具均抽取可读文本；扫描 PDF 的无文本页和受支持的图片材料返回有界的精确源 base64、MIME 类型、PDF 页码与 SHA-256，由 Consumer/Audit 通过服务清单中的原生 `ai_services.ocr_document` MCP 读取。普通 Consumer/非 OA Audit 的 direct-only MCP 命名空间同时包含 `mcp__agent_cli` 和 `mcp__ai_services`。OA Consumer/Audit 把这两个已授权工具放进同一个 CodeMode 调用；OA Audit 的其他已配置 MCP 保持 direct-only，内建命名空间被排除，在 JS 内存中将附件读取结果直接交给 `ai_services.ocr_document`；不把 base64 打印给模型或要求它在下一轮重写。OA 专属会话合同变化只使 OA Consumer 一次性换到兼容的新 session；OA Audit 在 CodeMode 内排除 functions、image_gen、multi_agent_v1、web；其他 Audit 保持 functions 排除，所有任务的既有 session 复用路径不变。服务角色清单仍仅暴露 `ai_services.ocr_document`，其他 AI Services 工具不可见。工具审批配置只对经批准的 AI Services 工具逐工具应用，不改全局审批策略，也不放宽其他 MCP 工具；工具可用性以本轮真实调用为准。保留 OCR 页码、置信度与警告；读取失败返回技术错误，不能解释为申请人未提供材料。MCP 进程退出时清理目录。受控发送与 OA 等注册操作没有暴露给角色 MCP，仍由审核后的 System 执行。所有任务类型的 Consumer 都不得通过 shell、CLI、MCP、browser 或其他工具直接联系他人或执行影响他人的外部动作（包括消息、邮件和审批）；文档写入保持允许。Consumer 只能提交 typed candidate，由 Audit 审核，System 只执行审核通过的持久化方案。Claude Consumer 通过 CLI 沙箱内的 Bash 工作（Audit 无 shell），Friday 仍不具备角色能力。实际工具调用与文件回读证明执行，无工具固定合成业务比较仅证明判断。

`read_dingtalk_oa_process_attachment` 下载后的临时文件路径由材料层显式返回，不依赖 PDF/图片/表格抽取器是否自行附带 `path`。因此表格正文已成功抽取时，两角色都能缓存该准确来源；实例 ID、文件 ID、原字节摘要、大小上限、OCR 分页和 provider 失败语义保持原样。PPTX 使用独立的压缩/解压上限，并为嵌入图片提供按阅读顺序编号的逐页读取；`page_number` 返回来源幻灯片号、原文件摘要及有界 JPEG，由原生 OCR MCP 逐页识别，不把整份大演示文稿塞入一次工具响应。

Codex Consumer/Audit 显式启用原生 `features.code_mode.enabled`。普通 Consumer 和非 OA Audit 将任务 MCP 声明为 direct-only；OA Consumer/Audit 的任务读取与 OCR 由同一个 CodeMode JS 调用衔接。角色工具选择、Audit functions 排除、沙箱和路线不变。超长直接调用结果可能被原生运行时截断，因而不能要求模型复制其中的 base64。

OA Consumer/Audit 处理扫描页时，在当前 Native Codex session 的同一 CodeMode 调用里把读取结果的确切字节、MIME、页码和摘要交给 `ai_services.ocr_document`；只显示 OCR 文本及来源信息。非 OA Audit 和其他角色保持原有直接工具路径。嵌套工具目录、shell 清单和其他 Agent 的目录都不能单独证明本轮工具是否可调用；以实际调用结果判断依赖故障，继续区分审阅端读取失败和申请人缺材料。

Audit 不得仅因 OCR/转写来源字节由已配置 AI Services 处理而额外要求人工批准；材料敏感性、任务范围、明确禁止处理要求和真实 provider 错误仍按原规则处理。此项不放宽消息或其他对外动作的审核。

补充人工问题验收使用 `evals/consumer_audit_human_review/` 下的 manifest 和 `python -m scripts.eval_consumer_audit_human_review`，分别调用 baseline/candidate 的真实原生 Audit、完整角色指令和 wire schema，核对候选摘要、修订及审核结果并保存原文。输入是刻意构造且结构合法的候选，不证明 Consumer 实际产生了这些问题；结论仍需独立原文复核，不替代真实发送或生产验收。

需关注中的新审核问题说明适用的任务类别、尚缺的可复用规则和判断依据，并列出已审核的 task-class 规则选项。旧问题的历史状态与选项保留，但不因此重新激活；隐藏旧选项不表示已重新审核或业务完成。

组织架构审批中，空白或“业务调整”等笼统依据属于可向申请人或已核实责任人补齐的事实缺口：先核实接收方，再询问具体业务需要、受影响工作与人员、接收安排和汇报关系；保持待办并独立核验证据。不得仅因 Agent 未查到证据就升级 Derek；`needs_human` 留给真实政策/权限缺口或明确需要 Derek 作出的业务取舍。不得凭未核实陈述直接批准或拒绝。

微信发送前失败（`pre_action_failure=1`，已记录非空 `action_started_at`、从未真正发出）的投递，自动重试用尽后停在 `failed`，History 与重试接口都把它当作可手动重试的投递：详情显示「重试发送」，`requeue_expired_wechat_delivery_for_user` 同时接受 `skipped` 与此类 `failed`。独立 Audit 拒绝（`wechat_audit_rejected`）从未领取，没有 `action_started_at`，不在此列；重新入队后发送仍需通过原有的已批准 Audit 证据才能领取。

失败微信 Attempt 的 History `POST /api/console/history/{attempt_id}/retry` 是原生读回恢复入口，不要求用户回复确认卡，也不调用 Sender。它先校验当前已审核候选、审核、执行代、动作正文和 Delivery 证据完全绑定。`sent` 仅在 Reader 从准确账号、会话和动作开始时间之后读回唯一匹配的本人消息时结算为 `done`；明确 `superseded_by_newer_wechat_trigger` 链只有在最终 successor 的已发送正文被读回、旧正文在完整有限窗口内不存在且账号/会话/目标相同后才结算为 `skipped`。无法证明时保留真实失败并返回冲突；原失败 Attempt、Run 和动作历史保持不变。

需要重新执行内部失败的 Attempt 时，调用 `POST /api/console/history/{attempt_id}/rerun`，由原任务流程重新读取并审核当前输入；调用该 API 不要求 Agent 让 Derek 回复聊天确认卡。它与 `/retry` 的外部动作只读对账分开：存在投递或回执时不通过 rerun 重发。

## 用户反馈处理轮次

Consumer/Audit 的通用应用结果合同只在 Developer 注入一次，不在每轮 Task context 复制。Audit 的重复 Role Boundary 合并到“应用结果合同”段；Task 中显式自定义 Audit Rules、完整候选与 digest、当前事实、阶段和反馈继续保留。会话绑定、角色政策和 wire schema 保持不变，Settings 的当前预览沿用同一装配函数，历史视图继续读取原始已保存输入。

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
精确 revision/SHA、active config、匹配的启动 load receipt、场景验证和 health；
`runtime_config` 要求前后 config、目标 receipt、场景验证和 health；`code` 要求
本地 `main` 祖先 commit、测试、不同 PID 的重启和 health；`mixed` 同时满足相应
code 与 Skill/config 条件。`needs_human` 不接受 resolution receipt，项目保持 open。任一适用
条件不满足时，未满足条件的项目保持 `processing`。`POST /api/console/feedback/batches/{id}/resolve`
可用 `feedback_ids` 结案批内指定项目；没有该字段时仍要求并结案整个批次。指定项目必须通过
同一套关联、决策范围、证据、提交祖先、测试、重启和 health 校验；服务级队列积压由独立的
健康与队列投影报告，不作为反馈项目的结案条件。未指定项目保持原状态。只有所有项目都结案后，
批次才变为 `resolved`。后续处理应只选择仍处于 `processing`
的项目，不重复结案或重放已结案项目的任务或外部动作。所有关联、证据和结果仍须通过
Feedback API 持久化并回读一致。

Feedback API 是现有本地后端边界内的操作接口，供 Workbench 和仓库 Agent 共用；它不对
公网暴露，也不增加 feedback 专用鉴权或第二套 Agent 流程。

反馈 intake、列表和待处理数读使用实际 native Store。
首次 event upsert 在同一事务中创建唯一 `feedback_processing` Task 和对应 detail，
并依据源 event 当前 `resolved_at` 初始化 Task 状态；重复 upsert 按既有冲突规则更新源 event 并更新 Task.updated_at。新 detail 的 `resolved_at` 使用源 SQL 的 `coalesce(resolved_at, '')`，按原 SQLite 值和存储类型复制；因此空 BLOB 与空格等空白 TEXT 仍保留其原值。Task 状态空值分类沿用 SQLite `trim(resolved_at) <> ''`：新建时空值对应 `pending`，非空值对应 `resolved`。若更新后的 source `resolved_at` 按该表达式非空，则现有 Task 状态变为
`resolved`，Feedback detail 的 `resolved_at` 更新为该源值；若为空，则保留当前
Task 状态和 detail resolution timestamp。重复 intake 不改关联、证据或历史轮次。Task 持有处理状态与
生命周期时间，Feedback detail 持有关联、证据和当前 round。列表与 by-key 读仍按
最新已发送 token attempt 或 manual key 解析回复上下文，role/session 直接读取 native
Run 的 role；`codex_session_id` 仍取 ReplyAttempt 本身的既有 source context。这个回复上下文的 attempt/run 与处理 detail 中保存的 `attempt_id`、`agent_run_id`
是不同事实。pending count 沿用 unresolved、pending、无 reviewer feedback 且无
corrected reply 的原条件。

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

稳定 Message-ID 查找若在多个文件夹命中，属于确定性身份歧义：在只读状态定位阶段失败，不执行邮件写入，也不自动重试；超时、DNS 和连接错误仍可重试。身份问题解决后，才可重新处理该邮件动作。

Provider observation scan 在一个 SQLite 写事务内发布消息状态、authoritative folder 缺失项对账和
folder generation 时间。generation 按账号和文件夹单独记录，分类状态读取仍投影该文件夹最近一次
扫描时间；消息内容未变时不更新消息行及其索引。扫描成员先放入连接级临时表，再按
`(account_id, provider_folder_id)` 索引对账，避免大批动态 `NOT IN` 参数并缩短共享数据库写锁持有时间。

IMAP 移动模式是账号级显式配置：默认 `imap_move_mode=move` 并要求服务端支持 `UID MOVE`；
只有 provider 官方协议把 `UID COPY` 定义为移动时才配置 `copy_as_move`。运行时不按 hostname
自动推断，不对普通 COPY 执行 `\\Deleted`/`EXPUNGE` 补偿；动作完成后统一用稳定 Message-ID
重新定位，确认邮件只存在于目标文件夹并读取新的 UID/UIDVALIDITY。

**对账已失败的 move/trash 动作**（2026-09-25）：`python -m app.cli reconcile-email-actions --account <id>
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
空 `--after` 表示从最小 action ID 开始，适用于规范化后保存为整数的 Action ID；
分页游标与内部领取 API 在边界统一转成文本 ID，不改变记录的原始 ID。对账领取按同一
事务内读取的 Action 当前 `started_at` 精确做 CAS，兼容仍保存空字符串的失败记录。输出只有计数（按
move/trash 分：verified、in_source、elsewhere、not_found、ambiguous、error），不含邮件内容。

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

模型 ActionPlan 的退订任务修复扫描只读取候选分类身份，确认缺任务后才加载完整分类行；
每次修复后的复核按分类 ID 定点查询，避免每修复一个任务就再次扫描所有历史分类。

退订不做结构化审核。Email worker 直接执行兼容调用语义 `unsubscribe_email(task_id)`：一次调用完成整件事——
打开 ActionPlan 已授权的 entry，按页面当场呈现的控件操作，直到第一个终态页面，然后返回
outcome 和脱敏后的页面原文。它只接受 task id 这一个调用方无法伪造的参数，其余全部从 durable 状态
读出，所以没有 proposal 要抄写、没有 acceptance 要绑定、没有 Audit turn，也没有 continuation 要续。

Direct 浏览器提交退订表单前，只为唯一的空 `type=email` 字段填写当前连接邮箱账户地址。
已有字段值保持不变，不从普通文本字段名称推测身份；缺失账户身份或多个邮箱字段时停止提交。
填写后重新读取真实控件，并核对提交目标、方法、编码、提交按钮和全部成功字段（包括隐藏字段）
与填写前一致，仅允许该邮箱值变化，再使用新控件引用提交。旧结构化审核执行路径不隐式填写字段。

幂等性只靠 receipt：`email_unsubscribe_receipts` 里每个动作身份一条，已有 receipt 时再调一次
只会把它原样返回，不会重复退订。这取代了原先的 claim 租约、effect digest 链、owner fence 和
Consumer→Audit 往返——退订在真实世界本来就是幂等的。同理，有浏览器步骤但没有 receipt
不再升级为 needs_human，重跑一次即可。历史 run、session、step、receipt 和失败事实保持不可变。

已经创建的退订任务重建上下文时，必须使用任务中保存的不可变、脱敏 ActionPlan 载荷；浏览器入口
只在创建任务时从邮件正文中选择一次。Provider 后续重排正文、移除 header 或无法读取当前邮件时，
不得重新选择入口并改变已有授权。已有终态 receipt 的新 Consumer/Audit 执行只读取该 receipt，
形成新的明确成功 run，不重新打开浏览器或发送新的外部请求。

启动时的终态 receipt 投影恢复只重开“receipt 已落库、任务因旧生命周期超时而失败”的退订任务。`skipped_no_reliable_entry` 和 `skipped_login_required` 两种跳过 receipt 且任务已有执行次数的，不再重开：手动重跑因原邮件已不在邮箱而失败（`email unsubscribe source message is unavailable`）时，每次重启重开只会让同一批任务必然再失败一次。
恢复更新按不可变动作身份连接终态回执，读取任务持久化字段时限定任务表；回执投影只能收口匹配的失败任务，不发起新的退订操作。

每个邮箱账户有两个独立回溯窗口：Agent 默认 30 天，模型默认 365 天。`email-message-check-once`
对单账户的 DNS、连接、超时及 IMAP 会话中断先按共享指数退避间隔重试，最多 3 次；同轮其他账户仍独立扫描。仅当失败账户全是这类外部传输故障时，定时任务按短时外部依赖故障处理：保留该轮失败事实，依既有持续故障门槛决定是否写入 Attention，后续定时轮次继续恢复。登录凭据、TLS 配置、扫描结果错误和本地逻辑失败不按传输故障重试或降级；成功轮次仍用既有正式入口消解已恢复的故障记录。
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
`junk` 先由代码发现标准退订候选；分类 Agent 只返回本次候选序号，服务按该次调用的候选列表绑定精确 URL，并继续执行既有类别、索引和持久化校验。之后 unsubscribe 使用独立的系统直接网页流程，最终再移动到系统 Trash。连接邮箱 OTP 仅允许站点、收件人、挑战上下文和时间窗全部
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

Console 的”模型训练”提供版本化门槛配置及显式主模型开关。达标显示红点，用户操作开关才调用 runtime-mode API；缺失测量不能达标；expected mode/model 冲突返回 409。

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
`CEO_LIVE_EMAIL_LOCATOR_FOLDER`, and `CEO_LIVE_EMAIL_TEST_FOLDER`. UID/folder variables are only locator hints; the test restores the designated message afterwards and never configures SMTP or calls send/reply.

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
会绕过签名、反馈链接、幂等键和投递台账，
同一条修复又抄坏一次。执行轮发送时若正文不是服务准备的，按结果契约违规处理，走普通纠正轮
（`app/outbound_text_authority.py`）。提案正文里也不得自带反馈链接或签名，服务会自己追加。

所有服务所有的 DingTalk、WeChat 人员可见文本，在 provider 调用前都必须由
`ServiceMessageSender` 生成并持久化 `PreparedOutboundMessage`。持久化键是
`(channel, delivery_key)`；首次准备确定最终正文、feedback token 和后缀版本，之后的重试、恢复、
发送回读与 WeChat 撤回只能复用该记录，不得根据当前配置重新生成。provider adapter 只接收已持久化
的最终正文，业务模块直接调用原始发送方法会被架构测试拒绝。

DingTalk provider 将该最终正文通过 `dws chat +messages-send --as user --markdown` 发送，使 Markdown
标题、列表和反馈链接由钉钉按 Markdown 渲染。回复触发消息用钉钉原生引用回复（`+messages-reply --content`），正文同样按 Markdown 渲染：2026-09-25 在Derek 自己的会话实测，标题、加粗、行内代码、无序与有序列表都正常显示，相邻行的列表项也分行——DWS 帮助里「普通引用按纯文本解释」与实际不符，读回接口把列表拼成一行只是读回的呈现。推送横幅标题去掉 Markdown 标记。Consumer 起草的钉钉消息正文一律写成结构化 Markdown（加粗结论行、`- ` 列表、关键事实加粗，分段时用短`###` 小标题）；微信、OA 审批评论和邮件正文保持纯文本。`needs_human_reason` 用中文大白话一句话写清 Derek 要决定的那一件事和原因，最多100字；详细判断依据留在 `decision_basis`，不出现 rule_coverage、规则卡、partial、动作映射等内部词（Derek 2026-09-24/25/2026-10-19）。群聊、单聊仍使用稳定的 conversation/user/openDingTalk ID；
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
退订入口导航遇到超时时，若同一次导航已离开空白页，执行器直接读取当前页面并检查真实页面回执，不再请求退订 URL；若仍停留在空白页，则保留外部超时失败并沿用有界退避。该读回只恢复已经到达的页面证据，不清除未知执行围栏，也不把页面未读成成功。
退订浏览器仅把固定的内部失败类别投影到错误码；已识别的导航超时和页面状态缺失必须与兜底 `email_unsubscribe_browser_failed` 区分，错误码、步骤日志和页面原文里不得写入 URL 或凭证。结果的 `error_detail` 记下失败是什么：已识别的 `UnsubscribeBrowserError` 写固定枚举 `category=<名称>`（没有专属错误码的类别共用兜底码 `email_unsubscribe_browser_failed`，这里是唯一说明具体类别的地方）；未预期异常（兜底）会留下异常类名和截断到 240 字符的消息：URL、cookie/token/session 一类字段和 32 位以上的长串都先替换掉，再写入回执 evidence、这次尝试的 `audit_summary` 和任务的错误文本（`<码>: <类名: 消息>`，重试判断只看第一个冒号前的码）。所有结构化 `action_type=unsubscribe` 的失败都保持真实 `failed` 状态并留在 History，不进入管理者 Attention；Attempt 详情同样保留失败状态和原因，但不提供 Attention 处理动作。外部原因的失败（第三方页面拒绝表单或一键请求、页面操作失败或超时、导航目标无效、控件不可用，清单在 `app/external_failures.py`）继续按既有有界指数退避重试。Attention 分类通过任务错误中的固定 `category=` 校验分类，允许其后出现已脱敏 `detail` 和 `operation` 阶段后缀；浏览器/会话自身类别仍作为内部错误显示。兼容旧 Attempt 的 `failed_browser: category=<名称>` 格式。若已有执行标记不满足明确 retryable 状态及身份校验，或 retryable 次数已耗尽，服务以 `email_unsubscribe_outcome_unresolved` 记录未决结果；任务仍失败且不进入管理者 Attention，不把围栏误报为内部 `ValueError`，不自动重放，也不清除标记。只有终态 receipt 可收口该动作。
旧失败任务的 effect-free 自动重入队还要求没有 `email_unsubscribe_execution_pending` 标记；即使没有 claim、receipt、effect、浏览器 step 或 Agent run，这个标记仍表示执行需要对账，恢复筛选必须保留失败状态并禁止自动重放。
执行标记在退订请求前落库。浏览器返回属于瞬时外部失败清单的明确错误时，服务把当前标记原子更新为 `retryable`，只保留错误码、次数和原任务身份；下一次领取仍按本段的固定次数与指数退避执行同一授权动作。`retry_count` 记录已授予的重试次数；共享上限为首次操作之后最多 3 次重试，所以 count 为 3 时仍可执行第 4 次浏览器操作；之后的下一次本地领取在浏览器运行前以 `email_unsubscribe_outcome_unresolved` 拒绝。没有明确失败结果（例如进程中断）、非瞬时错误或无回执异常仍保留原 pending 标记，阻止自动重放；这与已归类的外部浏览器错误分开处理，不能把未知结果当成可重试失败。

通过正式 Reply Task 重试后，Task 的 generation 或 input version 可能前进。若原标记是已识别的 `retryable` 外部浏览器失败，只有 task ID、稳定 Action identity 和完整 TaskInput 摘要仍与当前任务相同，才允许将标记接续到新 generation/input version；每次重试继续使用原计数和预算。pending、unknown、非瞬时错误或输入摘要变化的标记不接续，仍需只读核对，不清除围栏或重放来推定成功。

`email_unsubscribe_browser_timeout`、`email_unsubscribe_browser_session_unavailable`、兜底码 `email_unsubscribe_browser_failed` 这三个瞬时失败会先退避重试；用完固定次数后写终态 `failed` 错误文本时，只有兜底码把 `task_error` 里已经带的 `: category=<名称>`（或未预期异常的类名与消息）一并写入，其余两个专属码保持裸码——`app/external_failures.py` 只登记了这个精确格式。控制台看到的失败原因同样有这道口子：一个还没读到任何页面、因而没有回执的退订任务（浏览器在读页面前就报错），观测时间线过去只给状态不给原因，2026-09-28 起把任务自己的错误文本一并投影进 `kind=unsubscribe` 的记录（`list_email_classification_observability` 和 `list_unsubscribe_states` 的 `error` 字段），控制台把已知的码译成人话，未知的码原样显示而不是不显示。钉钉 OKR 登录失效（`okr_headless_session_expired…`、`okr_authorization_required…`）同样是外部原因：只有人能重新登录，也保持 `failed`、不进 Attention；只有标准错误码 `authorization_required` 才能承载「需要人工」的授权决定，OKR 自己的错误码不行，所以它记为失败而不是人工决定。唯一例外是 `email_unsubscribe_receipts.entry_url`：该列按 Derek 的明确要求保存这次实际打开的完整私密 URL（含 query 与 token），用于人工复现同一个退订入口。写入前校验它的 sha256 等于 `entry_reference` 的摘要，因此不能与生命周期认定的身份漂移；它不经过 `assert_no_credentials`，因为被保存的正是那类 token。打开该 URL 会真实执行退订，任何能读这张表或这个页面的人都能替当事人退订。退订浏览器不再对页面发出的网络请求做 origin 白名单、跳转或资源家族限制。
同一 generation、同一 proposal revision 内，Consumer 或 Audit 每个 worker pass 最多跑 2 次 turn（`MAX_ROLE_ATTEMPTS_PER_PROCESS`）；仍是可重试的通用失败时该 pass 以 `failed_retryable` 结束，并给出 `retry_after_seconds`（共享指数退避，封顶 15 分钟，`external_retry.retry_delay_seconds`）。Consumer 与 Audit 的失败次数分别按持久化同角色 run 计算，首次失败后最多重试 3 次；同一角色 4 次连续失败后，在下一次 provider 调用之前结果为 `failed_terminal`。容量、连接、来源刷新和其他可重试技术失败都计数，另一角色的失败不占用本角色预算，同角色成功 turn 会重置连续失败计数。错误码与 Agent 原文 `source_code` 保留在诊断和结果摘要里，任务结束为 `failed`，不再重新入队。尚未实际执行的活动租约等待不产生失败 turn；授权缺失沿用不可重试终态。该角色预算独立于 worker/task attempt；各自最多 3 次 retry（初始调用之外），不会退款或跨角色共用。定时执行与 Email 延期不再把已失败的 Consumer/Audit turn 退款后无限重跑。内容反馈预算是业务修订循环，不属于技术 retry 预算。

`dependency_read_unavailable` 和 `agent_context_refresh_failed` 都计入该角色最多 4 次失败 turn 的预算，不形成无限外部等待例外。`agent_context_refresh_failed` 仍保留服务侧外部刷新等待；同码持久化 run 按共享退避逐步延长，最长 15 分钟。成功读取但无匹配数据、证据缺失/歧义或规则未定义走正常证据/业务判断，不能伪报依赖故障。权限申请后等待使用经审核的 Consumer purpose `retry`，规则如下。

当路由执行失败时，已分类的 `RoutedCodexExecutionError.retryable_external_dependency` 同时决定 AgentRun 与 Runtime Attempt 的 `retryable` 记录。能力失败保持终止，容量和传输失败继续可重试；没有失败类别的旧式路由错误沿用既有默认值。

## 周期性工作的归属

Derek，2026-09-18：**后台周期性工作必须是定时任务**，在控制台里可见、可开关。不允许把
干活的循环藏在服务进程里，也不允许靠手工 CLI 命令批量灌入。

`scan-task-sources` 只读 AI 听记。
- 并入定时任务「补查遗漏的钉钉消息和日历更新」（`recover-recent-messages`）：钉钉待办完成扫描
  （`scan_completed_dingtalk_todos`，Derek 2026-09-25：完成由新证据驱动，不单独检查）。它按页列出
  Derek 创建的已完成钉钉待办（`dws todo task list --status true --role-types creator`，每页 20 条、
  最多 30 页，找齐所有活动链接即停），列表里出现的已链接待办直接关闭本地 TODO 或业务 Task，
  完成证据记为 `dingtalk_todo:<taskId>`，不经 Agent。同一步先按已保存回执
  收口结果未知的待办创建（`reconcile_unknown_business_task_todo_creates`），这是投递记账，不是完成检查。
- 任务长期记忆不再由服务写入（Derek 2026-10-24）：Consumer 在回合内自己调用 `memory_write`，服务没有任务记忆队列；
  见 `docs/architecture.md`「任务长期记忆」。
- 定时 `scan-meetings-once` 只发现符合条件的已结束会议并报告新建会议任务数；会议结论 Memory 写入器已退役，不会由此服务命令触发或计数。
- 仍为常驻循环的只有 `meeting-delivery`：它只投递已审核通过的会议结论，10 秒一轮就是它的意义；
  以及备份、cron 调度/派发、探针等不产生业务判断的基础设施。

Agent Cron Scheduler 在保存触发记录时遇到 SQLite `BUSY`/`LOCKED`，且存储层有限重试仍未取得写锁，
会保留尚未保存的触发点，等待 1 秒后重试。该冲突不会生成失败运行记录或使 Service worker 退出；
触发记录仍由原有原子校验与去重事务创建。

任务卡死的回收由服务启动恢复、Consumer 的 stale-age 检查和 Dispatcher 的过期租约处理共同负责；
不再依赖一个隐藏的业务级维护循环。`recover_stale_runtime_attempts` 与 `recover_expired_terminal_task_runtime_attempts` 由常驻组件 `runtime-attempt-reclaim` 每 5 分钟运行，回收运行期间过期的租约，使其不再卡住部署前的静默检查。

启动恢复 `reconcile_done_reply_tasks_with_failed_current_run` 会校正当前执行代最新 run 为
`failed`、但 task 投影误为 `done` 的回复任务，并保留成功外部动作和已收口错误的排除条件。

## 进程、租约和恢复

- 生产入口是 launchd 管理的 `com.ceo-agent-service.main`，由 supervisor 管理 worker 和 audit-web。
- 同一 `conversation_id` 同时只能有一个执行 Agent 持有 Codex session lock。
- 每个执行/审核 run 都有独立 lease、revision 和 transcript 范围。
- Dispatcher 对“租约已过期但 owner 进程仍存活”的来源不重新领取，以免重复执行。处理函数已经返回、只是以错误结束（记下 lease error）时，dispatcher 会把该租约标成“owner 已不在”（`owner_pid=0`、清空到期时间，按 owner 与 generation 围栏），来源随即按常规规则可再领取。
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

- 会议总结在 Agent 决策前，以日历组织者的完整名称通过当前 DingTalk 组织目录解析稳定身份；只有唯一精确姓名或昵称命中才回填组织者及同名唯一参会人，供来源和收件人身份核验使用，不因此决定业务收件目标。模糊、多候选或冲突身份不回填。日历中缺少稳定 ID 的其他参会人也按同一规则做唯一姓名/昵称补全。服务用当前 DWS 群搜索格式检索会议标题和摘要正文里的业务词，再读取候选群近期讨论，把去重后的实时候选及讨论片段交给 Agent；候选仍需核对相关讨论、业务承接和参会人与完整群成员的匹配，不能仅凭群名、词项重合或参会人覆盖发送。群搜索失败或结果不完整时任务重试，不将依赖故障说成“找不到群”。业务总结优先发到按相关讨论和参会人成员匹配选出的群；完整搜索后没有合适群时按「会议无合适群时的收件人」选择单聊对象。普通业务内容无需逐一核验群成员披露授权。裁员、减员、人员优化及其他人员敏感详情必须从业务群正文和标题中拆出，作为独立私信发送给身份、当前职责和该具体内容披露授权均有实时证据的个人；HR 群或高参会人匹配度不构成合并敏感内容的例外。没有经核验且获授权的个人时，不发送敏感部分并记录未解决事项，不私聊本人兜底。进入投递后复用同一个持久化投递键：钉钉发送使用由该键确定的 UUID，provider 成功返回会立刻写入同一键的回执。服务重启后，恢复的 worker 先复用回执；若进程恰在 provider 接收后中断，使用相同 UUID 继续投递，provider 的重复 UUID 回应视为原投递已送达，不能产生第二条群消息。

会议消息已有不可变发送回执时，恢复日历摘要先读取原事件；若描述完整包含同一冻结摘要正文，则记录
`already_present` 并结束摘要阶段，不再写日历或重发消息。否则仍按现有标记块更新，并要求原事件读回
与写入内容严格相等。

- 会议跟进的钉钉消息标题取 Agent 按实际受众核验的 `message_title`，原会议标题只有在该受众有权获知时才能沿用，不取群名或私聊收件人名；该标题只到达推送横幅和会话列表，聊天窗口里看不到，所以正文首行以 Markdown 一级标题（`# message_title`）写已核验标题，第二行以斜体呈现时间副标题（`*时间：…*`），二次总结另起斜体说明行。时间按 Asia/Shanghai 呈现：Minutes 交过来的时间戳是 UTC，直接格式化会把上午八点的会写成零点。Agent 的 `final_message` 只承载结论、行动和待确认事项，不重复会议标题和时间。正文用 Markdown 结构化：小节用加粗标签（如 **结论**、**后续行动**、**待确认**），多条并列事项用 `- ` 列表并在列表前空一行；钉钉这些消息以 `--markdown` 发送，加粗、标题和列表都按 Markdown 渲染（Derek 2026-09-24 确认渲染）。发送目标仍由已核实的受众决定，标题不参与路由或投递幂等键。
- 这一恢复规则适用于所有任务：普通服务重启只释放已经停止的 worker 租约，保留同一任务的执行代次、已准备消息和外部回执；恢复 worker 从这些事实继续。若运维明确完成了运行时/路由修复，则服务修复重试创建新的 execution generation 和新的 session 绑定，但仍复用同一业务对象及外部动作幂等事实；用户业务反馈重跑则按反馈闭环复用兼容 session。
- 外部动作的 operation、target 和 provider result identifier（若 provider 返回）会保留用于去重；缺少标识属于 provider/Agent 失败，不转换为额外状态。
- WeChat reader 由独立 launchd job 自动保持运行；worker 连续三次 IPC 超时后主动 kickstart 该 job，处理“进程仍在但 IPC 已卡住”的情况。worker 只恢复 reader 进程，不启动 WeChat 主应用，也不重放消息。
- 后台 WeChat 发送序列等到电脑连续无人操作满 30 秒才开始（`CEO_WECHAT_SEND_IDLE_SECONDS`，默认 30）；等待发送窗口没有 120 秒截止时间。发送 IPC 在等待期间保持连接，`SystemExecutor` 持续续租该已审核执行的 claim，避免长等待被判为失败或被另一执行者接管。操作中每次点击、每个字、按回车前都检查是否有比 Sender 自己最近一次事件更新的输入；有就立即暂停：清空未发送草稿、切回原前台应用，再重新等满 30 秒后从打开会话重来。独立的只读目标确认保留原有限等待；用户在 Console 主动触发的发送不等待也不暂停。连接不可用、发送开始后的中断和回执判定仍沿用原有失败/不确定状态规则。
- WeChat Sender 的被动 readiness 只有在 Accessibility 树含有发送导航所需的搜索框或会话列表控件时才返回 `ready`。仅检测到进程或窗口不足以证明登录会话可用；窗口存在但缺少这些控件（例如 QR 登录界面）时返回 `wechat_window_unavailable`。此检查不会激活、登录或发送。
- WeChat sender loop 在 `auto` 模式处理已审核的 `ready_to_send` 项时，以及批准、未执行失败重试和已确认无外发回执后的重试 API，都必须从同一当前 Task 代次、候选、Audit 和 action identity 读取已经持久化的预备正文，并逐字匹配 delivery。旧 delivery 只在审核门禁通过且最终正文相同的情况下，才可从 `wechat:{delivery_id}` 预备键恢复；不得重建正文。无法精确恢复时，重试 API 必须在重新入队前返回内部错误且不调用 Sender；这类内部错误由正式重试接口处理，不要求用户通过回复确认卡提供材料。每轮成功完成恢复检查和队列检查后，才将此前同组件错误标记为已恢复；抛出异常的轮次不会清除错误。该收口仅更新错误的恢复状态，不代表任何消息已发送；发送事实仍以 delivery 和 provider 回执为准。
- OKR 无头来源启动使用进程锁；并发调用者取得锁后必须再次读取共享缓存，复用前一个调用刚刷新的认证信息，不能重复启动浏览器或把正常刷新误报为锁超时。锁等待上限为 130 秒，worker 运行上限为 220 秒，调用方上限为 240 秒；worker 超时后会先终止并回收自己的 Chromium 进程组。认证刷新通过 `app.service_browser.launch_service_chrome` 使用每日 Chrome cookie 副本启动真 Chrome，不再启动或清理独立的 bundled Chromium 子进程。
- OKR 无头来源在请求业务 API 前校验新捕获认证的有效期。专用浏览器会话过期时返回明确的 `okr_headless_session_expired` 服务错误，不得继续请求并把认证失败误报为周期不存在。
- 个人季度不存在由共享 source 的 `MissingOkrPeriod` 类型表示：只有 provider 整数 code 0、完整且结构有效的个人季度列表及有效季度身份才能产生该结果。服务命令须指向 `scripts/dingteam_okr_headless_source.py`；旧 Skill 浏览器读取入口虽能读取已有目标，却未输出个人季度缺席收据。Codex 子进程保留服务进程的 `CEO_OKR_LIVE_SOURCE_COMMAND`，使 `agent_cli` 不会回读旧 `.env` 命令。headless wrapper 输出绑定请求 userId/periodLabel 的 `availability.status=goals_not_established`、UTC 采集时间、providerCode 0、periodsComplete true 和真实季度列表，不伪造 `processed` 空数组。认证准备、HTTP 401 和未分类共同来源失败保持 shared 技术失败；可明确归属单次成员读取的 HTTP 或格式失败使用 member 范围。错误按结构化 scope/code/detail 回到 source adapter 并抛出 `OkrLiveSourceError`，不靠错误文字猜缺目标或只用失败人数判断共享认证。依赖 wrapper 部署前必须核验本机共享 SDK 类型与打包 source 合同一致；保留共享目录无关改动。
- 过期的单人周 OKR 分析如果已经被同一管理者更晚周期的成功分析覆盖，启动恢复将旧作业置为 `completed`，记录 `superseded_by_later_completed_week` 并清除租约。它不再显示为当前 `running`；若相同自然键缺少缓存，正式分析流程仍会重新领取。
- 周 OKR 分析任务每次获得新的租约时使用新的 runtime 执行代次。单次执行中的结果格式修正保持有界；已终态的旧代次不得阻断同一分析任务在后续租约中的重新执行。
- 周 OKR runtime 租约过期并已记录为技术失败时，启动恢复必须同步关闭仍为 `running` 的父分析任务；父任务不得在没有有效 runtime 所有者时继续显示执行中。
- 所有需要 `BEGIN IMMEDIATE` 的 Store 写路径统一经过同一个有界重试事务。短暂的 SQLite 写锁在 Store 内等待并重试；只有超过上限的持续锁才上升为服务错误。队列 claim、反馈批处理和恢复路径不得绕过这一规则。

### Cron trigger 与 Consumer Dispatcher

Agent Cron 保存任务定义及其结构化 Skill refs、首选 Runtime route/model/options、工作目录、Cron
和时区。启动或主机恢复后，Scheduler 为每个任务最多补一个最近的已错过触发；更早的错过点合并，
任务创建时间之前的触发不补造，已有触发仍由唯一约束去重。之后恢复到下一个未来触发点。
手动运行只追加一次 manual trigger，不改变 `next_run_at`。正常到期时若上一轮关联 execution 尚未
终态，本轮以 `skipped` 和稳定原因结束；补跑则保留最近错过时间点，等上一轮终态后再提交。
若 Scheduler 派发时首选 Runtime 因 provider 暂时不可达而暂停，它不创建 `skipped` 运行，也不推进
该计划；后续 tick 在路由恢复后按原计划时间派发一次。多个错过点仍合并为最新一点。静态路由配置
错误继续进入 `skipped` 和 Attention。
seed 只迁移精确匹配旧仓库默认周期和时区的现有任务；
用户改过周期或时区时保留原值。配置变更生成新版本，不改写历史运行快照。

`scheduled_task_runs.snapshot_id` 必须引用 `scheduled_task_config_versions` 中的非空 `snapshot_json`。
完全相同的冻结配置只存一份；修改任务会产生新的配置版本，已排队的运行仍引用原版本。
Agent 执行使用 trigger 快照中的精确 managed Skill revision。该 revision 即使未载入当前进程的启动快照，也从不可变 revision store 按 ID 读取；缺失、Skill 身份不匹配或明确禁用仍拒绝，绝不替换为最新版。这样重试可以复用原输入绑定的 Skill 正文。

一次正常到期分为两个可恢复阶段：`scheduled` adapter 领取 `scheduled_task_runs.pending`，在一个
事务中创建或复用唯一 `reply_tasks.channel=scheduled` 输入、保存 execution link，并把 trigger
标记 `dispatched`；`scheduled_execution` adapter 再领取该未结算 execution source，按派发时冻结的
prompt、Skill protocol、首选 route、model、thinking 和 workdir 启动 Agent；首选线路失败时按统一
fallback 换到其余配置线路（Derek 2026-09-24，此前定时任务固定单线路、从不 fallback）。managed Skill
使用精确 revision；执行前若首选及其余线路都不可用、该 revision 或工作目录已经不可用，execution 以
`skipped` 收口并产生 Attention，不把业务结果写回 trigger。Scheduler 在派发前发现全部线路不可用时
同样记 `skipped`：配置性原因（未配置、缺能力、认证暂停）每次写 Attention，
provider 暂时不可用造成的路由暂停只留下 run 记录和路由暂停状态，不逐次写 Attention。

创建 Audit 输入时，编排器从当前代次、当前 reply task 且已完成的 Consumer 运行中（包括此前反馈修订的运行），读取
`agent_cli.consumer_document_write` 已完成调用的结构化返回，或 Claude 轨迹中单个文本块／字符串承载的同一 JSON 对象，并投影文档身份、provider 写入结果摘要和
字节数；不把报告正文重复注入。无匹配返回时 Audit 明确看到空列表。该证据只证明服务工具
返回了什么，不证明文档已被独立核实或报告事实正确。该交接适用于 `channel=scheduled` 的
Agent 任务，不增加日报或周报专属规则，也不改变 Audit 的独立审查标准。
本轮交接与真实写入回执形状的验证见 `docs/archive/records/scheduled-audit-receipt-validation.md`。

服务命令任务（`scheduled_tasks.command` 非空）只有第一阶段：`scheduled` adapter 领取 trigger
后，在同一个 claim 内直接运行服务命令目录中的实现（例如 `produce-once` 是 DingTalk 消息
producer 的一次增量读取，与 `app.cli produce-once` 相同），成功后把
`execution_kind=service_command`、`execution_id=<命令名>` 链接到 trigger 并标记 `dispatched`。
服务命令本身不经过 Runtime、Skill 或 synthetic scheduled Agent；只有命令发现真实对象后，
对应的 reply、meeting 或 work-summary Consumer 才会处理业务队列。正常触发遇到上一轮未终态时，
记为 `skipped`（`scheduled_task_previous_execution_active`），不并行执行；补跑时间点则等待上一轮终态。
命令抛错时 trigger 以 `failed` 和
`scheduled_task_service_command_failed: <原因>` 收口；除依赖短暂不可达（DNS、网关繁忙、超时）
以外的原因每次写入 Attention。依赖不可达按持续时间判断：同一任务连续失败不足 15 分钟时只留在
trigger 记录里，一次外部抖动不会在每分钟的命令上刷出成串同样的条目或 macOS 弹窗；连续失败超过
15 分钟则写一条 Attention（该条未解决期间不再重复写）并只发一次失败桌面通知，下一次成功自动
标记为已恢复并发送成功通知。因此一次两小时的 DNS 中断是一条 Attention 和一次失败通知，而不是
零条或一百二十条；恢复通知仍会告知命令再次成功。命令幂等，claim 丢失后的
重领会直接重跑。命令名不在目录中时 Scheduler 在派发前以
`scheduled_task_service_command_unavailable` 跳过。服务命令任务不经过 Runtime、Skill、
Consumer 或 Audit，也不产生 reply task、agent run 或 reply_attempt。

定时服务命令结果保存并完成 trigger 后，成功或失败都会发送桌面通知；Agent 形式任务在
Reply task 的 `done` 或 `failed` 终态保存后发送通知。标题包含任务名，正文包含命令结果摘要、
Agent 结果摘要或失败原因。等待重试、跳过、仍在执行及单纯派发不通知；通知不改变持久化状态。
终态保存后，通知以尽力投递处理：所有桌面通知后端均不可用时记录错误，不反向改变 Task 或 run 终态。

`wechat-produce-once` 默认每五分钟对唯一就绪的微信账号跑一次 producer。Reader 的可用性是
`wechat.reader` 健康事实而不是 trigger 失败：没有就绪账号时命令返回跳过摘要；Reader IPC 连续
失败 3 次时请求一次 Reader 重启、把健康标为 degraded 并只记录一条
`wechat_reader_unavailable`；App Data 权限缺失只记录一条 `wechat_data_permission_required`；
下一次成功读取恢复 healthy 并清除上述报告。其他异常才是命令失败，trigger 记 `failed`。
独立 Reader helper 的账号发现只检查 Reader backend 支持的 `db_storage/message/message_[0-9]*.db` 分片；不会递归扫描整个 `db_storage` 树。
Reader 会把 WeChat 共享文章的标题、摘要和 URL 解码为文本，因此链接消息进入与普通文本相同的回复队列；图片目前只保留消息类型元数据，未提供媒体文件或视觉输入时不会被当作可读文本触发。
单聊只有非空的可读入站文本可触发回复。producer 在 Reader 扫描时将相邻消息间隔不超过五分钟的候选合成一段，等该段最后一条消息已安静满五分钟后，才以最后一条创建回复任务；未结束的段保持在读取水位之后，下次扫描继续合并。已完成的较早一段可先入队，群聊 @ 规则不变。这样处理时间按实际看到完整安静窗口计算，不依赖任务在下一轮扫描前仍处于 pending。
通过 History 发起的 WeChat 手动 rerun 会在决策提示中明确标记为 Derek 的主动重跑；消息时效和后续上下文仍会提供给 Agent，但不能单独把该 rerun 强制收口为 `no_reply`。

独立微信 Consumer 的 `CodexDecision` 不是统一 Audit 候选。完成时仅对持久任务
通道为 `wechat`、包含有效 `action` 且不含 `outcome` 的旧契约免于候选采纳；
代次、角色和运行终态仍需匹配。原决策结果保留在原生轨迹。新版 proposal 及其他通道
继续执行完整候选校验，独立微信既有交付检查和外部动作拒绝不变。
微信 Consumer 和新增的独立 Audit 均在 model-only 环境运行：Codex 没有原生命令和 MCP 工具，Claude 没有工具，Friday 不可选。Consumer 写出 delivery 后，服务先持久化带尾缀的最终正文；Sender 循环读取最新同会话消息，Audit 以原始触发消息和精确 delivery 目标、正文为候选进行审核。Audit 得知最终正文含服务按既有策略追加的签名，仍审查实际完整正文，但不把这段签名误认作 Consumer 文本缺陷。Audit 运行与 Consumer 父运行同属当前代次，模型输出为批准或拒绝、候选 SHA-256 及摘要。数据库把已完成 Audit 的原生结果核对后，将运行 ID、摘要和决定记入 delivery；自动和人工发送入口及数据库 `claim_wechat_delivery` 均只领取批准且正文、目标、任务输入未变的记录。拒绝关闭记录，技术失败不授权发送。可重试的提供商容量失败保留同一 Audit run 和路由尝试，短暂停租后继续/恢复，不另建候选；重启恢复也不会把仍绑定当前待发送 delivery 的运行误判为孤儿。不可重试失败有界终止。原有自动/人工确认模式、Sender 目标绑定和回执核对保持有效。

微信专用 Runner 在首轮 Developer 指令中给出有效的最小 `AgentEnvelope` 字段示例；首次格式错误仍由原有同 session 校验修正处理，业务结果仍由完整模型校验。原生 output-schema 开关、路线、工具、会话和交付阶段均未因此改变。

同一 Dispatcher 还通过独立 adapter 领取普通 reply、meeting、work summary、OKR review、
当前 BusinessTask 的 DingTalk Todo outbox。adapter 只读写各自既有事实来源，并统一 claim generation、lease、唤醒、
公平性和容量；Consumer 保持领域边界。主动唤醒之外的有界等待只用于跨进程写入和异常恢复，不是
用户配置。Status 为每个实际 adapter 展示 pending、oldest、running 和 latest error；scheduler
进程/扫描健康与 scheduled run 的业务结果分别展示，空队列不会制造 Agent run。

默认业务生产任务通过稳定 migration key 幂等 seed，共 14 个（11 个服务命令；CEO 日报、CEO 管理周报和 OKR 管理周报是 Agent 任务）：邮件、钉钉消息、钉钉日历邀请、每小时
`:30` 的“恢复近期 DingTalk 消息”、会议、微信 reader、OA、会议行动项、Chrome 登录态同步、听记权限申请、CEO 日报、CEO 管理周报、每周 OKR，以及每天
`20:00`（`Asia/Shanghai`）的 AI 听记同步；清单以 `app/agent_cron/seeds.py` 为准。服务命令任务不创建 Agent run，旧的 producer timing loops
已移除。以 Agent 形式创建的旧 `dingtalk-message-check-v1`、`wechat-message-check-v1`、会议、OA、工作来源和
`ceo-minutes-sync-daily-v1` 在启动时原地转换为命令形式（保留名称、Cron、时区和启用状态，已删除的不动）。
其中“会议行动项”命令还会在同一轮只读发现管理周报、项目管理部周报和部门产研周报；报告按文档内容摘要建立带
文档 ID、链接、统计周期和内容 digest 的 `work_summary` 输入，重复扫描同一 digest 不重复入队。较大输入的逻辑 JSON 字段使用带版本标记的无损压缩 BLOB，最终 Tasks 向量保留其 SQLite 值类型；Task Agent、语义导入、项目修复和 Attention 投影在读取边界解码。副本回放以当前投影返回的精确 TEXT/BLOB 值做 CAS、通过当前 Tasks 写入器沿用既有 codec，准确来源／状态／正文指纹及 lease 校验不变；小 payload 保持普通 JSON。`python -m app.work_summary_payload --db <database>` 只对仍需保留正文的非终态行按小批事务压缩，可续跑；done/skipped 行继续使用终态精简，不删数据或自动 VACUUM。
新安装创建的全部默认任务都是暂停状态，用户配好连接器后自行启用；seed 从不改变已有任务的启用状态
（Derek 2026-09-23）。AI 听记同步不再需要 Skill 判断：分页读取
摘要与逐字稿、写入本地归档、维护内容游标都由 `app/minutes_sync.py` 确定性完成，时长不足五分钟的
会议直接跳过。成功运行的归档命令单行结果摘要会持久化到 scheduled run，并显示在定时任务运行记录中，包含
`discovered`、`synced`、`skipped`、`permission_requested`、`permission_pending`、`failed` 计数及可操作的跳过明细。
DingTalk 对无有效转写文本的摘要返回终态 `textGenerationInfo=SKIPPED/NO_VALID_TEXT` 与上游跳过的视觉摘要；同步器将其识别为空摘要，继续归档已有逐字稿。未知或不完整响应仍保留为单项失败。
History 也显示定时触发（Derek 2026-09-25，类型「定时命令」/「定时任务」，规则见 `docs/architecture.md` 的
「History 语义」）：任何定时任务的失败触发（之后同一任务成功过一次即为 `recovered`）、Agent 形式任务因不可用
被跳过的触发，以及结果不交给 Agent 的服务命令（听记同步、听记权限申请、OKR 周报）的每次成功运行；把结果交给
Agent 的生产命令成功时不进 History，它们排入的每一项各自是一条 History。邮件 provider 动作同样进 History
（类型「邮件动作」），失败口径与 Attention 相同。

History 的回复类记录以 task 为核心：列表行带 `task_id` 与 `run_count`，行内链接指向
`/history/tasks/<task_id>`；该页只读取 `tasks`、`runs` 和 Attempt 投影，列出 task 的全部 run
（含历史执行代）与 Attempt，Attempt 详情页保持原样（规则见 `docs/architecture.md` 的「History 语义」）。

微信 History 的任务完成与消息投递分别投影：同一任务、会话和当前执行代的最新投递为
`failed` 或 `send_unknown` 时，失败 Attempt 继续在列表筛选、详情、状态计数和图表中显示
`failed`，不能被候选任务的 `done` 掩盖。旧代、其他会话和已有后续成功投递的失败不覆盖
当前状态；旧 pending Attempt 对应已完成任务仍显示 `done`。这些读取不改写历史记录或投递
状态，也不派发重试。
该投递优先规则只覆盖物理 `send_status=failed` 的 Attempt；非失败历史行继续按原任务投影，
列表、详情、队列计数和图表不得各用不同的覆盖条件。
听记权限扫描在打开后台后显式查询最近 30 天至次日的历史记录；后台默认只筛当天，
空表不能直接归因为账号缺少管理权限。申请按持久化的 `requested_ids` 去重，
只有听记页面读回已申请状态才计为成功。
退订页返回的固定外部拒绝类别即使附带 `;operation=...` 阶段信息，也保留失败历史、
归为外部依赖故障而不计入待工程修复的 Attention；浏览器/会话自身错误仍计入 Attention。
## Chrome 登录态副本（系统服务）

需要登录的无头浏览器任务（退订链接、听记权限申请、Dingteam OKR）不各自重新登录：服务每天 `06:00`（`Asia/Shanghai`）运行“同步 Chrome 登录态”（`sync-chrome-cookies`，定时任务，`chrome-cookie-copy-daily-v1`），用 SQLite 备份接口把 `~/Library/Application Support/Google/Chrome/Default/Cookies` 复制到服务数据库旁的 `chrome-cookies/Default/Cookies`，再按明文 `host_key` 删掉 `CEO_CHROME_COOKIE_DENY_DOMAINS`（逗号分隔的域名，含子域名）里的银行、券商和支付类域名，其余全部保留供各任务复用；这份名单为空时命令拒绝执行。整个过程不解密任何 cookie，也不碰钥匙串。 Dingteam OKR token 缓存读取时先验证 JWT 有效期；过期或将过期的缓存不会返回，调用者在锁内重新读取缓存后再尝试既有 Chrome/本机 SSO 刷新。新捕获 token 也先验证再写入；认证仍不可用时保留认证失败。

所有任务只通过 `app/service_browser.py` 的 `launch_service_chrome` 启动浏览器：真 Chrome、始终无头、指向调用方自己的资料目录；启动前把最新的副本放进该目录的 `Default/Cookies`，并去掉 Playwright 默认的 `--use-mock-keychain`（否则 Chrome 解不开复制来的 cookie）。这样由真 Chrome 自己读“Chrome Safe Storage”钥匙串项，不会弹授权。副本是快照，会话过期后等下一次同步；不连接你正在用的 Chrome（Chrome 136 起默认资料目录不允许远程调试）。

另有一个每天 `19:30`（`Asia/Shanghai`）运行的“申请读不到的钉钉 AI 听记”服务命令：读取听记管理后台，逐条在听记页面提交访问申请，并以页面读回状态计数；对方批准后，`20:00` 的归档任务会读取内容。该后台由服务的共享无头 Chrome 打开（`app/service_browser.launch_service_chrome`），带的是每天复制的 Derek 本人 Chrome 登录态；这份副本能把他认到「选择你管理的组织」那一步，再由代码选中组织列表里的第一个组织（Derek 2026-10-23：不写死公司名）进入控制台。每次启动都会用当天的副本覆盖 profile，控制台自己的 `access_token` 不会留到下一次，因此不会跨运行保存该令牌。登录页加载超时以及组织选择或其后的控制台跳转超时统一按登录失效处理，任务保持失败；组织选择和控制台跳转各有 10 秒上限。服务会直接启动用户的 Google Chrome 并用新窗口前置打开听记后台，供用户完成正常登录或检查组织选择，而不是只在后台留一个标签。登录后仍需让每天的 `sync-chrome-cookies` 将最新登录态复制到服务 profile，后续任务才能继续；没有通过扫码或程序保存凭据来绕过登录。申请命令结果摘要同样持久化在 scheduled run，并显示在定时任务运行记录中，包括本次发现、已申请、已可读、申请人未解析和失败数量。

定时任务固定 Agent 案例在独立离线原生 CLI 中运行。评测 Consumer、Audit 和修订共享同一传输字段、标签与身份投影定义；业务规则仍来自各自精确 Skill 快照。冻结案例和评分函数，不改标历史失败；首轮、一次审核修订、实际正文/动作草稿与外部发布分别记录。参见 [Agent 验收记录](archive/records/scheduled-task-generic-agent-validation.md)。评测协议不改变生产 Consumer/Audit 契约。

OKR 报告的应用层只读取明确群名的稳定名单和明确人员/周期的实时数据及来源回执。每人一次读取、单次最多 240 秒，模型可见真实步骤结果；纯本地 `ceo-weekly-okr-report/scripts/report.py` 按稳定 userId 准备输入、计算原有精确公式并渲染。Agent 保存成员产物以恢复进度，发布管理报告、成员子页并提出群摘要消息。恢复的 Consumer turn 先读 Audit 与当前执行代的任务工件，再复用同一 scheduled run 中仍存在的名单和成员来源回执；由于没有独立摘要查询接口，刷新缺失成员来源用于发现来源变化。prepare 对照新摘要复用未变化成员的完整分析，只重做来源变化或分析未完成成员；同一 scheduled run 内已有成功回执的来源不重复读取。已保存的成员读取回执只在 userId、姓名、职级、钉钉身份和周期完全匹配时复用；回执文件缺失或明确失败时，只补读该成员，保存新回执后再 prepare。先前曾成功读取不等于当前仍有可验证回执；`source_failed` 回执不会缓存复用。来源摘要相同但带 `analysis_failure` 占位的文件不算完成、不复用。OKR Consumer 同一轮必须继续完成所有可分析成员，不得主动以部分分析结束；只有实际 turn 中断才保留有效产物并返回可重试的不完整结果。逐 KR 检索独立证据，零进度或未找到佐证仍按既有评分规则完成并说明证据缺口；领导力和文化分数须由各自行为证据支持，禁止跨维度借证，Audit逐人核对七项维度证据和分数。未评分成员保留已知职级与逐人来源缺口，不生成分数。业务 KR 的完成档位与领导力/文化行为档位分别应用；行为评分使用正式维度，70–79 为符合标准，80 分及以上须有明确超出当前职级标准的具体效果（经理日常辅导、总监在团队内复用方法本身仍可属于本职标准），90 分及以上须具备对应榜样范围证据；显示分数保留一位小数并采用通常四舍五入。每次渲染后都按当前 manifest 写入或更新并完整读回全部报告和附录，即使成员分析没变；旧标题及旧读回不能代替本次结果。群提案逐字使用当前 summary.md；这些步骤由 Skill 与现有 Audit 承担，未新增服务发布门槛。原独立周报 Agent 子任务、租约/缓存编排和星期日重复调度逻辑退出当前执行路径；历史记录保留。

访问申请页等待申请按钮实际可用；非权限加载壳不能当作已可读，只有听记读取 API 确认可读。Lark 没有默认定时任务 seed。

内部投递、发送状态确认、错误恢复仍是内部机制，不外化为 Cron；催办不自动发送，只由 Derek 点按钮发送。

Task Agent 的 liveness 只认完整的结构化 provider stdout 事件续租（其余见 `docs/architecture.md`）：stderr 和未完成的 stdout 片段不刷新进展时间；原有同 revision 的重复失败上限仍然有效。

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

反馈、人工重跑、进程失败和 typed-result 失败都会创建新的 append-only `agent_run`，但不会创建
新的业务 `reply_attempt`。只要原 runtime session 仍可访问，新 run 就向原 session 发送
continuation；新 revision 或 Skill/契约版本表示业务规则/结果版本前进，不表示必须创建新 session。
每个 turn 都记录当前契约哈希作为回执，但哈希变化不能切断上下文。只有 provider 明确返回 session
不存在或认证上下文失效时，才创建新 session。

Agent 返回的错误只是它观察到的现象，含义由服务决定（`app/agent_reported_error.py`）。wire 结果里的
`error_retryable` 与 `error_authorization_required` 不被读取；`error_code` 先统一为小写，服务认识的码
（需要授权或确认、dry run、可自行恢复的依赖、浏览器工具回报的退订码，以及缺少已声明操作的 `operation_unavailable`、`capability_unavailable`、`write_capability_unavailable`）按服务政策决定是否重试、

Derek 2026-10-25/26：缺少工具本身不是失败理由。若原操作不可执行，但现有信息足以形成真实、有用的回复，Consumer 应生成给请求人的消息候选，说明未完成操作、已有材料和请求人的下一步，并走原有独立 Audit 与投递流程；不能伪称操作成功。只有操作和有用回复都无法真实完成时才报 `operation_unavailable`（提示在 `_CAPABILITY_COMMON_TAIL`）。
是否等人；其他任何失败码记为 `agent_reported_failure`，走有上限的普通重试（同一 role、generation、revision 连续失败 4 次 turn，即初始调用加三次 retry，
终止为 `failed`，见上文），Agent 原文保存在 `source_code` 供排查。非失败结果（`needs_human`、`no_action` 等）上的码只作为原因标签，不驱动重试
或授权。编排层依赖的服务码（`runtime_*`、`codex_provider_*`、租约与恢复类）只能由服务写入，
Agent 写入时一律按 `agent_reported_failure` 处理。

服务反馈回调的结果格式校验失败属于结果解析失败：保留原始 `feedback_callback_pair_invalid` 在 `detail`，通过 `ResultParseError` 进入现有 `codex_result_invalid` 纠错流程；每次处理最多两个回合，同一角色/代次/修订连续四次失败后终止为 `failed`（首次调用加三次 retry）。校验不能抛出普通 `ValueError` 绕过编排后由调度器无限重新领取。该异常转换不改变反馈链接配对规则、历史来源校验、审核职责或调度器所有权。

**原则：任何更底层观察到的错误码或原文，被归并成更通用的服务码时都不能丢失，必须原样带到 Agent 报错记录里，供事后排查。** 通用化是给重试/授权决策用的分类，不是删除诊断信息的许可。已落实的例子：`agent_reported_failure` 保留 Agent 原文在 `source_code`；Agent 报告的 typed 失败结果保留其必填的 `summary` 在 `reported_summary`（2026-09-28，见下段）；Codex 进程失败保留 stderr 与 JSONL 里的 `error`/`message`/`detail`/`reason` 字段在 `detail`（`_process_failure_detail`，裁剪到 1000 字符、脱敏凭证）；路由执行错误沿 `__cause__` 链找具体解析/校验异常的 `reason`（`_runtime_failure_detail`）；退订浏览器错误保留异常类名和截断消息（见上文退订段落）。新增一处错误归并时必须同样保留来源，不能只留下分类码。

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

`maybe_create_dingtalk_todo` 必须提供当前 `business_task_id`；Todo dispatcher adapter
及 Status 中该 adapter 的队列展示只保留当前 BusinessTask Todo outbox。旧 link/outbox 的 schema、已保存状态、来源和
provider 回执保持原样，读取仍供 History、已发送 Todo 和 legacy-project 详情使用。
旧 Work Project、Work Todo、Work Update 保留各自 typed 历史行、reader 和由明确来源清单驱动的 task-semantic-import apply。
Derek 2026-10-20 选择：旧记录无法核验 current Task 身份时仅保留历史，不按公共整数
ID 关联或晋升、不取消或完成外部 Todo、不重发。完成扫描和未知创建回执核对仍只读取
当前 BusinessTask 的链接；旧 `active` 不再触发任何 provider 操作。

**催办由 Derek 点按钮发送**（Derek 2026-09-25：「催办应该变成 UI 上的一个按钮，让用户决定是否要发送催办（点击一键发送），如果后面有新的信息更新了 task，应该取消可催办状态」）：Task 详情页「催办」区列出该 Task 的全部 follow-up；待发送（`draft`/`approved`）和发送失败（`failed`）的每条带一个发送按钮，点击调用 `POST /api/console/tasks/items/{task_id}/follow-ups/{follow_up_id}/send`（带当前 `revision`），由 `send_business_task_follow_up` 当场发送：不看工作时间、不经 Agent 审核，点击就是决定。失败或结果未知的发送保持 `failed` 并把结果留在该行上，不排 Agent 修复；再点一次作为新 revision 发送，原 attempt 不改。Task Agent 把新信息应用到已有 Task 时，在同一事务里用 `cancel_pending_business_task_follow_ups` 撤回该 Task 其余未发出的 follow-up（`cancelled`，原因写明被哪条来源更新），同一来源信号新建的 follow-up 保留，正在发送的不动。

Task 7 的新 follow-up 只从链接来源信号的精确群/单聊目标与明确、可解析 `next_check_at` 创建，
不从 deadline 推导时间或猜收件人；`next_check_at` 只是建议的催办时间，不会到点自动发送。群聊回到精确
来源会话并提及来源证据支持的负责人；单聊发给来源明确指定的负责人账号，原会话 ID 保留用于核对。发送
（仅由上述按钮触发）有独立的 claim、lease、revision、幂等 UUID 和回执记录；发送中的租约过期或网络中断
标为 `unknown`，该 follow-up 显示为 `failed`，不自动重发，也不排 Agent 核查，是否再发由 Derek 再点一次决定。
旧 `follow_up_drafts` 只是历史记录，不会再被发送。

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

**问题**：钉钉从听记抽出的行动项，`executorList` 恒为空，负责人不在行动项里。所以负责人来源现在有两处：转录窗口，和整份会议摘要。

Task Agent 会把负责人证据再结构化为 `owner_kind`（`individual` / `team` / `unknown`）和 `owner_relation`（明确分派、自承诺、会议行动项、仅发言或未知）。只有明确指向单个个人且关系为前三种之一的行动项，才记录为正式 `meeting_action_item` Task，并保持 `assigned_unaccepted`；团队负责人、仅发言或关系不明仍是候选。缺少稳定 ID、日期或完成标准属于后续补充，不会单独把已经明确指派的行动项降级为候选。AI Minutes 行动项不以 `external_todo` 作为正式依据。

**做法**（`app/minutes_todo_context.py`，由 `scan_meeting_todos` 调用）：

1. 入队前读完整份逐字稿，**再读一次这场会议自己的 DingTalk 摘要**（`get_minutes_summary`，`minutes_full_summary` 取其 `result.fullSummary`）。
2. 转录窗口：对每条有有效 `createdTime` 的行动项，截取包含该时刻的那一段及前 6 段、后 4 段，每句写成一行“说话人：内容”放进 `transcript_excerpts`（钉钉的 `sentenceList` 把整段当作一句，所以在句末标点处自己拆句）。
3. 会议摘要：原样整段放进 `meeting_summary`，不做关键词或分节切片——钉钉的摘要经常直接写“行动项：**磊哥**与**周俊杰**负责代码 Review”，但可能出现在整场会议的任何位置，切片会漏掉切片规则没覆盖到的写法。
4. 没有摘录/摘要的情况：`createdTime` 缺失或为 0、超出逐字稿的行动项在转录窗口里没有摘录；逐字稿为空、或摘要为空字符串，对应字段就不放进去。逐字稿读取失败时仍继续读会议摘要，并把 `source_transcript_unavailable=true` 标记交给 Task Agent；Agent 只能基于实际传入的行动项和摘要作判断，不得从缺失转写中推断负责人、承诺或指派。若会议摘要读取失败，才将该会议保留为未入队、未记为已见并由下一轮重读。
5. 去重摘要仍只覆盖行动项本身，所以之后补读逐字稿或摘要不会让处理过的会议重复入队。

**提示词规则**：负责人是对话把事情交给的人或接下的人，不一定是说话人（甲说“你写下来”是指派给被点名的乙）；负责人引文可以来自 `transcript_excerpts` 或 `meeting_summary`，是一句带说话人标签、包含负责人名字的话（可摘取），常常不是决定本身的 `source_excerpt`；钉钉对认不出的说话人给的“发言人 N”是占位标签、团队或部门名（“研发”“算法团队”“Product Marketing”）也不是人，两者都绝不作负责人；转录窗口和摘要都说不清就留空——这是来源本身的边界，不是要去凑一个负责人出来。转写不可用标记存在时，Agent 只能使用实际可读的行动项和会议摘要，不得补造转写证据；没有明确个人负责人时仍不得将行动项升级为正式分派任务。日期仍不从听记取，因为没有可信的说话人到身份映射。

**回填已入库的候选**（已处理过的会议不会被再读到，所以必须回填）：`python -m app.cli backfill-minutes-owners`（`app/minutes_owner_backfill.py`）。

- 先从 Signal 事实只选符合条件的会议 UUID 与候选 Task ID，再按该 UUID 读取当前会议记录，核对响应记录的 UUID；缺失或不符时跳过该会议。只用该真实当前记录重读行动项、逐字稿和会议摘要，组装新的 Work Item，以新的来源引用再入队一次，由 Task Agent 用普通更新路径补负责人；旧库已存正文不能代替当前会议记录。
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
- 候选没有负责人是正常状态，界面不把它当缺口。

## 任务类型

- `okr_review`：指定人员和周期的逐 KR 评审。执行 Agent 按当前业务 Skill 读取实时
  `processed.objectives`/`processed.okrRows`，再生成评审；审核 Agent 审阅并反馈修改，
  修正版通过后才发送。底层读取错误（认证失效、浏览器/profile 锁、周期解析失败等）
  必须原样保留，不能被 `consumer_retry_exhausted` 覆盖。服务入口先复用有效 token；
  缓存过期时使用现有 Chrome cookie 副本启动 headless 浏览器刷新，不启动额外可见窗口。
  登录页存在“扫码登录”标签时优先走本机 DingTalk SSO 的该标签、已登录账号头像、
  原生确认和目标组织选择；若导航直接抵达 OKR 入口则接受该导航。当前中文登录页不能用
  英文 `QR Code` 标签匹配；可见的“立即登录”按钮可能被遮罩挡住，因此只有扫码入口缺失时
  才尝试直接账号入口。SSO 选择器/组织缺失和导航错误必须保留实际异常，不能静默吞掉后
  统一报“会话过期”；有限等待超时仍按真实认证/浏览器故障失败，不绕过验证或伪造取数成功。
  该任务的 `StructuredCodexRunner` 使用 `app/schemas/okr_review.schema.json` 声明
  原生输出：外围仍为通用 AgentEnvelope，`domain_payload` 为现有 OKR 模型的完整
  字段，所有 object 具备具体 properties、全部 required 字段和 `additionalProperties: false`。
- 管理者 OKR 周报使用普通 scheduled Agent 与 `ceo-weekly-okr-report` Skill。实时取数按成员执行；评分公式、身份绑定和渲染在 Skill 脚本，来源和发布事实使用既有记录。

## 文档索引

- 总体 A/B 架构：`docs/architecture.md`
- 路由失败和恢复：`docs/runtime-route-recovery.md`
- Runtime 路由、fallback 决策与 429 重试：`docs/architecture.md` 的「Agent 失败重试」与各 Runtime 路由小节，
  实现在 `app/runtime_fallback.py`
- 429 同路线重试在失败 attempt 已观察 session 时续用该 session，并提交 `continue`；冷启动首次返回
  429 也保留从失败 attempt 取得的 session。无 session 时重放原任务输入。细节见
  `docs/runtime-route-recovery.md`。
- Consumer/Audit 反馈设计：`docs/superpowers/specs/2026-08-06-consumer-audit-agent-design.md`
- OKR 领域输入和输出：`docs/superpowers/specs/2026-06-08-okr-review-runner-design.md`
- 当前实现：`app/agent_orchestrator.py`、`app/consumer_agent.py`、`app/audit_agent.py`、`app/okr_review.py`、`app/weekly_okr_report.py`、`app/store.py`
- 系统错误码目录：[`docs/error-catalog.md`](error-catalog.md)
### Production deployment lifecycle

SQLite failure diagnostics identify the waiting operation, not the owner of a cross-process write lock.

Production deployment is serialized at both the repository and service
boundaries. The deployer first waits for the persisted work leases to drain.
This includes valid dispatcher leases before an Agent runtime exists, meeting
preparation, and already-claimed meeting deliveries. Expired or terminal claims
and unclaimed ready-to-send work do not count as active execution.

Omitting `--target-commit` retains the
ordinary current-main deploy path: after the quiet wait it fetches once more and installs the newest
fast-forward `origin/main`, so commits pushed while claims were closed ride this deploy and the sessions
queued behind it find production already current and exit without draining again (a main rewrite that
drops the planned target fails before the stop). `--restart` does not accept a target SHA.

The repository mutex covers the protected-source unlock window as well as
checkout, build, verification, restart and relock; settings-only restart uses
the same mutex. If an interrupted fast-forward leaves only unstaged docs whose
bytes exactly match the fetched target commit, formal deployment stops the
service and takes its backup before restoring those docs to the current HEAD;
the ordinary fast-forward then installs the target. Staged edits, other paths,
or any content mismatch remain a hard local-change refusal.

The Email worker, Audit web, scheduled Email discovery, and scheduled report readers do not scan every historical email row while the service is starting or serving scheduled Email and report work; explicit and offline EmailStore callers retain full durable-row validation by default. Email detail reads still use fresh connections and do not cache record contents.

### Public information and native reply recovery

The fixed Consumer/Audit rules now distinguish established public disclosure
scope from factual support. All-staff public OKRs and ordinary coordination
within their established audience do not require an additional personnel
privacy grant. A privacy refusal must identify concrete nonpublic information
and an audience mismatch. Public visibility still does not support invented
conclusions, dates, commitments, or nonpublic individual assessments. This
contract is included even when a persisted custom Audit Rules template exists.

Native DingTalk replies commit the existing dispatch claim before the provider
call. In Store19.9 the claim timestamp lives in its prepared Action’s
`postfix_dispatch_created_at` slot; the source-shaped claim API is unchanged. After an interrupted call, another invocation of the same prepared
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

Unsubscribe forms execute through the exact native submitter selected by the
audited DOM model. Its selector is included in the control identity alongside
the form association, method, action and successful controls. The browser runs
the provider's submit handlers; the service does not reconstruct a GET or POST
from the form attributes. Completion still
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

An empty unsubscribe email field uses the original message recipient, not the
connected mailbox login. The direct operation obtains recipients from the
classification's exact account and stable message identity; only one parsed
recipient can be prepared. Missing or ambiguous recipients leave the field
unavailable rather than substituting the primary mailbox. Authentication and
mailbox OTP continue to use the connected account. The prepared recipient is
part of the complete form control digest, so hidden controls, submitter, target,
intent and form association remain checked before native submission. Prefilled
email fields must also match the source recipient; a missing source or different
prefilled address is rejected without submission. User
instructions to retain a subscription must not be recorded as provider opt-out
success or executed through an unsubscribe request.

`cancel-failed-email-unsubscribe` records an explicit principal decision to retain
one subscription. It requires the original task id, action identity, execution
generation and input version. The default previews without writing a decision;
`--apply` uses the configured browser profile's nonblocking lock and an immediate
database transaction. A changed identity or current plan, active dispatcher,
Agent history, unsubscribe claim, effect, step or provider receipt prevents
cancellation and requires reconciliation first. An accepted decision is stored
separately in EmailStore's `email_unsubscribe_cancellations` (schema 50), preserving the original failure and all history,
and the original task becomes `skipped`; no provider receipt is created. Repeat
apply is idempotent. The direct operation rejects the cancelled action, checks
again inside the profile lock before opening a page, and the durable write-claim
entry also rejects it. Missing-provider-receipt repair does not synthesize a
receipt for these principal cancellations. This is not a blanket address-level
unsubscribe policy or authorization to alter other mail actions.

Cancellation preview uses a read-only SQLite connection without constructing
execution stores or migrating schema.
Direct browser execution and result persistence retain the same profile lease,
including reentrant adapter calls that cross the session TTL. Before the first
browser operation, a generation/input-digest-bound execution-pending record is
written in the reply-task store's runtime metadata. Only an actual terminal
receipt clears that exact record. A crash, unconfirmed result or failed receipt
write leaves the record requiring external reconciliation and prevents a false
subscription-retention closure. An explicit retry of a worker-finalized login
or no-entry skip still requires its exact predecessor receipt digest; provider
completion receipts do not permit replay.

Real Chrome unsubscribe fixtures verify the persisted effect, continuation,
step, claim and receipt fields separately: only the receipt's `entry_url` may
contain the private entry. Returned redacted evidence must not contain it.
Entry reads can load redirects and resources under the existing no-network-
whitelist policy; these GETs are not proof of zero external effects. A discovered
form still returns a continuation without submission or terminal completion
until its control is accepted.

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

Audit web startup does not scan and cache History lists or charts. History
pages are computed when requested. No page-result cache or background page refresh
can reuse another page size or hide a just-committed record.
For failed Attempts, the History status projection also follows the owning
AgentRun's stable reply-task ID when the OA scanner has changed the task's
trigger key. Status filters and counts use that projected state; stored Attempt
and execution records are unchanged. A current candidate execution with an
unresolved uncertain receipt remains `uncertain`, even if the task closed;
independently resolved Attempts retain their resolution projection.
Performance evidence is in `docs/console-query-performance.md`.
Health readiness is not evidence that History requests or business actions
have completed; their APIs and provider receipts must still be read back.

History reads do not replay work or change stored status.

DingTalk meeting group searches explicitly request 100 candidates per page,
the provider's supported maximum, while retaining the complete-source check.
A partial response still fails closed;
larger pages do not change recipient ranking or authorize a send.

Worker status reads its local SQLite queue, Email health and component facts
on every request. These facts are not served from the last background payload:
after a worker writes its state, the next status request must reflect it.
External connector authentication probes retain their independent cache.

Typed result parsing preserves malformed or unclosed JSON as a result-stage
invalid-result failure, including its syntax cause. It is not classified as a
missing result. An earlier valid result in the same primary turn remains usable;
no JSON repair or successful external-effect inference is performed.

Manual reruns of scheduled Agent work preserve the original occurrence's
scheduled date, prompt, route, workspace, runtime options and run identity. They
create a new immutable input using the currently selected Skills on that
scheduled-task definition; automatic retries keep using the occurrence's
original pinned Skills. The original run and input remain unchanged. The manual
rerun budget is counted per exact scheduled input digest, so an identical input
still has the existing three-retry cap while a changed selected Skill snapshot
is a new input revision. Missing run, task or Skill material rejects the rerun
without queueing. Consumer, independent Audit, delivery authorization and
receipt gates are unchanged. A missing or invalid scheduled payload is rejected
instead of being rebuilt as a DingTalk message; the original scheduled run is
the authoritative source for explicit recovery.

The approved DingTalk send tool persists its exact prepared body and verified
provider receipt into `sent_replies` as soon as provider verification reports
`sent`, before returning to the Audit turn. A later invalid Audit result cannot
erase this delivery evidence. Pending or ambiguous verification produces no
successful History projection. This records one message's actual effect, not
completion of the whole proposal: the task and external-action completion
ledger still require the existing Audit lifecycle and evidence checks.

### Sensitive Result Classification

The post-process sensitive-result guard raises the same result-parse exception
as other invalid typed output. Rejection is recorded as `codex_result_invalid`
at the result stage, allowing the existing Audit result-correction prompt and
bounded attempt handling to operate. It is not a CLI execution outage. The
credential rejection itself remains unchanged; no unsafe result is accepted and
no content-feedback cycle is consumed by a parse failure.

### Daily Report Source Identity

Reports from previous scheduled runs can share the display title and summary of daily report `handled_today` facts; their completed status is evidence for that historical source only, not the current report's publication or notification.

# Email SQLite Contention Diagnostics

EmailStore and AutoReplyStore report connection contexts lasting at least one second, including
elapsed time and the caller's file, line, and function. This duration includes
lock acquisition, the body, commit, and close; it is not by itself proof of the
write-lock holder or transaction duration. Logs exclude SQL parameters and mail
content. Correlate the caller with the operating system's WAL-lock owner and
the affected run before changing transaction boundaries.

The Web process shares one successfully schema-initialized EmailStore between
registered email routes, Console detail routes and the email learning service.
Email route registration retains its existing initialization error boundary:
an invalid schema or account configuration does not prevent unrelated Console
routes from starting and is not treated as healthy email state. The Attempt DTO
builder queries this initialized store only for
an existing email-channel Attempt's classification and unsubscribe context.
Every request reads current records through fresh database connections; detail
results are not cached and requests do not repeat the full durable-state scan.

Direct provider-action claims hold BEGIN IMMEDIATE only over classifications
with pending or failed actions, rather than materializing every processed
classification's settled history on each poll. For each selected classification
all sibling plan versions remain visible: an older processing action still
blocks the current plan, dependencies and priority remain unchanged, and the
claim update remains atomic. Fully settled groups cannot yield a claim and are
excluded before rows are materialized. This requires no migration or replay.

A direct-action claim compares the Action owner's own stored `started_at`, read
inside the claim transaction, not the view's projected value. The view reports
a pending Action's `started_at` as NULL. The
status and attempt-count checks are unchanged, and no stored value is rewritten.

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
and exceptional exit. Cleanup never changes receipts, retry deadlines, attempt counts or terminal status.
Persisted `sent` receipts remain authoritative: recovery resumes only calendar
terminalization, not analysis or chat sending. Transcript-only meetings retain
the existing explicit calendar-skip receipt because no original event exists.

Meeting discovery may refresh a waiting recording, but once a job is queued,
claimed, retrying, ready for delivery or terminal, discovery cannot replace its
persisted source snapshot or participant evidence. Recovery refreshes failed
unsent jobs through the explicit replay lifecycle only when no standard or
sensitive DingTalk outbound is already prepared. A prepared outbound remains
owned by its existing delivery recovery path and cannot be cleared by scheduled
replay. A failed roster lookup during rediscovery must not remove
`calendar_evidence` from an already queued job.
Recent-meeting replay uses the same ended-recording metadata contract as
discovery, without requiring the provider status to equal the literal `ended`.

Task Agent validates formal creation and candidate promotion owner citations
before entering its atomic domain transaction. A repairable evidence error is
returned with the rejected candidate and original context, for at most two
correction rounds. Task also supplies a compact correction bound to the native
session that produced the previous candidate. The runtime selects it only when
the actual route resumes that exact session; cold starts and other sessions
receive the complete source, semantic context and correction. Existing route
selection and format-correction policies are unchanged. Friday creates a new
thread on every invocation, so it always receives full repair context even when
a prior thread reference is stored. No domain changes are
committed before validation succeeds. Exhaustion retains a real failed run and
uses the existing work-summary retry policy; it never guesses an owner or
changes a technical failure into `needs_human`. Memory-backed ownership still
requires linked source provenance and an `episode_id`.

Current Work Item Project citations, including nested context and assessment
evidence, use the same exact source identity and contiguous-quote validator
before domain writes and again during atomic apply. Invalid current citations
enter the existing bounded correction rounds with the rejected candidate.
This does not rewrite or normalize quotes; exhaustion remains failed without partial Project or signal writes.

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

来源快照中的反馈链接在检查副本中按 Markdown 链接和真实 JSON 容器读取；不要求其他参与者使用本人的外发签名、标签或段落格式。仍严格核对当前配置 host/path/query、生成 token、上下链接同一身份与唯一完整配对，额外未配对 URL 或凭据仍拒绝。只移除已核验 URL 的检查副本，不把来源链接当授权或外部动作回执。当前待发送正文仍要求原始严格格式；原 source_bindings、候选 digest、持久化结果与发送前来源回读比较不变，其他来源字段继续接受原有敏感值、深度和大小检查。确定性的来源结果验证失败记录 runtime_result_source_invalid、stage=result、source=service、retryable=false、session_continuable=false；不归为 CLI/provider 失败并反复恢复同一会话。

Attempt 详情的 system_execution 按 candidate、review、selection 绑定显示当前及历史阶段、声明顺序、未开始动作、失败/不确定状态和 provider 回执。只有 canonical action ledger 中核验成功的动作计为 verified；Audit approve 和用户选择都不能显示为已经执行。

History 的“外部动作”摘要按 receipt 的 `first_agent_run_id` 归属：属于当前 Attempt 所含运行的 verified receipt 才算本轮新产生的效果；本轮继续处理时复用的较早 receipt 单独显示为已复用，不冒充本轮操作或本轮 acting run。时间从 provider 结构化操作记录读取，以 UTC 毫秒精度呈现；Attempt 更新时刻不能替代原始操作时间。此规则只修正只读展示，保留不可变 receipt、原 done 状态和现有幂等复用语义。

独立 Email 退订仍使用原有 direct executor、浏览器步骤和回执。无运行时调用者的旧 Agent continuation driver 已删除；Agent finalizer 不再从历史 Audit 声明触发退订、切换渠道或伪造执行成功。默认 developer prompt 和 OA 规则种子统一为新角色合同；既有自定义规则、managed Skill 配置与 runtime-only Skill 必须在安静的正式发布窗口中逐项发布并记录版本，部署代码本身不会覆盖它们。

Consumer/Audit stage clarification (2026-10-04): `continue_after_execution` means the next stage can run immediately from the verified result. A material-request comment or return ends its request stage with the flag false; only a later source update establishes material arrival and permits a fresh complete candidate. The request receipt cannot replace the material. Audit runner and the frozen model harness share `audit_developer_instructions`, including registered action contracts and exact provider-identity evidence; a project/request ID is not an OA process identifier, and a decision notification does not perform a payment.

角色 MCP 启动参数携带原领取 run 的 execution_generation，启动时与当前任务校验；旧回合不能在 generation 更换后绑定到新一代工件，运行中的既有读写仍逐次检查当前 generation。

真实、未解除的 provider-risk refusal 必须保留 failed 与原始错误码，不能因不重试改为 no_action 完成；Audit 对错误完成候选退回修正。历史同一动作拒绝不允许换键或渠道重放。保留既有历史拒绝与恢复限制，不用关键词推断效果。

### 调度异常的重试上限（2026-10-05）

Derek 要求自动重试统一最多三次 retry（首次调用之外），即最多四次调用。ReplyQueueAdapter 的 handler 异常计入既有任务 attempts，
不再退款或立即重新领取；前三次按共享指数退避延后 60/120/240 秒，第四次原任务记为 failed，
保留原错误、业务对象、执行代和运行历史。ScheduledExecutionQueueAdapter 继承同一规则。
共享上限由 `app.external_retry.MAX_AUTOMATIC_RETRIES` 定义；DWS 瞬时调用、同路由容量重试、
浏览器瞬时失败及其他自动重试默认读取这一上限，配置值不得放大它。
普通主动 release 仍是未执行业务的调度释放，不消费异常预算。Consumer 与 Audit 的已持久化
技术失败分别使用独立的每角色、同执行代、同修订四次尝试预算（每个角色最多三次 retry），调用前检查；
底层 code/source_code 保留，预算耗尽不伪造 needs_human 或业务完成。

DWS 网关自己的 5xx 失败（stderr 里 `error.code=1`、`error.reason=http_<5xx>`）以 `http_<5xx>` 作为错误码，
对消息读取命令按上述共享上限自动重试，用尽后仍标记为可重试的外部依赖故障、不进入 Attention；
消息发送命令没有幂等键时不重试。

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

Consumer 与 Audit 可以使用只读 `convert_dingtalk_calendar_time` 按 IANA 时区数据库换算时间。

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

Prompts 内容标题随当前查看方式显示「模板」或「渲染结果」，与所选 Developer/User/Profile 页签对应；完整运行输入仍位于独立只读「运行输入」页签。

Developer Prompt 只承载适用于所有任务的角色、能力、证据、系统动作、对应路线所需的输出契约及共同工作原则；工作人格仍注入 Consumer 与 Audit，Audit Rules 只进入 Audit。Consumer 使用 Codex 或 Claude 原生输出 Schema 时，服务输入省去重复的完整 Pydantic wire schema 文本；Codex 的原生格式契约本身仍须提供，不能只给指纹；没有原生 schema 的 Friday 路线仍保留该文本，Audit 输入不变。所有 Consumer 路线继续共用不得直接发送对外消息、邮件或执行审批的角色约束；本精简不改变该约束、严格 Pydantic 业务校验、原生 schema、Audit 或 System 执行流程。OA、日历、消息受众、OKR、招聘和文档等专项流程由当前任务选择的 Skill 承载，不再作为全任务 Developer 规则重复注入。User Prompt 是 Consumer 的完整任务模板，必须包含恰好一个 `{{task_context}}`，仅支持普通文本和这个插槽。任务来源、定时要求、stage、反馈、既有回执及 continuation 先由服务组装成完整上下文，再填入插槽；不将旧消息块模板叠加到后台任务。Workbench、独立 WeChat、纯服务命令及 Email 退订保留各自入口和显式指令。

Consumer 和 Audit 每轮 Task 输入均传入当前任务已保存的专项指令，来源为「已保存任务配置」，现有运行输入快照记录该段来源和长度。Audit 不只从候选转述推断任务规则。`task_specific_instructions` 复用 Consumer 已有的精确 Skill 附录分离逻辑；只分离已知的完整末尾附录，不截断正文或按业务关键词改写。生成发现提示不重复预载，自定义 Skill 约定仍在 Skill 段保留。没有专项指令的任务不添加空段；审核权限、规则优先级、反馈和 session 行为不变。

System Action Contracts 在 Developer 中提供由 docs/system-action-contracts.md 自动生成的 capability/operation 目录；目录以 provider-neutral 的 `read_system_action_contract` 工具名指向 Runtime Context 中声明的原生入口（Codex 为 `mcp__agent_cli__read_system_action_contract`，Claude 为 `agent_cli.read_system_action_contract`）。该工具保留动作表外的全部原规范正文，只返回选定能力／操作的动作表行；只给 capability 时返回该能力的全部操作。原有角色、identity、payload、target、回执和完成条件保持完整，不返回其他操作的表行。Consumer 提出及 Audit 审查相应操作前必须读取规范；契约指纹仍包含全文。结果 schema 在 prompt 中只去除 title/default 注释，保留字段名、描述、枚举、约束及原有 Pydantic 校验。Codex Consumer 使用当前 Consumer wire model 生成的 general/OA 原生 Schema；Codex Audit 使用当前 Audit wire model 生成的同一原生 Schema，选择、解码和恢复指纹与 OAuth/API 认证方式无关。动态 JSON 对象以键值条目传输，并在严格 Pydantic 校验前无损还原；Audit 的 outcome/feedback 条件与 Consumer wire 分支约束保持不变，revision、digest 和候选字段断言仍由当前 Audit 上下文及既有校验负责。候选字段断言路径不存在时，修正反馈会指出准确路径和该父对象可用的字段名或数组索引，不回显候选字段值；校验和审核标准不变。Claude、Friday 及其他运行时路径不变。配置路线验证状态见 `docs/archive/records/native-consumer-output-validation.md`。

OA 评论出现未知效果后，System 只读原实例的正式 detail；摘要 records 只含操作结果，不能证明评论正文。恢复要求持久动作键、uncertain 状态及准确带时区的首次动作时间，并匹配 detail 中唯一的 ADD_REMARK、当前本人 ID、完整获批正文和动作开始后的毫秒时间。旧评论、其他作者、歧义或缺失证据继续保留 uncertain，绝不重新评论。回执保存原实例、原生操作类型／作者／时间与正文摘要，不编造 commentId、不复制整份审批详情；该读回不授权新的审批决定。

未精简任务的当前配置预览从原任务结构化输入识别冻结 Skill，使用任务内读取入口并标明冻结/当前目录来源；冻结材料不需要在当前安装目录存在。已结算回复输入的冻结正文精简后明确不可用。历史模式只展示选定原生 invocation 保存的服务 Task 正文，不追加任何新入口，也不由当前 Skill 或精简来源记录重建正文。

一次角色 invocation 只读取一次 Developer/User/Profile 正文，供组装、静态指纹与重试共用；Audit 不读取无关 User 模板。实际路线和工具命令确定后，再追加 Runtime Context。静态配置 SHA 写入运行期 runtime.prompt invocation facts，仅作来源回执；原生历史未保存这些元数据时不重建 SHA；Consumer 按 `conversation_id + route` 查找原 session，并要求所选 Skill 合同和当前 Developer 指令合同与保存值一致，否则新建 session；Audit 的独立 session 不与 Consumer 合并。

仓库受跟踪的 `data/prompts/developer_prompt.md` 是通用 Developer 模板，继续保留 Git 版本管理，
与已批准的 `app/defaults/developer_prompt.md` 保持逐字节一致。发布后的生产文件与回执一致
不等于工作树已干净；源码同步不放宽部署器的本地修改检查，也不允许手工重置生产模板。

Prompts 设置读取先返回 Developer/User 的已保存原文；某份模板验证或渲染失败时，仅该份渲染预览为空并返回明确的 `preview_errors`，编辑器仍显示原文供修正。保存仍需通过现有验证，后台角色调用仍严格验证，不自动迁移或覆盖旧模板。

精简只移除重复或已由任务 Skill 承载的流程说明，不改变角色边界、身份与证据规则、完整效果判断、业务结果模型或 Pydantic 校验。Audit 的候选、source_bindings、revision 和 digest 保持完整；只有 provider、object_ref 与来源 value 按排序 JSON 完整相符（保留布尔/数值等 JSON 类型区别）时，Task 中重复的触发正文、raw payload、历史消息正文和材料 reference 指向候选的来源绑定，同一输入内仍能读到完整值。来源不同时两份全文保留。未结算的定时服务命令在 `scheduled_consumer.skill_materials` 冻结完整 managed revision 或 operation snapshot；未结算的通用 Scheduled Agent 在 `scheduled_agent_execution.v1` 顶层保存有序 `skill_names`、`skill_materials` 及其来源，原始 trigger 投影只保留结构化 Skill 身份事实，不再次复制正文。冷启动和续接都从同一未结算持久输入恢复；已结算输入改为来源摘要，冻结正文读取明确不可用。每一轮 Consumer 与 Audit Task 只列当前选中 Skill 的名称、极简用途和按需读取入口。保存输入声明该名称为冻结 Skill 时使用 `agent_cli.read_task_skill(name)`；所选冻结 Skill 的 revision_id/sha256 未变且完整正文仍在同一 session 上下文时，发现提示允许复用已读的确切内容；新会话、来源变化或全文不在上下文时仍准确读取，不新增缓存或本轮读取回执。材料重复、损坏或缺失由精确读取报告技术错误，不得改读当前磁盘版本。没有冻结声明的已选 Skill 使用目录中的实际授权绝对路径调用 `agent_cli.read_skill(path=...)`；Skill 正文引用的其他已安装 Skill 通过 `agent_cli.read_skill(name=...)` 精确读取，不把依赖目录预装进 Task。旧任务的显式 inline Task 约定仍在每轮保留。正文不复制进新 prompt，也不根据任务文字猜测来源。未选择 Skill 时 Task 只给 `agent_cli.read_skill()` 发现入口；无参数调用返回授权目录内的全部 name 和路径，不重复所有用途描述；按主题 `query` 查询返回匹配项的完整 name、用途和路径元数据，均不返回正文，Agent 再按精确 name 或 path 读取。目录查询和 name 解析沿用相同授权根，越界符号链接不会进入元数据，重名明确报错。目录 frontmatter 只提取顶层 name/description，嵌套产品 metadata 不会隐藏可用 Skill。无论有无选中 Skill，显式自定义 Task 约定都继续在每轮 Task 保留；服务生成的发现文字由结构化来源标记识别，不重复注入。新运行事实记录实际入口来源与本轮 `skill_names`；未注入的旧全量目录不再作为 `skill_protocol` 保存。Email 分类、Meeting Alignment 与独立 WeChat 没有任务内读取工具；Email 和 WeChat 保留完整冻结正文。Meeting Alignment 的 Codex 路线在有冻结托管会议 Skill 时保留其完整正文，省去已执行的操作 Skill 正文；其他路线及没有托管会议 Skill 的运行保留完整冻结正文。Email 分类的 Codex 命令按显式内联 Skill 名称，通过原生 skills.config 仅禁用同名安装副本的自动正文注入；本轮服务选定的托管或冻结正文仍完整提交。此配置不重新启用或关闭其他 Skill，不影响 Claude/Friday 的原有单条消息提交，也不改变分类规则、工具权限、路由、审批或会话绑定。会议来源预装配不能代替冻结托管 Skill 中的业务要求，因此 Codex 的精简输入仍包含该 Skill 的完整正文，不能只提交名称提示。Runtime Context 缩短工具说明；Codex CodeMode 的可用工具清单显示原生 `mcp__server__tool` 可调用名称，Claude 保持 `server.tool` 表示法，其他环境事实不变。Task 中的 Skill 调用提示仍使用服务命名空间的 `agent_cli.read_*` 表示法；该提示与清单的区别在 `docs/archive/records/runtime-mcp-tool-name-validation.md` 记录。

Developer 保存先以现有渲染器验证，未知变量或不可渲染内容返回具体错误且不覆盖已保存正文；该检查属于配置格式合同。

低信息简短收口与轻量互动优先用原消息 reaction，避免重复短文字；涉及业务判断、承诺、解释、下一步或正式文字确认时仍用简短文字。已有 principal reaction 通常表示真人已轻量处理。该规则属于共同 Developer 提示，不改变 reaction Action 的输入、审核、执行或回执合同。模板发布仍由 `ci/prompt-template-release.json` 精确 SHA 限定。

配置回执同时记录 Developer 模板原文和渲染后共同原则正文的 SHA；相同模板引用的变量/文件/代码展开改变时，静态契约指纹也改变。静态指纹仍不是 session 身份；历史记录缺正文 SHA 时只显示未记录，不以当前值补造。

固定规则在已保存自定义审核模板前注入。

## DingTalk 临时处理表情

普通钉钉聊天消息被服务接入处理队列后，原消息上添加服务账号的文字表情「处理中」。
日历邀请、合成 service_task 和 dry-run 不添加。成功回复的 provider 回执写入 sent_replies 后，
立即移除这个临时表情；无需回复、失败、转人工以及待处理任务换成新消息时也移除。
待处理和执行中的重试保留表情。表情的添加、移除失败不改变业务任务或回复结果。

ProcessingReaction 在现有 service_state 保存文字表情模板与每条源消息的添加/移除意图；
正常生产和消费 pass 根据现有 reply_tasks 与 sent_replies 清理已结束的源消息，
重启后继续未确认的表情操作。只操作本功能记录的源消息与服务账号的「处理中」表情。
该进度展示不改变 Consumer/Audit 生命周期、发送授权或现有业务效果检查。

Meeting planner 的 `audit_summary` 不是审核回执。分析成功后，服务以独立 model-only Audit（无原生工具或 MCP）调用复核会议来源、群候选、准确收件人、正文与披露范围。会议原文明确把后续工作分配给精确收件人时，这项分工可作为向其分享完成任务所需简要结论和必要背景的依据；无需额外逐字发送指示，也不授权发送无关或敏感内容，职位本身不构成授权。运行器传来的 Codex JSONL 先提取最终 assistant 消息，路由器已规范化的纯 JSON 直接解析，然后对两种输入执行相同结果模型及候选 SHA-256 校验。审核输出绑定候选 SHA-256，并和候选及 Audit session 引用一并保存。只有通过该摘要绑定校验且 `outcome=approve` 的候选可转为 `ready_to_send`。对于无任何发送回执、无活动领取，且因候选与既有收件人绑定冲突而失败的旧任务，最新 Attempt 可以通过明确的 audit-only 恢复入口重新读取会议来源并审核原冻结候选；若上次 Audit 为 `return`，用户明确请求后可用同一入口再次复核。入口要求精确候选 SHA-256，不重新生成候选、不改目标或正文，并在解析前剥离旧审核封套。只有对同一候选的新独立 Audit `approve` 才以 CAS 回到原投递 worker；`return`、`reject`、Audit 错误和候选变化均保留失败且不触发发送。`return` 必须提供 rule/observation/requested_revision，服务持久化反馈并创建新 Meeting run 做最多三轮修订；用尽仍未通过才转入 `needs_human`。`reject` 转人工；格式或结果验证失败按原 Meeting 失败规则处理，外部依赖不可用沿用原退避重试。新投递和人工重排都会检查审核绑定，旧的未审核候选、缺失审核、修改后的候选或错误摘要均不得产生新发送。已经有 provider 回执时的只读效果核实与状态恢复沿用原有回执、正文、收件目标验证，不要求为既成外部效果补造审核。不可变收件人准备绑定、同一投递键、provider 回执和原目标冲突保护保持原样。

Meeting planner RuntimeAttempt 使用 `workload_kind='meeting'` 和准确 Meeting Run 的十进制 ID；独立 Audit 使用 `workload_kind='meeting_audit'` 和同一准确 Run 的十进制 ID。两者在 native `runtime_attempts.parent_id` 上都指向经 `meeting_alignment_runs` provenance 核实的那个 Run；Task ID 或无关 Run 不能代替该 owner。显式 source/native 转换分别保留新 source 的 `meeting_audit` 数字键，同时继续仅在转换器中处理历史 source `meeting` 键加 `:audit` 的记录。失败、启动恢复和回放检查同时覆盖两种 workload。

独立 Meeting Audit 的结果（`MeetingAlignmentAuditResult`）不含 digest：模型只返回 outcome、summary、feedback，Audit 提示词里也不放 digest，没有让模型抄写的字段。Audit 与候选的绑定由服务计算：`persist_audited_meeting_decision` 用被审核候选的 SHA-256 写入 `_service_audit.candidate_sha256`，读取时（`meeting_decision_audit_is_approved`、audit-only 恢复）重新计算并要求相等且结果为 `approve`；候选被改动则绑定失效。绑定保证不变，只是不再依赖模型转录。解析器对结构错误抛 `RoutedResultValidationError`，路由器对同一 Audit 会话执行唯一一次修正轮（`attempt_purpose=result_validation_correction`）；仍错则该次尝试终止为 `runtime_result_validation_failed`，Meeting Task 失败。已存储的旧 Audit 结果若仍带模型抄写的 `candidate_sha256`，读取时必须等于候选 digest，否则按未批准处理。

Meeting Task 在有界 Audit 反馈轮用完后仍未获批准时终止为 `needs_human`（`meeting_audit_unapproved`，未发送任何消息）。History 对它与 `failed`/`quarantined` 一样给出 `needs_manager` 注意项（人工处理、技术详情），不再显示为没有待办。`needs_human` 的 Meeting 属于 `rerun_meeting_alignment_jobs` 的可重跑状态（与 `failed`/`no_action` 同一入口，Derek 2026-10-27 批准）：只有控制台“重跑”按钮会调用，原 Run 全部保留，重跑从 `retry` 重新经 Consumer→独立 Audit，所有未发送/无外发/无占用的前置检查不变。

定时 Meeting 重放仅重新开启没有结果、也没有已准备钉钉外发动作的失败或 `no_action` 任务。检查使用 Phase 2 TaskBusinessKey 中保存的原生公开投递键，并同时检查主键、`:sensitive` 键和 `:target:` 派生键；准备好的 Action 即使没有 provider 回执也阻止重放。不得从 native Task ID 重建 source key。prepared outbound 仍由原有投递恢复路径处理；重放不能清除后重新分析其既定动作，也不能改变已绑定收件人。

## Meeting Audience Recovery

独立会议路径复用共享消息受众合同。候选级保密历史拒绝保留为明确未知证据，继续读取其他
候选；不当作空讨论、无群或发送许可。群成员完整性和真实群人数独立于会议参会人数。
Agent 依据实际讨论、完整当前名册、稳定身份、当前职责和内容披露范围决定群或明确私聊。
共享群发现对每阶段保留候选的完整成员和近期消息采用固定最多 4 个并行只读调用、每批最多 4 项，按原输入顺序消费结果后执行原筛选与稳定排序；不缩小候选集或改变阈值、完整性、逐项证据、读取计数及错误分类。并发完成顺序不影响同分候选顺序或首个失败；失败后不提交后续批次，等待已启动读取结束，不额外重试、缓存或发送。 DWS 执行边界仅允许原生 `chat group members` 与 `chat message list` 共享固定 4 个读取槽；其余命令及隔离进程命令仍全局排他，须等待已启动读取结束，所有命令继续使用同一启动间隔和原超时、错误处理。
仅有转写参会人而无完整日历名册时，群发现先按原发送可用性和标题规则预选，再读取保留群的完整成员；无标题匹配时仍保留原候选范围。完整日历名册仍先检查参会覆盖再筛标题，不能让不符合覆盖的同名群遮蔽合格群。该读取顺序优化不降低所选群的身份、完整名册和讨论证据要求，不缓存受众，也不赋予发送许可。
业务私聊只接受来源中唯一的稳定参会人身份或日历证明的组织者；拒绝本人和已核验别名。
个人私聊仍要求完整日历两人名册。正文同一私聊受众合并发送，不凭 HR 职务扩大披露范围。
计划群不可发送时保留失败，不能自动改投组织者或本人。
恢复前精确核对主消息及敏感消息已准备正文与本次披露范围，不仅检查相同受众分支。
旧 HR 合并正文若含仅适合另一受众的敏感部分，在任何发送之前明确失败；不覆盖正文或伪造送达。
会议准备在现有 `service_state` 的 `meeting_delivery_target:v1` 命名空间原子绑定规范化目标与
原动作正文，发送及复用回执前再次校验目标。新投递键包含规范化目标的 SHA-256 指纹；旧键仅在
其已有目标绑定与本次目标完全一致时复用，因此不同目标不借用原回执。旧 prepared 缺少目标绑定
时不从新决策推断或回填，明确保留未核实失败。重跑和来源恢复门禁同时检查旧键及目标专属键，
防止绕过已准备动作。无需新增表、列或历史数据 apply。
Provider 明确返回 `success=false` 的主消息或敏感消息记录真实失败，不继续发送后续部分，
不把缺失的送达回执升级为 `sent`。

`rerun_meeting_alignment_jobs` 是原身份重新分析入口，不是送达重试。事务内要求未保存任何
部分效果、未准备主消息或敏感消息、无已有回执、无锁/活跃 dispatcher 或投递归属，且无活跃
会议/运行时回合。已准备或已部分发送必须沿原回执恢复，不能清空重做候选。历史运行不改写。
这些身份和归属检查不证明模型所写业务理由真实，也不代表会议已迁入通用 Consumer/Audit 引擎。
Console 可从最新失败 Meeting Attempt 显式请求重跑；入口确认 Attempt 仍是该 Task 最新运行后，
只调用同一受保护重放方法。后续重新进入普通 Meeting 分析和独立 Audit，不直接发送，也不跳过
原有外发回执门禁。

`import-meeting-asr-transcript --job-id ... --input-json ...` 先只读预览目标任务和 ASR 元数据；
带 `--apply` 时，事务再次校验任务仍为未锁定的 `failed`、会议身份和日历参会证据不变、没有
投递回执/部分效果/活跃 dispatcher 或运行时 owner。它把完整 MCP ASR 分段、音频 SHA-256、模型
和完成分块数附加到原 `source_json`，保留已有来源事实与运行历史，并把同一 job 重新排入 `retry`。
分析及投递均从这份持久化 ASR 来源读取；无说话人身份时不把文本归给参会人。ASR 只替代失败的
逐字稿读取，不跳过 Meeting Agent 的内容、目标及受众核验，也不直接发送消息。并发改动、回执或
活动 owner 会使 apply 失败；不得通过再次导入或直接重置任务绕过该门禁。

### 历史反馈链接的来源读取（2026-10-07）

Consumer 捕获的历史来源文本可能合并多条消息；校验器按反馈 token 与 attempt ID 分组，并要求每组各自包含通过配置 host/path、query 字段和评分验证的上下反馈链接。旧链接的触发/回复预览各自仍接受敏感值检查，但文字差异不使当前任务失败；Markdown 来源解析保留 URL 原文，以便只在结果检查副本中移除经过检查的链接。当前待发送正文保持完整 query 的严格配对，来源原文、候选 digest 和执行前来源比较不受影响。

## Storage retention (2026-10-09)

工作汇总输入进入 `done` 或 `skipped` 时，在同一业务投影事务中删除排队正文，只保留来源身份、来源时间、原始正文摘要与字节数、明确的精简标记及既有状态和运行引用。同一来源再次入队不会修改执行中或终态记录，迟到的失败或重试不能重新打开已精简终态；`pending`、`processing`、`failed` 输入保留准确正文供续跑。显式维护命令负责历史终态正文清理，初始化不执行该批处理。History 只显示来源元数据，历史语义导入需要原始证据时明确报告正文不可用。已采纳的业务证据、结果与执行回执不受正文清理影响。

回复任务在既有终态结算条件满足后，将当前输入、输入版本和对应 Attempt 的输入文字改为来源摘要及引用；每份被移除的原文都保留准确 SHA-256 与字节数，Attempt 只绑定内容确实匹配的输入版本。实际完成入口 `finalize_orchestrated_reply_task` 在业务 Attempt、记忆事件和发送回执写入后检查精简；仍有调度租约时延后，在 `ReplyQueueAdapter.complete` 释放租约的同一事务再次检查该任务。其他完成／跳过、回执和人工结算路径也只检查它们刚结算的任务。失败、待人工、更新的输入版本、活动 Agent／调度租约和未结束的微信投递保持完整输入；`done` 最新运行失败且没有既有结算依据时不精简。邮件动作、计划版本、邮箱／消息／线程身份、脱敏退订入口与认证引用继续用于既有回执校验；定时运行与配置引用、冻结 Skill 的版本或路径及摘要留在小型来源记录里。原有不可变输入校验的批量入口以摘要比较，普通消息仍按身份去重；准确的新输入清除精简标记并继续既有代际流程。精简标记不能作为可执行输入；历史重跑和冻结 Skill 缺少准确原文时报告不可用，不从其他副本或当前材料拼造原文。历史批量清理由显式维护执行，状态、业务结果、审查决定与执行回执保持不变。

定时配置正文存于 `scheduled_task_config_versions`，触发记录以 `snapshot_id` 引用不可变版本。数据库迁移保留运行 ID、状态和已排队输入。

描述优化提案保持产生它时的快照身份与引用证据不变。若提案尚未评估而源快照已被最新数据替代，提案一次性转为 `unavailable`，原因 `description_proposal_source_unavailable`；不反复启动失败的评估，也不把旧证据套到新数据上。下一次基于最新数据的训练可产生新的提案。

Status API 与前端 SystemHealth 解码器均声明 native_delivery_coverage（checked、unavailable）：允许省略或 null；存在时只接受这两个非负整数，仍拒绝未知字段。该字段仅实时计算，不写入质量快照，也不改变质量违规判断。

Codex 原生工具只有开始、没有对应完成记录时，历史详情保留准确范围内的
`item.started` 和实际输入，并按原生顺序展示；不生成结果、退出码或成功回执。
匹配到完成记录时仍只采用对应完成投影，空参数不覆盖开始输入，旧 MCP end
格式的完成结果也不会被开始投影覆盖。这是过程可见性，不证明外部动作发生，
不改变运行终态、审批或重试资格。

服务捕获的 source_bindings 含凭据或未通过签名链接安全边界时，同样按 `runtime_result_source_invalid` 记录（规则见上）。模型自行生成的敏感值仍由原有结果边界拒绝；不删除来源字段、放宽凭据检查或把该失败改成 needs_human。

Task 验收读回在 SQLite 快照内取得准确的原生引用，关闭快照后读取该范围
内运行时实际选择的最后一个 schema 合法候选。验收保留所选候选的原始
字段，不用模型默认值补齐缺失证据，不回退到已移除的 SQL 决定正文。
原生决定缺失或全部候选无效时明确验收失败；业务投影仍以既有 SQLite
应用回执核对。本修复不授权生产语义导入，也不代替真实业务样本验收。

已完成的邮件分类任务不长期保存分类输入正文：`email_agent_classification_tasks.status=done` 在既有租约／代际校验的完成事务内，将 `input_json` 改为明确标记的来源记录，保留原始 JSON 摘要和字节数、去除 `scheduled_consumer` 后的不可变输入摘要、稳定邮件身份、provider locator、配置版本及脱敏退订候选引用。定时配置与 Skill 只保留运行／版本／摘要引用；模型输入、邮件正文和冻结 Skill 正文不再复制。重复发现以既有不可变输入语义核对摘要，不补回正文；内容变化仍按既有错误处理。pending／running／failed 保留完整输入以便恢复，分类结果、任务状态和执行回执保持原样。历史精简由显式维护命令执行，不在初始化时扫描。

稳定 UID 集合查询从索引 `idx_emails_class_account_locator`（account_id、class_folder、uidvalidity、class_status、class_updated_at desc、uid）读取，不再为文件夹和 UIDVALIDITY 过滤回读整张宽表；provider 元数据查询仍读取准确分类行，不使用缓存或重建内容。当前版本缺少该索引视为结构损坏，不在读取中自修复。

精简后的邮件来源记录同时保留业务分类 `category` 和候选来源 `action_parameters.candidate_source`，确保已完成 Attempt 详情的业务显示不变；其他参数正文不复制。日历卡片修订仍只比较呈现正文（精简后使用原文摘要），定时扫描运行 ID 的变化不会重新打开已完成任务。

## Schema publication and failed-start rollback

app.deploy、独立 repository_updater 模块和 CLI repository-updater 命令使用
ExistingSchemaUpgradeStateStore 读取部署状态，避免在 quiet/停止/备份前
构造 AutoReplyStore。数据库 publication 在 checkout 后、dependency/import
验证前捕获旧 schema；在停止窗口迁移后再验证和启动。

遇到不兼容 schema 时不删数据、不尝试旧代码启动。
restart callback 调用前即标记 attempted，使部分 bootstrap 失败也会停止
replacement。bootout 报错只有在原生 launchctl print 明确证明 label 不存在
时接受；权限或其他停止失败仍是错误。

每次新 schema 逆迁的首次上线必须由已更新的 deploy 调用方执行：已运行的旧 Python
updater 不会因为 fast-forward 而获得新 callback。后续更新后的独立 updater
入口也使用相同的停止、数据库 publication 和启动机制。

## Email current plan deduplication

计划由 `current_action_plan_id` 指向 `email_action_plans` 唯一读取；当前写入不保存重复 JSON。
分页列表一次批量查询相关计划，避免每条分类单独连接/查询。

模型异常归因的存在性查询只查分类主记录，不加载计划，也不表示计划有效。扫描使用的
原已有分类判断和完整读回继续
校验不可变计划；已有分类的计划损坏或版本冲突必须报告持久化冲突，不能误判为尚未落库
而回退到 Agent。对分类元数据漂移的修复不能覆盖或修复被篡改的不可变计划。

## SQLite connection lifecycle

`AutoReplyStore._connect` keeps one idle SQLite connection per database file
and thread, because every new connection re-parses the whole schema. Nested or concurrent use in the same thread still opens its own
connection, and a connection is discarded rather than kept when the body
raised, a transaction is still open, `foreign_keys` is no longer on, the
database file was replaced, or the store's `_open_connection` differs. The
"close" time in the slow-context diagnostics is therefore the time to return
the connection to the idle slot. Code that registers functions, handlers or
pragmas on a `_connect` connection must undo them, because the next call in
that thread sees the same connection. A connection going idle releases its
page cache (`pragma shrink_memory`) and keeps only the parsed schema.

> 已移出的历史章节与迁移实施记录（日期化清退日志、Store/Email 各期迁移、Phase4C、Email13/29、Native-source scalar 等）见 [archive/runtime-mechanism-superseded-notes.md](archive/runtime-mechanism-superseded-notes.md)。

## Task2D7B Workbench Turn native execution

Workbench runtime lifecycle and exact readers use canonical decimal `tasks.id` for Turn DTO/API identity. `tasks(kind='workbench_turn')` owns lifecycle status and lease; `workbench_turns(task_id)` owns source-backed Turn control and request fields. Parent archive fencing, create/idempotency, claim, lease renewal, stop, completion, stale recovery, event sequencing, timeline/SSE, artifacts and statistics read or write native owners. Events and artifacts use native Turn Task foreign keys. The executor/runtime-attempt contract is `workload_kind='workbench'` with `workload_key` equal to that canonical Turn Task ID; native trajectory is hydrated from the selected RuntimeAttempt session/range, and no logical Run or duplicate UUID alias is created. The current Turn contract no longer exposes `waiting_confirmation`; the retired confirmation workflow has no current status or API entrypoint. Source artifact event `payload.artifact_id` is the one translated typed reference: migration resolves it through the Task identity map, while inverse consumes explicit source-public identity metadata and checks the artifact's Turn owner. A newly native-created artifact receives a numeric source `legacy_key` from the artifact kind's source rowid allocator; its public UUID remains separate. Other payload members remain unchanged. Feedback Workbench references at public API boundaries are canonical decimal strings; optional empty item associations remain accepted as before.

### Full document native output delivery

Consumer 通用 Skill 约定只规定独立选择并应用适用 Skill；每轮 Task 发现入口统一决定何时读取，避免通用层的读取措辞与冻结来源复用条件冲突。安装来源和未选择 Skill 的既有读取入口不变，不新增缓存或读取回执。

Consumer/Audit 的原生 `read_dingtalk_messages` 单工具输出限额为30000，用于完整交付已请求的消息字段；该设置冷启动和续会话均保留。不提高全局输出限额，不改变窗口工具、正文或分流/审核规则；长目录与文件读取保持各自现有单工具设置。

`read_dingtalk_messages` 可用 `direct_open_dingtalk_id` 传入触发消息的对方身份；该参数即选择单聊路径，无需同时设置 `single_chat=true`。DWS 通过 `list-direct --open-dingtalk-id` 读取，不把单聊 CID 当作用户身份。显式设置 `single_chat=true` 却未给身份且标题为空时，在服务工具边界明确失败，避免空字符串联系人搜索；标题解析和群聊读取沿用原路径。此变更不增加 Audit 规则或改变消息正文投影。

`agent_cli.read_dingtalk_messages` 只在每条消息顶层字符串与同条 `raw_payload` 直接字符串字段完全相同、且按 FastMCP 实际缩进 JSON 格式放入 `messages` 数组后的模型可见文本更短时，才将重复的原始字段值改为 `null`，增加 `raw_payload_field_refs` 指向仍完整保留的顶层字段。展开引用可恢复原始字段值和键顺序；未重复字段、引用消息、反应、资源、身份、消息数量和顺序、失败结果保持原样。此投影只发生在该工具返回边界，不改变 DWS 查询、其他 MCP 工具、Prompt 或审核策略。

`agent_cli.read_dingtalk_sheet_range` 只在 DWS 成功返回且按 FastMCP 实际缩进 JSON 计算更短时，将各单元格重复的完整 `cellStyles` 对象放进顶层 `sharedCellStyles`，以原字段位置的 `cellStyleRef` 索引引用。原始 JSON 的行列、单元格值、样式内容和键顺序均可还原。失败或较小结果保持原样；不改变 DWS 查询参数、角色工具集、Prompt、session、路由或审批策略。单块输出仍受 Codex 有限大小约束，服务不会把 `success=true` 解释为模型已读完整表格。

OA pending scans carry the complete scheduled Consumer wrapper, including exact frozen Skill materials, into the claimed reply input. Consumer and Audit read those original bodies through read_task_skill; the scanner must not use the Task extraction projection that omits skill_materials. Task extraction sources retain their existing body-free scheduled context. Review deduplication includes a digest of selected OA Skill bodies, so a changed rule schedules one fresh review for a still-pending approver even if DingTalk records are unchanged; it waits for any same-approval review already pending or processing to settle before enqueueing that new revision. An unchanged Skill snapshot stays deduplicated. For org-structure approvals, applicant-correctable missing facts with a valid OA return path use a formal return; comments are reserved for missing return paths or genuine external-party waits, with no added document-format requirement. This changes input delivery and the selected OA policy, not routes or sessions; old inputs are not rebuilt.

OA review round binding (2026-10-15, completed 2026-10-17): when the scheduled OA scanner enqueues a new review input, it reads the current scheduled-task Skill selection again. For each selected managed Skill it resolves the newest registered revision directly from the managed Skill store. A revision missing from the worker's immutable startup snapshot (`managed_revision_not_loaded`) can be materialized for this new trigger; a disabled or otherwise unavailable latest revision stops the scan instead of falling back to older guidance. Selected operation Skills are read from their current authorized files. These exact bodies are frozen in the new OA trigger wrapper. Retries and corrections reuse their existing trigger snapshot; historical inputs and runs are immutable. A binding failure leaves the OA scan unadvanced and records the reason. Approval permissions, business thresholds and action gates do not change.

Service Consumer turns prefer their declared `mcp__agent_cli` tools for DingTalk reads and may use local `dws` commands through shell for source reads when appropriate. The OA Consumer may use the selected OA Skill's `dws oa approval attachment authorize-preview` and same-file read/download steps when native preview permission is missing. Consumer must not use `dws` through shell to send DingTalk messages or email, or to perform OA approve, reject, return, or comment actions; those effects use the reviewed typed-action path. Audit uses declared read tools only and must not use shell or local `dws` commands. OA instance/task identifiers passed to read tools come verbatim from the current pending/detail fields; parameter or response-shape errors trigger source-value verification before a safe same-target read retry, and are not treated as missing applicant evidence. Missing native read capabilities remain explicit dependency failures. `search_dingtalk_documents` publishes `page_size` 1–20 in its native parameter schema; invalid sizes fail argument validation before DWS, while omitted and valid sizes keep the existing query behavior.

The task-bound `read_dingtalk_messages` and `read_dingtalk_messages_in_window` resolve the direct-chat counterparty from the claimed trigger's stable openDingTalkId when given that task's conversation ID. The resolver compares task generation, original message ID and conversation ID before use. The window path calls `read_complete_message_window`, so it returns `complete=true` only after the existing pagination and query-range checks; its messages must still belong to the bound conversation. The declared MCP tool set exposes reads only; Consumer shell use of `dws` is separately limited to reads by the role instructions and must not send messages or email or perform OA actions.

### 日历组织者来源字段

日历解析继续生成原有 organizer 显示字符串，同时在 organizer_details 中保留
provider 返回的完整组织者对象。日历事件读取、窗口读取及既有 model_dump 任务输入
因此可看到原始 userId、openDingTalkId、邮箱及其他来源字段，字符串标识和嵌套值
不被改写。来源只有字符串时 details 为空，不根据显示名猜测稳定身份。新增对象是
来源证据，不能冒充接收人核验或授权结果；既有动作、Audit、通知和运行路线不变。

Recorded DingTalk delivery recovery (2026-10-14): startup reconciliation, explicit failed-task completion and protection against failed-run projection all honor the receipt verification state. A recorded receipt with verification.state=ambiguous or failed does not prove delivery and cannot restore done. Positive sent receipts and previously recorded legacy readback receipts keep their existing completion behavior.

Reply startup recovery composes the existing delivery, failed-run, confirmation,
unsubscribe, terminal-attempt, settlement, human-projection, and interrupted-run
reconciliations against the native Task projection.
Email unsubscribe settlement joins the native `email_unsubscribe_receipts`
projection by action identity and reads its source `outcome` field; it does not
infer the domain outcome from a provider result envelope.

Historical terminal Attempt or settlement projections cannot override a current external_action_uncertain/external_action_failed task error. A later verified provider result uses the normal execution completion writer. Original Attempt and settlement history remains unchanged.

An operator may explicitly stop a current failed `external_action_uncertain` Reply task through
`GET /api/console/reply-tasks/{task_id}` followed by `POST /api/console/reply-tasks/{task_id}/skip` with
the observed `execution_generation` and a non-empty reason. The generation and uncertain failure are checked
again inside the write transaction. The task becomes `skipped`; its external-action error and original Attempt
and Agent runs remain unchanged, and an immutable settlement error records the operator's reason. This records
that no more work should run; it does not report the external action as successful or verified.

Attempt detail and History attention prioritize the current failed task delivery error over historical Agent success summaries or a stored sent-reply row. Uncertain delivery is displayed as unconfirmed, while provider failure is displayed as failed. Historical Agent explanations and original raw receipts remain available as historical evidence.

Attempt detail 的 System Execution 只对当前审核候选，或已有执行记录的历史候选生成投影。过期候选若没有执行记录，仍可作为历史审核证据查看，但不把其中未开始的动作显示为“等待执行”。已有历史执行和 canonical provider receipt 继续只读显示；此投影不改变队列、重试或执行状态。

### 原始触发、当前来源和立项财务口径（2026-10-14）

立项财务 Skill 按本次项目范围、期间和综合口径判断毛利；历史试做行不自动代表当前 POC 或第一曲线项目。先核对成本是否已包含，避免重复计入。实际适用的第一曲线项目综合毛利低于 25% 时，原有特批路径继续适用。修订通过新的托管 Skill revision 发布；历史任务冻结输入不被覆盖。

会议工作人格来源引用（2026-10-14）：Meeting Alignment 每轮只取得一次实际注入的工作人格正文，来源引用为该正文的 `work_profile:sha256:<digest>`；历史来源按该引用回写，完整原生输入可重算摘要。会议结果不再被提示词要求回写本地文件路径。现有 codec 的本地路径和凭据限制不变。

Meeting 的决定默认是 `send`；面试（`skip_interview`，Derek 另行单独处理）和没有可共享信息量的会议（`skip_no_content`，不发一条空话）是两种不发送的决定（Derek，2026-10-28）：它们同样经独立 Audit，通过后任务分别终止为 `skipped` 和 `no_action`，不产生任何外发动作；Audit 认为会议有可共享内容时退回，走与 `send` 相同的有界反馈修订。Meeting Alignment 的业务总结优先发到按相关群讨论和参会人成员匹配选出的群；完整搜索后没有合适群时按下文「会议无合适群时的收件人」选择单聊对象。普通业务内容不要求逐一核验群成员的披露授权。参会名单不完整、无法与群成员匹配时，只要群讨论覆盖同一项目或客户且完整成员名单可读，就可选该群，并把这一缺口记入 audit_summary，不因此拒绝（Derek，2026-10-27）。群近期讨论的主题不必与本次会议议题重合（同一项目的群即对口）；Audit 退回目标时必须从已给候选中指出更合适的群及理由，不能只说证明不足（Derek，2026-10-28）。另外，人员评价、绩效、薪酬、晋升、去留、候选人结论、健康、请假及组织层面裁员/减员/人员优化信息必须从群正文和标题拆出，作为独立私聊发送给身份、当前职责和该内容披露授权均有实时证据的个人。HR 群或高参会人匹配度不构成合并敏感内容的例外；没有授权个人时省略敏感详情并记录未解决缺口。Planner 与独立 Audit 均应用此 Meeting 专属规则，覆盖通用受众合同对敏感群消息的条件性许可。

Meeting 的 Codex CLI 每轮显式设置 `project_doc_max_bytes=0`，省去 CLI 自动加载的工作区 `AGENTS.md`；服务装配的会议来源、工作人格、冻结托管 Skill、结果 Schema 与受众校验不变。非 Codex 路线不使用这个 CLI 配置。

Meeting Codex Developer 指令只保留任务特有的 DWS 群与身份只读取证提示；禁止直接对外发消息、邮件和执行审批的行为指令来自所有 Consumer 共用的同一文本。Meeting 只返回决策，由既有服务流程投递。这些指令不是 DWS 或 shell 的强制权限隔离；命令、凭据及投递实现均未改变。

会议 Skill 范围（2026-10-18；2026-10-21 修正）：服务仍保存并组装本轮全部冻结材料。仅 Codex CLI 路线在存在冻结托管会议 Skill 时，使用该完整托管规则构造实际提交输入，并在原生 Skill 目录保留 `dingtalk-chat` 入口供按需调用 DWS 群读取；其他重复 Skill 仍隐藏。服务已完成听记、日历等操作读取后，不再将对应操作 Skill 全文交给会议判断轮。Claude/Friday 及缺少托管会议 Skill 的旧任务保持原输入。原生输出纠错沿本路线的实际输入继续，切换路线时改用目标路线的输入；会议结果、受众和投递契约不变。匿名固定对照及早期校验漏判见 `docs/archive/records/meeting-managed-skill-scope-validation.md`；修正校验后的三轮结果见 `evals/trajectory_optimization/meeting_skill_scope_native.v3.json`。

已有外部动作的核验（2026-10-14）：System Executor 对已有 dispatched/uncertain journal 先调用具名处理器只读 reconcile；旧动作造成的来源变化不能阻止读取实际回执。该路径不再次 dispatch。所有尚未派发的新动作仍先检查原审批候选绑定的当前来源，已完成动作的身份和正文冲突检查继续执行。原生发送回执只从明确的发送返回结构读取 send-task 身份，引用的入站消息 ID 不能作为投递凭证。

正式部署的停止完成条件（2026-10-14）：在 bootout 前读取本次 launchd 服务的 supervisor 及其直接服务子进程 PID；停止等待同时要求标签已卸载和这些已知进程已退出，再进入备份、代码切换和数据库发布。沿用现有停止超时，不扫描或清理无关 CLI，也不增加审批步骤。

## Complete inline schema formatting

Claude Consumer additionally passes a compatible `ConsumerAgentWireResult` schema
inline through native CLI `--json-schema`. This projection retains the field
shapes and required fields but removes the wire schema's unsupported `oneOf` and
`allOf` conditions; the unchanged Pydantic wire/business models remain the
authoritative validator for those relationships and every result. The adapter
uses the native result's JSON text through the existing event normalizer and
parser. Audit and other Claude workloads keep their existing command; no route,
tool, Skill, prompt rule, retry or session policy changes.

Email action-family metadata belongs to the shared `actions` table. If its generated Email column, CHECK, collation or attempt parent FK is malformed, the genuine Store schema preflight raises `sqlite3.IntegrityError` before EmailStore's own current-schema validation, even with `validate_rows=False`. The Email Web route's existing exception mapping marks this `sqlite_integrity_error` and keeps Email unavailable. It does not repair the malformed structure or add a retry, recovery or authorization rule.

Service context JSON blocks use compact serialization rather than indentation.
Every original field, nested value, identifier and string whitespace/newline is
preserved; only JSON separator/indentation bytes change. Consumer and Audit share
this renderer. Candidate digest calculation, source binding, business validation,
role/tool boundaries, session reuse and historical saved inputs remain unchanged.
See docs/archive/records/context-json-formatting-validation.md for focused verification and
metadata-only measurements; publication status is recorded there.

### 会议人员方案披露与成员准确率（2026-10-15）

裁员、减员、人员优化的存在、进展、时间、名单复核、交接及替代培养安排本身均属人员敏感信息，没有个人姓名或人数也不得当作普通业务内容。会议总结中的任何人员敏感详情都必须从业务群正文和标题拆出，不再按群成员逐一核验是否可在群中披露；敏感部分只向身份、职责和具体内容披露授权均有实时证据的个人发送，无合适受众时保留未解决事项，不转发本人兜底。普通业务群根据相关群讨论和参会人成员匹配选择，不逐成员审查普通业务内容的披露授权；不为发送而选宽泛群。普通业务内容的审核不要求群讨论时间戳（候选里的 create_time 目前恒为空）和群可发送证明，可发送状态由投递时 `_group_delivery_state` 核验，不可发送则失败且不改投；成员匹配率与历史投递次数仍只是发现线索；HR/管理职务及参会人重合不能单独证明业务归属或个人披露授权。人员匹配采用3/N（匹配参会人数/完整群人数），不再使用参会人召回率。Agent 负责基于证据做敏感分类和个人授权判断，代码不增加关键词过滤或任意比例授权门槛。

会议消息新增 `message_title`，由 Agent 按实际接收受众核验：安全时可沿用会议原题，否则使用中性业务标题。正文首行与推送标题均只取此字段，不再自动拼接可能含人员方案信息的 `source.title`；没有已核验标题的持久化决策使用中性的“会议跟进”。原会议标题仍留在来源记录中用于追溯。

标题合同升级不会改写旧的已准备正文：旧决策未核验标题时采用中性标题；如已有未确认的准备正文与之不符，沿现有正文一致性检查停止，需通过原有重新决策流程生成新版本。已确认的精确回执仍按原动作核对，不重发以获取新标题。

Task Agent 每轮提交 Work Tracking Skill 的绝对路径和 SHA-256（所有可执行 Task 的路线相同）；冷启动读取全文，
续会话只在同版本全文仍在上下文时复用，否则重读。Codex 命令复用现有
`inlined_skill_names` 元数据，由适配器把已有路径筛选和同名隐藏规则合成一份
原生 `skills.config` 数组，避免重复配置键覆盖。仅隐藏同名安装副本的目录项。
其余目录选择、工具权限、输出 schema、路由与 session 绑定保持原样；不修改全局 Skill
或共享 AGENTS 配置。旧版同名入口的修复见 `docs/archive/records/task-inline-skill-once-validation.md`；
当前按需读取的验收与限制见 `docs/archive/records/task-skill-on-demand-validation.md`。

Codex continuation commands retain the caller-selected workspace through the native
global `--cd <workspace>` option before `exec resume`; cold commands are unchanged.
Continuation no longer inherits the service process directory. Workbench inserts
its existing model/MCP timeout options after the located exec/resume command so
global options remain intact. Native session IDs, source, schema, credentials,
permissions and route policy are unchanged. See `docs/archive/records/codex-resume-workspace-validation.md`.
Attempt current failure descriptions use only the latest Agent turn or the persisted final task/execution stop. A later completed turn supersedes earlier technical failures in the current-stop projection; historical runs remain available in the runtime trajectory and are never rewritten. A final Audit return/reject keeps its actual summary, and accepted but unverified System actions retain their uncertain execution diagnosis.

Audit field assertions are first bound to the exact current candidate. Only their sensitivity-check copy reuses the existing strict configured service callback-pair validation; original assertion values, candidate digests and persisted results are unchanged. Other Audit prose, malformed pairs and nonconfigured URLs retain credential detection. Fresh and recovered Audit results use the same check. Audit context identifies the service-prepared DingTalk signature and feedback postfix so Consumer is not asked to remove content the service reattaches.
For business-content judgment, the Audit context also renders each DingTalk message body after removing only the exact terminal service signature and the configured, validated feedback callback pair. The original candidate, destination and digest remain unchanged and visible for exact binding and field assertions; all remaining body text and all normal audience, factual and sensitivity checks still apply. This view does not alter approval or delivery behavior.

Attempt detail resolves a persisted run through its stable reply_task_id; message-key lookup is used only for records without a bound run. Current OA input keys can change while the original run keeps its task identity. Verified execution recovery can therefore project the settled task without rewriting historical Attempts or runs. 

Attempt current stop evidence is scoped to the current task execution generation, including an empty current-run list. Current failure summaries and codes come from that generation's final run, task error or exact current reviewed execution; an approved Audit summary cannot replace a failed or uncertain execution result. Historical run trajectories and execution details retain their identities, while their provider-risk rerun constraints continue to use the original Attempt. A settled done/skipped task has no current stop even if an old Audit returned its candidate.

The OA approval detail endpoint uses the same current-task-generation status projection as History detail for the newest Attempt. It returns the immutable persisted Attempt state separately as `recorded_status`, while older Attempts remain historical rows. This projection does not change the database or external receipts; uncertain current execution evidence is never upgraded to success.
Consumer and Audit both persist a new Codex session selected by the existing runtime retry router within the active run. After an idle timeout, a fresh Audit attempt may replace that run's current session just as Consumer does; each runtime attempt retains its original session, transcript slice and failure. A later Audit proposal revision continues the latest Audit-owned session from the same ReplyTask and execution generation, unless that latest run explicitly marks it noncontinuable. Session ownership comes from the latest native RuntimeAttempt at or before the current revision/Run and must match route name, runtime kind, credential mode and model; it does not depend on an OAuth route name or the historical Run projection. Every CLI-native Audit session event updates that existing projection to preserve the established repeated-invalid-result fresh-session rule, including non-OAuth retries. No Consumer session slot is used. Audit never reads or writes the Consumer conversation session slot. Every revised Audit still receives the current Task, full candidate and revision binding and independently checks current evidence. This changes Audit session continuity only; it does not change route selection, retry budgets, candidate binding, review policy or external execution.

Attention list, summary and count readers follow a historical Attempt's original AgentRun task ID when the OA scanner has since changed the trigger message key. A current bound task in done/skipped no longer appears as an unresolved historical Attempt. Same-generation failed tasks remain visible, valid reviewed human decisions keep their independent projection, and the historical Attempt/run/receipt records are unchanged.

## Consumer purpose output 和有界权限等待

新 Consumer wire 用 `action`、`reason`、`target`、`payload`、`evidence_refs` 表达业务核心；`evidence_refs` 对每个结果都是必填字段，无可用引用时返回空数组，权限重试必须列出验证访问请求的引用；保留 summary、评分、阶段及长期记忆元数据。purpose 来自当前任务已选择的 OA Skill 或明确 OA 动作上下文，否则使用普通 purpose。schema 和解析器由同一 purpose 动作定义生成，拒绝未知动作、不完整参数及非法 ID 类型。`plan` 的 payload 是现有完整 ConsumerProposal；直接动作规范化为同样的提案。OA `return` 规范化为 dingtalk-oa/revert_task；它与 Audit 的 return 反馈分属不同协议。OA 请求参数 revert_action 是 REVERT_FOR_APPROVAL 或 REVERT_FOR_RESUBMIT，REDIRECT_PROCESS 是外部操作记录类型。候选追加服务绑定的 purpose_output；旧候选不重写。

OA `request_access` 是普通 Consumer purpose 动作，不进入 Audit。target 必须包含准确流程和文件 ID；同一任务同一文件的权限请求去重，不同文件可分别申请。服务经现有 OA 评论执行器提交只读权限请求，并在同一 `service_state` purpose 记录里持久化 action key、分发时间、状态及回执。恢复时依据该记录精确对账；原回执未确认前不重发。Consumer 请求阶段必须结束当前材料审核；评论送达不证明权限已恢复。只有回执验证成功后才开始权限等待。权限未恢复时 Consumer 输出 `retry`，引用本任务已核验的权限申请回执；retry 同样不进入 Audit、不产生外部写操作，只安排预算内的延后重读。解析准备阶段和编排层均从当前任务已核验的原权限请求核对它确为 request_access，不解析 reason 关键词。兼容迁移前缺少 purpose_output 的旧 OA 候选时，只接受本任务唯一且完全匹配 `material-read-access-block` identity 的已核验评论；其他评论、未知 key、uncertain 回执和其他任务的请求不能充当成功申请。

预算持久化在 service_state，key 绑定 business_object_key（无业务键时绑定 reply task）和 purpose。原已验证请求的动作 dispatch 时间固定 72 小时硬截止；模型不能指定时间、次数或重试间隔，新的 receipt 不能重置已存在预算。Consumer 的回执引用在匹配前只剥离一个已知 `receipt:` 或 `prior_receipt_id=` 标记，随后与已验证动作存储的原始 key 精确比较。Consumer request_access 执行并核验后先等待，再进入后续 Consumer；Consumer retry 按同一 purpose 的预算延后重读。最多启动 3 次后续 Consumer 重读，每次固定间隔 12 小时；旧版本已排队的等待首次加载时同步更新为 12 小时间隔，不清零原次数、reservation 或 deadline。worker/scheduled/email 延后时保留普通 attempt 预算，遵守该延时。

每次实际准备启动后续 Consumer 前用既有 SQLite 短事务预留一次，最多 3 次。所有被请求文件共享一个业务对象和 purpose 预算。key 中不含 native session、proposal revision 或 execution generation；更换它们、重启和任务 attempt 退款都不能清零。预约使用 task/generation/revision/turn 唯一键，重复检查同一预约不重复扣数；进程在预约后退出仍消耗该次预算。到达 3 次或 72 小时后不再刷新来源或启动新的文件重读。OA purpose 在预算耗尽或权限申请明确失败时，让 Consumer 再运行一轮提出普通 `return` 候选；即使恢复记录里没有可关联的已完成 Consumer run，也启动受限的 Consumer 退回轮次，再走 Audit。该业务对象只认领一次 OA 退回轮次；后续重复触发挂靠该轮对应的已完成 Consumer run，进入 `permission_blocked` / `blocked`，防止重复退回。若没有可关联的 Consumer run，保留非失败状态并转人工处理。退回轮次不得再次申请权限或安排重试，但提退回前必须对被阻塞文件实读一次：能读则按正常审批处理，仍读不了才提退回。该候选经 Audit、执行器和节点读回，服务不合成或直接执行退回；候选无效或未产生时才转人工处理，外部权限问题不标记为 `failed`。非 OA purpose 仍可返回 `permission_blocked`，Attempt 为 `blocked`，ReplyTask 为 `needs_human` 队列外等待。History 的一次人工重读入口保留原预算，只用于确认权限已开通；仍不可读时不继续重试，也不执行 OA 动作。

## Deployment admission and drain (2026-10-20)

`python -m app.deploy` and `--restart` close the database-adjacent admission
flock first, then acquire the drain flock exclusively after already admitted
claim transactions commit. New dispatcher, direct email reply/classifier/provider,
Workbench and meeting delivery/Memory claims take admission nonblocking, acquire
the shared drain flock, and release admission before starting SQLite work; the
shared drain flock spans commit. The short admission turnstile prevents later
claims from starving an exclusive waiter. A closed turnstile yields an empty
claim without changing the source. Existing tasks keep renewing and finishing.
Incoming work is still saved and pending/retry rows stay queued, so deployment
waits for executing work rather than all future work.

The exclusive scope lasts through the existing stopped-service backup, checkout,
build, verification, restart, health and rollback sequence. Success or every
exception/timeout/cancel path closes the descriptor and permits claims again;
process exit also releases flock automatically. No pause bit, new task status or
retry-policy change is introduced. Independent direct email reply/classifier, Workbench and weekly-OKR claims,
processing provider operations count as in-flight even
before a native Agent starts. Processing Email actions are read through the
explicit publication source, including the current common Action status field;
a Store-only database without the Email migration ledger contributes zero Email
actions. Existing quiet timeout, backup and recovery rules remain in force.
Initial installation uses the prior normal quiet path because already running
old code has not loaded the new claim hooks.

### DWS 文档格式核验与字面内容

Consumer共用文档工具继续提交原始Markdown/JSONML工件；已执行写入算写入成功，核验状态独立保留，不因未核验自动重写。当前本机DWS核验按钉钉Markdown的双等号高亮语义处理作者输入，要求完整JSONML证明相同文字、规范化词间边界、高亮范围与颜色，再执行整体内容及表格对齐检查；不全局删除标记或忽略内容差异。需要保留双等号的标识符使用代码格式或转义，说明位于按需读取的dingtalk-doc Skill，不增加通用prompt。DWS源码修复仅提交外部PR，本机版本化发布与自然行为验收分别记录于document-write-recovery-validation.md；无新增写入重试、Audit规则或session机制。

### Current Meeting Run reference contract

Historical Meeting Runs without a native Task keep their original source Job
and Run/RuntimeAttempt/error history. NULL in the optional native Task reference
means unavailable; it does not enqueue or retry the old Job. Non-NULL Task
references use the current Task reader directly in HTML detail, API detail and
session-related history; lookup errors are not silently converted into historical
unavailability.

### 会议无合适群时的收件人（2026-10-28）

业务会议总结优先发到合适的业务群。完整搜索后没有合适群时，从来源明确的参会人中选择一人单聊；只有一位非当前用户参会人时发给该人，多人时优先选与讨论最相关者。不要求会议明确指派该人负责某项决定或行动。目标仍须绑定稳定 user_id，且不能是当前用户；敏感内容继续按单独的职责和披露授权规则处理。

对这类既有 `needs_human` Meeting，按精确 meeting ID 走 `replay-recent-meetings` 刷新当前听记与日历来源，再进入正常 Consumer 和独立 Audit。该入口保留原 Runs，并在任务锁、活动运行、投递 claim、已准备消息或外部回执存在时拒绝重开。
在同一 Meeting Task 的 Audit 反馈修订中，如果新候选丢失前一轮已读的群目标，下一轮 Consumer 会继续收到该群目标及其原证据，避免正文修订抹掉目标上下文。它仍只是候选；Consumer 重新选择，独立 Audit 和原投递门禁继续生效。


Reviewed Consumer Skill script implementation may live in a private installed package; the public `ci/reviewed-skill-scripts/` catalog pins its entrypoints and complete script-tree digest. Installation alone grants no script entrypoint. See [private script verification](private-skill-script-verification.md).

Reporting policy, scoring, tenant destinations and their executable implementations belong to independently installable organization Skills. The public reporting baselines carry generic discovery/evidence/delivery workflow only; scheduled tasks explicitly select the organization package for normal frozen script binding. Generic report publication preconditions name the generic digest, never the existing company digest.


### 当前投影与退休的 Workbench 确认状态

当前 Task delivery 与 Feedback 投影仅使用现行字段，不接受历史来源变体；历史转换的
字段顺序固定在显式来源投影模块。执行进度只由原生 Consumer Run owner 写入，旧计划
执行造数仅在测试夹具内。Workbench 当前状态为 queued、running、completed、stopped、
failed；已退休确认功能的 waiting_confirmation 不再进入 API 类型、计数或界面状态。
本次不改变原生 CLI 确认、外部授权、审查、回执或恢复策略，也不重解释历史任务状态。
