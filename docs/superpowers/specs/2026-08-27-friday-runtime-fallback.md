# Friday Runtime fallback HTTP contract

Status: frozen against Friday Runtime commit `939232c` (2026-08-27).

## Purpose

CEO Agent will use Friday Runtime as a route-neutral execution backend. Friday
owns provider selection and credentials, including providers that expose Chat
Completions such as MiniMax. CEO Agent sends task text, waits for the Friday
operation to finish, and consumes the resulting Artifact as one normalized
runtime result.

This document freezes transport facts for the adapter. It does not introduce
audit, approval, authorization, safety, effect-reconciliation, or external
business-action policy.

## Verified endpoints

All endpoints are under the configured Friday Runtime base URL and use the
versioned `/v1` API path. Successful JSON responses are wrapped by Friday's response
middleware as:

```json
{"result":"success","code":200,"message":null,"data":{}}
```

Errors use the same envelope with `result: "fail"`, an HTTP-derived `code`,
and a non-secret `message`.

## Authentication prerequisite

When Friday's `/v1` authentication middleware is enabled, every request must
carry exactly one of these credentials:

```text
Authorization: Bearer <RuntimeTicket>
X-Friday-Session-Token: <token>
```

The adapter-facing execution input must provide either `runtime_ticket` or
`friday_session_token`. A local test runtime may explicitly declare
`auth_disabled`; that mode carries no credential and is not an implicit
production default. The CEO Agent must pass the selected header on every
request and must never log its value.

### 1. Create a thread

`POST /v1/threads`

Request minimum (the project must already exist in Friday):

```json
{
  "project_id": "project-id",
  "title": "CEO Agent task",
  "dispatch_mode": "wait"
}
```

`project_id` is mandatory and is copied verbatim from the adapter execution
input; the mapping is deterministic and the adapter must not invent a project
or silently omit this field.

The successful `data` contains a `thread` view. The adapter requires a nonempty
`data.thread.thread_id`. The thread view contains `artifact_id` when the
runtime has created its thread Artifact.

### 2. Create a user turn

`POST /v1/threads/{thread_id}/turns`

Request minimum:

```json
{
  "message": {
    "role": "user",
    "intent": "send_message",
    "text": "...",
    "parts": []
  },
  "execution": {"dispatch_mode": "background"}
}
```

The background response is HTTP 202 and contains `data.operation` plus the
thread/turn/run identifiers in its operation request payload. The adapter must
retain the returned `operation_id` and `turn_id`; it must not synthesize a
second CEO Agent run.

### 3. Execute or retry the turn

`POST /v1/turns/{turn_id}/runs`

Request minimum:

```json
{"dispatch_mode": "background"}
```

This endpoint is the supported retry path for an existing turn. The initial
message endpoint already schedules the first run, so an adapter should call
this endpoint only when it is explicitly retrying a failed turn.

### 4. Poll an operation

`GET /v1/operations/{operation_id}`

The normalized `data.operation.status` values are `pending`, `running`,
`completed`, `failed`, `cancelled`, and `abandoned`. Only `completed` is a
successful execution result. `failed`, `cancelled`, and `abandoned` are
terminal failures and must retain the operation's non-secret error text.

### 5. Read the final Artifact

`GET /v1/artifacts?thread_id={thread_id}`

The normalized response contains `data.items`, a list of Artifact objects.
The adapter selects the Artifact associated with the target thread and
requires its `final_message` to be a nonempty string. Artifact outputs may
also contain text output records, but `final_message` is the stable final
result field for this contract.

The CEO-side normalized result calls this object `artifact`; the wire response
uses `items` because it is a collection endpoint. Missing items, a mismatched
thread, or a missing final message are invalid runtime results.

## State and timeout rules

The adapter execution input is `project_id` plus a nonempty `prompt`, together
with exactly one supported auth credential (or explicit local auth-disabled
mode). The adapter creates one Friday thread and one user turn for one
execution attempt. It polls the operation until a terminal status or its
bounded client timeout. Polling is transport waiting only; it does not create a new Consumer
or Audit run, consume a CEO task retry, or perform an external business write.

The adapter must not log authorization headers, provider credentials, or raw
Friday configuration. HTTP errors are mapped by the adapter task to stable
`friday_runtime_*` codes while preserving only safe status/detail text.

## Provenance

The contract was checked against:

- `friday-runtime/src/friday_runtime/api/main.py`: route declarations,
  response-envelope middleware, and operation handling;
- `friday-runtime/src/friday_runtime/api/schemas.py`: request fields;
- `friday-runtime/src/friday_runtime/services/runtime_service.py`: thread,
  turn, run, and artifact service behavior;
- `friday-runtime/src/friday_runtime/services/thread_service.py`: thread view
  and Artifact lookup behavior;
- `friday-runtime/src/friday_runtime/async_ops/models.py` and
  `friday-runtime/src/friday_runtime/artifacts/models.py`: status and Artifact
  shapes.
