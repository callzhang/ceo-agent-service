# Database redesign: final acceptance

唯一主验收入口。对应 [设计 spec](superpowers/specs/2026-10-11-database-redesign-design.md)；原始结果及边界保留在 [证据记录](database-redesign-final-capacity-evidence.json)。旧诊断、局部通过、副本通过和生产生效分别记录。

## 当前结论

**数据库结构改造已上线并通过生产读回。** 2026-10-19，官方部署从 `f836b6f9` 更新到 `a3779da46b1563228a0f3de8948227d1e6cfec5b`，新PID25339，生产checkout干净；Store `2026-10-14.1`／Email52。10张核心表＋既有迁移账本，无持久FTS／shadow。主库VACUUM后 **470,466,560 B／448.671875 MiB**，free0，checkpoint完成；最新读回WAL1,396,712 B，正常写入会继续变化。healthz与整体健康healthy，最新独立质量检查0缺失／0违规，队列processing0、failed10、Attention4与发布前相同。

2026-10-19 09:21 UTC，在真实生产路径只读执行同一发布 coordinator，6.794秒通过：源 Store `2026-10-13.5`／Email51，目标 Store `2026-10-14.1`／Email52，231来源版本全部准备成功。生产 checkout `aa18a59858a477c3e446560fa956fbe13e26136a` 干净。未调用 publish 或生产 Store 构造器；该结果不等于迁移已上线。

2026-10-19 10:03 UTC再次只读核对，生产仍为同一干净commit，116普通表＋1 virtual＋4 shadow；主文件514,686,976 B、WAL4,161,232 B，最终结构尚未发布。这是更新生产时点，不能与冻结来源的候选容量直接作差。

最新只读生产预检基于干净 `223788a26296a13341c8a522f362742c5606cb27`，12.778秒准备238来源版本成功。已批准预览的真实child副本正迁715.512秒通过，VACUUM后480,055,296 B，9,402封均单份且≤1000字符，默认DAO列表及查询56.733–121.055毫秒且来源读取被禁止。

**功能／迁移／发布验收无未完成项。** 仅已授权测试临时目录的删除受平台自动审批阻止；未绕过拒绝。已核验Email引用发布副本：7,424正文及5,501模型输入退休，其余所有物理字段（包括两处预览）、schema及其他表逐字段hash不变。加入1封测试邮件并完成额外原文退休的副本VACUUM后461,447,168 B／free0，不能当作纯结构收益。最新main的Claude Consumer schema修正已通过c446d9537合入，相关62项通过／0.54秒；没有改变存储声明、迁移／inverse或Codex路径。

生产显式应用7,424正文／5,501模型输入引用；自动生命周期继续将新增完成邮件转为引用，最终读回为7,425／5,502。9,475邮件的预览≤1000且唯一；真实生产列表165毫秒、合同预览查询362毫秒，响应无全文。实际详情按来源读回15,953字符，预览为准确前1000字符；曾有一次502，随后相同邮件200，瞬态上游原因未查实，未使用本地预览冒充全文。生产IAB实际页面正常加载并显示当前9,477邮件及默认范围提示（与测量时点有正常新增）。Derek已明确批准有界预览例外及默认预览／主动全文检索；完整正文仍不重复长期保存。

## 需求与证据

