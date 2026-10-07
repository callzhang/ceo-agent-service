# Prompt 配置与 Consumer/Audit 上下文整合计划

> **For agentic workers:** Derek 已于2026-10-06回复 OK 批准实施。执行使用 executing-plans/subagent-driven-development；下面调查基线保留为历史来源，实施与上线状态见末尾记录。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精简实际提交的 prompt，保留并补齐同一对话对象的已有 session 复用；Settings 中每个可编辑 prompt 有明确的运行消费者；编辑、渲染预览、实际提交及历史输入使用同一组装规则，并保留当前 Consumer/Audit/System 的职责与会话契约。

**Architecture:** 沿用 Consumer Agent A → Audit Agent B → System Executor。服务契约、可配置工作原则、工作人格、任务上下文与运行事实各有一个来源；由小型纯组装函数组合，而不是把旧对话模板直接叠到新角色协议上。运行器和预览复用组装函数，实际提交仍经过现有 AgentTurnProcess 与 native CLI adapters。

**Tech Stack:** Python/Pydantic、现有 file-backed prompt 设置、SQLite runtime.prompt 事件、Codex/Claude native CLI、React/TypeScript/Vitest。

**状态与调查基线:** 2026-10-06 America/Los_Angeles；origin/main 与生产为 0d6bf42eb2922ed57a6bc6de8b836f5a443106c3。本轮只调查和写计划，没有实施、调用模型、重跑业务或部署。

## 1. 阅读范围与架构约束

已阅读与本问题有关的当前文档，不以旧 spec 代替运行事实：

- `docs/architecture.md`：当前任务运行机制、主页面执行入口、Runtime-managed Skill、会话隔离、Consumer 普通工作与 System 受控动作边界。
- `docs/runtime-mechanism.md`：角色、Skill 修订与启动配置、任务类型、Runtime Context 与 Settings 渲染输入。
- `docs/audit-task-scope.md`：系统任务对应关系及 WeChat/服务命令等例外。
- `docs/consumer-audit-system-execution-validation.md`：当前已验收的角色契约、实际输入一致性与固定评价的证据边界。
- 历史 2026-09-29 Runtime Context 笔记只用于定位；本文判断以当前代码和文档为准。

必须保留的已有契约：

1. Consumer 准备完整候选并可执行普通材料工作；Audit 独立只读审核；System 执行已审核持久化精确方案。此次不新增审核项、授权、确认、恢复或动作策略。
2. 审核反馈、stage、执行代、candidate_digest、历史人工回答与 prior receipts 不能因模板整合被删掉或换成普通消息。
3. 定时 Agent 的专用 consumer_prompt 和结构化 Skill 引用仍然是任务自己的输入，不改成 Developer 全局设置。
4. 原 session 在正常反馈、修订和配置变更后继续使用。文档明确：契约 hash 是回执，不是会话身份边界；不得因新模板 SHA 而清空会话。
5. WeChat task3、Email 退订、纯扫描/同步命令及 Workbench 有独立职责，此次不强行加入 Consumer/Audit 流程。
6. 当前任务、run、attempt 与历史输入不重写；旧记录缺输入时仍显示不可用。

## 2. 当前实际路径及问题证据

