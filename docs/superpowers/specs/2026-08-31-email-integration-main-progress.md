# Email integration main 工作树进展记录

更新时间：2026-09-02

## 当前边界

本记录对应独立工作树：

`/Users/derek/Documents/Projects/ceo-agent-service/.worktrees/email-integration-main`

分支当前为 `codex/email-integration-main`。所有改动只在该工作树中验证；没有修改主工作树、没有重启生产 launchd、没有启用真实邮箱扫描，也没有执行真实邮箱写操作。

2026-09-02 Derek 已明确授权高置信度类别的确定性邮件处理，包括 label、mark_read、archive、move 和 trash；trash 仅进入可恢复 Trash，禁止永久删除。当前明确禁止所有 Email 回复：配置 API、worker runtime 和 `ceo-mail-review` skill 均不得生成或发送 `auto_reply`，SMTP 不启用。该授权不绕过模型/类别 eligibility，也不改变 unsubscribe 的订阅级 precision/support 门槛。

## 已移植能力

1. Email classifier 的只读扫描、模型 registry、训练/反馈和模型版本化核心。
2. Email 消息线程上下文和 `In-Reply-To` / `References` 元数据持久化；附件只保留 metadata。
3. Email 页面所需的分类详情、学习反馈和 task producer。
4. 独立 Email worker：扫描与确定性 provider action、Email Agent/Audit consumer、训练 scheduler 为三个独立组件。
5. 所有 Email Agent task payload 带显式 `lifecycle_version`：
   - `auto_reply` 历史 contract 保留用于兼容读取，但当前 runtime/API/skill 全局禁用，不创建任务、不连接 SMTP；
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

## 2026-09-02 随机只读正文与时间漂移实验

在用户授权后续高置信度确定性动作、同时明确禁止邮件回复后，本轮仍先保持
provider 零写入，只验证真实数据读取和模型质量。使用固定随机种子
`20260902` 从当时 2,318 个 INBOX UID 中抽取 80 封邮件头；80/80 均读取成功，
样本中 `List-Unsubscribe`、`List-Unsubscribe-Post` 和 `Auto-Submitted` 均为 0。
这只是随机样本证据，不能推断未来邮件不会提供这些头，也不构成退订来源的
precision/support 证据。

随后在邮箱新增一封邮件、UID 总数变为 2,319 后，使用生产
`ImapReadonlyAdapter` 对固定随机样本做 BODYSTRUCTURE 和受限正文读取。20/20 封
邮件均得到非空 `textBody`；正文长度中位数为 862 字符，P95 为 11,122 字符；
共识别 29 个附件 metadata，单封最多 16 个，没有读取附件字节。初次诊断曾把
标准化字段 `textBody` 误写成不存在的 `body`，导致错误报告 20/20 正文为空；
更正探针字段并复跑同一批样本后确认生产适配器没有该问题，因而没有修改实现。

另一组固定随机种子 `2026090201` 的 30 封邮件由 assistant 根据脱敏 header 和
受限正文做 provisional annotation，分布为：

- `important=9`
- `work=8`
- `notification=5`
- `junk=5`
- `billing=3`
- `personal/shopping/subscription=0`

按 UID 时间顺序使用较早 20 封训练、较新 10 封 holdout。训练段只有 1 封
`notification`，holdout 中有 4 封，主要是训练段未覆盖的登录和安全通知。生产
word-unigram Logistic，以及 `C=1`、word-bigram、char 3–5 和 word+char 五种
稀疏 Logistic 候选，均只得到 `10.0% Accuracy / 4.4% Macro F1`；
`notification` precision/recall 均为 0。单封 `vectorize + predict_proba` P95
范围为 `0.30–1.89 ms`，仍远低于 100 ms；这段延迟不含已有约 6 ms 的规范化和
jieba 端到端开销。

这次失败 holdout 是有价值的时间漂移证据：扩大模型或换 char 特征没有弥补新模板
样本缺失，当前瓶颈仍是有代表性的反馈覆盖。全部 30 条仍是 assistant provisional
annotations，不进入生产 feedback、model promotion、类别 eligibility 或自动退订
support。下一轮优先从 header 全量快照中定向抽取 `billing`、`shopping`、
`subscription` 和其他低覆盖候选，同时保留独立随机漂移样本；在时间顺序验证达到
类别 precision/support 门槛前，不启用 label、archive、move 或 Trash。

为支持后续累积实验，这 30 条已保存为 Git 忽略目录中的 privacy-bounded snapshot：
`data/email-experiments/2026-09-02-random-30.json`。snapshot 只保存脱敏
`model_text`、label、日期、消息/来源 digest，不保存 UID、发件人、主题、原始正文
或 URL；`label_source=assistant_authorized_manual_annotation`，digest 为
`518b6b7ee250ba184fedd489eacf606421e53272b7633df88f113da12d5f81b4`。重新加载
校验通过。该本地文件不进入 Git，也不等同于生产用户 feedback。

### 稀有类别候选发现结果

阿里企业邮箱对单次大 UID range 的 header FETCH 在 90 秒内仍未完成，因此主动
终止；这条服务端路径不适合在线候选发现或生产扫描。改为固定种子
`2026090202` 随机抽取 160 个 UID 并逐封读取有限 From/Subject，160/160 成功。
关键词仅产生 `billing=4`、`shopping=4`、`subscription=2`、`personal=1` 个
候选，人工复核发现明显语义碰撞：业务订单/交付不是个人 shopping，企业邀请不是
personal，Google Ads 周报也不自动等于用户不想继续接收的 subscription。因此
header 关键词只能做 review 排序，不能生成标签或 provider action。

