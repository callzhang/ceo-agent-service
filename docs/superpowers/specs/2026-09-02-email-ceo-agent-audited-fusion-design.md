# Email 与 CEO Agent 审核融合设计

日期：2026-09-02

状态：书面规范已批准，已进入实施计划

适用分支：`codex/email-integration-main`

## 1. 权威范围

本设计记录 Derek 选择的方案 A：

- `label`、`mark_read`、`archive`、`move` 和 `trash` 是 Email 子系统的确定性动作，由独立 Email worker 直接执行并回读，不创建 CEO Agent task，也不进入 Audit Agent；
- `unsubscribe` 是当前唯一允许创建的 `channel=email` Agent task，必须经过 Consumer A 提案、Audit Agent B 审核与执行、外部结果回读；
- 本设计范围内所有 Email 回复保持关闭，系统不生成 `auto_reply`，不连接 SMTP，也不发送 `mailto:` 退订邮件；
- `trash` 只表示移入可恢复废纸篓，禁止永久删除、IMAP `EXPUNGE` 和清空废纸篓。

本设计取代以下文档中的未来目标架构，但不改写其历史实验或实现记录：

- `2026-08-29-email-ceo-agent-integration-design.md` 中关于 `auto_reply` 和 Email Agent 动作的旧范围；
- `2026-08-30-email-unsubscribe-branch-integration-design.md` 中 Consumer-direct、零 Audit run 的特批生命周期；
- `2026-08-29-email-classifier-design.md` 中把当前退订运行时描述为 Consumer-direct 的段落。

`docs/architecture.md` 和 `docs/runtime-mechanism.md` 继续描述当前代码事实，直到本设计完成实现、测试和部署；不得在代码仍为 Consumer-direct 时把它们提前改成未来状态。

## 2. 当前事实与设计输入

### 2.1 分类器实验结论

最终 hard-example v3 仍是一个 CPU 友好的 word+char TF-IDF Logistic 分类器：

- 328 封邮件、143 个来源；
- 五折来源分组开发验证中，`junk >= 0.324` 为 34/35，precision 97.14%；
- 单封预测 p95 约 5.98 ms，满足 100 ms 目标；
- 冻结后对全新来源收集 20 个高置信度候选，只有 16 个是 junk，precision 80%；
- 误判包含媒体报道、政府合同、产品合作和 CEO 活动等可能有价值的陌生商业接洽。

因此 `email-tfidf-word-char-junk-label-v3-eb1dfe4a` 已被拒绝。当前没有 active、action-eligible 的生产模型，所有 model-only 邮箱写动作必须保持关闭。后续只保留：

- readonly shadow 预测和排序；
- 待反馈；
- 用户确认样本；
- 版本化训练与模型历史；
- 未来达到来源独立动作门槛后的选择性自动处理。

用户对高置信度邮件动作的授权与模型准入是两个独立条件。授权已经存在，但当前实验不满足准入，不能据此执行真实邮箱写操作。

### 2.2 当前代码事实

当前分支已经包含：

- 多账户 Email connector、readonly IMAP 扫描、分类器 registry、反馈训练和四个 Email 页面分区；
- 独立 Email worker；
- 确定性 provider action 的 claim、租约恢复、幂等执行和 readback；
- Consumer-direct 退订、headless browser、专用持久 profile、步骤记录和 terminal result text；
- 更早的 audited unsubscribe Store、effect digest、append-only continuation 和 `awaiting_audit` 基础设施。

当前代码尚未部署；真实邮箱实验均为 readonly，邮箱写操作、SMTP 连接和真实退订均为零。本设计不把已有代码或测试结果误报为生产激活。

## 3. 产品边界

### 3.1 分类、反馈和动作是三层状态

一封邮件依次经过：

```text
邮件读取
  -> 模型建议
  -> 最终分类
  -> 不可变 ActionPlan
  -> 各动作独立执行和观察
```

三层含义不能合并：

- 模型建议只表达预测，不是动作授权；
- 最终分类来自符合资格的模型或用户确认；
- ActionPlan 冻结当时的类别、模型、配置、动作和授权依据；
- 每个动作有自己的状态，分类完成不等于动作完成。

用户确认分类本身不创建一个通用 Agent task，也不意味着开放式“分析、回复或跟进”。如果该类别的固定配置包含 `unsubscribe`，既有类别配置就是明确的处理指令，系统可从确认后的不可变 ActionPlan 创建唯一的退订 task。确定性动作仍不创建 task。

