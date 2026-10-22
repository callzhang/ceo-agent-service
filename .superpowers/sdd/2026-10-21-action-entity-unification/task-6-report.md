# Task 6A1 report: reusable copy and capacity validator

Status: the deterministic copy/comparator/capacity portion is implemented and its focused tests pass. This is not a full Task 6 or Task 6A completion claim. Native CLI lifecycle and browser/UI acceptance remain separately owned follow-up work.

## Implementation

- Added `tools/validate_action_entity_copy.py` with a read-only SQLite backup entry point, owned `/tmp` copy lifecycle, current public prepared-action write/readback, paired inverse/old-code cold-write/re-forward flow, typed Action semantic comparison, same-population capacity verification, and compact JSON output.
- Added focused tests for read-only source backup, rejection of live provider clients, preservation of new facts over inverse/re-forward, same population and VACUUM requirements, typed identity remaps, and negative body/reference/out-of-scope-ID cases.
- Added `docs/action-entity-unification-validation.md` with invocation, comparator rules, saved-copy evidence, hashes, and capacity results.

## Verification

Command: `python -m pytest -q tests/test_validate_action_entity_copy.py`

Result: `9 passed in 0.09s`.

The previous corrected full-copy run is preserved at `/tmp/action-entity-task6-corrected.IH2XQu/copy`. Current writer/readback, inverse, actual pinned old-code cold write, and re-forward checks passed in that run. Its generic physical-row digest failed before the typed-identity ruling was implemented. The corrected same-cohort comparator was subsequently run against the saved `before-inverse.sqlite3` and `capacity-inverse.sqlite3` copies and passed exact population/schema/count/sequence/FK checks. The raw earlier `final_body` SQL failure trace was not retained, so no root cause is claimed for that earlier symptom.

For capacity, fresh SQLite backup copies of `before-inverse.sqlite3` and `cohort-current.sqlite3` were VACUUMed under `/tmp/action-entity-capacity-proof.jlai9a45`. The baseline is independently proven equal to an inverse of the re-forwarded current candidate by the comparator; that inverse copy was created from the same candidate population before storage measurement. Both capacity copies used 4,096-byte pages and had zero freelist pages. Their sizes were 490,471,424 and 487,849,984 bytes (delta -2,621,440, or -0.53%). Table pages changed +1,539 and index pages changed -2,168. Current Action Entity tables occupy 146,780,160 bytes, including 2,723,840 bytes for the identity map and 2,895,872 bytes across its two indexes. See the validation document for per-table totals and artifact hashes.

The first-red artifact `/tmp/task6-first-red.txt` is a missing-module pytest collection failure (SHA-256 `5cf4573c8433d596067c34cd34de4d80f7d8c7cdbe3becf99a3caf951f8dcb55`); it does not contain the unretained SQL failure. The full corrected-run log is `/tmp/task6-corrected-full-run.log` (SHA-256 `1dee340efc335b6ff69e9bd2852ff1f4ad1f75391d99df9ff0b094e49771b4b7`).

The reusable CLI was not rerun end-to-end after the comparator correction, to avoid repeating the completed full lifecycle. The saved-copy comparator and paired capacity measurement were run directly. Native CLI, UI, production writes, live provider calls, push, and deployment were not performed.

## Review fix round 1

Task 6A1 review approved the bounded A1 work with one P2: the reusable capacity measure did not record or compare `PRAGMA page_size`, although the saved-copy proof measured 4,096-byte pages on both sides. `_measure` now records its actual positive page size, and `assert_capacity_pair` rejects differing or nonpositive sizes. Focused tests cover a real 1,024-byte SQLite page-size measurement and mismatch/nonpositive negatives. The saved-copy evidence and lifecycle artifacts were not changed or regenerated.
