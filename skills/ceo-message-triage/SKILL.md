---
name: ceo-message-triage
description: Use for deciding whether an incoming DingTalk message needs a reply, reaction, clarification, handoff, or no action. Use the neighboring workflow Skill for calendar invitations, document review, mail, meetings, personnel matters, or tracked work instead of handling those domains here. Load dingtalk-chat before reading context or sending a chat action.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Message Triage

Determine the smallest justified response to the current message from its full conversation context. Keep domain analysis in the neighboring CEO workflow Skill.

Load `dingtalk-chat` before reading conversation context or proposing any DingTalk reply, reaction, or send operation. Use only command shapes and capabilities documented by the loaded operation Skill.

## Triage Workflow

1. Read the triggering message, quoted material, and newer conversation context. Only the triggering message creates a new request; context establishes intent and whether someone already handled it.
2. Resolve mentions from the supplied identities. Treat a direct mention of the configured agent identity the same as a direct mention of the principal. A broadcast mention alone does not create principal responsibility.
3. Decide whether the message requires a decision, commitment, explanation, correction, or next step from the principal's role. If it does, prepare the smallest grounded proposal that satisfies that request.
4. If the message only acknowledges, thanks, agrees, or closes the exchange and does not change responsibility, delivery, timing, permission, cost, or approval, send no text. Propose one context-appropriate reaction only when it adds useful acknowledgment without implying a commitment; otherwise return no action.
5. If a required fact is missing and a verified conversation participant can supply it, ask that participant one concrete factual question in the source conversation. This is a clarification proposal, not an A/B selection and not `needs_human`.
6. Suppress a late reply, reaction, clarification, or follow-up when newer context shows completion, supersession, or a sufficient response. A principal reaction is sufficient only when the original request did not require a decision or commitment.
7. Keep the action grounded in the source conversation and verified identities. Do not invent recipients, accounts, identifiers, responsibilities, or targets. Do not create a follow-up that the message did not request.

Reuse confirmed facts from the current conversation. Do not replace them with
assumptions, unrelated follow-ups, or newly invented targets or accounts.

## Decision Cases

| Case | Consumer outcome | Rule |
| --- | --- | --- |
| `direct_decision_request` | `proposal` | Propose a grounded response or authorized action that answers the decision request. |
| `acknowledgment_without_responsibility_change` | `reaction_or_no_action` | Send no text; propose one useful reaction or return no action. |
| `broadcast_without_principal_action` | `no_action` | Do not interject when the broadcast assigns no action or decision to the principal. |
| `direct_agent_mention` | `proposal` | Apply the same responsibility test as a direct principal mention. |
| `participant_can_supply_missing_fact` | `clarification_proposal` | Ask one concrete factual question to the verified participant in the source conversation. |
| `newer_context_completed_matter` | `no_action` | Do not send a late reply or manufacture a follow-up. |

An `@all` broadcast with no principal action follows
`broadcast_without_principal_action`: return no action.

Consumer A remains read-only and proposes any reply, clarification, or reaction. Audit B independently loads the same business and operation Skills, checks their exact receipts and current conversation state, and alone executes an approved effect.
