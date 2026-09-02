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
