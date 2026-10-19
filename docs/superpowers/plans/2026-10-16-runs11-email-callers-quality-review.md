# Runs11 Email callers — bounded QUALITY review

**Decision: QUALITY PASS for revised unselected proposal `2f6d29d2`.** The earlier retention scaling block is closed: the revised inner query uses the existing sample partial index even without SQLite planner statistics. On a synthetic fixture at the supplied frozen source's coarse row-count scale, it performed about 31,000 VM instructions versus about 29,000 for the native 2,048-row sample table. This review does not select the patch or establish current constructor/coordinator integration or final release.

## Scope and functional evidence

- Reviewed base `c30532f8de9aa211a04000f7c94bed9ad8568aa8`, revised proposal SHA-256 `2f6d29d21f91997b0aeb77a77c3ebe4e71438049c30e680ac17c5da08275e68e`, and focused test SHA-256 `915db2da8c464f9f6ec839df4c621c8f17e481b2639890b9ea0fb2333e2a319f`. The patch touches only `app/email_store.py` and `app/quality_gate.py`.
- Applied the exact patch in a fresh `TemporaryDirectory` containing a copied `app` tree. **All five focused tests passed in 35.07s** on this concurrently loaded host. The fifth test executes the public writer with higher-ID unrelated rows, asserts both kinds remain, and checks the retention plan and VM-work bound with no `sqlite_stat1`.
- The writer retains the native validations, field values, global 2,048 cap, and public sample ID order. Rate fallback, model observability, and QualityGate's logical labels and issue counts remain covered by the focused tests and preceding SPEC review. The revised `INDEXED BY idx_runs11_sample_id` applies only to the inner retention selection; its predicate and descending public-ID order remain unchanged.

## Frozen-scale synthetic probe

In the same copied-tree run but a separate fresh Store/Email database, I created a pure Runs11 target and inserted exactly **2,048 samples** with public IDs **3,591–5,638** plus **231,340 setup-kind rows** with public IDs **130,977–362,316**. These match the supplied frozen source's coarse sample count/water and old Runs row count/water while deliberately putting every other-kind public ID above the samples. `sqlite_stat1` was absent before the probe. A separate fresh native Email sample table contained 2,048 rows. I extracted the DELETE SQL from the revised copied `EmailStore.record_classifier_runtime_sample` method, rather than reconstructing the candidate SQL by hand. Its exact inner query contains the sample-index direction.

| Retention statement, three executions | Inner scan from `EXPLAIN QUERY PLAN` | Median elapsed | Approximate VM instructions |
| --- | --- | ---: | ---: |
| Native 2,048-row sample table | Native sample table | 0.774 ms | 29,000 |
| Exact revised proposal before `ANALYZE` | `idx_runs11_sample_id` | 0.880 ms | 31,000 |
| Exact revised proposal after `ANALYZE` | `idx_runs11_sample_id` | 0.914 ms | 31,000 |

SQLite's progress handler counted at 1,000-instruction intervals, so VM figures are approximate to that granularity. The statements deleted no rows; post-probe target counts stayed at 2,048 samples and 231,340 unrelated rows. The earlier no-index-hint candidate chose the general `idx_runs_public_id` scan on the same synthetic shape and consumed about 960,000 VM instructions; that was the block. The revised bound no longer depends on `ANALYZE` or the number of higher-ID unrelated Runs rows. Elapsed times are local fixture observations under host load, not production latency claims. No frozen row bodies or source database were migrated.

## Remaining boundary

Current 51-table constructor/coordinator integration, combined Store callers, copied-source inverse, final 11-table release, and live performance remain root-owned gates. No app, tests, index, provider, native CLI, network, service, or production state was changed by this review; all owned temporary directories were removed automatically. I did not stage or commit.
