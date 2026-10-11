# Context-based business review

Derek explicitly requested that business review have no trusted/untrusted context class and no extra trusted-authorization requirement. The approved change evaluates complete context, principal responsibility, facts, actual recipient and consequences. This is scoped separately from the completed Consumer/Audit/System release.

Consumer and Audit always receive the context contract, including when a custom saved Audit template exists. Mail and WeChat Skill wording follows the same rule. Explicit draft-only/do-not-send scope, fact and audience checks, configured action capabilities, exact persisted System plans and real runtime/provider errors remain unchanged. Historical rejected financial actions are not replayed.

The existing stopped-window publisher updates ten explicitly hashed files; only the default Audit rules and existing mail/WeChat Skills differ from installed content. WeChat is a shared Skill file, not an added managed Skill or background task.

## Validation

223 focused rule/mail/unsubscribe/publication tests passed against the repository Skill snapshot. Two focused System restart/receipt tests passed. Ruff passed. Independent code/publication review found no blocking issue.

The native read-only comparison used gpt-5.6-sol/high, concurrency one, 180 seconds per case, no tools or external sending. The corrected v2 frozen suite passed 6/6 on both baseline a385b86d and runtime candidate a0f3636c. Supported financial and financing-group replies were approved without a separate permission sentence. Audience mismatch, unsupported commitment, explicit draft-only conflict and missing cost were rejected or returned for concrete reasons. This verifies the stated behavior in these cases and does not demonstrate a quality gain over baseline.

The first v1 positive fixture accidentally asserted a cost change not in its facts; both refs correctly returned it. Original raw results are preserved. V2 changes only that first payload and reruns it on both refs. The other five raw outputs are reused after exact facts/Skill/Consumer subject, candidate digest, role instruction hash and settings equivalence verification. The original scorer enum was corrected from return_for_revision to the production return enum; expected outcomes are not included in model prompts.

Reproduce the full frozen v2 suite with:

```sh
python -m scripts.eval_consumer_audit_human_review --manifest evals/consumer_audit_business_context/v2.json --candidate-ref a0f3636c --output /absolute/path/native-comparison.json
```

Original and corrected artifacts: /Users/derek/Documents/memory/ceo-context-business-review-20261006/native-comparison.json and native-comparison-v2.json. Merge/CI/deployment and live readback remain separate release evidence.
