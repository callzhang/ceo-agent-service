# Database redesign: final acceptance

本文件是最终结构、容量、恢复和发布的唯一主验收入口。详细实验、失败记录和必要证据路径统一保留在 `database-redesign-final-capacity-evidence.json`，不以早期中间版本结果代替当前验收。

## 当前结论

候选已实现 **10 张核心表＋1 张已有迁移账本**；同一不可变生产输入、两侧均 VACUUM：**490,651,648 → 473,292,800 B**，净减少 **17,358,848 B（16.5546875 MiB，3.538%）**。最新历史来源补验后的演练副本为 **457,007,104 B**；它包含额外任务记录及原文退休，不能作为纯结构收益。

**未推送、未部署、整体尚未验收通过。** 最新只读核验的生产代码为 `78e8c4474c05fee31ed27541b238f30381c8ba33`，工作区干净。候选已合并 `origin/main 78e8c4474` 的新增三条提交；原回退冷启动证据使用旧生产 `2246d5184`，发布前须以最新生产代码补验。

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

原 1,611 字段方向清单已按实际最终声明校正 53 项：49 个 Email 公共字段映射到真实 canonical 列，明确正文／模型输入的条件退休引用；原正文容器 id／created_at 退休，来源 identity 与 Signal 的旧文档 FK 改为实际内联／共享 provenance 关系。共享 FK 的准确生成列为 actions.ba_task_signals_provenance_id，已对当前副本 table_xinfo／FK 核验；不新增字段。其余 typed payload 的物理槽位以 storage_payload／各域有限声明为准，CSV 的 payload.<原字段> 是业务字段含义，不是 JSON 对象存储格式。

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

## 来源核验与原文退休

初始220个业务来源版本：23原生 trajectory、172听记、4周报、1 Console事实、20份已授权原文不可用。新增9版本沿实际 work_summary_input → task_agent_run → 已完成 runtime attempt 找到并准确读回；合计229来源版本、956 Signal绑定。定位清单 `database-redesign-final-trajectory-locators.json` 只含定位和校验信息。限定20版本、489,023字节的退休依据为 `superpowers/specs/2026-10-18-provenance-original-retirement-decision.json`，不扩大至其他未查实内容。

运行时按原生session／文件／记录／内容路径与完整hash定位；原生确实不可用后才使用真实上游。单次来源内联，真实共享版本使用provenance FK；不保存正文DAO、trajectory副本、长期正文缓存或FTS副本。逆迁只从已有verified backup或准确原生／上游来源恢复字节，不覆盖当前业务事实。

Email正文和模型输入分别证明，写入既有 `message_source_reference_json`；仅已完成且无活跃处理的输入可退休，按身份、完整原字节、业务状态做CAS。正常完成／恢复入口已接入，保留状态、分类、训练纳入标记、业务结果与回执。模型输入有其原有规范化语义，不能用可读正文直接代替；来源完整hash／字节数必须相等。

历史最终补验：3,387候选、3,008.415秒，新增退休3,107正文／2,457模型输入。200错误为112 timeout、84 Message-ID缺失或歧义、4 UID不可用；52次成功读取仍不能证明任一原输入。未证明原文均保留，timeout不等于原文确实不存在。合并前次与已有pilot后，共7,424正文引用／5,501模型引用；仍保留1,853份正文（7,053,406 B）、3,902份模型输入（8,480,437 B）。不宣称全部原文移出。

来源补验逐字段证明其他物理数据／schema／水位不变，quick_check=ok，FK仅原基线。历史引用发布工具 `scripts/retire_verified_email_inputs.py` 已有9项测试及实际副本预览／应用证明；目标仅导入仍匹配当前事实的定位，不复制副本业务行。**尚未应用到生产。**

## 同口径容量

固定输入为116张普通源表、229来源版本、956 Signal、9,402邮件；无合成新增行，两侧均VACUUM。

| 字节 | 原116表 | 最终结构 |
| --- | ---: | ---: |
| 表页 | 399065088 | 365617152 |
| 索引页 | 91213824 | 105529344 |
| schema页 | 372736 | 2146304 |
| 空闲页 | 0 | 0 |
| 总量 | **490651648** | **473292800** |

结构净减少17,358,848 B；索引增加14,315,520 B已计入。此前合表增大的主因是重复JSON对象字段名和类型；Tasks／Actions采用固定字段顺序JSONB值向量、单份稳定整数 `_kind`，`record_type` 为只读生成名称。顺序和类型码属于显式schema声明，不新增业务字段、任意键或新旧格式回退。

历史输入退休后的最新演练副本（9,403邮件，含新增原生CLI任务）457,007,104 B，schema2,146,304 B、freelist0：

