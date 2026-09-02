# Email classifier random 100 holdout

更新时间：2026-09-02

## 实验目的

在已有 110 封样本之外，再从 INBOX 中排除全部旧样本后随机抽取 100 封，
检查更大样本下的时间顺序表现，并观察类别是否会因为邮箱时间分布而集中
出现在 holdout。

## 数据与边界

- 当前 INBOX 观察到 `2,311` 个 UID；排除此前 190 个已标注 UID 后，从剩余
  `2,121` 个 UID 中使用固定随机种子 `20260910` 抽取 100 封。
- 精确 UID 样本身份 digest 为
  `6134364742d32035535a78a17651ebd28ae55f0e150e8dfecf4c21d76014148f`；
  抓取时 `uidvalidity=2`，100/100 样本读取成功。
- 按 UID 时间顺序取前 80 封训练、后 20 封 holdout。
- 标签为本次会话的 `assistant_provisional_annotation`，不是用户确认的 gold
  feedback；标签分布为 `billing=9`、`important=36`、`junk=18`、
  `notification=24`、`subscription=4`、`work=9`。
- 训练集分布为 `billing=8`、`important=34`、`junk=13`、`notification=16`、
  `work=9`；没有 `subscription`、`personal` 或 `shopping`。4 封 subscription
  全部落在 holdout，说明时间顺序切分会暴露类别漂移，但本轮无法检验该类的
  学习能力。
- 邮件正文只用于本地特征计算；正文字符数中位数为 `1,275`，最大为 `12,406`。
  附件只保留 metadata，没有下载、打开、解析、OCR 或总结附件。
- 没有执行邮箱写操作，没有创建 Email task，没有写入生产数据库，也没有执行
  自动回复、退订、标签、归档或删除。

## 结果

| C | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.10 | 70.00% | 43.11% | 0.41 ms | 0.2265 | 0/20 |
| 0.25 | 70.00% | 43.11% | 0.32 ms | 0.2633 | 0/20 |
| 1.00 | 70.00% | 43.11% | 0.26 ms | 0.4014 | 0/20 |

三组 C 值的预测结果完全相同。holdout 的实际分布为
`billing=1`、`important=2`、`junk=5`、`notification=8`、`subscription=4`；
word-unigram Logistic 的结果为：

- `billing`：0/1；
- `important`：1/2；
- `junk`：5/5；
- `notification`：8/8；
- `subscription`：0/4，因为训练集没有 subscription 样本。

## 结论

1. 这轮 70% Accuracy 主要来自后置 holdout 中 `junk` 和 `notification` 的命中，
   不能代表八分类整体质量；Macro F1 只有 43.11%。
2. `subscription` 按 UID 时间集中出现在样本后段，导致训练集没有该类、holdout
   有 4 封。该现象是重要的数据分布证据：随机抽样后按时间切分不能保证每类都
   有训练 support，更不能据此开放自动退订。
3. C 值从 0.10 到 1.00 没有改变预测；最高置信度仍低于 0.41，所有样本均未
   达到 0.85，说明降低或提高 C 都不能解决类别覆盖问题。
4. 单封分类延迟稳定远低于 `100ms`，继续支持 word-unigram TF-IDF + balanced
   Logistic 作为 CPU 候选；当前瓶颈仍是反馈数据和时间分布，不是推理性能。
5. 本轮结果不能支持 active model 或任何 provider action。继续积累用户确认的
   `subscription`、`shopping`、`personal` 和 `work` 样本，并在合并时间窗口上
   验证类别级 precision、recall、support 和 calibration。

## 与前序样本合并的 210 封验证

为减少 100 封单独切分的偶然性，将本轮 100 封与前面已经固定标注的 110 封
合并为 210 封唯一样本，再按 UID 时间顺序做两组 expanding holdout。合并样本
身份 digest 为
`e0dfebfb105b3f7f5ee5d57ffd8db08574a1fde951ada7649b98f165cd1786ce`。

