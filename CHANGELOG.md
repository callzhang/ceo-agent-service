# Changelog

## Unreleased

### Audit reconciliation

- Registered the current direct-message send/read pair for audit reconciliation,
  while retaining target-scoped matching so a read from another recipient cannot
  confirm or suppress a pending delivery.
- Applied the same delivery-ledger recovery to persisted legacy direct-message
  actions, so a missing local delivery record requeues a new generation instead
  of attempting an unnecessary external reconciliation.
- Restored omitted direct-delivery ledger rows from the original Audit run's
  validated controlled receipt, while keeping recovery-only authorization
  checks restricted to recovery execution.
- Finalize an unknown Audit run from that restored ledger when it contains one
  matching direct-message action, avoiding a second model reconciliation or
  duplicate delivery; multi-action and non-direct work still requires normal
  reconciliation evidence.

### Material reading and local parsing

- Made the default downloaded-material reader detect OOXML workbooks by their
  file content, so extensionless downloads no longer fail after being treated
  as UTF-8 text.
- Added bounded PPTX text previews and fixed Audit parent validation after a
  retrying Consumer run, so a successful retry can continue into Audit.

### Skill-first Agent runtime

- Added seven distributable CEO business Skills for message triage, calendar invitations, document review,
  meeting work, mail review, personnel communication, and the combined task extraction/follow-up lifecycle.
- Consumer A now discovers and reads business and operation Skills dynamically. Audit B rereads the same
  Skills from verified completed tool-event receipts before reviewing or executing a candidate.
- The exact installed business-Skill catalog is now part of Consumer A's developer-level protocol, and a
  Consumer session rotates when that protocol changes. This prevents a turn from returning before any
  business Skill was read without introducing a service-side domain router.
- Corrected Consumer/Audit instructions to use the current nested proposal, feedback, external-result, and
  reconciliation fields while retaining the wire contract's top-level error fields. Audit results now fail
  closed when the required Consumer Skill receipts were not reread.
- Removed business keyword routing and service-side material interpretation from the documented architecture.
  The service transports references and exact read commands; Agents decide what evidence to read and how it
  affects the task.
- Kept OA, interview, and OKR logic in their existing specialist Skills instead of copying those rules into
  the CEO general prompt.
- Kept detailed Skill/tool audit in native Codex session JSONL and existing run/attempt/receipt persistence;
  no parallel Skill audit database is introduced.
- Added ownership-safe installation of the seven managed Skills under `~/.agents/skills`. User Skills under
  the same root are preserved, and no user Skills are installed under `~/.codex/skills`.
- Updated the native Skill-runtime fixtures to advertise their two MCP tools as read-only and non-destructive,
  and added live coverage for a three-message mail thread plus a full Consumer/Audit calendar dry-run.
- Expanded the sanitized Skill-runtime comparison matrix from 11 to 19 cases. The added cases cover
  authorized personnel delivery, the create/follow-up/complete work lifecycle, OA approve/return/reject
  decisions with applicant notification, and read-only recovery when an external side effect is unknown.
