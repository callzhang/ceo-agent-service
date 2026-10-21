# Consumer/Audit 原生输出：本轮验收边界

## 要解决的问题与已有证据

Consumer 运行 389361、389061 的完整 trajectory 在嵌套 `payload` 后多输出一个 `}`，触发 `codex_result_invalid` 并增加重试轮次。Audit 389088 含零宽字符；389212 的 JSON 有效，但缺必需的 `feedback`。这三类现象分开归因；本轮只尝试用原生结构约束减少前两类格式错误，不以 schema 掩盖缺字段的业务校验。旧结果解析器会截取第一个完整对象，可能忽略尾随的非法字符；原生路径必须严格解析最终 JSON，避免把格式缺陷报成成功。

生产 Codex Consumer/Audit 目前关闭 `--output-schema`。草稿 PR #31 的结构投影只绑定普通 Consumer；当前服务还使用 OA 专属 Consumer，其动作包括 `approve`、`reject`、`return`、`comment`。草稿快照也早于当前 Pydantic 合同，不能直接启用。

## 有限完成条件

1. 从**当前** Pydantic wire model 分别生成普通 Consumer、OA Consumer 和 Audit 的原生结构 schema；固定快照与生成结果一致，OA 四个动作不能消失。动态 JSON 对象及嵌套值可无损还原，空值和特殊键名保持原样。还原后仍由原有完整 Pydantic 业务合同校验，非法动作、缺少 Audit feedback 和候选绑定错误继续拒绝。
2. 先有失败回归，再做最小格式投影和调用接线；不增加业务 prompt 规则、路由 fallback、关键词特例或新的审核政策。续会话使用与当前角色、Consumer purpose 一致的 schema；不确定时不能把旧 schema session 当作新合同继续。
3. 固定匿名案例覆盖普通 Consumer 的候选与 `no_action`、OA 的专属动作、Audit `approve` 与 `return`；每条配置中的 Codex 路线都要有 Consumer/Audit 有效冷启动与同一 session 续会话，且各完成指定的只读 MCP 调用。固定业务案例同时核对判断、工具使用和候选绑定，不能只看 JSON 有效。失败冷启动不计续会话通过。
4. 受影响本地测试、固定案例和独立审查通过后，按仓库流程合并、部署；生产读回提交、PID、健康、队列、Attention、History 和 Settings 的实际输入。只有这些证据齐备才称为发布完成。

## 当前尚缺

Qwen 的六文件 custom-tool 传输修复仅在离线副本及 CPU 语法检查通过，未发布到共享提供商，不能算 Qwen 原生全角色验证。`codex_api` 当前匿名 schema-on/off 对照均无有效冷启动输出，具体失败层未查实；此前的 429 也不能据此解释为 schema 缺陷。Codex OAuth 的局部合同与工具传输检查不能替代完整业务案例。上述缺口未闭合前，CEO 服务保持原生 schema 关闭，PR #31 保持草稿状态。

## 本轮局部证据（未启用运行时）

- 失败回归先报 `ModuleNotFoundError: app.native_agent_output`；加入当前合同的投影和还原器后，两个尾随非法字符回归又先 RED，确认旧解析器误接受多余 `}` 与零宽字符。原生路径改为严格解析最后一条完整 JSON 后，原生 codec、Consumer purpose 及 OA 编排定向测试 **27 passed**，Ruff 和 `git diff --check` 通过。测试覆盖普通 `no_action`、OA 四种专属动作、Audit `approve/return`、动态 JSON 的嵌套特殊键、空值和重复键拒绝。三份生成 schema 与提交快照逐值一致。
- 本地增加原生还原结果到现有角色解析器的最小适配函数：Consumer 的 purpose 转换、Audit 的 revision/digest 绑定及解析器拒绝继续走原业务回调，不另写一套业务判断。新增测试先因函数缺失 RED；完成后原生模块 **15 passed**、Ruff 与 diff 检查通过。此时 `AgentTurnProcess` 仍关闭原生 schema，适配函数尚未进入生产调用路径。
- 本机 Codex CLI `0.163.0`、`gpt-6-luna`，匿名只读 `--output-schema` 冷启动：普通 Consumer `no_action`、OA Consumer `return`、Audit `return` 均有完成输出，当前 Pydantic 还原校验通过。这三次无工具冒烟仅证明 CLI 可接受结构。
- 使用同一虚构 `read_case` MCP（只读，返回 nonce）后，普通 Consumer、OA Consumer、Audit 各完成冷启动及同 session 续会话。六轮每轮精确读取对应的新 case 一次，没有错误调用；续会话的 session ID 与冷启动相同，最终结果引用新 nonce 且通过当前完整 wire model。原始匿名 JSONL 和 MCP trace 核对后已从本任务 `/tmp` 目录清理；未纳入仓库或业务记录。
- GPU4 的 vLLM 0.29 **候选** custom-tool 代码经只读 SSH stdin 在内存中加载；当前三份 schema 分别有 21／21／11 个顶层字段，Qwen 结构语法均接受完整最终 JSON 和 custom `exec` 调用分支。此检查没有模型推理、已安装包替换或 slot 重载，不能算共享提供商修复与原生全角色运行通过。
- `codex_api` 使用已配置路线、匿名普通 Consumer schema 和无工具输入只发送一次；100 秒上限结束前仅出现 `thread.started`、`turn.started`，没有错误事件、429 或模型最终输出。随后以同一路线、模型、匿名输入和 100 秒上限做一次**关闭 schema** 的对照，也只得到相同的两个开始事件，标准错误为空。两次冷启动都没有有效结果，当前 API 路线的无响应不能归因于 schema；具体失败层仍未查实。没有在失败冷启动后测试续会话，也没有自动重试。
- 另以当前精简 Pydantic wire schema 作为 Developer，对同一无工具虚构 OA 退回案例做原生 schema on/off 冷启动对照。两次都完成且通过各自格式与完整业务模型校验；原生格式中的嵌套动作 `target`/`payload` 无损恢复。但事实输入没有目标环节 ID，两次都自行填了该字段（原生为 `resubmit`，关闭 schema 为 `resubmission`）。因此**两次业务判断均未通过**，不能把结构有效当作 OA 验收，也没有证据将编造标识归因于原生 schema。后续固定案例必须核对标识来自实际来源，不因 JSON 有效而放行；不针对这个单例添加 prompt 补丁。
- 这些运行没有实际服务 Developer/Task 装配、完整角色工具目录、Work Profile、业务 Skill 或外部动作；不能代替四类任务固定案例、所有配置路线、生产读回，也不能据此打开运行时 schema。
