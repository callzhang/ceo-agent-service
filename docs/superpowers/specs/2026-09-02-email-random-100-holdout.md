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
