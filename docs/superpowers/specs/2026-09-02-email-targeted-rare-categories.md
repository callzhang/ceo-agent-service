# Email classifier targeted rare-category experiment

更新时间：2026-09-02

## 实验目的

在随机样本长期缺少 `subscription`、`shopping` 和 `personal` 的情况下，
从邮件头候选池定向抽取可能覆盖这些类别的邮件，检查新增类别覆盖是否能改善
CPU 分类器的时间顺序泛化，同时确认候选池关键词不会把普通工作通知误判为订阅。

## 数据与边界

- 使用随机种子 `20260908` 产生的定向候选集；候选池先通过邮件头筛选，再从
  `INBOX` 精确读取 50 个固定 UID。抓取时 `uidvalidity=2`，50/50 UID 全部仍然
  存在，没有因为邮箱新增邮件造成样本漂移。
- 精确样本身份 digest 为
  `dadd758fb2f38b71fa94bf7f0cf8f316c6eb1fa9338e23970d3f9cfd898c4a03`。
- 候选池的头部提示分布为：`other=652`、`personal_or_external=1212`、
  `shopping=47`、`subscription=43`、`work=275`；这只是抽样提示，不是最终标签。
- 最终标签由本次会话依据已确定的八分类定义进行
  `assistant_provisional_annotation`，不是用户确认的 gold feedback；标签分布为：
  `billing=5`、`important=9`、`junk=9`、`notification=13`、`shopping=1`、
  `subscription=2`、`work=11`，仍然没有 `personal` 样本。
- 邮件正文只用于本地特征计算，正文字符数中位数为 `1,285`，最大为 `46,775`；
  附件仅保留生产读取器提供的 metadata，没有下载、打开、解析、OCR 或总结附件。
- 本轮只执行 IMAP SSL 只读操作，没有写回邮箱、没有创建 Email task、没有写入
  生产数据库，也没有执行自动回复、退订或其他 provider action。

## 时间顺序 holdout

按 UID 时间顺序取前 35 封训练，后 15 封作为 holdout。由于本轮是针对稀有类别
的定向抽样，holdout 包含 `billing=1`、`important=3`、`junk=5`、
`notification=4`、`subscription=2`，训练集没有 `subscription`；因此该 split
可以检验真实时间顺序下的保守性，但不能证明 subscription 的可学习性。

| 候选 | Accuracy | Macro F1（实际 holdout 类别） | 预测 P95 | 最大置信度 | 达到 0.85 的数量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| word unigram Logistic，C=0.25 | 40.00% | 44.05% | 0.90 ms | 0.2824 | 0/15 |
| word bigram Logistic，C=0.25 | 40.00% | 44.29% | 1.37 ms | 0.2850 | 0/15 |

word-unigram 的 holdout 混淆矩阵（类别顺序为
`billing`、`important`、`junk`、`notification`、`subscription`）为：

```text
[[1, 0, 0, 0, 0],
 [0, 1, 1, 0, 0],
 [0, 0, 1, 0, 0],
 [0, 0, 0, 3, 0],
 [0, 0, 0, 0, 0]]
```

## 观察

1. 定向抽样确实补进了 `shopping=1` 和 `subscription=2`，但没有补进
   `personal`；`subscription` 还全部落在 holdout，训练阶段没有可学习样本，
   因而模型无法预测该类。这说明“候选池命中”不等于“训练覆盖”。
2. 头部关键词存在系统性误导：带有“订单及回款”的 CRM 报告实际是
   `notification`，Google Ads 的周期报告也是 `notification`，普通项目周报是
   `work`；只有明确的 Substack newsletter 被标为 `subscription`。因此不能把
   `weekly`、`order`、`report` 等单个关键词直接映射为固定动作。
3. word bigram 只带来 `0.24` 个百分点 Macro F1 增益，却把预测 P95 从 `0.90 ms`
   增加到 `1.37 ms`；在没有稳定质量收益的情况下，不替换当前 word-unigram
   候选。
4. 两个候选的最大置信度都低于 `0.30`，在 `0.85` 门槛下覆盖率为 `0`。这与前面
   80 封精确样本的实验一致：当前模型还没有可靠的自动分类区间，低置信度 review
   应继续承接所有结果。

## 结论与下一步

1. 当前 CPU 选型仍为 word-unigram TF-IDF + balanced Logistic；本轮没有证据支持
   增加 bigram 或把定向关键词直接做成规则分类器。
2. 不晋升 active model，不开启自动标签、归档、删除、自动回复或自动退订；生产
   worker 继续保持 `waiting_configuration / missing_model`。
3. 生产开发的下一道门槛不是继续做随机参数搜索，而是积累用户在 Email 页
   “待反馈”中确认的标签，确保每个可自动动作类别都有时间后置样本。尤其需要
   `subscription`、`shopping`、`personal` 的用户确认反馈。
4. 自动退订仍需单独满足 subscription-specific precision `>=0.95` 且 support
   `>=20`，并继续由 Consumer 做最终判断；本轮 provisional 标签和小样本不计入
   该门槛。
