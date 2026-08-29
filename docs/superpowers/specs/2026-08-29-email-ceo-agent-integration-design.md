# Email 分类器接入 CEO Agent 设计

日期：2026-08-29

状态：已完成交互设计确认，等待书面规范复核

关联基础设计：[Email 分类器 MVP 设计](./2026-08-29-email-classifier-design.md)

## 1. 目标

把已经验证的本地轻量邮件分类器接入 CEO Agent Service，使服务能够：

- 在仅有 CPU 的电脑上，以单封邮件 p95 小于 100ms 的速度完成分类；
- 配置并独立扫描多个标准 IMAP/SMTP 邮箱账户；
- 让所有账户共用一套类别、模型、阈值、动作配置和学习数据；
- 对高置信度邮件执行该类别已配置的动作；
- 把中低置信度邮件交给用户确认，并从明确反馈中学习；
- 对标签、已读、归档、移动和移入废纸篓等确定性动作直接执行；
- 对自动回复和多步网页退订自动创建 `channel=email` Agent task；
- 让每次分类、动作和 Agent run 都可追溯到准确的模型版本；
- 在 Email、Status、History 和 Attention 中提供一致的运行可观察性。

本设计遵循“先跑通”的范围。第一版不做跨平台预构建包、复杂发布矩阵、邮箱厂商专属业务逻辑或附件内容理解。

## 2. 已确认的产品边界

### 2.1 Email 页面固定为三个 Tab

Email 页面只包含：

1. **已处理**：已经有最终类别的邮件，以及各后续动作的独立状态；
2. **待反馈**：尚未获得最终类别、等待用户确认模型建议的邮件；
3. **邮件配置**：共享类别与动作配置、当前模型与学习状态、模型训练历史。

不增加独立“学习”Tab。学习信息属于“邮件配置”中的一个区块。

### 2.2 分类确认与 Agent task 分离

用户在“待反馈”中确认类别时，系统只完成以下业务变化：

- 保存最终类别和人工反馈；
- 把邮件从“待反馈”移到“已处理”；
- 把该反馈加入权威训练样本；
- 根据被确认类别的当前配置生成不可变 `ActionPlan`。

分类确认本身不是 CEO Agent task，也不代表用户发出了开放式处理指令。只有 `ActionPlan` 明确包含第一版支持的 Agent 动作时，才自动创建 `channel=email` task。

### 2.3 第一版的动作分工

不使用 Agent 的确定性动作：

- `label`
- `mark_read`
- `archive`
- `move`
- `trash`

使用 Consumer A 和 Audit Agent B 的动作：

- `unsubscribe`
- `auto_reply`

`unsubscribe` 和 `auto_reply` 都由邮件类别配置自动触发，不要求用户逐封确认。第一版不提供“分析附件”“判断如何跟进”或其他通用邮件处理任务。

`trash` 只表示移入废纸篓。第一版不允许永久删除，也不执行 IMAP `EXPUNGE`。

### 2.4 Agent 的邮件内容边界

Consumer A 和 Audit Agent B 可以读取：

- 当前邮件和可取得邮件线程的纯文本；
- 发件人、收件人、抄送人、主题和时间；
- 标准邮件头，包括 `List-Unsubscribe` 和 `List-Unsubscribe-Post`；
- 附件 metadata：文件名、MIME 类型、大小、数量和是否为内嵌附件；
- 最终类别、分类置信度、模型版本、配置版本和动作参数。

Agent 不下载、解析、OCR 或总结附件内容，也不得声称已经读过附件。HTML 邮件在标准化后转换为纯文本，不把原始 HTML 直接作为模型或 Agent 输入。

## 3. 选定架构

### 3.1 进程结构

生产环境继续只安装一个 launchd job：

```text
com.ceo-agent-service.main
  └─ app.service_supervisor
       ├─ existing worker
       ├─ audit-web
       └─ email worker
```

Email worker 是由现有 supervisor 管理的独立子进程，不复用 `DingTalkAutoReplyWorker`，也不把邮件分支塞入 DingTalk worker。Email 子进程异常退出时，supervisor 只重启该子进程，健康的现有 worker 和 audit-web 继续运行。

