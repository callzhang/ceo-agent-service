# Agent-Owned Material Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move DingTalk document, minutes, and ordinary file reading decisions from the worker into the ordinary CEO agent, while keeping calendar and image preprocessing in the worker.

**Architecture:** The worker remains the router, state machine, duplicate guard, delivery executor, calendar preprocessor, and image input preparer. For DingTalk documents, AI minutes, and file references, the worker will only extract material references and inject explicit DWS tool-use instructions into the prompt; the agent decides whether to read, which materials to read, and how to judge after reading. Agent-side DWS calls stay visible through existing Codex audit tool event capture.

**Tech Stack:** Python 3.12, SQLite, pytest, Codex CLI JSON sessions, DWS CLI, FastAPI audit UI.

**Implementation status:** Tasks 1-7 are implemented on branch `codex/agent-owned-material-reading`. Task 8 remains final verification, service restart, backlog check, and push.

---

## File Structure

- Modify `app/prompt.py`
  - Add a `MaterialReferenceContext` dataclass.
  - Replace the pre-read `linked_documents` prompt input with `material_references`.
  - Render a new `material_references_block` containing URL/file/minutes references and exact DWS usage instructions.
  - Keep `image_download_block` unchanged.

- Modify `app/user_prompt_blocks.py`
  - Replace the template block named `linked_documents_block` with `material_references_block`.
  - Keep backward compatibility only inside tests is not needed; update references directly.

- Modify `app/worker.py`
  - Stop calling `_read_linked_documents()` before ordinary agent decisions.
  - Add `_material_references()` to extract DingTalk doc URLs, AI minutes IDs/URLs, and ordinary file references from current and context messages.
  - Keep `_collect_image_paths()` as worker preprocessing.
  - Keep calendar and OA flows unchanged.
  - Remove or demote linked-document failure handling so document permissions do not fail the worker before agent execution.
  - Keep existing helper methods that are still used by tests until the final cleanup task removes unused code.

- Modify `app/codex_runner.py`
  - Add explicit developer instructions that the CEO agent may use DWS read-only material commands for documents, minutes, and files, must use JSON output, and must not expose secrets.
  - Keep `--ignore-user-config` and memory connector config behavior unchanged.

- Modify `tests/test_prompt.py`
  - Replace linked-document prompt tests with material-reference prompt tests.
  - Keep image prompt tests unchanged.

- Modify `tests/test_worker.py`
  - Update tests that previously expected worker-side document reads.
  - Add tests proving the worker does not call `dws.doc_info`, `dws.read_doc`, or `dws.minutes_info` before Codex for ordinary messages.
  - Add tests proving material references reach the prompt.
  - Keep calendar/OA/image preprocessing tests unchanged.

- Modify `tests/test_codex_runner.py`
  - Add coverage that the developer instructions include DWS material-reading guidance and keep memory connector isolation.

- Modify `docs/reply-worker-reliability.md`
  - Document the boundary: worker preprocesses calendar/images; agent reads documents/minutes/files through DWS when needed.

---

### Task 1: Add Material Reference Prompt Model

**Files:**
- Modify: `app/prompt.py`
- Modify: `app/user_prompt_blocks.py`
- Test: `tests/test_prompt.py`

- [ ] **Step 1: Write the failing prompt test**

Append this test near the current linked-document prompt tests in `tests/test_prompt.py`:

```python
def test_build_turn_prompt_includes_material_references_for_agent_reading():
    prompt = build_turn_prompt(
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="CEO-2 管理群",
            single_chat=False,
            unread_point=1,
        ),
        [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="CEO-2 管理群",
                single_chat=False,
                sender_name="韩露",
                create_time="2026-06-08 18:46:32",
                content="@Alex Chen(明哥) 看第二份材料",
            )
        ],
        [],
        style_lines=[],
        include_thread_prompt=False,
        material_references=[
            MaterialReferenceContext(
                kind="dingtalk_doc",
                reference="https://alidocs.dingtalk.com/i/nodes/doc123?utm_scene=team_space",
                source_message_id="msg-1",
                source_sender="韩露",
                source_time="2026-06-08 18:46:32",
            ),
            MaterialReferenceContext(
                kind="dingtalk_minutes",
                reference="7632756964333134343836383736303334325f3435313431363430365f35",
                source_message_id="msg-1",
                source_sender="韩露",
                source_time="2026-06-08 18:46:32",
            ),
        ],
    )

    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert "类型: dingtalk_doc" in prompt
    assert "dws doc info --node" in prompt
    assert "dws doc read --node" in prompt
    assert "类型: dingtalk_minutes" in prompt
    assert "dws minutes get info --id" in prompt
    assert "如果判断依赖材料正文，必须先读取材料" in prompt
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_prompt.py::test_build_turn_prompt_includes_material_references_for_agent_reading -q
```

