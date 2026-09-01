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

## 分类质量、fastText 对照和反馈学习实验

2026-08-31 重新只读取得先前 73 条 assistant provisional annotation 对应的邮件，并在内存中使用当前生产 `email_message_to_text` 路径脱敏。没有写入原始邮件文件、没有下载附件、没有执行 provider 写操作。生成的临时脱敏训练集为 73 条，类别分布是：

- `billing=3`
- `important=11`
- `junk=26`
- `notification=7`
- `subscription=12`
- `work=14`
- `personal=0`
- `shopping=0`

该临时脱敏数据的 SHA-256 为 `e038e5e2309f22958ae5f1c887b988ae46b20503299332bf583e8c772fba74e9`。哈希只用于本次实验一致性检查；数据不进入 Git，也不进入生产反馈库。

### CPU 和模型对照

当前 word-unigram TF-IDF + balanced Logistic 在 73 条数据上的时间顺序 70/30 结果仍为 `45.45% Accuracy / 38.47% Macro F1`；严格单线程纯预测 P95 为约 `0.55 ms`，包括标准化和 jieba 的既有端到端复测 P95 为约 `6.0 ms`，模型约 `339 KB`。100 ms 延迟目标有充分余量。

同一数据上重跑 fastText 对照后：

- 最好的本轮时间顺序候选约为 `50.0% Accuracy / 26.19% Macro F1`；
- `important` recall 仍为 `0`；
- 0.5 以上没有可自动处理样本；
- 预测 P95 约 `0.17 ms`，但未量化模型约 `26.4 MB`；
- 当前 fastText 0.9.3 的单线程 softmax 训练在该小样本上稳定出现 `Encountered NaN`，双线程或 OVA 路径可以训练，但 OVA 质量不可用。

因此延迟不是模型选择瓶颈。当前数据不支持用 fastText 替换 sparse Logistic；继续保留 TF-IDF + Logistic，避免以更大的模型和更不稳定的训练换取没有质量收益的预测速度。

### 新随机 40 条分布外 holdout

随后从当前 2,283 个 INBOX UID 中使用固定随机种子 `2026083102` 随机抽取 40 封。第一步只读取邮件头并由 assistant 按现有八分类语义做 provisional annotation；第二步对同一 UID 做 readonly 文本读取，附件仍只保留 metadata，立即转换为脱敏 `model_text`。没有保存原始邮件文件、没有下载附件、没有创建 task、没有执行邮箱写操作。

新 40 条标签分布是：

- `billing=5`
- `important=10`
- `junk=1`
- `notification=14`
- `work=10`
- `personal/shopping/subscription=0`

临时 holdout SHA-256 为 `cc9d99ee22f6b4b61061ffedec8e94d375b7135e2d5db59c6ba15e824245d68a`。40 条中有 39 个唯一脱敏模板，与生产特征版 73 条训练集只有 1 条完全相同的脱敏文本。

用 73 条训练、40 条独立 holdout 测试时，word-unigram、word-bigram、char 2–5 和 word+char 四种 sparse Logistic 都只有约 `20% Accuracy / 13% Macro F1`。这不是预处理版本错配：将 73 条训练数据和 40 条 holdout 全部重建为当前生产特征后，结果不变。根因是旧 73 条实验集明确排除了登录验证/验证码邮件，而新随机样本中 `notification` 有 14 条，主要来自这一新分布；旧模型完全没有预测出 `notification`。

### 反馈批量重训模拟

保持随机 40 条的最后 10 条为固定未见测试集，只将前面的 provisional labels 逐批加入 73 条训练集：

| 新反馈数量 | 固定最新 10 条 Accuracy | notification Precision / Recall | 训练时间 | 模型大小 |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 10% | 0% / 0% | 约 16 ms | 约 343 KB |
| 5 | 10% | 0% / 0% | 约 17 ms | 约 357 KB |
| 10 | 10% | 0% / 0% | 约 16 ms | 约 368 KB |
| 20 | 50% | 100% / 80% | 约 19 ms | 约 393 KB |
| 30 | 50% | 100% / 80% | 约 20 ms | 约 404 KB |

