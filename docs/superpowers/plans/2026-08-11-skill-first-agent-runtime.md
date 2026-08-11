# Skill-First Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace duplicated domain rules in CEO Agent prompts with seven dynamically loaded business Skills while preserving a small, always-loaded Consumer A / Audit B execution boundary.

**Architecture:** Codex continues to inherit the installer's normal `~/.codex` configuration and `~/.agents/skills`. Consumer A selects and reads the relevant business Skill through `agent_cli.read_skill`; the service derives a verified Skill receipt from the existing Codex tool event and supplies it to Audit B, which rereads the same Skill before reviewing or executing. The service never routes business content by keywords: it installs the Skills, transports verified Skill receipts, enforces role capabilities, persists lifecycle state, and performs exact duplicate/recovery handling.

**Tech Stack:** Python 3.12, Pydantic, SQLite, pytest, native `codex exec`, stdio MCP `agent_cli`, Markdown `SKILL.md` files, DWS CLI, launchd.

---

## Scope And File Structure

Create seven workflow Skills under the repository's distributable `skills/` bundle:

- `skills/ceo-message-triage/SKILL.md`: reply, reaction, clarification, and no-action judgment.
- `skills/ceo-calendar-invite/SKILL.md`: incoming calendar invitation review and response.
- `skills/ceo-document-review/SKILL.md`: DingTalk/Lark documents, files, images, and tables.
- `skills/ceo-meeting-work/SKILL.md`: minutes, silent meetings, meeting materials, summaries, and action items.
- `skills/ceo-mail-review/SKILL.md`: complete-thread mail review and reply workflow.
- `skills/ceo-personnel-communication/SKILL.md`: internal-personnel and candidate audience/visibility judgment.
- `skills/ceo-work-tracking/SKILL.md`: work extraction, project/TODO creation, follow-up, completion verification, and closure as one lifecycle.

Do not copy DWS command catalogs into these Skills. Each business Skill names the existing operation Skill it must load, such as `dingtalk-calendar`, `dingtalk-chat`, `dingtalk-doc`, `dingtalk-minutes`, or `dingtalk-mail`.

Modify these runtime units:

- `app/business_skills.py`: install and inspect the seven service-managed Skills.
- `app/setup_wizard.py`: make business Skill installation part of the normal tutorial/setup path.
- `app/agent_skill_usage.py`: derive verified Skill receipts from existing `agent_cli.read_skill` tool events.
- `app/agent_context.py`: carry verified Consumer Skill receipts into Audit B's context.
- `app/consumer_agent.py`: reduce always-loaded prose and require dynamic business Skill reading.
- `app/audit_agent.py`: require B to reread every verified business Skill used by A.
- `app/agent_orchestrator.py`: transport receipts from the completed Consumer run to Audit context.
- `app/task_agent.py`: replace the long work-item policy prompt with `ceo-work-tracking` Skill content plus the strict output contract.
- `data/prompts/developer_prompt.md` and `app/defaults/developer_prompt.md`: remove migrated domain rules from the superseded generic prompt while keeping one Skill-loading instruction for any remaining legacy invocation.
- `docs/architecture.md`, `docs/product-logic.md`, and `docs/message-routing-rules.md`: document the Skill-first source of business behavior.

Core rules that remain always loaded and must not move into a Skill:

1. Consumer A is Derek's read-only representative; Audit B is the only role allowed to execute an accepted candidate.
2. Output contracts and Pydantic field combinations are authoritative.
3. Supplied facts are reused; unsupported facts and targets are not invented.
4. A cannot write; B cannot silently change A's business meaning.
5. Exact duplicate effects are suppressed; a corrected revision remains executable.
6. Unknown effects enter read-only reconciliation and are never blindly replayed.
7. Credentials and runtime internals never enter external messages.
8. Authentication failures are surfaced; Agents never run login/reset/logout flows.

### Task 1: Package And Install The Seven Business Skills

**Files:**
- Create: `app/business_skills.py`
- Create: `skills/ceo-message-triage/SKILL.md`
- Create: `skills/ceo-calendar-invite/SKILL.md`
- Create: `skills/ceo-document-review/SKILL.md`
- Create: `skills/ceo-meeting-work/SKILL.md`
- Create: `skills/ceo-mail-review/SKILL.md`
- Create: `skills/ceo-personnel-communication/SKILL.md`
- Create: `skills/ceo-work-tracking/SKILL.md`
- Modify: `app/setup_wizard.py`
- Modify: `scripts/bootstrap-local-components.sh`
- Test: `tests/test_business_skills.py`
- Test: `tests/test_setup_wizard.py`
- Test: `tests/test_bootstrap_local_components.py`

- [ ] **Step 1: Write failing installation and inventory tests**

