# Consumer purpose 输出与权限重试

本文说明已实现的 Consumer 业务输出、OA 退回及权限等待协议。
实现入口为 `app/consumer_purpose.py`、`app/agent_wire_contracts.py`、
`app/consumer_purpose_retry.py` 和 `app/agent_orchestrator.py`。
部署和验收证据见 [验证记录](consumer-purpose-actions-validation.md)。

## 1. 通用业务字段

每个 Consumer purpose 的业务核心使用以下五个字段。它们属于普通 Consumer
输出，不是另一个 `dependency_action` 协议。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `action` | string | 当前 purpose 支持的动作。服务在推理前确定允许集合。 |
| `reason` | string | 选择该动作的说明，不能为空；服务不解析原因关键词来决定分流。 |
| `target` | object | 该动作的准确业务目标，按动作分别校验。 |
| `payload` | object | 该动作的参数，按动作分别校验。 |
| `evidence_refs` | string[] | 支持判断的证据引用；`retry` 必须引用原权限申请的已核实回执。 |

完整 wire 输出还保留 summary、评分、阶段绑定和长期记忆等元数据。
下文 JSON 只展示五个业务核心字段，不能直接当成完整 wire 结果提交。
purpose 由服务从任务的 OA 链接、已选 Skill 或明确 OA 动作上下文确定，
不是模型可以自行切换的输出字段。

## 2. 已注册的动作

| purpose | action | 参数与效果 |
| --- | --- | --- |
| 两者共有 | `plan` | `target={}`；`payload` 为完整的现有 ConsumerProposal，支持多动作任务。 |
| 两者共有 | `no_action` | 无业务动作，`target` 和 `payload` 均为空对象。 |
| 两者共有 | `retry` | 等待后重新读取，两个对象均为空；不直接调用外部发送工具。 |
| 两者共有 | `failed`、`needs_human` | 两个对象均为空；沿用完整结果中的错误或人工决策元数据。 |
| `oa` | `approve`、`reject` | 目标含字符串 `process_instance_id`、`task_id`；参数为 `remark`。 |
| `oa` | `return` | 目标额外含字符串 `target_activity_id`；参数为 `remark` 和 `revert_action`。 |
| `oa` | `comment`、`request_access` | 目标为 `process_instance_id`，参数为 `content`；使用现有 OA 评论动作。 |
| `general` | `reply`、`request_access` | 目标为 `conversation_id`、`message_id`，参数为 `content`；回复原会话消息。 |
| `general` | `react` | 同样定位原消息，参数为 `emoji`。 |

未知 action、非字符串 ID、缺失的必填参数和额外参数均不能作为合法新输出。
服务从同一动作定义生成 schema 并校验输出，再规范化为现有内部候选，交给
Audit 审阅。`plan` 不绕过既有动作合同和审核。

### OA 的 `return` 是真实退回

Consumer 的 `action: return` 映射到 `dingtalk-oa/revert_task`，
表示把 OA 流程退回准确节点。它不是函数返回值，也不是 Audit 的纠正反馈。
Audit 自己的 return/reject 仍属于审核反馈协议。

```json
{
  "action": "return",
  "reason": "已核实该附件无权访问，且没有可用的权限申请途径，需要提供可访问材料",
  "target": {
    "process_instance_id": "example-process",
    "task_id": "example-task",
    "target_activity_id": "example-originator-node"
  },
  "payload": {
    "remark": "请提供当前审批账号可访问的材料链接，以便继续核对实质内容。",
    "revert_action": "REVERT_FOR_RESUBMIT"
  },
  "evidence_refs": ["current-read:access-denied", "request-route:unavailable"]
}
```

以上 ID 和证据引用是说明用占位值；真实候选必须使用当前读取核实的值。
`revert_action` 只接受 `REVERT_FOR_APPROVAL` 或 `REVERT_FOR_RESUBMIT`。
`REDIRECT_PROCESS` 是外部操作记录类型，不能填作该请求参数。
退回候选仍须经现有 Audit、执行器和节点读回，不能凭权限错误直接操作 OA。

## 3. 权限申请与等待

1. 先区分真实权限拒绝与下载、解析、OCR、客户端等技术读取失败。
2. 有可用申请途径时，Consumer 形成一次 `request_access` 候选，向已核实的责任人请求访问，并设置 `continue_after_execution=false`。请求送达不是权限已恢复的证据，当前审核必须停止。
3. 完整候选经 Audit 和执行器处理，核实原申请动作回执后，服务安排首次延后重读。
4. 权限仍未恢复时，Consumer 可以提出 `retry`，引用同一原申请回执，交 Audit 审阅。
5. 到期重读后，依据新事实继续业务判断；已发送的申请不重复发送。

