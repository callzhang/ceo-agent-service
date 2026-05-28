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
- Codex prompt separation: stable CEO reply policy is passed as developer
  instructions, while each DingTalk turn is sent as the user message payload.
- Local SQLite history for reply attempts, send status, errors, feedback, and
  organization cache.
- Human-handoff state: when a conversation has been handed to the real user,
  the live worker pauses auto-replies and sends a clear local notification
  instead. Dry-run checks do not repeat that pause notification.
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
- `CEO_NOT_SEND_MESSAGE`: defaults to `1`; records decisions but does not send
  DingTalk messages. `CEO_DRY_RUN` is still accepted as a compatibility alias.
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

## Style Corpus

The local style corpus is stored under `CEO_CORPUS_DIR` and is not committed to
Git. The main file is `derek_style_corpus.csv`; `build-corpus` also writes a
derived `style_profile.md` in the same directory.

Build the corpus from local AI meeting-note Markdown files:

```bash
cd apps/local-service
.venv/bin/ceo-agent build-corpus \
  --workspace /Users/derek/Documents/memory \
  --corpus-dir /Users/derek/Documents/Projects/ceo-agent-service/corpus
```

This scans `AI听记/**/*.md` under `--workspace`, extracts Derek-style speaker
records, rewrites `derek_style_corpus.csv`, and refreshes `style_profile.md`.

Append recent DingTalk messages sent by the current `dws` user:

```bash
cd apps/local-service
.venv/bin/ceo-agent collect-corpus \
  --workspace /Users/derek/Documents/memory \
  --corpus-dir /Users/derek/Documents/Projects/ceo-agent-service/corpus
```

This uses authenticated `dws` read APIs, looks back 183 days, and appends
eligible sent-message records to `derek_style_corpus.csv`.

`build-work-profile` uses the same corpus by default: it rebuilds the local
AI-meeting corpus, appends DingTalk sent-message samples, then builds profile
evidence. Use `--skip-minutes-corpus` or `--skip-dingtalk-messages` when you
need to keep either corpus source unchanged for that run.

For the full profile distillation workflow, see
`docs/work-profile-distillation-tutorial.md`.

## Run

One no-send pass:

```bash
cd apps/local-service
CEO_NOT_SEND_MESSAGE=1 .venv/bin/ceo-agent run-once --not-send-message
```

Continuous no-send worker:

```bash
cd apps/local-service
CEO_NOT_SEND_MESSAGE=1 .venv/bin/ceo-agent run --not-send-message
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

On macOS, run it at login with the launchd agent:

```bash
mkdir -p ~/Library/Logs/ceo-agent-service ~/Library/LaunchAgents
cp launchd/com.derek.ceo-agent-service.audit-web.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.derek.ceo-agent-service.audit-web.plist
launchctl kickstart -k "gui/$(id -u)/com.derek.ceo-agent-service.audit-web"
```

This only starts the audit console; it does not run the auto-reply worker.

To run the live auto-reply pipeline, install the launchd agents:

```bash
scripts/install-auto-reply-agents.sh
```

The producer launchd agent runs `produce-once` every 5 minutes and only queues
eligible DingTalk messages. The consumer launchd agent runs `consume`
continuously and claims queued tasks one at a time before generating and sending
replies. This keeps frequent DingTalk checks from starting duplicate generation
work when a previous reply is still processing.

Generated decisions and send results are written to SQLite. To manually send a
specific reviewed attempt:

```bash
cd apps/local-service
CEO_NOT_SEND_MESSAGE=0 CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1 .venv/bin/ceo-agent send-attempt --attempt-id 123
```

The producer script uses a lock directory so a slow previous DingTalk check is
not overlapped by the next 5-minute trigger. To stop the launchd agents:

```bash
launchctl bootout "gui/$(id -u)/com.derek.ceo-agent-service.hourly-dry-run"
launchctl bootout "gui/$(id -u)/com.derek.ceo-agent-service.dry-run-consumer"
launchctl bootout "gui/$(id -u)/com.derek.ceo-agent-service.reply-producer"
launchctl bootout "gui/$(id -u)/com.derek.ceo-agent-service.reply-consumer"
```

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
- `docs/work-profile-distillation-tutorial.md`: how to regenerate the local
  Derek work profile from corpus, local documents, and read-only DingTalk
  knowledge base evidence.
- `SECURITY.md`: security policy and secret-handling expectations.

## License

MIT
