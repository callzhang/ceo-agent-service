# 质量巡检与收敛

本文件定义质量巡检边界。巡检回答“持久化工作是否停止推进、是否有失败未处理、结果是否与
History 一致”；它不解释 Agent 如何调用命令，也不维护外部动作的未知状态机。修复、重试和
外部发送由正常队列及 Agent Skill 执行。

## 当前运行契约

入口：

```sh
"$HOME/miniforge3/bin/python" -m app.cli quality-check --db "$CEO_WORKER_DB"
```

退出码 `0` 表示没有 violation；退出码 `2` 表示至少有一项 violation 或必需数据源不可检查。
结果 JSON 至少包含 `checked_at`、`mode`、`ok`、`checked_sources`、`missing_sources`、`violations`
和 `attention`。

## 检查覆盖

| 事实源 | violation | attention |
| --- | --- | --- |
| `reply_tasks` | `failed`、过期 `processing`、到期未领取的 `pending` | 新鲜 `pending` / `processing` |
| `reply_attempts` | 最新 trigger 的 `failed` / `blocked` 没有活动重试 | 有 `pending` / `processing` 任务 |
| `agent_runs` | 超时 `pending` / `running` 或终态与任务 projection 不一致 | 新鲜 `pending` / `running` |
| `work_summary_inputs` | `failed`、超时 `processing` | `pending` / `processing` |
| `follow_up_drafts` | `failed`、过期草稿 | 未来计划 follow-up |
| `meeting_alignment_jobs` | `failed` 或超时处理中状态 | 等待、排队或发送中 |
| `okr_review_requests` | `failed`、超时 `processing` | `pending` / `processing` |
| 外部投递队列 | 明确失败的投递 | 活动状态 |
| `feedback_events` | 未记录 `resolved_at` 的反馈 | 无 |
| `daily_scan_state` / `wechat_read_state` | scanner `last_error` 或 reader 不可用 | 无待处理工作时 reader 未就绪 |
| `errors` | 最近 4 小时新建、未解决且没有活动重试路径的服务错误 | Codex 容量暂停期间的共享事件 |

旧数据库中的 `unknown`、`reconciled`、`side_effect_state` 和 `pending_reconciliation` 仅作为历史
字段展示，不构成当前 violation，也不会触发特殊队列。当前代码只创建 `failed`、`needs_feedback`、
`needs_human` 和成功终态。

## Trigger 收敛

回复类记录按 `channel + conversation_id + trigger_message_id` 归并；审批按
`oa_process_instance_id` 归并。巡检只看该键上最新 attempt，旧失败保留在时间线，不覆盖当前
projection。后续同一 trigger 的成功或 `no_action` 会解除当前告警，但不会删除原始错误。

`needs_human` 只表示现有 Skill 无法覆盖且确需 Derek 决定的规则缺口。权限、网络、CLI、材料
读取、provider 或服务机制失败必须记录为 `failed`，不得包装成管理选择。

## 每小时处理顺序

1. 读取 `/workers` Attention、History 列表、attempt 详情和通知，确认同一 trigger 状态一致。
2. 对每个 `failed` 核对真实错误、当前 `available_at`、重试条件和是否已有后续 terminal projection。
3. 对每个 `needs_human` 核对规则缺口、影响范围和互斥选项；可由参与者回答的事实改为澄清候选。
4. 对运行中的任务检查 lease、session 和持久化结果，避免并发领取。
5. 记录 provider 返回的 `operation`、`target`、稳定 result identifier（若有），用于精确去重。

巡检只观察和产出诊断，不执行发送、审批、重放或工具命令，也不通过“未知工具”或 read-only
规则阻断 Agent。外部动作中断时，下一次 Agent turn 按业务 Skill 读取当前状态；质量门禁只检查
任务是否持续推进及结果是否与 projection 一致。

## 外部依赖边界

`quality-check` 检查 DingTalk，以及当前有 active task 或最近 72 小时失败 attempt 的可选通道。
没有待处理工作引用的未配置 Lark 仅保留在 `channel-doctor`；一旦任务进入队列，通道不可用才
成为 violation。Codex 不在本命令中执行登录或写入 smoke test，运行可用性由 `agent_runs`、`errors`
和独立诊断反映。

## 测试不变量

`tests/test_quality_gate.py` 应覆盖：

- 必需表缺失时 `ok=false` 且有 `source_missing`；
- 最新 failed attempt 没有活动重试时产生 violation；
- 后续 done/no_action projection 能解除旧错误告警但保留历史；
- stale processing 被发现；
- 旧 unknown/reconciled/side_effect 字段不触发新状态机；
- 新代码不会创建 `pending_reconciliation` 或依赖命令级审核。
