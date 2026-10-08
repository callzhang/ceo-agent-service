# Meeting Audience Recovery Implementation Plan

> **For agentic workers:** Use executing-plans task by task; preserve red-green evidence and reviewed release gates.

**Goal:** Complete the two original unsent meeting jobs under Derek's approved message-audience contract, without sending a private fallback to the principal.

**Architecture:** Keep candidate history-read denials as typed evidence instead of aborting all discovery. Reuse the shared audience contract in the actual meeting planner; verify stable recipients at planning and delivery boundaries and never switch audiences automatically. Preserve original meeting identities, historical runs, and external-effect receipts on formal recovery.

**Tech Stack:** Existing Python dataclasses, Pydantic models, pytest, DWS, routed meeting runtime, SQLite recovery APIs.

## Tasks

- [x] Add failing tests for candidate-scoped denied history alongside readable candidates, and all-denied evidence.
- [x] Implement typed denial evidence and meeting serialization; preserve transient failures and disallow denial-as-empty-history.
- [x] Add failing tests for shared meeting audience policy, verified non-principal business recipients, principal rejection, and no automatic delivery recipient switch.
- [x] Repair planner and delivery policy using original stable participant identities; keep role/content authorization checks explicit.
- [x] Add unsent/receipt/active-claim CAS regressions to the existing formal analysis rerun before using it.
- [ ] Run focused and related regressions, independent review, and fixed native routing comparison without external effects.
- [ ] Update runtime documentation and changelog, commit scoped fixes, run exact CI, and deploy through formal quiet/verified-backup gates.
- [ ] Read current discussion/membership/titles and all existing effects; recover jobs 5556/5812 by original identity and verify final provider receipts or evidence-backed no-action.

## Boundaries

Do not relax confidential-group access, route by titles alone, use a denied group as a verified group, send sensitive sections to self, replay old finance refusals, or treat queued/test/deploy results as business completion. This approved scope is a message-audience repair, not a Consumer/Audit Spec development-status task or wholesale meeting-engine migration.
