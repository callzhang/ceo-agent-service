# 执行 Agent 给出长期记忆，系统负责写入

状态：Derek 2026-09-24 批准并实施。与下文不同的两处按实现为准：队列是每个任务执行代一行（不是每条记忆一行），
每条记忆的写入结果记在该行的 `written_memory_ids_json` 里；`memory_write` 参数在写入时由固定不变的记录组装，
不预先存整份参数。

Derek 2026-09-24：「可以在 consumer agent 里面加一个结构化的输出，说明要长期记忆的内容？然后系统直接
调用 memory write？」「记忆不需要再审核了。」前提是服务的 Codex 运行关掉钩子（方案 1），「有持久化信息
就记录」这件事改由系统保证，高价值信息不能丢。

## 结论先说

执行 Agent（Consumer）的结果里加一个必填字段 `durable_memories`，由它当场写出本轮确认过的长期信息，
没有就给空列表。任务结束时系统取最后一版结果里的这个字段，排进队列，用服务自己的 memory-connector
客户端逐条写入。同时服务的 Codex 运行加 `--disable hooks`，不再让 memory-connector 的收尾钩子在一轮里
逼出第二份结果。

## 为什么要改

1. **收尾钩子会换掉结果。** memory-connector 0.4.0 装在 Codex 里的 Stop 钩子，会在 Agent 结束前追加
   「检查要不要写记忆」。Agent 按同一个结果格式再回一份，服务取结果时从后往前找第一份合格 JSON，
   于是取到的是这份。`codex exec --json` 的事件流里看不到钩子插话，服务无法分辨。日报运行 83977、
   83997 都被这样换成了 `no_action`（真正的结果是写文档加单聊）。
2. **让 Agent 自己调工具写记忆，会衰减到不写。** 9/19 统计：截至当日两周内没有一轮主动写过，最后一条是
   7/26；读取正常（144 次）。只有走系统队列的会议结论还在写。Derek 9/19 定：写 Memory 是系统在任务
   结束时做的检查（`1f40c5c0`），当时只建了队列表 `task_memory_write_events`，写入部分一直没做，表为 0 行。

## 设计

### 1. 结果格式加必填字段

`app/schemas/consumer_agent_result.schema.json` 与对应模型加：

```text
durable_memories: [            # 必填，可为空列表
  {
    "title":        "一句话标题",
    "content":      "一到三句自然语言，一条一件事",
    "source_time":  "这条信息产生的时间（ISO-8601），不是写入时间",
    "source_refs":  ["出处：消息 id、文档链接、审批单号……"],
    "subject":      { "type": "Person | Project | Customer | Organization", "name": "…" }   # 可选
  }
]
```

与 memory_write 参数的对应（Derek 2026-09-24：「memory_write 是不是要有 title、time、provenance 之类的」）。
memory_write 收 `data`、`type`、`created_at`（来源内容的时间，必填）、`thread_id`、`source_description`、
`source_metadata`、`provenance_metadata`、`entity_type`、`entity_attributes`，没有单独的标题参数。

| 参数 | 谁给 | 取值 |
|---|---|---|
| `data` | Agent | `title` + 空行 + `content`（与会议结论同样把标题写在正文第一行） |
| `type` | 系统 | `text` |
| `created_at` | Agent | `source_time` |
| `source_description` | Agent | `title` |
| `entity_type` / `entity_attributes` | Agent | `subject.type` / `{name: subject.name}`，没有就不传 |
| `thread_id` | 系统 | 任务的会话标识 |
| `source_metadata` | 系统 + Agent | `{kind: manual_source, schema_version: 1, payload: {channel, conversation_id, conversation_title, trigger_message_id, reply_task_id, source_refs}}` |
| `provenance_metadata` | 系统 | `{kind: manual_provenance, schema_version: 1, payload: {actor: ceo-agent-service consumer, agent_run_id, execution_generation, route, model, intent: task_durable_memory}}` |

出处（Agent 指认）和写入者记录（系统写死）分开，事后能反查每条记忆从哪来、谁写的，且写入者记录不依赖
Agent 填对。`app/memory_connector_client.py` 的 `write` 目前只传前五个参数，要扩展为透传其余参数。

