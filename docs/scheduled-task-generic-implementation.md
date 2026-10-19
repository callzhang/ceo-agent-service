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

固定七案例的严格注释验收未全部通过：最终首轮基线/候选均为5/7，一轮模拟 Audit/修订后为5/7与4/7。周报移除服务发布门槛的迁移检查全程通过。失败涉及保留/排除身份列表及评测标签与正式 Skill 结果名的区别；这些是实际模型输出，不能改标为通过，也不能当成真实报告或生产 Audit 的验收结果。完整原始注释、失败历史、字段定义版本和有界反馈见上述验收记录。

## 发布核查

使用 `python -m app.deploy --publish-consumer-system-contracts`，由部署器等待静默、备份、快进生产、发布 Skill/配置、重启和读回。发布结果、生产版本、PID、healthz、队列、Attention、History、14 项配置以实际核查补充。已有完成报告不重放。
