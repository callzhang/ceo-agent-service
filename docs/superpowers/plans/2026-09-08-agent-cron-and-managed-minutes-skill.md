# Agent Cron 与托管听记 Skill 实施计划

> **执行方式：** 使用 `subagent-driven-development`，严格按任务顺序执行。每项由新的实现子代理以 TDD 完成并提交，随后依次进行规格审查和代码质量审查；审查未通过不得进入下一项。

**目标：** 将 CEO Agent Service 的主动周期性业务统一为顶部“定时任务”，实现 `Cron + Skills + Runtime` 配置、可靠触发与历史，并把每日 AI 听记迁为绑定精确 revision 的 `ceo-minutes-sync` 托管 Skill；Consumer 队列检查统一为内部 Dispatcher，不暴露成 Cron 配置。

**架构：** `scheduled_tasks` 保存用户配置，`scheduled_task_runs` 只保存触发/分发快照。`AgentCronScheduler` 只计算未来触发、原子写入 run 并唤醒 Dispatcher；`ScheduledTaskQueueAdapter` 领取 run 后创建现有统一 Agent 生命周期可消费的业务输入，运行结果继续以 `execution_kind + execution_id` 指向事实来源。其他业务 Queue Adapter 保留各自事实表，只统一 claim、唤醒和分发，不建立万能业务队列表。

**技术栈：** Python 3.12、FastAPI、SQLite、Pydantic、`croniter`、React 19、TypeScript、React Router、Vitest/Testing Library。

## 不可变实施边界

- Cron 与 Skill 分属两个模块；Cron 只在顶部导航，Settings 不出现 Agent Cron。
- Connector 不保存周期、Prompt、Skill 或 Runtime；没有 Connector Cron。
- Consumer 队列轮询、投递、外部效果确认、Audit/feedback/revision 是内部机制。
- 不增加 system Cron、`trigger: cron`、微信复盘或补跑逻辑。
- Runtime 来自当前配置；不可用时跳过并提示，不静默 fallback。
- 托管 Skill 必须绑定精确 revision；创建新 revision 不改变已保存任务。
- 所有 Agent 执行继续遵守 `Consumer -> Audit -> feedback -> revision`，不增加 `discard`/`discarded`。
- 每个迁移项必须先验证新入口，再删除旧计时入口；不得长期双跑。

---

### Task 1：六段 Cron 与时区值对象

**文件：**

- Modify: `pyproject.toml`
- Create: `app/agent_cron/__init__.py`
- Create: `app/agent_cron/schedule.py`
- Create: `tests/test_agent_cron_schedule.py`

**步骤：**

- [ ] 先写失败测试，覆盖：六段秒级 Cron、每分钟、每天北京时间 20:00、周日 18:00、America/Los_Angeles DST 跳时/重时、非法五段/七段表达式、非法时区，以及结果严格大于传入时刻。
- [ ] 运行 `/Users/derek/miniforge3/bin/python -m pytest tests/test_agent_cron_schedule.py -q`，确认因模块缺失失败。
- [ ] 在 `pyproject.toml` 增加受约束的 `croniter` 依赖；实现以下窄接口：

```python
@dataclass(frozen=True)
class CronSchedule:
    expression: str
    timezone_name: str

    @classmethod
    def parse(cls, expression: str, timezone_name: str) -> "CronSchedule": ...
    def next_after(self, instant: datetime) -> datetime: ...
    def describe(self) -> str: ...
```

- [ ] 明确向 `croniter` 传递秒在首段的配置；所有持久化/比较时刻使用 UTC aware datetime，只有 Cron 计算和展示进入指定 `ZoneInfo`。
- [ ] 重跑 focused tests，确认通过；再运行 `/Users/derek/miniforge3/bin/python -m pytest tests/test_agent_cron_schedule.py tests/test_config.py -q`。
- [ ] 提交：`feat(cron): add six-field timezone schedule`。

### Task 2：Scheduled Task 持久化、快照与并发编辑

**文件：**

- Create: `app/agent_cron/models.py`
- Modify: `app/store.py`
- Modify: `tests/test_store.py`
- Create: `tests/test_scheduled_task_store.py`

**步骤：**

