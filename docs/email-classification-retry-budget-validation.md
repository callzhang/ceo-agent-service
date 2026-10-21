# Email classification retry budget repair

## Expected versus observed

Expected: a task that repeatedly fails or loses its worker eventually stops
automatic retries and retains the failure for diagnosis or explicit repair.

Read-only inspection of all active Task kinds on 2026-10-20 Pacific found seven
Email classification records at 58, 54, 48, 29, 23, 19 and 14 attempts, plus an
11-attempt record. Errors included `LookupError:email source UID is unavailable`,
a read timeout and DNS lookup failure. These were distinct from the Work Summary
shared-session retry bug. At the 58-attempt record's latest state, its processing
lease was still active and therefore was not altered during investigation.

The processing call path is `run_email_classification_task_once` ->
`provider_readback` -> Agent classification. An unavailable source UID raises
LookupError in the IMAP reader before model classification. The worker records
that exception using adapter.fail(retryable=True). The adapter previously chose
pending solely from retryable, with no count ceiling; its exponential backoff
only capped the delay at fifteen minutes. Lease expiration also always returned
to pending. The input and last error were read directly; no provider read or
external write was performed for this investigation.

## Repair

- Failure retry and expired claims share a three-attempt budget.
- Failed-at-limit records have no next retry time and retain their actual error.
- Startup recovery and claim transactions close already exhausted pending
  records, even if their old next retry time is in the future. These closures
  preserve attempt_count, original error, stable task identity and source input.
- The existing selected-task explicit repair still starts a new budget. Done
  records, unexpired running claims, source lookup semantics, model decisions,
  action generation and provider permissions are unchanged.

No schema, migration, Audit policy or outbound action change is included.

## Verification

Three new tests failed against the previous adapter: failure three remained
pending, lease expiration three remained pending, and a legacy 58-attempt input
remained queued. After the repair, 22 classification adapter tests passed,
including retained source inputs, ordinary backoff, stale-owner fencing, recovery
and explicit selected repair. Final repeat of the adapter selection: 22 passed. The worker integration
selection (classification_worker, classifier_provider_race and
classifier_distinguishes_permanent) passed all 16 cases. Ruff, git diff --check
and service-module import checks passed. No full-suite run was performed.

## Deployment

Publication and production readback are pending. The first Work Summary-only
release is draining through normal app.deploy. Its final in-flight claim was an
old Email classification attempt 58, with lease expiration
2026-10-21T04:17:12.597326+00:00; no active claim was forcibly changed.
