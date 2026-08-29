# Email CEO Agent Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing read-only email-classifier prototype into a multi-account Email subsystem that classifies mail in under 100 ms p95 on a resident CPU process, learns only from human-confirmed labels, executes recoverable deterministic IMAP actions directly, and routes only automatic replies and multi-step unsubscribe work through the existing execution Agent → Audit Agent → revision lifecycle.

**Architecture:** Keep one launchd service and add one independently supervised `email-worker` child. The child has separate scan/direct-action and Agent-task loops, shares the existing SQLite database and Agent orchestration contracts, and never reuses `DingTalkAutoReplyWorker`. All accounts share one category configuration and one versioned model registry. Provider writes are driven by immutable ActionPlans and stable identities; deterministic actions use provider read-before-write/readback, while `auto_reply` and `unsubscribe` create idempotent `channel=email` tasks.

**Tech Stack:** Python 3.12, SQLite/WAL, stdlib IMAP/SMTP, scikit-learn TF-IDF + balanced Logistic Regression, jieba, FastAPI/Pydantic, React 19/TypeScript/Vitest, Playwright for unsubscribe browser automation, pytest, launchd.

---

## Preconditions and invariants

- The approved design is `docs/superpowers/specs/2026-08-29-email-ceo-agent-integration-design.md` at commit `e9ee8f14`.
- The current prototype and its tests are user-owned uncommitted work. Before every task, inspect `git status --short`; stage and commit only the files listed in that task.
- The production launchd checkout may differ from this development checkout. Before a live claim, resolve the running `ProgramArguments`, database path, environment file, and code revision from `launchctl print`; do not infer them from this worktree.
- Existing 73-message experiments establish resident-process CPU latency, not safe automatic-action precision. Start production engineering with every category's `auto_action_eligible=false`. Do not enable real writes merely because confidence exceeds a configured threshold.
- Secrets remain in the configured `.env` file. SQLite stores secret references only, and API responses expose only `secret_configured: true|false`.
- Do not add a second launchd job, account-specific models, attachment downloads, permanent deletion/EXPUNGE, connector hot reload, or a generic “handle/follow up” Agent action.
- The existing task lifecycle in `docs/architecture.md` and `docs/runtime-mechanism.md` remains authoritative: execution Agent → Audit Agent → feedback/revision, with `skipped`, `failed`, `needs_feedback`, and `needs_human`; never add `discard` or overwrite an original run.

## Phase 0 — Freeze the evidence and safe rollout gate

### Task 1: Convert experiment conclusions into executable production gates

**Files:**

- Modify: `app/email_classifier_training.py`
- Modify: `tests/test_email_classifier_training.py`
- Modify: `docs/superpowers/specs/2026-08-29-email-classifier-design.md`
- Modify: `/Users/derek/Documents/Projects/tools/email_triage/EXPERIMENT_REPORT.md`

- [ ] Add a failing test proving a newly trained model can be globally promoted while all categories remain ineligible for automatic actions when validation precision/sample requirements are not met.

```python
def test_model_promotion_does_not_imply_category_action_eligibility():
    metrics = validation_metrics(
        macro_f1=0.61,
        latency_p95_ms=12.9,
        per_category={"important": CategoryMetrics(precision=0.84, sample_count=19)},
    )

    decision = assess_candidate(
        metrics,
        active_macro_f1=0.60,
        category_requirements={
            EmailCategory.IMPORTANT: EligibilityRequirement(
                minimum_precision=0.95,
                minimum_samples=30,
            )
        },
    )

    assert decision.promote_model is True
    assert decision.categories[EmailCategory.IMPORTANT].auto_action_eligible is False
    assert decision.categories[EmailCategory.IMPORTANT].reason == "precision_and_sample_gate_not_met"
```

- [ ] Run the focused test and confirm it fails because category eligibility is not yet represented separately from promotion.

Run: `.venv/bin/python -m pytest tests/test_email_classifier_training.py -q`

Expected: FAIL in `test_model_promotion_does_not_imply_category_action_eligibility`.

- [ ] Introduce explicit immutable result types and one assessment function; no action executor may infer eligibility from `confidence` alone.

```python
@dataclass(frozen=True)
class CategoryEligibility:
    category: EmailCategory
    configured_threshold: float
    validated_precision: float | None
    validation_sample_count: int
    auto_action_eligible: bool
    reason: str


@dataclass(frozen=True)
class CandidateAssessment:
    promote_model: bool
    promotion_reason: str
    categories: dict[EmailCategory, CategoryEligibility]
```

- [ ] Record the exact experiment facts in both design/evidence documents: latest strict single-thread p95 `12.9109 ms`, 73 provisional rows, 71 independent experiment tests, and the fact that no tested threshold reached the automatic-action precision bars.
- [ ] Run the focused tests again.

Run: `.venv/bin/python -m pytest tests/test_email_classifier_training.py -q`

Expected: PASS.

- [ ] Commit only the gate/test/document changes.

```bash
git add app/email_classifier_training.py tests/test_email_classifier_training.py docs/superpowers/specs/2026-08-29-email-classifier-design.md
git commit -m "test: enforce email auto-action eligibility gate"
```

The experiment report lives in the sibling experiment repository and must be committed there separately if that repository is versioned; do not stage it into `ceo-agent-service`.

## Phase 1 — Stable contracts and durable state

### Task 2: Replace the prototype's all-actions-through-Audit contract

**Files:**

- Modify: `app/email_classifier_contracts.py`
- Modify: `tests/test_email_classifier_contracts.py`

- [ ] Replace the existing test that expects every action to produce an Audit proposal with tests for the approved split.

