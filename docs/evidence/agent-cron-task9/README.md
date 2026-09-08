# Task 9 visual acceptance evidence

Date: 2026-09-08

The page was served from the `agent-cron` worktree by an isolated `audit-web`
process at `http://127.0.0.1:62371/scheduled-tasks`. The production service was
not restarted.

## Screenshots

- `scheduled-tasks-dark-wide.png`: Google Chrome 1440 x 1000 with Chrome's
  `--force-dark-mode` media emulation. Verifies dark canvas/surface separation,
  primary and secondary text, card borders, active navigation, and the empty
  state.
- `scheduled-tasks-dark-390.png`: Google Chrome with a 390 x 844 CSS viewport
  and `prefers-color-scheme: dark`, both set through the Chrome DevTools
  Protocol. It verifies the complete wrapped heading copy, the new-task
  button's right rounded edge, both workspace borders, and the list-then-editor
  single column without hiding overflow.

## Repeatable 390 px check

With an isolated server running, execute:

```sh
cd frontend
npm run verify:scheduled-tasks-viewport -- \
  --url http://127.0.0.1:62371/scheduled-tasks \
  --screenshot ../docs/evidence/agent-cron-task9/scheduled-tasks-dark-390.png
```

The real-browser check fails unless the document scroll width is at most the
390 px viewport width and the header description, new-task button, and
workspace all remain within the 12 px page gutters. It also rejects horizontal
overflow clipping, so a passing result proves fit rather than masked content.

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
