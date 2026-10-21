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

Qwen 的六文件 custom-tool 传输修复仅在离线副本及 CPU 语法检查通过，未发布到共享提供商，不能算 Qwen 原生全角色验证。OpenAI API 路线此前有 429 与有限的草稿对照，需要按当前合同重验；Codex OAuth 已有旧合同草稿证据，也不能替代本轮 OA purpose 验收。上述缺口未闭合前，CEO 服务保持原生 schema 关闭，PR #31 保持草稿状态。

## 本轮局部证据（未启用运行时）

- 失败回归先报 `ModuleNotFoundError: app.native_agent_output`；加入当前合同的投影和还原器后，两个尾随非法字符回归又先 RED，确认旧解析器误接受多余 `}` 与零宽字符。原生路径改为严格解析最后一条完整 JSON 后，原生 codec、Consumer purpose 及 OA 编排定向测试 **27 passed**，Ruff 和 `git diff --check` 通过。测试覆盖普通 `no_action`、OA 四种专属动作、Audit `approve/return`、动态 JSON 的嵌套特殊键、空值和重复键拒绝。三份生成 schema 与提交快照逐值一致。
- 本机 Codex CLI `0.163.0`、`gpt-6-luna`，匿名只读 `--output-schema` 冷启动：普通 Consumer `no_action`、OA Consumer `return`、Audit `return` 均有完成输出，当前 Pydantic 还原校验通过。这三次无工具冒烟仅证明 CLI 可接受结构。
- 使用同一虚构 `read_case` MCP（只读，返回 nonce）后，普通 Consumer、OA Consumer、Audit 各完成冷启动及同 session 续会话。六轮每轮精确读取对应的新 case 一次，没有错误调用；续会话的 session ID 与冷启动相同，最终结果引用新 nonce 且通过当前完整 wire model。原始匿名 JSONL 和 MCP trace 临时保留在本任务 `/tmp` 目录，待核对后清理；不纳入仓库或业务记录。
- 这些运行没有实际服务 Developer/Task 装配、完整角色工具目录、Work Profile、业务 Skill 或外部动作；不能代替四类任务固定案例、所有配置路线、生产读回，也不能据此打开运行时 schema。
