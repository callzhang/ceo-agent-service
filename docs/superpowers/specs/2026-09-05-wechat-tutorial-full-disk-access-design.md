# WeChat Tutorial Full Disk Access Design

## Goal

Make the existing Tutorial **Connect WeChat** action guide the user through the
one-time macOS Full Disk Access grant required by `CEO WeChat Reader.app`, then
complete a real database connection check. Tutorial keeps exactly two actions:
**Check** and **Connect WeChat**.

The flow must not grant storage access to Miniforge Python or the main
`ceo-agent-service` process. macOS still requires the user to authenticate and
enable the Reader in System Settings; the service cannot silently bypass that
boundary.

## Current Problem

`Connect WeChat` currently starts by discovering accounts and probing the WeChat
database. Without Full Disk Access, that access causes macOS to show the inline
“access data from other apps” dialog. The dialog grants only process-scoped App
Data access, so it appears again after a Reader restart.

The installer now explains the Full Disk Access requirement, but the Tutorial
does not initiate or track the permission workflow. A non-technical user can
therefore mistake the temporary dialog for successful installation.

## Chosen Flow

### First Connect

Before account discovery or database probing, the backend checks the latest
persisted `connect_wechat` setup event.

When no unfinished Full Disk Access guidance event exists, the backend:

1. verifies that the dedicated Reader application and LaunchAgent definition
   exist;
2. opens **System Settings → Privacy & Security → Full Disk Access** with the
   macOS settings URL;
3. records a successful setup action whose next step is `needs_action`;
4. returns evidence containing `full_disk_access_prompted=true`, the application
   display name, and a redacted application path;
5. tells the user to enable **CEO WeChat Reader** and then click the same
   **Connect WeChat** button again.

This phase must not call `discover_accounts`, `probe`, `read_messages`, or any
other operation that touches the WeChat container.

### Second Connect

When the latest unfinished `connect_wechat` event contains
`full_disk_access_prompted=true`, the backend treats the next click as permission
verification:

1. restart `com.stardust.ceo-agent.wechat-reader` so the Reader receives the new
   TCC permission in a fresh process;
2. wait for the Reader IPC health endpoint with a bounded timeout;
3. discover local WeChat accounts;
4. require exactly one account under the current automatic-selection contract;
5. probe the database and detect the current WeChat username;
6. persist the ready account state;
7. run one bounded message query to prove that real message data is readable;
8. return `done` only when all checks succeed.

No new button or modal is added. The existing action message explains which
phase is active.

### Already Connected

If the persisted WeChat account is ready and the Reader has a successful
post-restart read verification, a later **Connect WeChat** click verifies the
current connection directly instead of reopening System Settings.

## State Model

Use the existing `setup_wizard_events` table; no schema migration is needed.
The latest `connect_wechat` event is the durable phase marker.

- `next_step_status=needs_action` and
  `evidence.full_disk_access_prompted=true`: System Settings was opened and the
  next Connect click should verify permission.
- `next_step_status=done` and `evidence.database_status=ready` plus
  `evidence.message_read_verified=true`: the connection is complete.
- `next_step_status=blocked`: the Reader is installed but permission or account
  verification failed.

A new first-phase event supersedes an older failed or completed guidance event
only when the stored ready connection is absent. Repeated clicks while the
first-phase request is being processed must not open multiple System Settings
windows.

## Component Boundaries

### Permission launcher

Add a small macOS-specific function responsible only for opening the Full Disk
Access settings pane. It receives the command runner as a dependency so tests do
not open System Settings. It returns a structured failure if `/usr/bin/open`
cannot launch the pane.

The launcher does not click security controls, enter credentials, inspect the
TCC database, install a configuration profile, or claim that permission was
granted.

### Tutorial orchestration

`app.setup_wizard` owns the two-phase workflow because it already owns setup
events and action dispatch. It chooses between permission guidance and connection
verification before invoking the existing WeChat setup service.

### WeChat setup service

`WechatSetupService` continues to own account discovery, capability probing,
username detection, and persistence. Its connection result will include a
bounded real-read verification result. It does not open System Settings or query
wizard history.

### Frontend

The current `TutorialPage` action renderer remains generic. It displays the
backend message and refreshed step status; no WeChat-specific frontend state
machine is introduced.

## Error Handling

- Reader application missing: return `failed` with an installation-specific
  explanation; do not open System Settings.
- Settings pane failed to open: return `failed`; do not advance the phase marker.
- Reader restart or IPC health timeout: return `blocked` with the actual Reader
  health evidence.
- Full Disk Access not enabled: a verification attempt may receive
  `permission_required`; return `blocked` and instruct the user to enable the
  Reader. Do not retry in a loop and do not reopen the pane automatically during
  that request.
- Multiple accounts: preserve the existing exact-one-account requirement and
  return the account count without reading messages.
- Database probe succeeds but message read fails: remain `blocked`; a probe alone
  is not connection completion.

No fallback to public Python, direct database access from the main service, UI
automation, AppleScript clicking, or temporary App Data approval is allowed.

## Tests

Add focused regression coverage before implementation:

1. The first Connect opens the Full Disk Access pane and does not construct or
   call a database Reader.
2. A successful first phase persists `full_disk_access_prompted=true` and leaves
   the step at `needs_action`.
3. The second Connect restarts the Reader before account discovery and performs
   a bounded real message read before returning `done`.
4. Missing permission returns `blocked` without a retry loop or a second settings
   launch in the same request.
5. An already verified connection does not reopen System Settings.
6. Check remains read-only and never opens System Settings or restarts Reader.
7. Existing Tutorial API and React rendering tests continue to pass without a
   third WeChat action.

## Acceptance Criteria

- Tutorial visibly contains only **Check** and **Connect WeChat** for WeChat.
- On a fresh setup, the first Connect opens the correct Full Disk Access pane and
  performs zero WeChat-container reads.
- After the user enables `CEO WeChat Reader`, the next Connect restarts the
  Reader and proves a real message can be read.
- Two subsequent Reader restarts and reads create no new
  `AUTHREQ_PROMPTING` event for `com.stardust.ceo-agent.wechat-reader`.
- `python3.12` and the main service remain disabled in Full Disk Access.
- Focused backend, API, and frontend tests pass; the built Workbench assets are
  refreshed before live browser acceptance.

## Out of Scope

- Silent or programmatic Full Disk Access grants.
- MDM/PPPC profile deployment.
- Sender Accessibility permission changes.
- WeChat reply-scope selection, which remains in Settings.
- Changing message polling or automatic-reply timing.
