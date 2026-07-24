# Universal Consumer Agent

## Architecture

The universal consumer separates decision making from side effects:

1. The service loads the immutable reply task and checks blocking dependencies. DWS readiness is checked before Codex starts.
2. Codex produces a typed `UniversalPlan`. It does not log in to DWS and does not execute external actions.
3. The service validates dependencies, trusted targets, permissions, sensitivity, duplicate state, and dry-run policy.
4. The service persists the plan identity and executes each action through a capability-specific executor.
5. Every action has a durable execution row. History and attempt detail read a redacted projection containing only planner kind, capability, dependency names, action kinds, states, and safe error summaries.

Structured AI-minutes permission requests and system notifications are handled by
deterministic ingress capabilities before planning. They are protocol handling,
not semantic routing. OKR review requests use the explicit `queue_okr_review`
action so realtime OKR retrieval, request creation, and acknowledgement remain
durable and auditable.

Before planning, the service freezes recoverable OA follow-up targets, full
calendar invitation details and conflict results, downloaded image paths, and
read-only task details relevant to the conversation, sender, and trigger text.
The task context includes matching project fields, TODO descriptions, owners,
deadlines, recent updates, follow-ups, and linked DingTalk Todo state so the
planner can answer task-status questions without guessing from chat snippets
alone. Planner tool-call proof is persisted with the plan and copied into action
audit events; tool inputs and outputs are omitted from that proof.

Agents can also query task context on demand through the local read-only JSON API:

- `GET /api/task-management/search?q=<text>&conversation_id=<id>&owner_user_id=<id>&limit=3`
- `GET /api/task-management/projects/<project_id>`

These endpoints expose the same project, TODO, update, follow-up, and DingTalk
Todo-link context used by planner enrichment. They do not create or update tasks.

The universal observability projection never exposes the stored plan JSON, action target, action payload, canonical payload, or action result JSON.

## Terminal States

An action reaches one of these persisted states:

- `succeeded`: verified complete. A replay skips it.
- `failed`: definitely failed before a side effect completed. The task may follow the normal bounded retry policy.
- `unknown`: an external call may have completed but could not be verified. Automatic replay stops.
- `not_started`: no persisted action row exists yet.
- `started` or `recovering`: execution is in progress or using a supported recovery checkpoint.

A plan is complete only when all permitted actions are terminal according to their capability contract. `no_reply`, handoff, and terminal blocked decisions are still explicit planned actions rather than implicit routing outcomes.

## UNKNOWN Recovery

`UNKNOWN` protects against duplicate externally visible actions. The operator must inspect the target system and the attempt detail before changing state or rerunning:

1. Verify whether the original side effect exists in DingTalk, OA, mail, calendar, document, reaction, or Memory.
2. If it exists, record or recover the verified receipt through the capability's supported recovery path.
3. If it definitely does not exist, mark the original action as definitely failed before rerunning.
4. Never rerun solely because a client timed out.

## Feature Rollback

The universal consumer is controlled by `CEO_UNIVERSAL_CONSUMER` while rollout is in progress. Set it to `0` and restart `com.ceo-agent-service.main` to return new tasks to the legacy consumer. Existing universal plans and action rows remain immutable audit records and must not be deleted or replayed by the legacy path.

Rollback changes routing only. It does not convert `UNKNOWN` actions into retryable failures and does not bypass dependency, permission, or duplicate-send checks.
