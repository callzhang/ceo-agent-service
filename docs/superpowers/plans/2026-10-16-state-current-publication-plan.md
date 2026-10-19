# Current State consumers and publication readers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with independent SPEC then QUALITY. Steps use checkbox syntax.

**Goal:** Remove the remaining current direct State-table reads and preserve publication, quiet-wait and maintenance reads across the real native source and the selected State target.

**Architecture:** Apply the complete adjacent zero-context patches only to a disposable candidate after the reviewed Store70/constructor/Email9 proposals. Runtime current callers use existing State projections and writes. Publication entrypoints explicitly choose the declared current State marker or native source marker; historical release class methods/capture/inverse remain untouched. The current marker must match the existing target version and the complete State declaration. No new business fields, stored content, status, policy, approval check, retry, fallback or send path.

**Tech Stack:** Python3.13/SQLite3.50/pytest.

---

### Task 1: complete current direct consumer patch

**Files:** `app/processing_reaction.py` finish/sync; `app/quality_gate.py` scan_hourly_quality/_check_scan_health/_check_scheduler_health/_check_runtime_route_pauses/_has_active_codex_capacity_pause/_check_external_delivery_queues; `app/audit_web.py` _email_worker_health_snapshot/_queue_attention_rows/_memory_write_queue_snapshot; `app/rule_question_retirement.py` _retire_in_connection; `app/reply_task_deferral.py` _snapshot.

The full executable changes are `2026-10-16-state-current-direct-proposal.patch`, SHA256 d8b63673755399c81153f2ad8fb8c3cbdc635be628be0c727eb08a79a74cc66f. Thirteen existing functions in five files change. The two Memory readers explicitly request the already reviewed Skill receipt action declaration through the existing Reply variant selector. Reaction deletion is scoped to its existing kind; rule retirement uses the existing State insert primitive in its existing transaction. All original business checks, error labels and predicates remain.

Complete test fixture changes are `2026-10-16-state-current-direct-test-proposal.patch`, SHA25639ef8b632d4fb1cd1b50125a83f0f2727aeab5a391c9b58814a4c5bb83e65827. They convert physical State fixture inserts/reads in test_quality_gate, test_rule_question_retirement and test_reply_task_deferral. Existing transactions are explicit where the State write primitive requires them. Deferral's parameterized raw SQL fixture accepts a callable for the State write while preserving all assertion branches.

- [x] Exact source inventory and original callpath inspection.
- [x] Complete executable proposal and fixture patch; root reaction/quality/retirement88 PASS21.09s, with original test isolation config.
- [x] Fresh independent SPEC then QUALITY for exact final proposal bytes: SPEC149+5native; QUALITY149+29adversarial.
- [ ] Activate only with all State callers/constructors/version/progress/coordinator changes and reviewed tests.

### Task 2: publication and dispatcher quiescence

The full executable changes are `2026-10-16-state-publication-proposal.patch`, SHA25662fcca01a989f2e729d1b9bcf378a335482907a54a5f5ffff5e3d450b69de2ae.

**Files and exact scope:**

| File | Change |
|---|---|
| app/state_publication_sources.py | Two read-only helpers. publication_store_version requires exact State and selected current version when a main State object is declared; otherwise reads main native service_state and refuses a native marker stamped current. Missing bootstrap marker returns None. publication_dispatcher_source chooses the existing dispatcher projection for that same role. No failed current read retries native. |
| app/action_schema_release.py | publication_candidate_source marker source and explicit current Skill receipt declaration only. |
| app/action_receipt_schema_release.py | publication_receipt_sources same source/declaration change. |
| app/action_postfix_schema_release.py | publication_postfix_sources same change plus the missing existing Store20 branch returning original postfix projections. |
| app/action_delivery_schema_release.py | publication_delivery_sources same source/declaration change. |
| app/task_schema_release.py | publication_task_sources/publication_meeting_sources only; retain original table exclusions and all historical branches. |
| app/scheduled_config_storage.py | publication_scheduled_source only; retain original old run and scheduled source branches. |
| app/email_publication_sources.py | _markers uses explicit Store marker helper; retained Email migration ledger remains authoritative for its own version. Store-only current State may lack Email ledger and still supports generic publication readers. |
| app/repository_updater.py | in_flight_work dispatcher relation only; same predicates/aggregation and read-only connection. Existing progress DAO unchanged. |
| app/deploy_maintenance.py | check_maintenance dispatcher relation only; same ownership, PID, effect, native-evidence and task eligibility checks. |

Historical release class prepare/publish/verify/inverse implementations in those files are outside this patch. Paired coordinator source/target/inverse changes remain a separate required implementation, not fulfilled by these read-only helpers.

Complete test code and existing postfix coverage extension are `2026-10-16-state-publication-test-proposal.patch`. The new `tests/test_state_publication_marker.py` covers native source/current refusal, selected current/missing/historical markers, malformed/mixed/TEMP/changed-index State rejection, untouched transaction/dump/file bytes, native/current dispatcher fact types and real in_flight_work current lease counts. The existing generic Store-only publication test now includes postfix.

- [x] Bounded source research confirms eight publication marker readers and the two direct dispatcher quiescence paths; no import dependency on paired release constants.
- [x] Marker16 pure cases PASS before dispatcher additions; root combined144 PASS27.99s before adding native/current dispatcher and quiet-wait probes.
- [x] Re-run final durable artifacts and record exact result:149PASS29.29s, sequential exactpatch application and owned temporary removal. The first added real quiet-wait fixture omitted its explicit BEGIN and failed before exercising the query; correct the fixture transaction, without changing application primitives or query semantics.
- [x] Fresh independent SPEC then QUALITY, including native predecessor and read-only current/source role boundaries.
- [ ] Select with whole-State coordinator/progress closure after actual-copy/current-fact inverse and genuine predecessor cold verification. Final10core+ledger remains unfinished.

### Task 3: exact disposable reproduction

The complete executable harness is `2026-10-16-state-publication-proposal-preflight.py`. It copies the app, required tests including original conftest, two tracked static registries, and reads metadata fixtures through the original docs path. It applies nine exact existing/new patches sequentially and runs only the four affected functional files plus marker regression and two relevant external-consumer cases. All outbound behavior remains the tests' existing substitutes. It cleans its owned TemporaryDirectory on both success and failure; no actual DB or production action.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-state-publication-proposal-preflight.py
```

Before any runtime activation, preserve exact source/current declaration contracts, update architecture/runtime behavior in the same implementation commit, then obtain component review and combined actual-copy evidence. Patches alone and fresh constructors do not prove final table-count acceptance or deployment.

Final publication testpatch SHA25648cc567cecd2e2831d69890a183d1baadd43e4e0d3c6ff0d83255bc7a54577ef. The executable harness applies exact artifacts successfully and149tests passed29.29s. Constructor artifact emits existing blank-line whitespace warnings; its accepted bytes/declaration remain unchanged. Independent SPEC and QUALITY remain pending.

Contract clarification for review: complete current Actions/Skill declaration is checked by the seven generic publication selectors. Email availability/publication public functions keep their existing marker-pair-only behavior; only their shared _markers source selection changes to validated State. This patch does not add a new Email action validation gate or claim that availability alone validates an entire database. Constructor and coordinator target validation remain separate.

Independent QUALITY PASS149/28.98s plus29 adversarial/differential cases1.46s, no findings. SPEC PASS149/27.73s plus5native0.33s; exact23function AST/signatures and original release methods preserved. Accepted unselected component receipt: ../specs/2026-10-16-state-current-publication-readiness.json.
