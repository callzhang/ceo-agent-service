# Agent-Owned Material Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove service-side business-material reading from the CEO universal agent path so the service supplies material references and exact read commands, while the agent decides and performs read-only evidence gathering.

**Architecture:** The service remains responsible for ingestion, auth preflight, trusted target IDs, side-effect execution, idempotency, and receipts. The agent owns business-material expansion by receiving structured references in `UniversalTaskContext`, then using read-only DWS/Lark/Exa tools as needed. OA detail collection keeps identity/task/status facts, but attachment and document bodies become material references instead of service-expanded fallback text.

**Tech Stack:** Python 3, dataclasses, pytest, SQLite-backed CEO service store, DWS CLI/client, Codex universal planner/consumer.

---

## Scope Check

This plan is one subsystem: material-reading responsibility boundaries for CEO service universal processing. It does not change UI rendering, DWS CLI internals, OA action execution semantics, memory connector behavior, or the feedback-link service.

The plan follows `/Users/derek/.agents/AGENT.md`: no new legacy compatibility branch, no fallback body-reader, no broad rewrite. Existing service-side readers are removed from universal routing and replaced by one new material-reference contract.

## File Structure

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/universal_context.py`
  - Add `UniversalMaterialReference`.
  - Add `material_references` to `UniversalTaskContext`.
  - Render material references into planner prompt.
  - Include references in canonical JSON and hash.
  - Allow `build_universal_context()` to receive references from worker.

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/universal_planner.py`
  - Strengthen planner instructions: if a material reference exists and the decision depends on it, use the supplied command/read-only tools before concluding unreadable.
  - Keep side effects executor-owned.

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`
  - Stop calling `_read_calendar_linked_documents()` in universal task setup.
  - Convert ordinary DingTalk/Lark/file/minutes/OA form material into material references.
  - Keep image download because Codex needs local image paths.
  - Replace OA `oa_attachment_fallbacks` body expansion with `oa_material_references`.
  - Remove or leave unused legacy material body readers only after all tests prove no production path calls them.

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/oa_approval.py`
  - Update prompt text to say `oa_material_references` are supplied and must be read by the agent when needed.
  - Remove instruction that treats `oa_attachment_fallbacks` as source of truth.

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context.py`
  - Add context rendering and canonical-hash tests for universal material references.

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context_enrichment.py`
  - Replace tests expecting trusted-document injection with tests expecting material-reference propagation and no service-side DWS document reads.

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_worker.py`
  - Replace OA attachment fallback-body tests with reference-only tests.
  - Add OA alidocs folder reference extraction regression.

- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_planner.py`
  - Add planner prompt test for material-reference instructions.

- Create: `/Users/derek/Documents/Projects/ceo-agent-service/docs/agent-owned-material-reading.md`
  - Document service-vs-agent responsibility boundary and examples of material references.

## Task 1: Add Universal Material References To Context

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/universal_context.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context.py`

- [ ] **Step 1: Write the failing context render test**

Add this import in `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context.py`:

```python
from app.universal_context import (
    UniversalContextMessage,
    UniversalMaterialReference,
    UniversalTaskContext,
    build_universal_context,
    canonical_universal_context_json,
    universal_context_sha256,
)
```

Add this test near the existing render tests:

```python
def test_render_includes_material_references_with_commands() -> None:
    context = UniversalTaskContext(
        task_id=42,
        conversation_id="conversation-1",
        conversation_title="Friday planning",
        single_chat=True,
        trigger_message_id="trigger-1",
        trigger_sender="Derek",
        trigger_text="Please review this folder.",
        context_messages=(
            UniversalContextMessage("Derek", "trigger-1", "Please review this folder."),
        ),
        required_dependencies=("dws",),
        force_new_decision=True,
        dry_run=False,
        material_references=(
            UniversalMaterialReference(
                kind="dingtalk_doc",
                reference="https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk",
                source_message_id="trigger-1",
                source_sender="Derek",
                source_time="2026-07-20 10:00:00",
                read_command=(
                    "dws doc info --node https://alidocs.dingtalk.com/i/nodes/"
                    "NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk --format json"
                ),
            ),
        ),
    )

    rendered = context.render_for_agent()

    assert "Material references:" in rendered
    assert "kind=dingtalk_doc" in rendered
    assert "source_message_id=trigger-1" in rendered
    assert "source_sender=Derek" in rendered
    assert "dws doc info --node https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk --format json" in rendered
