# Action 实体统一设计

状态：Task 1–7 集成候选已实现并完成本地/只读生产副本验收；仍待 parent 全分支审阅，未推送或部署。本文保留原设计及其约束。2026-10-22。
原始代码基线：`7c37664518f04f8afcefd937b53484bef503a507`，Store19.12／Email53。Task 7 集成基线和生产配对证据见 [Task 7 报告](../../.superpowers/sdd/2026-10-21-action-entity-unification/task-7-integration-report.md)。

## 1. 问题与目标

现有十张核心表不等于十种业务实体。`shared_action_definitions.SOURCE_TO_TARGET` 将旧类型映射为 plan_version、effect_step、action_attempt 的来源变体；`shared_action_lifecycle._allocate` 仍按旧 namespace 分配，`transition` 仍按 origin 返回专用结果。之前的 A/B/C/D 确实消除了部分双写，但没有退出这些存储和接口边界。

本设计只定义外部动作统一这个改进点，不重写服务全部架构。验收指标是退休类型、字段唯一归属、专用写入器/状态分支/索引减少以及功能可恢复；不设表数量目标。类型变少但旧对象原样移入大 JSON、字节副本或长期 alias，不算完成。

## 2. 约束（实施计划逐字引用）

- 保留 provenance、必要状态、已采用业务事实和实际执行回执。
- 原生 trajectory 是交互原文的唯一来源；有独立可验证来源的正文不另存副本。
- 邮件预览保留至多 1,000 Unicode 字符。
- 不新增无真实来源的业务字段；新增技术引用必须标明旧字段来源和读取必要性。
- 不需要运行时兼容旧结构、旧 DTO 或旧类型名称；只保留必要的显式迁移与回退工具。
- 不新增或改变审查、授权、确认、取消、重试和外部结果核对策略。
- 先在隔离 worktree 和数据库副本验证；原生 CLI 验收的对外发送使用替身。
- 正迁后新增的业务事实和回执必须能通过当前数据逆迁恢复；不以恢复旧数据库备份冒充 schema 回退。
- 只有新备份验证成功后才清理旧备份；发布使用现有 app.deploy。

## 3. 统一边界

Task 表达待完成工作；Run 表达执行/审查；Action 表达一个能独立执行、恢复并确认结果的具体操作；Business 表达已采用业务事实；Email 表达一封邮件。操作名称可不同，生命周期、身份、领取和结果所有权不得按来源再复制。

第一发布单元限定为已有外部动作：reviewed candidate 的具体操作、邮件直接动作、prepared/sent 消息。它们使用一种 Action 记录结构；方案、审查、工作进展和普通错误不因此被改名为 Action。其余来源在后续单元退出；过渡期仍存在旧类型必须在进度表明确标未完成。

完整一期不包含退订浏览器 continuation/步骤的重写；这些真实可恢复操作不能在没有字段契约时按“过程”删除。它们需要与邮件单元一起形成下一份独立计划；本计划不得声称整个 Actions 表已经只剩一种类型。

## 4. 字段所有权与真实来源

以下是来源约束，非臆造的最终 DDL。执行 Task 1 必须把每个实际字段展开为机器可检查的映射和静态 DDL；未覆盖字段阻止发布，不能留到迁移事务临时猜测。

| 现有来源 | 所有权决策 |
|---|---|
| `email_actions.action_id/action_type/parameters_json/status/next_attempt_at/started_at/created_at/updated_at` | 归具体 Action；读取该 Action 时不再查旧来源投影 |
| `email_actions.action_plan_id/classification_id/config_version` | 保留真实计划/邮件关联；config_version 从不可变父计划取得，不保存子副本；不删除历史授权快照 |
| `email_action_attempts.action_id/attempt_number/status/started_at/finished_at/provider_operation/provider_target/provider_result_id/error` | 活跃执行信息归所属 Action；独立真实回执和未证实可丢弃的历史事实逐条保留；不以“最新值”覆盖不同历史事实 |
| `candidate_action_attempts.execution_id/action_index/external_action_key/status/result_json/created_at/updated_at` | 具体动作与其所属方案版本/执行的关系；复用已存在外部动作身份，不重新发号模拟一次新 effect |
| `external_action_results.external_action_key/operation/target_identifiers_json/provider_result_json/result_digest/first_agent_run_id/created_at` | 实际外部结果由具体动作唯一拥有；相同外部动作可被多次执行引用，不复制 provider 正文 |
| `outbound_postfixes.channel/delivery_key/final_body/feedback_token/postfix_version/created_at` 及 prepared 三个实际列 | 准备正文、发送身份、领取时间、收到回执时间分别保留；不能用准备时间替代发送时间 |
| `sent_replies.reply_text/sent_at/send_result_json/recall_key/recall_status/recall_error/recalled_at/feedback_token/channel/conversation_id/trigger_message_id/agent_run_id/external_action_key` | 对应发送 Action 的最终结果和撤回业务状态；逐条核对正文/回执是否和 prepared、external result 同一事实 |
| `sent_reply_observers.sent_reply_id/agent_run_id/reply_task_id/created_at` | 多 Task/Run 观察同一真实发送的关系；不能把每个 observer 变成一次新发送 |

