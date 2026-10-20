# 定时任务通用化实施与验收记录

## 范围

基于已部署 `be1ffd86f` 的隔离 worktree 实施，保留原开发树并发修改。14 项默认任务的迁移键、ID、周期、时区、启停及自定义内容沿用现有配置；OKR 从隐藏服务编排改为标准 Agent。业务要求属于 prompt/Skill，固定运算属于 Skill 脚本，服务保留来源身份、持久化、调度和接口机械约束。

## 实施分组

1. 通用运行上下文、明确时间范围的活动事实、产物分页、选定 Skill 脚本调用与显式文档写入。原有角色权限、Audit 和动作回执沿用。
2. 14 项声明配置和公共 seed；日报窗口/目录、周报会议定位/空白模板/三处更新/渲染进入 Skill；移除旧报告工具与服务端 publishable 检查。扫描类原本已通过 Consumer prompt/Skill 作业务判断，来源游标、去重、账号范围、Chrome 排除和审批执行保持原适配器。
3. OKR 接入模块只返回实时名单/成员来源；评分模型、身份绑定、公式和渲染迁至 managed ceo-weekly-okr-report。分析绑定来源摘要，来源变化重新分析。
4. 停服发布窗口原子更新 Skill 文件/revision/任务定义；发布前依据未完成执行的原始快照确认旧报告已结束，即使当前任务已改名或移除 Skill 引用也不会漏检，失败按精确版本回滚文件、引用和完整任务配置。

## 14 项任务的维护位置

任务定义统一在 `app/agent_cron/seeds.py`，服务命令绑定统一在 `app/cli.py::_service_command_registry`。下表中的适配器保留来源解析、游标、稳定身份和运行状态；业务判断在对应 Skill。

| ID | 任务 | 系统适配器或通用入口 | 业务维护入口 |
|---|---|---|---|
| 1 | 钉钉消息 | app/worker.py | ceo-message-triage |
| 2 | 会议结论 | app/meeting_alignment.py、meeting_alignment_source.py | ceo-meeting-work |
| 3 | 微信消息 | app/wechat/producer.py、scheduled_command.py | ceo-wechat；账号范围/发送模式仍是平台配置 |
| 4 | OA 审批 | app/task_scanners.py、oa_approval.py | dingtalk-oa-approval 与适用 Stardust 审批 Skill |
| 5 | 会议行动项 | app/task_scanners.py、task_report_scanner.py | ceo-meeting-work、ceo-work-tracking |
| 6 | OKR 管理周报 | app/weekly_okr_report.py 实时来源；普通 Agent | ceo-weekly-okr-report；scripts/report.py 精确计算/渲染 |
| 7 | 听记归档 | app/minutes_sync.py；确定性服务命令 | ceo-minutes-sync 运行说明 |
| 8 | 恢复扫描 | app/worker.py；共享来源去重 | ceo-message-triage、ceo-calendar-invite |
| 9 | 日历邀请 | app/worker.py；同一日历输入版本 | ceo-calendar-invite |
| 10 | 邮件分类 | app/email_scheduled_command.py；账号、来源及已有分类模型执行能力 | ceo-email-classifier 与账号分类配置 |
| 12 | 听记权限申请 | app/minutes_access.py；确定性服务命令 | dingtalk-minutes-access-request 运行说明 |
| 13 | CEO 管理周报 | 通用事实/产物/文档工具 | ceo-weekly-report；scripts/materials.py、render_dingtalk_jsonml.py |
| 14 | CEO 日报 | 通用事实/产物/文档工具 | ceo-daily-report；scripts/window.py、destination.py |
| 15 | Chrome 登录态 | app/chrome_cookie_snapshot.py；确定性服务命令 | 用途在任务说明；现有域名排除由适配器保留 |

Skill 的仓库源以 `ci/shared-skills` 为准；既有 operation Skill 仍按任务选定引用读取。没有为三项确定性命令增加 Agent 编排。

## 定向验证

以下为各次最终通过的定向运行，不作为互不重叠的总测试数累加：

