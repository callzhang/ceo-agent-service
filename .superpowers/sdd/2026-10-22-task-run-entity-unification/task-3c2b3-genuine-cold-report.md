# Task3C2B3 genuine old DAO cold-write report

Date: 2026-10-23  
Worktree: `/Users/derek/.codex/worktrees/task-run-entity-phase2/ceo-agent-service`  
Base: `57cd25e69`  
Predecessor source: `/private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf`  
Predecessor pin: `c38ce12cf1b2b18c147ea687bccdce11973827eb`

## Result

The bounded validator completed two portable inverse → genuine old DAO cold read/write → fresh forward cycles. The child imported `ReviewedCandidateStoreMixin` from the explicit predecessor root. It read source candidate public ID `40` (Consumer Run `9006`), Review `7`/Audit Run `9007`, Selection `18`, and execution progress, then wrote a supplement through the predecessor's public `record_candidate_supplement` method. SQLite was committed and closed before each child process.

The runner JSON-serialized and decoded only actual Task, Run, adoption metadata and inverse handoffs between each inverse and fresh forward. It retained the native new Audit Run/Review and supplement, preserved the retired supplement's absence, and retained a current Action occupying an adopted physical ID together with its own observer, provider receipt, receipt lineage, and identity-map entry. Integrity and foreign-key checks passed after each inverse, old DAO write, and fresh forward. This is a focused local conversion validation, not constructor activation, provider execution, or full Task5 acceptance.

## Exact command

```sh
python tools/validate_run_adoption_cold.py \
  --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf \
  --output-dir /tmp/task3c2b3-coldrun-acceptance
```

Exit code: `0`. Machine-readable output: `/tmp/task3c2b3-coldrun-acceptance/report.json`.

## Cycle evidence

Both cycles used predecessor module `/private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf/app/reviewed_candidate_store.py`, candidate digest `61ce60be426cf9f5efc212292560aa38a2b803610932b7d96d1fae19ad1f9400`, and returned `invalidation_reason=supplement`.

| Cycle | Old DAO supplement ID | Inverse source counts (Actions / receipts / observers) | Fresh forward counts (adopted results / Reviews / selections / supplements / progress) | Portable metadata SHA-256 | Inverse source SHA-256 | Fresh forward SHA-256 |
|---|---:|---:|---:|---|---|---|
| 1 | 52 | 13 / 4 / 3 | 2 / 3 / 1 / 3 / 2 | `cfe5bbec27e72491409d42f18d7dff16bb69be11822057f66940190f675f6223` | `2bc3662029a5689962698d322843dca5ffeae27fb2e71fb34d38c1a56231960e` | `a16318cf912f80e218c11d7015714839f5573e210fa7acc30b0933a376015d47` |
| 2 | 53 | 14 / 4 / 3 | 2 / 3 / 1 / 4 / 2 | `f791e1955aeef201fba85ebf001571f315761dd3752d46546e0f4132046ae6b5` | `550d2620238697b237d04a3ad7e6f4686e140cfe8be017f911ad487bb9ca1470` | `4b352bd15b3e64cc5e9f47d609fc9a23b79eea396a631ef319f149d81a03931e` |

The second cycle starts from the first cycle's fresh-forward database, rather than a recreated fixture. Before each cycle, the validator snapshots typed rows and columns for every native Action/receipt/observer/identity-map and adopted table. After fresh forward, it compares every existing row exactly, allowing only the old DAO's read-back invalidation on Consumer Run 9106 and the exact returned supplement row. It compares the Review `result_json`, candidate body/digest/timestamp/type, all receipts and provider lineage, observations, and identity facts as full stored rows. SQLite allocator rows are checked separately against the forward converter's returned per-kind waters merged with unchanged non-Run waters; cycle 1 inverse assigns the previously unmapped native Audit Run 9112 source physical ID 9113. The forward handoff preserves the captured Run/published waters at 9113 (from 9011/9112 baseline), and cycle 2 reuses the same identity with no new allocation or water increase. The new Review and Audit Run remained present after both cycles; supplement retirement/addition, collision owner, receipt `provider_receipt_id`, and identity-map relationship are checked directly, not inferred from counts.

## Verification and remaining gates

`python -m py_compile tools/validate_run_adoption_cold.py`, Ruff on the runner, and `git diff --check` passed. The validator also compares all existing typed native rows exactly after each forward, with only the precise invalidation and expected supplement row allowed to differ. Allocator waters are evaluated separately from the returned Run forward handoff, including preservation of the cycle-1 global reservation at 9113 and its unchanged cycle-2 water. The runner itself is the bounded integration execution; no full suite or large source copy was run. Task5 still owns the full 14-Store constructor, new Task/multi-input/native CLI/UI acceptance, and broader publication gates. No production checkout, providers, push, or deploy were touched.

## Commit

Implementation, validation documentation, this report, and this worktree's claim row were first committed as `338466749` and `0e801238e`; strict typed-row and independently derived allocator evidence is finalized in `483eabcd2` (`Strengthen Task3C2B3 cold cycle evidence`). The unrelated dirty C2A report was excluded and remains untouched.
