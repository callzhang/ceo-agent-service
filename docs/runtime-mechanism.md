# Current Runtime Mechanism

本文档是 CEO Agent Service 当前运行机制的唯一总览入口。`docs/superpowers/` 下的 spec/plan 文件仅用于追溯，不代表当前规则。

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

反馈必须包含规则、观察结果和修改要求。审核 Agent 不能直接改写执行 Agent 的业务正文；服务只保存 run、revision、反馈、session 和 provider 结果标识之间的关系。同一任务最多允许两个内容反馈周期；基础设施失败不消耗内容反馈周期。

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
- 重启时，未完成的 Agent turn 统一按 `failed` 重试；服务不创建 unknown 或独立状态核对队列，也不依据工具事件决定是否重放。下一次 Agent turn 按业务 Skill 读取当前外部状态，再自行判断后续动作。
- 外部动作的 operation、target 和 provider result identifier（若 provider 返回）会保留用于去重；缺少标识属于 provider/Agent 失败，不转换为额外状态。

### 应用层边界

应用层不审核 Agent 使用的命令、MCP 工具、Skill、读写模式或工具名称，也不维护
`side_effect_state`、`unknown`、`reconciled` 等业务状态。应用层只校验最终 typed result 的形状，
推进 `done`、`failed`、`needs_feedback` 和 `needs_human`，并保存去重所需的最小外部事实：
`operation`、`target`、provider 稳定结果标识。纯读取不需要 receipt；写入中断时由下一次 Agent turn
按业务 Skill 读取目标状态，服务不启动专门的只读核对回合，也不因“未知工具”阻断执行。

历史数据库中已经存在的 `unknown`、`reconciled` 或 `side_effect_state` 值仅作为不可变历史事实展示，
不得由新代码写入，也不参与当前状态迁移。旧 spec/plan 中描述这些状态机的内容属于历史设计，
不应作为实现依据。

## 任务类型

- `okr_review`：指定人员和周期的逐 KR 评审。执行 Agent 先通过固定只读入口
  `app.cli read-dingteam-okr --user-id <owner-id> --period-label <period>` 读取实时
  `processed.objectives`/`processed.okrRows`，再生成评审；审核 Agent 审阅并反馈修改，
  修正版通过后才发送。底层读取错误（认证失效、浏览器/profile 锁、周期解析失败等）
  必须原样保留，不能被 `consumer_retry_exhausted` 覆盖。
- `weekly_okr`：定时生成管理者 OKR 进度周报。分析、报告发布、群摘要发送和外部回读全部完成后，才推进周报成功日期。
- 普通消息、审批、会议、邮件、任务跟踪和 WeChat 任务都遵循同一生命周期与反馈规则，只在领域输入、工具权限和外部回读方式上不同。

## 文档索引

- 总体 A/B 架构：`docs/architecture.md`
- 路由失败和恢复：`docs/runtime-route-recovery.md`
- Consumer/Audit 反馈设计：`docs/superpowers/specs/2026-08-06-consumer-audit-agent-design.md`
- OKR 领域输入和输出：`docs/superpowers/specs/2026-06-08-okr-review-runner-design.md`
- 当前实现：`app/agent_orchestrator.py`、`app/consumer_agent.py`、`app/audit_agent.py`、`app/okr_review.py`、`app/weekly_okr_report.py`、`app/store.py`