- [ ] 先写失败测试覆盖三张表、结构化 Skill refs、精确托管 revision 校验、`migration_key` 幂等、`version` 冲突、启停、删除后 run 历史保留、相同 `(scheduled_task_id, scheduled_for)` 去重、手动 run 独立事件 ID。
- [ ] 将以下表加入 `STORE_SCHEMA_TABLES`、列 manifest、索引 manifest 与 `_initialize()`：`scheduled_tasks`、`scheduled_task_skill_refs`、`scheduled_task_runs`。删除任务采用 `deleted_at` 软删除，避免破坏历史外键；列表默认排除已删除配置。
- [ ] 在 `models.py` 定义不可变 `ScheduledTask`、`ScheduledTaskSkillRef`、`ScheduledTaskRun`、`ScheduledTaskSnapshot`；run 快照保存 JSON 且在写入时校验可反序列化。
- [ ] 在 `AutoReplyStore` 增加窄方法：

```python
create_scheduled_task(...)
update_scheduled_task(task_id, *, expected_version, ...)
set_scheduled_task_enabled(task_id, *, enabled, expected_version)
delete_scheduled_task(task_id, *, expected_version)
get_scheduled_task(task_id, *, include_deleted=False)
list_scheduled_tasks(*, include_deleted=False)
create_scheduled_task_run(...)
claim_scheduled_task_run(...)
link_scheduled_task_run_execution(...)
finish_scheduled_task_dispatch(...)
list_scheduled_task_runs(task_id)
```

- [ ] SQL 更新必须在单事务中完成版本检查、Skill refs 替换和 `updated_at` 更新；不要仅从 Prompt 正则提取 Skill。
- [ ] 运行 focused tests，并运行 schema/managed-skill 回归：`python -m pytest tests/test_scheduled_task_store.py tests/test_store.py tests/test_managed_skills.py -q`。
- [ ] 提交：`feat(cron): persist scheduled tasks and trigger runs`。

### Task 3：运行方式与 Skill 选项解析

**文件：**

- Create: `app/agent_cron/options.py`
- Modify: `app/agent_runtime_config.py`
- Modify: `app/skill_files.py`
- Create: `tests/test_agent_cron_options.py`

**步骤：**

- [ ] 先写失败测试：只返回当前已配置 Runtime route；健康项可选、暂不可用项保留且带原因、未配置项不出现；托管 Skill 返回 revision 列表，operation Skill 返回来源与内容摘要；禁用或缺失精确 revision 被标记不可执行。
- [ ] 实现 `ScheduledTaskOptionService`，组合 `load_runtime_config(...)`、当前 route capability/health、managed Skill store 和 `SkillFileService`，输出稳定 DTO；不要新增 Runtime 枚举副本。
- [ ] Runtime 的保存标识使用 route name（如 `codex_oauth`），快照另存 runtime kind/model/options；执行时再次解析同一 route name，找不到或不健康即返回不可用，不选择其他 route。
- [ ] 运行 `python -m pytest tests/test_agent_cron_options.py tests/test_agent_runtime_config.py tests/test_managed_skills.py tests/test_skill_files.py -q`。
- [ ] 提交：`feat(cron): expose runtime and skill choices`。

### Task 4：Agent Cron 后端 API

**文件：**

- Create: `app/web_api/scheduled_tasks.py`
- Modify: `app/web_api/registration.py`
- Modify: `app/audit_web.py`
- Create: `tests/test_console_scheduled_tasks_api.py`

**步骤：**

- [ ] 先写 API 失败测试，覆盖列表、详情、创建、带 version 更新、冲突 409、启停、手动运行、删除、历史、options、非法 Cron/时区、非法 Runtime/Skill refs 和删除后的历史读取。
- [ ] 在独立注册函数中实现：

```text
GET/POST /api/console/scheduled-tasks
GET/PUT/DELETE /api/console/scheduled-tasks/{id}
POST /api/console/scheduled-tasks/{id}/run
POST /api/console/scheduled-tasks/{id}/enable
POST /api/console/scheduled-tasks/{id}/disable
GET /api/console/scheduled-tasks/{id}/runs
GET /api/console/scheduled-task-options
```