Email 子进程内部包含彼此隔离的运行循环：

```text
扫描与分类循环
  → 多账户 IMAP 读取
  → 标准化与分类
  → 待反馈或 ActionPlan
  → 确定性动作

Email Agent task 消费循环
  → 只消费 channel=email
  → Consumer A
  → Audit Agent B
  → 自动回复或退订
```

较慢的 Agent run 不得暂停 IMAP 扫描。第一版先在一个 Email 子进程内保持这两个清晰边界；如果将来 Agent 邮件任务数量显著增加，可以在不改变数据库协议的前提下拆为两个子进程。

### 3.2 总体数据流

```text
多个 IMAP 账户
  ↓
只读扫描和标准化
  ↓
共享 CPU 分类模型
  ↓
category + confidence + margin + model_id
  ↓
读取共享类别配置
  ↓
┌─────────────────────────────────────────────┐
│ 达到类别自动处理门槛                         │
│ → 最终类别来源为 model                       │
│ → 生成不可变 ActionPlan                      │
└─────────────────────────────────────────────┘
  │
  ├─ 确定性动作 → provider adapter → 结果读回
  │
  └─ unsubscribe / auto_reply
       → channel=email task
       → Consumer A → Audit Agent B
       → provider / browser → 结果读回

未达到门槛
  ↓
待反馈
  ↓
用户确认类别
  ↓
训练反馈 + 基于当前配置生成 ActionPlan
```

## 4. 多邮箱 Connector

### 4.1 配置入口

连接配置与分类配置分开：

- `Settings / Connectors / Email` 管理多个 IMAP/SMTP 账户；
- `Email / 邮件配置` 管理全局类别、阈值、动作、学习和模型历史。

### 4.2 账户结构

每个账户拥有稳定且不包含密码的 `account_id`：

```text
account_id
display_name
email_address
imap_host
imap_port
imap_tls
imap_username
imap_secret_reference
smtp_host
smtp_port
smtp_tls
smtp_username
smtp_secret_reference
enabled
scan_folders
scan_interval
created_at
updated_at
```

第一版通过标准 IMAP 读取，通过标准 SMTP 回复。厂商差异封装在 connector 内，分类器、页面和 Agent task 不根据 Gmail、钉钉邮箱或其他品牌分支。

密码和应用密码延续当前服务的 `.env` 配置方式，但账户配置只保存 secret 引用。页面只显示“已配置/未配置”，不回显密码。凭据不得进入：

- SQLite 邮件记录；
- `trigger_raw_payload` 或 `channel=email` task payload；
- 分类文本或训练样本；
- Agent prompt、History、Status 或错误详情。

第一版修改连接配置后由主服务重启加载，不实现 connector 热更新。

### 4.3 共享模型与配置

所有启用账户共用：

- 一个类别集合；
- 一个 active model；
- 一套类别描述与阈值；
- 一套固定动作和动作参数；
- 一套自动学习策略；
- 一个权威训练样本集合。

每条分类、反馈、动作、训练样本和 Agent task 仍保存 `account_id`，以支持账户筛选、故障定位和样本贡献统计。第一版不训练账户专属模型，也不提供账户级动作覆盖。

### 4.4 扫描游标和稳定身份

每个账户、每个文件夹独立保存：

```text
account_id
folder
uidvalidity
last_seen_uid
last_success_at
last_error
```

邮件业务身份优先使用：

```text
account_id + RFC Message-ID
```

如果没有合法 `Message-ID`，使用：

```text
account_id + folder + UIDVALIDITY + UID
```

邮件移动后更新当前 provider locator，但不改变稳定业务身份。两个不同账户中出现相同 `Message-ID` 时仍然是两个独立业务对象。

## 5. 分类决策与 ActionPlan

### 5.1 最终分类

达到类别阈值并满足该类别自动处理资格时：

```text
classification_source = model
classification_status = processed
```

未达到门槛时：

```text
classification_status = pending_feedback
predicted_category = 模型建议
```

待反馈邮件不执行动作，也不创建 Agent task。用户确认后：

