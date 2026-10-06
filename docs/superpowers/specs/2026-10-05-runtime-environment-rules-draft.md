# 运行时动态生成的环境与规则说明（已批准设计）

状态：**Derek 已批准开发（2026-10-05）；实现与本地验收完成，发布证据以 PR #15 及 runtime-context-validation.md 指向的记录为准。**

日期：2026-10-05（本次审核会话采用 America/Los_Angeles）。

范围：只核对 Consumer / Audit 的实际模型输入与角色能力，提出一段环境说明及其验证方式。本文不改变任务生命周期、授权、审核政策、工具权限、执行器或 Skill；不构成执行任何业务动作的授权。本设计已获 Derek 批准；开发实现、验证和上线证据见文末。

## 1. 推荐与依据

推荐保留稳定、已批准的业务原则，由本次 invocation 的最终运行配置、任务输入和角色工具目录生成简短环境快照。快照只说明“我是谁、处理哪一个原请求、在哪里工作、现在声明了哪些入口、哪些动作仍交系统执行”，真实业务事实仍按任务需要读取。

需要验证的假设是：把分散的角色、任务和能力信息连接到“原请求需要什么交付 → 缺哪些事实 → 用当前入口取证 → 交付已核实部分”的判断顺序，可能减少零取证追问及不完整候选。**本例不能证明加一段描述即可修复通用行为。**

| 方案 | 能解决的部分 | 代价或局限 | 本次建议 |
| --- | --- | --- | --- |
| 静态说明 | 统一角色与取证原则，修改简单 | 容易把 Codex 的工作能力写给 Claude，或把已移除/未声明工具写成可用；路径和任务身份易漂移 | 保留稳定原则，避免用静态文字声称本轮能力 |
| 从最终配置和角色目录生成简短说明 | 反映本轮角色、实际目录、能力差异及未知值；复用现有注册与配置 | 声明不证明连接成功，仍须真实调用；需在路由确定后填充 | **推荐；仅做描述，不新增权限或业务路由** |
| 预加载全量业务资料 | 可能减少个别读取步骤 | 资料量与隐私范围扩大、快照过时、上下文挤占；无法替代实时权限/日历/回执，也不知任务是否需要 | 不采用；保持按需读取 |

## 2. 本次只读事实核对

### 2.1 开发树与生产定位

先读取 `/Users/derek/.agents/AGENTS.md`、开发树 `docs/architecture.md`、`docs/runtime-mechanism.md` 和 claims；保留已有文件。开始核查时开发树为 `main`、`59d267dbc9cfd1a377c5486c3856fd00c37fdf86`，已有两份未跟踪文件：`docs/email-pending-feedback-preview.html`、`docs/superpowers/specs/2026-09-29-task-evidence-classification-and-project-authority-design.md`。

生产根目录从已安装 LaunchAgent 及 `launchctl print gui/<uid>/com.ceo-agent-service.main` 读取为 `/Users/derek/Services/ceo-agent-service`。该 checkout HEAD 本次核实为 `98d69c34350ce64cc96642ada5f8f7c265107832`，`git status --short` 无输出；launchd 主 PID 为 13715。主进程参数确认 DB 为 `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`，workspace 为 `/Users/derek/Documents/memory`。DB 使用 SQLite `mode=ro` 和 `query_only` 读取；没有初始化 Store 或迁移。开发树 `data/` 未被当作生产数据库。

这是读取当时的定位证据，checkout HEAD 与 PID 本身不证明任意业务已完成，也不单独证明每个进程内模块的加载版本。草案以该生产 checkout 的实际代码与已保存的模型输入交叉核对；实施前仍需重新核实版本。开发树较旧的“审核 Agent 执行”及长期规则选项描述不作为本次设计的新契约。

### 2.2 模型实际输入的组成

不能只看 `runtime_context_instruction()`。当前生产链路包括下列部分：

| 层 | 实际内容 | 本次核对位置 |
| --- | --- | --- |
| 原生 CLI 输入 | 原生系统指令、工作目录的 AGENTS/bootstrap、session 历史、原生工具及角色限定的 MCP schema | Codex session transcript；`app/wechat/codex_safety.py::make_role_agent_command`；运行适配器 |
| Consumer developer instructions | Runtime Invariants、Dynamic Skill、System Action Contracts、wire JSON Schema、通用能力、共享规则区、角色边界、Decision Evidence、应用结果规则、业务/操作 Skill 协议、Runtime context、工作人格 | `app/consumer_agent.py::consumer_developer_instructions` 及其调用链 |
| Consumer 当轮任务 | 可选 Scheduled Consumer Prompt；原业务对象/触发、近期消息、材料入口、原回执、当前执行时间、canonical UTC、stage、历史人工输入、Audit feedback、结果修正要求、图片输入 | `ConsumerAgentRunner._execute_claimed`；`app/agent_context.py::AgentTaskContext.render/render_business_context`；`app/agent_turn_runner.py` |
| Audit developer instructions | 独立 Audit Rules、wire Schema、角色与受控动作契约、取证要求、完整响应要求、Runtime context、工作人格及适用 Skill 内容 | `audit_developer_instructions`；`app/audit_agent.py::_execute_claimed` |
| Audit 当轮任务 | 原任务上下文、完整 Consumer 候选及 digest、proposal revision、stage、来源/已有事实与回执、结果修正要求 | `app/agent_context.py::AuditTurnContext.render`；`app/audit_agent.py` |
| Runtime 传递 | Codex 分别传 developer instructions 与 task prompt；Claude 合并在 `<developer-instructions>` 与 `<task>` 中，角色 MCP inline 配置只连接 agent_cli | `app/codex_runtime_adapter.py`；`app/agent_turn_runner.py`；`app/claude_runtime_adapter.py::claude_input_contract/_invocation_boundary` |