Expected: FAIL with `NameError: name 'MaterialReferenceContext' is not defined` or `TypeError: build_turn_prompt() got an unexpected keyword argument 'material_references'`.

- [ ] **Step 3: Implement the prompt model**

In `app/prompt.py`, replace the existing linked-document dataclass block:

```python
@dataclass(frozen=True)
class LinkedDocumentContext:
    url: str
    title: str
    markdown: str
```

with:

```python
@dataclass(frozen=True)
class LinkedDocumentContext:
    url: str
    title: str
    markdown: str


@dataclass(frozen=True)
class MaterialReferenceContext:
    kind: str
    reference: str
    source_message_id: str
    source_sender: str
    source_time: str
```

Keep `LinkedDocumentContext` for the transition because calendar and existing tests may still import it during this task.

- [ ] **Step 4: Add the material reference argument and renderer**

In `app/prompt.py`, change the `build_turn_prompt()` signature to include `material_references`:

```python
def build_turn_prompt(
    conversation: DingTalkConversation,
    new_messages: list[DingTalkMessage],
    context_messages: list[DingTalkMessage],
    *,
    style_lines: list[str],
    include_thread_prompt: bool,
    linked_documents: list[LinkedDocumentContext] | None = None,
    material_references: list[MaterialReferenceContext] | None = None,
    image_download_errors: list[str] | None = None,
    known_people_lines: list[str] | None = None,
    sender_org_lines: list[str] | None = None,
) -> str:
```

After the existing `linked_documents_block` construction, add:

```python
    material_references_block = ""
    if material_references:
        material_lines: list[str] = [
            "待读取材料（由 agent 判断是否读取）:",
            (
                "如果判断依赖材料正文，必须先读取材料；如果消息正文已经足够，可以不读取。"
                "读取失败时不要臆测材料内容，应说明权限或材料问题。"
            ),
            "DWS 读取命令提示:",
            "- 钉钉文档: dws doc info --node <URL> --format json；需要正文时 dws doc read --node <URL> --format json",
            "- AI 听记: dws minutes get info --id <MINUTES_ID> --format json",
            "- 普通文件: 先用消息中的文件名和上下文判断是否需要读取；需要时使用 DWS 文件/云盘能力查询或下载。",
        ]
        for index, material in enumerate(material_references, start=1):
            material_lines.extend(material_reference_lines(index, material))
        material_references_block = _prompt_section_block(
            material_lines,
            trailing_newline=True,
        )
```

In the `render_user_prompt()` mapping, add:

```python
            "material_references_block": material_references_block,
```

Add this function below `linked_document_lines()`:

```python
def material_reference_lines(
    index: int, material: MaterialReferenceContext
) -> list[str]:
    return [
        f"- 材料{index}:",
        f"  类型: {material.kind}",
        f"  引用: {_shorten_url(material.reference)}",
        f"  来源消息: {material.source_message_id}",
        f"  发送人: {material.source_sender}",
        f"  时间: {material.source_time}",
    ]
```

- [ ] **Step 5: Update the user prompt block registry**

In `app/user_prompt_blocks.py`, add a new block after `linked_documents_block` for the transition:

```python
    UserPromptBlock(
        name="material_references_block",
        expression="app.user_prompt_blocks:material_references_block()",
        description="待读取的钉钉文档、AI听记、普通文件引用；没有材料时为空。",
        default=(
            "待读取材料（由 agent 判断是否读取）:\n"
            "如果判断依赖材料正文，必须先读取材料；如果消息正文已经足够，可以不读取。\n"
            "DWS 读取命令提示:\n"
            "- 钉钉文档: dws doc info --node <URL> --format json；需要正文时 dws doc read --node <URL> --format json\n"
            "- AI 听记: dws minutes get info --id <MINUTES_ID> --format json\n"
            "- 材料1:\n"
            "  类型: dingtalk_doc\n"
            "  引用: https://alidocs.dingtalk.com/i/nodes/example\n"
            "  来源消息: msg-1\n"
            "  发送人: Mina\n"
            "  时间: 2026-06-08 10:00:00"
        ),
    ),
```