| 目标 | 当前证据 | 边界／剩余事项 |
| --- | --- | --- |
| 原生 trajectory 是过程正文唯一来源 | session／准确记录范围／内容路径／完整hash定位；DB过程事件、审查调用及envelope副本退休 | 原生不可用明确显示不可用；不回写原文到trajectory |
| 约10张核心表，字段来自实际业务 | 下表、字段清单及当前有限声明；正常空库直接最终结构，无旧表／view写入兼容层 | 字段映射不能以物理表数代替 |
| 同数据、同VACUUM条件缩小结构 | 纯结构490,651,648→473,292,800 B；加批准预览后480,055,296 B，仍净减10,596,352 B | 固定229来源／956 Signal／9,402邮件；预览成本6,762,496 B单列，不混入后续退休或新任务收益 |
| 新任务完整执行及恢复 | 五分支13次真实CLI／4次替身发送；合并后另1原生任务 | 0真实发送；不是每个任务都单独整库逆迁 |
| 迁后新增数据可回退 | 新Task／Run／结果／回执经inverse→旧代码冷开→真实child再正迁→新代码冷开一致 | 不用旧备份覆盖当前业务事实；已知源FK finding保持，无新增 |
| 发布闭环 | main已合入，a3779da46官方deploy及生产读回通过 | 生产470,466,560 B，10核心表＋账本；临时目录清理受自动审批阻止 |

## 最终表、字段与退休清单

| 表 | 实际职责及必须保留 |
| --- | --- |
| tasks | 20类已有任务、队列、当前／claimed输入版本；身份、状态、原公开与物理ID关系 |
| runs | 执行、provider attempt、调度occurrence、恢复及原生session／记录范围；共11种已有类型 |
| actions | 61类已有方案、审查、修订、动作、尝试、步骤、事实与回执；关系、唯一性、公开ID及原事务语义 |
| business | Project、Task、Attention等业务对象及已采用事实 |
| emails | canonical邮件／当前分类；账号及稳定身份、必要状态、当前计划、业务结果与来源引用 |
| provenance | 真实共享来源身份、版本、locator、hash／字节数；无正文；单次来源直接内联 |
| configs | 既有账号／分类／Skill／路由配置与必要共享版本metadata；无训练全集 |
| provider_state | provider当前逐封邮件事实、同步身份／水位；不是大体积账号配置 |
| schedules | 定时配置、安排及既有run关联 |
| state | 既有状态、水位、lease和工具回执；延期回执沿用service_state命名空间 |
| email_schema_migrations | 既有迁移账本，非核心业务表 |

Tasks／Actions仅存稳定整数 `_kind` 和按真实字段声明排序的JSONB值向量；`record_type` 为只读生成名称。原SQL类型、负零／BLOB、ROWID／水位、公开ID、FK、CAS、claim／lease和约束顺序保持；历史TEXT声明仅用于显式来源迁移／inverse，不是运行时fallback。此前容量增长来自对象JSON的重复字段名及类型；此表示修订已包含完整索引成本验证。

字段方向清单共1,611项，已按最终声明校正53项（49个Email公共字段及来源容器／引用）。清单位于 [总字段映射](superpowers/specs/2026-10-11-database-redesign-field-map.csv)及各阶段addendum；准确物理槽位以当前有限声明为准，CSV `payload.<原字段>` 不是对象JSON格式。共享来源FK实际为 `actions.ba_task_signals_provenance_id`。

| 退休结构／字段 | 最终处理 |
| --- | --- |
| business_source_documents正文及容器id／created_at | 删除容器；Signal身份保留；单次locator内联、真实共享版本用provenance FK |
| Signal.source_document_id及旧正文索引／触发器 | 来源关系改为内联／共享provenance，保留准确来源版本与原唯一性／FK |
| Agent过程事件、审查调用正文、final_result／envelope、派生搜索正文 | 只读原生范围；采用的业务结果、执行状态和回执继续保留 |
| 独立codex_session_search_index／FTS／shadow | metadata归runs；既有检索从原生文件构造有界内存corpus，原评分不变 |
| work_projects.memory_context_json | 停止复制query／memories；goal／facts／current_state等业务事实保留 |
| 终态任务输入重复正文、meeting／OKR来源快照 | 准确来源验证后退休，保留身份／hash／bytes／版本；来源缺口不伪造 |
| reply_task_deferral_receipts | 19字段映射既有State：公开id进入key、其余18字段进入value；request_id幂等、paused条件及原事务不变 |

## 原文、训练与模型保留

