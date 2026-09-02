# Email classifier random expansion holdout

更新时间：2026-09-02

## 实验目的

扩大真实邮箱的时间顺序验证样本，确认上一轮 60 封 holdout 的提升是否稳定，
并观察 `subscription` 在训练集中有少量样本时能否泛化。该实验仍只评估
CPU 候选模型，不改变生产 worker 或任何邮箱动作。

## 数据与边界

- 当前 INBOX 共观察到 `2,310` 个 UID；排除此前已经标注的 130 个 UID 后，
  从剩余 2,180 个 UID 中使用固定随机种子 `20260909` 抽取 60 封。
- 60 封样本按 UID 排序后，前 45 封训练、后 15 封 holdout。精确样本身份
  digest 为 `6e665b49d81f6bccb393fee82a53f700f73fb4f0d00990cc972df44908c84749`；
  抓取时 `uidvalidity=2`，60/60 样本均成功读取。
- 标签由本次会话依据已经确定的八分类定义完成
  `assistant_provisional_annotation`，不是用户确认的 gold feedback。
- 60 封标签分布为：`billing=5`、`important=18`、`junk=10`、
  `notification=9`、`subscription=9`、`work=9`；本批没有
  `personal` 或 `shopping`。
- 训练集含 `subscription=3`，holdout 含 `subscription=6`；这比上一轮
  定向样本中“训练集没有 subscription”更适合做泛化观察，但 support 仍然很小。
- 邮件正文只用于本地特征计算；正文字符数中位数为 `1,085`，最大为 `36,817`。
  附件仍然只保留 metadata，没有下载、打开、解析、OCR 或总结附件。
- 没有执行邮箱写操作，没有创建 Email task，没有写入生产数据库，也没有执行
  自动回复、退订、标签、归档或删除。

## 60 封样本的时间顺序 holdout

| 候选 | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| word unigram Logistic，C=0.25 | 66.67% | 66.81% | 0.92 ms | 0.2103 | 0/15 |
| word bigram Logistic，C=0.25 | 66.67% | 64.76% | 1.63 ms | 0.2133 | 0/15 |

word-unigram 在 holdout 的实际类别上表现为：

- `important`：2/2 命中；
- `junk`：1/4 命中；
- `notification`：3/3 命中；
- `subscription`：4/6 命中，precision 为 `4/7`，约 `57.14%`，远低于退订门槛。

这批结果看起来优于此前部分小样本实验，但 holdout 只有 15 封，且类别分布
不均衡，不能单独作为模型晋升证据。

## 110 封合并样本的时间顺序验证

为检验 60 封结果是否只是抽样波动，将本轮 60 封与上一轮固定的 50 封定向样本
合并，形成 110 封唯一样本，按 UID 排序后取前 80 封训练、后 30 封 holdout。
合并样本身份 digest 为
`d3833f6f3deb81d42f2b34178a73f7010df622f913179b6ae41e69904e62476d`。

合并标签分布为：`billing=10`、`important=27`、`junk=19`、
`notification=22`、`shopping=1`、`subscription=11`、`work=20`；仍然没有
`personal`。合并 holdout 分布为 `important=3`、`junk=10`、
`notification=7`、`subscription=10`。

| 候选 | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| word unigram Logistic，C=0.25 | 43.33% | 27.61% | 0.58 ms | 0.2226 | 0/30 |
| word bigram Logistic，C=0.25 | 43.33% | 31.57% | 0.66 ms | 0.2222 | 0/30 |

word-unigram 在合并 holdout 中：

- `important`：2/3 命中；
- `junk`：9/10 命中；
- `notification`：2/7 命中；
- `subscription`：0/10 命中。

## 结论

1. 60 封样本的 `66.81%` Macro F1 没有在 110 封合并时间窗口上复现，合并结果
   降至 `27.61%`。当前应把 60 封结果视为小样本波动，而不是质量跃升。
2. 订阅类是最关键的反例：即使合并样本总共有 11 封 subscription，时间后置
   holdout 仍然 0/10 命中；这不能支持自动退订，更不能满足 subscription-specific
   precision `>=0.95` 和 support `>=20`。
3. word-unigram 和 word-bigram 的推理均远低于 `100ms`，但速度不是当前瓶颈；
   bigram 在合并样本上只提高 `3.96` 个百分点 Macro F1，仍未解决订阅泛化，
   因此继续保留 word-unigram 作为最小 CPU 候选。
