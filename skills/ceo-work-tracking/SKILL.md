---
name: ceo-work-tracking
description: Use when a message, meeting, or decision creates trackable work that needs extraction, assignment, project or TODO creation, follow-up, completion evidence, or closure. Use ceo-message-triage when no durable work item is needed and ceo-meeting-work for meeting synthesis before actions are confirmed. Load the relevant task operation Skill before changing tracked work.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Work Tracking

Treat work extraction, creation, follow-up, completion verification, and closure as one lifecycle. Preserve ownership, expected outcome, and evidence across each state change.

Load `dingtalk-todo` for DingTalk TODO operations, `task-management` for local task records, and `dingtalk-chat` before requesting updates or reporting closure.
