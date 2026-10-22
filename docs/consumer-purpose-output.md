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
| `oa` | `comment` | 目标为 `process_instance_id`，参数为 `content`；作为普通 OA 提案进入 Audit。 |
| `oa` | `request_access` | 目标含 `process_instance_id` 和 `file_id`，参数为 `content`；Consumer 请求该文件只读访问。 |
| `general` | `reply`、`request_access` | 目标为 `conversation_id`、`message_id`，参数为 `content`；回复原会话消息。 |
| `general` | `react` | 同样定位原消息，参数为 `emoji`。 |

未知 action、非字符串 ID、缺失的必填参数和额外参数均不能作为合法新输出。
服务从同一动作定义生成 schema 并校验输出，再规范化为现有内部候选。OA 的
approve、reject、return、comment 和普通 plan 都由 Audit 审阅；文件权限申请和权限等待 retry
是 Consumer purpose 动作，不提交给 Audit。

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
2. 有可用申请途径时，Consumer 直接返回一次带准确 `file_id` 的 `request_access`。服务通过既有 OA 评论执行器向已核实责任人请求该文件的只读权限；同一任务同一文件只发一次。此权限申请不进入 Audit。请求送达不是权限已恢复的证据，当前材料审核停止。
3. 服务核实请求回执后，写入同一 purpose 等待状态并安排首次延后重读。进程中断时只对账原请求；没有确认“未发生”之前不重复发送。
4. 权限仍未恢复时，Consumer 可以直接提出 `retry`，引用本任务已核验的权限申请回执；retry 不进入 Audit，也不执行外部写操作，只按共享预算等待后重新读取。
5. 到期重读后，依据新事实继续业务判断；已发送的申请不重复发送。

OA `request_access` 复用现有 OA 评论执行器发出只读权限请求，不直接修改文件 ACL。申请动作的 verified 回执只证明请求已送达，权限是否开通必须通过后续读取确认。不能把“已发送申请”写成“已获得权限”。

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

### 最多 3 次重读，每次间隔 12 小时

| 边界 | 实现语义 |
| --- | --- |
| 计数起点 | 原权限申请不计入；申请核实后的每次后续读取机会在启动前预留一次。 |
| 次数上限 | 原权限申请不计入；最多预留 3 次后续 Consumer 重读。预留后来源刷新失败或进程退出，该次仍消耗预算。 |
| 时间起点 | 从已核实原申请的持久化 dispatch 记录时间起算，保留 72 小时硬截止；先达到次数上限就停止。 |
| 间隔 | 每次权限重读固定间隔 12 小时，不再指数退避。旧预算首次被新代码读取时更新为该间隔，保留原 deadline、已用次数和 reservation。 |
| 停止条件 | 次数或时间先到任何一项即停止；到达期限时不再开始新读取或新 Consumer。 |
| 持久化范围 | 所有文件的权限申请共用同一业务对象和 purpose 预算；没有业务键时绑定 reply task。session、revision、generation、重启和普通 attempt 退款不清零。 |
| 幂等与恢复 | 同一预约重复检查不重复扣数；更换申请回执不能重置预算。 |

次数预留使用现有 SQLite 短事务。模型不能指定重试次数、间隔、申请时间或期限。
Worker、定时任务和 Email 的等待分支遵守延后时间，不消耗普通任务 attempt 预算；
权限等待自己的三次预算仍独立生效。

OA purpose 达到限制后停止自动重读，并让 Consumer 再运行一次形成普通 OA 退回候选；该轮不得读取、重试或再次申请被阻塞的文件。退回说明仅限“审批账号当前无法访问材料，请提供可访问版本或修正权限”，然后按普通 Consumer → Audit → System Execution 和节点读回流程处理。服务不合成或直接执行退回；有效候选未形成时才进入人工处理。明确的权限申请失败也使用同一 OA 退回流程。外部访问权限问题本身不得被分类为 `failed`。

非 OA purpose 在权限重读耗尽后仍可记录为 `permission_blocked`：Attempt 为 `blocked`，ReplyTask 停在 `needs_human` 队列外，表示等待权限处理。History 对精确的 `consumer_purpose_retry_exhausted` 可提供一次人工触发的 Consumer 访问检查；该入口保留原预算，只有原权限已开通时才使用，不重发权限申请、不增加自动重读次数。新旧格式的已验证权限申请回执都可授权该单次检查；旧 OA 评论回执在迁移时存入 `receipts`，无需补造 `access_requests` 记录。读取成功后，Consumer 仍须独立形成新候选，由 Audit 审核业务结果，再按已有执行与读回门禁处理。
预算耗尽后，同一业务对象的新触发没有本代 Consumer run，也记为 `permission_blocked`，挂靠该对象最近一次已完成的 Consumer run，而不是 `permission_retry_state_invalid`；人工访问检查据此可用。
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

三次重读、12 小时间隔和 72 小时截止的边界用受控时钟、SQLite 持久化和编排回归验证。
原生 Consumer 的 retry/return 固定案例只验证模型与 schema 的互操作。
部署、Skill 加载和业务动作是否完成分别核实，不能把测试通过、发布成功或
申请送达等同于权限获批或 OA 业务完成；验证不重放历史 OA 动作。

## 6. History 对权限等待的展示

OA `request_access` 评论经系统执行回执核验成功后，History 仍可保持 `pending`，
因为当前目的在等待权限开通后重读材料。此时 Attempt 原始 `send_error` 可能保留
早先的执行错误；详情应以当前任务的 `consumer_purpose_retry_requested` 状态为准，
并把旧错误明确标为历史。只有当前业务对象绑定的权限等待预算引用同一个已核验
评论回执时，才可显示评论送达及回执中的原始操作时间。不能把评论送达写成权限已开通，
也不能因此重发评论或把等待中的 OA 审批标成失败。
