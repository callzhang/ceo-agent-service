# Explicit Project Attention Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every relevant Project's attention judgment explicit and inspectable in the same Task Agent run, without manufacturing Tasks or cards.

**Architecture:** Add required `project_assessments` to the current result envelope. Preserve the existing Task transaction, no-field-change guard and Attention proposal projection; resolve assessment references against actual stored identities and save their application results in the independent receipt. No second Agent, periodic maintenance, recovery loop, automatic card or business keyword classifier.

**Tech Stack:** Python, Pydantic v2, SQLite, pytest, native routed Agent CLI; current React/Vite console for unchanged card display verification.

---

## Working boundary and file map

Approved design: `docs/superpowers/specs/2026-10-02-task-project-attention-assessment-design.md`.
Work only in `/Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service`.
One core writer at a time, with spec review followed by quality review between tasks.
Parent claims authorize narrow child claims; read the shared claim board before each edit.

- `app/task_models.py`: required wire model, structural references and receipt result model.
- `app/task_agent.py`: current result parsing/correction/prompt, known-project coverage, actual identity resolution and receipt recording. Existing Task application boundaries stay intact.
- `scripts/inspect_task_attention.py`: read-only exact-input assessment/receipt inspection; no store initialization or historical rewriting.
- `tests/test_task_models.py`, `tests/test_task_agent.py` (including routed runtime/correction cases), `tests/test_task_agent_session.py`, `tests/test_inspect_task_attention.py`: focused regression tests. Update only current result producers in other focused Task tests where the required envelope changes; do not reinterpret historical records.
- `ci/shared-skills/ceo-work-tracking/SKILL.md`, `docs/architecture.md`, `docs/runtime-mechanism.md`: current behavior, field guidance, and separation of judgment from application.
- `scripts/replay_task_attention.py`, fixed fixtures and `docs/task-attention-phase1-validation.md`: fixed semantic oracle and exact W39 readback, separate from parser success.

The current production baseline remains `7bf7be5e`; latest W39 experiment `c87752c0` correctly bound the named Projects but returned six null proposals and no project-specific negative explanations. Preserve that failed business result.

## Task 1: Required judgment wire model

Progress: implemented in `d004921e`, test masking tightened in `8097b54b`. Implementer RED: 16 failed / 10 passed / 59 deselected before model changes; focused GREEN: 26 passed; full model file: 85 passed, independently rerun by root (85 passed / 0.36s). Independent spec review and quality review passed; quality reviewer verified the narrow correction. This proves only the wire/model slice, not producer integration, native business judgment or deployment.

**Files:** `app/task_models.py`, `tests/test_task_models.py`, Task/Attention paragraphs in `docs/architecture.md` and `docs/runtime-mechanism.md`.

- [x] Write RED tests before changing the model. Missing `project_assessments` must fail; `[]` is explicit no-related-project output. A zero-based decision index is valid; boolean/negative indexes and guessed nonpositive persisted IDs fail. Reasons and project titles cannot be whitespace. Current/historical citation shape follows the existing proposal rules.

```python
def test_project_assessments_are_required_in_current_result():
    with pytest.raises(ValidationError, match="project_assessments"):
        TaskAgentDecision.model_validate({"task_decisions": []})
    result = TaskAgentDecision.model_validate({
        "task_decisions": [], "project_assessments": [],
        "update_summary": "本轮没有相关业务项目",
    })
    assert result.project_assessments == []

def test_project_assessment_retains_negative_reason_and_original_quote():
    from app.task_models import TaskProjectAssessment
    result = TaskProjectAssessment.model_validate({
        "project_title": "示例项目", "anchor_id": 3,
        "outcome": "not_needed", "reason": "本轮验收按原计划完成，没有新增经营影响",
        "assessment_basis": "current_observation",
        "evidence": [{"source_ref": "message:42", "source_excerpt": "验收按计划完成"}],
        "decision_indexes": [0], "task_ids": [7],
    })
    assert result.decision_indexes == [0]
    assert result.task_ids == [7]
    assert result.outcome == "not_needed"
```

