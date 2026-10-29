# Current28 capacity attribution, 2026-10-17

This is a derivative analysis of the accepted Feedback actual-copy receipt, not a fresh production measurement or another migration. The receipt SHA256 is `8226a7f54fe40c8b0e3ad76dab8184d1ce331027399f66b7a6df6606b352961b`; its source was actual production commit83c1d7f0 with116 ordinary tables. The first target has28 ordinary tables and precedes synthetic current writes. Source/target refer to the same consistent copied dataset, with the receipt's14-family fact coverage and whole-schema boundary; not all116 rowsets were compared. Decimal MB below means1,000,000 bytes.

The first target is **58,372,096 bytes larger**:501,903,360→560,275,456. Both have zero freelist pages. Reducing table count has not yet achieved the capacity objective. No reclaimable-free-page claim or speculative retention policy follows from this result.

## Exact additive growth

| Page category | Source bytes | Target bytes | Change bytes | Change MB |
|---|---:|---:|---:|---:|
| Table data pages | 399,974,400 | 440,070,144 | +40,095,744 | +40.096 |
| Index pages | 101,507,072 | 118,272,000 | +16,764,928 | +16.765 |
| SQLite schema pages | 421,888 | 1,933,312 | +1,511,424 | +1.511 |

These three differences account for all growth. Index pages account for28.72% of the increase, table pages68.69%, and schema pages2.59% (rounded). Freelist reclamation alone cannot address this increase. `dbstat.unused` describes unused space inside active pages and is not an estimate of bytes VACUUM will save.

## Largest target containers including their indexes

| Container | Total MB | Table/internal MB | Index MB | Unused inside active pages MB |
|---|---:|---:|---:|---:|
| `actions` | 208.490 | 176.374 | 32.117 | 34.660 |
| `runs` | 135.160 | 83.534 | 51.626 | 9.948 |
| `tasks` | 71.954 | 61.080 | 10.875 | 7.429 |
| `email_classifications` | 40.710 | 37.310 | 3.400 | 11.881 |
| `email_messages` | 36.336 | 34.726 | 1.610 | 4.729 |
| `provider_state` | 27.140 | 14.361 | 12.780 | 1.904 |
| `business` | 11.551 | 10.428 | 1.122 | 1.455 |
| `state` | 9.273 | 6.562 | 2.712 | 0.625 |
| `configs` | 8.724 | 7.344 | 1.380 | 0.623 |
| `business_source_documents` | 7.365 | 7.340 | 0.025 | 0.132 |
| `sqlite_schema` | 1.933 | 1.933 | 0.000 | 0.042 |
| `business_task_signals` | 1.114 | 0.684 | 0.430 | 0.120 |

Do not compare only the original and target `actions` tables and label the entire increase duplicate data: the target also contains formerly separate action-family tables. A causal per-family representation comparison requires matching all source families to their target kinds on the same data, with identical facts and index semantics. The receipt alone proves the totals above, not the saving from a proposed codec/index change.

## Follow-on work within the approved design

Finish remaining schema and caller consolidation without widening retention rules. Before accepting final11, obtain same-dataset per-family payload/physical-column/index attribution, then compare source and final target allocation after equivalent compaction on disposable copies. The current source already has no free pages. Report table/index/schema bytes and query budgets separately.

Inspect `actions`, `runs`, and `tasks` first because they dominate target size. Existing source fields, scalar types, necessary states, business results, execution receipts, identity and query behavior must survive. Removing any index requires identifying its actual callers and proving their query budget; an apparent duplicate name or shared prefix is insufficient. Any representation change should be a separately reviewed measured correction, not an unverified change hidden in a table merge.

Body retirement still requires a readable authoritative source. Existing source-document locator proofs have explicit coverage gaps; this accounting authorizes neither loss of unproved originals nor a second trajectory archive. No new field, feature cancellation, lifetime, runtime selector, source database write, real send, commit or deployment is introduced by this report.
