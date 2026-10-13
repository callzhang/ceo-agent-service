# 数据库副本：原生分支和其他任务入口补验（2026-10-12）

Derek 要求继续补齐此前尚未使用真实 Codex CLI 逐项验证的其他任务入口、审查退回/修订和失败恢复分支，并继续以数据库副本和发送替身隔离真实对外投递。

本轮七个主案例完成，另保留两个实际发生的业务收口分支和一次验收配置失败。Consumer、Audit、TaskAgent 与 Workbench 的模型输出全部来自实际 Codex CLI；没有脚本生成决定、伪造 native transcript 或把 provider 结果当作真实外部投递。本轮各副本新增执行回执合计 4 条，均由内存 provider 产生；真实发送为 0。

## 版本、数据和边界

- 隔离工作树：`/Users/derek/.codex/worktrees/database-redesign-plan/ceo-agent-service`；承接已提交的上一轮原生验收，不改生产 checkout。
- 被测 runtime 版本：`06c08020773978b613d3a94dc4b9642e4b0cc2ef`，本工作树的 app/ 与该 commit 一致。本次只修改验收脚本、回归检查、claim 和报告。
- CLI `0.154.0`，实际 `codex_oauth` / `gpt-5.6-luna`；原生认证和 `~/.codex` 保持原样。
- 新在线一致性源副本：471,347,200 字节；25,247 tasks、219,781 runs；Store 2026-10-12.1 / Email51。
- 源副本 SHA-256：`0420cfed8df10ebdaca7e5b884ae8c22517af4c62cb871761910d704386458c3`。每个候选数据库从该源文件独立复制；源文件只读共享，候选互不影响。
- 输入为本次合成材料。TaskAgent 的检索上下文为空测试集合，逻辑 session scope 使用独立随机测试身份；生产历史业务正文和既有 TaskAgent 会话没有作为模型输入。
- Consumer/Audit 使用真实角色 builder、runner、orchestrator、SystemExecutor、native handler 和 worker/dispatcher。消息 provider、上下文和通知为替身；不改模型结果或审查决定。
- TaskAgent 和 Workbench 使用实际 RoutedCodexExecution 和原生 adapter；单 OAuth 路线使用已有 bootstrap 路径，没有伪造 capability probe 或修改生产路由。验收调用关闭外部 MCP 和 shell；返回决定、业务应用、projection、运行事件及终态持久化走实际实现。
- 本轮证明合成资料下的这些存储/执行入口和分支；没有覆盖所有实际业务 Skill、所有邮件/会议/OA/OKR 来源组合、真实投递或所有未来错误。

## 逐项结果

| 案例 | 实测终态 | 新执行回执 | 结论 |
| --- | --- | --- | --- |
| 首次修订：测试续会话配置失效 | pending | 0 | 实际分支/问题保留 |
| 来源更新 → return → revision 1 → approve | done | 1 | 通过 |
| no_action | done | 0 | 通过 |
| CLI 启动失败与技术重试 | done | 1 | 通过 |
| 回执已保存后的收口中断与恢复 | done | 1 | 通过 |
| 定时目标缺证据 → needs_human | needs_human | 0 | 实际分支/问题保留 |
| 工作汇总 TaskAgent 业务候选 | done | 0 | 通过 |
| 工作台原生 turn | completed | 0 | 通过 |
| 任务约束矛盾 → reject → no_action/approve | done | 0 | 实际分支/问题保留 |
| 定时任务正向完整链路 | done | 1 | 通过 |

七个主案例的关键检查：

1. **来源变更修订**：R0 Consumer 读取两条记录，之后测试来源更新为三条；真实 Audit 发现旧候选与当前来源不一致并 return。Consumer 在原生续会话中读取新来源生成 R1，Audit approve，最终只执行修订后的 3 条/100 汇总。四个逻辑 run 的 revision 为 0/0/1/1，parent 链完整，旧候选/审查保留，批准 digest 与相应候选逐项相等。
2. **no_action**：真实 Consumer/Audit 通过后完成，业务 Attempt 为 skipped；发送和执行回执均为 0。
3. **技术启动失败**：测试仅在第一次真实 `codex exec` 加入无效参数，实际进程返回 2；不是伪造模型错误。失败 run/runtime attempt 保留，随后真实 Consumer/Audit 完成；技术 turn_attempt 增加，proposal_revision 保持 0，最终一个发送/回执。
4. **收口中断恢复**：数据库副本中一次性 SQLite trigger 使最终状态事务 abort。此前原生两个 Agent 已完成，外部替身回执已保存；业务 Attempt 插入回滚，任务仍 processing。移除测试 trigger 后，重新构造 Store/worker，仅从已持久化结果和回执收口为 done、完成输入精简；恢复构造器没有 model runner 或 executor，无法再次生成结果或发送。全程只有一个发送/回执。
5. **TaskAgent**：新工作输入领取后，真实 TaskAgent 生成来源充分但未指派的候选，应用为 business task 554、signal 785，domain run 350758 completed；按 projection_json 的 task_decisions 关联实际业务 ID，signal.source_ref 等于新输入来源。保存业务投影后，输入 done、payload_json={}、body hash/bytes 和来源保留。此路径按现有 TaskAgent 合同进行决策/本地语义校验/业务应用，没有新增 Consumer/Audit 角色。
6. **Workbench**：实际新 task/turn、client_request_id 幂等、ServiceWorkbenchRuntime/WorkbenchExecutor/RoutedCodexExecution 原生执行，回答中包含随机标记和 100；completed、4 个事件、原生 session 引用保存，重新打开 Store 可从原生来源读回最终正文。
7. **Scheduled**：真实 create config/run → trigger claim/dispatch → execution claim → Consumer/Audit → SystemExecutor 替身 → receipt → done。保存的配置/触发/执行 ID 关联不丢失，guard 完成后输入精简；重读终态不再发送。source_run_id=350758、scheduled_id=16、reply task=404362（各副本公共 ID 独立，不用整数 ID 判断生产污染）。

