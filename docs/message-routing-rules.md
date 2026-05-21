# DingTalk Message Routing Rules

This document lists DingTalk message shapes seen by the CEO auto-reply worker,
how they should be routed, and which rules are already enforced by code.

The goal is not to replace the model with broad keyword matching. Code rules
should only handle stable transport or card structures. If the meaning depends
on business context, the message should go to the agent with the right review
instructions.

## Routing Outcomes

- `skip_before_agent`: record `no_reply/skipped` without calling the agent.
- `agent_review`: send to the agent for context-aware judgment.
- `handoff_or_material_check`: send to the agent, but require material review or
  real-human handoff before any substantive action.
- `candidate_rule`: not implemented yet; proposed pattern needs real samples and
  tests before code changes.

## Code-Recognized Rules

### Non-Text DingTalk Message Types

Current behavior: `skip_before_agent`.

Code condition:

```python
message.message_type and message.message_type.lower() not in {"text"}
```

Message type keys parsed upstream:

```regex
^(msgType|messageType|contentType|content_type|msg_type|type)$
```

Examples:

```text
message_type=calendar, content=日程卡片
message_type=image, content=这是图片
message_type=file, content=这是文件
```

Notes:

- This is safe because DingTalk has already classified the message as non-text.
- If DingTalk omits the type, fallback text-shape rules apply.

### Rendered Media Or Calendar Placeholders

Current behavior: `skip_before_agent`.

Current code prefix list:

```regex
^\[(文件|图片|视频|日程)\]
```

Examples:

```text
[文件] product.md
[图片]
[视频]
[日程]
```

Notes:

- `[Ding]` is intentionally not in this skip list anymore.
- A raw ordinary external image URL is not covered by this rule.

### DingTalk Internal Link At Message Start

Current behavior: `skip_before_agent`.

Current code condition:

```regex
^\[dingtalk://
```

Examples:

```text
[dingtalk://dingtalkclient/page/flash_minutes_detail?...](dingtalk://dingtalkclient/page/flash_minutes_detail?...)
```

Notes:

- This catches DingTalk-rendered internal cards such as AI meeting notes links.
- It is intentionally narrower than all links. `https://example.com/a` should go
  to the agent.

### DingTalk Internal Or Rendered Media Link-Only Message

Current behavior: `skip_before_agent`.

Current code requires all of the following:

```regex
(
  dingtalk://
  | https?://[^\s)]*dingtalk\.com
  | \[(?:文件|图片|视频|日程)\]
)
```

and:

```regex
no question mark outside links: [？?]
```

and:

```text
after removing links/media and @mentions, remaining information units <= 2
```

Examples that skip:

```text
@磊哥 [dingtalk://dingtalkclient/page/flash_minutes_detail?x=1](dingtalk://dingtalkclient/page/flash_minutes_detail?x=1)
[https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?...](https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?...)
![图片](@lQLPJwKtm28)
```

Examples that do not skip:

```text
这个链接里的方案怎么看？ https://example.com/a
@磊哥 https://example.com/a
https://github.com/alchaincyf/darwin-skill @Derek Zen 这个达尔文 skill 挺有意思
```

Notes:

- Ordinary external links go to `agent_review`.
- DingTalk online document links may be prefetched before the agent when they
  use the supported online-document URL shape.

### Structured DingTalk Link Card

Current behavior: `skip_before_agent`, except approval/OA links.

Current code requires:

```regex
(
  dingtalk://
  | https?://[^\s)]*dingtalk\.com
  | \[(?:文件|图片|视频|日程)\]
)
```

and no question mark outside links:

```regex
[？?]
```

and structured field lines:

```regex
^\s*[^:：\n]{1,60}[:：]\s*\S+
```

with:

```text
line count >= 4
field_line_count >= 3
field_line_count / line_count >= 0.45
```

Example that skips:

```text
表单标题
字段一: A
字段二: B
字段三: C
字段四: D
[dingtalk://dingtalkclient/action/open_platform_link?x=1](dingtalk://dingtalkclient/action/open_platform_link?x=1)
```

Example that does not skip because it is an approval/OA link:

```text
闫成成提交的项目立项全流程（第一曲线）
项目经理: 闫成成
销售经理: 曹宇航
项目类型: 点云;图片;视频
总预估数据量: 2546573
[dingtalk://dingtalkclient/action/open_platform_link?pcLink=https%3A%2F%2Faflow.dingtalk.com%2F...%26swfrom%3Doa%26dinghash%3Dapproval](dingtalk://dingtalkclient/action/open_platform_link?x=1)
```

### Approval/OA Link Exception

Current behavior: `agent_review` with OA review requirements.

Current code recognizes approval/OA links with:

```regex
aflow\.dingtalk\.com|dinghash(?:=|%3D)approval|swfrom(?:=|%3D)oa
```

Examples:

```text
[Ding]张静提醒您审批他的录用申请
[Ding]黄楚提醒您审批他的费用报销对外付款综合审批单
贾金鹏提交的项目立项全流程（第一曲线）
... swfrom%3Doa ... dinghash%3Dapproval ...
```

Routing:

- Do not skip before agent.
- Agent must read `management/OA/钉钉审批审阅原则.md`.
- Agent must not approve, reject, return, or promise approval action unless all
  substantive materials are available and read.
- If approval materials are missing, the correct outcome is material gap
  explanation or human handoff, not a final approval judgment.

## Agent-Handled Message Types

These are intentionally not hard-skipped by code.