| 配置或路径 | 已确认事实 | 整合问题 |
| --- | --- | --- |
| Developer 模板 | `app/developer_prompt.py` 读写和渲染；`app/codex_runner.py:codex_developer_instructions` 在没有显式指令时用作默认值 | Consumer/Audit 显式传入自己的指令，绕过默认值；不能称为已使用的另一条业务入口 |
| User 模板 | `app/prompt.py:build_turn_prompt` 渲染旧钉钉消息块；app 中未找到业务调用，测试有调用 | 当前模板不是后台任务上下文的实际入口 |
| Work profile | `app/prompt.py:work_profile_instruction` 由 Consumer/Audit helper 注入 | 已生效；无需重新发明注入机制 |
| Consumer Developer | `consumer_developer_instructions` 生成角色、schema、动作、能力、共享规则、Skill、profile | 可配置工作原则缺席；不能直接追加含重复角色契约的旧默认模板 |
| Consumer Task | `ConsumerAgentRunner` 拼 Scheduled Consumer Prompt、`AgentTaskContext.render`、反馈和 continuation | 包含完整任务事实，不等价于旧 User 的消息/人员/附件字段 |
| Audit Developer/Task | helper 中有 Audit Rules；`AuditTurnContext.render` 再带 Audit Rules、角色规则及候选上下文 | 有重复注入，需要先证明移除重复是否影响模型，不能顺手删除 |
| Runtime Context | `AgentTurnProcess` 在 final route/工具命令确定后追加；切换路线时重新生成 | 这个位置正确，应保持；不挪入静态模板 |
| Workbench `/` | `app/workbench/service_runtime.py` 使用显式 `_WORKBENCH_DEVELOPER_INSTRUCTIONS` | 也不是旧模板的实际入口；页面不能把旧模板说成主页配置 |
| User 渲染预览 | Settings GET 以空变量渲染，`user_prompt_blocks` 提供示例默认消息 | 示例必须明确标示，不能冒充当前运行材料 |
| 完整输入预览 | `prompt_preview.py` 与角色 helper 组装 Developer，但 Task 有时复用历史保存正文 | “当前配置”与“历史任务正文”混合的来源必须明确，不以缺材料的重建冒充实际输入 |

工作人格 wrapper 还含旧式 handoff/硬规则说明。它属于固定注入文案，不能与可编辑 profile 正文混为一谈；若与当前已批准的普通工作/System 执行职责冲突，先提供逐条差异供确认，不借本次机械拼接静默改变业务策略。

生产只读核对：Developer 2101 字符、User 188 字符，均与仓库默认模板一致；User 预览包含示例消息。Work profile 有已保存内容；没有复制其私人正文到本计划。

### 当前组装概要

- Consumer Developer：角色/输出/动作协议 → 能力与共享规则 → Consumer 与质量规则 → Skill 协议 → Work profile → final-route Runtime Context。
- Consumer Task：运行约定前缀 → 定时专用 prompt（如有）→ 任务上下文与 stage → Audit feedback（如有）→ continuation/修正内容。
- Audit Developer：Audit Rules → 角色/输出/动作协议 → 能力与共享规则 → Audit 与质量规则 → Work profile → task Skill override（如有）→ Runtime Context。
- Audit Task：Audit 角色规则 → Audit Rules → 来源上下文 → 当前候选/revision/digest → 修正内容。

上面的箭头只描述各输入内部的排列。Codex Developer 与 Task 分别提交；Claude 经已有输入 contract 包装。不能推断原生 CLI 的 system/tool/history 全部包含在服务预览内。

## 3. 选择与推荐

| 方案 | 收益 | 代价/不足 | 判断 |
| --- | --- | --- | --- |
| A. 隐藏或删除旧模板，仅显示角色运行输入 | 最小变更，页面马上真实 | 仍不能配置通用工作原则或任务组织方式，没有解决 Derek 的配置诉求 | 可作为止误导措施，不是最终整合 |
| B. 直接把 Developer/User 追加到 Consumer | 接入快，设置开始生效 | 默认 Developer 重复角色与 schema；旧 User 不支持邮件/OA/定时输入；Audit 不知道新原则；材料可能遗漏 | 不建议 |
| C. 统一组装、明确各配置职责，迁移旧模板 | 配置有真实消费者，预览与执行一致，保留两角色差异 | 需要模板数据迁移、输入回归和固定模型比较 | 推荐 |

推荐 C，先机械统一来源，再迁移可配置内容。不是把所有提示词合并成一个巨大文本，也不创建另一套队列、路由或配置数据库。

## 4. 目标职责

