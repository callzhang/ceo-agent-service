# Tasks Responsive Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate page-level horizontal overflow on the Tasks console while preserving a dense wide-screen layout, a two-row medium layout, and the existing mobile cards.

**Architecture:** Keep the existing React DOM and behavior unchanged and implement the repair as a CSS-only responsive layer. Add one focused raw-stylesheet contract test that locks breakpoint ordering, filter layout, compact navigation, and the Sent TODO overflow boundary, then validate the compiled production UI in four real browser viewports.

**Tech Stack:** React 19, TypeScript, Vitest, Vite, CSS media queries, browser geometry inspection

---

## File map

- Create `frontend/src/styles.tasks-responsive.test.ts`: focused responsive CSS contract tests.
- Modify `frontend/src/styles.css`: add the 721-1100 px medium breakpoint before the existing 720 px mobile breakpoint.
- Modify `CHANGELOG.md`: record the user-visible overflow repair and responsive behavior.

No component, API, backend, task-data, or URL-state file should change.

### Task 1: Lock the responsive layout contract

**Files:**
- Create: `frontend/src/styles.tasks-responsive.test.ts`

- [ ] **Step 1: Write the failing stylesheet contract test**

```ts
import { describe, expect, it } from "vitest";

import workbenchStyles from "./styles.css?raw";

function mediumStyles() {
  const start = workbenchStyles.indexOf("@media (min-width: 721px) and (max-width: 1100px)");
  const end = workbenchStyles.indexOf("@media (max-width: 720px)", start);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  return workbenchStyles.slice(start, end);
}

describe("Tasks responsive layout contract", () => {
  it("keeps wide filters dense and introduces the medium breakpoint before mobile", () => {
    expect(workbenchStyles).toMatch(
      /\.filter-bar\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/,
    );
    expect(mediumStyles()).toMatch(
      /\.filter-bar\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/,
    );
  });

  it("uses four main controls on row one and right-aligns page metadata on row two", () => {
    const styles = mediumStyles();
    expect(styles).toMatch(
      /\.filter-bar > \.filter-bar-main\s*\{[^}]*grid-template-columns:\s*minmax\(220px, 1\.6fr\) repeat\(3, minmax\(120px, 0\.85fr\)\);/,
    );
    expect(styles).toMatch(
      /\.filter-bar > \.filter-bar-side\s*\{[^}]*justify-content:\s*flex-end;/,
    );
  });

  it("compacts every navigation destination without hiding one", () => {
    const styles = mediumStyles();
    expect(styles).toMatch(/\.global-brand\s*\{[^}]*min-width:\s*156px;/);
    expect(styles).toMatch(/\.global-nav-track\s*\{[^}]*gap:\s*4px;[^}]*padding:\s*8px 10px;/);
    expect(styles).toMatch(/\.global-nav-item\s*\{[^}]*min-width:\s*72px;[^}]*padding:\s*0 9px;/);
  });

  it("contains the wide Sent TODO table inside its own scroll region", () => {
    expect(mediumStyles()).toMatch(
      /\.sent-todos-table-wrap\s*\{[^}]*max-width:\s*100%;[^}]*overflow-x:\s*auto;/,
    );
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm run test --prefix frontend -- --run src/styles.tasks-responsive.test.ts
```

Expected: FAIL because the 721-1100 px media query does not exist.

- [ ] **Step 3: Commit only the failing test**

```bash
git add frontend/src/styles.tasks-responsive.test.ts
git commit -m "test(ui): lock tasks responsive layout contract"
```

### Task 2: Implement the medium-width CSS layer

