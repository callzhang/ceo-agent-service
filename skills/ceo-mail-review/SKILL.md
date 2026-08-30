---
name: ceo-mail-review
description: Use for audited email auto_reply or unsubscribe tasks authorized by an immutable ActionPlan. Review message and thread text, attachment metadata, and prior state receipts without reading attachment or linked content.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Mail Review

Load `ceo-mail-review` for `channel=email` tasks whose persisted immutable
ActionPlan currently authorizes exactly one `auto_reply` or `unsubscribe`
action. Classification confirmation by itself is not mail-action authorization.

## Compose Operation Skills

Use the platform's mail Skill only for message/thread text and sent or
unsubscribe state readback. Do not use document, drive, OCR, image, or linked
material Skills in this workflow.

| Source | Operation Skill |
| --- | --- |
| DingTalk mail | `dingtalk-mail` |
| Lark mail | `lark-mail` |
| Standard IMAP/SMTP email | the email operation capability supplied by the runtime |

## Resolve Complete Evidence

1. Use only message and thread text supplied by the email context, plus sender,
   recipients, subject, time, standard mail headers, and safe action metadata.
2. Treat attachments as attachment metadata only: filename, MIME type, byte
   size, count, and inline flag. The runtime contract is `image_paths=()`.
3. Do not open or inspect attachment content. Do not download, parse, OCR,
   summarize, or infer it. Do not invent attachment facts.
4. Do not open or inspect linked content in this Task 9 workflow. A URL present
   in message text is text evidence only; browser execution is a separately
   scoped unsubscribe capability.
5. Before proposing `auto_reply`, read the current sent state and safe prior
   receipts. Before proposing `unsubscribe`, read the current unsubscribe state
   and safe prior receipts. Do not propose a duplicate completed action.

The Agent performs the business judgment from those bounded facts. The service
supplies the immutable ActionPlan and metadata without interpreting message or
attachment content. Do not infer or invent unread content.

## Authorization And Outcome

For `channel=email`, the current immutable ActionPlan is the authorization.
Only its exact `auto_reply` or `unsubscribe` action may be proposed. Do not
derive a generic follow-up, attachment analysis, or another mail action from
the category, message wording, an older plan, or prior conversation.

- Consumer A proposes the exact action; it never sends or unsubscribes directly.
- Audit Agent B reviews the proposal under the existing feedback/revision
  lifecycle. Only Audit may execute an accepted action and verify readback.
- A justified `auto_reply` is one action in a canonical `proposal`; the mail
  operation capability owns sending and exact sent state readback.
- A justified `unsubscribe` proposal is limited to the authorized subscription
  source and must require exact unsubscribe state readback.
- If the complete current thread shows an equivalent reply already sent, use
  canonical `no_action` for the mail effect and report the verified state only
  when the current conversation needs it.
- If review is complete but reply authorization is absent, provide only the
  requested review or draft through an authorized channel; do not execute or
  propose a mail send.
- If text or state readback fails, report the dependency failure rather than
  drafting or claiming success from attachment metadata, a URL title, or a
  command response alone.

When the message says only that details are in an attachment, an `auto_reply`
may acknowledge receipt without evaluating the attachment. Never claim that an
attachment was read, correct, complete, approved, or understood.
