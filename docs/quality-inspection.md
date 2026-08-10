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
| `reply_attempts` | 最新 trigger 的 `failed` / `blocked` 没有活动恢复任务；24 小时内最新结果仍为 `dry_run` | 有 `pending` / `processing` recovery task |
| `agent_runs` | `unknown` 副作用、超过 30 分钟的 `pending` / `running` | 新鲜 `pending` / `running` |
| `work_summary_inputs` | `failed`、超过 21 分钟的 `processing` | `pending` / `processing` |
| `follow_up_drafts` | `failed`、计划时间已过且超过 15 分钟仍为 `draft` / `approved` | 未来计划的 follow-up |
| `meeting_alignment_jobs` | `failed`，或 `pending` / `processing` / `ready_to_send` / `retry` 超过 21 分钟未更新 | 仍在等待、排队或发送中的会议任务 |
| `okr_review_requests` | `failed`、超过 21 分钟的 `processing` | `pending` / `processing` |
| 外部投递队列 | `work_todo_dingtalk_links`、`wechat_deliveries`、`memory_write_events` 的失败或未知发送 | 这些队列的活动状态 |
| `feedback_events` | 未记录 `resolved_at` 的反馈 | 无 |
| `daily_scan_state` / `wechat_read_state` | scanner 仍有 `last_error`；微信 reader 不可用且存在待处理微信回复 | 无待处理微信工作时的 reader 未就绪 |
| `errors` | 最近 4 小时新建的服务错误 | 无 |

当前时间窗口是有意区分的：`errors` 为最近 4 小时，最新 `dry_run` 为最近 24 小时，
其余队列按当前所有未终态记录扫描。超时值来自 `app/quality_gate.py`，不要在 heartbeat
或审计页面重新硬编码另一套值。

## Trigger 收敛规则

回复类记录必须按业务 trigger 而不是按历史 attempt 行计数。当前键为：

`channel + conversation_id + trigger_message_id`

巡检只看这个键上 `updated_at` 最新的 attempt：

- 较早的 `blocked` / `dry_run` 后来已有 `sent` 或 `skipped`，旧行不再报警。
- 最新 `failed` / `blocked` 但同一 trigger 仍有 `pending` 或 `processing` 的
  `reply_task`，记为 `attention`，表示恢复正在进行。
- 最新 `failed` / `blocked` 没有活动恢复任务，才是 violation。
- `unknown` 外部写入不是可重试失败；只能进入只读 reconciliation，确认未发生后才
  创建新的 generation。

审批类记录按 `oa_process_instance_id` 收敛。后续 `agent_run` 持有同一审批实例的已验证
终态动作回执时，它会解除旧 `oa_approval` 的 `failed` / `blocked` 告警；单纯评论回执不会
解除仍在运行审批的告警。巡检不会因旧行保留而把已完成的审批重新标红。

因此 `blocked` 不是“等管理决策”的同义词。它描述当前依赖、材料、权限或安全条件
不满足；是否需要人做选择由 `needs_human` 的业务结果和对应 UI/通知承载。

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

## 外部依赖边界

`quality-check` 默认附加当前 `default_channel_gates()` 的实时检查，即 DingTalk 和
Lark。任一 gate 不是 `ready` 都作为 `channel:<name>/not_ready` violation。离线诊断
可以显式使用 `--no-verify-channels`，但该结果不能作为线上健康证明。

微信目前由本地 reader/delivery 状态覆盖，并没有在此命令中执行独立的 token 刷新或
发送 smoke test。Codex 也不在本命令中执行登录或写入 smoke test；其运行可用性由
`agent_runs`、`errors` 和独立的 `doctor-mcp` 诊断反映。DWS 的 gate 是可用性探针，
不是业务写入重试许可。文档和告警不得把这些局部检查表述成所有外部能力均已验证。

## 巡检、reconciliation 与修复的关系

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
