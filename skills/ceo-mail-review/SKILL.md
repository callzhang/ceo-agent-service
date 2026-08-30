---
name: ceo-mail-review
description: Use when an incoming email, DingTalk or Lark mail card, or channel=email action requires review, reply judgment, automatic reply, or unsubscribe handling.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Mail Review

Load `ceo-mail-review` for mail review, thread resolution, linked-material
inspection, reply judgment, or an audited `channel=email` action. First identify
which workflow supplied the request; its evidence and authorization boundaries
are different.

## Compose Operation Skills

Use the platform's mail and material Skills instead of copying their commands
into this business workflow.

| Source | Operation Skill |
| --- | --- |
| DingTalk mail | `dingtalk-mail` |
| Lark mail | `lark-mail` |
| DingTalk linked material | `dingtalk-doc`, `dingtalk-aitable`, or `dingtalk-drive` |
| Lark linked material | `lark-doc`, `lark-base`, or `lark-drive` |
| Standard IMAP/SMTP email | the email operation capability supplied by the runtime |

A linked material is not an email attachment. It is a document, table, or drive
item referenced from a DingTalk or Lark interactive mail review and readable
through the matching operation Skill. An attachment remains attachment metadata
only under this Email subsystem contract.

## DingTalk Or Lark Interactive Review

For a DingTalk or Lark interactive mail review:

1. Treat a truncated card or quoted preview only as a locator. Resolve the
   principal's mailbox and the complete original message or thread with the
   loaded mail Skill.
2. Confirm sender, recipients, subject, current thread state, and the exact
   request. Do not ask the sender to paste content that the loaded mail Skill can
   read.
3. Inspect every linked material needed for the requested judgment with its
   matching operation Skill. A link title or mail summary is not its content.
   Load `ceo-document-review` when the requested outcome requires substantive
   review of that linked material.
4. Check the current thread, sent state, and safe prior receipts before proposing
   a reply. Do not propose or execute a duplicate reply.

Do not treat an attached file as linked material. Do not download, open, OCR,
parse, summarize, or infer email attachment content.

## Automatic Email Action

For a `channel=email` task:

1. Use only the message and thread text supplied by the email context, plus
   sender, recipients, subject, time, standard mail headers, safe action metadata,
   and safe prior receipts.
2. Treat attachments as attachment metadata only: filename, MIME type, byte
   size, count, and inline flag. The runtime contract is `image_paths=()`.
3. Do not open or inspect attachment content. Do not download, parse, OCR,
   summarize, or infer it. Do not invent attachment facts.
4. Do not open or inspect linked content for a `channel=email` task. A URL in
   message text is text evidence only. Task 11 unsubscribe browser execution is
   a separate audited capability and does not authorize general link browsing.
5. Before proposing `auto_reply`, read the current sent state and safe prior
   receipts. Before proposing `unsubscribe`, read the current unsubscribe state
   and safe prior receipts. Do not propose a duplicate completed action.

When the message says only that details are in an attachment, an authorized
`auto_reply` may acknowledge receipt without evaluating the attachment. Never
claim that an attachment was read, correct, complete, approved, or understood.

### Audited Unsubscribe

An immutable ActionPlan containing `unsubscribe` does not require per-message
confirmation. Consumer A must select only a reliable entry associated with the
current subscription and propose the exact ordered browser operations using the
runtime's opaque entry and control references. Audit Agent B must review those
exact operations before any external write; neither agent may replace them with
an unreviewed navigation, form submission, or confirmation click.

On every initial run and retry, reconcile the current page, provider state, safe
prior receipt, and confirmation mail before another write. A verified completed
or already-unsubscribed state ends the flow without repeating an operation. Do
not treat a click alone as success: require a terminal page, provider response,
or confirmation-mail receipt.

Never place a full unsubscribe URL or query token in the proposal, step journal,
History, status, or error. The private URL may appear only in the restricted
runtime input consumed by the audited browser capability. Persist only opaque
references, redacted step types and states, fixed error codes, and a terminal
receipt.

Login, CAPTCHA, and payment requirements are skipped business outcomes, as is
the absence of a reliable browser entry. They do not require a user prompt and
do not enter Attention. Browser runtime and provider authentication failures are
technical failures: use the existing failed, retry, and exhausted-failure
Attention lifecycle without inventing a new top-level task status.

## Authorization And Outcome

Every reply requires explicit reply authorization.

- For a DingTalk or Lark review, the current request must explicitly authorize
  replying. Review-only, summarize-only, or approval-only requests do not
  authorize a mail reply. Authorization from an older message does not silently
  carry into a materially different current request.
- For `channel=email`, the current immutable ActionPlan is the authorization.
  Only its exact `auto_reply` or `unsubscribe` action may be proposed.
  Classification confirmation, category text, an older plan, or prior
  conversation is not authorization for another mail action.
- Consumer A proposes only the authorized action; it never sends or unsubscribes
  directly. Audit Agent B reviews the proposal under the existing
  feedback/revision lifecycle. Only Audit may execute an accepted action and
  verify exact readback.
- If the complete current thread shows an equivalent reply already sent, use
  canonical `no_action` for the mail effect and report the verified state only
  when the current conversation needs it.
- If review is complete but reply authorization is absent, provide only the
  requested review or draft through an authorized channel; do not execute or
  propose a mail send.

The agent performs the business judgment. The service supplies references and
exact commands without interpreting mail or linked content. For `channel=email`,
the service instead supplies the immutable ActionPlan and bounded metadata
without interpreting message or attachment content. Do not infer or invent
unread content.

## Missing Evidence

When a participant can resolve a genuine evidence gap in an interactive review,
ask one concrete question naming the specifically missing mail or linked material
and explain why it is needed for the requested judgment. Do not ask for a generic
resend or for content the loaded operation Skills can retrieve.

For an automatic `channel=email` action, report a text or state-readback
dependency failure rather than asking a participant to bypass the metadata-only
boundary or claiming success from attachment metadata, a URL title, or a command
response alone.
