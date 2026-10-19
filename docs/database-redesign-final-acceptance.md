# Database redesign: final acceptance

唯一主验收入口。对应 [设计 spec](superpowers/specs/2026-10-11-database-redesign-design.md)；原始结果及边界保留在 [证据记录](database-redesign-final-capacity-evidence.json)。旧诊断、局部通过、副本通过和生产生效分别记录。

## 当前结论

**候选已实现10张核心表＋1张已有账本；未推送、未部署、整体未完成。** 最新功能提交 `8faae621c` 修正仅被历史切换记录引用的模型权重保留；`a2589d733` 解耦全文搜索批次与页面大小；main 的原生 MCP 审查回执修正已通过 `b859e78ce` 合入。当前 Store 整文件450项、来源读取／HTTP四文件85项通过；其余既有回归证据见下表，不声称整仓测试全绿。

2026-10-19 09:21 UTC，在真实生产路径只读执行同一发布 coordinator，6.794秒通过：源 Store `2026-10-13.5`／Email51，目标 Store `2026-10-14.1`／Email52，231来源版本全部准备成功。生产 checkout `aa18a59858a477c3e446560fa956fbe13e26136a` 干净。未调用 publish 或生产 Store 构造器；该结果不等于迁移已上线。

2026-10-19 10:03 UTC再次只读核对，生产仍为同一干净commit，116普通表＋1 virtual＋4 shadow；主文件514,686,976 B、WAL4,161,232 B，最终结构尚未发布。这是更新生产时点，不能与冻结来源的候选容量直接作差。

当前主要差项：搜索产品方式／性能、历史广泛诊断与当前验证范围的准确区分、最终候选发布及生产读回。全文行为变化尚未确认，不能用缩小功能范围或持久正文缓存关闭性能差项。

## 需求与证据

| 目标 | 当前证据 | 边界／剩余事项 |
| --- | --- | --- |
| 原生 trajectory 是过程正文唯一来源 | session／准确记录范围／内容路径／完整hash定位；DB过程事件、审查调用及envelope副本退休 | 原生不可用明确显示不可用；不回写原文到trajectory |
| 约10张核心表，字段来自实际业务 | 下表、字段清单及当前有限声明；正常空库直接最终结构，无旧表／view写入兼容层 | 字段映射不能以物理表数代替 |
| 同数据、同VACUUM条件缩小结构 | 490,651,648→473,292,800 B，净减17,358,848 B／3.538% | 固定229来源／956 Signal／9,402邮件；不混入后续退休或新任务收益 |
| 新任务完整执行及恢复 | 五分支13次真实CLI／4次替身发送；合并后另1原生任务 | 0真实发送；不是每个任务都单独整库逆迁 |
| 迁后新增数据可回退 | 新Task／Run／结果／回执经inverse→旧代码冷开→真实child再正迁→新代码冷开一致 | 不用旧备份覆盖当前业务事实；已知源FK finding保持，无新增 |
| 发布闭环 | 新增main已合入；最新真实生产只读预检通过 | 搜索方式及性能未关闭；未推送／deploy／生产最终读回 |

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

冻结来源对照：初始220版本（23 native、172听记、4周报、1 Console事实、20授权不可用原文），新增9版本沿summary input→task run→completed runtime attempt准确找到；229版本／956 Signal。定位清单只含坐标及校验。最新生产预检另有2版本，共231；不将该增量混入固定容量对照。

限定20版本、489,023 UTF-8字节按 [已确认退休决定](superpowers/specs/2026-10-18-provenance-original-retirement-decision.json)移除，只保留定位／身份／hash／字节、业务事实和回执，并显示原文不可用。不得扩展到其他未核实原文，也不另存归档副本。

Email正文与model input分别按实际序列化、完整hash／字节证明；单次引用内联既有 `message_source_reference_json`。仅完成且无分类／回复／邮箱动作／dispatch活跃依赖时CAS退休；完成／恢复入口已接入。未知来源保留原文，模型输入不能用可读正文替代。