```text
classification_source = user
confirmed_category = 用户选择
classification_status = processed
```

“已处理”表示邮件已经有最终类别。后续动作可以显示为处理中、已完成、未执行或失败。

### 5.2 不可变 ActionPlan

最终类别确定时保存：

```text
classification_id
action_plan_id
action_plan_version
account_id
category
classification_source
confidence
model_id
config_version
actions
action_parameters
created_at
```

之后修改类别配置不会悄悄改变已有计划。人工确认使用确认时的最新配置生成计划；模型高置信度邮件使用分类时的配置生成计划。

### 5.3 配置校验

- `label` 必须提供一个或多个标签名；
- `move` 必须提供目标文件夹；
- `archive`、`move`、`trash` 同一类别最多配置一个；
- `auto_reply` 必须提供回复指令；
- 同一动作不能重复；
- 永久删除不可配置。

## 6. 确定性动作

确定性动作不创建 CEO Agent task。Email worker 按以下流程执行：

```text
按稳定身份定位邮件
  ↓
读取当前 flags、labels 和 folder
  ↓
已经达到目标状态？
  ├─ 是 → 记录 done，不重复写
  └─ 否 → 执行动作
           ↓
         再次读取 provider 状态
           ↓
         验证目标状态
```

每个动作独立保存：

```text
action_id
classification_id
account_id
action_type
parameters
config_version
status
attempt_count
started_at
finished_at
provider_operation
provider_target
provider_result_id
error
```

直接动作不是 CEO Agent task，其状态限定为 `pending`、`processing`、`done` 和 `failed`。每次尝试追加保存，当前状态只表示该动作现在是否完成。

同一类别可以同时配置确定性动作和 Agent 动作。例如 `mark_read + archive + unsubscribe` 中，前两个直接执行，退订自动创建 Agent task。各动作结果彼此独立。

## 7. 自动回复

### 7.1 触发条件

以下条件同时满足时自动创建 `channel=email` task：

- 最终类别已经确定；
- 该类别启用 `auto_reply`；
- 模型达到类别门槛，或类别由用户人工确认；
- 同一邮件、同一 ActionPlan 尚未创建自动回复任务。

自动回复不要求逐封确认。

### 7.2 配置和 Agent 输入

`auto_reply` 至少配置：

```text
enabled
instruction
language_policy
signature_policy
```

Consumer A 根据邮件和线程纯文本生成具体回复，附件只作为 metadata。若正文只说明“内容见附件”，Agent 可以生成不依赖附件内容的收件确认，但不能评价附件。

### 7.3 发送与防重复

每个自动回复动作生成稳定 outgoing `Message-ID`：

```text
email-action-<action_id>@ceo-agent.local
```

发送前检查 Sent 文件夹。只有未找到相同 outgoing `Message-ID` 时才调用 SMTP。发送后保存 provider 结果并从 Sent 读回目标收件人、主题和 outgoing `Message-ID`。

网络超时或服务中断时，下一次运行先检查 Sent，不得直接重发。只有 provider 稳定标识或 Sent 读回证据成立时，任务才可以记录为发送完成。

## 8. 多步网页退订

### 8.1 自动 Agent 工作流

退订达到触发条件后自动创建：

```text
channel = email
action = unsubscribe
```

Consumer A 从标准邮件头和正文中识别与当前订阅来源明确对应的退订入口，并提出退订目标。Audit Agent B 执行完整网页流程，包括：

- 打开退订 URL 和处理重定向；
- 阅读页面文本；
- 选择明确的退订选项；
- 填写必要字段；
- 执行最终确认；
- 读取完成页面；
- 必要时等待并处理后续确认邮件。

自动退订不要求用户事前确认。

### 8.2 边界和结果

- 只处理与当前订阅来源明确对应的流程；
- 允许使用带签名参数的唯一退订 URL；
- 完整带 token URL 只保存在受限任务输入中，不进入普通 History、训练样本或日志；
- 不订阅新内容、不购买服务、不修改无关账户资料；
- 登录、验证码、CAPTCHA 或付款要求使动作结果成为带明确原因的 `skipped`，不弹出用户确认，也不反复重试；
- 仅点击按钮不能证明成功，必须读取明确完成页面、服务端响应或确认邮件。

