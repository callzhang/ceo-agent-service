# Database redesign: final acceptance

本清单是最终结构、容量、恢复和发布的唯一主验收入口。历史实验保留在 `database-redesign-final-capacity-evidence.json` 对应命名记录中；旧中间结构的失败结果不能代替当前结论。

## 当前结论

最终物理结构是 **10 张核心表＋已有迁移账本**，表及字段去向见下表。同一不可变生产副本、两侧均 VACUUM 的最终触发器声明测量为 **490,651,648 → 473,292,800 B**，减少 **17,358,848 B（16.5546875 MiB，约 3.538%）**。历史邮件输入退休另算，不能计作结构本身收益。

本地合并提交 `628d61b58bb84507e97759014194c757a6db7716` 已集成 `origin/main` 的 `b3f59f3ae56419d1bf397346a476382a363041e7`。该合并代码完成真实 CLI 执行／审查任务 **5715333616543262**：done／executed、业务 Attempt、结果及发送回执各一份，替身 dispatch／readback 已核验，0 次真实发送；11 张既有目标表的原记录逐项不变，冷重开没有新增调用。此前 approve／审查修订／no_action／技术重试／结算恢复各分支的证据保持其原版本范围。

新增独立正文／模型输入来源证明后，当前 9,403 封邮件、迁移后真实 CLI 任务及其结果／回执的整库 inverse 已完成，旧生产代码 `2246d5184de62a4f629967fc0effbdb2b6504d10` 冷启动读回通过；所有命名 Email 字段精确一致，未重执行。该轮真实 child 已提交目标 schema、quick_check 通过，但随后因测试只复制 SQLite、漏带两份已存在的外部训练文件而退出；逐阶段复现确认失败在 selected baseline 文件读取，未改运行逻辑。已补齐经 read_snapshot／完整文件 hash 验证的两份当前文件，在同一副本上重新执行 inverse／真实 child 再正迁，暂不声称完整闭环通过。此前同物理 schema 的整库 roundtrip 已通过（673.713 秒），不能代替本轮新增输入生命周期验证。

**仍未完成**：完整邮件历史来源核验及可证明输入退休；本轮再正迁／冷启动收尾；全部历史退休后的 owner 相似案例冷重建耗时复测；稳定候选最终发布审查和推送部署。当前模型输入／来源／相似案例等 119 项通过，另两项 inverse 边界通过；更广批次 71 通过／5 失败后停止，失败为测试沿用旧声明；三处测试修正后两种源变体的 6 项补验通过（182.13 秒），学习／反馈 24 项通过（57.61 秒）。生产尚未变化，未推送、未部署。

- [x] 原生 trajectory 优先核验，原始副本 220 个及新增 9 个来源版本精确绑定，无正文来源 DAO、调用方和正常冷启动已切换。
- [x] 同一原始输入、相同 VACUUM 条件的最终物理控制，容量低于原结构及旧合表候选。
- [x] 完整原始副本经真实 coordinator/child 正迁，正常 Store/EmailStore 冷启动。
- [x] 迁移后新增结果、意图和执行回执，按当前事实逆迁，再经真实 child 正迁；旧回执及新增字段完整保留。
- [x] 真实 CLI approve、审查退回与修订、no_action、技术重试、结算中断恢复；0 次真实对外发送。
- [x] 可选延期／恢复回执使用现有 State，运行时不再创建额外表；两种旧来源变体验证新增任务与回执逆迁／再迁移。
- [ ] 终态 Email 正文／模型输入生命周期、准确来源读取、搜索与训练调用方改造及对比验收。
- [x] 当前外部模型／训练文件只读复核：恰好当前、可运行前版、最新候选及每类最新训练文件，无多余权重或活跃 pin；不代表 Email 模型输入生命周期已完成。
- [ ] 稳定同一候选的最终独立审查，主 agent 集成、提交、推送。
- [ ] 既有 deploy 发布及生产 PID、health、队列、Attention、History 回读。

## 最终目标表及实际字段去向

| 表 | 实际职责 | 当前边界 |
| --- | --- | --- |
| tasks | 20 类已有任务、队列、当前及 claimed 输入版本 | 保留任务身份、必要输入和执行状态；不重复存 trajectory |
| runs | 执行、provider attempt、调度 occurrence 与恢复状态 | 原生 session／记录范围作为轨迹入口 |
| actions | 61 类已有方案、审查、修订、事实、动作、尝试、步骤及执行回执 | 原公开 ID、类型、关系、唯一性保留；使用紧凑 JSONB 槽位 |
| business | Project、Task、Attention 等业务对象 | 保留已采用事实；来源原文退休不删除业务对象 |
| emails | canonical 邮件与当前分类 | message_source_reference_json 内联单次原文的准确定位／完整 hash／字节数；原文退休与模型输入生命周期仍需整体验收 |
| provenance | 真实共享来源的身份、版本、定位及校验信息 | 无正文；单次来源直接内联 Signal，共享时使用真实 FK |
| configs | 已有配置与共享版本 | 不新建兼容中间表 |
| provider_state | provider 当前邮件事实、同步身份与水位 | 逐封邮件事实与账号配置不同，不能整体删除 |
| schedules | 定时配置与安排 | 关联已有 runs occurrence |
| state | 已有高频状态、水位和工具回执 | 延期回执存既有 service_state 命名空间，不增表 |
| email_schema_migrations | 已有 Email schema 账本 | 非核心业务表，保留现有迁移机制 |

最终目标为 **10 张核心表 + 1 张已有迁移账本**。正常空库直接创建最终声明，不再先建 116 张旧表。完整物理控制和完整配对副本均实际达到该表清单；仅物理表数达标不足以证明全部生命周期完成。

| 现有字段／结构 | 最终去向或删除 | 必须保持 |
| --- | --- | --- |
| business_source_documents.body | 退休正文容器；不搬进 Actions、provenance、缓存或 trajectory | 精确来源版本、原生／上游定位、hash、字节数 |
| business_source_documents.identity_key | 单次来源内联 Signal；共享来源 provenance.identity_key | 版本按完整身份和原文字节区分，不只按链接合并 |
| business_source_documents.id、created_at | 退休容器别名和时间；不新增长期别名字段 | Signal 自己的公开 ID 与创建时间保留 |
| Signal.source_document_id | 删除；改为直接来源定位或共享 provenance_id FK | 证据角色、任务／项目关系和 Signal 身份 |
| 原正文唯一索引及不可变触发器 | 随正文表删除；保留最终来源唯一性及必要 FK 约束 | 未放开任意来源或重复身份 |
| context_json、evidence 引语、结果正文 | 以内容语义区分原文复制与业务事实 | 不整体清空 context，不以事实名义复制原文 |
| work_projects.memory_context_json | 已批准退休，当前运行无读写 | 其他业务字段保留 |
| reply_task_deferral_receipts | 原 19 字段映射到 State 的 service_state：key 为表名前缀加原 id，value 保存其余 18 字段 | request_id 幂等、单任务 paused 条件、原公开整数 ID 和全部回执值；现有 BEGIN IMMEDIATE 内完成检查和写入 |

延期工具运行时只读写最终 State。显式迁移接受准确旧 DDL 并退休旧表；当前数据逆迁会恢复原工具表及迁移后新回执。该历史 DDL 仅供明确迁移和恢复，不是运行时回退。既有资格、授权、备份、暂停和恢复语义没有改变。已知外部依赖在复制前拒绝。

## 来源核验和读取

220 个来源版本：**23 原生 trajectory + 172 听记 + 4 周报 + 1 Console 当前事实 + 20 已授权原文不可用**。929 条 Signal 的完整绑定一致。定位清单 `database-redesign-final-trajectory-locators.json` 只含定位与校验信息，不含正文。20 版本永久移除决定记录在 `superpowers/specs/2026-10-18-provenance-original-retirement-decision.json`，共 489,023 原文字节；授权范围不扩展至其他缺失原文。采用事实、来源身份／位置、hash／字节数、业务结果及回执保留，原文显示不可用。生产尚未执行本轮正文退休。

当前 Store、Signal 模型、语义服务、Project 上下文、Attention、backfill/import、worker 和 Web 使用最终 provenance DAO／投影；退休数字容器 ID 和 DB 正文 lookup 已移除。TaskAgent 按真实已完成 attempt 的 runtime/session/记录范围定位现有原生正文，在域写事务外准备来源；只有实际原生缺失后才采用现有上游定位。worker/Web 注入既有 DWS request reader，按需读取，不缓存正文。Console 原文从保留的真实业务事件准确取得。

逆迁从当前 Signal 绑定重建旧结构：原文来自明确传入的已有 verified backup 或准确原生／上游／Console 读取。备份仅提供旧原文字节，不覆盖当前业务行。RepositoryUpdater 将既有备份路径传给配对发布，未改变备份政策。

## 同口径容量

当前有效测量使用最新生产只读 backup 的同一份输入（229 个来源版本、956 条 Signal、9,402 封邮件、116 张普通源表），迁移前后均 VACUUM，未加入合成任务或业务行。先完成结构迁移，再单列历史 Email 小批退休的增量；两者不能混为结构本身的净收益。

| 字节 | 原 116 表 | 最终触发器声明正迁后 |
| --- | ---: | ---: |
| 表页 | 399065088 | 365617152 |
| 索引页 | 91213824 | 105529344 |
| schema 页 | 372736 | 2146304 |
| 空闲页 | 0 | 0 |
| 总量 | **490651648** | **473292800** |

