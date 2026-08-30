# Email Unsubscribe Branch Integration Design

**Date:** 2026-08-30
**Status:** Approved design, pending written review
**Target repository:** `/Users/derek/Documents/Projects/ceo-agent-service`
**Target branch:** `main`
**Runtime code tip:** `eb49420e46876a95b65b7346fc41dfefe1220ac1`

## 1. Purpose

Integrate the complete audited automatic email-unsubscribe implementation into
`main` without weakening the existing mail-review boundaries, omitting later
hardening fixes, rewriting the implementation history, or including unrelated
uncommitted work from the shared feature checkout.

The integration covers automatic `channel=email` actions only. It does not
authorize arbitrary link browsing, attachment inspection, or replies outside
the immutable Email `ActionPlan`.

## 2. Current Git State

`421c3fb47c026aaef09fbde2cbd06a2739922dfb` (`fix: preserve mail review skill
boundaries`) is already an ancestor of both `main` and the runtime code tip. Its
`tests/test_mail_review_skill.py` contract remains unchanged through the feature
tip.

The remaining feature range is one linear chain:

1. `02ae0f72` — `fix: fence automatic email reply delivery`
2. `d768026d` — `feat: automate audited email unsubscribe flows`
3. `dc2a0359` — `fix: complete audited unsubscribe recovery`
4. `36bc37a1` — `fix: prevent uncertain unsubscribe replay`
5. `76ab5720` — `fix: harden audited unsubscribe execution`
6. `1b24d2b7` — `fix: audit incremental unsubscribe steps`
7. `eb49420e` — `fix: bind audited unsubscribe controls`

`76ab5720` is not a standalone integration unit: it depends on the earlier
implementation and recovery commits. It is also not the final state because two
later audit/control-binding fixes follow it.

## 3. Decision

Merge the complete runtime range through `eb49420e`, together with the approved
docs-only design and implementation-plan commits that follow it, using a normal
`--no-ff` merge from a clean `main` worktree. No later runtime-code commit is in
scope unless a failing gate reveals a defect and the user approves the resulting
design change.

Do not:

- cherry-pick `421c3fb4` again;
- cherry-pick only `76ab5720`;
- omit `1b24d2b7` or `eb49420e`;
- squash the seven-commit feature chain;
- rebase or otherwise rewrite the feature history;
- merge from the dirty shared feature checkout;
- stage or commit unrelated API, Task, frontend, TODO-evidence, Email-page, or
  worker WIP.

The normal merge preserves the reviewed dependency order and makes the source
commits traceable. A merge commit provides a single integration boundary without
discarding the underlying audit history.

## 4. Alternatives Rejected

### Cherry-pick the chain

Cherry-picking all seven commits would reproduce nearly the same tree while
changing commit identities and increasing the chance of omission or ordering
errors. There is no compensating benefit because the feature is already a
linear descendant of the mail-review boundary commit.

### Squash the chain

Squashing would hide which review round added recovery, uncertain-effect
protection, incremental audit, and control binding. That history is valuable for
an external-write workflow and should remain inspectable.

### Stop at `76ab5720`

Stopping at the reported Task 11 hardening commit would omit later fixes that
limit each continuation to one appended operation and bind accepted operations
to exact discovered controls. The accepted product scope is the complete
runtime, not an earlier compatible subset.

## 5. Isolation and Worktrees

The shared checkout on `codex/email-ceo-agent-integration` contains unrelated
uncommitted work. It is evidence and source state, not an integration workspace.

Use two clean worktrees:

1. A temporary verification worktree detached at exact runtime code tip
   `eb49420e`.
   Run pre-merge tests there so results cannot accidentally include shared WIP.
2. The existing clean runtime worktree
   `/Users/derek/Documents/Projects/ceo-agent-service/.worktrees/runtime-main`
   on `main`. Fetch, verify it matches `origin/main`, perform merge preflight,
   create the merge commit, run post-merge tests, push, and deploy from there.

Before any merge, record:

- runtime code tip SHA;
- local and remote `main` SHA;
- merge base;
- clean status of both integration worktrees;
- the exact commit range and changed-file list;
- whether the runtime code tip is still a descendant of `76ab5720` and
  `421c3fb4`.

## 6. Functional Boundaries to Preserve

### Interactive DingTalk and Lark mail review

- Resolve the full original message or thread through the corresponding mail
  Skill.
- Read linked documents only through the matching document/table/drive Skill.
- Require explicit current-request authorization before replying.
- Do not confuse linked material with an email attachment.

### Automatic `channel=email` actions

- The immutable `ActionPlan` is the only action authorization.
- Attachments remain metadata-only; `image_paths=()` stays authoritative.
- General links in message text are not browsing authorization.
- Only an authorized `unsubscribe` action may use the audited unsubscribe
  capability.
- Consumer proposes; Audit reviews and is the only role allowed to execute an
  accepted external effect.

### Audited unsubscribe

- Ordinary browser flows begin with exactly `OPEN_ENTRY`; page controls are not
  guessed or precomputed.
- Authenticated RFC one-click is allowed only with typed provider evidence that
  valid DKIM covers both required headers. It is an isolated POST with the exact
  RFC body, never a GET and never a mailbox-cookie request.
- Each continuation is an append-only extension of the durable accepted prefix
  with exactly one new operation.
- The action, plan, classification, account, message, thread, entry, origin
  policy, and already accepted operation prefix remain bound across
  continuations.
- Only the newly accepted operation executes; accepted prefix operations never
  replay.
- Initial runs and retries reconcile terminal page/provider/mail evidence before
  another write.