冻结来源对照：初始220版本（23 native、172听记、4周报、1 Console事实、20授权不可用原文），新增9版本沿summary input→task run→completed runtime attempt准确找到；229版本／956 Signal。定位清单只含坐标及校验。发布前最后预检已准备238版本；不将该增量混入固定容量对照。

限定20版本、489,023 UTF-8字节按 [已确认退休决定](superpowers/specs/2026-10-18-provenance-original-retirement-decision.json)移除，只保留定位／身份／hash／字节、业务事实和回执，并显示原文不可用。不得扩展到其他未核实原文，也不另存归档副本。

Email正文与model input分别按实际序列化、完整hash／字节证明；单次引用内联既有 `message_source_reference_json`。仅完成且无分类／回复／邮箱动作／dispatch活跃依赖时CAS退休；完成／恢复入口已接入。未知来源保留原文，模型输入不能用可读正文替代。

历史补验3,387候选、3,008.415秒，新增退休3,107正文／2,457模型输入；200错误（112 timeout、84 Message-ID缺失／歧义、4 UID不可用）及52次不能证明输入的读取未冒充成功。合计7,424正文引用／5,501模型引用；仍保留1,853正文7,053,406 B、3,902模型输入8,480,437 B。`scripts/retire_verified_email_inputs.py` 有9项及实际副本preview／apply证明；只发布仍匹配的来源引用，不复制副本业务行；已于本次生产发布后显式应用，当前并发更新仍受原始hash／身份／状态匹配限制。

956 owner案例身份唯一，混合输入冷重建特征／标签与原副本逐项一致（152引用＋804保留，100.618秒）；正常训练使用既有最新selected文件或准确来源，标签目录不无意义读取正文。外部selected／folder各最新一份的read_snapshot、schema及digest已核对，无活跃pin。2026-10-18外部产物只读核验有三份权重：当前54,726,153 B、最新candidate54,710,702 B、历史模型8,905,681 B。2026-10-19重新核对，实际active.json／previous.json均不存在，online当前为0930模型，0919模型仅因历史切换记录被清理策略保留；此前“fallback manifest引用”的描述不成立。已移除history-only权重保护，切换事实／回执原字节保持，实际manifest依赖及训练活动保护不变。经典／embedding两种历史制品回归先失败，修复后registry整文件43 pass／1.60秒。生产正常worker清理已生效：历史8,905,681 B权重已移除，只保留当前54,726,153 B和最新候选54,710,702 B；active_run_id为空，无实际active／previous manifest依赖。业务切换事实和回执继续保留。

## 同口径容量与剩余占用

固定输入116普通表、229来源版本、956 Signal、9,402邮件，无合成新增行；两端均VACUUM。

| 字节 | 原116表 | 最终结构 |
| --- | ---: | ---: |
| 表页 | 399065088 | 365617152 |
| 索引页 | 91213824 | 105529344 |
| schema页 | 372736 | 2146304 |
| 空闲页 | 0 | 0 |
| 总量 | **490651648** | **473292800** |

净减17,358,848 B／16.5546875 MiB／3.538%；索引增加14,315,520 B已计入。不得以删除更多历史解释纯结构收益。

上表为无新增预览的纯结构基线。已批准1000字符预览实测：表页372,379,648 B、索引105,529,344 B、schema2,146,304 B、free0，共480,055,296 B。相对纯结构基线增加6,762,496 B／6.44921875 MiB；相对同一旧库仍节省10,596,352 B。9,402份预览逻辑UTF-8合计7,834,970 B，与页占用不混同；完整输入未因预览截断。

额外来源退休＋批准预览＋1封测试邮件的副本为461,447,168 B，属于混合阶段结果。**下表为实际生产20:22 UTC附近的页统计**，包含后来新增业务；不得与固定输入的结构收益混同。

