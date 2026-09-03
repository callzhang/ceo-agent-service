# Email 分类器 MVP 设计

## 状态

分类、反馈学习和固定动作边界已在对话中确认；当前模型选型已根据
修正后的真实邮箱实验更新。本文只定义 MVP 的分类、反馈学习和固定
动作边界，不包含 CEO Agent 运行时实现计划。已确认：`label`、`mark_read`、
`archive`、`move`、`trash` 是直接动作，不创建 Agent/Audit 任务；
当前分支的配置和 runtime 禁用 `auto_reply`：它不能从配置、分类结果或人工确认生成 task，
也不连接 SMTP；Email 尚未部署，也未在生产启用。历史 contract 仍保留用于兼容读取。本文中的 Consumer-direct 退订描述只记录
当时的实现历史：`unsubscribe` 使用 Consumer-direct。当前和后续实施权威已由获批设计
[`2026-09-02-email-ceo-agent-audited-fusion-design.md`](./2026-09-02-email-ceo-agent-audited-fusion-design.md)
取代，`unsubscribe` 目标生命周期为 `email_unsubscribe_audited_v2`：Consumer A 提案、
Audit Agent B 审核执行和外部 readback；这项指向不表示已经部署或在生产启用。

## 背景与目标

现有邮件过滤方案来自两个 n8n 工作流：

- `/Users/derek/Downloads/gmail filter.json`
- `/Users/derek/Downloads/Ding email classifier.json`

现有方案通过 LLM 直接判断邮件类别并驱动后续节点。目标是将核心判断收敛为一个轻量的、本地 CPU 可运行的多分类器，并保留以下产品能力：

- 一封邮件只产生一个互斥类别；
- 类别确定后，后续动作由配置固定决定；
- 模型无法高置信度判断时，把邮件暴露给用户；
- 用户选择的类别自动进入训练集；
- 支持标签、描述、自动归档、自动退订和 Trash 等配置；Email 回复当前不在可配置动作中；
- 本地分类路径的目标是 p95 小于 100ms；
- MVP 先在当前开发环境跑通，不做跨平台安装包和完整发布矩阵。

## 现有 n8n 兼容性

两个输入工作流的输出协议和动作边界不同，不能直接复用为同一个模型
类别协议：

| 工作流 | 现有类别/输出 | 现有动作节点 | 兼容处理 |
| --- | --- | --- | --- |
| `gmail filter.json` | `junk`、`shopping`、`billing`、`notification`、`subscription`、`unknown` | Gmail delete、label、mark-as-read、`derek-unsubscribe-email`、发送消息 | `unknown` 映射为拒判；其余类别进入统一八类协议 |
| `Ding email classifier.json` | `junk`、`important`、`receipt`、`work` | IMAP move-to-trash、move-to-发票、flags/labels、Telegram/钉钉通知、`derek-unsubscribe-email` | `receipt` 只作为候选映射到 `billing`，必须保留 `label_source=n8n_seed`，不能直接当人工确认 |

现有工作流还暴露出三个需要在迁移时显式处理的差异：

1. Gmail 使用 message/label ID，钉钉 IMAP 使用 UID/mailbox path；统一
   classifier 输出只携带稳定的 `message_id` 和分类结果，连接器适配器
   负责保存各自的 provider locator。
2. 两个工作流都有直接删除、移动、通知和退订节点；新的 classifier
   core 不调用这些节点，只输出分类和不确定性，动作执行必须由后续
   配置层决定。
3. Gmail 的 `unknown` 和钉钉方案缺失的 `personal`/`shopping` 不能
   被当成负类；它们分别表示拒判或尚未覆盖的训练类别。

迁移顺序应为：先以只读方式生成统一分类结果，再由适配器做 dry-run
动作计划；在用户确认类别映射和阈值前，不替换现有 n8n 动作工作流。

## CEO Agent 集成边界（外部写入路径已确认）

### 当前服务能复用什么

当前 CEO Agent 已有 `ceo-mail-review`，能够在收到邮件卡片后解析完整邮件/线程、读取关联材料，
并在用户明确授权时提出邮件回复候选；`reply_attempt` 也已经保存 mailbox、message ID、subject、
回复内容和邮件动作结果。这部分适合承接“需要 Derek 关注或处理”的邮件。

但当前运行时还没有邮件批量扫描器或 `email` channel worker：`reply_tasks` 虽然保存了通用 channel，
实际 `DingTalkAutoReplyWorker` 的领取、channel gate 和上下文构造仍以 DingTalk/WeChat 为主；现有
邮件 Skill 也不是批量过滤和退订协议。因而不能只把 `channel` 改成 `email` 就声称完成集成。

### 建议的数据流

```text
IMAP 只读连接器
    -> NormalizedEmail + provider locator
    -> EmailClassifier.predict()
    -> EmailDecision（分类、置信度、模型版本、动作计划）
       ├─ 低置信度/需确认 -> Email 页面“待反馈”（status=pending_feedback，action_plan=None）
       ├─ 用户确认类别     -> 反馈集 + 后台全量重训
       └─ 达到消息阈值且类别具备自动动作资格 -> 形成固定动作计划，先 dry-run，再进入执行闭环
```

分类器只输出分类事实和配置对应的动作计划，不直接连接 CEO Agent session，不直接发送邮件，不直接
删除/移动邮件，也不生成退订 URL。标准化对象至少保留：

- `provider`、`mailbox`、稳定的 `message_id`/`thread_id` 和 IMAP mailbox/UID 等 provider locator；
- `category`、`confidence`、`margin`、`model_version`、`label_source`；
- 触发该分类所需的有限邮件元数据，以及 `List-Unsubscribe` 的原始入口引用；
- 当前配置版本和动作计划版本。

原始密码、应用密码和完整认证配置不进入 `trigger_raw_payload`、训练样本或 Agent prompt。分类文本继续
使用当前脱敏规则；provider locator 只用于连接器后续读取和动作定位。

### 与现有任务生命周期的接法

对需要用户决定的邮件，后续实现可以把邮件线程映射为稳定的
`channel=email`、`conversation_id=<provider>:<mailbox>:<thread-or-message>`、
`trigger_message_id=<provider-message-id>`，再由邮件专用 adapter 构造 `AgentTaskContext`。但是这
需要单独实现 Email connector/task adapter、邮件 channel gate 和扫描 cursor；不能复用
`DingTalkConversation` 或在 DingTalk worker 中加入邮件分支。

注意力任务的职责是让 CEO Agent 使用完整邮件证据形成“保留/处理/询问”的候选；用户明确选择的类别
则直接写入 classifier feedback，不要求 LLM 重新猜标签。这样可以把“模型学习”与“CEO Agent 的
业务处理”分开，也避免每一封低置信度邮件都被误当成回复请求。