| 页面项 | 可配置内容 | 消费者 | 不包含 |
| --- | --- | --- | --- |
| Developer Prompt（共同工作原则） | 证据、判断、沟通、跨时区协作等通用工作原则 | Consumer 与 Audit：准备和审核依据保持一致 | 模型角色、输出 schema、工具授权、System 动作实现、业务 Skill 正文 |
| User Prompt（Consumer 任务模板） | 本轮任务正文的介绍、组织顺序和补充任务要求 | Consumer；所有经 AgentOrchestrator 的消息/OA/邮件/会议/定时任务 | 旧 CodexDecision 结构、Audit 角色指令、外部动作执行事实 |
| Work profile | Derek 的长期偏好、判断和表达方式 | Consumer 与 Audit | 动态任务事实、机器路径、模型配置 |
| Audit Rules | Audit 的独立业务审核规则 | Audit，沿用现有设置入口 | Consumer 的执行规则；此次不改变审核决定与生命周期 |
| 服务角色与输出契约 | 当前 role、wire schema、System action contract、能力边界 | 各自角色，只读显示 | 不作为通用 prompt 可编辑文本 |
| Runtime Context | 实际 route/model/thinking/cwd/工具声明、时间、参与者时区来源 | 各角色调用最后组装，只读 | 未查实的对方时区/空闲/认证状态 |
| 完整运行输入 | 上述来源组合后的 Developer/Task 文本及已存历史 | 只读 | 不是新 prompt，也不单独保存 |

Developer 的“共用”不等于两角色职责相同。角色和输出结构继续由各自既有 contract 提供；Audit 不使用 Consumer User 模板替代其候选审核上下文。

### 推荐拼接结构

Consumer Developer：

```text
既有 Consumer 角色、输出与能力契约
→ 共同 Developer 工作原则
→ 既有质量要求及 Skill 协议
→ Work profile
→ final-route Runtime Context
```

Consumer Task：

```text
User Prompt 模板（声明一个完整任务上下文插槽）
  └─ 定时任务专用要求（如有）
     + 当前业务任务/来源/人员/材料
     + stage、历史回执、历史人工回答与业务变化
     + 当前审核 feedback 与 proposal revision
     + continuation / 修正要求
```

Audit Developer：

```text
既有 Audit 角色、输出与能力契约
→ 同一份 Developer 工作原则
→ Audit Rules 与既有质量要求
→ Work profile
→ task Skill override（如有）
→ final-route Runtime Context
```

Audit Task：当前任务证据 + 精确 Consumer 候选/revision/digest + 修正要求；由现有结构化 renderer 提供，不使用 Consumer User 模板。

这些是目标结构。先确认各规则归属后才能从现有文本迁移；具体单段移动/去重必须在固定对照中单独评估，不假定文本换位置无影响。

## 5. 文件与实现边界

- 新建 `app/prompt_composition.py`：小型纯组装函数与不可变输入值对象，输出已有 Developer/Task 字符串；不负责读业务资料、执行动作、选择角色或 route。
- `app/developer_prompt.py`：继续使用已有配置路径；增加后台任务模板变量定义与渲染，配置文本一次读取。默认设置预览只能明确使用示例值。
- `app/consumer_agent.py`、`app/audit_agent.py`：角色/helper 调用同一组装入口；已有消费者契约和工具仍由原模块负责。
- `app/agent_context.py`：保留完整 business/stage/feedback/candidate renderer；分离重复指令前先冻结现有输出。
- `app/agent_turn_runner.py`：仍在 final command 确定后追加 Runtime Context，保存实际输入；fallback 复用本次冻结的配置，动态环境按 route 重渲染。
- `app/prompt_preview.py`、`app/web_api/registration.py`：共享组装、区分配置示例/实际历史/当前公共配置，不读取真实业务源来填补缺失。
- `frontend/src/pages/SettingsPage.tsx`、`RuntimePromptPreview.tsx` 与 promptPreview API 类型：说明消费者、生效时间、示例来源；原文与同模板渲染对应；完整角色输入保持独立。
- `app/defaults/developer_prompt.md`、`app/defaults/user_prompt.md`：迁移为新职责，避免默认模板重复角色协议。
- `app/prompt.py`、`app/user_prompt_blocks.py`、`app/codex_runner.py`：最终退休不再使用的旧业务 renderer/default。修改前对全 repo 作引用搜索，保留 work_profile 等仍使用的函数，不删除底层 native runner。
- 文档：`docs/architecture.md`、`docs/runtime-mechanism.md` 在相同行为提交中更新；本计划不把目标改写成已上线事实。

