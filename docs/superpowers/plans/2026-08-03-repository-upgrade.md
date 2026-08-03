# Repository Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect fast-forward updates for the installed CEO Agent Service, notify the operator, and provide a verified one-click upgrade that can first preserve local work on a user-named branch with a user-confirmed commit message.

**Architecture:** A pure Git/state module classifies the repository and persists a versioned operation record. A read-only Codex helper may suggest branch and commit names, while a detached updater owns all Git mutation, backup, verification, restart, and rollback. A small FastAPI route module and History banner expose the workflow without reloading the page; the service runner adds only the periodic check loop.

**Tech Stack:** Python 3.11, Pydantic 2, FastAPI, SQLite `service_state`, native `git`, native `codex exec`, launchd, pytest, temporary bare Git repositories.

---

## File Structure

- Create `app/repository_upgrade.py`: status models, Git inspection, persistence, locking, fingerprinting, and scheduled checks.
- Create `app/repository_upgrade_agent.py`: bounded/redacted suggestion prompt and read-only Codex execution.
- Create `app/repository_updater.py`: detached preservation, upgrade, verification, restart, reconciliation, and rollback transaction.
- Create `app/repository_upgrade_web.py`: FastAPI routes plus History banner HTML/JavaScript.
- Create `app/schemas/repository_upgrade_suggestion.schema.json`: strict Agent suggestion output.
- Modify `app/audit_web.py`: insert the banner mount point and register upgrade routes.
- Modify `app/cli.py`: configuration, periodic checker component, and updater command entry point.
- Modify `app/config.py`: repository-upgrade environment accessors.
- Modify `app/database_backup.py`: reusable named online backup primitive.
- Modify `README.md`: operator configuration and upgrade behavior.
- Create `tests/test_repository_upgrade.py`: Git classification, persistence, locks, and scheduler tests.
- Create `tests/test_repository_upgrade_agent.py`: suggestion input/output tests.
- Create `tests/test_repository_updater.py`: temporary-repository transaction and rollback tests.
- Create `tests/test_repository_upgrade_web.py`: routes, trust boundary, banner, and async UI tests.
- Modify `tests/test_cli.py`: service component and updater CLI wiring tests.
- Modify `tests/test_database_backup.py`: named pre-upgrade backup tests.

Do not modify stale reply-task recovery or Agent lease behavior in this feature.

### Task 1: Repository Status And Persistent Operation Model

**Files:**
- Create: `app/repository_upgrade.py`
- Create: `tests/test_repository_upgrade.py`

- [ ] **Step 1: Write failing Git-state tests**

Create temporary repositories with a bare `origin` and cover `current`, `update_available`, `local_changes`, `diverged`, and `check_failed`. Include tracked, untracked non-ignored, and ignored files.

```python
def test_inspect_reports_dirty_fast_forward_update(git_fixture):
    git_fixture.push_remote_commit("remote change")
    (git_fixture.local / "notes.txt").write_text("local draft")

    snapshot = RepositoryUpgradeService(git_fixture.local).check()

    assert snapshot.status == UpgradeStatus.LOCAL_CHANGES
    assert snapshot.commits_behind == 1
    assert snapshot.dirty_paths == ("notes.txt",)
    assert snapshot.local_commit != snapshot.remote_commit


def test_inspect_ignores_gitignored_runtime_files(git_fixture):
    git_fixture.push_remote_commit("remote change")
    (git_fixture.local / "data" / "runtime.sqlite3").write_text("runtime")

    snapshot = RepositoryUpgradeService(git_fixture.local).check()

    assert snapshot.status == UpgradeStatus.UPDATE_AVAILABLE
    assert snapshot.dirty_paths == ()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_repository_upgrade.py -q`

Expected: collection fails because `app.repository_upgrade` does not exist.

- [ ] **Step 3: Implement models, argument-safe Git runner, and classifier**

Implement these public contracts and keep subprocess use inside `GitRepository`:

```python
class UpgradeStatus(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    LOCAL_CHANGES = "local_changes"
    DIVERGED = "diverged"
    CHECK_FAILED = "check_failed"


class RepositorySnapshot(BaseModel):
    status: UpgradeStatus
    checked_at: datetime
    local_commit: str = ""
    remote_commit: str = ""
    commits_behind: int = 0
    release_summary: str = ""
    dirty_paths: tuple[str, ...] = ()
    fingerprint: str = ""
    error: str = ""


class GitRepository:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *args: str, check: bool = True) -> CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.root, text=True,
            capture_output=True, check=check,
        )

    def fetch(self, remote: str) -> None:
        self.run("fetch", "--prune", remote)

    def revision(self, ref: str) -> str:
        return self.run("rev-parse", "--verify", ref).stdout.strip()

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.run("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def visible_changes(self) -> tuple[str, ...]:
        raw = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        return tuple(sorted(parse_porcelain_paths(raw)))

    def fingerprint(self, local: str, remote: str, paths: tuple[str, ...]) -> str:
        branch = self.run("branch", "--show-current").stdout.strip()
        payload = json.dumps([branch, local, remote, *paths], separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()


class RepositoryUpgradeService:
    def check(self) -> RepositorySnapshot:
        checked_at = datetime.now(timezone.utc)
        try:
            self.repo.fetch(self.remote)
            local = self.repo.revision(self.branch)
            remote = self.repo.revision(f"{self.remote}/{self.branch}")
            paths = self.repo.visible_changes()
            if local == remote:
                status = UpgradeStatus.CURRENT
            elif self.repo.is_ancestor(local, remote):
                status = UpgradeStatus.LOCAL_CHANGES if paths else UpgradeStatus.UPDATE_AVAILABLE
            else:
                status = UpgradeStatus.DIVERGED
            return self._snapshot(status, checked_at, local, remote, paths)
        except (OSError, subprocess.SubprocessError) as exc:
            return RepositorySnapshot(
                status=UpgradeStatus.CHECK_FAILED,
                checked_at=checked_at,
                error=redact_upgrade_error(str(exc)),
            )
```

Use `git status --porcelain=v1 -z --untracked-files=all`, parse NUL-delimited records, and sort repository-relative paths. Compute the fingerprint from local commit, remote commit, current branch, and status records. Never call a shell.

- [ ] **Step 4: Add persisted state and operation-id tests**

```python
def test_state_round_trip_is_versioned(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    state = RepositoryUpgradeState.for_snapshot(snapshot, operation_id="op-1")

    save_repository_upgrade_state(store, state)

    assert load_repository_upgrade_state(store) == state


def test_same_operation_id_does_not_claim_twice(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert claim_repository_upgrade_operation(store, "op-1", snapshot.fingerprint)
    assert not claim_repository_upgrade_operation(store, "op-1", snapshot.fingerprint)
```

Persist one JSON object under `repository_upgrade_state:v1`. Use a separate atomic reservation row or lock file under `.git/ceo-agent-upgrade.lock`; do not infer exclusivity from UI state alone.

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_repository_upgrade.py tests/test_store.py -q`

Expected: PASS.

Commit:

```bash
git add app/repository_upgrade.py tests/test_repository_upgrade.py
git commit -m "feat: inspect repository upgrade state"
```

### Task 2: Read-Only Agent Suggestions

**Files:**
- Create: `app/repository_upgrade_agent.py`
- Create: `app/schemas/repository_upgrade_suggestion.schema.json`
- Create: `tests/test_repository_upgrade_agent.py`

- [ ] **Step 1: Write failing suggestion safety tests**

```python
def test_suggestion_prompt_uses_paths_stats_and_redacted_bounded_diff(tmp_path):
    change = RepositoryChangeSummary(
        paths=("app/history.py", ".env.local"),
        diff_stat="2 files changed, 8 insertions(+)",
        diff="+API_TOKEN=secret-value\n+def render_history(): pass",
    )

    prompt = build_preservation_suggestion_prompt(change)

    assert "secret-value" not in prompt
    assert "[redacted credential line]" in prompt
    assert len(prompt.encode()) <= SUGGESTION_PROMPT_MAX_BYTES


def test_agent_command_is_read_only(tmp_path, recording_executor):
    agent = RepositoryUpgradeSuggestionAgent(tmp_path, executor=recording_executor)
    agent.suggest(change)

    command = recording_executor.commands[0]
    assert 'approval_policy="never"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_repository_upgrade_agent.py -q`

Expected: collection fails because the module and schema do not exist.

- [ ] **Step 3: Implement strict suggestion schema and Agent**

The JSON schema permits only:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["branch_name", "commit_message"],
  "properties": {
    "branch_name": {"type": "string", "minLength": 1, "maxLength": 120},
    "commit_message": {"type": "string", "minLength": 1, "maxLength": 200}
  }
}
```

Implement:

```python
class PreservationSuggestion(BaseModel):
    branch_name: str = Field(min_length=1, max_length=120)
    commit_message: str = Field(min_length=1, max_length=200)


