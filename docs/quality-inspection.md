# 质量巡检与收敛

本文件定义 CEO Agent Service 的质量巡检边界。巡检回答的不是“最近有没有一条
`failed`”，而是“所有已登记的持久化工作是否被检查，是否有工作停止推进，是否有
外部动作需要只读核对”。它只观察和产出证据；修复、重试和外部发送仍由各自队列
的恢复流程执行。

## 当前运行契约

入口：

```sh
.venv/bin/python -m app.cli quality-check --db "$CEO_WORKER_DB"
```

命令调用 `app.quality_gate.scan_hourly_quality()`，把结果原子写入
`data/hourly-quality-gate.json`（可用 `CEO_HOURLY_QUALITY_GATE_PATH` 或
`--state-file` 覆盖）。退出码 `0` 表示没有 violation；退出码 `2` 表示至少有一项
violation 或必需数据源不可检查。

结果是机器可读 JSON，固定包含：

| 字段 | 含义 |
| --- | --- |
| `checked_at` | 本次检查的 UTC 时间 |
| `mode` | 当前固定为 `fail_closed_queue_coverage` |
| `ok` | 没有缺失数据源且没有 violation |
| `checked_sources` | 实际检查的 SQLite 表和已启用的 live channel gate |
| `missing_sources` | 本应检查但数据库中不存在的表 |
| `violations` | 已停止推进、未解决或无法确认的工作；使命令失败 |
| `attention` | 新鲜的排队或处理中工作；不使命令失败，但必须在小时报告中展示 |

不能把空结果解释为健康。如果任何 `REQUIRED_SOURCES` 表缺失，巡检直接失败并列出
`source_missing`；不会把该队列默认为零。

## 当前检查覆盖

| 事实源 | violation | attention |
| --- | --- | --- |
| `reply_tasks` | `failed`、超过 30 分钟的 `processing`、已到期且超过 15 分钟未领取的 `pending` | 新鲜 `pending` / `processing` |
| `reply_attempts` | 最新 trigger 的 `failed` / `blocked` 没有活动恢复任务；24 小时内最新结果仍为 `dry_run` | 有 `pending` / `processing` recovery task；每个最新 `needs_human`，必须逐条读取具体待决动作 |
| `agent_runs` | `unknown` 副作用、超过 30 分钟的 `pending` / `running` | 新鲜 `pending` / `running` |
| `work_summary_inputs` | `failed`、超过 21 分钟的 `processing` | `pending` / `processing` |
| `follow_up_drafts` | `failed`、计划时间已过且超过 15 分钟仍为 `draft` / `approved` | 未来计划的 follow-up |
| `meeting_alignment_jobs` | `failed`，或 `pending` / `processing` / `ready_to_send` / `retry` 超过 21 分钟未更新 | 仍在等待、排队或发送中的会议任务 |
| `okr_review_requests` | `failed`、超过 21 分钟的 `processing` | `pending` / `processing` |
| 外部投递队列 | `work_todo_dingtalk_links`、`wechat_deliveries`、`memory_write_events` 的失败或未知发送 | 这些队列的活动状态 |
| `feedback_events` | 未记录 `resolved_at` 的反馈 | 无 |
| `daily_scan_state` / `wechat_read_state` | scanner 仍有 `last_error`；微信 reader 不可用且存在待处理微信回复 | 无待处理微信工作时的 reader 未就绪 |
| `errors` | 最近 4 小时新建、未解决且没有活动恢复路径的服务错误 | Codex 容量暂停期间的一条共享容量事件 |

当前时间窗口是有意区分的：`errors` 为最近 4 小时，最新 `dry_run` 为最近 24 小时，
其余队列按当前所有未终态记录扫描。超时值来自 `app/quality_gate.py`，不要在 heartbeat
或审计页面重新硬编码另一套值。