## 6. 分阶段执行与停止条件

### 阶段 0：冻结来源和迁移清单

- [ ] 保存当前 commit、已配置模板 SHA、配置来源路径和各角色输入的脱敏基线。
- [ ] 在新隔离 worktree 领取文件；不使用生产 checkout 开发、构建或测试。
- [ ] 列出旧 Developer 的每一条原则归属：已有固定 contract、业务 Skill、共同原则、过时文字。已有职责不因迁移被改写。
- [ ] 对旧 User 变量列出新结构化块映射；保留 task/feedback/receipts/stage/candidate 数据完整性。
- [ ] 默认模板通过 seed SHA 判断后迁移；自定义模板只生成逐段差异，明确其迁移内容，不覆盖或猜测丢弃。
- [ ] 固定配置在同一 invocation 中只读一次，传给契约回执、组装与 route fallback；使用既有 Skill snapshot，不建立新修订数据库。

产物：来源清单、默认与自定义迁移差异、固定用例及完整输入基线。没有这些证据就不把旧模板接入运行器。

### 阶段 1：组装与预览统一，保持当前输入

- [ ] 先写 CapturingExecutor 回归，冻结 Consumer/Audit actual Developer/Task 文本；覆盖正常、反馈、定时和 fallback。
- [ ] 将现有字符串组合提取到共享纯函数，保留现有顺序、空段和规则；本阶段不删除重复原则，不启用旧模板。
- [ ] 复用同一冻结配置组装当前预览；历史仍读取保存原文。
- [ ] 把 User 示例标为“示例变量渲染”；没有绑定材料的预览标为“未绑定任务”。移除“正在使用的旧对话入口”断言。
- [ ] 验证组装前后实际输入逐字一致；native adapters 的现有包装保持一致。
- [ ] 单独提交机械统一与真实来源说明，证明没有业务策略变化。

产物：同一来源的 assembler + 预览，当前执行行为保持原样；这一步可以独立验收。

### 阶段 2：配置真正接入与数据迁移

- [ ] 定义新 Developer 为共用原则；把服务角色/schema 留在代码已有 contract 中。
- [ ] 定义新 User 的完整 `{{task_context}}` 插槽；整个现有上下文由服务 renderer 填入，仍保留结构化来源字段。
- [ ] User 模板要求完整插槽恰好一次；这是模板渲染格式合同，不新增业务审核/确认。未知变量返回具体渲染错误，不猜测填充。
- [ ] Scheduled consumer_prompt、feedback、stage、prior receipts、manual rerun 和 continuation 全部进入完整 task_context；不得只映射成聊天文字。
- [ ] 默认迁移生成可审阅 diff，保存一份验证过的原始模板备份；自定义内容须有显式迁移方案，不能靠静默 fallback。
- [ ] 每个角色 invocation 各自冻结本轮采用的配置正文；两角色的共用原则来自同一配置源，但不新增跨角色版本锁定政策。Developer/profile/rules 改动按现有下一轮读取或启动配置契约生效，若 Consumer 与 Audit 载入时配置已变化，快照如实标示各自 SHA 与时间。
- [ ] 把新静态内容 SHA 加入现有契约回执，动态时间/model fallback 快照不进入静态指纹；保留原 session lineage，hash 变化不重置会话。
- [ ] 继续记录完整 runtime.prompt，增加必要配置来源 SHA 即可，不存另一份重复业务上下文或新的快照表。
- [ ] Audit Rules 双重出现、Consumer rules 双重出现分别作为可比较优化；没有验证时不与首次接入一起删除。

