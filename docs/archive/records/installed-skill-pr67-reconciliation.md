# Installed Skill PR67 reconciliation

Snapshot: 2026-10-15T06:52:26.479877+00:00. This follows the worktree-owner handoff while the trajectory/source-loading goal remains active.

## Preserved state

PR67 is OPEN/DRAFT, head codex/installed-skill-on-demand at1564929411432db320ae0b64769e2526bad1e897. Local and remote branch refs agree. Eleven commits remain outside current main, including historical native controls and source/configuration evidence. The app still lists an active attachment for /Users/derek/.codex/worktrees/installed-skill-on-demand/ceo-agent-service, but that path does not exist and git worktree list contains no checkout on that branch. This does not establish how the original checkout disappeared or whether it had uncommitted files. This task did not delete, archive, restore or merge it, and does not certify it clean. The branch, PR and all committed evidence remain preserved.

## Original patch versus authoritative current main

The original runtime patch a738493c replaces two installed-discovery sentences that required every selected entry and every named dependency with applicability-based wording. Three-dot PR diff alone still shows that historical change. Current main already removed those longer instructions in3f00ea6d (prompt: compact task Skill entries and show exact frozen source identity). Its renderer now supplies compact metadata, exact frozen identity and the two read entrypoints without the old unconditional installed-body instruction. Thus the old two-line hunk is superseded; merging the old whole renderer would also replace newer frozen-source metadata/reuse behavior. Do not merge it to preserve historical test evidence.

Unique committed evidence remains useful and is reachable from the PR refs: unowned synthetic OA case Consumer reads7->1 while Audit does not establish a controlled saving; owned OA case preserves correct proposal but all four turns read7 bodies. Those completed cases do not prove stable read reduction, real installed-body/Profile acceptance or complete source-version consistency. No repeated experiment is launched to manufacture a saving.

## Next mechanism requiring verification

Current app/store.py scheduled-task validation and app/web_api/scheduled_tasks.py service-command validation still require Skill refs to exactly equal ordered dollar-name references in the prompt. As a result available frozen sources and explicitly requested workflows remain coupled. A future candidate should preserve all original bound/frozen sources and existing read-only query entrypoints while testing whether the prompt can request only the applicable workflow. Source membership, effective loaded rules and same-session version meaning must remain verified; existing business policy and old input snapshots are retained. This requires its own implementation, regressions and fixed-case acceptance before publication.

This reconciliation reports disposition only: preserve the unaccepted branch/PR and historical evidence; no claim that PR67 has been accepted or is safe to delete. No message was sent back to the cleanup chat without separate human authorization.
