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
| Database path | `~/Library/Application Support/ceo-agent-service/auto-reply.sqlite3` | Local SQLite runtime state, kept outside iCloud-managed Documents. |
| Corpus path | `./data/corpus` | Ignored by Git; contains style corpus. |
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
   "$HOME/miniforge3/bin/python" -m pip install -e '.[dev]'
   ```

5. Install and verify the Agent Workbench dependencies and production build:

   ```sh
   npm install --prefix frontend
   npm run test:workbench
   npm run build:workbench
   ```

   The build must create `app/static/workbench/index.html` plus every referenced
   file under `/workbench-assets/`. Generated files remain untracked. Do not let
   the installer download dependencies or build implicitly.

If any dependency download is blocked by network, package registry, or missing
credentials, report the exact failed command and error.

## Phase 2: Download And Verify Components

Start with the local component bootstrapper. It installs the components whose
source is known on the machine and verifies the rest:

```sh
scripts/bootstrap-local-components.sh --format json
```

The bootstrapper automatically installs `terminal-notifier` through Homebrew
when available and verifies Codex CLI and the Nvwa skill. DWS and Lark are
separate Tutorial steps because each CLI owns its installation and interactive
authorization lifecycle. If an internal component is missing, provide the
approved source through one of these environment variables and click its
Tutorial setup action:

- `DWS_INSTALLER_PATH`: executable installer for `dws`
- `DWS_INSTALL_COMMAND`: approved shell command for installing `dws`
- `LARK_CLI_INSTALL_COMMAND`: approved override for installing `lark-cli`
- `CODEX_INSTALL_COMMAND`: approved shell command for installing Codex CLI
- `NVWA_SKILL_SOURCE`: approved local directory containing the Nvwa skill

Do not ask the user to copy individual terminal commands when the bootstrapper
can perform the step. Only interrupt the user for the approved source, login
approval, QR/browser authorization, macOS permission clicks, or live-send
decisions.

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

4. If `dws` is missing, set `DWS_INSTALLER_PATH` or `DWS_INSTALL_COMMAND` to
   the organization's approved installer or package command, then click
   `Tutorial -> DingTalk CLI -> Install or configure`. Do not invent a download
   URL.

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

2. If Codex is not installed, set `CODEX_INSTALL_COMMAND` to the user's approved
   Codex installation command and run the bootstrapper. If Codex is not
   authenticated, complete the normal user login once outside the service. Do not
   store API keys in this repository; Consumer A and Audit B never run login,
   reset, or logout commands themselves.

3. Confirm continuity support later through a dry-run worker pass; the service
   uses Codex sessions through the local runtime, not a cloud-only worker.

### Optional Codex API failover rollout

The production route order is `codex_oauth,codex_api`: the existing local OAuth
identity remains primary and the API credential is a bounded fallback. Configure
the fallback only in the service environment:

```sh
CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api
CEO_CODEX_API_KEY=<service-owned-secret>
```

Never put the API key in argv, a prompt, a repository file, SQLite, History, a
diagnostic attachment, or a shell transcript. The runtime adapter injects it
only as `OPENAI_API_KEY` in the `codex_api` child environment and removes the
service variable; the OAuth child receives neither variable. Do not print the
environment while diagnosing this route. Do not enter or `export` the secret in
an interactive shell whose command history or transcript is retained; provision
it directly in the service-owned environment using the approved secret channel.

Route probes are synthetic, schema-constrained, read-only turns with all tools,
web access, plugins, apps, memories, browser features, and dynamic tool search
disabled. Run the operator probe from the same service account and environment:

```sh
.venv/bin/ceo-agent probe-agent-runtimes \
  --db "$CEO_WORKER_DB" --workspace "$CEO_WORKSPACE"
