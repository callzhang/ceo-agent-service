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
- `sent`：历史兼容名称；新任务以 `done` 表示完成，发送与回读保存在 trace。
- `needs_human`：现有 Skill 没有覆盖的一类规则需要人工确定；技术读取或 provider 失败使用 `failed`。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败；必须保留失败原因和阶段。

## 功能机制开关与任务生产

`Settings -> Skills` 的开关位于任务生产边界。功能与业务 Skill 的多对多关系由
`data/config/skill-features.json` 声明，运行时开关由 `data/config/skill-state.json`
持久化；每次生产检查都会读取最新状态，因此常驻 worker 不需要因开关变更而重启。

关闭一个功能只拒绝该功能对应的**新任务/新作业创建**，并不会取消、删除或改写已经存在的
`pending`、`running` 或重试任务。消费者领取和继续处理已有任务时不再重新判断该开关，保证
切换不会中断正在进行的生命周期。缺少或校验失败的关联 Skill 会让该功能标记为配置不完整，
并阻止它继续创建新任务；其他功能不受影响。

## Runtime-managed Skill 修订与启动配置

Settings 不再直接编辑项目目录或 `~/.agents/skills`。每次保存会在 SQLite 中创建一条不可变的
managed Skill revision，保存原始 `SKILL.md`、精确 SHA-256、父修订和来源。项目内七个
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

每个执行 Agent 和审核 Agent 的结构化结果都带有通用的 `risk`（`low`、`medium`、
`high`）和 `confidence`（0 到 1）字段，不区分任务领域。`needs_human` 只有在风险为
`high` 且置信度严格低于 `0.5` 时才允许；低置信度的技术或依赖失败仍然是 `failed`，
规则覆盖但需要修改的结果进入 `needs_feedback`。这样人工入口表示不可安全自行决策的
高后果规则缺口，而不是模型遇到不确定性就停止。

## 审核反馈闭环

```text
执行 Agent 生成 run R0
  -> 审核 Agent 审核 R0
      -> 通过：审核 Agent 执行/发布 R0，回读后进入 done/sent
      -> 需要修改：写入反馈 F0，R0 保留为历史 run
          -> 执行 Agent 收到 F0，生成修正版 R1
              -> 审核 Agent 审核 R1
                  -> 通过：发布 R1
                  -> 再需修改：进入下一反馈周期
                  -> 超过内容反馈上限：needs_human
```

反馈必须包含规则、观察结果和修改要求。审核 Agent 不能直接改写执行 Agent 的业务正文；服务只保存 run、revision、反馈、session 和 provider 结果标识之间的关系。同一任务最多允许三个内容反馈周期；基础设施失败不消耗内容反馈周期。

对于源单聊的澄清动作，Consumer 必须在 action target 中提供已经通过实时读取确认的参与者 `open_dingtalk_id`。若该参与者字段以 `verified_participant_open_dingtalk_id` 表示，Audit 将其作为同一稳定接收人身份执行单聊发送；不会把 `conversation_id` 当作群聊目标。

当 Audit 因临时授权映射或运行配置失败后被重新调度时，恢复会创建下一次 Audit turn；已失败的 turn 保持历史记录，不能被重复领取。

每个队列任务的 `Original trigger` 是该任务唯一的权威输入，由
`trigger_message_id` 标识。近期会话消息、材料和实时读取结果只能补充事实，不能把
Consumer 的任务改成另一个消息、日程或审批事项。Audit 返回 `feedback_provided` 后，服务
必须把规则、观察结果和修改要求传给下一版 Consumer proposal，再创建对应的 Audit run；
Audit 只反馈修改要求，不直接替换 Consumer 的业务正文。

## Task、Agent Run 与 Reply Attempt

运行时使用三层对象：`reply_task` 是可领取和重试的队列任务，`agent_run` 是一次
真实的 Consumer/Audit 执行，`reply_attempt` 是同一 trigger/channel 的稳定业务
当前投影。一个 task 可以有多个 agent run；重跑不新建业务 attempt，而是在原
`reply_attempt` 上更新 current projection，并把新的 agent run 追加到历史。