### 固定动作与 CEO Agent 的关系

`ActionPlan` 是对一个已处理分类和其固定配置的不可变执行授权快照，动作分为两条路径：

- `label`、`mark_read`、`archive`、`move`、`trash` 由邮件执行层按稳定 locator
  直接执行，不创建 Agent/Audit 任务；
- `auto_reply` 是保留的历史 Email task contract，但当前 Email 配置和 runtime 禁止它；
  它不创建 task、不连接 SMTP。`unsubscribe` 是当前唯一的 Email Agent task 动作，使用
  Consumer-direct 并完成 provider effect/readback。

因此第一版采用以下边界：

1. 分类器负责毫秒级分类、拒判和生成不可变的 `ActionPlan`；
2. 邮件执行层只消费 `direct_actions`，不得扩大或改写计划中的动作和参数；
3. 后续 Agent 路由只消费当前允许的 `agent_actions`：`unsubscribe` 应用
   Consumer-direct；`auto_reply` 当前禁用；
4. 只有消息置信度达到类别阈值且该类别 `auto_action_eligible=true` 才形成计划，
   但不能授权计划之外的动作。

### 建议的分阶段接入

| 阶段 | CEO Agent 侧变化 | 外部动作 |
| --- | --- | --- |
| A. 观察 | 只读扫描，分类结果和低置信度样本存本地；在 Email“待反馈”查看 | 不写邮箱 |
| B. 决策 | 用户在 Email“待反馈”中选择八类之一；反馈回 classifier store | 不写邮箱 |
| C. Dry-run | 生成直接动作和 Agent 动作的拟执行计划，展示目标与原因 | 不执行 |
| D. 直接动作 | 按类别配置逐项开启 label/mark_read/archive/move/trash | 不创建 Agent/Audit 任务，记录 provider 结果 |
| E. Email task 动作 | 当前只允许按门槛开启 unsubscribe；auto_reply 关闭 | 仅创建 Consumer-direct 工作并完成 readback；不连接 SMTP |

本节是集成边界设计，不代表直接动作执行器或 Agent 动作路由已经上线。
直接动作不经过 Audit B；`unsubscribe` 进入 Consumer-direct 生命周期并执行 provider
readback。`auto_reply` 当前不进入任何 Email runtime 生命周期。

### CEO Agent Email Adapter Contract（第一版待实现）

结合当前 `docs/architecture.md` 和 `docs/runtime-mechanism.md` 的任务契约，
第一版不把每个 classifier review item 直接写入 `reply_tasks`。两者分属不同
职责：classifier queue 负责类别确认和学习，CEO Agent task 负责需要业务理解、
处理或回复的邮件。推荐的提升路径是：

```text
Email review item
    -> 用户确认类别
    -> 仅学习：FeedbackStore + queue resolved
    -> 用户明确需要处理，或类别为 important 且配置要求暴露
    -> EmailTaskAdapter 创建 CEO Agent email task
```

当 review item 被提升为 CEO Agent 任务时，adapter 使用以下稳定映射：

```text
channel             = "email"
conversation_id     = "<provider>:<mailbox>:<thread-or-message>"
trigger_message_id  = "<provider-stable-message-id>"
```

`conversation_id` 和 `trigger_message_id` 必须由 provider locator 原样生成，
不能由主题、发件人或正文推导；同一三元组
`(channel, conversation_id, trigger_message_id)` 只允许一个业务队列任务。
仅当前允许的 `unsubscribe` Consumer 尝试产生独立 `agent_run`；`auto_reply` 当前禁用。
当前业务结果继续由对应的 `reply_attempt` 投影，
不能把 classifier queue 的 `pending/resolved`
状态写进 CEO Agent 的 `running/done/failed/needs_human` 状态列。

adapter 给 `AgentTaskContext` 的最小字段应为：

- `channel="email"`、稳定 `conversation_id`、稳定 `trigger_message_id`；
- provider、mailbox、thread/message locator，以及邮件时间、发件人和主题等必要元数据；
- `trigger_text` 只放经脱敏的分类摘要或用户明确的问题，完整正文由
  `ceo-mail-review` 和 `dingtalk-mail` 按 locator 重新读取；
- `materials` 中声明准确的邮件读取入口，不把原始密码、应用密码或完整认证配置放入
  `trigger_raw_payload`；
- `required_reviewed_skills` 至少指向 `ceo-mail-review`；`unsubscribe` 由 Consumer
  读取并执行退订 Skill；`auto_reply` 当前没有执行路径。

直接动作不进入 Consumer A；邮件执行层只能执行计划中的 `direct_actions`。Consumer A
只处理当前允许的 `unsubscribe`，不能重新分类或扩大动作；它由 Consumer 对邮件和 thread
做最终判断后直接执行已冻结计划。`auto_reply` 当前禁用。
用户确认类别时，系统应直接写 classifier feedback，不要求 Consumer A 再次猜类别。

当前没有 Email reply 的 Audit B 输入。若未来重新开放 `auto_reply`，必须另行确认
Consumer → Audit 策略。`unsubscribe` 不创建 Audit run 或 revision，但同样必须保存完整步骤、
terminal result text、receipt 和 observation digest。当前业务按既有 lifecycle 保存 provider 返回的最小
`operation`、`target` 和稳定 result identifier；若配置仍是 dry-run，则返回
`dry_run`，不能伪造 `executed`。分类概率、动作阈值和 dry-run 结果不能替代外部执行后的
provider readback。`unsubscribe` 不创建 Audit run 或 revision，但同样必须保存完整步骤、
terminal result text、receipt 和 observation digest。

第一版的可验证验收边界是：

1. 同一邮件重复扫描不会产生第二个 classifier review item；
2. 仅确认类别不会产生 `reply_task`；
3. 明确要求处理的邮件才会按上述映射创建 `channel=email` 任务；
4. 直接动作不创建 Consumer/Audit 任务，且只能执行计划内参数；
5. `auto_reply` 能关联到 Audit B 的审核/执行记录；
6. `unsubscribe` 能关联到 Consumer-direct 的步骤、terminal receipt 和 observation；
7. 两个 Email task 动作都遵循各自的 lease、幂等和 result readback 契约；
8. 外部动作未启用时只保存 dry-run 计划，不改变邮箱；
9. provider 读取失败进入 `failed`，不能伪装成 `needs_human` 或分类 `unknown`。

直接动作执行器和两个 Agent 动作的 CEO Agent 路由仍未实现；
不修改 `reply_tasks` schema、worker channel gate、Attention 页面或 launchd 配置。