```python
def test_action_plan_splits_direct_and_agent_actions():
    plan = EmailActionPlan.create(
        classification_id=7,
        account_id="ding-main",
        stable_message_identity="ding-main:<message@example.com>",
        category=EmailCategory.NEWSLETTER,
        classification_source=ClassificationSource.USER,
        confidence=0.58,
        model_id="email-tfidf-lr-20260829T214530Z-7f3a91c2",
        config_version="email-config-v3",
        actions=(EmailAction.MARK_READ, EmailAction.ARCHIVE, EmailAction.UNSUBSCRIBE),
        action_parameters={},
    )

    assert plan.direct_actions == (EmailAction.MARK_READ, EmailAction.ARCHIVE)
    assert plan.agent_actions == (EmailAction.UNSUBSCRIBE,)
    assert plan.action_plan_version == 1


def test_pending_feedback_has_no_action_plan():
    decision = EmailClassificationDecision.pending_feedback(
        predicted_category=EmailCategory.IMPORTANT,
        confidence=0.57,
        margin=0.09,
        model_id="email-tfidf-lr-20260829T214530Z-7f3a91c2",
    )
    assert decision.action_plan is None
```

- [ ] Run the focused tests and observe the old generic Audit-proposal behavior fail.

Run: `.venv/bin/python -m pytest tests/test_email_classifier_contracts.py -q`

Expected: FAIL before implementation.

- [ ] Define the exact stable contracts used by all later tasks.

```python
DIRECT_ACTIONS = frozenset({
    EmailAction.LABEL,
    EmailAction.MARK_READ,
    EmailAction.ARCHIVE,
    EmailAction.MOVE,
    EmailAction.TRASH,
})
AGENT_ACTIONS = frozenset({EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE})


@dataclass(frozen=True)
class EmailProviderLocator:
    account_id: str
    folder: str
    uidvalidity: int
    uid: int
    rfc_message_id: str = ""

    @property
    def stable_message_identity(self) -> str:
        if self.rfc_message_id:
            return f"{self.account_id}:{self.rfc_message_id}"
        return f"{self.account_id}:{self.folder}:{self.uidvalidity}:{self.uid}"


@dataclass(frozen=True)
class EmailAttachmentMetadata:
    filename: str
    mime_type: str
    size_bytes: int
    inline: bool
```

- [ ] Make `EmailActionPlan` immutable and require `action_plan_id`, integer `action_plan_version`, full `model_id`, `config_version`, and validated action parameters. Delete the old `build_email_audit_proposal` helper instead of leaving a compatibility path.
- [ ] Add validation tests for label names, move target, mutually exclusive archive/move/trash, required auto-reply instruction, duplicate actions, and rejection of permanent delete/EXPUNGE.
- [ ] Run the focused tests.

Run: `.venv/bin/python -m pytest tests/test_email_classifier_contracts.py -q`

Expected: PASS.

- [ ] Commit the contract replacement by itself.

```bash
git add app/email_classifier_contracts.py tests/test_email_classifier_contracts.py
git commit -m "refactor: define email action plan boundaries"
```

### Task 3: Add account-aware email persistence and migrations

**Files:**

- Modify: `app/email_store.py`
- Modify: `tests/test_email_store.py`

- [ ] Add migration tests that open both a fresh database and a copy created with the current prototype schema. Assert existing feedback/config rows survive, and new columns/tables are queryable.
- [ ] Extend `EmailStore` with these durable tables and uniqueness rules:

```sql
CREATE TABLE IF NOT EXISTS email_accounts (
    account_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    email_address TEXT NOT NULL,
    imap_host TEXT NOT NULL,
    imap_port INTEGER NOT NULL,
    imap_tls INTEGER NOT NULL,
    imap_username TEXT NOT NULL,
    imap_secret_reference TEXT NOT NULL,
    smtp_host TEXT NOT NULL,
    smtp_port INTEGER NOT NULL,
    smtp_tls INTEGER NOT NULL,
    smtp_username TEXT NOT NULL,
    smtp_secret_reference TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    scan_folders_json TEXT NOT NULL,
    scan_interval_seconds INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_scan_cursors (
    account_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    last_seen_uid INTEGER NOT NULL,
    last_success_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, folder)
);

CREATE TABLE IF NOT EXISTS email_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    stable_message_identity TEXT NOT NULL UNIQUE,
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    rfc_message_id TEXT NOT NULL,
    thread_identity TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipients_json TEXT NOT NULL,
    subject TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    preview TEXT NOT NULL,
    attachment_metadata_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_action_plans (
    action_plan_id TEXT PRIMARY KEY,
    action_plan_version INTEGER NOT NULL,
    classification_id INTEGER NOT NULL,
    account_id TEXT NOT NULL,
    category TEXT NOT NULL,
    classification_source TEXT NOT NULL,
    confidence REAL NOT NULL,
    model_id TEXT NOT NULL,
    config_version TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    action_parameters_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(classification_id, action_plan_version)
);

CREATE TABLE IF NOT EXISTS email_action_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    provider_operation TEXT NOT NULL,
    provider_target TEXT NOT NULL,
    provider_result_id TEXT NOT NULL,
    error TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    UNIQUE(action_id, attempt_number)
);
```

- [ ] Store one current row per planned **direct** action in `email_actions`, with unique `(action_plan_id, action_type)`, and current statuses limited to `pending`, `processing`, `done`, `failed`. Append every direct-action attempt to `email_action_attempts`. Agent actions remain in the immutable plan and are projected from their linked `reply_task`/Agent run instead of being forced into the direct-action status protocol.
- [ ] Extend `email_classifications` with `account_id`, `stable_message_identity`, `predicted_category`, `confirmed_category`, full `model_id`, and nullable `current_action_plan_id`. Preserve the existing `processed`/`pending_feedback` user-facing status meanings while allowing a later human correction to point at ActionPlan version `n+1` without deleting version `n`.
- [ ] Implement one transaction that persists message + classification + immutable ActionPlan + direct-action rows, then advances the cursor. For pending feedback, persist message + classification and advance without an ActionPlan.
- [ ] Prove idempotence: rescanning the same stable identity changes only the provider locator, and never creates duplicate feedback, ActionPlans, actions, or Agent tasks.
- [ ] Run focused persistence tests.

Run: `.venv/bin/python -m pytest tests/test_email_store.py -q`

