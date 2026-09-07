# Email Folder Classifier Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mailbox folders the authoritative category label, run an Agent-first unread-mail cold start, train a staged Jina-embedding classifier with category descriptions and an independent important head, and promote it only after the approved evidence gates.

**Architecture:** Replace the fixed category enum with validated category keys and account-scoped folder bindings. Split mailbox observation from classification decisions: provider adapters report folder role, unread state, important signals, and locator; an Agent classifies unread unbound mail during cold start; offline snapshots train a versioned embedding-plus-MLP candidate; mature candidates may classify historical mail and, only after whole-model promotion, become the primary real-time path with Agent fallback. Deterministic move/flag actions remain outside the Agent, while junk reuses the existing audited unsubscribe lifecycle.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLite, IMAP plus provider-specific adapters, NumPy/scikit-learn for the small classification heads, an OpenAI-compatible HTTP endpoint serving `jinaai/jina-embeddings-v5-text-small`, pytest, launchd.

---

## Scope and sequencing rules

- This plan implements backend contracts, storage, provider behavior, worker/runtime behavior, model lifecycle, Skills, and observability APIs. It does not implement the Email console UX; that work is owned elsewhere.
- Read `docs/architecture.md`, `docs/runtime-mechanism.md`, and the approved design `docs/superpowers/specs/2026-09-06-email-folder-source-and-staged-classifier-design.md` before Task 1.
- Preserve the existing Execution Agent -> Audit Agent lifecycle. Do not add a new audit gate to folder moves, flags, classification, or model promotion. Reuse the existing audited unsubscribe operation unchanged except for the explicitly scoped continuation behavior in Task 11.
- Each task ends in its own commit. Stage only the files listed in that task. If another agent changed one of those files, stop and reconcile ownership before staging.
- Use the repository's configured Python environment for every test command. Run focused tests before the commit and the complete suite in Task 12.

## Task 1: Replace the fixed category enum with dynamic category keys

**Files:**

- Modify: `app/email_classifier_contracts.py`
- Modify: `app/email_pipeline.py`
- Modify: `app/email_classifier_model.py`
- Modify: `tests/test_email_classifier_contracts.py`
- Modify: `tests/test_email_pipeline.py`
- Modify: `tests/test_email_classifier_model.py`

- [ ] **Step 1: Write failing category-contract tests**

Add tests proving that the initial set is exactly:

```python
INITIAL_EMAIL_CATEGORY_KEYS = (
    "work",
    "human_resources",
    "legal",
    "financing",
    "personal",
    "notification",
    "external_billing",
    "shopping",
    "junk",
)
```

Test that `important`, `subscription`, `other`, and `billing` are rejected as new category keys; test that a valid custom key such as `board_governance` is accepted. Test normalization rejects outer whitespace, uppercase, path separators, and keys longer than 64 characters.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
pytest -q tests/test_email_classifier_contracts.py tests/test_email_pipeline.py tests/test_email_classifier_model.py
```

Expected: failures reference the old `EmailCategory` enum and missing dynamic-key validation.

- [ ] **Step 3: Introduce the key type and initial definitions**

In `app/email_classifier_contracts.py`, replace enum identity checks with string equality and add:

```python
EmailCategoryKey = Annotated[
    StrictStr,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
]

INITIAL_EMAIL_CATEGORY_KEYS: tuple[str, ...] = (
    "work",
    "human_resources",
    "legal",
    "financing",
    "personal",
    "notification",
    "external_billing",
    "shopping",
    "junk",
)
RESERVED_EMAIL_CATEGORY_KEYS = frozenset({"important", "subscription", "other", "billing"})
_EMAIL_CATEGORY_KEY_ADAPTER = TypeAdapter(EmailCategoryKey)

def validate_email_category_key(value: object) -> str:
    key = _EMAIL_CATEGORY_KEY_ADAPTER.validate_python(value)
    if key in RESERVED_EMAIL_CATEGORY_KEYS:
        raise ValueError("reserved email category key")
    return key
```

The validator must return the exact supplied key after validation; it must not silently lowercase or trim it. Update `EmailActionPlan`, `EmailClassification`, identity hashing, and serialization to store category keys as strings.

- [ ] **Step 4: Remove enum-wide assumptions from pipeline/model contracts**

Change `EmailModelPrediction.category`, `EmailCategoryConfig.category`, `EmailClassificationDecision.category`, and human-confirmation parameters to category-key strings. Change TF-IDF `fit()` validation to accept an explicit `enabled_category_keys` argument rather than consulting a global enum. Keep TF-IDF load compatibility for old artifacts, but mark it legacy-only; do not make it the future online fallback.

- [ ] **Step 5: Run focused tests**

Run the command from Step 2. Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/email_classifier_contracts.py app/email_pipeline.py app/email_classifier_model.py tests/test_email_classifier_contracts.py tests/test_email_pipeline.py tests/test_email_classifier_model.py
git commit -m "refactor(email): support dynamic category keys"
```