## 设计结论

经过修正发件人域名/精确发件人预处理后的 73 条真实邮箱实验，MVP 的
默认模型更新为一个 TF-IDF + balanced Logistic 互斥多分类器：

```text
邮件结构化字段和正文
    -> 中文轻量分词及字段 token 化
    -> TF-IDF word unigram 稀疏表示
    -> balanced LogisticRegression
    -> softmax 多分类
    -> 类别级置信度阈值
    -> 通过阈值且类别具备自动动作资格才形成固定动作计划，否则请求用户决策
```

Logistic 被选为当前默认方案的原因是：在修正后的数据上，它的 Macro F1
明显高于 fastText，概率和 margin 容易取得，批量重训在 CPU 上只有毫秒级，
并且能直接序列化为一个很小的本地模型。当前实验中的五折 aggregate
结果为约 65.75% Accuracy / 65.77% Macro F1；这仍是临时标签上的方向性
结果，不能授权自动动作。

fastText 保留为实验对照：它的推理速度优秀，也支持词 n-gram、字符子词、
概率预测和量化，但在当前小样本邮箱切片上分类质量较低且多组参数出现
`Encountered NaN`。官方实现参考：[fastText 文本分类教程](https://fasttext.cc/docs/en/supervised-tutorial.html)、[fastText Python API](https://fasttext.cc/docs/en/python-module.html)

HashingVectorizer + SGD Logistic 保留为在线学习对照；当前实验显示其
`partial_fit` 流式质量和置信度不稳定，因此不做线上 ensemble，也不直接
用它替代批量重训。Linear SVM、TF-IDF Logistic 和 fastText 的比较应在
用户自己的时间顺序数据上完成，而不是仅引用公开数据集的结果。

当前已经实现了独立、无副作用的 classifier core；它支持模型版本标识、
模型文件原子替换，以及独立的配置/决策契约，但邮箱动作和 CEO Agent
runtime 仍未实现。自动动作的
开放仍等待更多人工确认数据及时间 holdout；直接动作与 Agent 动作的边界已经确认：
`label`、`mark_read`、`archive`、`move`、`trash` 不创建 Agent/Audit 任务；
`auto_reply` 当前禁用；`unsubscribe` 进入 Consumer-direct，并执行 provider readback。

## 类别体系

MVP 采用八个互斥训练类别：

| 类别 | 定义 | 默认动作方向 |
| --- | --- | --- |
| `important` | 明确需要用户尽快关注或处理 | 保留、打标签、通知用户 |
| `work` | 与日常工作有关，但不要求立即处理 | 保留、打标签 |
| `personal` | 真实个人关系或个人生活邮件 | 保留、打标签 |
| `notification` | 验证码、安全提醒、系统状态等时效通知 | 打标签，默认保留 |
| `billing` | 个人发票、账单、付款凭证 | 打标签、归档 |
| `shopping` | 订单确认、物流、退款和购物状态 | 打标签、归档 |
| `subscription` | 用户不希望继续接收的批量订阅 | 尝试退订、归档 |
| `junk` | 广告、营销、钓鱼、失效通知或无价值邮件 | 移入 Trash |

`unknown` 不是训练类别。它表示分类器的 top-1 结果没有达到该类别的自动处理阈值，或该类别当前
`auto_action_eligible=false`，属于拒绝自动决策的状态：

```text
top-1 类别未达到类别阈值，或类别 `auto_action_eligible=false`
    -> 暂不执行类别动作
    -> 暴露给用户
    -> 用户选择八个真实类别之一
    -> 保存为人工训练样本
```

`subscription` 的定义不是“所有 newsletter”，而是“用户不希望继续接收且可以退订的订阅”。用户希望保留的户外兴趣邮件不能仅因为包含 `unsubscribe` 就自动退订。

## 分类输入

分类器只接收一个标准化邮件对象，但将结构化字段编码为带命名空间的 token，以避免 From、Subject、正文中的相同词被混为一谈：

```text
__from_domain__github.com
__from__HASH_<sender-hash>
__to_direct__true
__has_unsubscribe__false
__auto_submitted__true
__has_attachment__false

__subject__审核
__subject__请求
__subject__合并

审核 请求 合并
请求 你 审核 一个 合并 请求
```

至少使用以下字段：

- 发件地址和发件域名；
- 收件人是否直接包含用户；
- Subject；
- 清洗后的纯文本正文；
- `List-Id`；
- `List-Unsubscribe` 是否存在；
- `Auto-Submitted`；
- 是否群发；
- 是否有附件；
- 用户是否曾回复该发件人或线程。

正文处理规则：

- HTML 转为纯文本；
- Subject 完整保留；
- 正文最多保留 12KB；
- 超长正文保留前 8KB 和后 4KB；
- 附件只保留文件名和 MIME 类型，不下载附件参与分类；
- 引用历史和重复正文尽量折叠。

中文使用 jieba 精确模式；英文按空格和标点切分。分词器常驻进程并只初始化一次。fastText 官方说明其 token 化基于 ASCII 空白字符，本身不理解中文词边界，因此中文需要在输入前进行边界处理。[fastText Python API 的预处理说明](https://fasttext.cc/docs/en/python-module.html)

## fastText 历史对照参数

fastText 仅作为历史对照和后续实验候选，曾使用以下参数作为起点：

```text
loss       = softmax
dim        = 32
wordNgrams = 2
minn       = 2
maxn       = 4
bucket     = 200000
minCount   = 1
epoch      = 25
thread     = 2
```

参数含义和取值依据：

- 互斥分类使用 `softmax`；
- `wordNgrams=2` 识别“付款通知”“会议邀请”“验证码”等短语；
- 中文两个汉字的词很常见，因此字符子词从 2 开始；
- `dim=32` 先控制模型大小，数据足够时再比较 64；
- `minCount=1` 保留低频但有价值的发件人、域名和产品名；
- MVP 先不使用预训练中文词向量；
- `thread` 只影响训练，不影响分类语义。

这些不是当前默认模型的生产参数。fastText 在修正后的数据上仍出现训练
数值不稳定；如重新评估，最终参数仍必须通过时间顺序验证集选择。

## 初始训练集

现有 n8n 分类结果可用于冷启动，但要区分标签来源：

```json
{
  "message_id": "abc123",
  "category": "subscription",
  "label_source": "n8n_seed",
  "label_confidence": "provisional"
}
```

用户明确选择或纠正的标签是确认数据：

```json
{
  "message_id": "def456",
  "category": "important",
  "label_source": "explicit_user_feedback",
  "label_confidence": "confirmed"
}
```

建议的冷启动顺序：

1. 优先使用最近邮件；
2. 优先确认 `important`、`subscription`、`junk` 和 `billing` 等影响动作的类别；
3. 每类尽量获得 20–50 封样本；
4. 样本不足的类别可以预测，但不开放高风险自动动作；
5. n8n 标签可以用于初始模型，但用户确认数据的权重高于 n8n 标签；
6. 不为了平衡而大量复制少数类样本。

用户确认数据应作为正式训练集长期保留。n8n 种子数据可以带权重，或在首次训练后逐步被人工标签替代。

## 预测、拒判和动作配置

模型输出一个 top-1 类别、分数和可选的 top-k 结果：

```json
{
  "category": "subscription",
  "confidence": 0.963,
  "alternatives": [
    {"category": "junk", "confidence": 0.021},
    {"category": "notification", "confidence": 0.009}
  ],
  "model_version": "20260829-103000"
}
```

每个类别拥有独立阈值和动作配置：

```yaml
categories:
  important:
    description: 需要我尽快关注或处理
    label: AI/重要
    threshold: 0.85
    actions:
      expose: true
      notify: true
      archive: false
      trash: false
      unsubscribe: false
      auto_reply:
        enabled: false
        template: null

  subscription:
    description: 我不希望继续接收的批量订阅
    label: AI/已退订
    threshold: 0.98
    actions:
      expose: false
      notify: false
      archive: true
      trash: false
      unsubscribe: true
      auto_reply:
        enabled: false
        template: null

  junk:
    description: 无价值、广告、营销或钓鱼邮件
    label: AI/垃圾
    threshold: 0.995
    actions:
      expose: false
      notify: false
      archive: false
      trash: true
      unsubscribe: false
      auto_reply:
        enabled: false
        template: null
```

当前 classifier workspace 还实现了无副作用的 `rank_for_review`：它对每封
邮件只预测一次，按低 top-1 probability、低 margin 和高 entropy 排序，
返回待人工决策的候选及诊断信号。它不持久化原始邮件，不执行类别动作，
也不改变 CEO Agent 的任务路由。

决策只有一个分类器：

```python
label, confidence = model.predict(email)
threshold = config[label].threshold

if confidence >= threshold:
    execute_fixed_action(label)
else:
    enqueue_user_decision(email, prediction=(label, confidence))
```

初始阈值只是冷启动值，不能直接视为校准后的概率。阈值应使用用户自己的时间验证集反推，尤其要单独评估 `subscription` 和 `junk` 的误操作风险。早期邮件过滤研究和 TREC Spam Track 都强调了正常邮件误杀与垃圾邮件漏判之间的不对称代价。[A Bayesian Approach to Filtering Junk E-Mail](https://www.microsoft.com/en-us/research/wp-content/uploads/1998/01/junkfilter.pdf)、[TREC Spam Track](https://trec.nist.gov/data/spam.html)

## 退订边界

分类器只判断类别，不生成退订链接。退订执行器按以下顺序寻找真实入口：

1. 邮件头 `List-Unsubscribe` 中的 HTTPS 地址；
2. 正文中明确标记为 unsubscribe/退订的 HTTPS 链接；
3. 只有 `mailto:` 时，第一版不自动发送退订邮件；
4. 找不到可信链接时，不猜 URL，只记录未找到入口并执行其余低风险动作。

因此，不会因为模型输出 `subscription` 就凭空构造 URL，也不会把 `unsubscribe` 这个词本身作为退订依据。

## 反馈与重训

用户反馈记录至少包含：

```json
{
  "message_id": "abc123",
  "predicted_category": "notification",
  "predicted_confidence": 0.61,
  "selected_category": "important",
  "source": "explicit_user_feedback",
  "corrected_at": "2026-08-29T10:35:00-07:00",
  "model_version": "20260829-103000"
}
```

训练流程：

```text
保存用户纠正
    -> 30 秒 debounce，合并连续操作
    -> 新建 candidate 模型
    -> 运行最小验证
    -> 通过则原子替换 active 模型
    -> 失败则继续使用旧模型
```

数量触发条件为：新增人工标签达到 5 条，或距离上次训练超过 10 分钟，或用户停止操作 30 秒，满足任一条件即可触发后台重训。

在修正后的 73 条脱敏样本上，按时间顺序模拟“前 15 条训练、后 58 条
逐封到达；只有低于阈值的邮件获得模拟人工确认并在下一封前重训”。阈值
`0.20/0.25/0.30` 对应的人工率为 `34.48%/67.24%/79.31%`，自动覆盖率为
`65.52%/32.76%/20.69%`，自动 precision 仅为
`65.79%/84.21%/83.33%`。这些标签仍是临时标注，不能当作线上准确率；但
它们已经足以说明当前模型在任何测试阈值下都没有达到自动动作的 precision
门槛。因此第一版继续保持 review-only + dry-run，反馈即时保存，重训按
批次 debounce，不做逐封全量重训。

按类别做五折 out-of-fold action-readiness recheck 后，当前没有类别达到
可开放条件：`important` 没有任何阈值达到 95% precision；满足目标的
`billing/junk/notification/subscription/work` 切片最多只有
`1/6/5/9/1` 条，远低于至少 20 条的候选证据量。尤其 `billing` 只有 3
条样本，五折结果还会触发样本不足 warning。因此这些 100% 的小切片不
能被解释为真实 precision，所有动作继续只能生成 dry-run。

另外用 73 条样本做了重复分层学习曲线：固定最新 22 条为时间 holdout，
从较早 51 条中分别抽取 15/20/30/40 条训练集，每个规模重复 50 次。
word-unigram TF-IDF + balanced Logistic 的 accuracy 均值分别为
`41.09%/46.64%/46.82%/47.00%`，完整 51 条训练时为 `45.45%`；对应
Macro-F1 均值为 `36.05%/40.13%/38.73%/39.15%`，完整训练为 `38.47%`。
15 到 20 条有改善，但之后基本平台且波动明显，说明当前瓶颈不是简单
增加样本数量就能解决。训练池中 `billing` 只有 3 条、`notification` 5
条、`subscription` 7 条，后续人工确认应优先补齐这些与自动动作直接相关
的类别，并重新建立确认标签的时间 holdout。

Logistic 的训练接口支持小规模数据的异步全量重训，因此 MVP 不修改模型
内部实现来追求单样本更新。用户反馈是实时保存的，但新模型在后台训练
完成并验证后才切换。

当前已在独立的 classifier workspace 实现最小反馈桥接：
`email_classifier_feedback.py` 只保存经过同一预处理路径脱敏后的 JSONL
文本，按 `message_id` 投影最新类别，重复的
`message_id + selected_category` 提交幂等，并通过全量重训生成带版本号的
classifier。它尚未连接邮箱或 CEO Agent，也不执行任何邮件动作。

分类器 workspace 还实现了 `email_classifier_decision.py` 作为生产侧可复用
的纯数据契约：`ClassifierConfig` 校验八个类别的描述、标签、独立阈值、
动作开关；`build_decision` 将一次 `Prediction` 转成带
provider locator、模型/配置版本的 `EmailDecision`。低置信度决策进入
Email“待反馈”，状态为 `pending_feedback` 且 `action_plan=None`；已处理分类
持有不可变 `ActionPlan`。直接动作由邮件执行层消费；`auto_reply` 当前禁用，`unsubscribe`
进入 Consumer-direct 生命周期。

在此契约之上，`email_classifier_pipeline.py` 提供了一个无连接器的批量
编排入口：逐封调用分类器，将低于类别阈值或类别不具备自动动作资格的决策交给本地
`email_review_queue.py`，并返回 review/auto-candidate 统计。review queue
使用 SQLite 按 `message_id` 幂等保存决策；它不保存原始正文。这个入口是
未来 IMAP readonly adapter 与 CEO Agent adapter 之间的最小接缝，当前仍
不负责扫描 cursor、任务路由或任何邮箱写操作。

review queue 同时保存同一预处理路径生成的脱敏 `model_text`（不保存原始
正文），因此用户确认后可以由 `FeedbackStore.record_review_item` 直接写入
反馈集，并携带原预测类别、概率和模型版本。推荐调用顺序是：先成功写入
feedback store，再将 queue item 标记为 `resolved`；如果反馈写入失败，
保留 `pending` 以便重试。这仍然是本地学习数据流，不是 CEO Agent 的任务
完成状态，也不会触发邮箱动作。

当前还实现了 `email_imap_readonly.py`：它使用 Python 标准库
`imaplib.IMAP4_SSL`，只调用 `SELECT(readonly=True)`、`UID SEARCH` 和
`UID FETCH`，将 RFC822/MIME 邮件转换为统一的 `id`、provider/mailbox/UID、
thread、收发件人、主题、纯文本正文、附件标记和退订头字段。原始 RFC822
内容只在内存中经过解析，不写入 review queue；适配器没有 SMTP、`STORE`、
`COPY` 或 `EXPUNGE` 路径。它已通过离线 fixture 验证，真实连接仍需由上层
显式提供 host、username 和 password，凭据不写入代码或配置样本。

离线 fixture 还验证了标准化邮件可以直接进入 classifier pipeline 和 review
queue，不需要 provider-specific 的二次转换。这个 adapter 只提供读取和
标准化；实际扫描调度、凭据注入、cursor 持久化和 CEO Agent 任务路由仍由
上层适配器负责。

`email_scan.py` 进一步提供 `scan_once` 作为一次只读批处理入口：上层传入
已经建立的 `ReadonlyMessageSource`、classifier、配置和 review queue，它只
负责获取指定 mailbox 的最近 N 封邮件并返回抓取/分类/入队统计。它不创建
连接、不读取环境凭据、不持久化 cursor，也不执行邮箱动作，因此可以被
CLI、独立 review service 或未来 CEO Agent email adapter 复用。

当前 workspace 还提供 `email_review_cli.py` 作为本地验证入口：`scan` 使用
环境变量名注入 IMAP 用户名/密码并执行一次只读扫描，`list` 查看 pending
决策，`resolve` 先写脱敏反馈再标记 queue item，`retrain` 从反馈集训练并
原子保存带版本号的模型。CLI 不打印密码；scan 的输出只有数量统计，且
没有任何邮箱写命令。它是本地 review 验证工具，不是 CEO Agent runtime
或生产调度器。

在 CEO Agent adapter 真正接入前，独立 workspace 还提供了纯函数
`email_task_adapter_contract.create_email_task_draft`。只有上层明确传入
`handling_requested=true` 且提供具体用户请求时，才生成不执行任何动作的
`EmailTaskDraft`；用户仅确认分类时返回 `None`。draft 固定输出：

```text
channel              = email
conversation_id      = provider:mailbox:thread-or-message
trigger_message_id   = provider-stable-message-id
required_skills      = [ceo-mail-review]
```

它只携带分类元数据、版本、provider locator 和用户明确请求，不携带原始
邮件正文，也不创建 `reply_task`、调用 CEO Agent 或执行邮箱动作。未来的
CEO Agent adapter 应遵循已确认的动作边界，只将当前允许的 `unsubscribe`
映射为现有生命周期中的任务提案；`auto_reply` 当前不创建任务；在此之前不修改 runtime、schema、worker gate、
Attention 页面或 launchd 配置。

配置支持 JSON round-trip；`email_config.example.json` 是一个完整但非激活
的八类示例，`scan --config <path>` 可以加载用户自己的描述、标签、阈值、
动作开关。示例阈值是冷启动参数，不是校准后的概率，也不
单独构成外部动作授权。

反馈重训由 `email_classifier_training.py` 负责 readiness 和模型晋级：至少
需要 5 条反馈、2 个类别且每个已出现类别至少 2 条样本；随后运行留一验证，
检查候选序列化后可重新加载，最后才更新 `model.active.pkl`，已有 active
模型先保留为 `model.previous.pkl`。条件不满足或候选检查失败时不改变 active
模型。CLI `retrain` 使用同一流程并返回拒绝原因。该流程属于模型完整性和
学习闭环，不改变 CEO Agent 的外部动作授权边界。

Phase-C 的 `email_action_dry_run.py` 只根据达到类别阈值且类别具备自动动作资格的
`EmailDecision` 生成动作预览，不调用 connector。对 `subscription`，它只
接受 `List-Unsubscribe` 或正文中明确退订行里的 HTTPS 链接；只有 `mailto:`
或没有入口时返回 `unavailable`，不猜测 URL。低于阈值或类别不具备自动动作资格的
决策不生成任何动作。该模块是展示/验证接口，不是退订、删除、归档或回复执行器。

模型保留：

```text
model.active.pkl
model.previous.pkl
```

分类进程始终使用当前 active 模型；训练失败、模型损坏或验证不通过时不影响当前分类。

## 性能验收

100ms 只衡量本地分类路径，不包含邮箱网络请求、附件下载、退订页面访问、通知发送或数据库写入：

```text
已取得邮件
    -> 字段标准化
    -> 正文截断
    -> jieba 分词
    -> Logistic predict_proba
    -> 阈值判断
```

至少记录：

- `normalization_ms`；
- `tokenization_ms`；
- `classifier_predict_ms`；
- `decision_ms`；
- `total_classification_ms`。

MVP 目标：

```text
p50 < 30ms
p95 < 100ms
p99 < 200ms
```

测试应使用常驻进程和真实邮件长度分布，另外单独记录冷启动和模型加载时间。当前阶段只在当前开发环境验证，不承诺尚未测试的平台。

当前开发机的初次 warm benchmark 已覆盖 73 条脱敏邮件、20 轮共 1,460
次测量，使用公共 Conda `/Users/derek/miniforge3`。完整
`predict_message -> decision` 路径的 p50/p95/p99 为
`3.0074/14.8520/23.6673 ms`，最大值 `54.7815 ms`；其中
`predict_message` p95 为 `14.8348 ms`，决策构造 p95 为 `0.0253 ms`。
这支持当前机器上的 p95 目标，但不是跨平台保证；冷启动、模型加载和
未来目标电脑上的实测仍需单独记录。进一步将
`OMP_NUM_THREADS/OPENBLAS_NUM_THREADS/MKL_NUM_THREADS/VECLIB_MAXIMUM_THREADS`
都限制为 `1` 后，完整路径 p50/p95/p99 为
`1.6094/8.8365/11.4147 ms`，最大值 `15.3465 ms`，说明当前 MVP 不
依赖多线程 BLAS 才能满足 100ms 目标。随后一次同样的严格单线程运行
得到 p50/p95/p99/max=`2.3379/11.1640/18.3518/56.9990 ms`；当前
73 条样本模型的内存序列化体积为 `338,720` bytes，词表为 `4,924`
个特征。该体积会随人工反馈增长，需要在模型晋级时持续记录。

### 2026-08-31 生产特征重跑和新随机 holdout

先前 73 条 assistant provisional annotations 已通过 readonly DWS 重新取得，
并全部使用当前生产 `email_message_to_text` 生成脱敏特征。原始邮件只存在于
进程内存，没有写入文件；附件没有下载，邮箱没有写操作。生产特征版 73 条
临时数据 SHA-256 为
`e038e5e2309f22958ae5f1c887b988ae46b20503299332bf583e8c772fba74e9`。

同一数据上重新比较后：word-unigram TF-IDF + balanced Logistic 的时间顺序
70/30 结果仍为 `45.45% Accuracy / 38.47% Macro F1`；严格单线程纯预测
P95 约 `0.55 ms`，现有完整预处理 + jieba + inference P95 约 `6.0 ms`。
fastText 本轮最好的时间顺序候选约为 `50.0% Accuracy / 26.19% Macro F1`，
`important` recall 仍为 0，且没有 0.5 以上的自动候选；未量化模型约
`26.4 MB`，Logistic 约 `339 KB`。fastText 0.9.3 在小样本单线程 softmax
训练时还可重复触发 `Encountered NaN`。因此延迟不是瓶颈，模型选择继续保持
TF-IDF + balanced Logistic。

随后从当前 2,283 个 INBOX UID 中用固定随机种子 `2026083102` 抽取 40 封。
邮件头标注和正文特征读取均为 readonly；附件仍只使用 metadata。assistant
provisional label 分布为 `billing=5`、`important=10`、`junk=1`、
`notification=14`、`work=10`，没有 `personal`、`shopping` 或
`subscription`。脱敏 holdout SHA-256 为
`cc9d99ee22f6b4b61061ffedec8e94d375b7135e2d5db59c6ba15e824245d68a`。

用 73 条训练、40 条独立测试时，word-unigram、word-bigram、char 2-5 和
word+char 都只有约 `20% Accuracy / 13% Macro F1`。训练和测试全部重建为
同一生产特征后结果不变，排除了预处理版本错配。主要分布缺口是：旧 73 条
实验明确排除了身份验证/验证码邮件，而新随机样本包含大量登录码和安全通知，
旧模型因此没有预测出 `notification`。

把随机 40 条的最后 10 条固定为未见测试集，并逐批加入前面的 provisional
feedback 后，20 条反馈把 Accuracy 从 10% 提升到 50%，`notification`
达到 100% precision / 80% recall；30 条没有继续改善，`work` recall 仍为 0。
每次重训约 `16-20 ms`，模型约 `343-404 KB`。合并 113 条的五折 OOF 为
`54.87% Accuracy / 50.71% Macro F1`，最大 top-1 confidence 只有
`0.3109`，所以 0.85 生产 threshold 的自动覆盖率仍为 0。

这些标签不是 user-confirmed gold feedback，不进入生产 feedback store、模型
promotion 或 category eligibility。实验支持 feedback + debounce + batch retrain
结构，但不支持开放任何自动动作。下一批数据应组合随机漂移样本、低置信度
active learning 和 `billing/personal/shopping/subscription` 定向补样，并保留
user-confirmed chronological holdout。

同日又用随机种子 `2026083103` 只读取了 80 封邮件头，用于独立观察分布；provisional
标注为 `important=26`、`notification=18`、`work=15`、`junk=10`、`billing=8`、
`subscription=2`、`personal=1`、`shopping=0`。这批数据没有读取正文，也不进入
模型评估、生产 feedback 或 model promotion；与前一轮 40 封不能未经 UID 去重就
合并计算样本支持。

同日对当前 INBOX 做了全量有限 header 的 readonly `UID FETCH` 分段扫描：
2,282/2,282 个 UID 成功解析且没有失败分段，`List-Unsubscribe` 和
`List-Unsubscribe-Post` 均为 0。该结果只描述本次邮箱快照，不授权自动退订，也不
替代未来新邮件的持续监测；正文和附件仍未读取。

随后对随机种子 `2026083103` 的同一 80 个 UID 按生产 readonly adapter 读取了纯文本，
附件仍只保留 metadata，并做随机 5-fold OOF：`67.50% Accuracy / 47.24% Macro F1`。
其中 `notification` 为 `93.75% precision / 83.33% recall`（support 18），但
`subscription` 只有 2 条 provisional 样本且 precision/recall 均为 0；该结果不是
时间顺序 holdout，也不是 user-confirmed gold，不能授权 model promotion 或任何自动
退订动作。脱敏特征 SHA-256 为
`e81c24f2a2da68f7f10467e5c812a1d6d669185c6e0be61164369783d96359b2`。

## 模型评测

模型比较采用同一份用户邮件和同一套输入标准化逻辑，至少比较：

1. TF-IDF word-unigram + balanced Logistic（当前默认候选）；
2. Char TF-IDF + Logistic Regression；
3. fastText（CPU/小模型对照）；
4. HashingVectorizer + SGD Logistic（在线学习对照）；
5. Char TF-IDF + Linear SVM，并对分数做校准；
6. BERT 类模型只作为离线准确率上界，不进入 MVP 部署候选。

数据按时间顺序切分，不使用简单随机切分：

```text
较旧邮件 70%：训练
中间邮件 10%：阈值和验证
最新邮件 20%：最终测试
```

条件允许时使用滚动时间窗口，模拟 TREC 的逐封到达和延迟反馈过程。评测指标包括：

- Macro-F1；
- 每类别 precision/recall；
- 动作代价加权错误率；
- 自动处理覆盖率；
- 在指定覆盖率下的 selective accuracy；
- risk-coverage 曲线；
- p50/p95/p99 本地分类延迟；
- 模型大小和内存；
- 用户纠正 10、50、100 次后的学习曲线。

产品上的“最优”不是平均 F1 最高，而是在满足延迟要求的前提下，自动处理覆盖率和高风险动作 precision 的组合最好。

建议的动作门槛：

```text
普通标签：precision >= 95%
归档：precision >= 97%
自动退订：precision >= 99%
Trash：precision >= 99.5%，否则关闭
```

这些是上线前的验收门槛，不是模型训练目标中的固定常量。

## 错误处理与模型状态

- 邮件解析失败：不分类，保留原邮件并暴露给用户；
- 分词失败：记录错误，不自动执行高风险动作；
- 模型加载失败：使用上一个已验证模型；
- 重训失败：保留 active 模型，保存训练错误；
- 预测类别不在配置中：视为不可自动处理，进入用户决策；
- 类别样本不足：允许观察预测，不开放对应高风险动作；
- 找不到退订入口：不猜 URL，记录原因；
- Email 回复策略关闭：不发送回复、不连接 SMTP；
- 用户反馈重复提交：按 `message_id + selected_category` 去重。

## MVP 非目标

第一阶段不做：

- 跨平台 wheel、随应用发布 runtime 或完整安装矩阵；
- Transformer、LLM 或 embedding 分类；
- 向量数据库；
- 多模型 ensemble；
- 用户之间的联邦或全局模型；
- AI 生成自动回复；
- 永久删除邮件；
- 自动修改类别体系；
- 复杂在线增量训练；
- 完整管理后台。

Trash 只作为可恢复状态，不执行永久清空。

## 2026-09-02 notification 影子候选证据

在固定门槛后使用未见时间批次 F/G 验证，去除 1 条与训练集重复的 `junk` 邮件，
最终 holdout 为 79 条，其中 notification positive support 为 21。balanced
TF-IDF Logistic（`C=0.25`）在 notification confidence `>=0.25` 时选出 19 条，
19 条全部正确，precision 100%、recall 90.48%；整体为 63.29% Accuracy、
54.55% Macro F1。`0.20` 门槛的 precision 只有 76%，`0.30` 虽保持 100%
precision 但 recall 降到 80.95%。SGD、Naive Bayes、取消 balanced class weight、
subject-only 和额外 margin 均未通过跨切分或跨批稳定性验证，不进入生产方案。

这批标签全部来自 `assistant_authorized_manual_annotation`，不是 user-confirmed
feedback；正式非 subscription 类别门槛仍要求 30 个 validation positive samples。
因此 notification 只进入“版本化候选模型 + readonly shadow”开发，不直接获得
生产自动动作资格。用户已授权门槛满足后的 label、mark-read、archive、move 和
可恢复 Trash，但明确禁止 Email 回复；SMTP 保持关闭，Trash 永不 EXPUNGE。

实现上，实验 snapshot 只能进入不可变 `candidate` 记录：不切换 active manifest，
不标记生产 feedback 已纳入模型，并把所有类别写为
`auto_action_eligible=false / non_authoritative_validation_labels`。第一版候选
`email-tfidf-lr-20260902T192218Z-6d8a1ca9` 使用 144 条训练、79 条去重验证，得到
68.35% Accuracy、62.18% Macro F1 和 0.61 ms P95；notification 结果保持 19/19，
但仍只允许 readonly shadow。

后续另一批 10 条冻结预测后的 assistant 标注中，notification `>=0.25` 为 5/5，
另一个 top-1 notification 预测因 confidence 只有 0.2007 被门槛挡住，人工标签实际
为 important。与 F/G 合计为 24/24 自动候选正确、26 个 notification positive
support。动态邮箱的随机实验必须用完整 `sample_id_digest` 集合标识；seed 不能在
最近 UID 池变化后复现同一批邮件。该结果仍不改变 user-confirmed 和 30-positive
正式门槛。

再增加两个不重叠随机批次后，四批 label-only shadow 共 37 条。与 F/G 合并，
notification 0.25 门槛为 29/29 自动候选正确、32 个 positive support、recall
90.63%。但新增 10 个自动候选全部来自同一登录/安全通知来源；跨来源的 Flight Watch
通知仍被预测为 subscription。该结果跨过研究样本数，不证明跨来源泛化，也不改变
assistant 标签的非权威状态。不能仅因累计 support 超过 30 就切换 active 或执行动作。

来源整体留出验证进一步证明类别级指标会产生误导。八个 snapshot 去重后共有 263
封邮件和 78 个匿名来源组；`LeaveOneGroupOut` 保证每一折的来源在训练中完全未见。
在同一个 balanced TF-IDF Logistic、`C=0.25` 和 notification 0.25 门槛下，候选仅
`2/11` 正确，precision 18.18%、recall 2.56%，整体 Accuracy 29.66%、Macro F1
21.43%。原 F/G 的 19 个正确候选只覆盖 3 个来源，且一个来源贡献 17 个。

删除 exact-sender hash，或同时删除 sender domain/hash，没有改善来源整体留出结果。
邮件正文与主题模板本身同样是强来源特征。因此第一版 eligibility 不能只有
`model_version + category + threshold`；至少还需要区分经过独立验证的已见来源和未知
来源。未知来源默认拒判并进入待反馈。即使用户已授权高置信度 label/read/archive/
move/可恢复 Trash，这项授权也不能绕过来源泛化和具体动作 precision 门槛；回复、
永久删除和 EXPUNGE 仍完全禁止。

真实邮箱随后增加两批来源去重 shadow，共 17 个训练时未见且互不重复的 sender
domain。冻结预测后 assistant 标注的 top-1 为 12/17；其中只有 1 个 notification
正样本，模型虽然 top-1 正确，但 confidence `0.233675 < 0.25`，所以 17 个未知来源
没有任何 notification 自动候选。该结果不证明 0.25 在未知来源上的 precision，反而
证明其自动覆盖集中在已见模板。第一版路由应把未知来源强制送入待反馈，直到来源均衡
验证得到足够候选和正样本；不能通过降低 threshold 来制造覆盖率。

进一步的单分类器表示比较使用相同五折来源分组。word+char TF-IDF Logistic
（word 1–2 + char-wb 3–5，`C=0.25`）取得最高的 35.74% Accuracy / 25.61%
Macro F1，单封 P95 低于 33 ms。小 bucket fastText 即使训练集 Accuracy 达 85.55%，
未知来源只有 25.48% / 17.24%，notification 各阈值均未接近 95% precision；因此
fastText 不替换当前 Logistic。

逐类别选择性指标中，只有 junk 在未知来源上出现足够分散的高 precision 候选：
threshold `>=0.30603` 为 24/24，覆盖 21 个来源，最大来源占 8.33%，recall 41.38%。
在后续新数据揭晓前，实验阈值冻结为 0.31，模型冻结为
`email-tfidf-word-char-junk-v1-d0fc0d4b`。该结果是开发集选择，不是最终 holdout；
模型保持 `auto_action_eligible=false`。只有全新来源 holdout 达到 Trash 的 99.5%
precision 和样本门槛后，才讨论可恢复 Trash；否则最多进入标签或继续 shadow。

冻结后的 20 个全新来源 holdout 否决了 junk v1。标签中有 8 个 junk；0.31 门槛
选出 7 个候选，只有 6 个正确，precision 85.71%。误判是一封具体、个性化的公司
播客采访邀请：即使形式类似冷邮件，它也可能有品牌价值，不能自动移入 Trash。
候选 `email-tfidf-word-char-junk-v1-d0fc0d4b` 状态因此变为 rejected，邮箱 effect
保持关闭。查看 holdout 后不能在同一批上重选阈值；20 封可进入 v2 训练，但 v2 必须
使用新的 source-disjoint holdout。

将该 20 封转入训练后，junk label-only v2 使用 283 封、98 个来源。五折来源分组下，
`junk >= 0.312` 为 29/30，precision 96.67%，positive support 66，覆盖 28 个来源，
最大来源占 6.67%。候选 `email-tfidf-word-char-junk-label-v2-d94ee93b` 只允许验证
增加 Junk 标签，显式禁止 mark-read/archive/move/Trash/unsubscribe/auto-reply。冻结
运行时为 Python 3.12.11、scikit-learn 1.8.0、numpy 2.4.3、scipy 1.17.1；不同
scikit-learn 版本不得直接加载该 pickle 后用于验证或生产判断。
它保持 inactive，等待另一批全新来源 holdout；label 通过不能自动推导出 Trash 通过。

v2 的独立来源验证也未通过。首批随机 30 个新来源只有 5 个阈值候选，虽为 5/5，
但证据量不足；随后从随机未知来源流收集 15 个额外候选，其中只有 12 个为 junk。
两批合计 17/20，precision 85%，低于 label-only 的 95%要求。三个错误都是不能忽略的
潜在商业机会：收购/M&A 接洽、研究合作和机构投资接洽。因此 v2 状态变为 rejected，
不得注册 active，也不得把“只加标签”扩大为移动或 Trash。

用户已授权高置信度标签、归档、移动和可恢复 move-to-Trash，但永久删除、EXPUNGE、
清空垃圾箱和邮件回复仍禁止。授权与模型准入是两个独立条件：只有某一类别、某一动作
通过冻结版本的来源独立验证，才允许该动作执行。下一版只能继续使用同一个 word+char
Logistic 分类器，把本轮 45 个主动学习样本加入训练，专门学习 junk 与有价值战略接洽
的边界；冻结 v3 后必须再用全新来源 holdout。若 v3 仍失败，停止自动动作实验。

hard-example v3 使用 328 封、143 个来源，仍是单个 word+char Logistic。五折来源
分组下整体 Accuracy/Macro F1 为 43.60%/34.17%；冻结 `junk >= 0.324` 为 34/35，
precision 97.14%、recall 36.96%，覆盖 33 个候选来源。模型
`email-tfidf-word-char-junk-label-v3-eb1dfe4a` 仍为 inactive label-only 候选，并显式
禁止 mark-read/archive/move/Trash/unsubscribe/auto-reply。最后一次全新来源验证必须在
该版本和阈值已提交后进行；失败即停止迭代。

v3 最终全新来源 precision 验证仍失败。随机遍历 87 个新来源后取得 20 个 0.324 阈值
候选，其中 `junk=16`、`important=3`、`work=1`，precision 80%。误判继续集中于媒体
报道、政府合同合作、相关产品合作和 CEO 活动等可能有价值的陌生接洽。这证明单封文本
可以很好识别典型推销模板，但当前 assistant 标签规模不足以稳定学习“陌生商业邮件的
用户价值”。`email-tfidf-word-char-junk-label-v3-eb1dfe4a` 因此 rejected。

当前模型结论收敛为：保留一个 CPU 单分类器提供建议和排序，所有类别默认进入待反馈；
只有未来真实用户反馈形成新的来源独立验证并达到对应动作门槛，才允许开启 model-only
动作。现阶段不开发或启用自动标签、移动、Trash；用户授权仍保留，但不替代质量门槛。

## 研究依据

- TREC Spam Track 使用按时间到达的邮件流、过滤分数、延迟反馈和有限主动查询，支持本方案采用时间顺序评测、拒判和用户反馈闭环。[NIST TREC Spam Track](https://trec.nist.gov/data/spam.html)、[TREC 2007 Spam Track Overview](https://trec.nist.gov/pubs/trec16/papers/SPAM.OVERVIEW16.pdf)
- Enron/SRI 邮件文件夹分类实验显示，MaxEnt、SVM 和 wide-margin Winnow 通常处于经典第一梯队，Naive Bayes 在多个用户上明显落后，并强调 From 等邮件头特征和时间线评测。[Automatic Categorization of Email into Folders](https://ciir.cs.umass.edu/pubfiles/ir-418.pdf)
- Gmail Priority Inbox 使用可扩展的线性 Logistic、用户级参数、用户阈值和显式反馈，说明简单线性模型可以支撑个性化邮件决策。[The Learning Behind Gmail Priority Inbox](https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/36955.pdf)
- fastText 官方方案支持词 n-gram、字符子词、概率预测和量化，满足本 MVP 对 CPU 和模型体积的约束。[fastText 官方监督分类教程](https://fasttext.cc/docs/en/supervised-tutorial.html)、[fastText Python API](https://fasttext.cc/docs/en/python-module.html)