这说明反馈保存后做轻量批量重训能够快速学会新的重复模板，但没有解决类别覆盖：固定测试中的 `work` recall 仍为 0。全部 113 条 assistant provisional annotations 做五折 OOF 时为 `54.87% Accuracy / 50.71% Macro F1`，其中：

- `notification`: `84.21% precision / 76.19% recall`
- `important`: `47.06% precision / 38.10% recall`
- `subscription`: `57.14% precision / 66.67% recall`
- `billing`: `11.11% precision / 12.50% recall`

113 条 OOF 的最大 top-1 confidence 只有 `0.3109`；在生产 0.85 threshold 下自动覆盖率仍为 0。所有标签均为 assistant provisional annotations，不是 user-confirmed gold feedback，因此不能用于 active model promotion、类别 eligibility 或自动退订 support。

### 当前实验结论

1. 保留 TF-IDF + balanced Logistic；100 ms 目标已经满足，不为更低的亚毫秒延迟改用质量更差、训练更不稳定且体积更大的 fastText。
2. 当前只适合 readonly scan + Email 待反馈；不得开放任何 model-only provider action。
3. feedback → debounce → batch retrain 的生产结构有效，但纯随机抽样会继续集中在 work/important/notification，无法补齐 `personal`、`shopping`、`subscription` 和低样本 `billing`。
4. 后续实验需要“定向补稀有类别 + 保留随机漂移样本”，并建立 user-confirmed、时间顺序 holdout；assistant annotations 只用于研究方向，不进入生产反馈库。
5. 当前生产代码不因本次实验更换模型；新增工作只更新实验记录和 CEO Agent 激活方案，生产配置继续 disabled。

## Email worker 默认 disabled 烟测

在隔离的临时 SQLite 数据库上直接启动 `email-worker` CLI，数据库没有任何
`email_accounts` 配置。进程输出 `email-worker waiting_configuration
reason=empty_accounts`，没有启动扫描、Consumer 或训练组件；该进程按设计继续
等待配置，随后由本轮实验显式结束。临时数据库、WAL/SHM、workspace、corpus
和环境文件均已删除并复核不存在。这个烟测只证明默认空配置的启动边界，不代表
生产 launchd 已部署或 Email shadow 已启用。

## 新一轮随机邮件头分布抽样

在用户允许随机读取而不是按 cursor 顺序读取后，使用随机种子
`2026083103` 从 INBOX 抽取了 80 封邮件头。此次只执行了 readonly
`select`、UID search 和 header fetch，没有读取正文、下载附件、保存原始邮件或
执行任何邮箱写操作。assistant 仅根据邮件头的主题和发件域做 provisional
annotation，分布为：

- `important=26`
- `notification=18`
- `work=15`
- `junk=10`
- `billing=8`
- `subscription=2`
- `personal=1`
- `shopping=0`

这不是新的模型 holdout，也不是 user-confirmed feedback；它只用于观察真实邮箱
分布和选择下一批只读文本实验。它与前一轮 40 条样本不能未经 UID 去重就合并为
独立样本量。当前结果再次说明，纯随机抽样仍然几乎覆盖不到 `shopping`，并且
`subscription` 与 `personal` 支持很低；后续应继续保留随机漂移监测，同时定向补
稀有类别和低置信度样本。生产配置继续 disabled。

随后尝试使用同一 SSL IMAP 连接做 `UID SEARCH HEADER` 定向统计。阿里云服务端对
`HEADER List-Unsubscribe` 和已知存在的 Subject 关键词都返回 0，不能作为有效的
header-search 统计接口；本次也未据此推断邮箱没有退订头。有效证据仍只限于实际
`BODY.PEEK` header fetch 的随机样本结果。后续若要估计退订头覆盖率，应使用受限的
readonly header fetch 扫描或服务端支持的其他只读分页接口，并单独记录成本与采样偏差。

随后使用 `UID FETCH` 分段读取所有当前 INBOX 的有限 header 字段，分段大小为 100；
2,282/2,282 个 UID 均成功解析，没有失败分段。当前邮箱快照中
`List-Unsubscribe=0`、`List-Unsubscribe-Post=0`、`Auto-Submitted=0`。这次全量
header-only 扫描可以作为当前快照的覆盖率证据，但不能推断未来邮件不会带退订入口，
也不能替代后续新邮件的持续观察。扫描仍未读取正文或附件，未执行任何邮箱写操作。

