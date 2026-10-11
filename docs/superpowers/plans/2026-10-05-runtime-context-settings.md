# Runtime Context 与 Settings 完整预览实施计划

> 使用 test-driven-development、subagent-driven-development 的实现与独立审核流程。Derek 已于本会话批准开发；原业务不重放。

**Goal:** 将运行环境与能力说明融合实际 Consumer/Audit 输入，并在 Settings 展示同链路渲染的当前预览或已有运行实际输入，包含对方时区取证原则。

**Architecture:** 最终角色 CLI 配置完成后生成 Runtime Context，替换原轻量 context；用现有 agent_run_events 保存该次服务提交输入，不加数据库或迁移。Settings 新 GET 端点复用组装与环境渲染模块；历史模式读取现有运行输入事件，不用当前配置重建历史。原审核、授权、路由和执行策略保持。

**Tech Stack:** Python/FastAPI/Pydantic，FastMCP 注册目录，React/TypeScript/Vitest。

## Task 1: 实际运行输入与环境描述

Files: app/runtime_prompt_context.py (new), app/prompt.py, app/consumer_agent.py, app/agent_turn_runner.py; tests/test_runtime_prompt_context.py (new), tests/test_agent_turn_runner.py.

- [x] 测试先行：Codex Consumer/Audit 与 Claude 目录差异；最终 cwd、task/generation 与 scheduled_consumer 绑定；无凭据/认证假设；日历对方时区原则；稳定规则 hash 与动态时间分离。
- [x] 使用 `python -m pytest -q tests/test_runtime_prompt_context.py` 确认新增功能尚未实现而失败，再实现。
- [x] `render_runtime_context(role, route, command, task, current_time)` 使用最终 argv 的非敏感 feature/allowlist 信息与 `build_role_server(role)` 注册描述。不执行工具、不建任务目录、不初始化 Store。命名时区未知如实写未知。
- [x] RuntimeContext 置换通过专用 context 参数/明确组装边界处理，不靠全文关键词/正则。实际运行完成路由和角色 command 配置后，将相同渲染文本传给 CLI。
- [x] `runtime.prompt` 事件保留 runtime_attempt_id、role、task/generation/revision、时间、route/model、完整 developer/task/submitted input 与 context。事件不含 argv、env、headers、provider secrets，也不代表工具进展/外部效果。
- [x] 捕获真实 adapter 输入，验证保存事件与提交内容一致；包含 fallback 更新、读取/工件行为不改变。

## Task 2: Settings API

Files: app/prompt_preview.py (new), app/web_api/registration.py (preview route only), tests/test_prompt_preview.py (new), tests/test_console_web_api.py (new endpoint tests).

- [x] 写 API 回归并运行红灯：GET `/api/console/settings/prompt-preview?role=consumer&route_name=codex_oauth&task_id=...&run_id=...`。
- [x] 响应 envelope `item`：mode/current-or-historical, status/available-or-unavailable, role, runtime_kind, route_name, model, rendered_at, task_id/run_id, developer_instructions, task_prompt, submitted_input, runtime_context, reason, scope, routes（name/runtime_kind/model）。
- [x] Current 模式复用 Consumer/Audit developer 组装；无任务标未绑定，已有 task 只用保存源上下文，Audit 无有效 candidate 明确缺失，不创造候选。最终工具配置复用现有 role command helpers，禁止读取外部来源。
- [x] Historical 模式读取指定 run 的 runtime.prompt 事件，保持历史值；旧 run 无记录返回 unavailable，不重跑或补写。route/role/ID 错误明确返回 4xx。
- [x] `python -m pytest -q tests/test_prompt_preview.py tests/test_console_web_api.py -k prompt` 验证无业务队列、动作/任务或 provider 副作用。

## Task 3: Settings 页面

Files: frontend/src/api/promptPreview.ts (new), frontend/src/pages/RuntimePromptPreview.tsx (new), SettingsPage.tsx (preview placement), focused Vitest.

- [x] 写红灯测试：Prompts Rendered preview 可进入完整角色预览，切换角色/route、当前/历史、未绑定/旧记录不可用、加载失败、长文可读；Template 编辑仍原样。
- [x] UI 通过上述端点显示 complete developer/task 或 Claude submitted input、Runtime Context、来源标记；readonly，不设置新发送/运行按钮。标清服务提交输入范围，CLI 生成系统/history 不伪造。
- [x] `npm test -- --run src/pages/RuntimePromptPreview.test.tsx src/pages/SettingsPage.test.tsx`，然后 `npm run build`。

## Task 4: 验证、文档与发布

- [x] 独立 spec review 后再 code review，修复问题。用隔离 fixture 页面检查 light/dark、窄屏、长 prompt、历史/未知状态；不使用生产发送验收。
- [x] 更新 docs/architecture.md 与 runtime-mechanism.md 的实际行为及 source/snapshot 限制，同提交保存获准 spec/plan。
- [x] 执行精确相关 Python/前端测试与 lint；不在开发主树/生产跑全套。
- [x] 使用 PR 推送 reviewed commit，附验证范围；取得必要固定行为对照证据前不声称通用改善。
- [ ] 按项目 `python -m app.deploy` 的空闲等待与健康机制发布已批准变更，核对 PID/healthz/queues/Attention/History 与 Settings 回读；若发布等待或证据不足，继续跟进并如实报告边界。