**Files:**
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/styles.tasks-responsive.test.ts`

- [ ] **Step 1: Add the medium breakpoint immediately before the first existing mobile breakpoint**

```css
@media (min-width: 721px) and (max-width: 1100px) {
  .filter-bar {
    grid-template-columns: minmax(0, 1fr);
    align-items: stretch;
  }

  .filter-bar > .filter-bar-main {
    grid-template-columns: minmax(220px, 1.6fr) repeat(3, minmax(120px, 0.85fr));
  }

  .filter-bar > .filter-bar-side {
    justify-content: flex-end;
  }

  .global-brand {
    min-width: 156px;
    padding: 10px 14px;
  }

  .global-brand strong { font-size: 0.9rem; }
  .global-brand small { font-size: 0.66rem; }

  .global-nav-track {
    gap: 4px;
    padding: 8px 10px;
  }

  .global-nav-item {
    min-width: 72px;
    padding: 0 9px;
    font-size: 12px;
  }

  .sent-todos-table-wrap {
    max-width: 100%;
    overflow-x: auto;
    overscroll-behavior-inline: contain;
  }
}
```

- [ ] **Step 2: Run the focused test and verify GREEN**

Run:

```bash
npm run test --prefix frontend -- --run src/styles.tasks-responsive.test.ts
```

Expected: 1 test file and 4 tests pass.

- [ ] **Step 3: Run Tasks and navigation regressions**

Run:

```bash
npm run test --prefix frontend -- --run src/pages/TasksPage.test.tsx src/components/GlobalNav.test.tsx src/styles.tasks-responsive.test.ts
```

Expected: all selected tests pass, including the existing task API, URL pagination, destination, and active-route assertions.

- [ ] **Step 4: Commit the minimal implementation**

```bash
git add frontend/src/styles.css
git commit -m "fix(ui): make tasks layout responsive at medium widths"
```

### Task 3: Validate the full frontend and production bundle

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the complete frontend suite**

Run `npm run test:workbench`.

Expected: all frontend test files pass; the known skipped tests remain skipped.

- [ ] **Step 2: Build the production frontend**

Run `npm run build:workbench`.

Expected: TypeScript reports no errors and Vite produces `frontend/dist` successfully.

- [ ] **Step 3: Add the changelog entry directly below the title**

```markdown
- 2026-09-13: the Tasks console now preserves its dense one-row filters on
  wide screens and switches to a two-row filter layout from 721px through
  1100px. The same breakpoint compacts every top-level navigation destination,
  and the intentionally wide Sent TODOs table owns its horizontal scroll, so
  1024px and tablet viewports no longer make the whole page scroll sideways.
```

- [ ] **Step 4: Run formatting and scoped change checks**

Run `git diff --check` and `git status --short`.

Expected: no whitespace errors; only task-owned files are staged. The existing
`data/config/service-mcp.json` and meeting-export scripts remain unstaged.

- [ ] **Step 5: Commit the changelog**

```bash
git add CHANGELOG.md
git commit -m "docs(ui): record tasks responsive repair"
```

### Task 4: Verify the live UI at every accepted viewport

**Files:**
- Verify only; no planned source changes.

- [ ] **Step 1: Rebuild the served assets and refresh the local service**

Use the repository's established production frontend build/deployment path.
Restart only the affected local service if it does not serve rebuilt assets
automatically. Do not change task data or service config.

Expected: `http://127.0.0.1:8765/tasks` returns HTTP 200 with the new asset hash.

- [ ] **Step 2: Verify the wide viewport at 1600 x 900**

Inspect:

```js
({
  innerWidth: window.innerWidth,
  documentWidth: document.documentElement.scrollWidth,
  filterRows: new Set(
    [...document.querySelectorAll(".filter-bar .filter-field")]
      .map((node) => Math.round(node.getBoundingClientRect().top)),
  ).size,
  navLinks: document.querySelectorAll(".global-nav-item").length,
})
```

Expected: `documentWidth === innerWidth`, `filterRows === 1`, and seven navigation links are present.

- [ ] **Step 3: Verify medium viewports at 1024 x 768 and 768 x 1024**

Run the same inspection and additionally inspect:

```js
({
  sentWrapperClientWidth: document.querySelector(".sent-todos-table-wrap")?.clientWidth,
  sentWrapperScrollWidth: document.querySelector(".sent-todos-table-wrap")?.scrollWidth,
  bodyOverflow: document.documentElement.scrollWidth > window.innerWidth,
})
```

Expected: no document overflow; the filters use two top positions; seven navigation destinations remain visible; Sent TODO excess width belongs to its wrapper.

- [ ] **Step 4: Verify the mobile viewport at 390 x 844**

Expected: no document overflow; filters are single-column; Tasks rows use the existing labeled-card presentation; any navigation scrolling is confined to the nav track.

- [ ] **Step 5: Exercise behavior, not only geometry**

At 1024 x 768, change type, state, sort, and page size; use next-page navigation; open a task detail and return. Confirm URL parameters and task data remain correct. At 390 x 844, keyboard-tab through every filter and the first task link to confirm focusability and DOM order are unchanged.

- [ ] **Step 6: Run final verification and preserve the known baseline boundary**

Run:

```bash
npm run test:workbench
npm run build:workbench
git diff --check
git status --short
```

Expected: frontend suite and production build pass. Report separately that the repository-level `npm test` was blocked before implementation by 63 unrelated Python Ruff findings; do not claim that gate is green unless a fresh run proves it.
