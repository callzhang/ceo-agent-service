# A5 original selection case — current native revalidation

## Original failure and recovered source

The 2026-10-19 `business-model-specialized-actions-native.v1` failure remains
recorded in `docs/validation/2026-10-19-specialized-actions-copy.json` and its
native trajectory. All four candidates authored an execute instruction requiring
exact raw message text. Service preparation appended `(via agent)`; Audit
repeatedly asked Consumer to remove it from the action, but preparation reapplied
it. No provider send occurred. This was not loss of database content or receipts.

The original trigger did **not** demand an exact body. It asked for a reusable
`task_class` execute/stop choice, supported by reading the local artifact and the
message action contract. The exact-body instruction was authored inside the
Consumer's execute option. The original task and material are now frozen in
`evals/business_model/a5-original-selection-case.json`, extracted from source
trajectory lines 7 and 30, with instruction/material/trajectory hashes. The
original temporary harness is gone; native trajectories remain authoritative.

## Current replay

On 2026-10-25, a current production-derived Store25.2/Email54 copy ran the exact
original trigger and material with real GPT-6 Luna/high through Codex CLI0.163.0.
No formatting-permission text was added to the input. Current Consumer and Audit
code, budgets, policies and preparation behavior were unchanged. The only
external message handler was the strict in-memory provider from the existing
native-copy harness; inherited non-task MCP servers were disabled for this
synthetic test using that harness's existing invocation scaffolding.

Consumer399429 and Audit399430 completed at revision0. The task reached
`needs_human` after Audit approval, with zero provider calls. Selecting `execute`
in the copy reused that exact reviewed option without another model call, then
produced one substitute send, one verification and one durable receipt. The
final body equaled the audited option exactly and contained the original marker,
three records and total100 with the service postfix. Reopening the database
preserved its executed terminal result. All original persisted rows across 48 existing tables were
unchanged (new fixture rows were allowed); quick_check passed and the original sole FK finding remained
`('runs',190042,'tasks',1)` (last value is a constraint ordinal).

This closes current revalidation of the original input; it does not rewrite the
historical failed run or claim that every future model output must pass. There
was no reproduced current runtime defect requiring a Consumer/Audit change.
Deliberately contradictory exact-full-message and mandatory-postfix requirements
were not introduced as a replacement fixture.

## Reproduction and evidence

After capturing a current, closed production-derived baseline and identical
candidate copy in an owned temporary root, run:

```sh
python scripts/validate_a5_native_selection.py --root <owned-root> --database <candidate.sqlite3> --baseline <baseline.sqlite3>
```

The script uses real CLI and substitute delivery; it writes failure evidence
before asserting the pre-selection contract. The concise result is in
[revalidation evidence](validation/2026-10-25-a5-original-selection-revalidation.json).
Native CLI session IDs and transcript coordinates are in that file; native
trajectory bodies are not copied into the database or retained in this report.

Historical checkpoint — 2026-10-25: scoped formatting, exact candidate assertion
and semantic API checks passed in the 96-test combined selection. The read-model
release was deployed and read back; artifact cleanup remained blocked by platform
approval at that checkpoint. See the
[overall read-model closeout](business-model-history-read-unification.md).

Later cleanup evidence is recorded in the
[2026-10-27 History closeout](history-artifact-closeout-20261027.md#cleanup) and
[current release closeout](release-acceptance-closeout-20261027.md). Those records
identify their own verified targets; they do not establish that every older A5
or historical validation root has been removed. The original-case native
revalidation above passed independently of that cleanup status.
