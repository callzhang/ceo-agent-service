# 送达证据不再依赖独立账本

状态：设计，待 Derek 批准后实施。Derek 2026-09-18：「不需要一个账本，只要每个任务如果计划发送，
audit agent 核实后就发送，然后记录发送成功的服务器返回码作为证据，这个发送就算完成了。」

## 现在是什么样

一次成功发送之后，服务写三处：

1. `agent_runs.final_result_json` 里 Audit 结果的 `external_result.live_result_reference`——provider 返回。
2. `external_action_results`——按 `external_action_key` 存的动作台账。
3. `sent_replies`——消息正文 + provider 返回 + 反馈 token，供 History、撤回、反馈回链使用。

第 1 项是运行自己的事实，第 2、3 项是它的投影。投影由 `_sent_reply_projection_from_result`
（`app/worker.py`）生成，`record_completed_agent_message_delivery`（`app/store.py`）落库。

## 为什么要拆

2026-09-16/17 修的四个故障，全部只存在于「第二份状态」里：

- 实时路径从不写账本：`_audit_terminal` 构造的 `OrchestrationResult` 不带 `consumer_result`，投影第一道门
  就返回 None。1741 次完成的 Audit 运行只产出 2068 行 `sent_replies`，而且几乎都来自事后补账（`de176316`）。
- 补账扫描挂在 `consume_once`，而生产走 `process_claimed_reply_task`，那段代码从未在服务里运行过（`9c7e1e91`）。
- 扫描的候选 SQL 漏了 `open_task_id` 等标识，`dws chat +dm` 这种只返回任务句柄的真实送达永远选不中（`6e97e459`）。
- 七行幻影账本：模型自述 `delivery_status: "sent"` 被当成送达写进账本，History 显示发过、实际没有。

同时发现账本的跨重跑去重**本来就不成立**：`external_action_key` 由 `action_identity` 参与计算，而
`action_identity` 是 Consumer 每次重跑现写的，重跑必然换 key，查不到旧记录。真正防住重复发送的是
Consumer 重读会话时看见自己上次发的消息，不是账本。

结论：账本没有承担它被设计来承担的职责，却制造了一整类「两份状态不同步」的故障。

## 目标状态

**唯一权威事实是运行自己的结果**：Audit 核实后发送，provider 返回码记进
`external_result.live_result_reference`，证据闸（`app/dingtalk_send_evidence.py`）已经要求这个返回必须
出现在运行记录的调用流里。发送到此完成。

Agent 发送在 `sent_replies` / `external_action_results` 里的部分降级为**派生结果**，不再有独立写入路径；
表本身保留，因为它还装着 2003 行历史和 OKR 这类直接发送的记录。

## 谁在写这张表（实测，2026-09-18）

| 来源 | 9 月行数 | 有运行可派生 |
|---|---|---|
| Agent 运行（Consumer/Audit） | 78 | 能 |
| OKR 周报发送 `app/cli.py:2077` | 18 | **不能**：不走运行模型，但发送当场就有 provider 返回 |
| 2026-09 之前的全部 2003 行 | — | 不能：早于运行模型，只读保留 |

微信不在其中：它有自己的 `wechat_deliveries`（101 行），`sent_replies` 里微信行数为 0。

所以原则统一成一句：**谁执行发送，谁在发送当场把 provider 返回记下来，只记一次，事后不再对账。**
Agent 发送的记录者是 Audit 运行；OKR 发送的记录者是那条命令自己。

## 现有读者与它们真正需要的东西

| 读者 | 位置 | 需要 | 来源 |
|---|---|---|---|
| History 显示已发消息 | `app/audit_web.py:6131` | 正文、时间、attempt 关联 | Agent：派生；直接发送：自身记录 |
| 反馈回链（👍/👎） | `app/feedback_events.py:15` | `feedback_token` | 从正文 `extract_feedback_link_context` 得出 |
| 撤回 | `app/audit_web.py:9831` | provider 消息 id / recall key | `live_result_reference` |
| Quality gate | `app/quality_gate.py:343` | 该对象是否已有成功送达 | 改查带证据的 executed 运行 |
| 定时任务恢复 | `app/scheduled_task_recovery.py:100` | 同上 | 同上 |

## 一次做完，不分阶段

Derek 2026-09-18：分阶段更差。理由成立且与 AGENTS.md 一致——分阶段必然要让新旧两条路并存，
那正是「为兼容旧逻辑写兼容代码」；中间还会出现「表在写但没人读」这种比两端都糟的状态；
每一阶段都要一次部署，而部署要重启，重启现在归心跳会话且会打断运行中的轮次。

**安全机制不是分阶段部署，而是改之前的离线等价证明**：

1. 写出派生函数 `delivery_projection(task, consumer_run, audit_run)`。
2. **不提交任何代码**，先拿线上库把全部 78 行 Agent 来源的 `sent_replies` 跑一遍，逐字段比对
   派生结果与表中现有行：正文、provider 返回、recall key、feedback token、attempt 关联。
   不一致的逐条查清，而不是调派生函数去迁就表——表里可能本来就有错行（已知七行幻影已删）。
3. 等价证明通过后，一次改完：读者切到派生、停止写入、删除 `record_completed_agent_message_delivery`
   与 `_repair_completed_message_delivery_projections` 及其候选 SQL、OKR 路径改为记录自身发送证据。
4. 一次部署（交心跳会话重启）。

## 会失去什么，必须明说

1. **跨 business object 的「这条消息发过没有」查询变慢**：现在是一次索引查表，之后要扫该对象的运行并
   解析 JSON。History 列表页要注意 N+1。
2. **没有强制的唯一约束**：`external_action_key` 的唯一索引消失后，防重复只剩证据闸和 Consumer 的上下文
   判断。鉴于跨重跑去重本就失效，这不是新风险，但要在文档里写清楚现状。
3. **老格式运行结果解析不了**：172 条早于 `310234e8` 的结果不满足当前契约。派生失败时 History 显示
   「该运行的送达记录不可读」，不要伪造，也不要吞掉。

## 不做什么

- 不迁移历史 `sent_replies`。
- 不改 `action_identity` 的生成方式（跨重跑稳定标识是另一个问题，不在本方案内）。
- 不动邮件退订的回执机制，它有自己的领域证据。

## 验收

- 线上任意一条已发消息，History、撤回入口、反馈回链的表现与拆之前一致。
- 新的 Agent 发送不再写 `sent_replies`，而 History 仍然显示它；OKR 发送仍有自己的记录。
- `grep -rn "record_completed_agent_message_delivery\|_repair_completed_message_delivery_projections" app/`
  无结果。
- 证据闸的回放结果不变（当前：286 条 9 月运行，222 放行 / 64 拦下）。