在同一随机源的 60 封受限正文中检测退订/偏好入口措辞，共命中 4 封。人工语义
复核后，LinkedIn 邀请接受属于 notification，Google Ads 周报更接近
billing/notification；保险促销和 OpenAI 产品更新是 subscription 候选，候选
precision 约 50%。邮箱服务端 `SEARCH BODY "unsubscribe"` 与 UID 变体都返回
`BAD invalid command or parameters`，不能用于低成本全库候选发现。生产方案应继续
采用小批 readonly 新邮件扫描和 active-learning 排序，不依赖大范围 FETCH 或
服务端 BODY/HEADER 搜索。

这 4 个候选已按 `notification=1`、`billing=1`、`subscription=2` 保存为另一份
Git 忽略的脱敏 snapshot：
`data/email-experiments/2026-09-02-unsubscribe-signal-4.json`，digest 为
`50f9dee93c838b071033d76958ce6a5c5420aa9da634e8334d3a76a497d7f6b0`。
将信号候选与最终语义标签分开，避免把正文出现 unsubscribe 的通知或报表直接训练
成 subscription；其中 2 条 assistant subscription 标签仍不计入自动退订的
user-confirmed 来源级 support。

### 第二批随机标注、脱敏缺口和累积验证

从不与第一批 30 封重叠的 UID 中，使用固定种子 `2026090203` 再随机抽取 30 封，
assistant provisional annotation 分布为：

- `notification=12`
- `important=7`
- `work=4`
- `junk=4`
- `subscription=2`
- `billing=1`

在生成 snapshot 前，脱敏诊断发现既有 `_clean` 只覆盖部分 token 前缀，未覆盖
Quota Report Hub 的 `qrp_...` 和 `qrp....` 形式。该批数据当时没有保存。通过红测
复现后，Email 模型清洗器现会在 jieba 分词前将两种形式替换为 `TOKEN`，持久化和
实验 snapshot 边界也会拒绝绕过清洗器传入的同类 token。相关 Email 回归为
`649 passed, 5 warnings`。修复提交为 `eebe00d1 fix: redact email access tokens`。

修复后只读复取同一批样本并保存为 Git 忽略的
`data/email-experiments/2026-09-02-random-30-b.json`；落盘内容中两种 token marker
均为 0，snapshot digest 为
`e735ff9bf8dbe8588af698136e8414335000744fc2371ed0c24091311e0c9af7`。

两批互不重叠的随机 snapshot 合计 60 条，分布为
`notification=17`、`important=16`、`work=12`、`junk=9`、`billing=4`、
`subscription=2`。按接收日期排序（同日按样本 digest 稳定排序）的前 48 条训练、
后 12 条 holdout：

- 生产 word-unigram Logistic：`66.7% Accuracy / 41.7% Macro F1`；
- word-bigram + char 3–5 Logistic：`58.3% Accuracy / 43.9% Macro F1`；
- 生产模型在普通分层 OOF 和按来源分组 OOF 中均为 `51.7% Accuracy`，Macro F1
  分别为 `35.5%` 和 `36.2%`；这批 60 条的小样本当时未观察到差异，但后续 263 条
  `LeaveOneGroupOut` 已推翻“没有来源虚高”的解释，见文末来源整体留出验证；
- 时间 holdout 中 notification 为 `100% precision / 100% recall`（support 6），
  billing 为 `100% / 100%`（support 1），junk 为 `50% / 50%`（support 2），
  important、subscription 均为 0；
- 最高 top-1 confidence 仅 `0.2011`，0.50/0.70/0.85 阈值均为零覆盖。

将 Logistic `C` 从 0.25 扫描到 64 只能制造更高但未经支持的置信度：`C=16/64`
时有 3/12 封超过 0.5 且本次恰好全对，但整体 Accuracy 降为 50%，0.7 和 0.85
仍零覆盖，候选 support 只有 3。因此保留生产 `C=0.25`，不通过调大 C 绕过类别
precision/support 门槛。当前正确结论仍是“模型已学到部分重复通知模板，但没有任何
类别达到自动 provider action 的证据要求”。

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

## 2026-08-31 新一轮随机只读头部抽样

为确认前一轮 mailbox 快照没有因读取方式或样本选择而产生明显偏差，使用同一
邮箱连接重新执行了一次独立随机抽样，固定种子为 `2026083104`：

- 当前 INBOX UID 总数：`2282`；随机抽样：`60`；头部读取成功：`60/60`；
- 只读取 `Date`、`List-Unsubscribe`、`List-Unsubscribe-Post` 和
  `Auto-Submitted`，使用 readonly `select` 和 `BODY.PEEK`；
- 本轮样本中 `List-Unsubscribe=0`、`List-Unsubscribe-Post=0`、
  `Auto-Submitted=0`；
- 没有读取正文、下载附件、保存原始内容、创建任务或执行邮箱写操作；密码只
  在本次进程内存中使用，未写入仓库或实验文件。

