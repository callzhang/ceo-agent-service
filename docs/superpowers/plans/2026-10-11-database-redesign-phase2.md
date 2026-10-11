# Database redesign Phase 2: canonical email action plans

Approved continuation of the phased storage redesign. Remove only the duplicate email_classifications.action_plan_json; current_action_plan_id references the existing canonical email_action_plans row. Preserve classification, authorization, plan revision, action execution and receipt semantics. No retention deadline or new recovery policy.

1. Implement schema48→49 in EmailStore: validate original snapshots before dropping the column transactionally. Read current plans in a batch for list APIs; remove current cache writers. Keep historical migration SQL necessary to reach49.
2. Implement a matched release inverse49→48: reconstruct the old cache from each current canonical plan, including legacy authorization serialization, in one transaction. Preserve post-migration business writes. Extend the existing deploy release mechanism; reject unverified transitions.
3. Run focused store/migration/API and release failure regression tests. Validate a consistent production copy with all-table digests excluding only removed cache/schema metadata, canonical/API equivalence, unchanged pre-existing FK violations, new writes surviving inverse and actual old48 startup.
4. Independently review migration/inverse and acceptance evidence. Deploy the pushed commit using app.deploy from the updated worktree, then read back code/PID/schema, health, queues, Attention/History, integrity and latest verified backup.
5. Record concise acceptance evidence and remove task-owned temporary databases/code. No live VACUUM in this phase.
