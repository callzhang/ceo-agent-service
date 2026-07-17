# WeChat Personal Account Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Tutorial-managed WeChat personal-account channel that reads selected local chats, automatically evaluates selected direct messages and selected-group `@current account` messages, sends exact-once text replies through macOS Accessibility, and supports a separate approved-only historical Memory import.

**Architecture:** Implement WeChat as a separate adapter, producer, consumer, and sender around the existing SQLite audit and Codex decision infrastructure. Add `channel` isolation to shared reply records so the DingTalk worker cannot claim WeChat work. Treat current-version database-key acquisition and stable UI-target verification as hard feasibility gates: the Tutorial may report `blocked`, but live reply must remain disabled until both gates pass.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite, FastAPI, pytest, macOS `osascript`/Accessibility, WCDB/SQLCipher through a subprocess boundary, existing Codex decision runner and Memory Connector MCP.

---

## Scope and execution order

Execute this plan in a clean `codex/` worktree. Preserve the user's existing
uncommitted changes in the main checkout. The stages are ordered and gated:

1. local models, persistence, discovery, snapshot, and reader contract;
2. current WeChat `4.1.10.80` key-provider feasibility proof;
3. Tutorial connection and stable friend/group selection;
4. isolated WeChat producer, decision consumer, and Accessibility sender;
5. one-shot historical candidate extraction, review, and approved Memory write;
6. controlled runtime verification and launchd rollout.

Tasks after the reader gate can be implemented against fakes, but no live
automatic sending may be enabled until Task 5 records `ready`. Task 10 adds a
second gate for stable UI target binding; arbitrary contacts/groups remain
disabled if it cannot be proven.

Do not implement or configure Tencent iLink in this plan. It remains a separate
future Bot channel and is not a fallback when the personal-account reader or
sender is blocked.

## File map

Create one focused package rather than adding WeChat logic to the already-large
`app/worker.py` and `app/audit_web.py`:

- `app/wechat/models.py`: channel data contracts and state enums.
- `app/wechat/discovery.py`: installed app, account, DB, friend, and group discovery.
- `app/wechat/snapshot.py`: restricted temporary DB/WAL/SHM snapshots.
- `app/wechat/key_provider.py`: in-memory key-provider boundary and blocked state.
- `app/wechat/sqlcipher.py`: SQLCipher subprocess protocol with key on stdin.
- `app/wechat/reader.py`: capability probe, schema parser, normalized reads, and watermarks.
- `app/wechat/setup.py`: Tutorial connection/check/save-scope operations.
- `app/wechat/producer.py`: selected-scope and exact-mention eligibility plus enqueue.
- `app/wechat/prompt.py`: channel-specific prompt/context rendering.
- `app/wechat/consumer.py`: Codex decision, audit attempt, and delivery preparation.
- `app/wechat/accessibility.py`: preflight, target binding, one-shot send, and confirmation.
- `app/wechat/service.py`: producer/consumer loops and recovery.
- `app/wechat/memory_import.py`: bounded extraction and candidate cleanup.
- `app/wechat/memory_writer.py`: approved-only Memory Connector write orchestration.
- `app/wechat/audit_web.py`: Tutorial picker and Memory review HTML helpers/routes.
- `app/schemas/wechat_memory_candidates.schema.json`: structured extraction schema.
- `scripts/wechat_key_probe.py`: isolated, non-persistent current-version diagnostic.

Modify shared files only at their integration boundaries:

- `app/store.py`: tables, channel columns, and narrowly scoped store methods.
- `app/setup_wizard.py`: add the independent `wechat_connection` step.
- `app/setup_wizard_models.py`: no new statuses; reuse existing contracts.
- `app/audit_web.py`: include WeChat router/render helper, not business logic.
- `app/cli.py`: commands, settings, service threads, and restart recovery.
- `app/config.py`: WeChat enable flags and intervals.
- `.env.example`: safe disabled defaults.

Add focused tests under `tests/wechat/` plus small integration assertions in
`tests/test_store.py`, `tests/test_setup_wizard.py`, `tests/test_audit_web.py`,
`tests/test_cli.py`, and `tests/test_worker.py`.

### Task 1: Define WeChat channel contracts

**Files:**
- Create: `app/wechat/__init__.py`
- Create: `app/wechat/models.py`
- Create: `tests/wechat/test_models.py`

- [ ] **Step 1: Write failing model tests**

```python
from pydantic import ValidationError
import pytest

from app.wechat.models import WechatMessage, WechatReplyScope


def test_group_message_mentions_exact_current_account():
    message = WechatMessage(
        account_id="acct-1",
        conversation_id="group-1",
        message_id="msg-1",
        sender_id="member-1",
        sender_display_name="Mina",
        conversation_type="group",
        direction="inbound",
        sent_at="2026-07-17T10:00:00+08:00",
        kind="text",
        text="@Derek 看下",
        mentioned_user_ids=["self-1"],
        source_version="4.1.10.80",
    )

    assert message.mentions_user("self-1") is True
    assert message.mentions_user("self-2") is False


def test_group_scope_cannot_use_direct_trigger():
    with pytest.raises(ValidationError):
        WechatReplyScope(
            account_id="acct-1",
            target_type="group",
            target_id="group-1",
            display_name="CEO group",
            trigger_mode="every_inbound_text",
        )
```

- [ ] **Step 2: Run the model tests and verify failure**

Run: `.venv/bin/python -m pytest tests/wechat/test_models.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.wechat'`.

- [ ] **Step 3: Implement immutable contracts and trigger validation**

```python
# app/wechat/models.py
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CapabilityStatus = Literal["ready", "blocked", "failed"]
TargetType = Literal["direct", "group"]
TriggerMode = Literal["every_inbound_text", "mention_current_account"]


class WechatAccount(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: str
    display_name: str
    self_user_id: str
    account_dir: str
    db_dir: str
    app_version: str


class WechatReplyTarget(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: str
    target_type: TargetType
    target_id: str
    conversation_id: str = ""
    display_name: str
    last_active_at: str = ""


class WechatReplyScope(WechatReplyTarget):
    trigger_mode: TriggerMode
    enabled: bool = True
    binding_status: Literal["unverified", "verified", "conflict"] = "unverified"
    binding_evidence: dict[str, str] = Field(default_factory=dict)
    disabled_reason: str = ""

    @model_validator(mode="after")
    def validate_trigger(self):
        expected = (
            "every_inbound_text"
            if self.target_type == "direct"
            else "mention_current_account"
        )
        if self.trigger_mode != expected:
            raise ValueError(f"{self.target_type} requires trigger_mode={expected}")
        return self


class WechatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: str
    conversation_id: str
    message_id: str
    sender_id: str
    sender_display_name: str
    conversation_type: TargetType
    direction: Literal["inbound", "outbound"]
    sent_at: str
    kind: Literal["text", "image", "file", "quote", "system", "unknown"]
    text: str = ""
    mentioned_user_ids: frozenset[str] = Field(default_factory=frozenset)
    source_version: str

    def mentions_user(self, user_id: str) -> bool:
        return bool(user_id) and user_id in self.mentioned_user_ids


class WechatCapability(BaseModel):
    status: CapabilityStatus
    account_id: str = ""
    app_version: str = ""
    reason: str = ""
    checked_at: str = ""


class WechatDelivery(BaseModel):
    id: int = 0
    task_id: int
    account_id: str
    target_type: TargetType
    target_id: str
    conversation_id: str = ""
    reply_text: str
    status: Literal[
        "ready_to_send", "sending", "sent", "send_unknown", "failed"
    ] = "ready_to_send"
    evidence: dict[str, str] = Field(default_factory=dict)
    error: str = ""
```

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_models.py -q`

Expected: `2 passed`.

```bash
git add app/wechat/__init__.py app/wechat/models.py tests/wechat/test_models.py
git commit -m "feat: add WeChat channel contracts"
```

### Task 2: Add isolated persistence and channel-safe reply queues

**Files:**
- Modify: `app/store.py`
- Modify: `tests/test_store.py`
- Create: `tests/wechat/test_store.py`

- [ ] **Step 1: Write failing migration and scope tests**

```python
from app.store import AutoReplyStore
from app.wechat.models import WechatReplyScope


