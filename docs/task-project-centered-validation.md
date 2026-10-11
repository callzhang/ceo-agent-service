# Project-centered work validation

## 输入缺口与部署协调核对：2026-10-07 01:33 PDT

只读调用当前 `_document_projection` 做最小对照：同一 4357 字符历史正文，中间
项目定义位于 1405；没有既有引文时只交付 [0,1024)、[3333,4357)，定义不可见；
给定同一原句引用后，1405–1452 原文区间被保留。没有改变预算或实现。
`render_task_semantic_context` 的引用来自已有 context、Task suggestion/owner proof
和 Attention assessment；单纯正式登记/项目证据关联不会自动提供对应正文中段的
引文。因此问题不是共享存储丢失正文，而是历史正文交付策略依赖已提取引用，未必
能支持尚无 context 的项目首次理解。当前原文仍完整交付，此结论不能扩展为当前
原文也被截断或模型必然漏判。修复方向仍待批准，不继续叠加第四轮提示词。

主 Agent 对部署阻断的协调核对已结束：正式模板 publication
`deploy-35173cc3-9190-4923-b038-5add1b2bee73` 回执 verified，当前模板与旧版备份
hash 对应；它建议将可变运行时模板移出 Git 跟踪、继续跟踪 `app/defaults/`，并通过
限定正式迁移保留已安装内容/备份/回执。此处是对方核对结果与待批准方案，不是
本 Agent 已实施变更；没有放行一般脏文件、重置模板或修改审核规则。

## 页面修复发布检查点：2026-10-07 01:30 PDT

仅 CSS/回归测试修复 `318ef1d6` 与文档 `8dd41d69` 已推送 origin/main；本轮重新
运行四个前端相关文件 48 passed、TypeScript/Vite build 成功、diff check 通过。
Task Agent/检索/Skill 与其架构说明候选仍未提交，未混入这次推送。

标准 `python -m app.deploy` 终态退出 1：生产 checkout 的受管跟踪文件
`data/prompts/developer_prompt.md` 存在本地变化，因此 **nothing was deployed**。
该已安装文件 SHA 与 `ci/prompt-template-release.json` 的新 Developer SHA 完全一致，
即 PR #19 的正式模板；没有覆盖、还原它或手动重启。已通知主 Agent 核对该受管
配置的正式处理路径。不能把已推送代码或生产数据开发预览当成已上线。

Quality run `37593760021` 已实际启动；本检查点仍运行 npm test，尚无通过结论。
生产真实关注详情 `/tasks/attention/1` 的暗色 390×844 检查：document width=390，
视觉可读；来源事实、Agent 判断、关联任务 0 分开展示。不是全部详情/主题矩阵的
完成证据，列表长登记依据的修复仍需部署后实际验证。

## 当前结论：部分符合，整体业务验收未通过（2026-10-07 01:24 PDT）

本节是当前状态；下方保留的历史检查点不表示今天的全部验收已完成。
线上已不是“风险/需关注均为 0”，但不能以两张卡片作为完整设计达标的证明。
本轮只读 API 核对生产 `7e0e9654`、PID `10931`、healthz ok；业务 Project 登记
32 条，列表中有目标/上下文的项目 4 个，active 需关注 2 张，全部 Task 431 条。
建议 0 是此前已核验值，本轮没有重新读取建议表，不将其写成新快照。
状态 API 快照 `2026-10-07T08:24:07.893432Z`：运行层 pending=3、processing=0、
failed=8、retryable=0、Attention=5。运行层 Attention 与业务需关注不是同一计数。

| 设计预期 | 实际证据 | 验收结论 |
| --- | --- | --- |
| 信息先汇入 Project，再区分真实安排、人员职责和未指派建议 | 真实 Project47 页面分开展示项目事实、总负责/分工、来源任务、Agent 建议；未知总负责保留待明确 | 结构符合；真实明确总负责与非空建议样本尚未验收 |
| 没有 Task 的项目仍可因经营风险进入需关注 | 线上 2 张业务关注；Project47 页面存在关注原因且来源任务为 0 | 零 Task 可关注成立；不证明风险发现完整 |
| 新资料更新项目事实/分工，保留有效旧资料 | 冻结真实会议 baseline 没有 Project 更新；v1 能更新 Project47，v2 又漏掉本次有证据的负责事项 | 未通过；开发候选未提交/部署 |
| 综合当前信息及相关项目定义，覆盖重要项目/风险线索 | v2 补齐正式登记后仍缺项目 context、漏重要线索；历史定义正文中段未交付 | 未通过；具体输入缺口已查实，不能把所有漏判归因于同一个缺口 |
| 引文既要逐字准确，也要属于正确项目/事项 | 逐字检查通过，但一条关注判断借用了前一个项目讨论的原句 | 未通过；语义归属仍须独立原文检查 |
| 日期类型和值明确，不把项目目标误作任务 DDL | v1 日期文字有值但类型/字段为空；v2 的该事实月份字段正确 | 局部改善；整体业务比较仍失败 |
| 暗色和窄屏列表完整可读 | 生产 390px 列表被长登记依据撑到 820px；开发修复后明/暗、1440/390px 均无整页横向溢出 | 局部修复已验证，未上线；其他页面完整矩阵仍待核对 |

剩余工作不能缩为“让风险数量非零”：需要补齐真实原文的项目定义交付，固定条件
比较项目/分工/风险覆盖与引用归属，再核验真实负责人、建议、CRM 和记忆的实际
结果。输入组织方案仍待确认，不把建议方向写成已批准设计；不直接 SQL 补卡或
强造项目、负责人、任务和风险。保留计划中尚未通过的 native/业务/UI 验收勾选。

### 窄屏列表真实检查与修复（开发验证，未上线）

生产过滤列表的 Project47 登记依据为完整 144 字符。明/暗模式下 390×844 viewport
对应 document width 820px；直接子 span 右边界约 819.5px，`overflow-wrap: normal`。
这是长来源标识与 flex 最小宽度的问题，不是暗色字体颜色本身消失。

仅在 `.business-task-meta` 及直接子元素设置可收缩最小宽度，子元素
`max-width: 100%; overflow-wrap: anywhere`，保留全文，不隐藏溢出、裁字或加省略号。
本地 Vite 预览通过只读代理读取同一生产数据：明/暗 1440×900 时整页宽度 1440，
明/暗 390×844 时整页宽度 390；完整 144 字符登记依据仍在页面，窄屏右边界 363px。
Project47 详情页另有一次暗色 390px 宽度读回，document width 为 390；这不是全部
详情页/视图的四模式验收。

回归先 RED（2 failed/10 passed），修复后四个前端相关文件共 48 passed；
`tsc --noEmit && vite build` 成功。需求与质量独立审查均 PASS；质量审查独立运行
CSS 测试 12 passed。JSDOM 测试证明实际 CSS 的交付/计算样式，真实浏览器证明
布局宽度，二者不相互替代。没有新依赖、业务数据/标签/主题或运行政策变化。
临时预览进程已退出，浏览器 viewport/media override 已恢复、临时 tab 已关闭。
此修复未部署，不将开发预览结果报告为生产页面已修复。

## 本轮交接与线上核对：2026-10-07 01:01 PDT

验证记录已提交 `0eedfc05`；推送时主线已有并行 PR #19，已通过普通 merge 保留全部
上游提交，并以 `d5dc0de1` 推送本轮文档。相对上游只发布三个文档文件；Task Agent、
检索及 Skill 候选仍为未提交开发改动，没有随该推送上线。合入上游后四个相关文件
`test_task_agent`、`test_work_tracking_skill`、`test_task_retrieval`、`test_business_skills`
323 passed，diff check 通过；这不替代冻结旧代码 native 结果或新基线业务验收。

本轮只读核验生产 checkout 已由并行工作推进到 `7e0e9654`，PID `10931`、healthz ok。
status 时间 `2026-10-07T08:01:35.721643Z`：pending=3、processing=0、failed=8、
retryable=0、运行层 Attention=5。业务计数仍为 32 条登记、4 个带 context 的 Project、
2 张 active 关注、0 条建议。不是本候选部署，不将别的发布认作本功能收尾。

## ProjectContext 刷新候选：2026-10-07（开发验证，未发布）

针对下述真实会议副本的上下文漏更新，当前候选仅修改 Task Agent 的上下文指导、
既有证据纠正提示及对应 Skill v6：完整快照保留已核实定义/旧引用，再合入有依据的新
事实/职责；不要求新来源重证整个项目，不以 Task/Attention 无变化省略上下文更新。
未修改检索、模型、schema、校验、工具权限或纠正预算；没有新 Agent/恢复循环。

新增指导和既有纠正提示回归先 5 failed，补文字后 5 passed。主 Agent 本轮五个
相关文件 `test_task_agent`、`test_work_tracking_skill`、`test_task_agent_runtime_repair`、
`test_task_source_documents`、`test_task_project_centered_eval` 共 315 passed；Ruff 和
diff check 通过。独立需求/质量审查通过。上述测试保护指导交付，不证明原生业务修复。

新配对实验已冻结 input `27497`、已验证部署副本初态、原文 payload hash、相同路由/
模型配置、900/300 秒预算及并发 1，独立原文 oracle 不进入 Agent prompt。baseline
采用 `f20d0cf1` archive，candidate 仅替换同 archive 的 Task Agent/Skill；两侧实际
模块 hash 分别记录，不以 archive 的 Git SHA 冒称候选已经提交。新 baseline native
已完成，保存 9 个项目/线索判断、0 个 Project 决定、0 个 Task 决定，context revision
仍为 4，再次复现上下文刷新失败。候选也已 completed，实际为 1 个 Project 决定、
7 个判断、0 个 Task 决定；项目 #47 新 revision 保留全部有效旧事实/职责与 Signal637，
合入当前 Signal638 的事实和职责，未知总负责及原 Task 身份/承诺均未改写。两边实际
route/model 都是 `codex_oauth / gpt-5.6-luna`。独立原文审查确认该核心刷新案例改善，
但**完整业务 oracle 仍失败**：正式项目 #25 漏判为身份不明，车铃线索较 baseline
遗漏，哈罗及有明确归因的奥迪风险线索也未完整覆盖。不执行成功样本的重复验收、不
宣称整轮修复/比较通过，不重跑或修改生产。

进一步核对两边实际 native 输入：正式项目清单同为 22 条、缺少 #25；但选中的活动
project anchor 列表包含 #25，实际库中也有同一 canonical_anchor_id 的正式登记与
原始定义。候选“不存在正式上下文”的解释符合它所收到的缺失资料，不应要求模型
从裸 anchor 猜正式身份。下一步只验证该交付关系的一致性：选中已有正式 Project 的
活动 anchor 时，也交付其精确登记资料；不改名称排序、不按相似名称关联身份。

另核对已有日期契约：新事实在文字中保留了月份目标，但 `date_type/date_value` 均为空，
不符合“有日期的 Project fact 须有明确类型和值”的原设计。此项独立记录为未通过，
不回改已冻结 oracle 的含义判定；目标月份不能变成 Task DDL，也不能补造年份/日期。

