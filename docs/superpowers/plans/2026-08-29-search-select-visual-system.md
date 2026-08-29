# Search and Select Visual System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace inconsistent search inputs, native-looking selects, and status filters with a shared B+C visual system across the React console without changing query parameters or filtering behavior.

**Architecture:** Add small presentational controls (`SearchField`, `SelectField`, `FilterBar`, `FilterChip`) under `frontend/src/components/filters/`. Page components remain responsible for URL state, loading, and data fetching; shared controls only emit `onChange`/`onClear`/`onSelect`. `FilterBar` supplies the control-bar layout, while `FilterChip` handles finite status shortcuts.

**Tech Stack:** React 19, TypeScript, React Router URL search params, CSS in `frontend/src/styles.css`, Vitest + Testing Library, native HTML input/select semantics.

---

### Task 1: Add shared SearchField and SelectField controls

**Files:**
- Create: `frontend/src/components/filters/SearchField.tsx`
- Create: `frontend/src/components/filters/SelectField.tsx`
- Create: `frontend/src/components/filters/FilterBar.tsx`
- Test: `frontend/src/components/filters/filters.test.tsx`

- [ ] **Step 1: Write failing component tests**

Test `SearchField` with an empty value (no clear button), a populated value (clear button with accessible name), and `Escape` invoking `onClear`. Test `SelectField` preserves the selected value, label, and native select role. Test `FilterBar` renders its children in the shared `filter-bar` wrapper.

- [ ] **Step 2: Run the component tests and verify the expected failure**

Run: `pnpm --dir frontend test --run src/components/filters/filters.test.tsx`

Expected: FAIL because the shared components do not exist yet.

- [ ] **Step 3: Implement the minimal controls**

`SearchField` must render a labelled `<input type="search">`, a decorative search icon, and a clear button only when `value` is non-empty. `SelectField` must render a visible label and native `<select>` with passed options/value. `FilterBar` must render `<div className="filter-bar">` and accept `children`.

- [ ] **Step 4: Run the component tests and verify they pass**

Run: `pnpm --dir frontend test --run src/components/filters/filters.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit the shared controls**

```bash
git add frontend/src/components/filters
git commit -m "feat: add shared search and select controls"
```

### Task 2: Add unified B+C styling and status chips

**Files:**
- Create: `frontend/src/components/filters/FilterChip.tsx`
- Modify: `frontend/src/components/filters/filters.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing FilterChip tests**

Test that a chip exposes `aria-pressed="true"` when active, calls `onClick` once, and renders an optional count. Test inactive chips remain keyboard buttons and do not use color-only state.

- [ ] **Step 2: Run the focused test to verify failure**

Run: `pnpm --dir frontend test --run src/components/filters/filters.test.tsx`

Expected: FAIL because `FilterChip` and the new selectors are missing.

- [ ] **Step 3: Implement FilterChip and CSS tokens**

Add `.filter-bar`, `.filter-search`, `.filter-select`, `.filter-chip-list`, and `.filter-chip` styles. Use the existing `--line`, `--line-strong`, `--surface`, `--surface-muted`, `--accent`, `--danger`, and `--focus` variables. Make control height at least 40px, provide visible focus rings, place the clear button inside the search field, style a custom arrow around the native select, and stack the bar/chips at the existing mobile breakpoint.

- [ ] **Step 4: Run the focused test to verify green**

