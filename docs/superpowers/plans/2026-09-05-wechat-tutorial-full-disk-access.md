# WeChat Tutorial Full Disk Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Tutorial’s existing **Connect WeChat** action open macOS Full Disk Access before any WeChat-container read, then use the next Connect click to restart the dedicated Reader and prove a real message is readable.

**Architecture:** `app.setup_wizard` owns a durable two-phase state machine backed by existing setup events. A focused macOS onboarding module opens the official settings pane and restarts/waits for the dedicated Reader; `WechatSetupService` remains responsible for account discovery and adds a bounded real-read verification. The React Tutorial stays generic and renders the backend’s phase message without adding a third action.

**Tech Stack:** Python 3.12, FastAPI, SQLite setup events, Unix-socket Reader IPC, launchd, pytest, React 19, TypeScript, Vitest, Vite.

---

## File Map

- Create `app/wechat/permission_onboarding.py`: macOS-only Reader installation checks, Full Disk Access settings launcher, Reader restart, and bounded IPC-health wait.
- Create `tests/wechat/test_permission_onboarding.py`: unit tests for the macOS command boundary and health wait.
- Modify `app/wechat/setup.py`: add a bounded message-read verification after a ready database probe.
- Modify `tests/wechat/test_setup.py`: test readable target selection, successful real-read evidence, and read failure.
- Modify `app/setup_wizard.py`: choose first-phase permission guidance versus second-phase verification from persisted setup events.
- Modify `tests/test_setup_wizard.py`: prove first Connect performs zero Reader access, second Connect restarts before connecting, failures do not loop, and completed connections do not reopen settings.
- Modify `tests/test_audit_web.py`: prove the Tutorial endpoint persists the phase marker and exposes only Check and Connect.
- Create `frontend/src/pages/TutorialPage.test.tsx`: prove the generic Tutorial displays the permission instruction, keeps two WeChat actions, and refreshes after Connect.
- Modify `docs/wechat-channel-operations.md`: document the Tutorial two-click permission workflow and diagnostic states.
- Modify `docs/wechat-channel-and-local-memory-research.md`: record the implemented onboarding boundary and live acceptance evidence.
- Rebuild `app/static/workbench/`: publish the verified React bundle used by the running service.

## Task 0: Isolate the Implementation

**Files:** None.

- [ ] **Step 1: Record the current main commit and dirty files**

Run:

```bash
git rev-parse --short HEAD
git status --short
```

Expected: `HEAD` includes design commit `19079f84`; unrelated user changes such as `app/agent_orchestrator.py`, `docs/runtime-mechanism.md`, `tests/test_agent_orchestrator.py`, `.superpowers/`, and `su-candidate.pdf` are identified and left untouched.

- [ ] **Step 2: Create an isolated worktree**

Run the `using-git-worktrees` skill and create a branch named `codex/wechat-tutorial-full-disk-access` from `19079f84` in a sibling Codex worktree.

Expected: the new worktree is clean and contains the approved design and this plan without copying unrelated dirty files.

- [ ] **Step 3: Run the focused baseline**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_setup.py tests/test_setup_wizard.py tests/test_audit_web.py -q
npm run test --prefix frontend -- --run
```

Expected: baseline tests pass before feature changes. Record any pre-existing failure instead of editing around it.

## Task 1: Add the macOS Permission-Onboarding Boundary

**Files:**
- Create: `app/wechat/permission_onboarding.py`
- Create: `tests/wechat/test_permission_onboarding.py`

- [ ] **Step 1: Write failing tests for installation validation and settings launch**

Create tests with an injected command runner:

```python
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from app.wechat.permission_onboarding import (
    FULL_DISK_ACCESS_SETTINGS_URL,
    PermissionOnboardingError,
    open_full_disk_access_settings,
)