登记资料交付的窄修复先复现缺失登记行；不加条件的初稿同时暴露了两个原有回归：
被选中的零分填充 anchor 带入无关项目/关注，可能挤掉原相关卡片。保留原 fixture 和
断言，只对**已选中、活动、Project 类型且按现有 query/title 分数为正**的 anchor 补
精确登记行。原 Project/Task/anchor 排序和数量限制不变，不将名字匹配当身份依据。
该文件 41 passed；主 Agent 六个相关文件共 356 passed，Ruff/diff check 和独立需求/
质量审查通过。第二次 native 已完成：Skill v6 单独候选 → 同 v6 加资料交付修复，
同初态/来源/实际 `codex_oauth / gpt-5.6-luna`、900/300 秒预算及并发 1。实际送达
正式登记 22→27，包含 #25；共享后可见原文字数仍为 100,895，整次 prompt 为
424,041 字符。这些是实际交付指标，不证明 Agent 读取了全部信息。

独立原文审查的最终结果仍为**完整业务验收失败**：奔驰身份/关注恢复，但仍无其
context；#47 的旧事实/分工保持、新事实月份类型和值正确，却漏掉当前有来源支持的
负责事项；判断 7→5，遗漏美团/三星财险，车铃、哈罗和有归因的奥迪线索仍未覆盖。
货拉拉引用中的一段逐字原文属于前一个哈罗讨论；另一引用支持其自身不确定性，因此
未证明新造百分比结论，但引用主体归属仍有问题。逐字引文通过不等于语义归属正确。
四份 native 输出从首轮起就只有一个项目更新、两个旧分工及五个判断；未证明这些
遗漏由纠正过程删掉。原始失败、v1/v2 及未通过项均保留，候选未提交或部署。

进一步查看实际输入的历史 document69：4397 字符正文只送达 [0,1024) 和
[3373,4397)。正式项目定义和负责事项在原文偏移 1428、2609、3012，均处于未交付
中段；尾部虽出现项目名字，但不是该完整定义。因此正式登记行补齐并没有补齐原始
项目资料。此项是已核验的具体输入缺口，不证明全部遗漏都由它或长上下文造成。

下一步先确认输入资料组织方向，不继续叠加提示词或以两个风险卡片缩小验收目标。
建议保留一个 Task Agent/当前模型，验证当前原文、相关完整项目定义/事实与必要 Task
历史的交付方式；该方向尚未批准，不作为已实施设计或已经查实的全部漏评根因。

## 原始部署备份的来源共享迁移验证：2026-10-07

已找到真实迁移前备份
`/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3.pre-42c3afa4-20260930T200939Z`：
schema marker `2026-09-25.1`、原 Signal `evidence_text` 存在，尚无共享正文表。
仅在独立 clone 调用现有 `_migrate_business_task_source_documents`，不运行其他 Store
升级，不改原备份或生产。原 369 条 Signal 的全部字段经 JOIN 恢复后逐值一致（计数与
SHA-256 相同）；实际形成 78 份共享正文。全部 23 张原业务/历史表的行计数和原值
digest 均未变化，包括 230 Tasks、389 evidence/events、20 anchor links、10,653
原 Agent runs、9,040 inputs。原事件/run/input JSON 按字符串比较，未解析后重写。

Signal 自增高水位保持 369；完整外键检查前后均只有原始
`meeting_alignment_runs(2298) → meeting_alignment_jobs` 缺失关联，没有新增/改变。
再次调用同一迁移函数，物理 Signals、共享正文/ID、原历史表及外键集合均无变化。
独立只读审查重新核对原 Signal 与 clone JOIN digest、23 表计数和正文 digest，确认
该证据成立，原备份仍保持迁移前结构。

此项补齐**真实原始数据的 source-body-only 前后等价与重复幂等**证据，但不是精确的
W39 冻结初态，也不是全 Store 升级或旧外键修复。缺失的精确 W39 初态仍记录缺失，
不把较早备份或先前反向重建样本冒称为它。

## 最新设计符合度：2026-10-07 00:12 PDT（07:12 UTC）

**结论：部分符合，尚未达到完整设计预期；Task 8/9 不能整体关闭。** 本节为最新
验收结论，后续日期段落保留历史事实，不作为当前发布版本或业务验收状态。
本轮只读核验生产和已有冻结评测产物、更新文档；没有重跑生产、补卡、改数据或部署。

| 设计预期 | 本轮核验的实际结果 | 验收结论 |
| --- | --- | --- |
| Project 可独立于 Task 保存上下文、生成需关注 | 生产有 4 个带 context 的项目、2 张 active 业务关注；已保存周报样本没有 Task 决定，关注没有 Task 成员 | 已有正向样本，不代表全量覆盖 |
| 新会议事实和负责事项持续更新完整 ProjectContext | 同源初态的两份 native 副本结果都 completed、各保存 6 个判断，但 Project 决定均为 0；两份库仍仅 4 份 context revision，项目 #47 的 context 与冻结初态逐字相同 | **不符合；当前首要业务缺口** |
| 综合多来源判断，不仅依赖周报 | 会议副本已有具体项目风险判断；生产同一会议重跑仍只有会议主题线索，未写入具体项目更新；当前生产卡片不能代表最新会议情况 | 副本有进展，生产稳定覆盖未通过 |
| 无重复任务；职责和真实安排分开 | 两份会议副本 Task 决定均为 0，已有行动覆盖时不重复创建是合理的；但不能据此省略新项目事实/职责 | 去重合理，Project 更新失败独立存在 |
| 唯一总负责、职责推导建议、CRM 关联及持久 Memory 可实际使用 | 未知总负责保持未知；生产建议数仍为 0；总负责正向样本、建议正向展示、确认客户关联及 Memory 写入回执/召回尚未完整验收 | 待验收，不能从字段/代码存在推定通过 |
| 错误引文可观察并按原有预算纠正，原始出处不放宽 | `bdc52029` 修复已随 `1e3711a0` 发布；Quality `37581151917` 成功；两份 native 副本实际纠正后完成，原失败保留 | 技术路径已验证；不代替业务上下文完整性 |
| 真实页面与迁移/重复处理满足完整验收 | 已有部分真实页面深浅/宽窄读回；所有页面组合、真实来源重复域幂等、迁移前后等价及旧项目身份核对仍未全部通过 | 未完成 |

本轮生产快照：运行 SHA `1e3711a0`，PID `28560`，healthz ok；32 条项目登记、
4 个项目有新版 context（#47–50，各 1 个 revision）、2 条 active 业务 Attention、
431 条来源 Task、0 条 Agent 建议。28 条没有新版 context 的登记记录不是“已确认的
28 个真实项目”，仍需核对历史身份与来源。`27465` 和 `27497` 都是 done，但后者
的 done 不证明具体项目覆盖通过。

status 快照 `2026-10-07T07:12:33.192173Z`：13 个队列、pending=3、processing=0、
failed=10、retryable=0、运行层 Attention=6；这 6 条不是业务风险数，failed 是队列
聚合，不是 10 个唯一故障。本轮没有重新验证全部外部动作或全服务无故障。

冻结会议副本读回（input `27497`，两边 900/300 秒预算、并发 1）：baseline 关注总数
3、candidate 总数 4；两边 Project 决定 0、判断 6、Task 决定 0、context revisions 4。
产物的 `passed=true` 仅证明该 replay 的运行/已有检查通过，**不证明完整人工业务
oracle 通过**。增加一张卡片不是排序整体更优的证据；检索候选仍未提交/发布。

下一步按已确认设计修复上下文更新指导，并在同源冻结回归中验证：保留有效历史事实和
原始引用、合入新事实/负责事项；新总负责人、新项目定义或新 Task 都不是更新前提。
纠正引文不能靠删除有原文支持的项目覆盖来通过。先副本首轮/重复读回，再小批生产
重处理；不改模型、不降 oracle、不直接 SQL 补卡，不增加 Agent 或隐藏恢复循环。

## 发布读回与下一个业务回归：2026-10-06 23:27 PDT

确定性证据反馈修复为 `bdc52029`；合入并保留并行 PR #21 后主线为 `1e3711a0`。
合并后 340 项相关测试通过；标准 deploy 已完成 `6cf43b29`→`1e3711a0`，生产 PID
`28560`、healthz ok。Quality `37581151917` 后续读回 completed/success，覆盖生产
`1e3711a0`；代码发布/CI 通过不改变下述上下文业务回归仍失败的结论。

生产实际数据仍为 32 个登记项目、4 个有 context、2 张业务关注；本轮未重跑生产输入，
不把副本里的 3/4 张卡片搬入生产。运行层 Attention=6、History=33,639；status 读回
13 个队列，pending=3、processing=0、failed=10、retryable=0。3 条待处理为 Email
provider actions；Work items 没有 pending/processing/failed，最新查询没有 running
runtime attempt。部署备份与当前库 failed Reply Task IDs 相同（4→4），但仍存在其他
历史失败/权限拒绝；此处不宣称整个服务无故障。13 队列指标不是业务风险或唯一失败对象数。

检索候选在独立原文复核中没有证明整体更优：增加一个有依据的观察项，同时遗漏一个
待确认项目线索、保留了旧关注描述，两边都漏了重要新上下文。候选代码/回归补丁已保存
在私有评测资料中，并从当前开发树撤下；未提交、未发布、不进入生产重跑。失败和两次
配对结果均保留。下一步不继续追加名称排序规则。

下一个明确业务回归仍使用 input `27497` 的冻结原文及初态，强化独立 oracle：

1. 已知项目的新重要事实/负责事项必须更新完整 ProjectContext；目标/范围及有效历史
   事实和职责保留原证据，新信息使用当前原文；新整体负责人或新项目定义不是更新前提。
2. 对已登记的一个核心项目，最新 context 必须同时含原 Signal `637` 和当前 `638` 的
   真实证明；原文中的阈值、目标及人员负责事项分别表达，不把目标当成停止条件。
3. 人名别名未核实及总体负责人未知保留未知；已有行动抑制重复 Task/建议，不能据此
   省略 Project 更新。纠正证据时不通过删除有原文支持的上下文/项目覆盖来逃避校验。
4. 首轮、重复域快照、完整原文复核分别验收。`completed`、回执通过和新增卡片不代替
   这一业务回归，也不代替原计划的多来源/迁移/真实页面/Memory/客户关联等剩余门禁。

CRM 已经用已授权 CLI 完成真实正向查询与稳定 ID 读回，名称与对应 AccountObj.name
一致；客户记录未修改，尚未关联任何项目。具体客户关联已请求用户确认，未把 resolver
单一候选当成人工确认；客户原始值仅留私有工作资料。

## 固定真实来源对照与继续修复：2026-10-06

本轮完成了新的只读副本对照，不把本机回归通过当成业务效果：

