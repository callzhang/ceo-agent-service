# OKR 综合 Agent 验收与线上报告核查

## 状态与证据边界

### 2026-10-23 正式发布预检：OA Skill 旧摘要与批准快照过期

PR #278 已于 08:54 UTC 合入 `main`（merge commit `2479202643d031046e9009536615771663bafdb1`）。
正式发布按空闲等待后，在 Consumer/Skill 文件摘要校验阶段失败：清单和批准前置记录把已安装的
`dingtalk-oa-approval/SKILL.md` 旧摘要记为 `b58fb9bb…`，并把候选摘要记为 `0175ea38…`；
当前生产安装文件与合并后 main 的源文件实际 SHA 均为 `59c0bfd4…`。因此是发布元数据过期，
不是 OKR Skill 行为或验收标准问题。候选 OA 文件内容没有变化，现将清单旧摘要和批准前置的
安装/候选摘要都校正到实际值，并保留其余文件、Skill 内容和发布规则。

同一份批准快照还有三项 OA 资产摘要陈旧：财务规则卡和 OA 人员 Skill 的线上文件与 main 源
都已是候选内容（分别为 `f15c6fea…`、`022dc534…`）；考勤差旅 Skill 的线上旧文件为
`131758f4…`，main 中已批准的候选为 `6fd80713…`。现据线上文件与 main 源分别校正旧摘要及
批准摘要，保留三项源文件正文和既有批准候选。

失败后生产代码仍为 `8a10080ffa19e050433e561f068bf56f1969d4e2`，PID 为 96045，healthz HTTP
200；运行配置仍为 368，task6 仍是 v27 / managed Skill revision235，最新 run 仍为 394981。
目标 Skill 文件读回仍为 SHA `59c0bfd4…`，没有契约文件写入，也没有新报告或外部副作用。
本次失败发布不构成 OKR 验收结果；修正清单通过定向校验并正式发布后，才会启动唯一一条新的
授权 task6 手动验收。

### 2026-10-23 手动 task6 run 394981：分析阶段实际失败，修复候选原生验收通过

run394981 由 task6 正式入口创建，reply task417039；不可变快照绑定 task6 v27、managed
`ceo-weekly-okr-report` revision235。完整 Consumer 轨迹有四次失败尝试：394985、395004、
395019、395079。前两次遇到真实的 Dingteam 登录过期；登录恢复后，attempt395019 读取完整
18人名单、15份有效 Q4 OKR 和3项 `goals_missing`，`prepare` 成功。之后模型因一位成员的独立
文档检索无匹配结果而退出，没有完成任何成员评分。attempt395079 复用了同一 scheduled run，
但又重读了名单，并在一次无匹配文档搜索后再次以 `analysis_incomplete` 退出。reply task 最终
`failed/agent_reported_failure`；没有 Audit、报告写入、通知提案、持久回执或外部副作用。

这是实际 Agent 行为问题，不是评测误判：工具确实返回空搜索结果，但 Skill 已要求缺少补充材料
时继续，因此模型应保留该证据缺口并继续分析。已在候选 Skill 明确“空检索不阻止后续成员分析”，
保留每位有效成员的完整分析、现有证据范围与评分规则；同时澄清 KR `base_score` 的离散档位
不适用于行为维度，行为维度仍按原有70–79岗位达标规则。修订后的摘要必须从最新 `summary.md`
读回并逐字更新待审提案。

固定空检索案例 `empty-independent-search-continues-analysis` 在原生 Consumer/Audit 流程中
通过：1/1 case、32项最终检查、Audit approve、无修订回合；工具全为合成 provider，不能算真实
发布。原有 v76 4/4 仍是先前基线，分开记录。新增案例还暴露单案例筛选退出状态的评测器错误；
状态回归修复后，单案例运行状态也为 passed。定向 pytest 52 passed，改动文件 Ruff 和
`git diff --check` 通过。

合入最新主线后，在提交 `67709ed8` 上再次执行同一原生案例，结果仍为 **1/1 passed**，32项
最终检查全通过，最终 Audit `approve`；本轮包含1次 Consumer 修订和3轮 Audit 检查。两位成员的
空检索均有记录，模拟 provider 的全部产物写入均读回通过。此证据对应合并后的代码与 Skill
摘要，仍是合成验收，不代表真实 OKR 文档或钉钉消息已发布。

截至该次复核时，该候选尚未部署，也没有再次创建 task6 运行。生产只读复核为 commit
`881ea46fe`、PID5478、healthz HTTP 200；task6 仍为 v27 / revision235，下次自然触发
**2026-10-25 10:00 UTC**。
394981 不重放。下一步推送并完成代码评审、正式发布；部署后仅新建一条授权的
task6 手动验证，严格核验完整 Consumer/Audit、全部报告文档真实读回和通知真实回执。

### 2026-10-23 正式发布与生产回读

PR #288（OA 考勤 Skill）先合入 `106c609d`，随后 PR #287（OKR 空检索继续分析及发布摘要收敛）
合入为 `55b0f9a3`。正式部署操作 `deploy-2ee360dd-3441-4496-8698-0b3b46721928`
经自然空闲等待成功，将生产从 `3875c379` 更新至 `55b0f9a3`；没有中断在途任务。

生产回读确认：supervisor PID `43616`，服务 worker PID `43623`，healthz HTTP 200；
Consumer/Skill 运行配置由 368 更新为 369，worker 对配置 369 的加载回执无错误，
task6 为 v28 / managed Skill revision240，加载的 OKR Skill SHA-256 为
`49ba4b0bdafdd2ba54989f6bd1ad6b799417464f2dad2cbeefb721eb85a32f8e`。正式发布回执状态为
`verified`；清单中的 26 项资产摘要全部一致，受影响的定时任务 Skill 引用读回一致。
队列 active 0 / queued 471，Attention 20，History 37604。发布后没有创建新的 task6
运行，因此这只证明 Skill 已发布并由新 worker 加载，不代表真实 OKR 报告业务验收通过；
run394981 保持失败历史，不重放。下一步应等待 task6 后续正常触发，再按完整 Consumer/Audit、
报告文档读回和真实通知回执验收业务行为。

无正文生产证据：[run394981 与候选验收读回](../evals/scheduled_task_generic/okr-production-readback.json)。
原生案例记录：[empty-search acceptance v3](../evals/scheduled_task_generic/okr-empty-search-acceptance.v3.json)。

### 2026-10-23 授权手动 OKR run 394267：登录恢复后仍未完成分析

run394267 使用不可变 task6 v23 / managed `ceo-weekly-okr-report` revision223，执行任务
416922；最终 History attempt15921 为 `failed`，Consumer 连续四次 `analysis_incomplete`
后停止。登录恢复后，Consumer 成功读取18名管理者的名单及来源，取得15份Q4 OKR，另有3人
确无Q4目标。随后第一轮要求读取一个不存在的本地绩效制度文件，未继续按已声明的 OKR
评分口径分析；其余重试也未进入评分。原因是实际 Consumer 行为，不是评测误判或认证失败。
本次没有 Audit、评分、文档写入或通知回执，也没有外部动作；旧运行保持失败且未重放。

当前生产 commit `4441aada0d36cde843eccd89f4d5f0a58c4aa268`，healthz 正常；task6仍为 v26 /
managed revision223，下次自然触发为 **2026-10-25 10:00 UTC**。候选修复明确让 Agent 使用
Skill 已声明的评分口径，并将额外材料缺失记录为限制。日报、message-triage、OA 审批、People
Skill 的安装摘要和 repository 摘要已按只读读回收敛；message-triage 当前 managed revision225
摘要也已核实。发布锁定测试 27 项、脚本边界和 precondition 检查 6 项、OKR/report 定向回归
134 项通过。代码和 Skill 仍仅在隔离分支，尚未正式发布或部署；发布后按 Derek 已授权范围新建
一次手动验证运行，并与自然运行分开统计。
无正文证据：[run394267 readback](../evals/scheduled_task_generic/okr-manual-394267-post-login-readback.json)。

截至 2026-10-22 17:50 UTC：task14 的自然运行 392889 已完成并产生真实文档更新和通知回执；
但业务验收未通过。Consumer 读取了完整的 65 个群会话目录（6 页），却没有调用任何逐会话消息
读取工具，最终报告也明确写出群聊未逐条读取。Audit 因消息最初缺少今日要点而退回一次，修订后
批准了报告和通知，没有拦住未尝试的必需群聊覆盖。日报 Skill 修复及合约回归已完成；日报事实、
回执和覆盖回归 **23 passed**，managed Skill 发布器定向回归 **27 passed**。代码已部署到
`526f3df5ef04dca3ed991e78adba0232718a7bee`，但 task14 仍绑定旧 revision203。正式 Skill 发布首次
被严格 precondition 拒绝：清单的 OKR 旧摘要仍写 revision222，而生产已运行 revision223、摘要
`18b23c5c…`。发布器在改写合约前即拒绝，没有改写合约文件。随后按生产读回更新清单候选摘要和
OKR revision223 的旧摘要；清单锁定测试和 26 文件只读 preflight 均通过。第二次正式发布已成功：
生产 commit `6f26fcbc1a4fb25a8b63f2733f7571ec7d924598`，task14 v51 绑定 daily Skill managed
revision224，SHA-256 `32d24bb5…`，与安装文件完全一致。healthz HTTP 200、PID60193；队列
active0/queued199、Attention22、History37257。Console System Health 显示 degraded（8 项 violation；
detail 包含未终态 work item，native delivery readback 有73/384项不可读），与 healthz 正常分开记录。
已完成报告不重放、不补发；部署后的下一次自然日报才可验证修复是否真正生效。
完整证据见文末 2026-10-22 自然日报记录及无正文生产读回 JSON。