210 封标签分布为：`billing=19`、`important=62`、`junk=38`、
`notification=46`、`shopping=1`、`subscription=15`、`work=29`，仍然没有
`personal`。这些仍是 assistant provisional annotation，不是用户确认的 gold
feedback。

| 训练/holdout | C | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 160/50 | 0.25 | 60.00% | 34.10% | 0.39 ms | 0.2589 | 0/50 |
| 160/50 | 1.00 | 58.00% | 33.00% | 0.37 ms | 0.5016 | 0/50 |
| 170/40 | 0.25 | 75.00% | 53.75% | 0.37 ms | 0.3792 | 0/40 |
| 170/40 | 1.00 | 70.00% | 49.73% | 0.39 ms | 0.6626 | 0/40 |

160/50 的训练集只有 `subscription=1`，holdout 有 `subscription=14`；170/40
的训练集有 `subscription=3`，holdout 有 `subscription=12`。因此 170/40 的较好
结果仍不能说明订阅类已经可学习：训练 support 过小，且整体最高置信度仍未达到
自动阈值。两组切分也显示，增加十封训练邮件会显著改变结果，当前质量估计尚未
稳定。

210 封结果进一步确认：推理延迟已经不是瓶颈，数据覆盖、时间漂移、用户确认
反馈和概率校准才是进入生产的前置条件。继续保持 active model 关闭，不开放任何
provider action；后续必须优先获得按类别、按来源、按时间窗口积累的用户确认样本。

## 置信度与 precision/coverage 曲线

在同一批 210 封样本上，对 word-unigram Logistic（balanced、C=0.25）的
top-1 confidence 做阈值扫描。结果仅用于检查是否存在稳定的自动处理子集，
不改变现有 `0.85/0.95` 门槛。

### 160/50 时间切分

| 阈值 | 覆盖数量 | 覆盖率 | 选中样本 precision | 预测 subscription | subscription precision |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.10 | 50/50 | 100.00% | 60.00% | 0 | — |
| 0.15 | 50/50 | 100.00% | 60.00% | 0 | — |
| 0.20 | 15/50 | 30.00% | 86.67% | 0 | — |
| 0.25 | 1/50 | 2.00% | 100.00% | 0 | — |
| 0.30 及以上 | 0/50 | 0.00% | — | 0 | — |

### 170/40 时间切分

| 阈值 | 覆盖数量 | 覆盖率 | 选中样本 precision | 预测 subscription | subscription precision |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.10 | 40/40 | 100.00% | 75.00% | 7 | 100.00%（7/7） |
| 0.15 | 40/40 | 100.00% | 75.00% | 7 | 100.00%（7/7） |
| 0.20 | 8/40 | 20.00% | 100.00% | 6 | 100.00%（6/6） |
| 0.25 | 1/40 | 2.50% | 100.00% | 1 | 100.00%（1/1） |
| 0.30 | 1/40 | 2.50% | 100.00% | 1 | 100.00%（1/1） |
| 0.40 及以上 | 0/40 | 0.00% | — | 0 | — |

两组时间切分在 threshold `0.20` 上直接矛盾：一个 precision 为 `86.67%` 且
没有 subscription 预测，另一个 precision 为 `100%` 且只覆盖 8 封。后者的
subscription support 只有 6，远低于自动退订所需的 `support>=20`，并且标签
仍然是 provisional annotation。因此不能把 `0.20` 作为生产阈值，也不能把
这组 6/6 结果当成 subscription-specific validation。

在批准的 `0.85` 和 `0.95` 门槛下，两组切分均为零覆盖；当前最安全的行为仍是
把预测作为建议展示在 Email“待反馈”，等待用户确认反馈积累后重新校准。

## 模板外 holdout

消息级时间切分可能让同一邮件模板的不同副本同时出现在训练和 holdout。为测量
对未见模板的泛化，使用同一批 210 封邮件，按规范化主题分为 161 个组：去除
重复的 `Re:`/`Fw:` 前缀、合并空白，并将数字归一化；同一组不会跨越训练和
holdout。按组的最大 UID 排序，前 120 组作为训练、后 41 组作为 holdout，最终
为 152/58 封。

