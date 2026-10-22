# Codex Consumer 原生输出验证

## 问题与验收

生产 task 416807 的 Consumer 在 2026-10-22 连续三次因 `codex_result_invalid` 失败。三份完整轨迹均显示模型先结束一个不完整的 JSON 对象，再把 `evidence_refs`、`summary`、风险及质量分数、`durable_memories` 等必填字段留在对象之外；严格解析器因此报告必填字段缺失。没有外部动作被执行。结果解析约定要求无效 JSON 保持无效，不能从尾部拼接字段修复。

候选修复让 Codex Consumer 直接使用现有 general/OA purpose wire model 派生的原生输出 Schema。Schema 将 action、target、payload 作为单一 `decision` 对象，并保留 action-specific target/payload 分支；自由 JSON 对象以键值条目表示，在原有 wire/Pydantic 校验前还原。验收要求：

- 旧的扁平、不完整结构不能满足新 Schema；完整原生输出可以还原为原有 wire/domain 结果。
- 动态 JSON 的特殊键、嵌套对象、数组、空值、布尔和数字值保持不变，错误或重复键仍拒绝。
- 只对 Codex Consumer 使用原生 Schema，依据任务已有 purpose 选择 general/OA；Audit、Claude、Friday、业务判断、审核和外部动作行为保持不变。
- 对已完成运行的恢复身份绑定实际 Schema 字节，不能把旧自由格式结果当作新格式结果恢复。
- 在配置路线上以相同匿名固定案例完成 Consumer 冷启动与同 session 续会话，并通过业务判断、来源读取、结构化结果和无外部发送校验后，才可进入发布。

## 本地验证

回归先于实现运行，因缺少 `app.native_agent_output` 在收集阶段失败。实现后：

- `tests/test_native_consumer_output.py` 覆盖 general/OA action 集、Schema 快照与当前 wire model 一致、扁平旧格式被拒绝、完整结果 round-trip、动态 JSON 特殊键/嵌套值及损坏后缀拒绝。
- 最新定向验证：`tests/test_native_consumer_output.py tests/test_agent_turn_runner.py` 为 45 passed；Consumer schema 选择、resume identity 和合法/损坏结果接线为 8 passed。完整 `tests/test_consumer_agent.py` 为 173 passed、4 skipped、3 failed。三个失败与已记录基线一致：两个 action projection 使用不存在的 `lease_owner` 列，一个现存 capability-section 长度断言要求不超过 3,556 字符、实际为 4,211；本次没有修改这些查询、提示或断言。
- `ruff check` 覆盖所有本次变更的 Python 文件通过，`git diff --check` 通过。

测试执行器只在命令确实携带本候选的 Consumer Schema，且原脚本结果符合现有 wire model 时，把有效 fixture 编码为原生格式；无效 fixture 保持原样，避免测试夹具掩盖解析拒绝。

## 匿名原生 CLI 验证与未完成验收

生产 checkout 的 `.env`（只读筛出非凭据配置项）配置 `CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api,qwen_gpu4,claude_oauth`、`CEO_CODEX_MODEL=gpt-6-luna`。本地 shell 的默认 `codex_oauth` 模型则是 `gpt-5.5`；CLI 对它返回 HTTP 400，提示该模型不能用于 ChatGPT Codex OAuth。该次没有得到模型结果，所以不是 Schema 失败，也不能作为通过。

为验证原生输出通道，随后显式选择服务允许的 `gpt-5.6-sol` 和 `gpt-5.6-luna`，使用本候选 Consumer Schema、空 Work Profile、合成输入、`read-only` sandbox、关闭 user config/hooks/apps/multi-agent，且没有业务 MCP 配置：

- `gpt-5.6-sol` 冷启动返回严格有效的 `no_action`，同 session `continue` 也返回有效 `no_action`。
- `gpt-5.6-sol` 的合成回复案例冷启动返回有效 `plan`，精确保留虚构会话/消息 ID 和回复正文，未调用消息发送或邮件工具。其完整 trajectory 只有两次本地只读 `exec`：读取共享 Agent 规则与 DingTalk chat Skill、查看可用操作目录；没有业务 MCP 调用或外部效果。对同 session 发 `continue` 后，Consumer 根据上一轮已有提案返回 `no_action`，没有重复提案。
- 与生产配置一致的 `codex_oauth` / `gpt-6-luna` 使用完整 Consumer 角色命令、general 原生 Schema 和共享外发约束，读取由本地动作合同目录实现的只读假 MCP。冷启动返回通过现有 Consumer parser 的 `proposal`，唯一候选为虚构群 `demo-conversation-2026` 的 `dingtalk-chat/send_group_message`，事实均引用 `synthetic:meeting-note-1`；完整 JSONL 只有该只读合同调用，没有其他工具调用。沿用同一 session 续会话后返回 `no_action`，无候选、无 MCP 调用。该案例验证的是合同读取、候选结构、外发约束和 session 行为；没有连接 DWS，也没有执行真实发送。
- `gpt-5.6-luna` 合成冷启动和同 session 续会话均返回有效 `no_action`，没有外部工具调用；冷启动仅读取共享 Agent 规则，续会话没有工具调用。
- 与生产 `.env` 一致的 `codex_oauth` / `gpt-6-luna` 在 general 与 OA Schema 下分别通过匿名 `no_action` 冷启动和同 session 续会话，四个结果都通过当前对应的 Consumer Pydantic parser，未发生外部工具调用。

这些匿名运行只使用 Codex OAuth、合成输入、空 Work Profile、只读本地目录及假只读 MCP；没有连接 `codex_api`、Qwen、Claude、业务 MCP 或生产记录。原先一次 proposal 探针没有可用假 MCP 调用，后续按真实 Consumer 角色配置重新验证后已通过；前一次失败的具体 harness 原因没有足够证据，故不作归因。部署和生产回读仍未完成。

## 安全与范围

共享 `CONSUMER_OUTBOUND_INSTRUCTION` 继续用于所有 Consumer 类型，包括通用、Meeting 和 WeChat：Consumer 不直接发送对外消息或邮件，受控动作仍由原服务流程审核及执行。没有新增任务专属发送规则、Audit 原生 Schema、解析修补、fallback、动作权限或外部操作。