## Task 2: Migrate category configuration and account folder bindings

**Files:**

- Modify: `app/email_store.py`
- Modify: `app/web_api/email.py`
- Modify: `tests/test_email_store.py`
- Modify: `tests/test_email_web_api.py`

- [ ] **Step 1: Write failing migration and API tests**

Cover a database containing old rows. The migration must map `billing` to `external_billing`, remove `important` and `subscription` configs, preserve classifications as immutable history, and leave historical removed-category rows readable. New category creation must require `core`, at least one `include`, at least one `exclude`, and a folder binding for each enabled account. Duplicate `(account_id, category_key)` and duplicate live provider-folder IDs must fail.

- [ ] **Step 2: Define the durable schema**

Add schema version migration for:

```sql
email_category_configs(
  category_key text primary key,
  display_name text not null,
  core_description text not null,
  include_json text not null check(json_valid(include_json)),
  exclude_json text not null check(json_valid(exclude_json)),
  threshold real not null,
  enabled integer not null,
  description_version text not null,
  config_version text not null,
  updated_at text not null
)

email_category_folder_bindings(
  account_id text not null,
  category_key text not null,
  provider_folder_id text not null,
  provider_folder_name text not null,
  binding_status text not null check(binding_status in ('active','missing','ambiguous','error')),
  last_verified_at text not null,
  primary key(account_id, category_key)
)
```

Add a partial unique index preventing two active bindings in one account from claiming the same `provider_folder_id`. Seed the nine initial descriptions from the approved spec, with explicit external-billing inclusions and exclusions. Do not infer a binding for the legacy folder name `发票`.

- [ ] **Step 3: Add atomic store methods**

Implement store methods named `list_category_configs`, `get_category_config`, `create_category_with_bindings`, `update_category_descriptions`, `set_folder_binding_status`, and `list_account_folder_bindings`. Their arguments must be typed values corresponding exactly to the schema fields above. Creation must be transactional: config activation occurs only after callers provide read-back-verified bindings for every enabled account.

- [ ] **Step 4: Update backend APIs without building UI**

Keep `GET /api/console/email/config`, but return structured descriptions and bindings. Add `POST /api/console/email/config` for a dynamic category and make `PUT /api/console/email/config/{category_key}` update descriptions/threshold/enabled state. Reject reserved keys with `400 invalid_email_category`; return `409 email_folder_binding_conflict` for exact binding conflicts.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_email_store.py tests/test_email_web_api.py
git add app/email_store.py app/web_api/email.py tests/test_email_store.py tests/test_email_web_api.py
git commit -m "feat(email): persist categories and folder bindings"
```

## Task 3: Add provider folder inventory, exact creation, and current-folder truth

**Files:**

- Modify: `app/email_imap_readonly.py`
- Modify: `app/email_provider_actions.py`
- Modify: `app/email_worker.py`
- Add: `app/email_folder_truth.py`
- Add: `tests/test_email_folder_truth.py`
- Modify: `tests/test_email_imap_readonly.py`
- Modify: `tests/test_email_provider_actions.py`
- Modify: `tests/test_email_worker.py`

- [ ] **Step 1: Write failing provider-role tests**

Test parsing of IMAP `LIST` special-use attributes (`\\Inbox`, `\\Junk`, `\\Trash`, `\\Sent`, `\\Drafts`), modified UTF-7 names, stable provider IDs where exposed, exact-name matching, duplicate-name ambiguity, folder creation and readback. Test the truth result values `categorized`, `unclassified`, `junk`, `excluded`, and `unavailable`.

- [ ] **Step 2: Add provider inventory contracts**

Create immutable values:

```python
class FolderRole(StrEnum):
    INBOX = "inbox"
    CATEGORY = "category"
    JUNK = "junk"
    TRASH = "trash"
    SENT = "sent"
    DRAFT = "draft"
    UNBOUND = "unbound"

@dataclass(frozen=True)
class ProviderFolder:
    provider_folder_id: str
    display_name: str
    role: FolderRole