- [x] Run `python -m pytest -q tests/test_task_models.py -k project_assessment`. Expect missing model/required-field regression failure, not infrastructure failure.
- [x] Implement this wire type before `TaskAgentDecision` and add required `project_assessments: list[TaskProjectAssessment]` to that envelope. Current test result fixtures must explicitly supply real assessments or `[]`; no automatic default, legacy union or payload synthesis.

```python
class TaskProjectAssessment(StrictTaskModel):
    project_title: str
    anchor_id: int | None = Field(default=None, strict=True, gt=0)
    project_decision_index: int | None = Field(default=None, strict=True, ge=0)
    outcome: Literal["needs_attention", "not_needed", "insufficient_evidence"]
    reason: str
    assessment_basis: Literal["current_observation", "historical_comparison"]
    evidence: list[TaskAttentionEvidence] = Field(min_length=1)
    decision_indexes: list[Annotated[int, Field(strict=True, ge=0)]] = Field(default_factory=list)
    task_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list)
    existing_attention_id: int | None = Field(default=None, strict=True, gt=0)

    @model_validator(mode="after")
    def valid_assessment(self) -> "TaskProjectAssessment":
        if not self.project_title.strip() or not self.reason.strip():
            raise ValueError("project assessment requires a nonblank title and reason")
        if self.anchor_id is not None and self.project_decision_index is not None:
            raise ValueError("project assessment requires one Project identity")
        if self.anchor_id is None and self.project_decision_index is None:
            if self.outcome != "insufficient_evidence":
                raise ValueError("unconfirmed Project requires insufficient_evidence")
        if self.existing_attention_id is not None and self.outcome != "needs_attention":
            raise ValueError("existing Attention reference requires needs_attention")
        if len(set(self.decision_indexes)) != len(self.decision_indexes):
            raise ValueError("project assessment decision indexes must be unique")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("project assessment Task IDs must be unique")
        current = any(item.signal_id is None for item in self.evidence)
        historical = any(item.signal_id is not None for item in self.evidence)
        if not current:
            raise ValueError("project assessment requires current null-ID evidence")
        if self.assessment_basis == "historical_comparison" and not historical:
            raise ValueError("historical_comparison requires positive persisted-ID evidence")
        return self
```

- [x] Add field descriptions explaining stored IDs versus decision positions, factual quotes versus inference, candidate eligibility and unknown-project evidence-only scope. Do not use classifiers to reject vague reasons; test nonblank structurally and quality in native evals.
- [x] Run `python -m pytest -q tests/test_task_models.py`; expect all model tests passing after deliberate fixture upgrades. Record other test files still awaiting the new envelope rather than reporting full integration done.
- [x] Document the required result shape as development-only, then commit owned files. Spec and quality reviews must both pass before Task 2.

## Task 2: Bind judgments to this run's actual decisions and Projects

Execute sequentially as 2a (envelope consistency, parser correction and prompt/Skill guidance) then 2b (stored Project/Task/card provenance and applied identity resolution). Review 2a before starting the stored-domain changes. Required field fixtures outside the current 2a tests remain an explicitly incomplete integration gate, not a reason to introduce runtime defaults.

Task 2a progress: implemented in `7f9ac396`, with independently identified mixed-reference corrections in `674251c6` and `63fde9c6`. Root independently reran the model, Agent and session files at `63fde9c6`: 277 passed / 10.07s. Spec re-review independently exercised 16 accept/reject probes plus 18 focused tests and passed. Quality review is in progress; Task 2b has not started. The original review failures are preserved in regression tests: known-anchor coverage of a current proposal, unequal known anchors hidden by a proposal, null-anchor Attention supported through the same exact-title proposal, and unequal explicit proposal titles. Matching now uses one shared predicate. These results prove envelope consistency and prompt integration only, not stored identity/citation validation, application receipts or business outcomes. Task 2 remains incomplete.

Task 2a final review: `485d8ac3` additionally fixes a direct support-title conflict previously masked by independent coverage, unrelated omitted-support diagnostics, and schema/correction scope wording. The selector-priority helper was removed. Root independently verified the failing direct probe before/after and ran 281 tests / 15.35s; final spec review passed 20 direct probes and 24 focused tests; final quality review passed six direct probes and independently reran all three files (281 passed / 10.74s), with no remaining findings. Task 2a is complete. Task 2b stored-domain validation and identity readback remain next; Task 2 as a whole is not complete.