class RepositoryUpgradeSuggestionAgent:
    def suggest(self, change: RepositoryChangeSummary) -> PreservationSuggestion:
        command = self.runner.build_command(
            prompt=build_preservation_suggestion_prompt(change),
            session_id=None,
            output_schema_path=SUGGESTION_SCHEMA_PATH,
            approval_policy="never",
            use_approval_bypass=False,
            preserve_native_model_config=True,
            developer_instructions=SUGGESTION_DEVELOPER_INSTRUCTIONS,
        )
        result = self.executor(
            command,
            prompt=build_preservation_suggestion_prompt(change),
            env=self.runner.build_env(preserve_local_cli_auth=True),
            total_timeout_seconds=120,
            idle_timeout_seconds=60,
        )
        if result.returncode != 0 or result.timed_out:
            raise SuggestionError("suggestion_agent_failed")
        suggestion = PreservationSuggestion.model_validate_json(
            extract_final_agent_message(result.stdout)
        )
        return suggestion
```

Replace any diff line for which `contains_credential(line)` is true. Truncate by UTF-8 bytes after redaction. Validate the returned branch with `git check-ref-format --branch` in Task 3 before showing it as usable; malformed Agent output is an explicit suggestion error, not an automatic branch choice.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest tests/test_repository_upgrade_agent.py tests/test_codex_runner.py -q`

Expected: PASS.

Commit:

```bash
git add app/repository_upgrade_agent.py app/schemas/repository_upgrade_suggestion.schema.json tests/test_repository_upgrade_agent.py
git commit -m "feat: suggest upgrade preservation metadata"
```

### Task 3: Named Online Backup And Detached Upgrade Transaction

**Files:**
- Modify: `app/database_backup.py`
- Create: `app/repository_updater.py`
- Create: `tests/test_repository_updater.py`
- Modify: `tests/test_database_backup.py`

- [ ] **Step 1: Write failing named-backup and transaction tests**

```python
def test_create_named_database_backup_is_consistent(tmp_path):
    source = seed_database(tmp_path / "worker.sqlite3")
    destination = tmp_path / "backups" / "before-upgrade-op-1.sqlite3"

    create_database_backup(source, destination)

    with sqlite3.connect(destination) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_clean_upgrade_fast_forwards_verifies_and_restarts(upgrade_fixture):
    result = upgrade_fixture.executor.execute(upgrade_fixture.operation)

    assert result.status == "succeeded"
    assert upgrade_fixture.local_head == upgrade_fixture.remote_head
    assert upgrade_fixture.calls[-2:] == ["restart", "health"]


def test_preservation_uses_exact_user_branch_and_message(upgrade_fixture):
    operation = upgrade_fixture.operation.model_copy(update={
        "branch_name": "fix/local-history-work",
        "commit_message": "fix: preserve local history work",
    })

    upgrade_fixture.executor.execute(operation)

    assert upgrade_fixture.commit_subject("fix/local-history-work") == (
        "fix: preserve local history work"
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_database_backup.py tests/test_repository_updater.py -q`

Expected: FAIL because named backup and updater contracts do not exist.

- [ ] **Step 3: Extract reusable database backup primitive**

```python
def create_database_backup(db_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with sqlite3.connect(db_path) as source, sqlite3.connect(temporary) as target:
            source.execute("pragma busy_timeout = 30000")
            source.backup(target)
            target.execute("pragma journal_mode = delete")
            if target.execute("pragma integrity_check").fetchone() != ("ok",):
                raise RuntimeError("database backup integrity check failed")
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
```

Refactor `backup_database_if_due` to call this function without changing daily retention behavior.

- [ ] **Step 4: Implement operation validation and preservation**

```python
class UpgradeOperation(BaseModel):
    operation_id: str
    expected_fingerprint: str
    original_commit: str
    target_commit: str
    branch_name: str = ""
    commit_message: str = ""


def validate_preservation(repo: GitRepository, operation: UpgradeOperation) -> None:
    repo.run("check-ref-format", "--branch", operation.branch_name)
    if repo.ref_exists(operation.branch_name) or repo.remote_ref_exists(operation.branch_name):
        raise UpgradePreconditionError("branch_already_exists")
    if not operation.commit_message.strip():
        raise UpgradePreconditionError("commit_message_required")
```