| 表 | 表页B | 索引B | 行数 |
| --- | ---: | ---: | ---: |
| actions | 139132928 | 29892608 | 100494 |
| business | 10530816 | 1220608 | 5171 |
| configs | 7585792 | 1396736 | 6090 |
| email_schema_migrations | 4096 | 0 | 39 |
| emails | 52162560 | 4067328 | 9475 |
| provenance | 98304 | 49152 | 148 |
| provider_state | 14372864 | 11403264 | 72344 |
| runs | 87855104 | 47312896 | 254651 |
| schedules | 16384 | 8192 | 15 |
| state | 6590464 | 2404352 | 23674 |
| tasks | 42778624 | 9502720 | 36578 |

另sqlite_sequence4096 B／51行，schema2,146,304 B，free0。在线dbstat的页合计470,536,192 B，与文件stat相差69,632 B；表统计读的是含WAL的在线快照，主文件及WAL持续写入，不将两个不同观测强行凑等。迁移后尚未整理主文件571,158,528→470,466,560 B，回收100,691,968 B；这包括空闲页和额外原文退休，不称为纯结构节省。备份目录只有1份有完整性标记的SQLite备份，512,090,112 B；与主库和模型权重分开统计。

## 完整业务链和当前事实inverse

批准预览后的新演练在同一冻结生产副本迁后，经公开persist_scan_result新增1,300字符邮件，预览1000且原始／模型输入保留。inverse301.427秒、旧生产代码223788a2冷启动读回304.606秒，随后真实child再正迁及当前代码冷启动通过；全部9,403邮件两种公共投影逐字段hash完全相同。最初汇总脚本在最终FK检查引用不存在的报告key，改从已保存的release metadata读取并补完quick_check／FK／字段复核，没有重跑迁移或掩盖原始脚本失败。

完整生产副本实际coordinator／child正迁、Store／EmailStore冷启动通过。迁后新增external_action_result／effect_intent／execution_receipt及Task／Run，经当前数据inverse、旧代码冷开、真实child再正迁、新代码冷开一致；645.788秒。后来补验旧代码78e8c4474冷启动读回1739.245秒；review_candidate／audit_review／execution／result／sent_reply／reply_attempt全部公开字段、SQLite类型和值匹配。旧代码更新不涉及存储声明的部分不重复整库往返；最终发布仍需按当时HEAD核对。

| 真实CLI分支 | 调用 | 替身发送 | 证明 |
| --- | ---: | ---: | --- |
| approve | 2 | 1 | 执行→审查→Attempt／业务结果／回执→冷重开 |
| revision | 4 | 1 | 审查退回→修订→再次审查→结算 |
| no_action | 2 | 0 | 完整无发送终态 |
| technical_retry | 3 | 1 | CLI启动失败后恢复 |
| settlement_recovery | 2 | 1 | 结算中断恢复，不重复模型或发送 |

合计13次真实CLI／4次内存替身／0真实发送，与整库往返为相同物理声明上的独立副本。合并后任务5715333616543262另经真实执行／审查：done／executed，业务Attempt／结果／回执各一份，dispatch／readback已核验；冷重开不重执行。quick_check=ok，源已有runs190042→tasks/fkid1保持，无新FK finding。既有外部训练文件按原完整hash供旧代码读取，不放宽恢复规则、不重复计原文退休。

## 当前验证与准确边界