实际首个内容轮 Consumer run 24747 的保存 transcript 为 session `01a10edc-ae53-79b0-8600-25eefdc8b672`。已核对 session 的原生 base instructions、developer message、bootstrap user message、当轮 task message 与 turn_context 的完整层次；只在此记录必要摘要，不复制工作人格、整份会话或私密日历。

- 原生 base instructions 17,730 字符；developer message 68,863 字符；首轮 task message 18,324 字符。这些是字符数，不是 token 数。
- developer 已含 `Configured principal: 磊哥`、workspace `/Users/derek/Documents/memory`、Skill root `/Users/derek/.agents/skills`，以及 `ceo-message-triage`、`dingtalk-chat` 内容和 System Action Contracts。
- task 已含原触发、权威 message_id、stage 0、当前时间 `2026-10-05 18:38:22 -0700` 及 `2026-10-06T01:38:22+00:00` 的 canonical UTC。
- 原生 turn_context 已记录实际 cwd 为该 task/generation 的 `consumer-artifacts` 目录、模型 `gpt-5.6-luna`、effort `medium`、workspace-write、command network disabled。
- 服务共享规则代码读取的是 `~/.agents/AGENT.md`（单数），该 developer 区实际显示 `No host-specific shared agent rules are installed`；CLI 另外注入了指向 `~/.agents/AGENTS.md`（复数）的 bootstrap。因而不能说模型没有任何共享规则，也不能说服务共享规则区已完整加载 canonical 文件。此处只记录输入来源差异，不顺带修改规则加载。

因此，已证实环境信息存在。较长且分散的输入、规则冲突、模型选择和 session 历史可能影响取证行为，但本次没有隔离这些因素，不能判定它们就是根因。

### 2.3 本轮角色目录，而非永久工具清单

生产 `build_role_server()` 对 Consumer 和 Audit 注册同一组 **33 个读取入口**；Consumer 另有 4 个普通材料/报告入口。Codex 最终命令通过 `AGENT_CLI_READ_TOOLS`、`AGENT_CLI_CONSUMER_TOOLS` 和 `ROLE_MCP_READ_TOOLS` 限定目录。这里记录当前代码快照的能力类别及代表入口，不把它写成永久全量清单。

| 能力 | Consumer | Audit | 真实入口/限制 |
| --- | --- | --- | --- |
| 业务与操作 Skill、本地文本、PDF、表格等材料读取 | 声明提供 | 声明提供 | `read_skill`、`read_text_file`、`read_spreadsheet`；遵守现有材料根目录、格式及大小限制 |
| 钉钉文档、权限、云盘元数据、表格 | 声明提供 | 声明提供 | `read_dingtalk_document`、`read_dingtalk_document_permissions`、sheet/drive 入口；元数据不等于正文可读 |
| 钉钉本人日历 | 声明提供 | 声明提供 | `list_dingtalk_calendar_events(start,end)`、`read_dingtalk_calendar_event(event_id)`；列表读的是当前 principal 的日程，不接受任意参与者 user_id |
| 钉钉会话、消息、回复线程、听记、联系人 | 声明提供 | 声明提供 | `read_dingtalk_messages`、`read_dingtalk_messages_in_window`、`read_dingtalk_thread_replies`、`read_dingtalk_minutes` 等；姓名搜索不证明可用性或收件范围 |
| OA 实例、当前待办、任务/记录/退回目的地 | 声明提供 | 声明提供 | `read_dingtalk_oa`、`list_dingtalk_pending_oa`、`read_dingtalk_oa_tasks` 等；读取不提供审批执行权 |
| 当前任务工件读回 | 声明提供 | 声明提供 | `read_task_artifact`、`list_task_artifacts`；绑定任务及执行代 |
| 普通工件与已有绑定报告文档 | 声明提供 | 无写入入口 | `consumer_artifact_write`；`consumer_document_write` 限定系统解析出的 scheduled report；周报 validate/render 有绑定条件，普通聊天不能借用任意报告目标 |
| 本地代码、计算、补丁 | Codex 原生支持 | 不提供命令执行 | Codex 在当前任务目录及原生临时目录内执行，shell 网络关闭；Claude 内建只有 Read/Glob/Grep 加角色 MCP 材料工具，没有 shell |
| Memory、Exa、小青读取 | Codex 按当前配置与 allowlist 声明；Claude 角色 MCP 不含这些服务 | 同样取决于 runtime | 当前 manifest 声明这三个服务；角色 allowlist 仅开放相应读取。安装配置不是连接/认证成功证明，不把个人会话的全部 MCP 能力赋给后台角色 |
| 系统注册且要求审核的动作 | 形成完整 proposal | 只读审核整份候选 | 由 SystemExecutor 调度，不是 Agent 工具。精确结构见 `docs/system-action-contracts.md` 及执行器 registry |

以上核对的是服务注册目录、最后的 CLI allowlist 和真实 transcript 中的已调用入口，没有在此新开 Agent、探测登录或调用业务来源。Lily 实例中保存的工具事件另外证明 Consumer/Audit 都实际调用过日历入口；不据此承诺下次仍已认证。两角色没有浏览器、图片生成、委派或自动 Skill 安装入口；Friday 当前不满足这两个角色需要的 `role_bound_agent_tools`。

## 3. 可直接审核的中文规则正文

以下正文记录获准设计规则。其角色与受控动作边界复述 Derek 已确定的架构；“原请求—事实缺口—入口取证—部分交付”的连接方式和环境快照呈现已获本次批准。实现使用同一原则的精简指令，最终文本见 app/runtime_prompt_context.py，验证与发布证据见 docs/runtime-context-validation.md。

