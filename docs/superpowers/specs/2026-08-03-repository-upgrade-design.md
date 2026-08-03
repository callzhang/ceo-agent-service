# Repository Upgrade Design

## Goal

Let an installed CEO Agent Service detect updates from its configured remote
repository, notify the operator, and apply a verified fast-forward upgrade from
the History page. When local work is uncommitted, the operator can preserve it
on a named branch and commit before upgrading.

## Scope

The first version supports the repository that contains the running service.
It compares local `main` with `origin/main` and only performs fast-forward
updates. It does not merge divergent branches, rebase local commits, open pull
requests, or resolve conflicts.

## Architecture

The feature has three focused components:

1. `RepositoryUpgradeService` inspects Git state, fetches remote metadata, and
   exposes a stable status model. It never restarts the running service itself.
2. The audit web app exposes status, suggestion, preserve, and upgrade APIs. The
   History page renders a compact update banner and refreshes only that banner.
3. A detached updater process performs the transactional upgrade. It persists
   progress before each step so the replacement service can display the same
   operation after restart.

The updater is separate from the launchd-managed service because restarting the
service is part of a successful upgrade. A process owned by the service cannot
reliably supervise its own replacement.

## Configuration

Configuration uses conservative defaults and may be overridden through the
existing service configuration mechanism:

- repository root: the checked-out service repository;
- remote: `origin`;
- target branch: `main`;
- check interval: six hours;
- launchd label: `com.ceo-agent-service.main`;
- focused and full verification commands: project-owned command lists;
- database path: the active Store database.

Remote and branch are explicit configuration values even though the initial UI
does not allow arbitrary user selection. This keeps installations with renamed
remotes deployable without adding multi-repository product scope.

## Update Discovery

Only one check may run at a time. A scheduled check and a manual refresh share
the same lock and state record.

The checker runs `git fetch --prune <remote>`, reads the local and remote commit
IDs, and classifies the repository as:

- `current`: local target equals remote target;
- `update_available`: local target is an ancestor of remote target;
- `local_changes`: an update is available and Git reports tracked or untracked
  non-ignored changes;
- `diverged`: neither commit is an ancestor of the other;
- `check_failed`: fetch or inspection failed.

Ignored files are not considered local source changes. This excludes runtime
databases, credentials, logs, caches, and other installation data already
covered by `.gitignore`.

The persisted status includes timestamps, local and remote commit IDs, commits
behind, a short release summary, dirty paths, and a redacted error. It never
stores environment variables, credentials, remote URLs containing credentials,
or raw command output.

## History Experience

When an update is available, History shows one restrained full-width banner
above the timeline. The banner includes current and available revisions, the
number of commits, the latest summary, last check time, and one primary action.

- Clean repository: `Upgrade`.
- Dirty repository: `Save branch and upgrade`.
- Diverged repository: no action button; show the reason and require manual Git
  reconciliation.
- Failed check: show `Retry check` and the redacted error.

The banner polls a small JSON endpoint. It does not reload the History page or
its timeline. A newly available update also publishes one browser notification
per remote commit ID. Repeated checks do not produce duplicate notifications.

## Preserving Local Work

`Save branch and upgrade` opens a confirmation dialog with:

- an editable branch-name field;
- an editable commit-message field;
- the Git-visible files that will be committed;
- an `Ask Agent` action that proposes both values.

The suggestion Agent receives only repository-relative changed paths, diff
statistics, and a bounded, redacted diff. It runs read-only and returns a branch
name plus a conventional commit message. It cannot run Git writes or start the
upgrade. Suggestions are optional; if suggestion generation fails, the dialog
shows the error and leaves manual entry available.

The server validates both fields immediately before mutation:

- branch name must pass `git check-ref-format --branch`, must not be the target
  branch, and must not already exist locally or on the configured remote;
- commit message must contain non-whitespace text and satisfy the configured
  maximum length;
- the current branch and working tree fingerprint must match the values shown
  in the dialog.

After confirmation, the detached updater creates the exact branch entered by
the operator, stages all Git-visible non-ignored changes, and commits them with
the exact confirmed message. There is no product-owned branch prefix. It then
switches to the configured target branch and continues the upgrade.

If branch creation, staging, or commit fails, the upgrade stops on the original
worktree state. It does not discard changes, delete the preservation branch, or
continue to update `main`.

## Upgrade Transaction

The updater acquires a repository-wide lock and rechecks all preconditions. A
button click is a request, not proof that the previously displayed Git state is
still current.

For a clean repository, the updater performs:

1. Persist `preparing` and the original commit ID.
2. Create a SQLite online backup in the configured runtime backup location.
3. Fetch the configured remote again.
4. Verify local target remains an ancestor of remote target.
5. Fast-forward the local target with `git merge --ff-only`.
6. Synchronize project dependencies using the repository's documented install
   command.
7. Run focused upgrade checks, then the configured full verification suite.
8. Persist `restarting`, then kickstart the launchd service.
9. Poll launchd, the local HTTP health endpoint, and Store readability from the
   new process.
10. Persist `succeeded` with the installed commit and verification summary.

The preservation flow runs its branch-and-commit phase first, then executes the
same clean-repository transaction. The updater never performs `git reset
--hard`, force checkout, force push, automatic merge, or automatic rebase.

## Failure And Rollback

Failures before the fast-forward leave the checkout and service unchanged.
Failures after the fast-forward trigger rollback:

1. Persist `rolling_back` with the failed step and redacted error.
2. Confirm that the target ref still equals the exact commit installed by this
   operation and that the worktree is still updater-owned and clean.
3. Move the target ref from that exact installed commit back to the recorded
   original commit with a compare-and-swap ref update, then restore the clean
   worktree from the restored target ref.
4. Restore dependencies for the original revision.
5. Restart launchd and verify the original service.
6. Persist `rolled_back` or `rollback_failed`.

If either ownership check fails, rollback must not alter Git state. The updater
persists `needs_manual` with both commit IDs and the failed precondition.

The database backup is not automatically restored because a started service may
have accepted new durable work. It is retained for manual recovery and shown in
the operation details. Database schema changes included in upgrades must remain
backward-compatible with the immediately previous release. An upgrade that
cannot satisfy that contract must provide its own explicit migration design.

Unknown process outcome is reconciled read-only on the next service start. The
service inspects the lock, updater PID, current commit, launchd process, and
health endpoint before deciding whether to resume status polling, mark success,
or expose manual recovery. It never launches a second updater merely because
the first HTTP request timed out.

## State Model

The status is stored as one versioned JSON record in `service_state`:

- discovery: `idle`, `checking`, `current`, `update_available`, `local_changes`,
  `diverged`, `check_failed`;
- preservation: `suggesting`, `awaiting_confirmation`, `preserving`;
- upgrade: `preparing`, `updating`, `verifying`, `restarting`, `succeeded`;
- recovery: `rolling_back`, `rolled_back`, `rollback_failed`, `needs_manual`.

Each operation has an ID. Mutating endpoints require that ID and the displayed
repository fingerprint. Repeated requests with the same operation ID return the
existing operation rather than starting another process.

## API Boundaries

The audit web app adds:

- `GET /api/repository-upgrade/status`;
- `POST /api/repository-upgrade/check`;
- `POST /api/repository-upgrade/suggest-preservation`;
- `POST /api/repository-upgrade/start`.

All mutating routes remain local-only under the audit web app's existing access
boundary. Request bodies use explicit Pydantic models. The service passes values
to subprocesses as argument arrays and never interpolates branch names, commit
messages, paths, or revisions into shell strings.

## Testing

Unit tests cover:

- clean, behind, dirty, diverged, fetch-failed, and ignored-file Git states;
- branch and commit-message validation;
- Agent suggestion input redaction and output validation;
- status transitions, operation idempotency, and stale UI fingerprints;
- browser notification deduplication by remote commit;
- updater command construction without shell interpolation;
- rollback before and after fast-forward;
- unknown-outcome reconciliation without duplicate updater launch.

Integration tests use temporary Git repositories with a bare remote. They cover
a clean fast-forward, preservation of tracked and untracked non-ignored files on
an operator-named branch, rejected divergent history, a test failure followed by
rollback, and successful restart verification through injected launchd and
health probes.

UI tests verify that only the update banner refreshes, fields remain editable,
the changed-file list matches the confirmed fingerprint, and repeated clicks do
not start duplicate operations.

## Acceptance Criteria

1. An installation learns about a new `origin/main` revision within six hours
   or immediately after a manual check.
2. One browser notification and one History banner identify the available
   revision without reloading the page.
3. A clean installation upgrades through one confirmed action and proves the
   new launchd process and local health endpoint are running.
4. A dirty installation can preserve all Git-visible, non-ignored changes using
   user-confirmed branch and commit names before upgrading.
5. Agent-generated names are suggestions only and cannot mutate the repository.
6. Divergence, changed confirmation state, failed tests, and concurrent upgrade
   attempts cannot overwrite local work or start an unsafe update.
7. A failed post-fast-forward upgrade either restores and verifies the previous
   service revision or ends in an explicit `rollback_failed`/`needs_manual`
   state with the database backup location available.