| 范围 | 证据 |
|---|---|
| 声明任务默认配置 | 41 项首次通过，2 项过时 fixture 修正后通过；14 个迁移键/周期/时区与迁移前逐项一致 |
| 日报/材料/文档回执移植 | 35 项首次通过，1 项旧物理表 fixture 改公共写入 API 后通过；新增来源 ID 回归通过 |
| OKR 来源与公式 | 167 项来源/schema/评分定向测试通过；36 个评分/模型/渲染函数迁移时 AST 与原实现一致 |
| 通用能力和 OKR 来源摘要 | 97 项通过，包含 Audit 产物隔离、不可变读回、失效分析拒用 |
| Agent 工具/角色/CLI/调度 Consumer | 127 项通过 |
| 通用产物与 native 工具元数据 | 60 项通过 |
| 周报 Skill 脚本 | 最终 50 项通过，缺渲染字段机械失败，缺业务材料不触发服务发布门槛 |
| 发布与回滚 | 原 22 项通过，旧 manifest hash 更新后该项通过；新增资产/稀疏顺序/完整任务迁移及回滚通过 |
| 普通报告任务 | 新增普通 managed-check 任务经过真实隔离 Store 调度/领取，调用选定脚本生成正文、读取产物并提交通用文档接口；1 项通过，系统无需报告类别分支 |

合并 d6a1b024c 后的47项交叉回归及195项文档/回执/工具/OKR回归通过，最终发布/回滚整文件27项通过。

只使用隔离数据库和模拟 provider；未运行全量测试。实际 Agent 固定案例记录见 scheduled-task-generic-agent-validation.md。原生 Agent 决策对照与真实外部发布分别报告，不以代码测试代替。

先前 revision-2 的严格注释验收失败原样保留：首轮基线/候选均为5/7，一轮模拟 Audit/修订后为5/7与4/7。2026-10-19 继续核查确认评测接口有两处缺陷：将排除说明中的来源追踪当作最终保留内容，以及 Audit 缺少与 Consumer 相同的评测标签/schema。revision-3 统一三阶段评测传输契约并收紧 schema，原七案例、评分函数、两组 Skill、模型/runtime 设置均未改变。新候选首轮7/7且迁移通过；新基线首轮7/7但错误忽略旧服务门槛，仍记失败。一轮独立 Audit/修订后两组均7/7且迁移通过；首轮失败未覆盖。独立正文/生产动作草稿在相同输入下两组均7/7，真实输出解析通过；其中候选 issue-1 的正文读回误判修复使用原输出重评，未重新调用模型。13 项评测回归通过。生产服务与业务 Skill 无此次修订。完整新旧输出、回归和正文/动作探针见上述验收记录；外部报告发布仍须真实回执，未重放已完成任务。

## 发布核查

使用 `python -m app.deploy --publish-consumer-system-contracts`，由部署器等待静默、备份、快进生产、发布 Skill/配置、重启和读回。已有完成报告不重放。

2026-10-19 实际发布结果：

- 部署操作 `deploy-5c9b8d79-070c-40f5-b362-2911e2b3db7d`，状态 succeeded，无错误。生产从 `0b6728edb533415527f7ee66c39d2bd54c9bfc99` 更新为 `51781f1c2eee5a0f46ebf880fd8f66b422b67388`。发布前已保留并核对同期主线变更，合并后另有52项交叉测试通过。
- 主进程 PID `73291`，healthz 返回 ok，active runtime Skill config `348`。三项报告的 managed Skill 摘要与仓库一致；本次26项发布资产通过部署器加载校验。
- 14项任务的ID、迁移键、周期、时区、启停状态逐项一致；非报告任务prompt未改变。OKR任务6已由 service command 改为普通 codex_oauth Agent，三项报告使用新prompt/Skill；历史138份配置快照完整摘要仍为 `130f6afff6bfaca9fe2d72d84e54c9ff1c51b8a25f279a9c42f5f797c0311bfe`。
- 22:01 UTC读回：Attention由5降至4；History失败记录仍为同一8条，无新增ID。Reply队列的1项待处理来自既有 `manual_rerun_from_attempt:15698`；本次没有触发该重跑。其后已由dispatcher领取，独立于报告迁移。
- 22:03 UTC起，调度器和dispatcher有新心跳，自动产生钉钉/邮件/日历扫描385880、385881、385882，使用当前任务版本。后续周期按原有overlap规则跳过未完成的同任务执行；初次观察时扫描仍在运行，未将pending记作完成。随后385880完成，摘要 `produce-once queued=0`；385881完成，摘要 `email-message-check-once accounts=2 discovered=5 failures=0`；385882仍在执行。再次读取History仍为相同8条失败记录。
- 日报下一次为2026-10-20 13:00 UTC，管理周报为2026-10-24 19:00 UTC，OKR周报为2026-10-25 10:00 UTC。没有手动重放既有报告；本次未取得新版本报告的真实外部发布回执。

截至原七案例 revision-3，候选首轮及有界审核后的决策验收均已通过，独立正文/动作草稿也已通过；不能继续将其标为未通过。OKR 综合工作流验收与三份报告的真实发布证据另见 [OKR 综合验收与线上核查](scheduled-okr-agent-acceptance.md)。新版报告下一次自然执行仍待到期，历史实际发布不冒充新版自然验收。