```

- [ ] **Step 2: Write the failing canonical JSON test**

Add this test in `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context.py`:

```python
def test_material_references_are_part_of_universal_context_hash() -> None:
    base = UniversalTaskContext(
        task_id=42,
        conversation_id="conversation-1",
        conversation_title="Friday planning",
        single_chat=True,
        trigger_message_id="trigger-1",
        trigger_sender="Derek",
        trigger_text="Please review this folder.",
        context_messages=(
            UniversalContextMessage("Derek", "trigger-1", "Please review this folder."),
        ),
        required_dependencies=("dws",),
        force_new_decision=True,
        dry_run=False,
    )
    with_reference = replace(
        base,
        material_references=(
            UniversalMaterialReference(
                kind="dingtalk_doc",
                reference="https://alidocs.dingtalk.com/i/nodes/folder-1",
                source_message_id="trigger-1",
                source_sender="Derek",
                source_time="2026-07-20 10:00:00",
                read_command="dws doc info --node https://alidocs.dingtalk.com/i/nodes/folder-1 --format json",
            ),
        ),
    )

    canonical = json.loads(canonical_universal_context_json(with_reference))

    assert canonical["material_references"] == [
        {
            "kind": "dingtalk_doc",
            "reference": "https://alidocs.dingtalk.com/i/nodes/folder-1",
            "source_message_id": "trigger-1",
            "source_sender": "Derek",
            "source_time": "2026-07-20 10:00:00",
            "read_command": "dws doc info --node https://alidocs.dingtalk.com/i/nodes/folder-1 --format json",
        }
    ]
    assert universal_context_sha256(base) != universal_context_sha256(with_reference)
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_universal_context.py::test_render_includes_material_references_with_commands tests/test_universal_context.py::test_material_references_are_part_of_universal_context_hash -v
```

Expected: FAIL with `ImportError: cannot import name 'UniversalMaterialReference'`.

- [ ] **Step 4: Implement minimal context support**

Add this dataclass after `UniversalContextMessage` in `/Users/derek/Documents/Projects/ceo-agent-service/app/universal_context.py`:

```python
@dataclass(frozen=True)
class UniversalMaterialReference:
    kind: str
    reference: str
    source_message_id: str
    source_sender: str
    source_time: str
    read_command: str = ""
```

Add this field to `UniversalTaskContext`:

```python
    material_references: tuple[UniversalMaterialReference, ...] = ()
```

Add this block to `render_for_agent()` before `"Recent messages:"`:

```python
                "Material references:",
                *self._render_material_references_for_agent(),
```

Add these methods to `UniversalTaskContext`:

```python
    def _render_material_references_for_agent(self) -> list[str]:
        if not self.material_references:
            return ["- none"]
        lines: list[str] = [
            "- If the decision depends on a material body, use the read_command or an equivalent read-only CLI/tool before concluding the material is unreadable.",
            "- Do not say a material is inaccessible until its supplied read path has been tried or the tool reports a concrete permission/login error.",
        ]
        for index, material in enumerate(self.material_references, start=1):
            command = material.read_command or "none"
            lines.append(
                f"- [{index}] kind={material.kind}; reference={material.reference}; "
                f"source_message_id={material.source_message_id}; "
                f"source_sender={material.source_sender}; source_time={material.source_time}; "
                f"read_command={command}"
            )
        return lines
```

In `__post_init__()`, add:

```python
        if not isinstance(self.material_references, tuple) or any(
            not isinstance(reference, UniversalMaterialReference)
            for reference in self.material_references
        ):
            raise TypeError("material_references must be a tuple[UniversalMaterialReference, ...]")
