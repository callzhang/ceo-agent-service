# 数据库副本：真实 Codex CLI 任务链路验收（2026-10-12）

本次验收通过：在生产数据库的在线一致性副本上，新建一个回复任务，使用真实 Consumer 和 Audit Codex CLI 完成执行与审查，再由实际 SystemExecutor 调用内存发送替身一次。业务结果、执行回执、任务完成、输入精简和重新打开数据库后的终态读取均通过。真实对外发送次数为 0。

这是一个使用合成业务材料的真实模型端到端案例，不是生产消息回放，也不证明真实钉钉投递。前一次 11 个脚本模型案例的失败、恢复等覆盖见 `database-copy-lifecycle-acceptance-20261012.md`；本次不将这些分支宣称为真实模型重复验证。

## 被测版本和隔离边界

- 生产运行版本与本次被测 runtime：`06c08020773978b613d3a94dc4b9642e4b0cc2ef`。
- 隔离工作树：`/Users/derek/.codex/worktrees/database-redesign-plan/ceo-agent-service`；本分支合并生产版本后，`app/` 与该版本没有差异。
- Codex CLI：`0.154.0`；路线 `codex_oauth`；模型 `gpt-5.6-luna`，与现场生产所选模型一致。
- 保留原生 `~/.codex` 认证与会话存储，没有复制凭据、修改原生配置或保存第二份 trajectory。
- 源数据库经只读连接和 SQLite backup 得到一致性副本；模型与 worker 仅绑定候选副本。
- 使用真实 role builders、ConsumerAgentRunner、AuditAgentRunner、AgentOrchestrator、SystemExecutor、原生消息 handler、DingTalkAutoReplyWorker 和 Store。
- 替换边界为测试材料上下文、消息读取/桌面通知/处理反应，以及外部消息 provider。模型结果、合同解析、审查、执行状态、业务结果与回执持久化均使用真实实现。
- 本次 CLI 仅开放任务绑定的 `read_task_artifact` 与 `read_system_action_contract`，关闭实际继承的外部 MCP servers；保留 Consumer/Audit 原生角色 sandbox。运行时源码和生产配置没有修改。

## 源数据

| 项目 | 值 |
| --- | --- |
| 源副本字节数 | 471,347,200 |
| tasks 行数 | 25,238 |
| runs 行数 | 219,589 |
| Store schema | 2026-10-12.1 |
| Email schema | 51 |
| 源副本 SHA-256 | `4d761cc6e307667148566d6ae2391ff9054e9bd0cc8d47ac073b1a87bcaed3a1` |

Email schema 51 是邮件数据库结构的迁移版本号，不是邮件数量或模型版本。

## 创建、执行、审查和结果

| 检查点 | 实测结果 |
| --- | --- |
| 新任务创建、领取 | reply task 404352；pending → processing |
| Consumer 读取和计算 | 原生工具读取本任务 case-input.json 和 send_group_message 契约；3 条记录、units 总和 100、材料中随机 verification_tag 均进入候选正文 |
| Audit 独立读取 | 原生工具重新读取相同材料和动作契约，给出 approve |
| 审查与候选绑定 | candidate 465、review 457；revision 0；批准的 digest 与候选 digest 完全一致 |
| 系统执行 | 实际 SystemExecutor 调用消息发送替身 1 次，替身回执验证 1 次 |
| 必要结果持久化 | external_action_results 1 条、sent_replies 1 条、reply_attempts 1 条，均属于新测试身份 |
| 完成和输入精简 | worker 返回完成，task.status=done；input_compacted=true，input_provenance_json 保留 |
| 原生轨迹引用 | 两个逻辑 Agent run 及各自 runtime attempt 均保存实际原生 session_id 与有效行区间；runs 无已删除的 final_result_json 列 |
| 重开数据库 | persisted_terminal_result 返回 executed；未再次调用模型或 provider |

批准的候选摘要为 `d27b9e155484b339338741fd386e7059298c14a2de0c6db3271f51ad081675c0`。

| 角色 | 逻辑 run | runtime attempt | 原生 session_id | 行区间（零起点、右端不含） | 状态 |
| --- | --- | --- | --- | --- | --- |
| consumer | 350566 | 350567 | `01a133cc-cd5b-7063-a3c3-10144755ce05` | [0, 32) | completed |
| audit | 350568 | 350569 | `01a133cd-364d-7fe3-b198-c2bdffbd751b` | [0, 47) | completed |