```python
def test_bundled_business_skills_have_unique_names_and_descriptions():
    skills = load_bundled_business_skills()
    assert [skill.name for skill in skills] == [
        "ceo-message-triage",
        "ceo-calendar-invite",
        "ceo-document-review",
        "ceo-meeting-work",
        "ceo-mail-review",
        "ceo-personnel-communication",
        "ceo-work-tracking",
    ]
    assert all(skill.description.strip() for skill in skills)


def test_install_bundled_business_skills_writes_complete_skill_directories(tmp_path):
    installed = install_bundled_business_skills(tmp_path / ".agents" / "skills")
    assert {item.name for item in installed} == set(BUNDLED_BUSINESS_SKILL_NAMES)
    for item in installed:
        text = (item.install_path / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {item.name}" in text
        assert "managed_by: ceo-agent-service" in text


def test_install_refuses_to_overwrite_user_owned_skill(tmp_path):
    target = tmp_path / ".agents" / "skills" / "ceo-calendar-invite"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user-owned content", encoding="utf-8")

    with pytest.raises(BusinessSkillInstallConflict):
        install_bundled_business_skills(tmp_path / ".agents" / "skills")
```

- [ ] **Step 2: Run the tests and verify the inventory does not exist**

Run: `pytest tests/test_business_skills.py tests/test_setup_wizard.py -k business_skill -q`

Expected: FAIL because `app.business_skills` and the bundled Skill directories do not exist.

- [ ] **Step 3: Implement the bundle installer with ownership-safe replacement**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from uuid import uuid4

from app.config import repo_root


BUNDLED_BUSINESS_SKILL_NAMES = (
    "ceo-message-triage",
    "ceo-calendar-invite",
    "ceo-document-review",
    "ceo-meeting-work",
    "ceo-mail-review",
    "ceo-personnel-communication",
    "ceo-work-tracking",
)


@dataclass(frozen=True)
class InstalledBusinessSkill:
    name: str
    install_path: Path


def bundled_business_skills_root() -> Path:
    return repo_root() / "skills"