def test_store_round_trips_wechat_scope(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    scope = WechatReplyScope(
        account_id="acct-1",
        target_type="group",
        target_id="group-1",
        conversation_id="cid-1",
        display_name="CEO group",
        trigger_mode="mention_current_account",
    )
    store.replace_wechat_reply_scopes("acct-1", [scope])

    assert store.list_wechat_reply_scopes("acct-1") == [scope]


def test_dingtalk_claim_does_not_claim_wechat_task(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="cid-1",
        conversation_title="Friend",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time="2026-07-17 10:00:00",
        trigger_sender="Friend",
        trigger_text="hello",
    )

    assert store.claim_reply_tasks(10, channel="dingtalk") == []
    assert len(store.claim_reply_tasks(10, channel="wechat")) == 1
```

- [ ] **Step 2: Run focused tests and verify the missing APIs**

Run: `.venv/bin/python -m pytest tests/wechat/test_store.py -q`

Expected: failures for `replace_wechat_reply_scopes` and the unknown `channel` argument.

- [ ] **Step 3: Add additive SQLite migrations**

Add `channel text not null default 'dingtalk'` to `reply_tasks`,
`reply_attempts`, and `sent_replies` through the existing `pragma table_info`
migration pattern. Create these tables in `AutoReplyStore._initialize()`:

```sql
create table if not exists wechat_read_state (
    account_id text primary key,
    account_dir text not null,
    db_dir text not null,
    app_version text not null,
    self_user_id text not null default '',
    capability_status text not null default 'blocked',
    capability_reason text not null default '',
    watermark_sent_at text not null default '',
    watermark_message_id text not null default '',
    last_scan_at text not null default '',
    updated_at text not null default current_timestamp
);
create table if not exists wechat_reply_scopes (
    account_id text not null,
    target_type text not null,
    target_id text not null,
    conversation_id text not null default '',
    display_name text not null,
    trigger_mode text not null,
    enabled integer not null default 1,
    binding_status text not null default 'unverified',
    binding_evidence_json text not null default '{}',
    disabled_reason text not null default '',
    last_discovered_at text not null default '',
    updated_at text not null default current_timestamp,
    primary key(account_id, target_type, target_id)
);
create table if not exists wechat_deliveries (
    id integer primary key autoincrement,
    reply_task_id integer not null unique,
    account_id text not null,
    target_type text not null,
    target_id text not null,
    conversation_id text not null default '',
    reply_text text not null,
    status text not null default 'ready_to_send',
    action_started_at text not null default '',
    evidence_json text not null default '{}',
    error text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(reply_task_id) references reply_tasks(id)
);
create index if not exists idx_wechat_deliveries_status
    on wechat_deliveries(status, id);
```

- [ ] **Step 4: Implement typed scope and channel-filtered queue methods**

Update `ReplyTask` with `channel: str = "dingtalk"`, add `channel` to enqueue
and row conversion, and change the claim signature to:

```python
def claim_reply_tasks(
    self,
    limit: int,
    now: str | None = None,
    *,
    channel: str = "dingtalk",
) -> list[ReplyTask]:
    # Existing transaction remains; add `and channel=?` and bind channel before limit.
```

Add optional `channel` filters to `list_reply_tasks()` and
`count_reply_tasks()`; preserve their current all-channel behavior when the
argument is `None`. Add `channel="dingtalk"` to `record_reply_attempt()`,
`record_reply_attempt_for_trigger()`, and `record_sent_reply()`, and include it
in their inserts and row models. Existing callers need no edits because the
default remains DingTalk.

Add methods with these exact contracts:

```python
def replace_wechat_reply_scopes(
    self, account_id: str, scopes: list[WechatReplyScope]
) -> None:
    if any(scope.account_id != account_id for scope in scopes):
        raise ValueError("scope account mismatch")
    with self._connect() as db:
        db.execute(
            "update wechat_reply_scopes set enabled=0, disabled_reason='not_selected', updated_at=current_timestamp where account_id=?",
            (account_id,),
        )
        for scope in scopes:
            db.execute(
                """
                insert into wechat_reply_scopes (
                    account_id, target_type, target_id, conversation_id,
                    display_name, trigger_mode, enabled, binding_status,
                    binding_evidence_json, disabled_reason, last_discovered_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
                on conflict(account_id, target_type, target_id) do update set
                    conversation_id=excluded.conversation_id,
                    display_name=excluded.display_name,
                    trigger_mode=excluded.trigger_mode,
                    enabled=excluded.enabled,
                    binding_status=excluded.binding_status,
                    binding_evidence_json=excluded.binding_evidence_json,
                    disabled_reason='',
                    last_discovered_at=excluded.last_discovered_at,
                    updated_at=current_timestamp
                """,
                (
                    scope.account_id, scope.target_type, scope.target_id,
                    scope.conversation_id, scope.display_name,
                    scope.trigger_mode, int(scope.enabled),
                    scope.binding_status,
                    json.dumps(scope.binding_evidence, ensure_ascii=False),
                    scope.last_active_at,
                ),
            )

def list_wechat_reply_scopes(
    self, account_id: str, *, enabled_only: bool = False
) -> list[WechatReplyScope]:
    where = "where account_id=?" + (" and enabled=1" if enabled_only else "")
    with self._connect() as db:
        rows = db.execute(
            f"select * from wechat_reply_scopes {where} order by target_type, display_name, target_id",
            (account_id,),
        ).fetchall()
    return [
        WechatReplyScope(
            account_id=row["account_id"], target_type=row["target_type"],
            target_id=row["target_id"], conversation_id=row["conversation_id"],
            display_name=row["display_name"], trigger_mode=row["trigger_mode"],
            enabled=bool(row["enabled"]), binding_status=row["binding_status"],
            binding_evidence=json.loads(row["binding_evidence_json"]),
            disabled_reason=row["disabled_reason"],
            last_active_at=row["last_discovered_at"],
        )
        for row in rows
    ]

def get_wechat_reply_scope(
    self, account_id: str, target_type: str, target_id: str
) -> WechatReplyScope | None:
    return next(
        (
            scope for scope in self.list_wechat_reply_scopes(account_id)
            if scope.target_type == target_type and scope.target_id == target_id
        ),
        None,
    )

def upsert_wechat_read_state(
    self, *, account_id: str, account_dir: str, db_dir: str,
    app_version: str, self_user_id: str, capability_status: str,
    capability_reason: str = "", watermark_sent_at: str = "",
    watermark_message_id: str = "", last_scan_at: str = "",
) -> None:
    with self._connect() as db:
        db.execute(
            """
            insert into wechat_read_state (
                account_id, account_dir, db_dir, app_version, self_user_id,
                capability_status, capability_reason, watermark_sent_at,
                watermark_message_id, last_scan_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(account_id) do update set
                account_dir=excluded.account_dir, db_dir=excluded.db_dir,
                app_version=excluded.app_version, self_user_id=excluded.self_user_id,
                capability_status=excluded.capability_status,
                capability_reason=excluded.capability_reason,
                watermark_sent_at=excluded.watermark_sent_at,
                watermark_message_id=excluded.watermark_message_id,
                last_scan_at=excluded.last_scan_at,
                updated_at=current_timestamp
            """,
            (
                account_id, account_dir, db_dir, app_version, self_user_id,
                capability_status, capability_reason, watermark_sent_at,
                watermark_message_id, last_scan_at,
            ),
        )

def get_wechat_read_state(self, account_id: str) -> dict[str, str] | None:
    with self._connect() as db:
        row = db.execute(
            "select * from wechat_read_state where account_id=?", (account_id,)
        ).fetchone()
    return dict(row) if row is not None else None

def list_wechat_read_states(self) -> list[dict[str, str]]:
    with self._connect() as db:
        rows = db.execute(
            "select * from wechat_read_state order by account_id"
        ).fetchall()
    return [dict(row) for row in rows]

def list_wechat_reply_scopes_for_ready_account(
    self, *, enabled_only: bool = True
) -> list[WechatReplyScope]:
    ready = [
        row for row in self.list_wechat_read_states()
        if row["capability_status"] == "ready"
    ]
    if len(ready) != 1:
        return []
    return self.list_wechat_reply_scopes(
        ready[0]["account_id"], enabled_only=enabled_only
    )
```

Use one transaction in `replace_wechat_reply_scopes`: validate every scope's
`account_id`, upsert submitted stable IDs, and disable omitted prior rows rather
than deleting audit history.

- [ ] **Step 5: Prove DingTalk compatibility and commit**

Run:

```bash
.venv/bin/python -m pytest tests/wechat/test_store.py tests/test_store.py tests/test_worker.py -q
```

Expected: all selected tests pass and existing rows still deserialize as
`channel="dingtalk"`.

```bash
git add app/store.py tests/test_store.py tests/wechat/test_store.py
git commit -m "feat: isolate WeChat persistence and queues"
```

### Task 3: Implement app/account discovery and read-only snapshots

**Files:**
- Create: `app/wechat/discovery.py`
- Create: `app/wechat/snapshot.py`
- Create: `tests/wechat/test_discovery.py`
- Create: `tests/wechat/test_snapshot.py`

- [ ] **Step 1: Write discovery and cleanup tests**

```python
def test_discover_accounts_ignores_directories_without_db_storage(tmp_path):
    container = tmp_path / "Documents" / "xwechat_files"
    valid = container / "acct_a" / "db_storage"
    valid.mkdir(parents=True)
    (valid / "message_0.db").write_bytes(b"db")
    (container / "cache_only").mkdir()

    accounts = discover_account_directories(container)

    assert [item.account_id for item in accounts] == ["acct_a"]


def test_snapshot_copies_db_wal_shm_and_cleans_up(tmp_path):
    source = tmp_path / "message_0.db"
    source.write_bytes(b"db")
    source.with_name(source.name + "-wal").write_bytes(b"wal")
    source.with_name(source.name + "-shm").write_bytes(b"shm")

    with readonly_snapshot(source, temp_root=tmp_path / "snapshots") as snapshot:
        assert sorted(path.name for path in snapshot.parent.iterdir()) == [
            "message_0.db", "message_0.db-shm", "message_0.db-wal"
        ]
        snapshot.write_bytes(b"snapshot-only")

    assert source.read_bytes() == b"db"
    assert not (tmp_path / "snapshots").exists()
```

- [ ] **Step 2: Run tests and verify missing modules**

Run: `.venv/bin/python -m pytest tests/wechat/test_discovery.py tests/wechat/test_snapshot.py -q`

Expected: import failures for both new modules.

- [ ] **Step 3: Implement deterministic discovery**

Use `plistlib` to read `/Applications/WeChat.app/Contents/Info.plist`; do not
launch or modify the app. Implement:

```python
def discover_wechat_install(
    app_path: Path = Path("/Applications/WeChat.app"),
) -> WechatInstall:
    with (app_path / "Contents/Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    return WechatInstall(
        app_path=app_path,
        bundle_id=str(info["CFBundleIdentifier"]),
        version=str(info.get("CFBundleShortVersionString") or info["CFBundleVersion"]),
    )

def discover_account_directories(xwechat_root: Path) -> list[WechatAccountDirectory]:
    result = []
    for account_dir in sorted(xwechat_root.iterdir(), key=lambda path: path.name):
        db_dir = account_dir / "db_storage"
        if db_dir.is_dir() and next(db_dir.rglob("*.db"), None) is not None:
            result.append(
                WechatAccountDirectory(
                    account_id=account_dir.name,
                    account_dir=account_dir,
                    db_dir=db_dir,
                )
            )
    return result

def default_xwechat_root() -> Path:
    return Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
```

Sort account candidates by stable directory name, not modification time. Return
zero, one, or many; never silently choose among multiple accounts.

- [ ] **Step 4: Implement a restricted snapshot context manager**

```python
@contextmanager
def readonly_snapshot(source_db: Path, *, temp_root: Path) -> Iterator[Path]:
    temp_root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="wechat-snapshot-", dir=temp_root.parent) as raw:
        root = Path(raw)
        root.chmod(0o700)
        for candidate in (source_db, source_db.with_name(source_db.name + "-wal"), source_db.with_name(source_db.name + "-shm")):
            if candidate.exists():
                shutil.copy2(candidate, root / candidate.name)
        snapshot = root / source_db.name
        if not snapshot.exists():
            raise FileNotFoundError(source_db)
        yield snapshot
