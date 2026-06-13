# dws Command Inventory

This document records the local `dws` CLI surface inspected for the CEO
auto-reply service. It is intentionally operational rather than tutorial-style:
use it to decide which commands are safe for the worker, which commands require
explicit human approval, and where to find exact parameter schemas.

This survey records the useful safety boundary and operational command families
for this repository. It is not an exhaustive schema snapshot. On the observed
CLI version, `dws schema -f json` returned only one product (`aitable-form`) with
no tools, so command coverage must be checked from the live CLI before changing
automation. Do not commit generated raw schema JSON; regenerate it locally only
when exact schema output is needed for review.

Observed `dws` version during this pass: `v1.0.37`.

## How To Check Current Commands

The most reliable current sources are live, non-mutating CLI probes:

- `dws --help` for the current top-level service list and utility commands.
- `dws <service> --help` and `dws <service> <command> --help` for command
  paths and accepted flags.
- `dws schema` or `dws schema -f json` only when it returns the relevant
  product/tool. Current output may be partial.
- live read-only probes, such as list/get/help commands, before wiring a command
  into automation.

CLI path construction:

```text
dws <service> <group> <cli_name>
```

Omit `<group>` when it is absent. Example:

```text
canonical_path: ding.send_ding_message
service: ding
group: message
cli_name: send
CLI: dws ding message send
```

For exact command schemas, prefer canonical paths from the generated JSON
snapshot only when the schema output includes the relevant product/tool.
Otherwise use command help and read-only probes:

```bash
dws chat message send --help
dws schema -f json > /tmp/dws-command-schema.snapshot.json
```

## Safety Rules

Do not execute mutating commands from automation unless the caller has already
decided the action and a send/approval gate has accepted it. Mutating commands
include:

- sending chat, DING, mail, todo, calendar, document, sheet, AI table, or report
  content
- approval agree/refuse/revoke/comment actions
- file upload, move, delete, rename, permission, or folder mutations
- group create/rename/dismiss/member/role mutations
- reaction, recall, forward, combine-forward, and card-send operations
- auth reset/logout and plugin install/remove/enable/disable operations

For CEO auto-reply, default to read-only commands plus explicit send gates:

- Read messages and context with `chat message list-*` commands.
- Read DingTalk docs through `doc read`.
- Resolve people/departments through `contact` commands.
- Use `chat message send`, `ding message send`, and recall commands only behind
  the service's explicit live-send controls.
- Use OA approval commands only for review support unless the human explicitly
  asks for the real approval action.

## Commands Actually Probed

The pass used only non-mutating probes:

| Command | Result | Notes |
| --- | --- | --- |
| `dws --help` | ok | Listed all discovered services and utility commands. |
| `dws schema -f json` | ok but partial | Returned `count: 1` with `aitable-form` and no tools, so it is not an exhaustive command catalog. |
| `dws version --format json` | ok | Confirmed CLI version/build metadata. |
| `dws doctor --json --timeout 5` | ok | Login/network/cache/version passed; CLI reported latest version `v1.0.37`. |
| `dws contact user get-self --format json` | ok | Verified read-only contact access; output was not copied into this doc. |
| `dws chat message list-unread-conversations --count 1 --format json` | ok | Verified read-only unread conversation access; output was not copied into this doc. |

Commands that can send, approve, edit, delete, revoke, or change configuration
were not executed. Check their current parameters with command help and schema
output when available; do not rely on this file as exhaustive coverage.

## Global Flags

These flags are available broadly across `dws` commands:

| Flag | Meaning |
| --- | --- |
| `--client-id` | Override DingTalk OAuth client ID. Do not hardcode in scripts. |
| `--client-secret` | Override DingTalk OAuth client secret. Do not hardcode in scripts. |
| `--debug` | Print debug logs. Avoid in user-facing logs because it may expose internals. |
| `--dry-run` | Preview when a command supports dry-run behavior. Do not assume every MCP tool is side-effect-free. |
| `--fields` | Select output fields. |
| `--format` / `-f` | Output format: `json`, `table`, `raw`, `pretty`, `ndjson`, `csv`. Prefer `json` in services. |
| `--jq` | Filter JSON output. Useful for script-safe extraction. |
| `--mock` | Use mock data where supported. |
| `--timeout` | HTTP timeout in seconds. |
| `--verbose` / `-v` | Verbose logs. |
| `--yes` / `-y` | Skip confirmation prompts. Only use behind a human-reviewed gate. |