结构正迁净减少 **17,358,848 B（16.5546875 MiB，约 3.538%）**。上表来自修正后的完整 guard 声明，不混入 Email 原文退休增量。先前 200 封小批退休 138 份／623,813 原文字节，净减少 585,728 B；该实验早于最终触发器声明，仅作为来源生命周期证据，不再与本表相加。最新模型输入小批另外核验 40 封，退休 27 份正文及 24 份模型输入，净减少 57,344 B；全量历史核验尚在运行。全部来源退休均逐字段保持业务事实，并校验原始字节的完整 hash／长度；0 次真实发送、邮箱改动或生产写入。

旧合表对象候选在较早原始快照上曾为 540,725,248 B，高于该快照原结构 487,759,872 B。主因是重复 JSON 对象字段名和类型；改为固定 JSONB 值序列与单份整数类型后，该快照的最终候选为 470,712,320 B。这个历史对照只说明修正增长原因，不能拿其数字替代上表最新同输入测量。最新结构的索引相对原结构仍增加 14,315,520 B，已计入净容量。

## 完整副本、CLI 和恢复证据

完整原始副本通过实际 coordinator → child 正迁和正常冷启动。随后新增 external_action_results、agent_effect_intents、agent_execution_receipts 各一条，完成当前数据逆迁；按原 Actions 公开投影和 SQLite 类型／值多重集核对，全部旧回执保留，每类恰好新增一条且每个字段与输入一致。再经真实 child 正迁通过，最后正迁 341.783 秒。quick_check=ok；保留同一条源库既有 runs/190042→tasks/fkid1 FK finding，没有新 finding。未声称零 FK 问题。

完整配对副本结果：`/tmp/redesign-final-paired-copy.pXBzsx/result.json`。首个后验脚本曾误查 Store19 已合入 Actions 的独立回执表，属于 oracle 错误；修正投影后验证，不重复执行已经成功的逆迁。

| 真实 CLI 分支 | 原生调用 | 替身发送 | 已验证结果 |
| --- | ---: | ---: | --- |
| approve | 2 | 1 | 执行、审查、业务 Attempt 与回执、重开 |
| revision | 4 | 1 | 审查退回、修订、再次审查及结算 |
| no_action | 2 | 0 | 无需发送的完整终态 |
| technical_retry | 3 | 1 | 首次 CLI 参数启动失败，随后正常恢复 |
| settlement_recovery | 2 | 1 | 真实 SQLite 结算中断，按已存结果恢复，不重复模型或发送 |

共 **13 次真实 Codex CLI、4 次内存替身发送、0 次真实发送**。各例正常重开、业务结果、应有回执、原生 trajectory 范围、原 11 表旧行与 FK finding 均通过。台账 `/tmp/redesign-final-paired-copy.pXBzsx/native-cases.json`，具体 run metadata 回填现有容量证据 JSON。CLI 和整库 schema 往返是相同最终声明上的独立副本，不声称五个 CLI 任务各自做过整库逆迁。

新增延期回执闭合验证：存储／正常 constructor／维护／harness 选择共 24 项通过 20.51 秒；两种旧来源变体的新任务／运行／paused 回执逆迁再迁移 2 项通过 151.27 秒；原延期工具只读预览、apply/resume 幂等、已发送后禁止覆盖恢复、dispatcher 抢先取得锁等 4 项通过 4.78 秒。完整原延期测试文件随后通过 **38 项，32.05 秒**；首轮 5 项失败均为测试仍直接写退休 Tasks 列，改用当前 Task DAO 后通过，未放宽业务校验。

配对逆迁元数据在 COMMIT 前完整输出并刷新；父进程在失败／超时时先接收完整记录，再抛原始错误。已有 IPC 7 项、更新器与发布路径 61 项定向检查通过。没有提高既有 900 秒子进程上限，也没有绕过正常 constructor。

## 剩余具体差项

1. Email `message_normalized_text` 常态完成回调已接入；最新副本 200 封历史小批有 138 封退休并完整核对，未完成全量历史批次。Console 列表／详情／全文搜索、任务完整线程、任务创建与缺失任务恢复均已解除缓存正文依赖。唯一保留的 Store 正文消费者是迁移时捕获当前 selected 训练基线，正常训练使用外部最新 selected 文件或准确来源。已新增准确 `fetch_message(locator, stable_message_identity=...)` 原语，完整读取选中的正文、跳过附件，校验账号／代际／各 FETCH UID／稳定身份，读取后复核代际；批量扫描原有 64 KiB 限制保持。剩余为全量历史原文可用性及模型输入退休；新增真实 CLI 任务／Email 数据的完整 inverse／再迁移已通过，不能据小批来源通过就退休全部正文。
2. 现有搜索是可见正文的 Unicode casefold 连续子串，具有准确 total、顺序和分页。IMAP SEARCH TEXT 未证明是结果超集；不能默默改成元数据搜索，也不建立持久正文／FTS 副本。既有来源方案和搜索分析中保留明确未选择的技术取舍。
3. `class_model_text` 与可读正文不同，现有真实正文消费者为 owner 相似案例检索；继续保留该功能。终态模型输入精简及按准确来源重建已实现：原生 trajectory 优先，当前实际 feature serializer 只在完整 hash／字节数匹配时可作为 provider 证明；现有来源 JSON 内联 model_input 定位，不新增列。正文和模型输入独立证明，active／未证明输入保留；相同输入精简保留训练纳入标记，真实输入更新继续使标记失效。当前小批 40 封有 27 份正文和 24 份模型输入退休，全 9,403 行其他字段一致；扩展来源／相似案例／selected training／生命周期检查 119 项通过（78.29 秒），另两项 model-only／当前新输入逆迁检查通过（13.88 秒）；受限独立代码复核无新增发现。完整当前数据配对回退与全量历史核验正在运行，更广批次的五项失败已定位为旧测试声明，修正后的 6 项配对发布／inverse／损坏拒绝检查通过（182.13 秒），学习／反馈 24 项通过（57.61 秒）；22 条 pending 输入继续保留。实际副本的 956 个人工标注案例已逐项与相同源 backup 比较，特征文本及标签完全一致；此次有 9 份 owner 输入已退休，读取用时 27.578 秒。全量退休后需再测冷重建耗时，不能据此证明最终延迟可接受。
4. 外部模型／训练文件保留复核已通过（详见下文）；继续保持批准的当前／前版／最新候选与按 snapshot 类型保留规则，不新增按天保留政策。
5. 完成上述代码／副本验收后，集中独立审查稳定候选，集成提交推送，再执行现有 deploy 和生产回读。当前所有成功结论均属于本地代码或副本，未生产生效。


## Email 准确读取与外部保留复核（2026-10-18）

`ImapReadonlyAdapter.fetch_message` 使用已有 EmailProviderLocator 和 MIME BODYSTRUCTURE 解析，完整读取选中的 text/plain、text/html 部分；不下载附件、不搜索相邻 UID、不写邮箱。每个实际 FETCH 的元数据单独绑定 UID，正文 literal 不参加 UID 解析；标签名 UID 不误判成 FETCH 属性。读取前后校验 UIDVALIDITY，返回前校验已有稳定身份和可选 RFC Message-ID。无 Message-ID 时复用既有内容身份；若完整正文与旧截断输入身份不同，拒绝冒充同一版本。

Task context 已用准确原语替换两条邻近 UID 批量窗口，移除旧邻居匹配 helper。已有活跃输入在 provider 不可用时使用其当前必要输入的行为没有改为终态正文保留政策；终态退休仍未实施。没有增加表、数据库字段、持久正文缓存或轨迹副本。

13 项准确读取新测试、原有 IMAP 整文件、Context 整文件及 worker 真实调用路径，共 **94 项通过，5.71 秒**。覆盖完整长正文／原批量截断不变、账号不匹配、缺失 UID、复用／错误 UID、RFC 与稳定身份不符、无 Message-ID 内容身份、读取中代际变化、multipart／附件跳过、名为 UID 的标签及 unsolicited metadata 不能验证错误 literal。所有 provider 均为替身；未连接真实邮箱，不证明现有历史邮件均可取回或搜索性能。

只读外部文件复核时间 `2026-10-18T13:25:43Z`：3 个权重共 **118,342,536 字节**，分别准确引用当前 online model、前一 online model 和最新其他 candidate；三个文件 SHA-256 均与 staged evidence 匹配。两个训练文件共 **26,507,412 字节**，selected/folder 类型各一份，对应各自最新 frozen publication，复用现有 read_snapshot 验证内容、schema、完整摘要，样本数分别 4,423／2,398。无 active training run、无 pin。未构造 Registry/EmailStore，未删除文件或写生产库；目前无多余权重／数据文件需要清理。已有两项跨模型家族的 prune 回归通过，2.42 秒。详细 metadata 回填 `database-redesign-final-capacity-evidence.json` 的 `latest_external_retention_readback`。


## Email 当前来源定位接入（2026-10-18）

`get_email_source_location` 仅投影现有 canonical message 定位和 provider_state 的当前文件夹事实，不读取／返回正文；无需 classification 存在。provider_state 本身没有 UID，未为本次改造虚构或增加 UID 字段。`EmailSourceReader` 在该读连接关闭后获取既有账号 source；可用 observation 指向不同文件夹时，显式调用 `locate_message`，在该观察文件夹按 RFC Message-ID 查询候选，逐条标头验证完整 Message-ID，只有唯一结果才读取完整正文。不以模糊主题、日期或任意其他邮件补足定位；没有可用 Message-ID、缺失或重复时明确不可用。原定位代际变化且没有已观察的新文件夹，当前仍会不可用，未声称可恢复所有移动／重建场景。