```

Do not call SQLite against the source path. Add a startup cleanup helper that
removes only stale directories with the exact `wechat-snapshot-` prefix under
the configured snapshot parent.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_discovery.py tests/wechat/test_snapshot.py -q`

Expected: all tests pass.

```bash
git add app/wechat/discovery.py app/wechat/snapshot.py tests/wechat/test_discovery.py tests/wechat/test_snapshot.py
git commit -m "feat: discover WeChat accounts and snapshot databases"
```

### Task 4: Build the key boundary, SQLCipher protocol, and blocked reader

**Files:**
- Create: `app/wechat/key_provider.py`
- Create: `app/wechat/sqlcipher.py`
- Create: `app/wechat/reader.py`
- Create: `tests/wechat/fakes.py`
- Create: `tests/wechat/conftest.py`
- Create: `tests/wechat/test_reader.py`
- Modify: `.env.example`

- [ ] **Step 1: Write reader capability and normalization tests**

```python
def test_reader_is_blocked_without_key_provider(fake_account, tmp_path):
    reader = WechatReader(
        backend=FakeCipherBackend(),
        key_provider=UnavailableKeyProvider("no validated provider"),
        snapshot_root=tmp_path,
    )

    capability = reader.probe(fake_account)

    assert capability.status == "blocked"
    assert capability.reason == "no validated provider"


def test_reader_normalizes_exact_group_mentions(fake_account, tmp_path):
    backend = FakeCipherBackend(
        rows=[{
            "message_id": "m1", "conversation_id": "g1", "sender_id": "u1",
            "sender_name": "Mina", "direction": "inbound", "sent_at": "2026-07-17T10:00:00+08:00",
            "kind": "text", "text": "@Derek hi", "mentioned_user_ids": ["self-1"],
            "conversation_type": "group",
        }]
    )
    reader = WechatReader(backend, StaticTestKeyProvider(b"secret"), tmp_path)

    messages = reader.read_messages(fake_account, limit=100)

    assert messages[0].mentioned_user_ids == frozenset({"self-1"})
```

- [ ] **Step 2: Run tests and verify missing reader APIs**

Run: `.venv/bin/python -m pytest tests/wechat/test_reader.py -q`

Expected: imports fail for `key_provider`, `sqlcipher`, and `reader`.

- [ ] **Step 3: Implement the in-memory provider protocol**