```

In `canonical_universal_context_json()`, validate and serialize `material_references`:

```python
    material_references: list[dict[str, str]] = []
    for reference in context.material_references:
        if not isinstance(reference, UniversalMaterialReference):
            raise TypeError("material_references items must be UniversalMaterialReference")
        for field_name in (
            "kind",
            "reference",
            "source_message_id",
            "source_sender",
            "source_time",
            "read_command",
        ):
            if not isinstance(getattr(reference, field_name), str):
                raise TypeError(f"material reference {field_name} must be a str")
        material_references.append(
            {
                "kind": reference.kind,
                "reference": reference.reference,
                "source_message_id": reference.source_message_id,
                "source_sender": reference.source_sender,
                "source_time": reference.source_time,
                "read_command": reference.read_command,
            }
        )
```

Include `"material_references": material_references` in the JSON object returned by `canonical_universal_context_json()`.

Add a `material_references` parameter to `build_universal_context()`:

```python
    material_references: tuple[UniversalMaterialReference, ...] = (),
```

Pass it into `UniversalTaskContext(...)`:

```python
        material_references=material_references,
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_universal_context.py::test_render_includes_material_references_with_commands tests/test_universal_context.py::test_material_references_are_part_of_universal_context_hash -v
```

Expected: PASS.

- [ ] **Step 6: Run impacted context suite**

Run:

```bash
pytest tests/test_universal_context.py tests/test_store.py::test_universal_context_round_trips_through_reply_task -v
```

Expected: PASS. If the named store test does not exist, run `pytest tests/test_store.py -k universal_context -v` and expect PASS.

- [ ] **Step 7: Commit**

```bash
git add app/universal_context.py tests/test_universal_context.py
git commit -m "feat: pass material references to universal agent"
```

## Task 2: Stop Universal Service-Side Material Body Injection

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context_enrichment.py`

- [ ] **Step 1: Replace the existing trusted-document injection test**

In `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context_enrichment.py`, replace the test that currently asserts `:trusted-document-1` is injected with this test:

```python
def test_universal_worker_passes_material_references_without_reading_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = make_worker(tmp_path)
    file_reference = message(
        "材料在这里：https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk",
        message_id="msg-file",
    )
    trigger = message(
        "@Derek Zen(磊哥) 请审核这份材料",
        message_id="msg-trigger",
    )
    consumer = CapturingConsumer()
    document_reader_calls = []
    monkeypatch.setattr(worker, "_calendar_invite_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker,
        "_read_calendar_linked_documents",
        lambda *args: document_reader_calls.append(args) or [],
    )
    monkeypatch.setattr(worker, "_collect_image_paths", lambda *_: ([], []))
    monkeypatch.setattr(worker, "_universal_consumer", lambda: consumer)

    worker._process_universal_queued_task(
        conversation(),
        reply_task(trigger),
        trigger,
        [file_reference, trigger],
        [file_reference, trigger],
    )

    assert document_reader_calls == []
    context = consumer.contexts[0]
    assert all(":trusted-document-" not in message.open_message_id for message in context.context_messages)
    assert context.material_references
    assert context.material_references[0].kind == "dingtalk_doc"
    assert context.material_references[0].reference == (
        "https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk"
    )
    assert context.material_references[0].read_command == (
        "dws doc info --node https://alidocs.dingtalk.com/i/nodes/"
        "NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk --format json"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/test_universal_context_enrichment.py::test_universal_worker_passes_material_references_without_reading_bodies -v
```

Expected: FAIL because `_read_calendar_linked_documents` is called or because `UniversalTaskContext` does not receive `material_references`.

- [ ] **Step 3: Implement universal reference passing**

In `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`, import the new context model:

```python
from app.universal_context import (
    UniversalContextMessage,
    UniversalMaterialReference,
    UniversalTaskContext,
    build_universal_context,
)
```

In `_process_universal_queued_task`, remove this block:

```python
        material_context_messages = list(context_messages) or list(prompt_context_messages)
        linked_documents = self._read_calendar_linked_documents(
            [trigger],
            material_context_messages,
        )
        for index, document in enumerate(linked_documents[:3], start=1):
            planner_context_messages.append(
                DingTalkMessage(
                    open_conversation_id=conversation.open_conversation_id,
                    open_message_id=(
                        f"{trigger.open_message_id}:trusted-document-{index}"
                    ),
                    conversation_title=conversation.title,
                    single_chat=conversation.single_chat,
                    sender_name="CEO系统",
                    create_time=trigger.create_time,
                    content=(
                        f"可信材料 {index}：{document.title or '未命名材料'}\n"
                        f"链接：{document.url or '无'}\n"
                        "以下正文由服务在规划前读取；必须据此处理，不要只看文件名。\n"
                        f"{document.markdown[:30000]}"
                    ),
                )
            )
```

