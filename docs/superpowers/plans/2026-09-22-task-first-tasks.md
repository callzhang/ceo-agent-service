# Task-first Tasks and CEO Attention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Project-first Tasks model with an evidence-backed Task-first semantic layer, make 需关注 the default CEO view, keep routine unrelated work out of that view, and preserve legacy records as traceable evidence rather than treating them as official projects.

**Architecture:** Add a bounded `business_*` domain beside runtime `reply_tasks` and operational `/attention`, then cut the Task Agent and Tasks console over to it. Immutable source signals feed candidate or formal business tasks; typed relations, clusters, canonical anchors, and official projects stay separate; persisted attention items are a recomputable projection with append-only lifecycle events. Existing `work_projects` and `work_todos` become read-only legacy evidence after an explicit, manifest-driven migration—there is no permanent dual-write path.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite, FastAPI, React 19, TypeScript, Vitest, Testing Library, Pytest.

---

## Delivery boundaries

- The product term **需关注** in Tasks is business attention. It must not reuse or change the existing operational `/attention` page or `app/web_api/attention.py`, which reports service/runtime problems.
- The new database namespace is `business_*` so it cannot be confused with `reply_tasks`, `workbench_tasks`, scheduled tasks, or the legacy `work_*` tables.
- The implementation is not a generic graph. It uses fixed task, evidence, relation, cluster, anchor, project, and attention tables with domain-specific invariants.
- An Agent may propose an identity match, relation, cluster, anchor match, project candidate, or attention candidate. Service code confirms only evidence-backed state transitions. Clustering never creates an official project.
- Business relevance is derived from at least one confirmed active business-anchor link. Free-form model confidence cannot set `business_relevance='relevant'` by itself.
- Formal-task promotion accepts exactly four evidence classes: explicit commitment, explicit assignment, external formal TODO, and explicit meeting action item. Suggestions and discussion remain candidates.
- Reading or acknowledging a business attention item never resolves it. Resolution requires a persisted resolution signal and an append-only attention event.
- New behavior must not be deployed until the old Project-first Task Agent decision path is removed. Intermediate commits may be incomplete inside an isolated worktree, but the running service must not dual-write.
- Production data migration is a separate irreversible operation. This plan implements and tests `plan` and bounded `apply` commands, but execution stops after a dry run until Derek explicitly approves applying a verified manifest to the live database.

## Execution preflight

- [x] Create an isolated worktree with `superpowers:using-git-worktrees`; do not implement this multi-file cutover in the shared checkout.
- [x] Read `/Users/derek/.agents/AGENTS.md`, the repository `AGENTS.md`, `docs/agent-claims.md`, the approved spec, `docs/architecture.md`, and `docs/runtime-mechanism.md` before editing.
- [x] Re-read `docs/agent-claims.md` before every task. Claim the exact files for that task, wait or hand off when another owner has a conflicting claim, stage only owned hunks, and remove the claim after the task commit.
- [x] Establish a baseline in the isolated worktree:

```bash
.venv/bin/pytest -q tests/test_task_models.py tests/test_task_store.py tests/test_task_agent.py tests/test_console_web_api.py tests/test_web_api_task_sort.py
npm --prefix frontend test -- --run src/pages/TasksPage.test.tsx src/pages/TaskDetailPage.test.tsx src/api/console.test.ts
```

Expected: record the exact baseline pass/fail counts before changing code. Existing unrelated failures remain explicit and must not be converted into acceptance.

## Target data contract

These names are fixed for the implementation. Do not substitute a generic entity/edge store.

| Table | Authority |
| --- | --- |
| `business_task_signals` | Immutable source observations, uniquely deduped by source identity and content digest. |
| `business_tasks` | Candidate and formal Task truth, including separate lifecycle and commitment states. |
| `business_task_evidence` | Typed links from a Task to immutable source signals. |
| `business_task_events` | Append-only lifecycle, commitment, merge, owner, deadline, and relevance transitions. |
| `business_task_relations` | Confirmed or proposed typed links between distinct Tasks. |
| `business_work_clusters` / `business_work_cluster_tasks` | Group related Tasks without sharing completion, ownership, or deadlines. |
| `business_anchors` / `business_task_anchor_links` | Canonical company anchors and evidence-backed Task matches. |
| `business_projects` | Official Projects only; every row references a canonical `project` anchor. |
| `business_project_candidates` | Provisional project proposals based on a work cluster; never an official Project until confirmed. |
| `business_attention_items` / `business_attention_tasks` | Current and historical CEO attention projection and its underlying Tasks. |
| `business_attention_events` | Append-only category/status/history events for each attention item. |
| `business_legacy_links` | Exact provenance from a semantic object to a legacy `work_projects`, `work_todos`, or `work_updates` row. |

The canonical state values are:

```python
class BusinessTaskStage(StrEnum):
    CANDIDATE = "candidate"
    FORMAL = "formal"

class BusinessTaskStatus(StrEnum):
    OPEN = "open"
    WAITING = "waiting"
    DONE = "done"
    CANCELLED = "cancelled"
    MERGED = "merged"

class CommitmentStatus(StrEnum):
    NONE = "none"
    ASSIGNED_UNACCEPTED = "assigned_unaccepted"
    ACCEPTED = "accepted"
    DISPUTED = "disputed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class FormalTaskBasis(StrEnum):
    EXPLICIT_COMMITMENT = "explicit_commitment"
    EXPLICIT_ASSIGNMENT = "explicit_assignment"
    EXTERNAL_TODO = "external_todo"
    MEETING_ACTION_ITEM = "meeting_action_item"

class BusinessRelevance(StrEnum):
    UNKNOWN = "unknown"
    NOT_RELEVANT = "not_relevant"
    RELEVANT = "relevant"

class AttentionCategory(StrEnum):
    FYI = "fyi"
    WATCH = "watch"
    DECISION = "decision"
    PUSH = "push"
```

## Task 1: Add semantic domain models and durable schema

**Files:**
- Create: `app/task_semantic_models.py`
- Modify: `app/store.py`
- Create: `tests/test_task_semantic_store.py`
- Modify: `tests/test_store.py`

- [x] **Step 1: Write failing schema and model tests**

Add tests proving a fresh store contains every target table and index, a Task can exist without a Project, a formal assignment can be `assigned_unaccepted`, and invalid enum combinations are rejected.

```python
def test_business_task_can_exist_without_project(tmp_path):
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    signal_id = store.create_business_task_signal(
        source_type="reply_attempt",
        source_ref="message:42",
        source_time="2026-09-22T10:00:00Z",
        evidence_text="王明，周五前提交美国客户报价第一版。",
        context_json="{}",
        dedupe_key="reply_attempt:message:42",
    )
    task_id = store.create_business_task(
        title="提交美国客户报价第一版",
        stage="formal",
        status="open",
        formal_basis="explicit_assignment",
        commitment_status="assigned_unaccepted",
        owner_name="王明",
    )
    store.link_business_task_evidence(
        task_id=task_id,
        signal_id=signal_id,
        evidence_role="assignment",
    )

    task = store.get_business_task(task_id)
    assert task is not None
    assert task.stage is BusinessTaskStage.FORMAL
    assert task.commitment_status is CommitmentStatus.ASSIGNED_UNACCEPTED
    assert store.list_business_task_project_links(task_id=task_id) == []
```

- [x] **Step 2: Run the new test and confirm the expected failure**

Run: `.venv/bin/pytest -q tests/test_task_semantic_store.py -x`

Expected: FAIL because `task_semantic_models` and the store methods/tables do not exist.

- [x] **Step 3: Implement strict Pydantic records and validators**

Create `app/task_semantic_models.py` with the enums above and frozen record models for signals, tasks, evidence, events, relations, clusters, anchors, projects, project candidates, attention items, attention events, and legacy links. Add validators with these exact rules:

```python
class BusinessTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    title: str
    description: str = ""
    stage: BusinessTaskStage
    status: BusinessTaskStatus
    formal_basis: FormalTaskBasis | None = None
    commitment_status: CommitmentStatus = CommitmentStatus.NONE
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence_json: str = "{}"
    deadline_at: str = ""
    business_relevance: BusinessRelevance = BusinessRelevance.UNKNOWN
    missing_evidence_json: str = "[]"
    merged_into_task_id: int | None = None
    created_at: str
    updated_at: str
    last_activity_at: str

    @model_validator(mode="after")
    def validate_state(self) -> "BusinessTask":
        if self.stage is BusinessTaskStage.FORMAL and self.formal_basis is None:
            raise ValueError("formal task requires formal_basis")
        if self.stage is BusinessTaskStage.CANDIDATE and self.formal_basis is not None:
            raise ValueError("candidate cannot carry formal_basis")
        if self.status is BusinessTaskStatus.MERGED and not self.merged_into_task_id:
            raise ValueError("merged task requires merged_into_task_id")
        if self.status is not BusinessTaskStatus.MERGED and self.merged_into_task_id:
            raise ValueError("only a merged task may carry merged_into_task_id")
        return self
```

- [x] **Step 4: Add the schema in `AutoReplyStore._initialize`**

Use SQLite `CHECK` constraints matching the enums, foreign keys for every semantic reference, unique keys for signal dedupe and membership tables, and indexes for the actual list paths:

```sql
create table if not exists business_task_signals (
    id integer primary key autoincrement,
    source_type text not null,
    source_ref text not null,
    source_time text not null default '',
    conversation_id text not null default '',
    conversation_title text not null default '',
    author_user_id text not null default '',
    author_name text not null default '',
    evidence_text text not null,
    context_json text not null default '{}',
    dedupe_key text not null unique,
    created_at text not null default current_timestamp
);

create table if not exists business_tasks (
    id integer primary key autoincrement,
    title text not null,
    description text not null default '',
    stage text not null check(stage in ('candidate', 'formal')),
    status text not null default 'open'
        check(status in ('open', 'waiting', 'done', 'cancelled', 'merged')),
    formal_basis text check(formal_basis is null or formal_basis in (
        'explicit_commitment', 'explicit_assignment',
        'external_todo', 'meeting_action_item'
    )),
    commitment_status text not null default 'none' check(commitment_status in (
        'none', 'assigned_unaccepted', 'accepted', 'disputed',
        'completed', 'cancelled'
    )),
    owner_user_id text not null default '',
    owner_name text not null default '',
    owner_evidence_json text not null default '{}',
    deadline_at text not null default '',
    business_relevance text not null default 'unknown'
        check(business_relevance in ('unknown', 'not_relevant', 'relevant')),
    missing_evidence_json text not null default '[]',
    merged_into_task_id integer,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    last_activity_at text not null default current_timestamp,
    foreign key(merged_into_task_id) references business_tasks(id),
    check((status='merged') = (merged_into_task_id is not null))
);

create table if not exists business_task_evidence (
    task_id integer not null,
    signal_id integer not null,
    evidence_role text not null check(evidence_role in (
        'discovery', 'commitment', 'assignment', 'acceptance', 'completion',
        'correction', 'merge_identity', 'relevance', 'resolution'
    )),
    created_at text not null default current_timestamp,
    primary key(task_id, signal_id, evidence_role),
    foreign key(task_id) references business_tasks(id),
    foreign key(signal_id) references business_task_signals(id)
);
```

Add the remaining tables from **Target data contract** with equally strict checks. Add their names and list indexes to `STORE_SCHEMA_REQUIRED_TABLES` and `STORE_SCHEMA_REQUIRED_INDEXES`; add critical columns to `STORE_SCHEMA_REQUIRED_COLUMNS`; bump `STORE_SCHEMA_VERSION` once for the complete semantic schema.

- [x] **Step 5: Add row parsers and minimum CRUD methods**

Implement only create/get/list primitives in this task. All multi-row transitions wait for Task 2.

Add these exact public methods: `create_business_task_signal`, `get_business_task_signal`, `create_business_task`, `get_business_task`, `list_business_tasks`, `link_business_task_evidence`, and `list_business_task_evidence`. `list_business_tasks` accepts optional `stages`, `statuses`, and `relevance` filters plus `limit=100` and `offset=0`; evidence methods address a Task by `task_id` and a signal by `signal_id`.

All create/update methods must use explicit allowed-column sets; no arbitrary SQL field passthrough.

- [x] **Step 6: Run focused tests**

Run: `.venv/bin/pytest -q tests/test_task_semantic_store.py tests/test_store.py -x`

Expected: PASS, including schema-manifest initialization on both a fresh database and an existing pre-version database fixture.

- [x] **Step 7: Commit**

```bash
git add app/task_semantic_models.py app/store.py tests/test_task_semantic_store.py tests/test_store.py
git commit -m "feat(tasks): add task-first semantic storage"
```

## Task 2: Make semantic mutations atomic and append-only

**Files:**
- Create: `app/task_semantic_service.py`
- Modify: `app/store.py`
- Modify: `tests/test_task_semantic_store.py`
- Create: `tests/test_task_semantic_service.py`

- [x] **Step 1: Write failing transaction and history tests**

Cover these cases:

1. signal + task + evidence + initial event commit together;
2. duplicate signal returns the original signal ID and does not duplicate evidence;
3. acceptance updates the existing assigned Task instead of creating another Task;
4. a failed event insert rolls back the Task update;
5. merge marks only the source Task as `merged`, preserves both Tasks, re-links all evidence to the target, and writes `merge_identity` evidence plus events for both IDs.

```python
def test_acceptance_changes_same_assigned_task(tmp_path):
    service = semantic_service(tmp_path)
    task_id = service.record_formal_task(assignment_input()).task_id

    result = service.apply_acceptance(acceptance_input(task_id=task_id))

    assert result.task_id == task_id
    assert result.created is False
    assert service.store.get_business_task(task_id).commitment_status.value == "accepted"
    assert [event.event_type for event in service.store.list_business_task_events(task_id)] == [
        "created", "commitment_changed"
    ]
```

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest -q tests/test_task_semantic_service.py -x`

Expected: FAIL because atomic service transitions do not exist.

- [x] **Step 3: Add explicit store transactions**

Implement transaction-scoped methods; do not call multiple public connection-opening methods from one transition.

```python
@contextmanager
def business_task_transaction(self) -> Iterator[sqlite3.Connection]:
    with self._immediate_write_transaction() as db:
        yield db

def append_business_task_event(
    self, *, task_id: int, event_type: str, signal_id: int | None,
    before_json: str, after_json: str, reason: str, _db: sqlite3.Connection,
) -> int:
    cursor = _db.execute(
        """
        insert into business_task_events
            (task_id, event_type, signal_id, before_json, after_json, reason)
        values (?, ?, ?, ?, ?, ?)
        """,
        (task_id, event_type, signal_id, before_json, after_json, reason),
    )
    return int(cursor.lastrowid)
```

The event types are domain state names, not free-form UI labels: `created`, `promoted`, `commitment_changed`, `owner_changed`, `deadline_changed`, `status_changed`, `relevance_changed`, and `merged`.

- [x] **Step 4: Implement `TaskSemanticService`**

Expose one method per state transition and keep field validation in service code:

`TaskSemanticService` exposes six command methods with typed request objects and `TaskMutationResult` responses: `record_candidate(RecordCandidate)`, `record_formal_task(RecordFormalTask)`, `promote_candidate(PromoteCandidate)`, `apply_acceptance(ApplyAcceptance)`, `update_task(UpdateBusinessTask)`, and `merge_same_deliverable(MergeBusinessTasks)`.

`record_formal_task` must reject a missing `formal_basis`. `merge_same_deliverable` must reject self-merge, merge chains, and a target already marked merged. It may move evidence links but never delete the source Task, signal, or events.

- [x] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest -q tests/test_task_semantic_store.py tests/test_task_semantic_service.py`