```

Extend the provider protocol with `list_folders()` and `create_folder_exact(name)`. IMAP uses special-use flags first, then provider-advertised well-known folders; it must not classify a folder by fuzzy localized-name matching.

- [ ] **Step 3: Implement folder materialization and readback**

For each enabled category and account: exact-bind one existing folder if unique; otherwise create it, list again, and activate only the exact readback. `junk` binds to the system Trash role and never creates a business folder. Missing/ambiguous bindings pause writes for that account/category.

- [ ] **Step 4: Resolve current category exclusively from provider state**

`app/email_folder_truth.py` must accept provider folder ID plus bindings and return category state. Do not read predicted/confirmed categories as fallback. Spam/Junk and Trash/Deleted return `junk`; Sent/Draft return excluded; Inbox and unbound return unclassified. A provider read error returns unavailable.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_email_folder_truth.py tests/test_email_imap_readonly.py tests/test_email_provider_actions.py tests/test_email_worker.py
git add app/email_imap_readonly.py app/email_provider_actions.py app/email_worker.py app/email_folder_truth.py tests/test_email_folder_truth.py tests/test_email_imap_readonly.py tests/test_email_provider_actions.py tests/test_email_worker.py
git commit -m "feat(email): make provider folders category truth"
```

## Task 4: Normalize important signals and add an idempotent flag action

**Files:**

- Modify: `app/email_classifier_contracts.py`
- Modify: `app/email_imap_readonly.py`
- Modify: `app/email_provider_actions.py`
- Modify: `app/email_store.py`
- Add: `app/email_important.py`
- Add: `tests/test_email_important.py`
- Modify: `tests/test_email_provider_actions.py`
- Modify: `tests/test_email_imap_readonly.py`
- Modify: `tests/test_email_store.py`

- [ ] **Step 1: Write failing union tests**

Test DingTalk (`11`, `1`, `107`, `PRY_HIGH`), Gmail (`STARRED`, `IMPORTANT`), standard IMAP (`\\Flagged`, `$Important`), and Graph (`flagged`, high importance, focused). Test that `junk` forces `important_effective=False`. Test move-then-flag ordering and retry of flag alone after a successful move.

- [ ] **Step 2: Implement provider-neutral important observation**

Add `ImportantSignals` with raw signal names and `provider_important: bool`. Provider adapters map only the approved trusted signals; ordinary priority headers and subject keywords remain features, not direct signals.

- [ ] **Step 3: Add `FLAG_IMPORTANT` as a deterministic action**

Extend `EmailAction`, `DIRECT_ACTIONS`, durable action constraints, executor readback, and IMAP implementation. IMAP applies `\\Flagged`, plus `$Important` only when advertised as a permanent keyword. Provider-specific adapters may map the same action to their native operation. A satisfied flag action is a readback no-op.

- [ ] **Step 4: Preserve the changed locator between actions**

When a move completes, persist its `updated_locator`. Generate/claim the flag action against that locator. If flag fails, keep move `done` and retry only the flag action.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_email_important.py tests/test_email_provider_actions.py tests/test_email_imap_readonly.py tests/test_email_store.py
git add app/email_classifier_contracts.py app/email_imap_readonly.py app/email_provider_actions.py app/email_important.py app/email_store.py tests/test_email_important.py tests/test_email_provider_actions.py tests/test_email_imap_readonly.py tests/test_email_store.py
git commit -m "feat(email): normalize and apply important flags"
```

## Task 5: Build folder-derived training snapshots

**Files:**

- Add: `app/email_training_snapshot.py`
- Modify: `app/email_experiment_snapshot.py`
- Modify: `app/email_store.py`
- Add: `tests/test_email_training_snapshot.py`
- Modify: `tests/test_email_experiment_snapshot.py`
- Modify: `tests/test_email_store.py`

- [ ] **Step 1: Write failing snapshot tests**

Use fake provider observations to prove: current bound folder wins over historical prediction; moving a mail changes its next label; all-date Spam/Trash produce junk; Inbox/unbound have no category; Sent/Draft are absent; important uses current provider union; attachments contribute metadata only; quoted body text remains included. Reject duplicate stable IDs and any duplicate-body/thread/事项 group split across train/test.

- [ ] **Step 2: Persist observations separately from prediction history**

Add a snapshot observation table keyed by `(snapshot_id, account_id, stable_message_identity)` with provider folder ID/name, category key or null, important boolean, normalized model input hash, thread/group keys, observed timestamp, and source (`natural` or `targeted`). The snapshot builder reads provider state at snapshot time and never rewrites immutable classification history.

- [ ] **Step 3: Implement deterministic input construction and grouping**

Version the input schema. Include sender/recipient structure, subject, full bounded body plus quoted text, mail headers approved by the spec, and attachment filename/MIME/size/count/inline metadata. Exclude attachment bytes. Group by the union of thread ID, duplicate normalized-body digest, sender/template signature, and explicit matter group. Balance training sampling by category and group, especially junk.

- [ ] **Step 4: Freeze snapshots**

Save ordered IDs, description version, input hashes, split assignment, seed, source distribution, and SHA-256. A later folder or star change creates a new snapshot/version; it does not mutate a frozen snapshot.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_email_training_snapshot.py tests/test_email_experiment_snapshot.py tests/test_email_store.py
git add app/email_training_snapshot.py app/email_experiment_snapshot.py app/email_store.py tests/test_email_training_snapshot.py tests/test_email_experiment_snapshot.py tests/test_email_store.py
git commit -m "feat(email): freeze folder-derived training snapshots"
```

