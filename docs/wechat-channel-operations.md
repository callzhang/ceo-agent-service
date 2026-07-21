# WeChat Personal-Account Channel — Operations

Status: the local personal-account receive/read/decide/send pipeline is implemented
and verified on this Mac. Sending remains disabled by default in repository
configuration, while this deployment explicitly enables automatic sending. Both
feasibility gates are proven (see
[research doc](wechat-channel-and-local-memory-research.md) and the plan's Task 4/5/10
correction blocks): local-DB read (1.72M messages decrypted, 0 HMAC failures) and
Accessibility send (verified live to 文件传输助手, including background queue status
`sent` followed by an exact outbound database readback).

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
| `sender_ipc.py` / `sender_helper.py` | Owner-only IPC client/server and dedicated signed Sender app entrypoint |
| `memory_import.py` / `memory_writer.py` | Bounded extraction + deterministic cleanup; claimed, approved-only Memory writer (`memory.py` keeps public imports) |
| `setup.py` / `audit_web.py` | Tutorial connect service + visible contact/group picker |
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

## Dedicated Reader permission boundary

The production reader is the dedicated **CEO WeChat Reader** executable with a
fixed bundle identifier and stable signing identity. Only that helper receives
App Data permission; it returns normalized, bounded results to the main service
over a local authenticated IPC interface, so the main Python service never opens
the WeChat database. Granting App Data / Full Disk Access to a shared Python
interpreter is neither required nor recommended. First authorization remains an
explicit local user action; zero-click deployment requires managed macOS/MDM
privacy policy.

## Dedicated Sender permission boundary

Accessibility is granted to `~/Applications/CEO WeChat Sender.app` (bundle ID
`com.stardust.ceo-agent.wechat-sender`), not to Miniforge Python or the main
launchd service. The main service calls a strict owner-only Unix socket (mode
`0600`); the helper only exposes health/preflight, target identification, bounded
text send, and best-effort recall operations. Build and install it with:

```sh
CEO_WECHAT_READER_SIGNING_IDENTITY='CEO WeChat Reader Local Signing' \
  ./scripts/build-wechat-sender-app.sh
./scripts/install-wechat-sender-app.sh
```

The stable signing identity prevents ordinary rebuilds from producing a new TCC
identity. After first install, add the dedicated app once in System Settings →
Privacy & Security → Accessibility and restart its LaunchAgent. The AX runner
resolves the actual WeChat application by bundle ID, waits for asynchronous UI
state, and navigates duplicate direct-chat names with the stable target ID before
requiring the composer title to match the expected display name. Group navigation
uses the verified unique group name.

## Diagnostic CLI

```bash
.venv/bin/python -m app.wechat.cli status                     # probe capability per account
.venv/bin/python -m app.wechat.cli read-recent --db data/auto-reply.sqlite3 --target-id filehelper --limit 100   # uses persisted self_user_id; redacted metadata
.venv/bin/python -m app.wechat.cli read-recent --db data/auto-reply.sqlite3 --target-id filehelper --include-text  # explicit local verify
.venv/bin/python -m app.wechat.cli produce-once               # scan enabled scopes → enqueue
.venv/bin/python -m app.cli wechat import-memory --db data/auto-reply.sqlite3 \
    --account-id '<ready-account-id>' --target-id '<wxid-or-chatroom-id>' \
    --since '2026-01-01' --until '2026-07-20T23:59:59+08:00' --limit 1000
.venv/bin/python scripts/wechat_key_probe.py --passphrase-file ~/.config/wx_read/passphrase.hex \
    --account-db-dir "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<acct>/db_storage"
```

`import-memory` is an explicit, one-shot operation. It requires exactly one
persisted `ready` account with a resolved self wxid, one or more `--target-id`
values (maximum 100), a date bound, and a total `--limit` from 1–10000. It reads
at most that limit from each target, keeps the globally newest bounded set
independent of target argument order, and sends only non-empty text messages to
the extraction runner. It reads only those local conversations, creates
cleaned `pending` rows, and never changes the automatic-reply activation
watermark or calls `memory_write`. Repeating the exact same account, targets,
bounds, and limit is idempotent.

Review the result at `/wechat/memory-review`. Each row shows the cleaned
statement, category, confidence, sensitivity, minimal redacted evidence, source
time and message/conversation IDs, cleanup notes, reviewer/time, review state,
Memory id/error, and write state. Approval is deliberately
per-row and requires an editable final statement and reviewer. Memory writing is
a second explicit action that processes only checked, already-approved IDs.
Bulk reject is available; bulk approve is not. A rejected or revoked row cannot
be written. If a previously written row is revoked, the page records
`revocation_unavailable` because the current Memory backend has no supported
delete/revoke operation; it does not claim that the Memory was removed.