读取完成关闭 source 后复核相同现有定位及 observation，若读取中当前事实改变则拒绝返回；原文没有写回数据库或修改旧定位。Task context 已从 classification 的可能过期坐标切换到该 reader；保留既有活跃输入不可用时的行为，尚未退休终态正文。

新增来源定位与移位测试、准确读取、原 IMAP／Context 整文件及 worker 调用检查，共 **105 项通过，13.59 秒**。先失败再实现的用例覆盖 message-only、移动后新 UID/代际、重复 Message-ID、HEADER 子串假阳性、源不可用、读取中定位变化以及旧 classification 不提供定位仍按当前 message 读取。实际最终 SQLite 库参与，IMAP 均为替身；对外发送为零。Ruff、diff 检查通过。

同一已验收最终副本只读 metadata 盘点：9,287 条 canonical message 均有合法已有定位字段，9,266 条有合法 RFC Message-ID；3,409 条带可用当前 observation，其中 65 条 observation 的文件夹与旧 message 定位不同，65 条均有 RFC Message-ID。这个盘点只说明新移位路径有实际适用数据，不是这 65 封的原文可用性证明。没有读取真实邮箱或源正文；盘点记录在现有 capacity evidence 的 email_source_location_copy_inventory。


Email 原生来源优先核验补充：主 agent 用当前 typed task/run 投影确认 `email_agent_classification_tasks.task_id = agent_runtime_attempts.workload_key` 且 workload_kind=email_classification 的准确关联，而非直接查询合表后的物理 workload 列。最终副本有 1,379 个分类任务、1,250 次分类 runtime attempt，其中 1,124 次为带 session 的已完成 Codex CLI attempt。按这些准确 session 对配置的 `/Users/derek/.codex` 原生路径库存核对，当前匹配文件为 0，因此未复制／读取原文，也未声称 native 中有准确正文。已检查安装 job 与项目 env，没有另外配置 HOME/CODEX_HOME 路径。这只证明当前配置环境没有这些确切文件，不证明永久丢失；经典模型分类也不能假定曾运行 Codex。待退休 Email 仍须先核实可用原生来源，无法核实时才核实 provider，不因本次路径缺失自动删除其他原文。定位 metadata 回填现有 evidence 的 email_native_source_probe，无新原文归档。


## 来源 Console 视图与搜索取舍（2026-10-18）

已实现 `EmailClassificationReader` 的列表、退订列表、详情和准确可见正文子串搜索。候选查询只投影已有分类业务元数据和附件元数据，不返回 `model_text`／`normalized_text`／`message_text`；复用原状态／类别／source／动作筛选。退订资格 SQL 仅抽出为共用函数，claim／receipt／queued task 的原资格不变。来源读取在 SQL 连接外，正文只在请求内使用，仅保留页面所需正文；按完整命中数计算总数和分页，发件人／主题已命中的非页面项不额外拉取正文。读取前后重新核对候选集合，变化和来源缺失以错误显式返回，不伪装成空原文、零命中或准确部分总数。详情保留原业务结果／附件和来源取得的收件人／Cc。

本节记录当时的来源服务／DAO 验证；后续已接入候选的 Console JSON 路由，**生产尚未部署，也没有物理退休 Email 正文**。差分 corpus 覆盖 plain／HTML／隐藏 style／跨标签可见文本／multipart 现有显示／Unicode casefold／字面百分号和注入样式文本，比较完整条目、total、顺序和分页；来源回调在该 corpus 提供准确的既有 rendered view，不能由此声称所有真实 provider 历史格式都已等价。另有真实最终 SQLite + 实际 ImapReadonlyAdapter 替身验证 SourceReader.read_view → Console 服务的详情和页面接线，包括 recipient/Cc，没有数据库正文读取依赖。

操作量只读控制：副本当前 all 范围有 9,287 行。搜索“合同”时 26 行由 sender/subject 匹配，另外 9,261 行需要来源正文才能判定准确总数；budget／unsubscribe 各需要 9,253 行。此项操作量测量时，逐封 reader 至少 5 个 session 命令，并逐封取得／关闭 source；后续候选已按账号批量复用 lease，仍需逐封读取准确原文，这不是可直接上线的全库同步搜索方案。未测真实 provider 延迟、未调用真实邮箱、未加正文索引副本。已向 Derek 请求一个明确产品取舍：仅保留发件人／主题搜索并取消 Console 正文搜索，或保留全文搜索并继续批量来源读取／耗时优化；**答复待定，不因等待而擅自更改合同**。详见现有 evidence 的 email_source_search_operation_bound。

## 不必要训练正文读取已解除（2026-10-18）

训练来源目录的计数只需现有身份、标签、来源和时间，原来却调用完整 `list_selected_training_records` 并构造所有 normalized_model_input。现在用 `list_selected_training_labels`，与真正训练读取共享准确的资格 SQL、顺序和标签投影，目录不选择两种输入正文。实际训练仍用原 canonical_model_input 代码，输入规范化／选样／晋升检查未改。CLI labeler 的“已分类身份”原先把完整 selected inputs 的身份再与全体分类身份合并；前者是后者的子集，现直接使用原有全体身份查询，避免无价值全文读取。

来源 Console 对照、source view、训练标签目录及原有 Web/退订/labeler/训练快照共 **50 项通过，55.96 秒**，含真实 FastAPI learning endpoint 在禁止调用完整输入 reader 时仍返回准确目录计数。Ruff／diff 检查通过。本轮没有 schema 或容量变更，没有生产写入或真实发送；外部模型、训练文件及已有 CLI／当前数据逆迁证据保持原边界。


## 当前训练基线与实际来源补验（2026-10-18）

在同一最终副本上，复用实际 selected-record 规范化代码生成当前输入校验，与该副本准确引用的最新外部 selected 文件比较。文件身份／version／digest／schema 与副本 metadata 完整一致，且使用既有 read_snapshot 校验：3,766 条当前合格标签中，3,140 条按 account+stable identity+source 匹配，类别／important 与规范化输入 hash 全部一致；626 条不在该基线中，不能套用别的记录或旧文件。文件共有 4,423 条 observation。此处只记录统计／hash，不输出或复制输入。规范化训练输入有裁剪预算，**不等于完整邮件原文**；本次未把实际训练读取切换为基线／provider，更未据此删除正文。

原生缺失核验后进行了最多三份真实 provider 原文只读抽查，均从副本取得现有账号／定位并复用既有 readonly connector，BODY.PEEK、无发信、无生产写入、无正文副本。Gmail 一份准确取得，可见正文 1,677 字节、与 DB 完全一致，旧 model_text 也一致。钉钉邮箱两份在旧 UID FETCH 得到 OK/[None]，原因为旧坐标下无记录，不能据此断言是解析器错误或永久丢失。诊断只保留 FETCH 元数据结构、UID 和 literal 字节数，未保留标头／正文内容。

已补齐一般来源路径：available observation 指向同一文件夹、旧坐标不可用且有有效 RFC Message-ID 时，在该已观察文件夹按完整标头唯一匹配重新定位，再走完整准确读取与版本复核；不是按主题／时间／最新邮件猜测。没有 available 当前文件夹事实时不猜测其他位置，不改写 DB locator。先失败回归证明原路径遗漏旧 UID 在原文件夹内失效的情形，修正后通过。两份钉钉样本重新读取后，在已记录文件夹内仍未得到唯一 Message-ID 匹配，因此**仍标记该位置不可用、原文保留**；未查遍其他文件夹，也不声称原文永久缺失或可以删除。

训练调用另关闭一个真实冗余：只选择 folder_snapshot 时，原选择函数仍读取全部 Agent／user-feedback 输入，再把未选择部分丢弃。现在只在实际选择这两种来源时调用当前 selected-record API，删除“仅为旧测试替身保留”的 legacy model_text 转换。样本身份、标签、others 映射、优先级、冻结和晋升行为不变。一个先失败回归证明 folder-only 不应触碰未选输入，空选择／folder／Agent-user／others 五项定向通过；Web preview 七项通过。

上述来源与训练相关文件合并检查 **83 项通过，48.20 秒**；Ruff、diff、compileall 通过。实际读取仅六次只读 source session（初查三次、元数据诊断一次、失败样本复查两次），0 次真实发送。物理 schema、容量控制、生产部署状态均未改变。该轮结束时 Console 路由仍未接入；最新进展见后续 HTTP 证据。搜索产品取舍、训练实际输入来源切换和终态退休／逆迁仍未完成。


## 按账号批量来源读取（组件通过，未接入 Console 搜索）

新增 `get_email_source_locations` 只读元数据批量投影及 `EmailSourceReader.read_many`，单封读取复用同一路径。请求内相同身份只读取一次，一批同账号邮件只取得／关闭一次既有 source；读取前关闭 SQL 连接，读取后复核整批定位。缺失定位不打开 provider，任一原文失败或任一定位变化均不返回部分结果。无正文持久缓存、连接池、新字段、来源定位写回或真实发送。

先运行新增五项测试，均因接口缺失失败；实现后来源 reader、IMAP、任务 Context 与 Console 组件四文件 **66 项通过，27.23 秒**。再补充重复身份／缺失定位检查及 worker 实际上下文调用路径，**17 项通过，8.90 秒**（与前组有重叠，不能相加）。Ruff 和 diff 检查通过。这里验证的是连接复用与现有读取行为，不是全库搜索性能；该轮尚未接入 Console batch consumer；最新候选已接入（见后续 HTTP 证据），已请求的搜索产品取舍仍待答复。未新增真实 provider 请求、schema 变化或生产部署。


