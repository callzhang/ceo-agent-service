# Email candidate capacity evidence, 2026-10-15

The Email13 consolidation reduces table count but has **not demonstrated a
capacity reduction**. The full approved design remains ten core tables plus
one migration ledger. This candidate reaches 108 noninternal tables, not eleven.

These measurements came from owned offline copies of the production backup
observed at `fca229bb363e30ab20ff5440b59d2c41e5fc4c24` (Store19 / Email51).
They are immutable-source observations, not a measurement of current production.
The source backup SHA-256 is
`738843db8e228a09e5de9bcae923915f6e9429db65585cfbb293ad3bec3f63a0`.
Only aggregate page measurements are retained here; no message content or
trajectory is reproduced.

## File and active pages

| Copy | File/page bytes | Free-list bytes | Active bytes | Noninternal tables |
| --- | ---: | ---: | ---: | ---: |
| Original source | 481,296,384 | 36,864 | 481,259,520 | 121 |
| Paired target with controlled public-API acceptance writes | 503,554,048 | 0 | 503,554,048 | 108 |
| Genuine old-code current-facts donor | 503,525,376 | 22,450,176 | 481,075,200 | 121 |

The target file is 22,257,664 bytes larger than the original source. Comparing
active pages with the old-code current-facts donor gives an increase of
22,478,848 bytes. Thus the observed growth is not explained merely by free pages
in the old file. No VACUUM or new retention policy was applied.

The following breakdown compares the original source's existing `actions`
plus its thirteen Email action tables with the paired target's `actions`.
It is a source-versus-target page breakdown, distinct from the donor comparison
above; controlled acceptance writes and page layout affect the exact totals.

| Storage | Original actions plus Email13 | Target actions | Difference |
| --- | ---: | ---: | ---: |
| Table pages | 110,096,384 | 125,857,792 | +15,761,408 |
| Index pages | 19,783,680 | 26,157,056 | +6,373,376 |

JSON field names, scalar representations, discriminators, source-row locators,
record headers and page layout can contribute to table cost. These page
measurements do not isolate each cause. Do not attribute the complete increase
to one codec detail without a controlled comparison.

## Bounded duplicate-index opportunity

Read-only query/schema research identified nine per-family TEXT-primary-key
indexes whose uniqueness is already covered by the retained full
`UNIQUE(record_type,email_public_key)` index. Their measured pages total
2,433,024 bytes. Removing them requires routing only each family's own public
primary-key projection through the existing `email_public_key`; dropping them
alone loses complete-key lookup plans. Child reference projections must retain
their own existing fields.

| Index | Measured bytes |
| --- | ---: |
| idx_actions_email_email_action_plans_1 | 1,040,384 |
| idx_actions_email_email_actions_3 | 1,056,768 |
| idx_actions_email_email_unsubscribe_cancellations_0 | 4,096 |
| idx_actions_email_email_unsubscribe_claims_1 | 110,592 |
| idx_actions_email_email_unsubscribe_continuations_0 | 4,096 |
| idx_actions_email_email_unsubscribe_receipts_1 | 110,592 |
| idx_actions_email_email_feedback_requests_2 | 98,304 |
| idx_actions_email_email_reply_dispatch_claims_2 | 4,096 |
| idx_actions_email_email_reply_receipts_1 | 4,096 |

This is an identified opportunity, **not an implemented or accepted change**.
Even reclaiming all measured pages would recover only about 10.8 percent of
the donor active-page increase. It cannot establish an overall capacity gain.
Verification must preserve NULL/mutable public keys, TEXT/BLOB affinity,
BINARY collation, duplicate/ignore/update behavior, continuation UPSERT,
native source ROWID, typed FK rejection and complete-key lookup/CAS/join plans.
The current paired copy/inverse proof and page measurements must then be renewed.

Preserve the two full native FK-parent indexes:
`idx_actions_postfix_identity` and `idx_actions_email_public_key`. Native
in-memory probes confirmed that replacing either with a partial parent index
causes `foreign key mismatch`. No source fields, payload keys or FK policy
are proposed for removal by this index unit.

## Acceptance limits and next work

The four cases in the refreshed copy script returned successfully, but the
overall invocation exited at its final production-HEAD equality assertion:
production advanced to `633f5e59d8235826b747a2afc55b3217dd0d8611` during
the run. The script asserted before returning its result, so no final JSON was
written and the temporary proof copies were cleaned. This is **not an accepted
latest-source run**, and missing per-case report values must not be invented.
The report-only repair must retain completed case evidence, distinguish
`production_changed` from success, preserve the entry source-ref guard and
keep a nonzero CLI exit when production changes. Case assertion failures must
continue propagating.