The writer transactionally claims each approved row. Concurrent clicks can call
the backend at most once. A confirmed successful `memory_write` tool event and a
stable Memory id are required before `written` is recorded. Ambiguous results are
marked `unknown` and are never retried automatically; clear failures are marked
`failed` for an intentional manual retry. Extraction and review persist no raw
chat transcript, DB path, passphrase, token, or API key.
If the process crashes while a row is `writing`, an operator can use that row's
“中断写入标记为 unknown” action; it never turns the row back into an automatically
retryable state. Cross-run candidates with the same normalized statement reuse
the existing row and merge source IDs/time without resetting its review/write
state; rejected/revoked rows do not suppress a later import. Before any pending
row is created, a read-only Codex matcher is hard-limited to the
`memory_connector.memory_recall` tool. Exact durable-Memory matches are skipped;
compatible matches use a separately validated merged statement and remain
pending; contradictory matches retain the new statement and are flagged pending.
Every non-`none` relation must cite a Memory id and minimal evidence that are
programmatically verified against the same object in the connector's returned
`memories` list; an empty list is a successful `none` result. The recall query
is deterministic for the whole candidate batch and must match the sole audited
tool call exactly. Missing, ambiguous, unrelated, or tool-noncompliant recall
fails the import closed.
Model-provided cleanup notes are never persisted. The interrupted-write action
also requires explicit confirmation and a row that has remained `writing` for at
least 15 minutes.

## Shared-file integration status

1. ✅ **`app/setup_wizard.py`** (updated 2026-07-21) — `wechat_connection` step registered
   (Phase 3, gates only on local `preflight`, independent of optional Memory MCP,
   `service_config`, and `data_corpus`; actions `check`/`connect` only).
   `run_setup_action`/`check_setup_step` dispatch to
   `WechatSetupService` via `service.build_setup_service`. `SetupWizardEvent` gained
   `next_step_status` so a successful action leaves the step `blocked` when the
   reader is blocked.
2. ✅ **`app/audit_web.py`** (updated 2026-07-21) — Tutorial shows only Check and
   Connect WeChat as soon as preflight is complete. Config → WeChat exposes one
   combined search for friends and groups (the results carry a visible type
   label) backed by `GET /config/wechat/conversations` and
   `POST /config/wechat/reply-scope`.
   Direct chats use `every_inbound_text`; groups are fixed to
   `mention_current_account`. The page does not expose DB paths or raw messages.
3. ✅ **`app/cli.py`** (done 2026-07-18):
   - `ceo-agent wechat <status|read-recent|produce-once|consume-once>` passes
     through (argparse REMAINDER) to `app.wechat.cli`. `wechat status`
     auto-detects+persists `self_user_id` and reports `ready`.
   - `run_service()` starts `wechat-producer`/`wechat-consumer` threads only when
     `CEO_WECHAT_READER_ENABLED` **and** a single account is persisted `ready`
     with a non-empty self-wxid
     (`_wechat_service_components`); disabled by default (no effect on the DingTalk
     service). Auto-send stays gated — the loops enqueue tasks and produce
     `ready_to_send` deliveries but do not send.
   - If macOS denies access to another app's data (`EACCES`/`EPERM`), the WeChat
     loop records one `wechat_data_permission_required` error and stops until
     service restart instead of retrying every poll interval.

## History review

The main History page (`/`) exposes four object checkboxes: `replay`, `wechat`,
`task`, and `meeting`. All are selected by default. Selecting only `wechat`
filters the SQL query, count, search, and pagination to WeChat reply attempts;
selecting only `replay` shows non-WeChat reply attempts. WeChat rows carry a
green channel badge. In confirm mode, an unambiguous `ready_to_send` delivery
also shows inline send/reject actions and returns to History after the action.
The action is matched by the exact conversation and trigger-message IDs, not by
conversation alone. A WeChat History row derives its displayed status from that
delivery, so a manually rejected delivery is shown as failed rather than stale
pending.

## Controlled verification (before any live send)

1. `.venv/bin/python -m pytest -q` — full suite green.
2. `wechat status` → the account reports `ready`; `read-recent --include-text` on
   File Transfer Helper — compare count/order/direction/timestamp/sender/text;
   run twice, confirm `produce-once` enqueues zero duplicates on the second scan.
   `read-recent` requires exactly one account persisted as `ready`; it never falls
   back to a directory discovered on disk. If that ready row has no self-wxid, it
   probes only that account and refuses to print guessed directions on failure.
3. Decision dry-run with `CEO_WECHAT_SENDER_ENABLED=0`: one direct message → one
   task + audited decision; an ordinary group message → no task; a real
   `@current account` group message → one task; no external send.
4. Sender only after File Transfer Helper binding is `verified`: one fixed test
   reply, confirm delivery status `sent` + matching outbound DB record, restart
   before a second send and confirm no duplicate; force a post-action ambiguity
   and verify `send_unknown` pauses the target with no auto-retry. This exact
   background-queue → dedicated Sender → outbound DB readback passed on
   2026-07-21.
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

- **Sender** (pure-AX, reverified 2026-07-21): composes via `AXValue` set on `chat_input_field`
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
  (cached at process launch — grant then relaunch the dedicated Sender app).
  Duplicate direct-chat display names are navigated with their stable target ID
  and then corroborated against the visible composer title; groups remain
  fail-closed on unique verified names. If the user set
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
  until `self_username` is populated. The diagnostic CLI now requires a resolved
  self-wxid before reading, so it cannot silently label every row inbound.
  Automatic producer, consumer, and sender loops do not start without it, and
  one-shot produce/consume commands build their reader with that persisted id.
- **Activation watermark**: each selected scope starts at its activation time;
  historical messages are never fed into auto-reply. The producer advances that
  scope only after the entire normalized batch has been handled. Producer pages
  oldest-first after the watermark while retaining every row in the watermark
  second as overlap; the unique task key removes duplicates without losing a
  same-second row that becomes visible later. Historical Memory import remains a
  separate, bounded, human-reviewed operation.