该结果与此前对 `2282/2282` 个 UID 的 header-only 快照一致，增强了当前快照的
重复采样证据，但不能推断未来邮件不会提供退订入口，也不能作为自动退订资格或
订阅来源 support 的证据。

## 2026-09-02 扩展标注、模型稳定性与 notification 最终验证

在固定邮箱快照（2,319 个 UID，最大 UID `32425`）上继续完成了 8 份隐私受限
实验快照。所有读取仍为 IMAP readonly；没有创建 Email task、没有连接 SMTP、
没有执行 provider 写操作。8 份快照共 264 行，标签总分布为：

- `notification=78`
- `junk=59`
- `important=48`
- `work=33`
- `subscription=26`
- `billing=20`

快照重新加载和 SHA-256 校验全部通过。按 `sample_id_digest` 检查发现 D、F 两批
各包含同一封 `junk` 邮件，因此 264 行对应 263 封唯一邮件。最终 holdout 重新按
消息摘要去重；这条重复不属于 `notification`，不会改变 notification 的候选数、
precision 或 recall，但去重后的整体指标以 79 条而不是 80 条为准。

### 候选模型和特征消融

在最初 60 条的较早 48 条训练、较新 12 条 holdout 上：

| 候选 | Accuracy | Macro F1 | 高置信度结论 |
| --- | ---: | ---: | --- |
| 生产 balanced Logistic，`C=0.25` | 66.7% | 41.7% | 最大 confidence 0.201，无 0.5+ 候选 |
| ComplementNB，`alpha=0.05` | 66.7% | 43.2% | 高 confidence precision 约 75%，不安全 |
| SGD log-loss，`alpha=1e-5` | 75.0% | 80.8% | 本切分 5 个 0.85+ 全对，但跨切分失效 |

SGD 的表面优势没有通过稳定性验证：换时间切分后 0.85 门槛 precision 降到 70%；
分层 OOF 为 69.4%–81.2%，按来源分组 OOF 为 60%–71.4%。MultinomialNB 质量更差
且更过度自信。取消 `class_weight=balanced` 后，多个时间切分 Accuracy 只有
8.3%–50%，Macro F1 只有 5.6%–28.6%。因此继续保留 balanced Logistic，
`C=0.25`；不能用过度自信的模型制造“高置信度”。

在互不重叠的三批随机样本 A30、B30、C40 合计 100 条上，按日期保留最新 20 条：

- Logistic：`55% Accuracy / 25% Macro F1`，最大 confidence 0.222；
- SGD：`45% / 44.4%`，0.85+ precision 66.7%；
- ComplementNB：`60% / 31%`，只有 1 个 0.85+ 候选；
- Logistic 对最新 20 条中的 notification 为 `100% precision / 100% recall`
  （support 10），但 `important`、`work`、`subscription` 仍不可靠。

把 4 条含退订/偏好入口措辞的定向样本加入训练后，Logistic Accuracy 从 55%
降到 30%，notification recall 从 100% 降到 10%，subscription 虽然 recall 为
100%，precision 只有 18.2%。同一来源会同时发送 notification、billing 和
subscription，这证明“来源”或“出现退订文字”不能直接决定类别。subject boost、
CSS 清理和正文截断都没有修复；subject-only 虽达到 70% Accuracy，但把
`important` recall 降到 0，因而不修改生产特征。

### 时间漂移学习模拟

用较早随机 80 条训练、D40 测试时，balanced Logistic 得到
`72.5% Accuracy / 43.5% Macro F1`。notification 在 confidence `>=0.20` 时
有 15 个自动候选，其中 14 个正确；唯一误报是 LinkedIn 冷销售邮件。加一个
margin 条件后本批为 14/14，但该条件没有跨批稳定价值。

同一旧模型直接测试 E40 时，`>=0.20` 且带 margin 的 notification 候选只有
14/20 正确；误报包括需要处理的 Vercel 邮件、LinkedIn subscription 和需要登录
恢复的 Quota 邮件。这再次表明 notification 与 important/subscription 的边界必须
通过新反馈学习，不能靠固定关键词。

加入 D40 反馈后再测试 E40：`70% Accuracy / 63.9% Macro F1`，在预先评估的
notification confidence `>=0.25` 下为 15/15，recall 93.8%。再加入 4 条定向样本
后整体提高到 `75% / 73.9%`，notification 仍为 15/15。由此冻结
`notification >=0.25`，不再查看后续 F/G 批来调门槛。

### 最终未见 holdout

使用“较早随机 80 条 + D40 + 4 条定向样本”训练，只在冻结门槛后评估 F40 与
G40。去除与训练集重复的 1 条 `junk` 后，最终 holdout 为 79 条，分布是
`notification=21`、`junk=20`、`subscription=16`、`important=11`、
`billing=6`、`work=5`：

- 整体：`63.29% Accuracy / 54.55% Macro F1`；
- notification `confidence >=0.25`：19 个候选，19 个正确，precision 100%；
- notification positive support：21；recall 90.48%；
- `0.22`、`0.25`、`0.27` 三个门槛在该 holdout 上均为 19/19；
- `0.20` 会扩大到 25 个候选、其中仅 19 个正确，precision 降到 76%；
- `0.30` 为 17/17，但 recall 降到 80.95%。