## Console JSON 来源读取已接入（候选代码，未部署）

实际注册的 classification 列表、详情、unsubscribe 列表现已使用来源 facade；不再调用 Store 的正文列表／详情 reader。`read_views` 复用同账号 source lease，全文搜索按页面大小分批读取需要核对的正文，维持 sender／subject／可见正文的字面 casefold 匹配、完整 total、排序和分页。仅保留当前批次和返回页的原文视图；metadata 命中只为当前页拉取正文。既有 readonly registry 在注册时构建一次，凭据从 Web effective environment 取得（包括显式 env 路径），worker 的原 os.environ 默认不变。来源失败／变化返回明确 `email_original_unavailable` 502，不回退数据库正文，不伪装为零命中或空原文。未知 ID 仍为 404。

默认接线测试采用真实最终 SQLite、实际 ImapReadonlyAdapter 和 IMAP session 替身；禁止 Store 旧正文 reader 后，列表、详情、仅来源正文命中的搜索、unsubscribe 筛选仍正确，6 封邮件按 3 个两封批次完成全文搜索。每个 lease 关闭一次；没有 SMTP 或真实 provider 调用。显式 env 文件凭据读取另有先失败后通过的回归。

本轮最终分组验证：Email Web 整文件 **181 项通过，127.50 秒**；来源 reader／Console facade／默认 HTTP 三文件 **51 项通过**（合并检查中这些测试全部通过）；Console Email／worker registry／当前 reply-Memory queue 重点检查 **10 项通过，16.33 秒**。这三组测试互不重复，共 242 项；未跑全仓库测试。最初全文件暴露的旧表／旧物理列测试种子已改为当前 typed DAO；三个共享 schema 拒绝测试的连接释放问题已修正，最终整文件已通过。Ruff／diff 检查通过。

同时修正 Status 的真实遗留路径：reply Memory queue 原先要求旧中间版完整 Actions 声明，最终结构会报错；现在与 Store reader 一样使用当前 reply variant selector，保留原 `memory_write_events`／`last_error` 语义，未替换成 Task Memory。回归包含真实 pending／failed 回复 Memory 事件及原队列状态断言。共享 `emails`／`state` 声明损坏仍按已有 paired schema 合同在启动前拒绝，测试验证 schema／ledger／cookie／字节未改变；独立 Email 初始化失败仍隔离为 503。没有新增安全／审查／授权规则。

尚未完成：真实历史来源整体可用性和搜索耗时；待答复的搜索范围产品取舍；历史线程与实际训练输入的来源切换；终态 Email 正文／模型输入物理退休及含新增数据的相应逆迁；最终独立审查、提交／推送、部署及生产回读。未增加真实来源请求、真实发送、生产写入或 schema／容量变更。此前同输入容量和 CLI／逆迁证据保持其原有范围，本轮未重新跑真实 CLI。


## 当前 selected 训练来源与完整输入迁出副本验证

实际 `_selection_provenance`、训练 preview 和手动 selected freeze 已使用 `EmailSelectedTrainingReader`；worker 注入现有来源工厂，Audit 的 Console 和 learning 共用一个只读来源 reader。最新同类型外部文件经现有 getter 核对文件身份／digest／schema；仅 account + stable identity + source + label/important/thread + 输入 schema 和已有邮件／分类更新时间不晚于冻结时刻的记录直接复用冻结输入。其他记录批量从准确来源读取，使用线上推理共用 `provider_model_input_fields` 和 `canonical_model_input`。没有 DB 正文／model_text fallback；坏文件不以其他来源掩盖，读取后复核标签和 reference。provider I/O 与冻结工作通过 FastAPI 原生线程池离开异步事件循环，新增先失败后通过的回归；来源缺失返回明确 `email_training_source_unavailable` 502。

真实只读最终控制副本上的新谓词复核：3,766 条合格标签中 3,140 条可复用准确旧 selected 基线，626 条需新的准确输入。未调用真实 provider。对新增来源输入采用现有线上规范化；Cc／退订等旧 DB 重组曾遗漏的特征现在可保留，不能声称这类新输入均与旧 DB 重组逐字节相同。既有冻结输入保持原样，选样来源优先级、标签／others 规则及线上模型激活政策没有改变。

随后在 `/tmp/selected-training-final-copy-ya3hgfyw/target.sqlite3` 完整工作副本上，用既有 normalizer、build/persist snapshot API 将**当前实际训练输入**刷新为最新 selected 文件。复制的旧最新数据文件只创建临时 hard link，未另存 provider 原文或 trajectory。新文件 14,322,415 字节；读取器在禁止 DB 输入 DAO 后返回的全部 **3,766 条记录（含所有标签及完整 normalized_model_input）与刷新前完全相同**，向量 SHA-256 `0a3907e7928d917e58c4f73c9b2a164849449eee9c90b3ba7b670b505ea09763`，provider 调用 0。按既有规则淘汰副本中的旧 selected 文件链接后，仍恰好保留每类最新数据文件共两份；旧非 pin payload 明确不可用。所有 Email 行完全未变、schema 未变、quick_check=ok；FK 保持已有 `[runs,190042,tasks,1]` 基线，无新增。生产目录、邮件原文和模型权重未改动。

最终代码检查：Email Web、learning、新 selected reader、默认来源 HTTP 四文件 **223 项通过，273.26 秒**；Console Email 与 worker training/registry 相关 **21 项通过，23.31 秒**，两组测试不重复。另有重叠的窄检查 62 项通过，不累加。默认 Audit HTTP 验证列表 → preview → manual freeze 共用一个 registry，训练 start 为替身，既未启动真实训练子进程，也未发信或切换线上模型。Ruff／diff 检查通过。

还需完成：该轮结束时当前 selected 数据刷新尚未接入正式迁移准备；此项已由后续完整配对演练闭合（见下文）。剩余为历史线程、owner examples／旧原型 training/inclusion 的剩余正文／model_text 依赖；终态正文退休及含新数据的相应 inverse；历史来源整体可用性／搜索产品取舍与耗时；最终独立审查、集成及部署回读。物理结构仍为 10 核心表 + 原迁移账本，同输入容量控制未改变。本轮没有重新运行真实 CLI 或完整 paired inverse，也没有部署。


## 训练基线已接入真实迁移子进程，新增 dataset 当前数据逆迁通过

真实 paired initializer 在 schema 提交及正常 Store／Email 冷启动后调用 `refresh_current_selected_baseline`。若当前最新已校验文件完整覆盖现有标签／版本，就直接复用，不再读 DB 输入或发布新文件；否则一次性用现有精确输入序列化和既有外部 snapshot API 发布当前训练数据。坏文件保持显式失败，输入捕获期间元数据变化不发布。迁移不调用训练 controller／provider、不激活模型，也不清理数据文件；现有 idle-training cleanup 按 active pin 与每类型最新规则负责保留。schema 逆迁元数据仍在 COMMIT 之前发给父进程，新增测试覆盖后续准备失败时元数据已完整输出。

先失败的五项回归后，实现与原有 source/child transport 重点检查 **22 项通过，15.35 秒**。完整原始 116 表副本 `/tmp/paired-training-final-copy-ezn99bqu/target.sqlite3` 经实际 coordinator／子进程正迁并自动准备当前基线，随后新增一次完整 selected dataset 发布；按当前数据逆迁，检查全部训练快照原字段的 typed multiset 和新增外部文件，再经实际子进程再次正迁。**全部 snapshot 元数据行保留、新增 dataset 文件与精确内容保留、3,766 条当前标签及规范化输入始终一致**；再次正迁复用新增 reference，未额外发布第三份数据。模拟现有空闲清理后保留每类最新文件共两份。首次正迁含准备 325.634 秒，完整 forward／新增发布／inverse／reforward 926.290 秒；没有真实 provider、训练子进程、发送或生产改动。quick_check=ok，FK 仍只有既有 `[runs,190042,tasks,1]`，表清单仍 10 核心 + ledger。本次包含新增数据，不是恢复旧 DB 备份。

对下一差项做了只读实测：956 条 user 标注 owner examples 的 `model_text` 共 2,147,610 字节，仍是现有相似案例检索的真实输入；从当前训练文件按现有 redactor 严格重建仅 78 条完全相同，878 条不同，故不能直接用训练文本替换。另有 22 条 pending_feedback 模型输入 29,748 字节需保留；8,309 条已 processed 的非 user 输入共 12,004,047 字节，没有 included-model 记录，是后续生命周期清理对象。此次没有删除这些输入，也没有改相似案例排序。后续需先为历史记录再次人工纠正补齐准确来源输入，避免把缺失缓存变成新的 owner 样例空输入，再处理终态缓存及线程原文。

剩余：历史线程与 owner/旧原型模型输入依赖及未来人工纠正输入路径；终态 Email 输入退休与相应新增数据 inverse；整体历史来源可用性／搜索取舍及耗时；最终独立审查、集成提交／推送、部署与生产回读。结构容量控制仍用原同输入基准，不将新增 dataset 元数据副本的占用混入结构比较。本轮未重跑真实 CLI，前述真实 CLI／结果及执行回执证据保持其原范围。生产未部署。


## 执行上下文整条邮件线程改为准确来源读取

`EmailContextSource.load_task_input` 现在一次 account batch 读取已关联的全部邮件，按原顺序投影当前／历史正文、发件人和附件元数据，保留原回执。删除了仅重读触发邮件的私有 helper 及 provider 不可用时退回 DB 正文的分支。SourceReader 仍在 IO 后核对现有来源元数据并 finally 释放连接。来源缺失／连接失败明确传播，不返回只包含部分线程的成功结果。