## Task 6: Make cold-start Agent classification the only online classifier

**Files:**

- Add: `app/email_classifier_agent.py`
- Modify: `app/email_classifier_scan.py`
- Modify: `app/email_task_producer.py`
- Modify: `app/email_task_adapter.py`
- Modify: `app/email_worker.py`
- Add: `skills/ceo-email-classifier/SKILL.md`
- Add: `tests/test_email_classifier_agent.py`
- Modify: `tests/test_email_classifier_scan_model.py`
- Modify: `tests/test_email_task_producer.py`
- Modify: `tests/test_email_task_adapter.py`
- Modify: `tests/test_email_worker.py`

- [ ] **Step 1: Write failing scan-gate and output tests**

Test the exact gate: unread AND in Inbox/configured unclassified source AND no pending/completed record. Test that read mail never invokes Agent, old unread mail does, formal category folders/system folders/unconfigured folders do not, Agent never marks read, and a stable pending record prevents duplicate Agent calls.

Test the typed Agent result:

```python
class AgentClassificationResult(BaseModel):
    category: str | None
    important: bool
    certainty: Literal["certain", "uncertain"]
    confidence: float
    reason: str
    unsubscribe_candidate_index: int | None
    unsubscribe_url: str | None
```

An uncertain result must have `category=None` and no unsubscribe selection. A URL must exactly equal the indexed deterministic candidate.

- [ ] **Step 2: Add the managed classifier Skill**

The Skill must contain the nine initial descriptions and precedence rules from the spec; require one category or explicit uncertainty; treat attachment content as unavailable while allowing metadata; separate important from category; select only an exact supplied unsubscribe candidate; never move, flag, browse, reply, or send mail.

- [ ] **Step 3: Replace cold-start TF-IDF scanning**

During pre-promotion runtime, scan only metadata plus unread provider state and enqueue a dedicated `channel=email` classification task for the Email worker. Do not invoke the local TF-IDF classifier and do not run the shadow model in real time. Parse the Agent output through `AgentClassificationResult`.

- [ ] **Step 4: Persist decision and deterministic action plan**