当前 `request_access` 通过 OA 评论或原会话回复发出请求，没有直接修改文件 ACL
的新增执行器。申请动作的 verified 回执只证明请求已送达，权限是否开通必须
通过后续读取确认。不能把“已发送申请”写成“已获得权限”。

等待的业务核心示例：

```json
{
  "action": "retry",
  "reason": "access requested",
  "target": {},
  "payload": {},
  "evidence_refs": ["receipt:original-verified-request-external-action-key"]
}
```

Consumer 可按证据来源返回原始 key，也可能带 `receipt:` 或
`prior_receipt_id=` 标记。服务只剥离一个已知标记，再与已存储的原始
`external_action_key` 做精确比较；不得省略或额外改写 key 的其余部分。最终必须解析为当前任务恰好一条已核实的权限申请动作，
且原候选 action 确为 `request_access`。普通评论、模型自行声明、未知 key、其他任务回执以及
uncertain 动作均不能启动权限等待。唯一兼容的旧候选是迁移前的 OA
`material-read-access-block` 评论：它必须有本任务的已核验 comment 回执、完全相同的
action identity，且旧候选只有一个对应评论动作。普通旧评论仍不满足条件。仅输出
`reason: access requested` 不满足条件。
`retry` 不是技术错误，不能同时携带非空技术错误码或置真的技术错误标志。

### 最多 10 次，最长 72 小时

| 边界 | 实现语义 |
| --- | --- |
| 计数起点 | 原权限申请不计入；申请核实后的每次后续读取机会在启动前预留一次。 |
| 次数上限 | 最多预留 10 次；不保证一定运行满 10 次 Consumer。预留后来源刷新失败或进程退出，该次仍消耗预算。 |
| 时间起点 | 从已核实原申请的持久化 dispatch 记录时间起算，保守地固定 72 小时期限。 |
| 退避间隔 | 首次 5 分钟，之后按已预留次数指数增长，单次最多 12 小时；下一次时间不晚于期限。 |
| 停止条件 | 次数或时间先到任何一项即停止；到达期限时不再开始新读取或新 Consumer。 |
| 持久化范围 | 绑定业务对象和 purpose；没有业务键时绑定 reply task。session、revision、generation、重启和普通 attempt 退款不清零。 |
| 幂等与恢复 | 同一预约重复检查不重复扣数；更换申请回执不能重置预算。 |

次数预留使用现有 SQLite 短事务。模型不能指定重试次数、间隔、申请时间或期限。
Worker、定时任务和 Email 的等待分支遵守延后时间，不消耗普通任务 attempt 预算；
权限等待自己的十次预算仍独立生效。

达到限制后返回 `failed_terminal`，错误码为 `consumer_purpose_retry_exhausted`。
它不会自动退回、拒绝或发送消息，也不能据此认定材料业务不合格。
业务动作完成或经审核 `no_action` 后关闭活动等待；原计数和期限保留。
72 小时边界控制新读取的启动，不表示强制中断已经开始的调用。

## 4. 无法申请及技术读取失败

真实无权访问、且没有可用申请途径时，不进入权限重试。OA Consumer 可根据
当前事实提出正常业务 `return`，请求可访问材料；其他 purpose 使用自己声明的
动作。不得把无法申请权限冒充已经申请，再通过 `retry` 延长等待。

下载、解析、OCR 或客户端失败本身不证明访问被拒绝，更不证明申请人有错。
这类问题不能单独成为 OA 拒绝或退回依据。普通 `dependency_read_unavailable`
也已退出无上限的外部等待分支，按现有持久化技术失败次数上限处理。

## 5. 验收与查阅

- [架构中的 Consumer purpose 协议](architecture.md#consumer-purpose-动作输出和权限重试)
- [运行机制中的权限等待](runtime-mechanism.md#consumer-purpose-output-和有界权限等待)
- [外部动作合同](system-action-contracts.md#consumer-purpose-action-names)
- [测试、原生模型验证及生产发布记录](consumer-purpose-actions-validation.md)
- [OA 操作 Skill 来源](../ci/shared-skills/dingtalk-oa-approval/SKILL.md)
- [文档审阅 Skill 来源](../ci/shared-skills/ceo-document-review/SKILL.md)

十次和 72 小时的边界用受控时钟、SQLite 持久化和编排回归验证。
原生 Consumer 的 retry/return 固定案例只验证模型与 schema 的互操作。
部署、Skill 加载和业务动作是否完成分别核实，不能把测试通过、发布成功或
申请送达等同于权限获批或 OA 业务完成；验证不重放历史 OA 动作。
