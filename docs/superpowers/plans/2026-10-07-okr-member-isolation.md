# OKR Member Isolation Implementation Plan

> **For agentic workers:** Use subagent-driven-development with spec and code review; implementation edits are bounded below.

**Goal:** Preserve complete roster reporting when an individual source or terminal analysis fails, without fake scores or missing-person omission.

**Architecture:** System-owned member gap records complement successful scored reviews. Collection and terminal analysis outcomes persist with stable identity; rendering and coverage validate the full roster. Shared prerequisite failures and live analysis remain failures/in-progress, not fabricated completed gaps.

**Tech Stack:** Python, Pydantic, pytest, existing report gateway and analysis leases.

### Task 1: Source Classification Boundary

- [ ] Inspect DwsLiveOkrSource and the configured headless source; establish how verified current-period absence differs structurally from transport/auth errors.
- [ ] No keyword matching of stderr. The shared direct-source module is already dirty from other work; do not overwrite, stage or publish its changes.
- [ ] If authoritative absence is not exposed by the current source contract, identify that exact blocker. Implement no success-looking empty payload around a generic exception.

### Task 2: Collection And Aggregate Isolation

- [ ] Add failing focused tests in tests/test_weekly_okr_report.py: first member failure still fetches later members; terminal member analysis failure retains the successful member; all technical source failures do not publish; missing/duplicate/overlapping roster coverage is rejected.
- [ ] Add a typed system-owned member gap record in app/weekly_okr_report.py with stable user identity, name, failed stage and evidence. Model-generated scored reviews remain strict; the model cannot manufacture gap coverage.
- [ ] Preserve per-manager source outcomes in the existing raw source artifact. Keep completed analysis cache and leases; WeeklyOkrAnalysisInProgress is not converted into a terminal gap.
- [ ] Validate disjoint complete scored/gap coverage against verified roster identities. Never synthesize zero KR, leadership or culture scores for gaps.

### Task 3: Rendering And Publication

- [ ] Render every member in management table and appendix, with absent scores and the precise gap stage. Keep real score denominators; group summary reports gaps accurately.
- [ ] Exercise existing dry-run and fake-gateway publication paths. Missing reviewers without explicit valid gap records still fail; verify LAST_SUCCESS advances only after the existing provider publication path completes.
- [ ] Existing source and analysis tests must pass after updating only assertions superseded by the approved policy. Do not relax successful KR coverage or live-source validation.

### Task 4: Review And Production Gate

- [ ] Separate this implementation from message prompt commits and their native scores.
- [ ] Spec compliance review, then independent code review, then fixed report comparison and exact CI.
- [ ] Do not deploy or recover the actual blocked weekly task until the live source contract can correctly represent all missing-quarter members and existing publication receipts are reconciled.