没有 `trigger_message_id` 的服务级错误无法由一条业务回复证明恢复。维护循环会在同类
错误连续四小时未再出现时，将其结案为“健康观察期内未复发”。记录不会删除；带触发消息
的错误仍优先接受同一消息的后续终态作为恢复证明。若四小时观察期内同一
trigger 没有新错误，且不存在 `pending` / `processing` / `failed` 的任务、未终态
attempt 或未知副作用，维护循环会将其作为“无活动工作流”的历史服务事故结案。
这不表示消息已投递或外部动作已成功，审计记录会保留原始详情与该区别。

服务错误保留原始时间和详情。只有完成了可读回验证的恢复动作，才可以写入明确的
解决说明和时间；质量巡检随后不再将该错误计为未解决。没有关联业务 trigger 的通道错误
不能仅因新任务成功而自动关闭，必须经过同类组件的完整健康观察期。

## Trigger 收敛规则

回复类记录必须按业务 trigger 而不是按历史 attempt 行计数。当前键为：

`channel + conversation_id + trigger_message_id`

巡检只看这个键上 `updated_at` 最新的 attempt：

- 较早的 `blocked` / `dry_run` 后来已有 `sent` 或 `skipped`，旧行不再报警。
- 最新 `blocked` 如果同一 trigger 的任务已 `done`，且不存在未知副作用或活动恢复，维护循环会将它结案为 `skipped`，并保留审计说明。该状态表示外部动作没有被自动重放，不表示动作成功。
- 最新 `failed` / `blocked` 但同一 trigger 仍有 `pending` 或 `processing` 的
  `reply_task`，记为 `attention`，表示恢复正在进行。
- 最新 `failed` / `blocked` 没有活动恢复任务，才是 violation。
- `unknown` 外部写入不是可重试失败；只能进入只读 reconciliation，确认未发生后才
  创建新的 generation。

审批类记录按 `oa_process_instance_id` 收敛。后续 `agent_run` 持有同一审批实例的已验证
终态动作回执时，它会解除旧 `oa_approval` 的 `failed` / `blocked` 告警；单纯评论回执不会
解除仍在运行审批的告警。巡检不会因旧行保留而把已完成的审批重新标红。

因此 `blocked` 不是“等管理决策”的同义词。它描述当前依赖、材料、权限或安全条件
不满足；是否需要人做选择由 `needs_human` 的业务结果和对应 UI/通知承载。每个
`needs_human` 必须给出 2 至 4 个互斥、可执行的选项和各自后果；已发送澄清、等待
外部材料或等待依赖恢复不属于 Derek 的待决项。

## 每小时处理顺序

每小时修复从审计页 `/workers` 的 **Attention** 开始，而不是从图表总数或历史
attempt 开始。Attention 中同一 reply trigger 只保留一条 `Reply task` 主记录；没有
关联任务的 `needs_human`、`blocked` 或 `failed` attempt 才单独显示。

处理每一条主记录时依次核对：

1. `sent_replies` 是否已有同一 trigger 的送达凭据。
2. `agent_runs` 的副作用是否 `completed`、`failed` 或 `unknown`，以及有无执行回执。
3. 后续同一 trigger / 审批实例是否已有成功、跳过或明确终态。
4. 只有确认没有送达且没有未知副作用时，才新建 generation 重试。

因此 Attention 的红色记录是小时修复的输入清单，不是展示性计数。任何未完成的
`Reply task`、`work item`、`meeting` 或外部投递失败都必须在报告中逐类说明其恢复、
对账或不可执行原因。

每小时还必须对每个最新 `needs_human` 读取对应 attempt、Consumer proposal 与 Audit
结果。报告必须写明具体待执行动作、是否会发送消息/接受日程/创建待办/执行审批，以及
为什么 Agent 不能安全自动完成；不得只写“有 N 项问题”。如果 `needs_human` 的原因是
命令契约、Skill receipt、材料读取、History 缺行、通知无法定位或其他服务机制问题，先
按服务错误修复并补回归测试，不能把它误报为 Derek 的业务决策。只有动作本身会代表
Derek 作出新的承诺、选择或外部写入且当前授权不足时，才保留为真实待决项。

