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
| `service_restart_before_effect` | 服务在本轮执行完成前重启 | 可重试 |
| `service_restart_after_completed_turn` | 服务重启发生在结果完成后、任务投影更新前 | 可重试；不得创建新的业务 attempt |
| `reply_task_lease_exhausted` | 任务租约/接管尝试达到上限 | 终态失败 |

## Runtime 路由

| 错误码 | 解释 | 默认处理 |
| --- | --- | --- |
| `runtime_execution_failed` | Runtime 执行失败但尚未归入更具体的阶段 | 按 stage、source_code 和 retryable 处理 |
| `runtime_provider_unreachable` | Runtime provider API、网络或连接不可用 | 修复 provider 后按 retryable 重试 |
| `runtime_provider_auth_failed` | Runtime provider token/凭据失效 | 修复凭据后重试 |
| `runtime_capability_missing` | 所需 runtime 能力未配置 | 终态失败，修复配置后重跑 |
| `runtime_execution_failed` | runtime 执行失败 | 按 retryable 重试 |
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
| `target_resolution_not_found` | 无法唯一解析业务目标 | 终态失败；不得猜测目标 |
| `delivery_failed` | 外部发送请求或 provider 结果失败；具体原因保存在 `source_code` | 按 provider retryable 重试 |
| `oa_skill_workflow_incomplete` | OA Skill 流程未完成 | 按当前业务能力重试或失败 |
| `provider_target_failed` | provider 或 Skill 无法完成目标选择；应用层不猜测目标 | 按具体 source_code 处理 |

provider 还可能返回自身的 `server_error_code`、HTTP 错误或 DWS/Friday 原始码；这些值
属于事实证据，不在应用层重新分类。查看具体 provider 的错误含义时，应同时查阅其
Skill 或 provider 契约文档。

## 历史错误码

历史数据库可能包含已经废弃的 `unknown`、`reconciled`、旧恢复状态或早期命令审核错误。
它们只作为历史事实展示，不参与当前状态迁移。历史数据迁移到当前代码时，必须保留原始
错误事件，并把当前投影归入 `failed`、`done` 或 `needs_human` 的现行语义。

## 相关文档

- 总体架构：[architecture.md](architecture.md)
- 运行机制：[runtime-mechanism.md](runtime-mechanism.md)
- 路由恢复：[runtime-route-recovery.md](runtime-route-recovery.md)
- 当前结构化结果契约：`app/agent_wire_contracts.py`
- History 可读化映射：`app/history_actions.py`
