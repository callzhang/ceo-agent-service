# dws Capabilities

This project uses the `dws` CLI as the only DingTalk integration surface.

## Read

- `dws chat message list-unread-conversations --format json`
- `dws chat message list --group <openConversationId> --time <time> --direction <newer|older> --limit <n> --format json`
- `dws doc read --node <alidocs-url> --format json`
- `dws contact user get-self --format json`
- `dws contact user get --ids <ids> --format json`
- `dws contact dept search --query <name> --format json`
- `dws contact dept list-members --ids <ids> --format json`

## Write

- `dws chat +messages-send --as user (--group <openConversationId> | --user <userId> | --open-dingtalk-id <id>) --title <title> --markdown <text> --format json --yes`
- `dws chat message send-by-bot --robot-code <code> ...`
- `dws ding message send --robot-code <code> --users <userId> --type app --content <text> --format json`
- `dws chat message recall-by-bot --robot-code <code> [--group <openConversationId>] --keys <processQueryKey> --format json --yes`

## Operational Notes

- Always request JSON output.
- Do not persist auth tokens, webhooks, robot codes, or authorization URLs.
- Read commands must not mark DingTalk messages read.
- Unread-message reads use a minimum five-message overlap window, even when
  the unread counter is one, because multiple messages can share a time anchor.
  Only the unread suffix is returned to the worker; overlap rows remain context.
- Live send commands are guarded by `CEO_NOT_SEND_MESSAGE=0` and
  `CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1`.
- Message time anchors are generated in the agent machine's local timezone and
  included in `run-once` output as `agent_local_timezone`.
