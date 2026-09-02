# Email 融合 CEO Agent 激活方案

**日期：** 2026-09-02

**状态：** Proposed，等待 Derek 反馈

**工作树：** `/Users/derek/Documents/Projects/ceo-agent-service`
**当前生产状态：** Email 集成代码已合并到 `main`，`dingtalk_primary` 账号和相关
Skills 已启用；由于没有可晋升的 active model，Email worker 仍保持
`waiting_configuration / missing_model`，没有真实邮箱分类、任务创建或邮箱写操作

## 1. 这份方案解决什么

Email 的代码集成已经覆盖多邮箱 connector、独立 Email worker、分类/反馈/训练、Email Console、确定性 provider action、自动回复的 Consumer → Audit，以及 unsubscribe 的 Consumer-direct 例外。

当前问题不再是“代码能不能跑”，而是“在分类质量不够时，哪些能力可以安全进入 CEO Agent”。截至 2026-09-02，210 封时间顺序验证仍显示 CPU 延迟充分达标，但分类质量受时间分布影响明显，不能开放任何 model-only 自动动作。

因此建议把“代码融合”和“动作激活”分开：先让 CEO Agent 获得只读 Email 能力和反馈闭环，再由真实 user-confirmed 数据逐类别解锁动作。

## 2. 固定系统边界

```text
Connector（可配置多个 IMAP/SMTP 邮箱）
    ↓
独立 Email worker
    ↓
readonly IMAP scan
    ↓
一个 CPU classifier
    ↓
category + confidence + alternatives + versioned model_id
    ↓
┌─────────────────────────────────────────────────────────┐
│ 未达到类别 eligibility / threshold                     │
│ → Email / 待反馈                                       │
│ → 用户确认或改类                                       │
│ → 保存 feedback                                        │
│ → debounce 后批量重训                                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 达到类别 eligibility + threshold                       │
│ → 生成固定、不可变 ActionPlan                          │
│ → 按动作类型进入 direct provider 或 Email task 生命周期 │
└─────────────────────────────────────────────────────────┘
```

分类确认本身只写分类反馈，不创建 CEO Agent task。只有当前类别配置明确要求 `auto_reply` 或 `unsubscribe` 时，才创建 `channel=email` task。

附件只进入 metadata；不下载、不 OCR、不提取、不让 classifier 或 Agent 阅读附件正文。

## 3. 与 CEO Agent 的路由关系

### 3.1 Connector

- 一个 connector 配置可以包含多个邮箱账户；每个账户有独立 `account_id`、IMAP/SMTP 地址、secret reference、扫描文件夹和扫描间隔。
- 凭据只从受控环境读取；Console 不回显明文密码。
- 每个 provider locator 都包含账户和文件夹身份，避免不同邮箱之间 message ID 冲突。

### 3.2 独立 Email worker

Email 不共享普通 task worker。一个由现有 supervisor 管理的 Email 子进程内部保留三条独立组件边界：

1. readonly scan + classifier；
2. direct provider action；
3. Email Agent task consumer + training scheduler。

这样 deterministic provider action 不占用 Agent worker，而自动回复和退订仍拥有各自明确的执行生命周期。

### 3.3 Task 生命周期

| 行为 | 是否自动创建 Email task | 执行路径 |
| --- | --- | --- |
| 用户仅确认分类 | 否 | 保存 feedback，进入批量学习 |
| 配置的 label/read/archive/move/trash | 否 | direct provider action + effect record/readback；trash 仅可恢复，不永久删除 |
| 配置的 `auto_reply` | 是 | Email Consumer → Audit Agent → effect/readback |
| 配置的 `unsubscribe` | 是 | Email unsubscribe Consumer-direct；记录完整 observability，不进入 Audit |
| 开放式分析、回复、跟进 | 只有用户明确要求时 | 普通 `channel=email` task；附件仍只有 metadata |

