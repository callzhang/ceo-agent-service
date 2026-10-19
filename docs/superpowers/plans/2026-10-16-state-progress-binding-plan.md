# Committed State progress binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with fresh SPEC then QUALITY. Steps use checkbox syntax.

**Goal:** Keep deployment progress readable/writable across a committed native-source/State migration without changing the generic updater protocol.

**Architecture:** The existing DAO selects its backend once during construction and reselects immediately before updater progress persistence. Selection observes one read-only committed SQLite snapshot, using the retained Email ledger first. Getters/setters stay explicitly bound and retain existing retries/transactions; there is no alternate-SQL fallback. This completes all three existing entrypoints through their existing DAO constructors, without release wrappers or publication callbacks.

**Tech Stack:** Python3.13, SQLite, existing State storage primitives, pytest, confined local Git fixture repositories.

---

### Task 1: exact DAO/progress delta

**File:** `app/repository_updater.py`.
Root source SHA256434db55b6aa8be6b1f70e88904d1aa5eca012810fa3e1e0a31ab3842f3dcf51d.
Candidate SHA256c9963c3f8d993c8031c01ee42e55564c7370f35cfc5e3b0ff7b362350735eb63.
Complete executable patch: `2026-10-16-state-progress-binding-proposal.patch`, SHA256a6d7fa9d9712ca0b1b1ed6bb20697759ee992489933d1380d126a95b6525e5d7.

Changes are exactly the ExistingSchemaUpgradeStateStore class docstring, its existing constructor, one new bind_committed_schema method, and RepositoryUpdater._persist. The complete new method is:

```python
    def bind_committed_schema(self) -> None:
        """Select the progress backend from one committed, read-only snapshot."""
        from app.action_email_schema_release import (
            SOURCE_EMAIL_VERSION, TARGET_EMAIL_VERSION, TARGET_STORE_VERSION,
        )
        from app.state_definitions import state_projection

        if not self.database_path.exists():
            self.bind_schema_source()
            return
        with closing(sqlite3.connect(
            self.database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30
        )) as db:
            db.execute("begin")
            ledger = db.execute(
                "select 1 from main.sqlite_schema where type='table' "
                "and name='email_schema_migrations' collate nocase"
            ).fetchone()
            version = db.execute(
                "select max(version) from main.email_schema_migrations"
            ).fetchone()[0] if ledger else None
            if version is not None:
                if type(version) is not int or not (
                    0 < version <= SOURCE_EMAIL_VERSION or version == TARGET_EMAIL_VERSION
                ):
                    raise ValueError("unsupported committed Email schema role")
                current = version == TARGET_EMAIL_VERSION
            elif db.execute(
                "select 1 from main.sqlite_schema where name='state' collate nocase"
            ).fetchone():
                marker = db.execute(
                    f"select value from ({state_projection('service_state')}) "
                    "where key='store_schema_version'"
                ).fetchone()
                if marker is None or marker[0] != TARGET_STORE_VERSION:
                    raise ValueError("unsupported committed Store-only State schema role")
                current = True
            else:
                current = False
        if current:
            self.bind_schema_current()
        else:
            self.bind_schema_source()

```

The constructor keeps its existing path assignment and source initialization, then calls `self.bind_committed_schema()`. Immediately before the existing `self.store.set_service_state(...)`, _persist adds exactly:

```python
if isinstance(self.store, ExistingSchemaUpgradeStateStore):
    self.store.bind_committed_schema()
```

The class docstring describes initial/progress selection and explicitly unchanged getters/setters. No other updater functions, protocols, dataclass fields, error statuses, restart/recovery policy, source/current storage SQL or retry behavior change. Version constants come from the existing paired release.

The retained Email ledger selects current only at the exact target. Positive earlier native versions through the existing source version remain source. Unknown future or malformed scalar versions fail visibly without mutation. With no ledger version, a declared State relation must supply the exact target Store marker to select current; native/empty databases stay source. Missing paths are never created. The selector intentionally does not validate complete current declarations: constructors/publication own that, and progress still needs to record a target verification failure.

- [x] Root callpath inspection plus independent bounded research; no separate entrypoint changes or archived Store coordinator edits justified.
- [x] Complete source/regression prototype; original code22RED/11PASS3.92s, corrected61PASS8.19s.
- [x] Final exact artifact harness after class docstring update:61PASS8.24s; corrected contextual patch reproduces identical application bytes.
- [x] Fresh independent SPEC then QUALITY, including contextual artifact correction; receipt ../specs/2026-10-16-state-progress-binding-readiness.json.
- [ ] Activate atomically with reviewed State callers/constructors/paired release, never alone.

### Task 2: complete regression

**Create:** `tests/test_state_progress_binding.py`, using the entire adjacent `2026-10-16-state-progress-binding-regression.py` verbatim (SHA256397d5b8a8541e18005c137b2662f16c6b74bb8d79f2bfca938928bd57a746858).

The complete module includes33 parameterized cases across both actual/fresh native State source declarations:

- Initial source/no-ledger/earlier ledger and current52/Store-only current binding; constructor byte equality.
- Unknown future, zero, noninteger, string and BLOB ledger refusal with byte equality.
- Missing path and empty database preservation.
- Fresh DAO loading an existing current persisted operation before updater execution.
- An already-created DAO getter still failing after the database migrates behind it, until explicitly rebound.
- Real confined RepositoryUpdater/Git execution using pure State transitions: success persists succeeded in State; commit-then-child failure with successful inverse persists rolled_back in native storage; inverse failure persists needs_manual in State.
- Generic protocol-only store persistence without any schema binding method.

No real launchd, provider or native CLI runs. Existing stop/start/health operations are test substitutes. Test Git repositories, DBs and backups stay inside the owned pytest temporary directory.

### Task 3: confined executable verification

Complete harness: `2026-10-16-state-progress-binding-preflight.py`.
It copies app and original conftest plus fixture imports, applies the exact proposal, places the canonical regression in tests, and runs only new33 plus the two adjacent existing test files (61total). It verifies owned TemporaryDirectory cleanup.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-state-progress-binding-preflight.py
```

### Task 4: integration, documentation and remaining acceptance

After component review, apply all accepted State caller/dispatcher/constructor/Email/direct/publication/paired/progress deltas to the isolated app together. Add this exact behavior paragraph to architecture and runtime documentation in that implementation commit:

> Upgrade progress selects the committed schema at DAO construction and before updater progress writes. The retained Email ledger selects native source or current State, while Store-only State uses its current Store marker. Selection is read-only and never retries another layout after SQL failure. Getters/setters remain explicitly bound with their original transactions and lock retry. A child that commits then fails and an inverse that fails are followed by selection from actual committed markers before final progress persistence.

Then run the focused combined consumer/constructor/paired/progress tests, actual-copy forward/current-fact inverse, genuine latest predecessor cold writes/reforward, fake-outbound native CLI and UI. These proposal tests alone do not satisfy the final10core+Emailledger goal or authorize production.

Artifact correction before activation: whole-State composition found the original zero-context pure-add _persist hunk inserted at the wrong offset after the publication patch added an import. No app source was activated. Regenerated only diff context (3lines), preserving candidateSHA c9963c3f exactly. Revised independent SPEC accepts standalone application and publication→progress composition: expected+1line offsets, exactmethodbytes/wholeAST scope, all350 application modules compile. Original logical61-case evidence remains applicable; root14patch combined matrix remains separate.
