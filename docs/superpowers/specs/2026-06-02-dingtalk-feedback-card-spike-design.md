# DingTalk Feedback Link Spike Design

## Goal

Collect lightweight feedback from the counterparty without requiring a DingTalk
robot in every group. The reply is still sent by the current user through the
existing text-message path. The feedback links open a simple Vercel-hosted page
deployed by the installer; this repo must not provide or depend on a shared
hard-coded feedback service URL.

## Scope

This is a feasibility spike only. It does not change the production
`send_reply` delivery path yet.

In scope:

- Add a Vercel feedback page for one reply.
- Add five rating choices: `特别没用`, `不太有用`, `一般`, `很有用`, `非常有用`.
- Map quick links so `踩` preselects `不太有用` and `赞` preselects `很有用`.
- Add an optional comment field.
- Show the original message and reply sample when the link carries them.
- Store minimal submitted feedback events in Vercel Blob for inspection.
- Keep the diagnostic event-list endpoint.

Out of scope:

- No DingTalk robot cards.
- No requirement to install a robot in each group.
- No SQLite schema changes.
- No production rollout of feedback on all replies.
- No full chat history stored in Vercel.

## Architecture

The local service generates two feedback URLs and appends them to a normal
DingTalk message sent through `dws chat message send`. The URL base comes from
`CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL`; leaving it empty disables feedback links.
Each installation should deploy its own Vercel project and set its own
`FEEDBACK_SPIKE_SECRET`.

- `GET /api/dingtalk-feedback-spike`
  - Renders an HTML feedback page.
  - Reads `feedback_token`, `rating`, `original_text`, and `reply_text` from the
    query string.
  - Does not persist feedback on page load.

- `POST /api/dingtalk-feedback-spike`
  - Accepts the selected five-level rating and optional `comment`.
  - Persists a minimal event in Vercel Blob under
    `feedback-spike-events/feedback-spike:<timestamp>:<random>.json`.
  - Renders a short confirmation page.
  - Still supports JSON output when `format=json` or an JSON `Accept` header is
    used.

- `GET /api/dingtalk-feedback-spike-events`
  - Lists recent captured events.
  - Requires either the shared diagnostic secret or a specific `feedback_token`.

## Local Commands

Preview a group message:

```bash
ceo-agent feedback-spike send-links \
  --preview \
  --vercel-base-url https://your-vercel-app.vercel.app \
  --conversation-id '<openConversationId>' \
  --original-text '对方原话' \
  --reply-text 'CEO agent 回复样例'
```

Preview a private message target:

```bash
ceo-agent feedback-spike send-links \
  --preview \
  --vercel-base-url https://your-vercel-app.vercel.app \
  --user-id '<receiverUserId>' \
  --original-text '对方原话' \
  --reply-text 'CEO agent 回复样例'
```

Generate the diagnostic event query URL:

```bash
ceo-agent feedback-spike events-url \
  --vercel-base-url https://your-vercel-app.vercel.app \
  --secret "$FEEDBACK_SPIKE_SECRET" \
  --limit 20
```

## Validation Criteria

- The sent DingTalk message is a normal current-user text message.
- The message contains `赞` and `踩` feedback links.
- Opening a feedback link renders a designed HTML page, not raw JSON.
- `赞` preselects `很有用`; `踩` preselects `不太有用`.
- The page shows original text and reply sample when available.
- Submitting the form records `feedback_token`, five-level `rating`, rating
  label, optional `comment`, original text, and reply sample.

## Privacy

- Vercel stores only the feedback token, rating, optional comment, and the small
  context carried by the link.
- The diagnostic event-list endpoint remains protected by secret unless queried
  for a single feedback token.
- Sensitive request headers are redacted before persistence.