Add the accessor near the existing block accessors:

```python
def material_references_block() -> str:
    return _block("material_references_block")
```

- [ ] **Step 6: Update the developer prompt template references**

Run this search to locate the template block order:

```bash
rg "linked_documents_block|image_download_block|context_messages_block" app tests
```

In the user prompt template registry order in `app/user_prompt_blocks.py`, place the material reference block immediately after `context_messages_block` and before `linked_documents_block`. In tests that assert the template block list, add this literal entry in the same position:

```text
<code: app.user_prompt_blocks:material_references_block()>
```

The resulting block order must be:

```text
<code: app.user_prompt_blocks:context_messages_block()>
<code: app.user_prompt_blocks:material_references_block()>
<code: app.user_prompt_blocks:linked_documents_block()>
<code: app.user_prompt_blocks:image_download_block()>
```

- [ ] **Step 7: Run the prompt test**

Run:

```bash
.venv/bin/python -m pytest tests/test_prompt.py::test_build_turn_prompt_includes_material_references_for_agent_reading -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

Run:

```bash
git add app/prompt.py app/user_prompt_blocks.py tests/test_prompt.py
git commit -m "feat: add agent material reference prompt block"
```

Expected: commit succeeds with only those files staged.

---

### Task 2: Extract Material References Without Reading Them

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing worker test**

Add this test near `test_dingtalk_doc_link_is_read_before_codex` in `tests/test_worker.py`:

```python
def test_dingtalk_material_links_are_passed_to_codex_without_worker_reading(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im"
    minutes_id = "7632756964333134343836383736303334325f3435313431363430365f35"
    trigger = message(
        "\n".join(
            [
                f"文档: {doc_url}",
                f"听记: dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId={minutes_id}&from=8",
                "@Alex Chen(明哥) 判断这个材料是否能推进",
            ]
        )
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先读材料再判断")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert dws.minutes_info_calls == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert "https://alidocs.dingtalk.com/i/nodes/doc123" in prompt
    assert minutes_id in prompt
    assert "dws doc read --node" in prompt
    assert "dws minutes get info --id" in prompt
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_dingtalk_material_links_are_passed_to_codex_without_worker_reading -q
```

Expected: FAIL because the worker still calls `doc_info` / `read_doc` or because no `material_references` block exists.

- [ ] **Step 3: Import the new prompt model**

In `app/worker.py`, change:

```python
from app.prompt import LinkedDocumentContext, build_turn_prompt
```

to:

```python
from app.prompt import (
    LinkedDocumentContext,
    MaterialReferenceContext,
    build_turn_prompt,
)
```

- [ ] **Step 4: Add material reference extraction**

In `app/worker.py`, add this method near `_read_linked_documents()`:

```python
    def _material_references(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[MaterialReferenceContext]:
        references: list[MaterialReferenceContext] = []
        seen: set[tuple[str, str]] = set()
        referenced_messages = self._referenced_document_messages(
            new_messages, context_messages
        )

        def add(kind: str, reference: str, message: DingTalkMessage) -> None:
            key = (kind, reference)
            if not reference or key in seen:
                return
            seen.add(key)
            references.append(
                MaterialReferenceContext(
                    kind=kind,
                    reference=reference,
                    source_message_id=message.open_message_id,
                    source_sender=message.sender_name,
                    source_time=message.create_time,
                )
            )

        for message in referenced_messages:
            for text in (message.content, message.quoted_content or ""):
                for match in DINGTALK_DOC_URL_PATTERN.finditer(text):
                    add("dingtalk_doc", self._canonical_doc_url(match.group(0)), message)
                for match in DINGTALK_SHANJI_DOC_SELECTOR_PATTERN.finditer(text):
                    task_uuid = self._minutes_task_uuid_from_selector_url(match.group(0))
                    add("dingtalk_minutes", task_uuid, message)
                for match in DINGTALK_MINUTES_LINK_PATTERN.finditer(text):
                    task_uuid = self._minutes_task_uuid_from_url(match.group(0))
                    add("dingtalk_minutes", task_uuid, message)
            for file_name in self._referenced_file_names([message], []):
                add("dingtalk_file", file_name, message)
        return references
```

- [ ] **Step 5: Stop ordinary worker material pre-reading**

In `_process_batch()` in `app/worker.py`, replace:

```python
        try:
            linked_documents = self._read_linked_documents(
                material_messages, context_messages
            )
            image_paths, image_download_errors = self._collect_image_paths(
                material_messages,
                context_messages,
            )
        except Exception as exc:
            handled = self._record_linked_document_error(
                conversation,
                trigger,
                exc,
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            if raise_on_delivery_failure and not handled:
                raise ReplyTaskProcessingError(str(exc)) from exc
            return
```

with:

```python
        material_references = self._material_references(
            material_messages,
            context_messages,
        )
        image_paths, image_download_errors = self._collect_image_paths(
            material_messages,
            context_messages,
        )
        linked_documents: list[LinkedDocumentContext] = []
```

This keeps image preprocessing but removes worker-side document/minutes/file reads from the ordinary path.

- [ ] **Step 6: Pass material references into prompt building**

In both calls to `self._build_prompt()` inside `_process_batch()`, add:

```python
            material_references=material_references,
```

Update `_build_prompt()` signature in `app/worker.py`:

```python
    def _build_prompt(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
        *,
        include_thread_prompt: bool = True,
        linked_documents: list[LinkedDocumentContext] | None = None,
        material_references: list[MaterialReferenceContext] | None = None,
        image_download_errors: list[str] | None = None,
    ) -> str:
```

Pass it through to `build_turn_prompt()`:

```python
            material_references=material_references,
```

- [ ] **Step 7: Run the worker test**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_dingtalk_material_links_are_passed_to_codex_without_worker_reading -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

Run:

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: pass DingTalk material references to agent"
```

Expected: commit succeeds.

---

### Task 3: Keep Calendar and Image Preprocessing Working

**Files:**
- Modify: `tests/test_worker.py`

- [ ] **Step 1: Add calendar regression test**

Add this new test to `tests/test_worker.py` near the existing calendar invite tests:

```python
def test_calendar_invite_still_injects_calendar_context_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程] OpenAI 合作讨论", message_type="calendar")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.calendar_events = [
        DwsCalendarEvent(
            event_id="event-1",
            title="OpenAI 合作讨论",
            start_time="2026-06-08 20:00:00",
            end_time="2026-06-08 21:00:00",
            organizer_name="韩露",
            response_status="needsAction",
            raw={"description": "讨论 OpenAI 合作主叙事"},
        )
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="日程已处理",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "OpenAI 合作讨论" in codex.calls[0][0]
    assert "讨论 OpenAI 合作主叙事" in codex.calls[0][0]
```

- [ ] **Step 2: Add image regression test**

Add this new test to `tests/test_worker.py` near the existing image download tests:

```python
def test_image_download_failure_is_still_passed_to_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 看图判断", message_id="msg-image-1")
    trigger.raw_payload = {
        "content": "[图片消息](mediaId=@lALPM6d...)",
        "messageType": "image",
        "extension": {"mediaId": "@lALPM6d"},
    }
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.image_download_error = DwsError("resource download unavailable")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="图片无法读取，请发可查看版本。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    prompt = codex.calls[0][0]
    assert "图片读取状态:" in prompt
    assert "resource download unavailable" in prompt
```

- [ ] **Step 3: Run calendar/image focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py -k "calendar_invite_still_injects_calendar_context_before_codex or image_download_failure_is_still_passed_to_codex" -q
```

Expected: PASS with exactly the two new tests selected.

- [ ] **Step 4: Commit Task 3**

Run:

```bash
git add tests/test_worker.py
git commit -m "test: preserve calendar and image preprocessing"
```

Expected: commit succeeds.

---

### Task 4: Add Ordinary Agent DWS Material Tool Guidance

**Files:**
- Modify: `app/codex_runner.py`
- Test: `tests/test_codex_runner.py`

- [ ] **Step 1: Write failing test for developer instructions**

Add this test in `tests/test_codex_runner.py`:

```python
def test_codex_developer_instructions_include_dws_material_reading_guidance():
    instructions = codex_developer_instructions()

    assert "DingTalk material reading" in instructions
    assert "dws doc info --node" in instructions
    assert "dws doc read --node" in instructions
    assert "dws minutes get info --id" in instructions
    assert "record why each material command was used" in instructions
    assert "Do not expose tokens" in instructions
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_codex_runner.py::test_codex_developer_instructions_include_dws_material_reading_guidance -q
```

Expected: FAIL because the current developer instructions do not contain these strings.

- [ ] **Step 3: Add material-reading instructions**

In `app/codex_runner.py`, add this constant below `CODEX_DEVELOPER_INSTRUCTIONS_PREFIX`:

```python
DWS_MATERIAL_READING_INSTRUCTIONS = """
DingTalk material reading:
- When the user asks for judgment that depends on DingTalk documents, AI minutes, or files, inspect the material before deciding.
- Use DWS read-only commands with --format json.
- For DingTalk docs, first run: dws doc info --node <URL> --format json. If it is an online document and content is needed, run: dws doc read --node <URL> --format json.
- For AI minutes, run: dws minutes get info --id <MINUTES_ID> --format json.
- For ordinary files, use the relevant DWS file or drive read/download capability only when the message cannot be answered from text context.
- If a material command fails because of permission, say exactly what permission or material is missing; do not invent document contents.
- If some referenced materials fail but other referenced materials are readable, use the readable materials and mention the limitation.
- record why each material command was used in the reasoning that supports the final JSON.
- Do not expose tokens, cookies, OAuth codes, signed URLs, local credential paths, or raw secret-bearing commands.
"""
```

Change `codex_developer_instructions()` to:

```python
def codex_developer_instructions() -> str:
    return (
        f"{CODEX_DEVELOPER_INSTRUCTIONS_PREFIX}\n\n"
        f"{DWS_MATERIAL_READING_INSTRUCTIONS.strip()}\n\n"
        f"{ceo_agent_thread_prompt()}"
    )
```

- [ ] **Step 4: Run codex runner tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_codex_runner.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add app/codex_runner.py tests/test_codex_runner.py
git commit -m "feat: instruct agent to read DingTalk materials"
```

Expected: commit succeeds.

---

### Task 5: Ensure Audit Tool Events Capture Agent DWS Reads

**Files:**
- Modify: `tests/test_codex_decision.py`
- Modify: `tests/test_audit_web.py`
- Modify: `app/codex_decision.py`
- Modify: `app/audit_web.py`

- [ ] **Step 1: Add Codex audit extraction test for DWS commands**

Add this test near `test_runner_tracks_audit_tool_events` in `tests/test_codex_decision.py`:

```python
def test_runner_tracks_dws_material_read_audit_events(tmp_path: Path):
    runner = make_runner(
        tmp_path,
        raw_output="\n".join(
            [
                '{"type":"thread.started","thread_id":"thread-1"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"type":"tool_call","name":"exec_command","arguments":{"cmd":"dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 --format json"}}}',
                '{"type":"item.completed","item":{"type":"tool_result","output":"{\\"title\\":\\"OpenAI 合作建议补充版\\",\\"success\\":true}"}}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"{\\"action\\":\\"send_reply\\",\\"reply_text\\":\\"建议主打 Expert Signal Flywheel\\",\\"reason\\":\\"已读取材料\\",\\"sensitivity_kind\\":\\"general\\"}"}}',
            ]
        ),
    )

    runner.decide(prompt="read material", session_id=None)

    assert runner.last_audit_tool_events
    assert runner.last_audit_tool_events[0]["tool"] == "exec_command"
    assert "dws doc read --node" in runner.last_audit_tool_events[0]["command"]
```

- [ ] **Step 2: Run Codex audit test**

Run:

```bash
.venv/bin/python -m pytest tests/test_codex_decision.py::test_runner_tracks_dws_material_read_audit_events -q
```

Expected: FAIL if the current extraction drops the `tool_result` output or does not pair it with the `exec_command` call.

- [ ] **Step 3: Preserve DWS command input and output in extraction**

In `app/codex_decision.py`, update `extract_codex_audit_events()` so a tool call and its output are both retained. The target behavior is this normalized shape:

```python
[
    {
        "tool": "exec_command",
        "call_id": "...",
        "command": "dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 --format json",
        "input": "{\"cmd\":\"dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 --format json\"}",
    },
    {
        "tool": "tool_output",
        "call_id": "...",
        "output": "{\"title\":\"OpenAI 合作建议补充版\",\"success\":true}",
    },
]
```

Use this implementation pattern inside the extractor where Codex JSON session items are parsed:

```python
if item_type == "tool_call":
    arguments = item.get("arguments") or {}
    command = ""
    if isinstance(arguments, dict):
        command = str(arguments.get("cmd") or arguments.get("command") or "")
    events.append(
        {
            "tool": str(item.get("name") or item.get("tool") or "tool"),
            "call_id": str(item.get("call_id") or item.get("id") or ""),
            "command": command,
            "input": json.dumps(arguments, ensure_ascii=False),
        }
    )
elif item_type == "tool_result":
    events.append(
        {
            "tool": "tool_output",
            "call_id": str(item.get("call_id") or item.get("id") or ""),
            "output": str(item.get("output") or item.get("text") or ""),
        }
    )
```

Keep existing behavior for non-DWS tools; this change preserves more input/output data rather than filtering by command name.

- [ ] **Step 4: Re-run Codex audit extraction test**

Run:

```bash
.venv/bin/python -m pytest tests/test_codex_decision.py::test_runner_tracks_dws_material_read_audit_events -q
```

Expected: PASS.

- [ ] **Step 5: Add audit UI rendering test**

Add this test in `tests/test_audit_web.py` near existing audit tool event rendering tests:

```python
def test_attempt_detail_renders_dws_material_tool_events(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "audit.sqlite3")
    attempt_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="CEO-2 管理群",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_text="@Alex 看材料",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="已读取材料",
        audit_tool_events_json=json.dumps(
            [
                {
                    "tool": "exec_command",
                    "command": "dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 --format json",
                    "output": "{\"title\":\"OpenAI 合作建议补充版\"}",
                }
            ],
            ensure_ascii=False,
        ),
        send_status="sent",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "Audit tool events" in html
    assert "dws doc read --node" in html
    assert "OpenAI 合作建议补充版" in html
```

- [ ] **Step 6: Ensure audit UI renders tool input/output pairs**

In `app/audit_web.py`, keep `_audit_tool_events_html()` grouping `tool_output` events by `call_id`. Ensure `_audit_tool_call_html()` renders all three sections when present:

```python
if command:
    parts.append(f"<div class=\"audit-tool-command\"><code>{html.escape(command)}</code></div>")
if input_text:
    parts.append(
        "<details class=\"audit-tool-io\" open>"
        "<summary>Input</summary>"
        f"<pre>{html.escape(input_text)}</pre>"
        "</details>"
    )
if output_text:
    parts.append(
        "<details class=\"audit-tool-io\">"
        "<summary>Output</summary>"
        f"<pre>{html.escape(output_text)}</pre>"
        "</details>"
    )
```

This keeps every DWS command parameter and result visible in the attempt detail page.

- [ ] **Step 7: Run audit tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_codex_decision.py::test_runner_tracks_dws_material_read_audit_events tests/test_audit_web.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

Run:

```bash
git add tests/test_codex_decision.py tests/test_audit_web.py app/codex_decision.py app/audit_web.py
git commit -m "test: verify DWS material reads appear in audit events"
```

Expected: commit succeeds with the audit extraction test, audit UI test, and any production adjustments required by Steps 3 and 6.

---

### Task 6: Remove Worker-Side Document/Minutes/File Pre-Read From Ordinary Path

**Files:**
- Modify: `app/worker.py`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_prompt.py`

- [ ] **Step 1: Update existing tests that expected pre-read documents**

Replace old expectations in `tests/test_worker.py`:

Old pattern:

```python
assert dws.read_doc_calls == [canonical_doc_url]
assert "已获取的钉钉材料:" in prompt
assert "根因是协作方式不对" in prompt
```

New pattern:

```python
assert dws.doc_info_calls == []
assert dws.read_doc_calls == []
assert "待读取材料（由 agent 判断是否读取）:" in prompt
assert canonical_doc_url in prompt
assert "dws doc read --node" in prompt
```

Update these tests:

```text
test_dingtalk_doc_link_is_read_before_codex
test_dingtalk_doc_link_in_context_is_read_before_codex
test_single_chat_alidocs_card_reaches_codex
```

Rename them to:

```text
test_dingtalk_doc_link_is_passed_to_codex_without_worker_read
test_dingtalk_doc_link_in_context_is_passed_to_codex_without_worker_read
test_single_chat_alidocs_card_reaches_codex_with_material_reference
```

- [ ] **Step 2: Update the single-chat retry behavior**

The existing `_single_chat_document_no_reply_needs_retry()` currently uses `linked_documents`. Replace it with material references.

In `app/worker.py`, change:

```python
        if self._single_chat_document_no_reply_needs_retry(
            conversation,
            linked_documents,
            decision,
        ):
```

to:

```python
        if self._single_chat_material_no_reply_needs_retry(
            conversation,
            material_references,
            decision,
        ):
```

Add this replacement method:

```python
    def _single_chat_material_no_reply_needs_retry(
        self,
        conversation: DingTalkConversation,
        material_references: list[MaterialReferenceContext],
        decision: CodexDecision,
    ) -> bool:
        return (
            conversation.single_chat
            and bool(material_references)
            and decision.action == CodexAction.NO_REPLY
        )
```

Replace `_single_chat_document_retry_prompt()` body with:

```python
    @staticmethod
    def _single_chat_document_retry_prompt() -> str:
        return (
            "上一次输出了 no_reply，但当前是私聊，且消息包含钉钉文档、AI听记或文件引用。"
            "请判断是否需要使用 DWS 读取材料。"
            "如果材料足够或读取后足够，action 用 send_reply，reply_text 给出结论、修改意见、风险、下一步或需要补充的具体问题；"
            "如果材料权限不足或缺正文，action 用 ask_clarifying_question。"
            "不要因为对方只发送材料、没有额外写“请处理/请 review”就 no_reply。"
            "只输出合法 JSON。"
        )
```

- [ ] **Step 3: Remove ordinary-path linked document error handling**

In `_process_batch()`, after Task 2 there should be no call to `_record_linked_document_error()` for ordinary documents. Confirm with:

```bash
rg "_record_linked_document_error|_read_linked_documents" app/worker.py
```

Expected remaining references:

```text
app/worker.py:<line>:    def _read_linked_documents(
app/worker.py:<line>:    def _record_linked_document_error(
```

If neither method is used outside tests, remove these methods:

```text
_read_linked_documents
_read_linked_alidocs_node
_read_linked_aitable
_read_linked_minutes
_record_linked_document_error
_linked_document_permission_request_reply
_linked_document_permission_context
```

Keep helpers that material reference extraction still uses:

```text
_dingtalk_doc_urls
_dingtalk_minutes_ids
_minutes_task_uuid_from_selector_url
_minutes_task_uuid_from_url
_canonical_doc_url
_referenced_file_names
```

- [ ] **Step 4: Run worker tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

Run:

```bash
git add app/worker.py tests/test_worker.py tests/test_prompt.py
git commit -m "refactor: let agent own DingTalk material reading"
```

Expected: commit succeeds.

---

### Task 7: Update Reliability Documentation

**Files:**
- Modify: `docs/reply-worker-reliability.md`

- [ ] **Step 1: Add material-reading boundary documentation**

In `docs/reply-worker-reliability.md`, add this section after the image or document handling section:

```markdown
## Material Reading Boundary

The worker does not pre-read DingTalk documents, AI minutes, or ordinary files for ordinary reply decisions. It extracts material references and injects them into the CEO agent prompt. The agent decides whether the message can be answered from text context, whether to read one or more materials through DWS, and how to respond after reading.

Worker-side preprocessing remains required for:

- Calendar invites, because the service owns response execution and conflict checks.
- Images, because Codex receives local image paths rather than DingTalk media IDs.
- OA approvals, because approval execution/commenting uses a separate audited approval flow.

For DingTalk materials, agent-side DWS calls must be visible in `audit_tool_events_json`. Permission failures should be treated as missing material context, not as a worker failure, unless the agent cannot answer without the material.
```

- [ ] **Step 2: Run documentation grep**

Run:

```bash
rg "pre-read|linked document|Material Reading Boundary|audit_tool_events_json" docs/reply-worker-reliability.md
```

Expected: output includes `Material Reading Boundary` and does not claim the worker must pre-read documents before Codex.

- [ ] **Step 3: Commit Task 7**

Run:

```bash
git add docs/reply-worker-reliability.md
git commit -m "docs: document agent-owned material reading"
```

Expected: commit succeeds.

---

### Task 8: End-to-End Verification, Service Restart, and Recovery

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_prompt.py \
  tests/test_codex_runner.py \
  tests/test_codex_decision.py::test_runner_tracks_dws_material_read_audit_events \
  tests/test_worker.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest
```

Expected: `776+ passed, 4 skipped, 1 warning` with the exact passed count updated by the new tests.

- [ ] **Step 3: Restart the launchd service**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
sleep 2
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/
```

Expected:

```text
state = running
HTTP code: 200
```

- [ ] **Step 4: Rerun #1085 through the service**

Run:

```bash
.venv/bin/ceo-agent rerun-message \
  --conversation-id 'cidrPwEErLfR2dQm4mAnntBLw==' \
  --message-id 'msgrALNoH4g/TBTgZwRm7YMAA==' \
  --force-new-decision
```

Expected:

```text
rerun-message processed conversation_id=cidrPwEErLfR2dQm4mAnntBLw== message_id=msgrALNoH4g/TBTgZwRm7YMAA== force_new_decision=True
```

Then inspect latest attempts:

```bash
sqlite3 data/auto-reply.sqlite3 <<'SQL'
.headers on
.mode column
SELECT id, action, send_status, conversation_title, trigger_sender, updated_at,
       substr(final_reply_text,1,240) AS final_reply
FROM reply_attempts
WHERE trigger_message_id='msgrALNoH4g/TBTgZwRm7YMAA=='
ORDER BY id DESC
LIMIT 3;
SQL
```

Expected:

```text
latest send_status is sent or dry_run according to runtime mode
latest final_reply_text discusses the readable OpenAI 合作建议补充版 material
latest final_reply_text is not just "打不开材料包"
```

- [ ] **Step 5: Check backlog**

Run:

```bash
utc_one_hour=$(TZ=UTC date -v-1H '+%Y-%m-%d %H:%M:%S')
utc_one_day=$(TZ=UTC date -v-24H '+%Y-%m-%d %H:%M:%S')
sqlite3 data/auto-reply.sqlite3 <<SQL
.headers on
.mode column
SELECT COUNT(*) AS recent_errors_1h FROM errors WHERE created_at >= '$utc_one_hour';
SELECT COUNT(*) AS failed_or_processing_tasks FROM reply_tasks WHERE status IN ('failed','processing');
SELECT COUNT(*) AS failed_blocked_dry_run_24h FROM reply_attempts
WHERE (created_at >= '$utc_one_day' OR updated_at >= '$utc_one_day')
  AND send_status IN ('failed','blocked','dry_run');
SQL
```

Expected:

```text
failed_or_processing_tasks = 0
failed_blocked_dry_run_24h = 0 unless a deliberate dry_run remains
```

- [ ] **Step 6: Push all commits**

Run:

```bash
git push
```

Expected: `main -> main` push succeeds.

---

## Self-Review

**Spec coverage:**
- Calendar remains worker-preprocessed: Task 3.
- Images remain worker-preprocessed: Task 3.
- Documents/minutes/files move to agent decision: Tasks 1, 2, 6.
- Agent decides whether and what to read: Tasks 1, 4.
- Audit tool events record DWS input/output: Task 5.
- OA runner remains separate: File structure and Task 7 document the boundary.
- #1085 recovery is included: Task 8.

**Placeholder scan:** This plan contains no `TBD`, `TODO`, "implement later", or unspecified "write tests" steps. Steps include exact file paths, exact commands, expected outcomes, and concrete code.

**Type consistency:** `MaterialReferenceContext` is introduced in Task 1, imported and used in Task 2, and referenced consistently in Task 6. The prompt block is consistently named `material_references_block`.