Expected: PASS.

```bash
git add app/task_semantic_service.py app/store.py tests/test_task_semantic_store.py tests/test_task_semantic_service.py
git commit -m "feat(tasks): persist atomic task evidence transitions"
```

## Task 3: Encode promotion, identity, and relation rules

**Files:**
- Create: `app/task_semantic_rules.py`
- Modify: `app/task_semantic_service.py`
- Create: `tests/test_task_semantic_rules.py`
- Modify: `tests/test_task_semantic_service.py`

- [x] **Step 1: Write the decision-table tests**

Use structured evidence fields in fixtures—never keyword matching against Chinese text.

```python
@pytest.mark.parametrize(
    ("basis", "expected_stage", "expected_commitment"),
    [
        ("explicit_commitment", "formal", "accepted"),
        ("explicit_assignment", "formal", "assigned_unaccepted"),
        ("external_todo", "formal", "accepted"),
        ("meeting_action_item", "formal", "assigned_unaccepted"),
        (None, "candidate", "none"),
    ],
)
def test_promotion_table(basis, expected_stage, expected_commitment):
    resolution = resolve_formality(
        FormalityEvidence(
            basis=None if basis is None else FormalTaskBasis(basis),
            assigner_is_authorized=True,
            deliverable_is_explicit=True,
            owner_is_explicit=True,
        )
    )
    assert resolution.stage.value == expected_stage
    assert resolution.commitment_status.value == expected_commitment
```

Add separate tests asserting: a shared goal returns `link` rather than `merge`; the same external TODO ID returns `merge`; uncertain identity returns `link`; and completing one cluster member leaves every sibling status unchanged.

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest -q tests/test_task_semantic_rules.py -x`

Expected: FAIL because the rules module is absent.

- [x] **Step 3: Implement pure rule inputs and outputs**

```python
@dataclass(frozen=True)
class FormalityEvidence:
    basis: FormalTaskBasis | None
    assigner_is_authorized: bool
    deliverable_is_explicit: bool
    owner_is_explicit: bool

@dataclass(frozen=True)
class IdentityEvidence:
    same_external_task_id: bool = False
    explicit_source_reference: bool = False
    same_deliverable: bool = False
    same_owner: bool = False
    same_context: bool = False
    compatible_time_window: bool = False

def resolve_identity(evidence: IdentityEvidence) -> Literal["merge", "link", "separate"]:
    if evidence.same_external_task_id or evidence.explicit_source_reference:
        return "merge"
    if (
        evidence.same_deliverable
        and evidence.same_owner
        and evidence.same_context
        and evidence.compatible_time_window
    ):
        return "merge"
    if evidence.same_deliverable or evidence.same_context:
        return "link"
    return "separate"
```

Implement `resolve_formality` as an exhaustive `FormalTaskBasis` match returning `candidate/none` when the basis is absent, `formal/accepted` for explicit commitment and external TODO, and `formal/assigned_unaccepted` for authorized explicit assignment or an explicit meeting action item. Reject an unauthorized assignment or an implicit deliverable instead of promoting it.

Rules:

- `external_todo` is formal because the external system supplies authority.
- `explicit_assignment` is formal only when the assigner is authorized and the deliverable is explicit; acceptance remains separate.
- `meeting_action_item` is formal only when the action item is explicit; owner may remain unresolved, but missing owner is recorded as missing evidence.
- merge requires the same external task ID, an explicit source reference, or the full conjunction of same deliverable + owner + context + compatible time window.
- every weaker match returns `link` or `separate`; it never merges on confidence score alone.

- [x] **Step 4: Wire rules into semantic service and run tests**

Run: `.venv/bin/pytest -q tests/test_task_semantic_rules.py tests/test_task_semantic_service.py`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add app/task_semantic_rules.py app/task_semantic_service.py tests/test_task_semantic_rules.py tests/test_task_semantic_service.py
git commit -m "feat(tasks): enforce task promotion and identity rules"
```

## Task 4: Add clusters, canonical anchors, official Projects, and relevance

**Files:**
- Create: `app/task_business_resolution.py`
- Modify: `app/store.py`
- Modify: `app/task_semantic_service.py`
- Create: `tests/test_task_business_resolution.py`
- Modify: `tests/test_task_semantic_store.py`

- [x] **Step 1: Write failing boundary tests**

Prove all of the following:

- related tasks can join one cluster and retain independent owner/deadline/status;
- a cluster may create one `business_project_candidates` row but no `business_projects` row;
- only a registry import or explicit confirmation creates an official Project;
- a Task becomes `relevant` only through a confirmed active anchor link;
- a proposed anchor link leaves relevance `unknown`;
- a confirmed `not_relevant` decision keeps the Task searchable but excludes it from default projection input.

```python
def test_cluster_cannot_create_official_project(tmp_path):
    resolver = business_resolver(tmp_path)
    cluster_id = resolver.create_cluster(title="美国客户成交", task_ids=[11, 12, 13])
    candidate_id = resolver.propose_project(cluster_id=cluster_id, reason="持续多任务目标")

    assert resolver.store.get_business_project_candidate(candidate_id) is not None
    assert resolver.store.list_business_projects() == []
```

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest -q tests/test_task_business_resolution.py -x`

Expected: FAIL because the resolver is absent.

- [x] **Step 3: Implement `BusinessResolutionService`**

`BusinessResolutionService` exposes exact methods for `create_cluster`, `add_relation`, `register_anchor`, `register_official_project`, `propose_anchor_match`, `confirm_anchor_match`, `propose_project`, and `confirm_project_candidate`. Each creation method returns the persisted row ID. Confirmation methods require a persisted evidence signal or canonical registry source; none accepts model confidence as authority.

`confirm_anchor_match` recalculates relevance from confirmed active links inside the same transaction and appends a `relevance_changed` event when the derived value changes. No text keyword, regex, or confidence threshold is permitted as a hard anchor.

- [x] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/test_task_business_resolution.py tests/test_task_semantic_service.py tests/test_task_semantic_store.py`

Expected: PASS.

```bash
git add app/task_business_resolution.py app/store.py app/task_semantic_service.py tests/test_task_business_resolution.py tests/test_task_semantic_store.py
git commit -m "feat(tasks): separate clusters projects and business anchors"
```

## Task 5: Build the persisted CEO attention projection

**Files:**
- Create: `app/task_attention_projection.py`
- Modify: `app/store.py`
- Create: `tests/test_task_attention_projection.py`

- [x] **Step 1: Write failing projection tests**

Cover creation, aggregation, category transition, resolution, and idempotent recomputation.

```python
def test_reading_does_not_resolve_business_attention(tmp_path):
    projection = attention_projection(tmp_path)
    item_id = projection.upsert(material_decision_input())

    projection.record_viewed(item_id=item_id, viewed_at="2026-09-22T12:00:00Z")

    item = projection.store.get_business_attention_item(item_id)
    assert item.status.value == "active"
    assert [event.event_type for event in projection.store.list_business_attention_events(item_id)] == [
        "opened"
    ]

```

Add separate tests that assert: category transition preserves the attention ID; one item can link multiple independently open Tasks; a non-relevant Task raises an eligibility error; resolution without a signal raises `ValueError`; and two recomputations produce identical item, link, and event counts.

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest -q tests/test_task_attention_projection.py -x`

Expected: FAIL because the projection does not exist.

- [x] **Step 3: Implement typed projection commands**

```python
@dataclass(frozen=True)
class AttentionProposal:
    stable_key: str
    category: AttentionCategory
    title: str
    business_area: str
    why_attention: str
    current_state: str
    ceo_action: str
    anchor_id: int
    task_ids: tuple[int, ...]
    evidence_signal_id: int

