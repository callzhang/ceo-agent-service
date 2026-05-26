# dws Command Inventory

This document records the local `dws` CLI surface inspected for the CEO
auto-reply service. It is intentionally operational rather than tutorial-style:
use it to decide which commands are safe for the worker, which commands require
explicit human approval, and where to find exact parameter schemas.

Generated schema snapshot:

- `docs/dws-command-schema.snapshot.json`
- Captured from `dws schema -f json`
- Covers 20 services and 338 MCP-backed commands
- `dws` version observed during this pass: `v1.0.26`

Exhaustive expanded reference:

- `docs/dws-exhaustive-command-reference.md`
- Expands all 338 schema-backed commands into CLI path, canonical path,
  group/subcommand, required top-level parameters, nested subparameters, CLI
  flag overlay, and a heuristic mutation-risk label
- Includes utility commands discovered from `dws --help` / command help that are
  not present in the product schema snapshot
- Enumerates command/subcommand/parameter paths from the captured schema; it
  intentionally does not enumerate concrete tenant-specific values or execute
  mutating parameter combinations

## How To Read The Snapshot

Each tool entry contains:

- `canonical_path`: stable MCP tool path, for example `ding.send_ding_message`
- `group` and `cli_name`: how the command appears in CLI form
- `parameters`: every accepted input parameter and its type/description
- `required`: required parameters
- `flag_overlay`: CLI flag aliases, transforms, and env defaults

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

For an exact command schema:

```bash
dws schema ding.send_ding_message
dws schema --cli-path "ding message send"
dws schema --jq '.tool.flag_overlay' --cli-path "ding message send"
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
| `dws schema -f json` | ok | Generated the complete snapshot. |
| `dws version --format json` | ok | Confirmed CLI version/build metadata. |
| `dws doctor --json --timeout 5` | ok with warning | Login/network/cache passed; CLI reported that a newer version exists. |
| `dws contact user get-self --format json` | ok | Verified read-only contact access; output was not copied into this doc. |
| `dws chat message list-unread-conversations --count 1 --format json` | ok | Verified read-only unread conversation access; output was not copied into this doc. |

Commands that can send, approve, edit, delete, revoke, or change configuration
were not executed. Their schemas were verified through `dws schema`.

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

## MCP Service Coverage

| Service | Commands | Scope |
| --- | ---: | --- |
| `ding` | 3 | DING send and recall. |
| `aiapp` | 3 | AI app create/query/modify. |
| `aisearch` | 5 | Enterprise people, knowledge, behavior, group, and help-center search. |
| `contact` | 10 | Current user, user lookup, department lookup, and department members. |
| `doc-comment` | 4 | Document comments and inline comments. |
| `chat` | 41 | IM extensions merged into the `chat` command tree. |
| `live` | 1 | Live stream list/info. |
| `mail` | 22 | Mail read/write/send/reply/forward operations. |
| `sheet` | 42 | DingTalk sheet operations. |
| `group` | 12 | Bot group/member operations. |
| `drive` | 7 | Drive file and folder operations. |
| `aitable` | 48 | AI table bases, tables, fields, records, views, dashboards, charts, import/export, attachments, templates. |
| `minutes` | 27 | AI meeting minutes list/detail/summary/todo/transcript/recording/mindmap/speaker/hotword/upload. |
| `oa` | 18 | Approval list/detail/agree/refuse/revoke/comment style operations. |
| `todo` | 17 | Todo create/query/update/finish/delete style operations. |
| `doc` | 26 | DingTalk doc search/browse/read/write/upload/download/file/folder/block/comment operations. |
| `calendar` | 17 | Calendar events, meetings, rooms, and availability. |
| `report` | 7 | Work report templates, create/query/statistics. |
| `chat-f80f4305` | 24 | Main chat/conversation/group/message commands. |
| `attendance` | 4 | Attendance shifts, records, summary, rules. |

## Utility Commands

These are not MCP service tools, so they are not in the JSON schema snapshot:

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
| Send reviewed direct reply | `dws chat message send-direct` behind live-send gate |
| DING handoff notification | `dws ding message send` behind live-send gate |
| Recall bot/user message | `dws chat message recall-by-bot` or equivalent recall command only from audit UI/gate |
| Review OA approval | `dws oa` read/detail commands plus referenced attachments/documents |
| Execute OA approval | Not allowed in automation by default; human explicit request required |

## Regenerating The Snapshot

Run this after upgrading `dws`:

```bash
dws schema -f json > docs/dws-command-schema.snapshot.json
```

Then update this file's version, command count, and service table if they
changed.
