# Email 文件夹事实源与阶段式分类器设计

日期：2026-09-06
状态：已确认，待书面审阅

## 1. 目的与权威边界

本设计更新 CEO Email Service 的类别体系、训练标签、Agent 与模型切换、邮箱
文件夹映射、important 信号和垃圾邮件退订入口。它补充并在冲突处取代：

- `2026-08-29-email-classifier-design.md` 中的八类固定分类和实时 shadow 假设；
- `2026-08-29-email-ceo-agent-integration-design.md` 中的分类生命周期；
- `2026-08-30-email-unsubscribe-branch-integration-design.md` 中遇到邮箱验证码或
  CAPTCHA 时一律停止的部分。

既有 `email_unsubscribe_audited_v2` Consumer、Audit、隔离浏览器 profile、执行
readback 和结果文字契约继续有效。本设计不重新定义通用任务生命周期，也不开放
自动回复。

目标是：冷启动时由 Agent 可靠处理未读邮件；积累数据后阶段性训练描述增强的
Embedding 分类器；单类别成熟后整理历史邮件；整个模型成熟后接管新邮件，Agent
只在模型拒判或不可用时接管。

## 2. 非目标

- 不读取、OCR、总结或推理附件内容；附件仅提供文件名、MIME、大小、数量和内嵌状态。
- 不增加“订阅”“其他”或“重要”业务类别。
- 不让 shadow model 对每封新邮件实时推理。
- 不让 Model 与 Classifier Agent 并行投票。
- 不引入 TF-IDF 在线降级路径。
- 不让邮箱中新建的任意自定义文件夹自动成为类别。
- 不实现自动回复或 SMTP 回复。
- 本设计不规定 Email 页面视觉布局。

## 3. 类别与 important

### 3.1 初始九个互斥业务类别

第一版提供以下九个类别；每封已归类邮件只有一个类别：

| Key | 名称 | 核心语义 |
| --- | --- | --- |
| `work` | 工作 | 日常经营、客户、项目、产品、技术、销售、交付和普通内部审批 |
| `human_resources` | 人事 | 招聘、候选人、雇佣关系、入转调离、薪资、绩效和员工关系 |
| `legal` | 法务 | 非融资场景的合同、法律权利义务、律师、诉讼、合规和知识产权 |
| `financing` | 融资 | 投资人关系、筹资、尽调、融资法律文件、资本结构和交割 |
| `personal` | 个人 | 本人、家庭、个人身份、个人法律和生活事务 |
| `notification` | 通知 | 验证码、安全状态、日历、系统状态和自动服务提醒 |
| `external_billing` | 外部账单 | 外部主体向本人或公司收费、开票、催款或出具付款凭证 |
| `shopping` | 购物 | 商品或标准消费服务的订单、物流、退款和履约状态 |
| `junk` | 垃圾 | 不希望接收、无保留价值、可疑或无关的推广和邮件 |

`subscription` 正式删除。退订是 `junk` 之后的动作，不是类别。`other` 不存在；
无法可靠分类的邮件保持未归类。`important` 是独立标签，不是业务类别。

用户以后可以创建新的正式类别；新类别创建对应邮箱文件夹、提供完整描述并进入新的
模型输出空间。因此“九类”是初始配置，不是永久硬编码上限。所有成熟和晋升门槛按
当时启用的完整类别集合计算。

### 3.2 关键类别边界

- 我方向客户开票、催收客户回款、内部预算和客户项目结算属于 `work`。
- 外部主体向我们收费、催款或出具费用凭证属于 `external_billing`。
- 文件主要服务于融资交易时属于 `financing`；普通商业合同、诉讼和合规属于
  `legal`。
- 普通劳动合同和人员流程属于 `human_resources`；劳动争议或法律条款审阅以
  `legal` 为主。
- 自动状态属于 `notification`；需要业务讨论的真实项目问题属于 `work`。
- 未经请求且无价值的招聘、融资、媒体和产品推广属于 `junk`，不能因主题词进入
  对应业务类别。
- 是否紧急、重大或需要 Derek 处理不改变业务类别，由 important 独立表达。

### 3.3 描述结构

每个类别配置必须包含：

```text
core     核心定义
include  正向包括项，可以有多条
exclude  明确排除项，可以有多条
```

系统分别计算 `core/include` 与 `exclude` 的 Embedding。动作说明不得写入类别语义，
例如 `junk` 描述不能把“存在 unsubscribe 链接”作为分类证据。

新建自定义类别时，必须同时创建邮箱文件夹并提供上述三组描述；只有名称、没有边界
描述的类别不能进入模型训练。

### 3.4 Important 联合信号