Preservation order is: recheck fingerprint, create exact branch, `git add --all`, verify the staged path set equals the confirmed Git-visible path set, commit exact message, switch back to configured target branch. Any failure stops before target-branch mutation and preserves the new branch/worktree state for the operator.

- [ ] **Step 5: Implement upgrade, rollback, and startup reconciliation**

Use injected callables for dependency sync, tests, launchd restart, launchd PID readback, local HTTP health, and Store readability. The production executor uses argument arrays:

```python
UPGRADE_STEPS = (
    "preparing", "updating", "verifying", "restarting", "succeeded"
)


class RepositoryUpdater:
    def execute(self, operation: UpgradeOperation) -> RepositoryUpgradeState:
        with self.lock.acquire(operation.operation_id):
            self._recheck_preconditions(operation)
            self._backup_database(operation)
            self._fetch_and_fast_forward(operation)
            self._sync_dependencies()
            self._run_verification()
            previous_pid = self.launchd.pid()
            self.launchd.restart()
            self._verify_new_service(previous_pid)
            return self.state.succeed(operation)
```

On post-fast-forward failure, compare-and-swap `refs/heads/<target>` from the installed commit to the original commit, restore the clean worktree, synchronize old dependencies, restart, and verify. If current ref or fingerprint changed, persist `needs_manual` and do not alter Git state.

Reconciliation reads the operation lock, updater PID, current ref, launchd PID, HTTP health, and Store. It never starts another updater. It marks success only when the installed target and replacement service are verified; otherwise it reports `needs_manual` or continues polling a live updater.

- [ ] **Step 6: Add duplicate-click, divergence, rollback, and unknown-outcome tests**

```python
def test_failed_verification_rolls_back_exact_installed_commit(upgrade_fixture):
    upgrade_fixture.verification.fail_with("focused tests failed")
    original = upgrade_fixture.local_head
    with pytest.raises(UpgradeFailed):
        upgrade_fixture.executor.execute(upgrade_fixture.operation)
    assert upgrade_fixture.local_head == original
    assert upgrade_fixture.state.status == "rolled_back"


def test_changed_ref_blocks_rollback_and_preserves_worktree(upgrade_fixture):
    upgrade_fixture.verification.change_ref_then_fail()
    with pytest.raises(UpgradeNeedsManual):
        upgrade_fixture.executor.execute(upgrade_fixture.operation)
    assert upgrade_fixture.state.status == "needs_manual"
    assert upgrade_fixture.verification.changed_ref == upgrade_fixture.local_head


def test_live_updater_reconciliation_does_not_spawn_duplicate(upgrade_fixture):
    upgrade_fixture.process_probe.live_pids.add(43210)
    state = upgrade_fixture.executor.reconcile(
        upgrade_fixture.operation.model_copy(update={"updater_pid": 43210})
    )
    assert state.status == "updating"
    assert upgrade_fixture.process_probe.spawned == []


def test_diverged_target_never_runs_merge(upgrade_fixture):
    upgrade_fixture.diverge_local_and_remote()
    with pytest.raises(UpgradePreconditionError, match="diverged"):
        upgrade_fixture.executor.execute(upgrade_fixture.operation)
    assert not any(call[:2] == ("merge", "--ff-only") for call in upgrade_fixture.git_calls)
```

- [ ] **Step 7: Run tests and commit**

Run: `.venv/bin/pytest tests/test_database_backup.py tests/test_repository_updater.py -q`

Expected: PASS.

Commit:

```bash
git add app/database_backup.py app/repository_updater.py tests/test_database_backup.py tests/test_repository_updater.py
git commit -m "feat: execute verified repository upgrades"
```

### Task 4: Local Web API And Async History Banner

**Files:**
- Create: `app/repository_upgrade_web.py`
- Modify: `app/audit_web.py`
- Create: `tests/test_repository_upgrade_web.py`

- [ ] **Step 1: Write failing API and rendering tests**

```python
def test_history_mounts_upgrade_banner_without_page_refresh(store):
    html = render_attempt_list(store)
    assert 'id="repository-upgrade-banner"' in html
    assert 'fetch("/api/repository-upgrade/status"' in html
    assert "window.location.reload" not in upgrade_script(html)


def test_dirty_start_requires_confirmed_branch_message_and_fingerprint(client):
    response = client.post("/api/repository-upgrade/start", json={
        "operation_id": "op-1",
        "fingerprint": "stale",
        "branch_name": "fix/local-work",
        "commit_message": "fix: preserve local work",
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "repository_state_changed"
```