```

Read each route independently. A failed OAuth probe pauses only `codex_oauth`;
a failed API probe pauses only `codex_api`. A healthy probe closes only its own
pause. Authentication, capacity, transport, capability, session-evidence,
result-validation, process, and effect-policy failures are stored as typed,
credential-safe codes. Do not copy raw provider stderr into a ticket or History.

Roll out in order:

1. **Stage 1 — probe only.** Configure `codex_api` but leave business fallback
   disabled. Run `probe-agent-runtimes`; require both routes to report
   `healthy=true`, a current expiry, and only capabilities the synthetic probe
   actually proves. Search command output, service logs, SQLite, and rendered
   History for the configured secret without printing it. Any match blocks
   rollout.
2. **Stage 2 — read-only fallback.** Enable only synthetic and reviewed
   read-only workloads. With `CEO_LIVE_RUNTIME_FAILOVER_E2E=1`, run
   `tests/e2e/test_runtime_failover_live.py` from an isolated test database. It
   verifies a real OAuth probe, an API probe with secret scans, and an injected
   OAuth authentication failure followed by one successful API attempt under
   the same `agent_run` and execution generation. Verify no business channel
   tool was available and no failed, processing, or unknown backlog was added.
3. **Stage 3 — Audit fallback.** Proceed only after retaining Stage 2 command,
   attempt, History, and secret-scan evidence. Use a dedicated reversible test
   target. Interrupt once before the first effect and require one API write with
   exact readback; interrupt once after effect start and require no provider
   switch or second write, only read-only reconciliation. Retain the receipt and
   external readback in release evidence.

Failover is allowed only when the persisted typed failure explicitly permits it,
the selected route has a current matching capability snapshot, no effect has
started, and the complete session range proves no write. Missing or ambiguous
session evidence, an unreviewed dynamic item, a started effect, or an unknown
write is terminal for provider selection. Unknown writes must be reconciled
against their original operation identity; they must never be retried by
switching credentials.

Rollback is intentionally small: remove `codex_api` from
`CEO_AGENT_RUNTIME_ROUTES`, reload the service environment, and restart and
verify the service using the normal deployment procedure. Do not erase route
pauses, attempt rows, receipts, or History: they are the evidence needed to
distinguish a safe read-only retry from an unknown write. Rollback does not
authorize a replay.

### Optional Friday Runtime fallback

Friday Runtime can be placed after the Codex routes when provider-specific APIs
(for example MiniMax Chat Completions) are configured inside Friday. CEO Agent
only sends a prompt to the existing Friday project; it does not receive or
persist the provider token. Configure the route with a Friday Runtime ticket or
session token:

```sh
CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api,friday_runtime
CEO_FRIDAY_RUNTIME_BASE_URL=http://127.0.0.1:8080
CEO_FRIDAY_RUNTIME_PROJECT_ID=<existing-friday-project-id>
CEO_FRIDAY_RUNTIME_TICKET=<runtime-ticket>
```

The Friday project owns provider and model selection (for example,
`MiniMax-M3`); CEO Agent does not override it.

For a local runtime that intentionally has authentication disabled, set
`CEO_FRIDAY_RUNTIME_AUTH_DISABLED=1` and omit both credential variables. The
adapter creates one Friday thread, submits one turn, waits for its operation,
and reads the final Artifact. A route change preserves the same CEO Agent run
and creates no business-side effect by itself.

Run the default, no-network contract test with:

```sh
.venv/bin/pytest -q tests/e2e/test_friday_runtime_fallback.py
```

The test uses a temporary HTTP server and SQLite database. It must pass before
enabling a local endpoint. To exercise a real local Friday Runtime, explicitly
opt in and provide its endpoint and project:

```sh
CEO_LIVE_FRIDAY_RUNTIME_E2E=1 \
FRIDAY_RUNTIME_BASE_URL=http://127.0.0.1:8080 \
CEO_FRIDAY_RUNTIME_PROJECT_ID=<existing-friday-project-id> \
.venv/bin/pytest -q tests/e2e/test_friday_runtime_fallback.py
```

The live test sends only the synthetic prompt `{"ok":true}` and expects a
schema-valid JSON response. Typed failures are reported as
`friday_runtime_unreachable`, `friday_runtime_auth_failed`,
`friday_runtime_result_invalid`, or `friday_runtime_failed`; inspect the
specific code before changing route order.

### Runtime Roles And Audit Rules

Consumer Agent A represents the installation owner: it reads evidence, reuses one
Codex session per business conversation, and prepares exact candidates. Audit
Agent B independently checks each candidate, starts a fresh session per revision,
then performs and verifies accepted external writes. A has no task-driven write
tools; B is the only task-driven writer.

Review the shared rules at **Config -> Audit Rules** before enabling live sends.
They apply to both roles and can refine business review requirements, but cannot
change the read/write boundary, exact-revision dedupe, unknown-result readback, or
two-cycle content-feedback limit. A missing fact that the sender can answer
becomes one concrete clarification message candidate; it is not an operator choice.

### macOS Notifications

The service prefers `terminal-notifier` for native macOS notifications and falls
back to browser notifications or `osascript` when unavailable. The bootstrapper
installs `terminal-notifier` automatically with Homebrew when possible:

```sh
scripts/bootstrap-local-components.sh --format json
```

### Memory Connector

If the deployment uses Friday Memory or another Memory Connector MCP endpoint,
configure and authenticate it once in the installing user's Codex profile. The
service reuses that native MCP configuration and does not copy endpoint headers,
commands, or tokens into `.env` or a service manifest. The Tutorial page should
guide the user to the native Codex setup when a tool is unavailable.

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
   CEO_WORKER_DB=$HOME/Library/Application Support/ceo-agent-service/auto-reply.sqlite3
   CEO_CORPUS_DIR=./data/corpus
   CEO_CODEX_MODEL=gpt-5.5
   CEO_CODEX_MODEL_REASONING_EFFORT=medium
   CEO_CODEX_MODEL_PROVIDER=
   CEO_DRY_RUN=1
   CEO_PRINCIPAL_NAME=<principal display name>
   USER_ALIAS=<principal display name>
   CEO_MENTION_ALIASES=<comma-separated DingTalk @ aliases>
   DOCUMENT_EXTRACTION_IDS=<names used in docs and prompts>
   CEO_ASSISTANT_SIGNATURE=<signature>
   CEO_HANDOFF_ACK=<handoff acknowledgement>
   CEO_LIVE_SEND_BLOCKERS_ACCEPTED=
   ```

   也可以在审计页 `Settings → Config → System Config` 修改模型与 thinking
   强度。保存会写入 `.env`；重启主服务后，所有新的 agent runtime 路由都会使用新设置。