| 验证组 | 最新有效证据 |
| --- | --- |
| Store存储API／schema／状态恢复 | 整文件450 pass／260.05秒，3worker，冻结源码；含原115历史失败当前合同映射 |
| Task Attention multisource | 156收集ID按1–71／72–76／77–90／91–156分段71／5／14／66 pass；去除无用wrapper后2项再pass；非一次不中断整文件 |
| worker恢复／触发 | 原29失败ID集合与当前收集完全一致，29 pass／313.32秒；原assert AST未变 |
| 微信发送模式／Task建议 | 整文件33 pass／55.19秒、17 pass／28.93秒；FakeSender、精确PreparedSources，0真实发送 |
| Signal／业务语义 | 整文件444 pass／176.96秒；正文只在请求内，持久Fact身份／hash／bytes／共享／不可变／失败原子性保持 |
| History／失败History | 整文件33 pass／66.20秒；失败10项追加19.41秒；同失败副本完整API269k→59k VM步，原100k预算不变 |
| Scheduled Consumer／最新native增量 | 整文件38 pass／94.87秒；合入main后native trajectory整文件10 pass／7.61秒，McpToolCall回执归一化 |
| 来源读取及HTTP／搜索批次 | 四文件85 pass／48.58秒；页面1／2／25同503源完整total／顺序／page，批量≤50，原502 HTTP fake已支持UID-set |
| 回复Attempt紧凑存储 | `tests/test_reply_attempt_action_storage.py` 177 pass／6既有skip／10.62秒；原值／类型／默认／分配器／事务／字段拒绝 |
| Follow-up／sync | 当前写入原表对照62 pass／25.74秒；历史TEXT inverse26 pass／202.02秒；新事实／ROWID／水位保留 |
| Task delivery及历史postfix | 原11观察失败定向闭合，54轻量＋5配对／inverse＋2历史四族；postfix两个TEXT模块110 pass／63.70秒。准确文件映射在证据中，不改称新整文件通过 |
| 发送回执历史存储 | frozen十二类型TEXT组件整文件27 pass／0.24秒；沿用历史DML adapter，全部原assert AST一致；不冒充最终运行时证明 |
| TODO同步派发／Project证据重放 | 整文件19 pass／7.03秒、28 pass／26.72秒；派发仅显式选择当前vector夹具，原lease／retry／receipt／atomicity断言保持，重放模块无需修改 |
| Task Agent上层 | 247当前收集节点全部有通过证据：冻结前缀174、相对日期1、原尾段56，加失败及受影响参数20 pass／32.08秒；root校验去重并集247及唯一错误文案node映射。原794断言增至804，保持错误证明拒绝／无写入／业务／回执语义；非一次不中断整文件 |
| 其他迁移／调用方 | 既有Configs39、Email元数据39、dispatcher74、路由63、回放27＋子进程1、只读Inspector12、检索35、Project评估25、Workbench manifest20、回复输入12、状态摘要9等证据按原范围复用；不把scoped结果改为整文件通过 |
| 前端及浏览器 | 46文件734 pass／2既有skip；tsc／vite通过；真实Chrome隔离Workbench2项106.23秒；原2秒启动预算下连接复用23项＋9连接控制通过 |

本轮关闭最后一个旧结构命令引用：`bb01074b8`退休隐式native-copy／FTS清理。当前最终空库先复现FTS不存在，改后6项CLI回归通过／1.48秒；保留三个输入清理函数AST逐一完全相同，独立只读审查无发现。旧native清理的4个缓存前提案例随已退休功能取消，旧来源迁移及inverse实现不变。

旧广泛诊断17,217 pass／1,899 fail／118 skip／58 error是持续修改中的失败收集，非稳定候选通过／失败数。按当前模块、精确node或明确退休／替换合同映射；尚未找到匹配的项目继续登记，不重复已经足够的全量回归。Source13旧schema真实正迁先前已完成，原lease经当前投影在同副本准确读回；后续Store整文件亦通过。

发布路径代码复核：官方deploy／updater及inverse机制保持。相对前次基线，paired publish新增退休前的预览回填，因此本次重新执行整库正迁／当前数据inverse／真实child再正迁；其余同物理声明的真实CLI证据复用。现有失败处理先对当前数据库inverse，再回退代码及重启；不以迁前backup覆盖新业务事实。

## 搜索性能与发布差项

全文仍为Unicode casefold连续子串、准确total／顺序／分页；来源缺失或变化明确报错。50条来源批次独立于页面limit：同一最终DAO9,402候选中搜索“合同”需9,376原文判定；页面25的502账号会话降至269，页面1原9,376亦降至269。此为操作数，不能当作网络性能证明。