产物：配置编辑能改变下一次实际角色输入；保存内容、预览内容与已提交快照可对应。行为变化需 PR 和固定 baseline/candidate 比较，不能只凭单元测试直接发布。

### 阶段 3：Settings 整合、旧入口退休与验收

- [ ] Prompts 顶部说明共同原则、Consumer 任务模板、工作人格、审核规则、系统契约和运行输入的作用及实际消费者。
- [ ] Developer/User/Profile 的 Template 与 Rendered preview 保持同模板；示例缺材料不能显示为真实任务。
- [ ] 完整运行输入同时显示角色、来源、配置 SHA、run/attempt/revision 与 prepared/invoked；沿用现有 redaction。
- [ ] 当前预览复用历史 Task 正文时注明历史来源和配置差异；没有新模板所需完整结构输入时明确不可重建，不从消息猜补。
- [ ] 全 repo 验证旧函数调用后删除无业务调用的 build_turn_prompt 及旧示例 provider；通用变量引擎如仍被 Audit/其他模板使用则不删除。
- [ ] 为低层 CodexRunner 取消“隐含使用业务 Developer 默认模板”；每个真实调用方继续显式提供自己的指令或既有 native-instruction 模式，测试 fixture 同步改为真实合同。
- [ ] Workbench、WeChat 和其他结构化分析器页面说明“不受该组 Consumer/Audit 配置影响”；首次不把共用规则隐式灌入所有模型调用。
- [ ] 行为文档、迁移步骤、部署/回退说明与代码同提交；检查 PR 的最终范围及模型输入证据。

## 7. 验证矩阵

| 场景 | 必须验证的事实 | 测试位置 |
| --- | --- | --- |
| 配置保存 | Developer 更改进入两角色实际 Developer；User 更改进入 Consumer Task；profile 进入两角色；Audit Rules 不进入 Consumer | tests/test_prompt_composition.py（新）、test_consumer_agent.py、test_audit_agent.py |
| 多来源任务 | 钉钉、OA、邮件、会议、scheduled 完整来源/材料不丢失 | test_agent_context.py、test_scheduled_agent_consumer.py、test_consumer_agent.py |
| 反馈与续接 | revision、stage、digest、feedback、prior receipts、人工回答、continuation 保留；正常同 session 继续 | test_agent_context.py、test_consumer_agent.py、test_audit_agent.py |
| route fallback | 本次模板正文不变，动态模型/工具/环境按 final route 更新 | test_runtime_prompt_context.py、test_agent_turn_runner.py |
| 配置并发 | invocation 中途保存模板不改变本轮已冻结正文；下一轮采用新值；没有半旧半新 hash/body | test_prompt_composition.py、test_consumer_agent.py |
| 模板数据迁移 | 默认 SHA 识别、自定义保留、缺/重复插槽、未知变量、读写错误 | test_prompt.py、test_console_web_api.py、迁移专项测试 |
| UI | 四个 Prompts 页签、Template/Rendered、当前/历史、Consumer/Audit、错误/不可用/旧run、保存再切换 | SettingsPage.test.tsx、RuntimePromptPreview.test.tsx、浏览器截图 |
| 调用一致 | CapturingExecutor 的 Developer/Task 与已存 runtime.prompt 精确一致，Claude/Codex 包装不漏正文 | test_runtime_prompt_context.py、test_prompt_preview.py |
| 作用范围 | Workbench、WeChat、纯命令、退订的既有显式指令和路由不变 | tests/workbench、tests/wechat、对应现有运行器测试 |
| 时区 | 已知双方时区、未知对方、夏令时、跨日、重复/不存在当地时间；已知时区不等于空闲 | 固定模型比较，相关 runtime/context 回归 |

计划使用的定向命令（在实现 worktree，不在生产 checkout）：

