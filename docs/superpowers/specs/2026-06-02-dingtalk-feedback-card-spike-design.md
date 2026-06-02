# DingTalk Feedback Card Spike Design

## Goal

Verify whether DingTalk interactive cards can collect reply feedback from the
counterparty through a Vercel callback before changing the production
`send_reply` path.

The spike answers three questions:

- Can DWS send a DingTalk card that contains the split-person reply text?
- Can the card show `赞` and `踩` buttons that call a Vercel endpoint?
- Can the callback carry enough structured data to bind feedback to one reply,
  at minimum `feedback_token` and `rating`?

## Scope

This is a feasibility spike only. It does not change production reply behavior.

In scope:

- Add a Vercel test endpoint for feedback callbacks.
- Add a Vercel query endpoint for recent captured spike events.
- Add a local spike command or script that sends one test card through
  `dws chat message send-card`.
- Store only minimal callback payloads in Vercel KV for inspection.

Out of scope:

- No changes to the worker `send_reply` delivery path.
- No changes to SQLite schema.
- No feedback token table.
- No agent prompt or memory behavior changes.
- No production DingTalk conversation rollout unless a test target is explicitly
  provided.
- No full chat content stored in Vercel.

## Architecture

The spike uses Vercel as the public callback receiver because the local
`127.0.0.1:8765` service is not reachable from DingTalk. Vercel only records
test callback events. The local CEO service remains the future source of truth
for production audit data.

Components:

- `POST/GET /api/dingtalk-feedback-spike`
  - Public callback endpoint for card button clicks.
  - Accepts both GET and POST because the exact DingTalk card action behavior is
    not yet known.
  - Records method, query, body, a safe subset of headers, and received time.
  - Stores events under Vercel KV keys such as
    `feedback-spike:<timestamp>:<random>`.
  - Returns a simple success payload unless DingTalk requires a different
    response format discovered during testing.

- `GET /api/dingtalk-feedback-spike-events`
  - Diagnostic endpoint to list recent spike events.
  - Protected by a shared secret.
  - Used only by the local operator to verify callback delivery and payload
    shape.

- Local spike sender
  - Sends one test card through `dws chat message send-card`.
  - Takes the test conversation/user target from CLI arguments.
  - Generates a `feedback_token` such as `spike_<timestamp>_<random>`.
  - Builds a minimal card containing a sample split-person reply and two
    feedback actions:
    - `rating=up`
    - `rating=down`
  - Includes `source=ceo-agent-spike` and the generated token in the button
    callback URL or card action payload.

## Data Flow

1. Operator runs the local spike sender with a test DingTalk target.
2. The sender calls `dws chat message send-card`.
3. DingTalk displays a card that contains the reply text and `赞` / `踩`
   controls.
4. The counterparty clicks one control.
5. DingTalk calls the Vercel spike endpoint.
6. Vercel writes the callback event to KV.
7. Operator queries the diagnostic endpoint and checks whether token, rating,
   and any actor identity were captured.

## Validation Criteria

The spike is successful only if all core criteria pass:

- The card sends successfully and renders correctly in DingTalk.
- Clicking `赞` or `踩` produces a request at Vercel.
- The callback includes `feedback_token` and `rating`.

Actor identity is preferred but not required for baseline feasibility. If actor
identity is missing, production design can still bind feedback by token and the
reply context, then decide separately whether stronger identity is needed.

## Failure Modes

- `send-card` fails:
  - Capture the DWS error summary.
  - Do not retry production replies.
  - Treat as card capability or parameter failure.

- Card sends but buttons do not render:
  - Adjust card data or template shape.
  - Continue only inside the spike.

- Buttons render but no Vercel callback arrives:
  - Treat as evidence that DingTalk card actions are not simple URL callbacks,
    or that callback setup requires additional DingTalk configuration.

- Vercel receives a callback but token or rating is missing:
  - Treat as parameter binding failure.
  - Adjust button URL/action payload and repeat the spike.

- Vercel receives token and rating but no actor:
  - Baseline feasibility passes.
  - Record actor identity as a follow-up investigation.

## Production Follow-Up If Spike Passes

If the spike passes, the production design should use a feature flag before
changing all replies:

- Only `send_reply` uses card-as-reply.
- `ask_clarifying_question`, handoff acknowledgements, and OA actions remain
  unchanged.
- Card delivery failure falls back to the current plain-text reply path.
- The feedback card carries an opaque `feedback_token`, not a local SQLite ID.
- Vercel KV stores minimal feedback events.
- The local service pulls unsynced feedback from Vercel and writes it into local
  SQLite.

## Security and Privacy

- Do not store full DingTalk chat context in Vercel.
- Use an opaque token for reply binding.
- Protect the diagnostic event-list endpoint with a shared secret.
- Redact sensitive headers before storing callback events.
- Keep Vercel as a temporary event inbox, not the authoritative audit store.

## Open Questions for the Spike

- Does `dws chat message send-card` require a pre-created `cardTemplateId`?
- What is the required shape of `cardData` for buttons?
- Does DingTalk support URL-style button actions for this card type?
- Does the callback include click actor identity?
- Does the callback response need a DingTalk-specific JSON format?
