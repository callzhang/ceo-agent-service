# Email 融合 CEO Agent 激活方案

**日期：** 2026-08-31

**状态：** 用户已授权高置信度确定性邮箱处理；回复邮件保持全局关闭；模型质量门槛仍未满足

**工作树：** `/Users/derek/Documents/Projects/ceo-agent-service/.worktrees/email-integration-main`
**生产状态：** 未合并、未部署；当前没有合格 active model，因此没有真实邮箱写操作

## 1. 这份方案解决什么

Email 的代码集成已经覆盖多邮箱 connector、独立 Email worker、分类/反馈/训练、Email Console、确定性 provider action，以及 unsubscribe 的 Consumer-direct 例外。当前部署明确关闭邮件回复；历史 `auto_reply` contract 和审计回执仍保留用于兼容读取，但不能从 Email 配置或 worker 运行时生成。

当前问题不再是“代码能不能跑”，而是“在分类质量不够时，哪些能力可以安全进入 CEO Agent”。最新实验结论是：CPU 延迟充分达标，但分类质量和类别覆盖不足，不能开放任何 model-only 自动动作。

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

分类确认本身只写分类反馈，不创建 CEO Agent task。当前部署只有明确授权且通过质量门槛的 `unsubscribe` 才创建 `channel=email` task；`auto_reply` 被全局禁用。

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
| 配置的 `auto_reply` | 否，当前配置/API/worker 均禁止 | 不创建 task，不连接 SMTP；历史 contract 仅保留兼容读取 |
| 配置的 `unsubscribe` | 是 | Email unsubscribe Consumer-direct；记录完整 observability，不进入 Audit |
| 开放式分析、回复、跟进 | 分析可按明确需求创建；回复当前禁用 | 普通 `channel=email` task；附件仍只有 metadata；不发送邮件 |

`unsubscribe` 是唯一跳过 Audit 的已批准例外。它仍受不可变 ActionPlan、Consumer 最终判断、可靠退订入口、幂等、独立持久浏览器 profile 和 terminal outcome 约束。

## 4. Email 页面

Email Console 保持四个分区：

1. `已处理`：最终分类和动作结果，包括分类来源、模型版本、配置版本及 effect/readback；
2. `待反馈`：模型建议、置信度、备选类别，由用户确认或改类；
3. `邮件配置`：类别描述、启用状态、threshold、固定动作和参数；
4. `学习`：active/previous/candidate 模型、完整 versioned model_id、训练时间、样本数、各类别样本数、Accuracy、Macro F1、每类别 precision/recall/F1 和 P50/P95 延迟。

Attention 不承载正常的低置信度分类。Attention 只接邮箱连接失败、provider action terminal failure、Agent/Audit 无法恢复、浏览器执行失败等真正异常。

## 5. 分阶段激活

### 阶段 A：合并但保持 disabled

- 合并 Email integration 代码；
- 不启动真实扫描；
- 不创建 active model；
- 不执行 provider action；
- 完成主分支回归、迁移检查和 launchd 配置审阅。

这是代码融合，不是生产能力激活。

### 阶段 B：readonly shadow + 待反馈

- 只启用一个邮箱的 readonly scan；
- 所有类别 `auto_action_eligible=false`；
- 所有邮件进入 `待反馈`，模型预测只作建议；
- 用户确认写入 authoritative feedback；
- batch retrain 允许生成 versioned candidate/active model，但 category action eligibility 继续关闭；
- 没有类别达到 eligibility 时不执行标签、归档、trash 或退订；邮件回复始终不执行。

建议采样不是 100% 随机：保留随机样本监测真实分布，同时定向补齐 `billing`、`personal`、`shopping`、`subscription` 和模型最不确定的类别。最新随机 40 条没有任何 personal/shopping/subscription，已经证明纯随机无法快速建立八分类训练集。

### 阶段 C：逐类别开启已授权 direct action

只有某个类别自己的 user-confirmed、time-ordered validation 达到当前批准门槛，且配置 threshold 与模型评测 threshold 完全一致时，才把该类别设为 `auto_action_eligible=true`。

- 不用 aggregate accuracy 或 aggregate Macro F1 代替类别证据；
- 不因为其他类别达标而连带开放当前类别；
- threshold 改动立即使 eligibility stale，重新回到待反馈；
- Derek 已明确授权高置信度类别执行 label/read/archive/move/trash；仍必须满足类别级质量门槛、threshold 一致性和 provider effect/readback；
- `trash` 只移动到可恢复的 Trash，禁止永久删除和 `EXPUNGE`；
- permanent delete 永远不进入 v1。

### 阶段 D：邮件回复（当前关闭）