Task 2b initial implementation: `2084ad5d`; root independently reran the three focused files (303 passed / 10.33s). Spec review independently exercised 20 real SQLite probes and found four unresolved gaps: a resolved current Project and positive Attention can select unequal canonical anchors; an existing-card claim need not cite that card's originals; an applied mapping can claim a Project that was not actually linked; an unresolved new Project can cite an unrelated existing Task. Root directly reproduced the canonical contradiction and false mapping. The implementation is **not approved**; the same writer is adding precise regressions and corrections before spec/quality re-review. No receipt/native/deployment gate has passed, and the remaining Task 2 boxes stay unchecked.

Task 2b follow-up evidence: `cd81b64e` corrected those four gaps, including decision-index-only unrelated support (root: 312 passed / 29.03s), but independent review still found a new unregistered Project could select another existing Project or guess its future ID, and task-ID-only judgments could lose a real applied Project mapping. `41b5bcef` fixes both; root independently reran the same three files (315 passed / 6.49s). Spec re-review verified all earlier cases with real SQLite, but found one remaining coverage omission: a `merge_identity` target is actually updated and returned yet its confirmed Project can receive no assessment. The same writer is adding omitted-target and valid-covered-merge regressions. Spec and quality approval remain pending; Task 2 is not complete.

Merge-target correction: `96c7e229` includes only the current identity merge target in the existing stored-task coverage collection and checks its existence before writes. The omitted-judgment regression failed before the change; the valid explicitly covered merge remains accepted without requiring the source Task to share the target Project. Root inspected the seven-line domain change and independently reran the three focused files (317 passed / 9.06s), then projection/retrieval files (46 passed / 13.11s). Spec re-review is running at that exact source commit; quality review has not started. No Task 3, native replay, deployment or production mutation is included.

Task 2b final spec review: passed at exact `96c7e229`, independently verified through 29 bounded real SQLite probes and 39 focused tests / 5.04s. This includes the omitted merge-target and nonexistent-target atomic rejection, valid target-only judgment and actual mapping, every previous identity/card-proof/support regression, skip/failed-acceptance/no-change controls, and bounded coverage exclusions. A fresh code-quality reviewer is now reviewing the full Task 2b functional range; Task 2 remains incomplete until that review passes. Task 3 has not started.

Task 2b quality review: **PASS** at the same functional source, with no Critical/Important findings. Reviewer independently ran 317 tests / 6.44s and seven real SQLite controls (including ambiguous/explicit selection and sibling-transaction rollback). One Minor follow-up is concrete: each historical citation/card proof reloads its Task's full evidence history; a 5,004-row/20-citation probe materialized 100,080 rows while holding the transaction. The same writer is replacing only these two membership checks with bounded indexed pair lookups using the existing primary key, with a non-timing regression. An explicitly indexed assessment selects a Project only when the actual returned Task really has that confirmed official link; absent a selection, ambiguous links remain null. Task 3 remains next after this bounded follow-up verification; no business/evaluation/release gate is inferred from quality approval.

Task 2b final source gate: `91c0032d` removes the full-history materialization with one transaction-local indexed membership predicate and a real-SQLite non-timing RED/GREEN regression. Root independently verified 318 tests / 15.96s and the actual covering-primary-key query plan. Bounded spec re-review passed nine independent SQLite probes and 18 focused tests / 4.01s; bounded quality re-review passed 13 focused tests / 1.97s, negative controls, and a 5,001-row/20-citation probe with exactly 20 pair lookups and zero full-history materializations. Prior full Task 2a/2b approvals remain valid, with no outstanding source findings. A fresh, single Task 3 writer is now implementing receipts and read-only inspection. The broader current-producer integration box remains pending and is explicitly tracked under Task 4; no runtime default or legacy reinterpretation is permitted.

**Files:** `app/task_models.py`, `app/task_agent.py`, current result producers and runtime/correction cases in `tests/test_task_agent.py`, `tests/test_task_agent_session.py` (session fixtures only), `ci/shared-skills/ceo-work-tracking/SKILL.md`, the two behavior documents.

