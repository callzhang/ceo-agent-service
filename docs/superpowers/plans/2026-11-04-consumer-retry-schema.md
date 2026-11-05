# Consumer Retry Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除服务要求 Agent 返回一种格式、随后又拒绝该格式的 retry 协议矛盾。

**Architecture:** 保持 canonical Consumer 合同为唯一来源，只把动作的三个字段投影到原生 decision。证据引用保留在根层，现有解析、权限引用和等待预算验证继续使用原接口。

**Tech Stack:** Python 3.12+、Pydantic、JSON Schema、pytest、现有 Codex 原生输出适配器。

**Spec:** `../specs/2026-11-04-oa-review-corrections-design.md` 的 B 与实施约束。

## Global Constraints

- 业务规则位于 Skill，不在 Python 中写人名、实例 ID 或审批类别特例。
- 实现不按 Codex、Claude、Friday 路由分出不同业务规则。
- 每个测试进程保持 2 GB、600 秒限制。代码通过既有 `python -m app.deploy` 门禁发布。
- 沿用现有版本化 Skill、原任务 Consumer→Audit→System 执行及回执机制。
- 在新鲜 origin/main worktree 中实施，执行时使用 using-git-worktrees Skill，先读认领表再认领具体文件。

## Review Focus

- general 与 OA 的 retry 都必须被生成的 Schema 接受，归 Task 1 的参数化回归。
- 证据引用缺失仍应被完整合同拒绝，归 Task 1 的根字段负例。
- 普通 plan、no_action 与 Audit 格式不得改变，归 Task 1 的现有全文件测试。
- 旧的错误嵌套字段不得被静默丢弃，归 Task 1 的生产形状负例。
- 权限等待预算耗尽时不能重新申请或无限重试，归 Task 2 的原任务恢复核验。

---

## 文件与接口

实现基线为 `/private/tmp/ceo-calendar-wechat-repairs-20261103` 的 `88733879b`。实施时从最新 main 建工作树，并重新核对以下位置。

- Modify `app/native_agent_output.py::native_output_schema`。只调整 Consumer decision 字段投影。
- Regenerate `app/schemas/consumer_general_agent_native_output.schema.json` 与 `app/schemas/consumer_oa_agent_native_output.schema.json`。
- Test `tests/test_native_consumer_output.py`。现有 `_consumer`、`_event`、`native_output_schema` 已可复用。
- Update `docs/runtime-mechanism.md` 中原生输出合同的现有说明。
- Use `tests/test_consumer_purpose_retry.py` 验证等待与引用行为，已有测试足够时不增加重复测试。

### Task 1. 修复 Schema 投影并证明生成与解析一致

**Interfaces**

- Consumes `consumer_wire_model(purpose)` 生成的 canonical Schema。
- Produces 既有 `native_output_schema(role: str = "consumer", *, purpose: str = "general") -> dict`。签名不变。
- `parse_native_output(raw: str, role: str = "consumer", *, purpose: str = "general") -> str` 继续只接受三个 decision 字段。

- [ ] **Step 1. 写参数化回归 `test_native_retry_schema_and_parser_agree`。** purpose 取 `general/oa`。用 `_consumer("retry", purpose=purpose)` 构造有效输入，断言 decision 只有三个键，`Draft202012Validator(schema).validate(native)` 通过，`parse_native_output(_event(native), purpose=purpose)` 得到 `action="retry"` 且根 `evidence_refs=["source:1"]`。
- [ ] **Step 2. 运行红测。** `python -m pytest -q tests/test_native_consumer_output.py -k native_retry_schema_and_parser_agree`。旧版本应因生成 Schema 要求 `decision.evidence_refs` 失败，不能只接受一般导入错误。
- [ ] **Step 3. 实现最小修复。** 在 `native_output_schema` 的 Consumer 分支中，decision 的 properties 只投影 canonical 分支的 `action/target/payload`；根层 evidence_refs 原样保留。canonical Schema 里的 retry 最少引用条件仍由原完整模型验证，不把它搬进 decision。
- [ ] **Step 4. 增加合同负例。** `test_native_retry_requires_root_evidence` 用空根引用断言 ResultParseError；`test_native_retry_rejects_nested_evidence` 在 decision 放入生产错误中的 evidence_refs，断言仍拒绝额外字段。两个 purpose 都覆盖。
- [ ] **Step 5. 重新生成两份原生 Schema。** 使用 `native_output_schema(purpose=purpose)` 和仓库现有 JSON 排版保存到 `native_output_schema_path(purpose=purpose)`，不手改生成 JSON。
- [ ] **Step 6. 运行验证。** `python -m pytest -q tests/test_native_consumer_output.py tests/test_agent_turn_runner.py tests/test_consumer_purpose_retry.py`。要求全部通过，包含已存在的持久 Schema 等价测试、引用匹配和预算负例。
- [ ] **Step 7. 更新原生输出说明、移除自己的 claim，提交。** `git add` 仅上述源文件、两份 Schema、测试、文档与自己的 claim hunk；提交名 `fix: keep retry evidence at Consumer root in native schema`。

### Task 2. 发布并恢复李芳菲的原任务

**Interfaces**

- Consumes Task 1 已提交且推送的精确 SHA，以及正式 History 重试入口。
- Produces 部署回读与真实任务结果。权限服务仍拒绝时，结果应准确体现依赖状态和剩余预算。

- [ ] **Step 1. 推送后正式部署。** 从干净实现树运行 `python -m app.deploy --target-commit <Task 1 完整 SHA>`，等待 gate 和 quiet 完成。
- [ ] **Step 2. 读回生产版本、PID、healthz、Status、Attention 和 History。** 只在生产确实包含该提交后继续。
- [ ] **Step 3. 重新读取 Task 418317、Attempt 16781 和源 OA 当前节点。** 若仍为相同 failed 任务且无后续完成，调用 `POST /api/console/history/16781/retry` 一次；已覆盖或正在运行时观察原任务。
- [ ] **Step 4. 核对权限等待路径。** 原生产轨迹的预算已经 `closed=true,count=3`，不得将它重新置零。由系统按现有耗尽处理分支形成真实候选；若依赖已恢复则正常读取。正常恢复不应再因 decision 结构失败，也不重复权限申请评论。
- [ ] **Step 5. 等原任务进入可解释的终态或正式等待，检查 Consumer/Audit、原回执及 OA 待办归属。** 简洁记录代码版本、是否仍有真实权限阻塞、是否发生外部动作和凭什么确认。

## 自审结果

Spec B 的字段位置、完整合同和原等待预算均有任务覆盖。无需兼容旧错误 decision，也无需修改 Audit 规则或添加新 API。该修复可独立发布。