def test_open_full_disk_access_requires_reader_and_launch_agent(tmp_path: Path):
    calls = []
    with pytest.raises(PermissionOnboardingError, match="Reader app is not installed"):
        open_full_disk_access_settings(
            reader_app=tmp_path / "CEO WeChat Reader.app",
            launch_agent=tmp_path / "reader.plist",
            run_command=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
    assert calls == []


def test_open_full_disk_access_uses_official_settings_url(tmp_path: Path):
    reader_app = tmp_path / "CEO WeChat Reader.app"
    launch_agent = tmp_path / "reader.plist"
    reader_app.mkdir()
    launch_agent.write_text("plist", encoding="utf-8")
    calls = []

    def run_command(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, stdout="", stderr="")

    result = open_full_disk_access_settings(
        reader_app=reader_app,
        launch_agent=launch_agent,
        run_command=run_command,
    )

    assert calls[0][0] == ["/usr/bin/open", FULL_DISK_ACCESS_SETTINGS_URL]
    assert result.app_name == "CEO WeChat Reader"
    assert result.settings_opened is True
```

- [ ] **Step 2: Run the launcher tests and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_permission_onboarding.py -q
```

Expected: collection fails because `app.wechat.permission_onboarding` does not exist.

- [ ] **Step 3: Implement the minimal launcher**

Create `permission_onboarding.py` with fixed, non-user-controlled paths and structured output:

```python
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.wechat.reader_ipc import ReaderIpcError


READER_LABEL = "com.stardust.ceo-agent.wechat-reader"
FULL_DISK_ACCESS_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
)


class PermissionOnboardingError(RuntimeError):
    pass


@dataclass(frozen=True)
class PermissionLaunchResult:
    app_name: str
    app_path: str
    settings_opened: bool


def default_reader_app() -> Path:
    return Path.home() / "Applications" / "CEO WeChat Reader.app"


def default_launch_agent() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{READER_LABEL}.plist"


def open_full_disk_access_settings(
    *,
    reader_app: Path | None = None,
    launch_agent: Path | None = None,
    run_command: Callable = subprocess.run,
) -> PermissionLaunchResult:
    app_path = reader_app or default_reader_app()
    plist_path = launch_agent or default_launch_agent()
    if not app_path.is_dir():
        raise PermissionOnboardingError("CEO WeChat Reader app is not installed.")
    if not plist_path.is_file():
        raise PermissionOnboardingError("CEO WeChat Reader LaunchAgent is not installed.")
    completed = run_command(
        ["/usr/bin/open", FULL_DISK_ACCESS_SETTINGS_URL],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise PermissionOnboardingError(
            completed.stderr.strip() or "Could not open Full Disk Access settings."
        )
    return PermissionLaunchResult(
        app_name="CEO WeChat Reader",
        app_path=str(app_path),
        settings_opened=True,
    )
```

- [ ] **Step 4: Write failing tests for Reader restart and bounded health wait**

Append tests that assert command order, retry behavior, and timeout:

```python
def test_restart_reader_waits_until_new_process_is_healthy():
    commands = []
    health_results = iter([
        ReaderIpcError("offline"),
        {"status": "ready"},
    ])

    class Reader:
        def health(self):
            value = next(health_results)
            if isinstance(value, Exception):
                raise value
            return value

    result = restart_reader_and_wait(
        Reader(),
        uid=501,
        run_command=lambda args, **kwargs: (
            commands.append(args) or CompletedProcess(args, 0, stdout="", stderr="")
        ),
        monotonic=iter([0.0, 0.1, 0.2]).__next__,
        pause=lambda _: None,
        timeout_seconds=1.0,
    )

    assert commands == [[
        "/bin/launchctl", "kickstart", "-k",
        "gui/501/com.stardust.ceo-agent.wechat-reader",
    ]]
    assert result["status"] == "ready"


def test_restart_reader_health_timeout_is_bounded():
    class OfflineReader:
        def health(self):
            raise ReaderIpcError("offline")

    with pytest.raises(PermissionOnboardingError, match="did not become ready"):
        restart_reader_and_wait(
            OfflineReader(),
            uid=501,
            run_command=lambda args, **kwargs: CompletedProcess(
                args, 0, stdout="", stderr=""
            ),
            monotonic=iter([0.0, 2.0]).__next__,
            pause=lambda _: None,
            timeout_seconds=1.0,
        )
```

- [ ] **Step 5: Run the restart tests and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_permission_onboarding.py -q
```

Expected: launcher tests pass; restart tests fail because `restart_reader_and_wait` is missing.

- [ ] **Step 6: Implement restart and bounded health wait**

Add:

```python
def restart_reader_and_wait(
    reader,
    *,
    uid: int | None = None,
    run_command: Callable = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
    timeout_seconds: float = 15.0,
) -> dict:
    domain_uid = os.getuid() if uid is None else uid
    command = [
        "/bin/launchctl",
        "kickstart",
        "-k",
        f"gui/{domain_uid}/{READER_LABEL}",
    ]
    completed = run_command(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise PermissionOnboardingError(
            completed.stderr.strip() or "Could not restart CEO WeChat Reader."
        )
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        try:
            health = reader.health()
        except ReaderIpcError:
            health = {}
        if health.get("status") == "ready":
            return health
        pause(0.2)
    raise PermissionOnboardingError("CEO WeChat Reader did not become ready.")
```

- [ ] **Step 7: Run focused tests and commit**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_permission_onboarding.py -q
```

Expected: all permission-onboarding tests pass.

Commit:

```bash
git add app/wechat/permission_onboarding.py tests/wechat/test_permission_onboarding.py
git commit -m "feat: add WeChat permission onboarding boundary"
```

## Task 2: Require a Real Message Read for Connection Completion

**Files:**
- Modify: `app/wechat/setup.py:31-74`
- Modify: `tests/wechat/test_setup.py:7-67`

- [ ] **Step 1: Write failing setup-service tests**

Extend `FakeReader` to record calls and add one direct target plus one message:

```python
class FakeReader:
    def __init__(self, status="ready", targets=None, messages=None):
        self.status = status
        self.targets = (
            [{
                "target_type": "direct",
                "target_id": "contact-1",
                "conversation_id": "conversation-1",
            }]
            if targets is None else targets
        )
        self.messages = [object()] if messages is None else messages
        self.calls = []

    def list_targets(self, account, *, kind, query, limit, offset):
        self.calls.append(("list_targets", kind, limit))
        return [item for item in self.targets if item["target_type"] == kind][:limit]

    def read_messages(self, account, *, conversation_id, conversation_type, limit):
        self.calls.append(("read_messages", conversation_id, conversation_type, limit))
        return self.messages[:limit]


def test_connect_requires_bounded_real_message_read(store):
    reader = FakeReader(
        targets=[{
            "target_type": "direct",
            "target_id": "contact-1",
            "conversation_id": "conversation-1",
        }],
        messages=[object()],
    )
    result = WechatSetupService(
        store, reader, lambda: "ready", accounts_provider=lambda: [_account()]
    ).connect()

    assert result.next_step_status == "ready"
    assert result.evidence["message_read_verified"] is True
    assert ("read_messages", "conversation-1", "direct", 1) in reader.calls


def test_connect_stays_blocked_when_no_message_can_be_read(store):
    reader = FakeReader(targets=[])
    result = WechatSetupService(
        store, reader, lambda: "ready", accounts_provider=lambda: [_account()]
    ).connect()

    assert result.next_step_status == "blocked"
    assert result.evidence["message_read_verified"] is False
```

- [ ] **Step 2: Run the two tests and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest \
  tests/wechat/test_setup.py::test_connect_requires_bounded_real_message_read \
  tests/wechat/test_setup.py::test_connect_stays_blocked_when_no_message_can_be_read -q
```

Expected: both fail because `connect()` does not perform or report a real read.

- [ ] **Step 3: Implement deterministic target selection and read verification**

Add a private method to `WechatSetupService`:

```python
    def _verify_message_read(self, account: WechatAccount) -> bool:
        for kind in ("direct", "group"):
            targets = self.reader.list_targets(
                account, kind=kind, query="", limit=1, offset=0
            )
            if not targets:
                continue
            target = targets[0]
            conversation_id = str(
                target.get("conversation_id") or target.get("target_id") or ""
            )
            if not conversation_id:
                continue
            messages = self.reader.read_messages(
                account,
                conversation_id=conversation_id,
                conversation_type=kind,
                limit=1,
            )
            return bool(messages)
        return False
```

Call it only when `capability.status == "ready"`. Set
`evidence["message_read_verified"]`, and make `next_step_status="blocked"` when
the database probe is ready but no message can be read. Preserve the existing
Sender Accessibility result independently.

- [ ] **Step 4: Add read-exception coverage**

Add this test and import `ReaderIpcError` from `app.wechat.reader_ipc`:

```python
def test_connect_reports_permission_required_without_retry(store):
    class PermissionReader(FakeReader):
        def read_messages(
            self, account, *, conversation_id, conversation_type, limit
        ):
            self.calls.append(("read_messages", conversation_id))
            raise ReaderIpcError(
                "Grant Full Disk Access to CEO WeChat Reader.",
                code="permission_required",
            )

    reader = PermissionReader()
    result = WechatSetupService(
        store, reader, lambda: "ready", accounts_provider=lambda: [_account()]
    ).connect()

    assert result.next_step_status == "blocked"
    assert result.evidence["database_status"] == "permission_required"
    assert result.evidence["message_read_verified"] is False
    assert [call[0] for call in reader.calls].count("read_messages") == 1
```

Catch only `ReaderIpcError` around `_verify_message_read`. Preserve its `code` in
`database_status`, set `message_read_verified=False`, and return without another
Reader call. Other programming errors must continue to fail the action rather
than being converted into permission failures.

- [ ] **Step 5: Run the complete setup-service suite**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_setup.py -q
```

Expected: all tests pass, including existing exact-one-account and Sender Accessibility behavior.

- [ ] **Step 6: Commit**

```bash
git add app/wechat/setup.py tests/wechat/test_setup.py
git commit -m "feat: verify real WeChat reads during setup"
```

## Task 3: Implement the Durable Two-Phase Connect Action

**Files:**
- Modify: `app/setup_wizard.py:964-1014,1092-1137`
- Modify: `tests/test_setup_wizard.py:1360-1410`

- [ ] **Step 1: Write the failing first-phase orchestration test**

Replace the old one-step dispatch expectation with a test that records a real
setup event in the temporary store only after the returned event is inspected:

```python
def test_first_wechat_connect_opens_full_disk_access_without_reader_access(
    monkeypatch, tmp_path: Path
):
    opened = []

    def fail_build_setup(_store):
        raise AssertionError("first phase must not construct the database Reader")

    monkeypatch.setattr("app.wechat.service.build_setup_service", fail_build_setup)
    monkeypatch.setattr(
        "app.setup_wizard.open_full_disk_access_settings",
        lambda: opened.append(True) or type(
            "Result", (), {
                "app_name": "CEO WeChat Reader",
                "app_path": "/Users/test/Applications/CEO WeChat Reader.app",
                "settings_opened": True,
            }
        )(),
    )
    monkeypatch.setenv("CEO_WORKER_DB", str(tmp_path / "worker.sqlite3"))

    event = run_setup_action("connect_wechat", repo_root=tmp_path, env={})

    assert opened == [True]
    assert event.status == "done"
    assert event.next_step_status == "needs_action"
    assert event.evidence["full_disk_access_prompted"] is True
```

- [ ] **Step 2: Run the first-phase test and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest \
  tests/test_setup_wizard.py::test_first_wechat_connect_opens_full_disk_access_without_reader_access -q
```

Expected: fail because the current action constructs the setup service and calls `connect()` immediately.

- [ ] **Step 3: Add phase parsing without a schema change**

Import `open_full_disk_access_settings`, `restart_reader_and_wait`, and
`PermissionOnboardingError`. Add a helper that reads the latest event safely:

```python
def _latest_wechat_setup_evidence(store: AutoReplyStore) -> dict[str, object]:
    events = store.list_setup_wizard_events("wechat_connection", limit=1)
    if not events:
        return {}
    try:
        value = json.loads(str(events[0].get("evidence_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
```

Do not add a new table, environment flag, marker file, or frontend-only phase state.

- [ ] **Step 4: Implement the first phase**

Change `_run_wechat_setup_action` to build the store first and branch before
constructing the setup service:

```python
    previous = _latest_wechat_setup_evidence(store)
    ready_state = store.list_wechat_read_states()
    verified = previous.get("message_read_verified") is True and any(
        row["capability_status"] == "ready" for row in ready_state
    )
    awaiting_permission = previous.get("full_disk_access_prompted") is True
    if not verified and not awaiting_permission:
        launched = open_full_disk_access_settings()
        return SetupWizardEvent(
            step_id="wechat_connection",
            action_id="connect_wechat",
            status="done",
            next_step_status="needs_action",
            summary=(
                "Full Disk Access settings opened. Enable CEO WeChat Reader, "
                "then click Connect WeChat again."
            ),
            evidence={
                "full_disk_access_prompted": True,
                "reader_app": launched.app_name,
                "reader_app_path": _redact_evidence_path(launched.app_path),
            },
        )
```

Use the existing path-redaction helper; never return the user home directory in API evidence.

- [ ] **Step 5: Run the first-phase test and verify GREEN**

Run the test from Step 2.

Expected: pass, with no setup-service construction.

- [ ] **Step 6: Write the failing second-phase ordering test**

Persist a prior event with `full_disk_access_prompted=true`. Inject a fake setup
service and a fake restart function that append to the same call list:

```python
def test_second_wechat_connect_restarts_reader_before_database_verification(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.record_setup_wizard_event(
        step_id="wechat_connection",
        action_id="connect_wechat",
        status="done",
        summary="settings opened",
        evidence_json=json.dumps({"full_disk_access_prompted": True}),
    )
    calls = []

    class FakeSetup:
        reader = object()

        def connect(self):
            calls.append("connect")
            return WechatSetupResult(
                action_id="connect_wechat",
                status="done",
                next_step_status="ready",
                summary="connected",
                evidence={
                    "database_status": "ready",
                    "message_read_verified": True,
                },
            )

    monkeypatch.setenv("CEO_WORKER_DB", str(db_path))
    monkeypatch.setattr(
        "app.wechat.service.build_setup_service", lambda _store: FakeSetup()
    )
    monkeypatch.setattr(
        "app.setup_wizard.restart_reader_and_wait",
        lambda _reader: calls.append("restart") or {"status": "ready"},
    )

    event = run_setup_action("connect_wechat", repo_root=tmp_path, env={})

    assert calls == ["restart", "connect"]
    assert event.next_step_status == "done"
    assert event.evidence["full_disk_access_prompted"] is True
    assert event.evidence["message_read_verified"] is True
```

- [ ] **Step 7: Run the second-phase test and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest \
  tests/test_setup_wizard.py::test_second_wechat_connect_restarts_reader_before_database_verification -q
```

Expected: fail because restart and phase-aware mapping are not implemented.

- [ ] **Step 8: Implement second-phase restart and result mapping**

Construct the setup service, call `restart_reader_and_wait(setup.reader)`, then
call `setup.connect()`. Map `ready` to wizard `done`, retain
`full_disk_access_prompted=true` in evidence, and return `blocked` for
`permission_required`, health timeout, or failed real-read verification. Do not
call either helper in a loop.

- [ ] **Step 9: Add failure and already-connected tests**

Factor the prior-event insert into this test helper:

```python
def _record_wechat_phase(store, **evidence):
    store.record_setup_wizard_event(
        step_id="wechat_connection",
        action_id="connect_wechat",
        status="done",
        summary="phase",
        evidence_json=json.dumps(evidence),
    )
```

Add a missing-permission test whose fake `connect()` returns
`database_status="permission_required"` and `message_read_verified=False`:

```python
assert settings_launch_count == 0
assert restart_count == 1
assert connect_count == 1
assert event.next_step_status == "blocked"
assert event.evidence["full_disk_access_prompted"] is True
```

Add an already-connected test after inserting both a ready WeChat read state and
an event with `database_status="ready"` and `message_read_verified=True`:

```python
event = run_setup_action("connect_wechat", repo_root=tmp_path, env={})
assert settings_launch_count == 0
assert restart_count == 0
assert connect_count == 1
assert event.next_step_status == "done"
```

The already-connected branch performs one direct verification through
`setup.connect()` but does not reopen System Settings or restart a healthy
Reader.

- [ ] **Step 10: Run the complete wizard suite and commit**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: all tests pass and the action definition still contains only Check and Connect.

Commit:

```bash
git add app/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: guide WeChat Full Disk Access in Tutorial"
```

## Task 4: Verify API Persistence and Generic Tutorial Rendering

**Files:**
- Modify: `tests/test_audit_web.py:2910-2975,3220-3265`
- Create: `frontend/src/pages/TutorialPage.test.tsx`

- [ ] **Step 1: Write a failing API persistence test**

Add a route test whose fake action returns the permission phase:

```python
def test_tutorial_wechat_connect_persists_permission_phase(monkeypatch, tmp_path: Path):
    def fake_run(action_id, *, repo_root, env):
        del repo_root, env
        assert action_id == "connect_wechat"
        return SetupWizardEvent(
            step_id="wechat_connection",
            action_id="connect_wechat",
            status="done",
            next_step_status="needs_action",
            summary="Enable CEO WeChat Reader, then click Connect WeChat again.",
            evidence={"full_disk_access_prompted": True},
        )

    monkeypatch.setattr(audit_web_module, "run_setup_action", fake_run)
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_setup_wizard_step(step_id="preflight", status="done", summary="ok")
    client = loopback_test_client(create_audit_app(db_path))

    response = client.post("/tutorial/run/connect_wechat")

    assert response.status_code == 200
    assert response.json()["next_step_status"] == "needs_action"
    event = AutoReplyStore(db_path).list_setup_wizard_events("wechat_connection")[0]
    assert json.loads(event["evidence_json"])["full_disk_access_prompted"] is True
    assert AutoReplyStore(db_path).get_setup_wizard_step("wechat_connection")["status"] == "needs_action"
```

- [ ] **Step 2: Run the API test and verify current behavior**

Run the new test. If it passes because the generic route already persists the
model correctly, retain it as contract coverage and do not change production API
code. If it fails, make the smallest correction in `app/audit_web.py` and rerun.

- [ ] **Step 3: Write the React Tutorial test**

Mock `getTutorial` with one WeChat step containing exactly two actions, mock
`runTutorialAction` with the permission message, click Connect, and assert:

```tsx
expect(screen.getByRole("button", { name: "Check" })).toBeInTheDocument();
expect(screen.getByRole("button", { name: "Connect WeChat" })).toBeInTheDocument();
expect(screen.queryByRole("button", { name: /verify/i })).not.toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "Connect WeChat" }));
expect(await screen.findByText(/Enable CEO WeChat Reader/)).toBeInTheDocument();
expect(runTutorialAction).toHaveBeenCalledWith("connect_wechat");
```

- [ ] **Step 4: Run the frontend test and verify RED or contract coverage**

Run:

```bash
npm run test --prefix frontend -- --run src/pages/TutorialPage.test.tsx
```

Expected: pass without production React changes if the generic renderer already
meets the approved design. If test setup exposes a missing accessible label or
refresh behavior, make only that focused correction in `TutorialPage.tsx` and
rerun.

- [ ] **Step 5: Run API and frontend suites and commit test contract**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_audit_web.py -q
npm run test --prefix frontend -- --run
```

Expected: all tests pass.

Commit only files changed by this task:

```bash
git add tests/test_audit_web.py frontend/src/pages/TutorialPage.test.tsx frontend/src/pages/TutorialPage.tsx
git commit -m "test: cover WeChat permission tutorial flow"
```

Omit `TutorialPage.tsx` from `git add` when no production frontend change was needed.

## Task 5: Documentation, Build, Merge, Reload, and Live Acceptance

**Files:**
- Modify: `docs/wechat-channel-operations.md`
- Modify: `docs/wechat-channel-and-local-memory-research.md`
- Rebuild: `app/static/workbench/`

- [ ] **Step 1: Update operator documentation after tests are green**

Document the exact states:

```text
First Connect: settings_opened / needs_action / zero database reads
Second Connect: Reader restarted / real message read / done
Permission missing: blocked / no retry loop
Check: read-only status / no settings launch / no Reader restart
```

State explicitly that macOS authentication remains a human action and that only
`CEO WeChat Reader.app` receives Full Disk Access.

- [ ] **Step 2: Run the full static verification**

Run:

```bash
npm test
npm run build:workbench
git diff --check
```

Expected: Ruff, the full Python suite, the full frontend suite, and the Workbench build pass with zero failures.

- [ ] **Step 3: Commit docs and built assets**

```bash
git add docs/wechat-channel-operations.md \
  docs/wechat-channel-and-local-memory-research.md \
  app/static/workbench
git commit -m "docs: publish WeChat permission onboarding"
```

- [ ] **Step 4: Review and merge the isolated branch**

Run the `requesting-code-review` and `finishing-a-development-branch` skills.
Verify the diff contains only the files named in this plan. Merge into `main`
without staging or overwriting unrelated main-worktree changes.

- [ ] **Step 5: Check runtime resumability before restart**

Read `docs/architecture.md` and `docs/runtime-mechanism.md`. Query the live worker
database for reply tasks, work-summary inputs, meeting jobs, and persisted
external actions. Confirm processing rows are resumable and external actions are
idempotent before terminating the current main process.

- [ ] **Step 6: Restart the main service and Reader**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl kickstart -k gui/$(id -u)/com.stardust.ceo-agent.wechat-reader
```

Then verify both launchd jobs report `state = running`, new PIDs, and no immediate exit.

- [ ] **Step 7: Exercise the first Connect phase live**

Because this Mac already has Reader Full Disk Access, temporarily testing a fresh
permission state must not revoke the working TCC grant. Instead, use an isolated
temporary worker database with no WeChat setup events and invoke the local
Tutorial action while keeping the installed Reader permission unchanged.

Verify:

- the Full Disk Access pane opens;
- the event is `needs_action` with `full_disk_access_prompted=true`;
- Reader logs show zero database requests for that first action;
- the production worker database is unchanged.

- [ ] **Step 8: Exercise the second Connect phase live**

Use the normal production Tutorial Connect action after confirming the first
phase event is persisted. Verify:

- Reader PID changes before the database probe;
- one account is discovered;
- a bounded real message read succeeds;
- the Tutorial step becomes `done`;
- Reader, Sender, and WeChat worker status remains healthy.

- [ ] **Step 9: Verify the original symptom is absent**

Record the current time, restart the Reader twice, and run one real message read
after each restart. Query TCC logs from the recorded timestamp for:

```text
bundle id: com.stardust.ceo-agent.wechat-reader
event: AUTHREQ_PROMPTING
```

Expected: zero new prompt events. Confirm in System Settings that
`CEO WeChat Reader` is on while `python3.12` remains off.

- [ ] **Step 10: Verify queues and clean task-owned temporary files**

Confirm no new unresolved `failed`, `processing`, `sending`, or `send_unknown`
rows were introduced by the restart or Tutorial action. Delete only temporary
databases and directories created by this implementation, using explicit paths.

- [ ] **Step 11: Final evidence report**

Report:

- implementation commit IDs;
- focused and full test counts;
- Workbench build result;
- main service and Reader post-restart PIDs;
- first-phase zero-read evidence;
- second-phase real-read evidence;
- post-restart TCC prompt count;
- queue reconciliation result;
- any unrelated dirty files preserved.