连接器把各邮箱的多个信号归一为：

```text
important_provider = any(trusted provider signals)
important_effective = category != junk AND
                      (important_provider OR important_model)
```

第一版映射：

| Provider | Important 信号并集 |
| --- | --- |
| DingTalk | tag `11`、tag `1`、tag `107`、`PRY_HIGH` |
| Gmail | `STARRED`、`IMPORTANT` |
| Standard IMAP | `\Flagged`、`$Important`（若支持） |
| Microsoft Graph | `flagged`、`importance=high`、`focused` |

主题或正文中的“紧急”“URGENT”以及发件人可任意设置的普通优先级邮件头，只作为
模型输入，不直接形成 provider important。

原始 provider 字段可用于调试和适配器 readback；训练头只接收统一布尔标签。
`junk` 覆盖所有 important 信号，不对进入 Trash 的邮件加星。

## 4. 文件夹是邮件类别的唯一事实源

### 4.1 当前类别

一封邮件当前所在的已绑定文件夹决定其当前类别。数据库中的 Agent 判断、模型预测、
动作计划和历史结果只是审计记录，不能覆盖当前文件夹。

用户把邮件从“法务”移到“融资”后，当前类别立即成为 `financing`，下一轮训练读取
新文件夹，不要求同时更新独立的 `final_category`。

### 4.2 类别配置与文件夹

类别定义、描述、动作和阈值由 CEO Email Service 管理；一个类别只有在每个账号中
创建或绑定了真实 provider folder ID 后，才可在该账号使用。类别创建操作是：

```text
创建配置 -> 创建或精确绑定邮箱文件夹 -> readback -> 激活绑定
```

邮箱中的任意自定义文件夹不会反向创建类别。名称与正式类别完全一致的现有文件夹可
精确绑定；不做模糊名称映射。尤其旧“发票”不能自动映射到“外部账单”。

每个账号保存独立 binding：

```text
account_id, category_key, provider_folder_id, provider_folder_name,
binding_status, last_verified_at
```

受管文件夹被重命名时，以稳定 provider ID 保持绑定并显示当前名称；被删除时暂停该
账号类别写入。存在多个同名候选时标记 `ambiguous`，不猜测。

### 4.3 系统文件夹

```text
Inbox                  未归类入口
Spam / Junk            category=junk
Deleted / Trash        category=junk
Sent                    不参与入站训练
Draft                   不参与入站训练
```

`junk` 不创建普通业务文件夹，固定映射每个账号的系统 Trash。优先使用 provider 的
special-use / well-known folder ID，不硬编码显示名称。

Gmail 通过添加业务类别 label 并移除 `INBOX` 实现与移动等价的效果；保留无关用户
labels。标准 IMAP、DingTalk 和 Outlook 使用其真实 folder move 能力。

## 5. 标签来源与训练数据

### 5.1 Category

训练时按邮件当前文件夹直接生成标签：

- 位于已绑定业务类别文件夹：标签为该类别；
- 位于任何时期的 Spam、Junk、Deleted 或 Trash：标签为 `junk`；
- Inbox 和未绑定文件夹：没有 category 标签；
- Sent 和 Draft：排除。

不根据邮件时间、已读状态、移动来源或数据库历史预测修改上述事实。

Spam 和 Trash 的全部历史邮件进入可用 junk 数据池。训练前去重并按线程、事项、
发件人和模板分组；每轮使用类别平衡采样，避免数量巨大的重复垃圾模板支配训练。

### 5.2 Important

Important 标签来自当前 provider 信号并集，不受文件夹、时间或已读状态限制。按用户
确认的简单闭环，不追踪星标由用户还是系统产生：用户取消或添加星标后，下一轮训练
直接读取当前邮箱状态。

### 5.3 去重与划分

- 邮件稳定 ID 重复只保留一条；重复正文不得跨训练和测试。
- 同一 thread、人工事项组和重复内容组必须位于同一 split。
- 定向补样本与自然收件分布分别统计。
- 测试集只用于最终评估；不能根据测试结果反复选择 split 或阈值。

## 6. Agent 在线处理

### 6.1 扫描门槛

Classifier Agent 只处理：

```text
当前未读
AND 位于 Inbox 或账号明确配置的其他未归类入口
AND 尚无 pending feedback / 已完成分类记录
```

不设“服务上线后收到”的时间门槛。第一次启用时，入口中仍未读的历史邮件也可处理。
Agent 不处理已读邮件，也不扫描正式类别文件夹、Spam、Trash、Sent、Draft 或未配置
自定义文件夹。分类动作本身不把邮件标为已读。