- baseline 为 `6ba1e25c` 的运行代码；candidate 为同一代码加未提交的项目检索 v2。
  两边初态都来自已验证完成的部署备份，source payload SHA-256 为
  `9f41f54c28f29d92c417ebee8b11225f3209864d3a7d6ebef793d7f62dccfebf`。
  两边均为 `codex_oauth / gpt-5.6-luna`、900/300 秒预算、并发 1、相同 Skill hash；
  预期只在独立人工 oracle，不进入 Agent 输入。副本评测明确提示外部只读、不写 Memory。
- baseline 首次 native 输出有 5 个具体项目判断，但有多处非连续原文引文；原有两轮
  修正后仍失败，最终为 `TaskDecisionRepairExhausted`。检查原始来源及嵌套 JSON 后，
  坏引文仍不匹配，不是解码遗漏导致的误拒绝；例如模型自行添加了原文没有的格式标记。
- candidate 检索确实包含此前漏掉的已登记短名称项目，但 native 只输出 1 个项目决定/
  1 个判断、0 个 Task 决定，仍遗漏人工预期中的另一个具体风险项目。它声明复用旧卡时
  未引用该卡保存的原证据，在原子应用阶段失败。两边 Task/Project/Attention/事件均无变化。
- 因此检索候选没有取得业务优越性证据，未提交或发布，不由 39 项回归及两阶段独立审查
  代替固定评测/PR。v1 的短 Latin 子串误优先已用现有分词完整 token 条件修正；不添加
  业务关键词或按名称确认身份，预算及同来源身份保留不变。
- 已实现另一个确定性修复：将原有 stored Project/Attention 校验接入已有两轮纠正，
  原子事务仍复查相同规则；数据库操作错误和原子应用错误不转成模型反馈。先复现
  2 个失败，再通过 258 项 Agent/runtime 测试及独立需求/质量审查；本 agent 另跑
  5 个相关文件 415 passed。此处仍为开发验证，不称为已部署或真实覆盖通过。
- 已修复的反馈缺口：同一输出多处当前引文错误原先只反馈第一处；现在一次报告所有
  不同的被拒引文、来源和原始错误。先复现 2 项失败；新增成功/耗尽/合法重复引文回归
  通过，Agent/runtime 共 261 passed、Source Documents 19 passed，并通过独立需求复查。
  两轮预算和逐字原文标准不变，不自动补造引文。组合修复的 6 个相关文件 437 passed、
  Ruff/diff check 和独立质量/文档复查通过；本次提交包含该修复，生产发布仍待完成。

两边共同加入相同纠错修复后的 v3 对照已完成：同一来源/初态/模型/预算/并发，两个
native 过程都实际因当前引文错误进入一次反馈后 completed；连续引文标准未放宽。
baseline 保存 6 个判断、2 个风险判断应用，关注 2→3（新建 1、更新 1）；candidate
保存 6 个判断、3 个风险判断，关注 2→4（新建 2、旧卡未更新）。两边都覆盖冻结 oracle
中的两个风险事项，但都有 0 个 Project 决定/完整 context 更新、0 个 Task 变化。
独立原文审查确认：这轮已有源任务覆盖行动，因此不新增任务/建议合理；但原文有重要
的新条件、目标和人员负责事项，而旧 context 仍只保留旧周期事实，违反完整上下文更新
设计。candidate 新增一个来源支持的观察项，同时漏掉 baseline 记录的一个待确认线索，
且旧关注卡没有反映最新约束；一个配对样本未证明排序算法整体更优，不合并该候选。
下一步针对上下文更新指导及纠错时保持原文支持的业务覆盖，不继续添加名称排序规则。

真实生产浏览器已查看需关注列表、零 Task 关注详情、项目列表及一个有 context 的项目
详情。确认事实/判断分区、未知总负责人、人员分工、明确日期类型、建议与真实任务分区；
观察到浅色宽屏和暗色窄屏文字可读，窄屏文档宽度=视口宽度 390，无整页横向溢出。
这些是局部真实 UI 验证，不是所有页面/四种视图组合或建议正向样本验收。默认项目首屏
主要是旧记录，实际有新上下文的条目需搜索找到；旧记录核对/补上下文仍待完成。
来源事实中的来源时间仍显示“未提供”，不将处理时间替代原始时间。

`f4a889df` Quality run `37575673234` 已 completed/success；它覆盖已部署的引文纠错，
不覆盖上述尚未提交的新修复。临时在线备份未完成即终止，未参与评测，其未验证副本已
可恢复地移入废纸篓；评测仍使用完成标记已核验的部署备份。完整目标保持 active。

## 全部验收目标继续执行：2026-10-06 22:34（America/Los_Angeles）

用户目标是“继续按设计完成全部验证和达到效果，并自检”；当前仍为 active，未将
非零关注、已发布修复或 completed 输入作为全部验收完成。

项目引文纠正修复已集成为 `f4a889df` 并通过标准 deploy 发布，PID `73148`、healthz
读回正常。当前主线新增回归先复现 4 个 ValueError 失败；纳入修复后五个相关文件
403 passed，独立需求审查 11 项、质量审查 15 项通过。复用原提交的作者和实现，
保留原始来源/连续引文标准、历史来源校验及两轮纠正预算，没有新增恢复循环。
Quality run `37575673234` 后续已 completed/success；见顶部新增检查点。

真实失败输入 `27497` 重跑为 run `10692`，状态 completed / input done，但输出为
0 个 Project 决定、0 个 Task 决定、1 个仅针对会议标题的 insufficient_evidence 判断。
没有更新正文里的具体项目上下文或生成新风险卡片。因此本轮业务覆盖不通过；
也未实际触发坏引文→纠正→成功的原生路径，不能用 completed 证明该正向业务验收。
生产仍为 32 个 Project、4 个有 context、2 张关注卡片。

输入诊断：当前正文 27,353 字符，实际投影标记未截断，范围 [0, 27353)；
检索共享后的可见正文共 100,895 字符。当前检索交付 22 个项目登记身份，
其中缺少该来源明确提及、数据库中已登记的一个短名称项目。此证据支持继续检查
项目检索排序与模型覆盖，不能把漏评只归因于输入没送达或只归因于模型。
该诊断不证明 Agent 已读取所有可见正文，也不宣称全部漏评根因已解决。

接下来保持同一来源/模型/路由冻结，先验证相关项目检索，再比较真实来源逐项目结果。
原生 synthetic/copy 评测将明确传递只读外部工具/不写 Memory 的 prompt 指引，
避免把评测资料当作真实业务记忆；该指引不是工具权限强制隔离。

本次只读刷新：生产 SHA 仍为 `f4a889df`，healthz 为 ok；业务 Attention API total=2，
Project API total=32。运行层 Attention total=6，History total=33,627，与业务风险
数量分开计数。work-summary 队列为 done=4,662、skipped=4,402，没有 pending、
processing 或 failed 输入；没有 running/pending runtime attempt。这些是本次快照，
不是对所有队列、外部动作或业务覆盖的全量健康证明。CI 后续成功见顶部新增检查点。

检索排序候选修复目前仅存在于开发工作树，未提交、未发布：把当前来源中明确出现的
已有项目名称排在偶然字符重合之前，保留同来源优先和原预算；不据名称建立或合并身份。
开发者报告窄回归先失败后通过、该文件 38 项通过，但独立审查和同输入 native 对照
尚未完成，不能写作“近期项目遗漏已修复”。真实来源的 same-source 项目数为 0；
交付的 22 个登记身份不等于 22 个同来源项目，不能据此误诊为同来源超额耗尽预算。

## 历史设计符合度：2026-10-06（America/Los_Angeles）

**结论：部分符合；代码已发布，业务验收尚未完成。** 非零关注数量证明样本路径
能够工作，不证明全部项目风险已经识别。此处保留当时状态，当前结论以顶部最新章节为准；下文保留的各日期段落
是当时的实验/发布记录，其中“未部署”“禁止 Memory 写入”等表述不再代表当前契约。
不回写旧实验结果，也不以生产新结果替换隔离副本的结果。

### 预期与实际

| 已确认的设计预期 | 当前证据与实际结果 | 判断 |
| --- | --- | --- |
| ProjectContext 独立更新，不需要 Task 作为载体 | 生产输入 `27465`、run `10690` 保存 4 份 ProjectContext；输出 4 个项目决定/判断、0 个 Task 决定 | 样本符合 |
| 项目没有 Task 也可根据业务风险生成需关注 | 同一轮 2 个 `needs_attention`、2 个 `not_needed`；2 张 active 卡片均没有 Task 成员，并有原始证据 | 样本符合 |
| 正常推进不因缺 Task 自动变成风险或证据不足 | 同一轮正常项目得到 2 个 `not_needed` | 样本符合 |
| 项目列表展示当前情况与关注原因 | 上一轮生产 API 读回 32 个 Project、2 条业务关注；2 个对应 Project 的 `attention_reason` 非空。本次只读数据库核验数量未变 | API 已验证；不等于本次真实浏览器验收 |
| 一个项目最多一个总负责人，其他人员按不同职责记录；未知不能猜 | 4 份新 context 的 overall_owner 都为空；其中 2 个项目分别保存 2/1 条职责。缺少总体负责人证据时保留未知合理，但没有唯一总负责的生产正向样本 | 部分验证 |
| 建议 Task 先展示，不能当作已指派/已接受 | 本轮没有创建 Task，生产 `origin=agent_suggestion` 计数为 0；关注与建议可独立存在 | 本轮未验证建议的实际展示和职责推导 |
| 综合会议、周报、聊天等来源判断；周报不是唯一来源 | 周报输入 `27465` 成功；较新会议 `27497` 的 run `10691` 引文失败，后续 `10692` 虽 completed，但只判断会议主题，0 个 Project/Task 决定，未覆盖正文具体项目 | 不符合稳定多来源覆盖预期 |
| 明确项目身份、保留无法确认的线索，不用相似名称猜关联 | 不确定身份未强行登记；最近重跑只保留一个会议主题线索。完整正文已交付，但相关已登记短名称项目被检索遗漏，且模型没有逐项目判断 | 身份约束保留；覆盖不通过，根因仍需固定对照 |
| 项目事实和判断可回到原始来源，错误可观察并反馈修正 | `f4a889df` 已将当前 Project 引文错误接入原有两轮纠正；先复现 4 个失败，再通过 403 项相关测试及独立审查。历史失败保留，连续原文约束未放宽；真实重跑未输出该类引用，未触发纠正正向路径 | 代码修复已发布；真实纠正成功路径待验收 |
| 持久的重要项目风险/进展可写入 Memory | `a385b86d` 已修改 prompt/Skill；实际安装 Skill 已同步。W39 原生样本未见 Memory 写调用/回执，也未做成功写入后的召回验证 | 规则已发布；实际写入效果未验收 |
| 客户可空，关联使用 CRM 稳定实体身份 | 客户关联实现已发布；本轮没有 CRM 写入，也没有新增 confirmed customer-link 正向读回 | 保留此前边界，不能宣称正向业务验收 |

### 当前数量与证据边界

