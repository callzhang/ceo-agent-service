# dws Capabilities

This project uses the `dws` CLI as the only DingTalk integration surface.

## Read

- `dws chat message list-unread-conversations --format json`
- `dws chat message list --group <openConversationId> --time <time> --forward <true|false> --limit <n> --format json`
- `dws doc read --node <alidocs-url> --format json`
- `dws contact user get-self --format json`
- `dws contact user get --ids <ids> --format json`
- `dws contact dept search --query <name> --format json`
- `dws contact dept list-members --ids <ids> --format json`

## Write

- `dws chat message send --group <openConversationId> --text <text> --format json --yes`
- `dws ding message send --users <userId> --type app --content <text> --format json`
- `dws chat message recall-by-bot --keys <processQueryKey> --format json --yes`

## Operational Notes

- Always request JSON output.
- Do not persist auth tokens, webhooks, robot codes, or authorization URLs.
- Read commands must not mark DingTalk messages read.
- Live send commands are guarded by `CEO_NOT_SEND_MESSAGE=0` and
  `CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1`.
- Message time anchors are generated in the agent machine's local timezone and
  included in `run-once` output as `agent_local_timezone`.
