# Current Runtime Mechanism

本文档是 CEO Agent Service 当前运行机制的唯一总览入口。`docs/superpowers/` 下的 spec/plan 文件仅用于追溯，不代表当前规则。

## 运行角色

每个需要 Agent 处理的任务都经过两个职责不同的角色：

1. 执行 Agent 读取上下文和证据，形成候选结果或任务结果。
2. 审核 Agent 独立检查执行结果，决定通过、反馈修改或升级人工处理。

执行 Agent 不确认自己的结果已经完成。审核 Agent 也不替执行 Agent 重写业务内容；如果业务含义、对象、证据或输出需要变化，审核 Agent 必须给出具体反馈，由执行 Agent 生成修正版。

## 标准生命周期

```text
pending
  -> processing
      -> needs_feedback
          -> revision_pending
              -> processing
                  -> done / sent
      -> needs_human
      -> failed
```

- `pending`：已持久化，等待执行。
- `processing`：执行 Agent 或审核 Agent 正在持有租约。
- `needs_feedback`：审核 Agent 已完成审阅，反馈已持久化，执行 Agent 需要修改原结果。
- `revision_pending`：修正版已排队；修正版必须有新的 revision 标识，并保留原结果和反馈的关联。
- `done`：逻辑完成且结果已持久化。
- `sent`：外部发送或写入完成，并有外部系统回读证据。
- `needs_human`：无法由证据读取、参与者澄清或有限反馈周期解决，需要人工判断。
- `failed`：执行、依赖、解析、状态转换或外部系统最终失败；必须保留失败原因和阶段。

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

反馈必须包含规则、观察结果和修改要求。审核 Agent 不能直接改写执行 Agent 的业务正文；服务只保存 run、revision、反馈、session 和外部回执之间的关系。同一任务最多允许两个内容反馈周期；基础设施失败不消耗内容反馈周期。

## 统一禁止事项

- 所有任务都不得使用 `discard` 动作。
- 所有任务都不得写入 `discarded` 状态。
- 不得用“丢弃”代替审核反馈、修正原 run、重新排队、人工升级或失败记录。
- 已产生的 run 不可被覆盖；修正版必须创建新的 revision，并通过父 run 或反馈关系关联原结果。
- 只有外部系统回读确认成功，任务才能进入 `sent`。

如果任务确定无需执行，应进入 `skipped`（保留原因）；如果结果需要修改，应进入 `needs_feedback`；如果处理失败，应进入 `failed`。

## 进程、租约和恢复

- 生产入口是 launchd 管理的 `com.ceo-agent-service.main`，由 supervisor 管理 worker 和 audit-web。
- 同一 `conversation_id` 同时只能有一个执行 Agent 持有 Codex session lock。
- 每个执行/审核 run 都有独立 lease、revision 和 transcript 范围。
- 重启时，未开始外部动作的 run 可以恢复；已经开始外部动作但结果未知的审核 run 只能进行匹配的只读回读，不得直接重放写入。
- 外部动作没有精确回执时，状态必须保持未知或失败，不能推断为成功。

## 任务类型

- `okr_review`：指定人员和周期的逐 KR 评审。执行 Agent 先生成评审，审核 Agent 审阅并反馈修改，修正版通过后才发送。
- `weekly_okr`：定时生成管理者 OKR 进度周报。分析、报告发布、群摘要发送和外部回读全部完成后，才推进周报成功日期。
- 普通消息、审批、会议、邮件、任务跟踪和 WeChat 任务都遵循同一生命周期与反馈规则，只在领域输入、工具权限和外部回读方式上不同。

## 文档索引

- 总体 A/B 架构：`docs/architecture.md`
- 路由失败和恢复：`docs/runtime-route-recovery.md`
- Consumer/Audit 反馈设计：`docs/superpowers/specs/2026-08-06-consumer-audit-agent-design.md`
- OKR 领域输入和输出：`docs/superpowers/specs/2026-06-08-okr-review-runner-design.md`
- 当前实现：`app/agent_orchestrator.py`、`app/consumer_agent.py`、`app/audit_agent.py`、`app/okr_review.py`、`app/weekly_okr_report.py`、`app/store.py`