生产只读数据库本次读回：32 个 Project、4 个有新版 context 的 Project、2 条 active
业务 Attention（category 均为 `decision`）。因此还有 28 个 Project 未形成新版上下文。
32 是当前库中的项目登记记录数，不代表这 32 条历史记录都已重新核对为真实业务项目；
旧记录的目标、范围、身份定义及可能的别名/重复仍需逐项核对，不批量确认或猜测合并。
这不是已知的“全部项目风险数”，也不能用历史 legacy risk 标签直接填充新关注。
两张卡片来自统计周期为 2026-09-21 至 2026-09-25 的周报；较新的 2026-10-05
会议更新尚未写入，不能把卡片的处理时间当成业务风险发生时间或最新确认时间。

本次运行层 Attention 是 6、History 是 33,627；它们不是业务风险数量。较早 status
聚合端点曾在 25 秒内超时，本次未重新核验该端点或全部队列。work-summary 当前
没有 failed/pending/processing，runtime attempt 没有 running/pending；不据此宣称
全部服务状态健康。此前失败的原始 run 保留，即使输入重试后已变为 done。

### 发布与重跑记录

- PR #16/#17、Memory 规则 `a385b86d` 及当前引文纠错 `f4a889df` 已通过标准 deploy
  发布。本次核验生产 SHA 为 `f4a889df`、healthz 正常；最近部署 PID 为 `73148`。
  主线 `c11966cb` 只改副本评测指引及验证记录，不代表生产已更新到该提交。
- 234 项 Task Agent/Skill 定向测试、Ruff、diff check 通过。Quality run `37560722735`
  本次核验为 completed/success，取代上一轮“CI 仍在运行”的状态。
- 部署后发现真实安装的 `ceo-work-tracking` 仍为旧版 Task-first；已使用现有安装函数
  同步当前 Project-centered Skill，并核对安装内容与候选文件一致。初始 0 context /
  0 project_assessment 是数据尚未按新契约处理的证据；不能只凭 Skill 版本差异断言
  它是所有漏判的唯一原因。
- 重跑沿用原 input/source 及正常队列处理，不直接 SQL 创建关注卡片。部署前备份
  已核验完成。手动启动在没有正确生产环境/MCP manifest 的两次尝试（runs `10688` /
  `10689`）失败于 command build；这些操作诊断失败不是项目没有风险，历史记录保留。
- 输入 `27465` 的成功 run `10690` 实际保存 4 个 context 和 2 张关注卡片。它与此前
  W39 隔离副本中 5 个 assessment / 3 张已有 Attention 的结果不是同一数据库初态，
  不能直接用 2/3 卡片数量作算法优劣比较或替代固定 oracle。
- 输入 `27497` 的 run `10691` 失败；schema correction 已执行，但领域引用验证拒绝了
  两段不连续原文被拼成单一引文的结果。没有把这一轮模型判断当成已保存项目事实。
- `f4a889df` 部署后同一输入的 run `10692` 为 completed，但没有形成具体项目决定、
  上下文更新或新卡片；运行状态成功与业务覆盖失败同时成立。

### 尚未完成与下一次验收

1. 引文错误接入既有 bounded decision repair 的代码工作已发布。继续验收真实原生
   先拒绝拼接引文、纠正后提交，以及重复重跑不新增重复卡片；不以单测代替实际路径。
2. 对近期来源中的项目线索逐项核对真实目标、范围与原始身份依据，区分正确关联、
   新正式定义和仍不确定。关联改进需冻结同一输入及期待结果，不能用标题前缀或
   相似性直接合并，也不把客户名自动升级为 Project。
3. 对明确列出的近期会议、报告和补充消息分批补上下文；分别报告已覆盖来源/项目、
   未覆盖范围、失败和关注原因，不用非零关注数充当全量覆盖证明。
4. 完成有总负责/分工的生产正向样本、建议 Task 的标签与状态读回、成功 Memory
   write 的回执与召回、真实浏览器页面和 confirmed CRM 客户关联的验收。

文档更新本身不发布检索候选、不补造业务结果，也不将未完成项标记为通过。

## 2026-10-07 production readback and Project-list latency repair

PR #16 (merged as `020ae847`) passed full GitHub Quality run `37550292249`;
PR #17 (merged as `9776ae2f`) passed full run `37553640789`. Both runs passed
`npm test`, workbench build, and Playwright browser tests. Standard deploys
completed from `fe4d2c39` to `020ae847`, then `020ae847` to `9776ae2f`. Final
launchd readback reports running PID `98190`; `/healthz` returns
`{ok: true, status: "ok"}`.

After PR #16, the Project list endpoint timed out at 20 seconds. Production
read-only DB counts were 28 Projects and 431 non-merged Tasks. Code inspection
found that each Project summary walked all Tasks and queried each Task's
Project links again. PR #17 batches the active Task→Project membership query.
After the second deploy, the same Project list endpoint returned all 28
Projects in **0.191 seconds**, with 0 proposed Project candidates; Project list
and detail summary semantics are covered by the new regression test.

## 2026-10-10 Project-list context/activity batching

Expected: a read-only Project list should return within normal console latency
while preserving the same saved context, latest revision/evidence timestamps,
active Attention reasons, Task counts, filters and customer grouping as detail
reads. Actual before this change: on the deployed service `/healthz` returned
200 in 0.802s and Project 37 detail returned 200 in 16.065s, but the same
Project-list query produced no response bytes within 25s. This was observed
latency, not a proven production exception. Source inspection found a second
N+1 path after the Task→Project membership batching: each Project summary opened
one connection for current context and another for revision, evidence and
Attention metadata.

The isolated worktree based on `origin/main` now batch-loads current Project
contexts and activity projection through one SQLite connection and supplies
those values to the existing summary DTO builder. The first regression failed
before the implementation exactly at the per-Project `get_business_project_context`
call. After the change, `tests/test_web_api_task_project_summary.py` reports
**11 passed**; its regression checks bounded connections for five Projects and
asserts the list summary equals each Project detail summary. Ruff and
`git diff --check` pass. No Project, Task, Attention or CRM data was written.

The fix was committed as `d839fb54` and pushed to `origin/main`. During deploy,
the first deploy command was interrupted while waiting for health. A separate
concurrent deploy from another worktree subsequently advanced production to
`dfa7bbe6`, which contains `d839fb54` as an ancestor alongside other changes;
that deployment is not attributed solely to this Project-list fix. Final
readback observed launchd PID `71140`, `/healthz` HTTP 200 in 0.104s, and the
production Project-list GET returned HTTP 200 in 1.597s (15,384 bytes): 20 of
32 Projects on page 1 and 0 Project candidates. The same production Attention
GET returned 7 items (4 decision, 3 watch). This is a successful production
readback for the list and Attention endpoints, not a broad Tasks acceptance.

The production status API returned after about 58s: 13 queues, 10 pending,
2 processing, 12 failed, 1 retryable and 24 general Attention items. These are
observed runtime states, not evidence that this change created or resolved any
queue item. The History list did not return within 120s, and the failed-only
History query did not return within 60s; neither is counted as a successful
History readback. The overall post-deploy inspection therefore remains partial,
and History latency is an outstanding service issue. No production Task,
Project, Attention or CRM data was written by this change.

Final runtime readback: the business Task Attention route has 0 active items;
the general Attention page has 5. History reports 33,611 records. The runtime
status endpoint returned in 4.977 seconds with 13 queues, 3 pending, 0
processing, 8 failed, 0 retryable, and 5 Attention items. The 3 pending records
are Email provider actions; scheduled dispatch reports 2 running leases. These
are observed existing queue states, not evidence of Task/Project writes by this
feature. No CRM writes were performed.

## 2026-10-06 latest prompt, fixed-eval and W39 readback

Derek confirmed the Task Agent restriction must remain prompt-only and limited
to `memory_connector.memory_write`; there is no general read-only instruction,
tool allowlist or CLI/MCP interception, and `document_upload` is not prohibited.
The candidate prompt and shared Skill also state exclusive Project selector
shapes, require every Project assessment to cite current null-ID evidence, and prefer one
display-only Task suggestion for one material Project risk unless the source
contains multiple distinct independently completable actions. This is intended
to avoid expanding each Project responsibility into separate inferred TODOs.

Task Agent and Skill tests passed **234 tests**; the focused fixed-eval contract
tests passed **21 tests**; Ruff and `git diff --check` passed. The latest
19-case serial v4 native run under `/private/tmp/project-centered-v4-final-rerun2-20261006`
passed 18/19. Its only failing assertion was the promoted same-ID Task title:
the saved Task was formal, owned by the named human, `assigned_unaccepted`, and
linked to the same Project, but said “付款排期” where the fixture allowed only
“付款时间” or “付款安排”. The source itself uses “付款排期”. Adding that
source-supported synonym to the fixture expectation and reading the saved
database back against the updated frozen oracle passed with no remaining
failures. The previously failing `same-project-three-sources`,
`peer-not-auto-member`, and `completed-task-risk-persists` cases also passed on
fresh isolated reruns; the latter preserved the completed acceptance Task and
created a separate payment-risk candidate. Native outputs are model-variable,
so the one-batch score and reruns are both retained rather than described as a
single 19/19 batch.

The W39 input `work_summary_inputs.id=27465` was replayed on a fresh SQLite
backup at `/private/tmp/project-centered-w39-final-replay-20261006.sqlite3`,
not on the retained source or production DB. It completed after one superseded
runtime attempt and one successful `codex_oauth` / `gpt-5.6-luna` attempt. The
persisted output contains five Project assessments (three `needs_attention`,
two `not_needed`), all five cited by `project_decision_index`; it updated three
existing Projects and evaluated two exact source-defined report registry rows.
One row reused an existing Project identity; the previously unresolved
`Einride POC` row created one new Project using an exact current registry-row
excerpt. Project count changed 20→21; the existing Task count
remained 259 with no changed/created Tasks; Attention remained three cards with
no changes; evidence validation passed and no duplicate card was created. This
is a bounded real-source replay on a copy, not a production mutation or a proof
of exhaustive cross-source project coverage. CRM lookup remains read-only;
there is still no positive confirmed CRM-customer link readback.

The implementation is on PR #16 and is not deployed. Its first GitHub full-suite
run completed with 20 failures among 10,696 passed tests. The failures exposed
stale fixtures/assertions after the required Project decision/assessment fields,
strict Task Agent output schema, and project-centred Skill contract changed. The
affected test expectations and inputs were corrected without weakening the
runtime contract. Verification now passes across all seven affected test files
(410 tests), plus the focused Task Agent/eval/daily-report/message-ledger set
(274 tests); Ruff and `git diff --check` pass. A new PR CI run is still required
for the updated commit. Remaining gates are rerun CI, final review, merge,
deployment, and post-deploy process/health/queue/Attention/History readback. See
later dated sections for historical experiments; where those sections say a
native or W39 run is pending, they describe an earlier checkpoint and are
superseded by this section.

## 2026-10-06 CRM customer-association continuation (unreleased)

