# Shared Task Agent Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让普通 Task 提取与 TODO／follow-up 完成检查复用同一条长期 Task Agent 会话，并保证同一时刻只有一轮使用它。

**Architecture:** 两个入口继续使用各自的 Work Item、决策校验和服务端落库逻辑，但向现有 `RoutedCodexExecution` 传同一个稳定 `conversation_id`；不同 `workload_key` 仍保留每次运行的独立审计记录。命令入口在领取输入前取得现有 SQLite session lock，运行期间续租，失锁时禁止应用决策。上下文压缩交给所选运行时的原生能力；当前 Work Item 与数据库始终是事实来源，会话历史只是背景。

**Tech Stack:** Python 3、SQLite `AutoReplyStore`、现有 routed Agent runtime、pytest、Ruff。

---

## 边界和前置条件

- 这是既有 Task-first 改造的后续计划，不包含 Project/TODO 模型改造、生产数据迁移、Agent 工具白名单、自定义压缩器或服务重启。
- 目前两个入口都使用 `session_scope_id=f"task:{active_run_id}"`，所以每次新运行都会得到新会话。`RoutedCodexExecution` 已按 `(conversation_id, route_name)` 保存／恢复 provider session；复用不需要改它的存储模型。不同 fallback route 仍各自有一条物理 session，不能宣称跨 provider 共享一条 transcript。
- 新范围固定为 `task-agent:work-tracking:v1`。旧 `task:<run_id>` 会话及其 run/History 记录保留，不迁移、不覆盖。单次 schema 修复继续使用现有 same-session retry。
- Codex CLI 的自动压缩由 Codex 自己触发；本改动只让它有机会在长期会话中发生，不通过 prompt 要求模型自行执行压缩，也不保证其他 provider 具备同样机制。压缩后仍须从当次 Work Item 和存储的 Task/TODO 状态确认事实。
- 本工作树已有未提交的 Task-first 改动。开工前重新检查 `docs/agent-claims.md`，与当前 owner 协调代码文件，并只提交自己负责的 hunks。完成代码后更新 `docs/architecture.md`、`docs/runtime-mechanism.md`，但不自行重启 `com.ceo-agent-service.main`；依仓库规则交给 heartbeat session 发布并读回。

## 文件地图

| 文件 | 单一职责 |
| --- | --- |
| `app/task_agent_session.py`（新） | 稳定 session scope、跨进程锁的取得／续租／失锁检测／释放。 |
| `app/task_agent.py` | 普通 Task 决策使用稳定 scope；应用前确认仍持有 lease；明确历史仅作背景。 |
| `app/task_completion_agent.py` | 完成检查使用相同 scope 和 lease 规则。 |
| `app/cli.py` | 在领取 Work Item 前持有一条共享 lease，并把 lease 检查传给两个入口。 |
| `tests/test_task_agent_session.py`（新） | lease 占用、续租、失锁、释放的单元测试。 |
| `tests/test_task_agent.py`、`tests/test_task_completion_agent.py` | 两种入口的 scope 和失锁不落库测试。 |
| `tests/test_cli.py` | 双进程竞争时不抢输入、不消耗次数，以及丢锁后的重试测试。 |
| `docs/architecture.md`、`docs/runtime-mechanism.md`、`/Users/derek/.agents/skills/ceo-work-tracking/SKILL.md` | 与运行时和提示词一致的行为说明。 |

## Task 1：固定跨入口会话身份

**Files:** Create `app/task_agent_session.py`; modify `app/task_agent.py`, `app/task_completion_agent.py`; test `tests/test_task_agent.py`, `tests/test_task_completion_agent.py`.

- [ ] 先在两个现有成功案例的假 runner 中记录 `session_scope_id`，各处理一条不同输入，断言两次普通 Task 和一次完成检查得到同一值，且各次 `workload_key` 仍是自己的 `task_agent_runs.id`。示例断言：

```python
assert ordinary_calls[0]["session_scope_id"] == TASK_AGENT_SESSION_SCOPE_ID
assert ordinary_calls[1]["session_scope_id"] == TASK_AGENT_SESSION_SCOPE_ID
assert completion_call["session_scope_id"] == TASK_AGENT_SESSION_SCOPE_ID
assert ordinary_calls[0]["workload_key"] != ordinary_calls[1]["workload_key"]
```