## Current Service Discovery

Use `dws --help` for the current top-level service list. During this pass it
included `aisearch`, `aitable`, `attendance`, `calendar`, `chat`, `contact`,
`devdoc`, `ding`, `doc`, `doc-comment`, `drive`, `hrmregister`, `live`, `mail`,
`minutes`, `oa`, `pat`, `report`, `sheet`, `todo`, and `wiki`. This section is
only a discovery marker, not a command-count table.

## Utility Commands

These are utility commands from `dws --help`, not CEO auto-reply service
commands:

| Command | Purpose | Mutation Risk |
| --- | --- | --- |
| `dws api <METHOD> <PATH>` | Raw DingTalk OpenAPI call. | Depends on method/path; treat non-GET as mutating. |
| `dws auth login` | Login or refresh auth. | Local auth mutation. |
| `dws auth logout` | Clear auth. | Local auth mutation. |
| `dws auth reset` | Reset local auth. | Local auth mutation. |
| `dws auth status` | Inspect auth status. | Read-only. |
| `dws cache refresh` | Refresh local tool cache. | Local cache mutation. |
| `dws cache status` | Inspect cache. | Read-only. |
| `dws completion bash|zsh|fish` | Generate shell completions. | Read-only unless redirected to shell config. |
| `dws config list` | List config/env knobs. | Read-only; may expose local config values. |
| `dws doctor` | Diagnose auth/network/cache/version. | Read-only. |
| `dws plugin build/create/dev/disable/enable/install/remove` | Manage plugins. | Local/plugin mutation. |
| `dws plugin config/info/list/validate` | Inspect or configure plugins. | Mixed; config mutates, info/list/validate read. |
| `dws recovery plan/execute/finalize` | Error recovery workflow. | Mixed; finalize writes recovery state. |
| `dws schema [path]` | Inspect MCP product/tool schemas. | Read-only. |
| `dws skill search/get/install` | Search/download/install skills. | Search is read-only; get/install mutate local files. |
| `dws version` | Print version. | Read-only. |

## CEO Service Allowlist

Current practical allowlist for the CEO auto-reply worker:

| Purpose | Preferred command family |
| --- | --- |
| List unread conversations | `dws chat message list-unread-conversations` |
| Read group context | `dws chat message list --group ...` |
| Read direct-chat context | `dws chat message list-direct --user ...` or `--open-dingtalk-id ...` |
| Search messages/groups when manually debugging | `dws chat search`, `dws chat message search`, `dws chat search-common` |
| Read DingTalk online docs | `dws doc read --node ...` |
| Locate ordinary DingTalk files | `dws doc` / `dws drive` read/download commands only |
| Resolve current user | `dws contact user get-self` |
| Resolve users/departments | `dws contact user get`, `dws contact user search`, `dws contact dept search`, `dws contact dept list-members` |
| Send reviewed group reply | `dws chat message send` behind live-send gate |
| Send reviewed direct reply | `dws chat message send --user ...` or `--open-dingtalk-id ...` behind live-send gate |
| DING handoff notification | `dws ding message send` behind live-send gate |
| Recall bot/user message | `dws chat message recall-by-bot` or equivalent recall command only from audit UI/gate |
| Review OA approval | `dws oa` read/detail commands plus referenced attachments/documents |
| Execute OA approval | Not allowed in automation by default; human explicit request required |

## Regenerating The Snapshot

Run this after upgrading `dws`:

```bash
dws schema -f json > /tmp/dws-command-schema.snapshot.json
```

Then update this file if the version, service discovery output, safety-relevant
command families, or CEO allowlist changed. Do not commit the generated raw JSON
unless there is a specific review need for the full schema artifact.