The isolated implementation branch now contains Project CRM columns/migration,
source-evidence validation and Task Agent lookup wiring, local explicit
confirm/clear APIs, Task customer projection through confirmed Projects, customer
grouping, and Project detail/list UI. These changes are not committed, pushed,
deployed, or live. Focused backend CRM/API/store tests pass (23); the broader
Task Agent/model/Skill/API set passes **375 tests**. Frontend API/page tests pass
(**69**) and the production frontend build succeeds. Ruff and `git diff --check`
pass. Task Agent Memory write restriction is prompt-only and names only
`memory_connector.memory_write`; no tool allowlist, CLI/MCP interception, or
`document_upload` restriction was added.

The actual production `sharecrm` session currently reports `tokenStatus=normal`.
Live AccountObj describe confirms the fields `_id`, `name`,
`field_customer_alias__c`, and `UDSText1__c`. A read-only SQL list query confirms
the CLI response uses `queryMeta.page` (including `returnedCount` and
`appliedLimit`), not the older documented flat metadata shape. A name-filtered
`query-by-sql` request is rejected by the installed CLI/service with a field
filter error. The implementation therefore uses the officially exposed
`query-by-name` resolver restricted to AccountObj. It returns structured
candidate IDs/names, but does not prove exhaustive exact uniqueness. All results,
including a single candidate from Task Agent evidence, remain unlinked until a
person confirms. The current live adapter smoke returns `NO_MATCH` for a
synthetic query; the resolved candidate envelope is covered with a fake-process
contract test. This validates the no-match path and parser shape, not a complete
positive customer-link readback. CRM release still requires a positive bounded
lookup/confirmation UI readback, push/deploy, and post-deploy service readback.

During CLI discovery, one `query-by-fields` read with an omitted search filter
returned the CLI's default first page of 20 CRM records. It was not persisted or
used; no further broad reads were made. Future discovery must supply a verified
restrictive filter before executing that command.

## Scope and completion level

2026-10-05 continuation: Derek confirmed the exact source Project title and the
migration rule to preserve the original foreign-key violation set without repair.
The fixed fixture is now version 4: source titles follow the source's full formal
name; source records carry the runtime's actual AI Minutes action-item or
authorized-assignment metadata; and report cases include an exact registry row.
Task Agent prompt and shared Skill now require a
complete ProjectContext snapshot on new facts, one assessment per relevant
Project (including each separate report row), exact current-source registration
quotes, and no Attention solely because an assignee has not accepted while work
is progressing. A material Project risk can be watched without a Task; a
display-only next-step suggestion is allowed only when saved Project
responsibilities support its concrete action and proposed owner. Focused verification
passed **742 tests**, Ruff and `git diff --check`; a native smoke passed the
role-based suggestion case. The full v2 native replay later scored **10/19**;
its original artifact is retained under
`/private/tmp/project-centered-native-v2-300e107e`. Review found invalid report
registry / meeting action metadata in two fixtures and real prompt gaps around
tasks inferred from roles or routine steps, missing-owner attention, and Chinese
role suffixes included in names. Version 3 corrects the source metadata and these
prompt rules. The v3 focused rerun of the nine previously failing cases completed
3/9; six still need correction or runtime diagnosis. The v4 oracle now distinguishes
missing overall owner alone (`not_needed`) from an explicit disputed transfer
(`needs_attention`), accepts a material-risk display-only next step when a sourced
responsibility fits, and keeps the existing-card membership assertion strict.
The full fixed v4 native comparison remains pending.

### 2026-10-05 native rerun on 89e401e0

The candidate completed the native runtime-route probe and began the fixed v4
suite on fresh temporary databases with `gpt-5.6-luna` via `codex_oauth`, a
loaded CI Skill, 900-second total timeout, 300-second idle timeout, and
concurrency 1. The first case completed but failed the frozen oracle: the Agent
invented a source Task to confirm a payment date despite no saved Project duty
or source action, then attached that Task to a zero-Task Attention case. This
was not a runtime availability or schema failure. The prompt and Skill were
clarified so Project risk may stand alone, and inferred Task suggestions require
a saved, sourced Project responsibility that supports both the action and its
proposed owner. A new clean-commit full 19-case run is required; no results from
the earlier d2450fbf run or this stopped partial replay count for that candidate.

### Initial full v4 native candidate (2026-10-05)

Candidate `75d7885022ac3a6c9f4cb76d751783726202e7dd`, route
`codex_oauth` / `gpt-5.6-luna`, effective timeout 900 seconds / idle 300 seconds,
concurrency 1, completed all 19 isolated native cases: 15 passed and 4 failed.
The behavioral failures were `unknown-overall-owner` (a responsibility-only
Project context generated two candidate Tasks) and
`responsibility-change-conflict` (the competing overall-owner candidates were
also stored as responsibilities, although `overall_owner` was correctly null).
Two other failures were over-specific oracle assertions, not wrong persisted
behavior: the promoted payment Task used “付款安排” rather than the expected
literal “付款时间”, and the normal two-deliverable assessment cited the actual
owner/action evidence rather than the separate sentence saying the deliverables
were independent. The v4 fixture/oracle and Task Agent guidance were refined for
these findings; the candidate replay after those changes is still required.
All case databases are isolated under `/private/tmp/project-centered-v4-full-75d78850`.

### Full v4 rerun at candidate `3a3070e2` (2026-10-05)

After the responsibility/owner rules and evidence alternatives were updated, a
fresh 19-case run completed **16/19**. The role-only task suppression,
overall-owner dispute, one-Project/multiple-source context, distinct deliverables,
same-source idempotency, same-reference versioning, same-ID promotion semantic
case, and exact Attention membership all passed in this run. Two cases ended in
`runtime_result_validation_failed` after a result-correction attempt
(`role-based-unnamed-suggestion` and `suggestion-promoted-same-id`); they are
unresolved runtime-contract failures, not semantic passes. The remaining
`completed-task-risk-persists` fixture lacked an explicit Project decision and
trusted completion linkage, so the Agent correctly retained an unresolved
Project clue and did not complete an ambiguously linked Task. The fixture is
being corrected to include the meeting's continuation decision, stable owner
identity, same conversation, and explicit reply-to reference. A new full replay
is required after this fixture correction. Artifacts:
`/private/tmp/project-centered-v4-candidate-3a3070e2`.

The corrected fixture was committed as `af893c10`; its fresh 19-case replay
completed **18/19**. The explicit Project-decision and trusted completion-link
fixture now passes, as do the two previously correction-sensitive cases. The
only failure is `peer-not-auto-member`: the Agent correctly kept the new
Wang Wu business-reconciliation responsibility in ProjectContext but also
created a source candidate from “王五负责另一项独立的商务对账”. That sentence
states a responsibility but contains no explicit action or action-item record,
so it must not create a second Task. This final finding led to one more prompt /
Skill rule: even when the responsibility names a distinct deliverable area,
`X负责Y` alone remains ProjectContext. The fixed native set must be rerun after
that rule is committed.
Artifact: `/private/tmp/project-centered-v4-final-af893c10`.

The Peer case was isolated and replayed once on `3e17adc2`: it passed with one
source-origin candidate for Li Si's explicit payment-schedule action, Wang Wu's
separate business-reconciliation responsibility stored only in ProjectContext,
one active Attention card with exactly the relevant payment Task, and no second
Task. This is targeted evidence; the full fixed 19-case replay at the revised
candidate is still required.

### Focused v4 native follow-up (2026-10-05)

The six-case native subset at candidate `ee02df12` completed **3/6** cases:
unknown overall owner, explicit responsibility-transfer conflict, and promotion
of the same suggested Task ID passed. The same-reference version and peer-member
cases failed before semantic comparison: both normal and result-correction
attempts recorded `runtime_result_validation_failed` / “No TaskAgentDecision
JSON found”; their stored result envelopes are empty. They are native runtime
result failures, not semantic passes. The completed-Task case ran successfully
but exposed both an oracle omission and an over-broad membership: actual state
contained the formal completed deliverable and the payment-risk suggestion
(two Tasks), while the Attention card also contained both. The corrected oracle
expects both Tasks but only the payment-risk member, and the Task Agent prompt
and shared Skill now explicitly exclude completed or unrelated Project Tasks
from an assessment's supporting members. Focused prompt/evaluator tests passed
(244 tests), Ruff and `git diff --check` passed. These changes still require a
fresh native replay; the prior artifact is immutable and remains evidence of the
earlier attempt.

A fresh peer-member replay at `15e2699c` completed after the runtime's normal
schema-correction attempt. It saved one payment follow-up suggestion with Li
Si as suggested owner, retained Wang Wu's distinct business-reconciliation
responsibility in ProjectContext, and the Attention card had exactly the one
payment-risk member. The prior fixture had required a second Task for that
independent responsibility; this contradicted the confirmed Project-first rule
and the instruction not to enumerate every small work item. The revised oracle
checks both distinct Project responsibilities and expects the single relevant
Task. This fresh case replay repeated twice at `3c7dca96`: one run failed when
correction hit `codex_provider_overloaded`; another failed result validation
because a suggestion also carried actual-owner fields. The same-reference case
also repeated “No TaskAgentDecision JSON found” on both normal and correction
attempts. Therefore the earlier successful peer domain readback is useful
semantic evidence, but the corrected fixed peer case has not yet passed its
evaluator. These native result-contract failures block the full comparison, W39
replay, PR merge and deployment.

The isolated completed-Task replay at `3c7dca96` persisted the expected two
separate Tasks: the formal验收材料 Task is `done`, the payment-risk suggestion
remains a candidate, and the active Attention card contains only the latter
Task. This confirms that completing the deliverable does not resolve the
Project risk and that the completed peer is excluded from Attention membership.
It is a focused persisted-row readback, not a substitute for the full fixed
native comparison.

For the current fixture revision, the source-only canonical input digest is
`b832fa601464e2a675b58661a2fd0792fb352973e18cca99393a1572701261f3`; the
full oracle fixture digest is
`b2ec9a6e40ab5f019ebe16bc2a59c6ada872be3518827772d3ba38b2786b3a9d`, and the
loaded candidate Skill digest is
`4249e1919a1f1b6dfcc084294ea47c94348add49b011acf22b1ca7f6fd5d2d69`.

The immutable W39 source copy was read-only backed up to
`/private/tmp/project-centered-w39-final-20261005.sqlite3` and opened using the
current Store code. All 403 Signal bodies and identity fields matched the source
hash exactly; Task and Project counts stayed 259 and 20; input 27465's hash was
unchanged; `quick_check=ok`; and the foreign-key check returned exactly the same
single pre-existing `meeting_alignment_runs` → missing
`meeting_alignment_jobs` row. No repair or original-copy write occurred. The
ordinary Store upgrade also introduced current-schema Project evidence/context
tables, populated Project evidence, and added default `origin=source` /
empty-suggestion fields to historical Tasks; the existing `business_object_tasks`
rebuild rewrote its timestamps. That timestamp behavior predates this change and
is recorded as a migration observation, not attributed to the Project-centered
source-body migration. Exact W39 replay and business-domain readback remain pending.

