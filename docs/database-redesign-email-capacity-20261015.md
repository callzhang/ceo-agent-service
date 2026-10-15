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
