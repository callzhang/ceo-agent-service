# Email 双回溯分类设计

## 目标

每个邮箱账户分别配置 Agent 和本地模型的回溯天数。默认 Agent 回溯 30 天，模型回溯 365 天。定时邮件检查同时推进两条有界路径：

- Agent 路径只处理 Agent 窗口内、符合账户未读/全部设置且尚无稳定记录的邮件。
- 模型路径处理模型窗口内其余尚无稳定记录的 Inbox 或明确配置为未分类来源的邮件，不受已读状态限制。
- 模型高置信度且类别已晋升时，保存分类和不可变动作计划，由现有 provider action consumer 移动、标记或清理邮件。
- 模型低置信度、预测为 `others` 或类别尚未晋升时，保存为现有 `pending_feedback` 状态，不创建 Agent 分类任务，也不创建动作计划。
- 模型不可用、Embedding 超时或存储失败属于技术失败：保留邮件待下次扫描，不伪装成人工待确认。

人工在“待确认”中选择最终类别后，继续走现有确认、动作计划和训练样本链路，因此新增历史样本会自然进入后续训练。

## 当前问题

当前 `email-message-check-once` 定时命令明确把 `online_runtime` 和 `accept_model` 设为 `None`，所以它只创建 Agent 分类任务。仓库虽有历史模型分类器，但它只能显式触发、只读取已有 Embedding cache，并且历史工作从未注册到自动 worker。结果是：账户的 30 天扫描窗口并不是“Agent 30 天 + 模型更长窗口”，而只是 Agent 单窗口。

## 方案比较

### 方案 A：同一次定时检查运行两条独立扫描（采用）

先运行 Agent 窗口，再运行模型窗口。两条路径都以稳定邮件身份和已持久化分类去重；因此重叠的最近邮件由 Agent 先取得，模型只看到剩余邮件。模型窗口每次最多读取固定批量，后续定时运行通过已持久化记录自然向更早邮件推进。

优点是复用现有定时任务、分类表、动作队列和待确认页面，状态可恢复且没有第二套人工队列。缺点是同一来源文件夹每轮会做两次只读搜索，但每次正文读取有批量上限。

### 方案 B：自动调度现有手工历史分类器（不采用）

该分类器只接受已读邮件和已有精确 Embedding cache，cache miss 会直接延期，无法实现“免费模型覆盖一年历史邮件”。它还把不确定结果写入历史结果表，而不是用户正在使用的“待确认”列表。

### 方案 C：一次扫描后按邮件年龄分流（不采用）

单次 provider 搜索看似更省，但一个共享游标会让持续到达的新邮件挤压历史回填，且未读、已读和两个时间窗口的分页语义耦合，恢复边界更复杂。

## 数据与配置

`email_accounts` 将旧的 `scan_lookback_days` 明确迁移为 `agent_lookback_days`，并新增 `model_lookback_days`：

- `agent_lookback_days`: 1–365，迁移时保留每个账户原值，默认 30。
- `model_lookback_days`: 1–3650，默认 365。
- `scan_read_state`: 保留，但只描述 Agent 窗口是仅未读还是全部；模型窗口总是覆盖已读和未读。

API 和前端只使用新字段，不保留双写或旧字段回退。设置页清楚说明两个窗口的职责以及不确定模型结果进入“待确认”。

## 扫描与优先级

每个启用账户按如下顺序运行：

1. 构建当前启用类别、描述和已验证目标文件夹。
2. Agent 扫描 `agent_lookback_days`，沿用 `scan_read_state` 和现有 Agent task producer。
3. 如果当前 runtime 是 `model_primary`，模型扫描 `model_lookback_days` 内全部已读/未读未分类邮件。
4. 模型扫描调用同一个已晋升 runtime 和 canonical model input；不调用顺序路由器，因此没有 Agent fallback。
5. 成功保存的 processed/pending_feedback 分类成为稳定记录，之后的扫描跳过它；重复副本沿用现有分类重放逻辑。

如果两个窗口重叠，Agent 路径优先。举例：Agent=30 天、model=365 天时，最近 30 天符合 Agent 读取条件的邮件由 Agent 处理；最近 30 天中 Agent 因“仅未读”而跳过的已读邮件，以及第 31–365 天的邮件，由模型处理。

## 模型结果

模型 runtime 保留当前实时接口：低置信度、`others` 和未晋升类别仍向实时顺序路由器表现为“需要 Agent fallback”。同时在结果中附带只供历史复核路径使用的原始预测证据。历史模型扫描读取该证据并写入：

- `category` / `predicted_category`: 模型建议类别；
- `confidence`, `margin`, `probabilities`: 原始模型分数；
- `classification_source`: `model`；
- `status`: `pending_feedback`；
- `action_plan`: `null`。

这保持实时新邮件的既有行为不变，同时让自动历史回填满足“不确定邮件不消耗 Agent”的要求。

## 失败与恢复

- 没有主模型时，Agent 扫描照常工作，模型扫描本轮为零；不把一年历史邮件交给 Agent。
- 模型推理或 provider 读取失败时，账户本轮报告失败，未持久化邮件下次重试。
- 已持久化 pending_feedback 不会重复推理或进入 Agent。
- accepted 分类保存后即进入现有动作队列；动作失败和重试继续由现有 provider action 生命周期负责。
- 定时扫描保持每文件夹固定批量，避免一次性读取一整年正文。

## 验收标准

1. 新建和迁移账户分别得到 Agent=30、模型=365 的默认值，旧账户 Agent 原值不丢失。
2. 设置页可独立保存两个窗口，并解释 Agent 读取状态只作用于 Agent。
3. 定时命令在主模型启用时按两个独立窗口查询。
4. 模型 accepted 邮件保存 processed 分类并创建现有动作任务，不创建 Agent 分类任务。
5. 模型 rejected、others、未晋升类别保存 pending_feedback，不创建任何 Agent 分类任务或动作计划。
6. 手工确认 pending_feedback 后仍生成训练样本和动作计划。
7. 模型不可用或技术失败不会把历史邮件回退给 Agent。
8. 架构和运行机制文档不再声称历史模型只能手工触发或晋升模型总会回退 Agent。