- [ ] API 输入使用 Pydantic 严格模型，拒绝多余字段；输出提供 `schedule_description`、`next_run_at`、最近 run 摘要和结构化 Skill refs。手动运行只创建 `trigger_kind=manual` 的 pending run，不修改正常 `next_run_at`。
- [ ] 在 `audit_web.create_app` 的现有 console route 装配点注入 store、runtime config/health 和唤醒回调；不要把 API 放回 Settings 路径。
- [ ] 运行 `python -m pytest tests/test_console_scheduled_tasks_api.py tests/test_console_managed_skills_api.py tests/test_audit_web.py -q`。
- [ ] 提交：`feat(cron): add scheduled task console API`。

### Task 5：Scheduler 的无补跑、重叠与能力失败语义

**文件：**

- Create: `app/agent_cron/scheduler.py`
- Modify: `app/store.py`
- Modify: `app/cli.py`
- Create: `tests/test_agent_cron_scheduler.py`
- Modify: `tests/test_cli.py`

**步骤：**

- [ ] 先写失败测试：首次启动只设定当前时间之后的触发点；停机跨过多个点不补跑；同一 planned instant 原子去重；上一轮非终态时新 run 为 `skipped`；手动 run 不移动计划；Runtime/managed revision 不可用时 skipped 且写 Attention；多个 scheduler 不重复触发。
- [ ] `AgentCronScheduler.start(now)` 对每个启用任务计算 `next_after(now)`，不枚举过去；`tick(now)` 只处理进程内记录的到期未来点，写 run 后立即计算下一个未来点。
- [ ] 通过 store 查询 `execution_kind + execution_id` 对应事实来源是否终态；状态判断放在 adapter/resolver，不在 run 表复制业务状态。
- [ ] Runtime/Skill 不可用使用稳定原因码，记录 run `skipped` 并进入现有 Attention 数据源；不创建业务执行，不 fallback。
- [ ] 在 `run_service` 加常驻 scheduler 组件和 `threading.Event` 唤醒入口；此时保留旧业务 producer 以便后续逐项迁移，但种子迁移尚未启用，避免双跑。
- [ ] 运行 `python -m pytest tests/test_agent_cron_scheduler.py tests/test_cli.py tests/test_history_actions.py -q`。
- [ ] 提交：`feat(cron): schedule future triggers without catch-up`。

### Task 6：统一内部 Dispatcher 协议和公平领取

**文件：**

- Create: `app/dispatcher/__init__.py`
- Create: `app/dispatcher/models.py`
- Create: `app/dispatcher/service.py`
- Create: `app/dispatcher/adapters.py`
- Modify: `app/cli.py`
- Create: `tests/test_consumer_dispatcher.py`

**步骤：**

- [ ] 先写失败测试：Scheduled/Reply/Meeting/Work Summary 四类 adapter 的 due/claim；原子单领；round-robin 公平；长任务提交到 worker pool 后 dispatcher 继续；空队列不写用户 run；lease 恢复和 execution generation；唤醒与有界等待。
- [ ] 定义统一信封，不复制业务 payload：

```python
@dataclass(frozen=True)
class DispatchEnvelope:
    adapter_name: str
    source_id: str
    available_at: datetime
    priority: int
    attempt: int
    generation: int
```

- [ ] `QueueAdapter` 只暴露 `metrics()`、`claim(now, owner, lease)`、`release(...)`；完成/失败写回各自事实表。`Dispatcher` round-robin adapter，并将信封提交给按 adapter 注册的 Consumer pool。
- [ ] 第一阶段用 adapter 包装现有 claim/consume 边界，不重写 DingTalk、WeChat、Meeting、Work Summary 业务 Consumer；旧 loop 中“扫描+执行”拆出一次执行函数供 worker pool 调用。
- [ ] Dispatcher 使用 `threading.Event.wait(timeout=...)` 作为跨进程/异常兜底，但该 timeout 不进入 UI 或 Settings。
- [ ] 运行 `python -m pytest tests/test_consumer_dispatcher.py tests/test_consumer_agent.py tests/test_meeting_alignment.py tests/wechat -q`。
- [ ] 提交：`feat(dispatcher): unify internal queue claiming`。

### Task 7：Scheduled Agent Consumer 接入统一 Agent 生命周期

**文件：**