Expected: PASS for fresh schema, migration, transaction rollback, identity dedupe, locator update, and action-attempt append tests.

- [ ] Commit only persistence changes.

```bash
git add app/email_store.py tests/test_email_store.py
git commit -m "feat: persist email accounts plans and actions"
```

### Task 4: Add multiple account configuration with secret references

**Files:**

- Create: `app/email_connector_config.py`
- Create: `tests/test_email_connector_config.py`
- Modify: `app/config.py`
- Modify: `app/web_api/email.py`
- Modify: `tests/test_console_web_api.py`

- [ ] Add failing tests for two configured accounts sharing global classification configuration, JSON payload validation, redacted API output, and `.env` secret-reference resolution.
- [ ] Define strict request/storage models.

```python
class EmailAccountPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    email_address: str = Field(min_length=3, max_length=254)
    imap_host: str = Field(min_length=1)
    imap_port: int = Field(ge=1, le=65535)
    imap_tls: bool = True
    imap_username: str = Field(min_length=1)
    imap_secret_reference: str = Field(pattern=r"^CEO_EMAIL_[A-Z0-9_]+_IMAP_SECRET$")
    smtp_host: str = Field(min_length=1)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_tls: bool = True
    smtp_username: str = Field(min_length=1)
    smtp_secret_reference: str = Field(pattern=r"^CEO_EMAIL_[A-Z0-9_]+_SMTP_SECRET$")
    enabled: bool = True
    scan_folders: tuple[str, ...] = ("INBOX",)
    scan_interval_seconds: int = Field(default=60, ge=15, le=3600)
```

- [ ] Do not add `pydantic[email]`; add a `field_validator("email_address")` that constructs `email.headerregistry.Address(addr_spec=value)` and rejects values without username/domain, keeping dependency scope unchanged.
- [ ] Implement `resolve_secret(reference, env)` that accepts only the strict reference pattern and returns the environment value without ever including it in dataclass repr, exceptions, API output, logs, or SQLite.
- [ ] Add dedicated APIs because generic `/api/console/settings/connectors` is read-only:

```text
GET  /api/console/email/accounts
POST /api/console/email/accounts
PUT  /api/console/email/accounts/{account_id}
POST /api/console/email/accounts/{account_id}/test
```

The test endpoint performs IMAP login/select/logout and SMTP connect/auth/quit only; it sends no mail and writes no mailbox state.
- [ ] Saving an account writes non-secret config to `email_accounts` and writes only provided secret values to the referenced `.env` keys through `app.config.write_env_values`. A blank secret preserves the current value. The response says `imap_secret_configured` and `smtp_secret_configured` and never returns values.
- [ ] Return `restart_required=true` after a save; do not hot-reload the connector.
- [ ] Run focused tests.

Run: `.venv/bin/python -m pytest tests/test_email_connector_config.py tests/test_console_web_api.py -q`

Expected: PASS, including assertions that known secret strings are absent from serialized responses and error details.

- [ ] Commit account configuration separately.

```bash
git add app/email_connector_config.py tests/test_email_connector_config.py app/config.py app/web_api/email.py tests/test_console_web_api.py
git commit -m "feat: configure multiple email accounts"
```

## Phase 2 — Read-only multi-account ingestion and model lifecycle

### Task 5: Make IMAP scanning UID-based, account-aware, and attachment-safe

**Files:**

- Modify: `app/email_imap_readonly.py`
- Modify: `app/email_classifier_scan.py`
- Modify: `tests/test_email_imap_readonly.py`
- Modify: `tests/test_email_classifier_scan_model.py`

- [ ] Add fake-IMAP tests for two accounts/folders, isolation when one account authentication fails, UIDVALIDITY reset, UID search after `last_seen_uid`, missing RFC Message-ID fallback, moved-message locator refresh, MIME alternatives, quoted replies, and attachments.
- [ ] Assert the adapter calls `select(folder, readonly=True)`, uses `UID SEARCH`/`UID FETCH`, and never sends `STORE`, `COPY`, `MOVE`, `EXPUNGE`, or SMTP commands in this phase.
- [ ] Normalize model/Agent text to sender, recipients, subject, and plain text body/thread. For attachments, persist only:

```python
EmailAttachmentMetadata(
    filename="报价单.pdf",
    mime_type="application/pdf",
    size_bytes=183421,
    inline=False,
)
```

Do not decode, save, OCR, summarize, or pass attachment payload bytes.
- [ ] On UIDVALIDITY change, rescan the folder from UID 1 and deduplicate by stable business identity. Record the reset and resulting cursor; do not guess that old numeric UIDs still identify the same mail.
- [ ] Advance each `(account_id, folder)` cursor only through the Task 3 transaction after message/classification persistence succeeds.
- [ ] Run focused tests.

Run: `.venv/bin/python -m pytest tests/test_email_imap_readonly.py tests/test_email_classifier_scan_model.py -q`

Expected: PASS, with fake protocol logs proving the path is read-only.

- [ ] Commit scanner changes.

```bash
git add app/email_imap_readonly.py app/email_classifier_scan.py tests/test_email_imap_readonly.py tests/test_email_classifier_scan_model.py
git commit -m "feat: scan multiple IMAP accounts by UID"
```

### Task 6: Add immutable model registry, metadata, and atomic promotion

**Files:**

- Create: `app/email_model_registry.py`
- Create: `tests/test_email_model_registry.py`
- Modify: `app/email_classifier_model.py`
- Modify: `app/email_classifier_training.py`
- Modify: `app/email_classifier_retrain.py`
- Modify: `app/email_classifier_learning.py`
- Modify: `app/email_classifier_runtime.py`
- Modify: `tests/test_email_classifier_model.py`
- Modify: `tests/test_email_classifier_training.py`
- Modify: `tests/test_email_classifier_learning.py`
- Modify: `tests/test_email_classifier_runtime.py`

