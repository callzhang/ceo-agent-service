# CEO Agent Service 工作区收敛与全页面 React 化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏现有 Agent/Audit 生命周期、外部动作和历史数据语义的前提下，收敛当前 React 控制台、Task/证据能力、Email 集成和运行发布状态，最终得到可从干净 Git checkout 构建、可真实浏览器验收、可由 launchd 稳定运行的版本。

**Architecture:** 保持现有 Python + FastAPI + SQLite + React/Vite 单体结构，不引入第二套页面渲染系统。React 负责全部普通业务页面和交互；FastAPI 负责结构化 JSON、SSE、持久化、业务命令、请求保护和 SPA 深链入口。Task/证据与 Email 保持各自领域边界，通过现有 `reply_task`、`agent_runs`、`reply_attempt` 和 Email classification/action-plan 契约交互，不能在页面层互相推断状态。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLite、launchd、React 19、TypeScript、Vite、React Router、Vitest、Testing Library、真实 Chromium/Playwright。

---

## 0. 当前状态、边界和执行规则

当前分支为 `codex/email-ceo-agent-integration`。共享 React 筛选器和图表组件已在提交 `6443aa6b` 中补齐；Task/证据能力已在 `687c1e8f` 中提交；Email worker、Email classifier 和退订相关工作已有多个独立提交。当前工作区仍可能出现并行 WIP，执行任何任务前必须重新运行：

```bash
git status --short
git diff --stat
git diff --cached --stat
git log --oneline --decorate -20
```

执行规则：

- 只提交本任务实际修改的文件；当前已有修改和未跟踪文件默认属于用户或其他并行任务，不得用 `git add .`、`git commit -a` 或清理命令覆盖。
- 已提交的 React、Task、Email 功能先做 diff 和测试复核，发现问题才修复；不要从头重写已经稳定的 Workbench、Audit lifecycle 或 Email provider 动作。
- 所有新增行为先写失败回归测试，再写最小实现；每完成一个可独立验收的领域就形成一个独立提交。
- 任何涉及审核、授权、确认、外部发送、退订、删除、恢复或状态策略的变更，必须拆成单独任务和单独提交，不能作为 UI 修复的顺手改动。
- 不修改 `docs/ui-audit-2026-08-29/` 和 `.superpowers/` 中的用户文件；计划文件除外。
- 不把页面测试结果当成业务任务完成结果；最终报告分别给出代码、服务、外部动作和队列状态。

### Task 0.1：建立基线并登记工作区归属

**Files:**

- Read: `git status`, `git log`, `docs/ui-audit-2026-08-29/report.md`
- Read: `frontend/package.json`, `package.json`, `pyproject.toml`
- Read: `docs/architecture.md`, `docs/runtime-mechanism.md`

- [ ] 运行前端基线：

```bash
pnpm --dir frontend test --run
pnpm --dir frontend exec tsc --noEmit
pnpm --dir frontend exec vite build --outDir /private/tmp/ceo-agent-baseline --emptyOutDir
```

预期：当前基线测试和类型检查通过；构建产物只写入 `/private/tmp/ceo-agent-baseline`，不覆盖 `app/static/workbench`。

- [ ] 运行后端只读基线：

```bash
${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest -q
${CEO_RUFF:-$HOME/miniforge3/bin/ruff} check app tests
git diff --check
```

- [ ] 将工作区文件按四类登记在实施记录中：
  - React 控制台：`frontend/src/api/console.*`、`frontend/src/app/*`、`frontend/src/components/*`、`frontend/src/pages/*`、`frontend/src/styles.css`。
  - Task/证据：`app/task_*.py`、`app/todo_completion.py`、`app/follow_up.py`、`app/store.py`、对应测试和 API。
  - Email：`app/email_*.py`、Email API、Email 页面、classifier 文档和对应测试。
  - 运行发布：`app/service_supervisor.py`、CLI、静态构建产物、launchd/readiness/队列读回。
- [ ] 若当前 HEAD 或工作区在基线运行期间发生变化，保存新的状态快照，重新运行基线，不根据旧输出判断完成。

### Task 0.2：复核已有提交，避免重复实现

**Files:**

- Read: `frontend/src/pages/SettingsPage.tsx`
- Read: `frontend/src/pages/AttentionPage.tsx`
- Read: `frontend/src/pages/TaskDetailPage.tsx`
- Read: `frontend/src/components/status/StatusBadge.tsx`
- Read: `frontend/src/app/router.tsx`
- Read: `app/web_api/registration.py`, `app/web_api/email.py`, `app/web_api/tasks.py`