The approved design is `superpowers/specs/2026-10-04-project-centered-work-design.md`;
the implementation checklist is `superpowers/plans/2026-10-04-project-centered-work.md`.
Tasks 1–7 are one release unit. Core integration is saved in `eeb69de9`; API/UI
integration is saved in `707d41f2`. Neither commit has been deployed by this workflow.
Task 8's deterministic evaluation scaffolding is verified. The first fixed native
baseline/candidate comparison ran on 2026-10-04 and did not pass; a candidate
rerun after prompt clarification, real W39 validation, PR and release remain pending.
Local tests and synthetic browser checks do not establish a live business effect.

## Fixed evidence and comparison procedure

`tests/fixtures/task_project_centered_v4.json` contains 19 version-4 cases. Both
runtimes receive the same original `source_inputs` in the same order. Native
cases have empty `existing_context`: neither side receives manually seeded
Project roles, owner, risk or conclusions. Unit tests may seed persisted facts
to check the evaluator, but these seeds are not native comparison evidence.
Each source version is enqueued immediately before its turn; enqueuing all
versions first would overwrite earlier bodies sharing a source reference.

Expected values remain offline and are compared only after actual application.
They cover Project identity, saved context and responsibilities, actual versus
suggested Task state, every relevant Project judgment, actual receipts and
Attention membership. Optional supporting membership may specify a bounded
`allowed_task_counts` in an assessment; it does not relax expected Project,
outcome, evidence or application status. An independent Task with no Project
has an explicit empty assessment set. An unconfirmed Project clue instead
requires an insufficient-evidence judgment without an invented official ID.

Only the final promotion, selective-member and completed-Task/persistent-risk
cases permit `allowed_application_statuses=["applied", "existing"]`: the current
contract permits refreshing a risk proposal or verifying a saved card. This
bounded oracle choice was reviewed before native execution, follows the source
facts, and does not waive actual card/anchor/evidence/member checks. Other cases
keep exact application statuses. A receipt status alone never proves card ID
continuity.

The cases include zero-Task risk, normal progress, ambiguous risk, no-report
meeting/chat context, a two-Project report, three-source synthesis, role-based
suggestions and settled negative evidence, unknown owner, responsibility
change/conflict, human promotion of the same suggested Task, same Task updates,
distinct deliverables, repeated sources, changed versions of one reference,
selective Attention membership and a completed Task whose Project risk persists.

The existing `scripts/replay_task_attention.py` runs the runtime from an explicit
`--code-root`. Pin the old baseline at `da368453` and the candidate at a clean
committed revision; set each side's own CI Skill root. Use the same configured
`codex_oauth` / `gpt-5.6-luna`, effective 900-second total / 300-second idle
budget and concurrency 1. Record actual route, model, code revision and Skill
hash in each fresh artifact. Old experiment artifacts are historical evidence,
not substitutes for this comparison. Native execution has not yet occurred for
this fixed Project-centered fixture.

## Readback and observability

The evaluator reads actual domain rows, not the Agent's summary. The full
`business_*` snapshot includes original bodies, Signals, Project context revisions,
Task dates, links and events. Repeating identical source input must leave that
domain unchanged; Agent runs and attempts are counted separately. Same-ref
version expectations count distinct stored source documents, not Signal rows.
Project titles are compared as a multiset: two same-title official rows are not
one Project. Ambiguous identities cannot choose an arbitrary owner's context.

The evaluator checks the schema actually present. Missing current Project
context/evidence/shared-body tables are reported as
`project_centered_storage_missing`, with explicit missing tables and FAIL. It
does not migrate the baseline, substitute candidate modules, fabricate receipts
or add a production compatibility layer. Its observed-source rule and exact
raw-text/one-JSON-leaf quotation oracle are independent of runtime modules.
Successful old turns still receive subsequent original inputs despite comparison
failures. Real execution, run or projection failures stop the sequence; every
step's original failure remains recorded.

Per-turn `context_deliveries` records the metrics actually delivered to the
runtime: document/Signal counts, full/visible character counts, truncation,
visible ranges and citation-budget status when present. It omits source bodies.
Absent historical metrics stay absent. These are delivered ranges, not proof
that the Agent read or understood them. The existing read-only inspector accepts
an optional `--replay-result` artifact and attaches these metrics only to its
matching persisted input/run. Historical runs are not rewritten or reconstructed.

## Deterministic verification record

Regression counterexamples reject wrong owner, suggestions falsely made formal,
missing second Project, an unsaved zero-Task card, duplicate Task/Project rows,
outbound intent growth, wrong same-count Attention members, insufficient source
versions, duplicate-source domain changes and missing Project judgments.

The initial old-schema counterexample failed with `OperationalError` before the
evaluator-only fix; the initial duplicate-Project counterexample returned no
failures because title dictionary keys collapsed rows. After repair, focused
source chronology / missing-schema tests passed (3 tests). A readback smoke using
the actual pinned `da368453` Store returned FAIL with all three missing tables,
without importing candidate runtime modules. This was not an Agent/native run.
The all-case assessment assertion also failed against the incomplete fixture;
all fixed original-source cases now define their expected final judgments.
Final primary verification: four targeted files, **191 passed / 14.56s**,
Ruff and diff check passed. Independent final scaffold/oracle review: three
tool/evaluator files, **40 passed / 2.87s**, no outstanding must-fix. Positive
bounded-status tests validate the current decision model before recording it;
rejected/error receipts still fail. No native PASS is claimed here.

Frozen SHA-256 values for this scaffold:

| Artifact | SHA-256 |
| --- | --- |
| Version-1 fixture | `cd5ac2387694f3457d24066aa5cb3917a2494a7a893da3c1d6c73f06ad445e34` |
| Source-only canonical input | `93ea623065006242182f281800cf7b82621926529a177e996b3da394a57cea1d` |
| New evaluator tests | `c999e44a4eacdf313a235f5f738cab3fd50d61eda2b1936bbb21c2a8f030b636` |
| Candidate CI Skill | `11c5df5b30aec5acd3df7e31be3bca740609db02147875ad1831d898d1d357c2` |

The source-only digest is computed with
`jq -cS '[.cases[]|{case_id,source_inputs,work_item}]'` followed by SHA-256.
The review-stage fixture changes touched offline expectations only, before the
first native execution. See the comparison addendum below; each native artifact
records its clean committed code revision and actual loaded Skill hash.

## Post-main-merge candidate smoke (2026-10-04)

The feature branch was normally merged with the then-current `origin/main`
(`60125400`) in merge commit `63c5bf0e`; the merged tree is clean. The pinned
old baseline remains `da368453`. Candidate `63c5bf0e` still contains the frozen
Task-centered fixture/oracle and CI Skill SHA-256 remains
`11c5df5b30aec5acd3df7e31be3bca740609db02147875ad1831d898d1d357c2`.

After the merge, the 15 directly affected Task/Project/evaluator/API test files
passed (**1202 passed / 196.01s**); the two-file Task 8 evaluator/inspector
check passed (**25 passed / 1.94s**). These verify deterministic behavior only.

A one-case native candidate smoke initially used the planned `codex_oauth` /
`gpt-5.6-luna` route, 900-second total / 300-second idle limits, concurrency 1,
and the candidate's CI Skill root. The Codex CLI was present (`0.154.0`) and
reported logged in, but no Task Agent turn reached the model: the production
runtime probe could not configure the service MCP manifest. Direct command
construction identified the missing required environment variable
`MEMORY_CONNECTOR_URL`; `CONNECTOR_API_KEY` is also absent from the local shell,
the production `.env`, and the launchd plist. The replay recorded a failed run
with no runtime attempts and no Project, Task, or Attention rows. This is a
runtime-configuration failure, not a native semantic result. At that point, the
fixed native baseline/candidate comparison remained unrun; do not treat this
configuration-blocked smoke as a candidate pass or failure. The subsequent full
comparison and MCP configuration resolution are recorded below.

The frozen W39 database had the unrelated `meeting_alignment_runs` row 2298 →
missing `meeting_alignment_jobs` row 4908 foreign-key reference, which blocked
opening that exact frozen copy for a complete migration and replay. A later
W39-derived migration-function round trip is recorded below; it does not replace
the missing pristine pre-migration comparison. The real W39 semantic
replay/readback remains blocked. No orphan was repaired or bypassed, and no
production semantic import/apply, push, PR, or deploy was performed at this
stage. Task 9 remains unstarted.

Earlier implementation checks: 14 core files / 636 passed; multisource plus
Project readback / 161 passed; API four files / 25 passed; frontend eight files /
86 passed and production build passed. Browser checks used synthetic fixtures,
light/dark and 1600/433/320 CSS-pixel widths, not production business data.

## Native MCP configuration and first fixed comparison (2026-10-04)

The earlier smoke's MCP-manifest blocker was resolved without copying credentials:
the isolated replay process explicitly used the service's existing
`data/config/service-mcp.json` through `CEO_SERVICE_MCP_CONFIG_PATH`. The manifest
uses native CLI OAuth configuration for its connected MCP servers. A read-only
preflight verified the manifest and the `codex_oauth` / `gpt-5.6-luna` route;
no production database was opened or changed.

The first full 19-case fixture was then replayed sequentially on fresh isolated SQLite
databases, using the same original inputs, model, route, timeout and concurrency
for the pinned baseline (`da368453`) and candidate (`f45a7a7a`), with each side's
own CI Skill root. The native database artifacts are retained under
`/tmp/project-centered-eval.NFdB25` for readback. Baseline: **0/19** cases passed
the new Project-centered contract; old-schema/missing-Project-storage failures
were common and are expected limitations of that baseline, not isolated evidence
of a semantic regression. Candidate before the prompt-contract clarification:
**2/19** passed all current fixture assertions (`single-report-two-projects`,
`unconfirmed-project`). This is not a release pass.

The failed candidate cases exposed two separate issues. First, the fixture expects
the short title `甲客户一期交付` even in source sentences that say
`甲客户一期交付项目`, while the current prompt explicitly asks the Agent to
preserve the authoritative source title; title-dependent context and assessment
assertions then cascade-fail. The fixture/source naming contract needs review;
the oracle was not changed after seeing the output. Second, native output
validation repeatedly rejected assessment selectors containing both
`anchor_id` and `project_decision_index`, Task updates that set status/relevance
without `transition=update_fields`, and assessment support lists containing
`skip` decisions. The Pydantic validators are intentional; the prompt previously
did not state these exact constraints clearly enough.

The Task Agent prompt and validation-repair prompt now state those existing
contracts explicitly. Regression assertions were added and the full
`tests/test_task_agent.py` file passed (**225 passed / 14.64s**), with Ruff and
`git diff --check` clean. The candidate rerun against this first clarification is
recorded in the following section. Further prompt clarifications made after that
rerun still need native verification. The W39 foreign-key orphan blocker below is
unchanged.