先改三项回归看到预期 **3 fail / 3 pass**（历史正文仍旧缓存、两种失败被吞掉）；实现后 Context/Source 两文件 **26 passed，9.16 秒**，另 worker `_load_email_task_input` 接入用例 **1 passed，1.40 秒**。新增准确 source 替身测试覆盖不含任何 DB 正文字段的线程、每封完整长正文、单次租用/顺序/logout、任一历史或当前来源缺失失败且不返回部分上下文。worker 原 fixture 总返回触发邮件，已改为按请求 identity 返回对应邮件；没有改断言或放宽真实 source 校验。Ruff / diff check 通过。

本项仅消除了实际执行上下文对缓存正文的依赖；Store 的 relation 查询仍 select 全正文，扫描创建任务与 missing-task recovery 仍有旧缓存读者，继续列为未完成。无 schema、容量基准、正文删除、真实 provider/CLI/发送或生产改动。最终结构及原完整副本/真实 CLI 证据范围不变，生产未部署。


## 创建／恢复调用方与线程查询已移除正文依赖

生产调用核实：`EmailAgentTaskRoute.context` 没有生产读者；真正执行由 worker 重新加载准确上下文。删除 route 的该字段和两次无用构建，创建阶段只用扫描已取得的当前原文及已存元数据，历史线程留到执行读取，不额外租用正在被扫描占用的账号连接。`EmailActionTaskProducer` 不再读取缓存的触发／历史正文，空扫描正文保持为空。既有执行上下文及 continuation 校验仍由 `load_existing_task_context` 完成，相关测试已迁到实际执行入口，没有删除校验。

`list_email_context_thread` 直接选当前 emails 的实际元数据字段，保留原账号隔离、触发线程关系、References 传递闭包、排序和附件信息；不再通过包含所有原文的完整投影扫描账号。SQLite authorizer 阻止正文/模型列的回归先失败、后通过。最终控制副本只读核实最大的 10 个线程组，关联组件分别 30/28/19/17/17/14/12/10/10/9 条，0.198 秒，禁止的原文列访问为零，无 provider 或 DB 写入。

模型缺失任务恢复改用既有 `EmailSourceReader` 和 LOW 账号来源，不再用相邻 UID 有限扫描，也删除缓存正文重建任务／猜测退订入口的两个旧 helper。准确来源不可用走原冲突报告，保持缺失任务候选，不写虚假的 skipped 回执；来源恢复后创建一次任务，重复 tick 不重复创建。终态任务已有结果但缺回执的既有补齐路径保持并有回归覆盖。

第一组 enqueue/body-free/missing-source 回归观察到预期失败；测试旧 SQL 夹具按实际 Tasks/Emails 结构更新，保持无写锁轮询、竞争 claim、冲突和幂等断言。四文件检查 98 pass / 1 fail，唯一剩余是旧“缺索引必须拒绝”假定；当前既有契约明确允许修复缺失辅助索引，故将损坏场景改为同名错误索引声明（而非改变运行行为），补验 1 pass / 1.31 秒且拒绝时 DB bytes 不变。worker 重点 20 pass / 20.93 秒；另补来源缺失→恢复→重复 tick 检查 1 pass / 3.26 秒。当前没有 schema 或正文删除，容量基准不变，未部署。

## 运行时分类 getter 移除无用原文读取（补验已完成）

核实 Store.get_classification 的五个生产调用方只用身份、计划、状态、展示元数据或收件人，均不使用 message_text/cc。getter 直接读实际分类字段（排除 model_text）和持久收件人／附件元数据，不再取原文、解析 MIME 或返回正文/cc。Console 原文详情继续由已有准确来源 facade 返回。明确的测试来源替身仍从其合成 DB fixture 读取正文，未新增生产回退。新 authorizer 回归先因 class_model_text 访问失败，后通过；相关 Store display/recipient 3 pass / 3.27 秒。控制副本上 25 个带当前计划的真实分类，计划引用／收件人一致，禁止正文和模型列访问为零，0.225 秒、无外部/DB 写入。订阅执行／审查、Context/Source/Web 更宽检查已完成，结果及修正如下。

已消除先前“Store 线程查询、创建任务、缺失任务恢复”的三项读取差项；仍需完成旧列表/训练原型调用清理、模型输入生命周期和实际邮件来源可用性／原文退休及 inverse。最终独立审查、提交／推送／部署及生产回读仍未完成。


更宽五文件运行 **162 pass / 4 fail，190.89 秒**。三项是 Audit 测试仍直接更新已退休 trigger_message_json 列，已用现有 Task8 payload SET 和当前 input 的真实物理关系更新夹具，保留原篡改拒绝断言；第四项是实际 `cancel_failed_email_unsubscribe_task` 仍写旧 status/error 等物理列，且用公开任务 ID 过滤物理 id。已改为现有 Task8 payload SET + `reply_tasks__id` 公共身份条件，保留既有 profile lock、立即事务、校验、取消记录、重复请求和“没有 provider 退订回执”行为。首次误用了仅适用于另外十二种任务的 Task12 point DAO，定向测试立即指出 unsupported kind，已纠正为既有八种任务的 Task8 写入器，没有新增兼容分支。相关 **10 pass / 12.53 秒**（包含四个原失败项及相邻拒绝／取消分支），Ruff/diff check 通过。本轮没有重跑此前已通过的真实 CLI／整库 inverse；它们仍按原证据范围记录。没有生产部署。


## 无生产调用方的训练原型接口退休，状态计数不再加载正文

Root 用实际 app 引用复核：当前训练走 frozen classic/embedding candidate 和最新外部 selected 输入；`train_and_promote` 的 registry 分支也没有生产调用者，不因历史测试调用而保留。移除整个 wrapper、path wrapper、Store readiness wrapper、弃用 retrain_if_due，以及专属旧 helper／Store list、inclusion/promotion callback API。保留真实冻结训练、现有模型 registry 原语、候选发布／启用、相似案例 owner 特征、metadata CAS 与迁移 selected serializer。Luna 仅做只读引用清单，Root 纠正其将 `_default_requirements` 误判为专属 helper 的结论，保留该函数及现有 frozen 调用；没有依其未核实建议删代码。

状态页原 pending_examples 语义不变：当前 user、processed、非空确认类别与 model_text、included_in_model_id 为空的数量。新 COUNT API 不把训练文本/样例 digest 集合加载进 Python，也不新增字段。RED：新增混合 eligible/empty/included/pending 回归因方法不存在失败；GREEN 1 pass / 2.52 秒。相同最终控制副本只读数值 **956**，与旧 eligible predicate 一致，0.333 秒，Python 输入记录物化为零。没有清理 956 个 owner 特征或 8,309 条其他输入。

随已取消 API 删除其 11 个训练 wrapper 专属测试和 5 个旧 inclusion-promotion 专属测试；原来顺带读取这些 API 的并发反馈、重扫、来源检查和确认用例改查真实保留的 owner-case DTO/计数，保留原业务断言。真实冻结训练／重训／selected source／迁移 preparation 四文件 **134 pass / 36.59 秒**；Store 混合计数、并发确认、初始化及重扫 **7 pass / 7.42 秒**；只读扫描重复执行保持人工反馈 **1 pass / 2.68 秒**；Web learning/training/control 重点 **38 pass / 12.85 秒**。

更宽 learning/Console training/Pipeline 首跑 66 pass / 29 fail，均来自旧元数据/source mock、原配置表 fixture 或旧 connection signature/query。更新测试为显式合成 selected reader、真实 metadata labels/candidates、现有 config DAO＋caller transaction、继承原生连接的 BEGIN trace 以保留写锁次序断言；不改生产算法或新增 fallback。Learning 在该批已通过。Console/Pipeline 第二批 49 pass / 26 fail 后修正配置 fixture 的 caller BEGIN 和旧 classification table readback，重点 2 pass / 3.83 秒；Console 全文件最终 **50 pass / 31.31 秒**。Pipeline 其余用例已在上述批次通过；并发配置更新补验保留原计划/动作版本一致断言。Ruff / diff check 通过。未重跑已满足的真实 CLI、整库 forward/inverse 或容量基准；无 schema、DB 数据、模型训练子进程、真实 provider/发送或生产部署。

剩余准确边界：旧 Store 正文列表/search（无生产调用方，保留中的测试 Oracle 待迁出运行代码）、实际 owner feature／其他终态模型输入生命周期、全量真实邮件原文可用性及可溯源输入退休/current-data inverse、最终独立验收和发布。最终 10 核心表＋迁移账本、同输入 456.30 MiB 和先前 CLI／整库 inverse 证据不因代码删除而扩大或改变。


## 旧正文列表／搜索运行接口退休，独立 SQL 基准移到测试侧

`EmailStore.list_classifications`、`list_unsubscribe_classifications`、`_classification_search` 已无生产调用方，已从运行模块移除（连同无用 Parser import）。现有 HTTP facade／准确来源正文搜索保持。旧 SQL 原样移入 tests/support/email_listing_oracle.py，仅供合成 fixture 的独立比较，app 没有测试 Oracle import；历史业务断言显式读取 Oracle，不加生产兼容方法或 fallback。source/detail 对比也明确使用测试 Oracle 的完整旧 DTO，实际 Store getter 仍只读元数据。两个隔离回执投影的测试改用独立 view mock，不再把旧方法加回 Store。