```python
class WechatKeyProvider(Protocol):
    def key_for(self, account: WechatAccount) -> bytes:
        raise NotImplementedError


class KeyProviderUnavailable(RuntimeError):
    pass


class UnavailableKeyProvider:
    def __init__(self, reason: str):
        self.reason = reason

    def key_for(self, account: WechatAccount) -> bytes:
        del account
        raise KeyProviderUnavailable(self.reason)
```

Keep `StaticTestKeyProvider` under `tests/wechat/fakes.py`; do not add an env-var
or file-based production key provider.

```python
# tests/wechat/fakes.py
class StaticTestKeyProvider:
    def __init__(self, key: bytes):
        self.key = key

    def key_for(self, account: WechatAccount) -> bytes:
        del account
        return self.key


class FakeCipherBackend:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []

    def probe(self, snapshot: Path, key: bytes) -> list[str]:
        del snapshot, key
        return ["Message"]

    def read_messages(self, snapshot: Path, key: bytes, *, limit: int) -> list[dict]:
        del snapshot, key
        return self.rows[:limit]
```

`tests/wechat/conftest.py` creates `fake_account` with a temporary DB/WAL/SHM
triple and imports these fakes for reader, producer, and setup tests.

- [ ] **Step 4: Implement SQLCipher over stdin**

`SqlCipherBackend` launches only `[sqlcipher_bin, "-batch", "-json", snapshot]`.
Send the key and fixed probe SQL on stdin so the key never appears in argv:

```python
script = "\n".join([
    f"PRAGMA key = \"x'{key.hex()}'\";",
    "PRAGMA query_only = ON;",
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;",
    ".quit",
])
process = subprocess.run(
    [self.sqlcipher_bin, "-batch", "-json", str(snapshot)],
    input=script,
    text=True,
    capture_output=True,
    timeout=self.timeout_seconds,
    check=False,
)
```

Redact stderr before raising, reject nonzero exit, reject an empty schema, and
zero a mutable `bytearray` copy in `finally`. Do not log stdin.

- [ ] **Step 5: Implement explicit parser profiles and reader status**

Create a `WechatSchemaProfile` registry keyed by exact app version. The
`4.1.10.80` profile contains verified queries only after Task 5 discovers table
and column names. Until then, `probe()` returns:

```python
WechatCapability(
    status="blocked",
    account_id=account.account_id,
    app_version=account.app_version,
    reason="wechat_4_1_10_80_schema_or_key_provider_unverified",
)
```

The reader must expose `probe(account)`, `list_targets(account, query, kind,
limit, offset)`, and `read_messages(account, conversation_id="", since="",
limit=100)`. Every method snapshots first and refuses to query when capability
is not `ready`.

- [ ] **Step 6: Add safe disabled defaults, run tests, and commit**

```dotenv
CEO_WECHAT_READER_ENABLED=0
CEO_WECHAT_SENDER_ENABLED=0
CEO_WECHAT_POLL_INTERVAL_SECONDS=5
CEO_WECHAT_SNAPSHOT_DIR=data/wechat-snapshots
CEO_WECHAT_SQLCIPHER_BIN=sqlcipher
```

Run: `.venv/bin/python -m pytest tests/wechat/test_reader.py tests/wechat/test_snapshot.py -q`

Expected: all tests pass, including an assertion that captured subprocess argv
does not contain `secret` or its hex value.

```bash
git add app/wechat/key_provider.py app/wechat/sqlcipher.py app/wechat/reader.py tests/wechat/fakes.py tests/wechat/conftest.py tests/wechat/test_reader.py .env.example
git commit -m "feat: add blocked-by-default WeChat reader"
```

### Task 5: Execute the current-version key and schema feasibility gate

**Files:**
- Create: `scripts/wechat_key_probe.py`
- Create: `tests/wechat/test_key_probe.py`
- Modify: `app/wechat/reader.py`
- Create: `tests/fixtures/wechat/README.md`

- [ ] **Step 1: Write a test that forbids raw key output**

```python
def test_probe_report_contains_fingerprint_not_key():
    report = candidate_report(b"0123456789abcdef", valid=True, schema=["Message"])
    encoded = json.dumps(report)

    assert "0123456789abcdef" not in encoded
    assert report["key_fingerprint"].startswith("sha256:")
    assert report["valid"] is True
```

- [ ] **Step 2: Implement a candidate pipe and fingerprint-only report**

The diagnostic parent creates a mode-`0600` FIFO, launches only the isolated
debug copy under LLDB, reads length-prefixed candidate bytes, validates each
candidate against a temporary snapshot, and writes a report containing only:

```python
def candidate_report(key: bytes, *, valid: bool, schema: list[str]) -> dict[str, object]:
    return {
        "key_fingerprint": "sha256:" + hashlib.sha256(key).hexdigest(),
        "valid": valid,
        "schema": schema,
    }
```

The LLDB ARM64 callback reads `sqlite3_key` arguments from `x1`/`x2` and writes
the bytes to the FIFO; it never prints them:

```python
def sqlite3_key_breakpoint(frame, _location, _internal):
    process = frame.GetThread().GetProcess()
    pointer = frame.FindRegister("x1").GetValueAsUnsigned()
    length = frame.FindRegister("x2").GetValueAsUnsigned()
    error = lldb.SBError()
    candidate = process.ReadMemory(pointer, length, error)
    if error.Success() and 0 < length <= 4096:
        os.write(KEY_PIPE_FD, struct.pack(">I", length) + candidate)
    return False
```

- [ ] **Step 3: Run only against an isolated diagnostic copy**

Run the probe with explicit paths and no shell interpolation:

```bash
.venv/bin/python scripts/wechat_key_probe.py \
  --app /private/tmp/WeChat-Debug.app \
  --source-account-index 0 \
  --source-db-relative message/message_0.db \
  --sqlcipher-bin sqlcipher \
  --report data/wechat-key-probe-report.json
```

Expected success evidence: `valid=true`, a nonempty schema list, no raw key in
the report/process output, no remaining FIFO/snapshot, and the official
`/Applications/WeChat.app` signature unchanged. Expected blocked evidence is a
specific reason such as `no sqlite3_key call observed` or `candidate failed
integrity`; it is not treated as partial success.

- [ ] **Step 4: Capture verified schema metadata without message content**

For a valid candidate, query only `sqlite_master`, `pragma table_info`, row
counts, and a maximum timestamp/ID. Add sanitized column/table fixtures and the
source version to `tests/fixtures/wechat/README.md`. Do not commit DB pages,
keys, contact names, group names, or message text.

- [ ] **Step 5: Make the gate decision and commit diagnostic artifacts**

If no production-safe key source exists outside the isolated re-signed copy,
leave the runtime provider unavailable and persist capability `blocked`. This
plan does not authorize using the diagnostic copy as the user's real WeChat or
persisting the recovered key. If a user-authorized, production-safe in-memory
source is proven, implement it behind `WechatKeyProvider` with a fake-backed
unit test and make `4.1.10.80` ready only after the SQLCipher integrity and
schema tests pass.

Run:

```bash
.venv/bin/python -m pytest tests/wechat/test_key_probe.py tests/wechat/test_reader.py -q
rg -n "0123456789abcdef|PRAGMA key" data/wechat-key-probe-report.json tests/fixtures/wechat || true
```

Expected: tests pass and the search returns no raw key. Stop live-channel work
if capability is still `blocked`; Tasks 6-13 may be developed only with fakes.

```bash
git add scripts/wechat_key_probe.py tests/wechat/test_key_probe.py app/wechat/reader.py tests/fixtures/wechat/README.md
git commit -m "test: gate WeChat 4.1.10 reader capability"
```