### 3.2 只处理文本，附件只保留 metadata

分类器、Consumer 和 Audit 可以使用：

- 当前邮件和可取得 thread 的标准化纯文本；
- sender、recipient、cc、subject、date；
- `Message-ID`、`In-Reply-To`、`References`；
- `List-Unsubscribe` 和 `List-Unsubscribe-Post`；
- 附件文件名、MIME、字节大小、数量和 inline 标记；
- 当前分类、模型版本、配置版本、ActionPlan 和安全历史 receipt。

任何组件都不得下载、打开、OCR、解析、总结或推断附件内容。Email task 固定 `image_paths=()`。普通正文 URL 只是文本证据，不授予任意网页浏览权限。

### 3.3 回复彻底关闭

目标运行时必须同时满足：

- Email 配置动作列表没有 `auto_reply`；
- task producer 拒绝 `auto_reply`；
- Email worker 不构建 reply capability；
- SMTP secret 不是启用 Email connector 的必要条件；
- 服务不连接 SMTP；
- `ceo-mail-review` 明确禁止从分类结果或 Email task 生成回复；
- `mailto:` 退订入口只记录为不可执行，不发送邮件。

历史 schema 或历史回执可以保留兼容读取，但不能形成新的发送路径。

## 4. 选定架构

### 4.1 进程边界

生产环境继续只有一个 launchd job：

```text
com.ceo-agent-service.main
  -> app.service_supervisor
       -> existing worker
       -> audit-web
       -> independent Email worker
```

Email worker 不复用 DingTalk worker。其内部包含三个互不阻塞的组件：

```text
scanner/classifier
  -> 多账户 readonly IMAP
  -> shadow 或最终分类
  -> ActionPlan

direct-action executor
  -> label / mark_read / archive / move / trash
  -> provider readback

email-task consumer
  -> 只领取 channel=email + unsubscribe
  -> Consumer A
  -> Audit Agent B
  -> audited browser effect/readback
```

训练由短生命周期子进程运行，不增加第二个 launchd job，也不阻塞扫描或 task consumer。

### 4.2 总体数据流

```text
多个 IMAP 账户
  -> readonly scan + normalized text
  -> 单个共享 CPU classifier
  -> category + confidence + alternatives + model_id
       |
       | 未达到模型/动作资格
       -> 待反馈 -> 用户确认 -> final classification
       |
       | 达到模型/动作资格
       -> model final classification
  -> immutable ActionPlan
       |
       | deterministic authorized actions
       -> direct executor -> provider readback
       |
       | unsubscribe
       -> channel=email task
       -> Consumer proposal
       -> Audit review/execute
       -> browser/provider readback
```

当前没有模型动作资格，因此初始运行只会产生 shadow/待反馈。只有用户确认后的类别可以生成可执行 ActionPlan；未来模型通过正式门槛后，才允许 model-only ActionPlan。

## 5. 多账户 Connector

每个邮箱账户具有稳定 `account_id`，独立保存：

- IMAP host、port、TLS、username 和 secret reference；
- enabled、scan folders、scan interval；
- 每 folder 的 UIDVALIDITY、last seen UID、最近成功时间和错误；
- provider locator 与稳定业务身份之间的映射。

稳定消息身份优先使用：

```text
account_id + RFC Message-ID
```

缺失合法 Message-ID 时使用：

```text
account_id + folder + UIDVALIDITY + UID
```

不同账户中的相同 Message-ID 不合并。移动邮件只更新 provider locator，不改变稳定业务身份。

所有账户共用类别、模型、阈值、动作策略和学习集，但每条预测、反馈、动作、task 和训练样本都保存 `account_id`。第一版不做账户专属模型或账户级策略覆盖。

SMTP 配置即使因历史兼容仍存在，也必须保持 inactive：不要求、不读取、不验证、不连接，也不在页面上表示为可用发送能力。

## 6. 最终分类与动作资格

### 6.1 用户确认路径

用户确认或纠正类别后：

1. 保存 user-confirmed final classification；
2. 保存权威训练反馈；
3. 基于确认时配置创建新 ActionPlan；
4. 对计划中的确定性动作创建 direct-action records；
5. 仅当计划包含 `unsubscribe` 时创建一个 `channel=email` task。

人工确认不受模型 precision/support gate 限制，但仍受动作配置、无回复政策、不可变计划和退订 Consumer/Audit 判断约束。