训练集分布为 `billing=18`、`important=56`、`junk=23`、`notification=25`、
`shopping=1`、`subscription=1`、`work=28`；holdout 分布为 `billing=1`、
`important=6`、`junk=15`、`notification=21`、`subscription=14`、`work=1`。

| C | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.25 | 55.17% | 30.32% | 0.39 ms | 0.2339 | 0/58 |
| 1.00 | 53.45% | 29.39% | 0.39 ms | 0.4154 | 0/58 |

相同数据的消息级 170/40 时间切分为 `75.00% Accuracy / 53.75% Macro F1`；
模板外切分降至 `55.17% / 30.32%`。这不是严格的生产分布估计，因为主题归一化
仍是实验分组启发式，但足以说明重复模板会明显抬高普通 holdout 指标。后续
评估应同时报告消息级时间 holdout 和模板/来源分组 holdout，不能只用前者晋升
模型或开启 provider action。

## 新一轮随机 100 封实验（2026-09-12）

为继续验证真实邮箱分布，而不是重复使用之前的固定样本，本轮通过 readonly
IMAP `UID SEARCH` 后使用随机种子 `20260912` 抽取 100 封邮件。读取过程只使用
`SELECT(readonly=True)`、`UID SEARCH` 和 `UID FETCH`；没有执行 `STORE`、`COPY`、
`MOVE`、`EXPUNGE`、SMTP 发信、退订或其他 provider 写操作。邮件正文只在内存中
用于本轮研究标注和评测，附件只记录是否存在，不读取附件正文。

本轮标签仍为 `assistant_provisional_annotation`，不是用户确认的 gold feedback，
也没有写入生产 feedback 表。样本摘要的 SHA-256 为
`0887efe46d7fcfd18052e823d7292ea2123509211506e674e530e3a62d0b76e6`，类别分布为：

`billing=8`、`important=21`、`junk=21`、`notification=19`、
`subscription=8`、`work=23`；本轮没有 `personal` 或 `shopping` 样本。

### UID 时间顺序 holdout

按 UID 从旧到新排序，前 80 封训练、后 20 封 holdout。使用当前生产候选
`jieba-tfidf-word-unigram-v1 + balanced Logistic`，比较三个正则强度：

| C | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.10 | 50.00% | 39.80% | 0.4468 ms | 0.2064 | 0/20 |
| 0.25 | 50.00% | 39.80% | 0.4515 ms | 0.2655 | 0/20 |
| 1.00 | 55.00% | 41.15% | 0.4254 ms | 0.4784 | 0/20 |

### 未见主题组 holdout

将去除常见回复/转发前缀并把数字归一化后的主题分成 87 组，使用固定随机种子
`42` 做按组的 80/20 holdout，训练/测试规模为 `80/20`。这组切分避免了同一
主题模板的副本同时出现在训练和测试中：

| C | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.10 | 50.00% | 44.43% | 0.5446 ms | 0.2104 | 0/20 |
| 0.25 | 50.00% | 44.43% | 0.5613 ms | 0.2760 | 0/20 |
| 1.00 | 50.00% | 44.43% | 0.5881 ms | 0.5041 | 0/20 |

本轮与前一批 210 封结果的方向一致：CPU 延迟仍不到 1 ms，远低于 100 ms
目标；但是普通时间切分只有 `50%–55% Accuracy`，未见主题组也只有 `50%`，
且所有模型在 `0.85` 门槛下均为零覆盖。C 从 `0.25` 调到 `1.0` 没有带来可
重复的跨模板质量改善。

本轮 100 封仍是 assistant provisional annotation，不能用于 active model
晋升、类别 eligibility 或自动退订 support。它进一步支持以下决策：继续采用
word-unigram TF-IDF + balanced Logistic 作为实现候选，但先进入 readonly shadow
和用户确认反馈阶段；在取得 user-confirmed、按类别和时间窗口分布的反馈前，不
开发或开启 model-only provider action。
