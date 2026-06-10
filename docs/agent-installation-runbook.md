# Agent Installation Runbook

This runbook is for an agent installing CEO Agent Service on a user's Mac. The
agent should run commands, inspect outputs, edit local config, and report
blocking prompts. Do not ask the user to copy commands into Terminal. Ask the
user only for choices, credentials, QR-code confirmation, OS permission clicks,
or policy decisions that the agent cannot make.

## Install Contract

Goal: leave the machine with a verified local service, prepared corpus/profile
data, and an audit web UI that can be used to review behavior before live send.

Default safety:

- Start in dry-run mode.
- Do not send DingTalk messages until `CEO_NOT_SEND_MESSAGE=0` and
  `CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1` are both explicitly confirmed.
- Do not commit or upload local chat exports, corpus files, SQLite databases,
  Codex sessions, DingTalk tokens, cookies, robot codes, or generated private
  evidence.
- Keep real work data outside the repository, normally under
  `~/Documents/memory`.
- Use the real user `HOME`; do not point `HOME` at the repository because
  `dws`, Codex, launchd, and MCP credentials depend on the user's normal
  profile directories.

## Phase 0: Collect Interactive Parameters

Collect these values before changing the machine. If a value is unknown, inspect
the local machine first and ask only when inspection cannot answer it.

| Parameter | Default | Notes |
| --- | --- | --- |
| Repository path | `~/Documents/Projects/ceo-agent-service` | Must be the service checkout. |
| Workspace path | `~/Documents/memory` | Local knowledge corpus, AI minutes, SOPs, and source docs. |
| Database path | `./data/auto-reply.sqlite3` | Local SQLite runtime state. |
| Corpus path | `./corpus` | Ignored by Git; contains style corpus. |
| Principal display name | user supplied | Used in prompts, aliases, and handoff text. |
| Mention aliases | user supplied | Include exact DingTalk @ aliases, comma-separated. |
| Assistant signature | user supplied | Text appended to automated replies. |
| Handoff acknowledgement | user supplied | Text used when the agent should hand off. |
| Feedback web URL | optional | Vercel/base URL for thumbs-up/down links. |
| Memory Connector URL | optional | Required only if setting up remote memory MCP config. |
| DingTalk KB workspace | optional | Workspace id or URL for profile evidence collection. |
| Live send opt-in | no by default | Ask only after dry-run evidence is reviewed. |

Write chosen values to `.env` from `.env.example`. Keep user-specific values in
`.env` or launchd environment, not in committed docs.

## Phase 1: Preflight The Checkout

1. Read the canonical machine rules:

   ```sh
   sed -n '1,240p' ~/.agents/AGENT.md
   ```

2. Inspect repository state and avoid unrelated changes:

   ```sh
   cd ~/Documents/Projects/ceo-agent-service
   git status --short --branch
   ```

3. Confirm Python and Node are available:

   ```sh
   python3 --version
   node --version
   npm --version
   ```

4. Create or refresh the Python environment:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -e '.[dev]'
   ```

5. Install Node dependencies only when package checks or Vercel/API tests are
   needed:

   ```sh
   npm install
   ```

If any dependency download is blocked by network, package registry, or missing
credentials, report the exact failed command and error.

## Phase 2: Download And Verify Components

### dws

1. Check whether `dws` exists:

   ```sh
   command -v dws
   dws --version
   ```

2. If it exists, check for updates:

   ```sh
   dws upgrade --check --format json
   ```

3. If the update check says an upgrade is available, upgrade it:

   ```sh
   dws upgrade -y --format json
   ```

4. If `dws` is missing, install it from the organization's approved source or
   package artifact. Do not invent a download URL. If the source is unknown, ask
   the user for the approved installer or internal distribution path.

5. Authenticate `dws`:

   ```sh
   dws auth status
   dws auth login
   dws doctor --json --timeout 5
   ```

The login step may require the user to approve a browser page, QR code, or
DingTalk prompt. The agent should initiate the flow and wait for the user's
confirmation instead of asking the user to run commands.

### Codex CLI

1. Confirm Codex can run:

   ```sh
   command -v codex
   codex --version
   ```

2. If Codex is not installed or not authenticated, use the user's approved
   Codex installation path. Do not store API keys in this repository.

3. Confirm continuity support later through a dry-run worker pass; the service
   uses Codex sessions through the local runtime, not a cloud-only worker.

### Memory Connector

If the deployment uses Friday Memory or another Memory Connector MCP endpoint:

```sh
.venv/bin/ceo-agent setup-memory-connector \
  --memory-url '<memory-mcp-url>'