Certain business category: plan `MOVE`, then optional `FLAG_IMPORTANT`. Certain junk: route per Task 7. Uncertain: store `pending_feedback`, leave provider location/flags/read state unchanged, and expose the Agent suggestion/reason as non-authoritative history. Category confirmation alone does not create a generic CEO task.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_email_classifier_agent.py tests/test_email_classifier_scan_model.py tests/test_email_task_producer.py tests/test_email_task_adapter.py tests/test_email_worker.py tests/test_mail_review_skill.py
git add app/email_classifier_agent.py app/email_classifier_scan.py app/email_task_producer.py app/email_task_adapter.py app/email_worker.py skills/ceo-email-classifier/SKILL.md tests/test_email_classifier_agent.py tests/test_email_classifier_scan_model.py tests/test_email_task_producer.py tests/test_email_task_adapter.py tests/test_email_worker.py
git commit -m "feat(email): classify unread inbox mail with agent"
```

## Task 7: Route junk through deterministic candidate discovery and audited unsubscribe

**Files:**

- Modify: `app/email_unsubscribe.py`
- Modify: `app/email_pipeline.py`
- Modify: `app/email_worker.py`
- Modify: `tests/test_email_unsubscribe.py`
- Modify: `tests/test_email_pipeline.py`
- Modify: `tests/test_email_worker.py`

- [ ] **Step 1: Write failing junk routing tests**

Test RFC one-click, ordinary List-Unsubscribe, HTML semantic links, and nearby plain-text URLs in deterministic order. Candidate discovery must make no network call. Test exact Agent candidate matching. Test `junk` without candidate -> Trash; with candidate -> audited unsubscribe -> Trash after terminal `done`, `already_unsubscribed`, or a defined non-retryable skipped outcome. A `needs_human` handoff must not Trash before continuation completes. Test deduplication prevents a second unsubscribe for the same message/entry.

- [ ] **Step 2: Remove subscription-category authorization**

Change action eligibility and planning so only `junk` may create `UNSUBSCRIBE`; delete all `EmailCategory.SUBSCRIPTION` conditions. Do not weaken the existing immutable ActionPlan binding, Consumer proposal, Audit review, origin policy, or effect receipt.

- [ ] **Step 3: Make extraction a reusable pre-Agent stage**

Return candidates with source, order, scheme, host, bounded context, and opaque reference. Supply these to the Classifier Agent. Persist only index/source/digest; re-extract the private full URL from authorized message material at execution.

- [ ] **Step 4: Chain Trash after unsubscribe terminal evidence**

Use a dependent deterministic action, not an Audit bypass. If unsubscribe enters `needs_human`, do not Trash until continuation reaches a terminal outcome. If it reaches a defined non-retryable skipped outcome, record observability and then Trash.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_email_unsubscribe.py tests/test_email_unsubscribe_audit.py tests/test_email_unsubscribe_continuation.py tests/test_email_pipeline.py tests/test_email_worker.py
git add app/email_unsubscribe.py app/email_pipeline.py app/email_worker.py tests/test_email_unsubscribe.py tests/test_email_pipeline.py tests/test_email_worker.py
git commit -m "feat(email): route junk through audited unsubscribe"
```

## Task 8: Add the cached Jina embedding client and description-aware two-head model

**Files:**

- Modify: `pyproject.toml`
- Add: `app/email_embedding_client.py`
- Add: `app/email_embedding_cache.py`
- Add: `app/email_embedding_classifier.py`
- Add: `tests/test_email_embedding_client.py`
- Add: `tests/test_email_embedding_cache.py`
- Add: `tests/test_email_embedding_classifier.py`

- [ ] **Step 1: Write transport, cache, scoring, and reload tests**

Test batches of at most 8 and a 2-second technical timeout. Test cache key equality only when normalized-input hash, input-schema version, embedding-model ID, and embedding revision all match. Test positive and exclusion description vectors change logits in opposite directions. Test learned shared `alpha` and `beta` remain in `[0, 2]`, initialize at `0.8` and `0.5`, and survive save/reload. Test category and important predictions are independent.

- [ ] **Step 2: Add the production embedding protocol**

Implement an injectable client that POSTs an OpenAI-compatible request:

```json
{"model":"jinaai/jina-embeddings-v5-text-small","input":["normalized email model text"]}
```

to `CEO_EMAIL_EMBEDDING_URL`, optionally using the secret referenced by `CEO_EMAIL_EMBEDDING_API_KEY`. Validate response ordering and fixed vector dimension. Never log request text, embedding values, or credentials. Add only the minimal runtime HTTP/numeric dependencies needed by the implementation.

- [ ] **Step 3: Implement the cache**

Store float32 arrays and metadata atomically below the model registry root. Description vectors use description-version hashes; mail vectors use input hashes. Corrupt, dimension-mismatched, model-mismatched, or revision-mismatched entries are cache misses, never partial reads.

- [ ] **Step 4: Implement the model**

Use a small deterministic NumPy/scikit-learn MLP category head and a binary important head. Category logits are:

```python
logits = mlp_logits + alpha * positive_similarity - beta * exclusion_similarity
```

where positive similarity aggregates normalized core/include vectors and exclusion similarity aggregates exclude vectors. Fit `alpha`/`beta` only inside training folds with box constraints. Save enabled category order, both heads, thresholds, descriptions, vector dimension, input version, embedding ID/revision, and checksum in one immutable artifact.

- [ ] **Step 5: Benchmark the warmed component**

Record queue, HTTP/network, embedding, head, and total latency. Unit tests use a fake clock/transport; an opt-in live test uses GPU4. Cached head inference P95 must be below 100ms; warm GPU endpoint target is P95 below 500ms.

- [ ] **Step 6: Run tests and commit**