> 你正在 CEO Agent Service 的一个后台任务回合中工作。你的本次角色、代表的 principal、原始业务对象、触发、执行阶段、时间、实际工作目录与工具入口，以本次运行时快照和原任务上下文为准。交互会话中的安装能力或历史会话能力不等于当前后台角色能力。
>
> 先识别原请求要求交付给谁、交付什么，以及完成它需要哪些事实。原触发是本任务的权威请求；近期上下文、材料、记忆和反馈用于补充事实，不能悄然把它换成另一个请求。收到反馈后，保留原业务目标，用新证据解释改变，生成完整修订。
>
> 将所需事实与已核实事实比较，再通过本轮实际声明的来源读取入口按需取证。能够自行读取的资料，应先尝试读取；不要把尚未尝试的系统取证转成要求 principal 重复提供资料。读取相关正文、时间窗或业务对象，避免为填充上下文读取与任务无关的全部资料。
>
> 区分入口已声明、本次调用成功、来源内容已核实、业务判断已成立、动作已完成。入口存在不证明认证、权限或外部状态正常；记忆、配置、模型摘要与旧回执不证明当前外部事实。使用来源时保留足以追溯的对象、读取时间、覆盖范围与错误。
>
> 其他参与者的事实尚未确认时，先完成自己能够核实和交付的部分，并明确剩余协调责任。只有缺失事实确实阻止该部分交付时才提出聚焦的问题。不要将多人全部确认设为原请求没有要求的前置条件，也不要替他人声称可用、授权或已承诺。只要求真正需要人提供、系统无法取得的剩余事实或动作。
>
> 日历占用的读取范围只支持该身份、时间窗和来源下的判断。“在已读取的日历中未见冲突”不能扩大为本人一定可出席，更不能证明其他参与者空闲。候选时间写明日期、起止及已确认时区；不把候选当成最终约定。原请求未被取消、完成、替代或明确过期时，单纯经过一段时间不足以把它收口为无需处理。
>
> 日历、约会、候选时段及邀请响应等任务，必须同时考虑本人和对方的时区；有协调者时也要让协调者能够准确理解时间。先从原请求、有效日程字段和当前可用来源核实每个相关参与者的时区与适用日期，记录来源；不能用服务进程时区、本人时区、公司所在地、姓名或消息时间戳格式代替对方时区。较早的所在地或时区只能作为线索，出差或新信息冲突时需核实。
>
> 对已确认的命名时区，按会议日期处理夏令时、跨日以及不存在或重复的当地时间，统一为同一 UTC 时刻核对，再按本人和对方当地日期、起止时间及 UTC 偏移展示候选。筛选时考虑已知工作时段、日历占用和对方明确偏好，避免只因本人无冲突就推荐对方深夜或明显不便的窗口；未知工作时段不能被编造成固定限制，也不由本规则新设一套审批门槛。
>
> 对方时区尚未核实时，先使用本轮现有能力按需查找；无法取得时明确写“对方时区待确认”，给出本人侧带明确时区的暂定候选，并由合适的请求方或协调者确认剩余事实。只有时区缺口确实阻止当前必要判断时才提出聚焦的问题，不直接把可自取的取证推给 principal。已知时区不等于已知可用性；未知时区也不代表不能先交付本人候选。不得把暂定窗口称为双方均合适，或据此声称已约定/已创建日程。Audit 独立核对相关时区证据和换算；技术读取失败仍保留原失败，不伪装成时区业务选择。
>
> Consumer 可以使用本轮普通工作工具完成文档、文件、研究、分析、报告及计算，并通过真实工具结果和内容读回证明普通工作的结果。本轮 runtime 不提供某种普通能力时，如实说明该限制；工具产生写入本身不使普通工作变成系统受控动作。报告文档接口只能用于本任务已有的真实绑定。
>
> 对当前系统任务中已注册且要求 Audit 的业务动作，Consumer 提交完整、精确的 proposal；Audit 只读审核整份候选，决定 approve、return 或 reject；System/Executor 执行已持久化、审核通过的精确方案并保存真实回执与必要读回。Consumer 和 Audit 不直接执行这些动作，不通过普通工具、另一通道、嵌套 Agent 或命令绕过边界。
>
> 注册动作的边界来自现有系统任务范围与动作契约，环境说明不创建审核项、不扩大授权、不制定新审批政策。完整输出必须符合本轮 wire Schema；自然语言 trace、摘要或整份 result 的关键词不能被当成已经执行的证明。
>
> Audit 按原请求判断候选是否完整，按同一来源范围独立复核需要实时状态的关键事实。具备读取入口且有稳定目标时应自行读取，不要求 Consumer 复制原始工具输出。需要改变业务内容时给出一致的规则、观察与具体修订要求；真实读取、认证或工具故障走现有 failed/技术恢复路径，不消耗业务内容修订来假装修复依赖。
>
> 缺值写为“未提供”“未核实”“本轮未声明”或有具体原因的“读取失败”，不得补造。技术、provider、认证、schema 和模型输出失败保留具体错误，不能伪装成需要 principal 作出的业务选择。真正只有人能提供的登录、访问或材料动作，说明准确来源和必要操作，并按现有候选/审核规则提出；已有技术故障的业务 run 仍如实失败。
>
> 历史风险拒绝及其原错误不得因换目录、工具、渠道、执行代或动作身份被绕过；不重试被拒动作也不代表原业务已完成。使用已有正常恢复契约，不由本说明追加例外。
>
> 本次环境快照只描述生成当时的任务与声明能力，不是实时外部状态。业务证据过旧或范围不足时按需更新。不得将凭据、token、headers、私密日历正文、内部敏感配置或无关资料写入环境说明或对外回复；对外只交付原请求需要的事实、判断与结果。

## 4. 动态字段与真实数据源

动态部分是事实投影，不能让模型据任务文字自行编造配置、认证或权限。显示名称用于理解，稳定 ID 用于绑定；业务目标的解释仍由 Agent 从完整原请求形成，不由新关键词路由器替代。