3. Keep dry-run on for first validation. For this codebase, dry-run can be set
   as either `CEO_DRY_RUN=1` or `CEO_NOT_SEND_MESSAGE=1`; launchd defaults to
   live processing, so review launchd behavior before installing the service.

4. Verify important paths exist:

   ```sh
   mkdir -p data/corpus "$HOME/Documents/memory"
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
   "$HOME/miniforge3/bin/ceo-agent" build-corpus \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./data/corpus
   ```

4. Append recent DingTalk sent-message samples:

   ```sh
   "$HOME/miniforge3/bin/ceo-agent" collect-corpus \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./data/corpus
   ```

This reads through the current `dws` identity. If the command fails on auth or
permission, fix `dws` before continuing.

## Phase 5: Generate And Review The Work Profile

1. Build the initial profile and evidence index:

   ```sh
   "$HOME/miniforge3/bin/ceo-agent" build-work-profile \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./data/corpus
   ```

2. If the user provided a DingTalk KB workspace id or URL, include it:

   ```sh
   "$HOME/miniforge3/bin/ceo-agent" build-work-profile \
     --workspace "$HOME/Documents/memory" \
     --corpus-dir ./data/corpus \
     --dingtalk-kb-workspace '<workspace-id-or-url>'
   ```

3. Expected outputs:

   ```text
   data/work-profile/work_profile.md
   data/profile-evidence/evidence_index.jsonl
   data/corpus/style_corpus.csv
   ```