当前不允许任何 Email 回复。API 不接受 `auto_reply` 配置，worker runtime 不接受包含
`auto_reply` 的 scan config，也不连接 SMTP。若未来重新开放，必须作为独立策略变更重新确认
Consumer → Audit → effect reconciliation/readback；本次授权不包含该项。

分类结果、人工确认和既有 Email task 都不构成回复授权。

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
| CPU 延迟 | Logistic 端到端 P95 约 6 ms | 通过 100 ms 门槛 |
| 模型体积 | Logistic 约 339–404 KB | 可接受 |
| fastText | 更快但 Macro F1 更低、约 26.4 MB、小样本训练不稳定 | 不替换当前模型 |
| 新随机 holdout | 40 条约 20% accuracy | 不开放自动分类动作 |
| 反馈学习 | 20 条后 notification 达到 100% precision / 80% recall（固定 10 条 provisional test） | 反馈闭环值得进入 shadow |
| 合并 113 条 OOF | 54.87% Accuracy / 50.71% Macro F1 | 仅研究证据 |
| 最大 confidence | 0.3109 | 0.85 threshold 下自动覆盖为 0 |
| subscription support | 12 条 provisional，0 条新随机样本 | 不满足 20 条 user-confirmed gate |
| personal/shopping | 0 条 | 不具备训练或评测资格 |

这些标签全部是 assistant provisional annotations，不是 production gold feedback。它们只能决定实验方向，不能授权 provider action。

## 7. 上线前还需要的证据

1. 在主分支合并后的完整 backend/frontend 回归；
2. migration 和 rollback 演练；
3. 单邮箱 readonly shadow 的 live HTTP、worker health、queue/backlog 和 SQLite integrity；
4. Email Console 浏览器验收：已处理、待反馈、配置、学习四个分区；
5. user-confirmed 时间顺序 holdout；
6. 每类别 eligibility 和 threshold 的不可变模型 metadata；
7. direct provider action 的真实小批量 effect/readback；
8. Email 回复禁用策略的 API、worker、skill 和 SMTP 不调用测试；
9. unsubscribe 只在订阅级门槛满足后做受控真实站点小批量验收；
10. 生产 launchd 新进程、Email worker 新进程、无 failed/processing backlog。

## 8. 等待 Derek 决定的事项

本方案到此停在产品激活决策，不执行合并、部署或真实邮箱写操作。需要确认：

1. 是否先合并 Email integration，并由一个邮箱开启 readonly shadow + 待反馈；
2. shadow 阶段是扫描全部新邮件，还是只抽样进入待反馈；
3. 是否采用“随机漂移样本 + 定向稀有类别 + 低置信度 active learning”的反馈采样组合；
4. 在当前批准的 precision/support 门槛之外，是否要为 `important` 增加 recall 下限。该项属于新的 safety gate，必须单独确认并单独实现，不能顺手加入本次合并。

## 9. 2026-08-31 完成审计矩阵

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| CPU 分类器满足 100 ms 目标 | 当前 Logistic 端到端 P95 约 6 ms | 已验证 |
| 分类、反馈、训练和模型版本化 | Email worker、Console 四个分区、模型 registry 及相关回归 | 已验证 |
| 分类确认不创建 task | pipeline/action-plan boundary 测试通过 | 已验证 |
| Email 回复全局关闭 | API、worker scan config、Email skill 和前端均拒绝/隐藏 `auto_reply` | 已实现，未启用 SMTP |
| `unsubscribe` 使用 Consumer-direct 例外 | lifecycle、Consumer、task-bound operation 及 loopback E2E 通过 | 已验证 |
| 退订使用独立 headless 浏览器 profile | 34 个 loopback browser tests 通过 | 已验证 |
| 退订 terminal result text 可追溯 | receipt、digest、步骤和 Email projection 测试通过 | 已验证 |
| 低置信度邮件进入待反馈 | 冷启动和 threshold/eligibility fail-closed 测试通过 | 已验证 |
| subscription 自动门槛达到 precision >= 0.95、support >= 20 | 当前只有 provisional 样本，且 support 不足 | 未满足，保持关闭 |
| user-confirmed 时间顺序 holdout | 当前尚未积累足够 user-confirmed feedback | 待实验 |
| 主分支合并、launchd 重启和线上 readback | 当前仍在独立工作树，未部署 | 待部署操作 |
| 高置信度 direct action 真实小批量验收 | 已获得外部效果授权，但当前没有合格 active model | 保持关闭，待模型门槛 |

该矩阵的“已验证”只表示独立工作树中的实现和测试证据，不表示主分支已经
合并，也不表示生产服务已经加载这些代码。当前唯一需要产品决策的动作是先否
允许以 disabled 配置合并，并随后开启只读 shadow；在此之前所有外部邮箱写动作
继续关闭。