`unsubscribe` 是唯一跳过 Audit 的已批准例外。它仍受不可变 ActionPlan、Consumer 最终判断、可靠退订入口、幂等、独立持久浏览器 profile 和 terminal outcome 约束。

## 4. Email 页面

Email Console 保持四个分区：

1. `已处理`：最终分类和动作结果，包括分类来源、模型版本、配置版本及 effect/readback；
2. `待反馈`：模型建议、置信度、备选类别，由用户确认或改类；
3. `邮件配置`：类别描述、启用状态、threshold、固定动作和参数；
4. `学习`：active/previous/candidate 模型、完整 versioned model_id、训练时间、样本数、各类别样本数、Accuracy、Macro F1、每类别 precision/recall/F1 和 P50/P95 延迟。

Attention 不承载正常的低置信度分类。Attention 只接邮箱连接失败、provider action terminal failure、Agent/Audit 无法恢复、浏览器执行失败等真正异常。

## 5. 分阶段激活

### 阶段 A：代码合并与动作关闭（已完成）

- Email integration 代码已合并到 `main`；
- 生产账号配置已启用，但由于没有 active model，worker 没有启动有效扫描；
- 没有创建 active model；
- 没有执行 provider action；
- 已完成主分支 Email 专项回归、旧库迁移检查、launchd 重启和本地健康回读。

这是代码融合，不是生产能力激活。

### 阶段 B：readonly shadow + 待反馈（尚待产品确认）

- 在明确批准后，只启用一个邮箱的 readonly scan；
- 所有类别 `auto_action_eligible=false`；
- 所有邮件进入 `待反馈`，模型预测只作建议；
- 用户确认写入 authoritative feedback；
- batch retrain 允许生成 versioned candidate/active model，但 category action eligibility 继续关闭；
- 不执行标签、归档、trash、回复或退订。

建议采样不是 100% 随机：保留随机样本监测真实分布，同时定向补齐 `billing`、`personal`、`shopping`、`subscription` 和模型最不确定的类别。最新累计 210 封 provisional 样本仍然只有 `shopping=1`、`personal=0`、`subscription=15`；纯随机和时间顺序切分都无法快速建立八分类训练集。

### 阶段 C：逐类别开启低风险 direct action

只有某个类别自己的 user-confirmed、time-ordered validation 达到当前批准门槛，且配置 threshold 与模型评测 threshold 完全一致时，才把该类别设为 `auto_action_eligible=true`。

- 不用 aggregate accuracy 或 aggregate Macro F1 代替类别证据；
- 不因为其他类别达标而连带开放当前类别；
- threshold 改动立即使 eligibility stale，重新回到待反馈；
- 第一批只考虑 label/read/archive 等可恢复、可 readback 的 direct action；
- permanent delete 永远不进入 v1。

### 阶段 D：自动回复

只有配置明确的固定回复场景进入 automatic `channel=email` task。回复正文仍由 Email Consumer 生成或读取配置，但任何 SMTP 外部写入必须经过 Audit Agent 并完成 effect reconciliation/readback。

开放式回复不因分类结果自动创建；必须来自用户明确要求。

### 阶段 E：自动退订候选

在以下条件全部成立前，model-predicted `subscription` 只进入待反馈：

- subscription-specific validated precision `>= 0.95`；
- subscription validation positive support `>= 20`；
- time-ordered validation 同时记录 recall 和 F1；
- 当前 threshold 与评测 threshold 完全一致；
- Consumer 对当前邮件和 thread 做最终判断；
- 存在可靠的 RFC one-click、`List-Unsubscribe` HTTPS 或明确退订语义 HTTPS 入口；
- 使用独立 persistent headless Chromium profile，不读取 Derek 主 Chrome cookie；
- 不处理密码、MFA、二维码、CAPTCHA 或付款；
- 保存 terminal result text、步骤、outcome 和 observation digest。

## 6. 当前实验如何约束激活