因此 notification 已形成值得进入生产影子链路的类别级信号，且无需新增 margin
规则。不过这 21 个 positive support 全部是
`assistant_authorized_manual_annotation`，不是生产 `user-confirmed` feedback；同时
当前非 subscription 类别的正式默认门槛仍是 30 个 validation positive samples。
所以本结果只批准开发“可追溯候选模型 + 只读 shadow 评估”，不能把 notification
直接标记为生产 `auto_action_eligible`，也不能据此执行真实移动或 Trash。

### 当前授权与下一步

Derek 已明确授权未来对满足类别门槛的高置信度邮件执行 label、mark-read、archive、
move 和可恢复 Trash；Trash 永不执行 EXPUNGE 或永久删除。任何 Email 回复都明确
禁止，SMTP 保持关闭。下一步先把上述脱敏快照接入不会污染生产 feedback 的候选模型
训练/registry 路径，生成完整版本号、训练时间、样本数、类别指标和延迟，并对真实
新邮件做只读 shadow scan。只有 user-confirmed 数据达到正式类别门槛后，才允许
对应确定性动作进入受控小批执行。

### 第一版生产影子候选

新增 `app.email_classifier_shadow.stage_snapshot_shadow_candidate`，把隐私受限 snapshot
转成不可变 registry candidate，同时保持以下硬边界：

- 只接受 `assistant_authorized_manual_annotation` snapshot；
- 训练集内出现重复消息时拒绝生成模型；验证集与训练集或验证集内部重复时排除并计数；
- 记录完整 `model_id`、artifact SHA-256、训练时间、训练/验证样本数、类别指标和
  p50/p95 延迟；
- 每个类别都写入 `auto_action_eligible=false` 和
  `non_authoritative_validation_labels`；
- 只登记 `candidate`，不创建或切换 active manifest，也不更新生产 feedback 的
  `included_in_model_id`。

使用 A30+B30+C40+D40+定向 4 条共 144 条训练，F40+G40 做最终验证。自动排除
D/F 重复的 1 条后，第一版候选为：

```text
model_id: email-tfidf-lr-20260902T192218Z-6d8a1ca9
status: candidate
reason: shadow_only_non_authoritative_labels
training samples: 144
validation samples: 79
Accuracy / Macro F1: 68.35% / 62.18%
prediction latency p50 / p95: 0.31 ms / 0.61 ms
notification >= 0.25: 19/19, precision 100%, recall 90.48%, positive support 21
active manifest: none
```

对应 focused model/snapshot tests 为 `78 passed, 5 warnings`；warning 仍来自既有
path-based promotion deprecation。候选 artifact 位于 Git 忽略的本地
`data/email-shadow-models/`，不包含原始邮件或凭据。它可以用于后续真实新邮件的
readonly shadow prediction，但当前不能触发 label、move、archive 或 Trash。

### 真实邮箱只读 shadow smoke

随后使用该 candidate 对真实 DingTalk 企业邮箱做一次独立 readonly smoke。固定随机
种子 `2026090210`，从当前 2,321 个 INBOX UID 的最近 500 封中随机抽取 10 封，
只通过生产 `ImapReadonlyAdapter` 读取 header、BODYSTRUCTURE 和受限文本部分：

- 模型预测分布：`billing=3`、`important=1`、`junk=3`、`notification=3`；
- `notification >=0.25` 候选：3；通用 `confidence >=0.85` 候选：0；
- mailbox writes：0；SMTP connections：0；attachments downloaded：0；
- 未输出或持久化发件人、主题、正文、URL、UID 或附件名。

这次没有 assistant gold label，因此只能验证真实输入兼容性和候选覆盖率，不能把
3 个 notification 候选计入 precision/support。逐封 IMAP 网络读取 10 封约 34 秒，
明显慢于模型 P95 0.61 ms；生产应依靠 UID cursor 只处理新增邮件，并保持小批串行，
不能把大范围随机回扫的耗时归因于分类器。

完整测试时，shell 通配符还收集到 4 个被 `.gitignore` 的本地旧副本
`tests/test_email_* 2.py`，其旧 contract 产生 12 个失败。这些文件不属于 Git
工作树，未修改也未删除。使用 `git ls-files` 选择正式测试后为 `649 passed,
5 warnings`，新增 shadow 测试另为 `1 passed`。提交后重新合并执行为
`650 passed, 5 warnings`。

### 新随机 10 条人工标注

对上述模型加载后的另一批 10 封随机 readonly 邮件输出生产清洗器生成的脱敏文本，
由 assistant 在模型预测冻结后完成标签。标签为：`notification=5`、`billing=2`、
`important=1`、`subscription=1`、`work=1`。top-1 预测正确 8/10：

- `notification >=0.25`：5 个候选，5 个正确，precision 100%；
- notification positive support：5；recall 100%；
- 另有 1 封模型预测 notification 但 confidence 只有 0.2007，人工标签为
  `important`，因此没有越过 0.25 自动门槛；
- 另一个错误是把 LinkedIn 内容摘要预测为 `junk`，人工标签为 `subscription`。

纯摘要标签证据保存在 Git 忽略的
`data/email-experiments/2026-09-02-shadow-10-labels.json`，只含消息摘要 hash、
预测类别/概率和人工标签，不含 model text、UID、来源、主题或正文。文件 SHA-256 为
`2cfe44be5c8f0cc68156d55074c95dec8385441a1815dfbdecbb8d3441ff0923`；重新计算后
指标匹配且 10 个 `sample_id_digest` 唯一。