### 6.2 Agent 输出和动作

Classifier Agent 一次输出：

```text
category, important, confidence/uncertainty, reason,
unsubscribe_candidate_index / unsubscribe_url（若适用）
```

Agent 确定时，执行层将邮件移入类别文件夹；important 时在移动后的 locator 上设置
provider 星标并 readback。Agent 不确定时，邮件保持原位置和未读状态，进入
`pending_feedback`。稳定 `(account_id, provider_message_id)` 保证不重复调用 Agent。

Classifier Agent 只作分类决定，不亲自移动、标星或打开退订网页。确定性的 folder
和 flag 动作沿用现有 provider action 路径；退订沿用 Consumer -> Audit 生命周期。

### 6.3 冷启动垃圾自动处理

Classifier Agent 判断 `junk` 后无需逐封人工确认：

- 没有退订候选：移动到系统 Trash；
- 有退订候选：进入退订任务，结束后移动到系统 Trash。

用户从 Trash 恢复邮件后，其当前文件夹自然改变下一轮标签。

## 7. 退订候选与 Agent

### 7.1 代码负责候选发现

候选发现不调用 Agent，也不访问 URL。确定性解析顺序：

1. RFC one-click `List-Unsubscribe-Post` + HTTPS `List-Unsubscribe`；
2. 普通 `List-Unsubscribe` 中的 HTTP(S) 或 `mailto:`；
3. HTML 链接 URL、可见文字、title 或辅助说明中的退订语义；
4. 纯文本中与退订语义相邻的完整 URL。

候选记录来源、顺序、scheme、host、必要上下文和不透明引用。关键词用于发现候选，不
等于授权访问或成功退订。

### 7.2 Classifier Agent 可以同步选择候选

Agent 已经进行冷启动分类时，可以在同一次分类结果中输出 `unsubscribe_url`，但该值必须与代码提取
的某个候选完全一致，不能构造、补全或改写 URL。系统校验后主要持久化候选索引、
来源和摘要；执行时从授权邮件材料重新解析完整 URL。

模型晋升后，`junk` 预测直接使用代码候选，不为候选发现调用 Classifier Agent。

### 7.3 退订执行和验证挑战

有候选的 junk 邮件创建独立 Unsubscribe Agent task。Agent选择可靠入口、处理网页
多步交互并由 Audit 审核持久化动作；最终保存网页或 RFC 响应的真实结果文字。

Agent可以：

- 使用隔离持久 profile 中已有的有效 session；
- 处理普通表单、偏好选择和多步确认；
- 从已经接入的授权邮箱读取与当前站点、收件人和时间窗口匹配的邮件验证码，并自动
  填写；验证码不得写入日志、训练集或结果文本；
- 点击并等待能正常自动通过的网页安全检查。

Agent不得接入 CAPTCHA 破解服务、伪造真人或读取主 Chrome cookies。出现真实图片、
滑块、拼图、音频等真人 CAPTCHA 时，Agent先做正常交互尝试；不能通过则任务进入
`needs_human`，在 Email 退订流程中交给用户完成，完成后允许继续。短信、TOTP、二维码
或密码只有在对应设备连接或安全凭据已明确配置时才能自动完成，否则同样进入
`needs_human`。

## 8. 阶段式 Embedding 模型

### 8.1 架构

正式候选为 `jinaai/jina-embeddings-v5-text-small` 邮件向量加两个输出头：

```text
邮件 embedding -> 当前启用类别集合的 category MLP
邮件 embedding -> important binary head
```

Category 分数为：

```text
score_c = MLP(x)_c + alpha * positive_similarity_c
                       - beta * exclusion_similarity_c
```

第一版 `alpha=0.8`、`beta=0.5`，在训练集内部学习并限制到 `[0, 2]`；初期所有类别
共享参数，测试集不能参与选择。

邮件输入包含正文、引用、结构化地址和附件 metadata。邮件向量按规范化输入 hash、
input schema version 和 embedding model revision 缓存；修改描述只重算少量描述向量，
升级输入或 embedding 模型后不能混用旧缓存。

### 8.2 性能

- 常驻 GPU4 新邮件主路径 P95 目标小于 500ms，100ms 是理想目标；
- 缓存后的历史重分类和分类头 P95 小于 100ms；
- 每批最多 8 封，微批等待最多 50ms；
- 单次 embedding 请求技术超时为 2 秒；
- 记录排队、网络、embedding、分类头和总耗时。

GPU4 冷态或故障不计作满足 500ms。模型晋升后遇到超时或服务不可用时调用 Classifier
Agent fallback，不引入 TF-IDF 在线结果。

### 8.3 阶段性训练