在同一随机种子 `2026083103` 的 80 个 UID 上，又按生产 `ImapReadonlyAdapter`
读取了标准纯文本部分；附件仍只保留 metadata，原始正文只存在进程内存。用这些
assistant provisional annotations 做随机 5-fold OOF，得到 `67.50% Accuracy /
47.24% Macro F1`，特征语料脱敏 SHA-256 为
`e81c24f2a2da68f7f10467e5c812a1d6d669185c6e0be61164369783d96359b2`。按类别：

- `notification`: `93.75% precision / 83.33% recall`，support 18；
- `important`: `68.97% precision / 76.92% recall`，support 26；
- `billing`: `60.00% precision / 75.00% recall`，support 8；
- `work`: `57.14% precision / 26.67% recall`，support 15；
- `junk`: `52.94% precision / 90.00% recall`，support 10；
- `subscription`: `0% precision / 0% recall`，support 2；
- `personal`: `0% precision / 0% recall`，support 1。

这次是随机 OOF，不是时间顺序 holdout，而且标签只根据邮件头做了 provisional
标注；它不能和前一轮独立 40 条 holdout 直接比较，也不能用于 model promotion、
类别 eligibility 或自动退订 support。它只说明生产特征路径对当前重复的通知分布有
学习信号，同时暴露出 subscription/personal 样本仍然不足，work recall 仍不稳定。

## 尚未开放的门槛

- 当前 DingTalk 企业邮箱配置仍保持 disabled；没有把实验标注当作用户 gold feedback，也没有自动启用模型或动作。
- unsubscribe 仍遵守订阅来源级门槛：precision >= 0.95 且 support >= 20；冷启动仅允许用户确认后的订阅来源进入自动化候选。
- 真实邮箱实验只允许 readonly header/metadata 抽样；生产启用前仍需独立 review、全量回归和用户确认。
- 外部邮箱回复等写动作仍需现有 Audit Agent 生命周期；unsubscribe 是已批准的唯一 Consumer-direct 例外。

## 2026-08-31 全量回归复核

在独立 Email 工作树使用普通 conda Python 完成一次全量测试：

- 结果：`5410 passed, 91 skipped, 40 deselected, 9 failed, 5 warnings`，耗时约 319 秒；
- 9 个失败均不属于 Email 路径，分别位于 Audit Web（4）、Console 健康探针（2）、会议对齐发送时序（2）和设置页导航（1）；
- 本轮选择的 Email 回归集合（`tests/test_email*.py`、`tests/test_mail_review_skill.py` 和文档契约）执行为 `644 passed, 5 warnings`，没有 Email 失败；warning 仍是既有 path-based model promotion deprecation；
- 本次验证没有修改源代码、没有连接真实邮箱、没有写邮箱，也没有重启主 launchd。工作树保持干净。

因此当前 Email 代码的本地专项基线是通过的，但整个仓库仍不能报告为全绿；上述 9 个非 Email 失败需要由对应模块单独处理，不能作为 Email 集成已可生产激活的证据。

## 2026-08-31 浏览器验收复核

首次在受限沙箱中开启浏览器测试时，Playwright 启动系统 Chrome 和自带
Chromium 都在启动后收到 `SIGABRT`，结果为 `1 passed, 33 errors`；这发生在
浏览器 fixture 建立阶段，不是应用断言失败。最小启动诊断也复现了相同现象。

随后在正常本机进程权限下，用同一套 Playwright 依赖和自带 Chromium 重跑：

- 最小 headless Chromium 启动：通过；
- `WORKBENCH_BROWSER_TESTS=1 pytest -q tests/browser/test_email_unsubscribe_browser.py`：`34 passed`，约 26 秒；
- 测试只访问 loopback fixture，没有访问真实邮箱或真实退订站点；
- 工作树没有因浏览器测试产生未提交源代码改动。

因此浏览器验收代码基线通过；受限沙箱中的失败记录为执行环境限制，不应被当成 Email 浏览器实现失败。生产验证仍不得借此替代真实部署后的健康检查，也不得在未获单独授权时访问真实退订站点。