### 2026-10-22 自然日报 392889：发布完成，业务验收失败

- 不可变执行快照：task14 配置 v49，managed `ceo-daily-report` revision203；reply416905、attempt15917，最终 `done`。Consumer 392892 revision0 与 392902 revision1 均完成；Audit 392897 revision0 返回，392910 revision1 批准。
- 完整轨迹：Consumer 会话 `01a163b3-a5fb-76e0-ad54-3cffad6d9e03`、`01a163b5-790c-7131-a416-9c1739a00e69`；Audit 会话 `01a163b4-fdb6-7193-914c-0e46858fee14`、`01a163b7-39b2-7703-bb5b-1574102bf1a4`。运行覆盖 2026-10-21 13:00 至 2026-10-22 13:00 UTC。会话目录完整读取 65 个、共 6 页，但消息读取工具调用数为 0；报告自己也说明没有读取群聊消息。
- 这是实际行为未达标：数据源返回了完整目录，Consumer 没有尝试必需的逐会话读取；Audit 的第一次退回只针对今日要点，第二次却批准了未覆盖的报告。不是评测误判，也不是 provider 读取失败。代码与 Skill 补强将明确区分“未尝试”和真实 provider 错误，并要求 Audit 对前者退回；实际错误/超时/不完整分页仍按既定策略披露，不降低覆盖要求。
- 文档确已写入并由 provider 完整读回：标题《CEO 每日总结 2026-10-22》，节点 `EpGBa2Lm8a7NA9YlUReYRy6RVgN7R35y`。提交正文 3498 字符，SHA-256 `af8603ff73170beef2e12711587e335f64f905a84983a78e88882d85f7d0c834`；provider 读回 3503 字符，SHA-256 `a3c1a052563c8a78968a79545f83fc1aafeaacba0a969a47a6f78669f9f877e8`。差异仅为 provider 对 Markdown 中 `_`、`*` 的五处反斜杠转义，去转义后与提交正文完全相同。Consumer 返回内容、Audit 独立读取文档及文档信息均已核查。
- 通知确有持久回执：System execution437、receipt18626、状态 `sent`；只读 provider 查询返回 `sendStatus=SUCCESS` 和真实 `openMessageId=msgALB31lHY4qfhfubfDS6J7w==`。
- 纠正此前自动化指令中的错误描述：手动 run391245 与自然 run392889 的 action identity 和 external key 不同，因此不存在同一 identity 的正文冲突。两条消息各自有成功回执；没有因此触发额外发送。
- run392889 保持原终态和外部结果，不重放、不补发。即使文档与通知发布成功，本次仍因来源覆盖失败而不计入新版自然验收通过。日报 Skill 合约修复已在 task14 v51 / managed revision224 生效；task13 当前 v44 / Skill202，task6 当前 v22 / Skill223，后两项 Skill 与 schedule 未改变。必须等待 task14 下一次正常触发 **2026-10-23 13:00 UTC**、task13 **2026-10-24 19:00 UTC** 和 task6 **2026-10-25 10:00 UTC**，按完整 trajectory、文档、审核和真实回执核验；此前不提前创建或补发报告。

### 部署后服务健康核查（2026-10-22 19:31 UTC）

这组 read-only 健康指标与三份报告的自然业务验收分开记录。healthz 仍返回 HTTP 200，但 System Health 为 degraded；质量门实际列出8条未解决项：2个 `ai_minutes` work-summary 输入以 `runtime_result_validation_failed` 失败，1个邮件分类任务以 DNS 名称解析错误失败，4条 workbench feedback 尚未记录 resolution，`ai_minutes_sync` 扫描器因听记列表翻页超过600页失败。最近4小时还存在1条 work-summary Task Agent 错误。另有 73/384 个最近 Audit run 没有可读 native delivery 结果；这是证据覆盖缺口，当前扫描没有据此生成 sent-receipt 违规。

Email provider actions 当前显示10个失败项（8个可重试、2个不可重试）；worker 状态曾记录 `EmailActionAttemptConflict`。两个邮箱扫描在19:31 UTC均有成功时间戳。没有重放这些动作，也没有查看或记录邮件正文。database-backup 的“database or disk is full”错误时间为10月9日；本次只读机器核查有102 GiB可用空间、SQLite主库约501 MB，因此未把旧错误当作当前磁盘满问题。以上失败和未解决状态尚未修复，不能据此声称 Console System Health 已恢复正常。

代码、Skill、原生合成验收、历史外部结果与新版自然业务结果分别记录。合成通过不替代真实运行；
文档读回、Audit 决策和通知回执也分别核实。

2026-10-20 UTC。原七案例的决策与独立正文/动作草稿验收已通过，详见
[原验收记录](scheduled-task-generic-agent-validation.md)。本记录补充 OKR 的完整
Skill 工作流、恢复、部分来源失败和真实报告读回。最终四场景 **4/4 通过**，
每项同时满足全部机械核查与独立 Audit approve。新版自然发布仍待到期。

原生探针运行生产配置选定的 `gpt-5.6-luna`、reasoning `high`，使用真实 Codex CLI、
真实 Skill prepare/render/destination 脚本与生产 purpose-output parser；provider IO
全部为隔离合成接口，消息只形成提案。独立原生 Audit 最多给一次修订，修订后再次
独立审核，所有机械断言也必须通过。它没有调用生产编排器或实际外部执行器。
CLI 事件未独立回报实际模型标识，记录的是明确请求值。完整设置、CLI 版本、脚本/Skill/cases/runner 摘要和合成调用轨迹保存在版本化 JSON。

## 固定四场景

| 场景 | 不可放宽的要求 |
| --- | --- |
| composite-coverage | 四人当前名单；两名同名者按 userId 区分；自报百分比不得代替独立证据；缺目标与成员来源失败各保留无评分段；公式、真实子页、完整读回和精确通知提案均通过 |
| recover-changed-source | 仅一个人的独立证据变化；来源未变者分析字节不变，变化者必须重分析；不得复用旧来源、制造重复子页或漏更新附录 |
| recover-changed-date | 评分日期变化，两份分析摘要均失效并更新，报告窗口与新日期一致 |
| shared-source-auth-failure | 共享认证失败必须失败并保留原因，不得发布、形成群消息或伪装成成员缺口/成功 |

[v1 案例](../evals/scheduled_task_generic/okr_cases.v1.json) 完整保留。
v1 将已按原承诺完全交付、客户验收且投入使用的业务 KR 错写为 60–80 分；
权威 `dingtang-okr-review` 的业务完成档位明确为完全达成 100 分，行为标杆条件
属于领导力/文化评分。v2 将该项改为严格的 **100–100**，其余事实逐字不变，
并增加未评分职级和真实缺口核查。新范围并非旧范围的超集；失败历史不重写。
回归固定 v1 hash，并验证两版事实相同和原七案例/原评分函数不变。

## 根因、失败保留与修复

- v1–v3：native provider 不接受顶层 allOf，探针启动失败。使用纯传输 envelope，
  解码后仍执行完整生产 schema/parser，保留结构化错误。未删掉条件校验。
- v4：合成 MCP 未配置 native 工具批准模式，调用全被拒。仅给该隔离夹具使用
  native 的直接配置；未扩大生产工具权限。
- v5：流程能完成；除上述错误业务预期外，Audit 找到两个真实渲染缺陷：未评分者
  已知职级被抹去，群摘要只给泛化缺口列表。修复 Skill 脚本，分数仍为空。
- v6：Agent 将“无额外行为标杆”误用为已完全达成业务 KR 的扣分理由，又把
  summary.md 的 Markdown 链接重写成裸链接。澄清 Skill 两类评分的适用范围和
  summary.md 为唯一通知原文；未修改公式、阈值或消息执行契约。
- v7：主流程通过；Audit 修正同名负责人歧义后再次批准。来源变化恢复失败，
  因为 Agent 重写了来源未变者分析，附录解析漏传父节点并产生重复页面，
  Audit 未发现全部发布差异。保留失败结果；prepare 增加来源摘要匹配的
  analysis_reusable，Skill 要求保留这些文件、恢复按主节点定位已有附录，
  修订后核对所有当前产物与读回。现有 Audit 生命周期未改变。
- 同时复现一个核查器漏洞：一套旧来源与旧分析自洽时可错判当前恢复成功。
  新增 exact_current_source_payloads 将来源逐项与本次固定事实比较，收紧验收。
  合成文档 info 返回真实存储的 parent_id，供只读审核检查父子关系。
- v8：调用者误把带目录路径传给相对评测目录的输出参数，第一案例完成后保存失败。
  最终轨迹随临时目录清理，不能计分。保留无语义通过声明的保存失败记录；新增
  输出目录启动检查（先 RED 后 GREEN），在模型配置与调用之前拒绝错误路径。

- v9：机械核查全部通过，但二次 Audit 错把评测日志中的早期片段当作当前产物。
  日志放在业务产物目录，并在读取自身时递归追加；最终 main.md 与读回均为同一
  SHA-256 `f80ee279b928cc2aeb55dcdc5fe7cca0eae1a67419134c53cf8e1e2ce3cdea42`，
  轨迹实际包含多次带链接的 render，与审核所称“只有一次未带链接 render”相反。
  将日志移出 Agent 可见目录，回归先 RED 后 GREEN。v11 从 v9 完整保存的产物
  恢复原始字节，严格验证全部机械断言、分析摘要、完整正文、固定事实/Skill/runtime
  不变后，只重新执行独立只读 Audit；不重新抽样 Consumer，也不增加修订次数。
  v9 的失败结论和两次审核反馈仍原样保留。v10 另因已脱敏的历史 Skill 调用
  被重复当作原始正文脱敏而保存失败；修正脱敏的幂等性，并先保存完整角色阶段
  checkpoint 再整理轨迹（回归先 RED 后 GREEN），不作语义通过声明。

