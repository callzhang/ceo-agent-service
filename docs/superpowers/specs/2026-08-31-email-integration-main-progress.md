# Email integration main 工作树进展记录

更新时间：2026-08-31

## 当前边界

本记录对应独立工作树：

`/Users/derek/Documents/Projects/ceo-agent-service/.worktrees/email-integration-main`

分支从 `main` 的 `aaac3fe5bef2b70af4dcd894c551e55097277d4f` 建立。所有改动只在该工作树中验证；没有修改主工作树、没有重启生产 launchd、没有启用真实邮箱扫描，也没有执行真实邮箱写操作。

## 已移植能力

1. Email classifier 的只读扫描、模型 registry、训练/反馈和模型版本化核心。
2. Email 消息线程上下文和 `In-Reply-To` / `References` 元数据持久化；附件只保留 metadata。
3. Email 页面所需的分类详情、学习反馈和 task producer。
4. 独立 Email worker：扫描与确定性 provider action、Email Agent/Audit consumer、训练 scheduler 为三个独立组件。
5. 所有 Email Agent task payload 带显式 `lifecycle_version`：
   - `auto_reply` → `consumer_audit_v1`，经过 Consumer → Audit；
   - `unsubscribe` → `email_unsubscribe_consumer_direct_v1`，由独立 unsubscribe Consumer 执行并记录 observability，不创建 Agent/Audit run。
6. `unsubscribe` 的生命周期选择 fail-closed：只有任务、上下文、原始 payload、分类、action identity 全部一致且为当前版本时才允许直通；其他任何不一致都走普通 Consumer → Audit。
7. 直接 provider action 具备 claim、租约恢复、有限重试和退订 terminal result / observation digest 持久化。

## 验证证据

已提交的核心批次：

- `5327fc2e fix: persist email thread metadata`
- `8455c52c feat: integrate email readonly classifier core`
- `8e784d07 feat: integrate email learning and task projection`

第三批包含 Email worker、unsubscribe Consumer/operation、browser profile/context source、provider action 生命周期、严格 task lifecycle 选择，以及对应测试。

随后补齐了实验快照、unsubscribe Consumer/context source、direct-consumer e2e 测试，以及受控 CLI 中唯一的 `execute_email_unsubscribe` 工具接线；这些改动已在当前独立工作树提交，尚未合并或部署。

## Console 集成

Email Console 已接入独立页面和全局导航 `/email`，页面包含四个分区：

- `已处理`：展示已有最终分类的邮件及处理详情；
- `待反馈`：展示模型建议和置信度，由用户确认最终分类；
- `邮件配置`：维护类别描述、阈值、启用状态和固定动作；
- `学习`：展示当前/历史模型版本、训练时间、样本数量、准确率、Macro F1 和预测延迟。

已处理详情只展示持久化的 observability，包括 provider action、自动回复和自动退订结果；退订的最终结果页文字、步骤和 observation digest 可追溯，但不展示私密 URL 或附件正文。

前端验证为 `29 个测试文件、270 个测试通过`，TypeScript 检查和 Vite production build 均通过。构建过程中补齐了现有 History 图表已经使用但依赖声明缺失的 `recharts`。

当前 Email 扩展回归命令覆盖 classifier contracts、connector config、store、只读 IMAP、model、registry、training、learning、runtime、scan、pipeline、provider actions、task adapter、reply delivery、unsubscribe、worker、web API、task lifecycle、Consumer、context source、实验快照和 Agent CLI，共：

最终 Email 扩展回归为 `675 passed, 1 skipped, 5 warnings`。

warning 来自既有 path-based model promotion deprecation，不影响本批次通过。

## 真实邮箱随机只读实验

2026-08-31 使用当前集成工作树中的 IMAP readonly adapter，对已配置的 DingTalk 企业邮箱 `INBOX` 做了一次隔离实验：

- 连接方式：IMAPS `imap.qiye.aliyun.com:993`，只执行 readonly select、UID SEARCH 和 `BODY.PEEK`；没有 STORE、COPY、MOVE、EXPUNGE、SMTP 发信或其他邮箱写操作；
- 抽样方式：不依赖生产 cursor，在本次运行内用固定随机种子 `20260831` 从当前 UID 集合随机抽取 8 封；
- 数据边界：分类器只使用规范化邮件输入，持久化仅写临时隔离 SQLite，实验结束后删除；输出和文档不记录发件人、主题、正文、URL 或 UID；
- 结果：当前 INBOX 观察到 2,283 个 UID，随机抽样/抓取/持久化均为 `8/8`；冷启动配置下自动资格关闭，因此 `processed=0`、`pending_feedback=8`，Email task producer 未提供，`mailbox_writes=0`；
- 实验标注：这是用于验证链路的 `experiment-metadata-v1` 临时标注，不是用户 gold feedback；按只读 metadata 规则得到 `important=7`、`notification=1`；
- 隐私检查：隔离库中的 `model_text` 未发现未脱敏 URL 或邮箱地址（均为 `0` 行）。

本次实验验证了“真实 IMAP → 随机抽样 → 规范化 → classifier → 冷启动待反馈 → 本地持久化”的闭环，但不能据此宣称分类准确率，也不能作为自动退订的 precision/support 资格证据。后续需要通过用户反馈积累各订阅来源的独立样本，再按 `precision >= 0.95` 且 `support >= 20` 评估自动退订资格。

实验过程中仅发现临时实验适配器的两个字段形状错误，均已在重跑前修正；当前生产代码和工作树文件没有因该实验修改。

## 尚未开放的门槛

- 当前 DingTalk 企业邮箱配置仍保持 disabled；没有把实验标注当作用户 gold feedback，也没有自动启用模型或动作。
- unsubscribe 仍遵守订阅来源级门槛：precision >= 0.95 且 support >= 20；冷启动仅允许用户确认后的订阅来源进入自动化候选。
- 真实邮箱实验只允许 readonly header/metadata 抽样；生产启用前仍需独立 review、全量回归和用户确认。
- 外部邮箱回复等写动作仍需现有 Audit Agent 生命周期；unsubscribe 是已批准的唯一 Consumer-direct 例外。