RED 新回归先因 Store 仍暴露原文列表接口失败；移除后 source reader／default Web 完整两文件 **40 pass / 33.08 秒**，包含全查询矩阵、原 HTML/CSS 可见文本、casefold／字面搜索、total/order/page、筛选、完整准确 source / missing source／无原文 DB fallback。其他七文件中 20 个受影响业务检查 **19 pass / 1 fail / 18.31 秒**；唯一失败是既有重扫授权快照测试仍查原 classification 表，已改为真实 canonical 列，补验 **1 pass / 2.57 秒**，保留原计划历史与 current plan 不变断言。两项隔离 Web 退订视图 **2 pass / 2.89 秒**。Ruff/diff check 通过。无 schema、数据库数据、provider / CLI / 发送或生产改动；原同输入容量和前述完整 CLI／inverse 证据不扩大。

只读补充定位下一输入差项：控制副本 9,287 条邮件中，Gmail 6,329 全部 processed，缓存正文 16,872,619 B、模型输入 7,484,874 B；Aliyun 2,958 条中 processed 2,936，缓存正文 8,099,463 B、模型输入 6,696,531 B。均为字段 UTF8 合计，不等于释放页数，未读取 provider 或删除数据。

同时检查 task 余量，758 个 meeting jobs 的 source_json 字段共 11,950,013 B，其中 291 个 job 的 scheduled_consumer 占 10,224,757 B，主要是固定 prompt/Skill 快照，需要下一步按原生 trajectory 或现有精确配置引用核实，不能直接当作无用数据删除。其余 top-level source 字段为实际会议身份/日历/list/info/discovery 元数据，另有 1 份 asr_transcript 21,446 B。结果/回执另列，未删。Luna 仅对该上下文字段的读者/来源引用做有界只读核实，未让它重扫轨迹或设计新 schema。

剩余：输入/owner 特征生命周期、邮件及会议 source 中可重取原文/指令的精确定位与终态退休（保留未证实原文）、新增数据 inverse、最终独立验证与提交推送部署。旧正文列表接口已不再列为实现缺口。

会议来源三样本补核：job 7706／5024／7714 分别关联独立 meeting run 2494／2304／2498，再关联 runtime attempt 21857／14642／21908；不是仅凭 Cron 的扫描命令判断来源。三个实际 session 在 configured Codex home 的路径库存均未找到，主 agent 独立复核相同结果；因此未读取轨迹正文，也尚不能证明注入内容已由原生来源完整保存。代码实际注入 prompt 与 materialized Skill protocol，并非整个 scheduled_consumer JSON。只限当前环境查无，不能推断永久缺失；本轮不退休这部分字段或扩大既定 20 份授权。

稳定邮件身份读取补核：get_classification_by_stable_identity 不再 SELECT * 读取未返回的模型输入，而是直接读取实际返回的分类字段。SQLite authorizer 回归先证实旧实现触碰 class_model_text，再验证新实现不读正文／模型输入；来源列表、计划引用、训练标签三文件首轮 41 项通过，3 个旧表 fixture 失败已迁至当前列并通过所在文件；worker 两条实际扫描／退订回执用例通过，调度拒绝用例的旧任务表计数改为当前 typed tasks 后两分支通过。只读控制副本 25 条 DTO／计划与旧投影逐项一致，禁止正文与模型输入读取为 0。未更改 schema、保留规则或生产数据。

会议来源有限集合路径核验完成：291 个 consumer-bearing job 中 288 个有关联 meeting run／attempt，316 个 meeting runs、335 个 attempt 引用，333 个非空 session 引用去重为 331（Codex 323、Claude 8）。configured native path inventory 找到 Claude 8 个文件，Codex 323 个文件查无，另有 2 个空 session attempt。找到文件的 3 个代表 job 7775／7772／7780，其实际注入 prompt 和 materialized Skill protocol 均在原生行 0、2 的字符串字段精确命中，主 agent 独立内存比对确认；文件实际行数 24／29／29，短于 DB 记录 end 114／82／82，因此只证明命中片段存在，不证明完整 turn 可读。未复制原文、未删除字段或数据；剩余文件缺失仅限 configured 环境，不推断永久丢失。

会议 Skill 上游精确内容补核（原生路径核验之后）：94 个带 skill_materials 的任务只有 4 个唯一材料版本，ceo-meeting-work 两个版本分别与既有 managed revision 142／167 的 content、sha256 一致；dingtalk-minutes／dingtalk-calendar 与各自现有 SKILL.md 原字节及声明 hash 一致。197 个旧 inline protocol 中，129 个与 managed revision 4 加上述两份 operation Skill 的既有渲染结果整串一致；另 68 个未匹配，不推断可恢复。以上 223 个任务的材料／protocol 有准确共享来源证据，但本轮尚未实施引用替换或原文退休；prompt、完整 JSON 与 replay／逆迁仍需按各自字段核对。无正文输出或副本、无数据修改。

下一实施步骤（原生直接文本来源）：复用 native_trajectory 的现有行 hash／leaf_path／字符范围／原文字节 hash 协议，为 locator 和 reader 增加显式 plain_utf8 选择，默认 json_string_literal_utf8 严格行为不变。不新增来源表或正文缓存。先用 Codex／Claude 合成原生文件验证 Unicode、完整匹配、指定范围、缺失／版本变化和模式不可隐式切换，再实现并运行既有 native source 整文件；随后在已找到的真实会议文件上只读定位／读回比较，不输出正文。该能力是会议终态 consumer 引用精简的前置，单独通过不代表正文已经退休或迁移完成。

原生直接文本定位实现完成：locate_native_source_body／read_native_source_body 只有双方显式选择 plain_utf8 才可读取直接注入的 prompt／Skill；默认 JSON literal 模式保持严格，不能按 metadata 静默切换模式。新增测试先 10 项失败（接口不存在），实现后与既有 native/source-reader 整文件合计 110 项通过，0.30 秒；覆盖 Unicode／反斜线／换行、行版本、字符范围、原文 hash、限定范围及显式模式。真实会议 job 7775／7772／7780 的 prompt 原字节 151 B 和完整未 strip 的 materialized protocol 34,801 B 均能定位到 native line 0 content leaf，并经新 reader 逐字节读回一致。原文仅在内存比较，引用无正文。能力尚未接入会议源字段退休或 schema release；没有数据删除或生产变化。

会议原生字段退休实施步骤：新增 meeting_consumer_source 专用纯函数，输入已有 scheduled_consumer 和直接会议 runtime attempt，使用显式 plain_utf8 定位并读回 prompt、inline protocol 或每个 material 的完整原字节；全部字段可校验才返回 scheduled_consumer_reference。保留已有身份／Skill 名称／protocol source，生成的短 discovery 配置不作原文迁移；reference 带当前 consumer 的规范化 JSON hash／字节数。Store 只在 sent/skipped 状态准备引用（SQL 连接外），再以原 source_json + 终态 CAS 更新现有 source_json，不改结果／回执；原生缺失保持原输入。正常终态写入调用该方法；历史调用仍是显式迁移批次，不加定时循环。先测试字段内容完整校验、缺失不精简、来源版本变化、业务字段不变、幂等与活动状态不精简；再在数据库副本及当前数据逆迁中检查新引用。不上线直到完整验收。

会议终态原生输入引用已接入候选：update_meeting_alignment_job 在 sent／skipped 提交后调用 compact_meeting_consumer_input。仅同一实际会议 attempt 能定位并读回全部 prompt／inline protocol 或 material 字段才退休；短 discovery 配置保留。原生缺失／变化保持原输入，processing／retry／ready_to_send／no_action／failed 不精简，原 source_json + 状态 CAS 避免覆盖并发来源修改，不改业务结果／回执／更新时间，不增加扫描循环。新组件先失败后实现，11 项通过（5.08 秒）；既有 Store／delivery／confirmed recovery 三文件 99 项通过、4 个旧结构 fixture 失败已适配当前 Tasks20／State 并逐项复查通过。

实际最终副本 /tmp/meeting-consumer-native.itqXsD/target.sqlite3 中，3 个历史 job 的 source_json 分别从 38,360／38,227／38,367 B 减至 5,488／5,362／5,495 B；全部其他源字段、业务结果、回执和 job 元数据逐项一致。全部原文可按新 native reference 精确读回，重复精简幂等、正常冷重开通过，quick_check=ok，仅保留既定 FK 基线。发送和生产写入均为 0。此为新终态引用局部副本验证；历史全量退休、上游 223 任务引用接入及新引用的最终完整配对逆迁仍未完成，不能用此前完整配对结果替代这些新差项。

下一实施步骤（会议共享来源与批次）：在原生逐字段核验未成功后，使用现有 scheduled run.snapshot 的 prompt／skill_refs，现有 managed revision 和 Operation Skill 文件，通过既有 ScheduledAgentContextBuilder._materialize_skills 生成协议；只有与原 prompt、全部 materials 或整个 inline protocol 原字节一致才保留引用。引用采用既有 run ID、revision/path/hash 事实，不保留材料正文。Store 增加一次显式终态批次，配对 child 在 Store 冷开后执行，不增定时扫描；旧不可核验版本保留。先测试完整匹配、版本不一致保留、原生优先和 child 顺序，再实现；最后副本批次、同口径整理与当前数据逆迁核验，待 Email 产品决策和全部最终验收结束才发布。