- [ ] 运行 `.venv/bin/pytest -q tests/test_task_agent.py tests/test_task_completion_agent.py`；新断言应失败，显示当前 `task:<run_id>` 范围。
- [ ] 在新模块定义 `TASK_AGENT_SESSION_SCOPE_ID = "task-agent:work-tracking:v1"`；两处 `decide(...)` 用此常量代替 `f"task:{active_run_id}"`，不改 `workload_key`、run 记录和 route 选择。
- [ ] 在两种 prompt 增加同一条明确规则，示例文本：

```text
Prior session turns are background only. Decide this turn from the current
Work Item, current retrieved state, and fresh source evidence. Never cite an
earlier turn as proof of a current assignment, status, deadline, or completion.
```

- [ ] 重跑上述两个测试文件，预期新断言通过，既有 source excerpt／completion evidence 校验仍通过。只提交 Task 1 自己的 hunks。

## Task 2：共享 session 的跨进程 lease

**Files:** Modify `app/task_agent_session.py`; create `tests/test_task_agent_session.py`.

- [ ] 先写测试：Store A 取得 lease 后 Store B 对同一 scope 获取返回 `None`；A 释放后 B 可以取得；模拟续租返回 `False` 时 `assert_owned()` 抛 `TaskAgentSessionLeaseLost`。真实 SQLite 测试使用两个 `AutoReplyStore` 指向同一临时 DB，避免只测进程内 mutex。
- [ ] 运行 `.venv/bin/pytest -q tests/test_task_agent_session.py`；预期因 helper 尚不存在而失败。
- [ ] 实现一个小型 `TaskAgentSessionLease`，只复用现有 `store.acquire_codex_session_lock`、`renew_codex_session_lock`、`release_codex_session_lock`；owner 形如 `task-agent:<pid>:<uuid>`。定义 `TaskAgentSessionLeaseLost(RuntimeError)`。类接口固定为 `try_acquire(cls, store: AutoReplyStore) -> TaskAgentSessionLease | None`、`assert_owned() -> None`、`close() -> None`、`__enter__()` 与 `__exit__()`。`try_acquire` 在 busy 时返回 `None`，不会领取输入。取得后每 60 秒用独立 DB 连接续租，显著短于 Store 的 20 分钟 stale TTL；续租失败或抛异常时将 lease 标为失效。`assert_owned()` 在关键点同步续租并在失锁时抛出专用异常。`close()` 停止并 join 续租线程，再按 owner 释放锁；重复关闭安全。

  实现时不要再建锁表或 token／credential proxy；用 `threading.Event.wait(60)` 让退出立即唤醒。续租线程仅更新锁和失效标志，不在后台应用业务决策。
- [ ] 增加可控短间隔测试：让续租循环使用注入的测试间隔，断言长于一次续租间隔仍独占、关闭后线程已停止。重跑 `tests/test_task_agent_session.py`，预期全部通过。只提交 Task 2 自己的 hunks。

## Task 3：领取前串行化，并在失锁时阻止写入

**Files:** Modify `app/cli.py`, `app/task_agent.py`, `app/task_completion_agent.py`; test `tests/test_cli.py`, `tests/test_task_agent.py`, `tests/test_task_completion_agent.py`.

- [ ] 在 `tests/test_cli.py` 增加 busy-lock 回归：先用第二个 Store 持有 `TASK_AGENT_SESSION_SCOPE_ID` 的锁，再调用 `process_work_items_command`，断言 `processed == 0`、输入仍 `pending`、`attempts == 0`；释放后再调用，断言恰好处理一次。单进程循环和两个 Store 竞争都要覆盖。
- [ ] 在两种 handler 的现有成功案例中注入一个 `assert_owned` 回调：第一次（决定前）通过、第二次（服务端 apply 前）抛 `TaskAgentSessionLeaseLost`；断言没有 business Task／TODO/follow-up 变化，run 为失败并由 CLI 安排输入重试。不要将失败的 provider transcript 当成已应用的业务操作。
- [ ] 运行 `.venv/bin/pytest -q tests/test_cli.py tests/test_task_agent.py tests/test_task_completion_agent.py`，预期新测试先失败。
- [ ] `process_work_items_command` 在 orphan/stale 恢复及 `claim_work_summary_inputs` 之前尝试取得 lease；busy 时打印 `processed=0` 并返回，不碰输入状态。持锁范围覆盖恢复、claim、Agent turn、结果校验、事务落库和本轮 post-apply。示例流程（既有 runner/DWS 构造置于 `with lease` 内、循环之前）：