```sh
pytest -q tests/test_prompt_composition.py tests/test_prompt.py tests/test_agent_context.py tests/test_consumer_agent.py tests/test_audit_agent.py tests/test_runtime_prompt_context.py tests/test_prompt_preview.py
npm --prefix frontend test -- --run src/pages/SettingsPage.test.tsx src/pages/RuntimePromptPreview.test.tsx
npm --prefix frontend run build
```

追加测试只按实际改动选择，不能把 serial 全库测试放在 live 服务机器。后台实际约束与跨域 native 行为另由正式 CI 检查；新文件名是计划新增项，不表示目前可运行。

固定模型比较：在候选实现前冻结用例、输入、现有生产配置、route/model/thinking、超时、重复次数和独立评分准则，baseline/candidate 一致。覆盖消息、OA材料、邮件、会议、定时报告、反馈续接和时区场景。记录原始模型输出、完整实际输入及内容 SHA，分别报告结构合同正确率、事实完整性、候选业务质量和 Audit 判断；不得用健康200或 parser通过代替业务效果。工具/回执必须明确是真实还是固定合成事实；禁止自动重放历史外部动作来测效果。此前单个 LA/London DST 小样本不能作为时区可靠性证明，需要固定比较并保留所有失败。

## 8. 发布、回退与验收边界

- 本计划仅是建议，尚未修改线上 prompt 或角色策略。
- 实施须先批准新配置职责及迁移差异；这次不夹带新的 audit/authorization/safety/recovery 行为。
- 阶段1可用逐字输入回归验收；阶段2/3的配置语义改变用 PR、固定评价和结果审阅验收。
- 推送完整 origin/main 后使用 `python -m app.deploy`；不直接编辑/构建生产 checkout，不手动 kickstart。
- 模板迁移必须纳入正式部署窗口，与 code revision 同步；不能先改 live file 再等待新代码。
- 回退通过正式部署可兼容的代码提交及已验证原配置，不 reset 生产、不覆盖历史run、不重放业务。迁移设计必须证明旧代码可读回备份配置。
- 上线后读回生产 commit、PID、healthz、queues、Attention、History、已加载配置和自然新 invocation 的完整 prompt；用 UI 验证 saved/template/rendered/runtime 的来源关系。
- 这次计划完成的标准是“可供决定的职责/取舍/步骤/验收均有明确依据”。代码完成、CI通过、部署成功、真实业务改善是后续独立证据。

## 9. 本计划的决策点

推荐批准：共同 Developer 原则作用于 Consumer/Audit；User 是 Consumer 全任务模板；Work profile 继续两角色共享；独立角色/schema/Audit Rules/Runtime Context 保留权威来源。先做阶段1，再依据迁移差异和固定评价执行阶段2/3。

不建议批准：直接追加旧模板、把旧聊天变量硬映射为所有业务任务、把所有模型调用改成同一prompt、借整合新增审核或重置会话。

本计划自检：全部三种页面配置的消费者已指定；scheduled/feedback/stage/receipts/两种CLI/历史缺输入/示例来源/自定义迁移/会话不重置/特殊入口均有任务或明确排除项。仍需 Derek 决定目标配置职责，然后再写基于该决定的精确实现代码与测试步骤。


## 10. Prompt 精简：新增明确验收目标

Derek 追加要求：prompt 必须精简，整合不能继续把各来源全文叠加。本轮读取生产已保存 runtime.prompt 作为样本，没有调用模型或执行业务：

| 角色/样本 | Developer 字符 | Task 字符 | 合计 | 其中 Runtime Context | invocation Skill 协议 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Consumer run24804 / attempt21623 | 73,231 | 36,356 | 109,587 | 4,994 | 14,638 |
| Audit run24805 / attempt21624 | 48,307 | 67,418 | 115,725 | 4,463 | 0 |