- [ ] Add tests for full immutable IDs, artifact SHA-256, candidate metadata, reload parity, latest-feedback inclusion, class protocol validation, p50/p95 latency, rejected candidates, and atomic active/previous switching.
- [ ] Generate IDs from family + UTC training time + the first eight hex characters of the artifact SHA-256.

```python
def build_model_id(*, trained_at: datetime, artifact_sha256: str) -> str:
    timestamp = trained_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"email-tfidf-lr-{timestamp}-{artifact_sha256[:8]}"
```

- [ ] Persist one metadata row for every `candidate`, `active`, `previous`, `rejected`, or `failed` model. Include parent ID, family/tokenizer/feature/dataset versions, training times, sample/category/account counts, validation method, accuracy, macro F1, per-category metrics/eligibility, latency, digest, and promotion reason.
- [ ] Save model and metadata to new immutable paths first. Reload and verify digest/prediction parity. Switch one small `active.json` manifest with `os.replace`; never overwrite an artifact named by `model_id`.
- [ ] If the active artifact cannot load or repeatedly fails prediction, atomically restore the verified `previous` manifest, persist the fallback reason, and emit the model-runtime failure used by Attention. Never reclassify or retrigger old messages during fallback.
- [ ] Keep `CpuTfidfLogisticClassifier`; do not add fastText to production. Warm jieba and load active model before mailbox work starts.
- [ ] Train in a short-lived subprocess invoked by the email child. The parent continues scanning and polls a durable training run; no new launchd service is created.
- [ ] Preserve the approved trigger: at least five unincluded feedback rows and either 30 seconds idle or 10 minutes since the last training. Manual training uses the same readiness/validation path.
- [ ] Mark each authoritative sample's `included_in_model_id` only after successful promotion. Predictions never become labels.
- [ ] Run the model lifecycle suites.

Run: `.venv/bin/python -m pytest tests/test_email_classifier_model.py tests/test_email_model_registry.py tests/test_email_classifier_training.py tests/test_email_classifier_learning.py tests/test_email_classifier_runtime.py -q`

Expected: PASS, including reload parity and active-manifest atomicity.

- [ ] Commit model lifecycle changes.

```bash
git add app/email_model_registry.py tests/test_email_model_registry.py app/email_classifier_model.py app/email_classifier_training.py app/email_classifier_retrain.py app/email_classifier_learning.py app/email_classifier_runtime.py tests/test_email_classifier_model.py tests/test_email_classifier_training.py tests/test_email_classifier_learning.py tests/test_email_classifier_runtime.py
git commit -m "feat: version and promote email models atomically"
```

### Task 7: Build the final classification and feedback-to-ActionPlan pipeline

**Files:**

- Create: `app/email_pipeline.py`
- Create: `tests/test_email_pipeline.py`
- Modify: `app/email_classifier_scan.py`
- Modify: `app/email_classifier_learning.py`
- Modify: `tests/test_email_classifier_scan_model.py`
- Modify: `tests/test_email_classifier_learning.py`

- [ ] Add tests for all four decision cases:

```text
confidence meets threshold + category eligible → processed + immutable ActionPlan
confidence meets threshold + category ineligible → pending_feedback + no ActionPlan
confidence below threshold                   → pending_feedback + no ActionPlan
human confirms any pending classification   → processed + current-config ActionPlan
```

- [ ] Implement a single decision function; eligibility is mandatory.

```python
def decide_classification(
    prediction: EmailModelPrediction,
    category_config: EmailCategoryConfig,
    eligibility: CategoryEligibility,
) -> EmailClassificationDecision:
    automatic = (
        category_config.enabled
        and eligibility.auto_action_eligible
        and prediction.confidence >= category_config.threshold
    )
    if not automatic:
        return EmailClassificationDecision.pending_feedback_from(prediction)
    return EmailClassificationDecision.processed_from_model(
        prediction=prediction,
        action_plan=build_action_plan(prediction, category_config),
    )
```

- [ ] Human confirmation first persists authoritative feedback, then snapshots the current category config into a new immutable ActionPlan. Confirmation alone never creates an unrelated task; only configured `auto_reply`/`unsubscribe` actions in that plan are later mapped to tasks.
- [ ] Support correcting a processed classification by appending a new human training label, creating ActionPlan version `n+1`, and changing only `current_action_plan_id`; preserve the previous plan and attempts for traceability. Do not mutate old actions into the new category.
- [ ] Record full `model_id` and `config_version` on classification, sample, plan, action, and later task metadata.
- [ ] Run pipeline tests.

Run: `.venv/bin/python -m pytest tests/test_email_pipeline.py tests/test_email_classifier_scan_model.py tests/test_email_classifier_learning.py -q`

Expected: PASS.

- [ ] Commit the pipeline.

```bash
git add app/email_pipeline.py tests/test_email_pipeline.py app/email_classifier_scan.py app/email_classifier_learning.py tests/test_email_classifier_scan_model.py tests/test_email_classifier_learning.py
git commit -m "feat: create gated email classification plans"
```

## Phase 3 — Provider writes and Agent task routing

### Task 8: Execute deterministic IMAP actions with readback and recovery

**Files:**

- Create: `app/email_provider_actions.py`
- Create: `tests/test_email_provider_actions.py`
- Modify: `app/email_store.py`
- Modify: `tests/test_email_store.py`

- [ ] Build a stateful fake IMAP provider and write failing tests for label, mark-read, archive, move, and trash; already-satisfied state; provider timeout after write; provider mismatch; retry; and no EXPUNGE.
- [ ] Use this exact execution boundary:

```python
@dataclass(frozen=True)
class ProviderActionResult:
    status: Literal["done", "failed"]
    provider_operation: str
    provider_target: str
    provider_result_id: str
    error: str = ""


class DeterministicEmailActionExecutor:
    def execute(self, action: StoredEmailAction) -> ProviderActionResult:
        current = self.provider.read_state(action.locator)
        if current.satisfies(action.action_type, action.parameters):
            return ProviderActionResult(
                status="done",
                provider_operation="readback_noop",
                provider_target=action.locator.stable_message_identity,
                provider_result_id=current.revision,
            )
        self.provider.apply(action.locator, action.action_type, action.parameters)
        verified = self.provider.read_state(action.locator)
        return verified.to_result(action)
```