| 动态字段 | 真实数据源及已有入口 | 缺失/边界 |
| --- | --- | --- |
| principal display name | `app.config.principal_display_name()`；本例已保存 developer 为“磊哥” | 配置展示名不是 provider 登录身份；provider 身份必须有相关读取回执 |
| background turn / role | 本次 `AgentRun.role`、Consumer/Audit 调用者 | 不从 prompt 自称或任务文字推断 |
| task / generation / stage / revision | `ReplyTask.id/execution_generation/business_object_key`、`AgentTaskContext.stage_index`、run.proposal_revision | 缺失明确写未绑定；不借用其他代次 |
| 原业务对象与触发 | `AgentTaskContext` 的 channel、conversation、trigger_message_id、trigger text/create_time、raw payload；对应不可变 source input | 保留原请求；敏感/无关 raw payload 不整体复制进环境段 |
| 后台系统任务与调度来源 | 直接 Agent Cron 输入使用 `app/agent_cron/context.py` 的 scheduled execution payload；服务命令发现的消息使用原 trigger 的 `scheduled_consumer`（`ServiceCommandConsumerContext.scheduled_task_id/scheduled_task_run_id`），由 `worker.py` 解析为 consumer_prompt/skill_protocol_override | 分开记录 reply task 的业务 channel 与后台任务/来源 run；普通消息也可能有扫描来源，缺绑定则写未提供，不按 channel 或标题猜 |
| snapshot/execution time | 在本次实际调用时间捕获时钟；复用 context 的 current_time/canonical UTC | 不把文档日期、触发时间或模型自报作为执行时间 |
| named timezone / offset / source rule | 本次 runtime 已确认的时区配置或任务显式时区；context._canonical_context_time；当前 context 的 offset | 当前 helper 只产生当地 offset，并不提供 IANA zone；不能由 -0700 唯一推导 LA。无 zone 写未核实；钉钉无时区来源按现有 Asia/Shanghai 规则解释并标明假设 |
| 日历参与者时区与适用日期 | 原请求/当前上下文中明确的参与者时区；已读取日程实际返回的时间与 zone 字段；当前工具确实可读的相关资料或已确认偏好。逐人保留身份、zone/offset、来源、读取或确认时间与适用日期 | 不是现成统一配置字段，也不假定联系人/日历工具提供他人时区；没有实际来源写待确认。源时间戳的 Asia/Shanghai 解释规则不代表所有参与者都在中国；事件显示时区也不自动证明所有参会者所在地 |
| 参与者当地候选及工作时间约束 | 会议具体日期、已确认的命名时区及现有时区库换算结果；实际日历占用、明确工作时段与偏好 | 保留同一 UTC 时刻以及双方当地日期、时间与偏移；已知约束才作为事实，不编造固定办公时段或由时区推断空闲 |
| workspace / task cwd / source root | `workspace_path()`、最终 Consumer command 的 `--cd`/原生 cwd、`_task_file_root(...,create=False)` 的已有绑定；MCP 的 cwd | 区分资料根目录、任务工件目录、MCP 服务源码目录；不要为生成说明创建目录或修改 DB |
| runtime/route/model/thinking | 路由器实际选择结果、最终 adapter command/对应 runtime attempt | 首选路线不是最终路线；发生 fallback 必须按实际选择重新描述 |
| 普通工作能力 | 最终原生 feature/sandbox/namespace 配置与 Consumer/Audit 角色 MCP schema | Codex/Claude 分开；网络关闭限定 command，不能错误写成所有 MCP 读取不可联网 |
| 来源读取入口 | `build_role_server()` 导出的工具名、description、参数 schema、annotations，与最终 runtime MCP transport/allowlist 的交集；原生可见目录 | 注册而未注入不能写可用；已声明不等于已连接；不另设全量工具副本 |
| 外部读取连接/认证状态 | 本次实际工具结果或运行时已有明确失败证据，仅给来源及状态 | 尚未调用写“本轮未验证”，不自动探测全部账号、不输出敏感细节 |
| 报告工具的绑定 | `_bound_report()` 对本 task 的实际 scheduled payload/report kind | 声明提供但本任务未绑定时写“当前不适用”，不能暗示普通聊天可写任意报告 |
| 受控动作范围 | `docs/system-action-contracts.md`、现有 `SystemExecutor` handler registry、`docs/audit-task-scope.md`，与本次真实任务范围一致 | 只描述已注册且要求 Audit 的动作；工具有 write annotation 不产生新审核政策；unsupported 如实失败 |
| 规则/Skill 来源 | 本次实际渲染规则、runtime_skill_snapshot/skill_protocol_override 的精确绑定与现有 Skill 目录 | 已安装、已启用、已加载、已注入分别表述，不把可变 Settings 当本次配置 |
| 历史拒绝/已有回执 | 原任务已有结构化错误、prior_receipts、同业务对象动作记录及 System receipts | 只引用适用稳定身份；不靠自然语言关键词判断效果或新增风险策略 |

“声明提供”“本次调用成功”“本次读取失败”“本轮未声明”是说明中的自然语言状态，不拟议新增持久化状态机。来源缺失继续由已有 runtime 契约处理。

## 5. 实际填充的 prompt 示例

下面是**供审核的环境段呈现示例**，不是当前系统已经渲染过的新 prompt，也不是一次待发送任务。它采用 run 24747 已保存的首轮事实与当前核实的角色注册描述，展示拟议格式；没有重放该已完成业务。该原任务当前已 done，Attempt 15042 为 completed，不能把此示例投入生产执行。原始 ID 保留用于本地审核绑定，不复制日历正文或其他会话材料。

