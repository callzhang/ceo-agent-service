# Task 9 visual acceptance evidence

Date: 2026-09-08

The page was served from the `agent-cron` worktree by an isolated `audit-web`
process at `http://127.0.0.1:62371/scheduled-tasks`. The production service was
not restarted.

## Screenshots

- `scheduled-tasks-dark-wide.png`: Google Chrome with a 1440 x 1000 CSS
  viewport and `prefers-color-scheme: dark`, both set through the Chrome
  DevTools Protocol. It verifies dark canvas/surface separation, primary and
  secondary text, card borders, active navigation, and the empty state.
- `scheduled-tasks-dark-390.png`: Google Chrome with a 390 x 844 CSS viewport
  and `prefers-color-scheme: dark`, both set through the Chrome DevTools
  Protocol. It verifies the complete wrapped heading copy, the new-task
  button's right rounded edge, both workspace borders, and the list-then-editor
  single column without hiding overflow.
- `scheduled-tasks-dark-390-bottom.png`: the same 390 px browser after opening
  the long new-task editor and scrolling to the document bottom. It verifies
  that the form controls, disabled/error state, and canvas below the form stay
  dark for the full scrollable document.

## Repeatable 390 px check

With an isolated server running, execute:

```sh
cd frontend
npm run verify:scheduled-tasks-viewport -- \
  --url http://127.0.0.1:62371/scheduled-tasks \
  --width 390 --height 844 \
  --screenshot ../docs/evidence/agent-cron-task9/scheduled-tasks-dark-390.png \
  --bottom-screenshot ../docs/evidence/agent-cron-task9/scheduled-tasks-dark-390-bottom.png
```

The real-browser check fails unless the document scroll width is at most the
390 px viewport width and the header description, new-task button, and
workspace all remain within the 12 px page gutters. It also rejects horizontal
overflow clipping, so a passing result proves fit rather than masked content.
The same check asserts that the Scheduled Tasks route computed the scoped dark
color scheme. Other routes retain the existing light tokens even when the
operating system is dark.

For the bottom check, the script opens the new-task editor, confirms that the
page is taller than the viewport, scrolls to the actual document bottom, and
requires the Scheduled Tasks route to cover that full document height. The
bottom pixel must resolve to a dark background and the expanded editor must
still have no horizontal document overflow. The captured run measured a
1423 px document, `scrollY` 579 px, route bottom 1422.95 px, and bottom canvas
`rgb(17, 20, 17)`.

The screenshot is first held in memory, then written to a temporary file in
the evidence directory and atomically renamed only after every assertion
passes. Chrome receives `SIGTERM` and must close before its temporary profile
is removed; a bounded timeout escalates to `SIGKILL`. The helper behavior is
covered by `npm run test:viewport-script --prefix frontend`.

## Interactive browser checks

The same isolated page was also opened through the real Chrome UI. The first
task editor was opened without saving. The following states were inspected:

- visible keyboard focus around the create button and task description;
- the unavailable Runtime explanation and disabled create action;
- the Skill suggestion panel after entering `$dingtalk`;
- form, panel, and suggestion boundaries at the wide viewport.

The backing database had no Scheduled Tasks and no available operation Skill
matching `$dingtalk`, so the interaction intentionally exercised the empty
suggestion result rather than creating or mutating production data. Dark
surface selection is covered by the Chrome media-emulated screenshots and the
static token contract in `frontend/src/styles.dark-mode.test.ts`.
