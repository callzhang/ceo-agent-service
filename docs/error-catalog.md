# 系统错误码目录

本文档是 CEO Agent Service **应用层错误码的唯一解释入口**。错误码记录在
`reply_tasks.error`、`reply_attempts.send_error` 或 `agent_runs.structured_error_json` 中。
同一个错误码可能同时出现在任务投影和执行 run；解释以本目录为准。

应用层只定义任务调度、Agent 结果契约、路由和投递的错误。业务 Skill、DingTalk/DWS、
Friday 或 Codex provider 返回的原始错误码会原样保留，并归入对应的 provider 类别；
应用层不得把 provider 错误改写成含糊的统一错误。

## 处理规则

| 类型 | 含义 | 处理方式 |
| --- | --- | --- |
| 可重试 | 当前执行未完成，仍可能由下一轮 Agent 得到结果 | 任务回到 `pending`，遵守退避和上限 |
| 终态失败 | 当前任务在现有能力下无法完成 | 保留 `failed`，允许人工触发同一业务 attempt 重跑 |
| 授权需要 | 缺少明确的业务授权或规则决策 | 进入 `needs_human`，讨论可复用规则，而不是指挥单个任务 |

`consumer_retry_exhausted` 和 `audit_retry_exhausted` 是重试上限结果，不是新的业务原因；
必须同时查看同一 run 的原始错误码。

## 当前错误与历史错误的展示边界

错误事件是 append-only 执行事实；Attention、Workers 和质量门展示的是业务对象的 current
projection。对具有 `business_object_key` 的任务，当前状态由 `business_object_tasks.reply_task_id`
指向的 task 决定。已经被后续成功 task 取代的旧 `failed` 行仍可在 History 展开，但不得继续计入
当前失败数、触发自动重试或显示人工处理按钮。

排查页面数字不一致时依次核对：

1. Attention 是否只列出当前失败；
2. Workers 的 Reply tasks 是否标记为 `current business-object projection`；
3. 质量门是否为零；
4. History 中的旧错误是否只是历史展开项；
5. 是否存在没有稳定业务键的独立任务，这类任务仍按自身状态统计。

因此“原始失败仍保留”和“当前失败为零”可以同时成立。不得为了清零页面而直接修改或删除
生产 SQLite 中的旧错误、session、run、tool event 或 provider 结果。

## Agent 与结果契约

| 错误码 | 解释 | 默认处理 |
| --- | --- | --- |
| `codex_process_failed` | Codex 进程异常退出 | 可重试 |
| `codex_process_timeout` | Codex 执行超时 | 可重试 |
| `codex_result_missing` | 运行结束但没有可解析的结构化结果 | 可重试 |
| `codex_result_invalid` | 有输出但不符合当前 typed-result schema | 可重试，修复契约后重跑 |
| `codex_stream_invalid` | 流式事件无法解析为合法执行事件 | 可重试 |
| `agent_result_failed` | Agent 返回正式 `failed` 结果 | 按结果中的 retryable 决定 |
| `agent_feedback_missing` | 修订流程缺少必要反馈 | 终止当前轮并记录失败 |
| `consumer_retry_deferred` | Consumer 尚未达到下一次重试时间 | 调度等待 |
| `consumer_retry_exhausted` | Consumer 已达到重试上限 | 终态失败 |
| `audit_retry_deferred` | Audit 尚未达到下一次重试时间 | 调度等待 |
| `audit_retry_exhausted` | Audit 已达到重试上限 | 终态失败 |
| `audit_revision_exhausted` | 内容反馈周期已达到上限 | 按当前规则进入 `needs_human` |

## 任务、租约与恢复

| 错误码 | 解释 | 默认处理 |
| --- | --- | --- |
| `run_not_found` | 任务引用的执行 run 不存在 | 终态失败，修复数据关系后重跑 |
| `agent_run_unavailable` | 当前 run 无法被接管或执行 | 可重试 |
| `execution_failed` | 无法进一步分类的 Agent 执行失败，必须同时查看阶段和原始错误 | 按阶段和 retryable 重试或终止 |
| `runtime_session_conflict` | session 所有权冲突 | 等待租约释放后重试 |
| `stale_agent_turn_recovery` | 发现过期 Agent turn，已进入普通重试 | 可重试 |
| `stale_before_agent_start` | 任务在 Agent 启动前失去租约 | 可重试 |
| `service_restart_interrupted` | 服务在 Agent 本轮执行完成前重启；不按命令或 effect 类型分流 | 可继续原 session，并以新 agent run 重试同一业务 task |
| `service_restart_after_completed_turn` | 服务重启发生在结果完成后、任务投影更新前 | 可重试；不得创建新的业务 attempt |
| `reply_task_lease_exhausted` | 任务租约/接管尝试达到上限 | 终态失败 |

## Runtime 路由

| 错误码 | 解释 | 默认处理 |
| --- | --- | --- |
| `runtime_execution_failed` | Runtime 执行失败但尚未归入更具体的阶段 | 按 stage、source_code 和 retryable 处理 |
| `runtime_provider_unreachable` | Runtime provider API、网络或连接不可用 | 修复 provider 后按 retryable 重试 |
| `runtime_provider_auth_failed` | Runtime provider token/凭据失效 | 修复凭据后重试 |
| `runtime_capability_missing` | 所需 runtime 能力未配置 | 终态失败，修复配置后重跑 |
| `runtime_executor_failed` | runtime executor 本身异常 | 按基础设施重试策略处理 |
| `runtime_result_invalid` | runtime 返回不符合结果契约 | 修复 adapter/契约后重试 |
| `runtime_result_validation_failed` | runtime 结果校验失败 | 终态失败或按 provider retryable 重试 |
| `runtime_post_start_failed` | runtime 已启动但后续阶段失败 | 按同一 run 的基础设施策略重试 |
| `codex_provider_auth_failed` | Codex provider 认证失败 | 修复认证后重试 |
| `codex_capacity_pause` | provider 容量不足，任务进入延迟队列 | 到 retry_at 后自动重试 |