浏览器流程中断后，下一次 Agent run 先读取当前页面或订阅状态。已经完成时补记结果；尚未完成时从可验证步骤继续；无法判断时记录当前 run 失败，不猜测也不盲目重复点击。

没有可靠退订入口，或流程要求登录、验证码、CAPTCHA、付款时，Agent 返回现有运行契约中的 `skipped` 和具体原因；任务按无外部动作完成，不进入 Attention。浏览器运行时、认证环境或状态读取本身发生技术故障时才记录 `failed`，并按现有重试与 Attention 规则处理。

## 9. Email task 映射和去重

Agent 动作映射为：

```text
channel = email
conversation_id = <account_id>:<stable-thread-identity>
trigger_message_id = <stable-message-identity>
action = unsubscribe | auto_reply
```

同一封邮件、同一种 Agent 动作、同一个 ActionPlan 只允许一个业务任务。稳定去重键为：

```text
account_id
+ stable_message_identity
+ action_type
+ action_plan_version
```

模型重训、IMAP 重扫或服务重启都不能重新触发已经完成或已经存在的任务。Agent task 继续遵循现有 Consumer A → Audit Agent B → feedback/revision 生命周期；审核修改创建新 revision，不覆盖原 run。

## 10. 学习和模型版本

### 10.1 权威训练样本

训练集只接受：

- 用户确认或修改的类别；
- 用户纠正“已处理”邮件后的类别；
- 明确标记来源的人工种子数据。

模型高置信度预测、动作执行成功、Agent 临时判断和未经人工确认的旧 n8n 标签不能自动成为训练标签。

每条样本保存：

```text
sample_id
account_id
stable_message_identity
redacted_model_text
confirmed_category
label_source
confirmed_at
original_prediction
original_confidence
original_model_id
included_in_model_id
```

### 10.2 自动训练

人工反馈先持久化，再检查重训条件。第一版自动训练条件为：

- 至少累计 5 条未参加训练的新反馈；并且
- 最后一条反馈后空闲 30 秒；或者
- 距离上次训练超过 10 分钟。

“立即训练”按钮不会绕过样本和验证检查。训练使用短生命周期子进程，避免 CPU 训练暂停 IMAP 扫描，不增加新的常驻 launchd job。

### 10.3 可追溯模型标识

每次训练生成新的不可变 `model_id`：

```text
email-tfidf-lr-20260829T214530Z-7f3a91c2
```

模型 metadata 至少包含：

```text
model_id
parent_model_id
model_family
tokenizer_version
feature_version
training_dataset_version
trained_at
training_started_at
training_finished_at
sample_count
new_sample_count
category_counts
account_counts
validation_method
accuracy
macro_f1
per_category_precision
per_category_recall
per_category_f1
prediction_latency_p50_ms
prediction_latency_p95_ms
artifact_sha256
status
promotion_reason
```

每次分类永久保存准确 `model_id`。页面可以从一封邮件追溯到模型版本、训练样本统计、配置版本、动作和 Agent run。

### 10.4 候选模型晋级

训练先生成 candidate，不直接覆盖 active。candidate 必须满足：

1. 模型文件可重新加载；
2. 重新加载后的预测与训练进程一致；
3. 包含触发训练的最新反馈；
4. 输出只包含当前类别协议；
5. CPU 单封预测 p95 小于 100ms；
6. Macro F1 不低于当前 active model；
7. 有足够验证样本的类别 precision 没有明显下降；
8. 模型文件摘要与 metadata 一致。

通过后原子切换：

```text
candidate → active
旧 active → previous
```

未通过的 candidate 保留为 `rejected` 或 `failed` 训练记录。正在处理的邮件继续使用 ActionPlan 中保存的原 `model_id`。

### 10.5 小样本和类别级资格

样本量不足时使用 leave-one-out 或分层交叉验证，并在页面标记“样本量不足，指标仅供观察”。样本足够后使用按时间切分的验证集。

