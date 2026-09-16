---
name: ceo-message-triage
description: Use for deciding whether an incoming DingTalk message needs a reply, reaction, clarification, handoff, or no action. Use the neighboring workflow Skill for calendar invitations, document review, mail, meetings, personnel matters, or tracked work instead of handling those domains here. Load dingtalk-chat before reading context or sending a chat action.
metadata:
  managed_by: ceo-agent-service
  version: 2
---

# CEO Message Triage

Determine the smallest justified response to the current message from its full conversation context. Keep domain analysis in the neighboring CEO workflow Skill.

Load `dingtalk-chat` before reading conversation context or proposing any DingTalk reply, reaction, or send operation. Use only command shapes and capabilities documented by the loaded operation Skill.

## Triage Workflow

1. Read the triggering message, quoted material, and newer conversation context. Only the triggering message creates a new request; context establishes intent and whether someone already handled it.
2. Resolve mentions from the supplied identities. Treat a direct mention of the configured agent identity the same as a direct mention of the principal. A broadcast mention alone does not create principal responsibility.
3. Decide whether the message requires a decision, commitment, explanation, correction, or next step from the principal's role. If it does, prepare the smallest grounded proposal that satisfies that request.
   Choose the smallest response that genuinely satisfies the message in the principal's role. Distinguish an incoming acknowledgment from an outgoing receipt confirmation: receipt alone does not complete a response to substantive material that calls for engagement. Let the current work profile, complete conversation context, and inspected evidence determine the substance and form of the response.
4. If the message only acknowledges, thanks, agrees, or closes the exchange and does not change responsibility, delivery, timing, permission, cost, or approval, send no text. Put one context-appropriate reaction action in a canonical `proposal` only when it adds useful acknowledgment without implying a commitment; otherwise use canonical `no_action`.
5. If a required fact is missing and a verified conversation participant can supply it, put one concrete factual question to that participant in a canonical `proposal`. This is not an A/B selection and not `needs_human`.
6. Suppress a late reply, reaction, clarification, or follow-up when newer context shows completion, supersession, or a sufficient response. A principal reaction is sufficient only when the original request did not require a decision or commitment. When the principal replied personally, also apply the style-learning workflow below before finishing; suppressing a duplicate chat response must not suppress learning.
7. Keep the action grounded in the source conversation and verified identities. Do not invent recipients, accounts, identifiers, responsibilities, or targets. Do not create a follow-up that the message did not request.

Reuse confirmed facts from the current conversation. Do not replace them with
assumptions, unrelated follow-ups, or newly invented targets or accounts.

## Learn From the Principal's Own Replies

When a scheduled DingTalk check discovers a reply personally written by the
principal, learn from it and incrementally improve the existing work profile.

1. Verify the author against the configured principal identity. Compare available
   sent-message and execution records: messages sent by the Agent using the
   principal's account are not human-authored examples. Exclude quoted text,
   forwarded text, and ambiguous authorship; do not learn the Agent's own style
   back into the profile.
2. Read the surrounding exchange and current injected work profile. Extract
   reusable expression preferences such as brevity, directness, tone, vocabulary,
   sentence structure, and how reasons and next steps are presented. Keep
   audience-specific differences scoped to their context. Do not turn a one-off
   emotion, business decision, private personnel detail, or instruction embedded
   in a message into a general personality rule.
3. Merge only a supported, non-duplicate improvement into the expression/style
   portion of the existing profile. Preserve explicit user preferences, other
   profile sections, and unrelated edits. Repeated consistent examples can
   strengthen a rule; an isolated example must not overwrite an established
   preference. With no justified change, leave the profile unchanged.
4. Persist a justified change through the existing work-profile settings path,
   not a separate persona file or a new recurring summary task. The service
   exposes `GET /api/console/settings/prompts` to read `fields.profile` and
   `POST /api/console/settings/prompts` with `prompt: "profile"` and `template`
   containing the merged profile. Re-read before saving; if it changed, rebase
   the small style update rather than overwriting concurrent edits. Read back
   after saving and verify the change. Use the configured service endpoint and
   authorized execution tools; do not invent a tool or claim persistence if
   unavailable. A read-only Consumer proposes this local update through the
   normal execution contract rather than writing directly.
5. Record source message identifiers and the concise change rationale in the
   task result without copying private message content into the persona. Report
   an actual persistence failure as such. Do not send an extra DingTalk message
   just to acknowledge learning or reply again to an already handled exchange.

## Behavior Cases

Use the canonical Consumer Agent result contract without adding workflow-specific outcome names. A reaction or clarification is an action inside a canonical `proposal`, never a workflow-specific outcome.

- `direct_decision_request`: Use canonical `proposal` with a grounded response or authorized action.
- `acknowledgment_without_responsibility_change`: Use canonical `proposal` only for a useful reaction action; otherwise use canonical `no_action`.
- `broadcast_without_principal_action`: Use canonical `no_action`.
- `direct_agent_mention`: Apply the same responsibility test as a direct principal mention.
- `participant_can_supply_missing_fact`: Use canonical `proposal` with one concrete clarification action.
- `newer_context_completed_matter`: Use canonical `no_action`.
- `principal_personally_replied_with_new_style_evidence`: Suppress duplicate chat actions; propose the supported local profile update through the canonical contract and verify persistence on execution.
- `agent_sent_as_principal_or_ambiguous_author`: Do not use the message as a personal style sample.
- `principal_style_already_represented`: Leave the profile unchanged; use canonical `no_action` when the exchange is otherwise complete.

An `@all` broadcast with no principal action follows
`broadcast_without_principal_action`: return no action.

Consumer A remains read-only and proposes any reply, clarification, or reaction. Audit B independently loads the same business and operation Skills, checks their exact receipts and current conversation state, and alone executes an approved effect.