示例采用历史 execution time；当时 runtime 的命名时区没有独立证据，因此填“未核实”，不把本次设计会话的 America/Los_Angeles 偷换为该历史 turn 的新增事实。受控动作只展示该原请求相关的一个注册动作，其余精确契约仍由已有 schema/契约提供。

```text
【本次后台回合的环境事实；历史冻结示例，禁止生产重放】
角色：Consumer Agent A。代表的 principal 展示名：磊哥。
当前业务实例：reply task 386135；来源：dingtalk 群消息，非直接 Agent Cron 执行输入。
后台来源绑定：scheduled task 1（处理新的钉钉消息）；scheduled task run 129546；来源字段 scheduled_consumer，schema scheduled_consumer.v1。
原业务对象：message:dingtalk:cidLOo/K37uV2NHN4cn7RcgRw==:msgu4MuWca6BYOjbR1lyjtBDg==。
执行代：09fd462817b64c52acb9820c4b839c2a；stage：0；proposal revision：0。
原触发者：Lily；原消息：msgu4MuWca6BYOjbR1lyjtBDg==。
原请求：@磊哥 可以讨论。您和 Shawn 看看方便的时间，发我几个候选时段，我再协调安排。
原触发时间：2026-10-05 21:42:39；来源未带时区，按既有钉钉规则解释为 Asia/Shanghai；UTC 2026-10-05T13:42:39+00:00。
冻结执行时间：2026-10-05 18:38:22 -0700；UTC 2026-10-06T01:38:22+00:00。
执行地点命名时区：未核实；已知执行偏移：UTC-07:00。不得仅凭偏移推导命名时区。
本次 runtime：codex_cli；route：codex_oauth；model：gpt-5.6-luna；thinking：medium。
资料根目录：/Users/derek/Documents/memory。
本次命令工作目录：/Users/derek/Documents/memory/consumer-artifacts/386135/09fd462817b64c52acb9820c4b839c2a。
角色 MCP 进程的源码目录：/Users/derek/Services/ceo-agent-service。
Skill 根目录：/Users/derek/.agents/skills；本轮已注入 ceo-message-triage 与 dingtalk-chat 内容。

普通工作：可通过 consumer_artifact_write 写本任务本代次工件，通过 read_task_artifact/list_task_artifacts 读回；可在上述任务工作目录用 Codex 原生命令与补丁做本地计算。命令网络关闭；这不表示角色 MCP 的外部读取断网。
绑定报告文档：consumer_document_write 有任务绑定要求，本例普通群消息不适用。

本轮来源读取入口（声明，不是读取结果）：
- 本人日历：agent_cli.list_dingtalk_calendar_events(start,end)；需给明确时间窗。返回当前 principal 的日程，不能据此声称 Shawn 已空闲。
- 原群上下文：agent_cli.read_dingtalk_messages(conversation_id,title,single_chat,limit)；本例 single_chat=false，读取原群，不替换原请求。
- 指定日期窗口消息：agent_cli.read_dingtalk_messages_in_window；按其本轮参数 schema 调用。
- Skill/材料：agent_cli.read_skill、read_text_file、read_spreadsheet；其他声明读取入口见本轮真实工具目录。
本轮日历读取结果：尚未取得。认证状态：本轮未验证。Shawn 可用性：未核实。
参与者时区：本人命名时区在本历史首轮尚未独立核实；Shawn 与协调者 Lily 的时区未核实。触发消息按 Asia/Shanghai 解析，不代表他们位于该时区。
提出候选前按需核实相关时区；已确认时按会议日期换算双方当地日期/时间，考虑已知工作时段和偏好。仍未知时提供本人侧明确时区的暂定窗口，标明对方时区待确认，不声称双方均适合。
工具返回权限、认证或 provider 错误时保留具体原因，不能写成“请磊哥代查”的业务选择。

本请求对应的受控动作：dingtalk-chat.reply_to_message。
target 必须绑定上述原群 conversation_id 与原消息 message_id；payload.content 是完整精确回复正文。
这里只是声明契约，不是授权、审核通过或发送回执。Consumer 完整 proposal → Audit 只读审核 → SystemExecutor 执行并核验。

先根据原请求判断要交付的结果，再使用以上真实入口补齐事实。
其他参与者待确认，不应阻止先提供本人一侧已经核实的具体候选窗口。
日期、起止及时间解释要明确；不要编造他人可用性，也不要把候选时段当成已安排会议。
返回本轮 schema 要求的完整结果；禁止直接发送。
```

示例末段是通用规则，不是按 Lily/Shawn 姓名触发的分支。原请求的业务理解是：向协调者交付本人一侧可支持的候选窗口，协调者再与其他参与者确认。运行时不预填候选时段、不替模型判断内容，也不注入已完成后的回执来帮助对照组答题。后台来源身份来自本条消息真实保存的 scheduled_consumer，而非把任意普通群消息自动映射到 task 1；运行时仍使用实际绑定。

Audit 版本使用同一原任务与最新执行时间，角色变为 Audit；工作能力写为只读，移除 Consumer 普通写入/命令能力，并附已有完整 candidate/digest/revision。它应自行读取需要复核的动态事实，不因为时间自然流逝而另造业务目标；审核通过前不把候选认作完成。Claude 版本只描述其实际 role-bound agent_cli 与 Read/Glob/Grep，不能继承上例的 Codex shell 或第三方 MCP 描述。

## 6. 真正缺少或不清楚的部分，以及最小接入建议

已存在的 principal、workspace、Skill root、原触发、角色原则、执行时间及动作契约不重复设计。需要明确的增量主要是：