会议共享来源／历史批次已接入配对 child：原生优先，失败后逐字节核对已有 run snapshot、managed revision 和 Operation Skill；复用已有 materialization renderer。36 项来源／批次／child 顺序及 native plain 测试通过（11.33 秒）。严格同一原始 116 表输入、无合成业务行／无新训练数据集发布的实际 paired apply 副本中，673 个终态任务审查后精简 223 个；整理前后数据和全部其他会议字段／回执一致。相同 VACUUM 后，新候选 470,712,320 B（448.90625 MiB），空闲 0；其中表 363,728,896、索引 104,845,312、schema 2,138,112 B。比原 487,759,872 B 小 17,047,552 B（16.2578125 MiB）；新增退休步骤自身从 478,470,144 B 减少 7,757,824 B（7.3984375 MiB）。旧 478,466,048 B 是先前物理构建对照，本次实际 paired apply 的 schema 页多 4,096 B，分开记录而不混为同一运行。

余下 68 个旧 inline protocol 的准确缺口为两个旧 dingtalk-minutes 版本（28 行的内容 14,077 B、40 行的内容 17,218 B）；对应 prompt、managed revision 4 与 calendar Skill 已匹配。指定 .agents Git 历史只有当前 minutes 版本，不能代替这两个旧版本。保留原输入，不扩大 20 份原文删除授权。

新增引用的整库逆迁演练进展：初次脚本误用了仅含初始化声明的旧 metadata，缺失后阶段 final_actions 逆迁声明，事务回滚且副本保持 11 表；从同一原始输入经实际 apply 取得完整 10 组件 inverse metadata 后重试，759 个会议任务完整字段、迁移后新增合成业务任务与替身回执、全部新来源引用逐项一致。随后真实 coordinator／child 再迁移已通过；759 个任务的全部字段、新任务业务结果／替身回执和新来源引用逐项一致，正常 Store 冷开、quick_check=ok，只有同一条既有 FK finding。完整重试往返 578.945 秒，其中逆迁 240.386 秒；没有真实发送或生产写入。此轮只验证会议来源退休后的完整当前数据往返，不增加真实 CLI 覆盖。


会议来源局部独立审查发现并复现文件 I/O 边界缺陷：终态提交后 native/Operation Skill 权限或编码错误会从精简步骤传播。新增 3 项回归原实现全部失败（2.53 秒）；仅把逐字段 native 读取及既有 materializer 的文件／编码错误归入“来源不可用，保留原输入”，不吞 SQLite 或任意异常。来源／child／native 三文件 39 项通过（11.57 秒），补充数据库错误仍传播及文件不可用检查 4 项通过（1.64 秒）；只读审查复核该缺陷闭合。Ruff／diff check 通过。完整容量及当前数据往返在这项 I/O 修正前完成，准确匹配路径和物理表示未变，未重复全库演练；该局部审查不能代替最终整体发布审查。无生产改动。


## Email 退休范围准确核验补充

同一最终往返副本的当前 selected baseline 覆盖 3,766 条标签／时钟：956 条 user_feedback 中正文原字节一致 178 条（146,990 B）、旧模型特征完全一致 78 条（51,030 B）；2,810 条 agent_auto_label 中正文一致 662 条（513,982 B）、旧模型特征一致 85 条（78,242 B）。合计只有 840 份正文与 163 份旧模型输入能从该训练文件完全复原，不能按邮件身份或训练记录存在就批量替换；未退休数据或新建原文归档。

复用已经完成的原生路径库存后，按两个账号 × 现有 model／agent／user 分类来源各取一个未重复的终态样本，经既有 readonly connector 与完整 BODY.PEEK 精确读取，6 个新样本中 5 个可读；这 5 个的完整 provider_body、可见正文和旧模型特征均与副本逐字节一致。另 1 个 DingTalk user 样本来源查无，原输入保留。本轮 43.925 秒，无真实发送、邮箱改动或生产写入，原文只在内存；现有 evidence 保留分类 ID／稳定身份 hash、定位及完整内容 hash／字节数，不含正文。此为来源可用性证据，不是全量可用性或正文退休完成证明。

对之前两个 DingTalk 查无样本只核对实际已完成 move 的参数和回执：目标均为既有“通知”文件夹，与 message locator 和 observation 一致，回执没有新 UID／代际，不能补足缺失原文。没有基于推测更换来源或扩大历史原文永久删除范围。邮件 schema／物理数据和生产均未改变；两个已发出的搜索与相似案例产品问题仍保持待答复，期间保留现有功能。


过程记录整理：27 个本任务未提交的中间 SPEC／QUALITY／harness 审查 JSON（25,307 B）已完整合并到现有 capacity evidence 的 historical_component_reviews。读取全部内容并逐项校验解析值完全一致后，移除独立文件；主 program 的两处索引同步更新。只保留一份主验收入口及现有证据文件，不新建审查台账。实际迁移读取的 20 份原文退休授权、原始来源证据、必要 freeze／acceptance／失败诊断均保留。历史组件批准不能代替最终整体发布批准。


## Email 原文引用与当前数据逆迁实施

已批准 spec 的单次来源直接内联规则应用于 emails：新增唯一可空字段 message_source_reference_json，记录完整原文 SHA／UTF8 字节数、实际序列化形式及 native 或 provider 定位；不新建原文／来源容器，也不把正文搬进现有 provenance。原 message_normalized_text 只在来源未证实或仍有活动依赖时保留；退休时置空，业务字段／模型特征／时间／回执不动。模型特征与搜索的产品选择不因这项原文字段改造而改变。

新 schema／原文精确证明／逆迁 reader 先出现预期 4 项、5 项及恢复原语 1 项失败。实现后 canonical 与来源组件 40 项通过（43.44 秒），更宽 62 项通过、1 项仅因测试假定 SQL 无 ORDER BY 的调用顺序而失败，已将断言改为精确调用多重集；后续来源／恢复／child preparation 三文件 38 项通过（22.44 秒）。历史 canonical FK 夹具补回准确 retired parent DDL，历史 object Action 插入不再错误调用最终 vector DAO，原 FK／业务往返断言保留。实际 Store 入口随后实施：未完成分类／回复／邮箱操作／发送或退订 claim 保留正文，原始内容、状态、时钟和当前计划 CAS 防止并发误删。最初 12 项因误用不含 trigger_message_json 的任务投影失败，已改用实际当前输入投影；批量详情额外两次元数据查询已合并。正文／来源两文件 46 项通过（24.45 秒），canonical／上下文／迁移三文件 44 项通过（42.61 秒），Ruff 通过。常态生命周期和历史全量退休接线仍未完成，不能把显式入口通过等同生产自动生效。

逆迁在写事务外准备原文：既有 source backup 只提供 hash／字节完全一致的原文字节；新增或更新版本从准确 native／provider 源取得。进入原 schema 逆迁前再次核对当前引用，不用旧备份覆盖当前业务行。新增行和旧备份版本不符两种恢复场景已有真实 SQLite 测试；实际整库新声明的 current-data inverse 仍待执行。同一原始 116 表输入的真实 coordinator／child 正迁已通过（388.964 秒，/tmp/email-body-final-copy.jJStlO/forward-result.json），47 列、10 个完整逆迁组件、quick_check=ok、仅保留既有 FK finding。新增整库当前数据 inverse／真实 child 再正迁已通过，详细范围见本节后验记录。


同口径新增测量：同一原始输入经 47 列 paired child、正常冷启动及既有会议退休后，只再对五个已证明准确的真实邮件样本执行显式 Store 退休；尚未添加合成业务行。VACUUM 后表 363,741,184、索引 104,845,312、schema 2,138,112、空闲 0，总计 **470,724,608 B**。比原始同输入 487,759,872 B 减少 17,035,264 B（16.24609375 MiB）；比此前 46 列候选增加 12,288 B。五份正文共 6,765 B，引用自身也占空间，因此不是该五份原文净省 6,765 B。五个退休操作均有完整 provider 等值证明，其他 owner 字段逐项不变；四个 Console 来源读回通过，第五个退休后续读取在 IMAP capability 连接超时，明确不计作读回成功，不加缓存或回退掩盖。

生产增量只读核验：当前 229 来源版本／956 Signal，较原始副本增加 9 版本／27 Signal。它们是三个完整 body 版本，沿实际 work_summary_inputs → task_agent_runs.summary_input_id → workload_kind=task 的原生 attempt 找到完整字节，Root 独立读回及通过实际生成链重新定位核对。已有 locator 清单追加九条定位元数据，原始 220 版本统计保持其快照范围；不写生产或复制正文。新的只读迁移预检已通过。为避免固定清单之后持续新增版本再造成同类失败，迁移对未列入历史清单的来源按这条真实生成链核验完整 native 原文；不能证明的仍拒绝迁移，不扩大那 20 份退休授权。该路径两项回归先 RED 后通过，全文件 8 项通过（0.22 秒）。最新生产副本整体验收尚未执行，不能由预检通过推断生产已上线。


新引用候选的完整当前数据 inverse 已完成：按明确列名对比 9,288 封邮件（含迁移后新增 message-only 行）及 9,287 条分类，全部当前值摘要与 inverse 前预存预期一致；五份已退休原文从匹配备份字节恢复，新增业务结果和发送替身回执完整保留。首次后验使用 SELECT *，被旧消息表历史追加字段的物理顺序误导；改成明确字段顺序后两组完整摘要相等，不重跑已成功的 inverse。真实 child 再正迁随后通过（resume 340.73 秒），完整邮件及分类摘要仍逐项一致，新增业务结果和替身回执保持，quick_check=ok，仅保留源库既有 runs/190042→tasks FK finding。结果记录于 /tmp/email-body-final-copy.jJStlO/inverse-result.json，并已合入现有 evidence。未调用真实发送，未写生产库。