- [ ] On startup recovery, turn stale `processing` direct actions back into claimable work, read provider state first, and only write if the goal is not already satisfied.
- [ ] `archive`, `move`, and `trash` are mutually exclusive destination operations. Trash moves/marks into provider trash semantics but never issues EXPUNGE or permanent delete.
- [ ] Append every attempt and update current action state in one transaction. A mismatch after the write is `failed` and can enter Attention; do not call it done from a successful command response alone.
- [ ] Run focused tests.

Run: `.venv/bin/python -m pytest tests/test_email_provider_actions.py tests/test_email_store.py -q`

Expected: PASS; fake command log contains no `EXPUNGE`.

- [ ] Commit deterministic writes separately from Agent/Audit routing.

```bash
git add app/email_provider_actions.py tests/test_email_provider_actions.py app/email_store.py tests/test_email_store.py
git commit -m "feat: execute recoverable direct email actions"
```

### Task 9: Create idempotent email Agent tasks and preserve Audit lifecycle

This is the explicitly scoped Audit integration change. Keep it separate from Task 8 so direct actions cannot enter Agent routing accidentally.

**Files:**

- Create: `app/email_task_adapter.py`
- Create: `tests/test_email_task_adapter.py`
- Modify: `app/store.py`
- Modify: `tests/test_task_store.py`
- Modify: `app/agent_context.py`
- Modify: `tests/test_agent_context.py`
- Modify: `skills/ceo-mail-review/SKILL.md`
- Modify: `tests/test_business_skills.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

- [ ] Add tests proving only `auto_reply` and `unsubscribe` create `reply_tasks`, using this idempotency identity:

```text
account_id + stable_message_identity + action_type + action_plan_version
```

- [ ] Use `channel="email"`, a deterministic conversation ID based on account + thread identity, and a deterministic trigger ID based on the action identity. Keep the existing unique `(channel, conversation_id, trigger_message_id)` queue contract.
- [ ] Store the action metadata needed for traceability in `trigger_raw_payload`, but assert the payload contains no credential, raw attachment bytes, local path, full unsubscribe token URL, or unredacted secret.
- [ ] Build `AgentTaskContext` from email/thread text and attachment metadata only:

```python
AgentTaskContext(
    channel="email",
    conversation=conversation,
    trigger=trigger,
    messages=thread_messages,
    materials=attachment_metadata_materials,
    prior_action_receipts=prior_receipts,
    raw_payload=safe_action_metadata,
    image_paths=(),
)
```

- [ ] Update `ceo-mail-review` so the persisted immutable ActionPlan is the current authorization for `auto_reply` or `unsubscribe`. Remove instructions to inspect attachment/link content. Require text-only message/thread evidence, attachment metadata only, sent/unsubscribe-state readback, and no invented attachment facts.
- [ ] Keep the existing execution Agent → Audit Agent → feedback/revision contracts unchanged. Do not add a direct send from the adapter and do not add new task statuses.
- [ ] Add a regression test that classification confirmation with zero Agent actions creates no `reply_task`.
- [ ] Run the focused Audit-routing tests.

Run: `.venv/bin/python -m pytest tests/test_email_task_adapter.py tests/test_task_store.py tests/test_agent_context.py tests/test_business_skills.py -q`

Expected: PASS.

- [ ] Commit this explicitly scoped Audit integration.

```bash
git add app/email_task_adapter.py tests/test_email_task_adapter.py app/store.py tests/test_task_store.py app/agent_context.py tests/test_agent_context.py skills/ceo-mail-review/SKILL.md tests/test_business_skills.py docs/architecture.md docs/runtime-mechanism.md
git commit -m "feat: route email agent actions through audit"
```

### Task 10: Implement automatic reply delivery with stable Message-ID reconciliation

**Files:**

- Create: `app/email_reply_delivery.py`
- Create: `tests/test_email_reply_delivery.py`
- Modify: `app/email_task_adapter.py`
- Modify: `app/email_store.py`

- [ ] Add a fake SMTP + Sent-folder test harness. Cover normal send, correct SMTP/Sent account selection, timeout before send, timeout after provider acceptance, restart during reconciliation, equivalent reply already present, and provider readback failure.
- [ ] Derive one stable outgoing Message-ID from the immutable action identity.

```python
def outgoing_message_id(action_identity: str, domain: str) -> str:
    digest = hashlib.sha256(action_identity.encode("utf-8")).hexdigest()[:32]
    return f"<ceo-email-{digest}@{domain}>"
```

- [ ] Before SMTP, search Sent/current thread for that Message-ID or an existing persisted receipt. After SMTP, search Sent and persist the provider result before completing the effect.
- [ ] If SMTP times out, mark the effect unresolved inside the existing Audit recovery lifecycle and reconcile Sent before any retry. Never send again solely because the client did not receive a success response.
- [ ] Ensure the execution proposal carries the exact recipient/thread/body produced by the Agent and reviewed by Audit; the sender must not regenerate or reinterpret it after Audit.
- [ ] Store only redacted excerpts in display/error projections; the durable provider receipt may contain provider IDs but not credentials.
- [ ] Run focused tests.

Run: `.venv/bin/python -m pytest tests/test_email_reply_delivery.py tests/test_email_task_adapter.py -q`

Expected: PASS, with exactly one fake SMTP acceptance across timeout/restart cases.

- [ ] Commit reply delivery.

```bash
git add app/email_reply_delivery.py tests/test_email_reply_delivery.py app/email_task_adapter.py app/email_store.py
git commit -m "feat: reconcile automatic email replies"
```

### Task 11: Implement multi-step browser unsubscribe as an Agent action

**Files:**

- Create: `app/email_unsubscribe.py`
- Create: `tests/test_email_unsubscribe.py`
- Create: `tests/browser/test_email_unsubscribe_browser.py`
- Modify: `app/email_task_adapter.py`
- Modify: `skills/ceo-mail-review/SKILL.md`

- [ ] Unit-test extraction and prioritization of RFC `List-Unsubscribe` entries and message-body links without storing token-bearing URLs in logs/status/history.
- [ ] Implement one typed outcome contract:

```python
class UnsubscribeOutcome(str, Enum):
    DONE = "done"
    ALREADY_UNSUBSCRIBED = "already_unsubscribed"
    SKIPPED_NO_RELIABLE_ENTRY = "skipped_no_reliable_entry"
    SKIPPED_LOGIN_REQUIRED = "skipped_login_required"
    SKIPPED_CAPTCHA = "skipped_captcha"
    SKIPPED_PAYMENT = "skipped_payment"
    FAILED_BROWSER = "failed_browser"
    FAILED_PROVIDER_AUTH = "failed_provider_auth"