- [ ] Write failing regressions in the existing source/Project fixtures for: omitted known Project, out-of-range decision position, no ProjectProposal at `project_decision_index`, duplicate assessments for the same known Project, positive judgment without proposal or exact existing card, negative judgment with a proposal, and two related decisions sharing one judgment. A plain-source unconfirmed Project with `insufficient_evidence` must be accepted without registering it.
- [ ] Run `python -m pytest -q tests/test_task_models.py tests/test_task_agent.py tests/test_task_agent_session.py`; preserve the RED evidence for the added assertions.
- [x] In `TaskAgentDecision`'s existing after-validation, resolve only structural references: every index addresses this envelope; a project decision index addresses a non-skip ProjectProposal; proposals and outcomes agree; one structured identity has one assessment. Relevant known anchors from `project_link_proposal` and positive attention anchors, plus current `project_proposal` positions, require coverage. Same exact-title ProjectProposals can share one assessment; this is not a fuzzy identity merger.
- [x] In the existing pre-application domain validation, include confirmed Project links of current real Task IDs in coverage. Verify assessment cites against the current WorkItem and stored original Signals using the existing provenance semantics; historical citations are actual original IDs. Verify referenced existing active cards and their actual Project/supporting Task/assessment evidence before treating them as already represented. Reject a guessed or unrelated existing card. Use existing bounded correction mechanisms; add no retry loop.
- [x] Preserve `apply_task_agent_decision`'s no-field-change guard. Collect actual applied decision-to-Task and decision-to-Project identities during the ordinary transaction for receipt resolution. No independently created Signal, Task or Project for negative/unresolved assessments, no event for a no-change assessment, no auto-close for `not_needed`.
- [x] Extend the prompt, schema descriptions, existing correction instructions and CI Skill together. Require explicit per-Project judgment, concrete reason and original evidence from any current source (meeting, chat, report). An explicit empty set explains no relevant Project in `update_summary`; no full-history coverage assertion.

The exact Agent output for a negative known-Project judgment is:

```json
{
  "task_decisions": [],
  "project_assessments": [{
    "project_title": "示例项目", "anchor_id": 3,
    "outcome": "not_needed", "reason": "本轮只确认按计划验收，未出现新增经营影响",
    "assessment_basis": "current_observation",
    "evidence": [{"source_ref": "message:42", "source_excerpt": "验收按计划完成"}],
    "decision_indexes": [], "task_ids": [7]
  }],
  "update_summary": "已判断示例项目本轮不需新增关注"
}
```

- [ ] Upgrade current fake Agent outputs intentionally, retaining original Task semantics and original negative tests. Tests must prove candidate stage unchanged, exact source rejection, proposal application rejection distinguishable from a negative judgment, normal no-change idempotence, and historical rows untouched.
- [x] Re-run the three focused test files, add relevant projection/retrieval tests if their interface changes, update behavior docs in the same commit. Complete spec then quality review before Task 3.

## Task 3: Persist application readback and expose read-only diagnosis

**Files:** `app/task_models.py`, `app/task_agent.py`, `scripts/inspect_task_attention.py`, `tests/test_inspect_task_attention.py`, focused Task/projection tests, architecture/runtime docs.

Initial implementation: `8e0913db`, with approved literal receipt/output names retained. Implementer reports five initial RED failures plus two additional RED identity controls (already-registered exact-title Project on a no-change Task; current Signal mapped through explicit Task IDs without support positions). Root independently verified the five prescribed files (348 passed / 7.88s). Fresh independent spec review is running; quality review has not started. Current source producers outside those files, native business comparison, deployment and live readback remain pending.

Spec review at `8e0913db` found three real-SQLite readback gaps: mixed applied/unapplied support hid the rejection; Task-ID-only support failed to propagate recompute errors; indexed no-change support lost a verified existing Task ID. `a7920700` adds precise RED/GREEN regressions and fixes them. Root independently reran the five files (351 passed / 8.66s), and spec re-review independently confirmed all three controls plus 17 focused tests / 1.12s. Mixed support now retains the real applied card ID and explicit rejection reason, while the outer partial result and actual card membership remain separately visible. The re-review still fails because an existing-card reference masks a relevant current proposal's upsert error, and runtime documentation retains an obsolete statement that receipt/inspection paths are not connected. The same writer is correcting these two bounded issues before further spec/quality review. Task 3 is not approved, and no native or release gate has passed.