| 项目 | 当前证据 | 决策 |
| --- | --- | --- |
| CPU 延迟 | 210 封实验中单封预测 P95 约 0.37–0.58 ms；既有端到端 P95 约 6 ms | 通过 100 ms 门槛 |
| 模型体积 | Logistic 约 339–404 KB | 可接受 |
| fastText | 更快但 Macro F1 更低、约 26.4 MB、小样本训练不稳定 | 不替换当前模型 |
| 新随机/合并 holdout | 100 封：70.00% Accuracy / 43.11% Macro F1；210 封：160/50 为 60.00% / 34.10%，170/40 为 75.00% / 53.75% | 结果随时间切分波动，不开放自动分类动作 |
| 反馈学习 | 20 条后 notification 达到 100% precision / 80% recall（固定 10 条 provisional test） | 反馈闭环值得进入 shadow |
| 合并 210 条时间顺序 holdout | 160/50：60.00% Accuracy / 34.10% Macro F1；170/40：75.00% / 53.75% | 仅研究证据，不能用 aggregate 指标晋升 |
| 最大 confidence | C=0.25 为 0.3792；C=1.0 为 0.6626 | 0.85 threshold 下自动覆盖为 0 |
| threshold 校准 | threshold=0.20 在两个时间切分分别为 86.67% precision、100.00% precision，覆盖率 30% 与 20%，不可复现 | 不降低生产 threshold |
| subscription support | 15 条 provisional，user-confirmed support 为 0 | 不满足 precision >= 0.95 且 support >= 20 gate |
| personal/shopping | personal=0、shopping=1 | 不具备完整训练或评测资格 |

这些标签全部是 assistant provisional annotations，不是 production gold feedback。它们只能决定实验方向，不能授权 provider action。

2026-09-02 的 210 封样本进一步证明：增加样本会改善部分时间窗口，但不能消除
类别漂移；最高置信度仍低于批准的 `0.85` 门槛，且 threshold `0.20` 的 precision
在相邻时间切分上不稳定。Naive Bayes 和不加权 Logistic 的对照也没有提供可安全
替代当前候选的模型。因此当前应进入“只读 shadow + 用户反馈”评审，而不是进入
model-only provider action 激活。

## 7. 上线前还需要的证据

1. 在主分支合并后的完整 backend/frontend 回归；
2. migration 和 rollback 演练；
3. 单邮箱 readonly shadow 的 live HTTP、worker health、queue/backlog 和 SQLite integrity；
4. Email Console 浏览器验收：已处理、待反馈、配置、学习四个分区；
5. user-confirmed 时间顺序 holdout；
6. 每类别 eligibility 和 threshold 的不可变模型 metadata；
7. direct provider action 的真实小批量 effect/readback；
8. 自动回复的 Consumer → Audit → effect reconciliation；
9. unsubscribe 只在单独确认后做受控真实站点小批量验收；
10. 生产 launchd 新进程、Email worker 新进程、无 failed/processing backlog。

## 8. 等待 Derek 决定的事项

代码合并、账号/Skills 启用和“动作关闭”的生产回读已经完成。本方案现在停在
下一阶段的产品激活决策，不执行模型晋升、真实邮箱写操作或自动任务开放。需要确认：

1. 是否允许使用当前唯一配置邮箱开启 readonly shadow，让邮件进入 Email 页面
   的“待反馈”；当前没有 active model，批准后仍需先生成候选模型，不能把本轮
   assistant provisional annotations 直接当作生产 gold；
2. shadow 阶段是扫描全部新邮件，还是只对有限样本进入“待反馈”；
3. 是否采用“随机漂移样本 + 定向稀有类别 + 低置信度 active learning”的反馈采样组合；
4. 用户确认样本达到什么最低总量后，才开始第一次 time-ordered candidate 评估；
5. 在当前批准的 precision/support 门槛之外，是否要为 `important` 增加 recall 下限。
   该项属于新的 safety gate，必须单独确认并单独实现，不能顺手加入现有代码。

### 8.1 建议的 readonly shadow 验证协议（待 Derek 确认）