class BusinessAttentionProjection:
    def __init__(self, store: AutoReplyStore):
        self.store = store
```

Implement `upsert(AttentionProposal) -> int`, `resolve(item_id, resolution_signal_id, reason) -> None`, and `recompute_for_tasks(task_ids) -> tuple[int, ...]` on that class. `upsert` uses `stable_key` for identity; `resolve` requires the evidence signal; recomputation skips writing an event when the semantic snapshot is unchanged.

Eligibility checks inside `upsert`:

1. every linked Task exists and is not merged into another unresolved identity;
2. at least one linked Task is `business_relevance='relevant'`;
3. `anchor_id` is linked to at least one linked Task with `status='confirmed'`;
4. why/current/action/title are non-empty;
5. `ceo_action` may be `当前无需处理`, but the category still has to describe a material information, risk, decision, or push state;
6. the evidence signal exists and is linked to at least one underlying Task.

`record_viewed` may update a separate non-authoritative view timestamp if needed by the UI, but it must not append a resolution event or change `status`.

- [x] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/test_task_attention_projection.py tests/test_task_business_resolution.py`

Expected: PASS.

```bash
git add app/task_attention_projection.py app/store.py tests/test_task_attention_projection.py
git commit -m "feat(tasks): derive traceable CEO attention items"
```

## Task 6: Replace the Task Agent’s Project-first decision contract

**Files:**
- Modify: `app/task_models.py`
- Modify: `app/task_agent.py`
- Modify: `app/task_retrieval.py`
- Modify: `tests/test_task_models.py`
- Modify: `tests/test_task_agent.py`
- Modify: `tests/test_task_retrieval.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

**Approved Task 7 semantic limits (2026-09-24):**

- Mirror only a formal Task with an explicit source-backed owner, `accepted`
  commitment, and a parseable `committed_deadline_at` fact. Do not derive a
  DingTalk TODO due date from requested, external, estimated, or next-check
  dates.
- Create a Task follow-up only when its linked source signal carries the exact
  conversation/recipient context and the Task has a parseable `next_check_at`.
  Preserve that source target exactly; do not infer a target or schedule from a
  deadline. The follow-up question may summarize the current Task state but
  must not create a new Task.
- Modify: `app/task_semantic_models.py`, `app/store.py`, `app/task_semantic_service.py`, and `app/task_semantic_rules.py` for typed date evidence and evidence-derived transitions
- Modify: relevant semantic storage/service/rules tests
- Modify: `/Users/derek/.agents/skills/ceo-work-tracking/SKILL.md`, the Skill loaded by the Task Agent

### Approved implementation amendments

The following requirements refine the original Task 6 sketch and override any
conflicting field or behavior below:

- A source WorkItem may produce zero or multiple `task_decisions`. Each item
  decision must cite an exact source excerpt/reference; the Agent extracts
  tasks from supplied context but cannot invent a task or assignee. Assignees
  must be explicitly identified by source text or authoritative metadata. If
  not, keep candidate/missing evidence rather than guessing.
- Commitment state is calculated by semantic rules from linked evidence; the
  model cannot set `commitment_status`. Assignment evidence can create a formal
  `assigned_unaccepted` Task. External TODO existence proves a formal record,
  not owner acceptance. Acceptance requires explicit owner acceptance evidence
  uniquely linked to an existing Task; “收到” alone is acknowledgement, not
  accepted scope or deadline. Preserve actor/origin for assignment and
  acceptance, and never treat Agent-authored output as the owner's evidence.
- A formal Task must have a source-backed, explicitly identified owner/team. If
  a meeting action item or other deliverable lacks an owner, retain it as a
  candidate or unmatched evidence; do not write a formal assignment with an
  inferred or blank owner.
- Use an explicit transition discriminator for `promote_candidate`,
  `apply_acceptance`, `update_fields`, and `merge_identity`; each calls its
  dedicated semantic-service/rule path. Generic updates cannot change
  commitment status. A merge supplies source/target IDs plus structured
  identity evidence. Project-candidate proposals require a supplied existing
  cluster ID. Existing formal Tasks and their evidence, relations, and anchor
  links must be retrieved for acceptance/update matching; semantic rank is
  context only and never authorizes a transition.
- Dates must not be overloaded. The system owns `created_at`; source-backed
  dates are typed as `assigned_at`, `requested_deadline_at`,
  `external_deadline_at`, `committed_deadline_at`, `estimated_deadline_at`, or
  `next_check_at`, each with source/actor provenance. Only an explicit owner
  commitment/acceptance can establish `committed_deadline_at`; estimated and
  check dates never mean owner default. No date is required to retain a
  Business Task. Task 7's DingTalk TODO mirror continues to require a real,
  parseable due date.
- CEO attention requires a confirmed relevant anchor and a concrete material
  trigger: threatened accepted commitment, material change, unresolved
  material assignment/commitment dispute, CEO decision or push, required Gate,
  or meaningful risk escalation. Relevance, acceptance, normal progress, or
  date proximity alone is insufficient; FYI requires materiality and a new
  meaningful change.
- When one source contains multiple action items, each Task signal needs a
  stable item-level dedupe identity while preserving the same original source
  reference. Reusing one source-level dedupe key for every action would cause
  replay handling to collapse distinct Tasks.
- Update the loaded work-tracking Skill in the same behavior change so it no
  longer directs Project/TODO-first routing or discards all routine low-impact
  work. Retained routine work is not CEO attention.
- Do not deploy Task 6 alone. Task 7 must preserve the separate date gate for
  DingTalk TODO creation, and the Task Agent cutover ships only with downstream
  workflow changes complete.
- TODO completion and follow-up repair are lifecycle operations on the same
  `TaskAgentDecision` envelope, not Project-first writes or a second Agent
  result contract. Their typed `todo_changes` and `follow_up_changes` fields
  are top-level siblings of `task_decisions`; source-specific Work Items may
  add zero or more applicable operations only for records linked in that
  Work Item. A successful operation requires current evidence tied to the
  bounded `search_trace` and observed tool receipts; insufficient completion
  evidence records the check without closing the TODO. Task changes, local
  TODO completion, follow-up updates, candidate bookkeeping, input status and
  run status commit atomically; configured external TODO completion continues
  through the existing outbox. The shared consumer may select context and
  service-side application by source type, but it must not start a separate
  Completion Agent or use a second decision schema.

- [x] **Step 1: Replace old decision fixtures with failing Task-first fixtures**

The new result contract is an envelope containing `task_decisions`, one entry
per source-backed action. Each entry's `action` and `transition` are separate:
`action` is `skip|record_candidate|create_task|update_task`; `transition` is
`none|promote_candidate|apply_acceptance|update_fields|merge_identity`. Each
task decision carries a source excerpt/reference. It has no direct
`commitment_status` or generic `deadline_at` output; dates use typed evidence.

```python
class TaskAgentDecision(StrictTaskModel):
    task_decisions: list[TaskDecision]

