---
name: ceo-document-review
description: Use for requests to inspect, summarize, compare, comment on, or approve DingTalk or Lark documents, files, images, and tables. Use ceo-mail-review for the enclosing mail thread and ceo-meeting-work for meeting records. Load the operation Skill matching the actual material type before reading or editing it.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Document Review

Identify the material type, read the current version, and keep review output tied to evidence in that material.

Load `dingtalk-doc` for DingTalk documents, `dingtalk-aitable` for AI tables, and `dingtalk-drive` for stored files. For Lark material, load the corresponding `lark-doc`, `lark-base`, or `lark-drive` Skill. Load `dingtalk-chat` before delivering review results in chat.