Also cover loopback/trusted mutation enforcement, suggestion failure, invalid/ref-existing branch, missing commit message, duplicate operation ID, and notification deduplication by remote commit.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_repository_upgrade_web.py -q`

Expected: FAIL because routes and banner are absent.

- [ ] **Step 3: Implement route registration with dependency factories**

```python
def register_repository_upgrade_routes(
    app: FastAPI,
    *,
    store_factory: Callable[[], AutoReplyStore],
    service_factory: Callable[[], RepositoryUpgradeService],
    suggestion_factory: Callable[[], RepositoryUpgradeSuggestionAgent],
    updater_launcher: Callable[[UpgradeOperation], int],
) -> None:
    @app.get("/api/repository-upgrade/status")
    def status() -> dict[str, object]:
        return service_factory().current_state().model_dump(mode="json")

    @app.post("/api/repository-upgrade/check", status_code=202)
    def check() -> dict[str, object]:
        return service_factory().check_and_persist(notify=True).model_dump(mode="json")

    @app.post("/api/repository-upgrade/suggest-preservation")
    def suggest(request: SuggestPreservationRequest) -> dict[str, str]:
        service = service_factory()
        service.require_fingerprint(request.fingerprint)
        return suggestion_factory().suggest(service.change_summary()).model_dump()

    @app.post("/api/repository-upgrade/start", status_code=202)
    def start(request: StartUpgradeRequest) -> dict[str, object]:
        operation = service_factory().prepare_operation(request)
        pid = updater_launcher(operation)
        return {"operation_id": operation.operation_id, "pid": pid}
```

Routes:

```text
GET  /api/repository-upgrade/status
POST /api/repository-upgrade/check
POST /api/repository-upgrade/suggest-preservation
POST /api/repository-upgrade/start
```

`start` rechecks current Git state after validating the request model. It returns `202` with operation ID and detached updater PID, `409` for fingerprint/divergence/concurrency conflicts, and `422` for invalid branch/message input. Launch the updater with `start_new_session=True`, repository root as `cwd`, and an argument array; do not pass mutable text through a shell.

- [ ] **Step 4: Implement compact banner and editable confirmation dialog**

`render_repository_upgrade_mount()` returns one full-width section before the History chart. JavaScript polls status every 60 seconds, updates only the banner node, and renders buttons for check, suggestion, and start. The dirty dialog keeps branch and message as editable inputs after Agent suggestions and lists every confirmed Git-visible path.

Disable the primary button while a request is active. Repeated clicks reuse the operation ID. Publish one existing browser-notification event for each unseen remote commit and store the notified commit in `service_state`.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_repository_upgrade_web.py tests/test_audit_web.py -q`

Expected: PASS.

Commit:

```bash
git add app/repository_upgrade_web.py app/audit_web.py tests/test_repository_upgrade_web.py
git commit -m "feat: add repository upgrade controls"
```

### Task 5: Configuration, Periodic Check, And Updater CLI

**Files:**
- Modify: `app/config.py`
- Modify: `app/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_repository_upgrade.py`

- [ ] **Step 1: Write failing configuration and service-component tests**

```python
def test_repository_upgrade_defaults(monkeypatch):
    monkeypatch.delenv("CEO_REPOSITORY_UPGRADE_REMOTE", raising=False)
    assert repository_upgrade_remote() == "origin"
    assert repository_upgrade_branch() == "main"
    assert repository_upgrade_check_interval_seconds() == 21600


def test_service_starts_repository_upgrade_checker(monkeypatch, tmp_path):
    names = record_service_thread_names(monkeypatch, tmp_path)
    assert "ceo-agent-service-repository-upgrade-check" in names
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_repository_upgrade.py -q`

Expected: FAIL because configuration and component are absent.

- [ ] **Step 3: Add explicit environment configuration**

```python
def repository_upgrade_remote() -> str:
    return os.getenv("CEO_REPOSITORY_UPGRADE_REMOTE", "origin").strip() or "origin"

def repository_upgrade_branch() -> str:
    return os.getenv("CEO_REPOSITORY_UPGRADE_BRANCH", "main").strip() or "main"

def repository_upgrade_check_interval_seconds() -> int:
    return env_int("CEO_REPOSITORY_UPGRADE_CHECK_INTERVAL_SECONDS", 6 * 60 * 60)

def repository_upgrade_enabled() -> bool:
    return not _env_truthy("CEO_REPOSITORY_UPGRADE_DISABLED")
```