```

- [ ] Map normal unsupported business conditions to existing task status `skipped`; map browser/runtime/auth technical failures to `failed` and existing retry/Attention handling. Do not create a new top-level task status for each outcome.
- [ ] Build local browser fixtures for direct success, redirect, two-step form, final confirmation click, already-unsubscribed page, login, CAPTCHA, payment request, and confirmation-email flow.
- [ ] Before resubmitting after restart, open the current page/provider state and check confirmation mail/receipt. Persist a redacted step journal and terminal receipt; never expose query tokens.
- [ ] Require no per-message user confirmation. The immutable ActionPlan is the authorization; Audit reviews the Agent's proposed browser operations under the existing lifecycle.
- [ ] Run unit and browser fixtures.

Run: `.venv/bin/python -m pytest tests/test_email_unsubscribe.py -q`

Expected: PASS.

Run: `WORKBENCH_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/browser/test_email_unsubscribe_browser.py -q`

Expected: PASS on all local fixtures; no external subscription is changed.

- [ ] Commit unsubscribe work.

```bash
git add app/email_unsubscribe.py tests/test_email_unsubscribe.py tests/browser/test_email_unsubscribe_browser.py app/email_task_adapter.py skills/ceo-mail-review/SKILL.md
git commit -m "feat: automate audited email unsubscribe flows"
```

## Phase 4 — Independent runtime and user surfaces

### Task 12: Add an independent email worker under the existing supervisor

**Files:**

- Create: `app/email_worker.py`
- Create: `tests/test_email_worker.py`
- Modify: `app/service_supervisor.py`
- Modify: `tests/test_service_supervisor.py`
- Modify: `app/cli.py`
- Modify: `tests/test_cli.py`

- [ ] Add tests that the supervisor starts `service`, `audit-web`, and `email-worker`; restarting one email child leaves both existing children running; shutdown terminates all three.
- [ ] Add one CLI command:

```text
ceo-agent email-worker --db PATH --workspace PATH --corpus-dir PATH
```

It loads enabled accounts and the active model before reporting ready.
- [ ] Implement the email child with isolated loops rather than one sequential loop:

```python
components = (
    ("email-scan-actions", run_scan_and_direct_actions_loop),
    ("email-agent-consumer", run_email_agent_task_loop),
    ("email-training", run_training_scheduler_loop),
)
```

Use the project's existing component-thread supervision pattern. A long Agent or browser run must not delay the scan/direct-action loop.
- [ ] `run_email_agent_task_loop` claims only `channel=email`; do not change `DingTalkAutoReplyWorker.consume_once` to recognize email and do not share its conversation adapter.
- [ ] Reuse `AgentOrchestrator` and the existing task/audit/revision stores. Add email-specific context and effect adapters only at their current extension points.
- [ ] Isolate account failures inside the scan loop: one account's authentication/provider error updates that account's health and must not prevent other due accounts from scanning. Record process/component heartbeat and bounded provider errors without leaking message bodies, attachment names, secret references, or URLs.
- [ ] Run runtime tests.

Run: `.venv/bin/python -m pytest tests/test_email_worker.py tests/test_service_supervisor.py tests/test_cli.py -q`

Expected: PASS; the restart test proves only the exited email child is replaced.

- [ ] Commit runtime wiring.

```bash
git add app/email_worker.py tests/test_email_worker.py app/service_supervisor.py tests/test_service_supervisor.py app/cli.py tests/test_cli.py
git commit -m "feat: supervise independent email worker"
```

### Task 13: Complete Email and connector APIs

**Files:**

- Modify: `app/web_api/email.py`
- Modify: `app/audit_web.py`
- Modify: `app/web_api/registration.py`
- Modify: `tests/test_console_web_api.py`
- Modify: `tests/test_audit_web.py`

- [ ] Add API contract tests for account filtering, processed/pending rows, action state, feedback, processed correction, category action parameters, current model, model history, category eligibility, learning state, and manual training.
- [ ] Expose these endpoints:

```text
GET  /api/console/email/classifications?status=processed&account_id=ding-main
GET  /api/console/email/classifications?status=pending_feedback&account_id=ding-main
POST /api/console/email/classifications/{id}/feedback
POST /api/console/email/classifications/{id}/correct
GET  /api/console/email/config
PUT  /api/console/email/config/{category}
GET  /api/console/email/learning
POST /api/console/email/learning/train
GET  /api/console/email/models
GET  /api/console/email/accounts
POST /api/console/email/accounts
PUT  /api/console/email/accounts/{account_id}
POST /api/console/email/accounts/{account_id}/test
```

- [ ] A feedback response returns the final classification and immutable ActionPlan snapshot, but does not claim actions are complete. Manual training returns a durable training-run ID and current readiness decision, not a false synchronous success.
- [ ] Keep the Email page at exactly three tabs. Account configuration appears under Settings / Connectors / Email, not a fourth Email tab.
- [ ] Verify all responses omit passwords, message bodies beyond the approved preview, full unsubscribe URLs, and attachment names from Status. The pending-feedback endpoint may include attachment metadata because that is part of the Email review page.
- [ ] Run API tests.

Run: `.venv/bin/python -m pytest tests/test_console_web_api.py tests/test_audit_web.py -q`

Expected: PASS.

- [ ] Commit the API surface.

```bash
git add app/web_api/email.py app/audit_web.py app/web_api/registration.py tests/test_console_web_api.py tests/test_audit_web.py
git commit -m "feat: expose email operations and learning APIs"
```

### Task 14: Complete the three-tab Email UI and Email connector settings

**Files:**

- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/pages/EmailPage.tsx`
- Modify: `frontend/src/pages/EmailPage.test.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Write failing Vitest tests for:

  - exactly three tabs: 已处理, 待反馈, 邮件配置;
  - account filter on processed/pending views;
  - model/user classification source, confidence, margin, full model ID and config version;
  - independent direct/Agent action states and links;
  - pending confirmation with model suggestion and attachment metadata;
  - category action parameters and eligibility reasons;
  - current model/sample/account/category metrics, auto-learning switch, pending-feedback count, manual training, and model history;
  - multiple Email accounts under Connectors with secret-configured indicators and restart-required notice.

- [ ] Replace loose `Record<string, unknown>` email fields with exact TypeScript interfaces matching Task 13, including `EmailActionItem`, `EmailModelVersion`, `EmailCategoryEligibility`, `EmailLearningState`, and `EmailAccountItem`.
- [ ] Do not show an open-ended “处理” control in 待反馈. The only business action is confirming/correcting the category.
- [ ] In 已处理, separate final classification state from action state; a classified row may correctly show “动作处理中” or one failed action.
- [ ] In 邮件配置, keep the order: categories/actions → current model → per-category eligibility → learning controls → model history.
- [ ] Build and test the frontend.

Run: `npm test --prefix frontend -- --run frontend/src/pages/EmailPage.test.tsx frontend/src/pages/SettingsPage.test.tsx`

Expected: PASS.

Run: `npm run build --prefix frontend`

Expected: TypeScript and Vite build succeed.

- [ ] Commit the UI.

```bash
git add frontend/src/api/console.ts frontend/src/pages/EmailPage.tsx frontend/src/pages/EmailPage.test.tsx frontend/src/pages/SettingsPage.tsx frontend/src/pages/SettingsPage.test.tsx frontend/src/styles.css
git commit -m "feat: complete email management interface"
```

### Task 15: Project Email into Status, History, and Attention correctly

**Files:**

- Modify: `app/audit_web.py`
- Modify: `app/web_api/registration.py`
- Modify: `app/task_retrieval.py`
- Modify: `tests/test_audit_web.py`
- Modify: `tests/test_console_web_api.py`
- Modify: `tests/test_task_retrieval.py`
- Modify: `frontend/src/pages/StatusPage.tsx`
- Modify: `frontend/src/pages/StatusPage.test.tsx`

- [ ] Add Status tests for email child PID/start/heartbeat, per-account enabled/last scan/cursor/latency/error, active model ID, pending-feedback count, direct-action queue, auto-reply/unsubscribe task counts, and latest training.
- [ ] Add History tests proving only actual `channel=email` Agent runs for `auto_reply` and `unsubscribe` appear. Direct label/read/archive/move/trash attempts remain on Email rows.
- [ ] Add Attention tests for auth failure, unrecoverable cursor, direct-action mismatch/failure, exhausted Agent task failure, browser/runtime failure, active-model load/prediction failure, and verified fallback to the previous model.
- [ ] Add negative Attention tests for pending feedback, insufficient samples, no configured action, no reliable unsubscribe entry, login, CAPTCHA, and payment-triggered `skipped`.
- [ ] Deduplicate current Attention by stable root-cause/context identity and remove it after recovery while retaining historical records.
- [ ] Status must not expose secrets, full body, attachment filename, or full unsubscribe URL.
- [ ] Run backend and frontend projection tests.

Run: `.venv/bin/python -m pytest tests/test_audit_web.py tests/test_console_web_api.py tests/test_task_retrieval.py -q`

Expected: PASS.

Run: `npm test --prefix frontend -- --run frontend/src/pages/StatusPage.test.tsx`

Expected: PASS.

- [ ] Commit observability projections.

```bash
git add app/audit_web.py app/web_api/registration.py app/task_retrieval.py tests/test_audit_web.py tests/test_console_web_api.py tests/test_task_retrieval.py frontend/src/pages/StatusPage.tsx frontend/src/pages/StatusPage.test.tsx
git commit -m "feat: expose email runtime observability"
```

## Phase 5 — Documentation, regression, and staged production validation

### Task 16: Verify the complete subsystem and perform controlled mailbox rollout

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: `docs/superpowers/specs/2026-08-29-email-classifier-design.md`
- Modify: `docs/superpowers/specs/2026-08-29-email-ceo-agent-integration-design.md`
- Modify: `/Users/derek/Documents/Projects/tools/email_triage/EXPERIMENT_REPORT.md`
- Modify only if required by deployment: `scripts/install-auto-reply-agents.sh`
- Modify only if runtime arguments differ: `launchd/com.ceo-agent-service.main.plist`

- [ ] Run all focused email tests first.

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_email_classifier_contracts.py \
  tests/test_email_connector_config.py \
  tests/test_email_store.py \
  tests/test_email_imap_readonly.py \
  tests/test_email_classifier_model.py \
  tests/test_email_model_registry.py \
  tests/test_email_classifier_training.py \
  tests/test_email_classifier_learning.py \
  tests/test_email_classifier_runtime.py \
  tests/test_email_classifier_scan_model.py \
  tests/test_email_pipeline.py \
  tests/test_email_provider_actions.py \
  tests/test_email_task_adapter.py \
  tests/test_email_reply_delivery.py \
  tests/test_email_unsubscribe.py \
  tests/test_email_worker.py -q
```