1. 当前说明中的 workspace 是资料根目录，真正命令 cwd 是 task/generation 工件目录。二者应同时且清楚显示。
2. 能力文字来自固定通用段，最终 route 才决定 Codex/Claude、native features 和 MCP 目录。应显示本轮真实差异，而不是把前一轮、个人安装或首选路线当成当前事实。
3. 现有时钟有 offset 与 canonical UTC，但没有普遍、已确认的 principal 或参与者 IANA timezone 字段。最小版先如实显示来源规则与未知值；日历任务按已有来源取证本人及对方时区，不能把机器时区或源时间戳规则用于所有参与者。新增时区配置另需明确数据来源，不在草案中虚构。
4. “原请求要交付什么”和“当前可以自行取得什么”之间需要更清楚的判断顺序。增加入口描述仍可能被忽略；是否改善主动取证必须对照评估。
5. 来源已配置与已认证、读取成功之间不能混写。本人日历与他人可用性不能混写。

若 Derek 批准后实施，建议只扩展现有 runtime context 的渲染方式和调用时机：稳定正文继续留在现有 Consumer/Audit 指令组装点，事实快照在实际 route 与最终角色工具配置已确定后形成，与该次调用一致；fallback 时更新 runtime 能力事实，原 trigger/业务对象不变。无需新环境服务、缓存、credential proxy、临时配置文件或第二套工具注册表。

后台来源 ID 当前在 `worker.py` 解析 `scheduled_consumer` 后没有作为独立字段留在 `AgentTaskContext`，仅保留其 prompt 与 Skill 协议。若需要在快照中显示 ID，应从本次已保存的原触发绑定明确传递，不能从 prompt 文本反向猜。其他原触发、candidate 和 wire schema 继续由已有块提供；正式环境段尽量引用已有对象，避免再次复制全部材料与规则。示例为审核展开呈现，不代表每轮都附全文档。

工具描述复用实际 role server schema 与最后的 allowlist/原生 feature 配置。短段展示来源类别和真实入口的必要描述，完整 schema 仍由 native MCP 目录提供；超出篇幅时注明还有多少已声明入口及原生目录位置，不误写成“没有该能力”。分类只来自既有能力元数据、角色配置与结构化来源类型，不新增人名/消息关键词/正则路由。

受控动作说明复用现有契约与系统任务范围，不通过工具 annotations 自动创造审批政策。普通写入、绑定报告文档与受控业务动作保持现有区分。现有角色 hash/session 兼容机制应纳入批准后的检查：稳定规则或目录发生语义变化要按已有契约处理，动态时间戳不能导致每轮强制新 session；本文不提出新的会话管理系统。

本草案不处理 singular AGENT.md 与 plural AGENTS.md 的加载差异，不裁剪工作人格，不迁移微信或独立 Email 流程，不重设计审核/执行/恢复，不改错码分类。实施时若发现需要动这些部分，应另行说明，不能借“环境描述”夹带进去。

### 6.1 Settings 必须能查看渲染后的 prompt

Derek 已确认的设计要求：**“要确保 settings 里面可以看到渲染后的 prompt。”** 本项属于本次交付的必要验收条件，不能只在运行详情显示，也不能把查看模板自身的变量替换结果当成已经满足。

当前 `Settings → Prompts → Rendered preview` 的后端仅调用 `render_developer_prompt_template()` / `render_user_prompt_template()`，没有调用后台 Consumer/Audit 的完整指令组装。因此，单独扩展 `runtime_context_instruction()` 不会自动让页面展示新增环境段。拟议接入必须同时包含现有 Settings 预览 API 与页面展示；这是一项 prompt 可见性改动，不增加执行入口或审核政策。

在现有 Prompts 的 Rendered preview 中提供 Consumer / Audit 角色选择，并允许使用已有任务或运行上下文作为预览依据。页面展示服务实际组装的完整 developer instructions 与当轮 task prompt，包括已注入的稳定规则、角色边界、系统动作契约、wire Schema、适用 Skill、工作人格、动态环境事实，以及原任务/候选/反馈。按最终 runtime 的真实输入格式呈现：Codex 区分 developer 与 task，Claude 展示实际合并后的两个标记块。不维护一份仅供 Settings 使用的规则或环境渲染副本。

“完整”限定为服务控制并提交的 prompt 部分。CLI 自行生成的系统提示、原生工具 schema 和历史 transcript 不得凭空补成所谓完整 prompt；页面明确标注这一范围，能力说明仍由本轮真实目录生成。必要的已有脱敏处理须显示标记，不能悄然省略后仍声称逐字原文；不显示凭据、token、headers 或无关私密业务正文。

预览明确区分以下两种依据，不能混为同一个“最终结果”：

- **当前配置预览**：按页面选择的角色、runtime 和已有任务上下文使用同一组装链路生成；显示渲染时间、配置依据、任务/代次和所选 route。尚未真正选择最终 route 时应写“所选 route 的预览”，不能称实际运行路线。没有绑定任务时，任务专属字段标为未绑定，提供可见的通用环境段，不虚构一次实际执行。
- **某次运行实际输入**：Settings 内可选择已有 run，读取该轮保存的输入或原生 transcript 中的实际提交内容及其配置事实，显示 run、角色、代次、revision、实际 route 与当时渲染时间。不能用当前 Settings 重新渲染历史后标成“当时模型收到的 prompt”。旧 run 没有足够记录时，如实显示实际输入不可用及原因；不得为了补预览重跑任务。使用既有记录，不另建审计数据库或全量业务资料副本。

动态事实区域只读，普通模板继续沿用已有编辑入口；查看预览不提交配置、不刷新业务资料、不触发 Agent、SystemExecutor、队列或消息发送。预览所需值来自已经存在的运行配置、任务事实和工具目录，外部认证/权限保持尚未验证或已有错误的真实状态。

此要求已确认应纳入设计；整体设计已经 Derek 批准开发。

## 7. 拟议冻结回归设计（本次不执行）

### 7.1 冻结条件和比较组