- [ ] 逐项确认以下已有能力与当前源码一致：
  - `ready`/`available` 为成功绿色，`unavailable`/`failed`/`error` 为危险红色，处理中和待处理使用进度/警告色，未知状态不伪造成功色。
  - Attention 有红色未解决数量 badge，摘要和详情不重复展示同一个错误。
  - `/tasks/:projectId` 能解析项目 ID；Facts 使用摘要/展开而不是窄 Source 列；Sent TODO 任意对象不会变成 `[object Object]`。
  - Agent Workbench 使用导航实际高度和 `min-height: 0`，不存在固定猜测导航高度。
  - Prompt/Audit preview 能对模板变量替换结果高亮；WeChat 回复范围在 Settings 内联编辑。
  - Agent Runtime 凭据可以回填、编辑、保存；模型使用下拉菜单，包含 OpenAI、MiniMax、Qwen、智谱等常见选项，并保留当前未知模型选项。
  - 路由按页面懒加载；普通业务深链由 SPA 入口处理，未知 `/api/*` 仍返回 JSON 404。
- [ ] 将每一项标记为 `已提交且通过`、`已提交但需真实浏览器复核` 或 `未完成`；后续任务只处理后两类。

---

## 1. React 控制台收敛：页面、契约和 UI 对齐

### Task 1.1：锁定 API DTO 和页面状态契约

**Files:**

- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/api/console.test.ts`
- Modify: `app/web_api/registration.py`, `app/web_api/tasks.py`, `app/web_api/email.py`
- Test: `tests/test_console_web_api.py`

- [ ] 先为列表和详情接口补充契约测试，确保成功响应包含 `items`/`item` 和 `meta.snapshot_at`，命令响应包含 `ok`、`message` 和必要的 `item`。
- [ ] 对以下字段统一执行后端规范化后再进入 DTO：字典、数组、异常对象、空值、结构化 provider error、来源路径和长文本。前端的 `displayValue` 只负责显示兜底，不替代后端 DTO 规范化。
- [ ] 验证未知 API 路径返回 JSON 404，不能被 SPA fallback 转成 `index.html`：

```python
response = client.get("/api/console/not-a-real-resource")
assert response.status_code == 404
assert response.headers["content-type"].startswith("application/json")
assert response.json()["code"] == "not_found"
```

- [ ] 为列表统一验证 loading、populated、empty、error、refreshing/stale 所需字段；刷新失败时响应不会把上一份有效数据替换成空数据。
- [ ] 为 API 验证 loopback、Host、Origin、Referer、JSON Content-Type、非法参数、重复命令和 401/403/409/422/500 的公开错误结构。
- [ ] 运行：

```bash
${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest tests/test_console_web_api.py -q
pnpm --dir frontend test --run
```

预期 API 契约测试和前端测试分别通过，且不出现 `[object Object]`、`undefined`、`null` 或原始 `<structured error>`。

### Task 1.2：完成 Settings 页面逐项对齐

**Files:**

- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.test.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `app/web_api/settings.py`, `app/web_api/registration.py`（仅在 DTO 或保存契约缺失时）

- [ ] 保留 Settings 的既定 section：`Status`、`Info`、`Configuration`、`Agent Runtime`、`Prompts`、`Connectors`、`Audit Rules`、`Attention`；Logs 不恢复为一级入口。
- [ ] Info 首屏恢复并保留运行说明、Producer 路由说明、有效配置值的解释；运行上下文放在显式展开区域，不把说明塞进隐藏 HTML 或页面脚本。
- [ ] Configuration 桌面端保持 `Key / Current value / Description`，Description 限制宽度并支持单项展开；移动端改为单列配置卡片。每个输入拥有显式 label、类型、默认值、scope、校验错误和局部保存状态。
- [ ] Agent Runtime 的敏感字段按当前产品要求处理：已保存凭据回填到可编辑输入；输入类型默认 password；显示/隐藏按钮具备 `aria-label`、`aria-pressed`、`aria-controls`；保存成功后仍可再次编辑；真实值不出现在普通文本和调试详情中。
- [ ] Agent Runtime 模型选择使用分组下拉，不允许只靠手填。至少提供：

```text
OpenAI/Codex: gpt-5.5, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna
MiniMax: MiniMax-M2.5, MiniMax-M2.1, MiniMax-M2
Qwen: qwen3-max, qwen3-coder-plus, qwen-plus, qwen-turbo
智谱: glm-5, glm-4.7, glm-4.6
```

如果后端当前值不在清单中，保留 `当前配置：<value>` 选项，不能静默清空。
- [ ] Prompt 页面保留 Developer/User 与 Template/Rendered preview 两组选择关系；Available runtime variables 使用 chips，详细说明折叠；编辑器内高亮 `{{principal}}` 等 token；preview 明确显示 sample runtime context。
- [ ] Audit Rules 明确呈现两层控制：上层“规则类型”切换 Template/Consumer/Audit，下层“查看方式”切换 Template/Rendered preview。切换一个层级不能意外重置另一个层级；Consumer/Audit 的 Template 视图继续说明实际编辑入口是 Template。
- [ ] WeChat 回复范围在 Connectors 页面直接显示，不跳转到 `/wechat/conversations`；已保存对象置顶/单独分组，搜索后保留已选项，保存状态显示未保存/保存中/已保存/失败，触控目标达到移动端尺寸。
- [ ] Connector 状态统一使用 `StatusBadge`，并对健康、未连接、不可用、锁屏、权限和未知分别使用语义颜色；Lark 原始结构化错误放入技术详情，人话区域显示发生了什么、影响和下一步。
- [ ] 增加/保留以下回归测试：
  - 已保存四类凭据回填并可编辑。
  - 清空或替换 token 后保存请求包含用户当前输入。
  - 模型选项和未知当前值保留。
  - Prompt preview 高亮替换值。
  - Audit 两组 tabs 的 `role`、`aria-selected`、href 和相对关系正确。
  - WeChat 回复范围无导航链接，搜索、勾选、保存和错误重试均局部完成。
  - 每个配置输入可通过 label 查询，secret toggle 保持焦点。
- [ ] 运行 Settings focused suite：

```bash
pnpm --dir frontend test --run src/pages/SettingsPage.test.tsx src/components/forms/SecretField.test.tsx src/components/editor/TokenEditor.test.tsx
```

### Task 1.3：修复并对齐 Attention、Status 和全部状态颜色

**Files:**

- Modify: `frontend/src/components/status/StatusBadge.tsx`
- Modify: `frontend/src/components/status/StatusBadge.test.tsx`
- Modify: `frontend/src/pages/AttentionPage.tsx`, `frontend/src/pages/StatusPage.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/pages/AttentionPage.test.tsx`, `frontend/src/pages/StatusPage.test.tsx`

- [ ] 将 Attention 设计成根因聚合列表：类别、严重程度、同类数量、根因、最近更新时间、短摘要、处理入口；完整 Error、命令、来源和关联详情在展开区。
- [ ] 数字 badge 使用明确的红色危险样式，数量来自同一个 Attention snapshot，显示“未解决数量 + 更新时间”；刷新时保留旧列表并显示 refreshing，不让 badge 和列表分别跳变。
- [ ] 当 Summary 与 Error 相同或一方为空时首屏只显示一次；详情中仍可查看完整值。
- [ ] 保证 `database is locked` 等 Service error 的链接指向真实可查询的 Attempt/Meeting/History 详情。前端不能把不存在的 ID 直接拼成 Attempt URL；API 返回的 `links` 必须通过详情存在性测试。
- [ ] Status 只显示系统快照、PID/Runs、processing/retryable/failed、组件健康、连接器摘要和队列统计；慢连接器探测不能阻塞 worker 快照。
- [ ] 用参数化测试覆盖至少：`ready`、`available`、`completed`、`connected`、`pending`、`processing`、`warning`、`unavailable`、`failed`、`error`、`denied`、`not_ready`、`unknown`，并检查文字、class 和可访问语义一致。
- [ ] 运行：

```bash
pnpm --dir frontend test --run src/pages/AttentionPage.test.tsx src/pages/StatusPage.test.tsx src/components/status/StatusBadge.test.tsx
${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest tests/test_console_web_api.py -q
```

### Task 1.4：修复 Tasks、Task detail、Sent TODO 和历史详情链接

**Files:**

- Modify: `frontend/src/pages/TasksPage.tsx`, `frontend/src/pages/TaskDetailPage.tsx`
- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/pages/TasksPage.test.tsx`, `frontend/src/pages/TaskDetailPage.test.tsx`, `frontend/src/api/console.test.ts`
- Backend read/modify when needed: `app/web_api/tasks.py`, `app/store.py`

- [ ] Tasks 桌面列表只保留 Project、Status、Priority、Owner、Progress、ToDos count；State、Next 和完整 TODO 进入详情/展开。移动端必须是单列卡片，提供项目名、状态/优先级/Owner、Progress、TODO 数量/最近一项和“查看详情”。禁止通过 `overflow-x:hidden` 隐藏核心字段。
- [ ] Facts 使用独立事实卡片：Description 默认三行，单条 Fact 独立展开，Source 默认标题/文件名，完整路径进入技术详情或 tooltip，Created/Updated 为次级 metadata。空值显示“未提供描述”和“来源未记录”。
- [ ] Project details、Updates、Memory context、Unlinked follow-ups 采用同样的摘要/展开策略；不再使用固定窄 Source 列。
- [ ] Sent TODO 的值规范化覆盖字符串、字典、数组、嵌套对象、空值和异常对象。字典优先取 `title`/`text`/`content`，数组按项目生成有界摘要，其他对象生成有界 JSON 摘要。
- [ ] 对 `Attempt not found` 建立端到端回归：准备一个真实存在的 attempt、一个不存在的 attempt 和一个 Attention 聚合链接；确认存在记录进入详情，不存在记录显示可理解的 404/关联失效说明，不把 `/attempts/<id>` 页面渲染成空白。
- [ ] Sent TODO 加载失败显示原因和重试，不把失败误显示为“没有 TODO”。
- [ ] 运行：

```bash
pnpm --dir frontend test --run src/pages/TasksPage.test.tsx src/pages/TaskDetailPage.test.tsx src/api/console.test.ts
${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest tests/test_console_web_api.py tests/test_audit_web.py -q
```

### Task 1.5：恢复 History、用户反馈与详情页的老版业务密度

**Files:**

- Modify: `frontend/src/pages/HistoryPage.tsx`, `frontend/src/pages/FeedbackPage.tsx`
- Modify: `frontend/src/pages/BusinessDetailPage.tsx`, `frontend/src/pages/CodexPages.tsx`, `frontend/src/pages/RuntimeErrorDetailPage.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/pages/HistoryPage.test.tsx`, `frontend/src/pages/FeedbackPage.test.tsx`, `frontend/src/pages/BusinessDetailPage.test.tsx`

- [ ] History 列表恢复老版可扫描的信息密度，只显示时间、业务标题、类型、状态、短摘要、操作者；完整 Input/Decision/Output/Changes/Reason/Source/Runtime details 进入详情展开。
- [ ] 保留对象类型、状态、搜索、分页、24 小时事件图和 Attempt 链接；筛选条件和分页写入 URL，前进/后退后状态一致。
- [ ] 用户反馈恢复老版的关联和处理能力，同时保留关键词搜索、状态筛选、分页、待处理数量、空态、错误态、同步和局部保存状态；已处理记录低优先级展示但不能丢失。
- [ ] Attempt、Meeting、OA、Codex 详情统一为业务结果、状态、输入/上下文、决策、输出/回复、动作和后续状态、可折叠 Runtime details；不默认显示内部 session ID、绝对本地路径或完整原始日志。
- [ ] 对长摘要、命令、错误、路径和 Changes 做 clamp + 当前记录展开；展开一条不能改变其他记录的状态。
- [ ] 运行 focused suite，并对比老版截图/DOM：

```bash
pnpm --dir frontend test --run src/pages/HistoryPage.test.tsx src/pages/FeedbackPage.test.tsx src/pages/BusinessDetailPage.test.tsx
```

### Task 1.6：保持路由、懒加载和 Workbench 高度正确

**Files:**

- Modify: `frontend/src/app/router.tsx`, `frontend/src/app/router.test.tsx`
- Modify: `frontend/src/app/AppShell.tsx`, `frontend/src/components/GlobalNav.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/app.test.tsx`, `frontend/src/components/GlobalNav.test.tsx`, `frontend/src/components/TaskList.test.tsx`

- [ ] 集中定义普通业务路由：`/`、`/history`、Attempt/Meeting/OA 详情、`/tasks`、`/tasks/:projectId`、`/settings`、`/user-feedback`、`/tutorial`、`/notifications`、`/codex`、WeChat 页面和统一 Not Found；旧 `/workers`、`/status`、`/attention`、`/logs`、`/errors`、`/config`、`/developer-prompt` 按既定兼容规则映射。
- [ ] GlobalNav 使用当前 location 判断 active，不使用 `index === 0`；一级导航只显示 Agent、History、Tasks、用户反馈、Settings。Email 若作为当前批准的独立业务入口展示，必须有单独产品决定和单独路由验收，不得隐式混入既有导航。
- [ ] 维持 route-level lazy loading；重新检查构建 manifest，确认 Agent、Settings、History、Tasks、Feedback、Email 和详情页面具有独立 chunk，公共依赖不会因页面导入方式重新合并成单一大包。
- [ ] Workbench 根布局使用实际导航高度：`.console-root { height: 100vh; min-height: 0; grid-template-rows: auto minmax(0, 1fr); }`，嵌套 shell/list/inspector 使用 `height: 100%` 和 `min-height: 0`；不写固定 `52px` 等导航假设。
- [ ] Router 测试覆盖直达、刷新、查询参数、前进、后退、错误页面和 `/tasks/836` 的 `projectId` 解析。

---

## 2. Task、Owner、Progress 和证据工作流复核

### Task 2.1：确认 Task 领域模型与运行契约不被 UI 迁移改变

**Files:**

- Read/modify only when a failing test proves necessary: `app/task_models.py`, `app/task_progress.py`, `app/task_retrieval.py`, `app/todo_completion.py`
- Read: `docs/architecture.md`, `docs/runtime-mechanism.md`, `skills/ceo-work-tracking/SKILL.md`
- Test: `tests/test_task_agent.py`, `tests/test_task_retrieval.py`, `tests/test_todo_completion.py`, `tests/test_task_store.py`

- [ ] 以 `pending -> running -> done/failed/needs_human` 和现有 `needs_feedback/revision_pending` 兼容语义为准，不增加 `discard`/`discarded`。
- [ ] 区分 `reply_task` 队列状态、`agent_runs` 执行事实和 `reply_attempt` 当前业务投影；页面显示当前投影，详情保留执行轨迹，不用 UI 重新推断业务状态。
- [ ] 复核 TODO 完成判断顺序：明确完成状态、完成证据、DingTalk TODO 完成链接、关联 follow-up；取消项不能误算完成；不确定证据不能自动关闭。
- [ ] 复核并发、重启、重复调用和旧数据：同一 execution generation 不重复关闭；反馈周期不因基础设施失败消耗；原始 run/feedback/revision 不被覆盖。
- [ ] 运行：

```bash
${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest tests/test_task_agent.py tests/test_task_retrieval.py tests/test_todo_completion.py tests/test_task_store.py -q
```

### Task 2.2：执行 Owner backfill 的小批量、可回滚验证

**Files:**

- Read/modify: `app/task_owner_backfill.py`
- Test: `tests/test_task_owner_backfill.py`
- Docs: `README.md` 或 `docs/runtime-mechanism.md`（只补实际运行命令和结果）

- [ ] 先对明确指定的项目/Task 执行 dry-run，确认唯一 follow-up owner、冲突 owner、无 owner 三种结果分别输出。
- [ ] 对唯一 owner 的小批量执行 apply；读回 `todo.owner_user_id`、完成证据和更新时间，确认只更新目标行。
- [ ] 对冲突 owner 保持不修改，并产生可理解的人工处理记录；不能猜测 owner。
- [ ] apply 前备份 SQLite；备份验证可读后才执行小批量，失败时通过现有恢复流程处理，不使用 `git reset` 或删除数据库。
- [ ] 运行：

```bash
${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest tests/test_task_owner_backfill.py -q
```

### Task 2.3：复核证据候选 API 和 Task detail 展示

**Files:**

- Modify only when required: `app/web_api/tasks.py`, `app/web_api/registration.py`, `frontend/src/api/console.ts`, `frontend/src/pages/TaskDetailPage.tsx`
- Test: `tests/test_console_web_api.py`, `frontend/src/api/console.test.ts`, `frontend/src/pages/TaskDetailPage.test.tsx`

- [ ] 证据候选 DTO 明确包含状态、来源类型、来源引用、原因、证据文本、决策和快照时间；完整来源路径只在展开详情显示。
- [ ] 页面将 Evidence candidates 与 TODO 完成状态分开；候选存在不等于 TODO 已完成，只有后端完成判断写入的 evidence 才能影响 Progress。
- [ ] 结构化 decision、异常和嵌套证据全部通过安全格式化；详情不会显示 `[object Object]` 或泄露凭据、完整邮件正文、外部 URL token、本地附件路径。
- [ ] 运行 Task API 和 React focused suite；再用真实项目 ID 读回 `/tasks/836`，验证刷新后项目标题、Facts、TODO、Owner、Progress 和 Evidence candidates 一致。

---

## 3. Email classifier、Email worker 与 Email 页面收敛

### Task 3.1：确定 Email classifier 的 review-only 与自动动作边界

**Files:**

- Read: `docs/superpowers/specs/2026-08-29-email-classifier-design.md`
- Read: `docs/superpowers/specs/2026-08-30-email-unsubscribe-branch-integration-design.md`
- Read: `app/email_classifier_contracts.py`, `app/email_pipeline.py`, `app/email_store.py`, `app/email_classifier_runtime.py`
- Test: `tests/test_email_classifier_contracts.py`, `tests/test_email_pipeline.py`, `tests/test_email_classifier_training.py`, `tests/test_email_store.py`

- [ ] 分类、人工确认、模型训练、只读扫描、ActionPlan 和 Email Agent task 的边界必须保持清楚：分类结果不是外部动作授权；只有显式批准的 `auto_reply`/`unsubscribe` 才能进入现有 Agent/Audit 生命周期。
- [ ] 只读 IMAP 使用 `SELECT(readonly=True)`、`UID SEARCH`、`UID FETCH`；不调用 `COPY`/`EXPUNGE`，不把完整正文、附件字节、完整退订 URL、凭据或本地附件路径写入持久化。
- [ ] 冷启动、模型不存在、阈值不足、类别未达到训练资格、模型损坏、active/previous 都不可用时，分别返回可解释状态；不能因为模型预测就执行邮箱动作。
- [ ] 人工确认先持久化；训练未到期或训练失败不能撤销确认；模型 candidate 只有通过 readiness、验证、序列化/reload 后才能晋级 active。
- [ ] 运行所有 Email classifier focused tests，并记录模型文件、训练状态和敏感字段不泄露检查。

### Task 3.2：复核独立 Email worker 的启动、健康和恢复

**Files:**

- Read/modify only when tests require: `app/email_worker.py`, `app/service_supervisor.py`, `app/cli.py`
- Test: `tests/test_email_worker.py`, `tests/test_service_supervisor.py`, `tests/test_cli.py`

- [ ] worker 启动顺序固定为：加载启用账户、加载 active model、依赖准备完成后报告 ready、再启动 scan/actions、agent-consumer、training 三个独立 daemon loop。
- [ ] 单账户扫描失败不能杀掉其他账户；Email task 失败只影响当前 task；健康状态只包含有界、脱敏的 error code/type，不写入正文、凭据、URL 或附件路径。
- [ ] 没有 provider executor 时 direct action 不得被 claim；外部动作结果必须使用现有 operation/target/provider result identifier 记录，不新增一套 parallel unknown 状态机。
- [ ] supervisor 继续只管理一个 launchd job 和现有 worker/audit-web 子进程，不新增第二个 plist。
- [ ] 运行：

```bash
${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest tests/test_email_worker.py tests/test_service_supervisor.py tests/test_cli.py -q
```

### Task 3.3：完成 Email React 页面与 API 的一致性

**Files:**

- Add/modify: `frontend/src/pages/EmailPage.tsx`, `frontend/src/pages/EmailPage.test.tsx`
- Modify: `frontend/src/api/console.ts`, `frontend/src/api/console.test.ts`, `frontend/src/app/router.tsx`
- Modify only for missing server contract: `app/web_api/email.py`, `app/web_api/registration.py`

- [ ] Email 页面固定为“已处理、待反馈、邮件配置”三个业务区域；列表有 loading、empty、error、分页、快照时间和局部操作状态。
- [ ] 人工确认分类只发送 JSON，显示保存成功/失败和冲突原因；不能把确认直接渲染为已执行邮箱动作。
- [ ] 配置页面显示类别、说明、阈值、动作、enabled、版本和保存状态；敏感凭据只显示 configured/遮罩状态，不能回填到普通文本。
- [ ] Email 页面是否进入一级导航必须以最终产品决定为准；默认保留 `/email` 深链和路由，不在没有明确产品确认时改变既定五项一级导航。
- [ ] 页面不直接暴露内部 session ID、数据库路径、模型文件绝对路径和 provider secret；技术详情显式展开后仍需脱敏。
- [ ] 运行 Email 页面 focused suite 和 API contract suite；用 mock store 验证 processed、pending feedback、empty、failed、409 重复确认和配置保存失败。

### Task 3.4：退订分支只做独立行为验收

**Files:**

- Read/modify only in the Email scope: `app/email_unsubscribe.py`, `tests/test_email_unsubscribe.py`, `tests/browser/test_email_unsubscribe_browser.py`
- Docs: `docs/superpowers/specs/2026-08-30-email-unsubscribe-branch-integration-design.md`

- [ ] 逐条验证登录/CAPTCHA/无法定位入口/页面跳转失败/确认失败/读回不确定等场景，确认 blocked/no-action/failed 的边界符合已批准设计。
- [ ] 每个外部写动作都通过 Audit 接受、执行和 readback；服务重启或响应不确定时不能盲目重播。
- [ ] 禁止用 UI 测试模拟真实退订成功；真实 provider 动作必须使用已有安全测试夹具或用户明确授权的受控环境。
- [ ] Email 退订通过后单独提交和单独报告，不与普通 React 样式或 Task progress 提交合并。

---

## 4. 干净构建、真实浏览器和运行发布验收

### Task 4.1：建立 1280×720 / 390×844 页面验收矩阵

**Files:**

- Add/modify only if repository已有浏览器测试约定: `tests/browser/*` 或 `frontend/e2e/*`
- Read: `docs/ui-audit-2026-08-29/report.md`

- [ ] 在两个视口逐一直接访问并刷新：

```text
/
/history
/tasks
/tasks/836
/attempts/{existing_attempt_id}
/meeting-attempts/{existing_run_id}
/oa-approvals/{existing_process_instance_id}
/user-feedback
/settings?tab=status
/settings?tab=info
/settings?tab=configuration
/settings?tab=agent-runtime
/settings?tab=prompts&prompt=user&view=preview
/settings?tab=connectors
/settings?tab=audit-rules&rule=template&view=preview
/settings?tab=attention
/tutorial
/notifications
/codex
/wechat/review
/wechat/memory-review
/wechat/deliveries
/wechat/conversations
/not-a-real-page
```

- [ ] 每页记录以下结果：直接访问、刷新、页面切换、查询参数保留、loading、empty、error、重试、筛选、搜索、分页、表单保存、保存失败、返回/前进/后退。
- [ ] 390px 必须检查：无核心字段消失、无水平溢出遮挡、筛选可发现、抽屉/详情可返回、键盘弹出后输入不被遮挡、按钮触控区域可用。
- [ ] 1280px 必须检查：页面不超过视口导致主体失去滚动、表格列可比较、长文本不撑爆布局、Settings tab 和 action 不重叠。
- [ ] Agent Workbench 额外执行冷启动和热刷新；确认主体不空白、任务列表/Inspector/Composer 都在导航下方可见，底部没有被遮挡。
- [ ] `/not-a-real-page` 显示统一 Not Found；`/api/console/not-a-real-resource` 返回 JSON 404。

### Task 4.2：执行可访问性和状态语义自检

**Files:**

- Modify only when failures identify a real issue: `frontend/src/components/*`, `frontend/src/pages/*`, `frontend/src/styles.css`
- Test: corresponding Vitest files and browser readback

- [ ] 用键盘完成 Agent、History、Tasks、Feedback、Settings、Prompt/Audit tabs、WeChat 搜索/勾选/保存、Runtime secret toggle 和 Attention 展开；每一步记录 focus 是否可见且顺序合理。
- [ ] 验证所有输入有 label/accessible name；tabs 使用 `role=tablist/tab/tabpanel` 和正确 `aria-selected`；状态更新使用局部 `aria-live`，不重复播报整张表。
- [ ] 检查 ready、unavailable、failed、warning、processing、pending、saved、saving、dirty、refreshing、stale 的颜色和文字同时表达，不只依赖颜色。
- [ ] 验证 secret 字段显示/隐藏后焦点留在按钮，保存的值可以按产品要求回填但不出现在普通日志、DOM 之外的技术文本或错误消息中。
- [ ] 如环境允许，使用 VoiceOver 完成至少 Settings 和 Attention 的标题、状态、按钮、展开区顺序读回；不能用“DOM 存在”代替读屏验收。

### Task 4.3：执行规模和 bundle 验收

**Files:**

- Read: final Vite manifest and generated chunks
- Add test/script only if absent: `frontend/scripts/*` or `tests/browser/*`

- [ ] 对 History、Tasks、Attention、Feedback 准备 1、10、100、1000、10000 条 fixture，分别记录首屏请求、首屏可见、API、首次交互、滚动、筛选、分页和 DOM 挂载数量。
- [ ] 验证自动刷新保留旧快照，不发生整页重绘或从 populated 闪成 empty；10,000 条列表使用分页、游标或虚拟化，不一次挂载全部详情。
- [ ] 使用最终构建 manifest 记录每个 route chunk 的 gzip 大小；确认 route-level lazy loading 生效，公共 chunk 与页面 chunk 没有非预期重复。
- [ ] 构建命令：

```bash
pnpm --dir frontend exec tsc --noEmit
pnpm --dir frontend test --run
pnpm --dir frontend exec vite build --outDir /private/tmp/ceo-agent-final-build --emptyOutDir
```

### Task 4.4：构建正式静态资源并验证 FastAPI SPA fallback

**Files:**

- Modify generated output only through the existing build command: `app/static/workbench/*`
- Read/modify when required: `app/audit_web.py`, `app/web_api/registration.py`
- Test: `tests/test_audit_web.py`, `tests/test_console_web_api.py`

- [ ] 最终代码全部提交后运行：

```bash
npm run build:workbench
```

- [ ] 重新读取 `app/static/workbench/index.html` 和 manifest，确认 HTML 引用的是本次构建生成的 hash 资源。
- [ ] 用 FastAPI TestClient 和真实 HTTP 分别验证 `/`、`/tasks/836`、`/history`、`/settings?tab=status`、`/user-feedback`、`/email` 返回 React 入口，`/api/*` 未知路径返回 JSON 404，DingTalk bridge/popup 和 Service Worker 仍走专用入口。
- [ ] `git diff --check` 通过；生成的静态资源只在正式 build 后纳入与该 build 相关的提交，不把临时 `/private/tmp` 产物加入仓库。

### Task 4.5：重启 launchd 并做运行时读回

**Files:**

- Read/modify only when needed: launchd plist、`app/service_supervisor.py`、README/运行手册
- Runtime: `com.ceo-agent-service.main`

- [ ] 先确认代码提交、静态资源提交和文档提交完整，再执行：

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,100p'
lsof -nP -iTCP:8765 -sTCP:LISTEN
curl -sS http://127.0.0.1:8765/
curl -sS http://127.0.0.1:8765/tasks/836
curl -sS 'http://127.0.0.1:8765/settings?tab=info'
```

- [ ] 确认新 PID 与重启前不同，readiness 通过，服务 supervisor、worker、audit-web 子进程均稳定；如果连接被拒绝，先检查 supervisor/child 稳定性再判断页面或 API。
- [ ] 真实 HTTP readback 至少包括 `/tasks/836`、`/history`、`/settings?tab=status`、`/settings?tab=info`、`/settings?tab=agent-runtime`、`/attention`、`/user-feedback`、`/wechat/conversations` 和一个真实 Attempt 详情。
- [ ] 读取 SQLite 当前 `processing/running`、`failed`、`needs_human`、Attention 数量、Email worker health、queue lease 和最近外部 provider receipt；对比重启前快照，确认没有 UI 发布引起的任务重播、任务丢失或 lease 泄漏。
- [ ] 对已有 failed/processing 项目单独记录业务原因和处理动作；不把它们自动标记完成，也不因为页面 200 就关闭 Attention。
- [ ] 如果运行时代码、prompt、路由或服务行为本轮发生变化，必须按仓库规则重新 restart/readback；仅共享组件追踪提交不需要重复重启，但正式静态资源切换需要重启并读回。

---

## 5. 提交边界、文档和最终报告

### Task 5.1：按领域形成独立提交

- [ ] React 公共组件/页面/API/测试按功能提交；不得夹带 Email worker、Task 语义或数据库迁移。
- [ ] Task/Owner/Progress/证据按 Task 领域提交；不得把 UI 颜色或 Settings 样式混入。
- [ ] Email classifier/worker/provider 行为按 Email 领域提交；退订外部动作单独提交。
- [ ] 构建产物、FastAPI fallback、launchd/readiness 和运行手册按发布领域提交。
- [ ] 每个提交前运行精确 `git diff --cached --stat`、`git diff --cached --check`，确认没有 `.superpowers/`、`docs/ui-audit-2026-08-29/`、`.pnpm-store/` 或其他用户 WIP。

### Task 5.2：更新运行和验收文档

**Files:**

- Modify only as needed: `README.md`, `docs/runtime-mechanism.md`, `docs/architecture.md`, `CHANGELOG.md`

- [ ] 记录普通业务页面由 React SPA 渲染、FastAPI 深链 fallback、`/api/*` JSON 404、DingTalk bridge/popup/Service Worker 例外。
- [ ] 记录 Settings 的配置保存、凭据回填、模型下拉、Prompt/Audit 两层 tabs、WeChat 回复范围内联维护方式。
- [ ] 记录 `ready/unavailable/failed/processing` 状态颜色和业务含义，避免后续页面自行定义另一套语义。
- [ ] 记录 `/tasks/836` 直达、静态构建、launchd restart/readback、SQLite 队列检查命令和已知业务 backlog；不把某次测试中的业务 failed 数量写成永久事实。

### Task 5.3：最终门禁

- [ ] 工作区没有未解释的 tracked modification；未跟踪文件均已确认是本任务产物、用户文件或明确的临时缓存。
- [ ] `git diff --check`、前端完整测试、后端完整测试、生产构建和最终 HTTP readback 全部有当前 HEAD 的输出证据。
- [ ] 浏览器矩阵覆盖 1280×720、390×844、所有业务路由、Settings 全部 section、关键空/错/慢/保存场景、键盘和 VoiceOver/focus 顺序。
- [ ] 业务动作单独列出：实际未发送、未退订、未删除、未审批的测试；任何真实外部动作只报告已明确授权且有 readback 的动作。
- [ ] 队列状态单独列出：`running/processing`、`failed`、`needs_human`、Attention 和最近 receipt；说明哪些是历史问题、哪些由本轮引入、哪些需要后续业务处理。
- [ ] 最终报告列出每个独立 commit SHA、验证命令、验证结果、未完成项和阻塞原因；禁止只报告“页面能打开”或单个 HTTP 200。

## 默认假设

- 继续使用现有 React/Vite、FastAPI、SQLite 和 launchd，不迁移到 Ant Design、NestJS、PostgreSQL、Docker 或 Kubernetes；这些是通用内部应用建议，不是本仓库当前批准的架构替换。
- 用户选择“整个工作区”，但不等于把四条业务线合成一个提交；默认采用四条独立工作流、一个最终发布门禁。
- `/email` 作为当前已有深链和 React 页面保留；是否进入一级导航需单独依据产品信息架构确认，默认不改变已经确认的 Agent/History/Tasks/用户反馈/Settings 五项导航。
- 已保存凭据按当前用户最新要求回填并可编辑；保存语义仍由后端现有 `.env`/配置写入逻辑负责，前端不改变密钥存储位置。
- “百分之百自检”解释为对已列页面、视口、状态、交互、API、构建、服务和队列门禁逐项留下证据；无法自动化的 VoiceOver/真实外部 provider 动作必须明确标为人工验收或受控环境验收，不能假装已经完成。
- 不删除历史数据、不重播外部消息、不自动处理现有 failed/processing/Attention；这些动作需要单独的业务处理决定。