`0c6b2bca` fixes the additional existing-card/current-proposal error case and the contradictory runtime statement. Root independently reran the same five files (352 passed / 9.30s) and inspected the source diff: a verified existing card is used as an `existing` result only when no current proposal is present; otherwise the current proposal's actual outcome remains visible while preserving the real card ID. Formal independent spec re-review is in progress. Quality review, broader producer integration and native/release gates remain pending.

Formal spec re-review of `0c6b2bca` (HEAD `f75b4e6d` differs only by the preceding plan note) returned PASS: independent five-file verification 352 passed / 8.31s, plus real-SQLite current-proposal failure retaining existing card ID, existing-only repeated application with zero new domain events, and the original three receipt failure controls. Inspector historical missing/empty distinction, private-field exclusion, database byte preservation and missing-file behavior passed. Behavior docs are consistent. A fresh independent Task 3 quality reviewer is now running; Task 3 remains unclosed until that gate passes. This does not approve producer integration, native business behavior or release.

Task 3 quality review formally returned PASS for the same functional source `0c6b2bca`: independent 334 tests / 7.57s plus 18 projection tests / 1.22s, scoped Ruff clean, and disposable SQLite unrelated-Project error, no-change proposal rejection and real field-change/application controls passed. No actionable source finding remains. The review and spec clarification confirm receipt `task_ids` are real persisted support/reference IDs, not proof of applied Project links or card membership; explicitly supported actual decisions retain relevant recompute failures without asserting the Project caused them. Task 3's source gates are now closed. Proceed to deliberate current producer/oracle integration; native comparison, exact W39 and release gates remain open.

Root also exercised the new read-only inspector on the retained current-schema historical W39 copy `w39-project-identity-candidate.sqlite3`: exact input 27465 returns old runs 10634/10662 with raw `project_assessments` absent, not synthesized as `[]` or a negative judgment; the later historical receipt remains the preserved failed-business `no_proposal` result (six decisions, four links, zero proposals/cards). The original immutable pre-phase-one `baseline.sqlite3` cannot be read by this receipt inspector because its schema lacks `projection_json`; direct read-only schema inspection confirmed that limitation. It was not changed, and no legacy-schema fallback was added. Existing Store initialization already adds the receipt column on normal current-schema replay copies. This proves actual historical JSON readback only, not a new native Task 3 judgment or business acceptance.

- [x] Write RED tests for a negative assessment with no proposal, unresolved Project, rejected proposal from an unapplied Task decision, applied folded proposals, matching existing card, projection error, and old run without assessments. Ensure no-change repeated input preserves actual IDs/events and negative judgment cannot close a card.
- [x] Add receipt assessment results with `assessment_index`, actual `anchor_id`, actual `task_ids`, actual `attention_id`, `status` (`recorded`, `applied`, `existing`, `rejected`, `error`) and application `reason`. Include verified citation readback (original `source_ref`, `source_excerpt`, actual persisted `signal_id` where one exists, source time and link from the immutable WorkItem/original Signal; never invent missing metadata). Raw judgment remains in `decision_json`; receipt results are independent of the business `outcome`. Resolve new identities from actual successful decisions, never assumed future counters. Existing proposal rejection/recompute reasons propagate; unresolved evidence-only assessment is `recorded` with no fake anchor/card. `no_proposal` still describes proposals only.
- [x] Finalize assessment receipt results after the existing projection consumer, using its actual outcomes; do not turn an unapplied positive judgment into successful negative judgment. Save through the existing independent projection receipt storage, not a new persistence table or background repair.
- [x] Extend the read-only script's exact-run select with `decision_json` and emit only the raw `project_assessments` plus the projection readback. Tests use valid stored JSON (`{}` for historical records), not an invented old judgment. Historical missing field is clearly shown as absent, not `not_needed`.

Inspection output for a current negative judgment must expose this relationship:

```json
{
  "run_id": 11,
  "project_assessments": [{
    "project_title": "示例项目", "anchor_id": 3, "outcome": "not_needed",
    "reason": "验收按计划完成，没有新增经营影响",
    "assessment_basis": "current_observation",
    "evidence": [{"source_ref": "message:42", "source_excerpt": "验收按计划完成"}],
    "decision_indexes": [], "task_ids": [7]
  }],
  "projection": {
    "status": "no_proposal", "proposal_count": 0,
    "project_assessments": [{
      "assessment_index": 0, "anchor_id": 3, "task_ids": [7],
      "attention_id": null, "status": "recorded", "reason": ""
    }]
  }
}
```

- [x] Run `python -m pytest -q tests/test_inspect_task_attention.py tests/test_task_models.py tests/test_task_agent.py tests/test_task_agent_session.py tests/test_task_attention_projection.py`. Expected focused tests green and inspection database bytes unchanged.
- [x] Update inspection instructions and outcome meanings in behavior docs, commit, spec review, quality review.

## Task 4: Fixed native semantic comparison and exact W39

**Files:** `scripts/replay_task_attention.py`, `tests/test_task_attention_multisource.py` (existing replay/oracle tests), existing versioned fixtures, `docs/task-attention-phase1-validation.md`, this plan's progress boxes.

Source integration was committed as `3d629042`: explicit current mock assessments, the additional nine-case fixture and the post-run raw-decision/receipt oracle. Root independently ran source registration, multisource and Web summary tests (143 passed / 10.87s), then the bounded CLI group (22 passed, 254 deselected / 2.06s). Original nine-case and Project competition fixture hashes remain unchanged. These are source checks, not native business acceptance. Independent spec review has identified three open gaps: an extra raw positive-ID citation can pass without a supporting Task/card relationship; duplicate receipt Task IDs can satisfy a list-count check; and four invalid Project-link cases now stop at companion assessment validation instead of their original rejection layer. Root inspected those exact branches and confirmed the causes. Spec review remains open; quality review and native evaluation must wait for fixes and formal re-review. No native run, push, release, global Skill publication or production mutation occurred in this slice.

Formal review returned SPEC FAIL with three disposable-SQLite reproductions. `daa2ead7` repairs those gaps without runtime changes: raw positive citations must correspond to the verified independent receipt and actual supporting Task/card proof, receipt Task identities must be unique real integers, and the seven original link-application guards use a deliberate domain-unit boundary with valid independent assessment components and exact original error assertions. The final injected invalid link is not claimed to be an accepted complete wire envelope. No raw Task-ID fallback fills missing receipt support. Implementer RED evidence was two false-positive oracle failures and four wrong-layer link failures; root independently checked the working repair (26 passed / 2.15s), then the committed three integration files (146 passed / 10.67s). Claim-only `de0c8b2f` releases the writer. Formal spec re-review is running; no source-quality or native gate is yet closed.

Additional root development checks before this repair, at functional `3d629042` plus plan-only `cfcf36a9`: five core files 352 passed / 11.52s; retrieval, business resolution and Attention API 49 passed / 11.86s; two actual page files 22 passed / 1.20s; TypeScript/Vite build and `app.cli`, `app.worker`, `app.email_worker`, `app.service_supervisor` imports passed. These checks do not replace final integrated-source verification, native semantic evaluation or production readback. Original fixtures and the candidate Skill hashes were independently rechecked unchanged.

Spec re-review of `daa2ead7` formally confirms all three original repairs, the longer linked-extra citation and distinct two-Task positive controls, and the explicit application-unit link-test boundary. It independently reran 146 integration tests / 10.65s and CLI 22 / 2.11s; root also reran CLI 22 / 2.28s. One same-scope provenance gap still causes SPEC FAIL: the no-Task insufficient-evidence case passes normally, but adding a receipt-only real unlinked Signal with matching ref/quote/time/link also falsely passes because relationship checking is conditional on nonempty receipt Task IDs. Raw judgment and expectations remain untouched in that control. The same writer is adding its narrow RED/GREEN regression and applying the existing Task-or-card proof check unconditionally to positive receipt Signals. No source gate, quality review, native evaluation or release is approved by this partial re-review.

`ab4bd59e` removes that nonempty-Task condition and adds the exact frozen no-Task case control and receipt-only unlinked-Signal regression. The implementer observed RED (one failed tampered result, one passed unchanged control / 0.46s), then 21 oracle tests and 148 integration tests passed. Root independently verified the committed three integration files (148 passed / 11.52s). No runtime change, generated Task, fixture change, raw-ID support fallback or leftover writer claim was introduced. Formal spec re-review is running at that functional SHA. Quality and native/business gates remain pending.