Replace it with:

```python
        material_context_messages = list(context_messages) or list(prompt_context_messages)
        material_references = self._universal_material_references(
            [trigger],
            material_context_messages,
        )
```

When calling `build_universal_context(...)`, add:

```python
            material_references=material_references,
```

Add this helper method to `DingTalkAutoReplyWorker`:

```python
    def _universal_material_references(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> tuple[UniversalMaterialReference, ...]:
        references = self._material_references(new_messages, context_messages)
        universal_references: list[UniversalMaterialReference] = []
        for reference in references:
            universal_references.append(
                UniversalMaterialReference(
                    kind=reference.kind,
                    reference=reference.reference,
                    source_message_id=reference.source_message_id,
                    source_sender=reference.source_sender,
                    source_time=reference.source_time,
                    read_command=reference.read_command
                    or self._default_material_read_command(reference.kind, reference.reference),
                )
            )
        return tuple(universal_references)

    @staticmethod
    def _default_material_read_command(kind: str, reference: str) -> str:
        if kind == "dingtalk_doc":
            return f"dws doc info --node {reference} --format json"
        if kind == "dingtalk_minutes":
            return f"dws minutes get info --id {reference} --format json"
        if kind == "lark_doc":
            return f"lark-cli docs +fetch --doc {reference} --doc-format markdown --format json --as bot"
        return ""
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/test_universal_context_enrichment.py::test_universal_worker_passes_material_references_without_reading_bodies -v
```

Expected: PASS.

- [ ] **Step 5: Run universal enrichment tests**

Run:

```bash
pytest tests/test_universal_context_enrichment.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/worker.py tests/test_universal_context_enrichment.py
git commit -m "fix: stop pre-reading universal task materials"
```

