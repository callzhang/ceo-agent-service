# CEO Agent Memory Outbox Design

Date: 2026-05-29

## Goal

Add durable memory support to the CEO DingTalk reply service without making memory availability part of the reply delivery critical path.

The service should:

- Let the Codex decision agent use the installed `memory_connector` MCP tools when it decides prior context is useful.
- Record every real reply event as a complete memory episode for later recall.
- Preserve DingTalk reply reliability when the memory backend is slow, down, or returns an error.

The service should not pre-inject recalled memory into every prompt. Recall should be an available tool, not a mandatory prompt block.

## Current Context

`CodexRunner.build_command()` currently launches the decision agent with:

- `--disable plugins`
- `--ignore-user-config`
- explicit `developer_instructions`
- explicit safety/runtime config

That means the installed memory connector in `~/.codex/config.toml` will not automatically appear inside CEO service Codex sessions. This is intentional isolation for the reply worker, but it means memory access must be added explicitly to the `codex exec` command produced by the service.

The service already records reply attempts and sent replies in SQLite. The correct write point for memory is after the service knows the final result of the event, not while the agent is drafting.

## Design

### 1. Expose Memory MCP To Codex Exec

Update `CodexRunner.build_command()` so CEO service Codex sessions get the `memory_connector` MCP server explicitly.

Keep the current isolation:

- Keep `--ignore-user-config`.
- Do not restore global plugin loading.
- Do not load arbitrary installed plugins.

Add explicit config equivalent to:

- `mcp_servers.memory_connector.url`
- `mcp_servers.memory_connector.bearer_token_env_var`
- `mcp_servers.memory_connector.env_http_headers`

The runner environment should expose:

- `CONNECTOR_API_KEY`
- `MEMORY_CONNECTOR_USER_ID=derek`
- `MEMORY_CONNECTOR_URL`, if needed by local hooks or diagnostics

The user id must be passed as `derek` when the agent calls `user_get`, `memory_recall`, `memory_write`, or `document_upload`.

The developer prompt should state that memory MCP is available for durable project, person, decision, and event recall, but it should not require recall for every trivial reply.

### 2. Add Memory Write Outbox

Add a SQLite table `memory_write_events`:

```sql
create table if not exists memory_write_events (
    id integer primary key,
    attempt_id integer not null,
    event_type text not null,
    payload_json text not null,
    status text not null default 'pending',
    attempts integer not null default 0,
    last_error text not null default '',
    memory_episode_id text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique(attempt_id, event_type)
);
```

`event_type` values:

- `reply_sent`
- `review_correction`

The outbox is the reliability boundary. Reply delivery can succeed even if memory writing fails.

### 3. Episode Payload

Each memory episode should record the event, not only the final reply text.

For `reply_sent`, write JSON with:

- `event`: `reply_sent`
- `conversation`: `conversation_id`, `title`, `single_chat`
- `trigger`: `message_id`, `sender`, `text`, `created_at` if available
- `decision`: `action`, `sensitivity_kind`, `codex_reason`, `audit_summary`
- `result`: `final_reply_text`, `send_status`, `sent_at`
- `provenance`: `attempt_id`, `codex_session_id`, transcript line range, compact send result metadata

For `review_correction`, write JSON with:

- `event`: `review_correction`
- the same conversation and trigger fields
- `original`: action, reason, draft reply, final reply, send status
- `review`: `reviewer_feedback`, `corrected_reply_text`, `reviewed_at`
- `provenance`: `attempt_id`, `codex_session_id`

Do not include secrets, tokens, raw command output, or full Codex transcripts.

### 4. Write Triggers

Create or update an outbox event when:

- `_deliver_final_reply()` successfully marks a `send_reply` or `ask_clarifying_question` attempt as `sent`.
- The handoff branch successfully sends the handoff acknowledgement.
- `handle_feedback_post()` records reviewer feedback or corrected reply.
- `record_feedback_command()` records reviewer feedback or corrected reply.
- `handle_reviewed_message_reply()` sends a manually reviewed reply and records feedback.

Do not create memory events for:

- `no_reply` unless a later review correction exists.
- `stop_with_error`.
- `failed`.
- `blocked`.
- `dry_run`.
- replies that were later only drafted but not sent.

### 5. Flush Command

Add a CLI command `flush-memory-events`.

Behavior:

- Select `pending` and retryable `failed` events.
- Mark each selected row `processing` before calling memory.
- Call `memory_write(type="json", user_id="derek", data=payload_json, created_at=event created_at, source_description=..., source_metadata=..., provenance_metadata=...)`.
- On success, mark `sent` and store returned episode id if available.
- On failure, increment `attempts`, store `last_error`, and mark `failed`.
- Never send or resend DingTalk messages.

The first implementation can run this command manually or from launchd. It does not need to be inside the consumer loop.

### 6. Audit Page

Show memory write state on attempt detail:

- no event
- pending
- sent with episode id
- failed with last error

This makes "reply was sent but memory did not write" visible without reading SQLite manually.

## Failure Handling

Memory write failures must not change `reply_attempts.send_status`.

If the memory backend returns 502, times out, or rejects a payload:

- the reply remains sent,
- the outbox records the failure,
- the next flush can retry.

This keeps memory as durable event capture, not as a reply delivery dependency.

## Testing

Add tests for:

- Codex exec command exposes only `memory_connector` MCP config while preserving `--ignore-user-config`.
- Runner environment includes memory connector variables when configured.
- Successful sent reply creates one `reply_sent` event.
- Reprocessing the same sent attempt does not duplicate the outbox row.
- Failed, blocked, dry-run, and no-reply attempts do not create `reply_sent`.
- Feedback via audit web creates `review_correction`.
- Feedback via CLI creates `review_correction`.
- Flush success marks an event `sent` and stores the memory episode id.
- Flush failure records `last_error` and does not modify the reply attempt.

## Open Implementation Notes

- The memory backend endpoint is currently configured by the installer under `~/.codex/memory_connector.env`. The service should read the same environment variables where possible, but runtime launchd configuration may need to source that env file explicitly.
- The actual Codex CLI config syntax for nested MCP server values should be verified with a command-level test before implementation.
- The memory backend was observed returning 502 during design. The outbox design assumes this can happen in production and treats it as retryable.