### 6.2 模型自动路径

“高置信度”不是单个全局分数。model-only 动作必须同时满足：

- active model 和不可变 metadata 存在；
- 模型、tokenizer、feature、dataset 和 artifact digest 可追溯；
- 预测达到该类别被冻结并验证过的 threshold；
- 该类别和具体动作的 `auto_action_eligible=true`；
- 指标来自冻结模型后的 source-disjoint、时间合理、user-confirmed holdout；
- assistant provisional labels、开发集、重复来源和看过后重选的 threshold 不计入生产资格；
- 当前配置 threshold 与验证报告完全一致；修改 threshold 后资格立即失效。

第一版最低动作门槛为：

| 动作 | 最低 precision | 最低 positive support | 额外约束 |
| --- | ---: | ---: | --- |
| `label`、`mark_read` | 95% | 30 | 新来源必须包含在独立验证中 |
| `archive`、`move` | 97% | 30 | provider readback 可证明目标 folder/state |
| `trash` | 99.5% | 30 | 仅 move-to-Trash，永不 EXPUNGE |
| `unsubscribe` candidate | 95% | 20 | 仅 `subscription`；之后仍由 Consumer 最终判断并由 Audit 执行 |

同一类别配置多个动作时，每个动作独立判断资格。例如类别达到 label 门槛但未达到 trash 门槛时，ActionPlan 只能授权 label；不得因为同属一个类别而顺带执行 trash。

### 6.3 当前模型状态

所有现有候选，包括 v3，必须保持 `candidate`/`rejected` 且 `auto_action_eligible=false`。实施本设计不能把它们切换为 active，不能把 assistant 标签改写为 user-confirmed，也不能通过降低 threshold 制造自动覆盖率。

## 7. 不可变 ActionPlan

ActionPlan 至少保存：

```text
action_plan_id
action_plan_version
classification_id
account_id
stable_message_identity
stable_thread_identity
category
classification_source
confidence
model_id
config_version
actions[]
  action_type
  parameters
  authorization_source
  eligibility_evidence_reference
  authorized
  ineligible_reason
created_at
```

计划只授权 `authorized=true` 的动作。修改配置、重训模型或提高资格不能回头扩大旧计划。需要改变动作时必须创建新计划版本；原计划和已完成动作保持可追溯。

用户纠正已经执行过的模型分类时：

- 立即取消尚未 claim 的旧计划动作；
- 创建新的 final classification 和 ActionPlan；
- 已完成的 provider effect 作为历史事实保留；
- 第一版不自动反向移动、恢复、重新订阅或补偿既有 effect。

## 8. 确定性动作路径

确定性动作不属于 CEO Agent task，因此不违反“Agent task 必须 Consumer/Audit”的全局契约：

```text
claim direct action
  -> resolve current provider locator
  -> read flags/labels/folder
  -> already at target?
       yes -> append no-op/done readback
       no  -> execute exact configured operation
              -> read provider state again
              -> verify target state
```

每次尝试追加保存：

```text
action_id
action_plan_id
account_id
stable_message_identity
action_type
parameters_digest
status
attempt_number
owner/lease generation
provider_operation
opaque provider_target
provider_result_id
readback_digest
started_at
finished_at
sanitized_error
```

允许的状态是 `pending`、`processing`、`done`、`failed`。不增加 `unknown`、`reconciled`、`discard` 或 `discarded`。

provider 写成功但本地记录中断时，重试必须先读取当前邮箱状态；已经达到目标时补记完成，不重复写。技术失败按有限重试处理，耗尽后投影到 Attention。动作结果显示在 Email“已处理”，不伪装成 Agent run，也不进入 History。

## 9. Audited unsubscribe 生命周期

### 9.1 Task 创建与身份

只有不可变 ActionPlan 中授权的 `unsubscribe` 创建 task：

```text
channel = email
action_type = unsubscribe
lifecycle_version = email_unsubscribe_audited_v2
```

去重身份为：

```text
account_id
+ stable_message_identity
+ action_type
+ action_plan_version
```

同一身份的重扫、重启、模型重训和重复确认返回原 task，不重置 task、execution generation 或历史 run。

Task payload 只保存账户/邮件/thread/ActionPlan 的稳定身份、模型和配置版本、opaque unsubscribe entry reference 与网络策略 reference。不得复制凭证、附件内容、本地路径、完整私密 URL 或 query token。

### 9.2 Consumer A

Consumer 是只读业务判断角色。它必须：