该批与此前 F/G 的 notification 结果合计为 24/24 自动候选正确、26 个 positive
support，但仍全部是 assistant-authorized 标注，且没有达到当前正式 30-positive
门槛，所以不改变生产 eligibility。

同一 seed 在稍后复跑时邮箱从 2,321 增加到 2,322 个 UID，最近 500 封的滑动池随之
改变，所得样本也改变。固定随机 seed 不能单独构成动态邮箱的实验身份；后续所有
可复核实验以完整 `sample_id_digest` 集合为准，seed 和 UID count 只作为采样说明。

### notification 30-positive 研究复核

继续完成两个互不重叠的小批次。第三批从 15 个随机 UID 中去重后剩 7 条；对两封
正文开头被 CSS 占满的 OpenAI 邮件额外查看脱敏文本末尾后，确认一封是产品推广
`subscription`，另一封是 Flight Watch `notification`。本批 top-1 为 4/7，
notification 正样本 1 条，但模型预测为 subscription，因此 0.25 门槛没有候选。
摘要文件及 SHA-256：

```text
data/email-experiments/2026-09-02-shadow-7-labels-c.json
6040c161210fe5be8ed899354f3d5ae05105dc42dab4d859bfda775abe3c7e57
```

第四批从 20 个随机 UID 中排除既有消息后取得 10 条，人工标签分布为
`work=3`、`notification=3`、`junk=2`、`important=1`、`subscription=1`，
top-1 为 7/10。三个 notification 候选均为明确登录/安全通知，全部在 0.25 以上且
3/3 正确。摘要文件及 SHA-256：

```text
data/email-experiments/2026-09-02-shadow-10-labels-d.json
7f2919fb1d792ce09e5bec3c6ad07deda27c9916b6e59596dc1c445a599e27bf
```

四个新 label-only 批次共 37 条、37 个唯一消息摘要。合并此前去重 F/G holdout 后，
冻结 notification 0.25 门槛的最终研究结果是：

- 自动候选：29；正确候选：29；precision 100%；
- notification positive support：32；recall 90.63%；
- 新增 37 条的 top-1 错误仍集中在 work/important/subscription/junk 边界；
- 新增 10 个自动 notification 候选全部来自同一个登录/安全通知来源模板，跨来源
  notification（例如航班更新）仍可能落在 threshold 以下或被预测成 subscription。

因此 0.25 已跨过“30 个 notification 正样本”的研究门槛，但证据存在明显来源集中，
并且标签仍是 assistant-authorized。当前 candidate 保持不 active，生产 provider action
仍为零。后续不再继续扩大同一邮箱的 assistant 自标样本；融合方案需要明确
assistant 标签是否可以成为 authoritative training data，以及类别 gate 是否需要按已配置
动作的风险采用更严格 precision。

### 来源整体留出验证推翻全局自动动作结论

为验证上述 29/29 是否来自跨来源能力，对八个含 model text 的隐私 snapshot 做了
`LeaveOneGroupOut`：同一个 `source_group_digest` 的所有邮件必须一起进入 holdout，
训练时完全看不到该来源。264 行去除 1 条重复后为 263 封、78 个匿名来源组。

- 整体 top-1 Accuracy `29.66%`，Macro F1 `21.43%`；
- notification `confidence >= 0.25` 只有 `2/11` 正确，precision `18.18%`；
- notification positive support 为 78，recall `2.56%`；
- 11 个候选只来自 3 个来源组，最大单一来源占 `81.82%`。

对原冻结 F/G holdout 再按训练集是否见过来源拆分：

```text
seen source:   49 rows, notification 18/18 candidates correct, support 18, recall 100%
unseen source: 30 rows, notification  1/1  candidate correct, support  3, recall 33.33%
```

原 19 个 notification 候选只覆盖 3 个来源组，其中一个来源贡献 17 个，占
`89.47%`。去掉精确发件人 hash，或同时去掉发件人 hash 与 domain 后，来源整体留出
结果没有实质变化（仍为 `2/11`）；这符合未见 token 本来就不会命中特征的预期，也
说明正文/主题模板本身与来源高度绑定，简单删 sender token 不能得到跨来源能力。

因此此前“notification 类别全局 0.25 门槛可用于自动动作”的解释被否决。当前证据
最多支持继续研究“已见且有独立验证证据的来源”；未知来源必须进入待反馈，不能仅凭
类别置信度执行 label、archive、move 或 Trash。Derek 已授权后续满足证据门槛的高
置信度邮件动作，包括可恢复地移入 Trash，但该授权不替代模型与动作门槛；永久删除、
EXPUNGE、清空 Trash 和任何邮件回复仍禁止。下一批实验改为来源去重/来源均衡采样，
并在冻结预测后由 assistant 标注。

### 两批真实邮箱来源去重 shadow

按上述策略，从动态 INBOX 最近 500/1,000 个 UID 中随机读取邮件头，排除八个训练/
验证 snapshot 和当前批次已经出现的 sender domain；只对入选的新域读取受限文本。
第一批读取 100 个随机 header 后得到 7 个新域，第二批读取 65 个 header 后得到 10
个新域。两批均使用 2,322 个 UID 的 readonly 观察，邮箱写入、SMTP 连接、附件下载
全部为 0。

冻结预测后的 assistant 标注结果：

