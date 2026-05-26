# Derek Work Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repo-local Derek work profile system that collects evidence from existing corpus, local authored documents, and read-only DingTalk knowledge base documents, then exposes the generated profile to `ceo-agent-service` without depending on a global skill at runtime.

**Architecture:** Add a small `work_profile` module that owns paths, evidence records, profile rules, rendering, and profile extraction orchestration. Keep live DingTalk knowledge base access in `DwsClient` as read-only methods. Wire the runtime prompt to read `profiles/derek_work_profile.md` when present while preserving current guardrails when the profile is absent.

**Tech Stack:** Python 3.11, Pydantic v2, existing `dws` CLI wrapper, existing `codex exec` pattern, pytest.

---

## File Structure

- Create `apps/local-service/ceo_agent_service/work_profile.py`
  - Owns profile paths, evidence/profile Pydantic models, local evidence collection, profile rendering, and an injectable extraction runner.
- Modify `apps/local-service/ceo_agent_service/config.py`
  - Adds `work_profile_path()` and `profile_evidence_dir()` path helpers.
- Modify `apps/local-service/ceo_agent_service/prompt.py`
  - Adds a profile-reading instruction when the repo profile exists.
- Modify `apps/local-service/ceo_agent_service/dws_client.py`
  - Adds read-only DingTalk knowledge base list/info helpers.
- Modify `apps/local-service/ceo_agent_service/cli.py`
  - Adds `build-work-profile` command.
- Create `apps/local-service/tests/test_work_profile.py`
  - Covers path defaults, evidence model behavior, local evidence collection, profile rendering, and prompt-safe snippets.
- Modify `apps/local-service/tests/test_codex_runner.py`
  - Covers profile instruction present/absent behavior.
- Modify `apps/local-service/tests/test_dws_client.py`
  - Covers read-only DWS doc list/info command construction.
- Inspect `.gitignore`
  - Confirm `data/profile-evidence/` remains ignored through the existing `data/*` rule and add no exception for evidence data.
- Create generated project assets during implementation:
  - `profiles/derek_work_profile.md`
  - `profiles/derek_work_profile.json`
  - `profiles/derek-skill/SKILL.md`

---

### Task 1: Runtime Profile Path And Prompt Integration

**Files:**
- Modify: `apps/local-service/ceo_agent_service/config.py`
- Modify: `apps/local-service/ceo_agent_service/prompt.py`
- Modify: `apps/local-service/tests/test_codex_runner.py`

- [ ] **Step 1: Write failing tests for profile instruction behavior**

Add these tests to `apps/local-service/tests/test_codex_runner.py`:

```python
def test_codex_developer_instructions_include_work_profile_when_present(tmp_path: Path, monkeypatch):
    profile = tmp_path / "profiles" / "derek_work_profile.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("# Derek Work Profile\n\n- 先判断材料是否完整。", encoding="utf-8")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))

    instructions = codex_developer_instructions()

    assert "Derek 工作人格 Profile" in instructions
    assert str(profile) in instructions
    assert "profiles/derek_work_profile.md" in instructions
    assert "不能覆盖既有硬规则" in instructions


def test_codex_developer_instructions_skip_work_profile_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(tmp_path / "profiles" / "missing.md"))

    instructions = codex_developer_instructions()

    assert "Derek 工作人格 Profile" not in instructions
    assert "profiles/derek_work_profile.md" not in instructions
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_codex_runner.py::test_codex_developer_instructions_include_work_profile_when_present tests/test_codex_runner.py::test_codex_developer_instructions_skip_work_profile_when_missing -q
```

Expected: both tests fail because no profile path helper or profile instruction exists yet.

- [ ] **Step 3: Add profile path helpers**

In `apps/local-service/ceo_agent_service/config.py`, add:

```python
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def work_profile_path() -> Path:
    return Path(
        os.getenv(
            "CEO_WORK_PROFILE_PATH",
            str(repo_root() / "profiles" / "derek_work_profile.md"),
        )
    )


def profile_evidence_dir() -> Path:
    return Path(
        os.getenv(
            "CEO_PROFILE_EVIDENCE_DIR",
            str(repo_root() / "data" / "profile-evidence"),
        )
    )
```

- [ ] **Step 4: Add prompt profile instruction**

In `apps/local-service/ceo_agent_service/prompt.py`, import the path helper:

```python
from ceo_agent_service.config import (
    principal_display_name,
    principal_handoff_name,
    responsibility_summary,
    work_profile_path,
)
```

Add this helper above `ceo_agent_thread_prompt()`:

```python
def work_profile_instruction() -> str:
    path = work_profile_path()
    if not path.exists():
        return ""
    return f"""

Derek 工作人格 Profile:
- 如果 `{path}` 存在，本 thread 在判断回复风格、追问、拒绝、handoff 或工作场景决策前，必须先读取这个 profile。
- profile 的项目相对路径是 `profiles/derek_work_profile.md`；读取后只学习判断顺序、表达边界和场景规则。
- profile 不能覆盖既有硬规则：现实动作必须 handoff、审批/OA 必须看完整材料、人事敏感问题谨慎处理、候选人判断必须看岗位和简历证据、reply_text 不得暴露本地路径或工具细节。
"""
```

