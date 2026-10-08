# Agent 指令精简、按需注入与原生输出修复实施计划

用户已批准完整实施，2026-10-08。沿用现有任务类型、Skill 目录、配置、路线、session 和输入记录；不新增模型路由、授权、安全策略、降级或历史迁移。规则仅适用于本项目，已从共享文件移入本 repo AGENTS.md。

- [x] 建立独立 task-scoped-prompts worktree，保留主检出其他 agent 的脏改动；原生输出 PR31 保持独立。
- [ ] 输入证据：四类任务（日历、邮件、文档、工作跟踪）的角色输入及完整 trajectory，覆盖成功/失败、冷启动/续会话；统计各来源长度与重复/常识/无关内容，不截断事实。
- [ ] 原生定位：比较 Qwen 完整 schema 空结果和成功调用全部轨迹；OpenAI API 容量恢复后做 OFF/minimal/exact 对照；根因、假设、缺失证据分开记录。
- [ ] 指令装配：共同 Developer 仅通用指令，角色边界/业务契约保留；任务 Skill 与专用要求按现有元数据进入每轮 Task；只给名称、用途、读取入口，不全文注入或按关键词路由。能力详细规范通过现有 CLI 按需读取。
- [ ] Settings：解释各 prompt 作用与实际顺序；共享装配函数；公共/任务绑定/历史来源清楚；增加每段来源、位置及脱敏字符长度，旧历史不重建来源；历史实际原文保留。
- [ ] 测试：最窄 RED/GREEN，固定相同模型、事实、工具和 criteria 的四类任务对照，包含跨时区/跨午夜/真实冲突/已处理/续会话/Audit 反馈。检查工具使用、业务语义、完整结果校验、Settings 对应关系及长度变化。
- [ ] 发布：精简、Settings、原生输出分开提交/验收；前两项独立发布，PR31 仅完整配置路线 cold/resume+MCP 有效结果验证通过后发布。文档同提交更新，受影响测试、独立 spec/quality 审查、正式 quiet deploy 与生产 PID/health/queues/Attention/History/Settings 回读。

## 当前证据

先前 native PR31 OAuth 验证与202 passed4 skipped属于原生修复版本，不能代替本次精简验收。codex_api 429控制证明当时capacity失败；Qwen minimal成功但exact无final，警告不是确认根因。私有完整工作证据保存于 /Users/derek/Documents/memory/ceo-agent-service/task-scoped-prompts-20261008/，不发布用户原始内容。

## 实施记录

- 项目规则提交 edda635f；共享文件只移除本次四条，其他规则保持原样。
- Settings sections 元数据接口：name/source（显示就绪）、placement(developer/task)、characters（脱敏文本字符数，不含段间分隔符），数组顺序即装配顺序；不重复保存段全文。
