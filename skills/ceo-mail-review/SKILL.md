---
name: ceo-mail-review
description: Use for reviewing an incoming mail card, resolving the complete message or thread, inspecting attachments and links, checking whether a reply already exists, and drafting or sending an authorized reply. Use ceo-document-review for a standalone material review outside the mail workflow. Load dingtalk-mail before any DingTalk mail operation.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Mail Review

Resolve and read the complete thread before judging the request or composing a response. Treat linked materials as evidence, and do not send unless the current request authorizes it.

Load `dingtalk-mail` for mail reads and writes. Load `dingtalk-doc` or `dingtalk-drive` before inspecting linked DingTalk materials.