```bash
pytest -q tests/test_email_embedding_client.py tests/test_email_embedding_cache.py tests/test_email_embedding_classifier.py
git add pyproject.toml app/email_embedding_client.py app/email_embedding_cache.py app/email_embedding_classifier.py tests/test_email_embedding_client.py tests/test_email_embedding_cache.py tests/test_email_embedding_classifier.py
git commit -m "feat(email): add description-aware embedding classifier"
```

## Task 9: Replace periodic promotion with staged snapshot training and maturity evidence

**Files:**

- Modify: `app/email_classifier_retrain.py`
- Modify: `app/email_classifier_learning.py`
- Modify: `app/email_classifier_training.py`
- Modify: `app/email_classifier_shadow.py`
- Modify: `app/email_model_registry.py`
- Add: `app/email_description_optimizer.py`
- Modify: `app/email_store.py`
- Modify: `tests/test_email_classifier_retrain.py`
- Modify: `tests/test_email_classifier_learning.py`
- Modify: `tests/test_email_classifier_training.py`
- Modify: `tests/test_email_classifier_shadow.py`
- Modify: `tests/test_email_model_registry.py`
- Add: `tests/test_email_description_optimizer.py`
- Modify: `tests/test_email_store.py`

- [ ] **Step 1: Write failing trigger and gate tests**

Test triggers at 50 changed/final folder or important labels, manual request, description-version change, and first minimum-ready snapshot. Test 49 changes do not train. Test training never activates a candidate. Test per-category historical eligibility requires accepted precision >=0.95, accepted hits >=20, and independent groups >=10. Test all enabled categories plus important must pass for two consecutive compatible candidates before promotion.

Also test that repeated confusion clusters can request one Agent-generated description proposal containing complete `core/include/exclude` fields and cited sample IDs; the proposal cannot mutate active descriptions. It is evaluated by training a candidate against the same frozen split, and it becomes effective only with the model version whose evidence passes the normal gates.

- [ ] **Step 2: Change retrain state from feedback count to snapshot changes**

Persist last trained snapshot SHA, folder/important change watermark, description version, and active run ID. Default `minimum_new_examples` becomes 50. Remove idle/max-interval training as independent triggers; polling remains only to observe an already-started subprocess.

- [ ] **Step 3: Train only from Task 5 snapshots**

The subprocess loads frozen splits, cached Jina vectors, and descriptions, trains both heads, calibrates per-category and important acceptance thresholds inside training/validation data, and evaluates the untouched test set once. Persist precision, recall, F1, accepted hits, accepted precision, independent accepted groups, latency percentiles, parameters, dependency versions, hashes, and failure reason.

- [ ] **Step 4: Extend registry compatibility and consecutive-pass evidence**

Registry metadata must identify enabled-category ordered set, description version, input schema, embedding model/revision, head format, and parent. A candidate is compatible only when all match. Store explicit per-category historical eligibility and whole-model readiness. Promotion requires two consecutive compatible passing model IDs and no unresolved historical-systematic-error flag.

- [ ] **Step 5: Add evidence-bound description optimization**

Cluster false positives/negatives and user-corrected folder moves by category pair and normalized group. Only after a minimum of five independent conflicting groups, invoke a bounded description-optimizer Agent with the current definitions and redacted examples. Persist its proposal, cited sample IDs, source description version, and status (`proposed`, `evaluated`, `accepted`, or `rejected`). Never edit active config directly. Candidate training evaluates the proposal on the frozen validation/test protocol; accepting a candidate atomically binds its description version to that model version.

- [ ] **Step 6: Run tests and commit**

```bash
pytest -q tests/test_email_classifier_retrain.py tests/test_email_classifier_learning.py tests/test_email_classifier_training.py tests/test_email_classifier_shadow.py tests/test_email_model_registry.py tests/test_email_description_optimizer.py tests/test_email_store.py
git add app/email_classifier_retrain.py app/email_classifier_learning.py app/email_classifier_training.py app/email_classifier_shadow.py app/email_model_registry.py app/email_description_optimizer.py app/email_store.py tests/test_email_classifier_retrain.py tests/test_email_classifier_learning.py tests/test_email_classifier_training.py tests/test_email_classifier_shadow.py tests/test_email_model_registry.py tests/test_email_description_optimizer.py tests/test_email_store.py
git commit -m "feat(email): stage snapshot-trained classifier candidates"
```

## Task 10: Add gated historical classification and whole-model online promotion

**Files:**