4. 所有 holdout 的最大置信度都低于 `0.23`，在 `0.85` 或 `0.95` 门槛下覆盖率
   均为 0。生产分类策略仍应是低置信度进入 Email 页“待反馈”，不能自动执行固定动作。
5. 暂不开发 active model 和生产自动动作。下一步最有价值的是积累用户确认的
   Email feedback，按类别和时间窗口补齐 `subscription`、`shopping`、`personal`
   以及 `work` 的训练/holdout 样本，再进行新的时间后置验证。

## 轻量 Naive Bayes 架构对照

为验证是否存在比 Logistic 更适合小样本稀疏文本的 CPU 架构，在完全相同的
110 封样本、80/30 时间顺序切分上增加了 ComplementNB 和 MultinomialNB；随后
在 60 封独立随机样本、45/15 切分上复核。所有候选都使用同一个
`email_message_to_text` 特征入口，没有引入向量数据库或附件内容。

110 封合并样本的结果：

| 候选 | Accuracy | Macro F1 | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TF-IDF + Logistic，balanced，C=0.25 | 43.33% | 27.61% | 0.40 ms | 0.2226 | 0/30 |
| TF-IDF + ComplementNB，alpha=0.1 | 36.67% | 31.79% | 0.58 ms | 0.9987 | 3/30 |
| Count + MultinomialNB，alpha=0.1 | 53.33% | 42.96% | 0.32 ms | 1.0000 | 27/30 |

在独立的 60 封样本上：

| 候选 | Accuracy | Macro F1 | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TF-IDF + Logistic，balanced，C=0.25 | 66.67% | 66.81% | 0.63 ms | 0.2103 | 0/15 |
| Count + MultinomialNB，alpha=0.1 | 53.33% | 35.65% | 0.40 ms | 1.0000 | 15/15 |
| TF-IDF + ComplementNB，alpha=0.1 | 66.67% | 66.81% | 0.48 ms | 0.9759 | 1/15 |

### 对照结论

1. MultinomialNB 在 110 封合并样本上的 F1 和 Accuracy 看起来较高，但在 60 封
   独立样本上明显下降；其概率严重过度自信，不能把 `0.85` 或 `1.0` 当成可靠
   的自动动作置信度。
2. ComplementNB 的质量在两个切分上分别表现为 `31.79%` 和 `66.81%` Macro F1，
   波动同样很大；TF-IDF 版本的置信度虽比 Count 版本保守，但仍不能证明已校准。
3. 这轮对照没有改变架构选择：继续保留 word-unigram TF-IDF + balanced Logistic，
   因为它的概率至少表现为保守，能安全地把当前不确定样本送入“待反馈”；如果
   将来引入 Naive Bayes，必须单独做概率校准和类别级 precision 验证，不能直接
   进入自动标签、自动回复或自动退订。

## Logistic 类别加权对照

在同一批 110 封样本和相同的 80/30 时间顺序切分上，进一步比较
`class_weight="balanced"` 与不加权 Logistic，并测试 `C=0.1/0.25/1.0`。

| 类别权重 | C | Accuracy | Macro F1 | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| balanced | 0.10 | 40.00% | 24.83% | 0.58 ms | 0.1742 | 0/30 |
| balanced | 0.25 | 43.33% | 27.61% | 0.42 ms | 0.2226 | 0/30 |
| balanced | 1.00 | 46.67% | 32.82% | 0.42 ms | 0.4266 | 0/30 |
| none | 0.10 | 10.00% | 4.55% | 0.39 ms | 0.3281 | 0/30 |
| none | 0.25 | 16.67% | 15.95% | 0.42 ms | 0.3679 | 0/30 |
| none | 1.00 | 16.67% | 13.50% | 0.41 ms | 0.5150 | 0/30 |

不加权模型在 30 封 holdout 中分别有 30、28、23 封预测成训练集中的多数类
`important`，不能覆盖后置样本的 `junk`、`notification` 和 `subscription`。
这说明类别加权是当前最小模型的必要配置；但即使 balanced、C=1.0 的结果相对
最好，也没有形成可自动处理的置信度区间，因此不据此修改生产阈值或开放动作。