## Prompt-contract candidate rerun (2026-10-04)

Commit `4b29348e` clarified assessment selector choice, disallowed `skip` indexes,
and the existing `update_fields` transition. A second sequential 19-case candidate
run used the same fixture, `codex_oauth` / `gpt-5.6-luna`, timeout, concurrency,
and candidate Skill root on fresh isolated databases under
`/tmp/project-centered-eval.NFdB25/candidate-guidance-*.sqlite3`. Only
`unconfirmed-project` passed all current assertions (**1/19**). The run still
showed title-oracle mismatches, Task/task-count mismatches and intermittent invalid
TaskAgentDecision output. Examples include status/relevance without the required
transition, a promotion that still carried a suggestion field, and
`project_link_evidence` without a Project selector. This single run does not prove
the prompt change made aggregate semantic quality worse; the native Agent is
non-deterministic, and the comparison is not repeated-sample statistical evidence.

The prompt now also states that new/skip decisions leave status and relevance
unset, promotion omits the suggestion field while preserving its saved history,
and project-link evidence requires a selected Project. These clarify existing
model-validator rules and do not weaken or bypass them. Regression coverage and
the full Task Agent file again pass (**225 passed / 9.63s**); Ruff and
`git diff --check` are clean. The rerun is documented in the next section; the
project-title source/oracle contract remains unresolved. No complete
fixed native comparison, W39 verification, PR, or deployment is claimed.

## Second prompt clarification native rerun (2026-10-04)

Commit `4cf23b9f` additionally clarified that create/candidate/skip decisions
leave status and relevance unset, suggestion promotion omits the suggestion
field, and Project link evidence requires a selected Project. The same 19 fixed
candidate cases were run sequentially on fresh databases under
`/tmp/project-centered-eval.NFdB25/candidate-contract2-*.sqlite3`, with the same
source inputs, model/route, timeout, concurrency and candidate Skill hash.
**2/19** passed (`single-report-two-projects`, `unconfirmed-project`); this is
still a clear fixed-eval failure, not a release candidate. Title-oracle mismatch
continues across cases whose source uses `甲客户一期交付项目`; the suite expects
`甲客户一期交付`. Task lifecycle/assignment outputs also remain inaccurate or
intermittently schema-invalid: this rerun again logged a status/relevance
transition error, and the responsibility-conflict, independent-Task,
same-source/version and completed-Task/risk cases did not pass. More prompt
wording alone is not established as sufficient. The fixture/title contract needs
an explicit resolution, and the remaining business-judgment failures need their
own expected-vs-actual review before any further algorithm change.

Readback of `candidate-contract2-project-risk-without-task.sqlite3` confirms that
at least some apparent title-related “missing context” failures are evaluator-key
cascades, not absent stored context: the Project row is titled
`甲客户一期交付项目`, its latest revision contains the payment-date fact, Project
evidence contains both meeting and chat Signals, no Task was created, and an active
Attention item is linked to that Project. The oracle looks up context/card identity
under `甲客户一期交付`, so it reports context, evidence and Attention mismatches
for this exact-title difference. This evidence does not resolve whether the desired
business title should retain or drop the generic suffix “项目”.

The third run used commit `4cf23b9f` and the same loaded Skill SHA-256
`11c5df5b30aec5acd3df7e31be3bca740609db02147875ad1831d898d1d357c2`. The full
`tests/test_task_agent.py` file passed (**225 passed / 9.63s**), Ruff and
`git diff --check` were clean. The semantic/native gate remains failed; W39,
independent review, PR, and release gates are still pending.

## Real W39 and release blockers

The preserved frozen W39 database has 259 Tasks, 16 Projects, 0 Attention,
398 Signals and 434 Task events; `quick_check=ok`. Its input 27465 includes
中汽创智、岚图、项目管理、Einride POC and 抽检包生命周期 clues. The department
heading 项目管理 must not be invented as an official Project. All relevant
clues require first-pass actual judgment or a stated identity/evidence limitation;
a second pass finding an earlier omission is not first-pass success.

The full frozen copy's pre-existing foreign-key failure is
`meeting_alignment_runs` row 2298 referencing missing `meeting_alignment_jobs`
row 4908. Source migration correctly rejected it and rolled back: original
Signal bodies, Tasks, run/event history and old schema marker remained intact.
The live database has the same independently observed unrelated orphan. No
repair, orphan deletion, ignored integrity check or focused replacement snapshot
has been authorized or performed by this workflow. Choices already requested
from Derek—an equivalent Task-domain-only comparison snapshot, and a separately
scoped production integrity repair—remain unanswered.

The empty temporary database used only for the pinned-Store smoke check was
moved to Trash after verification; it remains recoverable. The original frozen
W39 database and its migration-failure evidence were not removed or changed.

Do not deploy around this limitation. Once native comparison and real-data
verification are complete, require the approved PR, same-version loaded Skill,
standard `python -m app.deploy`, and actual PID/health/queue/Attention/History and
page readback. No production checkout edits, direct SQL card creation, automatic
assignment or whole-database business promotion belong to this release.

## 2026-10-05 resumed completion pass

The confirmed native output-contract defect was fixed: `TaskAgentCodexRunner`
previously requested no Codex output schema despite advertising
`structured_output`. The Task Agent now passes the checked strict schema through
`--output-schema`; `task_agent_output_schema()` derives it from the Pydantic
contract, requires every object property, removes unsupported `$ref` siblings,
and narrows the legacy arbitrary evidence dictionaries to their known or empty
wire shapes. The local parser and one same-session correction remain in place.
Regression tests were added before the implementation and showed both missing
schema selection and missing schema artifact as failures.

Verification: `tests/test_task_agent.py` and
`tests/test_work_tracking_skill.py` passed (**232 passed**); fixed evaluation
oracle/inspector plus semantic-store coverage passed (**517 passed**); Ruff,
`git diff --check`, schema parity and strict-object checks passed. A direct
synthetic `codex exec --model gpt-5.5 --output-schema ...` request generated a
valid `TaskAgentDecision`; its `null` default-list values also passed local
Pydantic parsing. This proves the strict schema can be used by the CLI, not that
the complete Task Agent workflow or business judgments pass.

The normal isolated replay entrypoint was also retried on fresh synthetic DBs.
It made no Project/Task/Attention writes and failed before any runtime attempt:
`no_eligible_route:codex_oauth=paused:runtime_probe_failed` (empty
`runtime_attempts`). Therefore fixed 19-case native candidate results remain
unverified; the native gate is not passed. The failure is at runtime capability
probe/route availability, not evidence that the corrected output schema was
rejected. A successful direct CLI smoke request does not substitute for the
production router probe.

Read-only inspection of the preserved local W39 working copy
`/private/tmp/project-centered-w39-final-20261005.sqlite3` found it is already on
Store schema `2026-10-04.4`, with 259 Tasks, 20 Projects, 403 Signals, 83 shared
source documents and 3 Attention items; `quick_check=ok`. Input 27465 is already
`skipped` and has prior runs 10634/10662; its latest stored projection includes
five Project registrations and three applied Attention proposals. This is a
readback of an already processed/migrated copy, not a fresh W39 replay, a repeat
idempotency test, or a frozen before/after comparison. The earlier note that this
copy still had the source-body migration blocked is stale for its current state;
the true pre-migration frozen baseline was not located or changed in this pass.

No PR, push, production deploy, or live Task page verification was performed:
the native route gate remains unavailable and the current W39 artifact cannot
serve as the untouched baseline. Do not treat prior persisted Attention or this
schema smoke request as release approval.

### Fixed v4 full replay on 05fa0c69

The complete serial native replay used 19 fresh databases, the configured
`codex_oauth` route with `gpt-5.6-luna`, the CI Skill SHA-256 recorded in each
result, 900-second total timeout, 300-second idle timeout and concurrency 1.
Fourteen cases passed. Five failed: two oracle mismatches
(`meeting-chat-no-report`, whose frozen expectation incorrectly required a Task
for routine planned progress; `peer-not-auto-member`, whose required fact phrase
did not match the source wording), two evidence-boundary failures (split but
individually valid citations in `same-project-three-sources`, and an invented
historical quote in `suggestion-promoted-same-id`), and one existing-card
membership rejection in the completed-Task/persistent-risk case. That final case
must keep the existing card's empty member list; no new payment Task is justified
without saved responsibility evidence.

The frozen oracle now keeps routine planned progress at zero Tasks, allows
multiple exact supporting excerpts for one assessment, matches the original
wording of the peer update, and does not expect a payment-follow-up Task absent
a saved responsibility. Prompt/Skill guidance now explicitly forbids adding a
Task updated in the current turn to an existing Attention card whose delivered
membership is empty, and forbids reconstructing historical quotations from
later summaries. Focused tests passed after these changes; a complete clean-
commit 19-case replay is still required. The 05fa0c69 result is diagnostic, not
the final gate.

### Full v4 replay on 8afb72c1 (2026-10-05)

The next 19-case native replay passed 15/19. Four cases remained red:
`existing-task-update` returned an invalid update/acceptance combination;
`repeated-same-source` ended in a Codex idle timeout (transport failure, not a
semantic result); `peer-not-auto-member` omitted the concrete verify-and-report
action stated in the source; and `completed-task-risk-persists` failed to keep
the project-level risk assessment and its suggested next step coherent.

The follow-up prompt and Skill now distinguish a specific verify-and-report
action from a bare duty-area responsibility, state that progress is not owner
acceptance, and permit the saved single Project overall owner as a display-only
coordinator suggestion for a project-wide risk when appropriate. The peer-case
oracle now checks the source's stable “payment uncertainty” phrase. The
completed-task case again expects the concrete payment-date follow-up suggestion
to the overall owner; this is a proposed next step, not a formal assignment.
These changes are covered by prompt/Skill regression assertions and the fixture.

The four-case failures have not yet been demonstrated resolved by a fresh full
native replay. Focused tests passed after the prompt/fixture changes, but the
fixed semantic gate remains open. The earlier 05fa0c69 interpretation that
forbade a project-wide suggestion without a saved specific responsibility was
too narrow and is superseded by the clarified single-overall-owner rule above.

### Full v4 replay on 3ae6e33a (2026-10-06)

The 19-case serial native replay used fresh temporary SQLite databases and the
same `codex_oauth` / `gpt-5.6-luna` route, 900-second total timeout, 300-second
idle timeout, concurrency 1 and Skill hash recorded in each result. It passed
15/19. The previously observed `existing-task-update`, `peer-not-auto-member`
and `repeated-same-source` cases passed after the prompt/fixture changes.

Four cases remain unresolved:

- `role-based-unnamed-suggestion`: material payment-date/cash impact was assessed
  and the saved Project role `王五负责商务回款` was preserved, but no display-only
  candidate next step was emitted.
- `single-report-two-projects`: the model emitted two Project decisions and
  Tasks for both report rows, but after schema feedback emitted an assessment
  only for the first Project; the correction turn failed the same strict contract.
