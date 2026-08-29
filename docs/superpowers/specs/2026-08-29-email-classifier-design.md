# Email 分类器 MVP 设计

## 状态

设计已在对话中确认，等待用户审阅本文件。本文只定义 MVP 的分类、反馈学习和固定动作边界，不包含实现计划。

## 背景与目标

现有邮件过滤方案来自两个 n8n 工作流：

- `/Users/derek/Downloads/gmail filter.json`
- `/Users/derek/Downloads/Ding email classifier.json`

现有方案通过 LLM 直接判断邮件类别并驱动后续节点。目标是将核心判断收敛为一个轻量的、本地 CPU 可运行的多分类器，并保留以下产品能力：

- 一封邮件只产生一个互斥类别；
- 类别确定后，后续动作由配置固定决定；
- 模型无法高置信度判断时，把邮件暴露给用户；
- 用户选择的类别自动进入训练集；
- 支持标签、描述、自动回复、自动归档、自动退订和 Trash 等配置；
- 本地分类路径的目标是 p95 小于 100ms；
- MVP 先在当前开发环境跑通，不做跨平台安装包和完整发布矩阵。

## 设计结论

MVP 使用一个 fastText 互斥多分类器：

```text
邮件结构化字段和正文
    -> 中文轻量分词及字段 token 化
    -> fastText 词 unigram/bigram + 字符子词
    -> softmax 多分类
    -> 类别级置信度阈值
    -> 通过阈值则执行该类别固定动作，否则请求用户决策
```

fastText 被选为默认方案的原因是：它保留了线性分类器的 CPU 速度和小模型特点，同时可以使用词组和字符子词，适合中文、英文、发件人地址、域名和产品名混合的邮件输入。官方实现支持监督分类、词 n-gram、字符子词、概率预测以及量化。[fastText 文本分类教程](https://fasttext.cc/docs/en/supervised-tutorial.html)、[fastText Python API](https://fasttext.cc/docs/en/python-module.html)

HashingVectorizer + SGD Logistic 保留为离线对照和 fastText 无法安装时的后备实现；不做线上 ensemble。Linear SVM、TF-IDF Logistic 和 fastText 的比较应在用户自己的时间顺序数据上完成，而不是仅引用公开数据集的结果。

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

`unknown` 不是训练类别。它表示分类器的 top-1 结果没有达到该类别的自动处理阈值，属于拒绝自动决策的状态：

```text
top-1 类别未达到类别阈值
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
__from_address__notifications@github.com
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

## fastText 初始参数

第一版使用以下参数作为起点：

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

这些是验证起点，不是永久配置。最终参数通过时间顺序验证集选择。

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

fastText 的公开监督训练接口以训练文件为输入，因此 MVP 采用小规模数据的异步全量重训，不修改 fastText 内部实现来追求单样本更新。用户反馈是实时保存的，但新模型在后台训练完成并验证后才切换。

模型保留：

```text
model.active.bin
model.previous.bin
```

分类进程始终使用当前 active 模型；训练失败、模型损坏或验证不通过时不影响当前分类。

## 性能验收

100ms 只衡量本地分类路径，不包含邮箱网络请求、附件下载、退订页面访问、通知发送或数据库写入：

```text
已取得邮件
    -> 字段标准化
    -> 正文截断
    -> jieba 分词
    -> fastText predict
    -> 阈值判断
```

至少记录：

- `normalization_ms`；
- `tokenization_ms`；
- `fasttext_predict_ms`；
- `decision_ms`；
- `total_classification_ms`。

MVP 目标：

```text
p50 < 30ms
p95 < 100ms
p99 < 200ms
```

测试应使用常驻进程和真实邮件长度分布，另外单独记录冷启动和模型加载时间。当前阶段只在当前开发环境验证，不承诺尚未测试的平台。

## 模型评测

模型比较采用同一份用户邮件和同一套输入标准化逻辑，至少比较：

1. fastText；
2. Char Hashing + SGD Logistic；
3. Char TF-IDF + Logistic Regression；
4. Char TF-IDF + Linear SVM，并对分数做校准；
5. BERT 类模型只作为离线准确率上界，不进入 MVP 部署候选。

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
- 自动回复未配置模板：不发送回复；
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

## 研究依据

- TREC Spam Track 使用按时间到达的邮件流、过滤分数、延迟反馈和有限主动查询，支持本方案采用时间顺序评测、拒判和用户反馈闭环。[NIST TREC Spam Track](https://trec.nist.gov/data/spam.html)、[TREC 2007 Spam Track Overview](https://trec.nist.gov/pubs/trec16/papers/SPAM.OVERVIEW16.pdf)
- Enron/SRI 邮件文件夹分类实验显示，MaxEnt、SVM 和 wide-margin Winnow 通常处于经典第一梯队，Naive Bayes 在多个用户上明显落后，并强调 From 等邮件头特征和时间线评测。[Automatic Categorization of Email into Folders](https://ciir.cs.umass.edu/pubfiles/ir-418.pdf)
- Gmail Priority Inbox 使用可扩展的线性 Logistic、用户级参数、用户阈值和显式反馈，说明简单线性模型可以支撑个性化邮件决策。[The Learning Behind Gmail Priority Inbox](https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/36955.pdf)
- fastText 官方方案支持词 n-gram、字符子词、概率预测和量化，满足本 MVP 对 CPU 和模型体积的约束。[fastText 官方监督分类教程](https://fasttext.cc/docs/en/supervised-tutorial.html)、[fastText Python API](https://fasttext.cc/docs/en/python-module.html)