v11 主流程和来源变化恢复均通过机械核查与独立审核。日期变化场景失败：Agent
将自己的发布清单命名为 documents.json，覆盖了同目录下同名的模拟 provider 内部
状态，导致后续 canonical info / destination 失败。角色正确保留技术失败并未发送。
这是夹具所有权冲突，真实 provider 状态不应由业务产物文件修改。新增 RED/GREEN
回归，将 provider 状态也移出产物目录；核查器只读该独立状态，业务同名文件不能
伪造或破坏 provider 事实。

v12 曾逐字恢复 v11 两个初步通过的场景，重新核对当前来源、分析摘要、全部读回/公式/
父子关系和新 provider 状态检查；不重新调用这两个场景的 Consumer 或 Audit。
日期场景在隔离 provider 状态后重跑，与共享失败场景通过当时的断言。旧 v11 失败
完整保留。然而日期 Audit 同时揭示此前未覆盖的评分标准缺陷，因此 v12 的4/4
仅为当时检查结果，不能作为最终完整验收。

- 权威 operation Skill 明确 80–89 要有超出职级标准的具体案例，70–79 才是
  符合标准。managed Skill 把边界写为 above80/above90，且未明确正式维度，
  导致模型用观察项代替正式四项领导力/三项文化，前两次 Audit 也未抓全。
  修正为 inclusive80/90 并列出正式维度；公式没有改变。
- v3 案例保留 v2 全部事实和业务 KR 分数要求，新增正式维度和当前标准70–79
  的严格断言。回归证明旧初步通过答案不满足新增核查；不复用这些答案为最终通过。
- 再核对实际工具代码发现，旧模拟写入返回 Markdown readback，真实服务返回
  SHA命名的 JSON 回执。最终夹具直接调用真实 app.agent_cli 的产物读写、列表、
  文档写入和文档读取回调，仅模拟任务绑定和 provider IO；规范URL与父节点使用
  实际 inspect 的 data.document 结构。增加真实回调/JSON回执回归，及必须执行
  prepare、必须读取规范URL、正文提交摘要和完整JSON读回的机械断言。
- v13 在相同冻结事实、模型/runtime、一次修订预算上重跑全部四场景；不复用旧
  Consumer/Audit。旧的临时复核/前缀复用入口因业务标准变化已退出当前 runner，
  历史代码与结果保存在先前提交中。v13 的主流程和来源恢复通过；日期场景仍失败。

v13 的日期轨迹进一步证明：首次 Audit 对 summary.md 使用 offset=1，删掉首个#，
却将该尾部当作全文；其返回 sha256 仍正确指向完整文件。Consumer 随后手改生成的
summary.md 来迎合错误反馈，后续审核还发现同名 owner_names 未消歧。新增回归
复现这两个问题：公共接口明确0起字符偏移，整页也返回范围元数据；Skill准备结果
给出稳定subject_label及owner字段说明；核查器重跑真实渲染并逐字比较所有生成文件，
按实际offset/完整文件SHA合并分页，拒绝手改canonical及同名裸负责人。修订预算不变。

v14 的只读复核因评测恢复后manifest仍指向旧临时目录而失败；发布正文与分析字节
没有变化。恢复步骤现用真实脚本重建当前目录的plan/schema/manifest，同时断言
分析摘要及全部发布正文逐字不变，并加入manifest目录绑定回归。v15将对已通过且
满足新增严格检查的两个原候选只读复审，不重采样Consumer、不增加修订；失败日期
场景与共享故障场景随后执行。旧失败均保留。v15诊断复核四项通过，但最终生成行为
仍另以当前Skill4/工具代码从零运行v16。

v16主流程首轮通过；来源恢复首轮将仍有原型进展者误评0，独立Audit要求按低档
部分进展修正，Consumer在原有一次修订内改为40。最终评分、公式、身份、全部
provider正文与层级及通知提案均通过，Audit approve；核查器却只统计本地回执文件
读取，漏认了完整直接provider读回。逐项重序列化这5份实际MCP返回，其SHA均与
对应写入回执SHA完全相同，且发生在最新写入之后。

读回观察器现同时识别完整回执文件与上述完全相同的provider快照；节点、版本、
complete/status、全文与SHA要求未放宽。新增先RED后GREEN的等价通道回归及旧
版本拒绝回归。v16原失败文件保持不变。v17在调用模型前因恢复缺少未被单独读取的
本地附录而停止，现用实际写入SHA与完整provider回执的双重SHA证明恢复原正文，
不从猜测或摘要生成内容。

v18保持v16的全部原始Consumer/Audit/修订输出，前两案例只用修正后的观察器重评，
不重采样、不加修订；当前Skill、实际工具函数、模型/runtime和冻结事实均与v16
完全一致。原错误检查和新检查分开保存。日期与共享故障案例随后新执行。v17的恢复前置错误未调用模型；最终结果保存在
[okr_results.v18.json](../evals/scheduled_task_generic/okr_results.v18.json)，状态passed，
四案例全部通过，临时目录清理确认true。

| 最终场景 | Consumer/Audit与核查结果 |
| --- | --- |
| 主流程 | 当前Skill4/工具代码的v16原始Consumer输出；Audit首轮approve；原观察器只认本地文件而误判读回，新观察器对原输出重评通过，29项机械断言通过 |
| 来源变化恢复 | v16原始首轮把原型误评0，原一轮修订后40；Audit approve；按相同完整JSON SHA重评，31项机械断言通过，未变化分析逐字保留 |
| 日期变化恢复 | v18新Consumer与Audit首轮通过；31项机械断言通过，两份分析按新日期失效重做 |
| 共享认证失败 | v18新Consumer正确failed；Audit approve，4项机械断言通过；无渲染、发布或通知提案 |

前两项当前工具、Skill、模型/runtime、冻结事实与v16完全一致；仅修正观察器，
原始判定、首轮错误、原审核和修订均保留，未增加反馈预算或重采样Consumer。
分数/身份/来源/正文/公式/层级标准均未改变。当前读取契约和Skill源码摘要已与
最终结果逐项核对；合并后还须核对实际工具函数AST摘要。

每次修改前核对实际输入、工具返回与模型结果。所有 failed JSON 留存，未用反复
抽样掩盖失败。Codex 内置资源目录查询仅记录为 native_inventory_calls；所有真正
provider 工具必须属于唯一合成服务器，外部同名工具仍被回归拒绝。

## 定向测试

最新一次相关运行：`test_scheduled_okr_agent_eval.py`、`test_weekly_okr_report.py`、
`test_scheduled_task_generic_eval.py` 共 **129 passed**。渲染两项回归与新增
恢复/当前来源两项回归均先 RED 后 GREEN。原七案例的 13 项保护保持通过。
发布整文件 `test_consumer_system_release.py` 最近一次 **27 passed**；其中旧
finance 预置摘要错误地与已更新的安装文件比较，改为固定原有批准摘要，保留全部
当前源摘要与 managed 摘要断言。未改变发布前置条件。最终合并/发布核查另追加。

## 三份报告实际外部结果

以下是旧版本真实执行。它们不证明迁移后或本次 Skill 修订后的自然报告成功。
本次只读核查，没有重放、创建报告或发送通知。正文和回执令牌未进入仓库；
仅保留 [无正文核查摘要](../evals/scheduled_task_generic/okr-production-readback.json)。

### CEO 日报