- `distinct-deliverables`: both independent source Tasks and Project evidence
  were extracted, but the second Task's `project_link_evidence` spliced the
  Project-definition sentence and later action sentence, omitting intervening
  source text. The exact-source validator correctly rejected that output.
- `completed-task-risk-persists`: the completed acceptance-material Task was
  correctly excluded from the existing risk card's empty membership, but the
  model emitted no new payment-date suggestion for the still-active Project risk.
  The oracle's former expectation of one Attention member contradicted the
  preserved empty-membership rule; it is corrected to zero while retaining the
  separate candidate Task expectation.

The candidate run is at `/private/tmp/project-centered-v11-full-3ae6e33a/results.jsonl`.
Follow-up prompt/Skill rules and regression assertions now explicitly require a
candidate when a material risk maps directly to a saved Project responsibility,
one assessment for each Project decision index, and contiguous unsynthesized
Project-link quotations. Focused prompt-contract tests pass. Those changes have
not yet been validated by another native replay; do not treat 15/19 as passing.
The clean W39 before-migration snapshot remains unavailable, so the data
comparison/idempotency and release gates remain blocked independently of this
semantic replay.

### Targeted v4 rerun on `f6a3714d` (2026-10-06)

The three semantic/contract follow-up cases passed on fresh native runs:
`role-based-unnamed-suggestion`, `single-report-two-projects`, and
`distinct-deliverables` all passed with code revision `f6a3714d` and loaded Skill
SHA-256 `72a5d26dbdb0a26afefff352f928728a651482c5dccff1ef75ed9b9787938055`.

`completed-task-risk-persists` first ended in `codex_idle_timeout`; a fresh retry
produced the expected Project-linked candidate Task for the unresolved payment
risk. The projector attached that candidate to the new Attention card during the
first source step, so the second step correctly received a card whose stored
membership contains that Task. The later `task_ids: [2]` therefore preserves, and
does not expand, the delivered membership. The `2d4e5c83` result was marked failed
only because the temporary fixture edit incorrectly expected zero members and
zero assessment members. The fixture has now been restored to expect one member
and the payment-date candidate. The “do not expand an empty card” prompt rule
remains valid for cards that are in fact delivered with no members. A fresh replay
with the corrected oracle and the full 19-case replay have not yet run on this
revision. Targeted run artifacts are retained in
`/private/tmp/project-centered-v12-targeted-f6a3714d/results.jsonl` and
`/private/tmp/project-centered-v12-risk-retry-f6a3714d/results.jsonl`.

### Full v4 replay on `ad65d0bb` (2026-10-06)

The final candidate full run used 19 fresh databases, the unchanged
`codex_oauth` / `gpt-5.6-luna` route, 900-second total timeout, 300-second idle
timeout and concurrency 1. It passed 18/19. The completed-risk case, including
the corrected one-member oracle, passed. The sole failure was
`responsibility-change-conflict`: overall_owner correctly remained null and both
competing claims were preserved, but the new Project fact said the transfer had
not reached consistent confirmation / still needed verification, without
explicitly saying “总体负责人存在冲突”. The confirmed contract and frozen oracle
require that conflict to be stated plainly in a Project fact. The prompt and Skill
now include the exact semantic requirement and prohibit using only the softer
phrases. The full run is recorded at
`/private/tmp/project-centered-v15-full-ad65d0bb/results.jsonl`; a fresh native
rerun on the corrected final candidate is still required.

### Final candidate local and native follow-up (`388effc7`, 2026-10-06)

The final owner-conflict wording is committed as
`388effc734534ed79f8d7a608cedb4e2115fde2e`; the CI Skill SHA-256 is
`4749583bbd0f82695b34dfaaa2066863ce3e87a920398136c3c5e89b8708bbe5`.
Focused local verification passed: `tests/test_task_agent.py`,
`tests/test_task_project_centered_eval.py`, and `tests/test_work_tracking_skill.py`
reported 253 passed; `tests/test_task_semantic_store.py`,
`tests/test_task_project_centered_eval.py`, and
`tests/test_inspect_task_attention.py` reported 519 passed. Ruff, fixture JSON
parsing, and `git diff --check` also passed.

A fresh isolated native run of `responsibility-change-conflict` used the pinned
`codex_oauth` / `gpt-5.6-luna` route and a new temporary database at
`/private/tmp/project-centered-v18-owner-388effc7/owner.sqlite3`. It did not
produce a Task Agent decision: the only runtime attempt ended after the configured
15-minute limit with `codex_total_timeout`. This is an unavailable semantic
result, not a pass and not a semantic failure. Earlier in-sandbox attempts also
failed before model execution because Codex could not write its own `~/.codex`
state; allowing the native CLI to access its normal home resolved that specific
sandbox failure but not the timeout. The owner-conflict rule therefore remains
without a fresh native semantic result. The complete fixed v4 replay, frozen
pre-migration W39 comparison, idempotency replay, PR, deployment, and production
readback remain open.

### Full v4 replay on `5ced17fd` and assessment-support follow-up (2026-10-06)

The 19-case native replay used fresh SQLite databases, the `codex_oauth` /
`gpt-5.6-luna` route, 900-second total timeout, 300-second idle timeout, and
concurrency 1. It completed **18/19**. Only `existing-task-update` failed with
`project_assessment_receipt_mismatch`: the turn updated the formal Project-linked
Task for the exact acceptance-material work described by its `not_needed`
progress assessment, but returned empty `decision_indexes` and `task_ids`, so the
persisted assessment receipt did not link the updated work. The other 18 cases
passed. Results are at
`/private/tmp/project-centered-v20-full-5ced17fd/results.jsonl`.

The follow-up prompt/Skill and architecture/runtime contract now explicitly say
to include a Task decision when it updates the same concrete Project work as the
assessment (including `not_needed` progress), while excluding unrelated Project
peers. Regression prompt test was confirmed RED before the prompt update. After
the update, `tests/test_task_agent.py` plus
`tests/test_work_tracking_skill.py` passed 232 tests, and
`tests/test_task_project_centered_eval.py` passed 21 tests; JSON fixture parsing
and `git diff --check` passed.

A fresh native rerun of the repaired case is not yet verified. Launching the
standalone replay from this task's current shell failed before model execution:
the production service MCP manifest could not resolve `MEMORY_CONNECTOR_URL`,
and the Codex OAuth capability probe therefore reported `runtime_probe_failed`
with no Task Agent runtime attempt. The direct CLI smoke itself works, but it is
not a substitute for the routed replay. Do not mark the prompt repair as native
verified or use the 18/19 baseline as a passing fixed evaluation. The next
verification needs the same initialized service-MCP environment as the full
replay, followed by the complete fixed v4 replay. The migration function's
idempotency passed on a W39-derived reconstruction, but equivalence against the
pristine pre-migration W39 database remains unverified. PR, deployment and
production readback also remain open.

### Read-only production-schema baseline check (2026-10-06)

A read-only SQLite online backup of the production database at
`/Users/derek/Services/ceo-agent-service/data/auto-reply.sqlite3` verified as
schema `2026-09-25.1`, `quick_check=ok`, zero foreign-key violations, and no
`source_document_id` column. The Business Task migration tables had no records
(`business_task_signals`, `business_tasks`, `business_task_evidence`,
`business_task_events`, `business_task_anchor_links`, and `business_projects`
all contained zero rows). This confirms the production file is an older-schema
database but cannot establish real-record migration equivalence or substitute
for the missing W39 snapshot. The temporary backup was deleted after the
readback; production was not modified.

### W39-derived source-body migration round trip (2026-10-06)

The retained post-migration W39 artifact at
`/private/tmp/project-centered-w39-final-20261005.sqlite3` had 403 Signals, 83
shared source documents, 259 Tasks, 443 Task evidence rows, 443 Task events, 40
Project links and 20 Projects. Because its original pre-migration file was not
available, a new temporary copy was reverse-reconstructed into the immediately
pre-source-document Signal table shape, restoring each Signal body from its
saved shared document, then the current
`AutoReplyStore._migrate_business_task_source_documents` function was executed.

The migration preserved all 403 public Signal rows and the listed Task/Project
evidence, event and link rows; it recreated 83 distinct source documents. The
single pre-existing foreign-key violation (meeting alignment run 2298 referring
to a missing job) was unchanged. A second migration invocation was idempotent.
This is useful W39-volume, W39-data migration-function evidence, but it is not a
comparison against the missing pristine pre-migration snapshot: the source
state was reconstructed from the retained migrated artifact. Keep that boundary
explicit. The temporary 3.2 GB reconstructed copy was removed after recording
the result; the retained W39 artifact and production database were not modified.

### Current focused verification after assessment-receipt prompt fix (2026-10-06)

At clean candidate `689869a6bb65bf510e83213925a506deb4374365`, the focused
offline evaluator/diagnostic tests completed **27 passed**:
`tests/test_task_project_centered_eval.py` and
`tests/test_inspect_task_attention.py`. A separate rerun of the Task Agent,
shared Skill, and evaluator contract tests completed **253 passed**; Ruff and
`git diff --check` passed. These runs verify the prompt contract and evaluator
locally, not the native model behavior.

Frozen candidate artifacts:

- `app/task_agent.py`: `4e1b1a34fa7b8002be7b036b706a2014421964ff695ffe38dbbcf4260f46f74d`
- `ci/shared-skills/ceo-work-tracking/SKILL.md`:
  `9b171396738f800dae398d356e9c8f4bf9695ef8e9cbf590981a66426aa2c9b8`
- `tests/fixtures/task_project_centered_v4.json`:
  `8ef185f96117fa71523a152d92d7fe9846aba4662d58a5a05d7887f11eccffef`

The formal fixed native replay remains unverified. This shell cannot resolve
the service MCP manifest because these non-production environment variables are
not configured: `MEMORY_CONNECTOR_URL`, `CONNECTOR_API_KEY`,
`MEMORY_CONNECTOR_AUTH_TYPE`, and `MEMORY_CONNECTOR_CONTENT_TYPE`. The routed
probe failed before a Task Agent attempt; a direct CLI smoke is not equivalent.
Do not inject production credentials or remove MCP from the candidate to claim a
formal pass. Next steps remain: provide a safely initialized non-production
service-MCP environment; run the full fixed 19-case replay; and only then finish
the W39/business, PR, deployment and production-readback gates. CRM customer
implementation also awaits Derek's review of the written addendum in the
design specification.

### CRM customer object schema readback (2026-10-06)

After `sharecrm auth status` reported `tokenStatus=normal`, the authorized CLI's
read-only `data describe get` for `AccountObj` confirmed the customer object's
stable ID field is `_id` and its customer display-name field is `name`
(`客户名称`). `UDSText1__c` is labeled `客户注册全称`, a distinct business field.
No customer records were queried and no CRM writes were performed. The design
addendum now names these exact schema fields; implementation remains gated on
Derek reviewing that written addendum. This does not change the unrelated native
evaluation, W39 migration, PR, deployment, or production-readback gates above.