字符数不是 token 计费。此环境未安装 tokenizer，没有制造 token 估算；样本只计服务 Developer/Task，不计原生 CLI system/tools/history，也不是所有任务的分布。实施阶段冻结不少于20个跨任务类型的真实脱敏输入样本，分别统计 static Developer、dynamic Task、native历史和provider usage（如果运行时可提供）。

样本 Consumer Developer 内另有10,543字符的完整 Pydantic wire schema、6,123字符的 System Action Contracts；已保存 Work profile 为16,324字符。这些是主要精简候选，不能把4,994字符的环境说明当成唯一原因。

### 精简顺序

1. 先去掉重复角色规则、重复 Audit Rules 和重复原理说明；每一份规则保留唯一权威来源。各项去重分别固定比较，不一次删除多种约束。
2. Skill 目录仅保留选择所需的名称、短用途及原生读取入口；操作细节让 Agent 按需读具体 Skill。使用既有任务 Skill 引用和完整可发现目录，不按业务关键词裁剪能力，也不复制所有 Skill 正文进上下文。
3. 把长期工作人格精炼成实际执行要点，原完整资料保留为来源文档。精炼版需要逐条审阅，不能每轮重新调用模型摘要，也不能只按字符截断。人格中的现行职责、证据要求和关键业务偏好不可丢失。
4. Runtime Context 保留本轮角色、route/model/thinking、目录、时间/时区和工具入口；缩短重复工具说明。参数及操作细节由本轮原生 tool schema/Skill 提供，不能宣称未知能力或认证成功。
5. 验证 native structured-output schema 是否已进入模型输入后，比较 Developer 内完整 schema 的重复注入方案；字段语义与严格输出模型仍保留单一代码来源，不通过改变 parser 放宽输出。
6. Task 优先去掉重复 serialization 和相同来源的重复块，不截断当前事实、候选、stage、feedback、prior receipts。历史摘要/增量传入属于另一项语义变化，须证明确切事实仍可读取，再纳入固定比较。

### 目标与测量

- 默认 Developer/User 模板应是短原则/组织模板，不再复制整个角色协议。
- 第一轮以相同配置/相同用例的静态 Developer 字符数降低30%以上作为设计目标，力争50%；这是待验收目标，不是已实现收益，也不对动态业务材料设粗暴总字符上限。
- 报告字符数、真实/参考 tokenizer信息、provider usage（如果可得）、first-turn和resume-turn耗时、tool读取增加、反馈轮次及质量失败。没有provider数据时明确缺失。
- 结构合同、事实完整性、候选质量及Audit独立判断不能因精简变差；若某项精简降低质量，保留最小必要信息并记录对照，不用“更短”单独证明更优。
- 精简纳入阶段2的固定评价；阶段1逐字输入一致性与阶段2精简变化分开验收。自然session累积的长历史不与新prompt长度混淆。

## 11. 同一对话对象 session 复用：优先复用现有机制

Derek 明确要求同一个对话对象复用已有session。代码与生产记录显示Consumer已经具备基础逻辑，本计划不建立第二套session映射：

- `ConsumerAgentRunner._consumer_route_sessions` 使用既有持久化 `conversation_id + route_name` 查询session；不是按姓名/群标题或本次task id创建会话。
- `AgentTurnProcess` 保存native session id及route映射；Codex用原生exec resume，Claude在成功路径原子保存自己的session。
- 现有反馈、强制重处理/新execution generation和contract hash变化继续resume；文档明确hash不是新session身份边界。
- Audit在本次审核run的重试使用自己的session，`persist_conversation_session=False`；不能把Consumer session直接交给Audit，否则会破坏角色独立性。
- 本次最近100个completed Consumer run中，有11组“同conversation、同session、不同task”记录。这证明已有复用样本，不证明每个入口和每次fallback都完整覆盖。

### 计划追加任务

