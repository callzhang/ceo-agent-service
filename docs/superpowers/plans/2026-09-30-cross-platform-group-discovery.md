# Cross-Platform Group Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract the staged group-finding flow into a provider-neutral service, wrap the existing DingTalk/DWS reads behind it, and migrate meeting discovery without changing final target selection or delivery.

**Architecture:** `GroupDiscoveryService` receives a normalized request and business policy. A provider adapter translates DingTalk/DWS objects into neutral group, member, message, and sendability values. The meeting caller continues to own fallback, target validation, and sending; Lark and Slack remain adapter slots until their runtime integrations are verified.

**Tech Stack:** Python 3, Pydantic/dataclasses already used by the service, Protocol interfaces, pytest, Ruff.

---

### Task 1: Define the neutral provider and result contracts

**Files:**
- Create: `app/group_discovery.py`
- Test: `tests/test_group_discovery.py`
- Modify: `docs/architecture.md` (group-discovery behavior paragraph)
- Modify: `docs/runtime-mechanism.md` (provider scope and failure outcome paragraph)

- [ ] **Step 1: Write failing contract tests**

Add tests that construct a fake provider and assert that the neutral service can represent DingTalk, Lark, and Slack scopes without provider-specific ID fields; candidate deduplication uses `(platform, workspace_key, external_group_id)`; and provider capability `unavailable` is distinguishable from a false result.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest -q tests/test_group_discovery.py`
Expected: FAIL because `app.group_discovery` does not exist.

- [ ] **Step 3: Implement minimal neutral contracts**

Create immutable dataclasses for `ProviderScope`, `GroupRef`, `MemberRef`, `GroupMessage`, `ProviderCapabilities`, `GroupDiscoveryRequest`, `GroupEvidence`, `GroupCandidate`, `GroupDiscoveryResult`, and `GroupDiscoveryOutcome`. Use opaque string IDs and include provider scope in every external reference.

- [ ] **Step 4: Run the contract tests**

Run: `pytest -q tests/test_group_discovery.py`
Expected: PASS.

- [ ] **Step 5: Document the live boundary**

Record that the generic service accepts DingTalk, Lark, and Slack scopes, while only the DingTalk adapter is live in this phase; missing provider capabilities return `unavailable` and never become an empty search.

- [ ] **Step 6: Commit**

```bash
git add app/group_discovery.py tests/test_group_discovery.py docs/architecture.md docs/runtime-mechanism.md
git commit -m "feat: add neutral group discovery contracts"
```

### Task 2: Implement the staged discovery service

**Files:**
- Modify: `app/group_discovery.py`
- Modify: `tests/test_group_discovery.py`

- [ ] **Step 1: Add failing staged-flow tests**

Cover complete audience rosters filtering members before message reads, title filtering before message reads, one survivor receiving one recent-message read, multiple survivors receiving comparable reads, scoped deduplication, incomplete rosters returning `unavailable`, and retryable provider failures remaining `RETRYABLE_FAILURE`.

- [ ] **Step 2: Run the focused tests and verify the new cases fail**

Run: `pytest -q tests/test_group_discovery.py`
Expected: the new behavior cases FAIL while the contract cases remain PASS.

- [ ] **Step 3: Implement the service**

Add `GroupDiscoveryProvider` and `GroupDiscoveryPolicy` protocols and implement `GroupDiscoveryService.discover()`: generate queries, deduplicate scoped groups, apply roster coverage when complete, score title, check sendability, read recent messages only for survivors, collect bounded evidence, and classify `VERIFIED`, `AMBIGUOUS`, `NO_VERIFIED_GROUP`, or `RETRYABLE_FAILURE`.

- [ ] **Step 4: Run focused tests and Ruff**

Run: `pytest -q tests/test_group_discovery.py && ruff check app/group_discovery.py tests/test_group_discovery.py`
Expected: PASS with no Ruff errors.

- [ ] **Step 5: Commit**

```bash
git add app/group_discovery.py tests/test_group_discovery.py
git commit -m "feat: implement staged group discovery"
```

### Task 3: Add the DingTalk provider adapter

**Files:**
- Modify: `app/group_discovery.py`
- Test: `tests/test_group_discovery.py`

- [ ] **Step 1: Write adapter tests**

Assert that DWS conversations become `GroupRef`, DWS member IDs become scoped `MemberRef`, DWS messages become bounded `GroupMessage`, and DWS errors are preserved as retryable provider errors.

- [ ] **Step 2: Run adapter tests and verify they fail**

Run: `pytest -q tests/test_group_discovery.py -k dingtalk`
Expected: FAIL because the adapter is not implemented.

- [ ] **Step 3: Implement `DingTalkGroupDiscoveryProvider`**

Wrap existing `search_conversations`, `list_group_member_open_dingtalk_ids`, and `read_recent_messages` methods. Keep DWS field names inside the adapter. Use the provider scope `platform="dingtalk"` and the configured account/workspace key.

- [ ] **Step 4: Run adapter tests and the DWS tests**

Run: `pytest -q tests/test_group_discovery.py tests/test_dws_client.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/group_discovery.py tests/test_group_discovery.py
git commit -m "feat: adapt dingtalk reads to group discovery"
```

### Task 4: Migrate meeting discovery to the reusable service

**Files:**
- Modify: `app/meeting_alignment.py`
- Modify: `tests/test_meeting_alignment.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