def install_bundled_business_skills(target_root: Path) -> tuple[InstalledBusinessSkill, ...]:
    target_root.mkdir(parents=True, exist_ok=True)
    installed: list[InstalledBusinessSkill] = []
    for name in BUNDLED_BUSINESS_SKILL_NAMES:
        source = bundled_business_skills_root() / name / "SKILL.md"
        content = source.read_text(encoding="utf-8")
        target_dir = target_root / name
        existing = target_dir / "SKILL.md"
        if existing.exists() and "managed_by: ceo-agent-service" not in existing.read_text(
            encoding="utf-8"
        ):
            raise BusinessSkillInstallConflict(
                f"refusing to overwrite user-owned Skill: {target_dir}"
            )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        temporary = target.with_name(f"SKILL.md.{uuid4().hex}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        installed.append(InstalledBusinessSkill(name=name, install_path=target_dir))
    return tuple(installed)
```

Only directories already marked `managed_by: ceo-agent-service` may be upgraded in place. A same-name user-owned Skill is a visible setup conflict, not something the installer overwrites or silently renames.

Add a `ceo-business-skills` component to `scripts/bootstrap-local-components.sh`. It invokes the Python installer with `Path.home() / ".agents" / "skills"`, reports each installed Skill in the existing JSON component result, and supports `--component ceo-business-skills` for an isolated repair. The setup wizard continues to call the bootstrap script through its existing `setup_cli_components` action and treats a missing bundle, unreadable source, or ownership conflict as a failed setup component. It must not write to `~/.codex/skills`.

- [ ] **Step 4: Write the seven Skill frontmatters with precise native discovery descriptions**

Every file begins with this managed marker and a domain-specific description:

```yaml
---
name: ceo-calendar-invite
description: Use for incoming DingTalk calendar invitations, calendar cards, meeting invitations, attendance decisions, schedule conflicts, tentative/accept/decline responses, and questions about why Derek should attend or what input is expected. Load dingtalk-calendar before issuing any DWS calendar command.
metadata:
  managed_by: ceo-agent-service
  version: 1
---
```

Use equally explicit descriptions for the other six Skills. The descriptions must identify positive triggers and neighboring boundaries; they must not contain a hardcoded business-person name, percentage, project, or one-off failure case.

- [ ] **Step 5: Run installation tests**

Run: `pytest tests/test_business_skills.py tests/test_setup_wizard.py tests/test_bootstrap_local_components.py -k 'business_skill or skill_install' -q`

Expected: PASS.

- [ ] **Step 6: Commit the distributable Skill bundle**

```bash
git add app/business_skills.py app/setup_wizard.py scripts/bootstrap-local-components.sh skills tests/test_business_skills.py tests/test_setup_wizard.py tests/test_bootstrap_local_components.py
git commit -m "feat: install CEO business workflow skills"
```

### Task 2: Verify A's Dynamic Skill Reads And Make B Reread Them

**Files:**
- Create: `app/agent_skill_usage.py`
- Modify: `app/agent_context.py`
- Modify: `app/agent_orchestrator.py`
- Modify: `app/consumer_agent.py`
- Modify: `app/audit_agent.py`
- Test: `tests/test_agent_skill_usage.py`
- Test: `tests/test_agent_context.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_consumer_agent.py`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Write a failing test for verified Skill receipts**

```python
def test_loaded_skill_receipts_use_completed_agent_cli_events_only():
    events = (
        {
            "type": "mcp_tool_call",
            "server": "agent_cli",
            "tool": "read_skill",
            "status": "completed",
            "arguments": {"path": "/Users/derek/.agents/skills/ceo-calendar-invite/SKILL.md"},
            "result": {"sha256": "calendar-sha", "content": "ignored here"},
        },
        {
            "type": "mcp_tool_call",
            "server": "agent_cli",
            "tool": "read_skill",
            "status": "failed",
            "arguments": {"path": "/Users/derek/.agents/skills/ceo-mail-review/SKILL.md"},
            "result": {},
        },
    )
    assert loaded_skill_receipts(events) == (
        LoadedSkillReceipt(
            name="ceo-calendar-invite",
            path="/Users/derek/.agents/skills/ceo-calendar-invite/SKILL.md",
            sha256="calendar-sha",
        ),
    )
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest tests/test_agent_skill_usage.py -q`

Expected: FAIL because the receipt parser does not exist.

- [ ] **Step 3: Implement receipt extraction without a business router**

```python
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable


@dataclass(frozen=True)
class LoadedSkillReceipt:
    name: str
    path: str
    sha256: str


def loaded_skill_receipts(events: Iterable[dict[str, object]]) -> tuple[LoadedSkillReceipt, ...]:
    receipts: dict[str, LoadedSkillReceipt] = {}
    for event in events:
        if (
            event.get("type") != "mcp_tool_call"
            or event.get("server") != "agent_cli"
            or event.get("tool") != "read_skill"
            or event.get("status") != "completed"
        ):
            continue
        arguments = event.get("arguments")
        result = event.get("result")
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            continue
        path = str(arguments.get("path") or "").strip()
        digest = str(result.get("sha256") or "").strip()
        if not path or not digest:
            continue
        name = Path(path).parent.name
        receipts[path] = LoadedSkillReceipt(name=name, path=path, sha256=digest)
    return tuple(receipts[path] for path in sorted(receipts))
```

This function consumes existing Codex session tool events. It does not create a second audit log and does not infer a Skill from message text.

- [ ] **Step 4: Add Skill receipts to Audit context**

Add `consumer_skills: tuple[LoadedSkillReceipt, ...]` to `AuditTurnContext`. Render it as:

```text
Verified business Skills read by Consumer A
[
  {"name":"ceo-calendar-invite","path":".../SKILL.md","sha256":"..."}
]
```

In `AgentOrchestrator`, derive the tuple from the completed Consumer run's existing `tool_events` immediately before constructing `_NextAudit`. Do not persist a parallel receipt table.

- [ ] **Step 5: Require A to select Skills and B to reread verified paths**

Add this short invariant to Consumer instructions:

```text
Before making a domain judgment, inspect the installed Skill catalog and call
agent_cli.read_skill for the most specific applicable business Skill. Then load
the operation Skill named by that business Skill before proposing a concrete CLI
or MCP action. Do not ask the service to classify the domain for you.
```

Add this to Audit instructions:

```text
Reread every verified business Skill used by Consumer A and compare the returned
sha256 with the supplied receipt before reviewing or executing. Also read the
operation-specific Skill for each proposed capability. If a required Skill is
missing, unreadable, or changed, return revision_required; do not guess its rules.
```

- [ ] **Step 6: Add A/B orchestration tests**

Cover these exact cases:

1. A reads `ceo-calendar-invite`; B receives its path and SHA.
2. B reads the same file and proceeds when SHA matches.
3. B returns `revision_required` when the Skill changed between A and B.
4. A reads no business Skill for a domain task; B requests a replacement proposal instead of executing.
5. A may load more than one Skill for a cross-domain task, such as calendar plus document review.

Run: `pytest tests/test_agent_skill_usage.py tests/test_agent_context.py tests/test_agent_orchestrator.py tests/test_consumer_agent.py tests/test_audit_agent.py -q`

Expected: PASS.

- [ ] **Step 7: Commit verified Skill handoff**

```bash
git add app/agent_skill_usage.py app/agent_context.py app/agent_orchestrator.py app/consumer_agent.py app/audit_agent.py tests/test_agent_skill_usage.py tests/test_agent_context.py tests/test_agent_orchestrator.py tests/test_consumer_agent.py tests/test_audit_agent.py
git commit -m "feat: verify business skills across consumer and audit"
```

### Task 3: Move Calendar Invitation Judgment Into `ceo-calendar-invite`

**Files:**
- Modify: `skills/ceo-calendar-invite/SKILL.md`
- Modify: `data/prompts/developer_prompt.md`
- Modify: `app/defaults/developer_prompt.md`
- Test: `tests/test_calendar_skill.py`
- Test: `tests/test_agent_runtime_worker.py`
- Test: `tests/e2e/test_consumer_audit_live.py`

- [ ] **Step 1: Add failing calendar behavior tests**

Create table-driven cases for:

```python
CALENDAR_CASES = (
    ("clear_value", "accept", False),
    ("worth_holding_but_uncertain", "tentative", False),
    ("no_derek_input_needed", "decline", False),
    ("missing_attendance_value", "clarify_inviter", True),
    ("missing_description_but_clear_title", "accept", False),
    ("silent_meeting_with_material", "process_material_and_accept", False),
    ("silent_meeting_without_material", "clarify_missing_material", True),
)
```

The E2E fixture for `missing_attendance_value` must assert that A reads the calendar Skill and exact calendar event command, proposes one concrete question to the verified inviter, B rereads the Skill, sends the question, and verifies the sent message. It must not end as `needs_human`.

- [ ] **Step 2: Run calendar tests and verify the current A/B gap**

Run: `pytest tests/test_calendar_skill.py tests/test_agent_runtime_worker.py -k calendar -q`

Expected: FAIL because current A/B tests verify only the event reference/read command and do not require Skill loading or a delivered clarification.

- [ ] **Step 3: Write the complete calendar decision workflow**

The Skill body must contain these rules verbatim in meaning:

1. Load `dingtalk-calendar` before calendar reads or writes and `dingtalk-chat` before chat fallback.
2. Read title, time, organizer, attendees, description, comments, linked materials, self-response state, and conflicting accepted events.
3. Missing description alone is not a clarification condition.
4. Accept when Derek's decision, customer, product, personnel, or cross-team input has clear value.
5. Tentatively hold when the meeting is relevant but confirmation is premature.
6. Decline when it is only broadcast/synchronization and does not require Derek's input.
7. When participation value or requested input remains unclear, ask the verified inviter one concrete factual question; prefer calendar comment when the installed capability supports it, otherwise send in the source chat.
8. A resolvable factual question is a proposal, never `needs_human`.
9. For a silent meeting or asynchronous review, read and process linked material; do not merely accept the event.
10. If the silent-meeting task lacks its referenced material, ask for that exact material.
11. B rereads live event state before execution and suppresses only an already-applied exact response or already-sent exact clarification.

- [ ] **Step 4: Remove the calendar paragraph from both generic prompt copies**

Delete the domain paragraph beginning `如果新消息涉及日程、日历邀请或会议安排` from both prompt files. Keep only the generic instruction to load the applicable business Skill.

- [ ] **Step 5: Run calendar unit and live-contract E2E tests**

Run: `pytest tests/test_calendar_skill.py tests/test_agent_runtime_worker.py -k calendar tests/e2e/test_consumer_audit_live.py -q`

Expected: PASS, including the clarification-delivery case.

- [ ] **Step 6: Commit the calendar pilot**

```bash
git add skills/ceo-calendar-invite data/prompts/developer_prompt.md app/defaults/developer_prompt.md tests/test_calendar_skill.py tests/test_agent_runtime_worker.py tests/e2e/test_consumer_audit_live.py
git commit -m "feat: move calendar decisions into a business skill"
```

### Task 4: Move Message Triage And Document Review Into Skills

**Files:**
- Modify: `skills/ceo-message-triage/SKILL.md`
- Modify: `skills/ceo-document-review/SKILL.md`
- Modify: `data/prompts/developer_prompt.md`
- Modify: `app/defaults/developer_prompt.md`
- Test: `tests/test_message_triage_skill.py`
- Test: `tests/test_document_review_skill.py`
- Test: `tests/test_agent_runtime_worker.py`

- [ ] **Step 1: Write failing behavior tests**

Message triage cases:

- direct request requiring a decision produces a proposal;
- gratitude/acknowledgement with no responsibility change produces no text and an appropriate reaction proposal only when useful;
- `@all` broadcast with no Derek action produces no action;
- direct `@Agent_name` task is handled like a direct `@Derek` task;
- missing facts that the participant can answer produce one concrete clarification proposal, not A/B choices;
- newer context showing the matter completed suppresses a late reply or follow-up.

Document cases:

- online document loads `dingtalk-doc` and reads current content;
- AI table loads `dingtalk-aitable`, not document read;
- ordinary file uses the supplied exact download/read command;
- image input is actually read before conclusions;
- a changed document is reread instead of reusing an old conclusion;
- a readable document is reviewed directly rather than asking the sender to paste it;
- unavailable decisive material returns the actual dependency failure, not invented content.

- [ ] **Step 2: Run tests and verify domain behavior still depends on generic prose**

Run: `pytest tests/test_message_triage_skill.py tests/test_document_review_skill.py -q`

Expected: FAIL.

- [ ] **Step 3: Write both Skill workflows and remove their duplicated generic paragraphs**

`ceo-message-triage` owns whether to reply, react, clarify, or do nothing. `ceo-document-review` owns material type discovery, latest-version reading, review output, and comment-vs-chat delivery. Neither Skill may contain target-specific names, static business keywords, or a second output schema.

Delete migrated paragraphs from generic prompts: low-information replies/reactions, broadcast handling, document/file review, latest-material reread, and document comment behavior. Retain only the strict A/B result contract and unsupported-fact prohibition.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_message_triage_skill.py tests/test_document_review_skill.py tests/test_agent_runtime_worker.py tests/test_prompt.py -q`

Expected: PASS.

- [ ] **Step 5: Commit triage and document Skills**

```bash
git add skills/ceo-message-triage skills/ceo-document-review data/prompts/developer_prompt.md app/defaults/developer_prompt.md tests/test_message_triage_skill.py tests/test_document_review_skill.py tests/test_agent_runtime_worker.py tests/test_prompt.py
git commit -m "feat: move message and document judgment into skills"
```

### Task 5: Move Meeting And Mail Workflows Into Skills

**Files:**
- Modify: `skills/ceo-meeting-work/SKILL.md`
- Modify: `skills/ceo-mail-review/SKILL.md`
- Modify: `app/codex_runner.py`
- Modify: `data/prompts/developer_prompt.md`
- Modify: `app/defaults/developer_prompt.md`
- Test: `tests/test_meeting_work_skill.py`
- Test: `tests/test_mail_review_skill.py`
- Test: `tests/test_codex_runner.py`
- Test: `tests/test_meeting_alignment_agent.py`

- [ ] **Step 1: Write failing meeting and mail tests**

Meeting tests require the Agent to load `ceo-meeting-work` and `dingtalk-minutes`, read summary/tasks/transcript when needed, put each participant mention next to that person's concrete task or information, avoid a leading wall of duplicate mentions, and ask only for a specifically missing meeting material.

Mail tests require the Agent to load `ceo-mail-review` and `dingtalk-mail`, resolve the complete original/thread from a truncated card, inspect linked material, avoid duplicate replies, and propose a reply only when the current request authorizes it.

- [ ] **Step 2: Run tests and verify the current constants are still injected globally**

Run: `pytest tests/test_meeting_work_skill.py tests/test_mail_review_skill.py tests/test_codex_runner.py -q`

Expected: FAIL while `DWS_MATERIAL_READING_INSTRUCTIONS` and the global DingTalk mail block remain the source of behavior.

- [ ] **Step 3: Move meeting and mail policy into the Skills**

Remove `DingTalk mail handling` from `app/codex_runner.py`. Reduce `DWS_MATERIAL_READING_INSTRUCTIONS` to authentication classification and the requirement to use the supplied exact read command; operation syntax belongs to operation Skills. Remove meeting/minutes and mail policy paragraphs from both generic prompt copies.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_meeting_work_skill.py tests/test_mail_review_skill.py tests/test_codex_runner.py tests/test_meeting_alignment_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit meeting and mail migration**

```bash
git add skills/ceo-meeting-work skills/ceo-mail-review app/codex_runner.py data/prompts/developer_prompt.md app/defaults/developer_prompt.md tests/test_meeting_work_skill.py tests/test_mail_review_skill.py tests/test_codex_runner.py tests/test_meeting_alignment_agent.py
git commit -m "feat: move meeting and mail workflows into skills"
```

### Task 6: Move Personnel Communication Into A Skill And Reuse Existing Specialist Skills

**Files:**
- Modify: `skills/ceo-personnel-communication/SKILL.md`
- Modify: `data/prompts/developer_prompt.md`
- Modify: `app/defaults/developer_prompt.md`
- Modify: `app/consumer_agent.py`
- Modify: `app/audit_agent.py`
- Test: `tests/test_personnel_communication_skill.py`
- Test: `tests/test_agent_context.py`
- Test: `tests/test_agent_runtime_worker.py`

- [ ] **Step 1: Write failing domain-composition tests**

Cover:

1. Internal employee performance/compensation loads `ceo-personnel-communication`.
2. HR direct chat may receive personnel information within HR responsibility.
3. A non-HR direct chat about a third party cannot receive unsupported sensitive details.
4. Candidate evaluation loads both `ceo-personnel-communication` and `stardust-interview`.
5. OA about personnel loads both `ceo-personnel-communication` and `dingtalk-oa-approval`.
6. OKR review loads `dingtang-okr-review`; ordinary OKR discussion does not invoke the specialized scoring workflow.
7. Business ownership, delivery, revenue, or project risk is not classified as personnel merely because a person's name appears.

- [ ] **Step 2: Run tests and verify the personnel block remains global**

Run: `pytest tests/test_personnel_communication_skill.py tests/test_agent_context.py -q`

Expected: FAIL.

- [ ] **Step 3: Write the personnel audience workflow and remove global duplication**

The Skill must distinguish the subject, recipient, audience authorization, HR role, self-related information, internal personnel, external candidate, and ordinary business facts. It must require the Agent to load `stardust-interview` for candidate evidence and `dingtalk-oa-approval` for approval work rather than copying either workflow.

Remove the detailed personnel/candidate block from both generic prompts. Keep the invariant that credentials and unsupported facts cannot be exposed.

- [ ] **Step 4: Require existing specialist Skills instead of duplicating them**

Consumer instructions state that OA, candidate interview, and OKR scoring work must load their installed specialist Skill. Audit instructions require the same Skill reread before execution. Do not add OA, interview, or OKR business rules to `CONSUMER_AGENT_RULES`.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_personnel_communication_skill.py tests/test_agent_context.py tests/test_agent_runtime_worker.py tests/test_consumer_agent.py tests/test_audit_agent.py -q`

Expected: PASS.

- [ ] **Step 6: Commit personnel and specialist composition**

```bash
git add skills/ceo-personnel-communication data/prompts/developer_prompt.md app/defaults/developer_prompt.md app/consumer_agent.py app/audit_agent.py tests/test_personnel_communication_skill.py tests/test_agent_context.py tests/test_agent_runtime_worker.py
git commit -m "feat: compose personnel and specialist business skills"
```

### Task 7: Replace Task Extraction And Follow-Up Prompts With One Work-Tracking Skill

**Files:**
- Modify: `skills/ceo-work-tracking/SKILL.md`
- Modify: `app/task_agent.py`
- Modify: `app/follow_up.py`
- Modify: `app/todo_sync.py`
- Test: `tests/test_work_tracking_skill.py`
- Test: `tests/test_task_agent.py`
- Test: `tests/test_follow_up.py`
- Test: `tests/test_todo_sync.py`

- [ ] **Step 1: Write failing lifecycle tests**

The test matrix must cover the whole loop:

```python
WORK_TRACKING_CASES = (
    "routine_process_is_discarded",
    "important_commitment_creates_todo_with_owner_evidence",
    "follow_up_cannot_exist_without_todo",
    "participant_or_speaker_is_not_owner_evidence",
    "due_follow_up_refreshes_live_todo_before_send",
    "completed_todo_suppresses_follow_up",
    "owner_correction_updates_todo_and_suppresses_old_draft",
    "follow_up_reply_updates_existing_work_item_instead_of_creating_duplicate",
    "stale_follow_up_is_skipped",
    "sensitive_follow_up_uses_verified_direct_target",
)
```

- [ ] **Step 2: Run tests and verify policy is embedded in `task_agent.py`**

Run: `pytest tests/test_work_tracking_skill.py tests/test_task_agent.py tests/test_follow_up.py tests/test_todo_sync.py -q`

Expected: FAIL because work judgment is still encoded in the large `TASK_AGENT_PROMPT`.

- [ ] **Step 3: Write the complete `ceo-work-tracking` lifecycle**

The Skill owns:

1. Deciding whether an item deserves tracking.
2. Choosing one-time action, TODO, or project.
3. Requiring stable owner identity and owner evidence.
4. Defining deadline, deliverable, completion standard, priority, and source context.
5. Binding every follow-up to a TODO.
6. Selecting a work-hours schedule and appropriate group/direct target.
7. Reading current project/TODO/external status before follow-up.
8. Closing or suppressing when completion evidence exists.
9. Sending only a contextual progress question when still open.
10. Applying replies to the existing project/TODO/follow-up instead of creating a duplicate.

The service continues to own persistence, scheduled wake-up, DingTalk Todo synchronization, exact send deduplication, and retry state.

- [ ] **Step 4: Reduce `TASK_AGENT_PROMPT` to role plus output contract**

Load the Skill with the existing `structured_agent.load_skill_text` pattern. The prompt contains only current work item/candidates, Memory availability facts, and the `TaskAgentDecision` schema contract. Remove duplicated owner, follow-up, scheduling, candidate, and completion policy prose from Python.

- [ ] **Step 5: Keep mechanical send guards in service code**

Retain these deterministic checks in `follow_up.py` and `todo_sync.py`:

- draft due time and local work hours;
- bound TODO existence;
- live DingTalk Todo completion refresh;
- local completion evidence;
- exact idempotency key;
- sent-result persistence and retry state.

Remove service code only when it performs business inference, such as guessing an owner or selecting a semantically different target. Replace those cases with a recoverable task for the work-tracking Agent.

- [ ] **Step 6: Run the lifecycle tests**

Run: `pytest tests/test_work_tracking_skill.py tests/test_task_agent.py tests/test_follow_up.py tests/test_todo_sync.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the unified lifecycle Skill**

```bash
git add skills/ceo-work-tracking app/task_agent.py app/follow_up.py app/todo_sync.py tests/test_work_tracking_skill.py tests/test_task_agent.py tests/test_follow_up.py tests/test_todo_sync.py
git commit -m "refactor: unify work extraction and follow-up in one skill"
```

### Task 8: Shrink The Always-Loaded Prompt And Delete Duplicated Domain Policy

**Files:**
- Modify: `app/agent_context.py`
- Modify: `app/consumer_agent.py`
- Modify: `app/audit_agent.py`
- Modify: `app/codex_runner.py`
- Modify: `data/prompts/developer_prompt.md`
- Modify: `app/defaults/developer_prompt.md`
- Test: `tests/test_agent_context.py`
- Test: `tests/test_consumer_agent.py`
- Test: `tests/test_audit_agent.py`
- Test: `tests/test_codex_runner.py`
- Test: `tests/test_prompt.py`

- [ ] **Step 1: Add a failing core-prompt boundary test**

```python
def test_consumer_core_prompt_contains_invariants_not_domain_workflows():
    text = CONSUMER_AGENT_RULES
    assert "read-only digital representative" in text
    assert "do not ask the user to provide confirmed facts again" in text
    assert "unknown" in text.lower()
    assert "calendar" not in text.lower()
    assert "OA work" not in text
    assert "internal_personnel" not in text
    assert len(text) < 5000
```

Add the equivalent test for Audit B and both generic prompt copies.

- [ ] **Step 2: Run prompt tests and verify they fail before deletion**

Run: `pytest tests/test_agent_context.py tests/test_consumer_agent.py tests/test_audit_agent.py tests/test_codex_runner.py tests/test_prompt.py -q`

Expected: FAIL because domain-specific OA, document, calendar, mail, personnel, and Memory rules remain globally injected.

- [ ] **Step 3: Reduce core instructions to the eight invariants in this plan**

Keep the current strict wire schema descriptions generated from Pydantic. Keep configurable Audit Rules visible to A and B. Replace all domain paragraphs with one dynamic Skill-loading instruction. Remove custom Memory operation guidance and rely on the installed `memory-connector:recall` and `memory-connector:remember` Skills; keep only the rule that unavailable Memory is reported as a dependency result and must not trigger login.

- [ ] **Step 4: Prove no duplicate domain rule remains**

Run:

```bash
rg -n "日历邀请|DingTalk mail handling|internal_personnel|候选人流程状态 follow-up|OA factual gap" app/agent_context.py app/consumer_agent.py app/audit_agent.py app/codex_runner.py data/prompts/developer_prompt.md app/defaults/developer_prompt.md
```

Expected: no matches except schema field names or a short specialist-Skill loading statement explicitly asserted by tests.

- [ ] **Step 5: Run prompt tests**

Run: `pytest tests/test_agent_context.py tests/test_consumer_agent.py tests/test_audit_agent.py tests/test_codex_runner.py tests/test_prompt.py -q`

Expected: PASS.

- [ ] **Step 6: Commit prompt simplification**

```bash
git add app/agent_context.py app/consumer_agent.py app/audit_agent.py app/codex_runner.py data/prompts/developer_prompt.md app/defaults/developer_prompt.md tests/test_agent_context.py tests/test_consumer_agent.py tests/test_audit_agent.py tests/test_codex_runner.py tests/test_prompt.py
git commit -m "refactor: keep only runtime invariants in core prompts"
```

### Task 9: Convert Production Failures Into Skill-Loading Evaluations

**Files:**
- Create: `evals/skill_runtime/cases.jsonl`
- Create: `evals/skill_runtime/run.py`
- Create: `tests/test_skill_runtime_evals.py`
- Modify: `docs/quality-inspection.md`

- [ ] **Step 1: Add sanitized evaluation cases**

Include generalized, non-sensitive versions of these previously observed classes:

1. Vague calendar invite requiring one factual question to the inviter.
2. Calendar invitation with enough title/context that must be accepted without unnecessary questioning.
3. Referenced document that must be read instead of asking the sender to paste it.
4. Image attachment that must be read before judgment.
5. Truncated mail card requiring full-thread lookup.
6. Personnel information sent to an unrelated recipient.
7. Repeated irrelevant follow-ups caused by treating participation as owner evidence.
8. Follow-up whose TODO is already complete.
9. OA factual gap that must be clarified with the applicant rather than presented as an A/B choice to Derek.
10. Silent meeting where each participant mention belongs next to one concrete action item.

Each JSONL row contains `case_id`, `trigger`, `context`, `expected_business_skills`, `forbidden_business_skills`, `expected_outcome`, and `required_assertions`. Do not store original private messages, user IDs, tokens, session IDs, or signed links.

- [ ] **Step 2: Write the failing eval harness test**

```python
def test_every_skill_runtime_eval_declares_skill_and_outcome():
    cases = load_cases(Path("evals/skill_runtime/cases.jsonl"))
    assert len(cases) >= 10
    for case in cases:
        assert case.expected_business_skills
        assert case.expected_outcome in {"proposal", "no_action", "needs_human", "failed"}
```

- [ ] **Step 3: Implement the deterministic fixture runner and optional live mode**

`run.py` validates case shape and can execute scripted Consumer/Audit fixtures by default. `--live` invokes the real local `codex exec` path and reports observed Skill read events, result contract, and Audit outcome without permitting external writes. Live mode must use dry-run Audit execution and must never be required by the unit test suite.

- [ ] **Step 4: Run eval tests**

Run: `pytest tests/test_skill_runtime_evals.py -q`

Expected: PASS.

Run: `.venv/bin/python evals/skill_runtime/run.py`

Expected: all cases valid and all scripted expectations pass.

- [ ] **Step 5: Commit the regression corpus**

```bash
git add evals/skill_runtime tests/test_skill_runtime_evals.py docs/quality-inspection.md
git commit -m "test: add skill-first agent runtime evaluations"
```

### Task 10: Document, Verify, Deploy, And Read Back Production

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/product-logic.md`
- Modify: `docs/message-routing-rules.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update architecture documentation**

Document this exact runtime flow:

```text
trigger/context/material references
  -> Consumer A discovers and reads business Skill(s)
  -> A reads operation Skill(s) and proposes an exact action
  -> service derives verified Skill receipts from existing tool events
  -> Audit B rereads the same business Skill(s) and operation Skill(s)
  -> B reviews, executes, and reads back
  -> service persists the existing run/attempt/receipt state
```

State explicitly that there is no keyword router, no service-side business material interpretation, and no parallel Skill audit database.

- [ ] **Step 2: Run focused suites**

Run:

```bash
pytest \
  tests/test_business_skills.py \
  tests/test_agent_skill_usage.py \
  tests/test_agent_context.py \
  tests/test_consumer_agent.py \
  tests/test_audit_agent.py \
  tests/test_agent_orchestrator.py \
  tests/test_agent_runtime_worker.py \
  tests/test_task_agent.py \
  tests/test_follow_up.py \
  tests/test_todo_sync.py \
  tests/test_skill_runtime_evals.py -q
```

Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`

Expected: PASS with no new deselections or xfails.

- [ ] **Step 4: Install Skills through the setup path in an isolated home**

Run:

```bash
HOME="$(mktemp -d)" scripts/bootstrap-local-components.sh --component ceo-business-skills --format json
```

Expected: seven Skills installed under the isolated `~/.agents/skills`; no files written under `~/.codex/skills`.

- [ ] **Step 5: Run one read-only live A/B probe**

Use a fixture calendar trigger with no external write permission. Verify persisted events show:

1. A called `agent_cli.read_skill` for `ceo-calendar-invite` and `dingtalk-calendar`.
2. B received A's verified Skill receipt and reread both Skills.
3. The final result is a valid dry-run outcome with `side_effect_state=none`.
4. No calendar response or chat message was externally sent.

- [ ] **Step 6: Commit documentation**

```bash
git add docs/architecture.md docs/product-logic.md docs/message-routing-rules.md README.md CHANGELOG.md
git commit -m "docs: explain skill-first agent workflows"
```

- [ ] **Step 7: Integrate the current main branch and push the reviewed feature branch**

Run:

```bash
git fetch origin
git merge --no-edit origin/main
pytest -q
git push -u origin HEAD
```

Expected: push succeeds without rewriting branch history. Run `requesting-code-review`, resolve all findings with focused tests, then use `finishing-a-development-branch` to merge the reviewed branch into `main` and push `main`. The exact commit deployed below must equal the resulting pushed `origin/main` commit.

- [ ] **Step 8: Restart the launchd service**

Run: `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`

Expected: a new supervisor PID starts from the production runtime checkout.

- [ ] **Step 9: Verify service, UI, Skill installation, and backlog**

Run:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -fsS http://127.0.0.1:8765/ >/dev/null
.venv/bin/python -m app.cli quality-check --verify-channels
```

Expected:

- audit web returns HTTP 200;
- production checkout commit equals the pushed main commit;
- all seven managed Skills exist under the service user's `~/.agents/skills`;
- DingTalk and Lark gates are ready;
- no `reply_tasks` are failed or processing;
- no `work_summary_inputs` are failed or processing;
- no WeChat delivery is sending, send-unknown, or ready-to-send;
- no meeting job is failed, processing, retry, or ready-to-send;
- no new error was created after restart.

- [ ] **Step 10: Run one production-safe behavior readback per migrated domain**

Use existing completed or synthetic dry-run contexts; do not resend historical messages or replay approvals. Confirm the latest Consumer run loads the expected Skill and Audit B rereads it for calendar, document, meeting, mail, personnel, and work tracking. For OA, interview, and OKR, confirm the existing specialist Skill is loaded rather than a copied CEO Skill.

## Self-Review Results

- **Spec coverage:** The plan covers all seven agreed business Skills, combines task extraction and follow-up into one lifecycle, preserves existing OA/interview/OKR Skills, keeps A/B safety boundaries always loaded, adds dynamic loading proof, removes duplicated prompt prose, packages installation for other users, adds regression evals, and includes deployment/readback.
- **Placeholder scan:** No unresolved placeholder, deferred implementation, or unspecified test step remains. Every task names exact files, commands, expected outcomes, and commit boundaries.
- **Type consistency:** `LoadedSkillReceipt` is defined once in `app/agent_skill_usage.py`, carried as `AuditTurnContext.consumer_skills`, and derived only from existing completed `agent_cli.read_skill` events. The proposal schema remains unchanged; no Agent-authored Skill provenance is trusted.