- Create: `app/agent_cron/consumer.py`
- Create: `app/agent_cron/context.py`
- Modify: `app/agent_context.py`
- Modify: `app/agent_orchestrator.py`
- Modify: `app/agent_turn_runner.py`
- Modify: `app/store.py`
- Create: `tests/test_scheduled_agent_consumer.py`
- Modify: `tests/test_agent_orchestrator.py`
- Modify: `tests/test_agent_turn_runner.py`

**步骤：**

- [ ] 先写失败测试：claimed run 只创建一个执行事实；Prompt 使用快照描述；只加载结构化 Skill refs 与精确 managed revision；指定 Runtime route；Consumer/Audit/feedback/revision 全链路；完成后写 `execution_kind + execution_id`；不可恢复失败写 failed；重启恢复不重复外部执行。
- [ ] 新建 scheduled execution source（只作为现有 Agent run 的父事实，不复制 Agent 状态），或复用已存在且满足完整 Consumer/Audit 契约的通用 source；不得把 Workbench 的无 Audit turn 直接当作完成。
- [ ] `ScheduledAgentContextBuilder` 从 run 快照组装上下文和 Skill protocol；operation Skills 记录实际来源摘要，managed Skills 从指定 revision 内容加载，不读取“当前最新”。
- [ ] 将 runtime route 作为强约束传给 router；若已不可用，在真正启动 Consumer 前关闭为 skipped/Attention，不走 failover。
- [ ] 保留原始 Agent run、Audit run 和 revision parent；运行终态通过现有 orchestrator 解析，run 表只链接 source ID。
- [ ] 运行 focused tests，再运行 `python -m pytest tests/test_scheduled_agent_consumer.py tests/test_agent_orchestrator.py tests/test_agent_turn_runner.py tests/test_agent_context.py -q`。
- [ ] 提交：`feat(cron): execute scheduled agents through audit lifecycle`。

### Task 8：顶部“定时任务”页面和 API 客户端

**文件：**

- Create: `frontend/src/api/scheduledTasks.ts`
- Create: `frontend/src/api/scheduledTasks.test.ts`
- Create: `frontend/src/pages/ScheduledTasksPage.tsx`
- Create: `frontend/src/pages/ScheduledTasksPage.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/GlobalNav.tsx`
- Modify: `frontend/src/components/GlobalNav.test.tsx`
- Modify: `frontend/src/styles.css`

**步骤：**

- [ ] 先写失败测试：顶部导航存在“定时任务”且 active；列表/选择；创建编辑；version 冲突；启停；手动运行；删除确认；历史；Runtime 不可用原因；精确 managed revision；`$` Skill 搜索和结构化 refs 同步；加载/空/错误状态。
- [ ] API 客户端对所有响应做运行时字段校验，不接受缺失的 Skill ref、version 或 run 状态。
- [ ] 页面采用紧凑 master-detail：宽屏左侧任务列表、右侧单一编辑表单；小于断点时任务卡片在上、编辑器在下。主要操作只有“保存”，启停/运行/删除为次级操作。
- [ ] Composer 的 `$` 搜索复用交互方式而不共享业务状态；选中 Skill 时同时插入显示 token 和结构化 ref，删除 token/标签时保持一致，后端仍以 refs 为准。
- [ ] 运行 `npm --prefix frontend test -- --run frontend/src/api/scheduledTasks.test.ts frontend/src/pages/ScheduledTasksPage.test.tsx frontend/src/components/GlobalNav.test.tsx`。
- [ ] 提交：`feat(ui): add top-level scheduled tasks workspace`。

### Task 9：暗色模式与窄窗口视觉验收

**文件：**

- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/pages/ScheduledTasksPage.test.tsx`
- Create: `frontend/src/styles.dark-mode.test.ts`

**步骤：**

- [ ] 先写失败测试/静态契约，要求 `:root { color-scheme: light dark; }` 或等价显式 dark tokens，且暗色正文、辅助文字、输入边界、禁用/错误/状态色都来自 token，不保留浅色硬编码背景。
- [ ] 增加 `@media (prefers-color-scheme: dark)` 变量；逐项检查 Cron 页面新增样式，并修正其使用到的共享 status/button/input token。不要借机重做无关页面。
- [ ] 增加窄屏断点，验证无固定双栏最小宽度、操作可换行、Skill 下拉不越界、历史表转卡片或可读列表。
- [ ] 运行完整前端测试：`npm --prefix frontend test -- --run`，并运行 `npm run build:workbench`。
- [ ] 用真实浏览器分别在系统暗色宽屏与约 390px 窗口检查文本、边界、焦点、错误状态、下拉和操作区，保存截图证据。
- [ ] 提交：`fix(ui): make scheduled tasks clear in dark and narrow views`。

### Task 10：`ceo-minutes-sync` 托管 Skill 与幂等种子

**文件：**

- Create: `skills/ceo-minutes-sync/SKILL.md`
- Modify: `app/managed_skills.py`
- Create: `app/agent_cron/seeds.py`
- Modify: `tests/test_managed_skills.py`
- Create: `tests/test_agent_cron_seeds.py`

**步骤：**

- [ ] 按 writing-skills/skill-creator 契约先写 Skill 场景测试，覆盖：分页、摘要+全文、无权限只在需要时申请、内容游标、重新获得访问权限、内容新鲜度证据、分类计数、不得以进程/列表成功冒充同步成功。
- [ ] 编写 `SKILL.md`，frontmatter 只包含 Skill 元数据，不包含 Cron/trigger；引用 `dingtalk-minutes` 和条件性的 `dingtalk-minutes-access-request`。
- [ ] 将名称加入 repository managed baseline；验证导入保持 exact bytes、immutable revision 和已有自定义同名保护。
- [ ] `seed_scheduled_tasks(...)` 使用稳定 `migration_key=ceo-minutes-sync-daily-v1`，创建北京时间 `0 0 20 * * *`、默认健康 Runtime、精确 repository revision；若无健康 Runtime 则保留禁用种子和可见原因，绝不选未配置项。
- [ ] 运行 `python -m pytest tests/test_managed_skills.py tests/test_agent_cron_seeds.py tests/test_calendar_skill.py tests/e2e/test_task5_skill_semantics_live.py -q`。
- [ ] 提交：`feat(cron): manage and seed daily minutes sync`。

### Task 11：逐项迁移现有主动业务生产者

**文件：**

- Modify: `app/agent_cron/seeds.py`
- Modify: `app/cli.py`
- Modify: `app/config.py`
- Modify: `app/meeting_alignment.py`
- Modify: `app/wechat/service.py`
- Modify: `app/weekly_okr_report.py`
- Modify: `tests/test_agent_cron_seeds.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_meeting_alignment.py`
- Modify: `tests/wechat/test_service.py`
- Modify: `tests/test_weekly_okr_report.py`

**步骤：**

- [ ] 为每个迁移先写失败测试，再单独完成并验证：DingTalk 消息每分钟、会议每分钟、WeChat 每 15 秒、OA 每小时、工作来源每天、周日 18:00 OKR。Lark 不创建默认种子。
- [ ] 每个 seed 使用稳定 migration key、明确 Prompt、结构化 Skill refs 和当前默认健康 Runtime。会议 Prompt 固定首版资格为 `ended_at + 10 minutes`；不在 Connector/Settings 增加 settle。
- [ ] 先在测试和临时 DB 验证新 Cron 能创建正确 run/业务输入，再从 `run_service` components 删除对应 producer timing loop；保留 Consumer 的一次执行逻辑并由 Dispatcher 驱动。
- [ ] WeChat 只迁移现有 reader/producer 行为，保留联系人、群聊 `@`、`auto/confirm`、sender 授权，不新增复盘或摘要。Sender/delivery 仍为内部机制。
- [ ] `run_task_maintenance_loop` 拆分：工作来源和 weekly OKR 由 Cron 触发；错误恢复、投递确认等内部维护继续常驻且不外化为 Cron。
- [ ] 从用户可见 Settings 配置中移除 producer/meeting/OA/work/weekly 的频率字段；内部 Dispatcher 的等待参数仍为内部常量或服务配置，不出现在 UI。
- [ ] 每迁移一项运行其 focused tests 并检查没有旧/新双入口；全部完成后运行 `python -m pytest tests/test_cli.py tests/test_agent_cron_seeds.py tests/test_meeting_alignment.py tests/test_weekly_okr_report.py tests/wechat -q`。
- [ ] 提交：`refactor(cron): migrate proactive business checks`。

### Task 12：Dispatcher 状态、Attention 与文档

**文件：**

- Modify: `app/web_api/registration.py`
- Modify: `app/quality_gate.py`
- Modify: `frontend/src/pages/StatusPage.tsx`
- Modify: `frontend/src/pages/AttentionPage.tsx`
- Modify: `frontend/src/pages/StatusPage.test.tsx`
- Modify: `frontend/src/pages/AttentionPage.test.tsx`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: `README.md`

**步骤：**

- [ ] 先写失败测试，要求每个 adapter 显示 pending/oldest/running/latest_error；空队列不显示假 run；Runtime/Skill 不可用的 Cron 出现在 Attention；scheduler health 与业务结果分离。
- [ ] Status 只展示内部可观察性，不提供 consumer polling 配置。Attention 链接到对应 `/scheduled-tasks?id=...`。
- [ ] 更新架构图和运行机制：Cron 触发边界、Dispatcher adapters、事实来源、execution link、no catch-up、overlap skip、manual run、runtime no-fallback、exact Skill revision。
- [ ] README 运维段说明新 UI、种子幂等和旧循环移除；不得把 removed macOS tasks 重新引入服务。
- [ ] 运行 `python -m pytest tests/test_quality_gate.py tests/test_console_web_api.py -q` 以及相关前端页面测试。
- [ ] 提交：`docs(cron): document scheduler and dispatcher operations`。

### Task 13：全量、生产重启与真实读回

**文件：**

- Modify only for defects found during verification; each defect starts with a failing regression test and receives its own focused commit.

**步骤：**

- [ ] 运行 Python 全量：`/Users/derek/miniforge3/bin/python -m pytest -q`，记录 passed/skipped/deselected 数量。
- [ ] 运行前端全量与构建：`npm --prefix frontend test -- --run && npm run build:workbench`。
- [ ] 运行 `git diff --check`、检查工作树，只保留任务文件；将 `graphify-out/` 与 `app/graphify-out/` 移到废纸篓，不提交生成缓存。
- [ ] 由最终审查子代理检查完整 diff 对设计 Spec、架构生命周期、无 incidental safety logic、暗色/窄屏和迁移无双跑；修复后重新审查。
- [ ] 重启：`launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`；用 `launchctl print gui/$(id -u)/com.ceo-agent-service.main` 确认新 PID。
- [ ] 通过 live API 读回：七个种子各一份、Cron/时区/next run 正确、Settings 无 Agent Cron、options 来自运行配置、managed minutes revision 精确。
- [ ] 手动运行 AI 听记同步，验证真实“发现/成功/跳过/无权限/失败”内容和游标，而非仅 HTTP/进程成功；确认关联 Consumer/Audit/revision 和 History。
- [ ] 观察 DingTalk、Meeting、WeChat 新触发至少一个完整周期，确认 Dispatcher 分发到正确 Consumer 且旧 producer 不再重复运行。
- [ ] 检查数据库没有新增 unresolved `failed`、长期 `processing`、过期 lease 或未认领 pending；外部效果未知状态保持现有语义。
- [ ] 在当前 `http://localhost:62361/` 真实浏览器验收宽屏暗色与窄屏，并保存最终截图。
- [ ] 按 `finishing-a-development-branch` 提供四种收尾选择：本地合并、推送并建 PR、保留分支、丢弃分支；未经选择不自行合并。

## 计划自审清单

- [ ] 所有设计范围均有对应任务；所有非目标均未被实现任务绕过。
- [ ] 每项先有失败测试、最小实现、focused regression、独立提交。
- [ ] 数据类型在存储/API/前端一致：ID、version、UTC 时间、Runtime route、Skill source/revision。
- [ ] `scheduled_task_runs` 不成为第二套业务状态；execution link 可解析到事实来源。
- [ ] Scheduler、Dispatcher、Consumer、Audit、delivery 边界清晰。
- [ ] 迁移顺序保证没有长期双跑，也没有先删旧入口再验证新入口。
- [ ] 发布验收包含真实内容、真实浏览器、新 PID 和 backlog，而非只看测试或 HTTP 200。
