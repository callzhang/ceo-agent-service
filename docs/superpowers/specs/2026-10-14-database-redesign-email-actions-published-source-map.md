# Published Email action source binding — 2026-10-14

The companion [metadata capture](2026-10-14-database-redesign-email-actions-published-source.json) contains all thirteen literal table declarations, column/index/FK/trigger metadata, the old sixteen-type `actions` object manifest, thirteen external triggers, twelve inbound FKs, native sequences, row counts, and per-column storage-class/population/byte aggregates. It contains no business row values, row digests, credentials, or trajectory.

Production checkout `eb797e6bd35de695c269054a2e697af8fde2baa5` was unchanged before and after the one SQLite `mode=ro`, `query_only=ON` deferred read transaction. The database reported Store `2026-10-13.5`, Email `51`, and 122 `sqlite_schema` table entries including internal `sqlite_sequence` (121 user tables). This checkout is newer than the initial E receipt `5691f6c7`; the binding rests on actual database metadata, rather than that earlier Git observation.

| Source table | Columns | Rows | `sqlite_sequence` | Indexes | Own triggers |
|---|---:|---:|---:|---:|---:|
| `email_action_plans` | 14 | 9,856 | none | 2 | 0 |
| `email_actions` | 18 | 10,245 | none | 4 | 2 |
| `email_action_attempts` | 10 | 12,241 | 12,441 | 1 | 0 |
| `email_unsubscribe_cancellations` | 2 | 1 | none | 1 | 0 |
| `email_unsubscribe_claims` | 19 | 1,082 | none | 2 | 0 |
| `email_unsubscribe_continuations` | 6 | 0 | none | 1 | 0 |
| `email_unsubscribe_effects` | 6 | 1,169 | none | 1 | 0 |
| `email_unsubscribe_receipts` | 20 | 1,082 | none | 2 | 0 |
| `email_unsubscribe_steps` | 8 | 823 | 823 | 1 | 0 |
| `email_direct_retry_requests` | 5 | 1 | none | 1 | 3 |
| `email_feedback_requests` | 6 | 1,413 | none | 3 | 0 |
| `email_reply_dispatch_claims` | 17 | 0 | none | 3 | 0 |
| `email_reply_receipts` | 13 | 0 | none | 2 | 0 |

All 144 columns and thirteen complete declarations match the frozen [nine-table source](2026-10-13-database-redesign-email-actions-source.json) and [four-child extension](2026-10-13-database-redesign-email-action-children-source.json). The published `actions` manifest exactly matches the default sixteen-type `actual_no_memory_fk` declaration. It holds 22,291 rows across fifteen populated types; sixteen types are allowed. The disabled Email29 and follow-up38 candidates were not selected in this production source.

The actual `email_unsubscribe_claims` order is `claimed_at` at zero-based column 16, `updated_at` 17, `audit_agent_run_id` 18. The frozen fresh variant puts `audit_agent_run_id` at 16, with correspondingly different index `cid` positions. It remains a fixture variant, not the observed published one. The row counts are current aggregate observations; the 122-entry count includes SQLite's internal sequence table and is consistent with the earlier 121-user-table receipt. No Email source declaration or old-action manifest drift was found.

All thirteen external account/message/classification blockers and twelve inbound FKs matched in the same transaction. The two action-count triggers and three immutable retry triggers are in the captured own objects. Source-only tests exercise nullable public TEXT keys, ordinary rowid independence and edge values, BLOB public keys, immediate FKs, duplicate replacement refusal, both count triggers, all thirteen blockers, and independent feedback key semantics. This evidence does not assign a target version or establish migration, inverse, old-code, provider, or UI acceptance.