Run: `pnpm --dir frontend test --run src/components/filters/filters.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit the visual primitives**

```bash
git add frontend/src/components/filters/FilterChip.tsx frontend/src/components/filters/filters.test.tsx frontend/src/styles.css
git commit -m "feat: style shared filter controls"
```

### Task 3: Migrate Agent, Tasks, and History toolbars

**Files:**
- Modify: `frontend/src/pages/TasksPage.tsx`
- Modify: `frontend/src/components/TaskList.tsx`
- Modify: `frontend/src/pages/HistoryPage.tsx`
- Modify: `frontend/src/pages/TasksPage.test.tsx`
- Modify: `frontend/src/components/TaskList.test.tsx`
- Modify: `frontend/src/pages/HistoryPage.test.tsx`

- [ ] **Step 1: Add page-level failing assertions**

Assert that Agent, Tasks, and History render `filter-bar`/shared search field, labelled selects, and status chips where applicable. Assert their existing URL keys (`q`, `category`, `task_state`, `sort`, `status`, `object_type`, `page_size`) remain unchanged after input/select/chip changes, while Agent’s local task search still filters the same list.

- [ ] **Step 2: Run page tests and verify failure**

Run: `pnpm --dir frontend test --run src/components/TaskList.test.tsx src/pages/TasksPage.test.tsx src/pages/HistoryPage.test.tsx`

Expected: FAIL on the new class/role assertions while existing query behavior remains green.

- [ ] **Step 3: Replace inline controls with shared controls**

Use `FilterBar` for the current toolbars. Use `SearchField` for Agent’s task search and each page’s `q`, `SelectField` for existing type/state/sort/page-size controls, and `FilterChip` only for finite status shortcuts. Keep each page’s `update()` function and URL names exactly as-is. Do not move data loading into shared controls.

- [ ] **Step 4: Run page tests and verify green**

Run: `pnpm --dir frontend test --run src/components/TaskList.test.tsx src/pages/TasksPage.test.tsx src/pages/HistoryPage.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit the page migration**

```bash
git add frontend/src/components/TaskList.tsx frontend/src/components/TaskList.test.tsx frontend/src/pages/TasksPage.tsx frontend/src/pages/HistoryPage.tsx frontend/src/pages/TasksPage.test.tsx frontend/src/pages/HistoryPage.test.tsx
git commit -m "feat: unify tasks and history filters"
```

### Task 4: Migrate Feedback, Attention, and WeChat search/filter controls

**Files:**
- Modify: `frontend/src/pages/FeedbackPage.tsx`
- Modify: `frontend/src/pages/AttentionPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/FeedbackPage.test.tsx`
- Modify: `frontend/src/pages/AttentionPage.test.tsx`
- Modify: `frontend/src/pages/SettingsPage.test.tsx`

- [ ] **Step 1: Add failing page assertions**

Assert shared search/select classes and accessible labels on Feedback, Attention, and WeChat connector search. Assert that Attention status chips preserve its status query if a status filter exists, and WeChat search still calls `listWechatTargets` with the same `query/kind/limit` values.

- [ ] **Step 2: Run tests and verify expected failure**

Run: `pnpm --dir frontend test --run src/pages/FeedbackPage.test.tsx src/pages/AttentionPage.test.tsx src/pages/SettingsPage.test.tsx`

Expected: FAIL only on the new shared-control assertions.

- [ ] **Step 3: Migrate controls without changing data semantics**

Use `FilterBar` in Feedback and Attention where controls exist. Use `SearchField` for the WeChat target query while preserving its explicit search button and loading/error state. Keep Attention’s red count badge and existing card expansion untouched. Use `FilterChip` for finite status shortcuts only; do not add a new API or hidden filter.

- [ ] **Step 4: Run tests and verify green**

Run: `pnpm --dir frontend test --run src/pages/FeedbackPage.test.tsx src/pages/AttentionPage.test.tsx src/pages/SettingsPage.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit the remaining page migration**

```bash
git add frontend/src/pages/FeedbackPage.tsx frontend/src/pages/AttentionPage.tsx frontend/src/pages/SettingsPage.tsx frontend/src/pages/FeedbackPage.test.tsx frontend/src/pages/AttentionPage.test.tsx frontend/src/pages/SettingsPage.test.tsx
git commit -m "feat: unify feedback attention and connector filters"
```

### Task 5: Verify responsive visual behavior and regressions

**Files:**
- Modify only if needed: `frontend/src/styles.css` and affected component/page tests

- [ ] **Step 1: Run all frontend tests**

Run: `pnpm --dir frontend test --run`

Expected: all frontend test files pass.

- [ ] **Step 2: Build the production bundle**

Run: `pnpm --dir frontend build`

Expected: TypeScript check and Vite build succeed.

- [ ] **Step 3: Inspect desktop and mobile layouts**

Use the browser at 1280px and 390px on Tasks, History, Feedback, Attention, Settings/Configuration, and Connectors/WeChat. Verify no page-level horizontal overflow, 40px minimum touch targets, readable labels, visible focus, and that long search text does not push the clear button out of the field.

- [ ] **Step 4: Check final diff**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; unrelated pre-existing worktree changes remain untouched.