Expected: PASS with zero skipped core email tests.

- [ ] Run the full backend and frontend gates.

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass; investigate any failure instead of excluding it.

Run: `.venv/bin/python -m ruff check app tests`

Expected: PASS.

Run: `npm test --prefix frontend -- --run`

Expected: PASS.

Run: `npm run build --prefix frontend`

Expected: PASS.

- [ ] Re-run the resident-process CPU benchmark from the shared Conda environment, with single-thread variables, using the current production classifier and sanitized corpus. Record p50/p95/p99/max, startup warmup, model load, artifact size, feature count, Python/platform/CPU, sample count, and exact git revision in `EXPERIMENT_REPORT.md` and the classifier design.

Run:

```bash
env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  /Users/derek/miniforge3/bin/python \
  /Users/derek/Documents/Projects/tools/email_triage/benchmark_classifier_latency.py
```

Expected: warm end-to-end p95 `< 100 ms`; otherwise stop before live writes.

- [ ] Update architecture/runtime docs with the final process tree, database identities, queue ownership, Audit boundary, recovery rules, API/UI semantics, secret handling, and operator commands. Record measured results, not intended results.
- [ ] Commit documentation and any separately justified deployment wiring. Keep a launchd/plist or Audit-policy change in its own commit if it was not already covered by the explicitly scoped tasks.