- [ ] **Step 1: Add a meeting-policy integration test**

Use the existing meeting fixtures to assert the meeting caller receives the same candidate order and evidence, including the regression where the correct group appears after the first raw search page; assert direct fallback remains outside the discovery service.

- [ ] **Step 2: Run the meeting tests and verify the integration case fails**

Run: `pytest -q tests/test_meeting_alignment.py -k group_candidates`
Expected: the new service-integration case FAIL before the caller is migrated.

- [ ] **Step 3: Adapt the meeting caller**

Create the DingTalk adapter and meeting policy at the existing `_search_meeting_group_candidates` boundary, translate the neutral candidates back into the existing meeting candidate dictionaries, and retain all existing target validation, Agent selection, fallback, and delivery behavior.

- [ ] **Step 4: Run focused meeting tests**

Run: `pytest -q tests/test_meeting_alignment.py tests/test_meeting_alignment_models.py tests/test_meeting_alignment_delivery.py`
Expected: PASS.

- [ ] **Step 5: Update runtime documentation**

Document that meeting discovery now uses the shared staged service and that platform adapters do not select a final target or send messages.

- [ ] **Step 6: Commit**

```bash
git add app/meeting_alignment.py tests/test_meeting_alignment.py docs/architecture.md docs/runtime-mechanism.md
git commit -m "refactor: use shared group discovery for meetings"
```

### Task 5: Verify, deploy, and read back

**Files:**
- No new files.

- [ ] **Step 1: Run the complete focused verification set**

Run: `pytest -q tests/test_group_discovery.py tests/test_meeting_alignment.py tests/test_meeting_alignment_models.py tests/test_meeting_alignment_delivery.py tests/test_dws_client.py && ruff check app/group_discovery.py app/meeting_alignment.py tests/test_group_discovery.py tests/test_meeting_alignment.py`
Expected: PASS.

- [ ] **Step 2: Inspect the diff and status**

Run: `git diff --check && git status --short`
Expected: no whitespace errors and only this feature's files changed.

- [ ] **Step 3: Push and deploy**

Run: `git push origin main` followed by `python -m app.deploy`.
Expected: fast-forward, import checks, restart, and health polling succeed.

- [ ] **Step 4: Read back runtime state**

Check the new PID, `/healthz`, queue counts, Attention, and History. Report implementation, tests, deployment, health, and external readback as separate gates.
