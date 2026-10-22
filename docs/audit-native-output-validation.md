# Codex Audit 原生输出格式验证

本次只处理 Codex Audit 的结构化输出传输。当前 main 的完整 Audit trajectory 曾出现零宽字符污染 JSON，以及 `return` 结果缺少必填 `feedback`；后者已由既有严格 Audit wire/Pydantic parser 拒绝，但模型格式失败会导致该轮无法完成。Consumer 原生输出修复已在当前 main，本次不改 Consumer 结构或行为。

Audit 使用现有 `AuditAgentWireResult` 生成 Codex 原生 Schema。为满足 Codex 最外层必须是 object 的限制，`outcome` 与 `feedback` 放在 `decision` 对象中，服务解析后还原成原 wire 结构。候选 revision/digest 仍从当前 `AuditTurnContext` 注入，候选字段断言仍经过原校验；业务审核和反馈规则没有变化。动态 JSON 值使用键值条目表示，并在严格 Pydantic 验证前还原。

验证记录：

- `tests/test_native_audit_output.py`、`tests/test_native_consumer_output.py`、`tests/test_agent_turn_runner.py`、`tests/test_audit_agent.py`：82 passed。
- Ruff 覆盖变更的五个 Python 文件，`git diff --check` 通过。
- 本机 Codex CLI 0.163.0 的只读匿名探针：首次尝试把条件 `oneOf` 放在最外层时，API 明确拒绝该 Schema；移入 `decision` 后，gpt-6-luna 冷启动生成完整 `return + feedback`，同一 session 续会话生成 `approve + feedback=null`。两轮没有调用工具或业务系统。
- 对冷启动返回的结果用服务端 `parse_native_result` 和当前 Audit parser 做本地回放，得到有效 return，revision 与 digest 由服务端注入。
- 生产配置的 `qwen_gpu4 / qwen3.8-27b / medium` 对照探针：三次使用原生 Schema 的隔离运行都未产生结果，完整轨迹显示模型调用 `exec_command`、`node_repl` 只读仓库查询，以及 Codex 未提供的 `request_user_input` / `update_goal`；没有连接业务 MCP 或执行外部动作。移除 Schema 并隔离用户工具后，同一服务 API 路线返回普通 JSON 并完成。结论是该服务 API 提供商尚未通过原生 Schema 验证；Audit 只在已验证的本地 OAuth 路线上选用原生 Schema，API 路线继续沿用原解析方式。

这证明 Codex OAuth / gpt-6-luna 的原生 schema 接受、冷启动和续会话格式；尚未证明其他 service API 提供商兼容性，也不是生产部署或生产回读证据。