```

Codex config uses the installed MCP Authorization header as the authenticated
OAuth identity. Do not provide or invent a separate `user_id`.

### Nvwa Persona Skill

The Nvwa skill is needed for reviewed profile distillation, not for runtime:

```sh
test -f ~/.agents/skills/nvwa/SKILL.md
```

If it is missing, install or sync the approved internal skill package into
`~/.agents/skills/nvwa`. Generated profile content belongs in this repository,
not in `~/.agents/skills`.

## Phase 3: Configure The Service

1. Create `.env` if absent:

   ```sh
   cp .env.example .env
   ```

   The app loads `CEO_ENV_FILE` automatically. If `CEO_ENV_FILE` is unset, it
   reads this repository's `.env`.

2. Edit `.env` with the Phase 0 values. Minimum fields to set:

   ```text
   CEO_WORKSPACE=$HOME/Documents/memory
   CEO_WORKER_DB=./data/auto-reply.sqlite3
   CEO_CORPUS_DIR=./corpus
   CEO_DRY_RUN=1
   CEO_PRINCIPAL_NAME=<principal display name>
   USER_ALIAS=<principal display name>
   CEO_MENTION_ALIASES=<comma-separated DingTalk @ aliases>
   DOCUMENT_EXTRACTION_IDS=<names used in docs and prompts>
   CEO_ASSISTANT_SIGNATURE=<signature>
   CEO_HANDOFF_ACK=<handoff acknowledgement>
   CEO_LIVE_SEND_BLOCKERS_ACCEPTED=
   ```

3. Keep dry-run on for first validation. For this codebase, dry-run can be set
   as either `CEO_DRY_RUN=1` or `CEO_NOT_SEND_MESSAGE=1`; launchd defaults to
   live processing, so review launchd behavior before installing the service.

4. Verify important paths exist:

   ```sh
   mkdir -p data corpus "$HOME/Documents/memory"
   test -d "$HOME/Documents/memory"
   ```

## Phase 4: Prepare Data Corpus

The workspace should contain readable local materials. Recommended shape:

```text
~/Documents/memory/
├── AI听记/
├── management/
│   ├── OA/
│   └── strategy/
├── recruiting/
├── Thinking/
└── graphify-out/
```

Agent tasks:

1. Confirm `AI听记` and key SOP folders exist. If missing, ask where the user's
   meeting notes, SOPs, HR/recruiting docs, and strategy docs live.
2. Do not move private files into Git. Keep them under `CEO_WORKSPACE` or another
   ignored local data path.
3. Build the local AI-minutes style corpus:

   ```sh
   .venv/bin/ceo-agent build-corpus \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./corpus
   ```

4. Append recent DingTalk sent-message samples:

   ```sh
   .venv/bin/ceo-agent collect-corpus \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./corpus
   ```

This reads through the current `dws` identity. If the command fails on auth or
permission, fix `dws` before continuing.

## Phase 5: Generate And Review The Work Profile

1. Build the initial profile and evidence index:

   ```sh
   .venv/bin/ceo-agent build-work-profile \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./corpus
   ```

2. If the user provided a DingTalk KB workspace id or URL, include it:

   ```sh
   .venv/bin/ceo-agent build-work-profile \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./corpus \
     --dingtalk-kb-workspace '<workspace-id-or-url>'
   ```

3. Expected outputs:

   ```text
   profiles/work_profile.md
   data/profile-evidence/evidence_index.jsonl
   corpus/style_corpus.csv
   ```

4. Run a Nvwa review pass over:

   ```text
   profiles/work_profile.md
   data/profile-evidence/evidence_index.jsonl
   corpus/style_corpus.csv
   ```

5. The Nvwa pass must rewrite only `profiles/work_profile.md`. It must not add
   raw private excerpts, absolute local paths, tokens, session ids, or DingTalk
   cache content.

6. Verify runtime consumption:

   ```sh
   .venv/bin/pytest \
     tests/test_work_profile.py \
     tests/test_prompt.py \
     tests/test_worker.py::test_consumer_codex_command_embeds_work_profile_content \
     -q
   ```

Runtime reads the profile through `app.prompt:work_profile_instruction()`.

## Phase 6: Validate dws Permissions

Run read probes first:

```sh
.venv/bin/ceo-agent probe-dws
dws auth status
dws doctor --json --timeout 5
```

For known online docs or AI tables, validate access by type:

```sh
dws doc info --node '<alidocs-url>' --format json
dws doc read --node '<alidocs-url>' --format json
```

Permissions to verify before live operation:

- DingTalk login and `dws` keychain state are available under the real user
  account.
- The agent can read unread conversations, group context, quoted messages, docs,
  AI tables, contacts, calendar items, OA materials, and AI minutes needed by the
  deployment.
- macOS allows Codex/Terminal process access needed for local files and network.
- Notifications are allowed if macOS notifications are part of the deployment.
- The service can bind the local audit web port, usually `127.0.0.1:8765`.
- OA approval actions and chat sends remain blocked until explicit live-send
  opt-in is reviewed.

If a required permission is unavailable, record the exact missing capability and
whether the right fix is user authorization, DingTalk admin scope, or a narrower
deployment boundary.

## Phase 7: Start Web Management In Dry-Run

1. Start the audit web UI:

   ```sh
   .venv/bin/python -m app.cli audit-web \
     --reload \
     --host 127.0.0.1 \
     --port 8765
   ```

2. Open and inspect:

   ```text
   http://127.0.0.1:8765/
   ```

3. Key pages:

   - `/`: reply history and pending tasks.
   - `/attempts/{id}`: single attempt, prompt, decision, evidence, send status.
   - `/tasks`: project/TODO summary and follow-up drafts.
   - `/codex`: local Codex session references.
   - `/developer-prompt`: prompt templates.
   - `/config`: routing rules and runtime config.
   - `/errors`: unresolved runtime errors.

4. Run one dry-run pass:

   ```sh
   CEO_NOT_SEND_MESSAGE=1 .venv/bin/ceo-agent run-once --not-send-message
   ```

5. Review the web UI for:

   - no unresolved `processing` or `failed` backlog
   - no leaked local paths, tokens, session ids, or raw tool output
   - correct routing for group @, single chat, OA, docs, calendar, and permission
     request cases
   - no unexpected live send

## Phase 8: Install launchd Service

Install launchd only after dry-run behavior and configuration are reviewed.

1. Inspect `launchd/com.ceo-agent-service.main.plist`. Confirm service root,
   workspace, DB, corpus path, principal/persona variables, and live-send
   defaults match the deployment.

2. If launchd should start in dry-run, edit the plist or environment before
   installation. The current template sets `CEO_NOT_SEND_MESSAGE=0`, so do not
   install it blindly on a fresh machine.

3. Install:

   ```sh
   scripts/install-auto-reply-agents.sh
   ```

4. Verify:

   ```sh
   launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
   curl -fsS http://127.0.0.1:8765/ >/tmp/ceo-agent-home.html
   ```

5. Check logs:

   ```sh
   ls -lah ~/Library/Logs/ceo-agent-service
   ```

6. Check the audit UI for unresolved failures or stuck tasks before reporting
   completion.

## Phase 9: Optional Live Send Enablement

Only after reviewing dry-run attempts with the user:

1. Confirm the exact live scope: which chats, which aliases, which actions, and
   whether OA/calendar/task follow-up actions are allowed.
2. Set:

   ```text
   CEO_NOT_SEND_MESSAGE=0
   CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1
   ```

3. Restart launchd if runtime service behavior changed:

   ```sh
   launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
   launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
   ```

4. Send one controlled test through the UI or a reviewed attempt:

   ```sh
   CEO_NOT_SEND_MESSAGE=0 CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1 \
     .venv/bin/ceo-agent send-attempt --attempt-id <reviewed-attempt-id>
   ```

5. Re-check `/attempts/{id}`, `/errors`, and recent DingTalk state.

## Completion Checklist

- Shared rules read from `~/.agents/AGENT.md`.
- Worktree inspected and unrelated changes preserved.
- Python environment installed and tests for touched behavior pass.
- `dws` exists, is authenticated, and passes `probe-dws`.
- Codex CLI exists and can be used by the worker.
- Optional Memory Connector configured without a separate memory `user_id`.
- `.env` contains deployment-specific values and remains uncommitted.
- Workspace and corpus directories exist outside committed source data.
- `build-corpus`, `collect-corpus`, and `build-work-profile` completed or have
  documented blockers.
- `profiles/work_profile.md` reviewed and contains no private raw evidence.
- Audit web UI loads on `127.0.0.1:8765`.
- Dry-run `run-once` has been reviewed in the UI.
- launchd is installed only after dry-run approval.
- No unresolved `failed` or `processing` backlog remains.
- Live send is disabled unless the user explicitly approved it.