### Task 6: Add Tutorial connection and independent channel status

**Files:**
- Create: `app/wechat/setup.py`
- Modify: `app/setup_wizard.py`
- Modify: `app/setup_wizard_models.py`
- Modify: `app/audit_web.py`
- Modify: `tests/test_setup_wizard.py`
- Create: `tests/wechat/test_setup.py`

- [ ] **Step 1: Write failing wizard-step tests**

```python
def test_wechat_step_depends_only_on_service_config():
    step = get_step_definition("wechat_connection")
    assert step.depends_on == ("service_config",)
    assert [action.id for action in step.actions] == [
        "check_wechat_connection", "connect_wechat", "verify_wechat"
    ]


def test_blocked_wechat_does_not_block_data_corpus(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for step_id in ("preflight", "cli_components", "mcp", "service_config"):
        store.upsert_setup_wizard_step(step_id=step_id, status="done", summary="ok")
    store.upsert_setup_wizard_step(
        step_id="wechat_connection", status="blocked", summary="key unavailable"
    )

    statuses = {item.step_id: item for item in build_wizard_status(store).steps}
    assert statuses["data_corpus"].status == "not_started"


def test_setup_event_can_report_blocked_next_step():
    event = SetupWizardEvent(
        step_id="wechat_connection", action_id="connect_wechat",
        status="done", next_step_status="blocked",
    )
    assert event.next_step_status == "blocked"
```

- [ ] **Step 2: Add the independent wizard definition**

Insert after `service_config` without changing `data_corpus.depends_on`:

```python
SetupStepDefinition(
    id="wechat_connection",
    title="Connect WeChat",
    phase="Phase 3",
    description="Connect the local personal account and select reply targets.",
    depends_on=["service_config"],
    actions=[
        SetupAction(id="check_wechat_connection", label="Check", step_id="wechat_connection", kind="check"),
        SetupAction(id="connect_wechat", label="Connect WeChat", step_id="wechat_connection", kind="run"),
        SetupAction(id="verify_wechat", label="Save and verify", step_id="wechat_connection", kind="run"),
    ],
)
```

- [ ] **Step 3: Implement setup operations with dependency injection**

First extend `SetupWizardEvent` without changing existing callers:

```python
class SetupWizardEvent(BaseModel):
    id: int = 0
    step_id: str
    action_id: str
    status: SetupActionStatus
    next_step_status: SetupStatus | None = None
    summary: str = ""
    evidence: dict[str, str | int | bool] = Field(default_factory=dict)
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    started_at: str = ""
    finished_at: str = ""
```

Update `tutorial_run()` to persist
`event.next_step_status or ("done" if event.status == "done" else "failed")`.
This lets a successful diagnostic action leave the channel step accurately
`blocked`.

```python
class WechatSetupService:
    def __init__(
        self,
        store: AutoReplyStore,
        reader: WechatReader,
        accessibility_preflight: Callable[[], str],
    ):
        self.store = store
        self.reader = reader
        self.accessibility_preflight = accessibility_preflight

    def connect(self, selected_account_id: str = "") -> SetupWizardEvent:
        accounts = self.reader.discover_accounts()
        if not selected_account_id and len(accounts) != 1:
            return SetupWizardEvent(
                step_id="wechat_connection", action_id="connect_wechat",
                status="failed", summary="Select exactly one detected WeChat account.",
                evidence={"account_count": len(accounts)},
            )
        account = next(
            item for item in accounts
            if item.account_id == (selected_account_id or accounts[0].account_id)
        )
        capability = self.reader.probe(account)
        self.store.upsert_wechat_read_state(
            account_id=account.account_id, account_dir=account.account_dir,
            db_dir=account.db_dir, app_version=account.app_version,
            self_user_id=account.self_user_id,
            capability_status=capability.status,
            capability_reason=capability.reason,
        )
        return SetupWizardEvent(
            step_id="wechat_connection", action_id="connect_wechat",
            status="done",
            next_step_status=capability.status,
            summary=capability.reason or "WeChat database is connected.",
            evidence={
                "account_id": account.account_id,
                "database_status": capability.status,
                "accessibility_status": self.accessibility_preflight(),
            },
        )

    def check(self) -> SetupStepStatus:
        states = self.store.list_wechat_read_states()
        ready = [row for row in states if row["capability_status"] == "ready"]
        return SetupStepStatus(
            step_id="wechat_connection", title="Connect WeChat",
            status="done" if len(ready) == 1 else "needs_action",
            summary="WeChat is ready." if len(ready) == 1 else "Connect one WeChat account.",
        )

    def verify(self) -> SetupWizardEvent:
        status = self.check()
        scopes = self.store.list_wechat_reply_scopes_for_ready_account(enabled_only=True)
        accessibility_status = self.accessibility_preflight()
        complete = (
            status.status == "done"
            and bool(scopes)
            and accessibility_status == "ready"
        )
        return SetupWizardEvent(
            step_id="wechat_connection", action_id="verify_wechat",
            status="done",
            next_step_status="done" if complete else "blocked",
            summary="WeChat scope verified." if complete else "Select at least one stable reply target.",
            evidence={
                "selected_target_count": len(scopes),
                "accessibility_status": accessibility_status,
            },
        )
```

`connect()` auto-selects exactly one detected account, returns
`needs_action` with redacted account choices for multiple accounts, runs
`reader.probe`, persists `wechat_read_state`, and reports separate
`database_status` and `accessibility_status`. A blocked reader returns a
successful action event whose resulting step summary is blocked; it never
reports `done`.

- [ ] **Step 4: Run setup tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_setup.py tests/test_setup_wizard.py -q`

Expected: all tests pass and the existing ordered-step expectation includes
`wechat_connection` after `service_config`.

```bash
git add app/wechat/setup.py app/setup_wizard.py app/setup_wizard_models.py app/audit_web.py tests/test_setup_wizard.py tests/wechat/test_setup.py
git commit -m "feat: add WeChat connection to Tutorial"
```

### Task 7: Add searchable friend/group picker and stable scope persistence

**Files:**
- Create: `app/wechat/audit_web.py`
- Modify: `app/audit_web.py`
- Modify: `tests/test_audit_web.py`
- Create: `tests/wechat/test_audit_web.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_wechat_picker_separates_duplicate_names_by_stable_id(client, fake_reader):
    response = client.get("/tutorial/wechat/conversations?kind=direct&query=Alex")
    assert response.status_code == 200
    assert [row["target_id"] for row in response.json()["items"]] == ["u-1", "u-2"]


def test_scope_api_forces_group_mention_trigger(client):
    response = client.post("/tutorial/wechat/reply-scope", json={
        "account_id": "acct-1",
        "targets": [{
            "target_type": "group", "target_id": "g-1",
            "display_name": "CEO", "trigger_mode": "every_inbound_text"
        }],
    })
    assert response.status_code == 422
```

- [ ] **Step 2: Implement narrow local routes**

Expose from `app/wechat/audit_web.py`:

```python
def register_wechat_tutorial_routes(
    app: FastAPI,
    *,
    store_factory: Callable[[], AutoReplyStore],
    setup_factory: Callable[[], WechatSetupService],
) -> None:
    @app.get("/tutorial/wechat/conversations")
    def list_targets(query: str = "", kind: str = "direct", limit: int = 50, offset: int = 0):
        if kind not in {"direct", "group"} or not 1 <= limit <= 100 or offset < 0:
            raise HTTPException(status_code=422, detail="invalid target query")
        return setup_factory().list_targets(query=query, kind=kind, limit=limit, offset=offset)

    @app.post("/tutorial/wechat/reply-scope")
    def save_scope(payload: WechatReplyScopeRequest):
        return setup_factory().save_scope(payload)