4. Run a Nvwa review pass over:

   ```text
   data/work-profile/work_profile.md
   data/profile-evidence/evidence_index.jsonl
   data/corpus/style_corpus.csv
   ```

5. The Nvwa pass must rewrite only `data/work-profile/work_profile.md`. It must not add
   raw private excerpts, absolute local paths, tokens, session ids, or DingTalk
   cache content.

6. Verify runtime consumption:

   ```sh
   "$HOME/miniforge3/bin/pytest" \
     tests/test_work_profile.py \
     tests/test_prompt.py \
     tests/test_worker.py::test_consumer_codex_command_embeds_work_profile_content \
     -q
   ```

Runtime reads the profile through `app.prompt:work_profile_instruction()`.

## Phase 6: Validate dws Permissions

Run read probes first:

```sh
"$HOME/miniforge3/bin/ceo-agent" probe-dws
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

1. Re-run the frontend checks and build after any Workbench source change:

   ```sh
   npm run test:workbench
   npm run build:workbench
   ```

2. Start the audit web UI:

   ```sh
   "$HOME/miniforge3/bin/python" -m app.cli audit-web \
     --reload \
     --host 127.0.0.1 \
     --port 8765
   ```

3. Open and inspect:

   ```text
   http://127.0.0.1:8765/
   ```

4. Key pages:

   - `/`: Agent Workbench conversations, streaming progress, artifacts, and confirmations.
   - `/history`: reply and execution history.
   - `/attempts/{id}`: single attempt, prompt, decision, evidence, send status.
   - `/tasks`: project/TODO summary and follow-up drafts.
   - `/workers`: service worker status.
   - `/settings`: configuration and logs.

   Workbench state is authoritative in SQLite. Server-sent events stream
   replayable progress but do not replace persisted state. Tool calls that need
   approval stop at a persisted confirmation and resume only after that exact
   confirmation is accepted. Codex is the first production runtime; Claude and
   Pi do not have dedicated adapters yet and may be added only by implementing
   the same event, stop, recovery, and confirmation contract.

5. Run one dry-run pass:

   ```sh
   CEO_NOT_SEND_MESSAGE=1 "$HOME/miniforge3/bin/ceo-agent" run-once --not-send-message
   ```

6. Review the web UI for:

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

   The installer exits before any plist or service mutation when the Workbench
   build or an asset referenced by its index is missing. Run
   `npm install --prefix frontend && npm run build:workbench` explicitly; the
   installer never runs those commands for you.

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
     "$HOME/miniforge3/bin/ceo-agent" send-attempt --attempt-id <reviewed-attempt-id>
   ```

5. Re-check `/attempts/{id}`, `/errors`, and recent DingTalk state.

## Completion Checklist

- Shared rules read from `~/.agents/AGENT.md`.
- Worktree inspected and unrelated changes preserved.
- Python environment installed and tests for touched behavior pass.
- `npm run test:workbench` and `npm run build:workbench` pass, with all index
  asset references present.
- `dws` exists, is authenticated, and passes `probe-dws`.
- Codex CLI exists and can be used by the worker.
- Optional Memory Connector configured without a separate memory `user_id`.
- `.env` contains deployment-specific values and remains uncommitted.
- Workspace and corpus directories exist outside committed source data.
- `build-corpus`, `collect-corpus`, and `build-work-profile` completed or have
  documented blockers.
- `data/work-profile/work_profile.md` reviewed and contains no private raw evidence.
- Audit web UI loads on `127.0.0.1:8765`.
- Dry-run `run-once` has been reviewed in the UI.
- launchd is installed only after dry-run approval.
- No unresolved `failed` or `processing` backlog remains.
- Live send is disabled unless the user explicitly approved it.