历史补验3,387候选、3,008.415秒，新增退休3,107正文／2,457模型输入；200错误（112 timeout、84 Message-ID缺失／歧义、4 UID不可用）及52次不能证明输入的读取未冒充成功。合计7,424正文引用／5,501模型引用；仍保留1,853正文7,053,406 B、3,902模型输入8,480,437 B。`scripts/retire_verified_email_inputs.py` 有9项及实际副本preview／apply证明；只发布仍匹配的来源引用，不复制副本业务行；尚未应用生产。

956 owner案例身份唯一，混合输入冷重建特征／标签与原副本逐项一致（152引用＋804保留，100.618秒）；正常训练使用既有最新selected文件或准确来源，标签目录不无意义读取正文。外部selected／folder各最新一份的read_snapshot、schema及digest已核对，无活跃pin。2026-10-18外部产物只读核验有三份权重：当前54,726,153 B、最新candidate54,710,702 B、历史模型8,905,681 B。2026-10-19重新核对，实际active.json／previous.json均不存在，online当前为0930模型，0919模型仅因历史切换记录被清理策略保留；此前“fallback manifest引用”的描述不成立。已移除history-only权重保护，切换事实／回执原字节保持，实际manifest依赖及训练活动保护不变。经典／embedding两种历史制品回归先失败，修复后registry整文件43 pass／1.60秒。生产旧权重尚未删除；发布后的实际清理及读回仍待完成。

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

后续完成额外来源退休并含新CLI任务的演练副本：9,403邮件，457,007,104 B，schema2,146,304 B、free0；这是混合阶段结果，不是生产实测或同口径结构收益。

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

另sqlite_sequence一页4096 B／51行。2026-10-19 07:52生产只读旧结构测量512,962,560 B／WAL4,152,992 B，不代表此刻大小，也不替代固定输入对照。

## 完整业务链和当前事实inverse

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

旧广泛诊断17,217 pass／1,899 fail／118 skip／58 error是持续修改中的失败收集，非稳定候选通过／失败数。按当前模块、精确node或明确退休／替换合同映射；尚未找到匹配的项目继续登记，不重复已经足够的全量回归。Source13旧schema真实正迁先前已完成，原lease经当前投影在同副本准确读回；后续Store整文件亦通过。

## 搜索性能与发布差项

全文仍为Unicode casefold连续子串、准确total／顺序／分页；来源缺失或变化明确报错。50条来源批次独立于页面limit：同一最终DAO9,402候选中搜索“合同”需9,376原文判定；页面25的502账号会话降至269，页面1原9,376亦降至269。此为操作数，不能当作网络性能证明。

真实只读样本30封同folder、已有完整来源校验的邮件全部重取，12.674秒：fetch9.597、连接1.285、logout0.390、metadata0.476秒。主要成本在上游读取，未按样本线性推算全库。Spec第191行关键列表性能基线尚未满足，连接复用不足以据此关闭差项。

待答复的具体选择：**发件人／主题搜索＋详情按需读正文**，或**保留全文并改后台检索／进度**。两者均涉及产品行为；确认前不删全文、不建长期原文／索引副本，也不把缩小功能范围当成原目标完成。

- [x] 最终结构／字段方向、严格同输入容量、真实CLI及迁后新事实inverse／reforward。
- [x] 已完成来源核验及限定20原文退休决定；Email未核实来源保留，退休发布工具有副本证明。
- [x] 上述当前回归及关键独立只读审查；后续增量按实际影响补验，不重跑无变化部分。
- [x] 本轮发现的当前消费者证据缺口已闭合：TaskAgent247节点、Sync派发19、Project重放28、发送回执历史27；旧广泛诊断仍保留原范围，不宣称整仓通过。
- [ ] 搜索产品方式及性能／页面行为验证；模型历史权重保留偏差已修复并验证，生产清理及只留当前／最新的实际读回待发布。
- [ ] 最终来源／候选绑定刷新、推送与官方`python -m app.deploy`；执行前完整备份，发布失败使用schema inverse保存迁后新事实。
- [ ] 生产PID／healthz／queues／Attention／History／最终schema及容量读回，核对无新增失败／卡住／重复发送后才报告生效。
