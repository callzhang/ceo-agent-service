# Status native delivery coverage contract

Production readback after the independently verified Phase5D release exposed
an existing frontend contract mismatch: backend SystemHealth already declares
optional nullable native_delivery_coverage, while the frontend exact-record
decoder rejects it. Both declarations are unchanged between the prior source
7fabea80 and D release1b6f55e5; this is not a delivery schema failure.

The live raw status producer returns the coverage object; the typed Console
endpoint also emits the default null field when coverage is absent. The Status
page visibly reported invalid status response. No private business body is
retained in this evidence.

Two new regressions fail on the original decoder, for null and for the backend's
checked/unavailable object. The fix declares just that existing optional field,
validates its exact two nonnegative integer fields and preserves unknown-field
rejection. Four malformed-object regressions also pass. The API and Status page
focused suites pass48 tests; the existing backend live-coverage contract test
passes, and TypeScript validation plus the frontend production build pass.

This is a separate deterministic frontend contract correction. No database,
diagnostic producer, policy, provider call or retention behavior changes.
Normal deployment completed from1b6f55e5 to9e0570cb. The actual Status page now
renders running/healthy, PID35390, queue statistics and the native availability
description. No invalid-response error remains. Its four current queue failures
are existing business records, not a claim that all historical work is resolved.