只读组件核对指出 native Console 详情原先会混用 flat recipients 与 native body 的缺失头字段。Root 以真实 IMAP adapter 合成邮件先复现 RED：原 To 一人／Cc 一人被显示为 To 两人、Cc 空。已删除此新增 native 展示分支；保留普通准确 provider header 读取，并校验 native 原文字节引用，避免为 recipient 角色臆造字段。该修改没有部署；来源／Console 两文件 54 项通过（35.30 秒），随后删除单用途额外 reader 层后两个有针对性的原生收件人／批量查询检查通过（2.66 秒），Ruff/diff check 通过。


常态接线的下一步边界已明确：使用既有共享 Email connector 的 source reader，在分类任务完成、最后一个确定邮箱动作完成、回复／退订任务完成之后，仅尝试当前分类 ID 的正文退休；不得在仍持有同一连接的扫描过程里递归申请连接，不新增历史轮询。维护性历史批次应明确执行，先保留最新完整 selected baseline，未知来源仍保留正文。完成这些接线及模型输入生命周期之后，才做稳定版本的最新生产副本与最终独立审查；当前没有提交、推送或部署本候选。


## Email 完成流程接线

分类任务、确定邮箱动作、审查后任务及 direct unsubscribe 的完成回调共用既有账号 connector 的 LOW-priority EmailSourceReader。分类在 adapter.complete 之后执行原文退休，清理异常不进入已经完成分类的 adapter.fail；直接动作先在 executor finally 归还 provider lease，再提交 receipt，成功后尝试退休本分类。失败动作和未解决任务保留输入。同步历史处理和历史恢复复用同一回调，不增加额外 registry、线程或历史轮询。

新增六个检查先因回调入口不存在 RED，接入后通过。真实 Store 验证两个动作中第一个完成时保留正文且不做 source IO，第二个完成后才清空正文／保存完整引用，模型输入不变；重开后无第三次执行。生产依赖检查验证四个入口是同一回调／同一 LOW readonly lease，历史恢复同样传递它。较宽检查的五项旧表 fixture 改为 canonical Email 列和既有 Task12 lease SET，原 provider-body、脱敏、crash dedupe 和 owner-example 断言保持，5 pass（6.93 秒）。历史生产批次 fixture 的 next_retry_at 也改为现有 Task12 SET，原完整运行通过（14.32 秒）；未为旧 fixture 新建兼容表。

有界只读核对发现 all-receipts-done 分支绕过动作执行后也绕过清理。原有三个回执中断点的真实 Store 回归在第三个 receipt 后中断情形出现预期 RED；同步历史 all-done 回归也出现预期 RED。已补上“仅复用已有成功回执”的完成清理，刚执行过动作的同一轮不重复拉取原文，业务动作仍不重复。完成／来源／生产接线及该恢复范围 12 pass（16.71 秒），Ruff/diff check 通过。这里只完成常态正文入口；不是全部历史正文或模型输入已退休，也未改待答复的搜索及相似案例功能。

最新生产副本正在独立验收：/tmp/db-redesign-latest.RIJJUz/source.sqlite3 是 SQLite URI mode=ro backup 后的本任务副本，整理后 490,651,648 B、116 张普通源表，含 229 来源版本／956 Signal／9,402 邮件。两类当前外部训练输入通过 read_snapshot 校验后复制到目标测试目录。新副本真实 child 正迁在运行；未写生产、未调用真实发送，不将此前原始副本的容量或 inverse 数值冒充该新副本结果。


最新生产副本的独立正迁完成（405.11 秒）：同一份输入、同一 VACUUM 条件，源表页 399,065,088 B／索引 91,213,824 B／schema 372,736 B／free 0，总计 490,651,648 B；目标表页 365,617,152 B／索引 105,529,344 B／schema 2,138,112 B／free 0，总计 473,284,608 B。减少 17,367,040 B（16.5625 MiB），116 张普通表变为十核心加账本，全部 229 个来源绑定，quick_check 与源 FK finding 一致。此测量在后续 Email 来源小批退休及真实 CLI 合成写入之前，不混用旧副本或新增数据容量。

在这份已迁移数据的隔离复制件 /tmp/ceo-native-copy.pXJcD4 上，复用现有真实 CLI approve harness，通过两次原生调用（Consumer／Audit）和一次内存发送／readback，新增 task 5391765958070050 的结果、业务 Attempt 和回执，原有十一表行逐项未变。随后把该任务、运行和回执以及新 Email 写入（共 9,403 行）纳入 current-data inverse。逆迁后的全部邮件／分类摘要匹配；使用当前生产 Git 提交导出的 app 代码在临时目录正常冷启动，读取同一任务／运行／attempt／回执快照和 executed 终态，逐项与 inverse 前一致，无新 CLI 或 provider 执行。真实 child 再正迁随后通过，整轮 582.052 秒；全部邮件／分类值和新真实 CLI 任务／业务结果／回执一致，正常新代码读回仍为 executed，quick_check=ok，只有源 FK finding。源代码提交号和阶段证据在现有 evidence 中；本轮生产仍只读。


发布接线局部独立核对：Luna 只读检查 updater／deploy／coordinator／paired child／inverse，Root 重新阅读关键调用点，确认 updater 停稳后的同一 backup 路径传入配对迁移及原文恢复；十个 inverse 组件在 COMMIT 前完整输出，父进程成功时要求齐全；正常 child 执行 Store 冷开、会议退休和当前 selected baseline 准备。范围内未发现遗漏。该检查不代表整个候选最终审查批准；主验收已同步最新整轮通过状态，仍未提交、推送或部署。


## 同输入 Console 查询与新增发布差项

对最新 9,402 封邮件快照，使用实际生产提交导出的旧 app 与候选 app、只读 URI、同样 8 MiB 连接缓存和禁用 mmap，各查询三次。队列返回摘要完全一致，旧中位 0.339799 秒；首次候选 10.096317 秒。执行计划复现 reply public ID 的生成 CASE 阻止现有 numeric identity 部分索引，导致每个 run 重新扫描 Tasks。改为原 legacy_key 的明确 cast，并保留索引准确谓词后，中位 0.365887 秒，三次摘要均保持一致；NULL legacy key 仍返回 NULL。原先“直接物理 ID”的局部实现会改变合法 NULL 行，已移除，未用于发布。

History 首屏 50 条中位由 91.295710 秒降至 13.069750 秒，总数查询由 79.331532 秒降至 13.953271 秒；首屏完整结果摘要及总数 22,796 均一致。测量期间存在副本／测试 CPU，故为实际观测，非独占机器的严格性能基准；未声称 History 已达到理想页面延迟。

Attention 旧版 9 行、候选 11 行：额外两条是失败 direct unsubscribe 对应的 reply tasks（405078、405076）。源行值一致，已核实生产提交含上游 `53c72dc2e` 的 Attention 隐藏行为，而本 worktree 还未集成该增量。最终集成必须保留此上游行为并重做 Attention 差分，不能把差异计为接受的产品变化。当前本地 HEAD 与已知 origin/main 双向分歧，不可覆盖或强推；本项与最终合入／发布一并处理。

触发器修正后，provenance vector／final current-data inverse／Email completion 三文件补验 26 项通过（45.79 秒）；未发生真实来源请求或发送。Luna 只读复核了编译覆盖及 native 历史工厂排除范围，未发现范围内差项。整库当前修正仍待实际 child 结果，不能由这些局部测试提前计为通过。

触发器修正后的同一原始输入、真实 child 正迁与正常冷启动已通过（447.826 秒）。VACUUM 后表 365,617,152 B、索引 105,529,344 B、schema 2,146,304 B、free 0，总计 **473,292,800 B**，比相同原始输入 490,651,648 B 减少 **17,358,848 B（16.5546875 MiB）**。相对前一候选仅增加 8,192 B schema 页；新候选尚未进行那 200 封 Email 小批，不把旧批次空间收益混入本次结构对比。229 来源绑定、十核心＋账本、quick_check、源 FK baseline 及最终 Tasks/Actions 不含虚拟 NEW/OLD 类型判别均通过。其后的隔离真实 CLI 执行／审查及新增当前数据逆迁／实际 child 再正迁均已通过（673.713 秒），完整字段核对及结果见顶部当前结论。发送使用替身，无生产写入；本地存储检查点已提交，尚未推送或部署。

触发器修正后的真实 CLI approve 已通过：Consumer／Audit 两次原生调用，1 次内存发送与 readback，0 次真实发送。新 task **6130645274975311** 完成业务结果、Attempt、回执和正常重开；原有十一表行逐项不变，原生 session／记录范围有效。已启动该真实新任务及新增 Email 行的 current-data inverse／旧生产代码冷启动读回／实际 child 再正迁；这轮往返仍在运行，不提前计通过。


已移植两个已部署的上游 Attention 修复（53c72dc2e 退订失败隐藏、fd8a05b2d OA pending 描述），仅把原 SQL 适配为既有 Task20 索引／生成列及 Attempt metadata 的 action 字段，不增加策略。上游原控制与 typed-only 回归先 2 fail（6.48 秒），修正后连同轻量 Attempt 控制 3 pass（5.93 秒）。同一副本与实际生产旧代码的 Attention 现在九条身份、每个返回字段 hash 全部一致；首轮九条身份已一致但两条 OA 文案不同，补回上游原文后才计完全一致。失败任务／Attempt／History 记录不删除。整个 origin/main 增量尚未合并；这两项是针对已查实差异的内容移植，不能代表全分支集成完成。