另外两个真实发生的业务分支没有隐藏为通过的正向发送：

- 首次定时资料只把目标写成测试群名称，Consumer 使用 scheduled-task-run 标识作为会话目标。Audit return；Consumer 修订为缺少精确身份的 needs_human，Audit approve 后进入 needs_human。provider=0、receipt=0，保留可恢复输入。此案例实际证明缺目标证据的完整人工收口。
- 第二次资料提供了精确目标，但任务末句“不要执行外部动作”与要求 SystemExecutor 替身执行的方案矛盾。真实 Audit reject，Consumer 移除发送方案，Audit approve/no_action，任务 done、provider=0、receipt=0。随后将测试要求准确写成“Agent 不直接投递，授权已替换 provider 的系统执行方案”，正向定时案例通过。没有改生产 prompt 或审查规则。

## 验收工具问题和真实错误边界

- 首次修订在 R1 读材料失败，停为 pending、provider=0。测试脚本的限制选项在 resume 之前且可能被后续角色配置覆盖。增加两个先失败后通过的回归，修正为在 prompt/session 之前最后追加限制参数。修正只属于验收脚本，生产续会话实现没有改动。
- 首次定时原生轨迹确实出现了本地只读会话检索，说明旧验收限制被后续角色工具列表覆盖；不能将早期调用表述成严格只开放两个读工具。新脚本最后生效的 MCP 限制已由回归验证。该案例没有调用任何发送 provider。
- 修订案例模型和持久化已成功后，报告查询引用了不属于 candidate_reviews 的字段；修正为通过该表真实 candidate_id 外键连接 review_candidates，再读 proposal_revision 和 digest。只补做读回，没有重复模型或发送。
- 工作汇总模型把标题归纳为“补齐验收来源链接”，未在 title 中保留测试标记。原验收按 title 搜标记的断言不正确；改按实际业务 projection 的 task_id/signal_id 和 source_ref 核对，业务结果与输入精简均成功。没有要求模型改变措辞来配合检查。
- 原生会话可能包含 Agent 自行纠正的工具调用错误。成功结论以最终审查、结果、回执和状态证明，不宣称所有原生调用全程无错。

## 原生引用

行区间为零起点、右端不含。runtime attempt 指向该轮实际原生范围；同一角色的内容修订可继续同一个 session。逻辑 run、parent/revision 和失败记录也已逐一核对。这里只保留引用，不保存第二份输入/工具输出/trajectory。

