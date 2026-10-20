# OKR 综合 Agent 验收与线上报告核查

## 状态与证据边界

2026-10-20 UTC。原七案例的决策与独立正文/动作草稿验收已通过，详见
[原验收记录](scheduled-task-generic-agent-validation.md)。本记录补充 OKR 的完整
Skill 工作流、恢复、部分来源失败和真实报告读回。四场景原生验收仍在执行，
最终结果不得以代码测试或独立 Audit 的 approve 替代。

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
  历史代码与结果保存在先前提交中。该最终运行仍在执行。

每次修改前核对实际输入、工具返回与模型结果。所有 failed JSON 留存，未用反复
抽样掩盖失败。Codex 内置资源目录查询仅记录为 native_inventory_calls；所有真正
provider 工具必须属于唯一合成服务器，外部同名工具仍被回归拒绝。

## 定向测试

最新一次相关运行：`test_scheduled_okr_agent_eval.py`、`test_weekly_okr_report.py`、
`test_scheduled_task_generic_eval.py` 共 **120 passed**。渲染两项回归与新增
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

| 新版报告 | 首次后续自然触发 UTC | 美国洛杉矶时间 |
| --- | --- | --- |
| CEO 日报 | 2026-10-20 13:00 | 10月20日 06:00 |
| CEO 管理周报 | 2026-10-24 19:00 | 10月24日 12:00 |
| OKR 管理周报 | 2026-10-25 10:00 | 10月25日 03:00 |

三个自然运行尚未到期。到期后须绑定不可变配置/Skill revision、实际 Consumer/Audit
轨迹、文档读回与所需发送回执核查；只读旧文档、健康检查或提前手动运行均不能替代。