每次巡检还要核对 `/workers` Attention、History 列表、attempt 详情和浏览器通知是否对
同一最新 trigger 给出一致的状态、具体理由与下一步。出现 failed / pending reconciliation
但 History 没有该当前记录，或详情没有脱敏原因、进度、外部副作用说明时，按可见性故障
处理并修复；不得只以图表的红色计数结案。

遇到低后果的日常处理，不得因为存在多个合理默认值就升级为 `needs_human`。Consumer
必须先读取适用的业务与操作 Skill；记忆工具可用时还必须进行聚焦 `memory_recall`，并把
记忆只作为稳定上下文、把实时读取作为外部状态依据。若动作仅影响 Derek 自己的可用性、
准备、确认或跟进，或仅让已识别的内部参与者落实一个已确认事项或已跟踪承诺的有限动作，
且不改变范围、时间、owner 或业务含义，并且不存在冲突、新的外部承诺、敏感目标、预算、
审批或不可逆结果，应由 Agent 提出并经 Audit 执行、回读验证。只有存在证据冲突、实质外部影响、不可逆结果
或目标无法可靠识别时，才保留为 Derek 的待决项。

Attention 只显示实际失败、排队或处理中的工作。未来计划或正常草稿状态的 follow-up 不属于
故障，保留在其业务列表而不占用 Attention。对于外部写入失败，巡检显示渠道原始错误码和
脱敏摘要；若发送回执未知，先进入只读对账，不能因为错误文本缺失就把它误判为已发送或直接
重发。

## 外部依赖边界

`quality-check` 默认检查 DingTalk，以及当前有 active task 或最近 72 小时 failed/blocked
attempt 的可选通道。没有任何待处理或待恢复工作引用的 Lark 未配置状态仅保留在
`channel-doctor`，不会把小时质量门禁标红；一旦 Lark 工作进入队列，Lark gate 立即成为
`channel:<name>/not_ready` violation。离线诊断可以显式使用 `--no-verify-channels`，但该
结果不能作为线上健康证明。

微信目前由本地 reader/delivery 状态覆盖，并没有在此命令中执行独立的 token 刷新或
发送 smoke test。Codex 也不在本命令中执行登录或写入 smoke test；其运行可用性由
`agent_runs`、`errors` 和独立的 `doctor-mcp` 诊断反映。DWS 的 gate 是可用性探针，
不是业务写入重试许可。文档和告警不得把这些局部检查表述成所有外部能力均已验证。

## 巡检、reconciliation 与修复的关系

自动恢复的陈旧任务不是用户可处理的失败。服务会保留任务的恢复状态，并在下一轮
按既有 Agent turn 和外部回执继续处理；只有恢复最终失败、外部结果无法对账，或确有
管理选择时才产生错误记录和用户通知。对账 Agent 必须先读取对应能力说明并完成最小
只读回查，不能把“尚未读取命令语法”升级为人工决策。

待领取任务中，只要当前 generation 存在 `unknown` 的 Audit run，消费者会优先领取它
进入只读 reconciliation，再领取普通待办。这个排序不增加并发，也不允许重放外部写入；
它只保证已经发生但尚未落回执的动作不会被普通重试长期饿死。

如果这类 task 仍是 `processing`，但 unknown run 已到核对时间且没有有效租约，消费者会
先把同一 generation 重新排为 `pending`，无需等待普通任务的 30 分钟 stale 阈值。未来
退避、暂停或仍有有效租约的 run 不会被提前领取。

同一数据库上的短暂写锁竞争属于可重试的本地基础设施故障。Agent run 的租约续期在
开始写事务前会有限退避重试；尚未进入事务的失败不代表外部副作用发生。重试耗尽后仍按
可重试失败进入原有恢复队列，不能把它升级为业务 `needs_human` 或重放未知写入。