## Task 3: Convert OA Material Expansion To References

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/oa_approval.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_worker.py`

- [ ] **Step 1: Replace OA attachment fallback body test**

In `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_worker.py`, replace the test that asserts `oa_attachment_fallbacks`, `downloaded_attachment`, `read_document`, and DWS body reads with this test:

```python
def test_oa_approval_detail_reports_attachment_reference_without_service_body_read(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的项目申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = DwsError("detail parse failed")
    dws.oa_approval_records["proc-1"] = {"records": []}
    dws.oa_approval_tasks["proc-1"] = {"tasks": [{"taskId": "task-1", "userid": "derek-user"}]}
    dws.current_user_id = "derek-user"
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "form_component_values": [
                {
                    "componentType": "DDAttachment",
                    "value": json.dumps(
                        [
                            {
                                "fileName": "项目实施计划（第三曲线大模型解决方案）.docx",
                                "fileId": "224596585916",
                                "spaceId": "space-1",
                                "fileType": "docx",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                }
            ]
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    detail = json.loads(oa_handler.approval_detail_texts[0])
    assert "oa_attachment_fallbacks" not in detail
    assert detail["oa_material_references"] == [
        {
            "kind": "dingtalk_oa_attachment",
            "reference": "224596585916",
            "source": "openapi_detail.form_component_values",
            "file_name": "项目实施计划（第三曲线大模型解决方案）.docx",
            "space_id": "space-1",
            "file_type": "docx",
            "read_command": (
                "dws oa attachment download --process-instance-id proc-1 "
                "--file-id 224596585916 --output <local-path> --format json"
            ),
        }
    ]
    assert dws.download_oa_attachment_calls == []
    assert dws.search_document_calls == []
    assert dws.read_doc_calls == []
```

- [ ] **Step 2: Add OA folder-link extraction regression**

Add this test in `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_worker.py`:

```python
def test_oa_approval_detail_reports_alidocs_folder_reference_from_openapi_form(
    tmp_path: Path, monkeypatch
):
    folder_url = "https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk"
    trigger = message(
        "[Ding]susu提醒您审批他的项目申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = DwsError("detail parse failed")
    dws.oa_approval_records["proc-1"] = {"records": []}
    dws.oa_approval_tasks["proc-1"] = {"tasks": [{"taskId": "task-1", "userid": "derek-user"}]}
    dws.current_user_id = "derek-user"
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "form_component_values": [
                {
                    "name": "立项材料",
                    "componentType": "TextField",
                    "value": f"项目材料：{folder_url}",
                }
            ]
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    detail = json.loads(oa_handler.approval_detail_texts[0])
    assert {
        "kind": "dingtalk_doc",
        "reference": folder_url,
        "source": "openapi_detail.form_component_values",
        "field_name": "立项材料",
        "read_command": f"dws doc info --node {folder_url} --format json",
    } in detail["oa_material_references"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_worker.py::test_oa_approval_detail_reports_attachment_reference_without_service_body_read tests/test_worker.py::test_oa_approval_detail_reports_alidocs_folder_reference_from_openapi_form -v
```

Expected: FAIL because current code emits `oa_attachment_fallbacks`, downloads/parses bodies, and does not extract ordinary alidocs links from OA form fields.

- [ ] **Step 4: Implement OA material references**

In `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`, replace this call in `_oa_approval_detail_text()`:

```python
        self._append_oa_attachment_fallbacks(documents)
```

with:

```python
        self._append_oa_material_references(documents)
```

Add these methods near the old attachment fallback methods:

```python
    def _append_oa_material_references(self, documents: dict[str, Any]) -> None:
        openapi_detail = documents.get("openapi_detail")
        if not isinstance(openapi_detail, dict):
            return
        process = openapi_detail.get("process_instance")
        if not isinstance(process, dict):
            return
        values = process.get("form_component_values")
        references: list[dict[str, str]] = []
        self._collect_oa_attachment_references(
            str(documents.get("process_instance_id") or ""),
            values,
            references,
        )
        self._collect_oa_alidocs_references(values, references)
        if references:
            documents["oa_material_references"] = self._dedupe_oa_material_references(references)

    @classmethod
    def _collect_oa_attachment_references(
        cls,
        process_instance_id: str,
        value: Any,
        references: list[dict[str, str]],
    ) -> None:
        for attachment in cls._oa_attachment_records(value):
            file_name = str(attachment.get("fileName") or attachment.get("file_name") or "")
            file_id = str(attachment.get("fileId") or attachment.get("file_id") or "")
            if not file_id:
                continue
            references.append(
                {
                    "kind": "dingtalk_oa_attachment",
                    "reference": file_id,
                    "source": "openapi_detail.form_component_values",
                    "file_name": file_name,
                    "space_id": str(attachment.get("spaceId") or attachment.get("space_id") or ""),
                    "file_type": str(attachment.get("fileType") or attachment.get("file_type") or ""),
                    "read_command": (
                        "dws oa attachment download "
                        f"--process-instance-id {process_instance_id} "
                        f"--file-id {file_id} --output <local-path> --format json"
                    ),
                }
            )

    @classmethod
    def _collect_oa_alidocs_references(
        cls,
        value: Any,
        references: list[dict[str, str]],
        *,
        field_name: str = "",
    ) -> None:
        if isinstance(value, list):
            for item in value:
                cls._collect_oa_alidocs_references(item, references, field_name=field_name)
            return
        if isinstance(value, dict):
            next_field_name = str(value.get("name") or value.get("label") or field_name)
            for nested_key in ("value", "extendValue", "rowValue"):
                if nested_key in value:
                    cls._collect_oa_alidocs_references(
                        value.get(nested_key),
                        references,
                        field_name=next_field_name,
                    )
            return
        if not isinstance(value, str):
            return
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            cls._collect_oa_alidocs_references(parsed, references, field_name=field_name)
        for match in DINGTALK_DOC_URL_PATTERN.finditer(value):
            url = cls._canonical_doc_url(match.group(0))
            references.append(
                {
                    "kind": "dingtalk_doc",
                    "reference": url,
                    "source": "openapi_detail.form_component_values",
                    "field_name": field_name,
                    "read_command": f"dws doc info --node {url} --format json",
                }
            )

    @staticmethod
    def _dedupe_oa_material_references(
        references: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for reference in references:
            key = (reference.get("kind", ""), reference.get("reference", ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(reference)
        return deduped
```

Remove `_append_oa_attachment_fallbacks()` and `_oa_attachment_fallback()` after the tests pass. Keep `_oa_attachment_records()` because the new reference collector uses it.

In `_annotate_oa_detail_recovery()`, replace:

```python
                "dws_records, dws_tasks, and oa_attachment_fallbacks as the "
```

with:

```python
                "dws_records, dws_tasks, and oa_material_references as the "
```

In `/Users/derek/Documents/Projects/ceo-agent-service/app/oa_approval.py`, replace prompt text that says `oa_attachment_fallbacks` with:

```python
"如果 openapi_detail 中提供 oa_material_references，且审批判断依赖材料正文，必须先使用其中 read_command 或等价只读 DWS 命令读取材料；不能因为材料未被 service 预展开就说不可访问。"
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_worker.py::test_oa_approval_detail_reports_attachment_reference_without_service_body_read tests/test_worker.py::test_oa_approval_detail_reports_alidocs_folder_reference_from_openapi_form -v
```

Expected: PASS.

- [ ] **Step 6: Search for removed fallback contract**

Run:

```bash
rg -n "oa_attachment_fallbacks|_append_oa_attachment_fallbacks|_oa_attachment_fallback|downloaded_attachment|read_document" app tests
```

Expected: no hits except removed-code references in git diff before staging. If there are hits in tests or prompts, update them to `oa_material_references`.

- [ ] **Step 7: Run OA and worker tests**

Run:

```bash
pytest tests/test_worker.py tests/test_oa_approval.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/worker.py app/oa_approval.py tests/test_worker.py
git commit -m "fix: expose OA materials as agent-readable references"
```

## Task 4: Make Planner Instructions Match Agent-Owned Reading

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/universal_planner.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_planner.py`

- [ ] **Step 1: Write the failing planner prompt test**

Add this test in `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_planner.py`:

```python
def test_planner_prompt_requires_trying_material_read_commands_before_unreadable() -> None:
    planner = UniversalPlanner(workspace=Path("/tmp"))
    context = make_context(
        trigger_text="请审核材料",
        context_messages=(
            UniversalContextMessage("Derek", "trigger-1", "请审核材料"),
        ),
        material_references=(
            UniversalMaterialReference(
                kind="dingtalk_doc",
                reference="https://alidocs.dingtalk.com/i/nodes/folder-1",
                source_message_id="trigger-1",
                source_sender="Derek",
                source_time="2026-07-20 10:00:00",
                read_command="dws doc info --node https://alidocs.dingtalk.com/i/nodes/folder-1 --format json",
            ),
        ),
    )

    prompt = planner.build_prompt(context)

    assert "Material references:" in prompt
    assert "use the supplied read_command" in prompt
    assert "Do not say a material is inaccessible" in prompt
    assert "dws doc info --node https://alidocs.dingtalk.com/i/nodes/folder-1 --format json" in prompt
```

If `make_context()` in this file does not accept `material_references`, update its signature:

```python
def make_context(
    *,
    trigger_text: str = "Please reply.",
    context_messages: tuple[UniversalContextMessage, ...] = (),
    material_references: tuple[UniversalMaterialReference, ...] = (),
) -> UniversalTaskContext:
    return UniversalTaskContext(
        task_id=1,
        conversation_id="cid-1",
        conversation_title="测试群",
        single_chat=False,
        trigger_message_id="trigger-1",
        trigger_sender="Derek",
        trigger_text=trigger_text,
        context_messages=context_messages
        or (UniversalContextMessage("Derek", "trigger-1", trigger_text),),
        required_dependencies=("dws",),
        force_new_decision=True,
        dry_run=False,
        material_references=material_references,
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/test_universal_planner.py::test_planner_prompt_requires_trying_material_read_commands_before_unreadable -v
```

Expected: FAIL because the exact material-reference instruction is not present.

- [ ] **Step 3: Add planner instruction**

In `/Users/derek/Documents/Projects/ceo-agent-service/app/universal_planner.py`, add this paragraph in `build_prompt()` after the DWS paragraph:

```python
                "Task context may include Material references. These are trusted "
                "pointers collected by the service, not expanded bodies. If a reply, "
                "approval comment, approval action, or document update depends on a "
                "material body, use the supplied read_command or an equivalent read-only "
                "DWS/Lark tool before deciding. Do not say a material is inaccessible "
                "until the supplied read path has been tried or the tool returns a "
                "concrete login, permission, missing target, or unsupported file error. "
                "For a DingTalk folder, first inspect it with dws doc info and then list "
                "children with dws doc list --folder when needed.",
```

- [ ] **Step 4: Run planner tests**

Run:

```bash
pytest tests/test_universal_planner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/universal_planner.py tests/test_universal_planner.py
git commit -m "fix: require agent material reads in planner prompt"
```

## Task 5: Remove Dead Legacy Body Readers From Universal Path

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_worker.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context_enrichment.py`

- [ ] **Step 1: Search current body-reader callers**

Run:

```bash
rg -n "_read_calendar_linked_documents|_read_linked_alidocs_node|_read_linked_aitable|_read_linked_minutes|_read_referenced_file\\(" app tests
```

Expected before edits: callers exist in worker and tests.

- [ ] **Step 2: Remove universal and normal reply callers**

In `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`, remove the normal reply `linked_documents` call in `_process_reply()`:

```python
        linked_documents: list[LinkedDocumentContext] = []
        if calendar_response_event is not None:
            linked_documents = self._read_calendar_linked_documents(
                material_messages, context_messages
            )
```

Replace it with:

```python
        linked_documents: list[LinkedDocumentContext] = []
```

Do not add a new service-side body reader. The prompt already receives `material_references`.

- [ ] **Step 3: Remove tests that require service-side body readers**

In `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_worker.py`, delete tests whose assertion requires `_read_calendar_linked_documents()` to return body text. Keep tests for `_material_references()` and `_referenced_file_read_command()`.

In `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_universal_context_enrichment.py`, keep only the reference propagation test from Task 2.

- [ ] **Step 4: Delete dead body-reader methods**

In `/Users/derek/Documents/Projects/ceo-agent-service/app/worker.py`, delete these methods only after Step 1 shows no app caller remains:

```python
_read_calendar_linked_documents
_linked_document_read_failure_context
_read_linked_alidocs_node
_read_linked_aitable
_read_linked_minutes
_format_minutes_material
_read_referenced_file
_read_referenced_file_by_message_file_id
```

Keep `_collect_image_paths()` and image download methods because Codex receives images as local files, not DWS commands.

- [ ] **Step 5: Verify no forbidden body-reader names remain**

Run:

```bash
rg -n "_read_calendar_linked_documents|_read_linked_alidocs_node|_read_linked_aitable|_read_linked_minutes|_read_referenced_file\\(" app tests
```

Expected: no hits.

- [ ] **Step 6: Run affected tests**

Run:

```bash
pytest tests/test_prompt.py tests/test_worker.py tests/test_universal_context_enrichment.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/worker.py tests/test_worker.py tests/test_universal_context_enrichment.py
git commit -m "refactor: remove service material body readers"
```

## Task 6: Document The Service-Agent Boundary

**Files:**
- Create: `/Users/derek/Documents/Projects/ceo-agent-service/docs/agent-owned-material-reading.md`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/docs/universal-consumer-agent.md`

- [ ] **Step 1: Create documentation**

Create `/Users/derek/Documents/Projects/ceo-agent-service/docs/agent-owned-material-reading.md`:

```markdown
# Agent-Owned Material Reading

The CEO service does not expand DingTalk, Lark, minutes, sheet, folder, or ordinary-file business material into prompt bodies.

The service owns:

- message ingestion and task state
- DWS auth preflight
- trusted OA process/task IDs and current-user ownership checks
- trusted mail/calendar targets
- material references and exact read-command hints
- side-effect execution, idempotency, status updates, and receipts

The agent owns:

- deciding whether a material body is needed
- reading material through supplied read commands or equivalent read-only tools
- listing DingTalk folders before judging their content
- downloading ordinary files when the task requires file content
- returning a concrete blocked/ask-clarifying result when read-only tools report login, permission, missing target, or unsupported file errors

Examples:

```text
kind=dingtalk_doc
reference=https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk
read_command=dws doc info --node https://alidocs.dingtalk.com/i/nodes/NZQYprEoWoEOXajDiBrvmqyEJ1waOeDk --format json
```

For a DingTalk folder, the agent first runs `dws doc info --node <URL> --format json`, then `dws doc list --folder <URL> --format json` when the info response identifies a folder.

For an OA attachment, the service supplies `kind=dingtalk_oa_attachment`, the file ID, process instance ID inside `read_command`, and no parsed body.
```
```

- [ ] **Step 2: Link from universal consumer docs**

Add this paragraph to `/Users/derek/Documents/Projects/ceo-agent-service/docs/universal-consumer-agent.md` near the task context section:

```markdown
Material reading follows [Agent-Owned Material Reading](agent-owned-material-reading.md): the service passes material references and read-command hints, while the universal agent performs read-only evidence gathering before planning any reply, approval comment, or blocked result that depends on material content.
```

- [ ] **Step 3: Verify docs have no stale fallback wording**

Run:

```bash
rg -n "oa_attachment_fallbacks|service.*预.*读取|trusted-document|可信材料|fallback" docs app tests
```

Expected: no hits for `oa_attachment_fallbacks`, `trusted-document`, or `可信材料`. Hits for the word `fallback` outside this subsystem must be inspected and left only if they are unrelated existing behavior.

- [ ] **Step 4: Commit**

```bash
git add docs/agent-owned-material-reading.md docs/universal-consumer-agent.md
git commit -m "docs: define agent-owned material reading"
```

## Task 7: Full Verification, Restart, And Backlog Check

**Files:**
- No source file changes expected.

- [ ] **Step 1: Run targeted regression tests**

Run:

```bash
pytest tests/test_universal_context.py tests/test_universal_context_enrichment.py tests/test_universal_planner.py tests/test_worker.py tests/test_oa_approval.py tests/test_prompt.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 3: Verify no service-side material body readers remain**

Run:

```bash
rg -n "_read_calendar_linked_documents|_read_linked_alidocs_node|_read_linked_aitable|_read_linked_minutes|_read_referenced_file\\(|oa_attachment_fallbacks|trusted-document|可信材料" app tests docs
```

Expected: no hits.

- [ ] **Step 4: Restart launchd service**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
```

Expected: command exits 0.

- [ ] **Step 5: Verify launchd status**

Run:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: output shows a running `pid` and no immediate crash reason.

- [ ] **Step 6: Check unresolved backlog**

Run:

```bash
sqlite3 data/auto-reply.sqlite3 "
select 'reply_tasks', status, count(*) from reply_tasks where status in ('failed','processing') group by status
union all
select 'work_summary_inputs', status, count(*) from work_summary_inputs where status in ('failed','processing') group by status
union all
select 'reply_attempts', send_status, count(*) from reply_attempts where send_status in ('failed','blocked') group by send_status;
"
```

Expected: counts are understood and reported. Do not claim the service is fully fixed if unresolved rows remain.

- [ ] **Step 7: Push commits**

Run:

```bash
git status --short
git push
```

Expected: only intended changes are committed; push succeeds.

## Self-Review

**Spec coverage:** The plan covers the user requirement that service code must not replace agent material work, honors the no legacy/fallback rule, keeps necessary service-owned safety boundaries, covers OA folder and attachment cases, updates planner instructions, tests regression behavior, documents the boundary, and includes restart/backlog verification.

**Placeholder scan:** The plan contains no empty placeholder markers and no step that says only “write tests” without code. Commands and expected outcomes are explicit.

**Type consistency:** `UniversalMaterialReference` is introduced in Task 1 and reused by worker and tests in later tasks. `material_references` is consistently a `tuple[UniversalMaterialReference, ...]`. OA detail uses `oa_material_references` consistently after Task 3.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-25-agent-owned-material-reading.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