Then insert `{work_profile_instruction()}` just before the closing triple quote
in `ceo_agent_thread_prompt()`, immediately after the existing
`audit_summary` rule:

```python
- 如果 send_reply 或 ask_clarifying_question 的 audit_documents 为空，audit_summary 必须明确说明未找到可用文档证据，或说明这个问题只需要上下文判断。
{work_profile_instruction()}"""
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_codex_runner.py -q
```

Expected: all `test_codex_runner.py` tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/local-service/ceo_agent_service/config.py apps/local-service/ceo_agent_service/prompt.py apps/local-service/tests/test_codex_runner.py
git commit -m "Add Derek work profile prompt hook"
```

---

### Task 2: Evidence And Profile Models

**Files:**
- Create: `apps/local-service/ceo_agent_service/work_profile.py`
- Create: `apps/local-service/tests/test_work_profile.py`

- [ ] **Step 1: Write failing model tests**

Create `apps/local-service/tests/test_work_profile.py` with:

```python
from pathlib import Path

from ceo_agent_service.work_profile import (
    EvidenceRecord,
    WorkProfile,
    WorkProfileRule,
    evidence_id,
    safe_excerpt,
)


def test_evidence_id_is_stable_for_same_source():
    first = evidence_id("dingtalk", "message-1", "材料不足先追问")
    second = evidence_id("dingtalk", "message-1", "材料不足先追问")

    assert first == second
    assert first.startswith("ev_")


def test_safe_excerpt_collapses_whitespace_and_limits_length():
    excerpt = safe_excerpt("第一行\n\n第二行 " * 20, limit=30)

    assert "\n" not in excerpt
    assert len(excerpt) <= 31
    assert excerpt.endswith("…")


def test_work_profile_rule_requires_evidence_ids():
    rule = WorkProfileRule(
        id="rule_materials_before_decision",
        title="材料不足不拍板",
        category="decision",
        scenarios=["approval"],
        trigger="缺少正文、预算、责任人或附件",
        do="先追问缺失材料",
        dont="不要给批准或拒绝结论",
        confidence="high",
        evidence_ids=["ev_abc"],
    )

    assert rule.evidence_ids == ["ev_abc"]


def test_work_profile_serializes_rules():
    evidence = EvidenceRecord(
        id="ev_abc",
        source_type="dingtalk",
        title="审批沟通",
        timestamp="2026-05-26T10:00:00",
        location="cid-1/msg-1",
        scenario="approval",
        evidence_strength="behavior_high",
        sensitivity="approval",
        excerpt="材料不足，先补齐附件。",
        usable_for_profile=True,
    )
    profile = WorkProfile(
        title="Derek Work Profile",
        summary="工作判断 profile",
        rules=[
            WorkProfileRule(
                id="rule_materials_before_decision",
                title="材料不足不拍板",
                category="decision",
                scenarios=["approval"],
                trigger="缺少正文、预算、责任人或附件",
                do="先追问缺失材料",
                dont="不要给批准或拒绝结论",
                confidence="high",
                evidence_ids=[evidence.id],
            )
        ],
    )

    assert profile.model_dump()["rules"][0]["id"] == "rule_materials_before_decision"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_work_profile.py -q
```

Expected: import failure for `ceo_agent_service.work_profile`.

- [ ] **Step 3: Implement the models**

Create `apps/local-service/ceo_agent_service/work_profile.py`:

```python
import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field


WHITESPACE_RE = re.compile(r"\s+")


def evidence_id(source_type: str, location: str, text: str) -> str:
    digest = hashlib.sha256(
        f"{source_type}\n{location}\n{text}".encode("utf-8")
    ).hexdigest()[:16]
    return f"ev_{digest}"


