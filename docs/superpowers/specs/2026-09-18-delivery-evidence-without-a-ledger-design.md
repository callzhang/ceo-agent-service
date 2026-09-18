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

`sent_replies` 与 `external_action_results` 降级为**只读投影**，从 (task, consumer run, audit run) 派生，
不再有独立写入路径。

## 现有读者与它们真正需要的东西

| 读者 | 位置 | 需要 | 能否从运行派生 |
|---|---|---|---|
| History 显示已发消息 | `app/audit_web.py:6131` | 正文、时间、attempt 关联 | 能：正文在 Consumer 提案 payload，时间/关联在 run |
| 反馈回链（👍/👎） | `app/feedback_events.py:15` | `feedback_token` | 能：token 本就是从正文里 `extract_feedback_link_context` 出来的 |
| 撤回 | `app/audit_web.py:9831` | provider 消息 id / recall key | 能：在 `live_result_reference` 里 |
| Quality gate | `app/quality_gate.py:343` | 「这条失败记录是否已有成功送达」 | 能：改为查该 business object 有无带证据的 executed 运行 |
| 定时任务恢复 | `app/scheduled_task_recovery.py:100` | 同上 | 同上 |
| 微信路径 | `app/cli.py:2077` | `record_sent_reply` 直接写 | **不能**：微信不走 Agent 运行，见下 |

`external_action_results` 目前只有 `store.py` 内部和恢复路径读，去重语义如上所述已经失效。

## 分四步做，每步独立可回滚

**第一步：把投影变成派生函数，不改存储。**
抽出 `delivery_projection(task, consumer_run, audit_run)`，让现有写入路径和所有读者都经过它。
此时行为不变，但「怎么从运行得到一条送达」只有一处定义。可独立提交与验证。

**第二步：读者改为按需派生，不再查表。**
History、反馈、撤回、quality gate、恢复路径逐个切到第一步的函数。每切一个，用线上数据对比
「表里的行」与「派生结果」必须逐字段一致——不一致先查清楚再继续。这一步结束时，表还在写，但没人读。

**第三步：停止写入，删掉补账扫描。**
移除 `record_completed_agent_message_delivery` 的调用、`_repair_completed_message_delivery_projections`
及其候选 SQL。这三样正是前述四个故障的来源。

**第四步：处理微信和历史数据。**
微信回复不经过 Consumer/Audit 运行，`app/cli.py:2077` 直接写 `sent_replies`。两个选择：让微信保留自己的
送达记录表，或把微信发送也纳入运行模型。这一步需要单独决定，不阻塞前三步。
历史表保留只读，不迁移、不删除——它是已发生事实的记录。

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
- 新发送不再写 `sent_replies`，而 History 仍然显示它。
- `grep -rn "record_completed_agent_message_delivery\|_repair_completed_message_delivery_projections" app/`
  无结果。
- 证据闸的回放结果不变（当前：286 条 9 月运行，222 放行 / 64 拦下）。