每次 `unknown` 恢复都使用新的、隔离的 Codex 会话，而不是续接原执行会话。原会话 ID
和事件仍保留在 `agent_runs` 作为不可变审计证据；恢复会话只接收持久化的任务、proposal、
operation 和回执上下文，并被限制为只读。这样原会话即使已经中断或终止，也不会让对账
无限重试或诱发写入重放。只有只读结果明确为 `absent`，后续受限的执行阶段才可新建会话
并执行已授权的单个动作。

Codex 的流式输出与本地 session JSONL 是同一 turn 的两份审计载体。若流式输出缺少已完成
的 reviewed MCP 调用，worker 会在短暂等待 session 落盘后读取对应的
`mcp_tool_call_end` 回执，并按同一 allowlist、命令摘要、目标和返回回执规则重新验证。
只有验证通过的回执才会补入 `agent_runs` 和发送台账；读取失败、未审核工具或回执不匹配
仍保持 `unknown`，不会以 session 文件的存在假定外部动作成功。

恢复失败状态必须保留可操作的分类，例如结果缺失、结果格式不合法、provider 不可用或
核验结果不符合约束；不得把它们重新覆盖成泛化的 `audit_recovery_failed`。页面和通知
据此展示具体下一步，而非只显示“失败”。

```text
Scheduler / hourly heartbeat
            |
            v
       quality-check
            |
            +--> violation: 创建可追踪的修复工作或升级
            +--> attention: 继续观察现有 recovery
            |
            v
    queue-specific reconciliation
            |
            +--> 已确认未执行 -> 新 generation / 明确重试
            +--> 已确认已执行 -> 写入终态或回执
            +--> 结果未知     -> 保持 unknown，只读继续核对
```

质量巡检不得直接重放发送、审批或其他没有幂等键的写操作。任何自动修复都必须先判断
是 bug 还是 feature request；bug 在独立分支完成测试和独立 review 后合并，feature
进入需求审批记录。巡检输出是这两个流程的输入证据，而不是绕过它们的授权。

## 测试不变量

`tests/test_quality_gate.py` 至少覆盖以下回归场景：

| 场景 | 期望 |
| --- | --- |
| 必需表缺失 | `ok=false` 且有 `source_missing` |
| 失败队列、陈旧 processing 或逾期待办 | `ok=false` |
| 历史 blocked 后有新的 terminal attempt | 不因旧 attempt 报警 |
| 未来 scheduled follow-up | 只进入 `attention` |
| channel gate 未就绪 | `ok=false` |
| 状态文件写入 | 每次输出完整 JSON，避免半写入 |

任何新增持久化队列都必须同时更新 `REQUIRED_SOURCES`、本文件的覆盖表和对应测试；
否则质量门必须以缺失覆盖失败，而不是静默遗漏。

## Skill runtime 回归语料

`evals/skill_runtime/cases.jsonl` 保存脱敏、泛化的 Consumer/Audit 业务回归场景。
`evals/skill_runtime/fixtures.jsonl` 独立保存每个场景已记录的 Consumer/Audit 协议：
实际 Skill 读取、只读证据事件、Consumer 嵌套结果和 Audit dry-run 结果。fixture 用
规范化 trigger/context 摘要以及当时安装的 Skill 路径和 SHA-256 摘要绑定；场景内容或
Skill 内容变化后，旧 fixture 必须失效。

当前语料包含 19 个对比场景。其中人事沟通同时覆盖“无关接收人不披露”和“已验证 HR
职责后最小化发送”；工作跟踪覆盖创建 TODO、绑定 follow-up、到期前读取当前状态、完成后
关闭并抑制提醒，以及参与者不等于 owner；OA 覆盖事实缺失时询问申请人、材料齐全时同意、
可补正时退回、不可补正的政策冲突时拒绝，并要求每种终态都通知实际申请人；恢复场景覆盖
外部动作无持久回执且读回不明确时只读核验、保持 unknown、禁止重放。测试中的矩阵断言会
阻止这些正向、反向或恢复路径被单独删除。