基于当前 210 封样本暴露出的时间漂移和模板重复问题，建议下一阶段只验证
“读取、分类、反馈、学习”四个环节，不验证邮箱写动作：

1. 只启用一个已配置邮箱的 readonly IMAP scan；扫描结果进入独立 Email worker，
   不调用 SMTP，不执行 `STORE`、移动、删除、归档或退订。
2. 使用当前 CPU 候选模型生成 `category`、`confidence`、备选类别和带版本的
   `model_id`；无论置信度高低都只作为 Email 页“待反馈”的建议，所有类别
   `auto_action_eligible=false`。
3. 用户确认或改类时，只保存 authoritative feedback，不创建 CEO Agent task；
   feedback 必须同时记录 provider locator、确认时间、原预测、最终类别、
   model_id 和 config_version，保证后续能按模型版本和时间窗口重建评估集。
4. 候选模型训练只从用户确认 feedback 产生；评估同时包含消息级时间 holdout
   和按规范化主题/来源分组的 holdout。任何一组关键类别证据不达标，候选模型
   只能留在 candidate，不能晋升 active。
5. 只有当某类别自己的 user-confirmed precision、support、recall/F1 和校准
   证据达标，且配置 threshold 与评测 threshold 完全一致时，才允许讨论该类别
   的 direct action；其他类别不受连带影响。`subscription` 仍额外要求
   precision `>=0.95`、support `>=20`，并由 Consumer 做最终判断。
6. readonly shadow 的验收证据固定为：Email worker 新进程、读取计数、反馈计数、
   SQLite integrity、无 provider write、无 Email task，以及学习页的模型版本和
   样本统计。没有这些 readback，不能把 shadow 视为已启用。

这份协议中的“只读 shadow 是否启用、扫描全部新邮件还是有限样本、是否采用
随机/定向/不确定性混合采样”仍然等待 Derek 的产品确认；在确认前保持
`waiting_configuration / missing_model` 和所有 provider action 关闭。

## 9. 2026-09-02 完成审计矩阵

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| CPU 分类器满足 100 ms 目标 | 当前 Logistic 单封预测 P95 约 0.37–0.58 ms，既有端到端 P95 约 6 ms | 已验证 |
| 分类、反馈、训练和模型版本化 | Email worker、Console 四个分区、模型 registry 及相关回归 | 已验证 |
| 分类确认不创建 task | pipeline/action-plan boundary 测试通过 | 已验证 |
| `auto_reply` 写操作经过 Audit | `consumer_audit_v1` 路径及测试通过 | 已验证 |
| `unsubscribe` 使用 Consumer-direct 例外 | lifecycle、Consumer、task-bound operation 及 loopback E2E 通过 | 已验证 |
| 退订使用独立 headless 浏览器 profile | 34 个 loopback browser tests 通过 | 已验证 |
| 退订 terminal result text 可追溯 | receipt、digest、步骤和 Email projection 测试通过 | 已验证 |
| 低置信度邮件进入待反馈 | 冷启动和 threshold/eligibility fail-closed 测试通过 | 已验证 |
| subscription 自动门槛达到 precision >= 0.95、support >= 20 | 210 封 provisional 样本中 support=15；user-confirmed support=0，时间 holdout 不稳定 | 未满足，保持关闭 |
| user-confirmed 时间顺序 holdout | 当前尚未积累足够 user-confirmed feedback | 待实验 |
| 主分支合并、launchd 重启和线上 readback | `main` 已包含 Email 集成；launchd 已重启，健康和学习 API 已回读 | 已完成 |
| 真实邮箱写操作小批量验收 | 尚未获得本阶段单独的外部效果授权 | 等待 Derek 授权 |

该矩阵的“已验证”只表示独立工作树中的实现和测试证据，不表示主分支已经
合并，也不表示生产服务已经加载这些代码。当前唯一需要产品决策的动作是先否
允许以 disabled 配置合并，并随后开启只读 shadow；在此之前所有外部邮箱写动作
继续关闭。
