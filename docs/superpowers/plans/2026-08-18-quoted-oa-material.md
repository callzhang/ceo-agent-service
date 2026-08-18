# 群聊引用 OA 审批卡片材料绑定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当群聊指令本身没有 OA 链接但引用的审批卡片包含精确流程与任务标识时，把该卡片绑定为当前 Agent 可见、可追溯的 `dingtalk_oa` 材料，同时保持现有来源优先级且不自动执行审批。

**Architecture:** 只修改 `DingTalkAutoReplyWorker._agent_material_references` 的 OA 材料选择边界：先使用任务已持久化 URL，再使用当前消息正文 URL，最后才使用引用内容 URL。引用来源必须同时解析出 process/task ID，并用被引用消息 ID 作为 `source_message_id`；不完整引用只保留在消息上下文，不生成部分 OA 材料。

**Tech Stack:** Python 3.12、pytest、Pydantic `DingTalkMessage`、现有 `extract_oa_url` 与 `MaterialReference` 契约、Markdown 文档。

---

## 文件结构

- Modify: `app/worker.py` — 在现有 OA 材料构建段选择 URL 来源并绑定准确的来源消息 ID。
- Modify: `tests/test_agent_runtime_worker.py` — 覆盖引用卡片成功绑定、来源优先级、不完整引用降级及无服务端预读/写入。
- Modify: `docs/reply-worker-reliability.md` — 记录引用审批卡片的材料选择边界与非自动执行原则。

不创建新模块、数据表、API、路由或兼容层。现有代码已经在一个方法内集中构建所有材料，本次只对其中 OA 小段做最小修改。

### Task 1: 以失败回归测试定义引用 OA 材料契约

**Files:**
- Modify: `tests/test_agent_runtime_worker.py`

- [x] **Step 1: Write the failing integration test**

在 `test_oa_material_does_not_recover_target_from_historical_context` 之前加入：

```python
def test_oa_material_binds_exact_target_from_quoted_approval_card(tmp_path: Path):
    quoted_url = (
        "https://aflow.dingtalk.com/detail"
        "?procInstId=quoted-proc&taskId=quoted-task"
    )
    trigger = _message("@CEO Agent 请审阅这条审批").model_copy(
        update={
            "quoted_message_id": "quoted-oa-1",
            "quoted_content": f"[OA 审批] {quoted_url}",
        }
    )
    worker, runner, dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION, summary="Review only."))],
    )
    _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    material = next(
        item for item in runner.calls[0][2].materials if item.kind == "dingtalk_oa"
    )
    assert json.loads(material.reference) == {
        "process_instance_id": "quoted-proc",
        "task_id": "quoted-task",
        "url": quoted_url,
    }
    assert material.source_message_id == "quoted-oa-1"
    assert material.read_commands == (
        ".venv/bin/python -m app.cli read-oa-approval-detail "
        "--instance-id quoted-proc",
        "dws oa approval tasks --instance-id quoted-proc --format json",
    )
    assert dws.forbidden_material_reads == []
    assert len(runner.calls) == 1
```

该测试同时证明：worker 只交付精确引用和读取命令，没有在构建上下文时预读审批，也没有因为识别链接自动执行审批；测试 runner 返回 `no_action` 后任务正常完成。

- [x] **Step 2: Run the test to verify it fails for the missing material**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_agent_runtime_worker.py::test_oa_material_binds_exact_target_from_quoted_approval_card \
  -q
