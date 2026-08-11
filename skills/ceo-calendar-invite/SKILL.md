---
name: ceo-calendar-invite
description: Use for incoming DingTalk calendar invitations, calendar cards, meeting invitations, attendance decisions, schedule conflicts, tentative, accept, or decline responses, and questions about why the principal should attend or what input is expected. Use ceo-meeting-work for meeting content after attendance is settled. Load dingtalk-calendar before issuing any DWS calendar command.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Calendar Invite

Review the invitation, schedule context, attendance rationale, and expected contribution before deciding or drafting a response.

Load `dingtalk-calendar` before calendar reads or writes. Load `dingtalk-chat` before a chat-based clarification or fallback response.