真实只读样本30封同folder、已有完整来源校验的邮件全部重取，12.674秒：fetch9.597、连接1.285、logout0.390、metadata0.476秒。主要成本在上游读取，未按样本线性推算全库。这是全文检索的历史上游读取成本；已批准方案的默认列表改为本地预览，不再逐封读取来源。真实整库DAO默认列表56.733毫秒，合同／STRASSE／周五查询114.125／118.088／121.055毫秒，分页25；整库HTTP四个请求177.345／198.655／178.731／260.548毫秒，0 provider调用；生产端到端仍单独核对。

此前二选一已被2026-10-19明确批准的折中方案取代：**最多1000字符纯文本预览＋本地默认搜索＋按需详情／主动全文检索**。不再等待旧选项答复；有界预览是已批准的重复内容例外，须单列其容量成本，不改变完整正文及模型输入的来源校验。

- [x] 最终结构／字段方向、严格同输入容量、真实CLI及迁后新事实inverse／reforward。
- [x] 已完成来源核验及限定20原文退休决定；Email未核实来源保留，退休发布工具有副本证明。
- [x] 上述当前回归及关键独立只读审查；后续增量按实际影响补验，不重跑无变化部分。
- [x] 本轮发现的当前消费者证据缺口已闭合：TaskAgent247节点、Sync派发19、Project重放28、发送回执历史27；旧广泛诊断仍保留原范围，不宣称整仓通过。
- [x] 搜索产品方式已批准，1000字符／唯一owner／完整详情／显式全文／输入回到预览已通过单测及隔离UI。
- [x] 整库HTTP性能及预览迁后新增邮件的current-data inverse／旧代码冷开／真实child再正迁：9,403封所有邮件字段相同，quick_check通过，只有原始FK finding，无模型或发送重放。
- [x] 历史模型权重已由正常worker清理；生产只留当前及最新候选，两份实际权重已读回。
- [x] 最终238来源绑定刷新、主线推送及官方`python -m app.deploy`成功；已有完整性标记备份，失败inverse机制已经当前数据演练。
- [x] 生产PID25339／healthz／queues／Attention／History／最终schema及VACUUM容量读回；fresh质量检查通过，无新增失败或卡住证据；测试触发真实发送0。

## Approved1000-character preview implementation plan (2026-10-19)

**Goal:** Resolve the search release gap with one bounded preview per existing Email row, local default search and explicit full search. **Architecture:** Reuse class_preview/message_preview, populate from displayed normalized original before body retirement, preserve full source hashes and inverse business facts. **Tech:** SQLite, Python reader/API, existing React EmailList.

- [x] Backend TDD: new preview tests prove1000 Unicode characters, HTML/plain display normalization, a single populated preview, no source calls for ordinary listing/query, and deep-only keyword matching only with full scope. Run `pytest -q tests/test_email_preview.py tests/test_email_classification_reader.py tests/test_email_source_web_api.py` in task temporary paths; existing full-read tests explicitly select full scope.
- [x] Implement `email_preview.py` bounded extraction/backfill, `persist_scan_result` and `compact_email_message_body` preview writes, paired publish backfill before retirement, Reader default local query and API `search_scope` enum. No new schema field/table, no change to classification/model inputs or external sends.
- [x] Frontend: extend EmailClassificationListParams with `search_scope`, default preview label and explicit full-search action, query changes return to preview mode, full loading/error UI and ordinary detail unchanged. Existing React tests plus full build.
- [x] Root review and production-copy forward/inverse/old-code cold readback/actual-child reforward verify approved preview policy and all9,403 Email fields. Existing unchanged CLI/receipt evidence reused; current-copy HTTP177–261ms,0 source calls.
- [x] Source-proof publication current-copy check:7,424 bodies／5,501 model inputs retired; all other physical fields/schema exact, including preview; quick_check／source-only FK verified.
- [x] Push/integrate and official deploy completed at a3779da46; production schema/queues/Attention/History/preview/source detail and quiet VACUUM verified.
