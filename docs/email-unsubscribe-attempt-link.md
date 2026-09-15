# Email unsubscribe direct execution

Unsubscribe is a low-risk, provider-idempotent email action.  An immutable
email `ActionPlan` selects the exact opaque entry; the Email worker then opens
that entry directly and persists the page result.  It does not create or wait
for a Consumer or Audit turn.

The browser may operate only a current-page control whose rendered label says
unsubscribe, confirm, or continue: a same-page link, a supported form, or a
standalone button.  The exact control is re-read immediately before it is
used, and every run stops at the first terminal page.  Login and CAPTCHA pages
remain provider outcomes (`skipped_login_required` and `skipped_captcha`), not
Audit decisions.

`email_unsubscribe_receipts` remains the idempotency record.  An existing
receipt is returned without opening the private entry again.  Attempt detail
exposes the receipt and its redacted result text, but never re-exports a raw
unsubscribe entry URL as a clickable link.

An explicit retry of `skipped_no_reliable_entry` reopens the same ActionPlan
entry. If the new run reaches a terminal result, the receipt becomes the
current projection while the original Attempt and old effect remain in the
history chain. If the page is still not operable, the original skip remains
the current result. A `durable_context_entry_unavailable` result has no
recoverable persisted entry and is not batch-retried.