1. 读取当前邮件、thread、标准头、安全 prior receipt 和 `ceo-mail-review`；
2. 判断它是否确实是不希望继续接收的批量订阅，而不是工作、安全、账单、订单、账户或个人邮件；
3. 检查是否已经退订或存在等价 terminal receipt；
4. 无充分依据时返回 canonical `no_action`；
5. 有充分依据时只提出与当前 ActionPlan 绑定的一个精确下一步操作；
6. 不执行浏览器 effect，不发送邮件，不改变账户、邮件、plan、entry 或 origin policy。

初始 proposal 只能是：

- `OPEN_ENTRY`；或
- 在 typed DKIM evidence 同时覆盖 `List-Unsubscribe` 与 `List-Unsubscribe-Post` 时，提出一个 authenticated `POST_ONE_CLICK`。

普通页面的按钮、表单和确认控件在页面打开前不能猜测或预计算。

### 9.3 Audit Agent B

Audit 检查 Consumer proposal 的：

- task/action/plan/classification/account/message/thread 身份；
- entry 与 exact-origin network policy reference；
- operation 类型与 opaque control reference；
- 已接受 prefix、previous effect digest 和 append-only 关系；
- 当前页面、provider、确认邮件和 prior receipt 的 readback；
- 是否存在越权浏览、附件访问、回复、登录、付款或重复 effect。

只有 Audit 可以执行已接受的操作。Consumer runtime 不暴露 `execute_email_unsubscribe` 写能力；task-bound capability 只提供给 Audit execution boundary。

Audit 认为业务判断或目标不正确时写入 feedback，由 Consumer 生成新 revision。Audit 不替 Consumer 改写提案。

### 9.4 多步 continuation

普通页面每次只允许审计一个新的 effect：

```text
Consumer proposes accepted-prefix + exactly one new operation
  -> Audit reviews
  -> Audit executes only the new operation
  -> runtime reads page/provider/mail state
       terminal -> persist receipt -> task done
       next control needed -> persist awaiting_audit continuation
                              -> new Consumer proposal
```

continuation 必须保持 action、plan、classification、account、message、thread、entry、origin policy 和已接受 prefix 全部不变。每次只能在 prefix 末尾增加一个来自已读取页面的 opaque control；已执行 prefix 永不重放。

`awaiting_audit` 是退订 effect/claim 的领域内部状态，不是新的顶层 task 状态。正常多步 continuation 不消耗“内容反馈最多两轮”的配额；只有 Audit 要求 Consumer 修正业务提案时才记 feedback cycle。

### 9.5 可靠入口

允许的可靠入口依次为：

1. typed provider evidence 证明 DKIM 覆盖两个必要 header 的 RFC one-click；
2. HTTPS `List-Unsubscribe`；
3. 正文中可明确证明 label 或紧邻上下文为 unsubscribe/退订的 HTTPS link。

分类器永远不构造、选择或输出私密 URL。只有 runtime 把 opaque entry reference 解析成受限 private URL。`mailto:`、普通营销链接和仅含关键词的 URL 都不可执行。

## 10. Headless browser 和网络边界

所有退订浏览器必须：

- `headless=true`；
- 使用 Email 专用、owner-only、持久 profile；
- 不读取、复制或挂载主 Chrome profile/cookies；
- 不控制系统 Chrome、应用内浏览器或当前用户 tab；
- 用 profile lock 串行化同一持久 profile；
- 只访问预授权 exact origins 和显式允许的 redirect origins；
- 拒绝 popup、download、新窗口、私网、metadata endpoint、embedded credential 和未授权 origin。

RFC one-click 使用独立临时 context，必须是 exact POST body `List-Unsubscribe=One-Click`，不带 cookie，不能退化为 GET。

专用 profile 已有有效 session 时可继续；出现 password、password manager、MFA、QR、CAPTCHA、新授权 grant、无法确定的 account selection 或 payment 时立即停止。系统不弹出交互浏览器，也不向用户索取 secret。

## 11. Result、恢复与错误语义

### 11.1 结果证据

退订结果必须来自 terminal 可见页面正文、RFC response body 或确认邮件，不使用 LLM 编写成功总结。持久化前：

- 规范化换行并去除控制字符；
- 脱敏完整 URL、query token、cookie、credential 和本地路径；
- 保留原语言和有意义换行；
- UTF-8 最多 16 KiB；
- 保存完整规范化 observation digest。