- [ ] 冻结复用回归：同conversation连续两个不同task；不同conversation隔离；新generation/feedback/模板或Skill变化保持session；进程重启从库恢复。
- [ ] route切换后分别查原route持久session；返回原route仍resume该route的旧session，不把Codex/Claude session id跨runtime互传。
- [ ] 核对来源到conversation_id的稳定映射：消息、定时、OA/邮件任务若没有同一真实对话对象，不由收件人显示名猜测合并。新增语义映射必须单独列出，不以全局一个session替代。
- [ ] 审计启动失败、session不存在/不可访问、认证上下文失效、fallback与恢复链：仅在已有明确新session条件下创建，并保留lineage及原因；正常配置变化不清空历史。
- [ ] 核对既有SQLite conversation lock和lease：同conversation顺序执行，不同conversation保留并发；不增加全局进程锁。
- [ ] 若发现未持久化、查错route或新task绕开已有映射的具体失败，再在原复用路径最小修复并补回归；没有失败证据就沿用现有代码。
- [ ] 在既有runtime attempt/历史字段中验证“本次新建还是resume、来源session、route、结果session与原因”，优先使用已有事件，避免新session数据库。
- [ ] 验证resume后的输入仍包含本轮触发、修订、来源与变化，不能因Agent记得旧事实而把当前任务省略。

复用session与精简prompt分别测量。resume保留上下文和可能的缓存收益，但不保证provider只处理增量或token成本下降；还可能累积更长历史。本计划不新增按token阈值强制重置session、自动剪裁事实或自建compaction策略。原生compaction能力及可用指标先验证，再决定是否需要独立优化。

新增验收用例的现有测试位置：`tests/test_consumer_agent.py`、`tests/test_agent_turn_runner.py`、`tests/test_agent_runtime_router.py`与现有Claude adapter/Store session持久化测试。部署后读回同conversation的自然连续task及session lineage，不为验收自动发送测试消息或重放历史动作。

## 12. 实施记录（候选阶段，未上线）

- 阶段1已提交 `7b9f08df`，组装前后 Consumer/Audit 输入逐字一致。
- 共同原则、唯一完整任务插槽、一次配置读取、实际输入指纹、同模板预览、旧无调用 renderer 退休及正式默认模板 publication 已实现；定向单元/接口/页面/构建检查已通过，最终候选的模型评价和上线读回待完成。
- 模板保存拒绝不可渲染 Developer、缺失/重复/隐藏 User 插槽；旧 User POST 入口复用同一处理器，不覆盖现有配置。
- 静态规则按唯一权威来源去重；完整 Audit candidate/source_bindings/digest 保留；完全相同来源在同一输入中引用，不同值保留全文。没有改变审核、执行、授权、会话重置政策。
- 固定20个合成用例 v2，baseline/candidate 共用同一短 profile 与任务 Skill override；另外冻结4个 exact source-binding 补充用例。原生 CLI 路线/模型/thinking 相同，禁用业务工具，没有业务重放。原生质量需独立审阅，schema/outcome screen 不能代替。
- 实际已保存 runtime.prompt 只有每角色4个不同任务，无法满足20个真实完整输入样本；缺历史不重新生成。已按结构计数保存私人证据，不导出业务正文。
- v2 候选静态 Developer 字符降幅 Consumer5.32%、Audit12.93%，尚未达到30%设计目标；Audit Task 平均降61.89%。该用例不覆盖标准目录或私人人格，不能扩大结论。
- 工作人格英文精简稿经独立逐条原文审阅，修正限定词后保留现有政策；16324→8820字符，参考 tiktoken0.14.0/o200k_base 3942→1705 tokens。尚未写入线上；私人原文、草稿、覆盖映射及完整模型证据均置于个人工作资料目录，不进入Git。原有数值门槛/handoff 等政策张力没有借精简改写。
- Consumer 的既有 conversation_id+route session 复用保留；新回归证明已保存 Developer/User 改动更新实际输入和契约回执，同时 resume 原session。Audit session 独立。

固定输入/原生模型输出、部署、自然新 invocation 和外部业务结果是不同证据边界。最终验收记录将给出 immutable candidate SHA、质量比较、发布回执及仍未满足的目标。