Add matching `WorkerSettings` fields and populate them in `settings_from_args` without exposing arbitrary verification commands through the web API.

- [ ] **Step 4: Add periodic loop and CLI updater entry point**

```python
def run_repository_upgrade_check_loop(settings, *, sleep=time.sleep):
    service = build_repository_upgrade_service(settings)
    while True:
        service.check_and_persist(notify=True)
        sleep(settings.repository_upgrade_check_interval_seconds)
```

Register this as a normal supervised service component only when enabled. A checker failure persists `check_failed` and sleeps; it must not terminate the whole CEO service.

Add a hidden/internal parser command:

```text
ceo-agent repository-updater --operation-id <id> --db <path> --repo <path>
```

The command loads the already-persisted operation by ID. It does not accept branch name, commit message, or target commit directly from command-line arguments.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_repository_upgrade.py -q`

Expected: PASS.

Commit:

```bash
git add app/config.py app/cli.py tests/test_cli.py tests/test_repository_upgrade.py
git commit -m "feat: schedule repository upgrade checks"
```

### Task 6: Operator Documentation And Full Verification

**Files:**
- Modify: `README.md`
- Verify: `docs/superpowers/specs/2026-08-03-repository-upgrade-design.md`

- [ ] **Step 1: Document behavior and configuration**

Add a concise operator section covering:

```text
CEO_REPOSITORY_UPGRADE_REMOTE=origin
CEO_REPOSITORY_UPGRADE_BRANCH=main
CEO_REPOSITORY_UPGRADE_CHECK_INTERVAL_SECONDS=21600
CEO_REPOSITORY_UPGRADE_DISABLED=0
```

Document clean upgrade, dirty preservation, user ownership of branch/message, Agent suggestions, divergence refusal, backup location, rollback states, and why ignored runtime data is never committed.

- [ ] **Step 2: Run formatting/static checks**

Run:

```bash
git diff --check
.venv/bin/python -m compileall -q app
```

Expected: both exit 0.

- [ ] **Step 3: Run focused suites**

Run:

```bash
.venv/bin/pytest tests/test_repository_upgrade.py tests/test_repository_upgrade_agent.py tests/test_repository_updater.py tests/test_repository_upgrade_web.py tests/test_database_backup.py tests/test_cli.py tests/test_audit_web.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/pytest -q`

Expected: PASS. Any environment-only sandbox failure must be rerun on the host and reported separately; do not classify it as passing without that rerun.

- [ ] **Step 5: Commit docs**

```bash
git add README.md
git commit -m "docs: explain repository upgrades"
```

### Task 7: Live Deployment And End-To-End Verification

**Files:**
- No new source files expected.

- [ ] **Step 1: Push the implementation branch**

Run: `git push -u origin codex/repository-upgrade`

Expected: remote branch updated to the locally tested commit.

- [ ] **Step 2: Merge to main only after code review**

Use a non-destructive merge workflow. Verify:

```bash
git status --short
git log -1 --oneline
git rev-parse main
git rev-parse origin/main
```

Expected: no tracked local changes and local/remote main agree after push.

- [ ] **Step 3: Restart and verify launchd**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: a new running PID and no immediate crash loop.

- [ ] **Step 4: Verify live API and History banner**

Run:

```bash
curl -sS http://127.0.0.1:8765/api/repository-upgrade/status
curl -sS http://127.0.0.1:8765/
```

Expected: valid versioned status JSON and `repository-upgrade-banner` in History HTML. Verify in the browser that status refresh does not reload or jump the History page.

- [ ] **Step 5: Run a temporary-remote live E2E without changing production main**

Point an injected `RepositoryUpgradeService` test instance at a temporary clone/bare remote. Verify one clean upgrade and one dirty preservation flow, including exact user branch/message and a replacement-process health probe. Do not manufacture a production update on `origin/main` merely to test the button.

- [ ] **Step 6: Verify service backlog and errors**

Read the production Store and confirm:

```text
reply_tasks status in (failed, processing): 0
work_summary_inputs status in (failed, processing): 0
meeting jobs status in (failed, processing, retry): 0
new repository-upgrade errors after restart: 0
```

Do not report deployment complete while any of these remain unresolved.
