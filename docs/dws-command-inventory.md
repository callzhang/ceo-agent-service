# dws Command Inventory

Survey of the local `dws` CLI used by the CEO service, re-done on 2026-10-28
with read-only probes only (`--help`, `dws schema`, `version`, `doctor`).
It is a capability reference, not a runtime command allowlist and not a second review
gate: Agent review and the business result contract own execution decisions.

Observed: `dws` `v1.0.65+ceo-docfmt.6747c671` (MCP static endpoint mode; upstream
latest was `1.0.66`). `dws doctor`: auth, keychain, runtime context and network
pass; only the version check warns.

## Live Command Catalog

Always check the live CLI before changing automation; this file will age.

- `dws schema -f json` now returns the full product catalog: 32 products,
  1501 tools (`count`, `tool_count`, `catalog_hash`, one row per product with
  `id` and `tool_count`). It is product level only; leaf schemas come from
  `dws schema --cli-path "<path>" --compact -f json` (parameters, constraints,
  examples) or `dws schema <product>` to expand one product.
- Each leaf carries `effect` (`read|write|destructive`), `risk`
  (`low|medium|high`), `confirmation` (`not_required|user_required`) and
  `idempotency` (`idempotent|retryable|non_idempotent|unknown`). Use `effect` to
  classify a command as read-only or mutating instead of guessing from its name;
  treat `unknown` idempotency as not safe to retry.
- Schema product ids are not identical to the `dws --help` service list: schema
  also lists `aicard`, `audit`, `dev`, `event`, `html`, `markdown`, `mcp` and
  `dingtalk-tag`, and has no `conference` row.
- Command path is `dws <service> [group] <command>`. Chat leads with `+shortcut`
  commands (for example `dws chat +messages-send`); lower-level resources
  (`dws chat message ...`) remain available. Shortcuts are preferred by the CLI.
- Do not commit raw schema output. Regenerate it into a `mktemp -d` directory
  when exact output is needed and delete it afterwards.

## Top-Level Services (`dws --help`)

| Service | Purpose |
| --- | --- |
| `agoal` | Agoal goal management |
| `aisearch` | AI search: semantic people and cross-source search |
| `aitable` | AI tables |
| `attendance` | Clock-in, shifts, statistics |
| `calendar` | Events, meeting rooms, free/busy |
| `chat` (alias `im`) | Groups, messages, bots |
| `conference` | Video meetings: start, schedule, invite, in-meeting control |
| `contact` | Directory: users, departments, roles |
| `contract` | Smart contract management |
| `devapp` | Developer app shortcuts |
| `devdoc` | Open-platform doc search |
| `ding` | DING messages, send and recall |
| `doc` | Online documents |
| `drive` | DingTalk Drive files |
| `hrbrain` | Org brain: talent pool, employee profiles |
| `live` | Live streams list and info |
| `mail` | Mailbox |
| `minutes` | AI minutes / meeting records |
| `oa` | OA approvals |
| `pat` | Behaviour authorization |
| `report` | Reports (daily/weekly templates) |
| `sheet` | Spreadsheets |
| `todo` | Todo tasks |
| `whiteboard` | Whiteboards |
| `wiki` | Knowledge bases, nodes, members, permissions |

Utility commands: `aicard` (A2UI card validate/preview), `api` (raw OpenAPI
call), `audit` (operation audit log), `auth`, `completion`, `config`, `dev`,
`doctor`, `event` (Stream subscription), `help`, `html`, `markdown`, `mcp`
(published MCP services), `plugin`, `profile`, `schema`, `skill`, `upgrade`,
`version`.

Hidden group: `dws dingtalk-tag` is the digital-employee command group (product
`deap-dev`); it is not in `--help`. Not used by this service.

## Global Flags

`--profile` (one-off organisation/account, `corpId:userId` as listed by
`dws profile list`; the default account only changes with `profile switch/use`),
`--format/-f` (`json|table|raw|pretty|ndjson|csv`; use `json`), `--jq`,
`--fields`, `--timeout` (seconds, default 30), `--dry-run` (only where the leaf
supports it), `--yes/-y` (skips confirmation; only behind a reviewed gate),
`--debug`, `--verbose/-v`, `--mock`, `--client-id`, `--client-secret` (never
hardcode credentials).

## Removed Or Unsupported

- `dws cache` and `dws recovery` are marked deprecated / not supported in help:
  service discovery is gone (static endpoint catalog) and failure-snapshot
  recovery is offline. Do not point Skills or Agents at them; use `doctor`,
  `schema` and the business command instead.
- `hrmregister` and `doc-comment` are no longer top-level services.
- `dws upgrade` replaces the binary; it is a local mutation, never run by
  automation.

## Safety Boundary

Read-only probes: `--help`, `schema`, `version`, `doctor`, `auth status`, and
list/get/read commands whose schema says `effect: read`.

Mutating (not for probing or automated exploration): sending chat, DING, mail,
todo, calendar, doc, sheet, AI table or report content; OA agree/refuse/revoke/
comment/add-sign; file upload, move, delete, rename, permission changes; group
create/rename/dismiss/member/role changes; reaction, recall, forward, card
send; `auth login/logout/reset`; `plugin`, `skill install`, `config`, `profile
switch`, `upgrade`; `api` with a non-GET method. `--dry-run` is not proof of
safety: check the leaf's `effect`.

Reading messages must not mark them read. Sends happen only behind the
service's live-send controls (see `docs/dws-capabilities.md`); OA approval
actions are never part of exploratory probing.
Do not copy command output (user data, IDs, tokens) into this file.

## CEO Service Command Reference

Command families the service uses, as examples. They are reference, not an
exhaustive or enforced allowlist: the service does not classify an otherwise
valid Agent result by command name, MCP tool name, or whether a command appears
here.

| Purpose | Command family (v1.0.65) |
| --- | --- |
| List unread conversations | `dws chat message list-unread-conversations` |
| Read group context | `dws chat message list --group ... --direction newer\|older` |
| Search messages / groups | `dws chat +search-msg`, `dws chat +chat-search`, `dws chat search-common` |
| Read DingTalk online docs | `dws doc read --node ...` |
| Locate ordinary files | `dws doc` / `dws drive` read and download commands only |
| Resolve current user / users / departments | `dws contact user get-self`, `get`, `search`; `dws contact dept search`, `list-members --depts` |
| Send a reviewed reply | `dws chat +messages-send --as user (--group \| --user \| --open-dingtalk-id) ...` behind the live-send gate |
| DING handoff notification | `dws ding message send --robot-code ...` behind the live-send gate |
| Recall a bot message | `dws chat message recall-by-bot --robot-code ...` from the audit UI / gate only |
| Review an OA approval | `dws oa` read and detail commands plus referenced attachments and documents |
| Execute an OA approval | not allowed in automation by default; needs an explicit human request |
