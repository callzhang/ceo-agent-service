# dws Exhaustive Command Reference

This render-safe index points to per-service files under `docs/dws-command-reference/`. The previous single-file expansion was too large for some Markdown renderers.

Coverage rule: the per-service files exhaustively enumerate every command path, subcommand path, schema parameter, nested schema parameter, and CLI flag overlay present in `docs/dws-command-schema.snapshot.json`. They do not enumerate concrete tenant-specific values or execute mutating parameter combinations.

Mutation note: mutating command combinations were not executed against DingTalk. They are enumerated from schema/help only.

## Snapshot

- Products in schema snapshot: 20
- MCP-backed commands in schema snapshot: 338
- CLI version observed: `v1.0.26`
- Schema source: `dws schema -f json`
- Top-level help services not present as products in the schema snapshot: `devdoc`, `pat`, `wiki`.
- Schema-only product IDs not listed as top-level help services: `group`, `chat-f80f4305`.

## Per-Service Files

| Service | Commands | Groups | File |
| --- | ---: | --- | --- |
| `ding` | 3 | -, message | [ding.md](dws-command-reference/ding.md) |
| `aiapp` | 3 | - | [aiapp.md](dws-command-reference/aiapp.md) |
| `aisearch` | 5 | - | [aisearch.md](dws-command-reference/aisearch.md) |
| `contact` | 10 | -, dept, user | [contact.md](dws-command-reference/contact.md) |
| `doc-comment` | 4 | - | [doc-comment.md](dws-command-reference/doc-comment.md) |
| `chat` | 41 | -, group, group.member-role, message | [chat.md](dws-command-reference/chat.md) |
| `live` | 1 | stream | [live.md](dws-command-reference/live.md) |
| `mail` | 22 | -, mailbox, message | [mail.md](dws-command-reference/mail.md) |
| `sheet` | 42 | -, filter-view, range | [sheet.md](dws-command-reference/sheet.md) |
| `group` | 12 | -, members | [group.md](dws-command-reference/group.md) |
| `drive` | 7 | - | [drive.md](dws-command-reference/drive.md) |
| `aitable` | 48 | -, attachment, base, chart, chart.share, dashboard, dashboard.share, export, field, import, record, table, template, view | [aitable.md](dws-command-reference/aitable.md) |
| `minutes` | 27 | -, get, hot-word, list, mind-graph, speaker, update, upload | [minutes.md](dws-command-reference/minutes.md) |
| `oa` | 18 | -, approval | [oa.md](dws-command-reference/oa.md) |
| `todo` | 17 | -, task | [todo.md](dws-command-reference/todo.md) |
| `doc` | 26 | -, block, file, folder | [doc.md](dws-command-reference/doc.md) |
| `calendar` | 17 | -, busy, event, participant, room | [calendar.md](dws-command-reference/calendar.md) |
| `report` | 7 | -, template | [report.md](dws-command-reference/report.md) |
| `chat-f80f4305` | 24 | -, group, group.members, message | [chat-f80f4305.md](dws-command-reference/chat-f80f4305.md) |
| `attendance` | 4 | -, record, shift | [attendance.md](dws-command-reference/attendance.md) |

## Utility Commands Not In Schema Snapshot

| CLI | Purpose | Command-specific parameters/flags |
| --- | --- | --- |
| `dws api <METHOD> <PATH>` | Raw DingTalk OpenAPI call | --base-url, --data, --page-all, --page-delay, --page-limit, --params |
| `dws auth login/logout/reset/status` | Authentication management | global flags |
| `dws cache refresh/status` | Cache management | global flags |
| `dws completion bash/zsh/fish` | Shell completion generation | -h |
| `dws config list` | Configuration listing | global flags |
| `dws doctor` | Health check | --json, --perf, --timeout |
| `dws help [COMMAND]` | Help for a command path | command path |
| `dws plugin build/config/create/dev/disable/enable/info/install/list/remove/validate` | Plugin management | see subcommand --help |
| `dws recovery plan/execute/finalize` | Error recovery workflow | global flags |
| `dws schema [path]` | Schema inspection | --cli-path |
| `dws skill search/get/install` | Skill marketplace | global flags |
| `dws version` | Print version | -h |

## Global Flags

| Flag | Meaning |
| --- | --- |
| `--client-id` | Override OAuth client ID. |
| `--client-secret` | Override OAuth client secret. |
| `--debug` | Show debug logs. |
| `--dry-run` | Preview operation when supported. |
| `--fields` | Filter output fields. |
| `--format, -f` | Output format: json/table/raw/pretty/ndjson/csv. |
| `--jq` | Filter JSON output. |
| `--mock` | Use mock data when supported. |
| `--timeout` | HTTP timeout seconds. |
| `--verbose, -v` | Verbose logs. |
| `--yes, -y` | Skip confirmation prompts. |

