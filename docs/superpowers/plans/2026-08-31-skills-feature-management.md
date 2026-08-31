# Settings Skills Feature Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Settings Skills page that manages feature-level switches, lists all project-owned skills, and supports safe preview/edit of `SKILL.md` files while only preventing new tasks for disabled features.

**Architecture:** Keep project `skills/` as the editable source and retain the existing service-managed runtime copy. Add a declarative feature registry mapping features to one or more skills, a persisted feature-state store, and a narrow filesystem service for validated reads/writes and runtime synchronization. Gate each new-task entrypoint by feature ID; do not filter shared skills globally and do not mutate existing tasks.

**Tech Stack:** Python/FastAPI-style local web API, existing `AutoReplyStore`/worker runtime, React + TypeScript Settings console, pytest, Vitest/Testing Library.

---

## File map

- Create `data/config/skill-features.json`: committed declarative feature-to-skill registry; add an explicit `.gitignore` exception.
- Create `app/skill_features.py`: registry parsing, feature-state persistence, feature gating, and response DTO construction. Keep it independent of HTTP and React.
- Create `app/skill_files.py`: project skill discovery, frontmatter/path validation, SHA calculation, atomic source writes, and service-managed runtime synchronization.
- Modify `app/business_skills.py`: expose one transaction-safe synchronization entrypoint that reuses the existing service-managed install rules without changing the current seven-skill validation contract.
- Modify `app/web_api/registration.py`: register the four Skills console endpoints and map domain errors to stable HTTP responses.
- Modify `frontend/src/api/console.ts`: add typed request helpers and parsers for feature/skill list, toggle, detail, and edit responses.
- Modify `frontend/src/pages/SettingsPage.tsx`: add the `skills` section, feature cards, associated-skill expansion, and preview/edit state.
- Modify `frontend/src/pages/SettingsPage.test.tsx`: cover navigation, feature cards, toggles, association view, editor, and error states.
- Modify `frontend/src/styles.css`: add only the Skills-specific card, badge, editor, and dependency styles following existing Settings tokens.
- Modify `app/worker.py`, `app/weekly_okr_report.py`, `app/email_classifier_scan.py`, `app/feedback_processing.py`, and `app/okr_review.py` only where the registry maps a feature to an existing new-task/job entrypoint: add feature checks directly before creation or claim.
- Create `tests/test_skill_features.py`, `tests/test_skill_files.py`, and `tests/test_console_skills_api.py` for domain, filesystem, and API coverage.
- Modify the owning routing tests (including `tests/test_worker.py`, `tests/test_weekly_okr_report.py`, or the existing focused files discovered in Task 3) for disabled-feature regressions.
- Modify `docs/architecture.md` and `docs/runtime-mechanism.md` with the feature-gate and “new tasks only” contract after tests pass.

### Task 1: Add the declarative feature registry and persisted state

**Files:**
- Create: `data/config/skill-features.json`
- Modify: `.gitignore`
- Create: `app/skill_features.py`
- Test: `tests/test_skill_features.py`

- [ ] **Step 1: Write failing registry/state tests**

Add tests that load a temporary registry and state path and assert:

```python
def test_registry_supports_many_skills_per_feature_and_shared_skills(tmp_path):
    registry = write_registry(tmp_path, {
        "features": [
            {"feature_id": "meeting_summary", "name": "Meeting Summary",
             "description": "会议摘要", "skills": ["ceo-meeting-work", "ceo-document-review"],
             "default_enabled": True},
            {"feature_id": "document_review", "name": "Document Review",
             "description": "文档审阅", "skills": ["ceo-document-review"],
             "default_enabled": True},
        ]
    })
    catalog = FeatureRegistry(registry_path=registry, state_path=tmp_path / "state.json")
    assert catalog.skills_for("meeting_summary") == ("ceo-meeting-work", "ceo-document-review")
    assert catalog.features_for_skill("ceo-document-review") == ("document_review", "meeting_summary")

def test_missing_state_uses_default_and_toggle_survives_reload(tmp_path):
    registry = write_registry(tmp_path, one_default_enabled=True)
    state = tmp_path / "state.json"
    catalog = FeatureRegistry(registry_path=registry, state_path=state)
    assert catalog.is_enabled("meeting_summary") is True
    catalog.set_enabled("meeting_summary", False)
    assert FeatureRegistry(registry_path=registry, state_path=state).is_enabled("meeting_summary") is False
```

