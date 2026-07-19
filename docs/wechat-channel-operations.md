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

## Shared-file integration status

1. ✅ **`app/setup_wizard.py`** (done 2026-07-18) — `wechat_connection` step registered
   (Phase 3, gates only on `service_config`, independent of `data_corpus`; actions
   `check`/`connect`/`verify`). `run_setup_action`/`check_setup_step` dispatch to
   `WechatSetupService` via `service.build_setup_service`. `SetupWizardEvent` gained
   `next_step_status` so a successful action leaves the step `blocked` when the
   reader is blocked.
2. ✅ **`app/audit_web.py`** (done 2026-07-18) — `register_wechat_tutorial_routes`
   mounted in `create_audit_app` (picker `GET /tutorial/wechat/conversations`,
   `POST /tutorial/wechat/reply-scope`); `tutorial_run` honors `next_step_status`.
3. ✅ **`app/cli.py`** (done 2026-07-18):
   - `ceo-agent wechat <status|read-recent|produce-once|consume-once>` passes
     through (argparse REMAINDER) to `app.wechat.cli`. `wechat status`
     auto-detects+persists `self_user_id` and reports `ready`.
   - `run_service()` starts `wechat-producer`/`wechat-consumer` threads only when
     `CEO_WECHAT_READER_ENABLED` **and** a single account is persisted `ready`
     (`_wechat_service_components`); disabled by default (no effect on the DingTalk
     service). Auto-send stays gated — the loops enqueue tasks and produce
     `ready_to_send` deliveries but do not send.

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

## Send confirmation & recall (2026-07-18)

- **Confirm vs auto** (`CEO_WECHAT_SEND_MODE`, default `confirm`): the consumer
  always produces a `ready_to_send` delivery. In **confirm** mode the sender loop
  sends **nothing** — deliveries wait for explicit approval
  (`ceo-agent wechat pending` / `approve --id N` / `reject --id N`, or
  `service.approve_wechat_delivery`/`reject_wechat_delivery`). In **auto** mode the
  `wechat-sender` loop sends them (only when `CEO_WECHAT_SENDER_ENABLED=1`). This is
  the primary guard against a wrong/awkward send.
- **Wrong-target detection is layered, not instant.** Immediate check = the AX
  binding (`chat_input_field.AXTitle == target`, twice); duplicates it cannot tell
  apart, so those rely on `binding_status=="verified"` (fail-closed). A **DB check
  by `conversation_id`** must be **delayed** (reconcile): the just-sent message
  sits in WeChat's WAL and is not in the decrypted mirror for seconds–minutes, so
  an immediate DB verify false-negatives and must **not** drive an auto-recall.
- **Recall (撤回)** is a **best-effort, unvalidated backstop**
  (`runner.recall_last_outbound(text)` → right-click bubble → 撤回; `service.
  recall_wechat_delivery`). It only works inside WeChat's ~2-minute window with the
  chat open, and reliable auto-triggering is limited (immediate wrong detection is
  hard; the DB reconcile that would catch it is past the window). Treat it as a
  manual "oops" action, not a guaranteed net — **confirm mode is the real safety.**

## Disable / rollback

- `CEO_WECHAT_READER_ENABLED=0`, `CEO_WECHAT_SENDER_ENABLED=0` (defaults) — loops
  never start.
- Purge the decrypted mirror (`CEO_WECHAT_MIRROR_DIR`, default
  `~/.cache/wx_read/plain`) and the passphrase file to remove all plaintext.
- The real `/Applications/WeChat.app` is never modified by this channel.

## Known residual risks / TODO

- **Sender** (pure-AX, 2026-07-18): composes via `AXValue` set on `chat_input_field`
  and sends by posting Return to WeChat's pid (`CGEventPostToPid`) — **no focus
  steal, no synthetic typing into the frontmost app**. Selecting the target chat
  is the one step that needs WeChat briefly key: on 4.1.10 the session list rows
  are static text with empty AX action lists, background clicks don't register,
  and background keystrokes land text in the search box but WeChat won't *run* the
  search unless its window is active — so full-background nav is not possible.
  Mitigation: the sender **waits until the user has been idle** (`idle_seconds`,
  no keyboard/mouse via `CGEventSourceSecondsSinceLastEventType`) before the ~1s
  foreground, then **restores the previously-frontmost app** (`restore_focus`) —
  so it never interrupts mid-typing. Residual: needs Accessibility permission
  (cached at process launch — grant then relaunch the sending process); duplicate
  display names require corroboration beyond the name; if the user set
  "Enter=newline", Return won't send (composer-not-cleared → fall back to ⌘Return).
- **Exact `@self` group detection** ✅ (done 2026-07-18): mentions are stored as a
  comma-separated wxid list in the message `source` column's
  `<msgsource><atuserlist>` (NOT `packed_info_data`, which is only flags). The
  reader (`schema.parse_mentions` + `backend.py`) extracts them, handling zstd
  (`WCDB_CT_source==4`); `WechatMessage.mentions_user(self_wxid)` gates group
  replies. Verified end-to-end on real data (self wxid `derek840121` matched in
  real group @-messages). Group triggers require `self_user_id` populated on the
  account so the gate has a wxid to match.
- **Direction**: real outbound/inbound needs the self-wxid; defaults inbound
  until `self_username` is populated.