```text
reply_task 1 ──< agent_runs
trigger/channel 1 ── 1 current reply_attempt
```

`reply_attempt` 的 `agent_run_id` 指向当前投影对应的最新或终态 run；完整执行历史
通过 run 的 task、generation 和关联事件查询。Attempt 页面可以切换多个 Consumer
或 Audit run，但不能编辑或覆盖旧 run。原始失败、session、runtime attempt、tool
event 和 provider 结果仍然作为 append-only 事实保留。

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

> **实现与部署状态：** Email audited-v2 lifecycle 已在本分支实现并通过开发/loopback 验证；实际 launchd 仍运行 main checkout，因此尚未部署，也未在生产启用。

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

Consumer A 是只读判断角色；它读取当前邮件、thread、安全 prior receipt 和 ActionPlan，每个
revision 只提出一个与当前 task/ActionPlan 绑定的新 operation，不执行浏览器 effect。
Audit Agent B 是唯一拥有 task-bound unsubscribe 写能力的角色；它校验 task、plan、账户、
邮件、thread 身份，已接受 operation prefix、previous effect digest、exact-origin network
policy 和当前 readback。多步骤页面每轮在已接受 prefix 后只追加一个 operation，Audit 只执行
新 operation，不重放已接受的 operation prefix。仍需继续页面流程时持久化 `awaiting_audit`
continuation；`awaiting_audit` 是 effect/claim 的领域状态，
不是顶层 task 状态。历史 run、session、step、receipt 和失败事实保持不可变。

冷启动期间，实时主路径是 Agent，且只处理服务观察到的未读 Inbox/未绑定来源邮件；Agent 不处理
已读邮件，也不因分类而改成已读。冻结训练 snapshot 直接采用 provider 文件夹和 important 信号，
训练与 shadow 评估均为离线、阶段性作业，不在收信路径实时训练或并行推理。某一类别满足
precision/support/group 门槛后，只获得显式历史批次的资格；全量线上模型仍需连续两个兼容版本对
全部类别和 important 都达标且没有未解决的系统性错误。整个模型晋升后，主路径严格按
`model -> Agent fallback` 顺序执行：模型接受时不调用 Agent；embedding 超时、失败或拒绝时才调用
一次 Agent。两个连续候选必须分别绑定不同且时间递增的冻结 snapshot；snapshot digest 必须不同，
folder/important 累计标签水位以及至少一项独立评估样本或组证据必须前进。同一 snapshot 的重复训练
不能满足晋升。

业务类别移动完成后在变更后的 locator 上执行 flag/read 动作并回读；用户在 provider 中再次移动
邮件时，下一份 snapshot 立即以该文件夹作为训练标签。历史任务按小批次、显式触发并保存游标，
不会自动扫描全部邮箱。`junk` 先由代码发现标准退订候选；只有 unsubscribe 进入 Consumer/Audit
网页流程，最终再移动到系统 Trash。连接邮箱 OTP 仅允许站点、收件人、挑战上下文和时间窗全部
匹配的临时读取；普通 CAPTCHA 在隔离 profile 中有限尝试，不能完成的密码/MFA/CAPTCHA 保存不含
秘密的有界 continuation 并交给用户，恢复时不重放已经审计的 operation prefix。

只读 Console API 提供当前 provider-derived category 或 unavailable、文件夹绑定、描述/训练 snapshot
版本、样本与组数量、逐类别历史资格、连续晋升证据、active mode/model、时延与 fallback、动作回读
和退订 continuation。API 不暴露完整退订 URL、OTP、secret、raw embedding、完整正文、附件字节或
浏览器 session 私密数据。外部配置的 embedding model/revision 不原样投影，也不返回可被离线枚举的
摘要；API 仅返回固定受控占位符。canonical model/snapshot ID 则按生产构造格式校验。当前 category
来自扫描进程持续写入的 latest-provider-observation 投影，
不读取冻结 snapshot，API 也不会额外连接邮箱；冻结数据只以 training snapshot 字段展示。SMTP、
自动回复和所有 reply/send 路径继续不可达。