Formal independent spec re-review of `ab4bd59e` returned PASS: 148 integration tests / 11.95s, CLI 22 / 2.22s and 21 oracle tests / 2.05s, plus the original four false-positive SQLite reproducers and permitted longer linked quote/distinct two-Task/no-Task controls. The actual no-Task control keeps zero Tasks/cards; the receipt-only unlinked Signal is rejected. All seven original link errors and inactive-state preservation were independently observed. A fresh bounded quality reviewer is now running. Root independently verified CLI 22 / 2.42s. Native paired evaluation, exact W39, semantic reason acceptance and release remain open.

Root refreshed `origin/main` to `9493fc87` (five upstream commits not yet merged into this branch), with no new change in the scoped Task/runtime/Skill/page files relative to the preceding preflight. Both pinned baseline `7bf7be5e` and candidate use installed `codex-cli 0.154.0`, with total/idle caps 900/300 seconds. Their CI Skill hashes remain separately pinned (`5c2bcbee...` baseline, `246688c2...` candidate). These are preparation facts only, not a native run or integrated freeze. Integrate and reverify after source quality approval.

Fresh source-quality review of `ab4bd59e` returned FAIL with three bounded oracle false positives independently reproduced after a valid control: duplicate receipt index hides an additional error receipt; JSON null reason passes through `str(None)`; an extra empty raw/receipt quote passes the substring test. Its independent 148 integration and 22 CLI tests pass but do not prove these absent controls. Root inspected the exact code branches and delegated precise RED/GREEN repairs to the same writer, without a new parser/framework, runtime restriction or fixture change. The reviewer withdrew a proposed generic confirmed-Project-link requirement after a real permitted current-selected/no-field-change recorded-reference control passed; reference Task IDs remain distinct from applied Project links and card membership. Quality, integrated freeze and native/release gates remain open.

`e8866186` repairs those three bounded checks: actual nonblank string reasons/quotes and a complete unique original-index receipt map, preserving output-order independence. Implementer RED was three false-positive failures / 1.18s; root independently reran the committed integration files (153 passed / 35.96s). The added positive order control uses real two-Project/Task/card domain application, then reverses only the receipt list. No fixed fixture or runtime behavior changed and the own writer claim was removed. The same fresh quality reviewer is now rechecking its original probes and permitted support/reference controls; native/integrated/release gates remain unapproved until source review closes.

Integration baseline at `f983b72a`: root ran `tests/test_task_source_project_registration.py` and `tests/test_task_attention_multisource.py` (94 failed, 30 passed / 20.79s). Directly inspected current producer fixtures still omit required `project_assessments`; representative failures stop at that required-field validation, before the intended domain assertions. Preserve original positive/negative expectations and deliberately upgrade these producers after the core contract is reviewed. This is an incomplete integration gate, not evidence of 94 distinct business defects, and not permission to synthesize runtime defaults or reinterpret historical runs.

Root refreshed that same two-file integration baseline at `499c7e12` after Task 3 spec approval: 94 failed, 30 passed / 7.10s. The displayed current-source registration failures still stop at missing required `project_assessments`; both test payload factories were read directly and still omit that field. Task 4 must deliberately upgrade current producers and then uncover/verify their original domain assertions, not hide this failure through a runtime default.

A bounded search for additional current `TaskAgentDecision` test producers found `tests/test_web_api_task_project_summary.py`; root ran its single test at `caef9630` (1 failed / 1.45s), also missing required `project_assessments` before its unchanged latest-report registry assertions. Include this current producer in the same deliberate fixture integration, preserving its original summary expectations. CLI and opt-in live e2e consumers remain separately identifiable; this search is not a full-suite verification.

Root then ran only `tests/test_cli.py -k process_work_items` at `fbbab991`: 4 failed, 18 passed, 254 deselected / 2.64s. The four failing functions are claimed-input processing, existing input without feature gate, stale processing recovery, and Task Agent timeouts. Read-only inspection of their actual disposable SQLite inputs confirmed every failure stores the missing `project_assessments` validation error, not a runtime/provider problem. Include these four current mocked outputs in the fixture upgrade while preserving their queue/timeouts/recovery assertions; do not run or rewrite unrelated CLI tests or opt-in native e2e inputs.