模型晋级不等于所有类别都能自动执行动作。每个类别独立保存：

```text
configured_threshold
validated_precision
validation_sample_count
auto_action_eligible
eligibility_reason
```

没有足够验证样本的类别即使预测分数很高，也进入“待反馈”，不执行自动动作。

## 11. 页面设计

### 11.1 已处理

每封邮件显示：

- 邮箱账户；
- 最终类别和分类来源；
- confidence、margin、完整 `model_id` 和配置版本；
- 每个动作的独立状态；
- 自动回复或退订对应的 Agent task 与 History 链接；
- 最后更新时间和错误。

动作尚未结束时显示“处理中”，不把最终分类和动作结果混成一个状态。

### 11.2 待反馈

显示模型建议、备选类别、confidence、margin、发件人、主题、正文预览、附件 metadata 和类别确认按钮。不显示开放式“处理”入口，也不在确认前执行动作。

### 11.3 邮件配置

内部依次展示：

1. 类别描述、阈值、固定动作和动作参数；
2. 当前完整 `model_id`、训练时间、样本总数、各类别和各账户样本数、Accuracy、Macro F1、p50/p95 延迟；
3. 每个类别的自动处理资格和原因；
4. 自动学习开关、“立即训练”和待训练反馈数量；
5. 模型历史、验证方法、晋级或拒绝原因和上一版本。

## 12. Status、History 和 Attention

### 12.1 Status

Status 增加：

- Email 子进程 PID、启动时间和健康状态；
- 每个邮箱账户的启用状态、最近扫描、游标、延迟和错误；
- active `model_id`；
- 待反馈数量；
- 确定性动作队列状态；
- 自动回复和退订任务数量；
- 最近一次训练状态。

Status 不展示密码、完整退订 URL、邮件正文或附件名称。

### 12.2 History

History 只展示实际创建 Agent task 的 `unsubscribe` 和 `auto_reply`。标签、已读、归档、移动和 Trash 的记录留在 Email 业务页面，不伪装成 Agent run。

### 12.3 Attention

Attention 只投影真正的系统异常，例如：

- IMAP 或 SMTP 认证失败；
- 扫描游标无法恢复；
- 确定性动作失败；
- Agent task 正常重试后仍失败；
- 浏览器或模型运行时故障；
- provider 结果与预期不一致。

以下情况不进入 Attention：

- 正常的待反馈邮件；
- 类别样本不足；
- 没有配置动作；
- 没有可靠退订入口；
- 登录、验证码、CAPTCHA 或付款要求导致退订被明确 `skipped`。

相同故障只有一个当前 Attention 投影。问题恢复后从当前列表消失，历史记录保留。

## 13. 中断恢复

### 13.1 扫描

扫描游标只在邮件读取、标准化和分类记录持久化成功后推进；若邮件已有最终类别，还必须先持久化对应 ActionPlan。待反馈邮件不创建 ActionPlan。游标推进不等待慢动作完成。重扫通过稳定业务身份返回已有记录，不重复反馈、动作或 Agent task。

### 13.2 直接动作

provider 写入后、数据库记录前发生中断时，重试先读取当前 flags、labels 和 folder。已经达到目标状态时补记 `done`，不重复写。

### 13.3 自动回复

SMTP 超时或服务中断后先检查 Sent 中的稳定 outgoing `Message-ID`。有发送证据时补记完成，没有证据时才允许发送。

### 13.4 退订

下一次 Agent run 先读取当前网页或订阅状态。已经完成时补记，未完成时从可验证步骤继续，无法判断时记录 run 失败。服务不新增 `unknown`、`reconciled` 或 `side_effect_state` 状态机。

### 13.5 模型

训练失败不影响 active model。active model 无法加载或连续预测失败时恢复 previous 已验证版本，保存回退原因并产生 Attention。回退不重算旧邮件，也不重新触发旧动作。

## 14. 错误和隐私边界

日志、API 和页面错误不得包含：

- IMAP/SMTP 密码；
- 完整认证配置；
- 带 token 的完整退订 URL；
- 邮件正文全文；
- 附件内容；
- SMTP 原始认证交互；
- Agent 私有运行参数。

