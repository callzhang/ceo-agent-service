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
  --output-dir /tmp/task3c2b3-coldrun-final
```

Exit code: `0`. Machine-readable output: `/tmp/task3c2b3-coldrun-final/report.json`.

## Cycle evidence

Both cycles used predecessor module `/private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf/app/reviewed_candidate_store.py`, candidate digest `61ce60be426cf9f5efc212292560aa38a2b803610932b7d96d1fae19ad1f9400`, and returned `invalidation_reason=supplement`.

| Cycle | Old DAO supplement ID | Inverse source counts (Actions / receipts / observers) | Fresh forward counts (adopted results / Reviews / selections / supplements / progress) | Portable metadata SHA-256 | Inverse source SHA-256 | Fresh forward SHA-256 |
|---|---:|---:|---:|---|---|---|
| 1 | 52 | 13 / 4 / 3 | 2 / 3 / 1 / 3 / 2 | `cfe5bbec27e72491409d42f18d7dff16bb69be11822057f66940190f675f6223` | `34f86d0a4d1436c2310fc02d13c5220c906e38dcce5810466301cbf1c9037a8d` | `ee83819fbf6c8d850a013ee6055dfb41457875f7362cf875716ef1e310322603` |
| 2 | 53 | 14 / 4 / 3 | 2 / 3 / 1 / 4 / 2 | `f791e1955aeef201fba85ebf001571f315761dd3752d46546e0f4132046ae6b5` | `c3535c5170dde889644e4e4e9f7365f64a7662d25fd20fd0558c7332123506e5` | `17db8db811d384f67ac2127784bd6e6a98e362f86be028765d68ce7d6bff9bdc` |

The second cycle starts from the first cycle's fresh-forward database, rather than a recreated fixture. The new Review and Audit Run remained present after both cycles; its current native Review ID and timestamp, supplement retirement/addition, collision owner, receipt `provider_receipt_id`, and identity-map relationship are checked directly, not inferred from counts.

## Verification and remaining gates

`python -m py_compile tools/validate_run_adoption_cold.py`, Ruff on the runner, and `git diff --check` passed. The runner itself is the bounded integration execution; no full suite or large source copy was run. Task5 still owns the full 14-Store constructor, new Task/multi-input/native CLI/UI acceptance, and broader publication gates. No production checkout, providers, push, or deploy were touched.

## Commit

Implementation, validation documentation, this report, and this worktree's claim row were committed together as `338466749` (`Validate Task3C2B3 genuine old DAO cold cycles`). The unrelated dirty C2A report was excluded and remains untouched.
