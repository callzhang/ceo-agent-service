# Task 业务指导去重：当前证据

候选工作树基于 schema 去重提交 b9ac8fb4；业务指导去重与 schema 提交分开，尚未发布。

## 运行与来源边界

2026-10-12 读取生产配置 `.env` 的非敏感路线字段：codex_oauth、codex_api、qwen_gpu4、claude_oauth；未配置 Friday。该读取证明文件配置，不等于观察每条路线实际成功运行。健康接口只读回查为 status=ok。

近期 Task 原生记录显示 Codex OAuth 同一 session 有续接及 runtime_result_validation_failed 修正；在当前本机 Codex 原生目录与索引中找不到抽查记录对应的 transcript 文件。缺少实际输入、工具返回和模型输出，不判断其业务失败原因，不重建或重放历史任务。

## 整段删除不成立

当前安装的 ceo-work-tracking Skill 与仓库 CI Skill 不完全一致。CI 具有而当前安装正文没有的规则包括跨版本报告项合并证明，以及顺序依赖/最近阻塞结果/建议标题中保留关键结果。Task 包装正文仍承载后者，不能因 CI 版本已具备就从运行输入删掉。只读对照还发现包装独有的完整 selector 三态、status/business_relevance 更新 transition、promotion suggestion 省略，以及禁止 skip 成为 Attention 决定成员等约束。

已按下表保留独有规则，移除由当前 Skill 承载的重复包装说明。没有修改 Skill、路由、权限、审核策略、结果模型、本地语义校验或会话机制。生产 prompt 尚未修改。

## 保留与移除依据

| 原包装区域 | 处理与依据 |
| --- | --- |
| Project 定义、CRM、完整职责/事实、Task 归属 | 已在当前 Skill 的 Project context 区承载；完整 Skill 原样加载一次 |
| 人工行动、建议、assignment/acceptance、日期 | 当前 Skill 的 action/suggestion 和 Human assignment/acceptance/date 区承载 |
| Attention 影响判断、实际成员、原证据、负面结果 | 当前 Skill 的 Project judgment 区承载；下述独有规则继续留在包装 |
| 原文引用、历史证明、共享 session、旧操作枚举 | 当前 Skill 的 Original evidence/shared session 与 Service boundary 区承载 |
| assessment 三态选择器 | 保留原包装，尤其未登记线索两者皆无仅用于 insufficient_evidence |
| sequential 依赖、最近阻塞结果、标题关键业务结果 | 保留原包装，当前安装 Skill 未包含完整规则 |
| 类似交付物关联/聚类、status/relevance update_fields 与顶层形状 | 保留原包装 |
| promotion 的 suggestion 省略、owner_evidence JSON 形状 | 保留原包装 |
| Attention decision_indexes 禁止 skip、相同事实无需新提案 | 保留原包装 |

动态时间、Work Item、source visible_ranges、当前检索上下文、scheduled scope 与完整 Skill 前缀逐字符保持一致。安装 Skill 的固定匿名长度比较为 45,278 → 29,726 字符，减少 34.35%；该前缀 SHA256：`38cb981adb08c17482976d8e6f111cc8ec4a6d10f433f9fa75c4858bce703cde`。

## 本地回归和审查

新增回归在修改前因同一引用规则注入两次而失败，修改后通过；测试先移除完整 Skill 再检查包装独有约束，避免 CI Skill 比安装版本丰富而掩盖错误删除。旧字符串断言改为检查完整已加载 Skill 与相同语义，领域模型和业务应用测试不改。

`pytest -q tests/test_task_guidance_dedup.py tests/test_task_agent.py tests/test_task_prompt_schema_delivery.py`：243 passed。相关 Ruff 检查通过。独立只读审查发现并修正截断残句及 CI 掩盖测试问题；独有标题规则进一步加入包装回归断言。

## 原生模型对照范围

固定匿名会议案例：显式创建星桥交付项目，陈明总负责交付、赵华负责商务，验收正常且没有新独立行动项。基线与候选使用同一仓库 CI Skill、来源、模型 gpt-5.6-luna、medium、完整 native output-schema；测试关闭 MCP，未发生外部写入。首次配置键引用导致启动失败，未进入模型，保留为环境记录而不计业务结果。

冷启动两版均输出正确项目、总体负责人和独立职责、零 Task、not_needed、无 Attention；本地完整业务应用均成功，存储回读仍为一个 Project、零 Task、原职责正确。冷启动来源上下文为人工最小全原文夹具；后续输入改用实际 retrieve_task_semantic_context/render_task_semantic_context 生成，不将夹具等同于生产检索验收。只读审查后的候选重新冷启动通过；修正前候选记录保留，不充作修正后证明。

原生完整事件和输入暂存 `/private/tmp/ceo-task-guidance-eval-7_xd_p6r`；摘要回执见 `evals/trajectory_optimization/task_guidance_dedup.results.v1.json`。基线与修正后候选各自冷启动和同 session 续接均通过，四次完整本地业务应用成功。续接复用同一 Project、零 Task、not_needed、无 Attention；总体负责人的原始存储引文逐项相等。续接前均使用同一份已真实应用的项目快照，续接来源与服务生成的检索上下文相同。完整事件另显示基线冷启动读取了一次共享 AGENTS.md，双方均有原生 Skill 目录截短警告；因此不能声称原生附加上下文严格相同或性能因果收益。这些读取与警告未在本次改动中被隐藏或关闭。

该对照不能证明风险建议、跨时区或其他配置提供商均通过，尚不具备全域发布结论。