### Email folder classifier live verification

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

所有服务所有的 DingTalk、WeChat 人员可见文本，在 provider 调用前都必须由
`ServiceMessageSender` 生成并持久化 `PreparedOutboundMessage`。持久化键是
`(channel, delivery_key)`；首次准备确定最终正文、feedback token 和后缀版本，之后的重试、恢复、
发送回读与 WeChat 撤回只能复用该记录，不得根据当前配置重新生成。provider adapter 只接收已持久化
的最终正文，业务模块直接调用原始发送方法会被架构测试拒绝。

这是一条机械传输边界，不是新的业务审核或授权规则。Email 仍是独立的非发送通道：SMTP、自动回复
和 `mailto` 均保持禁用，不受此 DingTalk / WeChat 后缀机制影响。

## 统一禁止事项

- 所有任务都不得使用 `discard` 动作。
- 所有任务都不得写入 `discarded` 状态。
- 不得用“丢弃”代替审核反馈、修正原 run、重新排队、人工升级或失败记录。
- 业务 `reply_attempt` 的 current projection 可以由重跑更新；同一 trigger/channel 复用原 attempt ID，
  不创建新的业务 attempt。原始失败作为 append-only state event 保留。其下的 `agent_runs`、proposal
  版本、revision lineage、session、runtime attempt 和 tool event 不得覆盖；attempt 页面可切换查看
  这些底层 run。provider 返回的 `operation`、`target`、稳定 result identifier 作为最小去重事实保存。
- Audit 返回 `executed` 后任务即可进入 `done`；外部结果的读取与判断由 Agent 按业务 Skill 完成。

如果任务确定无需执行，应进入 `done`，并在 trace 写入 `agent_output/no_action`；如果结果需要修改，写入 `audit_feedback` 并保持 `running`；如果处理失败，应进入 `failed`。

## 进程、租约和恢复

- 生产入口是 launchd 管理的 `com.ceo-agent-service.main`，由 supervisor 管理 worker 和 audit-web。
- 同一 `conversation_id` 同时只能有一个执行 Agent 持有 Codex session lock。
- 每个执行/审核 run 都有独立 lease、revision 和 transcript 范围。
- 已被恢复器终态化或被新 generation 替代的 run 视为 lease 丢失；旧执行线程不会把该状态记录成新的任务失败。
- 回复队列的 `processing` 有双重恢复边界：十分钟没有当前 generation 的运行心跳会被恢复；即使运行持续续租，单次队列处理超过一小时也会被释放，进入既有重试或终态路径，避免反馈循环无限占用队列。
- 重启时，未完成的 Agent turn 统一按 `failed` 重试；服务不创建 unknown 或独立状态核对队列，也不依据工具事件决定是否重放。下一次 Agent turn 按业务 Skill 读取当前外部状态，再自行判断后续动作。
- 会议总结进入投递后复用同一个持久化投递键：钉钉发送使用由该键确定的 UUID，provider 成功返回会立刻写入同一键的回执。服务重启后，恢复的 worker 先复用回执；若进程恰在 provider 接收后中断，使用相同 UUID 继续投递，provider 的重复 UUID 回应视为原投递已送达，不能产生第二条群消息。
- 外部动作的 operation、target 和 provider result identifier（若 provider 返回）会保留用于去重；缺少标识属于 provider/Agent 失败，不转换为额外状态。
- WeChat reader 由独立 launchd job 自动保持运行；worker 连续三次 IPC 超时后主动 kickstart 该 job，处理“进程仍在但 IPC 已卡住”的情况。worker 只恢复 reader 进程，不启动 WeChat 主应用，也不重放消息。
- OKR 无头来源启动使用进程锁；锁被遗留进程占用超过有限等待时间时，该次读取明确失败，不会无限排队或阻塞后续维护循环。该来源命令在独立进程组中运行，超时会终止脚本及其临时 headless Chrome 子进程，不能遗留后台浏览器。
- 周 OKR 分析任务每次获得新的租约时使用新的 runtime 执行代次。单次执行中的结果格式修正保持有界；已终态的旧代次不得阻断同一分析任务在后续租约中的重新执行。
- 已失败且经本地读回确认没有执行、送达或持久化回执的 reply task，若下一步只能由人工授权的外部动作完成，服务会保留原失败记录、写入新的 `needs_human` Attempt，并将队列 task 收口为 `done`；不会为此重放外部动作。
- 所有需要 `BEGIN IMMEDIATE` 的 Store 写路径统一经过同一个有界重试事务。短暂的 SQLite 写锁在 Store 内等待并重试；只有超过上限的持续锁才上升为服务错误。队列 claim、反馈批处理和恢复路径不得绕过这一规则。