### Ordinary External Links

Current behavior: `agent_review`.

Examples:

```text
@磊哥 https://example.com/a
https://github.com/alchaincyf/darwin-skill @Derek Zen 这个达尔文 skill 挺有意思
这个链接里的方案怎么看？ https://example.com/a
```

Reason:

- A plain external URL can be a real request, a document to review, or just
  FYI. The meaning depends on text and context.

Candidate structural regex for detection only:

```regex
https?://(?![^\s)]*dingtalk\.com)\S+
```

Recommended use:

- Use this to label the message as containing an external link.
- Do not use it to skip the agent.

### DING Approval Reminders

Current behavior: `agent_review`.

Examples:

```text
[Ding]张静提醒您审批他的录用申请
[Ding]于海龙提醒您审批他的云资源费用&服务器使用审批
[Ding]磊哥 房租已经过期，求审批
```

Candidate structural regex:

```regex
^\[Ding\].{0,80}(审批|求审批|请.*审批|催办)
```

Recommended use:

- Route to the agent with OA review rules.
- Do not skip before agent.

### Approval/OA Text Without DingTalk Link

Current behavior: `agent_review`.

Examples:

```text
请磊哥审批
这个录用申请麻烦看一下
费用报销对外付款综合审批单需要处理
```

Candidate regex for detection only:

```regex
(审批|催办|提交).{0,30}(申请|单|流程|全流程|报销|合同|录用|晋升|立项)
```

Recommended use:

- Route to the agent with OA review rules.
- Do not use this as `skip_before_agent`; it is business meaning, not transport
  structure.

### Real-World Action Requests

Current behavior: `agent_review`, usually `handoff_to_human`.

Examples:

```text
辛苦明姐去磊哥家里取一下身份证，拿到后我尽快去银行办理 @Derek Zen
候选人在外出差，需要临时找个面试的位置，预计晚5分钟，我稍后呼叫两位
磊哥你进下会议
```

Candidate regex for detection only:

```regex
(进会|参加会议|接电话|呼叫|到现场|取.*身份证|身份证原件|开户|放款|审批通过后办理)
```

Recommended use:

- This should not auto-send a status reply.
- Keep as model/prompt logic or a high-risk detector that forces
  `handoff_to_human`.

## Candidate Rules Not Yet Implemented

These need 2-3 real samples each before code changes.

### Automatic Sync Notifications

Possible examples:

```text
自动同步完成：xxx
AI 自动同步完成：xxx
已同步到知识库：xxx
```

Candidate regex:

```regex
^(AI\s*)?自动同步(完成|成功|失败)?[:：].*
|^已同步到(知识库|文档|项目)[:：].*
```

Suggested conditions before skipping:

- No `?` or `？`.
- No explicit request after the status line.
- No approval/OA link.
- No ordinary external URL that might require review.

### File State Notifications

Possible examples:

```text
文件已上传：xxx.pdf
文件已更新：xxx
文档已更新：xxx
已更新文档：xxx
```

Candidate regex:

```regex
^(文件|文档).{0,8}(已上传|已更新|上传完成|更新完成)[:：].*
|^已更新文档[:：].*
```

Suggested conditions before skipping:

- No explicit question.
- No `麻烦看一下` / `请 review` / `给 comments` style request in the same message.
- No attached ordinary DingTalk file that the user asks to review.

Do not skip examples:

```text
文件已更新，麻烦磊哥看一下
已更新文档，帮忙给 comments
```

### Generic Project Or Workflow Status Notifications

Possible examples:

```text
项目立项已提交
流程已流转到你
审批已通过
审批被退回
```

Candidate regex:

```regex
^(项目立项|流程|审批).{0,20}(已提交|已通过|被退回|已退回|已撤回|已流转).*
```

Suggested routing:

- If it contains approval/OA link markers, send to agent with OA review rules.
- If it is only status and no question/request, possible future
  `skip_before_agent`.
- If it asks Derek to act, send to agent or handoff.

### Link Already Provided / Attachment Follow-Up

Seen example:

```text
@磊哥 链接已放
[dingtalk://dingtalkclient/action/open_mini_app?...]
```

Candidate regex:

```regex
(@[^\s]+.*)?(链接|材料|附件).{0,8}(已放|已发|已上传|补上).*
```

Suggested routing:

- If this is a response to Derek asking for the material, skip or let agent
  judge from context.
- If it is a new review request, agent must read the linked material.
- Not safe for unconditional code skip.

## Summary Table

| Message type | Current route | Implemented code rule | Proposed next action |
| --- | --- | --- | --- |
| Non-text message type | skip before agent | yes | keep |
| `[文件]`, `[图片]`, `[视频]`, `[日程]` | skip before agent | yes | keep |
| `[Ding]` approval reminder | agent review | yes, by removing from skip prefixes | keep |
| DingTalk internal link only | skip before agent | yes | keep unless user asks to inspect link |
| Ordinary external link | agent review | yes, by excluding from link-only skip | keep |
| Structured DingTalk card | skip before agent | yes | keep |
| Structured approval/OA card | agent review | yes | keep |
| OA/approval text without link | agent review | no hard route, prompt rule only | consider detector, not skip |
| Automatic sync notification | agent/model or no sample | no | collect samples before code |
| File state notification | agent/model or no sample | no | collect samples before code |
| Project/workflow status notification | agent/model | partial OA link exception only | add only after samples |
| Real-world action request | agent review / handoff | prompt rule only | keep semantic in agent |