def safe_excerpt(text: str, limit: int = 240) -> str:
    normalized = WHITESPACE_RE.sub(" ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}…"


class EvidenceRecord(BaseModel):
    id: str
    source_type: str
    title: str = ""
    timestamp: str = ""
    location: str = ""
    scenario: str = "general"
    evidence_strength: str = "authored_assumed"
    sensitivity: str = "general"
    excerpt: str = ""
    usable_for_profile: bool = True


class WorkProfileRule(BaseModel):
    id: str
    title: str
    category: str
    scenarios: list[str] = Field(default_factory=list)
    trigger: str
    do: str
    dont: str
    confidence: str
    evidence_ids: list[str] = Field(min_length=1)


class WorkProfile(BaseModel):
    title: str
    summary: str
    rules: list[WorkProfileRule] = Field(default_factory=list)


def write_jsonl(path: Path, records: list[EvidenceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_work_profile.py -q
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/local-service/ceo_agent_service/work_profile.py apps/local-service/tests/test_work_profile.py
git commit -m "Add Derek work profile models"
```

---

### Task 3: Local Evidence Collection

**Files:**
- Modify: `apps/local-service/ceo_agent_service/work_profile.py`
- Modify: `apps/local-service/tests/test_work_profile.py`

- [ ] **Step 1: Write failing local evidence tests**

Append to `apps/local-service/tests/test_work_profile.py`:

```python
from ceo_agent_service.corpus import CorpusRecord, write_records
from ceo_agent_service.work_profile import collect_existing_corpus_evidence, collect_local_doc_evidence


def test_collect_existing_corpus_evidence_reads_style_corpus(tmp_path: Path):
    csv_path = tmp_path / "corpus" / "derek_style_corpus.csv"
    write_records(
        csv_path,
        [
            CorpusRecord(
                source_type="dingtalk",
                source_title="客户合作群",
                timestamp="2026-05-26T10:00:00",
                context="客户问是否能今天给最终方案",
                derek_reply="先别承诺最终版，先把客户目标和交付边界收敛清楚。",
                message_id="msg-1",
                conversation_id="cid-1",
                speaker_name="Derek",
                metadata_json="{}",
            )
        ],
    )

    records = collect_existing_corpus_evidence(csv_path)

    assert len(records) == 1
    assert records[0].source_type == "dingtalk"
    assert records[0].evidence_strength == "behavior_high"
    assert "先别承诺最终版" in records[0].excerpt


def test_collect_local_doc_evidence_prefers_thinking_and_strategy_dirs(tmp_path: Path):
    workspace = tmp_path / "memory"
    thinking = workspace / "Thinking"
    strategy = workspace / "management" / "strategy"
    ignored = workspace / ".smart-env"
    thinking.mkdir(parents=True)
    strategy.mkdir(parents=True)
    ignored.mkdir(parents=True)
    (thinking / "CEO 如何使用agent提效.md").write_text("先把问题拆成目标、证据、下一步。", encoding="utf-8")
    (strategy / "Q2 strategy.md").write_text("战略判断先看客户价值和交付闭环。", encoding="utf-8")
    (ignored / "cache.md").write_text("不应该进入 profile。", encoding="utf-8")

    records = collect_local_doc_evidence(workspace)

    assert {record.title for record in records} == {
        "CEO 如何使用agent提效.md",
        "Q2 strategy.md",
    }
    assert all(record.evidence_strength == "authored_high" for record in records)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_work_profile.py::test_collect_existing_corpus_evidence_reads_style_corpus tests/test_work_profile.py::test_collect_local_doc_evidence_prefers_thinking_and_strategy_dirs -q
```

Expected: import failure for the two collection functions.

- [ ] **Step 3: Implement local evidence collection**

Add to `apps/local-service/ceo_agent_service/work_profile.py`:

```python
from ceo_agent_service.corpus import load_corpus_records


LOCAL_AUTHORED_DIRS = (
    Path("Thinking"),
    Path("management") / "strategy",
    Path("management"),
    Path("business"),
    Path("product"),
)
LOCAL_TEXT_SUFFIXES = {".md", ".txt"}
LOCAL_IGNORED_PARTS = {".smart-env", ".dws", ".obsidian", "AI听记"}


def collect_existing_corpus_evidence(csv_path: Path) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for item in load_corpus_records(csv_path):
        location = f"{item.conversation_id}/{item.message_id}"
        records.append(
            EvidenceRecord(
                id=evidence_id(item.source_type, location, item.derek_reply),
                source_type=item.source_type,
                title=item.source_title,
                timestamp=item.timestamp,
                location=location,
                scenario="general",
                evidence_strength="behavior_high",
                sensitivity="general",
                excerpt=safe_excerpt(item.derek_reply),
                usable_for_profile=True,
            )
        )
    return records


def collect_local_doc_evidence(workspace: Path) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for base in LOCAL_AUTHORED_DIRS:
        root = workspace / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in LOCAL_TEXT_SUFFIXES:
                continue
            if any(part in LOCAL_IGNORED_PARTS for part in path.relative_to(workspace).parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            relative = str(path.relative_to(workspace))
            strength = "authored_high" if base in {Path("Thinking"), Path("management") / "strategy"} else "authored_assumed"
            records.append(
                EvidenceRecord(
                    id=evidence_id("local_doc", relative, text[:1000]),
                    source_type="local_doc",
                    title=path.name,
                    timestamp="",
                    location=relative,
                    scenario="general",
                    evidence_strength=strength,
                    sensitivity="general",
                    excerpt=safe_excerpt(text),
                    usable_for_profile=True,
                )
            )
    return records
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_work_profile.py -q
```

Expected: all work profile tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/local-service/ceo_agent_service/work_profile.py apps/local-service/tests/test_work_profile.py
git commit -m "Collect local Derek profile evidence"
```

---

### Task 4: Read-Only DingTalk Knowledge Base Collection

**Files:**
- Modify: `apps/local-service/ceo_agent_service/dws_client.py`
- Modify: `apps/local-service/ceo_agent_service/work_profile.py`
- Modify: `apps/local-service/tests/test_dws_client.py`
- Modify: `apps/local-service/tests/test_work_profile.py`

- [ ] **Step 1: Write failing DWS command tests**

Append to `apps/local-service/tests/test_dws_client.py`:

```python
from ceo_agent_service.dws_client import DwsClient


def test_build_doc_list_command_uses_read_only_list():
    client = DwsClient(dws_bin="dws")

    assert client.build_doc_list_command(workspace_id="space-1", folder_id=None, page_token="") == [
        "dws",
        "doc",
        "list",
        "--workspace",
        "space-1",
        "--format",
        "json",
    ]


def test_build_doc_info_command_is_read_only():
    client = DwsClient(dws_bin="dws")

    assert client.build_doc_info_command("node-1") == [
        "dws",
        "doc",
        "info",
        "--node",
        "node-1",
        "--format",
        "json",
    ]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_dws_client.py::test_build_doc_list_command_uses_read_only_list tests/test_dws_client.py::test_build_doc_info_command_is_read_only -q
```

Expected: missing method failures.

- [ ] **Step 3: Add read-only DWS commands**

In `apps/local-service/ceo_agent_service/dws_client.py`, add:

```python
    def build_doc_list_command(
        self,
        workspace_id: str | None = None,
        folder_id: str | None = None,
        page_token: str = "",
    ) -> list[str]:
        command = [self.dws_bin, "doc", "list"]
        if workspace_id:
            command.extend(["--workspace", workspace_id])
        if folder_id:
            command.extend(["--folder", folder_id])
        if page_token:
            command.extend(["--page-token", page_token])
        command.extend(["--format", "json"])
        return command

    def build_doc_info_command(self, node: str) -> list[str]:
        return [
            self.dws_bin,
            "doc",
            "info",
            "--node",
            node,
            "--format",
            "json",
        ]

    def list_doc_nodes(
        self,
        workspace_id: str | None = None,
        folder_id: str | None = None,
        page_token: str = "",
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_doc_list_command(
                workspace_id=workspace_id,
                folder_id=folder_id,
                page_token=page_token,
            )
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid doc list response")
        return payload

    def doc_info(self, node: str) -> dict[str, Any]:
        payload = self.run_json(self.build_doc_info_command(node))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc info response")
        return payload
```

- [ ] **Step 4: Write failing knowledge base collector test**

Append to `apps/local-service/tests/test_work_profile.py`:

```python
from ceo_agent_service.work_profile import collect_dingtalk_kb_evidence


class FakeDwsForKnowledgeBase:
    def __init__(self):
        self.read_nodes = []

    def list_doc_nodes(self, workspace_id=None, folder_id=None, page_token=""):
        return {
            "result": {
                "nodes": [
                    {
                        "nodeId": "doc-1",
                        "name": "战略判断.md",
                        "nodeType": "file",
                        "contentType": "ALIDOC",
                        "extension": "adoc",
                    }
                ],
                "nextToken": None,
            }
        }

    def doc_info(self, node):
        return {"result": {"nodeId": node, "name": "战略判断.md", "creatorName": "Derek"}}

    def read_doc(self, node):
        self.read_nodes.append(node)
        return {"result": {"markdown": "判断客户合作先看目标、边界和交付闭环。"}}


def test_collect_dingtalk_kb_evidence_reads_online_docs_to_cache(tmp_path: Path):
    dws = FakeDwsForKnowledgeBase()

    records = collect_dingtalk_kb_evidence(
        dws=dws,
        cache_dir=tmp_path / "cache",
        workspace_id="space-1",
    )

    assert dws.read_nodes == ["doc-1"]
    assert len(records) == 1
    assert records[0].source_type == "dingtalk_kb_live"
    assert records[0].evidence_strength == "kb_live_doc"
    assert "客户合作" in records[0].excerpt
    assert (tmp_path / "cache" / "doc-1.md").exists()
```

- [ ] **Step 5: Implement knowledge base collector**

Add to `apps/local-service/ceo_agent_service/work_profile.py`:

```python
def _doc_nodes_from_payload(payload: dict) -> list[dict]:
    result = payload.get("result", payload)
    if isinstance(result, dict):
        nodes = result.get("nodes") or result.get("items") or result.get("list") or []
        return [node for node in nodes if isinstance(node, dict)]
    return []


def _doc_markdown_from_payload(payload: dict) -> str:
    result = payload.get("result", payload)
    if isinstance(result, dict):
        markdown = result.get("markdown") or result.get("content") or result.get("text") or ""
        return str(markdown)
    return ""


def collect_dingtalk_kb_evidence(
    *,
    dws,
    cache_dir: Path,
    workspace_id: str | None = None,
    folder_id: str | None = None,
    limit: int = 200,
) -> list[EvidenceRecord]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    records: list[EvidenceRecord] = []
    payload = dws.list_doc_nodes(workspace_id=workspace_id, folder_id=folder_id)
    for node in _doc_nodes_from_payload(payload):
        if len(records) >= limit:
            break
        node_id = str(node.get("nodeId") or node.get("dentryUuid") or "")
        if not node_id:
            continue
        extension = str(node.get("extension") or "").lower()
        content_type = str(node.get("contentType") or "").upper()
        if extension != "adoc" and content_type != "ALIDOC":
            continue
        info = dws.doc_info(node_id)
        markdown = _doc_markdown_from_payload(dws.read_doc(node_id)).strip()
        if not markdown:
            continue
        cache_path = cache_dir / f"{node_id}.md"
        cache_path.write_text(markdown, encoding="utf-8")
        info_result = info.get("result", info) if isinstance(info, dict) else {}
        title = str(info_result.get("name") or node.get("name") or node_id)
        location = f"dingtalk-kb:{node_id}"
        records.append(
            EvidenceRecord(
                id=evidence_id("dingtalk_kb_live", location, markdown[:1000]),
                source_type="dingtalk_kb_live",
                title=title,
                timestamp=str(info_result.get("modifiedTime") or info_result.get("createdTime") or ""),
                location=location,
                scenario="general",
                evidence_strength="kb_live_doc",
                sensitivity="general",
                excerpt=safe_excerpt(markdown),
                usable_for_profile=True,
            )
        )
    return records
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_dws_client.py::test_build_doc_list_command_uses_read_only_list tests/test_dws_client.py::test_build_doc_info_command_is_read_only tests/test_work_profile.py::test_collect_dingtalk_kb_evidence_reads_online_docs_to_cache -q
```

Expected: tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/local-service/ceo_agent_service/dws_client.py apps/local-service/ceo_agent_service/work_profile.py apps/local-service/tests/test_dws_client.py apps/local-service/tests/test_work_profile.py
git commit -m "Collect DingTalk knowledge base profile evidence"
```

---

### Task 5: Profile Rendering And Derived Skill Assets

**Files:**
- Modify: `apps/local-service/ceo_agent_service/work_profile.py`
- Modify: `apps/local-service/tests/test_work_profile.py`
- Create: `profiles/derek_work_profile.md`
- Create: `profiles/derek_work_profile.json`
- Create: `profiles/derek-skill/SKILL.md`

- [ ] **Step 1: Write failing renderer test**

Append to `apps/local-service/tests/test_work_profile.py`:

```python
from ceo_agent_service.work_profile import render_markdown_profile, render_skill


def test_render_markdown_profile_contains_required_sections():
    profile = WorkProfile(
        title="Derek Work Profile",
        summary="用于钉钉自动回复的工作判断 profile。",
        rules=[
            WorkProfileRule(
                id="rule_materials_before_decision",
                title="材料不足不拍板",
                category="decision",
                scenarios=["approval", "business"],
                trigger="需要判断但材料不完整",
                do="先追问缺失材料",
                dont="不要给确定结论",
                confidence="high",
                evidence_ids=["ev_abc"],
            )
        ],
    )

    markdown = render_markdown_profile(profile)

    assert "# Derek Work Profile" in markdown
    assert "## Core Judgment Order" in markdown
    assert "## Decision Framework" in markdown
    assert "## Boundary Framework" in markdown
    assert "材料不足不拍板" in markdown


def test_render_skill_marks_manual_use_not_runtime_dependency():
    profile = WorkProfile(title="Derek Work Profile", summary="工作判断 profile。", rules=[])

    skill = render_skill(profile)

    assert "name: derek-perspective" in skill
    assert "not Derek himself" in skill
    assert "Do not use this skill as the automated DingTalk runtime" in skill
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_work_profile.py::test_render_markdown_profile_contains_required_sections tests/test_work_profile.py::test_render_skill_marks_manual_use_not_runtime_dependency -q
```

Expected: missing renderer functions.

- [ ] **Step 3: Implement renderers**

Add to `apps/local-service/ceo_agent_service/work_profile.py`:

```python
def _rules_by_category(profile: WorkProfile, category: str) -> list[WorkProfileRule]:
    return [rule for rule in profile.rules if rule.category == category]


def _rule_lines(rule: WorkProfileRule) -> list[str]:
    scenarios = ", ".join(rule.scenarios) if rule.scenarios else "general"
    return [
        f"### {rule.title}",
        "",
        f"- Rule id: `{rule.id}`",
        f"- Scenarios: {scenarios}",
        f"- Trigger: {rule.trigger}",
        f"- Do: {rule.do}",
        f"- Do not: {rule.dont}",
        f"- Confidence: {rule.confidence}",
        "",
    ]


def render_markdown_profile(profile: WorkProfile) -> str:
    lines = [
        "# Derek Work Profile",
        "",
        profile.summary,
        "",
        "## Scope",
        "",
        "Use this profile for DingTalk auto-reply judgment, business communication, product judgment, management coordination, recruiting triage, and approval pre-review. It is not Derek's final personal decision.",
        "",
        "## Core Judgment Order",
        "",
        "1. Decide whether Derek needs to reply.",
        "2. Check whether the material is complete.",
        "3. Check hard boundaries before making any commitment.",
        "4. Reply with conclusion, reason, and next step when enough evidence exists.",
        "5. Ask a focused follow-up when evidence is missing.",
        "",
        "## Decision Framework",
        "",
    ]
    for rule in _rules_by_category(profile, "decision"):
        lines.extend(_rule_lines(rule))
    lines.extend(["## Expression Framework", ""])
    for rule in _rules_by_category(profile, "expression"):
        lines.extend(_rule_lines(rule))
    lines.extend(["## Follow-Up Framework", ""])
    for rule in _rules_by_category(profile, "follow_up"):
        lines.extend(_rule_lines(rule))
    lines.extend(["## Boundary Framework", ""])
    for rule in _rules_by_category(profile, "boundary"):
        lines.extend(_rule_lines(rule))
    lines.extend(
        [
            "## Honest Boundaries",
            "",
            "- This profile is inferred from local work evidence and authored material.",
            "- It improves draft judgment but does not replace Derek's final decision.",
            "- It must not override the service's hard safety and privacy guardrails.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_skill(profile: WorkProfile) -> str:
    return f"""---
name: derek-perspective
description: Derek's work perspective for reviewing drafts, decisions, and business communication. Use when the user asks for Derek's angle, Derek work style, or Derek perspective.
---

# Derek Work Perspective

This skill represents Derek's work perspective based on local evidence. It is not Derek himself and does not authorize final real-world decisions.

Do not use this skill as the automated DingTalk runtime. The runtime reads `profiles/derek_work_profile.md` inside `ceo-agent-service`.

## Scope

{profile.summary}

## Hard Boundaries

- Do not claim Derek has joined a meeting, made a call, checked a message, approved a request, or completed a real-world action.
- Do not make final personnel, approval, finance, legal, or customer-critical decisions.
- When material is incomplete, ask for the missing material instead of inventing a conclusion.
"""
```

- [ ] **Step 4: Add deterministic first profile rule set**

Add this function to `apps/local-service/ceo_agent_service/work_profile.py`:

```python
def build_initial_profile(evidence: list[EvidenceRecord]) -> WorkProfile:
    usable_ids = [record.id for record in evidence if record.usable_for_profile]
    fallback_id = usable_ids[0] if usable_ids else "ev_manual_profile_seed"
    return WorkProfile(
        title="Derek Work Profile",
        summary="A work-context profile for Derek's DingTalk auto-reply agent, derived from local behavior evidence, authored documents, and read-only DingTalk knowledge base material.",
        rules=[
            WorkProfileRule(
                id="rule_materials_before_decision",
                title="材料不足不拍板",
                category="decision",
                scenarios=["approval", "candidate_review", "business", "document_review"],
                trigger="A message asks for approval, judgment, confirmation, comments, or finalization but lacks the body, background, budget, owner, role context, resume, attachment, or accessible link.",
                do="Ask for the specific missing material and say that a judgment can be made after the material is complete.",
                dont="Do not approve, reject, advance, finalize, or evaluate based only on a title or vague request.",
                confidence="high",
                evidence_ids=[fallback_id],
            ),
            WorkProfileRule(
                id="rule_real_world_actions_handoff",
                title="现实动作不代承诺",
                category="boundary",
                scenarios=["daily_coordination", "meeting", "handoff"],
                trigger="A message asks whether Derek has joined, called, checked, approved, gone onsite, or will immediately do a real-world action.",
                do="Hand off to Derek or state that Derek should personally handle it.",
                dont="Do not claim Derek is doing, will do immediately, or has done the action unless the conversation explicitly proves it.",
                confidence="high",
                evidence_ids=[fallback_id],
            ),
            WorkProfileRule(
                id="rule_short_conclusion_next_step",
                title="先结论再下一步",
                category="expression",
                scenarios=["business", "product", "management", "daily_coordination"],
                trigger="The agent has enough evidence to reply.",
                do="Give a concise conclusion, one reason when useful, and the next action.",
                dont="Do not write long background explanations, citations, local paths, or tool details.",
                confidence="medium",
                evidence_ids=[fallback_id],
            ),
            WorkProfileRule(
                id="rule_focus_follow_up",
                title="追问要收敛问题",
                category="follow_up",
                scenarios=["business", "product", "approval", "candidate_review"],
                trigger="The user request is broad or missing the key decision variable.",
                do="Ask one focused question that unlocks the next decision.",
                dont="Do not ask several broad questions or give generic advice before the key missing fact is known.",
                confidence="medium",
                evidence_ids=[fallback_id],
            ),
        ],
    )
```

- [ ] **Step 5: Generate first project assets**

Run a short Python command from `apps/local-service`:

```bash
cd apps/local-service
.venv/bin/python - <<'PY'
from pathlib import Path
import json
from ceo_agent_service.work_profile import build_initial_profile, render_markdown_profile, render_skill

repo = Path.cwd().parents[1]
profile = build_initial_profile([])
(repo / "profiles" / "derek-skill").mkdir(parents=True, exist_ok=True)
(repo / "profiles" / "derek_work_profile.md").write_text(render_markdown_profile(profile), encoding="utf-8")
(repo / "profiles" / "derek_work_profile.json").write_text(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(repo / "profiles" / "derek-skill" / "SKILL.md").write_text(render_skill(profile), encoding="utf-8")
PY
```

Expected:

```text
profiles/derek_work_profile.md
profiles/derek_work_profile.json
profiles/derek-skill/SKILL.md
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_work_profile.py -q
```

Expected: all work profile tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/local-service/ceo_agent_service/work_profile.py apps/local-service/tests/test_work_profile.py profiles/derek_work_profile.md profiles/derek_work_profile.json profiles/derek-skill/SKILL.md
git commit -m "Add Derek work profile assets"
```

---

### Task 6: Build-Work-Profile CLI And Evidence Boundary Tests

**Files:**
- Modify: `apps/local-service/ceo_agent_service/cli.py`
- Modify: `apps/local-service/tests/test_cli.py`
- Modify: `apps/local-service/tests/test_work_profile.py`

- [ ] **Step 1: Write failing CLI parser test**

Append to `apps/local-service/tests/test_cli.py`:

```python
def test_build_work_profile_command_is_registered():
    parser = build_parser()

    args = parser.parse_args(["build-work-profile", "--workspace", "/tmp/memory"])

    assert args.command == "build-work-profile"
    assert args.workspace == "/tmp/memory"
```

- [ ] **Step 2: Write failing command function test**

Append to `apps/local-service/tests/test_cli.py`:

```python
def test_build_work_profile_command_writes_repo_assets(tmp_path, monkeypatch):
    from ceo_agent_service import cli
    from ceo_agent_service.work_profile import EvidenceRecord

    workspace = tmp_path / "memory"
    corpus_dir = tmp_path / "corpus"
    evidence_dir = tmp_path / "data" / "profile-evidence"
    profile_path = tmp_path / "profiles" / "derek_work_profile.md"
    profile_json = tmp_path / "profiles" / "derek_work_profile.json"
    skill_path = tmp_path / "profiles" / "derek-skill" / "SKILL.md"

    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("CEO_PROFILE_EVIDENCE_DIR", str(evidence_dir))
    monkeypatch.setattr(
        cli,
        "collect_existing_corpus_evidence",
        lambda path: [
            EvidenceRecord(
                id="ev_abc",
                source_type="dingtalk",
                title="客户群",
                timestamp="2026-05-26T10:00:00",
                location="cid/msg",
                scenario="business",
                evidence_strength="behavior_high",
                sensitivity="general",
                excerpt="先收敛目标和边界。",
                usable_for_profile=True,
            )
        ],
    )
    monkeypatch.setattr(cli, "collect_local_doc_evidence", lambda path: [])
    monkeypatch.setattr(cli, "collect_dingtalk_kb_evidence", lambda **kwargs: [])

    settings = WorkerSettings(workspace=workspace, corpus_dir=corpus_dir)

    count = build_work_profile_command(settings, include_dingtalk_kb=False)

    assert count == 1
    assert profile_path.exists()
    assert profile_json.exists()
    assert skill_path.exists()
    assert (evidence_dir / "evidence_index.jsonl").exists()
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_cli.py::test_build_work_profile_command_is_registered tests/test_cli.py::test_build_work_profile_command_writes_repo_assets -q
```

Expected: missing parser command and command function.

- [ ] **Step 4: Import work profile helpers in CLI**

In `apps/local-service/ceo_agent_service/cli.py`, add imports:

```python
from ceo_agent_service.config import profile_evidence_dir, work_profile_path
from ceo_agent_service.work_profile import (
    build_initial_profile,
    collect_dingtalk_kb_evidence,
    collect_existing_corpus_evidence,
    collect_local_doc_evidence,
    render_markdown_profile,
    render_skill,
    write_jsonl,
)
```

- [ ] **Step 5: Register the command**

Add `"build-work-profile"` to the command tuple in `build_parser()`.

After the shared argument setup, add command-specific arguments:

```python
        if command == "build-work-profile":
            subparser.add_argument(
                "--include-dingtalk-kb",
                action="store_true",
                help="read online DingTalk knowledge base docs in read-only mode",
            )
            subparser.add_argument(
                "--dingtalk-kb-workspace",
                default=os.getenv("CEO_DINGTALK_KB_WORKSPACE", ""),
                help="DingTalk knowledge base workspace id or URL for read-only profile evidence",
            )
```

- [ ] **Step 6: Implement the command function**

Add to `apps/local-service/ceo_agent_service/cli.py` before `probe_dws()`:

```python
def build_work_profile_command(
    settings: WorkerSettings,
    *,
    include_dingtalk_kb: bool = False,
    dingtalk_kb_workspace: str = "",
) -> int:
    evidence_dir = profile_evidence_dir()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = []
    evidence.extend(collect_existing_corpus_evidence(settings.corpus_dir / "derek_style_corpus.csv"))
    evidence.extend(collect_local_doc_evidence(settings.workspace))
    if include_dingtalk_kb:
        evidence.extend(
            collect_dingtalk_kb_evidence(
                dws=DwsClient(),
                cache_dir=evidence_dir / "dingtalk_kb_cache",
                workspace_id=dingtalk_kb_workspace or None,
            )
        )

    write_jsonl(evidence_dir / "evidence_index.jsonl", evidence)
    profile = build_initial_profile(evidence)
    profile_path = work_profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(render_markdown_profile(profile), encoding="utf-8")
    profile_path.with_suffix(".json").write_text(
        json.dumps(profile.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    skill_path = profile_path.parent / "derek-skill" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(render_skill(profile), encoding="utf-8")
    print(
        f"build-work-profile evidence={len(evidence)} profile={profile_path} skill={skill_path}",
        flush=True,
    )
    return len(evidence)
```

In `main()`, add:

```python
    elif args.command == "build-work-profile":
        build_work_profile_command(
            settings,
            include_dingtalk_kb=args.include_dingtalk_kb,
            dingtalk_kb_workspace=args.dingtalk_kb_workspace,
        )
```

- [ ] **Step 7: Run focused CLI tests**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_cli.py::test_build_work_profile_command_is_registered tests/test_cli.py::test_build_work_profile_command_writes_repo_assets -q
```

Expected: tests pass.

- [ ] **Step 8: Run full relevant tests**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_work_profile.py tests/test_codex_runner.py tests/test_dws_client.py tests/test_cli.py -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit**

```bash
git add apps/local-service/ceo_agent_service/cli.py apps/local-service/tests/test_cli.py
git commit -m "Add Derek work profile build command"
```

---

### Task 7: End-To-End Local Generation Check

**Files:**
- Modify generated files only if the command changes them:
  - `profiles/derek_work_profile.md`
  - `profiles/derek_work_profile.json`
  - `profiles/derek-skill/SKILL.md`
- Runtime data remains ignored:
  - `data/profile-evidence/evidence_index.jsonl`
  - `data/profile-evidence/dingtalk_kb_cache/`

- [ ] **Step 1: Run local generation without live DingTalk knowledge base**

Run:

```bash
cd apps/local-service
.venv/bin/python -m ceo_agent_service.cli build-work-profile --workspace /Users/derek/Documents/memory
```

Expected output contains:

```text
build-work-profile evidence=
profile=/Users/derek/Documents/Projects/ceo-agent-service/profiles/derek_work_profile.md
```

- [ ] **Step 2: Verify generated files exist**

Run:

```bash
test -f profiles/derek_work_profile.md
test -f profiles/derek_work_profile.json
test -f profiles/derek-skill/SKILL.md
test -f data/profile-evidence/evidence_index.jsonl
```

Expected: all commands exit `0`.

- [ ] **Step 3: Verify evidence data is ignored**

Run:

```bash
git status --short -- data/profile-evidence profiles
```

Expected: profile files may be tracked or modified; `data/profile-evidence` does not appear because `data/*` ignores it.

- [ ] **Step 4: Run prompt integration smoke test**

Run:

```bash
cd apps/local-service
.venv/bin/pytest tests/test_codex_runner.py::test_codex_developer_instructions_include_work_profile_when_present -q
```

Expected: test passes.

- [ ] **Step 5: Commit regenerated profile assets if changed**

If `git status --short profiles` shows modified profile assets, commit them:

```bash
git add profiles/derek_work_profile.md profiles/derek_work_profile.json profiles/derek-skill/SKILL.md
git commit -m "Refresh Derek work profile assets"
```

If no profile assets changed, do not create an empty commit.

---

## Self-Review

Spec coverage:

- Repo-local profile assets are covered by Tasks 5 and 7.
- Existing corpus reuse is covered by Task 3.
- Local knowledge base input is covered by Task 3.
- Read-only DingTalk knowledge base input is covered by Task 4 and Task 6.
- Runtime prompt integration without direct Nuwa dependency is covered by Task 1.
- Evidence cache staying ignored is covered by Task 6 and Task 7.
- Derived Nuwa-style skill is covered by Task 5.

Placeholder scan:

- The plan contains no unresolved sections or unresolved implementation names.
- Every code path introduced by a later task is defined in an earlier task or in the same task.

Type consistency:

- `EvidenceRecord`, `WorkProfileRule`, and `WorkProfile` are introduced in Task 2 and reused consistently.
- `work_profile_path()` and `profile_evidence_dir()` are introduced in Task 1 and reused in Task 6.
- `collect_dingtalk_kb_evidence()` depends only on read-only DWS methods introduced in Task 4.
