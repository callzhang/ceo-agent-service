# Workbench Centered Global Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Center the Workbench global tabs and make their visual treatment consistent with the existing green design system.

**Architecture:** Keep the current `GlobalNav` markup and accessibility contract. Express the layout and visual contract in the existing stylesheet, with a focused raw-CSS regression test beside the component test.

**Tech Stack:** React, TypeScript, CSS, Vitest, Testing Library

---

### Task 1: Define and implement the navigation style contract

**Files:**
- Modify: `frontend/src/components/GlobalNav.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write the failing test**

Import `styles.css?raw`, parse `.global-nav-track`, `.global-nav-item`, and `.global-nav-item.active`, and assert centered distribution, consistent item alignment/minimum width, and the shared `--accent` active color.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --run src/components/GlobalNav.test.tsx`

Expected: FAIL because the track does not justify content at center, the items have no shared minimum width, and the active background uses `--ink`.

- [ ] **Step 3: Write minimal implementation**

Add `justify-content: center` to `.global-nav-track`; add centered content and a shared `84px` minimum width to `.global-nav-item`; use `--accent` for the active background/border and `--accent-soft` for hover feedback.

- [ ] **Step 4: Run focused and full frontend verification**

Run:

```sh
npm test -- --run src/components/GlobalNav.test.tsx
npm test -- --run
npm run build
```

Expected: all tests pass and Vite produces a production bundle.

- [ ] **Step 5: Verify the live layout**

Build and deploy through the existing production checkout, restart `com.ceo-agent-service.main`, then measure that the rendered item span center matches the navigation center and verify all referenced assets return HTTP 200.

- [ ] **Step 6: Commit**

```sh
git add docs/superpowers/specs/2026-08-15-workbench-centered-global-tabs-design.md docs/superpowers/plans/2026-08-15-workbench-centered-global-tabs.md frontend/src/components/GlobalNav.test.tsx frontend/src/styles.css
git commit -m "fix: center workbench global tabs"
```
