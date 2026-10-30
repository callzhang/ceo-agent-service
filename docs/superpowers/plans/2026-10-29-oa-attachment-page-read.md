# OA attachment page reads

Repair the reproduced cache warm-up dependency in the existing read-only form/comment attachment tools. A page request identifies the same process and file used by the normal downloader; it must not require a preceding metadata call in this MCP process. Reuse the existing download/materialization path when no local material exists, as the existing Drive page reader already does. Keep all source identity, filename, byte/expanded limits, type, page range, SHA, rendering, provider errors and role boundaries. No new authorization, Audit, policy, retry or execution mechanism; no private business source/data in this public change.

### Task 1: Repair and publish exact-source page acquisition

**Validation:** focused reader/role files and existing fixed-commit deployment gate.

1. Add synthetic role-level regressions for first-page reads, new MCP process and changed display filename; both form/comment PDFs plus form PPTX. Expected RED: current page_source_unavailable.
2. Reuse existing exact source downloader on cache miss, then existing page renderer. Run new regressions GREEN and focused reader/role tests.
3. Document this read behavior with the code. Run focused checks; commit only owned hunks, push and use standard fixed-commit deploy gate. Do not run a full suite beside a live deployment.
4. Verify new process/health/queues/Attention/History and resume only source-valid unfinished work through existing reviewed/new-source entrypoints. Completed/canceled/terminated effects remain no-replay.

Observed verification:16new regressions failed against the original cache precondition; all16passed after repair. Focused reader/comment/role suite67passed. A fresh source review and normal publication/readback remain pending.
