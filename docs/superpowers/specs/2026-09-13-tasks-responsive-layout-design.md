# Tasks Responsive Layout Design

**Date:** 2026-09-13
**Status:** Approved for implementation

## Problem and evidence

The production Tasks page is correct at 1600 x 900, but at 1024 x 768 the
document becomes wider than the viewport. Browser inspection measured the
filter bar and task table extending to approximately 1190 px from a 46 px left
edge, which produces page-level horizontal scrolling and clips the right side
of the navigation, filters, pagination, and task columns.

The task table already uses fixed layout at 100% width and can shrink with its
container. The primary overflow source is the filter bar: its wide-screen outer
grid, four minimum-width main controls, page-size control, and total count stay
on one row. The global navigation also keeps a 230 px brand block and seven
minimum-width destinations on one row at this width, leaving too little margin
for longer labels.

## Goals

- Preserve the current dense single-row layout on wide screens.
- Use a two-row filter layout at medium widths without removing or truncating
  controls.
- Preserve the existing mobile card presentation below 721 px.
- Keep every global navigation destination directly visible at medium widths.
- Prevent page-level horizontal scrolling at the accepted viewport sizes.
- Keep the intentionally wide Sent TODOs table scrollable inside its own
  container.
- Make no API, data, URL-parameter, filtering, sorting, or pagination changes.

## Selected responsive behavior

The approved direction combines option B on wide screens with option A at
narrower widths.

### Wide screens: 1101 px and above

Keep the current high-density presentation:

- search, type, state, sort, page size, and total remain on one row;
- all six task columns remain visible;
- the brand and all seven navigation destinations remain on one row.

### Medium screens: 721 px through 1100 px

Switch only the layout, not the controls or their order:

- the filter bar becomes one outer column;
- search, type, state, and sort stay together on its first row;
- page size and total move to a second row aligned to the end;
- the six-column Tasks table continues to fit the workspace width;
- the global brand, destination minimum widths, padding, and gaps become more
  compact so all seven destinations remain directly visible;
- no More menu is introduced, because hiding existing destinations would add
  an interaction cost unrelated to the overflow repair.

### Mobile screens: 720 px and below

Preserve the existing mobile behavior:

- one filter control per row;
- the Tasks table becomes labeled task cards;
- the brand occupies its own row and the destination row may scroll within its
  own container;
- the Sent TODOs table becomes its existing labeled card list.

The medium-width rules must be declared before the existing 720 px rules so
the mobile rules remain the final override.

## Overflow boundaries

The document and page workspace must not acquire horizontal overflow. The main
Tasks table remains `width: 100%` and shrinks with its parent.

The Sent TODOs table intentionally retains its 1456 px intrinsic width on wide
and medium screens. Its `.sent-todos-table-wrap` container owns horizontal
scrolling. This local scroll area must not increase `document.scrollWidth`.
Below 721 px, the existing card conversion removes that local horizontal
scroll.

## Accessibility and behavior preservation

Responsive changes are CSS-only. Existing field labels, DOM order, focus order,
accessible names, links, URL search parameters, and pagination actions remain
unchanged. Controls must not be hidden solely to make the layout fit.

## Test strategy

1. Add a focused stylesheet contract test for the medium breakpoint. It must
   cover the two-row filter arrangement, compact global navigation, and local
   Sent TODO overflow ownership.
2. Run the existing Tasks page component tests to prove filtering, table,
   links, and server pagination behavior remain intact.
3. Build the production frontend bundle.
4. Verify the live page at 1600 x 900, 1024 x 768, 768 x 1024, and 390 x 844.
5. At each viewport, compare `document.documentElement.scrollWidth` with
   `window.innerWidth`. They must be equal. At medium widths, confirm the Sent
   TODO wrapper can scroll independently when rows exist.
6. Confirm the wide view remains single-row, the medium view uses two rows, and
   the mobile view uses cards.

## Acceptance criteria

- 1600 x 900 retains the current dense layout and has no page-level horizontal
  scroll.
- 1024 x 768 and 768 x 1024 use the two-row filter layout and have no
  page-level horizontal scroll.
- 390 x 844 retains the existing mobile card layout and has no page-level
  horizontal scroll.
- All seven navigation destinations are visible at medium widths.
- Search, type, state, sort, page size, total, pagination, and all six Tasks
  fields remain available.
- Sent TODOs horizontal overflow is contained by its own wrapper on medium and
  wide screens.
- Existing frontend tests and the production build pass.

## Non-goals

- Redesigning task data, column semantics, pagination, or API requests.
- Adding a navigation drawer or More menu.
- Changing other console pages beyond shared filter and navigation behavior
  required to prevent the same overflow.
- Replacing the established React/Vite/CSS architecture or adding a component
  library.
