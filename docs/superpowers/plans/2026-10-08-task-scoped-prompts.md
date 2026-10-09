# Agent 指令精简、按需注入与原生输出修复实施计划

用户已批准完整实施，2026-10-08。沿用现有任务类型、Skill 目录、配置、路线、session 和输入记录；不新增模型路由、授权、安全策略、降级或历史迁移。规则仅适用于本项目，已从共享文件移入本 repo AGENTS.md。

- [x] 建立独立 task-scoped-prompts worktree，保留主检出其他 agent 的脏改动；原生输出 PR31 保持独立。
- [ ] 输入证据：四类任务（日历、邮件、文档、工作跟踪）的角色输入及完整 trajectory，覆盖成功/失败、冷启动/续会话；统计各来源长度与重复/常识/无关内容，不截断事实。
- [ ] 原生定位：比较 Qwen 完整 schema 空结果和成功调用全部轨迹；OpenAI API 容量恢复后做 OFF/minimal/exact 对照；根因、假设、缺失证据分开记录。
- [x] 指令装配实现：共同 Developer 仅通用指令，角色边界/业务契约保留；任务 Skill 与专用要求按现有元数据进入每轮 Task；只给名称、用途、读取入口，不全文注入或按关键词路由。能力详细规范通过现有 CLI 按需读取。最终场景与生产验收另列下方。
- [x] Settings 实现：解释各 prompt 作用与实际顺序；共享装配函数；公共/任务绑定/历史来源清楚；增加每段来源、位置及脱敏字符长度，旧历史不重建来源；历史实际原文保留。最终部署与生产回读尚未完成。
- [x] 本地与受控验收：最窄 RED/GREEN，固定相同模型、事实、工具和 criteria 的四类任务对照，包含跨时区/跨午夜/真实冲突/已处理/续会话/Audit 反馈。14次原生调用+必要2次真实反馈闭环后验，独立代码与业务审查通过；保留首审漏报及原始分数。生产回读另列发布门槛。
- [ ] 发布：精简、Settings、原生输出分开提交/验收；前两项独立发布，PR31 仅完整配置路线 cold/resume+MCP 有效结果验证通过后发布。文档同提交更新，受影响测试、独立 spec/quality 审查、正式 quiet deploy 与生产 PID/health/queues/Attention/History/Settings 回读。

## 当前证据

先前 native PR31 OAuth 验证与202 passed4 skipped属于原生修复版本，不能代替本次精简验收。codex_api 控制请求返回 HTTP 429，具体容量、配额或限流原因未查实，不作为 schema 缺陷。私有完整工作证据保存于 /Users/derek/Documents/memory/ceo-agent-service/task-scoped-prompts-20261008/，不发布用户原始内容。

输入统计覆盖403份已保存的 runtime.prompt；完整真实 trajectory 当前只确认日历样本，不能用快照统计替代四类任务的完整轨迹。其余类别使用固定匿名场景补充验证，并分别注明生产证据与受控证据。

Qwen 当前 CodeModeOnly 的工具缺失已定位到工具格式：Codex 将 MCP 包装为 custom/freeform exec，而当前 vLLM Responses 转换保留 function 工具、过滤 custom 工具。关闭 CodeMode 的候选虽然绕开此缺失，MCP 在 wire 上仍是 NamespaceTool；隔离 Consumer/Audit 冷启动与续会话四格均 schema 合法但零 MCP，全部失败。该候选未提交或发布。第二层原因经原探针请求和 provider 源码确认：vLLM 对真实 Codex 的 auto + strict=false namespace 工具保留纯最终 JSON grammar，不能同时生成 Qwen tool-call 语法。旧 strict=true 顶层 function 对照触发的工具 grammar 在内部清除了 text.format，因此其合法 JSON 只是事后验证，不是 provider 约束与工具兼容的证明。永久修复需在 provider 中组合工具与最终 schema grammar；本 repo 未引入降级、强制工具或补充 prompt 规则，PR31 保持未发布。

没有有效结果的冷启动不得将续会话计入通过。早期固定场景运行发现 fixture 缺少依赖工具、read_skill 参数与真实接口不一致，已保留并标记失效；不能作为最终对照验收。最终指令对照须先冻结实际装配输入与真实工具 schema，两臂保持同一已验证 OAuth 原生工具模式。

## 实施记录

- 项目规则提交 edda635f；共享文件只移除本次四条，其他规则保持原样。
- Settings sections 元数据接口：name/source（显示就绪）、placement(developer/task)、characters（脱敏文本字符数，不含段间分隔符），数组顺序即装配顺序；不重复保存段全文。
- 独立审查发现两处调用链缺陷，正在补回归修复：安装 Skill 入口须提交真实 read_skill(path)；通用 Scheduled Agent 须保存并按需读取冻结正文，不能在 Task 中全量展开。显式自定义任务约定与自动生成目录通过结构化来源区分。
- 安装目录解析须只读取 name/description 元数据，不能因无关的嵌套 metadata.requires 隐藏真实产品 Skill。依赖闭包注入实验被拒绝：日历41条/约1.45万字符，候选输入反而更长。最终入口沿用 read_skill：无选择器只返回 name/use/path 目录；给一个 name 或 path 精确读取；不把目录或依赖正文预先展开到 Task。目录、名称与读取回执仍遵守原授权路径范围，不新增策略。
- 最终 SDK 实现 adb23e1b；此前4d77969f包含并发暂存的P1修复与预览serializer测试，未重写历史。接口、冻结原文、实际来源回执的最终受影响测试分别14/39/10/4项通过，不能替代模型场景或生产验收。实际 Audit 只读目录337项均不含正文，日历/文档/聊天名称与路径读取相同 SHA；未指定任务入口263字符、冻结日历入口850字符、冻结文档714字符。等待独立最终审查和一次冻结后的完整对照。
- 最终源码/模型验收：ba3d5415为测试断言对齐，runtime仍adb23e1b；联合四模块70项、完整定时模块36项通过，独立code review批准。固定6冷启动场景共215702→158018字符（-26.74%），14/14完整Pydantic有效。候选初审6pass/1partial：Audit正确return阻止旧坏计划，但漏报payload字段。随后直接用真实feedback，Consumer R1自然读取合约并修正为content，原Audit session复审精确revision/digest后approve；无源码/prompt补丁、无动作执行，独立闭环审查批准。公共匿名casepack SHA9e6bb578...保持不变，原始14call评分不覆盖。
- 原生Qwen升级准备：官方上游2e06395...与FUNCTION strict floor通过真实Responses/Qwen/xgrammar离线组合grammar门槛，原两份schema hash未改；当前生产provider仍0.29.0，未用这个unit PASS冒充 served-runtime/model或API路线通过，PR31继续draft。
