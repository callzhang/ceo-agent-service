# WeChat Personal-Account Channel — Operations

Status: read-first pipeline implemented and tested; live automatic sending stays
disabled by default. Both feasibility gates are proven (see
[research doc](wechat-channel-and-local-memory-research.md) and the plan's Task 4/5/10
correction blocks): local-DB read (1.72M messages decrypted, 0 HMAC failures) and
Accessibility send (verified live to 文件传输助手).

## What is implemented (`app/wechat/`)

| Module | Role |
| --- | --- |
| `models.py` | Immutable channel contracts + trigger validation |
| `discovery.py` / `snapshot.py` | Account/DB discovery; read-only snapshots |
| `key_provider.py` | Passphrase provider boundary (blocked / persisted file) |
| `cipher.py` / `schema.py` / `backend.py` | SQLCipher-4 decrypt (passphrase→PBKDF2), zstd, `Msg_<md5>`/`Name2Id`/`contact.db` parsing, decrypted mirror |
| `reader.py` | Capability-gated normalized reads |
| `producer.py` | Eligible-message → channel-isolated reply task (exact group @-gate) |
| `prompt.py` / `consumer.py` | WeChat-specific prompt + Codex decision → fail-closed delivery |
| `accessibility.py` | Exact-once delivery state machine + real AX runner |
| `memory.py` | Bounded extraction + deterministic cleanup + approved-only writer |
| `setup.py` / `audit_web.py` | Tutorial connect service + target picker routes |
| `service.py` / `cli.py` | Composable steps/loops + diagnostic CLI |
| `scripts/wechat_key_probe.py` | Fingerprint-only key/schema gate |

Store isolation: `reply_tasks`/`reply_attempts`/`sent_replies` gain a `channel`
column (default `dingtalk`); claims are channel-filtered so the DingTalk worker
never claims WeChat work. New tables: `wechat_read_state`,
`wechat_reply_scopes`, `wechat_deliveries`, `wechat_memory_candidates`.

## One-time key capture (SIP stays on, real app untouched)

See `~/wx_read_toolkit/README.md`. Summary: shadow-copy WeChat → re-sign the copy
(`get-task-allow`, strip Hardened Runtime) → `capture_driver.py` breakpoints
`CCKeyDerivationPBKDF` → log out/in → the 32-byte passphrase is validated against
`message_0.db` and saved to `~/.config/wx_read/passphrase.hex` (chmod 600). The
passphrase is account-stable; re-capture only after logout/reinstall.

## Diagnostic CLI

```bash
.venv/bin/python -m app.wechat.cli status                     # probe capability per account
.venv/bin/python -m app.wechat.cli read-recent --target-id filehelper --limit 100   # redacted metadata
.venv/bin/python -m app.wechat.cli read-recent --target-id filehelper --include-text  # explicit local verify
.venv/bin/python -m app.wechat.cli produce-once               # scan enabled scopes → enqueue
.venv/bin/python scripts/wechat_key_probe.py --passphrase-file ~/.config/wx_read/passphrase.hex \
    --account-db-dir "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<acct>/db_storage"
```

## Remaining thin integration (into existing shared files)

These small wirings were intentionally left as reviewed edits to large shared
files (the channel logic + tests are complete and isolated):

1. `app/setup_wizard.py` — register a `wechat_connection` step (depends only on
   `service_config`; actions `check`/`connect`/`verify`) mapping
   `WechatSetupResult` to `SetupWizardEvent`; extend `SetupWizardEvent` with
   `next_step_status` so a successful action can leave the step `blocked`.
2. `app/audit_web.py` — `register_wechat_tutorial_routes(app, setup_factory=...)`
   and the Memory review routes/render helper.
3. `app/cli.py` — add `wechat-status` / `wechat-read-recent` / `wechat-produce-once`
   / `wechat-consume-once` (delegate to `app.wechat.cli`), and start the WeChat
   producer/consumer threads in `run_service()` only when
   `config.wechat_reader_enabled()` and the persisted capability is `ready`
   (`service.wechat_loop_names`). Sender flag checked per delivery.

## Controlled verification (before any live send)

1. `.venv/bin/python -m pytest -q` — full suite green.
2. `wechat status` → the account reports `ready`; `read-recent --include-text` on
   File Transfer Helper — compare count/order/direction/timestamp/sender/text;
   run twice, confirm `produce-once` enqueues zero duplicates on the second scan.
3. Decision dry-run with `CEO_WECHAT_SENDER_ENABLED=0`: one direct message → one
   task + audited decision; an ordinary group message → no task; a real
   `@current account` group message → one task; no external send.
4. Sender only after File Transfer Helper binding is `verified`: one fixed test
   reply, confirm visible receipt + matching outbound DB record, restart before a
   second send and confirm no duplicate; force a post-action ambiguity and verify
   `send_unknown` pauses the target with no auto-retry.
5. Bounded Memory import on an approved test scope → review page shows cleaned
   pending rows only; approve one, write once, write again → same Memory id, one
   tool call; reject another → cannot be written.

## Disable / rollback

- `CEO_WECHAT_READER_ENABLED=0`, `CEO_WECHAT_SENDER_ENABLED=0` (defaults) — loops
  never start.
- Purge the decrypted mirror (`CEO_WECHAT_MIRROR_DIR`, default
  `~/.cache/wx_read/plain`) and the passphrase file to remove all plaintext.
- The real `/Applications/WeChat.app` is never modified by this channel.

## Known residual risks / TODO

- **Sender**: needs Accessibility permission (cached at process launch); duplicate
  display names require corroboration beyond the name; Return-send fails if the
  user set "Enter=newline" (fall back to ⌘Return); sending steals focus ~1s.
- **Exact `@self` group detection**: v4 stores mentions in `packed_info_data`;
  the reader currently returns no mentions from real data (producer mention-gate
  is proven via tests, real extraction is a TODO) — keep group triggers gated
  until `packed_info_data` mention parsing lands.
- **Direction**: real outbound/inbound needs the self-wxid; defaults inbound
  until `self_username` is populated.