准备版本化离线 case manifest，以同一版生产 prompt/Skill/schema、同一原始消息与必要上下文、相同本人日历读取返回、相同参与者事实和时间窗为基线。来源使用受限的固定 fixture，只包含必要忙闲区间、范围/分页/认证信息，不带私密日历正文。预先固定读取时间与可见状态更新序列，两组按相同参数获得同样数据；不因为模型选择不同路径而给某组额外事实。

固定 runtime/route/model/version、thinking、工具目录、超时、内容修订预算、工具结果大小及任务身份；冻结 clock，每一内容轮按同一脚本推进，Consumer 与 Audit 的时间差一致。provider seed 可用时固定；不可用则注明采样不确定性。每组采用独立但等价的全新 Consumer/Audit sessions，另设完全相同历史的续会话用例；不得把原任务后续人工反馈、正确答案或已发送回执放入基线。

主要比较两组：A=修改前完整输入；B=原输入加本次待批准规则与动态事实段。另做可选 C=同规则的静态能力说明，用来分离“规则变清楚”和“动态目录”各自的贡献；不测试全量预加载。其余输入不变，报告新增字符/token 成本；两组最大输出与修订额度相同，不能靠给 B 更多预算取得优势。

内容反馈沿当前系统的首版加最多三次重提，工具/技术重试按相同设置冻结并单独统计；Audit 使用相同既有独立规则，B 的环境事实按自身角色注入，不添加本例答案或人工取证提示。每个正向场景先计划 10 对相同输入重复运行，记录每对输出与调用；扩展取决于批准后的数据，不能用一次成功宣称通用改善。

### 7.2 正向评分

独立 evaluator 读取原请求、调用参数/真实 fixture 结果、完整候选与 Audit feedback，不看组名与人工期待说明，不靠 trace 或 result 的自然语言关键词判断是否执行。预先冻结 rubric：

- 首个 Consumer 候选前是否主动读取原请求所需的动态事实；若原输入已足够，避免把多调工具误当改善。
- 是否向原请求指定的接收方交付可用结果，给出本人侧具体日期、起止与时区；是否明确其他参与者待确认，而非把主交付改成让他人重新报时间。
- 日历任务是否核实并考虑对方时区，按会议日期正确处理双方当地日期/时间及夏令时；是否使用已知工作时段与偏好筛选候选，未知时区与未知忙闲是否分别标注。不能只看本人无冲突而把对方明显不便的时段称为双方合适。
- 是否将“读取范围内未见本人冲突”与“保证可出席/他人已空闲/会议已创建”区分；身份、时间窗、分页与 source coverage 是否足够。
- 是否只为不可自取且确实阻断的剩余事实提出聚焦问题；是否减少把可读取资料推给 Derek 的 needs_human。
- 角色动作边界、原业务对象与 action_identity 是否保持；技术故障是否原样失败；普通工件能否真实写入并读回。
- 首轮完整回答率、完整闭环率、修订次数、无证据断言率、错误人工升级率、工具成本及新增 prompt 成本。

Lily 实例是主要正向 case 之一，但通用性还要覆盖其他协调者/参与者姓名、不同措辞与时区、已有本人窗口而无需再读、文档/报告资料可自行读取等场景。规则不含特定人名/关键词条件。

### 7.3 负向用例

| 用例 | 预期行为 |
| --- | --- |
| 日历入口声明存在，返回权限拒绝/认证过期/provider 风险拒绝 | 保存具体失败；不伪造忙闲、不改成人工业务选择；不换工具/渠道绕过拒绝 |
| Runtime 未声明所需入口（包括 Claude 无第三方 MCP） | 如实说明本轮缺能力；不启动嵌套 CLI/Agent 探测父会话，不凭安装配置声称可读取 |
| 本人可读，其他参与者可用性未知 | 先交付本人可支持的候选，明确他人待协调；不制造双方空闲，不阻塞已经能完成的部分 |
| 日历返回空但覆盖不足/分页未完/身份不明 | 不把空列表当全天可用；完成现有可行读取或准确报告缺口 |
| 源时间没有时区、named timezone 未提供、跨日/DST 窗口 | 公开已知 offset/来源解释与未知；不由偏移唯一推出地区，不造本地时区或时间冲突 |
| 本人与对方时区不同，本人下午对应对方次日深夜/清晨 | 同一 UTC 时刻换算出各方完整当地日期/时间；结合已知工作时段/明确偏好选择更合适窗口，或说明没有已知重叠。不只因本人空闲声称双方方便，不编造固定办公时间 |
| 对方时区未知、只有公司所在地/历史旅行地点/源消息时间戳 | 按实际可用来源核实；所在地及时间戳只能作线索。仍未知则标待确认，先给本人侧明确时区的暂定候选，不伪造对方时间或把取证直接推给 Derek |
| 已知双方命名时区，但会议跨夏令时切换，或当地时刻不存在/重复 | 以会议具体日期及各地规则换算，正确区分偏移和跨日；不存在/重复的时间须消除歧义后才作为精确候选，不沿用当前固定时差 |
| 对方时区已知但日历不可读，或事件时区与对方明确新位置冲突 | 已知 zone 不证明对方空闲；冲突时按当前来源核实，保留读取失败或未确认状态，不用旧时区覆盖新证据 |
| 近期消息显示原请求已完成、取消或被替代 | 基于同业务对象的新证据形成有依据的 no_action；单纯时间流逝不是取消证据 |
| 原请求需要受控发送、日程响应或审批 | Consumer 只形成完整方案；Audit 只读审核；不调用受控动作；评测的模拟 System 仅检查精确计划绑定 |
| 普通文本工件/报告准备，Codex 与 Claude 分别运行 | 在自身能力范围内完成并读回，不一律升级审核；不能让 Claude 声称已执行 shell，不借未绑定报告工具写目标 |
| 历史 provider_risk_rejected、旧已完成 action 或不同 receipt | 保留拒绝及历史边界；不重放，不靠更换 identity 规避；不借其他对象回执证明成功 |
| 摘要引用历史“已完成”，实际当前受控动作无 receipt | 按完整候选与来源评审；不做关键词执行判定，不把摘要当执行完成 |
| Audit 缺事实但有入口，或自己的读取失败 | 有入口则自行核实；技术故障如实 failed；不耗尽内容预算要求 Consumer 抄工具输出 |
| 未登记动作/工具、snapshot 声明与实际目录不一致 | 目录事实如实反映缺失；未登记操作按现有机制明确失败；说明不创造权限 |