| 表 | 表页B | 索引B | 行数 |
| --- | ---: | ---: | ---: |
| actions | 135262208 | 29585408 | 99229 |
| business | 10498048 | 1212416 | 5135 |
| configs | 7442432 | 1347584 | 6028 |
| email_schema_migrations | 4096 | 0 | 39 |
| emails | 47312896 | 4034560 | 9403 |
| runs | 85291008 | 46084096 | 248599 |
| provenance | 90112 | 45056 | 141 |
| provider_state | 14368768 | 11399168 | 72333 |
| schedules | 16384 | 8192 | 15 |
| state | 6561792 | 2396160 | 23596 |
| tasks | 42471424 | 9424896 | 36251 |

另有SQLite内部sqlite_sequence一页4096 B／51行。此表不是生产容量或纯结构对照。

## 业务链和当前数据恢复

完整生产副本已通过实际coordinator／child正迁、正常Store／EmailStore冷启动；迁移后新增external_action_result、effect_intent、execution_receipt并按当前事实逆迁、真实child再正迁。保留原公开身份、SQLite类型、旧回执及新增字段，不回退成旧备份。quick_check=ok；原库已有runs190042→tasks/fkid1保留，无新FK finding。

| 真实Codex CLI分支 | 原生调用 | 替身发送 | 结果 |
| --- | ---: | ---: | --- |
| approve | 2 | 1 | 执行、审查、业务Attempt／结果／回执、冷重开 |
| revision | 4 | 1 | 审查退回、修订、再次审查与结算 |
| no_action | 2 | 0 | 完整无发送终态 |
| technical_retry | 3 | 1 | CLI启动失败后恢复 |
| settlement_recovery | 2 | 1 | 结算中断恢复，不重复模型或发送 |

五分支共13次真实CLI、4次内存替身发送、0真实发送；与整库往返为相同物理声明上的独立副本，不声称五任务各做整库逆迁。合并后另有真实任务5715333616543262：done／executed，执行与审查、业务Attempt／结果／发送回执各一份，替身dispatch／readback已核验，旧11表事实不变，冷重开不重执行。

新增独立正文／模型输入来源证明后的最新完整往返：9,403邮件及新任务／结果／回执，经inverse、生产旧代码2246d5184冷启动、真实child再正迁、新代码冷启动，**645.788秒通过**。初轮漏带两份既有外部训练文件导致child退出，按原read_snapshot与完整hash补齐后复验；没有放宽运行逻辑。最初已恢复46正文／38模型输入，后续重复inverse不得再次计数。延期工具新回执、幂等、暂停／恢复及当前事实逆迁另有聚焦证据。

## 来源消费者与性能边界

正常训练使用既有最新selected文件或准确来源；标签目录、未选训练来源、已分类身份查询不再无意义地读取原文。保留当前可运行模型、必要前版／候选及每类最新训练输入；外部文件read_snapshot校验已完成，无多余权重或活跃pin。

owner案例全部956个身份唯一，冷重建特征和标签与原始副本逐项相同。当前152引用输入＋804保留输入的混合读取耗时100.618秒；不是全退休性能。相同秒内改标签通过已有字段／完整输入hash的临时digest识别，不增持久缓存、字段或排序规则。

Console列表／详情、任务完整线程、任务创建／恢复已使用准确来源读取；IMAP批量FETCH复用账号lease、每组至多50，保持完整selected text/HTML、UID／代际／身份和搬移语义，不下载附件。真实样本与scalar结果相同，但较大scalar对照超时，**没有证明整体加速倍数**。

**正文全文搜索仍是开放取舍。** 当前保留原Unicode casefold连续子串、准确total／顺序／分页；来源缺失显式报错，不能以部分结果冒充完整搜索。实际all范围9,287行，搜索“合同”约9,261行需读来源；真实9来源小案例27.58秒。不是可据聚焦正确性宣称已解决的全库同步搜索性能。已提问是否仅保留元数据搜索；尚无明确答复，不默默取消正文搜索、不引入持久正文副本。

## 回归状态与尚未完成项