| 批次 | 新域 | top-1 | notification positive | notification >= 0.25 |
| --- | ---: | ---: | ---: | ---: |
| source-diverse A | 7 | 5/7 | 0 | 0 candidates |
| source-diverse B | 10 | 7/10 | 1 | 0 candidates |

唯一 notification 是一个全新来源的设备断连告警；模型 top-1 为 notification，但
confidence 只有 `0.233675`，被 0.25 门槛拒绝。17 个互不重复且训练时未见的来源中
没有任何 notification 自动候选，因此不会引入新的 precision 分母，也再次确认现有
自动覆盖率主要来自已见模板。新来源中 `junk` 占 12/17，模型也出现 junk 与
work/important/billing 的边界错误；未知来源仍应进入待反馈。

Git 忽略的 label-only 证据不含正文、主题、UID、邮箱地址、URL 或附件，仅含消息和
来源 hash、冻结预测、概率与 assistant 标签：

```text
data/email-experiments/2026-09-02-source-diverse-7-labels.json
sha256 55b8bbeee7ad0bbe88602afe4253a99989efce37f77d3439d4df8eb55d4f6778

data/email-experiments/2026-09-02-source-diverse-10-labels-b.json
sha256 27f9d2654334eb15b6d2115c0f19080fe9ba31b9f9103904d5fd389adac24994
```

第一次操作曾直接 `source` 项目 `.env`，由于文件中存在非 shell-safe 的带空格值，
误触发了一个无关且失败的本地鉴权脚本；它未取得额外授权，IMAP 最终仍只读完成。
后续批次已改为把 `.env` 当纯文本读取，只提取精确的 IMAP secret key，不执行其中
任何内容。该方式作为后续邮箱实验固定操作边界。

### 单分类器表示复核与 junk 候选冻结

在 263 封、78 个来源组上固定使用五折 `StratifiedGroupKFold`，保证每一折的验证来源
在对应训练折中完全未见。候选仍全部是单个线性分类器：

| 表示/模型 | Accuracy | Macro F1 | notification >= 0.25 | P95 | 大小 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TF-IDF word unigram Logistic | 30.04% | 21.27% | 2/13 | 1.59 ms | 548 KB |
| TF-IDF word 1–2 Logistic | 32.70% | 22.92% | 2/12 | 16.35 ms | 2.69 MB |
| TF-IDF char-wb 3–5 Logistic | 32.70% | 23.23% | 1/11 | 4.88 ms | 1.56 MB |
| TF-IDF word+char Logistic | 35.74% | 25.61% | 12/23 | 32.55 ms | 4.25 MB |
| fastText small best | 25.48% | 17.24% | 8/21 | 0.33 ms | 7.34 MB |

fastText 使用已分词中文、`dim=32`、`bucket=50k`，扫描 word n-gram 1/2 与 epoch
50/100。最佳训练集 Accuracy 达 85.55%，来源分组验证只有 25.48%，所有 notification
阈值都远低于 95% precision，属于小数据过拟合。默认 200 万 bucket 的 fastText
模型约 490 MB，也没有更好的验证结果。因此 fastText 不进入当前生产候选；保留更小、
更稳的 Logistic 路径。

来源历史规则也只作为 eligibility 诊断：按接收日期顺序，来源已有至少 2 封且历史
标签完全一致时，后续样本为 37/37，但只覆盖 important/junk/subscription，且有效
来源很少。随后从最近 2,000 UID 随机扫描 800 个 header，试图寻找晚于对应历史的
同来源新邮件，结果为 0；因此不能把回看一致性冒充前瞻证据。

按类别检查未知来源的高精度区间后，只有 junk 值得继续：word+char Logistic 在
threshold `>=0.30603` 时为 24/24、recall 41.38%，覆盖 21 个来源，最大单一来源占
8.33%。为避免浮点边界，冻结实验阈值为 `junk >= 0.31`，并在查看任何后续 holdout
前生成不可变实验候选：

```text
model_id: email-tfidf-word-char-junk-v1-d0fc0d4b
artifact sha256: d0fc0d4b6b8018b993fe1f86aa386beb9813e6a25a6258a46a042cd8ae55e0b4
training: 263 messages / 78 source groups
artifact size: 4,460,415 bytes
measured single-message P95: 7.57 ms
status: frozen_experiment_candidate
auto_action_eligible: false
reason: fresh_source_holdout_pending
```

该 artifact 与 metadata 位于 Git 忽略的 `data/email-experiments/models/`，没有注册为
active，也不能驱动 mailbox action。下一步只用全新来源 holdout 验证冻结的 0.31；
开发/执行 Trash 仍要求独立 holdout 达到 99.5% precision 和足够 positive support。

### junk v1 全新来源 holdout：拒绝

冻结文档和候选提交后，以 seed `2026090215` 从最近 2,000 个 UID 随机扫描 132 个
header，取得 20 个与 263 封训练语料以及此前 17 个来源去重样本均不重叠的新 sender
domain。每个来源只取一封，预测由冻结候选先完成，再按 `ceo-mail-review` 的保守边界
由 assistant 标注；邮箱写入、SMTP 连接和附件下载仍全部为 0。

标签分布为 `junk=8`、`important=7`、`notification=3`、`work=2`，整体 top-1
为 9/20。冻结的 `junk >= 0.31` 选出 7 个候选，其中 6 个为 junk；唯一误判是一封
针对 Stardust 的具体播客采访邀请，属于可能有品牌价值且需要用户关注的机会，不可
自动丢弃：

