# Codex 原生 session 复用与逐轮 Developer 更新方案

状态：2026-10-07，根因和合成原生探针已确认；传输接口实施待讨论。此方案不改变角色、业务审核、授权或执行政策。

## 已确认问题

预期：同一真实 conversation + route 继续同一原生 session，每轮采用服务已冻结的当前 Developer/profile/Runtime Context。实际：服务把当前全文传给 `exec resume -c developer_instructions=...`，但安装的 Codex0.154.0 保留初始 Developer。Settings/数据库 prepared 快照只能证明服务构造并交给运行适配器的内容。

自然 Consumer24867 与上一不同 Task 的 Consumer24864 复用相同 conversation/route/session；新 profile 出现在服务快照，未出现在原生 Developer。独立 fresh Audit24868 的原生 Developer 包含新 profile。没有业务重放、发送或日历测试动作。

## 可复现原生实验

模型 gpt-5.6-luna、medium、串行，合成输入，无工具调用，使用原生认证与 home。

| 原生调用 | 同 session 的要求 | 实际输出 | 结论 |
| --- | --- | --- | --- |
| fresh exec Developer | BEFORE | BEFORE | 初始配置有效 |
| exec resume 新 Developer | AFTER | BEFORE | resume 更新无效 |
| 全局 -c 再 exec resume | AFTER | BEFORE | 改参数位置无效 |
| app-server thread/resume developerInstructions | AFTER_RPC | BEFORE | resume 参数仍无效 |
| turn/start additionalContext application | AFTER_CONTEXT | AFTER_CONTEXT | 新原生 Developer 生效 |
| 同 key 再更新 application context | AFTER_SECOND | AFTER_SECOND | 连续更新生效 |
| 再更新指令读取最早 assistant 结果 | BEFORE | BEFORE | 同 thread 历史可读取 |

最后三项的原生 transcript 确认消息角色为 developer，session ID 不变。中间一次诊断脚本把原生 reasoning item 误当工具而终止，其 aborted turn 保留；修正脚本后完整更新/历史读取探针通过。没有把该终止算成业务/服务失败。application context 在原生历史中追加消息，既有 Developer 与历史仍保留；并不删除旧文本，也不证明长会话 token 下降。

## 取舍

| 方案 | 收益 | 代价与判断 |
| --- | --- | --- |
| 沿用 exec resume | 当前调用方式与 session 复用保持 | 当前版本无法满足配置更新，只能明确说明限制 |
| 配置变化后创建新 session | 新配置可进入初始 Developer | 丢失同原生 session 连续性，与已批准要求冲突，不采用 |
| 改用原生 app-server stdio 的逐轮 application context | 同原生 thread 更新 Developer，历史继续可读，原生认证/home/MCP 保持 | 需要交互式 RPC 进程及事件映射，属于调用接口变更；推荐独立实施和验收 |

官方 app-server 文档描述 initialize、thread/start/resume 和 turn/start。安装版本生成的 JSON Schema 明确提供 additionalContext 的 application 类型；具体字段行为以上述本机原生实验为证，不能只凭文档断言生产支持。探针启用 experimentalApi，正式接入前验证该字段是否需要此能力及所选版本契约。

## 推荐的具体改动

1. 新建小型 Codex app-server 协议驱动，直接启动同一原生 `codex app-server --stdio`，每次服务 invocation 使用 initialize→thread/start 或 thread/resume→turn/start。需要保留 stdin 直至 turn/completed，现有 ProcessRunner 一次写入后关闭 stdin 的协议不适用。复用现有 timeout/进程组终止语义，不加入新的业务失败重试策略。
2. CodexRuntimeAdapter 把现有模型、thinking、provider、MCP inline配置、Skill选择和 native home/env 传给原生接口。保持现有 conversation+route session 查找及持久化，不按模板/hash 重置；不新建 route/session 数据库或更换凭据。
3. 新/旧 thread 均通过一个固定 source key 的 application context 提交本轮完整冻结 Developer，Task仍是原生 user input；不把 Developer 拼成 User，不手工删除或编辑 native JSONL，不建立凭据代理。
4. 把原生 thread/turn/item 的必要事件映射到现有 session、进度、工具、usage、完成和失败事件。保留现有 wire parser/result/revision/route fallback；不新增审核、确认、授权、reconciliation或状态政策。未知协议行为在验收阶段查清，不能悄悄回退 exec 路径。
5. Runtime Context 从实际配置/工具事实生成，不依赖 exec 命令字符串猜测；现有 runtime.prompt 同时记录冻结 Developer/Task和实际 native application-context提交结构。继续区分 prepared、适配器返回和原生 transcript。Settings的模板渲染维持同一来源，历史只读。
6. 仅接入后台 Consumer/Audit Codex 路线；Workbench、独立 WeChat、其他显式 exec 调用、Claude/Friday 不随之切换。

## 实施前的失败回归与接受条件

- 原生集成回归：fresh→resume配置变更→再次变更→读取最早历史，session完全相同、Developer确为原生developer消息；旧exec路径在更新断言失败。
- 协议驱动针对 initialize/resume/start、stdout/stderr、reasoning、agentMessage、MCP/工具、structured result、错误、timeout及正常退出的最小测试。使用原生权限与现有服务政策，不新增授权处理层。
- Consumer/Audit真实调用回归确认当前 profile、共同原则、动态Runtime Context进入实际RPC；两角色分离，conversation/route恢复不变。
- 保持固定模型/cases/settings，比较原exec与新接口的完整服务输入和原生输出。覆盖既有失败日历rollover、source binding、反馈及同session配置更新。不得用parser或一个合成输出替代业务质量。
- 独立代码/协议复核、定向本地测试与隔离CI；更新架构/运行文档在同一行为提交。
- 正式deploy后，用自然新invocation读回同conversation/route/session及新Developer的原生transcript；核对PID/health/queues/Attention/History。未到这一步不宣称生产修复，不为验收发送外部消息。

## 决策原因

共享 AGENTS 要求遵循现有设计、不要增加系统复杂度，除非更好的方案先与 Derek 讨论。此问题无法通过参数调整解决，原生逐轮context的方案已做到可复现和可审阅；在确认这个独立调用接口改动之前，只完成诊断、方案和现有发布的真实状态记录。