字段说明（写进结果契约，也就是 Agent 看到的格式说明）：
- 只写本轮确认过的长期信息：Derek 或相关人的偏好、做出的决定、可复用的规则或约定、稳定的业务事实、
  未完成事项的明确接续点。
- 不写：临时任务、日志、代码、一次性错误、未经确认的推测、敏感原文、密钥/令牌、未经授权的文档内容、
  与已有记忆重复的内容。
- 用人名而不是「用户」。

必填是为了每轮都必须想一次；以前可以不调工具，所以慢慢就不写了。

### 2. 取哪一份

任务进入 `done`、`skipped`、`needs_human` 任一终态时，取该任务**当前执行代**里最后一个完成的
Consumer 运行的结果。审核打回之前的版本已被修订取代，不取。任务在终态前失败、没有完成的 Consumer
结果，就没有可写的内容，记为 `skipped` 并写明原因。

### 3. 队列与写入

- 复用 `task_memory_write_events`。它现在是每个任务一行、`reply_task_id` 唯一，无法记录「一条写成、
  一条失败」。表是空的，改为每条记忆一行：键为 `(reply_task_id, execution_generation, item_index)`，
  加 `payload_json` 列保存上表组好的完整参数（入队时就定下，重试不重新组装）；没有记忆的任务写一行 `skipped` 占位，保证「每个结束的任务都有一个记忆结论」
  （`count_finished_tasks_without_memory_decision` 这个检查继续成立）。
- 排队：任务进入终态的同一处把这些行写入（沿用 `enqueue_finished_task_memory_write_events` 的防重复写法）。
- 写入：新增服务命令定时任务「写入任务长期记忆」，领取到期行，按 `payload_json` 调用现有的
  `app/memory_connector_client.py`（会议结论也用它，直接走 MCP，不经 Agent）。成功记 `memory_id`，失败按会议写入同样的退避重试，
  重试上限后记 `failed` 进 Attention。
- 不经审核 Agent（Derek 2026-09-24）。

### 4. 关掉服务 Codex 运行里的钩子

`app/codex_runner.py` 组装 `codex exec` 命令时加 `--disable hooks`（Codex 0.154 自带的特性开关）。
这会同时关掉 memory-connector 的开场读取（`user_get`）和收尾提醒，以及浏览器插件的收尾钩子；服务运行
各自带上下文，开场读取不影响结果，记忆写入改由上面的系统流程保证。

Claude 线路不用改：`app/claude_runtime_adapter.py` 已用 `--setting-sources ""` 加显式 `--settings`
（API 线路再加 `--bare`），本机的插件和钩子本来就进不了服务运行。

### 5. 上线顺序

字段、队列、写入任务和 `--disable hooks` 同一次上线，避免出现「钩子关了、系统还没接上」的空档。

## 不做的

- 不在审核 Agent 里判断记忆。
- 不改会议结论的写入流程。
- 整理 Tasks 的 Task Agent、邮件分类 Agent 暂不加字段，先看执行 Agent 这条线的效果。

## 风险

1. **Agent 可能习惯性给空列表。** 必填只能保证它想过，不能保证它写。上线一周后按渠道统计「有记忆的任务
   占比」，和 9/19 之前的主动写入量对比。
2. **记忆质量没有人工把关。** Derek 已确认不再审核；字段说明里的排除项是唯一约束。
3. **memory-connector 的服务端凭据过期**时，写入会停在重试，按会议写入同样的处理：进 Attention，
   用现有的重新授权流程恢复。

## 验证

1. 单元测试：结果格式必须带该字段、每条的必填子字段齐全；参数按上表组装（系统字段不取自 Agent）；终态取最后一版 Consumer 结果；修订前的版本不入队；每条记忆一行、
   无记忆的任务一行 `skipped`；写入成功、失败重试、重试上限；`codex exec` 命令含 `--disable hooks`。
2. 上线后手动跑一次日报：确认结果不再被换掉、日报发出，且该任务的记忆行状态正确。
3. 一天后看 `count_finished_tasks_without_memory_decision` 为 0，写入成功数与失败数。