- Add: `app/email_historical_classifier.py`
- Modify: `app/email_classifier_runtime.py`
- Modify: `app/email_classifier_scan.py`
- Modify: `app/email_worker.py`
- Add: `tests/test_email_historical_classifier.py`
- Modify: `tests/test_email_classifier_runtime.py`
- Modify: `tests/test_email_classifier_scan_model.py`
- Modify: `tests/test_email_worker.py`

- [ ] **Step 1: Write failing history and routing tests**

History tests: only read, unclassified Inbox/configured-source messages are candidates; top-1 must itself be historically eligible and above its threshold; an eligible second-place class cannot override an immature top-1; business-folder messages are untouched; move/flag actions retain read state.

Online tests: before full promotion, Agent is primary and model is not called. After promotion, accepted model output acts without Agent; rejection, timeout, embedding failure, model/version mismatch, or incomplete input calls Agent once. Model and Agent must never run in parallel. TF-IDF must never supply an online fallback result.

- [ ] **Step 2: Implement explicit runtime modes**

Use `agent_primary`, `shadow_history`, and `model_primary` states derived from verified registry metadata. Shadow-history runs only as a staged/manual job, not in the scan loop. Whole-model activation atomically switches new-mail routing to model-primary.

- [ ] **Step 3: Implement historical batches**

Use batches <=8 and cached vectors. Re-read current folder/unclassified state before planning and before executing each move. Record model ID, threshold, prediction, and action outcome as history, while current category remains provider-derived.

- [ ] **Step 4: Implement model-primary SLO fallback**

Microbatch for at most 50ms, request timeout after 2s, and capture P50/P95/P99 by stage. A warm successful path is SLO-compliant only when total P95 <500ms. Any technical failure rejects the model result and invokes Agent; it does not auto-classify from stale cache unless the exact current input cache key is valid.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_email_historical_classifier.py tests/test_email_classifier_runtime.py tests/test_email_classifier_scan_model.py tests/test_email_worker.py
git add app/email_historical_classifier.py app/email_classifier_runtime.py app/email_classifier_scan.py app/email_worker.py tests/test_email_historical_classifier.py tests/test_email_classifier_runtime.py tests/test_email_classifier_scan_model.py tests/test_email_worker.py
git commit -m "feat(email): gate historical and realtime model use"
```

## Task 11: Support bounded authentication continuation for unsubscribe

**Files:**

- Modify: `app/email_unsubscribe.py`
- Modify: `app/email_unsubscribe_continuation.py`
- Modify: `app/email_worker.py`
- Modify: `skills/ceo-mail-review/SKILL.md`
- Modify: `tests/test_email_unsubscribe.py`
- Modify: `tests/test_email_unsubscribe_continuation.py`
- Modify: `tests/test_email_worker.py`
- Modify: `tests/test_mail_review_skill.py`

- [ ] **Step 1: Write failing continuation tests**

Test isolated-profile existing session, multi-step form, connected-mail OTP matched by site/recipient/time window, and auto-passing normal challenge. Test that OTP values never appear in logs, durable steps, receipts, or training text. Test real CAPTCHA normal attempt then `needs_human`; SMS/TOTP/QR/password without configured capability also `needs_human`; user continuation resumes without replaying the audited prefix.

- [ ] **Step 2: Add typed authentication observations and controls**

Extend the existing append-only continuation with fixed kinds for `email_otp`, `captcha_handoff`, and `credential_handoff`. Preserve exact-origin policy and immutable action lineage. The Agent may read only the connected recipient mailbox and only messages inside a bounded challenge window whose sender/domain/context matches the current site.

- [ ] **Step 3: Keep CAPTCHA handling policy-bounded**

Permit ordinary interaction with a challenge already rendered in the isolated profile. Do not add CAPTCHA solver services, browser-fingerprint evasion, principal impersonation, or main Chrome cookie access. If the interaction does not pass, persist `needs_human` with a resumable browser/profile reference and no secret content.

- [ ] **Step 4: Update the Skill separately from classifier behavior**

Replace the obsolete rule that login/CAPTCHA are always skipped. State: Agent attempts ordinary supported interaction; connected-mail OTP may be retrieved automatically; unsupported password/MFA/CAPTCHA becomes `needs_human`; user completion resumes the audited continuation. Keep replies disabled and attachments metadata-only.

- [ ] **Step 5: Run tests and commit this explicitly scoped behavior change**

```bash
pytest -q tests/test_email_unsubscribe.py tests/test_email_unsubscribe_continuation.py tests/test_email_worker.py tests/test_mail_review_skill.py
git add app/email_unsubscribe.py app/email_unsubscribe_continuation.py app/email_worker.py skills/ceo-mail-review/SKILL.md tests/test_email_unsubscribe.py tests/test_email_unsubscribe_continuation.py tests/test_email_worker.py tests/test_mail_review_skill.py
git commit -m "feat(email): hand off unsubscribe authentication challenges"
```

## Task 12: Expose backend observability and verify the complete service

**Files:**

- Modify: `app/web_api/email.py`
- Modify: `app/email_store.py`
- Modify: `tests/test_email_web_api.py`
- Modify: `tests/test_email_learning.py`
- Add: `tests/test_email_folder_classifier_e2e.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