```

Register:

- `GET /tutorial/wechat/conversations` with `query`, `kind`, `limit<=100`, and `offset`;
- `POST /tutorial/wechat/reply-scope` with Pydantic-validated stable targets;
- `POST /tutorial/run/connect_wechat` and `POST /tutorial/run/verify_wechat` through the existing setup event persistence path.

Reject targets not returned by a fresh reader lookup for the selected account,
cross-account targets, duplicate IDs, and empty selections.

- [ ] **Step 3: Render the picker in the existing wizard card**

The HTML helper must render Friends/Groups tabs, search, checkboxes keyed by
`target_type:target_id`, the fixed group help text, and **Save and verify**.
Escape every display label. Do not render raw message previews, database paths,
keys, or binding evidence.

- [ ] **Step 4: Run API/render tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_audit_web.py tests/test_audit_web.py tests/test_setup_wizard.py -q`

Expected: all tests pass; duplicate names render as separate checkbox values.

```bash
git add app/wechat/audit_web.py app/audit_web.py tests/wechat/test_audit_web.py tests/test_audit_web.py
git commit -m "feat: select WeChat reply targets in Tutorial"
```

### Task 8: Produce channel-isolated reply tasks with exact group mention gates

**Files:**
- Create: `app/wechat/producer.py`
- Create: `tests/wechat/test_producer.py`
- Modify: `app/store.py`

- [ ] **Step 1: Write direct, group, and idempotency tests**

```python
def test_selected_group_requires_structured_self_mention(producer, reader, store):
    reader.messages = [
        group_message("m1", text="Derek 看下", mentioned_user_ids=[]),
        group_message("m2", text="@Derek 看下", mentioned_user_ids=["self-1"]),
    ]

    assert producer.run_once() == 1
    tasks = store.list_reply_tasks(channel="wechat")
    assert [task.trigger_message_id for task in tasks] == ["m2"]


def test_repeated_scan_does_not_duplicate_wechat_task(producer, store):
    assert producer.run_once() == 1
    assert producer.run_once() == 0
    assert store.count_reply_tasks(channel="wechat") == 1
```

- [ ] **Step 2: Implement eligibility as a pure function**

```python
def is_reply_candidate(
    message: WechatMessage,
    scope: WechatReplyScope | None,
    *,
    self_user_id: str,
) -> bool:
    if scope is None or not scope.enabled:
        return False
    if message.direction != "inbound" or message.kind != "text":
        return False
    if message.conversation_type == "direct":
        return scope.trigger_mode == "every_inbound_text"
    return (
        scope.trigger_mode == "mention_current_account"
        and message.mentions_user(self_user_id)
    )
```

Never infer a mention from display-name text.

- [ ] **Step 3: Implement overlap scanning and enqueue**

`WechatReplyProducer.run_once()` loads `ready` read state and enabled scopes,
reads newer records with a bounded overlap, resolves direct scopes by
`sender_id` and group scopes by `conversation_id`, then calls:

```python
store.enqueue_reply_task(
    channel="wechat",
    conversation_id=message.conversation_id,
    conversation_title=scope.display_name,
    single_chat=message.conversation_type == "direct",
    trigger_message_id=message.message_id,
    trigger_create_time=message.sent_at,
    trigger_sender=message.sender_display_name,
    trigger_text=message.text,
    trigger_message_json=message.model_dump_json(),
)
```

Advance the watermark only after the complete batch is normalized and every
eligible enqueue has returned. The unique key becomes `(channel,
conversation_id, trigger_message_id)`; migrate the old uniqueness safely by
rebuilding `reply_tasks` only if SQLite cannot add the new constraint in place.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_producer.py tests/wechat/test_store.py tests/test_worker.py -q`

Expected: WeChat tasks are idempotent and the DingTalk worker still claims only
`channel="dingtalk"`.

```bash
git add app/wechat/producer.py app/store.py tests/wechat/test_producer.py tests/wechat/test_store.py tests/test_worker.py
git commit -m "feat: produce eligible WeChat reply tasks"
```

### Task 9: Add a WeChat-specific Codex decision consumer

**Files:**
- Create: `app/wechat/prompt.py`
- Create: `app/wechat/consumer.py`
- Create: `tests/wechat/test_prompt.py`
- Create: `tests/wechat/test_consumer.py`

- [ ] **Step 1: Write prompt and decision tests**

```python
def test_prompt_keeps_context_in_same_conversation():
    prompt = build_wechat_turn_prompt(trigger, [same_chat_context])
    assert "same chat" in prompt
    assert "other chat" not in prompt
    assert "memory_recall" in prompt


def test_send_reply_creates_ready_delivery(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="收到，我下午给你结论。",
        reason="明确承诺",
        audit_summary="明确承诺",
    )

    assert consumer.run_once(limit=1) == 1
    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery.status == "ready_to_send"
```

- [ ] **Step 2: Build a channel-specific prompt without DWS assumptions**

Use `render_user_prompt()` and the existing work-profile/developer prompt path.
The WeChat prompt must state:

```text
- This is a selected personal WeChat conversation.
- Use memory_recall for relevant durable history; never write Memory here.
- Return only the existing AgentEnvelope.
- Allowed user modes: send_reply, ask_clarifying_question, handoff_to_human, no_reply.
- Do not request DingTalk-only system actions, reactions, documents, OA, calendar, or DING.
- Group context that did not mention the principal is background only.
```

Serialize at most 20 same-conversation messages and redact unsupported payloads.

- [ ] **Step 3: Implement a separate consumer**

```python
class WechatReplyConsumer:
    def run_once(self, limit: int = 50) -> int:
        for task in self.store.claim_reply_tasks(limit, channel="wechat"):
            self.process(task)
        return processed
```

`process()` parses `WechatMessage`, loads bounded context through the reader,
calls the existing `CodexDecisionRunner`, records a `reply_attempts` row with
`channel="wechat"`, and handles actions:

- `no_reply`: mark attempt skipped and task done;
- `handoff_to_human`: mark attempt handoff and task done without delivery;
- `stop_with_error`: use bounded task retry;
- `send_reply` or `ask_clarifying_question`: leak-check text, create one
  `wechat_deliveries/ready_to_send` row, and leave final delivery to Task 10.

Reject nonempty DingTalk-only `system_actions` as a failed decision rather than
executing them.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_prompt.py tests/wechat/test_consumer.py tests/test_codex_decision.py tests/test_prompt.py -q`

Expected: all tests pass; no fake DWS client is required by WeChat tests.

```bash
git add app/wechat/prompt.py app/wechat/consumer.py tests/wechat/test_prompt.py tests/wechat/test_consumer.py
git commit -m "feat: decide WeChat replies with existing CEO agent"
```

### Task 10: Implement fail-closed Accessibility delivery and target binding

**Files:**
- Create: `app/wechat/accessibility.py`
- Create: `app/wechat/send_message.applescript`
- Create: `tests/wechat/test_accessibility.py`
- Modify: `app/store.py`

- [ ] **Step 1: Write sender-state and no-blind-retry tests**

```python
def test_unverified_binding_blocks_before_send(sender, fake_runner):
    result = sender.send(delivery, scope_with(binding_status="unverified"))
    assert result.status == "failed"
    assert result.error == "target_binding_unverified"
    assert fake_runner.calls == []


def test_post_action_ambiguity_becomes_send_unknown(sender, fake_runner):
    fake_runner.result = AccessibilityResult(
        action_performed=True, visible_confirmation=False, target_fingerprint="fp-1"
    )
    result = sender.send(delivery, verified_scope)
    assert result.status == "send_unknown"


def test_recovery_never_resends_sending_or_unknown(store, sender):
    store.mark_wechat_delivery_sending(delivery.id)
    recovered = reconcile_incomplete_deliveries(store, reader)
    assert recovered[0].status in {"sent", "send_unknown"}
    assert sender.calls == []
```

