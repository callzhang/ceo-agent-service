# D1c worker-status fixture follow-up (2026-10-20)

The old failed Work Todo link remains a historical fact, while D1c removed its
`DingTalk Todos` queue and retryable count. The worker-page test previously
expected a retryable item from only that historical link. On the clean D1b
predecessor it passed; on the D1c source it failed at the existing
`summary.retryable >= 1` assertion. The baseline and candidate logs are held by
the release owner.

The test now adds a separate pending Reply task with a matching failed Reply
attempt. The existing Reply-attempt queue classifies that current pair as
retryable. The test keeps the original pending, failed, retryable, queue, and
HTML assertions, and additionally checks that the retryable count belongs to
Reply attempts, `DingTalk Todos` is absent from Status, and the failed old link
is still readable. The selected worker test passes (1 case, 7.84 seconds):
`/tmp/ceo-d2-legacy-writers.mcMs23/status-fixture-green.log`.

This is a test fixture and documentation follow-up to D1c. It changes no
runtime queue rule, provider operation, schema, or historical record.