Development UI baseline at `2fc67403`: root independently ran the actual two page files `frontend/src/pages/TasksPage.test.tsx` and `frontend/src/pages/TaskAttentionDetailPage.test.tsx` (22 passed / 1.27s), then `npm run build` (TypeScript and Vite succeeded). Build output is only in this isolated development worktree. This does not prove live card content, dark/narrow rendering, or final integrated release readiness; reverify as needed after source freeze/integration and retain the real browser gate.

- [ ] Add independent oracle assertions for assessment coverage, cited source and negative explanation; do not inject expected outcomes into Agent input. Keep original nine case expectations and project-name competition expectations. Add meeting/chat/report assessment cases, vague-risk/no-Task/no-confirmed-Project/routine negatives, same-Project two Tasks and already-represented-card idempotence.
- [ ] Run `python -m pytest -q tests/test_task_attention_multisource.py` first RED then GREEN. Use existing eval harness, not a new runtime, provider, route, retry or concurrency policy.
- [ ] Freeze commit and Skill hash. Use the existing native replay script for pinned baseline and candidate with identical model/route/timeout/concurrency. Baseline's missing field is observable baseline behavior, not silently reinterpreted by the candidate model.
- [ ] Create a fresh SQLite copy of the verified immutable baseline; replay only input `27465` with exact source ref `dingtalk-doc:a9E05BDRVQvy7QEacPZLB4anJ63zgkYA#sha256=21661643562265ca27e3369112a7ce3e91d9cbb6d21733050b5c3e7a9d42bf1e`. Expected: original Tasks reused, source-defined 中汽创智/岚图 identities, explicit risk judgments with cited经营影响, correctly supported cards, no unrelated promotion. Inspect with the existing command below, then replay the identical input a second time and compare Task/Project/Card/event identities.

```sh
python scripts/inspect_task_attention.py --db /var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/w39-project-assessment-candidate.sqlite3 --input-id 27465
```

- [ ] Record code/tests, mechanical completion, business oracle, actual source/entity/card readback separately. If business judgments fail, diagnose saved reasons; do not force cards, weaken the oracle or report parser success as business acceptance.
- [ ] Run current focused backend tests and existing two page test files, frontend production build and imports. No whole serial suite and no tests in the production checkout.

## Task 5: Continue the existing approved release gates

**Files:** existing phase-one implementation plan and validation report; no new release mechanism.

- [ ] Final review and fixed comparison before PR/merge for this product-behavior change. Preserve foreign edits and commits; attach any created PR to this chat.
- [ ] Push a complete commit and verify remote SHA, then use `python -m app.deploy` only. Verify actual production checkout/PID/health/queues/Attention/History; no direct process killing or manual production edits.
- [ ] Publish the matching global Skill through the existing approved Skill release workflow only after code deployment. Verify content/version/hash and preserve other agents' Skill changes.
- [ ] Verify the new production backup before exact, bounded requeue of input 27465; retain every original input payload, attempt and run. Ordinary service processing/readback must prove applied judgment/cards; acceptance of requeue does not prove completion.
- [ ] Inspect real browser Tasks/需关注 and card evidence details in light/dark/narrow layouts. No new business list or assessment audit page is part of this patch.
- [ ] Validate a genuine non-report production source when available; otherwise state that missing gate. Clean only this workflow's explicit owned temporary copies/worktrees after verified backup retention. Release claims and mark the original goal complete only when all required gates have evidence.

## Plan self-review

The approved spec maps to Tasks 1–3 (wire/coverage/provenance/application/inspection), Task 4 (positive and negative semantic acceptance, exact W39 twice) and Task 5 (publication and live readback). Same-Project grouping and existing-card idempotence preserve the ordinary Task/Attention path; historical data is inspected raw. No hidden classifier, completion Agent, safety-policy layer or full-company scan. All new wire names use `project_assessments`, `project_decision_index`, `decision_indexes`, `task_ids` and the three stated outcomes consistently. Runtime completion and business acceptance remain distinct.