CI 38692215658 at candidate `8ac15d2d` failed with 47 failures, 13,658 passes,
118 skips and 44 deselections. Source inspection maps 38 failures to Audit
fixtures already changed by incoming main633, eight to historical/current
schema test mismatches, and one to an unobserved rejected Linux SQL trace.
The local synthetic trace under SQLite3.53.4 had 726 statements and zero
rejections under the original whitelist; it does not prove the Linux case.
Do not widen that whitelist without identifying its rejected statements.

PR90 stays draft. Main integration, focused checks, copy/native/CI evidence,
publication and the full ten-table program are separate acceptance levels.
The remaining business/email/config/provider/state/schedule consolidation and
old-structure cleanup are still required. No main merge, deployment, real
provider sending or production database write occurred in this evidence unit.

## Refreshed production source at6c44

The read-only backup captured at2026-10-15T08:16:16.530378+00:00 bound exact
production6c44b082 before/after capture. Integrity and frozen source binding
passed:483487744bytes, Store19/Email51,121 noninternal tables, zero free-list
bytes and the unchanged1 FK finding. This is about483.5MB decimal. The prior
raw fca backup was removed after verification; only thin aggregate comparison
metadata remains beside the active source copy.

Compared with the earlier fca source, the file grew2191360bytes; active pages
grew2228224bytes because the earlier36864bytes of free pages were also used.
The source table-name set is identical. The old page report omitted the
zero-page codex_session_search_fts virtual-table declaration; its existence
and zero own/index pages were verified directly before comparison. Its shadow
table pages remain separately counted.

| Source table | Current rows | Current table plus index bytes | Increase in allocated bytes |
| --- | ---: | ---: | ---: |
| actions | 22,816 | 90,955,776 | 962,560 |
| tasks | 25,780 | 50,126,848 | 573,440 |
| managed_skill_revisions | 171 | 4,698,112 | 233,472 |
| runs | 229,837 | 124,080,128 | 204,800 |
| email_classifications | 8,911 | 39,759,872 | 57,344 |
| email_messages | 8,911 | 35,463,168 | 49,152 |
| email_action_plans | 9,945 | 15,032,320 | 40,960 |
| email_actions | 10,339 | 13,852,672 | 36,864 |
| email_provider_observations | 72,131 | 24,928,256 | 16,384 |
| runtime_skill_load_receipts | 4,087 | 4,481,024 | 16,384 |

These are measured source-page growth contributors, not proof that each new
row is necessary or that the candidate reduces storage. This new source has
not yet completed its candidate migration/capacity or native acceptance run.

## Latest readonly264 source: full per-table aggregate retained

The backup observed at2026-10-15T09:15:41.822422+00:00 has484073472bytes
(484.1MB decimal),290816free-list bytes and483782656active bytes. Production
HEAD was264c073c both before/after backup; Store19/Email51 and121noninternal
tables remain. This is a new source observation, not the candidate target size.
Full row/table/index counts for all121tables, including zero-own-page virtual
declarations and independently counted FTS shadow pages, are retained in
`docs/superpowers/specs/2026-10-15-database-redesign-264-source-pages.json`.
No page cost is allocated to record families by row share.

| Source table | Rows | Table bytes | Index bytes |
| --- | ---: | ---: | ---: |
| runs | 230,058 | 75,677,696 | 48,525,312 |
| actions | 22,875 | 80,068,608 | 11,198,464 |
| tasks | 25,791 | 43,556,864 | 6,283,264 |
| email_classifications | 8,919 | 36,532,224 | 3,239,936 |
| email_messages | 8,919 | 33,939,456 | 1,536,000 |
| email_provider_observations | 72,136 | 13,328,384 | 11,612,160 |
| email_action_plans | 9,953 | 13,737,984 | 1,302,528 |
| email_actions | 10,347 | 9,035,776 | 4,825,088 |
| work_updates | 5,468 | 12,115,968 | 249,856 |
| follow_up_drafts | 2,477 | 10,596,352 | 610,304 |
| errors | 15,380 | 7,561,216 | 950,272 |
| work_projects | 975 | 6,643,712 | 40,960 |

Account configuration is small: email_accounts still has2rows and8192combined
bytes. The72136email_provider_observations are per-message current facts, not
account settings; they occupy13328384table bytes plus11612160index bytes.
That distinction does not establish every index as necessary. The approved
provider duplicate-index work remains a separate measured optimization.

The source file grew565248bytes from the exact f264 source used by the completed
four-case proof. That source has been replaced after verifying the new backup;
its aggregate receipts remain. This report does not imply all retained records
are required, nor that reducing121to108tables saves capacity. The documented
Email target growth remains unresolved and the full ten-core-table-plus-ledger
goal remains open.

Bounded current-code scheduled-history findings are recorded separately in
`docs/database-redesign-scheduled-history-findings-20261015.md`. They identify
83610exact zero-work summaries as a classification cohort only, with concrete
source/recovery references that prevent equating the cohort to deletable rows.
No age cutoff, claim cleanup, history retirement or schema behavior was added.