Also cover duplicate feature IDs, empty skill lists, unknown feature IDs, invalid boolean values, and an atomic state write that leaves the previous file intact when replacement fails.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest tests/test_skill_features.py -q`

Expected: FAIL because `FeatureRegistry` and the registry fixture do not exist.

- [ ] **Step 3: Implement the registry and state API**

Implement `FeatureRegistry` with these concrete methods:

```python
class FeatureRegistry:
    def list_features(self) -> tuple[FeatureDefinition, ...]: ...
    def list_project_skill_names(self) -> tuple[str, ...]: ...
    def skills_for(self, feature_id: str) -> tuple[str, ...]: ...
    def features_for_skill(self, skill_name: str) -> tuple[str, ...]: ...
    def is_enabled(self, feature_id: str) -> bool: ...
    def set_enabled(self, feature_id: str, enabled: bool) -> FeatureState: ...
    def feature_status(self, feature_id: str, available_skills: set[str]) -> str: ...
```

Use immutable dataclasses for definitions/state, validate all IDs as non-empty path-safe names, load defaults when the state file is absent, and atomically replace the state file with UTF-8 JSON. Expose `feature_enabled(feature_id)` as the single runtime check so producers do not parse files themselves.

Populate `data/config/skill-features.json` with the current service-owned feature definitions and default all existing behavior to enabled. Add `!data/config/skill-features.json` to `.gitignore`; keep the runtime state file ignored.

- [ ] **Step 4: Run the tests and commit the domain layer**

Run: `pytest tests/test_skill_features.py -q`

Expected: PASS. Commit only the registry, manifest, ignore exception, and tests:

```bash
git add data/config/skill-features.json app/skill_features.py tests/test_skill_features.py .gitignore
git commit -m "feat: add feature-level skill registry"
```

### Task 2: Implement safe project skill discovery, editing, and runtime synchronization

**Files:**
- Create: `app/skill_files.py`
- Modify: `app/business_skills.py`
- Test: `tests/test_skill_files.py`
- Test: `tests/test_business_skills.py`

- [ ] **Step 1: Write failing filesystem tests**

Cover discovery of every `skills/*/SKILL.md`, frontmatter name/directory mismatch, missing `managed_by`, traversal and symlink rejection, SHA calculation, expected-SHA conflicts, atomic writes, and synchronization failure recovery. Include a test proving a source edit is copied to a temporary runtime root using the existing service-managed installer rules.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest tests/test_skill_files.py tests/test_business_skills.py -q`

Expected: FAIL because the new filesystem service and synchronization entrypoint do not exist.

- [ ] **Step 3: Implement the filesystem service**

Implement:

```python
class SkillFileService:
    def list_skills(self) -> tuple[ProjectSkill, ...]: ...
    def get_skill(self, name: str) -> SkillDocument: ...
    def save_skill(self, name: str, content: str, expected_sha256: str) -> SkillDocument: ...
```

Resolve names only from discovered project directories, require the target to remain under the repository `skills/` root, validate frontmatter before writing, calculate SHA-256 from the exact UTF-8 bytes, and use a sibling temporary file followed by `os.replace`. On save, call a new `sync_bundled_skill(name)` transaction in `app.business_skills`; report the source/sync stage in typed exceptions.

Do not alter `installed_business_skill_catalog()` ordering or the existing seven bundled business-skill tests. The synchronization helper must reject non-service-managed targets and preserve recovery data if a swap fails.

- [ ] **Step 4: Run the tests and commit the filesystem layer**

Run: `pytest tests/test_skill_files.py tests/test_business_skills.py -q`

Expected: PASS with all pre-existing business-skill tests unchanged. Commit:

```bash
git add app/skill_files.py app/business_skills.py tests/test_skill_files.py tests/test_business_skills.py
git commit -m "feat: safely edit and sync project skills"
```

### Task 3: Gate new-task entrypoints by feature ID

**Files:**
- Modify: the existing producer/job modules that create each feature’s new task (at minimum `app/worker.py`; include `app/weekly_okr_report.py`, feedback, email, or other entrypoint modules only where the registry maps them)
- Test: the owning focused routing tests

- [ ] **Step 1: Inventory and test current entrypoints**

Map every `feature_id` in `data/config/skill-features.json` to the existing method that creates a reply task or claims a feature job. Add failing tests at that boundary asserting:

```python
monkeypatch.setattr(feature_registry, "is_enabled", lambda feature_id: False)
assert producer.process_new_input(input_row) == 0
assert store.count_new_tasks() == before_count
```

For each disabled feature, assert no new task/job claim is created. Add a separate test that an already-running and already-queued record remains byte-for-byte/state-for-state unchanged when the feature is toggled off.

- [ ] **Step 2: Run the focused routing tests and verify they fail**

Run: `pytest tests/test_worker.py tests/test_weekly_okr_report.py -k feature_disabled -q`

Expected: FAIL because the producer/job boundaries do not consult `feature_enabled`.

- [ ] **Step 3: Add the minimal feature checks**

At each mapped entrypoint, place the check immediately before the existing enqueue/claim call:

```python
if not feature_enabled("meeting_summary"):
    logger.info("feature disabled; skipping new input feature_id=%s", "meeting_summary")
    return 0
```

Use the feature ID from the registry mapping rather than checking a skill name. Do not cancel, pause, rewrite, or reclassify existing queue rows. Do not remove shared skills from the consumer catalog; a skill may remain available to another enabled feature.

- [ ] **Step 4: Run routing regressions and commit**

Run the focused producer/job test files plus the existing task lifecycle tests. Expected: all pass, including unchanged enabled behavior and unchanged queued/running records. Commit the entrypoint changes and tests:

```bash
git add app/worker.py app/weekly_okr_report.py tests
git commit -m "feat: gate disabled features before new task creation"
```

### Task 4: Add the Skills Console API

**Files:**
- Modify: `app/web_api/registration.py`
- Create: `tests/test_console_skills_api.py`

- [ ] **Step 1: Write failing API tests**

Test these exact contracts:

```python
GET  /api/console/settings/skills
POST /api/console/settings/skills/{feature_id}/toggle  {"enabled": false}
GET  /api/console/settings/skills/{skill_name}
PUT  /api/console/settings/skills/{skill_name}  {"content": "...", "expected_sha256": "..."}
```

Assert list responses include all project skills, feature associations, status, and enabled state; toggle responses persist; detail responses include content/SHA/referencing features; edits reject stale SHA, invalid frontmatter, unknown names, and path-like names with stable 4xx bodies.

- [ ] **Step 2: Run API tests and verify failure**

Run: `pytest tests/test_console_skills_api.py -q`

Expected: FAIL with 404 or missing handler errors.

- [ ] **Step 3: Register handlers and error mapping**

Add narrow handlers that instantiate the registry and `SkillFileService`, return JSON-safe DTOs, map unknown IDs to 404, validation to 422, SHA conflicts to 409, and persistence/sync failures to 500 with a non-secret stage/detail. Keep generic `/api/console/settings/{section}` behavior unchanged for all existing sections.

- [ ] **Step 4: Run API regressions and commit**

Run: `pytest tests/test_console_skills_api.py tests/test_console_web_api.py -q`

Expected: PASS. Commit:

```bash
git add app/web_api/registration.py tests/test_console_skills_api.py
git commit -m "feat: expose skills management console API"
```

### Task 5: Build the React Skills page

**Files:**
- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add failing frontend tests**

Mock the four API helpers and add tests asserting:

- `/settings?tab=skills` highlights `Skills` in the existing navigation;
- feature cards show names, descriptions, associated skill chips, and ON/OFF state;
- toggling a feature calls the toggle API and shows success/error feedback;
- “查看关联 skills” reveals each associated skill and its preview/edit entry;
- “全部 skills” includes every project skill, including unassociated ones;
- preview renders returned `SKILL.md` text and SHA;
- edit/save sends `content` plus `expected_sha256`;
- 409 conflict and validation/sync errors preserve the draft and show a useful alert.

- [ ] **Step 2: Run the frontend tests and verify failure**

Run: `cd frontend && npm test -- --run src/pages/SettingsPage.test.tsx`

Expected: FAIL because the `skills` section, API helpers, and components do not exist.

- [ ] **Step 3: Implement typed API helpers and page state**

Add explicit TypeScript types for feature/skill list/detail responses and functions `getSkillFeatures`, `toggleSkillFeature`, `getSkillDetail`, and `saveSkill`. Extend `SettingsSection` and `sections` with `skills`. Keep list/detail loading independent so one broken skill does not blank the entire page.

Implement the A layout: feature cards are the primary list, associated skills expand inline, and a complete project-skill catalog provides preview/edit navigation. Use a controlled textarea/editor with preview and edit tabs, current SHA, save/cancel, and draft retention after errors. The copy must state that the switch affects new tasks only and that editing a shared skill affects every referencing feature.

- [ ] **Step 4: Add styles and run frontend tests**

Add focused classes for feature cards, dependency chips, switch status, skill detail, editor, and conflict alerts using existing CSS variables and responsive Settings layout rules. Run the focused test command from Step 2; expected PASS.

- [ ] **Step 5: Commit the frontend**

```bash
git add frontend/src/api/console.ts frontend/src/pages/SettingsPage.tsx frontend/src/pages/SettingsPage.test.tsx frontend/src/styles.css
git commit -m "feat: add settings skills management page"
```

### Task 6: Document the runtime contract and complete verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Test: the full focused backend/frontend suites and live console checks

- [ ] **Step 1: Document the final contract**

Document feature-level switches, many-to-many feature/skill associations, project-source/runtime-copy synchronization, and the rule that disabled features reject only new task creation. Explicitly state that shared skills remain available to other enabled features and that external operation skills are outside this editor.

- [ ] **Step 2: Run backend and frontend verification**

Run:

```bash
pytest tests/test_skill_features.py tests/test_skill_files.py tests/test_business_skills.py tests/test_console_skills_api.py tests/test_console_web_api.py -q
cd frontend && npm test -- --run
cd .. && git diff --check
```

Expected: all focused backend tests and the full frontend suite pass; `git diff --check` returns no output. Preserve unrelated pre-existing worktree changes and stage only task-owned files.

- [ ] **Step 3: Restart and verify the live service**

After runtime code changes, restart and verify the launchd service:

```sh
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Read back `GET /api/console/settings/skills`, toggle one feature off and back on, verify the persisted state after the new process starts, and exercise one enabled and one disabled new-input route. Confirm no unresolved `failed` or `processing` backlog was introduced.

- [ ] **Step 4: Commit documentation and verification notes**

```bash
git add docs/architecture.md docs/runtime-mechanism.md
git commit -m "docs: document skill feature switch contract"
```

## Self-review checklist

- Every spec requirement is covered: feature-first UI, all project skills, many-to-many associations, direct `SKILL.md` editing, runtime synchronization, independent feature state, new-task-only gating, shared-skill behavior, error handling, and full verification.
- No task changes user/system/plugin skill directories or introduces a sub-skill hierarchy.
- No step uses `discard` or `discarded`; existing lifecycle states remain unchanged.
- Feature IDs are data-driven through the registry; React does not contain a static feature enumeration.
- Existing uncommitted work is not staged by any command in this plan.