### 应用层边界

应用层不审核 Agent 使用的命令、MCP 工具、Skill、读写模式或工具名称，也不维护
`side_effect_state`、`unknown`、`reconciled` 等业务状态。应用层只校验最终 typed result 的形状，
推进 `done`、`failed`、`needs_feedback` 和 `needs_human`，并保存去重所需的最小外部事实：
`operation`、`target`、provider 稳定结果标识。纯读取不需要 receipt；写入中断时由下一次 Agent turn
按业务 Skill 读取目标状态，服务不启动专门的只读核对回合，也不因“未知工具”阻断执行。

历史数据库中已经存在的 `unknown`、`reconciled` 或 `side_effect_state` 值仅作为不可变历史事实展示，
不得由新代码写入，也不参与当前状态迁移。旧 spec/plan 中描述这些状态机的内容属于历史设计，
不应作为实现依据。

任务 Agent 的 `memory_recall_used` 是 Agent 给出的上下文记录，不是服务的工具调用验收条件。服务不得要求
`memory_recall` 工具事件、session receipt 或任何特定工具名称作为推进结构化任务决策的前置条件。

## 任务类型

- `okr_review`：指定人员和周期的逐 KR 评审。执行 Agent 先通过固定只读入口
  `app.cli read-dingteam-okr --user-id <owner-id> --period-label <period>` 读取实时
  `processed.objectives`/`processed.okrRows`，再生成评审；审核 Agent 审阅并反馈修改，
  修正版通过后才发送。底层读取错误（认证失效、浏览器/profile 锁、周期解析失败等）
  必须原样保留，不能被 `consumer_retry_exhausted` 覆盖。服务入口先复用有效 token；
  缓存过期时只启动 headless 浏览器刷新，不打开可见窗口，也不把“禁止可见浏览器”
  误解为“禁止刷新”。
- `weekly_okr`：定时生成管理者 OKR 进度周报。分析、报告发布、群摘要发送和外部回读全部完成后，才推进周报成功日期。
- 普通消息、审批、会议、邮件、任务跟踪和 WeChat 任务都遵循同一生命周期与反馈规则，只在领域输入、工具权限和外部回读方式上不同。

## 文档索引

- 总体 A/B 架构：`docs/architecture.md`
- 路由失败和恢复：`docs/runtime-route-recovery.md`
- Consumer/Audit 反馈设计：`docs/superpowers/specs/2026-08-06-consumer-audit-agent-design.md`
- OKR 领域输入和输出：`docs/superpowers/specs/2026-06-08-okr-review-runner-design.md`
- 当前实现：`app/agent_orchestrator.py`、`app/consumer_agent.py`、`app/audit_agent.py`、`app/okr_review.py`、`app/weekly_okr_report.py`、`app/store.py`
- 系统错误码目录：[`docs/error-catalog.md`](error-catalog.md)
- 会议总结进入投递后复用同一个持久化投递键：钉钉发送使用由该键确定的 UUID，provider 成功返回会立刻写入同一键的回执。服务重启后，恢复的 worker 先复用回执；若进程恰在 provider 接收后中断，使用相同 UUID 继续投递，provider 的重复 UUID 回应视为原投递已送达，不能产生第二条群消息。