成功或已完成至少保存：

```text
outcome
completed
result_text
receipt_id
operation
opaque target
provider result identifier
effect digest
observation digest
started_at
completed_at
```

### 11.2 Outcome 投影

| Outcome | Task | Attention |
| --- | --- | --- |
| `unsubscribed` | `done` | 否 |
| `already_unsubscribed` | `done` | 否 |
| `consumer_no_action` | `done` | 否 |
| `no_reliable_entry` | `done` + skipped reason | 否 |
| `login_required` | `done` + skipped reason | 否 |
| `captcha` | `done` + skipped reason | 否 |
| `payment_required` | `done` + skipped reason | 否 |
| browser/provider technical failure | bounded retry，最终 `failed` | 耗尽后是 |
| unresolved in-flight effect | reconciliation-only，最终 `failed` | 耗尽后是 |

登录、CAPTCHA、付款和缺少可靠入口是正常业务边界，不打扰用户。技术故障才进入现有 retry/failed/Attention 生命周期。

### 11.3 不确定 effect

任何可能已发出的外部 effect 在恢复后都必须先进行只读 reconciliation。空白页面、缺失 tab 或旧 journal 不证明 effect 未发出。没有与 exact effect 绑定的 terminal evidence 时，不重放操作，并以固定技术失败结束当前尝试。

服务不新增顶层 `unknown`、`reconciled` 或 `side_effect_state`。effect、continuation 和 receipt 仍是 append-only 领域事实。

## 12. Email 页面和 CEO Agent 可观察性

Email 页面保留四个分区：

1. **已处理**：所有具有 final classification 的邮件、ActionPlan、直接动作状态、退订 task/Audit run、terminal result text；
2. **待反馈**：模型建议、alternatives、confidence、margin、正文预览和附件 metadata，由用户确认类别；
3. **邮件配置**：类别描述、阈值、固定动作、动作参数、启用状态和 config version；
4. **学习**：active/candidate/rejected model、训练时间、样本数量、来源覆盖、验证方法、每类别指标、延迟、artifact digest、晋级/拒绝原因。

“学习”展示和训练不能自动授予动作资格。每个模型和分类必须显示完整带版本 `model_id`；每个动作都能回溯到模型、配置和 ActionPlan。

History 只展示真实 `channel=email` unsubscribe Agent task。确定性动作留在 Email 页面。

Status 显示：

- Email worker PID/健康；
- 各账户最近 scan/cursor/error；
- active model 或“无 active model”；
- pending feedback 数量；
- direct-action backlog；
- unsubscribe task/Audit backlog；
- 最近训练状态。

Attention 只显示真实系统异常；待反馈、样本不足、无动作、Consumer no-action、无可靠入口、登录/CAPTCHA/付款 skipped 不进入 Attention。

## 13. Legacy 与迁移

新 task 只使用 `email_unsubscribe_audited_v2`。`email_unsubscribe_consumer_direct_v1` 的历史 terminal task、run、step、effect 和 receipt 保持只读可见，不删除、不覆盖、不重写为 Audit 历史。

由于 Email 尚未生产启用，实施前必须读库证明没有 non-terminal v1 task。若发现任何 non-terminal v1 task：

- 不自动转换 lifecycle version；
- 不执行或重放旧 effect；
- 保存明确的 `legacy_email_unsubscribe_lifecycle` 技术错误；
- 由新的 classification/ActionPlan 版本在明确重新规划后创建 v2 task。

现有 audited Store 表、effect digest、continuation 和 control binding 优先复用。Consumer-direct 专用 runner、task-bound Consumer 写工具和“zero Audit run”路由应移除或停用；不得平行保留两个可执行退订生命周期。

## 14. 测试策略

### 14.1 文档和政策契约

- 当前 runtime 文档在实现前仍准确描述 Consumer-direct；
- future-state spec 明确方案 A；
- 实现 audited v2 的政策变更必须是独立 commit；
- 所有任务仍遵循 Consumer/Audit，确定性 direct action 明确不是 task；
- auto_reply/SMTP/mailto send 在文档、Skill、API、worker 中全部禁止。

### 14.2 分类与 ActionPlan

- 没有 active model时所有 model prediction 进入待反馈；
- rejected/candidate model不能生成 provider action；
- 用户确认可生成 immutable plan；
- 每个动作独立验证 eligibility；
- threshold 或模型改变不会扩大旧 plan；
- user correction 取消未 claim 的旧动作但不伪造补偿；
- model_id/config_version/action_plan_version 全链路可追溯。