- [ ] **Step 2: Implement preflight and stable binding**

`MacWechatAccessibility.preflight()` verifies the official bundle ID, running
logged-in process, unlocked GUI session, and Accessibility permission. Binding
is a separate explicit setup action that opens the target, extracts visible
identity evidence, and matches it to DB evidence. Store only a fingerprint and
redacted evidence:

```python
fingerprint = hashlib.sha256(
    f"{scope.account_id}\0{scope.target_type}\0{scope.target_id}\0{visible_identity}".encode()
).hexdigest()
```

Duplicate display names, missing stable corroboration, or changed evidence set
`binding_status="conflict"`. Display-name-only matching can never become
`verified`.

- [ ] **Step 3: Implement the one-shot AppleScript boundary**

The script accepts target display data and reply text as argv, uses `System
Events` against bundle `com.tencent.xinWeChat`, verifies the selected visible
chat header, sets the composer value as text, and performs exactly one send
action. It returns JSON with `action_performed`, `visible_confirmation`, and
redacted visible identity. It must not loop or retry the send action.

The Python runner invokes:

```python
subprocess.run(
    ["/usr/bin/osascript", str(script_path), target_label, reply_text],
    text=True,
    capture_output=True,
    timeout=30,
    check=False,
)
```

Do not interpolate target or reply text into AppleScript source.

- [ ] **Step 4: Implement persisted delivery transitions**

Use conditional SQLite updates:

```text
ready_to_send -> sending -> sent
                         -> send_unknown
ready_to_send -> failed  (only before action)
```

Persist `sending` before invoking `osascript`. A response with
`action_performed=true` but incomplete confirmation becomes `send_unknown` and
disables the scope until reconciliation. Reconciliation queries outbound local
messages by conversation/time/text hash; it never calls the sender.

- [ ] **Step 5: Run fake-backed tests and perform the target-binding gate**

Run: `.venv/bin/python -m pytest tests/wechat/test_accessibility.py tests/wechat/test_store.py -q`

Expected: all tests pass.

Then bind File Transfer Assistant and one explicitly approved direct/group test
target. If stable identity cannot be corroborated beyond display name, keep
arbitrary targets blocked and do not proceed to live broad rollout.

- [ ] **Step 6: Commit**

```bash
git add app/wechat/accessibility.py app/wechat/send_message.applescript app/store.py tests/wechat/test_accessibility.py tests/wechat/test_store.py
git commit -m "feat: deliver WeChat replies through Accessibility"
```

### Task 11: Add WeChat service loops, configuration, and recovery

**Files:**
- Create: `app/wechat/service.py`
- Modify: `app/config.py`
- Modify: `app/cli.py`
- Modify: `tests/test_cli.py`
- Create: `tests/wechat/test_service.py`

- [ ] **Step 1: Write disabled-default and loop-isolation tests**

```python
def test_wechat_threads_are_absent_by_default(fake_thread_factory, settings):
    run_service(settings, thread_factory=fake_thread_factory, wait=lambda: None)
    assert not any("wechat" in thread.name for thread in fake_thread_factory.threads)


def test_wechat_threads_start_only_when_reader_ready(fake_thread_factory, settings, store):
    settings.wechat_reader_enabled = True
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/tmp/acct-1", db_dir="/tmp/acct-1/db_storage",
        app_version="4.1.10.80", self_user_id="self-1",
        capability_status="ready", capability_reason=""
    )
    run_service(settings, thread_factory=fake_thread_factory, wait=lambda: None)
    assert {thread.name for thread in fake_thread_factory.threads} >= {
        "ceo-agent-service-wechat-producer",
        "ceo-agent-service-wechat-consumer",
    }
```

- [ ] **Step 2: Add typed settings and CLI diagnostics**

Add `wechat_reader_enabled`, `wechat_sender_enabled`,
`wechat_poll_interval_seconds`, `wechat_snapshot_dir`, and
`wechat_sqlcipher_bin` to `WorkerSettings`. Add:

```text
ceo-agent wechat-status --db data/auto-reply.sqlite3
ceo-agent wechat-read-recent --configured-account --target-id filehelper --limit 100
ceo-agent wechat-produce-once --db data/auto-reply.sqlite3
ceo-agent wechat-consume-once --db data/auto-reply.sqlite3 --not-send-message
```

`wechat-read-recent` prints normalized redacted metadata by default; require
`--include-text` for the explicitly authorized local verification run.
`--configured-account` resolves the single account persisted by Tutorial and
fails if zero or multiple accounts are configured.

- [ ] **Step 3: Add isolated loops and restart recovery**

`app/wechat/service.py` exposes `run_producer_loop`, `run_consumer_loop`, and
`reconcile_incomplete_deliveries`. `run_service()` adds threads only when the
reader flag is true and persisted capability is `ready`; the sender flag is
checked again for each delivery. Recovery runs before sender startup and turns
unreconciled `sending` rows into `send_unknown`, never `ready_to_send`.

- [ ] **Step 4: Run service tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_service.py tests/test_cli.py tests/test_worker.py -q`

Expected: all tests pass and DingTalk loop names/counts are unchanged under
default configuration.

```bash
git add app/wechat/service.py app/config.py app/cli.py tests/test_cli.py tests/wechat/test_service.py .env.example
git commit -m "feat: run isolated WeChat channel loops"
```

### Task 12: Extract cleaned historical Memory candidates in a one-shot job

**Files:**
- Create: `app/schemas/wechat_memory_candidates.schema.json`
- Create: `app/wechat/memory_import.py`
- Create: `tests/wechat/test_memory_import.py`
- Modify: `app/store.py`
- Modify: `app/cli.py`

- [ ] **Step 1: Write bounded-scope and cleanup tests**

```python
def candidate(statement: str, *, category: str) -> ExtractedMemoryCandidate:
    return ExtractedMemoryCandidate(
        statement=statement,
        category=category,
        confidence=0.9,
        sensitivity="normal",
        source_message_ids=["m1"],
        source_conversation_ids=["c1"],
        source_time_start="2026-07-17T10:00:00+08:00",
        source_time_end="2026-07-17T10:00:00+08:00",
        evidence_excerpt=statement,
        cleanup_notes="test fixture",
    )


def test_import_requires_explicit_bound(fake_reader, importer):
    with pytest.raises(ValueError, match="bounded scope"):
        importer.run(account_id="acct-1", target_ids=[], since="", until="", limit=0)


def test_credentials_never_become_candidates(importer):
    rows = importer.clean_candidates([
        candidate("验证码是 123456", category="fact"),
        candidate("Derek prefers concise status updates", category="preference"),
    ])
    assert [row.statement for row in rows] == ["Derek prefers concise status updates"]