- 自然调度 381115，task 405712，attempt 15703，配置 v29 / Skill revision132。
- [CEO 每日总结 2026-10-19](https://alidocs.dingtalk.com/i/nodes/amweZ92PV6xpLPY5sRDzBlBL8xEKBD6p)
  实时完整 JSONML 读回成功，revision1；通知 System execution324 有成功持久回执。
- 原 consumer_document_write 的 `doc_write_verification_failed` 保留。Markdown
  导出改变了空行/转义/格式；完整 JSONML 与提交稿剔除空白后的可见文字 **4402/4402
  字符逐字相同**。这是已证实的导出校验误报，无业务文字丢失；不宣称 Markdown
  字节相同，也没有修改通用文档机械校验来吞掉原错误。

### CEO 管理周报

- 手动调度 379485，task405567，attempt15653，配置v25；最终事项 `skipped`。
- [2026年10月26日管理层周会](https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiXEyNdeyJ1waOeDk)
  当前完整 JSONML 读回成功，revision1；与原 consumer 工具持久读回的 JSONML
  结构逐项相同。文档实际内容存在。
- 后续审核明确表示当前运行未绑定原始精确写入、版本保存及完整非目标区域差异
  回执，故未再次写入。当前事项跳过不能冒充完整发布审核通过。保留历史失败与
  跳过，不重放该报告。

### OKR 管理周报

- 旧服务命令手动运行379123，日期2026-10-19，旧摘要sent。此前自然运行375134
  的共享来源失败仍留在历史，不能用后来的手动成功覆盖。
- [2026Q4 至今的管理者 OKR 进度周报](https://alidocs.dingtalk.com/i/nodes/m9bN7RYPWdbednwAiRewoykjJZd1wyK0)
  主报告与 **18 份附录，19/19** 实时完整读回成功，共153291字符。
- 实时 wiki 子节点列表分页完成，无更多页；真实子节点集合与报告中的18个附录链接
  完全相同。群发送持久 openTaskId 再查询 provider，sendStatus 为 **SUCCESS** 且有
  实际消息身份。此处未宣称正文与本地原稿逐字相等，也不将旧服务成功当成新 Agent 验收。

## 自然调度与尚未到期的结果

迁移后已取得正常完成摘要的自然服务命令：钉钉消息、会议结论、微信、OA、恢复扫描、
日历邀请、邮件分类，共7项；邮件 failures=0。pending、同任务 overlap 跳过和
尚未到期的命令均单独保留，不写成已完成。

| 新版报告 | 已核实的自然触发 UTC | 美国洛杉矶时间 | 状态/下次触发 UTC |
| --- | --- | --- | --- |
| CEO 日报 | 2026-10-21 13:00 | 10月21日 06:00 | Consumer/Audit、完整 Markdown 读回和通知回执已核实；下次 2026-10-22 13:00 |
| CEO 管理周报 | — | — | 尚未到期；下次 2026-10-24 19:00 |
| OKR 管理周报 | — | — | 尚未到期；下次 2026-10-25 10:00 |

### 洛杉矶本地时刻与夏令时

2026-10-21 核对生产快照发现，日报仍以 `Asia/Shanghai` 的 21:00 配置来对应洛杉矶
06:00，OKR 周报仍以北京时间周日 18:00 对应洛杉矶周日 03:00。当前夏令时下二者的
UTC 触发时刻正确，但美国冬令时开始后会提前一小时。仓库默认值已调整为洛杉矶时区的
06:00 和周日 03:00；升级只应更改仍精确匹配旧默认周期与时区的任务，保留自定义设置，
并产生新配置版本而不改写旧运行快照。定向迁移与自定义保留回归已通过，变更已于
2026-10-21 部署至生产。服务启动 seed 完成后再次读取生产配置，task14 已为 v41，
task6 已为 v13，均使用 `America/Los_Angeles`；task13 保持原 v35 和洛杉矶周六 12:00。
三项任务仍启用，任务 ID 未变，下一次 UTC 触发时刻未变；最近运行仍分别是 task14 的
389818、task13 的 379485、task6 的 379123，其冻结快照保持旧配置版本。证据见
[无正文生产读回](../evals/scheduled_task_generic/okr-production-readback.json)。

日报10月20日自然触发确认漏记。电源日志在05:50—06:10 PDT只有CPU维护唤醒，
当天首次完整唤醒为13:38 PDT；这强烈指向主机当时休眠，但保留证据不能单独证明
服务进程在06:00的状态。用户明确选择不自动唤醒电脑；本次新增的唤醒计划已撤销，
原电源设置已恢复。独立的启动恢复 SQL 错误也曾存在并已在生产修复，二者因果贡献
无法从现有记录精确拆分。

本次代码修复会在服务启动或主机恢复后为每项任务补最近一个漏掉的触发；更早的漏跑
合并，不追溯创建多份报告。若当时上一轮尚未终态，保留该补跑时间点，待上一轮结束再提交。
10月20日漏跑不会追溯补发。调度器补跑修复已随生产提交 `16062f083` 的后续主线部署，
当前生产提交为 `effc38f936b418f3e4957542123590f00ef0e1ef`，主 PID `63212`，healthz 正常。
10月21日日报是到点后的正常 scheduled 触发，不是睡眠恢复补跑；它验证了新版本正常报告链路，
但尚未实测主机睡眠后恢复时的补跑分支。新版自然验收仍须绑定不可变配置/Skill revision、
完整 Consumer/Audit 轨迹、文档完整读回与真实通知回执；不手工重放。

### 新版 CEO 日报自然结果（2026-10-21）

- `scheduled_task_run` 389818 于 13:00 UTC 正常触发，task 14 / 配置 v40（cron `21:00`
  `Asia/Shanghai`；本次等于洛杉矶 06:00 PDT），快照绑定 `ceo-daily-report` managed
  revision 203；没有手动运行或补发。
- Consumer run 389822 completed，Audit run 389848 completed；attempt 15859 与 reply task
  416660 均为终态 `done`，revision_count=0。已读取两份原生完整轨迹。Consumer 一次
  `read_service_activity` 因不支持的来源名返回明确错误，随后以有效来源重试并完成；没有将错误
  当成空数据或成功掩盖。Audit 独立复核并批准唯一投递动作。
- 日报文档已创建并更新至 revision 1。最终 Consumer 产物为 3310 字符，SHA-256
  `a6f83ad0fd28b33f2263dfd6a0e79e105a3fd9177a927580b9120a53e6933406`。最终写入后，Audit
  再次从 provider 读取完整 Markdown（3311 字符，SHA-256
  `b4d785ed79742bf0ccb0dc0828204d4ecc11e62b2c5683b3aa013c0366e97998`）；内容仅多 provider
  的结尾换行，去掉结尾换行后逐字相同。完整 JSONML 读回也成功，但发生在最终更新之前，故只将
  最终更新后的完整 Markdown 读回作为最终正文核验。
- System execution 410 的唯一 action 为 `verified`，本地持久回执记录 `sent`；随后按回执中的
  provider 查询得到 `sendStatus=SUCCESS` 和真实消息身份。正文、消息正文、收件人标识及原始回执
  均不写入无正文证据文件。
- 此结果属于新版自然日报通过；不证明睡眠恢复补跑分支已实测。管理周报与 OKR 周报仍待各自
  正常调度，不提前创建或重放。

## 最终集成核查（2026-10-20 UTC）

最新主线普通合并后，相关快速回归 **141 passed in 11.47s**，覆盖 OKR
原生评测、来源/公式、原七案例、产物分页和新增普通报告任务。原生 v18
结果的9个实际工具函数 AST、5个 Skill/脚本源摘要与当前代码相同；5份
Consumer/修订结果仍通过当前生产 parser。所有发布 manifest 当前源摘要逐项一致。
包含发布/回滚的整组定向运行 **168 passed in 194.83s**。随后最新主线
的独立发布/回滚复核 **27 passed in 186.49s**。Ruff、diff 检查通过；
正式部署读回在完成后追加。

已建立本线程心跳“新版三报告自然验收”（automation-4），每天洛杉矶时间
07:00、13:00 检查正常调度；只在取得新版实际运行、审核、文档和通知证据
后更新状态，三份全部核实后暂停。不会提前重放历史报告。

### 发布清单过期的实际失败与修复

同期部署先将代码切换到89a4a130d；本次首个排队命令返回“another deploy
got there first”，不能当成 Skill 发布成功。随后正式发布操作
`deploy-9676e0a2-4f96-4571-a29d-9b36afd26a14` 在严格旧 SHA 校验处失败并
自动回滚，恢复 PID91999：财务 operation Skill 及规则卡此前已更新到批准
的新摘要，而 manifest 仍固定更早的旧摘要。没有发生本次 OKR Skill 写入。

只读采集当前26份安装文件和7项 managed 摘要，见
[精确前置摘要](../evals/scheduled_task_generic/okr-release-preconditions.json)。
新回归先在过期 finance 摘要失败，再只更新两项 `old_sha256`；所有新源、
业务内容、managed 旧摘要和严格发布代码均不变。修正后相关44项通过，
原“旧安装摘要不符必须在写入前失败”回归1项通过；直接调用实际只读
`_load_file_plan` 对当前26份文件的严格前置核查全部通过。先前允许旧/新
任一值的辅助文件比较不足以证明发布条件，不能替代严格发布器检查。

## 正式上线与最终状态

2026-10-20 UTC，操作 `deploy-2e035667-34c8-4e23-8b8c-e346ff7f2ec3`
状态 **succeeded**，无错误。生产提交
`65560d34d8de63471d1d05d144cbc344ceab5815`，主 PID **93354**，healthz ok。
部署命令为 `python -m app.deploy --publish-consumer-system-contracts`；正常静默、
备份、发布、重启与加载校验均由既有部署器完成。没有绕过静默或精确摘要条件。

05:03:52 UTC 的最终只读核查，见
[无正文部署证据](../evals/scheduled_task_generic/okr-deployment-readback.json)：

- 14项任务的ID、迁移键、周期、时区、启停、命令和prompt摘要逐项保留。
- 原144份历史配置快照的逐项摘要全部相同；正常调度新增后共146份。
- OKR task6为配置v8，managed revision **209**（parent204，revision_number2）；
  Skill元数据version4。四份安装文件与最终v18验收源摘要完全相同。
- 管理周报task13配置v31、Skill202；日报task14配置v36、Skill203。两项Skill
  内容未变，配置版本随正常发布流程更新。三项下一次触发时间未变。
- 启动中间核查曾见failed18、Attention13。逐项对照只读备份与原始run，
  涉及的是7—9月已存在的失败运行；现有启动恢复先临时投影为failed，随后
  相关任务恢复原done。原始失败run从未改写，也没有重放。
- 完成正常恢复后的failed **9**，与发布前相同；失败History仍为相同8个ID，
  新增失败ID **0**。Attention **3**，未将旧OA等问题宣称为已解决。
- 队列pending9、processing4是当时事实，不代表所有业务完成；调度器、
  dispatcher和服务心跳均healthy/running，tick晚于本次部署。
- 部署成功后自然日历扫描387082（05:01:10 UTC）完成，摘要
  `calendar-invites-once queued=0`。此前迁移后的7项命令完成事实仍保留。

原七案例与OKR综合Agent验收均通过。新版三份报告还未到期，实际自然发布
不能提前标记通过；本线程心跳继续核查。当前历史报告结果仍分别为日报真实
文档与通知成功、管理周报实际写入但事项skipped、旧OKR服务主报告及18附录
完整读回并发送SUCCESS。未重放或补发任何历史报告。

## 用户授权的手动 OKR 运行 391405（2026-10-21 UTC）

这是用户明确授权的正式调度入口手动运行，不算自然周报验收。触发快照绑定
task6 配置 v14、managed `ceo-weekly-okr-report` revision 209。第一次 Consumer
391423 因 `analysis_incomplete` 失败；自动重试 Consumer 391492 读取了18人，生成
14份 `analysis_failure` 和4个 `goals_missing`，并写入主报告及18份附录。Audit
391503 真实退回：14位有目标成员仍没有逐 KR 证据分析；只凭0进度和无评论不足以
说明分析完成。该候选虽有当时的 provider 文档读回，但没有通过审核，不是最终报告。

Consumer 对修订的第0、1、2次尝试（391505、391507、391512）都返回
`analysis_incomplete`，没有完成逐 KR 分析、更新读回报告或提交通知；截至
Consumer 391528 于 23:54:27 UTC 也以不可重试的 `execution_failed` 结束。它已重读
18人、重建准备材料并查了钉钉消息/文档和服务活动，但没有写出或更新任何逐人分析、
报告、附录或通知摘要。其终态理由是不能据有限检索对14位成员评分；没有文档写入或
群消息发送。原候选 391492 的草稿文件仍保留，不能当作本次已通过发布。

根因有两层，都是真实行为问题，不是评测误判：Consumer 多次从头重读成员来源，随后
把大部分时间花在收集/少量宽泛检索上，没有按成员续做逐 KR 分析；此外，实际代码
`report.py` 曾仅按来源摘要判断分析可复用，因此同摘要的 `analysis_failure` 占位也会被
标记 `analysis_reusable=true`，不适合恢复未完成分析。没有审核通过候选或群消息回执。
不要重放现有草稿；修复后的真实手动报告仍待新 Skill/script revision 正式部署后再运行。

为此已在隔离 worktree 更新 OKR Skill 至候选 version 5，并修正 `report.py`：修订先核对
Audit 与同次运行产物，只补缺失/变化来源；只有完整成功分析可复用，`analysis_failure`
占位必须重新分析；逐人完成后保存。零进度和未找到佐证按原评分标准处理并说明缺口；
若本轮未完成则保留已有进度并给现有有界重试机会。原定向评测文件在加入占位复用回归后
**35项通过**；Ruff、diff 检查、Skill/script 发布摘要核对通过。此变更尚未正式发布或部署，
其原生综合回归及新版实际报告运行仍未验收。

### 原生综合验收 v19–v21 的行为评分判定根因

v19 的最终报告使用了四项正式领导力维度和三项文化维度。它对有当前职级证据的战略、
团队赋能、组织协同、担当和透明协作评分75；对缺少具体证据的创新能力和AI时代原生能力
评分60，并明确说明证据缺口。输入的 `behavior-review` 只支持前述五项，没有创新行为或
AI使用及增益证据。

旧 grader 却要求每个人七项分数都在70–79。历史 v16 展示了这个判定会接受的误导结果：
它对没有证据的创新能力和AI能力也给70，并把业务交付/客户验收当作行为证据。v19 的
Agent 输出明确把缺少证据的两项记为60；旧 grader 因此报错，暴露了评测条件没有按事实
区分维度。但后续 v20 原生重跑又把两项都打成70，理由仍分别只是完成业务交付和“文化
行为总体有佐证”。这次失败证明除了旧 grader 判定过宽外，也有实际 Agent 行为问题：
它会把别的维度证据挪用到创新和AI能力，审核也没有发现。

评测现在按每个维度的输入证据分别验分：有当前标准证据的五项必须为70–79；缺少证据的
创新及AI两项必须为0–60。回归也确保把这两项虚增至70会失败。这让评测更严格地核对
证据，而不是放宽评分。v21 显示 Skill 的一般性“不得跨维度套证”仍不足：Agent 修正了
eval-a 两项，也在 Audit 退回后修正了 eval-b 的 AI 项，但 eval-b 仍把“完成交付”当作
创新能力证据打75，Audit 未发现。已将 Skill 提升至候选 version 8，要求渲染前逐人核对
七个维度与原始来源；创新须有具体改进、验证和结果；Audit 必须逐项检查证据与分数，并
在修改后重算受影响的总分、平均数和摘要。v22 还显示恢复场景没有刷新当前成员来源，导致
输入已变但沿用旧来源，并让 eval-b 的业务分数仍超出新案例范围。当前没有单独的来源摘要
查询接口，因此 Skill 和运行文档现要求恢复的每个 Consumer turn 先逐成员读取一次当前来源，
再按摘要复用未变化分析；同一 turn 不得重复成功读取。v23 还遭遇一次运行环境未注入 fixture
工具，以及评测恢复器读取未完成 case 时缺少 Audit 终态字段而崩溃。恢复器现有回归并能跳过
未完成记录。

v26 的报告日期变更场景已完成两轮 Consumer/Audit：最终报告采用 10 月20日触发日期，刷新
四位成员来源，重做日期和摘要变化的分析，完整重渲染主文档、附录和待审核摘要；没有发送
群消息。Audit 发现合成 `behavior-review` 虽称文化行为“有独立具体行为佐证”，但没有列出
事例，Agent因此把两项下调至60。v4 固定案例现为该场景补入三项文化行为的具体情境和结果，
同时保留创新及AI证据缺失，行为分数范围不变。该场景和其余案例须在新案例摘要下重跑；全部
通过前不发布。

v27 按新增的具体行为事实运行第一案例通过，第二案例仍失败：更新了主文档和发生变化的
eval-b附录，却没有更新日期变化所影响的eval-a及两份未评分成员附录。评测当时因缺少旧
附录对应的本轮写入回执抛出 `KeyError`，掩盖了完整性问题。候选 Skill version 9 现要求
每次渲染都写入/更新并完整读回当前 manifest 的全部附录，不因成员分析未变化而跳过；grader
现把缺回执记为失败检查，不再抛异常。恢复器也只复用同时满足 Audit approve 和 grader
passed 的案例，防止把“审核通过但正文不完整”的候选当作已接受结果。

v28 的 changed-source 修订 Consumer 返回的内层 production payload 缺少必需的
`evidence_refs`，严格解析器据此拒绝；原评测器当时抛异常，没有把失败输出保存下来。评测器
现在保留原始 envelope 和契约错误，同时仍将它判为失败，不放宽该字段要求。v29 通过了综合
和来源变化案例，但日期变更案例的 Audit 发现行为证据没有按人员归属：共用行为摘要被应用到
两位同名成员。v4 现在为两位成员提供各自绑定 userId 的行为事实，且保留创新与AI缺证；服务
fixture 按被读取的成员返回对应来源。该案例待重新通过 Consumer、Audit、评分和完整文档读回。

### v30–v32：输出字段契约问题与成员故障范围证据

v30 再次出现 Consumer 结果没有 `evidence_refs`，严格生产解析拒绝。复查发现该轮评测
把生产 purpose 对象编码在自由字符串中，CLI 的强制输出 schema 只约束外层字符串，
因此这个工具运行时没有机械约束内层字段；生产解析仍拒绝缺字段，不能把拒绝当成通过。
尝试将完整条件 schema 嵌入 CLI schema 后，v31 在模型执行前就因 strict schema 不支持
嵌套 `allOf` 而返回 400，确认该 CLI 传输层不能直接强制这份生产条件 schema。撤销该
评测方案，没有改解析器，也没有放宽 `evidence_refs`。现在通用 purpose schema 为此必填
字段增加明确说明：每个结果都要有该字段；没有适用引用时为空数组，权限重试需提供已核验
申请的引用。定向测试 **48 项通过**；已知发布摘要门禁仍因 `ceo-document-review` 的
approved digest 与 release manifest 不匹配而单独失败。

v32 使用上述 schema 说明重新进行完整原生四场景验收。`composite-coverage` 和
`recover-changed-source` 的 Consumer、Audit、评分、公式、全部报告/附录读回及通知提案
核查均通过。`recover-changed-date` 未通过：首次 Consumer 将来源结果的 `scope: member`
标签和“member-bound source unavailable”泛化诊断当作足够证明，在没有底层错误与隔离证据时
仍评分并写入更新版主文档和四份附录。Audit 正确退回。修订 Consumer 重读后确认仍无范围
证据，正确以 `failed` 停止，没有再写文档或发送通知；但首次已产生的内容仍留在 provider，
所以最终文档与完整审核要求未通过。失败原因是来源失败发生在首次发布之前没有被挡住。

此外，这个冻结案例本身也不支持它原先要求的“成员级失败可保留为未评分成员”通过结果：
模拟来源只有范围标签和泛化错误，Audit 无法核验证明故障只影响沈禾。因此案例输入与其预期
不一致。没有降低标准；Skill version 10 现要求只有同时具备具体底层错误和同轮 roster/其他
成员成功等可核查隔离证据，才能按成员级失败继续。单独的 `scope: member` 或
“member-bound”文本不够；证据不足时必须在评分、渲染、文档写入和通知前停止。合成夹具已补
入具体 userId 的当前周期 404、请求标识，以及同轮名册和其他成员读取成功的范围证据；这使
案例能在原有审核标准下检验“真正的成员级来源缺失”。v32 失败轨迹完整保留。下一轮需用
Skill v10 和修正后的固定来源事实重新运行四个场景，再决定是否为行为问题。

v33 按修正后的来源范围输入重跑时，在第一场 `composite-coverage` 被 grader 拒绝：两位同名经理的
“团队赋能能力”都被评分为65；Audit approve。追查当前 `dingtang-okr-review` 的明确标准后发现，
测试只提供了职责分工，缺少经理辅导成员独立解决问题的证据；对总监也缺少可复用能力建设证据，
所以固定案例要求该维度70–79并不成立。旧的“所有正式行为维度均达到当前职级标准”概括，不能替代
这些具体事实。此处是固定输入的证据缺口，grader 的分数范围没有放宽。v4 现在给总监增加了指导组
负责人轮流主持复盘、独立跟进依赖并由团队持续复用既有方法的当前周期证据；给经理增加每周辅导
工程师复盘、独立处理一线问题和新成员按清单独立部署的证据；均不包含超出职级的组织级效果。该
v33 失败结果保留。下一轮需在这些更准确的来源事实和 Skill v10 下重跑全部案例，复核评分和 Audit。

v34 在首场再次暴露审核缺项。Audit 要求把 eval-a 总监的组织协同80降到75，并让Consumer修订；但 eval-a 的团队赋能80同样没有超出职级标准的效果，Audit 首轮漏检。grader 因此仍拒绝；报告保留失败，不能将Audit approve视为验收通过。现有Skill虽然要求80+有超标案例并逐项检查七维，但针对“每一个80+都要举出超标效果”说明不够直接。候选 Skill version 12 明确：经理日常辅导、总监在本团队内复用方法可以证明符合岗位标准，但本身不证明超标；Audit 要逐项检查所有80+的范围与效果。

同轮二次Audit还发现 eval-b 四项领导力分数75、75、75、60的均值71.25被脚本显示为71.2。`report.py` 使用Python内置银行家舍入，未按当前验收口径通常四舍五入显示。已改为 Decimal `ROUND_HALF_UP`，并更新独立公式oracle和71.25边界测试。没有更改评分本身、权重、标准或一位小数精度。定向回归 **133 passed, 1 deselected**（唯一未执行项为已知的独立发布摘要门禁），Ruff 和 diff 检查通过。v34 完整结果保留；下一轮在 Skill 12、修正舍入脚本及当前四案例事实下重跑。

### v35：OKR 四场原生综合验收通过

v35 使用候选 Skill 12、`report.py` half-up 舍入和修正后的 v4 固定事实，从头运行四个场景，
没有复用旧 Consumer/Audit 输出。状态 **passed 4/4**，每场独立 Audit 均 approve，所有机械检查全绿：

- `composite-coverage`：同名成员区分、有效评分和两类未评分缺口通过；具体成员 404 的来源范围有同轮证据支持。
- `recover-changed-source`：只重做变化来源对应分析，其他有效分析按来源摘要复用；更新并读回全部当前 manifest 附录。
- `recover-changed-date`：按新触发日期刷新来源和受影响分析；成员级404范围通过审核，所有当前主/附录版本完整读回。
- `shared-source-auth-failure`：识别共享来源故障并以 `failed` 停止；没有成功报告或群消息提案。

每个目的输出都有必需的 `evidence_refs`。本轮检查到的报告、附录和群摘要均来自隔离模拟 provider；评测配置
明确 `external_executor_available=false`，没有真实文档发布、群通知或生产运行。v35 仅证明候选代码/Skill 的
原生合成验收通过。旧 391405 失败运行没有重放；真实自然运行与三份报告实际发布证据仍按生产回执单独核查。

### v36：暴露未变化分析被改写

v36 的第一场通过，但 `recover-changed-source` 未通过。完整轨迹显示 `prepare` 给未变化的 eval-a 标记
`analysis_reusable=true`；Consumer 仍重写 eval-a 的分析文件。Audit 退回的是报告摘要重复，未指出 eval-a 的证据或评分错误；Consumer 的修订又改写了该文件。grader 的 `unchanged_analysis_reused` 检查因此失败，最终分析、附录和摘要虽读回、Audit 最终 approve，仍不能判该场通过。根因是实际 Agent 没遵守已有的保留要求，不是 grader 误判；v36 失败完整保留。

Skill version 13 明确：`analysis_reusable=true` 时不得重写该成员文件；报告措辞、重复或排版反馈要修正在来源变化的成员分析或受影响的报告段落。只有 Audit 明确指出该成员证据或评分有误时才可改写，并继续逐字节保留其他可复用分析。

### v37：四场原生综合验收通过

v37 使用 Skill 13、当前 `report.py` 与 v4 固定事实，四场均由新 Consumer/Audit 运行完成，状态 **passed 4/4**，全部严格评分、成员身份、来源覆盖、审核、完整主文档与附录读回检查通过。特别是 `recover-changed-source` 中 eval-a 原样保留、eval-b 来源变化后重做；日期变化场按新日期重新分析；共享认证失败场在没有文档或通知提案的情况下终止。

本轮仍是隔离模拟 provider，`external_executor_available=false`，不等于生产文档或群消息发布。v37 结果保存在 `evals/scheduled_task_generic/okr_results.v37.json`；v36 失败也保留在 `okr_results.v36.json`。

### 当前生产发布与手动验收

生产只读核查确认服务提交 `f8fed0d9aaf6bc2d2f61fbef5f2d1be1d033182c`、活跃 runtime skill 配置 356，OKR managed revision 209；历史失败运行 391405 仍终态失败，没有重放。原来的发布快照指向旧提交/配置，且 OA 通用 Skill 清单摘要落后于实际已安装内容。现已根据生产安装文件、Audit 规则文件和数据库内活跃 managed revision 只读重建发布前置快照；清单 `old_sha256` 与安装读回一致，`new_sha256` 与仓库候选源一致。发布前置定向测试、OKR 综合测试、Consumer purpose 和报告脚本测试共 **137 passed**；Ruff 和 `git diff --check` 通过。

Skill 13 候选尚未部署；用户已授权部署后从 task6 正式调度入口启动一份新的手动验收。下一步走正常提交、推送和部署，再核实 PID、healthz、队列、History、不可变配置快照和 managed Skill revision，然后启动新运行。验收仍要求完整 Consumer/Audit 轨迹、所有报告文档实际读回及真实通知持久回执与 provider 查询。未达到这些条件前，不把合成验收记作生产结果，也不重放 391405 或任何已有外部回执的报告。

### v38–v49：补齐严格恢复与报告计算验收

v38–v48 的每轮失败结果均保留在对应 `okr_results.vNN.json` 中，没有覆盖旧证据。追踪后修正了以下实际问题和固定案例问题，评分与发布标准未下调：

- 报告摘要仍用 Python 浮点格式显示 75.25 为 75.2；最终分 `59.85` 也会因二进制浮点显示为 59.8。`report.py` 现在用 Decimal 计算加权最终分、系数及一位小数 half-up 显示，并用同一 Decimal 算法显示主文平均值与群摘要平均值。
- 恢复场景曾沿用旧成员源文件，未在本次执行重新读名册和全部成员。Skill version 14 起要求每轮先取得新源回执，再按当前 user ID、周期、结果及摘要决定是否复用分析。
- Audit 只要求改报告和附录时，Consumer 曾重写未变化成员分析。Skill version 15 要求 `analysis_reusable=true` 的文件在报告修订期间保持原字节，除非 Audit 明确指出该成员的证据或分数有错。
- 两位同名成员的报告级进展曾没标 user ID；报告也曾把未读取的文档、知识库、听记和群聊列为本轮评分来源，并重复写覆盖限制/客户验收风险。Skill versions 16–18 现要求进展与风险按 `subject_label` 归属，只列本轮实际读过的来源，重复风险与覆盖限制要合并。
- 原生 Audit 发现 eval-a/eval-b 的行为来源材料不足以支持案例要求的岗位达标分。冻结的 v4 案例保持原样，新建 v5 补足可核验的当前周期事实；没有改变职级评分范围、创新/AI缺证要求或超标准行为门槛。v5 的评分 grader 还会拒绝非 0/20/40/60/80/100 的业务 KR 基础分。
- 单成员 404 的原始合成回执不足以单独证明授权成功。模拟 provider 现提供带主体、请求 ID、授权状态、HTTP 状态和错误码的真实回执；单成员已授权 404 与共享认证失败 503 保持可区分，并由第四场验证共享故障阻止发布。

v49 从头运行四场 native Consumer/Audit，**4/4 passed**；每场的终态均为 Audit approve 且评分、来源、分析恢复、文档完整读回、通知提案和机械检查通过。来源变化场验证 eval-a 字节保持不变、eval-b 重做；日期变化场按新日期刷新；共享认证失败场没有报告写入或群消息提案。结果在 `evals/scheduled_task_generic/okr_results.v49.json`，冻结输入为 `okr_cases.v5.json`。这些仍是隔离模拟 provider 的原生合成结果，`external_executor_available=false`，不代表生产发布或真实通知。

v49 配套定向测试 **125 passed**，Ruff 与 `git diff --check` 通过。集成最新主线后发现发布基线测试仍引用旧安装哈希：OA Skill 测试写死 revision 150 的哈希，但生产数据库显示当前 attendance Skill 为 revision 220、哈希 `6fd80713…`；dingtalk-oa-approval 当前 revision 221、哈希 `fc9a16dc…`。OKR Skill 和脚本的候选源摘要也需随本次文件变化更新。以上是发布摘要与测试基线过期，不是报告业务验收失败。已将前置快照、发布清单和 attendance 摘要断言更新为生产实际哈希，并更新 OKR Skill/script 的候选哈希；相关三文件回归现为 **152 passed**。

当前生产读取为主线提交 `5eec88650`、PID `71765`、healthz 正常；task6 仍是配置 v19、不可变快照 198、managed Skill revision 209，旧失败运行 391405 未重放。本次 v49 Skill/script 候选仍未部署，尚未创建新的手动运行。提交并部署后，先核对 PID、healthz、队列、Attention、History、task6 新快照及 managed Skill revision/hash；确认执行绑定新版本后，才从正式 task6 手动入口创建一条新运行。新运行须以完整 Consumer/Audit 轨迹、终态、主文档和所有附录的 provider 完整读回、真实通知持久回执及 provider 查询为验收证据。绝不重放 391405 或任何已有外部回执的运行。

## 2026-10-22 task6 手动执行与脚本审查阻塞

正式入口创建的唯一当前 task6 手动运行是 **392030**，不可变快照绑定 task 配置
v20 和 `ceo-weekly-okr-report` managed revision 222。前两次 Consumer 尝试
392063/392065 因 Dingteam 专用登录态过期而停止；之后 Consumer 392092 仍报告同一认证
错误。浏览器 Cookie 快照已按现有 `sync-chrome-cookies` 命令刷新，不曾绕过登录或读写
用户凭据。随后系统自动对同一执行身份启动 Consumer 392114；该轮重新读了当前
18 人名册和全部成员资料，18/18 请求完成，15 份为通过验证的实时季度 OKR，3 份明确
返回本季度目标尚未建立。没有把缺失目标伪装成零分，也没有把它们当成读取失败。

392114 在运行 `ceo-weekly-okr-report` 的 `prepare` 脚本前，以
`operation_unavailable` 结束。完整 Consumer 轨迹显示：工具拒绝原因是
`Consumer Skill script is not reviewed`；运行没有产生评分分析、报告正文、在线文档
写入或通知提案，故也没有真实外部发送回执。生产源仓库中的 `report.py` 与已安装
Skill 文件字节一致，但 `consumer-scripts.json` 仍固定旧脚本树摘要
`94cb579f…`，而源码与安装包当前摘要都为 `a982ea36…`。这是审查清单未随已审核脚本
变更更新的实际运行缺陷，不是验收器错误或业务材料标准问题。

修复将 `consumer-scripts.json` 摘要同步到已安装和源代码完全相同的脚本树，并增加
回归：旧摘要时测试先失败；更新后 `test_consumer_skill_script_boundary.py`、
`test_weekly_okr_report.py` 和 `test_scheduled_okr_agent_eval.py` 共 **130 passed**。
脚本入口列表、脚本内容、评分、审核、来源覆盖和投递条件都未改变。修复提交与部署后，
只有在新的 task6 运行实际通过 Consumer/Audit、报告完整读回、通知持久回执及
provider 查询后，才会将 OKR 新版自然/手动结果记为通过；392030 的失败历史保留不改。

### 392187：重试重复读取导致分析始终未开始

task6 run **392187** 使用 task 配置 v20、managed Skill revision 222，派发到 reply task **416838**。Consumer attempt 392206、392275、392325 均在一次尝试内重新读取完整 18 人名册来源（15 人有 Q4 数据，3 人 `goals_missing`），重新运行 `prepare` 后只读到第一位成员的材料或评分 schema，随后以可重试的 `analysis_incomplete` 结束。attempt 392246 单独因 Codex provider 用量限制失败。三次 `analysis_incomplete` 轨迹都没有写出成员分析文件；该定时运行最终没有报告写入、通知提案或通知回执。

这是实际执行行为缺陷，不是评测问题或 OKR 来源故障。完整轨迹显示，Skill 把“新定时执行必须取新快照”误写成“每次 Consumer 重试都必须重读全部来源”。串行读取 18 人耗时约 10–12 分钟，重试便反复耗尽执行时间，评分分析没有起步。无正文证据见 `evals/scheduled_task_generic/okr-same-run-resume-evidence.json`。

修订后的 Skill 以 `scheduled_run_id` 区分新执行与重试：新定时运行仍须重新读取名册和所有成员；同一运行的 Consumer 重试、恢复和 Audit 修订复用该运行内已验证的来源回执，只补读缺失或失败项，并保留所有有效分析。评分、证据覆盖、Audit、文档完整读回和真实投递标准均未改变。更新了回归断言，定向测试 `tests/test_scheduled_okr_agent_eval.py`、`tests/test_weekly_okr_report.py` 和 `tests/test_consumer_skill_script_boundary.py` 为 **130 passed**；发布器与同一验收断言复测 **68 passed**。Ruff、`git diff --check` 与 26 项发布文件摘要 preflight 均通过。

此 Skill 修复尚未部署，也未创建新的运行。下一步提交并按正式流程部署；确认新配置快照绑定新 managed Skill revision 后，再创建一条 task6 手动验收运行。旧运行 392187 保持失败历史，不重放且不补发；新版运行仍需完整 Consumer/Audit、报告文档读回、持久通知回执和 provider 查询才能记作实际发布通过。

发布前置哈希已按只读读回刷新：生产代码仍为 `25eea1dc`，healthz 正常；运行配置为 361，task6 正在使用的旧 Skill revision 为 222，安装 Skill 摘要为 `f2781a35…`。修复将 Skill metadata 版本提升至 19。候选发布清单与哈希前置现可对齐当前安装文件；部署后仍要核实新增 revision 和新 task 配置快照。

### Skill v19 发布读回与 392433 分派结果

`d1652acd42d3a4e514a8107101c651f4c973950d` 已经两阶段正式部署（服务代码及 Consumer/Skill 合约发布）。生产读回：PID 60441、healthz 正常、14 项定时任务仍在；active runtime Skill 配置 362；task6 配置 v21，managed revision 223，安装 Skill SHA-256 与候选完全一致。task6 仍是洛杉矶周日 03:00。

部署后唯一创建的新验收 run **392433** 的不可变快照正确绑定 v21/revision223，但在 Consumer 启动前被分派器标记为 `skipped`：当时 `codex_oauth`、`qwen_gpu4`、`claude_oauth` 快照过期，`codex_api` 快照不健康。没有 Consumer/Audit trajectory、报告文档写入或通知回执，因此这不是新版 Agent 行为结果，也不能记为通过。稍后的运行时探测读回中，codex OAuth、Claude OAuth 和 Qwen 已恢复健康；Codex API 仍因 `codex_provider_overloaded` 不健康。健康快照恢复不能把 392433 的终态改成成功。

按验收约束，不重放已 `skipped` 的报告，也不另建手动运行。新版 OKR 来源快照重试逻辑尚未取得真实 Consumer 证据；继续观察下一个正常 task6 调度 **2026-10-25 10:00 UTC（洛杉矶周日 03:00）**。该自然运行需实际使用 v21/revision223，并通过完整 Consumer/Audit、文档完整读回及真实通知持久回执/provider 查询，才能证明实际发布通过。

### 睡眠/路由恢复补跑根因与修复

对 392433 的完整分派记录确认：此次运行在 Consumer 启动前因全部 Runtime 快照暂不可用而成为 `skipped`，期间没有文档写入或通知。该记录是历史终态，保持不变且不会重放。进一步追查调度器发现同类恢复缺陷：临时 provider 不可达时，Scheduler 也会把到期计划写成永久 `skipped` 并推进下一触发，导致主机从睡眠恢复后路由尚未就绪的情况错过补跑机会。

修复后，两个暂时不可用状态的回归均通过，`tests/test_agent_cron_scheduler.py` **28 passed**；改动文件 Ruff 检查与 `git diff --check` 通过。修复经 PR #241 合并为 `036b14624cddbcff04098ebec7697644f95370c0`，正式部署输出 `deployed b0839448 -> 036b1462`。

生产读回：PID **65404**，`/healthz` HTTP 200；队列 **active 1 / queued 152**，Attention **19**，History **37,179** 条；14 项定时任务仍在。task6 为 v21、洛杉矶周日 03:00、下次 **2026-10-25 10:00 UTC**；task13 为 v43、洛杉矶周六 12:00、下次 **2026-10-24 19:00 UTC**；task14 为 v49、洛杉矶每天 06:00、下次 **2026-10-22 13:00 UTC**。配置与触发时间保持原值。

GitHub Quality 检查在运行测试前被全仓 Ruff 阶段挡住，报告 14 处旧 lint 错误，位于本 PR 未改动的 5 个测试文件；Vercel 状态因预览部署额度不足失败。代码相关的调度定向回归、改动文件 Ruff 与差异检查已通过。未修补无关文件，也未降低任何验收门槛。新版 task6 仍只观察 2026-10-25 10:00 UTC 的自然触发，不重放 392433。
## 2026-10-23 OKR 综合验收修复续记

原生综合验收 `okr_results.v69.json` 四场 **4/4 passed**，覆盖完整报告、同周期来源变化恢复、报告日期变化恢复和共享认证失败保护。四场使用隔离 mock provider，没有连接真实外部执行器，不能代替生产发布证据。更早的 v50–v68 失败结果均保留供根因追查。

v50–v68 轨迹定位出实际报告问题：固定渲染器曾把没有来源日期的结果放进“本周重点进展”；缺口区域把内部状态码及原始英文诊断写入中文报告；日期变化恢复会沿用旧标题并写入新日期正文；修订时模型曾改写来源未变成员分析，或重复索要已核验的验收证据。`report.py` 现以“已核实进展”和报告日表述报告，把缺口转换为中文业务说明，并保留 404 不能证明目标是否存在的边界。Skill 现要求来源文件只由 `prepare` 生成、可复用分析逐字节保留、日期变化使用新标题，并只提出当前确实缺少的下一步证据。合成 Audit 提示澄清 mock 审核不会执行对外动作；gap grader 核对可读状态、成员身份和关键事实，不要求报告展示内部状态码。

定向测试 `tests/test_weekly_okr_report.py` 与 `tests/test_scheduled_okr_agent_eval.py` 为 **126 passed**；Consumer runtime/release 子集 **8 passed**；改动文件 Ruff 与 `git diff --check` 通过。完整 release-manifest 固定快照测试尚未通过：另一项 active daily-report 认领正在修改同一发布清单，其 `old_sha256` 当前为 32d24bb5，而本机验证记录中的 installed/managed SHA 为 45e10866。`consumer-scripts.json` 也仍由 `codex-okr-skill-script-review-digest-20261022` 认领；OKR 脚本修改后预期脚本树摘要为 `59a125b5…`，尚未更新。没有把这些并发清单差异写成通过，也没有部署或触发 task6。用户已授权部署后正式手动触发；需要共享发布哈希认领收敛、相关发布测试通过后，再按正式流程发布、读取生产配置与 History，并创建新的 task6 手动验收运行。

### task6 run 394267 真实失败核查

生产 History 显示手动 run **394267**（task6 v23 / managed Skill revision223）派发到 reply task **416922**，终态 `failed`。四次 Consumer 尝试（runtime run **394476、394493、394496、394499**）全部以 `analysis_incomplete` 结束；第一轮已读 18 人名册、15 人有效 Q4 源和 3 项 `goals_missing`，`prepare` 成功生成 source/plan/schema，但没有保存任何成员分析，随后没有 Audit、文档写入或通知。最终 History 标记 `agent_reported_failure (analysis_incomplete)`，`what_happened.reached_the_outside_world=false`，没有外部副作用。

完整 trajectory 中，第一轮尝试读取一个未配置的本地绩效制度文件，工具返回路径不存在；随后虽读了 schema 并查阅历史记忆，仍未继续分析。后续重试又读取了同一批成员源，最终都停在未完成分析。根因是 Agent 把未找到的补充制度材料当成了分析阻断条件，而本任务 Skill 和 `$dingtang-okr-review` 已有足够的正式评分口径。该次失败是实际 Consumer 行为问题，不是评测误报或数据源故障。

已在 Skill 中明确：不要求额外本地制度文件；如找到的补充材料无法读取，应把它记录为证据限制，继续使用已声明的评分口径，不阻止分析或发布。没有降低 0/20/40/60/80/100 评分档、证据要求、Audit、完整读回或通知门槛。Skill 回归断言加入后，`tests/test_weekly_okr_report.py` 与 `tests/test_scheduled_okr_agent_eval.py` 定向套件为 **126 passed**；改动文件 Ruff 与 `git diff --check` 通过。原生综合验收 `okr_results.v70.json` **4/4 passed**，覆盖完整报告、来源变化恢复、日期变化恢复及共享来源认证失败保护；它仍未模拟线上缺失本地制度文件的具体调用，根因修复需由下一条真实 task6 运行验证。此修复未部署；不重放 394267，待发布门槛收敛后创建一条新的 task6 手动验收运行。

### 发布门禁复核（2026-10-23）

修复提交 `0b226b55e` 合并最新 `origin/main` 后，重新运行定向测试：报告/综合评测 **126 passed**，Agent 工具说明 **8 passed**，改动文件 Ruff 与差异检查通过。发布专项 `tests/test_consumer_skill_script_boundary.py` 为 **4 passed / 1 failed**；失败项 `test_okr_report_prepare_script_matches_reviewed_package` 明确指出 OKR Skill 的 `consumer-scripts.json` 仍固定旧脚本树摘要，现有 `scripts/report.py` 改动尚未纳入已审核摘要。全局 pinned-release 测试另因日报行的 `old_sha256=32d24bb5…` 与已核实 `installed_sha256=45e10866…` 不一致而失败。两处均是发布清单未收敛，不代表降低或绕过 Skill 脚本审核；对应文件仍由既有 active claim 认领。尚未推送、部署或创建新 task6 运行。

**08:07 UTC 生产复核与摘要收敛：**只读 API 和安装文件读回确认 production 为 `b07bd9877b78a8d0ab1cc616485671d44918ae37`，healthz 200，runtime Skill config 368；task14 当前 managed revision 224 / SHA `32d24bb5…`，task6 当前 revision 235 / SHA `d7584ca0…`，task6 最近仍是已失败的 run394981。实际 OKR Skill 安装摘要已是 `d7584ca0…`，`scripts/report.py` 已是 `119a249d…`，原 manifest 却把它们分别登记为 `18b23c5c…` 和 `ab61de2c…`。日报已安装及 managed 摘要为 `32d24bb5…`，其内容摘要没有变；前置快照的 revision ID 203 已过期，应为 224。已在候选分支将旧安装摘要、旧 managed 摘要和对应前置版本按线上值收敛，候选 OKR Skill 新摘要仍为 `49ba4b0b…`；没有改 Skill 内容、评分标准或投递门槛。合并当前 main 后，OA 两项已审阅 Skill 的候选摘要也同步到了匹配的前置记录，未改其正文。发布专项与固定快照检查 **33 passed**，严格 `_load_file_plan` 对生产安装目标的 **26 个资产全部通过**；`git diff --check` 通过。PR 原 Quality 仍在跑旧 head，推送合并后的提交后需等待新 head 检查。随后另一项 mainline 发布尝试在 Skill 发布预检处失败，错误为 `Consumer system configured Skill has a different revision: ceo-weekly-okr-report`。它使用的 mainline 清单仍把 task6 当前 managed revision 235（SHA `d7584ca0…`）写作旧值 `18b23c5c…`；没有产生 Skill 文件写入。失败后 production 仍为提交 `b07bd9877b78a8d0ab1cc616485671d44918ae37`、PID 60615、healthz 200、runtime Skill config 368；task6/13/14 配置及最新 run ID 未变，也没有报告或外部副作用。该错误证实旧 managed 摘要是实际发布阻断；本候选分支已将旧 managed 摘要更新为线上 `d7584ca0…`。

### v71–v76：修复后原生综合验收通过

v71 首次复现了实际工作流违规：Consumer 收到 Audit 修订后直接覆写 `summary.md`、`main.md` 和 `full.md`，Audit 仍批准；独立渲染字节比对正确拒绝该候选。Skill 现将“改分析输入—重新 render—更新文档—完整读回”列为审核退回后的明确步骤，并明确禁止把生成文件当作修订输入。v72–v73 又暴露状态码泄露：报告脚本将成员 404 写入正文，而原评测器错误要求正文出现 404。脚本改用普通业务描述，评测器要求保留缺口原因、禁止泄露状态码；v73 轨迹同时确认 Consumer 也曾把内部状态码写进分析风险。

v74–v75 发现合成 Audit 没收到生产审核上下文中的上一轮退回意见，造成反馈来回漂移；合成 Audit 现携带上一轮 review，仍须独立核对当前候选。v75 还发现 Consumer 曾因业务 KR 未完成，把独立来源明确标为“达到经理标准”的战略行为维度打低；Skill 现明确业务 KR 与领导力/文化维度独立评分，低于标准分必须有该维度自身的反向证据。以上修正均保留评分、来源覆盖、Audit、完整读回和通知要求。

最新原生验收结果为 [v76 body-free readback](../evals/scheduled_task_generic/okr-native-v76-readback.json)，完整合成轨迹保存在 [okr_results.v76.json](../evals/scheduled_task_generic/okr_results.v76.json)：**4/4 场景通过**，每场最终 Audit `approve`，所有机械检查通过；包括 canonical render、完整文档读回、摘要逐字一致、同周期来源更新、跨日期快照和共享来源认证失败保护。合成 provider 不具备外部执行器；这证明代码/Skill/Agent 综合验收通过，不证明生产发布或真实群消息送达。v71–v75 失败原样保留，不用后续结果覆盖。

固定评测对照使用同一模型、CLI、冻结 case hash 和 grader hash：v75 的 `fresh-source-snapshot` 在 Audit approve 后因 `behavioral_scores_match_evidence` 失败，eval-b 战略能力为60；v76 同场景 Audit approve、全检查通过，分数为70，与来源中“达到经理岗位标准”的明确证据一致。没有改变 case 事实、评分范围或 grader 要求。

v76 后的定向测试为 `tests/test_weekly_okr_report.py` 与 `tests/test_scheduled_okr_agent_eval.py` **136 passed**，Consumer Skill 脚本边界 **5 passed**，完整 `tests/test_consumer_system_release.py` **27 passed**；改动文件 Ruff 与 `git diff --check` 通过。共享 Skill/脚本摘要已同步到 release manifest、脚本审核清单及匹配的生产安装/managed 旧摘要前置文件。production 尚未部署，也尚未创建 run394267 的替代运行。

### PR #277 的远端检查结果

PR #277 已打开，固定 v75/v76 case、模型/CLI 和 grader 对照及本地回归均附在 PR/无正文证据中。GitHub Quality 在 `npm test` 的首步全仓 Ruff lint 中失败，报 **16 项**、位于 7 个测试文件；逐个确认这 7 个文件与 `origin/main` 字节相同，单独在当前代码和这些原样文件上复现同一 lint 错误。后续 npm、workbench 和浏览器步骤因此未运行。Vercel 预览因账号额度限制失败，平台提示 24 小时后可重试。改动文件 Ruff、OKR 定向测试、Consumer 脚本审核测试及 release-manifest 测试均通过；没有为清绿而改动无关测试、降低门槛或跳过 OKR 验收。production 尚未部署；需先处理或获准接受上述远端基线阻塞，再继续正式合并/部署和新 task6 运行。

### 2026-10-23 18 人空搜索验收复核

匿名冻结案例 `empty-search-18-managers-incremental-analysis`：18 人中 15 人有 live OKR、3 人成功读取但目标列表为空；15 次独立文档搜索均无匹配。v82 完整轨迹发现，Consumer 写入第 1 位成员分析后先读取了第 2 位成员 source/schema，之后才读回第 1 位；Audit 仍批准候选。根因是 Skill 同时要求逐人读回和 prepare 后预读所有成员材料。现已改为按名册逐人延迟读取，grader 也会拦查下一位的 source/schema、搜索是否早于当前分析读回。

修订后 [v83 合成验收](../evals/scheduled_task_generic/okr_results.v83.json) 通过：15/15 成员顺序断言通过、3 位无目标成员保持未评分；最终 Audit approve，2 轮修订后完整报告、provider 读回和摘要候选均通过。全部使用隔离合成数据和工具，没有真实报告重放或外发。`tests/test_scheduled_okr_agent_eval.py tests/test_consumer_system_release.py` 为 **81 passed**，Ruff 与差异检查通过。Skill 发布摘要和候选摘要前置条件已同步；生产发布待 PR 合并后按流程执行。