```

Expected: FAIL at `next(...)` with `StopIteration`, because current OA extraction only reads `message.content` or `task.oa_url`.

- [x] **Step 3: Commit the red regression test**

```bash
git add tests/test_agent_runtime_worker.py
git commit -m "test: reproduce quoted OA material gap"
```

### Task 2: Implement exact source selection and conservative fallback

**Files:**
- Modify: `app/worker.py:2567-2624`
- Test: `tests/test_agent_runtime_worker.py`

- [x] **Step 1: Add source precedence and quoted completeness checks**

Replace the existing OA loop body with:

```python
        for message in (trigger,):
            task_oa_url = (
                task.oa_url.strip()
                if message.open_message_id == trigger.open_message_id
                else ""
            )
            message_oa_url = extract_oa_url(message.content)
            quoted_oa_url = extract_oa_url(message.quoted_content or "")
            if task_oa_url:
                oa_url = task_oa_url
                oa_source_message_id = message.open_message_id
                oa_from_quote = False
            elif message_oa_url:
                oa_url = message_oa_url
                oa_source_message_id = message.open_message_id
                oa_from_quote = False
            elif quoted_oa_url:
                oa_url = quoted_oa_url
                oa_source_message_id = (
                    message.quoted_message_id or message.open_message_id
                )
                oa_from_quote = True
            else:
                oa_url = ""
                oa_source_message_id = message.open_message_id
                oa_from_quote = False

            process_instance_id = self._oa_process_instance_id_from_url(oa_url)
            task_id = self._oa_task_id_from_url(oa_url)
            raw_process_id, raw_task_id = self._raw_oa_identifiers(
                message.raw_payload
            )
            if not oa_from_quote:
                process_instance_id = process_instance_id or raw_process_id
                task_id = task_id or raw_task_id
            if oa_from_quote and not (process_instance_id and task_id):
                continue

            if process_instance_id:
                detail_command = (
                    ".venv/bin/python -m app.cli "
                    "read-oa-approval-detail --instance-id "
                    + shlex.quote(process_instance_id)
                )
                tasks_command = (
                    "dws oa approval tasks --instance-id "
                    + shlex.quote(process_instance_id)
                    + " --format json"
                )
                reference = json.dumps(
                    {
                        "process_instance_id": process_instance_id,
                        "task_id": task_id,
                        "url": oa_url,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                add(
                    "dingtalk_oa",
                    reference,
                    oa_source_message_id,
                    (detail_command, tasks_command),
                )
            elif oa_url or self._is_oa_approval_message(message):
                reference = json.dumps(
                    {
                        "process_instance_id": process_instance_id,
                        "task_id": task_id,
                        "url": oa_url,
                        "original_reference": message.content,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                add(
                    "dingtalk_oa",
                    reference,
                    oa_source_message_id,
                    (),
                )
```

Do not add a second parser, regex, storage field, or automatic approval action.

- [x] **Step 2: Run the red test and verify it passes**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_agent_runtime_worker.py::test_oa_material_binds_exact_target_from_quoted_approval_card \
  -q
```

Expected: `1 passed`.

- [x] **Step 3: Add precedence and incomplete-reference boundary tests**

Add after the success regression:

```python
@pytest.mark.parametrize(
    ("task_oa_url", "message_text", "expected_process_id"),
    [
        (
            "https://aflow.dingtalk.com/detail"
            "?procInstId=persisted-proc&taskId=persisted-task",
            "@CEO Agent 请处理",
            "persisted-proc",
        ),
        (
            "",
            "@CEO Agent 请处理 "
            "https://aflow.dingtalk.com/detail"
            "?procInstId=content-proc&taskId=content-task",
            "content-proc",
        ),
    ],
)
def test_oa_material_keeps_task_or_message_source_ahead_of_quote(
    tmp_path: Path,
    task_oa_url: str,
    message_text: str,
    expected_process_id: str,
):
    trigger = _message(message_text).model_copy(
        update={
            "quoted_message_id": "quoted-oa-2",
            "quoted_content": (
                "https://aflow.dingtalk.com/detail"
                "?procInstId=quoted-proc&taskId=quoted-task"
            ),
        }
    )
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION))],
    )
    _enqueue(worker.store, trigger, oa_url=task_oa_url)

    assert worker.consume_once(max_tasks=1) == 1

    material = next(
        item for item in runner.calls[0][2].materials if item.kind == "dingtalk_oa"
    )
    assert json.loads(material.reference)["process_instance_id"] == (
        expected_process_id
    )
    assert material.source_message_id == trigger.open_message_id


def test_oa_material_ignores_incomplete_quoted_target(tmp_path: Path):
    trigger = _message("@CEO Agent 请审阅").model_copy(
        update={
            "quoted_message_id": "quoted-oa-incomplete",
            "quoted_content": (
                "https://aflow.dingtalk.com/detail?procInstId=quoted-proc"
            ),
        }
    )
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(ScriptOutcome.NO_ACTION))],
    )
    _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 1
    assert all(
        material.kind != "dingtalk_oa"
        for material in runner.calls[0][2].materials
    )
```

- [x] **Step 4: Run the focused OA material tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent_runtime_worker.py \
  -k "oa_material or oa_trigger_without_exact_process_id" -q
```

Expected: all selected tests pass, including the existing historical-context and ambiguous direct-trigger regressions.

- [x] **Step 5: Run lint for changed Python files**

Run (this checkout's virtual environment does not install Ruff; use the configured machine-level executable):

```bash
/Users/derek/miniforge3/bin/ruff check \
  app/worker.py tests/test_agent_runtime_worker.py
```

Expected: no lint errors.

- [x] **Step 6: Commit the implementation and boundary tests**

```bash
git add app/worker.py tests/test_agent_runtime_worker.py
git commit -m "fix: bind quoted OA approval material"
```

### Task 3: Document reliability semantics and verify the repository

**Files:**
- Modify: `docs/reply-worker-reliability.md:92-97`

- [x] **Step 1: Update the OA material reliability paragraph**

Append to the paragraph beginning `OA 待办扫描产生的是合成 trigger`:

```markdown
群聊触发本身没有 OA 链接时，worker 只在被引用消息含有可同时解析的
process/task ID 时补充同一种 `dingtalk_oa` 材料，并把来源绑定到被引用消息 ID。
任务已持久化链接和当前消息正文始终优先；不完整引用只保留为对话上下文，不拼接当前
trigger 的原始字段，也不生成审批动作。识别引用卡片本身不会触发同意、拒绝、退回或评论。
```

- [x] **Step 2: Run documentation and diff checks**

Run:

```bash
git diff --check
rg -n "引用消息|被引用消息|不完整引用" docs/reply-worker-reliability.md
```

Expected: `git diff --check` prints nothing; `rg` prints the new reliability paragraph.

- [x] **Step 3: Run focused worker suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_agent_runtime_worker.py \
  tests/test_worker.py \
  tests/test_oa_approval.py \
  -q
```

Expected: all tests pass.

- [x] **Step 4: Run the full repository test command**

Run:

```bash
npm test
```

Expected: backend and frontend suites pass with no new failures. Record exact counts in the handoff.

- [x] **Step 5: Commit documentation**

```bash
git add docs/reply-worker-reliability.md
git commit -m "docs: explain quoted OA material precedence"
```

- [ ] **Step 6: Verify commit scope before service reload**

Run:

```bash
git diff --check HEAD~3..HEAD
git status --short --branch
git log --oneline -4
```

Expected: the three feature commits contain only the planned test, implementation, and documentation files. Only the worktree-local `.venv` and `frontend/node_modules` symlinks may remain untracked.

- [ ] **Step 7: Restart and verify the live service after runtime commits**

Before restart, inspect claimed reply tasks, work-summary inputs, meeting jobs and persisted external actions for resumability/idempotency. Then run the project-required service reload:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: a new running PID. Read back queue state and external-action reconciliation; report no new unresolved `failed`, `processing`, claimed, or ambiguous work. If any in-flight item is not safely resumable, do not restart and report the exact blocker instead.