```text
junk candidates: 7
true junk: 6
precision: 85.71%
junk positive support: 8
recall: 75%
required Trash precision: 99.5%
decision: rejected
mailbox effects enabled: false
```

因此 `email-tfidf-word-char-junk-v1-d0fc0d4b` 已生成独立 rejection lifecycle，不能
进入生产 registry 或执行可恢复 Trash。提高 threshold 也不能挽救 v1：误判分数为
0.360342；阈值超过该值只剩 2 个候选，样本不足，且属于查看 holdout 后调参，不能再
把同一批当最终证据。

Git 忽略的证据及摘要：

```text
predictions + local redacted model text
data/email-experiments/2026-09-02-junk-source-disjoint-holdout-20-predictions.json
sha256 83ff365f619f120f84c8e60b62d04d73334d60cb9fc711268fbf8fc3af723295

label-only evaluation
data/email-experiments/2026-09-02-junk-source-disjoint-holdout-20-labels.json
sha256 3a4beaf6b08b02b2bb68386b98afd99e69f8fc6ed915e7115166befb46f6c153

candidate rejection lifecycle
data/email-experiments/models/email-tfidf-word-char-junk-v1-d0fc0d4b-rejection.json
sha256 3be6598bc9754bfc9a57cff040d5fc292fd9a7d8f8a428ecb2c246dab9f86636
```

这 20 封从现在起可以进入下一版训练池，但不能继续作为下一版 holdout。v2 必须重新
冻结并使用另一批 source-disjoint 邮件验证。当前没有任何类别达到真实写动作门槛，
所以仍只开发/运行 shadow 学习链路，不开发或启用 Trash。

### junk label-only v2 冻结

把 v1 的 20 个全新来源 holdout 正式转入开发训练池后，v2 共有 283 封、98 个来源。
它们不再计作验证证据。相同 word+char Logistic 在五折来源分组开发验证中为：

```text
Accuracy / Macro F1: 36.75% / 26.05%
junk >= 0.312: 29/30
precision: 96.67%
positive support: 66
recall: 43.94%
candidate sources: 28
maximum source share: 6.67%
```

该结果达到普通 label 的 95%研究门槛，但没有达到 archive/Trash 的风险门槛。冻结
候选因此只能研究“增加 Junk 标签”，显式禁止 mark-read、archive、move、Trash、
unsubscribe 和 auto-reply：

```text
model_id: email-tfidf-word-char-junk-label-v2-d94ee93b
parent: email-tfidf-word-char-junk-v1-d0fc0d4b (rejected)
artifact sha256: d94ee93b5ef73e3bda7af4d7d9d9544e72c3ff42cb390be4dd81c28f40d0d6c3
metadata sha256: 1b1ef381fdaf6dc299e4155f8f70d1920452857b13f904c4ba46edddf3b9c5b0
training: 283 messages / 98 source groups
artifact size: 4,718,956 bytes
single-message P95: 7.96 ms
runtime: Python 3.12.11 / scikit-learn 1.8.0 / numpy 2.4.3 / scipy 1.17.1
frozen threshold: 0.312
intended action: label
status: frozen_experiment_candidate
auto_action_eligible: false
```

v2 仍未注册 active。下一步必须使用另一批与 98 个训练来源、此前 17 个来源去重样本
均不重叠的新来源 holdout；只有该批也达到 label precision 门槛，才进入 label-only
生产代码开发。即使通过，也不开放移动或 Trash。

### 用户动作授权与 junk label-only v2 拒绝

用户现已授权后续高置信度邮件的标签、归档、移动和删除动作。删除在当前方案中固定为
可恢复的 move-to-Trash；永久删除、EXPUNGE 和清空垃圾箱不在授权范围。用户同时明确
禁止回复邮件，因此 SMTP 和 `auto_reply` 保持关闭。该授权只是允许在模型分别达到
动作门槛后执行，不能替代 precision、样本数、版本冻结和 provider readback。

v2 冻结提交 `6b0d63d8` 后，先以 seed `2026090216` 从最近 2,000 个 UID 随机读取
221 个 header，得到 30 个与已有 115 个来源 digest 均不重叠的新来源，每个来源一封。
冻结的 0.312 阈值命中 5 封，人工保守标注均为 junk；30 封整体 top-1 为 21/30，
junk positive support 为 14，阈值召回 5/14。5 个候选不足以跨过至少 20 个候选的
证据门槛，因此没有启用标签。

随后保持模型和阈值不变，以 seed `2026090217` 在同一最近 2,000 UID 池中继续随机
遍历未知来源。读取 636 个 header 和 73 个互不重复的新来源正文后，取得 15 个额外
阈值命中者。按 `ceo-mail-review` 的保守边界标注为 `junk=12`、`important=2`、
`work=1`，precision 为 12/15。三个误判分别是可能的收购/M&A 接洽、IITM 研究者的
免费数据评估提案以及 PSG Equity 的投资接洽；这些邮件即使形式像冷邮件，也不能被
自动标成 Junk。

两批合计 20 个新来源候选，17 个为 junk，最终 precision 85%，低于 label-only 的
95%门槛。v2 因而被拒绝，不能进入 production registry；标签、移动、Trash 等全部
mailbox effect 仍关闭。本轮共标注 45 个新来源；候选定向续样只用于 precision，不能
与首批随机 30 封混合计算 recall。

