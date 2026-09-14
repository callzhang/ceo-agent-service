# Attempt 页面展示 Consumer 执行结果设计

**日期：** 2026-09-14
**状态：** 已获设计批准，待文档审阅
**范围：** Attempt 详情页的只读 Consumer 结果投影

## 目标

在 `/attempts/{attempt_id}` 的现有基本信息网格中直接显示当前 Attempt 关联的最终 Consumer run 的四项执行结果：

- `confidence`
- `information_completeness`
- `rule_coverage`
- `risk`

页面还必须说明同一业务任务是否已有较新的 Consumer run 正在等待或运行。该状态不能覆盖当前 Attempt 已关联最终 Consumer run 的指标。

## 已确认的产品决定

1. 采用基本信息网格内的字段组，而不是独立 Consumer 卡片或仅保留跳转链接。
2. 只展示当前 Attempt 关联的最终 Consumer run；不把较早 revision、其他历史 run 或后续未完成 run 的数值当作当前结果。
3. `confidence`、`information_completeness` 与 `rule_coverage` 从 0–1 的持久化值显示为四舍五入后的整数百分比，例如 `0.82` 显示为 `82%`。
4. `risk` 保留已持久化的 `low`、`medium` 或 `high` 枚举值。
5. 任一结果不可用时，四项全部显示 `—`，并显示明确的错误原因；不得隐藏整个字段组。
6. 新 Consumer run 为 `pending` 或 `running` 时，显示其状态；它不改变已展示的四项指标。

## 方案比较

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 将结果放入现有基本信息网格 | 采用 | 与 Attempt 的当前状态、权限、重试次数和时间一起可快速判断；以“Consumer 执行结果”分组避免混淆。 |
| 独立 Consumer 结果卡 | 不采用 | 语义边界更强，但需要用户在页面内额外寻找信息。 |
| 仅保留“查看 Consumer 记录”链接 | 不采用 | 不能满足在 Attempt 页面直接查看结果的需求。 |

## 数据选择与流向

页面使用已有 `reply_attempts`、`reply_tasks` 与 `agent_runs` 数据，不新增列、表、Agent 轮次或状态机分支。

```text
Attempt.agent_run_id（当前终结 run）
  ├─ role = consumer：这个 run 就是当前 Consumer run
  └─ role = audit：读取 parent_agent_run_id 指向的 Consumer run
       ↓
Consumer.final_result_json 按 ConsumerAgentResult 解析
       ↓
Attempt 基本信息中的 Consumer 执行结果字段组

当前 ReplyTask 的 execution_generation
       ↓
较新的 Consumer pending/running run，或尚未创建 run 的排队任务
       ↓
“新 Consumer run：等待中 / 运行中”状态提示
```

Attempt 的终结 run 可能是 Consumer 本身，也可能是 Audit。Audit 的 `parent_agent_run_id` 是其精确审阅的 Consumer run，因此这是当前指标唯一允许使用的父子关系。页面不按列表位置、时间排序或“最近成功”的宽松规则选择指标。

新 run 状态从当前 `ReplyTask.execution_generation` 读取，独立于旧 Attempt 所关联终结 run 的 generation。这样重跑已经排队或运行时，旧的当前指标仍可见，而用户也能看到重跑进度。

## 页面呈现

在现有 Attempt 基本信息网格中增加下列字段组：

| 分组 | 字段 | 正常值示例 |
| --- | --- | --- |
| Consumer 执行结果 | `confidence` | `82%` |
| Consumer 执行结果 | `information_completeness` | `75%` |
| Consumer 执行结果 | `rule_coverage` | `100%` |
| Consumer 执行结果 | `risk` | `medium` |

若有较新的运行中 Consumer run，字段组下方追加一个状态行：

- `新 Consumer run：等待中`：持久化 run 为 `pending`，或任务已重排队但尚无具体 Consumer run。
- `新 Consumer run：运行中`：持久化 run 为 `running`。

没有较新的 `pending` 或 `running` Consumer run 时，不显示状态行。

## 不可用结果与错误呈现

| 条件 | 四项字段 | 错误原因 |
| --- | --- | --- |
| 关联 Consumer 有可解析最终结果 | 显示百分比与风险 | 不显示 |
| Consumer 已完成但没有最终结果 | 全部 `—` | `Consumer 未保存最终结果` |
| 最终结果不符合现行严格契约 | 全部 `—` | `Consumer 结果不符合当前契约` |
| Consumer run 失败 | 全部 `—` | 已持久化且适合页面展示的错误详情；若无详情则显示错误码 |
| Attempt 终结 run 无法解析到 Consumer 父 run | 全部 `—` | `未找到当前 Attempt 关联的 Consumer run` |

页面不展示 `final_result_json` 原文，也不在渲染时补写、修正、重跑或恢复任何历史结果。错误信息沿用页面现有的安全展示方式，避免把原始持久化内容直接投影到 HTML。

## 边界与非目标

- 不修改 Consumer/Audit 的结果契约，也不为旧结果增加兼容转换。
- 不修改任务路由、重试、反馈、恢复、审计或外部动作行为。
- 不创建新的 API、数据库迁移或持久化投影。
- 不展示历史 Consumer revision 的指标；历史轨迹继续由既有 Consumer 记录页承载。
- 不根据任何指标自动改变 Attempt 状态、触发人工决策或执行外部动作。

## 测试与验收

为 Attempt 详情渲染增加回归覆盖，至少验证：

1. Audit 终结的 Attempt 沿 `parent_agent_run_id` 读取对应 Consumer，并渲染 `82%`、`75%`、`100%` 与 `medium`。
2. Consumer 自身为终结 run 时，直接渲染该 run 的四项指标。
3. 最终结果为空或不符合现行契约时，四项均显示 `—`，并显示准确原因。
4. 失败 Consumer 显示 `—` 和已持久化错误原因，而不输出原始结果 JSON。
5. 同代存在多个 revision 时，页面只显示当前终结 run 精确关联的 Consumer，而不使用较早或其他 revision 的数值。
6. 新 Consumer run 分别为 `pending`、`running`，以及任务已排队但 run 尚未创建时，均显示正确状态，已有指标保持不变。
7. 渲染路径只读：不会创建或更新 ReplyAttempt、ReplyTask、AgentRun 或运行时尝试记录。

## 实现影响

预期实现集中在 Attempt 详情的读取/呈现层及其测试。Store 只提供现有读取接口；不会有 schema、运行时、路由或服务重启需求，除非实际实现发现当前读取接口无法表达该已批准的数据选择规则。