```

- [ ] **Step 2: Add candidate schema and persistence**

Create `wechat_memory_candidates`:

```sql
create table if not exists wechat_memory_candidates (
    id integer primary key autoincrement,
    import_run_id text not null,
    account_id text not null,
    statement text not null,
    edited_statement text not null default '',
    category text not null,
    confidence real not null,
    sensitivity text not null,
    source_conversation_ids_json text not null default '[]',
    source_message_ids_json text not null default '[]',
    source_time_start text not null default '',
    source_time_end text not null default '',
    evidence_excerpt text not null default '',
    cleanup_notes text not null default '',
    status text not null default 'pending',
    reviewer text not null default '',
    reviewed_at text not null default '',
    memory_write_status text not null default '',
    memory_id text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique(import_run_id, statement)
);
```

The JSON schema requires `statement`, `category`, `confidence`, `sensitivity`,
source message IDs, a minimal redacted excerpt, and cleanup notes. It forbids
additional properties. Mirror it with an `ExtractedMemoryCandidate` Pydantic
model in `app/wechat/memory_import.py`; use that model for Codex output
validation and for the deterministic cleanup input.

- [ ] **Step 3: Implement extraction and deterministic cleanup**

`WechatMemoryImporter.run()` requires account plus at least one target/date
bound and `1 <= limit <= 10000`. It reads snapshots, batches bounded messages
to Codex with the candidate schema, then applies deterministic filters for
passwords, verification codes, access tokens, full financial/medical data, raw
transcripts, empty sources, and unsupported categories. Normalize whitespace,
merge exact canonical duplicates, and preserve contradictions as separate
flagged rows.

It writes only `pending` rows and returns counts; it never calls
`memory_write`.

- [ ] **Step 4: Add the manual CLI and run tests**

```text
ceo-agent import-wechat-memory --account-id acct-1 \
  --target-id u-1 --since 2025-01-01 --until 2026-07-17 \
  --limit 1000 --db data/auto-reply.sqlite3
```

Run: `.venv/bin/python -m pytest tests/wechat/test_memory_import.py tests/test_cli.py tests/test_store.py -q`

Expected: tests pass; fake Memory writer call count remains zero.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/wechat_memory_candidates.schema.json app/wechat/memory_import.py app/store.py app/cli.py tests/wechat/test_memory_import.py tests/test_cli.py
git commit -m "feat: extract WeChat memory review candidates"
```

### Task 13: Add the review table and approved-only Memory writer

**Files:**
- Modify: `app/wechat/audit_web.py`
- Modify: `app/audit_web.py`
- Create: `app/wechat/memory_writer.py`
- Create: `tests/wechat/test_memory_review.py`
- Create: `tests/wechat/test_memory_writer.py`

- [ ] **Step 1: Write review transition tests**

```python
def test_pending_candidate_cannot_be_written(writer, store):
    candidate_id = seed_candidate(store, status="pending")
    with pytest.raises(ValueError, match="approved"):
        writer.write(candidate_id)


def test_approved_write_is_idempotent(writer, fake_codex, store):
    candidate_id = seed_candidate(store, status="approved")
    assert writer.write(candidate_id) == "memory-1"
    assert writer.write(candidate_id) == "memory-1"
    assert fake_codex.calls == 1
```

- [ ] **Step 2: Add local review routes and table**

Register:

- `GET /wechat/memory-review` with status/category/sensitivity filters;
- `POST /wechat/memory-review/{id}/approve` requiring the final statement;
- `POST /wechat/memory-review/{id}/reject`;
- `POST /wechat/memory-review/{id}/revoke`;
- `POST /wechat/memory-review/write-approved` with explicit selected IDs.

Render one row per candidate with cleaned statement, category, confidence,
sensitivity, redacted evidence, source time, cleanup notes, editable final
statement, and audit/write status. Support bulk reject; do not provide bulk
approve by default.

- [ ] **Step 3: Implement approved-only Memory orchestration**

`WechatMemoryWriter.write(candidate_id)` reloads the row transactionally,
requires `status="approved"`, returns the existing `memory_id` if already
written, and invokes Codex with a prompt that requires exactly one
`memory_write` call using the final cleaned statement and candidate source time.
It must not pass `user_id`, `graph_id`, or raw evidence. Validate audit tool
events contain successful `memory_write`, extract the returned UUID, then set
`memory_write_status="written"` and `memory_id`.

On ambiguous tool output, persist `memory_write_status="unknown"` and do not
retry automatically.

- [ ] **Step 4: Run review/writer tests and commit**

Run: `.venv/bin/python -m pytest tests/wechat/test_memory_review.py tests/wechat/test_memory_writer.py tests/test_audit_web.py -q`

Expected: all tests pass; pending/rejected/revoked rows never invoke the fake
Memory tool.

```bash
git add app/wechat/audit_web.py app/wechat/memory_writer.py app/audit_web.py tests/wechat/test_memory_review.py tests/wechat/test_memory_writer.py tests/test_audit_web.py
git commit -m "feat: review and write approved WeChat memories"
```

### Task 14: Complete controlled verification and live rollout

**Files:**
- Modify: `docs/agent-installation-runbook.md`
- Modify: `README.md`
- Test: all files changed above

- [ ] **Step 1: Run static and focused verification**

Run:

```bash
.venv/bin/python -m pytest tests/wechat tests/test_store.py tests/test_setup_wizard.py tests/test_audit_web.py tests/test_cli.py tests/test_worker.py -q
.venv/bin/python -m pytest -q
git diff --check
```

Expected: focused suite passes; full tracked suite passes or any unrelated
pre-existing failures are recorded with exact test names before proceeding.

- [ ] **Step 2: Verify latest 100 messages without sending**

With the reader gate `ready`, select File Transfer Assistant or the approved
test direct chat in Tutorial and run:

```bash
.venv/bin/ceo-agent wechat-read-recent \
  --configured-account \
  --target-id filehelper \
  --limit 100 --include-text
```

Manually compare message count, order, direction, timestamp, sender, and text;
run the command twice and verify the second producer scan enqueues zero
duplicates. Confirm source DB file hashes observed by the service did not change
through a service write and no snapshot/key artifact remains.

- [ ] **Step 3: Verify decision dry-run and group trigger**

Keep `CEO_WECHAT_SENDER_ENABLED=0`. Send one direct test message, one ordinary
selected-group message, and one real `@current account` group message. Run
producer/consumer once. Expected:

- direct message: one WeChat task and one audited decision;
- ordinary group message: no task;
- real mention: one WeChat task and one audited decision;
- no external send and no DingTalk worker claim.

- [ ] **Step 4: Verify one exact-once Accessibility send**

Enable the sender only after File Transfer Assistant binding is `verified`.
Send one fixed test reply, confirm visible receipt and matching outbound DB
record, then restart before another send and verify no duplicate. Force one
post-action ambiguous result in a controlled fake/instrumented run and verify
`send_unknown` pauses the target with no automatic retry.

- [ ] **Step 5: Verify one-shot Memory import**

Run a bounded import on an approved test scope. Confirm the review page contains
cleaned pending rows only. Approve one non-sensitive candidate, write it once,
run the writer again, and verify the same Memory ID returns with one tool call.
Reject another candidate and verify it cannot be written.

- [ ] **Step 6: Document operation and commit**

Document Tutorial connection, blocked reasons, selection rules, group `@`
behavior, sender prerequisites, `send_unknown` recovery, Memory review, and
disable/rollback flags.

```bash
git add README.md docs/agent-installation-runbook.md
git commit -m "docs: document WeChat channel operations"
```

- [ ] **Step 7: Restart and verify the live service**

After all runtime commits:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -fsS http://127.0.0.1:8765/tutorial >/dev/null
```

Expected: a new running PID, Tutorial HTTP 200, WeChat capability visible, no
unresolved `failed`, `processing`, `sending`, or `send_unknown` backlog, and no
new unexplained service errors. Broaden from File Transfer Assistant to one
direct contact and one selected group only after this check.

## Completion boundary

The full feature is complete only when both feasibility gates are `ready`, the
latest-100 read proof passes, an exact target can be bound without relying on
display name alone, and one direct plus one `@current account` group flow pass
without duplicate sends. If either gate remains blocked, the correct deliverable
is a functioning Tutorial that reports the blocker, disabled live loops, the
diagnostic evidence, and no claim that arbitrary-contact automatic reply is
operational.