```text
random 30 predictions sha256:
c993ea78c64b13fb664f741f505ca77dad525f1497eee0dab2975fbc62be3781

random 30 labels sha256:
ca0364ca9d424c1dc480b0bf61a25c9748ea2fc595099911a9432f982cb8b86f

candidate 15 predictions sha256:
f7a6044b5682d92f9968f9462c414f0c573874136c36216b20ebc4933b99b7a8

candidate 15 labels sha256:
7b5f1d290a52a7d139ad86642d1410c38fc138f3817e5c6bf0fae0bf0f713575

v2 rejection lifecycle sha256:
70ac4562b9f1f7a97f8cc15cc8c3cfe1cd8f4ab7a73b7ecc33a0fb81f13ace91
```

这 45 封现在可以作为一次主动学习增量进入下一版开发集，但不再能充当 v3 holdout。
下一轮只允许做一次保持单分类器的 hard-example v3：重点学习“无价值推销”和“有价值
战略/投资/研究接洽”的边界，重新做来源分组开发验证，再在冻结后使用全新来源验证。
若仍达不到 95% label precision，就停止迭代并将自动动作范围收敛为待反馈模式。

### hard-example junk label-only v3 冻结

把上述 45 个新来源标签转入开发训练池后，v3 共有 328 封、143 个来源。继续使用
同一个 word 1–2 + char-wb 3–5 TF-IDF balanced Logistic，没有增加第二个模型或规则
分类器。五折来源分组开发验证为：

```text
Accuracy / Macro F1: 43.60% / 34.17%
junk >= 0.324: 34/35
precision: 97.14%
positive support: 92
recall: 36.96%
candidate sources: 33
maximum source share: 5.71%
```

相比 v2，hard examples 改善了整体分类和 junk 边界；但开发集中仍将先前的播客采访
邀请判为 junk。达到 100% precision 时只剩 10 个候选，低于样本门槛。因此 v3 仍
只能作为 label-only 的最后冻结候选，不能用于移动或 Trash：

```text
model_id: email-tfidf-word-char-junk-label-v3-eb1dfe4a
parent: email-tfidf-word-char-junk-label-v2-d94ee93b (rejected)
artifact sha256: eb1dfe4a62cbcfa71aa900450a4ebd7eeebae541cbd18a3c4b7288ca59ebd3b6
metadata sha256: 093c10eca9d64b7993b6c24dad4e895939d56a078f8b850a05454fa09d129960
training: 328 messages / 143 source groups
single-message P95: 5.98 ms
runtime: Python 3.12.11 / scikit-learn 1.8.0 / numpy 2.4.3 / scipy 1.17.1
frozen threshold: 0.324
intended action: label
status: frozen_experiment_candidate
auto_action_eligible: false
```

v3 artifact 不注册 active。提交此冻结记录后，只允许再做一次全新来源、随机候选流
precision 验证；不能查看新样本后修改 0.324。通过 95%且满足候选数，才进入 label-only
生产代码；失败则停止模型迭代并保持待反馈/shadow。

### hard-example junk label-only v3 最终拒绝

v3 冻结提交 `11f80ebc` 后，以 seed `2026090218` 在最近 2,000 UID 中继续随机遍历
所有既有实验未见来源。读取 868 个 header 和 87 个互不重复的新来源正文后，取得 20 个
超过冻结阈值 0.324 的候选。预测先落盘并锁定 SHA，随后才由 assistant 按保守业务价值
边界标注；模型和阈值没有再修改。

20 个候选中 16 个为 junk，3 个为 important，1 个为 work，precision 80%。四个误判是：

1. 免费媒体报道邀请；
2. 可能接入大额政府合同车辆的合作邀约；
3. 与 Stardust 训练数据业务直接相关的 screen-recording 产品合作；
4. Pilot 面向旧金山企业 CEO 的活动邀请。

这些邮件都来自新来源，外观与批量冷推销相似，但有合理商业价值，不能自动标成 Junk。
v3 的独立结果低于 label-only 95%门槛，已生成 rejection lifecycle；不进入 production
registry，也不执行标签、移动、Trash、退订或其他 mailbox effect。邮箱写入、SMTP 连接
和附件下载均为 0。

```text
v3 candidate predictions sha256:
54fbd0e3e39c82b080095824e8b23f090b0f2c429d3b4e3448a20421d2505ce8

v3 candidate labels sha256:
0e2960748d8068e4b31bb7106091f1063b00a1d27394538d59f7700a698dacac

v3 rejection lifecycle sha256:
c3fa46827f16a97f92f428551f7a94fb68e0ce408368716b2b6254d4082570a3
```

到此停止继续用 assistant 标签循环拟合自动 Junk 动作。实验已证明 CPU 延迟和单分类器
体积不是障碍，真正限制是用户价值边界；继续在同一邮箱反复训练/抽样会逐渐把测试集变成
训练集，却不能证明未来新来源泛化。下一阶段只保留 readonly shadow、待反馈、版本记录和
用户确认后的学习；所有 model-only 邮箱写动作保持关闭。生产代码和 CEO Agent 融合必须
以这一失败结论为输入，不得把开发集 97.14%冒充上线证据。
