# Email 模型训练执行边界

模型训练请求在提交时绑定一个已冻结的文件夹快照。业务类别始终取快照中邮件所在文件夹；用户反馈与 Agent 自动标注只决定哪些快照邮件纳入本次训练，不能覆盖文件夹标签。

一次手动训练可选择 TF-IDF + Logistic Regression、fastText 与 Embedding + MLP。每个选择的模型家族在相同的训练、验证和测试切分中独立训练，并保存为候选版本；候选生成不会改写用户反馈，也不会启用线上模型。

训练运行记录保留选中来源、类别、来源数据集摘要与内部邮件身份清单。控制台只返回来源摘要，不返回该内部清单。TF-IDF 和 fastText 候选以本地 registry artifact 保存并执行保存后重新加载预测校验；Embedding + MLP 继续采用既有 staged evidence 与晋升门槛流程。

多家族一起训练时，各家族按顺序独立训练；experiment-only 的 TF-IDF / fastText 候选失败（Derek，2026-09-28 实测：fastText 自身的 C++ 优化器在偏薄的类别上发散到 NaN，报 `RuntimeError: Encountered NaN.`）只记入该次运行的 `reason`（`family_failures:<family>=<error>`），不影响本次已训练出的 Embedding + MLP 候选被保存；只有 Embedding + MLP 本身失败，或选中的家族全部失败，整次运行才记为 `failed`。

**自动重训只在收到新的文件夹快照事件时判断一次**（`observe_snapshot_and_maybe_retrain`），不是定时轮询重新判断；轮询循环（`run_training_scheduler_loop`，60 秒一次）只推进已在跑的那次运行的状态。`minimum_ready` 要求已启用的每个类别在同一份快照的 train/validation/test 三个切分里都至少有一条——一个类别若从不靠移动邮件进对应文件夹产生样本（如 `invoice`，2026-09-28 观测：已绑定“发票”文件夹但该文件夹里没有邮件），自动的按文件夹快照训练就会一直 `minimum_ready=False`、永远不会自动触发，要靠控制台手动训练里跨来源（`user_feedback`/`folder_snapshot`/`agent_auto_label`）的选择数据才够。