Shadow model 不对新邮件实时推理。触发条件：

- 每累计 50 封新增或变化的最终标签；
- 用户手动触发；
- 类别描述发生明确变化；
- 首次冷启动达到最低样本量。

每轮固定：数据快照、description version、split、seed、依赖、模型参数、模型文件
校验值和评测报告。Agent可根据重复混淆提出描述候选；新描述只有随完整训练和测试通过
的新模型版本生效，不能直接修改 active model。

## 9. 成熟、历史归类和实时晋升

### 9.1 单类别历史门槛

一个类别可用于历史归类需要：

- 在独立测试集的自动接受部分 precision >= 95%；
- 至少 20 个接受命中；
- 覆盖至少 10 个独立 thread / 事项组。

Shadow model 只扫描历史未归类邮件；只有 top-1 是已成熟类别且超过该类别阈值时才
移动。不得使用成熟的第二名覆盖未成熟的第一名。

业务类别文件夹中的邮件已经具有事实标签，不需历史重分类；Inbox 和配置的未归类
入口中的已读邮件不调用 Agent，可由达标 shadow model 阶段性归类。

### 9.2 整体晋升

整个模型晋升为新邮件主模型需要：

- 当前启用的全部类别都满足单类别门槛；
- important 自动接受部分 precision >= 95%；
- 连续两个候选模型版本通过；
- 输入、描述、embedding 和分类头版本一致；
- 历史归类没有未解决的系统性错误。

晋升前在线主流程为 `Classifier Agent -> User`。晋升后为：

```text
Active Model accepted -> 执行动作，不调用 Agent
Active Model rejected / failed -> Classifier Agent fallback
Classifier Agent uncertain -> User feedback
```

Model 与 Agent 不并行运行或投票。500ms 只约束正常模型主路径，不包含 Agent、邮箱
移动、Audit、退订或用户反馈时间。

## 10. 动作、失败和幂等

- 移动成功后使用新 locator 加星；移动和加星分别保存状态和 readback。
- 文件夹创建或移动失败时不能宣称分类成功；保留可重试状态。
- 移动成功、加星失败时不重复移动，只重试目标 locator 的加星。
- Agent不确定时不移动、不加星、不退订、不进 Trash。
- `junk` 无候选时直接 Trash；有候选时先完成、跳过或交接退订，再 Trash。
- CAPTCHA 或认证交接不是分类失败，保留退订 continuation 和可观察结果。
- 相同稳定邮件身份、动作计划和退订入口必须幂等，不能重复移动或重复退订。
- 当前类别读取失败时返回 unavailable，不能用数据库旧预测伪装当前文件夹事实。

## 11. 验收测试

至少覆盖：

1. 初始九类互斥协议、动态新增类别，以及删除 `important`、`subscription` 和
   `other` 类别；
2. 类别描述正向和排除向量实际参与分数；
3. DingTalk、IMAP、Gmail、Microsoft important 联合信号归一和 junk 覆盖；
4. folder ID 到类别的唯一事实映射、用户移动后的标签变化；
5. Spam / Trash 全历史 junk，Sent / Draft 排除，重复和 thread 泄漏拒绝；
6. Agent只处理配置入口中的未读邮件，已读邮件不调用 Agent；
7. pending feedback 防止同一未读邮件重复调用 Agent；
8. 每 50 条变化触发快照训练，shadow 不实时推理；
9. 单类别 95%/20/10 历史门槛和整体连续两版晋升；
10. Active Model accepted 不调用 Agent，拒判、超时和失败才 fallback；
11. 退订候选纯代码提取，Agent URL 必须匹配候选；
12. 邮件验证码自动续接，CAPTCHA/认证 `needs_human` continuation；
13. 移动后加星、部分失败重试、provider readback 和幂等；
14. 模型文件保存重载一致、版本和缓存键不可混用；
15. 常驻 GPU 主路径 P50/P95/P99、500ms SLO 和故障 fallback。

## 12. 实施分期

后续实施计划应拆成独立可验收阶段：

1. 类别协议、描述、folder source of truth 和 provider important adapter；
2. Agent 未读分类与文件夹动作；
3. 训练数据快照和阶段触发；
4. Jina 双头模型、描述相似度和离线评测；
5. 单类别历史归类；
6. 整体模型晋升和 Agent fallback；
7. 退订候选、邮箱验证码和 CAPTCHA 用户 continuation；
8. 多邮箱端到端验证和运行观测。

任何涉及 Audit 生命周期的新变化必须作为独立提交；本设计复用现有 audited unsubscribe，
不把新的审计规则混入分类器、文件夹同步或模型训练实现。