### 14.3 确定性动作

- label、mark_read、archive、move、move-to-Trash；
- 永久删除、EXPUNGE、empty Trash 永远不可达；
- provider readback；
- 写后中断的只读恢复；
- 多账户隔离；
- direct action 不创建 task、Consumer run 或 Audit run。

### 14.4 Audited unsubscribe

- task lifecycle 必须是 audited v2；
- Consumer 只能提案，不能取得写 capability；
- Audit 是唯一执行者；
- no-action 不启动浏览器；
- 初始 OPEN_ENTRY/verified one-click；
- 每个 continuation 只增加一个 operation；
- action/plan/account/message/thread/entry/origin/prefix 不可变；
- 只执行新 operation，不重放 prefix；
- uncertain effect 只 reconciliation；
- terminal result text、receipt 和 digest 一致；
- auto_reply 仍不可创建。

### 14.5 Browser 和 E2E

- loopback only，不访问真实邮箱或退订网站；
- headless、专用持久 profile、主 Chrome cookie 隔离；
- RFC one-click cookie-free POST；
- redirect/origin、popup/download/private-network 阻断；
- login/password/MFA/QR/CAPTCHA/payment 精确停止；
- result text 脱敏、限长；
- E2E 证明 `classification -> ActionPlan -> Consumer -> Audit -> effect -> readback -> done`，且至少有一个 Audit run；
- E2E 同时证明 direct actions 有零 Agent/Audit run。

## 15. 实施和发布阶段

本设计审阅通过后才能编写实施计划。实施仍保持 production-disabled，并按独立提交推进：

1. **生命周期政策 commit**：task lifecycle、architecture/runtime docs、documentation contracts；
2. **audited routing commit**：替换 Consumer-direct 路由，Consumer 只提案，Audit 执行；
3. **continuation/effect commit**：复用并收敛 audited Store/browser 绑定，移除 direct continuation；
4. **UI/observability commit**：显示 Audit run 和 audited result，移除 Consumer-direct 文案；
5. **verification commit**：focused Email、browser、E2E、full suite、frontend typecheck/build 和审查修复。

在模型仍无资格时，不启用真实 model-only 邮箱动作。发布验收分阶段进行：

1. readonly scan/shadow；
2. 用户确认后的受控 label/read/archive；
3. 用户确认后的受控 move/Trash；
4. loopback audited unsubscribe；
5. 另行选择真实订阅样本后执行一次受控退订；
6. 只有未来 user-confirmed source-disjoint 指标过门槛，才逐动作开放 model-only 自动化。

每个 runtime commit 都必须按仓库契约重启 `com.ceo-agent-service.main`，验证新 PID、HTTP health、Email worker health 和实时 backlog；但本分支尚未合并时不得误把开发 worktree 当生产运行目录。

## 16. 非目标

第一版不包含：

- 任何邮件回复、SMTP 发送或 `mailto:` 退订；
- 永久删除、EXPUNGE 或清空废纸篓；
- 附件内容读取；
- 任意网页浏览；
- password/MFA/CAPTCHA/payment 自动化；
- 通用“分析邮件”“判断如何跟进”或开放式 Email Agent task；
- 多模型 ensemble、embedding、LLM/Transformer 分类或向量数据库；
- 账户专属模型或账户级策略覆盖；
- 把 assistant 标签改写为用户 gold feedback；
- 自动反向补偿已经完成的邮箱 effect；
- 第二个 launchd job。

## 17. 完成标准

融合设计只有在以下条件全部满足时才可进入生产激活讨论：

- 用户完成本书面 spec 复核；
- 后续实施计划逐项覆盖本设计；
- current runtime 从 Consumer-direct 收敛为 audited v2，且没有第二条可执行退订路径；
- 确定性动作不创建 task，退订必有 Consumer 和 Audit run；
- 回复、SMTP、永久删除、EXPUNGE 和附件读取不可达；
- 多账户 IMAP、ActionPlan、model/config version、readback 和历史可追溯；
- rejected v3 和所有非权威样本保持不可自动执行；
- focused/full/browser/E2E/frontend gates 全部通过；
- architecture、runtime、Skill、UI 文案和实现一致；
- 服务重启、健康、Email worker 和 backlog 实时验证通过；
- 首次真实写操作仍遵循分阶段受控验收，不以 loopback 或单元测试冒充真实 provider 成功。
