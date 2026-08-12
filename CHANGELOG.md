# Changelog

## Unreleased

### Skill-first Agent runtime

- Added seven distributable CEO business Skills for message triage, calendar invitations, document review,
  meeting work, mail review, personnel communication, and the combined task extraction/follow-up lifecycle.
- Consumer A now discovers and reads business and operation Skills dynamically. Audit B rereads the same
  Skills from verified completed tool-event receipts before reviewing or executing a candidate.
- Removed business keyword routing and service-side material interpretation from the documented architecture.
  The service transports references and exact read commands; Agents decide what evidence to read and how it
  affects the task.
- Kept OA, interview, and OKR logic in their existing specialist Skills instead of copying those rules into
  the CEO general prompt.
- Kept detailed Skill/tool audit in native Codex session JSONL and existing run/attempt/receipt persistence;
  no parallel Skill audit database is introduced.
- Added ownership-safe installation of the seven managed Skills under `~/.agents/skills`. User Skills under
  the same root are preserved, and no user Skills are installed under `~/.codex/skills`.