- [ ] **Step 1: Write failing API/e2e tests**

Test backend responses for current provider-derived category/unavailable state, category bindings, description version, training snapshot/version, sample/group counts, per-category historical eligibility, consecutive promotion evidence, active mode/model ID, timing percentiles, fallback counts, action/readback status, and unsubscribe continuation. Do not assert presentation/layout.

The e2e fake-provider sequence is: unread Inbox mail -> Agent certain legal+important -> exact Legal folder move -> new-locator flag -> folder truth legal; user moves it to Financing -> next snapshot label financing; 50 changes -> staged candidate; mature historical legal top-1 -> historical move; full two-version promotion -> accepted model bypasses Agent; embedding timeout -> Agent fallback; junk with candidate -> audited unsubscribe -> Trash.

- [ ] **Step 2: Add read-only observability APIs**

Extend `GET /api/console/email/learning` and classification detail responses. Add `GET /api/console/email/folder-bindings` and `GET /api/console/email/model-versions/{model_id}`. Never return full unsubscribe URLs, secrets, OTP, raw embeddings, full email body, or attachment bytes.

- [ ] **Step 3: Update architecture documentation**

Document folder truth, independent important, Agent-primary cold start, offline-only shadow use, historical per-category gate, whole-model promotion, model-primary Agent fallback, and junk/unsubscribe flow. State explicitly that no email replies are sent and that classification confirmation creates no generic task.

- [ ] **Step 4: Run static and complete test verification**

```bash
ruff check app tests
pytest -q
```

Expected: zero lint errors and the entire suite passes.

- [ ] **Step 5: Run opt-in live verification**

With the configured test mailbox and GPU4 embedding endpoint, run the live tests selected by:

```bash
pytest -q -m live tests/test_email_folder_classifier_e2e.py tests/test_email_embedding_client.py
```

Verify no reply/send call occurred; verify one controlled test message was moved and flagged with provider readback; verify folder truth changes after a manual provider move; verify cached P95 <100ms and warm GPU-path P95 <500ms. Use a designated reversible test message and restore its original folder/flag state afterward.

- [ ] **Step 6: Commit docs and verification coverage**

```bash
git add app/web_api/email.py app/email_store.py tests/test_email_web_api.py tests/test_email_learning.py tests/test_email_folder_classifier_e2e.py docs/architecture.md docs/runtime-mechanism.md
git commit -m "test(email): verify folder classifier lifecycle"
```

- [ ] **Step 7: Restart and verify the live service**

```bash
old_pid=$(launchctl print gui/$(id -u)/com.ceo-agent-service.main | awk '/pid =/{print $3; exit}')
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Confirm the new PID differs from `old_pid`. Query the service health and store for unresolved Email `failed` or `processing` backlog. Do not report completion while a new failure, stuck action, or unreconciled unsubscribe claim remains.

## Final acceptance checklist

- [ ] Initial categories are the approved nine; dynamic categories work; removed category keys cannot be recreated.
- [ ] Provider folder is the only current category truth; Spam/Trash are junk; Sent/Draft are excluded.
- [ ] Important is the approved provider/model union and junk suppresses it.
- [ ] Cold start calls Agent only for unread unclassified source mail and never marks it read.
- [ ] Shadow training is staged, snapshot-based, description-aware, and never real-time.
- [ ] Historical use and whole-model promotion enforce their distinct evidence gates.
- [ ] Active model and Agent are sequential primary/fallback paths, never parallel.
- [ ] Junk candidate discovery is deterministic; unsubscribe remains audited; replies remain disabled.
- [ ] Folder move, changed locator, flag, unsubscribe, and continuation are idempotent and read back.
- [ ] Full tests, live provider readback, latency evidence, launchd restart, and backlog checks are recorded.