| 临时案例代号 | runtime attempt 公共 ID | session_id | 行区间 | 状态 |
| --- | --- | --- | --- | --- |
| gw6y8ak7 | 350759 | `01a133f8-221a-7e52-abfd-f5d059b5f17e` | [0, 38) | completed |
| gw6y8ak7 | 350761 | `01a133f8-6574-76c3-884c-298edee21975` | [0, 31) | completed |
| gw6y8ak7 | 350763 | `01a133f8-221a-7e52-abfd-f5d059b5f17e` | [38, 66) | failed |
| d3m9rcsy | 350759 | `01a133fb-d4dd-78b1-9be1-dee3bd1f4ada` | [0, 38) | completed |
| d3m9rcsy | 350761 | `01a133fc-aed2-7f71-84c7-d837000babc6` | [0, 31) | completed |
| d3m9rcsy | 350763 | `01a133fb-d4dd-78b1-9be1-dee3bd1f4ada` | [38, 64) | completed |
| d3m9rcsy | 350765 | `01a133fd-8f0b-7202-9f86-512b0b2bebfe` | [0, 29) | completed |
| oml1f1hm | 350759 | `01a133fd-ffc2-7a82-b368-4f7b40c587b4` | [0, 31) | completed |
| oml1f1hm | 350761 | `01a133fe-55ff-77a2-8eae-4672acd57ce8` | [0, 24) | completed |
| 3oy5xoii | 350759 | 无（真实 CLI 启动失败） | — | failed |
| 3oy5xoii | 350761 | `01a133fe-b4bf-7ca0-a569-790525f0efd0` | [0, 48) | completed |
| 3oy5xoii | 350763 | `01a133ff-7611-7842-bd56-05148ae773df` | [0, 35) | completed |
| bwm1m5x9 | 350759 | `01a13400-567b-7993-b856-8199ccd6a0a6` | [0, 52) | completed |
| bwm1m5x9 | 350761 | `01a13400-c1d0-7353-a637-1d31f17e5b00` | [0, 29) | completed |
| nk7_pnn8 | 350760 | `01a133ff-0591-7d51-a65a-3228fe342a55` | [0, 44) | completed |
| nk7_pnn8 | 350762 | `01a133ff-7703-70d0-8992-822a85f8acb7` | [0, 50) | completed |
| nk7_pnn8 | 350764 | `01a133ff-0591-7d51-a65a-3228fe342a55` | [44, 65) | completed |
| nk7_pnn8 | 350766 | `01a13400-b0ef-76a2-a26d-5c567eb06dcc` | [0, 72) | completed |
| 8nvyb0yp | 350759 | `01a13402-6077-7aa2-a834-acd41c3f4df3` | [0, 21) | completed |
| 0e3khduk | 350759 | `01a13402-b637-7a31-ae5d-fa375eddfc24` | [0, 15) | completed |
| agjlm3yl | 350760 | `01a13403-9c2e-7c80-9785-f24984b29ebd` | [0, 38) | completed |
| agjlm3yl | 350762 | `01a13404-6611-78a0-a405-375290adf151` | [0, 29) | completed |
| agjlm3yl | 350764 | `01a13403-9c2e-7c80-9785-f24984b29ebd` | [38, 53) | completed |
| agjlm3yl | 350766 | `01a13405-0a6b-7f63-82dd-35fc39c38a2a` | [0, 35) | completed |
| 068fnueh | 350760 | `01a13406-83bd-73f1-a0c0-2a09efd4065b` | [0, 36) | completed |
| 068fnueh | 350762 | `01a13406-d07e-77c1-ac45-d74ac59e499a` | [0, 37) | completed |

上述 21 个不同原生会话只保留在 `~/.codex`。每个非空引用都已核对文件存在、范围有效，并校验原始文件字节摘要；正文及工具返回没有复制到数据库或本文。

## 数据保护与生产读回

- 十个候选数据库逐个比较 131 张原有应用表：原有行和字段没有被删除或改写。所有 quick_check=ok，foreign_key_check 与源副本相同；原历史 runs 行190042 → 缺失 tasks404225 的 finding 没有新增、删除或修复。
- 新增 FTS 内部索引变化和 SQLite 分配计数器不作为业务原行比较对象。
- 所有来源文件 SHA-256 与一致性源副本相同；原生引用从实际文件核对，不使用合成 transcript。
- 生产只读核对：本轮全部任务标记 0 条、替身回执标记 0 条、上述 21 个 session_id 的 runs 0 条；healthz.ok=true。
- 最后生产 HEAD 为 `3e0d962487ada4d12c73e2fc43826439ea9ada50`。期间另一项工作完成 PR69 部署；与被测06c080的 app/ 差异只有 task_scanners.py 的 OA scheduled_consumer 冻结材料 payload 传递一行。此扫描入口没有由本次重跑，本次涉及的 runner、orchestrator、SystemExecutor、TaskAgent、scheduled dispatcher 和 Workbench 路径代码一致。本轮没有部署或重启。

## 针对性自动检查

- harness、orchestrator、feedback exhaustion、SystemExecutor、reviewed candidate、reply input reference、scheduled consumer、Workbench service runtime：**149 passed in 17.26s**。
- TaskAgent process_work_item：**17 passed, 222 deselected in 2.77s**。
- 合计 **166 passed**；两个新的验收参数回归先失败后通过，harness 共4项通过。
- 两个验收脚本和 harness Ruff 检查通过；未运行全量测试。
- 一个最初的自动检查命令使用了不存在的 feedback 测试文件名，因此未收集测试；修正为实际 test_agent_orchestrator_feedback_exhaustion.py 后完成上述149项检查。

## 可复用脚本与清理

`scripts/validate_native_copy_lifecycle.py` 保留上一轮 approve，并新增 revision/no_action/technical_retry/settlement_recovery；`scripts/validate_native_copy_additional.py` 使用各自真实入口验证 scheduled/work_summary/workbench。脚本会消耗原生模型配额；调用前须提供拥有的、完整性已验证的临时 SQLite 源副本。全部模型决定保持真实，外部 provider 为替身。

已在报告、生产隔离、源 hash 和十个副本无打开文件句柄核对后，删除本轮十个拥有的副本目录及六个针对性 pytest 临时目录。按 device/inode 去重计，清理文件共 5,388,379,397 字节；逐路径合计 9,630,504,197 字节（共享源硬链接不重复计算）。所有目标路径均已读回不存在。原生会话留在 Codex 自己的存储，既有生产备份不动。
