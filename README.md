# CEO Agent Service

Local-first DingTalk auto-reply service for executives and team leads.

The service reads unread DingTalk messages through `dws`, asks `codex exec` to
decide whether and how to respond, uses a local workspace plus graphify for
retrieval, and records every decision in SQLite for audit and feedback.

## Why Local-First

- Sensitive knowledge stays on the operator's machine.
- DingTalk is the user interface; no separate chat product is required.
- Replies are traceable through local SQLite rows and local Codex session logs.
- Dry-run is the default, so teams can review decisions before enabling sends.

## Features

- DingTalk unread conversation scanning for direct chats and mentioned group
  messages.
- Local workspace retrieval with graphify-first instructions.
- Structured Codex decision schema with `send_reply`, `no_reply`,
  `ask_clarifying_question`, `handoff_to_human`, and `stop_with_error`.
- Local SQLite history for reply attempts, send status, errors, feedback, and
  organization cache.
- FastAPI audit console with feedback and recall hooks.
- Optional style corpus built from local messages and meeting transcripts.
- Dry-run and live-send guardrails.

## Requirements

- Python 3.11+
- `dws` CLI authenticated for DingTalk
- Codex CLI with `codex exec`
- `graphify` installed in the local knowledge workspace

No runtime DingTalk credentials, SQLite databases, Codex sessions, or corpus
files should be committed to Git.

## Install

```bash
python3 -m venv apps/local-service/.venv
apps/local-service/.venv/bin/pip install -e 'apps/local-service[dev]'
```

Copy `.env.example` to `.env` and edit paths for your machine.

## Configuration

Common environment variables:

- `CEO_WORKSPACE`: local knowledge workspace used by Codex and graphify.
- `CEO_WORKER_DB`: SQLite path for local state.
- `CEO_DRY_RUN`: defaults to `1`; dry-run records decisions but does not send.
- `CEO_CORPUS_DIR`: optional local style corpus directory.
- `CEO_DWS_TRANSIENT_RETRY_ATTEMPTS`: retries for transient `dws` discovery or
  network failures; defaults to `3`.
- `CEO_DWS_TRANSIENT_RETRY_DELAY_SECONDS`: base delay before each transient
  retry; defaults to `1.0` and increases linearly per retry.
- `CEO_DING_ROBOT_CODE` or `DINGTALK_DING_ROBOT_CODE`: optional DING robot code
  for handoff notifications.
- `CEO_DING_ROBOT_NAME`: optional bot name resolved through `dws chat bot search`
  when a robot code is not configured.
- `CEO_DING_RECEIVER_USER_ID`: optional user id for handoff DINGs.
- `CEO_LIVE_SEND_BLOCKERS_ACCEPTED`: explicit opt-in required for live sends.

Persona variables:

- `CEO_PRINCIPAL_NAME`
- `CEO_PRINCIPAL_DISPLAY_NAME`
- `CEO_PRINCIPAL_HANDOFF_NAME`
- `CEO_MENTION_ALIASES`
- `CEO_CURRENT_USER_DISPLAY_NAMES`
- `CEO_STYLE_SPEAKER_NAMES`
- `CEO_ASSISTANT_SIGNATURE`
- `CEO_HANDOFF_ACK`
- `CEO_RESPONSIBILITY_SUMMARY`
- `CEO_FORBIDDEN_PATH_PREFIXES`

Keep the real user `HOME`; do not point `HOME` at the workspace. Codex and `dws`
need their normal local auth state.

## Run

One dry-run pass:

```bash
cd apps/local-service
CEO_DRY_RUN=1 .venv/bin/ceo-agent run-once
```

Continuous dry-run worker:

```bash
cd apps/local-service
CEO_DRY_RUN=1 .venv/bin/ceo-agent run
```

Probe DingTalk/Codex dependencies:

```bash
cd apps/local-service
.venv/bin/ceo-agent probe-dws
```

Refresh local organization cache:

```bash
cd apps/local-service
.venv/bin/ceo-agent refresh-org-cache
```

Run the audit console:

```bash
cd apps/local-service
.venv/bin/python -m ceo_agent_service.cli audit-web --reload --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765/`.

## Feedback

Record feedback on a decision:

```bash
cd apps/local-service
.venv/bin/ceo-agent feedback --attempt-id 123 --feedback "Too strong; ask for source material first."
```

Export reviewed feedback samples:

```bash
cd apps/local-service
.venv/bin/ceo-agent export-feedback --output ../../data/feedback.jsonl
```

## Tests

```bash
cd apps/local-service
.venv/bin/pytest -q
```

Live smoke tests are skipped by default and require explicit opt-in env vars.
They may read real DingTalk messages or send externally visible test messages.

## Documentation

- `docs/product-logic.md`: message handling, privacy, handoff, and audit logic.
- `docs/dws-capabilities.md`: DingTalk `dws` capabilities used by this project.
- `SECURITY.md`: security policy and secret-handling expectations.

## License

MIT
