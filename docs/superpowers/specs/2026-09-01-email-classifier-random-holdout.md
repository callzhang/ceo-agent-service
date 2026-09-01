# Email classifier random holdout

更新时间：2026-09-01

## 实验目的

验证在真实邮箱新样本上，当前 CPU sparse classifier 是否能稳定泛化，
并比较 word、character 和 LinearSVC 候选。该实验只用于模型研究，不会晋升
active model，也不授权任何 Email provider action。

## 数据与边界

- 从当前 INBOX 的 `2299` 个 UID 中，以固定种子 `20260906` 和 `20260907` 各随机抽取
  `40` 封；两批合并后为 `80` 个唯一 UID，没有冲突重复样本；
- 两批样本均通过生产 `ImapReadonlyAdapter` 读取标准纯文本；附件只保留 filename、
  MIME、声明大小、count 和 inline metadata；
- 按 UID 时间顺序取前 `60` 封训练、后 `20` 封 holdout；
- 标签由本次会话中的 assistant 依据已确定的八分类定义做
  `assistant_provisional_annotation`，不是 user-confirmed gold feedback；
- 合并标签分布：`billing=16`、`important=22`、`junk=12`、`notification=21`、
  `work=9`；本批仍没有 `personal`、`shopping`、`subscription` 样本；
- holdout 分布：`important=3`、`junk=6`、`notification=9`、`work=2`；
- 没有执行邮箱写操作，没有创建 Email task，没有把原始邮件或附件内容写入生产库。

## 时间顺序 holdout 结果

| 候选 | Accuracy | Macro F1（实际 holdout 类别） | 训练时间 | 预测 P95 | 模型序列化大小 |
| --- | ---: | ---: | ---: | ---: | ---: |
| word unigram Logistic | 55.00% | 37.30% | 约 51.7 ms | 约 0.21 ms | 约 179 KB |
| word bigram Logistic | 55.00% | 37.30% | 约 23.9 ms | 约 0.32 ms | 约 676 KB |
| char 2–5 Logistic | 55.00% | 38.82% | 约 220.6 ms | 约 2.39 ms | 约 3.91 MB |
| word + char Logistic | 55.00% | 37.82% | 约 163.6 ms | 约 2.67 ms | 约 3.90 MB |
| word bigram LinearSVC | 50.00% | 32.06% | 约 14.2 ms | 约 0.28 ms | 约 676 KB |

word-unigram Logistic 的 holdout 最大 top-1 confidence 为 `0.2442`，在 `0.85` 和
`0.95` 两个阈值下的覆盖率均为 `0`。其余候选也没有达到自动处理阈值；最高为
word + char Logistic 的 `0.2793`，仍不足以支持自动动作。

## 结论

1. 选型继续保持 word-unigram TF-IDF + balanced Logistic：char 特征只带来约
   `1.52` 个百分点 Macro F1 增益，但模型约大 `22` 倍、预测约慢 `11` 倍，且
   provisional 小样本不能证明该增益可重复；fastText 的此前对照也没有质量收益。
2. 这轮比前一轮单批 holdout 更稳定地显示出通知类信号，但 `work` recall 为 `0`，
   类别覆盖仍不完整；结果不能作为 active model promotion 证据。
3. 所有样本仍是 assistant provisional annotation，不能用于 subscription-specific
   precision/support gate，也不能授权自动退订、自动回复或其他 provider action。
4. 下一步应继续积累 user-confirmed feedback，特别是 `work`、`subscription`、
   `personal` 和 `shopping`，再用新的时间窗口做候选模型评估。生产 worker 保持
   `waiting_configuration / missing_model` 是预期结果。