- A terminated or uncertain in-flight effect is reconciliation-only until exact
  effect-bound terminal evidence exists.
- Full unsubscribe URLs and query tokens never enter proposals, journals,
  History, status, or errors. Durable records contain opaque references,
  redacted step kinds/states, fixed error codes, and terminal receipts.
- Login, CAPTCHA, payment, and absence of a reliable entry are skipped business
  outcomes. Browser runtime and provider authentication failures use the
  existing failure/retry/Attention lifecycle.

No new top-level task status, user-confirmation step, audit policy, or general
browser capability is introduced by the integration.

## 7. Verification Strategy

Historical receipts for `421c3fb4` and `76ab5720` are useful context but cannot
prove the final runtime code tip because `1b24d2b7` and `eb49420e` materially
change
runtime behavior. All required tests must run fresh against exact `eb49420e`,
then the integration-critical subset must run again against the merge commit.

### Pre-merge verification at exact runtime code tip

Run:

- `tests/test_mail_review_skill.py`
- `tests/test_email_reply_delivery.py`
- `tests/test_email_store.py`
- `tests/test_email_task_adapter.py`
- `tests/test_email_unsubscribe.py`
- `tests/browser/test_email_unsubscribe_browser.py`
- all `tests/test_email*.py`

Browser tests must use only the loopback test server. They must not access a
real mailbox, unsubscribe site, or external account.

Also run:

- scoped Ruff for every Python file changed by the feature range;
- `git diff --check` for the feature range;
- `git show --check` for each feature commit or the verified range.

### Merge preflight

Before changing `main`:

- fetch `origin`;
- verify `runtime-main` is clean and not behind or diverged from `origin/main`;
- inspect the three-way merge result and changed-file overlap;
- stop if any conflict requires a product or policy choice rather than making an
  implicit resolution.

### Post-merge verification

At minimum rerun:

- `tests/test_mail_review_skill.py`;
- `tests/test_email_reply_delivery.py`;
- `tests/test_email_store.py`;
- `tests/test_email_task_adapter.py`;
- `tests/test_email_unsubscribe.py`;
- `tests/browser/test_email_unsubscribe_browser.py`;
- all `tests/test_email*.py`;
- scoped Ruff;
- `git diff --check` and `git show --check`.

The merge is rejected if a test fails because of the integration. Pre-existing
warnings must be identified as pre-existing rather than silently ignored.

## 8. Merge, Push, and Deployment

After all pre-merge gates pass:

1. Merge the integration branch into `main` with `--no-ff` and a traceable merge
   message. Verify its runtime code ancestry ends at `eb49420e`; commits after
   that SHA must be approved design/plan documentation only unless a separately
   approved defect fix was required.
2. Run the post-merge gates.
3. Push `main` without force.
4. Verify `origin/main` resolves to the merge commit.
5. Wait for actively claimed reply tasks, work-summary inputs, and meeting jobs
   to reach a resumable terminal state before restarting.
6. Restart `com.ceo-agent-service.main`.
7. Verify a new supervisor PID, web listener PID, launchd state, and HTTP 200
   from the local health endpoint.
8. Read back queue state and persisted external-effect state. Confirm that no
   new failed, stuck, duplicate, or uncertain Email operation was introduced.

The integration does not access a real mailbox or unsubscribe website as a
deployment test. Runtime activation is established through process/readback,
configuration, loopback tests, and durable queue/effect inspection.

## 9. Existing Production Backlog

The repository currently has known historical failures unrelated to this Email
feature, including `work_summary_inputs#13908` and
`meeting_alignment_jobs#1529`. This integration must neither replay nor clear
them.

Report two states separately:

- **Email integration state:** code merged, tests passed, pushed, new runtime
  loaded, health/readback successful, and no new Email failure or uncertain
  effect introduced.
- **Global service state:** remains degraded or incomplete while any unresolved
  failed or processing backlog remains.

Do not describe the whole service or the overarching finish-branch goal as
complete until the repository's global backlog requirement is genuinely met.

## 10. Failure Handling

- If pre-merge tests fail, fix on a dedicated feature branch/worktree with a
  regression test; do not patch the merge worktree or weaken a test.
- If merge preflight conflicts, classify every conflict by business meaning and
  present any policy choice for approval before resolving it.
- If post-merge tests fail, do not push or deploy the merge.
- If push fails or remote advances, fetch and reconcile with a normal merge;
  never force-push.
- If restart occurs but health/readback fails, keep the integration incomplete
  and diagnose the runtime rather than relying on the new PID alone.
- If a new Email external effect is uncertain, reconcile durable receipts and
  provider state read-only; never resend merely to clear a queue.

## 11. Completion Criteria

The Email unsubscribe branch integration is accepted only when all of the
following are true:

- `main` contains the complete seven-commit feature range through `eb49420e`;
- `421c3fb4` remains the effective mail-review boundary foundation;
- the exact merge commit is pushed to `origin/main`;
- pre-merge and post-merge test matrices pass;
- loopback browser tests prove the multi-step audited flow without external
  access;
- scoped Ruff and Git whitespace/object checks pass;
- the running launchd service uses the merged `runtime-main` checkout and has a
  new verified PID;
- the health endpoint returns HTTP 200;
- no new Email failed, stuck, duplicate, or uncertain effect exists;
- shared uncommitted WIP remains unstaged and uncommitted;
- the temporary verification worktree is removed after its results are captured;
- known unrelated production failures are reported truthfully and not altered.

The wider finish-branch goal is complete only when its separate feedback
resolution and global backlog gates are also satisfied.