```bash
git add docs/architecture.md docs/runtime-mechanism.md docs/superpowers/specs/2026-08-29-email-classifier-design.md docs/superpowers/specs/2026-08-29-email-ceo-agent-integration-design.md
git commit -m "docs: record email subsystem verification"
```

- [ ] Resolve the live launchd checkout, interpreter, database, environment file, and currently deployed revision. Back up the live SQLite database and verify the backup opens before any migration. Do not copy development `.env` secrets into logs or documentation.
- [ ] Deploy and restart the existing service only after code, tests, docs, and the user-approved production step are ready.

Run: `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`

Expected: command succeeds.

Run: `launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,100p'`

Expected: new parent PID, correct release root/interpreter, and all three children become healthy.

- [ ] Execute the controlled real-mail acceptance in this exact order, documenting provider readback after every effect:

  1. Configure at least two accounts; run read-only scan/classify/feedback only.
  2. Enable label, mark-read, and archive on controlled test messages; verify provider state and restart idempotence.
  3. Enable auto-reply only for a controlled sender/category; verify Agent run, Audit revision, one SMTP acceptance, stable Message-ID, and Sent readback across a forced timeout/restart.
  4. Use a dedicated test subscription for direct, redirect, form, confirmation-click, and confirmation-email unsubscribe paths; verify redaction and restart behavior.
  5. Enable a production category only after its confirmed validation precision/sample gate is met. Leave every other category in pending-feedback mode.

- [ ] Query the live database and API after rollout. Confirm no unresolved `failed` or stale `processing` backlog, no duplicate effects, no pending feedback in Attention, accurate model/config/action/task traceability, and no secret/body/URL leakage in Status/History/error displays.
- [ ] If any external write has an ambiguous result, stop new work for that action, reconcile provider state read-only, and do not replay until identity and current state prove a retry is safe.
- [ ] Record the exact live test IDs, model ID, account IDs, action plan IDs, task/run IDs, provider receipt IDs, test counts, commit hashes, service PID, and remaining disabled categories in the experiment report and operator docs.

## Spec coverage map

1. Goal and approved product boundary: Preconditions, Tasks 1, 7, 14.
2. Three-tab Email lifecycle and classification/task separation: Tasks 7, 13, 14.
3. One supervised child with isolated scan and Agent loops: Task 12.
4. Multiple account connector, shared model/config, UID cursors, and stable identity: Tasks 3–5.
5. Final classification, immutable ActionPlan, and config validation: Tasks 2, 3, 7.
6. Deterministic actions and provider readback: Task 8.
7. Automatic reply creation, Audit, stable Message-ID, and Sent reconciliation: Tasks 9–10.
8. Multi-step browser unsubscribe and explicit skipped outcomes: Tasks 9, 11.
9. `channel=email` mapping and deduplication: Task 9.
10. Human-only learning labels, model history, full version ID, candidate promotion, and category eligibility: Tasks 1, 6, 7.
11. Email, learning, and connector pages/APIs: Tasks 13–14.
12. Status, History, and Attention semantics: Task 15.
13. Interruption recovery for scan, direct action, reply, unsubscribe, and model fallback: Tasks 3, 5, 6, 8, 10, 11, 16.
14. Error sanitization and privacy boundaries: Tasks 3–5, 9–15.
15. Automated, browser-fixture, CPU, and staged real-mail acceptance: Task 16.
16. Non-goals and rollout exclusions: Preconditions and every phase gate.

## Final spec-coverage checklist

- [ ] Multiple IMAP/SMTP accounts are independently scanned and share one model/config/training set.
- [ ] Stable identity survives rescans and moves; cursors advance only after durable classification state.
- [ ] Pending feedback performs no action and creates no task.
- [ ] Human confirmation becomes authoritative training data and snapshots current config into an immutable ActionPlan.
- [ ] Full immutable `model_id` is present on classifications, samples, plans, actions, and Agent tasks.
- [ ] Global model promotion and per-category automatic-action eligibility are separate decisions.
- [ ] Direct actions never use Agent/Audit and always use provider read-before-write/readback.
- [ ] Auto-reply and unsubscribe alone create `channel=email` tasks and use execution Agent → Audit Agent → revision.
- [ ] Agent receives message/thread text plus attachment metadata only; no attachment content is downloaded or interpreted.
- [ ] Reply retry reconciles Sent by stable Message-ID; unsubscribe retry reconciles browser/provider state.
- [ ] Model failure restores the verified previous version without reclassifying or retriggering old mail.
- [ ] Email remains exactly three tabs; account configuration remains under Settings / Connectors / Email.
- [ ] Status, History, and Attention follow the approved boundaries.
- [ ] Resident CPU prediction p95 is below 100 ms on every validated target machine.
- [ ] Live writes remain disabled for categories that have not met their own validation gate.
- [ ] Documentation and experiment evidence contain exact current measurements and revisions.