## 业务数据与 provider

| 错误码 | 解释 | 默认处理 |
| --- | --- | --- |
| `provider_read_failed` | 业务 provider 读取失败；具体原因保存在 `source_code` | 按 provider retryable 重试 |
| `provider_target_failed` | provider 或 Skill 无法完成目标选择；具体原因保存在 `source_code` | 按 provider 的 retryable 处理 |
| `delivery_failed` | 外部发送请求或 provider 结果失败；具体原因保存在 `source_code` | 按 provider retryable 重试 |
| `oa_skill_workflow_incomplete` | OA Skill 流程未完成 | 按当前业务能力重试或失败 |

provider 还可能返回自身的 `server_error_code`、HTTP 错误或 DWS/Friday 原始码；这些值
属于 provider 诊断，不在应用层重新分类。查看具体 provider 的错误含义时，应同时查阅其
Skill 或 provider 契约文档。

## 历史投影迁移

历史迁移只更新 `reply_tasks`/`reply_attempts` 的当前投影，不直接修改生产数据库，
也不删除任何 `agent_runs`、session、tool event 或原始错误事件。迁移读取旧错误关联的
结构化 run 信息：能确定具体阶段和 provider 原始码时写入 `stage`、`source`、
`source_code`；不能确定时使用 `execution_failed` 或 `delivery_failed`，并把旧值保留为
历史事件中的 `legacy_code`。新的重试沿用原 `reply_attempt`，追加新的 `agent_run`，
不会把重试上限伪装成新的业务原因。

会议历史投影迁移使用显式脚本，先预览再申请执行：

```sh
python scripts/migrate_error_projections.py --db <database>
python scripts/migrate_error_projections.py --db <database> --apply
```

脚本默认只读；`--apply` 只更新会议任务的 current projection，不更新
`meeting_alignment_runs` 历史错误字段。

## 定时任务与服务命令

trigger 自身的跳过原因写在 `scheduled_task_runs.skip_or_error_reason`；带 `scheduled-task:<id>`
来源写入 `errors` 的条目进入 Attention。

| 错误码 | 解释 | 默认处理 |
| --- | --- | --- |
| `scheduled_task_previous_execution_active` | 上一轮 trigger 或其 execution 尚未终态，本轮跳过 | 不处理；不写 errors |
| `scheduled_task_runtime_unavailable` | Agent 任务固定的 Runtime route 不健康或缺少能力 | 修复 Runtime 或改任务配置；provider 暂时过载或断连造成的路由暂停只写 run 记录，不进入 Attention |
| `scheduled_task_managed_skill_unavailable` | 绑定的精确 managed Skill revision 未加载或已禁用 | 加载该 revision 或重新绑定 |
| `scheduled_task_operation_skill_unavailable` | 引用的 operation Skill 不可用 | 安装或修复该 Skill |
| `scheduled_task_execution_unavailable` | 派发或执行前发现 Runtime、Skill 或工作目录已不可用 | 同上；execution 以 `skipped` 收口 |
| `scheduled_task_service_command_unavailable` | 服务命令任务引用的命令不在目录中 | 检查任务的 `command` 与 `service_command_options` |
| `scheduled_task_service_command_failed` | 服务命令抛出异常，trigger 记 `failed`；依赖不可达时只有连续失败超过 15 分钟才写本条，且一次中断只写一条 | 查看 detail 中的原因；命令幂等，下一次 trigger 会重跑，成功后自动标记为已恢复 |

## 微信通道

| 错误码 | 解释 | 默认处理 |
| --- | --- | --- |
| `wechat_data_permission_required` | macOS 拒绝访问微信数据，或 Reader 回报 `permission_required`；只记录一次 | 授予 CEO WeChat Reader 的 App Data 权限；producer 下一次成功读取后自动恢复，sender 循环需重启服务 |
| `wechat_reader_unavailable` | Reader IPC 连续失败 3 次，已请求一次 Reader 重启；只记录一次 | 观察 `wechat.reader` 健康；producer 下一次成功读取后自动 resolve |
| `wechat_sender_loop_error` | sender 循环中未分类的异常，或持续的 sqlite 锁 | 查看 detail |

## 历史错误码

历史数据库可能包含已经废弃的 `unknown`、`reconciled`、旧恢复状态或早期命令审核错误。
schema 升级会删除这些旧投影字段，把旧 `unknown` run 的当前状态迁移为 `failed`，并追加
`legacy_unknown_migrated` state event。原始错误事件、session、runtime attempt、tool event 和
provider 结果保持不变；当前业务投影归入 `failed`、`done` 或 `needs_human` 的现行语义。

`runtime_effect_policy_violation`、`agoal_live_read_unreviewed`、`audit_recovery_ambiguous`、
`audit_reconciliation_result_invalid`、`audit_reconciliation_evidence_mismatch`、
`wechat_producer_loop_error` 和 `wechat_consumer_loop_error` 只允许作为历史
错误文本保留。当前代码不得生成这些错误，也不得将它们纳入 Attention、Workers 当前失败或
自动恢复条件。

## 相关文档

- 总体架构：[architecture.md](architecture.md)
- 运行机制：[runtime-mechanism.md](runtime-mechanism.md)
- 路由恢复：[runtime-route-recovery.md](runtime-route-recovery.md)
- 当前结构化结果契约：`app/agent_wire_contracts.py`
- History 可读化映射：`app/history_actions.py`