错误记录只保留定位所需的最小事实：

```text
account_id
folder
stable_message_identity
action_type
agent_task_id
error_code
sanitized_detail
occurred_at
```

## 15. 测试和验收

### 15.1 自动化测试

分类与学习：

- 不可变、带摘要的 `model_id`；
- 每次分类保存准确模型版本；
- 只有明确标签进入训练集；
- 多账户样本进入共享模型；
- candidate 训练、晋级、拒绝和回退；
- 类别级自动处理资格；
- CPU p95 小于 100ms。

Connector：

- 多个模拟 IMAP/SMTP 账户；
- 单账户认证失败不影响其他账户；
- 账户和文件夹独立 cursor；
- `UIDVALIDITY` 变化恢复；
- 跨账户相同 `Message-ID` 不合并；
- 重扫不重复处理；
- SMTP 使用正确账户。

内容边界：

- HTML 转纯文本；
- Agent 取得正文和线程文本；
- Agent 只取得附件 metadata；
- 附件内容不进入 task；
- 凭据不进入数据库业务记录、训练数据、Agent task 或 API。

确定性动作：

- 标签、已读、归档、移动和 Trash；
- provider 结果读回；
- 写入后中断的幂等恢复；
- 配置冲突拒绝；
- 永久删除不可配置。

自动回复：

- 模型达标或人工确认后自动创建 task；
- 同一 ActionPlan 只创建一次；
- 稳定 outgoing `Message-ID`；
- SMTP 超时后先检查 Sent；
- 页面可追溯到 Agent run；
- task 不包含附件内容。

自动退订：

- 直接退订、重定向、多步表单和确认邮件；
- 已退订、无可靠入口、登录和 CAPTCHA；
- token URL 不泄露；
- 中断恢复不重复提交。

进程和页面：

- supervisor 独立管理 Email 子进程；
- Agent run 不阻塞扫描；
- Email 故障不影响现有 worker 和 audit-web；
- 三个 Email Tab 的状态语义；
- Status、History 和 Attention 的边界。

### 15.2 真实邮箱验收顺序

1. **只读观察**：连接多个账户，只扫描、分类和反馈，不写邮箱；
2. **可恢复直接动作**：在受控邮件上逐项启用标签、已读和归档，并读回结果；
3. **自动回复**：使用受控发件地址验证 Agent、Audit revision、SMTP、Sent 读回和中断防重复；
4. **多步退订**：使用专门测试订阅验证自动浏览器流程、确认邮件、结果记录和 URL 脱敏；
5. **按类别启用**：只有类别级真实验证达到门槛后才开启自动动作。

### 15.3 完成标准

生产融合只有同时满足以下条件才完成：

- 多个 IMAP/SMTP 账户可配置并独立扫描；
- 所有账户共用同一套模型和配置；
- 单封 CPU 分类 p95 小于 100ms；
- 中低置信度邮件只进入待反馈；
- 人工反馈参与学习但不创建无关任务；
- 确定性动作不使用 Agent，并有 provider 读回；
- 自动回复和多步退订自动使用 Agent，且无需用户确认；
- Agent 不读取附件内容；
- 分类、动作和 Agent run 可追溯到准确 `model_id`；
- 重扫、重启和超时不制造重复回复、退订或固定动作；
- Email、Status、History 和 Attention 的语义一致；
- 自动化测试、前端构建和受控真实邮箱验收通过；
- 实验报告、架构、运行机制和配置文档与实现一致。

## 16. 非目标

第一版明确不包含：

- 附件下载、解析、OCR、总结或内容判断；
- 通用“判断如何跟进”或任意邮件 Agent 指令；
- 永久删除和 IMAP `EXPUNGE`；
- 每个邮箱独立模型或类别配置；
- 每个账户独立动作覆盖；
- 邮箱厂商专属分类逻辑；
- connector 热更新；
- 第二个 launchd job；
- 模型预测自动反向生成训练标签；
- 正常待反馈邮件进入 Attention；
- 跨平台 wheel、捆绑 runtime 或完整发布矩阵。