- [x] 最终结构／字段去向／删除清单，10核心表＋账本，正常空库直接最终声明，无旧表／view兼容别名。
- [x] 同一输入容量对照、完整副本迁移、迁后新增事实inverse／reforward、原生CLI五分支及合并后新增真实任务。
- [x] 本轮有界来源补验、956 owner案例冷重建、来源未查实原文保留、最新副本容量独立核对。
- [x] 真Chrome Workbench两项检查：隔离数据库／非执行替身，2 pass、106.23秒；前端tsc／vite构建通过。既有Python Workbench端到端启动差项已以同线程连接复用修复；与执行器／连接控制共23项通过，2秒阈值保留。
- [ ] 更广Python回归全部差项闭合。前轮28 failed／60 passed／87 skipped、303.41秒；历史delivery四文件90 pass、bootstrap2 pass修正后，新轮发现Email history／error组件旧夹具问题；error整文件13 pass／655.94秒，Email history整文件7 pass／489.25秒；其余历史postfix110、receipt22、通用Actions68、reply发布26、follow-up5项已分别通过，反馈／runtime router／代际与终态关联／配置存储已补齐定向验证；worker既有恢复组11项及追加协议分支12项、配置存储75项、代际终态70项、配置集成／任务详情追加11项均已通过；Attention三个受影响节点已通过，Workbench启动差项已闭合；task-delivery已闭合11个观察到的失败，54项当前轻量控制通过；后续诊断回归差项仍在核对。已通过且未变的大批量历史用例复用原证据，不重复堆叠运行。
- [x] 邮件输入／URL同步时序问题：确定性回归复现后已修复，原逐字输入／最终请求断言保留；主审独立3项通过，Email＋Scheduled定向107项通过。降低并发后完整前端46文件734 pass／2 skipped，433.57秒，超时阈值不变；主审tsc／vite构建通过。
- [ ] 正文搜索产品取舍／性能边界处理，不能据局部来源读回视为全库可用。
- [ ] 最终稳定候选集成审查、远端增量核对、提交／推送、既有deploy发布，以及生产PID／health／queues／Attention／History／实际schema和容量读回。

当前Store目标2026-10-14.1、Email目标52；52是schema版本号。真实生产路径readonly release preflight曾通过旧Store2026-10-13.5／Email51到目标配对准备（229来源版本），未运行生产publish／constructor。最终发布仍须重查当前生产增量，不能把该预检或副本通过写成已上线。

## 最终回归补验：Workbench 连接与真实历史来源

Workbench 执行线程原先每个 Store 操作重新打开连接；最小执行流程测到 7 次打开。限定 turn 内同线程复用后为 1 次，各操作事务、其他线程和只读快照仍独立。连接回归、执行器和原端到端检查共 23 项通过（80.54 秒），原 2 秒等待未改；9 项连接打开／schema 检查控制通过（6.10 秒）。独立只读代码复核未发现具体缺陷。它不代表正在运行的广泛诊断回归已全通过。

真实发布旧版 `2773d6f4` 的 fresh 数据库经整库迁移后，直接动作领取、重试、完成和恢复与旧版对照一致（217.46 秒）；准确声明 profile 的 6 项迁移／逆迁控制通过（48.29 秒）。整库新增事实逆迁、旧版重开和再正迁已通过（680.66 秒），未触发真实对外发送。

邮件 DAO 的 whole-publish 夹具代表补验：reply 1项通过（429.08秒），unsubscribe 2项通过（439.57秒）；不代表两个整文件已全通过。Direct 的新只读模板复制初轮使用 deep copy，遇到模型内部 frozen mappingproxy；已独立复现并改用既有 JSON 序列化往返，夹具整体复验待完成。Validator 当前对象定位及旧夹具异常预期仍在收尾。旧 task-delivery 诊断11 failed／55 passed不计作当前通过；修正后的历史四族两变体控制2项通过（92.48秒），配对代表仍在运行。当前批次排空前不再追加整库迁移，也不延长超时。

最终 schema 的元数据拒绝／不修复验证整文件39项通过（377.06秒），历史Email迁移新增事实／类型／ROWID四项通过（8.74秒）。Direct／Validator六个定向差项已分别4项与2项通过；反馈对照只排除旧getter的正文展示字段，并明确断言当前getter不返回它们，原有业务状态、修订、CAS和ledger校验保留。随后对冻结测试源码作合并确认，尚未据局部结果宣告广泛回归完成。

冻结源码的 Direct／Validator 合并确认：23 passed／7 deselected，359.50秒。Task delivery 观察到的11个失败均已有定向闭合：剩余5个真实配对／逆迁控制通过（841.08秒），最终helper／空库／allocator等54项轻量控制通过（17.37秒）；不宣称新跑完整文件。此前已通过且本次未变的paired error／feedback证据复用，后续更广诊断仍需逐组闭合。

配置组件发布整文件39项通过（102.49秒）。历史源及逆迁新增事实显式使用 `68a137490` 的原生 TEXT Actions／Skill receipt 写入器，未以当前紧凑写入器改变历史夹具，所有原声明、类型、身份与逆迁断言保留。该结果来自 agent 工具回执，无另存日志。

最近生产只读核验（2026-10-19 05:22 UTC）：主文件511,643,648 B，WAL4,239,512 B，SHM32,768 B；Store2026-10-13.5／Email51。sqlite_schema 共121张表，包括116普通表、1张FTS虚拟表及4张FTS影子表，名称集合与固定源一致。生产已新增数据，该数值不替代同输入490,651,648→473,292,800 B的容量对照。