```python
lease = TaskAgentSessionLease.try_acquire(store)
if lease is None:
    print("process-work-items processed=0", flush=True)
    return 0
with lease:
    for _ in range(limit):
        lease.assert_owned()
        claimed = store.claim_work_summary_inputs(limit=1)
        if not claimed:
            break
        _process_claimed_work_summary_input(
            store, runner, claimed[0], dws=dws,
            session_lease=lease,
        )
```

- [ ] 给 `_process_claimed_work_summary_input` 和两个 handler 加可选 `session_lease` 参数（直接调用的既有测试可不传），在开始 Agent turn 前和 `task_agent_domain_apply_transaction()` 前调用 `session_lease.assert_owned()`。对 `TaskAgentSessionLeaseLost` 使用现有 `schedule_work_summary_input_retry` 路径，并将它归为 transient；不能把并发竞争记成任务语义失败。handler 目前会先标 input 为 `failed` 再抛出，CLI 的 retry 函数允许把该状态改回 `pending`，测试须验证最终状态与次数。
- [ ] 重跑三份测试，预期全部通过；特别检查普通 Task 与完成检查的原有分流、exactly-once 决策和外部 TODO outbox 不变。只提交 Task 3 自己的 hunks。

## Task 4：会话恢复、文档与验收

**Files:** Test `tests/test_routed_codex_execution.py`; modify `docs/architecture.md`, `docs/runtime-mechanism.md`, `/Users/derek/.agents/skills/ceo-work-tracking/SKILL.md`.

- [ ] 在 `tests/test_routed_codex_execution.py` 增加两个不同 `workload_key`、同一 `conversation_id=TASK_AGENT_SESSION_SCOPE_ID` 的测试，断言第二次读取同一 route 的 session ID 并走 provider resume；改用另一路由时读取该 route 自己的 session，不误用第一条。当前 session 不兼容时保留 routed runtime 已有 fresh-session retry 行为。无须启动真实 CLI 或强制填满 context window。
- [ ] 运行 `.venv/bin/pytest -q tests/test_routed_codex_execution.py`，预期新增测试通过；若失败，先检查现有 router 的 session contract hash 与 route-specific 存储，不增加另一层 session registry。
- [ ] 在两份运行文档和 `ceo-work-tracking` Skill 写明：共享逻辑范围、按 route 分开的物理 session、锁先于 claim、续租／失锁重试、当前来源优先、原生自动压缩的边界。文字不得暗示 prompt 已禁止写工具或压缩 transcript 是正式事实存储。
- [ ] 运行聚焦验收：

```bash
.venv/bin/pytest -q tests/test_task_agent_session.py tests/test_task_agent.py tests/test_task_models.py tests/test_task_semantic_service.py tests/test_task_business_resolution.py tests/test_task_completion_agent.py tests/test_todo_completion.py tests/test_follow_up.py tests/test_cli.py tests/test_routed_codex_execution.py tests/test_routed_result_privacy.py tests/test_work_tracking_skill.py
.venv/bin/ruff check app/task_agent_session.py app/task_agent.py app/task_completion_agent.py app/cli.py tests/test_task_agent_session.py tests/test_task_agent.py tests/test_task_completion_agent.py tests/test_cli.py tests/test_routed_codex_execution.py
git diff --check
```

  预期新增测试与先前通过的聚焦测试全部通过、Ruff 和 diff check 无错误；若现有全套测试另有无关失败，单独列出，不混作本功能验收。
- [ ] 手动核对两次输入的数据库记录：各自 `task_agent_runs.id` 不同、两次 `workload_key` 不同、同一 route 的 `conversation_runtime_sessions` 只有稳定 scope 对应的最新 session；旧 run/History 仍可读。生产验证须在负责重启的 heartbeat session 发布后做，不能把本地测试或 commit 当作已上线。

## 自检与交付条件

- [ ] 对照用户决定逐项确认：**一个 Task Agent 逻辑会话**、两类入口共用、跨运行历史可续接、并发串行、原生 compact、当前证据优先、无自建 compactor／工具白名单。
- [ ] 检查计划与实现中的名称一致：`TASK_AGENT_SESSION_SCOPE_ID`、`TaskAgentSessionLease`、`TaskAgentSessionLeaseLost`、`session_lease`；不要把 `workload_key` 也改成固定值。
- [ ] 在 Task-first 改造的最终发布门槛满足前，不执行生产重启。代码提交后按仓库约定通知 `CEO 服务错误检查与修复` heartbeat session，提供 commit、变更文件与读回要求。
