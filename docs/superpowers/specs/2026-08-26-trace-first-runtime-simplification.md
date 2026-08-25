# Trace-first runtime simplification

## 背景与目标

当前任务、run、attempt 和外部发送分别维护状态，造成重复且容易冲突。执行 Agent 的 trace 已保存输入、工具调用、审核反馈、修订和外部回读，因此数据库只保留调度与恢复所需的最小状态。

任务统一使用五个状态：`pending`、`running`、`done`、`failed`、`needs_human`。`processing` 合并为 `running`；`sent`、`needs_feedback`、`revision_pending` 都是 trace 事件，不再是任务状态。

审核 Agent 的反馈方向固定为“审核 Agent -> 执行 Agent”：审核 Agent 写入 `audit_feedback`，执行 Agent 基于反馈生成新的 revision，原 trace 不覆盖。审核 Agent 执行外部动作并写入 `external_effect`、`external_readback`；回读成功后任务进入 `done`。不能自动解决时进入 `needs_human`。

所有任务路径禁止 `discard` 和 `discarded`。无需动作的结果在 trace 写入 `agent_output/no_action` 后进入 `done`。

## 最小持久化字段

任务保留 id、status、attempt_count、current_run_id、error、创建/更新时间；run 保留租约、session、trace 引用和时间戳。业务正文、反馈、发送状态、周报成功日期均从 trace 派生。

## 验收

- OKR 评审与每周 OKR 进度汇报使用同一状态机。
- Trace 可见 `audit_feedback -> agent_revision -> audit_result` 顺序。
- 历史按五个任务状态筛选，详情展示 trace。
- 重启恢复只扫描 `pending`/`running`。