Consumer 返回 `no_action`、`needs_human` 或 `failed` 时流程在 Consumer 终止，fixture 的
Audit result 为 `null`、Audit events 为空，语料使用 `not_applicable`，不得为了统一形状再
启动 Audit。只有 Consumer 返回 proposal 时才进入 Audit dry-run；Audit 上下文使用生产 Skill receipt 解析与
`AuditTurnContext` 格式，携带 Consumer 实际读取的精确 path/SHA，并要求 Audit 逐项重读。

默认运行是确定性的已记录协议回放。它严格校验两份 JSONL 的结构、唯一 ID 和脱敏约束，
用生产 Consumer/Audit wire parser 重新解析嵌套结果，用实际 action/effect metadata
检查 proposal，再逐项对照语料中的 Skill、结果和机器可读断言。运行时不会根据
`case_id` 路由业务策略，也不会从 `expected_*` 或断言字段合成 observation。

```sh
python evals/skill_runtime/run.py
```

命令同时输出逐场景的人类可读状态和完整 JSON。它不调用模型、网络或外部系统，适合
单元测试和提交前回归；它证明的是当前语料、已记录协议、安装 Skill 和运行时契约仍然
一致，不证明模型此刻会重新作出相同判断。

需要补充当前本机模型的语义证据时，可以显式选择 live 模式：

```sh
python evals/skill_runtime/run.py --live
```

live 模式对每个场景分别启动真实本地 `codex exec` Consumer 和 Audit dry-run，向两者暴露
完整的内置业务 Skill 清单，并记录实际 Skill 读取、`execute_reviewed_read` 证据事件、
Consumer 严格结果和 Audit 严格结果。runner 要求 Consumer 和 Audit 都读取预期 Skill、
不读取禁止 Skill，且 Audit 的实际读取必须与 Consumer 的已验证 Skill receipts 完全一致；
runner 对每条机器可读断言使用本次结果或事件求值。每个 proposal 场景都必须得到
该行 `acceptable_audit_outcomes` 明确允许的 Audit dry-run 结论；所有非 proposal 结果均不
调用 Audit，并报告 `audit_outcome=not_applicable`。

两个进程都忽略用户配置与规则，使用 ephemeral、read-only sandbox，关闭 plugins、apps、
内置工具和 web，只暴露只读 fixture MCP 的 `read_skill` 与
`execute_reviewed_read`。Audit 不能执行或重放外部写入。live 是可选的新鲜模型语义证据，
不进入普通单元测试，也不替代真实业务读回、队列对账或 `quality-check`。

这套语料不实现或证明增量 cursor、72 小时滚动窗口、错误聚合、外部投递状态或服务健康。
这些能力仍属于下方目标演进，只有对应持久化实现和边界测试完成后才能在巡检报告中声称
已覆盖。

## 目标演进

以下是目标架构，尚未全部由当前命令实现，不能在运行报告中写成已完成：

| 能力 | 目标行为 | 需要的实现与证明 |
| --- | --- | --- |
| 增量窗口 | 用持久化 cursor 检查上次成功巡检到现在新增或更新的 error、attempt 与外部依赖失败 | 原子保存 cursor 和 report ID；中断后不跳过事件；加入 cursor 边界测试 |
| 72 小时滚动窗口 | 发现重试风暴、反复失败和被反复恢复的异常 | 聚合事件历史而非只看当前状态；定义速率阈值和时区测试 |
| 全部外部 live probe | Codex、DWS、微信、钉钉分别有不执行真实业务写入的认证/可达性证据 | 每项 probe 有 timeout、失败分类、脱敏输出和模拟测试 |
| 修复编排 | violation 形成一个可去重、可审计的 incident，修复后回写终态 | incident key、幂等 repair run、review/merge/restart 证据和恢复测试 |
| Dashboard 与 heartbeat | 同一 JSON 同时供审计页、通知和每小时报告消费 | 不直接读取零散表；测试同一 violation 在不同视图一致显示 |

在这些能力上线前，每小时报告必须同时列出当前状态扫描结果和未实现的覆盖缺口；不能用
旧的 `hourly-quality-gate.json` 或单个 “10/10” 标识替代实时检查。