class TaskDecision(StrictTaskModel):
    action: Literal["skip", "record_candidate", "create_task", "update_task"]
    transition: Literal["none", "promote_candidate", "apply_acceptance", "update_fields", "merge_identity"]
    skip_reason: str = ""
    task_id: int | None = None
    target_task_id: int | None = None
    source_excerpt: str = ""
    source_ref: str = ""
    title: str = ""
    description: str = ""
    formal_basis: FormalTaskBasis | None = None
    owner_user_id: str = ""
    owner_name: str = ""
    owner_evidence: dict[str, Any] = Field(default_factory=dict)
    date_evidence: list[TaskDateEvidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    identity_proposal: TaskIdentityProposal | None = None
    relation_proposals: list[TaskRelationProposal] = Field(default_factory=list)
    cluster_proposal: TaskClusterProposal | None = None
    anchor_match_proposals: list[TaskAnchorMatchProposal] = Field(default_factory=list)
    project_candidate_proposal: ProjectCandidateProposal | None = None
    attention_proposal: TaskAttentionProposal | None = None
    update_summary: str = ""
    memory_recall_used: bool = False
    risk: DecisionRisk = DecisionRisk.LOW
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rule_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    information_completeness: float = Field(default=1.0, ge=0.0, le=1.0)
```

Delete test expectations for `create_project`, `update_project`, `TaskProjectPatch`, and `project_name`. Add tests proving:

- “美国报价可以研究一下” fixture becomes `record_candidate`;
- one source containing several action items produces a separate decision per
  item, preserving its source excerpt, explicit assignee, and typed dates;
- explicit assignment creates a formal Task without any Project field;
- external TODO existence or “收到” alone cannot set owner acceptance;
- acceptance targets exactly one existing task ID and uses the dedicated
  acceptance transition;
- missing/ambiguous assignee stays unresolved instead of being invented;
- creation, assignment, requested/external/committed DDL, estimate, and
  `next_check_at` remain distinct; only owner-accepted DDL is a committed date;
- anchor/project proposals cannot directly create an official Project;
- small non-business work may still create a Task but cannot emit an eligible attention proposal.
- relevance, acceptance, and date proximity alone cannot emit attention without
  a concrete material trigger.

- [x] **Step 2: Run the focused tests and confirm failure**

Run: `.venv/bin/pytest -q tests/test_task_models.py tests/test_task_agent.py tests/test_task_retrieval.py -x`

Expected: FAIL on the old Project-first schema and prompt.

- [x] **Step 3: Change retrieval from Projects to semantic context**

Replace `ProjectCandidate` retrieval with bounded candidate and formal Task,
evidence, relation, anchor-link, cluster, anchor, and official Project retrieval.
Use public Store APIs with complete pagination, not private SQL or a fixed recent
window. The prompt context must label each collection so semantic similarity
cannot be mistaken for authority.

```python
@dataclass(frozen=True)
class TaskSemanticContext:
    task_candidates: tuple[BusinessTask, ...]
    formal_tasks: tuple[BusinessTask, ...]
    task_evidence: tuple[BusinessTaskEvidence, ...]
    task_relations: tuple[BusinessTaskRelation, ...]
    task_anchor_links: tuple[BusinessTaskAnchorLink, ...]
    clusters: tuple[BusinessWorkCluster, ...]
    anchors: tuple[BusinessAnchor, ...]
    official_projects: tuple[BusinessProject, ...]

def retrieve_task_semantic_context(
    store: AutoReplyStore, work_item: WorkItem, *, limit_per_kind: int = 20
) -> TaskSemanticContext:
    return TaskSemanticContext(
        task_candidates=tuple(retrieve_business_task_candidates(store, work_item, limit=limit_per_kind)),
        clusters=tuple(retrieve_business_clusters(store, work_item, limit=limit_per_kind)),
        anchors=tuple(retrieve_business_anchors(store, work_item, limit=limit_per_kind)),
        official_projects=tuple(store.list_business_projects(limit=limit_per_kind)),
    )
```

Do not use a fixed recent-500 window. Retrieval may rank candidates, but the prompt must state that rank is context, not merge/project confirmation.

- [x] **Step 4: Rewrite the Task Agent prompt and validator**

The prompt must explicitly state:

- first decide candidate vs formal Task;
- extract zero or more tasks, each tied to an exact source excerpt/reference;
- never invent a task or assignee; use only source-backed owners;
- assignment creates `assigned_unaccepted`; evidence rules derive acceptance and update the same Task;
- same deliverable may merge; related deliverables may cluster or link only;
- official Projects come only from the supplied official registry;
- Agent anchor matches and project candidates are proposals;
- no confirmed anchor means no business relevance and no CEO attention;
- routine low-impact work is retained outside the default view;
- `skip` means no plausible retained task signal, not “no project found”.
- no committed DDL without explicit owner acceptance; estimates/check dates are not DDL;
- attention requires a material trigger, not only business relevance or date proximity.

Update `_validate_task_agent_decision` to enforce required fields by action and to invoke the pure rules from Task 3. Remove `_apply_project`, protected Project-patch validation, and Project creation/update branches.

- [x] **Step 5: Apply the decision through semantic services in one transaction**

`apply_task_agent_decision` returns the resulting Task IDs, records the Task
Agent run as before, and persists signals, evidence, typed dates, Task/event
transitions, relations, and proposals as one semantic transaction. It then
invokes attention recomputation from committed semantic truth. Projection
failure is reportable/rebuildable and must not roll back valid Task/evidence
state or appear as fabricated empty-success attention. It must not write
`work_projects`, `work_todos`, or `work_updates`.

- [x] **Step 6: Update runtime truth documents in the same behavior commit**

In `docs/architecture.md` and `docs/runtime-mechanism.md`, replace every current
statement that the Task Agent creates/updates Projects and TODOs as its primary
truth. Document multi-task extraction, explicit owner evidence, dedicated
transitions, evidence-derived commitment state, typed dates, attention triggers,
proposal/confirmation boundary, and absence of dual-write. Update the loaded
`ceo-work-tracking` Skill in the same change.

- [x] **Step 7: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_task_models.py tests/test_task_agent.py tests/test_task_retrieval.py tests/test_task_semantic_rules.py tests/test_task_semantic_service.py
```

Expected: PASS with no `create_project` or `update_project` decision fixture
remaining in active Task Agent tests; all source action items are preserved;
direct commitment-status changes cannot bypass semantic evidence rules; typed
date and attention-trigger cases pass.

```bash
git add app/task_models.py app/task_agent.py app/task_retrieval.py tests/test_task_models.py tests/test_task_agent.py tests/test_task_retrieval.py docs/architecture.md docs/runtime-mechanism.md
git commit -m "refactor(tasks): make task agent task-first"
```

## Task 7: Re-key follow-up and DingTalk TODO execution to business Tasks

**Files:**
- Modify: `app/store.py`
- Modify: `app/follow_up.py`
- Modify: `app/todo_sync.py`
- Modify: `app/todo_completion.py`
- Modify: `app/task_progress.py`
- Modify: `app/task_lifecycle.py`
- Modify: `app/cli.py`
- Modify: `app/dispatcher/adapters.py`
- Modify: `app/task_completion_agent.py`
- Modify: `tests/test_follow_up.py`
- Modify: `tests/test_todo_sync.py`
- Modify: `tests/test_todo_completion.py`
- Modify: `tests/test_task_lifecycle.py`
- Modify: `tests/test_task_store.py`
- Modify: `tests/test_consumer_dispatcher.py`
- Modify: `tests/test_task_completion_agent.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

- [x] **Step 1: Write failing downstream tests against `business_task_id`**

Cover:

- an eligible standalone formal Task creates a DingTalk TODO without any Project;
- an assigned-unaccepted Task is retained but is not mirrored until the existing sync eligibility rules are satisfied;
- DingTalk completion closes the business Task and writes completion evidence/event;
- follow-up targets a business Task and completion of a sibling cluster Task does not close it;
- outbox idempotency keys use the business Task ID and preserve existing external receipt behavior.

- [x] **Step 2: Add replacement execution tables and migrate code references**

Create new tables rather than making new code depend on misleading legacy names:

```sql
create table if not exists business_task_dingtalk_links (
    id integer primary key autoincrement,
    business_task_id integer not null,
    dingtalk_task_id text not null default '',
    executor_user_id text not null default '',
    executor_name text not null default '',
    title_snapshot text not null default '',
    deadline_at_snapshot text not null default '',
    priority_snapshot text not null default '',
    status text not null check(status in ('creating','active','done','cancelled','failed')),
    last_dingtalk_done integer,
    last_dingtalk_payload_json text not null default '{}',
    last_pull_at text not null default '',
    last_push_at text not null default '',
    last_error text not null default '',
    retry_count integer not null default 0,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(business_task_id) references business_tasks(id)
);
```

Add `business_task_follow_ups` and `business_task_todo_sync_outbox` with the current follow-up/outbox receipt fields but a required `business_task_id`. Preserve `queued`, `running`, `completed`, `skipped`, `failed`, and `unknown` semantics; do not weaken receipt reconciliation.

- [x] **Step 3: Replace operational code paths**

Rename public functions around business Tasks:

Replace the public execution signatures with `maybe_create_dingtalk_todo(store, dws, *, business_task_id: int, now: str)`, `sync_completed_task_to_dingtalk(store, dws, *, business_task_id: int, evidence: dict[str, object], now: str)`, and `complete_business_task_from_external_todo(store, *, business_task_id: int, evidence: dict[str, object])`. Each loads the authoritative `BusinessTask`, rejects missing or merged Tasks, and writes the existing provider receipt/readback into the new Task-linked tables.

Reuse the existing external-effect receipt/readback rules. Change the internal record identity only; do not introduce new authorization, confirmation, retry, or safety gates.

- [x] **Step 4: Remove new-write access to legacy execution tables**

After callers move, delete the Task Agent and maintenance paths that create `work_todos`, `follow_up_drafts`, `work_todo_dingtalk_links`, or `task_todo_sync_outbox`. Keep their read methods only under the legacy-import/history boundary until Task 8 finishes.

- [x] **Step 5: Update docs, run tests, and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_follow_up.py tests/test_todo_sync.py tests/test_todo_completion.py tests/test_task_lifecycle.py tests/test_task_store.py tests/test_task_agent.py
```

Expected: PASS; no active test creates a Project merely to create or complete a Task.

Update `docs/architecture.md` and `docs/runtime-mechanism.md` in this commit with the new follow-up, outbox, receipt, and completion references.

```bash
git add app/store.py app/follow_up.py app/todo_sync.py app/todo_completion.py app/task_progress.py app/task_lifecycle.py app/cli.py tests/test_follow_up.py tests/test_todo_sync.py tests/test_todo_completion.py tests/test_task_lifecycle.py tests/test_task_store.py docs/architecture.md docs/runtime-mechanism.md
git commit -m "refactor(tasks): run follow-ups from business tasks"
```

## Task 8: Add manifest-driven legacy import and preserve ambiguous history

**Files:**
- Create: `app/task_semantic_import.py`
- Modify: `app/cli.py`
- Modify: `app/store.py`
- Create: `tests/test_task_semantic_import.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: Write failing import tests**

Cover exact provenance, ambiguity, idempotency, database fingerprint mismatch, and bounded apply.

```python
def test_ambiguous_legacy_project_stays_legacy_evidence(tmp_path):
    store = legacy_store_with_mixed_granularity(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path, limit=100)

    row = next(item for item in manifest.items if item.legacy_project_id == 7)
    assert row.disposition == "unresolved"
    assert row.semantic_task is None
    assert row.official_project is None

```

Add separate tests asserting: the second apply changes zero rows while every imported object keeps a `business_legacy_links` row; a changed fingerprint raises before the first write; and `--limit 1` applies only the first stable manifest item.

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest -q tests/test_task_semantic_import.py -x`

Expected: FAIL because import planner is absent.

- [x] **Step 3: Implement a versioned manifest**

```python
@dataclass(frozen=True)
class LegacyImportItem:
    legacy_project_id: int
    legacy_todo_ids: tuple[int, ...]
    disposition: Literal["formal_task", "official_project_match", "history_only"]
    evidence_refs: tuple[str, ...]
    semantic_task: dict[str, object] | None
    official_project_registry_key: str
    reason: str

@dataclass(frozen=True)
class TaskSemanticImportManifest:
    version: int
    manifest_id: str
    database_fingerprint: str
    created_at: str
    items: tuple[LegacyImportItem, ...]
```

The planner may import a legacy TODO as a formal Task only when its stored source evidence proves one of the four formal bases. A legacy Project becomes official only when it matches a pre-registered official Project key. Evidence-insufficient and candidate-only records are `history_only`: they stay reachable through the existing history route and are not copied into candidates, semantic signals, or default Attention. Rows are not merged on title similarity; only exact same-deliverable evidence (such as the same external Task ID) may authorize consolidation, preserving each source-row link. Before proposing a merge, count duplicate nonblank external Task IDs; title-only matches are explicitly not merge evidence.

- [x] **Step 4: Implement plan/apply CLI commands**

Add:

```text
task-semantic-import-plan --output MANIFEST [--limit N]
task-semantic-import-apply --manifest MANIFEST [--limit N]
```

`plan` is read-only. `apply` requires an exact database fingerprint and expected legacy row digests, writes semantic object plus `business_legacy_links` in one transaction per item, and treats an already-linked row as an idempotent skip.

- [x] **Step 5: Run tests and dry-run against a copied database**

Run:

```bash
.venv/bin/pytest -q tests/test_task_semantic_import.py tests/test_cli.py
tmp_dir=$(mktemp -d)
cp data/service.sqlite3 "$tmp_dir/service.sqlite3"
CEO_DB_PATH="$tmp_dir/service.sqlite3" .venv/bin/python -m app.cli task-semantic-import-plan --output "$tmp_dir/manifest.json" --limit 100
```

Expected: tests PASS; the plan reports formal-task, exact project-match, and history-only counts and makes no database writes. Do not run `task-semantic-import-apply` on the live database in this task.

- [x] **Step 6: Commit**

```bash
git add app/task_semantic_import.py app/cli.py app/store.py tests/test_task_semantic_import.py tests/test_cli.py
git commit -m "feat(tasks): plan evidence-backed legacy import"
```

## Task 9: Replace the Tasks API with three explicit semantic views

**Files:**
- Rewrite: `app/web_api/tasks.py`
- Modify: `app/web_api/registration.py`
- Modify: `tests/test_console_web_api.py`
- Rewrite: `tests/test_web_api_task_sort.py`
- Create: `tests/test_web_api_task_attention.py`

- [x] **Step 1: Write failing contract tests for the new endpoints**

The exact API surface is:

```text
GET /api/console/tasks/attention
GET /api/console/tasks/all
GET /api/console/tasks/projects
GET /api/console/tasks/attention/{attention_id}
GET /api/console/tasks/items/{task_id}
GET /api/console/tasks/projects/{project_id}
GET /api/console/tasks/legacy-projects/{legacy_project_id}
GET /api/console/tasks/sent-todos
```

Keep static routes registered before dynamic ID routes. Remove Project rows from the all-Tasks payload. The top-level `/api/console/tasks` endpoint may return a small navigation/summary envelope, but it must not preserve the old Project-as-Task list contract.

Test the default attention ordering: `decision`, `push`, `watch`, `fyi`, then latest meaningful update descending. Test server-side filtering and pagination before serialization.

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest -q tests/test_console_web_api.py tests/test_web_api_task_sort.py tests/test_web_api_task_attention.py -x`

Expected: FAIL on missing semantic endpoints and old DTO fields.

- [x] **Step 3: Implement strict DTOs**

```python
class ConsoleBusinessAttentionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    category: str
    business_area: str
    title: str
    why_attention: str
    current_state: str
    ceo_action: str
    anchor_label: str
    linked_task_count: int
    updated_at: str
    detail_url: str

class ConsoleBusinessTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    title: str
    stage: str
    status: str
    commitment_status: str
    owner: str
    deadline_at: str
    business_relevance: str
    anchor_labels: list[str]
    updated_at: str
    detail_url: str
```

Attention detail includes linked Tasks, evidence signals, anchor, and attention events. Task detail includes evidence, events, relations, clusters, anchors, official Project link, follow-ups, and DingTalk TODO link. Official Project detail includes only confirmed membership; project candidates have an explicit provisional DTO and label.

- [x] **Step 4: Preserve legacy evidence through an explicit endpoint**

Move the old Project detail builder behind `/legacy-projects/{id}`. It is historical evidence, not part of the new Tasks or official Projects lists. Update generated History and Sent TODO links to the correct semantic Task or explicit legacy route.

- [x] **Step 5: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_console_web_api.py tests/test_web_api_task_sort.py tests/test_web_api_task_attention.py tests/test_history.py
```

Expected: PASS; `/api/console/attention` remains unchanged and has separate tests.

```bash
git add app/web_api/tasks.py app/web_api/registration.py tests/test_console_web_api.py tests/test_web_api_task_sort.py tests/test_web_api_task_attention.py tests/test_history.py
git commit -m "feat(tasks): expose task-first console APIs"
```

## Task 10: Build the three-view Tasks console and readable dark theme

**Files:**
- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/api/console.test.ts`
- Rewrite: `frontend/src/pages/TasksPage.tsx`
- Rewrite: `frontend/src/pages/TasksPage.test.tsx`
- Rewrite: `frontend/src/pages/TaskDetailPage.tsx`
- Rewrite: `frontend/src/pages/TaskDetailPage.test.tsx`
- Create: `frontend/src/pages/TaskAttentionDetailPage.tsx`
- Create: `frontend/src/pages/TaskAttentionDetailPage.test.tsx`
- Create: `frontend/src/pages/TaskProjectDetailPage.tsx`
- Create: `frontend/src/pages/TaskProjectDetailPage.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/AppShell.tsx`
- Modify: `frontend/src/app/AppShell.test.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/styles.tasks-responsive.test.ts`

- [x] **Step 1: Write failing client mapping tests**

Add exact TypeScript interfaces and endpoint functions:

```typescript
export type TaskView = "attention" | "all" | "projects";
export type AttentionCategory = "fyi" | "watch" | "decision" | "push";

export interface BusinessAttentionSummary {
  id: string;
  category: AttentionCategory;
  business_area: string;
  title: string;
  why_attention: string;
  current_state: string;
  ceo_action: string;
  anchor_label: string;
  linked_task_count: number;
  updated_at: string;
  detail_url: string;
}

export function listBusinessAttention(params = {}, signal?: AbortSignal) {
  return request<BusinessAttentionList>(`/api/console/tasks/attention${query(params)}`, { signal });
}
export function listBusinessTasks(params = {}, signal?: AbortSignal) {
  return request<BusinessTaskList>(`/api/console/tasks/all${query(params)}`, { signal });
}
export function listBusinessProjects(params = {}, signal?: AbortSignal) {
  return request<BusinessProjectList>(`/api/console/tasks/projects${query(params)}`, { signal });
}
```

Test that missing required semantic fields fail visibly instead of silently mapping a Project payload into a Task.

- [x] **Step 2: Write failing Tasks page tests for approved IA**

Tests must prove:

- `/tasks` defaults to 需关注;
- the three tabs are 需关注, 全部任务, 正式项目;
- attention cards show category, business area, title, why, current state, CEO action, anchor, linked Task count, and update time;
- “当前无需处理” remains visible;
- category filters use 仅需知晓, 持续观察, 需要决策, 需要推动;
- routine unrelated Tasks appear only under 全部任务;
- provisional Project Candidates are visibly distinct and are not counted as 正式项目.

- [x] **Step 3: Implement route structure and details**

Use these paths:

```text
/tasks                         default view=attention
/tasks?view=all
/tasks?view=projects
/tasks/attention/:attentionId
/tasks/item/:taskId
/tasks/project/:projectId
/tasks/legacy-project/:legacyProjectId
```

Replace the old `projectId` detail route and update every UI link. The attention detail page renders the lifecycle and linked Tasks; Task detail renders commitment state and evidence; Project detail renders canonical registry evidence and confirmed linked Tasks.

- [x] **Step 4: Implement the approved balanced card layout**

Use semantic markup with this information order:

```tsx
<article className={`business-attention-card category-${item.category}`}>
  <header>
    <span>{attentionCategoryLabel(item.category)}</span>
    <span>{item.business_area}</span>
  </header>
  <h2><Link to={item.detail_url}>{item.title}</Link></h2>
  <dl>
    <div><dt>为什么关注</dt><dd>{item.why_attention}</dd></div>
    <div><dt>当前状态</dt><dd>{item.current_state}</dd></div>
    <div><dt>你的动作</dt><dd>{item.ceo_action}</dd></div>
  </dl>
  <footer>
    <span>{item.anchor_label}</span>
    <span>{item.linked_task_count} 个关联任务</span>
    <time>{localTime(item.updated_at)}</time>
  </footer>
</article>
```

Do not put raw evidence text or technical IDs on the default card.

- [x] **Step 5: Add Tasks-scoped dark tokens and contrast tests**

`AppShell` applies `task-domain-route` to every `/tasks` path. Add a dark token block scoped to that class, parallel to the existing scheduled-task theme, and use only variables in Task cards—no fixed light-only foreground/background pairs.

```css
@media (prefers-color-scheme: dark) {
  .task-domain-route {
    color-scheme: dark;
    --canvas: #111411;
    --panel: #171b17;
    --surface: #1b1f1b;
    --surface-muted: #242924;
    --ink: #f0f3ed;
    --ink-soft: #b3bab0;
    --line: #363d36;
    --line-strong: #555f55;
    --accent: #61d0a6;
    --on-accent: #071a13;
  }
}
```

Add CSS contract tests proving `.task-domain-route` owns dark tokens and attention cards use variables. Add component tests at a narrow viewport contract so CEO action text is not hidden or reordered after metadata.

- [x] **Step 6: Run frontend tests and build**

Run:

```bash
npm --prefix frontend test -- --run src/api/console.test.ts src/pages/TasksPage.test.tsx src/pages/TaskDetailPage.test.tsx src/pages/TaskAttentionDetailPage.test.tsx src/pages/TaskProjectDetailPage.test.tsx src/app/AppShell.test.tsx src/styles.tasks-responsive.test.ts
npm --prefix frontend run build
```

Expected: all tests PASS; TypeScript and Vite build succeed.

- [x] **Step 7: Commit**

```bash
git add frontend/src/api/console.ts frontend/src/api/console.test.ts frontend/src/pages/TasksPage.tsx frontend/src/pages/TasksPage.test.tsx frontend/src/pages/TaskDetailPage.tsx frontend/src/pages/TaskDetailPage.test.tsx frontend/src/pages/TaskAttentionDetailPage.tsx frontend/src/pages/TaskAttentionDetailPage.test.tsx frontend/src/pages/TaskProjectDetailPage.tsx frontend/src/pages/TaskProjectDetailPage.test.tsx frontend/src/app/router.tsx frontend/src/app/AppShell.tsx frontend/src/app/AppShell.test.tsx frontend/src/styles.css frontend/src/styles.tasks-responsive.test.ts
git commit -m "feat(tasks): make CEO attention the default view"
```

## Task 11: Remove the old active path, update product docs, and verify the cutover

**Files:**
- Modify: `app/task_models.py`
- Modify: `app/task_agent.py`
- Modify: `app/task_retrieval.py`
- Modify: `app/web_api/tasks.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: relevant tests from Tasks 1–10

- [x] **Step 1: Add a source-level regression test for retired behavior**

The test should inspect active Task Agent and API modules, not the historical import module:

```python
def test_active_task_path_has_no_project_first_actions():
    active_sources = [
        Path("app/task_models.py").read_text(),
        Path("app/task_agent.py").read_text(),
        Path("app/task_retrieval.py").read_text(),
        Path("app/web_api/tasks.py").read_text(),
    ]
    joined = "\n".join(active_sources)
    assert '"create_project"' not in joined
    assert '"update_project"' not in joined
    assert "TaskProjectPatch" not in joined
```

Also assert active code does not call `create_work_project` or `create_work_todo`. Historical importer and explicit legacy endpoint are allowed to read legacy tables; they must not create new legacy rows.

- [x] **Step 2: Delete retired active code and tests**

Remove old Project-patch validators, project ranking prompt rendering, Project-as-Task DTOs, old `/tasks/{project_id}` route, and active tests that encode those semantics. Do not delete legacy tables or evidence readers in this release.

- [x] **Step 3: Make all documents true**

Update:

- `README.md`: Tasks routes, data model, Task Agent behavior, business attention vs operational Attention, migration commands, and explicit production apply gate;
- `docs/architecture.md`: source signal → candidate/formal Task → merge/link/cluster → anchor/Project resolution → relevance → attention projection;
- `docs/runtime-mechanism.md`: persisted tables, atomic transitions, execution linkage, recomputation and failure behavior;
- `CHANGELOG.md`: user-visible cutover and the removal of Project-first writes.

Search and remove current-tense statements that say Tasks are `work_projects`, every TODO belongs to a Project, or the Task Agent creates Projects by default.

- [x] **Step 4: Run the full focused backend and frontend suites**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_task_models.py \
  tests/test_task_semantic_store.py \
  tests/test_task_semantic_service.py \
  tests/test_task_semantic_rules.py \
  tests/test_task_business_resolution.py \
  tests/test_task_attention_projection.py \
  tests/test_task_agent.py \
  tests/test_task_retrieval.py \
  tests/test_follow_up.py \
  tests/test_todo_sync.py \
  tests/test_todo_completion.py \
  tests/test_task_lifecycle.py \
  tests/test_task_semantic_import.py \
  tests/test_console_web_api.py \
  tests/test_web_api_task_sort.py \
  tests/test_web_api_task_attention.py \
  tests/test_history.py

npm --prefix frontend test -- --run \
  src/api/console.test.ts \
  src/pages/TasksPage.test.tsx \
  src/pages/TaskDetailPage.test.tsx \
  src/pages/TaskAttentionDetailPage.test.tsx \
  src/pages/TaskProjectDetailPage.test.tsx \
  src/app/AppShell.test.tsx \
  src/styles.tasks-responsive.test.ts

npm --prefix frontend run build
```

Expected: all listed tests and the build PASS.

- [x] **Step 5: Run the broader non-live regression suite**

Run: `.venv/bin/pytest -q -m 'not live and not browser'`

Expected: PASS, or document exact pre-existing unrelated failures with baseline evidence. Do not claim release readiness from focused tests alone.

- [x] **Step 6: Inspect the diff for false compatibility and stale claims**

Run:

```bash
rg -n 'create_project|update_project|TaskProjectPatch|Task 总结以项目为主线|work projects' app frontend/src README.md docs tests
git diff --check
git status --short
```

Expected: matches remain only in historical migration fixtures/documented legacy evidence where intentional; `git diff --check` is clean; unrelated shared-tree files are not staged.

- [x] **Step 7: Commit the cutover cleanup**

```bash
git add app/task_models.py app/task_agent.py app/task_retrieval.py app/web_api/tasks.py README.md CHANGELOG.md docs/architecture.md docs/runtime-mechanism.md tests frontend/src
git commit -m "refactor(tasks): retire project-first task path"
```

Before using this broad `git add` form, confirm the isolated worktree contains only this feature. In a shared checkout, stage explicit files or hunks instead.

## Task 12: Release handoff and acceptance evidence

**Files:**
- No new source files unless verification finds a defect.

- [x] **Step 1: Prepare migration evidence without applying live data**

Create and verify a SQLite online backup using the repository’s established backup procedure, then run `task-semantic-import-plan` against the backup/copy. Report:

- total legacy Projects and TODOs inspected;
- formal Tasks proposed;
- candidates proposed;
- official Project matches proposed;
- history-only legacy rows;
- duplicate nonblank external Task IDs eligible for a merge (never title-only guesses), plus rows missing an external ID;
- manifest ID and database fingerprint.

Do not run the live `apply` command without Derek’s explicit approval after he sees this report.

Read-only copy result (2026-09-24): `work_projects` 972, `work_todos` 3,327,
`work_updates` 5,345; 0 formal Tasks, 0 candidates, 0 exact official Project
matches, 9,644 history-only rows. Per Derek's decision, evidence-insufficient
legacy records remain history-only and are not surfaced as candidates. Of 875
old TODO links, 871 have a nonblank unique external Task ID, 4 lack an ID, and
no duplicate ID group qualifies for merge. The repository backup helper created
`/tmp/task8-import.pazkNk/service-2026-09-24.sqlite3`; `quick_check` returned
`ok`, SHA-256 `58759cbcf19b85edb389338ec5d2f226690be4534367fa0ae046110694d6fc00`.
Manifest `5ca9dd76c7ce46b7d311100e6d90e8e575882d3f9492a3d79c421b16048ebc8c`,
database fingerprint `68ca285f382fce9b341a7e42704eed7be12119d71773e32d658f9dc84c03df42`.
No apply was run against the copy or production.

- [ ] **Step 2: Review the product acceptance sample**

Use the latest 100 eligible source inputs, or all when fewer than 100 exist. For each surfaced business attention item verify:

- confirmed active business anchor;
- human-readable why/current/action fields;
- linked immutable source evidence;
- correct one of four attention categories;
- correct Task identity and merge behavior;
- routine unrelated small work absent from default attention;
- no Project created without registry/explicit confirmation evidence.

Record counts and every disagreement; do not summarize a partial sample as complete.

- [x] **Step 3: Send the runtime-restart handoff**

Do not restart `com.ceo-agent-service.main` from this implementation task. Send the heartbeat task `CEO 服务错误检查与修复` the final commit SHA and exact runtime/frontend files changed. Ask it to wait for an idle queue, verify imports, restart, and read back the new PID, health endpoint, queues, operational Attention, History, and Tasks APIs.

- [ ] **Step 4: Require live readback before release completion**

The heartbeat session must return evidence for:

- new process PID after restart;
- healthy service endpoint;
- no unresolved `processing` or new `failed` backlog caused by the cutover;
- `/api/console/tasks/attention`, `/all`, and `/projects` response shapes;
- `/api/console/attention` still serving operational attention unchanged;
- Tasks default view visible in both light and dark modes;
- one semantic Task detail showing evidence and commitment state;
- no new writes to `work_projects`, `work_todos`, or `work_updates` after the cutover time.

- [ ] **Step 5: Stop at the production migration gate**

If the code is live and verified but the legacy manifest has not been explicitly approved, report: “Task-first code is deployed and new inputs use the semantic model; legacy import is planned but not applied.” Do not describe historical migration as complete.

## Completion definition

Implementation is complete only when all of these are true:

- new inputs can create candidate or formal Tasks without a Project;
- assignment and acceptance update one Task with separate commitment states;
- merge, cluster, relation, official Project, and Project Candidate semantics are distinct and tested;
- business relevance requires a confirmed registered anchor;
- routine unrelated work remains in 全部任务 and stays out of 需关注;
- 需关注 defaults on `/tasks` and uses the approved balanced card fields;
- dark and light Tasks themes are readable and tested;
- business attention history is persisted and read does not resolve it;
- operational `/attention` remains a separate unchanged runtime surface;
- Task Agent and execution paths no longer create legacy Project-first records;
- legacy import is idempotent, evidence-backed, and still gated before live apply;
- runtime docs describe the deployed behavior in the same commits;
- tests, build, restart, API readback, and sample review have independent evidence.