来源定位：`app/action_storage.py:138-141`、`app/prepared_action_definitions.py:1-17`、`app/sent_result_definitions.py:1-25`、`app/shared_action_lifecycle.py:212`、`app/email_store.py:10994`、`app/reviewed_candidate_store.py:553-610`。

不得直接删除所有 attempts。已有诊断错误可以按原文来源退休；没有原生来源、但实际产生过的 provider 回执不是诊断日志。若一项操作确实有多个不同 provider 结果，应在同一 Action 的最小结果集合中保留现有回执事实，不保存旧 attempt 对象、整段日志或来源类型；实现前在字段契约中列出每个集合成员的实际旧字段、独立身份和基数。集合的具体技术编码由 Task 1 固定，后续任务不得自行另建 attempts/steps 类型。

## 5. 身份、版本和关系

统一使用 Action 的物理身份及现有稳定业务身份；旧 namespace/公开整数同号不能视作同一实体。迁移匹配优先使用已存在 external_action_key、delivery 身份和真实外键；不得按正文、标题相似度或“最新记录”猜合并。

方案可以有多个动作；Consumer/Audit 修订仍保留不同 Run 与精确方案绑定；同一外部结果可被多次运行观察。旧链接/API 同提交更新为统一实体读取，不永久保留 `legacy_key + record_type` 兼容分派。迁移辅助映射只用于显式转换和当前数据逆迁；不保存原文/旧行快照。

无法证明两个来源是同一业务事实时，保留独立身份和事实，不制造 provider 成功，不以空值默认修补。它们是否影响本单元覆盖必须在字段契约中具体列出，不能默默作为“历史归档”继续一套旧运行时。

## 6. 生命周期及读取

共同存储/读取接口不接收 origin/source_type；Provider adapter 只执行既有操作，不拥有领取、重试计数和结果双写。来源特有旧状态的映射必须按实际 caller 使用固定；不能把 dispatched 当成功、把 unknown 当 failed，或因 schema 变化重发。

正常运行只保存执行所需正文和唯一业务结果。已结束正文退休须逐条验证 native/provider 的完整字节与定位，并确保 History/恢复的读取链可工作。来源不可用要明确显示，不允许悄悄取另一版本或创建归档副本。

## 7. 发布边界与验证

一次发布包含新 DDL、所有目标 caller、API/UI、迁移和当前数据逆迁；不发布双写阶段。开发期间新实现可在副本独立测试，生产继续旧版本。Task 1 字段/身份契约先审查，随后必须实施，不把盘点作为项目结束点。

验证至少包括：多来源同号 ID；多 observer 同一发送；相同正文不同实际发送；provider 已接受但本地未完成；多次运行共用一份外部结果；不同历史回执；发送后撤回；修订前后方案绑定；source missing；迁移后新写入再 inverse/旧代码冷启动/reforward。原始已有 FK finding 做集合差分，不宣称源库零错误。

容量比较要求同一业务人口、同一 VACUUM 条件，分别报告表页、索引、字段正文、临时/备份。2026-10-21 的 493.26 MB 是旧快照，不能作为后续生产恒定基线；25.4 MB 是此前局部索引/重复内容机会，不是本单元保证收益。

## 8. 设计完成与否

执行状态（2026-10-22）：详细字段映射、静态 DDL、迁移/逆迁、writer/reader/API/UI 切换及 Task 6–7 验收已落在当前候选；Task 7 报告给出生产 Store19.12／Email53 只读备份、旧版本 Store 冷读写、current-fact inverse/re-forward、真实 native Consumer/Audit 与严格内存 provider 的边界。覆盖的七个有数据 source families 转为共同 Action/Receipt/Observer/typed identity map；另两项零行旧 discriminator 声明已按其 child 事实归属处理。其余 Actions 类型、专有 unsubscribe、Task/Run 及其他业务类型不在本单元完成范围。候选仍未发布，生产仍是 Store19.12／Email53；parent 全分支审阅和正式 release/readback 尚未完成。