两个角色的材料读取均成功返回 267 字节，动作契约读取均成功返回 4,147 字节。事后从实际原生会话行区间核对工具调用和返回，没有使用伪造 transcript 或脚本模型输出。

原始轨迹仅在 Codex 自己的存储中：

- `/Users/derek/.codex/sessions/2026/10/12/rollout-2026-10-12T22-47-02-01a133cc-cd5b-7063-a3c3-10144755ce05.jsonl`
  文件字节 SHA-256：`9998c0667738442f802208da7dcda07046286329301eac3ead44c3b14919a9bd`。
- `/Users/derek/.codex/sessions/2026/10/12/rollout-2026-10-12T22-47-29-01a133cd-364d-7fe3-b198-c2bdffbd751b.jsonl`
  文件字节 SHA-256：`dc21c75aa819cb3b6e8d202669b76a717cd47962b8e94a4a3d6c5679d801ba50`。

## 历史数据与生产隔离

- 131 张原有应用表逐表以源副本 `EXCEPT` 候选副本检查，原有行和字段没有被删除或改写。新验收行允许新增；SQLite 内部计数器及 FTS 虚拟/影子表不参加原始行比较。
- 候选数据库 quick_check=ok；源副本 SHA-256 在验收前后不变。
- foreign_key_check 与原有副本结果完全一致，未增加问题。原有 finding 为 runs 物理行 190042 引用缺失 tasks 404225；本次验收没有修复该历史问题，也不把数据库表述为无任何历史外键问题。
- 生产库只读核对：native-copy-acceptance 测试任务 0 条、native-copy-provider-receipt 回执 0 条、上述两个原生 session_id 的 runs 0 条。
- 最后读取的生产 HEAD 仍为 `06c08020773978b613d3a94dc4b9642e4b0cc2ef`；healthz 返回 ok。

## 验收中出现的问题

1. 测试初始化继承服务 MCP manifest，因缺少 MEMORY_CONNECTOR_URL 在模型启动前失败。测试需要的是绑定副本的本地工具，已改为仅供本次使用的空服务 manifest，保留角色 builder 注入的本地 MCP。该次没有模型或 provider 调用。
2. 测试限制工具时给未配置的 exa server 添加 disabled 条目，Codex 仍校验其 transport，导致 Consumer 启动失败。修正验收脚本为只关闭实际配置/继承的 server；没有修改产品实现。两个启动失败没有产生模型会话或发送。
3. 成功链路中 Audit 一次试图从 functions.exec 调用仅支持直接调用的本地 MCP，出现 TypeError。Agent 随后自主改用直接 MCP，成功读取材料和契约并批准候选。这是一次可恢复的工具调用错误；不能宣称整个原生轨迹零错误。
4. 最后校验使用了中间检查过程生成的规范化 JSON 行摘要，与文件原始字节摘要混淆，导致一次验收证据校验断言失败。重新按文件原始字节 SHA-256 核对，两个文件均与首次记录的 native_sha256 一致，行数也与数据库区间一致；上文仅记录原始字节摘要。

## 自动检查与可复用材料

- 测试脚本：`scripts/validate_native_copy_lifecycle.py`。输入必须是本次拥有的临时目录，内含 source.sqlite3 和 source-meta.json；candidate.sqlite3 必须尚不存在。脚本会运行真实付费/配额模型会话，外部 provider 始终为替身。
- 配置限制回归：`tests/test_native_copy_lifecycle_harness.py`，最初复现失败后修正，两项通过。
- 本次相关检查：native harness、orchestrator、system executor、reviewed candidate store、reply input reference，共 **108 passed in 8.30s**。脚本与回归文件 Ruff 检查通过。
- 本次没有 runtime、schema、迁移或部署修改；没有在生产创建测试任务或触发真实发送。

## 临时材料清理

已在结果和生产隔离核对后删除本次拥有的 `/private/tmp/ceo-native-copy.a0fv1n`，包含源副本、候选副本、两次初始化失败副本与合成材料。清理前文件合计 1,885,397,594 字节。仅保留本文、可复用验收脚本和回归检查；原生 Codex 会话保留在 `~/.codex`。
