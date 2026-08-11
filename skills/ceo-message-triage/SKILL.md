---
name: ceo-message-triage
description: Use for deciding whether an incoming DingTalk message needs a reply, reaction, clarification, handoff, or no action. Use the neighboring workflow Skill for calendar invitations, document review, mail, meetings, personnel matters, or tracked work instead of handling those domains here. Load dingtalk-chat before reading context or sending a chat action.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Message Triage

Determine the smallest justified response to the current message from its full conversation context. Keep domain analysis in the neighboring CEO workflow Skill.

Load `dingtalk-chat` before any DingTalk conversation read, reply, reaction, or send operation.