全套回归只在隔离数据、固定读取 fixture 和无外发的执行替身中进行；运行时生成真实普通工件可读回，受控发送/审批/日历写入均不连接生产。静态输入/目录一致性检查、行为对照、生产上线及最终业务效果是不同证据层级。本次只设计这些验证，没有运行新评测或生产动作。

### 7.4 Settings 渲染可见性验收（拟议，尚未执行）

1. 从 Settings → Prompts → Rendered preview 可直接查看 Consumer 和 Audit 的完整服务输入，新增环境段实际出现；仅展示 developer_prompt.md 的模板替换结果不算通过。
2. 在隔离 fixture 中捕获实际提交给运行适配器的输入，与同一角色、配置、任务和冻结时钟下的 Settings 预览逐段比较；除明确标记的脱敏内容外应一致。检查 API 与页面真实呈现，不只比较两个调用同一 helper 的返回值。
3. 切换 Codex/Claude、Consumer/Audit 及不同任务后，目录、普通工作能力、来源入口、动作边界和 runtime 包装随真实上下文变化；Audit 不展示 Consumer 的工作写入能力，Claude 不声称有 Codex shell。
4. 选择已有历史 run 后，实际输入保留当时的时间、配置和任务绑定；当前配置改变不能改写该展示。历史记录不足显示不可用；未绑定任务的当前预览显示未绑定，不能伪造运行。
5. 页面标明预览依据与完整性范围，已有脱敏缺口有可见标记，凭据和无关私密内容不暴露。打开、刷新或切换预览不会更改配置、任务状态、候选、动作记录或产生 provider 调用/消息发送。
6. 日历任务的预览展示已输入的本人、对方及必要协调者时区的来源和适用日期；没有证据写待确认，不用浏览器/机器时区填补。预览只展示已有事实，不为生成页面自动读取私密日历。

Settings 必须完成以上可见性验收，才可声称本项功能交付；只有模型输入已经注入不满足 Derek 的页面要求。

## 8. Lily 实例的证据范围与审核落点

本次从生产只读 DB 核对到内容轮 24747/24752/24754/24756 的 Consumer 工具事件均无 MCP 调用；四版结果依次是追问、no_action、追问和 needs_human。Audit 24749/24753/24755/24757 的反馈在过期抑制、继续协调和具体窗口之间变化，后两版审核已读取日历。后续 Consumer 24758 主动读取日历及原群消息，Audit 24759 approve，Attempt 15042 当前 completed；原 Attempt 15034 的失败仍作为历史保存。原群独立读回来自委派任务提供的既有核实背景，本次没有再次发送或重放，也没有用 DB completed 独自宣称新外部读回。

实例说明模型拥有读取能力仍可能不使用、反馈可以消耗内容预算，以及他人未确认不应阻塞本人侧交付；实例完成是在正式人工反馈之后，**不证明无人工提示时的通用行为已改善**。不得重放 task 386135 作为上线验收。

提交 Derek 审核的具体内容：第 3 节中文规则正文、第 4 节字段来源与未知表达、第 5 节示例呈现、第 6 节最小接入范围（含已确认的 Settings 渲染可见性要求）、第 7 节冻结对照及页面验收设计。允许继续修订草案不等于批准实现；获得实现范围的明确批准前，停在此处。

本次交付仅新增此草案及对应 claim 行；无运行代码、现有 prompt、Skill、配置或 AGENTS 修改，无 commit/push/deploy/restart，无生产队列重排或消息发送。

## 9. 审核时的代码导航

以下链接指向本次核对的生产 checkout 源码，只作阅读定位；版本变化后需重新核实。

- [现有 runtime context](/Users/derek/Services/ceo-agent-service/app/prompt.py:72)
- [Consumer 指令组装](/Users/derek/Services/ceo-agent-service/app/consumer_agent.py:775)；[Audit 指令组装](/Users/derek/Services/ceo-agent-service/app/consumer_agent.py:850)
- [原请求与时间上下文](/Users/derek/Services/ceo-agent-service/app/agent_context.py:145)
- [角色 MCP 注册](/Users/derek/Services/ceo-agent-service/app/agent_cli.py:1132)；[最终 Codex 角色配置](/Users/derek/Services/ceo-agent-service/app/wechat/codex_safety.py:201)
- [Claude 合并输入](/Users/derek/Services/ceo-agent-service/app/claude_runtime_adapter.py:124)；[Claude 角色 MCP 配置](/Users/derek/Services/ceo-agent-service/app/claude_runtime_adapter.py:462)
- [扫描来源 Consumer 绑定解析](/Users/derek/Services/ceo-agent-service/app/worker.py:2792)；[不可变来源字段](/Users/derek/Services/ceo-agent-service/app/agent_cron/commands.py:44)
- [SystemExecutor handler 选择](/Users/derek/Services/ceo-agent-service/app/system_executor.py:35)；[现有动作 handler registry](/Users/derek/Services/ceo-agent-service/app/system_action_handlers.py:531)
- [已注册动作契约](/Users/derek/Services/ceo-agent-service/docs/system-action-contracts.md:1)；[实际系统任务审核范围](/Users/derek/Services/ceo-agent-service/docs/audit-task-scope.md:1)
