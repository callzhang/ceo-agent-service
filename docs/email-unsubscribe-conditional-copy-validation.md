# Unsubscribe conditional-copy state validation

## Confirmed trajectory

Native page-judgement attempt `363299` showed a page containing “By
unsubscribing, you will no longer receive this newsletter” and an
`unsubscribe` button. The Agent returned `action_required`, correctly treating
the sentence as a consequence of the proposed action. The service then
overrode that result to `DONE` because `_state_from_text` matched the generic
“you will no longer receive” substring. This was a service-side state
classification error, not an Agent reasoning error.

## Change

The deterministic terminal-state recognizer now distinguishes conditional
future-consequence copy from explicit completion evidence. Conditional forms
such as “by unsubscribing”, “if you unsubscribe”, and “when you unsubscribe”
do not produce `DONE`; the page judgement and available controls remain in
effect. Explicit completion wording still takes precedence over management
controls, and existing tracker-host completion wording remains recognized.

No browser operation, receipt format, retry rule, or external-action
mechanism changed. No real unsubscribe was executed.

## Validation

The regression failed against the prior implementation and passes with the
change. Coverage includes the observed conditional sentence with an
unsubscribe button, no-model fallback with a button, conditional wording
variants, and an explicit completion page with a management control. The
focused unsubscribe, browser, and page-judgement tests are recorded in the
claim row in `docs/agent-claims.md`.
