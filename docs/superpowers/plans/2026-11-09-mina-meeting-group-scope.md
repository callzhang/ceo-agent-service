# Meeting business-group scope correction

Derek selected: preserve business-group delivery, correct the wrong-group judgment.

**Goal:** A department meeting summary reaches the group responsible for its work,
not a narrower recruiting discussion merely sharing a department keyword.

**Design:** Replace the loose meeting-specific group-selection wording with one
shared scope criterion used by both planner and independent Audit. Keep incomplete
attendee-roster tolerance, source-identified attendee direct delivery, sensitive
content policy, routes, models, retries, schemas, delivery and receipts unchanged.
No hardcoded department names, membership threshold or runtime gate.

- [x] Add and run a failing prompt-integration regression for both roles.
- [x] Replace existing selection wording with concrete group-purpose/whole-summary
  ownership criteria, requiring comparison even with a previously selected group.
- [x] Run focused meeting regressions and a fixed paired native no-tool evaluation:
  wrong specialist group, correct larger department group, incomplete roster,
  same-project different-topic group, and genuinely specialist meeting.
- [x] Update architecture/runtime behavior documents and investigation evidence.
- [ ] Create a PR containing fixed eval comparison; merge after verification.
- [ ] Standard deploy with its unmodified gate; verify PID, health, queues,
  Attention, History and production commit. Do not replay the completed meeting.
- [ ] Reply to Mina in the original private conversation; verify native delivery.
